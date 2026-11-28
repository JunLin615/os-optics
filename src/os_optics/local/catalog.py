"""The public optical catalog and Project documents as local inputs.

``load_public_catalog()`` returns the ``PublicOpticalCatalogV2`` a local run uses, resolved in this order:

1. an explicit catalog -- a published release given as a document, JSON text / bytes, a file path or a parsed model;
2. a catalog installed in memory by the host (``install_catalog``; the browser runtime's ``json_api.install_catalog``):
   the release a Project pins when it is installed, else the most recently installed one;
3. the local catalog store (``os_optics.catalog``): the release a Project pins (``catalog_version=``; fetched
   once when it is missing and automatic catalog access is on, and then the active release of a store that held none),
   else the active release, after the automatic check
   against the catalog service when one is due (offline: the stored release, silently).

No catalog is bundled with the Engine. Nothing usable -> ``LocalRunError`` ``CATALOG_NOT_AVAILABLE`` (the message says
how to get one); a release in a catalog schema this Engine cannot read -> ``ENGINE_UPGRADE_REQUIRED``.

Parsing is memoized by content digest: a catalog release is several megabytes and one run reads it more than once
(lowering, binding, the view), so the SHA-256 of the exact bytes (or of the canonical JSON of a document) maps to the
parsed, immutable (frozen) catalog. At most ``MAX_MEMOIZED_CATALOGS`` releases are kept; the memo never changes a result,
only how often the same bytes are validated.

A release of the local store is read as a ``StoredCatalog``: the store validated the whole document when the release
entered it and recorded by which catalog contract (``StoredRelease.validated_by``); its bytes are verified against their
SHA-256 on every read. When that contract is this Engine's, the products, materials and coatings a run asks for are
built one by one from the verified document and everything that walks the catalog (``products``, ``select`` ...) builds
the whole ``PublicOpticalCatalogV2`` once, on first use. When it is another Engine's (or none is recorded), the whole
document is validated first, as an explicit catalog always is, and the index entry is updated.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation, localcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from .._vendor.shared_contracts.common.decimal_string import DECIMAL_STRING_PATTERN, decimal_to_string
from .._vendor.shared_contracts.design_catalog import PublicCoatingV1, PublicMaterialV1, PublicOpticalCatalogV2, PublicOpticalProductV2
from .._vendor.shared_contracts.design_catalog.optical_catalog import DECIMAL_CONTEXT
from .._vendor.shared_contracts.optical_design import ProjectDocumentV2
from ..assembly_plan.mechanical.model import MechanicalPlanBinding, MechanicalSources
from ..catalog.errors import CatalogError, CatalogErrorCode
from ..catalog.mechanical import resolve_sources
from ..catalog.service import check_interval, mark_release_validated, network_available, release_bytes
from ..catalog.store import SUPPORTED_SCHEMA_VERSIONS, check_compatible, release_header, validated_here
from ..project_document import parse_project_document, plain_location
from .errors import LocalRunError, LocalRunErrorCode, fail

MAX_REPORTED_VALIDATION_ERRORS = 10
MAX_MEMOIZED_CATALOGS = 4
# content digest -> parsed catalog (bounded, insertion ordered; see the module docstring).
_PARSED_CATALOGS: dict[str, Any] = {}
# Catalogs a host installed in memory (``install_catalog``): catalog_version -> installed release, and the install order
# (the last one is active).
_INSTALLED_CATALOGS: dict[str, InstalledCatalog] = {}
_INSTALL_ORDER: list[str] = []


#: Plain wording of the most common contract validation errors (the others keep their own message).
VALIDATION_WORDS = {"extra_forbidden": "unknown field (not part of this format)", "missing": "required field missing"}


def _field_path(loc: tuple[Any, ...]) -> str:
    """``components[1].product.optical_sku`` from a validation error location."""

    out = ""
    for part in loc:
        out += f"[{part}]" if isinstance(part, int) else (f".{part}" if out else str(part))
    return out or "the document"


def _reported_errors(exc: ValidationError) -> list[dict[str, Any]]:
    """The validation errors a customer is told about: without pydantic's derived ``too_short`` clause on a list whose
    only items failed ("Tuple should have at least 1 item after validation, not 0") when an item's own error is there."""

    errors = [{**error, "loc": plain_location(tuple(error["loc"]))} for error in exc.errors()]

    def derived(error: dict[str, Any]) -> bool:
        loc = tuple(error["loc"])
        return error["type"] == "too_short" and any(tuple(other["loc"])[:len(loc)] == loc and len(other["loc"]) > len(loc)
                                                    for other in errors)

    return _out_of_model_inputs(_source_polarization_kind([e for e in errors if not derived(e)] or errors))


#: Project fields of time-domain dynamics: the Project is a static model (one nominal pose, evaluated at one instant).
TIME_DOMAIN_FIELDS = ("vibration", "time_series", "dynamic_feedback", "timing_noise", "timing_jitter")
#: What a refused time-domain field reads.
TIME_DOMAIN_REFUSAL = (
    "time-domain dynamics (vibration, timing noise or jitter, a time series of perturbations, dynamic feedback) are outside "
    "the Engine's model: a Project is static, its run evaluates the nominal poses at one instant (the static approximation: "
    "every quantity is the value for the stated geometry). A displacement's effect is the difference of two runs, the "
    "Project and a copy with the displaced pose (os-optics workspace transform moves a selection rigidly); its spectrum, "
    "statistics and feedback belong to a dynamic model outside the Engine (os-optics interop show multiphysics-stray-light)")
#: Other names of the sampled source kinds, and what a source model written with one reads: the kind's name and its fields.
SOURCE_MODEL_REFUSALS = {
    "COMPLEX_FIELD": "a sampled complex field is source_model kind SAMPLED_FIELD: wavelength_nm, grid {column_count, row_count, "
                     "pitch_mm}, amplitude and phase_rad ([row][column]), amplitude_unit (RELATIVE with power_w, or "
                     "SQRT_W_PER_MM2), polarization and ray_sampling {sample_count, seed}",
    "ARBITRARY_INTENSITY": "an arbitrary intensity pattern is source_model kind SAMPLED_INTENSITY: wavelength_nm and power_w (or "
                           "spectral_lines), intensity_map {grid, values, unit, interpolation}, angular_law and sampling "
                           "{sample_count, seed}",
    "SPECKLE_FIELD": "a speckle source is source_model kind SPECKLE: wavelength_nm, power_w, polarization, illumination, "
                     "phase_screen {statistics, phase_rms_rad, correlation_length_mm, seed}, grid, realizations and ray_sampling",
}
#: What a continuous spectral density on an extended emitter reads: the discrete form that samples it.
SPECTRAL_DENSITY_REFUSAL = (
    "an extended emitter's spectrum is spectral_lines: its rays carry no phase, so a spectrum's coherence does not apply to "
    "them. Sample the density as spectral_lines -- N lines at the centres of N equal bins, each with the power of its bin; a "
    "GAUSSIAN_BEAM source states a continuous spectrum as spectral_density")


def _out_of_model_inputs(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """An input the Project format leaves out because it is outside the Engine's model -- a time-domain field, a continuous
    spectral density on an extended emitter -- is refused with the boundary and the route instead of "unknown field"; a sampled
    source kind written by another name (``SOURCE_MODEL_REFUSALS``) with the kind's name and fields instead of "unknown tag"."""

    out = []
    for error in errors:
        loc = tuple(error["loc"])
        tag = (error.get("ctx") or {}).get("tag")
        if error["type"] == "extra_forbidden" and loc and loc[-1] in TIME_DOMAIN_FIELDS:
            out.append({**error, "type": "time_domain_out_of_model", "msg": TIME_DOMAIN_REFUSAL})
        elif error["type"] == "extra_forbidden" and loc and loc[-1] == "spectral_density" and "source_model" in loc:
            out.append({**error, "type": "spectral_density_as_lines", "msg": SPECTRAL_DENSITY_REFUSAL})
        elif error["type"] == "union_tag_invalid" and loc and loc[-1] == "source_model" and tag in SOURCE_MODEL_REFUSALS:
            out.append({**error, "type": "source_model_kind_name", "msg": SOURCE_MODEL_REFUSALS[tag]})
        else:
            out.append(error)
    return out


#: The source polarization kinds a Project states, with the fields each takes (``SourcePolarizationV1``).
SOURCE_POLARIZATION_KINDS = ("LINEAR (angle_deg), ELLIPTICAL (orientation_deg, ellipticity_deg), PARTIAL (polarized, "
                             "degree_of_polarization) or UNPOLARIZED")


def _source_polarization_kind(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A source polarization of another ``kind`` (``CIRCULAR``, ``RANDOM``) is read as LINEAR -- a linear polarization
    may omit its kind -- so its errors would name the LINEAR fields only: one error naming the kinds there are instead."""

    unknown: dict[tuple[Any, ...], Any] = {}
    for error in errors:
        loc = tuple(error["loc"])
        if (error["type"] == "literal_error" and len(loc) >= 3 and loc[-3:-1] == ("polarization", "LINEAR")
                and loc[-1] == "kind"):
            unknown[loc[:-2]] = error.get("input")
    if not unknown:
        return errors
    out: list[dict[str, Any]] = []
    for error in errors:
        loc = tuple(error["loc"])
        prefix = next((p for p in unknown if loc[:len(p) + 1] == (*p, "LINEAR")), None)
        if prefix is None:
            out.append(error)
        elif loc[-1] == "kind" and loc[:-1] == (*prefix, "LINEAR"):
            out.append({**error, "loc": (*prefix, "kind"), "type": "source_polarization_kind",
                        "msg": f"{unknown[prefix]!r} is not a source polarization kind: the kinds are {SOURCE_POLARIZATION_KINDS}"})
    return out


def _error_text(error: dict[str, Any]) -> str:
    """One validation error as a customer reads it: no "Value error, " prefix, a decimal-string pattern in words."""

    if error["type"] == "string_pattern_mismatch" and (error.get("ctx") or {}).get("pattern") == DECIMAL_STRING_PATTERN:
        text = (f"{error.get('input')!r} is not a plain decimal (digits with an optional sign and point, e.g. 52 or -0.5: no "
                "exponent, no leading or trailing zeros)")
        plain = _plain_decimal(error.get("input"))
        return text + (f"; write it as {plain!r}" if plain is not None and plain != error.get("input") else "")
    return VALIDATION_WORDS.get(error["type"], str(error["msg"]).removeprefix("Value error, "))


#: The largest decimal exponent whose plain form an error message suggests (beyond it the plain form is not a useful hint).
PLAIN_DECIMAL_MAX_EXPONENT = 40


def _plain_decimal(value: Any) -> str | None:
    """The canonical decimal string of a number written another way (``"1e-8"`` -> ``"0.00000001"``, ``"5.0"`` -> ``"5"``),
    None when ``value`` is not a finite number."""

    if not isinstance(value, str):
        return None
    try:
        number = Decimal(value.strip())
    except InvalidOperation:
        return None
    if not number.is_finite() or abs(number.adjusted()) > PLAIN_DECIMAL_MAX_EXPONENT:
        return None
    return decimal_to_string(number)


def _validation_message(exc: ValidationError) -> str:
    errors = _reported_errors(exc)
    lines = [f"{_field_path(e['loc'])}: {_error_text(e)}" for e in errors[:MAX_REPORTED_VALIDATION_ERRORS]]
    if len(errors) > MAX_REPORTED_VALIDATION_ERRORS:
        lines.append(f"... {len(errors) - MAX_REPORTED_VALIDATION_ERRORS} more")
    return "; ".join(lines)


def _raw(source: Any, what: str, code: LocalRunErrorCode) -> Any:
    """``bytes`` of a file path (``pathlib.Path`` or a ``str`` that is not JSON text) or of JSON text / bytes; a mapping
    (a document, or a pydantic model's JSON document, e.g. an equivalent contract class from another package)."""

    if isinstance(source, Mapping):
        return source
    if hasattr(source, "payload") and callable(source.payload):
        return source.payload()
    if isinstance(source, BaseModel):
        return source.model_dump(mode="json", by_alias=True, exclude_none=True)
    if isinstance(source, str) and not source.lstrip().startswith(("{", "[")):
        source = Path(source)
    if isinstance(source, Path):
        try:
            return source.read_bytes()
        except OSError as exc:
            raise fail(code, f"cannot read the {what} file: {exc}") from None
        except ValueError as exc:  # e.g. a NUL character in the path
            raise fail(code, f"cannot read the {what} file: the path is not valid ({exc})") from None
    if isinstance(source, str):
        return source.encode("utf-8")
    if isinstance(source, (bytes, bytearray)):
        return bytes(source)
    raise fail(code, f"cannot read a {what} from a {type(source).__name__}")


def _json(data: bytes, what: str, code: LocalRunErrorCode) -> Any:
    try:
        return json.loads(data)
    except ValueError as exc:  # not JSON, not UTF-8, or a number literal too long to convert
        raise fail(code, f"the {what} is not valid JSON: {exc}") from None
    except RecursionError:
        raise fail(code, f"the {what} is not valid JSON: it is nested too deeply") from None


def _document(source: Any, what: str, code: LocalRunErrorCode) -> Any:
    raw = _raw(source, what, code)
    return _json(raw, what, code) if isinstance(raw, bytes) else raw


def _parse_catalog(document: Any) -> PublicOpticalCatalogV2:
    try:
        # (the contract's own decimal context, never the caller's: a catalog reads the same whatever decimal settings the
        # calling thread has when it is first needed)
        with localcontext(DECIMAL_CONTEXT):
            return PublicOpticalCatalogV2.model_validate(document)
    except ValidationError as exc:
        raise fail(LocalRunErrorCode.CATALOG_INVALID, _validation_message(exc)) from None


class StoredCatalog:
    """A release of the local catalog store whose whole document this Engine's catalog contract validated (module
    docstring), read from its verified JSON document. It answers as the ``PublicOpticalCatalogV2`` of the release:

    * ``catalog_version`` / ``schema_version``: the document's;
    * ``get(sku)``, ``material(id)``, ``coating(id)``: that one record, built on first use and kept;
    * ``summary()``: the counts of ``json_api.catalog_summary``, from the document;
    * ``full()`` and every other attribute (``products``, ``materials``, ``coatings``, ``select`` ...): the whole
      ``PublicOpticalCatalogV2``, built once on first use; from then on the records are its own.

    A record that does not build raises ``LocalRunError`` ``CATALOG_INVALID`` naming it."""

    def __init__(self, document: Mapping[str, Any], full: PublicOpticalCatalogV2 | None = None) -> None:
        self._document = document
        self._full = full
        self._raw: dict[str, dict[str, Any]] = {}
        self._built: dict[tuple[str, str], Any] = {}

    def __repr__(self) -> str:
        return f"StoredCatalog({self.catalog_version!r})"

    @property
    def catalog_version(self) -> str:
        return self._document["catalog_version"]

    @property
    def schema_version(self) -> str:
        return self._document["schema_version"]

    def full(self) -> PublicOpticalCatalogV2:
        """The whole catalog as its contract model (validated once, then kept)."""

        if self._full is None:
            self._full = _parse_catalog(self._document)
        return self._full

    def _record(self, group: str, key: str, model: Any, identity: Any) -> Any:
        if not isinstance(identity, str):
            return None
        raw = self._raw.get(group)
        if raw is None:
            items = self._document.get(group)
            raw = self._raw[group] = {item[key]: item for item in items if isinstance(item, Mapping)} if isinstance(items, list) else {}
        item = raw.get(identity)
        if item is None:
            return None
        built = self._built.get((group, identity))
        if built is None:
            try:
                with localcontext(DECIMAL_CONTEXT):  # (as the whole catalog is built: _parse_catalog)
                    built = self._built[(group, identity)] = model.model_validate(item)
            except ValidationError as exc:
                raise fail(LocalRunErrorCode.CATALOG_INVALID,
                           f"{key} {identity} of public catalog {self.catalog_version}: {_validation_message(exc)}") from None
        return built

    def get(self, optical_sku: str) -> PublicOpticalProductV2 | None:
        """The product with this optical identity, or None."""

        if self._full is not None:
            return self._full.get(optical_sku)
        return self._record("products", "optical_sku", PublicOpticalProductV2, optical_sku)

    def material(self, material_id: str) -> PublicMaterialV1 | None:
        if self._full is not None:
            return self._full.material(material_id)
        return self._record("materials", "material_id", PublicMaterialV1, material_id)

    def coating(self, coating_id: str) -> PublicCoatingV1 | None:
        if self._full is not None:
            return self._full.coating(coating_id)
        return self._record("coatings", "coating_id", PublicCoatingV1, coating_id)

    def summary(self) -> dict[str, Any]:
        """Counts describing the release (``json_api.catalog_summary``), read from the document."""

        def counts(values) -> dict[str, int]:
            found: dict[str, int] = {}
            for value in values:
                found[value] = found.get(value, 0) + 1
            return dict(sorted(found.items()))

        products = self._document.get("products") or ()
        return {
            "catalog_version": self.catalog_version,
            "product_count": len(products),
            "material_count": len(self._document.get("materials") or ()),
            "coating_count": len(self._document.get("coatings") or ()),
            "categories": counts(p["category"] for p in products),
            "forms": counts(p["form"] for p in products),
            "model_kinds": counts(m["model_kind"] for p in products for m in p["simulation_models"]),
        }

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self.full(), name)


#: What ``load_public_catalog`` returns: the catalog model, or a store release read as one (``StoredCatalog``).
LocalCatalog = PublicOpticalCatalogV2 | StoredCatalog


def _memoized(key: str, parse) -> Any:
    catalog = _PARSED_CATALOGS.get(key)
    if catalog is None:
        catalog = parse()
        while len(_PARSED_CATALOGS) >= MAX_MEMOIZED_CATALOGS:
            del _PARSED_CATALOGS[next(iter(_PARSED_CATALOGS))]
        _PARSED_CATALOGS[key] = catalog
    return catalog


def _catalog_from(source: Any) -> PublicOpticalCatalogV2:
    raw = _raw(source, "public optical catalog", LocalRunErrorCode.CATALOG_INVALID)
    if isinstance(raw, bytes):
        key = "bytes:" + hashlib.sha256(raw).hexdigest()
        catalog = _memoized(key, lambda: _parse_catalog(_json(raw, "public optical catalog", LocalRunErrorCode.CATALOG_INVALID)))
        if isinstance(catalog, StoredCatalog):
            catalog.full()  # (the same bytes read from the store earlier: an explicit catalog is validated in full)
        return catalog
    try:
        canonical = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError):  # not a JSON document: let the contract report it (never memoized)
        return _parse_catalog(raw)
    return _memoized("document:" + hashlib.sha256(canonical).hexdigest(), lambda: _parse_catalog(raw))


@dataclass(frozen=True)
class InstalledCatalog:
    """A catalog release installed in memory: the parsed catalog and the SHA-256 / size of its exact bytes."""

    catalog: PublicOpticalCatalogV2
    sha256: str
    size_bytes: int

    def summary(self) -> dict[str, Any]:
        return {"catalog_version": self.catalog.catalog_version, "schema_version": self.catalog.schema_version, "sha256": self.sha256,
                "size_bytes": self.size_bytes, "product_count": len(self.catalog.products)}


def _local_error(exc: CatalogError) -> Exception:
    """The ``LocalRunError`` of a catalog store / synchronization failure."""

    if exc.code is CatalogErrorCode.ENGINE_UPGRADE_REQUIRED:
        return fail(LocalRunErrorCode.ENGINE_UPGRADE_REQUIRED, exc.message)
    if exc.code is CatalogErrorCode.CATALOG_INVALID:
        return fail(LocalRunErrorCode.CATALOG_INVALID, exc.message)
    message = exc.message if exc.code is CatalogErrorCode.CATALOG_NOT_AVAILABLE else f"{exc.code.value}: {exc.message}"
    return fail(LocalRunErrorCode.CATALOG_NOT_AVAILABLE, message)


def install_catalog(data: Any, *, expected_sha256: str | None = None) -> InstalledCatalog:
    """Install a catalog release in memory (its exact bytes) and make it the active catalog of this process.

    For a JSON host that obtains the catalog release itself. Refuses bytes
    that differ from ``expected_sha256`` and anything that is not a valid release (``CATALOG_INVALID``), a release in a
    catalog schema this Engine cannot read (``ENGINE_UPGRADE_REQUIRED``), and a different document under a
    ``catalog_version`` already installed. Several releases may be installed; a Project pinning one of them runs
    against it."""

    if isinstance(data, memoryview):
        data = data.tobytes()
    if not isinstance(data, (bytes, bytearray)):
        raise fail(LocalRunErrorCode.CATALOG_INVALID, "a catalog release is installed from its exact bytes")
    data = bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256:
        raise fail(LocalRunErrorCode.CATALOG_INVALID, f"the catalog bytes have SHA-256 {digest}, not the expected {expected_sha256}")
    try:
        document = release_header(data)
        check_compatible(document.get("schema_version"), document.get("catalog_version"))
    except CatalogError as exc:
        raise _local_error(exc) from None
    catalog = _memoized("bytes:" + digest, lambda: _parse_catalog(document))
    if isinstance(catalog, StoredCatalog):  # (the same bytes read from the store earlier: installed as the validated model)
        catalog = catalog.full()
    held = _INSTALLED_CATALOGS.get(catalog.catalog_version)
    if held is not None and held.sha256 != digest:
        raise fail(LocalRunErrorCode.CATALOG_INVALID,
                   f"a different public catalog {catalog.catalog_version} is already installed; a catalog version always denotes the same document")
    installed = InstalledCatalog(catalog, digest, len(data))
    _INSTALLED_CATALOGS[catalog.catalog_version] = installed
    if catalog.catalog_version in _INSTALL_ORDER:
        _INSTALL_ORDER.remove(catalog.catalog_version)
    _INSTALL_ORDER.append(catalog.catalog_version)
    return installed


def installed_catalogs() -> tuple[InstalledCatalog, ...]:
    """The releases installed in memory, in install order (the last one is active)."""

    return tuple(_INSTALLED_CATALOGS[version] for version in _INSTALL_ORDER)


def clear_installed_catalogs() -> None:
    """Forget every catalog installed in memory (the local store is used again)."""

    _INSTALLED_CATALOGS.clear()
    _INSTALL_ORDER.clear()


def catalog_resolution_status() -> dict[str, Any]:
    """Which catalog a run without an explicit catalog would use, without any network access: the active installed
    release (``source`` ``installed``), else the local store's active release (``source`` ``store``), else none."""

    installed = installed_catalogs()
    status: dict[str, Any] = {
        "available": False,
        "active": None,
        "installed": [{"catalog_version": item.catalog.catalog_version, "sha256": item.sha256} for item in installed],
        "supported_schema_versions": list(SUPPORTED_SCHEMA_VERSIONS),
    }
    if installed:
        active = installed[-1]
        status.update(available=True, active={"catalog_version": active.catalog.catalog_version, "schema_version": active.catalog.schema_version,
                                              "sha256": active.sha256, "source": "installed"})
        return status
    if not network_available():  # the browser runtime has no local store: its host installs the catalog
        return status
    from ..catalog.service import status as store_status

    try:
        report = store_status()
    except CatalogError:
        return status
    release = report.active
    if release is not None:
        # an active release in an earlier catalog schema is listed but cannot be used (``catalog sync`` replaces it)
        status.update(available=report.state.usable_active, active={"catalog_version": release.catalog_version, "schema_version": release.schema_version,
                                              "sha256": release.sha256, "source": "store"})
    return status


def resolved_release(catalog_version: str | None = None) -> tuple[str, str | None] | None:
    """The release ``load_public_catalog(None, catalog_version=...)`` uses now, without reading it: ``(catalog_version,
    sha256)`` -- the installed release ``catalog_version`` when it is held, else the last one installed; with none installed,
    the stored release ``catalog_version`` (its digest ``None``: a catalog version always denotes the same document), else the
    store's active release. ``None`` when that is not known without loading (no store in this runtime, or no active
    release): the load then says why."""

    if _INSTALL_ORDER:
        held = _INSTALLED_CATALOGS.get(catalog_version) if catalog_version is not None else None
        chosen = held or _INSTALLED_CATALOGS[_INSTALL_ORDER[-1]]
        return chosen.catalog.catalog_version, chosen.sha256
    if catalog_version is not None:
        return catalog_version, None
    active = catalog_resolution_status()["active"]
    return None if active is None else (active["catalog_version"], active["sha256"])


def pinned_catalog_version(project: Any) -> str | None:
    """The ``catalog_version`` the pinned catalog components of ``project`` name, when they all name the same one."""

    versions = {c.product.catalog_version for c in load_project(project).components if c.product is not None and c.product.catalog_version is not None}
    return next(iter(versions)) if len(versions) == 1 else None


def _stored(catalog_version: str | None) -> StoredCatalog:
    """The stored release as a ``StoredCatalog`` (module docstring), one per content digest in this process."""

    release, data = release_bytes(catalog_version)

    def read() -> StoredCatalog:
        document = _json(data, "public optical catalog", LocalRunErrorCode.CATALOG_INVALID)
        if validated_here(release):
            return StoredCatalog(document)
        # (stored by an Engine with another catalog contract, or before the contract was recorded: validated in full here)
        catalog = _parse_catalog(document)
        mark_release_validated(release)
        return StoredCatalog(document, catalog)

    return _memoized("bytes:" + release.sha256, read)


def exact_catalog(catalog_version: str) -> PublicOpticalCatalogV2 | None:
    """Release ``catalog_version`` exactly -- installed in memory, else from the local store (fetched when missing and
    automatic catalog access is on) -- or None when it cannot be obtained."""

    return exact_catalog_lookup(catalog_version)[0]


#: ``exact_catalog_lookup``'s reason when the catalog service does not distribute the release.
RELEASE_NOT_DISTRIBUTED = CatalogErrorCode.CATALOG_RELEASE_NOT_FOUND.value
#: ``exact_catalog_lookup``'s reason when the release is in an earlier catalog schema this Engine no longer reads.
RELEASE_UNREADABLE = "CATALOG_SCHEMA_RETIRED"


def automatic_catalog_access_off() -> bool:
    """Whether automatic catalog access is turned off (``OPENSCI_CATALOG_CHECK_INTERVAL=0``) on this device."""

    return network_available() and check_interval() == 0


def exact_catalog_lookup(catalog_version: str) -> tuple[PublicOpticalCatalogV2 | None, str | None]:
    """``exact_catalog`` and, when the release cannot be obtained, why: the code of the last attempt to obtain it
    (``CATALOG_RELEASE_NOT_FOUND``: the catalog service does not distribute it; ``CATALOG_SERVICE_UNREACHABLE``, ...),
    ``RELEASE_UNREADABLE`` (it is in an earlier catalog schema this Engine no longer reads), else
    ``CATALOG_NOT_AVAILABLE`` (not tried: automatic catalog access is off, or the browser runtime)."""

    installed = _INSTALLED_CATALOGS.get(catalog_version)
    if installed is not None:
        return installed.catalog, None
    if _INSTALL_ORDER or not network_available():
        return None, CatalogErrorCode.CATALOG_NOT_AVAILABLE.value
    try:
        return _stored(catalog_version), None
    except CatalogError as exc:
        if exc.details.get("retired"):
            return None, RELEASE_UNREADABLE
        return None, exc.details.get("last_attempt") or exc.code.value


def load_public_catalog(source: Any = None, *, catalog_version: str | None = None) -> PublicOpticalCatalogV2:
    """The public optical catalog a local run uses (module docstring): ``source`` (a document, JSON text / bytes, a
    file path or a model) when given; else the installed / stored release ``catalog_version`` (the release a Project
    pins) or the active one.

    Fails closed with ``LocalRunError``: ``CATALOG_INVALID`` (not a valid ``PublicOpticalCatalogV2``),
    ``CATALOG_NOT_AVAILABLE`` (no usable release on this device and none could be obtained) or
    ``ENGINE_UPGRADE_REQUIRED``.
    """

    if source is not None:
        return source if isinstance(source, (PublicOpticalCatalogV2, StoredCatalog)) else _catalog_from(source)
    if _INSTALL_ORDER:
        chosen = _INSTALLED_CATALOGS.get(catalog_version) if catalog_version is not None else None
        return (chosen or _INSTALLED_CATALOGS[_INSTALL_ORDER[-1]]).catalog
    try:
        return _stored(catalog_version)
    except CatalogError as exc:
        raise _local_error(exc) from None


def load_project(source: Any) -> ProjectDocumentV2:
    """A ``ProjectDocumentV2`` from a document (dict), a file path (``pathlib.Path`` or ``str``), JSON text
    (a ``str`` starting with ``{``) or bytes, or an equivalent model.

    Fails closed (``LocalRunError`` ``PROJECT_INVALID``) on anything that is not a valid v2 Project. The document is
    read by ``project_document.parse_project_document``. A source or declared optical model whose ``model_kind`` names
    physics outside the model's scope (fluorescence, Raman, chopper, lock-in) is refused first, as
    ``SIMULATION_MODEL_UNSUPPORTED`` with its boundary and route in ``details`` (``local.boundary``).
    """

    if isinstance(source, ProjectDocumentV2):
        return source
    document = _document(source, "project", LocalRunErrorCode.PROJECT_INVALID)
    from .boundary import out_of_scope_kind_problems

    refused = out_of_scope_kind_problems(document)  # (a model kind outside the model's scope: its boundary and route)
    if refused:
        raise LocalRunError(refused)
    try:
        return parse_project_document(document)
    except ValidationError as exc:
        raise fail(LocalRunErrorCode.PROJECT_INVALID, _validation_message(exc), _project_location(document, exc)) from None
    except RecursionError:
        raise fail(LocalRunErrorCode.PROJECT_INVALID, "the project is nested too deeply") from None


#: The Project lists whose items are named by an id (the location of a problem in one of them).
PROJECT_ITEM_IDS = {"components": ("component", "instance_id"), "observations": ("observation", "observation_id"),
                    "targets": ("target", "target_id"), "optical_models": ("optical model", "model_id"),
                    "model_materials": ("material", "material_id"), "model_coatings": ("coating", "coating_id")}


def _project_location(document: Any, exc: ValidationError) -> str | None:
    """``component L1`` / ``optical model lens1`` (the item of the first problem, by its id) when the first problem lies in
    a named list item."""

    errors = _reported_errors(exc)
    loc = errors[0]["loc"] if errors else ()
    if len(loc) < 2 or loc[0] not in PROJECT_ITEM_IDS or not isinstance(loc[1], int) or not isinstance(document, Mapping):
        return None
    kind, key = PROJECT_ITEM_IDS[loc[0]]
    items = document.get(loc[0])
    item = items[loc[1]] if isinstance(items, list) and 0 <= loc[1] < len(items) else None
    name = item.get(key) if isinstance(item, Mapping) else None
    where = f"{kind} {name}" if isinstance(name, str) and name else f"{kind} {loc[1] + 1}"
    return where + _surface_location(item, tuple(loc[2:]))


def _surface_location(item: Any, loc: tuple[Any, ...]) -> str:
    """`` surface S2`` when the problem lies in one surface of a declared model (``model.<KIND>.surfaces[k]``)."""

    node = item
    for position, part in enumerate(loc):
        if part == "surfaces":
            surfaces = node.get("surfaces") if isinstance(node, Mapping) else None
            index = loc[position + 1] if position + 1 < len(loc) else None
            if isinstance(surfaces, list) and isinstance(index, int) and 0 <= index < len(surfaces) and isinstance(surfaces[index], Mapping):
                surface_id = surfaces[index].get("surface_id")
                return f" surface {surface_id}" if isinstance(surface_id, str) and surface_id else f" surface {index + 1}"
            return ""
        if isinstance(node, Mapping) and isinstance(part, str) and part in node:
            node = node[part]
        elif not (isinstance(part, str) and part.isupper()):  # a discriminator tag (SURFACE_SEQUENCE) names no key
            return ""
    return ""


def mechanical_sources(cli_files: Any = (), plan_binding: MechanicalPlanBinding | None = None,
                       environ: Mapping[str, str] | None = None) -> MechanicalSources:
    """The public mechanical catalog documents a local assembly command uses: ``--mechanical`` files, else
    ``OS_OPTICS_MECHANICAL_CATALOG``, then what ``plan_binding`` binds, else the store's active OpenSci release
    (``os_optics.catalog.mechanical.resolve_sources``; no token, no network). ``environ`` defaults to the process
    environment. Failures raise ``MechanicalCatalogError`` with a ``MECHANICAL_*`` code."""

    return resolve_sources(tuple(cli_files), environ, plan_binding)
