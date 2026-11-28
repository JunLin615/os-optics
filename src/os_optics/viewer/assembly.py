"""The Experimental Lab Assembly of a verified Assembly Preview Package as a ``ViewerScene`` (in memory only).

Every part is its package GLB (part frame, metres) scaled to millimetres and placed exactly at ``pose_in_world``;
the beam is the package's beam reference axis; the customer's own hardware and the design observation planes are
markers at their design anchors. Nothing is re-posed, snapped or spaced here, and nothing is written anywhere.

Coloured by its checks (``validation``: the package's layout check; ``findings``: a local assembly plan's findings,
named by the plan's item ids, which are the package's part refs): a part whose worst status is not PASS takes the status
colour (``palette.CHECK_STATUS_RGB``: WARNING amber, FAIL / ERROR red, UNKNOWN grey), and its facts carry
``check_status`` and its ``findings`` (ids and codes). ``focus_items``: the parts of one finding (``view --finding``),
highlighted and framed. ``supply``: who supplies each part (a local plan's design BOM), carried in the facts.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from .._vendor.shared_contracts.assembly_preview import AssemblyPreviewPackageV1, parse_glb
from ..optical_view.colors import wavelength_display_rgb
from ..optical_view.glb import POLISHED_GLASS
from .model import ASSEMBLY_VIEW, ViewBeam, ViewerScene, ViewMarker, ViewMesh, ViewPart, place
from .palette import ASSEMBLY_CLASS_BY_CATEGORY, CHECK_STATUS_RGB, HIGHLIGHT_RGB, class_table

MM_PER_M = 1000.0
FLOAT32 = 5126
INDEX_TYPES = {5121: "<u1", 5123: "<u2", 5125: "<u4"}
TRIANGLES = 4
#: A design axis without one wavelength (a multi-line source): neutral grey, unlike every wavelength's display colour.
DEFAULT_BEAM_RGB = (0.55, 0.58, 0.62)
#: Worst-first order of a part's check status, and what a validation status / finding severity counts as.
CHECK_STATUS_ORDER = ("PASS", "WARNING", "UNKNOWN", "FAIL")
VALIDATION_STATUS = {"PASS": "PASS", "NOT_APPLICABLE": "PASS", "WARNING": "WARNING", "UNKNOWN": "UNKNOWN",
                     "OUT_OF_MODEL_SCOPE": "UNKNOWN", "FAIL": "FAIL"}
FINDING_STATUS = {"INFO": "PASS", "WARNING": "WARNING", "UNKNOWN": "UNKNOWN", "ERROR": "FAIL"}


class AssemblyViewError(ValueError):
    """The package cannot be shown (it is verified first; a part geometry that cannot be decoded is refused)."""


def _axis_runs(axis: Any) -> tuple[tuple[str, np.ndarray], ...]:
    """A beam axis as runs of one display role: ``DESIGN_AXIS`` for its main-beam stretches, ``SECONDARY`` for the
    stretches only weak light reaches (``segment_roles``), in path order."""

    points = np.asarray(axis.points_mm, dtype=float)
    roles = [r.value for r in axis.segment_roles] if axis.segment_roles else None
    if not roles:
        return (("DESIGN_AXIS", points),)
    runs, start = [], 0
    for k in range(1, len(roles) + 1):
        if k == len(roles) or roles[k] != roles[start]:
            runs.append(("SECONDARY" if roles[start] == "SECONDARY" else "DESIGN_AXIS", points[start:k + 1]))
            start = k
    return tuple(runs)


def _accessor(document: dict[str, Any], binary: bytes, index: int, dtype: str, width: int) -> np.ndarray:
    accessor = document["accessors"][index]
    view = document["bufferViews"][accessor["bufferView"]]
    offset = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
    count = accessor["count"] * width
    return np.frombuffer(binary, dtype=dtype, count=count, offset=offset).astype(np.float64 if dtype == "<f4" else np.int64)


def part_meshes(data: bytes) -> list[tuple[np.ndarray, np.ndarray]]:
    """``(positions mm, triangles)`` of every triangle primitive of one part GLB (part frame)."""

    document, binary = parse_glb(data)
    out = []
    for mesh in document.get("meshes", ()):
        for prim in mesh["primitives"]:
            if prim.get("mode", TRIANGLES) != TRIANGLES:
                raise AssemblyViewError("a part geometry holds a non-triangle primitive")
            positions = _accessor(document, binary, prim["attributes"]["POSITION"], "<f4", 3).reshape(-1, 3) * MM_PER_M
            if "indices" in prim:
                accessor = document["accessors"][prim["indices"]]
                dtype = INDEX_TYPES.get(accessor["componentType"])
                if dtype is None:
                    raise AssemblyViewError("a part geometry uses an unsupported index type")
                triangles = _accessor(document, binary, prim["indices"], dtype, 1).reshape(-1, 3)
            else:
                triangles = np.arange(len(positions), dtype=np.int64).reshape(-1, 3)
            if len(triangles) and (triangles.max() >= len(positions) or triangles.min() < 0):
                raise AssemblyViewError("a part geometry indexes past its vertices")
            out.append((positions, triangles))
    return out


def _chain_root(scene, part) -> str | None:
    """The part directly on the breadboard that (indirectly) carries ``part``: its support chain."""

    parts = {p.part_ref: p for p in scene.parts}
    ref, board = part.part_ref, scene.breadboard.part_ref
    while ref in parts and parts[ref].support is not None and parts[ref].support.parent_ref != board:
        ref = parts[ref].support.parent_ref
    return None if ref == board else ref


def _worst(statuses) -> str:  # noqa: ANN001 - an iterable of CHECK_STATUS_ORDER values
    return max(statuses, key=CHECK_STATUS_ORDER.index, default="PASS")


def part_check_status(package: AssemblyPreviewPackageV1, *, validation: Any = None, findings: Any = ()) -> dict[str, dict[str, Any]]:
    """``{part_ref: {"check_status", "findings": [{finding_id, code, severity}]}}`` of every part: the worst of its layout
    check status (``validation.parts``) and the severities of the plan findings naming it."""

    out: dict[str, dict[str, Any]] = {p.part_ref: {"check_status": "PASS", "findings": []} for p in package.scene.parts}
    if validation is not None:
        for row in validation.parts:
            if row.part_ref in out:
                out[row.part_ref]["check_status"] = VALIDATION_STATUS.get(row.status.value, "UNKNOWN")
    for finding in findings or ():
        for ref in finding.item_ids:
            if ref in out:
                entry = out[ref]
                entry["findings"].append({"finding_id": finding.finding_id, "code": finding.code.value,
                                          "severity": finding.severity.value})
                entry["check_status"] = _worst((entry["check_status"], FINDING_STATUS[finding.severity.value]))
    return out


def assembly_view_scene(package: AssemblyPreviewPackageV1, *, validation: Any = None, findings: Any = (),
                        focus_items: Any = None, supply: Any = None) -> ViewerScene:
    """The view of a verified package (``os_optics.assembly.load_assembly_preview`` or the client helper's
    in-memory preview). Reads only ``package.scene`` and ``package.files`` -- and, to colour the parts by their checks,
    ``validation`` (the package's ``AssemblyLocalValidationV1``) and ``findings`` (a plan's ``FindingV1`` list);
    ``focus_items`` (part refs) are highlighted and framed; ``supply`` (``{part_ref: supply}``) goes into the facts."""

    if not isinstance(package, AssemblyPreviewPackageV1):
        raise AssemblyViewError("view_assembly needs a verified AssemblyPreviewPackageV1 (os_optics.assembly.load_assembly_preview)")
    scene = package.scene
    classes = class_table(ASSEMBLY_VIEW)
    checked = validation is not None or bool(findings)
    status = part_check_status(package, validation=validation, findings=findings) if checked else {}
    focus = set(focus_items or ())
    if unknown := sorted(focus - {p.part_ref for p in scene.parts}):
        raise AssemblyViewError(f"no part {', '.join(unknown)} in this assembly")
    parts = []
    for part in sorted(scene.parts, key=lambda p: (p.assembly_order, p.part_ref)):
        part_class = ASSEMBLY_CLASS_BY_CATEGORY.get(part.category.value, "OTHER_SUPPORT")
        style = classes[part_class]
        try:
            raw = part_meshes(package.files[part.geometry.asset_path])
        except (KeyError, ValueError) as exc:
            raise AssemblyViewError(f"part {part.part_ref}: its geometry cannot be read ({type(exc).__name__})") from None
        pose = part.pose_in_world
        finish = {"transmission": POLISHED_GLASS.transmission, "roughness": POLISHED_GLASS.roughness} if style.alpha < 1.0 else {}
        rgb = style.rgb
        if part.part_ref in focus:
            rgb = HIGHLIGHT_RGB
        elif part.part_ref in status and status[part.part_ref]["check_status"] in CHECK_STATUS_RGB:
            rgb = CHECK_STATUS_RGB[status[part.part_ref]["check_status"]]
        meshes = tuple(ViewMesh(positions=place(pos, pose.position_mm, pose.quaternion_wxyz), triangles=tri, rgb=rgb, alpha=style.alpha,
                                material=part_class, **finish) for pos, tri in raw)
        facts: dict[str, Any] = {"category": part.category.value, "assembly_order": part.assembly_order,
                                 "position_mm": list(pose.position_mm), "quaternion_wxyz": list(pose.quaternion_wxyz)}
        line = getattr(part, "commercial_line_ref", None)
        if line is not None:
            facts["line_ref"] = line
        if part.part_ref == scene.breadboard.part_ref and getattr(scene.breadboard, "supplied_by", None) is not None:
            facts["supplied_by"] = scene.breadboard.supplied_by  # the customer's own optical table: not supplied
        if part.design_binding is not None:
            facts["design_instance_id"] = part.design_binding.instance_id
        if part.support is not None:
            facts["carried_by"] = part.support.parent_ref
            facts["support_relation"] = part.support.relation.value
        chain = _chain_root(scene, part)
        if chain is not None:
            facts["chain"] = chain
        if checked:
            facts["check_status"] = status[part.part_ref]["check_status"]
            facts["findings"] = status[part.part_ref]["findings"]
        if package.manifest.binding.local and line is not None:
            facts["design_bom_line"] = line
        if supply and part.part_ref in supply:
            facts["supply"] = supply[part.part_ref]
        if part.part_ref in focus:
            facts["highlighted"] = True
        parts.append(ViewPart(part_id=part.part_ref, part_class=part_class, label=part.identity.display_name,
                              sku=part.identity.opensci_sku, meshes=meshes, facts=facts))
    reference = scene.beam_reference
    beams = []
    for axis in reference.beam_axes:
        rgb = wavelength_display_rgb(axis.wavelength_nm) if axis.wavelength_nm is not None else DEFAULT_BEAM_RGB
        facts: dict[str, Any] = {"basis": axis.basis.value}
        if axis.notes:
            facts["notes"] = [n.value for n in axis.notes]
        beams.append(ViewBeam(beam_id=axis.path_key, rgb=rgb, wavelength_nm=axis.wavelength_nm, runs=_axis_runs(axis), facts=facts))
    markers = []
    for anchor in reference.design_anchors:
        if getattr(anchor, "customer_supplied", False):
            markers.append(ViewMarker(marker_id=anchor.instance_id, marker_class="CUSTOMER_SUPPLIED",
                                      label=f"{anchor.instance_id} (your own hardware)", position=np.asarray(anchor.position_mm, dtype=float),
                                      facts={"design_instance_id": anchor.instance_id, "anchor_kind": anchor.anchor_kind.value,
                                             **_supply_facts(anchor)}))
        elif anchor.anchor_kind.value == "OBSERVATION":
            markers.append(ViewMarker(marker_id=anchor.instance_id, marker_class="OBSERVATION", label=f"{anchor.instance_id} (observation plane)",
                                      position=np.asarray(anchor.position_mm, dtype=float),
                                      facts={"design_instance_id": anchor.instance_id, "anchor_kind": anchor.anchor_kind.value}))
    lo = np.asarray(scene.bounds.min_mm, dtype=float)
    hi = np.asarray(scene.bounds.max_mm, dtype=float)
    focus_lo, focus_hi = lo, hi
    if focus:
        boxes = [p.bounds for p in parts if p.part_id in focus]
        focus_lo = np.min(np.stack([b[0] for b in boxes]), axis=0)
        focus_hi = np.max(np.stack([b[1] for b in boxes]), axis=0)
    board = scene.breadboard
    identity = {"package_content_hash": package.content_hash, "breadboard": board.part_ref,
                "breadboard_outline_mm": [list(board.outline_min_xy_mm), list(board.outline_max_xy_mm)],
                "breadboard_top_z_mm": board.top_z_mm,
                "optical_axis_height_mm": reference.optical_axis_height_mm}
    plan = package.manifest.binding.local_plan
    if plan is not None:
        identity.update({"plan_id": plan.plan_id, "plan_revision": plan.revision, "plan_digest": plan.plan_digest.digest})
    if validation is not None:
        identity["layout_validation"] = {"status": validation.status.value, "result_hash": validation.result_hash}
    if focus:
        identity["focus_items"] = sorted(focus)
    return ViewerScene(
        view_kind=ASSEMBLY_VIEW, frame=scene.frame, title="Experimental Lab Assembly", parts=tuple(parts), beams=tuple(beams),
        markers=tuple(markers), focus_min=focus_lo, focus_max=focus_hi, bounds_min=lo, bounds_max=hi, identity=identity,
    )


def _supply_facts(anchor) -> dict[str, str]:  # noqa: ANN001 - the vendored DesignAnchorV1
    """What the customer supplies at a customer-supplied anchor: role, reason, OpenSci product."""

    supply = getattr(anchor, "customer_supply", None)
    if supply is None:
        return {}
    facts = {"optical_role": supply.optical_role, "reason": supply.reason.value}
    if supply.opensci_sku:
        facts["opensci_sku"] = supply.opensci_sku
    if supply.reserved_envelope is not None:
        facts["reserved_envelope"] = "CYLINDER"
    return facts
