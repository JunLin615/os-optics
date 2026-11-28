"""Optical model -> Engine element: the single place where an optical model becomes Engine physics.

Every model is compiled by the same generic rules below, whether it is a product's model in the public optical catalog
(``PublicOpticalCatalogV2``, schema 3.0) or a model the Project declares (``ProjectDocumentV2.optical_models``);
nothing here knows a SKU, a category or a form. A model only supplies numbers: surfaces, media, materials, coatings,
apertures, bodies, retardance, extinction. ``compile_model(instance_id, model, resolver, identity)`` returns a
``CompiledElement``: the Engine surfaces in the element frame, the Engine material models they refer to, and a validity
envelope per surface that ``validity.py`` checks after the run. The ``resolver`` says where the model's media and
coatings come from: the catalog (``CatalogResolver``; ``compile_element`` compiles a catalog product this way) or the
Project's ``model_materials`` / ``model_coatings`` (``ProjectResolver``; ``compile_declared``). ``lowering.py`` places
compiled elements in the Engine request; the Optical Simulation View binds a Project's Engine request to the same
compiled elements.

Numbers: every published decimal string becomes ``float(Decimal(s))`` (the nearest binary64), and derived values are
exact decimal arithmetic in ``DECIMAL_CONTEXT`` before one final conversion, so the hashed Engine input is identical
on every runtime. No libm function (trig, exp, log) feeds the Engine input.

Surfaces (both surface model kinds). A surface's profile and clear aperture become:

* a plane with a centred circular aperture inside a round outline: ``PlaneSurface`` with ``CircularAperture(clear
  radius)`` and the opaque ring ``mount_margin_mm = edge radius - clear radius``;
* a sphere (no conic, no polynomial, not cylindrical) with a centred circular aperture inside a round outline:
  ``SphereSurface`` with centre ``(0, 0, z + R)``, radius ``|R|``, ``cap_axis = (0, 0, -sign R)``, ``aperture_radius =
  min(clear radius, |R|)``, the same ring, and ``medium_positive`` = the medium on the side away from the centre;
* any other plane: ``PlaneSurface`` with its in-plane axes given (``aperture_u_axis``), a circular or rectangular
  aperture (decentred by ``center_offset_mm``) and, when the outline is not the clear aperture grown evenly, the
  ``mount_outline`` (the element's outline, or the body face / section the surface lies on);
* any other profile (conic, even asphere, cylinder, axicon, or a sphere with a rectangular / decentred aperture):
  ``SagSurface`` with the vertex curvature ``1 / R``, the conic constant, the polynomial terms and the axicon term
  (``linear_sag_coefficient``, the Engine's ``L`` of ``L rho``; a cylinder's ``rho`` is its curved coordinate). The Engine's sag surface is curved
  along its ``u`` axis; a catalog cylinder is straight along ``cylinder_axis``, so a cylinder straight along X has its
  Engine ``u`` along the surface's Y, and its aperture coordinates ``(x, y)`` become ``(y, -x)``. A two-variable sag shape
  (``sag``: Zernike, XY polynomial, biconic, toroid) is the Engine's ``sag_shape``, in the surface's own ``(x, y) = (u, v)``.

The light between the clear aperture and the outline is blocked (``EDGE_AS_OPAQUE_RIM``); light beyond the outline passes
by. Where a body face is not a rectangle in the surface's axes (the outline cannot be stated exactly), the support stays
the clear aperture: light beyond it continues in air, and ``validity.py`` reports it if it crosses the body.

``SURFACE_SEQUENCE`` (``SurfaceSequenceModelV1``, version 2), surface ``i`` of ``n`` (ids ``S1`` .. ``Sn``, kept as Engine
surface ids), vertex ``z_i``, axis local +Z: ``before`` = ``media[i-1]`` (air for the first surface), ``after`` =
``media[i]`` (air for the last one); ``medium_positive`` is the +Z side (``after``). A surface's outline is the outline of
the glass it bounds (``SurfaceSequenceModelV1.glass_outline_mm``: its own mechanical edge, or the element outline when it
states none, grown to a larger edge of a neighbour across glass -- the ground flange beyond its edge is opaque too); a
sphere with its own edge has its ring end at ``|R|`` at most.

``SURFACE_SET`` (``SurfaceSetModelV1``): each posed surface at its ``origin_mm`` with its ``normal`` and ``u_axis``;
``medium_positive`` = ``medium_front`` (the +normal side), ``medium_negative`` = ``medium_back``. The outline is the body
face the surface lies on, or the body's section through an internal surface (a cube's coated diagonal); a surface with no
body (an off-axis mirror face) has no mount ring.

A catalog material becomes the Engine material ``catalog:<MATERIAL_ID>`` (``engine_material_id``) -- a
``SellmeierMaterial`` or a PCHIP ``TabulatedIndexMaterial`` valid on exactly the published band -- so a catalog model never
silently replaces an Engine built-in; the published ``AIR`` is the Engine's ``air`` -- in a native request the air of the
Project's environment (``environment_air``: ``AirMaterial`` at its temperature and pressure, the constant 1 at zero pressure),
which ``lowering.py`` states as a request material -- and a declared model's ``VACUUM`` gap is the Engine's built-in
``vacuum`` (index 1). A material's ``index_reference``, spectral ``bulk_loss`` and ``thermo_optic`` data, and the new law kinds
(power series, Herzberger, temperature Sellmeier, measured tables) are carried into the Engine model (``engine_material``);
its temperature is the environment's where its index depends on it. A material the Project declares
(``ProjectModelMaterialV1``) becomes ``project:<MATERIAL_ID>``: the same Sellmeier / tabulated forms, or a
``ConstantIndexMaterial`` for a constant index, with its bulk ``absorption_coefficient_per_mm`` when it states one. The two
namespaces never meet, so a declared material never replaces a catalog material of the same name. A declared uniaxial
crystal (``birefringence``) is cut by the element that uses it: with the optic axis the declared model states
(``optic_axes``) it becomes the Engine ``UniaxialMaterial`` ``project:<MATERIAL_ID>@<instance>`` (a compound member:
``@<instance>-<member>``; ``CrystalCutResolver``), its ordinary and extraordinary index laws the same Sellmeier / tabulated /
constant numbers, its optic axis normalized in exact decimal arithmetic and given in the component's frame
(``optic_axis_component``: it turns with the component's pose; a member's axis is turned by the member's pose).

``traced``:

* ``REFLECTED``: ``MirrorInteraction`` with the surface's coating, reflecting on the side light arrives from: the +Z side
  of a surface-sequence surface (``positive`` for a plane or a sag surface, ``positive`` for a convex and ``negative``
  for a concave sphere), the front (``positive``) of a posed surface. A reflecting surface that states no coating is the
  Engine's ideal reflector: ``MirrorInteraction`` without a coating (R = 1 for both polarizations, reflection phases pi
  for s and 0 for p); nothing is checked there after the run (it states no wavelength or angle range), and the run lists
  the surface in the element's model description (``element_models[].ideal_reflectors``);
* ``TRANSMITTED``: ``RefractInteraction`` with the coating (``None``: the bare Fresnel interface of the two media) and
  ``trace_fresnel_reflection=False`` (``GHOST_REFLECTIONS_NOT_TRACED``); total internal reflection is the Engine's own;
* ``TRANSMITTED_AND_REFLECTED``: a splitting surface. Between two different media: ``RefractInteraction`` with
  ``trace_fresnel_reflection=True`` -- the reflected branch is traced, with the coating's values or, uncoated, the bare
  Fresnel reflection (a wedge sampler's front face); with the same medium on both sides (a coated plane without a
  substrate in air, a coated internal surface inside glass): ``BeamSplitterInteraction``, which needs a coating
  (``SURFACE_COATING_REQUIRED``). Only such surfaces trace a reflected branch, so branching stays bounded.

Coatings (``engine_coating``): bands -> per polarization (``R_s``, ``R_p``, ``T_s``, ``T_p``; equal when the band is not
polarization-resolved): one band -> ``ConstantCoating`` with the Engine's default validity (100-20000 nm, 0-90 deg);
several bands -> an AOI-independent ``TabulatedCoating`` whose wavelength nodes are the band edges (a single-wavelength
band is one node); a spectrum -> a ``TabulatedCoating`` with its own wavelength nodes (linear between them, as published;
a polarization-resolved spectrum with its own s and p values, else s = p = the unpolarized values); an angle-resolved table
(``angular_spectrum``) -> a ``TabulatedCoating`` on its own wavelength x AOI grid (bilinear, the AOI in the incident
medium, as the Engine reads it). Tables are held flat beyond the outermost data out to 100 and 20000 nm and, on the AOI
axis, out to 0 and 90 deg (an AOI-independent table is ``[0, 90]`` with equal columns). The Engine therefore never fails
on a coating range: values between bands or beyond the data are not published data, and ``validity.py`` reports every
such use (``OUT_OF_MODEL_SCOPE``), as it reports a wavelength outside the model's band or an angle outside a coating's
``valid_aoi_deg`` (read per ``aoi_reference``; an angle-resolved table's ``valid_aoi_deg`` lies inside its angles).

A compiled coating carries its record's id, angle range, reference medium and wavelengths (``declared_validity``). They change no
coefficient and no trace; the exposure bound evaluates the light the trace leaves out against them
(``validation/coating_use.py``), and ``validity.py`` keeps judging the chief ray from the published coating.

Coating phases (``engine_phases``): a coating's stated ``phase`` becomes the interaction's ``InterfacePhases`` (constant
phases one value each; a wavelength table on its own nodes, held flat to 100 and 20000 nm like the coating tables, and
the model band lies inside it). A ``MirrorInteraction`` then uses the stated reflection phases instead of (pi, 0), a
``BeamSplitterInteraction`` the stated reflection / transmission phases instead of (pi / 2 with ``r_p = -r_s``, 0), a
``RefractInteraction`` the stated transmission (and, for its reflected light, reflection) phases instead of the bare
Fresnel ones. A coating without ``phase`` keeps those defaults (the Engine input is unchanged).

Coating group-delay bounds: a coating's ``group_delay_bound_fs`` (catalog schema 3.2) becomes its Engine coating's
``group_delay_bound_fs``, which the trace attaches to every pass through it (``optics/interference/coherence.py``). It
holds where the coating's data hold, like its reflectance and transmittance: a use outside its bands or ``valid_aoi_deg``
is reported by ``validity.py`` (``OUT_OF_MODEL_SCOPE``). A coating without one keeps an undetermined group delay (the
Engine input is unchanged).

``POLARIZATION_ELEMENT`` (``PolarizationElementModelV1``): one ``PlaneSurface`` with id ``element`` at
``(0, 0, plane_z)``, normal ``+Z``, air on both sides, the clear aperture and opaque rim as above, and

* ``RETARDER``: ``RetarderInteraction(fast_axis_local=(1, 0, 0), retardance_rad = 2 pi retardance_waves,
  design_wavelength_nm, achromatic = (retardance_dispersion == CONSTANT), transmittance)``;
* ``LINEAR_POLARIZER``: ``PolarizerInteraction(axis_local=(1, 0, 0), transmittance_parallel = T,
  transmittance_perpendicular = T / extinction_ratio)``;
* ``CIRCULAR_POLARIZER``: that polarizer surface and a second ``PlaneSurface`` with id ``retarder`` at
  ``(0, 0, plane_z + retarder_offset_mm)`` (same aperture), ``RetarderInteraction(fast_axis_local = (cos a, sin a, 0))`` with
  ``a = retarder_axis_deg`` (``math.exact_trig``) and the retardance as for a ``RETARDER``, transmittance 1 (the element's
  transmittance is its polarizer's).

``retardance_dispersion = TABULATED``: the retarder also carries ``retardance_table_nm`` / ``retardance_table_rad`` (2 pi x
each row's waves) and ``retardance_interpolation`` (``linear`` / ``points_only``). ``position_parameter = axis_rotation_deg``:
``with_attenuator_position`` turns every polarizer and retarder axis of the element (of a ``COMPOUND`` unit: of the member
that names the parameter) about its surface normal by the selected angle.

With ``transmittance_bands`` the interaction also carries one band per published band (``RetarderTransmittanceBand`` /
``PolarizerTransmittanceBand``: the band's T, and for a polarizer ``T / extinction_ratio`` with the band's extinction ratio
or, when the bands state none, the element's): each spectral line uses the band holding its wavelength (on a shared
boundary the conservative pair: the smaller transmittance and, for a polarizer, the larger perpendicular transmittance),
the scalar values (the band minimum) apply outside every band.

``NEUTRAL_DENSITY`` (``NeutralDensityModelV1``): one ``PlaneSurface`` with id ``attenuator`` at ``(0, 0, 0)``, normal ``+Z``,
in-plane axes local X / Y, air on both sides, the clear aperture (circle or rectangle) and the opaque rim out to the outline
as for an aperture stop, and ``NeutralDensityInteraction`` with the density law (``FIXED`` / ``LINEAR_ROTARY`` /
``STEPPED_ROTARY`` / ``LINEAR_GRADIENT``, the same numbers), ``x_axis_local = (1, 0, 0)``, ``uncoated_transmittance``,
``reflectance`` (absent: 0), ``coated_inner_radius_mm`` (a coated annulus around an uncoated hub) and ``position`` 0; ``with_attenuator_position`` sets the wheel rotation (degrees) or
gradient shift (mm) the component's position parameter selects (``lowering.py``).

Model kinds a Project declares directly:

* ``APERTURE_STOP`` (``ApertureStopModelV1``): one ``PlaneSurface`` with id ``stop`` at ``(0, 0, plane_z)``, normal ``+Z``,
  in-plane axes local X / Y, air on both sides, ``ApertureStopInteraction`` (transmittance 1). Its clear aperture is the
  opening (``CircularAperture`` with the central ``obstruction_radius_mm``, or ``RectangularAperture`` for a slit, decentred
  by ``opening_center_mm``; with ``opening_shape`` an ``EllipticalAperture``, ``DShapedAperture`` or ``PolygonAperture``,
  ``_shaped``); the plate outline is the opaque mount (an even ring, or ``mount_outline``): light on the plate
  or the obstruction is blocked, light beyond the outline passes by;
* ``IDEAL_THIN_LENS`` (``IdealThinLensModelV1``): one ``PlaneSurface`` with id ``lens`` at ``(0, 0, plane_z)``, normal
  ``+Z``, air on both sides, ``ThinLensInteraction(focal_length_mm, transmittance)``, clear aperture ``clear_radius_mm``
  and the opaque ring out to ``edge_radius_mm``;
* ``TABULATED_THIN_LENS`` (``TabulatedThinLensModelV1``): the same ``lens`` plane with in-plane axes local X / Y
  (``aperture_u_axis`` = local X) and ``TabulatedThinLensInteraction``: the rows (``wavelength_nm``, ``focal_length_mm``,
  ``transmittance``), the interpolations (``LINEAR_IN_OPTICAL_POWER`` -> ``linear_in_optical_power``, ``LINEAR`` ->
  ``linear``, ``POINTS_ONLY`` -> ``points_only``), ``point_tolerance`` ``THIN_LENS_POINT_TOLERANCE`` (0.5 %),
  ``max_field_angle_deg``, ``max_beam_radius_mm``, the validated polarizations (``LINEAR_X`` -> ``linear_u``,
  ``LINEAR_Y`` -> ``linear_v``; ``ANY`` or none stated: every polarization) and the supported metrics
  (``engine_supported_metrics``: each listed metric is the Engine target metric of that name, and ``transmittance`` --
  the model's delivered power fraction -- stands for the power metrics ``power_W`` and ``coherent_power_W``);
* ``FARADAY_ROTATOR`` (``FaradayRotatorModelV1``): one ``PlaneSurface`` with id ``rotator`` at ``(0, 0, plane_z)``, normal
  ``+Z`` (the field axis), in-plane axes local X / Y, air on both sides, ``FaradayRotatorInteraction``: the rows
  (``wavelength_nm``, ``rotation_rad`` = ``rotation_deg`` as radians, ``transmittance``) and the interpolation (``LINEAR`` ->
  ``linear``, ``POINTS_ONLY`` -> ``points_only``), clear aperture ``clear_radius_mm`` and the opaque ring out to
  ``edge_radius_mm``;
* ``DIFFRACTION_GRATING`` (``DiffractionGratingModelV1``): one ``PlaneSurface`` with id ``grating`` at ``(0, 0, plane_z)``, normal
  ``+Z``, in-plane axes local X / Y, ``medium_positive`` / ``medium_negative`` its media, ``DiffractionGratingInteraction``: the
  mode, the period in nm (from ``line_density_per_mm`` as ``1e6 / density``), the grating vector ``(cos a, sin a, 0)`` of
  ``grating_vector_angle_deg`` and the orders, with the response table on its grid (``GratingOrderTable``: an ``UNPOLARIZED``
  table holds its mean as both TE and TM), clear aperture and outline as a neutral-density attenuator's;
* ``JOINED_BODIES`` (``JoinedBodiesModelV1``, a catalog polarizing prism): ONE Engine component holding every body's surfaces,
  compiled as a ``COMPOUND`` unit of ``SURFACE_SET`` members whose frame is the element frame (each body states its surfaces
  and body there): surface ids ``<body>-<surface_id>`` (``A-S1``), a crystal body's crystal the Engine ``UniaxialMaterial``
  ``catalog:<MATERIAL_ID>@<instance>-<body>`` with the body's optic axis (``optic_axes``), and the joints applied as a
  ``COMPOUND`` unit's (``_join``: an optical contact is one interface, a cement layer the cement between the two faces). The
  ``CompiledElement`` keeps the published model; its ``members`` are the bodies.
* ``COMPOUND`` (``CompoundModelV1``): ONE Engine component holding every member's surfaces. Each member model is compiled
  in its own frame and placed by its ``pose`` in the unit frame (``x_unit = R(q) x_member + p``: vertices, centres and
  normals, sag and aperture axes, polarizer / retarder axes); its surface ids become ``<member_id>-<surface_id>``
  (``L1-S1``, ``STOP-stop``). The ``CompiledElement`` of the unit lists the members (``members``) with their surfaces in
  the member frame, so validity checks and views treat every member body on its own. A member with an ``array``
  (``MemberArrayV1``) stands for one member per cell, ``<member_id>_<row>_<column>`` at the member's pose moved by the
  cell's lattice offset in the unit frame (``_array_cells``).

A surface's ``clear_shape`` (``ClearShapeV1``: an ellipse inscribed in its half widths, its circle cut by a flat, a polygon
inside its circle or rectangle) becomes an ``EllipticalAperture``, ``DShapedAperture`` or ``PolygonAperture`` in the
surface's Engine (u, v) axes, and its ``holes`` (``SurfaceHoleV1``) the surface's ``holes``; such a surface is never one of
the rotationally symmetric forms above (a plain plane or sphere), so it compiles to a ``PlaneSurface`` or ``SagSurface``
whose outline is the glass outline.

An ``APERTURE_STOP`` with a ``sample`` (``ComplexTransmissionSampleV1``) compiles its plane's interaction to a
``ComplexTransmissionInteraction`` (``sample_interaction``: amplitude, phase, ``LINEAR_RAMP`` -> ``LinearPhaseRamp``, ``STEP`` ->
``PhaseStep``, ``ZERNIKE`` -> ``ZernikePhase``, the map with ``nodes_x`` / ``nodes_y`` as ``nodes_u`` / ``nodes_v``): the opening
holds the thin sample, the plate around it blocks.

A surface's ``scatter`` (``SurfaceScatterV1``) becomes the ``scatter`` (``SurfaceScatter``) of its refracting or reflecting
interaction: ``bsdf``, ``fraction``, ``side`` (``REFLECT`` -> ``reflect``, ``TRANSMIT`` -> ``transmit``), ``exponent``,
``sample_count`` and ``seed`` (their Engine defaults when absent); a surface that splits a beam into two traced branches with
the same medium on both sides (a ``BeamSplitterInteraction``) is refused with it. A declared material's
``scattering_coefficient_per_mm`` and ``anisotropy_g`` become the Engine material's.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, localcontext
from typing import Any

import numpy as np
from pydantic import ValidationError

from .._vendor.shared_contracts.design_catalog import (
    AIR,
    AngleRangeV1,
    CoatingAngularSpectrumV1,
    JoinedBodiesModelV1,
    PolarizationElementKind,
    PolarizationElementModelV1,
    PosedSurfaceV1,
    PrescriptionSurfaceV1,
    PublicCoatingV1,
    PublicMaterialV1,
    PublicOpticalCatalogV2,
    PublicOpticalProductV2,
    RetardanceDispersion,
    SellmeierDispersionV1,
    SurfaceSequenceModelV1,
    SurfaceSetModelV1,
    TracedBranches,
    WavelengthBandV1,
)
from .._vendor.shared_contracts.design_catalog import (
    BoxGeometryV1,
    ConvexPolyhedronGeometryV1,
    CylinderGeometryV1,
    DesignGeometryV1,
    FactStatus,
    FrameOrigin,
    FrameReferenceAxis,
    OpticalCategory,
    OpticalForm,
    OpticalFrameV1,
    OpticalSymmetry,
)
from .._vendor.shared_contracts.design_catalog.optical_catalog import DECIMAL_CONTEXT, RESERVED_MEDIA, VACUUM, TabulatedDispersionV1
from .._vendor.shared_contracts.design_catalog.material_physics import (
    ExtinctionCoefficientLossV1,
    HerzbergerDispersionV1,
    IndexReferenceV1,
    PowerSeriesDispersionV1,
    SchottThermoOpticV1,
    TemperatureSellmeierDispersionV1,
)
from .._vendor.shared_contracts.optical_design.optical_model import DEFAULT_ENVIRONMENT, PropagationEnvironmentV1
from .._vendor.shared_contracts.common.decimal_string import decimal_to_string
from .._vendor.shared_contracts.common.geometry import PoseV1
from .._vendor.shared_contracts.design_catalog.optical_catalog import NeutralDensityModelV1
from .._vendor.shared_contracts.optical_design.optical_model import (
    ApertureStopModelV1,
    CompoundModelV1,
    ConstantIndexDispersionV1,
    CrystalOpticAxisV1,
    DiffractionGratingModelV1,
    FacetSolidModelV1,
    NonlinearCrystalModelV1,
    FaradayRotatorModelV1,
    IdealThinLensModelV1,
    MAX_CEMENT_THICKNESS_MM,
    MemberJointV1,
    TabulatedThinLensModelV1,
    ProjectModelMaterialV1,
    ProjectOpticalModelV1,
    gradient_rod,
)
from ..config import DEFAULT_NUMERICAL_CONFIG
from ..contracts.geometry import Pose3D
from ..contracts.materials import (
    AirMaterial,
    ExtinctionCoefficientLoss,
    HerzbergerMaterial,
    IndexReference,
    InternalTransmittanceLoss,
    PowerSeriesMaterial,
    SchottThermoOptic,
    TemperatureSellmeierMaterial,
    ConstantCoating,
    ConstantIndexLaw,
    ConstantIndexMaterial,
    CrystalTemperature,
    DeclaredCoatingValidity,
    IndexLaw,
    MaterialOpticalModel,
    SecondHarmonicGeneration,
    SellmeierIndexLaw,
    SellmeierMaterial,
    SourceTableIndexMaterial,
    TabulatedCoating,
    TabulatedIndexLaw,
    TabulatedIndexMaterial,
    ThermoOpticRow,
    UniaxialMaterial,
    ConstantGradient,
    GRADIENT_MEDIA,
    AxialGradientIndexMaterial,
    GradientIndexMaterial,
    TabulatedGradient,
)
from ..contracts.optics import (
    AbsorberInteraction,
    ApertureStopInteraction,
    FacetApproximation,
    MeshFacetGroup,
    MeshSurface,
    BeamSplitterInteraction,
    BiconicSag,
    CircularAperture,
    DensityStep,
    DShapedAperture,
    EllipticalAperture,
    DiffractionGratingInteraction,
    FaradayRotatorInteraction,
    GratingOrderTable,
    FixedDensity,
    InterfacePhases,
    LinearGradientDensity,
    LinearRotaryDensity,
    MirrorInteraction,
    NeutralDensityInteraction,
    SteppedRotaryDensity,
    PlaneSurface,
    PolarizerInteraction,
    PolarizerTransmittanceBand,
    PolygonAperture,
    RectangularAperture,
    RefractInteraction,
    SurfaceScatter,
    ComplexTransmissionInteraction,
    LinearPhaseRamp,
    PhaseStep,
    SampledTransmissionMap,
    ZernikePhase,
    ZernikePhaseTerm,
    RetarderInteraction,
    RetarderTransmittanceBand,
    SagSurface,
    SphereSurface,
    TabulatedThinLensInteraction,
    ThinLensInteraction,
    ToroidalSag,
    XyPolynomialSag,
    XySagTerm,
    ZernikeSag,
    ZernikeSagTerm,
)
from ..math.exact_trig import cis, cos_sin
from ..optics import apertures as aperture_regions
from ..scene.lower import lower_aperture
from ..scene.model import ApertureG
from ..optics.gaussian.truncation import gauss_legendre_rule
from ..optics.materials.index import build_evaluator
from ..optics.surfaces.tabulated_lens import ThinLensTable, response_at
from .bodies import Solid, face_polygon, section_polygon, solid_of
from .design_point import DEFAULT_DESIGN_POINT, DesignPointTolerances
from ..optics.surfaces.facets import planar_patches
from .errors import LocalRunError, LocalRunErrorCode, LocalRunProblem

#: The Engine angle (degrees from u toward v) of a flat's outward normal along +u, +v, -u, -v.
FLAT_SIDE_ANGLES_DEG = {(1, 0): 0.0, (0, 1): 90.0, (-1, 0): 180.0, (0, -1): -90.0}
#: An Engine aperture of a compiled surface.
EngineAperture = CircularAperture | RectangularAperture | EllipticalAperture | DShapedAperture | PolygonAperture

SURFACE_SEQUENCE = "SURFACE_SEQUENCE"
SURFACE_SET = "SURFACE_SET"
POLARIZATION_ELEMENT = "POLARIZATION_ELEMENT"
APERTURE_STOP = "APERTURE_STOP"
IDEAL_THIN_LENS = "IDEAL_THIN_LENS"
TABULATED_THIN_LENS = "TABULATED_THIN_LENS"
FARADAY_ROTATOR = "FARADAY_ROTATOR"
DIFFRACTION_GRATING = "DIFFRACTION_GRATING"
COMPOUND = "COMPOUND"
NEUTRAL_DENSITY = "NEUTRAL_DENSITY"
FACET_SOLID = "FACET_SOLID"
NONLINEAR_CRYSTAL = "NONLINEAR_CRYSTAL"
JOINED_BODIES = "JOINED_BODIES"
#: Published simulation models this runtime compiles: (model_kind, model_version).
SUPPORTED_MODELS = frozenset({(SURFACE_SEQUENCE, "2"), (SURFACE_SET, "1"), (POLARIZATION_ELEMENT, "1"), (NEUTRAL_DENSITY, "1"),
                              (JOINED_BODIES, "1")})
#: Models a Project may declare (``ProjectOpticalModelV1.model``): the catalog's (but its joined bodies: a Project joins members
#: of a ``COMPOUND`` unit) and the Project-only kinds.
DECLARED_MODELS = (SUPPORTED_MODELS - {(JOINED_BODIES, "1")}) | frozenset({(APERTURE_STOP, "1"), (IDEAL_THIN_LENS, "1"), (TABULATED_THIN_LENS, "1"), (FARADAY_ROTATOR, "1"),
                                                (DIFFRACTION_GRATING, "1"), (COMPOUND, "1"), (FACET_SOLID, "1"),
                                                (NONLINEAR_CRYSTAL, "1")})
#: A tabulated thin lens's validated metric that names a group of Engine target metrics rather than one: its delivered
#: power fraction validates the power the focused beam brings to a plane.
METRIC_GROUPS = {"transmittance": ("power_W", "coherent_power_W")}
#: Engine names of the polarizations a tabulated thin lens states (its plane's in-plane axes are local X / Y).
THIN_LENS_POLARIZATIONS = {"LINEAR_X": "linear_u", "LINEAR_Y": "linear_v"}
#: Engine surface id of a polarization element's single plane, and of a neutral-density attenuator's.
POLARIZATION_SURFACE_ID = "element"
#: The retarder surface of a ``CIRCULAR_POLARIZER`` (behind its polarizer, ``POLARIZATION_SURFACE_ID``).
CIRCULAR_RETARDER_SURFACE_ID = "retarder"
NEUTRAL_DENSITY_SURFACE_ID = "attenuator"
#: Engine surface id of an aperture stop's plane and of an ideal thin lens's plane.
APERTURE_STOP_SURFACE_ID = "stop"
THIN_LENS_SURFACE_ID = "lens"
#: Engine surface id of a Faraday rotator's plane.
FARADAY_ROTATOR_SURFACE_ID = "rotator"
#: Engine surface id of a diffraction grating's plane.
GRATING_SURFACE_ID = "grating"
#: Engine surface id of a facet solid's facet surface; the run traces its planar patches as ``facets.t<k>`` (``k`` the patch's
#: first triangle, ``scene/lower.py``).
FACET_SURFACE_ID = "facets"
#: A compound member's Engine surface id is ``<member_id><MEMBER_SURFACE_SEPARATOR><surface_id>`` (Engine ids forbid ``/``).
MEMBER_SURFACE_SEPARATOR = "-"
#: Engine id of the surrounding medium (the published ``AIR``): the native lowering states it as the air of the Project's
#: environment (``environment_air``).
ENGINE_AIR = "air"
#: Engine id of a declared model's ``VACUUM`` gap: the Engine's built-in ``vacuum`` (index exactly 1).
ENGINE_VACUUM = "vacuum"
#: Engine ids of the media that are not a material of an element (air and vacuum).
ENGINE_SURROUNDINGS = frozenset({ENGINE_AIR, ENGINE_VACUUM})
#: The vacuum wavelengths (nm) the environment's air is defined over (the air formula's range; vacuum: the Engine's band).
AIR_BAND_NM = (180.0, 100000.0)
VACUUM_BAND_NM = (100.0, 100000.0)
#: Namespace of catalog materials in the Engine material library (never an Engine built-in id).
CATALOG_MATERIAL_PREFIX = "catalog:"
#: Namespace of the materials a Project declares (``ProjectModelMaterialV1``).
PROJECT_MATERIAL_PREFIX = "project:"
#: A crystal as one element cuts it is the Engine material ``project:<MATERIAL_ID><CRYSTAL_CUT_SEPARATOR><instance>[-<member>]``.
CRYSTAL_CUT_SEPARATOR = "@"
# The Engine's default ConstantCoating validity (contracts.materials.ConstantCoating): a compiled coating is defined over
# all of it, so the Engine never stops a beam on a coating range; validity.py judges the published range instead.
COATING_TABLE_MIN_NM = Decimal(100)
COATING_TABLE_MAX_NM = Decimal(20000)
COATING_TABLE_AOI_DEG = (0.0, 90.0)
FULL_TURN_RAD = 2.0 * math.pi
#: Degrees to radians (a polarization element's stated axis angles).
DEG_TO_RAD = math.pi / 180.0
FULL_TURN_DEG = 360.0
LOCAL_X = (1.0, 0.0, 0.0)
LOCAL_Y = (0.0, 1.0, 0.0)
LOCAL_Z = (0.0, 0.0, 1.0)
# A face polygon is an axis-aligned rectangle in a surface's (u, v) axes when its corners agree with the rectangle's to this
# (mm): published corners are exact decimals, their float images agree to ~1e-12.
OUTLINE_TOLERANCE_MM = 1e-9
#: Paraxial axes of ``paraxial_matrix``: a rotationally symmetric element (``None``) or one local transverse axis.
PARAXIAL_AXES = (None, "X", "Y")


def _number(value: str | Decimal) -> float:
    """The nearest binary64 of a published decimal (never through a libm function)."""

    return float(Decimal(value))


@dataclass(frozen=True)
class SurfaceEnvelope:
    """What one compiled Engine surface is characterized for (the published data ``validity.py`` checks).

    ``surface_id`` is the Engine surface id in the element (``S1`` .. or ``element``); ``traced`` the published
    ``TracedBranches`` value, or ``POLARIZATION_ELEMENT`` for a Jones element. ``coating`` is the published coating of a
    coated surface (its bands or spectrum, ``valid_aoi_deg`` and ``aoi_reference``); ``valid_aoi_deg`` is the chief-ray
    angle range the surface is characterized for (the coating's, or a polarization element's own), ``None`` for an
    uncoated interface.
    """

    surface_id: str
    traced: str
    coating: PublicCoatingV1 | None = None
    valid_aoi_deg: AngleRangeV1 | None = None

    @property
    def ideal_reflector(self) -> bool:
        """A reflecting surface that states no coating: compiled as the Engine's ideal reflector (R = 1)."""

        return self.traced == TracedBranches.REFLECTED.value and self.coating is None


#: Every model kind ``compile_model`` compiles (catalog and declared).
CompiledModel = (SurfaceSequenceModelV1 | SurfaceSetModelV1 | PolarizationElementModelV1 | ApertureStopModelV1 | IdealThinLensModelV1
                 | TabulatedThinLensModelV1 | FaradayRotatorModelV1 | DiffractionGratingModelV1 | CompoundModelV1 | NeutralDensityModelV1
                 | FacetSolidModelV1 | NonlinearCrystalModelV1 | JoinedBodiesModelV1)


@dataclass(frozen=True)
class ElementIdentity:
    """Who a compiled element is, apart from its physics: what checks, messages and views name it by.

    A catalog product: ``optical_sku``, its public ``category`` / ``form``, ``label`` = its display name, its published
    ``frame`` and ``design_geometry``. A declared model: ``optical_model_id``, ``label`` = its display name (else its model
    id), no category or form, its frame and design geometry (stated, or derived by ``declared_frame`` /
    ``declared_design_geometry``; ``None`` for a model of zero thickness without a stated body, and for a ``COMPOUND``
    unit's frame). ``member_id`` names a member of a ``COMPOUND`` unit.
    """

    label: str
    optical_sku: str | None = None
    optical_model_id: str | None = None
    category: OpticalCategory | None = None
    form: OpticalForm | None = None
    frame: OpticalFrameV1 | None = None
    design_geometry: DesignGeometryV1 | None = None
    member_id: str | None = None

    @classmethod
    def of_product(cls, product: PublicOpticalProductV2) -> ElementIdentity:
        return cls(label=product.display_name, optical_sku=product.optical_sku, category=product.category, form=product.form,
                   frame=product.frame, design_geometry=product.design_geometry)

    @property
    def is_catalog(self) -> bool:
        return self.optical_sku is not None

    @property
    def is_reflector(self) -> bool:
        """A face-normal frame (a reflector or plate splitter placed by its coated face)."""

        return self.frame is not None and self.frame.is_reflector

    @property
    def name(self) -> str:
        """The element's identity in messages: its SKU, or its model id (and member)."""

        name = self.optical_sku or self.optical_model_id or self.label
        return name if self.member_id is None else f"member {self.member_id} ({name})"


@dataclass(frozen=True)
class CompiledElement:
    """One optical model compiled to Engine physics, in its element frame.

    * ``identity``: an ``ElementIdentity`` (the catalog product, or the declared model, it was compiled from);
    * ``model``: the compiled model;
    * ``surfaces``: the Engine surfaces (``PlaneSurface`` / ``SphereSurface`` / ``SagSurface``) of the component, in model
      order (a ``COMPOUND`` unit: every member's surfaces placed in the unit frame, member after member);
    * ``materials``: the Engine material models the surfaces refer to (ids ``catalog:<MATERIAL_ID>`` or
      ``project:<MATERIAL_ID>``), sorted by id;
    * ``envelopes``: one ``SurfaceEnvelope`` per surface, same order;
    * ``solid``: a ``SURFACE_SET`` model's body in floats (``bodies.Solid``), ``None`` otherwise;
    * ``members``: a ``COMPOUND`` unit's members (a ``JOINED_BODIES`` model's bodies), each a ``CompiledElement`` in its own
      member frame (surface ids already ``<member_id>-<surface_id>``) with its ``member_pose`` in the unit frame; empty otherwise;
    * ``valid_band_nm``: the model's band (vacuum wavelengths it is characterized for); ``None`` for an aperture stop
      (wavelength independent) and a compound unit (each member has its own).
    """

    instance_id: str
    identity: ElementIdentity
    model: CompiledModel
    surfaces: tuple[PlaneSurface | SphereSurface | SagSurface, ...]
    materials: tuple[MaterialOpticalModel, ...]
    envelopes: tuple[SurfaceEnvelope, ...]
    solid: Solid | None = None
    members: tuple[CompiledElement, ...] = ()
    member_pose: PoseV1 | None = None
    material_namespace: str = CATALOG_MATERIAL_PREFIX
    crystal_media: tuple[tuple[str, str], ...] = ()
    #: the declared model kind when the element is compiled as another kind's physics (a ``NONLINEAR_CRYSTAL``: its slab)
    declared_kind: str | None = None
    #: the Engine material ``air`` of the environment the element was compiled for (``environment_air``; ``None``: air at
    #: 20 C and 101325 Pa): what ``paraxial_matrix`` takes the surrounding medium to be
    air: MaterialOpticalModel | None = None
    #: the design-point tolerances the element was compiled under (``design_point.py``): its coatings' declared validity in
    #: the Engine request carries them, and ``validity.py`` judges the element's single design points by the same values
    design_point: DesignPointTolerances = DEFAULT_DESIGN_POINT

    @property
    def optical_sku(self) -> str | None:
        return self.identity.optical_sku

    @property
    def optical_model_id(self) -> str | None:
        return self.identity.optical_model_id

    @property
    def member_id(self) -> str | None:
        return self.identity.member_id

    @property
    def label(self) -> str:
        return self.identity.label

    @property
    def model_kind(self) -> str:
        return self.model.model_kind

    @property
    def stated_kind(self) -> str:
        """The model kind as the Project or catalog states it (``model_kind`` is the kind of the physics compiled)."""
        return self.declared_kind or self.model.model_kind

    @property
    def valid_band_nm(self) -> WavelengthBandV1 | None:
        return getattr(self.model, "valid_band_nm", None)

    @property
    def is_compound(self) -> bool:
        """A unit of members: a declared ``COMPOUND`` unit or a catalog ``JOINED_BODIES`` model (its bodies)."""

        return isinstance(self.model, (CompoundModelV1, JoinedBodiesModelV1))

    def bodies(self) -> tuple[CompiledElement, ...]:
        """The optical bodies the element is made of: a compound unit's members, else the element itself."""

        return self.members if self.is_compound else (self,)

    def engine_material_id(self, material_id: str) -> str:
        """The Engine id of one of the model's media (``air`` for ``AIR``; a crystal: the crystal as this element cuts it)."""

        crystal = dict(self.crystal_media).get(material_id)
        if crystal is not None:
            return crystal
        return surrounding_id(material_id) or f"{self.material_namespace}{material_id}"

    def engine_surface_id(self, surface_id: str) -> str:
        """The scene-wide Engine id of one of this element's surfaces (``<instance_id>/<surface_id>``)."""

        return f"{self.instance_id}/{surface_id}"

    def envelope_by_engine_id(self) -> dict[str, SurfaceEnvelope]:
        return {self.engine_surface_id(e.surface_id): e for e in self.envelopes}


class ModelResolver:
    """Where a model's media and coatings (and a compound unit's member models) come from, and the Engine namespace of its
    materials. ``CatalogResolver``: a catalog product's; ``ProjectResolver``: a declared model's."""

    namespace = CATALOG_MATERIAL_PREFIX
    #: The error a reference that does not resolve raises, and how the source is named in its message.
    missing_code = LocalRunErrorCode.CATALOG_INVALID
    missing_text = "is not published"
    #: The medium the optics operate in: its temperature is the materials' (``engine_material``).
    environment: PropagationEnvironmentV1 = DEFAULT_ENVIRONMENT
    #: The design-point tolerances the models compile under (the Engine defaults unless the lowering passes the Project's).
    design_point: DesignPointTolerances = DEFAULT_DESIGN_POINT

    def material(self, material_id: str) -> PublicMaterialV1 | ProjectModelMaterialV1 | None:
        raise LookupError(material_id)

    def coating(self, coating_id: str) -> PublicCoatingV1 | None:
        raise LookupError(coating_id)

    def model(self, model_id: str) -> ProjectOpticalModelV1 | None:
        """A declared model (a compound unit's member); a catalog has none."""

        return None

    def engine_material_id(self, material_id: str) -> str:
        """The Engine id of a medium: ``air`` for ``AIR``, else ``<namespace><MATERIAL_ID>``."""

        return surrounding_id(material_id) or f"{self.namespace}{material_id}"

    def engine_material(self, material: PublicMaterialV1 | ProjectModelMaterialV1) -> MaterialOpticalModel:
        """The Engine model of one of the model's media (``engine_material`` in this resolver's namespace, at the environment's
        temperature)."""

        return engine_material(material, self.namespace, temperature_c=_number(self.environment.temperature_c))

    def crystal_media(self) -> tuple[tuple[str, str], ...]:
        """``(material id, Engine id)`` of every crystal medium this resolver orients (none here)."""

        return ()


class CatalogResolver(ModelResolver):
    """Media and coatings of a catalog product: the catalog's own (``catalog:<MATERIAL_ID>``)."""

    def __init__(self, catalog: PublicOpticalCatalogV2, environment: PropagationEnvironmentV1 | None = None, *,
                 design_point: DesignPointTolerances = DEFAULT_DESIGN_POINT) -> None:
        self.catalog = catalog
        self.environment = environment or DEFAULT_ENVIRONMENT
        self.design_point = design_point

    def material(self, material_id: str) -> PublicMaterialV1 | None:
        return self.catalog.material(material_id)

    def coating(self, coating_id: str) -> PublicCoatingV1 | None:
        return self.catalog.coating(coating_id)


class ProjectResolver(ModelResolver):
    """Media, coatings and member models of a declared model: the Project's ``model_materials``, ``model_coatings`` and
    ``optical_models`` only (``project:<MATERIAL_ID>``); never the catalog."""

    namespace = PROJECT_MATERIAL_PREFIX
    missing_code = LocalRunErrorCode.PROJECT_INVALID
    missing_text = "is not declared in the Project"

    def __init__(self, project, design_point: DesignPointTolerances = DEFAULT_DESIGN_POINT) -> None:
        self.environment = getattr(project, "effective_environment", None) or DEFAULT_ENVIRONMENT
        self.design_point = design_point
        self.materials = {m.material_id: m for m in project.model_materials or ()}
        self.coatings = {c.coating_id: c for c in project.model_coatings or ()}
        self.models = {m.model_id: m for m in project.optical_models or ()}

    def material(self, material_id: str) -> ProjectModelMaterialV1 | None:
        return self.materials.get(material_id)

    def coating(self, coating_id: str) -> PublicCoatingV1 | None:
        return self.coatings.get(coating_id)

    def model(self, model_id: str) -> ProjectOpticalModelV1 | None:
        return self.models.get(model_id)


class CrystalCutResolver(ModelResolver):
    """A declared model's resolver that orients its crystals: the Project's media, coatings and models (``base``), with every
    crystal medium the model states an optic axis for (``ProjectOpticalModelV1.optic_axes``) becoming the Engine
    ``UniaxialMaterial`` ``project:<MATERIAL_ID>@<cut>`` -- ``cut`` the component (``<instance>``, or ``<instance>-<member>``
    for a compound member) -- with the axis in the element frame (``optic_axis_component`` = the component, so the axis turns
    with its pose; a compound member's axis is turned into the unit frame by ``_compound``). One crystal material cut two
    ways in two elements is two Engine materials."""

    def __init__(self, base: ModelResolver, axes: tuple[CrystalOpticAxisV1, ...], component: str, cut: str,
                 gradients: dict[str, tuple[float, float]] | None = None) -> None:
        self.base, self.component, self.cut = base, component, cut
        self.namespace, self.missing_code, self.missing_text = base.namespace, base.missing_code, base.missing_text
        self.environment = base.environment
        self.design_point = base.design_point
        self.axes = {axis.material_id: axis for axis in axes}
        # gradient-index media of the model with the rod's radius and its entrance on local Z (``gradient_rod``): each becomes
        # the Engine GradientIndexMaterial / AxialGradientIndexMaterial project:<MATERIAL_ID>@<cut> about the element's local Z axis
        self.gradients = dict(gradients or {})

    def material(self, material_id: str) -> PublicMaterialV1 | ProjectModelMaterialV1 | None:
        return self.base.material(material_id)

    def coating(self, coating_id: str) -> PublicCoatingV1 | None:
        return self.base.coating(coating_id)

    def model(self, model_id: str) -> ProjectOpticalModelV1 | None:
        return self.base.model(model_id)

    def engine_material_id(self, material_id: str) -> str:
        if material_id in self.axes or material_id in self.gradients:
            return f"{self.namespace}{material_id}{CRYSTAL_CUT_SEPARATOR}{self.cut}"
        return self.base.engine_material_id(material_id)

    def engine_material(self, material: PublicMaterialV1 | ProjectModelMaterialV1) -> MaterialOpticalModel:
        rod = self.gradients.get(material.material_id)
        if rod is not None:
            return gradient_material(material, self.engine_material_id(material.material_id), rod[0], self.component,  # type: ignore[arg-type]
                                     entrance_z_mm=rod[1])
        axis = self.axes.get(material.material_id)
        if axis is None:
            return self.base.engine_material(material)
        return crystal_material(material, self.engine_material_id(material.material_id), axis, self.component)  # type: ignore[arg-type]

    def crystal_media(self) -> tuple[tuple[str, str], ...]:
        return tuple((m, self.engine_material_id(m)) for m in sorted({*self.axes, *self.gradients}))


def _crystal_physics_problem(material: Any, what: str) -> None:
    """A crystal or gradient-index medium is lowered with the laws the Engine models for it (constant, Sellmeier, tabulated, in
    the Engine's convention): refuse anything else rather than evaluate it under another convention."""

    laws = [material.dispersion] + ([material.birefringence.extraordinary] if getattr(material, "birefringence", None) is not None else [])
    reasons = []
    if getattr(material, "index_reference", None) is not None:
        reasons.append("an index_reference")
    if getattr(material, "thermo_optic", None) is not None:
        reasons.append("thermo_optic data")
    reasons += [f"a {law.kind} law" for law in laws
                if not isinstance(law, (ConstantIndexDispersionV1, SellmeierDispersionV1, TabulatedDispersionV1))]
    for law in laws:
        if isinstance(law, TabulatedDispersionV1) and law.origin in {"MEASURED", "SOURCE_TABLE"}:
            reasons.append("a measured index table" if law.measured else "a source index table")
            break
    if reasons:
        raise _problem(LocalRunErrorCode.MATERIALS_UNSUPPORTED,
                       f"material {material.material_id}: a {what} is modelled with constant, Sellmeier or tabulated laws on "
                       f"vacuum wavelengths (absolute index); it states {', '.join(reasons)}", f"material {material.material_id}")


def _index_law(dispersion) -> IndexLaw:
    """One index of a declared crystal as an Engine index law (the same numbers as an isotropic material's)."""

    if isinstance(dispersion, ConstantIndexDispersionV1):
        return ConstantIndexLaw(refractive_index=_number(dispersion.refractive_index))
    if isinstance(dispersion, SellmeierDispersionV1):
        return SellmeierIndexLaw(b=tuple(_number(v) for v in dispersion.b),  # type: ignore[arg-type]
                                 c_um2=tuple(_number(v) for v in dispersion.c_um2))  # type: ignore[arg-type]
    return TabulatedIndexLaw(wavelength_nm=tuple(_number(v) for v in dispersion.wavelength_nm),
                             refractive_index=tuple(_number(v) for v in dispersion.refractive_index), interpolation="pchip")


def crystal_material(material: ProjectModelMaterialV1 | PublicMaterialV1, engine_id: str, axis: CrystalOpticAxisV1, component: str
                     ) -> UniaxialMaterial:
    """The Engine ``UniaxialMaterial`` of a declared (or published) crystal as one element cuts it: ordinary index ``dispersion``,
    extraordinary index ``birefringence.extraordinary``, valid on exactly the material's band, its bulk absorption, and the
    optic axis (normalized in exact decimal arithmetic) in the frame of ``component``."""

    assert material.birefringence is not None  # the contract gives an optic axis to crystals only
    _crystal_physics_problem(material, "uniaxial crystal")
    absorption = getattr(material, "absorption_coefficient_per_mm", None)  # (a published crystal states none)
    extra: dict[str, Any] = {} if absorption is None else {"absorption_coefficient_per_mm": _number(absorption)}
    if getattr(material, "bulk_loss", None) is not None:
        extra["bulk_loss"] = engine_bulk_loss(material.bulk_loss)
    return UniaxialMaterial(
        material_id=engine_id, valid_wavelength_nm=(_number(material.valid_band_nm.min_nm), _number(material.valid_band_nm.max_nm)),
        ordinary=_index_law(material.dispersion), extraordinary=_index_law(material.birefringence.extraordinary),
        optic_axis=tuple(_number(c) for c in axis.unit()), optic_axis_component=component, **extra,  # type: ignore[arg-type]
    )


def gradient_material(material: ProjectModelMaterialV1, engine_id: str, radius_mm: float, component: str, *,
                      entrance_z_mm: float = 0.0) -> GradientIndexMaterial | AxialGradientIndexMaterial:
    """The Engine ``GradientIndexMaterial`` of a declared radial gradient-index material in one element: the index on the axis
    ``dispersion``, the gradient constant (constant or rows), valid on exactly the material's band, its bulk absorption and
    scattering, the axis the element's local Z axis (``axis_component`` = the component) and the rod's radius ``radius_mm``.
    A declared axial one (``axial_gradient``) is the Engine ``AxialGradientIndexMaterial``: the index at the entrance
    ``dispersion``, its terms, the axis point ``(0, 0, entrance_z_mm)`` (``z = 0``) on local Z, the rod's radius."""

    _crystal_physics_problem(material, "gradient-index medium")
    extra: dict[str, Any] = {}
    for key in ("absorption_coefficient_per_mm", "scattering_coefficient_per_mm", "anisotropy_g"):
        value = getattr(material, key, None)
        if value is not None:
            extra[key] = _number(value)
    if material.bulk_loss is not None:
        extra["bulk_loss"] = engine_bulk_loss(material.bulk_loss)
    band = (_number(material.valid_band_nm.min_nm), _number(material.valid_band_nm.max_nm))
    if material.axial_gradient is not None:
        return AxialGradientIndexMaterial(
            material_id=engine_id, valid_wavelength_nm=band, base=_index_law(material.dispersion),
            coefficients_per_mm=tuple(_number(t) for t in material.axial_gradient.index_terms),
            axis_point_mm=(0.0, 0.0, entrance_z_mm), axis_direction=LOCAL_Z, axis_component=component, radius_mm=radius_mm,
            **extra,  # type: ignore[arg-type]
        )
    assert material.gradient is not None  # (compile_declared passes gradient materials only)
    law = material.gradient
    if law.gradient_constant_per_mm is not None:
        gradient = ConstantGradient(per_mm=_number(law.gradient_constant_per_mm))
    else:
        rows = law.gradient_rows or ()
        gradient = TabulatedGradient(wavelength_nm=tuple(_number(r.wavelength_nm) for r in rows),
                                     per_mm=tuple(_number(r.gradient_constant_per_mm) for r in rows))
    return GradientIndexMaterial(
        material_id=engine_id, valid_wavelength_nm=(_number(material.valid_band_nm.min_nm), _number(material.valid_band_nm.max_nm)),
        base=_index_law(material.dispersion), gradient=gradient, axis_point_mm=(0.0, 0.0, 0.0), axis_direction=LOCAL_Z,
        axis_component=component, radius_mm=radius_mm, **extra,  # type: ignore[arg-type]
    )


def surrounding_id(material_id: str) -> str | None:
    """The Engine id of a reserved medium: ``air`` for ``AIR`` (the environment's air), ``vacuum`` for ``VACUUM``; else
    ``None``."""

    if material_id == AIR:
        return ENGINE_AIR
    if material_id == VACUUM:
        return ENGINE_VACUUM
    return None


def environment_air(environment: PropagationEnvironmentV1 | None = None) -> MaterialOpticalModel:
    """The Engine material ``air`` of a Project's environment (``DEFAULT_ENVIRONMENT`` when ``None``): an ``AirMaterial`` at
    its temperature and pressure (``air_index``), defined over ``AIR_BAND_NM``; at zero pressure the air is vacuum, the
    constant index 1 over ``VACUUM_BAND_NM`` (the Engine's built-in ``air``)."""

    environment = environment or DEFAULT_ENVIRONMENT
    if environment.is_vacuum:
        return ConstantIndexMaterial(material_id=ENGINE_AIR, valid_wavelength_nm=VACUUM_BAND_NM, refractive_index=1.0,
                                     absorption_coefficient_per_mm=0.0)  # evacuated space is explicitly lossless
    return AirMaterial(material_id=ENGINE_AIR, valid_wavelength_nm=AIR_BAND_NM, temperature_c=_number(environment.temperature_c),
                       pressure_pa=_number(environment.pressure_pa))


def engine_index_reference(reference: IndexReferenceV1 | None) -> IndexReference | None:
    """The Engine image of a law's ``index_reference`` (``None``: the Engine's convention)."""

    if reference is None:
        return None
    air = reference.reference_air
    return IndexReference(
        wavelength={"VACUUM": "vacuum", "AIR": "air", "NOT_STATED": "not_stated"}[reference.wavelength],  # type: ignore[arg-type]
        index={"ABSOLUTE": "absolute", "RELATIVE_TO_AIR": "relative_to_air", "NOT_STATED": "not_stated"}[reference.index],  # type: ignore[arg-type]
        reference_air_temperature_c=None if air is None else _number(air.temperature_c),
        reference_air_pressure_pa=None if air is None else _number(air.pressure_pa))


def engine_bulk_loss(loss: Any) -> ExtinctionCoefficientLoss | InternalTransmittanceLoss:
    """The Engine image of a material's ``bulk_loss`` (rows on the law's wavelength, as published)."""

    waves = tuple(_number(w) for w in loss.wavelength_nm)
    if isinstance(loss, ExtinctionCoefficientLossV1):
        return ExtinctionCoefficientLoss(wavelength_nm=waves, k=tuple(_number(k) for k in loss.k))
    return InternalTransmittanceLoss(wavelength_nm=waves, internal_transmittance=tuple(_number(t) for t in loss.internal_transmittance),
                                     thickness_mm=tuple(_number(d) for d in loss.thickness_mm))


def engine_thermo_optic(thermo: SchottThermoOpticV1 | None) -> SchottThermoOptic | None:
    if thermo is None:
        return None
    valid = thermo.valid_temperature_c
    return SchottThermoOptic(d0=_number(thermo.d0), d1=_number(thermo.d1), d2=_number(thermo.d2), e0=_number(thermo.e0),
                             e1=_number(thermo.e1), lambda_tk_um=_number(thermo.lambda_tk_um),
                             reference_temperature_c=_number(thermo.reference_temperature_c),
                             valid_temperature_c=None if valid is None else (_number(valid[0]), _number(valid[1])))


def engine_material_id(material_id: str) -> str:
    """The Engine material id of a published medium: ``air`` for ``AIR``, else ``catalog:<MATERIAL_ID>``."""

    return surrounding_id(material_id) or f"{CATALOG_MATERIAL_PREFIX}{material_id}"


def engine_material(material: PublicMaterialV1 | ProjectModelMaterialV1, namespace: str = CATALOG_MATERIAL_PREFIX, *,
                    temperature_c: float | None = None) -> MaterialOpticalModel:
    """The Engine model of a material (a published one, or one the Project declares when ``namespace`` is
    ``project:``), valid on exactly its band (never extrapolated), with its bulk absorption when it states one: a constant
    index, a Sellmeier law (1 to 10 terms), a power series, a Herzberger or a temperature Sellmeier law, or a PCHIP table
    (carrying its measured or source-table origin), with the material's ``index_reference`` (how its law is written: the Engine
    evaluates it at the law's wavelength of each vacuum wavelength), ``bulk_loss`` (spectral) and ``thermo_optic``.
    ``temperature_c`` (the environment's) becomes the Engine material's temperature where the index depends on it (thermo
    data or a temperature law); otherwise the material is evaluated as its source states it."""

    band = (_number(material.valid_band_nm.min_nm), _number(material.valid_band_nm.max_nm))
    material_id = f"{namespace}{material.material_id}"
    absorption = getattr(material, "absorption_coefficient_per_mm", None)
    extra: dict[str, Any] = {} if absorption is None else {"absorption_coefficient_per_mm": _number(absorption)}
    for key in ("scattering_coefficient_per_mm", "anisotropy_g"):  # (declared materials only: bulk scattering)
        value = getattr(material, key, None)
        if value is not None:
            extra[key] = _number(value)
    reference = engine_index_reference(getattr(material, "index_reference", None))
    if reference is not None:
        extra["index_reference"] = reference
    if getattr(material, "bulk_loss", None) is not None:
        extra["bulk_loss"] = engine_bulk_loss(material.bulk_loss)
    thermo = engine_thermo_optic(getattr(material, "thermo_optic", None))
    if thermo is not None:
        extra["thermo_optic"] = thermo
    dispersion = material.dispersion
    if temperature_c is not None and (thermo is not None or isinstance(dispersion, TemperatureSellmeierDispersionV1)):
        extra["temperature_c"] = temperature_c
    common = {"material_id": material_id, "valid_wavelength_nm": band, **extra}
    if isinstance(dispersion, ConstantIndexDispersionV1):
        return ConstantIndexMaterial(refractive_index=_number(dispersion.refractive_index), **common)
    if isinstance(dispersion, SellmeierDispersionV1):
        return SellmeierMaterial(b=tuple(_number(v) for v in dispersion.b), c_um2=tuple(_number(v) for v in dispersion.c_um2), **common)
    if isinstance(dispersion, PowerSeriesDispersionV1):
        return PowerSeriesMaterial(coefficients=tuple(_number(v) for v in dispersion.coefficients), exponents=tuple(dispersion.exponents),
                                   **common)
    if isinstance(dispersion, HerzbergerDispersionV1):
        return HerzbergerMaterial(coefficients=tuple(_number(v) for v in dispersion.coefficients), **common)  # type: ignore[arg-type]
    if isinstance(dispersion, TemperatureSellmeierDispersionV1):
        return TemperatureSellmeierMaterial(
            s_coefficients=tuple(tuple(_number(v) for v in row) for row in dispersion.s_coefficients),
            pole_coefficients_um=tuple(tuple(_number(v) for v in row) for row in dispersion.pole_coefficients_um),
            temperature_k_range=(_number(dispersion.temperature_k_range[0]), _number(dispersion.temperature_k_range[1])),
            reference_temperature_k=_number(dispersion.reference_temperature_k), **common)
    if dispersion.measured:
        common["origin"] = "measured"
    table_type = SourceTableIndexMaterial if dispersion.origin == "SOURCE_TABLE" else TabulatedIndexMaterial
    return table_type(
        wavelength_nm=tuple(_number(v) for v in dispersion.wavelength_nm),
        refractive_index=tuple(_number(v) for v in dispersion.refractive_index), interpolation="pchip", **common,
    )

# (wavelength, R_s, R_p, T_s, T_p)
_Node = tuple[Decimal, float, float, float, float]


def _band_values(band) -> tuple[float, float, float, float]:
    if band.polarization_resolved:
        return _number(band.reflectance_s), _number(band.reflectance_p), _number(band.transmittance_s), _number(band.transmittance_p)
    r, t = _number(band.reflectance), _number(band.transmittance)
    return r, r, t, t


def _coating_nodes(coating: PublicCoatingV1) -> list[_Node]:
    if coating.spectrum is not None:
        spectrum = coating.spectrum
        nodes = []
        if spectrum.polarization_resolved:
            for w, r_s, r_p, t_s, t_p in zip(spectrum.wavelength_nm, spectrum.reflectance_s, spectrum.reflectance_p,  # type: ignore[arg-type]
                                             spectrum.transmittance_s, spectrum.transmittance_p, strict=True):  # type: ignore[arg-type]
                nodes.append((Decimal(w), _number(r_s), _number(r_p), _number(t_s), _number(t_p)))
            return nodes
        for w, r, t in zip(spectrum.wavelength_nm, spectrum.reflectance, spectrum.transmittance, strict=True):
            r_value, t_value = _number(r), _number(t)
            nodes.append((Decimal(w), r_value, r_value, t_value, t_value))
        return nodes
    nodes = []
    for band in coating.bands or ():
        values = _band_values(band)
        nodes.append((Decimal(band.min_nm), *values))
        if Decimal(band.max_nm) != Decimal(band.min_nm):
            nodes.append((Decimal(band.max_nm), *values))
    return nodes


def _held_flat(nodes: list[Decimal], low: Decimal, high: Decimal) -> tuple[list[Decimal], list[int]]:
    """``nodes`` extended to ``low`` and ``high`` when they do not reach them, and the index of the node whose values each
    extended node takes (the outermost data, held flat)."""

    nodes, index = list(nodes), list(range(len(nodes)))
    if nodes[0] > low:
        nodes.insert(0, low)
        index.insert(0, 0)
    if nodes[-1] < high:
        nodes.append(high)
        index.append(index[-1])
    return nodes, index


def _declared_validity(coating: PublicCoatingV1, design_point: DesignPointTolerances) -> DeclaredCoatingValidity:
    """The record's angle range and its reference medium, with the allowance ``validity.py`` reads the range with: the
    coating kind's tolerance of a single design angle (``design_point``), ``AOI_TOLERANCE_DEG`` of any range; and its
    wavelengths, a single wavelength with the tolerance it is matched within (``validation/coating_use.py`` evaluates the
    light the trace leaves out against them)."""

    from .validity import AOI_TOLERANCE_DEG, FILTER_COATING_KINDS, coating_angle_tolerance_deg  # (validity.py imports this module)

    angles = coating.valid_aoi_deg
    tolerance = coating_angle_tolerance_deg(coating, angles, design_point)
    if coating.spectrum is not None or coating.angular_spectrum is not None:
        bands = [coating.span_nm]
    else:
        bands = [(Decimal(band.min_nm), Decimal(band.max_nm)) for band in coating.bands or ()]
    relative = Decimal(0) if coating.kind in FILTER_COATING_KINDS else design_point.wavelength_rel
    return DeclaredCoatingValidity(record_id=coating.coating_id, aoi_reference=coating.aoi_reference.value, min_deg=_number(angles.min_deg),
                                   max_deg=_number(angles.max_deg), slack_deg=AOI_TOLERANCE_DEG + tolerance,
                                   wavelength_bands_nm=tuple((_number(low - relative * low), _number(high + relative * high)) if low == high
                                                             else (_number(low), _number(high)) for low, high in bands))


def _angular_coating(table: CoatingAngularSpectrumV1, group_delay_bound_fs: float | None = None,
                     declared_validity: DeclaredCoatingValidity | None = None) -> TabulatedCoating:
    """An angle-resolved table on its own (wavelength, AOI) grid, held flat beyond its outermost rows to 100 and 20000 nm
    and beyond its outermost angles to 0 and 90 deg (module docstring)."""

    wavelengths, rows = _held_flat([Decimal(w) for w in table.wavelength_nm], COATING_TABLE_MIN_NM, COATING_TABLE_MAX_NM)
    angles, columns = _held_flat([Decimal(a) for a in table.aoi_deg], *(Decimal(repr(a)) for a in COATING_TABLE_AOI_DEG))

    def grid(values: tuple[tuple[str, ...], ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(tuple(_number(values[i][j]) for j in columns) for i in rows)

    return TabulatedCoating(
        wavelength_nm=tuple(_number(w) for w in wavelengths), aoi_deg=tuple(_number(a) for a in angles),
        reflectance_s=grid(table.reflectance_s), reflectance_p=grid(table.reflectance_p),
        transmittance_s=grid(table.transmittance_s), transmittance_p=grid(table.transmittance_p),
        group_delay_bound_fs=group_delay_bound_fs, declared_validity=declared_validity,
    )


_PHASE_NAMES = ("reflection_s_rad", "reflection_p_rad", "transmission_s_rad", "transmission_p_rad")


def engine_phases(coating: PublicCoatingV1 | None) -> InterfacePhases | None:
    """The Engine phases (``InterfacePhases``) of a coating's stated ``phase``, or None without one (module docstring):
    constant phases one value each; a wavelength table on its own nodes, held flat beyond them to 100 and 20000 nm."""

    phase = coating.phase if coating is not None else None
    if phase is None:
        return None
    stated = [name for name in _PHASE_NAMES if getattr(phase, name) is not None]
    if phase.wavelength_nm is None:
        return InterfacePhases(**{name: (_number(getattr(phase, name)),) for name in stated})
    wavelengths, index = _held_flat([Decimal(w) for w in phase.wavelength_nm], COATING_TABLE_MIN_NM, COATING_TABLE_MAX_NM)
    return InterfacePhases(wavelength_nm=tuple(_number(w) for w in wavelengths),
                           **{name: tuple(_number(getattr(phase, name)[i]) for i in index) for name in stated})


def engine_coating(coating: PublicCoatingV1, design_point: DesignPointTolerances = DEFAULT_DESIGN_POINT) -> ConstantCoating | TabulatedCoating:
    """The Engine coating of a published coating (module docstring): constant for one band, else an AOI-independent table
    (band edges or spectrum nodes), per polarization, flat beyond the data to the Engine's default 100-20000 nm range; an
    angle-resolved table on its own (wavelength, AOI) grid, also held flat out to 0 and 90 deg. ``design_point``: the
    tolerances its single design angle and wavelength are declared with (``DeclaredCoatingValidity``)."""

    bound = _number(coating.group_delay_bound_fs) if coating.group_delay_bound_fs is not None else None
    declared = _declared_validity(coating, design_point)
    if coating.angular_spectrum is not None:
        return _angular_coating(coating.angular_spectrum, bound, declared)
    bands = coating.bands or ()
    if coating.spectrum is None and len(bands) == 1:
        r_s, r_p, t_s, t_p = _band_values(bands[0])
        return ConstantCoating(reflectance_s=r_s, reflectance_p=r_p, transmittance_s=t_s, transmittance_p=t_p,
                               group_delay_bound_fs=bound, declared_validity=declared)
    nodes = _coating_nodes(coating)
    if nodes[0][0] > COATING_TABLE_MIN_NM:
        nodes.insert(0, (COATING_TABLE_MIN_NM, *nodes[0][1:]))
    if nodes[-1][0] < COATING_TABLE_MAX_NM:
        nodes.append((COATING_TABLE_MAX_NM, *nodes[-1][1:]))

    def rows(k: int) -> tuple[tuple[float, float], ...]:
        return tuple((node[k], node[k]) for node in nodes)  # type: ignore[misc]

    return TabulatedCoating(
        wavelength_nm=tuple(_number(node[0]) for node in nodes), aoi_deg=COATING_TABLE_AOI_DEG,
        reflectance_s=rows(1), reflectance_p=rows(2), transmittance_s=rows(3), transmittance_p=rows(4),
        group_delay_bound_fs=bound, declared_validity=declared,
    )


def _media(model: SurfaceSequenceModelV1, index: int) -> tuple[str, str]:
    """(medium before, medium after) surface ``index`` along +Z, as published ids."""

    before = AIR if index == 0 else model.media[index - 1]
    after = AIR if index == len(model.surfaces) - 1 else model.media[index]
    return before, after


def _problem(code: LocalRunErrorCode, message: str, where: str) -> LocalRunError:
    return LocalRunError([LocalRunProblem(code, message, where)])


def _coating(resolver: ModelResolver, name: str, surface: PrescriptionSurfaceV1 | PosedSurfaceV1, where: str) -> PublicCoatingV1 | None:
    if surface.coating_id is None:
        return None
    coating = resolver.coating(surface.coating_id)
    if coating is None:
        raise _problem(resolver.missing_code, f"{name} surface {surface.surface_id}: coating {surface.coating_id} {resolver.missing_text}", where)
    return coating


def _interaction(surface: PrescriptionSurfaceV1 | PosedSurfaceV1, coating: PublicCoatingV1 | None, before: str, after: str, sku: str,
                 where: str, *, reflective_side: str = "positive", design_point: DesignPointTolerances = DEFAULT_DESIGN_POINT):
    compiled = engine_coating(coating, design_point) if coating is not None else None
    phases = engine_phases(coating)
    scatter = _scatter(surface.scatter)
    if surface.traced is TracedBranches.TRANSMITTED:
        return RefractInteraction(coating=compiled, trace_fresnel_reflection=False, phases=phases, scatter=scatter)
    if surface.traced is TracedBranches.TRANSMITTED_AND_REFLECTED and before != after:
        return RefractInteraction(coating=compiled, trace_fresnel_reflection=True, phases=phases, scatter=scatter)
    if surface.traced is TracedBranches.REFLECTED:
        # (no coating stated: the Engine's ideal reflector, R = 1 with the reflection phases pi / 0 -- module docstring)
        return MirrorInteraction(reflective_side=reflective_side, coating=compiled, phases=phases, scatter=scatter)  # type: ignore[arg-type]
    if compiled is None:
        raise _problem(
            LocalRunErrorCode.SURFACE_COATING_REQUIRED,
            f"{sku} surface {surface.surface_id} is traced {surface.traced.value} with the same medium on both sides but states "
            "no coating: a splitting surface has no split without one. State its coating (reflectance and transmittance), or "
            "trace it REFLECTED for an ideal reflector",
            where,
        )
    if scatter is not None:
        raise _problem(
            LocalRunErrorCode.SIMULATION_MODEL_UNSUPPORTED,
            f"{sku} surface {surface.surface_id} splits the beam into two traced branches with the same medium on both sides: a "
            "splitting surface does not scatter (state the scatter on a refracting or reflecting surface)", where)
    return BeamSplitterInteraction(coating=compiled, phases=phases)


def _scatter(spec) -> SurfaceScatter | None:
    """The Engine scatter of a surface's ``SurfaceScatterV1`` (``None``: a smooth surface)."""

    if spec is None:
        return None
    extra: dict = {}
    if spec.exponent is not None:
        extra["exponent"] = _number(spec.exponent)
    if spec.sample_count is not None:
        extra["sample_count"] = spec.sample_count
    if spec.seed is not None:
        extra["seed"] = spec.seed
    return SurfaceScatter(bsdf=spec.bsdf, fraction=_number(spec.fraction), side=spec.side.lower(), **extra)


# ------------------------------------------------------------------------------------------- apertures / outlines


def _engine_uv(surface: PrescriptionSurfaceV1 | PosedSurfaceV1, x: float, y: float) -> tuple[float, float]:
    """In-surface coordinates of the Engine surface for the catalog surface's local ``(x, y)``: unchanged, except for a
    cylinder straight along X, whose Engine ``u`` axis (the curved one) is the catalog's Y (``(x, y) -> (y, -x)``)."""

    return (y, -x) if surface.cylinder_axis == "X" else (x, y)


def _shaped(shape, centre: tuple[float, float], radius: str | None, half_widths, swap: bool) -> EngineAperture:
    """A ``ClearShapeV1`` (within its circle ``radius`` or rectangle ``half_widths``) as an Engine aperture about ``centre``
    (Engine u, v); ``swap``: a cylinder straight along X, whose Engine axes are the catalog's ``(y, -x)``."""

    if shape.kind == "ELLIPSE":
        hx, hy = (_number(v) for v in half_widths)
        return EllipticalAperture(semi_axes_mm=(hy, hx) if swap else (hx, hy), center_offset_mm=centre)
    if shape.kind == "D_SHAPE":
        ax, ay = (int(v) for v in shape.flat_axis())
        ax, ay = (ay, -ax) if swap else (ax, ay)
        angle = FLAT_SIDE_ANGLES_DEG[(ax, ay)]
        return DShapedAperture(radius_mm=_number(radius), flat_distance_mm=_number(shape.flat_mm), flat_angle_deg=angle,  # type: ignore[arg-type]
                               center_offset_mm=centre)
    corners = tuple((_number(y), -_number(x)) if swap else (_number(x), _number(y)) for x, y in shape.vertices_mm)
    return PolygonAperture(vertices_mm=corners, center_offset_mm=centre)


def clear_shape_region(shape, centre: tuple[float, float], radius: str | None, half_widths) -> ApertureG:
    """A ``ClearShapeV1`` (within its circle or rectangle, about ``centre``) as the region ``optics/apertures.py`` answers
    about, in the surface's local X / Y."""

    return lower_aperture(_shaped(shape, centre, radius, half_widths, False))


def hole_region(hole) -> ApertureG:
    """A ``SurfaceHoleV1`` as a region in its surface's local X / Y."""

    centre = (_number(hole.center_mm[0]), _number(hole.center_mm[1]))
    if hole.shape is not None:
        return clear_shape_region(hole.shape, centre, hole.radius_mm, hole.half_widths_mm)
    if hole.radius_mm is not None:
        return ApertureG("circle", _number(hole.radius_mm), 0.0, centre[0], centre[1])
    return ApertureG("rectangle", _number(hole.half_widths_mm[0]), _number(hole.half_widths_mm[1]), centre[0], centre[1])


def _engine_holes(surface: PrescriptionSurfaceV1 | PosedSurfaceV1) -> tuple[EngineAperture, ...] | None:
    """A surface's holes as Engine apertures in its Engine (u, v) axes (``None``: no hole)."""

    if not surface.holes:
        return None
    swap = surface.cylinder_axis == "X"
    out: list[EngineAperture] = []
    for hole in surface.holes:
        centre = _engine_uv(surface, _number(hole.center_mm[0]), _number(hole.center_mm[1]))
        if hole.shape is not None:
            out.append(_shaped(hole.shape, centre, hole.radius_mm, hole.half_widths_mm, swap))
        elif hole.radius_mm is not None:
            out.append(CircularAperture(radius_mm=_number(hole.radius_mm), center_offset_mm=centre))
        else:
            hx, hy = (_number(v) for v in hole.half_widths_mm)  # type: ignore[union-attr]
            half_u, half_v = (hy, hx) if swap else (hx, hy)
            out.append(RectangularAperture(half_width_mm=half_u, half_height_mm=half_v, center_offset_mm=centre))
    return tuple(out)


def _engine_aperture(surface: PrescriptionSurfaceV1 | PosedSurfaceV1) -> EngineAperture:
    cx, cy = (Decimal(v) for v in surface.aperture_center_mm) if surface.aperture_center_mm is not None else (Decimal(0), Decimal(0))
    centre = _engine_uv(surface, _number(cx), _number(cy))
    if surface.clear_shape is not None:
        return _shaped(surface.clear_shape, centre, surface.clear_radius_mm, surface.clear_half_widths_mm, surface.cylinder_axis == "X")
    if surface.clear_radius_mm is not None:
        return CircularAperture(radius_mm=_number(surface.clear_radius_mm), center_offset_mm=centre)
    hx, hy = (_number(v) for v in surface.clear_half_widths_mm)  # type: ignore[union-attr]
    half_u, half_v = (hy, hx) if surface.cylinder_axis == "X" else (hx, hy)
    return RectangularAperture(half_width_mm=half_u, half_height_mm=half_v, center_offset_mm=centre)


def _bounds(aperture: EngineAperture) -> tuple[float, float, float, float]:
    """``(u_min, u_max, v_min, v_max)`` of an aperture (its bounding box)."""

    cu, cv = aperture.center_offset_mm
    if not isinstance(aperture, (CircularAperture, RectangularAperture)):
        return aperture_regions.bounds(lower_aperture(aperture))
    if isinstance(aperture, CircularAperture):
        hu = hv = aperture.radius_mm
    else:
        hu, hv = aperture.half_width_mm, aperture.half_height_mm
    return cu - hu, cu + hu, cv - hv, cv + hv


def _mount(aperture: EngineAperture, outline: CircularAperture | RectangularAperture | None
           ) -> tuple[float, CircularAperture | RectangularAperture | None]:
    """``(mount_margin_mm, mount_outline)`` of a surface: an even ring when the outline is the aperture grown evenly
    (concentric circles, or rectangles with equal margins), else the outline itself (none: no ring)."""

    if outline is None:
        return 0.0, None
    if outline.center_offset_mm == aperture.center_offset_mm:
        if isinstance(outline, CircularAperture) and isinstance(aperture, CircularAperture):
            return max(0.0, outline.radius_mm - aperture.radius_mm), None
        if isinstance(outline, RectangularAperture) and isinstance(aperture, RectangularAperture):
            margin_u, margin_v = outline.half_width_mm - aperture.half_width_mm, outline.half_height_mm - aperture.half_height_mm
            if margin_u == margin_v and margin_u >= 0.0:
                return margin_u, None
    return 0.0, outline


def _contains(outline: CircularAperture | RectangularAperture, aperture: EngineAperture) -> bool:
    u0, u1, v0, v1 = _bounds(aperture)
    if isinstance(outline, RectangularAperture):
        o0, o1, p0, p1 = _bounds(outline)
        return o0 - OUTLINE_TOLERANCE_MM <= u0 and u1 <= o1 + OUTLINE_TOLERANCE_MM and p0 - OUTLINE_TOLERANCE_MM <= v0 and v1 <= p1 + OUTLINE_TOLERANCE_MM
    cu, cv = outline.center_offset_mm
    reach = outline.radius_mm + OUTLINE_TOLERANCE_MM
    if isinstance(aperture, CircularAperture):  # (any other shape: its bounding box)
        return math.hypot(aperture.center_offset_mm[0] - cu, aperture.center_offset_mm[1] - cv) + aperture.radius_mm <= reach
    return all(math.hypot(u - cu, v - cv) <= reach for u in (u0, u1) for v in (v0, v1))


def _sequence_outline(model: SurfaceSequenceModelV1, index: int) -> CircularAperture | RectangularAperture:
    """The outline of surface ``index``: the outline of the glass it bounds (``glass_outline_mm``), in its Engine axes."""

    surface = model.surfaces[index]
    hx, hy = model.glass_outline_mm(index)
    if model.is_round:
        return CircularAperture(radius_mm=_number(hx))
    ex, ey = _number(hx), _number(hy)
    half_u, half_v = (ey, ex) if surface.cylinder_axis == "X" else (ex, ey)
    return RectangularAperture(half_width_mm=half_u, half_height_mm=half_v)


def _float3(values) -> tuple[float, float, float]:
    return (_number(values[0]), _number(values[1]), _number(values[2]))


def _surface_axes(surface: PosedSurfaceV1) -> tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]:
    """``(origin, normal, u)`` of a posed surface as floats."""

    return _float3(surface.origin_mm), _float3(surface.normal), _float3(surface.u_axis)


def _cross(a, b) -> tuple[float, float, float]:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a, b) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _rectangle_of(points: list[tuple[float, float]]) -> RectangularAperture | None:
    """The axis-aligned rectangle whose corners are ``points`` (None when they are not exactly such a rectangle)."""

    if len(points) != 4:
        return None
    us, vs = [p[0] for p in points], [p[1] for p in points]
    u0, u1, v0, v1 = min(us), max(us), min(vs), max(vs)
    corners = [(u0, v0), (u1, v0), (u1, v1), (u0, v1)]
    if not all(any(abs(p[0] - c[0]) <= OUTLINE_TOLERANCE_MM and abs(p[1] - c[1]) <= OUTLINE_TOLERANCE_MM for p in points) for c in corners):
        return None
    if not (u1 > u0 and v1 > v0):
        return None
    return RectangularAperture(half_width_mm=(u1 - u0) / 2.0, half_height_mm=(v1 - v0) / 2.0,
                               center_offset_mm=((u0 + u1) / 2.0, (v0 + v1) / 2.0))


def _posed_outline(surface: PosedSurfaceV1, solid: Solid | None) -> CircularAperture | RectangularAperture | None:
    """The body face (or internal section) a posed surface lies on, as an outline in its Engine (u, v) axes."""

    if solid is None:
        return None
    origin, normal, u_cat = _surface_axes(surface)
    v_cat = _cross(normal, u_cat)

    def local(point) -> tuple[float, float]:
        rel = (point[0] - origin[0], point[1] - origin[1], point[2] - origin[2])
        return _engine_uv(surface, _dot(rel, u_cat), _dot(rel, v_cat))

    on_face = [i for i, (n, d) in enumerate(solid.planes) if abs(_dot(n, origin) - d) <= OUTLINE_TOLERANCE_MM * max(1.0, abs(d))
               and abs(abs(_dot(n, normal)) - 1.0) <= OUTLINE_TOLERANCE_MM]
    if solid.side_radius is not None:
        # a round plate's face: the circle of the side radius about the point where local Z meets the face (the face's
        # ellipse on the side cylinder contains it)
        if not on_face or normal[2] == 0.0:
            return None
        n, d = solid.planes[on_face[0]]
        return CircularAperture(radius_mm=solid.side_radius, center_offset_mm=local((0.0, 0.0, d / n[2])))
    polygon = face_polygon(solid, on_face[0]) if on_face else section_polygon(solid, origin, normal)
    return _rectangle_of([local(p) for p in polygon])


# ----------------------------------------------------------------------------------------------- surfaces


def _sequence_surface(model: SurfaceSequenceModelV1, index: int, surface: PrescriptionSurfaceV1, interaction, before: str,
                      after: str, engine_material_id=engine_material_id) -> PlaneSurface | SphereSurface | SagSurface:
    vertex = _number(surface.vertex_z_mm)
    outline = _sequence_outline(model, index)
    plain = model.is_round and surface.is_rotationally_symmetric
    reach = model.glass_outline_mm(index)[0]  # a round element: the outline radius of the glass this surface bounds
    if plain and surface.is_plane:
        clear = _number(surface.clear_radius_mm)  # type: ignore[arg-type]
        with localcontext(DECIMAL_CONTEXT):
            margin = _number(reach - Decimal(surface.clear_radius_mm))  # type: ignore[arg-type]
        return PlaneSurface(surface_id=surface.surface_id, origin_mm=(0.0, 0.0, vertex), normal=LOCAL_Z,
                            aperture=CircularAperture(radius_mm=clear), mount_margin_mm=margin,
                            medium_positive=engine_material_id(after), medium_negative=engine_material_id(before), interaction=interaction)
    if plain and not surface.is_aspheric and not surface.is_axicon:
        with localcontext(DECIMAL_CONTEXT):
            radius = Decimal(surface.radius_mm)  # type: ignore[arg-type]
            clear = Decimal(surface.clear_radius_mm)  # type: ignore[arg-type]
            # the glass outline may reach past the sphere's own edge (a flange): the sphere's support ends at |R|
            margin = _number(min(reach, abs(radius)) - clear) if surface.has_own_edge else _number(reach - clear)
            away_from_centre, towards_centre = (before, after) if radius > 0 else (after, before)
            if surface.traced is TracedBranches.REFLECTED:
                interaction = interaction.model_copy(update={"reflective_side": "negative" if radius > 0 else "positive"})
            return SphereSurface(
                surface_id=surface.surface_id, center_mm=(0.0, 0.0, _number(Decimal(surface.vertex_z_mm) + radius)),
                radius_mm=_number(abs(radius)), cap_axis=(0.0, 0.0, -1.0 if radius > 0 else 1.0),
                aperture_radius_mm=_number(min(clear, abs(radius))), mount_margin_mm=margin,
                medium_positive=engine_material_id(away_from_centre), medium_negative=engine_material_id(towards_centre),
                interaction=interaction,
            )
    aperture = _engine_aperture(surface)
    margin, mount_outline = _mount(aperture, outline)
    common = {"surface_id": surface.surface_id, "aperture": aperture, "mount_margin_mm": margin, "mount_outline": mount_outline,
              "holes": _engine_holes(surface), "medium_positive": engine_material_id(after), "medium_negative": engine_material_id(before),
              "interaction": interaction}
    if surface.is_plane:
        return PlaneSurface(origin_mm=(0.0, 0.0, vertex), normal=LOCAL_Z, aperture_u_axis=LOCAL_X, **common)
    return SagSurface(origin_mm=(0.0, 0.0, vertex), normal=LOCAL_Z, u_axis=LOCAL_Y if surface.cylinder_axis == "X" else LOCAL_X,
                      **_profile(surface), **common)


def _optional_number(value: str | None) -> float | None:
    return None if value is None else _number(value)


def _engine_sag_shape(shape) -> ZernikeSag | XyPolynomialSag | BiconicSag | ToroidalSag:
    """A published two-variable sag shape in Engine numbers."""

    if shape.kind == "ZERNIKE":
        return ZernikeSag(indexing=shape.indexing, normalization=shape.normalization, normalization_radius_mm=_number(shape.normalization_radius_mm),
                          terms=tuple(ZernikeSagTerm(index=t.index, value_mm=_number(t.value_mm)) for t in shape.terms))
    if shape.kind == "XY_POLYNOMIAL":
        return XyPolynomialSag(normalization_radius_mm=_number(shape.normalization_radius_mm),
                               terms=tuple(XySagTerm(x_power=t.x_power, y_power=t.y_power, value_mm=_number(t.value_mm)) for t in shape.terms))
    if shape.kind == "BICONIC":
        return BiconicSag(radius_x_mm=_optional_number(shape.radius_x_mm), radius_y_mm=_optional_number(shape.radius_y_mm),
                          conic_x=_optional_number(shape.conic_x), conic_y=_optional_number(shape.conic_y))
    return ToroidalSag(radius_y_mm=_optional_number(shape.radius_y_mm), conic=_optional_number(shape.conic),
                       y_terms=tuple(_number(a) for a in shape.y_terms or ()), radius_of_rotation_mm=_optional_number(shape.radius_of_rotation_mm))


def _profile(surface: PrescriptionSurfaceV1 | PosedSurfaceV1) -> dict:
    with localcontext(DECIMAL_CONTEXT):
        curvature = _number(Decimal(1) / Decimal(surface.radius_mm)) if surface.radius_mm is not None else 0.0
    profile = {"curvature_per_mm": curvature, "conic": _number(surface.conic) if surface.conic is not None else 0.0,
               "aspheric_coefficients": tuple(_number(a) for a in surface.aspheric_coefficients or ()),
               "cylindrical": surface.cylinder_axis is not None}
    if surface.linear_sag_coefficient is not None:
        profile["linear_sag_coefficient"] = _number(surface.linear_sag_coefficient)
    if surface.sag is not None:
        profile["sag_shape"] = _engine_sag_shape(surface.sag)
    return profile


def _posed_surface(surface: PosedSurfaceV1, solid: Solid | None, interaction, engine_material_id=engine_material_id) -> PlaneSurface | SagSurface:
    origin, normal, u_cat = _surface_axes(surface)
    engine_u = _cross(normal, u_cat) if surface.cylinder_axis == "X" else u_cat
    aperture = _engine_aperture(surface)
    outline = _posed_outline(surface, solid)
    if outline is not None and not _contains(outline, aperture):
        outline = None
    margin, mount_outline = _mount(aperture, outline)
    common = {"surface_id": surface.surface_id, "origin_mm": origin, "normal": normal, "aperture": aperture, "mount_margin_mm": margin,
              "mount_outline": mount_outline, "holes": _engine_holes(surface), "medium_positive": engine_material_id(surface.medium_front),
              "medium_negative": engine_material_id(surface.medium_back), "interaction": interaction}
    if surface.is_plane:
        return PlaneSurface(aperture_u_axis=engine_u, **common)
    return SagSurface(u_axis=engine_u, **_profile(surface), **common)


def _materials(resolver: ModelResolver, name: str, model, where: str) -> tuple[MaterialOpticalModel, ...]:
    materials = []
    for material_id in model.material_ids():
        material = resolver.material(material_id)
        if material is None:
            raise _problem(resolver.missing_code, f"{name}: material {material_id} {resolver.missing_text}", where)
        materials.append(resolver.engine_material(material))
    return tuple(sorted(materials, key=lambda m: m.material_id))


def _envelope(surface, coating: PublicCoatingV1 | None) -> SurfaceEnvelope:
    return SurfaceEnvelope(surface_id=surface.surface_id, traced=surface.traced.value, coating=coating,
                           valid_aoi_deg=coating.valid_aoi_deg if coating is not None else None)


def _surface_sequence(instance_id: str, identity: ElementIdentity, model: SurfaceSequenceModelV1, resolver: ModelResolver,
                      where: str) -> CompiledElement:
    surfaces: list[PlaneSurface | SphereSurface | SagSurface] = []
    envelopes: list[SurfaceEnvelope] = []
    for index, surface in enumerate(model.surfaces):
        before, after = _media(model, index)
        coating = _coating(resolver, identity.name, surface, where)
        interaction = _interaction(surface, coating, before, after, identity.name, where, design_point=resolver.design_point)
        surfaces.append(_sequence_surface(model, index, surface, interaction, before, after, resolver.engine_material_id))
        envelopes.append(_envelope(surface, coating))
    return CompiledElement(instance_id=instance_id, identity=identity, model=model, surfaces=tuple(surfaces),
                           materials=_materials(resolver, identity.name, model, where), envelopes=tuple(envelopes),
                           material_namespace=resolver.namespace, crystal_media=resolver.crystal_media())


def _surface_set(instance_id: str, identity: ElementIdentity, model: SurfaceSetModelV1, resolver: ModelResolver, where: str) -> CompiledElement:
    solid = solid_of(model.body) if model.body is not None else None
    surfaces: list[PlaneSurface | SphereSurface | SagSurface] = []
    envelopes: list[SurfaceEnvelope] = []
    for surface in model.surfaces:
        coating = _coating(resolver, identity.name, surface, where)
        interaction = _interaction(surface, coating, surface.medium_back, surface.medium_front, identity.name, where,
                                   design_point=resolver.design_point)
        surfaces.append(_posed_surface(surface, solid, interaction, resolver.engine_material_id))
        envelopes.append(_envelope(surface, coating))
    return CompiledElement(instance_id=instance_id, identity=identity, model=model, surfaces=tuple(surfaces),
                           materials=_materials(resolver, identity.name, model, where), envelopes=tuple(envelopes), solid=solid,
                           material_namespace=resolver.namespace, crystal_media=resolver.crystal_media())


def _in_plane_axis(angle_deg: float) -> tuple[float, float, float]:
    """The unit axis ``angle_deg`` from local +X toward +Y (``math.exact_trig``: the same bits in every runtime)."""
    turn = cis(angle_deg * DEG_TO_RAD)
    return (turn.real, turn.imag, 0.0)


def _retarder_interaction(model: PolarizationElementModelV1, axis: tuple[float, float, float], transmittance: float,
                          bands) -> RetarderInteraction:
    rows = model.retardance_rows if model.retardance_dispersion is RetardanceDispersion.TABULATED else None
    return RetarderInteraction(
        fast_axis_local=axis, retardance_rad=FULL_TURN_RAD * _number(model.retardance_waves),  # type: ignore[arg-type]
        design_wavelength_nm=_number(model.design_wavelength_nm),  # type: ignore[arg-type]
        achromatic=model.retardance_dispersion is RetardanceDispersion.CONSTANT, transmittance=transmittance,
        transmittance_bands=None if bands is None else tuple(
            RetarderTransmittanceBand(min_nm=_number(b.min_nm), max_nm=_number(b.max_nm), transmittance=_number(b.transmittance.value))
            for b in bands),
        retardance_table_nm=None if rows is None else tuple(_number(r.wavelength_nm) for r in rows),
        retardance_table_rad=None if rows is None else tuple(FULL_TURN_RAD * _number(r.retardance_waves) for r in rows),
        retardance_interpolation=None if rows is None else ("linear" if model.retardance_interpolation == "LINEAR" else "points_only"),
    )


def _polarization_element(instance_id: str, identity: ElementIdentity, model: PolarizationElementModelV1, resolver: ModelResolver) -> CompiledElement:
    retarder_surface = None
    with localcontext(DECIMAL_CONTEXT):
        clear, edge = Decimal(model.clear_radius_mm), Decimal(model.edge_radius_mm)
        transmittance = Decimal(model.transmittance.value)
        bands = model.transmittance_bands
        if model.element is PolarizationElementKind.RETARDER:
            interaction = _retarder_interaction(model, LOCAL_X, _number(transmittance), bands)
        else:
            extinction = Decimal(model.extinction_ratio.value)  # type: ignore[union-attr]

            def band(b) -> PolarizerTransmittanceBand:  # a band without its own extinction ratio has the element's
                t_band = Decimal(b.transmittance.value)
                ratio = Decimal(b.extinction_ratio.value) if b.extinction_ratio is not None else extinction
                return PolarizerTransmittanceBand(min_nm=_number(b.min_nm), max_nm=_number(b.max_nm), transmittance_parallel=_number(t_band),
                                                  transmittance_perpendicular=_number(t_band / ratio))

            interaction = PolarizerInteraction(axis_local=LOCAL_X, transmittance_parallel=_number(transmittance),
                                               transmittance_perpendicular=_number(transmittance / extinction),
                                               transmittance_bands=None if bands is None else tuple(band(b) for b in bands))
            if model.element is PolarizationElementKind.CIRCULAR_POLARIZER:
                retarder_surface = PlaneSurface(
                    surface_id=CIRCULAR_RETARDER_SURFACE_ID,
                    origin_mm=(0.0, 0.0, _number(Decimal(model.plane_z_mm) + Decimal(model.retarder_offset_mm))),  # type: ignore[arg-type]
                    normal=LOCAL_Z, aperture=CircularAperture(radius_mm=_number(clear)), mount_margin_mm=_number(edge - clear),
                    interaction=_retarder_interaction(model, _in_plane_axis(_number(model.retarder_axis_deg)), 1.0, None))  # type: ignore[arg-type]
        surface = PlaneSurface(
            surface_id=POLARIZATION_SURFACE_ID, origin_mm=(0.0, 0.0, _number(model.plane_z_mm)), normal=LOCAL_Z,
            aperture=CircularAperture(radius_mm=_number(clear)), mount_margin_mm=_number(edge - clear), interaction=interaction,
        )
    surfaces = (surface,) if retarder_surface is None else (surface, retarder_surface)
    envelopes = tuple(SurfaceEnvelope(surface_id=s.surface_id, traced=POLARIZATION_ELEMENT, valid_aoi_deg=model.valid_aoi_deg) for s in surfaces)
    return CompiledElement(instance_id=instance_id, identity=identity, model=model, surfaces=surfaces, materials=(), envelopes=envelopes,
                           material_namespace=resolver.namespace)


def _pair(values) -> tuple[float, float]:
    return _number(values[0]), _number(values[1])


def _density_law(law) -> FixedDensity | LinearRotaryDensity | SteppedRotaryDensity | LinearGradientDensity:
    if law.kind == "FIXED":
        return FixedDensity(optical_density=_number(law.optical_density))
    if law.kind == "LINEAR_ROTARY":
        return LinearRotaryDensity(od_start=_number(law.od_start), od_end=_number(law.od_end), arc_start_deg=_number(law.arc_start_deg),
                                   arc_end_deg=_number(law.arc_end_deg))
    if law.kind == "STEPPED_ROTARY":
        return SteppedRotaryDensity(steps=tuple(DensityStep(start_deg=_number(step.start_deg), end_deg=_number(step.end_deg),
                                                            optical_density=_number(step.optical_density)) for step in law.steps))
    return LinearGradientDensity(od_start=_number(law.od_start), od_end=_number(law.od_end), x_start_mm=_number(law.x_start_mm),
                                 x_end_mm=_number(law.x_end_mm))


def _neutral_density(instance_id: str, identity: ElementIdentity, model: NeutralDensityModelV1, resolver: ModelResolver) -> CompiledElement:
    if model.clear_radius_mm is not None:
        opening: CircularAperture | RectangularAperture = CircularAperture(radius_mm=_number(model.clear_radius_mm))
    else:
        half_u, half_v = _pair(model.clear_half_widths_mm)
        opening = RectangularAperture(half_width_mm=half_u, half_height_mm=half_v)
    if model.edge_radius_mm is not None:
        outline: CircularAperture | RectangularAperture = CircularAperture(radius_mm=_number(model.edge_radius_mm))
    else:
        edge_u, edge_v = _pair(model.edge_half_widths_mm)
        outline = RectangularAperture(half_width_mm=edge_u, half_height_mm=edge_v)
    margin, mount_outline = _mount(opening, outline)
    if model.clear_radius_mm is not None and model.edge_radius_mm is not None:
        with localcontext(DECIMAL_CONTEXT):  # the even ring, exactly
            margin = _number(Decimal(model.edge_radius_mm) - Decimal(model.clear_radius_mm))
    interaction = NeutralDensityInteraction(
        law=_density_law(model.law), x_axis_local=LOCAL_X,
        uncoated_transmittance=_number(model.uncoated_transmittance) if model.uncoated_transmittance is not None else None,
        reflectance=_number(model.reflectance) if model.reflectance is not None else 0.0,
        coated_inner_radius_mm=_number(model.coated_inner_radius_mm) if model.coated_inner_radius_mm is not None else None)
    surface = PlaneSurface(surface_id=NEUTRAL_DENSITY_SURFACE_ID, origin_mm=(0.0, 0.0, _number(model.plane_z_mm)), normal=LOCAL_Z,
                           aperture=opening, aperture_u_axis=LOCAL_X, mount_margin_mm=margin, mount_outline=mount_outline,
                           interaction=interaction)
    envelope = SurfaceEnvelope(surface_id=NEUTRAL_DENSITY_SURFACE_ID, traced=NEUTRAL_DENSITY)
    return CompiledElement(instance_id=instance_id, identity=identity, model=model, surfaces=(surface,), materials=(), envelopes=(envelope,),
                           material_namespace=resolver.namespace)


def _positioned_member(element: CompiledElement) -> CompiledElement | None:
    """The body of ``element`` whose model names a position parameter (an attenuator's, a polarization element's rotation);
    of a ``COMPOUND`` unit the first member that names one."""

    for body in element.bodies():
        if isinstance(body.model, (NeutralDensityModelV1, PolarizationElementModelV1)) and body.model.position_parameter is not None:
            return body
    return None


def attenuator_position_parameter(element: CompiledElement) -> str | None:
    """The ``selected_parameters`` key that positions the element (``nd_rotation_deg`` / ``nd_offset_mm`` of an attenuator,
    ``axis_rotation_deg`` of a polarization element, or of the compound member that names it), or ``None``."""

    body = _positioned_member(element)
    return None if body is None else body.model.position_parameter  # type: ignore[union-attr]


def positioned_for(element: CompiledElement, component) -> CompiledElement:
    """``element`` with the attenuator position its Project ``component`` selects (``selected_parameters`` entry of the
    model's ``position_parameter``; absent: unchanged). The lowering refuses an entry of the wrong kind or unit first."""

    key = attenuator_position_parameter(element)
    for parameter in component.selected_parameters if key is not None else ():
        if parameter.semantic_key == key and parameter.value_kind.value == "DECIMAL":
            return with_attenuator_position(element, _number(parameter.value))
    return element


def _turned(axis, normal, turn: complex) -> tuple[float, float, float]:
    """``axis`` (perpendicular to the unit ``normal``) turned about ``normal`` by the angle of the unit complex ``turn``:
    ``a cos + (n x a) sin``."""
    a, n = axis, normal
    cross = (n[1] * a[2] - n[2] * a[1], n[2] * a[0] - n[0] * a[2], n[0] * a[1] - n[1] * a[0])
    return tuple(a[k] * turn.real + cross[k] * turn.imag for k in range(3))  # type: ignore[return-value]


def with_attenuator_position(element: CompiledElement, position: float) -> CompiledElement:
    """``element`` with its position parameter applied: a ``NEUTRAL_DENSITY`` attenuator's wheel turned by ``position``
    degrees (from local +X toward +Y) or its gradient shifted by ``position`` mm along local +X; a polarization element's
    polarizer and retarder axes (of a compound unit: those of the member that names the parameter) turned by ``position``
    degrees about their surface normals."""

    body = _positioned_member(element)
    if body is not None and isinstance(body.model, PolarizationElementModelV1):
        turn = cis(position * DEG_TO_RAD)
        names = {s.surface_id for s in body.surfaces}
        axes = {"polarizer": "axis_local", "retarder": "fast_axis_local"}

        def turned(s):
            key = axes.get(s.interaction.kind)
            if key is None or s.surface_id not in names:
                return s
            return s.model_copy(update={"interaction": s.interaction.model_copy(update={key: _turned(getattr(s.interaction, key), s.normal, turn)})})
        return replace(element, surfaces=tuple(turned(s) for s in element.surfaces))
    surfaces = tuple(s.model_copy(update={"interaction": s.interaction.model_copy(update={"position": position})})
                     if isinstance(s.interaction, NeutralDensityInteraction) else s for s in element.surfaces)
    return replace(element, surfaces=surfaces)


def _aperture_stop(instance_id: str, identity: ElementIdentity, model: ApertureStopModelV1, resolver: ModelResolver) -> CompiledElement:
    centre = _pair(model.opening_center_mm) if model.opening_center_mm is not None else (0.0, 0.0)
    if model.opening_shape is not None:
        opening: EngineAperture = _shaped(model.opening_shape, centre, model.opening_radius_mm, model.opening_half_widths_mm, False)
    elif model.opening_radius_mm is not None:
        obstruction = _number(model.obstruction_radius_mm) if model.obstruction_radius_mm is not None else 0.0
        opening = CircularAperture(radius_mm=_number(model.opening_radius_mm), center_offset_mm=centre, obstruction_radius_mm=obstruction)
    else:
        half_u, half_v = _pair(model.opening_half_widths_mm)
        opening = RectangularAperture(half_width_mm=half_u, half_height_mm=half_v, center_offset_mm=centre)
    if model.edge_radius_mm is not None:
        outline: CircularAperture | RectangularAperture = CircularAperture(radius_mm=_number(model.edge_radius_mm))
    else:
        edge_u, edge_v = _pair(model.edge_half_widths_mm)
        outline = RectangularAperture(half_width_mm=edge_u, half_height_mm=edge_v)
    margin, mount_outline = _mount(opening, outline)
    if isinstance(opening, CircularAperture) and isinstance(outline, CircularAperture) and opening.center_offset_mm == outline.center_offset_mm:
        with localcontext(DECIMAL_CONTEXT):  # the even ring, exactly
            margin = _number(Decimal(model.edge_radius_mm) - Decimal(model.opening_radius_mm))  # type: ignore[arg-type]
    interaction = ApertureStopInteraction() if model.sample is None else sample_interaction(model.sample)
    surface = PlaneSurface(surface_id=APERTURE_STOP_SURFACE_ID, origin_mm=(0.0, 0.0, _number(model.plane_z_mm)), normal=LOCAL_Z,
                           aperture=opening, aperture_u_axis=LOCAL_X, mount_margin_mm=margin, mount_outline=mount_outline,
                           interaction=interaction)
    envelope = SurfaceEnvelope(surface_id=APERTURE_STOP_SURFACE_ID, traced=APERTURE_STOP)
    return CompiledElement(instance_id=instance_id, identity=identity, model=model, surfaces=(surface,), materials=(), envelopes=(envelope,),
                           material_namespace=resolver.namespace)


def sample_interaction(sample) -> ComplexTransmissionInteraction:
    """The Engine interaction of a thin sample in an aperture stop's opening (``ComplexTransmissionSampleV1``): its element-frame
    ``x``, ``y`` are the plane's ``u``, ``v``."""

    terms: list = []
    for term in sample.phase_terms or ():
        if term.kind == "LINEAR_RAMP":
            terms.append(LinearPhaseRamp(gradient_rad_per_mm=_pair(term.gradient_rad_per_mm)))
        elif term.kind == "STEP":
            terms.append(PhaseStep(phase_rad=_number(term.phase_rad), edge_normal=_pair(term.edge_normal),
                                   edge_offset_mm=0.0 if term.edge_offset_mm is None else _number(term.edge_offset_mm)))
        else:
            terms.append(ZernikePhase(indexing=term.indexing, normalization=term.normalization,
                                      normalization_radius_mm=_number(term.normalization_radius_mm),
                                      terms=tuple(ZernikePhaseTerm(index=i, value_rad=_number(v))
                                                  for i, v in zip(term.zernike_indices, term.zernike_values_rad))))
    grid = None
    if sample.sampled_map is not None:
        m = sample.sampled_map
        grid = SampledTransmissionMap(
            nodes_u=m.nodes_x, nodes_v=m.nodes_y, pitch_mm=_pair(m.pitch_mm), center_mm=(0.0, 0.0) if m.center_mm is None else _pair(m.center_mm),
            amplitude=None if m.amplitude is None else tuple(_number(v) for v in m.amplitude),
            phase_rad=None if m.phase_rad is None else tuple(_number(v) for v in m.phase_rad))
    return ComplexTransmissionInteraction(amplitude=1.0 if sample.amplitude is None else _number(sample.amplitude),
                                          phase_rad=0.0 if sample.phase_rad is None else _number(sample.phase_rad),
                                          phase_terms=tuple(terms), sampled_map=grid)


def _thin_lens(instance_id: str, identity: ElementIdentity, model: IdealThinLensModelV1, resolver: ModelResolver) -> CompiledElement:
    with localcontext(DECIMAL_CONTEXT):
        margin = _number(Decimal(model.edge_radius_mm) - Decimal(model.clear_radius_mm))
    surface = PlaneSurface(surface_id=THIN_LENS_SURFACE_ID, origin_mm=(0.0, 0.0, _number(model.plane_z_mm)), normal=LOCAL_Z,
                           aperture=CircularAperture(radius_mm=_number(model.clear_radius_mm)), mount_margin_mm=margin,
                           interaction=ThinLensInteraction(focal_length_mm=_number(model.focal_length_mm), transmittance=_number(model.transmittance)))
    envelope = SurfaceEnvelope(surface_id=THIN_LENS_SURFACE_ID, traced=IDEAL_THIN_LENS)
    return CompiledElement(instance_id=instance_id, identity=identity, model=model, surfaces=(surface,), materials=(), envelopes=(envelope,),
                           material_namespace=resolver.namespace)


def engine_supported_metrics(model: TabulatedThinLensModelV1) -> tuple[str, ...] | None:
    """The Engine target metrics a tabulated thin lens's ``supported_metrics`` validate (``METRIC_GROUPS`` expanded), sorted;
    ``None`` for a model that states no list (no metric is singled out)."""

    if model.supported_metrics is None:
        return None
    names: set[str] = set()
    for entry in model.supported_metrics:
        names.update(METRIC_GROUPS.get(entry.metric, (entry.metric,)))
    return tuple(sorted(names))


def tabulated_thin_lens_interaction(model: TabulatedThinLensModelV1) -> TabulatedThinLensInteraction:
    """The Engine interaction of a tabulated thin lens (module docstring)."""

    polarizations = model.validated_polarizations
    if polarizations is None or "ANY" in polarizations:
        validated = None
    else:
        validated = tuple(THIN_LENS_POLARIZATIONS[p] for p in polarizations)
    return TabulatedThinLensInteraction(
        wavelength_nm=tuple(_number(row.wavelength_nm) for row in model.rows),
        focal_length_mm=tuple(_number(row.focal_length_mm) for row in model.rows),
        transmittance=tuple(_number(row.transmittance) for row in model.rows),
        focal_interpolation="linear_in_optical_power" if model.focal_interpolation == "LINEAR_IN_OPTICAL_POWER" else "points_only",
        transmittance_interpolation="linear" if model.transmittance_interpolation == "LINEAR" else "points_only",
        max_field_angle_deg=_number(model.max_field_angle_deg) if model.max_field_angle_deg is not None else None,
        max_beam_radius_mm=_number(model.max_beam_radius_mm) if model.max_beam_radius_mm is not None else None,
        validated_polarizations=validated, supported_metrics=engine_supported_metrics(model))


def _tabulated_thin_lens(instance_id: str, identity: ElementIdentity, model: TabulatedThinLensModelV1, resolver: ModelResolver) -> CompiledElement:
    with localcontext(DECIMAL_CONTEXT):
        margin = _number(Decimal(model.edge_radius_mm) - Decimal(model.clear_radius_mm))
    surface = PlaneSurface(surface_id=THIN_LENS_SURFACE_ID, origin_mm=(0.0, 0.0, _number(model.plane_z_mm)), normal=LOCAL_Z,
                           aperture=CircularAperture(radius_mm=_number(model.clear_radius_mm)), aperture_u_axis=LOCAL_X,
                           mount_margin_mm=margin, interaction=tabulated_thin_lens_interaction(model))
    envelope = SurfaceEnvelope(surface_id=THIN_LENS_SURFACE_ID, traced=TABULATED_THIN_LENS)
    return CompiledElement(instance_id=instance_id, identity=identity, model=model, surfaces=(surface,), materials=(), envelopes=(envelope,),
                           material_namespace=resolver.namespace)


def faraday_rotator_interaction(model: FaradayRotatorModelV1) -> FaradayRotatorInteraction:
    """The Engine interaction of a Faraday rotator (module docstring)."""

    return FaradayRotatorInteraction(
        wavelength_nm=tuple(_number(row.wavelength_nm) for row in model.rows),
        rotation_rad=tuple(_number(row.rotation_deg) / FULL_TURN_DEG * FULL_TURN_RAD for row in model.rows),
        transmittance=tuple(_number(row.transmittance) for row in model.rows),
        interpolation="linear" if model.interpolation == "LINEAR" else "points_only")


def _faraday_rotator(instance_id: str, identity: ElementIdentity, model: FaradayRotatorModelV1, resolver: ModelResolver) -> CompiledElement:
    with localcontext(DECIMAL_CONTEXT):
        margin = _number(Decimal(model.edge_radius_mm) - Decimal(model.clear_radius_mm))
    surface = PlaneSurface(surface_id=FARADAY_ROTATOR_SURFACE_ID, origin_mm=(0.0, 0.0, _number(model.plane_z_mm)), normal=LOCAL_Z,
                           aperture=CircularAperture(radius_mm=_number(model.clear_radius_mm)), aperture_u_axis=LOCAL_X,
                           mount_margin_mm=margin, interaction=faraday_rotator_interaction(model))
    envelope = SurfaceEnvelope(surface_id=FARADAY_ROTATOR_SURFACE_ID, traced=FARADAY_ROTATOR, valid_aoi_deg=model.valid_aoi_deg)
    return CompiledElement(instance_id=instance_id, identity=identity, model=model, surfaces=(surface,), materials=(), envelopes=(envelope,),
                           material_namespace=resolver.namespace)


def grating_table(response) -> GratingOrderTable:
    """The Engine table of a grating's stated response (module docstring)."""

    wavelengths = sorted({Decimal(row.wavelength_nm) for row in response.rows})
    angles = sorted({Decimal(row.aoi_deg) for row in response.rows})
    orders = sorted({row.order for row in response.rows})
    rows = {(Decimal(row.wavelength_nm), Decimal(row.aoi_deg), row.order): row for row in response.rows}
    resolved = response.polarization_basis == "TE_TM"

    def values(name: str) -> tuple[float, ...]:
        return tuple(_number(getattr(rows[(w, a, m)], name if resolved else "efficiency")) for m in orders for w in wavelengths for a in angles)

    losses = {(Decimal(row.wavelength_nm), Decimal(row.aoi_deg)): row for row in response.absorption}

    def lost(name: str) -> tuple[float, ...] | None:
        if not losses:
            return None
        return tuple(_number(getattr(losses[(w, a)], name if resolved else "absorption")) for w in wavelengths for a in angles)

    return GratingOrderTable(
        normalization=response.normalization.lower(), polarization_basis="te_tm" if resolved else "unpolarized",
        interpolation="linear" if response.interpolation == "LINEAR" else "points_only", data_kind=response.data_kind.lower(),
        wavelength_nm=tuple(_number(w) for w in wavelengths), aoi_deg=tuple(_number(a) for a in angles), orders=tuple(orders),
        efficiency_te=values("efficiency_te"), efficiency_tm=values("efficiency_tm"), absorption_te=lost("absorption_te"),
        absorption_tm=lost("absorption_tm"), max_conical_angle_deg=_number(response.max_conical_angle_deg))


def diffraction_grating_interaction(model: DiffractionGratingModelV1) -> DiffractionGratingInteraction:
    """The Engine interaction of a diffraction grating (module docstring)."""

    turn = cis(_number(model.grating_vector_angle_deg) / FULL_TURN_DEG * FULL_TURN_RAD)
    if model.orders is not None:
        orders, every = model.orders, False
    else:
        first, last = model.all_propagating_orders  # type: ignore[misc]
        orders, every = tuple(range(first, last + 1)), True
    return DiffractionGratingInteraction(
        mode="reflection" if model.mode == "REFLECTION" else "transmission", period_nm=_number(model.period),
        grating_vector_local=(turn.real, turn.imag, 0.0), orders=orders, all_propagating=every,
        response=None if model.response is None else grating_table(model.response))


def _diffraction_grating(instance_id: str, identity: ElementIdentity, model: DiffractionGratingModelV1, resolver: ModelResolver) -> CompiledElement:
    if model.clear_radius_mm is not None:
        opening: CircularAperture | RectangularAperture = CircularAperture(radius_mm=_number(model.clear_radius_mm))
    else:
        half_u, half_v = _pair(model.clear_half_widths_mm)
        opening = RectangularAperture(half_width_mm=half_u, half_height_mm=half_v)
    if model.edge_radius_mm is not None:
        outline: CircularAperture | RectangularAperture = CircularAperture(radius_mm=_number(model.edge_radius_mm))
    else:
        edge_u, edge_v = _pair(model.edge_half_widths_mm)
        outline = RectangularAperture(half_width_mm=edge_u, half_height_mm=edge_v)
    margin, mount_outline = _mount(opening, outline)
    materials = []
    for material_id in model.material_ids():
        material = resolver.material(material_id)
        if material is None:
            raise _problem(resolver.missing_code, f"{identity.name}: material {material_id} {resolver.missing_text}", instance_id)
        materials.append(engine_material(material, resolver.namespace, temperature_c=_number(resolver.environment.temperature_c)))
    surface = PlaneSurface(surface_id=GRATING_SURFACE_ID, origin_mm=(0.0, 0.0, _number(model.plane_z_mm)), normal=LOCAL_Z, aperture=opening,
                           aperture_u_axis=LOCAL_X, mount_margin_mm=margin, mount_outline=mount_outline,
                           medium_positive=resolver.engine_material_id(model.medium_positive_z),
                           medium_negative=resolver.engine_material_id(model.medium_negative_z), interaction=diffraction_grating_interaction(model))
    envelope = SurfaceEnvelope(surface_id=GRATING_SURFACE_ID, traced=DIFFRACTION_GRATING)
    return CompiledElement(instance_id=instance_id, identity=identity, model=model, surfaces=(surface,), materials=tuple(materials),
                           envelopes=(envelope,), material_namespace=resolver.namespace)


def _nonlinear_crystal(instance_id: str, identity: ElementIdentity, model: NonlinearCrystalModelV1, resolver: ModelResolver,
                       where: str) -> CompiledElement:
    """A ``NONLINEAR_CRYSTAL``: its slab compiled as the crystal surface sequence it is
    (``NonlinearCrystalModelV1.as_surface_sequence``: two plane faces, the crystal between them, cut by the element like any
    crystal: ``resolver`` is the element's ``CrystalCutResolver``), whose Engine crystal material carries the interaction
    (``nonlinear``) and the crystal's temperature. The compiled physics is that surface sequence (``COMPILED_AS``); the
    element states its own kind (``declared_kind``)."""

    element = _surface_sequence(instance_id, identity, model.as_surface_sequence(), resolver, where)
    crystal_id = element.engine_material_id(model.medium)
    extra = {"nonlinear": nonlinear_interaction(model), "temperature": crystal_temperature(model)}
    if not any(m.material_id == crystal_id and isinstance(m, UniaxialMaterial) for m in element.materials):
        raise _problem(LocalRunErrorCode.OPTICAL_MODEL_INVALID, f"{identity.name}: the nonlinear crystal's medium {model.medium} is not "
                       "a crystal this element orients (state its optic axis in optic_axes)", where)
    try:
        materials = tuple(UniaxialMaterial.model_validate({**m.model_dump(), **{k: (v.model_dump() if v is not None else None)
                                                                                 for k, v in extra.items()}})
                          if m.material_id == crystal_id else m for m in element.materials)
    except ValidationError as exc:
        raise _problem(LocalRunErrorCode.OPTICAL_MODEL_INVALID, f"{identity.name}: nonlinear crystal: {exc.errors(include_url=False)}",
                       where) from None
    return replace(element, materials=materials, declared_kind=NONLINEAR_CRYSTAL)


def _facet_interaction(traced: str, coating: PublicCoatingV1 | None, design_point: DesignPointTolerances = DEFAULT_DESIGN_POINT
                       ) -> RefractInteraction | MirrorInteraction | AbsorberInteraction:
    """A facet's Engine interaction: refracting (bare Fresnel, or its coating), mirrored on the inside (the solid is the
    negative side; ideal without a coating) or absorbing."""
    compiled = engine_coating(coating, design_point) if coating is not None else None
    if traced == "ABSORBED":
        return AbsorberInteraction()
    if traced == "REFLECTED":
        return MirrorInteraction(reflective_side="negative", coating=compiled, phases=engine_phases(coating))
    return RefractInteraction(coating=compiled, phases=engine_phases(coating))


def _facet_solid(instance_id: str, identity: ElementIdentity, model: FacetSolidModelV1, resolver: ModelResolver) -> CompiledElement:
    def coating_of(coating_id: str | None) -> PublicCoatingV1 | None:
        if coating_id is None:
            return None
        coating = resolver.coating(coating_id)
        if coating is None:
            raise _problem(resolver.missing_code, f"{identity.name}: coating {coating_id} {resolver.missing_text}", instance_id)
        return coating

    material = resolver.material(model.medium)
    if material is None:
        raise _problem(resolver.missing_code, f"{identity.name}: material {model.medium} {resolver.missing_text}", instance_id)
    default = coating_of(model.coating_id)
    groups = tuple((group.traced, coating_of(group.coating_id), group) for group in model.facet_groups or ())
    approximation = FacetApproximation(kind=model.approximation, source=model.source, max_chord_deviation_mm=(
        None if model.max_chord_deviation_mm is None else _number(model.max_chord_deviation_mm)))
    surface = MeshSurface(
        surface_id=FACET_SURFACE_ID, vertices_mm=model.vertex_floats(), triangles=tuple(tuple(t) for t in model.triangles),
        approximation=approximation, closed=True, medium_positive=ENGINE_AIR, medium_negative=resolver.engine_material_id(model.medium),
        interaction=_facet_interaction("TRANSMITTED", default, resolver.design_point),
        facet_groups=tuple(MeshFacetGroup(triangles=tuple(g.triangles), interaction=_facet_interaction(traced, coating, resolver.design_point))
                           for traced, coating, g in groups) or None)
    # one validity envelope per planar patch the run traces (its facet group's surface and coating)
    kinds = (("TRANSMITTED", default), *((traced, coating) for traced, coating, _g in groups))
    envelopes = []
    for patch in planar_patches(model.vertex_floats(), model.triangles, surface.facet_group_of()):
        traced, coating = kinds[patch.group]
        envelopes.append(SurfaceEnvelope(surface_id=f"{FACET_SURFACE_ID}.t{patch.first}", traced=traced, coating=coating,
                                         valid_aoi_deg=coating.valid_aoi_deg if coating is not None else None))
    return CompiledElement(instance_id=instance_id, identity=identity, model=model, surfaces=(surface,),
                           materials=(resolver.engine_material(material),), envelopes=tuple(envelopes),
                           material_namespace=resolver.namespace, crystal_media=resolver.crystal_media())


# --------------------------------------------------------------------------------------------- compound units


Matrix3 = tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]


def rotation_matrix(quaternion_wxyz) -> Matrix3:
    """The rotation ``R(q)`` of a Hamilton quaternion ``(w, x, y, z)`` (active), normalized first (a unit quaternion is used
    as given, so the identity is exact). Plain multiplications and one square root: identical on every runtime."""

    w, x, y, z = (float(c) for c in quaternion_wxyz)
    norm = math.sqrt(w * w + x * x + y * y + z * z)
    if norm != 1.0:
        w, x, y, z = w / norm, x / norm, y / norm, z / norm
    return ((1.0 - 2.0 * (y * y + z * z), 2.0 * (x * y - w * z), 2.0 * (x * z + w * y)),
            (2.0 * (x * y + w * z), 1.0 - 2.0 * (x * x + z * z), 2.0 * (y * z - w * x)),
            (2.0 * (x * z - w * y), 2.0 * (y * z + w * x), 1.0 - 2.0 * (x * x + y * y)))


def _rotate(rotation: Matrix3, v) -> tuple[float, float, float]:
    # "+ 0.0" folds a negative zero, so equal geometry always serializes alike
    return tuple(row[0] * v[0] + row[1] * v[1] + row[2] * v[2] + 0.0 for row in rotation)  # type: ignore[return-value]


def _move(rotation: Matrix3, position, point) -> tuple[float, float, float]:
    turned = _rotate(rotation, point)
    return (turned[0] + position[0] + 0.0, turned[1] + position[1] + 0.0, turned[2] + position[2] + 0.0)


def _unit_quaternion(quaternion_wxyz) -> tuple[float, float, float, float]:
    """``q`` normalized as ``rotation_matrix`` normalizes it (a unit quaternion as given)."""
    w, x, y, z = (float(c) for c in quaternion_wxyz)
    norm = math.sqrt(w * w + x * x + y * y + z * z)
    return (w, x, y, z) if norm == 1.0 else (w / norm, x / norm, y / norm, z / norm)


def _placement(pose: PoseV1, inner: Pose3D | None) -> Pose3D:
    """The rigid pose ``pose o inner`` (``inner`` first, then ``pose``; ``inner`` ``None``: the identity): a facet surface's
    placement in the unit frame (``MeshSurface.placement``)."""
    rotation, position = rotation_matrix(pose.quaternion_wxyz), tuple(float(v) for v in pose.position_mm)
    a = _unit_quaternion(pose.quaternion_wxyz)
    if inner is None:
        return Pose3D(position_mm=position, quaternion_wxyz=a)
    b = _unit_quaternion(inner.quaternion_wxyz)
    product = (a[0] * b[0] - a[1] * b[1] - a[2] * b[2] - a[3] * b[3], a[0] * b[1] + a[1] * b[0] + a[2] * b[3] - a[3] * b[2],
               a[0] * b[2] - a[1] * b[3] + a[2] * b[0] + a[3] * b[1], a[0] * b[3] + a[1] * b[2] - a[2] * b[1] + a[3] * b[0])
    return Pose3D(position_mm=_move(rotation, position, inner.position_mm), quaternion_wxyz=_unit_quaternion(product))


#: Engine interactions that hold a direction of the element frame, by kind: the field that turns with a placed member.
ORIENTED_INTERACTION_AXES = {"polarizer": "axis_local", "retarder": "fast_axis_local", "neutral_density": "x_axis_local",
                             "diffraction_grating": "grating_vector_local"}
#: Engine interactions that hold no direction of the element frame (a placed member keeps them as they are).
AXIS_FREE_INTERACTIONS = frozenset({"refract", "mirror", "thin_lens", "tabulated_thin_lens", "beam_splitter", "absorber", "aperture_stop",
                                     "faraday_rotator", "complex_transmission"})  # (a Faraday rotator's field axis is its plane's normal)


def _placed_interaction(interaction, rotation: Matrix3):
    axis = ORIENTED_INTERACTION_AXES.get(interaction.kind)
    if axis is not None:
        return interaction.model_copy(update={axis: _rotate(rotation, getattr(interaction, axis))})
    if interaction.kind not in AXIS_FREE_INTERACTIONS:
        raise KeyError(f"interaction kind {interaction.kind!r} is in neither ORIENTED_INTERACTION_AXES nor AXIS_FREE_INTERACTIONS")
    return interaction


def placed_surface(surface: PlaneSurface | SphereSurface | SagSurface | MeshSurface, pose: PoseV1
                   ) -> PlaneSurface | SphereSurface | SagSurface | MeshSurface:
    """``surface`` (in a member's frame) in the unit frame where ``pose`` places the member: ``x_unit = R(q) x + p`` for
    points, ``R(q) v`` for directions (normals, aperture / sag axes, polarizer and retarder axes). A facet surface keeps its
    vertices as written and gains the placement (``MeshSurface.placement``, composed with one it has): its faces are found
    from the coordinates as given (their rounding decides which facets lie in one plane), as for the part placed directly."""

    rotation, position = rotation_matrix(pose.quaternion_wxyz), tuple(float(v) for v in pose.position_mm)
    update: dict = {"interaction": _placed_interaction(surface.interaction, rotation)}
    if isinstance(surface, MeshSurface):  # (its facet groups' interactions name no direction)
        return surface.model_copy(update={**update, "placement": _placement(pose, surface.placement)})
    if isinstance(surface, SphereSurface):
        update.update(center_mm=_move(rotation, position, surface.center_mm), cap_axis=_rotate(rotation, surface.cap_axis))
    else:
        update.update(origin_mm=_move(rotation, position, surface.origin_mm), normal=_rotate(rotation, surface.normal))
        if isinstance(surface, SagSurface):
            update["u_axis"] = _rotate(rotation, surface.u_axis)
        elif surface.aperture_u_axis is not None:
            update["aperture_u_axis"] = _rotate(rotation, surface.aperture_u_axis)
    return surface.model_copy(update=update)


def placed_material(material: MaterialOpticalModel, pose: PoseV1) -> MaterialOpticalModel:
    """A member's material in the unit frame: a crystal's optic axis turned by the member's pose (``R(q) c``); an isotropic
    material as it is."""

    if isinstance(material, GRADIENT_MEDIA):  # (its axis in the unit frame: the member's local Z placed)
        rotation, position = rotation_matrix(pose.quaternion_wxyz), tuple(float(v) for v in pose.position_mm)
        return material.model_copy(update={"axis_point_mm": _move(rotation, position, material.axis_point_mm),
                                           "axis_direction": _rotate(rotation, material.axis_direction)})
    if not isinstance(material, UniaxialMaterial):
        return material
    rotation = rotation_matrix(pose.quaternion_wxyz)
    update: dict = {"optic_axis": _rotate(rotation, material.optic_axis)}
    if material.nonlinear is not None and material.nonlinear.crystal_x_axis is not None:
        update["nonlinear"] = material.nonlinear.model_copy(update={"crystal_x_axis": _rotate(rotation, material.nonlinear.crystal_x_axis)})
    return material.model_copy(update=update)


def _as_member(element: CompiledElement, member_id: str, pose: PoseV1) -> CompiledElement:
    """A member's compiled element: its surfaces (member frame) and envelopes renamed ``<member_id>-<surface_id>``."""

    prefix = f"{member_id}{MEMBER_SURFACE_SEPARATOR}"
    return replace(
        element,
        surfaces=tuple(s.model_copy(update={"surface_id": prefix + s.surface_id}) for s in element.surfaces),
        envelopes=tuple(replace(e, surface_id=prefix + e.surface_id) for e in element.envelopes),
        member_pose=pose,
    )


def _array_cells(member) -> tuple[tuple[str, PoseV1], ...]:
    """The cells of an arrayed compound member (``MemberArrayV1``): ``(<member_id>_<row>_<column>, pose)``, each the member's
    pose moved by the cell's offset in the unit frame (its orientation kept)."""

    position = member.pose.position_mm
    out = []
    with localcontext(DECIMAL_CONTEXT):
        for (_i, _j, dx, dy), cell_id in zip(member.array.cells(), member.cell_ids(), strict=True):
            # (the offset added to the stated coordinate in exact decimal arithmetic, rounded once)
            moved = (float(Decimal(repr(position[0])) + dx), float(Decimal(repr(position[1])) + dy), position[2])
            out.append((cell_id, PoseV1(position_mm=moved, quaternion_wxyz=member.pose.quaternion_wxyz)))
    return tuple(out)


def _compound(instance_id: str, identity: ElementIdentity, model: CompoundModelV1, resolver: ModelResolver, where: str) -> CompiledElement:
    members: list[CompiledElement] = []
    problems: list[LocalRunProblem] = []
    for member in model.members:
        declared = resolver.model(member.model_id)
        member_where = f"{where} member {member.member_id}"
        if declared is None or isinstance(declared.model, CompoundModelV1):
            problems.append(LocalRunProblem(LocalRunErrorCode.OPTICAL_MODEL_INVALID,
                                            f"member {member.member_id}: model {member.model_id!r} is not a declared model a unit can hold", member_where))
            continue
        cells = ((member.member_id, member.pose),) if member.array is None else _array_cells(member)
        for cell_id, pose in cells:
            try:
                compiled = compile_declared(instance_id, declared, resolver, where=member_where, member_id=cell_id)
            except LocalRunError as exc:
                problems.extend(exc.problems)
                break
            members.append(_as_member(compiled, cell_id, pose))
    if problems:
        raise LocalRunError(problems)
    return _unit(instance_id, identity, model, members, resolver, where)


def _unit(instance_id: str, identity: ElementIdentity, model: CompoundModelV1 | JoinedBodiesModelV1, members: list[CompiledElement],
          resolver: ModelResolver, where: str) -> CompiledElement:
    """One Engine component of compiled ``members`` (each with its ``member_pose``): their surfaces placed in the unit frame,
    the model's joints applied (``_join``)."""

    surfaces = [placed_surface(s, m.member_pose) for m in members for s in m.surfaces]  # type: ignore[arg-type]
    envelopes = [e for m in members for e in m.envelopes]
    materials = {material.material_id: placed_material(material, m.member_pose) for m in members for material in m.materials}
    for joint in model.joints or ():
        cement = _join(surfaces, envelopes, joint, resolver, where)
        if cement is not None:
            materials[cement.material_id] = cement
    return CompiledElement(instance_id=instance_id, identity=identity, model=model, surfaces=tuple(surfaces),
                           materials=tuple(materials[k] for k in sorted(materials)), envelopes=tuple(envelopes),
                           members=tuple(members), material_namespace=resolver.namespace)


#: The pose of a joined body in its element: the identity (its surfaces and body are stated in the element frame).
_BODY_POSE = PoseV1()


def _joined_bodies(instance_id: str, identity: ElementIdentity, model: JoinedBodiesModelV1, resolver: ModelResolver, where: str
                   ) -> CompiledElement:
    """A catalog ``JOINED_BODIES`` model (module docstring): each body a ``SURFACE_SET`` member at the identity pose, a crystal
    body's crystal cut by its optic axis (``CrystalCutResolver``, cut ``<instance>-<body>``), then the joints."""

    members: list[CompiledElement] = []
    problems: list[LocalRunProblem] = []
    for body in model.members:
        body_where = f"{where} body {body.member_id}"
        cut = f"{instance_id}{MEMBER_SURFACE_SEPARATOR}{body.member_id}"
        body_resolver = resolver if body.optic_axes is None else CrystalCutResolver(resolver, body.optic_axes, instance_id, cut)  # type: ignore[arg-type]
        body_identity = ElementIdentity(label=f"{identity.label} body {body.member_id}", optical_sku=identity.optical_sku,
                                        member_id=body.member_id)
        try:
            compiled = compile_model(instance_id, body.model, body_resolver, body_identity, where=body_where)
        except LocalRunError as exc:
            problems.extend(exc.problems)
            continue
        members.append(_as_member(compiled, body.member_id, _BODY_POSE))
    if problems:
        raise LocalRunError(problems)
    return _unit(instance_id, identity, model, members, resolver, where)


#: Two joined faces face each other when their unit front normals add up to at most this (an angle of about this in rad);
#: an optical contact's faces coincide within ``JOINT_GAP_TOLERANCE_MM`` along that normal, and a cement layer is thicker.
JOINT_ANGLE_TOLERANCE = 1e-6
JOINT_GAP_TOLERANCE_MM = 1e-6


def _join(surfaces: list, envelopes: list[SurfaceEnvelope], joint: MemberJointV1, resolver: ModelResolver, where: str
          ) -> MaterialOpticalModel | None:
    """Apply one ``MemberJointV1`` to a unit's placed surfaces (in place). An optical contact replaces the two faces by one
    interface (member A's face, member B's medium on its front side; B's face and its envelope leave the unit's surfaces); a
    cement layer puts the cement on the front side of both faces. Returns the cement's Engine material (``None`` for a
    contact). ``LocalRunError`` ``OPTICAL_MODEL_INVALID`` when the faces do not face each other at the joint's spacing."""

    ids = [s.surface_id for s in surfaces]
    names = (f"{joint.member_a}{MEMBER_SURFACE_SEPARATOR}{joint.surface_a}", f"{joint.member_b}{MEMBER_SURFACE_SEPARATOR}{joint.surface_b}")
    label = f"joint {joint.member_a}.{joint.surface_a} / {joint.member_b}.{joint.surface_b}"
    ia, ib = ids.index(names[0]), ids.index(names[1])
    a, b = surfaces[ia], surfaces[ib]
    if not (isinstance(a, PlaneSurface) and isinstance(b, PlaneSurface)) or ENGINE_AIR not in (a.medium_positive, b.medium_positive):
        raise _problem(LocalRunErrorCode.OPTICAL_MODEL_INVALID, f"{label}: the joined faces are plane body faces with air in front", where)
    facing = math.sqrt(sum((x + y) * (x + y) for x, y in zip(a.normal, b.normal)))
    if facing > JOINT_ANGLE_TOLERANCE:
        raise _problem(LocalRunErrorCode.OPTICAL_MODEL_INVALID,
                       f"{label}: the faces do not face each other in the unit frame (their front normals {a.normal} and {b.normal} "
                       "are not opposite)", where)
    gap = _dot(tuple(q - p for p, q in zip(a.origin_mm, b.origin_mm)), a.normal)
    if joint.cement_material_id is None:
        if abs(gap) > JOINT_GAP_TOLERANCE_MM:
            raise _problem(LocalRunErrorCode.OPTICAL_MODEL_INVALID,
                           f"{label}: an optical contact's faces coincide; face {names[1]} lies {gap:.6g} mm from face {names[0]}", where)
        surfaces[ia] = a.model_copy(update={"medium_positive": b.medium_negative})
        del surfaces[ib]
        del envelopes[ib]
        return None
    if not JOINT_GAP_TOLERANCE_MM < gap <= float(MAX_CEMENT_THICKNESS_MM):
        raise _problem(LocalRunErrorCode.OPTICAL_MODEL_INVALID,
                       f"{label}: the cement layer between the faces is {gap:.6g} mm thick; a cement layer is more than "
                       f"{JOINT_GAP_TOLERANCE_MM:g} mm and at most {MAX_CEMENT_THICKNESS_MM} mm thick", where)
    material = resolver.material(joint.cement_material_id)
    if material is None:
        raise _problem(resolver.missing_code, f"{label}: cement {joint.cement_material_id} {resolver.missing_text}", where)
    cement = resolver.engine_material(material)
    surfaces[ia] = a.model_copy(update={"medium_positive": cement.material_id})
    surfaces[ib] = b.model_copy(update={"medium_positive": cement.material_id})
    return cement


# --------------------------------------------------------------------------------------------- entry points


def select_model(product: PublicOpticalProductV2, model_kind: str | None = None, where: str | None = None
                 ) -> SurfaceSequenceModelV1 | SurfaceSetModelV1 | PolarizationElementModelV1 | NeutralDensityModelV1 | JoinedBodiesModelV1:
    """The one published simulation model to compile (of ``model_kind`` if given). ``LocalRunError``
    ``SIMULATION_MODEL_AMBIGUOUS`` / ``SIMULATION_MODEL_UNSUPPORTED`` otherwise."""

    models = [m for m in product.simulation_models if model_kind is None or m.model_kind == model_kind]
    if len(models) > 1:
        raise _problem(LocalRunErrorCode.SIMULATION_MODEL_AMBIGUOUS,
                       f"{product.optical_sku} publishes {len(models)} matching simulation models; choose one with model_kind", where or product.optical_sku)
    if not models:
        raise _problem(LocalRunErrorCode.SIMULATION_MODEL_UNSUPPORTED,
                       f"{product.optical_sku} publishes no simulation model of kind {model_kind!r}", where or product.optical_sku)
    (model,) = models
    if (model.model_kind, model.model_version) not in SUPPORTED_MODELS:
        raise _problem(LocalRunErrorCode.SIMULATION_MODEL_UNSUPPORTED,
                       f"simulation model {model.model_kind} v{model.model_version} is not supported by this runtime", where or product.optical_sku)
    return model


#: The compiler of each model kind ``compile_model`` compiles (catalog and declared).
MODEL_COMPILERS = {
    SURFACE_SEQUENCE: _surface_sequence, SURFACE_SET: _surface_set, POLARIZATION_ELEMENT: _polarization_element,
    NEUTRAL_DENSITY: _neutral_density, APERTURE_STOP: _aperture_stop, IDEAL_THIN_LENS: _thin_lens,
    TABULATED_THIN_LENS: _tabulated_thin_lens, FARADAY_ROTATOR: _faraday_rotator, DIFFRACTION_GRATING: _diffraction_grating, COMPOUND: _compound,
    FACET_SOLID: _facet_solid, NONLINEAR_CRYSTAL: _nonlinear_crystal, JOINED_BODIES: _joined_bodies,
}
#: The compilers that also take the location ``where`` (they name the member or surface they refuse by it).
_LOCATED_COMPILERS = frozenset({SURFACE_SEQUENCE, SURFACE_SET, COMPOUND, NONLINEAR_CRYSTAL, JOINED_BODIES})
#: Model kinds compiled as the physics of another kind (their Engine surfaces and views are that kind's): a nonlinear crystal
#: is its slab, a crystal surface sequence whose Engine crystal material carries the interaction.
COMPILED_AS = {NONLINEAR_CRYSTAL: SURFACE_SEQUENCE}


def compile_model(instance_id: str, model: CompiledModel, resolver: ModelResolver, identity: ElementIdentity, *,
                  where: str | None = None) -> CompiledElement:
    """Compile one optical ``model`` for the Project component ``instance_id``: its media and coatings (and a compound unit's
    member models) come from ``resolver``; ``identity`` names the element (module docstring). Raises ``LocalRunError``
    (stable codes) when the model cannot be compiled faithfully; ``SIMULATION_MODEL_UNSUPPORTED`` for a model kind this
    runtime does not simulate. Every field of the model kinds it compiles is simulated: the contract is bundled with the
    Engine, so the runtime never reads a field it does not simulate."""

    where = where or f"component {instance_id}"
    compiler = MODEL_COMPILERS.get(model.model_kind)
    if compiler is None:
        raise _problem(LocalRunErrorCode.SIMULATION_MODEL_UNSUPPORTED,
                       f"{identity.name}: this os-optics does not simulate a {model.model_kind} model", where)
    try:
        if model.model_kind in _LOCATED_COMPILERS:
            element = compiler(instance_id, identity, model, resolver, where)  # type: ignore[operator]
        else:
            element = compiler(instance_id, identity, model, resolver)  # type: ignore[operator]
    except ValidationError as exc:  # a value the Engine contracts reject (e.g. a zero clear radius)
        raise _problem(LocalRunErrorCode.ENGINE_INPUT_INVALID, f"{identity.name}: {exc.errors(include_url=False)}", where) from None
    # (the tolerances its coatings were declared with travel with the element: validity.py reads them from it)
    return element if element.design_point == resolver.design_point else replace(element, design_point=resolver.design_point)


def compile_element(instance_id: str, product: PublicOpticalProductV2, catalog: PublicOpticalCatalogV2, *, model_kind: str | None = None,
                    environment: PropagationEnvironmentV1 | None = None,
                    design_point: DesignPointTolerances = DEFAULT_DESIGN_POINT) -> CompiledElement:
    """Compile ``product`` (a product of ``catalog``, whose materials and coatings it uses) for the Project component
    ``instance_id``. The model is ``select_model(product, model_kind)``. ``environment`` gives the material temperature
    and ambient pressure (default: 20 C, 101325 Pa). ``design_point`` gives the tolerances of single design points.
    Raises ``LocalRunError`` (stable codes) when the product cannot be compiled faithfully from its public data."""

    where = f"component {instance_id}"
    model = select_model(product, model_kind, where)
    resolver = CatalogResolver(catalog, environment, design_point=design_point)
    return with_air(compile_model(instance_id, model, resolver, ElementIdentity.of_product(product), where=where), resolver.environment)


def with_air(element: CompiledElement, environment: PropagationEnvironmentV1) -> CompiledElement:
    """``element`` (and its members) recording the air of ``environment`` (``CompiledElement.air``)."""

    air = environment_air(environment)
    return replace(element, air=air, members=tuple(with_air(m, environment) for m in element.members))


def compile_declared(instance_id: str, declared: ProjectOpticalModelV1, resolver: ModelResolver, *, where: str | None = None,
                     member_id: str | None = None) -> CompiledElement:
    """Compile the Project's declared model ``declared`` (``ProjectDocumentV2.optical_models``) for the component
    ``instance_id`` (a ``COMPOUND`` unit's member ``member_id``), with its media and coatings from ``resolver`` (a
    ``ProjectResolver``). Its frame and design geometry are stated or derived (``declared_frame``,
    ``declared_design_geometry``)."""

    where = where or f"component {instance_id}"
    gradients: dict[str, tuple[float, float]] = {}
    if isinstance(declared.model, (SurfaceSequenceModelV1, SurfaceSetModelV1)):
        for material_id in declared.material_ids():
            material = resolver.material(material_id)
            if isinstance(material, ProjectModelMaterialV1) and material.is_gradient:
                radius, entrance, _z_low, _z_high = gradient_rod(declared, material_id)
                gradients[material_id] = (_number(radius), _number(entrance))
    if declared.optic_axes is not None or gradients:
        cut = instance_id if member_id is None else f"{instance_id}{MEMBER_SURFACE_SEPARATOR}{member_id}"
        resolver = CrystalCutResolver(resolver, declared.optic_axes or (), instance_id, cut, gradients)
    identity = ElementIdentity(label=declared.display_name or declared.model_id, optical_model_id=declared.model_id,
                               frame=declared_frame(declared, where), design_geometry=declared_design_geometry(declared, where),
                               member_id=member_id)
    return with_air(compile_model(instance_id, declared.model, resolver, identity, where=where), resolver.environment)


def _unit_decimal(values) -> tuple[float, float, float]:
    """A stated direction normalized in exact decimal arithmetic, then to the nearest binary64 numbers."""

    with localcontext(DECIMAL_CONTEXT):
        x, y, z = (Decimal(c) for c in values)
        norm = (x * x + y * y + z * z).sqrt()
        return _number(x / norm), _number(y / norm), _number(z / norm)


def nonlinear_interaction(model: NonlinearCrystalModelV1) -> SecondHarmonicGeneration:
    """The Engine ``SecondHarmonicGeneration`` of a declared nonlinear crystal (the same numbers; the wave names in lower case,
    the crystal X axis normalized)."""

    return SecondHarmonicGeneration(
        fundamental_waves=model.fundamental_waves.lower(), harmonic_wave=model.harmonic_wave.lower(),  # type: ignore[arg-type]
        d_eff_pm_per_V=None if model.d_eff_pm_per_V is None else _number(model.d_eff_pm_per_V),
        d_tensor_pm_per_V=None if model.d_tensor_pm_per_V is None else tuple(tuple(_number(v) for v in row) for row in model.d_tensor_pm_per_V),  # type: ignore[arg-type]
        crystal_x_axis=None if model.crystal_x_axis is None else _unit_decimal(model.crystal_x_axis),
        qpm_period_um=None if model.qpm_period_um is None else _number(model.qpm_period_um), qpm_order=model.qpm_order,
        focusing=model.focusing.lower())  # type: ignore[arg-type]


def crystal_temperature(model: NonlinearCrystalModelV1) -> CrystalTemperature | None:
    """The Engine ``CrystalTemperature`` of a declared nonlinear crystal (``None`` without thermo-optic data)."""

    thermo = model.thermo_optic
    if thermo is None or model.temperature_c is None:
        return None
    return CrystalTemperature(
        temperature_c=_number(model.temperature_c), reference_temperature_c=_number(thermo.reference_temperature_c),
        thermo_optic=tuple(ThermoOpticRow(wavelength_nm=_number(r.wavelength_nm), dno_dT_per_K=_number(r.dno_dT_per_K),
                                          dne_dT_per_K=_number(r.dne_dT_per_K)) for r in thermo.rows),
        thermal_expansion_per_K=0.0 if thermo.thermal_expansion_per_K is None else _number(thermo.thermal_expansion_per_K))


# ------------------------------------------------------------------------------------- declared frame and body


def _invalid(where: str, model_id: str, message: str) -> LocalRunError:
    return _problem(LocalRunErrorCode.OPTICAL_MODEL_INVALID, f"model {model_id}: {message}", where)


ZERO = Decimal(0)
ORIGIN = (ZERO, ZERO, ZERO)
PLUS_Z = (ZERO, ZERO, Decimal(1))
#: Decimal tolerance (mm) of a frame origin placed on a body's centroid or a decentred aperture centre (the catalog's).
FRAME_TOLERANCE_MM = Decimal("1e-6")


def _decimals(values) -> tuple[Decimal, ...]:
    return tuple(Decimal(v) for v in values)


def _face_frame(propagation: str, symmetry: OpticalSymmetry) -> OpticalFrameV1:
    return OpticalFrameV1(origin=FrameOrigin.REFLECTING_FACE_CENTER, nominal_propagation=propagation,
                          reference_axis=FrameReferenceAxis.FACE_NORMAL, symmetry=symmetry)


def _transmissive_frame(symmetry: OpticalSymmetry) -> OpticalFrameV1:
    return OpticalFrameV1(origin=FrameOrigin.FIRST_SURFACE_VERTEX, symmetry=symmetry)


def _face_propagation(traced: TracedBranches) -> str | None:
    if traced is TracedBranches.REFLECTED:
        return "REFLECTED_ABOUT_+Z"
    if traced is TracedBranches.TRANSMITTED_AND_REFLECTED:
        return "SPLIT_ABOUT_+Z"
    return None


def _coated_face(model: SurfaceSetModelV1, propagation: str | None = None):
    """A posed surface at the origin with normal +Z that reflects or splits (of the frame's propagation when given)."""

    return next((s for s in model.surfaces if _decimals(s.origin_mm) == ORIGIN and _decimals(s.normal) == PLUS_Z
                 and _face_propagation(s.traced) is not None and (propagation is None or _face_propagation(s.traced) == propagation)), None)


def _derived_frame(model, symmetry: OpticalSymmetry) -> OpticalFrameV1 | None:
    """The frame by rule (``declared_frame``), or None when no rule applies."""

    if isinstance(model, SurfaceSequenceModelV1):
        first, last = model.surfaces[0], model.surfaces[-1]
        propagation = _face_propagation(last.traced)
        if Decimal(last.vertex_z_mm) == 0 and propagation is not None:  # every other vertex lies at z < 0
            return _face_frame(propagation, symmetry)
        return _transmissive_frame(symmetry) if Decimal(first.vertex_z_mm) == 0 else None
    if isinstance(model, SurfaceSetModelV1):
        face = _coated_face(model)
        if face is not None:
            return _face_frame(_face_propagation(face.traced), symmetry)  # type: ignore[arg-type]
        return _transmissive_frame(symmetry) if _decimals(model.surfaces[0].origin_mm) == ORIGIN else None
    return _transmissive_frame(symmetry)


def _frame_problem(model, frame: OpticalFrameV1) -> str | None:
    """Why ``frame`` does not describe ``model`` (the catalog's frame rules that do not depend on a product form), or None."""

    symmetric = model.is_rotationally_symmetric
    if (frame.symmetry is OpticalSymmetry.NONE) == symmetric:
        return ("symmetry is NONE exactly when the element is not rotationally symmetric about local Z (an in-plane axis, a "
                "cylindrical surface, a rectangular or decentred aperture or outline, posed surfaces)")
    if isinstance(model, (PolarizationElementModelV1, NeutralDensityModelV1)):
        what = "a polarization element" if isinstance(model, PolarizationElementModelV1) else "a neutral-density attenuator"
        if frame.reference_axis is not None:
            return f"{what} has a transmissive frame (no reference_axis)"
        return None if Decimal(model.plane_z_mm) == 0 else _plane_problem(what, model.plane_z_mm)
    if isinstance(model, SurfaceSequenceModelV1):
        if frame.has_ports:
            return "an element placed by its ports is a SURFACE_SET model"
        if frame.is_reflector:
            last = model.surfaces[-1]
            if Decimal(last.vertex_z_mm) != 0 or _face_propagation(last.traced) != frame.nominal_propagation:
                return (f"a face-normal frame's coated face is the last surface, at z = 0, traced "
                        f"{'REFLECTED' if frame.nominal_propagation == 'REFLECTED_ABOUT_+Z' else 'TRANSMITTED_AND_REFLECTED'}")
            return None
        return None if Decimal(model.surfaces[0].vertex_z_mm) == 0 else "a transmissive frame's origin is the first surface vertex (z = 0)"
    if frame.reference_axis is None:
        return None if _decimals(model.surfaces[0].origin_mm) == ORIGIN else "a transmissive frame's origin is the first surface vertex (0, 0, 0)"
    if frame.is_reflector:
        return None if _coated_face(model, frame.nominal_propagation) is not None else (
            "a face-normal frame's coated face is a reflecting or splitting surface at the origin with normal (0, 0, 1)")
    return _ports_problem(model, frame)


def _plane_problem(what: str, plane_z_mm: str) -> str:
    """The rule a declared thin element's plane follows, stated plainly. For a polarization element it is the Engine's rule
    for declared models: the shared contract keeps ``plane_z_mm`` free because it is also the public catalog's field."""

    rule = (f"in a declared model, {what}'s plane is its frame origin, so plane_z_mm must be 0 (it is {plane_z_mm}): set "
            f"plane_z_mm to 0 and move the component {plane_z_mm} mm along its local +Z instead (its nominal pose places the plane)")
    if what == "a polarization element":
        rule += ("; this is the Engine's rule for declared polarization elements -- the contract field also describes catalog "
                 "products, whose published plane may lie elsewhere in their frame")
    return rule


def _ports_problem(model: SurfaceSetModelV1, frame: OpticalFrameV1) -> str | None:
    body = model.body
    with localcontext(DECIMAL_CONTEXT):
        if frame.origin is FrameOrigin.BODY_CENTER:
            if body is None:
                return "a BODY_CENTER frame needs the model's body"
            centre = body.volume_and_centroid()[1]
            if body.is_round:
                centre = (ZERO, ZERO, centre[2])
            if any(abs(c) > FRAME_TOLERANCE_MM for c in centre):
                return "a BODY_CENTER frame's origin is the centroid of the model's body (on the axis of a round body)"
        elif not any(s.aperture_center_mm is not None and all(abs(c) <= FRAME_TOLERANCE_MM for c in s.aperture_center_point())
                     for s in model.surfaces):
            return "an APERTURE_CENTER frame's origin is the centre of a decentred surface's clear aperture"
        if body is not None:
            for port in frame.ports or ():
                point, direction = _decimals(port.point_mm), _decimals(port.direction)
                if not body.on_boundary(point):
                    return f"port {port.port_id} lies on the body's surface"
                for index in body.faces_through(point):
                    along = sum(n * d for n, d in zip(_decimals(body.faces[index].outward_normal), direction))
                    if (along >= 0) if port.role == "INPUT" else (along <= 0):
                        return f"port {port.port_id} must {'enter' if port.role == 'INPUT' else 'leave'} the body through its face"
    return None


def declared_frame(declared: ProjectOpticalModelV1, where: str = "optical_models") -> OpticalFrameV1 | None:
    """The element frame of a declared model, stated (``frame``, checked against the model) or derived by rule:

    * ``SURFACE_SEQUENCE``: the face-normal frame of a reflector or plate splitter when its last surface lies at z = 0 and
      reflects (``REFLECTED_ABOUT_+Z``) or splits (``SPLIT_ABOUT_+Z``; a single surface, or a plate whose first surface
      lies at z < 0); otherwise the transmissive frame (origin ``FIRST_SURFACE_VERTEX``) when the first vertex is at z = 0;
    * ``SURFACE_SET``: the face-normal frame when a reflecting or splitting surface lies at the origin with normal +Z, else
      the transmissive frame when the first surface lies at the origin; an element placed by its ports states its frame;
    * ``POLARIZATION_ELEMENT``, ``APERTURE_STOP``, ``IDEAL_THIN_LENS``, ``TABULATED_THIN_LENS``, ``FARADAY_ROTATOR``: the
      transmissive frame (local +Z the axis), whose origin is the element's plane: ``plane_z_mm`` must be 0 (a stop, thin
      lens or Faraday rotator states no frame);
    * ``DIFFRACTION_GRATING``: a reflection grating the face-normal frame (local +Z the ruled face's outward normal), a
      transmission grating the transmissive frame, both with the origin on its plane (a stated frame only names its ports);
    * ``COMPOUND``: no element frame (each member has its own; the unit is placed by its frame and ports);
    * ``FACET_SOLID``: the frame of its corners (the component's pose places it); a stated frame only names its ports.

    ``symmetry`` is ``ROTATIONAL_ABOUT_LOCAL_Z`` exactly when the model is rotationally symmetric about local Z: the model
    decides it, so a stated frame (other than a ports frame, which is ``NONE``) whose flag differs is used with the derived
    one (``declared_model_notices``: ``FRAME_SYMMETRY_DERIVED``). A stated frame must satisfy the other rules (and, for a
    ports frame, the catalog's body-centre / aperture-centre and port rules). ``LocalRunError`` ``OPTICAL_MODEL_INVALID``
    when no rule applies or a stated frame contradicts the model."""

    if isinstance(declared.model, NonlinearCrystalModelV1):  # (its slab: a transmissive surface sequence)
        declared = declared.model_copy(update={"model": declared.model.as_surface_sequence()})
    model = declared.model
    if isinstance(model, CompoundModelV1):
        return None
    if isinstance(model, FacetSolidModelV1):
        if declared.frame is not None and not declared.frame.has_ports:
            raise _invalid(where, declared.model_id, "frame: a facet solid's frame is the frame of its corners: state one only to name "
                                                     "its ports (reference_axis PORTS)")
        return declared.frame
    if isinstance(model, DiffractionGratingModelV1):
        if declared.frame is not None:
            if not declared.frame.has_ports or declared.frame.origin is not FrameOrigin.APERTURE_CENTER:
                raise _invalid(where, declared.model_id, "frame: a diffraction grating's frame follows from its mode: state one only to name its "
                                                         "ports (origin APERTURE_CENTER, reference_axis PORTS)")
            return declared.frame
        if model.mode == "REFLECTION":
            return _face_frame("REFLECTED_ABOUT_+Z", OpticalSymmetry.NONE)
        return _transmissive_frame(OpticalSymmetry.NONE)
    if isinstance(model, (ApertureStopModelV1, IdealThinLensModelV1, TabulatedThinLensModelV1, FaradayRotatorModelV1)):
        symmetric = isinstance(model, (IdealThinLensModelV1, TabulatedThinLensModelV1, FaradayRotatorModelV1)) or (
            model.opening_radius_mm is not None and model.opening_center_mm is None and model.edge_radius_mm is not None and model.sample is None)
        frame = _transmissive_frame(OpticalSymmetry.ROTATIONAL_ABOUT_LOCAL_Z if symmetric else OpticalSymmetry.NONE)
        what = ("an ideal thin lens" if isinstance(model, IdealThinLensModelV1) else
                "a tabulated thin lens" if isinstance(model, TabulatedThinLensModelV1) else
                "a Faraday rotator" if isinstance(model, FaradayRotatorModelV1) else "an aperture stop")
        if Decimal(model.plane_z_mm) != 0:  # (the contract refuses a stated frame for these kinds)
            raise _invalid(where, declared.model_id, f"frame: {_plane_problem(what, model.plane_z_mm)}")
        return frame
    symmetry = OpticalSymmetry.ROTATIONAL_ABOUT_LOCAL_Z if model.is_rotationally_symmetric else OpticalSymmetry.NONE
    frame = declared.frame if declared.frame is not None else _derived_frame(model, symmetry)
    if frame is not None and not frame.has_ports and frame.symmetry is not symmetry:
        # (the model decides the symmetry: a stated flag that differs is replaced; declared_model_notices states both)
        frame = OpticalFrameV1.model_validate({**frame.model_dump(mode="json"), "symmetry": symmetry.value})
    if frame is None:
        raise _invalid(where, declared.model_id, "state its frame: no frame follows from the model (a transmissive element has its first "
                                                 "surface vertex at the origin; a reflector its coated face at the origin, normal +Z; an "
                                                 "element placed by its ports names them)")
    problem = _frame_problem(model, frame)
    if problem is not None:
        raise _invalid(where, declared.model_id, f"frame: {problem}")
    return frame


#: A derived design body is rounded outward to this many decimal places (mm) (``SurfaceSequenceModelV1.outline_z_range_mm``
#: rounds a surface sequence's extent to the same step).
BODY_QUANTUM = Decimal("1e-9")


def _bound(value: Decimal, rounding: str) -> str:
    return decimal_to_string(value.quantize(BODY_QUANTUM, rounding=rounding))


def _sequence_z_range(model: SurfaceSequenceModelV1) -> tuple[Decimal, Decimal]:
    """Smallest and largest local z of the model's surfaces over its whole outline, rounded outward: the contract's one
    derivation (``SurfaceSequenceModelV1.outline_z_range_mm``), which the server's reserved envelope also takes."""

    return model.outline_z_range_mm()


def _derived_geometry(model, where: str, model_id: str) -> DesignGeometryV1 | None:
    if isinstance(model, SurfaceSequenceModelV1):
        low, high = _sequence_z_range(model)
        if low == high:
            return None
        if model.edge_radius_mm is not None:
            return CylinderGeometryV1(diameter_mm=decimal_to_string(Decimal(model.edge_radius_mm) * 2), z_min_mm=_bound(low, ROUND_FLOOR),
                                      z_max_mm=_bound(high, ROUND_CEILING), status=FactStatus.NOMINAL)
        z_low, z_high = Decimal(_bound(low, ROUND_FLOOR)), Decimal(_bound(high, ROUND_CEILING))
        with localcontext(DECIMAL_CONTEXT):
            centre, half = (z_low + z_high) / 2, (z_high - z_low) / 2
        return BoxGeometryV1(center_mm=("0", "0", decimal_to_string(centre)),
                             half_extents_mm=(*model.edge_half_widths_mm, decimal_to_string(half)), status=FactStatus.NOMINAL)  # type: ignore[misc]
    if isinstance(model, SurfaceSetModelV1):
        if model.body is None:
            raise _invalid(where, model_id, "a SURFACE_SET model without a body states its design_geometry (the outer body of the element)")
        body = model.body
        if body.is_round:
            low, high = body.z_range_mm()
            return CylinderGeometryV1(diameter_mm=decimal_to_string(Decimal(body.side_radius_mm) * 2), z_min_mm=_bound(low, ROUND_FLOOR),  # type: ignore[arg-type]
                                      z_max_mm=_bound(high, ROUND_CEILING), status=FactStatus.NOMINAL)
        return ConvexPolyhedronGeometryV1(vertices_mm=tuple(tuple(decimal_to_string(c) for c in v) for v in body.vertices_mm()),
                                          status=FactStatus.NOMINAL)
    if isinstance(model, FacetSolidModelV1):
        return _facet_box(model)
    return None  # a plane element of zero thickness (polarization element, aperture stop, thin lens)


def _facet_box(model: FacetSolidModelV1) -> BoxGeometryV1:
    """The axis-aligned box around a facet solid's corners, rounded outward to ``BODY_QUANTUM``."""

    corners = [_decimals(v) for v in model.vertices_mm]
    low = [Decimal(_bound(min(c[k] for c in corners), ROUND_FLOOR)) for k in range(3)]
    high = [Decimal(_bound(max(c[k] for c in corners), ROUND_CEILING)) for k in range(3)]
    with localcontext(DECIMAL_CONTEXT):
        centre = tuple(decimal_to_string((lo + hi) / 2) for lo, hi in zip(low, high))
        half = tuple(decimal_to_string((hi - lo) / 2) for lo, hi in zip(low, high))
    return BoxGeometryV1(center_mm=centre, half_extents_mm=half, status=FactStatus.NOMINAL)  # type: ignore[arg-type]


def _plane_points(model) -> list[tuple[Decimal, Decimal, Decimal]]:
    """Points a stated body must contain: each surface vertex (or plane centre) and the outline's extreme points there."""

    if isinstance(model, SurfaceSequenceModelV1):
        planes = [Decimal(s.vertex_z_mm) for s in model.surfaces]
        reach = (Decimal(model.edge_radius_mm),) * 2 if model.edge_radius_mm is not None else _decimals(model.edge_half_widths_mm)  # type: ignore[arg-type]
    elif isinstance(model, (ApertureStopModelV1, NeutralDensityModelV1, DiffractionGratingModelV1)):
        planes = [Decimal(model.plane_z_mm)]
        reach = (Decimal(model.edge_radius_mm),) * 2 if model.edge_radius_mm is not None else _decimals(model.edge_half_widths_mm)  # type: ignore[arg-type]
    else:
        planes = [Decimal(model.plane_z_mm)]
        reach = (Decimal(model.edge_radius_mm),) * 2
    points = []
    for z in planes:
        points += [(ZERO, ZERO, z), (reach[0], ZERO, z), (-reach[0], ZERO, z), (ZERO, reach[1], z), (ZERO, -reach[1], z)]
    return points


def _extended(geometry: DesignGeometryV1, points: list[tuple[Decimal, Decimal, Decimal]]) -> DesignGeometryV1 | None:
    """The smallest body of ``geometry``'s kind that contains ``geometry`` and ``points`` (element frame), rounded outward to
    ``BODY_QUANTUM`` (status ``NOMINAL``): a cylinder about local Z, an axis-aligned box. ``None`` for a convex polyhedron
    (no union of its kind is formed)."""

    with localcontext(DECIMAL_CONTEXT):
        if isinstance(geometry, CylinderGeometryV1):
            radius = max([Decimal(geometry.diameter_mm) / 2] + [(p[0] * p[0] + p[1] * p[1]).sqrt() for p in points])
            low = min([Decimal(geometry.z_min_mm)] + [p[2] for p in points])
            high = max([Decimal(geometry.z_max_mm)] + [p[2] for p in points])
            return CylinderGeometryV1(diameter_mm=_bound(radius * 2, ROUND_CEILING), z_min_mm=_bound(low, ROUND_FLOOR),
                                      z_max_mm=_bound(high, ROUND_CEILING), status=FactStatus.NOMINAL)
        if isinstance(geometry, BoxGeometryV1):
            centre, half = _decimals(geometry.center_mm), _decimals(geometry.half_extents_mm)
            low = [Decimal(_bound(min([centre[k] - half[k]] + [p[k] for p in points]), ROUND_FLOOR)) for k in range(3)]
            high = [Decimal(_bound(max([centre[k] + half[k]] + [p[k] for p in points]), ROUND_CEILING)) for k in range(3)]
            return BoxGeometryV1(center_mm=tuple(decimal_to_string((lo + hi) / 2) for lo, hi in zip(low, high)),  # type: ignore[arg-type]
                                 half_extents_mm=tuple(decimal_to_string((hi - lo) / 2) for lo, hi in zip(low, high)),  # type: ignore[arg-type]
                                 status=FactStatus.NOMINAL)
    return None


def declared_design_geometry(declared: ProjectOpticalModelV1, where: str = "optical_models") -> DesignGeometryV1 | None:
    """The outer body of a declared element (element frame): ``design_geometry`` as stated when it contains the model (every
    surface vertex and the outline there, a surface sequence's surfaces over its whole outline
    (``SurfaceSequenceModelV1.outline_points_mm``), a surface set's body); a stated cylinder or box that does not is extended
    to the smallest body of its kind that contains both (``_extended``; ``declared_model_notices``:
    ``DESIGN_GEOMETRY_EXTENDED``), so the solid check and every reserved envelope work with a body that holds the glass
    (a stated polyhedron that does not is refused); a ``COMPOUND`` unit's ``envelope``, or derived from the
    model (``status`` ``NOMINAL``): a surface sequence's outline (a cylinder of the edge diameter, a box of the edge half
    widths) between the smallest and largest z of its surfaces over the outline, rounded outward to ``BODY_QUANTUM``; a
    surface set's body (its cylinder, or the polyhedron of its corners). ``None``: an element of zero thickness without a
    stated body (a polarization element, an aperture stop, a thin lens, a Faraday rotator, a single plane surface)."""

    if isinstance(declared.model, NonlinearCrystalModelV1):  # (its slab: a box of its faces and length)
        declared = declared.model_copy(update={"model": declared.model.as_surface_sequence()})
    model = declared.model
    if isinstance(model, CompoundModelV1):
        return model.envelope
    geometry = declared.design_geometry
    if geometry is None:
        return _derived_geometry(model, where, declared.model_id)
    if isinstance(model, FacetSolidModelV1):
        corners = [_decimals(v) for v in model.vertices_mm]
        if not all(geometry.contains(v) for v in corners):
            extended = _extended(geometry, corners)
            if extended is not None:
                return extended
            raise _invalid(where, declared.model_id, "design_geometry must contain every corner of the facets (omit it to have it derived)")
        return geometry
    if isinstance(model, SurfaceSetModelV1):
        body = model.body
        if body is not None:
            if body.is_round:
                low, high = body.z_range_mm()
                radius = Decimal(body.side_radius_mm)  # type: ignore[arg-type]
                inside = isinstance(geometry, CylinderGeometryV1) and Decimal(geometry.diameter_mm) >= radius * 2 and (
                    Decimal(geometry.z_min_mm) - FRAME_TOLERANCE_MM <= low and high <= Decimal(geometry.z_max_mm) + FRAME_TOLERANCE_MM)
                points = [(r, s, z) for z in (low, high) for r, s in ((radius, ZERO), (-radius, ZERO), (ZERO, radius), (ZERO, -radius))]
            else:
                points = list(body.vertices_mm())
                inside = all(geometry.contains(v) for v in points)
            if not inside:
                extended = _extended(geometry, points)
                if extended is None:
                    raise _invalid(where, declared.model_id, "design_geometry must contain the model's body")
                return extended
        return geometry
    points = _plane_points(model) + (list(model.outline_points_mm()) if isinstance(model, SurfaceSequenceModelV1) else [])
    if not all(geometry.contains(p) for p in points):
        extended = _extended(geometry, points)
        if extended is not None:
            return extended
    if not all(geometry.contains(p) for p in _plane_points(model)):
        raise _invalid(where, declared.model_id, "design_geometry must contain every surface vertex and the element's outline there")
    if isinstance(model, SurfaceSequenceModelV1):
        missed = [p for p in model.outline_points_mm() if not geometry.contains(p)]
        if missed:
            outside = max(missed, key=lambda p: (abs(p[2]), p[0] * p[0] + p[1] * p[1]))  # the farthest surface point it misses
            low, high = model.outline_z_range_mm()
            raise _invalid(where, declared.model_id,
                           f"design_geometry must contain the element's surfaces over its whole outline: they reach z = "
                           f"{decimal_to_string(outside[2].quantize(Decimal('1e-6')))} mm at (x, y) = ({decimal_to_string(outside[0].quantize(Decimal('1e-6')))}, "
                           f"{decimal_to_string(outside[1].quantize(Decimal('1e-6')))}) mm; the surfaces span z = {decimal_to_string(low)} .. "
                           f"{decimal_to_string(high)} mm (omit design_geometry to have it derived)")
    return geometry


#: A stated frame's symmetry flag differs from the model's: the derived one is used (``declared_frame``).
FRAME_SYMMETRY_DERIVED = "FRAME_SYMMETRY_DERIVED"
#: A stated design_geometry does not hold the model: the smallest body of its kind that does is used
#: (``declared_design_geometry``).
DESIGN_GEOMETRY_EXTENDED = "DESIGN_GEOMETRY_EXTENDED"


def declared_model_notices(declared: ProjectOpticalModelV1) -> tuple[dict[str, Any], ...]:
    """What the runtime used instead of a stated fact of ``declared`` that contradicts its model: ``FRAME_SYMMETRY_DERIVED``
    (``model_id``, ``stated``, ``derived``) and ``DESIGN_GEOMETRY_EXTENDED`` (``model_id``, ``stated`` and ``used``: the two
    bodies). Empty when every stated fact is used as stated, or when the model is refused for another reason."""

    notices: list[dict[str, Any]] = []
    try:
        if declared.frame is not None:
            frame = declared_frame(declared)
            if frame is not None and frame.symmetry is not declared.frame.symmetry:
                notices.append({"code": FRAME_SYMMETRY_DERIVED, "model_id": declared.model_id,
                                "stated": declared.frame.symmetry.value, "derived": frame.symmetry.value})
        if declared.design_geometry is not None and not isinstance(declared.model, CompoundModelV1):
            used = declared_design_geometry(declared)
            if used is not None and used != declared.design_geometry:
                notices.append({"code": DESIGN_GEOMETRY_EXTENDED, "model_id": declared.model_id,
                                "stated": declared.design_geometry.model_dump(mode="json"), "used": used.model_dump(mode="json")})
    except LocalRunError:
        return ()
    return tuple(notices)


# ------------------------------------------------------------------------------------------------ paraxial


Matrix = tuple[tuple[float, float], tuple[float, float]]
IDENTITY: Matrix = ((1.0, 0.0), (0.0, 1.0))
#: Why ``paraxial_matrix`` refuses a surface whose low-order terms carry power it cannot state as one number per section.
PARAXIAL_SURFACE_NOT_SUPPORTED = "PARAXIAL_SURFACE_NOT_SUPPORTED"


class ParaxialSurfaceNotSupported(ValueError):
    """An element with a Zernike or XY polynomial surface has no paraxial matrix (``PARAXIAL_SURFACE_NOT_SUPPORTED``)."""


def _multiply(a: Matrix, b: Matrix) -> Matrix:
    return ((a[0][0] * b[0][0] + a[0][1] * b[1][0], a[0][0] * b[0][1] + a[0][1] * b[1][1]),
            (a[1][0] * b[0][0] + a[1][1] * b[1][0], a[1][0] * b[0][1] + a[1][1] * b[1][1]))


def reflecting_surface(element: CompiledElement) -> PrescriptionSurfaceV1 | None:
    """The reflector of a ``SURFACE_SEQUENCE`` element: its last surface traced ``REFLECTED`` (the one nearest the +Z side the
    light comes from); ``None`` for every other element."""

    if not isinstance(element.model, SurfaceSequenceModelV1):
        return None
    return next((s for s in reversed(element.model.surfaces) if s.traced is TracedBranches.REFLECTED), None)


def paraxial_matrix(element: CompiledElement, wavelength_nm: float, *, axis: str | None = None,
                    air: MaterialOpticalModel | None = None) -> Matrix:
    """Paraxial reduced ABCD matrix ``((A, B), (C, D))`` of an element at ``wavelength_nm``, from the plane of its first
    vertex to the plane of its last vertex, in air on both sides, for light travelling along local +Z, in the section along
    local ``axis`` (``"X"`` / ``"Y"``; ``None`` for a rotationally symmetric power). A reflector takes the light arriving
    along local -Z on the side facing local +Z and is the matrix at its vertex on the unfolded axis: vertex radius ``R``
    (positive: centre of curvature on the +Z side, a concave mirror of focal length ``R / 2``) gives
    ``((1, 0), (-2 / R, 1))``, a plane mirror the identity.

    Rays ``(y, n u)``: refraction at a surface of vertex radius ``R`` is ``((1, 0), (-(n2 - n1) / R, 1))`` (a conic constant
    and polynomial terms do not change the paraxial power; a cylinder has this power in the section along its curved axis
    and none along its straight axis; a biconic has ``1 / radius_x_mm`` along X and ``1 / radius_y_mm`` along Y; a toroid
    ``1 / radius_of_rotation_mm`` along X and ``1 / radius_y_mm + 2 y_terms[0]`` along Y), a gap ``t`` in index ``n`` is ``((1, t / n), (0, 1))``. The effective focal length
    is ``-1 / C``, the back focal distance (from the last vertex) ``-A / C``, the front focal distance (to the first
    vertex) ``-D / C``; the matrix of the element turned around (light along local -Z) is ``((D, B), (C, A))``. A
    polarization element, a Faraday rotator, a neutral-density attenuator and an aperture stop are the identity (``ValueError`` for a stop
    whose sample has a phase across it), an ideal thin lens of focal
    length ``f`` is ``((1, 0), (-1 / f, 1))``, a tabulated thin lens the same with its focal length at ``wavelength_nm``
    (``ValueError`` outside its table's domain). Uses the Engine's own refractive-index evaluation of the compiled
    materials; ``ValueError`` for a ``SURFACE_SET`` or ``FACET_SOLID`` element, a ``COMPOUND`` unit (``paraxial_analysis`` composes its
    members from their poses), a reflector the light reaches through other surfaces of the element, an element with
    cylindrical, biconic or toroidal surfaces without an ``axis``, an element with an axicon surface (a cone has no paraxial
    power: it deflects every ray by the same angle), or a wavelength outside a material's data;
    ``ParaxialSurfaceNotSupported`` (a ``ValueError``) for an element with a Zernike or XY polynomial surface: their low-order
    terms move, tilt and bend the surface at the vertex.

    Media: every index is the absolute index the trace uses (the one material-evaluation rule, ``optics/materials/index.py``)
    divided by the absolute index of the surrounding air ``air`` (the Engine material of the Project's environment,
    ``environment_air``; default: the air the element was compiled for, ``CompiledElement.air``) at the same vacuum wavelength, so the air around the element has
    index exactly 1 in the matrix and its numbers are distances and angles in that air: ``-1 / C`` is the focal length in it.
    A ``VACUUM`` gap has ``1 / n_air``. Snell's law and every distance depend only on these ratios, so the matrix is the
    paraxial limit of the trace in that air; at zero pressure (vacuum) the indices are the absolute ones.
    """

    if axis not in PARAXIAL_AXES:
        raise ValueError(f"axis must be None, 'X' or 'Y', got {axis!r}")
    name = element.identity.name
    if isinstance(element.model, ApertureStopModelV1) and element.model.sample is not None and (
            element.model.sample.phase_terms is not None or (element.model.sample.sampled_map is not None
                                                             and element.model.sample.sampled_map.phase_rad is not None)):
        raise ValueError(f"{name}: the sample in its opening has a phase across it, which has no paraxial matrix (trace it)")
    if isinstance(element.model, (PolarizationElementModelV1, ApertureStopModelV1, NeutralDensityModelV1, FaradayRotatorModelV1)):
        return IDENTITY
    if isinstance(element.model, IdealThinLensModelV1):
        return ((1.0, 0.0), (-1.0 / _number(element.model.focal_length_mm), 1.0))
    if isinstance(element.model, TabulatedThinLensModelV1):
        interaction = element.surfaces[0].interaction
        table = ThinLensTable(interaction.wavelength_nm, interaction.focal_length_mm, interaction.transmittance,  # type: ignore[union-attr]
                              interaction.focal_interpolation, interaction.transmittance_interpolation, interaction.point_tolerance)  # type: ignore[union-attr]
        focal, _, inside = response_at(table, wavelength_nm)
        if not inside:
            raise ValueError(f"{name}: {wavelength_nm!r} nm is outside its response table ({table.domain_text()})")
        return ((1.0, 0.0), (-1.0 / focal, 1.0))
    if isinstance(element.model, DiffractionGratingModelV1):
        raise ValueError(f"{name}: a diffraction grating has no paraxial matrix of its own: each order turns the axis")
    if isinstance(element.model, (SurfaceSetModelV1, FacetSolidModelV1)):
        raise ValueError(f"{name} is a {element.model.model_kind} element: its surfaces need not be coaxial, so it has no paraxial matrix")
    if isinstance(element.model, CompoundModelV1):
        raise ValueError(f"{name} is a {element.model.model_kind} unit: paraxial_analysis composes its members from their poses")
    model = element.model
    if any(s.is_axicon for s in model.surfaces):
        raise ValueError(f"{name} has an axicon surface: a cone deflects every ray by the same angle, so it has no paraxial matrix")
    if any(s.is_freeform and not s.has_section_powers for s in model.surfaces):
        raise ParaxialSurfaceNotSupported(f"{PARAXIAL_SURFACE_NOT_SUPPORTED}: {name} has a Zernike or XY polynomial surface: its terms move, "
                                          "tilt and bend the surface at the vertex, so it has no paraxial matrix; trace it instead")
    if axis is None and any(s.is_cylindrical or s.has_section_powers for s in model.surfaces):
        raise ValueError(f"{name} has cylindrical, biconic or toroidal surfaces: give the section axis ('X' or 'Y')")
    reflector = reflecting_surface(element)
    if reflector is not None:
        if reflector is not model.surfaces[-1]:
            raise ValueError(f"{name} surface {reflector.surface_id} is a reflector the light reaches through other surfaces of the element")
        if reflector.has_section_powers:
            curvature = _section_curvature(reflector.sag, axis)  # type: ignore[arg-type]
            return IDENTITY if curvature == 0.0 else ((1.0, 0.0), (-2.0 * curvature, 1.0))
        if reflector.radius_mm is None or (reflector.curved_axis is not None and reflector.curved_axis != axis):
            return IDENTITY
        return ((1.0, 0.0), (-2.0 / _number(reflector.radius_mm), 1.0))
    evaluators = {m.material_id: build_evaluator(m, DEFAULT_NUMERICAL_CONFIG) for m in element.materials}
    n_air = surrounding_index(air if air is not None else element.air, wavelength_nm)

    def index(material_id: str) -> float:
        if material_id == AIR:
            return 1.0
        if material_id == VACUUM:
            return 1.0 / n_air
        value, issue = evaluators[element.engine_material_id(material_id)].index(wavelength_nm)
        if value is None:
            raise ValueError(issue.message if issue is not None else f"no refractive index of {material_id} at {wavelength_nm} nm")
        return value / n_air

    gradients = {m.material_id: m for m in element.materials if isinstance(m, GradientIndexMaterial)}
    axial = {m.material_id: m for m in element.materials if isinstance(m, AxialGradientIndexMaterial)}
    matrix = IDENTITY
    for i, surface in enumerate(model.surfaces):
        before, after = _media(model, i)
        if i > 0:
            gap = _number(Decimal(surface.vertex_z_mm) - Decimal(model.surfaces[i - 1].vertex_z_mm))
            rod = gradients.get(element.engine_material_id(before)) if before not in RESERVED_MEDIA else None
            rod_z = axial.get(element.engine_material_id(before)) if before not in RESERVED_MEDIA else None
            if rod is not None:
                matrix = _multiply(gradient_gap_matrix(index(before), _gradient_at(rod, wavelength_nm), gap), matrix)
            elif rod_z is not None:
                terms = rod_z.coefficients_per_mm if n_air == 1.0 else tuple(a / n_air for a in rod_z.coefficients_per_mm)
                matrix = _multiply(axial_gap_matrix(index(before), terms, gap), matrix)
                # (the face behind an axial rod: the index there, n(L))
                n_back = index(before) + _axial_offset(terms, gap)
                if surface.radius_mm is not None and (surface.curved_axis is None or surface.curved_axis == axis):
                    matrix = _multiply(((1.0, 0.0), (-(index(after) - n_back) / _number(surface.radius_mm), 1.0)), matrix)
                continue
            else:
                matrix = _multiply(((1.0, gap / index(before)), (0.0, 1.0)), matrix)
        if surface.has_section_powers:
            curvature = _section_curvature(surface.sag, axis)  # type: ignore[arg-type]
            if curvature != 0.0:
                matrix = _multiply(((1.0, 0.0), (-(index(after) - index(before)) * curvature, 1.0)), matrix)
        elif surface.radius_mm is not None and (surface.curved_axis is None or surface.curved_axis == axis):
            power = (index(after) - index(before)) / _number(surface.radius_mm)
            matrix = _multiply(((1.0, 0.0), (-power, 1.0)), matrix)
    return matrix


def surrounding_index(air: MaterialOpticalModel | None, wavelength_nm: float) -> float:
    """The absolute index of the surrounding air ``air`` (``environment_air``; ``None``: air at 20 C and 101325 Pa) at a vacuum
    wavelength, by the Engine's evaluator (``ValueError`` outside its band)."""

    value, issue = build_evaluator(air if air is not None else environment_air(), DEFAULT_NUMERICAL_CONFIG).index(wavelength_nm)
    if value is None:
        raise ValueError(issue.message if issue is not None else f"no index of the surrounding air at {wavelength_nm} nm")
    return value


def gradient_gap_matrix(n0: float, g: float, length_mm: float) -> Matrix:
    """The reduced ABCD matrix (rays ``(y, n u)``) of a length ``L`` of a radial gradient-index medium on its axis,
    ``n(r)^2 = n0^2 (1 - (g r)^2)``: ``((cos gL, sin gL / (n0 g)), (-n0 g sin gL, cos gL))`` (a paraxial ray's period is
    ``2 pi / g``; Marchand, *Gradient Index Optics*, ch. 3). The phase ``gL`` is reduced with ``math.exact_trig``."""

    c, s = cos_sin(np.array(g * length_mm))
    c, s = float(c), float(s)
    return ((c, s / (n0 * g)), (-n0 * g * s, c))


#: Gauss-Legendre panels (8 points each) of the paraxial reduced length ``int dz / n(z)`` of an axial rod.
AXIAL_PARAXIAL_PANELS = 64


def _axial_offset(terms: tuple[float, ...], z: float) -> float:
    acc = 0.0
    for a in reversed(terms):
        acc = (acc + a) * z
    return acc


def axial_gap_matrix(n0: float, terms: tuple[float, ...], length_mm: float) -> Matrix:
    """The reduced ABCD matrix (rays ``(y, n u)``) of a length ``L`` of an axial gradient-index medium on its axis,
    ``n(z) = n0 + a1 z + a2 z^2 + a3 z^3``: the index does not vary across the axis, so a paraxial ray keeps its reduced slope
    ``n u`` and ``((1, int_0^L dz / n(z)), (0, 1))`` (Gauss-Legendre quadrature of the smooth ``1 / n``)."""

    nodes, weights = gauss_legendre_rule(8)
    total = 0.0
    for k in range(AXIAL_PARAXIAL_PANELS):
        for x, w in zip(nodes, weights):
            z = length_mm * (k + 0.5 * (float(x) + 1.0)) / AXIAL_PARAXIAL_PANELS
            total += 0.5 * float(w) * length_mm / AXIAL_PARAXIAL_PANELS / (n0 + _axial_offset(terms, z))
    return ((1.0, total), (0.0, 1.0))


def _gradient_at(material: GradientIndexMaterial, wavelength_nm: float) -> float:
    """``g`` of a gradient-index material at a vacuum wavelength (a constant, or linear between its rows)."""

    law = material.gradient
    if isinstance(law, ConstantGradient):
        return law.per_mm
    rows = list(zip(law.wavelength_nm, law.per_mm))
    for (w0, g0), (w1, g1) in zip(rows, rows[1:]):
        if w0 <= wavelength_nm <= w1:
            return g0 + (g1 - g0) * (wavelength_nm - w0) / (w1 - w0)
    raise ValueError(f"{wavelength_nm!r} nm is outside the gradient rows of {material.material_id}")


def _section_curvature(shape, axis: str) -> float:
    """The vertex curvature of a biconic's or a toroid's section along local ``axis`` (``paraxial_matrix``)."""

    if shape.kind == "BICONIC":
        radius = shape.radius_x_mm if axis == "X" else shape.radius_y_mm
        return 0.0 if radius is None else 1.0 / _number(radius)
    if axis == "X":
        return 0.0 if shape.radius_of_rotation_mm is None else 1.0 / _number(shape.radius_of_rotation_mm)
    base = 0.0 if shape.radius_y_mm is None else 1.0 / _number(shape.radius_y_mm)
    return base + (2.0 * _number(shape.y_terms[0]) if shape.y_terms else 0.0)
