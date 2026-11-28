"""``scripts/commercial_flow_smoke.py`` drives the whole local-design commercial workflow; here against the local Commercial API
stand-in (the payment confirmation happens between the phases, as OpenSci's operator console does it)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from commerce_stub import StubServer
from delivery_helpers import sealed_reference_deliveries
from test_commerce_client import AGENT, new_state

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "commercial_flow_smoke.py"


def load_script():
    spec = importlib.util.spec_from_file_location("commercial_flow_smoke", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_smoke_script_runs_both_phases(tmp_path, monkeypatch, capsys):
    smoke = load_script()
    with StubServer(new_state(deliveries=sealed_reference_deliveries())) as stub:
        monkeypatch.setenv("OPENSCI_API_URL", stub.url)
        monkeypatch.setenv("OPENSCI_TOKEN", AGENT)
        work = tmp_path / "work"
        assert smoke.main(["phase1", "--work-dir", str(work), "--transfer-made"]) == 0
        first = json.loads(capsys.readouterr().out)["reports"][0]
        assert first["local_run"]["status"] == "UNKNOWN"
        assert first["local_run"]["targets"] == {"focus_position": "PASS", "spot_radius": "PASS"}
        assert first["detail_before_order"] == "CREATE_ORDER_FIRST" and first["detail_after_report"] == "WAIT_FOR_PAYMENT_CONFIRMATION"
        assert first["transfer_report"]["payment_state"] == "PENDING_CONFIRMATION"
        assert smoke.main(["phase2", "--work-dir", str(work)]) == 1  # not confirmed yet
        assert "confirm it" in capsys.readouterr().out
        stub.state.confirm_payment()
        assert smoke.main(["phase2", "--work-dir", str(work)]) == 0
        second = json.loads(capsys.readouterr().out)["reports"][0]
        assert second["drawing"]["status"] == "DETAILED_ASSEMBLY_DRAWING" and len(second["drawing"]["files"]) == 4
        assert "artifacts" not in second and stub.state.artifact_sets == {}  # drawings are never uploaded
    names = sorted(p.name for p in work.iterdir())
    assert names == ["assembly_local_validation.json", "drawing", "run", "state.json"]
    assert AGENT not in (work / "state.json").read_text(encoding="utf-8")


@pytest.mark.parametrize("phase", ["phase1"])
def test_the_smoke_script_reports_a_missing_configuration(phase, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("OPENSCI_API_URL", raising=False)
    assert load_script().main([phase, "--work-dir", str(tmp_path)]) == 1
    assert json.loads(capsys.readouterr().out)["reports"][0]["error"]["code"] == "API_URL_MISSING"
