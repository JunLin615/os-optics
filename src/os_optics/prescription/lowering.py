"""Prescription IR -> the Project's native optical models: one ``COMPOUND`` unit of physical elements.

Grouping (in surface order; the medium before the first and after the last surface is air):

* an **element** is a run of surfaces from one with air before it to the next one with air after it: a singlet (two
  surfaces), a cemented doublet or triplet (glass meets glass at the inner surfaces), or windows enclosing a vacuum
  gap; it becomes a ``SURFACE_SEQUENCE`` model (surfaces ``S1`` .. in order, ``media`` the intervening glasses and any
  vacuum gaps, vertex positions relative to the first vertex, every surface traced ``TRANSMITTED``). Vacuum between
  separate compound members cannot be represented by the native model, whose inter-member space is Project air;
* a mirror in air becomes a one-face sequence that traces the reflected branch. Its local +Z axis faces the incident
  light. An explicit circular aperture or stop on an air plane becomes an opaque plate with the prescribed opening;
* any other surface with air on both sides (a dummy or reference surface) has no optical effect and is not a member: its
  spacing simply continues to the next surface (reported);
* a stop on a refracting surface is that surface's clear aperture (reported).

Outline rule: each window or uninterrupted run of glass has its own outline: the largest of its surfaces' mechanical
semi-diameters, using the clear semi-diameter where the mechanical one is missing. A surface with a mechanical
semi-diameter is edged there. A surface without one reaches its window's outline, except where its profile ends sooner
or leaves no glass there: it is edged at its own clear semi-diameter when that makes the element a valid solid (reported
per surface in ``members[].derived_edges``). The sequence's ``edge_radius_mm`` is the largest window outline. Each
smaller surface edge is stated on its surface. Beyond its edge a surface is held flat at its rim height out to the outline
of its glass (``SurfaceSequenceModelV1.held_sag_at``). An aperture stop's plate radius is its row's mechanical semi-diameter; without one it is the
largest outline radius of the unit, reduced where a neighbouring element's surface would reach the plate's plane (a
plate never overlaps an element), and never smaller than the opening.

The unit origin is the first surface vertex. Signed spacings accumulate along the fixed Z axis. Mirrors reverse the
propagation direction. Member poses preserve the world-space sag and orient reflecting surfaces toward the incident
light. The cylinder envelope covers each member's body, rounded outward to 1e-6 mm. Its input and output ports point
along the initial and final propagation directions.

Checks (each refusal names the rows / surfaces): the native surface model's own checks (profile domain over the clear
aperture, consecutive surfaces not meeting inside their common clear aperture -- the contract's messages), every surface's
profile reaching its edge, positive glass thickness out to the outline (each surface held flat beyond its edge),
consecutive elements not overlapping out to the smaller of their facing glass outlines, no aperture-stop plate crossing an
element, every element inside the ranges the prescription declares (``EdgeLimits``). Values stay exact decimals; only member
positions are floats in the contract's pose (the nearest binary64 of the exact decimal).

Every element's edge thickness (per glass, at that glass's outline) and every surface's mechanical over clear
semi-diameter are reported. An implausible element is imported with the warning the optical-solid check gives it
(``EDGE_THICKNESS_IMPLAUSIBLE`` above 1.5 clear diameters, ``EDGE_FAR_BEYOND_CLEAR_APERTURE``): an element named, with its
ratio. An edge thickness between the review ratio (0.35 by default, ``edge_review_ratio``) and 1.5 is a fact
(``above_review_ratio`` on its row), not a warning.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import ROUND_CEILING, ROUND_FLOOR, Context, Decimal, localcontext

from pydantic import ValidationError

from .._vendor.shared_contracts.common.decimal_string import decimal_to_string
from .._vendor.shared_contracts.design_catalog import (
    AIR,
    VACUUM,
    CylinderGeometryV1,
    FramePortV1,
    PrescriptionSurfaceV1,
    SurfaceSequenceModelV1,
)
from .._vendor.shared_contracts.design_catalog.enums import FactStatus, ModelFidelity, SimulationApproximation
from .._vendor.shared_contracts.design_catalog.optical_catalog import DECIMAL_CONTEXT, MAX_SURFACES
from .._vendor.shared_contracts.optical_design.optical_model import (
    MAX_COMPOUND_MEMBERS,
    ApertureStopModelV1,
    CompoundMemberV1,
    CompoundModelV1,
    ProjectOpticalModelV1,
)
from .._vendor.shared_contracts.common.geometry import PoseV1
from ..optical_solids.bodies import body_of
from ..optical_solids.checks import (
    EDGE_MARGIN_MIN_MM, EDGE_THICKNESS_RATIO_LIMIT, EDGE_THICKNESS_REVIEW_RATIO, EDGE_TO_CLEAR_RATIO_LIMIT, element_issues,
)
from ..optical_solids.report import ERROR
from .errors import ImportWarning, PrescriptionErrorCode as E, ProblemList, SourceLocation
from .glass import ResolvedMaterials
from .ir import IrSagShape, IrSurface, Prescription

LENS_MEMBER_PREFIX = "L"
STOP_MEMBER = "STOP"
#: Radial samples (0 .. outline) of the outline, overlap and plate-clearance checks.
RADIAL_SAMPLES = 64
#: The envelope is rounded outward to this step (mm).
ENVELOPE_QUANTUM = Decimal("0.000001")
#: A derived aperture-stop plate radius is rounded down to this step (mm).
PLATE_QUANTUM = Decimal("0.001")
#: Reported edge facts (mm, and ratios) are rounded to this step.
FACT_QUANTUM = Decimal("0.0001")
#: Exact arithmetic on prescription values (sums of spacings): wide enough that no realistic input rounds.
EXACT = Context(prec=200)
BASE_APPROXIMATIONS = (SimulationApproximation.NOMINAL_PRESCRIPTION, SimulationApproximation.IDEAL_SURFACE_FIGURE,
                       SimulationApproximation.GHOST_REFLECTIONS_NOT_TRACED, SimulationApproximation.EDGE_AS_OPAQUE_RIM)
NAME_MAX = 200
AXIS = ("0", "0", "1")
#: ``derived_edges[].basis``: the surface states no mechanical semi-diameter and is edged at its clear semi-diameter.
EDGE_BASIS_CLEAR_SEMI_DIAMETER = "CLEAR_SEMI_DIAMETER"


@dataclass
class _Group:
    """A member in the making: a lens element (``surfaces`` with their media) or an aperture stop (one surface)."""

    kind: str  # "LENS" | "STOP"
    surfaces: list[IrSurface]
    z: list[Decimal]
    media: list[str] = field(default_factory=list)
    member_id: str = ""
    axis_sign: int = 1  # orientation of the member's local +Z axis in the prescription frame
    edge: Decimal = Decimal(0)
    edge_basis: str = ""
    model: object | None = None
    profiles: list[PrescriptionSurfaceV1] = field(default_factory=list)
    edge_thickness: list[dict] = field(default_factory=list)
    mechanical_to_clear: list[dict] = field(default_factory=list)
    #: (0-based) surfaces without a mechanical semi-diameter that are edged at their own clear semi-diameter
    derived_edges: set[int] = field(default_factory=set)

    @property
    def rows(self) -> list[int]:
        return [s.location.row for s in self.surfaces if s.location.row is not None]

    @property
    def labels(self) -> str:
        labels = [s.label for s in self.surfaces]
        return labels[0] if len(labels) == 1 else f"{labels[0]}-{labels[-1]}"


@dataclass(frozen=True)
class _Element:
    """An element model alone, as the optical-solid check reads one (``optical_solids.bodies.body_of``)."""

    instance_id: str
    model: object


@dataclass(frozen=True)
class LoweredUnit:
    """The native models of one imported prescription and what the import report says about them."""

    models: tuple[ProjectOpticalModelV1, ...]
    compound_id: str
    members: tuple[dict, ...]
    assumptions: tuple[str, ...]
    warnings: tuple[ImportWarning, ...]
    image_plane_z_mm: Decimal | None
    counts: dict[str, int]


def _s(value: Decimal) -> str:
    return decimal_to_string(value)


def _fact(value: Decimal) -> str:
    """A reported edge fact, rounded to ``FACT_QUANTUM``."""

    return decimal_to_string(value.quantize(FACT_QUANTUM))


#: The directions the coaxial checks sample a surface along: local X and Y for a surface whose sag depends on the radius
#: or one axis (``False``: X, ``True``: Y), and both ways along the axes and diagonals when one has a two-variable shape.
AXIS_DIRECTIONS = (False, True)
SHAPE_DIRECTIONS = ((Decimal(1), Decimal(0)), (Decimal(-1), Decimal(0)), (Decimal(0), Decimal(1)), (Decimal(0), Decimal(-1)),
                    (Decimal("0.7071067811865475"), Decimal("0.7071067811865475")), (Decimal("-0.7071067811865475"), Decimal("0.7071067811865475")),
                    (Decimal("0.7071067811865475"), Decimal("-0.7071067811865475")), (Decimal("-0.7071067811865475"), Decimal("-0.7071067811865475")))


def _point(rho: Decimal, along) -> tuple[Decimal, Decimal]:
    """The point at radius ``rho`` along a direction of ``AXIS_DIRECTIONS`` / ``SHAPE_DIRECTIONS``."""

    if along is False:
        return rho, Decimal(0)
    if along is True:
        return Decimal(0), rho
    return rho * along[0], rho * along[1]


def _directions(*groups: _Group) -> tuple:
    return SHAPE_DIRECTIONS if any(s.sag is not None for group in groups for s in group.surfaces) else AXIS_DIRECTIONS


def _sag(profile: PrescriptionSurfaceV1, rho: Decimal, along) -> Decimal | None:
    """Sag at radius ``rho`` along a direction; ``None`` outside the profile's domain."""

    try:
        return profile.sag_at(*_point(rho, along))
    except ValueError:
        return None


def _samples(limit: Decimal) -> list[Decimal]:
    with localcontext(DECIMAL_CONTEXT):
        return [limit * i / RADIAL_SAMPLES for i in range(RADIAL_SAMPLES + 1)]


def _held(group: _Group, index: int, rho: Decimal, along) -> Decimal | None:
    """Sag of the element's surface ``index`` at radius ``rho`` along a direction, held flat beyond its own edge
    (``SurfaceSequenceModelV1.held_sag_at``); ``None`` outside the profile's domain."""

    try:
        return group.axis_sign * group.model.held_sag_at(index, *_point(rho, along))  # type: ignore[union-attr]
    except ValueError:
        return None


def _sag_document(shape: IrSagShape | None) -> dict | None:
    """An IR sag shape as the native contract's document."""

    if shape is None:
        return None
    if shape.kind == "ZERNIKE":
        return {"kind": "ZERNIKE", "indexing": shape.indexing, "normalization": shape.normalization,
                "normalization_radius_mm": _s(shape.normalization_radius_mm),
                "terms": [{"index": index, "value_mm": _s(value)} for index, value in shape.terms]}
    if shape.kind == "XY_POLYNOMIAL":
        return {"kind": "XY_POLYNOMIAL", "normalization_radius_mm": _s(shape.normalization_radius_mm),
                "terms": [{"x_power": i, "y_power": j, "value_mm": _s(value)} for i, j, value in shape.terms]}
    optional = (("radius_x_mm", shape.radius_x_mm), ("radius_y_mm", shape.radius_y_mm), ("conic_x", shape.conic_x), ("conic_y", shape.conic_y)) \
        if shape.kind == "BICONIC" else (("radius_y_mm", shape.radius_y_mm), ("conic", shape.conic), ("radius_of_rotation_mm", shape.radius_of_rotation_mm))
    document: dict = {"kind": shape.kind, **{key: _s(value) for key, value in optional if value is not None}}
    if shape.y_terms:
        document["y_terms"] = [_s(a) for a in shape.y_terms]
    return document


def _profile(surface: IrSurface, surface_id: str, vertex_z: Decimal, coating_id: str | None, edge: Decimal | None) -> PrescriptionSurfaceV1:
    return PrescriptionSurfaceV1(
        surface_id=surface_id, vertex_z_mm=_s(vertex_z), radius_mm=_s(surface.radius_mm) if surface.radius_mm is not None else None,
        conic=_s(surface.conic) if surface.conic is not None else None,
        aspheric_coefficients=tuple(_s(a) for a in surface.aspheric_mm) or None, cylinder_axis=surface.cylinder_axis,
        linear_sag_coefficient=_s(surface.linear_sag) if surface.linear_sag is not None else None, sag=_sag_document(surface.sag),
        clear_radius_mm=_s(surface.clear_semi_diameter_mm), edge_radius_mm=_s(edge) if edge is not None else None,
        holes=({"center_mm": ("0", "0"), "radius_mm": _s(surface.hole_radius_mm)},) if surface.hole_radius_mm else None,
        coating_id=coating_id, traced="REFLECTED" if surface.is_mirror else "TRANSMITTED", status=FactStatus.SPECIFIED,
    )


def _contract_message(exc: ValidationError) -> str:
    return "; ".join(str(error.get("msg", "")).removeprefix("Value error, ") for error in exc.errors(include_url=False))


class _Lowering:
    def __init__(self, prescription: Prescription, resolved: ResolvedMaterials, model_id: str, problems: ProblemList,
                 edge_review_ratio: float = EDGE_THICKNESS_REVIEW_RATIO) -> None:
        self.edge_review_ratio = edge_review_ratio
        self.rx = prescription
        self.resolved = resolved
        self.model_id = model_id
        self.problems = problems
        self.assumptions: list[str] = []
        self.warnings: list[ImportWarning] = []
        self.dummies = 0

    # --- positions and grouping

    def positions(self) -> list[Decimal]:
        z = [Decimal(0)]
        for surface in self.rx.surfaces[:-1]:
            z.append(EXACT.add(z[-1], surface.thickness_mm))  # type: ignore[arg-type]
        return z

    def medium(self, surface: IrSurface) -> str:
        return AIR if surface.material is None else self.resolved.material_ids[surface.material.key]

    def groups(self, z: list[Decimal]) -> list[_Group]:
        surfaces = self.rx.surfaces
        groups: list[_Group] = []
        before = AIR
        current: _Group | None = None
        direction = 1
        previous_optical_z = None
        for surface, position in zip(surfaces, z):
            if surface.is_mirror or before not in (AIR, VACUUM) or surface.material is not None:
                # Reference planes and the open centre of a stop may have reversed vertex order.
                # Actual interfaces must be encountered in the direction set by the last reflection.
                if previous_optical_z is not None and direction * (position - previous_optical_z) < 0:
                    self.problems.add(E.SURFACE_ORDER_INVALID, f"surface {surface.label} reverses the propagation order of the optical "
                                      "interfaces. Check the signed spacings and reflection records.", surface.location)
                    return []
                previous_optical_z = position
            if surface.is_mirror:
                if before != AIR or surface.material is not None:
                    self.problems.add(E.UNSUPPORTED_FEATURE, "an immersed or back-silvered mirror needs its substrate and return path",
                                      surface.location)
                    continue
                # A native mirror reflects on its local +Z side. A half turn about Y reverses both
                # its local z and its sag so the world profile remains the one in the source.
                mirror = surface
                if direction == 1:
                    if surface.sag is not None:
                        self.problems.add(E.UNSUPPORTED_FEATURE, "a flipped mirror with a two-variable sag is not supported", surface.location)
                        continue
                    mirror = replace(surface, radius_mm=-surface.radius_mm if surface.radius_mm else None,
                                     aspheric_mm=tuple(-a for a in surface.aspheric_mm),
                                     linear_sag=-surface.linear_sag if surface.linear_sag else None)
                direction = -direction
                groups.append(_Group("MIRROR", [mirror], [position], axis_sign=direction))
                continue
            after = self.medium(surface)
            if before != after and before in (AIR, VACUUM) and after in (AIR, VACUUM):
                self.problems.add(E.UNSUPPORTED_FEATURE, f"surface {surface.label} changes a gap from {before} to {after} "
                                  "without a modelled boundary (a bare plane cannot separate air and vacuum); give a "
                                  "physical window or a modelled interface", surface.location.at("material"))
                before = after
                continue
            if before == after and before in (AIR, VACUUM):
                if surface.coating is not None:  # a coating needs a substrate; a stop's opening has none (never dropped silently)
                    what = "an aperture stop's opening" if surface.is_stop else "a coated plane without substrate"
                    self.problems.add(E.UNSUPPORTED_FEATURE, f"a coated surface with {before.lower()} on both sides ({what}) is not imported; "
                                      "leave the coating blank, or give the substrate as glass between two surfaces",
                                      surface.location.at("coating"))
                elif surface.is_stop or surface.has_clear_aperture:
                    if before == VACUUM and current is not None:
                        self.problems.add(E.UNSUPPORTED_FEATURE, f"aperture stop {surface.label} inside an evacuated gap cannot be a "
                                          "separate member: the inter-member medium is Project air; place the stop outside the "
                                          "vacuum cell", surface.location.at("stop"))
                    else:
                        groups.append(_Group("STOP", [surface], [position], axis_sign=direction))
                else:
                    self.dummies += 1
                    self.warnings.append(ImportWarning(
                        "DUMMY_SURFACE", f"surface {surface.label} has {before.lower()} on both sides and no optical effect; it is not a member "
                        "(the spacing continues to the next surface)", surface.location))
            elif before in (AIR, VACUUM):
                if before == VACUUM and current is not None:
                    # The native sequence, unlike a compound unit, declares the medium of this physical gap.
                    # Keep the enclosing windows together, including any harmless dummy vacuum planes between them.
                    current.surfaces.append(surface)
                    current.z.append(position)
                    current.media.append(VACUUM)
                else:
                    current = _Group("LENS", [surface], [position])
                    groups.append(current)
            elif current is not None:
                current.surfaces.append(surface)
                current.z.append(position)
                current.media.append(before)
            if after == AIR:
                if current is not None and direction < 0:
                    # A transmitting member stores faces and media in physical +Z order, even on a return pass.
                    current.surfaces.reverse()
                    current.z.reverse()
                    current.media.reverse()
                current = None  # another lens now starts in Project air, never across an inter-member vacuum gap
            if surface.is_stop and not (before == after and before in (AIR, VACUUM)):
                self.assumptions.append(f"The aperture stop is surface {surface.label}'s clear aperture (radius "
                                        f"{_s(surface.clear_semi_diameter_mm)} mm): a stop on a refracting surface is that surface's edge.")
            before = after
        if before == VACUUM:
            self.problems.add(E.UNSUPPORTED_FEATURE, "the image space after the last surface is VACUUM, but the native "
                              "sequence ends in Project air; close the evacuated space with a physical window",
                              surfaces[-1].location.at("material"))
        elif before != AIR:
            last = surfaces[-1]
            self.problems.add(E.UNSUPPORTED_FEATURE, "the image space must be air: the native sequence ends in Project air "
                              "(close an evacuated space with a physical window)", last.location.at("material"))
        lenses = [g for g in groups if g.kind == "LENS"]
        stops = [g for g in groups if g.kind == "STOP"]
        for number, group in enumerate(lenses, start=1):
            group.member_id = f"{LENS_MEMBER_PREFIX}{number}"
        for number, group in enumerate((g for g in groups if g.kind == "MIRROR"), start=1):
            group.member_id = f"M{number}"
        for number, group in enumerate(stops, start=1):
            group.member_id = STOP_MEMBER if len(stops) == 1 else f"{STOP_MEMBER}{number}"
        if len(groups) > MAX_COMPOUND_MEMBERS:
            self.problems.add(E.UNSUPPORTED_FEATURE, f"the prescription has {len(groups)} elements and stops; one unit holds at most "
                              f"{MAX_COMPOUND_MEMBERS} (split it into several imports)")
        if not groups and not self.problems:
            self.problems.add(E.SURFACE_ORDER_INVALID, "the prescription has no element (no glass) and no aperture stop")
        return groups

    # --- lens elements

    def element(self, group: _Group) -> None:
        where = group.surfaces[0].location
        if len(group.surfaces) > MAX_SURFACES:
            self.problems.add(E.UNSUPPORTED_FEATURE, f"element {group.member_id} (surfaces {group.labels}) has {len(group.surfaces)} "
                              f"surfaces; one element holds at most {MAX_SURFACES}", where)
            return
        edges = []
        edges_valid = True
        for surface in group.surfaces:
            mechanical = surface.mechanical_semi_diameter_mm
            if mechanical is not None and mechanical < surface.clear_semi_diameter_mm:
                self.problems.add(E.EDGE_INVALID, f"mech_semi_diameter {_s(mechanical)} mm is smaller than the clear semi-diameter "
                                  f"{_s(surface.clear_semi_diameter_mm)} mm", surface.location.at("mech_semi_diameter"))
                edges_valid = False
            edges.append(mechanical if mechanical is not None else surface.clear_semi_diameter_mm)
        if not edges_valid:
            return
        group.edge = max(edges)
        if VACUUM in group.media:
            group.edge_basis = "the largest window outline, based on each window's mechanical or clear semi-diameters"
        else:
            group.edge_basis = ("the largest mechanical semi-diameter of its surfaces" if any(
                s.mechanical_semi_diameter_mm is not None for s in group.surfaces) else "its largest clear semi-diameter (no mechanical data)")
        # A vacuum gap keeps its surrounding windows in one native sequence, but separates their mechanical outlines.
        window_ends = [index + 1 for index, medium in enumerate(group.media) if medium == VACUUM]
        default_edges = []
        first_surface = 0
        for end in (*window_ends, len(edges)):
            window_edge = max(edges[first_surface:end])
            default_edges.extend([window_edge] * (end - first_surface))
            first_surface = end
        first = group.z[0]
        refusal: tuple[str, SourceLocation] | None = None  # (the finding of the element as the table states it)
        while True:
            profiles: list[PrescriptionSurfaceV1] = []
            for index, surface in enumerate(group.surfaces):
                coating = surface.coating.coating_id if surface.coating is not None else None
                mechanical = surface.mechanical_semi_diameter_mm
                edge = mechanical if mechanical is not None else default_edges[index]
                if index in group.derived_edges:
                    edge = surface.clear_semi_diameter_mm
                own_edge = edge if edge < group.edge else None
                try:
                    profiles.append(_profile(surface, f"S{index + 1}", EXACT.subtract(group.z[index], first), coating, own_edge))
                except ValidationError as exc:
                    self.problems.add(E.SURFACE_INVALID, _contract_message(exc), surface.location)
            if self.problems:
                return
            group.profiles = profiles
            approximations = set(BASE_APPROXIMATIONS)
            if any(m in self.resolved.from_catalog for m in group.media):
                approximations.add(SimulationApproximation.CATALOG_MATERIAL_DISPERSION)
            coatings = [p.coating_id for p in profiles if p.coating_id is not None]
            if coatings:
                if any(c.phase is None for c in self.resolved.coatings if c.coating_id in coatings):  # a coating without stated phases
                    approximations.add(SimulationApproximation.COATING_PHASE_NOT_MODELLED)
                if any(c.is_typical for c in self.resolved.coatings if c.coating_id in coatings):
                    approximations.add(SimulationApproximation.TYPICAL_SPECTRAL_CURVE)
            try:
                group.model = SurfaceSequenceModelV1(
                    fidelity=ModelFidelity.SURFACE_PRESCRIPTION, surfaces=tuple(profiles), media=tuple(group.media),
                    edge_radius_mm=_s(group.edge), valid_band_nm=self.resolved.band, approximations=tuple(sorted(approximations)),
                    status=FactStatus.SPECIFIED,
                )
            except ValidationError as exc:
                message = _contract_message(exc)
                for index, surface in enumerate(group.surfaces, start=1):
                    message = message.replace(f"S{index} ", f"{surface.label} ").replace(f"S{index}:", f"{surface.label}:")
                code = E.SURFACES_INTERSECT if "intersect" in message else E.MODEL_INVALID
                self.problems.add(code, f"element {group.member_id} (surfaces {group.labels}): {message}", where)
                return
            refused = self.outline(group)
            if refused is None:
                break
            message, location, involved = refused
            if refusal is None:
                refusal = (message, location)
            # A surface with a smaller clear semi-diameter can fall back from its window's outline to its clear edge.
            # Keep the original finding if no such edge makes the geometry valid.
            derivable = {i for i in involved if i not in group.derived_edges
                         and (group.surfaces[i].mechanical_semi_diameter_mm is None or group.surfaces[i].automatic_mechanical_edge)
                         and group.surfaces[i].clear_semi_diameter_mm < default_edges[i]}
            if not derivable:
                self.problems.add(E.EDGE_INVALID, *refusal)
                return
            group.derived_edges |= derivable
        if group.derived_edges:
            # a derived edge is kept only for a valid solid (the optical-solid check every run makes: the profiles of a
            # glass must not cross inside the clear aperture of either surface)
            solid, _ = element_issues(body_of(_Element(group.member_id, group.model)))
            if any(issue.severity == ERROR for issue in solid):
                self.problems.add(E.EDGE_INVALID, *refusal)  # type: ignore[misc]
                return
            labels = ", ".join(group.surfaces[i].label for i in sorted(group.derived_edges))
            group.edge_basis += (f"; surface{'s' if len(group.derived_edges) > 1 else ''} {labels} edged at "
                                 f"{'their' if len(group.derived_edges) > 1 else 'its'} clear semi-diameter")
        self.edge_facts(group)

    def edge_facts(self, group: _Group) -> None:
        """The element's edge thickness per glass and each surface's mechanical over clear semi-diameter: reported, warned
        about past the optical-solid check's limits, refused outside the ranges the prescription declares."""

        limits = self.rx.edge_limits
        model = group.model
        with localcontext(DECIMAL_CONTEXT):
            for index, medium in enumerate(group.media):
                if medium in (AIR, VACUUM):
                    continue  # edge thickness and its declared limits describe glass, not free space
                a, b = group.surfaces[index], group.surfaces[index + 1]
                reach = model.gap_outline_mm(index)[0]  # type: ignore[union-attr]
                thickness = min(group.z[index + 1] + _held(group, index + 1, reach, along_y) - group.z[index] - _held(group, index, reach, along_y)  # type: ignore[operator]
                                for along_y in _directions(group))
                diameter = 2 * min(a.clear_semi_diameter_mm, b.clear_semi_diameter_mm)
                ratio = thickness / diameter
                fact = {"surfaces": [a.label, b.label], "medium": medium, "edge_thickness_mm": _fact(thickness),
                        "clear_diameter_mm": _s(diameter), "ratio_to_clear_diameter": _fact(ratio)}
                if self.edge_review_ratio < float(ratio) <= EDGE_THICKNESS_RATIO_LIMIT:
                    fact["above_review_ratio"] = True  # a fact for review (stated only when it holds)
                group.edge_thickness.append(fact)
                what = f"element {group.member_id} (surfaces {group.labels}): the {medium} between surfaces {a.label} and {b.label}"
                if float(ratio) > EDGE_THICKNESS_RATIO_LIMIT:  # beyond every published product: the outline is not the edge
                    self.warnings.append(ImportWarning(
                        "EDGE_THICKNESS_IMPLAUSIBLE", f"{what} is {_fact(thickness)} mm thick at its edge, {ratio:.0%} of its clear diameter "
                        f"{_s(diameter)} mm (above {EDGE_THICKNESS_RATIO_LIMIT:.0%}); check the thicknesses and semi-diameters", a.location))
                if limits is not None and limits.thickness_min_mm is not None and thickness < limits.thickness_min_mm:
                    self.problems.add(E.EDGE_INVALID, f"{what} is {_fact(thickness)} mm thick at its edge, below the declared "
                                      f"edge_thickness_min_mm {_s(limits.thickness_min_mm)}", a.location)
                if limits is not None and limits.thickness_max_mm is not None and thickness > limits.thickness_max_mm:
                    self.problems.add(E.EDGE_INVALID, f"{what} is {_fact(thickness)} mm thick at its edge, above the declared "
                                      f"edge_thickness_max_mm {_s(limits.thickness_max_mm)}", a.location)
            for index, surface in enumerate(group.surfaces):
                clear = surface.clear_semi_diameter_mm
                edge = model.surface_edge_mm(index)[0]  # type: ignore[union-attr]
                ratio = edge / clear
                group.mechanical_to_clear.append({"surface": surface.label, "clear_semi_diameter_mm": _s(clear), "edge_radius_mm": _s(edge),
                                                  "ratio": _fact(ratio)})
                what = f"element {group.member_id}, surface {surface.label}: edged to {_s(edge)} mm with a clear semi-diameter of {_s(clear)} mm, a ratio of {_fact(ratio)}"
                if limits is not None and limits.ratio_min is not None and ratio < limits.ratio_min:
                    self.problems.add(E.EDGE_INVALID, f"{what}, below the declared mech_to_clear_ratio_min {_s(limits.ratio_min)}", surface.location)
                if limits is not None and limits.ratio_max is not None and ratio > limits.ratio_max:
                    self.problems.add(E.EDGE_INVALID, f"{what}, above the declared mech_to_clear_ratio_max {_s(limits.ratio_max)}", surface.location)
                reach = model.glass_outline_mm(index)[0]  # type: ignore[union-attr]
                if float(reach / clear) > EDGE_TO_CLEAR_RATIO_LIMIT and float(reach - clear) > EDGE_MARGIN_MIN_MM:
                    self.warnings.append(ImportWarning(
                        "EDGE_FAR_BEYOND_CLEAR_APERTURE", f"element {group.member_id}, surface {surface.label}: its glass reaches {_s(reach)} mm "
                        f"from the axis, {reach / clear:.2f} times its clear semi-diameter {_s(clear)} mm (above {EDGE_TO_CLEAR_RATIO_LIMIT:g} "
                        "times); give the surface its own mech_semi_diameter", surface.location))

    def outline(self, group: _Group) -> tuple[str, SourceLocation, tuple[int, ...]] | None:
        """Check profile reach, positive glass thickness over each glass outline and clearance where vacuum windows face
        one another. Return the first finding with its location and the (0-based) surfaces it concerns."""

        model = group.model
        directions = _directions(group)
        with localcontext(DECIMAL_CONTEXT):
            for index, (surface, profile) in enumerate(zip(group.surfaces, group.profiles)):
                edge = model.surface_edge_mm(index)[0]  # type: ignore[union-attr]
                if any(_sag(profile, edge, along) is None for along in directions):
                    if surface.mechanical_semi_diameter_mm is not None:
                        reach = f"its mechanical semi-diameter {_s(edge)} mm"
                    elif index in group.derived_edges:
                        reach = f"its clear semi-diameter {_s(edge)} mm"
                    elif VACUUM in group.media:
                        reach = f"its window's outline radius {_s(edge)} mm"
                    else:
                        reach = f"element {group.member_id}'s outline radius {_s(edge)} mm ({group.edge_basis})"
                    return (f"the surface's profile (radius {_s(surface.radius_mm) if surface.radius_mm is not None else 'inf'} "
                            f"mm) does not reach {reach}; give smaller semi-diameters", surface.location, (index,))
            for index, ((a, za), (b, zb)) in enumerate(zip(zip(group.surfaces, group.z), zip(group.surfaces[1:], group.z[1:]))):
                reach = model.gap_outline_mm(index)[0]  # type: ignore[union-attr]
                for rho in _samples(reach):
                    for along in directions:
                        gap = zb + _held(group, index + 1, rho, along) - za - _held(group, index, rho, along)  # type: ignore[operator]
                        if gap <= 0:
                            if group.media[index] == VACUUM:
                                message = (f"the windows facing across the vacuum gap between surfaces {a.label} and {b.label} "
                                           f"overlap at radius {rho.normalize():f} mm (their outlines overlap to {_s(reach)} mm); "
                                           "increase the vacuum spacing or reduce the mech_semi_diameter")
                            else:
                                message = (f"the glass between surfaces {a.label} and {b.label} has no thickness at "
                                           f"radius {rho.normalize():f} mm (the glass outline radius is {_s(reach)} mm, "
                                           f"{group.edge_basis}); check the radii and thickness, or give a smaller mech_semi_diameter")
                            return message, b.location, (index, index + 1)
        return None

    # --- between members

    def facing(self, front: _Group, back: _Group) -> None:
        """Consecutive lens elements across an air space: no contact inside the common clear aperture (the contract's
        check) and no overlap of the bodies out to the smaller outline."""

        a, b = front.surfaces[-1], back.surfaces[0]
        pa, pb = front.profiles[-1], back.profiles[0]
        za, zb = front.z[-1], back.z[0]
        last = len(front.surfaces) - 1
        try:
            SurfaceSequenceModelV1(
                fidelity=ModelFidelity.SURFACE_PRESCRIPTION,
                surfaces=(pa.model_copy(update={"surface_id": "S1", "vertex_z_mm": "0", "coating_id": None, "edge_radius_mm": None}),
                          pb.model_copy(update={"surface_id": "S2", "vertex_z_mm": _s(EXACT.subtract(zb, za)), "coating_id": None,
                                                "edge_radius_mm": None})),
                media=(AIR,), edge_radius_mm=_s(max(a.clear_semi_diameter_mm, b.clear_semi_diameter_mm)),
                valid_band_nm=self.resolved.band, approximations=BASE_APPROXIMATIONS,
                status=FactStatus.SPECIFIED,
            )
        except ValidationError as exc:
            message = _contract_message(exc).replace("S1 ", f"{a.label} ").replace("S2 ", f"{b.label} ")
            if "intersect" in message:  # the pair is only a contact probe (e.g. two elements may have different cylinder axes)
                self.problems.add(E.SURFACES_INTERSECT, f"elements {front.member_id} and {back.member_id}: {message}", b.location)
                return
        reach_a = front.model.glass_outline_mm(last)[0]  # type: ignore[union-attr]
        reach_b = back.model.glass_outline_mm(0)[0]  # type: ignore[union-attr]
        with localcontext(DECIMAL_CONTEXT):
            for rho in _samples(min(reach_a, reach_b)):
                for along_y in _directions(front, back):
                    gap = zb + _held(back, 0, rho, along_y) - za - _held(front, last, rho, along_y)  # type: ignore[operator]
                    if gap <= 0:
                        self.problems.add(E.BODIES_INTERSECT, f"elements {front.member_id} (surface {a.label}) and {back.member_id} "
                                          f"(surface {b.label}) overlap at radius {rho.normalize():f} mm (their outlines there are "
                                          f"{_s(reach_a)} and {_s(reach_b)} mm); increase the air space or reduce the "
                                          "mech_semi_diameter", b.location)
                        return

    def clearance(self, stop: _Group, neighbour: _Group, after: bool, reach: Decimal) -> Decimal | None:
        """The largest sampled radius (up to ``reach``) at which ``neighbour`` stays clear of the stop's plane; ``None``
        when it does at every radius."""

        surface_index = 0 if after else len(neighbour.surfaces) - 1
        z = neighbour.z[surface_index]
        plane = stop.z[0]
        limit = min(reach, neighbour.model.glass_outline_mm(surface_index)[0])  # type: ignore[union-attr]
        opening = stop.surfaces[0].clear_semi_diameter_mm
        if limit < opening:
            return None
        # Glass may protrude through the hole. Only the annulus from the opening to the plate's
        # edge has material which can overlap the neighbouring optic. Include the inner rim itself.
        previous = Decimal(0)
        with localcontext(DECIMAL_CONTEXT):
            for rho in (opening + offset for offset in _samples(limit - opening)):
                for along_y in _directions(neighbour):
                    sag = _held(neighbour, surface_index, rho, along_y)
                    surface_z = z + sag  # type: ignore[operator]
                    if (after and surface_z <= plane) or (not after and surface_z >= plane):
                        return previous
                previous = rho
        return None

    def stop(self, group: _Group, groups: list[_Group], index: int, unit_edge: Decimal) -> None:
        surface = group.surfaces[0]
        opening = surface.clear_semi_diameter_mm
        mechanical = surface.mechanical_semi_diameter_mm
        if surface.automatic_mechanical_edge and mechanical == opening:
            # A computed outline equal to the opening describes no plate material. Derive the
            # mounting extent from the surrounding assembly as for a stop without a stated edge.
            mechanical = None
        if mechanical is not None and mechanical < opening:
            self.problems.add(E.EDGE_INVALID, f"mech_semi_diameter {_s(mechanical)} mm is smaller than the stop opening {_s(opening)} mm",
                              surface.location.at("mech_semi_diameter"))
            return
        if not surface.is_plane:
            self.problems.add(E.UNSUPPORTED_FEATURE, "an aperture stop with air on both sides is a plane; leave its radius blank",
                              surface.location.at("radius"))
            return
        reach = mechanical if mechanical is not None else max(unit_edge, opening)
        edge = reach
        neighbours = [(groups[index - 1], group.axis_sign < 0)] if index > 0 and groups[index - 1].kind != "STOP" else []
        neighbours += [(groups[index + 1], group.axis_sign > 0)] if index + 1 < len(groups) and groups[index + 1].kind != "STOP" else []
        for neighbour, after in neighbours:
            clear = self.clearance(group, neighbour, after, reach)
            if clear is None:
                continue
            if mechanical is not None or clear < opening:
                where = "inside the stop opening" if clear < opening else f"inside the plate radius {_s(reach)} mm"
                self.problems.add(E.BODIES_INTERSECT, f"element {neighbour.member_id} reaches the plane of aperture stop {surface.label} "
                                  f"{where}; move the stop or give a smaller mech_semi_diameter", surface.location)
                return
            edge = min(edge, max(opening, clear.quantize(PLATE_QUANTUM, rounding=ROUND_FLOOR)))
        group.edge = edge
        if mechanical is not None:
            group.edge_basis = "its mechanical semi-diameter"
        else:
            group.edge_basis = "derived: the unit's largest outline radius, reduced to clear the neighbouring elements"
            self.assumptions.append(f"Aperture stop {surface.label}: plate radius {_s(edge)} mm ({group.edge_basis}); give "
                                    "mech_semi_diameter on the stop row to set it.")
        if edge == opening:
            if self.rx.source_format in {".zmx", ".seq", ".len"}:
                self.problems.add(E.EDGE_INVALID, f"aperture {surface.label} needs a plate outline larger than its "
                                  f"{_s(opening)} mm opening. Supply a mechanical radius for the opaque rim.", surface.location)
                return
            self.warnings.append(ImportWarning(
                "STOP_PLATE_AT_OPENING", f"aperture stop {surface.label}: the plate radius equals the opening ({_s(opening)} mm), so the "
                "stop blocks no light; give a larger mech_semi_diameter on the stop row", surface.location))
        try:
            group.model = ApertureStopModelV1(opening_radius_mm=_s(opening), edge_radius_mm=_s(edge), status=FactStatus.SPECIFIED)
        except ValidationError as exc:
            self.problems.add(E.MODEL_INVALID, f"aperture stop {surface.label}: {_contract_message(exc)}", surface.location)

    # --- the unit

    def check_folded_path(self, groups: list[_Group], image_vertex_z: Decimal) -> None:
        """A native scene encounters each body wherever the ray travels, including an unlisted return pass."""
        mirrors = [g for g in groups if g.kind == "MIRROR"]
        if not mirrors:
            return
        if len(mirrors) > 2:
            self.problems.add(E.UNSUPPORTED_FEATURE, "three or more reflections require explicit multi-pass encounter mapping",
                              mirrors[2].surfaces[0].location)
            return
        infinity = Decimal("Infinity")
        obj = self.rx.object
        start = -obj.distance_mm if obj and obj.distance_mm is not None else -infinity
        final_gap = self.rx.surfaces[-1].thickness_mm
        end = image_vertex_z + final_gap if final_gap is not None else (-infinity if len(mirrors) % 2 else infinity)
        primary = mirrors[0]
        if len(mirrors) == 2 and end >= primary.z[0] and not primary.surfaces[0].hole_radius_mm:
            self.problems.add(E.UNSUPPORTED_FEATURE, "the outgoing leg reaches the solid primary mirror. A coaxial beam "
                              "returning through the primary requires a declared central opening.", primary.surfaces[0].location)
            return
        vertices = [start, *(g.z[0] for g in mirrors), end]
        leg = 0
        for group in groups:
            if group.kind == "MIRROR":
                leg += 1
                continue
            lo, hi = min(group.z), max(group.z)
            for i, (a, b) in enumerate(zip(vertices, vertices[1:])):
                if i != leg and lo <= max(a, b) and hi >= min(a, b):
                    self.problems.add(E.UNSUPPORTED_FEATURE, f"surface {group.surfaces[0].label} also lies on propagation leg {i + 1}. "
                                      "This folded prescription needs an explicit multi-pass encounter model.", group.surfaces[0].location)
                    return

    def envelope(self, groups: list[_Group]) -> CylinderGeometryV1:
        radius = max(g.edge for g in groups)
        z_values: list[Decimal] = []
        with localcontext(DECIMAL_CONTEXT):
            for group in groups:
                if group.kind == "STOP":
                    z_values.append(group.z[0])
                    continue
                for index, z in enumerate(group.z):
                    for rho in _samples(group.edge):
                        for along_y in _directions(group):
                            z_values.append(z + _held(group, index, rho, along_y))  # type: ignore[operator]
            low = min(z_values).quantize(ENVELOPE_QUANTUM, rounding=ROUND_FLOOR)
            high = max(z_values).quantize(ENVELOPE_QUANTUM, rounding=ROUND_CEILING)
            if high == low:
                # A lone planar mirror or aperture has zero axial extent. Its display envelope needs a finite span.
                low -= ENVELOPE_QUANTUM
                high += ENVELOPE_QUANTUM
        return CylinderGeometryV1(diameter_mm=_s(radius * 2), z_min_mm=_s(low), z_max_mm=_s(high), status=FactStatus.DERIVED)

    def display_name(self, text: str, suffix: str = "") -> str:
        """``text`` + ``suffix`` within the contract's length; ``text`` is shortened so ``suffix`` is always kept."""

        room = NAME_MAX - len(suffix)
        return (text if len(text) <= room else text[: room - 3] + "...") + suffix

    def lower(self) -> LoweredUnit | None:
        z = self.positions()
        groups = self.groups(z)
        obj = self.rx.object
        if groups and obj and obj.distance_mm is not None and obj.distance_mm > 0 and self.rx.source_format in {".seq", ".len", ".zmx"}:
            if groups[0].z[0] < -obj.distance_mm:
                self.problems.add(E.SURFACE_ORDER_INVALID, "the first physical optic lies behind the stated real object. "
                                  "Check the signed spacings of the intervening reference planes.", groups[0].surfaces[0].location)
        self.check_folded_path(groups, z[-1])
        if self.problems:
            return None
        for group in groups:
            if group.kind != "STOP":
                self.element(group)
        if self.problems:
            return None
        lenses = sorted((g for g in groups if g.kind != "STOP"), key=lambda g: g.z[0])
        for front, back in zip(lenses, lenses[1:]):
            self.facing(front, back)
        if self.problems:
            return None
        unit_edge = max((g.edge for g in lenses), default=Decimal(0))
        for index, group in enumerate(groups):
            if group.kind == "STOP":
                self.stop(group, groups, index, unit_edge)
        if self.problems:
            return None
        title = self.rx.name or "Imported prescription"
        models: list[ProjectOpticalModelV1] = []
        members: list[CompoundMemberV1] = []
        report: list[dict] = []
        for group in groups:
            model_id = f"{self.model_id}-{group.member_id}"
            rows = group.rows
            row_text = f"row {rows[0]}" if len(rows) == 1 else f"rows {rows[0]}-{rows[-1]}"
            what = "aperture stop" if group.kind == "STOP" else "mirror" if group.kind == "MIRROR" else ("vacuum cell" if VACUUM in group.media else
                                                                 "cemented element" if len(group.surfaces) > 2 else "element")
            try:
                models.append(ProjectOpticalModelV1(
                    model_id=model_id, display_name=self.display_name(f"{title}: {what} {group.member_id}, surface{'s' if len(group.surfaces) > 1 else ''} "
                                                                      f"{group.labels} ({row_text}, {self.rx.source_format})"),
                    model=group.model))
            except ValidationError as exc:
                self.problems.add(E.MODEL_INVALID, f"{what} {group.member_id} (surfaces {group.labels}): {_contract_message(exc)}",
                                  group.surfaces[0].location)
                continue
            members.append(CompoundMemberV1(member_id=group.member_id, model_id=model_id,
                                            pose=PoseV1(position_mm=(0.0, 0.0, float(group.z[0])),
                                                        quaternion_wxyz=(1, 0, 0, 0) if group.axis_sign == 1 else (0, 0, 1, 0))))
            entry = {"member_id": group.member_id, "model_id": model_id, "model_kind": "APERTURE_STOP" if group.kind == "STOP" else "SURFACE_SEQUENCE",
                     "surfaces": [s.label for s in group.surfaces], "rows": rows, "z_mm": _s(group.z[0]),
                     "edge_radius_mm": _s(group.edge), "edge_basis": group.edge_basis}
            if group.kind == "LENS":
                entry["media"] = list(group.media)
                if any(p.has_own_edge for p in group.profiles):  # surface edges differ from the sequence outline
                    entry["surface_edge_radii_mm"] = [_s(group.model.surface_edge_mm(i)[0]) for i in range(len(group.profiles))]  # type: ignore[union-attr]
                if group.derived_edges:  # surfaces without mechanical data edged at their clear semi-diameter
                    entry["derived_edges"] = [{"surface": group.surfaces[i].label, "edge_radius_mm": _s(group.surfaces[i].clear_semi_diameter_mm),
                                               "basis": EDGE_BASIS_CLEAR_SEMI_DIAMETER} for i in sorted(group.derived_edges)]
                entry["edge_thickness"] = group.edge_thickness
                entry["mechanical_to_clear"] = group.mechanical_to_clear
            report.append(entry)
        if self.problems:
            return None
        envelope = self.envelope(groups)
        reflected_output = sum(surface.is_mirror for surface in self.rx.surfaces) % 2 == 1
        output_axis = ("0", "0", "-1") if reflected_output else AXIS
        output_z = envelope.z_min_mm if reflected_output else envelope.z_max_mm
        ports = (
            FramePortV1(port_id="IN", role="INPUT", point_mm=("0", "0", envelope.z_min_mm), direction=AXIS, status=FactStatus.DERIVED),
            FramePortV1(port_id="OUT", role="OUTPUT", point_mm=("0", "0", output_z), direction=output_axis, status=FactStatus.DERIVED),
        )
        try:
            compound = ProjectOpticalModelV1(
                model_id=self.model_id,
                display_name=self.display_name(title, f" ({self.rx.source_format}, source SHA-256 {self.rx.source_sha256})"),
                model=CompoundModelV1(members=tuple(members), envelope=envelope, ports=ports))
        except ValidationError as exc:
            self.problems.add(E.MODEL_INVALID, f"the imported unit: {_contract_message(exc)}")
            return None
        image_z = None
        last = self.rx.surfaces[-1]
        if self.rx.image is not None and last.thickness_mm is not None:
            image_z = EXACT.add(z[-1], last.thickness_mm)
            if last.thickness_mm != 0 and (last.thickness_mm < 0) != reflected_output:
                self.warnings.append(ImportWarning(
                    "IMAGE_BEFORE_LAST_SURFACE", f"the final spacing {_s(last.thickness_mm)} mm places a virtual image at z = "
                    f"{_s(image_z)} mm along the backward extension of the outgoing ray",
                    last.location.at("thickness")))
        counts = {"surfaces": len(self.rx.surfaces), "elements": len(lenses),
                  "cemented_elements": sum(1 for g in lenses if any(a not in (AIR, VACUUM) and b not in (AIR, VACUUM)
                                                                for a, b in zip(g.media, g.media[1:]))),
                  "aperture_stops": sum(1 for g in groups if g.kind == "STOP"), "dummy_surfaces": self.dummies, "members": len(groups)}
        mirror_count = sum(g.kind == "MIRROR" for g in lenses)
        if mirror_count:
            counts["mirrors"] = mirror_count
        return LoweredUnit(models=(compound, *models), compound_id=self.model_id, members=tuple(report),
                           assumptions=tuple(self.assumptions), warnings=tuple(self.warnings), image_plane_z_mm=image_z, counts=counts)


def lower_prescription(prescription: Prescription, resolved: ResolvedMaterials, model_id: str, problems: ProblemList,
                       edge_review_ratio: float = EDGE_THICKNESS_REVIEW_RATIO) -> LoweredUnit | None:
    """The COMPOUND unit and its member models (module docstring); problems are added to ``problems`` (``None`` then).
    ``edge_review_ratio``: an element's edge thickness over its clear diameter above which its fact row says
    ``above_review_ratio``."""

    return _Lowering(prescription, resolved, model_id, problems, edge_review_ratio).lower()


__all__ = ["LoweredUnit", "lower_prescription"]
