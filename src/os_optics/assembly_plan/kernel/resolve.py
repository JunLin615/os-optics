"""The data-driven multi-optic assembly kernel.

``resolve_assembly(request, catalog)`` realizes every OpenSci optic of one design as one support chain

    optic -> mount -> post -> post holder (integrated fork) -> shared breadboard

and the whole design as ONE coherent laboratory assembly. Every choice is a rule over the engineering parameters of
the mechanical catalog's identities, so each compatible identity is eligible automatically.

Steps (all deterministic: sorted iteration, explicit tie-breaks, no randomness, no I/O):

1. **Frame.** The design frame is +Z up (the Optical Simulation View's frame). Resolution may only turn the design
   about +Z and translate it. Every optic's optical axis (local +Z) must be horizontal, and a non-round optic must
   stand with one of its edges vertical; otherwise the design is refused (``UNSUPPORTED_OPTIC_ORIENTATION``), never
   silently re-oriented. The yaw is the smallest turn that lays the most nominal beam segments on board grid
   directions (a design drawn along X/Y is not turned).
2. **Mounts (DOF-aware).** A mount is compatible with an optic iff it holds that shape and size, its thickness range
   admits the optic, it leaves an aperture for a transmissive optic, and a post stud fits its post interface.
   Candidates are ranked by the number of the optic's requested retained DOFs the complete chain would deliver
   (the mount's own adjusters plus the post height, post rotation and base slot every chain has), then by the fewest
   unrequested adjusters, then by evidence quality and identity.
3. **Beam height.** One design-to-lab lift ``t_z`` for the whole design (the lab height of the design's z = 0 plane). A
   chain can reach an axis height iff some compatible (post, holder) pair gives an insertion depth inside the holder's
   insertion interval. The lift that puts the first optic nearest the preferred beam height (or exactly the customer's
   requested design-plane height) and that every chain reaches is chosen.
4. **Posts and holders.** Per chain, the pair that leaves the most height travel in the worse direction (the smaller
   of "insertion - interval minimum" and "interval maximum - insertion"), then the shorter (stiffer) post, the lower
   holder and identity.
5. **Layout and board.** Boards are tried smallest first; the first on which the chosen hardware delivers every
   requested DOF it can provide is selected (a fork that cannot reach a grid hole along its beam on a narrow board
   loses its axial slide, so a larger board may deliver more); if no board delivers them all, the smallest board with
   the most delivered DOFs. The design is translated so that as many chains as
   possible stand on a grid row along their optical axis, centred on the board. Each holder's fork is turned toward a
   free grid hole inside its slot, preferring one along the optical axis (that gives the chain an axial slide).
   Bodies of different chains may not intersect, the nominal beam may not pass through hardware it does not go
   through, every body must lie inside the board less its edge margin, and each hole fixes one holder. The chains
   are placed depth first: when a chain finds no hole, the earlier chains whose forks or holes stood in
   its way are revisited on their other holes (``_DepthFirst``). A first-fit search (every chain on its first hole,
   no revisits) runs first with a share of the budget, so that a design it lays out keeps exactly that layout -- unless
   a smaller board took the design's footprint but not the first-fit placement: the complete search then tries those
   smaller boards with the same hardware (``_on_the_smallest_board``, a share of the budget) and the smallest layout
   that delivers as many requested DOFs is selected.
6. **DOFs.** Each requested DOF's lab direction is matched to a mechanism of its own chain whose axis is parallel:
   mount adjusters first, then post height/rotation, then the base slot. A NUMERICAL request met by a manual
   mechanism is ``DELIVERED_AS_MANUAL_FALLBACK``; no mechanism: ``NOT_DELIVERED``.

A chain that cannot be completed falls back to its next-ranked mount; if none completes, the design is refused
with the reason. Nothing is invented: a missing fact keeps an identity out of the catalog, and an assumed value is
reported in ``assumptions`` and disclosed downstream.

Customer-supplied components (``ExternalRequest``) are external design anchors: no support chain. A light source
starts the nominal beam; a customer-supplied optical component of known category is visited by it like an OpenSci optic,
and its known envelope is RESERVED: every OpenSci body keeps the inter-chain clearance from it and from the column below it
down to the board top (the customer's own support), and the reserved space must lie on the board like any body.

Observation planes (``ObservationRequest``, the Project's virtual ``ObservationV1``) are design intent, not hardware:
they are nominal beam-path endpoints for the beam trace and are reported in the lab frame, but they never occupy the
board, never need to lie on or above it and never move the beam height or refuse a layout. A physical detector will
occupy the board through its own mechanical realization, not through a virtual plane's aperture.

The search is deterministic, bounded and fail-closed: candidate mounts, stacks, translations, fork turns and boards
are tried in a fixed, finite order (``ResolverPolicyV1.max_translations``, the ranked mount lists, the standard board
list), and at one translation every combination of the chains' fixing holes is tried or ruled out by a conflict (up
to ``ResolverPolicyV1.max_layout_backtracks`` revisits). A refusal means "no realization found within this search and its budget" under
the kernel's own rules (containment, clearance, beam, one hole per holder); it is not a proof that no physically
possible assembly exists.
"""

from __future__ import annotations

import heapq
import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from .beam import AXIAL_COSINE, PLATE_CATEGORIES, PORTED_CATEGORIES, trace_beam_paths
from .catalog import (
    FACT_BASES,
    KBoard,
    KernelCatalog,
    KHolder,
    KMember,
    KMount,
    KPost,
    MechanismKindV1,
    MountKindV1,
    OpticShapeV1,
    PublicBasisV1,
    ResolverPolicyV1,
)
from .geometry import (
    IDENTITY,
    Pose,
    Quat,
    Vec3,
    YawBox,
    add,
    bounds_apart,
    dot,
    norm,
    overlap_depth,
    plan_bounds,
    q_conj,
    q_mul,
    q_normalize,
    rotate,
    scale,
    segment_box_hit,
    snap_quat,
    snap_vec,
    sub,
    unit,
    yaw_direction,
    yaw_quaternion,
)
from .models import (
    AssemblyRealizationV2,
    AssemblyResolutionRefusal,
    AssemblyResolutionRequestV2,
    BeamPathV2,
    BoardDecisionV2,
    ChainJointV1,
    ChainPins,
    ChainTraceV2,
    CheckV2,
    DofResultV2,
    ExternalRequest,
    OpticChainV1,
    OpticRequest,
    RealizedFixationV2,
    RealizedMateV2,
    RealizedPartV2,
    RealizedSettingV2,
    ReservedSpaceV2,
    ResolutionBudget,
)

# ---- named numeric constants of this module (tolerances, digits and work caps)
#: Bisection steps of the largest clear incidence.
_BISECTION_STEPS = 40
#: The largest clear incidence is reported rounded down to a tenth of a degree.
_DECIMAL = 10.0
#: The base of the decimal rounding up of a reported number.
_DECIMAL_BASE = 10
#: Lengths (mm) and cosines closer than this are equal (float noise).
_EPS = 1e-09
#: Half a turn (deg): a full rotation range is [-180, 180].
_HALF_TURN_DEG = 180.0
#: Bisection steps of a lateral clearance.
_LATERAL_STEPS = 20
#: The largest incidence the clear-incidence bisection tries (deg).
_MAX_SEARCHED_INCIDENCE_DEG = 89.9
#: A spacing or a cosine difference below this is no change (mm, or a cosine).
_MICRO = 1e-06
#: Budget shares are percentages.
_PERCENT = 100
#: Steps per board pitch a position search moves by.
_PITCH_FRACTIONS = 10.0
#: A quarter turn (deg).
_QUARTER_TURN_DEG = 90.0
#: Mount candidates a chain trace lists.
_RANKING_SHOWN = 12
#: Rejected mounts a chain trace lists.
_REJECTIONS_SHOWN = 24
#: Decimals a lab position keeps (mm).
_SNAP_DIGITS = 9
#: A direction component or a difference this small is zero.
_TINY = 1e-12
#: Decimals a unit-direction component keeps.
_UNIT_DIGITS = 12


SIZE_TOL = 1e-6
_AXES: dict[str, Vec3] = {"Tx": (1.0, 0.0, 0.0), "Ty": (0.0, 1.0, 0.0), "Tz": (0.0, 0.0, 1.0),
                          "Rx": (1.0, 0.0, 0.0), "Ry": (0.0, 1.0, 0.0), "Rz": (0.0, 0.0, 1.0)}

#: Slot keys are distinctive (``rp:c1:mount``, ``rp:board``): no catalog word contains one, so a slot key never
#: passes for a part identity.
BOARD_KEY = "rp:board"


def _no_charge(checks: int) -> None:
    """The charge of work that is not budgeted (a caller that holds no budget)."""


def pk(chain_key: str, role: str) -> str:
    return f"rp:{chain_key}:{role}"


def chain_of(slot_key: str) -> str | None:
    parts = slot_key.split(":")
    return parts[1] if len(parts) == 3 else None


def role_of(slot_key: str) -> str:
    return slot_key.rsplit(":", 1)[1]

_DOF_ORDER = ("Tx", "Ty", "Tz", "Rx", "Ry", "Rz")
_MOUNT_SURPLUS = {MountKindV1.FIXED: 0, MountKindV1.CUBE_PLATFORM: 0, MountKindV1.ROTATION: 1,
                  MountKindV1.KINEMATIC: 2, MountKindV1.XY_TRANSLATION: 2}
_UP: Vec3 = (0.0, 0.0, 1.0)
#: A tip/tilt mount's clear aperture a reflector does not need is a surplus too, below one adjuster: a
#: kinematic mirror mount holds the reflector with its face at the front; a kinematic lens mount's seat and bore are
#: modelled for an optic resting on its lip. (Fixed mounts keep their order: a fixed lens mount is the long-standing
#: plain support of a mirror whose adjusters do not fit.)
_APERTURE_SURPLUS = 0.5


def _surplus(mount: KMount, optic: OpticRequest) -> float:
    """The unrequested capability of ``mount`` for ``optic`` (lower is preferred): its kind's adjusters, plus the clear
    aperture of a tip/tilt mount when the optic is a reflector."""

    aperture = (_APERTURE_SURPLUS if mount.mount_kind is MountKindV1.KINEMATIC and mount.holds.transmissive
                and not optic.mechanics.transmissive else 0.0)
    return _MOUNT_SURPLUS[mount.mount_kind] + aperture


def _bulk(mount: KMount) -> float:
    """Between otherwise equal candidates, the smaller transmissive tip/tilt mount first (its envelope volume, mm^3):
    the minimal adequate one, not the first by identity. 0 for every other mount, whose order is kept."""

    if mount.mount_kind is not MountKindV1.KINEMATIC or not mount.holds.transmissive:
        return 0.0
    lo, hi = mount.envelope.min_mm, mount.envelope.max_mm
    return round((hi[0] - lo[0]) * (hi[1] - lo[1]) * (hi[2] - lo[2]), 3)


# ------------------------------------------------------------------------------------------------ helpers


def _fmt(value: float, digits: int = 6) -> str:
    text = f"{round(value, digits) + 0.0:.{digits}f}".rstrip("0")
    return text + "0" if text.endswith(".") else text


def _deg(direction: tuple[float, float]) -> float:
    x, y = direction
    exact = {(1.0, 0.0): 0.0, (0.0, 1.0): _QUARTER_TURN_DEG, (-1.0, 0.0): _HALF_TURN_DEG, (0.0, -1.0): -_QUARTER_TURN_DEG}
    key = (round(x, _UNIT_DIGITS) + 0.0, round(y, _UNIT_DIGITS) + 0.0)
    if key in exact:
        return exact[key]
    return round(math.degrees(math.atan2(y, x)), 6) + 0.0


def _evidence_rank(member: KMember) -> tuple[int, int]:
    """Lower is better: no preview assumption, then reviewed-reference facts, then the rest; within one rank, an
    identity with reviewed-reference facts before one without."""

    bases = member.bases
    reference = 0 if PublicBasisV1.REVIEWED_REFERENCE in bases else 1
    if PublicBasisV1.PREVIEW_ASSUMPTION in bases:
        return 2, reference
    if reference == 0:
        return 0, 0
    return 1, 1


def _is_fact(member: KMember, *parameters: str) -> bool:
    for parameter in parameters:
        basis = member.basis_of(parameter)
        if basis is not None and basis not in FACT_BASES:
            return False
    return True


def _parallel(a: Vec3, b: Vec3, tol: float) -> float:
    """+1 / -1 when a and b are (anti)parallel unit vectors within ``tol``, else 0."""

    d = dot(a, b)
    if abs(abs(d) - 1.0) <= tol:
        return 1.0 if d > 0 else -1.0
    return 0.0


# ------------------------------------------------------------------------------------------------ frame


@dataclass(frozen=True)
class _Optic:
    request: OpticRequest
    chain_key: str
    rotation_lab: Quat  # yaw * the DOF frame of the design rotation (``dof_frame``)
    axis_lab: Vec3  # horizontal unit
    position_rel: Vec3  # yaw * design position (no translation yet)


def _design_yaw(optics: Sequence[OpticRequest], paths: Sequence[BeamPathV2], charge: Callable[[int], None] = _no_charge) -> Quat:
    """The yaw that lays the most nominal beam segments (else transmissive optic axes, else optic axes) on board grid
    directions; ties go to the smallest turn, then the first direction found. A design drawn along X/Y is not turned.
    Each candidate turn is ``charge``-d its count over every direction before it is counted."""

    directions: list[tuple[float, float]] = []
    for path in paths:
        for a, b in zip(path.points_design, path.points_design[1:]):
            d = (b[0] - a[0], b[1] - a[1])
            n = math.sqrt(d[0] * d[0] + d[1] * d[1])
            if n > _EPS:
                directions.append((d[0] / n, d[1] / n))
    if not directions:
        chosen = [o for o in optics if o.mechanics.transmissive] or list(optics)
        for optic in chosen:
            axis = rotate(optic.rotation_design, (0.0, 0.0, 1.0))
            n = math.sqrt(axis[0] ** 2 + axis[1] ** 2)
            directions.append((axis[0] / n, axis[1] / n))
    grid = ((1.0, 0.0), (0.0, 1.0), (-1.0, 0.0), (0.0, -1.0))
    candidates: list[tuple[float, float]] = [(1.0, 0.0)]
    for a in directions:
        target = max(grid, key=lambda t: a[0] * t[0] + a[1] * t[1])
        candidates.append((a[0] * target[0] + a[1] * target[1], a[0] * target[1] - a[1] * target[0]))
    candidates = list(dict.fromkeys(candidates))  # an equal later candidate never wins (ties go to the first)

    def aligned(c: float, s: float) -> int:
        charge(len(directions))
        count = 0
        for a in directions:
            x, y = a[0] * c - a[1] * s, a[0] * s + a[1] * c
            count += abs(x) <= _EPS or abs(y) <= _EPS
        return count

    best = max(enumerate(candidates), key=lambda item: (aligned(*item[1]), round(item[1][0], _UNIT_DIGITS), -item[0]))[1]
    if abs(best[1]) <= _TINY and best[0] > 0:
        return IDENTITY
    return yaw_quaternion(best)


def _check_orientation(optic: OpticRequest, policy: ResolverPolicyV1) -> None:
    axis = rotate(optic.rotation_design, (0.0, 0.0, 1.0))
    if abs(axis[2]) > policy.horizontal_tolerance:
        raise AssemblyResolutionRefusal(
            "UNSUPPORTED_OPTIC_ORIENTATION",
            "the design frame is +Z up; post-mounted hardware holds optics with a horizontal optical axis, and this optic's "
            "optical axis is not horizontal",
            details={"instance_id": optic.instance_id, "optical_axis_in_design": [round(v, _SNAP_DIGITS) for v in axis]},
        )
    if optic.mechanics.shape is not OpticShapeV1.ROUND:
        x = rotate(optic.rotation_design, (1.0, 0.0, 0.0))
        y = rotate(optic.rotation_design, (0.0, 1.0, 0.0))
        if max(abs(x[2]), abs(y[2])) < 1.0 - policy.horizontal_tolerance:
            raise AssemblyResolutionRefusal(
                "UNSUPPORTED_OPTIC_ORIENTATION",
                "a non-round optic must stand with one of its edges vertical (local X or Y along the design +Z)",
                details={"instance_id": optic.instance_id},
            )


# ------------------------------------------------------------------------------------------------ mechanisms


@dataclass(frozen=True)
class _Mechanism:
    code: str  # contract mechanism code
    motion: str  # TRANSLATION | ROTATION
    axis: Vec3  # lab unit axis
    provider: str  # "mount" | "post" | "holder"
    travel: tuple[float, float] | None
    unit: str
    basis: str  # RESOLVED | PREVIEW_ASSUMPTION
    adjustment: str
    description: str
    axis_point: Vec3 | None = None  # for rotations: a point on the axis


def _mount_rotation(axis_lab: Vec3) -> Quat:
    return yaw_quaternion((axis_lab[0], axis_lab[1]))


def _mount_mechanisms(mount: KMount, mount_rotation: Quat) -> list[_Mechanism]:
    y = rotate(mount_rotation, (0.0, 1.0, 0.0))
    x = rotate(mount_rotation, (1.0, 0.0, 0.0))
    out: list[_Mechanism] = []
    for mech in mount.mechanisms:
        basis = "RESOLVED" if _is_fact(mount, f"mechanism.{mech.kind.value}.travel") else "PREVIEW_ASSUMPTION"
        t = (-mech.travel, mech.travel)
        if mech.kind is MechanismKindV1.TIP_TILT:
            out.append(_Mechanism("TIP_TILT_MOUNT", "ROTATION", snap_vec(y), "mount", t, "deg", basis, mech.adjustment,
                                  "Tilt about the horizontal axis across the beam with the mount's adjusters."))
            out.append(_Mechanism("TIP_TILT_MOUNT", "ROTATION", _UP, "mount", t, "deg", basis, mech.adjustment,
                                  "Tilt about the vertical axis with the mount's adjusters."))
        elif mech.kind is MechanismKindV1.TRANSVERSE_TRANSLATION:
            out.append(_Mechanism("TRANSLATION_MOUNT", "TRANSLATION", snap_vec(y), "mount", t, "mm", basis, mech.adjustment,
                                  "Horizontal translation across the beam with the mount's adjuster."))
            out.append(_Mechanism("TRANSLATION_MOUNT", "TRANSLATION", _UP, "mount", t, "mm", basis, mech.adjustment,
                                  "Vertical translation with the mount's adjuster."))
        elif mech.kind is MechanismKindV1.AXIAL_ROTATION:
            full = mech.travel >= _HALF_TURN_DEG
            out.append(_Mechanism("ROTATION_MOUNT", "ROTATION", snap_vec(x), "mount", (-_HALF_TURN_DEG, _HALF_TURN_DEG) if full else t, "deg",
                                  basis, "CONTINUOUS_CLAMPED" if full else mech.adjustment,
                                  "Rotation of the optic about its optical axis in the rotation mount."))
    return out


def dof_frame(rotation_design: Quat) -> Quat:
    """The frame of an optic's retained DOFs: its design rotation turned about its own optical axis (local
    +Z, horizontal) to the nearest quarter turn at which local +X and +Y are horizontal and vertical.

    A round optic sits in the same mount at any roll about its axis (a waveplate's or polarizer's roll sets its optical
    axis, not its mount), so its Rx/Ry and Tx/Ty pairs are the tilts and shifts across its axis whatever the roll it
    was drawn with: a mirror drawn with a 45 degree or a shortest-arc roll gets the same tip/tilt mount, with the same
    DOF directions, as one drawn upright. Only DOF matching uses this frame; the optic's pose is the drawn one. A roll exactly midway between two quarter turns takes the one with local +X
    horizontal. A non-round optic stands with an edge vertical (``_check_orientation``): its frame is that quarter turn.
    A design rotation at a quarter turn is returned unchanged."""

    z = rotate(rotation_design, (0.0, 0.0, 1.0))
    x = rotate(rotation_design, (1.0, 0.0, 0.0))
    across = (-z[1], z[0], 0.0)  # +Z x axis: horizontal, across the optical axis
    n = math.sqrt(across[0] ** 2 + across[1] ** 2)
    if n <= _TINY:  # pragma: no cover - a vertical optical axis is refused before
        return rotation_design
    e1 = (across[0] / n, across[1] / n, 0.0)
    e2 = (z[1] * e1[2] - z[2] * e1[1], z[2] * e1[0] - z[0] * e1[2], z[0] * e1[1] - z[1] * e1[0])  # axis x e1: up
    roll = math.atan2(dot(x, e2), dot(x, e1))
    quarters = roll / (math.pi / 2.0)
    low = math.floor(quarters)
    if abs(quarters - low - 0.5) <= _EPS:
        nearest = low if low % 2 == 0 else low + 1  # midway: local +X horizontal (an even number of quarter turns)
    else:
        nearest = round(quarters)
    delta = nearest * (math.pi / 2.0) - roll
    if abs(delta) <= _TINY:
        return rotation_design
    return q_normalize(q_mul(rotation_design, (math.cos(delta / 2.0), 0.0, 0.0, math.sin(delta / 2.0))))


def _lab_direction(rotation_lab: Quat, axis: str) -> Vec3:
    return snap_vec(rotate(rotation_lab, _AXES[axis]))


def _delivered_by(requested: Iterable[tuple[str, str]], rotation_lab: Quat, mechanisms: Sequence[_Mechanism], tol: float) -> list[str]:
    out = []
    for axis, _control in requested:
        motion = "TRANSLATION" if axis.startswith("T") else "ROTATION"
        u = _lab_direction(rotation_lab, axis)
        if any(m.motion == motion and _parallel(m.axis, u, tol) for m in mechanisms):
            out.append(axis)
    return out


def _generic_stack_mechanisms(axis_lab: Vec3) -> list[_Mechanism]:
    """The adjustments every post/holder/fork chain offers (travel filled in once the chain is solved)."""

    return [
        _Mechanism("POST_HEIGHT_CLAMP", "TRANSLATION", _UP, "post", None, "mm", "RESOLVED", "CONTINUOUS_CLAMPED", ""),
        _Mechanism("POST_ROTATION_CLAMP", "ROTATION", _UP, "post", None, "deg", "RESOLVED", "CONTINUOUS_CLAMPED", ""),
        _Mechanism("BASE_SLOT_TRAVEL", "TRANSLATION", axis_lab, "holder", None, "mm", "RESOLVED", "COARSE_REPOSITION", ""),
    ]


# ------------------------------------------------------------------------------------------------ mounts


@dataclass(frozen=True)
class Incidence:
    """How the nominal beam meets one optic: the cosine of the largest angle between a beam segment at the optic and
    the optic's axis, and the largest distance (mm) of a visit point from the optic's centre, across its axis."""

    cos_theta: float = 1.0
    offset_mm: float = 0.0

    @property
    def degrees(self) -> float:
        return math.degrees(math.acos(max(-1.0, min(1.0, self.cos_theta))))


def _incidences(optics: Sequence[OpticRequest], paths: Sequence[BeamPathV2]) -> dict[str, Incidence]:
    """Per optic instance: the worst incidence of the nominal beam (angle to the optic axis, offset from its centre)."""

    by_id = {o.instance_id: o for o in optics}
    worst: dict[str, Incidence] = {}
    for path in paths:
        points = path.points_design
        for k, name in enumerate(path.through):
            optic = by_id.get(name)
            if optic is None:
                continue
            normal = rotate(optic.rotation_design, (0.0, 0.0, 1.0))
            off = sub(points[k], optic.position_design)
            along = dot(off, normal)
            radial = math.sqrt(max(0.0, dot(off, off) - along * along))
            current = worst.get(name, Incidence())
            cos_theta = current.cos_theta
            # a ports element (a cube splitter) is met by the segment entering it; its outputs leave through its ports
            ported = bool(optic.ports) or optic.mechanics.category in PORTED_CATEGORIES
            for a, b in ((k - 1, k),) if ported else ((k - 1, k), (k, k + 1)):
                if a < 0 or b >= len(points) or (ported and path.through[a] == name):
                    continue
                d = sub(points[b], points[a])
                n = norm(d)
                if n > _EPS:
                    cos_theta = min(cos_theta, abs(dot(d, normal)) / n)
            worst[name] = Incidence(cos_theta, max(current.offset_mm, radial))
    return worst


def oblique_basis(optic: OpticRequest, mount: KMount) -> tuple[str, ...]:
    """The bore facts of ``mount`` the oblique-clearance check of ``optic`` reads but the mount does not record, so it
    uses a stand-in: ``clear_aperture_mm`` (``policy.fallback_clear_aperture_ratio`` of the held size),
    ``retainer_depth_mm`` and ``rear_depth_mm`` (the mount envelope; the rear wall is read for a transmissive optic
    only). Empty: the judgment rests on recorded bore data."""

    holds = mount.holds
    assumed = []
    if holds.clear_aperture_mm is None:
        assumed.append("clear_aperture_mm")
    if holds.retainer_depth_mm is None:
        assumed.append("retainer_depth_mm")
    if optic.mechanics.transmissive and holds.rear_depth_mm is None:
        assumed.append("rear_depth_mm")
    return tuple(assumed)


def _aperture_depths(optic: OpticRequest, mount: KMount) -> tuple[tuple[float, float], tuple[float, float]]:
    """((glass, air) in front, (glass, air) behind): how far (mm) a ray travels along the optical axis, from the optic's
    reference plane (its design origin, where the beam visits it), through the optic's own glass and then through the
    mount's bore to the end of the bore wall, in front of and behind the reference plane. Recorded retainer / rear
    depths are used when present, otherwise the mount envelope (a conservative bound: the envelope is the whole body)."""

    mech = optic.mechanics
    holds = mount.holds
    origin_x = _optic_origin_in_mount(optic, mount)[0]
    front_face = origin_x + mech.axial_max_mm
    rear_face = origin_x + mech.axial_min_mm
    seat_x = mount.seat_stop_x_mm if mount.seat_stop_x_mm is not None else 0.0
    front_end = seat_x + holds.retainer_depth_mm if holds.retainer_depth_mm is not None else mount.envelope.max_mm[0]
    rear_end = seat_x - holds.rear_depth_mm if holds.rear_depth_mm is not None else mount.envelope.min_mm[0]
    return (max(0.0, mech.axial_max_mm), max(0.0, front_end - front_face)), (max(0.0, -mech.axial_min_mm), max(0.0, rear_face - rear_end))


def oblique_clearance(optic: OpticRequest, mount: KMount, incidence: Incidence, policy: ResolverPolicyV1, *,
                      cos_theta: float | None = None, lenient: bool = False) -> float:
    """The worst lateral clearance (mm; negative: clipped) between the beam and the mount's bore at the actual
    incidence.

    A ray meeting the optic ``offset`` from its centre at angle theta to its axis drifts sideways by ``tan`` of its
    angle times the axial distance it travels before it leaves the bore: inside the glass at the refracted angle (index
    ``policy.oblique_glass_index``), in the bore at theta. With a footprint ``w`` (``w / (2 cos theta)`` wide across the
    bore) it must stay inside the clear radius. The front wall counts for every optic (the beam arrives there and a
    reflector sends it back out there); the rear wall too for a transmissive optic (the beam leaves through it). For a
    centred beam and a thin optic this is the effective aperture ``CA cos(theta) - 2 depth sin(theta)`` of a tilted bore.

    Strict (default): the recorded clear aperture, else ``policy.fallback_clear_aperture_ratio`` of the held size, and
    the policy footprint. ``lenient``: the beam centre line against a bore as wide as the optic itself -- what certainly
    hits mount material when the mount's aperture is not recorded."""

    holds = mount.holds
    c = incidence.cos_theta if cos_theta is None else cos_theta
    if c <= _MICRO:
        return -math.inf
    s = math.sqrt(max(0.0, 1.0 - c * c))
    if lenient:
        clear_radius = holds.size_mm / 2.0
        half_footprint = 0.0
    else:
        clear_radius = (holds.clear_aperture_mm if holds.clear_aperture_mm is not None
                        else holds.size_mm * policy.fallback_clear_aperture_ratio) / 2.0
        half_footprint = policy.oblique_beam_footprint_mm / (2.0 * c)
    sin_in = s / policy.oblique_glass_index
    tan_in = sin_in / math.sqrt(1.0 - sin_in * sin_in)
    front, rear = _aperture_depths(optic, mount)
    sides = (front, rear) if optic.mechanics.transmissive else (front,)
    return min(clear_radius - (incidence.offset_mm + glass * tan_in + air * s / c + half_footprint) for glass, air in sides)


def oblique_status(optic: OpticRequest, mount: KMount, incidence: Incidence, policy: ResolverPolicyV1) -> str:
    """``CLEAR`` (the strict check passes), ``UNVERIFIED`` (only the lenient check passes and the mount's aperture is
    not recorded: usable, ranked after clear mounts, disclosed as an assumption) or ``CLIPPED`` (incompatible)."""

    if oblique_clearance(optic, mount, incidence, policy) >= 0:
        return "CLEAR"
    if mount.holds.clear_aperture_mm is None and oblique_clearance(optic, mount, incidence, policy, lenient=True) >= 0:
        return "UNVERIFIED"
    return "CLIPPED"


def _largest_clear_incidence(optic: OpticRequest, mount: KMount, incidence: Incidence, policy: ResolverPolicyV1) -> float:
    """The largest incidence (deg, rounded down to 0.1 deg) at which ``mount`` still passes the beam footprint."""

    if oblique_clearance(optic, mount, incidence, policy, cos_theta=1.0) < 0:
        return 0.0
    lo, hi = 0.0, _MAX_SEARCHED_INCIDENCE_DEG
    for _ in range(_BISECTION_STEPS):
        mid = (lo + hi) / 2.0
        if oblique_clearance(optic, mount, incidence, policy, cos_theta=math.cos(math.radians(mid))) >= 0:
            lo = mid
        else:
            hi = mid
    return math.floor(lo * _DECIMAL) / _DECIMAL


#: A ranking note (internal trace): the mount passes the beam at its incidence only on assumed aperture data.
OBLIQUE_CLEARANCE_UNVERIFIED = "OBLIQUE_CLEARANCE_UNVERIFIED"


def mount_incompatibility(optic: OpticRequest, mount: KMount, posts: Sequence[KPost], *, incidence: Incidence | None = None,
                          policy: ResolverPolicyV1 | None = None) -> str | None:
    """Why ``mount`` cannot hold ``optic`` (a stable reason code), or None if it can. Given the beam's ``incidence`` on
    the optic (and the policy), a mount whose bore or retainer would clip the beam footprint is incompatible
    (``BEAM_CLIPPED_AT_INCIDENCE``)."""

    mech = optic.mechanics
    holds = mount.holds
    if holds.shape is not mech.shape:
        return "SHAPE_MISMATCH"
    if abs(holds.size_mm - mech.size_mm) > holds.size_tolerance_mm + SIZE_TOL:
        return "SIZE_MISMATCH"
    if holds.thickness_min_mm is None and holds.thickness_max_mm is None and mech.shape is not OpticShapeV1.CUBE:
        return "MOUNT_THICKNESS_RANGE_UNKNOWN"
    if holds.thickness_min_mm is not None and mech.thickness_mm < holds.thickness_min_mm - SIZE_TOL:
        return "OPTIC_TOO_THIN"
    if holds.thickness_max_mm is not None and mech.thickness_mm > holds.thickness_max_mm + SIZE_TOL:
        return "OPTIC_TOO_THICK"
    if mech.transmissive and not holds.transmissive:
        return "NO_CLEAR_APERTURE"
    if not any(p.top_thread == mount.post_thread for p in posts):
        return "NO_POST_FOR_INTERFACE"
    if (incidence is not None and policy is not None and mech.shape is not OpticShapeV1.CUBE
            and oblique_status(optic, mount, incidence, policy) == "CLIPPED"):
        return "BEAM_CLIPPED_AT_INCIDENCE"
    return None


def rank_mounts(optic: _Optic, catalog: KernelCatalog,
                incidence: Incidence | None = None) -> tuple[list[tuple[tuple, KMount, list[str]]], list[dict[str, Any]]]:
    """Compatible mounts, best first: most requested retained DOFs delivered (mount adjusters + the generic post/slot
    mechanisms), fewest unrequested adjusters (for a reflector, a tip/tilt mount's unneeded clear aperture counts too),
    evidence, identity. Also the near-miss rejections (with reasons)."""

    requested = [(d.axis, d.control) for d in optic.request.retained_dofs]
    ranked: list[tuple[tuple, KMount, list[str]]] = []
    rejected: list[dict[str, Any]] = []
    unverified: set[str] = set()
    tol = catalog.policy.parallel_tolerance
    rotation = _mount_rotation(optic.axis_lab)
    pinned = optic.request.pins.mount if optic.request.pins is not None else None
    for mount in sorted(catalog.mounts, key=lambda m: m.identity):
        if pinned is not None and mount.identity != pinned:
            continue
        reason = mount_incompatibility(optic.request, mount, catalog.posts, incidence=incidence, policy=catalog.policy)
        if reason is not None:
            if reason not in ("SHAPE_MISMATCH", "SIZE_MISMATCH"):  # only near misses are worth recording
                entry: dict[str, Any] = {"mount": mount.identity, "reason": reason}
                if reason == "BEAM_CLIPPED_AT_INCIDENCE" and incidence is not None:
                    entry["max_incidence_deg"] = _largest_clear_incidence(optic.request, mount, incidence, catalog.policy)
                    # the basis of the judgment: the computed clearance and the bore facts that were stand-ins
                    clearance = oblique_clearance(optic.request, mount, incidence, catalog.policy)
                    if math.isfinite(clearance):
                        entry["clearance_mm"] = round(clearance, 3)
                    entry["assumed_mount_facts"] = list(oblique_basis(optic.request, mount))
                rejected.append(entry)
            continue
        mechanisms = _mount_mechanisms(mount, rotation) + _generic_stack_mechanisms(optic.axis_lab)
        delivered = _delivered_by(requested, optic.rotation_lab, mechanisms, tol)
        key = (-len(delivered), _surplus(mount, optic.request), _evidence_rank(mount), mount.identity)
        if (incidence is not None and optic.request.mechanics.shape is not OpticShapeV1.CUBE
                and oblique_status(optic.request, mount, incidence, catalog.policy) == "UNVERIFIED"):
            unverified.add(mount.identity)
            rejected.append({"mount": mount.identity, "reason": OBLIQUE_CLEARANCE_UNVERIFIED})
        ranked.append((key, mount, delivered))
    # a mount whose clearance at the incidence rests on an assumption comes after every clear one delivering as much
    ranked.sort(key=lambda item: (item[0][0], item[1].identity in unverified, *item[0][1:-1], _bulk(item[1]), item[0][-1]))
    return ranked, rejected


# ------------------------------------------------------------------------------------------------ stacks


@dataclass(frozen=True)
class _Stack:
    post: KPost
    holder: KHolder
    depth: float
    depth_max: float

    @property
    def depth_min(self) -> float:
        return self.holder.insertion_min_mm


def _stack_pairs(mount: KMount, catalog: KernelCatalog, pins: ChainPins | None = None) -> list[tuple[KPost, KHolder]]:
    """The (post, holder) pairs that fit ``mount``: the post's stud enters its post interface and the holder clamps
    the post's diameter; ``pins`` keeps only a plan's pinned post / holder."""

    posts = [p for p in catalog.posts if p.top_thread == mount.post_thread
             and (pins is None or pins.post is None or p.identity == pins.post)]
    holders = [h for h in catalog.holders if pins is None or pins.holder is None or h.identity == pins.holder]
    return [(p, h) for p in sorted(posts, key=lambda p: p.identity) for h in sorted(holders, key=lambda h: h.identity)
            if abs(p.diameter_mm - h.post_diameter_mm) <= SIZE_TOL]


def _height_intervals(mount: KMount, catalog: KernelCatalog, pins: ChainPins | None = None) -> list[tuple[float, float]]:
    """Axis heights (lab z of the optical axis) the mount can be held at."""

    out = []
    for post, holder in _stack_pairs(mount, catalog, pins):
        hi_depth = min(holder.insertion_max_mm, post.length_mm)
        if hi_depth < holder.insertion_min_mm:
            continue
        top = holder.height_mm + post.length_mm + mount.centre_height_mm
        out.append((top - hi_depth, top - holder.insertion_min_mm))
    return out


def _choose_stack(mount: KMount, axis_height: float, catalog: KernelCatalog,
                  pins: ChainPins | None = None) -> tuple[_Stack | None, list[dict[str, Any]]]:
    options = []
    for post, holder in _stack_pairs(mount, catalog, pins):
        depth = round(holder.height_mm + post.length_mm + mount.centre_height_mm - axis_height, _SNAP_DIGITS) + 0.0
        hi_depth = min(holder.insertion_max_mm, post.length_mm)
        if not holder.insertion_min_mm - _EPS <= depth <= hi_depth + _EPS:
            continue
        # keep the most height travel in the worse direction; then the stiffer (shorter) post, the lower holder; among
        # otherwise equal identities the better-evidenced, then by identity -- the mount order
        spare = min(depth - holder.insertion_min_mm, hi_depth - depth)
        evidence = tuple(a + b for a, b in zip(_evidence_rank(post), _evidence_rank(holder), strict=True))
        key = (-round(spare, 6), post.length_mm, holder.height_mm, evidence, post.identity, holder.identity)
        options.append((key, _Stack(post, holder, depth, hi_depth)))
    options.sort(key=lambda item: item[0])
    trace = [{"post": s.post.identity, "holder": s.holder.identity, "insertion_depth_mm": s.depth,
              "insertion_interval_mm": [s.holder.insertion_min_mm, s.depth_max]} for _, s in options[:8]]
    return (options[0][1] if options else None), trace


def _choose_lift(chains: Sequence[tuple[_Optic, KMount]], catalog: KernelCatalog, requested_height: float | None,
                 charge: Callable[[int], None] = _no_charge) -> float:
    """``t_z``: design z -> lab z (the lab height of the design's z = 0 plane). Every chain must reach ``z_design + t_z``.
    Observation planes are virtual and play no part. Each candidate height is ``charge``-d its chains first."""

    policy = catalog.policy
    z_ref = chains[0][0].position_rel[2]
    charge(len(chains))
    intervals = [(optic, [(lo - optic.position_rel[2], hi - optic.position_rel[2]) for lo, hi in _height_intervals(mount, catalog, optic.request.pins)])
                 for optic, mount in chains]
    for optic, spans in intervals:
        if not spans:
            raise _ChainFailure(optic.chain_key, "NO_STACK_FOR_MOUNT")

    def supported(t: float) -> str | None:
        """The first chain that cannot hold its optic at lift ``t`` (None: every chain can)."""

        charge(len(intervals))
        for optic, spans in intervals:
            z = optic.position_rel[2] + t
            if z < policy.min_beam_height_mm - _EPS or z > policy.max_beam_height_mm + _EPS:
                return optic.chain_key
            if not any(lo - _EPS <= t <= hi + _EPS for lo, hi in spans):
                return optic.chain_key
        return None

    if requested_height is not None:  # the lab height of the design z = 0 plane
        t = requested_height
        culprit = supported(t)
        if culprit is not None:
            raise _ChainFailure(culprit, "REQUESTED_BEAM_HEIGHT_UNREACHABLE")
        return t
    preferred = policy.preferred_beam_height_mm - z_ref
    step = policy.beam_height_step_mm
    limit = int((policy.max_beam_height_mm - policy.min_beam_height_mm) / step) + 1
    candidates = [preferred] + [t for k in range(1, limit + 1) for t in (preferred - k * step, preferred + k * step)]
    last_culprit = chains[0][0].chain_key
    for t in candidates:
        culprit = supported(t)
        if culprit is None:
            return t
        last_culprit = culprit
    raise _ChainFailure(last_culprit, "NO_COMMON_BEAM_HEIGHT", _reachable_heights(last_culprit, intervals, candidates, policy, charge))


def _reachable_heights(culprit: str, intervals: Sequence[tuple[_Optic, list[tuple[float, float]]]], candidates: Sequence[float],
                       policy: ResolverPolicyV1, charge: Callable[[int], None] = _no_charge) -> dict[str, Any]:
    """Where the culprit could stand: the lab axis heights its supports reach at all, and the design heights (z) that
    puts it at, given the lift every other chain accepts (nearest the preferred lift)."""

    optic, mine = next((o, spans) for o, spans in intervals if o.chain_key == culprit)
    z = optic.position_rel[2]
    detail: dict[str, Any] = {"current_design_z_mm": round(z, 3) + 0.0}
    if not mine:
        return detail
    lab_lo = max(min(lo for lo, _ in mine) + z, policy.min_beam_height_mm)  # spans are lifts: axis height = z + lift
    lab_hi = min(max(hi for _, hi in mine) + z, policy.max_beam_height_mm)
    detail["reachable_axis_height_mm"] = [round(lab_lo, 1) + 0.0, round(lab_hi, 1) + 0.0]
    for t in candidates:
        charge(len(intervals))
        if all(any(lo - _EPS <= t <= hi + _EPS for lo, hi in spans)
               and policy.min_beam_height_mm - _EPS <= o.position_rel[2] + t <= policy.max_beam_height_mm + _EPS
               for o, spans in intervals if o.chain_key != culprit):
            detail["reachable_design_z_mm"] = [round(lab_lo - t, 1) + 0.0, round(lab_hi - t, 1) + 0.0]
            break
    return detail


class _ChainFailure(Exception):
    def __init__(self, chain_key: str, reason: str, detail: dict[str, Any] | None = None):
        super().__init__(f"{chain_key}: {reason}")
        self.chain_key = chain_key
        self.reason = reason
        self.detail = detail or {}


# ------------------------------------------------------------------------------------------------ chains


@dataclass(frozen=True)
class _ChainPlan:
    optic: _Optic
    mount: KMount
    stack: _Stack
    mount_rotation: Quat
    optic_origin_in_mount: Vec3
    optic_position_lab_rel: Vec3  # lab position before the plan translation (z already lifted)
    mount_position_rel: Vec3

    @property
    def post_axis_rel(self) -> tuple[float, float]:
        return (self.mount_position_rel[0], self.mount_position_rel[1])


def _optic_origin_in_mount(optic: OpticRequest, mount: KMount) -> Vec3:
    mech = optic.mechanics
    if mount.seat_stop_x_mm is not None:
        x = mount.seat_stop_x_mm - mech.axial_max_mm
    else:
        x = -(mech.axial_min_mm + mech.axial_max_mm) / 2.0
    return (round(x, _SNAP_DIGITS) + 0.0, 0.0, mount.centre_height_mm)


def _plan_chain(optic: _Optic, mount: KMount, lift: float, catalog: KernelCatalog) -> _ChainPlan:
    z = round(optic.position_rel[2] + lift, _SNAP_DIGITS) + 0.0
    stack, _ = _choose_stack(mount, z, catalog, optic.request.pins)
    if stack is None:
        raise _ChainFailure(optic.chain_key, "NO_STACK_AT_BEAM_HEIGHT")
    rotation = _mount_rotation(optic.axis_lab)
    origin = _optic_origin_in_mount(optic.request, mount)
    lab = (optic.position_rel[0], optic.position_rel[1], z)
    mount_position = snap_vec(sub(lab, rotate(rotation, origin)))
    return _ChainPlan(optic, mount, stack, rotation, origin, lab, mount_position)


def _chain_bodies(plan: _ChainPlan, t: tuple[float, float], holder_yaw: Quat | None) -> dict[str, list[YawBox]]:
    """Collision bodies of one chain in the lab, keyed by slot key (the holder's fork only once it is turned)."""

    key = plan.optic.chain_key
    shift = (t[0], t[1], 0.0)
    mount_pose = Pose(add(plan.mount_position_rel, shift), plan.mount_rotation)
    post = plan.stack.post
    holder = plan.stack.holder
    post_bottom_z = mount_pose.position[2] - post.length_mm
    axis = (mount_pose.position[0], mount_pose.position[1])
    r = post.diameter_mm / 2.0
    bodies = {
        pk(key, "mount"): [YawBox(pk(key, "mount"), plan.mount.envelope.min_mm, plan.mount.envelope.max_mm, mount_pose)],
        pk(key, "post"): [YawBox(pk(key, "post"), (-r, -r, 0.0), (r, r, post.length_mm), Pose((axis[0], axis[1], post_bottom_z), plan.mount_rotation))],
    }
    rt = holder.tube_diameter_mm / 2.0
    yaw = holder_yaw if holder_yaw is not None else IDENTITY
    holder_pose = Pose((axis[0], axis[1], 0.0), yaw)
    holder_bodies = [YawBox(pk(key, "holder"), (-rt, -rt, 0.0), (rt, rt, holder.height_mm), holder_pose)]
    if holder_yaw is not None:
        holder_bodies.append(YawBox(pk(key, "holder"), holder.base.body.min_mm, holder.base.body.max_mm, holder_pose))
    bodies[pk(key, "holder")] = holder_bodies
    return bodies


#: The role word of a reserved space's slot key (``rp:x1:reserved``: external design anchor 1).
RESERVED = "reserved"


def reserved_key(n: int) -> str:
    return pk(f"x{n}", RESERVED)


def _reserved_bodies(externals: Sequence[ExternalRequest], orientation: Quat, lift: float) -> dict[str, list[YawBox]]:
    """The reserved space of every customer-supplied component with a known envelope, before the plan translation:
    its envelope cylinder (as a box) and the column below it down to the board top (lab z = 0). A component whose axis
    is horizontal gets a box turned with its axis; any other the world-aligned box around its turned envelope."""

    out: dict[str, list[YawBox]] = {}
    for n, external in enumerate(externals, start=1):
        envelope = external.envelope
        if envelope is None:
            continue
        key = reserved_key(n)
        position = rotate(orientation, external.position_design)
        centre = (position[0], position[1], position[2] + lift)
        rotation = q_normalize(q_mul(orientation, external.rotation_design))
        axis = rotate(rotation, (0.0, 0.0, 1.0))
        r = envelope.diameter_mm / 2.0
        if abs(axis[2]) <= _EPS:
            frame = yaw_quaternion((axis[0], axis[1]))
            lo: Vec3 = (envelope.z_min_mm, -r, min(-r, -centre[2]))
            hi: Vec3 = (envelope.z_max_mm, r, r)
        else:
            corners = [rotate(rotation, (x, y, z)) for x in (-r, r) for y in (-r, r) for z in (envelope.z_min_mm, envelope.z_max_mm)]
            frame = IDENTITY
            lo = (min(c[0] for c in corners), min(c[1] for c in corners), min(min(c[2] for c in corners), -centre[2]))
            hi = (max(c[0] for c in corners), max(c[1] for c in corners), max(c[2] for c in corners))
        out[key] = [YawBox(key, lo, hi, Pose(centre, frame))]
    return out


def _shifted(bodies: dict[str, list[YawBox]], t: tuple[float, float]) -> dict[str, list[YawBox]]:
    return {key: [YawBox(b.owner, b.lo, b.hi, Pose(add(b.pose.position, (t[0], t[1], 0.0)), b.pose.rotation)) for b in boxes]
            for key, boxes in bodies.items()}


_ALLOWED_CONTACT = {("mount", "post"), ("post", "holder")}


def _intra_chain_conflicts(bodies: dict[str, list[YawBox]]) -> list[str]:
    keys = sorted(bodies)
    out = []
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            roles = tuple(sorted((role_of(a), role_of(b))))
            if roles in {tuple(sorted(p)) for p in _ALLOWED_CONTACT}:
                continue
            if any(overlap_depth(x, y) > _EPS for x in bodies[a] for y in bodies[b]):
                out.append(f"{a}/{b}")
    return out


# ------------------------------------------------------------------------------------------------ layout


@dataclass(frozen=True)
class _Fixation:
    hole: tuple[int, int]
    along_mm: float
    direction: tuple[float, float]
    axial: bool  # the slot runs along the chain's optical axis

    @property
    def yaw(self) -> Quat:
        """The fork's yaw, computed when first asked for (most candidates of a chain are never turned to) and kept on
        the instance."""
        known = self.__dict__.get("_yaw")
        if known is None:
            known = yaw_quaternion(self.direction)
            object.__setattr__(self, "_yaw", known)
        return known


def _hole_xy(board: KBoard, i: int, j: int) -> tuple[float, float]:
    return (board.edge_offset_mm + i * board.pitch_mm, board.edge_offset_mm + j * board.pitch_mm)


def _fixation_candidates(plan: _ChainPlan, axis_xy: tuple[float, float], board: KBoard, *,
                         unbounded: bool = False, charge: Callable[[int], None] = _no_charge) -> tuple[list[_Fixation], bool]:
    """The board holes the chain's fork can reach, best first, and whether the board's edge cut off part of the reach
    (then the answer depends on where the design stands on this board, not only on the hole lattice). ``unbounded``:
    the hole lattice continued beyond the board (diagnosis only). Each hole examined is ``charge``-d first."""

    base = plan.stack.holder.base
    nx, ny = board.hole_count
    reach = base.slot_end_mm + _EPS
    i0u = int(math.floor((axis_xy[0] - reach - board.edge_offset_mm) / board.pitch_mm))
    i1u = int(math.ceil((axis_xy[0] + reach - board.edge_offset_mm) / board.pitch_mm))
    j0u = int(math.floor((axis_xy[1] - reach - board.edge_offset_mm) / board.pitch_mm))
    j1u = int(math.ceil((axis_xy[1] + reach - board.edge_offset_mm) / board.pitch_mm))
    edge = i0u < 0 or j0u < 0 or i1u > nx - 1 or j1u > ny - 1
    if unbounded:
        i0, i1, j0, j1 = i0u, i1u, j0u, j1u
    else:
        i0, i1, j0, j1 = max(0, i0u), min(nx - 1, i1u), max(0, j0u), min(ny - 1, j1u)
    charge(max(0, i1 - i0 + 1) * max(0, j1 - j0 + 1))
    a = (plan.optic.axis_lab[0], plan.optic.axis_lab[1])
    out = []
    for i in range(i0, i1 + 1):
        for j in range(j0, j1 + 1):
            hx, hy = _hole_xy(board, i, j)
            dx, dy = hx - axis_xy[0], hy - axis_xy[1]
            r = math.sqrt(dx * dx + dy * dy)
            if not base.slot_start_mm - _EPS <= r <= base.slot_end_mm + _EPS:
                continue
            d = (dx / r, dy / r)
            along = d[0] * a[0] + d[1] * a[1]
            across = abs(d[0] * a[1] - d[1] * a[0])
            category = 0 if across <= _EPS and along > 0 else 1 if across <= _EPS else 2
            key = (category, round(r, _SNAP_DIGITS), round(-along, _SNAP_DIGITS), i, j)
            out.append((key, _Fixation((i, j), round(r, _SNAP_DIGITS) + 0.0, d, category < 2)))
    out.sort(key=lambda item: item[0])
    return [f for _, f in out], edge


def _translation_candidates(plans: Sequence[_ChainPlan], board: KBoard, fixed_extent: tuple[tuple[float, float], tuple[float, float]],
                            charge: Callable[[int], None] = _no_charge, *, limit: int) -> list[tuple[float, float]]:
    """Plan translations to try, best first: most chains that request a translation along their optical axis (Tz)
    standing on a grid line along that axis (so their fork slides along the beam and delivers it), then most chains of
    any kind on such a line, then most of those with the downstream hole in the middle of the slot (equal axial travel
    both ways), then nearest the board centre.

    The candidates are every pair of an x translation (near the board centre, on each grid phase a chain asks for) and
    a y translation (likewise). The score of a pair is separable but for the "centred" count, so each chain's grid
    conditions are evaluated once per x and once per y value (as bit sets over the chains) and a pair is scored by
    two bit-set intersections; every value condition and every pair scored is ``charge``-d first (one check each)."""

    p, e = board.pitch_mm, board.edge_offset_mm
    rx: set[float] = set()
    ry: set[float] = set()

    def residue(value: float) -> float:
        return round(value % p, _SNAP_DIGITS) % p

    charge(len(plans))
    for plan in plans:
        ax, ay = plan.post_axis_rel
        a = plan.optic.axis_lab
        base = plan.stack.holder.base
        middle = (base.slot_start_mm + base.slot_end_mm) / 2.0
        if abs(a[1]) <= _TINY:  # along +/-x: stand on a grid row; downstream hole mid-slot
            ry.add(residue(e - ay))
            rx.add(residue(e - ax - math.copysign(middle, a[0])))
        if abs(a[0]) <= _TINY:  # along +/-y: stand on a grid column
            rx.add(residue(e - ax))
            ry.add(residue(e - ay - math.copysign(middle, a[1])))
    (fx0, fy0), (fx1, fy1) = fixed_extent
    cx, cy = (board.size_x_mm - (fx0 + fx1)) / 2.0, (board.size_y_mm - (fy0 + fy1)) / 2.0

    def on_grid(value: float) -> bool:
        return abs(((value - e) / p) - round((value - e) / p)) <= _EPS

    def around(residue: float | None, centre: float) -> list[float]:
        if residue is None:
            return [round(centre, _SNAP_DIGITS) + 0.0]
        k = round((centre - residue) / p)
        return [round(residue + (k + dk) * p, _SNAP_DIGITS) + 0.0 for dk in (0, -1, 1, -2, 2)]

    xs = sorted(rx) or [None]
    ys = sorted(ry) or [None]
    tx_values = list(dict.fromkeys(tx for rxv in xs for tx in around(rxv, cx)))
    ty_values = list(dict.fromkeys(ty for ryv in ys for ty in around(ryv, cy)))
    # (row coordinate, mid coordinate, mid-slot offset, wants Tz): a chain along x stands on a grid row when
    # ``on_grid(ay + ty)`` and has its downstream hole mid-slot when ``on_grid(ax + tx + offset)``; a chain along y on a
    # row when ``on_grid(ax + tx)``, mid-slot when ``on_grid(ay + ty + offset)`` (the previous scoring's expressions)
    along_x: list[tuple[float, float, float, bool]] = []
    along_y: list[tuple[float, float, float, bool]] = []
    for plan in plans:
        ax, ay = plan.post_axis_rel
        a = plan.optic.axis_lab
        base = plan.stack.holder.base
        middle = (base.slot_start_mm + base.slot_end_mm) / 2.0
        wants = any(d.axis == "Tz" for d in plan.optic.request.retained_dofs)
        if abs(a[1]) <= _TINY:
            along_x.append((ay, ax, math.copysign(middle, a[0]), wants))
        elif abs(a[0]) <= _TINY:
            along_y.append((ax, ay, math.copysign(middle, a[1]), wants))
    tz_x = sum(1 << i for i, chain in enumerate(along_x) if chain[3])
    tz_y = sum(1 << i for i, chain in enumerate(along_y) if chain[3])

    def on_row(chains: list[tuple[float, float, float, bool]], value: float) -> int:
        return sum(1 << i for i, chain in enumerate(chains) if on_grid(chain[0] + value))

    def mid_slot(chains: list[tuple[float, float, float, bool]], value: float) -> int:
        return sum(1 << i for i, chain in enumerate(chains) if on_grid(chain[1] + value + chain[2]))

    charge((len(tx_values) + len(ty_values)) * max(1, len(along_x) + len(along_y)))
    # per x value: the y-chains on a row and the x-chains whose fork is mid-slot; per y value the converse
    by_tx = []
    for tx in tx_values:
        row_y = on_row(along_y, tx)
        by_tx.append((tx, (row_y & tz_y).bit_count(), row_y.bit_count(), row_y, mid_slot(along_x, tx), abs(tx - cx)))
    by_ty = []
    for ty in ty_values:
        row_x = on_row(along_x, ty)
        by_ty.append((ty, (row_x & tz_x).bit_count(), row_x.bit_count(), row_x, mid_slot(along_y, ty), abs(ty - cy)))

    def scored():
        for tx, wanted_y, rows_y, row_y, mid_x, dx in by_tx:
            charge(len(by_ty))
            for ty, wanted_x, rows_x, row_x, mid_y, dy in by_ty:
                centred = (row_x & mid_x).bit_count() + (row_y & mid_y).bit_count()
                yield (-(wanted_x + wanted_y), -(rows_x + rows_y), -centred, round(dx + dy, 6), tx, ty)

    return [(key[4], key[5]) for key in heapq.nsmallest(limit, scored())]


@dataclass(frozen=True)
class _Layout:
    board: KBoard
    translation: tuple[float, float]
    fixations: dict[str, _Fixation]
    bodies: dict[str, list[YawBox]]
    footprint: tuple[tuple[float, float], tuple[float, float]]


class _BudgetExhausted(Exception):
    """The resolution's work budget (or its injected deadline) is spent."""


class _ShareSpent(_BudgetExhausted):
    """The first-fit search spent its share of the budget (``FIRST_FIT_SHARE``); the budget itself is not spent."""


#: Layout failure reasons decided by where the hardware stands (not by which hardware it is).
_POSITION_REASONS = frozenset({"INTER_CHAIN_COLLISION", "BEAM_OBSTRUCTED", "NO_FREE_FIXATION_HOLE", "OUTSIDE_BOARD"})
#: Those of them that are conflicts between the design's own supports, beams and fixing holes (not the board's edge).
_CONFLICT_REASONS = frozenset({"INTER_CHAIN_COLLISION", "BEAM_OBSTRUCTED", "NO_FREE_FIXATION_HOLE"})


class _FailureIndex:
    """The established layout failures of one lattice bucket, keyed by the chain-plan prefix they depend on: the first
    one recorded whose prefix starts the asked plans (one dictionary probe per distinct prefix length)."""

    def __init__(self) -> None:
        self.by_length: dict[int, dict[tuple, tuple[int, dict[str, Any]]]] = {}
        self.count = 0

    def lookup(self, sigs: tuple, charge: Callable[[int], None]) -> dict[str, Any] | None:
        charge(len(self.by_length))
        found: tuple[int, dict[str, Any]] | None = None
        for length, entries in self.by_length.items():
            hit = entries.get(sigs[:length])
            if hit is not None and (found is None or hit[0] < found[0]):
                found = hit
        return None if found is None else found[1]

    def add(self, prefix: tuple, failure: dict[str, Any]) -> None:
        self.by_length.setdefault(len(prefix), {}).setdefault(prefix, (self.count, failure))
        self.count += 1


def _plan_sig(plan: _ChainPlan) -> tuple:
    """Everything a chain's placement depends on, given the design orientation and lift."""

    return (plan.optic.chain_key, plan.mount.identity, plan.stack.post.identity, plan.stack.holder.identity, plan.stack.depth)


def _phase(value: float, pitch: float) -> float:
    return round(round(value % pitch, 6) % pitch, 6) + 0.0


@dataclass
class _Search:
    """The state of one ``resolve_assembly`` call: the budget, memoized plans and lifts, and the layout failures
    already established.

    Layout failure caching is exact: placing the chains of a design depends only on the chain plans, the beam
    segments (orientation and lift), and the board's hole lattice *relative to the design* (pitch and the phase of the
    translation against the lattice) -- unless the board's edge took part (a body or a hole outside the board). A
    failure at chain ``k`` with no edge involvement is therefore the answer for every board of the same lattice, every
    translation of the same phase and every later attempt whose first ``k + 1`` chains are unchanged.

    All work is charged to the budget before it is done (``charge``, ``place``): a spent budget raises
    ``_BudgetExhausted`` wherever the search is, also inside one layout evaluation."""

    budget: ResolutionBudget
    catalog: KernelCatalog
    rankings: list = field(default_factory=list)
    pointers: list[int] = field(default_factory=list)
    plans: dict[tuple, _ChainPlan] = field(default_factory=dict)
    conflicts: dict[tuple, list[str]] = field(default_factory=dict)
    lifts: dict[tuple, Any] = field(default_factory=dict)
    invariant: dict[tuple, _FailureIndex] = field(default_factory=dict)
    #: the first-fit search (no backtracking, ``backtracking`` False): its own failures, each the answer of a first-fit
    #: placement for the same plans prefix and lattice bucket (not a proof that no placement exists)
    first_fit: dict[tuple, _FailureIndex] = field(default_factory=dict)
    backtracking: bool = True
    #: the budget counts (chain placements, geometry checks) this search may reach (None: the budget's own limits)
    share: tuple[int, int] | None = None
    #: work both searches of one resolution share: the beam paths, the design yaw, each board's plan translations
    prepared: dict[Any, Any] = field(default_factory=dict)
    #: (bucket, plan signatures) whose chains the backtracking search placed on the hole lattice without edge
    unbounded_placed: set[tuple] = field(default_factory=set)
    best: dict[str, Any] | None = None
    cache_hits: int = 0
    #: ``x<n>`` -> the instance id of the n-th external (customer-supplied) design anchor
    externals: dict[str, str] = field(default_factory=dict)
    #: the design's chains and the failed mount attempts so far (for a refusal at any point)
    optics: list[_Optic] = field(default_factory=list)
    failures: list[dict[str, Any]] = field(default_factory=list)
    #: the customer's board size limit, and the smallest board outside it on which a layout was found (if any)
    limit: tuple[float, float] | None = None
    fitting_beyond_limit: KBoard | None = None
    #: only the boards before this one in ``_board_order`` (the search for a smaller board than the first-fit layout's)
    smaller_than: KBoard | None = None
    #: the layout fallbacks of the mounts chosen so far (chain key -> causes), as ``_assemble`` reports them
    fallbacks: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    #: the request and its nominal beam paths (a diagnosis lays the design out again with an element moved)
    request: AssemblyResolutionRequestV2 | None = None
    paths: tuple[BeamPathV2, ...] = ()
    #: where the trace of ``paths`` stopped at a cap of the policy (``trace_beam_paths(truncated=...)``); empty: complete
    paths_truncated: tuple[dict[str, Any], ...] = ()
    #: whether refusals and layout fallbacks are diagnosed (False: a diagnosis confirming a move it suggests)
    diagnose: bool = True
    #: whether the first layout found ends the search (a diagnosis asking only whether a moved design is laid out)
    first_layout: bool = False
    #: whether layout fallbacks are diagnosed later, in full, if the realization keeps them (``resolve_assembly``'s
    #: ``pending_fallbacks``, kept in ``pending``: id of a cause -> what its diagnosis needs)
    full_fallbacks: bool = False
    pending: dict[int, tuple[Any, ...]] | None = None

    def charge(self, checks: int) -> None:
        """Charge ``checks`` geometry checks before doing them; raise ``_BudgetExhausted`` when the budget (or this
        search's share of it, ``share``) is spent."""

        if self.share is not None and self.budget.geometry_checks + checks > self.share[1]:
            raise _ShareSpent()
        if not self.budget.take(checks=checks):
            raise _BudgetExhausted()

    def place(self) -> None:
        """Charge one chain placement before placing the chain; raise ``_BudgetExhausted`` when the budget (or this
        search's share of it) is spent."""

        if self.share is not None and self.budget.chain_placements + 1 > self.share[0]:
            raise _ShareSpent()
        if not self.budget.take(placements=1):
            raise _BudgetExhausted()

    def note(self, failure: dict[str, Any]) -> None:
        """Keep the most informative layout failure: the one reached with the most chains already placed (first seen
        on ties). Only a conflict between the design's supports, beams and fixing holes on a board the customer allows
        counts: a body over the edge of one board says nothing while larger boards remain, and a conflict
        on a board the customer's limit excludes is not one the customer has to resolve."""

        if failure.get("reason") not in _CONFLICT_REASONS or not _within_limit(failure["_ctx"][0], self.limit):
            return
        if self.best is None or failure["_depth"] > self.best["_depth"]:
            self.best = failure


def _try_layout(
    search: _Search,
    plans: Sequence[_ChainPlan],
    board: KBoard,
    yaw: Quat,
    lift: float,
    beam_segments: Sequence[tuple[Vec3, Vec3, frozenset[str]]],
    policy: ResolverPolicyV1,
    reserved: dict[str, list[YawBox]] | None = None,
) -> tuple[_Layout | None, dict[str, Any], dict[str, Any] | None]:
    """The first plan translation (best first) at which every chain is placed on ``board``; otherwise None, the last
    translation's failure, and the most informative conflict between the design's supports, beams and fixing holes
    seen at any translation (the one reached with the most chains placed, first seen on ties; None if every
    translation failed at the board's edge)."""

    margin = policy.board_edge_margin_mm
    # the fixed (fork-independent) extent of the design, relative
    search.charge(len(plans) + len(reserved or {}))
    rel_bodies = dict(reserved or {})
    for plan in plans:
        rel_bodies.update(_chain_bodies(plan, (0.0, 0.0), None))
    boxes = [b for bs in rel_bodies.values() for b in bs]
    (x0, y0), (x1, y1) = plan_bounds(boxes)
    if (x1 - x0) + 2 * margin > board.size_x_mm + _EPS or (y1 - y0) + 2 * margin > board.size_y_mm + _EPS:
        return None, {"reason": "FOOTPRINT_EXCEEDS_BOARD", "footprint_mm": [round(x1 - x0, 3), round(y1 - y0, 3)]}, None
    last_reason: dict[str, Any] = {"reason": "NO_TRANSLATION"}
    conflict: dict[str, Any] | None = None
    sigs = tuple(_plan_sig(plan) for plan in plans)
    memo = ("translations", board.identity, yaw, lift, sigs)
    if memo not in search.prepared:
        search.prepared[memo] = _translation_candidates(plans, board, ((x0, y0), (x1, y1)), search.charge,
                                                        limit=search.catalog.policy.max_translations)
    for t in search.prepared[memo]:
        layout, reason = _layout_at(search, plans, board, yaw, lift, t, beam_segments, policy, reserved, sigs)
        if layout is not None:
            return layout, {}, None
        last_reason = reason
        if reason.get("reason") in _CONFLICT_REASONS and (conflict is None or reason["_depth"] > conflict["_depth"]):
            conflict = reason
    return None, last_reason, conflict


class _Placement:
    """The bodies placed so far at one translation, with their clearance-inflated copies and per-chain bounds (the
    broad phase of the collision test). Every batch of geometry tests is ``charge``-d before it runs: the tests grow
    with the supports already placed and the beam segments, so a large design's evaluation is bounded inside itself."""

    def __init__(self, board: KBoard, policy: ResolverPolicyV1, beam_segments: Sequence[tuple[Vec3, Vec3, frozenset[str]]],
                 charge: Callable[[int], None] = _no_charge):
        margin = policy.board_edge_margin_mm
        self.lo_x, self.lo_y, self.hi_x, self.hi_y = margin, margin, board.size_x_mm - margin, board.size_y_mm - margin
        self.half_clearance = policy.inter_chain_clearance_mm / 2.0
        self.beam_clearance = policy.beam_clearance_mm
        self.segments = beam_segments
        self.placed: dict[str, list[YawBox]] = {}
        self.inflated: dict[str, list[YawBox]] = {}
        self.chain_bounds: dict[str, list[float]] = {}
        self.chain_of_key: dict[str, str] = {}
        self.fixations: dict[str, _Fixation] = {}
        self.hole_owner: dict[tuple[int, int], str] = {}
        self.edge = False  # the board's edge took part in a decision
        self.unbounded = False  # diagnosis: the board and its hole lattice continue without edge
        self.charge = charge

    def inside(self, boxes: Iterable[YawBox]) -> bool:
        if self.unbounded:
            return True
        boxes = list(boxes)
        self.charge(len(boxes))
        for box in boxes:
            x0, y0, _, x1, y1, _ = box.bounds
            if x0 < self.lo_x - _EPS or x1 > self.hi_x + _EPS or y0 < self.lo_y - _EPS or y1 > self.hi_y + _EPS:
                self.edge = True
                return False
        return True

    def clash(self, boxes: Sequence[YawBox], chain: str) -> str | None:
        """The slot key of the first placed body (of another chain) the boxes come closer to than the clearance."""

        grown = [b.inflated(self.half_clearance) for b in boxes]
        if not grown:
            return None
        self.charge(len(self.inflated))  # one broad-phase bounds test per placed body group
        columns = [b.bounds for b in grown]
        gb = (min(c[0] for c in columns), min(c[1] for c in columns), min(c[2] for c in columns),
              max(c[3] for c in columns), max(c[4] for c in columns), max(c[5] for c in columns))
        apart: dict[str, bool] = {}  # the body groups of one chain share its bounds
        for other_key, others in self.inflated.items():
            other_chain = self.chain_of_key[other_key]
            if other_chain == chain:
                continue
            far = apart.get(other_chain)
            if far is None:
                far = apart[other_chain] = bounds_apart(gb, self.chain_bounds[other_chain]) is not None
            if far:
                continue
            self.charge(len(grown) * len(others))
            for box in grown:
                for other in others:
                    if overlap_depth(box, other) > 0:
                        return other_key
        return None

    def blocked(self, boxes: Sequence[YawBox], chain_instance: str) -> tuple[Vec3, Vec3, frozenset[str], YawBox] | None:
        """The first beam segment (not ending at this chain's own optic) one of the boxes obstructs."""

        self.charge(len(self.segments) * len(boxes))
        for p0, p1, ends in self.segments:
            if chain_instance in ends:
                continue
            for box in boxes:
                hit = segment_box_hit(p0, p1, box, margin=self.beam_clearance)
                if hit is not None and hit > _MICRO:
                    return p0, p1, ends, box
        return None

    def reserve(self, reserved: dict[str, list[YawBox]]) -> bool:
        """Place the reserved spaces of the customer-supplied components (before any chain); False when one does not
        lie on the board."""

        for key, boxes in sorted(reserved.items()):
            if not self.inside(boxes):
                return False
            self._place(chain_of(key), {key: boxes})  # type: ignore[arg-type]
        return True

    def add(self, chain: str, fixation: _Fixation, bodies: dict[str, list[YawBox]]) -> None:
        self.fixations[chain] = fixation
        self.hole_owner[fixation.hole] = chain
        self._place(chain, bodies)

    def remove(self, chain: str) -> None:
        """Take one chain's bodies and fixation off the board again (the backtracking search revisits it)."""

        fixation = self.fixations.pop(chain)
        self.hole_owner.pop(fixation.hole, None)
        for key in [k for k, owner in self.chain_of_key.items() if owner == chain]:
            del self.placed[key]
            del self.inflated[key]
            del self.chain_of_key[key]
        self.chain_bounds.pop(chain, None)

    def _place(self, chain: str, bodies: dict[str, list[YawBox]]) -> None:
        for key, boxes in bodies.items():
            self.placed[key] = boxes
            self.chain_of_key[key] = chain
            grown = [b.inflated(self.half_clearance) for b in boxes]
            self.inflated[key] = grown
            current = self.chain_bounds.get(chain)
            for b in grown:
                if current is None:
                    current = list(b.bounds)
                else:
                    current = [min(current[k], b.bounds[k]) for k in range(3)] + [max(current[k], b.bounds[k]) for k in range(3, 6)]
            self.chain_bounds[chain] = current  # type: ignore[assignment]


class _ChainOptions:
    """The fixations of one chain at translation ``t`` among the chains already placed, found lazily in the kernel's
    preference order (its fixed bodies first, then each fixing hole its fork reaches, best first). ``next()`` returns
    the next acceptable fixation and its bodies, or None when none is left; ``failure()`` then says why (reason, the
    other chain involved, and for a missing hole how often each other chain was in the way).

    ``culprits`` collects, for every candidate turned down, an earlier chain whose *fixation* (fork and hole) turned it
    down: a hole that chain holds, or that chain's holder in the way. A candidate turned down by anything that does not
    depend on an earlier chain's fixation (the board edge, the beam, a mount, a post, a reserved space, the chain's own
    mount) adds nothing: no other fixation of an earlier chain can make it acceptable. This is the conflict set of the
    backtracking search (``_layout_at``)."""

    def __init__(self, plan: _ChainPlan, t: tuple[float, float], board: KBoard, state: _Placement):
        self.plan, self.state = plan, state
        key = plan.optic.chain_key
        self.instance = plan.optic.request.instance_id
        self.culprits: set[str] = set()
        self.in_the_way: dict[str, int] = {}
        self.candidates: list[_Fixation] = []
        self.position = 0
        self.blocked_failure: dict[str, Any] | None = None
        axis_xy = (plan.post_axis_rel[0] + t[0], plan.post_axis_rel[1] + t[1])
        fixed = _chain_bodies(plan, t, None)
        self.tube = fixed.pop(pk(key, "holder"))[0]
        self.fixed = fixed
        fixed_boxes = [b for bs in fixed.values() for b in bs]
        if not state.inside(fixed_boxes):
            self.blocked_failure = {"reason": "OUTSIDE_BOARD", "chain": key}
            return
        other = state.clash(fixed_boxes, key)
        if other is not None:
            self.blocked_failure = {"reason": "INTER_CHAIN_COLLISION", "chain": key, "with": chain_of(other), "_bodies": (other,)}
            self._blame(other)
            return
        hit = state.blocked(fixed_boxes, self.instance)
        if hit is not None:
            self.blocked_failure = {"reason": "BEAM_OBSTRUCTED", "chain": key, "_segment": hit}
            return
        self.candidates, edge = _fixation_candidates(plan, axis_xy, board, unbounded=state.unbounded, charge=state.charge)
        if edge and not state.unbounded:
            state.edge = True

    def _blame(self, slot_key: str) -> None:
        if role_of(slot_key) == "holder":  # a fork turned onto a hole: another fixation of that chain may clear it
            self.culprits.add(chain_of(slot_key))  # type: ignore[arg-type]

    def bodies(self, fixation: _Fixation) -> dict[str, list[YawBox]]:
        """The chain's bodies with its fork turned to ``fixation``."""

        key = self.plan.optic.chain_key
        holder_key = pk(key, "holder")
        holder_pose = Pose(self.tube.pose.position, fixation.yaw)
        base = self.plan.stack.holder.base.body
        holder = [YawBox(holder_key, self.tube.lo, self.tube.hi, holder_pose), YawBox(holder_key, base.min_mm, base.max_mm, holder_pose)]
        return {pk(key, "mount"): self.fixed[pk(key, "mount")], pk(key, "post"): self.fixed[pk(key, "post")], holder_key: holder}

    def next(self) -> tuple[_Fixation, dict[str, list[YawBox]]] | None:
        state, key = self.state, self.plan.optic.chain_key
        mount_boxes = self.fixed[pk(key, "mount")]
        while self.blocked_failure is None and self.position < len(self.candidates):
            fixation = self.candidates[self.position]
            self.position += 1
            owner = state.hole_owner.get(fixation.hole)
            if owner is not None:
                self.in_the_way[owner] = self.in_the_way.get(owner, 0) + 1
                self.culprits.add(owner)
                continue
            bodies = self.bodies(fixation)
            holder = bodies[pk(key, "holder")]
            if not state.inside(holder):
                continue
            clash = state.clash(holder, key)
            if clash is not None:
                self.in_the_way[chain_of(clash)] = self.in_the_way.get(chain_of(clash), 0) + 1  # type: ignore[index]
                self._blame(clash)
                continue
            if state.blocked(holder, self.instance) is not None:
                continue
            # within one chain only the mount and the holder may not touch (mount-post and post-holder are supports)
            state.charge(len(holder) * len(mount_boxes))
            if any(overlap_depth(x, y) > _EPS for x in holder for y in mount_boxes):
                continue
            return fixation, bodies
        return None

    def failure(self) -> dict[str, Any]:
        if self.blocked_failure is not None:
            return dict(self.blocked_failure)
        failure: dict[str, Any] = {"reason": "NO_FREE_FIXATION_HOLE", "chain": self.plan.optic.chain_key}
        if self.in_the_way:
            failure["with"] = max(self.in_the_way.items(), key=lambda item: (item[1], -int(item[0][1:])))[0]
        return failure


def _place_chain(plan: _ChainPlan, t: tuple[float, float], board: KBoard, state: _Placement) -> dict[str, Any] | tuple[_Fixation, dict[str, list[YawBox]]]:
    """Place one chain at translation ``t`` among the chains already placed: its fixed bodies, then the first free
    fixing hole its fork reaches. Returns the fixation and bodies, or the failure (reason, the other chain involved,
    and for a missing hole how often each other chain was in the way)."""

    options = _ChainOptions(plan, t, board, state)
    found = options.next()
    return found if found is not None else options.failure()


class _DepthFirst:
    """The depth-first placement of a design's chains at one translation.

    Each chain takes its first acceptable fixation, so a design that the first choices place is laid out exactly as by
    a first-fit placement. When a chain finds none, the search returns to the latest earlier chain whose fixation
    turned one of its candidates down (conflict-directed backjumping: an earlier chain that played no part is not
    revisited) and tries that chain's next fixation. A chain that fails for a reason no earlier fixation affects ends
    the search at once. ``place`` is called before each chain placement (a revisit is one too); at most ``backtracks``
    revisits (``ResolverPolicyV1.max_layout_backtracks``; each also charged one chain placement to the budget).

    After ``run``: ``deepest`` is the most informative failure (the one reached with the most chains placed, first seen
    on ties, with the fixations of the chains placed before it, so that its diagnosis replays exactly that placement),
    and ``proven`` says whether every placement of the chains up to it was tried or ruled out by a conflict."""

    def __init__(self, plans: Sequence[_ChainPlan], t: tuple[float, float], board: KBoard, state: _Placement,
                 place: Callable[[], None], backtracks: int):
        self.plans, self.t, self.board, self.state, self.place = plans, t, board, state, place
        self.limit = backtracks
        self.deepest: dict[str, Any] | None = None
        self.proven = True

    def run(self) -> bool:
        plans, state = self.plans, self.state
        order = {plan.optic.chain_key: k for k, plan in enumerate(plans)}
        count = len(plans)
        options: list[_ChainOptions | None] = [None] * count
        conflicts: list[set[int]] = [set() for _ in plans]
        backtracks = 0
        k = 0
        while k < count:
            chain_options = options[k]
            if chain_options is None:
                if k:
                    self.place()
                chain_options = options[k] = _ChainOptions(plans[k], self.t, self.board, state)
            found = chain_options.next()
            if found is not None:
                state.add(plans[k].optic.chain_key, *found)
                k += 1
                continue
            if self.deepest is None or k > self.deepest["_depth"]:
                self.deepest = {**chain_options.failure(), "_depth": k,
                                "_fixations": tuple(state.fixations[plan.optic.chain_key] for plan in plans[:k])}
            culprits = conflicts[k] | {order[c] for c in chain_options.culprits if c in order}
            if not culprits:
                return False  # no other fixation of an earlier chain can place this chain here
            if backtracks >= self.limit:
                self.proven = False
                return False
            backtracks += 1
            j = max(culprits)
            self.place()  # chain j is placed again, on its next fixation
            for m in range(k - 1, j - 1, -1):
                state.remove(plans[m].optic.chain_key)
            for m in range(j + 1, k + 1):
                options[m], conflicts[m] = None, set()
            conflicts[j] |= culprits - {j}
            k = j
        return True


def _layout_at(
    search: _Search,
    plans: Sequence[_ChainPlan],
    board: KBoard,
    yaw: Quat,
    lift: float,
    t: tuple[float, float],
    beam_segments: Sequence[tuple[Vec3, Vec3, frozenset[str]]],
    policy: ResolverPolicyV1,
    reserved: dict[str, list[YawBox]] | None = None,
    sigs: tuple | None = None,
) -> tuple[_Layout | None, dict[str, Any]]:
    """Place every chain at plan translation ``t`` (the reserved spaces of the customer-supplied components first, then
    the chains in design order, each on its own free hole, depth first: ``_DepthFirst``), or say which chain fails and
    why.

    Answered from the established failures when possible (see ``_Search``); otherwise a layout evaluation: each chain is
    charged one chain placement before it is placed (the reserved spaces go with the first one; a revisit is charged as
    a placement too) and every batch of geometry tests is charged before it runs, so a spent budget or a passed deadline
    stops the evaluation between two placements or two test batches, never after it has overrun the budget."""

    search.charge(len(plans))
    if sigs is None:
        sigs = tuple(_plan_sig(p) for p in plans)
    p = board.pitch_mm
    bucket = (yaw, lift, p, _phase(board.edge_offset_mm - t[0], p), _phase(board.edge_offset_mm - t[1], p))
    index = (search.invariant if search.backtracking else search.first_fit).get(bucket)
    if index is not None:
        cached = index.lookup(sigs, search.charge)
        if cached is not None:
            search.cache_hits += 1
            return None, cached
    if not search.budget.admit():
        raise _BudgetExhausted()
    search.charge(len(beam_segments))
    shifted_segments = [(add(p0, (t[0], t[1], 0.0)), add(p1, (t[0], t[1], 0.0)), ends) for p0, p1, ends in beam_segments]
    ctx = (board, yaw, lift, t, tuple(plans), tuple(beam_segments), reserved or {})
    if search.backtracking and (bucket, sigs) not in search.unbounded_placed:
        # first on the hole lattice continued without edge: when no placement exists there, none exists on any board of
        # this lattice at any translation of this phase, and the board-by-board search is not needed
        failure = _unbounded_probe(search, plans, board, t, shifted_segments, reserved, ctx)
        if failure is not None:
            search.invariant.setdefault(bucket, _FailureIndex()).add(sigs[:failure["_depth"] + 1], failure)
            search.note(failure)
            return None, failure
        search.unbounded_placed.add((bucket, sigs))
    state = _Placement(board, policy, shifted_segments, search.charge)
    search.place()  # the first chain, with the reserved spaces placed before it
    if not state.reserve(_shifted(reserved or {}, t)):
        return None, {"reason": "OUTSIDE_BOARD", "chain": plans[0].optic.chain_key, "reserved": True}
    placing = _DepthFirst(plans, t, board, state, search.place, policy.max_layout_backtracks if search.backtracking else 0)
    try:
        placed = placing.run()
    except _BudgetExhausted:
        if placing.deepest is not None:  # a spent budget still refuses with the failure this evaluation reached
            search.note({**placing.deepest, "_ctx": ctx})
        raise
    if not placed:
        assert placing.deepest is not None
        failure = {**placing.deepest, "_ctx": ctx}
        if not state.edge:
            if placing.proven:
                # every placement of the chains up to the failing one was tried or ruled out by a conflict: no board of
                # this lattice and no translation of this phase places them, whatever follows them
                search.invariant.setdefault(bucket, _FailureIndex()).add(sigs[:failure["_depth"] + 1], failure)
            if not search.backtracking:
                search.first_fit.setdefault(bucket, _FailureIndex()).add(sigs[:failure["_depth"] + 1], failure)
        search.note(failure)
        return None, failure
    boxes = [b for bs in state.placed.values() for b in bs]
    (x0, y0), (x1, y1) = plan_bounds(boxes)
    return _Layout(board, t, dict(state.fixations), dict(state.placed), ((x0, y0), (x1, y1))), {}


def _unbounded_probe(search: _Search, plans: Sequence[_ChainPlan], board: KBoard, t: tuple[float, float],
                     shifted_segments: Sequence[tuple[Vec3, Vec3, frozenset[str]]], reserved: dict[str, list[YawBox]] | None,
                     ctx: tuple) -> dict[str, Any] | None:
    """The backtracking placement of every chain at translation ``t`` on the hole lattice continued without edge (charged
    like a layout evaluation). Its failure when it proves that no placement exists there (the conflict it ends with,
    marked ``_unbounded`` so that its diagnosis replays it there too), else None."""

    state = _Placement(board, search.catalog.policy, shifted_segments, search.charge)
    state.unbounded = True
    search.place()
    state.reserve(_shifted(reserved or {}, t))
    probe = _DepthFirst(plans, t, board, state, search.place, search.catalog.policy.max_layout_backtracks)
    try:
        placed = probe.run()
    except _BudgetExhausted:
        if probe.deepest is not None:
            search.note({**probe.deepest, "_ctx": ctx, "_unbounded": True})
        raise
    if placed or not probe.proven:
        return None
    assert probe.deepest is not None
    return {**probe.deepest, "_ctx": ctx, "_unbounded": True}


def _board_rank(board: KBoard) -> tuple:
    """Smaller boards first: area, then the longer side, then the key."""

    return (board.size_x_mm * board.size_y_mm, max(board.size_x_mm, board.size_y_mm), board.size_x_mm, board.identity)


def _board_order(boards: Sequence[KBoard]) -> list[KBoard]:
    return sorted(boards, key=_board_rank)


def _within_limit(board: KBoard, limit: tuple[float, float] | None) -> bool:
    if limit is None:
        return True
    dims = sorted((board.size_x_mm, board.size_y_mm))
    lim = sorted(limit)
    return dims[0] <= lim[0] + _EPS and dims[1] <= lim[1] + _EPS


# ------------------------------------------------------------------------------------------------ DOFs


def _implement_dofs(plan: _ChainPlan, layout: _Layout, policy: ResolverPolicyV1, yaw: Quat,
                    ranked: Sequence[tuple[tuple, KMount, list[str]]] = ()) -> list[DofResultV2]:
    key = plan.optic.chain_key
    stack = plan.stack
    fixation = layout.fixations[key]
    axis_xy = (plan.post_axis_rel[0] + layout.translation[0], plan.post_axis_rel[1] + layout.translation[1])
    optic_lab = add(plan.optic_position_lab_rel, (layout.translation[0], layout.translation[1], 0.0))
    offset = math.sqrt((optic_lab[0] - axis_xy[0]) ** 2 + (optic_lab[1] - axis_xy[1]) ** 2)
    depth = stack.depth
    engagement_basis = "RESOLVED" if _is_fact(stack.holder, "insertion_min_mm", "insertion_max_mm") else "PREVIEW_ASSUMPTION"
    slot_basis = "RESOLVED" if _is_fact(stack.holder, "base.slot") else "PREVIEW_ASSUMPTION"
    mechanisms = _mount_mechanisms(plan.mount, plan.mount_rotation) + [
        _Mechanism("POST_HEIGHT_CLAMP", "TRANSLATION", _UP, "post", (-(stack.depth_max - depth), depth - stack.depth_min), "mm",
                   engagement_basis, "CONTINUOUS_CLAMPED", "Optical height: slide the post in its holder and lock it by hand."),
        _Mechanism("POST_ROTATION_CLAMP", "ROTATION", _UP, "post", (-_HALF_TURN_DEG, _HALF_TURN_DEG), "deg", "RESOLVED", "CONTINUOUS_CLAMPED",
                   "Rotation about the vertical post axis by hand.", axis_point=(axis_xy[0], axis_xy[1], 0.0)),
        _Mechanism("BASE_SLOT_TRAVEL", "TRANSLATION", (fixation.direction[0], fixation.direction[1], 0.0), "holder",
                   (fixation.along_mm - stack.holder.base.slot_end_mm, fixation.along_mm - stack.holder.base.slot_start_mm), "mm",
                   slot_basis, "COARSE_REPOSITION", "Coarse position along the base slot: loosen the base screw, slide, re-tighten."),
    ]
    provider_key = {"mount": pk(key, "mount"), "post": pk(key, "post"), "holder": pk(key, "holder")}
    selected = next((i for i, (_, m, _) in enumerate(ranked) if m.identity == plan.mount.identity), len(ranked))
    by_selected = ranked[selected][2] if selected < len(ranked) else []
    tried_before = {axis for _, _, delivered in ranked[:selected] for axis in delivered}
    offered_after = {axis for _, _, delivered in ranked[selected + 1:] for axis in delivered}
    out = []
    requested = sorted(plan.optic.request.retained_dofs, key=lambda d: _DOF_ORDER.index(d.axis))
    design_rotation = dof_frame(plan.optic.request.rotation_design)
    for dof in requested:
        motion = "TRANSLATION" if dof.axis.startswith("T") else "ROTATION"
        u = _lab_direction(plan.optic.rotation_lab, dof.axis)
        design_dir = snap_vec(rotate(design_rotation, _AXES[dof.axis]))
        match = None
        for mech in mechanisms:
            if mech.motion != motion:
                continue
            sign = _parallel(mech.axis, u, policy.parallel_tolerance)
            if sign == 0.0 or mech.travel is None:
                continue
            match = (mech, sign)
            break
        if match is None:
            if dof.axis in by_selected:
                why = "SLOT_NOT_ALONG_AXIS"  # offered only through a base slot the layout could not align with it
            elif dof.axis in tried_before:
                why = "HARDWARE_DID_NOT_FIT"  # better-ranked hardware delivering it did not fit the layout
            elif dof.axis in offered_after:
                why = "NOT_COMBINABLE_WITH_OTHER_DOFS"  # no compatible mount delivers it together with the others
            else:
                why = "NO_COMPATIBLE_HARDWARE"
            out.append(DofResultV2(plan.optic.request.instance_id, dof.axis, dof.control, "NONE", "NOT_DELIVERED", u, design_dir,
                                   "NONE", None, None, None, None, None, None, not_delivered_reason=why))
            continue
        mech, sign = match
        lo, hi = mech.travel  # type: ignore[misc]
        travel = (round(lo, 6) + 0.0, round(hi, 6) + 0.0) if sign > 0 else (round(-hi, 6) + 0.0, round(-lo, 6) + 0.0)
        status = "DELIVERED_AS_MANUAL_FALLBACK" if dof.control == "NUMERICAL" else "DELIVERED"
        axis_offset = round(offset, 6) + 0.0 if mech.code == "POST_ROTATION_CLAMP" else None
        out.append(DofResultV2(
            plan.optic.request.instance_id, dof.axis, dof.control, "MANUAL", status, u, design_dir, mech.code,
            provider_key[mech.provider], provider_key[mech.provider], travel, mech.unit, mech.basis, mech.adjustment,
            axis_offset_mm=axis_offset, description=mech.description,
        ))
    return out


# ------------------------------------------------------------------------------------------------ entry point


def _mount_geometry_sig(mount: KMount) -> tuple:
    """Every mount fact the layout depends on: two mounts equal here realize identically (only their adjusters and
    identity differ), so a layout failure of one is a layout failure of the other."""

    return (mount.envelope.min_mm, mount.envelope.max_mm, mount.centre_height_mm, mount.seat_stop_x_mm, mount.post_thread)


def _plate_tilt_cosine(policy: ResolverPolicyV1) -> float:
    return math.cos(math.radians(policy.max_plate_tilt_deg))


def _lens_tilt_cosine(policy: ResolverPolicyV1) -> float:
    return math.cos(math.radians(policy.max_lens_tilt_deg))


def beam_trace_settings(policy: ResolverPolicyV1) -> dict[str, Any]:
    """The keyword arguments of ``trace_beam_paths`` that the kernel policy sets: every trace of a design (the
    kernel's and its callers') uses the same ones."""

    return {"plate_tilt_cosine": _plate_tilt_cosine(policy), "lens_tilt_cosine": _lens_tilt_cosine(policy),
            "max_branches": policy.max_beam_branches, "max_points": policy.max_beam_points}


def resolve_assembly(request: AssemblyResolutionRequestV2, catalog: KernelCatalog, *,
                     budget: ResolutionBudget | None = None,
                     pending_fallbacks: dict[int, tuple[Any, ...]] | None = None) -> AssemblyRealizationV2:
    """Realize ``request`` with ``catalog``, or raise ``AssemblyResolutionRefusal`` with the reason.

    ``budget`` bounds the work (``ResolverPolicyV1.max_chain_placements`` and ``max_geometry_checks`` when omitted); a
    caller resolving the same design more than once shares one budget across the passes. Every part of the work that
    grows with the size of the design is charged to it before it runs (the beam trace, the design yaw, the beam
    height, the plan translations, each chain placement and each batch of geometry tests inside a layout evaluation),
    so a spent budget or a passed deadline refuses (``search_budget_exhausted``) from wherever the search is. The
    diagnoses of layout fallbacks are charged to the budget's own diagnosis account under the same deadline (one shared
    limit, however many chains fall back); only the refusal's diagnosis has a fixed allowance outside the budget.
    ``pending_fallbacks`` (an empty dict the caller keeps): the layout fallbacks are not diagnosed while the design is
    resolved; what their diagnosis needs is kept there instead, for ``diagnose_kept_fallbacks`` -- a refusal of the
    requested DOFs (``require_all_retained_dofs``) diagnoses in full only the fallbacks the realization keeps."""

    if budget is None:
        budget = ResolutionBudget(catalog.policy.max_chain_placements, max_geometry_checks=catalog.policy.max_geometry_checks)
    try:
        if pending_fallbacks is None:
            return _resolve(request, catalog, budget, diagnose=True)
        return _resolve(request, catalog, budget, diagnose=True, full_fallbacks=True, pending=pending_fallbacks)
    except AssemblyResolutionRefusal:
        budget.refused = True
        raise


def _keep_established(search: _Search, before: _Search | None, request: AssemblyResolutionRequestV2, catalog: KernelCatalog) -> None:
    """``search`` starts from what ``before`` established when it searched the same design (only the customer's board limit
    may differ, and the limit plays no part in a chain plan or a layout failure): the same plans, lifts, beam and failures."""

    if before is None or before.catalog is not catalog or replace(before.request, max_board_size_mm=None) != replace(request, max_board_size_mm=None):
        return
    search.plans, search.conflicts, search.lifts, search.invariant = before.plans, before.conflicts, before.lifts, before.invariant
    search.first_fit, search.prepared, search.unbounded_placed = before.first_fit, before.prepared, before.unbounded_placed


def _resolve(request: AssemblyResolutionRequestV2, catalog: KernelCatalog, budget: ResolutionBudget | None, *,
             diagnose: bool, full_fallbacks: bool = False, pending: dict[int, tuple[Any, ...]] | None = None,
             first_layout: bool = False, seed: _Search | None = None) -> AssemblyRealizationV2:
    """``resolve_assembly``; ``diagnose`` False: a refusal and the layout fallbacks carry no diagnosis (a diagnosis
    confirming a position it suggests resolves the moved design this way); ``full_fallbacks``: the layout fallbacks are
    kept in ``pending`` for a full diagnosis afterwards (``resolve_assembly``'s ``pending_fallbacks``); ``first_layout``: the
    first layout found is the answer, not the best one (whether the design is laid out at all)."""

    policy = catalog.policy
    if budget is None:
        budget = ResolutionBudget(policy.max_chain_placements, max_geometry_checks=policy.max_geometry_checks)
    if not request.optics:
        raise AssemblyResolutionRefusal("NO_OPENSCI_OPTIC", "the design has no OpenSci optical product to realize")
    for optic in request.optics:
        _check_orientation(optic, policy)
    externals = {f"x{n}": e.instance_id for n, e in enumerate(request.externals, start=1)}
    share = (budget.chain_placements + (budget.max_chain_placements - budget.chain_placements) * FIRST_FIT_SHARE // _PERCENT,
             budget.geometry_checks + (budget.max_geometry_checks - budget.geometry_checks) * FIRST_FIT_SHARE // _PERCENT)
    first = _Search(budget, catalog, externals=externals, limit=request.max_board_size_mm, request=request, backtracking=False,
                    share=share, diagnose=diagnose, full_fallbacks=full_fallbacks, pending=pending, first_layout=first_layout)
    _keep_established(first, seed if seed is not None else budget.last_search, request, catalog)
    budget.last_search = first
    found: AssemblyRealizationV2 | None = None
    try:
        found = _search_realization(first, request, catalog)
    except _ShareSpent:
        pass
    except _BudgetExhausted:
        raise _budget_refusal(first, first.optics, first.failures) from None
    except AssemblyResolutionRefusal as refusal:
        if refusal.code not in _LAYOUT_REFUSALS:
            raise  # not a matter of where the supports stand: backtracking cannot change it
    if found is not None and first_layout:
        return found
    if found is not None and not any(d.not_delivered_reason in _LAYOUT_LOSSES for d in found.dofs):
        return _on_the_smallest_board(first, found, request, catalog, budget)
    # the complete search -- the same, with earlier chains' fixations revisited (``_DepthFirst``)
    search = _Search(budget, catalog, externals=externals, limit=request.max_board_size_mm, request=request,
                     plans=first.plans, conflicts=first.conflicts, lifts=first.lifts, invariant=first.invariant,
                     prepared=first.prepared, unbounded_placed=first.unbounded_placed, diagnose=diagnose,
                     full_fallbacks=full_fallbacks, pending=pending, first_layout=first_layout)
    try:
        complete = _search_realization(search, request, catalog)
    except _BudgetExhausted:
        if found is not None:
            return found
        raise _budget_refusal(search, search.optics, search.failures) from None
    except AssemblyResolutionRefusal:
        if found is not None:
            return found
        raise
    return complete if found is None or _preferred(complete, found) else found


def _preferred(candidate: AssemblyRealizationV2, current: AssemblyRealizationV2) -> bool:
    """Whether ``candidate`` is the better realization: more requested DOFs delivered, then a smaller board (the
    kernel's order, step 5); on a tie the current one stands."""

    if _delivered(candidate) != _delivered(current):
        return _delivered(candidate) > _delivered(current)
    return _board_rank(candidate.board.board) < _board_rank(current.board.board)


#: The share (percent) of the budget left after the first-fit search that the search for a smaller board may spend.
SMALLER_BOARD_SHARE = 25


def _on_the_smallest_board(first: _Search, found: AssemblyRealizationV2, request: AssemblyResolutionRequestV2,
                           catalog: KernelCatalog, budget: ResolutionBudget) -> AssemblyRealizationV2:
    """The first-fit layout (``found``), or the same hardware laid out on a smaller board by the complete search.

    The first-fit placement may need a larger board than a layout exists on: a smaller board that took the design's
    footprint but where the first-fit placement failed (a fork it did not turn) is tried again with the complete,
    backtracking search, with the same mounts, posts and holders, within ``SMALLER_BOARD_SHARE`` of the budget left
    (the customer pays for the board). A smaller layout that delivers as many requested DOFs is
    taken; otherwise, or when the share runs out, the first-fit layout stands. Boards whose fixed footprint is larger
    than the board (``FOOTPRINT_EXCEEDS_BOARD``) are not tried again: no placement of the forks changes that."""

    retry = any(c["within_limit"] and not c["fits"] and c.get("reason") != "FOOTPRINT_EXCEEDS_BOARD" for c in found.board.considered)
    if not retry:
        return found
    share = (budget.chain_placements + (budget.max_chain_placements - budget.chain_placements) * SMALLER_BOARD_SHARE // _PERCENT,
             budget.geometry_checks + (budget.max_geometry_checks - budget.geometry_checks) * SMALLER_BOARD_SHARE // _PERCENT)
    search = _Search(budget, catalog, rankings=first.rankings, pointers=first.pointers, plans=first.plans, conflicts=first.conflicts,
                     lifts=first.lifts, invariant=first.invariant, share=share, prepared=first.prepared, externals=first.externals,
                     optics=first.optics, limit=request.max_board_size_mm, request=request, paths=first.paths,
                     smaller_than=found.board.board, fallbacks=first.fallbacks, diagnose=first.diagnose,
                     full_fallbacks=first.full_fallbacks, pending=first.pending)
    chosen = [(optic, first.rankings[i][0][first.pointers[i]][1]) for i, optic in enumerate(first.optics)]
    try:
        smaller = _realize(search, request, catalog, first.prepared["yaw"], chosen, first.rankings, first.pointers, first.paths,
                           first.fallbacks)
    except (_BudgetExhausted, _ChainFailure):
        return found
    return smaller if _preferred(smaller, found) else found


#: The share (percent) of the budget left at its start that the first-fit search may spend before the complete search
#: takes over.
FIRST_FIT_SHARE = 25
#: The refusals a complete layout search may overturn (the others do not depend on where the supports stand).
_LAYOUT_REFUSALS = frozenset({"MECHANICAL_LAYOUT_CONFLICT", "BREADBOARD_CONSTRAINT_UNSATISFIABLE", "BREADBOARD_NO_STANDARD_FIT",
                              "MECHANICAL_RESOLUTION_BOUND_EXCEEDED"})
#: Requested DOFs the first-fit layout lost (better-ranked hardware did not fit, or the fork could not turn along the
#: beam): the complete search may keep them.
_LAYOUT_LOSSES = frozenset({"HARDWARE_DID_NOT_FIT", "SLOT_NOT_ALONG_AXIS"})


def _delivered(realization: AssemblyRealizationV2) -> int:
    return sum(1 for d in realization.dofs if d.status != "NOT_DELIVERED")


def _search_realization(search: _Search, request: AssemblyResolutionRequestV2, catalog: KernelCatalog) -> AssemblyRealizationV2:
    policy = catalog.policy
    prepared = search.prepared  # shared by the first-fit and the complete search: the design's beam and yaw are done once
    if "yaw" not in prepared:
        truncated: list[dict[str, Any]] = []
        prepared["paths"] = trace_beam_paths(request.externals, request.optics, request.observations,
                                             **beam_trace_settings(policy), truncated=truncated, charge=search.charge)
        prepared["truncated"] = tuple(truncated)
        _check_beam_tilts(request, prepared["paths"], policy, search.charge)
        prepared["yaw"] = _design_yaw(request.optics, prepared["paths"], search.charge)
    paths, yaw = prepared["paths"], prepared["yaw"]
    search.paths = paths
    search.paths_truncated = prepared.get("truncated", ())
    optics = search.optics
    for n, req in enumerate(request.optics, start=1):
        rotation_lab = q_normalize(q_mul(yaw, dof_frame(req.rotation_design)))
        axis = rotate(rotation_lab, (0.0, 0.0, 1.0))
        axis_h = unit((axis[0], axis[1], 0.0))
        optics.append(_Optic(req, f"c{n}", rotation_lab, snap_vec(axis_h), rotate(yaw, req.position_design)))

    incidences = _incidences(request.optics, paths)
    rankings = search.rankings
    for optic in optics:
        ranked, rejected = rank_mounts(optic, catalog, incidences.get(optic.request.instance_id))
        if not ranked:
            raise _no_mount_refusal(optic, rejected, incidences.get(optic.request.instance_id))
        rankings.append((ranked, rejected))

    pointers = search.pointers
    pointers.extend([0] * len(optics))
    tried: list[set[tuple]] = [set() for _ in optics]
    failures = search.failures
    fallbacks = search.fallbacks
    for _attempt in range(policy.max_mount_attempts):
        chosen = [(optics[i], rankings[i][0][pointers[i]][1]) for i in range(len(optics))]
        try:
            return _realize(search, request, catalog, yaw, chosen, rankings, pointers, paths, fallbacks)
        except _ChainFailure as failure:
            failures.append(_public_failure_record(failure))
            index = next(i for i, o in enumerate(optics) if o.chain_key == failure.chain_key)
            ranked = rankings[index][0]
            tried[index].add(_mount_geometry_sig(ranked[pointers[index]][1]))
            following = pointers[index] + 1
            while following < len(ranked) and _mount_geometry_sig(ranked[following][1]) in tried[index]:
                following += 1  # the same geometry fails the same way: never re-run the layout search for it
            if following >= len(ranked) and failure.reason in _POSITION_REASONS:
                blamed = _fall_back_other(failure, optics, rankings, pointers, tried)
                if blamed is not None:  # the other chain gives up adjusters it was given, and this chain starts over
                    causes = fallbacks.setdefault(optics[blamed].chain_key, [])
                    cause = {"reason": _PUBLIC_REASONS.get(failure.reason, "NO_SUPPORTED_MOUNTING"), "with": optics[index].request.instance_id}
                    causes.append(_with_spacing(search, cause, failure, optics) if not causes else cause)
                    pointers[index], tried[index] = 0, set()
                    continue
            if following >= len(ranked) or failure.reason in _TERMINAL:
                raise _refusal(search, failure, optics[index], optics, failures) from None
            if len(ranked[following][2]) < len(ranked[pointers[index]][2]):
                causes = fallbacks.setdefault(failure.chain_key, [])
                cause = _public_cause(failure, optics, search.externals)
                causes.append(_with_spacing(search, cause, failure, optics) if not causes else cause)
            pointers[index] = following
    raise _budget_refusal(search, optics, failures, attempts=policy.max_mount_attempts)


def _fall_back_other(failure: _ChainFailure, optics: Sequence[_Optic], rankings: list, pointers: list[int],
                     tried: list[set[tuple]]) -> int | None:
    """When a chain has no alternative left and its failure names the chain in its way, and that other chain stands on
    hardware chosen for retained DOFs that a simpler compatible mount does not deliver, move the other chain to the best
    such simpler mount (a reported fallback) so that the design is not refused while a realization without
    those adjusters exists. Returns the index of the chain moved, or None."""

    other = failure.detail.get("with")
    w = next((i for i, o in enumerate(optics) if o.chain_key == other), None)
    if w is None:
        return None
    ranked = rankings[w][0]
    current = len(ranked[pointers[w]][2])
    for j in range(pointers[w] + 1, len(ranked)):
        if len(ranked[j][2]) < current and _mount_geometry_sig(ranked[j][1]) not in tried[w]:
            tried[w].add(_mount_geometry_sig(ranked[pointers[w]][1]))
            pointers[w] = j
            return w
    return None


#: The numbers of a layout diagnosis that tell how to move a component (``_with_spacing``).
_SPACING_NUMBERS = ("beam_direction_design", "min_extra_spacing_mm", "nearest_fixable_offset_mm", "suggested_position_design_mm",
                    "moved_with", "min_lateral_clearance_mm", "free_direction_design", "suggested_positions_design_mm", "suggested_positions_tightness",
                    "min_support_pitch_mm", "conflicting_pairs", "pairs_confirmed")


def _with_spacing(search: _Search, cause: dict[str, Any], failure: _ChainFailure, optics: Sequence[_Optic]) -> dict[str, Any]:
    """A layout fallback's cause with the numbers of the failure that forced it: which component to move
    (``suggestion.instance_id``, the one whose support failed) and how far, so that a refusal for the requested DOFs
    (``RETAINED_DOF_UNSATISFIED``) can say how to keep them."""

    if "_ctx" not in failure.detail or not search.diagnose or search.budget.refused:
        return cause
    if search.full_fallbacks and search.pending is not None:
        # diagnosed in full after the resolution, if the realization keeps this fallback (``resolve_assembly``)
        pending = dict(cause)
        search.pending[id(pending)] = (search, failure.detail, failure.chain_key, optics)
        return pending
    # charged to the resolution's diagnosis account (shared by every fallback, under the deadline), not to a fresh
    # allowance per fallback: their total no longer grows with the number of chains that fall back
    numbers = _diagnose(search, failure.detail, search.catalog, optics, pool=search.budget)
    move = _fallback_move(search, numbers, failure.chain_key, optics)
    return {**cause, "suggestion": move} if move else cause


def _fallback_move(search: _Search, numbers: dict[str, Any], chain_key: str, optics: Sequence[_Optic]) -> dict[str, Any] | None:
    """The suggestion of a layout fallback's cause from its diagnosis' numbers (None when there are none)."""

    moves = {k: numbers[k] for k in _SPACING_NUMBERS if k in numbers}
    if not moves:
        return None
    return {"instance_id": numbers.get("instance_id") or _instance_of(chain_key, optics, search.externals), **moves}


def diagnose_kept_fallbacks(realization: AssemblyRealizationV2, pending: dict[int, tuple[Any, ...]],
                            expired: Callable[[], bool] | None = None) -> None:
    """The full diagnosis (``_diagnose``'s ``full``: confirmed by resolving the moved design with the requested hardware
    kept) of the first fallback cause of every chain whose requested DOFs the realization lost to it, kept in
    ``pending`` by ``resolve_assembly`` -- written into the cause (``suggestion``) in place, on a diagnosis account of
    its own (``DEFAULT_MAX_DIAGNOSIS_CHECKS``, under the ``expired`` deadline)."""

    pool = ResolutionBudget(1, max_geometry_checks=1, expired=expired)
    lost = {d.instance_id for d in realization.dofs if d.not_delivered_reason == "HARDWARE_DID_NOT_FIT"}
    for chain in realization.chains:
        if chain.instance_id not in lost or not chain.fallback_causes:
            continue
        cause = chain.fallback_causes[0]
        entry = pending.get(id(cause))
        if entry is None:
            continue
        search, detail, chain_key, optics = entry
        numbers = _diagnose(search, detail, search.catalog, optics, pool=pool, full=True)
        move = _fallback_move(search, numbers, chain_key, optics)
        if move:
            cause["suggestion"] = move


def _public_failure_record(failure: _ChainFailure) -> dict[str, Any]:
    """The internal record of one failed attempt (JSON-safe: no geometry objects)."""

    return {"chain": failure.chain_key, "reason": failure.reason, **{k: v for k, v in failure.detail.items() if not k.startswith("_")}}


def _public_cause(failure: _ChainFailure, optics: Sequence[_Optic], externals: dict[str, str] | None = None) -> dict[str, Any]:
    cause: dict[str, Any] = {"reason": _PUBLIC_REASONS.get(failure.reason, "NO_SUPPORTED_MOUNTING")}
    other = failure.detail.get("with")
    instance = _instance_of(other, optics, externals) if other else None
    if instance is not None:
        cause["with"] = instance
    return cause


def _instance_of(chain_key: str | None, optics: Sequence[_Optic], externals: dict[str, str] | None = None) -> str | None:
    """The design component of a chain key (``c<n>``), or of an external design anchor (``x<n>``: a customer-supplied
    component whose reserved space was in the way)."""

    found = next((o.request.instance_id for o in optics if o.chain_key == chain_key), None)
    return found if found is not None else (externals or {}).get(chain_key or "")


#: The customer form of a failure reason (``details.reason`` of a refusal: the failure kind).
_PUBLIC_REASONS = {
    "INTER_CHAIN_COLLISION": "SUPPORTS_COLLIDE",
    "INTRA_CHAIN_COLLISION": "SUPPORTS_COLLIDE",
    "BEAM_OBSTRUCTED": "HARDWARE_BLOCKS_BEAM",
    "NO_FREE_FIXATION_HOLE": "NO_FIXING_HOLE",
    "NO_COMMON_BEAM_HEIGHT": "NO_COMMON_BEAM_HEIGHT",
    "NO_STACK_FOR_MOUNT": "NO_POST_AND_HOLDER_FOR_OPTIC",
    "NO_STACK_AT_BEAM_HEIGHT": "BEAM_HEIGHT_NOT_REACHABLE",
    "OUTSIDE_BOARD": "LAYOUT_OUTSIDE_BOARD",
}
_TERMINAL = frozenset({"BREADBOARD_NO_STANDARD_FIT", "BREADBOARD_CONSTRAINT_UNSATISFIABLE", "REQUESTED_BEAM_HEIGHT_UNREACHABLE"})
_LAYOUT_CODE_REASONS = frozenset({"INTER_CHAIN_COLLISION", "BEAM_OBSTRUCTED", "NO_FREE_FIXATION_HOLE", "OUTSIDE_BOARD"})
_MESSAGES = {
    "INTER_CHAIN_COLLISION": "the supports of two optics would collide; move the optics further apart",
    "INTRA_CHAIN_COLLISION": "the supports of this optic would collide with each other at this position",
    "BEAM_OBSTRUCTED": "the hardware of one optic would block the beam of another",
    "NO_FREE_FIXATION_HOLE": "an optic's support cannot be fixed to the breadboard grid at this position",
    "NO_COMMON_BEAM_HEIGHT": "the optics cannot all be held at their relative heights by the available supports",
    "NO_STACK_FOR_MOUNT": "no post and holder combination supports this optic",
    "NO_STACK_AT_BEAM_HEIGHT": "no post and holder combination reaches this optic's height",
    "OUTSIDE_BOARD": "the layout does not fit the breadboard",
}

#: Recovery actions of the refusal contract (``details.suggestion.action``).
ADJUST_COMPONENT_SPACING = "ADJUST_COMPONENT_SPACING"
MOVE_COMPONENT_OFF_BEAM = "MOVE_COMPONENT_OFF_BEAM"
ADJUST_COMPONENT_HEIGHT = "ADJUST_COMPONENT_HEIGHT"
RELAX_RETAINED_DOF = "RELAX_RETAINED_DOF"
REDUCE_TILT = "REDUCE_TILT"
CHANGE_COMPONENT = "CHANGE_COMPONENT"
ALLOW_LARGER_BREADBOARD = "ALLOW_LARGER_BREADBOARD"
SIMPLIFY_DESIGN = "SIMPLIFY_DESIGN"


def _design_dir(orientation: Quat, lab_xy: tuple[float, float]) -> list[float]:
    d = rotate(q_conj(orientation), (lab_xy[0], lab_xy[1], 0.0))
    return [round(v, 6) + 0.0 for v in d]


def _round(value: float, digits: int = 1) -> float:
    return round(value, digits) + 0.0


def _beam_direction(plan: _ChainPlan, segments: Sequence[tuple[Vec3, Vec3, frozenset[str]]]) -> tuple[float, float]:
    """The plan direction of the beam at this chain's optic (its incoming segment), else its optical axis."""

    instance = plan.optic.request.instance_id
    for p0, p1, ends in segments:
        if instance in ends:
            d = (p1[0] - p0[0], p1[1] - p0[1])
            n = math.sqrt(d[0] * d[0] + d[1] * d[1])
            if n > _EPS:
                return (d[0] / n, d[1] / n)
    return (plan.optic.axis_lab[0], plan.optic.axis_lab[1])


def _projection(boxes: Sequence[YawBox], d: tuple[float, float]) -> tuple[float, float]:
    values = [x * d[0] + y * d[1] for b in boxes for x, y in b.plan_corners()]
    return min(values), max(values)


#: The geometry checks one diagnosis may spend on each of its stages: every move it tries (the component alone, then with
#: what follows it), the new positions, and the confirmation of the pairs' spacings, ``DIAGNOSIS_STAGES`` in all. Each stage
#: has this for itself, so that one that runs out never takes the confirmation of the spacings from the refusal. The
#: refusal's diagnosis has this as its own fixed allowance, outside the search budget (a refusal of a spent
#: budget is still diagnosed; one refusal per kernel pass). A layout fallback's diagnosis is also charged to the
#: resolution's shared diagnosis account (``ResolutionBudget.take_diagnosis``). A diagnosis that runs out reports the
#: numbers found so far. The allowance is ``ResolverPolicyV1.diagnosis_max_geometry_checks``.
DIAGNOSIS_STAGES = 3
#: The checks a diagnosis may spend on the move a spacing or clearance of the named component applies to, beyond its stages.
DIAGNOSIS_OWN_MOVE_CHECKS = 10_000


def _round_up(value: float, digits: int = 1) -> float:
    """``value`` rounded up to ``digits`` decimals (a spacing that is enough, never one that falls short)."""

    factor = _DECIMAL_BASE ** digits
    return math.ceil(round(value * factor, 6)) / factor + 0.0


def _downstream(instance: str, paths: Sequence[BeamPathV2]) -> set[str]:
    """``instance`` and every design element the nominal beam visits after it: on its paths after it, and on every
    branch leaving it or an element after it (they move together when it moves along its incoming beam)."""

    moved = {instance}
    changed = True
    while changed:
        changed = False
        for path in paths:
            first = next((i for i, name in enumerate(path.through) if name in moved), None)
            if first is None:
                continue
            after = set(path.through[first + 1:])
            if not after <= moved:
                moved |= after
                changed = True
    return moved


def _moved_request(request: AssemblyResolutionRequestV2, moved: set[str], shift: Vec3) -> AssemblyResolutionRequestV2:
    def at(position: Vec3) -> Vec3:
        return (position[0] + shift[0], position[1] + shift[1], position[2] + shift[2])

    return replace(
        request,
        optics=tuple(replace(o, position_design=at(o.position_design)) if o.instance_id in moved else o for o in request.optics),
        externals=tuple(replace(e, position_design=at(e.position_design)) if e.instance_id in moved and e.role != "source" else e
                        for e in request.externals),
        observations=tuple(replace(o, position_design=at(o.position_design)) if o.observation_id in moved else o
                           for o in request.observations),
    )


#: The plan translations (lattice phases) a relayout tries besides the failure's own (``_relayout``'s ``phases``).
RELAYOUT_PHASES = 4
#: How many times a relayout gives the chain it fails at its next mount of another geometry (``_relayout``'s ``phases``).
RELAYOUT_SWAPS = 4


def _relayout(search: _Search, failure: dict[str, Any], catalog: KernelCatalog, request: AssemblyResolutionRequestV2,
              charge: Callable[[int], None], *, phases: bool = False) -> tuple[bool, dict[str, Any] | None] | None:
    """The whole design ``request`` (the failure's design with some elements moved) laid out again at the failure's
    translation and orientation on a board large enough (its hole lattice continued without edge): the beam is traced
    again (it must visit the same elements in the same order) and every chain is placed again, depth first. None when the
    move changes the beam or leaves a moved optic without a support at its height; else whether every chain was placed
    and the deepest failure (the one reached with the most chains placed). A placement counts only at a lattice phase
    the search itself tries for the moved design (its plan translations), so that a position found here is one the
    resolution finds too; with ``phases``, the first ``RELAYOUT_PHASES`` of those translations are tried when the
    failure's own does not place every chain (the moved design may stand elsewhere on the grid). The chains stand on the
    mounts the search tries first (``_first_plans``) when they can, the failure's otherwise; with ``phases``, the chain
    the layout fails at then takes its next mount of another geometry, up to ``RELAYOUT_SWAPS`` times, as the search's
    own mount fallback does."""

    _, yaw, _, _, _, _, _ = failure["_ctx"]
    first, lift = _first_plans(search, failure, catalog)
    result = _relayout_on(search, failure, catalog, request, charge, first, lift, phases)
    if not phases or result is None or result[0] or len(search.rankings) != len(first):
        return result
    # as the search does: the chain the layout fails at takes its next mount of another geometry, a few times
    current = list(first)
    tried = [{_mount_geometry_sig(p.mount)} for p in current]
    by_instance = {o.instance_id: o for o in request.optics}
    again = result
    for _ in range(RELAYOUT_SWAPS):
        deepest = again[1] if again is not None else None
        if deepest is None:
            break
        i = deepest["_depth"]
        swapped = False
        for _, mount, _ in search.rankings[i][0]:
            if _mount_geometry_sig(mount) in tried[i]:
                continue
            tried[i].add(_mount_geometry_sig(mount))
            optic = current[i].optic
            try:
                current[i] = _plan_chain(_optic_with_yaw(by_instance[optic.request.instance_id], optic.chain_key, yaw), mount, lift, catalog)
            except _ChainFailure:
                continue
            swapped = True
            break
        if not swapped:
            break
        again = _relayout_on(search, failure, catalog, request, charge, current, lift, phases)
        if again is None:
            break
        if again[0]:
            return again
    return result


def _relayout_on(search: _Search, failure: dict[str, Any], catalog: KernelCatalog, request: AssemblyResolutionRequestV2,
                 charge: Callable[[int], None], plans: Sequence[_ChainPlan], lift: float, phases: bool) -> tuple[bool, dict[str, Any] | None] | None:
    """``_relayout`` with the chains on the given plans (mounts) and lift."""

    board, yaw, _, t, _, _, _ = failure["_ctx"]
    policy = catalog.policy
    paths = trace_beam_paths(request.externals, request.optics, request.observations, **beam_trace_settings(policy),
                             charge=charge)
    if [p.through for p in paths] != [p.through for p in search.paths]:
        return None  # the move changes which elements the beam visits, or in which order: not this design any more
    by_instance = {o.instance_id: o for o in request.optics}
    moved_plans = []
    for plan in plans:
        optic = by_instance[plan.optic.request.instance_id]
        if optic.position_design == plan.optic.request.position_design:
            moved_plans.append(plan)
            continue
        try:
            moved_plans.append(_plan_chain(_optic_with_yaw(optic, plan.optic.chain_key, yaw), plan.mount, lift, catalog))
        except _ChainFailure:
            return None
    relative = []
    for path in paths:
        points = [rotate(yaw, point) for point in path.points_design]
        points = [(point[0], point[1], point[2] + lift) for point in points]
        relative.extend((points[k], points[k + 1], frozenset({path.through[k], path.through[k + 1]})) for k in range(len(points) - 1))
    reserved = _reserved_bodies(request.externals, yaw, lift)

    def place(at: tuple[float, float]) -> tuple[bool, dict[str, Any] | None]:
        segments = [(add(p0, (at[0], at[1], 0.0)), add(p1, (at[0], at[1], 0.0)), ends) for p0, p1, ends in relative]
        state = _Placement(board, policy, segments, charge)
        state.unbounded = True
        state.reserve(_shifted(reserved, at))
        placing = _DepthFirst(moved_plans, at, board, state, _no_charge_placement, search.catalog.policy.max_layout_backtracks)
        return placing.run(), placing.deepest

    placed, deepest = place(t)
    if not placed and not phases:
        return placed, deepest
    # the search lays the design out only at the plan translations it tries (``_translation_candidates`` of the moved
    # design): the lattice phase of this one must be among theirs, else one of theirs must place every chain too
    charge(len(moved_plans) + len(reserved))
    boxes = [b for plan in moved_plans for bs in _chain_bodies(plan, (0.0, 0.0), None).values() for b in bs]
    boxes += [b for bs in reserved.values() for b in bs]
    tried = {_lattice_phase(board, at): at for at in _translation_candidates(moved_plans, board, plan_bounds(boxes), charge,
                                                                             limit=search.catalog.policy.max_translations)}
    if placed and _lattice_phase(board, t) in tried:
        return True, None
    tried.pop(_lattice_phase(board, t), None)
    best: dict[str, Any] | None = deepest
    for at in list(tried.values())[:RELAYOUT_PHASES]:
        placed, deepest = place(at)
        if placed:
            return True, None
        if deepest is not None and (best is None or deepest["_depth"] > best["_depth"]):
            best = deepest
    return False, best


def _first_plans(search: _Search, failure: dict[str, Any], catalog: KernelCatalog) -> tuple[Sequence[_ChainPlan], float]:
    """The chain plans (and lift) of the design on the mounts the search tries first for every chain (each chain's
    best-ranked mount): what a resolution of a moved design starts with, so that a move found on them is one it finds.
    The failure's own plans when those mounts do not make a chain at a common height (memoized per orientation)."""

    _, yaw, lift, _, plans, _, _ = failure["_ctx"]
    key = ("first-plans", yaw, tuple(_plan_sig(p) for p in plans))
    if key not in search.prepared:
        found: tuple[Sequence[_ChainPlan], float] = (plans, lift)
        if search.request is not None and len(search.rankings) == len(plans):
            chosen = [(plan.optic, search.rankings[i][0][0][1]) for i, plan in enumerate(plans)]
            try:
                first_lift = _choose_lift(chosen, catalog, search.request.beam_height_mm)
                first = [_plan_chain(_optic_with_yaw(o.request, o.chain_key, yaw), m, first_lift, catalog) for o, m in chosen]
                if not any(_intra_chain_conflicts(_chain_bodies(p, (0.0, 0.0), None)) for p in first):
                    found = (first, first_lift)
            except _ChainFailure:
                pass
        search.prepared[key] = found
    return search.prepared[key]


def _lattice_phase(board: KBoard, t: tuple[float, float]) -> tuple[float, float]:
    """Where the hole lattice falls under a design at plan translation ``t`` (what a placement depends on, given the
    lattice continued without edge)."""

    return (_phase(board.edge_offset_mm - t[0], board.pitch_mm), _phase(board.edge_offset_mm - t[1], board.pitch_mm))


#: The steps (tenths of the pitch, each way) within which a single move is checked the way the search would lay it out
#: (other grid phases and mount fallbacks, ``_relayout``'s ``phases``); further out, on the failure's grid phase only.
NEAR_STEPS = 4


def _layout_after_move(search: _Search, failure: dict[str, Any], catalog: KernelCatalog, moved: set[str], shift: Vec3,
                       charge: Callable[[int], None], *, thorough: bool = False) -> tuple[bool, dict[str, Any] | None] | None:
    """The whole design, with the ``moved`` elements shifted by ``shift`` (design frame), laid out again (``_relayout``)."""

    return _relayout(search, failure, catalog, _moved_request(search.request, moved, shift), charge, phases=thorough)  # type: ignore[arg-type]


def _below_floor(pairs: Sequence[_BeamPair], least: dict[tuple[str, str], float], moved: set[str], shift: Vec3) -> bool:
    """Whether a move leaves two beam neighbours closer than their supports alone can stand (``_least_pitch``, on its grid):
    no layout to try. Only the floor's grid of half a millimetre is left to the layout."""

    for pair in pairs:
        floor = least.get((pair.later, pair.earlier))
        if floor is None:
            continue
        change = (dot(shift, pair.u) if pair.later in moved else 0.0) - (dot(shift, pair.u) if pair.earlier in moved else 0.0)
        if pair.spacing + change < floor - 0.5:
            return True
    return False


#: The optic category whose optical power a suggested move keeps: a move never changes the spacing of a
#: powered optic (a lens or a curved mirror) and the next powered optic or observation plane on its beam (a lens pair, a
#: telescope, a concave mirror pair, a lens and the plane it images onto are linked groups; they move as a whole or not at
#: all). A flat mirror has no power: it belongs to no linked group.
_POWERED_CATEGORY = "LENS"
#: The form tokens of the mirrors with a curved surface in a public optical identity (``OS-OPT-MIR-CONCAVE-...``,
#: ``OS-OPT-MIR-OAP-...``): the one statement of a mirror's form the kernel's request carries (its mechanics do not differ).
_CURVED_MIRROR_FORMS = frozenset({"CONCAVE", "OAP"})
#: Optic categories that send the beam on in another direction: moved along their beam, everything after them moves too.
_REFLECTIVE = frozenset({"MIRROR", "BEAMSPLITTER_PLATE", "BEAMSPLITTER_CUBE"})


def curved_mirror_sku(optical_sku: str | None) -> bool:
    """Whether a public optical identity names a mirror with a curved surface (focusing or defocusing)."""

    parts = (optical_sku or "").split("-")
    return len(parts) > 3 and parts[:3] == ["OS", "OPT", "MIR"] and parts[3] in _CURVED_MIRROR_FORMS


def _powered_ids(request: AssemblyResolutionRequestV2) -> set[str]:
    """The design's optics with optical power: its lenses and curved mirrors (``curved_mirror_sku``; a customer-supplied
    component says so itself: ``ExternalRequest.powered``, or its beam category is a lens)."""

    out = {o.instance_id for o in request.optics if o.mechanics.category == _POWERED_CATEGORY or o.mechanics.curved
           or curved_mirror_sku(o.optical_sku)}
    return out | {e.instance_id for e in request.externals if e.role != "source" and (e.beam_category == _POWERED_CATEGORY or e.powered)}


def _split_group(moved: set[str], search: _Search) -> str | None:
    """The powered optic a move of ``moved`` would separate from the next powered optic or observation plane on its beam
    (the first found, in path order), else None."""

    request = search.request
    assert request is not None
    powered = _powered_ids(request)
    planes = {o.observation_id for o in request.observations}
    for path in search.paths:
        nodes = [n for k, n in enumerate(path.through) if (k == 0 or path.through[k - 1] != n) and (n in powered or n in planes)]
        for a, b in zip(nodes, nodes[1:]):
            if a in powered and (a in moved) != (b in moved):
                return a if a not in moved else b
    return None


def _group_from(start: str, search: _Search) -> tuple[str, set[str]] | None:
    """The element to move along its beam together with every element after it, starting from ``start``, so that no
    linked group is split: ``start`` itself, else (repeatedly) the powered optic before it that it is linked to. None when every
    such move splits a group."""

    current, seen = start, set()
    while current not in seen:
        seen.add(current)
        moved = _downstream(current, search.paths)
        split = _split_group(moved, search)
        if split is None:
            return current, moved
        if split in moved:  # pragma: no cover - a group of everything after an element never leaves a later powered optic behind
            return None
        current = split
    return None  # pragma: no cover


def _moves_of(instance: str, search: _Search, *, spread: bool = False) -> list[tuple[str, set[str]]]:
    """The moves of ``instance`` along a beam that split no linked group, as (the element whose beam the move follows,
    the elements moved): a transmissive optic alone (a mirror or splitter moved alone would send the beam past what
    follows it); a transmissive optic with the transmissive optics after it up to the next powered optic or observation plane
    (a run of filters inside a linked group moves without changing the group's spacing); and the element with everything
    after it on its beam (``_group_from``: from the first powered optic of its linked group). A single move tries them in that
    order (the nearest, smallest change first); a spread tries the element with everything after it first, which widens
    one gap without narrowing the next."""

    request = search.request
    assert request is not None
    optic = next((o for o in request.optics if o.instance_id == instance), None)
    out: list[tuple[str, set[str]]] = []
    transmissive = optic is not None and optic.mechanics.category not in _REFLECTIVE
    if transmissive and _split_group({instance}, search) is None:
        out.append((instance, {instance}))
        run = _run_from(instance, search)
        if len(run) > 1 and _split_group(run, search) is None:
            out.append((instance, run))
    group = _group_from(instance, search)
    if group is not None and group not in out:
        if spread and group[0] == instance:
            out.insert(0, group)
        else:
            out.append(group)
    return out


def _run_from(instance: str, search: _Search) -> set[str]:
    """``instance`` and the elements after it on its beam up to, not including, the next powered optic, observation plane,
    mirror or splitter (on the first path that visits it)."""

    request = search.request
    assert request is not None
    stops = _powered_ids(request) | {o.observation_id for o in request.observations}
    stops |= {o.instance_id for o in request.optics if o.mechanics.category in _REFLECTIVE}
    stops |= {e.instance_id for e in request.externals if e.role == "source"}
    for path in search.paths:
        if instance not in path.through:
            continue
        run = {instance}
        for name in path.through[path.through.index(instance) + 1:]:
            if name in stops:
                break
            run.add(name)
        return run
    return {instance}


def _incoming(instance: str, paths: Sequence[BeamPathV2]) -> Vec3 | None:
    """The design direction (unit) of the nominal beam arriving at ``instance``, None if no beam arrives at it."""

    for path in paths:
        for k in range(1, len(path.through)):
            if path.through[k] == instance and path.through[k - 1] != instance:
                d = sub(path.points_design[k], path.points_design[k - 1])
                n = norm(d)
                if n > _EPS:
                    return scale(d, 1.0 / n)
    return None


def _positions(request: AssemblyResolutionRequestV2, shifts: dict[str, Vec3]) -> dict[str, list[float]]:
    """The design positions of the shifted elements (components and observation planes), by id."""

    where: dict[str, Vec3] = {o.instance_id: o.position_design for o in request.optics}
    where.update({e.instance_id: e.position_design for e in request.externals})
    where.update({o.observation_id: o.position_design for o in request.observations})
    return {name: [_round(where[name][i] + shifts[name][i], 3) for i in range(3)] for name in sorted(shifts)}


def _spread(search: _Search, failure: dict[str, Any], catalog: KernelCatalog, charge: Callable[[int], None],
            result: tuple[bool, dict[str, Any] | None]) -> dict[str, list[float]] | None:
    """New positions at which the whole design is laid out when no single move does (a row of supports at one
    pitch, two arms of a splitter): the conflict the layout ends with is removed by the smallest move along the beam that
    places more chains -- the later element of the conflicting pair moved together with every element after it (a
    linked group as a whole), which only widens its gap to the element before it -- and so on until every chain is
    placed. Each move must place more chains than the one before, so there are at most as many moves as chains. The
    positions are a layout that works at the failure's translation (``_compact`` then makes them tighter). None when a conflict is not removed by such a move within the diagnosis' reach
    or allowance."""

    board, _, _, _, plans, _, _ = failure["_ctx"]
    request = search.request
    assert request is not None
    current = request
    shifts: dict[str, Vec3] = {}
    step = board.pitch_mm / _PITCH_FRACTIONS
    chains = {p.optic.chain_key: p.optic.request.instance_id for p in plans}
    for _ in range(len(plans) + 1):
        placed, deepest = result
        if placed:
            return _positions(request, shifts)
        if deepest is None or deepest.get("reason") not in _CONFLICT_REASONS:
            return None
        depth = deepest["_depth"]
        failing = plans[depth].optic.request.instance_id
        other = chains.get(deepest.get("with") or "") or search.externals.get(deepest.get("with") or "")
        if other is None and "_segment" in deepest:  # a blocked beam: the element after the failing one on it, if any
            ends = [e for e in sorted(deepest["_segment"][2]) if e != failing and e in chains.values()]
            following = _downstream(failing, search.paths)
            other = next((e for e in ends if e in following), ends[0] if ends else None)
        starts = [failing]
        if other is not None and other != failing:
            starts = [other, failing] if other in _downstream(failing, search.paths) else [failing, other]
        found = None
        for head, moved in (move for start in starts for move in _moves_of(start, search, spread=True)):
            direction = _incoming(head, search.paths)
            if direction is None:
                continue
            try:
                for k in range(1, DIAGNOSIS_POSITION_STEPS + 1):
                    shift = scale(direction, k * step)
                    trial = _moved_request(current, moved, shift)
                    again = _relayout(search, failure, catalog, trial, charge)
                    if again is not None and (again[0] or (again[1] is not None and again[1]["_depth"] > depth)):
                        found = (trial, again, moved, shift)
                        break
            except _BudgetExhausted:
                return None
            if found is not None:
                break
        if found is None:
            return None
        current, result = found[0], found[1]
        for name in found[2]:
            shifts[name] = add(shifts.get(name, (0.0, 0.0, 0.0)), found[3])
    return None  # pragma: no cover - every move places more chains


#: The largest extra gap (mm) ``_open_up`` gives every gap that may change, in steps of a tenth of the pitch.
OPEN_UP_MAX_MM = 50.0


def _open_up(search: _Search, failure: dict[str, Any], catalog: KernelCatalog, charge: Callable[[int], None],
             least: dict[tuple[str, str], float], confirm: Callable[[dict[str, list[float]]], bool]) -> dict[str, list[float]] | None:
    """New positions with every gap of the design at least the least spacing its two supports can stand at (``least``,
    from ``_pair_floor``) and every gap that may change opened by the same extra spacing, the smallest (in tenths of the
    pitch, up to ``OPEN_UP_MAX_MM``) at which the design is laid out (``_relayout``, then ``confirm``). Along each beam
    each element moves with everything after it, along the beam arriving at it. A linked group (a powered optic and the next
    powered optic or observation plane, on a straight stretch of beam) keeps its length: the elements between its ends get
    the least spacings they need and share what is left in proportion to their present gaps; a gap between a powered optic
    and the next powered optic or observation plane, and a gap from a light source, are kept. None when no such extra works within the reach
    or allowance (a group too short for what is inside it: the design has to change its optics)."""

    request = search.request
    assert request is not None
    board = failure["_ctx"][0]
    powered = _powered_ids(request)
    planes = {o.observation_id for o in request.observations}
    sources = {e.instance_id for e in request.externals if e.role == "source"}
    paths = []
    for path in search.paths:  # each path's elements once each, in order, with their design positions
        names, points = [], []
        for name, point in zip(path.through, path.points_design, strict=True):
            if not names or names[-1] != name:
                names.append(name)
                points.append(point)
        paths.append((names, points))

    def need(earlier: str, later: str) -> float:
        """The least spacing the supports of two neighbouring optics can stand at (0 when not known: one of them has no
        support, or it was not found)."""

        return least.get((later, earlier), 0.0)

    def layout(extra: float) -> dict[str, list[float]] | None:
        shifts: dict[str, Vec3] = {}
        for names, points in paths:
            carried = shifts.get(names[0], (0.0, 0.0, 0.0))
            k = 1
            while k < len(names):
                gap = sub(points[k], points[k - 1])
                length = norm(gap)
                if length <= _EPS:
                    shifts.setdefault(names[k], carried)
                    k += 1
                    continue
                u = scale(gap, 1.0 / length)
                end = None
                if names[k - 1] in powered:  # a linked group from this powered optic to the next one or an observation plane
                    end = next((j for j in range(k, len(names)) if names[j] in powered or names[j] in planes), None)
                if end is not None and end > k:
                    stretch = [sub(points[j], points[j - 1]) for j in range(k, end + 1)]
                    gaps = [norm(v) for v in stretch]
                    total = sum(gaps)
                    straight = all(g > _EPS and dot(scale(v, 1.0 / g), u) > 1.0 - _EPS for v, g in zip(stretch, gaps, strict=True))
                    required = [need(names[j - 1], names[j]) for j in range(k, end + 1)]
                    if straight and sum(required) <= total + _EPS:
                        slack = total - sum(required)
                        new = [r + slack * g / total for r, g in zip(required, gaps, strict=True)]
                    else:
                        new = gaps
                    along_old = along_new = 0.0
                    for j, (g, g2) in enumerate(zip(gaps, new, strict=True), start=k):
                        along_old += g
                        along_new += g2
                        shifts.setdefault(names[j], add(carried, scale(u, along_new - along_old)))
                    carried = shifts[names[end]]
                    k = end + 1
                    continue
                if end is None and names[k - 1] not in sources:
                    wanted = max(length, need(names[k - 1], names[k]))
                    if math.isinf(wanted):
                        return None
                    carried = add(carried, scale(u, wanted - length + extra))
                shifts.setdefault(names[k], carried)
                carried = shifts[names[k]]
                k += 1
        moved = {n: v for n, v in shifts.items() if norm(v) > _EPS}
        return _positions(request, moved) if moved else None

    step = board.pitch_mm / _PITCH_FRACTIONS
    tried: set[tuple] = set()
    confirmed = 0
    for n in range(0, int(OPEN_UP_MAX_MM / step) + 1):
        positions = layout(n * step)
        if positions is None:
            continue
        key = tuple(sorted((k, tuple(v)) for k, v in positions.items()))
        if key in tried:
            continue
        tried.add(key)
        result = _relayout(search, failure, catalog, _placed(request, positions), charge)
        if result is not None and result[0]:
            if confirm(positions):
                return positions
            confirmed += 1
            if confirmed >= DIAGNOSIS_VERIFICATIONS:
                return None
    return None


#: The grid phases (fractions of the pitch along the pair's beam) a pair of supports is tried at (``_least_pitch``): the
#: layout's translation decides where the grid falls under them.
PAIR_PHASES = 5


def _stands(plan: _ChainPlan, other: _ChainPlan, board: KBoard, t: tuple[float, float], policy: ResolverPolicyV1,
            charge: Callable[[int], None], u: tuple[float, float], extra: float) -> bool:
    """Whether the supports of ``other`` and of ``plan`` moved by ``extra`` along ``u`` (lab plan) stand side by side on
    the hole lattice continued without edge, at some phase of the grid along ``u`` (``PAIR_PHASES``)."""

    moved = plan if extra == 0.0 else replace(plan, optic_position_lab_rel=add(plan.optic_position_lab_rel, (u[0] * extra, u[1] * extra, 0.0)),
                                              mount_position_rel=add(plan.mount_position_rel, (u[0] * extra, u[1] * extra, 0.0)))
    for j in range(PAIR_PHASES):
        phase = (t[0] + u[0] * j * board.pitch_mm / PAIR_PHASES, t[1] + u[1] * j * board.pitch_mm / PAIR_PHASES)
        state = _Placement(board, policy, (), charge)
        state.unbounded = True
        if _DepthFirst((other, moved), phase, board, state, _no_charge_placement, policy.max_layout_backtracks).run():
            return True
    return False


def _least_pitch(plan: _ChainPlan, other: _ChainPlan, board: KBoard, t: tuple[float, float], policy: ResolverPolicyV1,
                 charge: Callable[[int], None], spacing: float, u: tuple[float, float]) -> float | None:
    """The least spacing (mm, on a 0.5 mm grid from a tenth of the pitch up) at which the supports of two neighbouring
    optics alone can stand side by side (``_stands``): ``plan`` placed that far from ``other`` along ``u``. A lower bound
    of what the pair needs in the design (its neighbours take part too); None when not found within reach. A spent
    allowance raises ``_BudgetExhausted``."""

    step = board.pitch_mm / _PITCH_FRACTIONS
    for k in range(1, int(spacing / step) + DIAGNOSIS_POSITION_STEPS + 1):
        if _stands(plan, other, board, t, policy, charge, u, k * step - spacing):
            at = k * step
            if k > 1:
                for n in range(1, int(round(step / 0.5))):
                    if _stands(plan, other, board, t, policy, charge, u, (k - 1) * step + n * 0.5 - spacing):
                        at = (k - 1) * step + n * 0.5
                        break
            return _round_up(at)
    return None


def _no_charge_placement() -> None:
    """The placement charge of a diagnosis (bounded by its own geometry-check allowance instead)."""


#: How far (in tenths of the board pitch, each way) the diagnosis looks for the nearest position that works.
DIAGNOSIS_POSITION_STEPS = 40


@dataclass(frozen=True)
class _BeamPair:
    """Two OpenSci optics next to each other on a beam: ``later`` is visited after ``earlier``; ``spacing`` (mm) is the
    beam's length between them and ``u`` the design direction of the beam arriving at ``later``. Widening the pair moves
    ``later`` with ``tail`` along ``u``: every element after it on its beam (no other spacing changes), or inside a linked
    group the run of plain optics after it (up to the next powered optic, observation plane, mirror or splitter, which the run then
    comes closer to, by at most ``room``; a wider move takes ``full``, everything after it, and changes the group)."""

    later: str
    earlier: str
    spacing: float
    u: Vec3
    tail: frozenset[str]
    full: frozenset[str] = frozenset()
    room: float = math.inf

    def moved(self, extra: float) -> frozenset[str]:
        """What moves with ``later`` when the pair is widened by ``extra``: the run, unless that passes its room."""

        return self.tail if extra < self.room - _EPS else (self.full or self.tail)


def _beam_pairs(search: _Search, chains: set[str]) -> list[_BeamPair]:
    """Every pair of OpenSci optics (``chains``: their instance ids) next to each other on a beam, in path order."""

    out: dict[tuple[str, str], _BeamPair] = {}
    for path in search.paths:
        names, points = [], []
        for name, point in zip(path.through, path.points_design, strict=True):
            if not names or names[-1] != name:
                names.append(name)
                points.append(point)
        last: int | None = None
        for k, name in enumerate(names):
            if name not in chains:
                continue
            if last is not None and (name, names[last]) not in out:
                length = sum(norm(sub(points[j], points[j - 1])) for j in range(last + 1, k + 1))
                arriving = sub(points[k], points[k - 1])
                if length > _EPS and norm(arriving) > _EPS:
                    # with everything after it, else (inside a linked group) with the run of plain optics after it, so
                    # that the group keeps its spacing; else with everything after it (the group's spacing changes)
                    full = _downstream(name, search.paths)
                    tail, room = full, math.inf
                    plain = name not in _powered_ids(search.request) and all(  # type: ignore[arg-type]
                        o.mechanics.category not in _REFLECTIVE for o in search.request.optics if o.instance_id == name)  # type: ignore[union-attr]
                    if _split_group(full, search) is not None and plain:
                        run = _run_from(name, search)
                        if _split_group(run, search) is None:
                            tail = run
                            # the run may come no closer than touching the element after it (it would pass it)
                            end = max(j for j in range(k, len(names)) if names[j] in run)
                            room = norm(sub(points[end + 1], points[end])) if end + 1 < len(names) else math.inf
                    out[(name, names[last])] = _BeamPair(name, names[last], length, scale(arriving, 1.0 / norm(arriving)),
                                                         frozenset(tail), frozenset(full), room)
            last = k
    return list(out.values())


def _widened(request: AssemblyResolutionRequestV2, pairs: Sequence[_BeamPair], targets: dict[tuple[str, str], float]) -> dict[str, list[float]]:
    """The design positions after widening each pair to its target spacing (each later element moved with its tail
    along the beam arriving at it; the moves add up)."""

    shifts: dict[str, Vec3] = {}
    for pair in pairs:
        extra = targets.get((pair.later, pair.earlier), pair.spacing) - pair.spacing
        if extra <= _EPS:
            continue
        for name in pair.moved(extra):
            shifts[name] = add(shifts.get(name, (0.0, 0.0, 0.0)), scale(pair.u, extra))
    return _positions(request, shifts)


#: The step (mm) in which spacings a diagnosis raised are brought back, how many common shares of the way are tried first, and
#: the share of a stage one trial of new positions may spend (a position that fails must not take the trials after it).
COMPACT_GRAIN_MM = 0.5
TIGHTEN_SHARES = 4
COMPACT_TRIAL_SHARE = 32
#: How far new positions were made tighter (``suggested_positions_tightness``): every gap they widened stands at the least
#: spacing measured for its two supports; every gap that may shrink was reduced until the next ``COMPACT_GRAIN_MM`` no longer
#: resolved (a layout that holds, not proven the tightest: the grid decides, so a still smaller gap may work again); the
#: allowance ran out on the way (the positions hold, a tighter layout may exist).
POSITIONS_AT_LEAST_SPACING = "AT_LEAST_SPACING"
POSITIONS_COMPACTED = "COMPACTED"
POSITIONS_NOT_COMPACTED = "NOT_COMPACTED"
#: The design is laid out at the positions on some board, but the search, smallest board first, did not get there within
#: the diagnosis' allowance: the resolution itself may still run out of its budget.
POSITIONS_NOT_CONFIRMED = "NOT_CONFIRMED"


def _compact(search: _Search, failure: dict[str, Any], catalog: KernelCatalog, charge: Callable[[int], None], positions: dict[str, list[float]],
             pairs: Sequence[_BeamPair], least: dict[tuple[str, str], float], confirm: Callable[[dict[str, list[float]]], bool]) -> tuple[dict[str, list[float]], str]:
    """``positions`` (a layout the resolution confirmed) made tighter, and how far (``POSITIONS_*``): each gap they widened,
    the widest first, is bisected on a ``COMPACT_GRAIN_MM`` grid between where it stands and the larger of its drawn
    spacing and its supports' least spacing (``least``), by moving the later element with everything after it, as long as
    that splits no linked group. A trial counts when the whole design is laid out (``_relayout``, the cheap test) and the
    resolution confirms it (``confirm``), so every step taken holds. One sweep: the grid makes the outcome depend on the
    order, so the result is a layout that holds, not a proven tightest one."""

    request = search.request
    assert request is not None
    where: dict[str, Vec3] = {o.instance_id: o.position_design for o in request.optics}
    where.update({e.instance_id: e.position_design for e in request.externals})
    where.update({o.observation_id: o.position_design for o in request.observations})
    shifts: dict[str, Vec3] = {name: sub(tuple(value), where[name]) for name, value in positions.items()}  # type: ignore[arg-type]
    nothing: Vec3 = (0.0, 0.0, 0.0)

    def gap(pair: _BeamPair) -> float:
        return pair.spacing + dot(sub(shifts.get(pair.later, nothing), shifts.get(pair.earlier, nothing)), pair.u)

    def floor_of(pair: _BeamPair) -> float:
        return max(pair.spacing, least.get((pair.later, pair.earlier), 0.0))

    def layout(of: dict[str, Vec3]) -> dict[str, list[float]]:
        return _positions(request, {name: value for name, value in of.items() if norm(value) > _EPS})

    def narrowed(start: dict[str, Vec3], pair: _BeamPair, moved: set[str], by: float) -> dict[str, Vec3]:
        back = scale(pair.u, -by)
        return {**start, **{name: add(start.get(name, nothing), back) for name in moved}}

    def holds(of: dict[str, Vec3]) -> bool:
        trial = layout(of)
        result = _relayout(search, failure, catalog, _placed(request, trial), charge, phases=True)
        return result is not None and result[0] and confirm(trial)

    order = sorted(pairs, key=lambda p: (-(gap(p) - p.spacing), p.later, p.earlier))
    try:
        for pair in order:
            steps = math.floor((gap(pair) - floor_of(pair)) / COMPACT_GRAIN_MM + _EPS)
            if steps < 1:
                continue
            moved = _downstream(pair.later, search.paths)
            if _split_group(moved, search) is not None:
                continue  # the gap belongs to a linked group: it is not this diagnosis's to change
            start = shifts
            low, high = 0, steps  # ``low`` holds; test the whole way first, it is the common case of a generous opening
            if holds(narrowed(start, pair, moved, steps * COMPACT_GRAIN_MM)):
                low = steps
                shifts = narrowed(start, pair, moved, low * COMPACT_GRAIN_MM)
            while high - low > 1:
                middle = (low + high) // 2
                if holds(narrowed(start, pair, moved, middle * COMPACT_GRAIN_MM)):
                    low = middle
                    shifts = narrowed(start, pair, moved, low * COMPACT_GRAIN_MM)
                else:
                    high = middle
    except _BudgetExhausted:
        return layout(shifts), POSITIONS_NOT_COMPACTED
    widened = [p for p in pairs if gap(p) > p.spacing + _MICRO]
    if not widened:
        return layout(shifts), POSITIONS_NOT_COMPACTED  # no gap between beam neighbours to say anything of
    at_least = all(gap(p) <= floor_of(p) + COMPACT_GRAIN_MM for p in widened)
    return layout(shifts), POSITIONS_AT_LEAST_SPACING if at_least else POSITIONS_COMPACTED


#: How many times a diagnosis resolves the design again with its pairs widened before it publishes them unconfirmed.
PAIR_ROUNDS = 32
#: How many times one gap gets the plain step before its step doubles.
PAIR_EVEN_ROUNDS = 1
#: The share of the confirmation stage one of those resolutions may spend: a design that still fails must not take the
#: rounds after it.
PAIR_ROUND_SHARE = 4


def _reach(plan: _ChainPlan) -> float:
    """The farthest (mm, in plan) any body of a chain reaches from its optic, at any fixation: its mount, its post, and its
    holder turned towards any hole."""

    envelope, base = plan.mount.envelope, plan.stack.holder.base.body
    mount = max(math.hypot(x, y) for x in (envelope.min_mm[0], envelope.max_mm[0]) for y in (envelope.min_mm[1], envelope.max_mm[1]))
    fork = max(math.hypot(x, y) for x in (base.min_mm[0], base.max_mm[0]) for y in (base.min_mm[1], base.max_mm[1]))
    offset = math.hypot(plan.optic_position_lab_rel[0] - plan.mount_position_rel[0], plan.optic_position_lab_rel[1] - plan.mount_position_rel[1])
    return offset + max(mount, fork, plan.stack.holder.tube_diameter_mm / 2.0, plan.stack.post.diameter_mm / 2.0)


def _out_of_reach(plan: _ChainPlan, other: _ChainPlan, policy: ResolverPolicyV1) -> float:
    """The spacing (mm) from which on the supports of two optics cannot meet or want one hole, however their forks turn."""

    return _round_up(_reach(plan) + _reach(other) + policy.inter_chain_clearance_mm)


def _with_every_gap(pairs: Sequence[_BeamPair], least: dict[tuple[str, str], float]) -> dict[tuple[str, str], float]:
    """``least`` with the gaps the floor did not get to at the largest it measured (they likely need about as much)."""

    largest = max(least.values(), default=0.0)
    return {**{(p.later, p.earlier): largest for p in pairs}, **least}


def _beyond_reach(search: _Search, catalog: KernelCatalog, pairs: Sequence[_BeamPair], targets: dict[tuple[str, str], float],
                  plans: Sequence[_ChainPlan], least: dict[tuple[str, str], float]) -> dict[tuple[str, str], float]:
    """``targets`` raised to where the two supports of each pair cannot meet any more (``_out_of_reach``), for spacings the
    diagnosis could not confirm: as far on the way there as still lets the design lie on the largest standard board, with
    the gaps not listed at what they need (a number that takes the design off every board would leave nothing to apply)."""

    request = search.request
    assert request is not None
    by_instance = {p.optic.request.instance_id: p for p in plans}
    by_key = {(p.later, p.earlier): p for p in pairs}
    far = {key: max(value, _out_of_reach(by_instance[by_key[key].later], by_instance[by_key[key].earlier], catalog.policy)) for key, value in targets.items()}
    margin = 2.0 * catalog.policy.board_edge_margin_mm
    pad = 2.0 * max((_reach(plan) for plan in plans), default=0.0)
    sizes = [sorted((board.size_x_mm - margin, board.size_y_mm - margin)) for board in catalog.boards]

    def raised(share: float) -> dict[tuple[str, str], float]:
        return {key: _round_up(targets[key] + share * (far[key] - targets[key])) for key in targets}

    others = {key: value for key, value in _with_every_gap(pairs, least).items() if key not in targets}

    def on_a_board(share: float) -> bool:
        moved = _widened(request, pairs, {**others, **raised(share)})
        points = [moved.get(o.instance_id, o.position_design) for o in request.optics]
        extent = sorted((max(p[0] for p in points) - min(p[0] for p in points) + pad, max(p[1] for p in points) - min(p[1] for p in points) + pad))
        return any(extent[0] <= size[0] and extent[1] <= size[1] for size in sizes)

    if on_a_board(1.0):
        return far
    low, high = 0.0, 1.0
    for _ in range(8):
        middle = (low + high) / 2.0
        low, high = (middle, high) if on_a_board(middle) else (low, middle)
    return raised(low)


def _pair_floors(pairs: Sequence[_BeamPair], least: dict[tuple[str, str], float]) -> dict[tuple[str, str], float]:
    """The spacing a pair's target is not brought below: the least its supports alone stand at, and wider than it is drawn."""

    return {(p.later, p.earlier): max(least.get((p.later, p.earlier), 0.0), p.spacing + COMPACT_GRAIN_MM) for p in pairs}


def _tighten_pairs(pairs: Sequence[_BeamPair], targets: dict[tuple[str, str], float], least: dict[tuple[str, str], float],
                   holds: Callable[[dict[str, list[float]]], bool], request: AssemblyResolutionRequestV2) -> None:
    """Confirmed ``targets`` brought back as long as the design still holds (the raising went up by doubling, or to where
    the supports cannot meet): first all together, the same share of the way to the least spacing of their supports; then
    each one, the one furthest from its floor first, bisected in steps of ``COMPACT_GRAIN_MM``. Stops when the allowance
    runs out; every step taken holds."""

    floors = _pair_floors(pairs, least)
    try:
        low_share, high_share = 0.0, 1.0  # the share of the way from each floor to its target that still holds
        start = dict(targets)

        def shared(share: float) -> dict[tuple[str, str], float]:
            return {key: max(_round_up(floors[key] + share * (start[key] - floors[key])), floors[key]) if start[key] > floors[key] else start[key] for key in start}

        for _ in range(TIGHTEN_SHARES):
            middle = (low_share + high_share) / 2.0
            if holds(_widened(request, pairs, shared(middle))):
                high_share = middle
                targets.update(shared(middle))
            else:
                low_share = middle
        for key in sorted(targets, key=lambda k: (floors[k] - targets[k], k)):
            low, high = 0, math.floor((targets[key] - floors[key]) / COMPACT_GRAIN_MM + _EPS)
            at = targets[key]

            def works(n: int) -> bool:
                return holds(_widened(request, pairs, {**targets, key: _round(at - n * COMPACT_GRAIN_MM, 1)}))

            if high < 1:
                continue
            if works(high):
                low = high
            while high - low > 1:
                middle = (low + high) // 2
                if works(middle):
                    low = middle
                else:
                    high = middle
            targets[key] = _round(at - low * COMPACT_GRAIN_MM, 1)
    except _BudgetExhausted:
        return


#: The plan translations ``_fit_board`` tries per board, best first, the revisits at each, and how many steps above its
#: floor ``_settle_pairs`` tries a gap for one board.
FIT_TRANSLATIONS = 8
FIT_BACKTRACKS = 16
SETTLE_LEVELS = 8
#: How far towards ``_beyond_reach`` a diagnosis tries spacings it could not confirm by raising them step by step.
BEYOND_REACH_SHARES = (0.25, 0.5, 1.0)


def _fit_board(search: _Search, failure: dict[str, Any], catalog: KernelCatalog, request: AssemblyResolutionRequestV2, charge: Callable[[int], None],
               boards: Sequence[KBoard]) -> KBoard | None:
    """The first of ``boards`` on which ``request`` (the failure's design with elements moved) stands as the search would
    accept it: every chain on the mount it tries first, and every requested DOF those mounts offer delivered (a layout that
    loses one sends the search on to a larger board). Under ``require_all_retained_dofs`` (``full_fallbacks``) a NUMERICAL
    request does not count: a manual adjuster cannot meet it there, so it is not worth a larger board. Takes a layout per
    board, not a search; None when none takes it."""

    _, yaw, _, _, _, _, _ = failure["_ctx"]
    first, lift = _first_plans(search, failure, catalog)
    policy = catalog.policy
    paths = trace_beam_paths(request.externals, request.optics, request.observations, **beam_trace_settings(policy), charge=charge)
    if [p.through for p in paths] != [p.through for p in search.paths]:
        return None
    by_instance = {o.instance_id: o for o in request.optics}
    counted = [{d.axis for d in p.optic.request.retained_dofs if d.control != "NUMERICAL" or not search.full_fallbacks} for p in first]
    deliverable = sum(len(counted[i] & set(ranking[0][0][2])) for i, ranking in enumerate(search.rankings)) if len(search.rankings) == len(first) else 0
    turned = snap_quat(q_mul(QUARTER_TURN, yaw))
    margin = 2.0 * policy.board_edge_margin_mm
    variants: dict[Quat, Any] = {}

    def variant(orientation: Quat) -> Any:
        if orientation not in variants:
            charge(len(first))
            try:
                plans = [_plan_chain(_optic_with_yaw(by_instance[p.optic.request.instance_id], p.optic.chain_key, orientation), p.mount, lift, catalog) for p in first]
            except _ChainFailure:
                variants[orientation] = None
                return None
            relative = []
            for path in paths:
                points = [rotate(orientation, point) for point in path.points_design]
                points = [(point[0], point[1], point[2] + lift) for point in points]
                relative.extend((points[k], points[k + 1], frozenset({path.through[k], path.through[k + 1]})) for k in range(len(points) - 1))
            reserved = _reserved_bodies(request.externals, orientation, lift)
            boxes = [b for plan in plans for bs in _chain_bodies(plan, (0.0, 0.0), None).values() for b in bs] + [b for bs in reserved.values() for b in bs]
            variants[orientation] = (plans, relative, reserved, plan_bounds(boxes))
        return variants[orientation]

    for board in boards:
        for orientation in ([yaw] if board.size_x_mm == board.size_y_mm else [yaw, turned]):
            found = variant(orientation)
            if found is None:
                continue
            plans, relative, reserved, bounds = found
            (x0, y0), (x1, y1) = bounds
            if (x1 - x0) + margin > board.size_x_mm + _EPS or (y1 - y0) + margin > board.size_y_mm + _EPS:
                continue
            for at in _translation_candidates(plans, board, bounds, charge, limit=search.catalog.policy.max_translations)[:FIT_TRANSLATIONS]:
                segments = [(add(p0, (at[0], at[1], 0.0)), add(p1, (at[0], at[1], 0.0)), ends) for p0, p1, ends in relative]
                state = _Placement(board, policy, segments, charge)
                if not state.reserve(_shifted(reserved, at)) or not _DepthFirst(plans, at, board, state, _no_charge_placement, FIT_BACKTRACKS).run():
                    continue
                layout = _Layout(board, at, dict(state.fixations), dict(state.placed), bounds)
                delivered = sum(1 for i, plan in enumerate(plans) for d in _implement_dofs(plan, layout, policy, orientation)
                                if d.status != "NOT_DELIVERED" and d.axis in counted[i])
                if delivered >= deliverable:
                    return board
                break  # the first translation that places them is the one the search keeps: on to the next board
    return None


def _settle_pairs(search: _Search, failure: dict[str, Any], catalog: KernelCatalog, charge: Callable[[int], None], pairs: Sequence[_BeamPair],
                  targets: dict[tuple[str, str], float], least: dict[tuple[str, str], float], step: float) -> KBoard | None:
    """Confirmed ``targets`` brought to the smallest board that takes the design as the search would accept it (``_fit_board``),
    at the least spacings that board takes: the grid decides which spacings a board takes, so the least spacings alone may
    need a much larger board, and one slightly wider gap a much smaller one. For each smaller board, the next smaller first:
    each gap alone from its floor up to its target with the others at theirs; each gap alone a few steps above its floor with
    the others at their floors; then all together. Then each is brought back as far as the design stays on the board it
    ended on (``_tighten_pairs``). Returns that board; None, leaving the targets as they are, when the design stands on no
    board this way. A spent allowance raises ``_BudgetExhausted``, with the targets at a state that holds."""

    request = search.request
    assert request is not None
    order = _board_order(catalog.boards)
    floors = _pair_floors(pairs, least)

    def fit(values: dict[tuple[str, str], float], boards: Sequence[KBoard]) -> KBoard | None:
        return _fit_board(search, failure, catalog, _placed(request, _widened(request, pairs, values)), charge, boards)

    def trials() -> Iterable[dict[tuple[str, str], float]]:
        keys = sorted(targets)
        lowest = {key: floors[key] for key in keys}
        for key in keys:
            levels = math.ceil((targets[key] - floors[key]) / step)
            for level in range(levels):
                yield {**targets, key: _round(floors[key] + level * step, 1)}
        for key in keys:
            for level in range(SETTLE_LEVELS + 1):
                yield {**lowest, key: _round(floors[key] + level * step, 1)}
        for level in range(1, SETTLE_LEVELS + 1):
            yield {key: _round(floors[key] + level * step, 1) for key in keys}

    current = fit(targets, order)
    if current is None:
        return None
    seen: set[tuple[float, float]] = set()
    for board in reversed(order):
        size = (board.size_x_mm, board.size_y_mm)
        if _board_rank(board) >= _board_rank(current) or size in seen:
            continue
        seen.add(size)
        for values in trials():
            if fit(values, [board]) is not None:
                targets.clear()
                targets.update(values)
                current = board
                break
    _tighten_pairs(pairs, targets, least, lambda moved: _fit_board(search, failure, catalog, _placed(request, moved), charge, [current]) is not None, request)
    return current


def _named_by(deepest: dict[str, Any] | None, plans: Sequence[_ChainPlan]) -> frozenset[str] | None:
    """The components a layout's deepest failure names: the chain it failed at and the chain in its way, if any."""

    if deepest is None:
        return None
    instances = {p.optic.chain_key: p.optic.request.instance_id for p in plans}
    names = {instances.get(deepest.get("chain") or ""), instances.get(deepest.get("with") or "")} - {None}
    if "_segment" in deepest:
        names |= {name for name in deepest["_segment"][2] if name in instances.values()}
    return frozenset(names) or None


def _gaps_of(names: frozenset[str], pairs: Sequence[_BeamPair], search: _Search) -> list[tuple[str, str]]:
    """The gaps between beam neighbours (later, earlier) to widen for components ``names`` that a refusal names: the pair
    they are; else the gap arriving at the later of the two on its beam (it then moves away from the other); else, on two
    different beams or for one component alone, the gap arriving at each."""

    direct = [(p.later, p.earlier) for p in pairs if {p.later, p.earlier} == names]
    if direct:
        return direct
    arriving = set(names)
    if len(names) == 2:
        a, b = sorted(names)
        if b in _downstream(a, search.paths):
            arriving = {b}
        elif a in _downstream(b, search.paths):
            arriving = {a}
    return [(p.later, p.earlier) for p in pairs if p.later in arriving]


def _hardware_of(plan: _ChainPlan) -> tuple:
    """What the least spacing of a chain's support depends on, besides where the grid falls (``_least_pitch`` tries its phases)."""

    return (plan.optic.request.mechanics.identity, plan.mount.identity, plan.stack.post.identity, plan.stack.holder.identity, plan.stack.depth)


def _pair_floor(search: _Search, failure: dict[str, Any], catalog: KernelCatalog, charge: Callable[[int], None],
                named: tuple[str, str | None]) -> tuple[list[_BeamPair], dict[tuple[str, str], float], dict[tuple[str, str], float]]:
    """The beam pairs of the design, the least spacing at which each pair's supports alone can stand (``_least_pitch``;
    missing when not found), and the pairs that certainly need more room with the spacing to start them at: below their
    least spacing, and the named pair (when it is a beam pair) one tenth of the pitch wider than it is. Stops, keeping what
    it has, when the allowance runs out."""

    board, yaw, _, t, plans, _, _ = failure["_ctx"]
    by_instance = {p.optic.request.instance_id: p for p in plans}
    pairs = _beam_pairs(search, set(by_instance))
    least: dict[tuple[str, str], float] = {}
    targets: dict[tuple[str, str], float] = {}
    alike: dict[tuple, float] = {}  # the same two supports along the same direction (a row of optics): measured once
    failing, other = named
    for pair in pairs:
        key = (pair.later, pair.earlier)
        if {pair.later, pair.earlier} == {failing, other}:
            targets[key] = _round(pair.spacing + board.pitch_mm / _PITCH_FRACTIONS, 1)
    for pair in pairs:
        key = (pair.later, pair.earlier)
        v = rotate(yaw, pair.u)
        n = math.sqrt(v[0] * v[0] + v[1] * v[1])
        u = (v[0] / n, v[1] / n) if n > _EPS else (1.0, 0.0)
        supports = (_hardware_of(by_instance[pair.later]), _hardware_of(by_instance[pair.earlier]), round(u[0], 6), round(u[1], 6))
        found = alike.get(supports)
        if found is None:
            try:
                found = _least_pitch(by_instance[pair.later], by_instance[pair.earlier], board, t, catalog.policy, charge, pair.spacing, u)
            except _BudgetExhausted:
                break
            if found is None:
                continue
            alike[supports] = found
        least[key] = found
        if found > pair.spacing + _EPS:
            targets[key] = max(targets.get(key, 0.0), found)
    return pairs, least, targets


def _confirm_pairs(request: AssemblyResolutionRequestV2, pairs: Sequence[_BeamPair], targets: dict[tuple[str, str], float], step: float,
                   outcome_of: Callable[[dict[str, list[float]]], Any], search: _Search) -> bool:
    """Lay out and resolve the design again with every pair widened to its target (``_widened``); while it is refused for
    some components, the gaps they name (``_gaps_of``) get more -- ``step``, for a gap named often (``PAIR_EVEN_ROUNDS``) twice
    as much each further time -- or join, up to ``PAIR_ROUNDS`` times. The targets only grow, from the least spacing up, so
    that they stop near the least that resolves; True when the design resolves with them (``outcome_of``: True, or the
    components the failure names)."""

    rounds: dict[tuple[str, str], int] = {}
    spacing = {(p.later, p.earlier): p.spacing for p in pairs}
    for _ in range(PAIR_ROUNDS):
        try:
            outcome = outcome_of(_widened(request, pairs, targets))
        except _BudgetExhausted:
            return False
        if outcome is True:
            return True
        keys = _gaps_of(outcome, pairs, search) if outcome else []
        if not keys:
            return False  # another kind of refusal, or no gap a move along a beam can widen
        for key in keys:
            rounds[key] = rounds.get(key, 0) + 1
            targets[key] = _round(max(targets.get(key, spacing[key]), spacing[key]) + step * 2 ** max(0, rounds[key] - PAIR_EVEN_ROUNDS), 1)
    return False


def _pair_entries(search: _Search, pairs: Sequence[_BeamPair], targets: dict[tuple[str, str], float]) -> list[dict[str, Any]]:
    """``conflicting_pairs``: each pair to widen, in path order, with the spacing to widen it to (``min_support_pitch_mm``),
    the beam direction to move ``instance_id`` along (with ``moved_with``), and ``linked`` when widening it changes the
    spacing of a powered optic and the next powered optic or observation plane after it (the design has to change its
    optics)."""

    out = []
    for pair in pairs:
        target = targets.get((pair.later, pair.earlier))
        if target is None:
            continue
        moved = pair.moved(target - pair.spacing)
        entry: dict[str, Any] = {"instance_id": pair.later, "with": pair.earlier, "spacing_mm": _round(pair.spacing, 1),
                                 "min_support_pitch_mm": target, "beam_direction_design": [_round(v, 6) + 0.0 for v in pair.u],
                                 "linked": _split_group(set(moved), search) is not None}
        if len(moved) > 1:
            entry["moved_with"] = sorted(moved - {pair.later})
        out.append(entry)
    return out


def _clearing_spacing(mine: Sequence[YawBox], theirs: Sequence[YawBox], away: tuple[float, float], enough: float,
                      charge: Callable[[int], None]) -> float:
    """The least move (mm, on a 0.1 mm grid) of the boxes ``mine`` along ``away`` from which on they no longer overlap
    ``theirs`` (the collision test of the layout, on the clearance-inflated bodies): scanned down from ``enough``, a move
    at which their projections on ``away`` are apart."""

    def clear(s: float) -> bool:
        moved = [YawBox(b.owner, b.lo, b.hi, Pose(add(b.pose.position, (away[0] * s, away[1] * s, 0.0)), b.pose.rotation)) for b in mine]
        charge(len(moved) * len(theirs))
        return not any(overlap_depth(a, b) > 0 for a in moved for b in theirs)

    spacing = enough
    while spacing > 0.1 and clear(_round(spacing - 0.1, 1)):
        spacing = _round(spacing - 0.1, 1)
    return spacing


def _lateral_clearance(box: YawBox, p0: Vec3, p1: Vec3, policy: ResolverPolicyV1) -> tuple[float, tuple[float, float]] | None:
    """The smallest move (mm, rounded up to 0.1 mm) of ``box`` across the beam segment ``p0 -> p1``, and its plan
    direction, after which the segment no longer meets the box inflated by the beam clearance (the test the layout
    applies). None when no move across the segment within the diagnosis' reach clears it."""

    d = (p1[0] - p0[0], p1[1] - p0[1])
    n = math.sqrt(d[0] * d[0] + d[1] * d[1])
    if n <= _EPS:
        return None
    normal = (-d[1] / n, d[0] / n)
    base = p0[0] * normal[0] + p0[1] * normal[1]
    lo, hi = _projection([box.inflated(policy.beam_clearance_mm)], normal)
    lo, hi = lo - base, hi - base
    options = sorted(((max(0.0, -lo), normal), (max(0.0, hi), (-normal[0], -normal[1]))), key=lambda o: o[0])
    for amount, direction in options:
        shift = _round_up(amount)
        for _ in range(_LATERAL_STEPS):
            moved = YawBox(box.owner, box.lo, box.hi, Pose(add(box.pose.position, (direction[0] * shift, direction[1] * shift, 0.0)), box.pose.rotation))
            if shift > 0.0 and segment_box_hit(p0, p1, moved, margin=policy.beam_clearance_mm) is None:
                return shift, direction
            shift = _round(shift + 0.1, 1)
    return None


def _move_set(instance: str, search: _Search, reflective: bool, shift: Vec3, charge: Callable[[int], None]) -> set[str] | None:
    """What moves with ``instance`` when it moves by ``shift`` (a spacing or clearance it is given): itself (a
    transmissive optic that is not linked), else every element after it on its beam (a mirror or splitter, so that the
    beam still visits them; a transmissive optic the move would take past the element after it) -- the first that splits
    no linked group and keeps the beam visiting the same elements in the same order; None when none does."""

    options: list[set[str]] = []
    if not reflective and _split_group({instance}, search) is None:
        options.append({instance})
    following = _downstream(instance, search.paths)
    if following not in options and _split_group(following, search) is None:
        options.append(following)
    for moved in options:
        if _keeps_beam(search, moved, shift, charge):
            return moved
    return None


def _keeps_beam(search: _Search, moved: set[str], shift: Vec3, charge: Callable[[int], None]) -> bool:
    """Whether the nominal beam still visits the same elements in the same order with ``moved`` shifted by ``shift``."""

    request = _moved_request(search.request, moved, shift)  # type: ignore[arg-type]
    paths = trace_beam_paths(request.externals, request.optics, request.observations,
                             **beam_trace_settings(search.catalog.policy), charge=charge)
    return [p.through for p in paths] == [p.through for p in search.paths]


def _placed(request: AssemblyResolutionRequestV2, positions: dict[str, list[float]]) -> AssemblyResolutionRequestV2:
    """``request`` with the named elements (components and observation planes) at the given design positions."""

    def at(name: str, position: Vec3) -> Vec3:
        return tuple(positions[name]) if name in positions else position  # type: ignore[return-value]

    return replace(request,
                   optics=tuple(replace(o, position_design=at(o.instance_id, o.position_design)) for o in request.optics),
                   externals=tuple(replace(e, position_design=at(e.instance_id, e.position_design)) for e in request.externals),
                   observations=tuple(replace(o, position_design=at(o.observation_id, o.position_design)) for o in request.observations))


#: The positions one move of a diagnosis has the resolution itself confirm (``resolves``) before it gives up on that move.
DIAGNOSIS_VERIFICATIONS = 3


#: The geometry checks a diagnosis keeps for its floor (``_pair_floor``: the least spacing of every pair of beam
#: neighbours), spent first, so that every refusal carries a spacing even when the stages after it run out.
DIAGNOSIS_FLOOR_CHECKS = 150_000


def _diagnose(search: _Search, failure: dict[str, Any], catalog: KernelCatalog, optics: Sequence[_Optic], *,
              pool: ResolutionBudget | None = None, full: bool = False) -> dict[str, Any]:
    """Customer-safe numbers for a layout failure: replay the failing placement and measure what would fix it, within
    ``DIAGNOSIS_FLOOR_CHECKS`` for the floor, ``policy.diagnosis_max_geometry_checks`` for each of the ``DIAGNOSIS_STAGES`` stages
    after it (every move tried, the positions, the pairs' confirmation), and, with ``pool``, within what is
    left of that budget's diagnosis account and its deadline (deterministic without a deadline: the same failure is
    diagnosed the same way).

    Every spacing failure carries the direction of the named component's beam (``beam_direction_design``) and, from the
    floor, ``conflicting_pairs``: the pairs of beam neighbours that need more room, each with the spacing to widen it to
    (``_pair_floor``, ``_confirm_pairs``). The nearest position that works is searched along the beam with the whole
    design laid out again: a transmissive optic moves alone, or with the plain optics after it, or with
    every element after it; a mirror or a beamsplitter always moves together with every element after it on its beam
    (``moved_with`` names them); when the element after it on the blocked beam stands in the way, that element moves
    instead (``instance_id`` of the suggestion). A move never splits a linked group (a powered optic and the next
    powered optic or observation plane on its beam). When no single move works, ``suggested_positions_design_mm`` gives new positions at
    which the whole design is laid out (``_open_up``, else ``_spread``); when there are none, the pairs' spacings are
    raised until the design resolves with them, then brought back as far as it still does (``pairs_confirmed`` true). What
    it could not confirm goes to where the two supports cannot meet any more, as far as the largest board lets it
    (``_beyond_reach``, ``pairs_confirmed`` false, ``min_support_pitch_mm`` then more than the design may need): not a lower
    bound that brings the pair back. Positions and confirmed spacings are ones at which the resolution itself lays the design out (resolved again,
    without diagnosis, within the allowance), so following them never brings the same failure back.

    A layout fallback's diagnosis (``pool``, while the design is being resolved) is the single move found on the layout;
    with ``full`` (a second pass for a refusal of the requested DOFs) it is a refusal's, confirmed by resolving the design
    with the requested hardware kept."""

    stage_checks = catalog.policy.diagnosis_max_geometry_checks
    allowance = ResolutionBudget(1, max_geometry_checks=DIAGNOSIS_FLOOR_CHECKS)
    total = DIAGNOSIS_FLOOR_CHECKS + DIAGNOSIS_STAGES * stage_checks + DIAGNOSIS_OWN_MOVE_CHECKS
    spent = 0  # in all stages

    def charge(checks: int) -> None:
        nonlocal spent
        if (allowance.geometry_checks + checks > allowance.max_geometry_checks
                or spent + checks > total
                or (pool is not None and not pool.take_diagnosis(checks))):
            raise _BudgetExhausted()
        allowance.take(checks=checks)
        spent += checks

    def renew(limit: int | None = None) -> None:  # a fresh allowance per stage, within the total
        nonlocal allowance  # (the pool, if any, is not renewed)
        allowance = ResolutionBudget(1, max_geometry_checks=max(1, stage_checks if limit is None else limit))

    board, yaw, lift, t, plans, segments, reserved = failure["_ctx"]
    policy = catalog.policy
    depth = failure["_depth"]
    plan = plans[depth]
    shifted = [(add(p0, (t[0], t[1], 0.0)), add(p1, (t[0], t[1], 0.0)), ends) for p0, p1, ends in segments]
    state = _Placement(board, policy, shifted, charge)
    state.unbounded = bool(failure.get("_unbounded"))
    try:
        if not state.reserve(_shifted(reserved, t)):  # pragma: no cover - the replay reproduces the recorded placement
            return {}
        for earlier, fixation in zip(plans[:depth], failure["_fixations"], strict=True):
            state.add(earlier.optic.chain_key, fixation, _ChainOptions(earlier, t, board, state).bodies(fixation))
        replay = _place_chain(plan, t, board, state)
    except _BudgetExhausted:
        return {}
    if not isinstance(replay, dict):  # pragma: no cover
        return {}
    out: dict[str, Any] = {}
    beam = _beam_direction(plan, shifted)
    instance = plan.optic.request.instance_id
    reason = replay["reason"]
    reflective = plan.optic.request.mechanics.category in _REFLECTIVE
    on_beam = search.request is not None and _incoming(instance, search.paths) is not None
    other: str | None = next((p.optic.request.instance_id for p in plans if p.optic.chain_key == replay.get("with")), None)
    clearing: tuple[float, ...] | None = None
    if reason == "INTER_CHAIN_COLLISION":
        # the extra spacing along the beam, away from the other support, at which this support's mount and post clear
        # the other support's mount and post (and the space reserved for a customer-supplied component), with the
        # clearance the collision test keeps (the clearance-inflated, turned bodies) -- the least such spacing; the
        # supports must then still stand on the lattice (the pair's spacing in ``conflicting_pairs`` raises it).
        # A fork in the way can turn: it sets no spacing (the nearest position does)
        other_key = replay["_bodies"][0]
        other_chain = chain_of(other_key)
        if other_chain in search.externals:
            out["with"] = search.externals[other_chain]
        half = policy.inter_chain_clearance_mm / 2.0
        mine = [b.inflated(half) for bs in _chain_bodies(plan, t, None).values() for b in bs if b.owner != pk(plan.optic.chain_key, "holder")]
        theirs = [b.inflated(half) for key in dict.fromkeys((pk(other_chain, "mount"), pk(other_chain, "post"), other_key))  # type: ignore[arg-type]
                  if role_of(key) != "holder" for b in state.placed.get(key, [])]
        if theirs:
            (a0, a1), (b0, b1) = _projection(mine, beam), _projection(theirs, beam)
            ahead = a0 + a1 >= b0 + b1
            extra = b1 - a0 if ahead else a1 - b0
            if extra > 0:
                away = beam if ahead else (-beam[0], -beam[1])
                clearing = (away[0], away[1])
                try:
                    out["min_extra_spacing_mm"] = _clearing_spacing(mine, theirs, away, _round_up(extra), charge)
                except _BudgetExhausted:
                    out["min_extra_spacing_mm"] = _round_up(extra)
    elif reason == "BEAM_OBSTRUCTED":
        p0, p1, ends, box = replay["_segment"]
        others = sorted(e for e in ends if e != instance)
        if others:
            out["with"] = others[0]
        optic_ends = [e for e in others if any(p.optic.request.instance_id == e for p in plans)]
        if other is None and optic_ends:
            # the element after this one on the blocked beam (else the one before it) is the other party
            following = _downstream(instance, search.paths) if search.paths else {instance}
            other = next((e for e in optic_ends if e in following), optic_ends[0])
        lateral = _lateral_clearance(box, p0, p1, policy)
        if lateral is not None:
            out["min_lateral_clearance_mm"] = lateral[0]
            out["free_direction_design"] = _design_dir(yaw, lateral[1])
            clearing = lateral[1]
    if reason in _CONFLICT_REASONS and search.request is not None:
        request = search.request
        direction = _design_dir(yaw, beam)
        out["beam_direction_design"] = direction
        # the floor first, within its own allowance: every pair of beam neighbours that certainly needs more room (a
        # plain layout fallback's diagnosis, while a design is being resolved, is the single move only: its numbers reach
        # a customer only through a refusal of the requested DOFs, whose second pass diagnoses them in full)
        confirming = pool is None or full
        pairs, least, targets = _pair_floor(search, failure, catalog, charge, (instance, other)) if confirming else ([], {}, {})

        def attempt(moved_request: AssemblyResolutionRequestV2, *, beyond_limit: bool = False, most: int | None = None) -> Any:
            """Resolve ``moved_request`` again without diagnosis, within what is left of the allowance (at most ``most``
            checks): True when it is laid out (a refusal's diagnosis: on a board the customer's limit excludes too, with
            ``beyond_limit`` -- its next refusal names that board; a fallback's: with the requested hardware of the failing
            pair kept), else the components the refusal (or the lost hardware) names, or None."""

            attempts = [moved_request]
            if beyond_limit and moved_request.max_board_size_mm is not None:
                attempts.append(replace(moved_request, max_board_size_mm=None))
            outcome: Any = None
            for k, current in enumerate(attempts):
                room = min(allowance.max_geometry_checks - allowance.geometry_checks, total - spent)
                if most is not None:
                    room = min(room, most)
                if pool is not None:
                    room = min(room, pool.max_diagnosis_checks - pool.diagnosis_checks)
                if room < 1:
                    raise _BudgetExhausted()
                nested = ResolutionBudget(policy.max_chain_placements, max_geometry_checks=room, max_diagnosis_checks=0,
                                          expired=pool.expired if pool is not None else None)
                try:
                    realization = _resolve(current, catalog, nested, diagnose=False, first_layout=pool is None)
                    lost = [(d.instance_id, c) for d in realization.dofs if d.not_delivered_reason == "HARDWARE_DID_NOT_FIT"
                            for chain in realization.chains if chain.instance_id == d.instance_id for c in chain.fallback_causes[:1]]
                    if pool is None or not lost:
                        result: Any = True
                    else:
                        result = frozenset({lost[0][0], lost[0][1].get("with")} - {None})
                except AssemblyResolutionRefusal as refused:
                    result = frozenset({refused.details.get("instance_id"), refused.details.get("with")} - {None}) or None
                charge(max(1, nested.geometry_checks))
                if nested.exhausted_by == "DEADLINE":
                    raise _BudgetExhausted()
                if result is True:
                    return True
                if k == 0:
                    outcome = result
            return outcome

        def grade_of(moved_request: AssemblyResolutionRequestV2, most: int | None = None) -> tuple[int, tuple, tuple[float, float]] | None:
            """What the resolution makes of ``moved_request``, by the kernel's own order: the requested DOFs the layout loses,
            the rank of its board, and that board's size (any board, for positions the next refusal takes to a board the
            customer's limit excludes), within what is left of the allowance (at most ``most`` checks); None when it is
            not laid out."""

            room = min(allowance.max_geometry_checks - allowance.geometry_checks, total - spent)
            if most is not None:
                room = min(room, most)
            if pool is not None:
                room = min(room, pool.max_diagnosis_checks - pool.diagnosis_checks)
            if room < 1:
                raise _BudgetExhausted()
            nested = ResolutionBudget(policy.max_chain_placements, max_geometry_checks=room, max_diagnosis_checks=0,
                                      expired=pool.expired if pool is not None else None)
            try:
                realization = _resolve(replace(moved_request, max_board_size_mm=None), catalog, nested, diagnose=False)
            except AssemblyResolutionRefusal:
                return None
            finally:
                charge(max(1, nested.geometry_checks))
            board_found = realization.board.board
            return (sum(1 for d in realization.dofs if d.not_delivered_reason in _LAYOUT_LOSSES), _board_rank(board_found),
                    (board_found.size_x_mm, board_found.size_y_mm))

        def resolves(moved_request: AssemblyResolutionRequestV2, *, beyond_limit: bool = False, most: int | None = None) -> bool:
            return attempt(moved_request, beyond_limit=beyond_limit, most=most) is True

        # a single move, nearest first: a transmissive optic alone, with the plain optics after it, then with everything
        # after it; a mirror or splitter with everything after it -- each only if it splits no linked group; else the
        # linked group from its first powered optic. When the other element of the conflict is after this one on its beam, its
        # own moves are tried too
        starts = [instance] + ([other] if other is not None and other != instance and other in _downstream(instance, search.paths) else [])
        candidates = [move for start in starts for move in _moves_of(start, search)]
        if sum(1 for key, value in least.items() if value > next(p.spacing for p in pairs if (p.later, p.earlier) == key) + _EPS) >= 2:
            # two gaps certainly too narrow (below what their supports alone need): a single move widens one gap at
            # most, so none works -- straight to new positions
            candidates = []
        step = board.pitch_mm / _PITCH_FRACTIONS
        found = None
        least_lost: tuple[tuple, tuple] | None = None  # a refusal's best move so far that loses requested DOFs
        tried: set[tuple[str, float]] = set()
        moves_left = stage_checks  # of the stage, for every move
        for (head, moved) in candidates:
            way = direction if head == instance else [_round(v, 6) for v in (_incoming(head, search.paths) or direction)]
            # first near the component the way the search lays the design out (other grid phases, mount fallbacks:
            # the nearest position), on a share of the stage; then the whole reach on the failure's own grid phase
            passes = ([(True, NEAR_STEPS, stage_checks // 3)] if confirming else []) + [(False, DIAGNOSIS_POSITION_STEPS, stage_checks)]
            for thorough, reach, limit in passes:
                if moves_left <= 0:
                    break
                renew(min(limit, moves_left))
                checked = 0
                try:
                    for k in range(1, reach + 1):
                        for s in (k * step, -k * step):
                            shift = tuple(v * s for v in way)
                            if (head, s) in tried or (not thorough and _below_floor(pairs, least, moved, shift)):  # type: ignore[arg-type]
                                continue  # (the near pass may also change mounts, which the floor does not know)
                            laid_out = _layout_after_move(search, failure, catalog, moved, shift, charge, thorough=thorough)  # type: ignore[arg-type]
                            if laid_out is None or not laid_out[0]:
                                continue
                            tried.add((head, s))
                            checked += 1
                            # confirmed by resolving the moved design (a plain layout fallback's rests on the layout
                            # alone, to keep resolutions fast). A refusal's takes the nearest that loses no requested DOF;
                            # the nearest may stand only on a larger board with less delivered: kept in case none does better
                            moved_design = _moved_request(request, moved, shift)  # type: ignore[arg-type]
                            if not confirming or (pool is not None and resolves(moved_design)):
                                found = (s, head, moved, way)
                                break
                            # (the layout test says which are worth a search: one that loses DOFs only as the fallback)
                            if pool is None and (least_lost is None or _fit_board(search, failure, catalog, moved_design, charge, _board_order(catalog.boards)) is not None):
                                graded = grade_of(moved_design, most=stage_checks // PAIR_ROUND_SHARE)
                                if graded is not None and graded[0] == 0:
                                    found = (s, head, moved, way)
                                    break
                                if graded is not None and (least_lost is None or graded[:2] < least_lost[0]):
                                    least_lost = (graded[:2], (s, head, moved, way))
                            if checked >= DIAGNOSIS_VERIFICATIONS:
                                raise _BudgetExhausted()
                        if found is not None:
                            break
                except _BudgetExhausted:
                    pass  # this pass's allowance ran out: no position from it
                moves_left -= allowance.geometry_checks
                if found is not None:
                    break
            if found is not None:
                break
        if found is None and least_lost is not None:
            found = least_lost[1]
        positions = None
        if found is not None:
            s, head, moved, way = found
            out["nearest_fixable_offset_mm"] = _round(s, 1)
            position = next(o.position_design for o in request.optics if o.instance_id == head)
            out["instance_id"] = head
            out["beam_direction_design"] = list(way)
            out["suggested_position_design_mm"] = [_round(position[i] + way[i] * s, 3) for i in range(3)]
            if len(moved) > 1:
                out["moved_with"] = sorted(moved - {head})
        elif confirming:
            # new positions the resolution lays out (on a larger board than the customer's limit allows, if need be:
            # the next refusal then names that board): every gap that may change opened, else the spread's
            renew()
            try:
                positions = _open_up(search, failure, catalog, charge, _with_every_gap(pairs, least),
                                     lambda moved_positions: resolves(_placed(request, moved_positions), beyond_limit=pool is None))
            except _BudgetExhausted:
                positions = None
            left = allowance.max_geometry_checks - allowance.geometry_checks  # of the stage, for the spread
            if not positions and left > 0:
                renew(left)
                try:
                    start = _relayout(search, failure, catalog, request, charge)
                    positions = _spread(search, failure, catalog, charge, start) if start is not None else None
                    if positions and not resolves(_placed(request, positions), beyond_limit=pool is None):
                        positions = None
                except _BudgetExhausted:
                    positions = None
            if positions:
                # as tight as the rest of the allowance gets them (the stage the confirmation of the pairs would have used),
                # on no larger a board than the positions found take: a shorter design must not need a larger board
                renew(stage_checks * 3 // 4)  # the last quarter: to check what the compaction ends with
                tightness = POSITIONS_NOT_CONFIRMED
                found_positions, taken = positions, None
                try:
                    taken = grade_of(_placed(request, positions))
                    if taken is not None:
                        tightness = POSITIONS_NOT_COMPACTED
                        positions, tightness = _compact(search, failure, catalog, charge, positions, pairs, least,
                                                        lambda moved_positions: resolves(replace(_placed(request, moved_positions), max_board_size_mm=taken[2]),
                                                                                         most=stage_checks // COMPACT_TRIAL_SHARE))
                except _BudgetExhausted:
                    pass
                if taken is not None and positions != found_positions and pool is None:
                    renew(stage_checks // 4)
                    try:
                        compacted = grade_of(_placed(request, positions))
                    except _BudgetExhausted:
                        compacted = None
                    if compacted is None or compacted[:2] > taken[:2]:  # shorter, but with less delivered or on a larger board
                        positions, tightness = found_positions, POSITIONS_NOT_COMPACTED
                out["suggested_positions_design_mm"] = positions
                out["suggested_positions_tightness"] = tightness
            else:
                # no positions: the pairs' spacings, raised until the design resolves with them. Never left as a lower
                # bound that could bring the pair back: the ones not confirmed go to where their supports
                # cannot meet any more (then ``pairs_confirmed`` stays false unless the design resolves there too)
                spacing = {(p.later, p.earlier): p.spacing for p in pairs}
                certain = dict(targets)
                for key in _gaps_of(frozenset({instance, other} - {None}), pairs, search):
                    targets.setdefault(key, _round(spacing[key] + step, 1))
                if targets:
                    def widened_outcome(moved_positions: dict[str, list[float]], share: int = PAIR_ROUND_SHARE, thorough: bool = False) -> Any:
                        """True when the design with the pairs widened resolves (the layout first, the cheap test, which
                        also names what is still in the way), else the components the failure names."""

                        moved_request = _placed(request, moved_positions)
                        laid_out = _relayout(search, failure, catalog, moved_request, charge, phases=thorough)
                        if laid_out is None:
                            return None
                        if laid_out[0]:
                            return attempt(moved_request, beyond_limit=pool is None, most=stage_checks // share)
                        return _named_by(laid_out[1], plans)

                    renew(stage_checks * 3 // 8)
                    confirmed = _confirm_pairs(request, pairs, targets, step, widened_outcome, search)
                    stage_spent = allowance.geometry_checks
                    if not confirmed and clearing is not None:
                        # the conflict has a move of its own (a clearance, a spacing): only the gaps that certainly need
                        # more room stay, not the ones the rounds tried for it
                        targets = certain
                    if not confirmed and targets:
                        # the gaps the floor did not reach go with them: they are about to be the next refusal
                        for key, value in _with_every_gap(pairs, least).items():
                            if key not in least and key not in targets and value > spacing[key] + _EPS:
                                targets[key] = value
                        renew(stage_checks // 4)
                        far = _beyond_reach(search, catalog, pairs, targets, plans, least)
                        # part of the way first: the supports need not be out of each other's reach for the design to stand
                        try:
                            for way in BEYOND_REACH_SHARES:
                                trial = {key: _round_up(targets[key] + way * (far[key] - targets[key])) for key in targets}
                                # (asked of the resolution directly: the design may stand here only on simpler mounts)
                                if resolves(_placed(request, _widened(request, pairs, trial)), beyond_limit=pool is None, most=stage_checks // (2 * PAIR_ROUND_SHARE)):
                                    far, confirmed = trial, True
                                    break
                        except _BudgetExhausted:
                            confirmed = False
                        targets = far
                        stage_spent += allowance.geometry_checks
                    if confirmed:
                        confirmed_targets = dict(targets)
                        renew(stage_checks * 3 // 4 - stage_spent)
                        settled = None
                        try:
                            settled = _settle_pairs(search, failure, catalog, charge, pairs, targets, least, step)
                            if settled is None:  # the first mounts stand on no board: back as far as the resolution still lays it out
                                _tighten_pairs(pairs, targets, least, lambda moved_positions: widened_outcome(moved_positions, 2 * PAIR_ROUND_SHARE, True) is True, request)
                        except _BudgetExhausted:
                            pass
                        # the resolution itself has the last word, smallest board first, as it will search them
                        renew(stage_checks // 4)
                        try:
                            moved_request = _placed(request, _widened(request, pairs, targets))
                            holds = resolves(moved_request, beyond_limit=True, most=stage_checks // PAIR_ROUND_SHARE) if pool is not None else grade_of(moved_request) is not None
                        except _BudgetExhausted:
                            holds = False
                        if not holds:
                            targets, confirmed = confirmed_targets, False
                    if targets:
                        out["pairs_confirmed"] = confirmed
        entries = _pair_entries(search, pairs, targets)
        if entries:
            out["conflicting_pairs"] = entries
            out.setdefault("pairs_confirmed", False)
            named = next((e for e in entries if {e["instance_id"], e["with"]} == {instance, other}), None)
            if named is not None:
                # the spacing the two named supports need, for the design to make room for
                out["min_support_pitch_mm"] = named["min_support_pitch_mm"]
                if "min_extra_spacing_mm" in out and named["instance_id"] == instance:
                    # clearing the bodies is not enough when the supports then find no holes: the extra spacing is at
                    # least what the pair's spacing asks for
                    out["min_extra_spacing_mm"] = max(out["min_extra_spacing_mm"], _round_up(named["min_support_pitch_mm"] - named["spacing_mm"]))
        # the move a spacing or clearance of this component applies to: a free move across its own beam
        # takes it off the beam, a mirror moved alone sends the beam past what follows it, a powered optic moved alone leaves its
        # linked group, a move past the next element reorders the beam -- such numbers are not given
        own = None
        if clearing is not None and ("min_extra_spacing_mm" in out or "min_lateral_clearance_mm" in out):
            amount = out.get("min_extra_spacing_mm", out.get("min_lateral_clearance_mm", 0.0))
            way = _design_dir(yaw, clearing)
            if not on_beam or abs(sum(a * b for a, b in zip(way, direction))) >= 1.0 - _MICRO:
                renew(DIAGNOSIS_OWN_MOVE_CHECKS)
                try:
                    own = _move_set(instance, search, reflective, tuple(v * amount for v in way), charge)
                except _BudgetExhausted:
                    own = None
        if own is None or (found is not None and (found[1] != instance or found[2] != own)):
            # no move of this component applies them, or they belong to another move than the one suggested
            for key in ("min_extra_spacing_mm", "min_lateral_clearance_mm", "free_direction_design"):
                out.pop(key, None)
        elif found is None and len(own) > 1:
            out["moved_with"] = sorted(own - {instance})
    # only a board on which a layout was actually found: the search tries every board the customer allows
    out["larger_breadboard_may_help"] = search.fitting_beyond_limit is not None
    if search.fitting_beyond_limit is not None:
        out["smallest_fitting_board_mm"] = [search.fitting_beyond_limit.size_x_mm, search.fitting_beyond_limit.size_y_mm]
    return out


def _suggestion(reason: str, numbers: dict[str, Any]) -> dict[str, Any]:
    action = {
        "INTER_CHAIN_COLLISION": ADJUST_COMPONENT_SPACING,
        "NO_FREE_FIXATION_HOLE": ADJUST_COMPONENT_SPACING,
        "BEAM_OBSTRUCTED": MOVE_COMPONENT_OFF_BEAM,
        "OUTSIDE_BOARD": ALLOW_LARGER_BREADBOARD,
        "INTRA_CHAIN_COLLISION": CHANGE_COMPONENT,
        "NO_STACK_FOR_MOUNT": CHANGE_COMPONENT,
        "NO_COMMON_BEAM_HEIGHT": ADJUST_COMPONENT_HEIGHT,
        "NO_STACK_AT_BEAM_HEIGHT": ADJUST_COMPONENT_HEIGHT,
        "REQUESTED_BEAM_HEIGHT_UNREACHABLE": ADJUST_COMPONENT_HEIGHT,
        "BREADBOARD_CONSTRAINT_UNSATISFIABLE": ALLOW_LARGER_BREADBOARD,
        "BREADBOARD_NO_STANDARD_FIT": ADJUST_COMPONENT_SPACING,
    }.get(reason, SIMPLIFY_DESIGN)
    return {"action": action, **numbers}


def _search_bound(search: _Search, attempts: int | None) -> dict[str, Any] | None:
    """Which bound stopped the search (customer-safe): the deadline, a work counter with its limit and what was spent,
    or the mount-attempt cap (``attempts``: its value, when the attempts ran out)."""

    budget = search.budget
    if budget.exhausted_by == "DEADLINE":
        return {"kind": "DEADLINE"}
    if budget.exhausted_counter == "CHAIN_PLACEMENTS":
        return {"kind": "CHAIN_PLACEMENTS", "limit": budget.max_chain_placements, "spent": budget.chain_placements}
    if budget.exhausted_counter == "GEOMETRY_CHECKS":
        return {"kind": "GEOMETRY_CHECKS", "limit": budget.max_geometry_checks, "spent": budget.geometry_checks}
    if attempts is not None:
        return {"kind": "MOUNT_ATTEMPTS", "limit": attempts, "spent": attempts}
    return None


def _refusal(search: _Search, failure: _ChainFailure, optic: _Optic, optics: Sequence[_Optic], failures: list[dict[str, Any]], *,
             exhausted: bool = False, attempts: int | None = None) -> AssemblyResolutionRefusal:
    public_detail = {k: v for k, v in failure.detail.items() if k in ("footprint_mm", "max_board_size_mm", "smallest_fitting_board_mm",
                                                                       "standard_board_sizes_mm", "largest_standard_board_mm")}
    internal = {"failures": failures, **search.budget.summary(), "layout_cache_hits": search.cache_hits}
    if exhausted:
        internal["budget_exhausted_by"] = search.budget.exhausted_by
    if failure.reason == "BREADBOARD_CONSTRAINT_UNSATISFIABLE":
        smallest = public_detail.get("smallest_fitting_board_mm")
        if smallest:
            board_numbers = {"smallest_fitting_board_mm": smallest}
        else:
            # no layout was found on a larger board while searching: resolve the design again without the limit (no
            # diagnosis, a diagnosis' allowance) for the board it is laid out on; else the smallest standard board the
            # design's footprint fits on, with the edge margin, is the least the limit has to allow
            laid_out = _board_without_limit(search) if search.diagnose and not search.budget.refused else None
            if laid_out is not None:
                board_numbers = {"smallest_fitting_board_mm": laid_out}
                public_detail["smallest_fitting_board_mm"] = laid_out
            else:
                # the footprint whichever compatible mounts the search ends with (each chain's widest), so that allowing
                # it never brings the limit back for another mount choice
                widest = _widest_footprint(search) or public_detail.get("footprint_mm") or []
                taking = _smallest_board_taking(search.catalog, widest)
                board_numbers = {"smallest_board_for_footprint_mm": taking} if taking else {}
        details = {**public_detail, "reason": "BREADBOARD_LIMIT_TOO_SMALL", "suggestion": _suggestion(failure.reason, board_numbers)}
        return AssemblyResolutionRefusal(failure.reason, "no standard breadboard that fits the design is within the requested size limit",
                                         details=details, internal=internal)
    if failure.reason == "BREADBOARD_NO_STANDARD_FIT":
        footprint = public_detail.get("footprint_mm") or []
        largest = sorted(public_detail.get("largest_standard_board_mm") or [], reverse=True)
        numbers: dict[str, Any] = {}
        if len(footprint) == 2 and len(largest) == 2:
            usable = [v - 2.0 * search.catalog.policy.board_edge_margin_mm for v in largest]
            fp = sorted(footprint, reverse=True)
            numbers["reduce_extent_mm"] = [_round(max(0.0, fp[0] - usable[0]), 1), _round(max(0.0, fp[1] - usable[1]), 1)]
        details = {**public_detail, "reason": "LAYOUT_LARGER_THAN_STANDARD_BREADBOARDS", "suggestion": _suggestion(failure.reason, numbers)}
        return AssemblyResolutionRefusal(failure.reason, "the layout does not fit on any standard OpenSci breadboard", details=details,
                                         internal=internal)
    details: dict[str, Any] = {"instance_id": optic.request.instance_id, "reason": _PUBLIC_REASONS.get(failure.reason, "NO_SUPPORTED_MOUNTING")}
    other = _instance_of(failure.detail.get("with"), optics, search.externals)
    if other is not None:
        details["with"] = other
    numbers = {k: v for k, v in failure.detail.items() if k in ("reachable_design_z_mm", "current_design_z_mm", "reachable_axis_height_mm")}
    if "_ctx" in failure.detail and (search.backtracking or exhausted) and search.diagnose and not search.budget.refused:  # a first-fit layout refusal is superseded
        diagnosed = _diagnose(search, failure.detail, search.catalog, optics)
        if "with" in diagnosed and "with" not in details:
            details["with"] = diagnosed.pop("with")
        diagnosed.pop("with", None)
        numbers.update(diagnosed)
    chain_index = next(i for i, o in enumerate(optics) if o.chain_key == optic.chain_key)
    numbers["relaxing_retained_dofs_may_help"] = bool(optic.request.retained_dofs) and _untried_simpler_mount(search, chain_index)
    details["suggestion"] = _suggestion(failure.reason, numbers)
    if exhausted:
        details["search_budget_exhausted"] = True
        bound = _search_bound(search, attempts)
        if bound is not None:
            details["search_bound"] = bound
    if failure.reason == "REQUESTED_BEAM_HEIGHT_UNREACHABLE":
        return AssemblyResolutionRefusal(failure.reason, "the requested beam height cannot be reached by the available supports",
                                         details=details, internal=internal)
    return AssemblyResolutionRefusal(
        "MECHANICAL_LAYOUT_CONFLICT" if failure.reason in _LAYOUT_CODE_REASONS else "NO_SUPPORTED_MECHANICAL_REALIZATION",
        _MESSAGES.get(failure.reason, "no supported mounting configuration was found for this design"),
        details=details, internal=internal,
    )


def _widest_footprint(search: _Search) -> list[float] | None:
    """The plan extent of the design (at its yaw, before any turn) with every chain's bodies on each compatible mount it
    ranks, at the lift of the first-ranked ones: at least the footprint of any mount choice. None when not computable."""

    if search.request is None or "yaw" not in search.prepared or len(search.rankings) != len(search.optics):
        return None
    yaw = search.prepared["yaw"]
    chosen = [(optic, search.rankings[i][0][0][1]) for i, optic in enumerate(search.optics)]
    try:
        lift = _choose_lift(chosen, search.catalog, search.request.beam_height_mm)
    except _ChainFailure:
        return None
    boxes = []
    for i, optic in enumerate(search.optics):
        for _, mount, _ in search.rankings[i][0][:search.catalog.policy.max_mount_attempts]:
            try:
                plan = _plan_chain(_optic_with_yaw(optic.request, optic.chain_key, yaw), mount, lift, search.catalog)
            except _ChainFailure:
                continue  # a mount not held at this height: not one the search ends with at this lift
            boxes += [b for bs in _chain_bodies(plan, (0.0, 0.0), None).values() for b in bs]
    if not boxes:
        return None
    boxes += [b for bs in _reserved_bodies(search.request.externals, yaw, lift).values() for b in bs]
    (x0, y0), (x1, y1) = plan_bounds(boxes)
    return [round(x1 - x0, 3), round(y1 - y0, 3)]


def _board_without_limit(search: _Search) -> list[float] | None:
    """The board the design is laid out on without the customer's size limit, resolved again without diagnosis within
    a diagnosis' allowance; None when it is not laid out there either (or the allowance runs out)."""

    if search.request is None or search.request.max_board_size_mm is None:
        return None
    nested = ResolutionBudget(search.catalog.policy.max_chain_placements, max_geometry_checks=search.catalog.policy.diagnosis_max_geometry_checks,
                              max_diagnosis_checks=0)
    try:
        realization = _resolve(replace(search.request, max_board_size_mm=None), search.catalog, nested, diagnose=False, seed=search)
    except AssemblyResolutionRefusal:
        return None
    return [realization.board.board.size_x_mm, realization.board.board.size_y_mm]


def _smallest_board_taking(catalog: KernelCatalog, footprint: Sequence[float]) -> list[float] | None:
    """The smallest standard board (``_board_order``) whose area within the edge margin takes a plan ``footprint``, in
    either orientation (the layout may turn the design a quarter turn on a rectangular board)."""

    if len(footprint) != 2:
        return None
    margin = 2.0 * catalog.policy.board_edge_margin_mm
    fx, fy = footprint[0] + margin, footprint[1] + margin
    for board in _board_order(catalog.boards):
        x, y = board.size_x_mm + _EPS, board.size_y_mm + _EPS
        if (fx <= x and fy <= y) or (fy <= x and fx <= y):
            return [board.size_x_mm, board.size_y_mm]
    return None


def _untried_simpler_mount(search: _Search, chain_index: int) -> bool:
    """Whether a compatible mount delivering fewer requested DOFs was left untried for this chain."""

    ranked = search.rankings[chain_index][0]
    pointer = search.pointers[chain_index]
    current = len(ranked[pointer][2])
    return any(len(delivered) < current for _, _, delivered in ranked[pointer + 1:])


def _budget_refusal(search: _Search, optics: Sequence[_Optic], failures: list[dict[str, Any]], *,
                    attempts: int | None = None) -> AssemblyResolutionRefusal:
    """The bounded search stopped (work budget, deadline or attempt cap: ``attempts`` is the cap when the mount
    attempts ran out): refuse with the most informative failure seen, marked ``search_budget_exhausted`` and naming the
    bound (``search_bound``); with none, a plain bound refusal."""

    best = search.best
    if best is not None:
        optic = next(o for o in optics if o.chain_key == best["chain"])
        failure = _ChainFailure(best["chain"], best["reason"], {k: v for k, v in best.items() if k not in ("chain", "reason")})
        return _refusal(search, failure, optic, optics, failures, exhausted=True, attempts=attempts)
    internal = {"failures": failures, **search.budget.summary(), "layout_cache_hits": search.cache_hits,
                "budget_exhausted_by": search.budget.exhausted_by or ("ATTEMPTS" if attempts is not None else None)}
    details: dict[str, Any] = {"reason": "SEARCH_BOUND_REACHED", "search_budget_exhausted": True,
                               "suggestion": {"action": SIMPLIFY_DESIGN}}
    bound = _search_bound(search, attempts)
    if bound is not None:
        details["search_bound"] = bound
    return AssemblyResolutionRefusal("MECHANICAL_RESOLUTION_BOUND_EXCEEDED", "no supported mounting configuration was found for this design "
                                     "within the resolution's search bound",
                                     details=details, internal=internal)


def _no_mount_refusal(optic: _Optic, rejected: list[dict[str, Any]], incidence: Incidence | None) -> AssemblyResolutionRefusal:
    """No mount holds the optic. When some would, but clip the beam at its incidence, say so with the largest incidence
    they pass (reduce the tilt) and the basis of that judgment (``clearance_basis``: ``RECORDED`` when every clipping
    mount was judged on recorded bore data, ``ASSUMED`` when at least one was judged on stand-ins); otherwise the optic
    needs another product."""

    details: dict[str, Any] = {"instance_id": optic.request.instance_id, "optical_sku": optic.request.optical_sku}
    clipped = [r for r in rejected if r["reason"] == "BEAM_CLIPPED_AT_INCIDENCE"]
    if clipped and incidence is not None:
        details["reason"] = "MOUNT_CLIPS_OBLIQUE_BEAM"
        details["clearance_basis"] = "ASSUMED" if any(r.get("assumed_mount_facts") for r in clipped) else "RECORDED"
        details["suggestion"] = {"action": REDUCE_TILT, "incidence_deg": _round(incidence.degrees, 1),
                                 "max_incidence_deg": max(r["max_incidence_deg"] for r in clipped)}
        message = ("every mount that holds this optic would clip the beam at its angle of incidence"
                   if details["clearance_basis"] == "RECORDED" else
                   "no mount that holds this optic is established to pass the beam at its angle of incidence from the "
                   "mount data on record")
        return AssemblyResolutionRefusal("NO_COMPATIBLE_MOUNT", message, details=details, internal={"rejected": rejected})
    kinds = sorted({r["reason"] for r in rejected})
    details["reason"] = "OPTIC_NOT_HELD"
    details["suggestion"] = {"action": CHANGE_COMPONENT,
                             "near_misses": [k for k in kinds if k in ("OPTIC_TOO_THIN", "OPTIC_TOO_THICK", "NO_CLEAR_APERTURE")]}
    return AssemblyResolutionRefusal("NO_COMPATIBLE_MOUNT", "no available mount holds this optical product",
                                     details=details, internal={"rejected": rejected})


#: A beam crossing a transmissive optic within this angle of its axis (30 deg) is meant to pass through it.
_TILT_REPORT_COSINE = 0.8660254037844387


def _check_beam_tilts(request: AssemblyResolutionRequestV2, paths: Sequence[BeamPathV2], policy: ResolverPolicyV1,
                      charge: Callable[[int], None] = _no_charge) -> None:
    """A transmissive optic the nominal beam crosses within its radius but too far off its axis to be on the beam
    (lenses: within ``policy.max_lens_tilt_deg``; flat plates: within ``policy.max_plate_tilt_deg``) and within 30
    degrees of it is refused as an orientation it cannot be realized in, instead of being reported as hardware in the
    beam. With both bounds at 30 degrees (the defaults) nothing is refused here: every such optic
    is on the path, and its mount is judged at that incidence by its bore."""

    plate_cos = _plate_tilt_cosine(policy)
    lens_cos = min(AXIAL_COSINE, _lens_tilt_cosine(policy))
    segments = sum(max(0, len(path.points_design) - 1) for path in paths)
    for optic in request.optics:
        charge(1 + len(paths) + segments)
        if optic.mechanics.category in ("MIRROR", "BEAMSPLITTER_PLATE", "BEAMSPLITTER_CUBE"):
            continue
        if any(optic.instance_id in path.through for path in paths):
            continue
        normal = rotate(optic.rotation_design, (0.0, 0.0, 1.0))
        radius = optic.mechanics.size_mm / 2.0
        for path in paths:
            for k in range(len(path.points_design) - 1):
                p0, p1 = path.points_design[k], path.points_design[k + 1]
                d = sub(p1, p0)
                length = norm(d)
                if length <= _EPS:
                    continue
                u = scale(d, 1.0 / length)
                s = dot(sub(optic.position_design, p0), u)
                if s <= 0.0 or s >= length:
                    continue
                off = sub(sub(optic.position_design, p0), scale(u, s))
                if dot(off, off) > radius * radius:
                    continue
                cos_theta = abs(dot(u, normal))
                if cos_theta < _TILT_REPORT_COSINE:
                    continue  # crossed broadside: hardware in the beam, reported by the layout, not a tilt
                limit = plate_cos if optic.mechanics.category in PLATE_CATEGORIES else lens_cos
                incidence = math.degrees(math.acos(max(-1.0, min(1.0, cos_theta))))
                raise AssemblyResolutionRefusal(
                    "UNSUPPORTED_OPTIC_ORIENTATION",
                    "the beam crosses this optic too far off its axis for it to be assembled on the beam",
                    details={"instance_id": optic.instance_id, "reason": "OPTIC_TILT_NOT_SUPPORTED", "with": path.through[k],
                             "suggestion": {"action": REDUCE_TILT, "incidence_deg": _round(incidence, 1),
                                            "max_incidence_deg": _round(math.degrees(math.acos(limit)), 1)}},
                )


QUARTER_TURN: Quat = (0.7071067811865476, 0.0, 0.0, 0.7071067811865476)  # +90 degrees about +Z


def _optic_with_yaw(request: OpticRequest, chain_key: str, yaw: Quat) -> _Optic:
    rotation_lab = q_normalize(q_mul(yaw, dof_frame(request.rotation_design)))
    axis = rotate(rotation_lab, (0.0, 0.0, 1.0))
    return _Optic(request, chain_key, rotation_lab, snap_vec(unit((axis[0], axis[1], 0.0))), rotate(yaw, request.position_design))


def _realize(
    search: _Search,
    request: AssemblyResolutionRequestV2,
    catalog: KernelCatalog,
    yaw: Quat,
    chosen: list[tuple[_Optic, KMount]],
    rankings: list,
    pointers: list[int],
    paths: tuple[BeamPathV2, ...],
    fallbacks: dict[str, list[dict[str, Any]]] | None = None,
) -> AssemblyRealizationV2:
    """Boards smallest first; a rectangular board is tried with the design at ``yaw`` and turned a further 90 degrees
    (the grid is square, so both keep every grid alignment). The mount choice does not depend on the yaw: a turn about
    +Z turns every DOF direction and every mechanism axis together.

    A fitting layout is accepted at once when it delivers every requested DOF the chosen mounts, posts and slots can
    provide (``rank_mounts``' "delivers"); otherwise the search goes on to the other orientation and larger boards within
    the limit and keeps the smallest layout with the most delivered DOFs."""

    policy = catalog.policy
    lift_key = (tuple(_mount_geometry_sig(m) for _, m in chosen), request.beam_height_mm)
    if lift_key not in search.lifts:
        try:
            search.lifts[lift_key] = _choose_lift(chosen, catalog, request.beam_height_mm, search.charge)
        except _ChainFailure as failure:
            search.lifts[lift_key] = failure
    lift = search.lifts[lift_key]
    if isinstance(lift, _ChainFailure):
        raise _ChainFailure(lift.chain_key, lift.reason, dict(lift.detail))

    def variant(orientation: Quat) -> tuple[list[_ChainPlan], list[tuple[Vec3, Vec3, frozenset[str]]], dict[str, list[YawBox]]]:
        plans = []
        for optic, mount in chosen:
            memo = (optic.chain_key, mount.identity, lift, orientation)
            if memo not in search.plans:
                search.charge(1)
                plan = _plan_chain(_optic_with_yaw(optic.request, optic.chain_key, orientation), mount, lift, catalog)
                search.plans[memo] = plan
                search.conflicts[memo] = _intra_chain_conflicts(_chain_bodies(plan, (0.0, 0.0), None))
            if search.conflicts[memo]:
                raise _ChainFailure(optic.chain_key, "INTRA_CHAIN_COLLISION", {"pairs": search.conflicts[memo]})
            plans.append(search.plans[memo])
        segments: list[tuple[Vec3, Vec3, frozenset[str]]] = []
        for path in paths:
            pts = [rotate(orientation, p) for p in path.points_design]
            pts = [(p[0], p[1], p[2] + lift) for p in pts]
            for k in range(len(pts) - 1):
                segments.append((pts[k], pts[k + 1], frozenset({path.through[k], path.through[k + 1]})))
        return plans, segments, _reserved_bodies(request.externals, orientation, lift)

    turned = snap_quat(q_mul(QUARTER_TURN, yaw))
    deliverable = sum(len(rankings[i][0][pointers[i]][2]) for i in range(len(chosen)))
    best: tuple[int, AssemblyRealizationV2] | None = None
    considered: list[dict[str, Any]] = []
    fitting_ignoring_limit = None
    last_failure: dict[str, Any] = {}  # the last conflict found on a board the customer allows
    footprint_fits_within = False  # the design's footprint fits a board the customer allows
    variants: dict[Quat, tuple[list[_ChainPlan], list[tuple[Vec3, Vec3, frozenset[str]]], dict[str, list[YawBox]]]] = {}
    boards = _board_order(catalog.boards)
    if search.first_layout:  # whether the design is laid out at all: the board most likely to take it first
        boards = [board for board in reversed(boards) if _within_limit(board, request.max_board_size_mm)]
    try:
        for board in boards:
            if search.smaller_than is not None and _board_rank(board) >= _board_rank(search.smaller_than):
                break
            within = _within_limit(board, request.max_board_size_mm)
            orientations = [yaw] if board.size_x_mm == board.size_y_mm else [yaw, turned]
            for index, orientation in enumerate(orientations):
                if orientation not in variants:
                    variants[orientation] = variant(orientation)
                plans, segments, reserved = variants[orientation]
                candidate, reason, conflict = _try_layout(search, plans, board, orientation, lift, segments, policy, reserved)
                considered.append({"board": board.identity, "size_mm": [board.size_x_mm, board.size_y_mm], "within_limit": within,
                                   "design_turned_90": index == 1, "fits": candidate is not None,
                                   **({"reason": reason.get("reason")} if reason else {})})
                if candidate is None:
                    if within and reason.get("reason") != "FOOTPRINT_EXCEEDS_BOARD":
                        footprint_fits_within = True
                        if conflict is not None:
                            last_failure = conflict
                    continue
                if not within:
                    fitting_ignoring_limit = fitting_ignoring_limit or board
                    search.fitting_beyond_limit = search.fitting_beyond_limit or board
                    break
                realization = _assemble(request, catalog, orientation, lift, plans, candidate, rankings, pointers, paths, considered,
                                        fallbacks or {}, truncated=search.paths_truncated)
                delivered = sum(1 for d in realization.dofs if d.status != "NOT_DELIVERED")
                considered[-1]["requested_dofs_delivered"] = [delivered, deliverable]
                if best is None or delivered > best[0]:
                    best = (delivered, realization)
                if delivered >= deliverable or search.first_layout:
                    return realization
    except _BudgetExhausted:
        if best is None:
            raise
        # the search for more of the requested DOFs ran out: the best realization found stands
        return best[1]
    if best is not None:
        return best[1]
    plans, _, reserved = variants[yaw] if yaw in variants else variant(yaw)
    sizes = sorted({(b.size_x_mm, b.size_y_mm) for b in catalog.boards}, key=lambda s: (s[0] * s[1], s))
    footprint = _raw_footprint(plans, reserved)
    limit = {"footprint_mm": footprint, "max_board_size_mm": list(request.max_board_size_mm or ()),
             "standard_board_sizes_mm": [list(s) for s in sizes]}
    if fitting_ignoring_limit is not None:
        # a board the customer's limit excludes takes the layout: allowing it is what resolves the design
        raise _ChainFailure(plans[0].optic.chain_key, "BREADBOARD_CONSTRAINT_UNSATISFIABLE", {
            **limit, "smallest_fitting_board_mm": [fitting_ignoring_limit.size_x_mm, fitting_ignoring_limit.size_y_mm]})
    if last_failure.get("reason") in _CONFLICT_REASONS:
        raise _ChainFailure(last_failure["chain"], last_failure["reason"], {k: v for k, v in last_failure.items() if k not in ("chain", "reason")})
    if request.max_board_size_mm is not None and not footprint_fits_within:
        # no board within the limit even takes the footprint: the limit, not a conflict on a board it excludes
        raise _ChainFailure(plans[0].optic.chain_key, "BREADBOARD_CONSTRAINT_UNSATISFIABLE", limit)
    raise _ChainFailure(plans[0].optic.chain_key, "BREADBOARD_NO_STANDARD_FIT", {
        "footprint_mm": footprint, "largest_standard_board_mm": list(sizes[-1]) if sizes else [],
        "standard_board_sizes_mm": [list(s) for s in sizes]})


def _raw_footprint(plans: Sequence[_ChainPlan], reserved: dict[str, list[YawBox]] | None = None) -> list[float]:
    """The plan extent of the hardware bodies and the reserved spaces (the virtual observation planes occupy
    nothing)."""

    boxes = [b for plan in plans for bs in _chain_bodies(plan, (0.0, 0.0), None).values() for b in bs]
    boxes += [b for bs in (reserved or {}).values() for b in bs]
    (x0, y0), (x1, y1) = plan_bounds(boxes)
    return [round(x1 - x0, 3), round(y1 - y0, 3)]


# ------------------------------------------------------------------------------------------------ assembly


def _pose_in(parent: Pose, child_world: Pose) -> Pose:
    inv = q_conj(parent.rotation)
    return Pose(snap_vec(rotate(inv, sub(child_world.position, parent.position))), snap_quat(q_mul(inv, child_world.rotation)))


def _metric(thread: str) -> str:
    """ISO 261 coarse pitch for a bare metric designation (M4 -> M4x0.7); other designations unchanged."""

    coarse = {"M2": "0.4", "M2.5": "0.45", "M3": "0.5", "M4": "0.7", "M5": "0.8", "M6": "1.0", "M8": "1.25"}
    return f"{thread}x{coarse[thread]}" if thread in coarse else thread


def _assemble(
    request: AssemblyResolutionRequestV2,
    catalog: KernelCatalog,
    yaw: Quat,
    lift: float,
    plans: Sequence[_ChainPlan],
    layout: _Layout,
    rankings: list,
    pointers: list[int],
    paths: tuple[BeamPathV2, ...],
    considered: list[dict[str, Any]],
    fallbacks: dict[str, list[dict[str, Any]]] | None = None,
    *,
    truncated: Sequence[dict[str, Any]] = (),
) -> AssemblyRealizationV2:
    policy = catalog.policy
    board = layout.board
    t = layout.translation
    translation = (round(t[0], _SNAP_DIGITS) + 0.0, round(t[1], _SNAP_DIGITS) + 0.0, round(lift, _SNAP_DIGITS) + 0.0)
    design_to_lab = Pose(translation, yaw)
    board_pose = Pose((0.0, 0.0, -board.thickness_mm), IDENTITY)
    parts: list[RealizedPartV2] = [RealizedPartV2(
        slot_key=BOARD_KEY, role="BREADBOARD", chain_key=None, instance_id=None, member=board, optic=None, pose=board_pose,
        bodies=(), datums={"board_top": Pose((0.0, 0.0, board.thickness_mm), IDENTITY)}, support_parent=None, support_relation=None,
        assembly_order=1, visual=board.visual, color_rgb=board.color_rgb, approximate_envelope=not _is_fact(board, "envelope"),
    )]
    mates: list[RealizedMateV2] = []
    settings: list[RealizedSettingV2] = []
    fixations: list[RealizedFixationV2] = []
    dofs: list[DofResultV2] = []
    traces: list[ChainTraceV2] = []
    optic_poses: dict[str, Pose] = {}
    assumptions: set[str] = set()
    order = 2
    board_top_world = board_pose.compose(Pose((0.0, 0.0, board.thickness_mm)))
    for index, plan in enumerate(plans):
        key = plan.optic.chain_key
        req = plan.optic.request
        mount, stack, fixation = plan.mount, plan.stack, layout.fixations[key]
        axis_xy = (plan.post_axis_rel[0] + t[0], plan.post_axis_rel[1] + t[1])
        mount_pose = Pose(snap_vec(add(plan.mount_position_rel, (t[0], t[1], 0.0))), snap_quat(plan.mount_rotation))
        post_pose = Pose(snap_vec((axis_xy[0], axis_xy[1], mount_pose.position[2] - stack.post.length_mm)), mount_pose.rotation)
        holder_pose = Pose(snap_vec((axis_xy[0], axis_xy[1], 0.0)), snap_quat(fixation.yaw))
        optic_pose = design_to_lab.compose(Pose(req.position_design, req.rotation_design))
        optic_pose = Pose(snap_vec(optic_pose.position), snap_quat(optic_pose.rotation))
        optic_poses[req.instance_id] = optic_pose
        mech = req.mechanics
        seat_optic = Pose((0.0, 0.0, mech.axial_max_mm), IDENTITY)
        seat_world = optic_pose.compose(seat_optic)
        seat_mount = _pose_in(mount_pose, seat_world)
        holder_depth = stack.depth
        bodies = layout.bodies
        parts.append(RealizedPartV2(
            pk(key, "holder"), "POST_HOLDER", key, req.instance_id, stack.holder, None, holder_pose, tuple(bodies[pk(key, "holder")]),
            {"bottom": Pose((0.0, 0.0, 0.0)), "bore_top": Pose((0.0, 0.0, stack.holder.height_mm))}, BOARD_KEY, "STANDS_ON", order,
            stack.holder.visual, stack.holder.color_rgb, not _is_fact(stack.holder, "envelope")))
        parts.append(RealizedPartV2(
            pk(key, "post"), "POST", key, req.instance_id, stack.post, None, post_pose, tuple(bodies[pk(key, "post")]),
            {"bottom": Pose((0.0, 0.0, 0.0)), "top": Pose((0.0, 0.0, stack.post.length_mm))}, pk(key, "holder"), "INSERTED_INTO", order + 1,
            stack.post.visual, stack.post.color_rgb, False))
        parts.append(RealizedPartV2(
            pk(key, "mount"), "OPTIC_MOUNT", key, req.instance_id, mount, None, mount_pose, tuple(bodies[pk(key, "mount")]),
            {"support": Pose((0.0, 0.0, 0.0)), "seat": seat_mount}, pk(key, "post"), "MOUNTED_ON", order + 2,
            mount.visual, mount.color_rgb, not _is_fact(mount, "envelope", "centre_height_mm")))
        parts.append(RealizedPartV2(
            pk(key, "optic"), "OPTIC", key, req.instance_id, None, mech, optic_pose, (), {"seat": seat_optic}, pk(key, "mount"), "SEATED_IN",
            order + 3, mech.visual, mech.color_rgb, False))
        order += 4
        designation = f"OD{_fmt(mech.size_mm, 3)}-SEAT"
        mates.append(RealizedMateV2(pk(key, "optic"), "seat", pk(key, "mount"), "seat", Pose((0.0, 0.0, 0.0)), designation))
        mates.append(RealizedMateV2(pk(key, "mount"), "support", pk(key, "post"), "top", Pose((0.0, 0.0, 0.0)), _metric(mount.post_thread),
                                    (mount.post_fastening, _metric(mount.post_thread)) if mount.post_fastening else None))
        bore_top_world = holder_pose.compose(Pose((0.0, 0.0, stack.holder.height_mm)))
        mates.append(RealizedMateV2(pk(key, "post"), "bottom", pk(key, "holder"), "bore_top", _pose_in(post_pose, bore_top_world),
                                    f"OD{_fmt(stack.post.diameter_mm, 3)}-POST"))
        mates.append(RealizedMateV2(pk(key, "holder"), "bottom", BOARD_KEY, "board_top", _pose_in(holder_pose, board_top_world),
                                    f"{_metric(board.thread)}-GRID{_fmt(board.pitch_mm, 3)}"))
        engagement_assumed = not _is_fact(stack.holder, "insertion_min_mm", "insertion_max_mm")
        if engagement_assumed:
            assumptions.add(f"{key}.holder.insertion_interval")
        if not _is_fact(mount, "envelope", "centre_height_mm"):
            assumptions.add(f"{key}.mount.geometry")
        if any(r["mount"] == mount.identity and r["reason"] == OBLIQUE_CLEARANCE_UNVERIFIED for r in rankings[index][1]):
            assumptions.add(f"{key}.mount.oblique_beam_clearance")
        settings.append(RealizedSettingV2(pk(key, "mount"), "MOUNT_ORIENTATION_YAW", _fmt(_deg(yaw_direction(mount_pose.rotation)), 6), "deg", "RESOLVED"))
        settings.append(RealizedSettingV2(pk(key, "post"), "INSERTION_DEPTH", _fmt(holder_depth, 6), "mm",
                                          "PREVIEW_ASSUMPTION" if engagement_assumed else "RESOLVED"))
        settings.append(RealizedSettingV2(pk(key, "holder"), "BASE_YAW", _fmt(_deg(fixation.direction), 6), "deg", "RESOLVED"))
        settings.append(RealizedSettingV2(pk(key, "holder"), "FIXATION_THREAD", _metric(stack.holder.base.thread), "NONE", "RESOLVED"))
        fixations.append(RealizedFixationV2(pk(key, "holder"), "SLOT", fixation.hole, _metric(stack.holder.base.thread)))
        ranked, rejected = rankings[index]
        dofs.extend(_implement_dofs(plan, layout, policy, yaw, ranked))
        _, stack_trace = _choose_stack(mount, plan.optic_position_lab_rel[2], catalog, req.pins)
        traces.append(ChainTraceV2(
            chain_key=key, instance_id=req.instance_id, optical_sku=req.optical_sku, mount=mount.identity, post=stack.post.identity,
            holder=stack.holder.identity, beam_height_mm=round(plan.optic_position_lab_rel[2], _SNAP_DIGITS) + 0.0, insertion_depth_mm=holder_depth,
            hole=fixation.hole, fork_yaw_deg=_deg(fixation.direction),
            requested_dofs=tuple(f"{d.axis}:{d.control}" for d in req.retained_dofs),
            mount_ranking=tuple({"mount": m.identity, "delivers": delivered, "rank": [float(x) if isinstance(x, (int, float)) else x for x in k[:3]],
                                 "selected": i == pointers[index]} for i, (k, m, delivered) in enumerate(ranked[:_RANKING_SHOWN])),
            stack_ranking=tuple(stack_trace), rejected_mounts=tuple(rejected[:_REJECTIONS_SHOWN]),
            fallback_causes=tuple((fallbacks or {}).get(key, ())),
        ))
    for external in request.externals:
        for dof in sorted(external.retained_dofs, key=lambda d: _DOF_ORDER.index(d.axis)):
            u = snap_vec(rotate(q_mul(yaw, external.rotation_design), _AXES[dof.axis]))
            dofs.append(DofResultV2(external.instance_id, dof.axis, dof.control, "NONE", "NOT_DELIVERED", u,
                                    snap_vec(rotate(external.rotation_design, _AXES[dof.axis])), "NONE", None, None, None, None, None, None,
                                    customer_supplied=True))
    external_poses = {e.instance_id: _snap_pose(design_to_lab.compose(Pose(e.position_design, e.rotation_design))) for e in request.externals}
    observation_poses = {o.observation_id: _snap_pose(design_to_lab.compose(Pose(o.position_design, o.rotation_design))) for o in request.observations}
    fx0, fy0 = layout.footprint[0]
    fx1, fy1 = layout.footprint[1]
    if truncated:  # the beam trace stopped at a cap: the part past it was never tested against the hardware
        assumptions.add("beam.paths_truncated")
    engagement_fact = not any(a.endswith("insertion_interval") for a in assumptions)
    envelopes_fact = not any(p.approximate_envelope for p in parts)
    reserved = tuple(ReservedSpaceV2(e.instance_id, tuple(layout.bodies[reserved_key(n)]))
                     for n, e in enumerate(request.externals, start=1) if reserved_key(n) in layout.bodies)
    clearance = (f"no bodies of different chains come closer than {policy.inter_chain_clearance_mm:g} mm" if not reserved else
                 f"no bodies of different chains, and no body and the space reserved for a customer-supplied component, come "
                 f"closer than {policy.inter_chain_clearance_mm:g} mm")
    checks = (
        CheckV2("HEIGHT_CLOSURE", "PASS", "every optic's axis height is closed by its mount height, post length, holder height and insertion depth"),
        CheckV2("INTERFACE_FIT", "PASS", "post diameter equals the holder clamp diameter; post stud equals the mount's post interface"
                + ("" if not any(m.fastener and m.fastener[0] == "CAP_SCREW_THROUGH_BASE" for m in mates) else
                   " (or, where the mount is fastened by the cap screw supplied with it, the screw's thread equals the post's top thread)")),
        CheckV2("INTRA_CHAIN_ENVELOPE", "PASS", "no two bodies of one chain intersect outside their support contacts"),
        CheckV2("INTER_CHAIN_CLEARANCE", "PASS", clearance),
        CheckV2("BEAM_PATH_CLEARANCE", "PASS" if paths else "NOT_RUN", "the nominal beam path passes through no hardware except its own optics' mounts")
        if not truncated else
        CheckV2("BEAM_PATH_CLEARANCE", "NOT_RUN", "the traced part of the nominal beam path passes through no hardware except its own "
                "optics' mounts; the trace stopped at a cap of the derivation policy",
                context={"complete": False, "paths_traced": len(paths), "stopped": [dict(stop) for stop in truncated]}),
        CheckV2("BASE_FIXATION", "PASS", "each holder is fixed by its slot on its own board hole"),
        CheckV2("ENGAGEMENT_RANGE", "PASS" if engagement_fact else "NOT_RUN",
                "post insertion within the holder's insertion interval" + ("" if engagement_fact else " (the interval is an assumption)")),
        CheckV2("ACCEPTED_ENVELOPES", "PASS" if envelopes_fact else "NOT_RUN", "part envelopes rest on accepted facts" if envelopes_fact
                else "some part envelopes are approximate proxies"),
        CheckV2("ASSEMBLED_LAYOUT", "NOT_RUN", "the assembled optical scene is not re-simulated by the kernel"),
    )
    externals_out = tuple({"instance_id": e.instance_id, "position_mm": list(external_poses[e.instance_id].position)} for e in request.externals
                          if e.role == "source")
    decision = BoardDecisionV2(board, (0.0, 0.0), (board.size_x_mm, board.size_y_mm), (round(fx0, 6), round(fy0, 6)), (round(fx1, 6), round(fy1, 6)),
                               policy.board_edge_margin_mm, tuple(considered), externals_out)
    return AssemblyRealizationV2(
        design_yaw=snap_quat(yaw), design_translation=translation,
        parts=tuple(parts), mates=tuple(mates), settings=tuple(settings), fixations=tuple(fixations), dofs=tuple(dofs), checks=checks,
        chains=tuple(traces), board=decision, beam_paths=paths, optic_lab_poses=optic_poses, external_lab_poses=external_poses,
        observation_lab_poses=observation_poses, assumptions=tuple(sorted(assumptions)), reserved=reserved,
    )


def _snap_pose(pose: Pose) -> Pose:
    return Pose(snap_vec(pose.position), snap_quat(pose.rotation))


# ------------------------------------------------------------------------------------------------ one optic, no layout

#: The pose ``resolve_optic_chain`` gives every optic: local +Z (the optical axis) along lab +X (horizontal), local +Y
#: up, local +X along lab +Y. A retained DOF therefore keeps its meaning: Tx across the beam (horizontal), Ty vertical,
#: Tz along the beam, Rx / Ry the tilts about those, Rz the roll about the optical axis.
OPTIC_CHAIN_ROTATION: Quat = (0.5, 0.5, 0.5, 0.5)
_CHAIN_KEY = "c1"


def _height_span(mount: KMount, catalog: KernelCatalog, pins: ChainPins | None = None) -> tuple[float, float] | None:
    """The axis heights ``mount`` reaches with any post and holder, within the policy bounds (None: none)."""

    policy = catalog.policy
    intervals = _height_intervals(mount, catalog, pins)
    if not intervals:
        return None
    lo = max(min(a for a, _ in intervals), policy.min_beam_height_mm)
    hi = min(max(b for _, b in intervals), policy.max_beam_height_mm)
    return None if lo > hi + _EPS else (round(lo, 1) + 0.0, round(hi, 1) + 0.0)


def _default_height(mount: KMount, catalog: KernelCatalog, pins: ChainPins | None = None) -> float | None:
    """The axis height ``resolve_assembly`` gives a single optic on ``mount`` without a requested height: the preferred
    beam height, else the nearest reachable one on the policy's step (``_choose_lift`` for one chain at z = 0)."""

    policy = catalog.policy
    intervals = _height_intervals(mount, catalog, pins)
    if not intervals:
        return None
    preferred = policy.preferred_beam_height_mm
    step = policy.beam_height_step_mm
    limit = int((policy.max_beam_height_mm - policy.min_beam_height_mm) / step) + 1
    for t in [preferred] + [t for k in range(1, limit + 1) for t in (preferred - k * step, preferred + k * step)]:
        if t < policy.min_beam_height_mm - _EPS or t > policy.max_beam_height_mm + _EPS:
            continue
        if any(lo - _EPS <= t <= hi + _EPS for lo, hi in intervals):
            return t
    return None


def _chain_dofs(optic: _Optic, mount: KMount, stack: _Stack, mount_rotation: Quat, policy: ResolverPolicyV1,
                ranked: Sequence[tuple[tuple, KMount, list[str]]]) -> list[DofResultV2]:
    """The requested retained DOFs of one optic matched to its chain's mechanisms (``_implement_dofs`` without a
    layout: the base slot is taken along the optical axis, its travel the slot's length centred on the screw)."""

    depth = stack.depth
    engagement_basis = "RESOLVED" if _is_fact(stack.holder, "insertion_min_mm", "insertion_max_mm") else "PREVIEW_ASSUMPTION"
    slot_basis = "RESOLVED" if _is_fact(stack.holder, "base.slot") else "PREVIEW_ASSUMPTION"
    half_slot = (stack.holder.base.slot_end_mm - stack.holder.base.slot_start_mm) / 2.0
    mechanisms = _mount_mechanisms(mount, mount_rotation) + [
        _Mechanism("POST_HEIGHT_CLAMP", "TRANSLATION", _UP, "post", (-(stack.depth_max - depth), depth - stack.depth_min), "mm",
                   engagement_basis, "CONTINUOUS_CLAMPED", "Optical height: slide the post in its holder and lock it by hand."),
        _Mechanism("POST_ROTATION_CLAMP", "ROTATION", _UP, "post", (-_HALF_TURN_DEG, _HALF_TURN_DEG), "deg", "RESOLVED", "CONTINUOUS_CLAMPED",
                   "Rotation about the vertical post axis by hand."),
        _Mechanism("BASE_SLOT_TRAVEL", "TRANSLATION", optic.axis_lab, "holder", (-half_slot, half_slot), "mm", slot_basis,
                   "COARSE_REPOSITION", "Coarse position along the base slot when the holder is fixed with its slot along the "
                   "beam: loosen the base screw, slide, re-tighten."),
    ]
    selected = next((i for i, (_, m, _) in enumerate(ranked) if m.identity == mount.identity), len(ranked))
    tried_before = {axis for _, _, delivered in ranked[:selected] for axis in delivered}
    offered_after = {axis for _, _, delivered in ranked[selected + 1:] for axis in delivered}
    design_rotation = dof_frame(optic.request.rotation_design)
    out = []
    for dof in sorted(optic.request.retained_dofs, key=lambda d: _DOF_ORDER.index(d.axis)):
        motion = "TRANSLATION" if dof.axis.startswith("T") else "ROTATION"
        u = _lab_direction(optic.rotation_lab, dof.axis)
        design_dir = snap_vec(rotate(design_rotation, _AXES[dof.axis]))
        match = next(((m, s) for m in mechanisms if m.motion == motion and m.travel is not None
                      for s in (_parallel(m.axis, u, policy.parallel_tolerance),) if s != 0.0), None)
        if match is None:
            why = ("HARDWARE_DID_NOT_FIT" if dof.axis in tried_before
                   else "NOT_COMBINABLE_WITH_OTHER_DOFS" if dof.axis in offered_after else "NO_COMPATIBLE_HARDWARE")
            out.append(DofResultV2(optic.request.instance_id, dof.axis, dof.control, "NONE", "NOT_DELIVERED", u, design_dir,
                                   "NONE", None, None, None, None, None, None, not_delivered_reason=why))
            continue
        mech, sign = match
        lo, hi = mech.travel  # type: ignore[misc]
        travel = (round(lo, 6) + 0.0, round(hi, 6) + 0.0) if sign > 0 else (round(-hi, 6) + 0.0, round(-lo, 6) + 0.0)
        status = "DELIVERED_AS_MANUAL_FALLBACK" if dof.control == "NUMERICAL" else "DELIVERED"
        out.append(DofResultV2(optic.request.instance_id, dof.axis, dof.control, "MANUAL", status, u, design_dir, mech.code,
                               mech.provider, mech.provider, travel, mech.unit, mech.basis, mech.adjustment,
                               description=mech.description))
    return out


def optic_incidences(request: AssemblyResolutionRequestV2, policy: ResolverPolicyV1) -> dict[str, Incidence]:
    """How the nominal beam of a design meets each of its OpenSci optics (instance id -> ``Incidence``: the largest
    angle between a beam segment at the optic and its axis, and the largest offset from its centre), as
    ``resolve_assembly`` computes it before choosing mounts. An optic the traced beam does not visit is absent."""

    paths = trace_beam_paths(request.externals, request.optics, request.observations,
                             **beam_trace_settings(policy))
    return _incidences(request.optics, paths)


def resolve_optic_chain(optic: OpticRequest, catalog: KernelCatalog, *, beam_height_mm: float | None = None,
                        incidence_deg: float = 0.0, incidence_offset_mm: float = 0.0) -> OpticChainV1:
    """The support chain (mount, post, post holder) of ONE optic at a beam height, without a layout or a board, or
    ``AssemblyResolutionRefusal``.

    The optic stands with its optical axis horizontal (``OPTIC_CHAIN_ROTATION`` replaces its design pose); its
    ``retained_dofs`` and its ``mechanics`` are used as given. ``beam_height_mm`` is the optical axis above the
    surface the holder stands on (None: the height ``resolve_assembly`` gives a single optic -- the preferred beam
    height when reachable). ``incidence_deg`` is the angle at which the beam meets the optic (0: along its axis; a fold
    mirror 45) and ``incidence_offset_mm`` how far from its centre (``optic_incidences`` of a design): a mount whose
    bore would clip the beam there is incompatible.

    The mounts are ranked exactly as ``resolve_assembly`` ranks them (``rank_mounts``) and walked best first; a mount is
    skipped when no post and holder of the catalog reach the height with it (``_choose_stack``) or when its chain's
    parts would intersect (``_intra_chain_conflicts``). So when the best-ranked mount reaches the height, the chain is
    the one the full kernel resolves for the optic alone; when it does not, a lower-ranked mount that does is chosen
    (``fell_back``) instead of refusing. The post and holder are chosen by ``_choose_stack``.

    Refusals (``details``; ``internal`` names the mounts considered): ``NO_COMPATIBLE_MOUNT`` (no mount holds the
    optic -- ``details.reason`` ``OPTIC_NOT_HELD`` or ``MOUNT_CLIPS_OBLIQUE_BEAM`` -- or every chain's parts collide,
    ``SUPPORTS_COLLIDE``) and ``REQUESTED_BEAM_HEIGHT_UNREACHABLE`` (outside the policy bounds or no compatible mount
    reaches it; ``details.reachable_axis_height_mm`` when some height is reachable). Deterministic and pure."""

    policy = catalog.policy
    request = replace(optic, position_design=(0.0, 0.0, 0.0), rotation_design=OPTIC_CHAIN_ROTATION)
    _check_orientation(request, policy)
    chain = _optic_with_yaw(request, _CHAIN_KEY, IDENTITY)
    if not math.isfinite(incidence_deg) or not 0.0 <= incidence_deg < _QUARTER_TURN_DEG:
        raise ValueError("incidence_deg must be in [0, 90)")
    if not math.isfinite(incidence_offset_mm) or incidence_offset_mm < 0.0:
        raise ValueError("incidence_offset_mm must be finite and not negative")
    incidence = Incidence(math.cos(math.radians(incidence_deg)), float(incidence_offset_mm))
    ranked, rejected = rank_mounts(chain, catalog, incidence)
    base = {"instance_id": request.instance_id, "optical_sku": request.optical_sku}
    if not ranked:
        raise _no_mount_refusal(chain, rejected, incidence)
    spans = [s for s in (_height_span(m, catalog, optic.pins) for _, m, _ in ranked) if s is not None]
    reachable = (min(s[0] for s in spans), max(s[1] for s in spans)) if spans else None
    if beam_height_mm is not None:
        if not math.isfinite(beam_height_mm):
            raise ValueError("beam_height_mm must be finite")
        if not policy.min_beam_height_mm - _EPS <= beam_height_mm <= policy.max_beam_height_mm + _EPS:
            raise AssemblyResolutionRefusal(
                "REQUESTED_BEAM_HEIGHT_UNREACHABLE", "the beam height is outside the heights OpenSci mounting hardware is "
                f"configured for ({policy.min_beam_height_mm:g} to {policy.max_beam_height_mm:g} mm)",
                details={**base, "reason": "OUTSIDE_SUPPORTED_RANGE", "requested_beam_height_mm": beam_height_mm,
                         "supported_beam_height_mm": [policy.min_beam_height_mm, policy.max_beam_height_mm],
                         **({"reachable_axis_height_mm": list(reachable)} if reachable else {})})
    skipped: list[dict[str, Any]] = []
    collided = False
    for index, (_key, mount, _delivered) in enumerate(ranked):
        height = beam_height_mm if beam_height_mm is not None else _default_height(mount, catalog, optic.pins)
        if height is None:
            skipped.append({"mount": mount.identity, "why": "NO_STACK_FOR_MOUNT"})
            continue
        height = round(float(height), _SNAP_DIGITS) + 0.0
        stack, stack_trace = _choose_stack(mount, height, catalog, optic.pins)
        if stack is None:
            skipped.append({"mount": mount.identity, "why": "NO_STACK_AT_BEAM_HEIGHT", "reachable": _height_span(mount, catalog, optic.pins)})
            continue
        plan = _plan_chain(chain, mount, height, catalog)
        conflicts = _intra_chain_conflicts(_chain_bodies(plan, (0.0, 0.0), None))
        if conflicts:
            collided = True
            skipped.append({"mount": mount.identity, "why": "INTRA_CHAIN_COLLISION", "pairs": conflicts})
            continue
        assumptions = []
        if not _is_fact(stack.holder, "insertion_min_mm", "insertion_max_mm"):
            assumptions.append("holder.insertion_interval")
        if not _is_fact(mount, "envelope", "centre_height_mm"):
            assumptions.append("mount.geometry")
        if any(r["mount"] == mount.identity and r["reason"] == OBLIQUE_CLEARANCE_UNVERIFIED for r in rejected):
            assumptions.append("mount.oblique_beam_clearance")
        engagement_fact = "holder.insertion_interval" not in assumptions
        envelopes_fact = _is_fact(stack.holder, "envelope") and _is_fact(mount, "envelope", "centre_height_mm")
        mech = request.mechanics
        joints = (
            ChainJointV1("OPTIC_IN_MOUNT", f"OD{_fmt(mech.size_mm, 3)}-SEAT"),
            ChainJointV1("MOUNT_ON_POST", _metric(mount.post_thread),
                         (mount.post_fastening, _metric(mount.post_thread)) if mount.post_fastening else None),
            ChainJointV1("POST_IN_HOLDER", f"OD{_fmt(stack.post.diameter_mm, 3)}-POST"),
            ChainJointV1("HOLDER_ON_SURFACE", _metric(stack.holder.base.thread)),
        )
        checks = (
            CheckV2("MOUNT_HOLDS_OPTIC", "PASS", "the mount holds the optic's shape, size and thickness, leaves its clear "
                    "aperture open where light passes through it and does not clip the beam at the stated incidence"),
            CheckV2("HEIGHT_CLOSURE", "PASS", "the optical axis height is closed by the holder height, post length, mount "
                    "axis height and insertion depth"),
            CheckV2("INTERFACE_FIT", "PASS", "post diameter equals the holder clamp diameter; post stud equals the mount's "
                    "post interface" + ("" if mount.post_fastening != "CAP_SCREW_THROUGH_BASE" else
                                        " (the mount is fastened by the cap screw supplied with it, of the post's top thread)")),
            CheckV2("INTRA_CHAIN_ENVELOPE", "PASS", "no two parts of the chain intersect outside their support contacts"),
            CheckV2("ENGAGEMENT_RANGE", "PASS" if engagement_fact else "NOT_RUN",
                    "post insertion within the holder's insertion interval" + ("" if engagement_fact else " (the interval is an assumption)")),
            CheckV2("ACCEPTED_ENVELOPES", "PASS" if envelopes_fact else "NOT_RUN",
                    "part envelopes rest on accepted facts" if envelopes_fact else "some part envelopes are approximate proxies"),
            CheckV2("LAYOUT", "NOT_RUN", "no layout: clearance to other optics and hardware, the beam path beyond this optic "
                    "and the fixing hole positions are not checked"),
        )
        dofs = tuple(_chain_dofs(chain, mount, stack, plan.mount_rotation, policy, ranked))
        conditions = ("BASE_SLOT_ALONG_BEAM",) if any(d.mechanism == "BASE_SLOT_TRAVEL" for d in dofs) else ()
        return OpticChainV1(
            instance_id=request.instance_id, optical_sku=request.optical_sku, mount=mount, post=stack.post, holder=stack.holder,
            beam_height_mm=height, incidence_deg=float(incidence_deg), insertion_depth_mm=stack.depth,
            insertion_interval_mm=(stack.depth_min, stack.depth_max),
            height_travel_mm=(round(-(stack.depth_max - stack.depth), 6) + 0.0, round(stack.depth - stack.depth_min, 6) + 0.0),
            reachable_axis_height_mm=_height_span(mount, catalog, optic.pins) or (height, height),
            dofs=dofs, joints=joints, checks=checks, assumptions=tuple(assumptions), conditions=conditions,
            fell_back=index > 0 and any(s["why"] != "INTRA_CHAIN_COLLISION" for s in skipped),
            trace={"mount_ranking": [{"mount": m.identity, "delivers": d, "selected": i == index}
                                     for i, (_, m, d) in enumerate(ranked[:_RANKING_SHOWN])],
                   "skipped": skipped, "stack_ranking": stack_trace, "rejected_mounts": rejected[:_REJECTIONS_SHOWN]},
        )
    if collided and not any(s["why"] != "INTRA_CHAIN_COLLISION" for s in skipped):
        raise AssemblyResolutionRefusal("NO_COMPATIBLE_MOUNT", "the parts of every mounting chain for this optic would "
                                        "collide with each other", details={**base, "reason": "SUPPORTS_COLLIDE"},
                                        internal={"skipped": skipped})
    raise AssemblyResolutionRefusal(
        "REQUESTED_BEAM_HEIGHT_UNREACHABLE", "no mounting chain holds this optic at the requested beam height",
        details={**base, "reason": "BEAM_HEIGHT_NOT_REACHABLE", "requested_beam_height_mm": beam_height_mm,
                 **({"reachable_axis_height_mm": list(reachable)} if reachable else {})},
        internal={"skipped": skipped})


__all__ = ["OPTIC_CHAIN_ROTATION", "Incidence", "diagnose_kept_fallbacks", "dof_frame", "mount_incompatibility", "oblique_clearance",
           "oblique_status", "optic_incidences", "rank_mounts", "resolve_assembly", "resolve_optic_chain"]
