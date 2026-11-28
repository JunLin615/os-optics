"""One optic's support chain on a board: chosen once (``place_single_chain``) or re-posed with the hardware it has
(``replace_chain``).

``place_single_chain`` is the kernel's one-optic chain (``kernel.resolve_optic_chain``: the mount ranking, the post and
holder at the optic's axis height, the DOF delivery) plus the holder's fixation: the fork is turned to the nearest free
board hole its slot reaches (by distance from the post axis, then the hole's (i, j)). It serves the per-chain fallback of
a derivation the whole-design search refused, and the plan edits that select hardware again (``rederive_chain``,
``set_mounting CHAIN``). ``replace_chain`` recomputes the poses of a chain's existing hardware for a moved optic, swapped
hardware, another fixing hole or another board, without selecting anything; it reports what no longer holds
(``fixation_problem``: no hole within the slot / the kept hole is off the slot), never repairs it silently.

Every pose is the kernel's (``kernel.assemble``): the board top is lab z = 0 and the optic's axis height its lab z.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field

from .kernel import (AssemblyResolutionRefusal, ChainPins, DofResultV2, KBoard, KernelCatalog, KHolder, KMount, KPost, OpticMechanicsV1,
                     OpticRequest, resolve_optic_chain)
from .kernel.assemble import ChainPoses, chain_poses
from .kernel.geometry import IDENTITY, Pose, Quat, Vec3, rotate, snap_vec, unit, yaw_quaternion

#: A hole lies within a slot when its distance from the post axis is inside the slot's interval by this much (mm).
SLOT_TOLERANCE_MM = 1e-9
#: Decimals of a slot offset (mm).
SLOT_DECIMALS = 6

FIXATION_NO_FREE_HOLE = "NO_FREE_HOLE"
FIXATION_OFF_SLOT = "HOLE_OFF_SLOT"
FIXATION_OFF_BOARD = "HOLE_OFF_BOARD"


@dataclass(frozen=True)
class ChainPlacement:
    """One optic's chain in the lab. ``mount`` / ``post`` / ``holder`` None: no compatible hardware (``refusal`` says
    why); ``hole`` None: no fixing hole (``fixation_problem``)."""

    instance_id: str
    optic: OpticMechanicsV1
    optic_pose: Pose
    axis_lab: Vec3
    mount: KMount | None = None
    post: KPost | None = None
    holder: KHolder | None = None
    poses: ChainPoses | None = None
    hole: tuple[int, int] | None = None
    holder_yaw: Quat | None = None
    slot_offset_mm: float | None = None
    dofs: tuple[DofResultV2, ...] = ()
    assumptions: tuple[str, ...] = ()
    fallback_causes: tuple[dict, ...] = ()
    refusal: AssemblyResolutionRefusal | None = None
    fixation_problem: str | None = None
    fell_back: bool = False
    extras: dict = field(default_factory=dict, compare=False)

    @property
    def resolved(self) -> bool:
        return self.mount is not None and self.post is not None and self.holder is not None and self.poses is not None


def axis_of(pose: Pose) -> Vec3:
    """The horizontal unit direction of an optic's optical axis (its local +Z) in the lab."""

    axis = rotate(pose.rotation, (0.0, 0.0, 1.0))
    return snap_vec(unit((axis[0], axis[1], 0.0)))


def hole_xy(board: KBoard, i: int, j: int) -> tuple[float, float]:
    return (board.edge_offset_mm + i * board.pitch_mm, board.edge_offset_mm + j * board.pitch_mm)


def board_holes(board: KBoard) -> Iterable[tuple[int, int]]:
    nx, ny = board.hole_count
    for i in range(nx):
        for j in range(ny):
            yield (i, j)


def choose_hole(board: KBoard, axis_xy: tuple[float, float], holder: KHolder,
                occupied: Iterable[tuple[int, int]] = ()) -> tuple[tuple[int, int], Quat, float] | None:
    """The free board hole the holder's fork slot reaches from the post axis, nearest first (then by (i, j)), with the
    fork's yaw toward it and the hole's offset along the slot; None when the slot reaches no free hole."""

    taken = set(occupied)
    start, end = holder.base.slot_start_mm, holder.base.slot_end_mm
    best: tuple[float, tuple[int, int]] | None = None
    for hole in board_holes(board):
        if hole in taken:
            continue
        x, y = hole_xy(board, *hole)
        distance = math.hypot(x - axis_xy[0], y - axis_xy[1])
        if start - SLOT_TOLERANCE_MM <= distance <= end + SLOT_TOLERANCE_MM and (best is None or (distance, hole) < best):
            best = (distance, hole)
    if best is None:
        return None
    distance, hole = best
    x, y = hole_xy(board, *hole)
    return hole, yaw_quaternion((x - axis_xy[0], y - axis_xy[1])), round(distance, SLOT_DECIMALS)


def slot_offset(board: KBoard, hole: tuple[int, int], axis_xy: tuple[float, float], holder_yaw: Quat) -> tuple[float, float]:
    """``(along, across)``: where ``hole`` lies in the holder frame (its +x is the fork slot), relative to the post axis."""

    x, y = hole_xy(board, *hole)
    dx, dy = x - axis_xy[0], y - axis_xy[1]
    w, z = holder_yaw[0], holder_yaw[3]
    # rotate (dx, dy) by -yaw: the inverse of the holder's yaw-only rotation
    c, s = w * w - z * z, 2.0 * w * z
    return round(c * dx + s * dy, SLOT_DECIMALS), round(-s * dx + c * dy, SLOT_DECIMALS)


def _fix(placement: ChainPlacement, board: KBoard, occupied: Iterable[tuple[int, int]], keep_hole: tuple[int, int] | None,
         keep_yaw: Quat | None) -> ChainPlacement:
    """``placement`` with its holder fixed: on ``keep_hole`` (with ``keep_yaw``, else turned toward it) when given, else
    on the nearest free hole its slot reaches."""

    poses = placement.poses
    assert poses is not None and placement.holder is not None
    axis_xy = (poses.mount.position[0], poses.mount.position[1])
    holder = placement.holder
    if keep_hole is not None:
        nx, ny = board.hole_count
        if not (0 <= keep_hole[0] < nx and 0 <= keep_hole[1] < ny):
            return _with_yaw(placement, keep_yaw or IDENTITY, None, None, FIXATION_OFF_BOARD)
        x, y = hole_xy(board, *keep_hole)
        yaw = keep_yaw if keep_yaw is not None else (yaw_quaternion((x - axis_xy[0], y - axis_xy[1])) if (x, y) != axis_xy else IDENTITY)
        along, across = slot_offset(board, keep_hole, axis_xy, yaw)
        ok = (abs(across) <= SLOT_TOLERANCE_MM
              and holder.base.slot_start_mm - SLOT_TOLERANCE_MM <= along <= holder.base.slot_end_mm + SLOT_TOLERANCE_MM)
        return _with_yaw(placement, yaw, keep_hole, along, None if ok else FIXATION_OFF_SLOT)
    found = choose_hole(board, axis_xy, holder, occupied)
    if found is None:
        return _with_yaw(placement, keep_yaw or IDENTITY, None, None, FIXATION_NO_FREE_HOLE)
    hole, yaw, along = found
    return _with_yaw(placement, yaw, hole, along, None)


def _with_yaw(placement: ChainPlacement, yaw: Quat, hole: tuple[int, int] | None, along: float | None,
              problem: str | None) -> ChainPlacement:
    assert placement.mount is not None and placement.post is not None and placement.holder is not None
    poses = chain_poses(placement.optic, placement.optic_pose, placement.axis_lab, placement.mount, placement.post, placement.holder, yaw)
    return ChainPlacement(**{**placement.__dict__, "poses": poses, "hole": hole, "holder_yaw": poses.holder.rotation,
                             "slot_offset_mm": along, "fixation_problem": problem})


def place_single_chain(optic: OpticRequest, optic_pose: Pose, kcatalog: KernelCatalog, board: KBoard,
                       occupied_holes: Iterable[tuple[int, int]] = (), pins: ChainPins | None = None, *,
                       incidence_deg: float = 0.0, incidence_offset_mm: float = 0.0) -> ChainPlacement:
    """The chain the kernel selects for ``optic`` held at ``optic_pose`` (lab; its z the axis height above the board
    top), fixed on the nearest free hole of ``board`` its slot reaches (module docstring). A refusal of the kernel's chain
    search leaves the hardware unresolved (``refusal``)."""

    axis = axis_of(optic_pose)
    base = ChainPlacement(instance_id=optic.instance_id, optic=optic.mechanics, optic_pose=optic_pose, axis_lab=axis)
    request = optic if pins is None else OpticRequest(optic.instance_id, optic.optical_sku, optic.role, optic.position_design,
                                                      optic.rotation_design, optic.retained_dofs, optic.mechanics, optic.ports, pins)
    try:
        chain = resolve_optic_chain(request, kcatalog, beam_height_mm=optic_pose.position[2], incidence_deg=incidence_deg,
                                    incidence_offset_mm=incidence_offset_mm)
    except AssemblyResolutionRefusal as refusal:
        return ChainPlacement(**{**base.__dict__, "refusal": refusal})
    placed = ChainPlacement(**{**base.__dict__, "mount": chain.mount, "post": chain.post, "holder": chain.holder,
                               "dofs": chain.dofs, "assumptions": chain.assumptions, "fell_back": chain.fell_back})
    placed = _with_yaw(placed, IDENTITY, None, None, None)
    return _fix(placed, board, occupied_holes, None, None)


def replace_chain(instance_id: str, optic: OpticMechanicsV1, optic_pose: Pose, mount: KMount, post: KPost, holder: KHolder,
                  board: KBoard, *, hole: tuple[int, int] | None = None, holder_yaw: Quat | None = None,
                  occupied_holes: Iterable[tuple[int, int]] = (), dofs: tuple[DofResultV2, ...] = ()) -> ChainPlacement:
    """The chain of ``optic`` at ``optic_pose`` with the given hardware, re-posed: the mount from the optic's seat, the
    post from the mount, the holder under the post axis; fixed on ``hole`` (kept; ``holder_yaw`` kept when given, else
    turned toward it) or, without one, on the nearest free hole its slot reaches. Nothing is selected."""

    placed = ChainPlacement(instance_id=instance_id, optic=optic, optic_pose=optic_pose, axis_lab=axis_of(optic_pose), mount=mount,
                            post=post, holder=holder, dofs=dofs)
    placed = _with_yaw(placed, holder_yaw or IDENTITY, None, None, None)
    return _fix(placed, board, occupied_holes, hole, holder_yaw)


__all__ = ["FIXATION_NO_FREE_HOLE", "FIXATION_OFF_BOARD", "FIXATION_OFF_SLOT", "ChainPlacement", "axis_of", "board_holes", "choose_hole",
           "hole_xy", "place_single_chain", "replace_chain", "slot_offset"]
