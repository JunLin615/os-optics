"""Derive an assembly plan from a Project: the kernel's whole-design search, or a per-chain candidate when it refuses.

``derive_plan_content(project, catalog, sources, options)`` returns the decision fields of a plan revision
(``contract.CONTENT_FIELDS``, ids allocated, before the number rule) and the findings only the derivation knows (source
DERIVE). Steps:

1. ``request.classify``: every component as a CHAIN optic, an ANCHOR optic or a COMPONENT, with its supply.
2. Each CHAIN optic is checked first: an optical axis that is not horizontal (or a non-round optic without a vertical
   edge) makes it an ANCHOR with ``UNSUPPORTED_OPTIC_ORIENTATION``; no mount of the catalog that holds it makes it an
   ANCHOR with ``MOUNT_NOT_FOUND`` (its near misses in ``details``). A product the kernel has no mechanics for is an
   ANCHOR with ``MISSING_MECHANICAL_DATA``.
3. ``--from`` an earlier plan: items are matched by design instance (an optic's chain hardware by its links): their ids,
   supply, purchase and pins are kept; pinned hardware is kept by the search (``ChainPins``), a pinned board is the only
   board.
4. The board candidates: a customer table (one board of its grid), else the catalog's boards within
   ``max_board_size_mm``.
5. ``kernel.resolve_assembly`` under the work budget (``DeriveBudgetV1``; a time limit is polled through ``run_control``,
   so the kernel itself reads no clock). A realization becomes the plan; each requested DOF it does not deliver is a
   ``DOF_NOT_DELIVERED`` finding (the blocking component of a layout fallback named).
6. A refusal becomes findings on a candidate, never a missing plan: the refusal's finding (``findings.kernel_refusal_code``)
   and ``DERIVE_FELL_BACK`` (a spent budget: ``SEARCH_BUDGET_EXHAUSTED``); the candidate places each chain on its own
   (``chain.place_single_chain``) at the lift the kernel's rule gives (else the preferred beam height), turned by the
   kernel's design yaw, centred on the smallest board that holds its footprint (none within the limit: the largest
   allowed and ``BOARD_TOO_SMALL``), each holder on the nearest free hole its slot reaches (``NO_FIXING_HOLE`` when
   none); a chain no hardware reaches keeps unresolved hardware (``HEIGHT_NOT_REACHABLE`` / ``MOUNT_NOT_FOUND`` /
   ``BEAM_CLIPPED_AT_INCIDENCE``).

Without a time limit the outcome depends on the inputs only: the same Project, catalogs and options give the same
content (and plan digest) on every run.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .. import run_control
from ..run_monitor import RunMonitor
from .._vendor.shared_contracts.optical_design import ProjectDocumentV2
from .._vendor.shared_contracts.optical_design.optical_model import DesignIntentV1
from .contract import (CUSTOMER_TABLE_SKU, DISPLAY_TEXT_MAX, PLAN_SCHEMA_ID, PLAN_SCHEMA_VERSION, AssemblyPlanError, AssemblyPlanErrorCode,
                       ChainLinkKindV1, DeriveOptionsV1, FindingCheckV1, FindingSourceV1, FindingV1, ItemPrefixV1,
                       ItemRoleV1, MountingV1, PlanFindingCodeV1, RangeKindV1, SupplyReasonV1, SupplyV1, derived_plan_id,
                       normalize_numbers)
from .findings import FINDING_RULES, kernel_refusal_code, make_finding
from .chain import ChainPlacement, place_single_chain, replace_chain, slot_offset
from .kernel import (AssemblyRealizationV2, AssemblyResolutionRefusal, ChainPins, KBoard, KernelCatalog, OpticRequest, ResolutionBudget, beam_trace_settings, mount_incompatibility, resolve_assembly, table_board,
                     trace_beam_paths)
from .kernel.assemble import ChainPoses, board_pose, chain_bodies, fixation_thread, interface_designations, link_poses
from .kernel.catalog import FACT_BASES
from .kernel.geometry import IDENTITY, Pose, Quat, Vec3, plan_bounds, q_mul, rotate, snap_quat, snap_vec, yaw_direction
from .kernel import resolve as kernel
from .request import ClassifiedComponent, classify, kernel_request

#: The hash subject of the Project a plan binds (``ProjectBindingV1``).
PROJECT_BINDING_SUBJECT = "project"
#: The thickness given to a customer's optical table (its top is the lab origin plane; the table below it is not modelled).
CUSTOMER_TABLE_THICKNESS_MM = 12.7
#: Decimals of a recorded setting value (mm / deg) and of a yaw in degrees.
SETTING_DECIMALS = 6
#: Display names of plan items without a catalog name.
COMPONENT_NAMES = {SupplyReasonV1.CUSTOMER_LIGHT_SOURCE: "Customer light source", SupplyReasonV1.NOT_IN_CATALOG: "Customer component"}
UNRESOLVED_NAMES = {ItemRoleV1.MOUNT: "Mount (no compatible hardware)", ItemRoleV1.POST: "Post (no compatible hardware)",
                    ItemRoleV1.POST_HOLDER: "Post holder (no compatible hardware)"}
CUSTOMER_TABLE_NAME = "Customer optical table"
#: The kernel's DOF reasons the plan states as one of its own (``DofNotDeliveredReasonV1``).
DOF_REASONS = {"NO_COMPATIBLE_HARDWARE": "NO_COMPATIBLE_HARDWARE", "HARDWARE_DID_NOT_FIT": "HARDWARE_DID_NOT_FIT",
               "SLOT_NOT_ALONG_AXIS": "SLOT_NOT_ALONG_AXIS", "NOT_COMBINABLE_WITH_OTHER_DOFS": "NO_COMPATIBLE_HARDWARE"}
#: The range kinds of each kernel DOF mechanism (a two-axis mechanism names the axis of each).
_MECHANISM_RANGES = {"POST_HEIGHT_CLAMP": RangeKindV1.POST_HEIGHT, "POST_ROTATION_CLAMP": RangeKindV1.POST_ROTATION,
                     "BASE_SLOT_TRAVEL": RangeKindV1.SLOT_TRAVEL, "ROTATION_MOUNT": RangeKindV1.MOUNT_AXIAL_ROTATION}
#: Details of a refusal kept on its finding, in this order, while they fit the finding's bound.
_REFUSAL_DETAIL_KEYS = ("reason", "instance_id", "with", "search_budget_exhausted", "search_bound", "suggestion",
                        "reachable_axis_height_mm", "requested_beam_height_mm", "footprint_mm", "smallest_board_for_footprint_mm",
                        "max_incidence_deg", "clearance_basis")
_DISPLAY = re.compile(r"^[A-Za-z0-9 ,.()/+=\-x]+$")
#: A cosine this close to 1 is parallel (unit directions).
_PARALLEL_COSINE = 1.0 - 1e-9


@dataclass(frozen=True)
class DeriveOutcome:
    """The content of a derived revision (``content``: the decision fields), the findings only the derivation knows
    (source DERIVE), the Project it is bound to, and how the kernel answered."""

    content: dict[str, Any]
    derive_findings: tuple[FindingV1, ...]
    project: Any
    realization: AssemblyRealizationV2 | None = None
    refusal: AssemblyResolutionRefusal | None = None
    placements: tuple[ChainPlacement, ...] = ()


class TimeLimitMonitor(RunMonitor):
    """A run monitor that stops the run at its first checkpoint after ``limit_s`` seconds (the derivation's time limit)."""

    def __init__(self, limit_s: float, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.limit_s = float(limit_s)

    def step(self, count: int) -> None:
        if self.cancel_reason is None and self.clock() - self.started > self.limit_s:
            self.cancel(f"the derivation reached its time limit of {self.limit_s:g} s")
        super().step(count)


def _deadline() -> bool:
    """Whether the run's monitor stops the derivation here (a time limit or a cancellation; no monitor: never)."""

    try:
        run_control.checkpoint()
    except run_control.RunStopped:
        return True
    return False


# ------------------------------------------------------------------------------------------------ ids


class _Ids:
    """Item ids ``<prefix>-<n>``: kept from an earlier plan when matched, else the next number of the prefix."""

    def __init__(self, counters: Mapping[str, int] | None = None) -> None:
        self.counters = {p.value: int((counters or {}).get(p.value, 0)) for p in ItemPrefixV1}

    def new(self, prefix: ItemPrefixV1) -> str:
        self.counters[prefix.value] += 1
        return f"{prefix.value}-{self.counters[prefix.value]}"

    def keep_or_new(self, prefix: ItemPrefixV1, kept: str | None) -> str:
        return kept if kept is not None else self.new(prefix)


@dataclass
class _Previous:
    """What an earlier plan (``--from``) keeps: ids, supply / purchase and pins by design instance."""

    optic: dict[str, Any] = field(default_factory=dict)  # instance id -> item
    chain: dict[str, dict[ItemRoleV1, Any]] = field(default_factory=dict)  # instance id -> role -> hardware item
    board: Any = None
    revision: Any = None


def _previous(revision: Any) -> _Previous:
    if revision is None:
        return _Previous()
    items = {i.item_id: i for i in revision.items}
    carried_by = {link.a_item_id: link.b_item_id for link in revision.links}
    out = _Previous(board=items.get(revision.board_item_id), revision=revision)
    for item in revision.items:
        if item.design_instance_id is None:
            continue
        out.optic[item.design_instance_id] = item
        current, hardware = item.item_id, {}
        for role in (ItemRoleV1.MOUNT, ItemRoleV1.POST, ItemRoleV1.POST_HOLDER):
            nxt = carried_by.get(current)
            if nxt is None or nxt not in items or items[nxt].role is not role:
                break
            hardware[role] = items[nxt]
            current = nxt
        if hardware:
            out.chain[item.design_instance_id] = hardware
    return out


def _pins(previous: _Previous) -> dict[str, ChainPins]:
    pins: dict[str, ChainPins] = {}
    for instance, hardware in previous.chain.items():
        chosen = {role: item.identity.opensci_sku for role, item in hardware.items() if item.pinned and item.identity.opensci_sku}
        if chosen:
            pins[instance] = ChainPins(mount=chosen.get(ItemRoleV1.MOUNT), post=chosen.get(ItemRoleV1.POST),
                                       holder=chosen.get(ItemRoleV1.POST_HOLDER))
    return pins


# ------------------------------------------------------------------------------------------------ small helpers


def _fmt(value: float) -> str:
    return kernel._fmt(value, SETTING_DECIMALS)


def _yaw_deg(q: Quat) -> float:
    return kernel._deg(yaw_direction(q))


def _pose(pose: Pose) -> dict[str, Any]:
    return {"position_mm": list(snap_vec(pose.position)), "quaternion_wxyz": list(snap_quat(pose.rotation))}


def _display(name: str | None, fallback: str) -> str:
    """``name`` when it is display text of the plan (``DisplayText``), else ``fallback``."""

    return name if name and len(name) <= DISPLAY_TEXT_MAX and _DISPLAY.fullmatch(name) else fallback


def _parallel(a: Vec3, b: Vec3) -> bool:
    return abs(a[0] * b[0] + a[1] * b[1] + a[2] * b[2]) >= _PARALLEL_COSINE


def _is_fact(member: Any, *parameters: str) -> bool:
    return all(member.basis_of(p) is None or member.basis_of(p) in FACT_BASES for p in parameters)


def _purchase(supply: SupplyV1) -> dict[str, Any]:
    selected = supply is SupplyV1.OPENSCI
    return {"selected": selected, "quantity": 1 if selected else 0, "spare_units": 0}


def _bounded(details: Mapping[str, Any], keys: Iterable[str] = _REFUSAL_DETAIL_KEYS) -> dict[str, Any]:
    """The refusal ``details`` a finding keeps: the named keys in order while the finding's size and depth bounds hold."""

    from .contract import FINDING_DETAILS_MAX_BYTES, FINDING_DETAILS_MAX_DEPTH, _depth, canonical_bytes

    out: dict[str, Any] = {}
    for key in keys:
        if key not in details:
            continue
        trial = normalize_numbers({**out, key: details[key]})
        try:
            ok = _depth(trial) <= FINDING_DETAILS_MAX_DEPTH and len(canonical_bytes(trial)) <= FINDING_DETAILS_MAX_BYTES
        except ValueError:
            ok = False
        if ok:
            out = trial
    return out


# ------------------------------------------------------------------------------------------------ derive


def _catalog_boards(kcatalog: KernelCatalog, options: DeriveOptionsV1, previous: _Previous) -> tuple[KBoard, ...]:
    table = options.customer_table
    if table is not None:
        return (table_board(CUSTOMER_TABLE_SKU, size_x_mm=table.size_x_mm, size_y_mm=table.size_y_mm, pitch_mm=table.pitch_mm,
                            edge_offset_mm=table.edge_offset_mm, thread=table.thread, thickness_mm=CUSTOMER_TABLE_THICKNESS_MM,
                            display_name=CUSTOMER_TABLE_NAME),)
    board_item = previous.board
    if board_item is not None and board_item.pinned and board_item.identity.opensci_sku:
        pinned = kcatalog.member(board_item.identity.opensci_sku)
        if isinstance(pinned, KBoard):
            return (pinned,)
    return kcatalog.boards


def _prechecks(project: Any, classified: list[ClassifiedComponent], kcatalog: KernelCatalog, catalog: Any,
               pins: Mapping[str, ChainPins]) -> list[ClassifiedComponent]:
    """Step 2 of the module docstring: CHAIN optics no post mount can hold become anchors."""

    policy = kcatalog.policy
    by_instance = {c.instance_id: c for c in project.components}
    out = []
    for c in classified:
        if not c.chain:
            out.append(c)
            continue
        component = by_instance[c.instance_id]
        pose = component.nominal_pose
        optic = OpticRequest(c.instance_id, c.opensci_sku, c.optical_role, tuple(pose.position_mm), tuple(pose.quaternion_wxyz), (),  # type: ignore[arg-type]
                             c.mechanics, pins=pins.get(c.instance_id))  # type: ignore[arg-type]
        try:
            kernel._check_orientation(optic, policy)
        except AssemblyResolutionRefusal as refusal:
            out.append(c.as_anchor(PlanFindingCodeV1.UNSUPPORTED_OPTIC_ORIENTATION.value, _bounded(refusal.details)))
            continue
        pinned = optic.pins.mount if optic.pins is not None else None
        mounts = [m for m in kcatalog.mounts if pinned is None or m.identity == pinned]
        reasons = {m.identity: mount_incompatibility(optic, m, kcatalog.posts) for m in mounts}
        if not any(r is None for r in reasons.values()):
            near = [{"opensci_sku": sku, "reason": r} for sku, r in sorted(reasons.items())
                    if r not in ("SHAPE_MISMATCH", "SIZE_MISMATCH")]
            out.append(c.as_anchor(PlanFindingCodeV1.MOUNT_NOT_FOUND.value, _bounded({"near_misses": near}, ("near_misses",))))
            continue
        out.append(c)
    return out


def derive_plan_content(project: Any, catalog: Any, sources: Any, options: DeriveOptionsV1, from_revision: Any = None,
                        run: Any = None, *, plan_id: str | None = None, revision: int = 1, parent_digest: str | None = None,
                        kcatalog: KernelCatalog | None = None) -> DeriveOutcome:
    """The decision fields of a derived revision of ``project`` (module docstring).

    ``catalog``: the public optical catalog (``PublicOpticalCatalogV2``) the Project's products are in; ``sources``: the
    ``MechanicalSources`` the hardware comes from (``kcatalog``: their kernel catalog, when the caller built it);
    ``options``: the derive options (recorded in the revision); ``from_revision``: an earlier revision whose ids, supply,
    purchase and pins are kept; ``run``: the bound optics run (an ``OpticsRunBindingV1`` payload) or None.
    ``revision`` / ``parent_digest``: where the revision goes in its plan directory (``directory.py``)."""

    if not isinstance(project, ProjectDocumentV2):
        project = ProjectDocumentV2.model_validate(project)
    if kcatalog is None:
        if sources is None or (sources.empty if hasattr(sources, "empty") else not sources.documents):
            raise AssemblyPlanError(AssemblyPlanErrorCode.MECHANICAL_CATALOG_NOT_AVAILABLE,
                                    "no mechanical catalog is available: run `os-optics catalog sync` or pass --mechanical FILE")
        try:
            kcatalog = KernelCatalog.from_sources(sources)
        except ValueError as exc:
            raise AssemblyPlanError(AssemblyPlanErrorCode.MECHANICAL_CATALOG_NOT_AVAILABLE, str(exc)) from None
    previous = _previous(from_revision)
    pins = _pins(previous)
    boards = _catalog_boards(kcatalog, options, previous)
    kcatalog = kcatalog.with_boards(boards)
    classified = _prechecks(project, list(classify(project, catalog, frozenset(options.customer_owned))), kcatalog, catalog, pins)
    request = kernel_request(project, catalog, tuple(classified), max_board_size_mm=options.max_board_size_mm,
                             beam_height_mm=options.beam_height_mm, pins=pins)
    realization = refusal = None
    if request.optics:
        budget = ResolutionBudget(options.budget.max_chain_placements, max_geometry_checks=options.budget.max_geometry_checks,
                                  expired=_deadline)
        limit = options.budget.time_limit_s
        try:
            if limit is not None and run_control.active_monitor() is None:
                with run_control.monitored(TimeLimitMonitor(limit)):
                    realization = resolve_assembly(request, kcatalog, budget=budget)
            else:
                realization = resolve_assembly(request, kcatalog, budget=budget)
        except AssemblyResolutionRefusal as caught:
            refusal = caught
            refusal.details.setdefault("_deadline_hit", budget.exhausted_by == "DEADLINE")
    builder = _Builder(project, catalog, kcatalog, options, classified, previous)
    if realization is not None:
        builder.from_realization(realization)
    elif refusal is not None:
        builder.fallback(request, refusal)
    else:
        builder.anchors_only(request)
    content = builder.content(plan_id=plan_id, revision=revision, parent_digest=parent_digest, sources=sources, run=run,
                              from_revision=from_revision)
    return DeriveOutcome(content=content, derive_findings=tuple(builder.findings), project=project, realization=realization,
                         refusal=refusal, placements=tuple(builder.placements))


# ------------------------------------------------------------------------------------------------ the builder


class _Builder:
    """Builds the items, links and findings of one derived revision."""

    def __init__(self, project: Any, catalog: Any, kcatalog: KernelCatalog, options: DeriveOptionsV1,
                 classified: list[ClassifiedComponent], previous: _Previous) -> None:
        self.project, self.catalog, self.kcatalog, self.options = project, catalog, kcatalog, options
        self.classified = {c.instance_id: c for c in classified}
        self.order = [c.instance_id for c in classified]
        self.previous = previous
        counters = previous.revision.id_counters.model_dump() if previous.revision is not None else None
        self.ids = _Ids(counters)
        self.items: list[dict[str, Any]] = []
        self.links: list[dict[str, Any]] = []
        self.findings: list[FindingV1] = []
        self.placements: list[ChainPlacement] = []
        self.item_of_instance: dict[str, str] = {}
        self.holder_of_instance: dict[str, str] = {}
        self.yaw: Quat = IDENTITY
        self.translation: Vec3 = (0.0, 0.0, 0.0)
        self.board: KBoard | None = None
        self.board_id = ""
        self.order_counter = 1

    # ---------------------------------------------------------------------------- frame and anchors
    def _lab(self, component: Any) -> Pose:
        pose = component.nominal_pose
        frame = Pose(self.translation, self.yaw)
        world = frame.compose(Pose(tuple(pose.position_mm), tuple(pose.quaternion_wxyz)))  # type: ignore[arg-type]
        return Pose(snap_vec(world.position), snap_quat(world.rotation))

    def _component(self, instance_id: str) -> Any:
        return next(c for c in self.project.components if c.instance_id == instance_id)

    def _finding(self, code: PlanFindingCodeV1, items: Iterable[str], *, details: Mapping[str, Any] | None = None,
                 missing: Iterable[str] = ()) -> None:
        rule = FINDING_RULES[code]
        items = tuple(sorted(set(items)))
        if len(items) not in rule.item_counts:
            items = items[:max(rule.item_counts)] if max(rule.item_counts) and len(items) > max(rule.item_counts) else items
            if len(items) not in rule.item_counts:
                return
        finding = make_finding(code, items, check=FindingCheckV1.DERIVE, source=FindingSourceV1.DERIVE, details=dict(details or {}),
                               missing_data=missing)
        if all(f.finding_id != finding.finding_id for f in self.findings):
            self.findings.append(finding)

    # ---------------------------------------------------------------------------- board
    def _board(self, board: KBoard) -> None:
        self.board = board
        previous = self.previous.board
        table = board.identity == CUSTOMER_TABLE_SKU
        self.board_id = self.ids.keep_or_new(ItemPrefixV1.BRD, previous.item_id if previous is not None else None)
        member = self.kcatalog.member(board.identity) if not table else None
        user = member is not None and member.publisher == "USER"
        supply = SupplyV1.CUSTOMER_OWNED if table or user else SupplyV1.OPENSCI
        reason = SupplyReasonV1.CUSTOMER_TABLE if table else (SupplyReasonV1.USER_HARDWARE if user else None)
        identity = {"kind": "CUSTOMER_TABLE" if table else ("USER_MECH" if user else "OS_MECH"), "opensci_sku": board.identity,
                    "display_name": _display(board.display_name, "Breadboard")}
        item = {"item_id": self.board_id, "role": "BREADBOARD", "identity": identity, "supply": supply.value,
                "placement": {"pose_in_lab": _pose(board_pose(board)), "assembly_order": 1}, "purchase": _purchase(supply)}
        if reason is not None:
            item["supply_reason"] = reason.value
        self._carry(item, previous)
        self.items.append(item)

    def _carry(self, item: dict[str, Any], old: Any) -> None:
        """Keep an earlier plan's supply choice, purchase and pin on a matched item of the same identity."""

        if old is None or old.identity.opensci_sku != item["identity"].get("opensci_sku"):
            return
        item["pinned"] = bool(old.pinned)
        if item["supply"] == SupplyV1.OPENSCI.value and old.supply is SupplyV1.CUSTOMER_OWNED \
                and old.supply_reason is SupplyReasonV1.CUSTOMER_OWNED:
            item["supply"], item["supply_reason"] = SupplyV1.CUSTOMER_OWNED.value, SupplyReasonV1.CUSTOMER_OWNED.value
            item["purchase"] = _purchase(SupplyV1.CUSTOMER_OWNED)
        elif item["supply"] == old.supply.value == SupplyV1.OPENSCI.value:
            item["purchase"] = old.purchase.model_dump(mode="json")

    # ---------------------------------------------------------------------------- items
    def _identity_of(self, c: ClassifiedComponent) -> dict[str, Any]:
        if c.item_role == "OPTIC":
            out = {"kind": "OS_OPT", "opensci_sku": c.opensci_sku, "display_name": _display(c.display_name, c.opensci_sku or "Optic")}
            if c.display_name_zh_cn:
                out["display_name_zh_cn"] = c.display_name_zh_cn
            return out
        out = {"kind": "CUSTOMER_PART", "display_name": COMPONENT_NAMES[c.supply_reason]}  # type: ignore[index]
        if c.custom_part_key is not None:
            out["custom_part_key"] = c.custom_part_key
        return out

    def _design_item(self, c: ClassifiedComponent, placement: dict[str, Any] | None) -> str:
        old = self.previous.optic.get(c.instance_id)
        item_id = self.design_ids[c.instance_id]
        item: dict[str, Any] = {"item_id": item_id, "role": c.item_role, "identity": self._identity_of(c),
                                "design_instance_id": c.instance_id, "supply": c.supply.value, "purchase": _purchase(c.supply)}
        if c.supply_reason is not None:
            item["supply_reason"] = c.supply_reason.value
        if c.item_role == "OPTIC":
            item["mounting"] = c.mounting.value  # type: ignore[union-attr]
        if placement is not None:
            item["placement"] = placement
        if not c.chain and c.envelope is not None:
            item["envelope"] = {"shape": "CYLINDER", "basis": c.envelope_basis or "CATALOG_DESIGN_GEOMETRY", "reserve_column": True,
                                "diameter_mm": c.envelope.diameter_mm, "z_min_mm": c.envelope.z_min_mm, "z_max_mm": c.envelope.z_max_mm}
        if c.item_role == "OPTIC":
            self._carry(item, old)
            if item["supply"] != SupplyV1.OPENSCI.value:
                item["purchase"] = _purchase(SupplyV1(item["supply"]))
        self.items.append(item)
        self.item_of_instance[c.instance_id] = item_id
        return item_id

    def _anchor(self, c: ClassifiedComponent, retained: Iterable[Any]) -> None:
        placement = {"pose_in_lab": _pose(self._lab(self._component(c.instance_id))), "assembly_order": self._next_order()}
        if c.item_role == "OPTIC":
            placement["dofs"] = [{"axis": d.axis.value, "requested_control": d.control.value, "status": "NOT_DELIVERED",
                                  "mechanism": "NONE", "not_delivered_reason": "NO_COMPATIBLE_HARDWARE"} for d in retained]
        item_id = self._design_item(c, placement)
        if c.missing is not None and c.supply is SupplyV1.OPENSCI and c.supply_reason is None:
            self._finding(PlanFindingCodeV1.MISSING_MECHANICAL_DATA, (item_id,), missing=(c.missing.missing_data,),
                          details={"reason": c.missing.reason})
        if c.demoted is not None:
            self._finding(PlanFindingCodeV1(c.demoted), (item_id,), details=c.demoted_details)

    def _allocate(self, resolved: Iterable[str] = ()) -> None:
        """Every item id of the revision, before any item is built: kept from the earlier plan when matched, else new, in
        design order (each CHAIN optic's mount, post and holder after it). ``resolved``: the CHAIN optics whose hardware
        is placed (a blocked DOF then names the other chain's mount)."""

        self.design_ids: dict[str, str] = {}
        self.hardware_ids: dict[str, dict[ItemRoleV1, str]] = {}
        for instance in self.order:
            c = self.classified[instance]
            old = self.previous.optic.get(instance)
            prefix = ItemPrefixV1.OPT if c.item_role == "OPTIC" else ItemPrefixV1.CMP
            kept = old.item_id if old is not None and old.item_id.startswith(prefix.value + "-") else None
            self.design_ids[instance] = self.ids.keep_or_new(prefix, kept)
            if c.chain:
                old_hardware = self.previous.chain.get(instance, {})
                self.hardware_ids[instance] = {
                    role: self.ids.keep_or_new(hw_prefix, old_hardware[role].item_id if role in old_hardware else None)
                    for role, hw_prefix in ((ItemRoleV1.MOUNT, ItemPrefixV1.MNT), (ItemRoleV1.POST, ItemPrefixV1.PST),
                                            (ItemRoleV1.POST_HOLDER, ItemPrefixV1.HLD))}
        self.mount_of_instance = {i: self.hardware_ids[i][ItemRoleV1.MOUNT] for i in resolved if i in self.hardware_ids}

    def _next_order(self) -> int:
        self.order_counter += 1
        return self.order_counter

    # ---------------------------------------------------------------------------- chains
    def _chain(self, placement: ChainPlacement, c: ClassifiedComponent, component: Any) -> None:
        self.placements.append(placement)
        old_hardware = self.previous.chain.get(c.instance_id, {})
        resolved = placement.resolved
        orders = {}
        if resolved:
            for role in (ItemRoleV1.POST_HOLDER, ItemRoleV1.POST, ItemRoleV1.MOUNT):
                orders[role] = self._next_order()
        optic_order = self._next_order()
        optic_placement: dict[str, Any] = {"pose_in_lab": _pose(placement.optic_pose), "assembly_order": optic_order}
        optic_id = self._design_item(c, optic_placement)
        hw_ids = self.hardware_ids[c.instance_id]
        self.holder_of_instance[c.instance_id] = hw_ids[ItemRoleV1.POST_HOLDER]
        chain_ids = [optic_id, hw_ids[ItemRoleV1.MOUNT], hw_ids[ItemRoleV1.POST], hw_ids[ItemRoleV1.POST_HOLDER], self.board_id]
        kinds = list(ChainLinkKindV1)
        link_ids = [f"lnk-{chain_ids[k]}-{chain_ids[k + 1]}" for k in range(4)]
        if resolved:
            self._resolved_chain(placement, c, optic_placement, hw_ids, orders, link_ids, old_hardware)
        else:
            for role in (ItemRoleV1.MOUNT, ItemRoleV1.POST, ItemRoleV1.POST_HOLDER):
                self.items.append({"item_id": hw_ids[role], "role": role.value,
                                   "identity": {"kind": "NONE", "display_name": UNRESOLVED_NAMES[role]},
                                   "supply": "UNRESOLVED", "supply_reason": "NO_COMPATIBLE_HARDWARE", "purchase": _purchase(SupplyV1.UNRESOLVED)})
            optic_placement["dofs"] = [{"axis": d.axis.value, "requested_control": d.control.value, "status": "NOT_DELIVERED",
                                        "mechanism": "NONE", "not_delivered_reason": "NO_COMPATIBLE_HARDWARE"}
                                       for d in component.retained_dofs]
            datums = (("seat", "seat"), ("support", "top"), ("bottom", "bore_top"), ("bottom", "board_top"))
            for k in range(4):
                self.links.append({"link_id": link_ids[k], "kind": kinds[k].value, "a_item_id": chain_ids[k], "b_item_id": chain_ids[k + 1],
                                   "datum_a": datums[k][0], "datum_b": datums[k][1]})
            self._unresolved_finding(placement, optic_id)

    def _unresolved_finding(self, placement: ChainPlacement, optic_id: str) -> None:
        refusal = placement.refusal
        if refusal is None:
            return
        reason = refusal.details.get("reason")
        code = kernel_refusal_code(refusal.code, reason)
        if code in (PlanFindingCodeV1.DERIVE_FELL_BACK, PlanFindingCodeV1.SEARCH_BUDGET_EXHAUSTED):
            code = PlanFindingCodeV1.HEIGHT_NOT_REACHABLE
        self._finding(code, (optic_id,), details=_bounded(refusal.details))

    def _resolved_chain(self, placement: ChainPlacement, c: ClassifiedComponent, optic_placement: dict[str, Any],
                        hw_ids: dict[ItemRoleV1, str], orders: dict[ItemRoleV1, int], link_ids: list[str], old_hardware: dict) -> None:
        mount, post, holder, poses, board = placement.mount, placement.post, placement.holder, placement.poses, self.board
        assert mount is not None and post is not None and holder is not None and poses is not None and board is not None
        optic_id = self.item_of_instance[c.instance_id]
        engagement = "RESOLVED" if _is_fact(holder, "insertion_min_mm", "insertion_max_mm") else "PREVIEW_ASSUMPTION"
        relations = ("SEATED_IN", "MOUNTED_ON", "INSERTED_INTO", "STANDS_ON")
        parents = (hw_ids[ItemRoleV1.MOUNT], hw_ids[ItemRoleV1.POST], hw_ids[ItemRoleV1.POST_HOLDER], self.board_id)
        optic_placement["support"] = {"parent_item_id": parents[0], "relation": relations[0], "link_id": link_ids[0]}
        hardware = ((ItemRoleV1.MOUNT, mount, poses.mount, 1), (ItemRoleV1.POST, post, poses.post, 2),
                    (ItemRoleV1.POST_HOLDER, holder, poses.holder, 3))
        for role, view, pose, k in hardware:
            settings: list[dict[str, Any]] = []
            if role is ItemRoleV1.MOUNT:
                settings.append({"kind": "MOUNT_ORIENTATION_YAW", "value": _fmt(_yaw_deg(pose.rotation)), "unit": "deg", "basis": "RESOLVED"})
            elif role is ItemRoleV1.POST:
                settings.append({"kind": "INSERTION_DEPTH", "value": _fmt(poses.insertion_depth_mm), "unit": "mm", "basis": engagement})
            else:
                settings.append({"kind": "BASE_YAW", "value": _fmt(_yaw_deg(pose.rotation)), "unit": "deg", "basis": "RESOLVED"})
                settings.append({"kind": "FIXATION_THREAD", "value": fixation_thread(holder), "unit": "NONE", "basis": "RESOLVED"})
            item_placement: dict[str, Any] = {
                "pose_in_lab": _pose(pose), "assembly_order": orders[role], "settings": sorted(settings, key=lambda s: s["kind"]),
                "support": {"parent_item_id": parents[k], "relation": relations[k], "link_id": link_ids[k]}}
            if role is ItemRoleV1.POST_HOLDER and placement.hole is not None:
                item_placement["fixation"] = {"mode": "SLOT", "board_holes": [list(placement.hole)], "thread": fixation_thread(holder),
                                              "slot_offset_mm": placement.slot_offset_mm or 0.0}
            user = view.publisher == "USER"
            supply = SupplyV1.CUSTOMER_OWNED if user else SupplyV1.OPENSCI
            item = {"item_id": hw_ids[role], "role": role.value,
                    "identity": {"kind": "USER_MECH" if user else "OS_MECH", "opensci_sku": view.identity,
                                 "display_name": _display(view.display_name, view.identity)},
                    "supply": supply.value, "placement": item_placement, "purchase": _purchase(supply)}
            if user:
                item["supply_reason"] = SupplyReasonV1.USER_HARDWARE.value
            self._carry(item, old_hardware.get(role))
            self.items.append(item)
        designations = interface_designations(placement.optic, mount, post, holder, board)
        b_in_a = link_poses(poses, post, holder, board)
        fastener = None if mount.post_fastening is None else {"kind": mount.post_fastening, "thread": kernel._metric(mount.post_thread)}
        options = [o for o in mount.fastening_options if o != "PLAIN_STUD"]
        interfaces = {
            "OPTIC_IN_MOUNT": {"designation": designations["OPTIC_IN_MOUNT"], "diameter_mm": placement.optic.size_mm,
                               "depth_mm": placement.optic.thickness_mm},
            "MOUNT_ON_POST": {"designation": designations["MOUNT_ON_POST"], "thread": mount.post_thread,
                              **({"fastener": fastener} if fastener else {}),
                              **({"fastening_options": [{"kind": o, "thread": kernel._metric(mount.post_thread)} for o in options]}
                                 if len(mount.fastening_options) > 1 else {})},
            "POST_IN_HOLDER": {"designation": designations["POST_IN_HOLDER"], "diameter_mm": post.diameter_mm,
                               "depth_mm": poses.insertion_depth_mm},
            "HOLDER_ON_BOARD": {"designation": designations["HOLDER_ON_BOARD"], "thread": board.thread},
        }
        chain_ids = [optic_id, *parents]
        datums = (("seat", "seat"), ("support", "top"), ("bottom", "bore_top"), ("bottom", "board_top"))
        for k, kind in enumerate(ChainLinkKindV1):
            self.links.append({"link_id": link_ids[k], "kind": kind.value, "a_item_id": chain_ids[k], "b_item_id": chain_ids[k + 1],
                               "datum_a": datums[k][0], "datum_b": datums[k][1], "interface": interfaces[kind.value],
                               "b_in_a": _pose(b_in_a[kind.value])})
        ranges, by_mechanism = self._ranges(placement, optic_id, hw_ids)
        dofs = self._dofs(placement, optic_id, ranges, by_mechanism)
        for r in ranges:
            r["delivers"] = sorted({d["axis"] for d in dofs if d.get("range_id") == r["range_id"]},
                                   key=lambda a: ("Tx", "Ty", "Tz", "Rx", "Ry", "Rz").index(a))
        optic_placement["ranges"] = sorted(ranges, key=lambda r: r["range_id"])
        optic_placement["dofs"] = dofs

    def _ranges(self, placement: ChainPlacement, optic_id: str, hw_ids: dict[ItemRoleV1, str]) -> tuple[list[dict], dict]:
        mount, post, holder, poses = placement.mount, placement.post, placement.holder, placement.poses
        assert mount is not None and post is not None and holder is not None and poses is not None
        depth = poses.insertion_depth_mm
        depth_max = min(holder.insertion_max_mm, post.length_mm)
        engagement = "RESOLVED" if _is_fact(holder, "insertion_min_mm", "insertion_max_mm") else "PREVIEW_ASSUMPTION"
        up = (0.0, 0.0, 1.0)
        mount_q = poses.mount.rotation
        mx, my = snap_vec(rotate(mount_q, (1.0, 0.0, 0.0))), snap_vec(rotate(mount_q, (0.0, 1.0, 0.0)))
        seat = snap_vec(placement.optic_pose.position)
        above = sorted([optic_id, hw_ids[ItemRoleV1.MOUNT], hw_ids[ItemRoleV1.POST]])
        ranges: list[dict[str, Any]] = []
        by_mechanism: dict[tuple[str, str], str] = {}

        def add(kind: RangeKindV1, axis: Vec3, lo: float, hi: float, provider: str, moves: list[str], basis: str, adjustment: str,
                pivot: Vec3 | None = None, mechanism: str | None = None, which: str = "") -> None:
            range_id = f"rng-{optic_id}-{kind.value.lower().replace('_', '-')}"
            entry = {"range_id": range_id, "kind": kind.value, "axis_lab": list(axis), "min": min(round(lo, SETTING_DECIMALS), 0.0) + 0.0,
                     "max": max(round(hi, SETTING_DECIMALS), 0.0) + 0.0, "unit": "deg" if pivot is not None else "mm",
                     "provided_by": provider, "moves": sorted(moves), "basis": basis, "adjustment": adjustment}
            if pivot is not None:
                entry["pivot_lab"] = list(pivot)
            ranges.append(entry)
            if mechanism is not None:
                by_mechanism[(mechanism, which)] = range_id

        add(RangeKindV1.POST_HEIGHT, up, -(depth_max - depth), depth - holder.insertion_min_mm, hw_ids[ItemRoleV1.POST_HOLDER], above,
            engagement, "CONTINUOUS_CLAMPED", mechanism="POST_HEIGHT_CLAMP")
        post_axis = snap_vec((poses.post.position[0], poses.post.position[1], 0.0))
        add(RangeKindV1.POST_ROTATION, up, -kernel._HALF_TURN_DEG, kernel._HALF_TURN_DEG, hw_ids[ItemRoleV1.POST], above, "RESOLVED",
            "CONTINUOUS_CLAMPED", pivot=post_axis, mechanism="POST_ROTATION_CLAMP")
        if placement.hole is not None and placement.slot_offset_mm is not None:
            fork = snap_vec(rotate(poses.holder.rotation, (1.0, 0.0, 0.0)))
            s = placement.slot_offset_mm
            add(RangeKindV1.SLOT_TRAVEL, fork, s - holder.base.slot_end_mm, s - holder.base.slot_start_mm, hw_ids[ItemRoleV1.POST_HOLDER],
                [*above, hw_ids[ItemRoleV1.POST_HOLDER]], "RESOLVED" if _is_fact(holder, "base.slot") else "PREVIEW_ASSUMPTION",
                "COARSE_REPOSITION", mechanism="BASE_SLOT_TRAVEL")
        for mech in mount.mechanisms:
            basis = "RESOLVED" if _is_fact(mount, f"mechanism.{mech.kind.value}.travel") else "PREVIEW_ASSUMPTION"
            t = mech.travel
            if mech.kind.value == "TIP_TILT":
                add(RangeKindV1.MOUNT_TIP, my, -t, t, hw_ids[ItemRoleV1.MOUNT], [optic_id], basis, mech.adjustment, pivot=seat,
                    mechanism="TIP_TILT_MOUNT", which="y")
                add(RangeKindV1.MOUNT_TILT, up, -t, t, hw_ids[ItemRoleV1.MOUNT], [optic_id], basis, mech.adjustment, pivot=seat,
                    mechanism="TIP_TILT_MOUNT", which="z")
            elif mech.kind.value == "TRANSVERSE_TRANSLATION":
                add(RangeKindV1.MOUNT_TRANSLATION_Y, my, -t, t, hw_ids[ItemRoleV1.MOUNT], [optic_id], basis, mech.adjustment,
                    mechanism="TRANSLATION_MOUNT", which="y")
                add(RangeKindV1.MOUNT_TRANSLATION_Z, up, -t, t, hw_ids[ItemRoleV1.MOUNT], [optic_id], basis, mech.adjustment,
                    mechanism="TRANSLATION_MOUNT", which="z")
            else:
                add(RangeKindV1.MOUNT_AXIAL_ROTATION, mx, -t, t, hw_ids[ItemRoleV1.MOUNT], [optic_id], basis, mech.adjustment, pivot=seat,
                    mechanism="ROTATION_MOUNT")
        return ranges, {"_axes": {"y": my, "z": up}, **{f"{k[0]}|{k[1]}": v for k, v in by_mechanism.items()}}

    def _dofs(self, placement: ChainPlacement, optic_id: str, ranges: list[dict], by_mechanism: dict) -> list[dict[str, Any]]:
        out = []
        blocking = None
        for cause in placement.fallback_causes:
            other = cause.get("with")
            if other in self.mount_of_instance:  # the other chain's support blocks the hardware
                blocking = self.mount_of_instance[other]
                break
            if other in self.design_ids:
                blocking = self.design_ids[other]
                break
        axes = by_mechanism["_axes"]
        for d in sorted(placement.dofs, key=lambda d: ("Tx", "Ty", "Tz", "Rx", "Ry", "Rz").index(d.axis)):
            entry: dict[str, Any] = {"axis": d.axis, "requested_control": d.requested_control, "status": d.status,
                                     "mechanism": d.mechanism}
            if d.status == "NOT_DELIVERED":
                reason = DOF_REASONS.get(d.not_delivered_reason or "", "NO_COMPATIBLE_HARDWARE")
                entry["not_delivered_reason"] = reason
                if reason == "HARDWARE_DID_NOT_FIT" and blocking is not None and blocking != optic_id:
                    entry["blocking_item_id"] = blocking
                items = [optic_id] + ([entry["blocking_item_id"]] if "blocking_item_id" in entry else [])
                self._finding(PlanFindingCodeV1.DOF_NOT_DELIVERED, items,
                              details={"axis": d.axis, "reason": reason, "kernel_reason": d.not_delivered_reason or reason})
            else:
                key = f"{d.mechanism}|"
                if key not in by_mechanism:
                    which = "y" if _parallel(tuple(d.lab_direction), axes["y"]) else "z"
                    key = f"{d.mechanism}|{which}"
                range_id = by_mechanism.get(key)
                if range_id is not None:
                    entry["range_id"] = range_id
            out.append(entry)
        return out

    # ---------------------------------------------------------------------------- the three outcomes
    def from_realization(self, realization: AssemblyRealizationV2) -> None:
        self.yaw = snap_quat(realization.design_yaw)
        self.translation = realization.design_translation
        self._board(realization.board.board)
        self._allocate(t.instance_id for t in realization.chains)
        fixations = {f.slot_key: f for f in realization.fixations}
        traces = {t.instance_id: t for t in realization.chains}
        for instance in self.order:
            c = self.classified[instance]
            component = self._component(instance)
            if not c.chain:
                continue
            trace = traces[instance]
            key = trace.chain_key
            parts = {p.role: p for p in realization.parts if p.chain_key == key}
            optic_pose = realization.optic_lab_poses[instance]
            fixation = fixations[kernel.pk(key, "holder")]
            holder_pose = parts["POST_HOLDER"].pose
            poses = ChainPoses(optic=optic_pose, mount=parts["OPTIC_MOUNT"].pose, post=parts["POST"].pose, holder=holder_pose,
                               insertion_depth_mm=trace.insertion_depth_mm)
            axis_xy = (holder_pose.position[0], holder_pose.position[1])
            along = slot_offset(realization.board.board, fixation.hole, axis_xy, holder_pose.rotation)[0]
            placement = ChainPlacement(
                instance_id=instance, optic=c.mechanics, optic_pose=optic_pose,  # type: ignore[arg-type]
                axis_lab=snap_vec(rotate(parts["OPTIC_MOUNT"].pose.rotation, (1.0, 0.0, 0.0))),
                mount=parts["OPTIC_MOUNT"].member, post=parts["POST"].member, holder=parts["POST_HOLDER"].member,  # type: ignore[arg-type]
                poses=poses, hole=fixation.hole, holder_yaw=holder_pose.rotation, slot_offset_mm=along,
                dofs=tuple(d for d in realization.dofs if d.instance_id == instance),
                fallback_causes=trace.fallback_causes)
            self._chain(placement, c, component)
        self._anchors()

    def _anchors(self) -> None:
        for instance in self.order:
            c = self.classified[instance]
            if not c.chain:
                self._anchor(c, self._component(instance).retained_dofs if c.item_role == "OPTIC" else ())

    def anchors_only(self, request: Any) -> None:
        """No CHAIN optic: the anchors at their design poses on the smallest allowed board, centred."""

        board = self._smallest_board(None)[0]
        lift = self.options.beam_height_mm if self.options.beam_height_mm is not None else self.kcatalog.policy.preferred_beam_height_mm
        points = [tuple(c.nominal_pose.position_mm) for c in self.project.components]
        self.translation = self._centre(board, points, lift)
        self._board(board)
        self._allocate()
        self._anchors()

    def _centre(self, board: KBoard, points: list[tuple[float, float, float]], lift: float) -> Vec3:
        if not points:
            return (board.size_x_mm / 2.0, board.size_y_mm / 2.0, lift)
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        return (round(board.size_x_mm / 2.0 - (min(xs) + max(xs)) / 2.0, SETTING_DECIMALS),
                round(board.size_y_mm / 2.0 - (min(ys) + max(ys)) / 2.0, SETTING_DECIMALS), lift)

    def _allowed(self, board: KBoard) -> bool:
        return kernel._within_limit(board, self.options.max_board_size_mm)

    def _smallest_board(self, footprint: tuple[float, float] | None) -> tuple[KBoard, bool]:
        """The smallest allowed board that holds ``footprint`` (with the edge margin on both sides), and whether one did;
        otherwise the largest allowed board (or the largest board when none is allowed)."""

        margin = 2.0 * self.kcatalog.policy.board_edge_margin_mm
        ordered = kernel._board_order(self.kcatalog.boards)
        allowed = [b for b in ordered if self._allowed(b)] or ordered
        for board in allowed:
            if footprint is None or (footprint[0] + margin <= board.size_x_mm and footprint[1] + margin <= board.size_y_mm):
                return board, True
        return allowed[-1], False

    def fallback(self, request: Any, refusal: AssemblyResolutionRefusal) -> None:
        """The per-chain candidate of a refused derivation (module docstring, step 6)."""

        policy = self.kcatalog.policy
        try:
            paths = trace_beam_paths(request.externals, request.optics, request.observations, **beam_trace_settings(policy))
        except (AssemblyResolutionRefusal, ValueError, ZeroDivisionError):
            paths = ()
        try:
            self.yaw = snap_quat(kernel._design_yaw(request.optics, paths))
        except (ValueError, ZeroDivisionError):
            self.yaw = IDENTITY
        incidences = kernel._incidences(request.optics, paths) if paths else {}
        lift = self._lift(request)
        # hardware first (independent of the board), then the footprint chooses the board
        chosen: list[tuple[OpticRequest, ChainPlacement]] = []
        provisional = kernel._board_order(self.kcatalog.boards)[-1]
        for optic in request.optics:
            pose = Pose(rotate(self.yaw, optic.position_design), q_mul(self.yaw, optic.rotation_design))
            pose = Pose(snap_vec((pose.position[0], pose.position[1], pose.position[2] + lift)), snap_quat(pose.rotation))
            incidence = incidences.get(optic.instance_id)
            placed = place_single_chain(optic, pose, self.kcatalog, provisional, (), optic.pins,
                                        incidence_deg=0.0 if incidence is None else min(incidence.degrees, kernel._MAX_SEARCHED_INCIDENCE_DEG),
                                        incidence_offset_mm=0.0 if incidence is None else incidence.offset_mm)
            chosen.append((optic, placed))
        boxes = [b for _, p in chosen if p.resolved for bs in chain_bodies(p.mount, p.post, p.holder, p.poses).values() for b in bs]  # type: ignore[arg-type]
        points = [snap_vec(rotate(self.yaw, tuple(c.nominal_pose.position_mm))) for c in self.project.components]  # type: ignore[arg-type]
        if boxes:
            (x0, y0), (x1, y1) = plan_bounds(boxes)
        else:
            x0, y0, x1, y1 = (min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points))
        footprint = (round(x1 - x0, SETTING_DECIMALS), round(y1 - y0, SETTING_DECIMALS))
        board, fits = self._smallest_board(footprint)
        shift = (round(board.size_x_mm / 2.0 - (x0 + x1) / 2.0, SETTING_DECIMALS), round(board.size_y_mm / 2.0 - (y0 + y1) / 2.0, SETTING_DECIMALS))
        self.translation = (shift[0], shift[1], lift)
        self._board(board)
        occupied: set[tuple[int, int]] = set()
        placements: dict[str, ChainPlacement] = {}
        for optic, placed in chosen:
            moved = Pose(snap_vec((placed.optic_pose.position[0] + shift[0], placed.optic_pose.position[1] + shift[1],
                                   placed.optic_pose.position[2])), placed.optic_pose.rotation)
            if placed.resolved:
                again = replace_chain(optic.instance_id, placed.optic, moved, placed.mount, placed.post, placed.holder, board,  # type: ignore[arg-type]
                                      occupied_holes=occupied, dofs=placed.dofs)
                again = ChainPlacement(**{**again.__dict__, "assumptions": placed.assumptions, "fell_back": placed.fell_back})
                if again.hole is not None:
                    occupied.add(again.hole)
                placements[optic.instance_id] = again
            else:
                placements[optic.instance_id] = ChainPlacement(**{**placed.__dict__, "optic_pose": moved})
        self._allocate(i for i, p in placements.items() if p.resolved)
        for instance in self.order:
            c = self.classified[instance]
            if c.chain:
                self._chain(placements[instance], c, self._component(instance))
        self._anchors()
        for instance, placement in placements.items():
            if placement.resolved and placement.hole is None:
                self._finding(PlanFindingCodeV1.NO_FIXING_HOLE, (self.holder_of_instance[instance],),
                              details={"reason": placement.fixation_problem})
        if not fits:
            self._finding(PlanFindingCodeV1.BOARD_TOO_SMALL, (self.board_id,),
                          details={"footprint_mm": list(footprint),
                                   "smallest_board_for_footprint_mm": self._smallest_any(footprint)})
        self._refusal_findings(refusal)

    def _smallest_any(self, footprint: tuple[float, float]) -> list[float] | None:
        margin = 2.0 * self.kcatalog.policy.board_edge_margin_mm
        for board in kernel._board_order(self.kcatalog.boards):
            if footprint[0] + margin <= board.size_x_mm and footprint[1] + margin <= board.size_y_mm:
                return [board.size_x_mm, board.size_y_mm]
        return None

    def _lift(self, request: Any) -> float:
        policy = self.kcatalog.policy
        if self.options.beam_height_mm is not None:
            return self.options.beam_height_mm
        chains = []
        for n, optic in enumerate(request.optics, start=1):
            o = kernel._optic_with_yaw(optic, f"c{n}", self.yaw)
            ranked, _ = kernel.rank_mounts(o, self.kcatalog)
            if not ranked:
                break
            chains.append((o, ranked[0][1]))
        else:
            try:
                return kernel._choose_lift(chains, self.kcatalog, None) if chains else policy.preferred_beam_height_mm
            except (kernel._ChainFailure, AssemblyResolutionRefusal):
                pass
        first = request.optics[0].position_design[2] if request.optics else 0.0
        return policy.preferred_beam_height_mm - rotate(self.yaw, (0.0, 0.0, first))[2]

    def _refusal_findings(self, refusal: AssemblyResolutionRefusal) -> None:
        details = refusal.details
        reason = details.get("reason")
        code = kernel_refusal_code(refusal.code, reason)
        exhausted = bool(details.get("search_budget_exhausted"))
        items = [self.item_of_instance[i] for i in (details.get("instance_id"), details.get("with")) if i in self.item_of_instance]
        if code is PlanFindingCodeV1.BOARD_TOO_SMALL:
            items = [self.board_id]
        if code not in (PlanFindingCodeV1.DERIVE_FELL_BACK, PlanFindingCodeV1.SEARCH_BUDGET_EXHAUSTED):
            self._finding(code, items, details=_bounded(details))
        if exhausted or code is PlanFindingCodeV1.SEARCH_BUDGET_EXHAUSTED:
            bound = details.get("search_bound") if isinstance(details.get("search_bound"), dict) else {}
            self._finding(PlanFindingCodeV1.SEARCH_BUDGET_EXHAUSTED, (),
                          details=_bounded({"bound": bound.get("kind"), "limit": bound.get("limit"), "spent": bound.get("spent"),
                                            "deadline_hit": bool(details.get("_deadline_hit"))},
                                           ("bound", "limit", "spent", "deadline_hit")))
        self._finding(PlanFindingCodeV1.DERIVE_FELL_BACK, (),
                      details={"kernel_refusal": _bounded({"code": refusal.code, **{k: details[k] for k in ("reason", "instance_id", "with")
                                                                                     if k in details}},
                                                          ("code", "reason", "instance_id", "with"))})

    # ---------------------------------------------------------------------------- content
    def content(self, *, plan_id: str | None, revision: int, parent_digest: str | None, sources: Any, run: Any,
                from_revision: Any) -> dict[str, Any]:
        mechanical = [] if sources is None else [r.model_dump(mode="json") for r in sources.binding()]
        options = self.options.model_dump(mode="json", by_alias=True, exclude_none=True)
        # (the plan contract's subject of a bound Project is "project")
        project_ref = {"profile": "osc1", "digest": self.project.content_hash(), "subject": PROJECT_BINDING_SUBJECT}
        catalog_version = self.catalog.catalog_version
        if plan_id is None:
            plan_id = from_revision.plan_id if from_revision is not None else derived_plan_id(
                project_ref["digest"], catalog_version, mechanical, options)
        intent = self.project.design_intent or DesignIntentV1.LAB_ASSEMBLY
        bindings: dict[str, Any] = {"project": {"project_hash": project_ref, "design_intent": intent.value},
                                    "optical_catalog": {"catalog_version": catalog_version}, "mechanical": mechanical}
        if run is not None:
            bindings["optics_run"] = run.model_dump(mode="json", by_alias=True, exclude_none=True) if hasattr(run, "model_dump") else dict(run)
        new_ids = {i["item_id"] for i in self.items}
        retired = set(from_revision.retired_item_ids) if from_revision is not None else set()
        if from_revision is not None:
            retired |= {i.item_id for i in from_revision.items} - new_ids
        return {
            "schema": PLAN_SCHEMA_ID, "schema_version": PLAN_SCHEMA_VERSION, "plan_id": plan_id, "revision": revision,
            **({"parent_digest": parent_digest} if parent_digest is not None else {}),
            "origin": "DERIVE" if from_revision is None else "REDERIVE", "bindings": bindings,
            "frame": {"yaw_deg": _yaw_deg(self.yaw), "translation_mm": list(self.translation), "basis": "DERIVED"},
            "board_item_id": self.board_id, "items": sorted(self.items, key=lambda i: i["item_id"]),
            "links": sorted(self.links, key=lambda link: link["link_id"]), "id_counters": dict(self.ids.counters),
            "retired_item_ids": sorted(retired), "derive_options": options,
        }


__all__ = ["CUSTOMER_TABLE_THICKNESS_MM", "DeriveOutcome", "TimeLimitMonitor", "derive_plan_content"]

