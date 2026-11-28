"""Local Detailed Assembly Drawing outputs: the structured result and the local output manifest.

The user's runtime (``os-optics``) generates the drawing locally from a verified
Assembly Preview Package plus its verified Assembly Detail Package and writes four files::

    assembly_drawing.svg     the sheet: A3, or the smallest larger A-series sheet the assembly needs (deterministic bytes)
    assembly_drawing.pdf     the same sheet as a one-page PDF (deterministic bytes)
    drawing_result.json      AssemblyDrawingResultV1: what the sheet shows, as data
    drawing_manifest.json    AssemblyDrawingManifestV1: binding + every other file's size, media type and SHA-256

The manifest is the local output record that a client artifact upload (``client_assembly_artifact``) wraps; nothing here
uploads or persists. ``verify_drawing_outputs`` checks a received file set.

The drawing shows the resolved assembly in FRONT, SIDE, PLAN and PICTORIAL views with item balloons, dimensions in
millimetres marked with their basis (``DimensionBasisV1``), a parts legend of the Customer Purchase BOM lines, the
assembly steps and the assembly notes of the scene. Every customer-supplied design component (the customer's light
source, or an optical component OpenSci does not supply) is drawn as a labelled placeholder at its design position, is
listed in a customer-supplied components table apart from the parts legend (``customer_supplied_components``) and has
a placement step (``PLACE_CUSTOMER_SUPPLIED``); OpenSci neither supplies nor mounts it.

The generator runs the local assembly validation of the same preview package first. PASS / WARNING give the
``DETAILED_ASSEMBLY_DRAWING`` (``delivery_ready = true``). A check that did not pass is refused -- FAIL with
``DRAWING_REFUSED_VALIDATION_FAILED``, UNKNOWN / OUT_OF_MODEL_SCOPE with ``DRAWING_REFUSED_VALIDATION_INCONCLUSIVE`` --
unless the caller asks for a diagnostic sheet; with that request the status selects the sheet
(``DRAWING_STATUS_FOR_VALIDATION``): FAIL gives a ``DIAGNOSTIC_VALIDATION_FAILED`` sheet and UNKNOWN / OUT_OF_MODEL_SCOPE
a ``DIAGNOSTIC_VALIDATION_INCOMPLETE`` sheet, both with ``delivery_ready = false`` and the status stated on the sheet.

Schema 4.2: the drawing of a preview package with the LOCAL_PLAN binding (generated on the user's device from a local
assembly plan; preview manifest 3.1, detail 3.2): the binding names the local plan revision (``local_plan``) instead of
the commercial resolution and the Customer Purchase BOM, the parts legend is the plan's design BOM (source
``DESIGN_BOM``, every row stating who supplies the part: ``OPENSCI``, ``CUSTOMER_OWNED`` or ``UNRESOLVED``), item balloons
carry design BOM lines, and the description is ``LOCAL_DRAWING_DESCRIPTION`` ("Assembly drawing of your design ...").
A drawing states 4.1 exactly for the commercial binding and 4.2 exactly for the local one.

Schema 4.1: the status ``DIAGNOSTIC_VALIDATION_INCOMPLETE``; the drawing status is exactly the one its validation
status selects.

Schema 4.0:

* the file roles are the client artifact store's (``DRAWING_SVG`` / ``DRAWING_PDF`` / ``DRAWING_RESULT``, as
  ``client_assembly_artifact.ArtifactFileRoleV1`` names them), so a reader of the manifest downloads a stored drawing
  file by the role the manifest gives; ``LEGACY_DRAWING_ROLE_ALIASES`` maps the schema 3.0 names;
* the sheet is written in one ``locale`` (``en`` or ``zh-CN``); step texts may hold any printable text;
* ``validation`` states how many of the local assembly checks passed (``checks_passed`` of ``checks_evaluated``);
* ``optical_spacing`` lists the design distance between consecutive elements along each nominal beam path;
* a customer-supplied catalog component names the catalog model the design used (``model_display_name``).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, ValidationError, model_validator

from ..assembly_preview.manifest import CommercialBomRefV1, DesignBomRefV1, LocalPlanRefV1
from ..assembly_preview.package import json_file_bytes
from ..assembly_preview.scene import (
    AssemblyNoteCodeV1,
    CustomerSupplyReasonV1,
    DesignRole,
    DisplayText,
    LineRef,
    OpenSciSku,
    PartRef,
)
from ..assembly_preview.validation import AssemblyValidationStatusV1
from ..base import StrictContractModel
from ..common.hashes import HashRef, Sha256Digest, osc1_digest
from ..common.identifiers import BuildVersion, InstanceKey, OpenSciId
from ..optical_design.local_validation import ENGINE_PACKAGE_NAME, EnginePackageName, LocalRuntimeKindV1, ValidatedProjectRefV1
from .detail import CustomerFacingName, DetailReleaseRefV1, DimensionBasisV1, DimensionKindV1, DimensionRef, MeasureAxisV1


ASSEMBLY_DRAWING_RESULT_SCHEMA_ID: Literal["opensci.assembly_drawing.result"] = "opensci.assembly_drawing.result"
ASSEMBLY_DRAWING_MANIFEST_SCHEMA_ID: Literal["opensci.assembly_drawing.manifest"] = "opensci.assembly_drawing.manifest"
ASSEMBLY_DRAWING_SCHEMA_VERSION: Literal["4.1"] = "4.1"
#: 4.2: the drawing of a LOCAL_PLAN preview package (``DrawingBindingV1.local_plan``, legend source ``DESIGN_BOM`` with a
#: ``supply`` on every row, ``LOCAL_DRAWING_DESCRIPTION``).
ASSEMBLY_DRAWING_SCHEMA_VERSION_4_2: Literal["4.2"] = "4.2"
ASSEMBLY_DRAWING_SCHEMA_VERSIONS: tuple[str, ...] = (ASSEMBLY_DRAWING_SCHEMA_VERSION, ASSEMBLY_DRAWING_SCHEMA_VERSION_4_2)
ASSEMBLY_DRAWING_GENERATOR_ID: Literal["opensci.engine.assembly_drawing"] = "opensci.engine.assembly_drawing"

DRAWING_SVG_PATH = "assembly_drawing.svg"
DRAWING_PDF_PATH = "assembly_drawing.pdf"
DRAWING_RESULT_PATH = "drawing_result.json"
DRAWING_MANIFEST_PATH = "drawing_manifest.json"
SVG_MEDIA_TYPE: Literal["image/svg+xml"] = "image/svg+xml"
PDF_MEDIA_TYPE: Literal["application/pdf"] = "application/pdf"
JSON_MEDIA_TYPE: Literal["application/json"] = "application/json"
MAX_DRAWING_FILE_BYTES = 32 * 1024 * 1024

DRAWING_DESCRIPTION: Literal[
    "Detailed Assembly Drawing of your OpenSci system. Dimensions in millimetres, each marked with its basis. Item "
    "balloons carry the Customer Purchase BOM line of each part."
] = (
    "Detailed Assembly Drawing of your OpenSci system. Dimensions in millimetres, each marked with its basis. Item "
    "balloons carry the Customer Purchase BOM line of each part."
)
LOCAL_DRAWING_DESCRIPTION: Literal[
    "Assembly drawing of your design. Dimensions in millimetres, each marked with its basis. Item balloons carry the "
    "design BOM line of each part."
] = (
    "Assembly drawing of your design. Dimensions in millimetres, each marked with its basis. Item balloons carry the "
    "design BOM line of each part."
)
#: Who supplies a part of a 4.2 parts legend (the plan item's supply).
LegendSupplyV1 = Literal["OPENSCI", "CUSTOMER_OWNED", "UNRESOLVED"]

#: The languages a drawing sheet is written in.
DrawingLocaleV1 = Literal["en", "zh-CN"]
DRAWING_LOCALES: tuple[str, ...] = ("en", "zh-CN")

StepText = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=600, pattern=r"^[^\x00-\x1f\x7f<>]+$"),
    Field(description="Assembly step text from fixed templates (in the drawing's locale), naming BOM lines and OpenSci products."),
]
ValueText = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=24, pattern=r"^-?[0-9]+(\.[0-9]+)?$")]
ScaleLabel = Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9]+(\.[0-9]+)?:[0-9]+(\.[0-9]+)?$")]


class DrawingStatusV1(StrEnum):
    """What the generated document is.

    * ``DETAILED_ASSEMBLY_DRAWING`` -- the Detailed Assembly Drawing of an assembly whose local validation passed;
    * ``DIAGNOSTIC_VALIDATION_FAILED`` -- a diagnostic sheet of an assembly whose local validation FAILED (the failing
      checks are listed on the sheet);
    * ``DIAGNOSTIC_VALIDATION_INCOMPLETE`` -- a diagnostic sheet of an assembly whose local validation is UNKNOWN or
      OUT_OF_MODEL_SCOPE: a check could not be completed (the checks not evaluated are listed on the sheet)."""

    DETAILED_ASSEMBLY_DRAWING = "DETAILED_ASSEMBLY_DRAWING"
    DIAGNOSTIC_VALIDATION_FAILED = "DIAGNOSTIC_VALIDATION_FAILED"
    DIAGNOSTIC_VALIDATION_INCOMPLETE = "DIAGNOSTIC_VALIDATION_INCOMPLETE"


#: The drawing status a local assembly validation status selects (every validation status the generator can meet).
DRAWING_STATUS_FOR_VALIDATION: dict[AssemblyValidationStatusV1, DrawingStatusV1] = {
    AssemblyValidationStatusV1.PASS: DrawingStatusV1.DETAILED_ASSEMBLY_DRAWING,
    AssemblyValidationStatusV1.WARNING: DrawingStatusV1.DETAILED_ASSEMBLY_DRAWING,
    AssemblyValidationStatusV1.FAIL: DrawingStatusV1.DIAGNOSTIC_VALIDATION_FAILED,
    AssemblyValidationStatusV1.UNKNOWN: DrawingStatusV1.DIAGNOSTIC_VALIDATION_INCOMPLETE,
    AssemblyValidationStatusV1.OUT_OF_MODEL_SCOPE: DrawingStatusV1.DIAGNOSTIC_VALIDATION_INCOMPLETE,
}


def _status_problem(validation: AssemblyValidationStatusV1, status: DrawingStatusV1, delivery_ready: bool) -> str | None:
    """Why a (validation status, drawing status, delivery_ready) triple is not one of the allowed ones, or ``None``."""

    if delivery_ready != (status is DrawingStatusV1.DETAILED_ASSEMBLY_DRAWING):
        return "delivery_ready is true exactly for a DETAILED_ASSEMBLY_DRAWING"
    expected = DRAWING_STATUS_FOR_VALIDATION.get(validation)
    if expected is None:
        return f"no drawing is generated for a local assembly validation of status {validation.value}"
    if status is not expected:
        return f"a drawing whose local assembly validation is {validation.value} is {expected.value}"
    return None


def drawing_schema_version_for(binding: DrawingBindingV1) -> Literal["4.1", "4.2"]:
    """The drawing version a writer states for ``binding``: 4.2 for a LOCAL_PLAN package's drawing, else 4.1."""

    return ASSEMBLY_DRAWING_SCHEMA_VERSION_4_2 if binding.local else ASSEMBLY_DRAWING_SCHEMA_VERSION


def _version_problem(version: str, binding: DrawingBindingV1) -> str | None:
    if version != drawing_schema_version_for(binding):
        return "a drawing states 4.1 exactly for a commercial package and 4.2 exactly for a LOCAL_PLAN package"
    return None


class DrawingViewKindV1(StrEnum):
    FRONT = "FRONT"
    SIDE = "SIDE"
    PLAN = "PLAN"
    PICTORIAL = "PICTORIAL"


class DrawingSheetV1(StrEnum):
    """The one landscape ISO A-series sheet the drawing is laid out on (one page in both the SVG and the PDF).

    A3 is the default; a larger sheet is chosen (smallest first) only when the views, notes, steps and tables of the
    assembly -- e.g. several support chains on one breadboard -- do not fit a smaller one."""

    A3_LANDSCAPE_420x297_MM = "A3_LANDSCAPE_420x297_MM"
    A2_LANDSCAPE_594x420_MM = "A2_LANDSCAPE_594x420_MM"
    A1_LANDSCAPE_841x594_MM = "A1_LANDSCAPE_841x594_MM"
    A0_LANDSCAPE_1189x841_MM = "A0_LANDSCAPE_1189x841_MM"


class DrawingFileRoleV1(StrEnum):
    """The role of a drawing file: the client artifact store's own role names (``ArtifactFileRoleV1``)."""

    DRAWING_SVG = "DRAWING_SVG"
    DRAWING_PDF = "DRAWING_PDF"
    DRAWING_RESULT = "DRAWING_RESULT"


#: The schema 3.0 role names of the drawing files -> the role a stored drawing file has.
LEGACY_DRAWING_ROLE_ALIASES: dict[str, str] = {
    "ASSEMBLY_DRAWING_SVG": DrawingFileRoleV1.DRAWING_SVG.value,
    "ASSEMBLY_DRAWING_PDF": DrawingFileRoleV1.DRAWING_PDF.value,
}

_FILE_SPEC: dict[str, tuple[DrawingFileRoleV1, str]] = {
    DRAWING_SVG_PATH: (DrawingFileRoleV1.DRAWING_SVG, SVG_MEDIA_TYPE),
    DRAWING_PDF_PATH: (DrawingFileRoleV1.DRAWING_PDF, PDF_MEDIA_TYPE),
    DRAWING_RESULT_PATH: (DrawingFileRoleV1.DRAWING_RESULT, JSON_MEDIA_TYPE),
}


class StepActionV1(StrEnum):
    PLACE = "PLACE"            # the breadboard (support root)
    STAND_AND_FIX = "STAND_AND_FIX"  # a base standing on the board, fixed at its fixation holes
    INSERT = "INSERT"          # a post inserted into its holder, to the shown depth
    MOUNT = "MOUNT"            # a mount on its post / stage
    SEAT = "SEAT"              # an optic seated in its mount
    FASTEN = "FASTEN"
    ADJUST = "ADJUST"          # a delivered manual DOF (after assembly)
    MANUAL_OPERATION = "MANUAL_OPERATION"
    PLACE_CUSTOMER_SUPPLIED = "PLACE_CUSTOMER_SUPPLIED"  # a customer-supplied component, at its design position


# ---------------------------------------------------------------------------------------------- shared refs


class DrawingBindingV1(StrictContractModel):
    """The exact sources the drawing was generated from (all copied from the verified packages): the commercial
    resolution id and Customer Purchase BOM of a commercial package (4.1), or the local plan revision of a LOCAL_PLAN
    package (4.2), exactly one of the two forms."""

    project: ValidatedProjectRefV1
    local_validation_hash: HashRef | None = None
    commercial_resolution_id: OpenSciId | None = None
    commercial_bom: CommercialBomRefV1 | None = None
    preview_package_content_hash: Sha256Digest
    preview_package_revision: int = Field(strict=True, ge=1)
    detail_content_hash: Sha256Digest
    release: DetailReleaseRefV1
    local_plan: LocalPlanRefV1 | None = None

    @model_validator(mode="after")
    def _form(self) -> DrawingBindingV1:
        commercial = (self.commercial_resolution_id is not None, self.commercial_bom is not None)
        if self.local_plan is None and commercial != (True, True):
            raise ValueError("the drawing of a commercial package names its commercial resolution id and Customer Purchase BOM")
        if self.local_plan is not None and any(commercial):
            raise ValueError("the drawing of a LOCAL_PLAN package names its local plan and no commercial resolution or "
                             "Customer Purchase BOM")
        if (self.local_plan is not None) != (self.release.basis.value == "LOCAL_PLAN"):
            raise ValueError("the release basis is LOCAL_PLAN exactly for the drawing of a LOCAL_PLAN package")
        return self

    @property
    def local(self) -> bool:
        """True for the drawing of a LOCAL_PLAN package."""

        return self.local_plan is not None


class DrawingGeneratorRefV1(StrictContractModel):
    generator_id: Literal["opensci.engine.assembly_drawing"] = ASSEMBLY_DRAWING_GENERATOR_ID
    generator_version: BuildVersion
    engine_package: EnginePackageName = ENGINE_PACKAGE_NAME
    engine_version: BuildVersion
    engine_build_id: BuildVersion


class DrawingValidationRefV1(StrictContractModel):
    """The local assembly validation of the same preview package, which the generator ran before drawing:
    ``checks_passed`` of its ``checks_evaluated`` checks have the status PASS (the one check count the sheet prints)."""

    status: AssemblyValidationStatusV1
    result_hash: Sha256Digest
    checker_version: BuildVersion
    runtime_kind: LocalRuntimeKindV1
    checks_passed: int = Field(strict=True, ge=0)
    checks_evaluated: int = Field(strict=True, ge=1)

    @model_validator(mode="after")
    def _counts(self) -> DrawingValidationRefV1:
        if self.checks_passed > self.checks_evaluated:
            raise ValueError("checks_passed cannot exceed checks_evaluated")
        return self


# ---------------------------------------------------------------------------------------------- result


class DrawingViewV1(StrictContractModel):
    view: DrawingViewKindV1
    scale: ScaleLabel
    part_refs: tuple[PartRef, ...] = Field(description="Parts with at least one visible outline segment in this view.")
    visible_segment_count: int = Field(strict=True, ge=0)
    hidden_segment_count: int = Field(strict=True, ge=0)


class DrawingDimensionV1(StrictContractModel):
    dim_ref: DimensionRef
    kind: DimensionKindV1
    subject_part_ref: PartRef
    axis: MeasureAxisV1
    basis: DimensionBasisV1
    value_mm: ValueText = Field(description="Measured from the preview poses, printed to at most 3 decimals.")
    views: tuple[DrawingViewKindV1, ...] = Field(min_length=1)


class DrawingBalloonV1(StrictContractModel):
    commercial_line_ref: LineRef
    part_ref: PartRef
    views: tuple[DrawingViewKindV1, ...] = Field(min_length=1)


class PartsLegendRowV1(StrictContractModel):
    """One part of the sheet with its BOM line. ``supply`` (4.2 only, required there): who supplies the part."""

    commercial_line_ref: LineRef
    part_ref: PartRef
    opensci_sku: OpenSciSku
    display_name: DisplayText
    supply: LegendSupplyV1 | None = None


class PartsLegendV1(StrictContractModel):
    """The parts on the sheet, each with its line of the bound Customer Purchase BOM (quantities and prices are in the
    BOM; 4.1), or of the local plan's design BOM with who supplies it (``DESIGN_BOM``; 4.2)."""

    source: Literal["CUSTOMER_PURCHASE_BOM", "DESIGN_BOM"] = "CUSTOMER_PURCHASE_BOM"
    commercial_bom: CommercialBomRefV1 | None = None
    design_bom: DesignBomRefV1 | None = None
    rows: tuple[PartsLegendRowV1, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _source(self) -> PartsLegendV1:
        design = self.source == "DESIGN_BOM"
        if (self.commercial_bom is None) != design or (self.design_bom is None) == design:
            raise ValueError("a parts legend references exactly the BOM its source names")
        if any((row.supply is None) == design for row in self.rows):
            raise ValueError("every row of a DESIGN_BOM legend states its supply, and no row of a Customer Purchase BOM "
                             "legend does")
        return self


class AssemblyStepV1(StrictContractModel):
    """One assembly step: about one part of the sheet (``part_ref``), or -- ``PLACE_CUSTOMER_SUPPLIED`` -- about one
    customer-supplied design component (``design_instance_id``), which OpenSci neither supplies nor mounts."""

    step: int = Field(strict=True, ge=1)
    action: StepActionV1
    part_ref: PartRef | None = None
    design_instance_id: InstanceKey | None = None
    commercial_line_ref: LineRef | None = None
    text: StepText

    @model_validator(mode="after")
    def _subject(self) -> AssemblyStepV1:
        customer = self.action is StepActionV1.PLACE_CUSTOMER_SUPPLIED
        if (self.part_ref is None) != customer or (self.design_instance_id is None) == customer:
            raise ValueError(f"step {self.step}: a PLACE_CUSTOMER_SUPPLIED step names a design component, every other step a part")
        if customer and self.commercial_line_ref is not None:
            raise ValueError(f"step {self.step}: a customer-supplied component has no Customer Purchase BOM line")
        return self


class CustomerSuppliedRowV1(StrictContractModel):
    """One customer-supplied design component on the sheet (the customer-supplied components table, apart from the parts
    legend): what it is, why the customer supplies it, and where it goes. ``position_mm`` is its design position in the
    assembly world (the same frame as every part), ``design_position_mm`` the same point in the customer's own design
    frame; ``axis_world`` its local +Z (optical axis or face normal) in the assembly world."""

    design_instance_id: InstanceKey
    optical_role: DesignRole
    reason: CustomerSupplyReasonV1
    opensci_sku: OpenSciSku | None = None
    model_display_name: CustomerFacingName | None = Field(
        None, description="The name of the catalog product the design used as the model of the component (opensci_sku).")
    position_mm: tuple[float, float, float]
    design_position_mm: tuple[float, float, float]
    axis_world: tuple[float, float, float]
    reserved_envelope: bool = Field(description="True when the resolution reserved the component's space (known geometry).")


class OpticalSpacingRowV1(StrictContractModel):
    """The design distance between two consecutive elements (design instance ids) along one nominal beam path of the
    preview scene (``beam_reference.beam_axes``), printed to 0.1 mm."""

    path_key: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")]
    from_instance_id: InstanceKey
    to_instance_id: InstanceKey
    distance_mm: ValueText


class AssemblyDrawingResultV1(StrictContractModel):
    schema_id: Literal["opensci.assembly_drawing.result"] = Field(ASSEMBLY_DRAWING_RESULT_SCHEMA_ID, alias="schema")
    schema_version: Literal["4.1", "4.2"] = Field(
        ASSEMBLY_DRAWING_SCHEMA_VERSION, description="4.1: a commercial package's drawing; 4.2: a LOCAL_PLAN package's.")
    generator: DrawingGeneratorRefV1
    binding: DrawingBindingV1
    validation: DrawingValidationRefV1
    locale: DrawingLocaleV1 = Field(description="The language the sheet is written in.")
    drawing_status: DrawingStatusV1
    delivery_ready: bool = Field(description="True exactly for a DETAILED_ASSEMBLY_DRAWING.")
    description: Literal[
        "Detailed Assembly Drawing of your OpenSci system. Dimensions in millimetres, each marked with its basis. Item "
        "balloons carry the Customer Purchase BOM line of each part.",
        "Assembly drawing of your design. Dimensions in millimetres, each marked with its basis. Item balloons carry the "
        "design BOM line of each part.",
    ] = DRAWING_DESCRIPTION
    sheet: DrawingSheetV1 = DrawingSheetV1.A3_LANDSCAPE_420x297_MM
    views: tuple[DrawingViewV1, ...] = Field(min_length=1)
    dimensions: tuple[DrawingDimensionV1, ...] = ()
    balloons: tuple[DrawingBalloonV1, ...] = Field(min_length=1)
    parts_legend: PartsLegendV1
    assembly_steps: tuple[AssemblyStepV1, ...] = ()
    customer_supplied_components: tuple[CustomerSuppliedRowV1, ...] = Field(
        (), description="The customer-supplied design components drawn as placeholders at their design positions, by instance id.")
    optical_spacing: tuple[OpticalSpacingRowV1, ...] = Field(
        (), description="Design distances between consecutive elements along each nominal beam path, in path order.")
    notes: tuple[AssemblyNoteCodeV1, ...] = Field((), description="Assembly note codes of the preview scene carried onto the sheet.")
    result_hash: Sha256Digest = Field(description="osc1 digest of this document without result_hash.")

    @model_validator(mode="after")
    def _check(self) -> AssemblyDrawingResultV1:
        if problem := _status_problem(self.validation.status, self.drawing_status, self.delivery_ready):
            raise ValueError(problem)
        if problem := _version_problem(self.schema_version, self.binding):
            raise ValueError(problem)
        local = self.binding.local
        if self.description != (LOCAL_DRAWING_DESCRIPTION if local else DRAWING_DESCRIPTION):
            raise ValueError("the description is LOCAL_DRAWING_DESCRIPTION exactly for the drawing of a LOCAL_PLAN package")
        legend = self.parts_legend
        if local:
            if legend.source != "DESIGN_BOM" or legend.design_bom != self.binding.local_plan.design_bom:  # type: ignore[union-attr]
                raise ValueError("the parts legend must reference the bound local plan's design BOM")
        elif legend.source != "CUSTOMER_PURCHASE_BOM" or legend.commercial_bom != self.binding.commercial_bom:
            raise ValueError("the parts legend must reference the bound Customer Purchase BOM")
        ids = [c.design_instance_id for c in self.customer_supplied_components]
        if ids != sorted(set(ids)):
            raise ValueError("customer_supplied_components are unique and ordered by design_instance_id")
        placed = {s.design_instance_id for s in self.assembly_steps if s.design_instance_id is not None}
        if not placed <= set(ids):
            raise ValueError("a PLACE_CUSTOMER_SUPPLIED step names a component of the customer-supplied table")
        steps = [s.step for s in self.assembly_steps]
        if steps != list(range(1, len(steps) + 1)):
            raise ValueError("assembly steps are numbered 1..n in order")
        if self.result_hash != osc1_digest(self.hashed_payload()):
            raise ValueError("result_hash does not match the document")
        return self

    def payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)

    def hashed_payload(self) -> dict[str, Any]:
        payload = self.payload()
        del payload["result_hash"]
        return payload


# ---------------------------------------------------------------------------------------------- manifest


class DrawingOutputFileV1(StrictContractModel):
    path: Literal["assembly_drawing.svg", "assembly_drawing.pdf", "drawing_result.json"]
    role: DrawingFileRoleV1
    media_type: Literal["image/svg+xml", "application/pdf", "application/json"]
    size_bytes: int = Field(strict=True, ge=1, le=MAX_DRAWING_FILE_BYTES)
    sha256: Sha256Digest

    @model_validator(mode="after")
    def _spec(self) -> DrawingOutputFileV1:
        if (self.role, self.media_type) != _FILE_SPEC[self.path]:
            raise ValueError(f"{self.path}: role/media_type must be {_FILE_SPEC[self.path]}")
        return self


class AssemblyDrawingManifestV1(StrictContractModel):
    """The local output record of one drawing generation: what was generated, from exactly what, by which build."""

    schema_id: Literal["opensci.assembly_drawing.manifest"] = Field(ASSEMBLY_DRAWING_MANIFEST_SCHEMA_ID, alias="schema")
    schema_version: Literal["4.1", "4.2"] = ASSEMBLY_DRAWING_SCHEMA_VERSION
    generator: DrawingGeneratorRefV1
    binding: DrawingBindingV1
    validation: DrawingValidationRefV1
    locale: DrawingLocaleV1
    drawing_status: DrawingStatusV1
    delivery_ready: bool
    drawing_result_hash: Sha256Digest
    files: tuple[DrawingOutputFileV1, ...] = Field(min_length=3, max_length=3)
    manifest_content_hash: Sha256Digest = Field(description="osc1 digest of this manifest without manifest_content_hash.")

    @model_validator(mode="after")
    def _check(self) -> AssemblyDrawingManifestV1:
        if [f.path for f in self.files] != sorted(_FILE_SPEC):
            raise ValueError(f"files must be exactly {sorted(_FILE_SPEC)} in that order")
        if problem := _status_problem(self.validation.status, self.drawing_status, self.delivery_ready):
            raise ValueError(problem)
        if problem := _version_problem(self.schema_version, self.binding):
            raise ValueError(problem)
        if self.manifest_content_hash != osc1_digest(self.hashed_payload()):
            raise ValueError("manifest_content_hash does not match the manifest")
        return self

    def payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)

    def hashed_payload(self) -> dict[str, Any]:
        payload = self.payload()
        del payload["manifest_content_hash"]
        return payload


def drawing_manifest_for(result: AssemblyDrawingResultV1, files: Mapping[str, bytes]) -> AssemblyDrawingManifestV1:
    """Build the manifest of ``files`` (the SVG, PDF and result bytes) for ``result``."""

    entries = []
    for path in sorted(_FILE_SPEC):
        role, media = _FILE_SPEC[path]
        data = bytes(files[path])
        entries.append({"path": path, "role": role.value, "media_type": media, "size_bytes": len(data),
                        "sha256": hashlib.sha256(data).hexdigest()})
    content = {
        "schema": ASSEMBLY_DRAWING_MANIFEST_SCHEMA_ID,
        "schema_version": result.schema_version,
        "generator": result.generator.model_dump(mode="json", by_alias=True, exclude_none=True),
        "binding": result.binding.model_dump(mode="json", by_alias=True, exclude_none=True),
        "validation": result.validation.model_dump(mode="json", by_alias=True, exclude_none=True),
        "locale": result.locale,
        "drawing_status": result.drawing_status.value,
        "delivery_ready": result.delivery_ready,
        "drawing_result_hash": result.result_hash,
        "files": entries,
    }
    return AssemblyDrawingManifestV1.model_validate({**content, "manifest_content_hash": osc1_digest(content)})


def verify_drawing_outputs(files: Mapping[str, bytes]) -> tuple[str, ...]:
    """Every problem of a received drawing output set (the four files); empty = valid. Never raises.

    Checks the exact file set, the manifest (schema, canonical bytes, content hash), each file's size and SHA-256,
    the result document (schema, canonical bytes, result hash) and that result and manifest agree on the binding,
    generator, validation and status. It cannot re-derive the drawing: that needs the source packages."""

    expected = set(_FILE_SPEC) | {DRAWING_MANIFEST_PATH}
    if set(files) != expected:
        return (f"drawing outputs must be exactly {sorted(expected)}",)
    if any(not isinstance(v, (bytes, bytearray)) for v in files.values()):
        return ("drawing output content must be bytes",)
    problems: list[str] = []
    try:
        manifest = AssemblyDrawingManifestV1.model_validate_json(bytes(files[DRAWING_MANIFEST_PATH]))
    except (ValidationError, ValueError, RecursionError) as exc:
        return (f"{DRAWING_MANIFEST_PATH} is not a valid drawing manifest: {type(exc).__name__}",)
    if bytes(files[DRAWING_MANIFEST_PATH]) != json_file_bytes(manifest.payload()):
        problems.append(f"{DRAWING_MANIFEST_PATH} is not in canonical file form")
    for entry in manifest.files:
        data = bytes(files[entry.path])
        if len(data) != entry.size_bytes or hashlib.sha256(data).hexdigest() != entry.sha256:
            problems.append(f"{entry.path}: size or SHA-256 does not match the manifest")
    try:
        json.loads(bytes(files[DRAWING_RESULT_PATH]))
        result = AssemblyDrawingResultV1.model_validate_json(bytes(files[DRAWING_RESULT_PATH]))
    except (ValidationError, ValueError, RecursionError) as exc:
        return tuple(problems + [f"{DRAWING_RESULT_PATH} is not a valid drawing result: {type(exc).__name__}"])
    if bytes(files[DRAWING_RESULT_PATH]) != json_file_bytes(result.payload()):
        problems.append(f"{DRAWING_RESULT_PATH} is not in canonical file form")
    if result.result_hash != manifest.drawing_result_hash:
        problems.append("the manifest names another drawing result")
    for label in ("schema_version", "generator", "binding", "validation", "locale", "drawing_status", "delivery_ready"):
        if getattr(result, label) != getattr(manifest, label):
            problems.append(f"result and manifest disagree on {label}")
    if not bytes(files[DRAWING_PDF_PATH]).startswith(b"%PDF-"):
        problems.append(f"{DRAWING_PDF_PATH} is not a PDF")
    if b"<svg" not in bytes(files[DRAWING_SVG_PATH])[:512]:
        problems.append(f"{DRAWING_SVG_PATH} is not an SVG document")
    return tuple(problems)
