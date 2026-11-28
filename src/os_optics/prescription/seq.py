"""Read a centred, transmissive .seq prescription with explicit circular apertures."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from decimal import Decimal, InvalidOperation

from . import ir
from .csv_table import MATERIAL_ID_PATTERN, NUMBER_MAX, NUMBER_MIN, NUMBER_PATTERN
from .errors import ImportWarning, PrescriptionErrorCode as E, SourceLocation, refuse

FORMAT_ID = ".seq"
_UNITS = {"M": "mm", "MM": "mm", "C": "cm", "CM": "cm", "I": "in", "IN": "in"}
_TOKEN = re.compile(r'''"[^"\n]*"|'[^'\n]*'|![^\n]*|[^\s"'!;&]+|[;&]|["']''')
_GLOBALS = {"LEN", "TITLE", "DIM", "RDM", "EPD", "NAO", "FNO", "WL", "REF", "WTW", "TEM", "INI",
            "XAN", "YAN", "XOB", "YOB", "WTF", "VUX", "VLX", "VUY", "VLY"}
_ASPHERES = tuple("ABCDEFGH")  # A is r^4, H is r^18


@dataclass(frozen=True)
class _Command:
    key: str
    args: tuple[str, ...]
    location: SourceLocation

    def expect(self, *counts: int) -> None:
        if len(self.args) not in counts:
            raise refuse(E.ROW_INVALID, f"{self.key} expects {' or '.join(map(str, counts))} arguments", self.location)

    def number(self, token: str) -> Decimal:
        if NUMBER_PATTERN.fullmatch(token):
            try:
                value = Decimal(token)
                if value == 0 or NUMBER_MIN <= value.copy_abs() <= NUMBER_MAX:
                    return value
            except InvalidOperation:
                pass
        raise refuse(E.VALUE_INVALID, f"{self.key} requires a finite decimal, got {token!r}", self.location)

    def numbers(self) -> tuple[Decimal, ...]:
        if not self.args:
            raise refuse(E.FIELD_MISSING, f"{self.key} needs at least one value", self.location)
        return tuple(self.number(t) for t in self.args)

    def scalar(self) -> Decimal:
        self.expect(1)
        return self.number(self.args[0])

    def unsupported(self, reason: str):
        return refuse(E.UNSUPPORTED_FEATURE, f"{self.key}: {reason}", self.location)


def _commands(text: str) -> Iterator[_Command]:
    tokens: list[str] = []
    start = 0
    continued = False
    for line, raw in enumerate(text.removeprefix('\ufeff').splitlines(), 1):
        parts = [m.group() for m in _TOKEN.finditer(raw)]
        if parts and parts[-1].startswith('!'):
            parts.pop()
        if not parts:
            continue
        continued = parts[-1] == '&'
        if continued:
            parts.pop()
        else:
            parts.append(';')
        for part in parts:
            if part == ';':
                if tokens:
                    yield _Command(tokens[0].upper(), tuple(tokens[1:]), SourceLocation(row=start, column=tokens[0]))
                    tokens.clear()
            elif part in ('"', "'", '&'):
                raise refuse(E.TEXT_INVALID, 'Unterminated quotation or misplaced continuation', SourceLocation(row=line))
            else:
                if not tokens:
                    start = line
                tokens.append(part[1:-1] if part.startswith(('"', "'")) else part)
    if continued:
        raise refuse(E.TEXT_INVALID, 'Missing continuation at end of file', SourceLocation(row=start))


def _add(table: dict[str, _Command], cmd: _Command, key: str | None = None) -> None:
    key = key or cmd.key
    if key in table:
        raise refuse(E.ROW_INVALID, f"Repeated {key} (first at row {table[key].location.row})", cmd.location)
    table[key] = cmd


@dataclass
class _Surface:
    start: _Command
    modifiers: dict[str, _Command] = field(default_factory=dict)

    def modifier(self, cmd: _Command) -> None:
        key = cmd.key
        if key == 'CIR' and len(cmd.args) == 2 and cmd.args[0].upper() == 'EDG':
            key = 'EDGE'
            cmd = _Command(key, cmd.args[1:], cmd.location)
        if key not in {'STO', 'CIR', 'EDGE', 'SLB', 'SPH', 'CON', 'ASP', 'K', 'CUF', *_ASPHERES}:
            raise cmd.unsupported('unrecognized surface instruction')
        if key in {'STO', 'SPH', 'CON', 'ASP'}:
            cmd.expect(0)
        else:
            cmd.expect(1)
        _add(self.modifiers, cmd, key)

    def build(self, label: str, unit: str, radius_mode: bool, private_names: set[str], warnings: list[ImportWarning]) -> ir.IrSurface:
        row, mods = self.start, self.modifiers
        row.expect(2, 3)
        location = SourceLocation(row=row.location.row, column='S', surface=label, section='surfaces')
        if 'CIR' not in mods:
            raise refuse(E.FIELD_MISSING, 'Give the physical clear radius with CIR on each surface', location)
        clear = ir.to_mm(mods['CIR'].scalar(), unit)
        if clear <= 0:
            raise refuse(E.VALUE_INVALID, 'CIR clear radius must be positive', mods['CIR'].location)
        edge = ir.to_mm(mods['EDGE'].scalar(), unit) if 'EDGE' in mods else None
        if edge is not None and edge < clear:
            raise refuse(E.EDGE_INVALID, 'CIR EDG is smaller than the clear radius', mods['EDGE'].location)
        profile = [k for k in mods if k in {'SPH', 'CON', 'ASP'}]
        if len(profile) > 1:
            raise refuse(E.ROW_INVALID, 'Conflicting surface profiles', mods[profile[-1]].location)
        value = row.number(row.args[0])
        if value and not radius_mode:
            value, exact = ir.reciprocal(value)
            if not exact:
                warnings.append(ImportWarning('CONVERSION_ROUNDED', 'Reciprocal curvature rounded to 28 digits', location))
        radius = ir.to_mm(value, unit) if value else None
        thickness = ir.to_mm(row.number(row.args[1]), unit)
        k = mods['K'].scalar() if 'K' in mods else None
        if k and (profile == ['SPH'] or radius is None):
            raise mods['K'].unsupported('nonzero conic constant needs a curved conic or asphere')
        if 'CUF' in mods and mods['CUF'].scalar() != 0:
            raise mods['CUF'].unsupported('nonzero asphere curvature offset')
        coefficients = []
        for i, key in enumerate(_ASPHERES):
            if key not in mods:
                continue
            if profile != ['ASP']:
                raise mods[key].unsupported('polynomial coefficients require ASP')
            value, exact = ir.power_to_mm(mods[key].scalar(), unit, -(3 + 2 * i))
            if not exact:
                warnings.append(ImportWarning('CONVERSION_ROUNDED', 'Asphere coefficient rounded to 28 digits', mods[key].location))
            coefficients.extend([Decimal(0)] * (i - len(coefficients)))
            coefficients.append(value)
        material = None
        if len(row.args) == 3 and row.args[2].upper() != 'AIR':
            token = row.args[2].upper()
            if token in {'REFL', 'MIRROR'} or NUMBER_PATTERN.fullmatch(token) or not MATERIAL_ID_PATTERN.fullmatch(token):
                raise row.unsupported(f'expected a named transmissive glass, got {token!r}')
            designation, separator, maker = token.rpartition('_') if token not in private_names else ('', '', token)
            if separator and (not designation or not maker):
                raise row.unsupported('a qualified glass needs both a designation and a catalog tag')
            material = ir.MaterialRef(designation if separator else token, location.at('glass'), manufacturer=maker if separator else None)
        return ir.IrSurface(label, location, clear, radius_mm=radius, thickness_mm=thickness, material=material,
                            conic=k, aspheric_mm=tuple(coefficients), mechanical_semi_diameter_mm=edge,
                            is_stop='STO' in mods, comment=mods['SLB'].args[0] if 'SLB' in mods else None)


def _private_materials(commands: list[_Command]) -> tuple[ir.MaterialDefinition, ...]:
    if not commands or commands[0].key != 'PWL':
        raise refuse(E.FIELD_MISSING, 'PRV needs PWL followed by named index rows', commands[0].location if commands else None)
    wavelengths = commands[0].numbers()
    if len(wavelengths) < 2 or min(wavelengths) < 180 or max(wavelengths) > 100000 or len(set(wavelengths)) != len(wavelengths):
        raise refuse(E.MATERIAL_INVALID, 'PWL needs distinct wavelengths in the air model range 180..100000 nm', commands[0].location)
    materials = {}
    for row in commands[1:]:
        if not MATERIAL_ID_PATTERN.fullmatch(row.key) or len(row.key) > 64 or row.key in materials or row.key == 'PWL':
            raise refuse(E.MATERIAL_INVALID, 'Private glass needs a unique material name of up to 64 characters', row.location)
        if not row.args or not NUMBER_PATTERN.fullmatch(row.args[0]):
            raise row.unsupported('only tabulated index rows are supported in PRV')
        indices = row.numbers()
        if len(indices) != len(wavelengths) or min(indices) < 1:
            raise refuse(E.MATERIAL_INVALID, 'Give one refractive index of at least one per PWL wavelength', row.location)
        ordered = sorted(zip(wavelengths, indices, strict=True))
        waves, values = zip(*ordered, strict=True)
        materials[row.key] = ir.MaterialDefinition(row.key, ir.TabulatedIndexDefinition(waves, values), waves[0], waves[-1],
                                                   row.location, index_reference=ir.LAW_AIR, basis='Index samples from the prescription PRV table.')
    if not materials:
        raise refuse(E.FIELD_MISSING, 'PRV needs a named refractive-index table', commands[0].location)
    return tuple(materials.values())


def read_seq(text: str, *, default_unit: str = 'mm', requested_unit: str | None = None, ignore_columns=()) -> ir.Prescription:
    """Read the supported .seq commands. Source rows count physical lines from one."""
    if not isinstance(text, str) or not text.strip():
        raise refuse(E.TEXT_INVALID, 'Give a nonempty text prescription')
    if ignore_columns:
        raise refuse(E.OPTIONS_INVALID, 'ignore_columns applies only to surface tables')
    settings: dict[str, _Command] = {}
    private: list[_Command] | None = None
    materials: tuple[ir.MaterialDefinition, ...] = ()
    obj = image = None
    rows: list[_Surface] = []
    end = False
    for cmd in _commands(text):
        key = cmd.key
        if end:
            raise cmd.unsupported('commands after GO are outside the prescription')
        if private is not None:
            if key == 'END':
                cmd.expect(0)
                materials = _private_materials(private)
                private = None
            else:
                private.append(cmd)
        elif key == 'PRV':
            cmd.expect(0)
            if materials or obj:
                raise cmd.unsupported('give one private catalog before SO')
            private = []
        elif key in _GLOBALS or key == 'TIT':
            if key in {'DIM', 'RDM', 'LEN'} and obj:
                raise cmd.unsupported('geometry mode must precede SO')
            _add(settings, cmd, 'TITLE' if key == 'TIT' else key)
        elif key == 'SO' and not obj:
            cmd.expect(2)
            obj = cmd
        elif key == 'S' and obj and not image:
            rows.append(_Surface(replace(cmd, location=replace(cmd.location, surface=str(len(rows) + 1), section='surfaces'))))
        elif key == 'SI' and rows and not image:
            cmd.expect(2)
            if any(cmd.numbers()):
                raise cmd.unsupported('curved image surfaces and image defocus are outside this subset')
            image = cmd
        elif key == 'GO' and image:
            cmd.expect(0)
            end = True
        elif key in {'SO', 'S', 'SI', 'GO'}:
            raise refuse(E.SURFACE_ORDER_INVALID, 'Expected SO, one or more S rows, then SI', cmd.location)
        elif rows and not image:
            rows[-1].modifier(replace(cmd, location=replace(cmd.location, surface=str(len(rows)), section='surfaces')))
        else:
            raise cmd.unsupported('unrecognized instruction or misplaced surface modifier')
    if private is not None:
        raise refuse(E.TEXT_INVALID, 'Unterminated private catalog (missing END)')
    if not obj or not image or not rows:
        raise refuse(E.SURFACE_ORDER_INVALID, 'Expected SO, one or more S rows, then SI')

    for key in ('TITLE', 'DIM', 'INI'):
        if key in settings:
            settings[key].expect(1)
    for key in ('LEN', 'RDM'):
        if key in settings:
            settings[key].expect(0, 1)
    if 'LEN' in settings and settings['LEN'].args and not settings['LEN'].args[0].upper().startswith('VERSION:'):
        raise settings['LEN'].unsupported('only a lens header and its optional version comment are accepted')
    unit = requested_unit or default_unit
    if 'DIM' in settings:
        cmd = settings['DIM']
        unit = _UNITS.get(cmd.args[0].upper())
        if not unit:
            raise refuse(E.UNITS_INVALID, 'DIM expects M, C or I', cmd.location)
        if requested_unit and requested_unit != unit:
            raise refuse(E.UNITS_CONFLICT, f'DIM declares {unit}, requested {requested_unit}', cmd.location)
    if unit not in ir.MM_PER_UNIT:
        raise refuse(E.UNITS_INVALID, f'Unknown length unit {unit!r}')
    radius_mode = False
    if 'RDM' in settings:
        cmd = settings['RDM']
        if cmd.args and cmd.args[0].upper() not in {'Y', 'N'}:
            raise cmd.unsupported('RDM accepts Y or N')
        radius_mode = not cmd.args or cmd.args[0].upper() == 'Y'
    if obj.number(obj.args[0]) != 0:
        raise obj.unsupported('curved object surface')
    object_spacing = None if obj.args[1].upper() in {'INF', 'INFINITY'} else obj.number(obj.args[1])
    infinite = object_spacing is None or object_spacing >= Decimal('1e10')
    distance = None if infinite else ir.to_mm(object_spacing, unit)
    if distance is not None and distance <= 0:
        raise obj.unsupported('only positive real-object distances and objects at infinity are supported')
    warnings: list[ImportWarning] = []
    private_names = {m.name for m in materials}
    surfaces = tuple(row.build(str(i), unit, radius_mode, private_names, warnings) for i, row in enumerate(rows, 1))
    previous_medium = None
    for surface in surfaces:
        if previous_medium is None and surface.material is None and not surface.is_stop:
            raise refuse(E.UNSUPPORTED_FEATURE, 'An apertured air-space surface needs STO to become a physical stop', surface.location)
        previous_medium = surface.material
    for surface in surfaces[:-1]:
        if surface.thickness_mm <= 0:
            raise refuse(E.THICKNESS_INVALID, 'Expected positive spacing between surfaces', surface.location)
    if surfaces[-1].material:
        raise refuse(E.UNSUPPORTED_FEATURE, 'The image medium must be air', surfaces[-1].location)
    if sum(s.is_stop for s in surfaces) > 1:
        raise refuse(E.SURFACE_ORDER_INVALID, 'The prescription declares multiple stops', next(s.location for s in reversed(surfaces) if s.is_stop))

    axis_commands = [settings[k] for k in ('XAN', 'YAN', 'XOB', 'YOB') if k in settings]
    axes = {cmd.key[0]: cmd.numbers() for cmd in axis_commands}
    if len({cmd.key[1:] for cmd in axis_commands}) > 1 or len({len(v) for v in axes.values()}) > 1:
        raise refuse(E.VALUE_INVALID, 'Use one field type and equally sized X and Y lists', axis_commands[-1].location)
    field_type = 'height' if axis_commands and axis_commands[0].key.endswith('OB') else 'angle'
    count = len(next(iter(axes.values()))) if axes else 0
    zeros = (Decimal(0),) * count
    fields = tuple(zip(axes.get('X', zeros), axes.get('Y', zeros), strict=True))
    if field_type == 'height':
        fields = tuple((ir.to_mm(x, unit), ir.to_mm(y, unit)) for x, y in fields)
    for key in ('WTF', 'VUX', 'VLX', 'VUY', 'VLY'):
        if key in settings:
            cmd = settings[key]
            if cmd.numbers() != (int(key == 'WTF'),) * (count or 1):
                raise cmd.unsupported('only unit field weights and zero vignetting are supported, one value per field')
    pupils = [settings[k] for k in ('EPD', 'NAO', 'FNO') if k in settings]
    if len(pupils) > 1:
        raise refuse(E.ROW_INVALID, 'Give one pupil specification', pupils[-1].location)
    if pupils and (pupils[0].scalar() <= 0 or (pupils[0].key == 'NAO' and pupils[0].scalar() >= 1)):
        raise refuse(E.VALUE_INVALID, 'Expected positive pupil size or an object NA between zero and one', pupils[0].location)
    temperature = settings['TEM'].scalar() if 'TEM' in settings else Decimal(20)
    if temperature != 20:
        raise settings['TEM'].unsupported('only the standard 20 C material reference is supported')
    wavelengths = settings['WL'].numbers() if 'WL' in settings else ()
    if wavelengths and (min(wavelengths) < 180 or max(wavelengths) > 100000 or len(set(wavelengths)) != len(wavelengths)):
        raise refuse(E.VALUE_INVALID, 'WL wavelengths must be distinct and within 180..100000 nm', settings['WL'].location)
    vacuum = tuple(ir.air_to_vacuum_nm(w, temperature, Decimal(101325)).quantize(ir.VACUUM_WAVELENGTH_QUANTUM_NM) for w in wavelengths)
    weights = settings['WTW'].numbers() if 'WTW' in settings else (Decimal(1),) * len(vacuum)
    if 'WTW' in settings and (len(weights) != len(vacuum) or min(weights) < 0 or max(weights) == 0):
        raise refuse(E.VALUE_INVALID, 'WTW needs one nonnegative weight per wavelength and a positive total', settings['WTW'].location)
    primary = 0
    if 'REF' in settings:
        ref = settings['REF'].scalar()
        if not (1 <= ref <= len(vacuum)) or ref != ref.to_integral_value():
            raise refuse(E.VALUE_INVALID, 'REF must be a one-based index into WL', settings['REF'].location)
        primary = int(ref) - 1

    return ir.Prescription(
        FORMAT_ID, hashlib.sha256(text.encode('utf-8')).hexdigest(), unit, surfaces,
        name=settings['TITLE'].args[0] if 'TITLE' in settings else None, object=ir.ObjectSpace(obj.location, distance, infinite),
        image=image.location, materials=materials, wavelengths_nm=vacuum, wavelength_weights=weights, primary_wavelength_index=primary,
        wavelengths_in=ir.WAVELENGTHS_AIR,
        environment=ir.ReferenceEnvironment(temperature, Decimal(101325), ('temperature_c',) if 'TEM' in settings else ()),
        fields=fields, field_type=field_type, field_location=axis_commands[-1].location if axis_commands else None,
        wavelength_location=settings['WL'].location if wavelengths else None, warnings=tuple(warnings),
        object_na=settings['NAO'].scalar() if 'NAO' in settings else None,
        image_f_number=settings['FNO'].scalar() if 'FNO' in settings else None,
        entrance_pupil_diameter_mm=ir.to_mm(settings['EPD'].scalar(), unit) if 'EPD' in settings else None,
    )
