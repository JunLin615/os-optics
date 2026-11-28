"""Exact-enough, platform-deterministic geometry of the assembly kernel.

Only IEEE-correctly-rounded operations are used (+, -, *, /, sqrt): no trigonometry, so the same inputs give the
same bits on every platform. Rotations are quaternions (w, x, y, z), Hamilton, active: ``x_world = R(q) x_local``.

Every mechanical body the kernel places is a **yaw-only box**: an axis-aligned box in its part frame, whose
part frame is rotated about the vertical +z only. Two such boxes overlap iff their z intervals overlap and their
plan rectangles overlap (2-D separating-axis test on the four edge normals).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

# ---- named numeric constants of this module (tolerances, digits and work caps)
#: Decimals a snapped position keeps (mm).
_SNAP_DIGITS = 9
#: Float noise below this is rounded away (mm, or a unit component).
_SNAP_QUANTUM = 1e-09
#: A local inflation grows a turned box's world bounds by up to sqrt(2) times the margin.
_TURNED_BOUNDS_GROWTH = 1.5
#: Decimals a snapped quaternion component keeps.
_UNIT_DIGITS = 12


Vec3 = tuple[float, float, float]
Quat = tuple[float, float, float, float]

IDENTITY: Quat = (1.0, 0.0, 0.0, 0.0)


def add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def scale(a: Vec3, s: float) -> Vec3:
    return (a[0] * s, a[1] * s, a[2] * s)


def dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a: Vec3, b: Vec3) -> Vec3:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def norm(a: Vec3) -> float:
    return math.sqrt(dot(a, a))


def unit(a: Vec3) -> Vec3:
    n = norm(a)
    if n == 0.0:
        raise ValueError("zero vector")
    return (a[0] / n, a[1] / n, a[2] / n)


def q_mul(a: Quat, b: Quat) -> Quat:
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return (
        aw * bw - ax * bx - ay * by - az * bz,
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
    )


def q_conj(q: Quat) -> Quat:
    return (q[0], -q[1], -q[2], -q[3])


def q_normalize(q: Quat) -> Quat:
    n = math.sqrt(sum(c * c for c in q))
    u = tuple(c / n for c in q)
    if u[0] < 0 or (u[0] == 0 and next((c for c in u[1:] if c != 0), 0) < 0):
        u = tuple(-c for c in u)
    return tuple(c + 0.0 for c in u)  # type: ignore[return-value]


def q_unit(q: Quat) -> Quat:
    """The rotation ``q`` states, ``q / |q|``, sign kept (the components as given when the length is exactly 1). A design
    quaternion within the Project's 1e-6 of unit length is taken by its direction like any other."""

    n = math.sqrt(q[0] * q[0] + q[1] * q[1] + q[2] * q[2] + q[3] * q[3])
    return q if n == 1.0 else (q[0] / n, q[1] / n, q[2] / n, q[3] / n)


def rotate(q: Quat, v: Vec3) -> Vec3:
    """``R(q) v`` of the rotation ``q`` states (``q_unit``): ``q v q*`` scales by ``|q|^2`` otherwise."""

    q = q_unit(q)
    _, x, y, z = q_mul(q_mul(q, (0.0, *v)), q_conj(q))
    return (x, y, z)


def yaw_quaternion(direction_xy: tuple[float, float]) -> Quat:
    """The rotation about +z that takes +x onto the (non-zero) plan direction, via half-angle square roots."""

    dx, dy = direction_xy
    n = math.sqrt(dx * dx + dy * dy)
    if n == 0.0:
        raise ValueError("zero plan direction")
    c, s = dx / n, dy / n
    half_c = math.sqrt(max(0.0, (1.0 + c) / 2.0))
    half_s = math.sqrt(max(0.0, (1.0 - c) / 2.0))
    if s < 0:
        half_s = -half_s
    if half_c == 0.0:  # 180 degrees
        return (0.0, 0.0, 0.0, 1.0)
    return q_normalize((half_c, 0.0, 0.0, half_s))


def yaw_direction(q: Quat) -> tuple[float, float]:
    x = rotate(q, (1.0, 0.0, 0.0))
    return (x[0], x[1])


def is_yaw_only(q: Quat, tol: float = _SNAP_QUANTUM) -> bool:
    z = rotate(q, (0.0, 0.0, 1.0))
    return abs(z[0]) <= tol and abs(z[1]) <= tol and z[2] > 0


def snap(value: float, quantum: float = _SNAP_QUANTUM) -> float:
    """Round away float noise (e.g. 1e-17) so serialized geometry is stable; -0.0 becomes 0.0."""

    return round(value / quantum) * quantum + 0.0 if abs(value) > 0 else 0.0


def snap_vec(v: Vec3, quantum: float = _SNAP_QUANTUM) -> Vec3:
    return (round(v[0], _SNAP_DIGITS) + 0.0, round(v[1], _SNAP_DIGITS) + 0.0, round(v[2], _SNAP_DIGITS) + 0.0)


def snap_quat(q: Quat) -> Quat:
    return q_normalize(tuple(round(c, _UNIT_DIGITS) + 0.0 for c in q))  # type: ignore[arg-type]


@dataclass(frozen=True)
class Pose:
    position: Vec3
    rotation: Quat = IDENTITY

    def apply(self, p: Vec3) -> Vec3:
        return add(rotate(self.rotation, p), self.position)

    def compose(self, child: Pose) -> Pose:
        return Pose(self.apply(child.position), q_normalize(q_mul(self.rotation, child.rotation)))


@dataclass(frozen=True)
class YawBox:
    """A box ``[lo, hi]`` in a yaw-only part frame placed at ``pose`` (rotation about +z only).

    A box is immutable, so its plan corners and world bounds are computed once at construction, its edge normals and its
    last inflated copy once when first asked for (with the arithmetic of the former on-demand form, so every comparison
    gives the same bits) and kept on the instance."""

    owner: str  # the slot key of the part the body belongs to
    lo: Vec3
    hi: Vec3
    pose: Pose

    def __post_init__(self) -> None:
        q = self.pose.rotation
        position = self.pose.position
        plan = ((self.lo[0], self.lo[1]), (self.hi[0], self.lo[1]), (self.hi[0], self.hi[1]), (self.lo[0], self.hi[1]))
        if q[1] == 0.0 and q[2] == 0.0:
            # a yaw quaternion (w, 0, 0, z): ``rotate`` reduces to these products, term for term and in the same order,
            # so the corners carry exactly the bits of ``pose.apply``
            w, z = q[0], q[3]
            corners = []
            for x, y in plan:
                px, py = w * x - z * y, w * y + z * x
                corners.append((px * w - py * z + position[0], px * z + py * w + position[1]))
        else:
            corners = []
            for x, y in plan:
                p = self.pose.apply((x, y, 0.0))
                corners.append((p[0], p[1]))
        z = (self.pose.position[2] + self.lo[2], self.pose.position[2] + self.hi[2])
        xs = [c[0] for c in corners]
        ys = [c[1] for c in corners]
        # derived, immutable facts of the box (not dataclass fields: equality and hashing stay those of the fields)
        object.__setattr__(self, "_corners", tuple(corners))
        object.__setattr__(self, "_z", z)
        #: the world axis-aligned bounds ``(x0, y0, z0, x1, y1, z1)``
        object.__setattr__(self, "bounds", (min(xs), min(ys), z[0], max(xs), max(ys), z[1]))

    def __getattr__(self, name: str) -> Any:
        if name != "_axes":
            raise AttributeError(name)
        dx, dy = yaw_direction(self.pose.rotation)  # only a box that passes the broad phase needs its edge normals
        axes = ((dx, dy), (-dy, dx))
        object.__setattr__(self, "_axes", axes)
        return axes

    def z_range(self) -> tuple[float, float]:
        return self._z  # type: ignore[attr-defined]

    def plan_corners(self) -> list[tuple[float, float]]:
        return list(self._corners)  # type: ignore[attr-defined]

    def plan_axes(self) -> list[tuple[float, float]]:
        return list(self._axes)  # type: ignore[attr-defined]

    def inflated(self, margin: float) -> YawBox:
        last = self.__dict__.get("_inflated")
        if last is not None and last[0] == margin:
            return last[1]
        box = YawBox(self.owner, sub(self.lo, (margin, margin, margin)), add(self.hi, (margin, margin, margin)), self.pose)
        object.__setattr__(self, "_inflated", (margin, box))
        return box


#: Bounds closer than this are decided by the exact test, never by the bounding-box pre-check.
_BROAD_PHASE_SLACK = 1e-9


def bounds_apart(a: tuple[float, ...], b: tuple[float, ...]) -> float | None:
    """The largest axis gap (mm) by which two world bounds ``(x0, y0, z0, x1, y1, z1)`` are clearly apart (more than
    the float slack), else None (they may touch or overlap: decide with the exact test)."""

    gap = max(a[0] - b[3], b[0] - a[3], a[1] - b[4], b[1] - a[4], a[2] - b[5], b[2] - a[5])
    return -gap if gap > _BROAD_PHASE_SLACK else None


def _project(corners, axis: tuple[float, float]) -> tuple[float, float]:
    values = [c[0] * axis[0] + c[1] * axis[1] for c in corners]
    return min(values), max(values)


def overlap_depth(a: YawBox, b: YawBox) -> float:
    """The smallest separating-axis penetration (mm) of two yaw-only boxes, or <= 0 when they are separated or
    touching. Touching faces (penetration 0) do not overlap. Boxes whose world bounds are clearly apart are answered
    by the bounds gap (negative) without the separating-axis test."""

    az, bz = a.z_range(), b.z_range()
    depth = min(az[1], bz[1]) - max(az[0], bz[0])
    if depth <= 0:
        return depth
    apart = bounds_apart(a.bounds, b.bounds)
    if apart is not None:
        return apart
    ca, cb = a._corners, b._corners  # type: ignore[attr-defined]
    for axis in (*a._axes, *b._axes):  # type: ignore[attr-defined]
        lo_a, hi_a = _project(ca, axis)
        lo_b, hi_b = _project(cb, axis)
        d = min(hi_a, hi_b) - max(lo_a, lo_b)
        if d <= 0:
            return d
        depth = min(depth, d)
    return depth


def segment_box_hit(p0: Vec3, p1: Vec3, box: YawBox, *, margin: float = 0.0) -> float | None:
    """Parameter length (mm along the segment) of the part of segment p0->p1 inside ``box`` (inflated by ``margin``),
    or None if the segment misses it. Slab test in the box's own frame, after a world-bounds pre-check."""

    x0, y0, z0, x1, y1, z1 = box.bounds
    s = _BROAD_PHASE_SLACK + _TURNED_BOUNDS_GROWTH * margin  # a local inflation grows a turned box's world bounds by up to sqrt(2) margin
    if (max(p0[0], p1[0]) < x0 - s or min(p0[0], p1[0]) > x1 + s or max(p0[1], p1[1]) < y0 - s or min(p0[1], p1[1]) > y1 + s
            or max(p0[2], p1[2]) < z0 - s or min(p0[2], p1[2]) > z1 + s):
        return None
    inv = q_conj(box.pose.rotation)
    a = rotate(inv, sub(p0, box.pose.position))
    b = rotate(inv, sub(p1, box.pose.position))
    lo = sub(box.lo, (margin, margin, margin))
    hi = add(box.hi, (margin, margin, margin))
    t0, t1 = 0.0, 1.0
    d = sub(b, a)
    for i in range(3):
        if d[i] == 0.0:
            if a[i] <= lo[i] or a[i] >= hi[i]:
                return None
            continue
        ta, tb = (lo[i] - a[i]) / d[i], (hi[i] - a[i]) / d[i]
        if ta > tb:
            ta, tb = tb, ta
        t0, t1 = max(t0, ta), min(t1, tb)
        if t0 >= t1:
            return None
    return (t1 - t0) * norm(d)


def plan_bounds(boxes: list[YawBox]) -> tuple[tuple[float, float], tuple[float, float]]:
    xs: list[float] = []
    ys: list[float] = []
    for box in boxes:
        for x, y in box.plan_corners():
            xs.append(x)
            ys.append(y)
    return (min(xs), min(ys)), (max(xs), max(ys))


__all__ = [
    "IDENTITY",
    "Pose",
    "Quat",
    "Vec3",
    "YawBox",
    "add",
    "bounds_apart",
    "cross",
    "dot",
    "is_yaw_only",
    "norm",
    "overlap_depth",
    "plan_bounds",
    "q_conj",
    "q_mul",
    "q_normalize",
    "rotate",
    "scale",
    "segment_box_hit",
    "snap_quat",
    "snap_vec",
    "sub",
    "unit",
    "yaw_direction",
    "yaw_quaternion",
]
