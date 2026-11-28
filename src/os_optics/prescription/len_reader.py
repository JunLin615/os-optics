"""Read a centered, sequential text .len file into native prescription data."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .._vendor.shared_contracts.design_catalog.material_physics import STANDARD_PRESSURE_PA, STANDARD_TEMPERATURE_C
from ..composition.rotation import QUARTER_TURN_DEG
from .csv_table import NUMBER_MAX, NUMBER_MIN
from .errors import PrescriptionErrorCode as E, SourceLocation, refuse
from .ir import (
    MM_PER_UNIT, NM_PER_UM, VACUUM_WAVELENGTH_QUANTUM_NM, WAVELENGTHS_AIR,
    IrSurface, MaterialRef, ObjectSpace, Prescription, ReferenceEnvironment,
    air_to_vacuum_nm, power_to_mm, to_mm,
)

FORMAT_ID = ".len"
_NUMBER = re.compile(r"[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")
_TOKEN = re.compile(r'\s+|//.*|"[^"\n]*"|;|(?:[^\s;"/]|/(?!/))+')
_SYSTEM = {"UNI", "WV", "WW", "EBR", "NAO", "ANG", "OBH"}
_ASPHERE_POWERS = {"AD": 4, "AE": 6, "AF": 8, "AG": 10}
_SURFACE = {"AIR", "GLA", "RD", "TH", "AP", "AST", "CC", *_ASPHERE_POWERS}


@dataclass(frozen=True)
class _Record:
    args: tuple[str, ...]
    loc: SourceLocation

    def expect(self, count: int) -> None:
        if len(self.args) != count:
            raise refuse(E.ROW_INVALID, f"{self.loc.column} expects {count} arguments in this subset", self.loc)

    def number(self, index: int = 0) -> Decimal:
        raw = self.args[index]
        if _NUMBER.fullmatch(raw):
            try:
                value = Decimal(raw)
                if value == 0:
                    return Decimal(0)
                if NUMBER_MIN <= value.copy_abs() <= NUMBER_MAX:
                    return value
            except InvalidOperation:
                pass
        raise refuse(E.VALUE_INVALID, f"invalid decimal {raw!r}", self.loc)

    def scalar(self) -> Decimal:
        self.expect(1)
        return self.number()

    def integer(self, index: int) -> int:
        value = self.args[index]
        if not re.fullmatch(r"[0-9]{1,6}", value):
            raise refuse(E.VALUE_INVALID, "surface count must be a nonnegative integer", self.loc)
        return int(value)


def _commands(text: str):
    for row, line in enumerate(text.removeprefix("\ufeff").splitlines(), 1):
        words = []
        offset = 0
        while offset < len(line):
            token = _TOKEN.match(line, offset)
            if token is None:
                raise refuse(E.TEXT_INVALID, "unterminated quoted argument", SourceLocation(row=row))
            word = token.group()
            offset = token.end()
            if word.startswith("//"):
                break
            if word == ";":
                if words:
                    yield row, words
                words = []
            elif not word.isspace():
                words.append(word.strip('"'))
        if words:
            yield row, words


def _records(text: str):
    header = end = None
    system = {}
    blocks = []
    for row, words in _commands(text):
        key = words[0].upper()
        loc = SourceLocation(row=row, column=key, surface=str(len(blocks) - 1) if blocks else None)
        record = _Record(tuple(words[1:]), loc)
        if end is not None:
            raise refuse(E.SURFACE_ORDER_INVALID, "unexpected record after END", loc)
        if header is None:
            if key != "LEN" or len(record.args) not in {2, 4} or record.args[0].upper() != "NEW":
                raise refuse(E.FORMAT_UNSUPPORTED, 'expected LEN NEW "title" [focal-length surface-count]', loc)
            if len(record.args) == 4:
                record.number(2)
                record.integer(3)
            header = record
            blocks.append((loc, {}))
        elif key == "NXT":
            record.expect(0)
            blocks.append((SourceLocation(row=row, surface=str(len(blocks))), {}))
        elif key == "END":
            record.expect(1)
            actual = len(blocks) - 1
            if record.integer(0) != actual or len(header.args) == 4 and header.integer(3) != actual:
                raise refuse(E.SURFACE_ORDER_INVALID, f"surface count disagrees with the {actual} NXT records", loc)
            end = record
        elif key == "DES" or re.fullmatch(r"SNO[0-9]+", key):
            record.expect(1)  # Designer and system-note text.
        elif key in _SYSTEM or key in _SURFACE:
            target = system if key in _SYSTEM else blocks[-1][1]
            if key in target:
                raise refuse(E.UNSUPPORTED_FEATURE, f"repeated {key} assignment in one scope", loc)
            target[key] = record
        else:
            raise refuse(E.UNSUPPORTED_FEATURE, f"unsupported .len command {key!r}", loc)
    if end is None or len(blocks) < 3:
        raise refuse(E.SURFACE_ORDER_INVALID, "expected object, optical surfaces, image and END", SourceLocation(row=1))
    return header, system, blocks


def _aperture(record: _Record, unit: str, *, require_checked: bool) -> Decimal:
    if len(record.args) == 2 and record.args[0].upper() == "CHK":
        value = record.number(1)
    elif len(record.args) == 1 and not require_checked:
        value = record.scalar()
    else:
        raise refuse(E.UNSUPPORTED_FEATURE, "a checked circular aperture (AP CHK radius) is required here", record.loc)
    if value <= 0:
        raise refuse(E.VALUE_INVALID, "aperture radius must be positive", record.loc)
    return to_mm(value, unit)


def _surface(block, unit: str, is_stop: bool, last: bool) -> IrSurface:
    loc, data = block
    if "AP" not in data:
        raise refuse(E.FIELD_MISSING, "give the physical surface a circular aperture (AP CHK radius)", loc.at("AP"))
    radius = data["RD"].scalar() if "RD" in data else 0
    conic = data["CC"].scalar() if "CC" in data else 0
    if conic and not radius:
        raise refuse(E.SURFACE_INVALID, "a conic surface requires a finite nonzero radius", data["CC"].loc)
    if "AST" in data:
        data["AST"].expect(0)
    if "AIR" in data:
        data["AIR"].expect(0)
    material = None
    if "GLA" in data:
        gla = data["GLA"]
        if "AIR" in data or len(gla.args) != 1 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]*", gla.args[0]):
            raise refuse(E.UNSUPPORTED_FEATURE, "GLA must name a single catalog glass", gla.loc)
        material = MaterialRef(gla.args[0], gla.loc)
    thickness = data["TH"].scalar() if "TH" in data else Decimal(0)
    if abs(thickness) >= Decimal('9.9e9') or not last and thickness <= 0:
        raise refuse(E.THICKNESS_INVALID, "expected finite axial spacing, positive between physical surfaces",
                     data["TH"].loc if "TH" in data else loc.at("TH"))
    coeffs = []
    for key, power in _ASPHERE_POWERS.items():
        if key in data:
            while len(coeffs) < (power - 4) // 2:
                coeffs.append(Decimal(0))
            coeffs.append(power_to_mm(data[key].scalar(), unit, 1 - power)[0])
    return IrSurface(label=loc.surface, location=loc, radius_mm=to_mm(radius, unit) if radius else None,
                     thickness_mm=to_mm(thickness, unit), clear_semi_diameter_mm=_aperture(data["AP"], unit, require_checked=not is_stop),
                     conic=conic or None, aspheric_mm=tuple(coeffs), material=material, is_stop=is_stop)


def _end_plane(block, *, image: bool):
    loc, data = block
    for key, record in data.items():
        if key == "AIR":
            record.expect(0)
        elif key in {"TH", "AP"} or key == "RD" and record.scalar() == 0:
            continue
        else:
            raise refuse(E.UNSUPPORTED_FEATURE, f"the {'image' if image else 'object'} must be a plane in air", record.loc)
    if image and "TH" in data and data["TH"].scalar() != 0:
        raise refuse(E.UNSUPPORTED_FEATURE, "nonzero thickness after the image", data["TH"].loc)
    return loc, data


def _spectrum(system, header, environment):
    wave = system.get("WV")
    if wave:
        values = tuple(wave.number(i) for i in range(len(wave.args)))
    else:
        values = (Decimal("0.58756"), Decimal("0.48613"), Decimal("0.65627"))
    if not values or any(not Decimal("0.2") <= v <= Decimal("99") for v in values):
        raise refuse(E.VALUE_INVALID, "expected air wavelengths between 0.2 and 99 micrometres", (wave or header).loc)
    weight = system.get("WW")
    if weight:
        weight.expect(len(values))
    weights = tuple(weight.number(i) if weight else Decimal(1) for i in range(len(values)))
    if weights[0] <= 0 or any(w < 0 for w in weights):
        raise refuse(E.VALUE_INVALID, "weights must be nonnegative and the primary weight positive", (weight or header).loc)
    wavelengths = tuple(
        air_to_vacuum_nm(v * NM_PER_UM, environment.temperature_c, environment.pressure_pa).quantize(VACUUM_WAVELENGTH_QUANTUM_NM)
        for v in values
    )
    return wavelengths, weights, (wave or header).loc


def _fields(system, header, unit, infinite):
    axis = (Decimal(0), Decimal(0))
    if "OBH" in system:
        record = system["OBH"]
        if infinite or "ANG" in system:
            raise refuse(E.UNSUPPORTED_FEATURE, "OBH requires a finite object and a single field specification", record.loc)
        value = to_mm(record.scalar(), unit)
        kind = "height"
    else:
        record = system.get("ANG")
        value = record.scalar() if record else Decimal(0)
        if value and (not infinite or abs(value) >= QUARTER_TURN_DEG):
            raise refuse(E.UNSUPPORTED_FEATURE, "use angles below 90 degrees for an object at infinity", record.loc)
        kind = "angle"
    return kind, (axis, (Decimal(0), value)) if value else (axis,), (record or header).loc


def read_len(text: str, *, default_unit: str = "mm", requested_unit: str | None = None, ignore_columns=()) -> Prescription:
    """Read sequential LEN NEW / NXT / END records. Unknown optical commands fail at their source row."""
    if ignore_columns:
        raise refuse(E.OPTIONS_INVALID, "ignore_columns applies only to tables")
    header, system, blocks = _records(text)
    unit = requested_unit or default_unit
    if "UNI" in system:
        scale_mm = system["UNI"].scalar()
        unit = next((key for key, scale in MM_PER_UNIT.items() if scale == scale_mm), None)
        if requested_unit and unit and requested_unit != unit:
            raise refuse(E.UNITS_CONFLICT, "requested units conflict with UNI", system["UNI"].loc)
    if unit not in MM_PER_UNIT:
        raise refuse(E.UNITS_INVALID, "supported unit scales in mm are 1, 10, 1000 and 25.4", system.get("UNI", header).loc)
    obj_loc, obj = _end_plane(blocks[0], image=False)
    image_loc, image = _end_plane(blocks[-1], image=True)
    if "TH" not in obj:
        raise refuse(E.FIELD_MISSING, "object thickness must be explicit", obj_loc.at("TH"))
    distance = obj["TH"].scalar()
    infinite = abs(distance) >= Decimal('1e8')
    if not infinite and distance <= 0:
        raise refuse(E.UNSUPPORTED_FEATURE, "the finite object must precede the first vertex", obj["TH"].loc)
    if "AP" in obj:
        if len(obj["AP"].args) != 1:
            raise refuse(E.UNSUPPORTED_FEATURE, "aperture clipping at the object is unsupported", obj["AP"].loc)
        _aperture(obj["AP"], unit, require_checked=False)
    physical = blocks[1:-1]
    stops = [i for i, (_, data) in enumerate(physical) if "AST" in data]
    if len(stops) > 1:
        raise refuse(E.UNSUPPORTED_FEATURE, "only one aperture stop is supported", physical[stops[1]][1]["AST"].loc)
    stop = stops[0] if stops else 0
    surfaces = tuple(_surface(block, unit, i == stop, i == len(physical) - 1) for i, block in enumerate(physical))
    environment = ReferenceEnvironment(STANDARD_TEMPERATURE_C, STANDARD_PRESSURE_PA)
    wavelengths, weights, wave_loc = _spectrum(system, header, environment)
    field_type, fields, field_loc = _fields(system, header, unit, infinite)
    pupil = to_mm(system["EBR"].scalar(), unit) * 2 if "EBR" in system else None
    na = system["NAO"].scalar() if "NAO" in system else None
    if pupil is not None and (not infinite or pupil <= 0):
        raise refuse(E.UNSUPPORTED_FEATURE, "EBR requires a positive radius and an object at infinity", system["EBR"].loc)
    if na is not None and (infinite or not 0 < na < 1):
        raise refuse(E.UNSUPPORTED_FEATURE, "NAO requires a finite object and 0 < NA < 1", system["NAO"].loc)
    return Prescription(
        source_format=FORMAT_ID, source_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        input_unit=unit, name=header.args[1], surfaces=surfaces,
        object=ObjectSpace(obj_loc, None if infinite else to_mm(distance, unit), infinite), image=image_loc,
        image_semi_diameter_mm=_aperture(image["AP"], unit, require_checked=True) if "AP" in image else None,
        field_location=field_loc, wavelength_location=wave_loc, environment=environment,
        wavelengths_in=WAVELENGTHS_AIR, wavelengths_nm=wavelengths, wavelength_weights=weights,
        object_na=na, entrance_pupil_diameter_mm=pupil, field_type=field_type, fields=fields,
    )
