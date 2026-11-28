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
from os_optics.client.shell_words import (command_line, command_path, device_shells, printed_command, quote_portable,
                                          quote_posix, quote_powershell, shells_text)
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
    argv = ["os-optics", "order", "quote", command_path(bom), "--schema-version", "2"]
    resume, shells, texts = printed_command(argv)
    captured = capsys.readouterr()
    error = json.loads(captured.out)["error"]
    assert code == 77 and error["code"] == "TOKEN_MISSING" and error["recovery"] == "PROVIDE_TOKEN"
    assert stub.state.requests == []  # nothing was sent
    handoff = error["details"]["handoff"]
    assert handoff == {"register_url": "http://127.0.0.1:18960/account/register?next=%2Ftokens",
                       "login_url": "http://127.0.0.1:18960/account/login?next=%2Ftokens",
                       "token_file": str(env / ".opensci" / "token"), "resume_command": resume,
                       "resume_argv": argv, "resume_shells": shells, "resume_commands": texts}
    assert shells == list(device_shells())  # (a plain path: one text serves every shell of this device)
    text = captured.err.splitlines()
    assert text[0] == f"No OpenSci Account token on this device. Nothing was sent; your BOM stays in {bom}."
    assert text[1].startswith("1. Register:  http://127.0.0.1:18960/account/register?next=%2Ftokens   (or sign in: ")
    assert text[2].startswith("2. Create an Agent token there and save it to ") and "OPENSCI_TOKEN_FILE" in text[2]
    assert text[3] == f"3. Resume with the same command (quoted for {shells_text(shells)}):  {resume}"
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
        "register_url", "login_url", "token_file", "resume_command", "resume_argv", "resume_shells", "resume_commands"}
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


def test_quote_portable():
    """One text both shells read the same: bare, single quotes, else double quotes (an apostrophe); else none."""

    assert quote_portable("C:/Users/a/bom.json") == "C:/Users/a/bom.json"
    assert quote_portable("--schema-version") == "--schema-version"
    assert quote_portable("C:/dir with spaces/bom.json") == "'C:/dir with spaces/bom.json'"
    assert quote_portable("C:\\dir\\bom.json") == "'C:\\dir\\bom.json'"  # (a backslash: single quotes in both)
    assert quote_portable("C:/it's here/bom.json") == '"C:/it\'s here/bom.json"'
    assert quote_portable("@args") == "'@args'" and quote_portable("a,b") == "'a,b'"
    for argument in ("it's $HOME", "it's `date`", 'it\'s "x"', "it's \\x", "it's!", "\u2018q\u2019 $x", ""):
        assert quote_portable(argument) is None, argument
    both = ["powershell", "posix"]
    text, shells, texts = printed_command(["os-optics", "order", "quote", "C:/it's here/bom.json"], both)
    assert text == "os-optics order quote \"C:/it's here/bom.json\"" and shells == both and texts == dict.fromkeys(both, text)
    text, shells, texts = printed_command(["os-optics", "order", "quote", "/tmp/it's $x/bom.json"], both)
    assert shells == ["powershell"] and text == texts["powershell"] == "os-optics order quote '/tmp/it''s $x/bom.json'"
    assert texts["posix"] == "os-optics order quote '/tmp/it'\"'\"'s $x/bom.json'"
    assert printed_command(["a b"], ["posix"]) == ("'a b'", ["posix"], {"posix": "'a b'"})
    assert device_shells() == (("powershell", "posix") if os.name == "nt" else ("posix",))
    assert command_path("bom.json") == (Path("bom.json").absolute().as_posix() if os.name == "nt"
                                        else str(Path("bom.json").absolute()))
    assert "\\" not in command_path(Path.cwd() / "a" / "b.json") or os.name != "nt"
    with pytest.raises(ValueError):
        printed_command(["x"], ["cmd"])


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


PORTABLE = ["C:/it's a dir/bom.json", "/tmp/it's here/b.json", "a b", "it's", "C:\\dir with space\\b.json",
            "--schema-version", "http://127.0.0.1:9", "d\u00e9j\u00e0 vu/bom.json"]


@pytest.mark.parametrize("kind", ["posix", "powershell"])
def test_the_portable_text_reaches_a_program_unchanged_in_both_shells(kind, tmp_path):
    """Executed: the one text ``printed_command`` gives for both shells is read back unchanged by each of them."""

    echo = tmp_path / "echo args.py"
    echo.write_text("import json, sys\nsys.stdout.write(json.dumps(sys.argv[1:]))\n", encoding="utf-8")
    assert all(quote_portable(argument) is not None for argument in PORTABLE)
    text, shells, _ = printed_command([sys.executable, str(echo), *PORTABLE], ["powershell", "posix"])
    if quote_portable(sys.executable) is None or quote_portable(str(echo)) is None:
        pytest.skip("the interpreter's path has no text both shells read the same")
    assert shells == ["powershell", "posix"]
    done = _run(kind, ("& " if kind == "powershell" else "") + text, {**os.environ, "MSYS_NO_PATHCONV": "1", "MSYS2_ARG_CONV_EXCL": "*"})
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == PORTABLE


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
    assert handoff["resume_argv"] == ["os-optics", "order", "quote", command_path(bom), "--schema-version", "2",
                                      "--review-out", command_path(out), "--api-url", "http://127.0.0.1:9", "--json"]
    from os_optics.cli_commerce import _quote_argv

    argv = _quote_argv(str(bom), schema_version="2", confirm=str(review), review_out=str(out), api_url="http://h:1",
                       as_json=True)
    assert argv == ["os-optics", "order", "quote", command_path(bom), "--schema-version", "2", "--confirm",
                    command_path(review), "--review-out", command_path(out), "--api-url", "http://h:1", "--json"]
    assert [a for a in argv if a.startswith("--")] == list(RESUME_PRESERVED)


def _shell_env(home, api_url: str, *, plain: bool = False) -> dict[str, str]:
    """The environment of a shell that runs ``os-optics``; ``plain``: as a person's Git Bash has it on Windows, without
    the variables that turn off MSYS path conversion."""

    scripts = sysconfig.get_path("scripts")
    assert any((Path(scripts) / name).is_file() for name in ("os-optics", "os-optics.exe")), scripts
    drop = ("OPENSCI_TOKEN", "OPENSCI_TOKEN_FILE", "OPENSCI_ACCOUNT_URL", "MSYS_NO_PATHCONV", "MSYS2_ARG_CONV_EXCL")
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env.update(HOME=str(home), USERPROFILE=str(home), OPENSCI_API_URL=api_url,
               PATH=scripts + os.pathsep + env.get("PATH", ""), PYTHONIOENCODING="utf-8")
    if not plain:
        env.update(MSYS_NO_PATHCONV="1", MSYS2_ARG_CONV_EXCL="*")
    return env


BOTH = ("powershell", "posix")


def _hard_folder(tmp_path: Path) -> Path:
    """A folder whose path holds a backslash, a space and an apostrophe: on Windows the backslash is the separator,
    elsewhere it is a character of the folder's name."""

    folder = tmp_path / ("it's a dir with spaces" if os.name == "nt" else "it's a dir\\with spaces")
    folder.mkdir()
    assert "\\" in str(folder) and " " in str(folder) and "'" in str(folder)
    return folder


def _handoff(capsys, argv: list[str], monkeypatch) -> dict:
    """The handoff of ``argv`` without a token, printed for both shells (as on Windows): ``resume_commands`` has the
    text for each, and ``resume_command`` is the one for every shell in ``resume_shells``."""

    monkeypatch.setattr(shell_words, "device_shells", lambda: BOTH)
    code = main(argv)
    captured = capsys.readouterr()
    error = json.loads(captured.out)["error"]
    assert code == 77 and error["code"] == "TOKEN_MISSING"
    handoff = error["details"]["handoff"]
    assert (handoff["resume_command"], handoff["resume_shells"], handoff["resume_commands"]) == \
        tuple(printed_command(handoff["resume_argv"], BOTH))
    assert set(handoff["resume_commands"]) == set(BOTH)
    for shell in handoff["resume_shells"]:
        assert handoff["resume_commands"][shell] == handoff["resume_command"]
    if os.name == "nt":  # (paths written with /: one text serves PowerShell and sh, the apostrophe included)
        assert handoff["resume_shells"] == list(BOTH) and "\\" not in handoff["resume_command"]
    else:  # (a backslash and an apostrophe in one argument: no text both read the same, so one text per shell)
        assert handoff["resume_shells"] == ["powershell"]
    assert (f"3. Resume with the same command (quoted for {shells_text(handoff['resume_shells'])}):  "
            f"{handoff['resume_command']}") in captured.err
    for shell, text in handoff["resume_commands"].items():
        assert shell in handoff["resume_shells"] or f"   In {shell_words.SHELL_NAMES[shell]}:  {text}" in captured.err
    return handoff


@pytest.mark.parametrize("kind", ["posix", "powershell"])
def test_resume_command_executes_v1_with_spaces(kind, env, tmp_path, capsys, monkeypatch):
    _shell(kind)
    folder = _hard_folder(tmp_path)
    bom = folder / "the bom.json"
    bom.write_text(json.dumps(BOM), encoding="utf-8")
    with StubServer(new_state()) as a, StubServer(new_state()) as b, StubServer(new_state()) as c:
        monkeypatch.setenv("OPENSCI_API_URL", a.url)
        handoff = _handoff(capsys, ["order", "quote", str(bom), "--schema-version", "1"], monkeypatch)
        assert a.state.requests == []  # nothing was sent
        assert handoff["resume_argv"] == ["os-optics", "order", "quote", command_path(bom), "--schema-version", "1"]
        Path(handoff["token_file"]).parent.mkdir(parents=True)
        token_file(Path(handoff["token_file"]), AGENT)
        done = _run(kind, handoff["resume_commands"][kind], _shell_env(env, b.url, plain=True))
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
    folder = _hard_folder(tmp_path)
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
        handoff = _handoff(capsys, ["order", "quote", str(bom), "--confirm", str(review), "--review-out", str(out)],
                           monkeypatch)
        assert stub.state.requests == []
        assert handoff["resume_argv"] == ["os-optics", "order", "quote", command_path(bom), "--schema-version", "2",
                                          "--confirm", command_path(review), "--review-out", command_path(out)]
        Path(handoff["token_file"]).parent.mkdir(parents=True)
        token_file(Path(handoff["token_file"]), AGENT)
        done = _run(kind, handoff["resume_commands"][kind], _shell_env(env, stub.url, plain=True))
        answer = json.loads(done.stdout)
        assert answer["status"] == "CHANGES_REQUIRED" and answer["review"] == str(out), done.stderr
        sent = [r for r in stub.state.requests if r["method"] == "POST"]
        assert len(sent) == 1 and json.loads(sent[0]["body"]) == proposed
        assert sent[0]["headers"]["Idempotency-Key"] == idempotency_key_for("part-list-submit",
                                                                            canonical_json_bytes(proposed))
        written = json.loads(out.read_text("utf-8"))
        assert written["round"] == 2 and written["submitted"] == proposed


@pytest.mark.parametrize("kind", ["posix", "powershell"])
def test_next_command_executes_and_keeps_json(kind, env, tmp_path, capsys, monkeypatch):
    """The ``next_command`` of a review, run in each shell as printed: it keeps ``--json`` and confirms the review."""

    _shell(kind)
    folder = _hard_folder(tmp_path)
    bom = folder / "the bom.json"
    bom.write_text(json.dumps({**BOM, "lines": [*BOM["lines"], {"opensci_sku": "OS-MECH-TR-40", "quantity": 1}]}),
                   encoding="utf-8")
    with StubServer(new_state()) as stub:
        monkeypatch.setenv("OPENSCI_API_URL", stub.url)
        monkeypatch.setattr(shell_words, "device_shells", lambda: BOTH)
        stub.state.bom_unavailable = {"OS-MECH-TR-40"}
        (env / ".opensci").mkdir()
        token_file(env / ".opensci" / "token", AGENT)
        assert main(["order", "quote", str(bom), "--json"]) == 1
        quote = json.loads(capsys.readouterr().out)
        assert "response" in quote and quote["next_argv"][-1] == "--json"
        assert (quote["next_command"], quote["next_shells"], quote["next_commands"]) == \
            tuple(printed_command(quote["next_argv"], BOTH))
        review = json.loads(Path(quote["review"]).read_text("utf-8"))
        stub.state.requests.clear()
        stub.state.bom_unavailable = set()
        done = _run(kind, quote["next_commands"][kind], _shell_env(env, stub.url, plain=True))
        assert done.returncode == 0, done.stdout + done.stderr
        answer = json.loads(done.stdout)
        assert answer["status"] == "ORDERABLE" and answer["confirmed"] is True and "response" in answer
        sent = [r for r in stub.state.requests if r["method"] == "POST"]
        assert len(sent) == 1 and json.loads(sent[0]["body"]) == review["proposed_confirmation"]
