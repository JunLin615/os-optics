"""``os-optics order quote BOM.json [--confirm REVIEW.json]``: purchase BOM v2 with OpenSci's line review, the review
file and its confirmation, the version-1 bridge, the local checks, and ``order create`` of a quote whose changes are not
confirmed -- against the stand-in Commercial API (``commerce_stub``, BOM-only facts of DESIGN_P section 1)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from commerce_stub import StubServer
from os_optics.cli import main
from os_optics.client import OpenSciClient, canonical_json_bytes, idempotency_key_for
from os_optics.client.bom_review import confirmation_problems, purchase_bom_body
from test_commerce_cli import token_file
from test_commerce_client import AGENT, new_state

OPTIC = "OS-OPT-TEST"
MOUNT = "OS-MECH-KM-1"
POST = "OS-MECH-TR-40"
SCHEMA = "opensci.commercial.part_list_submission"


@pytest.fixture()
def stub():
    with StubServer(new_state()) as server:
        yield server


@pytest.fixture()
def env(stub, tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    for name in ("OPENSCI_TOKEN", "OPENSCI_TOKEN_FILE", "OPENSCI_ACCOUNT_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("OPENSCI_API_URL", stub.url)
    monkeypatch.setenv("OPENSCI_TOKEN_FILE", str(token_file(tmp_path / "agent-token", AGENT)))
    return home


def cli(capsys, *argv: str) -> tuple[int, dict]:
    code = main(list(argv))
    out = capsys.readouterr().out
    return code, json.loads(out) if out.strip() else {}


def write_bom(path: Path, lines: list[dict[str, Any]], **extra: Any) -> Path:
    path.write_text(json.dumps({"schema": SCHEMA, "schema_version": "2", "lines": lines, **extra}, indent=1), encoding="utf-8")
    return path


def posts(stub) -> list[dict[str, Any]]:
    return [r for r in stub.state.requests if r["method"] == "POST" and r["path"] == "/api/v1/part-list-submissions"]


TWO_LINES = [{"opensci_sku": OPTIC, "quantity": 2}, {"opensci_sku": MOUNT, "quantity": 2}]


def test_a_two_line_bom_with_part_numbers_and_quantities_is_orderable(env, capsys, stub, tmp_path):
    bom = write_bom(tmp_path / "bom.json", TWO_LINES)
    code, quote = cli(capsys, "order", "quote", str(bom))
    assert code == 0 and quote["status"] == "ORDERABLE" and quote["replayed"] is False and quote["lines"] == 2
    assert quote["next_command"] == f"os-optics order create {quote['resolution_id']}"
    assert [line["status"] for line in quote["bom_review"]["lines"]] == ["OK", "OK"]
    sent = posts(stub)[0]
    body = json.loads(sent["body"])
    assert body == {"schema": SCHEMA, "schema_version": "2", "lines": TWO_LINES}
    assert sent["headers"]["Idempotency-Key"] == idempotency_key_for("part-list-submit", canonical_json_bytes(body))
    code, again = cli(capsys, "order", "quote", str(bom), "--json")  # the same BOM: the same request, a replay
    assert code == 0 and again["replayed"] is True and again["resolution_id"] == quote["resolution_id"]
    assert again["response"]["resolution"]["bom_review"]["status"] == "ORDERABLE"
    assert {r["headers"]["Idempotency-Key"] for r in posts(stub)} == {sent["headers"]["Idempotency-Key"]}
    assert not (tmp_path / "bom.review.json").exists()
    code, placed = cli(capsys, "order", "create", quote["resolution_id"])
    assert code == 0 and placed["order"]["integrity"]["commercial_resolution_id"] == quote["resolution_id"]


def test_changed_lines_are_reviewed_confirmed_and_only_then_ordered(env, capsys, stub, tmp_path):
    """A plan-exported subset: optic, mount, a spare post, and a part number OpenSci does not supply."""

    lines = [{"line_id": "L1", "opensci_sku": OPTIC, "quantity": 1, "purpose": "PLAN"},
             {"line_id": "L2", "opensci_sku": MOUNT, "quantity": 1, "purpose": "PLAN"},
             {"line_id": "L3", "opensci_sku": "OS-MECH-NOT-SUPPLIED", "quantity": 1, "purpose": "PLAN"},
             {"line_id": "L4", "opensci_sku": POST, "quantity": 1, "purpose": "SPARE"}]
    bom = write_bom(tmp_path / "plan-bom.json", lines)
    code, quote = cli(capsys, "order", "quote", str(bom))
    review_path = tmp_path / "plan-bom.review.json"
    assert code == 1 and quote["status"] == "CHANGES_REQUIRED" and quote["review"] == str(review_path)
    assert [(l["line_id"], l["status"]) for l in quote["changed_lines"]] == [("L3", "NOT_ORDERABLE")]
    assert quote["next_command"] == f"os-optics order quote {bom} --confirm {review_path}"
    review = json.loads(review_path.read_text(encoding="utf-8"))
    assert review["schema"] == "os-optics.bom_review" and review["resolution_id"] == quote["resolution_id"]
    assert review["submitted"]["lines"] == lines and review["bom_review"] == quote["bom_review"]
    proposed = review["proposed_confirmation"]
    assert [l["line_id"] for l in proposed["lines"]] == ["L1", "L2", "L4"] and proposed["lines"][2]["purpose"] == "SPARE"
    assert proposed["confirmed_changes"] == {"resolution_id": quote["resolution_id"],
                                             "decisions": [{"line_id": "L3", "decision": "REMOVED"}]}
    # the quote with unconfirmed changes cannot be ordered
    code, refused = cli(capsys, "order", "create", quote["resolution_id"])
    assert code == 67 and refused["error"]["code"] == "BOM_CHANGES_UNCONFIRMED"
    assert refused["error"]["recovery"] == "CONFIRM_BOM_CHANGES" and "--confirm REVIEW.json" in refused["error"]["hint"]
    # the confirmation: its body is the proposed confirmation, as written
    code, confirmed = cli(capsys, "order", "quote", str(bom), "--confirm", str(review_path))
    assert code == 0 and confirmed["status"] == "ORDERABLE" and confirmed["confirmed"] is True
    assert confirmed["resolution_id"] != quote["resolution_id"]
    assert json.loads(posts(stub)[-1]["body"]) == proposed
    assert confirmed["bom_review"]["confirmed_changes"]["reviewed_resolution_id"] == quote["resolution_id"]
    code, placed = cli(capsys, "order", "create", confirmed["resolution_id"])
    assert code == 0 and placed["order"]["integrity"]["commercial_resolution_id"] == confirmed["resolution_id"]


def test_an_edited_confirmation_is_checked_by_the_service_rule_before_sending(env, capsys, stub, tmp_path):
    stub.state.max_line_quantity = 5
    lines = [{"line_id": "L1", "opensci_sku": OPTIC, "quantity": 9}, {"line_id": "L2", "opensci_sku": MOUNT, "quantity": 1},
             {"line_id": "L3", "opensci_sku": POST, "quantity": 1}]
    stub.state.bom_unavailable = {POST}
    bom = write_bom(tmp_path / "bom.json", lines)
    assert cli(capsys, "order", "quote", str(bom))[0] == 1
    review_path = tmp_path / "bom.review.json"
    review = json.loads(review_path.read_text(encoding="utf-8"))
    assert {l["line_id"]: l["status"] for l in review["bom_review"]["lines"]} == {
        "L1": "ABOVE_LIMIT", "L2": "OK", "L3": "TEMPORARILY_UNAVAILABLE"}
    sent = len(posts(stub))

    def edited(name: str, decisions: list[dict], lines_: list[dict]) -> Path:
        document = json.loads(json.dumps(review))
        document["proposed_confirmation"]["confirmed_changes"]["decisions"] = decisions
        document["proposed_confirmation"]["lines"] = lines_
        path = tmp_path / name
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    keep = {"line_id": "L2", "opensci_sku": MOUNT, "quantity": 1, "purpose": "PLAN"}
    fewer = {"line_id": "L1", "opensci_sku": OPTIC, "quantity": 5, "purpose": "PLAN"}
    cases = {
        "DOES_NOT_RESOLVE": ([{"line_id": "L1", "decision": "QUANTITY_CHANGED", "quantity": 5},
                              {"line_id": "L3", "decision": "QUANTITY_CHANGED", "quantity": 1}],
                             [fewer, keep, {"line_id": "L3", "opensci_sku": POST, "quantity": 1, "purpose": "PLAN"}]),
        "MISSING_DECISION": ([{"line_id": "L1", "decision": "REMOVED"}], [keep]),
        "NOT_FLAGGED": ([{"line_id": "L1", "decision": "REMOVED"}, {"line_id": "L2", "decision": "REMOVED"},
                         {"line_id": "L3", "decision": "REMOVED"}], [keep]),
        "LINES_DIFFER": ([{"line_id": "L1", "decision": "REMOVED"}, {"line_id": "L3", "decision": "REMOVED"}],
                         [keep, fewer]),
    }
    for reason, (decisions, lines_) in cases.items():
        code, refused = cli(capsys, "order", "quote", str(bom), "--confirm", str(edited(f"{reason}.json", decisions, lines_)))
        assert code == 65 and refused["error"]["code"] == "CONFIRMATION_INCONSISTENT", reason
        assert reason in [p["reason"] for p in refused["error"]["details"]["problems"]], reason
    assert len(posts(stub)) == sent  # nothing was sent
    # a quantity change resolves an ABOVE_LIMIT line; a substitute replaces the unavailable post
    good = edited("good.json", [{"line_id": "L1", "decision": "QUANTITY_CHANGED", "quantity": 5},
                                {"line_id": "L3", "decision": "SUBSTITUTED", "opensci_sku": "OS-MECH-PH-40", "quantity": 1}],
                  [fewer, keep, {"line_id": "L3", "opensci_sku": "OS-MECH-PH-40", "quantity": 1, "purpose": "PLAN"}])
    code, confirmed = cli(capsys, "order", "quote", str(bom), "--confirm", str(good))
    assert code == 0 and confirmed["status"] == "ORDERABLE"


def test_a_bom_with_no_suppliable_line_writes_its_review_and_cannot_be_confirmed(env, capsys, stub, tmp_path):
    bom = write_bom(tmp_path / "bom.json", [{"opensci_sku": "OS-OPT-UNKNOWN", "quantity": 1}])
    out = tmp_path / "reviews" / "r.json"
    out.parent.mkdir()
    code, refused = cli(capsys, "order", "quote", str(bom), "--review-out", str(out))
    assert code == 67 and refused["error"]["code"] == "BOM_NOT_ORDERABLE" and refused["review"] == str(out)
    assert "No line of this BOM can be supplied now" in refused["error"]["hint"]
    review = json.loads(out.read_text(encoding="utf-8"))
    assert review["resolution_id"] is None and "proposed_confirmation" not in review
    assert review["bom_review"]["lines"][0]["status"] == "NOT_ORDERABLE"
    sent = len(posts(stub))
    code, impossible = cli(capsys, "order", "quote", str(bom), "--confirm", str(out))
    assert code == 65 and impossible["error"]["code"] == "CONFIRMATION_NOT_POSSIBLE" and len(posts(stub)) == sent


def test_service_refusals_of_a_confirmation_carry_their_hint(env, capsys, stub, tmp_path):
    bom = write_bom(tmp_path / "bom.json", [{"line_id": "L1", "opensci_sku": OPTIC, "quantity": 1},
                                            {"line_id": "L2", "opensci_sku": "OS-MECH-NOPE", "quantity": 1}])
    assert cli(capsys, "order", "quote", str(bom))[0] == 1
    review_path = tmp_path / "bom.review.json"
    review = json.loads(review_path.read_text(encoding="utf-8"))
    # the reviewed quote was purged by retention (or belongs to another organization): 404
    stub.state.bom_reviews.clear()
    code, gone = cli(capsys, "order", "quote", str(bom), "--confirm", str(review_path))
    assert code == 67 and gone["error"]["code"] == "RESOLUTION_NOT_FOUND"
    assert f"quote the BOM again (os-optics order quote {bom})" in gone["error"]["hint"]
    # the service's own check refuses (here: the named quote needs no changes, a review file edited by hand)
    code, fresh = cli(capsys, "order", "quote", str(write_bom(tmp_path / "ok.json", [{"opensci_sku": OPTIC, "quantity": 3}])))
    assert code == 0
    review["resolution_id"] = fresh["resolution_id"]
    review["proposed_confirmation"]["confirmed_changes"]["resolution_id"] = fresh["resolution_id"]
    forged = tmp_path / "forged.json"
    forged.write_text(json.dumps(review), encoding="utf-8")
    code, invalid = cli(capsys, "order", "quote", str(bom), "--confirm", str(forged))
    assert code == 67 and invalid["error"]["code"] == "BOM_CONFIRMATION_INVALID"
    assert "run --confirm again with the review file as 'order quote' wrote it" in invalid["error"]["hint"]


def test_the_version_1_bridge_and_the_local_checks(env, capsys, stub, tmp_path):
    bom = write_bom(tmp_path / "bom.json", TWO_LINES)
    code, quote = cli(capsys, "order", "quote", str(bom), "--schema-version", "1")
    assert code == 0 and quote["schema_version"] == "1"
    assert json.loads(posts(stub)[-1]["body"]) == {
        "schema": SCHEMA, "schema_version": "1", "mechanical_supply": "NONE",
        "lines": [{"line_id": "L1", "opensci_sku": OPTIC, "quantity": 2}, {"line_id": "L2", "opensci_sku": MOUNT, "quantity": 2}]}
    sent = len(posts(stub))
    spare = write_bom(tmp_path / "spare.json", [{"opensci_sku": OPTIC, "quantity": 1, "purpose": "SPARE"}])
    for path, extra in ((spare, ("--schema-version", "1")),
                        (write_bom(tmp_path / "m.json", TWO_LINES, mounting={"L1": {}}), ()),
                        (write_bom(tmp_path / "d.json", TWO_LINES, mechanical_supply="DERIVED"), ()),
                        (write_bom(tmp_path / "q.json", [{"opensci_sku": OPTIC, "quantity": 0}]), ()),
                        (write_bom(tmp_path / "x.json", [{"opensci_sku": OPTIC, "quantity": 1, "price": 1}]), ()),
                        (write_bom(tmp_path / "ids.json", [{"line_id": "L1", "opensci_sku": OPTIC, "quantity": 1},
                                                          {"opensci_sku": MOUNT, "quantity": 1}]), ())):
        code, refused = cli(capsys, "order", "quote", str(path), *extra)
        assert code == 65 and refused["error"]["code"] == "PART_LIST_INVALID", path.name
    assert len(posts(stub)) == sent
    assert main(["order", "quote", str(bom), "--confirm", str(bom), "--schema-version", "1"]) == 64


# ---- the released 0.14 client (DESIGN_E 3.4 XR, DESIGN_P I11) ------------------------------------------------------------

PART_LIST_FIELDS_0_14 = ("schema", "schema_version", "lines", "mechanical_supply", "mounting", "optics_source", "platform",
                         "assembly")


def part_list_document_0_14(document: Mapping[str, Any], *, mechanical_supply: str | None = None) -> dict[str, Any]:
    """A verbatim copy of the body rule of os-optics 0.14 ``client/api.py`` ``part_list_document`` (its refusals left
    as assertions)."""

    assert isinstance(document, Mapping) and isinstance(document.get("lines"), list)
    assert not sorted(set(document) - set(PART_LIST_FIELDS_0_14))
    body: dict[str, Any] = {"schema": document.get("schema", SCHEMA), "schema_version": document.get("schema_version", "1"),
                            "lines": list(document["lines"]),
                            "mechanical_supply": mechanical_supply or document.get("mechanical_supply") or "NONE"}
    if document.get("mounting") is not None and mechanical_supply != "NONE":
        body["mounting"] = document["mounting"]
    body.update({name: document[name] for name in ("optics_source", "platform", "assembly") if name in document})
    return body


def test_a_purchase_bom_file_sent_by_the_released_0_14_client_is_a_valid_v2_body(stub, tmp_path):
    bom = {"schema": SCHEMA, "schema_version": "2",
           "lines": [{"line_id": "L1", "opensci_sku": OPTIC, "quantity": 1, "purpose": "PLAN"},
                     {"line_id": "L2", "opensci_sku": POST, "quantity": 1, "purpose": "SPARE"}]}
    released = part_list_document_0_14(bom)
    assert released == {**purchase_bom_body(bom), "mechanical_supply": "NONE"}
    assert purchase_bom_body(released) == released  # the same body passes this client's check unchanged
    client = OpenSciClient(stub.url, AGENT, retries=0, environ={})
    answer = client._post("/part-list-submissions", released,
                          key=idempotency_key_for("part-list-submit", canonical_json_bytes(released))).json()
    assert answer["resolution"]["bom_review"]["status"] == "ORDERABLE"
    # (0.14's key covers the injected mechanical_supply, so it differs from this client's; the BOM is the same)
    assert idempotency_key_for("part-list-submit", canonical_json_bytes(released)) != \
        idempotency_key_for("part-list-submit", canonical_json_bytes(purchase_bom_body(bom)))


def test_confirmation_problems_follow_the_service_rule():
    review = {"lines": [{"line_id": "L1", "opensci_sku": OPTIC, "quantity": 1, "purpose": "PLAN", "status": "OK"},
                        {"line_id": "L2", "opensci_sku": MOUNT, "quantity": 9, "purpose": "PLAN", "status": "ABOVE_LIMIT"}]}

    def body(lines, decisions):
        return {"lines": lines, "confirmed_changes": {"resolution_id": "r", "decisions": decisions}}

    l1 = {"line_id": "L1", "opensci_sku": OPTIC, "quantity": 1, "purpose": "PLAN"}
    assert confirmation_problems(review, body([l1], [{"line_id": "L2", "decision": "REMOVED"}])) == []
    assert confirmation_problems(review, body([l1, {"line_id": "L2", "opensci_sku": MOUNT, "quantity": 5}],
                                              [{"line_id": "L2", "decision": "QUANTITY_CHANGED", "quantity": 5}])) == []
    reasons = [p["reason"] for p in confirmation_problems(review, body([{"opensci_sku": OPTIC, "quantity": 1}],
                                                                        [{"line_id": "L9", "decision": "REMOVED"}]))]
    assert reasons == ["LINE_IDS_REQUIRED", "MISSING_DECISION", "UNKNOWN_LINE"]
