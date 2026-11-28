"""Drawings are never uploaded (#557 Q2): ``assembly drawing ID`` writes the drawing on this device and posts no
artifact set; ``--no-upload`` is accepted (deprecated, hidden, no effect) and ``--upload`` does not exist."""

from __future__ import annotations

import json

import pytest

from commerce_stub import StubServer
from delivery_helpers import sealed_reference_deliveries
from os_optics.cli import main
from test_commerce_cli import token_file
from test_commerce_client import AGENT, DRAWING_FILES, RESOLUTION_ID, new_state


@pytest.fixture()
def stub():
    with StubServer(new_state(deliveries=sealed_reference_deliveries())) as server:
        yield server


@pytest.fixture()
def env(stub, tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.delenv("OPENSCI_TOKEN", raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("OPENSCI_API_URL", stub.url)
    monkeypatch.setenv("OPENSCI_TOKEN_FILE", str(token_file(tmp_path / "agent-token", AGENT)))
    return home


def test_the_drawing_is_written_and_nothing_is_uploaded(env, stub, capsys, tmp_path):
    assert main(["order", "create", RESOLUTION_ID]) == 0
    stub.state.confirm_payment()
    capsys.readouterr()
    for extra in ([], ["--no-upload"]):
        out = tmp_path / ("drawing" + "".join(extra))
        code = main(["assembly", "drawing", RESOLUTION_ID, "--out", str(out), *extra])
        doc = json.loads(capsys.readouterr().out)
        assert code == 0 and doc["delivery_ready"] is True and "upload" not in doc
        assert sorted(p.name for p in out.iterdir()) == sorted(DRAWING_FILES)
    assert not [r for r in stub.state.requests if "artifact-sets" in r["path"]] and stub.state.artifact_sets == {}


def test_no_upload_is_hidden_and_upload_does_not_exist(capsys):
    with pytest.raises(SystemExit):
        main(["assembly", "drawing", "--help"])
    usage = capsys.readouterr().out.split("\n\n", 1)[0]  # (the description says it is accepted; the usage does not list it)
    assert "--no-upload" not in usage and "--upload" not in usage
    with pytest.raises(SystemExit) as refused:
        main(["assembly", "drawing", RESOLUTION_ID, "--out", "x", "--upload"])
    assert refused.value.code == 64
