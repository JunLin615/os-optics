"""2D/2.5D footprint geometry with Shapely (rotated polygons, sweeps, separation).

Shapely handles footprint booleans / distances only; it does not replace 3D physics.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from shapely import affinity
from shapely.geometry import LineString, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import nearest_points, unary_union

from ..config import NumericalConfig
from ..contracts.components import EngineeringComponentSnapshot, PolygonRegion
from ..math.pose import Pose

Array = NDArray[np.float64]

_MAX_DOUBLINGS = 64  # doubling search for a separating translation: 2^64 exceeds any physical length


@dataclass(frozen=True)
class Placed:
    """A component's global 2.5D geometry."""

    instance_id: str
    footprint: Polygon
    z_min: float
    z_max: float
    required_clearance_mm: float
    keepout_margin_mm: float
    keepout_regions: tuple[tuple[str, Polygon, float, float], ...]
    tool_regions: tuple[tuple[str, Polygon, float, float], ...]
    cable_regions: tuple[tuple[str, Polygon, float, float], ...]
    participates_in_beam: bool


def _project_points(pose: Pose, pts_xy: list[tuple[float, float]], z: float = 0.0) -> Array:
    arr = np.array([(x, y, z) for x, y in pts_xy], dtype=np.float64)
    return pose.apply_points(arr)[:, :2]


def _region(pose: Pose, region: PolygonRegion) -> tuple[str, Polygon, float, float]:
    xy = _project_points(pose, list(region.polygon_mm))
    corners = np.array([(x, y, z) for x, y in region.polygon_mm for z in region.z_range_mm], dtype=np.float64)
    zs = pose.apply_points(corners)[:, 2]
    return region.region_id, Polygon(xy), float(zs.min()), float(zs.max())


def place(comp: EngineeringComponentSnapshot, pose: Pose) -> Placed | None:
    env = comp.envelope
    if env is None:
        return None
    # the envelope lives in its own (plan-parallel) frame relative to the component frame
    pose = pose.compose(env.frame_pose.to_pose())
    fp = Polygon(_project_points(pose, list(env.footprint_mm)))
    corners = np.array([(x, y, z) for x, y in env.footprint_mm for z in env.z_range_mm], dtype=np.float64)
    zs = pose.apply_points(corners)[:, 2]
    return Placed(
        comp.instance_id, fp, float(zs.min()), float(zs.max()), env.required_clearance_mm, env.keepout_margin_mm,
        tuple(_region(pose, r) for r in env.keepout_polygons), tuple(_region(pose, r) for r in env.tool_access),
        tuple(_region(pose, r) for r in env.cable_keepout), env.participates_in_beam,
    )


def heights_overlap(a0: float, a1: float, b0: float, b1: float, tol: float) -> bool:
    return (a0 < b1 - tol) and (b0 < a1 - tol)


def validity_error(poly: Polygon, tol_mm: float) -> str | None:
    if not poly.is_valid:
        return "self-intersecting or otherwise invalid polygon"
    if poly.area <= tol_mm * tol_mm:
        return "zero-area polygon"
    return None


def clearance(a: BaseGeometry, b: BaseGeometry) -> float:
    return float(a.distance(b))


def nearest_pair(a: BaseGeometry, b: BaseGeometry) -> tuple[tuple[float, float], tuple[float, float]]:
    pa, pb = nearest_points(a, b)
    return (float(pa.x), float(pa.y)), (float(pb.x), float(pb.y))


def bounds(poly: BaseGeometry) -> tuple[float, float, float, float]:
    b = poly.bounds
    return (float(b[0]), float(b[1]), float(b[2]), float(b[3]))


def separation_translation(fixed: BaseGeometry, moving: BaseGeometry, required_mm: float, tol_mm: float) -> tuple[float, float]:
    """Smallest translation of ``moving`` along the centroid line (away from ``fixed``) making ``dist >= required_mm``.

    Deterministic: doubling search then bisection to ``tol_mm``."""
    ca, cb = fixed.centroid, moving.centroid
    dx, dy = cb.x - ca.x, cb.y - ca.y
    norm = math.hypot(dx, dy)
    if norm <= tol_mm:
        dx, dy, norm = 1.0, 0.0, 1.0
    ux, uy = dx / norm, dy / norm

    def ok(s: float) -> bool:
        shifted = affinity.translate(moving, ux * s, uy * s)
        # a valid separation has no overlap area (touching is allowed) and at least the required clearance
        return fixed.intersection(shifted).area <= tol_mm * tol_mm and fixed.distance(shifted) >= required_mm - tol_mm

    if ok(0.0):
        return (0.0, 0.0)
    hi = max(required_mm, tol_mm)
    for _ in range(_MAX_DOUBLINGS):
        if ok(hi):
            break
        hi *= 2.0
    lo = 0.0
    while hi - lo > tol_mm:
        mid = 0.5 * (lo + hi)
        if ok(mid):
            hi = mid
        else:
            lo = mid
    return (ux * hi, uy * hi)


def sweep_translation(poly: Polygon, dx0: float, dy0: float, dx1: float, dy1: float) -> Polygon:
    """Minkowski sweep of ``poly`` from translation ``(dx0, dy0)`` to ``(dx1, dy1)`` (exact for polygons)."""
    a, b = affinity.translate(poly, dx0, dy0), affinity.translate(poly, dx1, dy1)
    parts: list[BaseGeometry] = [a, b]
    coords = list(poly.exterior.coords)
    for (px, py), (qx, qy) in zip(coords[:-1], coords[1:]):
        parts.append(Polygon([(px + dx0, py + dy0), (qx + dx0, qy + dy0), (qx + dx1, qy + dy1), (px + dx1, py + dy1)]).buffer(0))
    merged = unary_union(parts)
    return _largest(merged)


def sweep_rotation(poly: Polygon, pose_matrix: Array, pivot: Array, axis: Array, angle_min_rad: float, angle_max_rad: float,
                   samples: int) -> Polygon:
    """Union of ``poly`` rotated about a 3D axis through ``pivot`` for sampled angles; projected to XY.

    Uses the full 3D rotation (the footprint lies at the pivot height) and projects the points back to the plane."""
    from ..math.rotation import rotation_matrix_axis_angle

    coords = np.array([(x, y, pivot[2]) for x, y in poly.exterior.coords[:-1]], dtype=np.float64)
    shapes: list[BaseGeometry] = []
    for k in range(samples):
        ang = angle_min_rad + (angle_max_rad - angle_min_rad) * k / (samples - 1)
        r = rotation_matrix_axis_angle(axis, ang)
        moved = (coords - pivot) @ r.T + pivot
        shapes.append(Polygon(moved[:, :2]).buffer(0))
    # bridge consecutive samples with their convex hull so the swept region is connected
    bridges = [unary_union([a, b]).convex_hull for a, b in zip(shapes[:-1], shapes[1:])]
    return _largest(unary_union(shapes + bridges))


def _largest(geom: BaseGeometry) -> Polygon:
    if isinstance(geom, Polygon):
        return geom
    polys = [g for g in getattr(geom, "geoms", []) if isinstance(g, Polygon)]
    return max(polys, key=lambda p: p.area) if polys else Polygon()


def centerline_envelope(start_xy: tuple[float, float], end_xy: tuple[float, float], radius: float, arc_resolution: int) -> Polygon:
    return LineString([start_xy, end_xy]).buffer(radius, quad_segs=arc_resolution, cap_style="flat")
