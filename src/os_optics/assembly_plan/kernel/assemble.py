"""The pieces of a realized support chain the plan rebuilds from its items: part datums, chain poses, collision bodies,
link poses and interface designations.

They are the rules ``resolve._assemble`` applies to a realization, stated once for one chain so that a plan edit (a moved
optic, swapped hardware, a re-fixed holder) re-poses its hardware exactly as the kernel placed it, and the preview
projection reads the same datums. Every pose is snapped like the kernel's (``snap_vec`` / ``snap_quat``).
"""

from __future__ import annotations

from dataclasses import dataclass

from .catalog import KBoard, KHolder, KMount, KPost, OpticMechanicsV1
from .geometry import IDENTITY, Pose, Quat, Vec3, YawBox, q_conj, q_mul, rotate, snap_quat, snap_vec, sub, yaw_quaternion
from .resolve import _fmt, _metric

#: Decimals of the interface designation numbers (``OD25.4-SEAT``).
_DESIGNATION_DIGITS = 3

#: The datum names of each part role (the scene's datum keys; the board's carrier datum is ``board_top``).
DATUMS = {
    "OPTIC": ("seat",),
    "MOUNT": ("support", "seat"),
    "POST": ("bottom", "top"),
    "POST_HOLDER": ("bottom", "bore_top"),
    "BREADBOARD": ("board_top",),
}
#: (carried datum, carrier datum) of each chain link kind.
LINK_DATUMS = {
    "OPTIC_IN_MOUNT": ("seat", "seat"),
    "MOUNT_ON_POST": ("support", "top"),
    "POST_IN_HOLDER": ("bottom", "bore_top"),
    "HOLDER_ON_BOARD": ("bottom", "board_top"),
}


def pose_in(parent: Pose, child_world: Pose) -> Pose:
    """``child_world`` in the frame of ``parent`` (snapped)."""

    inv = q_conj(parent.rotation)
    return Pose(snap_vec(rotate(inv, sub(child_world.position, parent.position))), snap_quat(q_mul(inv, child_world.rotation)))


def mount_rotation(axis_lab: Vec3) -> Quat:
    """The mount frame's yaw: its +x along the optic's horizontal optical axis."""

    return yaw_quaternion((axis_lab[0], axis_lab[1]))


def optic_origin_in_mount(optic: OpticMechanicsV1, mount: KMount) -> Vec3:
    """Where the optic's own frame origin sits in the mount frame: on the seat stop (its rear face against it), or centred
    on the post axis, at the mount's centre height."""

    if mount.seat_stop_x_mm is not None:
        x = mount.seat_stop_x_mm - optic.axial_max_mm
    else:
        x = -(optic.axial_min_mm + optic.axial_max_mm) / 2.0
    return (snap_vec((x, 0.0, 0.0))[0], 0.0, mount.centre_height_mm)


@dataclass(frozen=True)
class ChainPoses:
    """The lab poses of one chain's parts (the board top at lab z = 0)."""

    optic: Pose
    mount: Pose
    post: Pose
    holder: Pose
    insertion_depth_mm: float


def chain_poses(optic: OpticMechanicsV1, optic_pose: Pose, axis_lab: Vec3, mount: KMount, post: KPost, holder: KHolder,
                holder_yaw: Quat) -> ChainPoses:
    """The poses of the chain that holds ``optic`` at ``optic_pose`` (lab): the mount turned with the optic's horizontal
    axis ``axis_lab`` and placed so the optic sits in its seat, the post below the mount's post interface, the holder
    on the board under the post axis turned by ``holder_yaw``; the insertion depth closes the height."""

    rotation = mount_rotation(axis_lab)
    origin = optic_origin_in_mount(optic, mount)
    mount_position = snap_vec(sub(optic_pose.position, rotate(rotation, origin)))
    mount_pose = Pose(mount_position, snap_quat(rotation))
    post_pose = Pose(snap_vec((mount_position[0], mount_position[1], mount_position[2] - post.length_mm)), mount_pose.rotation)
    holder_pose = Pose(snap_vec((mount_position[0], mount_position[1], 0.0)), snap_quat(holder_yaw))
    depth = snap_vec((holder.height_mm + post.length_mm - mount_position[2], 0.0, 0.0))[0]
    return ChainPoses(Pose(snap_vec(optic_pose.position), snap_quat(optic_pose.rotation)), mount_pose, post_pose, holder_pose, depth)


def part_datums(role: str, view: KMount | KPost | KHolder | KBoard | OpticMechanicsV1, *, mount_pose: Pose | None = None,
                optic_pose: Pose | None = None, optic: OpticMechanicsV1 | None = None) -> dict[str, Pose]:
    """The datum frames of one part, in its own frame (``resolve._assemble``). A mount's ``seat`` is where the optic's
    seat lies in the mount frame, so it needs the mount's and the optic's lab poses and the optic."""

    if role == "OPTIC":
        return {"seat": Pose((0.0, 0.0, view.axial_max_mm), IDENTITY)}  # type: ignore[union-attr]
    if role == "MOUNT":
        if mount_pose is None or optic_pose is None or optic is None:
            raise ValueError("a mount's seat datum needs the mount and optic poses and the optic")
        seat_world = optic_pose.compose(Pose((0.0, 0.0, optic.axial_max_mm), IDENTITY))
        return {"support": Pose((0.0, 0.0, 0.0)), "seat": pose_in(mount_pose, seat_world)}
    if role == "POST":
        return {"bottom": Pose((0.0, 0.0, 0.0)), "top": Pose((0.0, 0.0, view.length_mm))}  # type: ignore[union-attr]
    if role == "POST_HOLDER":
        return {"bottom": Pose((0.0, 0.0, 0.0)), "bore_top": Pose((0.0, 0.0, view.height_mm))}  # type: ignore[union-attr]
    if role == "BREADBOARD":
        return {"board_top": Pose((0.0, 0.0, view.thickness_mm), IDENTITY)}  # type: ignore[union-attr]
    raise ValueError(f"no datums for role {role!r}")


def board_pose(board: KBoard) -> Pose:
    """The board's pose in the lab: its top face at lab z = 0."""

    return Pose((0.0, 0.0, -board.thickness_mm), IDENTITY)


def link_poses(poses: ChainPoses, post: KPost, holder: KHolder, board: KBoard) -> dict[str, Pose]:
    """``b_in_a`` of each link of a chain (the carrier's datum in the carried part's frame, as ``_assemble`` states it):
    the seat and the post interface coincide (identity), the holder's bore top in the post frame, the board top in the
    holder frame."""

    bore_top_world = poses.holder.compose(Pose((0.0, 0.0, holder.height_mm)))
    board_top_world = board_pose(board).compose(Pose((0.0, 0.0, board.thickness_mm)))
    return {"OPTIC_IN_MOUNT": Pose((0.0, 0.0, 0.0)), "MOUNT_ON_POST": Pose((0.0, 0.0, 0.0)),
            "POST_IN_HOLDER": pose_in(poses.post, bore_top_world), "HOLDER_ON_BOARD": pose_in(poses.holder, board_top_world)}


def interface_designations(optic: OpticMechanicsV1, mount: KMount, post: KPost, holder: KHolder,
                           board: KBoard) -> dict[str, str]:
    """The interface designation of each link (``OD25.4-SEAT``, ``M4x0.7``, ``OD12.7-POST``, ``M6x1.0-GRID25``)."""

    return {"OPTIC_IN_MOUNT": f"OD{_fmt(optic.size_mm, _DESIGNATION_DIGITS)}-SEAT",
            "MOUNT_ON_POST": _metric(mount.post_thread),
            "POST_IN_HOLDER": f"OD{_fmt(post.diameter_mm, _DESIGNATION_DIGITS)}-POST",
            "HOLDER_ON_BOARD": f"{_metric(board.thread)}-GRID{_fmt(board.pitch_mm, _DESIGNATION_DIGITS)}"}


def fixation_thread(holder: KHolder) -> str:
    """The thread of a holder's fixing screw (``FIXATION_THREAD``)."""

    return _metric(holder.base.thread)


def chain_bodies(mount: KMount, post: KPost, holder: KHolder, poses: ChainPoses, *, owner: str = "chain",
                 fork: bool = True) -> dict[str, tuple[YawBox, ...]]:
    """Collision bodies of one chain in the lab (``resolve._chain_bodies``), keyed by role: the mount's envelope, the
    post's cylinder as a box, the holder's tube and (``fork``) its turned fork base."""

    post_box = YawBox(f"{owner}:post", (-post.diameter_mm / 2.0, -post.diameter_mm / 2.0, 0.0),
                      (post.diameter_mm / 2.0, post.diameter_mm / 2.0, post.length_mm), poses.post)
    rt = holder.tube_diameter_mm / 2.0
    holder_boxes = [YawBox(f"{owner}:holder", (-rt, -rt, 0.0), (rt, rt, holder.height_mm), poses.holder)]
    if fork:
        holder_boxes.append(YawBox(f"{owner}:holder", holder.base.body.min_mm, holder.base.body.max_mm, poses.holder))
    return {"MOUNT": (YawBox(f"{owner}:mount", mount.envelope.min_mm, mount.envelope.max_mm, poses.mount),),
            "POST": (post_box,), "POST_HOLDER": tuple(holder_boxes)}


def slot_hole_offset(holder_pose: Pose, hole_xy: tuple[float, float]) -> float | None:
    """How far along the holder's fork slot (its +x from the post axis) a board hole lies, or None when the hole is off
    the slot's centreline."""

    local = rotate(q_conj(holder_pose.rotation), sub((hole_xy[0], hole_xy[1], 0.0), holder_pose.position))
    across = snap_vec(local)[1]
    return None if across != 0.0 else snap_vec(local)[0]


__all__ = ["DATUMS", "LINK_DATUMS", "ChainPoses", "board_pose", "chain_bodies", "chain_poses", "fixation_thread",
           "interface_designations", "link_poses", "mount_rotation", "optic_origin_in_mount", "part_datums", "pose_in",
           "slot_hole_offset"]

