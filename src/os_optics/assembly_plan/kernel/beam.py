"""Nominal beam paths of a design, in the design frame: straight segments between the design's elements.

This is not optics: it only answers "which elements does the nominal axis visit, in which order, and where does it
turn", so that the Assembly Preview can draw folded paths and the kernel can keep the beam clear of hardware.
Each customer light source emits along its local +Z; a customer-supplied optical component of known category is
visited like an OpenSci optic of that category. A ray visits the nearest element whose reference point lies
within that element's half-size of the ray (a flat mirror or a plate beamsplitter is visited where the ray meets
its face plane; a lens when the ray runs along its optical axis or crosses it at a tilt up to ``lens_tilt_cosine``, a
flat transmissive plate up to ``plate_tilt_cosine``; within ``AXIAL_COSINE`` always). A flat mirror reflects about its face normal (local +Z); a plate beamsplitter continues and also
spawns the reflected branch; every other element transmits. Observation planes are visited and passed. Each point
records what the beam does there (``BeamPathV2.steps``), so a caller can follow each spectral line through the
elements' published spectral data (``beam_lines``).

An element placed by its ports (a cube or polarizing cube splitter, a prism, an off-axis parabolic mirror: its published
``IN`` and ``OUT`` ports) is visited when the ray runs along its ``IN`` direction within its half-size of the ``IN``
point, and the beam leaves through every ``OUT`` port: the output closest to the input direction continues the path
(a cube's ``OUT_T``), every other output spawns a branch (a cube's ``OUT_R``). The element's point on the path is where
the input ray meets the output line (a cube's diagonal, a fold prism's hypotenuse, an off-axis mirror's aperture
centre; the body centre when the output continues the input line); an output that the input ray never meets (a
retro-reflector's offset return) gives two points, where the beam enters and where it leaves. An OpenSci cube splitter
without published ports follows the cube convention of its size (``IN`` at ``(0, 0, -a/2)`` along +Z, ``OUT_T`` at
``(0, 0, a/2)`` along +Z, ``OUT_R`` at ``(a/2, 0, 0)`` along +X).

Each element is visited once per path, except on the way back: once a flat mirror sends the path back along itself (a
double pass: a quarter-wave plate and a mirror behind a polarizing cube), the path may visit each element it met once more,
in the opposite direction. A cube splitter (a ports element whose ``OUT_T`` continues its ``IN`` line and that has one
other output, ``OUT_R``) is then also entered backwards through either output: the light leaves through ``IN`` reversed
and through the fourth face -- the point mirrored from ``OUT_R`` through the element's origin, along ``-OUT_R`` --
(entering through ``OUT_T``), or through ``IN`` reversed and that fourth face along ``-OUT_R`` (entering through
``OUT_R``). Each backward output carries the step of the port whose share it takes: leaving along the transmitted line
``SPLIT_PORT:OUT_T``, after the diagonal's reflection ``SPLIT_PORT:OUT_R``.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from .geometry import Vec3, add, cross, dot, norm, rotate, scale, sub, unit
from .models import BeamPathV2, ExternalRequest, ObservationRequest, OpticRequest, PortRequest

# ---- named numeric constants of this module (tolerances, digits and work caps)
#: Lines whose direction cross product is shorter than this are parallel.
_PARALLEL_LINES = 1e-09
#: A smaller denominator is singular.
_SINGULAR = 1e-12
#: Branch suffixes a..y before the z-prefixed continuation.
_SUFFIX_LETTERS = 25


#: The default caps of one trace (``ResolverPolicyV1.max_beam_points`` / ``max_beam_branches`` set the kernel's).
MAX_POINTS = 64
MAX_BRANCHES = 16
#: Why a trace stopped short of the whole beam (``trace_beam_paths(..., truncated=...)``): a branch was not followed
#: (``max_branches``) or a path reached ``max_points``.
TRUNCATED_BRANCHES = "BRANCHES"
TRUNCATED_POINTS = "POINTS"
EPS = 1e-6
#: cos(~2.6 deg): a beam this close to a transmissive optic's axis always passes through it (paraxial designs); also the
#: tolerance of port directions and of a beam sent back along itself.
AXIAL_COSINE = 0.999
#: Flat transmissive plates: a beam passes through them up to the caller's ``plate_tilt_cosine`` (an intentional tilt,
#: e.g. for back-reflection control); a lens up to the caller's ``lens_tilt_cosine``.
PLATE_CATEGORIES = frozenset({"FILTER", "WINDOW", "WAVEPLATE", "POLARIZER"})
#: The optic category whose tilt bound is ``lens_tilt_cosine``.
LENS_CATEGORY = "LENS"
#: The beam category of an element placed by its published ports.
PORTS_CATEGORY = "PORTS"
#: Kernel optic categories visited by their ports (an OpenSci cube splitter: its published ports or the cube convention).
PORTED_CATEGORIES = frozenset({PORTS_CATEGORY, "BEAMSPLITTER_CUBE"})
#: An output line and the input ray meet when they pass this close (mm), beyond the input ray's own offset from ``IN``.
PORT_MEET_MM = 1e-3
#: What the beam does at a point of its path (``BeamPathV2.steps``).
STEP_EMIT = "EMIT"  # it leaves the light source
STEP_TRANSMIT = "TRANSMIT"  # it passes a transmissive element (lens, filter, window, ...)
STEP_REFLECT = "REFLECT"  # a mirror folds it
STEP_SPLIT_TRANSMIT = "SPLIT_TRANSMIT"  # it continues through a plate beamsplitter (whose reflection is another branch)
STEP_SPLIT_REFLECT = "SPLIT_REFLECT"  # the reflected branch of a plate beamsplitter starts
STEP_OBSERVE = "OBSERVE"  # it crosses an observation plane
STEP_PORT = "PORT:"  # + port id: it leaves a ports element through its only output
STEP_SPLIT_PORT = "SPLIT_PORT:"  # + port id: it leaves a ports element through one of several outputs


@dataclass(frozen=True)
class _Port:
    port_id: str
    point: Vec3  # design frame
    direction: Vec3  # design frame, unit


@dataclass(frozen=True)
class _Target:
    key: str
    position: Vec3
    normal: Vec3  # local +Z in the design frame
    radius: float
    kind: str  # MIRROR | SPLITTER_PLATE | TRANSMIT | OBSERVATION | PORTS
    axial_cosine: float = AXIAL_COSINE  # TRANSMIT: the beam must run at least this close to the optic's axis
    entry: _Port | None = None  # PORTS: the IN port
    outputs: tuple[_Port, ...] = ()  # PORTS: the OUT ports, by port id


def cube_ports(size_mm: float) -> tuple[PortRequest, ...]:
    """The published-port convention of a cube splitter of edge ``size_mm`` (its frame origin is the body centre)."""

    h = size_mm / 2.0
    return (PortRequest("IN", (0.0, 0.0, -h), (0.0, 0.0, 1.0)), PortRequest("OUT_R", (h, 0.0, 0.0), (1.0, 0.0, 0.0)),
            PortRequest("OUT_T", (0.0, 0.0, h), (0.0, 0.0, 1.0)))


def _ports_target(key: str, position: Vec3, rotation: tuple[float, float, float, float], ports: tuple[PortRequest, ...],
                  size_mm: float) -> _Target | None:
    placed = [_Port(p.port_id, add(position, rotate(rotation, p.point_mm)), unit(rotate(rotation, p.direction))) for p in ports]
    entry = next((p for p in placed if p.port_id == "IN"), None)
    outputs = tuple(sorted((p for p in placed if p.port_id != "IN"), key=lambda p: p.port_id))
    if entry is None or not outputs:
        return None
    return _Target(key, position, rotate(rotation, (0.0, 0.0, 1.0)), size_mm / 2.0, "PORTS", entry=entry, outputs=outputs)


def _target(key: str, position: Vec3, rotation: tuple[float, float, float, float], category: str, size_mm: float,
            plate_tilt_cosine: float, ports: tuple[PortRequest, ...] = (), lens_tilt_cosine: float = AXIAL_COSINE) -> _Target | None:
    if category in PORTED_CATEGORIES:
        if not ports and category == "BEAMSPLITTER_CUBE":
            ports = cube_ports(size_mm)
        return _ports_target(key, position, rotation, ports, size_mm)
    kind = "MIRROR" if category == "MIRROR" else "SPLITTER_PLATE" if category == "BEAMSPLITTER_PLATE" else "TRANSMIT"
    cosine = (min(AXIAL_COSINE, plate_tilt_cosine) if category in PLATE_CATEGORIES
              else min(AXIAL_COSINE, lens_tilt_cosine) if category == LENS_CATEGORY else AXIAL_COSINE)
    return _Target(key, position, rotate(rotation, (0.0, 0.0, 1.0)), size_mm / 2.0, kind, cosine)


def _targets(optics: tuple[OpticRequest, ...], observations: tuple[ObservationRequest, ...], plate_tilt_cosine: float,
             externals: tuple[ExternalRequest, ...] = (), lens_tilt_cosine: float = AXIAL_COSINE) -> list[_Target]:
    out = []
    for optic in optics:
        target = _target(optic.instance_id, optic.position_design, optic.rotation_design, optic.mechanics.category,
                         optic.mechanics.size_mm, plate_tilt_cosine, optic.ports, lens_tilt_cosine)
        if target is not None:
            out.append(target)
    for external in externals:
        # a customer-supplied optical component of known category is visited like an OpenSci optic of that category
        if external.role != "source" and external.beam_category is not None and external.size_mm is not None:
            target = _target(external.instance_id, external.position_design, external.rotation_design, external.beam_category,
                             external.size_mm, plate_tilt_cosine, external.ports, lens_tilt_cosine)
            if target is not None:
                out.append(target)
    for obs in observations:
        radius = max(math.sqrt(u * u + v * v) for u, v in obs.plane_corners) if obs.plane_corners else 1.0
        out.append(_Target(obs.observation_id, obs.position_design, rotate(obs.rotation_design, (0.0, 0.0, 1.0)), radius, "OBSERVATION"))
    return out


def _hit(origin: Vec3, direction: Vec3, target: _Target) -> tuple[float, Vec3] | None:
    """Distance along the ray to the target's visit point, and that point (a ports element: where the ray enters)."""

    if target.kind == "PORTS":
        assert target.entry is not None
        if dot(direction, target.entry.direction) < AXIAL_COSINE:
            return None  # the beam enters a ports element only along its IN direction
        t = dot(sub(target.entry.point, origin), direction)
        point = add(origin, scale(direction, t))
        reference = target.entry.point
    elif target.kind in ("MIRROR", "SPLITTER_PLATE"):
        denom = dot(direction, target.normal)
        if abs(denom) < _SINGULAR:
            return None
        t = dot(sub(target.position, origin), target.normal) / denom
        point = add(origin, scale(direction, t))
        reference = target.position
    else:
        if target.kind == "TRANSMIT" and abs(dot(direction, target.normal)) < target.axial_cosine:
            return None  # a transmissive optic is on the path only when the beam runs (nearly) along its optical axis
        t = dot(sub(target.position, origin), direction)
        point = add(origin, scale(direction, t))
        reference = target.position
    if t <= EPS:
        return None
    off = sub(point, reference)
    if dot(off, off) > (target.radius + EPS) ** 2:
        return None
    return t, point


def _cube_ports(target: _Target) -> tuple[_Port, _Port] | None:
    """``(OUT_T, OUT_R)`` of a cube splitter (``OUT_T`` continues the ``IN`` line, one other output), else ``None``."""

    if target.kind != "PORTS" or target.entry is None or len(target.outputs) != 2:
        return None
    by_id = {o.port_id: o for o in target.outputs}
    through, side = by_id.get("OUT_T"), by_id.get("OUT_R")
    if through is None or side is None or dot(through.direction, target.entry.direction) < AXIAL_COSINE:
        return None
    return through, side


def _backward(origin: Vec3, direction: Vec3, target: _Target) -> tuple[float, Vec3, Vec3, list[tuple[_Port, str]]] | None:
    """A beam on its way back entering a cube splitter through one of its outputs: the distance to where it enters, that
    point, the output port's point it enters by and the element's outputs for it, each with the port whose share it takes
    (module docstring); ``None``: it does not enter."""

    ports = _cube_ports(target)
    if ports is None:
        return None
    assert target.entry is not None
    through, side = ports
    fourth = _Port("OUT_R", sub(scale(target.position, 2.0), side.point), scale(side.direction, -1.0))
    back = _Port("OUT_T", target.entry.point, scale(target.entry.direction, -1.0))
    for port, outputs in ((through, [(back, "OUT_T"), (fourth, "OUT_R")]),
                          (side, [(_Port("OUT_T", fourth.point, fourth.direction), "OUT_T"), (_Port("OUT_R", back.point, back.direction), "OUT_R")])):
        if dot(direction, port.direction) > -AXIAL_COSINE:
            continue
        t = dot(sub(port.point, origin), direction)
        if t <= EPS:
            continue
        point = add(origin, scale(direction, t))
        off = sub(point, port.point)
        if dot(off, off) > (target.radius + EPS) ** 2:
            continue
        return t, point, port.point, [(_Port(name, out.point, out.direction), name) for out, name in outputs]
    return None


def branch_suffix(n: int) -> str:
    """The key suffix of a branch spawned with ``n`` branches pending: ``a`` .. ``y``, then ``z`` followed by the suffix of
    ``n - 25``. The code is prefix-free, so a branch key never reads as the key of another branch of another parent."""

    return chr(ord("a") + n) if n < _SUFFIX_LETTERS else "z" + branch_suffix(n - _SUFFIX_LETTERS)


def is_child_key(parent: str, key: str) -> bool:
    """Whether ``key`` is the key of a branch spawned on the path ``parent`` (one suffix of ``branch_suffix`` longer)."""

    suffix = key[len(parent):]
    return (key.startswith(parent) and len(suffix) >= 1 and suffix[-1] != "z" and "a" <= suffix[-1] <= "y"
            and all(c == "z" for c in suffix[:-1]))


def _reflect(direction: Vec3, normal: Vec3) -> Vec3:
    return unit(sub(direction, scale(normal, 2.0 * dot(direction, normal))))


def _port_points(target: _Target, entry: Vec3, direction: Vec3, output: _Port, reference: Vec3 | None = None) -> list[Vec3]:
    """The points of a ports element on the path that leaves through ``output``, for a ray along ``direction`` entering at
    ``entry``: where the ray meets the output line (one point), else where it enters and where it leaves. ``reference``:
    the nominal point the ray enters at (default the ``IN`` port's)."""

    assert target.entry is not None
    shift = sub(entry, target.entry.point if reference is None else reference)  # the ray's offset from the nominal input
    tolerance = PORT_MEET_MM + norm(shift)
    v = output.direction
    across = cross(direction, v)
    if norm(across) <= _PARALLEL_LINES:  # parallel lines
        to_output = sub(output.point, entry)
        lateral = sub(to_output, scale(direction, dot(to_output, direction)))
        if dot(direction, v) > 0.0 and norm(lateral) <= tolerance:
            # the output continues the input line: the element's point is its reference on that line (a cube's centre)
            s = max(0.0, dot(sub(target.position, entry), direction))
            return [add(entry, scale(direction, s))]
        return [entry, add(output.point, shift)]
    # closest approach of the input ray (entry + s direction) and the output line (point + r v)
    w = sub(entry, output.point)
    b = dot(direction, v)
    d, e = dot(direction, w), dot(v, w)
    denominator = 1.0 - b * b
    s = (b * e - d) / denominator
    r = (e - b * d) / denominator
    meet = add(entry, scale(direction, s))
    gap = norm(sub(meet, add(output.point, scale(v, r))))
    if gap <= tolerance and s >= -EPS and r <= EPS:
        return [meet if s > EPS else entry]
    return [entry, add(output.point, shift)]


_STEP_BY_KIND = {"MIRROR": STEP_REFLECT, "SPLITTER_PLATE": STEP_SPLIT_TRANSMIT, "OBSERVATION": STEP_OBSERVE}


def trace_beam_paths(
    externals: tuple[ExternalRequest, ...],
    optics: tuple[OpticRequest, ...],
    observations: tuple[ObservationRequest, ...],
    wavelengths: dict[str, float] | None = None,
    *,
    plate_tilt_cosine: float = AXIAL_COSINE,
    lens_tilt_cosine: float = AXIAL_COSINE,
    max_branches: int = MAX_BRANCHES,
    max_points: int = MAX_POINTS,
    truncated: list[dict[str, str | int]] | None = None,
    charge: Callable[[int], None] | None = None,
) -> tuple[BeamPathV2, ...]:
    """The nominal paths, one per source and beamsplitter branch, at most ``max_branches`` per source and
    ``max_points`` per path. ``truncated`` (optional): every place the trace stopped short at one of these caps is
    appended to it as ``{path_key, limit: BRANCHES | POINTS, value, at}`` (``at``: the element where it stopped).
    ``charge`` (optional, a work budget's) is called with the number of design elements before each ray step tests
    them all."""

    def stopped(key: str, limit: str, at: str) -> None:
        if truncated is not None:
            entry = {"path_key": key, "limit": limit, "value": max_branches if limit == TRUNCATED_BRANCHES else max_points, "at": at}
            if entry not in truncated:
                truncated.append(entry)

    targets = _targets(optics, observations, plate_tilt_cosine, externals, lens_tilt_cosine)
    paths: list[BeamPathV2] = []
    sources = [e for e in externals if e.role == "source"]
    for index, source in enumerate(sources, start=1):
        pending: list[tuple[str, list[Vec3], Vec3, list[str], frozenset[str], list[str]]] = [
            (f"beam-{index}", [source.position_design], rotate(source.rotation_design, (0.0, 0.0, 1.0)), [source.instance_id], frozenset(),
             [STEP_EMIT])
        ]
        branch = 0
        while pending and branch < max_branches:
            key, points, direction, names, visited, steps = pending.pop(0)
            seen = set(visited)
            returning = False  # a mirror sent the path back along itself: it may meet each element once more (docstring)
            again: set[str] = set()
            while True:
                at_cap = len(points) >= max_points
                if at_cap and truncated is None:
                    break  # (a caller that asks no record: no look past the cap)
                if charge is not None:
                    charge(len(targets))
                best: tuple[float, Vec3, _Target] | None = None
                backward: tuple[Vec3, list[tuple[_Port, str]]] | None = None
                for target in targets:
                    reverse = None
                    if target.key in seen:
                        if not returning or target.key in again:
                            continue
                        reverse = _backward(points[-1], direction, target) if target.kind == "PORTS" else None
                        hit = (reverse[0], reverse[1]) if reverse is not None else (None if target.kind == "PORTS" else
                                                                                   _hit(points[-1], direction, target))
                    else:
                        hit = _hit(points[-1], direction, target)
                    if hit is not None and (best is None or (hit[0], target.key) < (best[0], best[2].key)):
                        best = (hit[0], hit[1], target)
                        backward = None if reverse is None else (reverse[2], reverse[3])
                if best is None:
                    break
                _, point, target = best
                if at_cap:
                    stopped(key, TRUNCATED_POINTS, target.key)  # the path goes on beyond the cap
                    break
                if target.key in seen:
                    again.add(target.key)
                seen.add(target.key)
                if backward is not None:
                    # a cube entered backwards: the output nearest the input direction continues, the other is a branch
                    reference, outputs = backward
                    outputs = sorted(outputs, key=lambda o: -dot(o[0].direction, direction))
                    for output, name in outputs[1:]:
                        if branch + len(pending) + 1 >= max_branches:
                            stopped(key, TRUNCATED_BRANCHES, target.key)
                            break
                        start = _port_points(target, point, direction, output, reference)
                        pending.append((f"{key}{branch_suffix(len(pending))}", start, output.direction, [target.key] * len(start),
                                        frozenset(seen), [STEP_SPLIT_PORT + name] * len(start)))
                    visit = _port_points(target, point, direction, outputs[0][0], reference)
                    if len(visit) > max_points - len(points):
                        stopped(key, TRUNCATED_POINTS, target.key)
                        visit = visit[: max_points - len(points)]
                    points.extend(visit)
                    names.extend([target.key] * len(visit))
                    steps.extend([STEP_SPLIT_PORT + outputs[0][1]] * len(visit))
                    direction = outputs[0][0].direction
                    continue
                if target.kind == "PORTS":
                    # the output nearest the input direction continues the path; every other output is a branch
                    outputs = sorted(target.outputs, key=lambda o: -dot(o.direction, direction))
                    leave = STEP_SPLIT_PORT if len(outputs) > 1 else STEP_PORT
                    for output in outputs[1:]:
                        if branch + len(pending) + 1 >= max_branches:
                            stopped(key, TRUNCATED_BRANCHES, target.key)
                            break
                        start = _port_points(target, point, direction, output)
                        pending.append((f"{key}{branch_suffix(len(pending))}", start, output.direction, [target.key] * len(start),
                                        frozenset(seen), [leave + output.port_id] * len(start)))
                    visit = _port_points(target, point, direction, outputs[0])
                    if len(visit) > max_points - len(points):
                        stopped(key, TRUNCATED_POINTS, target.key)
                        visit = visit[: max_points - len(points)]
                    points.extend(visit)
                    names.extend([target.key] * len(visit))
                    steps.extend([leave + outputs[0].port_id] * len(visit))
                    direction = outputs[0].direction
                    continue
                points.append(point)
                names.append(target.key)
                steps.append(_STEP_BY_KIND.get(target.kind, STEP_TRANSMIT))
                if target.kind == "MIRROR":
                    turned = _reflect(direction, target.normal)
                    returning = returning or dot(turned, direction) <= -AXIAL_COSINE
                    direction = turned
                elif target.kind == "SPLITTER_PLATE":
                    if branch + len(pending) + 1 < max_branches:
                        pending.append((f"{key}{branch_suffix(len(pending))}", [point], _reflect(direction, target.normal),
                                        [target.key], frozenset(seen), [STEP_SPLIT_REFLECT]))
                    else:
                        stopped(key, TRUNCATED_BRANCHES, target.key)
            if len(points) >= 2:
                paths.append(BeamPathV2(path_key=key, points_design=tuple(points), through=tuple(names),
                                        wavelength_nm=(wavelengths or {}).get(source.instance_id), steps=tuple(steps)))
            branch += 1
        for left in pending:  # (never left by the guards above; recorded if it were)
            stopped(left[0], TRUNCATED_BRANCHES, left[3][0])
    return tuple(paths)


__all__ = [
    "AXIAL_COSINE",
    "PLATE_CATEGORIES",
    "PORTED_CATEGORIES",
    "PORTS_CATEGORY",
    "STEP_EMIT",
    "STEP_OBSERVE",
    "STEP_PORT",
    "STEP_REFLECT",
    "STEP_SPLIT_PORT",
    "STEP_SPLIT_REFLECT",
    "STEP_SPLIT_TRANSMIT",
    "STEP_TRANSMIT",
    "TRUNCATED_BRANCHES",
    "TRUNCATED_POINTS",
    "branch_suffix",
    "cube_ports",
    "is_child_key",
    "trace_beam_paths",
]
