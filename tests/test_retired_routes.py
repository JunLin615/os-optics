"""Commands whose service routes the BOM-only OpenSci service retired (DESIGN_E 3.4, DESIGN_P I5/I6): each refusal
(410 ``ORDERING_BASIS_RETIRED`` / ``ROUTE_RETIRED``, 422 ``ORDER_OPTION_RETIRED``) has the recovery ``USE_LOCAL_PLAN``,
the hint of the local command that replaces it and exit 67; without a token the request is sent anonymously (the service
answers 410 before authentication), and only an earlier service's 401 leads to the token handoff."""

from __future__ import annotations

import json

import pytest

from commerce_stub import StubServer
from os_optics.cli import main
from os_optics.client import api as client_api
from os_optics.client.errors import RETIREMENT_CODES, Recovery, error_from_response
from test_commerce_cli import token_file
from test_commerce_client import AGENT, RESOLUTION_ID, new_state

SET_ID = "00000000-0000-4000-8000-000000000001"
ORDER_ID = "00000000-0000-4000-8000-000000000002"


@pytest.fixture()
def stub():
    with StubServer(new_state(retired=True)) as server:
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
    # (resolve builds its submission from a verified run directory first; here a minimal stand-in: the service retires it)
    monkeypatch.setattr(client_api, "submission_document", lambda run, **kw: {"schema": "opensci.commercial_submission"})
    return home


def _commands(tmp_path) -> list[tuple[list[str], str, str]]:
    """(argv, expected code, a phrase of the local hint)."""

    out = str(tmp_path / "out")
    return [
        (["resolve", str(tmp_path)], "ORDERING_BASIS_RETIRED", "os-optics assembly plan derive PROJECT.json --out PLAN"),
        (["assembly", "preview", RESOLUTION_ID], "ROUTE_RETIRED", "os-optics assembly plan preview PLAN --out preview.zip"),
        (["assembly", "drawing", RESOLUTION_ID, "--out", out], "ROUTE_RETIRED", "os-optics assembly plan draw PLAN --out DIR"),
        (["view", "--resolution", RESOLUTION_ID], "ROUTE_RETIRED", "os-optics view PLAN"),
        (["resolution", "layout", RESOLUTION_ID, "--out", out], "ROUTE_RETIRED", "the local assembly drawing (assembly plan draw)"),
        (["order", "layout", ORDER_ID, "--out", out], "ROUTE_RETIRED", "the local assembly drawing (assembly plan draw)"),
        (["resolution", "mounting-sheet", RESOLUTION_ID], "ROUTE_RETIRED", "os-optics assembly plan show PLAN"),
        (["order", "mounting-sheet", ORDER_ID], "ROUTE_RETIRED", "os-optics assembly plan show PLAN"),
        (["artifacts", "list", RESOLUTION_ID], "ROUTE_RETIRED", "drawings stay on your machine"),
        (["artifacts", "show", SET_ID], "ROUTE_RETIRED", "drawings stay on your machine"),
        (["artifacts", "download", SET_ID, "DRAWING_PDF", "--out", str(tmp_path / "x.pdf")], "ROUTE_RETIRED",
         "drawings stay on your machine"),
    ]


def _run(capsys, argv: list[str]) -> tuple[int, dict]:
    code = main(argv)
    return code, json.loads(capsys.readouterr().out)


@pytest.mark.parametrize("with_token", [True, False], ids=["token", "anonymous"])
def test_every_retired_command_names_its_local_replacement(env, stub, capsys, tmp_path, monkeypatch, with_token):
    if with_token:
        monkeypatch.setenv("OPENSCI_TOKEN_FILE", str(token_file(tmp_path / "agent-token", AGENT)))
    for argv, expected, phrase in _commands(tmp_path):
        before = len(stub.state.requests)
        code, doc = _run(capsys, argv)
        error = doc["error"]
        assert code == 67 and error["code"] == expected and error["recovery"] == "USE_LOCAL_PLAN", argv
        assert phrase in error["hint"] and error["family"] == "GONE" and error["http_status"] == 410, argv
        assert error["details"]["service_recovery"] in ("USE_LOCAL_PLAN", "PURCHASE_BOM"), argv
        sent = stub.state.requests[before:]
        assert sent and all(("Authorization" in r["headers"]) is with_token for r in sent), argv
    assert not (tmp_path / "out").exists() and stub.state.artifact_sets == {}


def test_order_create_of_a_design_resolution_and_derived_mechanics_name_the_bom_path(env, stub, capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENSCI_TOKEN_FILE", str(token_file(tmp_path / "agent-token", AGENT)))
    code, doc = _run(capsys, ["order", "create", RESOLUTION_ID])
    assert code == 67 and doc["error"]["code"] == "ORDERING_BASIS_RETIRED" and doc["error"]["recovery"] == "USE_LOCAL_PLAN"
    assert doc["error"]["hint"].startswith("Design resolutions can no longer be ordered.") and "order quote BOM.json" in doc["error"]["hint"]
    parts = tmp_path / "parts.json"
    parts.write_text(json.dumps({"lines": [{"line_id": "L1", "opensci_sku": "OS-OPT-TEST", "quantity": 1}],
                                 "mechanical_supply": "DERIVED", "mounting": {"L1": {"beam_height_mm": 125}}}), encoding="utf-8")
    code, doc = _run(capsys, ["resolve-parts", str(parts)])
    error = doc["error"]
    assert code == 67 and error["code"] == "ORDER_OPTION_RETIRED" and error["http_status"] == 422
    assert error["recovery"] == "USE_LOCAL_PLAN" and "list the mounts, posts and holders as lines" in error["hint"]
    code, doc = _run(capsys, ["resolve-parts", str(parts), "--mechanics", "none"])  # parts only: still served
    assert code == 0 and doc["resolution_id"]


def test_without_a_token_an_earlier_service_leads_to_the_handoff(env, stub, capsys, tmp_path):
    stub.state.retired = False  # a 0.14-era service: it authenticates before anything else
    code, doc = _run(capsys, ["assembly", "preview", RESOLUTION_ID])
    assert code == 77 and doc["error"]["code"] == "TOKEN_MISSING" and "handoff" in doc["error"]["details"]
    assert all("Authorization" not in r["headers"] for r in stub.state.requests)


def test_retirement_is_mapped_by_code_whatever_the_status():
    for status, code in ((410, "ROUTE_RETIRED"), (410, "ORDERING_BASIS_RETIRED"), (422, "ORDER_OPTION_RETIRED"),
                         (422, "ROUTE_RETIRED")):
        body = json.dumps({"error": {"code": code, "message": "retired", "recovery": "PURCHASE_BOM"}}).encode()
        error = error_from_response(status, body, None)
        assert error.recovery is Recovery.USE_LOCAL_PLAN and error.details["service_recovery"] == "PURCHASE_BOM"
        assert "os-optics order quote BOM.json" in error.hint
    assert RETIREMENT_CODES == {"ROUTE_RETIRED", "ORDERING_BASIS_RETIRED", "ORDER_OPTION_RETIRED"}
    confirm = error_from_response(409, json.dumps({"error": {"code": "BOM_CHANGES_UNCONFIRMED", "message": "x"}}).encode(), None)
    assert confirm.recovery is Recovery.CONFIRM_BOM_CHANGES and "--confirm REVIEW.json" in confirm.hint


@pytest.mark.parametrize("argv", [["resolve"], ["assembly", "preview"], ["assembly", "drawing"], ["resolution", "layout"],
                                  ["order", "mounting-sheet"], ["artifacts", "list"]])
def test_the_help_of_a_retired_command_says_so(capsys, argv):
    with pytest.raises(SystemExit):
        main([*argv, "--help"])
    text = " ".join(capsys.readouterr().out.split())
    assert "Retired by the BOM-only OpenSci service" in text


def test_resolve_parts_help_points_to_order_quote(capsys):
    with pytest.raises(SystemExit):
        main(["resolve-parts", "--help"])
    text = " ".join(capsys.readouterr().out.split())
    assert "ORDER_OPTION_RETIRED" in text and "order quote BOM.json" in text
