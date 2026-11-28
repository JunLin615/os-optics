"""The draft-through-sign-in handoff (#605 check 4, #576 token handoff): a token-needing command without a token sends
nothing, names the Account registration / sign-in pages (``?next=%2Ftokens``), the token file and the command to resume
with; after sign-in the same command sends the same request (the same idempotency key), so it never quotes twice."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

from commerce_stub import StubServer
from os_optics.cli import main
from os_optics.client import OpenSciConfigurationError, canonical_json_bytes, idempotency_key_for, resolve_token
from os_optics.client.bom_review import purchase_bom_body
from os_optics.client.credentials import account_handoff, handoff_text
from os_optics.cli_commerce import RESUME_EXCLUDED, RESUME_PRESERVED
from os_optics.client import shell_words
from os_optics.client.shell_words import command_line, device_shell, quote_posix, quote_powershell
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
    argv = ["os-optics", "order", "quote", str(bom), "--schema-version", "2"]
    captured = capsys.readouterr()
    error = json.loads(captured.out)["error"]
    assert code == 77 and error["code"] == "TOKEN_MISSING" and error["recovery"] == "PROVIDE_TOKEN"
    assert stub.state.requests == []  # nothing was sent
    handoff = error["details"]["handoff"]
    assert handoff == {"register_url": "http://127.0.0.1:18960/account/register?next=%2Ftokens",
                       "login_url": "http://127.0.0.1:18960/account/login?next=%2Ftokens",
                       "token_file": str(env / ".opensci" / "token"), "resume_command": command_line(argv),
                       "resume_argv": argv, "resume_shell": device_shell()}
    text = captured.err.splitlines()
    assert text[0] == f"No OpenSci Account token on this device. Nothing was sent; your BOM stays in {bom}."
    assert text[1].startswith("1. Register:  http://127.0.0.1:18960/account/register?next=%2Ftokens   (or sign in: ")
    assert text[2].startswith("2. Create an Agent token there and save it to ") and "OPENSCI_TOKEN_FILE" in text[2]
    assert text[3] == f"3. Resume with the same command:  {command_line(argv)}"
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
        "register_url", "login_url", "token_file", "resume_command", "resume_argv", "resume_shell"}
    assert error["details"]["handoff"]["resume_argv"] is None
    assert captured.err.startswith("No OpenSci Account token on this device. Nothing was sent.")
    assert "3. Resume with the same command:  the command you ran" in captured.err


# ---- the command to resume with (DESIGN_R R3) ---------------------------------------------------------------------------

SPECIAL = ["a b", "it's", "$HOME", "`date`", "a;b", "a&b", "\u2018quoted\u2019", "", "d\u00e9j\u00e0 vu/bom.json"]


def test_quote_posix():
    assert [quote_posix(a) for a in SPECIAL] == [
        "'a b'", "'it'\"'\"'s'", "'$HOME'", "'`date`'", "'a;b'", "'a&b'", "'\u2018quoted\u2019'", "''",
        "'d\u00e9j\u00e0 vu/bom.json'"]
    assert quote_posix("/tmp/run-1/bom_v2.json") == "/tmp/run-1/bom_v2.json"
    assert quote_posix("--schema-version") == "--schema-version"
    assert command_line(["os-optics", "order", "quote", "/x y/b.json"], "posix") == "os-optics order quote '/x y/b.json'"


def test_quote_powershell():
    assert [quote_powershell(a) for a in SPECIAL] == [
        "'a b'", "'it''s'", "'$HOME'", "'`date`'", "'a;b'", "'a&b'", "'\u2018\u2018quoted\u2019\u2019'", "''",
        "'d\u00e9j\u00e0 vu/bom.json'"]
    assert quote_powershell("C:\\Users\\a\\bom.json") == "C:\\Users\\a\\bom.json"
    assert quote_powershell("C:\\dir with spaces\\bom.json") == "'C:\\dir with spaces\\bom.json'"
    assert quote_powershell("@args") == "'@args'" and quote_powershell("a,b") == "'a,b'"
    with pytest.raises(ValueError):
        command_line(["x"], "cmd")


def _shell(kind: str) -> list[str]:
    """The command prefix that runs one command line in ``kind`` (``posix``: sh; ``powershell``: pwsh, else Windows
    PowerShell). A shell that is not installed skips the test, but fails it on CI (ubuntu-latest has both)."""

    exe = shutil.which("sh") if kind == "posix" else (shutil.which("pwsh") or shutil.which("powershell"))
    if exe is None:
        if os.environ.get("CI"):
            pytest.fail(f"no {kind} shell on this CI runner")
        pytest.skip(f"no {kind} shell installed")
    return [exe, "-c"] if kind == "posix" else [exe, "-NoProfile", "-NonInteractive", "-Command"]


def _run(kind: str, line: str, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run([*_shell(kind), line], env=env, capture_output=True, text=True, encoding="utf-8",
                          timeout=SHELL_TIMEOUT_S, stdin=subprocess.DEVNULL)


SHELL_TIMEOUT_S = 180


@pytest.mark.parametrize("kind", ["posix", "powershell"])
def test_the_quoted_arguments_reach_a_program_unchanged(kind, tmp_path):
    """Executed: each argument, quoted for the shell, arrives at the program exactly as written."""

    echo = tmp_path / "echo args.py"
    echo.write_text("import json, sys\nsys.stdout.write(json.dumps(sys.argv[1:]))\n", encoding="utf-8")
    arguments = [a for a in SPECIAL if a or kind == "posix"]  # (Windows PowerShell 5.1 drops an empty native argument)
    line = command_line([sys.executable, str(echo), *arguments], kind)
    env = {**os.environ, "MSYS_NO_PATHCONV": "1", "MSYS2_ARG_CONV_EXCL": "*"}
    done = _run(kind, ("& " if kind == "powershell" else "") + line, env)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == arguments


def _quote_parser() -> argparse.ArgumentParser:
    from os_optics import cli_commerce

    sub = argparse.ArgumentParser().add_subparsers()
    cli_commerce.register(sub, assembly=sub.add_parser("assembly").add_subparsers())
    order = sub.choices["order"]
    return next(a for a in order._actions if isinstance(a, argparse._SubParsersAction)).choices["quote"]


def test_resume_preserves_every_quote_option(env, tmp_path, capsys, monkeypatch):
    options = {max(action.option_strings, key=len) for action in _quote_parser()._actions if action.option_strings}
    assert options == set(RESUME_PRESERVED) | set(RESUME_EXCLUDED)  # a new option must be classified
    assert not set(RESUME_PRESERVED) & set(RESUME_EXCLUDED)
    bom = tmp_path / "bom.json"
    bom.write_text(json.dumps(BOM), encoding="utf-8")
    review, out = tmp_path / "r.json", tmp_path / "o.json"
    assert main(["order", "quote", "bom.json", "--review-out", "o.json", "--api-url", "http://127.0.0.1:9", "--json"]) == 64
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "INPUT_FILE_UNREADABLE"  # (relative to another cwd)
    monkeypatch.chdir(tmp_path)  # relative paths become absolute: the command runs from any directory after sign-in
    assert main(["order", "quote", "bom.json", "--review-out", "o.json", "--api-url", "http://127.0.0.1:9", "--json"]) == 77
    handoff = json.loads(capsys.readouterr().out)["error"]["details"]["handoff"]
    assert handoff["resume_argv"] == ["os-optics", "order", "quote", str(bom), "--schema-version", "2", "--review-out",
                                      str(out), "--api-url", "http://127.0.0.1:9", "--json"]
    from os_optics.cli_commerce import _quote_argv

    argv = _quote_argv(str(bom), schema_version="2", confirm=str(review), review_out=str(out), api_url="http://h:1",
                       as_json=True)
    assert argv == ["os-optics", "order", "quote", str(bom), "--schema-version", "2", "--confirm", str(review),
                    "--review-out", str(out), "--api-url", "http://h:1", "--json"]
    assert [a for a in argv if a.startswith("--")] == list(RESUME_PRESERVED)


def _shell_env(home, api_url: str) -> dict[str, str]:
    scripts = sysconfig.get_path("scripts")
    assert any((Path(scripts) / name).is_file() for name in ("os-optics", "os-optics.exe")), scripts
    env = {k: v for k, v in os.environ.items() if k not in ("OPENSCI_TOKEN", "OPENSCI_TOKEN_FILE", "OPENSCI_ACCOUNT_URL")}
    env.update(HOME=str(home), USERPROFILE=str(home), OPENSCI_API_URL=api_url, MSYS_NO_PATHCONV="1",
               MSYS2_ARG_CONV_EXCL="*", PATH=scripts + os.pathsep + env.get("PATH", ""), PYTHONIOENCODING="utf-8")
    return env


def _handoff(capsys, argv: list[str], kind: str, monkeypatch) -> dict:
    monkeypatch.setattr(shell_words, "device_shell", lambda: kind)
    code = main(argv)
    captured = capsys.readouterr()
    error = json.loads(captured.out)["error"]
    assert code == 77 and error["code"] == "TOKEN_MISSING"
    handoff = error["details"]["handoff"]
    assert handoff["resume_shell"] == kind and handoff["resume_command"] == command_line(handoff["resume_argv"], kind)
    assert f"3. Resume with the same command:  {handoff['resume_command']}" in captured.err
    return handoff


@pytest.mark.parametrize("kind", ["posix", "powershell"])
def test_resume_command_executes_v1_with_spaces(kind, env, tmp_path, capsys, monkeypatch):
    _shell(kind)
    folder = tmp_path / "dir with spaces"
    folder.mkdir()
    bom = folder / "the bom.json"
    bom.write_text(json.dumps(BOM), encoding="utf-8")
    with StubServer(new_state()) as a, StubServer(new_state()) as b, StubServer(new_state()) as c:
        monkeypatch.setenv("OPENSCI_API_URL", a.url)
        handoff = _handoff(capsys, ["order", "quote", str(bom), "--schema-version", "1"], kind, monkeypatch)
        assert a.state.requests == []  # nothing was sent
        assert handoff["resume_argv"] == ["os-optics", "order", "quote", str(bom), "--schema-version", "1"]
        Path(handoff["token_file"]).parent.mkdir(parents=True)
        token_file(Path(handoff["token_file"]), AGENT)
        done = _run(kind, handoff["resume_command"], _shell_env(env, b.url))
        assert done.returncode == 0, done.stdout + done.stderr
        assert json.loads(done.stdout)["status"] == "ORDERABLE"
        sent = [r for r in b.state.requests if r["method"] == "POST"]
        assert len(sent) == 1 and json.loads(sent[0]["body"])["schema_version"] == "1"
        # the same request as the command run directly with the token
        monkeypatch.setenv("OPENSCI_API_URL", c.url)
        assert main(["order", "quote", str(bom), "--schema-version", "1"]) == 0
        capsys.readouterr()
        direct = [r for r in c.state.requests if r["method"] == "POST"]
        assert len(direct) == 1 and direct[0]["body"] == sent[0]["body"]
        assert direct[0]["headers"]["Idempotency-Key"] == sent[0]["headers"]["Idempotency-Key"] == idempotency_key_for(
            "part-list-submit", canonical_json_bytes(purchase_bom_body(BOM, schema_version="1")))


@pytest.mark.parametrize("kind", ["posix", "powershell"])
def test_resume_command_executes_confirm_with_review_out_with_spaces(kind, env, tmp_path, capsys, monkeypatch):
    _shell(kind)
    folder = tmp_path / "dir with spaces"
    folder.mkdir()
    bom = folder / "the bom.json"
    bom.write_text(json.dumps({**BOM, "lines": [*BOM["lines"], {"opensci_sku": "OS-MECH-TR-40", "quantity": 1}]}),
                   encoding="utf-8")
    out = folder / "round two's review.json"
    with StubServer(new_state()) as stub:
        monkeypatch.setenv("OPENSCI_API_URL", stub.url)
        stub.state.bom_unavailable = {"OS-MECH-TR-40"}
        prep = token_file(tmp_path / "prep-token", AGENT)
        assert main(["order", "quote", str(bom), "--token-file", str(prep)]) == 1
        review = folder / "the bom.review.json"
        capsys.readouterr()
        proposed = json.loads(review.read_text("utf-8"))["proposed_confirmation"]
        stub.state.requests.clear()
        stub.state.bom_unavailable = {"OS-MECH-KM-1"}  # supply changes: the confirmation gets a round-2 review
        handoff = _handoff(capsys, ["order", "quote", str(bom), "--confirm", str(review), "--review-out", str(out)], kind,
                           monkeypatch)
        assert stub.state.requests == []
        assert handoff["resume_argv"] == ["os-optics", "order", "quote", str(bom), "--schema-version", "2", "--confirm",
                                          str(review), "--review-out", str(out)]
        Path(handoff["token_file"]).parent.mkdir(parents=True)
        token_file(Path(handoff["token_file"]), AGENT)
        done = _run(kind, handoff["resume_command"], _shell_env(env, stub.url))
        answer = json.loads(done.stdout)
        assert answer["status"] == "CHANGES_REQUIRED" and answer["review"] == str(out), done.stderr
        sent = [r for r in stub.state.requests if r["method"] == "POST"]
        assert len(sent) == 1 and json.loads(sent[0]["body"]) == proposed
        assert sent[0]["headers"]["Idempotency-Key"] == idempotency_key_for("part-list-submit",
                                                                            canonical_json_bytes(proposed))
        written = json.loads(out.read_text("utf-8"))
        assert written["round"] == 2 and written["submitted"] == proposed
