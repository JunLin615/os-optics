"""The plan directory: write protocol, reopen and verification, the journal, the lock and the reader rules."""

from __future__ import annotations

import json

import pytest
from plan_helpers import MECHANICAL, USER_MECHANICAL, catalog, derive

from os_optics import assembly_plan as ap
from os_optics.assembly_plan.contract import AssemblyPlanErrorCode
from os_optics.assembly_plan.directory import PlanDirectory, revision_bytes


def test_derive_writes_a_complete_directory_that_reopens(tmp_path):
    plan = derive(tmp_path)
    root = tmp_path / "plan"
    assert sorted(p.name for p in root.iterdir()) == ["history.jsonl", "plan.json", "projects", "revisions"]
    head = plan.head
    assert head.revision == 1 and head.origin.value == "DERIVE" and head.parent_digest is None
    assert (root / "revisions" / "r0001.json").read_bytes() == (root / "plan.json").read_bytes() == revision_bytes(head)
    assert (root / "projects" / f"{head.bindings.project.project_hash.digest}.json").is_file()
    assert plan.verify() == [] and not (root / "plan.lock").exists()
    assert ap.open_plan(root).head == head  # reopened from the files alone


def test_the_journal_has_no_clock_and_names_the_head(tmp_path):
    plan = derive(tmp_path)
    plan.edit([{"op": "pin", "item_id": "mnt-1", "pinned": True}], op_id="pin-1")
    lines = [json.loads(line) for line in (tmp_path / "plan" / "history.jsonl").read_text().splitlines()]
    assert [line["seq"] for line in lines] == [1, 2] and [line["kind"] for line in lines] == ["DERIVE", "EDIT"]
    assert all("at" not in line and "engine_build_id" in line for line in lines)
    assert lines[1]["op_id"] == "pin-1" and lines[1]["base_revision"] == 1 and lines[1]["result_revision"] == 2
    assert lines[0]["op_id"].startswith("auto-") and lines[1]["result_digest"] == plan.head.content_digest
    assert plan.history(op_id="pin-1")[0].result_revision == 2 and len(plan.history(limit=1)) == 1


def test_derive_refuses_a_directory_that_is_not_empty(tmp_path):
    (tmp_path / "plan").mkdir()
    (tmp_path / "plan" / "notes.txt").write_text("mine")
    with pytest.raises(ap.AssemblyPlanError) as caught:
        derive(tmp_path)
    assert caught.value.code is AssemblyPlanErrorCode.PLAN_DIR_NOT_EMPTY
    assert sorted(p.name for p in (tmp_path / "plan").iterdir()) == ["notes.txt"]


def test_rederive_from_a_plan_keeps_ids_and_writes_the_next_revision(tmp_path):
    plan = derive(tmp_path)
    plan.edit([{"op": "set_purchase", "item_id": "pst-1", "selected": True, "spare_units": 2}])
    again = ap.derive_plan(plan.project(), from_plan=tmp_path / "plan", mechanical=MECHANICAL, catalog=catalog())
    revision = again.revision
    assert revision.revision == 3 and revision.origin.value == "REDERIVE" and revision.parent_digest == plan.revision(2).content_digest
    assert {i.item_id for i in revision.items} == {i.item_id for i in plan.revision(1).items}
    assert revision.item("pst-1").purchase.spare_units == 2  # the purchase choice is kept
    assert ap.open_plan(tmp_path / "plan").verify() == []


@pytest.mark.parametrize("damage", ["revision", "plan_json", "project", "history"])
def test_an_inconsistent_directory_is_refused_with_its_problems(tmp_path, damage):
    plan = derive(tmp_path)
    plan.edit([{"op": "pin", "item_id": "mnt-1", "pinned": True}])
    root = tmp_path / "plan"
    if damage == "revision":
        document = json.loads((root / "revisions" / "r0001.json").read_bytes())
        document["frame"]["yaw_deg"] = 1.0
        (root / "revisions" / "r0001.json").write_text(json.dumps(document))
    elif damage == "plan_json":
        (root / "plan.json").write_bytes((root / "revisions" / "r0001.json").read_bytes())
    elif damage == "project":
        next((root / "projects").iterdir()).unlink()
    else:
        (root / "history.jsonl").write_text((root / "history.jsonl").read_text() + "{not json\n")
    with pytest.raises(ap.AssemblyPlanError) as caught:
        ap.open_plan(root)
    assert caught.value.code is AssemblyPlanErrorCode.PLAN_DIR_INCONSISTENT and caught.value.problems


def test_an_interrupted_write_leaves_a_revision_file_that_is_ignored_and_overwritten(tmp_path):
    plan = derive(tmp_path)
    root = tmp_path / "plan"
    (root / "revisions" / "r0002.json").write_bytes(b"{partial")
    assert plan.verify() == [] and PlanDirectory.at(root).stray_revisions() == [2]
    result = plan.edit([{"op": "pin", "item_id": "mnt-1", "pinned": True}])
    assert result.revision.revision == 2 and plan.verify() == []


def test_a_held_lock_refuses_a_second_writer_and_writes_nothing(tmp_path):
    plan = derive(tmp_path)
    (tmp_path / "plan" / "plan.lock").write_bytes(b"")
    with pytest.raises(ap.AssemblyPlanError) as caught:
        plan.edit([{"op": "pin", "item_id": "mnt-1", "pinned": True}])
    assert caught.value.code is AssemblyPlanErrorCode.PLAN_DIR_LOCKED and "plan.lock" in caught.value.details["recovery"]
    assert plan.head.revision == 1


def test_user_documents_are_copied_so_the_plan_reopens_with_its_own_copies(tmp_path):
    plan = derive(tmp_path, mechanical=USER_MECHANICAL)
    head = plan.head
    users = [b for b in head.bindings.mechanical if b.publisher.value == "USER"]
    assert len(users) == 1
    assert (tmp_path / "plan" / "mechanical" / f"{users[0].sha256}.json").is_file()
    reopened = ap.open_plan(tmp_path / "plan", catalog=catalog(), mechanical=MECHANICAL)  # the OPENSCI file only
    sources = reopened.sources(head, MECHANICAL)
    assert [d.publisher for d in sources.documents] == ["OPENSCI", "USER"]
    assert reopened.check().status.state is not None


def test_a_plan_without_its_mechanical_catalog_names_the_recovery(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENSCI_CATALOG_DIR", str(tmp_path / "empty-store"))
    monkeypatch.delenv("OS_OPTICS_MECHANICAL_CATALOG", raising=False)
    derive(tmp_path)
    plan = ap.open_plan(tmp_path / "plan", catalog=catalog())
    with pytest.raises(ap.AssemblyPlanError) as caught:
        plan.check()
    assert caught.value.code is AssemblyPlanErrorCode.MECHANICAL_CATALOG_NOT_AVAILABLE
    assert "catalog sync" in caught.value.message
