"""(the complete fixing-hole search) and the refusal guidance that depends on it.

Before: every chain took the first free fixing hole its fork reached, in design order, and an earlier chain's hole and
fork turn were never revisited, so two optics closer than ~62 mm on one beam were refused ``NO_FIXING_HOLE`` although
turning the first fork upstream lays them out under the kernel's own rules. Now the chains are placed depth first
with the earlier chains revisited (``_DepthFirst``), after a first-fit search whose layouts are kept exactly.
"""

from __future__ import annotations

import json
import math
from dataclasses import replace

import pytest

from os_optics.assembly_plan.kernel import (
    AssemblyResolutionRefusal,
    AssemblyResolutionRequestV2,
    ResolutionBudget,
    ResolverPolicyV1,
    resolve_assembly,
)
from os_optics.assembly_plan.kernel import resolve as kernel

from kernel_fixture import ND_FILTER, SQRT_HALF, catalog, lens, look, mirror, screen, source

IDENTITY_WORDS = ("OS-MECH-", "rp:", "part_key")


def pair(spacing: float, **kwargs) -> AssemblyResolutionRequestV2:
    return AssemblyResolutionRequestV2(optics=(lens("L1"), lens("L2", position=(spacing, 0.0, 0.0))), externals=(source(),), **kwargs)


def filters(spacing: float, **kwargs) -> AssemblyResolutionRequestV2:
    """Two plane filters: moving one along the beam changes no optical spacing (no linked group)."""

    return AssemblyResolutionRequestV2(optics=(lens("F1", mechanics=ND_FILTER), lens("F2", position=(spacing, 0.0, 0.0), mechanics=ND_FILTER)),
                                       externals=(source(),), **kwargs)


def refusal_of(request: AssemblyResolutionRequestV2, **kwargs) -> AssemblyResolutionRefusal:
    with pytest.raises(AssemblyResolutionRefusal) as refused:
        resolve_assembly(request, catalog(), **kwargs)
    text = json.dumps(refused.value.details)
    for word in IDENTITY_WORDS:
        assert word not in text, (word, text)
    return refused.value


def layout(realization) -> list[tuple]:
    return [(c.instance_id, c.hole, c.fork_yaw_deg) for c in realization.chains]


@pytest.mark.parametrize("spacing", [30.0, 40.0, 50.0, 60.0])
def test_two_lenses_closer_than_a_downstream_fork_are_laid_out(spacing):
    """The first fork pointed downstream lies under the second lens's holder tube; turning it (upstream or aside) leaves
    both chains a hole of their own. Each layout passes the kernel's own checks."""

    budget = ResolutionBudget(12000)
    r = resolve_assembly(pair(spacing), catalog(), budget=budget)
    assert len(r.chains) == 2 and budget.exhausted_by is None
    assert {c.instance_id for c in r.chains} == {"L1", "L2"} and r.chains[0].hole != r.chains[1].hole
    assert all(c.status == "PASS" for c in r.checks if c.kind in ("INTER_CHAIN_CLEARANCE", "BEAM_PATH_CLEARANCE", "BASE_FIXATION"))
    if spacing == 30.0:  # only the upstream turn of the first fork leaves the second a hole on the grid row
        assert [c.fork_yaw_deg for c in r.chains] == [180.0, 0.0]


def test_the_smallest_board_a_complete_search_finds_replaces_a_larger_first_fit_board(monkeypatch):
    """62.5 mm apart, the first-fit placement lays the pair out on the 200 x 100 board with both forks along the beam,
    while the complete search fits the 150 x 100 board, which took the footprint but not the first-fit forks. The
    customer pays for the board, so the smaller layout with the same hardware and as many
    requested DOFs is taken; it passes the kernel's own checks."""

    budget = ResolutionBudget(12000)
    r = resolve_assembly(pair(62.5), catalog(), budget=budget)
    assert (r.board.board.size_x_mm, r.board.board.size_y_mm) == (150.0, 100.0) and budget.exhausted_by is None
    assert {c.instance_id for c in r.chains} == {"L1", "L2"} and r.chains[0].hole != r.chains[1].hole
    assert all(c.status == "PASS" for c in r.checks if c.kind in ("INTER_CHAIN_CLEARANCE", "BEAM_PATH_CLEARANCE", "BASE_FIXATION"))
    # the search for the smaller board has a share of the budget; when it has none, the first-fit layout stands
    monkeypatch.setattr(kernel, "SMALLER_BOARD_SHARE", 0)
    r = resolve_assembly(pair(62.5), catalog())
    assert (r.board.board.size_x_mm, r.board.board.size_y_mm) == (200.0, 100.0)
    assert layout(r) == [("L1", (3, 1), 0.0), ("L2", (6, 1), 0.0)]


def test_a_first_fit_board_no_smaller_board_takes_the_footprint_of_is_not_searched_again(monkeypatch):
    """The smaller boards are tried again only where the first-fit placement failed on a board that took the design's
    footprint: when every smaller board is too small for the fixed footprint (the issue-260 design, whose kinematic
    mirror mount needs the 450 x 300 board), no placement of the forks can change that, and no extra search runs."""

    calls = []
    monkeypatch.setattr(kernel, "_realize", lambda *args, _realize=kernel._realize, **kwargs: calls.append(args[0].smaller_than) or _realize(*args, **kwargs))
    r = resolve_assembly(pair(140.0), catalog())
    assert all(c["fits"] or c["reason"] == "FOOTPRINT_EXCEEDS_BOARD" for c in r.board.considered)
    assert calls == [None]


def test_without_revisits_the_search_is_the_first_fit_one(monkeypatch):
    """The complete search's first descent is the first-fit placement: with no revisit allowed it refuses the 30 mm pair
    as the first-fit search does."""

    monkeypatch.setattr(kernel, "_unbounded_probe", lambda *args: None)
    with pytest.raises(AssemblyResolutionRefusal) as refusal:
        resolve_assembly(pair(30.0), catalog(policy=ResolverPolicyV1(max_layout_backtracks=0)))  # no revisit allowed
    refused = refusal.value
    assert (refused.details["instance_id"], refused.details["reason"], refused.details["with"]) == ("L2", "NO_FIXING_HOLE", "L1")


def test_a_truly_infeasible_pair_is_refused_within_the_budget_with_a_position_that_works():
    """20 mm apart no fixation of either fork leaves the other a hole: refused without spending the budget, and the
    suggested position is the nearest one at which the whole design is laid out again (not the ~62 mm a first-fit
    placement needed)."""

    budget = ResolutionBudget(12000)
    refused = refusal_of(filters(20.0), budget=budget)
    assert budget.exhausted_by is None and "search_budget_exhausted" not in refused.details
    s = refused.details["suggestion"]
    assert s["action"] == "ADJUST_COMPONENT_SPACING" and s["beam_direction_design"] == [1.0, 0.0, 0.0]
    assert s["instance_id"] == "F2" and s["suggested_position_design_mm"][0] < 30.0
    assert len(resolve_assembly(filters(s["suggested_position_design_mm"][0]), catalog()).chains) == 2
    # no board was found on which it fits, so a larger one is not offered
    assert s["larger_breadboard_may_help"] is False and "smallest_fitting_board_mm" not in s


def test_a_truly_infeasible_lens_pair_is_given_the_pitch_it_needs_not_a_move_of_one_lens():
    """the same spacing between two lenses is a lens pair. Moving L2 alone would change the pair's
    optics (before: "move L2 to 27.5 mm"), so no single-lens move is suggested; the refusal states the least support
    pitch of the linked pair instead, and a design that makes that room is laid out."""

    refused = refusal_of(pair(20.0))
    s = refused.details["suggestion"]
    assert "suggested_position_design_mm" not in s and "suggested_positions_design_mm" not in s
    assert s["conflicting_pairs"] == [{"instance_id": "L2", "with": "L1", "spacing_mm": 20.0, "min_support_pitch_mm": s["min_support_pitch_mm"],
                                       "beam_direction_design": [1.0, 0.0, 0.0], "linked": True}]
    assert len(resolve_assembly(pair(s["min_support_pitch_mm"]), catalog()).chains) == 2


def _fold(spacing: float) -> AssemblyResolutionRequestV2:
    """Source along +X -> M1 folds to +Y -> M2 ``spacing`` mm on folds back to +X -> lens 100 mm on -> screen."""

    return AssemblyResolutionRequestV2(
        optics=(mirror("M1"), mirror("M2", position=(0.0, spacing, 0.0), normal=(SQRT_HALF, -SQRT_HALF, 0.0)),
                lens("L1", position=(100.0, spacing, 0.0))),
        externals=(source(),), observations=(screen(position=(176.5, spacing, 0.0)),))


def _moved(request: AssemblyResolutionRequestV2, names: set[str], shift: tuple[float, float, float]) -> AssemblyResolutionRequestV2:
    def at(p):
        return tuple(a + b for a, b in zip(p, shift))

    from dataclasses import replace
    return replace(request,
                   optics=tuple(replace(o, position_design=at(o.position_design)) if o.instance_id in names else o for o in request.optics),
                   observations=tuple(replace(o, position_design=at(o.position_design)) if o.observation_id in names else o
                                      for o in request.observations))


def test_a_mirror_refusal_gives_a_position_that_works_and_what_moves_with_it():
    """a fold mirror cannot move alone along its beam (the beam would miss what follows it). Its refusal names
    the nearest position along its incoming beam at which it works, moved together with every element after it, and
    following it literally resolves."""

    refused = refusal_of(_fold(20.0))
    d = refused.details
    assert (d["instance_id"], d["with"]) == ("M2", "M1")
    s = d["suggestion"]
    assert s["action"] == "ADJUST_COMPONENT_SPACING" and s["beam_direction_design"] == [0.0, 1.0, 0.0]
    assert s["moved_with"] == ["L1", "focal_plane"]
    offset = s["nearest_fixable_offset_mm"]
    assert s["suggested_position_design_mm"] == [0.0, 20.0 + offset, 0.0]
    followed = _moved(_fold(20.0), {"M2", "L1", "focal_plane"}, (0.0, offset, 0.0))
    assert len(resolve_assembly(followed, catalog()).chains) == 3


def test_the_extra_spacing_clears_the_turned_supports_it_names():
    """``min_extra_spacing_mm`` is measured on the clearance-inflated, turned bodies and rounded up, so
    moving the named support by it away from the other never brings the same pair back as a collision."""

    beam_y = look((0.0, 1.0, 0.0))
    for k in range(10):
        spacing = 10.0 + 0.5 * k
        request = AssemblyResolutionRequestV2(optics=(mirror("M1"), lens("L1", position=(0.0, spacing, 0.0), rotation=beam_y)),
                                              externals=(source(),),
                                              observations=(screen(position=(0.0, spacing + 80.0, 0.0), rotation=beam_y),))
        d = refusal_of(request).details
        assert (d["instance_id"], d["reason"], d["with"]) == ("L1", "SUPPORTS_COLLIDE", "M1")
        extra = d["suggestion"]["min_extra_spacing_mm"]
        assert extra == math.ceil(extra * 10) / 10
        moved = AssemblyResolutionRequestV2(optics=(mirror("M1"), lens("L1", position=(0.0, spacing + extra, 0.0), rotation=beam_y)),
                                            externals=(source(),),
                                            observations=(screen(position=(0.0, spacing + extra + 80.0, 0.0), rotation=beam_y),))
        try:
            resolve_assembly(moved, catalog())
        except AssemblyResolutionRefusal as again:
            assert (again.details.get("reason"), again.details.get("with")) != ("SUPPORTS_COLLIDE", "M1"), spacing


# ------------------------------------------------------------------------------------------------ board attribution


def test_a_limit_that_excludes_the_board_a_layout_was_found_on_names_that_board():
    """25 mm apart the pair needs the 200 x 100 board (on the 150 x 100 board no placement exists): under a 150 x 100
    limit the refusal is the limit, with the board on which the layout was found -- not the conflict on the smaller
    board."""

    refused = refusal_of(pair(25.0, max_board_size_mm=(150.0, 100.0)))
    assert refused.code == "BREADBOARD_CONSTRAINT_UNSATISFIABLE"
    assert refused.details["reason"] == "BREADBOARD_LIMIT_TOO_SMALL"
    assert refused.details["suggestion"] == {"action": "ALLOW_LARGER_BREADBOARD", "smallest_fitting_board_mm": [200.0, 100.0]}


def test_a_limit_below_the_footprint_is_reported_as_the_limit_not_as_a_conflict_on_an_excluded_board():
    """L3 stands 20 mm behind L2 (no placement on any board) and the design is 245 mm long: under a 150 x 100 limit no
    allowed board takes even the footprint. Before, the hole conflict found on the boards the limit excludes was
    reported; now the limit, with the footprint and the limit (no fitting board is named: none was found)."""

    request = AssemblyResolutionRequestV2(optics=(lens("L1"), lens("L2", position=(200.0, 0.0, 0.0)), lens("L3", position=(220.0, 0.0, 0.0))),
                                          externals=(source(),), max_board_size_mm=(150.0, 100.0))
    refused = refusal_of(request)
    assert refused.code == "BREADBOARD_CONSTRAINT_UNSATISFIABLE" and refused.details["reason"] == "BREADBOARD_LIMIT_TOO_SMALL"
    assert refused.details["max_board_size_mm"] == [150.0, 100.0] and refused.details["footprint_mm"][0] > 150.0
    assert "smallest_fitting_board_mm" not in refused.details
    # : the least the limit has to allow -- the smallest standard board the footprint fits on, edge margin
    # included -- so that the suggestion leaves a number to apply
    assert refused.details["suggestion"] == {"action": "ALLOW_LARGER_BREADBOARD", "smallest_board_for_footprint_mm": [300.0, 150.0]}
    # applying it brings the design's own conflict (L3 20 mm behind L2), not the limit again
    again = refusal_of(replace(request, max_board_size_mm=(300.0, 150.0)))
    assert again.code == "MECHANICAL_LAYOUT_CONFLICT" and (again.details["instance_id"], again.details["with"]) == ("L3", "L2")


def test_a_conflict_within_the_limit_with_no_fitting_board_anywhere_is_the_conflict():
    refused = refusal_of(pair(20.0, max_board_size_mm=(150.0, 100.0)))
    assert refused.code == "MECHANICAL_LAYOUT_CONFLICT" and refused.details["reason"] == "NO_FIXING_HOLE"
    assert refused.details["suggestion"]["larger_breadboard_may_help"] is False


def _row(xs) -> AssemblyResolutionRequestV2:
    return AssemblyResolutionRequestV2(optics=tuple(lens(f"L{i + 1}", position=(x, 0.0, 0.0), mechanics=ND_FILTER) for i, x in enumerate(xs)),
                                       externals=(source(),), observations=(screen(position=(xs[-1] + 80.0, 0.0, 0.0)),))


def test_a_filter_in_a_tight_row_gets_a_move_that_resolves():
    """in a row of filters 30, 22.5 and 30 mm apart, L3 is hemmed in by L2 and L4. Before, the refusal had no
    move to apply; it now gives the nearest position of L3 (with what moves with it, if anything) that the resolution
    itself lays out -- found the way the search does, with its mount fallbacks -- and following it literally resolves."""

    xs = (0.0, 30.0, 52.5, 82.5)
    d = refusal_of(_row(xs)).details
    assert (d["instance_id"], d["reason"], d["with"]) == ("L3", "NO_FIXING_HOLE", "L2")
    s = d["suggestion"]
    assert s["instance_id"] == "L3" and s["beam_direction_design"] == [1.0, 0.0, 0.0]
    offset = s["nearest_fixable_offset_mm"]
    assert s["suggested_position_design_mm"] == [52.5 + offset, 0.0, 0.0]
    moved = {"L3", *s.get("moved_with", ())}
    followed = AssemblyResolutionRequestV2(
        optics=tuple(lens(f"L{i + 1}", position=(x + (offset if f"L{i + 1}" in moved else 0.0), 0.0, 0.0), mechanics=ND_FILTER)
                     for i, x in enumerate(xs)),
        externals=(source(),), observations=(screen(position=(xs[-1] + 80.0 + (offset if "focal_plane" in moved else 0.0), 0.0, 0.0)),))
    assert len(resolve_assembly(followed, catalog()).chains) == 4
