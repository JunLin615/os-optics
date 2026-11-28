"""Open a sequential prescription as an editable, standalone optical Project."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from pydantic import ValidationError

from .._vendor.shared_contracts.design_catalog import PublicOpticalCatalogV2
from .._vendor.shared_contracts.optical_design import ProjectDocumentV2
from . import PrescriptionImport, _contract_text, _import_models
from .errors import PrescriptionErrorCode as E, PrescriptionImportError, SourceLocation, refuse
from .glass import CatalogSource, _Catalog
from .illumination import sources
from .zmx import read_zmx
from .zmx_config import configuration_states

_READERS = {".zmx": read_zmx}


def _text(path: Path) -> str:
    data = path.read_bytes()
    encoding = "utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    try:
        return data.decode(encoding)
    except UnicodeDecodeError as exc:
        raise refuse(E.TEXT_INVALID, f"Cannot read {path.name} at byte {exc.start}. Save a text prescription as UTF-8 or UTF-16 with a byte-order mark.") from None


def _standalone(imported: PrescriptionImport) -> ProjectDocumentV2:
    rx = imported.prescription
    for glass in imported.report["glasses"]:
        if rx.glass_catalogs and glass.get("outside_catalogue_order") is not False:
            surface = next(s for s in rx.surfaces if s.material and s.material.key == glass["written"])
            raise refuse(E.GLASS_UNKNOWN, f"Cannot verify glass {glass['written']!r} in the specified catalogs "
                         f"({' '.join(rx.glass_catalogs)}). Supply the matching catalog record with its identity.",
                         surface.material.location)
    unit, *models = imported.optical_models
    components = []
    details = {member["member_id"]: member for member in imported.report["members"]}
    for member in unit.model.members:
        detail = details[member.member_id]
        kind = detail["model_kind"]
        surfaces = [s for s in rx.surfaces if s.label in detail["surfaces"]]
        role = "aperture" if kind == "APERTURE_STOP" else "lens"
        if any(s.is_mirror for s in surfaces):
            role = "mirror"
        labels = ", ".join(detail["surfaces"])
        description = f"{rx.source_format} surfaces {labels}."
        for surface in surfaces:
            if surface.is_stop:
                description += f" Aperture stop at surface {surface.label}."
        solves = sorted({w.location.column for w in rx.warnings if w.code == "FROZEN_SURFACE_SOLVE" and w.location
                         and w.location.surface in detail["surfaces"]})
        if solves:
            description += f" {', '.join(solves)} imported at the saved numeric values."
        components.append({"instance_id": member.member_id, "optical_role": role,
                           "custom_part": {"custom_part_key": member.model_id, "description": description},
                           "optical_model_id": member.model_id, "nominal_pose": member.pose})
    observations = []
    image_z = imported.report["image_plane_z_mm"]
    if image_z is not None:
        mirrors = [surface for surface in rx.surfaces if surface.is_mirror]
        reversed_axis = len(mirrors) % 2
        if len(mirrors) == 2 and Decimal(image_z) <= Decimal(details["M1"]["z_mm"]):
            raise refuse(E.UNSUPPORTED_FEATURE, "The image lies on an earlier reflection leg. A two-sided native observation "
                         "would record extra arrivals. Export a final image beyond the first mirror.", rx.image)
        physical = {label for member in details.values() for label in member["surfaces"]}
        distance = 0
        for surface in reversed(rx.surfaces):
            distance += surface.thickness_mm
            if surface.label in physical:
                break
        if distance < 0 and not reversed_axis or distance > 0 and reversed_axis:
            raise refuse(E.UNSUPPORTED_FEATURE, f"The virtual image at z={image_z} mm requires backwards extrapolation. "
                         "Export a real image plane to record native ray arrivals.", rx.image)
        image = {"observation_id": "image", "pose": {"position_mm": [0, 0, float(image_z)],
                 "quaternion_wxyz": [0, 1, 0, 0] if reversed_axis else [1, 0, 0, 0]}}
        if rx.image_semi_diameter_mm:
            image["aperture"] = {"shape": "circle", "radius_mm": float(rx.image_semi_diameter_mm)}
        observations.append(image)
    return ProjectDocumentV2.model_validate({
        "schema": "opensci.project", "project_schema_version": "2.0", "design_intent": "OPTICAL_SYSTEM",
        "components": [*sources(imported), *components], "optical_models": models, "model_materials": imported.model_materials or None,
        "model_coatings": imported.model_coatings or None, "environment": imported.environment, "observations": observations,
    })


def _configuration(base: ProjectDocumentV2, state: ProjectDocumentV2, imported: PrescriptionImport, number: int) -> dict:
    # The reader accepts changes to spacings, fields and system aperture. A spacing inside a
    # glass element would change a surface model, beyond the native pose override.
    rx = imported.prescription
    originals = {model.model_id: model.model for model in base.optical_models}
    details = {member["model_id"]: member for member in imported.report["members"]}
    if originals.keys() != details.keys():
        raise refuse(E.UNSUPPORTED_FEATURE, "The number or grouping of elements changes. Export this state separately.",
                     SourceLocation(configuration=number))
    for current in state.optical_models:
        if originals[current.model_id] != current.model:
            member = details[current.model_id]
            surface = member["surfaces"][0]
            where = next(s.location for s in rx.surfaces if s.label == surface)
            raise refuse(E.UNSUPPORTED_FEATURE, f"The element at surfaces {', '.join(member['surfaces'])} changes shape or aperture. "
                         "Export this state as a separate prescription, or use a spacing between optical elements.",
                         replace(where, configuration=number))
    component_poses, source_models = [], []
    for original, current in zip(base.components, state.components, strict=True):
        if original.nominal_pose != current.nominal_pose:
            component_poses.append({"instance_id": current.instance_id, "nominal_pose": current.nominal_pose})
        if original.source_model != current.source_model:
            source_models.append({"instance_id": current.instance_id, "source_model": current.source_model})
    if len(base.observations) != len(state.observations):
        raise refuse(E.UNSUPPORTED_FEATURE, "The state changes between a finite and an infinite image. Export it separately.",
                     replace(rx.image, configuration=number))
    observation_poses = []
    for original, current in zip(base.observations, state.observations):
        if original.aperture != current.aperture:
            raise refuse(E.UNSUPPORTED_FEATURE, "The physical image aperture changes between states. Export this state separately.",
                         replace(rx.image, configuration=number))
        if original.pose != current.pose:
            observation_poses.append({"observation_id": current.observation_id, "pose": current.pose})
    label = f"Configuration {number}"
    if rx.entrance_pupil_diameter_mm is not None:
        label += f", entrance pupil {rx.entrance_pupil_diameter_mm:g} mm"
    elif rx.image_f_number is not None:
        label += f", f/{rx.image_f_number:g}"
    elif rx.object_na is not None:
        label += f", object NA {rx.object_na:g}"
    return {"configuration_id": f"config-{number}", "label": label,
            "component_poses": component_poses, "source_models": source_models, "observation_poses": observation_poses}


def read_prescription_project(path: str | Path, *, catalog: PublicOpticalCatalogV2 | CatalogSource | None = None) -> ProjectDocumentV2:
    """Read a supported file, resolving named glasses once. Save the returned Project with ``project.payload()``.

    ``catalog`` may be a public catalog or a callable that loads one. By default the active local catalog is used
    when a glass needs it. ``PrescriptionImportError.problems`` locates unsupported features in the input.
    """
    path = Path(path)
    extension = path.suffix.lower()
    reader = _READERS.get(extension)
    if reader is None:
        if extension == ".zda":
            message = f"Open the matching {path.with_suffix('.zmx').name} prescription beside this session file."
        elif extension == ".zar":
            message = "Restore the archive and open its text .zmx prescription with its referenced material catalog."
        else:
            message = f"Export a sequential text prescription as {', '.join(_READERS)} to open {path.name}."
        raise refuse(E.FORMAT_UNSUPPORTED, message)
    if catalog is None:
        from ..local.catalog import load_public_catalog
        catalog = load_public_catalog
    # All configurations of this file resolve against the same lazily loaded release and name index.
    materials = _Catalog(catalog, "catalog")
    text = _text(path)
    active, texts = configuration_states(text) if extension == ".zmx" else (1, [text])
    states = []
    for number, state_text in enumerate(texts, 1):
        try:
            rx = reader(state_text)
            imported = _import_models(rx, catalog=materials)
            states.append((_standalone(imported), imported))
        except PrescriptionImportError as exc:
            if len(texts) == 1:
                raise
            raise PrescriptionImportError([replace(p, location=replace(p.location or SourceLocation(), configuration=number))
                                           for p in exc.problems]) from None
        except ValidationError as exc:
            raise refuse(E.PROJECT_INVALID, f"Cannot construct the optical Project. {_contract_text(exc)}",
                         SourceLocation(configuration=number) if len(texts) > 1 else None) from None
    base, _ = states[active - 1]
    if len(states) == 1:
        return base
    configurations = [_configuration(base, state, imported, n) for n, (state, imported) in enumerate(states, 1)]
    return ProjectDocumentV2.model_validate({**base.payload(), "configurations": configurations})
