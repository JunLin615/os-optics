"""Neutral part geometry of a local assembly plan's preview package: primitives -> triangle mesh -> one GLB per part.

The same tessellation and GLB layout an Assembly Preview Package of OpenSci always used (the package verifier's
allow-list, ``GLB_GENERATOR``), so a package generated on this device from a plan and a delivered one read alike.

Pure standard library and deterministic across platforms:

* the only circle data is ``_OCTANT_COS``, eight literal cosines, expanded to a 32-segment unit circle by exact
  symmetry, so no libm call can differ between runtimes by one ulp;
* every other value is produced by +, -, * and / on the input dimensions;
* the GLB JSON carries only the part_ref, the fixed generator text, finish names and numbers.

The mesh stays in the part's own frame (``PART_LOCAL``), in metres, +Z up. It carries no node transform: the package
scene places it with the part's world pose. A primitive is any object with the visual-primitive fields (``kind``,
``finish``, ``min_mm``/``max_mm`` of a BOX, ``base_mm``/``axis``/``length_mm``/``radius_mm``/``inner_radius_mm`` of a
CYLINDER): the public mechanical catalog's ``VisualPrimitiveV1`` or ``Primitive`` below.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from typing import Any

from .._vendor.shared_contracts.assembly_preview import GLB_GENERATOR, AppearanceV1, FinishV1

# cos(k * pi / 16), k = 0..8 (exact literals; k = 8 is exactly 0).
_OCTANT_COS = (
    1.0,
    0.9807852804032304,
    0.9238795325112867,
    0.8314696123025452,
    0.7071067811865476,
    0.5555702330196023,
    0.38268343236508984,
    0.19509032201612833,
    0.0,
)
#: Segments of a full circle, and of a board hole marker's coarser circle.
SEGMENTS = 32
MARKER_SEGMENTS = 8
#: Points of a circle quadrant (``SEGMENTS / 4``).
QUADRANT_POINTS = 8
MM_PER_M = 1000.0
DARK_RGB = (0.05, 0.05, 0.06)
GLASS_ALPHA = 0.35
FINISH_ORDER = (FinishV1.BODY, FinishV1.DARK, FinishV1.GLASS, FinishV1.METAL)
_METALLIC = {FinishV1.BODY: 0.2, FinishV1.DARK: 0.0, FinishV1.GLASS: 0.0, FinishV1.METAL: 0.9}
_ROUGHNESS = {FinishV1.BODY: 0.55, FinishV1.DARK: 0.9, FinishV1.GLASS: 0.05, FinishV1.METAL: 0.3}
#: Decimals of a material's colour factor.
COLOUR_DECIMALS = 6
#: A board hole marker: a thin dark disc sunk this far below the board top and this tall (mm).
HOLE_MARKER_DEPTH_MM = 0.6
HOLE_MARKER_HEIGHT_MM = 0.65
_FLOAT = "<f"
_UINT = "<I"
_ARRAY_BUFFER = 34962
_ELEMENT_ARRAY_BUFFER = 34963
_FLOAT_COMPONENT = 5126
_UINT_COMPONENT = 5125
_TRIANGLES = 4
_GLB_MAGIC = b"glTF"
_GLB_VERSION = 2
_JSON_CHUNK = 0x4E4F534A
_BIN_CHUNK = 0x004E4942
#: GLB container header and chunk header sizes (bytes) and the chunk alignment.
GLB_HEADER_BYTES = 12
GLB_CHUNK_HEADER_BYTES = 8
GLB_ALIGNMENT = 4


@dataclass(frozen=True)
class Primitive:
    """A visual primitive built here (board hole markers, a reserved space): the visual-primitive fields."""

    kind: str
    finish: str
    min_mm: tuple[float, float, float] | None = None
    max_mm: tuple[float, float, float] | None = None
    base_mm: tuple[float, float, float] | None = None
    axis: str | None = None
    length_mm: float | None = None
    radius_mm: float | None = None
    inner_radius_mm: float = 0.0


def _unit_circle() -> tuple[tuple[float, float], ...]:
    """32 (cos, sin) pairs, counter-clockwise from +u, built from the octant table by exact symmetry."""

    points = []
    for k in range(SEGMENTS):
        quadrant, r = divmod(k, QUADRANT_POINTS)
        c, s = _OCTANT_COS[r], _OCTANT_COS[QUADRANT_POINTS - r]
        points.append(((c, s), (-s, c), (-c, -s), (s, -c))[quadrant])
    return tuple(points)


UNIT_CIRCLE = _unit_circle()
# (u, v, a) axis indices, right-handed (u x v = a), for a cylinder along x / y / z.
_FRAMES = {"x": (1, 2, 0), "y": (2, 0, 1), "z": (0, 1, 2)}


def _point(frame: tuple[int, int, int], u: float, v: float, a: float) -> tuple[float, float, float]:
    out = [0.0, 0.0, 0.0]
    out[frame[0]], out[frame[1]], out[frame[2]] = u, v, a
    return (out[0], out[1], out[2])


@dataclass
class _Group:
    positions: list[tuple[float, float, float]] = field(default_factory=list)
    normals: list[tuple[float, float, float]] = field(default_factory=list)
    indices: list[int] = field(default_factory=list)

    def quad(self, corners: list[tuple[float, float, float]],
             normal: tuple[float, float, float] | list[tuple[float, float, float]]) -> None:
        """Two triangles over four corners; ``normal`` is one face normal or four vertex normals."""

        base = len(self.positions)
        self.positions.extend(corners)
        self.normals.extend(normal if isinstance(normal, list) else [normal] * len(corners))
        self.indices.extend((base, base + 1, base + 2, base, base + 2, base + 3))

    def fan(self, centre: tuple[float, float, float], rim: list[tuple[float, float, float]],
            normal: tuple[float, float, float], reverse: bool) -> None:
        base = len(self.positions)
        self.positions.append(centre)
        self.positions.extend(rim)
        self.normals.extend([normal] * (len(rim) + 1))
        n = len(rim)
        for k in range(n):
            a, b = base + 1 + k, base + 1 + (k + 1) % n
            self.indices.extend((base, b, a) if reverse else (base, a, b))


@dataclass(frozen=True)
class PartMesh:
    groups: dict[FinishV1, _Group]
    bounds_min_mm: tuple[float, float, float]
    bounds_max_mm: tuple[float, float, float]

    @property
    def triangle_count(self) -> int:
        return sum(len(g.indices) // 3 for g in self.groups.values())


def _box(group: _Group, lo: tuple[float, float, float], hi: tuple[float, float, float]) -> None:
    x0, y0, z0 = lo
    x1, y1, z1 = hi
    group.quad([(x0, y0, z0), (x0, y1, z0), (x1, y1, z0), (x1, y0, z0)], (0.0, 0.0, -1.0))
    group.quad([(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)], (0.0, 0.0, 1.0))
    group.quad([(x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1)], (0.0, -1.0, 0.0))
    group.quad([(x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (x1, y1, z0)], (0.0, 1.0, 0.0))
    group.quad([(x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (x0, y1, z0)], (-1.0, 0.0, 0.0))
    group.quad([(x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1)], (1.0, 0.0, 0.0))


def _cylinder(group: _Group, prim: Any, segments: int) -> None:
    frame = _FRAMES[prim.axis]
    base = prim.base_mm
    bu, bv, ba = base[frame[0]], base[frame[1]], base[frame[2]]
    a0, a1 = ba, ba + prim.length_mm
    step = SEGMENTS // segments
    circle = UNIT_CIRCLE[::step]
    r_out, r_in = prim.radius_mm, prim.inner_radius_mm
    axis_neg, axis_pos = _point(frame, 0.0, 0.0, -1.0), _point(frame, 0.0, 0.0, 1.0)
    for k in range(segments):
        (c0, s0), (c1, s1) = circle[k], circle[(k + 1) % segments]
        outer = [_point(frame, bu + r_out * c0, bv + r_out * s0, a0), _point(frame, bu + r_out * c1, bv + r_out * s1, a0),
                 _point(frame, bu + r_out * c1, bv + r_out * s1, a1), _point(frame, bu + r_out * c0, bv + r_out * s0, a1)]
        n0, n1 = _point(frame, c0, s0, 0.0), _point(frame, c1, s1, 0.0)
        group.quad(outer, [n0, n1, n1, n0])
        if r_in > 0.0:
            inner = [_point(frame, bu + r_in * c0, bv + r_in * s0, a0), _point(frame, bu + r_in * c0, bv + r_in * s0, a1),
                     _point(frame, bu + r_in * c1, bv + r_in * s1, a1), _point(frame, bu + r_in * c1, bv + r_in * s1, a0)]
            m0, m1 = _point(frame, -c0, -s0, 0.0), _point(frame, -c1, -s1, 0.0)
            group.quad(inner, [m0, m0, m1, m1])
            for a, normal, flip in ((a0, axis_neg, True), (a1, axis_pos, False)):
                ring = [_point(frame, bu + r_out * c0, bv + r_out * s0, a), _point(frame, bu + r_out * c1, bv + r_out * s1, a),
                        _point(frame, bu + r_in * c1, bv + r_in * s1, a), _point(frame, bu + r_in * c0, bv + r_in * s0, a)]
                group.quad(ring[::-1] if flip else ring, normal)
    if r_in == 0.0:
        for a, normal, reverse in ((a0, axis_neg, True), (a1, axis_pos, False)):
            rim = [_point(frame, bu + r_out * c, bv + r_out * s, a) for c, s in circle]
            group.fan(_point(frame, bu, bv, a), rim, normal, reverse)


def board_hole_markers(origin_xy_mm: tuple[float, float], pitch_mm: tuple[float, float], count: tuple[int, int],
                       top_z_local_mm: float, radius_mm: float) -> tuple[Primitive, ...]:
    """Thin dark discs flush with the board top, one per grid hole, in the board's own frame."""

    return tuple(
        Primitive(kind="CYLINDER", finish=FinishV1.DARK.value, axis="z", length_mm=HOLE_MARKER_HEIGHT_MM, radius_mm=radius_mm,
                  base_mm=(origin_xy_mm[0] + i * pitch_mm[0], origin_xy_mm[1] + j * pitch_mm[1],
                           top_z_local_mm - HOLE_MARKER_DEPTH_MM))
        for i in range(count[0]) for j in range(count[1])
    )


def tessellate(primitives: tuple[Any, ...], *, marker_primitives: int = 0) -> PartMesh:
    """Mesh of a part. The last ``marker_primitives`` primitives use the coarser marker circle."""

    if not primitives:
        raise ValueError("a part has at least one visual primitive")
    groups: dict[FinishV1, _Group] = {}
    first_marker = len(primitives) - marker_primitives
    for index, prim in enumerate(primitives):
        group = groups.setdefault(FinishV1(prim.finish), _Group())
        if prim.kind == "BOX":
            _box(group, tuple(prim.min_mm), tuple(prim.max_mm))
        else:
            _cylinder(group, prim, MARKER_SEGMENTS if index >= first_marker else SEGMENTS)
    points = [p for g in groups.values() for p in g.positions]
    lo = tuple(min(p[i] for p in points) for i in range(3))
    hi = tuple(max(p[i] for p in points) for i in range(3))
    ordered = {finish: groups[finish] for finish in FINISH_ORDER if finish in groups}
    return PartMesh(groups=ordered, bounds_min_mm=lo, bounds_max_mm=hi)  # type: ignore[arg-type]


def _f32(value: float) -> float:
    return struct.unpack(_FLOAT, struct.pack(_FLOAT, value))[0]


def _material(finish: FinishV1, appearance: AppearanceV1) -> dict[str, object]:
    rgb = DARK_RGB if finish is FinishV1.DARK else appearance.base_color_rgb
    alpha = GLASS_ALPHA if finish is FinishV1.GLASS else 1.0
    return {
        "name": finish.value.lower(),
        "pbrMetallicRoughness": {
            "baseColorFactor": [round(c, COLOUR_DECIMALS) for c in rgb] + [alpha],
            "metallicFactor": _METALLIC[finish],
            "roughnessFactor": _ROUGHNESS[finish],
        },
        "alphaMode": "BLEND" if finish is FinishV1.GLASS else "OPAQUE",
        "doubleSided": True,
    }


def part_glb_bytes(part_ref: str, mesh: PartMesh, appearance: AppearanceV1, *, generator: str = GLB_GENERATOR) -> bytes:
    """One GLB: a single node/mesh named ``part_ref``, one primitive + material per finish."""

    binary = bytearray()
    accessors: list[dict[str, object]] = []
    views: list[dict[str, object]] = []
    primitives: list[dict[str, object]] = []
    materials: list[dict[str, object]] = []

    def add(data: bytes, target: int, accessor: dict[str, object]) -> int:
        views.append({"buffer": 0, "byteOffset": len(binary), "byteLength": len(data), "target": target})
        binary.extend(data)
        accessors.append({"bufferView": len(views) - 1, **accessor})
        return len(accessors) - 1

    for finish, group in mesh.groups.items():
        metres = [tuple(_f32(c / MM_PER_M) for c in p) for p in group.positions]
        position_bytes = b"".join(struct.pack("<3f", *p) for p in metres)
        normal_bytes = b"".join(struct.pack("<3f", *n) for n in group.normals)
        index_bytes = b"".join(struct.pack(_UINT, i) for i in group.indices)
        position = add(position_bytes, _ARRAY_BUFFER, {
            "componentType": _FLOAT_COMPONENT, "count": len(metres), "type": "VEC3",
            "min": [min(p[i] for p in metres) for i in range(3)], "max": [max(p[i] for p in metres) for i in range(3)],
        })
        normal = add(normal_bytes, _ARRAY_BUFFER, {"componentType": _FLOAT_COMPONENT, "count": len(group.normals), "type": "VEC3"})
        indices = add(index_bytes, _ELEMENT_ARRAY_BUFFER, {"componentType": _UINT_COMPONENT, "count": len(group.indices),
                                                           "type": "SCALAR"})
        materials.append(_material(finish, appearance))
        primitives.append({"attributes": {"POSITION": position, "NORMAL": normal}, "indices": indices,
                           "material": len(materials) - 1, "mode": _TRIANGLES})

    document = {
        "asset": {"version": "2.0", "generator": generator},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": part_ref}],
        "meshes": [{"name": part_ref, "primitives": primitives}],
        "materials": materials,
        "accessors": accessors,
        "bufferViews": views,
        "buffers": [{"byteLength": len(binary)}],
    }
    json_chunk = json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    json_chunk += b" " * (-len(json_chunk) % GLB_ALIGNMENT)
    bin_chunk = bytes(binary) + b"\x00" * (-len(binary) % GLB_ALIGNMENT)
    total = GLB_HEADER_BYTES + GLB_CHUNK_HEADER_BYTES + len(json_chunk) + GLB_CHUNK_HEADER_BYTES + len(bin_chunk)
    return (struct.pack("<4sII", _GLB_MAGIC, _GLB_VERSION, total)
            + struct.pack("<II", len(json_chunk), _JSON_CHUNK) + json_chunk
            + struct.pack("<II", len(bin_chunk), _BIN_CHUNK) + bin_chunk)
