"""Turn check outcomes into the canonical ``AssemblyLocalValidationV1`` document (ordered, hashed, validated)."""

from __future__ import annotations

from .._vendor.shared_contracts.assembly_preview import (
    ASSEMBLY_CHECK_ORDER,
    CHECK_BASIS,
    ISSUE_RULES,
    AssemblyCheckKindV1,
    AssemblyCheckResultV1,
    AssemblyIssueV1,
    AssemblyLocalValidationV1,
    AssemblyPreviewPackageV1,
    CheckerRefV1,
    CheckerTolerancesV1,
    FIDELITY_ORDER,
    MetricV1,
    PartValidationV1,
    ValidatedPackageRefV1,
    ValidationSummaryV1,
    assembly_validation_hash,
    interference_outcome,
    validation_schema_version_for,
)
from .._vendor.shared_contracts.optical_design.local_validation import LocalValidationStatusV1, status_severity
from .._version import DISTRIBUTION_NAME, ENGINE_VERSION
from ..local.record import detect_runtime_kind
from ..manifest.build import engine_build_id
from .checks import CheckOutcome, Finding
from .tolerances import ASSEMBLY_CHECKER_VERSION

_S = LocalValidationStatusV1


def _worst(statuses: list[LocalValidationStatusV1]) -> LocalValidationStatusV1:
    relevant = [s for s in statuses if s is not _S.NOT_APPLICABLE]
    return max(relevant, key=status_severity) if relevant else _S.NOT_APPLICABLE


def _issue(finding: Finding) -> AssemblyIssueV1:
    rule = ISSUE_RULES[finding.code]
    return AssemblyIssueV1(code=finding.code, check=rule.check, severity=rule.severity, part_refs=finding.part_refs,
                           mate_ref=finding.mate_ref, design_instance_id=finding.design_instance_id,
                           metrics=tuple(MetricV1(name=n, value=v) for n, v in finding.metrics), message=rule.message)


def _check(outcome: CheckOutcome) -> AssemblyCheckResultV1:
    effects = [ISSUE_RULES[f.code].status_effect for f in outcome.findings]
    if all(e is _S.PASS for e in effects) and (not outcome.applicable or not outcome.evaluated):  # nothing checked; INFO only
        status = _S.NOT_APPLICABLE
    else:
        status = _worst([_S.PASS, *effects])
    return AssemblyCheckResultV1(check=outcome.check, status=status, basis=CHECK_BASIS[outcome.check],
                                 evaluated=0 if status is _S.NOT_APPLICABLE else outcome.evaluated)


def build_result(package: AssemblyPreviewPackageV1, outcomes: list[CheckOutcome], tolerances: CheckerTolerancesV1) -> AssemblyLocalValidationV1:
    integrity = CheckOutcome(AssemblyCheckKindV1.PACKAGE_INTEGRITY, evaluated=len(package.files))
    by_kind = {o.check: o for o in [integrity, *outcomes]}
    ordered = [by_kind[kind] for kind in ASSEMBLY_CHECK_ORDER]
    checks = [_check(o) for o in ordered]
    issues = sorted((_issue(f) for o in ordered for f in o.findings), key=AssemblyIssueV1.sort_key)

    scene, manifest = package.scene, package.manifest
    parts = []
    for part in sorted(scene.parts, key=lambda p: p.part_ref):
        effects = [ISSUE_RULES[i.code].status_effect for i in issues if part.part_ref in i.part_refs]
        parts.append(PartValidationV1(part_ref=part.part_ref, category=part.category, opensci_sku=part.identity.opensci_sku,
                                      commercial_line_ref=part.commercial_line_ref, geometry_fidelity=part.geometry.fidelity,
                                      status=_worst([_S.PASS, *effects])))
    fidelities = {p.geometry.fidelity for p in scene.parts}
    summary = ValidationSummaryV1(geometry_fidelity=next(f for f in FIDELITY_ORDER if f in fidelities),
                                  interference=interference_outcome({c.check: c.status for c in checks}))
    binding = manifest.binding
    package_ref = ValidatedPackageRefV1(
        package_content_hash=manifest.package_content_hash, package_revision=manifest.package_revision, project=binding.project,
        resolution=binding.resolution, local_validation_hash=binding.local_validation_hash,
        commercial_resolution_id=binding.commercial_resolution_id, local_plan=binding.local_plan)
    checker = CheckerRefV1(checker_version=ASSEMBLY_CHECKER_VERSION, runtime_kind=detect_runtime_kind(), engine_package=DISTRIBUTION_NAME,
                           engine_version=ENGINE_VERSION, engine_build_id=engine_build_id())

    def dump(model) -> object:
        return model.model_dump(mode="json", by_alias=True, exclude_none=True)

    body = {
        "schema": "opensci.assembly_local_validation",
        "schema_version": validation_schema_version_for(package_ref),
        "package": dump(package_ref),
        "checker": dump(checker),
        "tolerances": dump(tolerances),
        "status": _worst([c.status for c in checks]).value,
        "summary": dump(summary),
        "checks": [dump(c) for c in checks],
        "issues": [dump(i) for i in issues],
        "parts": [dump(p) for p in parts],
    }
    result = AssemblyLocalValidationV1.model_validate({**body, "result_hash": assembly_validation_hash(body)})
    if result.hashed_payload() != body:  # the canonical form must be exactly what was hashed
        raise AssertionError("assembly validation payload is not in canonical form")
    return result
