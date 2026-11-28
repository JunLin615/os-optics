"""The format-neutral intermediate representation (IR) of a sequential lens prescription.

Every supported sequential prescription is read into this IR,
and only the IR is lowered to the Project's native optical models (``lowering.py``). A format adapter therefore only
reads text: units, sign conventions, element grouping, geometry checks and the native model are the same for every
format.

Conventions (binding for every adapter):

* **Sequential and coaxial.** Surfaces use a common coordinate frame with an initial propagation direction of +Z.
  Reflection reverses the propagation direction. An adapter diagnoses unsupported decentres, tilts and coordinate breaks.
* **Lengths in millimetres.** An adapter converts the file's unit to mm exactly (``to_mm``: 1 in = 25.4 mm, 1 cm = 10 mm,
  1 m = 1000 mm) and keeps every value an exact ``Decimal`` -- never a binary float.
* **Radius sign.** ``radius_mm`` is the signed vertex radius of curvature, positive when the centre of curvature lies
  on the surface's +Z side and negative on its −Z side. A plane has no radius. The sign is converted to the member's
  local frame during lowering.
* **Profile.** ``z(r) = c r^2 / (1 + sqrt(1 - (1 + k) c^2 r^2)) + A4 r^4 + A6 r^6 + ... + a r`` with ``c = 1 / radius``,
  ``k = conic``, ``aspheric_mm = (A4, A6, ...)`` in mm units (``A_2n`` in mm^(1 - 2n)) and ``linear_sag = a``, the
  axicon term (a cone of slope ``dz / dr = a``, dimensionless: it does not scale with the length unit); a cylinder
  (``cylinder_axis`` X or Y: the local axis it is straight along) measures ``r`` across its straight axis and may carry
  a conic, aspheric terms (an acylinder) or an axicon term (a roof). A two-variable sag shape (``sag``: ``IrSagShape``) adds
  to this profile (Zernike, XY polynomial) or replaces it (biconic, toroid).
* **Thickness.** ``thickness_mm`` is the signed displacement of the next vertex in the fixed frame. A reflected
  return leg can travel toward −Z. Dummy and stop vertices can also have a negative spacing on a forward leg.
  The final displacement locates the image. ``Prescription.image_at_infinity`` marks an infinite image conjugate.
* **Material.** ``material`` of a surface is the medium after it (between it and the next surface); ``None`` is air.
  The medium before the first surface and after the last one is air.
* **Apertures.** ``clear_semi_diameter_mm`` is the radius of the surface's clear aperture (required: it is never
  guessed); ``mechanical_semi_diameter_mm`` optionally gives the surface's own mechanical edge (the radius it is edged
  to; the element's outline is the largest of them).
* **Declared ranges.** ``edge_limits`` optionally states what the author expects of the elements: the glass thickness at
  the edge and each surface's mechanical over clear semi-diameter. An element outside them is refused (``lowering.py``).
* **Stop.** ``is_stop`` marks the aperture stop. A stop on a plane with air on both sides is an opaque plate with a
  circular opening of the clear semi-diameter; a stop on a refracting surface is that surface's clear aperture.
* **Coatings.** ``coating`` of a surface names a coating the prescription defines (``coatings``: a polarization-resolved
  or unpolarized spectrum, or an angle-resolved table over wavelength and the angle of incidence in the incident medium,
  optionally with stated reflection / transmission phases) or a published coating id.
* **Object and image.** The object surface is not geometry: its distance (finite, infinite or not stated) is recorded
  only. The image surface is not an element: the last surface's thickness, when an image surface follows, says where
  the image plane is.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Context, Decimal, Inexact, ROUND_HALF_EVEN, localcontext

from .errors import ImportWarning, SourceLocation

#: Nanometres per micrometre for source wavelengths (exact).
NM_PER_UM = Decimal(1000)
#: Millimetres per unit of every length unit a prescription may use (exact).
MM_PER_UNIT: dict[str, Decimal] = {"mm": Decimal(1), "cm": Decimal(10), "m": Decimal(1000), "in": Decimal("25.4")}
#: Spellings accepted for each unit.
UNIT_SPELLINGS: dict[str, str] = {
    "mm": "mm", "millimeter": "mm", "millimeters": "mm", "millimetre": "mm", "millimetres": "mm",
    "cm": "cm", "centimeter": "cm", "centimeters": "cm", "centimetre": "cm", "centimetres": "cm",
    "m": "m", "meter": "m", "meters": "m", "metre": "m", "metres": "m",
    "in": "in", "inch": "in", "inches": "in",
}
#: Significant digits of a converted value that is not an exact decimal (a reciprocal, or an inch-based aspheric
#: coefficient): the relative rounding error is below 5e-28.
CONVERSION_DIGITS = 28
_CONVERSION = Context(prec=CONVERSION_DIGITS, rounding=ROUND_HALF_EVEN)


def to_mm(value: Decimal, unit: str) -> Decimal:
    """A length in ``unit`` as mm (exact: a product of two finite decimals)."""

    return value * MM_PER_UNIT[unit]


def power_to_mm(value: Decimal, unit: str, exponent: int) -> tuple[Decimal, bool]:
    """A coefficient in ``unit ** exponent`` as ``mm ** exponent`` and whether the conversion is exact. Negative exponents
    (curvature: -1, aspheric ``A_2n``: 1 - 2n) divide; a non-terminating quotient is rounded to ``CONVERSION_DIGITS``
    significant digits."""

    factor = MM_PER_UNIT[unit] ** abs(exponent)
    if exponent >= 0:
        return value * factor, True
    context = _CONVERSION.copy()
    context.clear_flags()
    result = context.divide(value, factor)
    return result, not context.flags[Inexact]


#: Air wavelengths are converted to vacuum wavelengths on this grid (nm); a band is rounded inward to ``BAND_QUANTUM_NM``.
VACUUM_WAVELENGTH_QUANTUM_NM = Decimal("0.000001")
#: The arithmetic of the air-to-vacuum conversion (the air model's own: 34 digits).
_AIR_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)


def air_to_vacuum_nm(air_nm: Decimal, temperature_c: Decimal, pressure_pa: Decimal) -> Decimal:
    """The vacuum wavelength (exact decimal, 34 digits) whose wavelength in air of ``temperature_c`` / ``pressure_pa`` is
    ``air_nm``: ``l_vac = l_air * n_air(l_vac)``, the shared air model (``material_physics.air_index_exact``), solved by
    fixed-point iteration (``n_air`` varies slowly). Zero pressure is vacuum: the wavelength is unchanged."""

    from .._vendor.shared_contracts.design_catalog.material_physics import air_index_exact

    if pressure_pa == 0:
        return air_nm
    vacuum = air_nm
    with localcontext(_AIR_CONTEXT):
        for _ in range(6):
            vacuum = air_nm * air_index_exact(vacuum, temperature_c, pressure_pa)
    return vacuum


def reciprocal(value: Decimal) -> tuple[Decimal, bool]:
    """``1 / value`` and whether it is exact (else rounded to ``CONVERSION_DIGITS`` significant digits)."""

    context = _CONVERSION.copy()
    context.clear_flags()
    result = context.divide(Decimal(1), value)
    return result, not context.flags[Inexact]


@dataclass(frozen=True)
class SellmeierDefinition:
    """``n^2 = 1 + sum b_i l^2 / (l^2 - c_i)``, ``l`` the law's wavelength in micrometres (the vacuum wavelength unless the
    definition states ``index_reference`` ``AIR``), ``c_i`` in um^2."""

    b: tuple[Decimal, Decimal, Decimal]
    c_um2: tuple[Decimal, Decimal, Decimal]


@dataclass(frozen=True)
class ConstantIndexDefinition:
    """A refractive index that does not vary with wavelength inside the material's band."""

    refractive_index: Decimal


#: How a material definition states its law (``MaterialDefinition.index_reference``).
LAW_VACUUM = "VACUUM"
LAW_AIR = "AIR"


@dataclass(frozen=True)
class MaterialDefinition:
    """A material the prescription defines itself (its name is the material id: uppercase letters, digits, ``. _ -``).

    ``index_reference``: ``VACUUM`` (the law takes vacuum wavelengths and gives the absolute index; ``min_nm`` /
    ``max_nm`` are vacuum wavelengths) or ``AIR`` (the glass catalogues' convention: the law takes air wavelengths and gives
    the index relative to air, both of the standard reference air 20 C / 101325 Pa; ``min_nm`` / ``max_nm`` are air
    wavelengths -- the Project copy states it as ``IndexReferenceV1`` and its valid band in vacuum wavelengths)."""

    name: str
    dispersion: SellmeierDefinition | ConstantIndexDefinition
    min_nm: Decimal
    max_nm: Decimal
    location: SourceLocation
    absorption_per_mm: Decimal | None = None
    display_name: str | None = None
    basis: str | None = None
    index_reference: str = LAW_VACUUM


@dataclass(frozen=True)
class CoatingNode:
    """One tabulated point of a coating the prescription defines: power fractions at a vacuum wavelength (and, for an
    angle-resolved coating, an angle of incidence in degrees, in the medium the light arrives from), unpolarized
    (``reflectance`` / ``transmittance``) and / or per polarization (``*_s`` / ``*_p``), and optionally the stated phases
    (radians, ``CoatingPhaseV1`` convention) at that wavelength. Values are exact decimals as written."""

    wavelength_nm: Decimal
    location: SourceLocation
    aoi_deg: Decimal | None = None
    reflectance: Decimal | None = None
    transmittance: Decimal | None = None
    reflectance_s: Decimal | None = None
    reflectance_p: Decimal | None = None
    transmittance_s: Decimal | None = None
    transmittance_p: Decimal | None = None
    reflection_s_rad: Decimal | None = None
    reflection_p_rad: Decimal | None = None
    transmission_s_rad: Decimal | None = None
    transmission_p_rad: Decimal | None = None


@dataclass(frozen=True)
class CoatingDefinition:
    """A coating the prescription defines itself (its name is the coating id: uppercase letters, digits, ``. _ -``):
    its ``kind`` (``CoatingKind``), its tabulated ``nodes`` (a spectrum when no node states an angle, else an
    angle-resolved table on a full wavelength x angle grid), the angles it holds for (``min_aoi_deg`` / ``max_aoi_deg``;
    an angle-resolved table defaults to its own angles), how they are read (``aoi_reference``), the status of its data and
    provenance texts."""

    name: str
    kind: str
    nodes: tuple[CoatingNode, ...]
    location: SourceLocation
    min_aoi_deg: Decimal | None = None
    max_aoi_deg: Decimal | None = None
    aoi_reference: str | None = None
    status: str | None = None
    display_name: str | None = None
    basis: str | None = None


@dataclass(frozen=True)
class MaterialRef:
    """A medium named by a surface, resolved by ``glass.py``: ``name`` the glass as written (the designation part of a
    ``MAKER:DESIGNATION`` cell), ``manufacturer`` the maker (or catalogue tag) the prescription states for it, if any, and
    ``explicit_id``: the name is a stable material id (``ID:<material_id>``), never looked up as a designation."""

    name: str
    location: SourceLocation
    manufacturer: str | None = None
    explicit_id: bool = False

    @property
    def key(self) -> str:
        """The resolution key of this reference: one glass per distinct (maker, name, explicit id) as written."""

        if self.explicit_id:
            return f"ID:{self.name}"
        return self.name if self.manufacturer is None else f"{self.manufacturer}:{self.name}"

    @property
    def written(self) -> str:
        return self.key


@dataclass(frozen=True)
class CoatingRef:
    """A coating named by a surface (a published coating id)."""

    coating_id: str
    location: SourceLocation


@dataclass(frozen=True)
class IrSagShape:
    """A two-variable sag shape in mm (the fields of the native ``ZernikeSagV1`` .. ``ToroidalSagV1``): ``terms`` are
    ``(index, value_mm)`` of a Zernike shape or ``(x_power, y_power, value_mm)`` of an XY polynomial; ``y_terms`` are a toroid's
    ``alpha_1, alpha_2, ...`` of ``y^2, y^4, ...`` in ``mm^(1 - 2i)``."""

    kind: str
    indexing: str | None = None
    normalization: str | None = None
    normalization_radius_mm: Decimal | None = None
    terms: tuple[tuple, ...] = ()
    radius_x_mm: Decimal | None = None
    radius_y_mm: Decimal | None = None
    conic_x: Decimal | None = None
    conic_y: Decimal | None = None
    conic: Decimal | None = None
    y_terms: tuple[Decimal, ...] = ()
    radius_of_rotation_mm: Decimal | None = None


@dataclass(frozen=True)
class IrSurface:
    """One real surface (module docstring for every convention)."""

    label: str
    location: SourceLocation
    clear_semi_diameter_mm: Decimal
    radius_mm: Decimal | None = None
    conic: Decimal | None = None
    aspheric_mm: tuple[Decimal, ...] = ()
    linear_sag: Decimal | None = None
    cylinder_axis: str | None = None
    sag: IrSagShape | None = None
    thickness_mm: Decimal | None = None
    material: MaterialRef | None = None
    mechanical_semi_diameter_mm: Decimal | None = None
    is_stop: bool = False
    coating: CoatingRef | None = None
    comment: str | None = None
    is_mirror: bool = False
    hole_radius_mm: Decimal | None = None
    has_clear_aperture: bool = False  # explicit aperture also clips light on an otherwise inert air plane
    automatic_mechanical_edge: bool = False  # a computed body outline can extend beyond the polished profile

    @property
    def is_plane(self) -> bool:
        return self.radius_mm is None and not self.aspheric_mm and self.linear_sag is None and self.sag is None


@dataclass(frozen=True)
class ObjectSpace:
    """The object surface: its distance to the first surface (``None``: not stated) or an object at infinity."""

    location: SourceLocation
    distance_mm: Decimal | None = None
    infinite: bool = False


@dataclass(frozen=True)
class EdgeLimits:
    """The ranges a prescription declares for its own elements (any bound may be absent): the glass thickness at the edge
    (mm) and a surface's mechanical over clear semi-diameter."""

    thickness_min_mm: Decimal | None = None
    thickness_max_mm: Decimal | None = None
    ratio_min: Decimal | None = None
    ratio_max: Decimal | None = None


#: How the wavelengths of a prescription are written (``Prescription.wavelengths_in``).
WAVELENGTHS_VACUUM = "VACUUM"
WAVELENGTHS_AIR = "AIR"


@dataclass(frozen=True)
class ReferenceEnvironment:
    """The environment a prescription states: air at ``temperature_c`` and ``pressure_pa`` (zero pressure: vacuum).
    ``stated``: which of the two the file stated (the other is the default 20 C / 101325 Pa)."""

    temperature_c: Decimal
    pressure_pa: Decimal
    stated: tuple[str, ...] = ()


@dataclass(frozen=True)
class Prescription:
    """A whole sequential prescription in the IR.

    ``surfaces`` are the real surfaces (no object or image surface), in order. ``band_nm`` is the wavelength band the
    prescription states (``None``: derived from its materials). ``image_at_infinity``: the last surface's thickness is
    infinite (an afocal prescription; no image plane). ``warnings`` are what the adapter did that the author
    should know (for example a value rounded by a unit conversion). ``source_sha256`` identifies the exact input text.

    **Wavelengths.** Every wavelength of the IR (``band_nm``, coating nodes) is a *vacuum* wavelength: an adapter whose
    format writes air wavelengths converts them once (``wavelengths_in`` records how the file wrote them, ``AIR``: air of
    ``environment`` at the system temperature and pressure).
    A material definition states its own law convention (``MaterialDefinition.index_reference``). ``environment``: the
    reference environment the prescription states (``None``: it states none; the Project's own environment applies).
    ``glass_catalogs``: the catalogue order the prescription states (maker codes or catalogue names, first preferred),
    which decides a glass name several published records answer to.
    """

    source_format: str
    source_sha256: str
    input_unit: str
    surfaces: tuple[IrSurface, ...]
    name: str | None = None
    object: ObjectSpace | None = None
    image: SourceLocation | None = None
    materials: tuple[MaterialDefinition, ...] = ()
    coatings: tuple[CoatingDefinition, ...] = ()
    band_nm: tuple[Decimal, Decimal] | None = None
    warnings: tuple[ImportWarning, ...] = ()
    image_at_infinity: bool = False
    edge_limits: EdgeLimits | None = None
    wavelengths_in: str = WAVELENGTHS_VACUUM
    environment: ReferenceEnvironment | None = None
    glass_catalogs: tuple[str, ...] = ()
    #: Extra columns not read: ``(section, column as written, reason)``, reason ``DECLARED`` (named by the caller) or
    #: ``BLANK`` (blank in every row).
    ignored_columns: tuple[tuple[str, str, str], ...] = ()
    #: Vacuum wavelengths in source order, with relative weights and a zero-based primary index.
    wavelengths_nm: tuple[Decimal, ...] = ()
    wavelength_weights: tuple[Decimal, ...] = ()
    primary_wavelength_index: int = 0
    field_location: SourceLocation | None = None
    wavelength_location: SourceLocation | None = None
    object_na: Decimal | None = None
    entrance_pupil_diameter_mm: Decimal | None = None
    image_f_number: Decimal | None = None
    image_semi_diameter_mm: Decimal | None = None
    fields: tuple[tuple[Decimal, Decimal], ...] = ()
    field_type: str = "angle"  # field angles in degrees, or object heights in mm for "height"
    field_weights: tuple[Decimal, ...] = ()  # per-field merit weights in source order
    aperture_location: SourceLocation | None = None  # location of the declared system aperture, including float by stop


__all__ = [
    "CONVERSION_DIGITS",
    "LAW_AIR",
    "LAW_VACUUM",
    "MM_PER_UNIT",
    "UNIT_SPELLINGS",
    "WAVELENGTHS_AIR",
    "WAVELENGTHS_VACUUM",
    "VACUUM_WAVELENGTH_QUANTUM_NM",
    "ReferenceEnvironment",
    "air_to_vacuum_nm",
    "CoatingDefinition",
    "CoatingNode",
    "CoatingRef",
    "ConstantIndexDefinition",
    "EdgeLimits",
    "IrSagShape",
    "IrSurface",
    "MaterialDefinition",
    "MaterialRef",
    "ObjectSpace",
    "Prescription",
    "SellmeierDefinition",
    "power_to_mm",
    "reciprocal",
    "to_mm",
]
