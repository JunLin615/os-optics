"""Plan-level checks (DESIGN 1.3.6, 2.1): what a revision's own decisions say about fit, height, fixation, reserved space,
supply and agreement with its Project, recomputed on every write and every ``plan check`` (findings of source CHECK).

* PLAN_INTERFACES, per resolved chain (optic -> mount -> post -> holder -> board, with the bound catalogs' facts):
  the seat (``INTERFACE_MISMATCH`` rules SHAPE, SIZE, THICKNESS_RANGE, THICKNESS_RANGE_UNKNOWN, TRANSMISSIVE), the post
  stud (THREAD; several fastenings allowed: ``FASTENING_VARIES``), the post in the holder (POST_DIAMETER; the insertion
  depth within the holder's interval and the post length: ``INSERTION_OUT_OF_RANGE``; the recorded depth closing the
  optic's height: ``HEIGHT_CLOSURE_MISMATCH``), the fork screw in the board (BOARD_THREAD); the DOF results
  (``DOF_NOT_DELIVERED``, ``DOF_MANUAL_FALLBACK``); identities the bound catalogs do not hold
  (``IDENTITY_NOT_IN_CATALOG``), optics without mechanics (``MISSING_MECHANICAL_DATA``) and catalogs other than the bound
  ones (``CATALOG_BINDING_CHANGED``).
* PLAN_LAYOUT: every holder's fixing hole on the board grid, on its fork slot's centreline within the slot, and not
  shared (``FIXATION_HOLE_INVALID``; a resolved holder without one: ``NO_FIXING_HOLE``); every declared envelope kept
  clear of every chain body (``RESERVED_SPACE_INTRUSION``, the envelope with the intruding hardware item).
* PLAN_SUPPLY: an optic OpenSci supplies unmounted (``OPENSCI_SUPPLIED_UNMOUNTED``), an item nobody supplies
  (``UNRESOLVED_SUPPLY``).
* PLAN_OPTICS: each optic and component placed where its Project puts it through the plan frame, and the same design
  instances in both (``PLAN_PROJECT_MISMATCH``).

Pure: reads the revision and its ``PlanContext`` only.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any

from .._vendor.shared_contracts.assembly_preview.validation import AssemblyIssueCodeV1
from .contract import (CUSTOMER_TABLE_SKU, AssemblyPlanContentV1, ChainLinkKindV1, FindingCheckV1, FindingSourceV1, FindingV1,
                       ItemIdentityKindV1, ItemRoleV1, MountingV1, PlanFindingCodeV1, PlanItemV1, SettingKindV1, SupplyReasonV1,
                       SupplyV1)
from .findings import make_finding
from .kernel import KBoard, KHolder, KMount, KPost, table_board
from .chain import slot_offset
from .kernel.assemble import ChainPoses, chain_bodies
from .kernel.geometry import IDENTITY, Pose, YawBox, overlap_depth, q_mul
from .kernel.resolve import SIZE_TOL
from .mechanical.model import OpticShapeV1

#: A position (mm) and a unit-quaternion dot product this close agree: the plan's number rule rounds positions to 1e-6 mm
#: and the frame's yaw to 1e-6 deg, which moves a point 300 mm from the frame origin by up to 2.6e-6 mm.
POSITION_TOLERANCE_MM = 1e-5
ROTATION_DOT_TOLERANCE = 1e-9
#: A recorded insertion depth closes the optic's height when it is this close to the depth the poses give (mm).
CLOSURE_TOLERANCE_MM = 2e-6
#: The thickness given to a customer's optical table in the kernel (its top is the lab origin plane).
CUSTOMER_TABLE_THICKNESS_MM = 12.7
#: Declared-envelope clearance: a penetration deeper than this is an intrusion (mm).
INTRUSION_TOLERANCE_MM = 1e-6

_I = PlanFindingCodeV1


def _finding(code: Any, items: Iterable[str], check: FindingCheckV1, *, interface: ChainLinkKindV1 | None = None,
             details: dict[str, Any] | None = None, missing: Iterable[str] = ()) -> FindingV1:
    return make_finding(code, items, check=check, source=FindingSourceV1.CHECK, interface=interface, details=details,
                        missing_data=missing)


def board_view(content: AssemblyPlanContentV1, context: Any) -> KBoard | None:
    """The kernel view of the revision's board: its catalog identity, or the customer table of the derive options."""

    board = content.item(content.board_item_id)
    if board is None:
        return None
    if board.identity.kind is ItemIdentityKindV1.CUSTOMER_TABLE:
        table = content.derive_options.customer_table
        if table is None:
            return None
        return table_board(CUSTOMER_TABLE_SKU, size_x_mm=table.size_x_mm, size_y_mm=table.size_y_mm, pitch_mm=table.pitch_mm,
                           edge_offset_mm=table.edge_offset_mm, thread=table.thread, thickness_mm=CUSTOMER_TABLE_THICKNESS_MM)
    view = context.kcatalog.member(board.identity.opensci_sku)
    return view if isinstance(view, KBoard) else None


def chain_items(content: AssemblyPlanContentV1, optic_id: str) -> dict[ItemRoleV1, PlanItemV1]:
    """``{role: item}`` of the hardware an optic's chain links name (mount, post, holder)."""

    carried_by = {link.a_item_id: link.b_item_id for link in content.links}
    out: dict[ItemRoleV1, PlanItemV1] = {}
    current = optic_id
    for role in (ItemRoleV1.MOUNT, ItemRoleV1.POST, ItemRoleV1.POST_HOLDER):
        nxt = carried_by.get(current)
        item = None if nxt is None else content.item(nxt)
        if item is None or item.role is not role:
            break
        out[role] = item
        current = item.item_id
    return out


def pose_of(item: PlanItemV1) -> Pose | None:
    if item.placement is None:
        return None
    p = item.placement.pose_in_lab
    return Pose(tuple(float(v) for v in p.position_mm), tuple(float(v) for v in p.quaternion_wxyz))  # type: ignore[arg-type]


def _setting(item: PlanItemV1, kind: SettingKindV1) -> Any:
    if item.placement is None:
        return None
    return next((s.value for s in item.placement.settings if s.kind is kind), None)


class ResolvedChain:
    """One optic's chain with the kernel views of its hardware (``views`` None when an identity is not in the catalogs)."""

    def __init__(self, optic: PlanItemV1, hardware: dict[ItemRoleV1, PlanItemV1], context: Any) -> None:
        self.optic = optic
        self.mount = hardware.get(ItemRoleV1.MOUNT)
        self.post = hardware.get(ItemRoleV1.POST)
        self.holder = hardware.get(ItemRoleV1.POST_HOLDER)
        self.resolved = all(h is not None and h.placement is not None for h in (self.mount, self.post, self.holder))
        views = [None if h is None else context.kcatalog.member(h.identity.opensci_sku or "") for h in (self.mount, self.post, self.holder)]
        self.mount_view, self.post_view, self.holder_view = views
        self.complete = (self.resolved and isinstance(self.mount_view, KMount) and isinstance(self.post_view, KPost)
                         and isinstance(self.holder_view, KHolder))

    def poses(self) -> ChainPoses | None:
        if not self.complete:
            return None
        mount, post, holder = pose_of(self.mount), pose_of(self.post), pose_of(self.holder)  # type: ignore[arg-type]
        optic = pose_of(self.optic)
        assert mount is not None and post is not None and holder is not None and optic is not None
        depth = self.holder_view.height_mm + self.post_view.length_mm - mount.position[2]  # type: ignore[union-attr]
        return ChainPoses(optic=optic, mount=mount, post=post, holder=holder, insertion_depth_mm=depth)

    def insertion_depth(self) -> float | None:
        poses = self.poses()
        return None if poses is None else poses.insertion_depth_mm


def resolved_chains(content: AssemblyPlanContentV1, context: Any) -> list[ResolvedChain]:
    return [ResolvedChain(item, chain_items(content, item.item_id), context) for item in content.items
            if item.role is ItemRoleV1.OPTIC and item.mounting is MountingV1.CHAIN]


# ------------------------------------------------------------------------------------------------ interfaces


def _seat_findings(chain: ResolvedChain, context: Any) -> list[FindingV1]:
    optic, mount = chain.optic, chain.mount_view
    assert isinstance(mount, KMount) and chain.mount is not None
    mechanics = context.optic_mechanics(optic)
    items = (optic.item_id, chain.mount.item_id)
    if mechanics is None:
        return [_finding(_I.MISSING_MECHANICAL_DATA, (optic.item_id,), FindingCheckV1.PLAN_INTERFACES,
                         missing=(f"{optic.identity.opensci_sku}:mechanics",))]
    holds = mount.holds
    mismatch: list[tuple[str, Any, Any]] = []
    if holds.shape is not mechanics.shape:
        mismatch.append(("SHAPE", holds.shape.value, mechanics.shape.value))
    elif abs(holds.size_mm - mechanics.size_mm) > holds.size_tolerance_mm + SIZE_TOL:
        mismatch.append(("SIZE", holds.size_mm, mechanics.size_mm))
    else:
        if holds.thickness_min_mm is None and holds.thickness_max_mm is None and mechanics.shape is not OpticShapeV1.CUBE:
            mismatch.append(("THICKNESS_RANGE_UNKNOWN", None, mechanics.thickness_mm))
        elif ((holds.thickness_min_mm is not None and mechanics.thickness_mm < holds.thickness_min_mm - SIZE_TOL)
              or (holds.thickness_max_mm is not None and mechanics.thickness_mm > holds.thickness_max_mm + SIZE_TOL)):
            mismatch.append(("THICKNESS_RANGE", [holds.thickness_min_mm, holds.thickness_max_mm], mechanics.thickness_mm))
        if mechanics.transmissive and not holds.transmissive:
            mismatch.append(("TRANSMISSIVE", "a mount that leaves a clear aperture", "a mount without one"))
    return [_finding(_I.INTERFACE_MISMATCH, items, FindingCheckV1.PLAN_INTERFACES, interface=ChainLinkKindV1.OPTIC_IN_MOUNT,
                     details={"rule": rule, "expected": expected, "found": found}) for rule, expected, found in mismatch]


def _chain_findings(chain: ResolvedChain, board: KBoard | None, board_id: str, context: Any) -> list[FindingV1]:
    out = _seat_findings(chain, context)
    mount, post, holder = chain.mount_view, chain.post_view, chain.holder_view
    assert isinstance(mount, KMount) and isinstance(post, KPost) and isinstance(holder, KHolder)
    m, p, h = chain.mount, chain.post, chain.holder
    assert m is not None and p is not None and h is not None
    if post.top_thread != mount.post_thread:
        out.append(_finding(_I.INTERFACE_MISMATCH, (m.item_id, p.item_id), FindingCheckV1.PLAN_INTERFACES,
                            interface=ChainLinkKindV1.MOUNT_ON_POST,
                            details={"rule": "THREAD", "expected": mount.post_thread, "found": post.top_thread}))
    if len(mount.fastening_options) > 1:
        out.append(_finding(_I.FASTENING_VARIES, (m.item_id, p.item_id), FindingCheckV1.PLAN_INTERFACES,
                            details={"fastening_options": list(mount.fastening_options)}))
    if abs(post.diameter_mm - holder.post_diameter_mm) > SIZE_TOL:
        out.append(_finding(_I.INTERFACE_MISMATCH, (p.item_id, h.item_id), FindingCheckV1.PLAN_INTERFACES,
                            interface=ChainLinkKindV1.POST_IN_HOLDER,
                            details={"rule": "POST_DIAMETER", "expected": holder.post_diameter_mm, "found": post.diameter_mm}))
    depth = chain.insertion_depth()
    assert depth is not None
    low, high = holder.insertion_min_mm, min(holder.insertion_max_mm, post.length_mm)
    if depth < low - SIZE_TOL or depth > high + SIZE_TOL:
        out.append(_finding(_I.INSERTION_OUT_OF_RANGE, (p.item_id, h.item_id), FindingCheckV1.PLAN_INTERFACES,
                            details={"insertion_depth_mm": depth, "allowed_mm": [low, high]}))
    recorded = _setting(p, SettingKindV1.INSERTION_DEPTH)
    try:
        recorded_mm = None if recorded is None else float(recorded)
    except (TypeError, ValueError):
        recorded_mm = None
    if recorded_mm is not None and abs(recorded_mm - depth) > CLOSURE_TOLERANCE_MM:
        out.append(_finding(_I.HEIGHT_CLOSURE_MISMATCH, (chain.optic.item_id, p.item_id), FindingCheckV1.PLAN_INTERFACES,
                            details={"recorded_insertion_depth_mm": recorded_mm, "closing_insertion_depth_mm": depth}))
    if board is not None and holder.base.thread != board.thread:
        out.append(_finding(_I.INTERFACE_MISMATCH, (h.item_id, board_id), FindingCheckV1.PLAN_INTERFACES,
                            interface=ChainLinkKindV1.HOLDER_ON_BOARD,
                            details={"rule": "BOARD_THREAD", "expected": board.thread, "found": holder.base.thread}))
    return out


def _dof_findings(optic: PlanItemV1) -> list[FindingV1]:
    out = []
    for dof in () if optic.placement is None else optic.placement.dofs:
        if dof.status.value == "NOT_DELIVERED":
            items = [optic.item_id] + ([dof.blocking_item_id] if dof.blocking_item_id is not None else [])
            out.append(_finding(_I.DOF_NOT_DELIVERED, items, FindingCheckV1.PLAN_INTERFACES,
                                details={"axis": dof.axis.value, "reason": dof.not_delivered_reason.value}))  # type: ignore[union-attr]
        elif dof.status.value == "DELIVERED_AS_MANUAL_FALLBACK":
            out.append(_finding(_I.DOF_MANUAL_FALLBACK, (optic.item_id,), FindingCheckV1.PLAN_INTERFACES,
                                details={"axis": dof.axis.value}))
    return out


# ------------------------------------------------------------------------------------------------ layout


def _fixation_findings(chains: list[ResolvedChain], board: KBoard | None, board_id: str) -> list[FindingV1]:
    out: list[FindingV1] = []
    if board is None:
        return out
    nx, ny = board.hole_count
    users: dict[tuple[int, int], list[str]] = {}
    for chain in chains:
        if not chain.complete:
            continue
        holder = chain.holder
        assert holder is not None and holder.placement is not None and isinstance(chain.holder_view, KHolder)
        fixation = holder.placement.fixation
        if fixation is None:
            out.append(_finding(_I.NO_FIXING_HOLE, (holder.item_id,), FindingCheckV1.PLAN_LAYOUT))
            continue
        pose = pose_of(holder)
        assert pose is not None
        for hole in fixation.board_holes:
            users.setdefault(tuple(hole), []).append(holder.item_id)  # type: ignore[arg-type]
            if not (0 <= hole[0] < nx and 0 <= hole[1] < ny):
                out.append(_finding(_I.FIXATION_HOLE_INVALID, (holder.item_id,), FindingCheckV1.PLAN_LAYOUT,
                                    details={"hole": list(hole), "reason": "OFF_BOARD_GRID"}))
                continue
            along, across = slot_offset(board, tuple(hole), (pose.position[0], pose.position[1]), pose.rotation)  # type: ignore[arg-type]
            base = chain.holder_view.base
            if abs(across) > POSITION_TOLERANCE_MM or not base.slot_start_mm - POSITION_TOLERANCE_MM <= along <= base.slot_end_mm + POSITION_TOLERANCE_MM:
                out.append(_finding(_I.FIXATION_HOLE_INVALID, (holder.item_id,), FindingCheckV1.PLAN_LAYOUT,
                                    details={"hole": list(hole), "reason": "NOT_ON_SLOT",
                                             "slot_mm": [base.slot_start_mm, base.slot_end_mm], "along_mm": along,
                                             "across_mm": across}))
    for hole, holders in sorted(users.items()):
        if len(holders) > 1:
            out.append(_finding(_I.FIXATION_HOLE_INVALID, sorted(holders)[:2], FindingCheckV1.PLAN_LAYOUT,
                                details={"hole": list(hole), "reason": "SHARED"}))
    return out


def envelope_box(item: PlanItemV1) -> YawBox | None:
    """The lab bounding box of a reserved envelope (and the column below it down to the board top, lab z = 0)."""

    envelope, pose = item.envelope, pose_of(item)
    if envelope is None or pose is None:
        return None
    if envelope.shape == "BOX":
        lo, hi = envelope.min_mm, envelope.max_mm
    else:
        r = envelope.diameter_mm / 2.0  # type: ignore[operator]
        lo, hi = (-r, -r, envelope.z_min_mm), (r, r, envelope.z_max_mm)
    corners = [pose.apply((x, y, z)) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]  # type: ignore[index]
    low = tuple(min(c[k] for c in corners) for k in range(3))
    high = tuple(max(c[k] for c in corners) for k in range(3))
    if envelope.reserve_column:
        low = (low[0], low[1], min(low[2], 0.0))
    return YawBox(f"env:{item.item_id}", low, high, Pose((0.0, 0.0, 0.0), IDENTITY))  # type: ignore[arg-type]


def _intrusion_findings(content: AssemblyPlanContentV1, chains: list[ResolvedChain]) -> list[FindingV1]:
    envelopes = [(i, envelope_box(i)) for i in content.items if i.role is ItemRoleV1.DECLARED_ENVELOPE]
    out: list[FindingV1] = []
    for chain in chains:
        poses = chain.poses()
        if poses is None:
            continue
        bodies = chain_bodies(chain.mount_view, chain.post_view, chain.holder_view, poses)  # type: ignore[arg-type]
        owners = {"MOUNT": chain.mount, "POST": chain.post, "POST_HOLDER": chain.holder}
        for item, box in envelopes:
            if box is None:
                continue
            for role, boxes in sorted(bodies.items()):
                depth = max(overlap_depth(box, b) for b in boxes)
                if depth > INTRUSION_TOLERANCE_MM:
                    out.append(_finding(AssemblyIssueCodeV1.RESERVED_SPACE_INTRUSION, (item.item_id, owners[role].item_id), FindingCheckV1.PLAN_LAYOUT,  # type: ignore[union-attr]
                                        details={"penetration_mm": depth}))
    return out


# ------------------------------------------------------------------------------------------------ supply / catalogs / optics


def _supply_findings(content: AssemblyPlanContentV1) -> list[FindingV1]:
    out = []
    for item in content.items:
        if item.supply_reason is SupplyReasonV1.OPENSCI_SUPPLIED_UNMOUNTED:
            out.append(_finding(_I.OPENSCI_SUPPLIED_UNMOUNTED, (item.item_id,), FindingCheckV1.PLAN_SUPPLY))
        if item.supply is SupplyV1.UNRESOLVED:
            out.append(_finding(_I.UNRESOLVED_SUPPLY, (item.item_id,), FindingCheckV1.PLAN_SUPPLY,
                                details={"reason": None if item.supply_reason is None else item.supply_reason.value}))
    return out


def _catalog_findings(content: AssemblyPlanContentV1, context: Any) -> list[FindingV1]:
    out = []
    for item in content.items:
        kind, sku = item.identity.kind, item.identity.opensci_sku
        known = True
        if kind in (ItemIdentityKindV1.OS_MECH, ItemIdentityKindV1.USER_MECH):
            known = context.mechanical.identity(sku) is not None
        elif kind is ItemIdentityKindV1.OS_OPT:
            known = context.product(sku) is not None
        if not known:
            out.append(_finding(_I.IDENTITY_NOT_IN_CATALOG, (item.item_id,), FindingCheckV1.PLAN_INTERFACES,
                                details={"opensci_sku": sku}))
    bound = [b.model_dump(mode="json") for b in content.bindings.mechanical]
    supplied = [r.model_dump(mode="json") for r in context.mechanical.binding()]
    if bound != supplied:
        out.append(_finding(_I.CATALOG_BINDING_CHANGED, (), FindingCheckV1.PLAN_INTERFACES,
                            details={"catalog": "mechanical", "bound": [b["sha256"] for b in bound], "supplied": [s["sha256"] for s in supplied]}))
    version = getattr(context.catalog, "catalog_version", None)
    if version is not None and version != content.bindings.optical_catalog.catalog_version:
        out.append(_finding(_I.CATALOG_BINDING_CHANGED, (), FindingCheckV1.PLAN_INTERFACES,
                            details={"catalog": "optical", "bound": content.bindings.optical_catalog.catalog_version, "supplied": version}))
    return out


def frame_pose(content: AssemblyPlanContentV1) -> Pose:
    yaw = math.radians(content.frame.yaw_deg)
    half = yaw / 2.0
    return Pose(tuple(content.frame.translation_mm), (math.cos(half), 0.0, 0.0, math.sin(half)))  # type: ignore[arg-type]


def lab_pose_of_component(content: AssemblyPlanContentV1, component: Any) -> Pose:
    """Where the plan frame puts a Project component in the lab."""

    nominal = component.nominal_pose
    frame = frame_pose(content)
    position = frame.apply(tuple(float(v) for v in nominal.position_mm))  # type: ignore[arg-type]
    return Pose(position, q_mul(frame.rotation, tuple(float(v) for v in nominal.quaternion_wxyz)))  # type: ignore[arg-type]


def _same_pose(a: Pose, b: Pose) -> bool:
    if max(abs(x - y) for x, y in zip(a.position, b.position, strict=True)) > POSITION_TOLERANCE_MM:
        return False
    norm = math.sqrt(sum(c * c for c in a.rotation)) * math.sqrt(sum(c * c for c in b.rotation))
    return abs(abs(sum(x * y for x, y in zip(a.rotation, b.rotation, strict=True))) / norm - 1.0) <= ROTATION_DOT_TOLERANCE


def project_findings(content: AssemblyPlanContentV1, project: Any) -> list[FindingV1]:
    """PLAN_PROJECT_MISMATCH: pose or instance disagreement between the plan's design items and their Project."""

    out = []
    components = {c.instance_id: c for c in project.components}
    seen = set()
    for item in content.items:
        instance = item.design_instance_id
        if instance is None:
            continue
        seen.add(instance)
        component = components.get(instance)
        pose = pose_of(item)
        if component is None:
            out.append(_finding(_I.PLAN_PROJECT_MISMATCH, (item.item_id,), FindingCheckV1.PLAN_OPTICS,
                                details={"instance_id": instance, "reason": "NOT_IN_PROJECT"}))
        elif pose is not None and not _same_pose(pose, lab_pose_of_component(content, component)):
            expected = lab_pose_of_component(content, component)
            out.append(_finding(_I.PLAN_PROJECT_MISMATCH, (item.item_id,), FindingCheckV1.PLAN_OPTICS,
                                details={"instance_id": instance, "reason": "POSE_DIFFERS",
                                         "plan_position_mm": list(pose.position), "project_position_mm": list(expected.position)}))
    for instance in sorted(set(components) - seen):
        out.append(_finding(_I.PLAN_PROJECT_MISMATCH, (), FindingCheckV1.PLAN_OPTICS,
                            details={"instance_id": instance, "reason": "NOT_IN_PLAN"}))
    return out


def plan_findings(content: AssemblyPlanContentV1, context: Any) -> list[FindingV1]:
    """Every plan-level finding of a revision's decisions (module docstring)."""

    board = board_view(content, context)
    chains = resolved_chains(content, context)
    out = _supply_findings(content) + _catalog_findings(content, context) + project_findings(content, context.project)
    for chain in chains:
        if chain.complete:
            out += _chain_findings(chain, board, content.board_item_id, context)
        out += _dof_findings(chain.optic)
    out += _fixation_findings(chains, board, content.board_item_id)
    out += _intrusion_findings(content, chains)
    unique: dict[str, FindingV1] = {}
    for finding in out:
        unique.setdefault(finding.finding_id, finding)
    return list(unique.values())


__all__ = ["ResolvedChain", "board_view", "chain_items", "envelope_box", "frame_pose", "lab_pose_of_component", "plan_findings",
           "pose_of", "project_findings", "resolved_chains"]
