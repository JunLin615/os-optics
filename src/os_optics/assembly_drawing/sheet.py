"""The Detailed Assembly Drawing sheet of one resolved assembly.

Left: FRONT and SIDE elevations (third-angle arrangement, one scale), the PLAN view with the whole breadboard and its
hole grid, and a PICTORIAL (axonometric) view. Right: the description, the dimension table with each dimension's basis,
the adjustments table, the optical spacing table, the alignment procedure, the customer-supplied components table,
notes, the ordered assembly steps, the parts legend (the Customer Purchase BOM lines) and the title block: the Order
Number and the customer prominently, the one check statement (the local assembly check run before drawing: status and
passed / evaluated checks) and, small, the package references. Every text is a template of the message catalog
(``i18n``) in the drawing's locale; lengths are printed to 0.1 mm, angles to 0.1 deg.

Labels avoid each other: beam axes that coincide in a view share one label (their wavelengths listed), a label is put
where it overlaps no other text, no part outline and stays inside its view; fixing-hole tags and customer-supplied
component labels search free positions the same way.

Everything drawn is read from ``DrawingFacts``: outlines are the package part meshes placed by their poses and
classified by ``hidden_lines``; every printed number is a scene value or a detail dimension measured from the poses.
The builder never moves a part. It records what each view shows (visible parts, segment counts), which view carries
each dimension and each balloon, for ``AssemblyDrawingResultV1``.

The sheet: the builder lays the drawing out on the smallest landscape A-series sheet it fits (``layout.SHEETS``:
A3, A2, A1, A0), first with the fewest right-hand text columns and at legible view scales, and only then at any
supported scale. A3 with one text column is the default sheet with its fixed view areas; a larger sheet splits its view
region by the size of the content. The views frame the whole assembly -- every support chain on the breadboard, the
lengthwise design beam axes, the anchors they show and every dimension endpoint of the view -- never one stack.

Several support chains: dimensions of the same kind, basis and value that measure the same span in a view are drawn
once as ``n x value`` (ISO 129 repeated features); when a side of a view would stack more than
``layout.ORDINATE_MIN_LINES`` dimension lines, the dimensions measured from a common datum (board corner, board top)
are drawn as ordinate dimensions; every part is ballooned (leaders search around its own chain); fixation holes are
tagged in the PLAN view and described once in the notes; notes group repeated facts; the steps are grouped per chain;
the right-hand text flows through the text columns and breaks tables between rows.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from .._vendor.shared_contracts.assembly_detail import DimensionBasisV1, DimensionKindV1, DimensionV1, DrawingStatusV1, MeasureAxisV1
from .._vendor.shared_contracts.assembly_preview.scene import FixationModeV1
from . import layout as L
from .canvas import (
    ACCENT,
    DIAGNOSTIC,
    GREY,
    INK,
    PAPER,
    PREVIEW,
    WATERMARK,
    Canvas,
    Rect,
    Style,
    S_BEAM,
    S_BEAM_SECONDARY,
    S_CENTER,
    S_DIAGNOSTIC_BOX,
    S_DIM,
    S_DIM_PREVIEW,
    S_EXTERNAL,
    S_FILL_ACCENT,
    S_FILL_BOARD,
    S_FILL_INK,
    S_FILL_PAPER,
    S_FILL_PREVIEW,
    S_FRAME,
    S_HAIR,
    S_HIDDEN,
    S_OUTLINE,
    S_OUTLINE_THIN,
    S_TABLE,
    S_TABLE_THIN,
    S_THIN,
)
from .geometry import FRONT, PICTORIAL, PLAN, SIDE, PosedMesh, View, circle_points, dist2
from .hidden_lines import DROP, HIDDEN, VISIBLE, ViewScene, chain
from .alignment import alignment_items, beam_paths, spacing_rows
from .model import ChainFacts, DrawingFacts
from .steps import (
    AXIS_LETTER,
    UNMOUNTED_REASON,
    Step,
    customer_rows,
    words_for,
    customer_what,
    dof_rows,
    fastener_text,
    hole_label,
    located,
    outside_board,
    safe,
    world_axis,
)
from .text import mm, paper, text_width, value_text, wrap

V2 = tuple[float, float]
Vec3 = tuple[float, float, float]

TEXT_ASCENT = 0.75
TEXT_DESCENT = 0.25
SEG_GRID_CELL = 5.0
CLEARANCE_CAP = 6.0
LEADER_COST_CROSS = 500.0
LEADER_COST_SEGMENT = 4.0
LEADER_COST_TEXT = 60.0
LEADER_COST_ZONE = 30.0
LEADER_COST_AXIS = 4.0
LEADER_COST_ANCHOR_SHARE = 200.0
LEADER_COST_CLEARANCE = 3.0
ANCHOR_KEEP_FRACTION = 0.8
ANCHOR_MAX_KEPT = 40
CLEAN_BONUS = 1.5
BALLOON_PASSES = 3
RING_WEIGHT = 2.0
ANCHOR_SHARE_MM = 4.0
PART_CLEARANCE = 1.0
HOLE_RING_POINTS = 16
MIN_RUN_PAPER = 0.3
DIAG_ISSUE_LIMIT = 6
MERGE_DECIMALS = 6  # world mm: two dimension spans are the same span when they agree to this many decimals
LENGTHWISE_MM = 1.0  # a beam segment is seen lengthwise in a view when its projection is at least this long (world mm)

#: Dimensions measured from a common datum (the board corner or the board top): drawable as ordinate dimensions.
ORDINATE_KINDS = frozenset({DimensionKindV1.FIXATION_HOLE_POSITION, DimensionKindV1.OPTIC_BOARD_POSITION,
                            DimensionKindV1.OPTICAL_AXIS_HEIGHT})
ELEVATION_VIEWS = {"FRONT": FRONT, "SIDE": SIDE, "PLAN": PLAN}
SIDES = ("LEFT", "TOP", "BOTTOM")


def scale_label(scale: float) -> str:
    if scale >= 1.0:
        return "1:1"
    return f"1:{value_text(1.0 / scale)}"


@dataclass
class ViewStats:
    scale: str
    visible_parts: list[str] = field(default_factory=list)
    visible_segments: int = 0
    hidden_segments: int = 0


@dataclass
class SheetRecord:
    views: dict[str, ViewStats] = field(default_factory=dict)
    dimension_views: dict[str, list[str]] = field(default_factory=dict)
    balloon_views: dict[str, list[str]] = field(default_factory=dict)
    balloon_anchors: dict[str, dict[str, V2]] = field(default_factory=dict)


@dataclass
class _ViewDraw:
    view: View
    scale: float
    origin: V2
    scene: ViewScene
    mesh_refs: list[str]
    area: Rect
    crop_u: tuple[float, float] | None = None

    def P(self, uv: V2) -> V2:
        return (self.origin[0] + self.scale * uv[0], self.origin[1] - self.scale * uv[1])

    def W(self, p: Vec3) -> V2:
        return self.P(self.view.uv(p))

    def to_uv(self, p: V2) -> V2:
        return ((p[0] - self.origin[0]) / self.scale, (self.origin[1] - p[1]) / self.scale)


@dataclass(frozen=True)
class _DimPlan:
    dim: DimensionV1
    view: str
    vertical: bool  # measured along paper v
    side: str  # LEFT / TOP / BOTTOM
    a: Vec3
    b: Vec3
    value: float


@dataclass(frozen=True)
class _DimLine:
    """One drawn dimension: the dimensions of one kind, basis and printed value that measure the same span in a view
    (``n x value``). ``rep`` is the member whose feature is nearest to the side the line is drawn on."""

    rep: _DimPlan
    members: tuple[_DimPlan, ...]


@dataclass(frozen=True)
class _SideLayout:
    """The dimensions of one side of one view: stacked (baseline) lines, and the datum group drawn as ordinates."""

    lines: tuple[_DimLine, ...]
    ordinate: tuple[_DimLine, ...] = ()


@dataclass(frozen=True)
class _ViewSpec:
    """Where and how large one view is drawn (decided before anything is rendered)."""

    view: View
    scale: float
    area: Rect
    slab: PosedMesh
    u_range: tuple[float, float]
    v_range: tuple[float, float]
    margins: tuple[float, float, float, float]
    crop: tuple[float, float] | None


class _Flow:
    """A cursor through the right-hand text columns: a block that does not fit the rest of a column starts the next."""

    def __init__(self, columns: Sequence[Rect], last_bottom: float) -> None:
        self.columns = list(columns)
        self.bottoms = [c[3] for c in self.columns[:-1]] + [last_bottom]
        self.index = 0
        self.overflow = False

    @property
    def x0(self) -> float:
        return self.columns[self.index][0]

    @property
    def x1(self) -> float:
        return self.columns[self.index][2]

    @property
    def top(self) -> float:
        return self.columns[self.index][1]

    @property
    def bottom(self) -> float:
        return self.bottoms[self.index]

    def fit(self, y: float, height: float) -> float:
        """``y`` if a block of ``height`` fits below it in this column, else the top of the next column."""

        if y + height <= self.bottom:
            return y
        if self.index + 1 < len(self.columns):
            self.index += 1
            return self.top
        self.overflow = True
        return y


# ============================================================================== small geometry helpers


def _seg_dist(p: V2, a: V2, b: V2) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    l2 = dx * dx + dy * dy
    t = 0.0 if l2 == 0.0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2))
    ex, ey = p[0] - a[0] - t * dx, p[1] - a[1] - t * dy
    return math.sqrt(ex * ex + ey * ey)


def _segments_cross(a: V2, b: V2, c: V2, d: V2) -> bool:
    def orient(p: V2, q: V2, r: V2) -> float:
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    d1, d2, d3, d4 = orient(c, d, a), orient(c, d, b), orient(a, b, c), orient(a, b, d)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)) and d1 != 0 and d2 != 0 and d3 != 0 and d4 != 0


def _seg_rect(a: V2, b: V2, r: Rect) -> bool:
    x0, y0, x1, y1 = r
    if max(a[0], b[0]) < x0 or min(a[0], b[0]) > x1 or max(a[1], b[1]) < y0 or min(a[1], b[1]) > y1:
        return False
    if any(x0 <= p[0] <= x1 and y0 <= p[1] <= y1 for p in (a, b)):
        return True
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    return any(_segments_cross(a, b, corners[k], corners[(k + 1) % QUAD]) for k in range(QUAD))


QUAD = 4


def _overlap(a: Rect, b: Rect) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


class _SegGrid:
    """Spatial hash over paper segments for clearance and crossing queries."""

    def __init__(self, segs: Sequence[tuple[V2, V2]], cell: float = SEG_GRID_CELL) -> None:
        self.cell = cell
        self.cells: dict[tuple[int, int], list[int]] = {}
        self.segs = list(segs)
        for idx, (a, b) in enumerate(self.segs):
            for key in self._keys(min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])):
                self.cells.setdefault(key, []).append(idx)

    def _keys(self, x0: float, y0: float, x1: float, y1: float) -> list[tuple[int, int]]:
        c = self.cell
        return [(i, j) for i in range(math.floor(x0 / c), math.floor(x1 / c) + 1) for j in range(math.floor(y0 / c), math.floor(y1 / c) + 1)]

    def _near(self, x0: float, y0: float, x1: float, y1: float) -> list[int]:
        out: set[int] = set()
        for key in self._keys(x0, y0, x1, y1):
            out.update(self.cells.get(key, ()))
        return sorted(out)

    def nearest(self, p: V2, radius: float) -> float:
        best = radius
        for idx in self._near(p[0] - radius, p[1] - radius, p[0] + radius, p[1] + radius):
            a, b = self.segs[idx]
            best = min(best, _seg_dist(p, a, b))
        return best

    def crossings(self, a: V2, b: V2) -> int:
        return sum(1 for idx in self._near(min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]))
                   if _segments_cross(a, b, *self.segs[idx]))

    def hits_rect(self, r: Rect) -> bool:
        """Whether a segment touches or crosses the rectangle ``r``."""

        return any(_seg_rect(*self.segs[idx], r) for idx in self._near(r[0], r[1], r[2], r[3]))


def _role_pieces(axis) -> list[tuple[bool, list[Vec3]]]:  # noqa: ANN001 - the vendored BeamAxisV1
    """The axis as consecutive stretches of one display role: ``(secondary, points)``, in path order."""

    points = [tuple(p) for p in axis.points_mm]
    roles = [getattr(r, "value", r) for r in axis.segment_roles] if getattr(axis, "segment_roles", None) else None
    if not roles:
        return [(False, points)]  # type: ignore[list-item]
    out, start = [], 0
    for k in range(1, len(roles) + 1):
        if k == len(roles) or roles[k] != roles[start]:
            out.append((roles[start] == "SECONDARY", points[start:k + 1]))
            start = k
    return out  # type: ignore[return-value]


def _clip_polyline_xy(points: Sequence[Vec3], lo: V2, hi: V2) -> list[list[Vec3]]:
    """Clip a 3-D polyline to the world box lo <= (x, y) <= hi (Liang-Barsky per segment)."""

    out: list[list[Vec3]] = []
    for a, b in zip(points, points[1:]):
        t0, t1 = 0.0, 1.0
        ok = True
        for k in range(2):
            d = b[k] - a[k]
            for p, q in ((-d, a[k] - lo[k]), (d, hi[k] - a[k])):
                if p == 0.0:
                    if q < 0.0:
                        ok = False
                    continue
                r = q / p
                if p < 0.0:
                    t0 = max(t0, r)
                else:
                    t1 = min(t1, r)
        if not ok or t0 >= t1:
            continue
        pa = tuple(a[k] + (b[k] - a[k]) * t0 for k in range(3))
        pb = tuple(a[k] + (b[k] - a[k]) * t1 for k in range(3))
        out.append([pa, pb])  # type: ignore[list-item]
    return out


def _same_point(a: Sequence[float], b: Sequence[float]) -> bool:
    return all(round(a[k], MERGE_DECIMALS) == round(b[k], MERGE_DECIMALS) for k in range(3))


def _lengthwise(view: View, a: Vec3, b: Vec3) -> bool:
    (ua, va), (ub, vb) = view.uv(a), view.uv(b)
    return math.sqrt((ub - ua) * (ub - ua) + (vb - va) * (vb - va)) >= LENGTHWISE_MM


# ============================================================================== the builder


class SheetBuilder:
    def __init__(self, facts: DrawingFacts, *, drawing_status: DrawingStatusV1, validation_status: str, validation_issues: Sequence[str],
                 checker_version: str, steps: Sequence[Step], generator_version: str, validation_interference: str,
                 locale: str = "en", checks_passed: int = 0, checks_evaluated: int = 1,
                 legend_supply: Mapping[str, str] | None = None):
        self.f = facts
        self.locale = locale
        #: The drawing of a local assembly plan: who supplies each part of the legend (by part_ref), from its design BOM.
        self.legend_supply = dict(legend_supply or {})
        self.w = words_for(facts, locale)
        self.checks_passed = checks_passed
        self.checks_evaluated = checks_evaluated
        self.drawing_status = drawing_status
        self.diagnostic = drawing_status is not DrawingStatusV1.DETAILED_ASSEMBLY_DRAWING
        #: The message-key suffix of a diagnostic sheet: "diagnostic" (the check FAILED) or "incomplete" (not completed).
        self.diag_key = "incomplete" if drawing_status is DrawingStatusV1.DIAGNOSTIC_VALIDATION_INCOMPLETE else "diagnostic"
        self.validation_status = validation_status
        self.validation_issues = list(validation_issues)
        self.checker_version = checker_version
        self.validation_interference = validation_interference
        self.steps = list(steps)
        self.generator_version = generator_version
        self.multi = len(facts.chains) > 1
        self.geometry = L.sheet_geometries()[0]
        self.cv = Canvas(self.geometry.spec.width, self.geometry.spec.height)
        self.record = SheetRecord()
        self.segs: list[tuple[V2, V2]] = []
        self.rects: list[Rect] = []
        self.zones: list[Rect] = []
        self.balloons: list[tuple[V2, float]] = []
        self.leaders: list[tuple[V2, V2]] = []
        self.views: dict[str, _ViewDraw] = {}
        self.segs_beam: list[tuple[V2, V2]] = []
        self.beam_labels: dict[str, list[str]] = {}
        self.pending_beam_labels: list[tuple] = []
        self.board_index = -1
        self.flow = _Flow([self.geometry.right], self.geometry.right[3])
        self.dims = self._plan_dimensions()
        self.sides = self._side_layouts()
        self.text_size = RIGHT_COLUMN_SIZES[0]

    @property
    def sheet(self) -> L.SheetSpec:
        return self.geometry.spec

    # ------------------------------------------------------------------ bookkeeping

    def _track(self, subpaths: Sequence[Sequence[V2]], closed: bool = False) -> None:
        for pts in subpaths:
            seq = list(pts) + ([pts[0]] if closed and len(pts) > 2 else [])
            self.segs.extend(zip(seq, seq[1:]))

    def _path(self, subpaths: Sequence[Sequence[V2]], style: Style, closed: bool = False, track: bool = True) -> None:
        self.cv.path(subpaths, style, closed)
        if track:
            self._track(subpaths, closed)

    @staticmethod
    def _text_rect(p: V2, s: str, size: float, *, anchor: str = "start", bold: bool = False, rotate: float = 0.0) -> Rect:
        w = text_width(s, size, bold)
        x0 = {"start": 0.0, "middle": -w / 2.0, "end": -w}[anchor]
        if rotate:
            return (p[0] - size * TEXT_ASCENT, p[1] - x0 - w, p[0] + size * TEXT_DESCENT, p[1] - x0)
        return (p[0] + x0, p[1] - size * TEXT_ASCENT, p[0] + x0 + w, p[1] + size * TEXT_DESCENT)

    def _text(self, p: V2, s: str, size: float, *, anchor: str = "start", bold: bool = False, rotate: float = 0.0,
              color: str = INK, track: bool = True) -> Rect:
        self.cv.text(p, s, size, anchor=anchor, bold=bold, rotate=rotate, color=color)
        r = self._text_rect(p, s, size, anchor=anchor, bold=bold, rotate=rotate)
        if track:
            self.rects.append(r)
        return r

    def _arrowhead(self, tip: V2, direction: V2, style: Style, length: float = L.DIM_ARROW, half: float = L.DIM_ARROW_HALF) -> None:
        ux, uy = direction
        bx, by = tip[0] - ux * length, tip[1] - uy * length
        self.cv.polygon([tip, (bx - uy * half, by + ux * half), (bx + uy * half, by - ux * half)], style)

    # ------------------------------------------------------------------ dimension planning

    def _dim_view(self, dim: DimensionV1) -> str:
        if dim.axis is MeasureAxisV1.WORLD_Z:
            return "FRONT"
        if dim.kind in (DimensionKindV1.FIXATION_HOLE_POSITION, DimensionKindV1.OPTIC_BOARD_POSITION):
            return "PLAN"
        return "FRONT" if dim.axis is MeasureAxisV1.WORLD_X else "SIDE"

    def _plan_dimensions(self) -> list[_DimPlan]:
        plans = []
        top = self.f.board.top_z
        for dim in self.f.detail.detail.dimensions:
            m = self.f.detail.measured[dim.dim_ref]
            view_key = self._dim_view(dim)
            view = ELEVATION_VIEWS[view_key]
            axis = (1.0 if dim.axis is MeasureAxisV1.WORLD_X else 0.0, 1.0 if dim.axis is MeasureAxisV1.WORLD_Y else 0.0,
                    1.0 if dim.axis is MeasureAxisV1.WORLD_Z else 0.0)
            vertical = abs(view.uv(axis)[1]) > abs(view.uv(axis)[0])
            if vertical:
                side = "LEFT"
            elif view_key == "PLAN":
                side = "BOTTOM"
            else:
                side = "TOP" if min(m.from_world_mm[2], m.to_world_mm[2]) > top + L.DIM_FIRST else "BOTTOM"
            plans.append(_DimPlan(dim, view_key, vertical, side, m.from_world_mm, m.to_world_mm, m.value_mm))
        return plans

    def _dims_of(self, view_key: str, side: str | None = None) -> list[_DimPlan]:
        return [d for d in self.dims if d.view == view_key and (side is None or d.side == side)]

    @staticmethod
    def _measure(plan: _DimPlan, point: Vec3) -> float:
        """The coordinate of ``point`` along the paper direction ``plan`` measures (world mm)."""

        return ELEVATION_VIEWS[plan.view].uv(point)[1 if plan.vertical else 0]

    def _span(self, plan: _DimPlan) -> tuple[float, float]:
        a, b = self._measure(plan, plan.a), self._measure(plan, plan.b)
        return min(a, b), max(a, b)

    def _merge_key(self, plan: _DimPlan) -> tuple[object, ...]:
        lo, hi = self._span(plan)
        return (plan.dim.kind, plan.dim.basis, value_text(abs(plan.value)), round(lo, MERGE_DECIMALS), round(hi, MERGE_DECIMALS))

    @staticmethod
    def _nearness(plan: _DimPlan, side: str) -> float:
        """How far a dimension's features are from the side it is drawn on (smaller = nearer)."""

        view = ELEVATION_VIEWS[plan.view]
        (ua, va), (ub, vb) = view.uv(plan.a), view.uv(plan.b)
        if side == "LEFT":
            return max(ua, ub)
        if side == "TOP":
            return -min(va, vb)
        return max(va, vb)

    def _side_layouts(self) -> dict[tuple[str, str], _SideLayout]:
        """Per (view, side): the dimension lines (same kind, basis, value and span merged to one ``n x`` line) and,
        when a side would stack more than ``ORDINATE_MIN_LINES`` lines, its largest common-datum group as ordinates."""

        layouts = {}
        for view_key in ELEVATION_VIEWS:
            for side in SIDES:
                plans = sorted(self._dims_of(view_key, side), key=lambda d: (abs(d.value), d.dim.dim_ref))
                groups: list[list[_DimPlan]] = []
                keys: list[tuple[object, ...]] = []
                for plan in plans:
                    key = self._merge_key(plan)
                    if key in keys:
                        groups[keys.index(key)].append(plan)
                    else:
                        keys.append(key)
                        groups.append([plan])
                lines = [_DimLine(min(g, key=lambda p: self._nearness(p, side)), tuple(g)) for g in groups]
                ordinate: list[_DimLine] = []
                if len(lines) > L.ORDINATE_MIN_LINES:
                    datums: dict[float, list[_DimLine]] = {}
                    for line in lines:
                        if line.rep.dim.kind in ORDINATE_KINDS:
                            datums.setdefault(round(self._measure(line.rep, line.rep.a), MERGE_DECIMALS), []).append(line)
                    best = max(datums.values(), key=len, default=[])
                    if len(best) >= 2:
                        ordinate = best
                        lines = [line for line in lines if all(line is not o for o in ordinate)]
                layouts[(view_key, side)] = _SideLayout(tuple(lines), tuple(ordinate))
        return layouts

    def _count_prefix(self, line: _DimLine) -> str:
        return self.w("dim.repeat", n=len(line.members)) if len(line.members) > 1 else ""

    def _ordinate_text(self, line: _DimLine) -> str:
        """An ordinate label is a coordinate from the datum: it holds for every feature on its extension line."""

        return self._dim_texts(line.rep)[-1]

    def _ordinate_depth(self, layout: _SideLayout) -> float:
        widest = max([text_width(ORDINATE_DATUM_TEXT, L.DIM_TEXT)] + [text_width(self._ordinate_text(line), L.DIM_TEXT)
                                                                       for line in layout.ordinate])
        return L.DIM_FIRST + L.ORD_LEADER + L.ORD_TEXT_GAP + widest

    def _side_extent(self, view_key: str, side: str) -> float:
        """Paper depth the dimensions of one side of a view take beyond its content (0 = none)."""

        layout = self.sides[(view_key, side)]
        n = len(layout.lines)
        if layout.ordinate:
            return self._ordinate_depth(layout) + n * L.DIM_STEP + (L.DIM_TEXT if n else 0.0)
        return 0.0 if n == 0 else L.DIM_FIRST + (n - 1) * L.DIM_STEP + L.DIM_TEXT

    # ------------------------------------------------------------------ view content

    def _stack_meshes(self) -> list[PosedMesh]:
        return [p.mesh for p in self.f.stack() if p.mesh is not None]

    def _anchor_points(self) -> list[Vec3]:
        return [tuple(a.position_mm) for a in self.f.scene.beam_reference.design_anchors]  # type: ignore[misc]

    def _beam_points(self) -> list[Vec3]:
        return [tuple(p) for axis in self.f.scene.beam_reference.beam_axes for p in axis.points_mm]  # type: ignore[misc]

    def _uv_range(self, view: View, points: Sequence[Vec3]) -> tuple[float, float, float, float]:
        uv = [view.uv(p) for p in points]
        return min(p[0] for p in uv), min(p[1] for p in uv), max(p[0] for p in uv), max(p[1] for p in uv)

    def _stack_vertices(self) -> list[Vec3]:
        return [v for m in self._stack_meshes() for v in m.vertices]

    def _segments(self) -> list[tuple[Vec3, Vec3]]:
        return [(tuple(axis.points_mm[k]), tuple(axis.points_mm[k + 1]))  # type: ignore[misc]
                for axis in self.f.scene.beam_reference.beam_axes for k in range(len(axis.points_mm) - 1)]

    def _anchor_shown(self, view: View, anchor) -> bool:  # noqa: ANN001 - the vendored DesignAnchorV1
        """Whether an elevation shows a (customer-supplied or observation) design anchor: the anchor ends a design beam
        axis segment the view sees lengthwise (the one-chain FRONT view without beam axes). Seen end-on, it would sit on the
        optic it feeds."""

        segments = self._segments()
        if not segments:
            return view is FRONT
        p = anchor.position_mm
        return any(_lengthwise(view, a, b) for a, b in segments if _same_point(p, a) or _same_point(p, b))

    def _beam_content(self, view: View) -> list[Vec3]:
        segments = self._segments()
        if not segments:
            return self._anchor_points() if view is FRONT else []
        points = [p for a, b in segments if _lengthwise(view, a, b) for p in (a, b)]
        return points + [tuple(a.position_mm) for a in self.f.scene.beam_reference.design_anchors  # type: ignore[misc]
                         if (a.customer_supplied or a.anchor_kind.value == "OBSERVATION") and self._anchor_shown(view, a)]

    def _elevation_content(self, view: View) -> tuple[tuple[float, float], tuple[float, float]]:
        """The u crop and v range of an elevation: every part, the beam axes it sees lengthwise and the anchors it shows,
        and both ends of every dimension it carries (along the measured direction)."""

        board = self.f.board
        u0, v0, u1, v1 = self._uv_range(view, self._stack_vertices() + self._beam_content(view))
        for plan in self._dims_of(view.key):
            lo, hi = self._span(plan)
            if plan.vertical:
                v0, v1 = min(v0, lo), max(v1, hi)
            else:
                u0, u1 = min(u0, lo), max(u1, hi)
        crop = (u0 - L.VIEW_PAD_MM, u1 + L.VIEW_PAD_MM)
        return crop, (min(v0, board.top_z - board.thickness), v1)

    def _margins(self, view_key: str) -> tuple[float, float, float, float]:
        """Paper margins (left, top, right, bottom) the dimensions and label of a view need beyond its content."""

        def need(side: str) -> float:
            extent = self._side_extent(view_key, side)
            return 0.0 if extent == 0.0 else extent + 1.0

        return need("LEFT"), need("TOP"), 0.0, need("BOTTOM") + L.VIEW_LABEL_GAP + L.VIEW_LABEL_HEIGHT

    def _fits(self, area: Rect, width: float, height: float, margins: tuple[float, float, float, float]) -> bool:
        left, top, right, bottom = margins
        return (width + left + right <= area[2] - area[0] - 2.0 * L.AREA_INSET
                and height + top + bottom <= area[3] - area[1] - 2.0 * L.AREA_INSET)

    def _origin(self, area: Rect, u_range: tuple[float, float], v_range: tuple[float, float], s: float,
                margins: tuple[float, float, float, float]) -> V2:
        left, top, right, bottom = margins
        width = (u_range[1] - u_range[0]) * s + left + right
        height = (v_range[1] - v_range[0]) * s + top + bottom
        x0 = (area[0] + area[2]) / 2.0 - width / 2.0 + left
        y0 = (area[1] + area[3]) / 2.0 - height / 2.0 + top
        return (x0 - u_range[0] * s, y0 + v_range[1] * s)

    def _choose_scale(self, checks: Sequence[tuple[Rect, float, float, tuple[float, float, float, float]]]) -> float:
        for s in L.STANDARD_SCALES:
            if all(self._fits(area, w * s, h * s, m) for area, w, h, m in checks):
                return s
        raise LayoutError(f"the assembly does not fit the {self.sheet.size} sheet at any supported scale")

    def _pictorial_content(self) -> tuple[PosedMesh, tuple[V2, V2], tuple[float, float, float, float]]:
        """The pictorial's board piece (grid aligned around every part), its crop and uv range."""

        board = self.f.board
        verts = self._stack_vertices()
        sx0, sy0, sx1, sy1 = (min(v[0] for v in verts), min(v[1] for v in verts), max(v[0] for v in verts), max(v[1] for v in verts))
        if board.grid_origin is not None and board.grid_pitch is not None:
            (gx, gy), (px, py) = board.grid_origin, board.grid_pitch
            cx0 = gx + px * (math.floor((sx0 - gx) / px) - 1)
            cx1 = gx + px * (math.ceil((sx1 - gx) / px) + 1)
            cy0 = gy + py * (math.floor((sy0 - gy) / py) - 1)
            cy1 = gy + py * (math.ceil((sy1 - gy) / py) + 1)
        else:
            cx0, cx1, cy0, cy1 = sx0 - L.VIEW_PAD_MM, sx1 + L.VIEW_PAD_MM, sy0 - L.VIEW_PAD_MM, sy1 + L.VIEW_PAD_MM
        slab = board.slab(x_range=(cx0, cx1), y_range=(cy0, cy1))
        crop = ((max(cx0, board.x0), max(cy0, board.y0)), (min(cx1, board.x1), min(cy1, board.y1)))
        return slab, crop, self._uv_range(PICTORIAL, verts + list(slab.vertices))

    def _pictorial_scale(self, area: Rect, uv: tuple[float, float, float, float], m_i: tuple[float, float, float, float]) -> float:
        u0, v0, u1, v1 = uv
        avail_w = area[2] - area[0] - 2.0 * L.AREA_INSET - 2.0 * L.BALLOON_RINGS[1]
        avail_h = area[3] - area[1] - 2.0 * L.AREA_INSET - m_i[3] - L.BALLOON_RINGS[1]
        return min(avail_w / (u1 - u0), avail_h / (v1 - v0))

    def _plan_views(self, geometry: L.SheetGeometry, strict: bool) -> dict[str, _ViewSpec] | None:
        """The four views on ``geometry`` (scale, area, content), or ``None`` if they do not fit (``strict``: not at the
        legible minimum scales ``layout.MIN_ELEVATION_SCALE`` / ``MIN_PLAN_SCALE``)."""

        board = self.f.board
        crop_f, v_f = self._elevation_content(FRONT)
        crop_s, v_s = self._elevation_content(SIDE)
        m_f, m_s = self._margins("FRONT"), self._margins("SIDE")
        pts = self._stack_vertices() + [(board.x0, board.y0, board.top_z), (board.x1, board.y1, board.top_z)]
        u0, v0, u1, v1 = self._uv_range(PLAN, pts)
        m_p = self._margins("PLAN")
        slab_i, crop_i, uv_i = self._pictorial_content()
        m_i = (0.0, 0.0, 0.0, L.VIEW_LABEL_GAP + L.VIEW_LABEL_HEIGHT)
        sizes = {"FRONT": (crop_f[1] - crop_f[0], v_f[1] - v_f[0], m_f), "SIDE": (crop_s[1] - crop_s[0], v_s[1] - v_s[0], m_s),
                 "PLAN": (u1 - u0, v1 - v0, m_p)}
        if geometry.is_v012_a3:
            areas = {"FRONT": L.FRONT_AREA, "SIDE": L.SIDE_AREA, "PLAN": L.PLAN_AREA, "PICTORIAL": L.PICTORIAL_AREA}
            try:
                s_el = self._choose_scale([(areas[k], *sizes[k]) for k in ("FRONT", "SIDE")])
                s_p = self._choose_scale([(areas["PLAN"], *sizes["PLAN"])])
            except LayoutError:
                return None
            if strict and (s_el < L.MIN_ELEVATION_SCALE or s_p < L.MIN_PLAN_SCALE):
                return None
        else:
            found = self._computed_areas(geometry.view_region, sizes, strict)
            if found is None:
                return None
            areas, s_el, s_p = found
        s_i = self._pictorial_scale(areas["PICTORIAL"], uv_i, m_i)
        self.pictorial_crop = crop_i
        return {
            "FRONT": _ViewSpec(FRONT, s_el, areas["FRONT"], board.slab(x_range=crop_f), crop_f, v_f, m_f, crop_f),
            "SIDE": _ViewSpec(SIDE, s_el, areas["SIDE"], board.slab(y_range=crop_s), crop_s, v_s, m_s, crop_s),
            "PLAN": _ViewSpec(PLAN, s_p, areas["PLAN"], board.slab(), (u0, u1), (v0, v1), m_p, None),
            "PICTORIAL": _ViewSpec(PICTORIAL, s_i, areas["PICTORIAL"], slab_i, (uv_i[0], uv_i[2]), (uv_i[1], uv_i[3]), m_i, None),
        }

    @staticmethod
    def _need(size: tuple[float, float, tuple[float, float, float, float]], s: float) -> tuple[float, float]:
        """Paper width and height an elevation or the plan needs at scale ``s`` in a computed layout (content, margins,
        the area inset and room for balloons)."""

        w, h, (left, top, right, bottom) = size
        return (w * s + left + right + 2.0 * L.AREA_INSET + 2.0 * L.BALLOON_ROOM,
                h * s + top + bottom + 2.0 * L.AREA_INSET + L.BALLOON_ROOM)

    def _computed_areas(self, region: Rect, sizes: dict[str, tuple[float, float, tuple[float, float, float, float]]],
                        strict: bool) -> tuple[dict[str, Rect], float, float] | None:
        """Split the view region of a larger sheet: FRONT | SIDE above PLAN | PICTORIAL, at the largest elevation scale
        and then the largest plan scale that fit; the space left over goes to the views in proportion."""

        x0, y0, x1, y1 = region
        width, height = x1 - x0, y1 - y0
        gap = L.VIEW_GAP
        pict_w, pict_h = L.PICTORIAL_MIN
        for s_el in L.STANDARD_SCALES:
            if strict and s_el < L.MIN_ELEVATION_SCALE:
                return None
            (fw, fh), (sw, sh) = self._need(sizes["FRONT"], s_el), self._need(sizes["SIDE"], s_el)
            if fw + gap + sw > width:
                continue
            row1 = max(fh, sh)
            for s_p in L.STANDARD_SCALES:
                if s_p > s_el:
                    continue  # the plan is never drawn larger than the elevations
                if strict and s_p < L.MIN_PLAN_SCALE:
                    break
                pw, ph = self._need(sizes["PLAN"], s_p)
                row2 = max(ph, pict_h)
                if pw + gap + pict_w > width or row1 + gap + row2 > height:
                    continue
                spare = height - gap - row1 - row2
                h1 = row1 + spare * row1 / (row1 + row2)
                w_front = fw + (width - gap - fw - sw) * fw / (fw + sw)
                w_plan = pw + (width - gap - pw - pict_w) * pw / (pw + pict_w)
                ym = y0 + h1
                areas = {"FRONT": (x0, y0, x0 + w_front, ym), "SIDE": (x0 + w_front + gap, y0, x1, ym),
                         "PLAN": (x0, ym + gap, x0 + w_plan, y1), "PICTORIAL": (x0 + w_plan + gap, ym + gap, x1, y1)}
                return areas, s_el, s_p
        return None

    def _realize_views(self, specs: dict[str, _ViewSpec]) -> None:
        stack = self._stack_meshes()
        for key in ("FRONT", "SIDE", "PLAN", "PICTORIAL"):
            spec = specs[key]
            self._add_view(spec.view, spec.scale, spec.area, stack, spec.slab, spec.u_range, spec.v_range, spec.margins, spec.crop)

    def _add_view(self, view: View, s: float, area: Rect, stack: list[PosedMesh], slab: PosedMesh, u_range: tuple[float, float],
                  v_range: tuple[float, float], margins: tuple[float, float, float, float], crop: tuple[float, float] | None) -> None:
        meshes = stack + [slab]
        refs = [m.part_ref for m in meshes]
        scene = ViewScene(view, meshes)
        origin = self._origin(area, u_range, v_range, s, margins)
        self.views[view.key] = _ViewDraw(view, s, origin, scene, refs, area, crop)
        self.board_index = len(meshes) - 1

    # ------------------------------------------------------------------ view drawing

    def _render_parts(self, vd: _ViewDraw, *, show_hidden: bool, outline: Style) -> None:
        stats = ViewStats(scale="NTS" if vd.view is PICTORIAL else scale_label(vd.scale))
        scene = vd.scene
        board_mi = len(vd.mesh_refs) - 1
        crop_planes = self._crop_planes(vd)
        order = [p.part_ref for p in self.f.parts]
        per_part: dict[str, tuple[list[list[V2]], list[list[V2]]]] = {}
        for mi, ref in enumerate(vd.mesh_refs):
            vis: list[list[V2]] = []
            hid: list[list[V2]] = []
            for seg in scene.drawable_edges(mi):
                if mi == board_mi and vd.view is not PICTORIAL and self._on_crop_plane(seg.a, seg.b, crop_planes):
                    continue  # a cut end of the broken-out board: drawn as a break line instead
                for st, pts in scene.classify(seg.a, seg.b, mi, seg.shell, vd.scale):
                    if st == DROP:
                        continue
                    run = [vd.P(p) for p in pts]
                    if dist2(run[0], run[-1]) < MIN_RUN_PAPER:
                        continue
                    (vis if st == VISIBLE else hid).append(run)
            per_part[ref] = (chain(vis), chain(hid) if show_hidden else [])
        for ref in order:
            if ref not in per_part:
                continue
            vis, hid = per_part[ref]
            facts = self.f.part(ref)
            self.cv.group({"class": "part", "data-view": vd.view.key, "data-part-ref": ref, "data-line-ref": facts.line_ref,
                           "data-sku": facts.sku})
            if hid:
                self._path(hid, S_HIDDEN)
            if vis:
                self._path(vis, outline)
            self.cv.end()
            if vis:
                stats.visible_parts.append(ref)
            stats.visible_segments += sum(len(p) - 1 for p in vis)
            stats.hidden_segments += sum(len(p) - 1 for p in hid)
        self.record.views[vd.view.key] = stats

    def _crop_planes(self, vd: _ViewDraw) -> list[tuple[int, float]]:
        board = self.f.board
        slab = vd.scene.meshes[-1]
        lo, hi = slab.bounds()
        planes = []
        for axis, (outline_lo, outline_hi) in enumerate(((board.x0, board.x1), (board.y0, board.y1))):
            if lo[axis] > outline_lo:
                planes.append((axis, lo[axis]))
            if hi[axis] < outline_hi:
                planes.append((axis, hi[axis]))
        return planes

    @staticmethod
    def _on_crop_plane(a: Vec3, b: Vec3, planes: list[tuple[int, float]]) -> bool:
        return any(a[axis] == value and b[axis] == value for axis, value in planes)

    def _board_fill(self, vd: _ViewDraw) -> None:
        lo, hi = vd.scene.meshes[-1].bounds()
        corners = [(x, y, z) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
        uv = [vd.W(p) for p in corners]
        self.cv.rect((min(p[0] for p in uv), min(p[1] for p in uv), max(p[0] for p in uv), max(p[1] for p in uv)), S_FILL_BOARD)

    def _break_lines(self, vd: _ViewDraw) -> None:
        board = self.f.board
        for axis, value in self._crop_planes(vd):
            if (vd.view is FRONT and axis != 0) or (vd.view is SIDE and axis != 1):
                continue
            p_top = [0.0, 0.0, board.top_z]
            p_bot = [0.0, 0.0, board.top_z - board.thickness]
            p_top[axis] = p_bot[axis] = value
            other = 1 - axis
            p_top[other] = p_bot[other] = (board.y0 if axis == 0 else board.x0)
            top, bot = vd.W(tuple(p_top)), vd.W(tuple(p_bot))  # type: ignore[arg-type]
            mid = (top[1] + bot[1]) / 2.0
            z = L.BREAK_ZIG
            zig = [(top[0], top[1] - L.BREAK_OVER), (top[0], mid - z), (top[0] - z, mid - z / 2.0), (top[0] + z, mid + z / 2.0),
                   (top[0], mid + z), (bot[0], bot[1] + L.BREAK_OVER)]
            self._path([zig], S_THIN)

    def _hole_symbols(self, vd: _ViewDraw, crop: tuple[V2, V2] | None) -> None:
        board = self.f.board
        if board.grid_count is None:
            return
        r_world = L.HOLE_SYMBOL_R / vd.scale
        stack_boxes = []
        for mi in range(len(vd.mesh_refs) - 1):
            uv = [vd.view.uv(p) for p in vd.scene.meshes[mi].vertices]
            stack_boxes.append((min(p[0] for p in uv), min(p[1] for p in uv), max(p[0] for p in uv), max(p[1] for p in uv)))
        circles: list[tuple[V2, float]] = []
        runs: list[list[V2]] = []
        board_mi = len(vd.mesh_refs) - 1
        for i in range(board.grid_count[0]):
            for j in range(board.grid_count[1]):
                hx, hy = board.hole_xy(i, j)
                if crop is not None and not (crop[0][0] < hx < crop[1][0] and crop[0][1] < hy < crop[1][1]):
                    continue
                ring = circle_points((hx, hy, board.top_z), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), r_world)
                uv = [vd.view.uv(p) for p in ring]
                box = (min(p[0] for p in uv), min(p[1] for p in uv), max(p[0] for p in uv), max(p[1] for p in uv))
                covered = any(b[0] <= box[2] and b[2] >= box[0] and b[1] <= box[3] and b[3] >= box[1] for b in stack_boxes)
                if not covered and vd.view is PLAN:
                    circles.append((vd.W((hx, hy, board.top_z)), L.HOLE_SYMBOL_R))
                    continue
                pts = ring[::len(ring) // HOLE_RING_POINTS] if not covered else ring
                seq = pts + [pts[0]]
                for a, b in zip(seq, seq[1:]):
                    for st, part in vd.scene.classify(a, b, board_mi, 0, vd.scale):
                        if st == VISIBLE:
                            runs.append([vd.P(p) for p in part])
        self.cv.group({"class": "board-holes", "data-view": vd.view.key, "data-part-ref": board.part_ref,
                       "data-grid": f"{board.grid_count[0]}x{board.grid_count[1]}"})
        for c, r in circles:
            self.cv.circle(c, r, S_HAIR)
        if runs:
            self.cv.path(chain(runs), S_HAIR)
        self.cv.end()

    def _grid_indices(self, vd: _ViewDraw) -> None:
        """Hole numbers along the board edges: columns 1..n along +X, rows 1..m along +Y (the notes state the origin)."""

        board = self.f.board
        if board.grid_count is None:
            return
        self.cv.group({"class": "board-grid-index", "data-view": vd.view.key})
        top = vd.W((board.x0, board.y1, board.top_z))[1]
        right = vd.W((board.x1, board.y0, board.top_z))[0]
        for i in range(board.grid_count[0]):
            x = vd.W((board.hole_xy(i, 0)[0], board.y1, board.top_z))[0]
            self._text((x, top - L.DIM_TEXT_OFFSET), str(i + 1), L.TEXT_TINY, anchor="middle", color=GREY)
        for j in range(board.grid_count[1]):
            y = vd.W((board.x1, board.hole_xy(0, j)[1], board.top_z))[1]
            self._text((right + L.DIM_TEXT_OFFSET, y + L.TEXT_TINY / 2.0 - TEXT_DESCENT), str(j + 1), L.TEXT_TINY, color=GREY)
        self.cv.end()

    def _free(self, vd: _ViewDraw, rects: Sequence[Rect], grid: _SegGrid, *, pad: float | None = None) -> bool:
        """Text boxes that stay inside the view, overlap no text, dimension zone or balloon and cross no drawn line."""

        pad = LABEL_PAD if pad is None else pad
        area = vd.area
        board = {len(vd.mesh_refs) - 1}
        for r in rects:
            box = (r[0] - pad, r[1] - pad, r[2] + pad, r[3] + pad)
            if box[0] < area[0] or box[1] < area[1] or box[2] > area[2] or box[3] > area[3]:
                return False
            samples = ((box[0], box[1]), (box[2], box[1]), (box[0], box[3]), (box[2], box[3]),
                       ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0))
            if any(not vd.scene.meshes_at(*vd.to_uv(pt)) <= board for pt in samples):
                return False  # over a part (the breadboard excepted)
            if any(_overlap(box, other) for other in self.rects) or any(_overlap(box, z) for z in self.zones):
                return False
            if any(_overlap(box, (c[0] - rad, c[1] - rad, c[0] + rad, c[1] + rad)) for c, rad in self.balloons):
                return False
            if grid.hits_rect(box):
                return False
        return True

    def _beam_label_spots(self, runs: Sequence[Sequence[V2]], text: str, size: float) -> list[tuple[V2, str]]:
        """Where a beam label may go, in order: beside the first segments of its runs (above / below, right / left of
        points along each segment), then stacked further away."""

        w = text_width(text, size)
        gap = L.DIM_TEXT_OFFSET * 2.0
        spots: list[tuple[V2, str]] = []
        for k in range(LABEL_STACK):
            off = gap + k * size * L.LINE_FACTOR
            for run in runs:
                for a, b in zip(run, run[1:]):
                    if dist2(a, b) < w / 2.0:
                        continue
                    for t in LABEL_ALONG:
                        p = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
                        spots += [((p[0] + gap, p[1] - off), "start"), ((p[0] + gap, p[1] + off + size * TEXT_ASCENT), "start"),
                                  ((p[0] - gap, p[1] - off), "end"), ((p[0] - gap, p[1] + off + size * TEXT_ASCENT), "end")]
        return spots

    def _beam_label(self, vd: _ViewDraw, runs: Sequence[Sequence[V2]], text: str, attrs: dict[str, str]) -> None:
        size = L.TEXT_TINY
        grid = _SegGrid(self.segs)
        spots = self._beam_label_spots(runs, text, size)
        if not spots:
            return
        chosen = next((s for s in spots if self._free(vd, [self._text_rect(s[0], text, size, anchor=s[1])], grid)), None)
        if chosen is None:  # nowhere free: keep the label off the other labels at least
            chosen = next((s for s in spots if not any(_overlap(self._text_rect(s[0], text, size, anchor=s[1]), r)
                                                        for r in self.rects)), spots[0])
        self.cv.group(attrs)
        self._text(chosen[0], text, size, anchor=chosen[1], color=ACCENT)
        self.cv.end()

    def _beam(self, vd: _ViewDraw, clip: tuple[V2, V2] | None = None) -> None:
        """Every design beam axis of the view: its visible runs with an arrow, or, seen end-on, a circle (one end-on label
        per paper row). Labels: axes whose runs coincide in this view share one label naming every wavelength; a label
        text is placed once per view (parallel beams of several stacks), where it overlaps nothing. A stretch only weak
        light reaches (``segment_roles`` SECONDARY) is drawn thinner, with short dashes."""

        entries = []
        weak_runs: dict[int, list[list[V2]]] = {}
        for axis in self.f.scene.beam_reference.beam_axes:
            visible: list[list[V2]] = []
            weak: list[list[V2]] = []
            end_on: list[V2] = []
            for secondary, stretch in _role_pieces(axis):
                pieces = _clip_polyline_xy(stretch, clip[0], clip[1]) if clip is not None else [stretch]  # type: ignore[arg-type]
                for piece in pieces:
                    for a, b in zip(piece, piece[1:]):
                        pa, pb = vd.W(a), vd.W(b)  # type: ignore[arg-type]
                        if dist2(pa, pb) < MIN_RUN_PAPER:
                            end_on.append(pa)
                            continue
                        for st, run in vd.scene.classify(a, b, -1, -1, vd.scale, through_glass=True):  # type: ignore[arg-type]
                            if st == VISIBLE:
                                (weak if secondary else visible).append([vd.P(p) for p in run])
            weak_runs[len(entries)] = chain(weak)
            entries.append((axis, chain(visible) + weak_runs[len(entries)], end_on))
        rows: dict[float, list[int]] = {}
        for index, (_, chained, end_on) in enumerate(entries):
            if not chained and end_on:
                rows.setdefault(round(end_on[0][1], 2), []).append(index)
        labelled = {max(members, key=lambda k: (entries[k][2][0][0], -k)): len(members) for members in rows.values()}
        coincident: dict[tuple, list[int]] = {}
        for index, (_, chained, _) in enumerate(entries):
            if chained:
                key = tuple(sorted(tuple((round(p[0], 1), round(p[1], 1)) for p in run) for run in chained))
                coincident.setdefault(key, []).append(index)
        for index, (axis, chained, end_on) in enumerate(entries):
            self.cv.group({"class": "beam-axis", "data-view": vd.view.key, "data-path-key": axis.path_key, "data-basis": axis.basis.value})
            for run in chained:
                self.segs_beam.extend(zip(run, run[1:]))
            if chained:
                strong = [run for run in chained if not any(run is w for w in weak_runs[index])]
                if strong:
                    self._path(strong, S_BEAM)
                if weak_runs[index]:
                    self._path(weak_runs[index], S_BEAM_SECONDARY)
                first = chained[0]
                a, b = first[0], first[1]
                length = dist2(a, b)
                ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
                tip = (a[0] + ux * min(length / 2.0, L.BEAM_ARROW * QUAD), a[1] + uy * min(length / 2.0, L.BEAM_ARROW * QUAD))
                self._arrowhead(tip, (ux, uy), S_FILL_ACCENT, L.BEAM_ARROW, L.BEAM_ARROW_HALF)
            elif end_on:
                c = end_on[0]
                self.cv.circle(c, L.BEAM_END_ON_R, Style(stroke=ACCENT, width=S_BEAM.width))
                self.cv.circle(c, L.ANCHOR_DOT, S_FILL_ACCENT)
                if index in labelled:
                    stack_right = max(vd.W(p)[0] for mesh in vd.scene.meshes[:-1] for p in mesh.vertices)
                    x = max(c[0] + L.BEAM_END_ON_R, stack_right) + L.DIM_TEXT_OFFSET
                    self._path([[(c[0] + L.BEAM_END_ON_R, c[1]), (x, c[1])]], Style(stroke=ACCENT, width=S_HAIR.width))
                    self._text((x, c[1] - L.DIM_TEXT_OFFSET), self.w("beam.end_on" if labelled[index] == 1 else "beam.end_on_many"),
                               L.TEXT_TINY, color=ACCENT)
                    self._text((x, c[1] + L.TEXT_TINY + L.DIM_TEXT_OFFSET), self.w("beam.towards_viewer"), L.TEXT_TINY, color=ACCENT)
            self.cv.end()
        if vd.view is PICTORIAL:
            return
        placed = self.beam_labels.setdefault(vd.view.key, [])
        for members in coincident.values():
            nms = []
            for k in members:
                nm = entries[k][0].wavelength_nm
                if nm and value_text(nm) not in nms:
                    nms.append(value_text(nm))
            if not nms:
                label = self.w("beam.label")
            else:
                label = self.w("beam.label_nm" if len(nms) == 1 else "beam.labels_nm", nm=", ".join(nms))
            if label in placed:
                continue
            placed.append(label)
            self.pending_beam_labels.append((vd, entries[members[0]][1], label, {
                "class": "beam-label", "data-view": vd.view.key,
                "data-path-keys": " ".join(entries[k][0].path_key for k in members)}))

    def _place_beam_labels(self) -> None:
        """The beam labels of a view, placed after its symbols, anchors and fixing callouts so they avoid them all."""

        for vd, runs, label, attrs in self.pending_beam_labels:
            self._beam_label(vd, runs, label, attrs)
        self.pending_beam_labels = []

    @staticmethod
    def _anchor_kind(anchor) -> str:  # noqa: ANN001 - the vendored DesignAnchorV1
        """SOURCE (a customer-supplied light source), CUSTOMER (a customer-supplied optical component), or the anchor
        kind (an OpenSci optic's COMPONENT anchor, an OBSERVATION)."""

        if anchor.customer_supplied:
            supply = anchor.customer_supply
            if supply is not None and supply.reason.value == UNMOUNTED_REASON:
                return "UNMOUNTED"  # an optic OpenSci supplies without mounting hardware
            return "SOURCE" if supply is None or supply.optical_role == "source" else "CUSTOMER"
        return anchor.anchor_kind.value

    def _reserved_rect(self, vd: _ViewDraw, anchor) -> tuple[V2, V2] | None:  # noqa: ANN001 - DesignAnchorV1
        """The paper rectangle of a customer-supplied component's reserved envelope in a view (None: unknown shape)."""

        supply = anchor.customer_supply
        envelope = None if supply is None else supply.reserved_envelope
        if envelope is None:
            return None
        r = envelope.diameter_mm / 2.0
        q = supply.orientation_wxyz
        corners = []
        for x in (-r, r):
            for y in (-r, r):
                for z in (envelope.z_min_mm, envelope.z_max_mm):
                    w, qx, qy, qz = q
                    tx, ty, tz = 2 * (qy * z - qz * y), 2 * (qz * x - qx * z), 2 * (qx * y - qy * x)
                    v = (x + w * tx + (qy * tz - qz * ty), y + w * ty + (qz * tx - qx * tz), z + w * tz + (qx * ty - qy * tx))
                    corners.append(vd.W(tuple(a + b for a, b in zip(anchor.position_mm, v, strict=True))))  # type: ignore[arg-type]
        lo = (min(c[0] for c in corners), min(c[1] for c in corners))
        hi = (max(c[0] for c in corners), max(c[1] for c in corners))
        return lo, hi

    def _anchor_label(self, vd: _ViewDraw, c: V2, h: float, title: str, sub: str, ids: str) -> None:
        """A customer-supplied anchor's label: the full label below / above / right / left of its symbol where it
        overlaps nothing and stays in the view, else its ids alone, else none (the tables list every component)."""

        grid = _SegGrid(self.segs)
        size = L.TEXT_SMALL
        for lines in self._source_label_options(c, h, title, sub, ids):
            rects = [self._text_rect(p, t, size, bold=b) for p, t, b in lines]
            if self._free(vd, rects, grid):
                for p, t, b in lines:
                    self._text(p, t, size, bold=b, color=GREY)
                return

    def _anchors(self, vd: _ViewDraw, clip: tuple[V2, V2] | None = None) -> None:
        """The customer-supplied sources and components and the observation anchors of a view; anchors on the same paper
        point share one symbol and one label (e.g. several sources seen one behind the other). A customer-supplied
        optical component is a dashed design-position mark: its reserved envelope when its shape is known, else the
        standard symbol with "outline unknown", labelled as the customer's own (never by the catalog model's SKU)."""

        shown: list[tuple[str, V2, list]] = []
        for anchor in self.f.scene.beam_reference.design_anchors:
            p = tuple(anchor.position_mm)
            if clip is not None and not (clip[0][0] <= p[0] <= clip[1][0] and clip[0][1] <= p[1] <= clip[1][1]):
                continue
            if vd.view in (FRONT, SIDE) and not self._anchor_shown(vd.view, anchor):
                continue
            kind = self._anchor_kind(anchor)
            if kind not in ("SOURCE", "CUSTOMER", "UNMOUNTED", "OBSERVATION"):
                continue
            c = vd.W(p)  # type: ignore[arg-type]
            for entry in shown:
                if entry[0] == kind and dist2(entry[1], c) < MIN_RUN_PAPER:
                    entry[2].append(anchor)
                    break
            else:
                shown.append((kind, c, [anchor]))
        deferred: list[tuple[V2, float, str, str, str]] = []
        for kind, c, anchors in shown:
            first = anchors[0]
            ids = ", ".join(safe(a.instance_id) for a in anchors)
            if kind == "SOURCE":
                h = L.EXTERNAL_SYMBOL
                attrs = {"class": "external-source", "data-view": vd.view.key, "data-instance-id": first.instance_id,
                         "data-supply": "CUSTOMER_SUPPLIED"}
                if len(anchors) > 1:
                    attrs["data-instance-ids"] = " ".join(a.instance_id for a in anchors)
                self.cv.group(attrs)
                self._path([[(c[0] - h, c[1] - h), (c[0] + h, c[1] - h), (c[0] + h, c[1] + h), (c[0] - h, c[1] + h)]], S_EXTERNAL, closed=True)
                self._path([[(c[0] - h, c[1] - h), (c[0] + h, c[1] + h)], [(c[0] - h, c[1] + h), (c[0] + h, c[1] - h)]], S_EXTERNAL)
                self.cv.end()
                title = self.w("anchor.source" if len(anchors) == 1 else "anchor.sources", ids=ids)
                deferred.append((c, h, title, self.w("anchor.sub"), ids))
            elif kind in ("CUSTOMER", "UNMOUNTED"):
                supply = first.customer_supply
                attrs = {"class": "customer-supplied", "data-view": vd.view.key, "data-instance-id": first.instance_id,
                         "data-supply": UNMOUNTED_REASON if kind == "UNMOUNTED" else "CUSTOMER_SUPPLIED",
                         "data-reason": supply.reason.value}
                if len(anchors) > 1:
                    attrs["data-instance-ids"] = " ".join(a.instance_id for a in anchors)
                rect = self._reserved_rect(vd, first)
                attrs["data-outline"] = "RESERVED_ENVELOPE" if rect is not None else "UNKNOWN"
                self.cv.group(attrs)
                if rect is None or max(rect[1][0] - rect[0][0], rect[1][1] - rect[0][1]) < 2 * L.EXTERNAL_SYMBOL:
                    h = L.EXTERNAL_SYMBOL
                    (x0, y0), (x1, y1) = (c[0] - h, c[1] - h), (c[0] + h, c[1] + h)
                else:
                    (x0, y0), (x1, y1) = rect
                    h = max(c[0] - x0, x1 - c[0], c[1] - y0, y1 - c[1])
                self._path([[(x0, y0), (x1, y0), (x1, y1), (x0, y1)]], S_EXTERNAL, closed=True)
                self._path([[(x0, y0), (x1, y1)], [(x0, y1), (x1, y0)]], S_EXTERNAL)
                self.cv.end()
                role = self.w.enum("role", supply.optical_role).upper()
                if kind == "UNMOUNTED":
                    title, sub = self.w("anchor.unmounted", ids=ids, role=role), self.w("anchor.sub_unmounted")
                else:
                    title = self.w("anchor.component", ids=ids, role=role)
                    sub = self.w("anchor.sub" if rect is not None else "anchor.sub_unknown")
                deferred.append((c, h, title, sub, ids))
            else:
                self.cv.group({"class": "design-anchor", "data-view": vd.view.key, "data-instance-id": first.instance_id})
                t = L.ANCHOR_TICK
                self._path([[(c[0], c[1] - t), (c[0], c[1] + t)]], Style(stroke=ACCENT, width=S_BEAM.width))
                self._text((c[0], c[1] + t + L.TEXT_TINY), ids, L.TEXT_TINY, anchor="middle", color=ACCENT)
                self.cv.end()
        for c, h, title, sub, ids in deferred:  # labels after every symbol: a label never covers another symbol
            self.cv.group({"class": "customer-supplied-label", "data-view": vd.view.key})
            self._anchor_label(vd, c, h, title, sub, ids)
            self.cv.end()

    @staticmethod
    def _source_label_options(c: V2, h: float, title: str, sub: str, ids: str) -> list[list[tuple[V2, str, bool]]]:
        """Where a customer-supplied anchor's label may go, in order: the full label below, above, right and left of the
        symbol, then its ids alone below / above / right / left."""

        line = L.TEXT_SMALL * L.LINE_FACTOR
        x = c[0] - h
        below, above = c[1] + h + line, c[1] - h - L.TEXT_SMALL * TEXT_DESCENT - L.DIM_TEXT_OFFSET
        right = c[0] + h + L.DIM_TEXT_OFFSET * 2.0
        width = max(text_width(title, L.TEXT_SMALL, True), text_width(sub, L.TEXT_SMALL))
        left = c[0] - h - L.DIM_TEXT_OFFSET * 2.0 - width
        mid = c[1] - line / 2.0 + L.TEXT_SMALL * TEXT_ASCENT
        return [[((x, below), title, True), ((x, below + line), sub, False)],
                [((x, above - line), title, True), ((x, above), sub, False)],
                [((right, mid), title, True), ((right, mid + line), sub, False)],
                [((left, mid), title, True), ((left, mid + line), sub, False)],
                [((x, below), ids, True)], [((x, above), ids, True)], [((right, mid), ids, True)],
                [((c[0] - h - L.DIM_TEXT_OFFSET * 2.0 - text_width(ids, L.TEXT_SMALL, True), mid), ids, True)]]

    def _fixation_marks(self, vd: _ViewDraw) -> None:
        board = self.f.board
        for fixation in self.f.scene.fixations:
            if not fixation.board_holes or board.grid_origin is None:
                continue
            for i, j in fixation.board_holes:
                hx, hy = board.hole_xy(i, j)
                c = vd.W((hx, hy, board.top_z))
                r = L.FIXATION_SYMBOL_R
                self.cv.group({"class": "fixation-hole", "data-view": vd.view.key, "data-part-ref": fixation.part_ref,
                               "data-hole": f"{i},{j}", "data-mode": fixation.mode.value})
                if vd.view is PLAN:
                    self.cv.circle(c, r, Style(width=S_OUTLINE.width))
                    self._path([[(c[0] - r - 1.0, c[1]), (c[0] + r + 1.0, c[1])], [(c[0], c[1] - r - 1.0), (c[0], c[1] + r + 1.0)]], S_CENTER)
                else:
                    top = vd.W((hx, hy, board.top_z))
                    bottom = vd.W((hx, hy, board.top_z - board.thickness))
                    self._path([[(top[0], top[1] - r * 2.0), (bottom[0], bottom[1] + r)]], S_CENTER)
                self.cv.end()

    def _gridded_fixations(self) -> list:
        return [f for f in self.f.scene.fixations if f.board_holes and self.f.board.grid_origin is not None]

    def _fixation_callout(self, vd: _ViewDraw) -> None:
        """A callout of each board fixing, on a free corner of the breadboard in the PLAN view (one fixing); with
        several fixings, or when no board corner is free of the stack and the beam, the hole is tagged instead and
        the notes describe the fixing."""

        if len(self._gridded_fixations()) > 1:
            self._fixation_tags(vd)
            return
        board = self.f.board
        stack_pts = [vd.W(p) for mesh in vd.scene.meshes[:-1] for p in mesh.vertices]
        sx0, sy0 = min(p[0] for p in stack_pts), min(p[1] for p in stack_pts)
        sx1, sy1 = max(p[0] for p in stack_pts), max(p[1] for p in stack_pts)
        bx0, by0 = vd.W((board.x0, board.y1, board.top_z))
        bx1, by1 = vd.W((board.x1, board.y0, board.top_z))
        size = L.TEXT_SMALL
        for fixation in self.f.scene.fixations:
            if not fixation.board_holes or board.grid_origin is None:
                continue
            owner = self.f.part(fixation.part_ref)
            i, j = fixation.board_holes[0]
            hx, hy = board.hole_xy(i, j)
            lines = [self.w("fixing.title", col=i + 1, row=j + 1),
                     self.w("fixing.where", line=owner.line_ref, mode=self.w.enum("fix_mode", fixation.mode.value), x=mm(hx), y=mm(hy))]
            if fixation.fastener is not None:
                lines.append(fastener_text(self.w, fixation.fastener))
            width_limit = (bx1 - bx0) - 2.0 * L.CALLOUT_INSET
            lines = [part for t in lines for part in wrap(t, width_limit - 2.0 * L.CALLOUT_PAD, size, t is lines[0])]
            w = max(text_width(t, size, k == 0) for k, t in enumerate(lines)) + 2.0 * L.CALLOUT_PAD
            h = len(lines) * size * L.LINE_FACTOR + 2.0 * L.CALLOUT_PAD
            inset = L.CALLOUT_INSET
            corners = [(bx1 - inset - w, by1 - inset - h), (bx1 - inset - w, by0 + inset), (bx0 + inset, by0 + inset),
                       (bx0 + inset, by1 - inset - h)]
            box = None
            for cx, cy in corners:
                r = (cx, cy, cx + w, cy + h)
                clear = not (r[0] < sx1 + inset and r[2] > sx0 - inset and r[1] < sy1 + inset and r[3] > sy0 - inset)
                if (clear and not any(_seg_rect(a, b, r) for a, b in self.segs_beam)
                        and not any(_overlap(r, t) for t in self.rects)):
                    box = r
                    break
            if box is None:
                self._fixation_tags(vd, [fixation])
                continue
            c = vd.W((hx, hy, board.top_z))
            self.cv.group({"class": "fixation-callout", "data-view": vd.view.key, "data-part-ref": fixation.part_ref,
                           "data-hole": f"{i},{j}", "data-mode": fixation.mode.value})
            self.cv.rect(box, Style(width=S_HAIR.width, fill=PAPER))
            target = (min(max(c[0], box[0]), box[2]), min(max(c[1], box[1]), box[3]))
            self._path([[target, c]], S_THIN)
            self.cv.circle(c, L.ANCHOR_DOT, S_FILL_INK)
            y = box[1] + L.CALLOUT_PAD + size * TEXT_ASCENT
            for k, t in enumerate(lines):
                self._text((box[0] + L.CALLOUT_PAD, y), t, size, bold=k == 0)
                y += size * L.LINE_FACTOR
            self.rects.append(box)
            self.cv.end()

    def _fixation_tags(self, vd: _ViewDraw, fixations: list | None = None) -> None:
        """Each fixing hole tagged with its part's line and the hole number (column, row), beside its symbol where the
        tag overlaps no part outline and no other text (the first free of eight places around the hole)."""

        board = self.f.board
        r = L.FIXATION_SYMBOL_R
        gap = L.FIXATION_TAG_GAP
        grid = _SegGrid(self.segs)
        for fixation in self._gridded_fixations() if fixations is None else fixations:
            owner = self.f.part(fixation.part_ref)
            for i, j in fixation.board_holes:
                c = vd.W((*board.hole_xy(i, j), board.top_z))
                text = self.w("fixing.tag", line=owner.line_ref, col=i + 1, row=j + 1)
                tw = text_width(text, L.TEXT_TINY, True)
                spots = [(c[0] + r + gap, c[1] - r - gap), (c[0] + r + gap, c[1] + r + gap + L.TEXT_TINY),
                         (c[0] - r - gap - tw, c[1] - r - gap), (c[0] - r - gap - tw, c[1] + r + gap + L.TEXT_TINY),
                         (c[0] - tw / 2.0, c[1] - r - gap * 2.0), (c[0] - tw / 2.0, c[1] + r + gap * 2.0 + L.TEXT_TINY),
                         (c[0] + r + gap, c[1] + L.TEXT_TINY / 2.0), (c[0] - r - gap - tw, c[1] + L.TEXT_TINY / 2.0)]
                at = next((s for s in spots if self._free(vd, [self._text_rect(s, text, L.TEXT_TINY, bold=True)], grid, pad=gap / 2.0)),
                          spots[0])
                self.cv.group({"class": "fixation-tag", "data-view": vd.view.key, "data-part-ref": fixation.part_ref,
                               "data-hole": f"{i},{j}", "data-mode": fixation.mode.value})
                x0, y0, x1, y1 = self._text_rect(at, text, L.TEXT_TINY, bold=True)
                pad = gap / 2.0
                self.cv.rect((x0 - pad, y0 - pad, x1 + pad, y1 + pad), S_FILL_PAPER)
                self._text(at, text, L.TEXT_TINY, bold=True)
                self.cv.end()

    def _view_label(self, vd: _ViewDraw, content_bottom: float) -> None:
        key = vd.view.key
        title = self.w(f"view.{key}")
        if vd.view is PICTORIAL:
            (cx0, cy0), (cx1, cy1) = self.pictorial_crop
            board = self.f.board
            cropped = (cx0, cy0, cx1, cy1) != (board.x0, board.y0, board.x1, board.y1)
            extra = self.w("view_dir.PICTORIAL_CROPPED" if cropped else "view_dir.PICTORIAL")
        else:
            extra = self.w(f"view_dir.{key}")
        x = (vd.area[0] + vd.area[2]) / 2.0
        y = min(content_bottom + L.VIEW_LABEL_GAP, vd.area[3] - L.VIEW_LABEL_HEIGHT + L.TEXT_TITLE)
        self._text((x, y), title, L.TEXT_TITLE, anchor="middle", bold=True)
        w = text_width(title, L.TEXT_TITLE, True)
        self.cv.line((x - w / 2.0, y + L.DIM_TEXT_OFFSET), (x + w / 2.0, y + L.DIM_TEXT_OFFSET), S_OUTLINE_THIN)
        head = self.w("view.not_to_scale") if vd.view is PICTORIAL else self.w("view.scale", scale=scale_label(vd.scale))
        self._text((x, y + L.TEXT_TITLE + 1.0), self.w("view.label", head=head, extra=extra), L.TEXT_SMALL, anchor="middle")

    def _content_bottom(self, vd: _ViewDraw) -> float:
        lo, hi = vd.scene.meshes[-1].bounds()
        pts = [vd.W((x, y, z)) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
        for mesh in vd.scene.meshes[:-1]:
            pts.extend(vd.W(p) for p in mesh.vertices)
        bottom = max(p[1] for p in pts)
        return bottom + (self._side_extent(vd.view.key, "BOTTOM") if vd.view.key in ELEVATION_VIEWS else 0.0)

    def _draw_view(self, key: str) -> None:
        vd = self.views[key]
        self.cv.group({"class": "view", "data-view": key, "data-scale": "NTS" if vd.view is PICTORIAL else scale_label(vd.scale)})
        if vd.view is not PICTORIAL:
            self._board_fill(vd)
        if vd.view is PLAN:
            self._hole_symbols(vd, None)
        elif vd.view is PICTORIAL:
            self._hole_symbols(vd, self.pictorial_crop)
        self._render_parts(vd, show_hidden=vd.view in (FRONT, SIDE), outline=S_OUTLINE_THIN if vd.view is PICTORIAL else S_OUTLINE)
        if vd.view in (FRONT, SIDE):
            self._break_lines(vd)
        if vd.view is PLAN:
            self._grid_indices(vd)
            board = self.f.board
            self._beam(vd, clip=((board.x0, board.y0), (board.x1, board.y1)))
            self._anchors(vd, clip=((board.x0, board.y0), (board.x1, board.y1)))
        elif vd.view is PICTORIAL:
            self._beam(vd, clip=self.pictorial_crop)
            self._anchors(vd, clip=self.pictorial_crop)
        else:
            self._beam(vd)
            self._anchors(vd)
        if vd.view in (FRONT, PLAN):
            self._fixation_marks(vd)
        if vd.view is PLAN:
            self._fixation_callout(vd)
        self._place_beam_labels()
        if vd.view is not PICTORIAL:
            self._dimensions(vd)
        self._view_label(vd, self._content_bottom(vd))
        self.cv.end()

    # ------------------------------------------------------------------ dimensions

    def _local_extent(self, vd: _ViewDraw, side: str, lo: float, hi: float) -> float:
        """The outermost paper coordinate of the stack (or, in PLAN, the board) facing ``side`` over [lo, hi]."""

        scene = vd.scene
        meshes = range(len(scene.meshes)) if vd.view is PLAN or side == "BOTTOM" else range(len(scene.meshes) - 1)
        best: float | None = None
        for mi in meshes:
            mesh = scene.meshes[mi]
            for tri in mesh.triangles:
                pts = [vd.view.uv(mesh.vertices[i]) for i in tri]
                if side == "LEFT":
                    v0, v1 = min(p[1] for p in pts), max(p[1] for p in pts)
                    if v1 >= lo and v0 <= hi:
                        value = vd.P((min(p[0] for p in pts), 0.0))[0]
                        best = value if best is None else min(best, value)
                else:
                    u0, u1 = min(p[0] for p in pts), max(p[0] for p in pts)
                    if u1 >= lo and u0 <= hi:
                        if side == "TOP":
                            value = vd.P((0.0, max(p[1] for p in pts)))[1]
                            best = value if best is None else min(best, value)
                        else:
                            value = vd.P((0.0, min(p[1] for p in pts)))[1]
                            best = value if best is None else max(best, value)
        if side == "TOP":
            for anchor in self._anchor_points():
                u, v = vd.view.uv(anchor)
                if lo <= u <= hi:
                    value = vd.P((u, v))[1] - L.EXTERNAL_SYMBOL
                    best = value if best is None else min(best, value)
        assert best is not None
        return best

    def _dim_texts(self, plan: _DimPlan) -> list[str]:
        v = mm(abs(plan.value))
        basis = plan.dim.basis
        if basis is DimensionBasisV1.RESOLVED:
            return [v]
        if basis is DimensionBasisV1.NOMINAL:
            return [self.w("dim.nominal", v=v), self.w("dim.nominal_short", v=v), self.w("dim.nominal_tiny", v=v)]
        if basis is DimensionBasisV1.REFERENCE:
            return [self.w("dim.reference", v=v), self.w("dim.reference_short", v=v)]
        return [self.w("dim.customer", v=v), self.w("dim.customer_short", v=v), self.w("dim.customer_tiny", v=v)]

    def _dimensions(self, vd: _ViewDraw) -> None:
        placed: list[tuple[str, float, float, float]] = []  # side, lo, hi, paper coordinate
        for side in SIDES:
            layout = self.sides[(vd.view.key, side)]
            if layout.ordinate:
                self._draw_ordinates(vd, side, layout, placed)
            for line in layout.lines:
                plan = line.rep
                ua, ub = vd.view.uv(plan.a), vd.view.uv(plan.b)
                k = 1 if side == "LEFT" else 0
                lo, hi = min(ua[k], ub[k]), max(ua[k], ub[k])
                local = self._local_extent(vd, side, lo, hi)
                pos = local + L.DIM_FIRST if side == "BOTTOM" else local - L.DIM_FIRST
                for s2, lo2, hi2, pos2 in placed:
                    if s2 == side and hi2 >= lo and lo2 <= hi:
                        pos = min(pos, pos2 - L.DIM_STEP) if side in ("LEFT", "TOP") else max(pos, pos2 + L.DIM_STEP)
                placed.append((side, lo, hi, pos))
                self._draw_dimension(vd, line, pos)

    def _dimension_attrs(self, vd: _ViewDraw, line: _DimLine, style: str | None = None) -> dict[str, str]:
        dim = line.rep.dim
        attrs = {"class": "dimension", "data-view": vd.view.key, "data-dim-ref": dim.dim_ref, "data-kind": dim.kind.value,
                 "data-basis": dim.basis.value, "data-axis": dim.axis.value, "data-subject-part-ref": dim.subject_part_ref,
                 "data-value-mm": value_text(line.rep.value)}
        if len(line.members) > 1:
            attrs["data-dim-refs"] = " ".join(m.dim.dim_ref for m in line.members)
            attrs["data-count"] = str(len(line.members))
        if style is not None:
            attrs["data-style"] = style
        return attrs

    def _record(self, vd: _ViewDraw, line: _DimLine) -> None:
        for member in line.members:
            self.record.dimension_views.setdefault(member.dim.dim_ref, []).append(vd.view.key)

    def _draw_dimension(self, vd: _ViewDraw, line: _DimLine, pos: float) -> None:
        plan = line.rep
        dim = plan.dim
        pa, pb = vd.W(plan.a), vd.W(plan.b)
        nominal = dim.basis is DimensionBasisV1.NOMINAL
        style = S_DIM_PREVIEW if nominal else S_DIM
        color = PREVIEW if nominal else (GREY if dim.basis is DimensionBasisV1.REFERENCE else INK)
        arrow_style = S_FILL_PREVIEW if nominal else S_FILL_INK
        self.cv.group(self._dimension_attrs(vd, line))
        size = L.DIM_TEXT
        if plan.vertical:
            x = pos
            for fx, fy in (pa, pb):
                sgn = 1.0 if x > fx else -1.0
                self._path([[(fx + sgn * L.DIM_EXT_GAP, fy), (x + sgn * L.DIM_EXT_OVER, fy)]], S_DIM)
            y0, y1 = min(pa[1], pb[1]), max(pa[1], pb[1])
            span = y1 - y0
            self._path([[(x, y0), (x, y1)]], style)
            self._arrows(x, y0, x, y1, span, arrow_style, vertical=True)
            text = self._fit_text(line, span)
            outside = text_width(text, size) > span - 2.0 * L.DIM_TEXT_CLEAR
            ty = y0 - L.DIM_ARROW * 2.0 if outside else (y0 + y1) / 2.0
            anchor = "start" if outside else "middle"
            self._text((x - L.DIM_TEXT_OFFSET, ty), text, size, anchor=anchor, rotate=ROTATE_UP, color=color)
            self.zones.append((x - size - L.DIM_TEXT_CLEAR, y0, x + L.DIM_TEXT_CLEAR, y1))
        else:
            y = pos
            for fx, fy in (pa, pb):
                sgn = 1.0 if y > fy else -1.0
                self._path([[(fx, fy + sgn * L.DIM_EXT_GAP), (fx, y + sgn * L.DIM_EXT_OVER)]], S_DIM)
            x0, x1 = min(pa[0], pb[0]), max(pa[0], pb[0])
            span = x1 - x0
            self._path([[(x0, y), (x1, y)]], style)
            self._arrows(x0, y, x1, y, span, arrow_style, vertical=False)
            text = self._fit_text(line, span)
            w = text_width(text, size)
            tx = (x0 + x1) / 2.0 if w <= span - 2.0 * L.DIM_TEXT_CLEAR else x1 + L.DIM_ARROW * 2.0 + w / 2.0
            self._text((tx, y - L.DIM_TEXT_OFFSET), text, size, anchor="middle", color=color)
            self.zones.append((x0, y - size - L.DIM_TEXT_CLEAR, x1, y + L.DIM_TEXT_CLEAR))
        self.cv.end()
        self._record(vd, line)

    def _fit_text(self, line: _DimLine, span: float) -> str:
        prefix = self._count_prefix(line)
        options = [prefix + option for option in self._dim_texts(line.rep)]
        for option in options:
            if text_width(option, L.DIM_TEXT) <= span - 2.0 * L.DIM_TEXT_CLEAR:
                return option
        return options[-1]

    def _arrows(self, x0: float, y0: float, x1: float, y1: float, span: float, style: Style, *, vertical: bool) -> None:
        a_dir, b_dir = ((0.0, -1.0), (0.0, 1.0)) if vertical else ((-1.0, 0.0), (1.0, 0.0))
        if span >= L.DIM_ARROW * 3.0:
            self._arrowhead((x0, y0), a_dir, style)
            self._arrowhead((x1, y1), b_dir, style)
        else:
            self._arrowhead((x0, y0), b_dir, style)
            self._arrowhead((x1, y1), a_dir, style)

    def _draw_ordinates(self, vd: _ViewDraw, side: str, layout: _SideLayout, placed: list[tuple[str, float, float, float]]) -> None:
        """Ordinate dimensions of one side: the common datum marked 0, every other value at the end of an extension line
        from its feature (jogged where labels would touch); the band they take is registered like one dimension line."""

        size = L.DIM_TEXT
        k = 1 if side == "LEFT" else 0
        along = 1 if side == "LEFT" else 0  # the paper axis the labels are spread along
        datum = layout.ordinate[0].rep.a
        entries: list[tuple[V2, str, _DimLine | None]] = [(vd.W(datum), ORDINATE_DATUM_TEXT, None)]
        merged: dict[tuple[float, str], list[_DimLine]] = {}
        for line in layout.ordinate:  # features on one extension line with one printed value share one label
            merged.setdefault((round(vd.W(line.rep.b)[along], 2), self._ordinate_text(line)), []).append(line)
        for (_, text), lines in merged.items():
            members = tuple(m for line in lines for m in line.members)
            nearest = min(members, key=lambda m: self._nearness(m, side))
            entries.append((vd.W(nearest.b), text, _DimLine(lines[0].rep, members)))
        coords = [vd.view.uv(datum)[k]] + [vd.view.uv(line.rep.b)[k] for line in layout.ordinate]
        lo, hi = min(coords), max(coords)
        local = self._local_extent(vd, side, lo, hi)
        base = local + L.DIM_FIRST if side == "BOTTOM" else local - L.DIM_FIRST
        pitch = size * L.LINE_FACTOR if side == "LEFT" else size + L.ORD_LABEL_GAP
        entries.sort(key=lambda e: (e[0][along], e[1]))
        slots: list[float] = []
        for feature, _, _ in entries:
            slots.append(feature[along] if not slots else max(feature[along], slots[-1] + pitch))
        widest = 0.0
        for (feature, text, line), slot in zip(entries, slots):
            widest = max(widest, text_width(text, size))
            if line is None:
                self.cv.group({"class": "dimension-datum", "data-view": vd.view.key, "data-style": "ORDINATE"})
                color, style = INK, S_DIM
            else:
                self.cv.group(self._dimension_attrs(vd, line, "ORDINATE"))
                nominal = line.rep.dim.basis is DimensionBasisV1.NOMINAL
                style = S_DIM_PREVIEW if nominal else S_DIM
                color = PREVIEW if nominal else (GREY if line.rep.dim.basis is DimensionBasisV1.REFERENCE else INK)
            fx, fy = feature
            if side == "BOTTOM":
                self._path([[(fx, fy + L.DIM_EXT_GAP), (fx, base), (slot, base + L.ORD_LEADER)]], style)
                self._text((slot + size * (TEXT_ASCENT - TEXT_DESCENT) / 2.0, base + L.ORD_LEADER + L.ORD_TEXT_GAP), text, size,
                           anchor="end", rotate=ROTATE_UP, color=color)
            elif side == "TOP":
                self._path([[(fx, fy - L.DIM_EXT_GAP), (fx, base), (slot, base - L.ORD_LEADER)]], style)
                self._text((slot + size * (TEXT_ASCENT - TEXT_DESCENT) / 2.0, base - L.ORD_LEADER - L.ORD_TEXT_GAP), text, size,
                           rotate=ROTATE_UP, color=color)
            else:
                self._path([[(fx - L.DIM_EXT_GAP, fy), (base, fy), (base - L.ORD_LEADER, slot)]], style)
                self._text((base - L.ORD_LEADER - L.ORD_TEXT_GAP, slot + size * (TEXT_ASCENT - TEXT_DESCENT) / 2.0), text, size,
                           anchor="end", color=color)
            self.cv.end()
            if line is not None:
                self._record(vd, line)
        depth = L.ORD_LEADER + L.ORD_TEXT_GAP + widest
        first, last = min([e[0][along] for e in entries] + slots), max([e[0][along] for e in entries] + slots)
        if side == "BOTTOM":
            far = base + depth
            self.zones.append((first - size, base, last + size, far))
        elif side == "TOP":
            far = base - depth
            self.zones.append((first - size, far, last + size, base))
        else:
            far = base - depth
            self.zones.append((far, first - size, base, last + size))
        placed.append((side, lo, hi, far))

    # ------------------------------------------------------------------ balloons

    def _anchor_candidates(self, vd: _ViewDraw, mi: int, grid: _SegGrid, allow_hidden: bool) -> list[tuple[V2, float]]:
        mesh = vd.scene.meshes[mi]
        uv = [vd.view.uv(p) for p in mesh.vertices]
        u0, v0 = min(p[0] for p in uv), min(p[1] for p in uv)
        u1, v1 = max(p[0] for p in uv), max(p[1] for p in uv)
        step = max(L.BALLOON_ANCHOR_STEP / vd.scale, (u1 - u0) / ANCHOR_GRID_MAX, (v1 - v0) / ANCHOR_GRID_MAX)
        nx, ny = int((u1 - u0) / step), int((v1 - v0) / step)
        centre = ((u0 + u1) / 2.0, (v0 + v1) / 2.0)
        region = vd.area
        found: list[tuple[float, float, V2]] = []
        behind: list[tuple[float, float, V2]] = []  # the part's outline hidden behind others (a buried part)
        for ix in range(1, nx):
            for iy in range(1, ny):
                u, v = u0 + ix * step, v0 + iy * step
                first = vd.scene.first_mesh(u, v)
                at = vd.scene.meshes_at(u, v)
                if first is None or mi not in at:
                    continue
                p = vd.P((u, v))
                if not (region[0] < p[0] < region[2] and region[1] < p[1] < region[3]):
                    continue
                clean = at == {mi}
                clear = min(grid.nearest(p, CLEARANCE_CAP), CLEARANCE_CAP) + (CLEAN_BONUS if clean else 0.0)
                off = math.sqrt((u - centre[0]) * (u - centre[0]) + (v - centre[1]) * (v - centre[1]))
                (found if first[0] == mi else behind).append((round(clear, 2), -round(off, 3), p))
        if not found and allow_hidden:
            found = behind
        if not found:
            return []
        top = max(f[0] for f in found)
        keep = sorted((f for f in found if f[0] >= ANCHOR_KEEP_FRACTION * top), key=lambda f: (-f[0], -f[1], f[2]))
        stride = max(1, len(keep) // ANCHOR_MAX_KEPT)
        return [(f[2], f[0]) for f in keep[::stride]]

    @staticmethod
    def _ring_candidates(vd: _ViewDraw, members: Sequence[int], rings: Sequence[float] = L.BALLOON_RINGS,
                         bottom: bool = False) -> list[tuple[V2, float]]:
        """Balloon centres on rings around the paper box of some meshes: left and right columns and the top row (and,
        with ``bottom``, the bottom row), each with its ring's weight."""

        pts = [vd.W(p) for mi in members for p in vd.scene.meshes[mi].vertices]
        gx0, gy0 = min(p[0] for p in pts), min(p[1] for p in pts)
        gx1, gy1 = max(p[0] for p in pts), max(p[1] for p in pts)
        out: list[tuple[V2, float]] = []
        for ring, off in enumerate(rings):
            y = gy0 - off
            while y <= gy1 + off:
                out.append(((round(gx0 - off, 2), round(y, 2)), ring * RING_WEIGHT))
                out.append(((round(gx1 + off, 2), round(y, 2)), ring * RING_WEIGHT))
                y += L.BALLOON_CANDIDATE_STEP
            x = gx0 - off
            while x <= gx1 + off:
                out.append(((round(x, 2), round(gy0 - off, 2)), ring * RING_WEIGHT))
                if bottom:
                    out.append(((round(x, 2), round(gy1 + off, 2)), ring * RING_WEIGHT))
                x += L.BALLOON_CANDIDATE_STEP
        return out

    def _disk_clear(self, vd: _ViewDraw, c: V2, radius: float, grid: _SegGrid, blocked: Sequence[Rect]) -> bool:
        """A balloon at ``c`` lies inside the view area, on no part but the breadboard, away from drawn lines, text,
        dimension zones and the other balloons."""

        region = vd.area
        margin = radius + L.BALLOON_GAP
        if not (region[0] + radius <= c[0] <= region[2] - radius and region[1] + radius <= c[1] <= region[3] - radius):
            return False
        board = len(vd.mesh_refs) - 1
        for dx, dy in ((0.0, 0.0), (radius, 0.0), (-radius, 0.0), (0.0, radius), (0.0, -radius)):
            if not vd.scene.meshes_at(*vd.to_uv((c[0] + dx, c[1] + dy))) <= {board}:
                return False
        if grid.nearest(c, margin) < margin:
            return False
        if any(r[0] - margin < c[0] < r[2] + margin and r[1] - margin < c[1] < r[3] + margin for r in blocked):
            return False
        return not any(dist2(c, b[0]) < radius + b[1] + L.BALLOON_GAP for b in self.balloons)

    def _place_balloon_stacks(self, key: str) -> None:
        """One stacked balloon group per support chain in view ``key``: its BOM lines side by side in assembly order,
        one leader from the balloon nearest the chain to a visible point of the chain's top-most visible part (for
        assemblies with too many parts to balloon one by one). A chain without a free place keeps its parts for the
        one-by-one fill-in."""

        vd = self.views[key]
        radius = L.BALLOON_R
        pitch = 2.0 * radius
        grid = _SegGrid(self.segs)
        index = {ref: mi for mi, ref in enumerate(vd.mesh_refs)}
        blocked = self.rects + self.zones
        for chain in self.f.chains:
            refs = [r for r in chain.part_refs if r in index and r not in self.record.balloon_views]
            if not refs:
                continue
            target = None
            for ref in reversed(refs):
                anchors = self._anchor_candidates(vd, index[ref], grid, False)
                if anchors:
                    target = (ref, anchors)
                    break
            if target is None:
                continue
            members = [index[r] for r in chain.part_refs if r in index]
            count = sum(len(vd.scene.meshes[mi].vertices) for mi in members)
            centre = (sum(vd.W(p)[0] for mi in members for p in vd.scene.meshes[mi].vertices) / count,
                      sum(vd.W(p)[1] for mi in members for p in vd.scene.meshes[mi].vertices) / count)
            top = max(cl for _, cl in target[1])
            best: tuple[float, list[V2], V2] | None = None
            layouts = []
            for c, w in self._ring_candidates(vd, members, L.STACK_RINGS, bottom=True):
                step = -pitch if c[0] < centre[0] else pitch
                layouts.append((c, w, [(round(c[0] + k * step, 2), c[1]) for k in range(len(refs))]))
            for c, w in self._ring_candidates(vd, members, L.STACK_RINGS, bottom=True):  # a column: the second choice
                step = -pitch if c[1] < centre[1] else pitch
                layouts.append((c, w + STACK_COLUMN_COST, [(c[0], round(c[1] + k * step, 2)) for k in range(len(refs))]))
            for c, w, centres in layouts:
                a, cl = min(target[1], key=lambda ac: (dist2(ac[0], c) + LEADER_COST_CLEARANCE * (top - ac[1]), ac[0]))
                length = dist2(a, c)
                if length < radius + L.BALLOON_MIN_LEADER:
                    continue
                cost = length + w + LEADER_COST_CLEARANCE * (top - cl)
                if best is not None and cost >= best[0]:
                    continue
                if not all(self._disk_clear(vd, p, radius, grid, blocked) for p in centres):
                    continue
                s0 = (c[0] + (a[0] - c[0]) * radius / length, c[1] + (a[1] - c[1]) * radius / length)
                cost += LEADER_COST_CROSS * sum(1 for p, q in self.leaders if _segments_cross(s0, a, p, q))
                cost += LEADER_COST_CROSS * sum(1 for b, _ in self.balloons if _seg_dist(b, s0, a) < radius + L.BALLOON_GAP)
                cost += LEADER_COST_TEXT * sum(1 for r in self.rects if _seg_rect(s0, a, r))
                cost += LEADER_COST_ZONE * sum(1 for r in self.zones if _seg_rect(s0, a, r))
                cost += LEADER_COST_SEGMENT * grid.crossings(s0, a)
                if best is None or cost < best[0]:
                    best = (cost, centres, a)
            if best is None:
                continue
            _, centres, a = best
            s = (centres[0][0] + (a[0] - centres[0][0]) * radius / dist2(a, centres[0]),
                 centres[0][1] + (a[1] - centres[0][1]) * radius / dist2(a, centres[0]))
            first = centres[0]  # the balloon nearest the chain carries the leader
            for ref, c in zip(refs, sorted(centres)):  # read left to right in assembly order
                facts = self.f.part(ref)
                self.cv.group({"class": "balloon", "data-view": key, "data-line-ref": facts.line_ref, "data-part-ref": ref,
                               "data-anchor": f"{paper(a[0])} {paper(a[1])}", "data-stack": str(chain.number),
                               "data-leader-part-ref": target[0]})
                if c == first:
                    self.cv.line(s, a, S_THIN)
                    self.cv.circle(a, L.ANCHOR_DOT, S_FILL_INK)
                self.cv.circle(c, radius, Style(width=S_OUTLINE_THIN.width, fill=PAPER))
                self.cv.text((c[0], c[1] + L.BALLOON_TEXT * TEXT_ASCENT / 2.0 + TEXT_DESCENT), facts.line_ref, L.BALLOON_TEXT,
                             anchor="middle", bold=True)
                self.cv.end()
                self.balloons.append((c, radius))
                self.record.balloon_views.setdefault(ref, []).append(key)
            self.leaders.append((s, a))
            self.record.balloon_anchors.setdefault(target[0], {})[key] = a
            self._track([[centres[0], centres[-1]]])

    def _clusters(self, vd: _ViewDraw) -> list[list[int]]:
        """The meshes whose balloons search one ring of candidate places: the whole assembly (one chain), or each
        support chain on its own (several chains; the breadboard may use every ring)."""

        if not self.multi:
            return [list(range(len(vd.mesh_refs) - 1))]
        index = {ref: mi for mi, ref in enumerate(vd.mesh_refs)}
        return [[index[r] for r in c.part_refs if r in index] for c in self.f.chains]

    def _place_balloons(self, key: str, only: set[str] | None = None, allow_hidden: bool = False) -> None:
        """Balloons of the parts of one view (or of ``only`` those), leaders to visible points of each part; a part
        visible in no view (``allow_hidden``) gets its leader to its hidden outline."""

        vd = self.views[key]
        radius = L.BALLOON_R
        grid = _SegGrid(self.segs)
        anchor_sets = {mi: (self._anchor_candidates(vd, mi, grid, allow_hidden)
                            if (only is None or vd.mesh_refs[mi] in only) and self.f.part(vd.mesh_refs[mi]).on_bom else [])
                       for mi in range(len(vd.mesh_refs))}
        region = vd.area
        clusters = self._clusters(vd)
        seen: dict[V2, int] = {}
        raw: list[tuple[V2, float, int]] = []
        for number, members in enumerate(clusters):
            if not members:
                continue
            for c, w in self._ring_candidates(vd, members):
                if c not in seen:
                    seen[c] = number
                    raw.append((c, w, number))
        blocked = self.rects + self.zones
        margin = radius + L.BALLOON_GAP
        cands = [(c, w, n) for c, w, n in raw
                 if region[0] + radius <= c[0] <= region[2] - radius and region[1] + radius <= c[1] <= region[3] - radius
                 and grid.nearest(c, margin) >= margin
                 and vd.scene.first_mesh(*vd.to_uv(c)) is None
                 and not any(r[0] - margin < c[0] < r[2] + margin and r[1] - margin < c[1] < r[3] + margin for r in blocked)
                 and not any(dist2(c, b[0]) < radius + b[1] + L.BALLOON_GAP for b in self.balloons)]
        home = {mi: number for number, members in enumerate(clusters) for mi in members}

        def start(c: V2, a: V2) -> V2:
            length = dist2(c, a)
            return (c[0] + (a[0] - c[0]) * radius / length, c[1] + (a[1] - c[1]) * radius / length)

        placed: dict[int, tuple[V2, V2]] = {}

        def best_for(mi: int) -> tuple[V2, V2] | None:
            aset = anchor_sets[mi]
            if not aset:
                return None
            top = max(cl for _, cl in aset)
            others = [(start(c, a), a) for k, (c, a) in placed.items() if k != mi]
            best: tuple[float, V2, V2] | None = None
            mine = home.get(mi)
            for c, w, number in cands:
                if mine is not None and number != mine:
                    continue
                if any(dist2(c, pc) < 2.0 * radius + L.BALLOON_GAP for k, (pc, _) in placed.items() if k != mi):
                    continue
                a, cl = min(aset, key=lambda ac: (dist2(ac[0], c) + LEADER_COST_CLEARANCE * (top - ac[1]), ac[0]))
                length = dist2(a, c)
                if length < radius + L.BALLOON_MIN_LEADER:
                    continue
                s0 = start(c, a)
                dx, dy = abs(a[0] - c[0]), abs(a[1] - c[1])
                cost = length + w + LEADER_COST_CLEARANCE * (top - cl)
                if dy < L.TAN_6_DEG * dx or dx < L.TAN_6_DEG * dy:
                    cost += LEADER_COST_AXIS
                if best is not None and cost >= best[0]:
                    continue  # every further term only adds
                cost += LEADER_COST_CROSS * sum(1 for p, q in self.leaders + others if _segments_cross(s0, a, p, q))
                cost += LEADER_COST_CROSS * sum(1 for k, (pc, _) in placed.items() if k != mi and _seg_dist(pc, s0, a) < radius + L.BALLOON_GAP)
                cost += LEADER_COST_ANCHOR_SHARE * sum(1 for k, (_, pa) in placed.items() if k != mi and dist2(pa, a) < ANCHOR_SHARE_MM)
                if best is not None and cost >= best[0]:
                    continue
                cost += LEADER_COST_TEXT * sum(1 for r in self.rects if _seg_rect(s0, a, r))
                cost += LEADER_COST_ZONE * sum(1 for r in self.zones if _seg_rect(s0, a, r))
                if best is not None and cost >= best[0]:
                    continue
                cost += LEADER_COST_SEGMENT * grid.crossings(s0, a)
                if best is None or cost < best[0]:
                    best = (cost, c, a)
            return (best[1], best[2]) if best else None

        order = list(range(len(vd.mesh_refs)))
        for _ in range(BALLOON_PASSES):
            for mi in order:
                found = best_for(mi)
                if found is not None:
                    placed[mi] = found
        for mi in order:
            if mi not in placed:
                continue
            c, a = placed[mi]
            ref = vd.mesh_refs[mi]
            facts = self.f.part(ref)
            s = start(c, a)
            self.cv.group({"class": "balloon", "data-view": key, "data-line-ref": facts.line_ref, "data-part-ref": ref,
                           "data-anchor": f"{paper(a[0])} {paper(a[1])}"})
            self.cv.line(s, a, S_THIN)
            self.cv.circle(a, L.ANCHOR_DOT, S_FILL_INK)
            self.cv.circle(c, radius, Style(width=S_OUTLINE_THIN.width, fill=PAPER))
            self.cv.text((c[0], c[1] + L.BALLOON_TEXT * TEXT_ASCENT / 2.0 + TEXT_DESCENT), facts.line_ref, L.BALLOON_TEXT,
                         anchor="middle", bold=True)
            self.cv.end()
            self.balloons.append((c, radius))
            self.leaders.append((s, a))
            self.record.balloon_views.setdefault(ref, []).append(key)
            self.record.balloon_anchors.setdefault(ref, {})[key] = a

    def _balloon_all_parts(self) -> None:
        """Every part (every Customer Purchase BOM line) gets a balloon. One chain: FRONT and PICTORIAL, then
        the parts still without one in PLAN, SIDE and, to a hidden outline, FRONT / PICTORIAL. Several chains: one
        balloon per part in the PICTORIAL (up to ``BALLOON_ONE_BY_ONE_MAX`` parts), else one stacked group per chain in
        the PLAN view, where no chain hides another; then the rest one by one (FRONT, SIDE, ...), finally hidden."""

        if not self.multi:
            for key in ("FRONT", "PICTORIAL"):
                self._place_balloons(key)
            fill = (("PLAN", False), ("SIDE", False), ("FRONT", True), ("PICTORIAL", True))
        elif len(self.f.bom_parts()) <= L.BALLOON_ONE_BY_ONE_MAX:
            self._place_balloons("PICTORIAL")
            fill = (("FRONT", False), ("SIDE", False), ("PLAN", False), ("FRONT", True), ("PICTORIAL", True))
        else:
            self._place_balloon_stacks("PLAN")
            fill = (("FRONT", False), ("SIDE", False), ("PICTORIAL", False), ("PLAN", False), ("FRONT", True), ("PICTORIAL", True))
        for key, allow_hidden in fill:
            missing = {p.part_ref for p in self.f.bom_parts() if p.part_ref not in self.record.balloon_views}
            if not missing:
                break
            self._place_balloons(key, only=missing, allow_hidden=allow_hidden)
        missing = {p.part_ref for p in self.f.bom_parts() if p.part_ref not in self.record.balloon_views}
        if missing:
            raise LayoutError(f"no balloon could be placed for {sorted(missing)}")

    # ------------------------------------------------------------------ frame, banner, watermark

    def _frame(self) -> None:
        x0, y0, x1, y1 = self.geometry.frame
        width, height = self.sheet.width, self.sheet.height
        b = L.BORDER
        self.cv.group({"class": "frame"})
        self.cv.rect((b, b, width - b, height - b), S_HAIR)
        self.cv.rect(self.geometry.frame, S_FRAME)
        cols, rows = self.sheet.zones
        for i in range(cols + 1):
            x = x0 + (x1 - x0) * i / cols
            if 0 < i < cols:
                self.cv.line((x, b), (x, y0), S_THIN)
                self.cv.line((x, y1), (x, height - b), S_THIN)
            if i < cols:
                xc = x + (x1 - x0) / cols / 2.0
                self.cv.text((xc, y0 - L.FRAME_ZONE_TEXT_INSET + TEXT_DESCENT), str(i + 1), L.TEXT_TINY, anchor="middle", color=GREY)
                self.cv.text((xc, height - b - L.FRAME_ZONE_TEXT_INSET + 1.0), str(i + 1), L.TEXT_TINY, anchor="middle", color=GREY)
        for j in range(rows + 1):
            y = y0 + (y1 - y0) * j / rows
            if 0 < j < rows:
                self.cv.line((b, y), (x0, y), S_THIN)
                self.cv.line((x1, y), (width - b, y), S_THIN)
            if j < rows:
                yc = y + (y1 - y0) / rows / 2.0
                self.cv.text(((b + x0) / 2.0, yc), ZONE_LETTERS[j], L.TEXT_TINY, anchor="middle", color=GREY)
                self.cv.text(((x1 + width - b) / 2.0, yc), ZONE_LETTERS[j], L.TEXT_TINY, anchor="middle", color=GREY)
        self.cv.end()

    def _watermark(self) -> None:
        if not self.diagnostic:
            return
        left, right = self.geometry.left, self.geometry.right
        self.cv.group({"class": "diagnostic-watermark"})
        for cx, cy in ((left[0] + left[2]) / 2.0, (left[1] + left[3]) / 2.0), ((right[0] + right[2]) / 2.0, self.sheet.height / 2.0):
            self.cv.text((cx, cy), self.w("watermark"), L.WATERMARK_TEXT if cx < right[0] else L.TEXT_TITLE * 2.0,
                         anchor="middle", bold=True, rotate=ROTATE_WATERMARK, color=WATERMARK)
        self.cv.end()

    def _banner(self) -> None:
        x0, y0, x1, y1 = banner = self.geometry.banner
        self.cv.group({"class": "notice-banner",
                       "data-status": self.drawing_status.value})
        if self.diagnostic:
            self.cv.rect(banner, S_DIAGNOSTIC_BOX)
            self._text(((x0 + x1) / 2.0, y0 + L.BANNER_TEXT + 1.0), self.w(f"banner.{self.diag_key}"),
                       L.BANNER_TEXT, anchor="middle", bold=True, color=DIAGNOSTIC)
            self._text(((x0 + x1) / 2.0, y1 - 1.0), self.w(f"banner.{self.diag_key}_sub"),
                       L.TEXT_BODY, anchor="middle", bold=True, color=DIAGNOSTIC)
        else:
            self.cv.rect(banner, S_TABLE)
            self._text(((x0 + x1) / 2.0, y0 + L.BANNER_TEXT + 1.0), self.w("banner.title"), L.BANNER_TEXT, anchor="middle", bold=True)
            self._text(((x0 + x1) / 2.0, y1 - 1.0), self.w("banner.subtitle"), L.TEXT_BODY, anchor="middle")
        self.cv.end()

    # ------------------------------------------------------------------ right column

    def _para(self, x0: float, x1: float, y: float, text: str, size: float, *, bold: bool = False, color: str = INK,
              indent: float = 0.0, lead: str | None = None) -> float:
        lines = wrap(text, x1 - x0 - indent, size, bold)
        for i, line in enumerate(lines):
            if i == 0 and lead:
                self._text((x0, y), lead, size, bold=True, track=False)
            self._text((x0 + indent, y), line, size, bold=bold, color=color, track=False)
            y += size * L.LINE_FACTOR
        return y

    def _para_height(self, text: str, size: float, *, bold: bool = False, indent: float = 0.0) -> float:
        return len(wrap(text, self.flow.x1 - self.flow.x0 - indent, size, bold)) * size * L.LINE_FACTOR

    def _heading(self, x0: float, y: float, text: str) -> float:
        self._text((x0, y + L.TEXT_HEAD), text, L.TEXT_HEAD, bold=True, track=False)
        return y + L.TEXT_HEAD * L.LINE_FACTOR + 1.0

    HEADING_H = L.TEXT_HEAD * L.LINE_FACTOR + 1.0

    def _item(self, y: float, text: str, size: float, *, lead: str, indent: float, color: str = INK, bold: bool = False) -> float:
        """One numbered / labelled paragraph of a list (notes, steps, DOF rows), moved to the next column if needed."""

        y = self.flow.fit(y, self._para_height(text, size, bold=bold, indent=indent))
        return self._para(self.flow.x0, self.flow.x1, y + size, text, size, bold=bold, color=color, indent=indent, lead=lead) - size

    def _right_column(self, size: float) -> float:
        flow = self.flow
        x0, x1 = flow.x0, flow.x1
        y = flow.top
        g = self.cv
        # description
        g.group({"class": "drawing-notice"})
        description = self.w("description")
        head, sep, rest = description.partition(". " if self.locale == "en" else "。")
        head += "." if self.locale == "en" else sep
        notice_lines = [(line, True) for line in wrap(head, x1 - x0 - 2.0, size, True)]
        notice_lines += [(line, False) for line in wrap(rest, x1 - x0 - 2.0, size)]
        h = len(notice_lines) * size * L.LINE_FACTOR + 2.0
        g.rect((x0, y, x1, y + h), Style(width=S_TABLE.width, fill="#fbfbfb"))
        yy = y + size + 1.0
        for line, bold in notice_lines:
            self._text((x0 + 1.0, yy), line, size, bold=bold, track=False)
            yy += size * L.LINE_FACTOR
        g.end()
        y += h + L.BLOCK_GAP
        if self.diagnostic:
            y = self._diagnostic_block(y, size)
        y = self._dimension_table(y, size)
        y = self._dof_table(y, size)
        y = self._spacing_table(y, size)
        y = self._alignment(y, size)
        y = self._customer_table(y, size)
        y = self._notes(y, size)
        y = self._steps(y, size)
        return y

    def _diagnostic_block(self, y: float, size: float) -> float:
        lines = [self.w("diag.head", status=self.w.enum("status", self.validation_status))]
        lines += self.validation_issues[:DIAG_ISSUE_LIMIT]
        if len(self.validation_issues) > DIAG_ISSUE_LIMIT:
            lines.append(self.w("diag.more", n=len(self.validation_issues) - DIAG_ISSUE_LIMIT))
        wrapped = [w for line in lines for w in wrap(line, self.flow.x1 - self.flow.x0 - 2.0, size, True)]
        h = len(wrapped) * size * L.LINE_FACTOR + 2.0
        y = self.flow.fit(y, h)
        x0, x1 = self.flow.x0, self.flow.x1
        self.cv.group({"class": "diagnostic-issues"})
        self.cv.rect((x0, y, x1, y + h), S_DIAGNOSTIC_BOX)
        yy = y + size + 1.0
        for i, line in enumerate(wrapped):
            self._text((x0 + 1.0, yy), line, size, bold=i == 0, color=DIAGNOSTIC, track=False)
            yy += size * L.LINE_FACTOR
        self.cv.end()
        return y + h + L.BLOCK_GAP

    def _table_header(self, y: float, size: float, heads: Sequence[str], columns: L.Columns, row: float) -> float:
        x0, x1 = self.flow.x0, self.flow.x1
        self.cv.line((x0, y), (x1, y), S_TABLE)
        for head, ex in zip(heads, columns.edges(x0, x1)):
            self._text((ex + 1.0, y + size + TEXT_DESCENT), head, size * TEXT_SMALL_FACTOR, bold=True, track=False)
        y += row
        self.cv.line((x0, y), (x1, y), S_TABLE_THIN)
        return y

    def _dimension_table(self, y: float, size: float) -> float:
        g = self.cv
        g.group({"class": "dimension-table"})
        row = size * L.LINE_FACTOR + 1.0
        heads = tuple(self.w(f"dim_table.head.{k}") for k in ("ref", "kind", "view", "value", "basis"))
        y = self.flow.fit(y, self.HEADING_H + 2.0 * row)
        y = self._heading(self.flow.x0, y, self.w("dim_table.heading"))
        y = self._table_header(y, size, heads, L.DIMENSION_COLUMNS, row)
        for plan in self.dims:
            column = self.flow.index
            at = self.flow.fit(y, row)
            if self.flow.index != column:  # continued in the next column: close this one, repeat the header
                g.line((self.columns_x(column)[0], y), (self.columns_x(column)[1], y), S_TABLE_THIN)
                y = self._table_header(at, size, heads, L.DIMENSION_COLUMNS, row)
            x0, x1 = self.flow.x0, self.flow.x1
            edges = L.DIMENSION_COLUMNS.edges(x0, x1)
            dim = plan.dim
            value = mm(abs(plan.value))
            shown = f"({value})" if dim.basis is DimensionBasisV1.REFERENCE else value
            tag = "" if dim.basis is DimensionBasisV1.RESOLVED else self.w(f"basis_tag.{dim.basis.value}")
            kind = self.w("dim_table.kind", kind=self.w.enum("kind", dim.kind.value), axis=AXIS_LETTER[dim.axis],
                          line=self.f.line_ref(dim.subject_part_ref))
            color = PREVIEW if dim.basis is DimensionBasisV1.NOMINAL else INK
            g.group({"class": "dimension-row", "data-dim-ref": dim.dim_ref, "data-basis": dim.basis.value, "data-value-mm": value_text(plan.value)})
            cells = (dim.dim_ref, kind, self.w(f"view_short.{plan.view}"), shown + (f" {tag}" if tag else ""),
                     self.w.enum("basis", dim.basis.value))
            for cell, ex, nx in zip(cells, edges, edges[1:]):
                fit = size
                while text_width(cell, fit) > nx - ex - 1.0 and fit > L.TEXT_TINY:
                    fit = round(fit - TEXT_STEP, 2)
                self._text((ex + 1.0, y + size + TEXT_DESCENT), cell, fit, color=color, track=False,
                           bold=cell == cells[3] and dim.basis is DimensionBasisV1.NOMINAL)
            g.end()
            y += row
        g.line((self.flow.x0, y), (self.flow.x1, y), S_TABLE_THIN)
        y += 1.0
        legend = self.w("dim_table.legend")
        if any(len(line.members) > 1 for layout in self.sides.values() for line in layout.lines):
            legend += self.w("dim_table.legend_repeat")
        y = self.flow.fit(y, self._para_height(legend, size * TEXT_SMALL_FACTOR) + size)
        y = self._para(self.flow.x0, self.flow.x1, y + size, legend, size * TEXT_SMALL_FACTOR, color=GREY)
        g.end()
        return y + L.BLOCK_GAP - size

    def columns_x(self, index: int) -> tuple[float, float]:
        column = self.flow.columns[index]
        return column[0], column[2]

    def _dof_table(self, y: float, size: float) -> float:
        rows = dof_rows(self.f, self.locale)
        if not rows:
            return y
        self.cv.group({"class": "dof-table"})
        first = self.w("dof.row", delivery=rows[0][1], detail=rows[0][2])
        y = self.flow.fit(y, self.HEADING_H + self._para_height(first, size, indent=DOF_INDENT))
        y = self._heading(self.flow.x0, y, self.w("dof.heading"))
        for label, delivery, detail, highlighted in rows:
            fixed = delivery == self.w("dof.fixed")
            color = DIAGNOSTIC if fixed else (PREVIEW if highlighted else INK)
            y = self._item(y, self.w("dof.row", delivery=delivery, detail=detail), size, lead=label, indent=DOF_INDENT, color=color)
        self.cv.end()
        return y + L.BLOCK_GAP

    def _spacing_table(self, y: float, size: float) -> float:
        """The design distance between consecutive elements along each nominal beam path (0.1 mm)."""

        rows = spacing_rows(self.f, self.w)
        if not rows:
            return y
        paths = {p.path_key: p for p in beam_paths(self.f, self.w)}
        self.cv.group({"class": "optical-spacing"})
        y = self.flow.fit(y, self.HEADING_H + 2.0 * size * L.LINE_FACTOR)
        y = self._heading(self.flow.x0, y, self.w("spacing.heading"))
        current = None
        for row in rows:
            if row.path_key != current:
                current = row.path_key
                nm = paths[current].wavelength_nm
                title = self.w("spacing.path", path=current, nm=self.w("spacing.path_nm", nm=value_text(nm)) if nm else "")
                y = self.flow.fit(y, size * L.LINE_FACTOR * 2.0)
                self._text((self.flow.x0, y + size), title, size, bold=True, track=False)
                y += size * L.LINE_FACTOR
            text = self.w("spacing.row", a=row.a.label, b=row.b.label, d=mm(row.distance_mm))
            self.cv.group({"class": "spacing-row", "data-path-key": row.path_key, "data-from": row.a.instance_id or "",
                           "data-to": row.b.instance_id or "", "data-distance-mm": mm(row.distance_mm)})
            y = self._item(y, text, size, lead="", indent=NOTE_INDENT) + NOTE_GAP
            self.cv.end()
        self.cv.end()
        return y + L.BLOCK_GAP

    def _alignment(self, y: float, size: float) -> float:
        items = alignment_items(self.f, self.w)
        self.cv.group({"class": "alignment"})
        y = self.flow.fit(y, self.HEADING_H + self._para_height(items[0], size, indent=NOTE_INDENT))
        y = self._heading(self.flow.x0, y, self.w("align.heading"))
        for k, item in enumerate(items, start=1):
            y = self._item(y, item, size, lead=f"{k}.", indent=NOTE_INDENT) + NOTE_GAP
        self.cv.end()
        return y + L.BLOCK_GAP

    def _customer_table(self, y: float, size: float) -> float:
        """Your own components: every design component the customer supplies (not on the parts legend), what it is,
        why the customer supplies it and where it goes -- located from the board corner and the board top, its
        outline when the resolution reserved its space (else "outline unknown"), and a visible statement when it stands
        outside the breadboard."""

        rows = customer_rows(self.f, self.locale)
        if not rows:
            return y
        # the optics OpenSci supplies unmounted have their own table
        for heading, group in (("customer.heading", [r for r in rows if r.reason != UNMOUNTED_REASON]),
                               ("customer.heading_unmounted", [r for r in rows if r.reason == UNMOUNTED_REASON])):
            if group:
                y = self._customer_group(y, size, heading, group)
        return y

    def _customer_group(self, y: float, size: float, heading: str, rows: list) -> float:
        table = self.f.board.customer_table
        items = []
        for row in rows:
            dx, dy, h = located(self.f, row.position)
            off = outside_board(self.f, row.position)
            # outside the board only how far beyond which edge (the warning line) -- never a negative distance from an edge
            where = self.w("customer.where_outside" if off is not None else "customer.where", x=mm(row.position[0]),
                           y=mm(row.position[1]), z=mm(row.position[2]), dx=dx, dy=dy, h=h)
            parts = [customer_what(self.w, row)] + ([] if row.role == "source" else [self.w.enum("reason", row.reason)]) + [where,
                     self.w("customer.axis", axis=world_axis(row.axis))]
            if row.envelope is not None:
                parts.append(self.w("customer.envelope", d=mm(row.envelope[0]), z0=mm(row.envelope[1]), z1=mm(row.envelope[2])))
            elif row.role != "source":
                parts.append(self.w("customer.no_outline"))
            warn = None
            if off is not None:
                warn = self.w("customer.off_table" if table else "customer.off_board", d=mm(off[1]), edge=off[0], h=h)
            items.append((row.instance_id, self.w("step.join").join(parts) + self.w("step.end"), warn))
        self.cv.group({"class": "customer-supplied-table" if heading == "customer.heading" else "supplied-unmounted-table"})
        y = self.flow.fit(y, self.HEADING_H + self._para_height(items[0][1], size, indent=DOF_INDENT))
        y = self._heading(self.flow.x0, y, self.w(heading))
        for lead, text, warn in items:
            self.cv.group({"class": "customer-supplied-row", "data-instance-id": lead,
                           **({"data-outside-board": "true"} if warn else {})})
            y = self._item(y, text, size, lead=safe(lead), indent=DOF_INDENT)
            if warn:
                y = self._item(y, warn, size, lead="", indent=DOF_INDENT, color=DIAGNOSTIC, bold=True)
            self.cv.end()
        self.cv.end()
        return y + L.BLOCK_GAP

    def _fixation_notes(self) -> list[str]:
        f = self.f
        board = f.board
        fixations = f.scene.fixations
        notes = []
        fasteners: list[str] = []
        if len(fixations) > 1:  # several stacks: one note per fixing mode
            groups: dict[str, list] = {}
            for fixation in fixations:
                if fixation.mode is FixationModeV1.MANUAL_BASE_FIXATION:
                    owner = f.part(fixation.part_ref)
                    notes.append(self.w("note.fixing_manual", line=owner.line_ref, sku=owner.sku, op=fixation.manual_operation_ref))
                    continue
                groups.setdefault(fixation.mode.value, []).append(fixation)
                if fixation.fastener is not None:
                    text = fastener_text(self.w, fixation.fastener)
                    if text not in fasteners:
                        fasteners.append(text)
            for mode, members in groups.items():
                where = []
                for fixation in members:
                    holes = ", ".join(hole_label(i, j) for i, j in fixation.board_holes)
                    hx, hy = board.hole_xy(*fixation.board_holes[0]) if fixation.board_holes and board.grid_origin is not None else (0.0, 0.0)
                    where.append(self.w("note.fixing_where", line=f.line_ref(fixation.part_ref), holes=holes, x=mm(hx), y=mm(hy)))
                notes.append(self.w("note.fixings", mode=self.w.enum("fix_mode", mode), board=self._board_name(),
                                    where=self.w("step.join").join(where)))
        else:
            for fixation in fixations:
                owner = f.part(fixation.part_ref)
                if fixation.mode is FixationModeV1.MANUAL_BASE_FIXATION:
                    notes.append(self.w("note.fixing_manual", line=owner.line_ref, sku=owner.sku, op=fixation.manual_operation_ref))
                    continue
                holes = ", ".join(hole_label(i, j) for i, j in fixation.board_holes)
                hx, hy = board.hole_xy(*fixation.board_holes[0]) if fixation.board_holes and board.grid_origin is not None else (0.0, 0.0)
                notes.append(self.w("note.fixing_one", line=owner.line_ref, sku=owner.sku, mode=self.w.enum("fix_mode", fixation.mode.value),
                                    board=self._board_name(), holes=holes, x=mm(hx), y=mm(hy)))
                if fixation.fastener is not None:
                    fasteners.append(fastener_text(self.w, fixation.fastener))
        notes += [self.w("note.fasteners", fastener=text) for text in fasteners]
        return notes

    def _board_name(self) -> str:
        """The breadboard as the notes name it: its BOM line, or the customer's own optical table."""

        board = self.f.board
        return self.w("step.your_table") if board.customer_table else self.w("note.board_name", line=self.f.line_ref(board.part_ref))

    def _source_notes(self) -> list[str]:
        anchors = self.f.scene.beam_reference.design_anchors
        sources = [a for a in anchors if self._anchor_kind(a) == "SOURCE"]
        notes = self._light_source_notes(sources)
        components = [a for a in anchors if self._anchor_kind(a) == "CUSTOMER"]
        if components:
            notes.append(self.w("note.components", ids=", ".join(safe(a.instance_id) for a in components)))
        unmounted = [a for a in anchors if self._anchor_kind(a) == "UNMOUNTED"]
        if unmounted:  # the optics OpenSci supplies without mounting hardware: the customer mounts them
            notes.append(self.w("note.unmounted", ids=", ".join(safe(a.instance_id) for a in unmounted)))
        return notes

    def _light_source_notes(self, sources: list) -> list[str]:
        if len(sources) > 1:
            where = self.w("step.join").join(self.w("note.source_where", id=safe(a.instance_id), x=mm(a.position_mm[0]),
                                                    y=mm(a.position_mm[1]), z=mm(a.position_mm[2])) for a in sources)
            return [self.w("note.sources", where=where)]
        return [self.w("note.source", id=safe(a.instance_id), x=mm(a.position_mm[0]), y=mm(a.position_mm[1]), z=mm(a.position_mm[2]))
                for a in sources]

    def _beam_note(self) -> list[str]:
        reference = self.f.scene.beam_reference
        height = reference.optical_axis_height_mm
        axes = reference.beam_axes
        if not axes:
            return []
        nms: list[str] = []
        for axis in axes:
            if axis.wavelength_nm and value_text(axis.wavelength_nm) not in nms:
                nms.append(value_text(axis.wavelength_nm))
        nm = self.w("note.beam_nm", nm=", ".join(nms)) if nms else ""
        if len(axes) == 1:
            return [self.w("note.beam_one", nm=nm, height=self.w("note.beam_height", h=mm(height)) if height is not None else "")]
        return [self.w("note.beam_many", paths=", ".join(a.path_key for a in axes), nm=nm,
                       height=self.w("note.beam_height", h=mm(height)) if height is not None else self.w("note.beam_heights"))]

    def _notes(self, y: float, size: float) -> float:
        f = self.f
        scene = f.scene
        board = f.board
        notes: list[str] = [self.w("note.outlines_table" if board.customer_table else "note.outlines")]
        if self.multi:
            notes.append(self.w("note.stacks", n=len(f.chains)))
        notes += self._fixation_notes()
        if board.customer_table:
            notes.append(self.w("note.your_table", x0=mm(board.x0), x1=mm(board.x1),
                                y0=mm(board.y0), y1=mm(board.y1)))
        if board.grid_count is not None and board.grid_pitch is not None and board.grid_origin is not None:
            name = self.w("note.area_name") if board.customer_table else self.w("note.board_name", line=f.line_ref(board.part_ref))
            notes.append(self.w("note.board", name=name, cols=board.grid_count[0], rows=board.grid_count[1], thread=board.thread,
                                px=mm(board.grid_pitch[0]), py=mm(board.grid_pitch[1]), x=mm(board.grid_origin[0]),
                                y=mm(board.grid_origin[1])))
        if any(s.kind.value in ("BASE_YAW", "MOUNT_ORIENTATION_YAW") for s in scene.settings):
            notes.append(self.w("note.angles"))
        notes += self._source_notes()
        notes += self._beam_note()
        grouped: dict[str, list[str]] = {}
        unmounted = {a.instance_id for a in scene.beam_reference.design_anchors if self._anchor_kind(a) == "UNMOUNTED"}
        for item in scene.notes:
            subject = (f.line_ref(item.part_ref) if item.part_ref else "") or (
                f"{safe(item.design_instance_id)}{' ' + item.dof_axis.value if item.dof_axis else ''}" if item.design_instance_id else "")
            code = item.code.value
            if code == "CUSTOMER_SUPPLIED_COMPONENT" and item.design_instance_id in unmounted:
                code = "CUSTOMER_SUPPLIED_COMPONENT_unmounted"  # an optic OpenSci supplies unmounted
            grouped.setdefault(code, [])
            if subject and subject not in grouped[code]:
                grouped[code].append(subject)
        if grouped:
            parts = [self.w("note.assembly_item", code=self.w.enum("note_code", code), subjects=", ".join(subjects))
                     if subjects else self.w.enum("note_code", code) for code, subjects in grouped.items()]
            notes.append(self.w("note.assembly", items=self.w("step.join").join(parts)))
        self.cv.group({"class": "notes"})
        y = self.flow.fit(y, self.HEADING_H + self._para_height(notes[0], size, indent=NOTE_INDENT))
        y = self._heading(self.flow.x0, y, self.w("notes.heading"))
        for k, note in enumerate(notes, start=1):
            y = self._item(y, note, size, lead=f"{k}.", indent=NOTE_INDENT) + NOTE_GAP
        self.cv.end()
        return y + L.BLOCK_GAP

    def _chain_title(self, chain: ChainFacts, line_refs: Sequence[str]) -> str:
        ids = self.w("steps.stack_ids", ids=", ".join(safe(i) for i in chain.instance_ids)) if chain.instance_ids else ""
        return self.w("steps.stack", n=chain.number, ids=ids, lines=" / ".join(line_refs))

    def _steps(self, y: float, size: float) -> float:
        self.cv.group({"class": "assembly-steps"})
        heading = self.w("steps.heading_multi" if self.multi else "steps.heading")
        first = self.steps[0].text if self.steps else ""
        y = self.flow.fit(y, self.HEADING_H + self._para_height(first, size, indent=NOTE_INDENT))
        y = self._heading(self.flow.x0, y, heading)
        if self.f.detail.detail.release.delivery_mode == "OPENSCI_ASSEMBLY":  # the steps document an assembled system
            self.cv.group({"class": "opensci-assembly", "data-delivery-mode": "OPENSCI_ASSEMBLY"})
            y = self._item(y, self.w("steps.opensci_assembly"), size, lead="", indent=0.0, bold=True) + NOTE_GAP
            self.cv.end()
        current = 0
        for number, step in enumerate(self.steps, start=1):
            if self.multi and step.chain and step.chain != current:
                current = step.chain
                chain = self.f.chains[current - 1]
                title = self._chain_title(chain, [self.f.line_ref(r) for r in chain.part_refs])
                height = size * L.LINE_FACTOR + CHAIN_GAP + self._para_height(step.text, size, indent=NOTE_INDENT)
                y = self.flow.fit(y, height)
                self.cv.group({"class": "step-chain", "data-chain": str(chain.number), "data-root-part-ref": chain.root_ref})
                self._text((self.flow.x0, y + size + CHAIN_GAP), title, size, bold=True, track=False)
                self.cv.end()
                y += size * L.LINE_FACTOR + CHAIN_GAP
            self.cv.group({"class": "step", "data-step": str(number), "data-action": step.action, "data-part-ref": step.part_ref or "",
                           "data-line-ref": step.line_ref or "",
                           **({"data-instance-id": step.design_instance_id} if step.design_instance_id else {})})
            y = self._item(y, step.text, size, lead=f"{number}.", indent=NOTE_INDENT) + NOTE_GAP
            self.cv.end()
        self.cv.end()
        return y

    def _legend(self, y_bottom: float, size: float) -> float:
        x0, _, x1, _ = self.geometry.text_columns()[-1]
        rows = sorted(self.f.bom_parts(), key=lambda p: (p.line_ref, p.part_ref))
        edges = L.LEGEND_COLUMNS.edges(x0, x1)
        row_h = size * L.LINE_FACTOR + 1.0
        zh_names = self.f.detail.detail.line_display_names_zh_cn if self.locale == "zh-CN" else {}
        name_lines = [wrap(self._legend_name(p, zh_names), edges[3] - edges[2] - 1.0, size * TEXT_SMALL_FACTOR) for p in rows]
        heights = [max(1, len(n)) * size * TEXT_SMALL_FACTOR * L.LINE_FACTOR + 1.0 for n in name_lines]
        title_lines = wrap(self.w("legend.title"), x1 - x0 - 2.0, size, True)
        total = len(title_lines) * size * L.LINE_FACTOR + 1.0 + row_h + sum(heights)
        y = y_bottom - total
        top = y
        g = self.cv
        binding = self.f.preview.manifest.binding
        if binding.local:
            g.group({"class": "parts-legend", "data-source": "DESIGN_BOM",
                     "data-bom-hash": binding.local_plan.design_bom.bom_hash.digest})  # type: ignore[union-attr]
        else:
            bom = binding.commercial_bom
            g.group({"class": "parts-legend", "data-source": "CUSTOMER_PURCHASE_BOM",
                     "data-bom-hash": bom.bom_hash.digest if bom is not None else ""})
        g.rect((x0, top, x1, y_bottom), S_TABLE)
        yy = y + size + TEXT_DESCENT
        for line in title_lines:
            self._text((x0 + 1.0, yy), line, size, bold=True, track=False)
            yy += size * L.LINE_FACTOR
        y += len(title_lines) * size * L.LINE_FACTOR + 1.0
        g.line((x0, y), (x1, y), S_TABLE)
        for head, ex in zip((self.w("legend.head.line"), self.w("legend.head.sku"), self.w("legend.head.name")), edges):
            self._text((ex + 1.0, y + size + TEXT_DESCENT), head, size * TEXT_SMALL_FACTOR, bold=True, track=False)
        y += row_h
        g.line((x0, y), (x1, y), S_TABLE_THIN)
        for p, lines, h in zip(rows, name_lines, heights):
            row_attrs = {"class": "legend-row", "data-line-ref": p.line_ref, "data-part-ref": p.part_ref, "data-sku": p.sku}
            if p.part_ref in self.legend_supply:
                row_attrs["data-supply"] = self.legend_supply[p.part_ref]
            g.group(row_attrs)
            base = y + size * TEXT_SMALL_FACTOR + TEXT_DESCENT
            self._text((edges[0] + 1.0, base), p.line_ref, size, bold=True, track=False)
            fit = size * TEXT_SMALL_FACTOR
            while text_width(p.sku, fit) > edges[2] - edges[1] - 1.0 and fit > L.TEXT_TINY * TEXT_SMALL_FACTOR:
                fit = round(fit - TEXT_STEP, 2)
            self._text((edges[1] + 1.0, base), p.sku, fit, track=False)
            for k, line in enumerate(lines):
                self._text((edges[2] + 1.0, base + k * size * TEXT_SMALL_FACTOR * L.LINE_FACTOR), line, size * TEXT_SMALL_FACTOR, track=False)
            g.end()
            y += h
            g.line((x0, y), (x1, y), S_TABLE_THIN)
        for ex in edges[1:-1]:
            g.line((ex, top + len(title_lines) * size * L.LINE_FACTOR + 1.0), (ex, y_bottom), S_TABLE_THIN)
        g.end()
        return top

    def _legend_name(self, p, zh_names: Mapping[str, str]) -> str:  # noqa: ANN001 - model.PartFacts
        """A legend row's name; on a local plan's drawing a part OpenSci does not supply says who does."""

        name = zh_names.get(p.line_ref) or p.display_name
        supply = self.legend_supply.get(p.part_ref)
        if supply is None or supply == "OPENSCI":
            return name
        return f"{name} ({self.w(f'legend.supply.{supply}')})"

    def _title_block_rect(self) -> Rect:
        x0, _, x1, y1 = self.geometry.text_columns()[-1]
        return (x0, y1 - L.TITLE_BLOCK_H, x1, y1)

    def _fitted(self, text: str, width: float, height: float, *, bold: bool, sizes: Sequence[float]) -> tuple[float, list[str]]:
        """The largest of ``sizes`` at which ``text`` wraps into ``width`` x ``height`` (else the smallest, wrapped)."""

        for size in sizes:
            lines = wrap(text, width, size, bold)
            if len(lines) * size * L.LINE_FACTOR <= height:
                return size, lines
        return sizes[-1], wrap(text, width, sizes[-1], bold)

    def _title_block(self, r: Rect) -> None:
        """The title block: the title, the Order Number and the customer (a confirmed Order; otherwise the release),
        the one assembly check statement (the local assembly check run before drawing: its status and interference);
        then units, size, sheet and projection. The package identities are in the SVG metadata and the drawing result."""

        release = self.f.detail.detail.release
        x0, y0, x1, y1 = r
        g = self.cv
        g.group({"class": "title-block"})
        g.rect(r, S_TABLE)

        def label(cx0: float, cy0: float, text: str) -> None:
            self._text((cx0 + 1.0, cy0 + L.TEXT_TINY + TEXT_DESCENT), text, L.TEXT_TINY, color=GREY, track=False)

        def cell(cx0: float, cy0: float, cx1: float, cy1: float, title: str, value: str, sizes: Sequence[float], *, bold: bool = True,
                 color: str = INK, attrs: dict[str, str] | None = None) -> None:
            g.rect((cx0, cy0, cx1, cy1), S_TABLE_THIN)
            if attrs:
                g.group(attrs)
            label(cx0, cy0, title)
            top = cy0 + L.TEXT_TINY + 1.0
            size, lines = self._fitted(value, cx1 - cx0 - 2.0, cy1 - top - 0.5, bold=bold, sizes=sizes)
            yy = top + size * TEXT_ASCENT + TB_VALUE_DROP
            for line in lines:
                self._text((cx0 + 1.0, yy), line, size, bold=bold, color=color, track=False)
                yy += size * L.LINE_FACTOR
            if attrs:
                g.end()

        ya = y0 + TB_ROW_TITLE
        g.rect((x0, y0, x0 + TB_LOGO_W, ya), S_TABLE_THIN)
        self._text((x0 + TB_LOGO_W / 2.0, y0 + TB_ROW_TITLE / 2.0 + 1.0), "OpenSci", TB_LOGO_TEXT, anchor="middle", bold=True, track=False)
        g.rect((x0 + TB_LOGO_W, y0, x1, ya), S_TABLE_THIN)
        label(x0 + TB_LOGO_W, y0, self.w("tb.label.title"))
        self._text((x0 + TB_LOGO_W + 1.0, y0 + L.TEXT_TINY + 1.0 + L.TEXT_HEAD), self.w("tb.title"), L.TEXT_HEAD + 0.5, bold=True, track=False)
        self._text((x0 + TB_LOGO_W + 1.0, ya - TB_SUB_RAISE), self.w("tb.title_sub"), L.TEXT_SMALL, track=False)
        yb = ya + TB_ROW_ORDER
        big = (TB_ORDER_TEXT, L.TEXT_HEAD, L.TEXT_BODY, L.TEXT_SMALL, L.TEXT_TINY)
        if release.order_number is not None:
            split = x0 + (x1 - x0) * TB_ORDER_SPLIT
            cell(x0, ya, split, yb, self.w("tb.label.order"), release.order_number, big, attrs={"class": "order-number"})
            cell(split, ya, x1, yb, self.w("tb.label.customer"), release.customer_display_name or "-", big,
                 attrs={"class": "customer"})
        else:
            cell(x0, ya, x1, yb, self.w("tb.label.release"), self.w.enum("tb.release", release.basis.value), big[1:])
        yc = yb + TB_ROW_CHECK
        interference = self.w.enum("interference", self.validation_interference)
        if self.diagnostic:
            statement = self.w("tb.check_incomplete" if self.diag_key == "incomplete" else "tb.check_diag",
                               passed=self.checks_passed, evaluated=self.checks_evaluated, interference=interference)
        else:
            statement = self.w("tb.check", status=self.w.enum("status", self.validation_status), interference=interference)
        g.rect((x0, yb, x1, yc), Style(width=S_TABLE.width, fill="#fde8e8" if self.diagnostic else "#f3f3f3"))
        cell(x0, yb, x1, yc, self.w("tb.label.check"), statement, (L.TEXT_BODY, L.TEXT_SMALL, L.TEXT_TINY),
             color=DIAGNOSTIC if self.diagnostic else INK, attrs={"class": "check-statement", "data-status": self.validation_status,
                                                                  "data-checks-passed": str(self.checks_passed),
                                                                  "data-checks-evaluated": str(self.checks_evaluated)})
        cells = [("units", "mm"), ("size", self.sheet.size), ("sheet", "1 / 1"), ("projection", self.w("tb.projection"))]
        edges = TB_BOTTOM_COLUMNS.edges(x0, x1)
        for (key, value), a, b in zip(cells, edges, edges[1:]):
            cell(a, yc, b, y1, self.w(f"tb.label.{key}"), value, (L.TEXT_BODY, L.TEXT_SMALL, L.TEXT_TINY), bold=key == "sheet")
        g.end()

    # ------------------------------------------------------------------ layout choice and build

    def _text_fits(self, geometry: L.SheetGeometry) -> float | None:
        """The largest text size at which notice, tables, notes, steps, legend and title block fit ``geometry``'s text
        columns (laid out on a scratch canvas), or ``None``."""

        keep_geometry, keep_canvas = self.geometry, self.cv
        self.geometry = geometry
        try:
            columns = geometry.text_columns()
            for size in RIGHT_COLUMN_SIZES:
                self.cv = Canvas(geometry.spec.width, geometry.spec.height)
                legend_top = self._legend(self._title_block_rect()[1] - L.BLOCK_GAP, size)
                if legend_top - L.BLOCK_GAP <= columns[-1][1]:
                    continue
                self.flow = _Flow(columns, legend_top - L.BLOCK_GAP)
                bottom = self._right_column(size)
                if not self.flow.overflow and bottom <= self.flow.bottom:
                    return size
            return None
        finally:
            self.geometry, self.cv = keep_geometry, keep_canvas

    def _choose_layout(self) -> tuple[L.SheetGeometry, dict[str, _ViewSpec], float]:
        """The smallest sheet (then the fewest text columns) whose views fit at legible scales and whose text fits;
        failing that, the smallest whose views fit at any supported scale."""

        text: dict[tuple[str, int], float | None] = {}
        for strict in (True, False):
            for geometry in L.sheet_geometries():
                self.geometry = geometry
                specs = self._plan_views(geometry, strict)
                if specs is None:
                    continue
                key = (geometry.spec.key, geometry.columns)
                if key not in text:
                    text[key] = self._text_fits(geometry)
                if text[key] is not None:
                    return geometry, specs, text[key]  # type: ignore[return-value]
        if not any(size is not None for size in text.values()):
            raise LayoutError("the notes and assembly steps do not fit the sheet" if text
                              else "the assembly does not fit any supported sheet at any supported scale")
        raise LayoutError("the assembly does not fit any supported sheet at any supported scale")

    def build(self) -> Canvas:
        geometry, specs, size = self._choose_layout()
        self.geometry = geometry
        self.text_size = size
        self.cv = Canvas(geometry.spec.width, geometry.spec.height)
        self._watermark()
        self._frame()
        self._banner()
        self._realize_views(specs)
        for key in ("PLAN", "FRONT", "SIDE", "PICTORIAL"):
            self._draw_view(key)
        self._balloon_all_parts()
        tb = self._title_block_rect()
        legend_top = self._legend(tb[1] - L.BLOCK_GAP, size)
        self.flow = _Flow(geometry.text_columns(), legend_top - L.BLOCK_GAP)
        self._right_column(size)
        self._title_block(tb)
        return self.cv


class LayoutError(ValueError):
    """The sheet cannot be laid out (``LAYOUT_DOES_NOT_FIT``)."""


TEXT_SMALL_FACTOR = 0.9
#: One literal per printed zone letter (the output allow-list reads literals): ISO 5457 rows, I and O left out.
ZONE_LETTERS = ("A", "B", "C", "D", "E", "F", "G", "H", "J", "K", "L", "M", "N", "P", "Q", "R")
ORDINATE_DATUM_TEXT = "0"
ROTATE_UP = -90.0
ROTATE_WATERMARK = -30.0
ANCHOR_GRID_MAX = 60
TEXT_STEP = 0.1
DOF_INDENT = 11.0
NOTE_INDENT = 4.0
NOTE_GAP = 0.6
CHAIN_GAP = 0.8
STACK_COLUMN_COST = 10.0  # a column of stacked balloons is taken only when a row finds no better place
TB_ROW_TITLE = 12.0
TB_ROW_ORDER = 11.0  # the Order Number and the customer (or the release)
TB_ROW_CHECK = 9.0  # the one check statement
TB_ORDER_SPLIT = 0.4
TB_ORDER_TEXT = 3.4
LABEL_STACK = 3  # beam label positions: this many distances from the beam
LABEL_PAD = 0.4  # paper mm kept free around a label
TB_VALUE_DROP = 0.3
TB_SUB_RAISE = 1.2
LABEL_ALONG = (0.12, 0.3, 0.5, 0.7, 0.88)  # ... at these fractions along each segment
TB_LOGO_W = 26.0
TB_LOGO_TEXT = 5.0
TB_BOTTOM_COLUMNS = L.Columns((0.18, 0.22, 0.2, 0.4))
RIGHT_COLUMN_SIZES = (2.2, 2.1, 2.0, 1.9, 1.8, 1.7, 1.6)
