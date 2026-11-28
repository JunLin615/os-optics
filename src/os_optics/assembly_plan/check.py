"""The check of a plan revision: plan-level checks, the layout check of its preview package, optics freshness, status.

``check_content(content, context)`` runs, on one revision's decisions:

1. the plan-level checks (``interfaces.plan_findings``);
2. the projection of the revision into its LOCAL_PLAN Assembly Preview Package (``projection.project_plan``) and the
   unchanged layout and collision check of that package (``assembly.validate_assembly_layout``), whose issues become
   findings (``findings.finding_from_layout_issue``: part refs are item ids, a design anchor maps to its item, a mate to
   its link kind, an item resting on a conservative identity profile gives basis CONSERVATIVE_PROFILE). A plan the scene
   cannot carry (the user's own hardware), hardware the scene leaves out (the resolved part of an unresolved chain) and
   a projection that fails give ``LAYOUT_NOT_EVALUATED`` (UNKNOWN) instead, so an unevaluated layout never reads as a
   passed one;
3. the freshness of the bound optics run for the revision's Project (``local.freshness.run_freshness`` over the run's
   Local Validation record; a run that is not CURRENT gives ``OPTICS_RESULTS_STALE``);
4. the status (``PlanStatusV1``): state from the severities, optics results, the layout check's reference, the evidence
   summary (items resting on conservative profiles or preview assumptions) and the purchase summary.

The DERIVE findings the caller carries (``findings.carry_derive_findings``) are merged in (a CHECK finding with the
same id wins). Nothing is written.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .._vendor.shared_contracts.assembly_preview import AssemblyLocalValidationV1
from .._version import ENGINE_VERSION
from .bom import purchase_line_count
from .contract import (AssemblyPlanContentV1, AssemblyPlanError, AssemblyPlanRevisionV1, CheckedWithV1, EvidenceSummaryV1,
                       FindingCheckV1, FindingSourceV1, FindingV1, ItemIdentityKindV1, LayoutValidationRefV1, OpticsResultsStateV1,
                       PlanFactBasisV1, PlanFindingCodeV1, PlanStatusV1, PurchaseSummaryV1, SupplyV1, build_revision,
                       finding_counts, plan_state)
from .findings import finding_from_layout_issue, make_finding, merge_findings
from .interfaces import plan_findings
from .kernel.models import RESOLVER_KERNEL_VERSION

#: The version of the plan-level checks (``CheckedWithV1.plan_checks_version``).
PLAN_CHECKS_VERSION = "1.0.0"
CHECKED_WITH = CheckedWithV1(checker_version=ENGINE_VERSION, kernel_version=RESOLVER_KERNEL_VERSION,
                             plan_checks_version=PLAN_CHECKS_VERSION)
_UNCHECKED_STATUS = PlanStatusV1.model_validate({
    "state": "READY", "counts": {"error": 0, "unknown": 0, "warning": 0, "info": 0}, "optics_results": "NOT_RUN",
    "evidence": {}, "purchase": {"selected_lines": 0, "excluded_items": 0, "unresolved_items": 0}})
#: ``details.reason`` of a LAYOUT_NOT_EVALUATED finding.
NOT_EVALUATED_USER_HARDWARE = "USER_HARDWARE_NOT_PROJECTABLE"
NOT_EVALUATED_PROJECTION = "PROJECTION_FAILED"
#: Characters of a refusal message a finding keeps.
MESSAGE_EXCERPT = 240
_FRESHNESS_STATE = {"CURRENT": OpticsResultsStateV1.CURRENT, "HISTORICAL": OpticsResultsStateV1.STALE,
                    "UNDETERMINED": OpticsResultsStateV1.UNDETERMINED}


@dataclass(frozen=True)
class CheckOutcome:
    """A check of one revision: its findings and status, the package and layout check behind them, the run freshness."""

    findings: tuple[FindingV1, ...]
    status: PlanStatusV1
    checked_with: CheckedWithV1
    projection: Any = None
    validation: AssemblyLocalValidationV1 | None = None
    freshness: dict[str, Any] | None = None

    def revision(self, content: Any) -> AssemblyPlanRevisionV1:
        """The revision of ``content`` with this check recorded."""

        return build_revision(_payload(content), findings=self.findings, status=self.status, checked_with=self.checked_with)


def _payload(content: Any) -> dict[str, Any]:
    return content.payload() if hasattr(content, "payload") else dict(content)


def unchecked_revision(content: Any) -> AssemblyPlanRevisionV1:
    """``content`` as a revision with an empty recorded check (its plan digest is final: findings never enter it)."""

    return build_revision(_payload(content), findings=[], status=_UNCHECKED_STATUS, checked_with=CHECKED_WITH)


def _not_evaluated(items: Iterable[str], reason: str, **details: Any) -> FindingV1:
    return make_finding(PlanFindingCodeV1.LAYOUT_NOT_EVALUATED, tuple(items), check=FindingCheckV1.PLAN_LAYOUT,
                        source=FindingSourceV1.CHECK, details={"reason": reason, **details})


def conservative_items(content: AssemblyPlanContentV1, context: Any) -> list[str]:
    out = []
    for item in content.items:
        if item.identity.kind in (ItemIdentityKindV1.OS_MECH, ItemIdentityKindV1.USER_MECH):
            identity = context.mechanical.identity(item.identity.opensci_sku)
            if identity is not None and identity.profile.kind == "CONSERVATIVE":
                out.append(item.item_id)
    return sorted(out)


def _assumption_items(content: AssemblyPlanContentV1) -> list[str]:
    out = []
    for item in content.items:
        placement = item.placement
        if placement is None:
            continue
        if (any(s.basis is PlanFactBasisV1.PREVIEW_ASSUMPTION for s in placement.settings)
                or any(r.basis is PlanFactBasisV1.PREVIEW_ASSUMPTION for r in placement.ranges)):
            out.append(item.item_id)
    return sorted(out)


def layout_findings(revision: AssemblyPlanRevisionV1, context: Any,
                    tolerances: Any = None) -> tuple[list[FindingV1], Any, AssemblyLocalValidationV1 | None]:
    """``(findings, projection, validation)`` of the layout check of ``revision``'s preview package (module docstring)."""

    from ..assembly import validate_assembly_layout
    from .projection import project_plan

    user = sorted(i.item_id for i in revision.items if i.identity.kind is ItemIdentityKindV1.USER_MECH)
    if user:
        return [_not_evaluated((item_id,), NOT_EVALUATED_USER_HARDWARE) for item_id in user], None, None
    try:
        projection = project_plan(revision, context.for_revision(revision))
    except AssemblyPlanError as exc:
        return [_not_evaluated((), NOT_EVALUATED_PROJECTION, code=exc.code.value, message=exc.message[:MESSAGE_EXCERPT])], None, None
    validation = validate_assembly_layout(projection.package, tolerances=tolerances)
    findings = [_not_evaluated((o.item_id,), o.reason) for o in projection.omitted]
    links = {frozenset((link.a_item_id, link.b_item_id)): link.kind for link in revision.links}
    mates = {m.mate_ref: links[frozenset((m.part_a_ref, m.part_b_ref))] for m in projection.package.scene.mates
             if frozenset((m.part_a_ref, m.part_b_ref)) in links}
    parts = {ref: ref for ref in projection.parts}
    conservative = conservative_items(revision, context)
    instances = dict(projection.anchors)
    for issue in validation.issues:
        refs = {ref: parts.get(ref, ref) for ref in issue.part_refs}
        try:
            findings.append(finding_from_layout_issue(issue, item_of_part=refs, item_of_instance=instances,
                                                      link_kind_of_mate=mates, conservative_items=conservative))
        except (KeyError, ValueError):
            findings.append(_not_evaluated((), NOT_EVALUATED_PROJECTION, code=issue.code.value,
                                           message="a layout issue names a design component the plan does not hold"))
    return findings, projection, validation


def optics_state(revision: AssemblyPlanContentV1, context: Any, run: Any = None) -> tuple[OpticsResultsStateV1, dict[str, Any] | None]:
    """The freshness of the bound run (or of ``run``, a run directory / record a caller names) for the plan's Project."""

    from ..local.freshness import RunNotReadable, run_freshness

    source = run if run is not None else context.local_validation
    bound = revision.bindings.optics_run
    if source is None and bound is None:
        return OpticsResultsStateV1.NOT_RUN, None
    if source is None:
        current = bound.project_hash.digest == context.project.content_hash()  # type: ignore[union-attr]
        freshness = {"freshness": "CURRENT" if current else "HISTORICAL", "reasons": [] if current else ["PHYSICS_CHANGED"],
                     "basis": "PROJECT_HASH"}
        return (OpticsResultsStateV1.CURRENT if current else OpticsResultsStateV1.STALE), freshness
    try:
        source = source.payload() if hasattr(source, "payload") else source
        freshness = run_freshness(context.project, source, catalog=context.catalog)
    except (RunNotReadable, ValueError) as exc:
        return OpticsResultsStateV1.UNDETERMINED, {"freshness": "UNDETERMINED", "reasons": [type(exc).__name__]}
    return _FRESHNESS_STATE.get(freshness["freshness"], OpticsResultsStateV1.UNDETERMINED), freshness


def check_content(content: Any, context: Any, *, derive_findings: Iterable[FindingV1] = (), run: Any = None,
                  tolerances: Any = None) -> CheckOutcome:
    """The check of one revision's decisions (module docstring)."""

    revision = unchecked_revision(content)
    found = plan_findings(revision, context)
    layout, projection, validation = layout_findings(revision, context, tolerances)
    found += layout
    state, freshness = optics_state(revision, context, run)
    if state in (OpticsResultsStateV1.STALE, OpticsResultsStateV1.UNDETERMINED):
        found.append(make_finding(PlanFindingCodeV1.OPTICS_RESULTS_STALE, (), check=FindingCheckV1.PLAN_OPTICS,
                                  source=FindingSourceV1.CHECK,
                                  details={"freshness": (freshness or {}).get("freshness"),
                                           "reasons": list((freshness or {}).get("reasons") or [])}))
    findings = merge_findings(found, derive_findings)
    counts = finding_counts(findings)
    layout_ref = None if validation is None else LayoutValidationRefV1(
        status=validation.status, result_hash=validation.result_hash, checker_version=validation.checker.checker_version)
    items = revision.items
    status = PlanStatusV1(
        state=plan_state(counts), counts=counts, optics_results=state, layout_validation=layout_ref,
        evidence=EvidenceSummaryV1(conservative_profile_items=tuple(conservative_items(revision, context)),
                                   preview_assumption_items=tuple(_assumption_items(revision))),
        purchase=PurchaseSummaryV1(selected_lines=purchase_line_count(items),
                                   excluded_items=sum(1 for i in items if not i.purchase.selected),
                                   unresolved_items=sum(1 for i in items if i.supply is SupplyV1.UNRESOLVED)))
    return CheckOutcome(findings=findings, status=status, checked_with=CHECKED_WITH, projection=projection, validation=validation,
                        freshness=freshness)


__all__ = ["CHECKED_WITH", "PLAN_CHECKS_VERSION", "CheckOutcome", "check_content", "conservative_items", "layout_findings",
           "optics_state", "unchecked_revision"]
