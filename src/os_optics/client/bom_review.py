"""Purchase BOM quotes: the request body, the service's line review, and the confirmation of changed lines.

A purchase BOM (``opensci.commercial.part_list_submission`` version ``2``) is a list of OpenSci part numbers and
quantities -- optics (``OS-OPT-*``), mounts, posts, post holders and breadboards (``OS-MECH-*``) -- that
``os-optics assembly plan bom PLAN --purchase`` writes or a person writes by hand::

    {"schema": "opensci.commercial.part_list_submission", "schema_version": "2",
     "lines": [{"line_id": "L1", "opensci_sku": "OS-OPT-...", "quantity": 2, "purpose": "PLAN"},
               {"line_id": "L2", "opensci_sku": "OS-MECH-...", "quantity": 1, "purpose": "SPARE"}]}

``line_id`` and ``purpose`` (``PLAN``, the default, or ``SPARE``) are optional (the service numbers lines ``L1..Ln``
when no line states its id). OpenSci checks that each line can be supplied now, never whether the parts fit together:
the answer carries a review (``resolution.bom_review``) whose status is ``ORDERABLE`` (order the resolution) or
``CHANGES_REQUIRED`` (some lines cannot be supplied as listed: each line has a status ``OK``, ``NOT_ORDERABLE``,
``TEMPORARILY_UNAVAILABLE`` or ``ABOVE_LIMIT``). A quote whose lines changed is ordered only after the changes are
confirmed: the confirmed BOM is sent again with ``confirmed_changes`` naming the reviewed resolution and one decision per
changed line (``REMOVED``; ``QUANTITY_CHANGED`` with the new quantity, which resolves an ``ABOVE_LIMIT`` line only;
``SUBSTITUTED`` with another part number and quantity). With no line that can be supplied the service answers ``422
BOM_NOT_ORDERABLE`` with the review in ``details.bom_review`` and stores nothing.

The review file (``os-optics.bom_review`` 1.0) keeps what was sent, the service's review verbatim and a proposed
confirmation (the OK lines unchanged, ``REMOVED`` for every changed line); a person or an Agent may edit its decisions
and lines before ``order quote BOM.json --confirm REVIEW.json`` sends it. ``confirmation_body`` checks it first, by the
service's own rule: every decision names a changed line, every changed line has exactly one decision, a quantity change
alone does not resolve a line that cannot be supplied, every line states its id, and the decisions turn the reviewed
lines into exactly the submitted lines (``CONFIRMATION_INCONSISTENT``; nothing is sent).

Version ``1`` (``--schema-version 1``, for a service before BOM-only ordering) is the earlier part-list body: line ids
are filled in (``L1..Ln``) when no line states one, ``mechanical_supply`` is ``NONE``, and a line with a ``purpose``
cannot be sent (spare units are then added to the quantities by the BOM writer).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .errors import LocalInputError, Recovery

PART_LIST_SCHEMA = "opensci.commercial.part_list_submission"
BOM_SCHEMA_VERSIONS = ("1", "2")
#: The top-level fields of a version-2 purchase BOM and the fields of one line.
BOM_FIELDS = ("schema", "schema_version", "lines", "confirmed_changes", "mechanical_supply")
LINE_FIELDS = ("opensci_sku", "quantity", "line_id", "purpose")
PURPOSES = ("PLAN", "SPARE")
DEFAULT_PURPOSE = "PLAN"
MECHANICAL_SUPPLY_NONE = "NONE"
LINE_ID_PREFIX = "L"
REVIEW_SCHEMA = "os-optics.bom_review"
REVIEW_SCHEMA_VERSION = "1.0"
ORDERABLE = "ORDERABLE"
CHANGES_REQUIRED = "CHANGES_REQUIRED"
OK = "OK"
#: Line statuses a quantity change alone does not resolve (the part cannot be supplied at all right now).
UNSUPPLIABLE = ("NOT_ORDERABLE", "TEMPORARILY_UNAVAILABLE")
DECISIONS = ("REMOVED", "QUANTITY_CHANGED", "SUBSTITUTED")
#: Local refusal codes (nothing is sent).
BOM_INVALID = "PART_LIST_INVALID"
CONFIRMATION_INCONSISTENT = "CONFIRMATION_INCONSISTENT"
CONFIRMATION_NOT_POSSIBLE = "CONFIRMATION_NOT_POSSIBLE"


def _invalid(message: str, **details: Any) -> LocalInputError:
    return LocalInputError(BOM_INVALID, message, details=details, recovery=Recovery.FIX_REQUEST)


def _lines(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    lines = document.get("lines")
    if not isinstance(lines, list) or not lines:
        raise _invalid("a purchase BOM has a non-empty list of lines", fields=["lines"])
    checked = []
    for index, line in enumerate(lines):
        if not isinstance(line, Mapping):
            raise _invalid(f"line {index + 1} is not an object", line=index + 1)
        unknown = sorted(set(line) - set(LINE_FIELDS))
        if unknown:
            raise _invalid(f"line {index + 1} has unknown fields: {', '.join(unknown)} (a line has {', '.join(LINE_FIELDS)})",
                           line=index + 1, fields=unknown)
        sku, quantity = line.get("opensci_sku"), line.get("quantity")
        if not isinstance(sku, str) or not sku:
            raise _invalid(f"line {index + 1} needs an opensci_sku", line=index + 1, fields=["opensci_sku"])
        if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 1:
            raise _invalid(f"line {index + 1} needs a whole quantity of at least 1", line=index + 1, fields=["quantity"])
        if "purpose" in line and line["purpose"] not in PURPOSES:
            raise _invalid(f"line {index + 1}: purpose is {' or '.join(PURPOSES)}", line=index + 1, fields=["purpose"])
        if "line_id" in line and (not isinstance(line["line_id"], str) or not line["line_id"]):
            raise _invalid(f"line {index + 1}: line_id is a non-empty text", line=index + 1, fields=["line_id"])
        checked.append(dict(line))
    stated = [line["line_id"] for line in checked if "line_id" in line]
    if stated and len(stated) != len(checked):
        raise _invalid("either every line states its line_id or none does", fields=["lines"])
    if len(set(stated)) != len(stated):
        raise _invalid("line ids are unique", fields=["lines"])
    return checked


def purchase_bom_body(document: Mapping[str, Any], *, schema_version: str = "2") -> dict[str, Any]:
    """The request body of ``POST /api/v1/part-list-submissions`` for a purchase BOM document, in version ``2`` (the
    file as it is, after the local check) or ``1`` (module docstring). Raises ``LocalInputError`` ``PART_LIST_INVALID``
    for a document that cannot be sent (nothing is sent)."""

    if schema_version not in BOM_SCHEMA_VERSIONS:
        raise ValueError(f"schema_version must be one of {', '.join(BOM_SCHEMA_VERSIONS)}")
    if not isinstance(document, Mapping):
        raise _invalid("a purchase BOM is a JSON object", fields=[])
    unknown = sorted(set(document) - set(BOM_FIELDS))
    if unknown:
        raise _invalid(f"unknown purchase BOM fields: {', '.join(unknown)} (a purchase BOM has {', '.join(BOM_FIELDS)}; "
                       "OpenSci no longer selects mounts or assembles: list every part as a line)", fields=unknown)
    if document.get("schema", PART_LIST_SCHEMA) != PART_LIST_SCHEMA:
        raise _invalid(f"schema is {PART_LIST_SCHEMA}", fields=["schema"])
    if document.get("schema_version", "2") not in BOM_SCHEMA_VERSIONS:
        raise _invalid(f"schema_version is one of {', '.join(BOM_SCHEMA_VERSIONS)}", fields=["schema_version"])
    if document.get("mechanical_supply", MECHANICAL_SUPPLY_NONE) != MECHANICAL_SUPPLY_NONE:
        raise _invalid("mechanical_supply can only be NONE: OpenSci no longer selects mounts for a part list; derive the "
                       "chains locally (os-optics assembly plan derive) and list the mounts, posts and holders as lines",
                       fields=["mechanical_supply"])
    lines = _lines(document)
    if schema_version == "2":
        body: dict[str, Any] = {"schema": PART_LIST_SCHEMA, "schema_version": "2", "lines": lines}
        if document.get("confirmed_changes") is not None:
            body["confirmed_changes"] = document["confirmed_changes"]
        if "mechanical_supply" in document:
            body["mechanical_supply"] = MECHANICAL_SUPPLY_NONE
        return body
    if document.get("confirmed_changes") is not None:
        raise _invalid("confirmed changes need schema version 2", fields=["confirmed_changes"])
    spares = [index + 1 for index, line in enumerate(lines) if "purpose" in line]
    if spares:
        raise _invalid("a line with a purpose cannot be sent in schema version 1: write the BOM again with "
                       "'assembly plan bom PLAN --purchase --schema-version 1' (spares merged into the quantities)",
                       fields=["purpose"], lines=spares)
    numbered = [{"line_id": line.get("line_id", f"{LINE_ID_PREFIX}{index + 1}"), "opensci_sku": line["opensci_sku"],
                 "quantity": line["quantity"]} for index, line in enumerate(lines)]
    return {"schema": PART_LIST_SCHEMA, "schema_version": "1", "lines": numbered, "mechanical_supply": MECHANICAL_SUPPLY_NONE}


def review_of(response: Mapping[str, Any]) -> dict[str, Any] | None:
    """The line review of a quote answer (``resolution.bom_review``), or None (an earlier service answers without)."""

    resolution = response.get("resolution") if isinstance(response, Mapping) else None
    review = resolution.get("bom_review") if isinstance(resolution, Mapping) else None
    return dict(review) if isinstance(review, Mapping) else None


def changed_lines(review: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The review lines whose status is not ``OK``, in order."""

    return [dict(line) for line in review.get("lines", ()) if isinstance(line, Mapping) and line.get("status") != OK]


def _line_key(line: Mapping[str, Any]) -> tuple[Any, Any, Any, Any]:
    return line.get("line_id"), line.get("opensci_sku"), line.get("quantity"), line.get("purpose", DEFAULT_PURPOSE)


def review_document(submitted: Mapping[str, Any], review: Mapping[str, Any], resolution_id: str | None) -> dict[str, Any]:
    """The review file (module docstring): with ``resolution_id`` (a persisted CHANGES_REQUIRED quote) a proposed
    confirmation that keeps every OK line and removes every changed one; without it (no line could be supplied) none."""

    document: dict[str, Any] = {"schema": REVIEW_SCHEMA, "schema_version": REVIEW_SCHEMA_VERSION, "resolution_id": resolution_id,
                                "submitted": dict(submitted), "bom_review": dict(review)}
    if resolution_id is not None:
        lines = [{"line_id": line["line_id"], "opensci_sku": line["opensci_sku"], "quantity": line["quantity"],
                  "purpose": line.get("purpose", DEFAULT_PURPOSE)}
                 for line in review.get("lines", ()) if isinstance(line, Mapping) and line.get("status") == OK]
        decisions = [{"line_id": line["line_id"], "decision": "REMOVED"} for line in changed_lines(review)]
        document["proposed_confirmation"] = {"schema": PART_LIST_SCHEMA, "schema_version": "2", "lines": lines,
                                             "confirmed_changes": {"resolution_id": resolution_id, "decisions": decisions}}
    return document


def _apply(review_lines: Sequence[Mapping[str, Any]], decisions: Mapping[str, Mapping[str, Any]]) -> list[tuple]:
    expected = []
    for line in review_lines:
        decision = decisions.get(line.get("line_id"))
        if line.get("status") == OK or decision is None:
            expected.append(_line_key(line))
        elif decision.get("decision") == "QUANTITY_CHANGED":
            expected.append((line.get("line_id"), line.get("opensci_sku"), decision.get("quantity"),
                             line.get("purpose", DEFAULT_PURPOSE)))
        elif decision.get("decision") == "SUBSTITUTED":
            expected.append((line.get("line_id"), decision.get("opensci_sku"), decision.get("quantity"),
                             line.get("purpose", DEFAULT_PURPOSE)))
    return expected


def confirmation_problems(review: Mapping[str, Any], body: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The reasons ``body`` (a version-2 BOM with ``confirmed_changes``) does not confirm ``review`` (the service's
    rule, module docstring), sorted by line id and reason; empty when it does."""

    review_lines = [line for line in review.get("lines", ()) if isinstance(line, Mapping)]
    by_id = {line.get("line_id"): line for line in review_lines}
    changes = body.get("confirmed_changes") if isinstance(body.get("confirmed_changes"), Mapping) else {}
    raw = changes.get("decisions") if isinstance(changes.get("decisions"), list) else []
    problems: list[dict[str, Any]] = []
    decisions: dict[str, Mapping[str, Any]] = {}
    for decision in raw:
        line_id = decision.get("line_id") if isinstance(decision, Mapping) else None
        kind = decision.get("decision") if isinstance(decision, Mapping) else None
        if kind not in DECISIONS:
            problems.append({"line_id": line_id, "reason": "UNKNOWN_DECISION"})
            continue
        if line_id in decisions:
            problems.append({"line_id": line_id, "reason": "DUPLICATE_DECISION"})
            continue
        decisions[line_id] = decision
        line = by_id.get(line_id)
        if line is None:
            problems.append({"line_id": line_id, "reason": "UNKNOWN_LINE"})
        elif line.get("status") == OK:
            problems.append({"line_id": line_id, "reason": "NOT_FLAGGED"})
        elif kind == "QUANTITY_CHANGED" and line.get("status") in UNSUPPLIABLE:
            problems.append({"line_id": line_id, "reason": "DOES_NOT_RESOLVE"})
    for line in review_lines:
        if line.get("status") != OK and line.get("line_id") not in decisions:
            problems.append({"line_id": line.get("line_id"), "reason": "MISSING_DECISION"})
    lines = [line for line in body.get("lines", ()) if isinstance(line, Mapping)]
    if any("line_id" not in line for line in lines):
        problems.append({"line_id": None, "reason": "LINE_IDS_REQUIRED"})
    expected = _apply(review_lines, decisions)
    submitted = [_line_key(line) for line in lines]
    if not problems and submitted != expected:
        first = next((i for i, (a, b) in enumerate(zip(expected, submitted)) if a != b), min(len(expected), len(submitted)))
        problems.append({"line_id": None, "reason": "LINES_DIFFER", "expected_line_ids": [k[0] for k in expected],
                         "submitted_line_ids": [k[0] for k in submitted], "first_difference": first})
    return sorted(problems, key=lambda p: (str(p.get("line_id") or ""), p["reason"]))


def confirmation_body(review_file: Mapping[str, Any]) -> dict[str, Any]:
    """The body ``--confirm REVIEW.json`` sends: the review file's ``proposed_confirmation`` (as edited), checked
    against its review first (module docstring). Raises ``LocalInputError`` ``CONFIRMATION_NOT_POSSIBLE`` (a review
    without a persisted quote, or not a review file) or ``CONFIRMATION_INCONSISTENT`` (``details.problems``)."""

    if not isinstance(review_file, Mapping) or review_file.get("schema") != REVIEW_SCHEMA:
        raise LocalInputError(CONFIRMATION_NOT_POSSIBLE, f"the file is not a review file ({REVIEW_SCHEMA}) written by "
                              "'os-optics order quote'", recovery=Recovery.FIX_REQUEST)
    resolution_id = review_file.get("resolution_id")
    proposed = review_file.get("proposed_confirmation")
    review = review_file.get("bom_review")
    if not resolution_id or not isinstance(proposed, Mapping) or not isinstance(review, Mapping):
        raise LocalInputError(CONFIRMATION_NOT_POSSIBLE, "this review has no quote to confirm: no line of the BOM could be "
                              "supplied, so nothing was stored; change the BOM and quote it again",
                              details={"resolution_id": resolution_id}, recovery=Recovery.FIX_REQUEST)
    body = purchase_bom_body(proposed, schema_version="2")
    changes = body.get("confirmed_changes")
    if not isinstance(changes, Mapping) or changes.get("resolution_id") != resolution_id:
        raise LocalInputError(CONFIRMATION_INCONSISTENT, "the confirmation names another quote than the review",
                              details={"resolution_id": resolution_id,
                                       "problems": [{"line_id": None, "reason": "OTHER_RESOLUTION"}]},
                              recovery=Recovery.FIX_REQUEST)
    problems = confirmation_problems(review, body)
    if problems:
        raise LocalInputError(CONFIRMATION_INCONSISTENT, "the confirmed changes do not turn the reviewed lines into the "
                              "submitted lines (details.problems); nothing was sent",
                              details={"resolution_id": resolution_id, "problems": problems}, recovery=Recovery.FIX_REQUEST)
    return body


__all__ = [
    "CHANGES_REQUIRED",
    "CONFIRMATION_INCONSISTENT",
    "CONFIRMATION_NOT_POSSIBLE",
    "ORDERABLE",
    "REVIEW_SCHEMA",
    "changed_lines",
    "confirmation_body",
    "confirmation_problems",
    "purchase_bom_body",
    "review_document",
    "review_of",
]
