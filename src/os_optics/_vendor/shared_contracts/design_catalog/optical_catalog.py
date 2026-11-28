"""`PublicOpticalCatalogV2`: the design-stage public optical product projection.

This is what a user's own Agent (Mode A), a local/headless workflow, or the Browser Designer (Mode B) consumes to choose
optical elements for optical design and simulation. Every field is explicitly defined, and ``extra="forbid"`` rejects
anything else.

Identity: ``optical_sku`` (``OS-OPT-...``) is the OpenSci optical product identity, built from the product's
engineering parameters. ``display_name`` is its English name and ``display_name_zh_cn`` the same name in Simplified
Chinese; both are generated from the product's specification.

Schema 3.0 publishes products as **data for the Engine's general physics**, not as per-SKU algorithms:

* ``materials``: dispersion models (Sellmeier coefficients or a PCHIP table) with their valid band;
* ``coatings``: power reflectance / transmittance per wavelength band (optionally per polarization, s and p), as a
  tabulated spectrum (optionally per polarization) or as s / p tables over wavelength and angle of incidence, with an
  angle-of-incidence range and, optionally, the coating's reflection / transmission phases and a bound of its group
  delay;
* four simulation model kinds:

  - ``SURFACE_SEQUENCE`` (model version 2): coaxial surfaces along local Z (vertex position, surface profile -- plane,
    sphere, conic, even asphere or cylinder --, clear aperture -- circle or rectangle, optionally decentred --, coating,
    which branches are traced) and the media between them, inside a round or rectangular outline. Lenses (singlets,
    cemented doublets, aspheres, cylindrical lenses), windows, plates, filters, dichroic mirrors, plate beamsplitters
    and mirrors are all instances;
  - ``SURFACE_SET`` (model version 1): surfaces with an explicit position and orientation in the element frame and,
    optionally, the convex solid body the light travels in. Cube splitters, prisms, wedged plates and off-axis mirror
    sections are instances;
  - ``POLARIZATION_ELEMENT``: a Jones-matrix retarder or linear polarizer at one plane;
  - ``NEUTRAL_DENSITY``: an attenuator at one plane whose optical density is fixed or varies with the position of the
    hit point (a variable or stepped filter wheel, a linear gradient filter);
  - ``JOINED_BODIES`` (catalog schema 3.5): two or more solid bodies (each a ``SURFACE_SET`` with its body) in the element
    frame, joined face to face by an optical contact or a cement layer, or apart across air, each body with the optic axis
    of every uniaxial crystal it is made of. Polarizing prisms (Glan-Taylor, Glan-laser, Glan-Thompson, Wollaston, Rochon)
    are instances;
* optionally, per product, the power ratings (laser-induced damage thresholds) the manufacturer states, with the
  conditions they hold for.

The local runtime lowers every product the same way (``opensci_engine.local``); a product only supplies numbers.

Schema versions: ``3.0``, ``3.1``, ``3.2``, ``3.3``, ``3.4``, ``3.5`` and ``3.6`` are read (``PUBLIC_OPTICAL_CATALOG_SCHEMA_VERSIONS``); a catalog document of
an earlier schema is not accepted. ``3.1`` adds optional fields only, and a document that uses any of them states
``schema_version = "3.1"`` (``catalog_schema_3_1_features``): polarization-resolved tabulated spectra, angle-resolved coating
tables, coating phases, the axicon term and conic / aspheric terms on cylindrical surfaces, band-resolved polarization
element transmittance, the ``NEUTRAL_DENSITY`` model kind and power ratings. ``3.2`` adds one optional field, a coating's
group-delay bound (``PublicCoatingV1.group_delay_bound_fs``); a document that uses it states ``schema_version = "3.2"``
(``catalog_schema_3_2_features``) and may use every 3.1 field. ``3.3`` adds one optional field, a surface-sequence
surface's own mechanical edge (``PrescriptionSurfaceV1.edge_radius_mm`` / ``edge_half_widths_mm``); a document that uses
it states ``schema_version = "3.3"`` (``catalog_schema_3_3_features``) and may use every 3.1 and 3.2 field. ``3.4`` adds one
optional field, a surface's two-variable sag shape (``sag``: Zernike, XY polynomial, biconic or toroid); a document that
uses it states ``schema_version = "3.4"`` (``catalog_schema_3_4_features``) and may use every earlier field. ``3.5`` adds
uniaxial crystal materials (``PublicMaterialV1.birefringence``: the extraordinary index law) and the ``JOINED_BODIES`` model
kind (bodies joined face to face, with optic axes); a document that uses them states ``schema_version = "3.5"``
(``catalog_schema_3_5_features``) and may use every earlier field. ``3.6`` adds unreferenced design materials, additional
material index laws and source conventions, measured tables, bulk loss, thermo-optic data and material identity. A document
using any of these features states ``schema_version = "3.6"`` (``catalog_schema_3_6_features``). Documents recorded in
earlier schema versions keep their existing payloads and hashes.

All derived checks below run in exact decimal arithmetic in ``DECIMAL_CONTEXT``, so a document is accepted or refused
identically on every runtime, with the same message.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, Context, Decimal, InvalidOperation, localcontext
from enum import StrEnum
from typing import Annotated, Any, ClassVar, Literal

from pydantic import Field, PrivateAttr, StringConstraints, field_validator, model_validator

from ..base import OmitAbsentFields, StrictContractModel
from ..common.decimal_string import OpticalDecimal, decimal_to_string, read_decimal
from ..common.text import AuthorName, AuthorText
from ..common.ordering import duplicate_keys
from ..common.polygons import MAX_POLYGON_VERTICES, polygon_problem
from ..common.sag_shapes import (
    BICONIC,
    DECIMAL_OPS,
    MAX_SAG_TERMS,
    MAX_TOROID_Y_TERMS,
    SAG_COMBINATION_NOT_SUPPORTED,
    SAG_OUTSIDE_DOMAIN,
    TOROIDAL,
    SagForm,
    sag_form,
    sag_value,
    xy_terms_problem,
    zernike_terms_problem,
)
from .material_physics import (
    MAX_SELLMEIER_TERMS,
    BulkLossV1,
    HerzbergerDispersionV1,
    IndexReferenceV1,
    MaterialIdentityV1,
    PowerSeriesDispersionV1,
    TableMeasurementV1,
    TemperatureSellmeierDispersionV1,
    ThermoOpticV1,
)
from .enums import (
    CoatingAoiReference,
    CoatingKind,
    CommercialReadiness,
    DeliveredForm,
    FactStatus,
    FrameOrigin,
    FrameReferenceAxis,
    MechanicalAccessoryPolicy,
    ModelFidelity,
    OpenSciMounting,
    OpticalCategory,
    OpticalForm,
    OpticalSymmetry,
    SimulationApproximation,
)


PUBLIC_OPTICAL_CATALOG_SCHEMA_ID = "opensci.design_catalog.public_optical_catalog"
#: The catalog ``schema_version`` of a document that uses no 3.1 field (the default; see the module docstring).
PUBLIC_OPTICAL_CATALOG_SCHEMA_VERSION = "3.0"
#: The catalog ``schema_version`` a document that uses any 3.1 field (and no 3.2 field) states.
PUBLIC_OPTICAL_CATALOG_SCHEMA_VERSION_3_1 = "3.1"
#: The catalog ``schema_version`` a document that uses any 3.2 field (and no 3.3 field) states.
PUBLIC_OPTICAL_CATALOG_SCHEMA_VERSION_3_2 = "3.2"
#: The catalog ``schema_version`` a document that uses any 3.3 field (and no 3.4 field) states.
PUBLIC_OPTICAL_CATALOG_SCHEMA_VERSION_3_3 = "3.3"
#: The catalog ``schema_version`` a document that uses any 3.4 field (and no 3.5 field) states.
PUBLIC_OPTICAL_CATALOG_SCHEMA_VERSION_3_4 = "3.4"
#: The catalog ``schema_version`` a document that uses any 3.5 field (and no 3.6 feature) states.
PUBLIC_OPTICAL_CATALOG_SCHEMA_VERSION_3_5 = "3.5"
#: The catalog ``schema_version`` a document that publishes a design material (a material no product names) states.
PUBLIC_OPTICAL_CATALOG_SCHEMA_VERSION_3_6 = "3.6"
#: Every catalog ``schema_version`` this contract reads, oldest first.
PUBLIC_OPTICAL_CATALOG_SCHEMA_VERSIONS: tuple[str, ...] = (PUBLIC_OPTICAL_CATALOG_SCHEMA_VERSION, PUBLIC_OPTICAL_CATALOG_SCHEMA_VERSION_3_1,
                                                           PUBLIC_OPTICAL_CATALOG_SCHEMA_VERSION_3_2, PUBLIC_OPTICAL_CATALOG_SCHEMA_VERSION_3_3,
                                                           PUBLIC_OPTICAL_CATALOG_SCHEMA_VERSION_3_4, PUBLIC_OPTICAL_CATALOG_SCHEMA_VERSION_3_5,
                                                           PUBLIC_OPTICAL_CATALOG_SCHEMA_VERSION_3_6)

OpticalSku = Annotated[
    str,
    StringConstraints(strict=True, min_length=8, max_length=120, pattern=r"^OS-OPT(-[A-Z0-9]+)+$"),
    Field(description="OpenSci optical product number: 'OS-OPT-' followed by uppercase letter and digit segments."),
]

CatalogVersion = Annotated[
    str,
    StringConstraints(strict=True, pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}\.[1-9][0-9]*$"),
    Field(description="Catalog release label '<YYYY-MM-DD>.<n>'."),
]

MaterialId = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=64, pattern=r"^[A-Z0-9][A-Z0-9_.-]*$"),
    Field(description="Identifier of a published material model (uppercase), e.g. 'N-BK7', 'FUSED_SILICA'."),
]
#: The surrounding medium between surfaces of an air-spaced element and outside every element: air at the conditions of
#: the Project's propagation environment (``optical_design.optical_model.PropagationEnvironmentV1``; air at zero pressure is
#: vacuum).
AIR = "AIR"
#: Vacuum, whatever the environment: a gap of a Project's declared model may state it (an evacuated space).
VACUUM = "VACUUM"
#: Media names no material may take.
RESERVED_MEDIA = frozenset({AIR, VACUUM})

CoatingId = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=96, pattern=r"^[A-Z0-9][A-Z0-9_.-]*$"),
    Field(description="Identifier of a published coating model (uppercase)."),
]

SurfaceId = Annotated[str, StringConstraints(strict=True, pattern=r"^S[1-9][0-9]?$"), Field(description="Surface label 'S1', 'S2', ...")]
PortId = Annotated[
    str,
    StringConstraints(strict=True, max_length=16, pattern=r"^(IN|OUT(_[A-Z0-9]+)?)$"),
    Field(description="Port label: 'IN' (the nominal input), 'OUT' or 'OUT_<NAME>' (an output, e.g. 'OUT_T', 'OUT_R')."),
]
DecimalPair = tuple[OpticalDecimal, OpticalDecimal]
DecimalTriple = tuple[OpticalDecimal, OpticalDecimal, OpticalDecimal]
CylinderAxis = Literal["X", "Y"]

# Arithmetic on published values runs in this fixed context, never the caller's thread context, so a derived
# value is the same on every runtime whatever ``decimal`` settings are active.
DECIMAL_CONTEXT = Context(prec=28, rounding=ROUND_HALF_EVEN)

# Public catalog text is generated English: printable ASCII only, with at least one visible character. This also keeps
# look-alike and compatibility-character spellings (full-width, mathematical, Cyrillic look-alikes) out of the catalog.
_PUBLIC_ASCII = r"^[ -~]*[!-~][ -~]*$"
_PUBLIC_ASCII_TEXT = re.compile(_PUBLIC_ASCII)
PublicName = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=200, pattern=_PUBLIC_ASCII)]
PublicText = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=1000, pattern=_PUBLIC_ASCII)]
# The generated Simplified Chinese name: printable ASCII (numbers, units, glass names) plus CJK unified ideographs and the
# Chinese comma / enumeration comma only (no full-width forms of ASCII characters).
_PUBLIC_ZH_CN = r"^[ -~、一-鿿，]*[!-~、一-鿿，][ -~、一-鿿，]*$"
PublicNameZhCn = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=200, pattern=_PUBLIC_ZH_CN),
    Field(description="Simplified Chinese (zh-CN) display name."),
]

_PRISM_FORMS = frozenset({
    OpticalForm.RIGHT_ANGLE_PRISM, OpticalForm.PENTA_PRISM, OpticalForm.DOVE_PRISM, OpticalForm.EQUILATERAL_PRISM,
    OpticalForm.WEDGE_PRISM, OpticalForm.ROOF_PRISM, OpticalForm.CORNER_CUBE_RETROREFLECTOR,
})
#: Glan-type polarizers (one transmitted beam) and polarizing splitter prisms (two diverging beams): uniaxial crystal prisms
#: published as ``JOINED_BODIES`` models only (and only they publish that kind).
GLAN_PRISM_FORMS = frozenset({OpticalForm.GLAN_TAYLOR_PRISM, OpticalForm.GLAN_LASER_PRISM, OpticalForm.GLAN_THOMPSON_PRISM})
SPLITTING_PRISM_FORMS = frozenset({OpticalForm.WOLLASTON_PRISM, OpticalForm.ROCHON_PRISM})
POLARIZING_PRISM_FORMS = GLAN_PRISM_FORMS | SPLITTING_PRISM_FORMS
# The only forms each category may carry; a form under the wrong category is rejected.
FORMS_BY_CATEGORY: dict[OpticalCategory, frozenset[OpticalForm]] = {
    OpticalCategory.LENS: frozenset({
        OpticalForm.PLANO_CONVEX_LENS, OpticalForm.BICONVEX_LENS, OpticalForm.PLANO_CONCAVE_LENS, OpticalForm.BICONCAVE_LENS,
        OpticalForm.POSITIVE_MENISCUS_LENS, OpticalForm.NEGATIVE_MENISCUS_LENS, OpticalForm.BEST_FORM_LENS,
        OpticalForm.ACHROMATIC_DOUBLET, OpticalForm.ASPHERIC_LENS, OpticalForm.CYLINDRICAL_LENS, OpticalForm.AXICON,
    }),
    OpticalCategory.MIRROR: frozenset({OpticalForm.FLAT_MIRROR, OpticalForm.CONCAVE_MIRROR, OpticalForm.OFF_AXIS_PARABOLIC_MIRROR}),
    OpticalCategory.BEAMSPLITTER: frozenset({
        OpticalForm.PLATE_BEAMSPLITTER, OpticalForm.BEAM_SAMPLER, OpticalForm.CUBE_BEAMSPLITTER,
        OpticalForm.POLARIZING_CUBE_BEAMSPLITTER, OpticalForm.POLARIZING_PLATE_BEAMSPLITTER, *SPLITTING_PRISM_FORMS,
    }),
    OpticalCategory.FILTER: frozenset({
        OpticalForm.DICHROIC_MIRROR, OpticalForm.BANDPASS_FILTER, OpticalForm.LONGPASS_FILTER, OpticalForm.SHORTPASS_FILTER,
        OpticalForm.NOTCH_FILTER, OpticalForm.NEUTRAL_DENSITY_FILTER,
    }),
    OpticalCategory.WINDOW: frozenset({OpticalForm.PLANE_WINDOW}),
    OpticalCategory.WAVEPLATE: frozenset({OpticalForm.HALF_WAVE_PLATE, OpticalForm.QUARTER_WAVE_PLATE}),
    OpticalCategory.POLARIZER: frozenset({OpticalForm.LINEAR_POLARIZER, *GLAN_PRISM_FORMS}),
    OpticalCategory.PRISM: _PRISM_FORMS,
}
#: Forms whose frame is the reflector / splitter frame (``reference_axis = FACE_NORMAL``, origin on the coated face).
FACE_NORMAL_FORMS: frozenset[OpticalForm] = frozenset({
    OpticalForm.FLAT_MIRROR, OpticalForm.CONCAVE_MIRROR, OpticalForm.PLATE_BEAMSPLITTER, OpticalForm.BEAM_SAMPLER,
    OpticalForm.DICHROIC_MIRROR, OpticalForm.POLARIZING_PLATE_BEAMSPLITTER,
})
#: Nominal propagation of a face-normal form: a mirror only reflects; the others also transmit.
REFLECT_ONLY_FORMS: frozenset[OpticalForm] = frozenset({OpticalForm.FLAT_MIRROR, OpticalForm.CONCAVE_MIRROR})
#: Forms placed by their ports (``reference_axis = PORTS``) and the frame origin each uses.
PORTS_FORM_ORIGINS: dict[OpticalForm, FrameOrigin] = {
    OpticalForm.CUBE_BEAMSPLITTER: FrameOrigin.BODY_CENTER,
    OpticalForm.POLARIZING_CUBE_BEAMSPLITTER: FrameOrigin.BODY_CENTER,
    OpticalForm.OFF_AXIS_PARABOLIC_MIRROR: FrameOrigin.APERTURE_CENTER,
    **{form: FrameOrigin.BODY_CENTER for form in _PRISM_FORMS},
}
PORTS_FORMS: frozenset[OpticalForm] = frozenset(PORTS_FORM_ORIGINS)
#: Lens forms whose surfaces are planes and spheres only.
SPHERICAL_LENS_FORMS: frozenset[OpticalForm] = FORMS_BY_CATEGORY[OpticalCategory.LENS] - {
    OpticalForm.ASPHERIC_LENS, OpticalForm.CYLINDRICAL_LENS, OpticalForm.AXICON}
# The simulation model kinds each category may publish (a NEUTRAL_DENSITY model: a NEUTRAL_DENSITY_FILTER only; a
# JOINED_BODIES model: a polarizing prism only).
MODEL_KINDS_BY_CATEGORY: dict[OpticalCategory, frozenset[str]] = {
    OpticalCategory.LENS: frozenset({"SURFACE_SEQUENCE"}),
    OpticalCategory.MIRROR: frozenset({"SURFACE_SEQUENCE", "SURFACE_SET"}),
    OpticalCategory.BEAMSPLITTER: frozenset({"SURFACE_SEQUENCE", "SURFACE_SET", "JOINED_BODIES"}),
    OpticalCategory.FILTER: frozenset({"SURFACE_SEQUENCE", "SURFACE_SET", "NEUTRAL_DENSITY"}),
    OpticalCategory.WINDOW: frozenset({"SURFACE_SEQUENCE", "SURFACE_SET"}),
    OpticalCategory.WAVEPLATE: frozenset({"POLARIZATION_ELEMENT"}),
    OpticalCategory.POLARIZER: frozenset({"POLARIZATION_ELEMENT", "JOINED_BODIES"}),
    OpticalCategory.PRISM: frozenset({"SURFACE_SET"}),
}
#: Project ``optical_role`` values (``shared_contracts.optical_design.OpticalRoleV1``) a product of each category may play.
#: A dichroic mirror is a wavelength-selective splitter, so FILTER products may also be placed as a ``splitter``; a prism
#: may also be placed as a fold ``mirror``.
DESIGN_ROLES_BY_CATEGORY: dict[OpticalCategory, frozenset[str]] = {
    OpticalCategory.LENS: frozenset({"lens"}),
    OpticalCategory.MIRROR: frozenset({"mirror"}),
    OpticalCategory.BEAMSPLITTER: frozenset({"splitter"}),
    OpticalCategory.FILTER: frozenset({"filter", "splitter"}),
    OpticalCategory.WINDOW: frozenset({"window"}),
    OpticalCategory.WAVEPLATE: frozenset({"waveplate"}),
    OpticalCategory.POLARIZER: frozenset({"polarizer"}),
    OpticalCategory.PRISM: frozenset({"prism", "mirror"}),
}
#: Roles a product of a form may play beyond its category's (a polarizing splitter is also a polarizer).
EXTRA_DESIGN_ROLES_BY_FORM: dict[OpticalForm, frozenset[str]] = {
    OpticalForm.POLARIZING_CUBE_BEAMSPLITTER: frozenset({"polarizer"}),
    OpticalForm.POLARIZING_PLATE_BEAMSPLITTER: frozenset({"polarizer"}),
    **{form: frozenset({"polarizer"}) for form in SPLITTING_PRISM_FORMS},
}
# Largest angle of incidence an AngleRangeV1 may state (grazing).
MAX_AOI_DEG = Decimal(90)
MAX_SURFACES = 16
#: Even-asphere polynomial terms a surface may carry: A4 .. A18.
MAX_ASPHERIC_TERMS = 8
MAX_SPECTRUM_NODES = 4096
#: The most (wavelength, angle) nodes one angle-resolved coating table holds.
MAX_ANGULAR_SPECTRUM_NODES = 65536
#: The most entries one phase table, one band list of a polarization element and one stepped density law hold.
MAX_PHASE_NODES = MAX_SPECTRUM_NODES
MAX_POLARIZATION_BANDS = 64
MAX_DENSITY_STEPS = 64
MAX_POWER_RATINGS = 32
#: The largest magnitude of a surface's axicon term (``linear_sag_coefficient``, dz / d rho): a cone half-angle of
#: about 84 degrees.
MAX_LINEAR_SAG_COEFFICIENT = Decimal(10)
#: The unpolarized values of a polarization-resolved spectrum and the means of its s and p values agree to this.
SPECTRUM_POLARIZATION_MEAN_TOLERANCE = Decimal("1e-6")
MIN_BODY_FACES = 4
MAX_BODY_FACES = 32
#: Faces of a round body (``ConvexBodyV1.side_radius_mm``): its front and back face.
ROUND_BODY_FACES = 2
MIN_POLYHEDRON_VERTICES = 4
MAX_POLYHEDRON_VERTICES = 32
MAX_PORTS = 8
#: A unit vector's length may differ from 1 by this much (published decimals of irrational components).
UNIT_TOLERANCE = Decimal("1e-9")
#: Two unit vectors are perpendicular when their dot product is at most this, parallel when it is at least 1 minus this.
DIRECTION_TOLERANCE = Decimal("1e-8")
#: Positional allowance (mm) of the geometric consistency checks (a surface or port on its body, a frame origin).
GEOMETRY_TOLERANCE_MM = Decimal("1e-6")
# Allowance of the exact vertex / containment arithmetic (mm): rounding of published irrational components only.
_VERTEX_TOLERANCE_MM = Decimal("1e-9")
_SINGULAR = Decimal("1e-12")
_PI = Decimal("3.141592653589793238462643383")
#: s / p values and their published mean agree to this.
POLARIZATION_MEAN_TOLERANCE = Decimal("1e-12")
# Interpolated spectrum values are rounded down to this step, so a convex combination never exceeds R + T = 1.
_SPECTRUM_QUANTUM = Decimal("1e-18")
# Sample points per aperture radius / half width of the surface-gap check (boundary and interior).
_GAP_SAMPLES = 8
# Rational directions per eighth of a turn along which a surface with a two-variable sag shape is sampled (its sag is not
# monotone in the radius, nor even along one direction).
_SHAPE_DIRECTIONS_PER_OCTANT = 4
#: The most holes one surface states.
MAX_SURFACE_HOLES = 16
#: Sample points per outline radius (a round element, along local X, Y and the diagonals) and per outline half width (a
#: rectangular one, on a grid) of ``SurfaceSequenceModelV1.outline_points_mm``: the sag of a sphere or a conic is monotone
#: in the radius, so its extremes are among them; an even asphere is sampled there.
OUTLINE_SAMPLES = 64
RECT_OUTLINE_SAMPLES = 16
#: The axial extent of a model's glass (``outline_z_range_mm``) is rounded outward to this step (mm).
BODY_QUANTUM_MM = Decimal("1e-9")
# Rim points per half circle where a circular clear aperture's containment in a round body's side is checked when the
# exact bound (centre distance plus the aperture's largest radial extent) does not settle it (rational points of the
# circle: no trigonometry, identical on every runtime).
_APERTURE_RIM_STEPS = 360
# Terms of the Decimal power series of cos / sin (an angle of at most pi converges far below the context's precision).
_TRIG_TERMS = 40
#: Relative agreement of an off-axis paraboloid's published reflected focal length and decentre with the relations
#: RFL = 2 f / (1 + cos theta) and d = 2 f tan(theta / 2) of its parent focal length f and off-axis angle theta.
OAP_RELATION_TOLERANCE = Decimal("0.005")
_ZERO = Decimal(0)
_ONE = Decimal(1)
_TWO = Decimal(2)
_HALF = Decimal("0.5")
_DEG_180 = Decimal(180)


def parse_query_decimal(value: Decimal | str, label: str) -> Decimal:
    """A finite Decimal from caller input, or ValueError (never a bare decimal.InvalidOperation)."""

    try:
        number = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"{label} must be a finite decimal, got {value!r}") from None
    if not number.is_finite():
        raise ValueError(f"{label} must be a finite decimal, got {value!r}")
    return number


def _sorted_or_unchanged(value: object, key) -> object:
    """Sort a raw sequence into canonical order; leave malformed input for field validation to reject."""

    if not isinstance(value, (list, tuple)):
        return value
    try:
        return sorted(value, key=key)
    except (InvalidOperation, KeyError, AttributeError, TypeError, ValueError):
        return value


def _raw(item: object, field: str) -> object:
    return item[field] if isinstance(item, dict) else getattr(item, field)


def _positive(value: str, label: str) -> Decimal:
    number = Decimal(value)
    if number <= 0:
        raise ValueError(f"{label} must be positive, got {value}")
    return number


def _fraction(value: str, label: str) -> Decimal:
    number = Decimal(value)
    if not Decimal(0) <= number <= Decimal(1):
        raise ValueError(f"{label} is a power fraction in [0, 1], got {value}")
    return number


# ------------------------------------------------------------------------------------------- exact 3-vectors

Vec = tuple[Decimal, Decimal, Decimal]


def _vec(values: tuple[str, ...]) -> Vec:
    return Decimal(values[0]), Decimal(values[1]), Decimal(values[2])  # type: ignore[return-value]


def _add(a: Vec, b: Vec) -> Vec:
    return a[0] + b[0], a[1] + b[1], a[2] + b[2]


def _sub(a: Vec, b: Vec) -> Vec:
    return a[0] - b[0], a[1] - b[1], a[2] - b[2]


def _scale(a: Vec, s: Decimal) -> Vec:
    return a[0] * s, a[1] * s, a[2] * s


def _dot(a: Vec, b: Vec) -> Decimal:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Vec, b: Vec) -> Vec:
    return a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]


def _norm(a: Vec) -> Decimal:
    return _dot(a, a).sqrt()


def _combinations(items: list, size: int):
    """Every ``size``-element combination of ``items`` (2 or 3), in index order."""

    count = len(items)
    if size == 2:
        return [(items[i], items[j]) for i in range(count) for j in range(i + 1, count)]
    return [(items[i], items[j], items[k]) for i in range(count) for j in range(i + 1, count) for k in range(j + 1, count)]


def _cos_sin_deg(degrees: Decimal) -> tuple[Decimal, Decimal]:
    """``(cos, sin)`` of an angle in degrees by their power series in ``DECIMAL_CONTEXT`` (no libm: identical everywhere)."""

    with localcontext(DECIMAL_CONTEXT):
        x = degrees * _PI / _DEG_180
        cos, sin, term_c, term_s = _ZERO, _ZERO, _ONE, x
        for k in range(_TRIG_TERMS):
            cos += term_c
            sin += term_s
            term_c = -term_c * x * x / ((2 * k + 1) * (2 * k + 2))
            term_s = -term_s * x * x / ((2 * k + 2) * (2 * k + 3))
        return cos, sin


def _close(a: Vec, b: Vec, tolerance: Decimal) -> bool:
    return all(abs(x - y) <= tolerance for x, y in zip(a, b))


def _unit(values: tuple[str, ...], label: str) -> Vec:
    """The published unit vector, refused unless its length is 1 within ``UNIT_TOLERANCE``."""

    with localcontext(DECIMAL_CONTEXT):
        vector = _vec(values)
        if abs(_norm(vector) - _ONE) > UNIT_TOLERANCE:
            raise ValueError(f"{label} must be a unit vector (length 1 within {UNIT_TOLERANCE})")
        return vector


# --------------------------------------------------------------------------------------------- common values


class PublicValueV1(StrictContractModel):
    """One published scalar with its establishment status."""

    value: OpticalDecimal
    status: FactStatus


class PublicVector2V1(StrictContractModel):
    """Two published values ``(x, y)`` (local X, local Y) with their establishment status."""

    value: DecimalPair
    status: FactStatus


class PublicVector3V1(StrictContractModel):
    """Three published values ``(x, y, z)`` (local X, Y, Z) with their establishment status."""

    value: DecimalTriple
    status: FactStatus


class WavelengthBandV1(StrictContractModel):
    """A closed wavelength interval in nm (vacuum wavelength). ``min_nm == max_nm`` is a single design wavelength (for
    example a laser-line coating or a multi-order waveplate specified at its design wavelength only); the local runtime
    matches it within a per-model tolerance, except for filters, whose stated points are exact."""

    min_nm: OpticalDecimal
    max_nm: OpticalDecimal
    status: FactStatus

    @model_validator(mode="after")
    def _ordered(self) -> WavelengthBandV1:
        if not _positive(self.min_nm, "min_nm") <= _positive(self.max_nm, "max_nm"):
            raise ValueError("min_nm cannot exceed max_nm")
        return self

    def contains(self, wavelength_nm: Decimal) -> bool:
        return Decimal(self.min_nm) <= wavelength_nm <= Decimal(self.max_nm)

    def within(self, other: WavelengthBandV1) -> bool:
        return other.contains(Decimal(self.min_nm)) and other.contains(Decimal(self.max_nm))


class AngleRangeV1(StrictContractModel):
    """A closed angle-of-incidence interval in degrees (0 = normal incidence).

    For a coating the angle is the chief ray's angle to the surface normal, read as the coating's ``aoi_reference``
    says (by default its equivalent angle in air: ``n sin(theta)`` is invariant through a thin film, so a coating on
    the back of a plate is judged by the same number as on its front). ``min_deg == max_deg`` is a single design angle (a
    45 deg splitter coating); the local runtime matches it within a tolerance per coating kind: for an interference filter
    coating (dichroic, bandpass, edge, notch) the tilt that shifts its spectrum by a quarter of its narrowest band or
    transition gap, and none for a neutral-density coating.
    """

    min_deg: OpticalDecimal
    max_deg: OpticalDecimal
    status: FactStatus

    @model_validator(mode="after")
    def _ordered(self) -> AngleRangeV1:
        low, high = Decimal(self.min_deg), Decimal(self.max_deg)
        if not Decimal(0) <= low <= high <= MAX_AOI_DEG:
            raise ValueError(f"angle range must satisfy 0 <= min_deg <= max_deg <= {MAX_AOI_DEG}")
        return self

    def contains(self, angle_deg: Decimal) -> bool:
        return Decimal(self.min_deg) <= angle_deg <= Decimal(self.max_deg)


# ------------------------------------------------------------------------------------------------- materials


class SellmeierDispersionV1(StrictContractModel):
    """``n^2 = 1 + sum_i b_i l^2 / (l^2 - c_i)``, ``l`` the law's wavelength in micrometres (the vacuum wavelength unless the
    material's ``index_reference`` states air wavelengths), ``c_i`` in um^2; a term with ``c_i = 0`` is a constant term.
    Catalog schemas through 3.5 require three terms. Schema 3.6 allows one to ``MAX_SELLMEIER_TERMS`` terms."""

    kind: Literal["SELLMEIER"] = "SELLMEIER"
    b: tuple[OpticalDecimal, ...] = Field(min_length=1, max_length=MAX_SELLMEIER_TERMS)
    c_um2: tuple[OpticalDecimal, ...] = Field(min_length=1, max_length=MAX_SELLMEIER_TERMS)

    @model_validator(mode="after")
    def _terms(self) -> SellmeierDispersionV1:
        if len(self.b) != len(self.c_um2):
            raise ValueError("b and c_um2 must have equal length")
        return self


class TabulatedDispersionV1(OmitAbsentFields, StrictContractModel):
    """``n(lambda)`` by monotone PCHIP interpolation of a table (never extrapolated), on the law's wavelength.

    ``origin`` (schema 3.6): ``SOURCE_FORMULA`` (the meaning when absent) -- the table samples a source dispersion formula
    and ``max_interpolation_error`` is the largest difference, established by the publisher, between the interpolated table
    and that formula anywhere inside the valid band; ``MEASURED`` -- the table is the source's own measured index, its
    sample points as published (``measurement``: the conditions stated with them), with no formula to differ from:
    ``max_interpolation_error`` is then absent unless a separate reference establishes one. A table bounds the index only;
    its derivatives (group index, group-delay dispersion) are not bounded by it. A native Project may also use
    ``SOURCE_TABLE`` when the source supplies the nodes themselves and gives no measurement or formula metadata.
    """

    _omit_when_none: ClassVar[tuple[str, ...]] = ("origin", "max_interpolation_error", "measurement")

    kind: Literal["TABULATED"] = "TABULATED"
    wavelength_nm: tuple[OpticalDecimal, ...] = Field(min_length=2)
    refractive_index: tuple[OpticalDecimal, ...] = Field(min_length=2)
    interpolation: Literal["PCHIP"] = "PCHIP"
    max_interpolation_error: OpticalDecimal | None = None
    origin: Literal["SOURCE_FORMULA", "MEASURED", "SOURCE_TABLE"] | None = None
    measurement: TableMeasurementV1 | None = None

    @model_validator(mode="after")
    def _table(self) -> TabulatedDispersionV1:
        if len(self.wavelength_nm) != len(self.refractive_index):
            raise ValueError("wavelength_nm and refractive_index must have equal length")
        points = [Decimal(w) for w in self.wavelength_nm]
        if any(b <= a for a, b in zip(points, points[1:])):
            raise ValueError("wavelength_nm must be strictly increasing")
        for n in self.refractive_index:
            _positive(n, "refractive_index")
        if self.origin == "SOURCE_TABLE":
            if self.measurement is not None or self.max_interpolation_error is not None:
                raise ValueError("SOURCE_TABLE represents source nodes without measurement or formula-error metadata")
            return self
        measured = self.origin == "MEASURED"
        if not measured and self.max_interpolation_error is None:
            raise ValueError("a table of a source formula states its max_interpolation_error")
        if not measured and self.measurement is not None:
            raise ValueError("measurement belongs to a MEASURED table")
        if measured and self.measurement is None:
            raise ValueError("a MEASURED table states its measurement (the conditions and source)")
        return self

    @property
    def measured(self) -> bool:
        return self.origin == "MEASURED"


DispersionV1 = Annotated[
    SellmeierDispersionV1 | TabulatedDispersionV1 | PowerSeriesDispersionV1 | HerzbergerDispersionV1
    | TemperatureSellmeierDispersionV1,
    Field(discriminator="kind"),
]
#: Dispersion kinds catalog schema 3.6 adds.
SCHEMA_3_6_DISPERSION_KINDS = frozenset({"POWER_SERIES", "HERZBERGER", "TEMPERATURE_SELLMEIER"})


def dispersion_schema_3_6_features(dispersion: Any) -> tuple[str, ...]:
    """What a dispersion law uses of catalog schema 3.6: a new kind, a Sellmeier law of other than three terms, a measured
    table."""

    if dispersion.kind in SCHEMA_3_6_DISPERSION_KINDS:
        return (f"{dispersion.kind} dispersion",)
    if isinstance(dispersion, SellmeierDispersionV1) and len(dispersion.b) != 3:
        return (f"a {len(dispersion.b)}-term Sellmeier law",)
    if isinstance(dispersion, TabulatedDispersionV1) and (dispersion.origin is not None or dispersion.measurement is not None):
        return ("a table with a stated origin",)
    return ()


def law_band_problem(label: str, dispersion: Any, valid_min_nm: Decimal, valid_max_nm: Decimal,
                     index_reference: IndexReferenceV1 | None) -> str | None:
    """Why a law does not cover the material's valid band (vacuum wavelengths), on the law's own wavelength: a table must
    span it; a temperature law is a law like any other here. None when it does."""

    low, high = valid_min_nm, valid_max_nm
    if index_reference is not None:
        low, high = index_reference.law_wavelength_exact(low), index_reference.law_wavelength_exact(high)
    if isinstance(dispersion, TabulatedDispersionV1):
        table = dispersion.wavelength_nm
        if low < Decimal(table[0]) or high > Decimal(table[-1]):
            return f"{label}the valid band must lie inside the tabulated range"
    return None


class PublicBirefringenceV1(StrictContractModel):
    """A uniaxial crystal's second index law (catalog schema 3.5): ``extraordinary`` is the index of light polarized along
    the optic axis (a Sellmeier formula or a PCHIP table, as the ordinary index); the material's ``dispersion`` is then its
    ordinary index. Light in the crystal travels as an ordinary and an extraordinary wave; the element that uses the
    crystal states its optic axis (``JoinedBodyV1.optic_axes``)."""

    kind: Literal["UNIAXIAL"] = "UNIAXIAL"
    extraordinary: DispersionV1


def material_physics_problem(material: Any) -> str | None:
    """Why the optional physics of a published or declared material (``material_physics``: ``index_reference``,
    ``bulk_loss``, ``thermo_optic``, a temperature law) is inconsistent with its band and laws, or None. Shared by
    ``PublicMaterialV1`` and the Project's ``ProjectModelMaterialV1``."""

    low, high = Decimal(material.valid_band_nm.min_nm), Decimal(material.valid_band_nm.max_nm)
    reference = getattr(material, "index_reference", None)
    laws = [("", material.dispersion)]
    birefringence = getattr(material, "birefringence", None)
    if birefringence is not None:
        laws = [("ordinary index: ", material.dispersion), ("extraordinary index: ", birefringence.extraordinary)]
    with localcontext(DECIMAL_CONTEXT):
        for label, law in laws:
            problem = law_band_problem(label, law, low, high, reference)
            if problem is not None:
                return problem
        if reference is not None:
            problem = reference.check_vacuum_band(low, high)
            if problem is not None:
                return problem
    temperature_law = any(isinstance(law, TemperatureSellmeierDispersionV1) for _label, law in laws)
    if temperature_law and getattr(material, "thermo_optic", None) is not None:
        return "a temperature law (TEMPERATURE_SELLMEIER) states the index at every temperature: omit thermo_optic"
    if temperature_law and birefringence is not None:
        return "a uniaxial crystal's indices are not temperature laws"
    return None


def material_schema_3_6_features(material: Any) -> tuple[str, ...]:
    """What a published material uses of catalog schema 3.6 beyond being published for design."""

    features = [f"material {material.material_id}: {name}" for name in ("identity", "index_reference", "bulk_loss", "thermo_optic")
                if getattr(material, name, None) is not None]
    laws = [material.dispersion] + ([material.birefringence.extraordinary] if material.birefringence is not None else [])
    for law in laws:
        features += [f"material {material.material_id}: {name}" for name in dispersion_schema_3_6_features(law)]
    return tuple(features)


class PublicMaterialV1(OmitAbsentFields, StrictContractModel):
    """A published refractive material model. ``basis`` says how it is established (for example the Sellmeier formula
    of a glass catalogue). ``birefringence`` (catalog schema 3.5): the material is a uniaxial crystal
    (``PublicBirefringenceV1``) whose ``dispersion`` is its ordinary index; absent, the material is isotropic.

    Catalog schema 3.6 (``material_physics``): ``identity`` -- maker, exact designation, aliases, catalogue tags, variant
    base, ordering status and source edition; ``index_reference`` -- the wavelength and index convention of the law and the
    source's band (the Engine evaluates the law at the law's wavelength of each vacuum wavelength; ``valid_band_nm`` is
    the vacuum band); ``bulk_loss`` -- measured extinction coefficient or internal transmittance (absent: no stated loss,
    not zero loss); ``thermo_optic`` -- the change of the index with temperature."""

    # Serialized only when given: a material without it keeps its earlier payload.
    _omit_when_none: ClassVar[tuple[str, ...]] = ("birefringence", "identity", "index_reference", "bulk_loss", "thermo_optic")

    material_id: MaterialId
    display_name: PublicName
    dispersion: DispersionV1
    valid_band_nm: WavelengthBandV1
    status: FactStatus
    basis: PublicText
    birefringence: PublicBirefringenceV1 | None = None
    identity: MaterialIdentityV1 | None = None
    index_reference: IndexReferenceV1 | None = None
    bulk_loss: BulkLossV1 | None = None
    thermo_optic: ThermoOpticV1 | None = None

    @model_validator(mode="after")
    def _consistent(self) -> PublicMaterialV1:
        if not Decimal(self.valid_band_nm.min_nm) < Decimal(self.valid_band_nm.max_nm):
            raise ValueError(f"material {self.material_id}: the valid band must be an interval")
        if self.material_id in RESERVED_MEDIA:
            raise ValueError(f"{self.material_id} is a reserved medium, not a published material")
        laws = (self.dispersion, self.birefringence.extraordinary) if self.birefringence else (self.dispersion,)
        if any(isinstance(law, TabulatedDispersionV1) and law.origin == "SOURCE_TABLE" for law in laws):
            raise ValueError("SOURCE_TABLE is a native Project material origin")
        problem = material_physics_problem(self)
        if problem is not None:
            raise ValueError(f"material {self.material_id}: {problem}")
        return self

    @property
    def is_crystal(self) -> bool:
        """Whether the material is a uniaxial crystal (``birefringence`` stated)."""

        return self.birefringence is not None


# -------------------------------------------------------------------------------------------------- coatings


class CoatingBandV1(StrictContractModel):
    """Power reflectance / transmittance of a coating within one wavelength band.

    ``reflectance`` / ``transmittance`` apply to unpolarized light. When the coating is characterized per polarization,
    ``reflectance_s``, ``reflectance_p``, ``transmittance_s`` and ``transmittance_p`` are all present (s: electric field
    perpendicular to the plane of incidence, p: in it) and ``reflectance`` / ``transmittance`` are their means; absent,
    s and p have the same values.
    """

    min_nm: OpticalDecimal
    max_nm: OpticalDecimal
    reflectance: OpticalDecimal = Field(None, description="Unpolarized power reflectance; may be omitted when the four s and p "
                                        "values are stated (it is then their mean).")  # type: ignore[assignment]
    transmittance: OpticalDecimal = Field(None, description="Unpolarized power transmittance; may be omitted when the four s and "
                                          "p values are stated (it is then their mean).")  # type: ignore[assignment]
    reflectance_s: OpticalDecimal | None = None
    reflectance_p: OpticalDecimal | None = None
    transmittance_s: OpticalDecimal | None = None
    transmittance_p: OpticalDecimal | None = None
    status: FactStatus

    @model_validator(mode="before")
    @classmethod
    def _means_filled(cls, data: Any) -> Any:
        return _unpolarized_means_filled(data, per_row=False)

    @model_validator(mode="after")
    def _consistent(self) -> CoatingBandV1:
        if self.reflectance is None or self.transmittance is None:
            raise ValueError("a band states reflectance and transmittance, or all of reflectance_s, reflectance_p, transmittance_s "
                             "and transmittance_p (their means are then the unpolarized values)")
        if not _positive(self.min_nm, "min_nm") <= _positive(self.max_nm, "max_nm"):
            raise ValueError("min_nm cannot exceed max_nm")
        if _fraction(self.reflectance, "reflectance") + _fraction(self.transmittance, "transmittance") > 1:
            raise ValueError("reflectance + transmittance cannot exceed 1")
        resolved = (self.reflectance_s, self.reflectance_p, self.transmittance_s, self.transmittance_p)
        if any(value is not None for value in resolved):
            if any(value is None for value in resolved):
                raise ValueError("a polarization-resolved band states all of reflectance_s, reflectance_p, transmittance_s and transmittance_p")
            r_s, r_p = _fraction(self.reflectance_s, "reflectance_s"), _fraction(self.reflectance_p, "reflectance_p")  # type: ignore[arg-type]
            t_s, t_p = _fraction(self.transmittance_s, "transmittance_s"), _fraction(self.transmittance_p, "transmittance_p")  # type: ignore[arg-type]
            if r_s + t_s > 1 or r_p + t_p > 1:
                raise ValueError("reflectance + transmittance cannot exceed 1 for either polarization")
            with localcontext(DECIMAL_CONTEXT):
                if (abs(Decimal(self.reflectance) - (r_s + r_p) / _TWO) > POLARIZATION_MEAN_TOLERANCE
                        or abs(Decimal(self.transmittance) - (t_s + t_p) / _TWO) > POLARIZATION_MEAN_TOLERANCE):
                    raise ValueError("reflectance and transmittance must be the means of their s and p values")
        return self

    @property
    def polarization_resolved(self) -> bool:
        return self.reflectance_s is not None

    def contains(self, wavelength_nm: Decimal) -> bool:
        return Decimal(self.min_nm) <= wavelength_nm <= Decimal(self.max_nm)


_SPECTRUM_POLARIZED = ("reflectance_s", "reflectance_p", "transmittance_s", "transmittance_p")


def _interpolation_weights(nodes: list[Decimal], point: Decimal) -> tuple[int, int, Decimal] | None:
    """``(lower, upper, fraction)`` of ``point`` between neighbouring ``nodes`` (strictly increasing), ``lower == upper``
    on a node, or None outside ``[nodes[0], nodes[-1]]``. Runs in the caller's ``DECIMAL_CONTEXT``."""

    if not nodes[0] <= point <= nodes[-1]:
        return None
    upper = next(i for i, node in enumerate(nodes) if node >= point)
    if nodes[upper] == point:
        return upper, upper, _ZERO
    return upper - 1, upper, (point - nodes[upper - 1]) / (nodes[upper] - nodes[upper - 1])


def _floor_quantum(value: Decimal) -> Decimal:
    return value.quantize(_SPECTRUM_QUANTUM, rounding=ROUND_FLOOR).normalize()


class CoatingSpectrumV1(StrictContractModel):
    """A coating's tabulated spectrum: power ``reflectance`` / ``transmittance`` at strictly increasing vacuum
    wavelengths, linearly interpolated between neighbouring points. The coating is characterized on
    ``[wavelength_nm[0], wavelength_nm[-1]]`` only.

    ``reflectance`` / ``transmittance`` apply to unpolarized light. A spectrum measured per polarization also states
    ``reflectance_s``, ``reflectance_p``, ``transmittance_s`` and ``transmittance_p`` (all four, one value per
    wavelength; s: electric field perpendicular to the plane of incidence, p: in it); ``reflectance`` /
    ``transmittance`` are then their means (within ``SPECTRUM_POLARIZATION_MEAN_TOLERANCE``). Absent, s and p have the
    unpolarized values.
    """

    wavelength_nm: tuple[OpticalDecimal, ...] = Field(min_length=2, max_length=MAX_SPECTRUM_NODES)
    transmittance: tuple[OpticalDecimal, ...] = Field(
        None, min_length=2, max_length=MAX_SPECTRUM_NODES,
        description="Unpolarized power transmittance per wavelength; may be omitted when the four s and p columns are stated (it "
        "is then their mean).")  # type: ignore[assignment]
    reflectance: tuple[OpticalDecimal, ...] = Field(
        None, min_length=2, max_length=MAX_SPECTRUM_NODES,
        description="Unpolarized power reflectance per wavelength; may be omitted when the four s and p columns are stated (it "
        "is then their mean).")  # type: ignore[assignment]
    reflectance_s: tuple[OpticalDecimal, ...] | None = Field(None, min_length=2, max_length=MAX_SPECTRUM_NODES)
    reflectance_p: tuple[OpticalDecimal, ...] | None = Field(None, min_length=2, max_length=MAX_SPECTRUM_NODES)
    transmittance_s: tuple[OpticalDecimal, ...] | None = Field(None, min_length=2, max_length=MAX_SPECTRUM_NODES)
    transmittance_p: tuple[OpticalDecimal, ...] | None = Field(None, min_length=2, max_length=MAX_SPECTRUM_NODES)
    interpolation: Literal["LINEAR"] = "LINEAR"
    status: FactStatus

    @model_validator(mode="before")
    @classmethod
    def _means_filled(cls, data: Any) -> Any:
        return _unpolarized_means_filled(data, per_row=True)

    @model_validator(mode="after")
    def _table(self) -> CoatingSpectrumV1:
        if self.reflectance is None or self.transmittance is None:
            raise ValueError("a spectrum states reflectance and transmittance, or all of reflectance_s, reflectance_p, "
                             "transmittance_s and transmittance_p (their means are then the unpolarized values)")
        if not len(self.wavelength_nm) == len(self.transmittance) == len(self.reflectance):
            raise ValueError("wavelength_nm, transmittance and reflectance must have equal length")
        points = [_positive(w, "wavelength_nm") for w in self.wavelength_nm]
        if any(b <= a for a, b in zip(points, points[1:])):
            raise ValueError("wavelength_nm must be strictly increasing")
        for r, t in zip(self.reflectance, self.transmittance):
            if _fraction(r, "reflectance") + _fraction(t, "transmittance") > 1:
                raise ValueError("reflectance + transmittance cannot exceed 1")
        resolved = [getattr(self, name) for name in _SPECTRUM_POLARIZED]
        if any(values is not None for values in resolved):
            if any(values is None for values in resolved):
                raise ValueError("a polarization-resolved spectrum states all of reflectance_s, reflectance_p, transmittance_s and "
                                 "transmittance_p")
            if any(len(values) != len(self.wavelength_nm) for values in resolved):
                raise ValueError("reflectance_s, reflectance_p, transmittance_s and transmittance_p have one value per wavelength")
            with localcontext(DECIMAL_CONTEXT):
                for i, (r_s, r_p, t_s, t_p) in enumerate(zip(*resolved)):
                    r_s, r_p = _fraction(r_s, "reflectance_s"), _fraction(r_p, "reflectance_p")
                    t_s, t_p = _fraction(t_s, "transmittance_s"), _fraction(t_p, "transmittance_p")
                    if r_s + t_s > 1 or r_p + t_p > 1:
                        raise ValueError(f"reflectance + transmittance cannot exceed 1 for either polarization (at "
                                         f"{self.wavelength_nm[i]} nm)")
                    if (abs(Decimal(self.reflectance[i]) - (r_s + r_p) / _TWO) > SPECTRUM_POLARIZATION_MEAN_TOLERANCE
                            or abs(Decimal(self.transmittance[i]) - (t_s + t_p) / _TWO) > SPECTRUM_POLARIZATION_MEAN_TOLERANCE):
                        raise ValueError(f"reflectance and transmittance must be the means of their s and p values (within "
                                         f"{SPECTRUM_POLARIZATION_MEAN_TOLERANCE}; at {self.wavelength_nm[i]} nm)")
        return self

    @property
    def polarization_resolved(self) -> bool:
        return self.reflectance_s is not None

    @property
    def span_nm(self) -> tuple[Decimal, Decimal]:
        return Decimal(self.wavelength_nm[0]), Decimal(self.wavelength_nm[-1])

    def _interpolate(self, wavelength_nm: Decimal, columns: tuple[tuple[str, ...], ...]) -> tuple[Decimal, ...] | None:
        with localcontext(DECIMAL_CONTEXT):
            weights = _interpolation_weights([Decimal(w) for w in self.wavelength_nm], wavelength_nm)
            if weights is None:
                return None
            lower, upper, fraction = weights
            if lower == upper:
                return tuple(Decimal(values[upper]) for values in columns)
            return tuple(_floor_quantum(Decimal(values[lower]) + (Decimal(values[upper]) - Decimal(values[lower])) * fraction)
                         for values in columns)

    def values_at(self, wavelength_nm: Decimal) -> tuple[Decimal, Decimal] | None:
        """``(reflectance, transmittance)`` at ``wavelength_nm`` by linear interpolation (rounded down to 1e-18), or None
        outside the tabulated span."""

        return self._interpolate(wavelength_nm, (self.reflectance, self.transmittance))  # type: ignore[return-value]

    def polarized_values_at(self, wavelength_nm: Decimal) -> tuple[Decimal, Decimal, Decimal, Decimal] | None:
        """``(reflectance_s, reflectance_p, transmittance_s, transmittance_p)`` at ``wavelength_nm`` by linear
        interpolation (rounded down to 1e-18), or None outside the tabulated span; an unpolarized spectrum gives its
        unpolarized values for both polarizations."""

        if self.reflectance_s is None:
            values = self.values_at(wavelength_nm)
            return None if values is None else (values[0], values[0], values[1], values[1])
        return self._interpolate(wavelength_nm, tuple(getattr(self, name) for name in _SPECTRUM_POLARIZED))  # type: ignore[return-value]


AngularTable = tuple[tuple[OpticalDecimal, ...], ...]


class CoatingAngularSpectrumV1(StrictContractModel):
    """A coating's s / p power reflectance and transmittance tabulated over wavelength and angle of incidence.

    ``wavelength_nm`` (vacuum wavelengths) and ``aoi_deg`` (angles of incidence in degrees, 0 = normal incidence) are
    strictly increasing grids of at least two nodes each. Each of ``reflectance_s``, ``reflectance_p``,
    ``transmittance_s`` and ``transmittance_p`` holds one row per wavelength, each row one value per angle
    (``table[i][j]`` at ``wavelength_nm[i]``, ``aoi_deg[j]``), every value a power fraction in ``[0, 1]`` with
    ``R + T <= 1`` for each polarization at every node. s: electric field perpendicular to the plane of incidence, p: in
    it (the s / p triad of the Engine: ``s = d x n`` normalized, ``p = d x s``).

    ``aoi_axis = INCIDENT_MEDIUM``: the angle is the physical angle of incidence in the medium the light arrives from.
    Values between nodes are interpolated bilinearly in wavelength and angle; a wavelength or angle outside the grid is
    outside the model and never extrapolated. At most ``MAX_ANGULAR_SPECTRUM_NODES`` nodes.
    """

    wavelength_nm: tuple[OpticalDecimal, ...] = Field(min_length=2, max_length=MAX_ANGULAR_SPECTRUM_NODES // 2)
    aoi_deg: tuple[OpticalDecimal, ...] = Field(min_length=2, max_length=MAX_ANGULAR_SPECTRUM_NODES // 2)
    reflectance_s: AngularTable = Field(min_length=2, max_length=MAX_ANGULAR_SPECTRUM_NODES // 2)
    reflectance_p: AngularTable = Field(min_length=2, max_length=MAX_ANGULAR_SPECTRUM_NODES // 2)
    transmittance_s: AngularTable = Field(min_length=2, max_length=MAX_ANGULAR_SPECTRUM_NODES // 2)
    transmittance_p: AngularTable = Field(min_length=2, max_length=MAX_ANGULAR_SPECTRUM_NODES // 2)
    interpolation: Literal["BILINEAR"] = "BILINEAR"
    aoi_axis: Literal["INCIDENT_MEDIUM"] = "INCIDENT_MEDIUM"
    status: FactStatus

    @model_validator(mode="after")
    def _table(self) -> CoatingAngularSpectrumV1:
        rows, columns = len(self.wavelength_nm), len(self.aoi_deg)
        if rows * columns > MAX_ANGULAR_SPECTRUM_NODES:
            raise ValueError(f"an angle-resolved coating table holds at most {MAX_ANGULAR_SPECTRUM_NODES} nodes, got {rows} x {columns}")
        wavelengths = [_positive(w, "wavelength_nm") for w in self.wavelength_nm]
        if any(b <= a for a, b in zip(wavelengths, wavelengths[1:])):
            raise ValueError("wavelength_nm must be strictly increasing")
        angles = [Decimal(a) for a in self.aoi_deg]
        if any(b <= a for a, b in zip(angles, angles[1:])):
            raise ValueError("aoi_deg must be strictly increasing")
        if not (_ZERO <= angles[0] and angles[-1] <= MAX_AOI_DEG):
            raise ValueError(f"aoi_deg lies within [0, {MAX_AOI_DEG}] degrees")
        tables = [getattr(self, name) for name in _SPECTRUM_POLARIZED]
        for name, table in zip(_SPECTRUM_POLARIZED, tables):
            if len(table) != rows or any(len(row) != columns for row in table):
                raise ValueError(f"{name} has one row per wavelength ({rows}) and one value per angle ({columns}) in each row")
        with localcontext(DECIMAL_CONTEXT):
            for i in range(rows):
                for j in range(columns):
                    r_s, r_p, t_s, t_p = (_fraction(table[i][j], name) for name, table in zip(_SPECTRUM_POLARIZED, tables))
                    if r_s + t_s > 1 or r_p + t_p > 1:
                        raise ValueError(f"reflectance + transmittance cannot exceed 1 for either polarization (at "
                                         f"{self.wavelength_nm[i]} nm, {self.aoi_deg[j]} deg)")
        return self

    @property
    def span_nm(self) -> tuple[Decimal, Decimal]:
        return Decimal(self.wavelength_nm[0]), Decimal(self.wavelength_nm[-1])

    @property
    def aoi_span_deg(self) -> tuple[Decimal, Decimal]:
        return Decimal(self.aoi_deg[0]), Decimal(self.aoi_deg[-1])

    @property
    def node_count(self) -> int:
        return len(self.wavelength_nm) * len(self.aoi_deg)

    def values_at(self, wavelength_nm: Decimal, aoi_deg: Decimal) -> tuple[Decimal, Decimal, Decimal, Decimal] | None:
        """``(reflectance_s, reflectance_p, transmittance_s, transmittance_p)`` at ``wavelength_nm`` and ``aoi_deg`` by
        bilinear interpolation in exact decimals (rounded down to 1e-18), or None outside the grid."""

        with localcontext(DECIMAL_CONTEXT):
            along_w = _interpolation_weights([Decimal(w) for w in self.wavelength_nm], wavelength_nm)
            along_a = _interpolation_weights([Decimal(a) for a in self.aoi_deg], aoi_deg)
            if along_w is None or along_a is None:
                return None
            (i0, i1, u), (j0, j1, v) = along_w, along_a

            def interpolate(table: AngularTable) -> Decimal:
                low = Decimal(table[i0][j0]) + (Decimal(table[i0][j1]) - Decimal(table[i0][j0])) * v
                high = Decimal(table[i1][j0]) + (Decimal(table[i1][j1]) - Decimal(table[i1][j0])) * v
                value = low + (high - low) * u
                return Decimal(table[i0][j0]) if (u, v) == (_ZERO, _ZERO) else _floor_quantum(value)

            return tuple(interpolate(getattr(self, name)) for name in _SPECTRUM_POLARIZED)  # type: ignore[return-value]


#: The phase convention of ``CoatingPhaseV1`` (the only one).
COATING_PHASE_CONVENTION = "S_P_TRIAD_EXP_MINUS_I_OMEGA_T"
PhaseValue = OpticalDecimal | tuple[OpticalDecimal, ...]
_PHASE_PAIRS = (("reflection_s_rad", "reflection_p_rad"), ("transmission_s_rad", "transmission_p_rad"))


class CoatingPhaseV1(StrictContractModel):
    """The reflection and transmission phases of a coating, per polarization, in radians.

    Convention (``S_P_TRIAD_EXP_MINUS_I_OMEGA_T``): the complex amplitude coefficients are ``r = sqrt(R) exp(i phi_r)``
    and ``t = sqrt(T) exp(i phi_t)`` for each of s and p, in the Engine's s / p triad, with the time dependence
    ``exp(-i omega t)`` of the field: ``s = d_in x n`` normalized (the same vector for the incident and the outgoing
    field; ``n`` the surface normal), ``p_in = d_in x s`` for the incident field and ``p_out = d_out x s`` for the
    reflected or transmitted field (``d_in``, ``d_out`` their propagation directions).

    In this triad a reflection at normal incidence has ``r_p = -r_s`` for a dielectric interface or coating (and an ideal
    metal mirror has ``r_s = -1``, ``r_p = +1``: phases pi and 0). Thin-film tools that orient the p field so that
    ``r_p = r_s`` at normal incidence (the admittance convention of Macleod-style design programs) give reflection p
    phases that differ by pi: add pi to their reflection p phases (``phi_r,p = phi_r,p(tool) + pi``) when stating them
    here; their s phases and all transmission phases carry over unchanged. Check a converted coating at normal
    incidence, where its s and p reflection phases must differ by pi.

    ``reflection_s_rad`` / ``reflection_p_rad`` and ``transmission_s_rad`` / ``transmission_p_rad`` are stated in s / p
    pairs (a pair may be absent). Without ``wavelength_nm`` every stated phase is one constant value; with
    ``wavelength_nm`` (strictly increasing vacuum wavelengths) every stated phase is a tuple with one value per
    wavelength, interpolated linearly and never extrapolated (a wavelength outside the table is outside the model).

    Where the phases apply: a reflecting face uses the reflection phases instead of its default ones (pi for s, 0 for
    p); a splitting face uses the reflection and transmission phases instead of its defaults (pi / 2 with ``r_p =
    -r_s`` on reflection, 0 on transmission); a refracting coated face uses the transmission phases instead of the bare
    Fresnel phase, and the reflection phases for the light it reflects. A coating without ``phase`` keeps those
    defaults. Stated phases apply to light arriving from either side, so a passive coating needs, per polarization,
    ``R + T + 2 sqrt(R T) |cos(phi_r - phi_t)| <= 1``: a lossless one (``R + T = 1``) has ``phi_r - phi_t = +-pi / 2``.
    """

    reflection_s_rad: PhaseValue | None = None
    reflection_p_rad: PhaseValue | None = None
    transmission_s_rad: PhaseValue | None = None
    transmission_p_rad: PhaseValue | None = None
    wavelength_nm: tuple[OpticalDecimal, ...] | None = Field(None, min_length=2, max_length=MAX_PHASE_NODES)
    convention: Literal["S_P_TRIAD_EXP_MINUS_I_OMEGA_T"] = COATING_PHASE_CONVENTION
    status: FactStatus

    @model_validator(mode="after")
    def _consistent(self) -> CoatingPhaseV1:
        stated = []
        for s_name, p_name in _PHASE_PAIRS:
            s_value, p_value = getattr(self, s_name), getattr(self, p_name)
            if (s_value is None) != (p_value is None):
                raise ValueError(f"{s_name} and {p_name} are stated together")
            if s_value is not None:
                stated += [(s_name, s_value), (p_name, p_value)]
        if not stated:
            raise ValueError("a coating phase states reflection_s_rad / reflection_p_rad, transmission_s_rad / transmission_p_rad "
                             "or both")
        if self.wavelength_nm is None:
            if any(not isinstance(value, str) for _, value in stated):
                raise ValueError("without wavelength_nm every stated phase is one constant value")
            return self
        points = [_positive(w, "wavelength_nm") for w in self.wavelength_nm]
        if any(b <= a for a, b in zip(points, points[1:])):
            raise ValueError("wavelength_nm must be strictly increasing")
        for name, value in stated:
            if isinstance(value, str) or len(value) != len(points):
                raise ValueError(f"with wavelength_nm, {name} has one value per wavelength ({len(points)})")
        return self

    @property
    def span_nm(self) -> tuple[Decimal, Decimal] | None:
        """The wavelengths a tabulated phase covers (None for constant phases, which hold at every wavelength)."""

        if self.wavelength_nm is None:
            return None
        return Decimal(self.wavelength_nm[0]), Decimal(self.wavelength_nm[-1])

    def phases_at(self, wavelength_nm: Decimal) -> dict[str, Decimal] | None:
        """The stated phases at ``wavelength_nm`` by name (linear interpolation of a table, exact decimals), or None
        outside a table's span."""

        names = [name for pair in _PHASE_PAIRS for name in pair if getattr(self, name) is not None]
        if self.wavelength_nm is None:
            return {name: Decimal(getattr(self, name)) for name in names}
        with localcontext(DECIMAL_CONTEXT):
            weights = _interpolation_weights([Decimal(w) for w in self.wavelength_nm], wavelength_nm)
            if weights is None:
                return None
            lower, upper, fraction = weights
            return {name: Decimal(getattr(self, name)[lower]) + (Decimal(getattr(self, name)[upper]) - Decimal(getattr(self, name)[lower]))
                    * fraction for name in names}


class PublicCoatingV1(StrictContractModel):
    """A published coating model: piecewise-constant R / T ``bands``, a tabulated ``spectrum`` or an angle-resolved
    ``angular_spectrum`` (exactly one), and the angle-of-incidence range they hold for (``valid_aoi_deg``, read as
    ``aoi_reference`` says). An angle-resolved table is read in the incident medium (``aoi_reference =
    INCIDENT_MEDIUM``) and ``valid_aoi_deg`` lies inside its angle grid. ``phase`` optionally states the coating's
    reflection / transmission phases (``CoatingPhaseV1``); absent, the default phase convention applies.

    ``group_delay_bound_fs`` (schema 3.2, optional) bounds the coating's group delay: the magnitude of the group delay
    ``d phi / d omega`` of its reflection and of its transmission, for s and p, at every wavelength and angle of incidence
    its model covers (its bands, spectrum or table, and ``valid_aoi_deg``), is at most this many femtoseconds. A
    reflection's delay is counted from a reflection at the coated face's plane, a transmission's from crossing that plane
    (the thickness of the stack itself included). Absent, the coating's group delay is not stated.

    Wavelengths between or outside the bands, or outside the spectrum's span (e.g. a filter's transition edge) are
    **outside the model**: a run that sends light of such a wavelength to the coating is reported ``OUT_OF_MODEL_SCOPE``,
    never extrapolated.
    """

    coating_id: CoatingId
    kind: CoatingKind
    display_name: AuthorName
    bands: tuple[CoatingBandV1, ...] | None = Field(None, min_length=1)
    spectrum: CoatingSpectrumV1 | None = None
    angular_spectrum: CoatingAngularSpectrumV1 | None = None
    phase: CoatingPhaseV1 | None = None
    valid_aoi_deg: AngleRangeV1
    aoi_reference: CoatingAoiReference = CoatingAoiReference.AIR_EQUIVALENT
    group_delay_bound_fs: OpticalDecimal | None = None
    basis: AuthorText

    @field_validator("bands", mode="before")
    @classmethod
    def _sort_bands(cls, value: object) -> object:
        return _sorted_or_unchanged(value, lambda item: Decimal(_raw(item, "min_nm")))

    @model_validator(mode="after")
    def _consistent(self) -> PublicCoatingV1:
        if self.kind is CoatingKind.UNCOATED:
            raise ValueError("an uncoated surface has no coating model (coating_id is omitted)")
        if self.group_delay_bound_fs is not None:
            _positive(self.group_delay_bound_fs, "group_delay_bound_fs")
        if sum(value is not None for value in (self.bands, self.spectrum, self.angular_spectrum)) != 1:
            raise ValueError(f"coating {self.coating_id}: publish exactly one of bands, spectrum and angular_spectrum")
        bands = self.bands or ()
        for a, b in zip(bands, bands[1:]):
            if not Decimal(a.max_nm) < Decimal(b.min_nm):
                raise ValueError(f"coating {self.coating_id}: bands must be disjoint")
        table = self.angular_spectrum
        if table is not None:
            if self.aoi_reference is not CoatingAoiReference.INCIDENT_MEDIUM:
                raise ValueError(f"coating {self.coating_id}: an angle-resolved table is read in the incident medium (aoi_reference "
                                 "INCIDENT_MEDIUM)")
            low, high = table.aoi_span_deg
            if not (low <= Decimal(self.valid_aoi_deg.min_deg) and Decimal(self.valid_aoi_deg.max_deg) <= high):
                raise ValueError(f"coating {self.coating_id}: valid_aoi_deg lies inside the table's angles ({table.aoi_deg[0]} to "
                                 f"{table.aoi_deg[-1]} deg)")
        return self

    def band_at(self, wavelength_nm: Decimal, aoi_deg: Decimal | None = None) -> CoatingBandV1 | None:
        """The band holding ``wavelength_nm`` (None between / beyond the bands); for a spectrum, the interpolated values
        as a single-wavelength band (None outside its span); for an angle-resolved table, the s / p values interpolated
        at ``wavelength_nm`` and ``aoi_deg`` (in the incident medium) as a polarization-resolved single-wavelength band
        whose unpolarized values are their means (None outside the grid, or when no ``aoi_deg`` is given)."""

        if self.angular_spectrum is not None:
            values = None if aoi_deg is None else self.angular_spectrum.values_at(wavelength_nm, aoi_deg)
            if values is None:
                return None
            r_s, r_p, t_s, t_p = values
            point = format(wavelength_nm.normalize(), "f")
            with localcontext(DECIMAL_CONTEXT):
                mean_r, mean_t = (r_s + r_p) / _TWO, (t_s + t_p) / _TWO
            return CoatingBandV1(min_nm=point, max_nm=point, reflectance=format(mean_r, "f"), transmittance=format(mean_t, "f"),
                                 reflectance_s=format(r_s, "f"), reflectance_p=format(r_p, "f"), transmittance_s=format(t_s, "f"),
                                 transmittance_p=format(t_p, "f"), status=self.angular_spectrum.status)
        if self.spectrum is None:
            return next((band for band in self.bands or () if band.contains(wavelength_nm)), None)
        values = self.spectrum.values_at(wavelength_nm)
        if values is None:
            return None
        point = format(wavelength_nm.normalize(), "f")
        return CoatingBandV1(min_nm=point, max_nm=point, reflectance=format(values[0], "f"), transmittance=format(values[1], "f"),
                             status=self.spectrum.status)

    def covers(self, wavelength_nm: Decimal) -> bool:
        """Whether the coating is characterized at ``wavelength_nm`` (for an angle-resolved table: at some angle of its
        grid)."""

        if self.spectrum is not None or self.angular_spectrum is not None:
            low, high = self.span_nm
            return low <= wavelength_nm <= high
        return any(band.contains(wavelength_nm) for band in self.bands or ())

    @property
    def span_nm(self) -> tuple[Decimal, Decimal]:
        if self.spectrum is not None:
            return self.spectrum.span_nm
        if self.angular_spectrum is not None:
            return self.angular_spectrum.span_nm
        bands = self.bands or ()
        return Decimal(bands[0].min_nm), Decimal(bands[-1].max_nm)

    @property
    def polarization_resolved(self) -> bool:
        """Whether the coating states distinct s and p values (polarization-resolved bands or spectrum, or an
        angle-resolved table)."""

        if self.angular_spectrum is not None:
            return True
        if self.spectrum is not None:
            return self.spectrum.polarization_resolved
        return any(band.polarization_resolved for band in self.bands or ())

    @property
    def is_angle_resolved(self) -> bool:
        """Whether the coating's values depend on the angle of incidence (``angular_spectrum``)."""

        return self.angular_spectrum is not None

    @property
    def is_typical(self) -> bool:
        """Whether the coating's values are a manufacturer's typical curve (``FactStatus.TYPICAL``)."""

        if self.spectrum is not None:
            return self.spectrum.status is FactStatus.TYPICAL
        if self.angular_spectrum is not None:
            return self.angular_spectrum.status is FactStatus.TYPICAL
        return any(band.status is FactStatus.TYPICAL for band in self.bands or ())

    def schema_3_1_features(self) -> tuple[str, ...]:
        """The catalog-schema-3.1 fields this coating uses (empty for a 3.0 coating)."""

        features = []
        if self.spectrum is not None and self.spectrum.polarization_resolved:
            features.append(f"coating {self.coating_id}: polarization-resolved spectrum")
        if self.angular_spectrum is not None:
            features.append(f"coating {self.coating_id}: angular_spectrum")
        if self.phase is not None:
            features.append(f"coating {self.coating_id}: phase")
        return tuple(features)

    def schema_3_2_features(self) -> tuple[str, ...]:
        """The catalog-schema-3.2 fields this coating uses (empty when it uses none)."""

        return (f"coating {self.coating_id}: group_delay_bound_fs",) if self.group_delay_bound_fs is not None else ()


# ---------------------------------------------------------------------------------------- surface profiles


class TracedBranches(StrEnum):
    """Which light a surface passes on: ``TRANSMITTED`` (refraction / transmission only, including total internal
    reflection at an uncoated interface; any partial reflection is not traced), ``REFLECTED`` (a reflector: transmitted
    power is not traced) or ``TRANSMITTED_AND_REFLECTED`` (a splitting surface: both branches are traced)."""

    TRANSMITTED = "TRANSMITTED"
    REFLECTED = "REFLECTED"
    TRANSMITTED_AND_REFLECTED = "TRANSMITTED_AND_REFLECTED"


def _is_zero(value: object) -> bool:
    """Whether a decimal as written (any spelling ``OpticalDecimal`` reads) is zero; anything else, including an invalid
    value, is not: its field refuses it."""

    number = read_decimal(value)
    return number is not None and number.is_zero()


def _same_decimals(a: Any, b: Any) -> bool:
    """Whether two decimals as written (or pairs of them, any spelling ``OpticalDecimal`` reads) state the same values;
    False when either is not one."""

    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_same_decimals(x, y) for x, y in zip(a, b))
    x, y = read_decimal(a), read_decimal(b)
    return x is not None and y is not None and x == y


_EXACT_MEAN = Context(prec=2000)


def _mean_text(a: object, b: object) -> str | None:
    """The exact decimal mean of two decimals as written, canonical; None when either is not a decimal."""

    x, y = read_decimal(a), read_decimal(b)
    if x is None or y is None:
        return None
    with localcontext(_EXACT_MEAN):
        return decimal_to_string((x + y) / 2)


_UNPOLARIZED_MEANS = (("reflectance", "reflectance_s", "reflectance_p"), ("transmittance", "transmittance_s", "transmittance_p"))


def _unpolarized_means_filled(data: Any, per_row: bool) -> Any:
    """A coating band (``per_row`` False) or spectrum (True) document whose unpolarized ``reflectance`` / ``transmittance``
    is omitted while all four s and p values are stated, before validation: the omitted value filled with the exact mean
    of its s and p values (canonical decimals: the payload of the fully written document). A stated value is kept (the
    after-validator compares it with the mean)."""

    if not isinstance(data, dict) or not all(isinstance(data.get(n), (str, int, float, list, tuple)) for n in _SPECTRUM_POLARIZED):
        return data
    out = data
    for name, s_name, p_name in _UNPOLARIZED_MEANS:
        if data.get(name) is not None:
            continue
        s_values, p_values = data[s_name], data[p_name]
        if per_row:
            if not isinstance(s_values, (list, tuple)) or not isinstance(p_values, (list, tuple)) or len(s_values) != len(p_values):
                continue
            means = [_mean_text(s, p) for s, p in zip(s_values, p_values)]
            if any(m is None for m in means):
                continue
            out = {**out, name: means}
        else:
            mean = _mean_text(s_values, p_values)
            if mean is not None:
                out = {**out, name: mean}
    return out


def _outline_filled(data: Any) -> Any:
    """A surface sequence document without an outline whose surfaces all state their own edge (all round, or all
    rectangular), before validation: the outline filled with the largest surface edge (the outline is the largest edge)."""

    if not isinstance(data, dict) or data.get("edge_radius_mm") is not None or data.get("edge_half_widths_mm") is not None:
        return data
    surfaces = data.get("surfaces")
    if not isinstance(surfaces, (list, tuple)) or not surfaces or not all(isinstance(s, dict) for s in surfaces):
        return data
    radii = [read_decimal(s.get("edge_radius_mm")) for s in surfaces]
    widths = [s.get("edge_half_widths_mm") for s in surfaces]
    with localcontext(_EXACT_MEAN):  # (canonical text of a value as written: no rounding)
        if all(r is not None for r in radii):
            return {**data, "edge_radius_mm": decimal_to_string(max(radii))}  # type: ignore[type-var]
        if all(isinstance(w, (list, tuple)) and len(w) == 2 for w in widths):
            xs, ys = [read_decimal(w[0]) for w in widths], [read_decimal(w[1]) for w in widths]  # type: ignore[index]
            if all(v is not None for v in xs + ys):
                return {**data, "edge_half_widths_mm": [decimal_to_string(max(xs)), decimal_to_string(max(ys))]}  # type: ignore[type-var]
    return data


def _edges_at_the_outline_dropped(data: Any) -> Any:
    """A surface sequence document whose surfaces state no edge equal to the element outline, before validation: a surface
    edged at the outline reaches the outline, which is what a surface without an edge does (one spelling)."""

    if not isinstance(data, dict) or not isinstance(data.get("surfaces"), (list, tuple)):
        return data
    surfaces = []
    for surface in data["surfaces"]:
        for key in ("edge_radius_mm", "edge_half_widths_mm"):
            if isinstance(surface, dict) and key in surface and _same_decimals(surface[key], data.get(key)):
                surface = {k: v for k, v in surface.items() if k != key}
            elif isinstance(surface, PrescriptionSurfaceV1) and _same_decimals(getattr(surface, key), data.get(key)):
                surface = surface.model_copy(update={key: None})
        surfaces.append(surface)
    return {**data, "surfaces": surfaces}


def _nonzero_terms(terms: object) -> object:
    """A sag shape's raw ``terms`` without the terms of value zero (a term of value zero adds nothing); unchanged when
    nothing would be left or the input is malformed (its field refuses it)."""

    if not isinstance(terms, (list, tuple)):
        return terms
    kept = [term for term in terms if not (isinstance(term, dict) and _is_zero(term.get("value_mm")))]
    return kept if kept else terms


def _zero_terms_only(sag: object) -> bool:
    """Whether a raw Zernike or XY polynomial sag shape states only terms of value zero (it adds nothing to the profile)."""

    if not isinstance(sag, dict) or sag.get("kind") not in ("ZERNIKE", "XY_POLYNOMIAL"):
        return False
    terms = sag.get("terms")
    return isinstance(terms, (list, tuple)) and bool(terms) and all(isinstance(t, dict) and _is_zero(t.get("value_mm")) for t in terms)


def _equivalent_profile_spellings(data: Any) -> Any:
    """A surface document with its equivalent spellings in canonical form, before validation: an explicit ``conic`` of
    zero is a sphere (the key is dropped), trailing zero ``aspheric_coefficients`` are trimmed (all zero: dropped), a
    ``linear_sag_coefficient`` of zero is no axicon term (dropped), an ``aperture_center_mm`` of ``(0, 0)`` is a centred
    aperture (dropped), and a Zernike or XY polynomial ``sag`` whose terms are all zero adds nothing (dropped; a zero
    term among others is dropped by the shape). None of them changes the profile."""

    if not isinstance(data, dict):
        return data
    out = data
    if "conic" in data and _is_zero(data["conic"]):
        out = {k: v for k, v in out.items() if k != "conic"}
    if "linear_sag_coefficient" in data and _is_zero(data["linear_sag_coefficient"]):
        out = {k: v for k, v in out.items() if k != "linear_sag_coefficient"}
    center = data.get("aperture_center_mm")
    if isinstance(center, (list, tuple)) and len(center) == 2 and all(_is_zero(v) for v in center):
        out = {k: v for k, v in out.items() if k != "aperture_center_mm"}
    if _zero_terms_only(data.get("sag")):
        out = {k: v for k, v in out.items() if k != "sag"}
    coefficients = data.get("aspheric_coefficients")
    if isinstance(coefficients, (list, tuple)) and coefficients and _is_zero(coefficients[-1]):
        trimmed = list(coefficients)
        while trimmed and _is_zero(trimmed[-1]):
            trimmed.pop()
        out = {k: v for k, v in out.items() if k != "aspheric_coefficients"}
        if trimmed:
            out["aspheric_coefficients"] = trimmed
    return out


class ZernikeTermV1(StrictContractModel):
    """One Zernike term: ``value_mm`` times the term of number ``index`` (of the shape's ``indexing``)."""

    index: int = Field(strict=True, ge=1)
    value_mm: OpticalDecimal


class ZernikeSagV1(StrictContractModel):
    """Zernike sag added to the surface's radius, conic and aspheric terms: ``z += sum value_mm Z_index(rho, theta)``,
    ``rho = sqrt(x^2 + y^2) / normalization_radius_mm``, ``x = rho cos(theta) normalization_radius_mm``.

    ``indexing`` and ``normalization`` go together: ``NOLL`` with ``RMS_ORTHONORMAL`` (Noll's single index, each term of unit RMS
    over the unit disc: ``Z4 = sqrt(3) (2 rho^2 - 1)``, ``Z6 = sqrt(6) rho^2 cos(2 theta)``, index 4 .. 231), ``FRINGE`` with
    ``PEAK_UNIT`` (the 37-term Fringe ordering, each term of unit peak: ``Z4 = 2 rho^2 - 1``, ``Z5 = rho^2 cos(2 theta)``, index
    4 .. 37). The terms are not referenced to the vertex: a term with no angular dependence moves the point on the axis
    by its value at ``rho = 0`` and a coma term tilts the surface there, as in a sequential ray-tracer's Zernike sag surface.
    Piston and tilt state a pose and are refused (``SAG_TERM_NOT_SUPPORTED``). The polynomial is defined at every radius:
    a clear aperture beyond the normalization radius evaluates the stated terms there (``rho > 1``)."""

    kind: Literal["ZERNIKE"] = "ZERNIKE"
    indexing: Literal["NOLL", "FRINGE"]
    normalization: Literal["RMS_ORTHONORMAL", "PEAK_UNIT"]
    normalization_radius_mm: OpticalDecimal
    terms: tuple[ZernikeTermV1, ...] = Field(min_length=1, max_length=MAX_SAG_TERMS)

    _form: SagForm = PrivateAttr()

    @field_validator("terms", mode="before")
    @classmethod
    def _sort_terms(cls, value: object) -> object:
        return _sorted_or_unchanged(_nonzero_terms(value), lambda item: _raw(item, "index"))

    @model_validator(mode="after")
    def _consistent(self) -> ZernikeSagV1:
        _positive(self.normalization_radius_mm, "normalization_radius_mm")
        indices = [term.index for term in self.terms]
        if any(Decimal(term.value_mm) == 0 for term in self.terms):
            raise ValueError("omit a Zernike term whose value is zero")
        problem = zernike_terms_problem(self.indexing, self.normalization, indices)
        if problem is not None:
            raise ValueError(problem)
        with localcontext(DECIMAL_CONTEXT):
            self._form = sag_form(self, DECIMAL_OPS)
        return self

    @property
    def form(self) -> SagForm:
        return self._form


class XyTermV1(StrictContractModel):
    """One monomial: ``value_mm (x / R)^x_power (y / R)^y_power``."""

    x_power: int = Field(strict=True, ge=0)
    y_power: int = Field(strict=True, ge=0)
    value_mm: OpticalDecimal


class XyPolynomialSagV1(StrictContractModel):
    """XY polynomial sag added to the surface's radius, conic and aspheric terms: ``z += sum value_mm (x / R)^x_power
    (y / R)^y_power``, ``R = normalization_radius_mm``, ``2 <= x_power + y_power <= 20``; each coefficient is a length.
    Piston and tilt state a pose and are refused (``SAG_TERM_NOT_SUPPORTED``)."""

    kind: Literal["XY_POLYNOMIAL"] = "XY_POLYNOMIAL"
    normalization_radius_mm: OpticalDecimal
    terms: tuple[XyTermV1, ...] = Field(min_length=1, max_length=MAX_SAG_TERMS)

    _form: SagForm = PrivateAttr()

    @field_validator("terms", mode="before")
    @classmethod
    def _sort_terms(cls, value: object) -> object:
        return _sorted_or_unchanged(_nonzero_terms(value), lambda item: (_raw(item, "x_power"), _raw(item, "y_power")))

    @model_validator(mode="after")
    def _consistent(self) -> XyPolynomialSagV1:
        _positive(self.normalization_radius_mm, "normalization_radius_mm")
        powers = [(term.x_power, term.y_power) for term in self.terms]
        if any(Decimal(term.value_mm) == 0 for term in self.terms):
            raise ValueError("omit an XY term whose value is zero")
        problem = xy_terms_problem(powers)
        if problem is not None:
            raise ValueError(problem)
        with localcontext(DECIMAL_CONTEXT):
            self._form = sag_form(self, DECIMAL_OPS)
        return self

    @property
    def form(self) -> SagForm:
        return self._form


class BiconicSagV1(StrictContractModel):
    """A biconic: ``z = (c_x x^2 + c_y y^2) / (1 + sqrt(1 - (1 + k_x) c_x^2 x^2 - (1 + k_y) c_y^2 y^2))``, ``c_x = 1 / radius_x_mm``,
    ``c_y = 1 / radius_y_mm`` (absent: flat along that axis), ``k_x = conic_x``, ``k_y = conic_y`` (absent: 0). It replaces the
    surface's ``radius_mm``, ``conic`` and ``aspheric_coefficients``, which stay absent."""

    kind: Literal["BICONIC"] = "BICONIC"
    radius_x_mm: OpticalDecimal | None = None
    radius_y_mm: OpticalDecimal | None = None
    conic_x: OpticalDecimal | None = None
    conic_y: OpticalDecimal | None = None

    _form: SagForm = PrivateAttr()

    @model_validator(mode="after")
    def _consistent(self) -> BiconicSagV1:
        if self.radius_x_mm is None and self.radius_y_mm is None:
            raise ValueError("a biconic needs radius_x_mm, radius_y_mm or both")
        for value, label in ((self.radius_x_mm, "radius_x_mm"), (self.radius_y_mm, "radius_y_mm")):
            if value is not None and Decimal(value) == 0:
                raise ValueError(f"{label} must be non-zero (omit it for a section without power)")
        if (self.conic_x is not None and self.radius_x_mm is None) or (self.conic_y is not None and self.radius_y_mm is None):
            raise ValueError("a biconic's conic needs the radius of its section")
        with localcontext(DECIMAL_CONTEXT):
            self._form = sag_form(self, DECIMAL_OPS)
        return self

    @property
    def form(self) -> SagForm:
        return self._form


class ToroidalSagV1(StrictContractModel):
    """A toroid: the curve ``z_y(y) = c y^2 / (1 + sqrt(1 - (1 + k) c^2 y^2)) + sum_i y_terms[i-1] y^(2i)`` in the YZ plane
    (``c = 1 / radius_y_mm``, absent: 0; ``k = conic``, absent: 0) rotated about the axis parallel to Y through
    ``z = radius_of_rotation_mm``: ``z = R - sign(R) sqrt((R - z_y)^2 - x^2)``; absent: no rotation, the curve extruded along X.
    It replaces the surface's ``radius_mm``, ``conic`` and ``aspheric_coefficients``, which stay absent. ``y_terms[0]`` multiplies
    ``y^2`` and so adds ``2 y_terms[0]`` to the curvature of the YZ section at the vertex."""

    kind: Literal["TOROIDAL"] = "TOROIDAL"
    radius_y_mm: OpticalDecimal | None = None
    conic: OpticalDecimal | None = None
    y_terms: tuple[OpticalDecimal, ...] | None = Field(None, min_length=1, max_length=MAX_TOROID_Y_TERMS)
    radius_of_rotation_mm: OpticalDecimal | None = None

    _form: SagForm = PrivateAttr()

    @model_validator(mode="before")
    @classmethod
    def _trailing_zero_terms_dropped(cls, data: Any) -> Any:
        """Trailing ``y_terms`` of value zero add nothing: trimmed (all zero: ``y_terms`` dropped)."""

        if not isinstance(data, dict):
            return data
        terms = data.get("y_terms")
        if not isinstance(terms, (list, tuple)) or not terms or not _is_zero(terms[-1]):
            return data
        trimmed = list(terms)
        while trimmed and _is_zero(trimmed[-1]):
            trimmed.pop()
        out = {k: v for k, v in data.items() if k != "y_terms"}
        if trimmed:
            out["y_terms"] = trimmed
        return out

    @model_validator(mode="after")
    def _consistent(self) -> ToroidalSagV1:
        if self.radius_y_mm is None and self.y_terms is None and self.radius_of_rotation_mm is None:
            raise ValueError("a toroid needs radius_y_mm, y_terms or radius_of_rotation_mm")
        for value, label in ((self.radius_y_mm, "radius_y_mm"), (self.radius_of_rotation_mm, "radius_of_rotation_mm")):
            if value is not None and Decimal(value) == 0:
                raise ValueError(f"{label} must be non-zero (omit it for a section without power)")
        if self.conic is not None and self.radius_y_mm is None:
            raise ValueError("a toroid's conic needs radius_y_mm")
        if self.y_terms is not None and Decimal(self.y_terms[-1]) == 0:
            raise ValueError("y_terms must end with a non-zero term")
        with localcontext(DECIMAL_CONTEXT):
            self._form = sag_form(self, DECIMAL_OPS)
        return self

    @property
    def form(self) -> SagForm:
        return self._form


SagShapeV1 = Annotated[ZernikeSagV1 | XyPolynomialSagV1 | BiconicSagV1 | ToroidalSagV1, Field(discriminator="kind")]


class ClearShapeV1(StrictContractModel):
    """The shape of a clear aperture (or of an opening, or a hole) within the circle or rectangle it is stated by
    (Project optical models; a published catalog does not carry it).

    * ``ELLIPSE``: the ellipse inscribed in the stated rectangle (``*_half_widths_mm``: its semi-axes along local X / Y);
    * ``D_SHAPE``: the stated circle (``*_radius_mm``) cut by a straight flat: the points whose coordinate along
      ``flat_side`` (``+X``, ``-X``, ``+Y`` or ``-Y`` from the centre) is at most ``flat_mm`` (``|flat_mm|`` below the radius);
    * ``POLYGON``: a simple polygon (convex or not; a triangle has 3 corners) with corners ``vertices_mm`` relative to the
      centre, counter-clockwise in local X / Y, every corner inside the stated circle or rectangle.

    Its bounding circle / rectangle keeps every rule stated about it (the profile's domain, the edge, the body)."""

    kind: Literal["ELLIPSE", "D_SHAPE", "POLYGON"]
    flat_mm: OpticalDecimal | None = None
    flat_side: Literal["+X", "-X", "+Y", "-Y"] | None = None
    vertices_mm: tuple[DecimalPair, ...] | None = Field(None, min_length=3, max_length=MAX_POLYGON_VERTICES)

    @model_validator(mode="after")
    def _consistent(self) -> ClearShapeV1:
        if self.kind == "D_SHAPE":
            if self.flat_mm is None or self.flat_side is None or self.vertices_mm is not None:
                raise ValueError("a D_SHAPE states flat_mm and flat_side (and no vertices_mm)")
        elif self.kind == "POLYGON":
            if self.vertices_mm is None or self.flat_mm is not None or self.flat_side is not None:
                raise ValueError("a POLYGON states vertices_mm (and no flat)")
            problem = polygon_problem(self.corners())
            if problem is not None:
                raise ValueError(problem)
        elif self.flat_mm is not None or self.flat_side is not None or self.vertices_mm is not None:
            raise ValueError("an ELLIPSE is the ellipse inscribed in its rectangle: it states no flat and no vertices")
        return self

    def corners(self) -> tuple[tuple[Decimal, Decimal], ...]:
        return tuple((Decimal(x), Decimal(y)) for x, y in self.vertices_mm or ())

    def flat_axis(self) -> tuple[Decimal, Decimal]:
        """The flat's outward normal (local X / Y)."""

        return {"+X": (_ONE, _ZERO), "-X": (-_ONE, _ZERO), "+Y": (_ZERO, _ONE), "-Y": (_ZERO, -_ONE)}[self.flat_side or "+X"]

    def check_within(self, radius: Decimal | None, half_widths: tuple[Decimal, Decimal] | None, what: str) -> None:
        """The shape fits the circle (``radius``) or rectangle (``half_widths``) it is stated by."""

        if self.kind == "ELLIPSE" and half_widths is None:
            raise ValueError(f"{what}: an ELLIPSE is stated by half widths (its semi-axes), not a radius")
        if self.kind == "D_SHAPE":
            if radius is None:
                raise ValueError(f"{what}: a D_SHAPE is stated by a radius (the circle its flat cuts)")
            if not abs(Decimal(self.flat_mm)) < radius:  # type: ignore[arg-type]
                raise ValueError(f"{what}: |flat_mm| must be smaller than the radius (the flat cuts the circle)")
        if self.kind == "POLYGON":
            with localcontext(DECIMAL_CONTEXT):
                for x, y in self.corners():
                    inside = (x * x + y * y <= radius * radius if radius is not None
                              else abs(x) <= half_widths[0] and abs(y) <= half_widths[1])  # type: ignore[index]
                    if not inside:
                        raise ValueError(f"{what}: polygon corner ({x}, {y}) lies outside the stated "
                                         f"{'circle' if radius is not None else 'rectangle'}")

    def contains(self, x: Decimal, y: Decimal, radius: Decimal | None, half_widths: tuple[Decimal, Decimal] | None) -> bool:
        """Whether ``(x, y)`` (relative to the centre) lies in the shape, boundary included up to 1e-9 mm."""

        with localcontext(DECIMAL_CONTEXT):
            tol = _VERTEX_TOLERANCE_MM
            if self.kind == "ELLIPSE":
                hx, hy = half_widths  # type: ignore[misc]
                ax, ay = hx + tol, hy + tol
                return x * x * ay * ay + y * y * ax * ax <= ax * ax * ay * ay
            if self.kind == "D_SHAPE":
                ux, uy = self.flat_axis()
                reach = radius + tol  # type: ignore[operator]
                return x * x + y * y <= reach * reach and x * ux + y * uy <= Decimal(self.flat_mm) + tol  # type: ignore[arg-type]
            corners = self.corners()
            held = False
            for i in range(len(corners)):
                (x1, y1), (x2, y2) = corners[i], corners[(i + 1) % len(corners)]
                if (y1 > y) != (y2 > y) and x < x1 + (x2 - x1) * (y - y1) / (y2 - y1):
                    held = not held
                ex, ey = x2 - x1, y2 - y1
                t = min(_ONE, max(_ZERO, ((x - x1) * ex + (y - y1) * ey) / (ex * ex + ey * ey)))
                qx, qy = x - x1 - t * ex, y - y1 - t * ey
                if qx * qx + qy * qy <= tol * tol:
                    return True
            return held

    def boundary_points(self, radius: Decimal | None, half_widths: tuple[Decimal, Decimal] | None) -> tuple[tuple[Decimal, Decimal], ...]:
        """Deterministic points on the shape's boundary (relative to the centre): a polygon's corners and edge midpoints,
        an ellipse's and a D shape's arc at rational points of the circle, a flat's two ends."""

        with localcontext(DECIMAL_CONTEXT):
            if self.kind == "POLYGON":
                corners = self.corners()
                mids = [((a[0] + b[0]) / 2, (a[1] + b[1]) / 2) for a, b in zip(corners, corners[1:] + corners[:1])]
                return (*corners, *mids)
            points = []
            for c, sn in _turn_points():
                if self.kind == "ELLIPSE":
                    points.append((half_widths[0] * c, half_widths[1] * sn))  # type: ignore[index]
                else:
                    point = (radius * c, radius * sn)  # type: ignore[operator]
                    if self.contains(point[0], point[1], radius, half_widths):
                        points.append(point)
            if self.kind == "D_SHAPE":
                ux, uy = self.flat_axis()
                f = Decimal(self.flat_mm)  # type: ignore[arg-type]
                chord = (radius * radius - f * f).sqrt()  # type: ignore[operator]
                points += [(f * ux - chord * uy, f * uy + chord * ux), (f * ux + chord * uy, f * uy - chord * ux)]
            return tuple(points)


def _turn_points() -> tuple[tuple[Decimal, Decimal], ...]:
    """Rational points of the unit circle, a full turn (``_shape_directions``, mirrored into every quadrant)."""

    half = _shape_directions()
    return tuple(dict.fromkeys((*half, *((-x, -y) for x, y in half))))


class SurfaceHoleV1(StrictContractModel):
    """An opening through the element (a holed mirror, a perforated plate) inside a surface's clear aperture, in the
    surface's local X / Y: there is no surface there and light passes straight through. A circle (``radius_mm``) or a
    rectangle (``half_widths_mm``) about ``center_mm``, optionally refined by ``shape`` (``ClearShapeV1``). Holes lie inside
    the clear aperture and apart from each other (Project optical models; a published catalog does not carry them)."""

    center_mm: DecimalPair
    radius_mm: OpticalDecimal | None = None
    half_widths_mm: DecimalPair | None = None
    shape: ClearShapeV1 | None = None

    @model_validator(mode="after")
    def _consistent(self) -> SurfaceHoleV1:
        if (self.radius_mm is None) == (self.half_widths_mm is None):
            raise ValueError("a hole has exactly one of radius_mm and half_widths_mm")
        radius, half_widths = self.bounding()
        if self.shape is not None:
            self.shape.check_within(radius, half_widths, "hole")
        return self

    def bounding(self) -> tuple[Decimal | None, tuple[Decimal, Decimal] | None]:
        if self.radius_mm is not None:
            return _positive(self.radius_mm, "hole radius_mm"), None
        return None, (_positive(self.half_widths_mm[0], "hole half_widths_mm"), _positive(self.half_widths_mm[1], "hole half_widths_mm"))  # type: ignore[index]

    def reach(self) -> Decimal:
        """The radius of the circle about its centre that holds it."""

        radius, half_widths = self.bounding()
        with localcontext(DECIMAL_CONTEXT):
            return radius if radius is not None else (half_widths[0] * half_widths[0] + half_widths[1] * half_widths[1]).sqrt()  # type: ignore[index]

    def corner_points(self) -> tuple[tuple[Decimal, Decimal], ...]:
        """Points whose containment in a convex clear aperture puts the whole hole in it: its bounding box's corners."""

        radius, half_widths = self.bounding()
        reach_x, reach_y = (radius, radius) if radius is not None else half_widths  # type: ignore[misc]
        cx, cy = Decimal(self.center_mm[0]), Decimal(self.center_mm[1])
        return tuple((cx + sx * reach_x, cy + sy * reach_y) for sx in (-_ONE, _ONE) for sy in (-_ONE, _ONE))


#: The most rays a scattering surface sends out per beam that meets it, and the largest exponent of a COSINE_POWER lobe.
MAX_SCATTER_SAMPLE_COUNT = 65536
MAX_SCATTER_EXPONENT = Decimal(1000)


class SurfaceScatterV1(StrictContractModel):
    """Diffuse scatter of a rough surface (Project optical models; a published catalog does not carry it).

    ``fraction`` (0 < f <= 1) of the power meeting the surface leaves it as incoherent, unpolarized light on ``side``
    (``REFLECT``: back into the medium the light comes from; ``TRANSMIT``: into the medium behind it), with the angular law
    ``bsdf`` about the surface normal on that side: ``LAMBERTIAN`` (intensity proportional to cos(theta)) or
    ``COSINE_POWER`` (proportional to cos^n(theta), ``n = exponent``). The rest of the light meets the surface's own
    interaction unchanged. The scattered light of each beam is traced as ``sample_count`` rays (default 1024) from the beam's
    hit point (``seed``: the sampling sequence's shift; default 0); their powers add (no phase, speckle or diffraction). A
    reflecting surface scatters on its reflecting side only; a surface that splits a beam into two traced branches does not
    scatter."""

    bsdf: Literal["LAMBERTIAN", "COSINE_POWER"]
    fraction: OpticalDecimal
    side: Literal["REFLECT", "TRANSMIT"]
    exponent: OpticalDecimal | None = None
    sample_count: int | None = Field(None, ge=2, le=MAX_SCATTER_SAMPLE_COUNT)
    seed: int | None = Field(None, ge=0)

    @model_validator(mode="after")
    def _consistent(self) -> SurfaceScatterV1:
        fraction = _positive(self.fraction, "scatter fraction")
        if fraction > _ONE:
            raise ValueError("scatter fraction is at most 1")
        if (self.bsdf == "COSINE_POWER") != (self.exponent is not None):
            raise ValueError("scatter exponent is stated with bsdf COSINE_POWER and only with it")
        if self.exponent is not None and _positive(self.exponent, "scatter exponent") > MAX_SCATTER_EXPONENT:
            raise ValueError(f"scatter exponent is at most {MAX_SCATTER_EXPONENT}")
        return self


class _SurfaceProfile:
    """Profile and clear aperture of one surface (shared by ``PrescriptionSurfaceV1`` and ``PosedSurfaceV1``).

    Equivalent spellings are read in canonical form (``_equivalent_profile_spellings``): ``conic`` 0 is a sphere and is
    dropped, trailing zero aspheric terms are trimmed.

    Transverse coordinates ``(x, y)`` are the surface's local X / Y (for a posed surface: along ``u_axis`` and
    ``normal x u_axis``). The sag along the surface axis is

        z(rho) = c rho^2 / (1 + sqrt(1 - (1 + k) c^2 rho^2)) + A4 rho^4 + A6 rho^6 + ... + a rho

    with ``c = 1 / radius_mm`` (0 without a radius), ``k = conic`` (0 when absent), ``aspheric_coefficients`` =
    (A4, A6, ...) and ``a = linear_sag_coefficient`` (0 when absent); ``rho^2 = x^2 + y^2``, or for a cylindrical
    surface the square of the curved coordinate only (``cylinder_axis = "X"``: straight along X, curved along Y,
    ``rho = |y|``).

    The linear term ``a rho`` is the axicon term: a cone of slope ``a = dz / d rho`` (dimensionless; ``|a| <
    MAX_LINEAR_SAG_COEFFICIENT``). Alone it is a cone with its apex at the vertex -- a refracting axicon face of cone
    half-angle ``alpha`` (measured from the surface plane) has ``a = tan(alpha)``, positive when the cone rises toward
    +Z -- and it may be combined with a radius, a conic and aspheric terms. On a cylindrical surface it is a roof of
    two planes meeting along the straight axis. A cylindrical surface may carry a conic and aspheric terms (an
    acylinder); it needs a radius or an axicon term.

    A two-variable sag shape (``sag``, catalog schema 3.4): a Zernike or XY polynomial shape adds to the radius, conic and
    aspheric terms; a biconic or a toroid replaces them. A shape does not combine with ``cylinder_axis`` or
    ``linear_sag_coefficient``, and every shape's profile covers the clear aperture.
    """

    # The concrete surface models declare the fields surface_id, radius_mm, conic, aspheric_coefficients,
    # linear_sag_coefficient, cylinder_axis, sag, clear_radius_mm, clear_half_widths_mm and aperture_center_mm.

    def _check_profile(self) -> None:
        sid = self.surface_id
        if (self.clear_radius_mm is None) == (self.clear_half_widths_mm is None):
            raise ValueError(f"surface {sid}: publish exactly one of clear_radius_mm (a circle) and clear_half_widths_mm (a rectangle)")
        if self.clear_radius_mm is not None:
            _positive(self.clear_radius_mm, "clear_radius_mm")
        else:
            for value in self.clear_half_widths_mm:  # type: ignore[union-attr]
                _positive(value, "clear_half_widths_mm")
        if self.aperture_center_mm is not None and all(Decimal(v) == 0 for v in self.aperture_center_mm):
            raise ValueError(f"surface {sid}: omit aperture_center_mm for a centred aperture")
        self._check_shape_and_holes()
        if self.radius_mm is not None and Decimal(self.radius_mm) == 0:
            raise ValueError("radius_mm must be non-zero (omit it for a plane)")
        if self.conic is not None:
            if self.radius_mm is None:
                raise ValueError(f"surface {sid}: a conic constant needs radius_mm (the vertex radius)")
            if Decimal(self.conic) == 0:
                raise ValueError(f"surface {sid}: omit conic for a sphere (k = 0)")
        if self.aspheric_coefficients is not None and Decimal(self.aspheric_coefficients[-1]) == 0:
            raise ValueError(f"surface {sid}: aspheric_coefficients must end with a non-zero term")
        if self.linear_sag_coefficient is not None:
            linear = Decimal(self.linear_sag_coefficient)
            if linear == 0:
                raise ValueError(f"surface {sid}: omit linear_sag_coefficient for a surface without an axicon term")
            if abs(linear) >= MAX_LINEAR_SAG_COEFFICIENT:
                raise ValueError(f"surface {sid}: |linear_sag_coefficient| must be below {MAX_LINEAR_SAG_COEFFICIENT}, got "
                                 f"{self.linear_sag_coefficient}")
        if self.cylinder_axis is not None and self.radius_mm is None and self.linear_sag_coefficient is None:
            raise ValueError(f"surface {sid}: a cylindrical surface needs radius_mm or linear_sag_coefficient")
        if self.radius_mm is not None:
            with localcontext(DECIMAL_CONTEXT):
                radius = Decimal(self.radius_mm)
                k = Decimal(self.conic) if self.conic is not None else _ZERO
                if (_ONE + k) * self.profile_reach_sq() > radius * radius:
                    raise ValueError(
                        f"surface {sid}: |radius_mm| must be at least clear_radius_mm (the profile must be defined over the whole "
                        "clear aperture)"
                    )
        self._check_sag_shape()

    def _check_sag_shape(self) -> None:
        """How a two-variable sag shape combines with the rest of the profile and with the clear aperture."""

        shape, sid = self.sag, self.surface_id
        if shape is None:
            return
        if self.cylinder_axis is not None or self.linear_sag_coefficient is not None:
            raise ValueError(f"{SAG_COMBINATION_NOT_SUPPORTED}: surface {sid}: a {shape.kind} sag shape does not combine with "
                             "cylinder_axis or linear_sag_coefficient")
        if shape.kind in (BICONIC, TOROIDAL) and (self.radius_mm is not None or self.conic is not None or self.aspheric_coefficients is not None):
            raise ValueError(f"{SAG_COMBINATION_NOT_SUPPORTED}: surface {sid}: a {shape.kind} sag shape replaces radius_mm, conic and "
                             "aspheric_coefficients: state its sections in the shape")
        with localcontext(DECIMAL_CONTEXT):
            # (a Zernike sum is a polynomial defined at every radius: a clear aperture beyond its normalization radius
            # evaluates the stated terms there, as sequential design programs do)
            if not shape.form.additive:
                for x, y in self.aperture_points():
                    if not sag_value(shape.form, x, y, DECIMAL_OPS)[1]:
                        raise ValueError(f"{SAG_OUTSIDE_DOMAIN}: surface {sid}: the {shape.kind} profile is not defined at "
                                         f"({x:.6g}, {y:.6g}) mm inside the clear aperture")

    def _bounding(self) -> tuple[Decimal | None, tuple[Decimal, Decimal] | None]:
        """The clear aperture's stated circle (radius) or rectangle (half widths)."""

        if self.clear_radius_mm is not None:
            return Decimal(self.clear_radius_mm), None
        return None, (Decimal(self.clear_half_widths_mm[0]), Decimal(self.clear_half_widths_mm[1]))  # type: ignore[index]

    def _check_shape_and_holes(self) -> None:
        """A clear shape fits its circle / rectangle; every hole lies inside the clear aperture (its bounding box's corners
        in it: the whole hole for a convex aperture; the Engine checks the exact distance) and the holes lie apart (their
        enclosing circles do not overlap)."""

        sid = self.surface_id
        radius, half_widths = self._bounding()
        if self.clear_shape is not None:
            self.clear_shape.check_within(radius, half_widths, f"surface {sid}")
        holes = self.holes or ()
        with localcontext(DECIMAL_CONTEXT):
            for i, hole in enumerate(holes):
                if not all(self.aperture_contains(x, y, holes=False) for x, y in hole.corner_points()):
                    raise ValueError(f"surface {sid}: hole {i} does not lie inside the clear aperture")
                for j in range(i):
                    other = holes[j]
                    dx = Decimal(hole.center_mm[0]) - Decimal(other.center_mm[0])
                    dy = Decimal(hole.center_mm[1]) - Decimal(other.center_mm[1])
                    reach = hole.reach() + other.reach()
                    if dx * dx + dy * dy < reach * reach:
                        raise ValueError(f"surface {sid}: holes {j} and {i} are not apart (their enclosing circles overlap)")

    @property
    def has_clear_shape_or_holes(self) -> bool:
        """The surface states a clear shape or holes (Project optical models only)."""

        return self.clear_shape is not None or self.holes is not None

    def _check_scatter(self) -> None:
        """A reflecting surface scatters on its reflecting side; a surface splitting a beam into two traced branches does not."""

        scatter = getattr(self, "scatter", None)
        if scatter is None:
            return
        if self.traced is TracedBranches.REFLECTED and scatter.side != "REFLECT":  # type: ignore[attr-defined]
            raise ValueError(f"surface {self.surface_id}: a REFLECTED surface scatters on its reflecting side (side REFLECT)")  # type: ignore[attr-defined]

    # --- aperture geometry -------------------------------------------------------------------------------

    def _center(self) -> tuple[Decimal, Decimal]:
        if self.aperture_center_mm is None:
            return _ZERO, _ZERO
        return Decimal(self.aperture_center_mm[0]), Decimal(self.aperture_center_mm[1])

    def _half_widths(self) -> tuple[Decimal, Decimal]:
        """Half widths of the aperture's bounding rectangle along local X / Y."""

        if self.clear_radius_mm is not None:
            radius = Decimal(self.clear_radius_mm)
            return radius, radius
        return Decimal(self.clear_half_widths_mm[0]), Decimal(self.clear_half_widths_mm[1])  # type: ignore[index]

    @property
    def is_circular_aperture(self) -> bool:
        return self.clear_radius_mm is not None

    def aperture_extents_mm(self) -> tuple[Decimal, Decimal]:
        """Full widths ``(x, y)`` of the clear aperture (a circle: its diameter twice)."""

        hx, hy = self._half_widths()
        return hx * 2, hy * 2

    def aperture_bounds_mm(self) -> tuple[Decimal, Decimal, Decimal, Decimal]:
        """``(x_min, x_max, y_min, y_max)`` of the clear aperture in the surface's local X / Y."""

        cx, cy = self._center()
        hx, hy = self._half_widths()
        return cx - hx, cx + hx, cy - hy, cy + hy

    def aperture_contains(self, x_mm: Decimal, y_mm: Decimal, holes: bool = True) -> bool:
        """Whether the local point ``(x, y)`` lies in the clear aperture (boundary included, up to 1e-9 mm): in its shape
        and, with ``holes``, in no hole."""

        with localcontext(DECIMAL_CONTEXT):
            cx, cy = self._center()
            dx, dy = x_mm - cx, y_mm - cy
            if self.clear_shape is not None:
                if not self.clear_shape.contains(dx, dy, *self._bounding()):
                    return False
            if holes and self.holes:
                for hole in self.holes:
                    radius, half_widths = hole.bounding()
                    hx, hy = x_mm - Decimal(hole.center_mm[0]), y_mm - Decimal(hole.center_mm[1])
                    if hole.shape is not None:
                        within = hole.shape.contains(hx, hy, radius, half_widths)
                    elif radius is not None:
                        within = hx * hx + hy * hy < radius * radius
                    else:
                        within = abs(hx) < half_widths[0] and abs(hy) < half_widths[1]  # type: ignore[index]
                    if within:
                        return False
            if self.clear_shape is not None:
                return True
            if self.clear_radius_mm is not None:
                reach = Decimal(self.clear_radius_mm) + _VERTEX_TOLERANCE_MM
                return dx * dx + dy * dy <= reach * reach
            hx, hy = self._half_widths()
            return abs(dx) <= hx + _VERTEX_TOLERANCE_MM and abs(dy) <= hy + _VERTEX_TOLERANCE_MM

    def aperture_reach_sq(self) -> Decimal:
        """The largest ``x^2 + y^2`` over the clear aperture (its farthest point from the surface axis, squared)."""

        with localcontext(DECIMAL_CONTEXT):
            cx, cy = self._center()
            if self.clear_radius_mm is not None:
                reach = (cx * cx + cy * cy).sqrt() + Decimal(self.clear_radius_mm)
                return reach * reach
            hx, hy = self._half_widths()
            x, y = abs(cx) + hx, abs(cy) + hy
            return x * x + y * y

    def profile_reach_sq(self) -> Decimal:
        """The largest squared profile coordinate ``rho^2`` over the clear aperture."""

        if self.cylinder_axis is None:
            return self.aperture_reach_sq()
        with localcontext(DECIMAL_CONTEXT):
            cx, cy = self._center()
            hx, hy = self._half_widths()
            curved = abs(cy) + hy if self.cylinder_axis == "X" else abs(cx) + hx
            return curved * curved

    def aperture_points(self) -> tuple[tuple[Decimal, Decimal], ...]:
        """Deterministic sample points ``(x, y)`` of the clear aperture: its centre, its boundary (a rectangle's corners
        and edges) and interior points, used by the surface-gap check."""

        with localcontext(DECIMAL_CONTEXT):
            cx, cy = self._center()
            steps = [Decimal(i) / _GAP_SAMPLES for i in range(_GAP_SAMPLES + 1)]
            points: list[tuple[Decimal, Decimal]] = []
            if self.clear_radius_mm is not None:
                radius = Decimal(self.clear_radius_mm)
                if self.sag is not None:
                    directions = _shape_directions()
                else:
                    # Rounded down, so every diagonal sample lies inside the circle (and inside the profile's domain).
                    diagonal = _HALF.sqrt().quantize(_SPECTRUM_QUANTUM, rounding=ROUND_FLOOR)
                    directions = ((_ONE, _ZERO), (-_ONE, _ZERO), (_ZERO, _ONE), (_ZERO, -_ONE),
                                  (diagonal, diagonal), (-diagonal, diagonal), (diagonal, -diagonal), (-diagonal, -diagonal))
                points.append((cx, cy))
                for step in steps[1:]:
                    points.extend((cx + dx * radius * step, cy + dy * radius * step) for dx, dy in directions)
            else:
                hx, hy = self._half_widths()
                signed = [s * 2 - _ONE for s in steps]
                points.extend((cx + hx * a, cy + hy * b) for a in signed for b in signed)
            if self.clear_shape is not None or self.holes:
                # the points within the shape (and outside the holes), the shape's boundary and every hole's
                radius, half_widths = self._bounding()
                points = [p for p in points if self.aperture_contains(*p)]
                if self.clear_shape is not None:
                    points += [(cx + x, cy + y) for x, y in self.clear_shape.boundary_points(radius, half_widths)]
                for hole in self.holes or ():
                    hole_radius, hole_half = hole.bounding()
                    hx0, hy0 = Decimal(hole.center_mm[0]), Decimal(hole.center_mm[1])
                    if hole.shape is not None:
                        rim = hole.shape.boundary_points(hole_radius, hole_half)
                    elif hole_radius is not None:
                        rim = tuple((hole_radius * c, hole_radius * sn) for c, sn in _turn_points())
                    else:
                        rim = tuple((sx * hole_half[0], sy * hole_half[1]) for sx in (-_ONE, _ONE) for sy in (-_ONE, _ONE))  # type: ignore[index]
                    points += [(hx0 + x, hy0 + y) for x, y in rim]
            return tuple(points)

    # --- profile ------------------------------------------------------------------------------------------

    @property
    def is_plane(self) -> bool:
        return self.radius_mm is None and self.aspheric_coefficients is None and self.linear_sag_coefficient is None and self.sag is None

    @property
    def is_axicon(self) -> bool:
        """The profile carries the linear (axicon) term."""

        return self.linear_sag_coefficient is not None

    @property
    def is_aspheric(self) -> bool:
        """A conic or polynomial (even-asphere) profile."""

        return self.conic is not None or self.aspheric_coefficients is not None

    @property
    def is_cylindrical(self) -> bool:
        return self.cylinder_axis is not None

    @property
    def is_freeform(self) -> bool:
        """The profile carries a two-variable sag shape (``sag``)."""

        return self.sag is not None

    @property
    def has_section_powers(self) -> bool:
        """A biconic or a toroid: its two sections along local X and Y have their own vertex power."""

        return self.sag is not None and not self.sag.form.additive

    @property
    def is_rotationally_symmetric(self) -> bool:
        """Rotating the surface about its axis changes nothing: no cylinder, no sag shape, a centred circular aperture."""

        return (self.cylinder_axis is None and self.sag is None and self.clear_radius_mm is not None and self.aperture_center_mm is None
                and self.clear_shape is None and self.holes is None)

    @property
    def curved_axis(self) -> Literal["X", "Y"] | None:
        """The local axis a cylindrical surface curves along (None for any other profile)."""

        if self.cylinder_axis is None:
            return None
        return "Y" if self.cylinder_axis == "X" else "X"

    def profile_sag(self, rho_sq_mm2: Decimal) -> Decimal:
        """Sag at profile coordinate ``rho`` given as ``rho^2`` (class docstring); ValueError outside the profile."""

        with localcontext(DECIMAL_CONTEXT):
            sag = _ZERO
            if self.radius_mm is not None:
                curvature = _ONE / Decimal(self.radius_mm)
                k = Decimal(self.conic) if self.conic is not None else _ZERO
                discriminant = _ONE - (_ONE + k) * curvature * curvature * rho_sq_mm2
                if discriminant < 0:
                    raise ValueError(f"surface {self.surface_id}: the profile is not defined at rho^2 = {rho_sq_mm2} mm^2")
                sag = curvature * rho_sq_mm2 / (_ONE + discriminant.sqrt())
            power = rho_sq_mm2 * rho_sq_mm2
            for coefficient in self.aspheric_coefficients or ():
                sag += Decimal(coefficient) * power
                power *= rho_sq_mm2
            if self.linear_sag_coefficient is not None:
                sag += Decimal(self.linear_sag_coefficient) * rho_sq_mm2.sqrt()
            return sag

    def schema_3_1_features(self) -> tuple[str, ...]:
        """The catalog-schema-3.1 fields this surface uses: the axicon term, conic / aspheric terms on a cylinder."""

        features = []
        if self.linear_sag_coefficient is not None:
            features.append(f"surface {self.surface_id}: linear_sag_coefficient")
        if self.cylinder_axis is not None and (self.conic is not None or self.aspheric_coefficients is not None):
            features.append(f"surface {self.surface_id}: conic or aspheric terms on a cylindrical surface")
        return tuple(features)

    def schema_3_4_features(self) -> tuple[str, ...]:
        """The catalog-schema-3.4 fields this surface uses: a two-variable sag shape."""

        return (f"surface {self.surface_id}: sag",) if self.sag is not None else ()

    def sag_at(self, x_mm: Decimal, y_mm: Decimal = _ZERO) -> Decimal:
        """Sag (offset along the surface axis from the vertex) at the local transverse point ``(x, y)``. For a
        rotationally symmetric profile ``sag_at(r)`` is the sag at transverse radius ``r``."""

        with localcontext(DECIMAL_CONTEXT):
            if self.sag is not None:
                z, ok = sag_value(self.sag.form, x_mm, y_mm, DECIMAL_OPS)
                if not ok:
                    raise ValueError(f"surface {self.surface_id}: the profile is not defined at ({x_mm}, {y_mm}) mm")
                if not self.sag.form.additive:
                    return z
                return z + self.profile_sag(x_mm * x_mm + y_mm * y_mm)
            if self.cylinder_axis == "X":
                rho_sq = y_mm * y_mm
            elif self.cylinder_axis == "Y":
                rho_sq = x_mm * x_mm
            else:
                rho_sq = x_mm * x_mm + y_mm * y_mm
            return self.profile_sag(rho_sq)


# ------------------------------------------------------------------------------- SURFACE_SEQUENCE (v2) model


class PrescriptionSurfaceV1(_SurfaceProfile, StrictContractModel):
    """One surface of a ``SURFACE_SEQUENCE`` model, in the element frame (its axis is local Z, its vertex at
    ``(0, 0, vertex_z_mm)``).

    Profile (``_SurfaceProfile``): a plane (no ``radius_mm``, no aspheric terms), a sphere (``radius_mm``: the signed
    vertex radius, positive when the centre of curvature lies on the +Z side), a conic (``conic`` k: -1 a paraboloid, < -1
    a hyperboloid, between -1 and 0 a prolate and > 0 an oblate ellipsoid), an even asphere (``aspheric_coefficients``
    A4, A6, ... on rho^4, rho^6, ..., with or without a conic), an axicon (``linear_sag_coefficient``, alone or added to
    any of these), a cylinder (``cylinder_axis``: the local axis it is straight along; with a conic or aspheric terms
    an acylinder) or a two-variable sag shape (``sag``: Zernike, XY polynomial, biconic, toroid). Clear aperture: exactly one of ``clear_radius_mm`` (a circle) and ``clear_half_widths_mm`` (a
    rectangle, local X / Y), centred on the axis or at ``aperture_center_mm``. ``coating_id`` names a published coating
    (absent: an uncoated interface, whose power split is the bare Fresnel result of the two refractive indices).

    Mechanical edge (catalog schema 3.3): at most one of ``edge_radius_mm`` (a round edge) and ``edge_half_widths_mm``
    (a rectangular one, local X / Y), centred on the axis and containing the clear aperture: how far the surface itself
    extends (its polished and edged size; a prescription's mechanical semi-diameter). Absent, the surface reaches the
    element outline (``SurfaceSequenceModelV1.edge_radius_mm`` / ``edge_half_widths_mm``).
    """

    surface_id: SurfaceId
    vertex_z_mm: OpticalDecimal
    radius_mm: OpticalDecimal | None = None
    conic: OpticalDecimal | None = None
    aspheric_coefficients: tuple[OpticalDecimal, ...] | None = Field(None, min_length=1, max_length=MAX_ASPHERIC_TERMS)
    linear_sag_coefficient: OpticalDecimal | None = None
    cylinder_axis: CylinderAxis | None = None
    sag: SagShapeV1 | None = None
    clear_radius_mm: OpticalDecimal | None = None
    clear_half_widths_mm: DecimalPair | None = None
    aperture_center_mm: DecimalPair | None = None
    clear_shape: ClearShapeV1 | None = None
    holes: tuple[SurfaceHoleV1, ...] | None = Field(None, min_length=1, max_length=MAX_SURFACE_HOLES)
    scatter: SurfaceScatterV1 | None = None
    edge_radius_mm: OpticalDecimal | None = None
    edge_half_widths_mm: DecimalPair | None = None
    coating_id: CoatingId | None = None
    traced: TracedBranches
    status: FactStatus

    @model_validator(mode="before")
    @classmethod
    def _canonical_spellings(cls, data: Any) -> Any:
        return _equivalent_profile_spellings(data)

    @model_validator(mode="after")
    def _consistent(self) -> PrescriptionSurfaceV1:
        self._check_profile()
        self._check_scatter()
        sid = self.surface_id
        if self.edge_radius_mm is not None and self.edge_half_widths_mm is not None:
            raise ValueError(f"surface {sid}: publish at most one of edge_radius_mm (a round edge) and edge_half_widths_mm (a rectangular edge)")
        with localcontext(DECIMAL_CONTEXT):
            if self.edge_radius_mm is not None:
                edge = _positive(self.edge_radius_mm, "edge_radius_mm")
                if self.aperture_reach_sq() > edge * edge:
                    raise ValueError(f"surface {sid}: its clear aperture (reaching {self.aperture_reach_sq().sqrt():.6g} mm from the axis) "
                                     f"exceeds its edge radius {self.edge_radius_mm} mm")
            elif self.edge_half_widths_mm is not None:
                ex, ey = (_positive(v, "edge_half_widths_mm") for v in self.edge_half_widths_mm)
                x_min, x_max, y_min, y_max = self.aperture_bounds_mm()
                if max(-x_min, x_max) > ex or max(-y_min, y_max) > ey:
                    raise ValueError(f"surface {sid}: its clear aperture exceeds its edge half widths "
                                     f"({self.edge_half_widths_mm[0]} x {self.edge_half_widths_mm[1]} mm)")
        return self

    @property
    def has_own_edge(self) -> bool:
        """The surface states its own mechanical edge (else it reaches the element outline)."""

        return self.edge_radius_mm is not None or self.edge_half_widths_mm is not None

    def schema_3_3_features(self) -> tuple[str, ...]:
        """The catalog-schema-3.3 fields this surface uses: its own mechanical edge."""

        return (f"surface {self.surface_id}: edge_radius_mm / edge_half_widths_mm",) if self.has_own_edge else ()


class ParaxialPropertiesV1(StrictContractModel):
    """Paraxial first-order properties of a lens model at ``wavelength_nm`` (DERIVED from the published prescription and
    materials; element frame, mm). ``back_focal_length_mm`` is measured from the last surface vertex to the rear focal
    point along +Z; ``front_focal_length_mm`` from the front focal point to the first vertex (positive when the focal
    point lies before the lens). The principal planes are local z positions.

    ``axis``: ``ROTATIONAL`` for a rotationally symmetric power; for an element with cylindrical surfaces, the local axis
    the surfaces curve along (``X`` or ``Y``): the properties are those of that section (the other one has no power
    from the cylinders); for an element with biconic or toroidal surfaces, the section they are stated for (``X`` or
    ``Y``). An element with a Zernike or XY polynomial surface publishes no paraxial properties."""

    wavelength_nm: OpticalDecimal
    effective_focal_length_mm: OpticalDecimal
    back_focal_length_mm: OpticalDecimal
    front_focal_length_mm: OpticalDecimal
    front_principal_plane_z_mm: OpticalDecimal
    rear_principal_plane_z_mm: OpticalDecimal
    axis: Literal["ROTATIONAL", "X", "Y"] = "ROTATIONAL"
    status: FactStatus


def _spherical_and_centred(surface: PrescriptionSurfaceV1) -> bool:
    return not surface.is_aspheric and not surface.is_axicon and surface.is_rotationally_symmetric


class SurfaceSequenceModelV1(StrictContractModel):
    """Coaxial surfaces along local Z and the media between them, in the element frame (model version 2).

    ``surfaces`` are ordered by increasing ``vertex_z_mm``; ``media[i]`` fills the space between ``surfaces[i]`` and
    ``surfaces[i+1]`` (so ``len(media) == len(surfaces) - 1``); the medium outside the element is ``AIR``, the air of the
    Project's propagation environment. A gap of a Project's declared model may be ``VACUUM`` (an evacuated space).
    The outline is exactly one of ``edge_radius_mm`` (a round element) and ``edge_half_widths_mm`` (a rectangular one,
    local X / Y): the element's largest extent. Consecutive surfaces may not meet inside their common clear aperture
    (checked with the full profile at deterministic points of the apertures, boundary included).

    Mechanical edges: each surface reaches its own edge (``PrescriptionSurfaceV1.edge_radius_mm`` /
    ``edge_half_widths_mm``, of the outline's shape and inside it; absent: the outline itself; an edge equal to the outline
    is read as absent), and the outline is the largest of them (per axis). The rectangular edges of two surfaces bounding
    one glass nest: one contains the other on both axes. Beyond its edge a surface continues as a flat face held at its rim height
    (``held_sag_at``: the sag of the edge point on the ray from the axis), out to the outline of the glass it bounds
    (``glass_outline_mm``: per axis the largest edge of the surface and of its neighbours across glass). The glass
    between two consecutive surfaces extends to the larger of their edges (``gap_outline_mm``). Between a surface's clear
    aperture and its glass outline, light is blocked (``EDGE_AS_OPAQUE_RIM``: the polished rim and the ground flange);
    beyond it light passes by. A model whose surfaces state no edge has every surface reaching the outline.

    The model is characterized for vacuum wavelengths in ``valid_band_nm`` (inside every medium's and every coating's
    band) and, at each coated surface, for the coating's angle-of-incidence range. Outside either, a run is outside the
    model's scope. A single surface with no media is a thin coated element: a mirror (traced ``REFLECTED``), or a coated
    plane whose substrate is not modelled (``SUBSTRATE_NOT_MODELLED``).
    """

    model_kind: Literal["SURFACE_SEQUENCE"] = "SURFACE_SEQUENCE"
    model_version: Literal["2"] = "2"
    fidelity: ModelFidelity
    surfaces: tuple[PrescriptionSurfaceV1, ...] = Field(min_length=1, max_length=MAX_SURFACES)
    media: tuple[MaterialId, ...] = ()
    edge_radius_mm: OpticalDecimal | None = None
    edge_half_widths_mm: DecimalPair | None = None
    paraxial: ParaxialPropertiesV1 | None = None
    valid_band_nm: WavelengthBandV1
    approximations: tuple[SimulationApproximation, ...] = Field(min_length=1)
    status: FactStatus

    @field_validator("approximations", mode="before")
    @classmethod
    def _sort_approximations(cls, value: object) -> object:
        return _sorted_or_unchanged(value, str)

    @model_validator(mode="before")
    @classmethod
    def _canonical_edges(cls, data: Any) -> Any:
        return _edges_at_the_outline_dropped(_outline_filled(data))

    @model_validator(mode="after")
    def _consistent(self) -> SurfaceSequenceModelV1:
        if len(self.media) != len(self.surfaces) - 1:
            raise ValueError("media must have exactly one entry per gap between consecutive surfaces")
        ids = [s.surface_id for s in self.surfaces]
        if ids != [f"S{i}" for i in range(1, len(ids) + 1)]:
            raise ValueError("surfaces must be labelled S1, S2, ... in order")
        z = [Decimal(s.vertex_z_mm) for s in self.surfaces]
        if any(b <= a for a, b in zip(z, z[1:])):
            raise ValueError("surfaces must be ordered by strictly increasing vertex_z_mm")
        if (self.edge_radius_mm is None) == (self.edge_half_widths_mm is None):
            raise ValueError("publish exactly one of edge_radius_mm (a round outline) and edge_half_widths_mm (a rectangular outline)")
        with localcontext(DECIMAL_CONTEXT):
            if self.edge_radius_mm is not None:
                edge = _positive(self.edge_radius_mm, "edge_radius_mm")
                beyond = next((s for s in self.surfaces if s.aperture_reach_sq() > edge * edge), None)
                if beyond is not None:
                    raise ValueError(f"surface {beyond.surface_id}: its clear aperture (reaching {beyond.aperture_reach_sq().sqrt():.6g} mm "
                                     f"from the axis) exceeds the edge radius {self.edge_radius_mm} mm")
            else:
                ex, ey = (_positive(v, "edge_half_widths_mm") for v in self.edge_half_widths_mm)  # type: ignore[union-attr]
                for surface in self.surfaces:
                    x_min, x_max, y_min, y_max = surface.aperture_bounds_mm()
                    if max(-x_min, x_max) > ex or max(-y_min, y_max) > ey:
                        raise ValueError(f"surface {surface.surface_id}: its clear aperture exceeds the edge half widths "
                                         f"({self.edge_half_widths_mm[0]} x {self.edge_half_widths_mm[1]} mm)")
            self._check_surface_edges()
            for (front, front_z), (back, back_z) in zip(zip(self.surfaces, z), zip(self.surfaces[1:], z[1:])):
                if _spherical_and_centred(front) and _spherical_and_centred(back):
                    # Between two centred planes / spheres the gap is monotone in the transverse radius, so its extremes are
                    # at the axis (vertex order, checked above) and at the smaller clear radius.
                    radius = min(Decimal(front.clear_radius_mm), Decimal(back.clear_radius_mm))  # type: ignore[arg-type]
                    points: tuple[tuple[Decimal, Decimal], ...] = ((radius, _ZERO),)
                else:
                    points = tuple(p for p in (*front.aperture_points(), *back.aperture_points())
                                   if front.aperture_contains(*p) and back.aperture_contains(*p))
                for x, y in points:
                    if back_z + back.sag_at(x, y) - front_z - front.sag_at(x, y) <= 0:
                        raise ValueError(f"surfaces {front.surface_id} and {back.surface_id} intersect inside the clear aperture")
        if len(self.surfaces) == 1 and not self.surfaces[0].is_plane and self.surfaces[0].traced is not TracedBranches.REFLECTED:
            raise ValueError(f"surface {self.surfaces[0].surface_id}: a single curved surface has no glass behind it: trace it REFLECTED "
                             "(a mirror), or add the second surface of the lens; a single transmitting surface is a coated plane")
        cylinder_axes = {s.cylinder_axis for s in self.surfaces if s.cylinder_axis is not None}
        if len(cylinder_axes) > 1:
            raise ValueError("the cylindrical surfaces of one element share one cylinder_axis")
        if self.paraxial is not None and any(s.is_freeform for s in self.surfaces):
            if any(s.is_freeform and not s.has_section_powers for s in self.surfaces):
                raise ValueError("an element with a Zernike or XY polynomial surface publishes no paraxial properties")
            if cylinder_axes or self.paraxial.axis == "ROTATIONAL":
                raise ValueError("the paraxial properties of a biconic or toroidal element are those of one section (axis X or Y), "
                                 "without cylindrical surfaces")
        elif self.paraxial is not None:
            expected = "ROTATIONAL" if not cylinder_axes else ("Y" if cylinder_axes == {"X"} else "X")
            if self.paraxial.axis != expected:
                raise ValueError(f"paraxial.axis must be {expected} (the axis the element's surfaces curve along)")
        if len(set(self.approximations)) != len(self.approximations):
            raise ValueError("duplicate approximation")
        return self

    @property
    def is_round(self) -> bool:
        return self.edge_radius_mm is not None

    @property
    def is_rotationally_symmetric(self) -> bool:
        """A round outline and only rotationally symmetric surfaces (no cylinder, centred circular apertures)."""

        return self.is_round and all(s.is_rotationally_symmetric for s in self.surfaces)

    def _check_surface_edges(self) -> None:
        """Every surface's own edge has the outline's shape and lies inside it; the outline is the largest edge; the edges of
        two surfaces bounding one glass nest (one contains the other on both axes, so the glass's outline is one of them)."""

        if not any(s.has_own_edge for s in self.surfaces):
            return
        outline = self.outline_half_widths_mm()
        for surface in self.surfaces:
            if self.is_round and surface.edge_half_widths_mm is not None:
                raise ValueError(f"surface {surface.surface_id}: a round element's surfaces state edge_radius_mm, not edge_half_widths_mm")
            if not self.is_round and surface.edge_radius_mm is not None:
                raise ValueError(f"surface {surface.surface_id}: a rectangular element's surfaces state edge_half_widths_mm, not edge_radius_mm")
            ex, ey = self._own_edge(surface)
            if ex > outline[0] or ey > outline[1]:
                what = f"edge radius {surface.edge_radius_mm} mm" if self.is_round else \
                    f"edge half widths ({surface.edge_half_widths_mm[0]} x {surface.edge_half_widths_mm[1]} mm)"  # type: ignore[index]
                raise ValueError(f"surface {surface.surface_id}: its {what} exceeds the element outline")
        edges = [self._own_edge(s) for s in self.surfaces]
        largest = (max(e[0] for e in edges), max(e[1] for e in edges))
        if largest != outline:
            stated = f"edge_radius_mm {self.edge_radius_mm}" if self.is_round else \
                f"edge_half_widths_mm ({self.edge_half_widths_mm[0]} x {self.edge_half_widths_mm[1]})"  # type: ignore[index]
            reached = f"{largest[0].normalize():f}" if self.is_round else f"({largest[0].normalize():f} x {largest[1].normalize():f})"
            raise ValueError(f"the outline is the largest surface edge: {stated} mm, but the largest surface edge is {reached} mm")
        for k, medium in enumerate(self.media):
            (ax, ay), (bx, by) = edges[k], edges[k + 1]
            if medium not in RESERVED_MEDIA and not (ax <= bx and ay <= by or ax >= bx and ay >= by):
                raise ValueError(f"surfaces {self.surfaces[k].surface_id} and {self.surfaces[k + 1].surface_id} bound one glass: their edges "
                                 f"must nest (one contains the other on both axes), not {ax.normalize():f} x {ay.normalize():f} mm and "
                                 f"{bx.normalize():f} x {by.normalize():f} mm")

    def outline_half_widths_mm(self) -> tuple[Decimal, Decimal]:
        """Half widths ``(x, y)`` of the outline (a round element: its radius twice)."""

        if self.edge_radius_mm is not None:
            radius = Decimal(self.edge_radius_mm)
            return radius, radius
        return Decimal(self.edge_half_widths_mm[0]), Decimal(self.edge_half_widths_mm[1])  # type: ignore[index]

    def _own_edge(self, surface: PrescriptionSurfaceV1) -> tuple[Decimal, Decimal]:
        if surface.edge_radius_mm is not None:
            radius = Decimal(surface.edge_radius_mm)
            return radius, radius
        if surface.edge_half_widths_mm is not None:
            return Decimal(surface.edge_half_widths_mm[0]), Decimal(surface.edge_half_widths_mm[1])
        return self.outline_half_widths_mm()

    def surface_edge_mm(self, index: int) -> tuple[Decimal, Decimal]:
        """Half widths ``(x, y)`` of surface ``index``'s edge (0-based; a round edge: its radius twice): its own edge, else
        the outline."""

        return self._own_edge(self.surfaces[index])

    def glass_outline_mm(self, index: int) -> tuple[Decimal, Decimal]:
        """Half widths ``(x, y)`` of the outline of the glass surface ``index`` bounds: per axis the largest edge of the
        surface and of its neighbours across a non-air medium (a surface with air on both sides: its own edge). The
        surface blocks light from its clear aperture out to here."""

        edges = [self.surface_edge_mm(index)]
        if index > 0 and self.media[index - 1] not in RESERVED_MEDIA:
            edges.append(self.surface_edge_mm(index - 1))
        if index < len(self.media) and self.media[index] not in RESERVED_MEDIA:
            edges.append(self.surface_edge_mm(index + 1))
        return max(e[0] for e in edges), max(e[1] for e in edges)

    def gap_outline_mm(self, gap: int) -> tuple[Decimal, Decimal]:
        """Half widths ``(x, y)`` of the outline of gap ``gap`` (between surfaces ``gap`` and ``gap + 1``): a glass gap
        extends to the larger of the two surfaces' edges (per axis); an air gap lies between two bodies, over the smaller of
        their glass outlines (where both exist)."""

        if self.media[gap] not in RESERVED_MEDIA:
            a, b = self.surface_edge_mm(gap), self.surface_edge_mm(gap + 1)
            return max(a[0], b[0]), max(a[1], b[1])
        a, b = self.glass_outline_mm(gap), self.glass_outline_mm(gap + 1)
        return min(a[0], b[0]), min(a[1], b[1])

    def held_point_mm(self, index: int, x_mm: Decimal, y_mm: Decimal) -> tuple[Decimal, Decimal]:
        """The point of surface ``index`` whose sag holds at ``(x, y)``: the point itself inside the surface's edge, beyond
        it the edge point on the ray from the axis through it."""

        ex, ey = self.surface_edge_mm(index)
        with localcontext(DECIMAL_CONTEXT):
            if self.is_round:
                rho_sq = x_mm * x_mm + y_mm * y_mm
                if rho_sq <= ex * ex:
                    return x_mm, y_mm
                scale = ex / rho_sq.sqrt()
            else:
                reach = max(abs(x_mm) / ex, abs(y_mm) / ey)
                if reach <= _ONE:
                    return x_mm, y_mm
                scale = _ONE / reach
            return x_mm * scale, y_mm * scale

    def held_sag_at(self, index: int, x_mm: Decimal, y_mm: Decimal = _ZERO) -> Decimal:
        """Sag of surface ``index`` at ``(x, y)`` over its whole glass outline: the profile inside its edge, held flat at the
        rim height beyond it (``held_point_mm``). ValueError where the profile is not defined."""

        return self.surfaces[index].sag_at(*self.held_point_mm(index, x_mm, y_mm))

    def clear_aperture_extents_mm(self) -> tuple[Decimal, Decimal]:
        """The element's effective clear aperture: the smallest full width ``(x, y)`` of any surface's clear aperture."""

        extents = [s.aperture_extents_mm() for s in self.surfaces]
        return min(e[0] for e in extents), min(e[1] for e in extents)

    def outline_extents_mm(self) -> tuple[Decimal, Decimal]:
        """Full widths ``(x, y)`` of the outline (a round element: its diameter twice)."""

        if self.edge_radius_mm is not None:
            diameter = Decimal(self.edge_radius_mm) * 2
            return diameter, diameter
        return Decimal(self.edge_half_widths_mm[0]) * 2, Decimal(self.edge_half_widths_mm[1]) * 2  # type: ignore[index]

    @property
    def clear_radius_mm(self) -> Decimal:
        """Half the smallest clear-aperture width of the element (a round element: its smallest clear radius)."""

        return min(self.clear_aperture_extents_mm()) / 2

    def outline_points_mm(self) -> tuple[Vec, ...]:
        """Deterministic points ``(x, y, z)`` of every surface over the element's whole outline -- not only its clear
        aperture: the glass reaches the outline; a surface with its own edge is held flat beyond it (``held_sag_at``) --
        in surface order (points beyond a profile's domain are left out): a
        round outline along local X, Y and the two diagonals, ``OUTLINE_SAMPLES`` steps from the axis to the edge (a
        diagonal step rounded down, so it stays inside; with a two-variable sag shape along ``_shape_directions``); a
        rectangular outline on a grid of ``RECT_OUTLINE_SAMPLES`` steps
        per half width, its corners included. The element's glass lies between these surfaces: a body that contains every
        point (and is convex) contains the element."""

        with localcontext(DECIMAL_CONTEXT):
            xy = self._outline_samples()
            points: list[Vec] = []
            for index, surface in enumerate(self.surfaces):
                vertex = Decimal(surface.vertex_z_mm)
                sag = (lambda x, y, index=index: self.held_sag_at(index, x, y)) if surface.has_own_edge else surface.sag_at
                for x, y in xy:
                    try:
                        points.append((x, y, vertex + sag(x, y)))
                    except ValueError:  # beyond the profile's domain (the optical-solid check refuses such a model)
                        continue
            return tuple(points)

    def _outline_samples(self) -> list[tuple[Decimal, Decimal]]:
        """The transverse points of ``outline_points_mm`` (a round outline: the axis, then row by row outward, a row per step
        with one point per direction; a rectangular one: ``2 RECT_OUTLINE_SAMPLES + 1`` points per row)."""

        if self.edge_radius_mm is not None:
            edge = Decimal(self.edge_radius_mm)
            if any(s.is_freeform for s in self.surfaces):
                directions = _shape_directions()
            else:
                diagonal = _HALF.sqrt().quantize(_SPECTRUM_QUANTUM, rounding=ROUND_FLOOR)
                directions = ((_ONE, _ZERO), (-_ONE, _ZERO), (_ZERO, _ONE), (_ZERO, -_ONE),
                              (diagonal, diagonal), (-diagonal, diagonal), (diagonal, -diagonal), (-diagonal, -diagonal))
            return [(_ZERO, _ZERO)] + [(dx * edge * k / OUTLINE_SAMPLES, dy * edge * k / OUTLINE_SAMPLES)
                                       for k in range(1, OUTLINE_SAMPLES + 1) for dx, dy in directions]
        ex, ey = (Decimal(v) for v in self.edge_half_widths_mm)  # type: ignore[union-attr]
        steps = [Decimal(i - RECT_OUTLINE_SAMPLES) / RECT_OUTLINE_SAMPLES for i in range(2 * RECT_OUTLINE_SAMPLES + 1)]
        return [(ex * a, ey * b) for a in steps for b in steps]

    def _shape_rise_between_samples(self) -> Decimal:
        """How far a two-variable shape can rise between the sample points of ``outline_points_mm``: an eighth of the largest
        second difference of its sag along a row or across rows (the chord of a parabola through three points a step apart
        departs from it by that much). A peak between two directions is not a sample."""

        xy = self._outline_samples()
        if self.edge_radius_mm is not None:
            count = (len(xy) - 1) // OUTLINE_SAMPLES
            start, closed = 1, True
        else:
            count = 2 * RECT_OUTLINE_SAMPLES + 1
            start, closed = 0, False
        worst = _ZERO
        for index, surface in enumerate(self.surfaces):
            if not surface.is_freeform:
                continue
            zs: list[Decimal | None] = []
            for x, y in xy[start:]:
                try:
                    zs.append(self.held_sag_at(index, x, y) if surface.has_own_edge else surface.sag_at(x, y))
                except ValueError:
                    zs.append(None)
            rows = [zs[k:k + count] for k in range(0, len(zs), count)]
            lines = [(*row, *row[:2]) if closed else tuple(row) for row in rows] + list(zip(*rows))
            for line in lines:
                for a, b, c in zip(line, line[1:], line[2:]):
                    if a is not None and b is not None and c is not None:
                        worst = max(worst, abs(a - 2 * b + c))
        return worst / 8

    def outline_z_range_mm(self) -> tuple[Decimal, Decimal]:
        """The smallest and largest local z of the element's surfaces over its whole outline (``outline_points_mm``), rounded
        outward to ``BODY_QUANTUM_MM``: the axial extent of its glass. The one derivation of a model's body extent: the
        Engine's derived design body (view, solid check) and the server's reserved envelope and Optical Layout outline
        both take it."""

        zs = [p[2] for p in self.outline_points_mm()]
        if any(s.is_freeform for s in self.surfaces):
            with localcontext(DECIMAL_CONTEXT):
                rise = self._shape_rise_between_samples()
                zs += [min(zs) - rise, max(zs) + rise]
        if not zs:  # pragma: no cover - the vertex of every surface is inside its profile's domain
            zs = [Decimal(s.vertex_z_mm) for s in self.surfaces]
        return min(zs).quantize(BODY_QUANTUM_MM, rounding=ROUND_FLOOR), max(zs).quantize(BODY_QUANTUM_MM, rounding=ROUND_CEILING)

    def material_ids(self) -> tuple[str, ...]:
        return tuple(sorted({m for m in self.media if m not in RESERVED_MEDIA}))

    def coating_ids(self) -> tuple[str, ...]:
        return tuple(sorted({s.coating_id for s in self.surfaces if s.coating_id is not None}))


# ---------------------------------------------------------------------------------------- SURFACE_SET model


class BodyFaceV1(StrictContractModel):
    """One bounding plane of a convex body: the half-space ``outward_normal . (x - point_mm) <= 0`` (element frame)."""

    point_mm: DecimalTriple
    outward_normal: DecimalTriple

    @model_validator(mode="after")
    def _unit(self) -> BodyFaceV1:
        _unit(self.outward_normal, "outward_normal")
        return self


class ConvexBodyV1(StrictContractModel):
    """The solid an element's light travels in: the intersection of the half-spaces ``faces``, filled with ``medium``.

    * a polyhedron (``side_radius_mm`` absent): 4 to 32 faces bounding a solid of positive volume (a cube, a prism, a
      rectangular or wedged plate); every face is a real face of it (no redundant or repeated plane);
    * a round plate (``side_radius_mm`` present): exactly a front face (outward normal with +Z component) and a back
      face (-Z component) inside the cylinder of that radius about local Z (a round wedged plate); its thickness is
      positive across the whole disc.
    """

    medium: MaterialId
    faces: tuple[BodyFaceV1, ...] = Field(min_length=ROUND_BODY_FACES, max_length=MAX_BODY_FACES)
    side_radius_mm: OpticalDecimal | None = None

    _vertices: tuple[Vec, ...] = PrivateAttr(default=())

    @model_validator(mode="after")
    def _consistent(self) -> ConvexBodyV1:
        if self.medium in RESERVED_MEDIA:
            raise ValueError(f"a body is made of a published material, not {self.medium}")
        with localcontext(DECIMAL_CONTEXT):
            planes = self._planes()
            for (i, (n_i, d_i)), (j, (n_j, d_j)) in _combinations(list(enumerate(planes)), 2):
                if _dot(n_i, n_j) >= _ONE - DIRECTION_TOLERANCE and abs(d_i - d_j) <= GEOMETRY_TOLERANCE_MM:
                    raise ValueError(f"body faces {i + 1} and {j + 1} are the same plane")
            if self.side_radius_mm is not None:
                self._check_round(planes)
            else:
                self._vertices = self._check_polyhedron(planes)
        return self

    def _planes(self) -> list[tuple[Vec, Decimal]]:
        """``(outward unit normal, offset)`` per face: the half-space is ``normal . x <= offset``."""

        planes = []
        for face in self.faces:
            normal = _vec(face.outward_normal)
            planes.append((normal, _dot(normal, _vec(face.point_mm))))
        return planes

    def _check_round(self, planes: list[tuple[Vec, Decimal]]) -> None:
        radius = _positive(self.side_radius_mm, "side_radius_mm")  # type: ignore[arg-type]
        if len(planes) != ROUND_BODY_FACES:
            raise ValueError("a round body (side_radius_mm) has exactly a front and a back face")
        (front, front_offset), (back, back_offset) = planes
        if not (front[2] > 0 and back[2] < 0):
            raise ValueError("a round body's first face faces +Z (front) and its second -Z (back)")
        # Front surface z = (d_f - n_f.xy) / n_fz, back z = (d_b - n_b.xy) / n_bz; their difference is linear in (x, y),
        # so its minimum over the disc is the value at the centre minus |gradient| * radius.
        gradient = (back[0] / back[2] - front[0] / front[2], back[1] / back[2] - front[1] / front[2])
        centre = front_offset / front[2] - back_offset / back[2]
        if centre - (gradient[0] * gradient[0] + gradient[1] * gradient[1]).sqrt() * radius <= 0:
            raise ValueError("a round body's faces meet inside its side radius (its thickness must be positive across the disc)")

    def _check_polyhedron(self, planes: list[tuple[Vec, Decimal]]) -> tuple[Vec, ...]:
        if len(planes) < MIN_BODY_FACES:
            raise ValueError(f"a polyhedral body has at least {MIN_BODY_FACES} faces")
        normals = [n for n, _ in planes]
        # Bounded iff no direction d has n_k . d <= 0 for every face; such a cone's extreme rays are n_i x n_j.
        spanning = False
        for n_i, n_j in _combinations(normals, 2):
            direction = _cross(n_i, n_j)
            if _dot(direction, direction) <= _SINGULAR * _SINGULAR:
                continue
            spanning = True
            for sign in (_ONE, -_ONE):
                ray = _scale(direction, sign)
                if all(_dot(n_k, ray) <= _SINGULAR for n_k in normals):
                    raise ValueError("the body's faces do not enclose a bounded solid")
        if not spanning:
            raise ValueError("the body's faces do not enclose a bounded solid")
        vertices: list[Vec] = []
        for (n_i, d_i), (n_j, d_j), (n_k, d_k) in _combinations(planes, 3):
            jk, ki, ij = _cross(n_j, n_k), _cross(n_k, n_i), _cross(n_i, n_j)
            determinant = _dot(n_i, jk)
            if abs(determinant) <= _SINGULAR:
                continue
            point = _scale(_add(_add(_scale(jk, d_i), _scale(ki, d_j)), _scale(ij, d_k)), _ONE / determinant)
            if all(_dot(n, point) - d <= _VERTEX_TOLERANCE_MM for n, d in planes) and not any(
                _close(point, seen, _VERTEX_TOLERANCE_MM) for seen in vertices
            ):
                vertices.append(point)
        for index, (n, d) in enumerate(planes):
            if sum(1 for v in vertices if abs(_dot(n, v) - d) <= _VERTEX_TOLERANCE_MM) < 3:
                raise ValueError(f"body face {index + 1} does not bound the body (it touches it in fewer than 3 corners)")
        ordered = tuple(sorted(vertices))
        if _volume_and_centroid(ordered, planes)[0] <= _SINGULAR:
            raise ValueError("the body has no volume")
        return ordered

    @property
    def is_round(self) -> bool:
        return self.side_radius_mm is not None

    def vertices_mm(self) -> tuple[Vec, ...]:
        """The polyhedron's corners in ascending (x, y, z) order (empty for a round body)."""

        return self._vertices

    def face_vertices_mm(self, index: int) -> tuple[Vec, ...]:
        """The corners of face ``index`` (0-based) in order around the face (counter-clockwise seen from outside)."""

        with localcontext(DECIMAL_CONTEXT):
            normal, offset = self._planes()[index]
            on_face = [v for v in self._vertices if abs(_dot(normal, v) - offset) <= _VERTEX_TOLERANCE_MM]
            return _around(on_face, normal)

    def volume_and_centroid(self) -> tuple[Decimal, Vec]:
        """Volume (mm^3) and centroid (centre of volume, element frame) of the body.

        A round plate is closed form: its faces are ``z = f(x, y)`` linear over the disc of radius ``r`` (front ``u``, back
        ``l``), so with ``t = u - l = a + b x + c y`` the volume is ``pi r^2 a``, the centroid ``x = b r^2 / (4 a)``,
        ``y = c r^2 / (4 a)`` and ``z = ((u0^2 - l0^2) r^2 + (u1^2 + u2^2 - l1^2 - l2^2) r^4 / 4) / (2 a r^2)``."""

        with localcontext(DECIMAL_CONTEXT):
            if not self.is_round:
                return _volume_and_centroid(self._vertices, self._planes())
            radius = Decimal(self.side_radius_mm)  # type: ignore[arg-type]
            (front, front_offset), (back, back_offset) = self._planes()
            upper = (front_offset / front[2], -front[0] / front[2], -front[1] / front[2])
            lower = (back_offset / back[2], -back[0] / back[2], -back[1] / back[2])
            a, b, c = (upper[k] - lower[k] for k in range(3))
            r2 = radius * radius
            z = ((upper[0] ** 2 - lower[0] ** 2) * r2 + (upper[1] ** 2 + upper[2] ** 2 - lower[1] ** 2 - lower[2] ** 2) * r2 * r2 / 4) / (2 * a * r2)
            return _PI * r2 * a, (b * r2 / (4 * a), c * r2 / (4 * a), z)

    def contains(self, point: Vec, tolerance: Decimal = GEOMETRY_TOLERANCE_MM) -> bool:
        """Whether ``point`` lies in the body (its boundary included, up to ``tolerance`` mm)."""

        with localcontext(DECIMAL_CONTEXT):
            if any(_dot(n, point) - d > tolerance for n, d in self._planes()):
                return False
            if self.side_radius_mm is not None:
                return (point[0] * point[0] + point[1] * point[1]).sqrt() <= Decimal(self.side_radius_mm) + tolerance
            return True

    def faces_through(self, point: Vec, tolerance: Decimal = GEOMETRY_TOLERANCE_MM) -> tuple[int, ...]:
        """Indexes (0-based) of the faces whose plane passes through ``point`` (within ``tolerance`` mm)."""

        with localcontext(DECIMAL_CONTEXT):
            return tuple(i for i, (n, d) in enumerate(self._planes()) if abs(_dot(n, point) - d) <= tolerance)

    def on_boundary(self, point: Vec, tolerance: Decimal = GEOMETRY_TOLERANCE_MM) -> bool:
        """Whether ``point`` lies on the body's surface (a face, or a round body's side), up to ``tolerance`` mm."""

        if not self.contains(point, tolerance):
            return False
        if self.faces_through(point, tolerance):
            return True
        if self.side_radius_mm is None:
            return False
        with localcontext(DECIMAL_CONTEXT):
            return abs((point[0] * point[0] + point[1] * point[1]).sqrt() - Decimal(self.side_radius_mm)) <= tolerance

    def z_range_mm(self) -> tuple[Decimal, Decimal]:
        """Smallest and largest local z of the body."""

        with localcontext(DECIMAL_CONTEXT):
            if self.side_radius_mm is None:
                return min(v[2] for v in self._vertices), max(v[2] for v in self._vertices)
            radius = Decimal(self.side_radius_mm)
            (front, front_offset), (back, back_offset) = self._planes()
            reach_front = (front[0] * front[0] + front[1] * front[1]).sqrt() / front[2] * radius
            reach_back = (back[0] * back[0] + back[1] * back[1]).sqrt() / abs(back[2]) * radius
            return back_offset / back[2] - reach_back, front_offset / front[2] + reach_front


def _around(points: list[Vec], normal: Vec) -> tuple[Vec, ...]:
    """Coplanar ``points`` ordered counter-clockwise about ``normal`` (by the exact "diamond" pseudo-angle, no
    trigonometry)."""

    if len(points) < 3:
        return tuple(points)
    n = Decimal(len(points))
    centre = (sum(p[0] for p in points) / n, sum(p[1] for p in points) / n, sum(p[2] for p in points) / n)
    first = _sub(points[0], centre)
    second = _cross(normal, first)

    def pseudo_angle(point: Vec) -> Decimal:
        offset = _sub(point, centre)
        x, y = _dot(offset, first), _dot(offset, second)
        if y >= 0:
            return y / (x + y) if x >= 0 else _ONE - x / (y - x)
        return _TWO - y / (-x - y) if x < 0 else 3 + x / (x - y)

    return tuple(sorted(points, key=pseudo_angle))


def _volume_and_centroid(vertices: tuple[Vec, ...], planes: list[tuple[Vec, Decimal]]) -> tuple[Decimal, Vec]:
    """Volume and centre of volume of the convex polyhedron with these corners and faces (tetrahedra from the mean corner
    over a fan of every face)."""

    count = Decimal(len(vertices))
    inner = (sum(v[0] for v in vertices) / count, sum(v[1] for v in vertices) / count, sum(v[2] for v in vertices) / count)
    total = _ZERO
    moment: Vec = (_ZERO, _ZERO, _ZERO)
    for normal, offset in planes:
        face = _around([v for v in vertices if abs(_dot(normal, v) - offset) <= _VERTEX_TOLERANCE_MM], normal)
        if len(face) < 3:
            continue
        k = Decimal(len(face))
        centre = (sum(p[0] for p in face) / k, sum(p[1] for p in face) / k, sum(p[2] for p in face) / k)
        for a, b in zip(face, (*face[1:], face[0])):
            six = abs(_dot(_sub(a, inner), _cross(_sub(b, inner), _sub(centre, inner))))
            total += six
            moment = _add(moment, _scale(_add(_add(inner, centre), _add(a, b)), six / 4))
    if total == 0:
        return _ZERO, inner
    return total / 6, _scale(moment, _ONE / total)


class PosedSurfaceV1(_SurfaceProfile, StrictContractModel):
    """One surface of a ``SURFACE_SET`` model, placed in the element frame.

    Its vertex is ``origin_mm``; ``normal`` (unit) is its axis and front direction; ``u_axis`` (unit, perpendicular to
    ``normal``) is its local X and ``v = normal x u_axis`` its local Y. The profile and clear aperture are those of
    ``PrescriptionSurfaceV1`` in this (u, v) frame (``radius_mm`` positive when the centre of curvature lies on the
    +normal side; ``cylinder_axis = "X"``: straight along u). ``medium_front`` fills the +normal (front) side,
    ``medium_back`` the other; a ``REFLECTED`` surface reflects light arriving on its front side.
    """

    surface_id: SurfaceId
    origin_mm: DecimalTriple
    normal: DecimalTriple
    u_axis: DecimalTriple
    radius_mm: OpticalDecimal | None = None
    conic: OpticalDecimal | None = None
    aspheric_coefficients: tuple[OpticalDecimal, ...] | None = Field(None, min_length=1, max_length=MAX_ASPHERIC_TERMS)
    linear_sag_coefficient: OpticalDecimal | None = None
    cylinder_axis: CylinderAxis | None = None
    sag: SagShapeV1 | None = None
    clear_radius_mm: OpticalDecimal | None = None
    clear_half_widths_mm: DecimalPair | None = None
    aperture_center_mm: DecimalPair | None = None
    clear_shape: ClearShapeV1 | None = None
    holes: tuple[SurfaceHoleV1, ...] | None = Field(None, min_length=1, max_length=MAX_SURFACE_HOLES)
    scatter: SurfaceScatterV1 | None = None
    medium_front: MaterialId
    medium_back: MaterialId
    coating_id: CoatingId | None = None
    traced: TracedBranches
    status: FactStatus

    @model_validator(mode="before")
    @classmethod
    def _canonical_spellings(cls, data: Any) -> Any:
        return _equivalent_profile_spellings(data)

    @model_validator(mode="after")
    def _consistent(self) -> PosedSurfaceV1:
        normal = _unit(self.normal, f"surface {self.surface_id} normal")
        u_axis = _unit(self.u_axis, f"surface {self.surface_id} u_axis")
        with localcontext(DECIMAL_CONTEXT):
            if abs(_dot(normal, u_axis)) > DIRECTION_TOLERANCE:
                raise ValueError(f"surface {self.surface_id}: u_axis must be perpendicular to normal")
        self._check_profile()
        self._check_scatter()
        if (self.medium_front == self.medium_back and self.traced is TracedBranches.TRANSMITTED and self.coating_id is None):
            raise ValueError(f"surface {self.surface_id}: an uncoated transmitting surface between the same medium does nothing")
        return self

    def frame(self) -> tuple[Vec, Vec, Vec, Vec]:
        """``(origin, normal, u, v)`` of the surface in the element frame (exact decimals)."""

        with localcontext(DECIMAL_CONTEXT):
            normal, u_axis = _vec(self.normal), _vec(self.u_axis)
            return _vec(self.origin_mm), normal, u_axis, _cross(normal, u_axis)

    def point_at(self, x_mm: Decimal, y_mm: Decimal) -> Vec:
        """The surface point at local transverse coordinates ``(x, y)`` (along u, v), in the element frame."""

        origin, normal, u_axis, v_axis = self.frame()
        with localcontext(DECIMAL_CONTEXT):
            return _add(_add(origin, _scale(u_axis, x_mm)), _add(_scale(v_axis, y_mm), _scale(normal, self.sag_at(x_mm, y_mm))))

    def aperture_center_point(self) -> Vec:
        """The centre of the clear aperture on the surface, in the element frame."""

        x, y = self._center()
        return self.point_at(x, y)


def _shape_directions() -> tuple[tuple[Decimal, Decimal], ...]:
    """Unit directions around the circle: the rational points ``((1 - t^2) / (1 + t^2), 2 t / (1 + t^2))`` with ``t`` in equal
    steps up to ``tan(pi / 8)``, and their mirror images (identical on every runtime; each at most 1 long, so inside the
    circle): the directions along which a surface whose sag is not monotone in the radius is sampled."""

    with localcontext(DECIMAL_CONTEXT):
        tangent = _TWO.sqrt() - _ONE
        octant = []
        for k in range(_SHAPE_DIRECTIONS_PER_OCTANT + 1):
            t = tangent * k / _SHAPE_DIRECTIONS_PER_OCTANT
            denominator = _ONE + t * t
            octant.append(((_ONE - t * t) / denominator, 2 * t / denominator))
        quarter = [*octant, *[(y, x) for x, y in reversed(octant[:-1])]]
        half = [*quarter, *[(-y, x) for x, y in quarter[1:]]]
        return (*half, *[(-x, -y) for x, y in half[1:-1]])


def _circle_rim() -> list[tuple[Decimal, Decimal]]:
    """Rational points ``((1 - t^2) / (1 + t^2), 2 t / (1 + t^2))`` of the unit circle and their mirror images: t in [-1, 1]
    in ``_APERTURE_RIM_STEPS`` steps covers the half x >= 0 (largest angular step 2 / _APERTURE_RIM_STEPS rad)."""

    points = []
    for k in range(_APERTURE_RIM_STEPS + 1):
        t = Decimal(2 * k - _APERTURE_RIM_STEPS) / _APERTURE_RIM_STEPS
        x, y = (_ONE - t * t) / (_ONE + t * t), 2 * t / (_ONE + t * t)
        points += [(x, y), (-x, y)]
    return points


def _aperture_in_body(surface: PosedSurfaceV1, body: ConvexBodyV1, planes: list[tuple[Vec, Decimal]]) -> bool:
    """Whether the whole clear aperture of ``surface`` lies in ``body`` (within ``GEOMETRY_TOLERANCE_MM``).

    A rectangular aperture: its four corners (a convex body holds the convex rectangle when it holds its corners; exact
    for a plane). A circular one of radius ``r`` about the centre ``c`` with in-plane axes ``u``, ``v``: against a face
    ``n . x <= d`` its farthest point reaches ``n . c + r sqrt((n . u)^2 + (n . v)^2)`` (exact for a plane); against a
    round body's side its radial distance is at most ``|c_xy| + r s`` (``s`` the largest radial stretch of the in-plane
    axes; exact when the aperture is centred on the axis or lies across it), else the rim is checked at ``_circle_rim``
    points. A curved surface's rim is checked on its profile at the corners / rim points."""

    _, _, u_axis, v_axis = surface.frame()
    cx, cy = surface._center()
    tolerance = GEOMETRY_TOLERANCE_MM
    if surface.clear_radius_mm is None:
        hx, hy = surface._half_widths()
        return all(body.contains(surface.point_at(cx + sx * hx, cy + sy * hy), tolerance) for sx in (-_ONE, _ONE) for sy in (-_ONE, _ONE))
    radius = Decimal(surface.clear_radius_mm)
    if not surface.is_plane:
        return all(body.contains(surface.point_at(cx + radius * x, cy + radius * y), tolerance) for x, y in _circle_rim())
    centre = surface.point_at(cx, cy)
    for n, d in planes:
        nu, nv = _dot(n, u_axis), _dot(n, v_axis)
        if _dot(n, centre) + radius * (nu * nu + nv * nv).sqrt() - d > tolerance:
            return False
    if body.side_radius_mm is None:
        return True
    side = Decimal(body.side_radius_mm)
    a = u_axis[0] * u_axis[0] + u_axis[1] * u_axis[1]
    b = u_axis[0] * v_axis[0] + u_axis[1] * v_axis[1]
    c = v_axis[0] * v_axis[0] + v_axis[1] * v_axis[1]
    stretch = ((a + c) / 2 + (((a - c) / 2) ** 2 + b * b).sqrt()).sqrt()
    if (centre[0] * centre[0] + centre[1] * centre[1]).sqrt() + radius * stretch <= side + tolerance:
        return True
    for x, y in _circle_rim():
        rim = _add(centre, _add(_scale(u_axis, radius * x), _scale(v_axis, radius * y)))
        if (rim[0] * rim[0] + rim[1] * rim[1]).sqrt() > side + tolerance:
            return False
    return True


class SurfaceSetModelV1(StrictContractModel):
    """Surfaces placed anywhere in the element frame and, optionally, the convex solid between them (model version 1).

    ``surfaces`` (S1, S2, ...) are ``PosedSurfaceV1``; ``body`` is the solid the light travels in (a cube, a prism, a
    wedged plate), absent for an element of thin coated surfaces whose substrate is not modelled (an off-axis mirror's
    reflecting face). Every non-air medium of a surface is the body's medium; every surface's aperture centre lies on or
    in the body; a surface on a body face lies in that face's plane, parallel to it, with air on the outside and the
    body's medium inside; a surface inside the body has the body's medium on both sides (a coated internal plane, e.g.
    a cube splitter's diagonal). Light that reaches a body face carrying no traced surface leaves the model there
    (``UNTRACED_FACES_BLOCK``, listed exactly when the body has such a face).

    Characterized for vacuum wavelengths in ``valid_band_nm`` and, at each coated surface, for the coating's
    angle-of-incidence range.
    """

    model_kind: Literal["SURFACE_SET"] = "SURFACE_SET"
    model_version: Literal["1"] = "1"
    fidelity: ModelFidelity
    surfaces: tuple[PosedSurfaceV1, ...] = Field(min_length=1, max_length=MAX_SURFACES)
    body: ConvexBodyV1 | None = None
    valid_band_nm: WavelengthBandV1
    approximations: tuple[SimulationApproximation, ...] = Field(min_length=1)
    status: FactStatus

    _untraced_faces: bool = PrivateAttr(default=False)

    @field_validator("approximations", mode="before")
    @classmethod
    def _sort_approximations(cls, value: object) -> object:
        return _sorted_or_unchanged(value, str)

    @property
    def has_untraced_faces(self) -> bool:
        """Whether a face of the body carries no traced surface (light reaching it leaves the model:
        ``UNTRACED_FACES_BLOCK``)."""

        return self._untraced_faces

    @model_validator(mode="after")
    def _consistent(self) -> SurfaceSetModelV1:
        ids = [s.surface_id for s in self.surfaces]
        if ids != [f"S{i}" for i in range(1, len(ids) + 1)]:
            raise ValueError("surfaces must be labelled S1, S2, ... in order")
        if len(set(self.approximations)) != len(self.approximations):
            raise ValueError("duplicate approximation")
        media = {m for s in self.surfaces for m in (s.medium_front, s.medium_back)} - {AIR}
        body = self.body
        if body is None:
            if media:
                raise ValueError("a SURFACE_SET model with a non-air medium publishes its body")
            if SimulationApproximation.UNTRACED_FACES_BLOCK in self.approximations:
                raise ValueError("UNTRACED_FACES_BLOCK applies to a model with a body")
            return self
        if media - {body.medium}:
            raise ValueError(f"every non-air medium of a SURFACE_SET model is its body's medium ({body.medium})")
        covered: set[int] = set()
        with localcontext(DECIMAL_CONTEXT):
            planes = body._planes()
            for surface in self.surfaces:
                centre = surface.aperture_center_point()
                if not body.contains(centre):
                    raise ValueError(f"surface {surface.surface_id} lies outside the body")
                if not _aperture_in_body(surface, body, planes):
                    raise ValueError(f"surface {surface.surface_id}: its clear aperture reaches outside the body")
                _, normal, _, _ = surface.frame()
                on_faces = body.faces_through(centre)
                if not on_faces:
                    if surface.medium_front != body.medium or surface.medium_back != body.medium:
                        raise ValueError(f"surface {surface.surface_id} lies inside the body: the body's medium is on both its sides")
                    continue
                for index in on_faces:
                    alignment = _dot(normal, planes[index][0])
                    if alignment >= _ONE - DIRECTION_TOLERANCE:
                        expected = (AIR, body.medium)
                    elif alignment <= DIRECTION_TOLERANCE - _ONE:
                        expected = (body.medium, AIR)
                    else:
                        raise ValueError(f"surface {surface.surface_id} lies on body face {index + 1} but is not parallel to it")
                    if (surface.medium_front, surface.medium_back) != expected:
                        raise ValueError(
                            f"surface {surface.surface_id} on body face {index + 1}: medium_front is {expected[0]} and medium_back "
                            f"{expected[1]} (air outside the body, {body.medium} inside)"
                        )
                    covered.add(index)
        self._untraced_faces = len(covered) < len(body.faces)
        if not self._untraced_faces and SimulationApproximation.UNTRACED_FACES_BLOCK in self.approximations:
            raise ValueError("UNTRACED_FACES_BLOCK is listed only when a body face carries no traced surface")
        return self

    @property
    def is_rotationally_symmetric(self) -> bool:
        return False

    def material_ids(self) -> tuple[str, ...]:
        return tuple(sorted({m for s in self.surfaces for m in (s.medium_front, s.medium_back) if m not in RESERVED_MEDIA}))

    def coating_ids(self) -> tuple[str, ...]:
        return tuple(sorted({s.coating_id for s in self.surfaces if s.coating_id is not None}))


# ---------------------------------------------------------------------------------- JOINED_BODIES model

#: The most bodies one ``JOINED_BODIES`` model holds, the most joints it states and the most crystals one body orients.
MAX_JOINED_BODIES = 8
MAX_BODY_JOINTS = 8
MAX_BODY_OPTIC_AXES = 4
#: The widest cement layer of a joint (mm). An optical contact's faces coincide within ``JOINT_GAP_TOLERANCE_MM`` along their
#: normal, a cement layer is thicker; two joined faces face each other when their unit front normals add up to at most
#: ``JOINT_FACING_TOLERANCE`` (an angle of about that in rad).
MAX_JOINT_CEMENT_MM = Decimal(1)
JOINT_GAP_TOLERANCE_MM = Decimal("1e-6")
JOINT_FACING_TOLERANCE = Decimal("1e-6")

BodyMemberId = Annotated[
    str,
    StringConstraints(strict=True, pattern=r"^[A-Z][A-Z0-9_]{0,15}$"),
    Field(description="Body id within a JOINED_BODIES model: an uppercase letter, then uppercase letters, digits or '_' (e.g. A, B)."),
]


class OpticAxisV1(StrictContractModel):
    """The optic axis of a uniaxial crystal in one body: ``material_id`` names a crystal (``PublicMaterialV1.birefringence``)
    the body is made of and ``direction`` the axis in the element frame. Only the line matters: the direction is normalized
    and its sign does not change the physics."""

    material_id: MaterialId
    direction: DecimalTriple

    @model_validator(mode="after")
    def _direction(self) -> OpticAxisV1:
        with localcontext(DECIMAL_CONTEXT):
            if _dot(_vec(self.direction), _vec(self.direction)) == 0:
                raise ValueError(f"optic axis of {self.material_id}: the direction must not be zero")
        return self

    def unit(self) -> Vec:
        """The direction scaled to length 1 (exact decimal arithmetic in ``DECIMAL_CONTEXT``)."""

        with localcontext(DECIMAL_CONTEXT):
            vector = _vec(self.direction)
            return _scale(vector, _ONE / _norm(vector))


class JoinedBodyV1(StrictContractModel):
    """One body of a ``JOINED_BODIES`` model: a ``SURFACE_SET`` model with its body (``model.body``), surfaces and body in the
    element frame, and ``optic_axes``: the optic axis (``OpticAxisV1``) of the crystal the body is made of (absent for an
    isotropic body). Every surface bounding a crystal is a plane."""

    member_id: BodyMemberId
    model: SurfaceSetModelV1
    optic_axes: tuple[OpticAxisV1, ...] | None = Field(None, min_length=1, max_length=MAX_BODY_OPTIC_AXES)

    @model_validator(mode="after")
    def _consistent(self) -> JoinedBodyV1:
        if self.model.body is None:
            raise ValueError(f"body {self.member_id}: a joined body is a SURFACE_SET model with its body")
        if self.model.body.is_round:
            raise ValueError(f"body {self.member_id}: a joined body is a polyhedron (no side_radius_mm)")
        duplicates = duplicate_keys(self.optic_axes or (), "material_id")
        if duplicates:
            raise ValueError(f"body {self.member_id}: optic_axes states material {', '.join(d[0] for d in duplicates)} more than once")
        unused = sorted({a.material_id for a in self.optic_axes or ()} - set(self.model.material_ids()))
        if unused:
            raise ValueError(f"body {self.member_id}: optic_axes names {unused}, not a medium of the body")
        return self

    def optic_axis(self, material_id: str) -> OpticAxisV1 | None:
        """The optic axis this body states for the crystal ``material_id`` (``None``: not stated)."""

        return next((a for a in self.optic_axes or () if a.material_id == material_id), None)


class BodyJointV1(StrictContractModel):
    """Two bodies of a ``JOINED_BODIES`` model joined face to face: surface ``surface_a`` of body ``member_a`` and surface
    ``surface_b`` of body ``member_b``. Both are uncoated plane faces of their bodies traced ``TRANSMITTED`` (air in front,
    the body's medium behind) and they face each other (their front normals are opposite). Without ``cement_material_id``
    the faces coincide (an optical contact): the element has one interface there, body A's medium on one side and body B's on
    the other; with it, the faces are parallel and apart by more than ``JOINT_GAP_TOLERANCE_MM`` and at most
    ``MAX_JOINT_CEMENT_MM`` (the cement layer, that isotropic material). The joint's interface is body A's surface (its clear
    aperture)."""

    member_a: BodyMemberId
    surface_a: SurfaceId
    member_b: BodyMemberId
    surface_b: SurfaceId
    cement_material_id: MaterialId | None = None

    @model_validator(mode="after")
    def _distinct(self) -> BodyJointV1:
        if self.member_a == self.member_b:
            raise ValueError(f"a joint joins two different bodies, got {self.member_a} twice")
        return self


class JoinedBodiesModelV1(StrictContractModel):
    """Two or more solid bodies in the element frame, joined face to face or apart across air (model version 1; catalog
    schema 3.5): a polarizing prism of two crystal prisms (Glan-Taylor and Glan-laser across an air gap, Glan-Thompson across a
    cement layer, Wollaston and Rochon in optical contact).

    ``members`` (``JoinedBodyV1``): each a ``SURFACE_SET`` model with its body, surfaces and body in the element frame, and the
    optic axis of the crystal it is made of; the bodies do not overlap. ``joints`` (``BodyJointV1``): faces joined by an optical
    contact or a cement layer (a face is in one joint at most); faces not in a joint meet air. Characterized for vacuum
    wavelengths in ``valid_band_nm`` (inside every body's band); ``approximations`` lists every body's and the model's own
    (``CEMENT_LAYER_INTERFERENCE_NOT_MODELLED`` exactly when a joint has a cement layer).
    """

    model_kind: Literal["JOINED_BODIES"] = "JOINED_BODIES"
    model_version: Literal["1"] = "1"
    fidelity: ModelFidelity
    members: tuple[JoinedBodyV1, ...] = Field(min_length=2, max_length=MAX_JOINED_BODIES)
    joints: tuple[BodyJointV1, ...] | None = Field(None, min_length=1, max_length=MAX_BODY_JOINTS)
    valid_band_nm: WavelengthBandV1
    approximations: tuple[SimulationApproximation, ...] = Field(min_length=1)
    status: FactStatus

    @field_validator("approximations", mode="before")
    @classmethod
    def _sort_approximations(cls, value: object) -> object:
        return _sorted_or_unchanged(value, str)

    @model_validator(mode="after")
    def _consistent(self) -> JoinedBodiesModelV1:
        duplicates = duplicate_keys(self.members, "member_id")
        if duplicates:
            raise ValueError(f"duplicate member_id in a JOINED_BODIES model: {duplicates}")
        if len(set(self.approximations)) != len(self.approximations):
            raise ValueError("duplicate approximation")
        for member in self.members:
            if not self.valid_band_nm.within(member.model.valid_band_nm):
                raise ValueError(f"the model band lies outside body {member.member_id}'s band")
            missing = sorted(set(member.model.approximations) - set(self.approximations))
            if missing:
                raise ValueError(f"approximations lists every body's approximations (body {member.member_id}: {missing})")
        cemented = any(j.cement_material_id is not None for j in self.joints or ())
        if cemented != (SimulationApproximation.CEMENT_LAYER_INTERFERENCE_NOT_MODELLED in self.approximations):
            raise ValueError("CEMENT_LAYER_INTERFERENCE_NOT_MODELLED is listed exactly when a joint has a cement layer")
        by_id = {m.member_id: m for m in self.members}
        faces: list[tuple[str, str]] = []
        for joint in self.joints or ():
            sides = []
            for member_id, surface_id in ((joint.member_a, joint.surface_a), (joint.member_b, joint.surface_b)):
                member = by_id.get(member_id)
                if member is None:
                    raise ValueError(f"a joint names {member_id}, which is not a body of the model")
                sides.append(_joined_face(member, surface_id))
                faces.append((member_id, surface_id))
            _check_joint_geometry(joint, sides[0], sides[1])
        repeated = sorted({f"{m}.{s}" for m, s in faces if faces.count((m, s)) > 1})
        if repeated:
            raise ValueError(f"a body surface is in more than one joint: {repeated}")
        return self

    @property
    def is_rotationally_symmetric(self) -> bool:
        return False

    def member_surfaces(self) -> tuple[PosedSurfaceV1, ...]:
        """Every body's surfaces, body after body."""

        return tuple(s for m in self.members for s in m.model.surfaces)

    def material_ids(self) -> tuple[str, ...]:
        """The bodies' media and the joints' cements."""

        media = {m for member in self.members for m in member.model.material_ids()}
        media |= {j.cement_material_id for j in self.joints or () if j.cement_material_id is not None}
        return tuple(sorted(media))

    def coating_ids(self) -> tuple[str, ...]:
        return tuple(sorted({c for member in self.members for c in member.model.coating_ids()}))

    def cement_ids(self) -> tuple[str, ...]:
        """The cement materials of the joints."""

        return tuple(sorted({j.cement_material_id for j in self.joints or () if j.cement_material_id is not None}))


def _joined_face(member: JoinedBodyV1, surface_id: str) -> PosedSurfaceV1:
    """Surface ``surface_id`` of ``member``, checked to be an uncoated plane face of its body traced ``TRANSMITTED`` (air in front,
    the body's medium behind)."""

    where = f"joint face {member.member_id}.{surface_id}"
    surface = next((s for s in member.model.surfaces if s.surface_id == surface_id), None)
    if surface is None:
        raise ValueError(f"{where}: body {member.member_id} has no surface {surface_id}")
    if not surface.is_plane or surface.traced is not TracedBranches.TRANSMITTED or surface.coating_id is not None:
        raise ValueError(f"{where}: a joined face is an uncoated plane traced TRANSMITTED")
    body = member.model.body
    assert body is not None  # (JoinedBodyV1 requires it)
    if surface.medium_front != AIR or surface.medium_back != body.medium:
        raise ValueError(f"{where}: a joined face is a face of its body (air in front, {body.medium} behind)")
    return surface


def _check_joint_geometry(joint: BodyJointV1, a: PosedSurfaceV1, b: PosedSurfaceV1) -> None:
    """The two faces face each other (front normals opposite within ``JOINT_FACING_TOLERANCE``) and lie at the joint's spacing
    along A's normal: coincident within ``JOINT_GAP_TOLERANCE_MM`` (an optical contact) or a cement layer thicker than that and
    at most ``MAX_JOINT_CEMENT_MM`` (exact decimal arithmetic)."""

    label = f"joint {joint.member_a}.{joint.surface_a} / {joint.member_b}.{joint.surface_b}"
    with localcontext(DECIMAL_CONTEXT):
        origin_a, normal_a, _, _ = a.frame()
        origin_b, normal_b, _, _ = b.frame()
        if _norm(_add(normal_a, normal_b)) > JOINT_FACING_TOLERANCE:
            raise ValueError(f"{label}: the faces do not face each other (their front normals are not opposite)")
        gap = _dot(_sub(origin_b, origin_a), normal_a)
        if joint.cement_material_id is None:
            if abs(gap) > JOINT_GAP_TOLERANCE_MM:
                raise ValueError(f"{label}: an optical contact's faces coincide; face {joint.member_b}.{joint.surface_b} lies "
                                 f"{gap.normalize()} mm from face {joint.member_a}.{joint.surface_a}")
        elif not JOINT_GAP_TOLERANCE_MM < gap <= MAX_JOINT_CEMENT_MM:
            raise ValueError(f"{label}: the cement layer between the faces is {gap.normalize()} mm thick; a cement layer is more "
                             f"than {JOINT_GAP_TOLERANCE_MM} mm and at most {MAX_JOINT_CEMENT_MM} mm thick")


# ---------------------------------------------------------------------------------- POLARIZATION_ELEMENT model


class PolarizationElementKind(StrEnum):
    RETARDER = "RETARDER"
    LINEAR_POLARIZER = "LINEAR_POLARIZER"
    CIRCULAR_POLARIZER = "CIRCULAR_POLARIZER"


class RetardanceDispersion(StrEnum):
    """How a retarder's retardance depends on wavelength inside its valid band.

    ``INVERSE_WAVELENGTH``: ``delta(lambda) = delta0 * lambda0 / lambda`` (a zero-order plate, birefringence dispersion
    not modelled); ``CONSTANT``: the design retardance (an achromatic plate within its specified band); ``TABULATED``: the
    retardance stated per wavelength (``retardance_rows``, e.g. a dual-wavelength plate: half wave at 1064 nm and full wave
    at 532 nm), linear in wavelength between rows or at the rows' wavelengths only (``retardance_interpolation``).
    """

    INVERSE_WAVELENGTH = "INVERSE_WAVELENGTH"
    CONSTANT = "CONSTANT"
    TABULATED = "TABULATED"


#: The most retardance rows of a polarization element.
MAX_RETARDANCE_ROWS = 64


class RetardanceRowV1(StrictContractModel):
    """A retarder's retardance (in waves) at one vacuum wavelength."""

    wavelength_nm: OpticalDecimal
    retardance_waves: OpticalDecimal

    @model_validator(mode="after")
    def _positive_values(self) -> RetardanceRowV1:
        _positive(self.wavelength_nm, "wavelength_nm")
        _positive(self.retardance_waves, "retardance_waves")
        return self


class PolarizationBandV1(StrictContractModel):
    """A polarization element's transmittance (and, for a polarizer, extinction ratio) within one wavelength band
    ``[min_nm, max_nm]``."""

    min_nm: OpticalDecimal
    max_nm: OpticalDecimal
    transmittance: PublicValueV1
    extinction_ratio: PublicValueV1 | None = None
    status: FactStatus

    @model_validator(mode="after")
    def _consistent(self) -> PolarizationBandV1:
        if not _positive(self.min_nm, "min_nm") <= _positive(self.max_nm, "max_nm"):
            raise ValueError("min_nm cannot exceed max_nm")
        _fraction(self.transmittance.value, "transmittance")
        if self.extinction_ratio is not None and _positive(self.extinction_ratio.value, "extinction_ratio") < 1:
            raise ValueError("extinction_ratio must be at least 1")
        return self

    def contains(self, wavelength_nm: Decimal) -> bool:
        return Decimal(self.min_nm) <= wavelength_nm <= Decimal(self.max_nm)


class PolarizationElementModelV1(StrictContractModel):
    """A Jones-matrix element at the plane ``z = plane_z_mm`` of the element frame.

    Its axis is local +X (``axis``): the fast axis of a retarder, the transmission axis of a polarizer. Turning the
    element about local Z turns the axis (``symmetry = NONE``).

    * ``RETARDER``: retardance ``retardance_waves`` (in waves) at ``design_wavelength_nm``, scaled by
      ``retardance_dispersion``; power transmittance ``transmittance``;
    * ``LINEAR_POLARIZER``: power transmittance ``transmittance`` for light polarized along the axis and
      ``transmittance / extinction_ratio`` perpendicular to it;
    * ``CIRCULAR_POLARIZER``: that linear polarizer at ``plane_z_mm`` followed by a retarder (``retardance_waves`` at
      ``design_wavelength_nm``, a quarter wave for a circular polarizer, scaled by ``retardance_dispersion``) at
      ``plane_z_mm + retarder_offset_mm`` whose fast axis is ``retarder_axis_deg`` from +X toward +Y (+-45 for circular
      light). Light entering on the polarizer's side leaves circularly polarized; light returning through the retarder first
      meets the polarizer after it (an isolator against its own reflection). ``transmittance`` (and its bands) is the whole
      element's.

    ``retardance_dispersion = TABULATED`` states the retardance per wavelength in ``retardance_rows`` (strictly increasing
    wavelengths, one of them the design point ``design_wavelength_nm`` / ``retardance_waves``): ``LINEAR`` in wavelength
    between rows over their span, or ``POINTS_ONLY`` at the rows' wavelengths; light at any other wavelength is outside the
    model (nothing is extrapolated).

    ``position_parameter = axis_rotation_deg`` names the component parameter (``selected_parameters`` semantic key, degrees)
    that turns the element's axes about local Z from +X toward +Y (a rotation mount: the half-wave plate of a tunable
    splitter or attenuator); unset, the axes are as stated.

    Characterized for wavelengths in ``valid_band_nm`` and a chief-ray angle of incidence in ``valid_aoi_deg``.

    ``transmittance_bands`` optionally resolves the transmittance (and a polarizer's extinction ratio) by wavelength:
    sorted, contiguous bands (each band's ``max_nm`` is the next band's ``min_nm``) that tile ``valid_band_nm`` exactly.
    The scalar ``transmittance`` (and ``extinction_ratio``) is then the smallest band value: the guaranteed value over
    the whole valid band. A band states an extinction ratio only when the element states one, and then every band
    does. A wavelength on the boundary of two bands takes the smaller value.
    """

    model_kind: Literal["POLARIZATION_ELEMENT"] = "POLARIZATION_ELEMENT"
    model_version: Literal["1"] = "1"
    fidelity: ModelFidelity
    element: PolarizationElementKind
    plane_z_mm: OpticalDecimal
    axis: Literal["+X"] = "+X"
    retardance_waves: OpticalDecimal | None = None
    design_wavelength_nm: OpticalDecimal | None = None
    retardance_dispersion: RetardanceDispersion | None = None
    transmittance: PublicValueV1 = Field(None, description="Power transmittance; may be omitted with transmittance_bands (it is "
                                         "then the smallest band transmittance).")  # type: ignore[assignment]
    extinction_ratio: PublicValueV1 | None = None
    clear_radius_mm: OpticalDecimal
    edge_radius_mm: OpticalDecimal
    valid_band_nm: WavelengthBandV1
    valid_aoi_deg: AngleRangeV1
    transmittance_bands: tuple[PolarizationBandV1, ...] | None = Field(None, min_length=1, max_length=MAX_POLARIZATION_BANDS)
    approximations: tuple[SimulationApproximation, ...] = Field(min_length=1)
    status: FactStatus
    retardance_rows: tuple[RetardanceRowV1, ...] | None = Field(None, min_length=1, max_length=MAX_RETARDANCE_ROWS)
    retardance_interpolation: Literal["LINEAR", "POINTS_ONLY"] | None = None
    retarder_axis_deg: OpticalDecimal | None = None
    retarder_offset_mm: OpticalDecimal | None = None
    position_parameter: Literal["axis_rotation_deg"] | None = None

    @field_validator("approximations", mode="before")
    @classmethod
    def _sort_approximations(cls, value: object) -> object:
        return _sorted_or_unchanged(value, str)

    def _check_retardance_rows(self) -> None:
        tabulated = self.retardance_dispersion is RetardanceDispersion.TABULATED
        if (self.retardance_rows is not None or self.retardance_interpolation is not None) and not tabulated:
            raise ValueError("retardance_rows and retardance_interpolation belong to a TABULATED retardance_dispersion")
        if not tabulated:
            return
        rows = self.retardance_rows
        if rows is None or self.retardance_interpolation is None:
            raise ValueError("a TABULATED retardance_dispersion states retardance_rows and retardance_interpolation")
        wavelengths = [Decimal(row.wavelength_nm) for row in rows]
        if any(b <= a for a, b in zip(wavelengths, wavelengths[1:])):
            raise ValueError("retardance_rows have strictly increasing wavelengths")
        if any(not self.valid_band_nm.contains(w) for w in wavelengths):
            raise ValueError("every retardance row lies inside the valid band")
        design = (Decimal(self.design_wavelength_nm), Decimal(self.retardance_waves))  # type: ignore[arg-type]
        if design not in {(Decimal(r.wavelength_nm), Decimal(r.retardance_waves)) for r in rows}:
            raise ValueError("the design point (design_wavelength_nm, retardance_waves) is one of the retardance_rows")

    @field_validator("transmittance_bands", mode="before")
    @classmethod
    def _sort_bands(cls, value: object) -> object:
        return _sorted_or_unchanged(value, lambda item: Decimal(_raw(item, "min_nm")))

    @model_validator(mode="before")
    @classmethod
    def _band_minimum_filled(cls, data: Any) -> Any:
        """An element document with ``transmittance_bands`` that omits its scalar ``transmittance`` (or, when every band
        states one, its ``extinction_ratio``), before validation: filled with the band entry of smallest value, which is
        what the scalar is. A stated scalar is kept (the after-validator compares it)."""

        if not isinstance(data, dict) or not isinstance(data.get("transmittance_bands"), (list, tuple)) or not data["transmittance_bands"]:
            return data
        bands = data["transmittance_bands"]
        out = data
        for name in ("transmittance", "extinction_ratio"):
            if data.get(name) is not None:
                continue
            entries = [band.get(name) if isinstance(band, dict) else None for band in bands]
            values = [read_decimal(e.get("value")) if isinstance(e, dict) else None for e in entries]
            if any(v is None for v in values):
                continue
            smallest = min(range(len(values)), key=lambda i: values[i])  # type: ignore[arg-type,return-value]
            out = {**out, name: dict(entries[smallest])}  # type: ignore[arg-type]
        return out

    def _check_bands(self) -> None:
        bands = self.transmittance_bands
        if bands is None:
            return
        low, high = Decimal(self.valid_band_nm.min_nm), Decimal(self.valid_band_nm.max_nm)
        if Decimal(bands[0].min_nm) != low or Decimal(bands[-1].max_nm) != high:
            raise ValueError("transmittance_bands tile valid_band_nm exactly (the first band starts at its min_nm, the last ends at "
                             "its max_nm)")
        for a, b in zip(bands, bands[1:]):
            if Decimal(a.max_nm) != Decimal(b.min_nm):
                raise ValueError(f"transmittance_bands are contiguous: a band ending at {a.max_nm} nm is followed by one starting there, "
                                 f"not at {b.min_nm} nm")
        if len(bands) > 1 and any(Decimal(band.min_nm) == Decimal(band.max_nm) for band in bands):
            raise ValueError("each of several transmittance_bands spans an interval")
        if Decimal(self.transmittance.value) != min(Decimal(band.transmittance.value) for band in bands):
            raise ValueError("transmittance is the smallest band transmittance of transmittance_bands")
        ratios = [band.extinction_ratio for band in bands]
        if any(ratio is not None for ratio in ratios):
            if self.extinction_ratio is None:
                raise ValueError("a band states an extinction_ratio only when the element states one")
            if any(ratio is None for ratio in ratios):
                raise ValueError("when one band states an extinction_ratio every band does")
            if Decimal(self.extinction_ratio.value) != min(Decimal(ratio.value) for ratio in ratios):  # type: ignore[union-attr]
                raise ValueError("extinction_ratio is the smallest band extinction_ratio of transmittance_bands")

    def band_at(self, wavelength_nm: Decimal) -> PolarizationBandV1 | None:
        """The transmittance band holding ``wavelength_nm`` (on a boundary, the one with the smaller transmittance), or
        None without bands or outside them."""

        holding = [band for band in self.transmittance_bands or () if band.contains(wavelength_nm)]
        return min(holding, key=lambda band: Decimal(band.transmittance.value)) if holding else None

    @model_validator(mode="after")
    def _consistent(self) -> PolarizationElementModelV1:
        _fraction(self.transmittance.value, "transmittance")
        if not _positive(self.clear_radius_mm, "clear_radius_mm") <= _positive(self.edge_radius_mm, "edge_radius_mm"):
            raise ValueError("clear_radius_mm cannot exceed edge_radius_mm")
        retarder_fields = (self.retardance_waves, self.design_wavelength_nm, self.retardance_dispersion)
        placement = (self.retarder_axis_deg, self.retarder_offset_mm)
        circular = self.element is PolarizationElementKind.CIRCULAR_POLARIZER
        if self.element is PolarizationElementKind.RETARDER or circular:
            if any(f is None for f in retarder_fields):
                raise ValueError(f"a {self.element.value} has retardance_waves, design_wavelength_nm and retardance_dispersion")
            _positive(self.retardance_waves, "retardance_waves")  # type: ignore[arg-type]
            if not self.valid_band_nm.contains(_positive(self.design_wavelength_nm, "design_wavelength_nm")):  # type: ignore[arg-type]
                raise ValueError("the design wavelength must lie inside the valid band")
        elif any(f is not None for f in retarder_fields):
            raise ValueError("a LINEAR_POLARIZER has no retarder fields")
        if self.element is PolarizationElementKind.RETARDER:
            if self.extinction_ratio is not None:
                raise ValueError("a RETARDER has no extinction_ratio")
        else:
            if self.extinction_ratio is None:
                raise ValueError(f"a {self.element.value} has an extinction_ratio")
            if _positive(self.extinction_ratio.value, "extinction_ratio") < 1:
                raise ValueError("extinction_ratio must be at least 1")
        if circular:
            if any(f is None for f in placement):
                raise ValueError("a CIRCULAR_POLARIZER states retarder_axis_deg and retarder_offset_mm (where its retarder lies)")
            if Decimal(self.retarder_offset_mm) == 0:  # type: ignore[arg-type]
                raise ValueError("retarder_offset_mm is not 0: the retarder lies behind the polarizer, on one side of it")
            if abs(Decimal(self.retarder_axis_deg)) > 180:  # type: ignore[arg-type]
                raise ValueError("retarder_axis_deg lies in [-180, 180]")
        elif any(f is not None for f in placement):
            raise ValueError("retarder_axis_deg and retarder_offset_mm belong to a CIRCULAR_POLARIZER")
        self._check_retardance_rows()
        if len(set(self.approximations)) != len(self.approximations):
            raise ValueError("duplicate approximation")
        self._check_bands()
        return self

    @property
    def is_rotationally_symmetric(self) -> bool:
        """A Jones element has an in-plane axis."""

        return False

    def clear_aperture_extents_mm(self) -> tuple[Decimal, Decimal]:
        diameter = Decimal(self.clear_radius_mm) * 2
        return diameter, diameter


# ------------------------------------------------------------------------------------- NEUTRAL_DENSITY model


def _density(value: str, label: str) -> Decimal:
    number = Decimal(value)
    if number < 0:
        raise ValueError(f"{label} is an optical density of at least 0, got {value}")
    return number


def _arc(start: str, end: str, label: str) -> None:
    if not _ZERO <= Decimal(start) < Decimal(end) <= Decimal(360):
        raise ValueError(f"{label}: an arc satisfies 0 <= start < end <= 360 degrees, got {start} to {end}")


class FixedDensityV1(StrictContractModel):
    """One optical density ``optical_density`` over the whole clear aperture: power transmittance ``10^-OD``."""

    kind: Literal["FIXED"] = "FIXED"
    optical_density: OpticalDecimal

    @model_validator(mode="after")
    def _consistent(self) -> FixedDensityV1:
        _density(self.optical_density, "optical_density")
        return self

    def density_range(self) -> tuple[Decimal, Decimal]:
        value = Decimal(self.optical_density)
        return value, value


class LinearRotaryDensityV1(StrictContractModel):
    """A continuously variable filter wheel: the optical density varies linearly with the azimuth of the hit point
    about the element axis, from ``od_start`` at ``arc_start_deg`` to ``od_end`` at ``arc_end_deg`` (azimuth measured
    from local +X toward local +Y, after the wheel is turned by its position parameter). ``linearity_percent`` is the
    manufacturer's stated deviation from the linear law, when stated. Outside the coated arc the uncoated transmittance
    applies."""

    kind: Literal["LINEAR_ROTARY"] = "LINEAR_ROTARY"
    od_start: OpticalDecimal
    od_end: OpticalDecimal
    arc_start_deg: OpticalDecimal
    arc_end_deg: OpticalDecimal
    linearity_percent: OpticalDecimal | None = None

    @model_validator(mode="after")
    def _consistent(self) -> LinearRotaryDensityV1:
        _density(self.od_start, "od_start")
        _density(self.od_end, "od_end")
        _arc(self.arc_start_deg, self.arc_end_deg, "the coated arc")
        if self.linearity_percent is not None and Decimal(self.linearity_percent) < 0:
            raise ValueError("linearity_percent cannot be negative")
        return self

    def density_range(self) -> tuple[Decimal, Decimal]:
        values = (Decimal(self.od_start), Decimal(self.od_end))
        return min(values), max(values)


class DensityStepV1(StrictContractModel):
    """One step of a stepped filter wheel: optical density ``optical_density`` over the azimuths ``[start_deg,
    end_deg]``."""

    start_deg: OpticalDecimal
    end_deg: OpticalDecimal
    optical_density: OpticalDecimal

    @model_validator(mode="after")
    def _consistent(self) -> DensityStepV1:
        _arc(self.start_deg, self.end_deg, "a density step")
        _density(self.optical_density, "optical_density")
        return self


class SteppedRotaryDensityV1(StrictContractModel):
    """A stepped filter wheel: sectors of constant optical density at stated azimuths of the hit point about the
    element axis (measured from local +X toward local +Y, after the wheel is turned by its position parameter). Steps
    are sorted by ``start_deg`` and do not overlap (they may touch); between steps the uncoated transmittance applies."""

    kind: Literal["STEPPED_ROTARY"] = "STEPPED_ROTARY"
    steps: tuple[DensityStepV1, ...] = Field(min_length=1, max_length=MAX_DENSITY_STEPS)

    @field_validator("steps", mode="before")
    @classmethod
    def _sort_steps(cls, value: object) -> object:
        return _sorted_or_unchanged(value, lambda item: Decimal(_raw(item, "start_deg")))

    @model_validator(mode="after")
    def _consistent(self) -> SteppedRotaryDensityV1:
        for a, b in zip(self.steps, self.steps[1:]):
            if Decimal(b.start_deg) < Decimal(a.end_deg):
                raise ValueError(f"density steps do not overlap: a step ends at {a.end_deg} deg after the next starts at {b.start_deg} deg")
        return self

    def density_range(self) -> tuple[Decimal, Decimal]:
        values = [Decimal(step.optical_density) for step in self.steps]
        return min(values), max(values)


class LinearGradientDensityV1(StrictContractModel):
    """A linear gradient filter: the optical density varies linearly along local X, from ``od_start`` at
    ``x_start_mm`` to ``od_end`` at ``x_end_mm`` (the hit point's local X after the filter is shifted by its position
    parameter). Outside ``[x_start_mm, x_end_mm]`` the uncoated transmittance applies."""

    kind: Literal["LINEAR_GRADIENT"] = "LINEAR_GRADIENT"
    od_start: OpticalDecimal
    od_end: OpticalDecimal
    x_start_mm: OpticalDecimal
    x_end_mm: OpticalDecimal

    @model_validator(mode="after")
    def _consistent(self) -> LinearGradientDensityV1:
        _density(self.od_start, "od_start")
        _density(self.od_end, "od_end")
        if not Decimal(self.x_start_mm) < Decimal(self.x_end_mm):
            raise ValueError("x_start_mm must be smaller than x_end_mm")
        return self

    def density_range(self) -> tuple[Decimal, Decimal]:
        values = (Decimal(self.od_start), Decimal(self.od_end))
        return min(values), max(values)


DensityLawV1 = Annotated[
    FixedDensityV1 | LinearRotaryDensityV1 | SteppedRotaryDensityV1 | LinearGradientDensityV1, Field(discriminator="kind")
]
_ROTARY_LAWS = (LinearRotaryDensityV1, SteppedRotaryDensityV1)


class NeutralDensityModelV1(StrictContractModel):
    """An attenuator at the plane ``z = plane_z_mm`` (0, the frame origin) of the element frame, axis local +Z.

    ``law`` gives the optical density ``OD`` at each point where light crosses the plane, so for a position-dependent
    law the attenuation varies across a beam's footprint: power transmittance ``10^-OD``. ``FIXED``: one density;
    ``LINEAR_ROTARY`` / ``STEPPED_ROTARY``: a filter wheel whose density depends on the azimuth of the hit point about
    the element axis; ``LINEAR_GRADIENT``: a density linear along local X. Where the law leaves the plane uncoated
    (outside the coated arc, between steps, beyond the gradient) the power transmittance is
    ``uncoated_transmittance``; without it, light reaching an uncoated part is outside the model's scope.

    ``coated_inner_radius_mm`` (a position-dependent law in a round clear aperture): the coating is an annulus between
    this radius and the clear radius, as on a filter wheel coated around a central hub; light crossing the plane closer
    to the element axis meets the uncoated plane (``uncoated_transmittance``, or outside the model's scope without it).

    ``reflectance`` is the power fraction the element reflects, the same everywhere (absent: 0); light that is neither
    transmitted nor reflected is absorbed. ``position_parameter`` names the component parameter (``selected_parameters``
    semantic key) that turns a wheel about the element axis (``nd_rotation_deg``, degrees from local +X toward local +Y)
    or shifts a gradient along local X (``nd_offset_mm``); unset, the position is 0.

    The clear aperture is exactly one of ``clear_radius_mm`` and ``clear_half_widths_mm`` (local X / Y), the outline
    exactly one of ``edge_radius_mm`` and ``edge_half_widths_mm``; light between them is blocked and light beyond the
    outline passes by. Characterized for vacuum wavelengths in ``valid_band_nm``.
    """

    model_kind: Literal["NEUTRAL_DENSITY"] = "NEUTRAL_DENSITY"
    model_version: Literal["1"] = "1"
    fidelity: ModelFidelity
    law: DensityLawV1
    plane_z_mm: OpticalDecimal = "0"
    uncoated_transmittance: OpticalDecimal | None = None
    reflectance: OpticalDecimal | None = None
    clear_radius_mm: OpticalDecimal | None = None
    clear_half_widths_mm: DecimalPair | None = None
    coated_inner_radius_mm: OpticalDecimal | None = None
    edge_radius_mm: OpticalDecimal | None = None
    edge_half_widths_mm: DecimalPair | None = None
    valid_band_nm: WavelengthBandV1
    position_parameter: Literal["nd_rotation_deg", "nd_offset_mm"] | None = None
    approximations: tuple[SimulationApproximation, ...] = Field(min_length=1)
    status: FactStatus

    @field_validator("approximations", mode="before")
    @classmethod
    def _sort_approximations(cls, value: object) -> object:
        return _sorted_or_unchanged(value, str)

    @model_validator(mode="after")
    def _consistent(self) -> NeutralDensityModelV1:
        if Decimal(self.plane_z_mm) != 0:
            raise ValueError("a neutral-density element lies in its frame's z = 0 plane: plane_z_mm is 0 (place the element at the plane)")
        if (self.clear_radius_mm is None) == (self.clear_half_widths_mm is None):
            raise ValueError("a neutral-density element has exactly one of clear_radius_mm and clear_half_widths_mm")
        if (self.edge_radius_mm is None) == (self.edge_half_widths_mm is None):
            raise ValueError("a neutral-density element has exactly one of edge_radius_mm and edge_half_widths_mm")
        with localcontext(DECIMAL_CONTEXT):
            if self.clear_radius_mm is not None:
                radius = _positive(self.clear_radius_mm, "clear_radius_mm")
                clear = (radius, radius)
            else:
                clear = tuple(_positive(v, "clear_half_widths_mm") for v in self.clear_half_widths_mm)  # type: ignore[union-attr]
            if self.edge_radius_mm is not None:
                edge = _positive(self.edge_radius_mm, "edge_radius_mm")
                fits = (clear[0] <= edge if self.clear_radius_mm is not None
                        else clear[0] * clear[0] + clear[1] * clear[1] <= edge * edge)
            else:
                ex, ey = (_positive(v, "edge_half_widths_mm") for v in self.edge_half_widths_mm)  # type: ignore[union-attr]
                fits = clear[0] <= ex and clear[1] <= ey
            if not fits:
                raise ValueError("the clear aperture must lie inside the outline (edge_radius_mm / edge_half_widths_mm)")
            if self.coated_inner_radius_mm is not None:
                if isinstance(self.law, FixedDensityV1) or self.clear_radius_mm is None:
                    raise ValueError("coated_inner_radius_mm bounds the coated annulus of a position-dependent law in a round clear aperture "
                                     "(clear_radius_mm)")
                if not _positive(self.coated_inner_radius_mm, "coated_inner_radius_mm") < clear[0]:
                    raise ValueError("coated_inner_radius_mm must be smaller than clear_radius_mm")
            reflectance = _fraction(self.reflectance, "reflectance") if self.reflectance is not None else _ZERO
            lowest_density = self.law.density_range()[0]
            highest_transmittance = Decimal(10) ** -lowest_density
            if reflectance + highest_transmittance > 1:
                raise ValueError(f"reflectance + transmittance cannot exceed 1: at the lowest optical density {lowest_density} the "
                                 f"transmittance is {highest_transmittance:.6g}")
            if self.uncoated_transmittance is not None:
                if isinstance(self.law, FixedDensityV1):
                    raise ValueError("a FIXED density covers the whole clear aperture: omit uncoated_transmittance")
                if reflectance + _fraction(self.uncoated_transmittance, "uncoated_transmittance") > 1:
                    raise ValueError("reflectance + uncoated_transmittance cannot exceed 1")
        if self.position_parameter == "nd_rotation_deg" and not isinstance(self.law, _ROTARY_LAWS):
            raise ValueError("nd_rotation_deg turns a filter wheel: it needs a LINEAR_ROTARY or STEPPED_ROTARY law")
        if self.position_parameter == "nd_offset_mm" and not isinstance(self.law, LinearGradientDensityV1):
            raise ValueError("nd_offset_mm shifts a gradient filter: it needs a LINEAR_GRADIENT law")
        if len(set(self.approximations)) != len(self.approximations):
            raise ValueError("duplicate approximation")
        return self

    @property
    def is_position_dependent(self) -> bool:
        """Whether the optical density depends on where light crosses the element (every law but ``FIXED``)."""

        return not isinstance(self.law, FixedDensityV1)

    @property
    def is_rotationally_symmetric(self) -> bool:
        """A fixed density in a round aperture and outline."""

        return isinstance(self.law, FixedDensityV1) and self.clear_radius_mm is not None and self.edge_radius_mm is not None

    def clear_aperture_extents_mm(self) -> tuple[Decimal, Decimal]:
        if self.clear_radius_mm is not None:
            diameter = Decimal(self.clear_radius_mm) * 2
            return diameter, diameter
        return Decimal(self.clear_half_widths_mm[0]) * 2, Decimal(self.clear_half_widths_mm[1]) * 2  # type: ignore[index]

    def outline_extents_mm(self) -> tuple[Decimal, Decimal]:
        if self.edge_radius_mm is not None:
            diameter = Decimal(self.edge_radius_mm) * 2
            return diameter, diameter
        return Decimal(self.edge_half_widths_mm[0]) * 2, Decimal(self.edge_half_widths_mm[1]) * 2  # type: ignore[index]

    def material_ids(self) -> tuple[str, ...]:
        return ()

    def coating_ids(self) -> tuple[str, ...]:
        return ()


SimulationModelV2 = Annotated[
    SurfaceSequenceModelV1 | SurfaceSetModelV1 | PolarizationElementModelV1 | NeutralDensityModelV1 | JoinedBodiesModelV1,
    Field(discriminator="model_kind"),
]
#: Single-plane catalog model kinds: no surfaces, media or coatings.
THIN_ELEMENT_MODELS = (PolarizationElementModelV1, NeutralDensityModelV1)


def model_schema_3_3_features(model: object) -> tuple[str, ...]:
    """The catalog-schema-3.3 fields a simulation model uses: a surface-sequence surface's own mechanical edge."""

    if isinstance(model, SurfaceSequenceModelV1):
        return tuple(feature for surface in model.surfaces for feature in surface.schema_3_3_features())
    return ()


def model_schema_3_4_features(model: object) -> tuple[str, ...]:
    """The catalog-schema-3.4 fields a simulation model uses: a surface's two-variable sag shape."""

    if isinstance(model, (SurfaceSequenceModelV1, SurfaceSetModelV1)):
        return tuple(feature for surface in model.surfaces for feature in surface.schema_3_4_features())
    if isinstance(model, JoinedBodiesModelV1):
        return tuple(feature for surface in model.member_surfaces() for feature in surface.schema_3_4_features())
    return ()


def model_schema_3_5_features(model: object) -> tuple[str, ...]:
    """The catalog-schema-3.5 fields a simulation model uses: the ``JOINED_BODIES`` model kind (and with it the crystal
    materials and optic axes only it uses)."""

    return ("JOINED_BODIES model",) if isinstance(model, JoinedBodiesModelV1) else ()


def model_schema_3_1_features(model: object) -> tuple[str, ...]:
    """The catalog-schema-3.1 fields a simulation model uses (empty for a 3.0 model)."""

    if isinstance(model, NeutralDensityModelV1):
        return ("NEUTRAL_DENSITY model",)
    if isinstance(model, PolarizationElementModelV1):
        return ("POLARIZATION_ELEMENT transmittance_bands",) if model.transmittance_bands is not None else ()
    if isinstance(model, (SurfaceSequenceModelV1, SurfaceSetModelV1)):
        return tuple(feature for surface in model.surfaces for feature in surface.schema_3_1_features())
    if isinstance(model, JoinedBodiesModelV1):
        return tuple(feature for surface in model.member_surfaces() for feature in surface.schema_3_1_features())
    return ()


# ----------------------------------------------------------------------------------------------- product


class FramePortV1(StrictContractModel):
    """A named entry or exit of an element placed by its ports (element frame).

    ``IN`` (``role = INPUT``): the nominal input beam travels along ``direction`` through ``point_mm``. ``OUT`` /
    ``OUT_<NAME>`` (``role = OUTPUT``): light of the nominal input leaves from ``point_mm`` along ``direction`` (e.g.
    ``OUT_T`` the transmitted and ``OUT_R`` the reflected output of a cube splitter). ``status`` says how the port is
    established (a deviated output computed at the design wavelength is ``DERIVED``).
    """

    port_id: PortId
    role: Literal["INPUT", "OUTPUT"]
    point_mm: DecimalTriple
    direction: DecimalTriple
    status: FactStatus

    @model_validator(mode="after")
    def _consistent(self) -> FramePortV1:
        if (self.role == "INPUT") != (self.port_id == "IN"):
            raise ValueError(f"port {self.port_id}: IN is the one INPUT port; every OUT port is an OUTPUT")
        _unit(self.direction, f"port {self.port_id} direction")
        return self


class OpticalFrameV1(StrictContractModel):
    """The element's local frame.

    * A transmissive element (``reference_axis`` absent): local +Z is the optical axis and the nominal propagation
      direction; the origin is the first surface vertex (``FIRST_SURFACE_VERTEX``).
    * A reflector or plate splitter (``reference_axis = FACE_NORMAL``): local +Z is the outward normal of the coated
      face, pointing toward the side light arrives from; light arrives travelling toward local -Z and leaves reflected
      about local +Z (``REFLECTED_ABOUT_+Z``), a splitter also transmitting through its plate (``SPLIT_ABOUT_+Z``); the
      origin is the centre of the coated face (``REFLECTING_FACE_CENTER``).
    * An element placed by its ports (``reference_axis = PORTS``, ``nominal_propagation = PORTS``): ``ports`` give the
      nominal input and the outputs; the origin is the body's centroid (``BODY_CENTER``: a cube splitter, a prism) or the
      centre of a decentred surface's clear aperture (``APERTURE_CENTER``: an off-axis mirror section).

    Conventions of ports elements (element frame; ``a`` the cube edge): a cube splitter has ``IN`` at ``(0, 0, -a/2)``
    along +Z, ``OUT_T`` at ``(0, 0, a/2)`` along +Z and ``OUT_R`` at ``(a/2, 0, 0)`` along +X, its coated diagonal
    through the origin with normal ``(1, 0, -1)/sqrt(2)``; a polarizing cube transmits p (plane of incidence = local XZ)
    to ``OUT_T`` and reflects s to ``OUT_R``. A fold prism (right-angle, penta) takes ``IN`` along +Z and sends ``OUT``
    along +X; a dove prism sends ``OUT`` along +Z; a corner cube along -Z. An off-axis parabolic mirror takes collimated
    light along its parent axis travelling toward -Z and sends ``OUT`` from the origin toward the parent focus.
    """

    origin: FrameOrigin
    optical_axis: Literal["+Z"] = "+Z"
    nominal_propagation: Literal["+Z", "REFLECTED_ABOUT_+Z", "SPLIT_ABOUT_+Z", "PORTS"] = "+Z"
    reference_axis: FrameReferenceAxis | None = Field(
        None,
        description="FACE_NORMAL for a reflector / plate splitter (local +Z = outward face normal); PORTS for an element placed "
        "by its ports; absent for a transmissive element.",
    )
    symmetry: OpticalSymmetry
    ports: tuple[FramePortV1, ...] | None = Field(None, min_length=2, max_length=MAX_PORTS)

    @field_validator("ports", mode="before")
    @classmethod
    def _sort_ports(cls, value: object) -> object:
        return _sorted_or_unchanged(value, lambda item: str(_raw(item, "port_id")))

    @model_validator(mode="after")
    def _consistent(self) -> OpticalFrameV1:
        face = self.reference_axis is FrameReferenceAxis.FACE_NORMAL
        ported = self.reference_axis is FrameReferenceAxis.PORTS
        if ported:
            if self.nominal_propagation != "PORTS" or self.origin not in (FrameOrigin.BODY_CENTER, FrameOrigin.APERTURE_CENTER):
                raise ValueError("a ports frame has nominal_propagation PORTS and origin BODY_CENTER or APERTURE_CENTER")
            ids = [port.port_id for port in self.ports or ()]
            if len(set(ids)) != len(ids):
                raise ValueError("duplicate port_id")
            if "IN" not in ids or len(ids) < 2:
                raise ValueError("a ports frame has the IN port and at least one OUT port")
            if self.symmetry is not OpticalSymmetry.NONE:
                raise ValueError("an element placed by its ports has symmetry NONE")
            return self
        if self.ports is not None:
            raise ValueError("only a ports frame (reference_axis PORTS) has ports")
        if (self.nominal_propagation not in ("REFLECTED_ABOUT_+Z", "SPLIT_ABOUT_+Z")) == face or (
            self.origin is FrameOrigin.REFLECTING_FACE_CENTER
        ) != face:
            raise ValueError(
                "a face-normal frame has reference_axis FACE_NORMAL, a reflected / split nominal propagation and origin "
                "REFLECTING_FACE_CENTER together; a transmissive frame has none of them"
            )
        if not face and self.origin is not FrameOrigin.FIRST_SURFACE_VERTEX:
            raise ValueError("a transmissive frame's origin is FIRST_SURFACE_VERTEX")
        return self

    @property
    def is_reflector(self) -> bool:
        return self.reference_axis is FrameReferenceAxis.FACE_NORMAL

    @property
    def has_ports(self) -> bool:
        return self.reference_axis is FrameReferenceAxis.PORTS

    def port(self, port_id: str) -> FramePortV1 | None:
        return next((port for port in self.ports or () if port.port_id == port_id), None)


class CylinderGeometryV1(StrictContractModel):
    """Design geometry: the bounding cylinder about local Z from ``z_min_mm`` to ``z_max_mm`` (diameter
    ``diameter_mm``), used by the optical 3D view and for design-time clearance.
    """

    kind: Literal["CYLINDER"] = "CYLINDER"
    diameter_mm: OpticalDecimal
    z_min_mm: OpticalDecimal
    z_max_mm: OpticalDecimal
    status: FactStatus

    @model_validator(mode="after")
    def _ordered(self) -> CylinderGeometryV1:
        _positive(self.diameter_mm, "diameter_mm")
        if not Decimal(self.z_min_mm) < Decimal(self.z_max_mm):
            raise ValueError("z_min_mm must be smaller than z_max_mm")
        return self

    def extents_mm(self) -> Vec:
        """Full widths of the bounding box along local X, Y, Z."""

        diameter = Decimal(self.diameter_mm)
        return diameter, diameter, Decimal(self.z_max_mm) - Decimal(self.z_min_mm)

    def contains(self, point: Vec, tolerance: Decimal = GEOMETRY_TOLERANCE_MM) -> bool:
        with localcontext(DECIMAL_CONTEXT):
            reach = Decimal(self.diameter_mm) / 2 + tolerance
            return (point[0] * point[0] + point[1] * point[1] <= reach * reach
                    and Decimal(self.z_min_mm) - tolerance <= point[2] <= Decimal(self.z_max_mm) + tolerance)


class BoxGeometryV1(StrictContractModel):
    """Design geometry: the box centred at ``center_mm`` with half extents ``half_extents_mm`` along local X, Y, Z."""

    kind: Literal["BOX"] = "BOX"
    center_mm: DecimalTriple
    half_extents_mm: DecimalTriple
    status: FactStatus

    @model_validator(mode="after")
    def _positive_extents(self) -> BoxGeometryV1:
        for value in self.half_extents_mm:
            _positive(value, "half_extents_mm")
        return self

    def extents_mm(self) -> Vec:
        return _scale(_vec(self.half_extents_mm), _TWO)

    def contains(self, point: Vec, tolerance: Decimal = GEOMETRY_TOLERANCE_MM) -> bool:
        with localcontext(DECIMAL_CONTEXT):
            offset = _sub(point, _vec(self.center_mm))
            return all(abs(o) <= h + tolerance for o, h in zip(offset, _vec(self.half_extents_mm)))


class ConvexPolyhedronGeometryV1(StrictContractModel):
    """Design geometry: the convex hull of ``vertices_mm`` (4 to 32 corners, each an extreme corner of the hull, in
    ascending (x, y, z) order) -- for example a prism's outline."""

    kind: Literal["CONVEX_POLYHEDRON"] = "CONVEX_POLYHEDRON"
    vertices_mm: tuple[DecimalTriple, ...] = Field(min_length=MIN_POLYHEDRON_VERTICES, max_length=MAX_POLYHEDRON_VERTICES)
    status: FactStatus

    _hull: tuple[tuple[Vec, Decimal], ...] = PrivateAttr(default=())

    @field_validator("vertices_mm", mode="before")
    @classmethod
    def _sort_vertices(cls, value: object) -> object:
        return _sorted_or_unchanged(value, lambda item: tuple(Decimal(v) for v in item))

    @model_validator(mode="after")
    def _hull_of_extreme_corners(self) -> ConvexPolyhedronGeometryV1:
        with localcontext(DECIMAL_CONTEXT):
            points = [_vec(v) for v in self.vertices_mm]
            for a, b in _combinations(points, 2):
                if _close(a, b, _VERTEX_TOLERANCE_MM):
                    raise ValueError("duplicate vertex")
            planes: list[tuple[Vec, Decimal]] = []
            for a, b, c in _combinations(points, 3):
                normal = _cross(_sub(b, a), _sub(c, a))
                length = _norm(normal)
                if length <= _SINGULAR:
                    continue
                unit = _scale(normal, _ONE / length)
                offset = _dot(unit, a)
                sides = [_dot(unit, p) - offset for p in points]
                for sign in (_ONE, -_ONE):
                    if all(s * sign <= _VERTEX_TOLERANCE_MM for s in sides) and any(s * sign < -_VERTEX_TOLERANCE_MM for s in sides):
                        plane = (_scale(unit, sign), offset * sign)
                        if not any(_close(plane[0], q[0], _VERTEX_TOLERANCE_MM) and abs(plane[1] - q[1]) <= _VERTEX_TOLERANCE_MM
                                   for q in planes):
                            planes.append(plane)
            if len(planes) < MIN_POLYHEDRON_VERTICES:
                raise ValueError("the vertices do not span a solid")
            for point in points:
                if sum(1 for n, d in planes if abs(_dot(n, point) - d) <= _VERTEX_TOLERANCE_MM) < 3:
                    raise ValueError("every vertex is a corner of the hull (no vertex inside it or on a face or edge)")
            self._hull = tuple(planes)
        return self

    def extents_mm(self) -> Vec:
        points = [_vec(v) for v in self.vertices_mm]
        return tuple(max(p[k] for p in points) - min(p[k] for p in points) for k in range(3))  # type: ignore[return-value]

    def contains(self, point: Vec, tolerance: Decimal = GEOMETRY_TOLERANCE_MM) -> bool:
        with localcontext(DECIMAL_CONTEXT):
            return all(_dot(n, point) - d <= tolerance for n, d in self._hull)


DesignGeometryV1 = Annotated[CylinderGeometryV1 | BoxGeometryV1 | ConvexPolyhedronGeometryV1, Field(discriminator="kind")]


class PowerRatingV1(StrictContractModel):
    """A power-handling rating (laser-induced damage threshold) and the conditions it was established under.

    ``regime = CW``: a continuous-wave rating, ``value`` in ``W/cm`` (power per beam diameter, the linear power density
    of the long-pulse / CW regime) or ``W/cm2`` (irradiance); no pulse fields. ``regime = PULSED``: a pulsed rating,
    ``value`` a fluence in ``J/cm2`` for pulses of ``pulse_duration_s`` (at ``repetition_rate_hz`` when stated).
    ``pulse_count`` is the number of pulses the rating states (1 for a single-pulse rating, read as one pulse on each
    tested site); ``test_protocol`` how they were applied, when the rating states it: ``S_ON_1``, each tested site received
    ``pulse_count`` pulses at one fluence level (ISO 21254-2 S-on-1). ``value_definition`` (``J/cm2`` and ``W/cm2`` only)
    says what the value is: ``PEAK``, the peak fluence or irradiance of the test beam, or ``TOTAL_PER_1E2_AREA``, the
    test pulse's total energy (or the beam's total power) divided by the area of the test beam's 1/e^2 diameter,
    pi (d / 2)^2 -- for a Gaussian (TEM00) test beam half its peak (not the mean within that diameter, which is 0.865 of
    the ratio); absent when the rating does not state it, and the value then establishes no damage threshold for a peak
    exposure. ``wavelength_nm`` is the test wavelength;
    ``beam_diameter_mm`` the diameter of the test beam on the sample and ``beam_diameter_definition`` (``1/E2``,
    ``FWHM`` or ``NOT_STATED``) how that diameter is measured; ``polarization_condition`` the stated test polarization.
    ``basis`` says where the rating comes from (a manufacturer's specification, a test report, the customer's own
    statement).
    """

    regime: Literal["CW", "PULSED"] = Field(description="CW (a continuous-wave rating) or PULSED (a rating for pulses).")
    value: OpticalDecimal = Field(description="The rated damage threshold, in unit.")
    unit: Literal["W/cm", "W/cm2", "J/cm2"] = Field(
        description="W/cm (power per beam diameter) or W/cm2 (irradiance) for a CW rating; J/cm2 (fluence) for a pulsed one.")
    wavelength_nm: OpticalDecimal = Field(description="Test wavelength in nm.")
    pulse_duration_s: OpticalDecimal | None = Field(
        None, description="Test pulse duration in s (pulsed ratings; compared with the source's FWHM pulse duration).")
    repetition_rate_hz: OpticalDecimal | None = Field(None, description="Test repetition rate in Hz, when the rating states one.")
    beam_diameter_mm: OpticalDecimal | None = Field(
        None, description="Diameter of the test beam on the sample in mm, when the rating states one.")
    beam_diameter_definition: Literal["1/E2", "FWHM", "NOT_STATED"] | None = Field(
        None, description="How beam_diameter_mm is measured: 1/E2, FWHM or NOT_STATED.")
    pulse_count: int | None = Field(
        None, ge=1, le=2**53 - 1, strict=True,
        description="The number of pulses the rating states (1: a single-pulse rating, read as one pulse on each tested site). A "
        "count above 1 is compared with the pulses a site of the design's surface receives only when test_protocol says how "
        "the pulses were applied.")
    test_protocol: Literal["S_ON_1"] | None = Field(
        None, description="How the pulse_count pulses were applied, when the rating states it: S_ON_1, each tested site "
        "received pulse_count pulses at one fluence level (ISO 21254-2 S-on-1; 1-on-1 for one pulse). Omitted when the "
        "rating does not state it.")
    value_definition: Literal["PEAK", "TOTAL_PER_1E2_AREA"] | None = Field(
        None, description="What a J/cm2 or W/cm2 value is: PEAK (the test beam's peak fluence or irradiance) or "
        "TOTAL_PER_1E2_AREA (the test pulse's total energy, or the beam's total power, divided by pi (d/2)^2 with d the test "
        "beam's 1/e^2 diameter: half the peak of a Gaussian test beam). Omitted when the rating does not state it: the value "
        "then establishes no damage threshold for a peak exposure.")
    polarization_condition: AuthorText | None = None
    status: FactStatus
    basis: AuthorText

    @model_validator(mode="after")
    def _consistent(self) -> PowerRatingV1:
        _positive(self.value, "value")
        _positive(self.wavelength_nm, "wavelength_nm")
        for name in ("pulse_duration_s", "repetition_rate_hz", "beam_diameter_mm"):
            if getattr(self, name) is not None:
                _positive(getattr(self, name), name)
        pulse_fields = (self.pulse_duration_s, self.repetition_rate_hz, self.pulse_count)
        if self.regime == "PULSED":
            if self.pulse_duration_s is None or self.unit != "J/cm2":
                raise ValueError("a PULSED rating is a fluence in J/cm2 and states pulse_duration_s")
        elif self.unit not in ("W/cm", "W/cm2") or any(value is not None for value in pulse_fields):
            raise ValueError("a CW rating is in W/cm or W/cm2 and has no pulse_duration_s, repetition_rate_hz or pulse_count")
        if self.value_definition is not None and self.unit == "W/cm":
            raise ValueError("value_definition applies to a J/cm2 or W/cm2 value; a W/cm value is a power per beam diameter")
        if self.test_protocol is not None and self.pulse_count is None:
            raise ValueError("test_protocol says how a pulsed rating's pulse_count pulses were applied: state pulse_count")
        return self


def _check_published_text(coatings: tuple[PublicCoatingV1, ...], products: tuple[PublicOpticalProductV2, ...]) -> None:
    """The published catalog's coating and rating text is printable ASCII (``PublicName`` / ``PublicText``), as all its
    other text: the classes accept any author text because a Project reuses them."""

    texts = [(f"coating {c.coating_id} {name}", getattr(c, name)) for c in coatings for name in ("display_name", "basis")]
    texts += [(f"{p.optical_sku} power_ratings[{i}] {name}", getattr(r, name)) for p in products
              for i, r in enumerate(p.specifications.power_ratings or ()) for name in ("polarization_condition", "basis")]
    for where, value in texts:
        if value is not None and _PUBLIC_ASCII_TEXT.fullmatch(value) is None:
            raise ValueError(f"{where}: the published catalog's text is printable ASCII, got {value!r}")


def check_power_ratings(ratings: tuple[PowerRatingV1, ...] | None) -> None:
    """Refuse duplicate power ratings (``ValueError``)."""

    payloads = [rating.model_dump_json() for rating in ratings or ()]
    if len(set(payloads)) != len(payloads):
        raise ValueError("duplicate power rating")


class ProductSpecificationsV1(StrictContractModel):
    """The values a designer compares products by (the public specification envelope).

    Always present: the outer size (exactly one of ``outer_diameter_mm`` for a round element and
    ``outer_dimensions_mm`` -- full widths along local X, Y, Z -- for a rectangular plate or a solid), the clear aperture
    (exactly one of ``clear_aperture_diameter_mm`` and ``clear_aperture_dimensions_mm``, full widths along local X / Y,
    the input face of an element placed by its ports), thickness (a lens's centre thickness; a plate's substrate
    thickness; a solid's extent along local Z) and the coating summary. Category- and form-specific values are present
    when the product uses them (``REQUIRED_SPECIFICATIONS``, ``REQUIRED_SPECIFICATIONS_BY_FORM``). ``materials`` lists the
    published material models of the optic's media.

    ``focal_length_mm`` of an off-axis parabolic mirror is its reflected focal length (aperture centre to focus),
    ``parent_focal_length_mm`` the focal length of its parent paraboloid and ``off_axis_angle_deg`` the angle between the
    parent axis and the reflected chief ray. ``deviation_angle_deg`` is the angle a prism turns the nominal beam by,
    ``wedge_angle_deg`` the angle between a wedged element's faces. ``extinction_ratio`` of a polarizing splitter is its
    transmitted p : s power ratio. ``power_ratings`` are the product's stated power-handling ratings with their
    conditions (catalog schema 3.1). ``cone_angle_deg`` is an axicon's base angle: the angle between its conical face and
    the plane perpendicular to its axis (catalog schema 3.1); its ``deviation_angle_deg``, when published, is the angle by
    which the conical face turns a ray travelling parallel to the axis.
    """

    outer_diameter_mm: PublicValueV1 | None = None
    outer_dimensions_mm: PublicVector3V1 | None = None
    clear_aperture_diameter_mm: PublicValueV1 | None = None
    clear_aperture_dimensions_mm: PublicVector2V1 | None = None
    thickness_mm: PublicValueV1
    coating: CoatingKind
    materials: tuple[MaterialId, ...] = ()
    working_band_nm: WavelengthBandV1 | None = None
    design_wavelength_nm: PublicValueV1 | None = None
    design_aoi_deg: PublicValueV1 | None = None
    focal_length_mm: PublicValueV1 | None = None
    focal_length_tolerance_percent: PublicValueV1 | None = None
    back_focal_length_mm: PublicValueV1 | None = None
    parent_focal_length_mm: PublicValueV1 | None = None
    off_axis_angle_deg: PublicValueV1 | None = None
    deviation_angle_deg: PublicValueV1 | None = None
    wedge_angle_deg: PublicValueV1 | None = None
    reflectance: PublicValueV1 | None = None
    transmittance: PublicValueV1 | None = None
    optical_density: PublicValueV1 | None = None
    center_wavelength_nm: PublicValueV1 | None = None
    bandwidth_fwhm_nm: PublicValueV1 | None = None
    edge_wavelength_nm: PublicValueV1 | None = None
    retardance_waves: PublicValueV1 | None = None
    extinction_ratio: PublicValueV1 | None = None
    power_ratings: tuple[PowerRatingV1, ...] | None = Field(None, min_length=1, max_length=MAX_POWER_RATINGS)
    cone_angle_deg: PublicValueV1 | None = None

    @field_validator("materials", mode="before")
    @classmethod
    def _sort_materials(cls, value: object) -> object:
        return _sorted_or_unchanged(value, str)

    @model_validator(mode="after")
    def _consistent(self) -> ProductSpecificationsV1:
        if (self.outer_diameter_mm is None) == (self.outer_dimensions_mm is None):
            raise ValueError("publish exactly one of outer_diameter_mm and outer_dimensions_mm")
        if (self.clear_aperture_diameter_mm is None) == (self.clear_aperture_dimensions_mm is None):
            raise ValueError("publish exactly one of clear_aperture_diameter_mm and clear_aperture_dimensions_mm")
        thickness = _positive(self.thickness_mm.value, "thickness_mm")
        with localcontext(DECIMAL_CONTEXT):
            if self.outer_diameter_mm is not None:
                outer = _positive(self.outer_diameter_mm.value, "outer_diameter_mm")
                if self.clear_aperture_diameter_mm is not None:
                    fits = _positive(self.clear_aperture_diameter_mm.value, "clear_aperture_diameter_mm") <= outer
                else:
                    x, y = (_positive(v, "clear_aperture_dimensions_mm") for v in self.clear_aperture_dimensions_mm.value)  # type: ignore[union-attr]
                    fits = x * x + y * y <= outer * outer
            else:
                dims = [_positive(v, "outer_dimensions_mm") for v in self.outer_dimensions_mm.value]  # type: ignore[union-attr]
                if thickness > dims[2]:
                    raise ValueError("thickness_mm cannot exceed the outer dimension along local Z")
                if self.clear_aperture_diameter_mm is not None:
                    fits = _positive(self.clear_aperture_diameter_mm.value, "clear_aperture_diameter_mm") <= sorted(dims)[1]
                else:
                    x, y = (_positive(v, "clear_aperture_dimensions_mm") for v in self.clear_aperture_dimensions_mm.value)  # type: ignore[union-attr]
                    fits = x <= dims[0] and y <= dims[1]
        if not fits:
            raise ValueError("clear aperture cannot exceed the outer diameter / outer dimensions")
        if self.off_axis_angle_deg is not None and not _ZERO < Decimal(self.off_axis_angle_deg.value) < _DEG_180:
            raise ValueError("off_axis_angle_deg must lie strictly between 0 and 180")
        if self.deviation_angle_deg is not None and not _ZERO <= Decimal(self.deviation_angle_deg.value) <= _DEG_180:
            raise ValueError("deviation_angle_deg must lie between 0 and 180")
        if self.wedge_angle_deg is not None and not _ZERO < Decimal(self.wedge_angle_deg.value) < MAX_AOI_DEG:
            raise ValueError("wedge_angle_deg must lie strictly between 0 and 90")
        if self.cone_angle_deg is not None and not _ZERO < Decimal(self.cone_angle_deg.value) < MAX_AOI_DEG:
            raise ValueError("cone_angle_deg must lie strictly between 0 and 90")
        if self.parent_focal_length_mm is not None:
            _positive(self.parent_focal_length_mm.value, "parent_focal_length_mm")
        for name in ("reflectance", "transmittance"):
            published = getattr(self, name)
            if published is not None:
                _fraction(published.value, name)
        for name in ("design_wavelength_nm", "center_wavelength_nm", "edge_wavelength_nm", "bandwidth_fwhm_nm", "retardance_waves"):
            published = getattr(self, name)
            if published is not None:
                _positive(published.value, name)
        if self.optical_density is not None and Decimal(self.optical_density.value) < _ZERO:
            raise ValueError(f"optical_density cannot be negative, got {self.optical_density.value}")
        if self.extinction_ratio is not None and Decimal(self.extinction_ratio.value) < _ONE:
            raise ValueError(f"extinction_ratio is a power ratio of at least 1, got {self.extinction_ratio.value}")
        if self.focal_length_mm is not None and Decimal(self.focal_length_mm.value) == _ZERO:
            raise ValueError("focal_length_mm cannot be zero")
        if self.focal_length_tolerance_percent is not None and Decimal(self.focal_length_tolerance_percent.value) < _ZERO:
            raise ValueError("focal_length_tolerance_percent cannot be negative")
        if self.design_aoi_deg is not None and not _ZERO <= Decimal(self.design_aoi_deg.value) <= MAX_AOI_DEG:
            raise ValueError("design_aoi_deg must lie between 0 and 90")
        if len(set(self.materials)) != len(self.materials):
            raise ValueError("duplicate material")
        check_power_ratings(self.power_ratings)
        if any(rating.status is FactStatus.DECLARED for rating in self.power_ratings or ()):
            raise ValueError("a published power rating is established by the manufacturer or OpenSci, not DECLARED")
        return self

    def outer_extents_mm(self) -> tuple[Decimal, Decimal, Decimal | None]:
        """Full outer widths along local X, Y and Z (Z is None for a round element: its thickness is ``thickness_mm``)."""

        if self.outer_diameter_mm is not None:
            diameter = Decimal(self.outer_diameter_mm.value)
            return diameter, diameter, None
        x, y, z = (Decimal(v) for v in self.outer_dimensions_mm.value)  # type: ignore[union-attr]
        return x, y, z

    def clear_aperture_extents_mm(self) -> tuple[Decimal, Decimal]:
        """Full clear-aperture widths along local X / Y (a circle: its diameter twice)."""

        if self.clear_aperture_diameter_mm is not None:
            diameter = Decimal(self.clear_aperture_diameter_mm.value)
            return diameter, diameter
        x, y = self.clear_aperture_dimensions_mm.value  # type: ignore[union-attr]
        return Decimal(x), Decimal(y)


#: Specification fields a product of each category must publish (beyond the always-present ones).
REQUIRED_SPECIFICATIONS: dict[OpticalCategory, frozenset[str]] = {
    OpticalCategory.LENS: frozenset({"focal_length_mm", "design_wavelength_nm"}),
    OpticalCategory.MIRROR: frozenset({"working_band_nm", "reflectance"}),
    OpticalCategory.BEAMSPLITTER: frozenset({"working_band_nm", "reflectance", "transmittance", "design_aoi_deg"}),
    OpticalCategory.FILTER: frozenset({"working_band_nm"}),
    OpticalCategory.WINDOW: frozenset({"working_band_nm"}),
    OpticalCategory.WAVEPLATE: frozenset({"retardance_waves", "design_wavelength_nm", "working_band_nm"}),
    OpticalCategory.POLARIZER: frozenset({"extinction_ratio", "working_band_nm"}),
    OpticalCategory.PRISM: frozenset({"working_band_nm"}),
}
#: Specification fields a product of a form must publish beyond its category's.
REQUIRED_SPECIFICATIONS_BY_FORM: dict[OpticalForm, frozenset[str]] = {
    OpticalForm.POLARIZING_CUBE_BEAMSPLITTER: frozenset({"extinction_ratio"}),
    OpticalForm.POLARIZING_PLATE_BEAMSPLITTER: frozenset({"extinction_ratio"}),
    OpticalForm.OFF_AXIS_PARABOLIC_MIRROR: frozenset({"focal_length_mm", "parent_focal_length_mm", "off_axis_angle_deg"}),
    OpticalForm.WEDGE_PRISM: frozenset({"wedge_angle_deg"}),
    OpticalForm.AXICON: frozenset({"cone_angle_deg"}),
    **{form: frozenset({"extinction_ratio"}) for form in SPLITTING_PRISM_FORMS},
}
#: Category specifications a form does not have: an axicon has no focal length and no design wavelength of one; a
#: polarizing splitter prism splits by polarization at normal incidence (no reflectance, no design angle of incidence).
EXEMPT_SPECIFICATIONS_BY_FORM: dict[OpticalForm, frozenset[str]] = {
    OpticalForm.AXICON: frozenset({"focal_length_mm", "design_wavelength_nm"}),
    **{form: frozenset({"reflectance", "design_aoi_deg"}) for form in SPLITTING_PRISM_FORMS},
}
_PARABOLOID = Decimal(-1)


def design_roles_for(category: OpticalCategory, form: OpticalForm) -> frozenset[str]:
    """Project ``optical_role`` values a product of ``category`` / ``form`` may play."""

    return DESIGN_ROLES_BY_CATEGORY.get(category, frozenset()) | EXTRA_DESIGN_ROLES_BY_FORM.get(form, frozenset())


#: The fields of a public product that state supply, not engineering: a change of them never changes a simulation.
SUPPLY_FACT_FIELDS: frozenset[str] = frozenset({"commercial_readiness", "opensci_mounting", "delivered_form"})
#: The words a product is presented with: its names, summary and notices. Neither a design's simulation nor its mechanical
#: realization reads them, so a release that rewords them publishes the same engineering content.
PRESENTATION_FIELDS: frozenset[str] = frozenset({"display_name", "display_name_zh_cn", "summary", "notices"})


class PublicOpticalProductV2(StrictContractModel):
    """One OpenSci optical product of the public catalog.

    ``commercial_readiness`` says whether OpenSci supplies the product; ``opensci_mounting`` whether OpenSci also supplies
    mounting hardware for it; ``delivered_form`` whether the delivered part is the optical element itself or the element
    in its manufacturer's housing. The last two are catalog schema 3.1 facts, published together; a release without them
    (every release before 2026-10-15.1) mounts every ORDERABLE product and delivers the optical element itself
    (``mounting`` and ``delivery_form`` give the effective values for any release).
    """

    optical_sku: OpticalSku
    category: OpticalCategory
    form: OpticalForm
    display_name: PublicName
    display_name_zh_cn: PublicNameZhCn
    summary: PublicText
    commercial_readiness: CommercialReadiness
    frame: OpticalFrameV1
    specifications: ProductSpecificationsV1
    simulation_models: tuple[SimulationModelV2, ...] = Field(min_length=1)
    design_geometry: DesignGeometryV1
    notices: tuple[PublicText, ...] = ()
    opensci_mounting: OpenSciMounting | None = Field(
        None, description="Whether OpenSci supplies mounting hardware for the product (catalog schema 3.1; absent in an "
                          "earlier release, where every ORDERABLE product is mounted).")
    delivered_form: DeliveredForm | None = Field(
        None, description="OPTIC: the optical element itself; HOUSED: the element in its manufacturer's housing, whose "
                          "outline differs from the design geometry (catalog schema 3.1; absent in an earlier release: OPTIC).")

    @property
    def mounting(self) -> OpenSciMounting:
        """Whether OpenSci mounts the product, in any release: ``opensci_mounting``, or for a release without it
        ``AVAILABLE`` exactly when the product is ORDERABLE."""

        if self.opensci_mounting is not None:
            return self.opensci_mounting
        return OpenSciMounting.AVAILABLE if self.commercial_readiness is CommercialReadiness.ORDERABLE else OpenSciMounting.NOT_AVAILABLE

    @property
    def delivery_form(self) -> DeliveredForm:
        """The delivered form in any release: ``delivered_form``, or ``OPTIC`` for a release without it."""

        return self.delivered_form if self.delivered_form is not None else DeliveredForm.OPTIC

    def engineering_document(self) -> dict[str, Any]:
        """The product's public document without its supply facts (``SUPPLY_FACT_FIELDS``: whether OpenSci supplies and
        mounts it and in which form it is delivered) and its presentation words (``PRESENTATION_FIELDS``: names, summary,
        notices), ``None`` fields omitted: what a design's simulation and mechanical realization depend on (frame,
        specifications, simulation models, design geometry). Two releases publishing equal engineering documents of a
        product simulate and mount it identically."""

        return self.model_dump(mode="json", by_alias=True, exclude_none=True,
                               exclude=set(SUPPLY_FACT_FIELDS | PRESENTATION_FIELDS))

    @model_validator(mode="after")
    def _consistent(self) -> PublicOpticalProductV2:
        if self.form not in FORMS_BY_CATEGORY.get(self.category, frozenset()):
            raise ValueError(f"form {self.form} is not a {self.category} form")
        if (self.opensci_mounting is None) != (self.delivered_form is None):
            raise ValueError("opensci_mounting and delivered_form are published together")
        if self.opensci_mounting is OpenSciMounting.AVAILABLE and self.commercial_readiness is not CommercialReadiness.ORDERABLE:
            raise ValueError("OpenSci mounts only a product it supplies: opensci_mounting AVAILABLE needs commercial_readiness ORDERABLE")
        if self.delivered_form is DeliveredForm.HOUSED and self.opensci_mounting is OpenSciMounting.AVAILABLE:
            raise ValueError("OpenSci does not mount a housed product: delivered_form HOUSED needs opensci_mounting NOT_AVAILABLE")
        self._check_frame()
        kinds = [(model.model_kind, model.model_version) for model in self.simulation_models]
        if len(set(kinds)) != len(kinds):
            raise ValueError("duplicate simulation model kind/version")
        allowed = MODEL_KINDS_BY_CATEGORY.get(self.category, frozenset())
        for kind, _ in kinds:
            if kind not in allowed:
                raise ValueError(f"a {self.category} product cannot publish a {kind} simulation model")
            if self.form in PORTS_FORMS and kind != "SURFACE_SET":
                raise ValueError(f"a {self.form} product publishes SURFACE_SET models")
            if kind == "NEUTRAL_DENSITY" and self.form is not OpticalForm.NEUTRAL_DENSITY_FILTER:
                raise ValueError(f"a NEUTRAL_DENSITY model belongs to a NEUTRAL_DENSITY_FILTER product, not a {self.form}")
            if (kind == "JOINED_BODIES") != (self.form in POLARIZING_PRISM_FORMS):
                raise ValueError(f"a JOINED_BODIES model belongs to a polarizing prism product, which publishes JOINED_BODIES models "
                                 f"only (a {self.form} publishing a {kind} model)")
        symmetric = all(model.is_rotationally_symmetric for model in self.simulation_models)
        if (self.frame.symmetry is OpticalSymmetry.NONE) == symmetric:
            raise ValueError(
                "symmetry is NONE exactly when the element is not rotationally symmetric about local Z (an in-plane axis, a "
                "cylindrical surface, a rectangular or decentred aperture or outline, posed surfaces)"
            )
        required = ((REQUIRED_SPECIFICATIONS.get(self.category, frozenset()) - EXEMPT_SPECIFICATIONS_BY_FORM.get(self.form, frozenset()))
                    | REQUIRED_SPECIFICATIONS_BY_FORM.get(self.form, frozenset()))
        missing = sorted(name for name in required if getattr(self.specifications, name) is None)
        if missing:
            raise ValueError(f"a {self.category} product must publish specifications {missing}")
        if len(set(self.notices)) != len(self.notices):
            raise ValueError("duplicate notice")
        for model in self.simulation_models:
            self._check_model(model)
            if isinstance(model, SurfaceSetModelV1) and model.has_untraced_faces != (
                    SimulationApproximation.UNTRACED_FACES_BLOCK in model.approximations):
                raise ValueError("UNTRACED_FACES_BLOCK is listed exactly when a body face carries no traced surface (a published "
                                 "product lists it exactly)")
        self._check_form_profiles()
        self._check_design_geometry()
        return self

    def _check_frame(self) -> None:
        face = self.form in FACE_NORMAL_FORMS
        ported = self.form in PORTS_FORMS
        if self.frame.is_reflector != face:
            raise ValueError(f"a {self.form} product {'has' if face else 'does not have'} the face-normal frame")
        if self.frame.has_ports != ported:
            raise ValueError(f"a {self.form} product {'is' if ported else 'is not'} placed by its ports (reference_axis PORTS)")
        if face:
            expected = "REFLECTED_ABOUT_+Z" if self.form in REFLECT_ONLY_FORMS else "SPLIT_ABOUT_+Z"
            if self.frame.nominal_propagation != expected:
                raise ValueError(f"a {self.form} product's nominal propagation is {expected}")
        if ported and self.frame.origin is not PORTS_FORM_ORIGINS[self.form]:
            raise ValueError(f"a {self.form} product's frame origin is {PORTS_FORM_ORIGINS[self.form]}")

    def _check_model(self, model: SurfaceSequenceModelV1 | SurfaceSetModelV1 | PolarizationElementModelV1 | NeutralDensityModelV1
                     | JoinedBodiesModelV1) -> None:
        spec = self.specifications
        if spec.working_band_nm is not None and not model.valid_band_nm.within(spec.working_band_nm):
            raise ValueError("a simulation model's valid band lies outside the working band")
        if isinstance(model, NeutralDensityModelV1):
            clear_x, clear_y = spec.clear_aperture_extents_mm()
            model_x, model_y = model.clear_aperture_extents_mm()
            if model_x > clear_x or model_y > clear_y:
                raise ValueError("a simulation model's clear aperture exceeds the published clear aperture")
            outer_x, outer_y, _ = spec.outer_extents_mm()
            if (model.edge_radius_mm is not None) != (spec.outer_diameter_mm is not None) or model.outline_extents_mm() != (outer_x, outer_y):
                raise ValueError("a simulation model's outline is the published outer diameter / outer dimensions along local X / Y")
            return
        if isinstance(model, PolarizationElementModelV1):
            if spec.outer_diameter_mm is None or spec.clear_aperture_diameter_mm is None:
                raise ValueError("a POLARIZATION_ELEMENT product publishes an outer diameter and a clear aperture diameter")
            if Decimal(model.clear_radius_mm) * 2 > Decimal(spec.clear_aperture_diameter_mm.value):
                raise ValueError("a simulation model's clear radius exceeds the published clear aperture")
            if Decimal(model.edge_radius_mm) * 2 != Decimal(spec.outer_diameter_mm.value):
                raise ValueError("a simulation model's edge radius is the published outer radius")
            return
        if set(model.material_ids()) - set(spec.materials):
            raise ValueError("specifications.materials must list every medium of the simulation model")
        if isinstance(model, SurfaceSequenceModelV1):
            self._check_sequence(model)
        elif isinstance(model, JoinedBodiesModelV1):
            self._check_joined(model)
        else:
            self._check_surface_set(model)

    def _check_joined(self, model: JoinedBodiesModelV1) -> None:
        """A polarizing prism: its outer dimensions are the bodies' extents along local X, Y, Z; the first body's first surface
        is its entrance face, its clear aperture inside the published one, and its centre the frame origin (a transmissive
        frame: light arrives along local +Z)."""

        spec = self.specifications
        with localcontext(DECIMAL_CONTEXT):
            vertices = [v for member in model.members for v in member.model.body.vertices_mm()]  # type: ignore[union-attr]
            extents = tuple(max(v[k] for v in vertices) - min(v[k] for v in vertices) for k in range(3))
            if spec.outer_dimensions_mm is None or not _close(extents, spec.outer_extents_mm(), GEOMETRY_TOLERANCE_MM):  # type: ignore[arg-type]
                raise ValueError("outer_dimensions_mm are the joined bodies' extents along local X, Y, Z")
            entrance = model.members[0].model.surfaces[0]
            clear_x, clear_y = spec.clear_aperture_extents_mm()
            aperture_x, aperture_y = entrance.aperture_extents_mm()
            if aperture_x > clear_x or aperture_y > clear_y:
                raise ValueError("the entrance face's clear aperture exceeds the published clear aperture")
            if not _close(entrance.aperture_center_point(), (_ZERO, _ZERO, _ZERO), GEOMETRY_TOLERANCE_MM):
                raise ValueError("a polarizing prism's frame origin is the centre of its entrance face (the first body's first surface)")

    def _check_sequence(self, model: SurfaceSequenceModelV1) -> None:
        spec = self.specifications
        clear_x, clear_y = spec.clear_aperture_extents_mm()
        model_x, model_y = model.clear_aperture_extents_mm()
        if model_x > clear_x or model_y > clear_y:
            raise ValueError("a simulation model's clear radius exceeds the published clear aperture")
        outer_x, outer_y, _ = spec.outer_extents_mm()
        if model.is_round:
            if spec.outer_diameter_mm is None or Decimal(model.edge_radius_mm) * 2 != outer_x:  # type: ignore[arg-type]
                raise ValueError("a simulation model's edge radius is the published outer radius")
        elif spec.outer_dimensions_mm is None or model.outline_extents_mm() != (outer_x, outer_y):
            raise ValueError("a simulation model's edge half widths are half the published outer dimensions along local X / Y")
        face = self.frame.is_reflector
        if face and Decimal(model.surfaces[-1].vertex_z_mm) != 0:
            raise ValueError("a face-normal product's coated face is its last surface, at z = 0")
        if face and model.surfaces[-1].traced is not self._coated_face_branches():
            raise ValueError(f"a {self.form} product's coated face (z = 0) is traced {self._coated_face_branches().value}")
        if not face and Decimal(model.surfaces[0].vertex_z_mm) != 0:
            raise ValueError("a transmissive product's first surface vertex is the frame origin (z = 0)")

    def _coated_face_branches(self) -> TracedBranches:
        """What a face-normal product's coated face traces: a mirror reflects; a splitter, sampler or dichroic splits."""

        return TracedBranches.REFLECTED if self.form in REFLECT_ONLY_FORMS else TracedBranches.TRANSMITTED_AND_REFLECTED

    def _check_surface_set(self, model: SurfaceSetModelV1) -> None:
        spec = self.specifications
        origin: Vec = (_ZERO, _ZERO, _ZERO)
        body = model.body
        if body is None:
            clear_x, clear_y = spec.clear_aperture_extents_mm()
            outer_x, outer_y, _ = spec.outer_extents_mm()
            extents = [s.aperture_extents_mm() for s in model.surfaces]
            if min(e[0] for e in extents) > clear_x or min(e[1] for e in extents) > clear_y:
                raise ValueError("a simulation model's clear radius exceeds the published clear aperture")
            if any(e[0] > outer_x or e[1] > outer_y for e in extents):
                raise ValueError("a surface's clear aperture exceeds the published outer size")
        else:
            outer_x, outer_y, outer_z = spec.outer_extents_mm()
            if body.is_round:
                if spec.outer_diameter_mm is None or Decimal(body.side_radius_mm) * 2 != outer_x:  # type: ignore[arg-type]
                    raise ValueError("a round body's side radius is the published outer radius")
            else:
                vertices = body.vertices_mm()
                extents = tuple(max(v[k] for v in vertices) - min(v[k] for v in vertices) for k in range(3))
                if spec.outer_dimensions_mm is None or not _close(extents, (outer_x, outer_y, outer_z), GEOMETRY_TOLERANCE_MM):  # type: ignore[arg-type]
                    raise ValueError("outer_dimensions_mm are the body's extents along local X, Y, Z")
        if self.form is OpticalForm.OFF_AXIS_PARABOLIC_MIRROR:
            mirrors = [s for s in model.surfaces if s.traced is TracedBranches.REFLECTED and s.conic is not None
                       and Decimal(s.conic) == _PARABOLOID and s.aperture_center_mm is not None and s.aspheric_coefficients is None
                       and s.sag is None]
            if not mirrors:
                raise ValueError("an off-axis parabolic mirror reflects on a paraboloid (conic -1) with a decentred aperture")
            # a REFLECTED surface reflects the light arriving on its front (+normal) side: a focusing paraboloid is concave
            # toward it, its centre of curvature on that side (radius_mm > 0)
            mirrors = [s for s in mirrors if Decimal(s.radius_mm) > _ZERO]  # type: ignore[arg-type]
            if not mirrors:
                raise ValueError("an off-axis parabolic mirror's paraboloid is concave toward the light it reflects (radius_mm > 0)")
            parent = spec.parent_focal_length_mm
            if parent is not None and all(abs(Decimal(s.radius_mm)) != Decimal(parent.value) * 2 for s in mirrors):  # type: ignore[arg-type]
                raise ValueError("the paraboloid's vertex radius is twice the parent focal length")
            self._check_paraboloid_relations(mirrors)
        frame = self.frame
        with localcontext(DECIMAL_CONTEXT):
            if frame.reference_axis is None:
                if not _close(_vec(model.surfaces[0].origin_mm), origin, _ZERO):
                    raise ValueError("a transmissive product's first surface vertex is the frame origin (0, 0, 0)")
            elif frame.is_reflector:
                branches = self._coated_face_branches()
                if not any(s.traced is branches and _close(_vec(s.origin_mm), origin, _ZERO)
                           and _vec(s.normal) == (_ZERO, _ZERO, _ONE) for s in model.surfaces):
                    raise ValueError(f"a face-normal product's coated face is a surface at the origin with normal (0, 0, 1), traced "
                                     f"{branches.value}")
            elif frame.origin is FrameOrigin.BODY_CENTER:
                centre = body.volume_and_centroid()[1] if body is not None else None
                if centre is not None and body.is_round:  # type: ignore[union-attr]
                    centre = (_ZERO, _ZERO, centre[2])  # a round body's axis is local Z: the point of it at the centroid's height
                if centre is None or not _close(centre, origin, GEOMETRY_TOLERANCE_MM):
                    raise ValueError("a BODY_CENTER frame's origin is the centroid of the model's body (on the axis of a round body)")
            elif not any(s.aperture_center_mm is not None and _close(s.aperture_center_point(), origin, GEOMETRY_TOLERANCE_MM)
                         for s in model.surfaces):
                raise ValueError("an APERTURE_CENTER frame's origin is the centre of a decentred surface's clear aperture")
            if body is not None:
                for port in frame.ports or ():
                    point, direction = _vec(port.point_mm), _vec(port.direction)
                    if not body.on_boundary(point):
                        raise ValueError(f"port {port.port_id} lies on the body's surface")
                    for index in body.faces_through(point):
                        along = _dot(_vec(body.faces[index].outward_normal), direction)
                        if (along >= 0) if port.role == "INPUT" else (along <= 0):
                            raise ValueError(f"port {port.port_id} must {'enter' if port.role == 'INPUT' else 'leave'} the body through its face")

    def _check_paraboloid_relations(self, mirrors: list[PosedSurfaceV1]) -> None:
        """RFL = 2 f / (1 + cos theta) and the aperture decentre d = 2 f tan(theta / 2) = 2 f sin(theta) / (1 + cos theta),
        within ``OAP_RELATION_TOLERANCE``, where the parent focal length f and the off-axis angle theta are published."""

        spec = self.specifications
        if spec.parent_focal_length_mm is None or spec.off_axis_angle_deg is None:
            return
        with localcontext(DECIMAL_CONTEXT):
            f = Decimal(spec.parent_focal_length_mm.value)
            cos, sin = _cos_sin_deg(Decimal(spec.off_axis_angle_deg.value))
            if spec.focal_length_mm is not None:
                expected = 2 * f / (_ONE + cos)
                if abs(Decimal(spec.focal_length_mm.value) - expected) > OAP_RELATION_TOLERANCE * expected:
                    raise ValueError(f"an off-axis parabolic mirror's reflected focal length is 2 f / (1 + cos theta) = "
                                     f"{expected.quantize(Decimal('0.0001'))} mm, published {spec.focal_length_mm.value}")
            expected = 2 * f * sin / (_ONE + cos)
            decentres = [(Decimal(s.aperture_center_mm[0]) ** 2 + Decimal(s.aperture_center_mm[1]) ** 2).sqrt() for s in mirrors]  # type: ignore[index]
            if all(abs(d - expected) > OAP_RELATION_TOLERANCE * expected for d in decentres):
                raise ValueError(f"an off-axis parabolic mirror's aperture is decentred by 2 f tan(theta / 2) = "
                                 f"{expected.quantize(Decimal('0.0001'))} mm")

    def _check_form_profiles(self) -> None:
        sequences = [m for m in self.simulation_models if isinstance(m, SurfaceSequenceModelV1)]
        surfaces = [s for m in sequences for s in m.surfaces]
        if self.form in SPHERICAL_LENS_FORMS and any(s.is_aspheric or s.is_cylindrical or s.is_axicon or s.is_freeform for s in surfaces):
            raise ValueError(f"a {self.form} has plane and spherical surfaces only")
        if self.form is OpticalForm.ASPHERIC_LENS and (not any(s.is_aspheric or s.is_freeform for s in surfaces) or any(s.is_cylindrical for s in surfaces)):
            raise ValueError("an ASPHERIC_LENS has a conic, polynomial or freeform surface and no cylindrical surface")
        if self.form is OpticalForm.CYLINDRICAL_LENS and not any(s.is_cylindrical or s.has_section_powers for s in surfaces):
            raise ValueError("a CYLINDRICAL_LENS has a cylindrical, biconic or toroidal surface")
        if self.form is OpticalForm.AXICON and (not any(s.is_axicon for s in surfaces) or any(s.is_cylindrical for s in surfaces)):
            raise ValueError("an AXICON has a conical (axicon) surface and no cylindrical surface")

    def _check_design_geometry(self) -> None:
        geometry = self.design_geometry
        spec = self.specifications
        extents = geometry.extents_mm()
        with localcontext(DECIMAL_CONTEXT):
            if spec.outer_diameter_mm is not None:
                outer = Decimal(spec.outer_diameter_mm.value)
                if extents[0] < outer or extents[1] < outer:
                    raise ValueError("design_geometry must bound the outer diameter")
            else:
                x, y, z = spec.outer_extents_mm()
                if isinstance(geometry, CylinderGeometryV1):
                    bounded = extents[0] * extents[0] >= x * x + y * y and extents[2] >= z  # type: ignore[operator]
                else:
                    bounded = extents[0] >= x and extents[1] >= y and extents[2] >= z  # type: ignore[operator]
                if not bounded:
                    raise ValueError("design_geometry must bound the outer dimensions")
            for model in self.simulation_models:
                if isinstance(model, JoinedBodiesModelV1):
                    if not all(geometry.contains(v) for member in model.members for v in member.model.body.vertices_mm()):  # type: ignore[union-attr]
                        raise ValueError("design_geometry must contain the model's bodies")
                    continue
                body = model.body if isinstance(model, SurfaceSetModelV1) else None
                if body is None:
                    continue
                if body.is_round:
                    low, high = body.z_range_mm()
                    radius = Decimal(body.side_radius_mm)  # type: ignore[arg-type]
                    if not isinstance(geometry, CylinderGeometryV1) or Decimal(geometry.diameter_mm) < radius * 2 or not (
                        Decimal(geometry.z_min_mm) - GEOMETRY_TOLERANCE_MM <= low and high <= Decimal(geometry.z_max_mm) + GEOMETRY_TOLERANCE_MM
                    ):
                        raise ValueError("a round body's design_geometry is a CYLINDER that contains it")
                elif not all(geometry.contains(v) for v in body.vertices_mm()):
                    raise ValueError("design_geometry must contain the model's body")

    @property
    def design_roles(self) -> frozenset[str]:
        """Project ``optical_role`` values this product may play."""

        return design_roles_for(self.category, self.form)

    @property
    def is_rotationally_symmetric(self) -> bool:
        return self.frame.symmetry is OpticalSymmetry.ROTATIONAL_ABOUT_LOCAL_Z


def catalog_schema_3_1_features(coatings: tuple[PublicCoatingV1, ...], products: tuple[PublicOpticalProductV2, ...]) -> tuple[str, ...]:
    """Every catalog-schema-3.1 field the coatings and products of a catalog document use (empty for a 3.0 document)."""

    features = [feature for coating in coatings for feature in coating.schema_3_1_features()]
    for product in products:
        if product.specifications.power_ratings is not None:
            features.append(f"{product.optical_sku}: power_ratings")
        if product.specifications.cone_angle_deg is not None:
            features.append(f"{product.optical_sku}: cone_angle_deg")
        if product.form is OpticalForm.AXICON:
            features.append(f"{product.optical_sku}: AXICON form")
        if product.opensci_mounting is not None:
            features.append(f"{product.optical_sku}: opensci_mounting / delivered_form")
        features += [f"{product.optical_sku}: {feature}" for model in product.simulation_models for feature in model_schema_3_1_features(model)]
    return tuple(features)


def catalog_schema_3_2_features(coatings: tuple[PublicCoatingV1, ...], products: tuple[PublicOpticalProductV2, ...]) -> tuple[str, ...]:
    """Every catalog-schema-3.2 field the coatings and products of a catalog document use (empty when it uses none)."""

    del products  # (schema 3.2 adds a coating field only)
    return tuple(feature for coating in coatings for feature in coating.schema_3_2_features())


def catalog_schema_3_3_features(coatings: tuple[PublicCoatingV1, ...], products: tuple[PublicOpticalProductV2, ...]) -> tuple[str, ...]:
    """Every catalog-schema-3.3 field the products of a catalog document use (empty when it uses none)."""

    del coatings  # (schema 3.3 adds a surface field only)
    return tuple(f"{product.optical_sku}: {feature}" for product in products for model in product.simulation_models
                 for feature in model_schema_3_3_features(model))


def catalog_schema_3_5_features(coatings: tuple[PublicCoatingV1, ...], products: tuple[PublicOpticalProductV2, ...]) -> tuple[str, ...]:
    """Every catalog-schema-3.5 field the products of a catalog document use: a ``JOINED_BODIES`` model (and with it the
    crystal materials and optic axes, which only such a model uses). Empty for a document of an earlier schema."""

    return tuple(f"{product.optical_sku}: {feature}" for product in products for model in product.simulation_models
                 for feature in model_schema_3_5_features(model))


def catalog_schema_3_4_features(coatings: tuple[PublicCoatingV1, ...], products: tuple[PublicOpticalProductV2, ...]) -> tuple[str, ...]:
    """Every catalog-schema-3.4 field the products of a catalog document use (empty when it uses none)."""

    del coatings  # (schema 3.4 adds a surface field only)
    return tuple(f"{product.optical_sku}: {feature}" for product in products for model in product.simulation_models
                 for feature in model_schema_3_4_features(model))


def catalog_schema_3_6_features(coatings: tuple[PublicCoatingV1, ...], products: tuple[PublicOpticalProductV2, ...],
                                materials: tuple[PublicMaterialV1, ...] = ()) -> tuple[str, ...]:
    """Every catalog-schema-3.6 feature of a catalog document: a published material no product's
    ``specifications.materials`` names (a design material, published for designing with it), and the material physics
    schema 3.6 adds (``material_schema_3_6_features``: identity, index reference, bulk loss, thermo-optic data, the new
    dispersion kinds). Empty for a document of an earlier schema."""

    del coatings  # (schema 3.6 publishes materials only)
    used = {material_id for product in products for material_id in product.specifications.materials}
    features = [f"material {material.material_id}: design material (no product names it)" for material in materials
                if material.material_id not in used]
    for material in materials:
        features += material_schema_3_6_features(material)
    return tuple(features)


def required_catalog_schema_version(coatings: tuple[PublicCoatingV1, ...], products: tuple[PublicOpticalProductV2, ...],
                                    materials: tuple[PublicMaterialV1, ...] = ()) -> str:
    """The ``schema_version`` a catalog document with these coatings, products and published materials states: ``3.6`` when it
    publishes a design material (``catalog_schema_3_6_features``), else ``3.5`` when it uses any 3.5 field
    (``catalog_schema_3_5_features``), else ``3.4`` when it uses any 3.4 field (``catalog_schema_3_4_features``), else ``3.3``
    when it uses any 3.3 field (``catalog_schema_3_3_features``), else ``3.2`` when it uses any 3.2 field
    (``catalog_schema_3_2_features``), else ``3.1`` when it uses any 3.1 field (``catalog_schema_3_1_features``), else ``3.0``.
    Without ``materials`` no design material is considered."""

    if catalog_schema_3_6_features(coatings, products, materials):
        return PUBLIC_OPTICAL_CATALOG_SCHEMA_VERSION_3_6
    if catalog_schema_3_5_features(coatings, products):
        return PUBLIC_OPTICAL_CATALOG_SCHEMA_VERSION_3_5
    if catalog_schema_3_4_features(coatings, products):
        return PUBLIC_OPTICAL_CATALOG_SCHEMA_VERSION_3_4
    if catalog_schema_3_3_features(coatings, products):
        return PUBLIC_OPTICAL_CATALOG_SCHEMA_VERSION_3_3
    if catalog_schema_3_2_features(coatings, products):
        return PUBLIC_OPTICAL_CATALOG_SCHEMA_VERSION_3_2
    return PUBLIC_OPTICAL_CATALOG_SCHEMA_VERSION_3_1 if catalog_schema_3_1_features(coatings, products) else PUBLIC_OPTICAL_CATALOG_SCHEMA_VERSION


def _declared_status_at(node: object, path: str) -> str | None:
    """The path of the first ``status`` that is ``DECLARED`` in a catalog document's raw input (dicts, lists, models)."""

    if isinstance(node, StrictContractModel):
        node = node.model_dump(mode="json", by_alias=True)
    if isinstance(node, dict):
        if node.get("status") == FactStatus.DECLARED.value:
            return f"{path}.status" if path else "status"
        for key, value in node.items():
            if isinstance(value, (dict, list, tuple, StrictContractModel)):
                found = _declared_status_at(value, f"{path}.{key}" if path else str(key))
                if found is not None:
                    return found
    elif isinstance(node, (list, tuple)):
        for index, value in enumerate(node):
            if isinstance(value, (dict, list, tuple, StrictContractModel)):
                found = _declared_status_at(value, f"{path}[{index}]")
                if found is not None:
                    return found
    return None


#: The values of a focal-length sign filter.
FOCAL_SIGNS = ("positive", "negative")
#: What a focal-length query compares: stated in every response that applied one.
FOCAL_QUERY_BASIS = (
    "focal_length_mm is the signed nominal focal length a product publishes, at its design_wavelength_nm when it states one. "
    "A wavelength filter selects products with a simulation model valid at that wavelength and does not change the value "
    "compared; paraxial analysis of a placed component gives its focal distances at a chosen wavelength."
)


class FocalQueryRefusal(StrEnum):
    """Why a focal-length query was refused (``CatalogQueryError.reason``)."""

    VALUE_INVALID = "FOCAL_VALUE_INVALID"
    BOUNDS_CONFLICT = "FOCAL_BOUNDS_CONFLICT"
    TOLERANCE_WITHOUT_VALUE = "FOCAL_TOLERANCE_WITHOUT_VALUE"
    TOLERANCE_NEGATIVE = "FOCAL_TOLERANCE_NEGATIVE"
    RANGE_INVERTED = "FOCAL_RANGE_INVERTED"
    SIGN_UNKNOWN = "FOCAL_SIGN_UNKNOWN"
    SIGN_CONTRADICTS_RANGE = "FOCAL_SIGN_CONTRADICTS_RANGE"


class CatalogQueryError(ValueError):
    """A refused catalog query: its stable ``reason``, the query ``parameter`` concerned and a ``message``."""

    def __init__(self, reason: FocalQueryRefusal, parameter: str, message: str) -> None:
        super().__init__(f"{reason.value}: {message}")
        self.reason = reason
        self.parameter = parameter
        self.message = message


def _focal_decimal(value: object, parameter: str) -> Decimal | None:
    if value is None:
        return None
    try:
        if isinstance(value, bool):
            raise ValueError
        # a float by its shortest spelling: -50.1 means -50.1, not its binary expansion
        return parse_query_decimal(str(value) if isinstance(value, float) else value, parameter)  # type: ignore[arg-type]
    except ValueError:
        raise CatalogQueryError(FocalQueryRefusal.VALUE_INVALID, parameter,
                                f"{parameter} must be a finite decimal in millimetres, got {value!r}") from None


@dataclass(frozen=True)
class FocalLengthQuery:
    """A signed nominal focal-length filter: the one definition every public catalog surface applies (the CLI, the JSON
    API and ``PublicOpticalCatalogV2.select``; a further surface builds it with ``of`` and answers with ``apply``).

    It compares ``specifications.focal_length_mm`` exactly, in millimetres and with its sign: negative lenses lie below
    zero, so a lens near -50 mm is the range [-60, -40], and ``focal_sign`` keeps one side of zero. A product that
    publishes no focal length never matches. ``focal_length_mm`` / ``focal_tolerance_mm`` hold what was asked for as a
    nominal value; ``focal_min_mm`` / ``focal_max_mm`` are the bounds compared (inclusive; ``None``: open). The tolerance is
    the caller's, separate from the ``focal_length_tolerance_percent`` a product publishes. ``FOCAL_QUERY_BASIS`` says what
    the compared value is."""

    focal_min_mm: Decimal | None = None
    focal_max_mm: Decimal | None = None
    focal_sign: str | None = None
    focal_length_mm: Decimal | None = None
    focal_tolerance_mm: Decimal | None = None

    @classmethod
    def of(
        cls,
        *,
        focal_length_mm: object = None,
        focal_tolerance_mm: object = None,
        focal_min_mm: object = None,
        focal_max_mm: object = None,
        focal_sign: object = None,
    ) -> FocalLengthQuery | None:
        """The query caller input describes, ``None`` when it asks for none, or ``CatalogQueryError``. A value is a decimal
        string or a number; ``focal_length_mm`` (with an optional ``focal_tolerance_mm``, default exact) excludes
        ``focal_min_mm`` / ``focal_max_mm``."""

        nominal = _focal_decimal(focal_length_mm, "focal_length_mm")
        tolerance = _focal_decimal(focal_tolerance_mm, "focal_tolerance_mm")
        low = _focal_decimal(focal_min_mm, "focal_min_mm")
        high = _focal_decimal(focal_max_mm, "focal_max_mm")
        if focal_sign is not None and focal_sign not in FOCAL_SIGNS:
            raise CatalogQueryError(FocalQueryRefusal.SIGN_UNKNOWN, "focal_sign", f"unknown focal_sign {focal_sign!r}; known: {list(FOCAL_SIGNS)}")
        if nominal is None and tolerance is None and low is None and high is None and focal_sign is None:
            return None
        if nominal is not None and (low is not None or high is not None):
            raise CatalogQueryError(FocalQueryRefusal.BOUNDS_CONFLICT, "focal_length_mm",
                                    "give focal_length_mm (with focal_tolerance_mm) or focal_min_mm / focal_max_mm, not both")
        if tolerance is not None and nominal is None:
            raise CatalogQueryError(FocalQueryRefusal.TOLERANCE_WITHOUT_VALUE, "focal_tolerance_mm", "focal_tolerance_mm is a tolerance on focal_length_mm; give focal_length_mm too")
        if tolerance is not None and tolerance < _ZERO:
            raise CatalogQueryError(FocalQueryRefusal.TOLERANCE_NEGATIVE, "focal_tolerance_mm", f"focal_tolerance_mm cannot be negative, got {tolerance}")
        if nominal is not None:
            low, high = nominal, nominal
            if tolerance:  # no arithmetic on an exact query: the bound is the value asked for, every digit of it
                try:
                    with localcontext(DECIMAL_CONTEXT):
                        low, high = nominal - tolerance, nominal + tolerance
                except ArithmeticError:
                    raise CatalogQueryError(FocalQueryRefusal.VALUE_INVALID, "focal_length_mm",
                                            f"focal_length_mm {nominal} and focal_tolerance_mm {tolerance} are out of range") from None
        if low is not None and high is not None and low > high:
            raise CatalogQueryError(FocalQueryRefusal.RANGE_INVERTED, "focal_min_mm", f"focal_min_mm {low} is above focal_max_mm {high}")
        beyond = (f"at or below {high}" if focal_sign == "positive" and high is not None and high <= _ZERO
                  else f"at or above {low}" if focal_sign == "negative" and low is not None and low >= _ZERO else None)
        if beyond is not None:
            raise CatalogQueryError(FocalQueryRefusal.SIGN_CONTRADICTS_RANGE, "focal_sign",
                                    f"no {focal_sign} focal length is {beyond}; bounds are signed: a lens near -50 mm is "
                                    "focal_min_mm -60, focal_max_mm -40")
        return cls(focal_min_mm=low, focal_max_mm=high, focal_sign=focal_sign, focal_length_mm=nominal, focal_tolerance_mm=tolerance)  # type: ignore[arg-type]

    def matches(self, product: PublicOpticalProductV2) -> bool:
        """Whether ``product`` publishes a focal length inside this query."""

        published = product.specifications.focal_length_mm
        if published is None:
            return False
        value = Decimal(published.value)
        return ((self.focal_min_mm is None or value >= self.focal_min_mm)
                and (self.focal_max_mm is None or value <= self.focal_max_mm)
                and (self.focal_sign is None or (value > _ZERO) == (self.focal_sign == "positive")))

    def apply(self, candidates: tuple[PublicOpticalProductV2, ...]) -> tuple[tuple[PublicOpticalProductV2, ...], dict[str, Any]]:
        """The ``candidates`` that match, and the ``focal_filter`` a response carries: the query (decimal strings),
        ``FOCAL_QUERY_BASIS`` and how many candidates publish no focal length (``excluded_without_focal_length``)."""

        asked = {name: str(value) for name in ("focal_length_mm", "focal_tolerance_mm") if (value := getattr(self, name)) is not None}
        return tuple(product for product in candidates if self.matches(product)), {
            **asked,
            "focal_min_mm": None if self.focal_min_mm is None else str(self.focal_min_mm),
            "focal_max_mm": None if self.focal_max_mm is None else str(self.focal_max_mm),
            "focal_sign": self.focal_sign,
            "basis": FOCAL_QUERY_BASIS,
            "excluded_without_focal_length": sum(1 for product in candidates if product.specifications.focal_length_mm is None),
        }


def _model_surfaces(model: object) -> tuple:
    """Every surface of a published model (a joined model's: every body's)."""

    if isinstance(model, JoinedBodiesModelV1):
        return model.member_surfaces()
    return tuple(getattr(model, "surfaces", ()))


def _check_joined_media(sku: str, model: JoinedBodiesModelV1, materials: dict[str, PublicMaterialV1]) -> None:
    """The crystal rules of a joined model's bodies (each crystal medium of a body has its optic axis there, only crystals
    have one, every surface bounding a crystal is a plane), its cements (isotropic, their band covering the model's) and its
    approximations (``CRYSTAL_INTERFACE_PER_WAVE`` exactly when a body is a crystal)."""

    crystal_seen = False
    for member in model.members:
        crystals = {m for m in member.model.material_ids() if materials[m].is_crystal}
        crystal_seen = crystal_seen or bool(crystals)
        for axis in member.optic_axes or ():
            if axis.material_id not in crystals:
                raise ValueError(f"{sku}: body {member.member_id}'s optic_axes names {axis.material_id}, which is not a crystal (it "
                                 "states no birefringence)")
        missing = sorted(m for m in crystals if member.optic_axis(m) is None)
        if missing:
            raise ValueError(f"{sku}: body {member.member_id} is made of the crystal {missing[0]} and needs its optic axis in optic_axes")
        curved = [s.surface_id for s in member.model.surfaces if not s.is_plane and {s.medium_front, s.medium_back} & crystals]
        if curved:
            raise ValueError(f"{sku}: body {member.member_id} surface {curved[0]} bounds a crystal and is curved; every face of a "
                             "crystal is a plane")
    for cement_id in model.cement_ids():
        if materials[cement_id].is_crystal:
            raise ValueError(f"{sku}: cement {cement_id} is a crystal; a cement layer is isotropic")
    if crystal_seen != (SimulationApproximation.CRYSTAL_INTERFACE_PER_WAVE in model.approximations):
        raise ValueError(f"{sku}: CRYSTAL_INTERFACE_PER_WAVE is listed exactly when a body is a crystal")


class PublicOpticalCatalogV2(StrictContractModel):
    """The public optical catalog document (schema 3.0, 3.1, 3.2, 3.3, 3.4, 3.5 or 3.6).

    ``schema_version`` is ``3.6`` when the document publishes an unreferenced design material or uses any of the material
    physics features listed by ``catalog_schema_3_6_features``, else ``3.5`` when the document uses what schema 3.5 adds
    (``catalog_schema_3_5_features``: crystal materials
    and ``JOINED_BODIES`` models; a reader of schema 3.4 only cannot read it), else ``3.4`` when it uses the field schema 3.4 adds
    (``catalog_schema_3_4_features``; a reader of schema 3.3 only cannot read it), else ``3.3`` when it uses the field schema 3.3 adds (``catalog_schema_3_3_features``; a reader
    of schema 3.2 only cannot read it), else ``3.2`` when it uses the field schema 3.2 adds (``catalog_schema_3_2_features``; a reader
    of schema 3.1 only cannot read it), else ``3.1`` when it uses any field schema 3.1 adds (``catalog_schema_3_1_features``;
    a reader of schema 3.0 only cannot read it) and ``3.0`` otherwise (``required_catalog_schema_version``); a document may
    also state a later version than the fields it uses need. No value of a public catalog has status ``DECLARED`` (a value a Project's author states for an
    element the Project declares), in any schema version.

    ``materials`` holds every material a product names. A catalog that states schema ``3.6`` may also publish materials
    for optical design that no product uses. ``coatings`` holds exactly the coatings the products use.
    """

    schema_id: Literal["opensci.design_catalog.public_optical_catalog"] = Field(alias="schema")
    schema_version: Literal["3.0", "3.1", "3.2", "3.3", "3.4", "3.5", "3.6"] = PUBLIC_OPTICAL_CATALOG_SCHEMA_VERSION
    catalog_version: CatalogVersion
    mechanical_accessories: MechanicalAccessoryPolicy
    categories_offered: tuple[OpticalCategory, ...]
    materials: tuple[PublicMaterialV1, ...] = ()
    coatings: tuple[PublicCoatingV1, ...] = ()
    products: tuple[PublicOpticalProductV2, ...] = Field(min_length=1)

    _products_by_sku: dict[str, PublicOpticalProductV2] = PrivateAttr(default_factory=dict)
    _materials_by_id: dict[str, PublicMaterialV1] = PrivateAttr(default_factory=dict)
    _coatings_by_id: dict[str, PublicCoatingV1] = PrivateAttr(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _no_declared_values(cls, data: object) -> object:
        found = _declared_status_at(data, "")
        if found is not None:
            raise ValueError(f"a public catalog publishes no DECLARED value (a Project author's statement): {found}")
        return data

    @field_validator("products", mode="before")
    @classmethod
    def _sort_products(cls, value: object) -> object:
        return _sorted_or_unchanged(value, lambda item: str(_raw(item, "optical_sku")))

    @field_validator("materials", mode="before")
    @classmethod
    def _sort_materials(cls, value: object) -> object:
        return _sorted_or_unchanged(value, lambda item: str(_raw(item, "material_id")))

    @field_validator("coatings", mode="before")
    @classmethod
    def _sort_coatings(cls, value: object) -> object:
        return _sorted_or_unchanged(value, lambda item: str(_raw(item, "coating_id")))

    @field_validator("categories_offered", mode="before")
    @classmethod
    def _sort_categories(cls, value: object) -> object:
        return _sorted_or_unchanged(value, str)

    @model_validator(mode="after")
    def _consistent(self) -> PublicOpticalCatalogV2:
        for label, items, key in (("optical_sku", self.products, "optical_sku"), ("material_id", self.materials, "material_id"),
                                  ("coating_id", self.coatings, "coating_id")):
            duplicates = duplicate_keys(items, key)
            if duplicates:
                raise ValueError(f"duplicate {label} in PublicOpticalCatalogV2: {duplicates}")
        if set(self.categories_offered) != {product.category for product in self.products} or len(set(self.categories_offered)) != len(
            self.categories_offered
        ):
            raise ValueError("categories_offered must list exactly the categories of the published products")
        _check_published_text(self.coatings, self.products)
        materials = {m.material_id: m for m in self.materials}
        coatings = {c.coating_id: c for c in self.coatings}
        used_materials: set[str] = set()
        used_coatings: set[str] = set()
        for product in self.products:
            used_materials.update(product.specifications.materials)
            for model in product.simulation_models:
                if isinstance(model, THIN_ELEMENT_MODELS):
                    continue
                media = getattr(model, "media", None) or ()
                sides = [m for s in getattr(model, "surfaces", ()) for m in (getattr(s, "medium_front", None), getattr(s, "medium_back", None))]
                if VACUUM in media or VACUUM in sides:
                    raise ValueError(f"{product.optical_sku}: a published model has no {VACUUM} gap (a Project's declared model may state one)")
                for material_id in model.material_ids():
                    material = materials.get(material_id)
                    if material is None:
                        raise ValueError(f"{product.optical_sku}: material {material_id} is not published")
                    if not model.valid_band_nm.within(material.valid_band_nm):
                        raise ValueError(f"{product.optical_sku}: the model band lies outside material {material_id}'s band")
                    if material.is_crystal and not isinstance(model, JoinedBodiesModelV1):
                        raise ValueError(f"{product.optical_sku}: the crystal {material_id} is a medium of a JOINED_BODIES model, whose "
                                         "bodies state its optic axis")
                if isinstance(model, JoinedBodiesModelV1):
                    _check_joined_media(product.optical_sku, model, materials)
                typical = False
                for coating_id in model.coating_ids():
                    coating = coatings.get(coating_id)
                    if coating is None:
                        raise ValueError(f"{product.optical_sku}: coating {coating_id} is not published")
                    used_coatings.add(coating_id)
                    low, high = coating.span_nm
                    if not (low <= Decimal(model.valid_band_nm.min_nm) and Decimal(model.valid_band_nm.max_nm) <= high):
                        raise ValueError(f"{product.optical_sku}: the model band lies outside coating {coating_id}'s bands")
                    phase_span = coating.phase.span_nm if coating.phase is not None else None
                    if phase_span is not None and not (phase_span[0] <= Decimal(model.valid_band_nm.min_nm)
                                                       and Decimal(model.valid_band_nm.max_nm) <= phase_span[1]):
                        raise ValueError(f"{product.optical_sku}: the model band lies outside coating {coating_id}'s phase table")
                    typical = typical or coating.is_typical
                if typical != (SimulationApproximation.TYPICAL_SPECTRAL_CURVE in model.approximations):
                    raise ValueError(f"{product.optical_sku}: TYPICAL_SPECTRAL_CURVE is listed exactly when a coating of the model is a typical curve")
        # Every product material must be published. The schema 3.6 check below governs unreferenced design materials.
        missing_materials = sorted(used_materials - set(materials))
        if missing_materials:
            raise ValueError(f"a product names a material the catalog does not publish: {', '.join(missing_materials[:10])}")
        if set(coatings) != used_coatings:
            raise ValueError("coatings must be exactly the coatings the products use")
        if self.schema_version == PUBLIC_OPTICAL_CATALOG_SCHEMA_VERSION:
            features = catalog_schema_3_1_features(self.coatings, self.products)
            if features:
                listed = "; ".join(features[:10]) + (f"; and {len(features) - 10} more" if len(features) > 10 else "")
                raise ValueError(f"a catalog that uses schema 3.1 fields states schema_version \"3.1\" ({listed})")
        if self.schema_version not in (PUBLIC_OPTICAL_CATALOG_SCHEMA_VERSION_3_2, PUBLIC_OPTICAL_CATALOG_SCHEMA_VERSION_3_3,
                                       PUBLIC_OPTICAL_CATALOG_SCHEMA_VERSION_3_4, PUBLIC_OPTICAL_CATALOG_SCHEMA_VERSION_3_5,
                                       PUBLIC_OPTICAL_CATALOG_SCHEMA_VERSION_3_6):
            features = catalog_schema_3_2_features(self.coatings, self.products)
            if features:
                listed = "; ".join(features[:10]) + (f"; and {len(features) - 10} more" if len(features) > 10 else "")
                raise ValueError(f"a catalog that uses schema 3.2 fields states schema_version \"3.2\" ({listed})")
        if self.schema_version not in (PUBLIC_OPTICAL_CATALOG_SCHEMA_VERSION_3_3, PUBLIC_OPTICAL_CATALOG_SCHEMA_VERSION_3_4,
                                       PUBLIC_OPTICAL_CATALOG_SCHEMA_VERSION_3_5, PUBLIC_OPTICAL_CATALOG_SCHEMA_VERSION_3_6):
            features = catalog_schema_3_3_features(self.coatings, self.products)
            if features:
                listed = "; ".join(features[:10]) + (f"; and {len(features) - 10} more" if len(features) > 10 else "")
                raise ValueError(f"a catalog that uses schema 3.3 fields states schema_version \"3.3\" ({listed})")
        shaped = [f"{product.optical_sku} surface {surface.surface_id}" for product in self.products
                  for model in product.simulation_models for surface in _model_surfaces(model)
                  if surface.has_clear_shape_or_holes]
        if shaped:
            raise ValueError("clear_shape and holes are declared in Project optical models, not in a published catalog ("
                             + "; ".join(shaped[:10]) + ")")
        scattering = [f"{product.optical_sku} surface {surface.surface_id}" for product in self.products
                      for model in product.simulation_models for surface in _model_surfaces(model)
                      if getattr(surface, "scatter", None) is not None]
        if scattering:
            raise ValueError("surface scatter is declared in Project optical models, not in a published catalog ("
                             + "; ".join(scattering[:10]) + ")")
        if self.schema_version not in (PUBLIC_OPTICAL_CATALOG_SCHEMA_VERSION_3_4, PUBLIC_OPTICAL_CATALOG_SCHEMA_VERSION_3_5,
                                       PUBLIC_OPTICAL_CATALOG_SCHEMA_VERSION_3_6):
            features = catalog_schema_3_4_features(self.coatings, self.products)
            if features:
                listed = "; ".join(features[:10]) + (f"; and {len(features) - 10} more" if len(features) > 10 else "")
                raise ValueError(f"a catalog that uses schema 3.4 fields states schema_version \"3.4\" ({listed})")
        if self.schema_version not in (PUBLIC_OPTICAL_CATALOG_SCHEMA_VERSION_3_5, PUBLIC_OPTICAL_CATALOG_SCHEMA_VERSION_3_6):
            features = catalog_schema_3_5_features(self.coatings, self.products)
            if features:
                listed = "; ".join(features[:10]) + (f"; and {len(features) - 10} more" if len(features) > 10 else "")
                raise ValueError(f"a catalog that uses schema 3.5 fields states schema_version \"3.5\" ({listed})")
        if self.schema_version != PUBLIC_OPTICAL_CATALOG_SCHEMA_VERSION_3_6:
            features = catalog_schema_3_6_features(self.coatings, self.products, self.materials)
            if features:
                listed = "; ".join(features[:10]) + (f"; and {len(features) - 10} more" if len(features) > 10 else "")
                raise ValueError(f"a catalog that publishes design materials states schema_version \"3.6\" ({listed})")
        return self

    def payload(self) -> dict[str, Any]:
        """The canonical JSON document (aliases, ``None`` fields omitted): what a publisher writes and a reader parses."""

        return self.model_dump(mode="json", by_alias=True, exclude_none=True)

    def model_post_init(self, context: Any, /) -> None:
        self._products_by_sku = {product.optical_sku: product for product in self.products}
        self._materials_by_id = {material.material_id: material for material in self.materials}
        self._coatings_by_id = {coating.coating_id: coating for coating in self.coatings}

    def get(self, optical_sku: str) -> PublicOpticalProductV2 | None:
        """The product with this optical identity, or None."""

        return self._products_by_sku.get(optical_sku)

    def material(self, material_id: str) -> PublicMaterialV1 | None:
        return self._materials_by_id.get(material_id)

    def coating(self, coating_id: str) -> PublicCoatingV1 | None:
        return self._coatings_by_id.get(coating_id)

    def select(
        self,
        *,
        category: OpticalCategory | str | None = None,
        wavelength_nm: Decimal | str | None = None,
        form: OpticalForm | str | None = None,
        readiness: CommercialReadiness | str | None = None,
        mounting: OpenSciMounting | str | None = None,
        focal_min_mm: Decimal | str | None = None,
        focal_max_mm: Decimal | str | None = None,
        focal_sign: str | None = None,
    ) -> tuple[PublicOpticalProductV2, ...]:
        """Products of ``category`` / ``form`` (if given) that have a simulation model valid at ``wavelength_nm`` (if given),
        with ``commercial_readiness`` ``readiness``, OpenSci mounting ``mounting`` (``PublicOpticalProductV2.mounting``) and a
        published nominal focal length in ``[focal_min_mm, focal_max_mm]`` of sign ``focal_sign`` (``positive`` / ``negative``;
        ``FocalLengthQuery``) when given."""

        wanted = OpticalCategory(category) if category is not None else None
        wanted_form = OpticalForm(form) if form is not None else None
        wanted_readiness = CommercialReadiness(readiness) if readiness is not None else None
        wanted_mounting = OpenSciMounting(mounting) if mounting is not None else None
        wavelength = parse_query_decimal(wavelength_nm, "wavelength_nm") if wavelength_nm is not None else None
        focal = FocalLengthQuery.of(focal_min_mm=focal_min_mm, focal_max_mm=focal_max_mm, focal_sign=focal_sign)
        return tuple(
            product
            for product in self.products
            if (wanted is None or product.category is wanted)
            and (wanted_form is None or product.form is wanted_form)
            and (wanted_readiness is None or product.commercial_readiness is wanted_readiness)
            and (wanted_mounting is None or product.mounting is wanted_mounting)
            and (wavelength is None or any(model.valid_band_nm.contains(wavelength) for model in product.simulation_models))
            and (focal is None or focal.matches(product))
        )

    def select_by_focal_length(self, focal: FocalLengthQuery | None, **filters: Any) -> tuple[tuple[PublicOpticalProductV2, ...], dict[str, Any] | None]:
        """``select(**filters)`` narrowed by ``focal``, with the response's ``focal_filter`` (``FocalLengthQuery.apply``;
        ``None`` without a query)."""

        candidates = self.select(**filters)
        return (candidates, None) if focal is None else focal.apply(candidates)
