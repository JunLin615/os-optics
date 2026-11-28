"""Assembly Local Validation: the result of checking ONE verified Assembly Preview Package on the user device.

After Assembly Resolution the server sends the device a verified Assembly Preview Package (``package.py``). The device
checks the resolved assembly it describes -- poses, part geometry, support tree, mates, breadboard, fixations and the
nominal beam axes -- and reports the outcome here.

The result states:

* which package was checked (``package_content_hash``), and through its manifest the Project and the resolution (or,
  schema 2.1, the local assembly plan revision of a package generated on the user's device: ``local_plan``);
  which checker build produced it, and every tolerance it used (``tolerances``);
* a fixed, ordered list of checks (``ASSEMBLY_CHECK_ORDER``), each with its status, the basis it rests on and how many
  subjects it evaluated;
* issues with a closed vocabulary of codes, each with its fixed check, severity, status effect and message
  (``ISSUE_RULES``); the parts involved are named by package-local ``part_ref``;
* a per-part status, so a viewer can colour the parts of the same package without re-deriving geometry;
* a summary (``ValidationSummaryV1``): what was checked, on which geometry, and whether any interference was found.

Every string is an enum value, a fixed template, a package-local reference, an ``OS-*`` identity from the package, or
a hash.

Status vocabulary and "worst of" order are the Local Validation Record's (``LocalValidationStatusV1``):
NOT_APPLICABLE < PASS < WARNING < UNKNOWN < OUT_OF_MODEL_SCOPE < FAIL. UNKNOWN is never a pass.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, model_validator

from ..base import StrictContractModel
from ..common.hashes import HashRef, Sha256Digest, osc1_digest
from ..common.identifiers import BuildVersion, InstanceKey, OpenSciId
from ..optical_design.local_validation import (ENGINE_PACKAGE_NAME, EnginePackageName, LocalRuntimeKindV1, LocalValidationStatusV1,
                                                ValidatedProjectRefV1, status_severity)
from .manifest import LocalPlanRefV1, ResolutionRefV1
from .scene import GeometryFidelityV1, PartCategoryV1, PartRef


ASSEMBLY_LOCAL_VALIDATION_SCHEMA_ID: Literal["opensci.assembly_local_validation"] = "opensci.assembly_local_validation"
ASSEMBLY_LOCAL_VALIDATION_SCHEMA_VERSION: Literal["2.0"] = "2.0"
#: 2.1: the result of a package with the LOCAL_PLAN binding (``ValidatedPackageRefV1.local_plan``, no resolution).
ASSEMBLY_LOCAL_VALIDATION_SCHEMA_VERSION_2_1: Literal["2.1"] = "2.1"
ASSEMBLY_LOCAL_VALIDATION_SCHEMA_VERSIONS: tuple[str, ...] = (ASSEMBLY_LOCAL_VALIDATION_SCHEMA_VERSION,
                                                              ASSEMBLY_LOCAL_VALIDATION_SCHEMA_VERSION_2_1)
ASSEMBLY_CHECKER_ID: Literal["opensci.assembly_layout_checker"] = "opensci.assembly_layout_checker"
MAX_ISSUES = 4096

AssemblyValidationStatusV1 = LocalValidationStatusV1

SUMMARY_STATEMENT: Literal[
    "Layout and collision check of the resolved assembly on the part geometry of its Assembly Preview Package: poses, "
    "part geometry, support structure and attachment, breadboard containment and fixation, support engagement, "
    "part-to-part collision and beam-axis clearance, at the tolerances recorded in this result."
] = (
    "Layout and collision check of the resolved assembly on the part geometry of its Assembly Preview Package: poses, "
    "part geometry, support structure and attachment, breadboard containment and fixation, support engagement, "
    "part-to-part collision and beam-axis clearance, at the tolerances recorded in this result."
)


class AssemblyCheckKindV1(StrEnum):
    PACKAGE_INTEGRITY = "PACKAGE_INTEGRITY"  # manifest, digests, content hash, schema, GLB structure (before any geometry)
    SCENE_TRANSFORMS = "SCENE_TRANSFORMS"  # finite poses, unit quaternions
    PART_GEOMETRY = "PART_GEOMETRY"  # each part mesh decodes, is closed and consistently oriented, has volume
    SUPPORT_STRUCTURE = "SUPPORT_STRUCTURE"  # support tree rooted at the breadboard, acyclic (package contract)
    SUPPORT_ATTACHMENT = "SUPPORT_ATTACHMENT"  # mates close; supported parts are not detached from their support
    BOARD_CONTAINMENT = "BOARD_CONTAINMENT"  # OpenSci-supplied hardware lies on the resolved breadboard
    BOARD_FIXATION = "BOARD_FIXATION"  # declared fixation holes lie under the fixed part
    EXPECTED_ENGAGEMENT = "EXPECTED_ENGAGEMENT"  # support contacts overlap only inside their interface allowance
    PART_COLLISION = "PART_COLLISION"  # parts not expected to touch do not interpenetrate beyond the collision thresholds
    BEAM_AXIS_CLEARANCE = "BEAM_AXIS_CLEARANCE"  # the nominal beam centre line passes through no mechanical part


#: The fixed order of ``AssemblyLocalValidationV1.checks``: every kind exactly once.
ASSEMBLY_CHECK_ORDER: tuple[AssemblyCheckKindV1, ...] = tuple(AssemblyCheckKindV1)


class CheckBasisV1(StrEnum):
    """What a check's conclusion rests on."""

    PACKAGE_VERIFICATION = "PACKAGE_VERIFICATION"  # the canonical package verifier
    PACKAGE_CONTRACT = "PACKAGE_CONTRACT"  # a rule the scene contract already enforces
    SCENE_POSES = "SCENE_POSES"  # poses and datum frames of the scene
    PART_GEOMETRY = "PART_GEOMETRY"  # the package's part geometry, placed by the scene poses


CHECK_BASIS: dict[AssemblyCheckKindV1, CheckBasisV1] = {
    AssemblyCheckKindV1.PACKAGE_INTEGRITY: CheckBasisV1.PACKAGE_VERIFICATION,
    AssemblyCheckKindV1.SCENE_TRANSFORMS: CheckBasisV1.SCENE_POSES,
    AssemblyCheckKindV1.PART_GEOMETRY: CheckBasisV1.PART_GEOMETRY,
    AssemblyCheckKindV1.SUPPORT_STRUCTURE: CheckBasisV1.PACKAGE_CONTRACT,
    AssemblyCheckKindV1.SUPPORT_ATTACHMENT: CheckBasisV1.PART_GEOMETRY,
    AssemblyCheckKindV1.BOARD_CONTAINMENT: CheckBasisV1.PART_GEOMETRY,
    AssemblyCheckKindV1.BOARD_FIXATION: CheckBasisV1.PART_GEOMETRY,
    AssemblyCheckKindV1.EXPECTED_ENGAGEMENT: CheckBasisV1.PART_GEOMETRY,
    AssemblyCheckKindV1.PART_COLLISION: CheckBasisV1.PART_GEOMETRY,
    AssemblyCheckKindV1.BEAM_AXIS_CLEARANCE: CheckBasisV1.PART_GEOMETRY,
}


class IssueSeverityV1(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class AssemblyIssueCodeV1(StrEnum):
    NON_FINITE_POSE = "NON_FINITE_POSE"
    NON_UNIT_QUATERNION = "NON_UNIT_QUATERNION"
    GEOMETRY_UNREADABLE = "GEOMETRY_UNREADABLE"
    GEOMETRY_NOT_CLOSED = "GEOMETRY_NOT_CLOSED"
    GEOMETRY_DEGENERATE = "GEOMETRY_DEGENERATE"
    GEOMETRY_BUDGET_EXCEEDED = "GEOMETRY_BUDGET_EXCEEDED"
    ATTACHMENT_NOT_EVALUATED = "ATTACHMENT_NOT_EVALUATED"
    MATE_MISALIGNED = "MATE_MISALIGNED"
    MATE_SEPARATED = "MATE_SEPARATED"
    SUPPORT_GEOMETRY_SEPARATED = "SUPPORT_GEOMETRY_SEPARATED"
    PART_OUTSIDE_BREADBOARD = "PART_OUTSIDE_BREADBOARD"
    CONTAINMENT_NOT_EVALUATED = "CONTAINMENT_NOT_EVALUATED"
    PART_OVERHANGS_BREADBOARD = "PART_OVERHANGS_BREADBOARD"
    CUSTOMER_SUPPLIED_COMPONENT = "CUSTOMER_SUPPLIED_COMPONENT"
    FIXATION_HOLE_NOT_UNDER_PART = "FIXATION_HOLE_NOT_UNDER_PART"
    FIXATION_NOT_EVALUATED = "FIXATION_NOT_EVALUATED"
    ENGAGEMENT_EXCEEDS_INTERFACE = "ENGAGEMENT_EXCEEDS_INTERFACE"
    ENGAGEMENT_MINOR_OVERLAP = "ENGAGEMENT_MINOR_OVERLAP"
    ENGAGEMENT_NOT_BOUNDED = "ENGAGEMENT_NOT_BOUNDED"
    ENGAGEMENT_NOT_EVALUATED = "ENGAGEMENT_NOT_EVALUATED"
    PART_INTERFERENCE = "PART_INTERFERENCE"
    MINOR_OVERLAP = "MINOR_OVERLAP"
    PAIR_NOT_EVALUATED = "PAIR_NOT_EVALUATED"
    BEAM_AXIS_OBSTRUCTED = "BEAM_AXIS_OBSTRUCTED"
    BEAM_AXIS_NOT_EVALUATED = "BEAM_AXIS_NOT_EVALUATED"
    RESERVED_SPACE_INTRUSION = "RESERVED_SPACE_INTRUSION"


class IssueSubjectV1(StrEnum):
    """What an issue names: nothing (the assembly), one part, two parts, one or two parts, or a design component."""

    ASSEMBLY = "ASSEMBLY"  # no part_refs, no design instance
    PART = "PART"  # exactly one part_ref
    PAIR = "PAIR"  # exactly two part_refs
    PART_OR_PAIR = "PART_OR_PAIR"  # a part (pose / datum) or the two parts of a mate
    DESIGN_COMPONENT = "DESIGN_COMPONENT"  # a design_instance_id, no part_refs
    PART_AND_DESIGN_COMPONENT = "PART_AND_DESIGN_COMPONENT"  # exactly one part_ref and a design_instance_id


SUBJECT_PART_COUNTS: dict[IssueSubjectV1, tuple[int, ...]] = {
    IssueSubjectV1.ASSEMBLY: (0,),
    IssueSubjectV1.PART: (1,),
    IssueSubjectV1.PAIR: (2,),
    IssueSubjectV1.PART_OR_PAIR: (1, 2),
    IssueSubjectV1.DESIGN_COMPONENT: (0,),
    IssueSubjectV1.PART_AND_DESIGN_COMPONENT: (1,),
}

#: Codes about one mate carry its ``mate_ref`` ("REQUIRED"); a pose problem carries it exactly when it names the two
#: parts of a mate ("WITH_PAIR"). Every other code carries none.
MATE_REF_RULES: dict[str, str] = {
    "NON_FINITE_POSE": "WITH_PAIR",
    "NON_UNIT_QUATERNION": "WITH_PAIR",
    "ATTACHMENT_NOT_EVALUATED": "REQUIRED",
    "MATE_MISALIGNED": "REQUIRED",
    "MATE_SEPARATED": "REQUIRED",
    "SUPPORT_GEOMETRY_SEPARATED": "REQUIRED",
    "ENGAGEMENT_EXCEEDS_INTERFACE": "REQUIRED",
    "ENGAGEMENT_MINOR_OVERLAP": "REQUIRED",
    "ENGAGEMENT_NOT_BOUNDED": "REQUIRED",
    "ENGAGEMENT_NOT_EVALUATED": "REQUIRED",
}


class IssueRuleV1(StrictContractModel):
    """The fixed meaning of one issue code."""

    subject: IssueSubjectV1
    check: AssemblyCheckKindV1
    severity: IssueSeverityV1
    status_effect: LocalValidationStatusV1
    message: str


def _rule(subject: IssueSubjectV1, check: AssemblyCheckKindV1, severity: IssueSeverityV1, effect: LocalValidationStatusV1,
          message: str) -> IssueRuleV1:
    return IssueRuleV1(subject=subject, check=check, severity=severity, status_effect=effect, message=message)


_C, _S, _E, _P = AssemblyCheckKindV1, IssueSeverityV1, LocalValidationStatusV1, IssueSubjectV1
_I = AssemblyIssueCodeV1
_NOT_EVALUATED = "This check could not be evaluated for this part, because the part could not be placed or measured."

#: Code -> subject, check, severity, the status it forces on its check, and the one allowed message (no interpolation).
ISSUE_RULES: dict[AssemblyIssueCodeV1, IssueRuleV1] = {
    _I.NON_FINITE_POSE: _rule(_P.PART_OR_PAIR, _C.SCENE_TRANSFORMS, _S.ERROR, _E.FAIL, "A part pose or datum frame has a non-finite value."),
    _I.NON_UNIT_QUATERNION: _rule(_P.PART_OR_PAIR, _C.SCENE_TRANSFORMS, _S.ERROR, _E.FAIL,
                                  "A part pose, datum frame or mate rotation is not a unit quaternion."),
    _I.GEOMETRY_UNREADABLE: _rule(_P.PART, _C.PART_GEOMETRY, _S.ERROR, _E.UNKNOWN,
                                  "The geometry of this part could not be read as a triangle mesh; checks involving it are not evaluated."),
    _I.GEOMETRY_NOT_CLOSED: _rule(_P.PART, _C.PART_GEOMETRY, _S.ERROR, _E.UNKNOWN,
                                  "The geometry of this part is not a closed, consistently oriented surface; its volume is undefined, "
                                  "so the volume-based checks involving it (support engagement, collision overlap, beam-axis "
                                  "crossing) are not evaluated."),
    _I.GEOMETRY_DEGENERATE: _rule(_P.PART, _C.PART_GEOMETRY, _S.ERROR, _E.UNKNOWN,
                                  "The geometry of this part encloses no positive volume, so the volume-based checks involving it "
                                  "(support engagement, collision overlap, beam-axis crossing) are not evaluated."),
    _I.GEOMETRY_BUDGET_EXCEEDED: _rule(_P.ASSEMBLY, _C.PART_GEOMETRY, _S.ERROR, _E.UNKNOWN,
                                       "The package geometry exceeds the checker's work limit (max_total_triangles), so the "
                                       "geometric checks were not run; a larger max_total_triangles runs them."),
    _I.ATTACHMENT_NOT_EVALUATED: _rule(_P.PAIR, _C.SUPPORT_ATTACHMENT, _S.WARNING, _E.UNKNOWN, _NOT_EVALUATED),
    _I.MATE_MISALIGNED: _rule(_P.PAIR, _C.SUPPORT_ATTACHMENT, _S.WARNING, _E.WARNING,
                              "The two datums of this mate do not coincide exactly (offset below the detachment tolerance)."),
    _I.MATE_SEPARATED: _rule(_P.PAIR, _C.SUPPORT_ATTACHMENT, _S.ERROR, _E.FAIL,
                             "The two datums of this mate are apart by more than the detachment tolerance: the part is not attached to "
                             "what the scene says carries it."),
    _I.SUPPORT_GEOMETRY_SEPARATED: _rule(_P.PAIR, _C.SUPPORT_ATTACHMENT, _S.ERROR, _E.FAIL,
                                         "This part's geometry is separated from the geometry of its declared support by more than the "
                                         "detachment tolerance (floating part)."),
    _I.PART_OUTSIDE_BREADBOARD: _rule(_P.PART, _C.BOARD_CONTAINMENT, _S.ERROR, _E.FAIL,
                                      "This OpenSci-supplied part lies outside the resolved breadboard outline."),
    _I.PART_OVERHANGS_BREADBOARD: _rule(_P.PART, _C.BOARD_CONTAINMENT, _S.WARNING, _E.WARNING,
                                        "This part, carried above the breadboard, extends beyond the breadboard outline."),
    _I.CONTAINMENT_NOT_EVALUATED: _rule(_P.PART, _C.BOARD_CONTAINMENT, _S.WARNING, _E.UNKNOWN, _NOT_EVALUATED),
    _I.CUSTOMER_SUPPLIED_COMPONENT: _rule(_P.DESIGN_COMPONENT, _C.BOARD_CONTAINMENT, _S.INFO, _E.PASS,
                                          "This design component is your own hardware, placed as your design specifies; "
                                          "breadboard containment applies to the OpenSci-supplied parts."),
    _I.FIXATION_HOLE_NOT_UNDER_PART: _rule(_P.PART, _C.BOARD_FIXATION, _S.ERROR, _E.FAIL,
                                           "A breadboard hole declared for fixing this part does not lie under the part."),
    _I.FIXATION_NOT_EVALUATED: _rule(_P.PART, _C.BOARD_FIXATION, _S.WARNING, _E.UNKNOWN, _NOT_EVALUATED),
    _I.ENGAGEMENT_EXCEEDS_INTERFACE: _rule(_P.PAIR, _C.EXPECTED_ENGAGEMENT, _S.ERROR, _E.FAIL,
                                           "This part and its support overlap beyond the collision thresholds, outside the allowance of their "
                                           "declared interface."),
    _I.ENGAGEMENT_MINOR_OVERLAP: _rule(_P.PAIR, _C.EXPECTED_ENGAGEMENT, _S.WARNING, _E.WARNING,
                                       "This part and its support overlap slightly outside their interface allowance, "
                                       "below the collision thresholds."),
    _I.ENGAGEMENT_NOT_BOUNDED: _rule(_P.PAIR, _C.EXPECTED_ENGAGEMENT, _S.WARNING, _E.UNKNOWN,
                                     "This part and its support overlap beyond surface contact, and their interface gives no "
                                     "bounded engagement allowance to judge it."),
    _I.ENGAGEMENT_NOT_EVALUATED: _rule(_P.PAIR, _C.EXPECTED_ENGAGEMENT, _S.WARNING, _E.UNKNOWN, _NOT_EVALUATED),
    _I.PART_INTERFERENCE: _rule(_P.PAIR, _C.PART_COLLISION, _S.ERROR, _E.FAIL,
                                "These parts are not expected to touch, and their geometry interpenetrates beyond the "
                                "collision thresholds."),
    _I.MINOR_OVERLAP: _rule(_P.PAIR, _C.PART_COLLISION, _S.WARNING, _E.WARNING,
                            "These parts overlap slightly, below the collision thresholds."),
    _I.PAIR_NOT_EVALUATED: _rule(_P.PAIR, _C.PART_COLLISION, _S.WARNING, _E.UNKNOWN,
                                 "Collision between these parts was not evaluated because a part could not be placed or measured."),
    _I.BEAM_AXIS_OBSTRUCTED: _rule(_P.PART, _C.BEAM_AXIS_CLEARANCE, _S.WARNING, _E.WARNING,
                                   "The nominal beam centre line passes through this mechanical part."),
    _I.BEAM_AXIS_NOT_EVALUATED: _rule(_P.PART, _C.BEAM_AXIS_CLEARANCE, _S.WARNING, _E.UNKNOWN, _NOT_EVALUATED),
    _I.RESERVED_SPACE_INTRUSION: _rule(_P.PART_AND_DESIGN_COMPONENT, _C.PART_COLLISION, _S.ERROR, _E.FAIL,
                                       "This OpenSci-supplied part intrudes into the space reserved for a customer-supplied "
                                       "design component."),
}
del _C, _S, _E, _P, _I, _NOT_EVALUATED


class MetricNameV1(StrEnum):
    OVERLAP_VOLUME_MM3 = "OVERLAP_VOLUME_MM3"
    PENETRATION_MM = "PENETRATION_MM"
    ENGAGED_VOLUME_MM3 = "ENGAGED_VOLUME_MM3"
    DEEP_OVERLAP_VOLUME_MM3 = "DEEP_OVERLAP_VOLUME_MM3"
    POSITION_ERROR_MM = "POSITION_ERROR_MM"
    ANGLE_ERROR_DEG = "ANGLE_ERROR_DEG"
    GAP_MM = "GAP_MM"
    OUTSIDE_DISTANCE_MM = "OUTSIDE_DISTANCE_MM"
    HOLE_DISTANCE_MM = "HOLE_DISTANCE_MM"
    QUATERNION_NORM = "QUATERNION_NORM"
    TRIANGLE_COUNT = "TRIANGLE_COUNT"
    AXIS_LENGTH_IN_PART_MM = "AXIS_LENGTH_IN_PART_MM"
    COLUMNS_CAST = "COLUMNS_CAST"  # integration columns cast for one pair's overlap measure
    COLUMNS_SKIPPED = "COLUMNS_SKIPPED"  # of those, columns left out because a part's surface winding did not close there


class MetricV1(StrictContractModel):
    name: MetricNameV1
    value: float


class AssemblyIssueV1(StrictContractModel):
    code: AssemblyIssueCodeV1
    check: AssemblyCheckKindV1
    severity: IssueSeverityV1
    part_refs: tuple[PartRef, ...] = Field(default=(), max_length=2, description="Involved parts, sorted.")
    mate_ref: Annotated[str, StringConstraints(strict=True, pattern=r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$", max_length=64)] | None = None
    design_instance_id: InstanceKey | None = None
    metrics: tuple[MetricV1, ...] = Field(default=(), description="Named measurements, ordered by name.")
    message: str

    @model_validator(mode="after")
    def _fixed(self) -> AssemblyIssueV1:
        rule = ISSUE_RULES[self.code]
        if self.check is not rule.check or self.severity is not rule.severity or self.message != rule.message:
            raise ValueError(f"issue {self.code.value}: check, severity and message must be the fixed ISSUE_RULES entry")
        if list(self.part_refs) != sorted(set(self.part_refs)):
            raise ValueError(f"issue {self.code.value}: part_refs must be unique and sorted")
        component = self.design_instance_id is not None
        names_component = rule.subject in (IssueSubjectV1.DESIGN_COMPONENT, IssueSubjectV1.PART_AND_DESIGN_COMPONENT)
        if len(self.part_refs) not in SUBJECT_PART_COUNTS[rule.subject] or component != names_component:
            raise ValueError(f"issue {self.code.value}: its subject is {rule.subject.value}, but it names {len(self.part_refs)} "
                             f"part(s) and {'a' if component else 'no'} design instance")
        mate_rule = MATE_REF_RULES.get(self.code.value)
        wants_mate = mate_rule == "REQUIRED" or (mate_rule == "WITH_PAIR" and len(self.part_refs) == 2)
        if (self.mate_ref is not None) != wants_mate:
            raise ValueError(f"issue {self.code.value}: mate_ref is {'required' if wants_mate else 'not allowed'} here")
        names = [m.name.value for m in self.metrics]
        if names != sorted(set(names)):
            raise ValueError(f"issue {self.code.value}: metrics must be unique and ordered by name")
        return self

    def sort_key(self) -> tuple[Any, ...]:
        return (ASSEMBLY_CHECK_ORDER.index(self.check), self.code.value, self.part_refs, self.mate_ref or "", self.design_instance_id or "")


class AssemblyCheckResultV1(StrictContractModel):
    check: AssemblyCheckKindV1
    status: LocalValidationStatusV1
    basis: CheckBasisV1
    evaluated: int = Field(ge=0, description="How many subjects (parts, pairs, mates, holes, axis segments) were evaluated.")


class PartValidationV1(StrictContractModel):
    """Per-part view for a renderer overlay: identity from the package, and the worst status of its issues."""

    part_ref: PartRef
    category: PartCategoryV1
    opensci_sku: Annotated[str, StringConstraints(strict=True, pattern=r"^OS(-[A-Z0-9]+)+$", max_length=120)]
    commercial_line_ref: Annotated[str, StringConstraints(strict=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$", max_length=64)] | None = None
    geometry_fidelity: GeometryFidelityV1
    status: LocalValidationStatusV1


class ValidatedPackageRefV1(StrictContractModel):
    """Which package was checked: its content hash and the binding its manifest declares -- the resolution of a
    commercial package, or the local plan revision of a LOCAL_PLAN package (exactly one of them)."""

    package_content_hash: Sha256Digest
    package_revision: int = Field(ge=1)
    project: ValidatedProjectRefV1
    resolution: ResolutionRefV1 | None = None
    local_validation_hash: HashRef | None = None
    commercial_resolution_id: OpenSciId | None = None
    local_plan: LocalPlanRefV1 | None = None

    @model_validator(mode="after")
    def _one_basis(self) -> ValidatedPackageRefV1:
        if (self.resolution is None) == (self.local_plan is None):
            raise ValueError("a checked package names exactly one of its resolution and its local plan")
        if self.local_plan is not None and self.commercial_resolution_id is not None:
            raise ValueError("a locally generated package has no commercial resolution id")
        return self


def validation_schema_version_for(package: ValidatedPackageRefV1) -> Literal["2.0", "2.1"]:
    """The result version a writer states for a checked package: 2.1 for a LOCAL_PLAN package, else 2.0."""

    return ASSEMBLY_LOCAL_VALIDATION_SCHEMA_VERSION_2_1 if package.local_plan is not None else ASSEMBLY_LOCAL_VALIDATION_SCHEMA_VERSION


class CheckerRefV1(StrictContractModel):
    checker_id: Literal["opensci.assembly_layout_checker"] = ASSEMBLY_CHECKER_ID
    checker_version: BuildVersion
    runtime_kind: LocalRuntimeKindV1
    engine_package: EnginePackageName = ENGINE_PACKAGE_NAME
    engine_version: BuildVersion
    engine_build_id: BuildVersion


class CheckerTolerancesV1(StrictContractModel):
    """The thresholds this result was produced with (mm unless named otherwise)."""

    overlap_noise_mm: float = Field(gt=0, description="Overlap thinner than this is surface contact, not an issue.")
    overlap_noise_volume_mm3: float = Field(gt=0, description="Overlap smaller than this volume is surface contact, not an issue.")
    collision_penetration_mm: float = Field(gt=0, description="Penetration at or above this (with the volume below) is a collision.")
    collision_volume_mm3: float = Field(gt=0, description="Overlap volume at or above this (with the penetration above) is a collision; so "
                                                        "is a deep core (at least collision_penetration_mm thick along all three axes) "
                                                        "of this volume.")
    buried_fraction: float = Field(gt=0, le=1, description="An overlap of at least this fraction of the smaller part's volume is a collision.")
    engagement_radial_allowance_mm: float = Field(ge=0, description="Added to an interface's nominal radius for its engagement zone.")
    engagement_axial_allowance_mm: float = Field(ge=0, description="Added to an engagement zone's axial range at both ends.")
    fastener_engagement_diameters: float = Field(gt=0, description="Axial reach of a threaded engagement, in nominal diameters.")
    mate_position_tolerance_mm: float = Field(gt=0, description="Datum offset above this is reported as a misaligned mate.")
    mate_angle_tolerance_deg: float = Field(gt=0, description="Datum rotation above this is reported as a misaligned mate.")
    detachment_distance_mm: float = Field(gt=0, description="Datum offset or geometry gap at or above this is a detached part.")
    detachment_angle_deg: float = Field(gt=0, description="Datum rotation at or above this is a detached part.")
    board_outline_tolerance_mm: float = Field(ge=0, description="Allowed excursion beyond the breadboard outline.")
    quaternion_norm_tolerance: float = Field(gt=0, description="Allowed deviation of a quaternion norm from 1.")
    sample_spacing_mm: float = Field(gt=0, description="Target spacing of the overlap integration columns.")
    max_columns_per_axis: int = Field(ge=1, description="Upper bound of integration columns per axis and pair.")
    max_total_triangles: int = Field(ge=1, description="Work limit of the local checker: the most triangles of one package it "
                                                       "checks (above it the geometric checks are not run).")

    @model_validator(mode="after")
    def _ordered(self) -> CheckerTolerancesV1:
        if not (self.overlap_noise_mm < self.collision_penetration_mm and self.overlap_noise_volume_mm3 < self.collision_volume_mm3
                and self.mate_position_tolerance_mm < self.detachment_distance_mm
                and self.mate_angle_tolerance_deg < self.detachment_angle_deg):
            raise ValueError("every contact / mate tolerance must be below its collision / detachment threshold")
        return self


class InterferenceV1(StrEnum):
    """Outcome of the collision and support-engagement checks at the recorded tolerances."""

    NO_INTERFERENCE = "NO_INTERFERENCE"  # no pair of parts interferes
    INTERFERENCE = "INTERFERENCE"  # at least one pair of parts interferes
    NOT_DETERMINED = "NOT_DETERMINED"  # a part could not be evaluated, so the outcome is open


#: Least detailed first.
FIDELITY_ORDER: tuple[GeometryFidelityV1, ...] = (GeometryFidelityV1.NOMINAL_SHAPE, GeometryFidelityV1.SIMPLIFIED_MESH)


class ValidationSummaryV1(StrictContractModel):
    """What was checked, on which geometry, and whether interference was found."""

    geometry_basis: Literal["ASSEMBLY_PREVIEW_PART_GEOMETRY"] = "ASSEMBLY_PREVIEW_PART_GEOMETRY"
    geometry_fidelity: GeometryFidelityV1 = Field(description="The least detailed part geometry the checks used.")
    interference: InterferenceV1
    statement: Literal[
        "Layout and collision check of the resolved assembly on the part geometry of its Assembly Preview Package: poses, "
        "part geometry, support structure and attachment, breadboard containment and fixation, support engagement, "
        "part-to-part collision and beam-axis clearance, at the tolerances recorded in this result."
    ] = SUMMARY_STATEMENT


def interference_outcome(statuses: dict[AssemblyCheckKindV1, LocalValidationStatusV1]) -> InterferenceV1:
    """The interference outcome of a set of check statuses (collision and support engagement decide it)."""

    contact = [statuses[AssemblyCheckKindV1.PART_COLLISION], statuses[AssemblyCheckKindV1.EXPECTED_ENGAGEMENT]]
    if LocalValidationStatusV1.FAIL in contact:
        return InterferenceV1.INTERFERENCE
    if all(s in (LocalValidationStatusV1.PASS, LocalValidationStatusV1.WARNING) for s in contact):
        return InterferenceV1.NO_INTERFERENCE
    return InterferenceV1.NOT_DETERMINED


def _worst(statuses: list[LocalValidationStatusV1]) -> LocalValidationStatusV1:
    relevant = [s for s in statuses if s is not LocalValidationStatusV1.NOT_APPLICABLE]
    return max(relevant, key=status_severity) if relevant else LocalValidationStatusV1.NOT_APPLICABLE


class AssemblyLocalValidationV1(StrictContractModel):
    """The structured result of the local assembly layout check of one verified Assembly Preview Package."""

    schema_id: Literal["opensci.assembly_local_validation"] = Field(ASSEMBLY_LOCAL_VALIDATION_SCHEMA_ID, alias="schema")
    schema_version: Literal["2.0", "2.1"] = Field(
        ASSEMBLY_LOCAL_VALIDATION_SCHEMA_VERSION, description="2.0: a commercial package; 2.1: a LOCAL_PLAN package.")
    package: ValidatedPackageRefV1
    checker: CheckerRefV1
    tolerances: CheckerTolerancesV1
    status: LocalValidationStatusV1
    summary: ValidationSummaryV1
    checks: tuple[AssemblyCheckResultV1, ...]
    issues: tuple[AssemblyIssueV1, ...] = Field(default=(), max_length=MAX_ISSUES)
    parts: tuple[PartValidationV1, ...] = Field(min_length=1)
    result_hash: Sha256Digest = Field(description="osc1 digest of this document without result_hash.")

    @model_validator(mode="after")
    def _check(self) -> AssemblyLocalValidationV1:  # noqa: C901 - one flat list of consistency rules
        if self.schema_version != validation_schema_version_for(self.package):
            raise ValueError("a result states 2.0 exactly for a commercial package and 2.1 exactly for a LOCAL_PLAN package")
        if tuple(c.check for c in self.checks) != ASSEMBLY_CHECK_ORDER:
            raise ValueError("checks must list every AssemblyCheckKindV1 once, in ASSEMBLY_CHECK_ORDER")
        for check in self.checks:
            if check.basis is not CHECK_BASIS[check.check]:
                raise ValueError(f"check {check.check.value}: basis must be {CHECK_BASIS[check.check].value}")
        keys = [i.sort_key() for i in self.issues]
        if keys != sorted(set(keys)):
            raise ValueError("issues must be unique and ordered by check order, code, part_refs, mate_ref and design instance")
        by_check: dict[AssemblyCheckKindV1, list[LocalValidationStatusV1]] = {c.check: [] for c in self.checks}
        for issue in self.issues:
            by_check[issue.check].append(ISSUE_RULES[issue.code].status_effect)
        for check in self.checks:
            effects = by_check[check.check]
            informational = all(e is LocalValidationStatusV1.PASS for e in effects)  # INFO issues decide nothing
            if (check.status is LocalValidationStatusV1.NOT_APPLICABLE) != (informational and not check.evaluated):
                raise ValueError(f"check {check.check.value}: NOT_APPLICABLE means, and only it means, that nothing was "
                                 "evaluated and no non-informational issue was raised")
            if check.status is LocalValidationStatusV1.NOT_APPLICABLE:
                continue
            expected = _worst([LocalValidationStatusV1.PASS, *effects])
            if check.status is not expected:
                raise ValueError(f"check {check.check.value}: status must be {expected.value} (worst of its issues)")
        if self.checks[0].status is not LocalValidationStatusV1.PASS:
            raise ValueError("a result exists only for a package that passed verification (PACKAGE_INTEGRITY PASS)")
        if self.status is not _worst([c.status for c in self.checks]):
            raise ValueError("status must be the worst check status")

        refs = [p.part_ref for p in self.parts]
        if refs != sorted(set(refs)):
            raise ValueError("parts must be unique and sorted by part_ref")
        for issue in self.issues:
            if unknown := set(issue.part_refs) - set(refs):
                raise ValueError(f"issue {issue.code.value} names unknown part(s) {sorted(unknown)}")
        for part in self.parts:
            effects = [ISSUE_RULES[i.code].status_effect for i in self.issues if part.part_ref in i.part_refs]
            if part.status is not _worst([LocalValidationStatusV1.PASS, *effects]):
                raise ValueError(f"part {part.part_ref}: status must be the worst status of the issues naming it")

        least = next(f for f in FIDELITY_ORDER if f in {p.geometry_fidelity for p in self.parts})
        if self.summary.geometry_fidelity is not least:
            raise ValueError(f"summary.geometry_fidelity must be the least detailed part fidelity, {least.value}")
        outcome = interference_outcome({c.check: c.status for c in self.checks})
        if self.summary.interference is not outcome:
            raise ValueError(f"summary.interference must be {outcome.value} for these check statuses")
        if self.result_hash != osc1_digest(self.hashed_payload()):
            raise ValueError("result_hash does not match the document")
        return self

    def payload(self) -> dict[str, Any]:
        """Canonical serialized form (``None`` fields omitted)."""

        return self.model_dump(mode="json", by_alias=True, exclude_none=True)

    def hashed_payload(self) -> dict[str, Any]:
        payload = self.payload()
        del payload["result_hash"]
        return payload


def assembly_validation_hash(payload_without_hash: dict[str, Any]) -> str:
    """``result_hash`` = osc1 digest of the result payload without that field."""

    return osc1_digest(payload_without_hash)
