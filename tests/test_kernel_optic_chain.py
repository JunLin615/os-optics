"""``resolve_optic_chain``: one optic's support chain at a beam height, without a layout.

* Over a sweep of the synthetic catalog -- every test optic, beam heights 40-300 mm and retained DOF sets -- the chain
  is exactly the one the full kernel resolves for the optic alone (one-optic design, a source on its axis, the same
  requested height): mount, post, holder, insertion depth and delivered DOFs; where the full kernel refuses for want of
  a mount the chain function refuses the same way; where the full kernel refuses the requested height because its
  best-ranked mount cannot reach it, the chain function either refuses the same way or holds the optic on a
  lower-ranked mount that does (``fell_back``).
* Without a height, the chain is the full kernel's own choice (the preferred 125 mm when reachable).
* A lower-ranked mount is taken when the best-ranked one cannot reach the height.
* Refusals: no mount holds the optic, an unreachable or out-of-policy height (with the reachable range), and the
  oblique incidence rule.
"""

from __future__ import annotations

import json

import pytest

from os_optics.assembly_plan.kernel import (
    OPTIC_CHAIN_ROTATION,
    AssemblyResolutionRefusal,
    AssemblyResolutionRequestV2,
    ChainPins,
    DofRequest,
    ExternalRequest,
    OpticRequest,
    optic_incidences,
    resolve_assembly,
    resolve_optic_chain,
)

from kernel_fixture import (DICHROIC, FIXED, FIXED_50, FIXED_ALT, KINEMATIC, KINEMATIC_T, LENS, LENS_50, MIRROR, ND_FILTER, ODD_OPTIC,
                            ROTATION, XY, catalog as base_catalog)

OPTICS = (LENS, MIRROR, LENS_50, ND_FILTER, DICHROIC, ODD_OPTIC)
DOF_SETS = {"MIRROR": [(), ("Rx", "Ry")], "BEAMSPLITTER_PLATE": [(), ("Rx", "Ry")], "LENS": [(), ("Tx", "Ty"), ("Rz",)],
            "FILTER": [(), ("Tz",)]}
HEIGHTS = (40.0, 75.0, 125.0, 200.0, 300.0)


def catalog():
    return base_catalog(mounts=(FIXED, FIXED_ALT, XY, KINEMATIC, ROTATION, FIXED_50, KINEMATIC_T))


def request(mechanics, dofs: tuple[str, ...] = (), *, controls: str = "MANUAL", pins=None) -> OpticRequest:
    return OpticRequest("L1", mechanics.optical_sku, "part_list", (0.0, 0.0, 0.0), OPTIC_CHAIN_ROTATION,
                        tuple(DofRequest(axis, controls) for axis in dofs), mechanics, pins=pins)


def full_kernel(optic: OpticRequest, height: float | None):
    """The full kernel on a one-optic design: the optic at the origin, a source 200 mm before it on its axis."""

    design = AssemblyResolutionRequestV2(
        optics=(optic,), externals=(ExternalRequest("S", "source", (-200.0, 0.0, 0.0), OPTIC_CHAIN_ROTATION, ()),),
        beam_height_mm=height)
    return resolve_assembly(design, catalog())


def outcome(call):
    try:
        return call()
    except AssemblyResolutionRefusal as refusal:
        return refusal


def delivered(dofs) -> list[tuple[str, str, str]]:
    return sorted((d.axis, d.status, d.mechanism) for d in dofs if d.status != "NOT_DELIVERED")


def sweep_cases():
    for mechanics in OPTICS:
        for dofs in DOF_SETS.get(mechanics.category, [()]):
            for height in HEIGHTS:
                yield mechanics, height, dofs


def test_sweep_matches_the_full_kernel_for_one_optic():
    counts = {"same_chain": 0, "same_refusal": 0, "fell_back": 0}
    for mechanics, height, dofs in sweep_cases():
        optic = request(mechanics, dofs)
        kernel = outcome(lambda: full_kernel(optic, height))
        mine = outcome(lambda: resolve_optic_chain(optic, catalog(), beam_height_mm=height))
        sku = mechanics.optical_sku
        if isinstance(kernel, AssemblyResolutionRefusal):
            if kernel.code == "REQUESTED_BEAM_HEIGHT_UNREACHABLE" and not isinstance(mine, AssemblyResolutionRefusal):
                assert mine.fell_back, (sku, height)
                assert mine.beam_height_mm == height
                counts["fell_back"] += 1
                continue
            assert isinstance(mine, AssemblyResolutionRefusal), (sku, height, kernel.code)
            assert mine.code == kernel.code, (sku, height, mine.code, kernel.code)
            counts["same_refusal"] += 1
            continue
        assert not isinstance(mine, AssemblyResolutionRefusal), (sku, height, mine.code, mine.details)
        chain = kernel.chains[0]
        assert (mine.mount.identity, mine.post.identity, mine.holder.identity) == (chain.mount, chain.post, chain.holder), sku
        assert mine.insertion_depth_mm == chain.insertion_depth_mm
        assert mine.beam_height_mm == chain.beam_height_mm == height
        assert not mine.fell_back
        assert delivered(mine.dofs) == delivered(kernel.dofs), sku
        counts["same_chain"] += 1
    assert counts["same_chain"] >= 30 and counts["same_refusal"] >= 5, counts


def test_without_a_height_the_chain_is_the_full_kernels_own_choice():
    for mechanics, _height, dofs in list(sweep_cases())[::3]:
        optic = request(mechanics, dofs)
        kernel = outcome(lambda: full_kernel(optic, None))
        mine = outcome(lambda: resolve_optic_chain(optic, catalog()))
        if isinstance(kernel, AssemblyResolutionRefusal):
            assert isinstance(mine, AssemblyResolutionRefusal) and mine.code == kernel.code, mechanics.optical_sku
            continue
        chain = kernel.chains[0]
        assert (mine.mount.identity, mine.post.identity, mine.holder.identity, mine.beam_height_mm) == (
            chain.mount, chain.post, chain.holder, chain.beam_height_mm), mechanics.optical_sku


def test_height_arithmetic_joints_and_dofs_of_a_kinematic_mirror_chain():
    chain = resolve_optic_chain(request(MIRROR, ("Rx", "Ry", "Ty")), catalog(), beam_height_mm=125.0, incidence_deg=45.0)
    assert chain.mount.mount_kind.value == "KINEMATIC"
    closure = chain.holder.height_mm + chain.post.length_mm + chain.mount.centre_height_mm - chain.insertion_depth_mm
    assert closure == pytest.approx(125.0)
    lo, hi = chain.insertion_interval_mm
    assert lo <= chain.insertion_depth_mm <= hi
    assert chain.height_travel_mm == pytest.approx((-(hi - chain.insertion_depth_mm), chain.insertion_depth_mm - lo))
    assert [j.joint for j in chain.joints] == ["OPTIC_IN_MOUNT", "MOUNT_ON_POST", "POST_IN_HOLDER", "HOLDER_ON_SURFACE"]
    assert chain.joints[0].interface_designation == "OD25.4-SEAT"
    by_axis = {d.axis: d for d in chain.dofs}
    assert by_axis["Rx"].mechanism == by_axis["Ry"].mechanism == "TIP_TILT_MOUNT"
    assert by_axis["Ty"].mechanism == "POST_HEIGHT_CLAMP" and by_axis["Ty"].travel == chain.height_travel_mm
    assert {c.kind: c.status for c in chain.checks}["LAYOUT"] == "NOT_RUN"
    assert chain.incidence_deg == 45.0 and chain.reachable_axis_height_mm[0] <= 125.0 <= chain.reachable_axis_height_mm[1]
    text = str((chain.joints, chain.checks))
    assert all(key not in text for key in (chain.mount.identity, chain.post.identity, chain.holder.identity))


def test_axial_slide_is_conditional_on_the_slot_along_the_beam():
    chain = resolve_optic_chain(request(ND_FILTER, ("Tz",)), catalog(), beam_height_mm=100.0)
    tz = next(d for d in chain.dofs if d.axis == "Tz")
    assert tz.mechanism == "BASE_SLOT_TRAVEL" and tz.status == "DELIVERED"
    assert chain.conditions == ("BASE_SLOT_ALONG_BEAM",)
    span = chain.holder.base.slot_end_mm - chain.holder.base.slot_start_mm
    assert tz.travel == pytest.approx((-span / 2, span / 2))


def test_a_lower_ranked_mount_is_tried_when_the_best_cannot_reach_the_height():
    """The fixed lens mount (best ranked without DOFs) stops below 325 mm; the rotation mount, taller, reaches it."""

    optic = request(LENS)
    kernel = outcome(lambda: full_kernel(optic, 325.0))
    assert isinstance(kernel, AssemblyResolutionRefusal) and kernel.code == "REQUESTED_BEAM_HEIGHT_UNREACHABLE"
    chain = resolve_optic_chain(optic, catalog(), beam_height_mm=325.0)
    top = resolve_optic_chain(optic, catalog(), beam_height_mm=125.0)
    assert chain.fell_back and chain.mount.identity != top.mount.identity
    assert chain.beam_height_mm == 325.0
    assert chain.trace["skipped"][0]["why"] == "NO_STACK_AT_BEAM_HEIGHT"


def test_refusals_are_structured_and_name_no_identity():
    with pytest.raises(AssemblyResolutionRefusal) as low:
        resolve_optic_chain(request(LENS), catalog(), beam_height_mm=10.0)
    assert low.value.code == "REQUESTED_BEAM_HEIGHT_UNREACHABLE"
    assert low.value.details["reason"] == "OUTSIDE_SUPPORTED_RANGE"
    assert low.value.details["supported_beam_height_mm"] == [25.0, 400.0]
    with pytest.raises(AssemblyResolutionRefusal) as unreachable:
        resolve_optic_chain(request(LENS), catalog(), beam_height_mm=30.0)
    assert unreachable.value.code == "REQUESTED_BEAM_HEIGHT_UNREACHABLE"
    assert unreachable.value.details["reason"] == "BEAM_HEIGHT_NOT_REACHABLE"
    lo, hi = unreachable.value.details["reachable_axis_height_mm"]
    assert 30.0 < lo < hi <= 400.0
    unheld = outcome(lambda: resolve_optic_chain(request(ODD_OPTIC), catalog()))
    assert isinstance(unheld, AssemblyResolutionRefusal) and unheld.code == "NO_COMPATIBLE_MOUNT"
    assert unheld.details["reason"] in ("OPTIC_NOT_HELD", "MOUNT_CLIPS_OBLIQUE_BEAM")
    for refusal in (low.value, unreachable.value, unheld):
        text = json.dumps(refusal.details)
        assert "OS-MECH-" not in text and "rp:" not in text


def test_incidence_restricts_the_mounts():
    normal = resolve_optic_chain(request(MIRROR, ("Rx", "Ry")), catalog(), beam_height_mm=125.0)
    folded = resolve_optic_chain(request(MIRROR, ("Rx", "Ry")), catalog(), beam_height_mm=125.0, incidence_deg=45.0)
    assert len(folded.trace["rejected_mounts"]) >= len(normal.trace["rejected_mounts"])
    with pytest.raises(ValueError):
        resolve_optic_chain(request(MIRROR), catalog(), incidence_deg=90.0)


def test_the_chain_is_deterministic_and_ignores_the_given_pose():
    a = resolve_optic_chain(request(LENS, ("Tx", "Ty")), catalog(), beam_height_mm=90.0)
    moved = OpticRequest("L1", LENS.optical_sku, "x", (5.0, 7.0, 9.0), (1.0, 0.0, 0.0, 0.0),
                         (DofRequest("Tx", "MANUAL"), DofRequest("Ty", "MANUAL")), LENS)
    b = resolve_optic_chain(moved, catalog(), beam_height_mm=90.0)
    assert a == b


def test_the_design_incidence_of_a_one_optic_design_is_along_its_axis():
    optic = request(LENS)
    design = AssemblyResolutionRequestV2(
        optics=(optic,), externals=(ExternalRequest("S", "source", (-200.0, 0.0, 0.0), OPTIC_CHAIN_ROTATION, ()),))
    found = optic_incidences(design, catalog().policy)
    assert found["L1"].degrees == pytest.approx(0.0, abs=1e-6) and found["L1"].offset_mm == pytest.approx(0.0, abs=1e-9)
    offset = resolve_optic_chain(optic, catalog(), beam_height_mm=125.0, incidence_deg=0.0, incidence_offset_mm=0.0)
    assert offset == resolve_optic_chain(optic, catalog(), beam_height_mm=125.0)
    with pytest.raises(ValueError):
        resolve_optic_chain(optic, catalog(), incidence_offset_mm=-1.0)


# ------------------------------------------------------------------------------------------------ pins (a plan's pinned hardware)


def test_pins_keep_a_plans_pinned_mount_post_and_holder():
    free = resolve_optic_chain(request(LENS), catalog(), beam_height_mm=125.0)
    pins = ChainPins(mount=XY.identity, post="OS-MECH-TEST-POST-D12P7-L75", holder="OS-MECH-TEST-HOLDER-L50")
    pinned = resolve_optic_chain(request(LENS, pins=pins), catalog(), beam_height_mm=125.0)
    assert free.mount.identity != XY.identity
    assert (pinned.mount.identity, pinned.post.identity, pinned.holder.identity) == (pins.mount, pins.post, pins.holder)
    closure = pinned.holder.height_mm + pinned.post.length_mm + pinned.mount.centre_height_mm - pinned.insertion_depth_mm
    assert closure == pytest.approx(125.0)


def test_a_pin_that_cannot_hold_the_optic_refuses_like_no_mount():
    refused = outcome(lambda: resolve_optic_chain(request(LENS, pins=ChainPins(mount=KINEMATIC.identity)), catalog(), beam_height_mm=125.0))
    assert isinstance(refused, AssemblyResolutionRefusal) and refused.code == "NO_COMPATIBLE_MOUNT"
    short = outcome(lambda: resolve_optic_chain(request(LENS, pins=ChainPins(post="OS-MECH-TEST-POST-D12P7-L25",
                                                                             holder="OS-MECH-TEST-HOLDER-L25")),
                                                catalog(), beam_height_mm=300.0))
    assert isinstance(short, AssemblyResolutionRefusal) and short.code == "REQUESTED_BEAM_HEIGHT_UNREACHABLE"


def test_pins_hold_in_the_full_kernel_too():
    pins = ChainPins(mount=XY.identity)
    design = AssemblyResolutionRequestV2(
        optics=(request(LENS, pins=pins),), externals=(ExternalRequest("S", "source", (-200.0, 0.0, 0.0), OPTIC_CHAIN_ROTATION, ()),))
    assert resolve_assembly(design, catalog()).chains[0].mount == XY.identity
