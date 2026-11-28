"""Behaviour of the multi-optic assembly kernel (``os_optics.assembly_plan.kernel``).

Several optics resolved together on one board; dimensional variants selected from catalog data (no per-length code);
support hardware varying with height and DOF requests; more than one board with the smallest fitting one chosen;
DOF-aware mount choice with truthful fallback; the reference chain reproduced through the rules; deterministic
selection; fail-closed refusals; the explicit frame contract.
"""

from __future__ import annotations

import dataclasses
import hashlib
import itertools
import json
import math

import pytest

from os_optics.assembly_plan.kernel import (
    AssemblyResolutionRefusal,
    AssemblyResolutionRequestV2,
    resolve_assembly,
)
from os_optics.assembly_plan.kernel.geometry import overlap_depth, q_mul, rotate
from os_optics.assembly_plan.kernel.resolve import BOARD_KEY

from kernel_fixture import (
    BEAM_X,
    BEAM_X_EXAMPLE,
    FIXED,
    HOLDER_HEIGHTS,
    KINEMATIC,
    LENS_50,
    ODD_OPTIC,
    POST_LENGTHS,
    ROTATION,
    XY,
    catalog,
    fold_request,
    lens,
    look,
    mirror,
    reference_request,
    screen,
    source,
)


def chain(realization, instance="L1"):
    return next(c for c in realization.chains if c.instance_id == instance)


def dof(realization, instance, axis):
    return next(d for d in realization.dofs if d.instance_id == instance and d.axis == axis)


def digest(realization) -> str:
    """A canonical digest of everything a realization says (for determinism checks)."""

    def plain(value):
        if dataclasses.is_dataclass(value):
            return {f.name: plain(getattr(value, f.name)) for f in dataclasses.fields(value)}
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        if isinstance(value, dict):
            return {str(k): plain(v) for k, v in sorted(value.items())}
        if isinstance(value, (list, tuple, frozenset, set)):
            items = [plain(v) for v in value]
            return sorted(items, key=json.dumps) if isinstance(value, (set, frozenset)) else items
        return value

    return hashlib.sha256(json.dumps(plain(realization), sort_keys=True, default=str).encode()).hexdigest()


# ------------------------------------------------------------------------------------------------ reference chain


def test_the_reference_chain_is_reproduced_by_the_rules():
    """One lens needing only vertical adjustment: the rules pick the fixed Ø25.4 mount, the 75 mm post and the 75 mm
    fork holder, at the 125 mm beam height with a 47 mm insertion."""

    r = resolve_assembly(reference_request(dofs=(("Ty", "MANUAL"),)), catalog())
    c = chain(r)
    assert (c.mount, c.post, c.holder) == ("OS-MECH-TEST-MOUNT-FIXED25", "OS-MECH-TEST-POST-D12P7-L75", "OS-MECH-TEST-HOLDER-L75")
    assert c.beam_height_mm == 125.0 and c.insertion_depth_mm == 47.0
    mount = r.part("rp:c1:mount")
    optic = r.part("rp:c1:optic")
    post = r.part("rp:c1:post")
    assert mount.pose.position[2] == pytest.approx(103.0) and post.pose.position[2] == pytest.approx(28.0)
    # the lens sits against the mount's stop, 2.8 mm upstream of the post axis
    assert optic.pose.position[0] - mount.pose.position[0] == pytest.approx(-2.8, abs=1e-9)
    assert optic.pose.position[2] == pytest.approx(125.0)
    ty = dof(r, "L1", "Ty")
    assert (ty.status, ty.mechanism, ty.travel) == ("DELIVERED", "POST_HEIGHT_CLAMP", (-28.0, 22.0))
    # the fork slot points along the beam with the fixing hole mid-slot: equal axial travel both ways
    assert c.fork_yaw_deg == 0.0
    assert [(f.slot_key, f.mode) for f in r.fixations] == [("rp:c1:holder", "SLOT")]


def test_board_top_is_the_lab_origin_plane_and_the_design_is_only_translated():
    r = resolve_assembly(reference_request(), catalog())
    assert r.design_yaw == (1.0, 0.0, 0.0, 0.0)
    board = r.part(BOARD_KEY)
    assert board.pose.position == (0.0, 0.0, -12.7)
    t = r.design_translation
    for key, pose in r.optic_lab_poses.items():
        assert pose.position == pytest.approx((0.0 + t[0], 0.0 + t[1], 0.0 + t[2]))


# ------------------------------------------------------------------------------------------------ DOF-aware mounts


def test_transverse_retained_dofs_select_a_translating_mount():
    """Manual transverse alignment of the focusing lens."""

    r = resolve_assembly(reference_request(dofs=(("Tx", "MANUAL"), ("Ty", "MANUAL"), ("Tz", "NUMERICAL"))), catalog())
    assert chain(r).mount == "OS-MECH-TEST-MOUNT-XY25"
    tx, ty, tz = (dof(r, "L1", a) for a in ("Tx", "Ty", "Tz"))
    assert (tx.status, tx.mechanism, tx.travel, tx.adjustment) == ("DELIVERED", "TRANSLATION_MOUNT", (-1.0, 1.0), "FINE_ADJUSTER")
    assert (ty.status, ty.mechanism) == ("DELIVERED", "TRANSLATION_MOUNT")
    assert (tz.status, tz.mechanism, tz.delivered_control) == ("DELIVERED_AS_MANUAL_FALLBACK", "BASE_SLOT_TRAVEL", "MANUAL")
    assert tz.travel == (-12.5, 12.5)


def test_without_retained_dofs_the_simplest_compatible_mount_is_chosen():
    assert chain(resolve_assembly(reference_request(), catalog())).mount == "OS-MECH-TEST-MOUNT-FIXED25"


def test_rotation_about_the_axis_selects_a_rotation_mount():
    r = resolve_assembly(reference_request(dofs=(("Rz", "MANUAL"),)), catalog())
    assert chain(r).mount == "OS-MECH-TEST-MOUNT-ROT25"
    rz = dof(r, "L1", "Rz")
    assert (rz.status, rz.mechanism, rz.travel) == ("DELIVERED", "ROTATION_MOUNT", (-180.0, 180.0))


def test_a_mirror_with_tip_tilt_gets_a_kinematic_mount_and_a_lens_never_does():
    r = resolve_assembly(fold_request(), catalog())
    assert chain(r, "M1").mount == "OS-MECH-TEST-MOUNT-KIN25"
    for axis in ("Rx", "Ry"):
        d = dof(r, "M1", axis)
        assert (d.status, d.mechanism, d.travel) == ("DELIVERED", "TIP_TILT_MOUNT", (-4.0, 4.0))
    # a kinematic mirror mount has no clear aperture: never offered to a transmissive optic
    only_kinematic = catalog(mounts=(KINEMATIC,))
    with pytest.raises(AssemblyResolutionRefusal) as refused:
        resolve_assembly(reference_request(dofs=(("Rx", "MANUAL"),)), only_kinematic)
    assert refused.value.code == "NO_COMPATIBLE_MOUNT"


def test_an_undeliverable_dof_is_reported_not_claimed():
    r = resolve_assembly(reference_request(dofs=(("Rx", "MANUAL"), ("Ry", "MANUAL"))), catalog())
    rx, ry = dof(r, "L1", "Rx"), dof(r, "L1", "Ry")
    assert (rx.status, rx.mechanism, rx.delivered_control) == ("NOT_DELIVERED", "NONE", "NONE")
    # rotation about the vertical post axis delivers Ry (local +Y is up), with the lever arm disclosed
    assert (ry.status, ry.mechanism) == ("DELIVERED", "POST_ROTATION_CLAMP") and ry.axis_offset_mm == pytest.approx(2.8)


# ------------------------------------------------------------------------------------------------ family variants


def _brute_force_best(realization, beam_height):
    """The best (post, holder) pair over the whole family, by the documented objective."""

    mount_centre = FIXED.centre_height_mm
    best = None
    for length, height in itertools.product(POST_LENGTHS, HOLDER_HEIGHTS):
        depth = height + length + mount_centre - beam_height
        lo, hi = max(10.0, round(height / 3, 3)), min(height, length)
        if not lo - 1e-9 <= depth <= hi + 1e-9:
            continue
        key = (-round(min(depth - lo, hi - depth), 6), length, height)
        best = min(best, (key, length, height)) if best else (key, length, height)
    return best


@pytest.mark.parametrize("beam_height", [60.0, 90.0, 125.0, 170.0, 230.0, 300.0])
def test_post_and_holder_are_family_variants_selected_by_the_beam_height(beam_height):
    r = resolve_assembly(reference_request(beam_height_mm=beam_height), catalog())
    c = chain(r)
    _, length, height = _brute_force_best(r, beam_height)
    assert (c.post, c.holder) == (f"OS-MECH-TEST-POST-D12P7-L{length:g}", f"OS-MECH-TEST-HOLDER-L{height:g}")
    assert c.beam_height_mm == beam_height
    assert height + length + FIXED.centre_height_mm - c.insertion_depth_mm == pytest.approx(beam_height)


@pytest.mark.parametrize("length", POST_LENGTHS)
def test_every_post_length_of_the_family_is_usable_without_code(length):
    """Any member of the post family works on its own: no per-length implementation exists."""

    r = resolve_assembly(reference_request(), catalog(posts=(length,)))
    assert chain(r).post == f"OS-MECH-TEST-POST-D12P7-L{length:g}"


def test_a_larger_optic_uses_its_own_diameter_family():
    request = AssemblyResolutionRequestV2(optics=(lens(mechanics=LENS_50),), externals=(source(),), observations=(screen(),))
    assert chain(resolve_assembly(request, catalog())).mount == "OS-MECH-TEST-MOUNT-FIXED50"


# ------------------------------------------------------------------------------------------------ multi-optic


def test_a_folded_two_optic_design_is_one_coherent_assembly():
    r = resolve_assembly(fold_request(), catalog())
    assert [c.chain_key for c in r.chains] == ["c1", "c2"]
    assert r.beam_paths[0].through == ("SRC", "M1", "L1", "focal_plane")
    # one board, every holder on its own hole, every body inside the board less its margin, no two chains touching
    holes = [f.hole for f in r.fixations]
    assert len(holes) == len(set(holes)) == 2
    margin = catalog().policy.board_edge_margin_mm
    sx, sy = r.board.board.size_x_mm, r.board.board.size_y_mm
    bodies = [(p.chain_key, b) for p in r.parts for b in p.bodies]
    for _, body in bodies:
        for x, y in body.plan_corners():
            assert margin - 1e-6 <= x <= sx - margin + 1e-6 and margin - 1e-6 <= y <= sy - margin + 1e-6
    for (ca, a), (cb, b) in itertools.combinations(bodies, 2):
        if ca != cb:
            assert overlap_depth(a, b) <= 0.0
    # the lens keeps its design position relative to the mirror
    m, l1 = r.optic_lab_poses["M1"].position, r.optic_lab_poses["L1"].position
    assert (l1[0] - m[0], l1[1] - m[1], l1[2] - m[2]) == pytest.approx(rotate(r.design_yaw, (0.0, 100.0, 0.0)))


def test_four_parallel_lenses_share_one_board_turned_to_fit_it():
    optics = tuple(lens(f"L{k}", position=(0.0, 100.0 * k, 0.0)) for k in range(4))
    sources = tuple(source(f"S{k}", position=(-200.0, 100.0 * k, 0.0)) for k in range(4))
    r = resolve_assembly(AssemblyResolutionRequestV2(optics=optics, externals=sources), catalog())
    assert len(r.chains) == 4
    assert (r.board.board.size_x_mm, r.board.board.size_y_mm) == (450.0, 150.0)
    assert r.design_yaw == pytest.approx((math.sqrt(0.5), 0.0, 0.0, math.sqrt(0.5)))


def test_the_grid_serves_the_chain_that_asks_for_an_axial_translation():
    """Two lenses 140 mm apart across their beams: both cannot stand on grid rows of a 25 mm grid. The one that retains
    Tz (along its beam) gets the row, so its fork slot runs along the beam and delivers Tz; the other one, which asked
    for nothing along its beam, turns its fork to reach a hole. Swapping which lens asks swaps the outcome."""

    for asking, other in (("L1", "L2"), ("L2", "L1")):
        wants = (("Tz", "MANUAL"),)
        optics = (lens("L1", position=(0.0, 0.0, 0.0), dofs=wants if asking == "L1" else ()),
                  lens("L2", position=(0.0, 140.0, 0.0), dofs=wants if asking == "L2" else ()))
        sources = (source("S1", position=(-200.0, 0.0, 0.0)), source("S2", position=(-200.0, 140.0, 0.0)))
        r = resolve_assembly(AssemblyResolutionRequestV2(optics=optics, externals=sources), catalog())
        assert dof(r, asking, "Tz").status == "DELIVERED" and dof(r, asking, "Tz").mechanism == "BASE_SLOT_TRAVEL"
        beam_deg = math.degrees(2.0 * math.atan2(r.design_yaw[3], r.design_yaw[0]))  # the beams' lab direction

        def along_beam(instance):
            off = (chain(r, instance).fork_yaw_deg - beam_deg) % 180.0
            return min(off, 180.0 - off) <= 1e-6

        assert along_beam(asking) and not along_beam(other)


def test_the_smallest_fitting_board_is_selected_and_a_limit_is_explained():
    r = resolve_assembly(reference_request(), catalog())
    assert (r.board.board.size_x_mm, r.board.board.size_y_mm) == (150.0, 100.0)
    considered = [c for c in r.board.considered if c["fits"]]
    assert considered[0]["board"] == r.board.board.identity
    with pytest.raises(AssemblyResolutionRefusal) as refused:
        resolve_assembly(reference_request(max_board_size_mm=(120.0, 120.0)), catalog())
    assert refused.value.code == "BREADBOARD_CONSTRAINT_UNSATISFIABLE"
    assert refused.value.details["smallest_fitting_board_mm"] == [150.0, 100.0]
    assert [150.0, 100.0] in refused.value.details["standard_board_sizes_mm"]


# ------------------------------------------------------------------------------------------------ frame contract


def test_rolling_a_round_lens_changes_its_dof_directions_not_the_hardware():
    """The design frame is +Z up. With local +Y up, Ty is the post height; the example quaternion (local +X down)
    makes Tx the vertical DOF instead -- same hardware, same placement."""

    up = resolve_assembly(reference_request(dofs=(("Tx", "MANUAL"), ("Ty", "MANUAL"))), catalog(mounts=(FIXED,)))
    down = resolve_assembly(reference_request(dofs=(("Tx", "MANUAL"), ("Ty", "MANUAL")), rotation=BEAM_X_EXAMPLE), catalog(mounts=(FIXED,)))
    assert [p.pose.position for p in up.parts] == [p.pose.position for p in down.parts]
    assert dof(up, "L1", "Ty").lab_direction == (0.0, 0.0, 1.0) and dof(up, "L1", "Ty").status == "DELIVERED"
    assert dof(up, "L1", "Tx").status == "NOT_DELIVERED"
    assert dof(down, "L1", "Tx").lab_direction == (0.0, 0.0, -1.0) and dof(down, "L1", "Tx").travel == (-22.0, 28.0)
    assert dof(down, "L1", "Ty").status == "NOT_DELIVERED"


def test_a_design_drawn_at_an_angle_is_turned_onto_the_grid():
    angle = look((math.cos(math.pi / 6), math.sin(math.pi / 6), 0.0))
    request = AssemblyResolutionRequestV2(optics=(lens(rotation=angle),), externals=(source(position=(-173.20508075688772, -100.0, 0.0), rotation=angle),))
    r = resolve_assembly(request, catalog())
    axis = rotate(r.optic_lab_poses["L1"].rotation, (0.0, 0.0, 1.0))
    assert abs(axis[1]) <= 1e-9 or abs(axis[0]) <= 1e-9  # the beam runs along a grid row or column
    assert chain(r).fork_yaw_deg in (0.0, 90.0, 180.0, -90.0)


# ------------------------------------------------------------------------------------------------ refusals


def test_a_vertical_optic_is_refused_not_reoriented():
    with pytest.raises(AssemblyResolutionRefusal) as refused:
        resolve_assembly(AssemblyResolutionRequestV2(optics=(lens(rotation=(1.0, 0.0, 0.0, 0.0)),)), catalog())
    assert refused.value.code == "UNSUPPORTED_OPTIC_ORIENTATION"


def test_an_optic_no_mount_holds_is_refused():
    request = AssemblyResolutionRequestV2(optics=(lens(mechanics=ODD_OPTIC),), externals=(source(),))
    with pytest.raises(AssemblyResolutionRefusal) as refused:
        resolve_assembly(request, catalog())
    assert refused.value.code == "NO_COMPATIBLE_MOUNT"


def test_optics_too_close_to_hold_are_refused():
    request = AssemblyResolutionRequestV2(optics=(lens("L1"), lens("L2", position=(8.0, 0.0, 0.0))), externals=(source(),))
    with pytest.raises(AssemblyResolutionRefusal) as refused:
        resolve_assembly(request, catalog())
    assert refused.value.code == "MECHANICAL_LAYOUT_CONFLICT"


def test_an_unreachable_requested_beam_height_is_refused():
    with pytest.raises(AssemblyResolutionRefusal) as refused:
        resolve_assembly(reference_request(beam_height_mm=20.0), catalog())
    assert refused.value.code == "REQUESTED_BEAM_HEIGHT_UNREACHABLE"


def test_a_layout_larger_than_every_standard_board_is_a_custom_platform():
    """Hardware 1300 mm apart (two lenses on parallel beams) fits no standard board (the largest is 1200 x 600)."""

    request = AssemblyResolutionRequestV2(optics=(lens("L1"), lens("L2", position=(0.0, 1300.0, 0.0))),
                                          externals=(source("S1"), source("S2", position=(-200.0, 1300.0, 0.0))))
    with pytest.raises(AssemblyResolutionRefusal) as refused:
        resolve_assembly(request, catalog())
    assert refused.value.code == "BREADBOARD_NO_STANDARD_FIT"
    assert refused.value.details["largest_standard_board_mm"]


def test_a_mount_body_in_another_optics_beam_is_refused():
    """L2 stands across the beam of S1 -> L1 (its own axis along +Y): its mount would block that beam."""

    request = AssemblyResolutionRequestV2(
        optics=(lens("L1", position=(0.0, 0.0, 0.0)), lens("L2", position=(-60.0, -4.0, 0.0), rotation=look((0.0, 1.0, 0.0)))),
        externals=(source("S1"),))
    with pytest.raises(AssemblyResolutionRefusal) as refused:
        resolve_assembly(request, catalog())
    assert refused.value.code == "MECHANICAL_LAYOUT_CONFLICT"


# ------------------------------------------------------------------------------------------------ determinism


def test_selection_is_deterministic_and_independent_of_catalog_order():
    first = resolve_assembly(fold_request(), catalog())
    again = resolve_assembly(fold_request(), catalog())
    shuffled = catalog(mounts=(ROTATION, KINEMATIC, XY, FIXED), boards=tuple(reversed(((150, 100), (200, 100), (150, 150), (200, 200), (300, 150),
                                                                                   (450, 150), (300, 300), (600, 300), (600, 600)))),
                       posts=tuple(reversed(POST_LENGTHS)), holders=tuple(reversed(HOLDER_HEIGHTS)))
    reordered = resolve_assembly(fold_request(), shuffled)
    assert digest(first) == digest(again)
    assert [(c.mount, c.post, c.holder, c.hole) for c in first.chains] == [(c.mount, c.post, c.holder, c.hole) for c in reordered.chains]
    assert first.part(BOARD_KEY).member.size_x_mm == reordered.part(BOARD_KEY).member.size_x_mm


def test_mount_mechanisms_turn_with_the_design():
    """A yaw of the whole design turns DOF directions and mechanism axes together (the mount ranking is yaw-free)."""

    r = resolve_assembly(fold_request(), catalog())
    for d in r.dofs:
        if d.status != "NOT_DELIVERED":
            part = r.part(d.implemented_by)
            assert part.chain_key == chain(r, d.instance_id).chain_key
    assert q_mul((1.0, 0.0, 0.0, 0.0), r.design_yaw) == r.design_yaw


# ------------------------------------------------------------------ virtual observation planes occupy nothing

def _layout_facts(r):
    """What a virtual observation plane must not change: the board, every part's pose and every hole."""

    return (r.board.board.identity, r.design_yaw, r.design_translation, tuple((p.slot_key, p.pose) for p in r.parts),
            tuple((f.slot_key, f.hole) for f in r.fixations))


def _with_screen(observation):
    return AssemblyResolutionRequestV2(optics=(lens(),), externals=(source(),), observations=(observation,))


def test_moving_a_virtual_observation_farther_away_does_not_enlarge_the_board():
    near = resolve_assembly(_with_screen(screen(position=(76.5, 0.0, 0.0))), catalog())
    for x in (300.0, 900.0, 5000.0):
        far = resolve_assembly(_with_screen(screen(position=(x, 0.0, 0.0))), catalog())
        assert _layout_facts(far) == _layout_facts(near), x


def test_enlarging_a_virtual_observation_aperture_does_not_enlarge_the_board_or_raise_the_beam():
    small = resolve_assembly(_with_screen(screen()), catalog())
    for half in (140.0, 600.0):
        big = dataclasses.replace(screen(), plane_corners=((-half, -half), (half, half), (-half, half), (half, -half)))
        r = resolve_assembly(_with_screen(big), catalog())
        assert _layout_facts(r) == _layout_facts(small), half
        assert chain(r, "L1").beam_height_mm == 125.0


def test_a_virtual_observation_outside_or_below_the_board_is_not_a_mechanical_refusal():
    reference = resolve_assembly(_with_screen(screen()), catalog())
    for position in ((76.5, 0.0, -400.0), (76.5, 900.0, 0.0), (-3000.0, 0.0, 0.0)):
        r = resolve_assembly(_with_screen(screen(position=position)), catalog())  # no refusal
        assert _layout_facts(r) == _layout_facts(reference), position
    # and at an explicitly requested beam height that would put a large plane below the board top
    big = dataclasses.replace(screen(), plane_corners=((-140.0, -140.0), (140.0, 140.0), (-140.0, 140.0), (140.0, -140.0)))
    r = resolve_assembly(AssemblyResolutionRequestV2(optics=(lens(),), externals=(source(),), observations=(big,), beam_height_mm=125.0),
                         catalog())
    assert chain(r, "L1").beam_height_mm == 125.0


def test_a_virtual_observation_remains_a_beam_path_endpoint():
    """The plane on the beam ends the nominal path (beam-clearance reasoning uses it); it is reported in the lab frame."""

    for x in (76.5, 900.0):
        r = resolve_assembly(_with_screen(screen(position=(x, 0.0, 0.0))), catalog())
        [path] = r.beam_paths
        assert path.through == ("SRC", "L1", "focal_plane")
        end = r.design_to_lab.apply(path.points_design[-1])
        assert end == pytest.approx(r.observation_lab_poses["focal_plane"].position)
    off_axis = resolve_assembly(_with_screen(screen(position=(76.5, 300.0, 0.0))), catalog())
    assert off_axis.beam_paths[0].through == ("SRC", "L1")  # a plane the beam does not reach is not an endpoint


