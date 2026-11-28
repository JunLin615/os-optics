"""Assembly kernel hardening: a bounded, cached search, the
neutral refusal contract, requested retained DOFs driving hardware selection with reported fallbacks
, beam clearance through a mount at oblique incidence and small intentional plate tilts.
"""

from __future__ import annotations

import json
import math

import pytest

from os_optics.assembly_plan.kernel import (
    AssemblyResolutionRefusal,
    AssemblyResolutionRequestV2,
    DofRequest,
    Incidence,
    OpticRequest,
    ResolutionBudget,
    ResolverPolicyV1,
    mount_incompatibility,
    oblique_clearance,
    resolve_assembly,
)
from os_optics.assembly_plan.kernel import diagnose_kept_fallbacks
from os_optics.assembly_plan.kernel import resolve as kernel
from os_optics.assembly_plan.kernel.catalog import BoxV1

from kernel_fixture import (
    DICHROIC,
    FIXED,
    FIXED_ALT,
    KINEMATIC,
    KINEMATIC_T,
    LENS,
    ND_FILTER,
    ROTATION,
    ROTATION_DEEP,
    SQRT_HALF,
    XY,
    catalog,
    lens,
    look,
    mirror,
    mount,
    optic,
    reference_request,
    renamed,
    screen,
    source,
)

#: Catalog identities and slot keys never reach a refusal's details.
IDENTITY_WORDS = ("OS-MECH-", "rp:", "part_key")


def chain(realization, instance):
    return next(c for c in realization.chains if c.instance_id == instance)


def dof(realization, instance, axis):
    return next(d for d in realization.dofs if d.instance_id == instance and d.axis == axis)


def refusal_of(request, cat=None, **kwargs) -> AssemblyResolutionRefusal:
    with pytest.raises(AssemblyResolutionRefusal) as refused:
        resolve_assembly(request, cat or catalog(), **kwargs)
    assert_customer_safe(refused.value.details)
    return refused.value


def assert_customer_safe(details) -> None:
    text = json.dumps(details)
    for word in IDENTITY_WORDS:
        assert word not in text, (word, text)


def yawed(deg: float):
    a = math.radians(deg)
    return look((math.cos(a), math.sin(a), 0.0))


def row_of_lenses(count: int, spacing: float) -> AssemblyResolutionRequestV2:
    return AssemblyResolutionRequestV2(optics=tuple(lens(f"L{k}", position=(spacing * k, 0.0, 0.0)) for k in range(count)),
                                       externals=(source(),))


# ------------------------------------------------------------------------------------------------ bounded search


def _many_mounts(count: int):
    """``count`` fixed Ø25.4 mounts of distinct geometry (each a different layout), all compatible with the lens."""

    return tuple(mount(f"F{k:02d}", "FIXED", centre=20.0 + 0.25 * k, envelope=((-5.1 - 0.1 * k, -15.25, 0.0), (5.1, 15.25, 37.25 + k)))
                 for k in range(count))


def test_a_pathological_design_is_refused_within_the_work_budget():
    """Twelve lenses 21 mm apart on one beam can never all be fixed, and forty mounts of distinct geometry would each
    re-run the full board x orientation x translation sweep for every failing chain: the old search's work was
    ``max_mount_attempts`` full sweeps (tens of thousands of layout evaluations). The bounded search refuses within the
    budget, deterministically, with the most informative failure."""

    cat = catalog(mounts=_many_mounts(40), policy=ResolverPolicyV1(max_chain_placements=4000))
    request = row_of_lenses(12, 21.0)
    naive_bound = cat.policy.max_mount_attempts * len(cat.boards) * 2 * cat.policy.max_translations
    budget = ResolutionBudget(cat.policy.max_chain_placements)
    first = refusal_of(request, cat, budget=budget)
    assert budget.chain_placements <= cat.policy.max_chain_placements and budget.layout_evaluations < naive_bound
    assert first.code == "MECHANICAL_LAYOUT_CONFLICT"
    assert first.details["instance_id"].startswith("L") and first.details["suggestion"]["action"] == "ADJUST_COMPONENT_SPACING"
    again = refusal_of(request, cat)
    assert (again.code, again.details) == (first.code, first.details)


@pytest.mark.parametrize("attempts", [1, 2])
def test_a_refusal_at_the_mount_attempt_cap_names_it(attempts):
    """Two lenses 21 mm apart with forty mounts of distinct geometry: every mount the search tries fails the layout, so
    the policy's mount-attempt cap stops it; the refusal names that cap (the work budget is not spent)."""

    cat = catalog(mounts=_many_mounts(40), policy=ResolverPolicyV1(max_mount_attempts=attempts))
    budget = ResolutionBudget(12000)
    refused = refusal_of(row_of_lenses(2, 21.0), cat, budget=budget)
    assert refused.details["search_budget_exhausted"] is True and budget.exhausted_by is None
    assert refused.details["search_bound"] == {"kind": "MOUNT_ATTEMPTS", "limit": attempts, "spent": attempts}


def test_a_beam_trace_stopped_at_a_cap_leaves_the_clearance_check_not_run():
    """The lens design traces SRC -> L1 -> focal_plane. With the policy's point cap at two, the trace stops before the
    plane: the realization does not claim the beam clear of hardware past that point (BEAM_PATH_CLEARANCE NOT_RUN with
    the cap and where it stopped, assumption ``beam.paths_truncated``). Under the default caps nothing changes."""

    request = reference_request()
    full = resolve_assembly(request, catalog())
    check = {c.kind: c for c in full.checks}["BEAM_PATH_CLEARANCE"]
    assert (check.status, check.context) == ("PASS", None) and "beam.paths_truncated" not in full.assumptions
    capped = resolve_assembly(request, catalog(policy=ResolverPolicyV1(max_beam_points=2)))
    check = {c.kind: c for c in capped.checks}["BEAM_PATH_CLEARANCE"]
    assert check.status == "NOT_RUN"
    assert check.context == {"complete": False, "paths_traced": 1,
                             "stopped": [{"path_key": "beam-1", "limit": "POINTS", "value": 2, "at": "focal_plane"}]}
    assert "beam.paths_truncated" in capped.assumptions and capped.beam_paths[0].through == ("SRC", "L1")


def test_exhausting_the_budget_refuses_with_the_most_informative_failure_seen():
    cat = catalog(mounts=_many_mounts(40))
    budget = ResolutionBudget(5)
    refused = refusal_of(row_of_lenses(12, 21.0), cat, budget=budget)
    assert budget.exhausted_by == "WORK" and budget.chain_placements == 5  # charged before each chain: never past the budget
    assert refused.details["search_budget_exhausted"] is True
    assert refused.details["search_bound"] == {"kind": "CHAIN_PLACEMENTS", "limit": 5, "spent": 5}
    assert refused.details["reason"] in ("SUPPORTS_COLLIDE", "NO_FIXING_HOLE", "HARDWARE_BLOCKS_BEAM")
    assert refused.details["suggestion"]["action"] in ("ADJUST_COMPONENT_SPACING", "MOVE_COMPONENT_OFF_BEAM")
    # deterministic: the same budget gives the same refusal
    assert refusal_of(row_of_lenses(12, 21.0), cat, budget=ResolutionBudget(5)).details == refused.details


def test_an_injected_deadline_stops_the_search_and_the_kernel_reads_no_clock():
    calls = []

    def expired() -> bool:
        calls.append(1)
        return len(calls) > 3

    budget = ResolutionBudget(10_000, expired=expired)
    refused = refusal_of(row_of_lenses(12, 21.0), catalog(mounts=_many_mounts(40)), budget=budget)
    # polled when an evaluation starts and every DEADLINE_POLL_CHECKS checks inside one (a backtracking evaluation can
    # reach that): the fourth poll stops the search, wherever it is
    assert budget.exhausted_by == "DEADLINE" and len(calls) == 4 and 1 <= budget.layout_evaluations <= 3
    assert refused.details["search_budget_exhausted"] is True


def test_the_policy_bounds_every_resolution_and_a_shared_budget_bounds_several_passes():
    tight = catalog(mounts=_many_mounts(40), policy=ResolverPolicyV1(max_chain_placements=7))
    assert refusal_of(row_of_lenses(12, 21.0), tight).details["search_budget_exhausted"] is True
    budget = ResolutionBudget(40)
    resolve_assembly(reference_request(), catalog(), budget=budget)
    used = budget.layout_evaluations
    assert 1 <= used <= 40
    resolve_assembly(reference_request(), catalog(), budget=budget)  # a second pass spends the same budget
    assert budget.layout_evaluations == 2 * used
    with pytest.raises(ValueError):
        ResolutionBudget(0)


def test_a_resolvable_design_spends_little_of_the_budget():
    budget = ResolutionBudget(2000)
    r = resolve_assembly(row_of_lenses(4, 100.0), catalog(), budget=budget)
    assert len(r.chains) == 4 and budget.layout_evaluations < 50


# ------------------------------------------------------------------------------------------------ refusal contract


def _filter(instance="F1", position=(0.0, 0.0, 0.0)):
    """A plane filter: moving it along its beam changes no optical spacing (it is in no linked group)."""

    return lens(instance, position=position, mechanics=ND_FILTER)


def test_colliding_supports_name_both_components_and_the_spacing_needed():
    refused = refusal_of(AssemblyResolutionRequestV2(optics=(_filter("L1"), _filter("L2", position=(8.0, 0.0, 0.0))), externals=(source(),)))
    d = refused.details
    assert (refused.code, d["instance_id"], d["reason"], d["with"]) == ("MECHANICAL_LAYOUT_CONFLICT", "L2", "SUPPORTS_COLLIDE", "L1")
    s = d["suggestion"]
    assert s["action"] == "ADJUST_COMPONENT_SPACING" and s["min_extra_spacing_mm"] > 0
    assert s["beam_direction_design"] == [1.0, 0.0, 0.0] and s["instance_id"] == "L2"
    # the nearest position along the beam is a real fix for this pair
    moved = AssemblyResolutionRequestV2(optics=(_filter("L1"), _filter("L2", position=tuple(s["suggested_position_design_mm"]))),
                                        externals=(source(),))
    assert len(resolve_assembly(moved, catalog()).chains) == 2


def test_colliding_lenses_are_a_linked_pair_given_the_spacing_they_need():
    """two lenses 8 mm apart are a lens pair; moving one of them alone changes the pair's optics, so
    no move of one lens is suggested (no position, no extra spacing for it). The refusal states the least spacing the
    two supports need, as a linked pair, and that spacing is enough."""

    refused = refusal_of(AssemblyResolutionRequestV2(optics=(lens("L1"), lens("L2", position=(8.0, 0.0, 0.0))), externals=(source(),)))
    d = refused.details
    assert (d["instance_id"], d["reason"], d["with"]) == ("L2", "SUPPORTS_COLLIDE", "L1")
    s = d["suggestion"]
    for key in ("suggested_position_design_mm", "suggested_positions_design_mm", "min_extra_spacing_mm", "moved_with"):
        assert key not in s, key
    (pair,) = s["conflicting_pairs"]
    assert (pair["instance_id"], pair["with"], pair["linked"], pair["spacing_mm"]) == ("L2", "L1", True, 8.0)
    assert s["min_support_pitch_mm"] == pair["min_support_pitch_mm"] > 8.0 and pair["beam_direction_design"] == [1.0, 0.0, 0.0]
    spaced = AssemblyResolutionRequestV2(optics=(lens("L1"), lens("L2", position=(pair["min_support_pitch_mm"], 0.0, 0.0))), externals=(source(),))
    assert len(resolve_assembly(spaced, catalog()).chains) == 2


def test_a_missing_fixing_hole_names_the_support_in_the_way_and_the_nearest_position():
    """20 mm apart, no fork of L1 leaves L2 a free hole (every fixation of both was tried); the suggested position is a
    real fix."""

    refused = refusal_of(AssemblyResolutionRequestV2(optics=(_filter("L1"), _filter("L2", position=(20.0, 0.0, 0.0))), externals=(source(),)))
    d = refused.details
    assert (d["instance_id"], d["reason"], d["with"]) == ("L2", "NO_FIXING_HOLE", "L1")
    s = d["suggestion"]
    assert s["action"] == "ADJUST_COMPONENT_SPACING" and s["nearest_fixable_offset_mm"] > 0
    assert s["larger_breadboard_may_help"] is False and s["relaxing_retained_dofs_may_help"] is False
    moved = AssemblyResolutionRequestV2(optics=(_filter("L1"), _filter("L2", position=tuple(s["suggested_position_design_mm"]))),
                                        externals=(source(),))
    assert len(resolve_assembly(moved, catalog()).chains) == 2


def test_hardware_in_another_beam_names_that_beam_and_the_lateral_clearance():
    request = AssemblyResolutionRequestV2(
        optics=(lens("L1", position=(0.0, 0.0, 0.0)), lens("L2", position=(-60.0, -4.0, 0.0), rotation=look((0.0, 1.0, 0.0)))),
        externals=(source("S1"),))
    d = refusal_of(request).details
    assert (d["instance_id"], d["reason"], d["with"]) == ("L2", "HARDWARE_BLOCKS_BEAM", "L1")
    s = d["suggestion"]
    assert s["action"] == "MOVE_COMPONENT_OFF_BEAM" and s["min_lateral_clearance_mm"] > 0
    assert s["free_direction_design"] in ([0.0, 1.0, 0.0], [0.0, -1.0, 0.0])


def test_unreachable_relative_heights_give_the_reachable_design_heights():
    request = AssemblyResolutionRequestV2(optics=(lens("L1"), lens("L2", position=(100.0, 0.0, 380.0))), externals=(source(),))
    refused = refusal_of(request)
    d = refused.details
    assert d["reason"] == "NO_COMMON_BEAM_HEIGHT" and d["suggestion"]["action"] == "ADJUST_COMPONENT_HEIGHT"
    lo, hi = d["suggestion"]["reachable_axis_height_mm"]
    assert lo < hi
    z_lo, z_hi = d["suggestion"]["reachable_design_z_mm"]
    assert z_lo < z_hi


def test_board_limits_and_unheld_optics_name_the_change():
    limit = refusal_of(reference_request(max_board_size_mm=(120.0, 120.0)))
    assert limit.details["suggestion"] == {"action": "ALLOW_LARGER_BREADBOARD", "smallest_fitting_board_mm": [150.0, 100.0]}
    odd = refusal_of(reference_request(), catalog(mounts=(KINEMATIC,)))  # a lens and only a mirror mount
    assert odd.code == "NO_COMPATIBLE_MOUNT" and odd.details["suggestion"]["action"] == "CHANGE_COMPONENT"
    assert odd.details["suggestion"]["near_misses"] == ["NO_CLEAR_APERTURE"]


def test_refusal_details_never_name_catalog_identities():
    for request, cat in (
        (AssemblyResolutionRequestV2(optics=(lens("L1"), lens("L2", position=(8.0, 0.0, 0.0))), externals=(source(),)), catalog()),
        (row_of_lenses(12, 21.0), catalog(mounts=_many_mounts(40))),
        (reference_request(max_board_size_mm=(120.0, 120.0)), catalog()),
        (reference_request(), catalog(mounts=(KINEMATIC,))),
    ):
        refused = refusal_of(request, cat)  # refusal_of asserts customer safety
        assert set(refused.details) <= {"instance_id", "reason", "with", "suggestion", "search_budget_exhausted", "search_bound", "optical_sku",
                                        "footprint_mm", "max_board_size_mm", "smallest_fitting_board_mm", "standard_board_sizes_mm",
                                        "largest_standard_board_mm"}


# ------------------------------------------------------------------------------------------------ retained DOFs


WITH_TRANSMISSIVE_TIP_TILT = (FIXED, FIXED_ALT, XY, KINEMATIC, KINEMATIC_T, ROTATION)


def _dichroic(dofs, normal=(-SQRT_HALF, SQRT_HALF, 0.0)) -> OpticRequest:
    return OpticRequest("DM1", DICHROIC.optical_sku, "splitter", (0.0, 0.0, 0.0), look(normal), tuple(DofRequest(a, "MANUAL") for a in dofs), DICHROIC)


@pytest.mark.parametrize(("optic", "axes", "mount_key"), [
    ("lens", ("Tx", "Ty"), "OS-MECH-TEST-MOUNT-XY25"),
    ("lens", ("Rz",), "OS-MECH-TEST-MOUNT-ROT25"),
    ("lens", ("Rx", "Ry"), "OS-MECH-TEST-MOUNT-KIN25T"),
    ("mirror", ("Rx", "Ry"), "OS-MECH-TEST-MOUNT-KIN25"),
    ("mirror", ("Tx", "Ty"), "OS-MECH-TEST-MOUNT-XY25"),
    ("mirror", ("Rz",), "OS-MECH-TEST-MOUNT-ROT25"),
    ("dichroic", ("Rx", "Ry"), "OS-MECH-TEST-MOUNT-KIN25T"),
    ("lens", (), "OS-MECH-TEST-MOUNT-FIXED25"),
])
def test_requested_retained_dofs_select_the_hardware_that_delivers_them(optic, axes, mount_key):
    dofs = tuple((a, "MANUAL") for a in axes)
    if optic == "lens":
        request = reference_request(dofs=dofs)
        instance = "L1"
    elif optic == "mirror":
        request = AssemblyResolutionRequestV2(optics=(mirror(dofs=dofs),), externals=(source(),))
        instance = "M1"
    else:
        request = AssemblyResolutionRequestV2(optics=(_dichroic(axes),), externals=(source(),))
        instance = "DM1"
    r = resolve_assembly(request, catalog(mounts=WITH_TRANSMISSIVE_TIP_TILT))
    assert chain(r, instance).mount == mount_key
    for axis in axes:
        assert dof(r, instance, axis).status == "DELIVERED", axis


def test_without_compatible_tip_tilt_hardware_a_lens_falls_back_and_says_why():
    r = resolve_assembly(reference_request(dofs=(("Rx", "MANUAL"), ("Ry", "MANUAL"))), catalog())
    rx = dof(r, "L1", "Rx")
    assert (rx.status, rx.not_delivered_reason) == ("NOT_DELIVERED", "NO_COMPATIBLE_HARDWARE")
    assert dof(r, "L1", "Ry").not_delivered_reason is None


def test_axial_translation_is_delivered_by_the_slot_along_the_beam():
    r = resolve_assembly(reference_request(dofs=(("Tz", "MANUAL"),)), catalog())
    tz = dof(r, "L1", "Tz")
    assert (tz.status, tz.mechanism) == ("DELIVERED", "BASE_SLOT_TRAVEL")


def test_a_layout_forced_fallback_from_requested_hardware_is_reported():
    """L1 asks for tip/tilt; its tip/tilt mount leaves L2 (a filter 30 mm downstream) no fixing hole. The kernel gives L1 the
    plain mount instead of refusing the design, and reports the fallback and what forced it."""

    request = AssemblyResolutionRequestV2(optics=(lens("L1", dofs=(("Rx", "MANUAL"), ("Ry", "MANUAL"))), _filter("L2", position=(30.0, 0.0, 0.0))),
                                          externals=(source(),))
    with pytest.raises(AssemblyResolutionRefusal):
        resolve_assembly(request, catalog(mounts=(FIXED,)))  # without the alternative this spacing does not resolve
    r = resolve_assembly(request, catalog(mounts=(FIXED, KINEMATIC_T)))
    assert chain(r, "L1").mount == "OS-MECH-TEST-MOUNT-FIXED25"
    rx = dof(r, "L1", "Rx")
    assert (rx.status, rx.not_delivered_reason) == ("NOT_DELIVERED", "HARDWARE_DID_NOT_FIT")
    (cause,) = chain(r, "L1").fallback_causes
    assert (cause["reason"], cause["with"]) == ("NO_FIXING_HOLE", "L2")
    # : the cause says which component to move, and where, to keep the requested hardware
    move = cause["suggestion"]
    assert move["instance_id"] == "L2" and move["beam_direction_design"] == [1.0, 0.0, 0.0]
    moved = AssemblyResolutionRequestV2(optics=(request.optics[0], _filter("L2", position=tuple(move["suggested_position_design_mm"]))),
                                        externals=(source(),))
    assert chain(resolve_assembly(moved, catalog(mounts=(FIXED, KINEMATIC_T))), "L1").mount == "OS-MECH-TEST-MOUNT-KIN25T"
    # with room, the requested hardware is used
    roomy = AssemblyResolutionRequestV2(optics=(lens("L1", dofs=(("Rx", "MANUAL"), ("Ry", "MANUAL"))), _filter("L2", position=(70.0, 0.0, 0.0))),
                                        externals=(source(),))
    assert chain(resolve_assembly(roomy, catalog(mounts=(FIXED, KINEMATIC_T))), "L1").mount == "OS-MECH-TEST-MOUNT-KIN25T"


def test_a_reflector_prefers_a_mirror_mount_to_an_equally_adjustable_lens_mount():
    """A transmissive tip/tilt mount delivers a mirror's Rx / Ry as well as a mirror mount does,
    but its clear aperture is a capability the mirror does not need: the mirror mount comes first; a lens, which needs
    the aperture, keeps the transmissive one."""

    lens_mount = KINEMATIC_T
    mounts = catalog(mounts=(FIXED, KINEMATIC, lens_mount))
    mirror_request = AssemblyResolutionRequestV2(optics=(mirror(dofs=(("Rx", "MANUAL"), ("Ry", "MANUAL"))),), externals=(source(),))
    r = resolve_assembly(mirror_request, mounts)
    ranking = [m["mount"] for m in chain(r, "M1").mount_ranking]
    assert ranking[:2] == ["OS-MECH-TEST-MOUNT-KIN25", "OS-MECH-TEST-MOUNT-KIN25T"] and chain(r, "M1").mount == "OS-MECH-TEST-MOUNT-KIN25"
    assert {dof(r, "M1", a).status for a in ("Rx", "Ry")} == {"DELIVERED"}
    # without a mirror mount the transmissive one serves the mirror
    assert chain(resolve_assembly(mirror_request, catalog(mounts=(FIXED, lens_mount))), "M1").mount == "OS-MECH-TEST-MOUNT-KIN25T"
    lens_request = reference_request(dofs=(("Rx", "MANUAL"), ("Ry", "MANUAL")))
    assert chain(resolve_assembly(lens_request, mounts), "L1").mount == "OS-MECH-TEST-MOUNT-KIN25T"


def test_between_equal_transmissive_tip_tilt_mounts_the_smaller_one_is_chosen():
    """two transmissive tip/tilt mounts equal in what they deliver, and their evidence: the smaller
    envelope wins, not the earlier identity; the order of every other mount kind is untouched."""

    bulky = renamed(KINEMATIC_T, "OS-MECH-TEST-MOUNT-AKIN25T", envelope=BoxV1(min_mm=(-26.0, -45.0, 0.0), max_mm=(25.0, 45.0, 77.0)))
    request = reference_request(dofs=(("Rx", "MANUAL"), ("Ry", "MANUAL")))
    r = resolve_assembly(request, catalog(mounts=(FIXED, bulky, KINEMATIC_T)))
    ranking = [m["mount"] for m in chain(r, "L1").mount_ranking]
    assert ranking.index("OS-MECH-TEST-MOUNT-KIN25T") < ranking.index("OS-MECH-TEST-MOUNT-AKIN25T") and chain(r, "L1").mount == "OS-MECH-TEST-MOUNT-KIN25T"
    # mirror mounts keep the part-key order between equals
    big_mirror = renamed(KINEMATIC, "OS-MECH-TEST-MOUNT-AKIN25", envelope=BoxV1(min_mm=(-30.0, -45.0, 0.0), max_mm=(3.0, 45.0, 77.0)))
    mirror_request = AssemblyResolutionRequestV2(optics=(mirror(dofs=(("Rx", "MANUAL"), ("Ry", "MANUAL"))),), externals=(source(),))
    ranking = [m["mount"] for m in chain(resolve_assembly(mirror_request, catalog(mounts=(FIXED, KINEMATIC, big_mirror))), "M1").mount_ranking]
    assert ranking.index("OS-MECH-TEST-MOUNT-AKIN25") < ranking.index("OS-MECH-TEST-MOUNT-KIN25")


# ------------------------------------------------------------------------------------------------ oblique incidence


def test_a_45_degree_dichroic_never_gets_a_mount_whose_bore_clips_the_beam():
    """A rotation mount with a 44 mm bore is fine on axis but clips a beam crossing it at 45 degrees: with another
    compatible mount it is rejected (the requested Rz then falls back, reported); alone, a clear refusal names the
    largest incidence it passes."""

    request = AssemblyResolutionRequestV2(optics=(_dichroic(("Rz",)),), externals=(source(),))
    r = resolve_assembly(request, catalog(mounts=(FIXED, ROTATION_DEEP)))
    assert chain(r, "DM1").mount == "OS-MECH-TEST-MOUNT-FIXED25"
    assert any(m["mount"] == "OS-MECH-TEST-MOUNT-ROT25D" and m["reason"] == "BEAM_CLIPPED_AT_INCIDENCE" for m in chain(r, "DM1").rejected_mounts)
    (deep,) = [m for m in chain(r, "DM1").rejected_mounts if m["mount"] == "OS-MECH-TEST-MOUNT-ROT25D"]
    assert deep["assumed_mount_facts"] == ["clear_aperture_mm", "retainer_depth_mm", "rear_depth_mm"] and deep["clearance_mm"] < 0
    assert dof(r, "DM1", "Rz").not_delivered_reason == "NO_COMPATIBLE_HARDWARE"
    # a short-bore rotation mount passes the 45 degree beam and delivers Rz
    assert chain(resolve_assembly(request, catalog(mounts=(FIXED, ROTATION))), "DM1").mount == "OS-MECH-TEST-MOUNT-ROT25"
    refused = refusal_of(request, catalog(mounts=(ROTATION_DEEP,)))
    assert refused.code == "NO_COMPATIBLE_MOUNT"
    s = refused.details["suggestion"]
    assert refused.details["reason"] == "MOUNT_CLIPS_OBLIQUE_BEAM" and s["action"] == "REDUCE_TILT"
    assert s["incidence_deg"] == 45.0 and 0.0 < s["max_incidence_deg"] < 45.0


def test_a_clipping_judgment_states_its_basis():
    """The basis of a BEAM_CLIPPED_AT_INCIDENCE judgment: the computed clearance and the bore facts that were stand-ins.
    ROTATION_DEEP records no aperture and no bore depths (the envelope stands in): the refusal's basis is ASSUMED. A
    mount with the same long bore recorded (aperture and depths) is judged on recorded data: RECORDED. The outcome and
    the clearance number are the ones computed before (the basis only adds fields)."""

    policy = ResolverPolicyV1()
    request = AssemblyResolutionRequestV2(optics=(_dichroic(("Rz",)),), externals=(source(),))
    oblique = Incidence(math.cos(math.radians(45.0)), 0.0)
    refused = refusal_of(request, catalog(mounts=(ROTATION_DEEP,)))
    assert refused.details["clearance_basis"] == "ASSUMED"
    assert "established" in refused.message
    (entry,) = [r for r in refused.internal["rejected"] if r["reason"] == "BEAM_CLIPPED_AT_INCIDENCE"]
    assert entry["assumed_mount_facts"] == ["clear_aperture_mm", "retainer_depth_mm", "rear_depth_mm"]
    assert entry["clearance_mm"] == round(oblique_clearance(_dichroic(()), ROTATION_DEEP, oblique, policy), 3) < 0
    assert kernel.oblique_basis(_dichroic(()), ROTATION_DEEP) == ("clear_aperture_mm", "retainer_depth_mm", "rear_depth_mm")
    # a reflector reads no rear wall
    assert kernel.oblique_basis(mirror(), ROTATION_DEEP) == ("clear_aperture_mm", "retainer_depth_mm")

    recorded = mount("ROT25R", "ROTATION", centre=35.0, envelope=((-22.0, -24.7, 0.0), (22.0, 24.7, 59.7)), tmax=12.0,
                     mechanisms=ROTATION_DEEP.mechanisms, clear_aperture=22.0, retainer_depth=20.0, rear_depth=20.0)
    assert kernel.oblique_basis(_dichroic(()), recorded) == ()
    refused = refusal_of(request, catalog(mounts=(recorded,)))
    assert refused.details["clearance_basis"] == "RECORDED"
    assert refused.message == "every mount that holds this optic would clip the beam at its angle of incidence"
    (entry,) = [r for r in refused.internal["rejected"] if r["reason"] == "BEAM_CLIPPED_AT_INCIDENCE"]
    assert entry["assumed_mount_facts"] == [] and entry["clearance_mm"] < 0
    # one clipping mount judged on stand-ins makes the whole refusal ASSUMED
    assert refusal_of(request, catalog(mounts=(recorded, ROTATION_DEEP))).details["clearance_basis"] == "ASSUMED"


def test_the_glass_index_of_the_oblique_check_is_policy():
    """The index the oblique check assumes for the glass is ``ResolverPolicyV1.oblique_glass_index`` (default 1.4, the
    value the check always used). Inside the glass a ray drifts by ``t tan(asin(sin(theta) / n))``: a higher index
    drifts less and leaves more clearance, by exactly the difference of the two drifts through the thickest glass side."""

    oblique = Incidence(math.cos(math.radians(45.0)), 0.0)
    optic = _dichroic(())
    default = oblique_clearance(optic, ROTATION_DEEP, oblique, ResolverPolicyV1())
    assert ResolverPolicyV1().oblique_glass_index == 1.4
    assert oblique_clearance(optic, ROTATION_DEEP, oblique, ResolverPolicyV1(oblique_glass_index=1.4)) == default
    denser = oblique_clearance(optic, ROTATION_DEEP, oblique, ResolverPolicyV1(oblique_glass_index=1.8))
    s = math.sin(math.radians(45.0))
    drift = lambda n, t: t * math.tan(math.asin(s / n))  # noqa: E731
    (front_glass, _), (rear_glass, _) = kernel._aperture_depths(optic, ROTATION_DEEP)
    # the worst side is the same at both indices here: the side with the most glass and bore
    candidates = [denser - default - (drift(1.4, g) - drift(1.8, g)) for g in (front_glass, rear_glass)]
    assert min(abs(c) for c in candidates) < 1e-9 and denser > default


def test_oblique_clearance_follows_the_tilted_bore_geometry():
    policy = ResolverPolicyV1()
    request = _dichroic(())
    on_axis, oblique = Incidence(1.0, 0.0), Incidence(math.cos(math.radians(45.0)), 0.0)
    # recorded aperture data are used instead of the conservative fallback
    recorded = mount("REC", "ROTATION", centre=35.0, envelope=((-22.0, -24.7, 0.0), (22.0, 24.7, 59.7)), tmax=12.0,
                     mechanisms=ROTATION_DEEP.mechanisms, clear_aperture=24.0, retainer_depth=2.0, rear_depth=2.0)
    assert oblique_clearance(request, ROTATION_DEEP, on_axis, policy) > 0 > oblique_clearance(request, ROTATION_DEEP, oblique, policy)
    assert oblique_clearance(request, recorded, oblique, policy) > 0
    assert mount_incompatibility(request, recorded, catalog().posts, incidence=oblique, policy=policy) is None
    assert mount_incompatibility(request, ROTATION_DEEP, catalog().posts, incidence=oblique, policy=policy) == "BEAM_CLIPPED_AT_INCIDENCE"
    # a mirror in a kinematic mount (face flush with the mount front) is clear at 45 degrees
    assert oblique_clearance(mirror(), KINEMATIC, oblique, policy) > 0


# ------------------------------------------------------------------------------------------------ plate tilt


@pytest.mark.parametrize("tilt", [3.0, 4.0, 5.0, -5.0])
def test_a_slightly_tilted_nd_filter_stays_on_the_beam(tilt):
    """An ND filter yawed a few degrees against back-reflection is on the beam path: its own mount is not reported as
    hardware in the beam."""

    nd = lens("ND", position=(-60.0, 0.0, 0.0), rotation=yawed(tilt), mechanics=ND_FILTER)
    request = AssemblyResolutionRequestV2(optics=(nd, lens("L1")), externals=(source(),), observations=(screen(),))
    r = resolve_assembly(request, catalog())
    assert r.beam_paths[0].through == ("SRC", "ND", "L1", "focal_plane")
    assert {c.instance_id for c in r.chains} == {"ND", "L1"}


def test_tilt_beyond_the_policy_bound_is_refused_as_tilt_and_the_default_bound_is_30_degrees():
    """By default a filter or a lens the beam crosses within 30 degrees of its axis is on the beam path (its mount is then
    judged at that incidence by its bore). A deployment that sets tighter bounds in the policy refuses a tilt beyond them
    as an orientation, naming the bound."""

    for optic in (lens("ND", rotation=yawed(15.0), mechanics=ND_FILTER), lens("L1", rotation=yawed(4.0))):
        request = AssemblyResolutionRequestV2(optics=(optic,), externals=(source(),), observations=(screen(),))
        r = resolve_assembly(request, catalog())
        assert r.beam_paths[0].through == ("SRC", optic.instance_id, "focal_plane")
    tighter = catalog(policy=ResolverPolicyV1(max_plate_tilt_deg=10.0, max_lens_tilt_deg=2.6))
    for optic, limit in ((lens("ND", rotation=yawed(15.0), mechanics=ND_FILTER), 10.0), (lens("L1", rotation=yawed(4.0)), 2.6)):
        refused = refusal_of(AssemblyResolutionRequestV2(optics=(optic,), externals=(source(),), observations=(screen(),)), tighter)
        assert refused.code == "UNSUPPORTED_OPTIC_ORIENTATION"
        s = refused.details["suggestion"]
        assert (refused.details["reason"], s["action"], s["max_incidence_deg"]) == ("OPTIC_TILT_NOT_SUPPORTED", "REDUCE_TILT", limit)
    assert (ResolverPolicyV1().max_plate_tilt_deg, ResolverPolicyV1().max_lens_tilt_deg) == (30.0, 30.0)
    # the bound is policy: a stricter policy refuses the 5 degree filter
    strict = catalog(policy=ResolverPolicyV1(max_plate_tilt_deg=2.0))
    nd5 = AssemblyResolutionRequestV2(optics=(lens("ND", rotation=yawed(5.0), mechanics=ND_FILTER),), externals=(source(),),
                                      observations=(screen(),))
    assert refusal_of(nd5, strict).details["suggestion"]["max_incidence_deg"] == 2.0
    assert LENS.category == "LENS"


@pytest.mark.parametrize("make", [lambda t: lens("ND", rotation=yawed(t), mechanics=ND_FILTER), lambda t: lens("L1", rotation=yawed(t))],
                         ids=["filter", "lens"])
def test_a_tilted_transmissive_optic_is_judged_by_its_mounts_bore(make):
    """A 20 degree filter and a 10 degree lens resolve with the optic on the path. A mount whose recorded bore clips the
    beam at 28 degrees is refused with the largest incidence it passes (a per-mount, computed limit), and the same design
    at that incidence resolves in it. Beyond 30 degrees the optic is still hardware in the beam, not a tilt."""

    for tilt in (20.0, 10.0):
        request = AssemblyResolutionRequestV2(optics=(make(tilt),), externals=(source(),), observations=(screen(),))
        r = resolve_assembly(request, catalog())
        assert r.beam_paths[0].through[1] == request.optics[0].instance_id and len(r.chains) == 1
    deep = mount("ROT25R", "ROTATION", centre=35.0, envelope=((-22.0, -24.7, 0.0), (22.0, 24.7, 59.7)), tmax=12.0,
                 mechanisms=ROTATION_DEEP.mechanisms, clear_aperture=22.0, retainer_depth=20.0, rear_depth=20.0)
    request = AssemblyResolutionRequestV2(optics=(make(28.0),), externals=(source(),), observations=(screen(),))
    refused = refusal_of(request, catalog(mounts=(deep,)))
    s = refused.details["suggestion"]
    assert (refused.code, refused.details["reason"], refused.details["clearance_basis"]) == ("NO_COMPATIBLE_MOUNT", "MOUNT_CLIPS_OBLIQUE_BEAM", "RECORDED")
    assert s["incidence_deg"] == 28.0 and 20.0 < s["max_incidence_deg"] < 28.0
    at_limit = AssemblyResolutionRequestV2(optics=(make(s["max_incidence_deg"]),), externals=(source(),), observations=(screen(),))
    assert chain(resolve_assembly(at_limit, catalog(mounts=(deep,))), request.optics[0].instance_id).mount == "OS-MECH-TEST-MOUNT-ROT25R"
    broadside = AssemblyResolutionRequestV2(optics=(make(35.0),), externals=(source(),), observations=(screen(),))
    refused = refusal_of(broadside)
    assert (refused.code, refused.details["reason"]) == ("MECHANICAL_LAYOUT_CONFLICT", "HARDWARE_BLOCKS_BEAM")


def test_a_mount_that_passes_the_oblique_beam_only_on_assumed_aperture_data_is_deprioritized_and_disclosed():
    """A Ø12.7 dichroic at 45 deg: a ring that seats the plate at its front face leaves 3.7 mm of bore wall behind it,
    which the transmitted beam passes only if the (unrecorded) clear aperture is near the optic size; a ring that holds
    the plate in its middle passes it on the conservative assumption. The middle-seated ring is chosen; alone, the
    front-seated ring is still used (the centre line clears the optic-sized bore) with the assumption disclosed."""

    small = optic("OS-OPT-TEST-FLT-DICHROIC-D12", "BEAMSPLITTER_PLATE", size=12.7, thickness=3.2, axial=(-3.2, 0.0))
    front_seated = mount("RING12F", "FIXED", size=12.7, centre=15.0, envelope=((-3.8, -8.9, 0.0), (3.8, 8.9, 23.9)), tmax=5.2, seat=3.1)
    mid_seated = mount("RING12M", "FIXED", size=12.7, centre=22.0, envelope=((-3.8, -8.9, 0.0), (3.8, 8.9, 44.0)), tmax=4.2)
    dm = OpticRequest("DM1", small.optical_sku, "splitter", (0.0, 0.0, 0.0), look((-SQRT_HALF, SQRT_HALF, 0.0)), (), small)
    request = AssemblyResolutionRequestV2(optics=(dm,), externals=(source(),))
    both = resolve_assembly(request, catalog(mounts=(front_seated, mid_seated)))
    assert chain(both, "DM1").mount == "OS-MECH-TEST-MOUNT-RING12M" and "c1.mount.oblique_beam_clearance" not in both.assumptions
    assert {"mount": "OS-MECH-TEST-MOUNT-RING12F", "reason": "OBLIQUE_CLEARANCE_UNVERIFIED"} in chain(both, "DM1").rejected_mounts
    alone = resolve_assembly(request, catalog(mounts=(front_seated,)))
    assert chain(alone, "DM1").mount == "OS-MECH-TEST-MOUNT-RING12F"
    assert "c1.mount.oblique_beam_clearance" in alone.assumptions


def test_a_layout_fallback_inside_a_linked_lens_pair_carries_the_pairs_pitch():
    """(strict mode): L1's tip/tilt mount does not fit 25 mm before L2, and the two lenses are a linked pair,
    so no move of one lens keeps it. The fallback's cause -- what a RETAINED_DOF_UNSATISFIED refusal lifts -- used to
    carry only the beam direction; it now names the linked pair with the spacing at which the design resolves with the
    requested hardware kept (confirmed), and a design at that spacing gets the tip/tilt mount back."""

    tip_tilt = (("Rx", "MANUAL"), ("Ry", "MANUAL"))

    def request(gap):
        return AssemblyResolutionRequestV2(optics=(lens("L1", dofs=tip_tilt), lens("L2", position=(gap, 0.0, 0.0))), externals=(source(),),
                                           observations=(screen(position=(gap + 80.0, 0.0, 0.0)),))

    # a refusal of the requested DOFs diagnoses in full the fallbacks the realization keeps (a resolution itself keeps
    # them to a single move, to stay fast)
    pending: dict = {}
    r = resolve_assembly(request(25.0), catalog(mounts=(FIXED, KINEMATIC_T)), pending_fallbacks=pending)
    diagnose_kept_fallbacks(r, pending)
    (cause,) = chain(r, "L1").fallback_causes
    move = cause["suggestion"]
    assert "suggested_position_design_mm" not in move
    (pair,) = move["conflicting_pairs"]
    assert (pair["instance_id"], pair["with"], pair["linked"], pair["spacing_mm"]) == ("L2", "L1", True, 25.0)
    assert move["min_support_pitch_mm"] == pair["min_support_pitch_mm"] > 25.0 and move["pairs_confirmed"] is True
    roomy = resolve_assembly(request(pair["min_support_pitch_mm"]), catalog(mounts=(FIXED, KINEMATIC_T)))
    assert chain(roomy, "L1").mount == "OS-MECH-TEST-MOUNT-KIN25T"
