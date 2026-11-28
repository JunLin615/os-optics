"""Editable native Gaussian alignment probes for a standalone sequential prescription."""

from __future__ import annotations

import math
from decimal import Decimal
from typing import TYPE_CHECKING

from .._vendor.shared_contracts.design_catalog.material_physics import STANDARD_TEMPERATURE_C
from ..composition.rotation import QUARTER_TURN_DEG, cos_sin_deg, normalize_quaternion
from ..units import NM_TO_MM
from .errors import PrescriptionErrorCode as E, SourceLocation, refuse
from .ir import IrSurface

if TYPE_CHECKING:
    from . import PrescriptionImport


# The prescription describes design fields and relative spectral weights. Optical power is a probe setting.
_PROBE_POWER_W = Decimal('0.001')
_DEFAULT_PROBE_WAVELENGTH_NM = Decimal(550)
# The collimated Gaussian probe waist samples 4% of the clear pupil radius.
_PUPIL_TO_PROBE_WAIST_RATIO = 25.0


def _spectrum(imported: PrescriptionImport) -> tuple[float, dict, str]:
    rx = imported.prescription
    location = rx.wavelength_location or SourceLocation(section="wavelengths")
    band = imported.report["wavelength_band_nm"]
    bounds = (Decimal(band["min_nm"]), Decimal(band["max_nm"])) if band else None
    waves = rx.wavelengths_nm
    if not waves:
        wave = _DEFAULT_PROBE_WAVELENGTH_NM
        if bounds:
            wave = max(bounds[0], min(wave, bounds[1]))
        waves = (wave,)
    if not 0 <= rx.primary_wavelength_index < len(waves):
        raise refuse(E.VALUE_INVALID, "the primary wavelength must name a declared line", location)
    primary = waves[rx.primary_wavelength_index]
    weights = rx.wavelength_weights or (Decimal(1),) * len(waves)
    if len(weights) != len(waves) or any(w < 0 for w in weights) or not any(weights):
        raise refuse(E.VALUE_INVALID, "give each wavelength a nonnegative weight and at least one positive weight", location)
    lines: dict[float, Decimal] = {}
    for wave, weight in zip(waves, weights, strict=True):
        if wave <= 0:
            raise refuse(E.VALUE_INVALID, f"the probe wavelength {wave} nm must be positive", location)
        if bounds and (weight or wave == primary) and not bounds[0] <= wave <= bounds[1]:
            raise refuse(E.MATERIAL_OUTSIDE_BAND, f"the probe wavelength {wave} nm lies outside the supported band {bounds[0]} to {bounds[1]} nm", location)
        if weight:
            key = float(wave)
            lines[key] = lines.get(key, Decimal(0)) + weight
    total = sum(lines.values())
    emission = [{"wavelength_nm": wave, "power_w": float(_PROBE_POWER_W * w / total)} for wave, w in lines.items()]
    if any(line["power_w"] <= 0 for line in emission):
        raise refuse(E.VALUE_INVALID, "the relative spectral weights exceed the native source power range", location)
    model = emission[0] if len(emission) == 1 else {"spectral_lines": emission}
    note = f"Primary wavelength {primary:g} nm in vacuum." if rx.wavelengths_nm else f"Assumed probe wavelength {primary:g} nm in vacuum."
    return float(primary), model, note


def _curvature(s: IrSurface, axis: str) -> float:
    if s.is_mirror or s.linear_sag or s.sag and s.sag.kind not in {"BICONIC", "TOROIDAL", "XY_POLYNOMIAL"}:
        raise refuse(E.UNSUPPORTED_FEATURE, "cannot determine the entrance pupil through this surface for an off-axis probe", s.location)
    radius = s.radius_mm
    if s.cylinder_axis == axis:
        return 0.0
    extra = Decimal(0)
    if s.sag and s.sag.kind == "XY_POLYNOMIAL":
        terms = {(x, y): value for x, y, value in s.sag.terms}
        if terms.get((1, 1)):
            raise refuse(E.UNSUPPORTED_FEATURE, "the XY cross term couples the two entrance-pupil sections", s.location)
        power = (2, 0) if axis == "X" else (0, 2)
        extra = 2 * terms.get(power, Decimal(0)) / s.sag.normalization_radius_mm**2
    elif s.sag:
        radius = s.sag.radius_y_mm if axis == "Y" else (
            s.sag.radius_x_mm if s.sag.kind == "BICONIC" else s.sag.radius_of_rotation_mm)
        if s.sag.kind == "TOROIDAL" and axis == "Y" and s.sag.y_terms:
            extra = 2 * s.sag.y_terms[0]
    return (1.0 / float(radius) if radius else 0.0) + float(extra)


def _stop_transfer(imported: PrescriptionImport, stop_index: int, primary: float) -> list[tuple[float, float]]:
    """Coefficients A, B for each section: stop-plane height = A * first-vertex-plane height + B * incident slope.

    Back-propagate that linear functional through each preceding gap and refracting interface. The index evaluation is
    the native trace's (including atmospheric and thermal reference). B/A is the signed entrance-pupil position in mm.
    """
    from ..config import DEFAULT_NUMERICAL_CONFIG
    from ..local.compile import engine_material, environment_air, surrounding_index
    from ..optics.materials.index import build_evaluator

    rx = imported.prescription
    before_stop = rx.surfaces[:stop_index]
    resolved = {g["written"]: g["material_id"] for g in imported.report["glasses"]}
    materials = {m.material_id: m for m in imported.model_materials}
    try:
        n_air = surrounding_index(environment_air(imported.environment), primary)
    except ValueError as exc:
        raise refuse(E.MATERIAL_OUTSIDE_BAND, str(exc), rx.wavelength_location or SourceLocation(section="wavelengths")) from None
    indices = [n_air]
    cache = {"VACUUM": 1.0}
    for surface in before_stop:
        ref = surface.material
        if ref is None:
            indices.append(n_air)
            continue
        material_id = resolved.get(ref.key, ref.name.upper())
        if material_id not in cache:
            temperature = float(imported.environment.temperature_c) if imported.environment else float(STANDARD_TEMPERATURE_C)
            evaluator = build_evaluator(engine_material(materials[material_id], temperature_c=temperature), DEFAULT_NUMERICAL_CONFIG, temperature)
            value, issue = evaluator.index(primary)
            if value is None:
                raise refuse(E.MATERIAL_INVALID, issue.message if issue else f"no index for {ref.key} at {primary} nm", ref.location)
            cache[material_id] = value
        indices.append(cache[material_id])
    result = []
    for axis in ("X", "Y"):
        a, b = 1.0, 0.0
        for i in reversed(range(stop_index)):
            s = before_stop[i]
            b = (b + a * float(s.thickness_mm)) / indices[i + 1]
            a -= b * (indices[i + 1] - indices[i]) * _curvature(s, axis)
            b *= indices[i]
        result.append((a, b))
    return result


def _slope(angle: Decimal, location: SourceLocation) -> float:
    if not -QUARTER_TURN_DEG < angle < QUARTER_TURN_DEG:
        raise refuse(E.UNSUPPORTED_FEATURE, f"angular field {angle} degrees does not propagate towards increasing z", location)
    # Reduce the unsigned angle so tiny negative slopes retain the same precision as positive ones.
    c, s = cos_sin_deg(float(abs(angle)))
    if c == 0.0:
        raise refuse(E.UNSUPPORTED_FEATURE, f"angular field {angle} degrees is at grazing incidence at native precision", location)
    return (-s if angle < 0 else s) / c


def sources(imported: PrescriptionImport) -> list[dict]:
    """One beam per field, in the first-vertex frame (+Z incident), with the full positive-weight spectrum."""
    rx = imported.prescription
    primary, emission, spectrum_note = _spectrum(imported)
    fields = rx.fields or ((Decimal(0), Decimal(0)),)
    finite = rx.object is not None and rx.object.distance_mm is not None and not rx.object.infinite
    if finite and rx.object.distance_mm <= 0:
        raise refuse(E.UNSUPPORTED_FEATURE, "a source waist on or behind the first vertex cannot launch a real finite-object probe", rx.object.location)
    field_loc = rx.field_location or SourceLocation(section="fields")
    off_axis = any(x or y for x, y in fields)
    if off_axis and (rx.field_type not in {"angle", "height"} or rx.field_type == "height" and not finite):
        raise refuse(E.UNSUPPORTED_FEATURE, f"off-axis field type {rx.field_type!r} needs an object-space angle or a height at a finite real object", field_loc)

    stop_index = next((i for i, s in enumerate(rx.surfaces) if s.is_stop), 0)
    stop = rx.surfaces[stop_index]
    clear = float(stop.clear_semi_diameter_mm)
    hole = float(stop.hole_radius_mm or 0)
    target = ((clear + hole) / 2 if hole else 0.0, 0.0)
    transfer = _stop_transfer(imported, stop_index, primary) if stop_index and (off_axis or hole) else [(1.0, 0.0)] * 2
    front = float(imported.optical_models[0].model.envelope.z_min_mm)
    if finite:
        z = -float(rx.object.distance_mm)
        # Waist giving the smallest paraxial spot at one object distance, at the primary vacuum wavelength.
        waist = math.sqrt(-z * (primary * NM_TO_MM / math.pi))
        conjugate = f"Finite object at z={z:g} mm."
    else:
        z = min(0.0, front) - max(1.0, clear)
        # Sample 4% of the specified entrance-pupil radius. For a holed stop, keep the probe inside the clear annulus.
        radius = float(rx.entrance_pupil_diameter_mm) / 2 if rx.entrance_pupil_diameter_mm else clear
        waist = (min(radius, clear - hole) if hole else radius) / _PUPIL_TO_PROBE_WAIST_RATIO
        conjugate = "Object at infinity." if rx.object and rx.object.infinite else "Assumed object at infinity."
    # A native observation records crossings in either direction. On a single reflection the image can sit
    # on the incoming leg. A collimated field can start past that plane in the gap ahead of all the optics.
    image_z = imported.report["image_plane_z_mm"]
    if image_z is not None and sum(s.is_mirror for s in rx.surfaces) == 1 and z <= float(image_z):
        plane = float(image_z)
        if finite:
            raise refuse(E.UNSUPPORTED_FEATURE, "the reflected image observation lies on the incident path from the finite object. "
                         "A two-sided observation would also record incident light", rx.image)
        z = (plane + front) / 2
        if not plane < z < front:
            raise refuse(E.UNSUPPORTED_FEATURE, "the reflected image observation leaves no free launch plane between the image "
                         "and the front of the optics for a collimated field", rx.image)
    aperture = []
    if rx.object_na is not None:
        aperture.append(f"object-space NA {rx.object_na:g}")
    if rx.entrance_pupil_diameter_mm is not None:
        aperture.append(f"entrance pupil diameter {rx.entrance_pupil_diameter_mm:g} mm")
    if rx.image_f_number is not None:
        aperture.append(f"image-space f/{rx.image_f_number:g}")
    aperture_note = f" The prescription specifies {', '.join(aperture)}." if aperture else ""
    aim = f"paraxial aim at stop {stop.label}" if stop.is_stop else "aim at the first vertex plane (no marked stop)"
    aim += ", midway across its clear annulus on +X" if hole else ", on axis"

    components = []
    for i, field in enumerate(fields, 1):
        slopes, positions = [], []
        for value, hit, (a, b) in zip(field, target, transfer, strict=True):
            # y_stop = A*(y_launch - z_launch*u) + B*u. For an angular field the signed incoming slope is known;
            # for an object height the launch coordinate is known. The zero-field central ray also holds at a singular pupil.
            if value == 0 and hit == 0:
                slope, coord = 0.0, 0.0
            elif rx.field_type == "height":
                coord = float(value)
                if b - a * z == 0:
                    raise refuse(E.UNSUPPORTED_FEATURE, "the object plane is conjugate to the stop; its height does not determine a unique probe direction", field_loc)
                slope = (hit - a * coord) / (b - a * z)
            else:
                slope = _slope(value, field_loc)
                if a == 0:
                    raise refuse(E.UNSUPPORTED_FEATURE, "the entrance pupil is at infinity; the angle does not determine a unique probe position", field_loc)
                # The angle locates the chief ray at the pupil centre. A finite annular field keeps that
                # same object point and changes its launch direction to sample the clear annulus.
                coord = (z - b / a) * slope
                if hit and finite:
                    if b == a * z:
                        raise refuse(E.UNSUPPORTED_FEATURE, "the finite object plane is conjugate to the annular stop", field_loc)
                    slope += hit / (b - a * z)
                elif hit:
                    coord += hit / a
            slopes.append(slope)
            positions.append(coord)
        dx, dy = slopes
        if not all(math.isfinite(v) for v in (*slopes, *positions, z, waist)) or waist <= 0:
            raise refuse(E.VALUE_INVALID, "the derived probe pose or waist exceeds native numerical precision", field_loc)
        # Shortest rotation of local +Z to normalize((dx,dy,1)), without independent Euler-angle approximations.
        length = math.hypot(dx, dy, 1.0)
        q = normalize_quaternion((1.0 + 1.0 / length, -dy / length, dx / length, 0.0))
        if rx.field_type == "height":
            coordinates = f"object height ({field[0]:g}, {field[1]:g}) mm"
        elif rx.field_type == "angle":
            coordinates = f"incoming angle ({field[0]:g}, {field[1]:g}) deg"
        else:
            coordinates = "axial field"
        merit_note = f" Field merit weight {rx.field_weights[i - 1]:g}." if rx.field_weights else ""
        description = (f"Gaussian alignment probe for field {i}. Base {coordinates}.{merit_note} {conjugate} {spectrum_note} "
                       f"Primary-wavelength {aim}.{aperture_note} Narrow Gaussian sampling with 1 mW per field and "
                       "local-X linear polarization.")
        components.append({"instance_id": f"SRC{i}", "optical_role": "source",
                           "custom_part": {"custom_part_key": "field-probe", "description": description},
                           "nominal_pose": {"position_mm": [*positions, z], "quaternion_wxyz": list(q)},
                           "source_model": {"model_kind": "GAUSSIAN_BEAM", **emission, "waist_radius_mm": waist,
                                            "waist_position_mm": 0.0, "m2": 1.0, "polarization": {"kind": "LINEAR", "angle_deg": 0.0}}})
    return components
