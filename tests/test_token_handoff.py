"""The draft-through-sign-in handoff (#605 check 4, #576 token handoff): a token-needing command without a token sends
nothing, names the Account registration / sign-in pages (``?next=%2Ftokens``), the token file and the command to resume
with; after sign-in the same command sends the same request (the same idempotency key), so it never quotes twice."""

from __future__ import annotations

import json

import pytest

from commerce_stub import StubServer
from os_optics.cli import main
from os_optics.client import OpenSciConfigurationError, canonical_json_bytes, idempotency_key_for, resolve_token
from os_optics.client.bom_review import purchase_bom_body
from os_optics.client.credentials import account_handoff, handoff_text
from os_optics.deployment import default_account_origin
from test_commerce_cli import token_file
from test_commerce_client import AGENT, new_state

BOM = {"schema": "opensci.commercial.part_list_submission", "schema_version": "2",
       "lines": [{"opensci_sku": "OS-OPT-TEST", "quantity": 1}, {"opensci_sku": "OS-MECH-KM-1", "quantity": 1}]}


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
    return home


def test_order_quote_without_a_token_hands_off_and_resumes_with_the_same_request(env, stub, tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("OPENSCI_ACCOUNT_URL", "http://127.0.0.1:18960/account/")
    bom = tmp_path / "bom.json"
    bom.write_text(json.dumps(BOM), encoding="utf-8")
    code = main(["order", "quote", str(bom)])
    captured = capsys.readouterr()
    error = json.loads(captured.out)["error"]
    assert code == 77 and error["code"] == "TOKEN_MISSING" and error["recovery"] == "PROVIDE_TOKEN"
    assert stub.state.requests == []  # nothing was sent
    handoff = error["details"]["handoff"]
    assert handoff == {"register_url": "http://127.0.0.1:18960/account/register?next=%2Ftokens",
                       "login_url": "http://127.0.0.1:18960/account/login?next=%2Ftokens",
                       "token_file": str(env / ".opensci" / "token"), "resume_command": f"os-optics order quote {bom}"}
    text = captured.err.splitlines()
    assert text[0] == f"No OpenSci Account token on this device. Nothing was sent; your BOM stays in {bom}."
    assert text[1].startswith("1. Register:  http://127.0.0.1:18960/account/register?next=%2Ftokens   (or sign in: ")
    assert text[2].startswith("2. Create an Agent token there and save it to ") and "OPENSCI_TOKEN_FILE" in text[2]
    assert text[3] == f"3. Resume with the same command:  os-optics order quote {bom}"
    assert "never creates a second quote" in text[4]
    # after sign-in: the token is saved where the handoff said; the same command sends the same request
    (env / ".opensci").mkdir()
    token_file(env / ".opensci" / "token", AGENT)
    assert main(["order", "quote", str(bom)]) == 0
    first = json.loads(capsys.readouterr().out)
    assert main(["order", "quote", str(bom)]) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["replayed"] is True and second["resolution_id"] == first["resolution_id"]
    keys = {r["headers"]["Idempotency-Key"] for r in stub.state.requests if r["method"] == "POST"}
    assert keys == {idempotency_key_for("part-list-submit", canonical_json_bytes(purchase_bom_body(BOM)))}


def test_the_account_url_comes_from_the_environment_else_the_deployment_file(env, tmp_path):
    default = default_account_origin()
    assert default.startswith("https://") and "/register" not in default
    handoff = account_handoff({}, home=tmp_path)
    assert handoff["register_url"] == default.rstrip("/") + "/register?next=%2Ftokens"
    assert handoff["login_url"] == default.rstrip("/") + "/login?next=%2Ftokens"
    assert handoff["token_file"] == str(tmp_path / ".opensci" / "token") and handoff["resume_command"] is None
    custom = account_handoff({"OPENSCI_ACCOUNT_URL": "https://account.example.test/"})
    assert custom["login_url"] == "https://account.example.test/login?next=%2Ftokens"
    with pytest.raises(OpenSciConfigurationError) as missing:
        resolve_token(None, environ={"OPENSCI_ACCOUNT_URL": "http://localhost:9/account"}, home=tmp_path)
    assert missing.value.details["handoff"]["register_url"] == "http://localhost:9/account/register?next=%2Ftokens"
    assert "the command you ran" in handoff_text(missing.value.details["handoff"])


def test_any_token_needing_command_prints_the_handoff(env, capsys):
    assert main(["account", "status"]) == 77
    captured = capsys.readouterr()
    error = json.loads(captured.out)["error"]
    assert error["code"] == "TOKEN_MISSING" and set(error["details"]["handoff"]) == {
        "register_url", "login_url", "token_file", "resume_command"}
    assert captured.err.startswith("No OpenSci Account token on this device. Nothing was sent.")
    assert "3. Resume with the same command:  the command you ran" in captured.err
