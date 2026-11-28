"""``os-optics view TARGET`` for an Assembly Preview Package or a local assembly plan directory, and the Python API behind
it (``package_view_scene``, ``plan_view_scene``): target detection, colouring by the checks, the facts each part carries,
``--finding`` framing, refusals, snapshots without a browser. No token, no network. Synthetic data only."""

from __future__ import annotations

import json

import numpy as np
import pytest

import plan_projection_fixture as fx
from drawing_helpers import PREVIEW_ZIP
from os_optics import assembly_plan, cli
from os_optics.assembly_plan import findings as f
from os_optics.assembly_plan.contract import FindingBasisV1, FindingCheckV1, FindingSourceV1, PlanFindingCodeV1
from os_optics.assembly_plan.projection import project_plan
from os_optics.cli_view import TARGET_PACKAGE, TARGET_PLAN, TARGET_RUN, target_kind
from os_optics.viewer import ViewerInputError, package_view_scene, plan_view_scene, scene_summary
from os_optics.viewer.palette import CHECK_STATUS_RGB, HIGHLIGHT_RGB


@pytest.fixture(scope="module")
def plan():
    rev = fx.revision()
    return rev, fx.context(rev)


def _finding(code, items, **details):
    return f.make_finding(code, item_ids=items, check=FindingCheckV1.PLAN_INTERFACES, basis=FindingBasisV1.FACT,
                          source=FindingSourceV1.CHECK, details=details)


def _parts(scene) -> dict:
    return {p.part_id: p for p in scene.parts}


def test_targets_are_told_apart(tmp_path, plan):
    rev, ctx = plan
    archive = tmp_path / "preview.zip"
    archive.write_bytes(project_plan(rev, ctx).package.archive_bytes())
    package_dir = tmp_path / "pkg"
    package_dir.mkdir()
    for name, data in project_plan(rev, ctx).package.files.items():
        (package_dir / name).parent.mkdir(parents=True, exist_ok=True)
        (package_dir / name).write_bytes(data)
    plan_dir = tmp_path / "plan"
    plan_dir.mkdir()
    (plan_dir / "plan.json").write_text("{}", encoding="utf-8")
    (plan_dir / "history.jsonl").write_text("", encoding="utf-8")
    assert target_kind(str(archive)) == TARGET_PACKAGE and target_kind(str(package_dir)) == TARGET_PACKAGE
    assert target_kind(str(plan_dir)) == TARGET_PLAN and target_kind(str(tmp_path)) == TARGET_RUN


def test_a_plan_is_coloured_by_its_checks_and_findings(plan):
    rev, ctx = plan
    error = _finding(PlanFindingCodeV1.INTERFACE_MISMATCH, ("mnt-1", "pst-1"), rule="THREAD", expected="M4", found="M3")
    scene = plan_view_scene(rev, ctx, findings=[error])
    parts = _parts(scene)
    assert parts["mnt-1"].facts["check_status"] == "FAIL" and parts["pst-1"].facts["check_status"] == "FAIL"
    assert parts["mnt-1"].meshes[0].rgb == CHECK_STATUS_RGB["FAIL"]
    assert parts["mnt-1"].facts["findings"] == [{"finding_id": error.finding_id, "code": "INTERFACE_MISMATCH", "severity": "ERROR"}]
    assert parts["opt-1"].facts["check_status"] == "PASS" and parts["opt-1"].meshes[0].rgb != CHECK_STATUS_RGB["FAIL"]
    assert parts["opt-1"].facts["design_bom_line"] == "D1" and parts["opt-1"].facts["supply"] == "OPENSCI"
    assert scene.identity["plan_id"] == rev.plan_id and scene.identity["layout_validation"]["status"] == "WARNING"
    summary = scene_summary(scene)
    mount = summary["parts_by_class"]["MOUNT"][0]
    assert mount["check_status"] == "FAIL" and mount["findings"][0]["code"] == "INTERFACE_MISMATCH"


def test_a_finding_frames_and_highlights_its_pair(plan):
    rev, ctx = plan
    finding = _finding(PlanFindingCodeV1.INTERFACE_MISMATCH, ("mnt-1", "pst-1"), rule="THREAD", expected="M4", found="M3")
    scene = plan_view_scene(rev, ctx, findings=[finding], finding_id=finding.finding_id)
    parts = _parts(scene)
    assert parts["mnt-1"].facts["highlighted"] and parts["pst-1"].facts["highlighted"] and "highlighted" not in parts["opt-1"].facts
    assert parts["mnt-1"].meshes[0].rgb == HIGHLIGHT_RGB
    lo = np.minimum(parts["mnt-1"].bounds[0], parts["pst-1"].bounds[0])
    hi = np.maximum(parts["mnt-1"].bounds[1], parts["pst-1"].bounds[1])
    assert np.allclose(scene.focus_min, lo) and np.allclose(scene.focus_max, hi)
    assert scene.identity["focus_items"] == ["mnt-1", "pst-1"]
    with pytest.raises(ViewerInputError):
        plan_view_scene(rev, ctx, findings=[finding], finding_id="f-000000000000")
    whole = f.make_finding(PlanFindingCodeV1.SEARCH_BUDGET_EXHAUSTED, (), check=FindingCheckV1.DERIVE,
                           basis=FindingBasisV1.NOT_EVALUATED, source=FindingSourceV1.DERIVE, details={"bound": "max_geometry_checks"})
    with pytest.raises(ViewerInputError):
        plan_view_scene(rev, ctx, findings=[whole], finding_id=whole.finding_id)


def test_a_package_view_carries_its_layout_check(plan):
    rev, ctx = plan
    scene = package_view_scene(project_plan(rev, ctx).package)
    parts = _parts(scene)
    assert parts["mnt-1"].facts["check_status"] == "WARNING" and parts["mnt-1"].meshes[0].rgb == CHECK_STATUS_RGB["WARNING"]
    unchecked = package_view_scene(project_plan(rev, ctx).package, checks=False)
    assert "check_status" not in _parts(unchecked)["mnt-1"].facts
    commercial = package_view_scene(PREVIEW_ZIP)
    assert all("check_status" in p.facts for p in commercial.parts) and "plan_id" not in commercial.identity
    with pytest.raises(ViewerInputError):
        package_view_scene(b"not a zip")


def _run(capsys, *argv) -> tuple[int, str, str]:
    code = cli.main(["view", *argv])
    out = capsys.readouterr()
    return code, out.out, out.err


def test_view_package_writes_snapshots_without_a_token(tmp_path, capsys, monkeypatch, plan):
    for name in ("OPENSCI_TOKEN", "OPENSCI_TOKEN_FILE"):
        monkeypatch.delenv(name, raising=False)
    rev, ctx = plan
    archive = tmp_path / "preview.zip"
    archive.write_bytes(project_plan(rev, ctx).package.archive_bytes())
    code, out, _ = _run(capsys, str(archive), "--snapshot", str(tmp_path / "shots"), "--views", "iso")
    assert code == 0, out
    summary = json.loads((tmp_path / "shots" / "scene_summary.json").read_text(encoding="utf-8"))
    assert summary["identity"]["plan_id"] == rev.plan_id
    assert summary["parts_by_class"]["MOUNT"][0]["check_status"] == "WARNING"
    code, out, _ = _run(capsys, str(archive), "--write-html", str(tmp_path / "view.html"))
    assert code == 0 and (tmp_path / "view.html").stat().st_size > 0  # a local plan's package may be kept as a page
    code, _, err = _run(capsys, str(PREVIEW_ZIP), "--write-html", str(tmp_path / "commercial.html"))
    assert code == 64 and "--write-html" in err  # a delivered package stays in its delivery's terms


def test_view_refusals(tmp_path, capsys, monkeypatch, plan):
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"PK not an archive")
    code, out, _ = _run(capsys, str(bad), "--snapshot", str(tmp_path / "s"))
    assert code == 65 and json.loads(out)["error"]["code"] == "VIEW_INPUT_INVALID"
    code, _, err = _run(capsys, str(PREVIEW_ZIP), "--finding", "f-000000000000")
    assert code == 64 and "plan directory" in err
    code, _, err = _run(capsys, str(tmp_path), "--no-checks")
    assert code == 64
    plan_dir = tmp_path / "plan"
    plan_dir.mkdir()
    (plan_dir / "plan.json").write_text("{}", encoding="utf-8")
    (plan_dir / "history.jsonl").write_text("", encoding="utf-8")
    monkeypatch.delattr(assembly_plan, "open_plan_view", raising=False)
    code, out, _ = _run(capsys, str(plan_dir), "--snapshot", str(tmp_path / "s"))
    assert code == 65 and "open_plan_view" in json.loads(out)["error"]["message"]


def test_view_plan_dir_through_the_plan_opener(tmp_path, capsys, monkeypatch, plan):
    rev, ctx = plan
    finding = _finding(PlanFindingCodeV1.INTERFACE_MISMATCH, ("mnt-1", "pst-1"), rule="THREAD", expected="M4", found="M3")
    seen = {}

    def opener(plan_dir, *, revision=None, mechanical=()):
        seen.update(plan_dir=plan_dir, revision=revision, mechanical=mechanical)
        return rev, ctx, (finding,)

    monkeypatch.setattr(assembly_plan, "open_plan_view", opener, raising=False)
    plan_dir = tmp_path / "plan"
    plan_dir.mkdir()
    (plan_dir / "plan.json").write_text("{}", encoding="utf-8")
    (plan_dir / "history.jsonl").write_text("", encoding="utf-8")
    code, out, _ = _run(capsys, str(plan_dir), "--revision", "1", "--finding", finding.finding_id,
                        "--snapshot", str(tmp_path / "shots"), "--views", "iso")
    assert code == 0, out
    assert seen == {"plan_dir": str(plan_dir), "revision": 1, "mechanical": ()}
    summary = json.loads((tmp_path / "shots" / "scene_summary.json").read_text(encoding="utf-8"))
    assert summary["identity"]["focus_items"] == ["mnt-1", "pst-1"]
    assert summary["parts_by_class"]["POST"][0]["highlighted"] is True
