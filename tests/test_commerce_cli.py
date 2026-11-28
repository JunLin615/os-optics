"""The commercial ``os-optics`` commands (``os_optics.cli_commerce``) against the local Commercial API stand-in.

JSON on stdout, the documented exit codes, the token never a command-line value and never printed (except the one
``account token create`` without ``--save``), ``--save`` writing an owner-only token file, rotation and logout, and
the local-design commercial flow: resolve -> preview -> order -> report -> drawing refused (75) -> confirmed -> drawing + upload
-> artifacts.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from commerce_stub import AGENT_SCOPES, MANAGE_SCOPE, StubServer, credential_for
from delivery_helpers import sealed_reference_deliveries
from os_optics.cli import main
from os_optics.client import read_token_file
from test_commerce_client import AGENT, MANAGER, RESOLUTION_ID, REVIEW, new_state

DRAWING_FILES = ["assembly_drawing.pdf", "assembly_drawing.svg", "drawing_manifest.json", "drawing_result.json"]


@pytest.fixture(scope="module")
def deliveries():
    return sealed_reference_deliveries()


@pytest.fixture()
def stub(deliveries):
    with StubServer(new_state(deliveries=deliveries)) as server:
        yield server


@pytest.fixture()
def env(stub, tmp_path, monkeypatch):
    """A clean environment: the stub as OPENSCI_API_URL, an empty home directory, no token variables."""

    home = tmp_path / "home"
    home.mkdir()
    for name in ("OPENSCI_TOKEN", "OPENSCI_TOKEN_FILE", "OPENSCI_API_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("OPENSCI_API_URL", stub.url)
    return home


def token_file(path: Path, credential: str) -> Path:
    path.write_text(credential + "\n", encoding="ascii")
    return path


def run_cli(capsys, *argv: str) -> tuple[int, dict, str]:
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, json.loads(captured.out) if captured.out.strip() else {}, captured.out + captured.err


def test_no_command_takes_the_token_as_a_value(env, capsys):
    for option in ("--token", "--token-f", "--api"):  # no abbreviation of --token-file / --api-url is accepted
        with pytest.raises(SystemExit):
            main(["account", "status", option, AGENT])
    code, doc, out = run_cli(capsys, "account", "status", "--token-file", AGENT)  # a token given as the file
    assert code == 77 and doc["error"]["code"] == "TOKEN_FILE_UNREADABLE"
    assert AGENT not in out and AGENT[37:] not in out


def test_configuration_errors(env, capsys, monkeypatch):
    code, doc, _ = run_cli(capsys, "account", "status")
    assert code == 77 and doc["error"]["code"] == "TOKEN_MISSING" and doc["error"]["recovery"] == "PROVIDE_TOKEN"
    monkeypatch.delenv("OPENSCI_API_URL")
    monkeypatch.setenv("OPENSCI_TOKEN", AGENT)
    code, doc, out = run_cli(capsys, "account", "status")
    assert code == 78 and doc["error"]["code"] == "API_URL_MISSING" and AGENT not in out
    code, doc, _ = run_cli(capsys, "account", "status", "--api-url", "http://opensci.example")
    assert code == 78 and doc["error"]["code"] == "API_URL_INVALID"


def test_account_status_never_prints_the_token(env, capsys, tmp_path):
    path = token_file(tmp_path / "agent-token", AGENT)
    code, doc, out = run_cli(capsys, "account", "status", "--token-file", str(path))
    assert code == 0 and doc["token"]["capabilities"]["place_order"] is True
    assert doc["credential"] == {"token_id": doc["token"]["token_id"], "source": "argument file", "path": str(path)}
    assert AGENT not in out and AGENT[37:] not in out
    code, doc, _ = run_cli(capsys, "account", "status", "--token-file", str(token_file(tmp_path / "x", credential_for("nobody"))))
    assert code == 77 and doc["error"]["recovery"] == "REQUEST_NEW_TOKEN" and doc["error"]["http_status"] == 401


def test_token_create_saves_owner_only_or_prints_once(env, capsys, tmp_path, stub):
    manager = token_file(tmp_path / "manager-token", MANAGER)
    code, doc, out = run_cli(capsys, "account", "token", "create", "--token-file", str(manager), "--save", "--label", "agent")
    saved = env / ".opensci" / "token"
    assert code == 0 and doc["saved_to"] == str(saved) and "credential" not in doc
    credential = read_token_file(saved).reveal()
    assert credential not in out and doc["token"]["scopes"] == AGENT_SCOPES and doc["token"]["label"] == "agent"
    code, _, out = run_cli(capsys, "account", "token", "create", "--token-file", str(manager), "--save")
    assert code == 64 and "already exists" in out and read_token_file(saved).reveal() == credential
    code, doc, out = run_cli(capsys, "account", "token", "create", "--token-file", str(manager), "--profile", "review",
                             "--lifecycle", "permanent", "--lifetime-hours", "12")
    assert code == 0 and doc["credential"].startswith("oat_") and out.count(doc["credential"]) == 1 and doc["notice"]
    assert json.loads(stub.state.requests[-1]["body"])["lifetime_hours"] == 12
    code, doc, _ = run_cli(capsys, "account", "status")  # the saved default token file is found
    assert code == 0 and doc["credential"]["source"] == "default token file"
    code, _, out = run_cli(capsys, "account", "token", "create", "--save", str(saved), "--overwrite")
    assert code == 64 and "read from this file" in out
    code, doc, _ = run_cli(capsys, "account", "token", "create", "--scopes", "commercial:resolve")
    assert code == 77 and doc["error"]["recovery"] == "REQUEST_SCOPE"
    code, _, out = run_cli(capsys, "account", "token", "create", "--token-file", str(manager), "--lifetime-hours", "721")
    assert code == 64 and "lifetime_hours" in out
    code, doc, _ = run_cli(capsys, "account", "token", "list", "--token-file", str(manager))
    assert code == 0 and len(doc["tokens"]) >= 4
    code, doc, _ = run_cli(capsys, "account", "token", "revoke", doc["tokens"][-1]["token_id"], "--token-file", str(manager))
    assert code == 0 and doc["status"] == "REVOKED"


def test_token_rotate_and_logout(env, capsys, tmp_path, stub):
    manager = token_file(tmp_path / "manager-token", MANAGER)
    agent_file = token_file(tmp_path / "agent-token", AGENT)
    old_id = stub.state.tokens[AGENT].token_id
    code, doc, out = run_cli(capsys, "account", "token", "rotate", "--token-file", str(agent_file),
                             "--issuer-token-file", str(manager))
    assert code == 0 and doc["status"] == "ROTATED" and doc["saved_to"] == str(agent_file)
    assert doc["previous_token_id"] == old_id and doc["previous_token"]["status"] == "REVOKED"
    new = read_token_file(agent_file).reveal()
    assert new != AGENT and new not in out and stub.state.tokens[new].scopes == AGENT_SCOPES
    code, doc, _ = run_cli(capsys, "account", "token", "rotate", "--token-file", str(manager), "--issuer-token-file", str(manager))
    assert code == 77 and doc["error"]["code"] == "TOKEN_NOT_ROTATABLE" and doc["error"]["recovery"] == "REQUEST_NEW_TOKEN"
    code, doc, _ = run_cli(capsys, "account", "logout", "--token-file", str(agent_file))
    assert code == 0 and doc["token"]["status"] == "REVOKED" and doc["removed_token_file"] == str(agent_file)
    assert not agent_file.exists() and stub.state.tokens[new].status == "REVOKED"


def test_the_commercial_flow_from_the_command_line(env, capsys, tmp_path, stub, monkeypatch):
    from os_optics.local import example_project, run_optical_project

    run_dir = tmp_path / "run"
    run_optical_project(example_project(), output_dir=run_dir)
    monkeypatch.setenv("OPENSCI_TOKEN_FILE", str(token_file(tmp_path / "agent-token", AGENT)))
    outputs = []

    def cli(*argv):
        code, doc, out = run_cli(capsys, *argv)
        outputs.append(out)
        return code, doc

    code, doc = cli("resolve", str(run_dir), "--no-breadboard")
    assert code == 0 and doc["resolution_id"] == RESOLUTION_ID and doc["replayed"] is False
    assert json.loads(stub.state.requests[-1]["body"])["customer_constraints"] == {"include_breadboard": False}
    assert cli("resolve", str(run_dir), "--no-breadboard")[1]["replayed"] is True
    assert cli("resolution", "show", RESOLUTION_ID)[1]["resolution_id"] == RESOLUTION_ID
    assert cli("resolution", "show", RESOLUTION_ID, "--locale", "en")[1]["locale"] == "en"

    validation_out = tmp_path / "validation.json"
    code, doc = cli("assembly", "preview", RESOLUTION_ID, "--out", str(validation_out))
    assert code == 0 and doc["validation"]["status"] in ("PASS", "WARNING") and doc["out"] == str(validation_out)
    assert json.loads(validation_out.read_text(encoding="utf-8"))["result_hash"] == doc["validation"]["result_hash"]
    limited_out = tmp_path / "limited.json"
    code, doc = cli("assembly", "preview", RESOLUTION_ID, "--out", str(limited_out), "--tolerance", "max_total_triangles=100")
    assert code == 0 and doc["validation"]["status"] == "UNKNOWN"  # the caller's work limit, recorded in the result
    assert json.loads(limited_out.read_text(encoding="utf-8"))["tolerances"]["max_total_triangles"] == 100

    code, doc = cli("assembly", "drawing", RESOLUTION_ID, "--out", str(tmp_path / "early"))
    assert code == 75 and doc["error"]["recovery"] == "CREATE_ORDER_FIRST" and not (tmp_path / "early").exists()
    code, doc = cli("account", "address", "list")
    assert code == 0 and doc["addresses"][0]["is_default"] is True
    code, doc = cli("account", "address", "add", "--recipient", "李四", "--phone", "13900000000", "--province", "北京",
                    "--city", "北京市", "--district", "海淀区", "--street", "中关村大街 27 号", "--postal-code", "100080",
                    "--label", "Home", "--default")
    assert code == 0 and doc["replayed"] is False and doc["address"]["is_default"] is True
    assert json.loads(stub.state.requests[-1]["body"])["make_default"] is True
    address_id = doc["address"]["address_id"]
    code, doc = cli("account", "address", "add", "--recipient", "王五", "--phone", "x", "--province", "广东", "--city", "深圳市",
                    "--district", "南山区", "--street", "科技园路 2 号")
    assert code == 67 and doc["error"]["code"] == "SHIPPING_ADDRESS_INVALID" and doc["error"]["recovery"] == "FIX_REQUEST"
    code, doc = cli("order", "create", RESOLUTION_ID, "--address", address_id)
    order_id = doc["order"]["order_id"]
    assert doc["account_handoff"]["path"] == f"/orders/{order_id}"
    assert "https://" not in json.dumps(doc["account_handoff"]) and "Open Account" in doc["account_handoff"]["message"]
    assert doc["account_handoff"]["order_number"] == doc["order"]["order_number"]
    assert "quotation" in doc["account_handoff"]["message"]
    # the optional feedback nudge names this Order; nothing is sent by itself
    assert doc["feedback"]["command"].endswith(f"--order-id {order_id}") and "feedback submit" in doc["feedback"]["command"]
    assert not any(r["path"].endswith("/feedback") for r in stub.state.requests)
    assert code == 0 and doc["replayed"] is False and doc["order"]["payment"]["state"] == "AWAITING_TRANSFER"
    assert doc["order"]["shipping_address"]["recipient_name"] == "李四"
    assert json.loads(stub.state.requests[-1]["body"])["shipping_address_id"] == address_id
    assert cli("order", "show", order_id)[1]["order_number"] == doc["order"]["order_number"]
    assert cli("order", "show", order_id, "--locale", "zh-CN", "--document", "quotation")[1]["document"] == "quotation/presentation"
    assert cli("order", "list")[1]["orders"][0]["order_id"] == order_id
    code, doc = cli("order", "report-transfer", order_id)
    assert code == 0 and doc["order"]["payment"]["state"] == "PENDING_CONFIRMATION"
    code, doc = cli("order", "withdraw", order_id)
    assert code == 67 and doc["error"]["code"] == "ORDER_NOT_WITHDRAWABLE" and doc["error"]["recovery"] == "CONTACT_OPENSCI"
    code, doc = cli("assembly", "drawing", RESOLUTION_ID, "--out", str(tmp_path / "early"))
    assert code == 75 and doc["error"]["recovery"] == "WAIT_FOR_PAYMENT_CONFIRMATION" and doc["error"]["hint"]

    stub.state.confirm_payment()
    out_dir = tmp_path / "drawing"
    code, doc = cli("assembly", "drawing", RESOLUTION_ID, "--out", str(out_dir))
    assert code == 0 and doc["delivery_ready"] is True and sorted(p.name for p in out_dir.iterdir()) == DRAWING_FILES
    # drawings are never uploaded (#557): the summary has no upload block and no artifact set is posted
    assert "upload" not in doc and not any(r["path"].endswith("/artifact-sets") for r in stub.state.requests)
    code, again = cli("assembly", "drawing", RESOLUTION_ID, "--out", str(out_dir))  # regenerated: its own files replaced
    assert code == 0 and again["files"] == doc["files"]
    code, again = cli("assembly", "drawing", RESOLUTION_ID, "--out", str(out_dir), "--overwrite")  # accepted
    assert code == 0 and "upload" not in again
    code, local_only = cli("assembly", "drawing", RESOLUTION_ID, "--out", str(tmp_path / "local"), "--no-upload")
    assert code == 0 and "upload" not in local_only and local_only["delivery_ready"] is True  # (deprecated, no effect)

    code, listed = cli("artifacts", "list", RESOLUTION_ID)
    assert code == 0 and listed["artifact_sets"] == [] and stub.state.artifact_sets == {}
    code, doc = cli("artifacts", "show", "00000000-0000-4000-8000-000000000999")
    assert code == 67 and doc["error"]["code"] == "ARTIFACT_SET_NOT_FOUND"

    grant_keys = [d["grant"]["key"] for d in stub.state.deliveries]
    text = "\n".join(outputs)
    for secret in (AGENT, AGENT[37:], *grant_keys):
        assert secret not in text
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(
        ["agent-token", "drawing", "home", "limited.json", "local", "run", "validation.json"])


def test_a_diagnostic_sheet_of_a_resolution_is_written_and_exits_2(env, capsys, tmp_path, stub, monkeypatch):
    """'assembly drawing' follows the rule of 'assembly draw': the delivery sheet exits 0; a sheet that is not delivery
    ready (the local assembly check did not pass) is written all the same, its summary names it, and the exit code is 2."""
    import dataclasses

    from os_optics.client.api import OpenSciClient
    from os_optics.assembly_drawing import DrawingStatusV1

    monkeypatch.setenv("OPENSCI_TOKEN_FILE", str(token_file(tmp_path / "agent-token", AGENT)))
    assert run_cli(capsys, "order", "create", RESOLUTION_ID)[0] == 0
    stub.state.confirm_payment()
    drawn = OpenSciClient.assembly_drawing

    def diagnostic(self, *args, **kwargs):  # (the sheet of a check that did not pass)
        drawing = drawn(self, *args, **kwargs)
        return dataclasses.replace(drawing, result=drawing.result.model_copy(update={
            "drawing_status": DrawingStatusV1.DIAGNOSTIC_VALIDATION_FAILED, "delivery_ready": False}))

    monkeypatch.setattr(OpenSciClient, "assembly_drawing", diagnostic)
    out_dir = tmp_path / "diagnostic"
    code, doc, _ = run_cli(capsys, "assembly", "drawing", RESOLUTION_ID, "--out", str(out_dir), "--no-upload")
    assert code == 2 and (doc["status"], doc["delivery_ready"]) == ("DIAGNOSTIC_VALIDATION_FAILED", False)
    assert sorted(p.name for p in out_dir.iterdir()) == DRAWING_FILES and "upload" not in doc


@pytest.mark.parametrize("command", [["assembly", "drawing"], ["assembly", "draw"]])
def test_both_drawing_commands_state_the_same_exit_codes_in_their_help(capsys, command):
    """One rule for both drawing commands: 0 = the delivery sheet, 2 = the diagnostic sheet asked for with --diagnostic,
    65 = a check that did not pass without --diagnostic (nothing written)."""
    with pytest.raises(SystemExit):
        main([*command, "--help"])
    text = " ".join(capsys.readouterr().out.split())
    assert "0 = Detailed Assembly Drawing written, 2 = diagnostic sheet written (--diagnostic: local assembly check FAILED " \
           "or incomplete; status and delivery_ready say which sheet)" in text
    assert "local assembly check that did not pass without --diagnostic" in text
    assert text.count("65 = ") == 1 and "64 = " in text and "0 = done" not in text


def test_the_drawing_help_states_that_nothing_is_uploaded_and_the_local_command(capsys):
    with pytest.raises(SystemExit):
        main(["assembly", "drawing", "--help"])
    text = " ".join(capsys.readouterr().out.split())
    assert "nothing is uploaded (drawings stay on this device; --no-upload is accepted and has no effect)" in text
    assert "assembly plan draw PLAN --out DIR" in text and "upload.status" not in text


def test_a_service_that_cannot_be_reached_exits_69(env, capsys, monkeypatch, tmp_path):
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    monkeypatch.setenv("OPENSCI_TOKEN", AGENT)
    monkeypatch.setattr("os_optics.client.transport.RETRY_BACKOFF_SECONDS", (0.0,))
    code, doc, out = run_cli(capsys, "order", "list", "--api-url", f"http://127.0.0.1:{port}")
    assert code == 69 and doc["error"]["code"] == "SERVICE_UNREACHABLE" and AGENT not in out


def test_help_names_every_commercial_command(capsys):
    for argv in (["account", "--help"], ["order", "--help"], ["assembly", "preview", "--help"], ["assembly", "drawing", "--help"],
                 ["artifacts", "--help"], ["resolve", "--help"], ["resolution", "show", "--help"],
                 ["account", "token", "create", "--help"], ["account", "token", "rotate", "--help"],
                 ["feedback", "--help"], ["feedback", "submit", "--help"], ["resolve-parts", "--help"],
                 ["products", "mechanical", "--help"], ["resolution", "mounting-sheet", "--help"],
                 ["order", "mounting-sheet", "--help"]):
        with pytest.raises(SystemExit) as done:
            main(argv)
        assert done.value.code == 0
    text = capsys.readouterr().out
    assert "--token-file" in text and "OPENSCI_API_URL" in text and "report-transfer" in text
    assert MANAGE_SCOPE in text


def _commerce_help_texts() -> str:
    import contextlib
    import io

    import os_optics.cli_commerce as cli_commerce
    import os_optics.client as client
    from os_optics.client import api, credentials, errors, transport

    from os_optics import mounting_sheet

    texts = [cli_commerce.__doc__, client.__doc__, api.__doc__, credentials.__doc__, errors.__doc__, transport.__doc__,
             mounting_sheet.__doc__, *errors.RECOVERY_HINTS.values(), *errors.CODE_HINTS.values()]
    for module in (api, credentials, errors, transport):
        for value in vars(module).values():
            if getattr(value, "__module__", None) == module.__name__ and getattr(value, "__doc__", None):
                texts.append(value.__doc__)
                texts += [m.__doc__ for m in vars(value).values() if callable(m) and getattr(m, "__doc__", None)]
    for argv in (["account", "token", "create"], ["account", "token", "rotate"], ["account", "logout"], ["resolve"],
                 ["resolution", "show"], ["order", "create"], ["order", "report-transfer"], ["order", "withdraw"],
                 ["assembly", "preview"],
                 ["assembly", "drawing"], ["artifacts", "download"], ["feedback"], ["feedback", "submit"],
                 ["resolve-parts"], ["products"], ["products", "mechanical"], ["resolution", "mounting-sheet"],
                 ["order", "mounting-sheet"]):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), pytest.raises(SystemExit):
            main([*argv, "--help"])
        texts.append(buffer.getvalue())
    return "\n".join(t for t in texts if t)


@pytest.mark.parametrize("phrase", [
    "confirm with the user", "before creating the order", "ask the user", "tell me before", "human confirmation",
    "approval", "resolver", "supplier", "internal", "not guaranteed", "drm", "engineering review", "not a manufacturing",
    "in this version", "proxy geometry",
])
def test_commercial_help_and_docstrings_state_the_product_only(phrase):
    """The commercial helper texts never ask for a second confirmation before the Order and never describe
    implementation internals (customer-visible: CLI help and the public client docstrings)."""

    assert phrase not in _commerce_help_texts().lower()


def test_the_assembly_of_a_resolution_is_viewed_from_memory(env, capsys, tmp_path, stub, monkeypatch):
    monkeypatch.setenv("OPENSCI_TOKEN_FILE", str(token_file(tmp_path / "agent-token", AGENT)))
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    shots = tmp_path / "shots"
    code, doc, out = run_cli(capsys, "view", "--resolution", RESOLUTION_ID, "--snapshot", str(shots), "--views", "iso,top")
    assert code == 0 and doc["view_kind"] and (shots / "iso.png").is_file() and (shots / "scene_summary.json").is_file()
    code, doc, out = run_cli(capsys, "assembly", "preview", RESOLUTION_ID, "--snapshot", str(tmp_path / "shots2"))
    assert code == 0 and set(doc["snapshots"]) >= {"iso", "summary"} and all(isinstance(v, str) for v in doc["snapshots"].values())
    assert list(work.iterdir()) == []  # nothing of the protected package was written
    code, _, _ = run_cli(capsys, "view", "--resolution", RESOLUTION_ID, "--write-html", str(tmp_path / "a.html"))
    assert code == 64 and not (tmp_path / "a.html").exists()
    assert AGENT not in out


def test_json_output_to_a_pipe_is_utf8():
    import subprocess
    import sys

    done = subprocess.run([sys.executable, "-m", "os_optics", "catalog", "list", "--limit", "1"], capture_output=True,
                          env={**__import__("os").environ, "PYTHONIOENCODING": ""}, check=False)
    assert done.returncode == 0
    # JSON output is ASCII (non-ASCII as \\u escapes): any reader decodes it, whatever its default encoding or code page
    text = done.stdout.decode("ascii")
    names = [p["display_name_zh_cn"] for p in __import__("json").loads(text)["products"]]
    assert names and any("一" <= ch <= "鿿" for name in names for ch in name)


def test_resolution_show_has_its_own_help(capsys):
    with pytest.raises(SystemExit):
        main(["resolution", "show", "--help"])
    assert "Read a commercial resolution" in capsys.readouterr().out


def test_the_help_says_where_the_beam_height_is_reported(capsys):
    """The beam height is not an option, and the help says where to read it -- the resolution's frame and
    customer-supplied components, and the localized resolution / Order."""

    for argv in (["resolve", "--help"], ["resolution", "show", "--help"]):
        with pytest.raises(SystemExit):
            main(argv)
    text = " ".join(capsys.readouterr().out.split())
    assert "frame.optic_axis_heights" in text and "customer_supplied_components[].axis_height_mm" in text
    assert "above the breadboard top (above your own table top with --no-breadboard)" in text
    assert "'resolution show ID --locale zh-CN|en'" in text and "assembly.optic_axis_heights" in text


def test_resolve_takes_the_purchase_options_and_no_beam_height(env, capsys, tmp_path, stub, monkeypatch):
    """``--no-breadboard`` (your own optical table) or ``--assembly-service`` (OpenSci assembles it), never
    both; the beam height is no option any more; without an option no constraints are sent."""

    from os_optics.local import example_project, run_optical_project

    run_dir = tmp_path / "run"
    run_optical_project(example_project(), output_dir=run_dir)
    monkeypatch.setenv("OPENSCI_TOKEN_FILE", str(token_file(tmp_path / "agent-token", AGENT)))
    assert run_cli(capsys, "resolve", str(run_dir))[0] == 0
    assert "customer_constraints" not in json.loads(stub.state.requests[-1]["body"])
    assert run_cli(capsys, "resolve", str(run_dir), "--assembly-service")[0] == 0
    assert json.loads(stub.state.requests[-1]["body"])["customer_constraints"] == {"assembly_service": True}
    for argv in (("--no-breadboard", "--assembly-service"), ("--beam-height-mm", "100")):
        with pytest.raises(SystemExit) as refused:
            main(["resolve", str(run_dir), *argv])
        assert refused.value.code == 64
    help_text = capsys.readouterr().err
    assert "not allowed with argument" in help_text and "--beam-height-mm" in help_text


def test_resolve_marks_components_the_customer_supplies(env, capsys, tmp_path, stub, monkeypatch):
    """``--customer-supplied INSTANCE_ID`` (repeatable) sends the sorted, de-duplicated instance ids as
    ``customer_constraints.customer_supplied_components``; the help says what it means."""

    from os_optics.local import example_project, run_optical_project

    run_dir = tmp_path / "run"
    run_optical_project(example_project(), output_dir=run_dir)
    monkeypatch.setenv("OPENSCI_TOKEN_FILE", str(token_file(tmp_path / "agent-token", AGENT)))
    assert run_cli(capsys, "resolve", str(run_dir), "--customer-supplied", "L1", "--customer-supplied", "L1")[0] == 0
    assert json.loads(stub.state.requests[-1]["body"])["customer_constraints"] == {"customer_supplied_components": ["L1"]}
    with pytest.raises(SystemExit):
        main(["resolve", "--help"])
    help_text = capsys.readouterr().out
    assert "--customer-supplied" in help_text and "customer_supplied_components" in help_text


def test_an_unpaid_order_is_withdrawn_from_the_command_line(env, capsys, stub, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENSCI_TOKEN_FILE", str(token_file(tmp_path / "agent-token", AGENT)))

    def cli(*argv: str) -> tuple[int, dict]:
        code, doc, _ = run_cli(capsys, *argv)
        return code, doc

    code, doc = cli("order", "create", RESOLUTION_ID)
    order_id = doc["order"]["order_id"]
    code, doc = cli("order", "withdraw", order_id)
    assert code == 0 and doc["order_status"] == "WITHDRAWN" and doc["resolution_deleted"] is True
    code, doc = cli("order", "withdraw", order_id)
    assert code == 67 and doc["error"]["code"] == "ORDER_NOT_FOUND" and doc["error"]["http_status"] == 404


def test_feedback_submit_sends_one_item_with_its_context(env, capsys, stub, tmp_path, monkeypatch):
    from catalog_store_helpers import FIXTURE_CATALOG, catalog_version_of

    from os_optics import __version__

    review = token_file(tmp_path / "review-token", REVIEW)  # any Account token may send feedback
    details = tmp_path / "details.md"
    details.write_text("用户需要订购。I ran 'opensci-engine order create RID'.\nIt was refused.\n", encoding="utf-8")
    monkeypatch.setenv("OPENSCI_AGENT_NAME", "Lab Copilot")
    argv = ["feedback", "submit", "--token-file", str(review), "--category", "PROBLEM", "--summary",
            "order create refused after resolve", "--details-file", str(details), "--error-code",
            "SHIPPING_ADDRESS_REQUIRED", "--request-id", "req-0001", "req-0002", "--request-id", "req-0003",
            "--resolution-id", RESOLUTION_ID, "--command", "opensci-engine order create RID", "--locale", "zh-CN"]
    code, doc, out = run_cli(capsys, *argv)
    assert code == 0 and doc["status"] == "RECEIVED" and doc["replayed"] is False and doc["feedback_id"]
    assert REVIEW not in out
    body = json.loads(stub.state.requests[-1]["body"])
    assert body["details"] == "用户需要订购。I ran 'opensci-engine order create RID'.\nIt was refused."
    assert body["context"] == {"engine_version": __version__, "runtime": body["context"]["runtime"],
                               "agent": "Lab Copilot", "catalog_version": catalog_version_of(FIXTURE_CATALOG),
                               "error_code": "SHIPPING_ADDRESS_REQUIRED", "request_ids": ["req-0001", "req-0002", "req-0003"],
                               "resolution_id": RESOLUTION_ID, "command": "opensci-engine order create RID",
                               "locale": "zh-CN"}
    code, again, _ = run_cli(capsys, *argv)  # the same feedback again: the same receipt
    assert code == 0 and again == {**doc, "replayed": True} and len(stub.state.feedback) == 1
    code, doc, _ = run_cli(capsys, "feedback", "submit", "--token-file", str(review), "--category", "DOCUMENTATION",
                           "--summary", "README lacks the feedback limits", "--details", "x", "--agent", "Named Agent")
    assert code == 0 and json.loads(stub.state.requests[-1]["body"])["context"]["agent"] == "Named Agent"


def test_feedback_submit_reads_stdin_and_omits_an_absent_catalog(env, capsys, stub, tmp_path, monkeypatch,
                                                                  empty_catalog_store):
    import io

    monkeypatch.setenv("OPENSCI_TOKEN_FILE", str(token_file(tmp_path / "agent-token", AGENT)))
    monkeypatch.setattr("sys.stdin", io.StringIO("details from standard input\n"))
    code, doc, _ = run_cli(capsys, "feedback", "submit", "--category", "FEATURE_REQUEST", "--summary",
                           "an English quotation", "--details-file", "-")
    body = json.loads(stub.state.requests[-1]["body"])
    assert code == 0 and body["details"] == "details from standard input"
    assert set(body["context"]) == {"engine_version", "runtime"}  # no active catalog release, no agent name


def test_feedback_submit_refusals(env, capsys, stub, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENSCI_TOKEN_FILE", str(token_file(tmp_path / "agent-token", AGENT)))
    base = ["feedback", "submit", "--category", "PROBLEM", "--summary", "drawing refused twice"]
    # checked here, nothing sent: usage error 64 with the reason on stderr (a token is never repeated)
    for extra, reason in ((["--summary", "abc", "--details", "x"], "summary must be 5 to 200"),
                          (["--details", "x" * 8001], "details must be 1 to 8000"),
                          (["--details", f"token {AGENT}"], "contains an OpenSci Account token"),
                          (["--details", "x", "--order-id", "42"], "context.order_id must be a UUID")):
        code, doc, out = run_cli(capsys, *base, *extra)
        assert (code, doc) == (64, {}) and reason in out and AGENT not in out and AGENT[37:] not in out
    (tmp_path / "latin1.txt").write_bytes("caf\xe9".encode("latin-1"))
    code, _, out = run_cli(capsys, *base, "--details-file", str(tmp_path / "latin1.txt"))
    assert code == 64 and "not UTF-8" in out
    code, _, out = run_cli(capsys, *base, "--details-file", str(tmp_path / "missing.txt"))
    assert code == 64 and "cannot read" in out
    for argv in ((*base,), (*base, "--details", "x", "--details-file", "f"), (*base[:2], "--category", "BUG", "--details", "x")):
        with pytest.raises(SystemExit) as usage:
            main(list(argv))
        assert usage.value.code == 64
    capsys.readouterr()
    assert stub.state.requests == []
    # refused by the service: 67 with recovery FIX_REQUEST
    code, doc, _ = run_cli(capsys, *base, "--details", "the staff page showed oss_abcdefghijklmnopqrstuvwx")
    assert code == 67 and doc["error"]["code"] == "FEEDBACK_INVALID" and doc["error"]["recovery"] == "FIX_REQUEST"
    assert doc["error"]["details"] == {"reason": "CONTAINS_CREDENTIAL", "invalid_fields": ["details"]}
    # rate limited: 69 (send it again later) with recovery RETRY_LATER and the advised wait
    stub.state.feedback_rate_limited = True
    code, doc, _ = run_cli(capsys, *base, "--details", "x")
    error = doc["error"]
    assert code == 69 and (error["code"], error["recovery"], error["http_status"]) == ("FEEDBACK_RATE_LIMITED", "RETRY_LATER", 429)
    assert error["retry_after_seconds"] == 1800 and "Retry-After" in error["hint"]
    # no token at all: 77
    monkeypatch.delenv("OPENSCI_TOKEN_FILE")
    code, doc, _ = run_cli(capsys, *base, "--details", "x")
    assert code == 77 and doc["error"]["recovery"] == "PROVIDE_TOKEN"


def test_feedback_help_names_the_rules():
    import contextlib
    import io

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), pytest.raises(SystemExit) as done:
        main(["feedback", "submit", "--help"])
    text = " ".join(buffer.getvalue().split())
    assert done.value.code == 0
    for phrase in ("OpenSci Account token (Agent or Review profile)", "One problem or suggestion per submission",
                   "Include no tokens, passwords", "Tell the user what you sent", "--details-file", "RETRY_LATER",
                   "OPENSCI_AGENT_NAME"):
        assert phrase in text
    from os_optics.cli_commerce import FEEDBACK_CATEGORY_CHOICES
    from os_optics.client import FEEDBACK_CATEGORIES

    assert FEEDBACK_CATEGORY_CHOICES == FEEDBACK_CATEGORIES


def test_resolve_takes_the_purchase_scope_and_spares_and_order_create_an_additional_order(env, capsys, tmp_path, stub,
                                                                                          monkeypatch):
    """``--optics-only`` sends ``purchase_scope: OPTICS_ONLY`` (exclusive with the breadboard
    and service options), ``--spare SKU=N`` (repeatable) the spare units, ``order create --additional-order`` the
    explicit additional Order."""

    from os_optics.local import example_project, run_optical_project

    run_dir = tmp_path / "run"
    run_optical_project(example_project(), output_dir=run_dir)
    monkeypatch.setenv("OPENSCI_TOKEN_FILE", str(token_file(tmp_path / "agent-token", AGENT)))
    assert run_cli(capsys, "resolve", str(run_dir), "--optics-only", "--spare", "OS-OPT-B=2", "--spare", "OS-OPT-A=1")[0] == 0
    assert json.loads(stub.state.requests[-1]["body"])["customer_constraints"] == {
        "purchase_scope": "OPTICS_ONLY", "spare_units": {"OS-OPT-A": 1, "OS-OPT-B": 2}}
    for argv in (("--optics-only", "--no-breadboard"), ("--optics-only", "--assembly-service"), ("--spare", "X=0"),
                 ("--spare", "X")):
        with pytest.raises(SystemExit) as refused:
            main(["resolve", str(run_dir), *argv])
        assert refused.value.code == 64
    capsys.readouterr()
    assert run_cli(capsys, "order", "create", RESOLUTION_ID)[0] == 0
    assert run_cli(capsys, "order", "create", RESOLUTION_ID, "--additional-order")[0] == 0
    assert json.loads(stub.state.requests[-1]["body"]) == {"resolution_id": RESOLUTION_ID, "additional_order": True}
    text = main_help(capsys, "resolve") + main_help(capsys, "order", "create")
    assert "--optics-only" in text and "Optical Layout Drawing" in text and "--additional-order" in text


def main_help(capsys, *command: str) -> str:
    with pytest.raises(SystemExit):
        main([*command, "--help"])
    return capsys.readouterr().out
