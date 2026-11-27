"""Finding rules of the assembly plan: the code table, fixed messages, finding ids, layout-issue conversion and the merge
rules that carry findings from one revision to the next.

Every finding code has one ``FindingRule``: its severity (fixed, except ``INTERFACE_MISMATCH`` with the rule
``THICKNESS_RANGE_UNKNOWN``, which is UNKNOWN), how many items it names (0 = the whole plan, 1 = a part, 2 = a pair),
the chain-link kind it is about (fixed, any or none), the checks that may report it, its one English message (no
interpolation: numbers go to ``details``; translation is the drawing's job) and the ``details`` keys that tell two
findings of the same code and items apart (they enter the id).

``finding_id = "f-" + osc1_digest({code, item_ids, interface, details_key})[:12]``, ``details_key`` = the rule's id keys
present in ``details``: the same issue keeps its id across revisions and checker runs.

Layout-validation findings keep the ``AssemblyIssueCodeV1`` code of ``validate_assembly_layout``; their severity follows
the issue's status effect (FAIL -> ERROR, UNKNOWN / OUT_OF_MODEL_SCOPE -> UNKNOWN, WARNING -> WARNING, else INFO), their
message is the ``ISSUE_RULES`` message, and part refs map to item ids (they are equal for a projected plan).

Lifetime: CHECK findings are recomputed by every check; DERIVE findings are copied into later revisions until an op
touches one of their items (``carry_derive_findings``). When a CHECK and a DERIVE finding share an id, the CHECK one is
kept (``merge_findings``).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .._vendor.shared_contracts.assembly_preview.validation import (ISSUE_RULES, SUBJECT_PART_COUNTS, AssemblyIssueCodeV1,
                                                                     AssemblyIssueV1, IssueSubjectV1, LocalValidationStatusV1)
from .._vendor.shared_contracts.common.hashes import osc1_digest
from .contract import (FINDING_ID_DIGITS, ChainLinkKindV1, FindingBasisV1, FindingCheckV1, FindingCode, FindingSeverityV1,
                       FindingSourceV1, FindingV1, PlanFindingCodeV1, normalize_numbers)

_F = PlanFindingCodeV1
_S = FindingSeverityV1
_C = FindingCheckV1
_L = ChainLinkKindV1

#: ``FindingRule.interface`` value of a code whose findings may name any link kind (or none).
ANY_INTERFACE = "ANY"


@dataclass(frozen=True)
class FindingRule:
    severity: FindingSeverityV1
    item_counts: tuple[int, ...]
    interface: ChainLinkKindV1 | str | None
    checks: frozenset[FindingCheckV1]
    message: str
    id_keys: tuple[str, ...] = ()


#: The ``rule`` values of an INTERFACE_MISMATCH finding (``details.rule``).
INTERFACE_RULES = ("SHAPE", "SIZE", "THICKNESS_RANGE", "THICKNESS_RANGE_UNKNOWN", "TRANSMISSIVE", "CLEAR_APERTURE", "THREAD",
                   "POST_DIAMETER", "BOARD_THREAD")
#: The one INTERFACE_MISMATCH rule that is UNKNOWN rather than ERROR.
UNKNOWN_INTERFACE_RULE = "THICKNESS_RANGE_UNKNOWN"


def _rule(severity: FindingSeverityV1, counts: tuple[int, ...], interface: Any, checks: Iterable[FindingCheckV1], message: str,
          id_keys: tuple[str, ...] = ()) -> FindingRule:
    return FindingRule(severity, counts, interface, frozenset(checks), message, id_keys)


_PLAN_RULES: dict[PlanFindingCodeV1, FindingRule] = {
    _F.UNSUPPORTED_OPTIC_ORIENTATION: _rule(
        _S.ERROR, (1,), None, (_C.DERIVE, _C.PLAN_LAYOUT),
        "This optic's axis is not horizontal, or it is a non-round optic without a vertical edge: no post mount can hold it."),
    _F.MOUNT_NOT_FOUND: _rule(
        _S.WARNING, (1,), _L.OPTIC_IN_MOUNT, (_C.DERIVE,),
        "No compatible mount was found for this optic; it is placed at its design position for you to mount."),
    _F.MISSING_MECHANICAL_DATA: _rule(
        _S.UNKNOWN, (1,), ANY_INTERFACE, (_C.DERIVE, _C.PLAN_INTERFACES),
        "A fact this check needs is not recorded for this item (see missing_data); the check is not evaluated."),
    _F.HEIGHT_NOT_REACHABLE: _rule(
        _S.ERROR, (1, 2), _L.POST_IN_HOLDER, (_C.DERIVE, _C.PLAN_INTERFACES),
        "No post and holder reach the axis height of this optic."),
    _F.NO_COMMON_BEAM_HEIGHT: _rule(
        _S.ERROR, (1, 2), None, (_C.DERIVE,),
        "No beam height above the board is reachable by the hardware of these optics together."),
    _F.BOARD_TOO_SMALL: _rule(
        _S.ERROR, (1,), _L.HOLDER_ON_BOARD, (_C.DERIVE,),
        "The layout's footprint does not fit on the largest allowed board."),
    _F.SUPPORTS_COLLIDE: _rule(
        _S.ERROR, (1, 2), None, (_C.DERIVE,),
        "The supports of these items collide; more spacing between the components is needed."),
    _F.BEAM_CLEARANCE: _rule(
        _S.ERROR, (1, 2), None, (_C.DERIVE,),
        "This hardware blocks the beam to or from the optic."),
    _F.NO_FIXING_HOLE: _rule(
        _S.ERROR, (1, 2), _L.HOLDER_ON_BOARD, (_C.DERIVE, _C.PLAN_LAYOUT),
        "No free board hole lies within the fork slot of this holder."),
    _F.BEAM_CLIPPED_AT_INCIDENCE: _rule(
        _S.ERROR, (1, 2), _L.OPTIC_IN_MOUNT, (_C.DERIVE, _C.PLAN_INTERFACES),
        "The mount's bore clips the beam footprint at this optic's angle of incidence."),
    _F.OBLIQUE_CLEARANCE_UNVERIFIED: _rule(
        _S.UNKNOWN, (1, 2), _L.OPTIC_IN_MOUNT, (_C.PLAN_INTERFACES,),
        "The beam clearance through this mount rests on assumed bore facts and is not verified."),
    _F.OPTIC_TILT_NOT_SUPPORTED: _rule(
        _S.ERROR, (1,), None, (_C.DERIVE,),
        "The tilt of this optic is outside what its hardware can hold."),
    _F.DOF_NOT_DELIVERED: _rule(
        _S.WARNING, (1, 2), None, (_C.DERIVE, _C.PLAN_INTERFACES),
        "A degree of freedom this component retains is not delivered by its hardware.", ("axis",)),
    _F.DOF_MANUAL_FALLBACK: _rule(
        _S.INFO, (1,), None, (_C.PLAN_INTERFACES,),
        "A numerically controlled degree of freedom is delivered as a manual adjustment.", ("axis",)),
    _F.SEARCH_BUDGET_EXHAUSTED: _rule(
        _S.UNKNOWN, (0,), None, (_C.DERIVE,),
        "The hardware search stopped at its work budget or time limit; the plan is the best candidate found."),
    _F.DERIVE_FELL_BACK: _rule(
        _S.INFO, (0,), None, (_C.DERIVE,),
        "The whole-design search was refused; this plan places each chain on its own (details.kernel_refusal)."),
    _F.INTERFACE_MISMATCH: _rule(
        _S.ERROR, (2,), ANY_INTERFACE, (_C.PLAN_INTERFACES,),
        "These two parts do not fit each other at their interface (details.rule, expected, found).", ("rule",)),
    _F.INSERTION_OUT_OF_RANGE: _rule(
        _S.ERROR, (2,), _L.POST_IN_HOLDER, (_C.PLAN_INTERFACES,),
        "The post's insertion depth in this holder is outside the holder's insertion range."),
    _F.HEIGHT_CLOSURE_MISMATCH: _rule(
        _S.ERROR, (2,), None, (_C.PLAN_INTERFACES,),
        "Holder height, post length, mount centre height and insertion do not add up to this optic's axis height."),
    _F.RANGE_EXCEEDED: _rule(
        _S.ERROR, (1,), None, (_C.PLAN_LAYOUT,),
        "A move left this optic's hardware outside its declared adjustment range without re-fixing it.", ("range_id",)),
    _F.FIXATION_HOLE_INVALID: _rule(
        _S.ERROR, (1, 2), _L.HOLDER_ON_BOARD, (_C.PLAN_LAYOUT,),
        "This holder's fixing hole is off the board grid, outside its slot, or shared with another holder."),
    _F.IDENTITY_NOT_IN_CATALOG: _rule(
        _S.ERROR, (1,), None, (_C.PLAN_INTERFACES,),
        "The bound catalogs do not contain this item's identity."),
    _F.FASTENING_VARIES: _rule(
        _S.INFO, (1, 2), _L.MOUNT_ON_POST, (_C.PLAN_INTERFACES,),
        "The mount allows several fastenings to the post; the one used is chosen at assembly."),
    _F.PLAN_PROJECT_MISMATCH: _rule(
        _S.ERROR, (0, 1), None, (_C.PLAN_OPTICS,),
        "The plan and its Project disagree on a component (its pose, or a component one has and the other does not).",
        ("instance_id",)),
    _F.OPTICS_RESULTS_STALE: _rule(
        _S.WARNING, (0, 1, 2), None, (_C.PLAN_OPTICS,),
        "The optical results bound to this plan are not current for its Project; run the optics again."),
    _F.OPENSCI_SUPPLIED_UNMOUNTED: _rule(
        _S.INFO, (1,), None, (_C.PLAN_SUPPLY,),
        "OpenSci supplies this optic without a mount; you mount it at its design position."),
    _F.UNRESOLVED_SUPPLY: _rule(
        _S.WARNING, (1,), None, (_C.PLAN_SUPPLY,),
        "Nobody supplies this item yet: it is not orderable or no compatible hardware was found."),
    _F.CATALOG_BINDING_CHANGED: _rule(
        _S.WARNING, (0,), None, (_C.PLAN_INTERFACES,),
        "A catalog bound to this plan is not the one supplied now; rebind the catalogs to check against it.", ("catalog",)),
}

_ISSUE_SEVERITY: dict[LocalValidationStatusV1, FindingSeverityV1] = {
    LocalValidationStatusV1.FAIL: _S.ERROR,
    LocalValidationStatusV1.UNKNOWN: _S.UNKNOWN,
    LocalValidationStatusV1.OUT_OF_MODEL_SCOPE: _S.UNKNOWN,
    LocalValidationStatusV1.WARNING: _S.WARNING,
}
#: Item counts of a layout issue subject: the design-component subjects name the component's item too.
_SUBJECT_ITEM_COUNTS: dict[IssueSubjectV1, tuple[int, ...]] = {
    subject: tuple(n + (subject in (IssueSubjectV1.DESIGN_COMPONENT, IssueSubjectV1.PART_AND_DESIGN_COMPONENT)) for n in counts)
    for subject, counts in SUBJECT_PART_COUNTS.items()
}


def _layout_rule(code: AssemblyIssueCodeV1) -> FindingRule:
    issue = ISSUE_RULES[code]
    checks = {_C.LAYOUT_VALIDATION}
    if code is AssemblyIssueCodeV1.RESERVED_SPACE_INTRUSION:
        checks.add(_C.PLAN_LAYOUT)  # also the plan-level check against declared envelopes
    return _rule(_ISSUE_SEVERITY.get(issue.status_effect, _S.INFO), _SUBJECT_ITEM_COUNTS[issue.subject], ANY_INTERFACE, checks,
                 issue.message)


#: The rule of every finding code (plan codes, then the layout-validation codes).
FINDING_RULES: dict[FindingCode, FindingRule] = {**_PLAN_RULES, **{code: _layout_rule(code) for code in AssemblyIssueCodeV1}}

#: Kernel refusal (``AssemblyResolutionRefusal.code`` or ``details.reason``) -> plan finding code. Any other refusal maps
#: to DERIVE_FELL_BACK only.
KERNEL_REFUSAL_CODES: dict[str, PlanFindingCodeV1] = {
    "UNSUPPORTED_OPTIC_ORIENTATION": _F.UNSUPPORTED_OPTIC_ORIENTATION,
    "NO_COMPATIBLE_MOUNT": _F.MOUNT_NOT_FOUND,
    "NO_COMMON_BEAM_HEIGHT": _F.NO_COMMON_BEAM_HEIGHT,
    "BEAM_HEIGHT_NOT_REACHABLE": _F.HEIGHT_NOT_REACHABLE,
    "NO_POST_AND_HOLDER_FOR_OPTIC": _F.HEIGHT_NOT_REACHABLE,
    "REQUESTED_BEAM_HEIGHT_UNREACHABLE": _F.HEIGHT_NOT_REACHABLE,
    "SUPPORTS_COLLIDE": _F.SUPPORTS_COLLIDE,
    "HARDWARE_BLOCKS_BEAM": _F.BEAM_CLEARANCE,
    "NO_FIXING_HOLE": _F.NO_FIXING_HOLE,
    "LAYOUT_OUTSIDE_BOARD": _F.BOARD_TOO_SMALL,
    "BREADBOARD_NO_STANDARD_FIT": _F.BOARD_TOO_SMALL,
    "BREADBOARD_CONSTRAINT_UNSATISFIABLE": _F.BOARD_TOO_SMALL,
    "MOUNT_CLIPS_OBLIQUE_BEAM": _F.BEAM_CLIPPED_AT_INCIDENCE,
    "OPTIC_TILT_NOT_SUPPORTED": _F.OPTIC_TILT_NOT_SUPPORTED,
    "search_budget_exhausted": _F.SEARCH_BUDGET_EXHAUSTED,
    "MECHANICAL_RESOLUTION_BOUND_EXCEEDED": _F.SEARCH_BUDGET_EXHAUSTED,
}

del _F, _S, _C, _L


def kernel_refusal_code(code: str, reason: str | None = None) -> PlanFindingCodeV1:
    """The finding code of a kernel refusal (``reason`` first, then ``code``; DERIVE_FELL_BACK when neither is mapped)."""

    for key in (reason, code):
        if key is not None and key in KERNEL_REFUSAL_CODES:
            return KERNEL_REFUSAL_CODES[key]
    return PlanFindingCodeV1.DERIVE_FELL_BACK


def severity_for(code: FindingCode, details: Mapping[str, Any] | None = None) -> FindingSeverityV1:
    rule = FINDING_RULES[code]
    if code is PlanFindingCodeV1.INTERFACE_MISMATCH and (details or {}).get("rule") == UNKNOWN_INTERFACE_RULE:
        return FindingSeverityV1.UNKNOWN
    return rule.severity


def finding_id(code: FindingCode, item_ids: Iterable[str], interface: ChainLinkKindV1 | None,
               details: Mapping[str, Any] | None = None) -> str:
    """The stable id of a finding: ``"f-" + osc1_digest({code, item_ids, interface, details_key})[:12]``."""

    rule = FINDING_RULES[code]
    details = details or {}
    details_key = normalize_numbers({k: details[k] for k in rule.id_keys if k in details})
    digest = osc1_digest({"code": code.value, "item_ids": sorted(item_ids),
                          "interface": None if interface is None else interface.value, "details_key": details_key})
    return "f-" + digest[:FINDING_ID_DIGITS]


def make_finding(code: FindingCode, item_ids: Iterable[str] = (), *, check: FindingCheckV1, source: FindingSourceV1,
                 interface: ChainLinkKindV1 | None = None, basis: FindingBasisV1 = FindingBasisV1.FACT,
                 missing_data: Iterable[str] = (), details: Mapping[str, Any] | None = None) -> FindingV1:
    """A finding by its rule (severity, message and id filled in); ``ValueError`` when it breaks the rule."""

    rule = FINDING_RULES[code]
    items = tuple(sorted(set(item_ids)))
    if interface is None and isinstance(rule.interface, ChainLinkKindV1):
        interface = rule.interface
    payload = normalize_numbers(dict(details or {}))
    finding = FindingV1(finding_id=finding_id(code, items, interface, payload), code=code, severity=severity_for(code, payload),
                        item_ids=items, interface=interface, check=check, basis=basis, missing_data=tuple(sorted(set(missing_data))),
                        message=rule.message, details=payload, source=source)
    problems = finding_problems(finding)
    if problems:
        raise ValueError(f"finding {code.value}: " + "; ".join(problems))
    return finding


def finding_problems(finding: FindingV1) -> list[str]:
    """How ``finding`` breaks the rule of its code (empty when it follows it)."""

    rule = FINDING_RULES[finding.code]
    problems: list[str] = []
    if finding.severity is not severity_for(finding.code, finding.details):
        problems.append(f"severity is {severity_for(finding.code, finding.details).value}")
    if len(finding.item_ids) not in rule.item_counts:
        problems.append(f"names {' or '.join(str(n) for n in rule.item_counts)} item(s)")
    if rule.interface != ANY_INTERFACE and finding.interface is not rule.interface:
        problems.append(f"interface is {rule.interface.value if isinstance(rule.interface, ChainLinkKindV1) else None}")
    if finding.check not in rule.checks:
        problems.append(f"is reported by {' or '.join(sorted(c.value for c in rule.checks))}")
    if (finding.check is FindingCheckV1.DERIVE) != (finding.source is FindingSourceV1.DERIVE):
        problems.append("source DERIVE exactly for check DERIVE")
    if finding.message != rule.message:
        problems.append("message is the code's fixed message")
    if finding.code is PlanFindingCodeV1.INTERFACE_MISMATCH and finding.details.get("rule") not in INTERFACE_RULES:
        problems.append(f"details.rule is one of {', '.join(INTERFACE_RULES)}")
    if finding.code is PlanFindingCodeV1.MISSING_MECHANICAL_DATA and not finding.missing_data:
        problems.append("names the missing data")
    if finding.finding_id != finding_id(finding.code, finding.item_ids, finding.interface, finding.details):
        problems.append("finding_id does not match its code, items, interface and details key")
    return problems


def finding_from_layout_issue(issue: AssemblyIssueV1, *, item_of_part: Mapping[str, str],
                              item_of_instance: Mapping[str, str] | None = None,
                              link_kind_of_mate: Mapping[str, ChainLinkKindV1] | None = None,
                              conservative_items: Iterable[str] = ()) -> FindingV1:
    """The plan finding of one ``validate_assembly_layout`` issue: part refs -> item ids, the design instance -> its item,
    the mate -> its link kind; basis CONSERVATIVE_PROFILE when an involved item rests on a conservative profile,
    NOT_EVALUATED when the issue only says a check was not evaluated, else FACT."""

    items = [item_of_part[ref] for ref in issue.part_refs]
    if issue.design_instance_id is not None:
        items.append((item_of_instance or {})[issue.design_instance_id])
    interface = None if issue.mate_ref is None else (link_kind_of_mate or {}).get(issue.mate_ref)
    effect = ISSUE_RULES[issue.code].status_effect
    if set(items) & set(conservative_items):
        basis = FindingBasisV1.CONSERVATIVE_PROFILE
    elif effect in (LocalValidationStatusV1.UNKNOWN, LocalValidationStatusV1.OUT_OF_MODEL_SCOPE):
        basis = FindingBasisV1.NOT_EVALUATED
    else:
        basis = FindingBasisV1.FACT
    details: dict[str, Any] = {"metrics": [m.model_dump(mode="json") for m in issue.metrics]} if issue.metrics else {}
    return make_finding(issue.code, items, check=FindingCheckV1.LAYOUT_VALIDATION, source=FindingSourceV1.CHECK,
                        interface=interface, basis=basis, details=details)


def sort_findings(findings: Iterable[FindingV1]) -> tuple[FindingV1, ...]:
    return tuple(sorted(findings, key=FindingV1.sort_key))


def merge_findings(*groups: Iterable[FindingV1]) -> tuple[FindingV1, ...]:
    """One sorted finding list from several: each id once; a CHECK finding wins over a DERIVE one of the same id, else
    the first given is kept."""

    kept: dict[str, FindingV1] = {}
    for group in groups:
        for finding in group:
            present = kept.get(finding.finding_id)
            if present is None or (present.source is FindingSourceV1.DERIVE and finding.source is FindingSourceV1.CHECK):
                kept[finding.finding_id] = finding
    return sort_findings(kept.values())


def carry_derive_findings(previous: Iterable[FindingV1], touched_items: Iterable[str],
                          retired_items: Iterable[str] = ()) -> tuple[FindingV1, ...]:
    """The DERIVE findings of the previous revision that the next one keeps: those naming no touched or retired item
    (once an op touches an item, the checks speak for it). Plan-wide DERIVE findings (no items) are kept."""

    dropped = set(touched_items) | set(retired_items)
    return tuple(f for f in previous if f.source is FindingSourceV1.DERIVE and not dropped.intersection(f.item_ids))


def blocking_new_findings(base: Iterable[FindingV1], result: Iterable[FindingV1]) -> tuple[FindingV1, ...]:
    """``require_check``: the ERROR / UNKNOWN findings of ``result`` whose id the base revision does not have."""

    known = {f.finding_id for f in base}
    return tuple(f for f in result if f.severity in (FindingSeverityV1.ERROR, FindingSeverityV1.UNKNOWN) and f.finding_id not in known)


def findings_diff(base: Iterable[FindingV1], result: Iterable[FindingV1]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """``(findings_added, findings_resolved)`` ids of an edit, each sorted."""

    before = {f.finding_id for f in base}
    after = {f.finding_id for f in result}
    return tuple(sorted(after - before)), tuple(sorted(before - after))
