"""Canonical bytes, building, and verification of an Assembly Detail Package against its bound preview package.

``verify_assembly_detail(detail_bytes, preview)`` is what a client (or the server before sending) runs: canonical
file bytes, schema, content hash, the binding field by field against the verified preview manifest, and every
dimension against the bound scene: points resolve, the kind's shape rules and allowed bases (``ALLOWED_BASES``) hold,
the value is measured from the preview's own poses and is finite (signed: to - from), a shown setting equals its
measured value, the axis height equals the scene's, and every INSERTION_DEPTH setting is shown by exactly one
dimension. It returns every problem found and never raises on malformed input. ``load_assembly_detail`` raises
``AssemblyDetailPackageError`` instead and returns the verified pair with the measured values.

A preview package with the LOCAL_PLAN binding (preview manifest 3.1) takes a 3.2 supplement: ``build_assembly_detail``
copies its local plan revision into the binding (release ``LOCAL_PLAN``), and the verifier compares it field by field
with the manifest as it compares the commercial resolution id and the Customer Purchase BOM of a 3.1 supplement.

Measurement uses the preview's poses only (``x_world = R(q) x_local + position_mm``, the quaternion normalized as
the renderer does); there is no second pose authority.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from pydantic import ValidationError

from ..assembly_preview.package import AssemblyPreviewPackageV1, json_file_bytes
from ..assembly_preview.scene import (
    OPTICAL_CENTRE_DATUM,
    AssemblyPreviewSceneV1,
    PartCategoryV1,
    SettingBasisV1,
    SettingKindV1,
    SupportRelationV1,
)
from .detail import (
    AXIS_INDEX,
    MAX_DETAIL_BYTES,
    AssemblyDetailPackageV1,
    CustomerSuppliedModelV1,
    DetailBindingV1,
    DetailReleaseRefV1,
    DimensionBasisV1,
    DimensionKindV1,
    DimensionV1,
    MeasureAxisV1,
    PointKindV1,
    PointRefV1,
    ReleaseBasisV1,
    detail_content_hash_of,
    detail_schema_version_for,
    detail_usage_terms_for,
)

#: Agreement required between a measured dimension and the scene value it must reproduce (mm).
MEASURE_TOLERANCE_MM = 1e-6

Vec3 = tuple[float, float, float]

_PLAN_AXES = frozenset({MeasureAxisV1.WORLD_X, MeasureAxisV1.WORLD_Y})


class AssemblyDetailPackageError(ValueError):
    """Raised when detail bytes do not form a valid supplement of the given preview package; ``problems`` lists why."""

    def __init__(self, problems: tuple[str, ...]):
        super().__init__("invalid assembly detail package: " + "; ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class MeasuredDimensionV1:
    dim_ref: str
    value_mm: float
    from_world_mm: Vec3
    to_world_mm: Vec3


@dataclass(frozen=True)
class VerifiedAssemblyDetailV1:
    """A detail package verified against one verified preview package, with every dimension measured."""

    detail: AssemblyDetailPackageV1
    preview: AssemblyPreviewPackageV1
    measured: Mapping[str, MeasuredDimensionV1]
    detail_bytes: bytes

    @property
    def content_hash(self) -> str:
        return self.detail.detail_content_hash


# ------------------------------------------------------------------------------------------------ bytes


def detail_file_bytes(detail: AssemblyDetailPackageV1) -> bytes:
    """The canonical file bytes (sorted keys, 2-space indent, LF, trailing newline); the protected-delivery plaintext."""

    return json_file_bytes(detail.payload())


def build_assembly_detail(
    *,
    preview: AssemblyPreviewPackageV1,
    release: DetailReleaseRefV1,
    dimensions: tuple[DimensionV1, ...],
    customer_supplied_models: tuple[CustomerSuppliedModelV1, ...] = (),
    line_display_names_zh_cn: Mapping[str, str] | None = None,
) -> AssemblyDetailPackageV1:
    """Bind ``dimensions`` (and the catalog models of customer-supplied components) to ``preview`` (binding copied from
    the verified preview manifest, never from a caller), hash, and verify the result against the same preview. A
    LOCAL_PLAN preview takes a 3.2 supplement whose ``release`` basis is ``LOCAL_PLAN``. Raises
    ``AssemblyDetailPackageError``."""

    manifest = preview.manifest
    source = manifest.binding
    if source.local:
        if release.basis is not ReleaseBasisV1.LOCAL_PLAN:
            raise AssemblyDetailPackageError(("the supplement of a locally generated preview package is released LOCAL_PLAN",))
        binding = DetailBindingV1(project=source.project, local_validation_hash=source.local_validation_hash,
                                  local_plan=source.local_plan, preview_package_content_hash=manifest.package_content_hash,
                                  preview_package_revision=manifest.package_revision)
    else:
        if source.commercial_resolution_id is None or source.commercial_bom is None:
            raise AssemblyDetailPackageError(("only a preview package bound to a commercial resolution and its "
                                              "Customer Purchase BOM, or to a local assembly plan, can be supplemented",))
        binding = DetailBindingV1(project=source.project, local_validation_hash=source.local_validation_hash,
                                  commercial_resolution_id=source.commercial_resolution_id,
                                  commercial_bom=source.commercial_bom,
                                  preview_package_content_hash=manifest.package_content_hash,
                                  preview_package_revision=manifest.package_revision)
    content = {
        "schema": "opensci.assembly_detail.package",
        "schema_version": detail_schema_version_for(binding),
        "usage": detail_usage_terms_for(binding).model_dump(mode="json", by_alias=True),
        "binding": binding.model_dump(mode="json", by_alias=True, exclude_none=True),
        "release": release.model_dump(mode="json", by_alias=True, exclude_none=True),
        "dimensions": [d.model_dump(mode="json", by_alias=True, exclude_none=True) for d in dimensions],
        "customer_supplied_models": [m.model_dump(mode="json", by_alias=True, exclude_none=True)
                                     for m in sorted(customer_supplied_models, key=lambda m: m.instance_id)],
        "line_display_names_zh_cn": dict(sorted((line_display_names_zh_cn or {}).items())),
    }
    try:
        detail = AssemblyDetailPackageV1.model_validate({**content, "detail_content_hash": detail_content_hash_of(content)})
    except ValidationError as exc:
        raise AssemblyDetailPackageError((f"detail document is invalid: {_first_error(exc)}",)) from None
    load_assembly_detail(detail_file_bytes(detail), preview)
    return detail


# ------------------------------------------------------------------------------------------ geometry


def _rotate(q: tuple[float, float, float, float], v: Vec3) -> Vec3:
    norm = math.sqrt(q[0] * q[0] + q[1] * q[1] + q[2] * q[2] + q[3] * q[3])
    w, x, y, z = (c / norm for c in q)
    # v' = v + 2 w (u x v) + 2 u x (u x v), u = (x, y, z)
    cx, cy, cz = y * v[2] - z * v[1], z * v[0] - x * v[2], x * v[1] - y * v[0]
    dx, dy, dz = y * cz - z * cy, z * cx - x * cz, x * cy - y * cx
    return (v[0] + 2.0 * (w * cx + dx), v[1] + 2.0 * (w * cy + dy), v[2] + 2.0 * (w * cz + dz))


def _place(position: Vec3, quaternion: tuple[float, float, float, float], local: Vec3) -> Vec3:
    r = _rotate(quaternion, local)
    return (r[0] + position[0], r[1] + position[1], r[2] + position[2])


def point_in_world(point: PointRefV1, scene: AssemblyPreviewSceneV1) -> Vec3:
    """World position of a point reference; raises ``KeyError``/``ValueError`` naming what does not resolve."""

    board = scene.breadboard
    if point.kind is PointKindV1.DESIGN_ANCHOR:
        for anchor in scene.beam_reference.design_anchors:
            if anchor.instance_id == point.instance_id:
                return tuple(anchor.position_mm)  # type: ignore[return-value]
        raise KeyError(f"design anchor {point.instance_id!r} is not in the scene")
    part = next((p for p in scene.parts if p.part_ref == point.part_ref), None)
    if part is None:
        raise KeyError(f"part {point.part_ref!r} is not in the scene")
    pose = part.pose_in_world
    if point.kind is PointKindV1.PART_ORIGIN:
        return tuple(pose.position_mm)  # type: ignore[return-value]
    if point.kind is PointKindV1.PART_DATUM:
        datum = next((d for d in part.datums if d.datum_key == point.datum_key), None)
        if datum is None:
            raise KeyError(f"part {point.part_ref!r} has no datum {point.datum_key!r}")
        return _place(pose.position_mm, pose.quaternion_wxyz, datum.pose_in_part.position_mm)
    if board.part_ref != point.part_ref:
        raise ValueError(f"a {point.kind.value} point must name the breadboard {board.part_ref!r}")
    if point.kind is PointKindV1.BOARD_CORNER:
        return (board.outline_min_xy_mm[0], board.outline_min_xy_mm[1], board.top_z_mm)
    grid = board.hole_grid
    assert point.hole is not None
    if grid is None or not (point.hole[0] < grid.count[0] and point.hole[1] < grid.count[1]):
        raise ValueError(f"hole {tuple(point.hole)} is not on the breadboard grid")
    return (grid.origin_xy_mm[0] + point.hole[0] * grid.pitch_mm[0],
            grid.origin_xy_mm[1] + point.hole[1] * grid.pitch_mm[1], board.top_z_mm)


def measure(dimension: DimensionV1, scene: AssemblyPreviewSceneV1) -> MeasuredDimensionV1:
    a = point_in_world(dimension.from_point, scene)
    b = point_in_world(dimension.to_point, scene)
    axis = AXIS_INDEX[dimension.axis]
    return MeasuredDimensionV1(dim_ref=dimension.dim_ref, value_mm=b[axis] - a[axis], from_world_mm=a, to_world_mm=b)


# ------------------------------------------------------------------------------------------ rules


_NOMINAL = DimensionBasisV1.NOMINAL

#: The bases each kind may carry. A placement dimension is RESOLVED or, when it follows from a nominal set-up value,
#: NOMINAL; a fixation offset is a REFERENCE dimension (or NOMINAL); a stand-off to the customer's own source is
#: CUSTOMER_DESIGN; an insertion depth has exactly its setting's basis (``None`` here: checked against the setting).
ALLOWED_BASES: dict[DimensionKindV1, frozenset[DimensionBasisV1] | None] = {
    DimensionKindV1.OPTICAL_AXIS_HEIGHT: frozenset({DimensionBasisV1.RESOLVED, _NOMINAL}),
    DimensionKindV1.INSERTION_DEPTH: None,
    DimensionKindV1.OPTIC_BOARD_POSITION: frozenset({DimensionBasisV1.RESOLVED, _NOMINAL}),
    DimensionKindV1.FIXATION_HOLE_POSITION: frozenset({DimensionBasisV1.RESOLVED, _NOMINAL}),
    DimensionKindV1.FIXATION_OFFSET: frozenset({DimensionBasisV1.REFERENCE, _NOMINAL}),
    DimensionKindV1.EXTERNAL_SOURCE_STANDOFF: frozenset({DimensionBasisV1.CUSTOMER_DESIGN}),
}


def _setting_basis(basis: SettingBasisV1) -> DimensionBasisV1:
    return DimensionBasisV1(basis.value)


def _fixation_holes(scene: AssemblyPreviewSceneV1, part_ref: str) -> set[tuple[int, int]]:
    return {tuple(h) for f in scene.fixations if f.part_ref == part_ref for h in f.board_holes}  # type: ignore[misc]


def _is_optic(scene: AssemblyPreviewSceneV1, point: PointRefV1, subject: str, *, centre: bool = False) -> bool:
    """``point`` is the origin of the optic ``subject`` (``centre``: or its optical-centre datum)."""

    at_centre = centre and point.kind is PointKindV1.PART_DATUM and point.datum_key == OPTICAL_CENTRE_DATUM
    if (point.kind is not PointKindV1.PART_ORIGIN and not at_centre) or point.part_ref != subject:
        return False
    return scene.part(subject).category is PartCategoryV1.OPTICAL_ELEMENT


def _optic_axis_height(scene: AssemblyPreviewSceneV1, optic_ref: str) -> float | None:
    """The axis height of one optic above the board top: its design anchor's height. When the optics of a scene share one
    height, the scene-wide ``optical_axis_height_mm`` must equal it too (both are checked)."""

    anchor = next((a for a in scene.beam_reference.design_anchors if a.realized_by_part_ref == optic_ref), None)
    if anchor is None:
        return scene.beam_reference.optical_axis_height_mm
    return round(anchor.position_mm[2] - scene.breadboard.top_z_mm, 6) + 0.0


def _rule_problems(dim: DimensionV1, scene: AssemblyPreviewSceneV1, value: float) -> list[str]:  # noqa: C901 - one flat rule table
    """The shape each dimension kind must have in the bound scene (``DIMENSION_RULES``)."""

    kind, frm, to, subject = dim.kind, dim.from_point, dim.to_point, dim.subject_part_ref
    board_ref = scene.breadboard.part_ref
    out: list[str] = []
    allowed = ALLOWED_BASES[kind]
    if allowed is not None and dim.basis not in allowed:
        out.append(f"a {kind.value} dimension rests on one of {sorted(b.value for b in allowed)}, not {dim.basis.value}")
    if dim.setting is not None and kind is not DimensionKindV1.INSERTION_DEPTH:
        out.append("only an INSERTION_DEPTH shows a setting")

    if kind is DimensionKindV1.OPTICAL_AXIS_HEIGHT:
        board_top = frm.kind is PointKindV1.BOARD_CORNER or (
            frm.kind is PointKindV1.PART_DATUM and frm.part_ref == board_ref)
        if dim.axis is not MeasureAxisV1.WORLD_Z or not board_top or not _is_optic(scene, to, subject):
            out.append("OPTICAL_AXIS_HEIGHT runs along WORLD_Z from the board top to the optic's origin")
        scene_height = scene.beam_reference.optical_axis_height_mm
        if scene_height is not None and abs(value - scene_height) > MEASURE_TOLERANCE_MM:
            out.append(f"measured axis height {value!r} mm is not the scene's optical_axis_height_mm {scene_height!r}")
        height = _optic_axis_height(scene, subject)
        if height is None or abs(value - height) > MEASURE_TOLERANCE_MM:
            out.append(f"measured axis height {value!r} mm is not the optic's axis height {height!r}")
    elif kind is DimensionKindV1.INSERTION_DEPTH:
        part = scene.part(subject)
        parent = part.support.parent_ref if part.support else None
        if (dim.axis is not MeasureAxisV1.WORLD_Z or part.support is None
                or part.support.relation is not SupportRelationV1.INSERTED_INTO
                or frm.kind is not PointKindV1.PART_DATUM or frm.part_ref != subject
                or to.kind is not PointKindV1.PART_DATUM or to.part_ref != parent):
            out.append("INSERTION_DEPTH runs along WORLD_Z from a datum of the inserted part to a datum of the part it is "
                       "INSERTED_INTO")
        setting = next((s for s in scene.settings if dim.setting is not None and s.part_ref == dim.setting.part_ref
                        and s.kind is dim.setting.kind), None)
        if dim.setting is None or dim.setting.kind.value != "INSERTION_DEPTH" or dim.setting.part_ref != subject:
            out.append("INSERTION_DEPTH must show the subject part's INSERTION_DEPTH setting")
        elif setting is None:
            out.append(f"setting {dim.setting.kind.value} of {dim.setting.part_ref!r} is not in the scene")
        else:
            try:
                shown = float(setting.value)
            except ValueError:
                shown = math.nan
            if setting.unit != "mm" or not abs(value - shown) <= MEASURE_TOLERANCE_MM:
                out.append(f"measured insertion depth {value!r} mm is not the setting value {setting.value!r}")
            if dim.basis is not _setting_basis(setting.basis):
                out.append(f"basis {dim.basis.value} is not the setting's basis {setting.basis.value}")
    elif kind is DimensionKindV1.OPTIC_BOARD_POSITION:
        if (dim.axis not in _PLAN_AXES or frm.kind is not PointKindV1.BOARD_CORNER
                or not _is_optic(scene, to, subject, centre=True)):
            out.append("OPTIC_BOARD_POSITION runs along WORLD_X/Y from the board corner to the optic's origin or optical centre")
    elif kind is DimensionKindV1.FIXATION_HOLE_POSITION:
        if (dim.axis not in _PLAN_AXES or frm.kind is not PointKindV1.BOARD_CORNER
                or to.kind is not PointKindV1.BOARD_HOLE or tuple(to.hole or ()) not in _fixation_holes(scene, subject)):
            out.append("FIXATION_HOLE_POSITION runs along WORLD_X/Y from the board corner to a fixation hole of the subject part")
    elif kind is DimensionKindV1.FIXATION_OFFSET:
        ends = {frm.kind, to.kind}
        hole = frm if frm.kind is PointKindV1.BOARD_HOLE else to
        origin = to if hole is frm else frm
        if (dim.axis not in _PLAN_AXES or ends != {PointKindV1.BOARD_HOLE, PointKindV1.PART_ORIGIN}
                or origin.part_ref != subject or tuple(hole.hole or ()) not in _fixation_holes(scene, subject)):
            out.append("FIXATION_OFFSET runs along WORLD_X/Y between the subject part's origin and one of its fixation holes")
    elif kind is DimensionKindV1.EXTERNAL_SOURCE_STANDOFF:
        anchor = next((a for a in scene.beam_reference.design_anchors if a.instance_id == frm.instance_id), None)
        if (frm.kind is not PointKindV1.DESIGN_ANCHOR or anchor is None or not anchor.customer_supplied
                or not _is_optic(scene, to, subject, centre=True)):
            out.append("EXTERNAL_SOURCE_STANDOFF runs from a customer-supplied design anchor to the optic's origin or optical centre")
    return out


# ------------------------------------------------------------------------------------------ verify


def _first_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        errors = exc.errors(include_input=False)
        if errors:
            first = errors[0]
            where = ".".join(str(p) for p in first.get("loc", ()))
            return f"{where}: {first.get('msg', 'invalid')}" if where else str(first.get("msg", "invalid"))
    return type(exc).__name__


def _binding_problems(binding: DetailBindingV1, preview: AssemblyPreviewPackageV1) -> list[str]:
    source = preview.manifest.binding
    problems = []
    for label, got, want in (
        ("Project", binding.project, source.project),
        ("Local Validation hash", binding.local_validation_hash, source.local_validation_hash),
        ("commercial resolution id", binding.commercial_resolution_id, source.commercial_resolution_id),
        ("Customer Purchase BOM", binding.commercial_bom, source.commercial_bom),
        ("local plan", binding.local_plan, source.local_plan),
        ("preview package content hash", binding.preview_package_content_hash, preview.manifest.package_content_hash),
        ("preview package revision", binding.preview_package_revision, preview.manifest.package_revision),
    ):
        if got != want:
            problems.append(f"binding: {label} is not the bound preview package's")
    return problems


def verify_assembly_detail(
    detail_bytes: bytes,
    preview: AssemblyPreviewPackageV1,
    *,
    expected_content_hash: str | None = None,
) -> tuple[str, ...]:
    """Every problem of ``detail_bytes`` as the supplement of the verified ``preview`` (empty = valid). Never raises."""

    if not isinstance(detail_bytes, (bytes, bytearray)):
        return ("detail package must be bytes",)
    if len(detail_bytes) > MAX_DETAIL_BYTES:
        return (f"detail package exceeds {MAX_DETAIL_BYTES} bytes",)
    if not isinstance(preview, AssemblyPreviewPackageV1):
        return ("the preview package must be a verified AssemblyPreviewPackageV1",)
    try:
        json.loads(bytes(detail_bytes), object_pairs_hook=_no_duplicate_keys)
        detail = AssemblyDetailPackageV1.model_validate_json(bytes(detail_bytes))
    except (ValidationError, ValueError, RecursionError) as exc:  # deeply nested JSON raises RecursionError
        return (f"not a valid assembly detail package: {_first_error(exc)}",)
    problems: list[str] = []
    if bytes(detail_bytes) != detail_file_bytes(detail):
        problems.append("assembly detail package is not in canonical file form")
    if expected_content_hash is not None and detail.detail_content_hash != expected_content_hash:
        problems.append(f"detail content hash {detail.detail_content_hash} is not the expected {expected_content_hash}")
    problems.extend(_binding_problems(detail.binding, preview))
    scene = preview.scene
    for dim in detail.dimensions:
        try:
            scene.part(dim.subject_part_ref)
            value = measure(dim, scene).value_mm
        except (KeyError, ValueError) as exc:
            problems.append(f"{dim.dim_ref}: {exc.args[0] if exc.args else type(exc).__name__}")
            continue
        if not math.isfinite(value):
            problems.append(f"{dim.dim_ref}: measured value is not finite")
            continue
        problems.extend(f"{dim.dim_ref}: {p}" for p in _rule_problems(dim, scene, value))
    problems.extend(_completeness_problems(detail, scene))
    problems.extend(_supplied_model_problems(detail, scene))
    lines = {p.commercial_line_ref for p in scene.parts if p.commercial_line_ref is not None}
    bom = "design BOM" if detail.binding.local else "Customer Purchase BOM"
    problems.extend(f"line display name {key!r} names no {bom} line of the bound scene"
                    for key in sorted(set(detail.line_display_names_zh_cn) - lines))
    return tuple(problems)


def _supplied_model_problems(detail: AssemblyDetailPackageV1, scene: AssemblyPreviewSceneV1) -> list[str]:
    """Each named catalog model is the OpenSci product of a customer-supplied design anchor of the bound scene."""

    anchors = {a.instance_id: a for a in scene.beam_reference.design_anchors}
    out = []
    for model in detail.customer_supplied_models:
        anchor = anchors.get(model.instance_id)
        supply = anchor.customer_supply if anchor is not None else None
        if supply is None or supply.opensci_sku != model.opensci_sku:
            out.append(f"customer-supplied model {model.instance_id!r} is not the OpenSci product of a customer-supplied "
                       "component of the bound scene")
    return out


def _completeness_problems(detail: AssemblyDetailPackageV1, scene: AssemblyPreviewSceneV1) -> list[str]:
    """Required drawing facts: a length setting the drawing prints must be shown by exactly one dimension whose value is
    measured from the poses (so a stored setting value can never be printed unchecked)."""

    out: list[str] = []
    for setting in scene.settings:
        if setting.kind is not SettingKindV1.INSERTION_DEPTH:
            continue
        shown = [d for d in detail.dimensions if d.kind is DimensionKindV1.INSERTION_DEPTH and d.setting is not None
                 and d.setting.part_ref == setting.part_ref]
        if len(shown) != 1:
            out.append(f"missing required drawing fact: the INSERTION_DEPTH setting of {setting.part_ref!r} must be shown "
                       f"by exactly one INSERTION_DEPTH dimension (found {len(shown)})")
    return out


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [k for k, _ in pairs]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate JSON object key")
    return dict(pairs)


def load_assembly_detail(
    detail_bytes: bytes,
    preview: AssemblyPreviewPackageV1,
    *,
    expected_content_hash: str | None = None,
) -> VerifiedAssemblyDetailV1:
    """Verify ``detail_bytes`` against ``preview`` and return it with every dimension measured, or raise
    ``AssemblyDetailPackageError``."""

    problems = verify_assembly_detail(detail_bytes, preview, expected_content_hash=expected_content_hash)
    if problems:
        raise AssemblyDetailPackageError(problems)
    detail = AssemblyDetailPackageV1.model_validate_json(bytes(detail_bytes))
    measured = {d.dim_ref: measure(d, preview.scene) for d in detail.dimensions}
    return VerifiedAssemblyDetailV1(detail=detail, preview=preview, measured=MappingProxyType(measured),
                                    detail_bytes=bytes(detail_bytes))
