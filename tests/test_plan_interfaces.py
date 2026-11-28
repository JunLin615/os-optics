"""Plan-level checks (``interfaces.plan_findings`` and ``check.check_content``): every interface rule, insertion and
height closure, fixation, supply, catalog binding, Project agreement and the layout check's own limits."""

from __future__ import annotations

import derive_helpers as h
import pytest
from mechanical_fixture import USER_FIXTURE, fixture_path
from plan_helpers import (BOARD_300, MECHANICAL, THIN_POST, USER_MECHANICAL, WIDE_MOUNT, catalog, derive, finding, hardware,
                          hwp_plan, item_of)

from os_optics import assembly_plan as ap
from os_optics.assembly_plan.check import check_content
from os_optics.assembly_plan.contract import AssemblyPlanErrorCode, AssemblyPlanRevisionV1, normalize_numbers
from os_optics.assembly_plan.interfaces import plan_findings
from os_optics.assembly_plan.mechanical import MechanicalSources


def _rules(revision) -> set[tuple[str, str]]:
    return {(f.interface.value, f.details["rule"]) for f in finding(revision, "INTERFACE_MISMATCH")}


def test_a_derived_plan_has_no_plan_level_error(tmp_path):
    for kind in ("focus", "folded"):
        plan = derive(tmp_path, kind, name=kind)
        found = plan_findings(plan.head, plan.context())
        assert not [f for f in found if f.severity.value in ("ERROR", "UNKNOWN")], kind
        assert finding(plan.head, "DOF_MANUAL_FALLBACK")  # the lens' NUMERICAL Tz is met by a manual adjustment


def test_seat_rules_size_and_thickness(tmp_path):
    plan = derive(tmp_path)
    mount = hardware(plan.head, "opt-1")["MOUNT"]
    wide = plan.edit([{"op": "set_hardware", "item_id": mount.item_id, "opensci_sku": WIDE_MOUNT}]).revision
    assert ("OPTIC_IN_MOUNT", "SIZE") in _rules(wide)
    issue = finding(wide, "INTERFACE_MISMATCH")[0]
    assert issue.item_ids == ("mnt-1", "opt-1") and issue.check.value == "PLAN_INTERFACES" and issue.details["expected"] == 50.8


def test_post_rules_thread_diameter_and_insertion(tmp_path):
    plan = derive(tmp_path)
    post = hardware(plan.head, "opt-1")["POST"]
    thin = plan.edit([{"op": "set_hardware", "item_id": post.item_id, "opensci_sku": THIN_POST}]).revision
    assert {("MOUNT_ON_POST", "THREAD"), ("POST_IN_HOLDER", "POST_DIAMETER")} <= _rules(thin)
    assert not finding(thin, "INSERTION_OUT_OF_RANGE")  # 20 mm of the 50 mm post stays in the holder


def test_insertion_out_of_range_after_a_lift_beyond_the_post(tmp_path):
    plan = derive(tmp_path)
    lifted = plan.edit([{"op": "move_optic", "item_id": "opt-1", "delta_design_mm": [0.0, 0.0, 100.0]}]).revision
    issue = finding(lifted, "INSERTION_OUT_OF_RANGE")
    assert issue and issue[0].item_ids == ("hld-1", "pst-1") and issue[0].details["insertion_depth_mm"] < 0.0


def test_board_thread_rule_on_a_customer_table(tmp_path):
    plan = derive(tmp_path)
    table = {"size_x_mm": 600.0, "size_y_mm": 300.0, "pitch_mm": 25.0, "edge_offset_mm": 12.5, "thread": "M4"}
    revision = plan.edit([{"op": "set_board", "customer_table": table, "recentre": True}]).revision
    assert ("HOLDER_ON_BOARD", "BOARD_THREAD") in _rules(revision)


def test_height_closure_follows_the_recorded_insertion_depth(tmp_path):
    plan = derive(tmp_path)
    payload = plan.head.payload()
    post = next(i for i in payload["items"] if i["item_id"] == "pst-1")
    setting = next(s for s in post["placement"]["settings"] if s["kind"] == "INSERTION_DEPTH")
    setting["value"] = "3"
    revision = AssemblyPlanRevisionV1.model_validate({**payload, "content_digest": ap.plan_content_digest(normalize_numbers(payload))})
    found = plan_findings(revision, plan.context())
    assert [f.code.value for f in found if f.code.value == "HEIGHT_CLOSURE_MISMATCH"] == ["HEIGHT_CLOSURE_MISMATCH"]


def test_a_shared_fixing_hole_is_found(tmp_path):
    plan = derive(tmp_path, "folded")
    first, second = sorted((i for i in plan.head.items if i.role.value == "POST_HOLDER"), key=lambda i: i.item_id)
    hole = list(first.placement.fixation.board_holes[0])
    revision = plan.edit([{"op": "set_fixation", "item_id": second.item_id, "hole": hole}]).revision
    shared = [f for f in finding(revision, "FIXATION_HOLE_INVALID") if f.details.get("reason") == "SHARED"]
    assert shared and shared[0].item_ids == tuple(sorted((first.item_id, second.item_id)))


def test_supply_findings_and_unorderable_optics():
    cat = h.optical_catalog(**{"OS-OPT-TEST-WP-HWP-633": {"opensci_mounting": "NOT_AVAILABLE"}})
    prj = h.with_hwp(h.project("focus", cat))
    result = ap.derive_plan(prj, mechanical=MECHANICAL, catalog=cat)
    hwp = item_of(result.revision, instance="HWP")
    assert hwp.mounting.value == "ANCHOR" and hwp.supply_reason.value == "OPENSCI_SUPPLIED_UNMOUNTED"
    assert [f.item_ids for f in finding(result.revision, "OPENSCI_SUPPLIED_UNMOUNTED")] == [(hwp.item_id,)]


def test_identities_missing_from_the_documents_and_changed_bindings(tmp_path):
    plan = derive(tmp_path)
    users_only = MechanicalSources.of(fixture_path(USER_FIXTURE).read_bytes(), fixture_path().read_bytes())
    context = ap.plan_context(plan.project(), plan.catalog, users_only, revision=plan.head)
    found = {f.code.value for f in plan_findings(plan.head, context)}
    assert "CATALOG_BINDING_CHANGED" in found
    assert "IDENTITY_NOT_IN_CATALOG" not in found


def test_a_project_the_plan_does_not_match_is_reported(tmp_path):
    from os_optics.local import update_project

    plan = derive(tmp_path)
    moved = update_project(plan.project(), [{"op": "set_component_pose", "instance_id": "L1", "position_mm": [5.0, 0.0, 0.0]}]).project
    from os_optics.local import load_project

    context = plan.context().with_project(load_project(moved))
    mismatch = [f for f in plan_findings(plan.head, context) if f.code.value == "PLAN_PROJECT_MISMATCH"]
    assert mismatch and mismatch[0].details["instance_id"] == "L1" and mismatch[0].details["reason"] == "POSE_DIFFERS"


def test_user_hardware_is_checked_but_not_projected(tmp_path):
    plan = derive(tmp_path, mechanical=USER_MECHANICAL)
    post = hardware(plan.head, "opt-1")["POST"]
    revision = plan.edit([{"op": "set_hardware", "item_id": post.item_id, "opensci_sku": "USER-MECH-TEST-POST-D12P7-L60"}]).revision
    swapped = revision.item(post.item_id)
    assert swapped.supply.value == "CUSTOMER_OWNED" and swapped.supply_reason.value == "USER_HARDWARE" and not swapped.purchase.selected
    assert [f.item_ids for f in finding(revision, "LAYOUT_NOT_EVALUATED")] == [(post.item_id,)]
    assert revision.status.layout_validation is None and revision.status.state.value in ("INCOMPLETE", "HAS_ERRORS")
    with pytest.raises(ap.AssemblyPlanError) as caught:
        ap.plan_preview(plan)
    assert caught.value.code is AssemblyPlanErrorCode.PLAN_USER_HARDWARE_NOT_PROJECTABLE


def test_layout_issues_become_findings_with_item_ids_and_basis(tmp_path):
    plan = hwp_plan(tmp_path)
    hwp = item_of(plan.head, instance="HWP")
    mount = hardware(plan.head, hwp.item_id)["MOUNT"]
    revision = plan.edit([{"op": "set_hardware", "item_id": mount.item_id, "opensci_sku": "OS-MECH-TEST-ROTATION-MOUNT-D25"}]).revision
    clash = finding(revision, "PART_INTERFERENCE")[0]
    assert clash.check.value == "LAYOUT_VALIDATION" and clash.severity.value == "ERROR" and clash.basis.value == "FACT"
    assert revision.status.layout_validation.status.value == "FAIL"
    folded = derive(tmp_path, "folded", name="folded")
    assert folded.head.status.evidence.conservative_profile_items  # the mirror's conservative mount profile


def test_check_content_is_deterministic(tmp_path):
    plan = derive(tmp_path)
    first = check_content(plan.head, plan.context())
    second = check_content(plan.head, plan.context())
    assert first.findings == second.findings and first.status == second.status


def test_board_change_without_recentre_reports_outside_parts(tmp_path):
    plan = hwp_plan(tmp_path)
    hwp = item_of(plan.head, instance="HWP")
    moved = plan.edit([{"op": "move_optic", "item_id": hwp.item_id, "delta_design_mm": [-25.0, 0.0, 0.0]}]).revision
    assert finding(moved, "PART_OUTSIDE_BREADBOARD") or finding(moved, "NO_FIXING_HOLE")
    fixed = plan.edit([{"op": "set_board", "opensci_sku": BOARD_300, "recentre": True}]).revision
    assert not finding(fixed, "PART_OUTSIDE_BREADBOARD") and catalog() is not None
