"""Layout validation: intersection, keepout, plate boundary, beam envelope, optical height, moving envelope, tool/cable regions."""

from __future__ import annotations

import math

import numpy as np
from shapely.geometry import Polygon

from ..config import NumericalConfig
from ..contracts.project import ProjectSnapshot
from ..contracts.results import LayoutIssue, LayoutResult
from ..errors import ErrorCode, Status, worst
from ..math.pose import Pose
from ..optics.rays.tracer import TraceOutput
from ..contracts.optics import MeshSurface
from ..scene.lower import _lower_pose, facet_patch_ids
from . import geometry as G


def _issue(kind: str, status: Status, ids: tuple[str, ...], message: str, *, clearance: float | None = None,
           required: float | None = None, contact: tuple[tuple[float, float], ...] = (), bounds: dict | None = None,
           moves: tuple[dict[str, tuple[float, float]], ...] = ()) -> LayoutIssue:
    deficit = None if clearance is None or required is None else max(0.0, required - clearance)
    return LayoutIssue(kind=kind, status=status, device_ids=ids, clearance_mm=clearance, required_clearance_mm=required,
                       deficit_mm=deficit, contact_or_nearest_points=contact, rotated_bounds=bounds or {},
                       suggested_moves=moves, message=message)


def _global_actuator_axis(pose: Pose, axis_local: tuple[float, float, float]) -> np.ndarray:
    a = pose.rotation @ np.array(axis_local, dtype=np.float64)
    return a / np.linalg.norm(a)


def moving_envelope(comp_footprint: Polygon, pose: Pose, actuators: tuple, cfg: NumericalConfig) -> Polygon:
    """Sequentially sweep the footprint over each actuator's travel (translation: exact Minkowski sweep)."""
    poly = comp_footprint
    for act in actuators:
        axis = _global_actuator_axis(pose, act.axis_local)
        if act.dof == "translate":
            poly = G.sweep_translation(poly, axis[0] * act.travel_min, axis[1] * act.travel_min, axis[0] * act.travel_max,
                                       axis[1] * act.travel_max)
        else:
            pivot = pose.apply_points(np.array(act.pivot_local_mm, dtype=np.float64)[None, :])[0]
            poly = G.sweep_rotation(poly, pose.rotation, pivot, axis, math.radians(act.travel_min), math.radians(act.travel_max),
                                    cfg.moving_envelope_rotation_samples)
    return poly


def _pair_issues(a: G.Placed, b: G.Placed, dist: float, bounds_all: dict, cfg: NumericalConfig) -> list[LayoutIssue]:
    """Physical intersection, keepout / clearance and explicit keepout regions for one component pair."""
    tol = cfg.layout_geometry_tol_mm
    found: list[LayoutIssue] = []
    overlap = a.footprint.intersection(b.footprint).area if dist <= tol else 0.0
    required = max(a.required_clearance_mm, b.required_clearance_mm, a.keepout_margin_mm, b.keepout_margin_mm)
    pair_bounds = {a.instance_id: bounds_all[a.instance_id], b.instance_id: bounds_all[b.instance_id]}
    if overlap > tol * tol or dist < required - tol:
        moves = (
            {b.instance_id: G.separation_translation(a.footprint, b.footprint, required, tol)},
            {a.instance_id: G.separation_translation(b.footprint, a.footprint, required, tol)},
        )
        contact = G.nearest_pair(a.footprint, b.footprint)
        if overlap > tol * tol:
            found.append(_issue(ErrorCode.PHYSICAL_INTERSECTION.value, Status.FAIL, (a.instance_id, b.instance_id),
                                f"footprints of {a.instance_id!r} and {b.instance_id!r} overlap (area {overlap:.4g} mm^2)",
                                clearance=0.0, required=required, bounds=pair_bounds, moves=moves, contact=contact))
        else:
            found.append(_issue(ErrorCode.KEEPOUT_VIOLATION.value, Status.FAIL, (a.instance_id, b.instance_id),
                                f"clearance {dist:.4g} mm between {a.instance_id!r} and {b.instance_id!r} is below the required {required:.4g} mm",
                                clearance=dist, required=required, bounds=pair_bounds, moves=moves, contact=contact))
    for owner, other in ((a, b), (b, a)):  # explicit keepout polygons
        for rid, region, z0, z1 in owner.keepout_regions:
            if G.heights_overlap(z0, z1, other.z_min, other.z_max, cfg.layout_height_tol_mm) and region.intersection(other.footprint).area > tol * tol:
                found.append(_issue(ErrorCode.KEEPOUT_VIOLATION.value, Status.FAIL, (owner.instance_id, other.instance_id),
                                    f"{other.instance_id!r} intrudes into keepout region {rid!r} of {owner.instance_id!r}",
                                    clearance=0.0, required=None,
                                    moves=({other.instance_id: G.separation_translation(region, other.footprint, 0.0, tol)},)))
    return found


def run_layout_checks(project: ProjectSnapshot, cfg: NumericalConfig, trace: TraceOutput | None) -> LayoutResult:
    tol = cfg.layout_geometry_tol_mm
    issues: list[LayoutIssue] = []
    checks: list[str] = []
    poses: dict[str, Pose] = {c.instance_id: _lower_pose(c.pose, cfg, f"component:{c.instance_id}") for c in project.components}
    placed: dict[str, G.Placed] = {}
    for comp in project.components:
        pl = G.place(comp, poses[comp.instance_id])
        if pl is None:
            continue
        err = G.validity_error(pl.footprint, tol)
        if err is not None:
            issues.append(_issue(ErrorCode.INVALID_FOOTPRINT.value, Status.FAIL, (comp.instance_id,),
                                 f"footprint of {comp.instance_id!r}: {err}"))
            continue
        placed[comp.instance_id] = pl
    ids = sorted(placed)
    bounds_all = {i: G.bounds(placed[i].footprint) for i in ids}

    # ---- pairwise: physical intersection / keepout / clearance ---------------------------------------------
    checks.append("pairwise_intersection_keepout")
    min_clearance: float | None = None
    min_pair: tuple[str, str] | None = None
    boxes = np.array([bounds_all[i] for i in ids], dtype=np.float64).reshape(-1, 4)
    # Candidate pairs (height-overlapping) ordered by bounding-box distance, a lower bound of the exact footprint distance. Once the
    # bound exceeds every requirement and the current minimum clearance (plus the geometry tolerance), the remaining pairs cannot
    # produce an issue or a tolerance-equivalent new minimum.
    candidates: list[tuple[float, int, int]] = []
    for ai in range(len(ids)):
        for bi in range(ai + 1, len(ids)):
            a, b = placed[ids[ai]], placed[ids[bi]]
            if G.heights_overlap(a.z_min, a.z_max, b.z_min, b.z_max, cfg.layout_height_tol_mm):
                gap_x = max(boxes[bi, 0] - boxes[ai, 2], boxes[ai, 0] - boxes[bi, 2], 0.0)
                gap_y = max(boxes[bi, 1] - boxes[ai, 3], boxes[ai, 1] - boxes[bi, 3], 0.0)
                candidates.append((math.hypot(gap_x, gap_y), ai, bi))
    candidates.sort()
    max_required = max([max(p.required_clearance_mm, p.keepout_margin_mm) for p in placed.values()], default=0.0)
    has_regions = any(p.keepout_regions for p in placed.values())
    per_pair: dict[tuple[int, int], list[LayoutIssue]] = {}
    for box_dist, ai, bi in candidates:
        # A candidate whose bound is within ``tol`` of the running minimum could still be tolerance-equivalent, so it is not pruned.
        if not has_regions and min_clearance is not None and box_dist >= max_required + tol and box_dist >= min_clearance + tol:
            break
        a, b = placed[ids[ai]], placed[ids[bi]]
        dist = G.clearance(a.footprint, b.footprint)
        # Tolerance-aware minimum selection: a strictly lower distance (beyond ``tol``) wins outright; distances within ``tol`` are
        # equivalent and broken lexicographically on the (sorted) instance-id pair so the winner is deterministic across platforms.
        if min_clearance is None or dist < min_clearance - tol:
            min_clearance, min_pair = dist, (a.instance_id, b.instance_id)
        elif dist < min_clearance + tol and min_pair is not None and (a.instance_id, b.instance_id) < min_pair:
            min_clearance, min_pair = dist, (a.instance_id, b.instance_id)
        found = _pair_issues(a, b, dist, bounds_all, cfg)
        if found:
            per_pair[(ai, bi)] = found
    for key in sorted(per_pair):  # emission order independent of the pruning order
        issues.extend(per_pair[key])

    # ---- plate boundary --------------------------------------------------------------------------------------------
    plate = project.baseplate
    if plate is not None:
        checks.append("plate_boundary")
        outline = Polygon(list(plate.outline_mm))
        if not outline.is_valid or outline.area <= tol * tol:
            issues.append(_issue(ErrorCode.INVALID_FOOTPRINT.value, Status.FAIL, ("baseplate",), "invalid baseplate outline"))
        else:
            inner = outline.buffer(-plate.edge_margin_mm, quad_segs=cfg.layout_arc_resolution) if plate.edge_margin_mm > 0 else outline
            for i in ids:
                fp = placed[i].footprint
                if inner.is_empty or not inner.buffer(tol).contains(fp):
                    overshoot = float(max((inner.distance(_pt(c)) for c in fp.exterior.coords), default=0.0))
                    move = _inward_move(fp, inner, tol)
                    issues.append(_issue(ErrorCode.PLATE_BOUNDARY_VIOLATION.value, Status.FAIL, (i,),
                                         f"footprint of {i!r} extends {overshoot:.4g} mm beyond the allowed plate area",
                                         clearance=-overshoot, required=0.0, bounds={i: bounds_all[i]}, moves=({i: move},)))

    # ---- beam envelope and optical height -------------------------------------------------------------------------
    if trace is not None:
        checks.append("beam_envelope")
        checks.append("optical_height")
        owner_of = _surface_owner_map(project)
        seen: set[tuple[str, str]] = set()
        for path in trace.paths:
            prev_owner = owner_of.get(path.branch_surface_id or "") if path.parent_path_id else _source_owner(project, path.source_id)
            for seg in path.segments:
                end_owner = owner_of.get(seg.end_surface_id or "") if seg.end_surface_id else None
                if seg.end_mm is not None and seg.beam_radius_start_mm is not None and seg.beam_radius_end_mm is not None:
                    radius = cfg.beam_envelope_k * max(seg.beam_radius_start_mm, seg.beam_radius_end_mm)
                    env = G.centerline_envelope((seg.start_mm[0], seg.start_mm[1]), (seg.end_mm[0], seg.end_mm[1]), radius,
                                                cfg.layout_arc_resolution)
                    z0 = min(seg.start_mm[2], seg.end_mm[2]) - radius
                    z1 = max(seg.start_mm[2], seg.end_mm[2]) + radius
                    for i in ids:
                        pl = placed[i]
                        if not pl.participates_in_beam or i in (prev_owner, end_owner):
                            continue
                        if not G.heights_overlap(z0, z1, pl.z_min, pl.z_max, cfg.layout_height_tol_mm):
                            continue
                        if env.intersection(pl.footprint).area > tol * tol and (path.path_id, i) not in seen:
                            seen.add((path.path_id, i))
                            line_d = G.clearance(pl.footprint, _line(seg))
                            issues.append(_issue(ErrorCode.BEAM_ENVELOPE_BLOCKED.value, Status.FAIL, (i,),
                                                 f"beam envelope of path {path.path_id!r} ({cfg.beam_envelope_k:g} x beam radius) intersects {i!r}",
                                                 clearance=line_d, required=radius, bounds={i: bounds_all[i]}))
                prev_owner = end_owner
                # optical height at the element hit
                if end_owner is not None and end_owner in placed and seg.end_mm is not None:
                    comp = next(c for c in project.components if c.instance_id == end_owner)
                    env_snap = comp.envelope
                    if env_snap is not None and env_snap.optical_center_local_mm is not None:
                        nominal = poses[end_owner].apply_points(np.array(env_snap.optical_center_local_mm, dtype=np.float64)[None, :])[0, 2]
                        dz = seg.end_mm[2] - float(nominal)
                        if abs(dz) > env_snap.optical_height_tolerance_mm and (path.path_id, "h" + end_owner) not in seen:
                            seen.add((path.path_id, "h" + end_owner))
                            issues.append(_issue(ErrorCode.OPTICAL_HEIGHT_MISMATCH.value, Status.FAIL, (end_owner,),
                                                 f"beam of path {path.path_id!r} meets {end_owner!r} {dz:+.4g} mm from its nominal optical height",
                                                 clearance=abs(dz), required=env_snap.optical_height_tolerance_mm))

    # ---- moving envelopes -------------------------------------------------------------------------------------
    moving = [c for c in project.components if c.actuators and c.instance_id in placed]
    if moving:
        checks.append("moving_envelope")
    for comp in moving:
        pl = placed[comp.instance_id]
        sweep = moving_envelope(pl.footprint, poses[comp.instance_id], comp.actuators, cfg)
        for j in ids:
            if j == comp.instance_id:
                continue
            other = placed[j]
            if G.heights_overlap(pl.z_min, pl.z_max, other.z_min, other.z_max, cfg.layout_height_tol_mm) and sweep.intersection(other.footprint).area > tol * tol:
                issues.append(_issue(ErrorCode.MOVING_ENVELOPE_VIOLATION.value, Status.FAIL, (comp.instance_id, j),
                                     f"moving envelope of {comp.instance_id!r} sweeps into {j!r}", clearance=G.clearance(pl.footprint, other.footprint),
                                     required=None, bounds={comp.instance_id: G.bounds(sweep), j: bounds_all[j]}))
        if plate is not None:
            outline = Polygon(list(plate.outline_mm))
            if outline.is_valid and not outline.buffer(tol).contains(sweep):
                issues.append(_issue(ErrorCode.MOVING_ENVELOPE_VIOLATION.value, Status.FAIL, (comp.instance_id, "baseplate"),
                                     f"moving envelope of {comp.instance_id!r} leaves the baseplate", bounds={comp.instance_id: G.bounds(sweep)}))

    # ---- tool access and cable keepout ------------------------------------------------------------------------
    checks.append("tool_and_cable_regions")
    for i in ids:
        for label, regions, code in (("tool access", placed[i].tool_regions, ErrorCode.TOOL_ACCESS_BLOCKED),
                                     ("cable keepout", placed[i].cable_regions, ErrorCode.CABLE_KEEPOUT_VIOLATION)):
            for rid, region, z0, z1 in regions:
                for j in ids:
                    if j == i:
                        continue
                    other = placed[j]
                    if G.heights_overlap(z0, z1, other.z_min, other.z_max, cfg.layout_height_tol_mm) and region.intersection(other.footprint).area > tol * tol:
                        issues.append(_issue(code.value, Status.FAIL, (i, j), f"{label} region {rid!r} of {i!r} is obstructed by {j!r}",
                                             clearance=0.0, bounds={i: bounds_all[i], j: bounds_all[j]}))

    status = worst([x.status for x in issues], empty=Status.PASS) if ids else Status.NOT_APPLICABLE
    return LayoutResult(status=status, issues=tuple(issues), checks_run=tuple(checks), rotated_bounds=bounds_all,
                        min_clearance_mm=min_clearance, min_clearance_pair=min_pair)


def _pt(coord: tuple[float, float]):  # shapely Point without importing at module import time twice
    from shapely.geometry import Point

    return Point(coord)


def _line(seg):
    from shapely.geometry import LineString

    return LineString([(seg.start_mm[0], seg.start_mm[1]), (seg.end_mm[0], seg.end_mm[1])])


def _inward_move(fp: Polygon, inner: Polygon, tol: float) -> tuple[float, float]:
    if inner.is_empty:
        return (0.0, 0.0)
    target = inner.representative_point()
    cx, cy = fp.centroid.x, fp.centroid.y
    dx, dy = target.x - cx, target.y - cy
    n = math.hypot(dx, dy)
    if n <= tol:
        return (0.0, 0.0)
    ux, uy = dx / n, dy / n
    from shapely import affinity

    lo, hi = 0.0, n
    if not inner.buffer(tol).contains(affinity.translate(fp, ux * hi, uy * hi)):
        return (ux * hi, uy * hi)  # cannot fully fit: move toward the plate interior as far as the target point
    while hi - lo > tol:
        mid = 0.5 * (lo + hi)
        if inner.buffer(tol).contains(affinity.translate(fp, ux * mid, uy * mid)):
            hi = mid
        else:
            lo = mid
    return (ux * hi, uy * hi)


def _surface_owner_map(project: ProjectSnapshot) -> dict[str, str]:
    out: dict[str, str] = {}
    for comp in project.components:
        for s in comp.surfaces:
            out[f"{comp.instance_id}/{s.surface_id}"] = comp.instance_id
            if isinstance(s, MeshSurface):  # (traced as its planar patches)
                out.update((sid, comp.instance_id) for sid in facet_patch_ids(comp.instance_id, s))
    for obs in project.observations:
        if obs.device_id is not None:
            out[f"observation/{obs.observation_id}"] = obs.device_id
    return out


def _source_owner(project: ProjectSnapshot, source_id: str) -> str | None:
    for comp in project.components:
        if comp.source is not None and comp.source.source_id == source_id:
            return comp.instance_id
    return None
