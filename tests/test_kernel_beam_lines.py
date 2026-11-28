"""each spectral line of each light source is shown only on the branches it takes.

The nominal trace stays geometric (every arm of every splitter is kept clear of hardware); ``route_beam_lines`` is what
the assembly presentations draw and list: a dichroic sends each line down one arm, a blocking filter stops a line, a
plain beamsplitter sends every line down both arms, a designed weak pick-off is drawn, and the beams are numbered in the
Engine's path order (``<source id>#<line index>``) whatever order the Project lists its sources in.
"""

from __future__ import annotations

import math

import pytest

from os_optics.assembly_plan.kernel import ExternalRequest, ObservationRequest
from os_optics.assembly_plan.kernel.beam import STEP_EMIT, STEP_OBSERVE, STEP_SPLIT_REFLECT, STEP_SPLIT_TRANSMIT, STEP_TRANSMIT, trace_beam_paths
from os_optics.assembly_plan.kernel.beam_lines import route_beam_lines

from os_optics._vendor.shared_contracts.optical_design.design_content import TargetV1

from kernel_fixture import SQRT_HALF, look, screen, source

IR, GREEN = 1064.0, 532.0
BEAM_X, BEAM_Y = look((1.0, 0.0, 0.0)), look((0.0, 1.0, 0.0))
FOLD = look((-SQRT_HALF, SQRT_HALF, 0.0))  # a 45 deg splitter at the origin: transmits along +X, reflects into +Y


def target(observation: str, *, metric: str = "power_W", source: str | None = None, wavelength: float | None = None,
           **bounds: float) -> TargetV1:
    return TargetV1(target_id=f"t-{observation}", metric=metric, observation_id=observation, source_id=source,
                    wavelength_nm=wavelength, **bounds)


def _part(instance: str, category: str, position, rotation) -> ExternalRequest:
    return ExternalRequest(instance, "optic", position, rotation, (), beam_category=category, size_mm=25.4)


def separator(*sources: str, filters: bool = True):
    """Two co-located lasers -> dichroic DM1 (reflects 1064 nm into +Y, transmits 532 nm along +X); SP1 (short-pass)
    on the green arm and LP1 (long-pass) on the IR arm; focus_G and focus_IR at the ends."""

    parts = [source(s, position=(-150.0, 0.0, 0.0)) for s in sources] + [_part("DM1", "BEAMSPLITTER_PLATE", (0.0, 0.0, 0.0), FOLD)]
    if filters:
        parts += [_part("SP1", "FILTER", (40.0, 0.0, 0.0), BEAM_X), _part("LP1", "FILTER", (0.0, 40.0, 0.0), BEAM_Y)]
    observations = (screen("focus_G", position=(100.0, 0.0, 0.0)), screen("focus_IR", position=(0.0, 100.0, 0.0), rotation=BEAM_Y))
    return trace_beam_paths(tuple(parts), (), observations)


#: (element, wavelength) -> (transmitted share, reflected share)
SPECTRA = {
    ("DM1", IR): (0.05, 0.95), ("DM1", GREEN): (0.9, 0.1),
    ("SP1", IR): (1e-5, 0.0), ("SP1", GREEN): (0.9, 0.0),
    ("LP1", IR): (0.9, 0.0), ("LP1", GREEN): (1e-5, 0.0),
    ("BS", IR): (0.5, 0.5), ("BS", GREEN): (0.5, 0.5),
    ("PICK", IR): (0.9, 0.1), ("PICK", GREEN): (0.9, 0.1),
}


def response(element: str, step: str, wavelength: float) -> float | None:
    spectrum = SPECTRA.get((element, wavelength))
    if spectrum is None:
        return None
    return spectrum[1] if step == STEP_SPLIT_REFLECT else spectrum[0]


def _drawn(axes) -> set[tuple[float, tuple[str, ...]]]:
    return {(a.wavelength_nm, a.through) for a in axes}


def test_the_trace_records_what_the_beam_does_at_each_point():
    paths = separator("SRC_IR")
    assert [(p.through, p.steps) for p in paths] == [
        (("SRC_IR", "DM1", "SP1", "focus_G"), (STEP_EMIT, STEP_SPLIT_TRANSMIT, STEP_TRANSMIT, STEP_OBSERVE)),
        (("DM1", "LP1", "focus_IR"), (STEP_SPLIT_REFLECT, STEP_TRANSMIT, STEP_OBSERVE)),
    ]


def test_a_tilted_lens_is_on_the_path_within_the_callers_lens_bound():
    """A lens yawed 10 degrees to the beam: visited when the caller's ``lens_tilt_cosine`` admits 10 degrees, passed by
    without it (the trace's own default is the paraxial ``AXIAL_COSINE``); a filter is bound by ``plate_tilt_cosine``
    only."""

    yaw = math.radians(10.0)
    tilted = look((math.cos(yaw), math.sin(yaw), 0.0))
    observations = (screen("cam", position=(100.0, 0.0, 0.0)),)
    for category, kwargs, visited in (
        ("LENS", {}, False),
        ("LENS", {"lens_tilt_cosine": math.cos(math.radians(30.0))}, True),
        ("LENS", {"plate_tilt_cosine": math.cos(math.radians(30.0))}, False),
        ("FILTER", {"lens_tilt_cosine": math.cos(math.radians(30.0))}, False),
        ("FILTER", {"plate_tilt_cosine": math.cos(math.radians(30.0))}, True),
    ):
        parts = (source("SRC", position=(-100.0, 0.0, 0.0)), _part("X", category, (0.0, 0.0, 0.0), tilted))
        (path,) = trace_beam_paths(parts, (), observations, **kwargs)
        assert ("X" in path.through) is visited, (category, kwargs)


def _splitter_tree(count: int):
    """``count`` plate beamsplitters along +X, each sending a branch to +Y onto one wide plane: ``count + 1`` paths."""

    parts = [source("SRC", position=(-100.0, 0.0, 0.0))] + [_part(f"BS{k}", "BEAMSPLITTER_PLATE", (k * 30.0, 0.0, 0.0), FOLD)
                                                              for k in range(count)]
    corners = ((-3000.0, -3000.0), (3000.0, -3000.0), (3000.0, 3000.0), (-3000.0, 3000.0))
    wide = ObservationRequest("top", (2000.0, 200.0, 0.0), BEAM_Y, corners)  # beyond the splitters: every branch ends on it
    return tuple(parts), (wide,)


def test_a_trace_that_reaches_its_branch_cap_says_where_and_a_higher_cap_traces_it_all():
    """16 splitters give 17 paths: with the cap of 16 branches the trace stops at the last splitter and says so; with
    32 every path is traced, and the first 16 keep their keys."""

    parts, planes = _splitter_tree(16)
    stopped: list = []
    capped = trace_beam_paths(parts, (), planes, max_branches=16, truncated=stopped)
    assert len(capped) == 16
    assert stopped == [{"path_key": "beam-1", "limit": "BRANCHES", "value": 16, "at": "BS15"}]
    complete: list = []
    full = trace_beam_paths(parts, (), planes, max_branches=32, truncated=complete)
    assert complete == [] and len(full) == 17
    assert [p.path_key for p in full][:16] == [p.path_key for p in capped]
    # without a record the trace is the same as before (the default caps)
    assert trace_beam_paths(parts, (), planes) == capped


def test_a_trace_that_reaches_its_point_cap_says_where():
    parts, planes = _splitter_tree(3)
    stopped: list = []
    (main, *_) = trace_beam_paths(parts, (), planes, max_points=3, truncated=stopped)
    assert main.through == ("SRC", "BS0", "BS1")
    assert {"path_key": "beam-1", "limit": "POINTS", "value": 3, "at": "BS2"} in stopped
    assert trace_beam_paths(parts, (), planes, max_points=64, truncated=(complete := [])) and complete == []


def test_branch_keys_stay_letters_and_never_read_as_another_branch():
    from os_optics.assembly_plan.kernel.beam import branch_suffix, is_child_key

    assert [branch_suffix(n) for n in range(16)] == list("abcdefghijklmnop")  # the keys every design has had
    assert branch_suffix(24) == "y" and branch_suffix(25) == "za" and branch_suffix(50) == "zza"
    assert is_child_key("beam-1", "beam-1a") and is_child_key("beam-1", "beam-1za")
    assert not is_child_key("beam-1", "beam-1ab") and not is_child_key("beam-1", "beam-1z") and not is_child_key("beam-1", "beam-1")


def test_a_dichroic_and_its_filters_send_each_line_down_its_own_arm_only():
    # SRC_IR first in the Project, as in the audit's separator; the geometric trace sends both down both arms
    paths = separator("SRC_IR", "SRC_G")
    assert len(paths) == 4
    axes = route_beam_lines(paths, {"SRC_IR": (IR,), "SRC_G": (GREEN,)}, response)
    assert _drawn(axes) == {
        (GREEN, ("SRC_G", "DM1", "SP1", "focus_G")),
        (IR, ("SRC_IR", "DM1")),  # the 5 % IR leak through the dichroic is not drawn (and SP1 blocks it)
        (IR, ("DM1", "LP1", "focus_IR")),
    }
    # numbered in the Engine's path order (SRC_G#0 before SRC_IR#0), not the Project's source order
    assert [(a.path_key, a.source_id) for a in axes] == [("beam-1", "SRC_G"), ("beam-2", "SRC_IR"), ("beam-2a", "SRC_IR")]


def test_one_two_line_source_is_split_by_line():
    axes = route_beam_lines(separator("SRC"), {"SRC": (IR, GREEN)}, response)
    assert [(a.path_key, a.wavelength_nm, a.through) for a in axes] == [
        ("beam-1", IR, ("SRC", "DM1")),
        ("beam-1a", IR, ("DM1", "LP1", "focus_IR")),
        ("beam-2", GREEN, ("SRC", "DM1", "SP1", "focus_G")),
    ]


def test_a_blocking_filter_stops_the_only_light_on_an_arm():
    # one IR laser: the green arm carries only the 5 % IR leak; it is drawn up to the short-pass filter, never past it
    axes = route_beam_lines(separator("SRC_IR"), {"SRC_IR": (IR,)}, response)
    assert _drawn(axes) == {(IR, ("SRC_IR", "DM1", "SP1")), (IR, ("DM1", "LP1", "focus_IR"))}


def test_a_leak_on_an_arm_without_a_main_beam_is_still_light_on_that_arm():
    # no filters: the green arm carries only the IR leak, which nothing stops
    axes = route_beam_lines(separator("SRC_IR", filters=False), {"SRC_IR": (IR,)}, response)
    assert _drawn(axes) == {(IR, ("SRC_IR", "DM1", "focus_G")), (IR, ("DM1", "focus_IR"))}


def _plate(name: str):
    parts = (source("SRC", position=(-150.0, 0.0, 0.0)), _part(name, "BEAMSPLITTER_PLATE", (0.0, 0.0, 0.0), FOLD))
    observations = (screen("out_T", position=(100.0, 0.0, 0.0)), screen("out_R", position=(0.0, 100.0, 0.0), rotation=BEAM_Y))
    return trace_beam_paths(parts, (), observations)


def test_a_plate_beamsplitter_sends_every_line_down_both_arms():
    axes = route_beam_lines(_plate("BS"), {"SRC": (IR, GREEN)}, response)
    assert _drawn(axes) == {(w, through) for w in (IR, GREEN) for through in (("SRC", "BS", "out_T"), ("BS", "out_R"))}


def test_a_designed_weak_pick_off_is_drawn_and_an_undesigned_line_on_it_is_not():
    paths = _plate("PICK")  # 10 % reflection: a weak branch
    lines = {"SRC": (IR, GREEN)}
    # the pick-off's target asks for the IR line only
    axes = route_beam_lines(paths, lines, response, targets=[target("out_R", source="SRC", wavelength=IR, minimum=1e-5)])
    assert _drawn(axes) == {(IR, ("SRC", "PICK", "out_T")), (GREEN, ("SRC", "PICK", "out_T")), (IR, ("PICK", "out_R"))}
    # without a target the weak arm still carries light, and no main beam crosses it: both lines are drawn there
    assert _drawn(route_beam_lines(paths, lines, response)) == {
        (w, through) for w in (IR, GREEN) for through in (("SRC", "PICK", "out_T"), ("PICK", "out_R"))}


def test_an_absorbing_plane_stops_the_light():
    parts = (source("SRC", position=(-150.0, 0.0, 0.0)),)
    observations = (screen("det", position=(0.0, 0.0, 0.0)), screen("far", position=(100.0, 0.0, 0.0)))
    paths = trace_beam_paths(parts, (), observations)
    assert _drawn(route_beam_lines(paths, {"SRC": (IR,)}, response)) == {(IR, ("SRC", "det", "far"))}
    assert _drawn(route_beam_lines(paths, {"SRC": (IR,)}, response, absorbing=frozenset({"det"}))) == {(IR, ("SRC", "det"))}


def test_an_element_without_spectral_data_does_not_stop_a_line():
    axes = route_beam_lines(separator("SRC_IR", "SRC_G"), {"SRC_IR": (IR,), "SRC_G": (GREEN,)}, lambda e, s, w: None)
    assert len(axes) == 4 and {a.through for a in axes} == {("SRC_IR", "DM1", "SP1", "focus_G"), ("DM1", "LP1", "focus_IR"),
                                                             ("SRC_G", "DM1", "SP1", "focus_G")}


def test_paths_without_steps_are_refused():
    from dataclasses import replace

    paths = tuple(replace(p, steps=()) for p in separator("SRC_IR"))
    with pytest.raises(ValueError):
        route_beam_lines(paths, {"SRC_IR": (IR,)}, response)


def test_axis_points_are_the_trace_points():
    paths = separator("SRC_IR", "SRC_G")
    by_through = {p.through: p.points_design for p in paths}
    for axis in route_beam_lines(paths, {"SRC_IR": (IR,), "SRC_G": (GREEN,)}, response):
        full = next(points for through, points in by_through.items() if through[: len(axis.through)] == axis.through)
        assert all(math.dist(a, b) == 0.0 for a, b in zip(axis.points_design, full))


# ------------------------------------------------------------------------------------------- review round

#: (element, wavelength) -> (transmitted share, best transmitted share): a short-pass filter's stop band, a neutral
#: density filter passing every line at its own best share.
NEUTRAL = {("ND", IR): (1e-4, 1e-4), ("ND", GREEN): (1e-4, 1e-4), ("SP1", IR): (1e-5, 0.9), ("SP1", GREEN): (0.9, 0.9)}


def relative(element: str, step: str, wavelength: float):
    if (element, wavelength) in NEUTRAL:
        return NEUTRAL[(element, wavelength)]
    return response(element, step, wavelength)


@pytest.mark.parametrize("leak_bound", [
    {"minimum": 0.0, "maximum": 1e-5},  # a leak bound written with a zero minimum
    {"target_value": 0.0, "tolerance_abs": 1e-5},  # ... or as zero within a tolerance
    {"metric": "position_u_mm", "minimum": -0.5, "maximum": 0.5},  # a non-power target on the blocked leak
])
def test_a_target_on_a_blocked_leak_never_draws_it(leak_bound):
    paths = separator("SRC_IR", "SRC_G")
    lines = {"SRC_IR": (IR,), "SRC_G": (GREEN,)}
    bound = target("focus_G", source="SRC_IR", **leak_bound)
    axes = route_beam_lines(paths, lines, relative, targets=[bound])
    assert (IR, ("SRC_IR", "DM1")) in _drawn(axes)
    assert not any(a.wavelength_nm == IR and {"SP1", "focus_G"} & set(a.through) for a in axes)


def test_a_neutral_attenuator_never_blocks_and_a_stop_band_always_does():
    parts = (source("SRC", position=(-150.0, 0.0, 0.0)), _part("ND", "FILTER", (0.0, 0.0, 0.0), BEAM_X))
    paths = trace_beam_paths(parts, (), (screen("cam", position=(100.0, 0.0, 0.0)),))
    for targets in ((), (target("cam", source="SRC", maximum=2e-4),)):
        assert _drawn(route_beam_lines(paths, {"SRC": (GREEN, IR)}, relative, targets=targets)) == {
            (GREEN, ("SRC", "ND", "cam")), (IR, ("SRC", "ND", "cam"))}
    # without the best share the same 1e-4 counts as blocking (the absolute rule an unknown element keeps)
    assert _drawn(route_beam_lines(paths, {"SRC": (GREEN,)}, lambda e, s, w: 1e-4)) == {(GREEN, ("SRC", "ND"))}


def _three_line_router():
    """A three-line laser on two dichroics: DMa reflects 450 nm into B, DMb reflects 532 nm into G, 633 nm goes on to R."""

    parts = (source("SRC", position=(-150.0, 0.0, 0.0)), _part("DMa", "BEAMSPLITTER_PLATE", (0.0, 0.0, 0.0), FOLD),
             _part("DMb", "BEAMSPLITTER_PLATE", (60.0, 0.0, 0.0), FOLD))
    observations = (screen("B", position=(0.0, 100.0, 0.0), rotation=BEAM_Y), screen("G", position=(60.0, 100.0, 0.0), rotation=BEAM_Y),
                    screen("R", position=(150.0, 0.0, 0.0)))
    reflect = {("DMa", 450.0): 0.95, ("DMa", GREEN): 0.1, ("DMa", 633.0): 0.1,
               ("DMb", 450.0): 0.95, ("DMb", GREEN): 0.95, ("DMb", 633.0): 0.1}

    def split(element: str, step: str, wavelength: float):
        r = reflect[(element, wavelength)]
        return (r, 0.95) if step == STEP_SPLIT_REFLECT else (1.0 - r, 0.9)

    return trace_beam_paths(parts, (), observations), split


def test_aggregate_targets_promote_what_their_plane_mostly_receives_not_a_leak_of_the_first_line():
    paths, split = _three_line_router()
    targets = [target(plane, minimum=5e-4) for plane in ("B", "G", "R")]  # no source, no line: the summed power
    axes = route_beam_lines(paths, {"SRC": (450.0, GREEN, 633.0)}, split, powers={"SRC": (1e-3, 1e-3, 1e-3)}, targets=targets)
    ends = {(a.wavelength_nm, a.through[-1]) for a in axes}
    assert {(450.0, "B"), (GREEN, "G"), (633.0, "R")} <= ends
    assert not {(450.0, "G"), (450.0, "R"), (GREEN, "R"), (633.0, "G")} & ends  # the 450 nm leaks stay leaks


def test_an_aggregate_pick_off_target_promotes_both_sources_on_the_weak_arm():
    parts = (source("SRC_G", position=(-150.0, 0.0, 0.0)), source("SRC_R", position=(-150.0, 0.0, 0.0)),
             _part("PICK", "BEAMSPLITTER_PLATE", (0.0, 0.0, 0.0), FOLD))
    observations = (screen("MAIN", position=(150.0, 0.0, 0.0)), screen("ARM", position=(0.0, 100.0, 0.0), rotation=BEAM_Y))
    paths = trace_beam_paths(parts, (), observations)
    lines = {"SRC_G": (GREEN,), "SRC_R": (IR,)}
    weak_arm = route_beam_lines(paths, lines, response, targets=[target("ARM", minimum=1e-4)])
    assert {(a.wavelength_nm, a.through) for a in weak_arm if a.through[-1] == "ARM"} == {(GREEN, ("PICK", "ARM")), (IR, ("PICK", "ARM"))}
    # a target that names no source sums both: each carries half of what the arm receives, so both are designed light
    # (without the target they are still drawn: weak, and no main beam crosses the arm)
    assert {a.through for a in route_beam_lines(paths, lines, response)} == {a.through for a in weak_arm}


def test_a_blocked_interferometer_arm_reaching_the_designed_plane_is_not_drawn_through_its_filter():
    """A Mach-Zehnder of two plates with a long-pass filter on one arm: the 532 nm light of that arm is rejected there,
    though the arm reaches the plane whose target asks for 532 nm."""

    parts = (source("S532", position=(-150.0, 0.0, 0.0)), _part("BS1", "BEAMSPLITTER_PLATE", (0.0, 0.0, 0.0), FOLD),
             _part("F_R", "FILTER", (0.0, 50.0, 0.0), BEAM_Y),
             _part("MR", "MIRROR", (0.0, 100.0, 0.0), look((SQRT_HALF, -SQRT_HALF, 0.0))),
             _part("MT", "MIRROR", (100.0, 0.0, 0.0), look((-SQRT_HALF, SQRT_HALF, 0.0))),
             _part("BS2", "BEAMSPLITTER_PLATE", (100.0, 100.0, 0.0), FOLD))
    paths = trace_beam_paths(parts, (), (screen("P", position=(200.0, 100.0, 0.0)),))

    def mz(element: str, step: str, wavelength: float):
        if element == "F_R":
            return 5e-6, 0.9
        return (0.5, 0.5) if element.startswith("BS") else None

    axes = route_beam_lines(paths, {"S532": (GREEN,)}, mz, targets=[target("P", source="S532", wavelength=GREEN, minimum=1e-4)])
    assert all("F_R" not in a.through[:-1] for a in axes)  # the arm's light ends at the filter
    assert any(a.through[-1] == "P" for a in axes)  # the other arm reaches the plane
