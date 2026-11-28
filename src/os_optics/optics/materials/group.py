"""Group refractive index ``n_g = n - lambda dn/dlambda`` of a material model, from the same dispersion the Engine traces with,
and its group-delay dispersion per unit length (``lambda`` the vacuum wavelength).

The group-delay dispersion of a path of length ``L`` is ``L k2`` with ``k2 = lambda^3 / (2 pi) d^2n/dlambda^2`` (mm^2 per mm of
path): the second derivative of the phase ``k0 n L`` over the vacuum wavenumber ``k0`` (``d^2 (k0 n) / dk0^2``); divided by
``c^2`` it is the familiar group-velocity dispersion (N-BK7 at 532 nm: 8.0e-6 mm^2 per mm, 89 fs^2/mm).

Analytic derivatives (``+ - * / sqrt`` only, so CPython and Pyodide give the same bits) for every formula law:

* constant index: ``n_g = n`` and ``k2 = 0`` (no dispersion), unless air enters through its index reference;
* Sellmeier ``f = n^2 = 1 + sum B_i l^2 / (l^2 - C_i)`` (and a temperature Sellmeier law at its temperature):
  ``f' = -2 l sum B_i C_i / (l^2 - C_i)^2``, ``f'' = 2 sum B_i C_i (3 l^2 + C_i) / (l^2 - C_i)^3``;
* power series ``f = sum a_i l^e_i``: ``f' = sum a_i e_i l^(e_i - 1)``, ``f'' = sum a_i e_i (e_i - 1) l^(e_i - 2)``;
  from ``f``: ``n' = f' / (2 n)``, ``n'' = f'' / (2 n) - f'^2 / (4 n^3)``;
* Herzberger ``n = A + B L + C L^2 + D s + E s^2 + F s^3`` (``s = l^2``, ``L = 1 / (s - 0.028)``): ``dn/ds = -B L^2 - 2 C L^3 + D
  + 2 E s + 3 F s^2``, ``d2n/ds2 = 2 B L^3 + 6 C L^4 + 2 E + 6 F s``;
* air (``air_index``): ``air_index_slope``.

The material-evaluation rule's chain (``index.py``): ``l_law = l / n_ref(l)`` has ``l_law' = 1 / n_ref - l n_ref' / n_ref^2``
and ``l_law'' = -2 n_ref' / n_ref^2 - l n_ref'' / n_ref^2 + 2 l n_ref'^2 / n_ref^3``; ``n_law(l) = law(l_law)`` has
``n_law' = law' l_law'`` and ``n_law'' = law'' l_law'^2 + law' l_law''``; ``n_abs = n_law n_ref`` adds ``n_law n_ref'`` and
``2 n_law' n_ref' + n_law n_ref''``; the Schott term ``dn_abs = A(n_law) B(l_law)`` (``A = (n^2 - 1) / (2 n)``) is
differentiated through both.

Tables: a table that samples a source formula is differentiated numerically, as before (a central difference of the
model's own interpolation over ``2 h``, ``h = TABULATED_STEP_NM``, one-sided at a band end; its second difference over
``h``), through the whole rule. Tables with ``origin = measured`` or ``source_table`` establish the phase index at their
nodes. Their group index and group-delay dispersion are unavailable. ``GroupDispersion.limitation`` records the reason.

``None`` outside the material's valid band (never extrapolated).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ...config import NumericalConfig
from ...contracts.materials import (
    HERZBERGER_CONSTANT_UM2,
    AirMaterial,
    ConstantIndexMaterial,
    HerzbergerMaterial,
    MaterialOpticalModel,
    PowerSeriesMaterial,
    SellmeierMaterial,
    TabulatedIndexMaterial,
    TemperatureSellmeierMaterial,
    UniaxialMaterial,
    GRADIENT_MEDIA,
    reference_air_index,
)
from ...units import NM_TO_UM
from .index import (
    build_evaluator,
    integer_power,
    law_material,
    material_temperature,
    temperature_kelvin,
    temperature_sellmeier_terms,
)

#: Per-nm slopes to per-um slopes (first and second derivative), and um to nm.
PER_NM_TO_PER_UM = 1e3
PER_NM2_TO_PER_UM2 = 1e6
UM_TO_NM = 1e3
#: Factors of the second derivatives of the Herzberger law and the Schott term (6 F s, 6 C L^4; E (6 l^2 + 2 l_tk^2) / q^3).
SIX = 6.0
#: Half width (nm) of the difference that gives dn/dlambda of a tabulated index.
TABULATED_STEP_NM = 0.01
#: Why a measured table has no group index or group-delay dispersion.
MEASURED_TABLE_LIMITATION = (
    "MEASURED_TABLE_DERIVATIVE: a measured index table establishes the index at its points, not its slope or curvature: no "
    "group index or group-delay dispersion is claimed from it")


@dataclass(frozen=True)
class GroupDispersion:
    """A material's index at a vacuum wavelength with its group index and group-delay dispersion ``k2`` (mm^2 per mm of path);
    ``n_group`` / ``k2`` are ``None`` with ``limitation`` saying why where they are not established. ``method``: ``analytic``
    (a formula law), ``numeric`` (a table of a source formula), ``none`` (a measured table)."""

    n: float
    n_group: float | None
    k2: float | None
    method: str
    limitation: str | None = None


def _isotropic(model: MaterialOpticalModel) -> MaterialOpticalModel:
    if isinstance(model, UniaxialMaterial):
        return law_material(model, model.ordinary)
    if isinstance(model, GRADIENT_MEDIA):
        return law_material(model, model.base)
    return model


def _legacy(model: MaterialOpticalModel) -> bool:
    """A model with neither an index reference nor thermo data: its derivatives keep their earlier operations (bits)."""
    return getattr(model, "index_reference", None) is None and getattr(model, "thermo_optic", None) is None


def _sellmeier_derivatives(b, c, lam: float) -> tuple[float, float, float]:
    """``(f, f', f'')`` of ``f = n^2`` of a Sellmeier law at ``lam`` um (per um, per um^2)."""
    lam2 = lam * lam
    f = 1.0
    first = 0.0
    second = 0.0
    for bi, ci in zip(b, c):
        denom = lam2 - ci
        f += bi * lam2 / denom
        first += bi * ci / (denom * denom)
        second += bi * ci * (3.0 * lam2 + ci) / (denom * denom * denom)
    return f, -2.0 * lam * first, 2.0 * second


def _from_square(f: float, f1: float, f2: float) -> tuple[float, float, float]:
    n = math.sqrt(f)
    return n, f1 / (2.0 * n), f2 / (2.0 * n) - f1 * f1 / (4.0 * n * n * n)


def law_derivatives(model: MaterialOpticalModel, law_um: float, temperature_c: float) -> tuple[float, float, float] | None:
    """``(n, dn/dl, d2n/dl2)`` of a formula law at its own wavelength ``law_um`` (um; per um, per um^2); ``None`` for a table."""

    if isinstance(model, ConstantIndexMaterial):
        return model.refractive_index, 0.0, 0.0
    if isinstance(model, SellmeierMaterial):
        return _from_square(*_sellmeier_derivatives(model.b, model.c_um2, law_um))
    if isinstance(model, TemperatureSellmeierMaterial):
        b, c = temperature_sellmeier_terms(model, float(temperature_kelvin(temperature_c)))
        return _from_square(*_sellmeier_derivatives(b, c, law_um))
    if isinstance(model, PowerSeriesMaterial):
        f = f1 = f2 = 0.0
        for a, e in zip(model.coefficients, model.exponents):
            f += a * integer_power(law_um, e)
            f1 += a * e * integer_power(law_um, e - 1)
            f2 += a * e * (e - 1) * integer_power(law_um, e - 2)
        return _from_square(f, f1, f2)
    if isinstance(model, HerzbergerMaterial):
        a, b, c, d, e, f = model.coefficients
        s = law_um * law_um
        big_l = 1.0 / (s - HERZBERGER_CONSTANT_UM2)
        n = a + b * big_l + c * big_l * big_l + d * s + e * s * s + f * s * s * s
        dn_ds = -b * big_l * big_l - 2.0 * c * big_l * big_l * big_l + d + 2.0 * e * s + 3.0 * f * s * s
        d2n_ds2 = 2.0 * b * big_l * big_l * big_l + SIX * c * big_l * big_l * big_l * big_l + 2.0 * e + SIX * f * s
        return n, dn_ds * 2.0 * law_um, d2n_ds2 * 4.0 * s + dn_ds * 2.0
    if isinstance(model, AirMaterial):
        from ..._vendor.shared_contracts.design_catalog.material_physics import air_index, air_index_slope

        vacuum_nm = law_um * UM_TO_NM
        n1, n2 = air_index_slope(vacuum_nm, model.temperature_c, model.pressure_pa)
        return air_index(vacuum_nm, model.temperature_c, model.pressure_pa), n1 * PER_NM_TO_PER_UM, n2 * PER_NM2_TO_PER_UM2
    return None


def _chain(model: MaterialOpticalModel, wavelength_nm: float, temperature_c: float) -> tuple[float, float, float] | None:
    """``(n_abs, dn_abs/dl, d2n_abs/dl2)`` at a vacuum wavelength (um units) through the material-evaluation rule."""

    from ..._vendor.shared_contracts.design_catalog.material_physics import air_index_slope

    lam = wavelength_nm * NM_TO_UM
    reference = getattr(model, "index_reference", None)
    air_wavelength = reference is not None and reference.air_wavelength
    relative = reference is not None and reference.relative_to_air
    r, r1, r2 = 1.0, 0.0, 0.0
    if air_wavelength or relative:
        r = reference_air_index(reference, wavelength_nm)
        s1, s2 = air_index_slope(wavelength_nm, reference.reference_air_temperature_c, reference.reference_air_pressure_pa)
        r1, r2 = s1 * PER_NM_TO_PER_UM, s2 * PER_NM2_TO_PER_UM2
    if air_wavelength:
        g = lam / r
        g1 = 1.0 / r - lam * r1 / (r * r)
        g2 = -2.0 * r1 / (r * r) - lam * r2 / (r * r) + 2.0 * lam * r1 * r1 / (r * r * r)
    else:
        g, g1, g2 = lam, 1.0, 0.0
    law = law_derivatives(model, g, temperature_c)
    if law is None:
        return None
    m, m1, m2 = law
    n_law, n1_law, n2_law = m, m1 * g1, m2 * g1 * g1 + m1 * g2
    if relative:
        n, n1, n2 = n_law * r, n1_law * r + n_law * r1, n2_law * r + 2.0 * n1_law * r1 + n_law * r2
    else:
        n, n1, n2 = n_law, n1_law, n2_law
    thermo = getattr(model, "thermo_optic", None)
    if thermo is not None:
        dt = temperature_c - thermo.reference_temperature_c
        if dt != 0.0:
            a = (n_law * n_law - 1.0) / (2.0 * n_law)
            a1 = 0.5 * (1.0 + 1.0 / (n_law * n_law))
            a2 = -1.0 / (n_law * n_law * n_law)
            d_part = thermo.d0 * dt + thermo.d1 * dt * dt + thermo.d2 * dt * dt * dt
            e_part = thermo.e0 * dt + thermo.e1 * dt * dt
            q = g * g - thermo.lambda_tk_um * thermo.lambda_tk_um
            # A zero E numerator is a removable pole (D-only correction),
            # with exactly zero E slopes even at q = 0. Otherwise the index
            # evaluator has already refused the singular neighbourhood.
            if e_part == 0.0:
                bb, bb1, bb2 = d_part, 0.0, 0.0
            else:
                bb = d_part + e_part / q
                bb1 = -2.0 * g * e_part / (q * q)
                bb2 = e_part * (SIX * g * g + 2.0 * thermo.lambda_tk_um * thermo.lambda_tk_um) / (q * q * q)
            da, d2a = a1 * n1_law, a2 * n1_law * n1_law + a1 * n2_law
            db, d2b = bb1 * g1, bb2 * g1 * g1 + bb1 * g2
            n, n1, n2 = n + a * bb, n1 + da * bb + a * db, n2 + d2a * bb + 2.0 * da * db + a * d2b
    return n, n1, n2


def group_dispersion(model: MaterialOpticalModel, wavelength_nm: float, cfg: NumericalConfig,
                     temperature_c: float | None = None) -> GroupDispersion | None:
    """The index, group index and group-delay dispersion of ``model`` at the vacuum wavelength ``wavelength_nm`` and a
    temperature (the material's own, else ``temperature_c``, else 20 C); ``None`` where the index is not available."""

    whole = isinstance(model, UniaxialMaterial) or isinstance(model, GRADIENT_MEDIA)
    model = _isotropic(model)
    evaluator = build_evaluator(model, cfg, temperature_c)
    n, _issue = evaluator.index(wavelength_nm)
    if n is None:
        return None
    temperature = material_temperature(model, temperature_c)
    if whole:  # (a crystal or gradient medium read as a whole: numerically, as it always was)
        return GroupDispersion(n, _numeric_group_index(evaluator, n, wavelength_nm), _numeric_k2(evaluator, wavelength_nm), "numeric")
    if isinstance(model, TabulatedIndexMaterial):
        if model.measured:
            return GroupDispersion(n, None, None, "none", MEASURED_TABLE_LIMITATION)
        if model.origin == "source_table":
            return GroupDispersion(n, None, None, "none", "SOURCE_TABLE_DERIVATIVE: the source tabulates phase index only.")
        return GroupDispersion(n, _numeric_group_index(evaluator, n, wavelength_nm), _numeric_k2(evaluator, wavelength_nm), "numeric")
    if _legacy(model) and isinstance(model, (ConstantIndexMaterial, SellmeierMaterial)):
        return GroupDispersion(n, _legacy_group_index(model, n, wavelength_nm), _legacy_k2(model, n, wavelength_nm), "analytic")
    got = _chain(model, wavelength_nm, temperature)
    if got is None:  # pragma: no cover - every formula law has derivatives
        return GroupDispersion(n, None, None, "none", "no derivative of this law")
    _value, n1, n2 = got
    lam = wavelength_nm * NM_TO_UM
    n_group = n - lam * n1
    k2 = lam * lam * lam * n2 * UM_CUBED_PER_UM2_TO_MM / (2.0 * math.pi)
    return GroupDispersion(n, n_group if math.isfinite(n_group) else None, k2 if math.isfinite(k2) else None, "analytic")


def _legacy_group_index(model: ConstantIndexMaterial | SellmeierMaterial, n: float, wavelength_nm: float) -> float:
    if isinstance(model, ConstantIndexMaterial):
        return n
    lam = wavelength_nm * NM_TO_UM
    lam2 = lam * lam
    total = 0.0
    for b, c in zip(model.b, model.c_um2):
        denom = lam2 - c
        total += b * c / (denom * denom)
    return n + lam2 * total / n


def _legacy_k2(model: ConstantIndexMaterial | SellmeierMaterial, n: float, wavelength_nm: float) -> float | None:
    if isinstance(model, ConstantIndexMaterial):
        return 0.0
    lam = wavelength_nm * NM_TO_UM
    lam2 = lam * lam
    first = 0.0
    second = 0.0
    for b, c in zip(model.b, model.c_um2):
        denom = lam2 - c
        first += b * c / (denom * denom)
        second += b * c * (3.0 * lam2 + c) / (denom * denom * denom)
    f1 = -2.0 * lam * first
    f2 = 2.0 * second
    curvature_um = f2 / (2.0 * n) - f1 * f1 / (4.0 * n * n * n)  # d^2n/dlambda^2 in 1/um^2
    value = lam2 * lam * curvature_um * UM_CUBED_PER_UM2_TO_MM / (2.0 * math.pi)
    return value if math.isfinite(value) else None


def _numeric_group_index(evaluator, n: float, wavelength_nm: float) -> float | None:
    low, high = evaluator.band_nm
    below = max(low, wavelength_nm - TABULATED_STEP_NM)
    above = min(high, wavelength_nm + TABULATED_STEP_NM)
    n_below, _ = evaluator.index(below)
    n_above, _ = evaluator.index(above)
    if n_below is None or n_above is None or above <= below:
        return None
    slope = (n_above - n_below) / (above - below)
    value = n - wavelength_nm * slope
    return value if math.isfinite(value) else None


def _numeric_k2(evaluator, wavelength_nm: float) -> float | None:
    low, high = evaluator.band_nm
    if high - low < 2.0 * TABULATED_STEP_NM:
        return None
    centre = min(max(wavelength_nm, low + TABULATED_STEP_NM), high - TABULATED_STEP_NM)
    n_below, _ = evaluator.index(centre - TABULATED_STEP_NM)
    n_mid, _ = evaluator.index(centre)
    n_above, _ = evaluator.index(centre + TABULATED_STEP_NM)
    if n_below is None or n_mid is None or n_above is None:
        return None
    curvature_nm = (n_above - 2.0 * n_mid + n_below) / (TABULATED_STEP_NM * TABULATED_STEP_NM)  # 1/nm^2
    value = wavelength_nm * wavelength_nm * wavelength_nm * curvature_nm * NM3_PER_NM2_TO_MM / (2.0 * math.pi)
    return value if math.isfinite(value) else None


def group_index(model: MaterialOpticalModel, wavelength_nm: float, cfg: NumericalConfig,
                temperature_c: float | None = None) -> float | None:
    """``n_g`` of ``model`` at the vacuum wavelength ``wavelength_nm`` (module docstring); ``None`` where it is not available
    (outside the band; a measured table)."""

    got = group_dispersion(model, wavelength_nm, cfg, temperature_c)
    return None if got is None else got.n_group


def group_delay_dispersion(model: MaterialOpticalModel, wavelength_nm: float, cfg: NumericalConfig,
                           temperature_c: float | None = None) -> float | None:
    """``k2 = lambda^3 / (2 pi) d^2n/dlambda^2`` of ``model`` at the vacuum wavelength ``wavelength_nm``, in mm^2 per mm of path
    (module docstring); 0 for a constant index, ``None`` outside the valid band or for a measured table."""

    got = group_dispersion(model, wavelength_nm, cfg, temperature_c)
    return None if got is None else got.k2


#: lambda^3 [um^3] x d^2n/dlambda^2 [1/um^2] -> mm (lambda^3 n'' is a length: um -> mm).
UM_CUBED_PER_UM2_TO_MM = 1e-3
#: lambda^3 [nm^3] x d^2n/dlambda^2 [1/nm^2] -> mm.
NM3_PER_NM2_TO_MM = 1e-6


__all__ = ["MEASURED_TABLE_LIMITATION", "TABULATED_STEP_NM", "GroupDispersion", "group_delay_dispersion", "group_dispersion",
           "group_index", "law_derivatives"]
