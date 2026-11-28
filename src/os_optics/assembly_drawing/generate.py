"""Generate, write and verify the local Detailed Assembly Drawing of one verified preview + detail package pair.

The generator reads the bytes of an opened delivery, a path, or already verified packages. Order of work, all
fail-closed:

1. verify the Assembly Preview Package (canonical contract verifier) and require its commercial binding -- or its
   LOCAL_PLAN binding (a package generated on this device from a local assembly plan), drawn as schema 4.2 with the
   plan's design BOM (``design_bom``, bound by its digest) as the parts legend;
2. verify the Assembly Detail Package against it (top-level version, then the schema BEFORE anything walks the
   untrusted document, content hash, canonical bytes, binding field by field, every dimension measured from the
   preview poses, required drawing facts); a document nested too deeply is refused like any malformed one;
   bytes are verified where they enter: a verified package / detail object is used as it is (one verification);
3. run the local assembly validation (``os_optics.assembly.validate_assembly_layout``) on the same package (or use the
   caller's ``validation`` when it is of this package and this checker build); PASS / WARNING give the
   DETAILED_ASSEMBLY_DRAWING. A check that did not pass is refused unless the caller asks for a diagnostic sheet
   (``diagnostic=True``): FAIL -> ``DRAWING_REFUSED_VALIDATION_FAILED``, UNKNOWN / OUT_OF_MODEL_SCOPE ->
   ``DRAWING_REFUSED_VALIDATION_INCONCLUSIVE``. With ``diagnostic`` the status selects the sheet
   (``DRAWING_STATUS_FOR_VALIDATION``): FAIL -> DIAGNOSTIC_VALIDATION_FAILED, UNKNOWN / OUT_OF_MODEL_SCOPE ->
   DIAGNOSTIC_VALIDATION_INCOMPLETE (both bannered, watermarked, listing their issues, not delivery ready).
4. collect the facts, generate the steps, lay out and render the sheet (SVG, PDF) in the requested locale (``en`` or
   ``zh-CN``; every sheet text comes from the message catalog ``i18n``), build ``AssemblyDrawingResultV1`` and
   ``AssemblyDrawingManifestV1``.

That every output token comes from the verified inputs, the generator's templates or is a number is a property of the
generator, checked at build time over every drawing the tests generate (``tests/drawing_text_check.py``).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .._vendor.shared_contracts.assembly_detail import (
    ASSEMBLY_DETAIL_SCHEMA_ID,
    ASSEMBLY_DETAIL_SCHEMA_VERSIONS,
    ASSEMBLY_DRAWING_GENERATOR_ID,
    ASSEMBLY_DRAWING_RESULT_SCHEMA_ID,
    DRAWING_DESCRIPTION,
    DRAWING_LOCALES,
    DRAWING_MANIFEST_PATH,
    DRAWING_PDF_PATH,
    DRAWING_RESULT_PATH,
    DRAWING_STATUS_FOR_VALIDATION,
    DRAWING_SVG_PATH,
    LOCAL_DRAWING_DESCRIPTION,
    MAX_DETAIL_BYTES,
    AssemblyDetailPackageError,
    AssemblyDetailPackageV1,
    AssemblyDrawingManifestV1,
    AssemblyDrawingResultV1,
    DrawingStatusV1,
    VerifiedAssemblyDetailV1,
    drawing_manifest_for,
    drawing_schema_version_for,
    load_assembly_detail,
    verify_drawing_outputs,
)
from .._vendor.shared_contracts.assembly_preview import (
    ISSUE_RULES,
    AssemblyLocalValidationV1,
    AssemblyPreviewPackageError,
    AssemblyPreviewPackageV1,
    json_file_bytes,
)
from .._vendor.shared_contracts.common.hashes import osc1_digest
from .._version import DISTRIBUTION_NAME, ENGINE_VERSION
from ..assembly import ASSEMBLY_CHECKER_VERSION, load_assembly_preview, validate_assembly_layout
from ..manifest.build import engine_build_id
from ..output_files import replace_files
from .errors import AssemblyDrawingError, DrawingErrorCode
from .alignment import spacing_rows
from .geometry import VIEWS
from .i18n import DEFAULT_LOCALE
from .model import drawing_facts
from .pdf import render_pdf
from .sheet import LayoutError, SheetBuilder
from .steps import assembly_steps, customer_rows, words_for
from .svg import render_svg
from .text import value_text

#: Version of the drawing semantics (layout, views, templates). Bump when a drawing of the same packages could change.
GENERATOR_VERSION = "4.0.0"  # 2.0.0: drawing schema 2.0 (Detailed Assembly Drawing status, description, assembly notes); 2.1.0: customer-supplied components drawn at their design positions, their table and placement steps; 3.0.0: drawing schema 4.0 (artifact-store roles, locale en / zh-CN with Chinese PDF text, Order Number and customer in the title block, one check statement, optical spacing and alignment, 0.1 mm / 0.1 deg, label placement, customer-supplied components located and named as modelled); 3.1.0: title block without references and generator, assembly check as status and interference, description and notes in product words; 3.2.0: beam-axis elements named by the scene's ids, optical spacing between optic centres (the board position's point), the face of an asymmetric optic that meets the beam; 4.0.0: drawing schema 4.1, a check that is not PASS / WARNING selects a diagnostic sheet instead of refusing (DIAGNOSTIC_VALIDATION_INCOMPLETE)
SVG_METADATA_SCHEMA = "opensci.assembly_drawing.svg_metadata"
DRAWING_FILES = (DRAWING_SVG_PATH, DRAWING_PDF_PATH, DRAWING_RESULT_PATH, DRAWING_MANIFEST_PATH)

#: The contract's model-validator message for a document whose ``detail_content_hash`` is not its osc1 digest.
HASH_MISMATCH_MESSAGE = "detail_content_hash does not match the document"

PreviewSource = str | Path | bytes | bytearray | Mapping[str, bytes] | AssemblyPreviewPackageV1
DetailSource = str | Path | bytes | bytearray | memoryview | VerifiedAssemblyDetailV1
OutputSource = str | Path | Mapping[str, bytes]


@dataclass(frozen=True)
class AssemblyDrawing:
    """One generated drawing: the four output files and their parsed result / manifest."""

    files: Mapping[str, bytes]
    result: AssemblyDrawingResultV1
    manifest: AssemblyDrawingManifestV1
    validation: AssemblyLocalValidationV1

    @property
    def drawing_status(self) -> DrawingStatusV1:
        return self.result.drawing_status

    @property
    def delivery_ready(self) -> bool:
        return self.result.delivery_ready

    def write(self, out_dir: str | Path) -> Path:
        """Write exactly the four files into ``out_dir`` (created if needed) and return it.

        The four files of an earlier drawing there are replaced (a drawing is regenerated from the same packages); other
        files in the directory are left alone. The set is written to temporary names first and moved into place only
        once complete (``os_optics.output_files``)."""

        return replace_files(out_dir, {name: self.files[name] for name in DRAWING_FILES})


# ============================================================================== inputs


def load_preview(source: PreviewSource) -> AssemblyPreviewPackageV1:
    """Verify an Assembly Preview Package (any source ``os_optics.assembly`` accepts) or raise
    ``PREVIEW_PACKAGE_INVALID``; a package bound neither to a commercial resolution and its Customer Purchase BOM nor to
    a local assembly plan (LOCAL_PLAN) raises ``PREVIEW_NOT_COMMERCIALLY_BOUND``."""

    try:
        package = load_assembly_preview(source)
    except AssemblyPreviewPackageError as exc:
        raise AssemblyDrawingError(DrawingErrorCode.PREVIEW_PACKAGE_INVALID, exc.problems) from None
    except OSError as exc:
        raise AssemblyDrawingError(DrawingErrorCode.PREVIEW_PACKAGE_INVALID, (f"cannot read the package: {type(exc).__name__}",)) from None
    except RecursionError:  # a JSON document (manifest, scene, GLB JSON chunk) nested too deeply to walk
        raise AssemblyDrawingError(DrawingErrorCode.PREVIEW_PACKAGE_INVALID, ("a package document is nested too deeply",)) from None
    except (TypeError, ValueError) as exc:
        raise AssemblyDrawingError(DrawingErrorCode.PREVIEW_PACKAGE_INVALID, (f"not a package: {type(exc).__name__}",)) from None
    binding = package.manifest.binding
    if not binding.local and (binding.commercial_resolution_id is None or binding.commercial_bom is None):
        raise AssemblyDrawingError(DrawingErrorCode.PREVIEW_NOT_COMMERCIALLY_BOUND, (
            "the preview package is not bound to a commercial resolution and its Customer Purchase BOM",))
    return package


def _detail_bytes(source: DetailSource) -> bytes:
    if isinstance(source, memoryview):
        return source.tobytes()
    if isinstance(source, (bytes, bytearray)):
        return bytes(source)
    if isinstance(source, (str, Path)):
        try:
            return Path(source).read_bytes()
        except OSError as exc:
            raise AssemblyDrawingError(DrawingErrorCode.DETAIL_PACKAGE_MALFORMED, (f"cannot read the detail package: {type(exc).__name__}",)) from None
    raise AssemblyDrawingError(DrawingErrorCode.INPUT_INVALID, ("a detail package is a path, its bytes or a verified detail",))


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [k for k, _ in pairs]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate JSON object key")
    return dict(pairs)


def load_assembly_detail_package(source: DetailSource, preview: PreviewSource, *,
                                 expected_content_hash: str | None = None) -> VerifiedAssemblyDetailV1:
    """Verify an Assembly Detail Package against its preview package; raise ``AssemblyDrawingError``.

    Codes: ``DETAIL_PACKAGE_MALFORMED`` (not JSON, not the contract's shape, not canonical bytes),
    ``DETAIL_UNSUPPORTED_VERSION``, ``DETAIL_CONTENT_HASH_MISMATCH``, ``DETAIL_BINDING_MISMATCH`` (another preview
    package, Project, Local Validation, resolution or BOM) and ``DETAIL_DIMENSION_INVALID``. A
    ``VerifiedAssemblyDetailV1`` is returned as it is when its preview is this preview package (content hash)."""

    package = load_preview(preview)
    if isinstance(source, VerifiedAssemblyDetailV1):
        if source.preview.content_hash != package.content_hash:
            raise AssemblyDrawingError(DrawingErrorCode.DETAIL_BINDING_MISMATCH, (
                "binding: preview package content hash is not the bound preview package's",))
        if expected_content_hash is not None and source.content_hash != expected_content_hash:
            raise AssemblyDrawingError(DrawingErrorCode.DETAIL_CONTENT_HASH_MISMATCH, ("the detail package is not the expected one",))
        return source
    data = _detail_bytes(source)
    malformed = DrawingErrorCode.DETAIL_PACKAGE_MALFORMED
    if len(data) > MAX_DETAIL_BYTES:
        raise AssemblyDrawingError(malformed, (f"detail package exceeds {MAX_DETAIL_BYTES} bytes",))
    # Untrusted bytes: parse (bounded, never raises out), check the version on the top level only, then validate
    # against the contract schema BEFORE anything walks the document recursively (hashing, canonical form).
    try:
        document = json.loads(data, object_pairs_hook=_no_duplicates)
    except RecursionError:
        raise AssemblyDrawingError(malformed, ("detail package is nested too deeply",)) from None
    except ValueError as exc:  # also UnicodeDecodeError, lone surrogates, int digit limits
        raise AssemblyDrawingError(malformed, (f"detail package is not JSON: {type(exc).__name__}",)) from None
    if not isinstance(document, dict):
        raise AssemblyDrawingError(malformed, ("detail package is not a JSON object",))
    if document.get("schema") != ASSEMBLY_DETAIL_SCHEMA_ID or document.get("schema_version") not in ASSEMBLY_DETAIL_SCHEMA_VERSIONS:
        raise AssemblyDrawingError(DrawingErrorCode.DETAIL_UNSUPPORTED_VERSION, (
            f"supported: {ASSEMBLY_DETAIL_SCHEMA_ID} {' / '.join(ASSEMBLY_DETAIL_SCHEMA_VERSIONS)}",))
    try:  # the model re-computes detail_content_hash (osc1) over the validated, schema-bounded document
        parsed = AssemblyDetailPackageV1.model_validate_json(data)
    except ValidationError as exc:
        errors = exc.errors(include_input=False, include_url=False)
        if any(HASH_MISMATCH_MESSAGE in str(e.get("msg", "")) for e in errors):
            raise AssemblyDrawingError(DrawingErrorCode.DETAIL_CONTENT_HASH_MISMATCH, (f"{HASH_MISMATCH_MESSAGE}",)) from None
        where = ".".join(str(p) for p in errors[0].get("loc", ())) if errors else ""
        raise AssemblyDrawingError(malformed, (f"not a valid assembly detail package ({where or 'document'})",)) from None
    except (RecursionError, ValueError) as exc:
        raise AssemblyDrawingError(malformed, (f"not a valid assembly detail package: {type(exc).__name__}",)) from None
    if expected_content_hash is not None and parsed.detail_content_hash != expected_content_hash:
        raise AssemblyDrawingError(DrawingErrorCode.DETAIL_CONTENT_HASH_MISMATCH, ("the detail package is not the expected one",))
    try:
        return load_assembly_detail(data, package)
    except AssemblyDetailPackageError as exc:
        problems = exc.problems
    except RecursionError:  # pragma: no cover - the schema bounds the nesting of a validated document
        raise AssemblyDrawingError(malformed, ("detail package is nested too deeply",)) from None
    for prefix, code in (("assembly detail package is not in canonical", malformed), ("not a valid", malformed),
                         ("binding:", DrawingErrorCode.DETAIL_BINDING_MISMATCH), ("dim-", DrawingErrorCode.DETAIL_DIMENSION_INVALID),
                         ("missing required drawing fact", DrawingErrorCode.MISSING_REQUIRED_FACT)):
        hit = [p for p in problems if p.startswith(prefix)]
        if hit:
            raise AssemblyDrawingError(code, hit)
    raise AssemblyDrawingError(malformed, problems)


# ============================================================================== result


def _ratio(scale: float) -> str:
    return f"{value_text(scale)}:1" if scale >= 1.0 else f"1:{value_text(1.0 / scale)}"


#: The issue status effects an incomplete check lists on its diagnostic sheet: what could not be completed.
INCOMPLETE_EFFECTS = frozenset({"UNKNOWN", "OUT_OF_MODEL_SCOPE"})


def _line(issue) -> str:  # noqa: ANN001 - the vendored AssemblyIssueV1
    return f"{issue.code.value}: {', '.join(issue.part_refs) or '-'}"


def _issue_lines(validation: AssemblyLocalValidationV1, status: DrawingStatusV1) -> list[str]:
    """The issues a sheet lists: for an incomplete check, those that left it incomplete (``*_NOT_EVALUATED``,
    ``GEOMETRY_*``, ``ENGAGEMENT_NOT_BOUNDED`` ...); otherwise the ERROR issues, else the WARNING ones."""

    if status is DrawingStatusV1.DIAGNOSTIC_VALIDATION_INCOMPLETE:
        return [_line(i) for i in validation.issues if ISSUE_RULES[i.code].status_effect.value in INCOMPLETE_EFFECTS]
    return [_line(i) for i in validation.issues if i.severity.value == "ERROR"] or [
        _line(i) for i in validation.issues if i.severity.value == "WARNING"]


def _metadata(detail: VerifiedAssemblyDetailV1, validation: AssemblyLocalValidationV1, status: DrawingStatusV1,
              views: dict[str, str], record, dims: list[dict[str, Any]], locale: str) -> dict[str, Any]:  # noqa: ANN001
    manifest = detail.preview.manifest
    binding = manifest.binding
    release = detail.detail.release
    plan = binding.local_plan
    return {
        "schema": SVG_METADATA_SCHEMA,
        "schema_version": LOCAL_SCHEMA_VERSION if binding.local else COMMERCIAL_SCHEMA_VERSION,
        "generator_id": ASSEMBLY_DRAWING_GENERATOR_ID,
        "generator_version": GENERATOR_VERSION,
        "locale": locale,
        "order_number": release.order_number,
        "customer_display_name": release.customer_display_name,
        "drawing_status": status.value,
        "delivery_ready": status is DrawingStatusV1.DETAILED_ASSEMBLY_DRAWING,
        "description": LOCAL_DRAWING_DESCRIPTION if binding.local else DRAWING_DESCRIPTION,
        **({"plan_id": plan.plan_id, "plan_revision": plan.revision, "plan_digest": plan.plan_digest.digest,
            "design_bom_hash": plan.design_bom.bom_hash.digest} if plan is not None else {}),
        "project_hash": binding.project.project_hash.digest,
        "local_validation_hash": binding.local_validation_hash.digest if binding.local_validation_hash else None,
        "commercial_resolution_id": binding.commercial_resolution_id,
        "commercial_bom_hash": binding.commercial_bom.bom_hash.digest if binding.commercial_bom else None,
        "preview_package_content_hash": manifest.package_content_hash,
        "preview_package_revision": manifest.package_revision,
        "detail_content_hash": detail.detail.detail_content_hash,
        "release_basis": detail.detail.release.basis.value,
        "validation_status": validation.status.value,
        "validation_checker_version": validation.checker.checker_version,
        "validation_interference": validation.summary.interference.value,
        **({} if status is DrawingStatusV1.DETAILED_ASSEMBLY_DRAWING else {
            "validation_checks_not_passed": [c.check.value for c in validation.checks if c.status.value not in ("PASS", "NOT_APPLICABLE")]}),
        "views": views,
        "balloons": {ref: {"line_ref": detail.preview.scene.part(ref).commercial_line_ref, "views": v}
                     for ref, v in sorted(record.balloon_views.items())},
        "dimensions": {d["dim_ref"]: {k: d[k] for k in ("kind", "basis", "axis", "value_mm", "views", "subject_part_ref")} for d in dims},
    }


def _usable_validation(validation: AssemblyLocalValidationV1 | None, preview: AssemblyPreviewPackageV1) -> bool:
    """Whether a caller's validation is the running checker's result for this very package."""

    if validation is None:
        return False
    if not isinstance(validation, AssemblyLocalValidationV1):
        raise AssemblyDrawingError(DrawingErrorCode.INPUT_INVALID, ("validation is an AssemblyLocalValidationV1",))
    return (validation.package.package_content_hash == preview.content_hash
            and validation.checker.checker_version == ASSEMBLY_CHECKER_VERSION
            and validation.checker.engine_build_id == engine_build_id())


def legend_supply_of(preview: AssemblyPreviewPackageV1, design_bom: Mapping[str, Any] | None) -> dict[str, str]:
    """Who supplies each part of a LOCAL_PLAN package (``{part_ref: supply}``) from its design BOM document, which must
    be the one the package binds (``bom_digest`` = the binding's design BOM hash = the osc1 digest of the document
    without that field) and must name every part with a design BOM line. Raises ``MISSING_REQUIRED_FACT``."""

    binding = preview.manifest.binding
    plan = binding.local_plan
    if plan is None:
        return {}
    problem = "the drawing of a local assembly plan needs the design BOM the package binds"
    if not isinstance(design_bom, Mapping):
        raise AssemblyDrawingError(DrawingErrorCode.MISSING_REQUIRED_FACT, (problem,))
    try:
        body = {k: v for k, v in design_bom.items() if k != "bom_digest"}
        if design_bom.get("bom_digest") != plan.design_bom.bom_hash.digest or osc1_digest(body) != plan.design_bom.bom_hash.digest:
            raise ValueError(problem)
        supply = {item_id: line["supply"] for line in design_bom["lines"] for item_id in line["item_ids"]}
    except (KeyError, TypeError, ValueError):
        raise AssemblyDrawingError(DrawingErrorCode.MISSING_REQUIRED_FACT, (problem,)) from None
    out = {}
    for part in preview.scene.parts:
        if part.commercial_line_ref is None:
            continue
        if supply.get(part.part_ref) not in LEGEND_SUPPLIES:
            raise AssemblyDrawingError(DrawingErrorCode.MISSING_REQUIRED_FACT, (
                f"part {part.part_ref} has no supply in the design BOM the package binds",))
        out[part.part_ref] = supply[part.part_ref]
    return out


#: The supplies a 4.2 parts legend row states.
LEGEND_SUPPLIES = frozenset({"OPENSCI", "CUSTOMER_OWNED", "UNRESOLVED"})
COMMERCIAL_SCHEMA_VERSION = "4.1"
LOCAL_SCHEMA_VERSION = "4.2"


def generate_assembly_drawing(preview_source: PreviewSource, detail_source: DetailSource, *,
                              expected_preview_content_hash: str | None = None,
                              expected_detail_content_hash: str | None = None, locale: str = DEFAULT_LOCALE,
                              validation: AssemblyLocalValidationV1 | None = None, diagnostic: bool = False,
                              design_bom: Mapping[str, Any] | None = None) -> AssemblyDrawing:
    """Generate the drawing of a preview package and its detail package (see the module docstring), written in
    ``locale`` (``en`` or ``zh-CN``). ``validation``: the local assembly validation the caller already ran (e.g. with
    its own tolerances); it is used when it is of this package and the running checker build, otherwise the check
    runs here with the default tolerances. ``diagnostic``: draw a check that did not pass as a diagnostic sheet instead
    of refusing it. ``design_bom``: the design BOM document of a LOCAL_PLAN package (required for one; its parts legend
    states who supplies each part)."""

    if locale not in DRAWING_LOCALES:
        raise AssemblyDrawingError(DrawingErrorCode.INPUT_INVALID, (f"locale must be one of {', '.join(DRAWING_LOCALES)}",))
    preview = load_preview(preview_source)
    if expected_preview_content_hash is not None and preview.content_hash != expected_preview_content_hash:
        raise AssemblyDrawingError(DrawingErrorCode.PREVIEW_PACKAGE_INVALID, ("the preview package is not the expected one",))
    detail = load_assembly_detail_package(detail_source, preview, expected_content_hash=expected_detail_content_hash)
    supply = legend_supply_of(preview, design_bom)
    if not _usable_validation(validation, preview):
        validation = validate_assembly_layout(preview)
    state = validation.status.value
    status = DRAWING_STATUS_FOR_VALIDATION[validation.status]
    if status is not DrawingStatusV1.DETAILED_ASSEMBLY_DRAWING and not diagnostic:
        if state == "FAIL":
            raise AssemblyDrawingError(DrawingErrorCode.DRAWING_REFUSED_VALIDATION_FAILED, (
                "the local assembly validation of this package FAILED; request diagnostic mode for a diagnostic sheet",)
                + tuple(_issue_lines(validation, status)))
        raise AssemblyDrawingError(DrawingErrorCode.DRAWING_REFUSED_VALIDATION_INCONCLUSIVE, (
            f"the local assembly validation of this package is {state}: a drawing needs PASS or WARNING; request diagnostic "
            "mode for a diagnostic sheet that lists what could not be completed",) + tuple(_issue_lines(validation, status)))
    facts = drawing_facts(detail)
    try:
        steps = assembly_steps(facts, locale)
    except ValueError as exc:
        raise AssemblyDrawingError(DrawingErrorCode.MISSING_REQUIRED_FACT, (str(exc),)) from None
    checks_passed = sum(1 for c in validation.checks if c.status.value == "PASS")
    checks_evaluated = len(validation.checks)
    builder = SheetBuilder(facts, drawing_status=status, validation_status=state,
                           validation_issues=_issue_lines(validation, status), checker_version=validation.checker.checker_version,
                           validation_interference=validation.summary.interference.value,
                           steps=steps, generator_version=GENERATOR_VERSION, locale=locale,
                           checks_passed=checks_passed, checks_evaluated=checks_evaluated, legend_supply=supply)
    try:
        canvas = builder.build()
    except LayoutError as exc:
        raise AssemblyDrawingError(DrawingErrorCode.LAYOUT_DOES_NOT_FIT, (str(exc),)) from None
    record = builder.record
    scales = {view.key: _ratio(builder.views[view.key].scale) for view in VIEWS}

    manifest = preview.manifest
    binding = manifest.binding
    local = binding.local
    drawing_binding: dict[str, Any] = {
        "project": binding.project.model_dump(mode="json", by_alias=True, exclude_none=True),
        "preview_package_content_hash": manifest.package_content_hash,
        "preview_package_revision": manifest.package_revision,
        "detail_content_hash": detail.detail.detail_content_hash,
        "release": detail.detail.release.model_dump(mode="json", by_alias=True, exclude_none=True),
    }
    if local:
        drawing_binding["local_plan"] = binding.local_plan.model_dump(mode="json", by_alias=True, exclude_none=True)  # type: ignore[union-attr]
        legend_bom = {"source": "DESIGN_BOM", "design_bom": drawing_binding["local_plan"]["design_bom"]}
    else:
        drawing_binding["commercial_resolution_id"] = binding.commercial_resolution_id
        drawing_binding["commercial_bom"] = binding.commercial_bom.model_dump(mode="json", by_alias=True, exclude_none=True)  # type: ignore[union-attr]
        legend_bom = {"source": "CUSTOMER_PURCHASE_BOM", "commercial_bom": drawing_binding["commercial_bom"]}
    if binding.local_validation_hash is not None:
        drawing_binding["local_validation_hash"] = binding.local_validation_hash.model_dump(mode="json", by_alias=True, exclude_none=True)
    dims = []
    for dim in detail.detail.dimensions:
        views = record.dimension_views.get(dim.dim_ref)
        if not views:
            raise AssemblyDrawingError(DrawingErrorCode.LAYOUT_DOES_NOT_FIT, (f"{dim.dim_ref} could not be drawn",))
        dims.append({"dim_ref": dim.dim_ref, "kind": dim.kind.value, "subject_part_ref": dim.subject_part_ref, "axis": dim.axis.value,
                     "basis": dim.basis.value, "value_mm": value_text(detail.measured[dim.dim_ref].value_mm), "views": views})
    seen_codes: list[str] = []
    for item in preview.scene.notes:
        if item.code.value not in seen_codes:
            seen_codes.append(item.code.value)
    content: dict[str, Any] = {
        "schema": ASSEMBLY_DRAWING_RESULT_SCHEMA_ID,
        "schema_version": LOCAL_SCHEMA_VERSION if local else COMMERCIAL_SCHEMA_VERSION,
        "generator": {"generator_id": ASSEMBLY_DRAWING_GENERATOR_ID, "generator_version": GENERATOR_VERSION,
                      "engine_package": DISTRIBUTION_NAME, "engine_version": ENGINE_VERSION, "engine_build_id": engine_build_id()},
        "binding": drawing_binding,
        "validation": {"status": state, "result_hash": validation.result_hash, "checker_version": validation.checker.checker_version,
                       "runtime_kind": validation.checker.runtime_kind.value, "checks_passed": checks_passed,
                       "checks_evaluated": checks_evaluated},
        "locale": locale,
        "drawing_status": status.value,
        "delivery_ready": status is DrawingStatusV1.DETAILED_ASSEMBLY_DRAWING,
        "description": LOCAL_DRAWING_DESCRIPTION if local else DRAWING_DESCRIPTION,
        "sheet": builder.sheet.key,
        "views": [{"view": view.key, "scale": scales[view.key], "part_refs": record.views[view.key].visible_parts,
                   "visible_segment_count": record.views[view.key].visible_segments,
                   "hidden_segment_count": record.views[view.key].hidden_segments} for view in VIEWS],
        "dimensions": dims,
        "balloons": [{"commercial_line_ref": p.line_ref, "part_ref": p.part_ref, "views": record.balloon_views[p.part_ref]}
                     for p in facts.bom_parts()],
        "parts_legend": {**legend_bom,
                         "rows": [{"commercial_line_ref": p.line_ref, "part_ref": p.part_ref, "opensci_sku": p.sku,
                                   "display_name": p.display_name, **({"supply": supply[p.part_ref]} if local else {})}
                                  for p in sorted(facts.bom_parts(), key=lambda p: (p.line_ref, p.part_ref))]},
        "assembly_steps": [{"step": n, "action": s.action, "text": s.text,
                            **({"part_ref": s.part_ref} if s.part_ref else {}),
                            **({"design_instance_id": s.design_instance_id} if s.design_instance_id else {}),
                            **({"commercial_line_ref": s.line_ref} if s.line_ref else {})} for n, s in enumerate(steps, start=1)],
        "customer_supplied_components": [row.result() for row in customer_rows(facts, locale)],
        "optical_spacing": [r for r in (row.result() for row in spacing_rows(facts, words_for(facts, locale))) if r is not None],
        "notes": seen_codes,
    }
    try:
        result = AssemblyDrawingResultV1.model_validate({**content, "result_hash": osc1_digest(content)})
    except ValidationError as exc:
        errors = exc.errors(include_input=False, include_url=False)
        where = ".".join(str(p) for p in errors[0].get("loc", ())) if errors else ""
        raise AssemblyDrawingError(DrawingErrorCode.MISSING_REQUIRED_FACT, (f"the drawing result does not validate at {where}",)) from None

    if result.schema_version != drawing_schema_version_for(result.binding):  # pragma: no cover - the model checks it
        raise AssemblyDrawingError(DrawingErrorCode.MISSING_REQUIRED_FACT, ("the drawing result states another version",))
    metadata = _metadata(detail, validation, status, scales, record, dims, locale)
    words = words_for(facts, locale)
    title = words({DrawingStatusV1.DETAILED_ASSEMBLY_DRAWING: "title",
                   DrawingStatusV1.DIAGNOSTIC_VALIDATION_FAILED: "title.diagnostic",
                   DrawingStatusV1.DIAGNOSTIC_VALIDATION_INCOMPLETE: "title.incomplete"}[status])
    source = (f"{binding.local_plan.plan_id} r{binding.local_plan.revision}" if local  # type: ignore[union-attr]
              else binding.commercial_resolution_id)
    description = words("svg.desc", title=title, resolution=source, preview=manifest.package_content_hash,
                        revision=manifest.package_revision, detail=detail.detail.detail_content_hash,
                        description=words("description"))
    svg = render_svg(canvas, title, description, metadata, locale=locale)
    pdf = render_pdf(canvas, title)
    files = {DRAWING_SVG_PATH: svg, DRAWING_PDF_PATH: pdf, DRAWING_RESULT_PATH: json_file_bytes(result.payload())}
    drawing_manifest = drawing_manifest_for(result, files)
    files[DRAWING_MANIFEST_PATH] = json_file_bytes(drawing_manifest.payload())
    return AssemblyDrawing(files=dict(files), result=result, manifest=drawing_manifest, validation=validation)


# ============================================================================== verification


def drawing_output_files(source: OutputSource, *, known_only: bool = False) -> dict[str, bytes]:
    """The files of an output directory (top level only) or of a ``{name: bytes}`` map. ``known_only`` reads only the
    four drawing file names of a directory (whatever else it holds is not read; see ``unrelated_output_entries``)."""

    if isinstance(source, Mapping):
        return {str(k): bytes(v) if isinstance(v, (bytes, bytearray, memoryview)) else v for k, v in source.items()}  # type: ignore[misc]
    directory = Path(source)
    if not directory.is_dir():
        raise AssemblyDrawingError(DrawingErrorCode.DRAWING_OUTPUTS_INVALID, ("no drawing output directory there",))
    if known_only:
        return {name: (directory / name).read_bytes() for name in DRAWING_FILES if (directory / name).is_file()}
    return {p.name: p.read_bytes() for p in sorted(directory.iterdir()) if p.is_file()}


def unrelated_output_entries(directory: str | Path) -> list[str]:
    """Names of the entries of an output directory that are not one of the four drawing files (sorted)."""

    path = Path(directory)
    return sorted(p.name for p in path.iterdir() if p.name not in DRAWING_FILES) if path.is_dir() else []


def verify_assembly_drawing(source: OutputSource) -> tuple[str, ...]:
    """Every problem of a drawing output set (a directory written by ``AssemblyDrawing.write`` or a file map); empty =
    valid. Strict: the contract verifier checks the exact file set (no extra file), the manifest, sizes, SHA-256, the
    result and their agreement. (The output-text allow-list is a property checked at generation time: it needs the
    verified input packages.) Never raises on malformed outputs."""

    try:
        files = drawing_output_files(source)
    except AssemblyDrawingError as exc:
        return exc.problems
    except OSError as exc:
        return (f"cannot read the drawing outputs: {type(exc).__name__}",)
    try:
        return tuple(verify_drawing_outputs(files))
    except RecursionError:
        return ("a drawing output document is nested too deeply",)
    except (TypeError, ValueError) as exc:
        return (f"the drawing outputs cannot be read: {type(exc).__name__}",)
