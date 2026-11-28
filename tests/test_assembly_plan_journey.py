"""The local assembly journey of #605 check 1 / check 2 through the command line, as a user runs it: no token, no network.

Each command runs in its own process (``python -m os_optics``) with a clean environment: a temporary home and catalog
store, no token variable, the commercial service URL on an unroutable loopback port, automatic catalog checks off, and a
socket guard that records every connection attempt (the test fails on any connection that is not to the loopback
address). Steps (DESIGN_E 5.2 / 5.3):

* ordinary A (``focus``): import the optical and mechanical fixtures, write the example Project, derive, reopen (show),
  preview, view snapshot, draw, purchase BOM, history; declare an envelope beside the lens post, move the lens into it
  (RESERVED_SPACE_INTRUSION of the envelope and the holder), move it back (cleared), the design BOM lists the envelope
  and the purchase BOM leaves it out;
* refinement (``focus`` + half-wave plate): derive (the rotation mount did not fit: DOF_NOT_DELIVERED), run the optics,
  swap in the rotation mount (PART_INTERFERENCE of the two mounts), try incompatible swaps (refused with
  --require-check; written without it, then reverted), move the plate upstream onto a larger board (the clash is gone,
  Rz delivered, the Project changed, the optics results STALE), run the optics again (CURRENT), preview, draw (the legend
  names the rotation mount), purchase BOM (the rotation mount, not the fixed one); every revision reopens.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import derive_helpers as h
import pytest
from mechanical_fixture import fixture_path

from os_optics.assembly_plan.schemas import DesignBomV1, PurchaseBomV2

ROOT = Path(__file__).resolve().parent
OPTICAL_FIXTURE = ROOT / "fixtures" / "catalog" / "test_optical_catalog.v2.json"
ROTATION = "OS-MECH-TEST-ROTATION-MOUNT-D25"
FIXED = "OS-MECH-TEST-LENS-MOUNT-FIXED-D25"
GUARD = textwrap.dedent('''
    import json, socket, sys
    LOG = sys.argv[1]
    _connect = socket.socket.connect

    def _guarded(self, address):
        host = address[0] if isinstance(address, tuple) else str(address)
        with open(LOG, "a", encoding="utf-8") as log:
            log.write(json.dumps({"host": host}) + "\\n")
        if host not in ("127.0.0.1", "::1", "localhost"):
            raise OSError("network access is not allowed in this test")
        return _connect(self, address)

    socket.socket.connect = _guarded
    from os_optics.cli import main
    sys.exit(main(sys.argv[2:]))
''')


class Shell:
    """Runs ``os-optics`` commands in fresh processes with the clean environment of the module docstring."""

    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.guard = tmp / "guard.py"
        self.guard.write_text(GUARD, encoding="utf-8")
        self.log = tmp / "connections.jsonl"
        home = tmp / "home"
        home.mkdir()
        keep = {k: v for k, v in os.environ.items() if k.upper() in ("PATH", "SYSTEMROOT", "TEMP", "TMP", "COMSPEC", "PATHEXT", "WINDIR")}
        self.env = {**keep, "HOME": str(home), "USERPROFILE": str(home), "APPDATA": str(home / "AppData"),
                    "LOCALAPPDATA": str(home / "Local"), "XDG_CONFIG_HOME": str(home / ".config"),
                    "OPENSCI_CATALOG_DIR": str(tmp / "store"), "OPENSCI_CATALOG_CHECK_INTERVAL": "0",
                    "OPENSCI_API_URL": "http://127.0.0.1:9", "OPENSCI_CATALOG_URL": "http://127.0.0.1:9/api/v1/catalog",
                    "PYTHONIOENCODING": "utf-8"}

    def __call__(self, *argv: str, expect: int | tuple[int, ...] = 0) -> dict:
        result = subprocess.run([sys.executable, str(self.guard), str(self.log), *argv], capture_output=True, text=True,
                                env=self.env, cwd=self.tmp, timeout=600, check=False)
        allowed = expect if isinstance(expect, tuple) else (expect,)
        assert result.returncode in allowed, f"{argv}: exit {result.returncode}\n{result.stdout[-3000:]}\n{result.stderr[-3000:]}"
        return json.loads(result.stdout) if result.stdout.strip().startswith("{") else {"text": result.stdout}

    def edit(self, plan: Path, *ops: dict, expect: int | tuple[int, ...] = 0, extra: tuple[str, ...] = ()) -> dict:
        args = [arg for op in ops for arg in ("--op", json.dumps(op))]
        return self("assembly", "plan", "edit", str(plan), *args, *extra, expect=expect)

    def connections(self) -> list[str]:
        if not self.log.is_file():
            return []
        return [json.loads(line)["host"] for line in self.log.read_text(encoding="utf-8").splitlines() if line.strip()]


def _codes(doc: dict) -> set[str]:
    return {f["code"] for f in doc["findings"]}


@pytest.fixture
def shell(tmp_path):
    sh = Shell(tmp_path)
    sh("catalog", "import", str(OPTICAL_FIXTURE))
    sh("catalog", "import", str(fixture_path()))
    yield sh
    assert all(host in ("127.0.0.1", "::1", "localhost") for host in sh.connections()), sh.connections()


def test_ordinary_journey_with_a_declared_envelope(shell, tmp_path):
    example = shell("project", "example", "--kind", "focus")
    project = tmp_path / "project.json"
    project.write_text(json.dumps(example), encoding="utf-8")
    plan = tmp_path / "plan"
    derived = shell("assembly", "plan", "derive", str(project), "--out", str(plan))
    assert derived["revision"] == 1 and derived["status"] in ("READY", "READY_WITH_WARNINGS")
    shown = shell("assembly", "plan", "show", str(plan), "--items")
    assert shown["content_digest"] == derived["content_digest"]
    preview = shell("assembly", "plan", "preview", str(plan), "--out", str(tmp_path / "preview.zip"))
    assert preview["manifest_schema_version"] == "3.1" and (tmp_path / "preview.zip").is_file()
    shell("view", str(plan), "--snapshot", str(tmp_path / "snap"))
    assert (tmp_path / "snap" / "iso.png").is_file()
    drawn = shell("assembly", "plan", "draw", str(plan), "--out", str(tmp_path / "drawing"))
    assert drawn["delivery_ready"] and (tmp_path / "drawing" / "assembly_drawing.pdf").is_file()
    shell("assembly", "plan", "bom", str(plan), "--purchase", "--out", str(tmp_path / "bom.json"))
    body = PurchaseBomV2.model_validate(json.loads((tmp_path / "bom.json").read_bytes()))
    assert len(body.lines) == 5 and {line.purpose for line in body.lines} == {"PLAN"}

    post = next(i for i in shown["items"] if i["item_id"] == "pst-1")
    assert post["role"] == "POST"
    full = json.loads((plan / "plan.json").read_bytes())
    x, y, _ = next(i for i in full["items"] if i["item_id"] == "pst-1")["placement"]["pose_in_lab"]["position_mm"]
    box = {"shape": "BOX", "min_mm": [x - 10.0, y + 15.0, 0.0], "max_mm": [x + 10.0, y + 35.0, 40.0]}
    declared = shell.edit(plan, {"op": "declare_envelope", "label": "Customer camera", "envelope": box})
    assert declared["ops"][0]["created_items"] == ["env-1"]
    into = shell.edit(plan, {"op": "move_optic", "item_id": "opt-1", "delta_design_mm": [0.0, 25.0, 0.0]}, expect=1)
    intrusions = [f for f in into["findings"] if f["code"] == "RESERVED_SPACE_INTRUSION"]
    assert ["env-1", "hld-1"] in [f["item_ids"] for f in intrusions] and into["project_changed"]
    back = shell.edit(plan, {"op": "move_optic", "item_id": "opt-1", "delta_design_mm": [0.0, -25.0, 0.0]})
    assert "RESERVED_SPACE_INTRUSION" not in _codes(back)
    design = DesignBomV1.model_validate(shell("assembly", "plan", "bom", str(plan), "--design"))
    envelope = next(line for line in design.lines if line.role == "DECLARED_ENVELOPE")
    assert envelope.supply == "CUSTOMER_OWNED" and envelope.item_ids == ("env-1",)
    shell("assembly", "plan", "bom", str(plan), "--purchase", "--out", str(tmp_path / "bom2.json"))
    assert not any("env" in line["opensci_sku"].lower() for line in json.loads((tmp_path / "bom2.json").read_bytes())["lines"])
    for revision, code in zip(range(1, 5), (0, 0, 1, 0), strict=True):  # revision 3 holds the intrusion
        assert shell("assembly", "plan", "show", str(plan), "--revision", str(revision), expect=code)["revision"] == revision
    history = shell("assembly", "plan", "history", str(plan))
    assert [line["kind"] for line in history["lines"]] == ["DERIVE", "EDIT", "EDIT", "EDIT"]


def test_refinement_journey(shell, tmp_path):
    project = tmp_path / "hwp.json"
    project.write_text(json.dumps(h.with_hwp(h.project("focus", h.optical_catalog()))), encoding="utf-8")
    plan = tmp_path / "plan"
    derived = shell("assembly", "plan", "derive", str(project), "--out", str(plan))
    blocked = [f for f in derived["findings"] if f["code"] == "DOF_NOT_DELIVERED"]
    assert blocked and blocked[0]["item_ids"] == ["mnt-1", "opt-2"]  # the plate's Rz, blocked by the lens mount
    ran = shell("assembly", "plan", "run-optics", str(plan), "--out", str(tmp_path / "run1"))
    assert ran["optics_results"] == "CURRENT" and ran["origin"] == "REBIND"

    swapped = shell.edit(plan, {"op": "set_hardware", "item_id": "mnt-2", "opensci_sku": ROTATION}, expect=1)
    clash = [f["item_ids"] for f in swapped["findings"] if f["code"] == "PART_INTERFERENCE"]
    assert clash == [["mnt-1", "mnt-2"]]
    head = swapped["revision"]

    for op, expected in (({"op": "set_hardware", "item_id": "mnt-2", "opensci_sku": "OS-MECH-TEST-MIRROR-MOUNT-KIN-D25"},
                          {("OPTIC_IN_MOUNT", "TRANSMISSIVE")}),
                         ({"op": "set_hardware", "item_id": "pst-2", "opensci_sku": "OS-MECH-TEST-POST-D6-L50"},
                          {("MOUNT_ON_POST", "THREAD"), ("POST_IN_HOLDER", "POST_DIAMETER")})):
        refused = shell.edit(plan, op, expect=65, extra=("--require-check",))
        assert refused["error"]["code"] == "PLAN_CHECK_REFUSED"
        written = shell.edit(plan, op, expect=1)
        assert expected <= {(f.get("interface"), f.get("rule")) for f in written["findings"] if f["code"] == "INTERFACE_MISMATCH"}
        reverted = shell.edit(plan, {"op": "revert_to", "revision": head}, expect=1)
        assert {f["code"] for f in reverted["findings"]} & {"INTERFACE_MISMATCH"} == set()
        head = reverted["revision"]

    moved = shell.edit(plan, {"op": "move_optic", "item_id": "opt-2", "delta_design_mm": [-25.0, 0.0, 0.0]},
                       {"op": "set_board", "opensci_sku": "OS-MECH-TEST-BREADBOARD-300X150-M6G25", "recentre": True},
                       extra=("--write-project", str(tmp_path / "moved.json")))
    assert "PART_INTERFERENCE" not in _codes(moved) and "OPTICS_RESULTS_STALE" in _codes(moved)
    assert moved["project_changed"] and moved["run_freshness"]["freshness"] == "HISTORICAL"
    assert moved["project_written"] and (tmp_path / "moved.json").is_file()
    full = json.loads((plan / "plan.json").read_bytes())
    dofs = next(i for i in full["items"] if i["item_id"] == "opt-2")["placement"]["dofs"]
    assert [(d["axis"], d["status"], d["mechanism"]) for d in dofs] == [("Rz", "DELIVERED", "ROTATION_MOUNT")]

    rerun = shell("assembly", "plan", "run-optics", str(plan), "--out", str(tmp_path / "run2"))
    assert rerun["optics_results"] == "CURRENT" and "OPTICS_RESULTS_STALE" not in {f["code"] for f in rerun["findings"]}
    shell("assembly", "plan", "preview", str(plan), "--out", str(tmp_path / "preview.zip"))
    drawn = shell("assembly", "plan", "draw", str(plan), "--out", str(tmp_path / "drawing"))
    assert drawn["delivery_ready"]
    legend = (tmp_path / "drawing" / "drawing_result.json").read_text(encoding="utf-8")
    assert ROTATION in legend and FIXED not in legend
    shell("assembly", "plan", "bom", str(plan), "--purchase", "--out", str(tmp_path / "bom.json"))
    skus = {line["opensci_sku"] for line in json.loads((tmp_path / "bom.json").read_bytes())["lines"]}
    assert ROTATION in skus and FIXED not in skus
    final = shell("assembly", "plan", "show", str(plan))["revision"]
    for revision in range(1, final + 1):
        assert shell("assembly", "plan", "show", str(plan), "--revision", str(revision), expect=(0, 1, 2))["revision"] == revision
