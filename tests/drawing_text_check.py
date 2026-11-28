"""Build-time gate: every human-readable string a drawing emits comes from its inputs, its templates or is a number.

A test helper, not part of the wheel. It checks the Detailed Assembly Drawing (``assembly_drawing``) and the Optical
Layout Drawing (``optical_layout``). ``install_generation_hook`` (tests/conftest.py) runs it, together with the contract
verifier of the output set, on EVERY drawing and layout any test generates; the parametrised fixture tests run it over
every drawing fixture in both locales.

The drawing must add nothing beyond its verified inputs and the generator's own fixed templates. Every output of a
drawing is tokenized -- a token is a maximal run of ASCII letters and digits, compared
case-insensitively -- and every token must be allowed:

* ``assembly_drawing.svg``: every text node (``<title>``, ``<desc>``, every sheet text; every key and string value of
  the ``<metadata>`` JSON), every attribute value and every element / attribute name; the purely geometric attributes
  (path data, coordinates, sizes, dash arrays, the rotation transform) are instead checked against a fixed numeric
  grammar, and the file must follow the SVG writer's own layout (the XML declaration, then one element per line);
* ``assembly_drawing.pdf``: the whole file read as latin-1 (every text-show string, the Info strings, the operators and
  the object syntax), with every hex string (the Chinese text, the UTF-16BE title) decoded and checked as text;
* ``drawing_result.json`` and ``drawing_manifest.json``: every string value.

A token is allowed when it is

1. a token of a public field of the verified inputs that the generator actually reads (``input_vocabulary``: OpenSci
   SKUs, display names, part / line / mate / operation refs, interface and thread designations, instance ids, path keys,
   enum values, the setting values and units, the resolution status, the bindings' hashes and ids, the release, the
   dimension refs, and the local assembly validation of the same package), or the ``HASH_SHORT`` prefix of one of
   those hashes;
2. a token of the generator's own fixed template vocabulary (``template_vocabulary``): every string and bytes literal
   (docstrings and bare string statements excluded) of the ``assembly_drawing`` modules and of the bundled
   ``assembly_detail`` contract, derived from their source at run time, plus the engine version and build id; or
3. a number: digits, a ``<digits>x<digits>`` count, or a 64-hex digest.

``unexpected_text`` lists the file and the place of each string holding another token, never the token.
"""


from __future__ import annotations

import ast
import contextlib
import functools
import json
import re
from collections.abc import Iterable, Iterator, Mapping
from importlib import resources
from typing import Any

from os_optics._vendor.shared_contracts.assembly_detail import (
    DRAWING_MANIFEST_PATH,
    DRAWING_PDF_PATH,
    DRAWING_RESULT_PATH,
    DRAWING_SVG_PATH,
    VerifiedAssemblyDetailV1,
    verify_drawing_outputs,
)
from os_optics._vendor.shared_contracts.assembly_preview import AssemblyLocalValidationV1
from os_optics._vendor.shared_contracts.optical_layout import OpticalLayoutPackageV1
from os_optics._vendor.shared_contracts.optical_layout.drawing import LAYOUT_MANIFEST_PATH, LAYOUT_PDF_PATH, LAYOUT_SVG_PATH
from os_optics._version import ENGINE_VERSION

#: Length of the short digests the title block prints.
HASH_SHORT = 12

#: A token: a maximal run of ASCII letters and digits, or one CJK ideograph (Chinese text is checked character by
#: character: every ideograph of an output must occur in a template or an input field).
_TOKEN = re.compile(r"[A-Za-z0-9]+|[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
#: A PDF hex string of the drawing's writer (UCS-2BE text of the Chinese font, or the UTF-16BE document title).
_PDF_HEX_TEXT = re.compile(r"(?<!<)<((?:[0-9A-F]{4})+)>(?!>)")
_NUMBER = re.compile(r"[0-9]+(?:x[0-9]+)?|[0-9a-f]{64}")
_HASH_VALUE = re.compile(r"[0-9a-f]{32,}")
_NUM = r"-?[0-9]+(?:\.[0-9]+)?"
#: A string that is never allowed (a place whose content does not follow its fixed grammar).
REJECTED = "\x00"
#: The SVG writer's layout: an XML declaration, then one element per line (open, close, empty, or with text).
_SVG_DECLARATION = '<?xml version="1.0" encoding="UTF-8"?>'
_SVG_LINE = re.compile(r'</?(?P<tag>[A-Za-z]+)(?P<attrs>(?: [A-Za-z:-]+="[^"<>]*")*)/?>(?:(?P<text>[^<>]*)</(?P=tag)>)?')
_SVG_ATTRIBUTE = re.compile(r' ([A-Za-z:-]+)="([^"<>]*)"')
#: Geometric SVG attributes: checked against a numeric grammar instead of being tokenized.
_GEOMETRY_ATTRIBUTES: dict[str, re.Pattern[str]] = {
    "d": re.compile(r"[MLZ0-9.\- ]+"),
    "x": re.compile(_NUM), "y": re.compile(_NUM), "cx": re.compile(_NUM), "cy": re.compile(_NUM), "r": re.compile(_NUM),
    "font-size": re.compile(_NUM), "stroke-width": re.compile(_NUM),
    "stroke-dasharray": re.compile(f"{_NUM}(?: {_NUM})*"), "viewBox": re.compile(f"{_NUM}(?: {_NUM}){{3}}"),
    "width": re.compile(f"{_NUM}(?:mm)?"), "height": re.compile(f"{_NUM}(?:mm)?"),
    "transform": re.compile(f"rotate\\({_NUM} {_NUM} {_NUM}\\)"),
}

#: Public scene fields the generator reads (``*`` = every list item; a trailing list of strings is read whole).
SCENE_FIELDS = (
    "parts.*.part_ref", "parts.*.identity.opensci_sku", "parts.*.identity.display_name", "parts.*.commercial_line_ref",
    "parts.*.category", "parts.*.support.relation", "parts.*.support.parent_ref", "parts.*.support.mate_ref",
    "mates.*.mate_ref", "mates.*.interface_designation", "mates.*.part_a_ref", "mates.*.fastener.kind",
    "mates.*.fastener.thread_designation",
    "settings.*.part_ref", "settings.*.kind", "settings.*.value", "settings.*.unit", "settings.*.basis",
    "fixations.*.part_ref", "fixations.*.mode", "fixations.*.manual_operation_ref", "fixations.*.fastener.thread_designation",
    "manual_operations.*.operation_ref", "manual_operations.*.part_ref", "manual_operations.*.kind",
    "dof_implementations.*.axis", "dof_implementations.*.design_instance_id", "dof_implementations.*.status",
    "dof_implementations.*.requested_control", "dof_implementations.*.delivered_control",
    "dof_implementations.*.mechanism", "dof_implementations.*.travel_basis",
    "dof_implementations.*.implemented_by_part_ref", "dof_implementations.*.moved_part_ref",
    "dof_implementations.*.optic_part_ref",
    "breadboard.part_ref", "breadboard.hole_grid.thread_designation",
    "beam_reference.beam_axes.*.path_key", "beam_reference.beam_axes.*.basis",
    "beam_reference.design_anchors.*.instance_id", "beam_reference.design_anchors.*.anchor_kind",
    "beam_reference.design_anchors.*.customer_supply.reason", "beam_reference.design_anchors.*.customer_supply.optical_role",
    "beam_reference.design_anchors.*.customer_supply.opensci_sku",
    "notes.*.code", "notes.*.part_ref", "notes.*.design_instance_id", "notes.*.dof_axis",
    "resolution.passed_checks",
)
#: Public manifest fields (``**`` = every string below).
MANIFEST_FIELDS = ("package_content_hash", "binding.project.**", "binding.local_validation_hash.**",
                   "binding.commercial_resolution_id", "binding.commercial_bom.**", "binding.basis", "binding.local_plan.**")
DETAIL_FIELDS = ("detail_content_hash", "release.**", "dimensions.*.dim_ref", "dimensions.*.kind", "dimensions.*.basis",
                 "dimensions.*.axis", "dimensions.*.subject_part_ref", "customer_supplied_models.**",
                 "line_display_names_zh_cn.**")
VALIDATION_FIELDS = ("status", "result_hash", "checker.checker_version", "checker.runtime_kind", "checks.*.check", "issues.*.code",
                     "issues.*.severity", "issues.*.part_refs", "issues.*.design_instance_id", "summary.interference")


def tokens(text: str) -> set[str]:
    """The lowercase tokens of ``text``."""

    return {t.lower() for t in _TOKEN.findall(text)}


def is_number(token: str) -> bool:
    return _NUMBER.fullmatch(token) is not None


# ============================================================================== vocabulary


def _literal_tokens(source: str) -> set[str]:
    tree = ast.parse(source)
    skipped = {id(node.value) for node in ast.walk(tree)
               if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)}
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and id(node) not in skipped:
            if isinstance(node.value, str):
                out |= tokens(node.value)
            elif isinstance(node.value, bytes):
                out |= tokens(node.value.decode("latin-1"))
    return out


#: The packages whose literals are the template vocabulary of the assembly drawing.
TEMPLATE_PACKAGES = ("os_optics.assembly_drawing", "os_optics._vendor.shared_contracts.assembly_detail")


def _source_tokens(sources: Iterable[tuple[str, tuple[str, ...] | None]]) -> frozenset[str]:
    """Tokens of every literal of the given packages' ``.py`` sources (``None``: every module), read from the source
    tree, plus the engine version. A template changed at run time (a test's monkeypatch) is not in it."""

    words: set[str] = set(tokens(ENGINE_VERSION))
    for package, names in sources:
        for child in sorted(resources.files(package).iterdir(), key=lambda c: c.name):
            if child.name.endswith(".py") and (names is None or child.name in names):
                words |= _literal_tokens(child.read_bytes().decode("utf-8"))
    return frozenset(words)


@functools.cache
def template_vocabulary() -> frozenset[str]:
    """Tokens of every literal of the drawing generator and of its drawing contract, plus the engine version."""

    return _source_tokens((package, None) for package in TEMPLATE_PACKAGES)


def _strings_below(node: Any) -> Iterator[str]:
    stack = [node]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            yield item
        elif isinstance(item, dict):
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)


def _select(node: Any, path: tuple[str, ...]) -> Iterator[str]:
    if not path:
        if isinstance(node, str):
            yield node
        elif isinstance(node, list):
            yield from (item for item in node if isinstance(item, str))
        return
    head, rest = path[0], path[1:]
    if head == "**":
        yield from _strings_below(node)
    elif head == "*":
        if isinstance(node, list):
            for item in node:
                yield from _select(item, rest)
    elif isinstance(node, dict) and head in node:
        yield from _select(node[head], rest)


def selected_strings(payload: Mapping[str, Any], fields: Iterable[str]) -> list[str]:
    return [value for field in fields for value in _select(payload, tuple(field.split(".")))]


def input_vocabulary(detail: VerifiedAssemblyDetailV1, validation: AssemblyLocalValidationV1) -> frozenset[str]:
    """Tokens of the public input fields the generator reads (and the short prefixes of their hashes)."""

    preview = detail.preview
    values = (selected_strings(preview.scene.payload(), SCENE_FIELDS)
              + selected_strings(preview.manifest.payload(), MANIFEST_FIELDS)
              + [preview.manifest.package_content_hash]
              + selected_strings(detail.detail.payload(), DETAIL_FIELDS)
              + selected_strings(validation.payload(), VALIDATION_FIELDS))
    words: set[str] = set()
    for value in values:
        words |= tokens(value)
        if _HASH_VALUE.fullmatch(value):
            words |= tokens(value[:HASH_SHORT])
    return frozenset(words)


def allowed_vocabulary(detail: VerifiedAssemblyDetailV1, validation: AssemblyLocalValidationV1,
                       generator_values: Iterable[str] = ()) -> frozenset[str]:
    """Inputs + templates + the generator identity values of this run (engine version, build id)."""

    extra: set[str] = set()
    for value in generator_values:
        extra |= tokens(value)
    return input_vocabulary(detail, validation) | template_vocabulary() | frozenset(extra)


# ============================================================================== the check


def _svg_strings(data: bytes) -> Iterator[tuple[str, str]]:
    """(place, string) of everything human-readable in the SVG; a geometric attribute off its grammar is yielded as
    an unconditionally rejected place."""

    try:
        lines = data.decode("utf-8").split("\n")
    except UnicodeDecodeError:
        yield "file (not UTF-8)", REJECTED
        return
    if lines[0] != _SVG_DECLARATION or lines[-1] != "":
        yield "file (not the generator's SVG layout)", REJECTED
    for number, line in enumerate(lines[1:-1], start=2):
        match = _SVG_LINE.fullmatch(line)
        if match is None:  # the generator writes one element per line; anything else is not its output
            yield f"line {number} (not the generator's SVG layout)", REJECTED
            continue
        tag = match["tag"]
        yield f"<{tag}>", tag
        for name, value in _SVG_ATTRIBUTE.findall(match["attrs"]):
            yield f"<{tag}> attribute name", name
            grammar = _GEOMETRY_ATTRIBUTES.get(name)
            if grammar is None:
                yield f"<{tag}> {name}", _unescape(value)
            elif grammar.fullmatch(value) is None:
                yield f"<{tag}> {name} (not geometry)", REJECTED
        text = match["text"]
        if text and tag == "metadata":  # JSON: every key and string value (JSON literals are syntax)
            try:
                document = json.loads(_unescape(text))
            except (ValueError, RecursionError):
                yield f"<{tag}> text (not JSON)", REJECTED
            else:
                for where, value in _json_strings(document, keys=True):
                    yield f"<{tag}> text {where}", value
        elif text:
            yield f"<{tag}> text", _unescape(text)


def _unescape(text: str) -> str:
    for entity, raw in (("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&amp;", "&")):
        text = text.replace(entity, raw)
    return text


def _json_strings(document: Any, *, keys: bool = False) -> Iterator[tuple[str, str]]:
    """(path, string) of every string value of a parsed JSON document (and, with ``keys``, of every key)."""

    stack: list[tuple[str, Any]] = [("", document)]
    while stack:
        where, item = stack.pop()
        if isinstance(item, str):
            yield where or "value", item
        elif isinstance(item, dict):
            for key, value in item.items():
                path = f"{where}.{key}" if where else str(key)
                if keys:
                    yield f"{path} (key)", str(key)
                stack.append((path, value))
        elif isinstance(item, list):
            stack.extend((f"{where}[]", value) for value in item)


def pdf_strings(name: str, data: bytes) -> Iterator[tuple[str, str]]:
    """(place, string) of a drawing PDF: each hex string decoded as UCS-2BE text (a leading byte-order mark dropped),
    and the rest of the file read as latin-1."""

    text = data.decode("latin-1")
    for match in _PDF_HEX_TEXT.finditer(text):
        raw = bytes.fromhex(match.group(1))
        decoded = raw.decode("utf-16-be", errors="replace")
        yield f"{name}: text string", decoded[1:] if decoded.startswith("\ufeff") else decoded
    yield f"{name}: file", _PDF_HEX_TEXT.sub(" ", text)


def output_strings(files: Mapping[str, bytes]) -> Iterator[tuple[str, str]]:
    """(file: place, string) of every human-readable string of a drawing output set."""

    for place, text in _svg_strings(files[DRAWING_SVG_PATH]):
        yield f"{DRAWING_SVG_PATH}: {place}", text
    yield from pdf_strings(DRAWING_PDF_PATH, files[DRAWING_PDF_PATH])
    for name in (DRAWING_RESULT_PATH, DRAWING_MANIFEST_PATH):
        for place, text in _json_strings(json.loads(files[name])):
            yield f"{name}: {place}", text


def unexpected_text(files: Mapping[str, bytes], allowed: frozenset[str]) -> list[str]:
    """Human-safe problems (file, place and count; never the token) of every output string holding a token that is
    not allowed; empty = the outputs add nothing beyond their inputs and templates."""

    counts: dict[str, int] = {}
    for where, text in output_strings(files):
        if text == REJECTED:
            counts[where] = counts.get(where, 0) + 1
            continue
        bad = sum(1 for token in tokens(text) if token not in allowed and not is_number(token))
        if bad:
            counts[where] = counts.get(where, 0) + bad
    return [f"{where}: {n} token(s) outside the verified inputs and the generator templates" for where, n in sorted(counts.items())]


def drawing_problems(files: Mapping[str, bytes], detail: VerifiedAssemblyDetailV1, validation: AssemblyLocalValidationV1,
                     generator_values: Iterable[str] = ()) -> list[str]:
    """Every text problem of a drawing output set, then every problem the contract verifier finds in it."""

    return unexpected_text(files, allowed_vocabulary(detail, validation, generator_values)) + list(verify_drawing_outputs(files))


# ============================================================================== the Optical Layout Drawing

#: A colour attribute of the SVG (a beam path's wavelength display colour): checked against this grammar, not tokenized.
_COLOUR = re.compile(r"#[0-9a-f]{6}")
_COLOUR_PLACES = (" fill", " stroke")
#: (package, module names or None for every module): the sources whose literals are the layout's template vocabulary.
LAYOUT_TEMPLATE_SOURCES = (
    ("os_optics.optical_layout", None),
    ("os_optics.assembly_drawing", ("canvas.py", "svg.py", "pdf.py", "text.py", "layout.py")),
    ("os_optics._vendor.shared_contracts.optical_layout", None),
)


@functools.cache
def layout_template_vocabulary() -> frozenset[str]:
    """Tokens of every literal of the layout generator, its drawing backend and its contract, plus the engine version."""

    return _source_tokens(LAYOUT_TEMPLATE_SOURCES)


def layout_input_vocabulary(package: OpticalLayoutPackageV1) -> frozenset[str]:
    words: set[str] = set()
    for _, value in _json_strings(package.model_dump(mode="json", by_alias=True)):
        words |= tokens(value)
        if _HASH_VALUE.fullmatch(value):
            words |= tokens(value[:HASH_SHORT])
    return frozenset(words)


def layout_unexpected_text(files: Mapping[str, bytes], package: OpticalLayoutPackageV1,
                           generator_values: Iterable[str] = ()) -> list[str]:
    """Human-safe problems (file, place, count) of every layout output string holding a token that is not allowed."""

    allowed = set(layout_input_vocabulary(package)) | set(layout_template_vocabulary())
    for value in generator_values:
        allowed |= tokens(str(value))
    strings = [(f"{LAYOUT_SVG_PATH}: {place}", text) for place, text in _svg_strings(files[LAYOUT_SVG_PATH])]
    strings += list(pdf_strings(LAYOUT_PDF_PATH, files[LAYOUT_PDF_PATH]))
    strings += [(f"{LAYOUT_MANIFEST_PATH}: {place}", text) for place, text in _json_strings(json.loads(files[LAYOUT_MANIFEST_PATH]))]
    counts: dict[str, int] = {}
    for where, text in strings:
        if text == REJECTED:
            counts[where] = counts.get(where, 0) + 1
            continue
        if where.endswith(_COLOUR_PLACES) and _COLOUR.fullmatch(text):
            continue
        bad = sum(1 for token in tokens(text) if token not in allowed and not is_number(token))
        if bad:
            counts[where] = counts.get(where, 0) + bad
    return [f"{where}: {n} token(s) outside the package and the generator templates" for where, n in sorted(counts.items())]


def layout_problems(drawing: Any) -> list[str]:  # noqa: ANN401 - os_optics.optical_layout.OpticalLayoutDrawing
    manifest = json.loads(drawing.files[LAYOUT_MANIFEST_PATH])
    return layout_unexpected_text(drawing.files, drawing.package, (str(v) for v in manifest["generator"].values()))


# ============================================================================== the generation hook

_HOOK = {"enabled": True}


@contextlib.contextmanager
def unchecked() -> Iterator[None]:
    """Generate without the hook (a test that injects a token and then calls the check itself)."""

    _HOOK["enabled"] = False
    try:
        yield
    finally:
        _HOOK["enabled"] = True


def install_generation_hook(patch: Any) -> None:  # noqa: ANN401 - a pytest MonkeyPatch
    """Run the text check and the output verifier on every drawing and layout the generators return (``patch``: a
    MonkeyPatch the caller undoes). A problem fails the test that generated the drawing."""

    from os_optics.assembly_drawing import generate as drawing_module
    from os_optics.optical_layout import generate as layout_module

    seen: dict[str, Any] = {}
    facts, drawing_class, layout_class = drawing_module.drawing_facts, drawing_module.AssemblyDrawing, layout_module.OpticalLayoutDrawing

    def capturing_facts(detail, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        seen["detail"] = detail
        return facts(detail, *args, **kwargs)

    def checked_drawing(**fields):  # noqa: ANN003, ANN202
        drawing = drawing_class(**fields)
        if _HOOK["enabled"]:
            generator = drawing.result.generator.model_dump(mode="json").values()
            problems = drawing_problems(drawing.files, seen["detail"], drawing.validation, (str(v) for v in generator))
            assert problems == [], problems
        return drawing

    def checked_layout(**fields):  # noqa: ANN003, ANN202
        drawing = layout_class(**fields)
        if _HOOK["enabled"]:
            problems = layout_problems(drawing)
            assert problems == [], problems
        return drawing

    patch.setattr(drawing_module, "drawing_facts", capturing_facts)
    patch.setattr(drawing_module, "AssemblyDrawing", checked_drawing)
    patch.setattr(layout_module, "OpticalLayoutDrawing", checked_layout)
