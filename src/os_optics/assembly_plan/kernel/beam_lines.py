"""The beam a customer sees: each spectral line of each light source drawn only where the design sends it.

``trace_beam_paths`` is geometric: every source goes down every branch of every splitter and through every filter, so
that the kernel keeps every arm clear of hardware. That trace is not what a customer may be shown as light: a dichroic
sends each line down one arm, and a short-pass filter stops the long line. ``route_beam_lines`` follows each spectral
line along the geometric paths with the share of the arriving power each element passes into each branch and the best
share the same element passes at any wavelength (the caller's ``response``: the elements' published spectral data), and
returns one presentation axis per line and branch, cut where that line stops, by the display rule of the Optical
Simulation View (``shared_contracts.optical_view``):

* a branch that receives less than ``SECONDARY_BRANCH_POWER_SHARE`` of the line's arriving power at a split (a dichroic
  leak, a weak reflection) is a weak branch for that line;
* an element that passes the line at less than ``BLOCKED_POWER_SHARE`` of its own best share (a filter's stop band)
  blocks it, and so does an absorbing observation plane; a wavelength-neutral attenuator (a neutral density filter of
  any density) never blocks;
* a path that reaches an observation plane whose Project target asks for the line there (``designed_arrivals``: an
  explicit source and line, or a material share of what an aggregate target sums) is the designed signal: its weak
  stretches are main beams (a weak pick-off), never a blocked one;
* a line is drawn where it is a main beam; on a stretch that no main beam crosses (an arm that only weak light reaches)
  its weak light is drawn too, never blocked light. A line not drawn into a stretch is not drawn beyond it.

Polarization: with a ``polarized`` response (``PolarizedResponse``; the Engine's
``os_optics.optics.polarization.routing`` behind it) each line carries its source's polarization along the path, and
every element's share is the one for the polarization that arrives there (a polarizing splitter sends an s-polarized line
down its reflected arm, crossed polarizers stop it), so the presentation agrees with the Engine's traced Optical
Simulation View. Where the arriving polarization is not known (behind an element without polarization facts) the
unpolarized share decides, and an axis whose drawing another polarization would change carries the note
``POLARIZATION_DEPENDENT``. Where the share at an element is not a polarization-resolved value inside the element's
characterized data (the polarized response could not follow the line through it and gave its unpolarized published
share, or met it outside its characterized angles, wavelengths or polarizations: ``LinePassage.resolved`` false), the
axis carries ``POLARIZATION_NOT_RESOLVED`` from there on. A drawn stretch that only weak light reaches is a ``SECONDARY`` stretch (``segment_roles``),
as the Optical Simulation View draws it.

Identity and order are the Engine's: one presentation beam per light source and spectral line, numbered
``beam-1``, ``beam-2``, ... in the order of the Engine path ids of their first paths (``<source id>#<line index>``), so
the Optical Simulation View and every assembly presentation nest and list the beams in one order. A branch keeps the
letters of its geometric branch (``beam-2a``: the branch of ``beam-2`` its first splitter spawned).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..._vendor.shared_contracts.optical_view import (
    BLOCKED_POWER_SHARE,
    SECONDARY_BRANCH_POWER_SHARE,
    TARGET_LINE_TOLERANCE_NM,
    PlaneArrivalV1,
    designed_arrivals,
)

from .beam import STEP_EMIT, STEP_OBSERVE, STEP_SPLIT_PORT, STEP_SPLIT_REFLECT, STEP_SPLIT_TRANSMIT
from .geometry import Vec3, sub
from .models import BeamPathV2

#: ``(element id, step, wavelength nm) -> (the share of the arriving power the element sends on along this step, the
#: best share it sends along this step at any wavelength or None)``; None: not known, the light passes unchanged.
Response = Callable[[str, str, float], "tuple[float, float | None] | None"]

#: The note of an axis whose drawing another polarization would change: the design does not determine the polarization
#: arriving at an element whose share depends on it.
POLARIZATION_DEPENDENT = "POLARIZATION_DEPENDENT"
#: The note of an axis whose share at an element is not a polarization-resolved value inside the element's
#: characterized data (module docstring).
POLARIZATION_NOT_RESOLVED = "POLARIZATION_NOT_RESOLVED"
#: Display roles of a drawn stretch (``shared_contracts.optical_view.BeamDisplayRoleV1``).
ROLE_PRIMARY, ROLE_SECONDARY = "PRIMARY", "SECONDARY"

_BLOCKED, _WEAK, _MAIN = 0, 1, 2
_SPLIT_STEPS = (STEP_SPLIT_TRANSMIT, STEP_SPLIT_REFLECT)


@dataclass(frozen=True)
class LinePassage:
    """What an element does to one line along one step (``PolarizedResponse``): the share of the arriving power it
    sends on (for the arriving polarization; the unpolarized share when that is not known), the best share it sends
    along this step (``None``: the share is judged on its own), the polarization leaving (opaque here; ``None``: not
    known) and, when the arriving polarization is not known, the lowest and highest share over every polarization;
    ``resolved`` false when the share is not a polarization-resolved value inside the element's characterized data (an
    unpolarized published share used instead, or the element met outside its characterized range)."""

    share: float
    best: float | None = None
    state: Any = None
    share_range: tuple[float, float] | None = None
    resolved: bool = True


class PolarizedResponse(Protocol):
    """Each line's polarization along the paths: ``start``: the polarization a source emits (``None``: not known);
    ``step``: what an element does to a line arriving at ``point`` (design frame) along ``direction_in`` and leaving toward
    ``direction_out`` (segment vectors, not normalized; ``None``: the path ends there) with the arriving polarization
    ``state``; ``None``: not known (the
    light passes unchanged, and its polarization is not known beyond)."""

    def start(self, source_id: str) -> Any: ...

    def step(self, element: str, step: str, wavelength_nm: float, point: Vec3, direction_in: Vec3,
             direction_out: Vec3 | None, state: Any) -> LinePassage | None: ...


@dataclass(frozen=True)
class BeamLineAxisV1:
    """One spectral line of one light source along one branch of the design, in the design frame: ``points_design[k]``
    is at element ``through[k]``."""

    path_key: str
    source_id: str
    line_index: int
    wavelength_nm: float
    points_design: tuple[Vec3, ...]
    through: tuple[str, ...]
    #: The display role of each stretch (points k -> k+1: ``ROLE_PRIMARY`` / ``ROLE_SECONDARY``); ``None``: all primary.
    segment_roles: tuple[str, ...] | None = None
    #: ``POLARIZATION_DEPENDENT`` when another polarization would draw this axis differently, ``POLARIZATION_NOT_RESOLVED``
    #: when a share on it is not polarization-resolved (module docstring); else empty.
    notes: tuple[str, ...] = ()


@dataclass
class _Line:
    """One line on one path: the state and relative power arriving at each point, the state leaving it; with a
    polarized response also the polarization arriving at each point and whether a decision up to it depended on a
    polarization that is not known."""

    arriving: list[int]
    leaving: list[int]
    power: list[float]
    polarization: list[Any] = field(default_factory=list)
    dependent: list[bool] = field(default_factory=list)
    unresolved: list[bool] = field(default_factory=list)


def _parent(key: str) -> str | None:
    """The geometric path a branch splits from: its key without the last letter (``beam-1ab`` -> ``beam-1a``)."""

    return key[:-1] if key[-1:].isalpha() else None


def _is_split(step: str) -> bool:
    return step in _SPLIT_STEPS or step.startswith(STEP_SPLIT_PORT)


def _decide(state: int, share: float, best: float | None, split: bool) -> int:
    """The display rule at one element (module docstring)."""

    if share < BLOCKED_POWER_SHARE * (best if best is not None and best > 0.0 else 1.0):
        return _BLOCKED
    if split and share < SECONDARY_BRANCH_POWER_SHARE:
        return min(state, _WEAK)
    return state


def _directions(path: BeamPathV2, k: int, arriving: Vec3 | None) -> tuple[Vec3 | None, Vec3 | None]:
    """The nominal direction arriving at point ``k`` (``arriving`` at a branch's first point) and the one leaving the
    element there (from its last point on the path; ``None`` where the path ends)."""

    d_in = sub(path.points_design[k], path.points_design[k - 1]) if k > 0 else arriving
    element = path.through[k]
    j = k + 1
    while j < len(path.through) and path.through[j] == element:
        j += 1
    d_out = sub(path.points_design[j], path.points_design[j - 1]) if j < len(path.through) else None
    return d_in, d_out


def _follow(path: BeamPathV2, state: int, power: float, wavelength: float, response: Response | None, absorbing: frozenset[str],
            lift: bool, *, polarized: PolarizedResponse | None = None, polarization: Any = None, arriving: Vec3 | None = None,
            dependent: bool = False, unresolved: bool = False) -> _Line:
    """One line along one path from the state, power (polarization, direction) it arrives at the path's first point
    in; ``lift``: the path is designed for this line, its weak stretches are main beams."""

    line = _Line([], [], [])
    for k, (element, step) in enumerate(zip(path.through, path.steps, strict=True)):
        line.arriving.append(state)
        line.power.append(power)
        line.polarization.append(polarization)
        first_visit = k == 0 or path.through[k - 1] != element  # a ports element entered and left at two points
        if step != STEP_EMIT and first_visit:
            if step == STEP_OBSERVE:
                if element in absorbing:
                    state = _BLOCKED
            elif polarized is not None:
                d_in, d_out = _directions(path, k, arriving)
                passage = None if d_in is None else polarized.step(element, step, wavelength, path.points_design[k], d_in,
                                                                    d_out, polarization)
                if passage is None:
                    polarization = None  # an element without polarization facts: the polarization beyond is not known
                else:
                    split = _is_split(step)
                    if passage.share_range is not None:
                        lo, hi = passage.share_range
                        dependent = dependent or _decide(state, lo, passage.best, split) != _decide(state, hi, passage.best, split)
                    unresolved = unresolved or not passage.resolved
                    power *= passage.share
                    state = _decide(state, passage.share, passage.best, split)
                    polarization = passage.state
            else:
                passed = response(element, step, wavelength) if response is not None else None
                if passed is not None:
                    share, best = passed if isinstance(passed, tuple) else (passed, None)
                    power *= share
                    state = _decide(state, share, best, _is_split(step))
        line.leaving.append(state)
        line.dependent.append(dependent)
        line.unresolved.append(unresolved)
    if lift:
        line.arriving = [_MAIN if s == _WEAK else s for s in line.arriving]
        line.leaving = [_MAIN if s == _WEAK else s for s in line.leaving]
    return line


def route_beam_lines(
    paths: Sequence[BeamPathV2],
    lines: Mapping[str, Sequence[float]],
    response: Response | None,
    *,
    powers: Mapping[str, Sequence[float]] | None = None,
    absorbing: frozenset[str] = frozenset(),
    targets: Iterable[Any] = (),
    tolerance_nm: float = TARGET_LINE_TOLERANCE_NM,
    polarized: PolarizedResponse | None = None,
) -> tuple[BeamLineAxisV1, ...]:
    """The presentation axes of ``paths`` (``trace_beam_paths`` with steps): ``lines`` gives each light source's spectral
    lines in the author's order (a source without lines is not drawn) and ``powers`` their powers (default: equal),
    ``response`` each element's share per step and wavelength, ``absorbing`` the absorbing observation planes,
    ``targets`` the Project's targets (``TargetV1``). ``polarized``, when given, is used instead of ``response``: each
    line then follows its source's polarization (module docstring)."""

    by_key = {p.path_key: p for p in paths}
    if any(len(p.steps) != len(p.through) for p in paths):
        raise ValueError("route_beam_lines needs the steps of every path (trace_beam_paths)")
    roots: dict[str, str] = {}  # geometric root key -> source id
    for p in paths:
        if _parent(p.path_key) is None and p.steps and p.steps[0] == STEP_EMIT and p.through[0] in lines:
            roots[p.path_key] = p.through[0]

    def root_of(key: str) -> str | None:
        while key is not None and key not in roots:
            key = _parent(key)  # type: ignore[assignment]
        return key

    order = [p for p in sorted(paths, key=lambda p: (len(p.path_key), p.path_key)) if root_of(p.path_key) is not None]

    def follow(lift: set[tuple[str, str, int]]) -> dict[tuple[str, str, int], _Line]:
        state: dict[tuple[str, str, int], _Line] = {}
        for p in order:
            source = roots[root_of(p.path_key)]  # type: ignore[index]
            parent = by_key.get(_parent(p.path_key) or "")
            for index, wavelength in enumerate(lines[source]):
                arriving: Vec3 | None = None
                if parent is None:
                    start, power = _MAIN, (powers or {}).get(source, (1.0,) * len(lines[source]))[index]
                    polarization, dependent, unresolved = (polarized.start(source) if polarized is not None else None), False, False
                else:
                    pa = state.get((parent.path_key, source, index))
                    if pa is None or p.through[0] not in parent.through:
                        continue
                    at = parent.through.index(p.through[0])
                    start, power = pa.arriving[at], pa.power[at]  # the line as it arrives at the splitter
                    polarization, dependent = pa.polarization[at], at > 0 and pa.dependent[at - 1]
                    unresolved = at > 0 and pa.unresolved[at - 1]
                    if at > 0:
                        arriving = sub(parent.points_design[at], parent.points_design[at - 1])
                key = (p.path_key, source, index)
                state[key] = _follow(p, start, float(power), float(wavelength), response, absorbing, key in lift,
                                     polarized=polarized, polarization=polarization, arriving=arriving, dependent=dependent,
                                     unresolved=unresolved)
        return state

    roles = follow(set())
    arrivals: dict[str, list[PlaneArrivalV1]] = {}
    for (key, source, index), line in roles.items():
        p = by_key[key]
        for k, (element, step) in enumerate(zip(p.through, p.steps, strict=True)):
            if step == STEP_OBSERVE:
                arrivals.setdefault(element, []).append(PlaneArrivalV1(
                    key=(key, source, index), source_id=source, wavelength_nm=float(lines[source][index]), power=line.power[k],
                    blocked=line.arriving[k] == _BLOCKED))
    lift = {key for _, key in designed_arrivals(targets, arrivals, tolerance_nm=tolerance_nm)}
    if lift:
        roles = follow(lift)

    # the stretches a main beam crosses (between the same two elements: the coincident axes of several sources too)
    main: set[tuple[str, str]] = set()
    for (key, _, _), line in roles.items():
        through = by_key[key].through
        main.update((through[k], through[k + 1]) for k in range(len(through) - 1) if line.leaving[k] == _MAIN)

    drawn_to: dict[tuple[str, str, int], int] = {}  # the last point a line is drawn to on a path
    for p in order:
        source = roots[root_of(p.path_key)]  # type: ignore[index]
        parent = by_key.get(_parent(p.path_key) or "")
        for index in range(len(lines[source])):
            line = roles.get((p.path_key, source, index))
            if line is None:
                continue
            if parent is not None and drawn_to.get((parent.path_key, source, index), -1) < parent.through.index(p.through[0]):
                continue  # not drawn into the splitter: not drawn beyond it
            end = 0
            for k in range(len(p.through) - 1):
                stretch = (p.through[k], p.through[k + 1])
                if line.leaving[k] == _MAIN or (line.leaving[k] == _WEAK and stretch not in main):
                    end = k + 1
                else:
                    break
            drawn_to[(p.path_key, source, index)] = end

    pairs = sorted({(source, index) for source in roots.values() for index in range(len(lines[source]))},
                   key=lambda pair: f"{pair[0]}#{pair[1]}")
    number = {pair: n for n, pair in enumerate(pairs, start=1)}
    out = []
    for (key, source, index), end in drawn_to.items():
        if end < 1:
            continue
        p = by_key[key]
        line = roles[(key, source, index)]
        suffix = key[len(root_of(key) or ""):]
        segment = tuple(ROLE_SECONDARY if line.leaving[k] == _WEAK else ROLE_PRIMARY for k in range(end))
        # a decision at a drawn point, or at the point where the drawing stops, that another polarization would change
        notes = tuple(note for note, flagged in ((POLARIZATION_DEPENDENT, line.dependent[end]),
                                                 (POLARIZATION_NOT_RESOLVED, line.unresolved[end])) if flagged)
        out.append(BeamLineAxisV1(path_key=f"beam-{number[(source, index)]}{suffix}", source_id=source, line_index=index,
                                  wavelength_nm=float(lines[source][index]), points_design=p.points_design[: end + 1],
                                  through=p.through[: end + 1], segment_roles=segment if ROLE_SECONDARY in segment else None,
                                  notes=notes))
    return tuple(sorted(out, key=lambda a: (number[(a.source_id, a.line_index)], a.path_key)))


__all__ = [
    "POLARIZATION_DEPENDENT",
    "POLARIZATION_NOT_RESOLVED",
    "ROLE_PRIMARY",
    "ROLE_SECONDARY",
    "BeamLineAxisV1",
    "LinePassage",
    "PolarizedResponse",
    "Response",
    "route_beam_lines",
]
