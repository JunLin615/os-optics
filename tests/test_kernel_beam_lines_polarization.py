"""``route_beam_lines`` with a polarized response.

Each line carries its source's polarization along the geometric paths; an element's share is the one for the
polarization that arrives there. These tests use a small synthetic response (polarization ``"s"`` / ``"p"`` / ``None``
for not known) so the display rule is checked on its own; the Engine's own routing is checked against traced powers in
``opensci-engine/tests/test_polarization_routing.py`` and end to end in the project API's tests.
"""

from __future__ import annotations

import math

from os_optics.assembly_plan.kernel import ExternalRequest
from os_optics.assembly_plan.kernel.beam import STEP_SPLIT_REFLECT, trace_beam_paths
from os_optics.assembly_plan.kernel.beam_lines import (
    POLARIZATION_DEPENDENT,
    POLARIZATION_NOT_RESOLVED,
    ROLE_PRIMARY,
    ROLE_SECONDARY,
    LinePassage,
    route_beam_lines,
)

from kernel_fixture import SQRT_HALF, look, screen, source

IR = 1064.0
BEAM_X = look((1.0, 0.0, 0.0))
FOLD = look((-SQRT_HALF, SQRT_HALF, 0.0))  # a 45 deg splitter at the origin: transmits along +X, reflects into +Y
#: A polarizing splitter: (transmitted, reflected) share per arriving polarization.
PBS = {"p": (0.9, 0.1), "s": (0.0009, 0.95)}


def _part(instance: str, category: str, position, rotation) -> ExternalRequest:
    return ExternalRequest(instance, "optic", position, rotation, (), beam_category=category, size_mm=25.4)


class Synthetic:
    """``ROT`` (a half-wave plate turning s into p and back), ``UNKNOWN`` (an element without polarization facts),
    ``PBS`` (a polarizing splitter) and ``POL`` (a polarizer passing p)."""

    def __init__(self, sources: dict[str, str | None]) -> None:
        self.sources = sources
        self.calls: list[tuple] = []

    def start(self, source_id: str):
        return self.sources[source_id]

    def step(self, element, step, wavelength_nm, point, direction_in, direction_out, state):
        self.calls.append((element, step, direction_in, direction_out, state))
        if element == "ROT":
            return LinePassage(share=1.0, state=None if state is None else {"s": "p", "p": "s"}[state])
        if element == "UNKNOWN":
            return None
        if element == "FALLBACK":  # not followed: its unpolarized published share, no polarization beyond
            return LinePassage(share=0.5, state=None, resolved=False)
        if element == "PBS":
            k = 1 if step == STEP_SPLIT_REFLECT else 0
            if state is None:
                lo, hi = sorted((PBS["s"][k], PBS["p"][k]))
                return LinePassage(share=(lo + hi) / 2.0, best=0.5, state="p" if k == 0 else None, share_range=(lo, hi))
            return LinePassage(share=PBS[state][k], best=0.5, state=state)
        if element == "POL":
            if state is None:
                return LinePassage(share=0.45, state="p", share_range=(1e-5, 0.9))
            return LinePassage(share=0.9 if state == "p" else 1e-5, state="p")
        return LinePassage(share=1.0, state=state)


def splitter(*before: str, after_t: tuple[str, ...] = ()):
    """SRC (+X) -> the elements ``before`` -> PBS at the origin (out_T along +X, out_R along +Y) -> ``after_t`` on the
    transmitted arm."""

    parts = [source("SRC", position=(-150.0, 0.0, 0.0))]
    parts += [_part(name, "WAVEPLATE", (-100.0 + 30.0 * i, 0.0, 0.0), BEAM_X) for i, name in enumerate(before)]
    parts.append(_part("PBS", "BEAMSPLITTER_PLATE", (0.0, 0.0, 0.0), FOLD))
    parts += [_part(name, "POLARIZER", (40.0 + 10.0 * i, 0.0, 0.0), BEAM_X) for i, name in enumerate(after_t)]
    observations = (screen("out_T", position=(100.0, 0.0, 0.0)), screen("out_R", position=(0.0, 100.0, 0.0), rotation=look((0.0, 1.0, 0.0))))
    return trace_beam_paths(tuple(parts), (), observations)


def _axes(paths, polarization: str | None, **kwargs):
    response = Synthetic({"SRC": polarization})
    return route_beam_lines(paths, {"SRC": (IR,)}, None, polarized=response, **kwargs), response


def _by_end(axes):
    return {a.through[-1]: a for a in axes}


def test_an_s_polarized_line_takes_the_reflected_arm_and_its_transmitted_leak_is_secondary():
    axes, _ = _axes(splitter(), "s")
    ends = _by_end(axes)
    assert ends["out_R"].through == ("PBS", "out_R") and ends["out_R"].segment_roles is None  # the main beam
    # the 0.09 % leak is the only light on the transmitted arm: drawn, as a secondary stretch after the splitter
    assert ends["out_T"].through == ("SRC", "PBS", "out_T")
    assert ends["out_T"].segment_roles == (ROLE_PRIMARY, ROLE_SECONDARY)
    assert not any(a.notes for a in axes)


def test_a_half_wave_plate_turning_s_into_p_sends_the_line_through():
    axes, _ = _axes(splitter("ROT"), "s")
    ends = _by_end(axes)
    assert ends["out_T"].segment_roles is None
    assert ends["out_R"].segment_roles == (ROLE_SECONDARY,)  # Rp = 10 %: weak, and nothing else on that arm


def test_a_crossed_polarizer_stops_the_line():
    axes, _ = _axes(splitter(after_t=("POL",)), "s")
    # s through the splitter's leak, then a polarizer passing p: blocked at it (absolute share below 1e-3)
    assert _by_end(axes).get("out_T") is None
    assert any(a.through[-1] == "POL" for a in axes)


def test_an_unknown_polarization_at_a_polarizing_split_is_noted_on_the_arms_it_decides():
    axes, response = _axes(splitter("UNKNOWN"), "s")
    ends = _by_end(axes)
    # unpolarized: (0.9 + 0.0009) / 2 and (0.95 + 0.1) / 2, both main beams -- but s or p alone would make one arm weak
    assert ends["out_T"].segment_roles is None and ends["out_R"].segment_roles is None
    assert ends["out_T"].notes == (POLARIZATION_DEPENDENT,) and ends["out_R"].notes == (POLARIZATION_DEPENDENT,)
    # the element without facts loses the polarization: the splitter sees None
    assert [c[4] for c in response.calls if c[0] == "PBS"] == [None, None]


def test_behind_a_polarizer_the_polarization_is_known_again():
    axes, response = _axes(splitter("UNKNOWN", "POL"), "s")
    # whether the polarizer passes the line depends on what arrives (noted), but it emits p whatever arrives
    assert {a.notes for a in axes} == {(POLARIZATION_DEPENDENT,)}
    assert [c[4] for c in response.calls if c[0] == "PBS"] == ["p", "p"]
    assert _by_end(axes)["out_R"].segment_roles == (ROLE_SECONDARY,)
    # a known polarization needs no note
    assert not any(a.notes for a in _axes(splitter("POL"), "p")[0])


def test_a_source_without_a_declared_polarization_routes_as_unpolarized_light_with_notes():
    axes, _ = _axes(splitter(), None)
    assert {a.notes for a in axes} == {(POLARIZATION_DEPENDENT,)}


def test_a_branch_starts_from_the_polarization_and_direction_arriving_at_its_splitter():
    _, response = _axes(splitter("ROT"), "s")
    pbs = [c for c in response.calls if c[0] == "PBS"]
    # the transmitted step (on the root path) and the reflected branch's first step both see p arriving along +X
    unit = lambda v: tuple(round(c / math.sqrt(sum(x * x for x in v)), 12) + 0.0 for c in v)  # noqa: E731
    assert [(c[1], unit(c[2]), unit(c[3]), c[4]) for c in pbs] == [
        ("SPLIT_TRANSMIT", (1.0, 0.0, 0.0), (1.0, 0.0, 0.0), "p"), ("SPLIT_REFLECT", (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), "p")]


def test_each_source_keeps_its_own_polarization():
    parts = (source("S_S", position=(-150.0, 0.0, 0.0)), source("S_P", position=(-150.0, 0.0, 0.0)),
             _part("PBS", "BEAMSPLITTER_PLATE", (0.0, 0.0, 0.0), FOLD))
    observations = (screen("out_T", position=(100.0, 0.0, 0.0)), screen("out_R", position=(0.0, 100.0, 0.0), rotation=look((0.0, 1.0, 0.0))))
    paths = trace_beam_paths(parts, (), observations)
    axes = route_beam_lines(paths, {"S_S": (IR,), "S_P": (IR,)}, None, polarized=Synthetic({"S_S": "s", "S_P": "p"}))
    roles = {(a.source_id, a.through[-1]): a.segment_roles for a in axes}
    # each arm has one main beam: the other source's weak light there is not drawn over it
    assert roles == {("S_S", "PBS"): None, ("S_S", "out_R"): None, ("S_P", "out_T"): None}


def test_an_element_the_routing_could_not_resolve_marks_the_axis_from_there_on():
    """M7: an element whose share came from its unpolarized published data (or outside its characterized range) marks every
    axis that passes it POLARIZATION_NOT_RESOLVED; branches split off after it inherit the note, and the polarizing splitter
    after it is POLARIZATION_DEPENDENT as for any unknown polarization."""

    axes, response = _axes(splitter("FALLBACK"), "s")
    assert {a.notes for a in axes} == {(POLARIZATION_DEPENDENT, POLARIZATION_NOT_RESOLVED)}
    assert [c[4] for c in response.calls if c[0] == "PBS"] == [None, None]
    # resolved elements: no note
    assert not any(POLARIZATION_NOT_RESOLVED in a.notes for a in _axes(splitter("ROT"), "s")[0])
