"""MaterialOpticalModel (refractive index) and coating model contracts."""

from __future__ import annotations

import math
from typing import Annotated, ClassVar, Literal

from pydantic import Field, SerializerFunctionWrapHandler, field_validator, model_serializer, model_validator

from .._vendor.shared_contracts.design_catalog.material_physics import air_index
from ..config import DEFAULT_NUMERICAL_CONFIG
from .base import Id, OmitAbsentFields, StrictModel, unit_direction

_ENERGY_TOL = DEFAULT_NUMERICAL_CONFIG.energy_conservation_tol  # structural R+T<=1 check


#: The most terms of a Sellmeier law (a constant term is a term with ``C = 0``).
MAX_SELLMEIER_TERMS = 10
#: The most terms of a power series ``n^2 = sum a_i l^e_i`` and the largest magnitude of its integer exponents.
MAX_POWER_SERIES_TERMS = 16
MAX_POWER_SERIES_EXPONENT = 24
#: The constant of the Herzberger formula's ``L = 1 / (l^2 - 0.028)`` (um^2).
HERZBERGER_CONSTANT_UM2 = 0.028
#: The most rows of a spectral bulk-loss table.
MAX_LOSS_ROWS = 4096


class IndexReference(StrictModel):
    """How a material's dispersion law is written (the Engine image of ``design_catalog.material_physics.IndexReferenceV1``).

    The Engine's spectral coordinate is the vacuum wavelength ``l``. ``wavelength``: the law takes the vacuum wavelength
    (``vacuum``) or the wavelength in the source's reference air (``air``: ``l_law = l / n_ref(l)``); ``index``: the law gives
    the absolute index (``absolute``) or the index relative to that air (``relative_to_air``: ``n_abs = n_law n_ref(l)``).
    ``n_ref`` is ``air_index(l, reference_air_temperature_c, reference_air_pressure_pa)`` -- the source's reference air,
    never the medium the lens operates in. ``not_stated``: the source states no convention for it; it is evaluated as
    ``vacuum`` / ``absolute`` (the Engine's convention) and reported as not stated. A law on air wavelengths has its tables
    (index, loss) on ``l_law``; the material's ``valid_wavelength_nm`` stays a band of vacuum wavelengths."""

    wavelength: Literal["vacuum", "air", "not_stated"]
    index: Literal["absolute", "relative_to_air", "not_stated"]
    reference_air_temperature_c: float | None = None
    reference_air_pressure_pa: float | None = Field(None, ge=0)

    @model_validator(mode="after")
    def _air(self) -> IndexReference:
        needs_air = self.wavelength == "air" or self.index == "relative_to_air"
        stated = self.reference_air_temperature_c is not None and self.reference_air_pressure_pa is not None
        if needs_air and not stated:
            raise ValueError("a law on air wavelengths or relative to air states its reference air (temperature and pressure)")
        if not needs_air and (self.reference_air_temperature_c is not None or self.reference_air_pressure_pa is not None):
            raise ValueError("the reference air belongs to a law on air wavelengths or relative to air")
        if self.reference_air_temperature_c is not None and not self.reference_air_temperature_c > -273.15:
            raise ValueError("reference_air_temperature_c must be above absolute zero")
        return self

    @property
    def air_wavelength(self) -> bool:
        return self.wavelength == "air"

    @property
    def relative_to_air(self) -> bool:
        return self.index == "relative_to_air"

    @property
    def stated(self) -> bool:
        """Whether the source states both conventions (else the unstated part is evaluated in the Engine's convention)."""
        return self.wavelength != "not_stated" and self.index != "not_stated"


class ExtinctionCoefficientLoss(StrictModel):
    """Measured extinction coefficient ``k`` at the wavelengths ``wavelength_nm`` (on the law's wavelength ``l_law``):
    ``alpha = 4 pi k / l`` (per mm, ``l`` the queried vacuum wavelength), linear in measured ``k`` on the law's
    wavelength between rows, then converted to alpha; unknown outside them (a single row: at its wavelength only)."""

    kind: Literal["extinction_coefficient"] = "extinction_coefficient"
    wavelength_nm: tuple[float, ...] = Field(min_length=1, max_length=MAX_LOSS_ROWS)
    k: tuple[float, ...] = Field(min_length=1, max_length=MAX_LOSS_ROWS)

    @model_validator(mode="after")
    def _rows(self) -> ExtinctionCoefficientLoss:
        _loss_rows(self.wavelength_nm, self.k, "k")
        if any(not math.isfinite(v) or v < 0 for v in self.k):
            raise ValueError("k cannot be negative")
        return self


class InternalTransmittanceLoss(StrictModel):
    """Measured internal transmittance ``T_i`` (bulk only: no surface reflection, no coating) of a sample of thickness
    ``thickness_mm`` at each wavelength ``wavelength_nm`` (on ``l_law``): ``alpha = -ln(T_i) / d``, linear in ``alpha`` between
    rows, unknown outside them."""

    kind: Literal["internal_transmittance"] = "internal_transmittance"
    wavelength_nm: tuple[float, ...] = Field(min_length=1, max_length=MAX_LOSS_ROWS)
    internal_transmittance: tuple[float, ...] = Field(min_length=1, max_length=MAX_LOSS_ROWS)
    thickness_mm: tuple[float, ...] = Field(min_length=1, max_length=MAX_LOSS_ROWS)

    @model_validator(mode="after")
    def _rows(self) -> InternalTransmittanceLoss:
        _loss_rows(self.wavelength_nm, self.internal_transmittance, "internal_transmittance")
        if len(self.thickness_mm) != len(self.wavelength_nm):
            raise ValueError("thickness_mm must state one thickness per wavelength")
        if any(not 0.0 < t <= 1.0 for t in self.internal_transmittance):
            raise ValueError("internal_transmittance must lie in (0, 1]")
        if any(not d > 0.0 for d in self.thickness_mm):
            raise ValueError("thickness_mm must be positive")
        return self


SpectralLoss = Annotated[ExtinctionCoefficientLoss | InternalTransmittanceLoss, Field(discriminator="kind")]


def _loss_rows(wavelengths: tuple[float, ...], values: tuple[float, ...], label: str) -> None:
    if len(wavelengths) != len(values):
        raise ValueError(f"wavelength_nm and {label} must have equal length")
    if any(not w > 0 for w in wavelengths):
        raise ValueError("wavelength_nm must be positive")
    if any(b <= a for a, b in zip(wavelengths, wavelengths[1:])):
        raise ValueError("wavelength_nm must be strictly increasing")


class SchottThermoOptic(StrictModel):
    """The Schott formula of the change of the absolute index with temperature (the glass catalogues' TD data):
    ``dn_abs = (n^2 - 1) / (2 n) (D0 dT + D1 dT^2 + D2 dT^3 + (E0 dT + E1 dT^2) / (l^2 - l_tk^2))``, ``dT = T - T0``, ``n`` the
    law's index at ``T0`` and ``l`` its wavelength (um, ``l_law``); ``n_abs(T) = n_abs(T0) + dn_abs``. ``valid_temperature_c``:
    the temperatures the source states the coefficients for (a temperature outside them is refused; ``None``: not stated).
    A published coefficient set can place ``lambda_tk_um`` inside the dispersion band; the reference-temperature base index
    remains valid there. An off-reference nonzero E correction at its pole is undefined (never replaced by zero);
    a zero E numerator is removable, so a D-only correction remains valid at the pole."""

    kind: Literal["schott_dn_dt"] = "schott_dn_dt"
    d0: float
    d1: float
    d2: float
    e0: float
    e1: float
    lambda_tk_um: float
    reference_temperature_c: float
    valid_temperature_c: tuple[float, float] | None = None


class _MaterialBase(OmitAbsentFields, StrictModel):
    absent_omitted: ClassVar[tuple[str, ...]] = ("scattering_coefficient_per_mm", "anisotropy_g", "bulk_loss")

    material_id: Id
    valid_wavelength_nm: tuple[float, float] = Field(
        description="Inclusive validity band [min, max] in nm (vacuum wavelength). No extrapolation outside."
    )
    absorption_coefficient_per_mm: float = Field(
        0.0, ge=0, description="Bulk power absorption alpha in P=P0 exp(-alpha L). An omitted value computes as zero for "
        "the geometric upper envelope, but does not state zero loss; an explicitly supplied 0 is a lossless assumption.")
    scattering_coefficient_per_mm: float | None = Field(
        None, ge=0, description="Bulk scattering coefficient mu_s: a traced beam loses exp(-mu_s L) of its power to scatter along "
        "a path L (with absorption: exp(-(alpha + mu_s) L)). The scattered light is booked (BULK_SCATTER) and not followed, so "
        "the power of every plane its source line lights is a lower bound. Omitted: no bulk scattering.")
    anisotropy_g: float | None = Field(
        None, gt=-1, lt=1, description="Mean cosine g of the bulk scattering angle (Henyey-Greenstein anisotropy); stated with "
        "scattering_coefficient_per_mm and reported, as the scattered light is not followed.")
    bulk_loss: SpectralLoss | None = Field(
        None, description="Measured spectral bulk absorption (extinction coefficient k or internal transmittance at a thickness, "
        "on the law's wavelength): alpha(lambda) linear between its rows and unknown outside them -- a path there is traced "
        "without bulk absorption and the run reports that its transmission is not established (BULK_LOSS_NOT_ESTABLISHED). "
        "It replaces absorption_coefficient_per_mm (the two are not stated together).")

    @model_serializer(mode="wrap")
    def _omit_absent_fields(self, handler: SerializerFunctionWrapHandler):
        # Preserve absence through Engine-request JSON round trips: without this,
        # Pydantic's computational default 0 serializes as an explicitly assumed
        # lossless material and a reloaded request silently gains known power.
        data = OmitAbsentFields._omit_absent_fields(self, handler)
        if isinstance(data, dict) and "absorption_coefficient_per_mm" not in self.model_fields_set:
            data.pop("absorption_coefficient_per_mm", None)
        return data

    @model_validator(mode="after")
    def _band(self) -> _MaterialBase:
        lo, hi = self.valid_wavelength_nm
        if not (0 < lo < hi):
            raise ValueError("valid_wavelength_nm must satisfy 0 < min < max")
        if self.anisotropy_g is not None and self.scattering_coefficient_per_mm is None:
            raise ValueError("anisotropy_g describes bulk scattering: state scattering_coefficient_per_mm with it")
        if self.bulk_loss is not None and self.absorption_coefficient_per_mm != 0.0:
            raise ValueError("state bulk_loss (spectral) or absorption_coefficient_per_mm, not both")
        return self


class _IsotropicMaterial(_MaterialBase):
    """The material physics an isotropic material may state with its law: ``index_reference`` (how the law is written;
    ``None``: vacuum wavelength and absolute index), ``thermo_optic`` (the change of the index with temperature) and
    ``temperature_c`` (the material's temperature; ``None``: the temperature of the material library, the environment's,
    20 C by default). A material without thermo data is evaluated as stated, whatever the temperature."""

    absent_omitted: ClassVar[tuple[str, ...]] = ("scattering_coefficient_per_mm", "anisotropy_g", "bulk_loss", "index_reference",
                                                 "thermo_optic", "temperature_c")

    index_reference: IndexReference | None = None
    thermo_optic: SchottThermoOptic | None = None
    temperature_c: float | None = Field(None, gt=-273.15)


class ConstantIndexMaterial(_IsotropicMaterial):
    kind: Literal["constant"] = "constant"
    refractive_index: float = Field(gt=0)


class SellmeierMaterial(_IsotropicMaterial):
    """``n^2 = 1 + sum B_i l^2 / (l^2 - C_i)`` with l in micrometres and C_i in um^2 (1 to ``MAX_SELLMEIER_TERMS`` terms; a
    term with ``C_i = 0`` is a constant term)."""

    kind: Literal["sellmeier"] = "sellmeier"
    b: tuple[float, ...] = Field(min_length=1, max_length=MAX_SELLMEIER_TERMS)
    c_um2: tuple[float, ...] = Field(min_length=1, max_length=MAX_SELLMEIER_TERMS)

    @model_validator(mode="after")
    def _terms(self) -> SellmeierMaterial:
        if len(self.b) != len(self.c_um2):
            raise ValueError("b and c_um2 must have equal length")
        return self


class PowerSeriesMaterial(_IsotropicMaterial):
    """``n^2 = sum_i a_i l^e_i`` (``l`` in micrometres, integer exponents ``e_i``, evaluated by repeated multiplication): the
    Schott power series and refractiveindex.info formula 3 with integer exponents."""

    kind: Literal["power_series"] = "power_series"
    coefficients: tuple[float, ...] = Field(min_length=1, max_length=MAX_POWER_SERIES_TERMS)
    exponents: tuple[int, ...] = Field(min_length=1, max_length=MAX_POWER_SERIES_TERMS)

    @model_validator(mode="after")
    def _terms(self) -> PowerSeriesMaterial:
        if len(self.coefficients) != len(self.exponents):
            raise ValueError("coefficients and exponents must have equal length")
        if any(abs(e) > MAX_POWER_SERIES_EXPONENT for e in self.exponents):
            raise ValueError(f"exponents must lie within +-{MAX_POWER_SERIES_EXPONENT}")
        return self


class HerzbergerMaterial(_IsotropicMaterial):
    """``n = A + B L + C L^2 + D l^2 + E l^4 + F l^6`` with ``L = 1 / (l^2 - 0.028)`` (``l`` in micrometres; AGF formula 3)."""

    kind: Literal["herzberger"] = "herzberger"
    coefficients: tuple[float, float, float, float, float, float]


class TemperatureSellmeierMaterial(_IsotropicMaterial):
    """A law of index and temperature together (NASA CHARMS form): ``n^2 - 1 = sum_i S_i(T) l^2 / (l^2 - l_i(T)^2)`` with
    ``S_i(T) = sum_j s[i][j] T^j`` and ``l_i(T) = sum_j p[i][j] T^j`` (``T`` in kelvin = ``temperature_c + 273.15``, ``l`` and
    ``l_i`` in um), evaluated at the material's temperature and refused outside ``temperature_k_range``. A temperature
    inside that range may still move a fitted pole into the optical band: such a local singularity is not a supported
    index; the published coefficients and ordinary-temperature evaluations are retained."""

    kind: Literal["temperature_sellmeier"] = "temperature_sellmeier"
    s_coefficients: tuple[tuple[float, ...], ...] = Field(min_length=1, max_length=MAX_SELLMEIER_TERMS)
    pole_coefficients_um: tuple[tuple[float, ...], ...] = Field(min_length=1, max_length=MAX_SELLMEIER_TERMS)
    temperature_k_range: tuple[float, float]
    reference_temperature_k: float

    @model_validator(mode="after")
    def _shape(self) -> TemperatureSellmeierMaterial:
        if len(self.s_coefficients) != len(self.pole_coefficients_um):
            raise ValueError("s_coefficients and pole_coefficients_um must have one row per term")
        if any(not 1 <= len(row) <= 8 for row in (*self.s_coefficients, *self.pole_coefficients_um)):
            raise ValueError("each temperature polynomial has 1 to 8 coefficients")
        low, high = self.temperature_k_range
        if not 0 < low < high:
            raise ValueError("temperature_k_range must satisfy 0 < min < max")
        if not low <= self.reference_temperature_k <= high:
            raise ValueError("reference_temperature_k must lie in temperature_k_range")
        if self.thermo_optic is not None:
            raise ValueError("a temperature law states the index at every temperature: omit thermo_optic")
        return self


class AirMaterial(_MaterialBase):
    """Air at ``temperature_c`` and ``pressure_pa``: its absolute index by the shared air formula
    (``design_catalog.material_physics.air_index``: Edlen 1953 as OpticStudio scales it, ``+ - * /`` only; exactly 1 at zero
    pressure). The native lowering states the Project's environment as the Engine material ``air`` of this kind."""

    kind: Literal["air"] = "air"
    temperature_c: float = Field(20.0, gt=-273.15)
    pressure_pa: float = Field(101325.0, ge=0)


class TabulatedIndexMaterial(_IsotropicMaterial):
    """A tabulated index (on the law's wavelength), PCHIP or linear, never extrapolated. ``origin``: ``source_formula`` (the
    meaning when absent: the table samples a source formula) or ``measured`` (the source's own measured index: its derivatives
    are not established by it -- no group-delay dispersion is claimed from it, ``optics/materials/group.py``)."""

    absent_omitted: ClassVar[tuple[str, ...]] = (*_IsotropicMaterial.absent_omitted, "origin")

    kind: Literal["tabulated"] = "tabulated"
    wavelength_nm: tuple[float, ...] = Field(min_length=2)
    refractive_index: tuple[float, ...] = Field(min_length=2)
    interpolation: Literal["pchip", "linear"] = "pchip"
    origin: Literal["source_formula", "measured"] | None = None

    @property
    def measured(self) -> bool:
        return self.origin == "measured"

    @model_validator(mode="after")
    def _table(self) -> TabulatedIndexMaterial:
        if len(self.wavelength_nm) != len(self.refractive_index):
            raise ValueError("wavelength_nm and refractive_index must have equal length")
        if any(b <= a for a, b in zip(self.wavelength_nm, self.wavelength_nm[1:])):
            raise ValueError("wavelength_nm must be strictly increasing")
        if any(n <= 0 for n in self.refractive_index):
            raise ValueError("refractive_index must be positive")
        lo, hi = (law_wavelength_nm(self.index_reference, w) for w in self.valid_wavelength_nm)
        if lo < self.wavelength_nm[0] or hi > self.wavelength_nm[-1]:
            raise ValueError("valid_wavelength_nm must lie inside the tabulated wavelength range (on the law's wavelength)")
        return self


class SourceTableIndexMaterial(TabulatedIndexMaterial):
    """Phase-index nodes supplied directly by the source. Their slope and curvature are unspecified."""

    kind: Literal["source_table"] = "source_table"
    origin: Literal["source_table"] = "source_table"


def reference_air_index(reference: IndexReference, vacuum_nm: float) -> float:
    """``n_ref`` of a law's reference air at a vacuum wavelength (``air_index``)."""

    return air_index(vacuum_nm, reference.reference_air_temperature_c, reference.reference_air_pressure_pa)  # type: ignore[arg-type]


def law_wavelength_nm(reference: IndexReference | None, vacuum_nm: float) -> float:
    """``l_law`` of a vacuum wavelength: ``l / n_ref(l)`` for a law on air wavelengths, else ``l``."""

    if reference is None or not reference.air_wavelength:
        return vacuum_nm
    return vacuum_nm / reference_air_index(reference, vacuum_nm)


class ConstantIndexLaw(StrictModel):
    """One refractive index of a crystal, the same at every wavelength of the material's band."""

    kind: Literal["constant"] = "constant"
    refractive_index: float = Field(gt=0)


class SellmeierIndexLaw(StrictModel):
    """One refractive index of a crystal: ``n^2 = 1 + sum B_i l^2 / (l^2 - C_i)`` with l in micrometres and C_i in um^2."""

    kind: Literal["sellmeier"] = "sellmeier"
    b: tuple[float, ...] = Field(min_length=1, max_length=MAX_SELLMEIER_TERMS)
    c_um2: tuple[float, ...] = Field(min_length=1, max_length=MAX_SELLMEIER_TERMS)

    @model_validator(mode="after")
    def _terms(self) -> SellmeierIndexLaw:
        if len(self.b) != len(self.c_um2):
            raise ValueError("b and c_um2 must have equal length")
        return self


class TabulatedIndexLaw(StrictModel):
    """One refractive index of a crystal, tabulated over the vacuum wavelength (PCHIP or linear, never extrapolated)."""

    kind: Literal["tabulated"] = "tabulated"
    wavelength_nm: tuple[float, ...] = Field(min_length=2)
    refractive_index: tuple[float, ...] = Field(min_length=2)
    interpolation: Literal["pchip", "linear"] = "pchip"

    @model_validator(mode="after")
    def _table(self) -> TabulatedIndexLaw:
        if len(self.wavelength_nm) != len(self.refractive_index):
            raise ValueError("wavelength_nm and refractive_index must have equal length")
        if any(b <= a for a, b in zip(self.wavelength_nm, self.wavelength_nm[1:])):
            raise ValueError("wavelength_nm must be strictly increasing")
        if any(n <= 0 for n in self.refractive_index):
            raise ValueError("refractive_index must be positive")
        return self


IndexLaw = Annotated[ConstantIndexLaw | SellmeierIndexLaw | TabulatedIndexLaw, Field(discriminator="kind")]


class ThermoOpticRow(StrictModel):
    """The temperature coefficients of a crystal's two indices at one vacuum wavelength (per kelvin)."""

    wavelength_nm: float = Field(gt=0)
    dno_dT_per_K: float = Field(description="d n_o / dT (1/K) at this wavelength.")
    dne_dT_per_K: float = Field(description="d n_e / dT (1/K) at this wavelength.")


#: The most rows of a crystal's thermo-optic table.
MAX_THERMO_OPTIC_ROWS = 64


class CrystalTemperature(StrictModel):
    """A crystal at ``temperature_c`` whose index laws hold at ``reference_temperature_c``: each index is
    ``n(lambda, T) = n(lambda, T_ref) + dn/dT(lambda) (T - T_ref)``, ``dn/dT`` linear in wavelength between the rows of
    ``thermo_optic`` (one row: the same at every wavelength of the band). A wavelength outside the rows' span (two rows or
    more) has no stated temperature coefficient: the index is not evaluated there. ``thermal_expansion_per_K`` stretches a
    quasi-phase-matching grating's period, ``Lambda(T) = Lambda (1 + alpha (T - T_ref))``."""

    temperature_c: float
    reference_temperature_c: float
    thermo_optic: tuple[ThermoOpticRow, ...] = Field(min_length=1, max_length=MAX_THERMO_OPTIC_ROWS)
    thermal_expansion_per_K: float = 0.0

    @model_validator(mode="after")
    def _rows(self) -> CrystalTemperature:
        waves = [row.wavelength_nm for row in self.thermo_optic]
        if any(b <= a for a, b in zip(waves, waves[1:])):
            raise ValueError("thermo_optic rows must be ordered by strictly increasing wavelength_nm")
        return self


#: The most order of a quasi-phase-matching grating (odd orders only: a 50 % duty cycle has no even ones).
MAX_QPM_ORDER = 15


class SecondHarmonicGeneration(StrictModel):
    """The second-order nonlinearity of a crystal as one element uses it for second-harmonic generation.

    ``fundamental_waves``: the crystal waves the two fundamental photons travel as (``oo``, ``ee``: type I, or type 0 under
    quasi-phase matching; ``oe``: type II); ``harmonic_wave``: the wave the harmonic is generated in. ``d_eff_pm_per_V`` states
    the effective coefficient of this interaction, or ``d_tensor_pm_per_V`` the contracted 3 x 6 tensor ``d_il`` (columns xx,
    yy, zz, yz, xz, xy) in the crystal frame whose Z is the optic axis and X ``crystal_x_axis`` (in the frame of the optic
    axis), and the run contracts it with the traced waves' electric fields. ``qpm_period_um`` (order ``qpm_order``, 50 % duty
    cycle): a periodically poled crystal whose grating vector lies along the face normal; the stated coefficient is then the
    unpoled crystal's (``d_eff_pm_per_V``: for example ``d33`` of LiNbO3 for e + e -> e, not the reduced ``2 d33 / pi``; the
    tensor as it is) and the run multiplies it by ``2 / (m pi)`` itself. The crystal is uniaxial: biaxial (KTP, LBO, ...) and
    cubic crystals are not modelled, and no crystal data are built in (the coefficients, indices and thermo-optic data are
    stated). ``focusing``: ``near_field`` takes the beam radius as constant along the crystal (thin crystal);
    ``boyd_kleinman`` follows the Gaussian beam's focusing through it (one fundamental wave only)."""

    fundamental_waves: Literal["oo", "ee", "oe"]
    harmonic_wave: Literal["o", "e"]
    d_eff_pm_per_V: float | None = None
    d_tensor_pm_per_V: tuple[tuple[float, float, float, float, float, float],
                             tuple[float, float, float, float, float, float],
                             tuple[float, float, float, float, float, float]] | None = None
    crystal_x_axis: tuple[float, float, float] | None = None
    qpm_period_um: float | None = Field(None, gt=0)
    qpm_order: int = Field(1, strict=True, ge=1, le=MAX_QPM_ORDER)
    focusing: Literal["near_field", "boyd_kleinman"] = "near_field"

    @field_validator("crystal_x_axis")
    @classmethod
    def _x_axis(cls, v: tuple[float, float, float] | None) -> tuple[float, float, float] | None:
        return None if v is None else unit_direction(v, _AXIS_NORM_TOL, "crystal_x_axis")  # type: ignore[return-value]

    @model_validator(mode="after")
    def _consistent(self) -> SecondHarmonicGeneration:
        if (self.d_eff_pm_per_V is None) == (self.d_tensor_pm_per_V is None):
            raise ValueError("state exactly one of d_eff_pm_per_V and d_tensor_pm_per_V")
        if (self.d_tensor_pm_per_V is None) != (self.crystal_x_axis is None):
            raise ValueError("crystal_x_axis is stated with d_tensor_pm_per_V and only with it")
        if self.qpm_order % 2 == 0:
            raise ValueError("qpm_order must be odd (a 50 % duty-cycle grating has no even orders)")
        if self.focusing == "boyd_kleinman" and self.fundamental_waves == "oe":
            raise ValueError("boyd_kleinman focusing takes both fundamental photons in one wave (oo or ee)")
        return self


class UniaxialMaterial(_MaterialBase):
    """A uniaxial birefringent crystal (calcite, quartz without its optical activity, YVO4, alpha-BBO, ...): the ordinary index
    ``n_o`` for light polarized perpendicular to the optic axis and the extraordinary index ``n_e`` for light polarized along it,
    each with its own dispersion law, and the direction of the optic axis.

    Light entering the crystal splits into an ordinary wave (index ``n_o``, ray along its wave normal) and an extraordinary wave
    (index ``n(theta)`` with ``1/n(theta)^2 = cos^2(theta)/n_o^2 + sin^2(theta)/n_e^2``, ``theta`` the angle between its wave normal
    and the optic axis), whose ray (energy) direction walks off its wave normal by ``rho``,
    ``tan(rho) = (n_o^2 - n_e^2) sin(theta) cos(theta) / (n_e^2 cos^2(theta) + n_o^2 sin^2(theta))`` (away from the axis for
    ``n_e < n_o``). Every face of the crystal is a plane that refracts (``optics/polarization/uniaxial.py`` states the model).
    Biaxial crystals and optical activity are outside this model."""

    absent_omitted: ClassVar[tuple[str, ...]] = ("scattering_coefficient_per_mm", "anisotropy_g", "bulk_loss", "temperature", "nonlinear")

    kind: Literal["uniaxial"] = "uniaxial"
    ordinary: IndexLaw = Field(description="The ordinary index n_o (light polarized perpendicular to the optic axis).")
    extraordinary: IndexLaw = Field(description="The extraordinary index n_e (light polarized along the optic axis).")
    optic_axis: tuple[float, float, float] = Field(
        description="Unit direction of the optic axis (its sign does not matter), in the frame of optic_axis_component.")
    optic_axis_component: Id | None = Field(
        None, description="The component whose frame optic_axis is given in (it turns with that component's pose); None: the "
        "scene frame.")
    temperature: CrystalTemperature | None = Field(
        None, description="The crystal's temperature and thermo-optic coefficients; None: the index laws as stated.")
    nonlinear: SecondHarmonicGeneration | None = Field(
        None, description="The crystal generates the second harmonic of light entering it through a face; None: linear.")

    @field_validator("optic_axis")
    @classmethod
    def _axis(cls, v: tuple[float, float, float]) -> tuple[float, float, float]:
        return unit_direction(v, _AXIS_NORM_TOL, "optic_axis")  # type: ignore[return-value]

    @model_validator(mode="after")
    def _crystal(self) -> UniaxialMaterial:
        lo, hi = self.valid_wavelength_nm
        for law in (self.ordinary, self.extraordinary):
            if isinstance(law, TabulatedIndexLaw) and (lo < law.wavelength_nm[0] or hi > law.wavelength_nm[-1]):
                raise ValueError("valid_wavelength_nm must lie inside each tabulated index's wavelength range")
        if self.nonlinear is not None and self.nonlinear.crystal_x_axis is not None:
            dot = sum(a * b for a, b in zip(self.nonlinear.crystal_x_axis, self.optic_axis))
            if abs(dot) > _AXIS_NORM_TOL:
                raise ValueError("nonlinear.crystal_x_axis must be perpendicular to optic_axis")
        return self


#: How far from 1 the squared length of a stated axis may be for its components to be used unchanged (a unit vector written
#: with a few decimals); a longer or shorter axis is normalized.
_AXIS_NORM_TOL = 1e-6


class ConstantGradient(StrictModel):
    """A radial gradient constant ``g`` (1/mm), the same at every wavelength of the material's band."""

    kind: Literal["constant"] = "constant"
    per_mm: float = Field(gt=0)


class TabulatedGradient(StrictModel):
    """A radial gradient constant ``g`` (1/mm) per vacuum wavelength, linear between rows, never extrapolated."""

    kind: Literal["tabulated"] = "tabulated"
    wavelength_nm: tuple[float, ...] = Field(min_length=2)
    per_mm: tuple[float, ...] = Field(min_length=2)

    @model_validator(mode="after")
    def _table(self) -> TabulatedGradient:
        if len(self.wavelength_nm) != len(self.per_mm):
            raise ValueError("wavelength_nm and per_mm must have equal length")
        if any(b <= a for a, b in zip(self.wavelength_nm, self.wavelength_nm[1:])):
            raise ValueError("wavelength_nm must be strictly increasing")
        if any(g <= 0 for g in self.per_mm):
            raise ValueError("per_mm must be positive")
        return self


GradientLaw = Annotated[ConstantGradient | TabulatedGradient, Field(discriminator="kind")]


class GradientIndexMaterial(_MaterialBase):
    """A radial gradient-index medium (a GRIN rod lens): ``n(r)^2 = n0^2 (1 - (g r)^2)`` with ``n0`` the index on the axis
    (``base``) and ``g`` the gradient constant (``gradient``), ``r`` the distance from the axis through ``axis_point_mm`` along
    ``axis_direction`` (in the frame of ``axis_component``), out to ``radius_mm`` (the rod's side).

    In this profile a ray's transverse coordinates are exactly ``x(z) = x0 cos(W z) + (x0' / W) sin(W z)`` along the axis, with
    ``W = n0 g / beta`` and the ray invariant ``beta = n dz/ds`` (Marchand, *Gradient Index Optics*, 1978, ch. 3); the optical
    path is ``(n0^2 / beta) (L - g^2 int r^2 dz)``. The medium is traced between two plane faces perpendicular to its axis
    (``optics/rays/gradient.py``); a ray reaching ``radius_mm`` leaves through the rod's side."""

    kind: Literal["radial_gradient"] = "radial_gradient"
    base: IndexLaw = Field(description="The index n0 on the axis.")
    gradient: GradientLaw = Field(description="The gradient constant g (1/mm) of n(r)^2 = n0^2 (1 - (g r)^2).")
    axis_point_mm: tuple[float, float, float] = Field(description="A point of the profile's axis, in the frame of axis_component.")
    axis_direction: tuple[float, float, float] = Field(description="Unit direction of the axis (its sign does not matter).")
    axis_component: Id | None = Field(None, description="The component whose frame the axis is given in; None: the scene frame.")
    radius_mm: float = Field(gt=0, description="The rod's radius: the profile holds from the axis out to it, and a ray reaching it "
                             "leaves through the rod's side.")

    @field_validator("axis_direction")
    @classmethod
    def _axis(cls, v: tuple[float, float, float]) -> tuple[float, float, float]:
        return unit_direction(v, _AXIS_NORM_TOL, "axis_direction")  # type: ignore[return-value]

    @model_validator(mode="after")
    def _gradient(self) -> GradientIndexMaterial:
        lo, hi = self.valid_wavelength_nm
        if isinstance(self.base, TabulatedIndexLaw) and (lo < self.base.wavelength_nm[0] or hi > self.base.wavelength_nm[-1]):
            raise ValueError("valid_wavelength_nm must lie inside the base index's wavelength range")
        if isinstance(self.gradient, TabulatedGradient):
            if lo < self.gradient.wavelength_nm[0] or hi > self.gradient.wavelength_nm[-1]:
                raise ValueError("valid_wavelength_nm must lie inside the gradient's wavelength range")
            largest = max(self.gradient.per_mm)
        else:
            largest = self.gradient.per_mm
        if largest * self.radius_mm >= 1.0:
            raise ValueError("g * radius_mm must stay below 1 (the index is real out to the rod's side)")
        return self


#: The most polynomial terms of an axial gradient (``a1 z``, ``a2 z^2``, ``a3 z^3``).
MAX_AXIAL_TERMS = 3


class AxialGradientIndexMaterial(_MaterialBase):
    """An axial gradient-index medium: ``n(z) = n0 + a1 z + a2 z^2 + a3 z^3``, ``z`` (mm) the position along the axis through
    ``axis_point_mm`` (``z = 0`` there) in the direction ``axis_direction`` (in the frame of ``axis_component``), the same at
    every distance from the axis out to ``radius_mm`` (the rod's side); ``n0`` the index law ``base`` (its dispersion) and
    ``coefficients_per_mm`` the terms ``a1`` (1/mm), ``a2`` (1/mm^2), ``a3`` (1/mm^3), the same at every wavelength.

    The index does not vary across the axis, so a ray keeps its transverse optical direction ``k = n t_perp`` and lies in
    the plane of the axis and ``k``: ``x(z) = x0 + k_x int dz / sqrt(n^2 - k^2)``, its optical path
    ``int n^2 / sqrt(n^2 - k^2) dz`` (``optics/rays/gradient.py``, Gauss-Legendre quadrature of smooth integrands)."""

    kind: Literal["axial_gradient"] = "axial_gradient"
    base: IndexLaw = Field(description="The index n0 at the axis point (z = 0).")
    coefficients_per_mm: tuple[float, ...] = Field(min_length=1, max_length=MAX_AXIAL_TERMS, description=(
        "The terms a1 (1/mm), a2 (1/mm^2), a3 (1/mm^3) of n(z) = n0 + a1 z + a2 z^2 + a3 z^3."))
    axis_point_mm: tuple[float, float, float] = Field(description="The point z = 0 of the axis, in the frame of axis_component.")
    axis_direction: tuple[float, float, float] = Field(description="Unit direction of increasing z.")
    axis_component: Id | None = Field(None, description="The component whose frame the axis is given in; None: the scene frame.")
    radius_mm: float = Field(gt=0, description="The rod's radius: a ray reaching it leaves through the rod's side.")

    @field_validator("axis_direction")
    @classmethod
    def _axis(cls, v: tuple[float, float, float]) -> tuple[float, float, float]:
        return unit_direction(v, _AXIS_NORM_TOL, "axis_direction")  # type: ignore[return-value]

    @model_validator(mode="after")
    def _axial(self) -> AxialGradientIndexMaterial:
        if not all(math.isfinite(a) for a in self.coefficients_per_mm) or not any(a != 0.0 for a in self.coefficients_per_mm):
            raise ValueError("coefficients_per_mm must be finite and not all zero (a medium without a gradient is homogeneous)")
        lo, hi = self.valid_wavelength_nm
        if isinstance(self.base, TabulatedIndexLaw) and (lo < self.base.wavelength_nm[0] or hi > self.base.wavelength_nm[-1]):
            raise ValueError("valid_wavelength_nm must lie inside the base index's wavelength range")
        return self


#: The gradient-index media: their index varies within them (``optics/rays/gradient.py``).
GRADIENT_MEDIA = (GradientIndexMaterial, AxialGradientIndexMaterial)


MaterialOpticalModel = Annotated[
    ConstantIndexMaterial | SellmeierMaterial | TabulatedIndexMaterial | SourceTableIndexMaterial | UniaxialMaterial | GradientIndexMaterial
    | AxialGradientIndexMaterial | PowerSeriesMaterial | HerzbergerMaterial | TemperatureSellmeierMaterial | AirMaterial,
    Field(discriminator="kind"),
]
#: The isotropic materials with one dispersion law (what ``optics/materials/index.py`` evaluates directly).
ISOTROPIC_MEDIA = (ConstantIndexMaterial, SellmeierMaterial, TabulatedIndexMaterial, PowerSeriesMaterial, HerzbergerMaterial,
                   TemperatureSellmeierMaterial, AirMaterial)


#: What a coating's ``group_delay_bound_fs`` states (the same for both coating kinds).
GROUP_DELAY_BOUND_DESCRIPTION = (
    "The largest magnitude (fs) of the coating's group delay d phi / d omega, in reflection and in transmission, for s and p, "
    "wherever its data hold: a reflection counted from a reflection at the surface, a transmission from crossing it. Two "
    "beams whose paths meet such coatings differently then have a relative group delay within the sum of the bounds that "
    "differ between them (optics/interference/coherence.py). None: the coating's group delay is not stated.")


class DeclaredCoatingValidity(StrictModel):
    """The angle range and the wavelengths a published coating record states its values for, carried into the lowered coating.
    It reads no coefficient: the tracer and a coating's own data range stay as they are. The exposure bound evaluates the left-out
    light against it (``validation/coating_use.py``)."""

    record_id: str = Field(min_length=1, max_length=256, description="The record's coating id.")
    aoi_reference: Literal["AIR_EQUIVALENT", "INCIDENT_MEDIUM"] = Field(
        description="The medium the range refers to: the angle in air with the same n sin(theta), or the angle in the medium the light arrives from.")
    min_deg: float = Field(ge=0, le=90)
    max_deg: float = Field(ge=0, le=90)
    slack_deg: float = Field(0.0, ge=0, description="The allowance around the range: the coating kind's tolerance of a single design angle, the rounding allowance of a range.")
    wavelength_bands_nm: tuple[tuple[float, float], ...] = Field(
        (), description="The wavelengths (nm) the record states values at, each band with the tolerance a single wavelength is matched within. Empty: not stated.")

    @model_validator(mode="after")
    def _ordered(self) -> DeclaredCoatingValidity:
        if self.min_deg > self.max_deg:
            raise ValueError("min_deg must not exceed max_deg")
        return self


#: What a coating's ``declared_validity`` states (the same for both coating kinds).
DECLARED_VALIDITY_DESCRIPTION = (
    "The angle range and wavelengths the coating's published record states, with its reference medium; None: a coating without a record "
    "(its own data range is all it states).")


class ConstantCoating(OmitAbsentFields, StrictModel):
    """Power coefficients independent of wavelength / angle within a declared validity band."""

    absent_omitted: ClassVar[tuple[str, ...]] = ("group_delay_bound_fs", "declared_validity")

    kind: Literal["constant"] = "constant"
    reflectance_s: float = Field(0.0, ge=0, le=1)
    reflectance_p: float = Field(0.0, ge=0, le=1)
    transmittance_s: float = Field(1.0, ge=0, le=1)
    transmittance_p: float = Field(1.0, ge=0, le=1)
    valid_wavelength_nm: tuple[float, float] = (100.0, 20000.0)
    max_aoi_deg: float = Field(90.0, gt=0, le=90)
    group_delay_bound_fs: float | None = Field(None, gt=0, description=GROUP_DELAY_BOUND_DESCRIPTION)
    declared_validity: DeclaredCoatingValidity | None = Field(None, description=DECLARED_VALIDITY_DESCRIPTION)

    @model_validator(mode="after")
    def _energy(self) -> ConstantCoating:
        if self.reflectance_s + self.transmittance_s > 1.0 + _ENERGY_TOL or self.reflectance_p + self.transmittance_p > 1.0 + _ENERGY_TOL:
            raise ValueError("R + T must not exceed 1 for either polarization")
        lo, hi = self.valid_wavelength_nm
        if not (0 < lo < hi):
            raise ValueError("valid_wavelength_nm must satisfy 0 < min < max")
        return self


class TabulatedCoating(OmitAbsentFields, StrictModel):
    """R/T on a regular (wavelength, AOI) grid, bilinear interpolation, no extrapolation.

    ``values[name][i][j]`` with ``i`` indexing ``wavelength_nm`` and ``j`` indexing ``aoi_deg``.
    """

    absent_omitted: ClassVar[tuple[str, ...]] = ("group_delay_bound_fs", "declared_validity")

    kind: Literal["tabulated"] = "tabulated"
    wavelength_nm: tuple[float, ...] = Field(min_length=2)
    aoi_deg: tuple[float, ...] = Field(min_length=2)
    reflectance_s: tuple[tuple[float, ...], ...]
    reflectance_p: tuple[tuple[float, ...], ...]
    transmittance_s: tuple[tuple[float, ...], ...]
    transmittance_p: tuple[tuple[float, ...], ...]
    group_delay_bound_fs: float | None = Field(None, gt=0, description=GROUP_DELAY_BOUND_DESCRIPTION)
    declared_validity: DeclaredCoatingValidity | None = Field(None, description=DECLARED_VALIDITY_DESCRIPTION)

    @model_validator(mode="after")
    def _grid(self) -> TabulatedCoating:
        nw, na = len(self.wavelength_nm), len(self.aoi_deg)
        if any(b <= a for a, b in zip(self.wavelength_nm, self.wavelength_nm[1:])):
            raise ValueError("wavelength_nm must be strictly increasing")
        if any(b <= a for a, b in zip(self.aoi_deg, self.aoi_deg[1:])):
            raise ValueError("aoi_deg must be strictly increasing")
        if self.aoi_deg[0] < 0 or self.aoi_deg[-1] > 90:
            raise ValueError("aoi_deg must lie within [0, 90]")
        tables = (self.reflectance_s, self.reflectance_p, self.transmittance_s, self.transmittance_p)
        for tab in tables:
            if len(tab) != nw or any(len(row) != na for row in tab):
                raise ValueError("coating tables must have shape (len(wavelength_nm), len(aoi_deg))")
            if any(not (0.0 <= v <= 1.0) for row in tab for v in row):
                raise ValueError("coating values must lie in [0, 1]")
        for i in range(nw):
            for j in range(na):
                if self.reflectance_s[i][j] + self.transmittance_s[i][j] > 1.0 + _ENERGY_TOL:
                    raise ValueError("R_s + T_s must not exceed 1")
                if self.reflectance_p[i][j] + self.transmittance_p[i][j] > 1.0 + _ENERGY_TOL:
                    raise ValueError("R_p + T_p must not exceed 1")
        return self


CoatingModel = Annotated[ConstantCoating | TabulatedCoating, Field(discriminator="kind")]
