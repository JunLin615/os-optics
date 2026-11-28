"""adversarial "Agent walks" -- every refusal's suggestion applied literally, then resolved again.

A walk either resolves, or each refusal it meets names a conflicting pair it has not met before (strictly new
information), within a few steps; it never ends on a refusal that leaves nothing to apply. A suggested move never
splits a linked group (a lens and the next lens or observation plane on its beam keep their spacing); a linked pair
that is itself too tight is given the least support pitch it needs instead, which the walk applies as the redesign an
Agent would make.
"""

from __future__ import annotations

import math
import sys
from dataclasses import replace

import pytest

from os_optics.assembly_plan.kernel import (
    AssemblyResolutionRefusal,
    AssemblyResolutionRequestV2,
    ExternalRequest,
    OpticRequest,
    ResolverPolicyV1,
    curved_mirror_sku,
    resolve_assembly,
)

from os_optics.assembly_plan.kernel import resolve as kernel
from os_optics.assembly_plan.kernel.beam import trace_beam_paths

from kernel_fixture import DICHROIC, LENS, MIRROR, ND_FILTER, SQRT_HALF, catalog, lens, look, mirror, screen, source

BEAM_Y = look((0.0, 1.0, 0.0))
#: The fields a refusal's suggestion may carry; a walk step applies the first that is present, in this order.
MOVES = ("suggested_position_design_mm", "suggested_positions_design_mm", "conflicting_pairs", "min_extra_spacing_mm",
         "min_lateral_clearance_mm", "smallest_fitting_board_mm", "smallest_board_for_footprint_mm")


def _diagnosis_allowance(patch, checks: int) -> None:
    """Every catalog this module's helpers build carries the kernel policy's per-stage diagnosis allowance ``checks``."""

    base = catalog
    patch.setattr(sys.modules[__name__], "catalog",
                  lambda **kwargs: base(policy=ResolverPolicyV1(diagnosis_max_geometry_checks=checks), **kwargs))


def _positions(request: AssemblyResolutionRequestV2) -> dict[str, tuple[float, float, float]]:
    out = {o.instance_id: o.position_design for o in request.optics}
    out.update({e.instance_id: e.position_design for e in request.externals})
    out.update({o.observation_id: o.position_design for o in request.observations})
    return out


def _placed(request: AssemblyResolutionRequestV2, where: dict[str, tuple[float, float, float]]) -> AssemblyResolutionRequestV2:
    return replace(request,
                   optics=tuple(replace(o, position_design=tuple(where[o.instance_id])) for o in request.optics),
                   externals=tuple(replace(e, position_design=tuple(where[e.instance_id])) for e in request.externals),
                   observations=tuple(replace(o, position_design=tuple(where[o.observation_id])) for o in request.observations))


def _shifted(where, names, delta):
    return {n: (tuple(p[i] + delta[i] for i in range(3)) if n in names else p) for n, p in where.items()}


def _linked_spacings(request: AssemblyResolutionRequestV2) -> dict[tuple[str, str], float]:
    """The distance of each lens to the next lens or observation plane after it on the beam (design order along +X/+Y
    rows of these tests: the nearest such element ahead of it on its beam axis)."""

    where = _positions(request)
    lenses = [o for o in request.optics if o.mechanics.category == "LENS"]
    ends = {o.instance_id for o in request.optics if o.mechanics.category == "LENS"} | {o.observation_id for o in request.observations}
    out = {}
    for optic in lenses:
        p = optic.position_design
        ahead = []
        for name in ends - {optic.instance_id}:
            q = where[name]
            d = tuple(q[i] - p[i] for i in range(3))
            n = math.sqrt(sum(v * v for v in d))
            axis = tuple(round(v, 9) for v in _axis(optic))
            if n > 1e-9 and sum(d[i] / n * axis[i] for i in range(3)) > 1.0 - 1e-6:
                ahead.append((n, name))
        if ahead:
            n, name = min(ahead)
            out[(optic.instance_id, name)] = round(n, 6)
    return out


def _axis(optic: OpticRequest):
    from os_optics.assembly_plan.kernel.geometry import rotate

    return rotate(optic.rotation_design, (0.0, 0.0, 1.0))


def walk(request: AssemblyResolutionRequestV2, *, steps: int = 4) -> list[dict]:
    """Follow every refusal literally; the trace of refusals (the last entry ``{"resolved": ...}``)."""

    trace: list[dict] = []
    seen: set[frozenset] = set()
    fam = catalog()
    for _ in range(steps):
        try:
            realization = resolve_assembly(request, fam)
        except AssemblyResolutionRefusal as refusal:
            d = refusal.details
            s = d.get("suggestion") or {}
            pair = frozenset(x for x in (d.get("instance_id"), d.get("with")) if x)
            entry = {"code": refusal.code, "reason": d.get("reason"), "pair": pair, "suggestion": s}
            trace.append(entry)
            if pair:
                assert pair not in seen, f"the same conflicting pair came back: {trace}"
                seen.add(pair)
            request = _follow(request, d, s, trace)
            continue
        trace.append({"resolved": realization})
        return trace
    raise AssertionError(f"no resolution within {steps} steps: {trace}")


def _follow(request: AssemblyResolutionRequestV2, d: dict, s: dict, trace: list) -> AssemblyResolutionRequestV2:
    where = _positions(request)
    instance = s.get("instance_id") or d.get("instance_id")
    applied = next((key for key in MOVES if key in s), None)
    assert applied is not None, f"a refusal with nothing to apply: {trace}"
    before = _linked_spacings(request)
    if applied == "suggested_position_design_mm":
        target = s["suggested_position_design_mm"]
        delta = tuple(target[i] - where[instance][i] for i in range(3))
        moved = _shifted(where, {instance, *s.get("moved_with", ())}, delta)
    elif applied == "suggested_positions_design_mm":
        moved = {n: (tuple(s[applied][n]) if n in s[applied] else p) for n, p in where.items()}
    elif applied in ("min_extra_spacing_mm", "min_lateral_clearance_mm"):
        if applied == "min_extra_spacing_mm":
            direction = s["beam_direction_design"]
            other = where.get(d.get("with"))
            if other is not None and sum((where[instance][i] - other[i]) * direction[i] for i in range(3)) < 0:
                direction = [-v for v in direction]
        else:
            direction = s["free_direction_design"]
        delta = tuple(s[applied] * v for v in direction)
        moved = _shifted(where, {instance, *s.get("moved_with", ())}, delta)
    elif applied == "conflicting_pairs":
        # every pair at its least pitch: the later element moved along the pair's beam with what it names. A linked pair
        # cannot make that room without changing a linked group: the walk's stand-in for the redesign an Agent makes
        # (re-choosing its optics) moves the later element with everything after it on its beam
        paths = trace_beam_paths(request.externals, request.optics, request.observations)
        moved = where
        for pair in s["conflicting_pairs"]:
            u = pair["beam_direction_design"]
            delta = tuple(u[i] * (pair["min_support_pitch_mm"] - pair["spacing_mm"]) for i in range(3))
            names = kernel._downstream(pair["instance_id"], paths) if pair["linked"] else {pair["instance_id"], *pair.get("moved_with", ())}
            moved = _shifted(moved, names, delta)
            trace[-1]["redesign"] = trace[-1].get("redesign") or pair["linked"]
        if not trace[-1]["redesign"]:
            followed = _placed(request, moved)
            assert _linked_spacings(followed) == before, (applied, s)
            return followed
        return _placed(request, moved)
    else:
        return replace(request, max_board_size_mm=tuple(s[applied]))
    followed = _placed(request, moved)
    # a suggested move keeps every linked spacing
    assert _linked_spacings(followed) == before, (applied, s)
    return followed


def _filters(xs, *, beam_x=True):
    return tuple(lens(f"F{k + 1}", position=(x, 0.0, 0.0) if beam_x else (0.0, x, 0.0), mechanics=ND_FILTER,
                      rotation=look((1.0, 0.0, 0.0)) if beam_x else BEAM_Y) for k, x in enumerate(xs))


def _row(xs, *, plane=True) -> AssemblyResolutionRequestV2:
    return AssemblyResolutionRequestV2(optics=_filters(xs), externals=(source(),),
                                       observations=(screen(position=(xs[-1] + 80.0, 0.0, 0.0)),) if plane else ())


# ------------------------------------------------------------------------------------------------ transmissive lines


@pytest.mark.parametrize("xs", [
    (0.0, 20.0),  # a pair whose supports cannot stand at all
    (0.0, 8.0),  # supports colliding
    (0.0, 30.0, 52.5, 82.5),  # one filter hemmed in by its neighbours
    tuple(22.5 * k for k in range(6)),  # a row at one pitch: no single move works (the spread)
    tuple(20.0 * k for k in range(8)),
])
def test_a_line_of_transmissive_optics_converges(xs):
    trace = walk(_row(xs))
    assert "resolved" in trace[-1] and len(trace) <= 3


def test_a_lens_between_filters_moves_with_its_observation_plane():
    """A focusing lens in a tight line: a move of the lens, or of a filter after it, keeps the lens-to-plane spacing
    (asserted in every step of the walk)."""

    optics = (lens("F1", mechanics=ND_FILTER), lens("L1", position=(20.0, 0.0, 0.0), mechanics=LENS),
              lens("F2", position=(40.0, 0.0, 0.0), mechanics=ND_FILTER))
    request = AssemblyResolutionRequestV2(optics=optics, externals=(source(),), observations=(screen(position=(96.473, 0.0, 0.0)),))
    trace = walk(request)
    assert "resolved" in trace[-1]


# ------------------------------------------------------------------------------------------------ linked groups


@pytest.mark.parametrize("spacing", [8.0, 20.0])
def test_a_tight_lens_pair_is_never_split_and_is_given_its_pitch(spacing):
    """A lens pair (a telescope, a relay) too tight for its supports: no step moves one lens of it; the refusal names the
    pair as linked with the least support pitch, and the redesign at that pitch resolves."""

    request = AssemblyResolutionRequestV2(optics=(lens("L1"), lens("L2", position=(spacing, 0.0, 0.0))), externals=(source(),),
                                          observations=(screen(position=(spacing + 80.0, 0.0, 0.0)),))
    trace = walk(request)
    first = trace[0]["suggestion"]
    assert first["conflicting_pairs"][0]["linked"] is True and "suggested_position_design_mm" not in first
    assert trace[0].get("redesign") and "resolved" in trace[-1] and len(trace) == 2


def test_a_lens_pair_crowding_a_filter_moves_as_a_whole():
    """A filter, then a lens pair 60 mm apart, the first lens too close to the filter: the pair moves as one group with
    its observation plane (a move of the second lens alone would split it)."""

    optics = (lens("F1", mechanics=ND_FILTER), lens("L1", position=(20.0, 0.0, 0.0)), lens("L2", position=(80.0, 0.0, 0.0)))
    request = AssemblyResolutionRequestV2(optics=optics, externals=(source(),), observations=(screen(position=(160.0, 0.0, 0.0)),))
    trace = walk(request)
    assert "resolved" in trace[-1] and len(trace) <= 3
    for step in trace[:-1]:
        s = step["suggestion"]
        if "suggested_position_design_mm" in s:
            assert s["instance_id"] != "L2" or "L1" in s.get("moved_with", ())


# ------------------------------------------------------------------------------------------------ mirrors and splitters


def _fold(spacing: float, *, lens_after: float = 100.0) -> AssemblyResolutionRequestV2:
    return AssemblyResolutionRequestV2(
        optics=(mirror("M1"), mirror("M2", position=(0.0, spacing, 0.0), normal=(SQRT_HALF, -SQRT_HALF, 0.0)),
                lens("L1", position=(lens_after, spacing, 0.0))),
        externals=(source(),), observations=(screen(position=(lens_after + 76.5, spacing, 0.0)),))


@pytest.mark.parametrize("spacing", [12.0, 20.0, 30.0])
def test_a_fold_mirror_pair_converges_and_its_mirror_moves_with_what_follows(spacing):
    trace = walk(_fold(spacing))
    assert "resolved" in trace[-1] and len(trace) <= 3
    for step in trace[:-1]:
        s = step["suggestion"]
        if s.get("instance_id") == "M2" and "suggested_position_design_mm" in s:
            assert {"L1", "focal_plane"} <= set(s["moved_with"])


@pytest.mark.parametrize("spacing", [10.0, 12.5, 14.5])
def test_a_lens_crowding_a_fold_mirror_converges(spacing):
    request = AssemblyResolutionRequestV2(optics=(mirror("M1"), lens("L1", position=(0.0, spacing, 0.0), rotation=BEAM_Y)),
                                          externals=(source(),), observations=(screen(position=(0.0, spacing + 80.0, 0.0), rotation=BEAM_Y),))
    trace = walk(request)
    assert "resolved" in trace[-1] and len(trace) <= 3


def _splitter(arm: float) -> AssemblyResolutionRequestV2:
    """A plate beamsplitter at the origin (transmits +X, reflects into +Y), a filter pair on each arm ``arm`` mm apart."""

    bs = OpticRequest("BS", DICHROIC.optical_sku, "splitter", (0.0, 0.0, 0.0), look((-SQRT_HALF, SQRT_HALF, 0.0)), (), DICHROIC)
    t_arm = tuple(lens(f"T{k}", position=(arm * k, 0.0, 0.0), mechanics=ND_FILTER) for k in (1, 2))
    r_arm = tuple(lens(f"R{k}", position=(0.0, arm * k, 0.0), rotation=BEAM_Y, mechanics=ND_FILTER) for k in (1, 2))
    return AssemblyResolutionRequestV2(optics=(bs, *t_arm, *r_arm), externals=(source(),),
                                       observations=(screen("outT", position=(2 * arm + 80.0, 0.0, 0.0)),
                                                     screen("outR", position=(0.0, 2 * arm + 80.0, 0.0), rotation=BEAM_Y)))


@pytest.mark.parametrize("arm", [20.0, 25.0, 30.0])
def test_two_arms_of_a_splitter_converge(arm):
    trace = walk(_splitter(arm))
    assert "resolved" in trace[-1] and len(trace) <= 3


# ------------------------------------------------------------------------------------------------ curved mirrors

CONCAVE = replace(MIRROR, optical_sku="OS-OPT-TEST-MIR-CONCAVE-D25-F100", curved=True)
FLAT = replace(MIRROR, optical_sku="OS-OPT-TEST-MIR-FLAT-D25-B")


def _mirror_pair(mechanics, spacing: float, *, third=None) -> AssemblyResolutionRequestV2:
    """Two mirrors folding +X -> +Y -> +X, ``spacing`` mm apart, of the given form; then ``third``, then a plane."""

    m1 = replace(mirror("M1"), optical_sku=mechanics.optical_sku, mechanics=mechanics)
    m2 = replace(mirror("M2", position=(0.0, spacing, 0.0), normal=(SQRT_HALF, -SQRT_HALF, 0.0)), optical_sku=mechanics.optical_sku, mechanics=mechanics)
    return AssemblyResolutionRequestV2(optics=(m1, m2, *((third,) if third else ())), externals=(source(),),
                                       observations=(screen(position=(200.0, spacing, 0.0)),))


def _refused(request: AssemblyResolutionRequestV2) -> dict:
    with pytest.raises(AssemblyResolutionRefusal) as refused:
        resolve_assembly(request, catalog())
    return refused.value.details


@pytest.mark.parametrize("spacing", [12.0, 15.0, 18.0])
def test_a_curved_mirror_pair_is_linked_and_a_flat_pair_is_not(spacing):
    """The owner's linked rule with curved mirrors as powered elements: at the same spacing a flat pair is given a move of
    its later mirror (with what follows it), a concave pair never one that changes its spacing: the pair is reported linked
    with the support pitch it needs."""

    flat = _refused(_mirror_pair(FLAT, spacing))["suggestion"]
    assert {(p["instance_id"], p["with"]): p["linked"] for p in flat.get("conflicting_pairs", ())}.get(("M2", "M1"), False) is False
    assert flat.get("instance_id") == "M2" and "suggested_position_design_mm" in flat or "suggested_positions_design_mm" in flat

    curved = _refused(_mirror_pair(CONCAVE, spacing))["suggestion"]
    pair = next(p for p in curved["conflicting_pairs"] if (p["instance_id"], p["with"]) == ("M2", "M1"))
    assert pair["linked"] is True and pair["min_support_pitch_mm"] > spacing
    assert "suggested_position_design_mm" not in curved and "suggested_positions_design_mm" not in curved
    assert "min_extra_spacing_mm" not in curved


def test_a_flat_mirror_inside_a_curved_group_is_not_a_group_end():
    """A flat fold between two concave mirrors belongs to no group, but the two concave mirrors around it are consecutive
    powered optics: a move of the fold (with what follows it) would change their distance and is never suggested."""

    cm1 = replace(mirror("CM1"), optical_sku=CONCAVE.optical_sku, mechanics=CONCAVE)
    fold = replace(mirror("FM", position=(0.0, 12.0, 0.0), normal=(SQRT_HALF, -SQRT_HALF, 0.0)), optical_sku=FLAT.optical_sku, mechanics=FLAT)
    cm2 = replace(mirror("CM2", position=(80.0, 12.0, 0.0), normal=(-SQRT_HALF, SQRT_HALF, 0.0)), optical_sku=CONCAVE.optical_sku, mechanics=CONCAVE)
    request = AssemblyResolutionRequestV2(optics=(cm1, fold, cm2), externals=(source(),), observations=(screen(position=(80.0, 200.0, 0.0), rotation=BEAM_Y),))
    s = _refused(request)["suggestion"]
    assert s.get("instance_id") != "FM" and "suggested_positions_design_mm" not in s
    if "suggested_position_design_mm" in s:  # the whole group moves
        assert s["instance_id"] == "CM1" and {"FM", "CM2", "focal_plane"} <= set(s["moved_with"])


def test_a_curved_mirror_walk_keeps_the_mirror_and_its_plane_together():
    """A filter crowding a concave mirror: the filter's move never changes the mirror's distance to the plane after it."""

    cm = replace(mirror("CM"), optical_sku=CONCAVE.optical_sku, mechanics=CONCAVE)
    request = AssemblyResolutionRequestV2(
        optics=(cm, lens("F1", position=(0.0, 10.0, 0.0), rotation=BEAM_Y, mechanics=ND_FILTER), lens("F2", position=(0.0, 30.0, 0.0), rotation=BEAM_Y, mechanics=ND_FILTER)),
        externals=(source(),), observations=(screen(position=(0.0, 120.0, 0.0), rotation=BEAM_Y),))
    where = _positions(request)
    trace = walk(request)
    assert "resolved" in trace[-1]
    for step in trace[:-1]:
        s = step["suggestion"]
        positions = s.get("suggested_positions_design_mm", {})
        assert "focal_plane" not in positions or tuple(positions["focal_plane"]) == where["focal_plane"]
        assert "focal_plane" not in s.get("moved_with", ()) or s.get("instance_id") == "CM"


def test_a_customer_supplied_curved_mirror_says_so_itself():
    """A customer-supplied component has no OpenSci identity: ``ExternalRequest.powered`` makes it a group end. A lens right
    after it then does not move away from it with the plane (without the fact it may, as after a flat mirror)."""

    from os_optics.assembly_plan.kernel import ReservedEnvelope

    def request(powered: bool) -> AssemblyResolutionRequestV2:
        supplied = ExternalRequest("CM", "mirror", (0.0, 0.0, 0.0), look((-SQRT_HALF, SQRT_HALF, 0.0)), (), beam_category="MIRROR", size_mm=25.4,
                                   envelope=ReservedEnvelope(diameter_mm=25.4, z_min_mm=-6.0, z_max_mm=0.0), powered=powered)
        return AssemblyResolutionRequestV2(optics=(lens("L1", position=(0.0, 12.0, 0.0), rotation=BEAM_Y),), externals=(source(), supplied),
                                           observations=(screen(position=(0.0, 100.0, 0.0), rotation=BEAM_Y),))

    plain = _refused(request(False))["suggestion"]
    assert plain.get("instance_id", "L1") == "L1" and "suggested_position_design_mm" in plain and "focal_plane" in plain["moved_with"]
    curved = _refused(request(True))["suggestion"]
    assert not (curved.get("instance_id", "L1") == "L1" and "suggested_position_design_mm" in curved)


@pytest.mark.parametrize(("sku", "curved"), [("OS-OPT-MIR-CONCAVE-D12P7-F100-AG", True), ("OS-OPT-MIR-OAP-D25-F50", True), ("OS-OPT-MIR-FLAT-D25-AG", False),
                                             ("OS-OPT-LENS-PCX-D25-F100", False), ("OS-OPT-LENS-CONCAVE", False), (None, False), ("OS-OPT-MIR", False)])
def test_the_form_of_a_curved_mirror_comes_from_its_public_identity(sku, curved):
    assert curved_mirror_sku(sku) is curved


# ------------------------------------------------------------------------------------------------ tight positions


def _span(request: AssemblyResolutionRequestV2, positions: dict) -> float:
    """The length of the row of optics along +X with the new positions applied."""

    where = {**_positions(request), **{k: tuple(v) for k, v in positions.items()}}
    xs = [where[o.instance_id][0] for o in request.optics]
    return max(xs) - min(xs)


def _positions_of(request: AssemblyResolutionRequestV2) -> dict:
    s = _refused(request)["suggestion"]
    assert "suggested_positions_design_mm" in s, s
    return s


ROW = tuple(20.0 * k for k in range(5))


def test_new_positions_are_tighter_than_the_layout_found_and_still_resolve(monkeypatch):
    """new positions were a confirmed layout, not a tight one. Each gap they widened is now bisected as long as
    the design still resolves; the result holds, is shorter than the layout found first, and says how far it got."""

    request = _row(ROW)
    with monkeypatch.context() as patch:  # the sweep of every gap, given the room for it
        _diagnosis_allowance(patch, 3_000_000)
        assert _positions_of(request)["suggested_positions_tightness"] in (kernel.POSITIONS_AT_LEAST_SPACING, kernel.POSITIONS_COMPACTED)
    tight = _positions_of(request)
    assert resolve_assembly(_placed(request, {**_positions(request), **{k: tuple(v) for k, v in tight["suggested_positions_design_mm"].items()}}), catalog()).chains
    with monkeypatch.context() as patch:
        patch.setattr(kernel, "_compact", lambda search, failure, cat, charge, positions, *rest: (positions, kernel.POSITIONS_NOT_COMPACTED))
        found = _positions_of(request)
    assert _span(request, tight["suggested_positions_design_mm"]) < _span(request, found["suggested_positions_design_mm"])


def test_positions_the_allowance_could_not_compact_say_so_and_still_resolve(monkeypatch):
    real = kernel._compact

    def starved(search, failure, cat, charge, *rest):
        def spent(checks):
            raise kernel._BudgetExhausted()

        return real(search, failure, cat, spent, *rest)

    monkeypatch.setattr(kernel, "_compact", starved)
    request = _row(ROW)
    s = _positions_of(request)
    assert s["suggested_positions_tightness"] == kernel.POSITIONS_NOT_COMPACTED
    assert resolve_assembly(_placed(request, {**_positions(request), **{k: tuple(v) for k, v in s["suggested_positions_design_mm"].items()}}), catalog()).chains


def test_positions_the_search_did_not_confirm_say_so(monkeypatch):
    """Whether a design is laid out is asked largest board first; the resolution searches smallest board first and may run
    out of its budget before it gets there. Positions it did not get to within the allowance say so."""

    real = kernel._resolve
    request = _row(ROW)

    def out_of_budget(moved, cat, budget, **kwargs):
        if not kwargs.get("first_layout") and not kwargs.get("diagnose", True) and moved is not request:
            raise AssemblyResolutionRefusal("MECHANICAL_RESOLUTION_BOUND_EXCEEDED", "spent")
        return real(moved, cat, budget, **kwargs)

    monkeypatch.setattr(kernel, "_resolve", out_of_budget)
    assert _positions_of(request)["suggested_positions_tightness"] == kernel.POSITIONS_NOT_CONFIRMED


def test_spacings_the_search_did_not_confirm_are_not_called_confirmed(monkeypatch):
    real = kernel._resolve

    def out_of_budget(moved, cat, budget, **kwargs):
        if not kwargs.get("first_layout") and not kwargs.get("diagnose", True):
            raise AssemblyResolutionRefusal("MECHANICAL_RESOLUTION_BOUND_EXCEEDED", "spent")
        return real(moved, cat, budget, **kwargs)

    monkeypatch.setattr(kernel, "_resolve", out_of_budget)
    request = AssemblyResolutionRequestV2(optics=(lens("L1"), lens("L2", position=(20.0, 0.0, 0.0))), externals=(source(),), observations=(screen(position=(100.0, 0.0, 0.0)),))
    assert _refused(request)["suggestion"]["pairs_confirmed"] is False


def test_compaction_keeps_a_linked_group_whole():
    """A lens pair in a tight row of filters: the lens-to-lens and lens-to-plane spacings stay as drawn in the compacted
    positions (``walk`` asserts it for every step, and that they resolve)."""

    optics = (lens("F1", mechanics=ND_FILTER), lens("F2", position=(10.0, 0.0, 0.0), mechanics=ND_FILTER), lens("F3", position=(20.0, 0.0, 0.0), mechanics=ND_FILTER),
              lens("L1", position=(30.0, 0.0, 0.0)), lens("L2", position=(100.0, 0.0, 0.0)))
    request = AssemblyResolutionRequestV2(optics=optics, externals=(source(),), observations=(screen(position=(180.0, 0.0, 0.0)),))
    trace = walk(request)
    assert "resolved" in trace[-1]


# ------------------------------------------------------------------------------------------------ a spent allowance


def _dense(s: float) -> AssemblyResolutionRequestV2:
    """A plate splitter; on each arm a filter pair and a fold mirror, every element ``s`` mm from the one before it."""

    bs = OpticRequest("BS", DICHROIC.optical_sku, "splitter", (0.0, 0.0, 0.0), look((-SQRT_HALF, SQRT_HALF, 0.0)), (), DICHROIC)
    t_arm = (lens("FT", position=(s, 0.0, 0.0), mechanics=ND_FILTER), lens("WT", position=(2 * s, 0.0, 0.0), mechanics=ND_FILTER),
             mirror("MT", position=(3 * s, 0.0, 0.0), normal=(-SQRT_HALF, -SQRT_HALF, 0.0)))
    r_arm = (lens("FR", position=(0.0, s, 0.0), rotation=BEAM_Y, mechanics=ND_FILTER), lens("WR", position=(0.0, 2 * s, 0.0), rotation=BEAM_Y, mechanics=ND_FILTER),
             mirror("MR", position=(0.0, 3 * s, 0.0), normal=(-SQRT_HALF, -SQRT_HALF, 0.0)))
    return AssemblyResolutionRequestV2(optics=(bs, *t_arm, *r_arm), externals=(source(),),
                                       observations=(screen("outT", position=(3 * s, -150.0, 0.0), rotation=look((0.0, -1.0, 0.0))),
                                                     screen("outR", position=(-150.0, 3 * s, 0.0), rotation=look((-1.0, 0.0, 0.0)))))


@pytest.mark.parametrize(("name", "request_of"), [
    ("row6", lambda: _row(tuple(22.5 * k for k in range(6)))),
    ("row8", lambda: _row(tuple(20.0 * k for k in range(8)))),
    ("lens_pair", lambda: AssemblyResolutionRequestV2(optics=(lens("L1"), lens("L2", position=(20.0, 0.0, 0.0))), externals=(source(),), observations=(screen(position=(100.0, 0.0, 0.0)),))),
    ("fold", lambda: _fold(15.0)),
    ("splitter", lambda: _splitter(25.0)),
    ("dense", lambda: _dense(25.0)),
])
@pytest.mark.parametrize("allowance", [2_000, 20_000])
def test_a_spent_allowance_never_brings_a_pair_back(monkeypatch, name, request_of, allowance):
    """a diagnosis that runs out before a single move, new positions or the confirmation of its spacings used to
    publish lower bounds, and the walk met the same pair again with a larger number (dense_s25 strict: MT / WT 26.5, then
    29.0, then 68.0). A spacing it could not confirm now stands where the two supports cannot meet, and says it is not
    confirmed; the walk meets no pair twice (``walk`` asserts it). The redesign moves are the walk's."""

    _diagnosis_allowance(monkeypatch, allowance)
    trace = walk(request_of(), steps=8)
    assert "resolved" in trace[-1]
    for step in trace[:-1]:
        s = step["suggestion"]
        if s.get("pairs_confirmed") is False and "suggested_position_design_mm" not in s and "suggested_positions_design_mm" not in s:
            assert all(p["min_support_pitch_mm"] > p["spacing_mm"] for p in s.get("conflicting_pairs", ()))


def test_an_unconfirmed_spacing_says_so_and_a_confirmed_one_resolves(monkeypatch):
    request = AssemblyResolutionRequestV2(optics=(lens("L1"), lens("L2", position=(20.0, 0.0, 0.0))), externals=(source(),), observations=(screen(position=(100.0, 0.0, 0.0)),))
    confirmed = _refused(request)["suggestion"]
    assert confirmed["pairs_confirmed"] is True
    (pair,) = confirmed["conflicting_pairs"]
    widened = _shifted(_positions(request), {"L2", "focal_plane"}, (pair["min_support_pitch_mm"] - pair["spacing_mm"], 0.0, 0.0))
    assert resolve_assembly(_placed(request, widened), catalog()).chains

    _diagnosis_allowance(monkeypatch, 20)
    starved = _refused(request)["suggestion"]
    (unconfirmed,) = starved["conflicting_pairs"]
    assert starved["pairs_confirmed"] is False
    # the spacing from which the two supports cannot meet, whichever way their forks turn: more than the confirmed one,
    # and every gap of a row counts when the board caps it
    assert unconfirmed["min_support_pitch_mm"] > pair["min_support_pitch_mm"]


# ------------------------------------------------------------------------------------------------ board limits


def test_a_board_limit_below_the_footprint_converges():
    """Under a 150 x 100 limit a 245 mm line does not fit any allowed board: the refusal is the limit with the smallest
    board the footprint fits on; applying it, the design is laid out (or its own conflict is named, then fixed)."""

    request = replace(_row((0.0, 100.0, 245.0)), max_board_size_mm=(150.0, 100.0))
    trace = walk(request)
    assert trace[0]["code"] == "BREADBOARD_CONSTRAINT_UNSATISFIABLE" and "resolved" in trace[-1]


def test_a_board_limit_that_excludes_the_board_a_layout_was_found_on_converges():
    request = replace(_row((0.0, 25.0), plane=False), max_board_size_mm=(150.0, 100.0))
    trace = walk(request)
    assert "resolved" in trace[-1] and len(trace) <= 3


def test_without_a_board_limit_a_conflict_is_never_reported_as_a_board_matter():
    trace = walk(_row(tuple(20.0 * k for k in range(8))))
    for step in trace[:-1]:
        assert step["code"] != "BREADBOARD_CONSTRAINT_UNSATISFIABLE" and step["suggestion"]["action"] != "ALLOW_LARGER_BREADBOARD"


# ------------------------------------------------------------------------------------------------ review cases


def _fold_group(x: float) -> AssemblyResolutionRequestV2:
    """Two fold mirrors 25 mm apart, then a lens pair ``x`` mm after M2 (review: foldtele): L1's support reaches M1's."""

    return AssemblyResolutionRequestV2(
        optics=(mirror("M1"), mirror("M2", position=(0.0, 25.0, 0.0), normal=(SQRT_HALF, -SQRT_HALF, 0.0)),
                lens("L1", position=(x, 25.0, 0.0)), lens("L2", position=(x + 15.0, 25.0, 0.0))),
        externals=(source(),), observations=(screen(position=(x + 95.0, 25.0, 0.0)),))


@pytest.mark.parametrize("x", [10.0, 15.0, 17.5])
def test_conflicting_pairs_are_beam_neighbours_along_the_beam_into_the_later_one(x):
    """Review D2: L1 collides with M1, which is not its beam neighbour (M2 is): the pair (L1, M1) was listed along their
    diagonal ([0.51, 0.86, 0]), and following it moved the lens group off its beam. Every entry is now two beam
    neighbours, along the beam arriving at the later one."""

    with pytest.raises(AssemblyResolutionRefusal) as refused:
        resolve_assembly(_fold_group(x), catalog())
    assert (refused.value.details["instance_id"], refused.value.details["with"]) == ("L1", "M1")
    pairs = refused.value.details["suggestion"].get("conflicting_pairs", [])
    neighbours = {("M2", "M1"): [0.0, 1.0, 0.0], ("L1", "M2"): [1.0, 0.0, 0.0], ("L2", "L1"): [1.0, 0.0, 0.0]}
    assert pairs
    for p in pairs:
        assert neighbours[(p["instance_id"], p["with"])] == p["beam_direction_design"], p


def test_conflicting_pairs_compose_in_any_order():
    """Review D5: applying the entries one by one used to narrow a neighbour's gap. Each entry widens only its own gap
    (the later element with everything moving with it), so the entries applied in either order give the same design."""

    with pytest.raises(AssemblyResolutionRefusal) as refused:
        resolve_assembly(_row(tuple(5.0 * k for k in range(4))), catalog())
    s = refused.value.details["suggestion"]
    pairs = s["conflicting_pairs"]
    assert len(pairs) >= 2

    def applied(order):
        where = _positions(_row(tuple(5.0 * k for k in range(4))))
        for p in order:
            delta = tuple(v * (p["min_support_pitch_mm"] - p["spacing_mm"]) for v in p["beam_direction_design"])
            where = _shifted(where, {p["instance_id"], *p.get("moved_with", ())}, delta)
        return {k: tuple(round(x, 6) for x in v) for k, v in where.items()}

    assert applied(pairs) == applied(list(reversed(pairs)))
    if s["pairs_confirmed"]:
        assert len(resolve_assembly(_placed(_row(tuple(5.0 * k for k in range(4))), applied(pairs)), catalog()).chains) == 4


def test_a_footprint_board_takes_the_footprint_with_any_of_the_mounts():
    """Review D7: under a size limit no allowed board takes even the footprint, and no layout was found on a larger one:
    the board named was the one the footprint of the mounts tried at that point fits, too small for the wider mounts the
    search then falls back to (the next submission asked for a larger board). It now takes the footprint with each
    chain's widest compatible mount, so allowing it never brings the limit back."""

    from kernel_fixture import FIXED, XY

    # F3 stands 20 mm behind F2: not laid out on any board, so the limit refusal names a board for the footprint
    request = replace(_row((0.0, 200.0, 220.0)), max_board_size_mm=(150.0, 100.0))
    fam = catalog(mounts=(FIXED, XY))
    with pytest.raises(AssemblyResolutionRefusal) as refused:
        resolve_assembly(request, fam)
    board = refused.value.details["suggestion"]["smallest_board_for_footprint_mm"]
    try:
        resolve_assembly(replace(request, max_board_size_mm=tuple(board)), fam)
    except AssemblyResolutionRefusal as again:
        assert again.code != "BREADBOARD_CONSTRAINT_UNSATISFIABLE", again.details


def test_a_run_inside_a_linked_group_never_passes_the_plane_after_it():
    """A filter 7.5 mm after a lens whose observation plane is 7.5 mm further: widening the filter's gap to the lens by
    the run of plain optics alone would take the filter past the plane (seen on the 06.3 catalog: lensplane_d15 asked
    for the window 49.5 mm after the lens, 34.5 mm past its plane). Such a pair moves with everything after it and is
    marked linked (the lens's distance to its plane changes)."""

    request = AssemblyResolutionRequestV2(
        optics=(lens("W1", position=(-20.0, 0.0, 0.0), mechanics=ND_FILTER), lens("L1"), lens("W2", position=(7.5, 0.0, 0.0), mechanics=ND_FILTER)),
        externals=(source(),), observations=(screen(position=(15.0, 0.0, 0.0)),))
    with pytest.raises(AssemblyResolutionRefusal) as refused:
        resolve_assembly(request, catalog())
    pair = next(p for p in refused.value.details["suggestion"]["conflicting_pairs"] if (p["instance_id"], p["with"]) == ("W2", "L1"))
    assert pair["min_support_pitch_mm"] - pair["spacing_mm"] > 7.5
    assert pair["linked"] is True and pair["moved_with"] == ["focal_plane"]
