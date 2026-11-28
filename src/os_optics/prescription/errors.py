"""Structured errors of prescription import.

An import either produces a complete set of models or fails with ``PrescriptionImportError`` listing every problem
found; a partial model is never returned. Each problem has a stable ``PrescriptionErrorCode``, a message that says what
is wrong and what to do, and a location: the ``row`` (the line of the file, counting from 1), the ``column`` (its header
name), the ``surface`` (its label as written) and the ``section`` (``surfaces`` / ``materials`` / ``coatings``) where they
apply.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


#: The error code of the JSON API envelope of a refused import.
PRESCRIPTION_IMPORT_INVALID = "PRESCRIPTION_IMPORT_INVALID"


class PrescriptionErrorCode(StrEnum):
    """Why a prescription is refused (stable values).

    Reading: ``OPTIONS_INVALID`` (an unknown option or a bad option value), ``FORMAT_UNSUPPORTED`` (a format the
    import does not read), ``TEXT_INVALID`` (not UTF-8 text, empty, an unterminated quote), ``DIRECTIVE_INVALID`` (an
    unknown ``#!`` directive or a bad directive value), ``UNITS_INVALID`` (a unit that is not mm, cm, m or in),
    ``UNITS_CONFLICT`` (the caller asked for one unit and the file states another),
    ``SECTION_INVALID`` (an unknown or repeated ``[section]``), ``COLUMN_UNKNOWN``, ``COLUMN_DUPLICATE``,
    ``COLUMN_MISSING`` (a required column is absent from the header), ``FIELD_MISSING`` (a required value is empty),
    ``VALUE_INVALID`` (not a number, a number beyond 1e-300 .. 1e300 in magnitude, a surface label that is not printable
    ASCII, a ``type`` the row's values contradict, or not one of the accepted words), ``ROW_INVALID`` (more cells than header columns),
    ``SURFACE_ORDER_INVALID`` (the object surface is not first, the image surface not last, a repeated label, no
    surface).

    Physics: ``THICKNESS_INVALID`` (a zero or negative spacing between two surfaces, an infinite spacing after a surface
    other than the object and the last surface),
    ``GLASS_UNKNOWN`` (a material that is neither defined in the file nor published in the catalog),
    ``GLASS_AMBIGUOUS`` (a name that matches several published materials), ``MATERIAL_INVALID`` (a material definition
    that is incomplete or inconsistent, or whose refractive index is not real and at least 1 throughout its band), ``MATERIAL_OUTSIDE_BAND`` (no part of the wavelength band lies inside the
    valid band of every material and coating used), ``COATING_UNKNOWN`` (a coating that is neither defined in the file nor published),
    ``COATING_INVALID`` (a coating definition that is incomplete -- a missing grid point, a value missing on a row -- or
    that the coating contract refuses, e.g. R + T above 1; the message is the contract's), ``SURFACE_INVALID`` (a profile the surface model refuses, e.g. a radius
    smaller than the clear semi-diameter), ``SURFACES_INTERSECT`` (two consecutive surfaces meet inside their clear
    aperture), ``EDGE_INVALID`` (a mechanical semi-diameter smaller than the clear one, a surface that does not reach the
    element's outline, glass of zero or negative thickness at the outline), ``BODIES_INTERSECT`` (two consecutive
    elements, or an element and an aperture-stop plate, overlap), ``UNSUPPORTED_FEATURE`` (decentres and tilts, coordinate
    breaks, non-sequential surfaces, gradient index, diffractive surfaces, mirrors, an ``a2`` term, object or image space
    that is not air, a coated surface with air on both sides -- a coated aperture stop included --, more surfaces or
    elements than a model holds).

    Result: ``MODEL_INVALID`` (the imported model is refused by the Project contract -- the message is the contract's),
    ``PROJECT_INVALID`` (the Project to import into is not valid, or the import conflicts with it).
    """

    OPTIONS_INVALID = "OPTIONS_INVALID"
    FORMAT_UNSUPPORTED = "FORMAT_UNSUPPORTED"
    TEXT_INVALID = "TEXT_INVALID"
    DIRECTIVE_INVALID = "DIRECTIVE_INVALID"
    UNITS_INVALID = "UNITS_INVALID"
    UNITS_CONFLICT = "UNITS_CONFLICT"
    SECTION_INVALID = "SECTION_INVALID"
    COLUMN_UNKNOWN = "COLUMN_UNKNOWN"
    COLUMN_DUPLICATE = "COLUMN_DUPLICATE"
    COLUMN_MISSING = "COLUMN_MISSING"
    FIELD_MISSING = "FIELD_MISSING"
    VALUE_INVALID = "VALUE_INVALID"
    ROW_INVALID = "ROW_INVALID"
    SURFACE_ORDER_INVALID = "SURFACE_ORDER_INVALID"
    THICKNESS_INVALID = "THICKNESS_INVALID"
    GLASS_UNKNOWN = "GLASS_UNKNOWN"
    GLASS_AMBIGUOUS = "GLASS_AMBIGUOUS"
    MATERIAL_INVALID = "MATERIAL_INVALID"
    MATERIAL_OUTSIDE_BAND = "MATERIAL_OUTSIDE_BAND"
    COATING_UNKNOWN = "COATING_UNKNOWN"
    COATING_INVALID = "COATING_INVALID"
    SURFACE_INVALID = "SURFACE_INVALID"
    SURFACES_INTERSECT = "SURFACES_INTERSECT"
    EDGE_INVALID = "EDGE_INVALID"
    BODIES_INTERSECT = "BODIES_INTERSECT"
    UNSUPPORTED_FEATURE = "UNSUPPORTED_FEATURE"
    MODEL_INVALID = "MODEL_INVALID"
    PROJECT_INVALID = "PROJECT_INVALID"


@dataclass(frozen=True)
class SourceLocation:
    """Where in the input a value comes from. Every field is optional; ``row`` is the 1-based line of the file."""

    row: int | None = None
    column: str | None = None
    surface: str | None = None
    section: str | None = None
    configuration: int | None = None

    def as_dict(self) -> dict[str, object]:
        data: dict[str, object] = {}
        for key in ("row", "column", "surface", "section", "configuration"):
            value = getattr(self, key)
            if value is not None:
                data[key] = value
        return data

    def describe(self) -> str:
        parts = []
        if self.configuration is not None:
            parts.append(f"configuration {self.configuration}")
        if self.row is not None:
            parts.append(f"row {self.row}")
        if self.column is not None:
            parts.append(f"column {self.column}")
        if self.surface is not None:
            parts.append(f"surface {self.surface}")
        return ", ".join(parts)

    def at(self, column: str) -> SourceLocation:
        """The same row and surface, column ``column``."""

        return SourceLocation(row=self.row, column=column, surface=self.surface, section=self.section, configuration=self.configuration)


@dataclass(frozen=True)
class PrescriptionProblem:
    code: PrescriptionErrorCode
    message: str
    location: SourceLocation | None = None

    def as_dict(self) -> dict[str, object]:
        data: dict[str, object] = {"code": self.code.value, "message": self.message}
        if self.location is not None and self.location.as_dict():
            data["location"] = self.location.as_dict()
        return data


class PrescriptionImportError(ValueError):
    """The prescription cannot be imported as given; ``problems`` lists every reason found."""

    def __init__(self, problems: list[PrescriptionProblem] | tuple[PrescriptionProblem, ...]) -> None:
        self.problems: tuple[PrescriptionProblem, ...] = tuple(problems)
        super().__init__("; ".join(
            f"{p.code.value}{f' at {p.location.describe()}' if p.location is not None and p.location.describe() else ''}: {p.message}"
            for p in self.problems))

    @property
    def codes(self) -> tuple[PrescriptionErrorCode, ...]:
        return tuple(p.code for p in self.problems)

    def as_dict(self) -> dict[str, object]:
        return {"code": PRESCRIPTION_IMPORT_INVALID, "problems": [p.as_dict() for p in self.problems]}


class ProblemList:
    """Collects problems; ``raise_if_any`` raises them together."""

    def __init__(self) -> None:
        self.items: list[PrescriptionProblem] = []

    def add(self, code: PrescriptionErrorCode, message: str, location: SourceLocation | None = None) -> None:
        self.items.append(PrescriptionProblem(code, message, location))

    def __bool__(self) -> bool:
        return bool(self.items)

    def raise_if_any(self) -> None:
        if self.items:
            raise PrescriptionImportError(self.items)


def refuse(code: PrescriptionErrorCode, message: str, location: SourceLocation | None = None) -> PrescriptionImportError:
    return PrescriptionImportError([PrescriptionProblem(code, message, location)])


@dataclass(frozen=True)
class ImportWarning:
    """Something the import did that the author should know about (the result is exact for what the report states)."""

    code: str
    message: str
    location: SourceLocation | None = None

    def as_dict(self) -> dict[str, object]:
        data: dict[str, object] = {"code": self.code, "message": self.message}
        if self.location is not None and self.location.as_dict():
            data["location"] = self.location.as_dict()
        return data
