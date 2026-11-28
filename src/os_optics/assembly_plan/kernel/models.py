"""Inputs, outputs and refusals of the multi-optic assembly kernel.

The kernel is pure: the caller (``assembly_plan.request``) maps a Project to an ``AssemblyResolutionRequestV2`` (the
design facts and the mechanics of each OpenSci optic, from the public optical catalog) and receives an
``AssemblyRealizationV2`` or an ``AssemblyResolutionRefusal``; ``assembly_plan.derive`` turns either into a plan.

Frames:

* the **design frame** is the Project's frame: right-handed, mm, +Z up (gravity along -Z), the frame the Optical
  Simulation View renders. Resolution never tilts it;
* the **lab frame** (``ASSEMBLY_WORLD``) is the selected breadboard's frame: origin at a board corner on its top
  surface, +Z up, the board covering ``[0, size_x] x [0, size_y]``;
* ``design_to_lab`` is a yaw about +Z followed by a translation: ``p_lab = R_z(yaw) p_design + t``;
* **component-local** DOFs (Tx..Rz) are along/about the component's own axes; their lab direction is
  ``R_z(yaw) R(q_component) e_axis``, reported per DOF.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .catalog import DEFAULT_MAX_GEOMETRY_CHECKS, KBoard, KHolder, KMount, KPost, OpticMechanicsV1, VisualPrimitiveV1
from .geometry import Pose, Quat, Vec3, YawBox, q_unit

RESOLVER_KERNEL_ID = "os-optics.assembly_plan.kernel"
#: 1.0.0: the kernel over the public mechanical catalog (``opensci.public_mechanical_catalog`` 1.0). Its rules are those
#: of the assembly kernel 2.6.0 OpenSci ran before; its ranking breaks ties by catalog identity (delivered DOFs, unrequested
#: capability, evidence basis, identity), so the same inputs give the same plan on every installation.
RESOLVER_KERNEL_VERSION = "1.0.0"


class AssemblyResolutionRefusal(Exception):
    """The design cannot be realized with the catalog. ``code`` and ``message`` are customer text; ``details`` is
    structured context; ``internal`` records the candidates considered (catalog identities).

    ``details`` of a mechanical refusal is stable:

    * ``instance_id``: the design component whose support could not be realized;
    * ``reason``: the failure kind (``SUPPORTS_COLLIDE``, ``HARDWARE_BLOCKS_BEAM``, ``NO_FIXING_HOLE``,
      ``MOUNT_CLIPS_OBLIQUE_BEAM``, ``OPTIC_TILT_NOT_SUPPORTED``, ``NO_COMMON_BEAM_HEIGHT``, ...);
    * ``with``: the other design component involved (the one collided with, whose beam is blocked or whose support
      occupies the fixing positions), when known;
    * ``suggestion``: ``{"action": <recovery code>, ...numbers}`` -- what to change, with the numbers the kernel
      could determine (spacing along the beam in mm, the free direction in the design frame, the nearest position
      along the beam at which the support can be fixed, a reachable height range, the largest supported tilt ...);
    * ``search_budget_exhausted``: true when the bounded search stopped before trying every alternative (the
      reported failure is then the most informative one seen).

    Nothing in ``details`` names a mount identity or a kernel internal."""

    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None, internal: dict[str, Any] | None = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.details = details or {}
        self.internal = internal or {}


#: The injected deadline is polled at the start of every layout evaluation and at least every this many geometry checks.
DEADLINE_POLL_CHECKS = 2048
#: The geometry checks the diagnoses of a resolution's layout fallbacks may spend together, across every kernel pass
#: (one per fallback chain, so without a shared limit their total would grow with the design).
DEFAULT_MAX_DIAGNOSIS_CHECKS = 1_000_000


class ResolutionBudget:
    """The deterministic work budget of one derivation, shared by every kernel pass of it.

    Two counters, each with a hard limit, are charged **before** the work they stand for is done, so neither can ever
    exceed its limit and a refused charge stops the search where it is -- also in the middle of one layout evaluation:

    * **chain placements** (``max_chain_placements``): the search breadth. A layout evaluation (placing the support
      chains of the design at one board, design orientation and plan translation) is charged one placement per chain
      it places or tries, each before that chain is placed.
    * **geometry checks** (``max_geometry_checks``): the elementary tests the work consists of, which grow with the
      size of the design -- a broad-phase bounds test against one placed support, a box-box clearance test, a
      beam-segment-box test, a board hole examined, a plan-translation candidate scored, a beam-height candidate checked
      against a chain, a beam-trace step against one design element. Each batch of them (e.g. one placed body against
      every support already on the board, or against every beam segment) is charged before it runs.

    Once either is spent the kernel refuses with the most informative failure it has seen
    (``details.search_budget_exhausted``). ``expired`` is an optional safety net injected by the caller (the derive
    pipeline polls the run's monitor through ``run_control``), polled when a layout evaluation starts and at least
    every ``DEADLINE_POLL_CHECKS`` geometry checks: the kernel itself never reads a clock, so without it the outcome
    depends only on the inputs.

    A third account, **diagnosis checks** (``max_diagnosis_checks``), is charged by the diagnoses of layout fallbacks
    (the numbers of a move that would keep a component's requested hardware), the same way and under the
    same deadline: one shared limit for every fallback of every kernel pass, so their total no longer grows with the
    number of chains that fall back. They have their own account, so that they never take work from the search and a
    resolution's outcome does not depend on them; a spent diagnosis account only leaves later fallbacks without numbers.
    The diagnosis of the refusal itself has a fixed allowance of its own (``ResolverPolicyV1.diagnosis_max_geometry_checks``),
    once per kernel pass, so that a refusal of a spent budget still says how to fix the design.

    A further kernel pass of the same design on the same budget (``last_search``: the search state of the pass before)
    keeps what that pass established instead of searching for it again. Its refusal only answers the caller's question
    about the design; it is not diagnosed again (``refused``)."""

    def __init__(self, max_chain_placements: int, *, max_geometry_checks: int = DEFAULT_MAX_GEOMETRY_CHECKS,
                 expired: Callable[[], bool] | None = None, max_diagnosis_checks: int = DEFAULT_MAX_DIAGNOSIS_CHECKS) -> None:
        if max_chain_placements < 1:
            raise ValueError("a resolution budget needs at least one chain placement")
        if max_geometry_checks < 1:
            raise ValueError("a resolution budget needs at least one geometry check")
        if max_diagnosis_checks < 0:
            raise ValueError("a resolution budget's diagnosis allowance cannot be negative")
        self.max_chain_placements = max_chain_placements
        self.max_geometry_checks = max_geometry_checks
        self.expired = expired
        self.chain_placements = 0
        self.geometry_checks = 0
        self.layout_evaluations = 0
        self._next_poll = DEADLINE_POLL_CHECKS
        self.max_diagnosis_checks = max_diagnosis_checks
        self.diagnosis_checks = 0
        self._next_diagnosis_poll = DEADLINE_POLL_CHECKS
        #: "WORK" (a work counter) or "DEADLINE" (the injected safety net) once exhausted
        self.exhausted_by: str | None = None
        #: which work counter was spent when ``exhausted_by`` is "WORK": "CHAIN_PLACEMENTS" or "GEOMETRY_CHECKS"
        self.exhausted_counter: str | None = None
        #: a kernel pass on this budget has refused
        self.refused = False
        #: the search state of the last kernel pass on this budget (``resolve._Search``)
        self.last_search: Any = None

    def _deadline_passed(self) -> bool:
        if self.expired is not None and self.expired():
            self.exhausted_by = "DEADLINE"
            return True
        return False

    def admit(self) -> bool:
        """May one more layout evaluation start (room for at least one chain placement, deadline not passed)? False
        when the budget is (or has just become) exhausted; True counts the evaluation."""

        if self.exhausted_by is not None:
            return False
        if self.chain_placements >= self.max_chain_placements or self.geometry_checks >= self.max_geometry_checks:
            self.exhausted_by = "WORK"
            self.exhausted_counter = "CHAIN_PLACEMENTS" if self.chain_placements >= self.max_chain_placements else "GEOMETRY_CHECKS"
            return False
        if self._deadline_passed():
            return False
        self.layout_evaluations += 1
        return True

    def take(self, *, placements: int = 0, checks: int = 0) -> bool:
        """Charge ``placements`` chain placements and ``checks`` geometry checks before doing that work. False (nothing
        charged, the budget exhausted) when either would pass its limit or the deadline has passed: the caller must
        then not do the work."""

        if self.exhausted_by is not None:
            return False
        if (self.chain_placements + placements > self.max_chain_placements
                or self.geometry_checks + checks > self.max_geometry_checks):
            self.exhausted_by = "WORK"
            self.exhausted_counter = ("CHAIN_PLACEMENTS" if self.chain_placements + placements > self.max_chain_placements
                                      else "GEOMETRY_CHECKS")
            return False
        if self.geometry_checks + checks >= self._next_poll:  # the batch reaches the next multiple of the poll interval
            self._next_poll = ((self.geometry_checks + checks) // DEADLINE_POLL_CHECKS + 1) * DEADLINE_POLL_CHECKS
            if self._deadline_passed():
                return False
        self.chain_placements += placements
        self.geometry_checks += checks
        return True

    def take_diagnosis(self, checks: int) -> bool:
        """Charge ``checks`` geometry checks of a layout-fallback diagnosis to the diagnosis account before doing them.
        False (nothing charged) when the account would pass its limit, the budget is already exhausted or the deadline
        has passed (polled like ``take``; a passed deadline exhausts the whole budget)."""

        if self.exhausted_by is not None or self.diagnosis_checks + checks > self.max_diagnosis_checks:
            return False
        if self.diagnosis_checks + checks >= self._next_diagnosis_poll:
            self._next_diagnosis_poll = ((self.diagnosis_checks + checks) // DEADLINE_POLL_CHECKS + 1) * DEADLINE_POLL_CHECKS
            if self._deadline_passed():
                return False
        self.diagnosis_checks += checks
        return True

    def summary(self) -> dict[str, Any]:
        return {"chain_placements": self.chain_placements, "max_chain_placements": self.max_chain_placements,
                "geometry_checks": self.geometry_checks, "max_geometry_checks": self.max_geometry_checks,
                "layout_evaluations": self.layout_evaluations, "diagnosis_checks": self.diagnosis_checks,
                "max_diagnosis_checks": self.max_diagnosis_checks, "exhausted_by": self.exhausted_by}

    @property
    def exhausted(self) -> bool:
        return self.exhausted_by is not None


# ------------------------------------------------------------------------------------------------ request


@dataclass(frozen=True)
class DofRequest:
    axis: str  # "Tx".."Rz"
    control: str  # "MANUAL" | "NUMERICAL"


@dataclass(frozen=True)
class PortRequest:
    """A published port of an element placed by its ports (a cube or polarizing cube splitter, a prism, an off-axis
    parabolic mirror), in the element's own frame: ``IN`` -- the nominal input beam travels along ``direction`` through
    ``point_mm``; ``OUT`` / ``OUT_<NAME>`` -- light of the nominal input leaves from ``point_mm`` along ``direction``
    (e.g. ``OUT_T`` the transmitted and ``OUT_R`` the reflected output of a cube splitter)."""

    port_id: str
    point_mm: Vec3
    direction: Vec3

    @property
    def is_input(self) -> bool:
        return self.port_id == "IN"


@dataclass(frozen=True)
class ChainPins:
    """Catalog identities a chain must use (``None``: the ranking chooses). A pinned identity that cannot hold the
    optic leaves no candidate, and the kernel refuses as it does for an optic no mount holds."""

    mount: str | None = None
    post: str | None = None
    holder: str | None = None


@dataclass(frozen=True)
class OpticRequest:
    """One OpenSci optical product of the design. ``ports``: the published ports of a product placed by its ports (the
    nominal beam enters at ``IN`` and leaves through every ``OUT`` port); empty for every other form (a cube splitter
    without published ports follows the cube convention of its size)."""

    instance_id: str
    optical_sku: str
    role: str
    position_design: Vec3
    rotation_design: Quat
    retained_dofs: tuple[DofRequest, ...]
    mechanics: OpticMechanicsV1
    ports: tuple[PortRequest, ...] = ()
    #: identities the ranking must keep for this optic's chain (a plan's pinned hardware; None: any)
    pins: ChainPins | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "rotation_design", q_unit(tuple(float(c) for c in self.rotation_design)))  # taken by its direction, q / |q|


@dataclass(frozen=True)
class ReservedEnvelope:
    """The known geometry of a customer-supplied component: a cylinder about its local +Z (optical axis / face normal),
    ``diameter_mm`` wide, from ``z_min_mm`` to ``z_max_mm`` in its own design frame."""

    diameter_mm: float
    z_min_mm: float
    z_max_mm: float

    def __post_init__(self) -> None:
        if not (self.diameter_mm > 0 and self.z_min_mm < self.z_max_mm):
            raise ValueError("a reserved envelope needs a positive diameter and z_min_mm < z_max_mm")


@dataclass(frozen=True)
class ExternalRequest:
    """A customer-supplied design component: not OpenSci hardware and without an OpenSci support chain. It is an
    external design anchor at its design pose.

    * ``role == "source"``: the customer's light source; the nominal beam starts at it (local +Z).
    * any other role: a customer-supplied optical component (an OpenSci product the customer supplies, or a component
      of their own). ``beam_category`` (a kernel optic category: ``MIRROR``, ``BEAMSPLITTER_PLATE``, ``LENS``,
      ``FILTER``, ``WINDOW``, ``WAVEPLATE``, ``POLARIZER``, or ``PORTS`` for an element placed by its published
      ``ports``) and ``size_mm`` (its outer size) make the nominal beam visit it as it would visit an OpenSci optic of
      that category (a mirror folds the beam, a plate splitter branches it, a ports element sends the beam that enters
      at ``IN`` out of every ``OUT`` port); ``envelope`` is its known geometry, reserved in the layout: OpenSci hardware
      keeps the inter-chain clearance from it and from the column below it down to the board top (the customer's own
      support), and the reserved space lies on the board. Without them the component is an anchor only (position,
      orientation, DOFs). ``powered``: a mirror with a curved surface (the category of a lens already says so): it is
      kept in a linked group like an OpenSci curved mirror, whose identity says so (``resolve.curved_mirror_sku``).
    """

    instance_id: str
    role: str
    position_design: Vec3
    rotation_design: Quat
    retained_dofs: tuple[DofRequest, ...]
    beam_category: str | None = None
    size_mm: float | None = None
    envelope: ReservedEnvelope | None = None
    ports: tuple[PortRequest, ...] = ()
    powered: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "rotation_design", q_unit(tuple(float(c) for c in self.rotation_design)))  # taken by its direction, q / |q|


@dataclass(frozen=True)
class ObservationRequest:
    """A Project observation plane (the virtual ``ObservationV1``): design intent, not hardware. It is a nominal
    beam-path endpoint (its aperture decides whether the traced beam reaches it) and is reported in the lab frame; it
    occupies no board space, need not lie on or above the board and never constrains the assembly."""

    observation_id: str
    position_design: Vec3
    rotation_design: Quat
    plane_corners: tuple[tuple[float, float], ...]  # aperture extreme points in the plane's own (u, v)

    def __post_init__(self) -> None:
        object.__setattr__(self, "rotation_design", q_unit(tuple(float(c) for c in self.rotation_design)))  # taken by its direction, q / |q|


@dataclass(frozen=True)
class AssemblyResolutionRequestV2:
    optics: tuple[OpticRequest, ...]
    externals: tuple[ExternalRequest, ...] = ()
    observations: tuple[ObservationRequest, ...] = ()
    max_board_size_mm: tuple[float, float] | None = None
    #: The customer's requested lab height of the design's z = 0 plane (the axis height of every optic drawn at z = 0).
    beam_height_mm: float | None = None


# ------------------------------------------------------------------------------------------------ result


@dataclass(frozen=True)
class RealizedPartV2:
    """One placed physical unit. ``member`` is the catalog identity's kernel view (None for the optic, which is the
    customer's own design selection realized by its OpenSci optical product)."""

    slot_key: str  # "c1.optic", "c1.mount", "c1.post", "c1.holder", "board"
    role: str  # OPTIC | OPTIC_MOUNT | POST | POST_HOLDER | BREADBOARD
    chain_key: str | None
    instance_id: str | None  # the design component this part realizes or supports
    member: KPost | KHolder | KMount | KBoard | None
    optic: OpticMechanicsV1 | None
    pose: Pose  # part frame in the lab
    bodies: tuple[YawBox, ...]  # collision bodies in the lab (yaw-only boxes); the optic has none of its own
    datums: dict[str, Pose]  # datum frames in the part frame
    support_parent: str | None
    support_relation: str | None  # SEATED_IN | MOUNTED_ON | INSERTED_INTO | STANDS_ON
    assembly_order: int
    visual: tuple[VisualPrimitiveV1, ...]
    color_rgb: tuple[float, float, float]
    approximate_envelope: bool

    @property
    def identity(self) -> str:
        return self.optic.optical_sku if self.optic is not None else self.member.identity  # type: ignore[union-attr]


@dataclass(frozen=True)
class RealizedMateV2:
    a: str
    datum_a: str
    b: str
    datum_b: str
    b_in_a: Pose
    interface_designation: str | None
    #: (kind, thread designation) when the joint is not the plain stud joint (``KMount.post_fastening``), else None
    fastener: tuple[str, str] | None = None


@dataclass(frozen=True)
class RealizedSettingV2:
    slot_key: str
    kind: str  # INSERTION_DEPTH | BASE_YAW | MOUNT_ORIENTATION_YAW | FIXATION_THREAD
    value: str
    unit: str  # mm | deg | NONE
    basis: str  # RESOLVED | PREVIEW_ASSUMPTION


@dataclass(frozen=True)
class RealizedFixationV2:
    slot_key: str
    mode: str  # SLOT
    hole: tuple[int, int]
    thread: str


@dataclass(frozen=True)
class DofResultV2:
    instance_id: str
    axis: str
    requested_control: str
    delivered_control: str  # MANUAL | NONE
    status: str  # DELIVERED | DELIVERED_AS_MANUAL_FALLBACK | NOT_DELIVERED
    lab_direction: Vec3
    design_direction: Vec3
    mechanism: str  # POST_HEIGHT_CLAMP | POST_ROTATION_CLAMP | BASE_SLOT_TRAVEL | TIP_TILT_MOUNT | TRANSLATION_MOUNT | ROTATION_MOUNT | NONE
    implemented_by: str | None  # slot key of the part providing the mechanism
    moved_part: str | None
    travel: tuple[float, float] | None
    travel_unit: str | None
    travel_basis: str | None  # RESOLVED | PREVIEW_ASSUMPTION
    adjustment: str | None  # FINE_ADJUSTER | CONTINUOUS_CLAMPED | COARSE_REPOSITION
    axis_offset_mm: float | None = None
    customer_supplied: bool = False
    description: str | None = None
    #: Why a requested DOF is not delivered (None when it is): ``NO_COMPATIBLE_HARDWARE`` (no compatible mount
    #: provides it), ``HARDWARE_DID_NOT_FIT`` (compatible hardware exists but no layout of the design accommodates it,
    #: so a simpler support was used) or ``SLOT_NOT_ALONG_AXIS`` (the base slot could not be aligned with it).
    not_delivered_reason: str | None = None


@dataclass(frozen=True)
class CheckV2:
    kind: str
    status: str  # PASS | NOT_RUN
    detail: str
    #: Structured facts of the check when it was not run in full (customer-safe), e.g. a beam trace stopped at a cap:
    #: ``{"complete": false, "paths_traced": n, "stopped": [{path_key, limit, value, at}, ...]}``. None otherwise.
    context: dict[str, Any] | None = None


@dataclass(frozen=True)
class ChainTraceV2:
    """How one support chain was decided: the chosen members and every alternative considered, with reasons."""

    chain_key: str
    instance_id: str
    optical_sku: str
    mount: str
    post: str
    holder: str
    beam_height_mm: float
    insertion_depth_mm: float
    hole: tuple[int, int]
    fork_yaw_deg: float
    requested_dofs: tuple[str, ...]
    mount_ranking: tuple[dict[str, Any], ...]
    stack_ranking: tuple[dict[str, Any], ...]
    rejected_mounts: tuple[dict[str, Any], ...]
    #: Customer-safe causes (failure kind, other component) that made the kernel fall back from hardware delivering
    #: more of the requested DOFs to this chain's mount (a fallback is reported, never silent).
    fallback_causes: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class BoardDecisionV2:
    board: KBoard
    outline_min_mm: tuple[float, float]
    outline_max_mm: tuple[float, float]
    footprint_min_mm: tuple[float, float]
    footprint_max_mm: tuple[float, float]
    edge_margin_mm: float
    considered: tuple[dict[str, Any], ...]
    external_sources: tuple[dict[str, Any], ...]

    @property
    def footprint_size_mm(self) -> tuple[float, float]:
        return (self.footprint_max_mm[0] - self.footprint_min_mm[0], self.footprint_max_mm[1] - self.footprint_min_mm[1])


@dataclass(frozen=True)
class BeamPathV2:
    path_key: str
    points_design: tuple[Vec3, ...]
    through: tuple[str, ...]  # instance ids / observation ids in path order
    wavelength_nm: float | None = None
    #: What the beam does at each point (``beam.STEP_*``, or ``PORT:<id>`` / ``SPLIT_PORT:<id>``: it leaves a ports
    #: element through that output); empty when not traced by ``trace_beam_paths``.
    steps: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReservedSpaceV2:
    """The lab space reserved for one customer-supplied component (its envelope and the column below it), as yaw-only
    boxes; no OpenSci body comes closer than the inter-chain clearance."""

    instance_id: str
    bodies: tuple[YawBox, ...]


@dataclass(frozen=True)
class AssemblyRealizationV2:
    design_yaw: Quat
    design_translation: Vec3  # z: the lab height of the design's z = 0 plane; each chain reports its optic's axis height
    parts: tuple[RealizedPartV2, ...]
    mates: tuple[RealizedMateV2, ...]
    settings: tuple[RealizedSettingV2, ...]
    fixations: tuple[RealizedFixationV2, ...]
    dofs: tuple[DofResultV2, ...]
    checks: tuple[CheckV2, ...]
    chains: tuple[ChainTraceV2, ...]
    board: BoardDecisionV2
    beam_paths: tuple[BeamPathV2, ...]
    optic_lab_poses: dict[str, Pose]
    external_lab_poses: dict[str, Pose]
    observation_lab_poses: dict[str, Pose]
    assumptions: tuple[str, ...] = field(default=())  # parameter paths resting on PREVIEW_ASSUMPTION
    #: the reserved spaces of the customer-supplied components with a known envelope, in request order
    reserved: tuple[ReservedSpaceV2, ...] = field(default=())

    @property
    def design_to_lab(self) -> Pose:
        return Pose(self.design_translation, self.design_yaw)

    def part(self, key: str) -> RealizedPartV2:
        return next(p for p in self.parts if p.slot_key == key)


# ------------------------------------------------------------------------------------------------ one optic, no layout


@dataclass(frozen=True)
class ChainJointV1:
    """One joint of a support chain without a layout: ``OPTIC_IN_MOUNT`` (``OD<d>-SEAT``), ``MOUNT_ON_POST`` (the
    mount's post thread; ``fastener`` when the mount is not fixed by the post's own stud), ``POST_IN_HOLDER``
    (``OD<d>-POST``, clamped at ``insertion_depth_mm``) and ``HOLDER_ON_SURFACE`` (the holder's fixing thread, one screw
    through its slot into a hole of the surface it stands on)."""

    joint: str
    interface_designation: str
    fastener: tuple[str, str] | None = None


@dataclass(frozen=True)
class OpticChainV1:
    """The support chain of one optic held at a stated beam height with no layout (``resolve_optic_chain``): the
    optic stands with its optical axis horizontal, its holder on a flat surface (a breadboard or table top); nothing
    about other optics, a board or a beam path was checked.

    ``beam_height_mm`` is the optical axis above that surface; it is closed by ``holder.height_mm + post.length_mm +
    mount.centre_height_mm - insertion_depth_mm``. ``height_travel_mm`` is how far the post can slide down / up in its
    holder from there (the insertion interval). ``reachable_axis_height_mm`` is the range of axis heights the chosen
    mount reaches with any post and holder of the catalog (within the policy bounds). ``dofs`` are the retained DOFs
    of the request matched to the chain's mechanisms (``implemented_by``: ``mount`` / ``post`` / ``holder``); the base
    slot travel along the optical axis holds only when the holder is fixed with its slot along the beam
    (``conditions``). ``fell_back``: a lower-ranked mount was chosen because the better-ranked ones cannot hold the
    optic at this height. ``trace`` records the ranking (catalog identities)."""

    instance_id: str
    optical_sku: str
    mount: KMount
    post: KPost
    holder: KHolder
    beam_height_mm: float
    incidence_deg: float
    insertion_depth_mm: float
    insertion_interval_mm: tuple[float, float]
    height_travel_mm: tuple[float, float]
    reachable_axis_height_mm: tuple[float, float]
    dofs: tuple[DofResultV2, ...]
    joints: tuple[ChainJointV1, ...]
    checks: tuple[CheckV2, ...]
    assumptions: tuple[str, ...]
    conditions: tuple[str, ...]
    fell_back: bool
    trace: dict[str, Any] = field(default_factory=dict, compare=False)


__all__ = [
    "RESOLVER_KERNEL_ID",
    "RESOLVER_KERNEL_VERSION",
    "AssemblyRealizationV2",
    "AssemblyResolutionRefusal",
    "AssemblyResolutionRequestV2",
    "BeamPathV2",
    "BoardDecisionV2",
    "ChainJointV1",
    "ChainPins",
    "ChainTraceV2",
    "CheckV2",
    "DofRequest",
    "DofResultV2",
    "ExternalRequest",
    "ObservationRequest",
    "OpticChainV1",
    "OpticRequest",
    "PortRequest",
    "RealizedFixationV2",
    "RealizedMateV2",
    "RealizedPartV2",
    "RealizedSettingV2",
    "ReservedEnvelope",
    "ReservedSpaceV2",
    "ResolutionBudget",
]
