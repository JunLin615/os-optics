"""Read a sequential .zmx text prescription into the shared, millimetre-based IR."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .._vendor.shared_contracts.design_catalog.material_physics import AIR_MODEL_BAND_NM
from .errors import ImportWarning, PrescriptionErrorCode as E, SourceLocation, refuse
from .ir import (
    MM_PER_UNIT, UNIT_SPELLINGS, VACUUM_WAVELENGTH_QUANTUM_NM, CoatingRef, IrSagShape,
    IrSurface, MaterialRef, ObjectSpace, Prescription, ReferenceEnvironment, WAVELENGTHS_AIR,
    air_to_vacuum_nm, power_to_mm, reciprocal, to_mm,
)

FORMAT_ID = ".zmx"
# File, drawing, optimization and analysis settings which do not change the frozen sequential geometry.
_HEADER_METADATA = set("VERS LANG NOTE PFIL SDMA OMMA ROPD PICB GLRS GSTD NSCD COFN HYPR LUID MAZH VOLF".split())
_SURFACE_METADATA = set("COMM HIDE MIRR SLAB POPS".split())
_FROZEN_SOLVES = {"MAZH", "PZUP", "VDSZ"}
# Explicit end-of-prescription records. Only known merit, tolerance and inactive configuration records can follow.
_ANALYSIS = set("BLNK TOL MOFF REAX REAY REAB DIFF OPDC EFFL RWCE APMN TRAR RAID PARB CONF DMFS OPER RMSW RSCE RSCH REAI REAR SUMM DIVI PROD DIFF MNCG MXCG MNEG MXEG MNCA MXCA MNCT MXCT MNDT MXDT MNGA MXGA MNEA MXEA MNCG MXCG MNAG MXAG MTFA MTFS REAA REAN RANG GLCM GLCZ".split())
_GLOBAL = set("MODE NAME UNIT GCAT ENVD FTYP XFLN YFLN FWGN WAVM WAVL WWGT PWAV ENPD OBNA FNUM FLOA".split())
_ZERO_VIGNETTING = {"VDXN", "VDYN", "VCXN", "VCYN", "VANN"}
_SURFACE = set("SURF TYPE CURV DISZ GLAS DIAM MEMA CONI PARM CLAP FLAP COAT STOP".split())


@dataclass(frozen=True)
class _Record:
    key: str
    args: tuple[str, ...]
    where: SourceLocation

    def word(self, index: int = 0) -> str:
        if index >= len(self.args):
            raise refuse(E.FIELD_MISSING, f"{self.key} requires a value at position {index + 1}", self.where)
        return self.args[index]

    def number(self, index: int = 0) -> Decimal:
        token = self.word(index)
        try:
            value = Decimal(token)
        except InvalidOperation:
            value = Decimal("NaN")
        if not value.is_finite() or value and not -300 <= value.adjusted() <= 300:
            raise refuse(E.VALUE_INVALID, f"{self.key} expects a finite decimal, got {token!r}", self.where)
        return value

    def integer(self, index: int = 0) -> int:
        n = self.number(index)
        if n != n.to_integral_value() or abs(n) > 1_000_000:
            raise refuse(E.VALUE_INVALID, f"{self.key} expects an integer index or count", self.where)
        return int(n)

    def positive(self) -> Decimal:
        n = self.number()
        if n <= 0:
            raise refuse(E.VALUE_INVALID, f"{self.key} requires a positive value", self.where)
        return n

    def unsupported(self, detail: str):
        return refuse(E.UNSUPPORTED_FEATURE, f"{self.key} {detail}", self.where)


class _Block(dict[str, _Record]):
    def __init__(self):
        super().__init__()
        self.indexed: dict[str, dict[int, _Record]] = {}

    def add(self, record: _Record) -> None:
        if record.key in {"PARM", "WAVM"}:
            table = self.indexed.setdefault(record.key, {})
            key = record.integer()
            if key < 1 or key in table:
                raise refuse(E.ROW_INVALID, f"invalid or repeated {record.key} index {key}", record.where)
            table[key] = record
        else:
            if record.key in self:
                raise refuse(E.ROW_INVALID, f"repeated {record.key} record", record.where)
            self[record.key] = record

    def require(self, key: str) -> _Record:
        if key not in self:
            where = next((r.where for r in self.values()), None)
            raise refuse(E.FIELD_MISSING, f"missing {key} record", where)
        return self[key]


def _blocks(text: str) -> tuple[_Block, list[_Block]]:
    header, surfaces = _Block(), []
    current = header
    for row, line in enumerate(text.lstrip("\ufeff").splitlines(), 1):
        words = line.split()
        if not words or words[0].startswith(("!", "#")):
            continue
        key = words[0].upper()
        surface = str(len(surfaces) - 1) if surfaces else None
        record = _Record(key, tuple(words[1:]), SourceLocation(row, key, surface, "surfaces" if surfaces else "system"))
        if key == "SURF":
            index = record.integer()
            if index != len(surfaces):
                raise refuse(E.SURFACE_ORDER_INVALID, f"expected SURF {len(surfaces)}, got {index}", record.where)
            current = _Block()
            surfaces.append(current)
            current.add(_Record(key, record.args, SourceLocation(row, key, str(index), "surfaces")))
        elif key in _GLOBAL and not surfaces or key in (_SURFACE | _FROZEN_SOLVES) and surfaces:
            current.add(record)
        elif key in _ZERO_VIGNETTING and not surfaces:
            if any(record.number(i) for i in range(len(record.args))):
                raise record.unsupported("specifies field-dependent pupil vignetting")
        elif key == "RAIM" and not surfaces:
            if record.integer() != 0:
                raise record.unsupported("enables non-default ray aiming")
        elif key in {"GFAC", "PUSH"} and not surfaces:
            if any(record.number(i) for i in range(len(record.args))):
                raise record.unsupported("contains nonzero system factors")
        elif key == "POLS" and not surfaces:
            if record.args != ("1", "0", "1", "0", "0", "1", "0"):
                raise record.unsupported("declares a polarization state outside the supported source convention")
        elif key == "FIMP" and surfaces:
            if record.args:
                raise record.unsupported("references an external surface file")
        elif key == "MNUM":
            if record.integer() != 1:
                raise record.unsupported("has multiple configurations. Open the file with read_prescription_project to select all states")
        elif (key in _HEADER_METADATA and not surfaces or key in _SURFACE_METADATA and surfaces
              or key in _ANALYSIS and surfaces):
            continue
        else:
            raise record.unsupported("is outside the supported sequential prescription records")
    if len(surfaces) < 3:
        raise refuse(E.SURFACE_ORDER_INVALID, "the prescription needs an object, at least one optical surface, and an image")
    return header, surfaces


def _environment(header: _Block) -> ReferenceEnvironment:
    rec = header.get("ENVD")
    if rec is None:
        return ReferenceEnvironment(Decimal(20), Decimal(101325))
    temperature, pressure = rec.number(), rec.number(1) * 101325
    if temperature <= Decimal("-273.15") or pressure < 0:
        raise refuse(E.VALUE_INVALID, "invalid operating temperature or pressure", rec.where)
    if len(rec.args) > 2 and rec.number(2):
        raise rec.unsupported("requires thermal scaling of the source geometry")
    return ReferenceEnvironment(temperature, pressure, ("temperature_c", "pressure_pa"))


def _spectrum(header: _Block, count: int | None, env: ReferenceEnvironment):
    waves = header.indexed.get("WAVM", {})
    if waves:
        if "WAVL" in header or "WWGT" in header:
            raise refuse(E.ROW_INVALID, "use one wavelength record convention in a prescription", next(iter(waves.values())).where)
        count = len(waves) if count is None else count
        if not 1 <= count <= len(waves) or any(i not in waves for i in range(1, count + 1)):
            raise refuse(E.FIELD_MISSING, "the active wavelength rows do not match the declared count", next(iter(waves.values())).where)
        points = [(waves[i], waves[i].number(1), waves[i].number(2)) for i in range(1, count + 1)]
    else:
        wav = header.require("WAVL")
        count = len(wav.args) if count is None else count
        if not 1 <= count <= len(wav.args):
            raise refuse(E.FIELD_MISSING, "the active wavelengths must be present in WAVL", wav.where)
        weights = header.get("WWGT")
        points = [(wav, wav.number(i), weights.number(i) if weights else Decimal(1)) for i in range(count)]
    spectrum, weights = [], []
    for record, um, weight in points:
        nm = um * 1000
        if not AIR_MODEL_BAND_NM[0] <= nm <= AIR_MODEL_BAND_NM[1] or weight < 0:
            raise refuse(E.VALUE_INVALID, "wavelength is outside the reference air model or its weight is negative", record.where)
        vacuum = air_to_vacuum_nm(nm, env.temperature_c, env.pressure_pa)
        spectrum.append(vacuum.quantize(VACUUM_WAVELENGTH_QUANTUM_NM))
        weights.append(weight)
    primary = header["PWAV"].integer() - 1 if "PWAV" in header else 0
    if not 0 <= primary < len(spectrum) or not any(weights):
        raise refuse(E.VALUE_INVALID, "the spectrum needs a valid primary index and nonzero total weight", points[0][0].where)
    return tuple(spectrum), tuple(weights), primary, points[0][0].where


def _fields(header: _Block, unit: str):
    ftyp = header.get("FTYP")
    kind = ftyp.integer() if ftyp else 0
    n = ftyp.integer(2) if ftyp else max((len(header[k].args) for k in ("XFLN", "YFLN") if k in header), default=1)
    if kind not in (0, 1) or n < 1 or n > max((len(header[k].args) for k in ("XFLN", "YFLN") if k in header), default=1):
        raise refuse(E.UNSUPPORTED_FEATURE, "FTYP requires angle fields (0) or object heights (1) and a count matching "
                     "the supplied field coordinates", ftyp.where if ftyp else None)
    factor = MM_PER_UNIT[unit] if kind == 1 else 1
    fields = tuple(tuple(header[k].number(i) * factor if k in header else Decimal(0) for k in ("XFLN", "YFLN")) for i in range(n))
    return fields, "height" if kind else "angle"


def _distance(record: _Record, unit: str) -> Decimal | None:
    return None if record.word().upper() in {"INF", "INFINITY"} else to_mm(record.number(), unit)


def _check_end(block: _Block, *, image: bool = False) -> None:
    forbidden = ("GLAS", "COAT", "STOP") if image else ("GLAS", "COAT", "CLAP", "FLAP", "STOP")
    for key in forbidden:
        if key in block and not (key == "GLAS" and block[key].word().upper() == "AIR"):
            raise block[key].unsupported("on the object or image requires a model for the external medium or aperture")
    diameter = block.get("DIAM")
    if not image and diameter and len(diameter.args) > 1 and diameter.integer(1) == 1 and diameter.number():
        raise diameter.unsupported("defines a clipped object plane. Export the object field positions and a separate physical aperture")
    if "TYPE" in block and block["TYPE"].word().upper() != "STANDARD":
        raise block["TYPE"].unsupported("on the object or image requires a nonplanar conjugate")
    for key in ("CURV", "CONI"):
        if key in block and block[key].number():
            raise block[key].unsupported("defines a nonplanar object or image")
    if any(p.number(1) for p in block.indexed.get("PARM", {}).values()):
        raise block["SURF"].unsupported("defines a profiled object or image")


def _aperture(block: _Block, unit: str) -> tuple[Decimal, Decimal] | None:
    aperture = block.get("CLAP") or block.get("FLAP")
    if aperture is None:
        return None
    if "CLAP" in block and "FLAP" in block:
        raise aperture.unsupported("conflicts with a second aperture record")
    if len(aperture.args) > 3 or len(aperture.args) > 2 and aperture.number(2):
        raise aperture.unsupported("has an unsupported aperture flag")
    inner, outer = (to_mm(aperture.number(i), unit) for i in (0, 1))
    if not 0 <= inner < outer:
        raise refuse(E.VALUE_INVALID, "the circular aperture requires 0 <= inner < outer radius", aperture.where)
    return inner, outer


def _image_radius(block: _Block, unit: str) -> Decimal | None:
    aperture = _aperture(block, unit)
    if aperture:
        inner, outer = aperture
        if inner:
            raise (block.get("CLAP") or block["FLAP"]).unsupported("defines an annular image plane")
        return outer
    diameter = block.get("DIAM")
    if diameter and len(diameter.args) > 1 and diameter.integer(1) == 1:
        if diameter.number() < 0:
            raise refuse(E.VALUE_INVALID, "the image aperture radius must be nonnegative", diameter.where)
        return to_mm(diameter.number(), unit) or None
    return None


def _surface(block: _Block, unit: str, warnings: list[ImportWarning]) -> IrSurface:
    where = block["SURF"].where
    kind = block["TYPE"].word().upper() if "TYPE" in block else "STANDARD"
    if kind not in {"STANDARD", "EVENASPH"}:
        raise block["TYPE"].unsupported(f"{kind} requires a different surface geometry")
    curvature = block["CURV"].number() if "CURV" in block else Decimal(0)
    radius = None
    if curvature:
        # Reciprocal in source units, followed by exact unit scaling.
        inverse, exact = reciprocal(curvature)
        radius = to_mm(inverse, unit)
        if not exact:
            warnings.append(ImportWarning("CONVERSION_ROUNDED", "the reciprocal curvature was rounded to 28 significant digits", block["CURV"].where))
    asphere = [Decimal(0)] * 8
    sag = None
    for i, param in block.indexed.get("PARM", {}).items():
        value = param.number(1)
        if not value:
            continue
        if kind != "EVENASPH" or not 1 <= i <= 9:
            raise param.unsupported(f"parameter {i} has no supported mapping for {kind}")
        coeff, exact = power_to_mm(value, unit, 1 - 2 * i)
        if not exact:
            warnings.append(ImportWarning("CONVERSION_ROUNDED", "the asphere coefficient was rounded to 28 significant digits", param.where))
        if i == 1:
            # A2*(x²+y²) is exactly an additive two-variable sag with a unit normalization radius.
            sag = IrSagShape("XY_POLYNOMIAL", normalization_radius_mm=Decimal(1), terms=((2, 0, coeff), (0, 2, coeff)))
        else:
            asphere[i - 2] = coeff
    while asphere and not asphere[-1]:
        asphere.pop()
    glass = block.get("GLAS")
    name = glass.word().upper() if glass else "AIR"
    if name.startswith("___") or glass and len(glass.args) > 1 and glass.integer(1) not in (0,):
        raise glass.unsupported("requires a resolved catalog material with its dispersion law")
    mirror = name == "MIRROR"
    material = None if name in {"MIRROR", "AIR"} else MaterialRef(name, glass.where)
    coating = block.get("COAT")
    clear = to_mm(block.require("DIAM").number(), unit)
    mechanical = to_mm(block["MEMA"].number(), unit) if "MEMA" in block else None
    hole = None
    aperture = _aperture(block, unit)
    if aperture:
        inner, outer = aperture
        if inner and not mirror:
            raise (block.get("CLAP") or block["FLAP"]).unsupported("specifies an annulus on a transmitting surface")
        hole, clear = inner or None, outer
    if clear <= 0:
        raise refuse(E.VALUE_INVALID, "the optical surface requires a positive clear semi-diameter", block["DIAM"].where)
    return IrSurface(where.surface, where, clear, radius_mm=radius, conic=block["CONI"].number() if "CONI" in block else None,
                     aspheric_mm=tuple(asphere), sag=sag, thickness_mm=_distance(block.require("DISZ"), unit), material=material,
                     mechanical_semi_diameter_mm=mechanical or None, is_stop="STOP" in block, is_mirror=mirror,
                     coating=CoatingRef(coating.word().upper(), coating.where) if coating else None, hole_radius_mm=hole,
                     has_clear_aperture=aperture is not None,
                     automatic_mechanical_edge="MEMA" in block and len(block["MEMA"].args) > 1 and block["MEMA"].integer(1) == 0)


def read_zmx(text: str, *, default_unit: str = "mm", requested_unit: str | None = None,
             ignore_columns: tuple[str, ...] = ()) -> Prescription:
    if ignore_columns:
        raise refuse(E.OPTIONS_INVALID, "ignore_columns applies to a table with column headers")
    header, blocks = _blocks(text)
    mode = header.require("MODE")
    if mode.word().upper() != "SEQ":
        raise mode.unsupported("must be SEQ for a sequential prescription")
    unit = UNIT_SPELLINGS.get(header["UNIT"].word().lower()) if "UNIT" in header else default_unit
    if unit not in MM_PER_UNIT:
        raise refuse(E.UNITS_INVALID, "unsupported length unit", header["UNIT"].where if "UNIT" in header else mode.where)
    if requested_unit and requested_unit != unit:
        raise refuse(E.UNITS_CONFLICT, f"the file states {unit}, the requested unit is {requested_unit}",
                     header["UNIT"].where if "UNIT" in header else mode.where)
    env = _environment(header)
    for key in ("ENPD", "OBNA", "FNUM"):
        record = header.get(key)
        if record and any(record.number(i) for i in range(1, len(record.args))):
            raise record.unsupported("declares additional pupil or telecentricity settings")
    ftyp = header.get("FTYP")
    if ftyp and ftyp.integer(3) < 1:
        raise refuse(E.VALUE_INVALID, "the active wavelength count must be positive", ftyp.where)
    waves, weights, primary, wave_location = _spectrum(header, ftyp.integer(3) if ftyp else None, env)
    fields, field_type = _fields(header, unit)
    obj, image = blocks[0], blocks[-1]
    _check_end(obj)
    _check_end(image, image=True)
    warnings = []
    for block in blocks:
        for key in sorted(_FROZEN_SOLVES & block.keys()):
            warnings.append(ImportWarning("FROZEN_SURFACE_SOLVE", f"{key} uses the saved numeric surface values. "
                                          "Edits in the Project use fixed geometry.", block[key].where))
    surfaces = tuple(_surface(block, unit, warnings) for block in blocks[1:-1])
    for surf, block in zip(surfaces[:-1], blocks[1:-2]):
        if surf.thickness_mm is None:
            raise refuse(E.THICKNESS_INVALID, "an interior surface cannot be followed by infinite spacing", block["DISZ"].where)
    object_distance = _distance(obj.require("DISZ"), unit)
    if sum(k in header for k in ("ENPD", "OBNA", "FNUM", "FLOA")) > 1:
        raise refuse(E.ROW_INVALID, "the source declares conflicting system aperture definitions")
    return Prescription(
        FORMAT_ID, hashlib.sha256(text.encode("utf-8")).hexdigest(), unit, surfaces,
        name=" ".join(header["NAME"].args).strip('"') or None if "NAME" in header else None,
        object=ObjectSpace(obj["SURF"].where, object_distance, object_distance is None), image=image["SURF"].where,
        image_at_infinity=surfaces[-1].thickness_mm is None, warnings=tuple(warnings),
        band_nm=(min(waves), max(waves)), wavelengths_nm=waves, wavelength_weights=weights, primary_wavelength_index=primary,
        wavelengths_in=WAVELENGTHS_AIR, environment=env, glass_catalogs=header["GCAT"].args if "GCAT" in header else (),
        object_na=header["OBNA"].positive() if "OBNA" in header else None,
        entrance_pupil_diameter_mm=to_mm(header["ENPD"].positive(), unit) if "ENPD" in header else None,
        image_f_number=header["FNUM"].positive() if "FNUM" in header else None,
        image_semi_diameter_mm=_image_radius(image, unit),
        fields=fields, field_type=field_type, field_location=ftyp.where if ftyp else None, wavelength_location=wave_location,
        field_weights=tuple(header["FWGN"].number(i) for i in range(len(fields))) if "FWGN" in header else (),
    )
