"""Materials and coatings of an imported prescription: names -> the Project's self-contained model materials / coatings.

A medium named by a surface resolves, in this order, to

1. a material the prescription defines itself (``[materials]``; matched by its exact name, case-insensitively, when the
   surface names no maker and no ``ID:``): a Sellmeier or constant-index ``ProjectModelMaterialV1`` exactly as written
   (with ``index_reference`` ``AIR``: the glass catalogues' convention, stated as ``IndexReferenceV1`` with its vacuum
   band), held to the same rule as every Project material (its refractive index real and at least 1 throughout its band:
   no Sellmeier pole inside it; ``MATERIAL_INVALID`` otherwise);
2. with the glass source ``catalog``, a material published in the public optical catalog in use, resolved with the
   shared resolver of the public contracts (``material_physics.MaterialNameIndex``: the same rule the catalog builder
   uses), reading each published material's ``identity`` (maker, exact designation, aliases, catalogue tags):

   * ``ID:<id>``: that stable id, and nothing else;
   * a maker (``MAKER:DESIGNATION`` or the ``glass_manufacturer`` column): that maker's (or catalogue's) record answering
     to the name (its id, designation or an alias); several: ``GLASS_AMBIGUOUS``;
   * a name several records answer to and a catalogue order the prescription states (``glass_catalogs``): the record of
     the first catalogue in the order that has exactly one of them (``F2`` in a CDGM-first
     prescription is CDGM's F2);
   * without a deciding maker or order, a shared designation is ``GLASS_AMBIGUOUS`` even when one candidate has that
     stable id (bare ``F2`` names both SCHOTT's ``F2`` and CDGM's ``CDGM_F2``);
   * a unique exact stable id, designation or alias resolves to its record; several candidates name their maker,
     designation and how to choose;
   * else a loose spelling (case, spaces, ``-``, ``_`` and ``.`` ignored: ``NBK7`` -> ``N-BK7``) when exactly one record
     matches it, reported (``GLASS_NAME_MATCHED``); several: ``GLASS_AMBIGUOUS``.

   With a stated catalogue order, a glass the prescription does not qualify by maker that resolves to a record outside
   every listed catalogue (its maker and catalogue tags; e.g. Zemax ``MISC`` ``PMMA`` -> a literature model) is a
   substitution, not the specified curve: reported (``GLASS_OUTSIDE_CATALOGUES``) and marked ``outside_catalogue_order``
   in the report's ``glasses`` (a record without identity cannot be checked and is not marked).

   A catalog without identities (before schema 3.6) resolves by id and loose id as before. The published material's
   complete optical definition is copied verbatim into the Project (``catalog_copy.copy_catalog_material``, the copy
   every route uses: dispersion, valid band, index convention, bulk loss, thermo-optic data, and ``copied_from``: the
   catalog release, the chosen id and the source edition), so the Project reloads without the catalog and without a
   second name lookup.

Anything else is ``GLASS_UNKNOWN``, naming the glass and the ways to resolve it (the closest published names, a
definition in ``[materials]``). A coating named by a surface resolves to the coating the prescription defines
(``[coatings]``, matched by its exact name, case-insensitively; ``inline_coating``) or else, with the glass source
``catalog``, to a published coating id, copied verbatim (``COATING_UNKNOWN`` otherwise). A defined coating becomes a
``PublicCoatingV1`` held to the coating contract (``COATING_INVALID`` with the contract's reason otherwise):

* no node states ``aoi_deg``: a ``spectrum`` over its wavelengths (sorted), unpolarized or, when a node states s / p
  values, polarization-resolved (every node then states all four; an unpolarized value left blank is their mean, exactly);
* every node states ``aoi_deg``: an ``angular_spectrum`` on the grid of its distinct wavelengths and angles (every grid
  point exactly once, all four s / p values on each; the angle is read in the incident medium, ``aoi_reference``
  ``INCIDENT_MEDIUM``), valid by default over its own angles;
* phases: a phase column with the same value on every row is a constant phase; otherwise the phases are tabulated over
  the coating's wavelengths (an angle-resolved coating states one phase per wavelength: the same on every angle of it).

A spectrum states the angles it holds for (``min_aoi_deg`` / ``max_aoi_deg``). The data status defaults to
``DECLARED``, the basis to "Defined in the imported prescription.". A defined coating no surface uses is reported
(``COATING_UNUSED``) and not imported.

The wavelength band of the imported models is the band the prescription (or the caller) states where it lies inside the
band of every material and coating used; a stated band that reaches beyond one of them is narrowed to the part they all
cover (warning ``BAND_NARROWED``, naming what limits it), and one that shares no wavelength with them is refused
(``MATERIAL_OUTSIDE_BAND``). Without a stated band it is the intersection of those bands.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from pydantic import ValidationError

from .._vendor.shared_contracts.common.decimal_string import decimal_to_string
from .._vendor.shared_contracts.design_catalog import FactStatus, PublicCoatingV1, PublicOpticalCatalogV2, VACUUM, WavelengthBandV1
from .._vendor.shared_contracts.optical_design.optical_model import ConstantIndexDispersionV1, ProjectModelMaterialV1
from .._vendor.shared_contracts.design_catalog.material_physics import STANDARD_REFERENCE_AIR, IndexReferenceV1, MaterialNameIndex
from .._vendor.shared_contracts.design_catalog.optical_catalog import SellmeierDispersionV1
from ..catalog_copy import copy_catalog_material
from .errors import ImportWarning, PrescriptionErrorCode as E, ProblemList, SourceLocation
from .ir import LAW_AIR, CoatingDefinition, CoatingNode, ConstantIndexDefinition, MaterialDefinition, MaterialRef, Prescription

GLASS_SOURCES = ("catalog", "inline")
#: How many published names an unknown glass's message suggests.
SUGGESTIONS = 3
_LOOSE = re.compile(r"[\s._-]+")

CatalogSource = Callable[[], PublicOpticalCatalogV2]


def _loose(name: str) -> str:
    return _LOOSE.sub("", name.upper())


def _distance(a: str, b: str) -> int:
    """Levenshtein distance (for suggestions only)."""

    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


class _Catalog:
    """The catalog in use, loaded once on first need; ``None`` with ``reason`` when there is none."""

    def __init__(self, source: PublicOpticalCatalogV2 | CatalogSource | None, glass_source: str) -> None:
        self._source = source
        self._enabled = glass_source == "catalog"
        self._loaded = False
        self._catalog: PublicOpticalCatalogV2 | None = None
        self._index: MaterialNameIndex | None = None
        self.reason = "the glass source is 'inline': only materials defined in [materials] are used" if not self._enabled else ""

    def get(self) -> PublicOpticalCatalogV2 | None:
        if not self._enabled:
            return None
        if not self._loaded:
            self._loaded = True
            if self._source is None:
                self.reason = "no public optical catalog is available here"
            elif not callable(self._source):  # a catalog (the contract model, or a store release read as one)
                self._catalog = self._source
            else:
                try:
                    self._catalog = self._source()
                except ValueError as exc:  # LocalRunError (CATALOG_NOT_AVAILABLE ...) is a ValueError
                    self.reason = f"no public optical catalog is available on this device ({exc}); run 'os-optics catalog sync'"
        return self._catalog

    def index(self) -> MaterialNameIndex:
        """The shared name index of the catalog's materials (built once)."""

        if self._index is None:
            self._index = MaterialNameIndex(self.get().materials)  # type: ignore[union-attr]
        return self._index


@dataclass(frozen=True)
class ResolvedMaterials:
    """The resolution of every medium and coating of a prescription.

    ``material_ids``: the material id of each medium name as written (``None`` for air). ``materials`` /
    ``coatings``: the Project model materials / coatings used, sorted by id. ``from_catalog``: the ids copied from the
    catalog. ``band``: the models' wavelength band. ``catalog_version``: the catalog release glasses or coatings were
    copied from (``None`` when none was used). ``glasses``: how each glass as written resolved (``written``,
    ``material_id``, ``how``, and for a published one its ``manufacturer``, ``designation``, the ``candidates`` a catalogue
    order chose among).
    """

    material_ids: dict[str, str]
    materials: tuple[ProjectModelMaterialV1, ...]
    coatings: tuple[PublicCoatingV1, ...]
    from_catalog: frozenset[str]
    band: WavelengthBandV1 | None
    band_basis: str
    catalog_version: str | None
    warnings: tuple[ImportWarning, ...]
    glasses: tuple[dict[str, object], ...] = ()


def _inline_material(definition: MaterialDefinition) -> ProjectModelMaterialV1:
    if isinstance(definition.dispersion, ConstantIndexDefinition):
        dispersion: object = ConstantIndexDispersionV1(refractive_index=decimal_to_string(definition.dispersion.refractive_index))
    else:
        dispersion = SellmeierDispersionV1(b=tuple(decimal_to_string(v) for v in definition.dispersion.b),
                                           c_um2=tuple(decimal_to_string(v) for v in definition.dispersion.c_um2))
    low, high = definition.min_nm, definition.max_nm
    reference = None
    if definition.index_reference == LAW_AIR:  # the glass catalogues' convention (ir.MaterialDefinition)
        reference = IndexReferenceV1(wavelength="AIR", index="RELATIVE_TO_AIR", reference_air=STANDARD_REFERENCE_AIR,
                                     source_band_nm=(decimal_to_string(low), decimal_to_string(high)), stated_by="SOURCE_TEXT",
                                     basis="Stated in the imported prescription ([materials] index_reference AIR).")
        low, high = reference.vacuum_band()
    return ProjectModelMaterialV1(
        material_id=definition.name, display_name=definition.display_name or definition.name, dispersion=dispersion,
        valid_band_nm=WavelengthBandV1(min_nm=decimal_to_string(low), max_nm=decimal_to_string(high), status=FactStatus.SPECIFIED),
        absorption_coefficient_per_mm=decimal_to_string(definition.absorption_per_mm) if definition.absorption_per_mm is not None else None,
        basis=definition.basis or "Defined in the imported prescription.", index_reference=reference,
    )


_POWER_COLUMNS = ("reflectance_s", "reflectance_p", "transmittance_s", "transmittance_p")
_PHASE_COLUMNS = ("reflection_s_rad", "reflection_p_rad", "transmission_s_rad", "transmission_p_rad")
_PHASE_TEXT = {"reflection_s_rad": "reflection_phase_s", "reflection_p_rad": "reflection_phase_p",
               "transmission_s_rad": "transmission_phase_s", "transmission_p_rad": "transmission_phase_p"}
DECLARED_COATING_BASIS = "Defined in the imported prescription."


def _text(value: Decimal) -> str:
    return decimal_to_string(value)


def _spectrum(definition: CoatingDefinition, nodes: list[CoatingNode], problems: ProblemList) -> dict | None:
    resolved = any(getattr(n, c) is not None for n in nodes for c in _POWER_COLUMNS)
    table: dict[str, list[str]] = {key: [] for key in ("wavelength_nm", "reflectance", "transmittance", *(_POWER_COLUMNS if resolved else ()))}
    ok = True
    for node in nodes:
        if resolved:
            values = [getattr(node, c) for c in _POWER_COLUMNS]
            if any(v is None for v in values):
                problems.add(E.COATING_INVALID, f"coating {definition.name}: a polarization-resolved coating states reflectance_s, "
                             "reflectance_p, transmittance_s and transmittance_p on every row", node.location)
                ok = False
                continue
            r_s, r_p, t_s, t_p = values
            mean_r = node.reflectance if node.reflectance is not None else (r_s + r_p) / 2  # type: ignore[operator]
            mean_t = node.transmittance if node.transmittance is not None else (t_s + t_p) / 2  # type: ignore[operator]
            for key, value in zip(_POWER_COLUMNS, values):
                table[key].append(_text(value))  # type: ignore[arg-type]
        else:
            if node.reflectance is None or node.transmittance is None:
                problems.add(E.COATING_INVALID, f"coating {definition.name}: each row states reflectance and transmittance (or the four "
                             "s / p values)", node.location)
                ok = False
                continue
            mean_r, mean_t = node.reflectance, node.transmittance
        table["wavelength_nm"].append(_text(node.wavelength_nm))
        table["reflectance"].append(_text(mean_r))
        table["transmittance"].append(_text(mean_t))
    return table if ok else None


def _angular(definition: CoatingDefinition, nodes: list[CoatingNode], problems: ProblemList) -> dict | None:
    wavelengths = sorted({n.wavelength_nm for n in nodes})
    angles = sorted({n.aoi_deg for n in nodes})  # type: ignore[type-var]
    grid: dict[tuple[Decimal, Decimal], CoatingNode] = {}
    ok = True
    for node in nodes:
        key = (node.wavelength_nm, node.aoi_deg)
        if key in grid:
            problems.add(E.COATING_INVALID, f"coating {definition.name}: {_text(key[0])} nm at {_text(key[1])} deg is given twice "  # type: ignore[arg-type]
                         f"(first on row {grid[key].location.row})", node.location)
            ok = False
            continue
        if node.reflectance is not None or node.transmittance is not None or any(getattr(node, c) is None for c in _POWER_COLUMNS):
            problems.add(E.COATING_INVALID, f"coating {definition.name}: an angle-resolved coating states reflectance_s, reflectance_p, "
                         "transmittance_s and transmittance_p on every row (and no unpolarized reflectance / transmittance)", node.location)
            ok = False
        grid[key] = node  # type: ignore[index]
    missing = [(w, a) for w in wavelengths for a in angles if (w, a) not in grid]
    if missing:
        listed = ", ".join(f"{_text(w)} nm at {_text(a)} deg" for w, a in missing[:5])
        problems.add(E.COATING_INVALID, f"coating {definition.name}: an angle-resolved coating gives every wavelength at every angle; "
                     f"missing {listed}{' ...' if len(missing) > 5 else ''}", definition.location)
        ok = False
    if not ok:
        return None
    table: dict[str, object] = {"wavelength_nm": [_text(w) for w in wavelengths], "aoi_deg": [_text(a) for a in angles]}  # type: ignore[arg-type]
    for column in _POWER_COLUMNS:
        table[column] = [[_text(getattr(grid[(w, a)], column)) for a in angles] for w in wavelengths]
    return table


def _phase(definition: CoatingDefinition, nodes: list[CoatingNode], problems: ProblemList) -> dict | None:
    stated = [c for c in _PHASE_COLUMNS if any(getattr(n, c) is not None for n in nodes)]
    if not stated:
        return None
    per_wavelength: dict[str, dict[Decimal, Decimal]] = {c: {} for c in stated}
    for node in nodes:
        for column in stated:
            value = getattr(node, column)
            if value is None:
                problems.add(E.COATING_INVALID, f"coating {definition.name}: {_PHASE_TEXT[column]} is stated on some rows and not on this "
                             "one; give it on every row", node.location)
                return None
            known = per_wavelength[column].setdefault(node.wavelength_nm, value)
            if known != value:
                problems.add(E.COATING_INVALID, f"coating {definition.name}: {_PHASE_TEXT[column]} differs between angles at "
                             f"{_text(node.wavelength_nm)} nm; a coating's phases depend on wavelength only", node.location)
                return None
    wavelengths = sorted({n.wavelength_nm for n in nodes})
    phase: dict[str, object] = {}
    if all(len(set(per_wavelength[c].values())) == 1 for c in stated):
        for column in stated:
            phase[column] = _text(next(iter(per_wavelength[column].values())))
    else:
        phase["wavelength_nm"] = [_text(w) for w in wavelengths]
        for column in stated:
            phase[column] = [_text(per_wavelength[column][w]) for w in wavelengths]
    return phase


def inline_coating(definition: CoatingDefinition, problems: ProblemList) -> PublicCoatingV1 | None:
    """The ``PublicCoatingV1`` of a coating the prescription defines (module docstring), or None with the problems added."""

    nodes = sorted(definition.nodes, key=lambda n: (n.wavelength_nm, n.aoi_deg if n.aoi_deg is not None else Decimal(0)))
    if not nodes:
        problems.add(E.COATING_INVALID, f"coating {definition.name} has no row with a wavelength", definition.location)
        return None
    with_angle = [n for n in nodes if n.aoi_deg is not None]
    if with_angle and len(with_angle) != len(nodes):
        problems.add(E.COATING_INVALID, f"coating {definition.name}: give aoi_deg on every row (an angle-resolved table) or on none "
                     "(a spectrum)", definition.location)
        return None
    status = definition.status or FactStatus.DECLARED.value
    document: dict[str, object] = {
        "coating_id": definition.name, "kind": definition.kind, "display_name": definition.display_name or definition.name,
        "basis": definition.basis or DECLARED_COATING_BASIS}
    if with_angle:
        table = _angular(definition, nodes, problems)
        if table is None:
            return None
        document["angular_spectrum"] = {**table, "status": status}
        low = definition.min_aoi_deg if definition.min_aoi_deg is not None else Decimal(table["aoi_deg"][0])  # type: ignore[index]
        high = definition.max_aoi_deg if definition.max_aoi_deg is not None else Decimal(table["aoi_deg"][-1])  # type: ignore[index]
        document["aoi_reference"] = definition.aoi_reference or "INCIDENT_MEDIUM"
    else:
        seen: dict[Decimal, CoatingNode] = {}
        for node in nodes:
            if node.wavelength_nm in seen:
                problems.add(E.COATING_INVALID, f"coating {definition.name}: {_text(node.wavelength_nm)} nm is given twice (first on row "
                             f"{seen[node.wavelength_nm].location.row})", node.location)
                return None
            seen[node.wavelength_nm] = node
        spectrum = _spectrum(definition, nodes, problems)
        if spectrum is None:
            return None
        document["spectrum"] = {**spectrum, "status": status}
        if definition.min_aoi_deg is None or definition.max_aoi_deg is None:
            problems.add(E.COATING_INVALID, f"coating {definition.name}: a spectrum states the angles of incidence it holds for "
                         "(min_aoi_deg and max_aoi_deg on its first row)", definition.location)
            return None
        low, high = definition.min_aoi_deg, definition.max_aoi_deg
        if definition.aoi_reference is not None:
            document["aoi_reference"] = definition.aoi_reference
    document["valid_aoi_deg"] = {"min_deg": _text(low), "max_deg": _text(high), "status": status}
    before = len(problems.items)
    phase = _phase(definition, nodes, problems)
    if len(problems.items) > before:
        return None
    if phase is not None:
        document["phase"] = {**phase, "status": status}
    try:
        return PublicCoatingV1.model_validate(document)
    except ValidationError as exc:
        reasons = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {str(e['msg']).removeprefix('Value error, ')}" if e["loc"]
                            else str(e["msg"]).removeprefix("Value error, ") for e in exc.errors(include_url=False))
        problems.add(E.COATING_INVALID, f"coating {definition.name}: {reasons}", definition.location)
        return None


def _suggest(name: str, ids: list[str]) -> list[str]:
    key = _loose(name)
    return sorted(ids, key=lambda candidate: (_distance(key, _loose(candidate)), candidate))[:SUGGESTIONS]


def _who(material: object) -> str:
    """``SCHOTT_F5 (SCHOTT F5)``: a published material's id and, when it states its identity, maker and designation."""

    identity = getattr(material, "identity", None)
    if identity is None:
        return material.material_id  # type: ignore[attr-defined]
    named = f"{identity.manufacturer} {identity.designation}" if identity.manufacturer else identity.designation
    return f"{material.material_id} ({named})"  # type: ignore[attr-defined]


def _names(material: object) -> list[str]:
    identity = getattr(material, "identity", None)
    names = [material.material_id]  # type: ignore[attr-defined]
    if identity is not None:
        names += [identity.designation, *identity.aliases]
        if identity.manufacturer:
            names.append(f"{identity.manufacturer}:{identity.designation}")
    return names


HOW_TO_CHOOSE = ("write MAKER:DESIGNATION (e.g. SCHOTT:F5; or give the glass_manufacturer column), the stable id (ID:<material id>), "
                 "or state the prescription's catalogue order (#! glass_catalogs = CDGM SCHOTT)")


def _resolve_glass(ref: MaterialRef, catalog: PublicOpticalCatalogV2, index: MaterialNameIndex, order: tuple[str, ...],
                   problems: ProblemList, warnings: list[ImportWarning]) -> tuple[object, dict[str, object]] | None:
    """The published material a glass names, by the shared resolver (``material_physics.MaterialNameIndex``; module
    docstring), with its resolution record; ``None`` with the problem added."""

    by_key = {m.material_id.upper(): m for m in catalog.materials}
    written = ref.key
    version = catalog.catalog_version
    if ref.explicit_id:
        published = by_key.get(ref.name.upper())
        if published is None:
            options = _suggest(ref.name, [m.material_id for m in catalog.materials])
            problems.add(E.GLASS_UNKNOWN, f"glass {written!r} names the stable id {ref.name.upper()}, which catalog {version} does not "
                         f"publish (closest ids: {', '.join(options)})", ref.location)
            return None
        return published, {"written": written, "material_id": published.material_id, "how": "material_id (explicit)", **_identity(published)}
    loose = False
    if ref.manufacturer is not None:
        result = index.resolve(ref.name, manufacturer=ref.manufacturer)
        if result.how == "none":
            result = index.resolve(ref.name, manufacturer=ref.manufacturer, loose=True)
            loose = result.resolved is not None
    else:
        result = index.resolve(ref.name, catalogue_order=order)
        if result.how == "none":
            result = index.resolve(ref.name, loose=True)
            loose = result.how == "loose match"
    if result.ambiguous:
        candidates = ", ".join(_who(by_key[m]) for m in result.candidates)
        by = (f" of manufacturer {ref.manufacturer}" if ref.manufacturer is not None
              else f" (the catalogue order {' '.join(order)} does not decide it)" if order else "")
        problems.add(E.GLASS_AMBIGUOUS, f"glass {written!r} matches several published materials{by}: {candidates}; {HOW_TO_CHOOSE}",
                     ref.location)
        return None
    if result.resolved is None:
        if ref.manufacturer is not None and not any(n.made_by(ref.manufacturer) for n in index.materials.values()):
            makers = sorted({n.manufacturer for n in index.materials.values() if n.manufacturer})
            problems.add(E.GLASS_UNKNOWN, f"glass {written!r}: catalog {version} publishes no material of manufacturer or catalogue "
                         f"{ref.manufacturer!r} (manufacturers: {', '.join(makers) or 'none stated'})", ref.location)
            return None
        pool = [m for m in catalog.materials if ref.manufacturer is None or index.materials[m.material_id.upper()].made_by(ref.manufacturer)]
        options = _suggest(ref.name, sorted({name for m in pool for name in _names(m)}))
        problems.add(E.GLASS_UNKNOWN, f"glass {written!r} is neither defined in [materials] nor published in catalog {version} "
                     f"(closest published: {', '.join(options)}). Use a published glass, or define it in [materials] (kind SELLMEIER "
                     "with b1..b3, c1..c3, or CONSTANT_INDEX with index, and min_nm / max_nm)", ref.location)
        return None
    published = by_key[result.resolved]
    record: dict[str, object] = {"written": written, "material_id": published.material_id,
                                 "how": "loose match" if loose else result.how, **_identity(published)}
    if loose:
        warnings.append(ImportWarning("GLASS_NAME_MATCHED", f"glass {written!r} is read as the published material {_who(published)} "
                                      "(a loose spelling: case, spaces, '-', '_' and '.' ignored); write its exact designation or id",
                                      ref.location))
    if result.how == "catalogue order":
        record["candidates"] = list(result.candidates)
        record["catalogue_order"] = list(order)
    identity = getattr(published, "identity", None)
    if order and ref.manufacturer is None and identity is not None:
        inside = any(index.materials[published.material_id.upper()].made_by(tag) for tag in order)
        record["outside_catalogue_order"] = not inside
        if not inside:
            source = identity.manufacturer or f"no maker: {published.basis}"
            warnings.append(ImportWarning(
                "GLASS_OUTSIDE_CATALOGUES", f"glass {written!r}: the prescription's catalogues are {' '.join(order)}; it resolved to "
                f"{published.material_id} ({source}), outside them -- a different source's curve: a substitution, not the specified "
                "curve. Check it, or define the specified glass in [materials]", ref.location))
    return published, record


def _identity(published: object) -> dict[str, object]:
    identity = getattr(published, "identity", None)
    if identity is None:
        return {}
    out: dict[str, object] = {"designation": identity.designation, "source_edition": identity.source_edition}
    if identity.manufacturer:
        out["manufacturer"] = identity.manufacturer
    return out


def resolve_materials(prescription: Prescription, *, catalog: PublicOpticalCatalogV2 | CatalogSource | _Catalog | None, glass_source: str,
                      band_nm: tuple[Decimal, Decimal] | None, band_stated_by: str | None, problems: ProblemList
                      ) -> ResolvedMaterials | None:
    """Resolve every medium and coating (module docstring); problems are added to ``problems`` (``None`` returned then)."""

    source = catalog if isinstance(catalog, _Catalog) else _Catalog(catalog, glass_source)
    warnings: list[ImportWarning] = []
    inline = {d.name: d for d in prescription.materials}
    material_ids: dict[str, str] = {}
    chosen: dict[str, ProjectModelMaterialV1] = {}
    first_use: dict[str, SourceLocation] = {}
    from_catalog: set[str] = set()
    refused: set[str] = set()
    glasses: list[dict[str, object]] = []
    used_catalog = False
    for surface in prescription.surfaces:
        ref = surface.material
        if ref is None or ref.key in material_ids:
            continue
        exact = ref.name.upper()
        if exact == VACUUM:
            if ref.manufacturer is not None or ref.explicit_id:
                problems.add(E.MATERIAL_INVALID, f"{ref.key!r} names the reserved VACUUM medium, not a maker's grade or a material id; "
                             "write VACUUM alone for an evacuated gap", ref.location)
                continue
            material_ids[ref.key] = VACUUM
            continue  # a reserved medium, not a glass: no catalog lookup and no Project material copy
        if exact in inline and ref.manufacturer is None and not ref.explicit_id:
            if exact in from_catalog:
                problems.add(E.MATERIAL_INVALID, f"material {exact} is defined in [materials] and is also the published material another "
                             "surface names; rename the [materials] definition", ref.location)
                continue
            material_ids[ref.key] = exact
            glasses.append({"written": ref.key, "material_id": exact, "how": "defined in [materials]"})
            if exact not in chosen and exact not in refused:
                try:
                    chosen[exact] = _inline_material(inline[exact])
                except ValidationError as exc:  # the contract's physics rule (a pole in the band, n < 1, ...)
                    refused.add(exact)
                    reasons = "; ".join(str(e["msg"]).removeprefix("Value error, ") for e in exc.errors(include_url=False))
                    problems.add(E.MATERIAL_INVALID, f"{reasons}; correct the definition (or its min_nm / max_nm)",
                                 inline[exact].location)
                    continue
            first_use.setdefault(exact, ref.location)
            continue
        published_catalog = source.get()
        if published_catalog is None:
            problems.add(E.GLASS_UNKNOWN, f"glass {ref.key!r} is not defined in [materials], and {source.reason}. Define it in "
                         "[materials] (kind SELLMEIER with b1..b3, c1..c3, or CONSTANT_INDEX with index, and min_nm / max_nm)",
                         ref.location)
            continue
        found = _resolve_glass(ref, published_catalog, source.index(), prescription.glass_catalogs, problems, warnings)
        if found is None:
            continue
        published, record = found
        if published.material_id in chosen and published.material_id not in from_catalog or published.material_id in refused:
            problems.add(E.MATERIAL_INVALID, f"glass {ref.key!r} names the published material {published.material_id}, and [materials] "
                         f"defines a material of that id too; rename the [materials] definition", ref.location)
            continue
        try:
            copy = chosen.get(published.material_id) or copy_catalog_material(published, published_catalog.catalog_version)
        except ValidationError as exc:  # a published record the Project contract refuses (should not happen)
            reasons = "; ".join(str(e["msg"]).removeprefix("Value error, ") for e in exc.errors(include_url=False))
            problems.add(E.MATERIAL_INVALID, f"published material {published.material_id} cannot be copied into a Project: {reasons}",
                         ref.location)
            continue
        used_catalog = True
        material_ids[ref.key] = published.material_id
        glasses.append(record)
        chosen.setdefault(published.material_id, copy)
        first_use.setdefault(published.material_id, ref.location)
        from_catalog.add(published.material_id)
    for name, definition in inline.items():
        if name not in chosen and name not in refused:
            warnings.append(ImportWarning("MATERIAL_UNUSED", f"material {name} is defined but no surface uses it; it is not imported",
                                          definition.location))
    coatings: dict[str, PublicCoatingV1] = {}
    coating_use: dict[str, SourceLocation] = {}
    defined = {d.name: d for d in prescription.coatings}
    refused_coatings: set[str] = set()
    for surface in prescription.surfaces:
        ref = surface.coating
        if ref is None or ref.coating_id in coatings or ref.coating_id in refused_coatings:
            continue
        if ref.coating_id in defined:
            coating = inline_coating(defined[ref.coating_id], problems)
            if coating is None:
                refused_coatings.add(ref.coating_id)
                continue
            coatings[ref.coating_id] = coating
            coating_use[ref.coating_id] = ref.location
            continue
        published_catalog = source.get()
        coating = published_catalog.coating(ref.coating_id) if published_catalog is not None else None
        if coating is None:
            where = (f"published in catalog {published_catalog.catalog_version}" if published_catalog is not None else source.reason)
            problems.add(E.COATING_UNKNOWN, f"coating {ref.coating_id!r} is neither defined in [coatings] nor {where}; define it in "
                         "[coatings], give a published coating id (os-optics catalog coatings), or leave the cell blank for an "
                         "uncoated surface", ref.location)
            continue
        used_catalog = True
        coatings[ref.coating_id] = coating
        coating_use[ref.coating_id] = ref.location
    for name, definition in defined.items():
        if name not in coatings and name not in refused_coatings:
            warnings.append(ImportWarning("COATING_UNUSED", f"coating {name} is defined but no surface uses it; it is not imported",
                                          definition.location))
    if problems:
        return None
    band, basis = _band(chosen, first_use, coatings, coating_use, band_nm, band_stated_by, problems, warnings)
    if problems:
        return None
    catalog = source.get() if used_catalog else None
    return ResolvedMaterials(
        material_ids=material_ids, materials=tuple(chosen[k] for k in sorted(chosen)), coatings=tuple(coatings[k] for k in sorted(coatings)),
        from_catalog=frozenset(from_catalog), band=band, band_basis=basis,
        catalog_version=catalog.catalog_version if catalog is not None else None, warnings=tuple(warnings), glasses=tuple(glasses),
    )


def _band(materials: dict[str, ProjectModelMaterialV1], material_use: dict[str, SourceLocation], coatings: dict[str, PublicCoatingV1],
          coating_use: dict[str, SourceLocation], band_nm: tuple[Decimal, Decimal] | None, stated_by: str | None,
          problems: ProblemList, warnings: list[ImportWarning]) -> tuple[WavelengthBandV1 | None, str]:
    spans: list[tuple[str, Decimal, Decimal, SourceLocation]] = [
        (f"material {key}", Decimal(m.valid_band_nm.min_nm), Decimal(m.valid_band_nm.max_nm), material_use[key]) for key, m in materials.items()]
    spans += [(f"coating {key}", *c.span_nm, coating_use[key]) for key, c in coatings.items()]
    spans += [(f"coating {key}'s phase table", *c.phase.span_nm, coating_use[key]) for key, c in coatings.items()  # type: ignore[union-attr]
              if c.phase is not None and c.phase.span_nm is not None]
    if band_nm is not None:
        low, high = band_nm
        limiting = [(what, a, b, where) for what, a, b, where in spans if not (a <= low and high <= b)]
        if not limiting:
            return (WavelengthBandV1(min_nm=decimal_to_string(low), max_nm=decimal_to_string(high), status=FactStatus.SPECIFIED),
                    stated_by or "stated")
        inner_low, inner_high = max(low, *(a for _w, a, _b, _l in limiting)), min(high, *(b for _w, _a, b, _l in limiting))
        if not inner_low <= inner_high:
            # (nothing of the stated band is covered by all of them: each one wholly outside it is named, else the set)
            apart = [(what, a, b, where) for what, a, b, where in limiting if b < low or high < a]
            for what, a, b, where in apart:
                problems.add(E.MATERIAL_OUTSIDE_BAND, f"the wavelength band {low}-{high} nm ({stated_by}) lies outside {what}'s valid band "
                             f"{a}-{b} nm; state a band inside it or use a material characterized there", where)
            if not apart:
                names = ", ".join(f"{what} {a}-{b} nm" for what, a, b, _l in limiting)
                problems.add(E.MATERIAL_OUTSIDE_BAND, f"no part of the wavelength band {low}-{high} nm ({stated_by}) is inside the valid "
                             f"band of every material and coating used ({names}); use materials characterized over a common band",
                             limiting[0][3])
            return None, ""
        names = ", ".join(f"{what} ({a}-{b} nm)" for what, a, b, _l in limiting)
        warnings.append(ImportWarning("BAND_NARROWED", f"the wavelength band {low}-{high} nm ({stated_by}) is narrowed to "
                                      f"{inner_low}-{inner_high} nm, the part inside the valid band of {names}", limiting[0][3]))
        return (WavelengthBandV1(min_nm=decimal_to_string(inner_low), max_nm=decimal_to_string(inner_high), status=FactStatus.DERIVED),
                f"{stated_by or 'stated'}, narrowed to the valid bands of the materials and coatings used")
    if not spans:
        return None, "none (no material)"
    low = max(a for _w, a, _b, _l in spans)
    high = min(b for _w, _a, b, _l in spans)
    if not low <= high:
        names = ", ".join(f"{what} {a}-{b} nm" for what, a, b, _l in spans)
        problems.add(E.MATERIAL_OUTSIDE_BAND, f"the materials and coatings share no wavelength band ({names}); use materials "
                     "characterized over a common band", spans[0][3])
        return None, ""
    return (WavelengthBandV1(min_nm=decimal_to_string(low), max_nm=decimal_to_string(high), status=FactStatus.DERIVED),
            "the intersection of the valid bands of the materials and coatings used")


__all__ = ["GLASS_SOURCES", "ResolvedMaterials", "resolve_materials"]
