"""Optics the Project declares by their physics (``ProjectDocumentV2.optical_models``), in an assembly plan.

A component that is not an OpenSci catalog product and uses a declared optical model (``custom_part`` +
``optical_model_id``; a COMPOUND unit is one such component) is a customer-supplied ``NOT_IN_CATALOG`` component like any
other user-defined component (``request.classify``): OpenSci does not supply it. What the declared model adds are the
engineering facts the assembly kernel uses for a customer-supplied catalog product, read from the customer's own
Project:

* **reserved envelope** (``ReservedEnvelope``, basis ``PROJECT_DECLARED_GEOMETRY``): the cylinder about the component's
  local +Z that holds its declared geometry -- the model's ``design_geometry`` when the Project states one, a COMPOUND
  unit's ``envelope``, else the bounding geometry of the model itself (``derived_geometry``). Bounding rule (the same as
  for a catalog product, ``supplied._envelope``): a cylinder is itself; a box or a convex polyhedron is held by the
  cylinder through its corner farthest from local Z, over its z range. A model's own geometry: a SURFACE_SEQUENCE is its
  outline (``edge_radius_mm`` round, ``edge_half_widths_mm`` rectangular: the largest of its surfaces' mechanical
  edges) over the z range its surfaces span over that
  whole outline (``SurfaceSequenceModelV1.outline_z_range_mm``, rounded outward: the contract's one derivation, the same
  the Engine's derived design body takes); a SURFACE_SET is the box symmetric
  about local Z holding its body's corners (a round body: its rim) and its surfaces' clear-aperture sample points; a
  FACET_SOLID is the box symmetric about local Z holding its corners (the Engine's derived design geometry is the box
  around the corners themselves, which may be off the axis; held here like a stated box of that extent: the reserved
  cylinder about local Z contains it). An
  element of no thickness (an ideal or tabulated thin lens, an aperture stop, a polarization element, a neutral-density
  attenuator without a declared ``design_geometry``) has no body to reserve: only its position is kept, as for any user-defined component.
* **how the nominal beam visits it** (``beam_category`` / ``ports`` / ``size_mm``), derived only where the declared model
  states it exactly:

  - a frame with ``reference_axis = PORTS`` (or a COMPOUND unit with ``ports``): the element is placed by its ports, like
    a catalog cube splitter, prism or off-axis mirror (``PORTS``);
  - a ``FACE_NORMAL`` frame: a mirror (``REFLECTED_ABOUT_+Z``) or a plate splitter (``SPLIT_ABOUT_+Z``), like the catalog's
    face-normal forms;
  - no frame, a SURFACE_SEQUENCE whose only reflecting or splitting surface is its last one, at z = 0 (the derived
    frame of a reflector placed by its coated face): a mirror / a plate splitter;
  - a model that traces no reflection and whose element axis is local Z -- a SURFACE_SEQUENCE (every surface normal to
    local Z on the axis: the axial beam passes undeviated), an ideal or tabulated thin lens, an aperture stop, a
    polarization element, a neutral-density attenuator, a NONLINEAR_CRYSTAL (its slab) -- is transmissive along local +Z (``LENS`` with a curved surface
    or a lens, else a flat plate, ``WINDOW`` / ``WAVEPLATE`` / ``POLARIZER`` / ``FILTER``); a COMPOUND unit without ports is transmissive when every member is, on the unit's Z
    axis and with its own axis along it.

  Anything else -- a reflecting model in a transmissive frame, a reflector without a frame whose coated face is not its
  last surface at z = 0, a SURFACE_SET or a FACET_SOLID (its faces may refract or fold the beam) or a DIFFRACTION_GRATING
  (each of its orders leaves in its own direction) without a ports frame, a COMPOUND unit of folded or off-axis members
  without ports -- does not say where the beam leaves it (``beam_path_undefined``): the nominal beam paths end where they
  enter such an element (``end_paths_at_undeclared_exits``), never routed straight through a declared reflector.

Only the contract fields above are read, never the run's derivation of an omitted frame: where the declared frame is
absent and the exit is not fixed by the rules above, the design must state the frame or ports. Everything here is the
customer's own Project data, and no identifier of the model library is echoed to a plan document.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any

from .._vendor.shared_contracts.common.sag_shapes import sag_form, sag_slopes
from .._vendor.shared_contracts.design_catalog import TracedBranches
from .._vendor.shared_contracts.optical_design import (
    CompoundModelV1,
    ProjectDocumentV2,
    ProjectOpticalModelV1,
)
from .kernel import BeamPathV2, ObservationRequest, PortRequest, ReservedEnvelope
from .kernel.beam import PORTS_CATEGORY, STEP_TRANSMIT
from .kernel.geometry import rotate

# ---- named numeric constants of this module (tolerances, digits and work caps)
#: A direction component or a distance below this is zero (the ray is parallel / at the origin).
_PARALLEL_EPS = 1e-15
#: Slack added to the reach of a declared geometry (mm).
_REACH_SLACK_MM = 1e-06


#: The basis of a reserved envelope read from a Project-declared optical model.
PROJECT_DECLARED_GEOMETRY = "PROJECT_DECLARED_GEOMETRY"
#: ``details.reason`` of ``DECLARED_OPTIC_BEAM_PATH_UNDEFINED``: what the declared model must state.
#: A sag shape's slope at its vertex above this (rad, tan) deviates the axial beam: rounding of two first-order terms that cancel stays below it.
_VERTEX_SLOPE_TOLERANCE = 1e-12
FRAME_REQUIRED = "FRAME_REQUIRED"
PORTS_REQUIRED = "PORTS_REQUIRED"
#: Kernel optic categories a declared model is visited as.
LENS, WINDOW, MIRROR, SPLITTER, FILTER = "LENS", "WINDOW", "MIRROR", "BEAMSPLITTER_PLATE", "FILTER"
TRANSMISSIVE_CATEGORIES = frozenset({LENS, WINDOW, FILTER, "WAVEPLATE", "POLARIZER"})
#: The kernel category of each declared model kind of one plane, which the axial beam passes undeviated along local +Z
#: (a polarization element: by its element, ``_POLARIZATION_CATEGORY``).
_PLANE_CATEGORY = {"IDEAL_THIN_LENS": LENS, "TABULATED_THIN_LENS": LENS, "APERTURE_STOP": WINDOW, "NEUTRAL_DENSITY": FILTER, "FARADAY_ROTATOR": WINDOW}
_POLARIZATION_CATEGORY = {"RETARDER": "WAVEPLATE", "LINEAR_POLARIZER": "POLARIZER"}
#: A member of a COMPOUND unit is on the unit's axis when its origin lies this close to it (mm) and its axis is this
#: close to parallel (``|cos|``).
_AXIS_TOLERANCE_MM = 1e-9
_AXIS_COSINE = 1.0 - 1e-12


@dataclass(frozen=True)
class DeclaredGeometry:
    """An element's outer geometry in its own frame, as the layout draws it and the kernel reserves it: round
    (``diameter_mm``) or rectangular symmetric about local Z (``width_mm`` across X, ``height_mm`` across Y), from
    ``z_min_mm`` to ``z_max_mm``; ``reach_mm`` is the radius of the cylinder about local Z that holds it."""

    shape: str
    reach_mm: float
    z_min_mm: float
    z_max_mm: float
    diameter_mm: float | None = None
    width_mm: float | None = None
    height_mm: float | None = None
    #: the shorter full width across local X / Y (the size the kernel's beam visit uses)
    across_mm: float = 0.0

    @property
    def has_volume(self) -> bool:
        return self.reach_mm > 0 and self.z_min_mm < self.z_max_mm

    def envelope(self) -> ReservedEnvelope | None:
        if not self.has_volume:
            return None
        return ReservedEnvelope(diameter_mm=2.0 * self.reach_mm, z_min_mm=self.z_min_mm, z_max_mm=self.z_max_mm)

    def outline(self) -> dict[str, Any] | None:
        """The Optical Layout outline (``OpticalLayoutOutlineV1``) at a catalog product's step (0.001 mm), rounded outward
        (sizes and ``z_max_mm`` up, ``z_min_mm`` down) so the drawn outline always holds the element."""

        if not self.has_volume:
            return None
        body = ({"shape": "ROUND", "diameter_mm": _up(self.diameter_mm)} if self.shape == "ROUND"
                else {"shape": "RECTANGULAR", "width_mm": _up(self.width_mm), "height_mm": _up(self.height_mm)})
        return {**body, "z_min_mm": _down(self.z_min_mm), "z_max_mm": _up(self.z_max_mm)}


@dataclass(frozen=True)
class DeclaredOptic:
    """The public engineering facts of one component that uses a declared optical model."""

    instance_id: str
    optical_model_id: str
    geometry: DeclaredGeometry | None
    beam_category: str | None = None
    size_mm: float | None = None
    ports: tuple[PortRequest, ...] = ()
    #: why the declared model does not say where the beam leaves it (``FRAME_REQUIRED`` / ``PORTS_REQUIRED``); None when it does
    beam_path_undefined: str | None = None
    #: the model field to declare (``details.fields`` of the refusal)
    missing_field: str | None = None

    @property
    def envelope(self) -> ReservedEnvelope | None:
        return None if self.geometry is None else self.geometry.envelope()


def _r(value: float | None, digits: int = 3) -> float:
    out = round(float(value or 0.0), digits)
    return 0.0 if out == 0 else out


#: The Optical Layout outline step (mm), as for a catalog product.
_OUTLINE_STEP = Decimal("0.001")


def _outward(value: float | None, rounding: str) -> float:
    out = float(Decimal(repr(float(value or 0.0))).quantize(_OUTLINE_STEP, rounding=rounding))
    return 0.0 if out == 0 else out


def _up(value: float | None) -> float:
    return _outward(value, ROUND_CEILING)


def _down(value: float | None) -> float:
    return _outward(value, ROUND_FLOOR)


def _f(value: Any) -> float:
    return float(Decimal(str(value)))


# --------------------------------------------------------------------------------------------- geometry


def geometry_of(design_geometry: Any) -> DeclaredGeometry:
    """A contract ``DesignGeometryV1`` (cylinder, box, convex polyhedron) as an element outline; the box and the
    polyhedron are held by the rectangle symmetric about local Z through their farthest extent (``optics_only.outline_of``)
    and by the cylinder through their corner farthest from local Z (``supplied._envelope``)."""

    if design_geometry.kind == "CYLINDER":
        diameter = _f(design_geometry.diameter_mm)
        return DeclaredGeometry("ROUND", diameter / 2.0, _f(design_geometry.z_min_mm), _f(design_geometry.z_max_mm),
                                diameter_mm=diameter, across_mm=diameter)
    if design_geometry.kind == "BOX":
        cx, cy, cz = (_f(v) for v in design_geometry.center_mm)
        hx, hy, hz = (_f(v) for v in design_geometry.half_extents_mm)
        corners = [(cx + sx * hx, cy + sy * hy, cz + sz * hz) for sx in (-1.0, 1.0) for sy in (-1.0, 1.0) for sz in (-1.0, 1.0)]
        across = 2.0 * min(hx, hy)
    else:
        corners = [tuple(_f(c) for c in vertex) for vertex in design_geometry.vertices_mm]
        across = min(max(p[0] for p in corners) - min(p[0] for p in corners), max(p[1] for p in corners) - min(p[1] for p in corners))
    return _box_of(corners, across)


def _box_of(points: list[tuple[float, float, float]], across: float | None = None) -> DeclaredGeometry:
    width = 2.0 * max(abs(p[0]) for p in points)
    height = 2.0 * max(abs(p[1]) for p in points)
    reach = max(math.hypot(p[0], p[1]) for p in points)
    if across is None:
        across = min(max(p[0] for p in points) - min(p[0] for p in points), max(p[1] for p in points) - min(p[1] for p in points))
    return DeclaredGeometry("RECTANGULAR", reach, min(p[2] for p in points), max(p[2] for p in points),
                            width_mm=width, height_mm=height, across_mm=across)


def _sags(surface: Any) -> list[tuple[Decimal, Decimal, Decimal]]:
    """``(x, y, sag)`` at the clear aperture's deterministic sample points (centre, rim, interior) of one surface."""

    out = []
    for x, y in surface.aperture_points():
        try:
            out.append((x, y, surface.sag_at(x, y)))
        except ValueError:  # pragma: no cover - the contract keeps every clear aperture inside its profile
            continue
    return out


def _sequence_geometry(model: Any) -> DeclaredGeometry:
    # the contract's one derivation of the glass's axial extent: over the whole outline, rounded outward (the Engine's
    # derived design body takes the same)
    low, high = model.outline_z_range_mm()
    z_min, z_max = _f(low), _f(high)
    if model.edge_radius_mm is not None:
        diameter = 2.0 * _f(model.edge_radius_mm)
        return DeclaredGeometry("ROUND", diameter / 2.0, z_min, z_max, diameter_mm=diameter, across_mm=diameter)
    hx, hy = (_f(v) for v in model.edge_half_widths_mm)
    return DeclaredGeometry("RECTANGULAR", math.hypot(hx, hy), z_min, z_max, width_mm=2.0 * hx, height_mm=2.0 * hy,
                            across_mm=2.0 * min(hx, hy))


def _surface_set_geometry(model: Any) -> DeclaredGeometry:
    points: list[tuple[float, float, float]] = []
    body = model.body
    if body is not None:
        if body.is_round:
            radius = _f(body.side_radius_mm)
            for face in body.faces:
                nx, ny, nz = (_f(v) for v in face.outward_normal)
                offset = sum(n * _f(p) for n, p in zip((nx, ny, nz), face.point_mm, strict=True))
                lateral = math.hypot(nx, ny)
                # the rim of the disc of the body's side radius: along local X / Y (its extent) and along the face's
                # slope (the face plane's extreme heights over the disc)
                rim = [(radius, 0.0), (-radius, 0.0), (0.0, radius), (0.0, -radius)]
                if lateral > 0:
                    rim += [(radius * nx / lateral, radius * ny / lateral), (-radius * nx / lateral, -radius * ny / lateral)]
                points.extend((x, y, (offset - nx * x - ny * y) / nz) for x, y in rim)
        else:
            points.extend(tuple(_f(c) for c in vertex) for vertex in body.vertices_mm())
    for surface in model.surfaces:
        origin = [_f(v) for v in surface.origin_mm]
        normal = [_f(v) for v in surface.normal]
        u = [_f(v) for v in surface.u_axis]
        v = [normal[1] * u[2] - normal[2] * u[1], normal[2] * u[0] - normal[0] * u[2], normal[0] * u[1] - normal[1] * u[0]]
        for a, b, sag in _sags(surface):
            fa, fb, fs = _f(a), _f(b), _f(sag)
            points.append(tuple(origin[k] + fa * u[k] + fb * v[k] + fs * normal[k] for k in range(3)))  # type: ignore[misc]
    return _box_of(points)


def _slab(physics: Any) -> Any:
    """A NONLINEAR_CRYSTAL as the crystal slab it is (``as_surface_sequence``: two plane faces along local Z); any other
    model as it is."""

    return physics.as_surface_sequence() if physics.model_kind == "NONLINEAR_CRYSTAL" else physics


def derived_geometry(model: ProjectOpticalModelV1) -> DeclaredGeometry | None:
    """The bounding geometry of a declared model that states no ``design_geometry`` (module docstring); None for an
    element of no thickness of its own (an ideal thin lens, an aperture stop, a polarization element)."""

    physics = _slab(model.model)
    if physics.model_kind == "SURFACE_SEQUENCE":
        return _sequence_geometry(physics)
    if physics.model_kind == "SURFACE_SET":
        return _surface_set_geometry(physics)
    if physics.model_kind == "FACET_SOLID":  # (its corners, held about local Z as a stated box around them is: the reserved
        # cylinder and outline contain the Engine's derived box, which may lie off the axis)
        return _box_of([tuple(_f(c) for c in v) for v in physics.vertices_mm])  # type: ignore[misc]
    return None


def declared_geometry(model: ProjectOpticalModelV1) -> DeclaredGeometry | None:
    """The geometry the layout draws and the kernel reserves for a component using ``model``."""

    if isinstance(model.model, CompoundModelV1):
        return geometry_of(model.model.envelope)
    if model.design_geometry is not None:
        return geometry_of(_used_design_geometry(model))
    return derived_geometry(model)


def _used_design_geometry(model: ProjectOpticalModelV1) -> Any:
    """The stated body as the Engine uses it: a stated cylinder or box that does not hold the model's surfaces is
    extended to the smallest body of its kind that does (``os_optics.local.compile.declared_design_geometry``), so the
    reserved envelope is never smaller than the glass. A body the Engine refuses is the stated one (the submission's
    re-lowering refuses the Project)."""

    from os_optics._vendor.shared_contracts.optical_design import ProjectOpticalModelV1 as EngineModel
    from os_optics.local import LocalRunError
    from os_optics.local.compile import declared_design_geometry

    try:
        used = declared_design_geometry(EngineModel.model_validate(model.model_dump(mode="json")))
    except LocalRunError:
        return model.design_geometry
    return model.design_geometry if used is None else used


def _outline_size(model: ProjectOpticalModelV1, geometry: DeclaredGeometry | None) -> float | None:
    """The outer size the kernel's beam visit uses: a round outline's diameter, else the shorter outer width across
    local X / Y (``supplied._size_mm`` for a catalog product); a body without an outline of its own: its geometry's."""

    physics = _slab(model.model)
    edge = getattr(physics, "edge_radius_mm", None)
    if edge is not None:
        return 2.0 * _f(edge)
    widths = getattr(physics, "edge_half_widths_mm", None)
    if widths is not None:
        return 2.0 * min(_f(w) for w in widths)
    return None if geometry is None or geometry.across_mm <= 0 else geometry.across_mm


# ------------------------------------------------------------------------------------------------ beam


def _ports(frame_ports: Any) -> tuple[PortRequest, ...]:
    return tuple(PortRequest(p.port_id, tuple(_f(v) for v in p.point_mm), tuple(_f(v) for v in p.direction))  # type: ignore[arg-type]
                 for p in frame_ports or ())


def _reflecting(surface: Any) -> bool:
    return surface.traced is not TracedBranches.TRANSMITTED


def _face_category(traced: TracedBranches) -> str:
    return MIRROR if traced is TracedBranches.REFLECTED else SPLITTER


def _bends_the_axial_beam(model: ProjectOpticalModelV1) -> bool:
    """A sag shape with a slope at its vertex (the Zernike terms of azimuthal order 1, such as coma): the beam along local Z leaves the surface off it, by the model's own numbers."""

    physics = model.model
    if physics.model_kind != "SURFACE_SEQUENCE":
        return False
    for surface in physics.surfaces:
        if surface.sag is not None:
            _z, slope_x, slope_y, _w, _ok = sag_slopes(sag_form(surface.sag), 0.0, 0.0)
            if abs(slope_x) > _VERTEX_SLOPE_TOLERANCE or abs(slope_y) > _VERTEX_SLOPE_TOLERANCE:
                return True
    return False


def _transmissive(model: ProjectOpticalModelV1) -> str | None:
    """The kernel category of a model the axial beam passes undeviated along local +Z, else None."""

    physics = _slab(model.model)
    if _bends_the_axial_beam(model):
        return None
    if physics.model_kind in _PLANE_CATEGORY:
        return _PLANE_CATEGORY[physics.model_kind]
    if physics.model_kind == "POLARIZATION_ELEMENT":
        return _POLARIZATION_CATEGORY[physics.element.value]
    if physics.model_kind == "SURFACE_SEQUENCE" and not any(_reflecting(s) for s in physics.surfaces):
        return WINDOW if all(s.is_plane for s in physics.surfaces) else LENS
    return None


def _single_model_beam(model: ProjectOpticalModelV1) -> tuple[str | None, tuple[PortRequest, ...], str | None]:
    """``(category, ports, undefined reason)`` of a declared model that is not a COMPOUND unit."""

    frame, physics = model.frame, _slab(model.model)
    if frame is not None and frame.has_ports:
        return PORTS_CATEGORY, _ports(frame.ports), None
    if physics.model_kind == "DIFFRACTION_GRATING":  # (which of its orders is the designed exit is the customer's to state)
        return None, (), PORTS_REQUIRED
    if _bends_the_axial_beam(model):  # (where the beam leaves is the customer's to state: the kernel would lay hardware along local Z)
        return None, (), PORTS_REQUIRED
    if frame is not None and frame.is_reflector:
        return (MIRROR if frame.nominal_propagation == "REFLECTED_ABOUT_+Z" else SPLITTER), (), None
    category = _transmissive(model)
    if category is not None:
        return category, (), None
    if frame is None and physics.model_kind == "SURFACE_SEQUENCE":
        # the derived frame of a reflector placed by its coated face: its last surface, at z = 0, is the only one that
        # reflects or splits
        *front, last = physics.surfaces
        if _reflecting(last) and Decimal(last.vertex_z_mm) == 0 and not any(_reflecting(s) for s in front):
            return _face_category(last.traced), (), None
        return None, (), FRAME_REQUIRED
    if physics.model_kind in ("SURFACE_SET", "FACET_SOLID"):
        return None, (), PORTS_REQUIRED
    return None, (), FRAME_REQUIRED


def _on_unit_axis(member: Any) -> bool:
    pose = member.pose
    x, y, _ = (_f(v) for v in pose.position_mm)
    axis = rotate(tuple(_f(q) for q in pose.quaternion_wxyz), (0.0, 0.0, 1.0))  # type: ignore[arg-type]
    return abs(x) <= _AXIS_TOLERANCE_MM and abs(y) <= _AXIS_TOLERANCE_MM and abs(axis[2]) >= _AXIS_COSINE


def _compound_beam(project: ProjectDocumentV2, unit: CompoundModelV1) -> tuple[str | None, tuple[PortRequest, ...], str | None]:
    if unit.ports is not None:
        return PORTS_CATEGORY, _ports(unit.ports), None
    categories = []
    for member in unit.members:
        model = project.optical_model(member.model_id)
        category = None if model is None or model.frame is not None and model.frame.reference_axis is not None \
            else _transmissive(model)
        if category is None or not _on_unit_axis(member):
            return None, (), PORTS_REQUIRED
        categories.append(category)
    return (LENS if LENS in categories else WINDOW), (), None


def declared_optic(project: ProjectDocumentV2, component: Any) -> DeclaredOptic | None:
    """The public engineering facts of a component that uses a declared optical model (module docstring); None for any
    other component."""

    model_id = getattr(component, "optical_model_id", None)
    if model_id is None:
        return None
    model = project.optical_model(model_id)
    if model is None:  # pragma: no cover - the Project contract resolves every optical_model_id
        return None
    geometry = declared_geometry(model)
    if isinstance(model.model, CompoundModelV1):
        category, ports, undefined = _compound_beam(project, model.model)
        missing = f"optical_models[{model_id}].model.ports"
    else:
        category, ports, undefined = _single_model_beam(model)
        missing = f"optical_models[{model_id}].frame"
    size = _outline_size(model, geometry)
    if category is not None and size is None:  # pragma: no cover - every declared model has an outline or a body
        category, undefined = None, FRAME_REQUIRED
    return DeclaredOptic(
        instance_id=component.instance_id, optical_model_id=model_id, geometry=geometry,
        beam_category=category, size_mm=size if category is not None else None, ports=ports,
        beam_path_undefined=undefined, missing_field=missing if undefined is not None else None,
    )


# ------------------------------------------------------------------------------ optics-only beam paths


def _entry(origin: tuple[float, ...], end: tuple[float, ...], geometry: DeclaredGeometry) -> float | None:
    """The smallest segment parameter ``t`` in [0, 1] at which the segment ``origin -> end`` (element frame) is inside
    the cylinder about local Z of radius ``reach_mm`` between ``z_min_mm`` and ``z_max_mm``; None when it never is."""

    lo, hi = 0.0, 1.0
    d = [e - o for e, o in zip(end, origin, strict=True)]
    if abs(d[2]) < _PARALLEL_EPS:
        if not geometry.z_min_mm <= origin[2] <= geometry.z_max_mm:
            return None
    else:
        t1, t2 = sorted(((geometry.z_min_mm - origin[2]) / d[2], (geometry.z_max_mm - origin[2]) / d[2]))
        lo, hi = max(lo, t1), min(hi, t2)
    a = d[0] * d[0] + d[1] * d[1]
    b = 2.0 * (origin[0] * d[0] + origin[1] * d[1])
    c = origin[0] * origin[0] + origin[1] * origin[1] - geometry.reach_mm * geometry.reach_mm
    if a < _PARALLEL_EPS:
        if c > 0:
            return None
    else:
        disc = b * b - 4.0 * a * c
        if disc < 0:
            return None
        root = math.sqrt(disc)
        lo, hi = max(lo, (-b - root) / (2.0 * a)), min(hi, (-b + root) / (2.0 * a))
    return lo if lo <= hi else None


def _to_local(point: tuple[float, ...], position: tuple[float, ...], quaternion: tuple[float, ...]) -> tuple[float, float, float]:
    w, x, y, z = quaternion
    offset = tuple(p - q for p, q in zip(point, position, strict=True))
    return rotate((w, -x, -y, -z), offset)  # type: ignore[arg-type]


#: The observation id prefix of the probes ``exit_probes`` adds to a trace (no Project id starts with it).
PROBE_PREFIX = "~exit:"


def _blocking(optics: dict[str, DeclaredOptic]) -> dict[str, DeclaredOptic]:
    return {k: o for k, o in optics.items() if o.beam_path_undefined is not None and o.geometry is not None}


def exit_probes(project: ProjectDocumentV2, optics: dict[str, DeclaredOptic]) -> tuple[ObservationRequest, ...]:
    """Virtual observation planes for the nominal trace, one per element whose declared model does not say where the
    beam leaves it: the trace passes observations wherever the beam crosses their reach (a sphere about the element's
    geometry), so every ray that may enter the element yields a path there for ``end_paths_at_undeclared_exits`` to cut
    (a ray that meets no element is otherwise not a path at all)."""

    out = []
    for component in project.components:
        optic = _blocking(optics).get(component.instance_id)
        if optic is None:
            continue
        geometry = optic.geometry
        assert geometry is not None
        pose = component.nominal_pose
        quaternion = tuple(_f(q) for q in pose.quaternion_wxyz)
        middle = rotate(quaternion, (0.0, 0.0, (geometry.z_min_mm + geometry.z_max_mm) / 2.0))  # type: ignore[arg-type]
        centre = tuple(_f(p) + m for p, m in zip(pose.position_mm, middle, strict=True))
        radius = math.hypot(geometry.reach_mm, (geometry.z_max_mm - geometry.z_min_mm) / 2.0) + _REACH_SLACK_MM
        out.append(ObservationRequest(PROBE_PREFIX + component.instance_id, centre, (1.0, 0.0, 0.0, 0.0), ((radius, 0.0),)))  # type: ignore[arg-type]
    return tuple(out)


def _without_probes(path: BeamPathV2) -> BeamPathV2 | None:
    keep = [i for i, name in enumerate(path.through) if not name.startswith(PROBE_PREFIX)]
    if len(keep) == len(path.through):
        return path
    if len(keep) < 2:
        return None
    return BeamPathV2(path_key=path.path_key, points_design=tuple(path.points_design[i] for i in keep),
                      through=tuple(path.through[i] for i in keep), wavelength_nm=path.wavelength_nm,
                      steps=tuple(path.steps[i] for i in keep) if path.steps else ())


def end_paths_at_undeclared_exits(paths: tuple[BeamPathV2, ...], project: ProjectDocumentV2,
                                  optics: dict[str, DeclaredOptic]) -> tuple[BeamPathV2, ...]:
    """Each nominal path ends where it first enters an element whose declared model does not say where the beam leaves
    it (``beam_path_undefined``; its geometry's cylinder, in its own frame): that element is the path's last point, the
    rest of the path and every branch split off after it are dropped. The ``exit_probes`` points are then removed (they
    lie on the straight segments); every other path is returned unchanged."""

    blocking = _blocking(optics)
    if not blocking:
        return paths
    poses = {c.instance_id: c.nominal_pose for c in project.components if c.instance_id in blocking}
    kept: dict[str, tuple[BeamPathV2, int | None]] = {}  # path key -> (path, index of the last kept point or None)
    for path in paths:
        cut: tuple[int, float, str, tuple[float, float, float]] | None = None
        for index, (start, end) in enumerate(zip(path.points_design, path.points_design[1:])):
            for instance_id, optic in sorted(blocking.items()):
                pose = poses[instance_id]
                position = tuple(_f(v) for v in pose.position_mm)
                quaternion = tuple(_f(q) for q in pose.quaternion_wxyz)
                t = _entry(_to_local(start, position, quaternion), _to_local(end, position, quaternion), optic.geometry)  # type: ignore[arg-type]
                if t is not None and (cut is None or t < cut[1]):
                    point = tuple(s + t * (e - s) for s, e in zip(start, end, strict=True))
                    cut = (index, t, instance_id, point)  # type: ignore[assignment]
            if cut is not None:
                break
        if cut is None:
            kept[path.path_key] = (path, None)
            continue
        index, _, instance_id, point = cut
        steps = path.steps[: index + 1] + ((STEP_TRANSMIT,) if path.steps else ())
        kept[path.path_key] = (BeamPathV2(path_key=path.path_key, points_design=path.points_design[: index + 1] + (point,),
                                          through=path.through[: index + 1] + (instance_id,), wavelength_nm=path.wavelength_nm,
                                          steps=steps), index)
    out = []
    for path in paths:
        if _cut_off(path.path_key, path, kept):
            continue
        cleaned = _without_probes(kept[path.path_key][0])
        if cleaned is not None:
            out.append(cleaned)
    return tuple(out)


def _cut_off(key: str, path: BeamPathV2, kept: dict[str, tuple[BeamPathV2, int | None]]) -> bool:
    """A branch is dropped when the path it splits from (its key minus one trailing letter) was cut before the branch's
    first element (its splitter), or was itself dropped."""

    parent_key = key[:-1]
    if not (key and key[-1].isalpha() and parent_key in kept):
        return False
    parent, index = kept[parent_key]
    if _cut_off(parent_key, parent, kept):
        return True
    if index is None or not path.through:
        return False
    return path.through[0] not in parent.through[: index + 1]


__all__ = [
    "FRAME_REQUIRED",
    "PORTS_REQUIRED",
    "PROJECT_DECLARED_GEOMETRY",
    "DeclaredGeometry",
    "DeclaredOptic",
    "declared_geometry",
    "declared_optic",
    "derived_geometry",
    "end_paths_at_undeclared_exits",
    "exit_probes",
    "geometry_of",
]
