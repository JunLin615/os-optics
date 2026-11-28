"""Edit operations of a plan revision (DESIGN 1.3.8): ``apply_ops(base, project, ops, context)`` -> the next revision's
decisions, its Project and one result per op. Pure: the plan directory, journal and check are ``workflow``'s.

The ops are applied in order on a working copy; any refusal raises ``PLAN_OP_INVALID`` (``details.op_index``,
``details.op_code``: a ``PlanOpErrorCode``) and nothing is kept. Geometry follows the kernel (``chain``):

* an optic's chain is re-placed after every change that moves the optic, swaps its hardware, re-fixes its holder or
  changes the board: the mount from the optic's seat, the post below the mount, the holder under the post axis, the
  insertion depth closing the height. The kernel's one-chain search is asked first with the chain's three identities
  pinned, so the DOF results are the kernel's; when it refuses those identities (an incompatible swap) the hardware is
  re-posed as it is and every retained DOF is NOT_DELIVERED -- the plan check then names the interface that does not fit;
* the holder keeps its fixing hole when the slot still reaches it (``refix`` NEAREST_HOLE: otherwise the nearest free hole
  the slot reaches; KEEP_HOLE: the hole is kept and the check reports the slot travel exceeded);
* an optic move edits the Project (``set_component_pose`` through ``local.project_ops.update_project``) and the result says
  the Project changed (the bound run is then not current: ``check``).

Item ids are never renumbered: a re-placed chain keeps its ids, created items take the next number of their prefix,
retired items are listed in ``retired_item_ids``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any

from .._vendor.shared_contracts.optical_design import ProjectDocumentV2
from .contract import (CUSTOMER_TABLE_SKU, AssemblyPlanContentV1, AssemblyPlanError, AssemblyPlanErrorCode, AssemblyPlanRevisionV1,
                       FrameBasisV1, ItemIdentityKindV1, ItemPrefixV1, ItemRoleV1, MountingV1, OpResultV1, PlanOpErrorCode,
                       PlanOriginV1, RangeKindV1, ROTATION_RANGE_KINDS, SupplyReasonV1, SupplyV1, content_payload, item_id_parts,
                       normalize_numbers)
from .chain import SLOT_TOLERANCE_MM, ChainPlacement, board_holes, hole_xy, place_single_chain, replace_chain
from .derive import _Builder, _pose, _previous
from .interfaces import board_view, chain_items, frame_pose, lab_pose_of_component, pose_of
from .kernel import ChainPins, KBoard, KHolder, KMount, KPost, table_board
from .kernel.assemble import board_pose, chain_bodies
from .kernel.geometry import Pose, overlap_depth, plan_bounds, q_conj, q_mul, rotate, snap_quat, snap_vec, sub
from .request import chain_optic, classify

#: Hardware role -> the mechanical kind its identity has.
ROLE_KIND = {ItemRoleV1.MOUNT: "MOUNT", ItemRoleV1.POST: "POST", ItemRoleV1.POST_HOLDER: "POST_HOLDER",
             ItemRoleV1.BREADBOARD: "BREADBOARD"}
HARDWARE_ROLES = (ItemRoleV1.MOUNT, ItemRoleV1.POST, ItemRoleV1.POST_HOLDER)
HARDWARE_PREFIX = {ItemRoleV1.MOUNT: ItemPrefixV1.MNT, ItemRoleV1.POST: ItemPrefixV1.PST, ItemRoleV1.POST_HOLDER: ItemPrefixV1.HLD}
CUSTOMER_TABLE_THICKNESS_MM = 12.7
#: Two chains' bodies closer than this penetration (mm) clear each other when a holder is re-fixed.
CLEARANCE_TOLERANCE_MM = 1e-6
CUSTOMER_TABLE_NAME = "Customer optical table"
#: Rotation that takes local +Z onto the lab axis of a declared cylinder.
_SQRT_HALF = math.sqrt(0.5)
CYLINDER_AXIS_ROTATION = {"x": (_SQRT_HALF, 0.0, _SQRT_HALF, 0.0), "y": (_SQRT_HALF, -_SQRT_HALF, 0.0, 0.0),
                          "z": (1.0, 0.0, 0.0, 0.0)}
_AXIS_INDEX = {"x": 0, "y": 1, "z": 2}


def op_error(index: int, code: PlanOpErrorCode, message: str, **details: Any) -> AssemblyPlanError:
    return AssemblyPlanError(AssemblyPlanErrorCode.PLAN_OP_INVALID, message, {"op_index": index, "op_code": code.value, **details})


def _purchase(supply: SupplyV1) -> dict[str, Any]:
    selected = supply is SupplyV1.OPENSCI
    return {"selected": selected, "quantity": 1 if selected else 0, "spare_units": 0}


@dataclass
class EditOutcome:
    """What ``apply_ops`` produced: the next decisions (a payload), the Project, the op results, the items the ops
    touched or retired, the findings the ops' own searches made (source DERIVE) and the USER documents to copy."""

    content: dict[str, Any]
    project: ProjectDocumentV2
    results: list[OpResultV1]
    touched: set[str] = field(default_factory=set)
    retired: set[str] = field(default_factory=set)
    derive_findings: list[Any] = field(default_factory=list)
    project_changed: bool = False
    origin: PlanOriginV1 = PlanOriginV1.EDIT


class _Edit:
    """The working copy of one edit call."""

    def __init__(self, base: AssemblyPlanContentV1, project: ProjectDocumentV2, context: Any, revisions: Any) -> None:
        self.data: dict[str, Any] = content_payload(base.payload())
        self.project = project
        self.context = context if context.project.content_hash() == project.content_hash() else context.with_project(project)
        self.revisions = revisions
        self.base = base
        self.touched: set[str] = set()
        self.retired: set[str] = set()
        self.derive_findings: list[Any] = []
        self.project_changed = False
        self.index = 0

    # ------------------------------------------------------------------------------------------- views

    @property
    def model(self) -> AssemblyPlanContentV1:
        return AssemblyPlanContentV1.model_validate(normalize_numbers(self.data))

    def fail(self, code: PlanOpErrorCode, message: str, **details: Any) -> AssemblyPlanError:
        return op_error(self.index, code, message, **details)

    def item(self, item_id: str, *roles: ItemRoleV1) -> Any:
        model = self.model
        found = model.item(item_id)
        if found is None:
            raise self.fail(PlanOpErrorCode.ITEM_UNKNOWN, f"the plan has no item {item_id}", item_id=item_id)
        if roles and found.role not in roles:
            raise self.fail(PlanOpErrorCode.ROLE_MISMATCH, f"{item_id} is a {found.role.value}, not a "
                            + " or ".join(r.value for r in roles), item_id=item_id, role=found.role.value)
        return found

    def item_dict(self, item_id: str) -> dict[str, Any]:
        return next(i for i in self.data["items"] if i["item_id"] == item_id)

    def classified(self, instance_id: str) -> Any:
        customer = tuple(self.data.get("derive_options", {}).get("customer_owned", ()))
        return next(c for c in classify(self.project, self.context.catalog, frozenset(customer)) if c.instance_id == instance_id)

    def component(self, instance_id: str) -> Any:
        found = self.context.component(instance_id)
        if found is None:
            raise self.fail(PlanOpErrorCode.ITEM_UNKNOWN, f"the Project has no component {instance_id}", instance_id=instance_id)
        return found

    def board(self) -> KBoard:
        board = board_view(self.model, self.context)
        if board is None:
            raise AssemblyPlanError(AssemblyPlanErrorCode.MECHANICAL_CATALOG_NOT_AVAILABLE,
                                    "the plan's board is not in the mechanical documents in use", {"op_index": self.index})
        return board

    def hardware_view(self, sku: str | None, role: ItemRoleV1) -> Any:
        view = None if sku is None else self.context.kcatalog.member(sku)
        expected = {ItemRoleV1.MOUNT: KMount, ItemRoleV1.POST: KPost, ItemRoleV1.POST_HOLDER: KHolder, ItemRoleV1.BREADBOARD: KBoard}[role]
        return view if isinstance(view, expected) else None

    def occupied(self, except_holder: str | None) -> set[tuple[int, int]]:
        holes: set[tuple[int, int]] = set()
        for item in self.model.items:
            if item.role is ItemRoleV1.POST_HOLDER and item.item_id != except_holder and item.placement is not None \
                    and item.placement.fixation is not None:
                holes |= {tuple(h) for h in item.placement.fixation.board_holes}  # type: ignore[misc]
        return holes

    def new_id(self, prefix: ItemPrefixV1) -> str:
        counters = self.data["id_counters"]
        counters[prefix.value] = int(counters.get(prefix.value, 0)) + 1
        return f"{prefix.value}-{counters[prefix.value]}"

    def next_order(self) -> int:
        return max((i["placement"]["assembly_order"] for i in self.data["items"] if i.get("placement")), default=1) + 1

    # ------------------------------------------------------------------------------------------- frame

    def frame(self) -> Pose:
        return frame_pose(self.model)

    def lab_of(self, instance_id: str) -> Pose:
        pose = lab_pose_of_component(self.model, self.component(instance_id))
        return Pose(snap_vec(pose.position), snap_quat(pose.rotation))

    def design_of(self, lab: Pose) -> Pose:
        frame = self.frame()
        inverse = q_conj(frame.rotation)
        return Pose(rotate(inverse, sub(lab.position, frame.position)), q_mul(inverse, lab.rotation))

    # ------------------------------------------------------------------------------------------- items

    def replace_items(self, built: list[dict[str, Any]], links: list[dict[str, Any]] | None, chain_ids: set[str]) -> None:
        orders = {i["item_id"]: i["placement"]["assembly_order"] for i in self.data["items"] if i.get("placement")}
        placed = [item for item in built if item.get("placement")]
        if all(item["item_id"] in orders for item in placed):  # the same parts again: their assembly order is kept
            for item in placed:
                item["placement"]["assembly_order"] = orders[item["item_id"]]
        else:  # new parts: the built order (board first, each part after its support), after every other part
            fresh = self.next_order()
            for item in sorted(placed, key=lambda i: i["placement"]["assembly_order"]):
                item["placement"]["assembly_order"] = fresh
                fresh += 1
        ids = {i["item_id"] for i in built}
        self.data["items"] = sorted([i for i in self.data["items"] if i["item_id"] not in ids] + built, key=lambda i: i["item_id"])
        if links is not None:
            kept = [link for link in self.data["links"] if link["a_item_id"] not in chain_ids]
            self.data["links"] = sorted(kept + links, key=lambda link: link["link_id"])
        self.touched |= ids

    def retire(self, item_ids: set[str]) -> None:
        self.data["items"] = [i for i in self.data["items"] if i["item_id"] not in item_ids]
        self.data["links"] = [link for link in self.data["links"]
                              if link["a_item_id"] not in item_ids and link["b_item_id"] not in item_ids]
        self.data["retired_item_ids"] = sorted(set(self.data.get("retired_item_ids", ())) | item_ids)
        self.retired |= item_ids

    def builder(self) -> _Builder:
        model = self.model
        options = model.derive_options
        b = _Builder(self.project, self.context.catalog, self.context.kcatalog, options, [], _previous(model))
        b.board = self.board()
        b.board_id = model.board_item_id
        frame = self.frame()
        b.yaw, b.translation = frame.rotation, frame.position
        b.design_ids = {i.design_instance_id: i.item_id for i in model.items if i.design_instance_id is not None}
        b.hardware_ids = {}
        b.mount_of_instance = {}
        for item in model.items:
            if item.role is ItemRoleV1.OPTIC and item.mounting is MountingV1.CHAIN:
                mount = chain_items(model, item.item_id).get(ItemRoleV1.MOUNT)
                if mount is not None and mount.placement is not None:
                    b.mount_of_instance[item.design_instance_id] = mount.item_id
        b.order_counter = self.next_order()
        return b

    def build_chain(self, optic: Any, placement: ChainPlacement, hardware_ids: dict[ItemRoleV1, str], *,
                    pinned: set[str] = frozenset()) -> None:  # type: ignore[assignment]
        """Write an optic's chain from a placement (the builder's rules), keeping its supply, purchase and pins."""

        instance = optic.design_instance_id
        c = replace(self.classified(instance), mounting=MountingV1.CHAIN, supply=optic.supply, supply_reason=optic.supply_reason)
        if c.mechanics is None:
            raise self.fail(PlanOpErrorCode.KIND_MISMATCH, f"{optic.item_id}: the optic has no mechanics a mount can hold")
        b = self.builder()
        b.classified = {instance: c}
        b.hardware_ids = {instance: hardware_ids}
        b.design_ids[instance] = optic.item_id
        b._chain(placement, c, self.component(instance))
        old = {i.item_id: i for i in self.model.items}
        for item in b.items:
            before = old.get(item["item_id"])
            if before is not None and before.identity.opensci_sku == item["identity"].get("opensci_sku"):
                item["pinned"] = bool(before.pinned)
                if item["supply"] == before.supply.value:
                    item["purchase"] = before.purchase.model_dump(mode="json")
            if item["item_id"] in pinned:
                item["pinned"] = True
        optic_item = next(i for i in b.items if i["item_id"] == optic.item_id)
        optic_item["supply"] = optic.supply.value
        optic_item.pop("supply_reason", None)
        if optic.supply_reason is not None:
            optic_item["supply_reason"] = optic.supply_reason.value
        optic_item["purchase"] = optic.purchase.model_dump(mode="json")
        for declared in optic_item.get("placement", {}).get("ranges", ()):
            declared["axis_lab"] = list(_unit_axis(declared["axis_lab"]))
        component = self.component(instance)
        dofs = optic_item.get("placement", {}).get("dofs")
        if dofs is not None:
            present = {d["axis"] for d in dofs}
            for retained in component.retained_dofs:
                if retained.axis.value not in present:
                    dofs.append({"axis": retained.axis.value, "requested_control": retained.control.value, "status": "NOT_DELIVERED",
                                 "mechanism": "NONE", "not_delivered_reason": "NO_COMPATIBLE_HARDWARE"})
            dofs.sort(key=lambda d: ("Tx", "Ty", "Tz", "Rx", "Ry", "Rz").index(d["axis"]))
        for link in b.links:  # a post lifted out of its holder: the link states no negative depth (the check reports it)
            interface = link.get("interface") or {}
            if interface.get("depth_mm") is not None and interface["depth_mm"] < 0.0:
                interface["depth_mm"] = 0.0
        chain_ids = {optic.item_id, *hardware_ids.values()}
        self.replace_items(b.items, b.links, chain_ids)
        if not placement.resolved:
            self.derive_findings += b.findings

    def place_chain(self, optic: Any, lab: Pose, views: tuple[Any, Any, Any] | None, *, hole: tuple[int, int] | None,
                    holder_yaw: Any = None, nearest: bool = True) -> ChainPlacement:
        """The chain of ``optic`` at ``lab`` with ``views`` (mount, post, holder): the kernel's pinned one-chain search for
        its DOF results, re-posed with the kept hole (``nearest``: the nearest free hole when the kept one is off the
        slot)."""

        instance = optic.design_instance_id
        component = self.component(instance)
        product = self.context.product(optic.identity.opensci_sku)
        mechanics = self.context.optic_mechanics(optic)
        if mechanics is None or product is None:
            raise self.fail(PlanOpErrorCode.KIND_MISMATCH, f"{optic.item_id}: the optic has no mechanics a mount can hold")
        board = self.board()
        holder_item = chain_items(self.model, optic.item_id).get(ItemRoleV1.POST_HOLDER)
        occupied = self.occupied(None if holder_item is None else holder_item.item_id)
        dofs: tuple[Any, ...] = ()
        if views is not None:
            mount, post, holder = views
            try:
                request = chain_optic(component, product, ChainPins(mount=mount.identity, post=post.identity, holder=holder.identity))
                found = place_single_chain(request, lab, self.context.kcatalog, board, occupied, request.pins)
            except ValueError:
                found = None
            if found is not None and found.resolved and (found.mount.identity, found.post.identity, found.holder.identity) == (  # type: ignore[union-attr]
                    mount.identity, post.identity, holder.identity):
                dofs = found.dofs
            if hole is not None:
                placed = replace_chain(instance, mechanics, lab, mount, post, holder, board, hole=hole, holder_yaw=holder_yaw,
                                       occupied_holes=occupied, dofs=dofs)
                if placed.fixation_problem is None or not nearest:
                    return placed
            return self.refix(optic, instance, mechanics, lab, views, board, occupied, dofs)
        request = chain_optic(component, product)
        return place_single_chain(request, lab, self.context.kcatalog, board, occupied, None)

    def other_bodies(self, optic_id: str) -> list[Any]:
        """The collision bodies of every other resolved chain of the plan."""

        from .interfaces import ResolvedChain

        bodies: list[Any] = []
        model = self.model
        for other in model.items:
            if other.role is not ItemRoleV1.OPTIC or other.item_id == optic_id or other.mounting is not MountingV1.CHAIN:
                continue
            chain = ResolvedChain(other, chain_items(model, other.item_id), self.context)
            poses = chain.poses()
            if poses is not None:
                bodies += [b for bs in chain_bodies(chain.mount_view, chain.post_view, chain.holder_view, poses).values() for b in bs]  # type: ignore[arg-type]
        return bodies

    def refix(self, optic: Any, instance: str, mechanics: Any, lab: Pose, views: tuple[Any, Any, Any], board: KBoard,
              occupied: set[tuple[int, int]], dofs: tuple[Any, ...]) -> ChainPlacement:
        """The chain fixed on the nearest free hole its slot reaches whose turned fork clears every other chain (the
        nearest free hole when none does; no hole when the slot reaches none)."""

        mount, post, holder = views
        others = self.other_bodies(optic.item_id)
        first = replace_chain(instance, mechanics, lab, mount, post, holder, board, occupied_holes=occupied, dofs=dofs)
        if first.hole is None or not others:
            return first
        axis = (first.poses.mount.position[0], first.poses.mount.position[1])  # type: ignore[union-attr]
        candidates = []
        for hole in board_holes(board):
            if hole in occupied:
                continue
            x, y = hole_xy(board, *hole)
            distance = math.hypot(x - axis[0], y - axis[1])
            if holder.base.slot_start_mm - SLOT_TOLERANCE_MM <= distance <= holder.base.slot_end_mm + SLOT_TOLERANCE_MM:
                candidates.append((distance, hole))
        for _, hole in sorted(candidates):
            placed = replace_chain(instance, mechanics, lab, mount, post, holder, board, hole=hole, occupied_holes=occupied, dofs=dofs)
            if placed.fixation_problem is not None or placed.poses is None:
                continue
            own = [b for bs in chain_bodies(mount, post, holder, placed.poses).values() for b in bs]
            if all(overlap_depth(a, b) <= CLEARANCE_TOLERANCE_MM for a in own for b in others):
                return placed
        return first

    def current_views(self, optic: Any) -> tuple[tuple[Any, Any, Any] | None, dict[ItemRoleV1, Any]]:
        hardware = chain_items(self.model, optic.item_id)
        views = tuple(self.hardware_view(hardware[r].identity.opensci_sku if r in hardware else None, r) for r in HARDWARE_ROLES)
        complete = all(v is not None for v in views) and all(hardware[r].placement is not None for r in HARDWARE_ROLES)
        return (views if complete else None), hardware  # type: ignore[return-value]

    def replace_optic_chain(self, optic: Any, lab: Pose, *, keep_hole: bool = True, nearest: bool = True,
                            views: tuple[Any, Any, Any] | None = None, pinned: set[str] = frozenset()) -> None:  # type: ignore[assignment]
        current, hardware = self.current_views(optic)
        views = views or current
        if views is None or len(hardware) != len(HARDWARE_ROLES):
            item = self.item_dict(optic.item_id)
            item["placement"]["pose_in_lab"] = _pose(lab)
            self.touched.add(optic.item_id)
            return
        holder = hardware[ItemRoleV1.POST_HOLDER]
        hole = None
        if keep_hole and holder.placement is not None and holder.placement.fixation is not None:
            hole = tuple(holder.placement.fixation.board_holes[0])
        placed = self.place_chain(optic, lab, views, hole=hole, nearest=nearest)  # type: ignore[arg-type]
        self.build_chain(optic, placed, {r: hardware[r].item_id for r in HARDWARE_ROLES}, pinned=pinned)

    # ------------------------------------------------------------------------------------------- project

    def set_design_pose(self, instance: str, position: Any, quaternion: Any) -> None:
        from ..local.project_ops import ProjectUpdateError, update_project

        op: dict[str, Any] = {"op": "set_component_pose", "instance_id": instance, "position_mm": [float(v) for v in position]}
        if quaternion is not None:
            op["quaternion_wxyz"] = [float(v) for v in quaternion]
        try:
            update = update_project(self.project, [op], expected_project_hash=self.project.content_hash(), catalog=self.context.catalog)
        except ProjectUpdateError as exc:
            raise AssemblyPlanError(AssemblyPlanErrorCode.PROJECT_REJECTED, exc.message,
                                    {"op_index": self.index, "project_error": exc.as_dict()}) from None
        self.project = ProjectDocumentV2.model_validate(update.project)
        self.context = self.context.with_project(self.project)
        self.project_changed = True
        project_hash = self.project.content_hash()
        self.data["bindings"]["project"]["project_hash"]["digest"] = project_hash


def _unit_axis(axis: Any) -> tuple[float, float, float]:
    """``axis`` scaled to unit length (a declared range's axis; the kernel's snapped components can be off by 1e-9)."""

    norm = math.sqrt(sum(float(c) * float(c) for c in axis))
    return tuple(float(c) / norm for c in axis)  # type: ignore[return-value]


# ------------------------------------------------------------------------------------------------ the ops


def _move(edit: _Edit, optic: Any, design_position: Any, design_quaternion: Any, refix: str) -> None:
    edit.set_design_pose(optic.design_instance_id, design_position, design_quaternion)
    lab = edit.lab_of(optic.design_instance_id)
    if optic.mounting is MountingV1.CHAIN:
        edit.replace_optic_chain(optic, lab, keep_hole=True, nearest=refix == "NEAREST_HOLE")
    else:
        edit.item_dict(optic.item_id)["placement"]["pose_in_lab"] = _pose(lab)
        edit.touched.add(optic.item_id)


def op_move_optic(edit: _Edit, op: Any) -> None:
    optic = edit.item(op.item_id, ItemRoleV1.OPTIC)
    nominal = edit.component(optic.design_instance_id).nominal_pose
    current = [float(v) for v in nominal.position_mm]
    if op.delta_design_mm is not None:
        position = [a + b for a, b in zip(current, op.delta_design_mm, strict=True)]
    else:
        position = list(op.position_design_mm)
    _move(edit, optic, position, op.quaternion_wxyz, op.refix.value)


def op_adjust(edit: _Edit, op: Any) -> None:
    optic = edit.item(op.item_id, ItemRoleV1.OPTIC)
    ranges = {} if optic.placement is None else {r.range_id: r for r in optic.placement.ranges}
    declared = ranges.get(op.range_id)
    if declared is None:
        raise edit.fail(PlanOpErrorCode.RANGE_UNKNOWN, f"{op.item_id} declares no range {op.range_id}", ranges=sorted(ranges))
    if not declared.min <= op.delta <= declared.max:
        raise edit.fail(PlanOpErrorCode.RANGE_EXCEEDED, f"{op.range_id} moves {declared.min:g} to {declared.max:g} "
                        f"{declared.unit.value} from here, not {op.delta:g}", range=[declared.min, declared.max])
    lab = pose_of(optic)
    assert lab is not None
    axis = tuple(declared.axis_lab)
    if declared.kind in ROTATION_RANGE_KINDS:
        half = math.radians(op.delta) / 2.0
        q = (math.cos(half), *(math.sin(half) * a for a in axis))
        pivot = tuple(declared.pivot_lab)  # type: ignore[arg-type]
        position = tuple(p + d for p, d in zip(pivot, rotate(q, sub(lab.position, pivot)), strict=True))  # type: ignore[arg-type]
        moved = Pose(position, q_mul(q, lab.rotation))  # type: ignore[arg-type]
    else:
        moved = Pose(tuple(p + op.delta * a for p, a in zip(lab.position, axis, strict=True)), lab.rotation)  # type: ignore[arg-type]
    design = edit.design_of(moved)
    quaternion = None if declared.kind not in ROTATION_RANGE_KINDS else design.rotation
    _move(edit, optic, design.position, quaternion, "NEAREST_HOLE" if declared.kind is not RangeKindV1.SLOT_TRAVEL else "KEEP_HOLE")


def _optic_of_hardware(edit: _Edit, item_id: str) -> Any:
    model = edit.model
    for optic in model.items:
        if optic.role is ItemRoleV1.OPTIC and any(h.item_id == item_id for h in chain_items(model, optic.item_id).values()):
            return optic
    raise edit.fail(PlanOpErrorCode.ITEM_UNKNOWN, f"{item_id} belongs to no optic's chain")


def op_set_hardware(edit: _Edit, op: Any) -> None:
    item = edit.item(op.item_id, *HARDWARE_ROLES)
    identity = edit.context.mechanical.identity(op.opensci_sku)
    if identity is None:
        raise edit.fail(PlanOpErrorCode.IDENTITY_UNKNOWN, f"{op.opensci_sku} is not in the mechanical documents in use",
                        opensci_sku=op.opensci_sku)
    if identity.kind != ROLE_KIND[item.role]:
        raise edit.fail(PlanOpErrorCode.KIND_MISMATCH, f"{op.opensci_sku} is a {identity.kind}, not a {ROLE_KIND[item.role]}",
                        opensci_sku=op.opensci_sku)
    optic = _optic_of_hardware(edit, item.item_id)
    current, hardware = edit.current_views(optic)
    views = list(edit.hardware_view(hardware[r].identity.opensci_sku if r in hardware else None, r) for r in HARDWARE_ROLES)
    views[HARDWARE_ROLES.index(item.role)] = edit.hardware_view(op.opensci_sku, item.role)
    if any(v is None for v in views) or len(hardware) != len(HARDWARE_ROLES):
        raise edit.fail(PlanOpErrorCode.KIND_MISMATCH, f"the chain of {optic.item_id} has unresolved hardware: use rederive_chain "
                        "(or set_mounting CHAIN) to select it", optic=optic.item_id)
    lab = pose_of(optic)
    assert lab is not None
    holder = hardware[ItemRoleV1.POST_HOLDER]
    hole = tuple(holder.placement.fixation.board_holes[0]) if holder.placement and holder.placement.fixation else None
    placed = edit.place_chain(optic, lab, tuple(views), hole=hole, nearest=False)  # type: ignore[arg-type]
    edit.build_chain(optic, placed, {r: hardware[r].item_id for r in HARDWARE_ROLES}, pinned={item.item_id})


def op_set_fixation(edit: _Edit, op: Any) -> None:
    holder = edit.item(op.item_id, ItemRoleV1.POST_HOLDER)
    optic = _optic_of_hardware(edit, holder.item_id)
    views, hardware = edit.current_views(optic)
    if views is None:
        raise edit.fail(PlanOpErrorCode.KIND_MISMATCH, f"the chain of {optic.item_id} has unresolved hardware")
    yaw = None
    if op.base_yaw_deg is not None:
        half = math.radians(op.base_yaw_deg) / 2.0
        yaw = (math.cos(half), 0.0, 0.0, math.sin(half))
    lab = pose_of(optic)
    assert lab is not None
    placed = edit.place_chain(optic, lab, views, hole=tuple(op.hole), holder_yaw=yaw, nearest=False)  # type: ignore[arg-type]
    edit.build_chain(optic, placed, {r: hardware[r].item_id for r in HARDWARE_ROLES})


def _footprint(edit: _Edit) -> tuple[float, float, float, float]:
    model = edit.model
    boxes = []
    for optic in model.items:
        if optic.role is ItemRoleV1.OPTIC and optic.mounting is MountingV1.CHAIN:
            views, hardware = edit.current_views(optic)
            if views is None:
                continue
            from .interfaces import ResolvedChain

            chain = ResolvedChain(optic, hardware, edit.context)
            poses = chain.poses()
            if poses is not None:
                boxes += [b for bs in chain_bodies(*views, poses).values() for b in bs]
    points = [pose_of(i).position for i in model.items if i.is_anchor and i.placement is not None]  # type: ignore[union-attr]
    if boxes:
        (x0, y0), (x1, y1) = plan_bounds(boxes)
        xs, ys = [x0, x1] + [p[0] for p in points], [y0, y1] + [p[1] for p in points]
    else:
        xs, ys = [p[0] for p in points] or [0.0], [p[1] for p in points] or [0.0]
    return min(xs), min(ys), max(xs), max(ys)


def op_set_board(edit: _Edit, op: Any) -> None:
    board_item = edit.item_dict(edit.data["board_item_id"])
    if op.customer_table is not None:
        table = op.customer_table
        new = table_board(CUSTOMER_TABLE_SKU, size_x_mm=table.size_x_mm, size_y_mm=table.size_y_mm, pitch_mm=table.pitch_mm,
                          edge_offset_mm=table.edge_offset_mm, thread=table.thread, thickness_mm=CUSTOMER_TABLE_THICKNESS_MM)
        edit.data["derive_options"]["customer_table"] = table.model_dump(mode="json")
        board_item.update(identity={"kind": "CUSTOMER_TABLE", "opensci_sku": CUSTOMER_TABLE_SKU, "display_name": CUSTOMER_TABLE_NAME},
                          supply="CUSTOMER_OWNED", supply_reason="CUSTOMER_TABLE", purchase=_purchase(SupplyV1.CUSTOMER_OWNED))
    else:
        identity = edit.context.mechanical.identity(op.opensci_sku)
        view = edit.hardware_view(op.opensci_sku, ItemRoleV1.BREADBOARD)
        if identity is None or view is None:
            raise edit.fail(PlanOpErrorCode.IDENTITY_UNKNOWN if identity is None else PlanOpErrorCode.KIND_MISMATCH,
                            f"{op.opensci_sku} is not a breadboard of the mechanical documents in use", opensci_sku=op.opensci_sku)
        new = view
        edit.data["derive_options"].pop("customer_table", None)
        user = edit.context.mechanical.publisher_of(op.opensci_sku) == "USER"
        supply = SupplyV1.CUSTOMER_OWNED if user else SupplyV1.OPENSCI
        board_item.update(identity={"kind": "USER_MECH" if user else "OS_MECH", "opensci_sku": op.opensci_sku,
                                    "display_name": view.display_name or op.opensci_sku},
                          supply=supply.value, purchase=_purchase(supply))
        board_item.pop("supply_reason", None)
        if user:
            board_item["supply_reason"] = SupplyReasonV1.USER_HARDWARE.value
    board_item["placement"]["pose_in_lab"] = _pose(board_pose(new))
    board_item["pinned"] = True
    edit.touched.add(board_item["item_id"])
    if op.recentre:
        x0, y0, x1, y1 = _footprint(edit)
        shift = (new.size_x_mm / 2.0 - (x0 + x1) / 2.0, new.size_y_mm / 2.0 - (y0 + y1) / 2.0)
        frame = edit.data["frame"]
        frame["translation_mm"] = [frame["translation_mm"][0] + shift[0], frame["translation_mm"][1] + shift[1], frame["translation_mm"][2]]
        frame["basis"] = FrameBasisV1.EDITED.value
    _replace_everything(edit)


def _replace_everything(edit: _Edit) -> None:
    """Every item re-placed for the current frame and board: anchors at their design poses, chains re-fixed."""

    for item in edit.model.items:
        if item.design_instance_id is None or item.placement is None:
            continue
        lab = edit.lab_of(item.design_instance_id)
        if item.role is ItemRoleV1.OPTIC and item.mounting is MountingV1.CHAIN:
            continue
        edit.item_dict(item.item_id)["placement"]["pose_in_lab"] = _pose(lab)
        edit.touched.add(item.item_id)
    for holder in [i for i in edit.model.items if i.role is ItemRoleV1.POST_HOLDER and i.placement is not None]:
        edit.item_dict(holder.item_id)["placement"].pop("fixation", None)
    for optic in [i for i in edit.model.items if i.role is ItemRoleV1.OPTIC and i.mounting is MountingV1.CHAIN]:
        edit.replace_optic_chain(optic, edit.lab_of(optic.design_instance_id), keep_hole=False)


def op_set_supply(edit: _Edit, op: Any) -> None:
    item = edit.item(op.item_id)
    data = edit.item_dict(item.item_id)
    if op.supply == "OPENSCI":
        if item.identity.kind not in (ItemIdentityKindV1.OS_OPT, ItemIdentityKindV1.OS_MECH):
            raise edit.fail(PlanOpErrorCode.SUPPLY_NOT_ALLOWED, f"OpenSci does not supply {item.item_id} ({item.identity.kind.value})")
        reason = None
        if item.role is ItemRoleV1.OPTIC:
            c = edit.classified(item.design_instance_id)
            if c.supply is SupplyV1.UNRESOLVED:
                raise edit.fail(PlanOpErrorCode.SUPPLY_NOT_ALLOWED, f"{item.identity.opensci_sku} is not orderable from OpenSci")
            if c.supply_reason is SupplyReasonV1.OPENSCI_SUPPLIED_UNMOUNTED and item.mounting is MountingV1.ANCHOR:
                reason = SupplyReasonV1.OPENSCI_SUPPLIED_UNMOUNTED.value
        data["supply"] = "OPENSCI"
        data.pop("supply_reason", None)
        if reason is not None:
            data["supply_reason"] = reason
        data["purchase"] = _purchase(SupplyV1.OPENSCI)
    else:
        if item.identity.kind not in (ItemIdentityKindV1.OS_OPT, ItemIdentityKindV1.OS_MECH):
            raise edit.fail(PlanOpErrorCode.SUPPLY_NOT_ALLOWED, f"{item.item_id} is already the customer's ({item.identity.kind.value})")
        data.update(supply="CUSTOMER_OWNED", supply_reason="CUSTOMER_OWNED", purchase=_purchase(SupplyV1.CUSTOMER_OWNED))
    edit.touched.add(item.item_id)


def op_set_purchase(edit: _Edit, op: Any) -> None:
    item = edit.item(op.item_id)
    if op.selected and item.supply is not SupplyV1.OPENSCI:
        raise edit.fail(PlanOpErrorCode.SUPPLY_NOT_ALLOWED, f"{item.item_id} is not supplied by OpenSci ({item.supply.value}): "
                        "set_supply OPENSCI first")
    if op.selected:
        quantity = op.quantity if op.quantity is not None else (item.purchase.quantity if item.purchase.selected else item.quantity)
        spare = op.spare_units if op.spare_units is not None else (item.purchase.spare_units if item.purchase.selected else 0)
        purchase = {"selected": True, "quantity": quantity, "spare_units": spare}
    else:
        purchase = {"selected": False, "quantity": 0, "spare_units": 0}
    edit.item_dict(item.item_id)["purchase"] = purchase
    edit.touched.add(item.item_id)


def op_set_mounting(edit: _Edit, op: Any) -> None:
    optic = edit.item(op.item_id, ItemRoleV1.OPTIC)
    if op.mounting is optic.mounting:
        return
    instance = optic.design_instance_id
    if op.mounting is MountingV1.ANCHOR:
        hardware = chain_items(edit.model, optic.item_id)
        data = edit.item_dict(optic.item_id)
        data["mounting"] = MountingV1.ANCHOR.value
        data["placement"] = {"pose_in_lab": data["placement"]["pose_in_lab"], "assembly_order": data["placement"]["assembly_order"]}
        edit.retire({h.item_id for h in hardware.values()})
        c = replace(edit.classified(instance), mounting=MountingV1.ANCHOR, supply=optic.supply, supply_reason=optic.supply_reason)
        if optic.supply is SupplyV1.OPENSCI and c.supply_reason is SupplyReasonV1.OPENSCI_SUPPLIED_UNMOUNTED:
            c = replace(c, supply_reason=SupplyReasonV1.OPENSCI_SUPPLIED_UNMOUNTED)
        b = edit.builder()
        b.classified = {instance: c}
        b._anchor(c, edit.component(instance).retained_dofs)
        for item in b.items:
            item["purchase"] = optic.purchase.model_dump(mode="json")
            item["pinned"] = optic.pinned
        edit.replace_items(b.items, None, {optic.item_id})
        return
    if edit.context.optic_mechanics(optic) is None:
        raise edit.fail(PlanOpErrorCode.KIND_MISMATCH, f"{optic.item_id}: the optic has no mechanics a mount can hold")
    reason = None if optic.supply_reason is SupplyReasonV1.OPENSCI_SUPPLIED_UNMOUNTED else optic.supply_reason
    model_optic = optic.model_copy(update={"mounting": MountingV1.CHAIN, "supply_reason": reason})
    ids = {role: edit.new_id(HARDWARE_PREFIX[role]) for role in HARDWARE_ROLES}
    placed = edit.place_chain(model_optic, edit.lab_of(instance), None, hole=None)
    edit.build_chain(model_optic, placed, ids)


def op_rederive_chain(edit: _Edit, op: Any) -> None:
    optic = edit.item(op.item_id, ItemRoleV1.OPTIC)
    if optic.mounting is not MountingV1.CHAIN:
        raise edit.fail(PlanOpErrorCode.ROLE_MISMATCH, f"{optic.item_id} is an ANCHOR: use set_mounting CHAIN")
    hardware = chain_items(edit.model, optic.item_id)
    unknown = [k for k in op.keep if k not in {h.item_id for h in hardware.values()}]
    if unknown:
        raise edit.fail(PlanOpErrorCode.ITEM_UNKNOWN, f"keep names items outside the chain of {optic.item_id}: {', '.join(unknown)}")
    pins = {role: hardware[role].identity.opensci_sku for role in HARDWARE_ROLES if role in hardware and hardware[role].item_id in op.keep}
    instance = optic.design_instance_id
    product = edit.context.product(optic.identity.opensci_sku)
    request = chain_optic(edit.component(instance), product,
                          ChainPins(mount=pins.get(ItemRoleV1.MOUNT), post=pins.get(ItemRoleV1.POST), holder=pins.get(ItemRoleV1.POST_HOLDER)))
    holder = hardware.get(ItemRoleV1.POST_HOLDER)
    occupied = edit.occupied(None if holder is None else holder.item_id)
    placed = place_single_chain(request, edit.lab_of(instance), edit.context.kcatalog, edit.board(), occupied, request.pins)
    ids = {role: hardware[role].item_id if role in hardware else edit.new_id(HARDWARE_PREFIX[role]) for role in HARDWARE_ROLES}
    edit.build_chain(optic, placed, ids, pinned={hardware[r].item_id for r in pins})


def op_pin(edit: _Edit, op: Any) -> None:
    item = edit.item(op.item_id)
    edit.item_dict(item.item_id)["pinned"] = op.pinned
    edit.touched.add(item.item_id)


def op_rebind_catalogs(edit: _Edit, op: Any) -> None:
    bindings = edit.data["bindings"]
    bindings["mechanical"] = [r.model_dump(mode="json") for r in edit.context.mechanical.binding()]
    version = getattr(edit.context.catalog, "catalog_version", None)
    if version is not None:
        bindings["optical_catalog"]["catalog_version"] = version


def op_declare_envelope(edit: _Edit, op: Any) -> None:
    model = edit.model
    if op.item_id is not None:
        if model.item(op.item_id) is not None or op.item_id in model.retired_item_ids:
            raise edit.fail(PlanOpErrorCode.ITEM_ID_TAKEN, f"{op.item_id} is already used in this plan", item_id=op.item_id)
        number = item_id_parts(op.item_id)[1]  # type: ignore[index]
        counters = edit.data["id_counters"]
        counters["env"] = max(int(counters.get("env", 0)), number)
        item_id = op.item_id
    else:
        item_id = edit.new_id(ItemPrefixV1.ENV)
    envelope = op.envelope
    if envelope.shape == "BOX":
        centre = tuple((lo + hi) / 2.0 for lo, hi in zip(envelope.min_mm, envelope.max_mm, strict=True))
        half = tuple((hi - lo) / 2.0 for lo, hi in zip(envelope.min_mm, envelope.max_mm, strict=True))
        pose = Pose(centre, (1.0, 0.0, 0.0, 0.0))  # type: ignore[arg-type]
        local = {"shape": "BOX", "min_mm": [-h for h in half], "max_mm": list(half)}
    else:
        index = _AXIS_INDEX[envelope.axis]
        mid = (envelope.z_min_mm + envelope.z_max_mm) / 2.0
        others = iter(envelope.centre_mm)
        centre = tuple(mid if k == index else next(others) for k in range(3))
        pose = Pose(centre, CYLINDER_AXIS_ROTATION[envelope.axis])  # type: ignore[arg-type]
        length = envelope.z_max_mm - envelope.z_min_mm
        local = {"shape": "CYLINDER", "diameter_mm": envelope.diameter_mm, "z_min_mm": -length / 2.0, "z_max_mm": length / 2.0}
    item = {"item_id": item_id, "role": "DECLARED_ENVELOPE",
            "identity": {"kind": "CUSTOMER_PART", "display_name": op.label}, "supply": "CUSTOMER_OWNED",
            "supply_reason": "NOT_IN_CATALOG", "purchase": _purchase(SupplyV1.CUSTOMER_OWNED),
            "placement": {"pose_in_lab": _pose(pose), "assembly_order": edit.next_order()},
            "envelope": {**local, "basis": "USER_DECLARED", "reserve_column": op.reserve_column}}
    edit.data["items"] = sorted(edit.data["items"] + [item], key=lambda i: i["item_id"])
    edit.touched.add(item_id)


def op_remove_envelope(edit: _Edit, op: Any) -> None:
    item = edit.item(op.item_id, ItemRoleV1.DECLARED_ENVELOPE)
    edit.retire({item.item_id})


def op_revert_to(edit: _Edit, op: Any) -> None:
    if edit.revisions is None:
        raise edit.fail(PlanOpErrorCode.REVISION_UNKNOWN, "revert_to needs the plan directory")
    try:
        target, project = edit.revisions(op.revision)
    except AssemblyPlanError as exc:
        raise edit.fail(PlanOpErrorCode.REVISION_UNKNOWN, exc.message, revision=op.revision) from None
    current = edit.data
    restored = target.payload()
    counters = {k: max(int(current["id_counters"].get(k, 0)), int(restored["id_counters"].get(k, 0)))
                for k in {*current["id_counters"], *restored["id_counters"]}}
    target_ids = {i["item_id"] for i in restored["items"]}
    retired = (set(current.get("retired_item_ids", ())) | {i["item_id"] for i in current["items"]}) - target_ids
    for key in ("bindings", "frame", "board_item_id", "items", "links", "derive_options"):
        current[key] = restored[key] if key in restored else current.get(key)
    current["id_counters"] = counters
    current["retired_item_ids"] = sorted(retired)
    edit.touched |= target_ids | {i for i in retired}
    if project.content_hash() != edit.project.content_hash():
        edit.project_changed = True
    edit.project = project
    edit.context = edit.context.with_project(project)


OPS = {"move_optic": op_move_optic, "adjust": op_adjust, "set_hardware": op_set_hardware, "set_fixation": op_set_fixation,
       "set_board": op_set_board, "set_supply": op_set_supply, "set_purchase": op_set_purchase, "set_mounting": op_set_mounting,
       "rederive_chain": op_rederive_chain, "pin": op_pin, "rebind_catalogs": op_rebind_catalogs,
       "declare_envelope": op_declare_envelope, "remove_envelope": op_remove_envelope, "revert_to": op_revert_to}


def apply_ops(base: AssemblyPlanContentV1 | AssemblyPlanRevisionV1, project: ProjectDocumentV2, ops: Any, context: Any, *,
              revisions: Any = None) -> EditOutcome:
    """Apply ``ops`` (``AssemblyPlanOpV1`` models) to ``base`` (module docstring). ``revisions(n)`` returns
    ``(revision, project)`` of revision ``n`` of the plan (for ``revert_to``). The outcome's ``content`` is the next
    revision's decisions without ``revision`` / ``parent_digest`` / ``origin`` set (the caller sets them)."""

    edit = _Edit(base, project, context, revisions)
    results: list[OpResultV1] = []
    origin = PlanOriginV1.EDIT
    for index, op in enumerate(ops):
        edit.index = index
        before_ids = {i["item_id"] for i in edit.data["items"]}
        touched_before, retired_before = set(edit.touched), set(edit.retired)
        changed_project = edit.project_changed
        edit.project_changed = False
        OPS[op.op](edit, op)
        try:
            edit.model  # (every op leaves valid decisions)
        except ValueError as exc:
            raise op_error(index, PlanOpErrorCode.OP_MALFORMED, f"the op leaves an invalid plan: {str(exc)[:MAX_MESSAGE]}") from None
        after_ids = {i["item_id"] for i in edit.data["items"]}
        results.append(OpResultV1(op_index=index, op=op.op,
                                  changed_items=tuple(sorted((edit.touched - touched_before) & before_ids & after_ids)),
                                  retired_items=tuple(sorted(edit.retired - retired_before)),
                                  created_items=tuple(sorted(after_ids - before_ids)), project_changed=edit.project_changed))
        edit.project_changed = edit.project_changed or changed_project
        if op.op == "revert_to":
            origin = PlanOriginV1.REVERT
        elif op.op == "rebind_catalogs" and origin is PlanOriginV1.EDIT and len(ops) == 1:
            origin = PlanOriginV1.REBIND
    return EditOutcome(content=edit.data, project=edit.project, results=results, touched=edit.touched, retired=edit.retired,
                       derive_findings=edit.derive_findings, project_changed=edit.project_changed, origin=origin)


#: Characters of a validation message an op refusal keeps.
MAX_MESSAGE = 400

__all__ = ["OPS", "EditOutcome", "apply_ops", "op_error"]
