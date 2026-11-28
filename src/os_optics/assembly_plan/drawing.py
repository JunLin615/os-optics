"""Plan revision -> assembly drawing of your design (drawing schema 4.2), generated and kept on this device.

``plan_drawing(revision, out_dir, context=...)``: project the revision into its LOCAL_PLAN preview package
(``projection``), issue its detail package (``detail``, detail 3.2), run the local layout check on the package and draw
it with the Engine's drawing generator (``os_optics.assembly_drawing``), the parts legend being the plan's design BOM
with who supplies each part. Nothing is uploaded.

Refusals, in this order:

* ``PLAN_HAS_FINDINGS`` -- the plan has ERROR or UNKNOWN findings outside the layout check (``check`` other than
  LAYOUT_VALIDATION; the drawing's validation block cannot state them, so no sheet is drawn, in either mode);
* the plan holds your own hardware (``PLAN_USER_HARDWARE_NOT_PROJECTABLE``, from the projection);
* the layout check did not pass and no diagnostic sheet was asked for: the generator's
  ``DRAWING_REFUSED_VALIDATION_FAILED`` / ``DRAWING_REFUSED_VALIDATION_INCONCLUSIVE`` (``AssemblyDrawingError``).

With ``diagnostic`` a check that did not pass gives the diagnostic sheet (``DrawingOutcome.exit_code`` 2); a passing
check gives the Detailed drawing (exit code 0). ``out_dir`` receives the four drawing files plus ``preview.zip`` (the
package archive) and ``assembly-detail.json``; other files there are left alone.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .._vendor.shared_contracts.assembly_detail import ASSEMBLY_DETAIL_FILE_NAME, AssemblyDetailPackageV1, detail_file_bytes
from .._vendor.shared_contracts.assembly_preview import AssemblyLocalValidationV1, AssemblyPreviewPackageV1
from ..assembly import validate_assembly_layout
from ..assembly_drawing.generate import AssemblyDrawing, generate_assembly_drawing
from ..output_files import replace_files
from .contract import (AssemblyPlanError, AssemblyPlanErrorCode, AssemblyPlanRevisionV1, FindingCheckV1, FindingSeverityV1,
                       FindingV1)
from .detail import project_plan_detail
from .projection import ProjectionContext, project_plan

PREVIEW_ARCHIVE_NAME = "preview.zip"
#: Exit codes of ``assembly plan draw`` (DESIGN 3.1): the Detailed drawing, a diagnostic sheet.
EXIT_DRAWING = 0
EXIT_DIAGNOSTIC = 2
#: Findings that refuse a drawing when they are not the layout check's own.
BLOCKING_SEVERITIES = frozenset({FindingSeverityV1.ERROR, FindingSeverityV1.UNKNOWN})


@dataclass(frozen=True)
class DrawingOutcome:
    """One drawing of a plan revision: the package, its detail, the layout check and the drawing files."""

    package: AssemblyPreviewPackageV1
    detail: AssemblyDetailPackageV1
    validation: AssemblyLocalValidationV1
    drawing: AssemblyDrawing
    written: tuple[Path, ...] = ()

    @property
    def exit_code(self) -> int:
        return EXIT_DRAWING if self.drawing.delivery_ready else EXIT_DIAGNOSTIC

    def files(self) -> dict[str, bytes]:
        """Every file of the outcome: the four drawing files, the package archive and the detail document."""

        return {**dict(self.drawing.files), PREVIEW_ARCHIVE_NAME: self.package.archive_bytes(),
                ASSEMBLY_DETAIL_FILE_NAME: detail_file_bytes(self.detail)}

    def summary(self) -> dict[str, Any]:
        result = self.drawing.result
        return {"drawing_status": result.drawing_status.value, "delivery_ready": result.delivery_ready,
                "schema_version": result.schema_version, "validation_status": self.validation.status.value,
                "package_content_hash": self.package.content_hash, "detail_content_hash": self.detail.detail_content_hash,
                "files": sorted(self.files())}


def blocking_findings(findings: Iterable[FindingV1]) -> list[FindingV1]:
    """The ERROR / UNKNOWN findings a drawing cannot state (every check but the layout check's own)."""

    return [f for f in findings if f.severity in BLOCKING_SEVERITIES and f.check is not FindingCheckV1.LAYOUT_VALIDATION]


def plan_drawing(revision: AssemblyPlanRevisionV1, out_dir: str | Path | None = None, *, context: ProjectionContext,
                 locale: str = "en", diagnostic: bool = False, findings: Iterable[FindingV1] | None = None,
                 validation: AssemblyLocalValidationV1 | None = None) -> DrawingOutcome:
    """Draw ``revision`` (see the module docstring). ``findings``: the plan findings to judge (default the revision's
    recorded ones); ``validation``: a layout check of the same package the caller already ran; ``out_dir``: write the
    files there (None: keep them in memory)."""

    blocking = blocking_findings(revision.findings if findings is None else findings)
    if blocking:
        raise AssemblyPlanError(
            AssemblyPlanErrorCode.PLAN_HAS_FINDINGS,
            "the plan has ERROR or UNKNOWN findings a drawing cannot show: resolve them (assembly plan check) first",
            {"findings": [{"finding_id": f.finding_id, "code": f.code.value, "item_ids": list(f.item_ids)} for f in blocking]})
    projection = project_plan(revision, context)
    package = projection.package
    detail = project_plan_detail(package, revision, context)
    if validation is None or validation.package.package_content_hash != package.content_hash:
        validation = validate_assembly_layout(package)
    drawing = generate_assembly_drawing(package, detail_file_bytes(detail), locale=locale, validation=validation,
                                        diagnostic=diagnostic, design_bom=context.design_bom)
    outcome = DrawingOutcome(package=package, detail=detail, validation=validation, drawing=drawing)
    if out_dir is None:
        return outcome
    directory = replace_files(out_dir, outcome.files())
    return DrawingOutcome(package=package, detail=detail, validation=validation, drawing=drawing,
                          written=tuple(directory / name for name in sorted(outcome.files())))


__all__ = ["DrawingOutcome", "EXIT_DIAGNOSTIC", "EXIT_DRAWING", "PREVIEW_ARCHIVE_NAME", "blocking_findings", "plan_drawing"]
