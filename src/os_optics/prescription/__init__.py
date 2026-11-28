"""Import sequential optical prescriptions as native models.

``read_prescription_project(path, *, catalog=None)`` opens a supported file as a standalone Project with
editable elements, field probes, and the prescribed image plane or configuration states.

``import_prescription(text, format="csv-surface-table/1", ...)`` reads the text with the format's adapter into the
format-neutral IR (``ir.Prescription``), resolves its glasses and coatings (``glass.py``) and lowers it (``lowering.py``)
into one ``COMPOUND`` optical model whose members are the prescription's physical elements (``SURFACE_SEQUENCE``) and
aperture stops (``APERTURE_STOP``), with the ``model_materials`` / ``model_coatings`` they use, a suggested
``custom_part`` component that places the unit, and an import report. ``merge_into_project`` adds all of it to a Project.

The imported models are ordinary Project-native models: the local runtime compiles them with the same code as every
other optic, so the imported optics are traced, inspected and reported like natively authored ones. An import is
complete or refused (``PrescriptionImportError`` with every problem located by row, column and surface); a partial model
is never returned. Pure Python: it runs unchanged in the browser (Pyodide).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from pydantic import ValidationError

from .._vendor.shared_contracts.common.decimal_string import decimal_to_string
from ..catalog_copy import material_conflict
from ..project_document import parse_project_document
from .._vendor.shared_contracts.common.geometry import PoseV1
from .._vendor.shared_contracts.design_catalog import PublicCoatingV1, PublicOpticalCatalogV2
from .._vendor.shared_contracts.optical_design import OpticalComponentInstanceV1, ProjectDocumentV2
from .._vendor.shared_contracts.optical_design.optical_model import (
    DEFAULT_ENVIRONMENT,
    ProjectModelMaterialV1,
    ProjectOpticalModelV1,
    PropagationEnvironmentV1,
)
from .csv_table import FORMAT_ID as CSV_SURFACE_TABLE, read_csv_surface_table
from .errors import (
    PRESCRIPTION_IMPORT_INVALID,
    ImportWarning,
    PrescriptionErrorCode,
    PrescriptionImportError,
    PrescriptionProblem,
    ProblemList,
    SourceLocation,
    refuse,
)
from .glass import GLASS_SOURCES, CatalogSource, resolve_materials
from .ir import MM_PER_UNIT, UNIT_SPELLINGS, WAVELENGTHS_AIR, Prescription
from .lowering import lower_prescription

#: Prescription formats the import reads.
PRESCRIPTION_FORMATS = (CSV_SURFACE_TABLE,)
READERS = {CSV_SURFACE_TABLE: read_csv_surface_table}
DEFAULT_MODEL_ID = "prescription"
#: A model id prefix leaves room for the member suffix (``-STOP64``) within the 64-character model id.
MODEL_ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,56}$")
IMPORT_ROLE = "lens"
DESCRIPTION_MAX = 1000
_E = PrescriptionErrorCode


@dataclass(frozen=True)
class PrescriptionImport:
    """A complete import: the IR read, the native models (the ``COMPOUND`` unit first), the materials and coatings they
    use, the suggested component and the import report."""

    prescription: Prescription
    optical_models: tuple[ProjectOpticalModelV1, ...]
    model_materials: tuple[ProjectModelMaterialV1, ...]
    model_coatings: tuple[PublicCoatingV1, ...]
    component: OpticalComponentInstanceV1
    report: dict[str, Any]
    #: The reference environment the prescription states (``None``: it states none; the Project's own applies).
    environment: PropagationEnvironmentV1 | None = None

    @property
    def model_id(self) -> str:
        return self.optical_models[0].model_id

    def as_dict(self) -> dict[str, Any]:
        def dump(item) -> dict[str, Any]:
            return item.model_dump(mode="json", by_alias=True, exclude_none=True)

        out = {
            "format": self.prescription.source_format,
            "model_id": self.model_id,
            "optical_models": [dump(m) for m in self.optical_models],
            "model_materials": [dump(m) for m in self.model_materials] or None,  # null like the Project field when none
            "model_coatings": [dump(c) for c in self.model_coatings] or None,
            "component": dump(self.component),
            "report": self.report,
        }
        if self.environment is not None:
            out["environment"] = dump(self.environment)
        return out


def _text(text: str | bytes) -> str:
    if isinstance(text, bytes):
        try:
            return text.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise refuse(_E.TEXT_INVALID, f"the prescription is not UTF-8 text ({exc.reason} at byte {exc.start})") from None
    if not isinstance(text, str):
        raise refuse(_E.TEXT_INVALID, f"the prescription must be text, got {type(text).__name__}")
    return text


def _contract_text(exc: ValidationError) -> str:
    """A contract refusal as a customer reads it: each problem's field path and message."""

    return "; ".join(f"{'.'.join(str(p) for p in e['loc']) or 'the document'}: {str(e['msg']).removeprefix('Value error, ')}"
                     for e in exc.errors(include_url=False))


def _assumptions(rx: Prescription, unit_assumptions: tuple[str, ...], band_basis: str, cemented: int, image_z: Decimal | None) -> list[str]:
    unit = rx.input_unit
    items = [
        "Coaxial and sequential: every surface is centred on the unit's optical axis (+Z, the direction light travels), in the "
        "order of the rows; there are no decentres or tilts.",
        "Radius sign: a positive radius has its centre of curvature after the surface (+Z side), the convention of the native "
        "model; radii are copied unchanged.",
        "Lengths are read in mm." if unit == "mm" else f"Lengths are read in {unit} and converted to mm exactly (1 {unit} = "
        f"{decimal_to_string(MM_PER_UNIT[unit])} mm).",
        "Frame: the unit's origin is the first surface's vertex; each element is placed along +Z at the sum of the spacings before it.",
    ]
    if any(s.is_mirror for s in rx.surfaces):
        items[:2] = ["Light enters along +Z and reverses direction at each mirror.",
                     "Radius and vertex spacing use the same fixed Z axis on both legs of the path."]
    if rx.object is None:
        items.append("The prescription has no object surface.")
    elif rx.object.infinite:
        items.append("The object is at infinity (the object surface is not geometry).")
    elif rx.object.distance_mm is not None:
        items.append(f"The object is {decimal_to_string(rx.object.distance_mm)} mm before the first surface (the object surface is "
                     "not geometry; place a source there).")
    else:
        items.append("The object distance is not stated (the object surface is not geometry).")
    if image_z is not None:
        items.append(f"The image surface is not an element; the image plane is at z = {decimal_to_string(image_z)} mm in the unit frame.")
    elif rx.image_at_infinity:
        items.append("The image is at infinity (the last surface's thickness is inf: an afocal prescription); no image plane is reported.")
    elif rx.image is not None:
        items.append("The image surface is not an element; the last surface states no distance to it.")
    items.append("A surface with a mechanical semi-diameter is edged there and held flat at its rim height beyond it out to the "
                 "outline of its glass. Each uninterrupted run of glass has an outline based on its own surfaces. A surface "
                 "without a mechanical semi-diameter counts with its clear semi-diameter and normally reaches that outline. "
                 "Light between a clear aperture and the outline of its glass is blocked.")
    if cemented:
        items.append("Cemented elements: the glasses meet at the shared surface; the cement layer is not modelled.")
    items.append(f"Wavelength band: {band_basis}.")
    environment = _environment(rx)
    items.append("Wavelengths are vacuum wavelengths in the Project; " + _wavelength_report(rx, environment)["basis"] + ".")
    if environment is None:
        items.append("The file states no environment: the Project's own environment applies (air at 20 C and 101325 Pa when the "
                     "Project states none).")
    else:
        how = ("stated by the file" if rx.environment.stated else  # type: ignore[union-attr]
               "the default reference of the file's air wavelengths (it states no temperature or pressure)")
        items.append(f"Environment: {_environment_text(environment)}, {how} (the air outside the elements and in every "
                     "air gap, and the temperature of the materials); merging the import sets it on the Project.")
    items.append("A surface without a coating is a bare interface (Fresnel transmission of its two media).")
    return items + list(unit_assumptions)


def import_prescription(text: str | bytes, *, format: str = CSV_SURFACE_TABLE, model_id: str = DEFAULT_MODEL_ID,
                        wavelength_band_nm: tuple[Decimal, Decimal] | None = None, units: str | None = None, glass_source: str = "catalog",
                        catalog: PublicOpticalCatalogV2 | CatalogSource | None = None, ignore_columns: tuple[str, ...] = (),
                        edge_review_ratio: float | None = None) -> PrescriptionImport:
    """Import a prescription (module docstring).

    ``format``: one of ``PRESCRIPTION_FORMATS``. ``model_id``: the id of the ``COMPOUND`` unit; its members are
    ``<model_id>-L1``, ``<model_id>-STOP``, ... ``wavelength_band_nm``: the band the models are valid for (overrides a
    band the file states; default: the file's band, else the intersection of the materials' bands). ``units``: the
    length unit the file is read in (default: the unit the file states, else mm); a file that states another unit is
    refused (``UNITS_CONFLICT``), never silently read in either. ``glass_source``: ``catalog`` (glasses not defined in the file are copied from
    the public optical catalog) or ``inline`` (only the file's own materials). ``catalog``: the catalog to use, or a
    function returning it (called only when a glass or coating needs it). ``ignore_columns``: extra columns of the file
    (any table) that are not read, beside those its ``#! ignore_columns`` directive names; an unknown column with values
    that is not named is refused (``COLUMN_UNKNOWN``), one blank in every row is skipped; ``report.ignored_columns`` lists
    both. ``edge_review_ratio``: an element's edge thickness over its clear diameter above which its
    ``report.members[].edge_thickness[]`` row says ``above_review_ratio`` (default 0.35; above 1.5 it is a warning).
    Raises ``PrescriptionImportError``.
    """

    problems = ProblemList()
    if not isinstance(format, str) or format not in READERS:
        problems.add(_E.FORMAT_UNSUPPORTED, f"format {format!r} is not a prescription format the import reads; formats: {', '.join(PRESCRIPTION_FORMATS)}")
    if not isinstance(model_id, str) or not MODEL_ID_PATTERN.match(model_id):
        problems.add(_E.OPTIONS_INVALID, f"model_id {model_id!r} must start with a letter and hold letters, digits, '_' or '-' "
                     "(at most 57 characters)")
    unit = "mm" if units is None else UNIT_SPELLINGS.get(units.strip().lower()) if isinstance(units, str) else None
    if unit is None:
        problems.add(_E.UNITS_INVALID, f"unknown length unit {units!r}; use one of {', '.join(MM_PER_UNIT)}")
    if glass_source not in GLASS_SOURCES:
        problems.add(_E.OPTIONS_INVALID, f"glass_source must be one of {', '.join(GLASS_SOURCES)}, got {glass_source!r}")
    if wavelength_band_nm is not None and not (0 < wavelength_band_nm[0] <= wavelength_band_nm[1]):
        problems.add(_E.OPTIONS_INVALID, "wavelength_band_nm must be positive with min_nm <= max_nm")
    if isinstance(ignore_columns, str) or not all(isinstance(c, str) and c.strip() for c in ignore_columns):
        problems.add(_E.OPTIONS_INVALID, "ignore_columns is a list of column names")
    if edge_review_ratio is not None and (isinstance(edge_review_ratio, bool) or not isinstance(edge_review_ratio, (int, float))
                                          or not 0 < edge_review_ratio < float("inf")):
        problems.add(_E.OPTIONS_INVALID, f"edge_review_ratio must be a positive number, got {edge_review_ratio!r}")
    problems.raise_if_any()
    rx = READERS[format](_text(text), default_unit=unit, requested_unit=None if units is None else unit,  # type: ignore[arg-type]
                         ignore_columns=tuple(ignore_columns))
    return _import_models(rx, model_id=model_id, catalog=catalog, glass_source=glass_source,
                          wavelength_band_nm=wavelength_band_nm, edge_review_ratio=edge_review_ratio)


def _import_models(rx: Prescription, *, model_id: str = DEFAULT_MODEL_ID,
                   catalog: PublicOpticalCatalogV2 | CatalogSource | None = None, glass_source: str = "catalog",
                   wavelength_band_nm: tuple[Decimal, Decimal] | None = None,
                   edge_review_ratio: float | None = None) -> PrescriptionImport:
    problems = ProblemList()
    warnings: list[ImportWarning] = list(rx.warnings)
    band, stated_by = rx.band_nm, "stated in the file"
    if wavelength_band_nm is not None:
        if band is not None and band != wavelength_band_nm:
            warnings.append(ImportWarning("BAND_OVERRIDDEN", f"the band {band[0]}-{band[1]} nm stated in the file is replaced by the "
                                          f"requested {wavelength_band_nm[0]}-{wavelength_band_nm[1]} nm"))
        band, stated_by = wavelength_band_nm, "as requested"
    resolved = resolve_materials(rx, catalog=catalog, glass_source=glass_source, band_nm=band, band_stated_by=stated_by, problems=problems)
    problems.raise_if_any()
    lowered = lower_prescription(rx, resolved, model_id, problems,  # type: ignore[arg-type]
                                 *(() if edge_review_ratio is None else (float(edge_review_ratio),)))
    problems.raise_if_any()
    assert lowered is not None and resolved is not None
    environment = _environment(rx)
    title = rx.name or "Imported prescription"
    counts = lowered.counts
    description = (f"{title}: imported from a {rx.source_format} prescription ({counts['elements']} element"
                   f"{'s' if counts['elements'] != 1 else ''}, {counts['aperture_stops']} aperture stop{'s' if counts['aperture_stops'] != 1 else ''})")
    try:
        component = OpticalComponentInstanceV1.model_validate({
            "instance_id": model_id, "optical_role": IMPORT_ROLE,
            "custom_part": {"custom_part_key": model_id, "description": description[:DESCRIPTION_MAX]},
            "nominal_pose": {"position_mm": [0.0, 0.0, 0.0], "quaternion_wxyz": [1.0, 0.0, 0.0, 0.0]}, "optical_model_id": model_id,
        })
        ProjectDocumentV2.model_validate({
            "schema": "opensci.project", "project_schema_version": "2.0", "components": [component],
            "optical_models": list(lowered.models), "model_materials": list(resolved.materials) or None,
            "model_coatings": list(resolved.coatings) or None, "environment": environment,
        })
    except ValidationError as exc:
        raise refuse(_E.MODEL_INVALID, f"the imported models are refused by the Project contract: {_contract_text(exc)}") from None
    warnings += list(resolved.warnings) + list(lowered.warnings)
    report: dict[str, Any] = {
        "format": rx.source_format,
        "source_sha256": rx.source_sha256,
        "name": rx.name,
        "input_unit": rx.input_unit,
        "model_id": model_id,
        "counts": {**counts, "materials": len(resolved.materials), "materials_from_catalog": len(resolved.from_catalog),
                   "materials_defined_in_file": len(resolved.materials) - len(resolved.from_catalog), "coatings": len(resolved.coatings)},
        "wavelength_band_nm": None if resolved.band is None else {"min_nm": resolved.band.min_nm, "max_nm": resolved.band.max_nm,
                                                                  "basis": resolved.band_basis},
        "catalog_version": resolved.catalog_version,
        "glasses": list(resolved.glasses),
        "wavelengths": _wavelength_report(rx, environment),
        "environment": None if environment is None else {**environment.model_dump(mode="json"), "stated": list(rx.environment.stated)},  # type: ignore[union-attr]
        "object": None if rx.object is None else {
            "at_infinity": rx.object.infinite,
            "distance_mm": decimal_to_string(rx.object.distance_mm) if rx.object.distance_mm is not None else None},
        "image_plane_z_mm": decimal_to_string(lowered.image_plane_z_mm) if lowered.image_plane_z_mm is not None else None,
        "members": list(lowered.members),
        "assumptions": _assumptions(rx, lowered.assumptions, resolved.band_basis, counts["cemented_elements"], lowered.image_plane_z_mm),
        "warnings": [w.as_dict() for w in warnings],
    }
    if rx.ignored_columns:  # (stated only when a column was skipped: other reports keep their form)
        report["ignored_columns"] = [{"section": section, "column": column, "reason": reason}
                                     for section, column, reason in rx.ignored_columns]
    return PrescriptionImport(prescription=rx, optical_models=lowered.models, model_materials=resolved.materials,
                              model_coatings=resolved.coatings, component=component, report=report, environment=environment)


def _environment(rx: Prescription) -> PropagationEnvironmentV1 | None:
    """The Project environment of the environment the prescription states (``None``: it states none)."""

    if rx.environment is None:
        return None
    return PropagationEnvironmentV1(temperature_c=decimal_to_string(rx.environment.temperature_c),
                                    pressure_pa=decimal_to_string(rx.environment.pressure_pa))


def _wavelength_report(rx: Prescription, environment: PropagationEnvironmentV1 | None) -> dict[str, Any]:
    if rx.wavelengths_in == WAVELENGTHS_AIR:
        assert environment is not None
        converted = not environment.is_vacuum
        return {"written_in": "AIR", "converted_to_vacuum": converted,
                "basis": (f"the file writes air wavelengths of air at {environment.temperature_c} C and {environment.pressure_pa} Pa "
                          "(wavelengths_in = AIR); each was converted once to the vacuum wavelength with the shared air model "
                          f"({environment.air_model})" if converted else
                          "the file writes air wavelengths at zero pressure: vacuum wavelengths, unchanged")}
    return {"written_in": "VACUUM", "converted_to_vacuum": False,
            "basis": "the file writes vacuum wavelengths (wavelengths_in = VACUUM, the format's convention when not stated)"}


def _environment_text(environment: PropagationEnvironmentV1) -> str:
    if environment.is_vacuum:
        return f"vacuum (zero pressure) at {environment.temperature_c} C"
    return f"air at {environment.temperature_c} C and {environment.pressure_pa} Pa"


def _same_environment(a: PropagationEnvironmentV1, b: PropagationEnvironmentV1) -> bool:
    return (Decimal(a.temperature_c) == Decimal(b.temperature_c) and Decimal(a.pressure_pa) == Decimal(b.pressure_pa)
            and a.air_model == b.air_model)


def _payload(item) -> dict[str, Any]:
    return item.model_dump(mode="json", by_alias=True, exclude_none=True)


def merge_into_project(project: Any, imported: PrescriptionImport, *, instance_id: str | None = None,
                       pose: PoseV1 | dict | None = None) -> ProjectDocumentV2:
    """``project`` with the imported models, materials and coatings added and the suggested component placed as
    ``instance_id`` (default: the model id) at ``pose`` (default: the identity pose). A material the Project already
    declares with the same physics (``catalog_copy.physics_difference``: dispersion, valid band, absorption; its name and
    basis are provenance) and a coating it declares with identical content are shared (the Project's entry is kept); any
    other id conflict is refused (``PROJECT_INVALID``).

    Environment: an import whose prescription states its environment (``PrescriptionImport.environment``) sets it on the
    Project. A Project that states another environment is refused (``PROJECT_INVALID``), and so is a Project that states
    none but already holds optics (they operate in the default air at 20 C / 101325 Pa) when the import's differs from
    that default: an import never changes the medium of optics already in the Project. An import that states no
    environment leaves the Project's as it is. (Optics: a component with a catalog product or a declared optical model;
    sources and observation planes are not.)"""

    try:
        base = project if isinstance(project, ProjectDocumentV2) else parse_project_document(project)
    except ValidationError as exc:
        raise refuse(_E.PROJECT_INVALID, f"the Project to import into is not valid: {_contract_text(exc)}") from None
    problems = ProblemList()
    document = base.payload()
    existing_models = {m.model_id for m in base.optical_models or ()}
    for model in imported.optical_models:
        if model.model_id in existing_models:
            problems.add(_E.PROJECT_INVALID, f"the Project already declares optical model {model.model_id!r}; import with another model_id")
    merged: dict[str, list[dict[str, Any]]] = {}
    for key, ident, ours in (("model_materials", "material_id", imported.model_materials), ("model_coatings", "coating_id", imported.model_coatings)):
        current = [_payload(item) for item in getattr(base, key) or ()]
        by_id = {item[ident]: item for item in current}
        for item in ours:
            data = _payload(item)
            known = by_id.get(data[ident])
            if known is None:
                current.append(data)
                by_id[data[ident]] = data
            elif key == "model_materials":
                conflict = material_conflict(known, data)  # the same glass copied by another route or release is shared
                if conflict is not None:
                    problems.add(_E.PROJECT_INVALID, conflict)
            elif known != data:
                problems.add(_E.PROJECT_INVALID, f"the Project already declares {ident} {data[ident]!r} with different data; rename one of them")
        merged[key] = current
    component = _payload(imported.component)
    if instance_id is not None:
        component["instance_id"] = instance_id
    if pose is not None:
        component["nominal_pose"] = _payload(pose) if isinstance(pose, PoseV1) else pose
    if any(c.instance_id == component["instance_id"] for c in base.components):
        problems.add(_E.PROJECT_INVALID, f"the Project already has a component {component['instance_id']!r}; give another instance id")
    wanted = imported.environment
    if wanted is not None:
        if base.environment is not None and not _same_environment(base.environment, wanted):
            problems.add(_E.PROJECT_INVALID, f"the prescription states its environment as {_environment_text(wanted)}, and the Project "
                         f"states {_environment_text(base.environment)}; the prescription's wavelengths and materials are stated for its "
                         "own environment. Import into a Project of the same environment, or change the Project's environment "
                         "first (or the prescription's temperature_c / pressure directives)")
        elif (base.environment is None and any(c.product is not None or c.optical_model_id is not None for c in base.components)
              and not _same_environment(DEFAULT_ENVIRONMENT, wanted)):
            problems.add(_E.PROJECT_INVALID, f"the prescription states its environment as {_environment_text(wanted)}, and the Project "
                         f"states none: its optics operate in the default {_environment_text(DEFAULT_ENVIRONMENT)}. Set the Project's "
                         "environment first if its optics operate in the prescription's, or import into another Project")
    problems.raise_if_any()
    if wanted is not None:
        document["environment"] = _payload(wanted)
    document["optical_models"] = [_payload(m) for m in base.optical_models or ()] + [_payload(m) for m in imported.optical_models]
    for key, items in merged.items():
        if items:
            document[key] = items
    document["components"] = [*document.get("components", []), component]
    try:
        return ProjectDocumentV2.model_validate(document)
    except ValidationError as exc:
        raise refuse(_E.PROJECT_INVALID, f"the Project with the import is not valid: {_contract_text(exc)}") from None


from .project import read_prescription_project

__all__ = [
    "read_prescription_project",
    "CSV_SURFACE_TABLE",
    "DEFAULT_MODEL_ID",
    "PRESCRIPTION_FORMATS",
    "PRESCRIPTION_IMPORT_INVALID",
    "ImportWarning",
    "Prescription",
    "PrescriptionErrorCode",
    "PrescriptionImport",
    "PrescriptionImportError",
    "PrescriptionProblem",
    "SourceLocation",
    "import_prescription",
    "merge_into_project",
]
