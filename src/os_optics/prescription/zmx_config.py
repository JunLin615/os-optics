"""Expand a .zmx configuration table into prescriptions in configuration-number order."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .csv_table import NUMBER_MAX, NUMBER_MIN, NUMBER_PATTERN
from .errors import PrescriptionErrorCode as E, SourceLocation, refuse


_SURFACE_RECORD = {"THIC": "DISZ", "SDIA": "DIAM", "CRVT": "CURV"}
_FIELD_RECORD = {"XFIE": "XFLN", "YFIE": "YFLN"}
_APERTURE_RECORDS = ("FNUM", "ENPD", "OBNA")
_SURFACE_SOLVES = {"PZUP", "PKUP", "MAZH"}
# Includes the operand name, target, configuration, value and ten solve fields.
_FIXED_CELL_WORDS = 14
_REFERENCE_CONFIG = 7
_SOLVE_SCALE = 9


def _index(token: str, location: SourceLocation) -> int:
    if token.isascii() and token.isdecimal():
        try:
            return int(token)
        except ValueError:
            pass
    raise refuse(E.VALUE_INVALID, f"Expected a nonnegative integer index, found {token!r}.", location)


def _number(token: str, location: SourceLocation) -> Decimal:
    if NUMBER_PATTERN.fullmatch(token):
        try:
            value = Decimal(token)
            if not value or NUMBER_MIN <= value.copy_abs() <= NUMBER_MAX:
                return value
        except InvalidOperation:
            pass
    raise refuse(E.VALUE_INVALID, f"Expected a finite configuration value in range, found {token!r}.", location)


@dataclass(frozen=True)
class _Cell:
    operand: str
    index: int
    value: str
    location: SourceLocation


def _cell(words: list[str], row: int, count: int) -> _Cell:
    operand = words[0].upper()
    location = SourceLocation(row=row, column=operand, section="configurations")
    if len(words) < 3:
        raise refuse(E.ROW_INVALID, f"{operand} requires a target and a configuration number.", location)
    index = _index(words[1], location)
    config = _index(words[2], location)
    location = SourceLocation(row=row, column=operand, section="configurations", configuration=config,
                              surface=str(index) if operand in _SURFACE_RECORD else None)
    if not 1 <= config <= count:
        raise refuse(E.VALUE_INVALID, f"Configuration {config} is outside 1 through {count}.", location)
    if operand not in (*_SURFACE_RECORD, *_FIELD_RECORD, "APER", "MOFF"):
        raise refuse(E.UNSUPPORTED_FEATURE, f"Configuration operand {operand!r} is unsupported.", location)
    value_and_solve = words[3:_REFERENCE_CONFIG]
    references = words[_REFERENCE_CONFIG:_SOLVE_SCALE]
    modifiers = words[_SOLVE_SCALE:]
    if operand == "MOFF":
        # The disabled operand has two encodings, with or without a trailing zero.
        if (index or value_and_solve != ['""', '0', '0', '0'] or modifiers[:2] != ['0', '0.0'] or
                modifiers[2:] not in (['""'], ['""', '0'])):
            raise refuse(E.UNSUPPORTED_FEATURE, "Only an empty disabled configuration operand is supported.", location)
    else:
        if len(words) != _FIXED_CELL_WORDS:
            raise refuse(E.ROW_INVALID, f"{operand} requires its value and complete solve fields.", location)
        scale, offset, *solve_data = modifiers
        if (value_and_solve[1:] != ['0', '0', '0'] or solve_data != ['0', '""', '0'] or
                _number(scale, location) != 1 or _number(offset, location) != 0):
            raise refuse(E.UNSUPPORTED_FEATURE, f"{operand} has unsupported solve or parameter fields. "
                         "Supply fixed values for each configuration.", location)
        value = _number(words[3], location)
        if operand in {"APER", "SDIA"} and value <= 0:
            raise refuse(E.VALUE_INVALID, f"{operand} requires a positive aperture.", location)
    for token in references:
        if _index(token, location) < 1:
            raise refuse(E.VALUE_INVALID, "Expected positive reference indices in the configuration cell.", location)
    return _Cell(operand, index, words[3], location)


def _destination(cell: _Cell, records: dict, lines: list[str]) -> tuple[int, int]:
    """Return zero-based line and token indices into the saved prescription."""
    operand, index, location = cell.operand, cell.index, cell.location
    slot = 1
    if operand == "APER":
        if index:
            raise refuse(E.VALUE_INVALID, "APER must address the system aperture (index 0).", location)
        matches = [row for key in _APERTURE_RECORDS for row in records.get((None, key), [])]
    elif operand in _FIELD_RECORD:
        matches = records.get((None, _FIELD_RECORD[operand]), [])
        field_types = records.get((None, "FTYP"), [])
        if len(field_types) == 1:
            definition = lines[field_types[0]].split()
            if len(definition) > 3 and index > _index(definition[3], location):
                raise refuse(E.VALUE_INVALID, f"{operand} addresses undeclared field {index}.", location)
        slot = index
    else:
        matches = records.get((index, _SURFACE_RECORD[operand]), [])
    if len(matches) != 1:
        raise refuse(E.UNSUPPORTED_FEATURE, f"{operand} requires one matching prescription record for target {index}. "
                     f"Found {len(matches)}.", location)
    words = lines[matches[0]].split()
    if not 1 <= slot < len(words):
        raise refuse(E.VALUE_INVALID, f"The prescription has no value for {operand} target {index}.", location)
    if operand == "SDIA" and len(words) > 2 and words[2] not in {'0', '1', '3'}:
        raise refuse(E.UNSUPPORTED_FEATURE, f"SDIA conflicts with the DIAM solve on row {matches[0] + 1}. "
                     "Supply a fixed surface aperture.", location)
    return matches[0], slot


def _fixed_edges(records: dict, surfaces: list[int], lines: list[str], controlled: set) -> dict[int, Decimal]:
    """Automatic mechanical edges take the largest clear radius in a contiguous glass element."""
    edges: dict[int, Decimal] = {}
    element = []
    radii = []
    for surface in surfaces:
        element.append(surface)
        rows = records.get((surface, "DIAM"), [])
        if len(rows) == 1 and ("SDIA", surface) not in controlled:
            words = lines[rows[0]].split()
            if words[2:3] in (['1'], ['3']):
                location = SourceLocation(row=rows[0] + 1, column="DIAM", surface=str(surface), section="surfaces")
                radii.append(_number(words[1], location))
        if (surface, "GLAS") not in records:
            if len(radii) == len(element):
                edges.update(dict.fromkeys(element, max(radii)))
            element = []
            radii = []
    return edges


def _check_dependencies(records: dict, surfaces: list[int], lines: list[str], controlled: set, config: int) -> None:
    """Saved results of surface solves may apply only to the selected configuration."""
    physical = set(surfaces[1:-1])
    fixed_edges = _fixed_edges(records, surfaces[1:-1], lines, controlled)
    for (surface, key), rows in records.items():
        if surface is None or key not in _SURFACE_SOLVES | {"CURV", "GLAS", "DIAM", "MEMA"}:
            continue
        for line in rows:
            words = lines[line].split()
            location = SourceLocation(row=line + 1, column=key, surface=str(surface), section="surfaces",
                                      configuration=config)
            if key in _SURFACE_SOLVES or (key == "CURV" and len(words) > 2 and words[2] != '0') or (
                    key == "GLAS" and len(words) > 2 and words[2] == '2'):
                raise refuse(E.UNSUPPORTED_FEATURE, f"{key} has a surface solve requiring per-configuration resolution. "
                             "Supply fixed values in each configuration.", location)
            if surface in physical and key in {"DIAM", "MEMA"} and len(words) > 2:
                if key == "DIAM" and ("SDIA", surface) in controlled and words[2] in {'0', '1', '3'}:
                    continue
                if key == "MEMA" and words[2] == '0' and _number(words[1], location) == fixed_edges.get(surface):
                    continue
                if words[2] not in {'1', '3'}:
                    raise refuse(E.UNSUPPORTED_FEATURE, f"{key} has a surface aperture solve ({words[2]}) whose saved "
                                 "radius only describes the active configuration. Supply fixed radii for each state.", location)


def configuration_texts(text: str) -> list[str]:
    """Return ordinary .zmx records for each state, retaining the source rows of the base prescription.

    Configuration cell errors use the original operand line, target and configuration. A file without a
    table is returned verbatim. Every cell of a controlled quantity must have an explicit fixed value.
    """
    return configuration_states(text)[1]


def configuration_states(text: str, *, aperture_locations: dict[int, SourceLocation] | None = None) -> tuple[int, list[str]]:
    """Expand the saved states, optionally recording the original APER cell for each state."""
    lines = text.splitlines()
    records: dict[tuple[int | None, str], list[int]] = defaultdict(list)
    surfaces: list[int] = []
    surface = None
    selectors = []
    for line, raw in enumerate(lines):
        words = raw.lstrip('\ufeff').split()
        if not words:
            continue
        key = words[0].upper()
        if key == "MNUM":
            break
        # Some merit operands share names with configuration operands. The fixed-cell
        # layout (including quoted solve data) identifies an out-of-table record.
        if key == "MOFF" or (len(words) == _FIXED_CELL_WORDS and words[-2] == '""' and
                             key in (*_SURFACE_RECORD, *_FIELD_RECORD, "APER", "APMN", "APMX", "PRAM", "WAVE")):
            raise refuse(E.ROW_INVALID, f"Configuration operand {key} precedes its MNUM record.",
                         SourceLocation(row=line + 1, column=key, section="configurations"))
        if key == "CONF":
            selectors.append(line)
        if key == "SURF" and len(words) == 2:
            surface = _index(words[1], SourceLocation(row=line + 1, column="SURF", section="surfaces"))
            surfaces.append(surface)
        records[surface, key].append(line)
    else:
        return 1, [text]

    location = SourceLocation(row=line + 1, column="MNUM", section="configurations")
    if len(words) != 3:
        raise refuse(E.ROW_INVALID, "MNUM requires the configuration count and active configuration.", location)
    count, active = (_index(token, location) for token in words[1:])
    if not 1 <= active <= count:
        raise refuse(E.VALUE_INVALID, "MNUM requires an active configuration between one and the count.", location)
    table_start = line
    base = lines[:table_start]
    for selector in selectors:
        base[selector] = ''

    groups: dict[tuple[str, int], dict[int, _Cell]] = defaultdict(dict)
    present: set[int] = set()
    for line in range(table_start + 1, len(lines)):
        words = lines[line].split()
        if not words:
            continue
        cell = _cell(words, line + 1, count)
        config = cell.location.configuration
        assert config is not None
        present.add(config)
        if cell.operand == "MOFF":
            continue
        group = groups[cell.operand, cell.index]
        if config in group:
            raise refuse(E.ROW_INVALID, f"Duplicate {cell.operand} target {cell.index} in configuration {config}.", cell.location)
        group[config] = cell

    if count > 1 and len(present) != count:
        missing = next(n for n in range(1, count + 1) if n not in present)
        raise refuse(E.ROW_INVALID, f"Missing configuration {missing} from the table.",
                     SourceLocation(row=table_start + 1, column="MNUM", section="configurations", configuration=missing))
    edits: dict[tuple[int, int], dict[int, _Cell]] = {}
    changed = set()
    for group in groups.values():
        first = next(iter(group.values()))
        if len(group) != count:
            missing = next(n for n in range(1, count + 1) if n not in group)
            raise refuse(E.ROW_INVALID, f"Missing value for {first.operand} target {first.index} in configuration {missing}.",
                         SourceLocation(row=first.location.row, column=first.operand, surface=first.location.surface,
                                        configuration=missing, section="configurations"))
        target = _destination(first, records, base)
        edits[target] = group
        for config, cell in group.items():
            if Decimal(cell.value) != Decimal(group[active].value):
                if cell.operand == "SDIA" and any((cell.index, k) in records for k in ("CLAP", "FLAP")):
                    raise refuse(E.UNSUPPORTED_FEATURE, "SDIA varies a surface with a separate aperture record. "
                                 "Supply one explicit clear aperture per state.", cell.location)
                changed.add(config)
    if changed:
        _check_dependencies(records, surfaces, base, set(groups), min(changed))

    result = []
    for config in range(1, count + 1):
        state = base.copy()
        for (line, slot), group in edits.items():
            raw = state[line]
            words = raw.split()
            words[slot] = group[config].value
            if group[config].operand == "APER" and aperture_locations is not None:
                aperture_locations[config] = group[config].location
            if group[config].operand == "SDIA" and len(words) > 2:
                words[2] = '1'  # The operand explicitly sets the clear semi-diameter in this state.
            state[line] = raw[:len(raw) - len(raw.lstrip())] + ' '.join(words)
        result.append('\n'.join(state) + '\n')
    return active, result
