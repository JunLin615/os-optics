"""Plan preview package -> Assembly Detail Package (detail 3.2, release ``LOCAL_PLAN``): the drawing's dimensions.

``project_plan_detail(package, revision, context)`` issues the dimension references of one verified LOCAL_PLAN preview
package with the same fixed, closed rule table OpenSci applies to a delivered package (``assembly_detail_rules``):

* per optical element -- ``OPTICAL_AXIS_HEIGHT`` (board top -> optic origin, RESOLVED), ``OPTIC_BOARD_POSITION`` along X
  and Y (board corner -> the optic's optical centre, its origin in a scene without that datum; RESOLVED), and for each
  customer-supplied light source ``EXTERNAL_SOURCE_STANDOFF`` along the dominant world axis (CUSTOMER_DESIGN) to the
  optic the source's nominal beam meets first (none when the first component on that beam is customer-supplied);
* per part INSERTED_INTO its support with an INSERTION_DEPTH setting -- ``INSERTION_DEPTH`` between the two mated
  datums, with exactly the setting's basis;
* per fixation hole -- ``FIXATION_HOLE_POSITION`` along X and Y (RESOLVED) and ``FIXATION_OFFSET`` of the fixed part's
  origin from the hole along each plan axis where it is non-zero (REFERENCE).

``setting_bases`` is the provenance cross-check: the basis of every setting as the plan revision states it. A scene
setting whose basis disagrees refuses the projection (``PLAN_DIR_INCONSISTENT``, ``details.code`` ``DETAIL_PROVENANCE_MISMATCH``). The catalog model of each
customer-supplied catalog optic and the Simplified Chinese design BOM line names come from the plan's items and its
design BOM. Pure: no file, network, clock or environment access.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .._vendor.shared_contracts.assembly_detail import (
    MEASURE_TOLERANCE_MM,
    AssemblyDetailPackageError,
    AssemblyDetailPackageV1,
    CustomerSuppliedModelV1,
    DetailReleaseRefV1,
    DimensionBasisV1,
    DimensionKindV1,
    DimensionV1,
    MeasureAxisV1,
    PointKindV1,
    PointRefV1,
    ReleaseBasisV1,
    SettingRefV1,
    build_assembly_detail,
    point_in_world,
)
from .._vendor.shared_contracts.assembly_preview import AssemblyPreviewPackageV1
from .._vendor.shared_contracts.assembly_preview.scene import (OPTICAL_CENTRE_DATUM, AssemblyPreviewSceneV1, PartCategoryV1,
                                                                SettingKindV1, SupportRelationV1)
from .contract import AssemblyPlanError, AssemblyPlanErrorCode, AssemblyPlanRevisionV1, PlanFactBasisV1
from .projection import SETTING_BASIS

DETAIL_PROJECTOR_ID = "os-optics.assembly_plan.detail"
DETAIL_PROJECTOR_VERSION = "1.0.0"  # the rule table of OpenSci's detail projector 3.2.0, over a LOCAL_PLAN package

_PLAN = (MeasureAxisV1.WORLD_X, MeasureAxisV1.WORLD_Y)
_AXES = (MeasureAxisV1.WORLD_X, MeasureAxisV1.WORLD_Y, MeasureAxisV1.WORLD_Z)
LOCAL_RELEASE = DetailReleaseRefV1(basis=ReleaseBasisV1.LOCAL_PLAN)


def _board_top(scene: AssemblyPreviewSceneV1) -> PointRefV1:
    board = scene.part(scene.breadboard.part_ref)
    for datum in board.datums:
        if datum.datum_key == "board_top":
            return PointRefV1(kind=PointKindV1.PART_DATUM, part_ref=board.part_ref, datum_key="board_top")
    return PointRefV1(kind=PointKindV1.BOARD_CORNER, part_ref=board.part_ref)


def _light_source(anchor: Any) -> bool:
    """A customer-supplied light source anchor (a customer-supplied optical component is not a source)."""

    supply = anchor.customer_supply
    return anchor.customer_supplied and (supply is None or supply.optical_role == "source")


def _first_optic_fed(scene: AssemblyPreviewSceneV1) -> dict[str, set[str]]:
    """``{customer source instance id: {optic part_ref}}``: the optic each source's nominal beam axis meets first."""

    anchors = scene.beam_reference.design_anchors
    components = [a for a in anchors if a.realized_by_part_ref is not None or a.customer_supplied]

    def at(point: Any, anchor: Any) -> bool:
        return max(abs(p - q) for p, q in zip(point, anchor.position_mm, strict=True)) <= MEASURE_TOLERANCE_MM

    by_id = {a.instance_id: a for a in anchors}
    component_ids = {a.instance_id for a in components}
    fed: dict[str, set[str]] = {}
    for beam in scene.beam_reference.beam_axes:
        named = [by_id.get(i) for i in beam.through] if beam.through is not None else None
        if named is not None:
            source = named[0] if named[0] is not None and _light_source(named[0]) else None
        else:
            source = next((a for a in anchors if _light_source(a) and at(beam.points_mm[0], a)), None)
        if source is None:
            continue
        for k, point in enumerate(beam.points_mm[1:], start=1):
            if named is not None:
                reached = named[k] if named[k] is not None and named[k].instance_id in component_ids else None
            else:
                reached = next((a for a in components if at(point, a)), None)
            if reached is None:
                continue
            if reached.realized_by_part_ref is not None:
                fed.setdefault(source.instance_id, set()).add(reached.realized_by_part_ref)
            break
    return fed


def _dominant_axis(a: tuple[float, float, float], b: tuple[float, float, float]) -> MeasureAxisV1:
    deltas = [abs(b[i] - a[i]) for i in range(3)]
    return _AXES[deltas.index(max(deltas))]


def assembly_detail_dimensions(scene: AssemblyPreviewSceneV1) -> tuple[DimensionV1, ...]:
    """The dimension references of the fixed rule table for ``scene`` (see the module docstring)."""

    board = scene.breadboard
    corner = PointRefV1(kind=PointKindV1.BOARD_CORNER, part_ref=board.part_ref)
    specs: list[tuple[DimensionKindV1, str, PointRefV1, PointRefV1, MeasureAxisV1, DimensionBasisV1, SettingRefV1 | None]] = []
    fed = _first_optic_fed(scene)
    for part in sorted(scene.parts, key=lambda p: (p.assembly_order, p.part_ref)):
        ref = part.part_ref
        origin = PointRefV1(kind=PointKindV1.PART_ORIGIN, part_ref=ref)
        for fixation in (f for f in scene.fixations if f.part_ref == ref):
            for i, j in fixation.board_holes:
                hole = PointRefV1(kind=PointKindV1.BOARD_HOLE, part_ref=board.part_ref, hole=(i, j))
                for axis in _PLAN:
                    specs.append((DimensionKindV1.FIXATION_HOLE_POSITION, ref, corner, hole, axis, DimensionBasisV1.RESOLVED, None))
                here, there = point_in_world(origin, scene), point_in_world(hole, scene)
                for index, axis in enumerate(_PLAN):
                    if abs(there[index] - here[index]) > MEASURE_TOLERANCE_MM:
                        specs.append((DimensionKindV1.FIXATION_OFFSET, ref, origin, hole, axis, DimensionBasisV1.REFERENCE, None))
        support = part.support
        if support is not None and support.relation is SupportRelationV1.INSERTED_INTO:
            setting = next((s for s in scene.settings if s.part_ref == ref and s.kind is SettingKindV1.INSERTION_DEPTH), None)
            mate = next((m for m in scene.mates if m.mate_ref == support.mate_ref), None)
            if setting is not None and mate is not None:
                own, parents = (mate.datum_a, mate.datum_b) if mate.part_a_ref == ref else (mate.datum_b, mate.datum_a)
                frm = PointRefV1(kind=PointKindV1.PART_DATUM, part_ref=ref, datum_key=own)
                to = PointRefV1(kind=PointKindV1.PART_DATUM, part_ref=support.parent_ref, datum_key=parents)
                specs.append((DimensionKindV1.INSERTION_DEPTH, ref, frm, to, MeasureAxisV1.WORLD_Z,
                              DimensionBasisV1(setting.basis.value), SettingRefV1(part_ref=ref, kind=setting.kind)))
        if part.category is PartCategoryV1.OPTICAL_ELEMENT:
            if scene.beam_reference.optical_axis_height_mm is not None or any(
                    a.realized_by_part_ref == ref for a in scene.beam_reference.design_anchors):
                specs.append((DimensionKindV1.OPTICAL_AXIS_HEIGHT, ref, _board_top(scene), origin, MeasureAxisV1.WORLD_Z,
                              DimensionBasisV1.RESOLVED, None))
            centre = (PointRefV1(kind=PointKindV1.PART_DATUM, part_ref=ref, datum_key=OPTICAL_CENTRE_DATUM)
                      if any(d.datum_key == OPTICAL_CENTRE_DATUM for d in part.datums) else origin)
            for axis in _PLAN:
                specs.append((DimensionKindV1.OPTIC_BOARD_POSITION, ref, corner, centre, axis, DimensionBasisV1.RESOLVED, None))
            for anchor in sorted(scene.beam_reference.design_anchors, key=lambda a: a.instance_id):
                if _light_source(anchor) and ref in fed.get(anchor.instance_id, ()):
                    src = PointRefV1(kind=PointKindV1.DESIGN_ANCHOR, instance_id=anchor.instance_id)
                    axis = _dominant_axis(point_in_world(src, scene), point_in_world(centre, scene))
                    specs.append((DimensionKindV1.EXTERNAL_SOURCE_STANDOFF, ref, src, centre, axis,
                                  DimensionBasisV1.CUSTOMER_DESIGN, None))
    return tuple(DimensionV1(dim_ref=f"dim-{n}", kind=kind, subject_part_ref=subject, from_point=frm, to_point=to, axis=axis,
                             basis=basis, setting=setting)
                 for n, (kind, subject, frm, to, axis, basis, setting) in enumerate(specs, start=1))


def setting_bases_of(revision: AssemblyPlanRevisionV1) -> dict[tuple[str, str], str]:
    """``{(item_id, setting kind): scene basis}`` of every placement setting of ``revision``."""

    return {(item.item_id, s.kind.value): SETTING_BASIS[PlanFactBasisV1(s.basis)].value
            for item in revision.items if item.placement is not None for s in item.placement.settings}


def customer_supplied_models(package: AssemblyPreviewPackageV1, revision: AssemblyPlanRevisionV1) -> tuple[CustomerSuppliedModelV1, ...]:
    """The catalog model of every customer-supplied anchor that names an OpenSci product, from the plan's items."""

    by_instance = {item.design_instance_id: item for item in revision.items if item.design_instance_id is not None}
    out = []
    for anchor in package.scene.beam_reference.design_anchors:
        supply = anchor.customer_supply
        item = by_instance.get(anchor.instance_id)
        if supply is None or supply.opensci_sku is None or item is None:
            continue
        out.append(CustomerSuppliedModelV1(instance_id=anchor.instance_id, opensci_sku=supply.opensci_sku,
                                           display_name=item.identity.display_name,
                                           display_name_zh_cn=item.identity.display_name_zh_cn))
    return tuple(sorted(out, key=lambda m: m.instance_id))


def line_names_zh_cn(package: AssemblyPreviewPackageV1, design_bom: Mapping[str, Any]) -> dict[str, str]:
    """The Simplified Chinese name of each design BOM line the scene's parts carry."""

    used = {p.commercial_line_ref for p in package.scene.parts if p.commercial_line_ref is not None}
    return {line["line_id"]: line["display_name_zh_cn"] for line in design_bom.get("lines", ())
            if line.get("line_id") in used and line.get("display_name_zh_cn")}


def project_plan_detail(package: AssemblyPreviewPackageV1, revision: AssemblyPlanRevisionV1, context: Any) -> AssemblyDetailPackageV1:
    """The Assembly Detail Package (3.2) of a verified LOCAL_PLAN preview ``package`` of ``revision``. ``context`` gives
    the revision's design BOM (``context.design_bom``)."""

    binding = package.manifest.binding
    if not binding.local or binding.local_plan.plan_digest.digest != revision.content_digest:  # type: ignore[union-attr]
        raise AssemblyPlanError(AssemblyPlanErrorCode.PLAN_DIR_INCONSISTENT,
                                "the preview package is not the LOCAL_PLAN package of this plan revision")
    recorded = setting_bases_of(revision)
    for setting in package.scene.settings:
        if recorded.get((setting.part_ref, setting.kind.value)) != setting.basis.value:
            raise AssemblyPlanError(
                AssemblyPlanErrorCode.PLAN_DIR_INCONSISTENT,
                f"setting {setting.kind.value} of {setting.part_ref!r} has basis {setting.basis.value} in the preview but "
                f"{recorded.get((setting.part_ref, setting.kind.value))!r} in the plan revision",
                {"code": "DETAIL_PROVENANCE_MISMATCH", "part_ref": setting.part_ref, "kind": setting.kind.value})
    dimensions = assembly_detail_dimensions(package.scene)
    if not dimensions:
        raise AssemblyPlanError(AssemblyPlanErrorCode.PLAN_HAS_FINDINGS, "the preview scene has no dimensionable feature",
                                {"code": "DETAIL_NOTHING_TO_DIMENSION"})
    design_bom = getattr(context, "design_bom", None) or {}
    try:
        return build_assembly_detail(preview=package, release=LOCAL_RELEASE, dimensions=dimensions,
                                     customer_supplied_models=customer_supplied_models(package, revision),
                                     line_display_names_zh_cn=line_names_zh_cn(package, design_bom))
    except AssemblyDetailPackageError as exc:
        raise AssemblyPlanError(AssemblyPlanErrorCode.PLAN_DIR_INCONSISTENT, "the detail package does not verify",
                                {"code": "DETAIL_INVALID"}, list(exc.problems)) from None
