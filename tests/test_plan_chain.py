"""One chain on a board (``assembly_plan.chain``) and the chain pieces a plan rebuilds from (``kernel.assemble``): both
reproduce the kernel's own placement."""

from __future__ import annotations

import pytest
from kernel_fixture import FIXED, XY, catalog, holder, post, reference_request

from os_optics.assembly_plan.chain import (FIXATION_NO_FREE_HOLE, FIXATION_OFF_BOARD, FIXATION_OFF_SLOT, choose_hole, hole_xy,
                                           place_single_chain, replace_chain, slot_offset)
from os_optics.assembly_plan.kernel import BOARD_KEY, resolve_assembly
from os_optics.assembly_plan.kernel.assemble import (chain_bodies, chain_poses, interface_designations, link_poses, part_datums,
                                                     pose_in)
from os_optics.assembly_plan.kernel.geometry import Pose, yaw_quaternion


@pytest.fixture(scope="module")
def realized():
    return resolve_assembly(reference_request(dofs=(("Ty", "MANUAL"),)), catalog())


def test_the_chain_pieces_reproduce_the_kernels_own_poses_mates_and_datums(realized):
    parts = {p.role: p for p in realized.parts}
    optic, mount, stack_post, stack_holder = parts["OPTIC"], parts["OPTIC_MOUNT"], parts["POST"], parts["POST_HOLDER"]
    board = realized.board.board
    axis = (1.0, 0.0, 0.0)
    poses = chain_poses(optic.optic, optic.pose, axis, mount.member, stack_post.member, stack_holder.member, stack_holder.pose.rotation)
    assert (poses.mount, poses.post, poses.holder) == (mount.pose, stack_post.pose, stack_holder.pose)
    assert poses.insertion_depth_mm == realized.chains[0].insertion_depth_mm
    b_in_a = link_poses(poses, stack_post.member, stack_holder.member, board)
    mates = {(m.a, m.b): m for m in realized.mates}
    assert b_in_a["POST_IN_HOLDER"] == mates[(stack_post.slot_key, stack_holder.slot_key)].b_in_a
    assert b_in_a["HOLDER_ON_BOARD"] == mates[(stack_holder.slot_key, BOARD_KEY)].b_in_a
    designations = interface_designations(optic.optic, mount.member, stack_post.member, stack_holder.member, board)
    assert designations["OPTIC_IN_MOUNT"] == mates[(optic.slot_key, mount.slot_key)].interface_designation
    assert designations["HOLDER_ON_BOARD"] == mates[(stack_holder.slot_key, BOARD_KEY)].interface_designation
    assert part_datums("MOUNT", mount.member, mount_pose=mount.pose, optic_pose=optic.pose, optic=optic.optic) == mount.datums
    assert part_datums("POST", stack_post.member) == stack_post.datums
    assert part_datums("POST_HOLDER", stack_holder.member) == stack_holder.datums
    assert part_datums("BREADBOARD", board) == realized.part(BOARD_KEY).datums
    bodies = chain_bodies(mount.member, stack_post.member, stack_holder.member, poses)
    assert [(b.lo, b.hi, b.pose) for b in bodies["MOUNT"]] == [(b.lo, b.hi, b.pose) for b in mount.bodies]
    assert [(b.lo, b.hi, b.pose) for b in bodies["POST_HOLDER"]] == [(b.lo, b.hi, b.pose) for b in stack_holder.bodies]
    assert pose_in(mount.pose, mount.pose) == Pose((0.0, 0.0, 0.0))


def test_a_single_chain_is_the_kernels_one_optic_chain_fixed_on_the_nearest_hole(realized):
    optic = realized.parts[-1] if realized.parts[-1].role == "OPTIC" else next(p for p in realized.parts if p.role == "OPTIC")
    request = reference_request(dofs=(("Ty", "MANUAL"),)).optics[0]
    board = realized.board.board
    placed = place_single_chain(request, optic.pose, catalog(), board)
    assert placed.resolved and placed.fixation_problem is None
    assert (placed.mount.identity, placed.post.identity, placed.holder.identity) == (FIXED.identity, post(75).identity, holder(75).identity)
    axis_xy = (placed.poses.post.position[0], placed.poses.post.position[1])
    along, across = slot_offset(board, placed.hole, axis_xy, placed.holder_yaw)
    assert across == 0.0 and along == placed.slot_offset_mm
    assert placed.holder.base.slot_start_mm <= along <= placed.holder.base.slot_end_mm
    assert placed.poses.post.position == realized.part("rp:c1:post").pose.position
    # every other free hole within the slot is at least as far
    hole = placed.hole
    again = place_single_chain(request, optic.pose, catalog(), board, occupied_holes={hole})
    assert again.hole != hole and again.slot_offset_mm >= placed.slot_offset_mm


def test_pins_and_refusals_of_a_single_chain(realized):
    optic = next(p for p in realized.parts if p.role == "OPTIC")
    request = reference_request().optics[0]
    from os_optics.assembly_plan.kernel import ChainPins

    pinned = place_single_chain(request, optic.pose, catalog(), realized.board.board, pins=ChainPins(mount=XY.identity))
    assert pinned.mount.identity == XY.identity
    low = Pose((optic.pose.position[0], optic.pose.position[1], 5.0), optic.pose.rotation)
    refused = place_single_chain(request, low, catalog(), realized.board.board)
    assert not refused.resolved and refused.refusal is not None and refused.refusal.code == "REQUESTED_BEAM_HEIGHT_UNREACHABLE"


def test_replace_chain_reposes_the_hardware_and_reports_a_hole_that_no_longer_holds(realized):
    parts = {p.role: p for p in realized.parts}
    optic, mount, p_post, p_holder = parts["OPTIC"], parts["OPTIC_MOUNT"], parts["POST"], parts["POST_HOLDER"]
    board = realized.board.board
    hole = realized.fixations[0].hole
    same = replace_chain("L1", optic.optic, optic.pose, mount.member, p_post.member, p_holder.member, board, hole=hole,
                         holder_yaw=p_holder.pose.rotation)
    assert same.fixation_problem is None and same.poses.holder == p_holder.pose and same.hole == hole
    # the optic 2 mm higher: the post slides up, the holder and its hole stay
    up = Pose((optic.pose.position[0], optic.pose.position[1], optic.pose.position[2] + 2.0), optic.pose.rotation)
    raised = replace_chain("L1", optic.optic, up, mount.member, p_post.member, p_holder.member, board, hole=hole,
                           holder_yaw=p_holder.pose.rotation)
    assert raised.poses.insertion_depth_mm == pytest.approx(same.poses.insertion_depth_mm - 2.0) and raised.fixation_problem is None
    # moved 9 mm along y, the kept hole is off the slot's centreline
    aside = Pose((optic.pose.position[0], optic.pose.position[1] + 9.0, optic.pose.position[2]), optic.pose.rotation)
    off = replace_chain("L1", optic.optic, aside, mount.member, p_post.member, p_holder.member, board, hole=hole,
                        holder_yaw=p_holder.pose.rotation)
    assert off.fixation_problem == FIXATION_OFF_SLOT
    # without a kept hole the nearest free one is chosen again; a hole off the board is named
    refixed = replace_chain("L1", optic.optic, aside, mount.member, p_post.member, p_holder.member, board)
    assert refixed.fixation_problem is None and refixed.hole is not None
    nx, ny = board.hole_count
    assert replace_chain("L1", optic.optic, optic.pose, mount.member, p_post.member, p_holder.member, board,
                         hole=(nx, 0)).fixation_problem == FIXATION_OFF_BOARD


def test_choose_hole_finds_none_when_every_hole_in_reach_is_taken(realized):
    board = realized.board.board
    h = holder(75)
    axis = hole_xy(board, 2, 2)
    reach = {hole for hole in ((i, j) for i in range(board.hole_count[0]) for j in range(board.hole_count[1]))}
    assert choose_hole(board, axis, h, reach) is None
    found = choose_hole(board, axis, h)
    assert found is not None
    (hole, yaw, along) = found
    assert slot_offset(board, hole, axis, yaw) == (along, 0.0)
    assert yaw == yaw_quaternion((hole_xy(board, *hole)[0] - axis[0], hole_xy(board, *hole)[1] - axis[1]))
    parts = {p.role: p for p in realized.parts}
    full = replace_chain("L1", parts["OPTIC"].optic, parts["OPTIC"].pose, parts["OPTIC_MOUNT"].member, parts["POST"].member,
                         parts["POST_HOLDER"].member, board, occupied_holes=reach)
    assert (full.fixation_problem, full.hole, full.slot_offset_mm) == (FIXATION_NO_FREE_HOLE, None, None)
