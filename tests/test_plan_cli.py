"""``os-optics assembly plan ...``: the exit-code table, the summaries and the refusals (in process)."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout

import derive_helpers as h
import pytest
from plan_helpers import MECHANICAL, ROTATION_MOUNT, USER_MECHANICAL, catalog

from os_optics.cli import main


def run(*argv: str, mechanical=MECHANICAL) -> tuple[int, dict | None, str]:
    extra = [arg for path in mechanical for arg in ("--mechanical", path)] if mechanical else []
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main([*argv, *extra])
    text = out.getvalue()
    try:
        doc = json.loads(text) if text.strip() else None
    except ValueError:
        doc = None
    return code, doc, err.getvalue()


@pytest.fixture
def project_file(tmp_path):
    path = tmp_path / "project.json"
    path.write_text(json.dumps(h.with_hwp(h.project("focus", catalog()))))
    return path


def test_derive_show_check_history_and_bom(tmp_path, project_file):
    plan = tmp_path / "plan"
    code, doc, _ = run("assembly", "plan", "derive", str(project_file), "--out", str(plan))
    assert code == 0 and doc["revision"] == 1 and doc["status"] == "READY_WITH_WARNINGS" and doc["next"]
    assert {f["code"] for f in doc["findings"]} >= {"DOF_NOT_DELIVERED"}
    code, doc, _ = run("assembly", "plan", "show", str(plan), "--items")
    assert code == 0 and {i["item_id"] for i in doc["items"]} >= {"opt-1", "opt-2", "mnt-1", "mnt-2", "brd-1"}
    lens = next(i for i in doc["items"] if i["item_id"] == "opt-1")
    assert lens["chain"] == ["mnt-1", "pst-1", "hld-1"] and lens["ranges"]
    code, doc, _ = run("assembly", "plan", "check", str(plan), "--out", str(tmp_path / "check.json"))
    assert code == 0 and json.loads((tmp_path / "check.json").read_text())["plan"]["revision"] == 1
    code, doc, _ = run("assembly", "plan", "history", str(plan), mechanical=None)
    assert code == 0 and [line["kind"] for line in doc["lines"]] == ["DERIVE"]
    code, doc, _ = run("assembly", "plan", "bom", str(plan), "--purchase", "--out", str(tmp_path / "bom.json"),
                       "--report", str(tmp_path / "report.json"), mechanical=None)
    assert code == 0 and doc["next_command"].startswith("os-optics order quote")
    assert json.loads((tmp_path / "bom.json").read_text())["schema_version"] == "2"
    code, doc, _ = run("assembly", "plan", "bom", str(plan), "--design", mechanical=None)
    assert code == 0 and doc["schema"] == "os-optics.design_bom"


def test_edit_exit_codes_follow_the_plan_state(tmp_path, project_file):
    plan = tmp_path / "plan"
    run("assembly", "plan", "derive", str(project_file), "--out", str(plan))
    op = json.dumps({"op": "set_hardware", "item_id": "mnt-2", "opensci_sku": ROTATION_MOUNT})
    code, doc, _ = run("assembly", "plan", "edit", str(plan), "--op", op, "--op-id", "rotate")
    assert code == 1 and doc["status"] == "HAS_ERRORS" and doc["revision"] == 2 and doc["op_id"] == "rotate"
    assert any(f["code"] == "PART_INTERFERENCE" for f in doc["findings"])
    code, doc, _ = run("assembly", "plan", "edit", str(plan), "--op", op, "--op-id", "rotate")
    assert code == 1 and doc["replayed"] is True and doc["revision"] == 2
    code, doc, _ = run("assembly", "plan", "edit", str(plan), "--op", json.dumps({"op": "pin", "item_id": "nope-1", "pinned": True}))
    assert code == 65 and doc["error"]["code"] == "PLAN_OP_INVALID" and doc["error"]["details"]["op_code"] == "ITEM_UNKNOWN"
    code, doc, _ = run("assembly", "plan", "edit", str(plan), "--op", json.dumps({"op": "pin", "item_id": "mnt-1", "pinned": True}),
                       "--expect-base", "0" * 64)
    assert code == 65 and doc["error"]["code"] == "BASE_CHANGED"
    code, _, err = run("assembly", "plan", "edit", str(plan))
    assert code == 64 and "--ops" in err


def test_draw_refuses_a_failed_layout_unless_diagnostic(tmp_path, project_file):
    plan = tmp_path / "plan"
    run("assembly", "plan", "derive", str(project_file), "--out", str(plan))
    code, doc, _ = run("assembly", "plan", "draw", str(plan), "--out", str(tmp_path / "ok"))
    assert code == 0 and doc["delivery_ready"] is True and doc["schema_version"] == "4.2"
    run("assembly", "plan", "edit", str(plan), "--op", json.dumps({"op": "set_hardware", "item_id": "mnt-2", "opensci_sku": ROTATION_MOUNT}))
    code, doc, _ = run("assembly", "plan", "draw", str(plan), "--out", str(tmp_path / "refused"))
    assert code == 65 and doc["error"]["code"] == "DRAWING_REFUSED_VALIDATION_FAILED"
    code, doc, _ = run("assembly", "plan", "draw", str(plan), "--out", str(tmp_path / "diag"), "--diagnostic")
    assert code == 2 and doc["delivery_ready"] is False


def test_refusals_and_inconsistent_directories(tmp_path, project_file):
    plan = tmp_path / "plan"
    run("assembly", "plan", "derive", str(project_file), "--out", str(plan))
    code, doc, _ = run("assembly", "plan", "derive", str(project_file), "--out", str(plan))
    assert code == 65 and doc["error"]["code"] == "PLAN_DIR_NOT_EMPTY"
    code, doc, _ = run("assembly", "plan", "derive", str(project_file), "--out", str(tmp_path / "other"),
                       mechanical=[str(tmp_path / "missing.json")])
    assert code == 64 and doc["error"]["details"]["mechanical_code"] == "MECHANICAL_FILE_UNREADABLE"
    (plan / "plan.json").write_text("{}")
    code, doc, _ = run("assembly", "plan", "show", str(plan))
    assert code == 66 and doc["error"]["code"] == "PLAN_DIR_INCONSISTENT"
    code, _, _ = run("assembly", "plan", "show", str(tmp_path / "nowhere"))
    assert code == 66


def test_user_hardware_makes_the_plan_incomplete_and_preview_refused(tmp_path, project_file):
    plan = tmp_path / "plan"
    run("assembly", "plan", "derive", str(project_file), "--out", str(plan), mechanical=USER_MECHANICAL)
    op = json.dumps({"op": "set_hardware", "item_id": "pst-1", "opensci_sku": "USER-MECH-TEST-POST-D12P7-L60"})
    code, doc, _ = run("assembly", "plan", "edit", str(plan), "--op", op, mechanical=USER_MECHANICAL)
    assert code in (1, 2) and any(f["code"] == "LAYOUT_NOT_EVALUATED" for f in doc["findings"])
    code, doc, _ = run("assembly", "plan", "preview", str(plan), "--out", str(tmp_path / "p.zip"), mechanical=USER_MECHANICAL)
    assert code == 65 and doc["error"]["code"] == "PLAN_USER_HARDWARE_NOT_PROJECTABLE"


@pytest.mark.parametrize("kind", ["assembly-plan", "assembly-plan-ops", "design-bom", "purchase-bom", "bom-review",
                                  "public-mechanical-catalog"])
def test_schema_kinds(kind):
    code, doc, _ = run("schema", kind, mechanical=None)
    assert code == 0 and doc["type"] == "object"


def test_assembly_draw_of_a_plan_package_takes_its_design_bom(tmp_path, project_file):
    plan = tmp_path / "plan"
    run("assembly", "plan", "derive", str(project_file), "--out", str(plan))
    run("assembly", "plan", "draw", str(plan), "--out", str(tmp_path / "first"))
    run("assembly", "plan", "bom", str(plan), "--design", "--out", str(tmp_path / "design.json"), mechanical=None)
    package, detail = str(tmp_path / "first" / "preview.zip"), str(tmp_path / "first" / "assembly-detail.json")
    code, doc, _ = run("assembly", "draw", package, "--detail", detail, "--out", str(tmp_path / "again"), mechanical=None)
    assert code == 65 and doc["status"] == "ERROR"  # a local plan's package needs its design BOM
    code, doc, _ = run("assembly", "draw", package, "--detail", detail, "--out", str(tmp_path / "again"),
                       "--design-bom", str(tmp_path / "design.json"), mechanical=None)
    assert code == 0 and doc["delivery_ready"] is True
    assert (tmp_path / "again" / "assembly_drawing.svg").read_bytes() == (tmp_path / "first" / "assembly_drawing.svg").read_bytes()


def test_a_thread_the_designations_cannot_carry_is_a_usage_error_not_a_crash(tmp_path, project_file):
    code, _, err = run("assembly", "plan", "derive", str(project_file), "--out", str(tmp_path / "plan"), "--customer-table", "600",
                       "300", "--table-thread", "1/4-20")
    assert code == 64 and "--table-thread" in err
    run("assembly", "plan", "derive", str(project_file), "--out", str(tmp_path / "plan"))
    table = {"size_x_mm": 600.0, "size_y_mm": 300.0, "pitch_mm": 25.4, "edge_offset_mm": 12.7, "thread": "1/4-20"}
    code, doc, _ = run("assembly", "plan", "edit", str(tmp_path / "plan"), "--op",
                       json.dumps({"op": "set_board", "customer_table": table}))
    assert code == 65 and doc["error"]["code"] == "PLAN_OP_INVALID" and doc["error"]["details"]["op_code"] == "OP_MALFORMED"
