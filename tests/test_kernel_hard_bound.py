"""the hard bound inside one layout evaluation: the work budget (chain placements and geometry checks) is
charged before the work, so a large design's evaluation stops between two chains or two batches of geometry tests once
the budget or the injected deadline is spent -- with the deterministic, customer-safe refusal of the bounded search.

The designs here are deliberately large (a Project has no contract-level component cap): a 7 x 7 grid of lenses on seven
parallel beams (49 support chains on one breadboard), and 150 lenses scattered off any grid (every one asks for its own
plan-translation phase).
"""

from __future__ import annotations

import json
import math
import random
import time
from types import SimpleNamespace

import pytest

from os_optics.assembly_plan.kernel import (
    AssemblyResolutionRefusal,
    AssemblyResolutionRequestV2,
    ResolutionBudget,
    ResolverPolicyV1,
    resolve_assembly,
)
from os_optics.assembly_plan.kernel import resolve as kernel

from kernel_fixture import BEAM_X, FIXED, KINEMATIC_T, board, catalog, lens, screen, source

IDENTITY_WORDS = ("OS-MECH-", "rp:", "part_key")


def lens_grid(rows: int = 7, per_row: int = 7, *, gap: float = 75.0, jitter: float = 0.0, clash_last: bool = False) -> AssemblyResolutionRequestV2:
    """``rows`` parallel beams along +X, ``per_row`` lenses on each, ``gap`` mm apart both ways. ``clash_last``: the last
    lens stands 9 mm behind its neighbour position (its support collides), so the layout fails at the deepest chain."""

    optics, externals = [], []
    for r in range(rows):
        y = r * gap + jitter * r
        externals.append(source(f"S{r}", position=(-60.0, y, 0.0), rotation=BEAM_X))
        for k in range(per_row):
            optics.append(lens(f"L{r}_{k}", position=(k * gap + jitter * (k * 7 % 11), y, 0.0)))
    if clash_last:
        last = optics[-1]
        optics[-1] = lens(last.instance_id, position=(last.position_design[0] - gap + 9.0, last.position_design[1], 0.0))
    return AssemblyResolutionRequestV2(optics=tuple(optics), externals=tuple(externals))


def scattered_lenses(count: int = 150, seed: int = 260) -> AssemblyResolutionRequestV2:
    rng = random.Random(seed)
    optics = tuple(lens(f"L{k}", position=(round(rng.uniform(0.0, 480.0), 3), round(rng.uniform(0.0, 480.0), 3), 0.0)) for k in range(count))
    return AssemblyResolutionRequestV2(optics=optics, externals=(source(position=(-60.0, 0.0, 0.0)),))


def refused(request, budget=None, cat=None) -> AssemblyResolutionRefusal:
    with pytest.raises(AssemblyResolutionRefusal) as refusal:
        resolve_assembly(request, cat or catalog(), budget=budget)
    text = json.dumps(refusal.value.details)
    for word in IDENTITY_WORDS:
        assert word not in text, (word, text)
    return refusal.value


def within(budget: ResolutionBudget) -> None:
    assert budget.chain_placements <= budget.max_chain_placements
    assert budget.geometry_checks <= budget.max_geometry_checks


def test_the_large_grid_resolves_in_one_layout_evaluation():
    budget = ResolutionBudget(12000)
    realization = resolve_assembly(lens_grid(), catalog(), budget=budget)
    assert len(realization.chains) == 49
    assert (budget.layout_evaluations, budget.chain_placements, budget.exhausted_by) == (1, 49, None)


def test_one_layout_evaluation_never_places_more_chains_than_the_budget_has_left():
    """Before: the evaluation was admitted with budget left and then placed all 49 chains (49 placements against a
    budget of 10). Now each chain is charged before it is placed and the evaluation stops at the tenth."""

    budget = ResolutionBudget(10)
    refusal = refused(lens_grid(), budget)
    # one evaluation of the first-fit search (stopped by its share of the budget, ) and one of the complete one
    assert (budget.chain_placements, budget.layout_evaluations, budget.exhausted_by) == (10, 2, "WORK")
    # no layout failure was established before the stop: the plain, actionable bound refusal
    assert refusal.code == "MECHANICAL_RESOLUTION_BOUND_EXCEEDED"
    # the refusal names the budget that stopped the search, its limit and what was spent
    assert refusal.details == {"reason": "SEARCH_BOUND_REACHED", "search_budget_exhausted": True, "suggestion": {"action": "SIMPLIFY_DESIGN"},
                               "search_bound": {"kind": "CHAIN_PLACEMENTS", "limit": 10, "spent": 10}}
    assert refusal.internal["exhausted_by"] == "WORK" and refusal.internal["chain_placements"] == 10
    assert budget.exhausted_counter == "CHAIN_PLACEMENTS"


def test_a_spent_geometry_budget_stops_a_large_evaluation_inside_itself():
    full = ResolutionBudget(12000)
    resolve_assembly(lens_grid(), catalog(), budget=full)
    half = ResolutionBudget(12000, max_geometry_checks=full.geometry_checks // 2)
    refusal = refused(lens_grid(), half)
    assert half.exhausted_by == "WORK" and half.layout_evaluations == 1
    assert 1 <= half.chain_placements < 49  # stopped between two chains of the one evaluation
    within(half)
    assert refusal.details["search_budget_exhausted"] is True
    assert half.exhausted_counter == "GEOMETRY_CHECKS"
    assert refusal.details["search_bound"] == {"kind": "GEOMETRY_CHECKS", "limit": half.max_geometry_checks, "spent": half.geometry_checks}


def test_the_deadline_is_polled_inside_a_large_evaluation():
    """The injected deadline expires as soon as the first evaluation has started: it is noticed within one batch of
    geometry tests (``DEADLINE_POLL_CHECKS``), not after the 49 chains."""

    budget = ResolutionBudget(12000, expired=lambda: budget.layout_evaluations >= 1)
    refusal = refused(lens_grid(), budget)
    assert budget.exhausted_by == "DEADLINE" and budget.layout_evaluations == 1
    assert budget.chain_placements < 49
    assert refusal.details["search_budget_exhausted"] is True
    assert refusal.details["search_bound"] == {"kind": "DEADLINE"} and budget.exhausted_counter is None


def test_a_large_pathological_design_terminates_within_a_low_budget_deterministically():
    """49 chains, the deepest one colliding: every evaluation runs 48 placements before it fails. With a low geometry
    budget the search stops inside it, refuses with the most informative failure seen (the deepest chain), the same
    every time, and the time follows the budget."""

    outcomes = []
    for _ in range(2):
        budget = ResolutionBudget(12000, max_geometry_checks=150_000)
        start = time.perf_counter()
        refusal = refused(lens_grid(jitter=0.37, clash_last=True), budget)
        elapsed = time.perf_counter() - start
        within(budget)
        assert budget.exhausted_by == "WORK" and budget.geometry_checks > 140_000
        assert elapsed < 10.0, elapsed  # ~0.3 s on the development desktop; generous for shared CI runners
        outcomes.append((refusal.code, refusal.details))
    code, details = outcomes[0]
    assert outcomes[1] == outcomes[0]
    assert code == "MECHANICAL_LAYOUT_CONFLICT" and details["search_budget_exhausted"] is True
    assert (details["instance_id"], details["reason"]) == ("L6_6", "SUPPORTS_COLLIDE")
    assert details["suggestion"]["action"] == "ADJUST_COMPONENT_SPACING"


def test_the_default_policy_bounds_a_large_pathological_design():
    """The same design with the default policy: bounded by the geometry checks (the placements alone would allow the
    whole search: ~12 000 placements of ~330 checks each at this size)."""

    policy = ResolverPolicyV1()
    budget = ResolutionBudget(policy.max_chain_placements, max_geometry_checks=policy.max_geometry_checks)
    start = time.perf_counter()
    refusal = refused(lens_grid(jitter=0.37, clash_last=True), budget)
    elapsed = time.perf_counter() - start
    within(budget)
    assert budget.exhausted_by == "WORK" and budget.chain_placements < policy.max_chain_placements
    assert (refusal.details["instance_id"], refusal.details["reason"]) == ("L6_6", "SUPPORTS_COLLIDE")
    assert elapsed < 60.0, elapsed  # ~2.5 s on the development desktop


def test_plan_translation_scoring_of_a_scattered_design_is_charged():
    """150 lenses off any grid ask for ~150 translation phases per axis: ~560 000 candidate translations per board.
    Scoring them is charged like any other work, so a low budget stops there -- the previous search scored every
    candidate against every chain (minutes per board) before its first, budgeted, evaluation."""

    budget = ResolutionBudget(12000, max_geometry_checks=200_000)
    start = time.perf_counter()
    refusal = refused(scattered_lenses(), budget)
    within(budget)
    assert budget.exhausted_by == "WORK" and time.perf_counter() - start < 10.0
    assert refusal.details["search_budget_exhausted"] is True


def test_the_refusal_diagnosis_has_its_own_fixed_allowance(monkeypatch):
    request = lens_grid(rows=1, per_row=2, gap=8.0)  # two lenses 8 mm apart: their supports collide
    full = refused(request)
    # a linked lens pair: the least support pitch of the pair, measured by the diagnosis
    assert full.details["suggestion"]["min_support_pitch_mm"] > 8.0 and full.details["suggestion"]["conflicting_pairs"]
    starved_policy = catalog(policy=ResolverPolicyV1(diagnosis_max_geometry_checks=20))  # the policy's per-stage allowance
    floor = refused(request, cat=starved_policy)
    # the refusal itself (who, why, what to do) does not depend on the diagnosis
    assert (floor.code, floor.details["instance_id"], floor.details["reason"], floor.details["with"]) ==         (full.code, full.details["instance_id"], full.details["reason"], full.details["with"])
    assert floor.details["suggestion"]["action"] == full.details["suggestion"]["action"]
    # review: the floor has an allowance of its own, spent first -- a starved diagnosis still names the pair
    # and the least spacing its supports can stand at (unconfirmed)
    s = floor.details["suggestion"]
    assert s["conflicting_pairs"] and s["pairs_confirmed"] is False and s["min_support_pitch_mm"] > 8.0
    assert "nearest_fixable_offset_mm" not in s and "suggested_positions_design_mm" not in s
    monkeypatch.setattr(kernel, "DIAGNOSIS_FLOOR_CHECKS", 20)
    starved = refused(request, cat=starved_policy)
    assert starved.details["suggestion"]["action"] == full.details["suggestion"]["action"]
    for key in ("nearest_fixable_offset_mm", "min_support_pitch_mm", "conflicting_pairs", "suggested_positions_design_mm"):
        assert key not in starved.details["suggestion"], key


def tilted_row(count: int = 4, gap: float = 20.0) -> AssemblyResolutionRequestV2:
    """``count`` lenses ``gap`` mm apart on one beam, each asking for tip/tilt: with only a fixed mount and a tip/tilt
    mount too large for the spacing, chain after chain falls back to the fixed mount (a layout fallback), and each
    fallback's cause is diagnosed for the move that would keep the tip/tilt mount."""

    return AssemblyResolutionRequestV2(
        optics=tuple(lens(f"L{k + 1}", position=(k * gap, 0.0, 0.0), dofs=(("Rx", "MANUAL"), ("Ry", "MANUAL"))) for k in range(count)),
        externals=(source(),), observations=(screen(position=(count * gap + 60.0, 0.0, 0.0)),))


class _FallbackDiagnoses:
    """Counts the geometry checks the layout-fallback diagnoses spend (``_with_spacing``), whatever they are charged to."""

    def __init__(self, monkeypatch):
        self.calls, self.checks, self.inside = 0, 0, False
        with_spacing, take = kernel._with_spacing, ResolutionBudget.take

        def counted_with_spacing(*args, **kwargs):
            self.calls, self.inside = self.calls + 1, True
            try:
                return with_spacing(*args, **kwargs)
            finally:
                self.inside = False

        def counted_take(budget, *, placements=0, checks=0):
            if self.inside and budget.max_chain_placements == 1:  # a diagnosis allowance
                self.checks += checks
            return take(budget, placements=placements, checks=checks)

        monkeypatch.setattr(kernel, "_with_spacing", counted_with_spacing)
        monkeypatch.setattr(ResolutionBudget, "take", counted_take)


TILT_CATALOG = dict(mounts=(FIXED, KINEMATIC_T))


def test_layout_fallback_diagnoses_share_one_bounded_account_of_the_budget(monkeypatch):
    """every chain that falls back had its cause diagnosed with a fresh allowance outside the
    budget, so the work outside the bound grew with the number of chains that fall back (2.2 M checks, seconds, for a
    30-lens design). The diagnoses now share the budget's diagnosis account: their total is bounded by it, and the
    refusal itself (decided by the search, diagnosed with its own allowance) does not depend on it."""

    diagnoses = _FallbackDiagnoses(monkeypatch)
    roomy = ResolutionBudget(12000)
    full = refused(tilted_row(), roomy, catalog(**TILT_CATALOG))
    # several fallbacks, each a real search for a move (laid out on the mounts the search tries first, a
    # move is found sooner than on the failure's own mounts)
    assert diagnoses.calls >= 3 and diagnoses.checks > 10_000
    assert roomy.diagnosis_checks == diagnoses.checks <= roomy.max_diagnosis_checks
    diagnoses.calls, diagnoses.checks = 0, 0
    tight = ResolutionBudget(12000, max_diagnosis_checks=5_000)
    starved = refused(tilted_row(), tight, catalog(**TILT_CATALOG))
    assert diagnoses.calls >= 3 and diagnoses.checks == tight.diagnosis_checks <= 5_000
    assert (tight.chain_placements, tight.geometry_checks, tight.exhausted_by) == (roomy.chain_placements, roomy.geometry_checks, None)
    assert (starved.code, starved.details) == (full.code, full.details)  # the refusal keeps its numbers
    assert full.details["suggestion"]["min_support_pitch_mm"] > 20.0


def test_a_layout_fallback_diagnosis_stops_at_the_deadline(monkeypatch):
    """The injected deadline is polled inside a fallback's diagnosis too: once it has passed, the diagnosis stops within
    one poll interval and the resolution refuses as a spent budget, with the refusal's own numbers."""

    from os_optics.assembly_plan.kernel.models import DEADLINE_POLL_CHECKS

    diagnoses = _FallbackDiagnoses(monkeypatch)
    budget = ResolutionBudget(12000, expired=lambda: diagnoses.inside)
    refusal = refused(tilted_row(), budget, catalog(**TILT_CATALOG))
    # the deadline is noticed at the first poll of the diagnosis account (a fallback's diagnosis now spends little --
    # the pair floor and a single move on the layout -- so that poll may come in a later fallback than the first)
    assert budget.exhausted_by == "DEADLINE" and diagnoses.calls >= 1
    assert diagnoses.checks <= 2 * DEADLINE_POLL_CHECKS
    assert refusal.details["search_budget_exhausted"] is True and refusal.details["suggestion"]["action"] == "ADJUST_COMPONENT_SPACING"


def test_a_budget_charge_that_would_pass_a_limit_is_refused_and_not_counted():
    budget = ResolutionBudget(3, max_geometry_checks=100)
    assert budget.take(placements=2, checks=60) and budget.take(placements=1, checks=40)
    assert not budget.take(checks=1) and budget.exhausted_by == "WORK"
    assert (budget.chain_placements, budget.geometry_checks) == (3, 100)
    assert not budget.admit()
    with pytest.raises(ValueError):
        ResolutionBudget(1, max_geometry_checks=0)


def test_the_deadline_is_polled_every_few_thousand_checks_without_a_clock():
    calls = []
    budget = ResolutionBudget(10, max_geometry_checks=10**9, expired=lambda: calls.append(1) or False)
    for _ in range(100):
        assert budget.take(checks=1000)
    # 100 000 checks: one poll per DEADLINE_POLL_CHECKS (the kernel never reads a clock itself)
    from os_optics.assembly_plan.kernel.models import DEADLINE_POLL_CHECKS
    assert len(calls) == pytest.approx(100_000 / DEADLINE_POLL_CHECKS, abs=1)


# ------------------------------------------------------------------------------------------------ translations


def _reference_translation_candidates(plans, board_, fixed_extent):
    """The previous scoring (every candidate against every chain): the oracle the bit-set scoring must reproduce."""

    p, e = board_.pitch_mm, board_.edge_offset_mm
    rx, ry = set(), set()

    def residue(value):
        return round(value % p, 9) % p

    for plan in plans:
        ax, ay = plan.post_axis_rel
        a = plan.optic.axis_lab
        base = plan.stack.holder.base
        middle = (base.slot_start_mm + base.slot_end_mm) / 2.0
        if abs(a[1]) <= 1e-12:
            ry.add(residue(e - ay))
            rx.add(residue(e - ax - math.copysign(middle, a[0])))
        if abs(a[0]) <= 1e-12:
            rx.add(residue(e - ax))
            ry.add(residue(e - ay - math.copysign(middle, a[1])))
    (fx0, fy0), (fx1, fy1) = fixed_extent
    cx, cy = (board_.size_x_mm - (fx0 + fx1)) / 2.0, (board_.size_y_mm - (fy0 + fy1)) / 2.0

    def on_grid(value):
        return abs(((value - e) / p) - round((value - e) / p)) <= 1e-9

    def score(tx, ty):
        wanted = rows = centred = 0
        for plan in plans:
            ax, ay = plan.post_axis_rel
            a = plan.optic.axis_lab
            base = plan.stack.holder.base
            middle = (base.slot_start_mm + base.slot_end_mm) / 2.0
            if abs(a[1]) <= 1e-12 and on_grid(ay + ty):
                on_row, mid = True, on_grid(ax + tx + math.copysign(middle, a[0]))
            elif abs(a[0]) <= 1e-12 and on_grid(ax + tx):
                on_row, mid = True, on_grid(ay + ty + math.copysign(middle, a[1]))
            else:
                on_row, mid = False, False
            rows += on_row
            centred += mid
            wanted += on_row and any(d.axis == "Tz" for d in plan.optic.request.retained_dofs)
        return wanted, rows, centred

    def around(residue_, centre):
        if residue_ is None:
            return [round(centre, 9) + 0.0]
        k = round((centre - residue_) / p)
        return [round(residue_ + (k + dk) * p, 9) + 0.0 for dk in (0, -1, 1, -2, 2)]

    out = []
    for rxv in sorted(rx) or [None]:
        for ryv in sorted(ry) or [None]:
            for tx in around(rxv, cx):
                for ty in around(ryv, cy):
                    wanted, rows, centred = score(tx, ty)
                    out.append(((-wanted, -rows, -centred, round(abs(tx - cx) + abs(ty - cy), 6), tx, ty), (tx, ty)))
    out.sort(key=lambda item: item[0])
    seen, result = set(), []
    for _, t in out:
        if t not in seen:
            seen.add(t)
            result.append(t)
    return result[:ResolverPolicyV1().max_translations]


def _fake_plan(rng: random.Random, pitch: float):
    axis = rng.choice([(1.0, 0.0, 0.0), (-1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, -1.0, 0.0), (math.sqrt(0.5), math.sqrt(0.5), 0.0)])
    # on the lattice (multiples of a tenth of the pitch, often hitting grid lines) or anywhere
    coordinate = (lambda: rng.randint(-40, 40) * pitch / 10.0) if rng.random() < 0.6 else (lambda: rng.uniform(-200.0, 200.0))
    slot = rng.choice([(10.0, 40.0), (12.5, 37.5), (5.0, 30.0)])
    dofs = tuple(SimpleNamespace(axis=a) for a in rng.sample(["Tx", "Ty", "Tz", "Rx", "Rz"], rng.randint(0, 2)))
    return SimpleNamespace(post_axis_rel=(coordinate(), coordinate()),
                           optic=SimpleNamespace(axis_lab=axis, request=SimpleNamespace(retained_dofs=dofs)),
                           stack=SimpleNamespace(holder=SimpleNamespace(base=SimpleNamespace(slot_start_mm=slot[0], slot_end_mm=slot[1]))))


def test_bit_set_translation_scoring_reproduces_the_previous_order_exactly():
    rng = random.Random(20260)
    boards = [board(300, 300), board(600, 300), board(450, 150), board(900, 600)]
    for case in range(300):
        b = rng.choice(boards)
        plans = [_fake_plan(rng, b.pitch_mm) for _ in range(rng.randint(1, 14))]
        extent = ((rng.uniform(-150.0, 0.0), rng.uniform(-150.0, 0.0)), (rng.uniform(0.0, 150.0), rng.uniform(0.0, 150.0)))
        charged = []
        got = kernel._translation_candidates(plans, b, extent, charged.append, limit=ResolverPolicyV1().max_translations)
        assert got == _reference_translation_candidates(plans, b, extent), case
        assert sum(charged) >= len(got)


def test_a_further_pass_on_one_budget_keeps_what_was_established_and_is_not_diagnosed(monkeypatch):
    """the service's second kernel pass (the DOFs on a larger board) searched the design again and diagnosed a
    refusal nobody reads, which made a board limit refusal the slowest there was. It now starts from the failures the
    first pass established, without a diagnosis, and answers the same."""

    from dataclasses import replace

    request = replace(lens_grid(rows=1, per_row=4, gap=20.0), max_board_size_mm=(150.0, 100.0))
    fam = catalog()
    fresh = ResolutionBudget(12000)
    first = refused(request, fresh, fam)
    calls = []
    diagnose = kernel._diagnose
    monkeypatch.setattr(kernel, "_diagnose", lambda *args, **kwargs: calls.append(1) or diagnose(*args, **kwargs))
    alone = ResolutionBudget(12000)
    unlimited = replace(request, max_board_size_mm=None)
    expected = refused(unlimited, alone, fam)
    assert calls and first.code

    calls.clear()
    before = fresh.geometry_checks
    again = refused(unlimited, fresh, fam)
    assert not calls and again.code == expected.code and again.details["instance_id"] == expected.details["instance_id"]
    assert "conflicting_pairs" not in again.details["suggestion"]
    assert fresh.geometry_checks - before < alone.geometry_checks  # the first pass's failures are not established again
