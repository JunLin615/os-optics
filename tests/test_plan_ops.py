"""Plan edit operations (DESIGN 1.3.8): each op, op refusals, the journal rules (replay, OP_ID_REUSED, BASE_CHANGED),
require_check, revert and the Project edit of an optic move."""

from __future__ import annotations

import pytest
from plan_helpers import (BOARD_300, BOARD_450, MIRROR_MOUNT, ROTATION_MOUNT, THIN_POST, codes, derive, finding, hardware, hwp_plan,
                          item_of)

from os_optics import assembly_plan as ap
from os_optics.assembly_plan.contract import AssemblyPlanErrorCode

LENS_ID = "opt-1"


def _refused(plan, ops, **kwargs):
    with pytest.raises(ap.AssemblyPlanError) as caught:
        plan.edit(ops, **kwargs)
    return caught.value


def test_set_hardware_to_a_wider_mount_reports_the_interference_pair(tmp_path):
    plan = hwp_plan(tmp_path)
    hwp = item_of(plan.head, instance="HWP")
    mount = hardware(plan.head, hwp.item_id)["MOUNT"]
    assert finding(plan.head, "DOF_NOT_DELIVERED")[0].details["reason"] == "HARDWARE_DID_NOT_FIT"
    result = plan.edit([{"op": "set_hardware", "item_id": mount.item_id, "opensci_sku": ROTATION_MOUNT}])
    revision = result.revision
    swapped = revision.item(mount.item_id)
    assert swapped.identity.opensci_sku == ROTATION_MOUNT and swapped.pinned and swapped.purchase.selected
    clash = finding(revision, "PART_INTERFERENCE")
    assert [f.item_ids for f in clash] == [tuple(sorted((mount.item_id, hardware(revision, LENS_ID)["MOUNT"].item_id)))]
    assert result.result.ops[0].changed_items and not result.result.project_changed


@pytest.mark.parametrize(("role", "sku", "expected"), [
    ("MOUNT", MIRROR_MOUNT, {("OPTIC_IN_MOUNT", "TRANSMISSIVE")}),
    ("POST", THIN_POST, {("MOUNT_ON_POST", "THREAD"), ("POST_IN_HOLDER", "POST_DIAMETER")}),
])
def test_an_incompatible_swap_names_its_interface_and_require_check_refuses_it(tmp_path, role, sku, expected):
    plan = hwp_plan(tmp_path)
    hwp = item_of(plan.head, instance="HWP")
    target = hardware(plan.head, hwp.item_id)[role]
    refused = _refused(plan, [{"op": "set_hardware", "item_id": target.item_id, "opensci_sku": sku}], require_check=True)
    assert refused.code is AssemblyPlanErrorCode.PLAN_CHECK_REFUSED and plan.head.revision == 1
    assert expected <= {(f["interface"], f["details"]["rule"]) for f in refused.details["findings"] if f["code"] == "INTERFACE_MISMATCH"}
    written = plan.edit([{"op": "set_hardware", "item_id": target.item_id, "opensci_sku": sku}]).revision
    assert expected <= {(f.interface.value, f.details["rule"]) for f in finding(written, "INTERFACE_MISMATCH")}
    reverted = plan.edit([{"op": "revert_to", "revision": 1}]).revision
    assert reverted.origin.value == "REVERT" and reverted.revision == 3
    assert reverted.item(target.item_id).identity.opensci_sku == target.identity.opensci_sku
    assert not finding(reverted, "INTERFACE_MISMATCH")


def test_a_move_edits_the_project_and_makes_the_bound_run_stale(tmp_path):
    from os_optics.local import run_optical_project

    plan = hwp_plan(tmp_path)
    run = run_optical_project(plan.project(), catalog=plan.catalog, output_dir=tmp_path / "run")
    bound = plan.bind_run(run.local_validation).revision
    assert bound.origin.value == "REBIND" and bound.status.optics_results.value == "CURRENT"
    hwp = item_of(bound, instance="HWP")
    mount = hardware(bound, hwp.item_id)["MOUNT"]
    plan.edit([{"op": "set_hardware", "item_id": mount.item_id, "opensci_sku": ROTATION_MOUNT}])
    moved = plan.edit([{"op": "move_optic", "item_id": hwp.item_id, "delta_design_mm": [-25.0, 0.0, 0.0]},
                       {"op": "set_board", "opensci_sku": BOARD_300, "recentre": True}])
    revision = moved.revision
    assert moved.result.project_changed and moved.result.ops[0].project_changed
    assert moved.result.run_freshness["freshness"] == "HISTORICAL" and "PHYSICS_CHANGED" in moved.result.run_freshness["reasons"]
    assert revision.status.optics_results.value == "STALE" and finding(revision, "OPTICS_RESULTS_STALE")
    assert not finding(revision, "PART_INTERFERENCE") and revision.frame.basis.value == "EDITED"
    dofs = {d.axis.value: d for d in revision.item(hwp.item_id).placement.dofs}
    assert dofs["Rz"].status.value == "DELIVERED" and dofs["Rz"].mechanism.value == "ROTATION_MOUNT"
    component = next(c for c in plan.project(revision).components if c.instance_id == "HWP")
    assert [float(v) for v in component.nominal_pose.position_mm][0] == pytest.approx(-55.0)
    assert codes(revision, "ERROR") == set()


def test_set_supply_and_purchase_shape_the_purchase_bom(tmp_path):
    plan = derive(tmp_path, "folded")
    head = plan.head
    lens = item_of(head, "OS-OPT-TEST-LENS-PCX-F100")
    mirror = item_of(head, "OS-OPT-TEST-MIR-FLAT")
    post = hardware(head, mirror.item_id)["POST"]
    revision = plan.edit([{"op": "set_supply", "item_id": lens.item_id, "supply": "CUSTOMER_OWNED"},
                          {"op": "set_purchase", "item_id": post.item_id, "selected": True, "spare_units": 1},
                          {"op": "set_purchase", "item_id": head.board_item_id, "selected": False}]).revision
    assert revision.item(lens.item_id).supply.value == "CUSTOMER_OWNED" and not revision.item(lens.item_id).purchase.selected
    assert revision.item(post.item_id).purchase.spare_units == 1 and not revision.item(head.board_item_id).purchase.selected
    refused = _refused(plan, [{"op": "set_purchase", "item_id": lens.item_id, "selected": True}])
    assert refused.details["op_code"] == "SUPPLY_NOT_ALLOWED" and refused.details["op_index"] == 0


def test_set_mounting_anchor_retires_the_chain_and_chain_creates_fresh_ids(tmp_path):
    plan = derive(tmp_path)
    chain_ids = {i.item_id for i in hardware(plan.head, LENS_ID).values()}
    anchored = plan.edit([{"op": "set_mounting", "item_id": LENS_ID, "mounting": "ANCHOR"}]).revision
    assert set(anchored.retired_item_ids) == chain_ids and anchored.item(LENS_ID).envelope is not None
    chained = plan.edit([{"op": "set_mounting", "item_id": LENS_ID, "mounting": "CHAIN"}]).revision
    fresh = {i.item_id for i in hardware(chained, LENS_ID).values()}
    assert fresh and not fresh & chain_ids and set(chained.retired_item_ids) == chain_ids
    assert all(item_id.endswith("-2") for item_id in fresh)


def test_rederive_chain_keeps_item_ids(tmp_path):
    plan = hwp_plan(tmp_path)
    hwp = item_of(plan.head, instance="HWP")
    before = {role: i.item_id for role, i in hardware(plan.head, hwp.item_id).items()}
    after = plan.edit([{"op": "rederive_chain", "item_id": hwp.item_id}]).revision
    assert {role: i.item_id for role, i in hardware(after, hwp.item_id).items()} == before


def test_set_fixation_and_board_refix_holders(tmp_path):
    plan = derive(tmp_path)
    holder = hardware(plan.head, LENS_ID)["POST_HOLDER"]
    hole = list(holder.placement.fixation.board_holes[0])
    off = plan.edit([{"op": "set_fixation", "item_id": holder.item_id, "hole": [0, 0]}]).revision
    assert {f.code.value for f in off.findings} & {"FIXATION_HOLE_INVALID", "RANGE_EXCEEDED"}
    back = plan.edit([{"op": "set_fixation", "item_id": holder.item_id, "hole": hole}]).revision
    assert not {f.code.value for f in back.findings} & {"FIXATION_HOLE_INVALID", "RANGE_EXCEEDED"}
    bigger = plan.edit([{"op": "set_board", "opensci_sku": BOARD_450, "recentre": True}]).revision
    assert bigger.item(bigger.board_item_id).identity.opensci_sku == BOARD_450 and codes(bigger, "ERROR") == set()
    table = plan.edit([{"op": "set_board", "customer_table": {"size_x_mm": 600.0, "size_y_mm": 300.0, "pitch_mm": 25.0,
                                                               "edge_offset_mm": 12.5, "thread": "M6"}, "recentre": True}]).revision
    board = table.item(table.board_item_id)
    assert board.identity.kind.value == "CUSTOMER_TABLE" and board.supply.value == "CUSTOMER_OWNED" and not board.purchase.selected


def test_declared_envelope_intrusion_is_found_and_cleared(tmp_path):
    plan = derive(tmp_path)
    x, y, _ = plan.head.item("pst-1").placement.pose_in_lab.position_mm
    box = {"shape": "BOX", "min_mm": [x - 10.0, y + 15.0, 0.0], "max_mm": [x + 10.0, y + 35.0, 40.0]}
    declared = plan.edit([{"op": "declare_envelope", "label": "Customer camera", "envelope": box}])
    env = declared.result.ops[0].created_items[0]
    assert env == "env-1" and not finding(declared.revision, "RESERVED_SPACE_INTRUSION")
    into = plan.edit([{"op": "move_optic", "item_id": LENS_ID, "delta_design_mm": [0.0, 25.0, 0.0]}]).revision
    pairs = {f.item_ids for f in finding(into, "RESERVED_SPACE_INTRUSION")}
    assert ("env-1", "hld-1") in pairs
    back = plan.edit([{"op": "move_optic", "item_id": LENS_ID, "delta_design_mm": [0.0, -25.0, 0.0]}]).revision
    assert not finding(back, "RESERVED_SPACE_INTRUSION")
    cylinder = {"shape": "CYLINDER", "diameter_mm": 10.0, "z_min_mm": 0.0, "z_max_mm": 20.0, "axis": "x", "centre_mm": [0.0, 20.0]}
    taken = _refused(plan, [{"op": "declare_envelope", "item_id": "env-1", "label": "Again", "envelope": cylinder}])
    assert taken.details["op_code"] == "ITEM_ID_TAKEN"
    removed = plan.edit([{"op": "remove_envelope", "item_id": "env-1"}]).revision
    assert "env-1" in removed.retired_item_ids and removed.item("env-1") is None


def test_adjust_moves_within_a_declared_range_and_refuses_beyond_it(tmp_path):
    plan = derive(tmp_path)
    ranges = {r.kind.value: r for r in plan.head.item(LENS_ID).placement.ranges}
    height = ranges["POST_HEIGHT"]
    step = height.max / 2.0
    moved = plan.edit([{"op": "adjust", "item_id": LENS_ID, "range_id": height.range_id, "delta": step}])
    assert moved.result.project_changed
    z0 = plan.revision(1).item(LENS_ID).placement.pose_in_lab.position_mm[2]
    assert moved.revision.item(LENS_ID).placement.pose_in_lab.position_mm[2] == pytest.approx(z0 + step, abs=1e-6)
    refused = _refused(plan, [{"op": "adjust", "item_id": LENS_ID, "range_id": height.range_id, "delta": height.max * 10.0 + 1.0}])
    assert refused.details["op_code"] == "RANGE_EXCEEDED"


def test_the_journal_replays_refuses_reuse_and_checks_the_base(tmp_path):
    plan = derive(tmp_path)
    ops = [{"op": "pin", "item_id": "mnt-1", "pinned": True}]
    first = plan.edit(ops, op_id="pin-mount", expected_base=plan.head.content_digest)
    replay = plan.edit(ops, op_id="pin-mount")
    assert replay.result.replayed and replay.revision.revision == first.revision.revision == 2 and len(plan.history()) == 2
    reused = _refused(plan, [{"op": "pin", "item_id": "mnt-1", "pinned": False}], op_id="pin-mount")
    assert reused.code is AssemblyPlanErrorCode.OP_ID_REUSED
    stale = _refused(plan, ops, expected_base=plan.revision(1).content_digest)
    assert stale.code is AssemblyPlanErrorCode.BASE_CHANGED and stale.details["head"] == plan.head.content_digest


@pytest.mark.parametrize(("ops", "op_code"), [
    ([{"op": "set_hardware", "item_id": "mnt-9", "opensci_sku": ROTATION_MOUNT}], "ITEM_UNKNOWN"),
    ([{"op": "set_hardware", "item_id": "mnt-1", "opensci_sku": THIN_POST}], "KIND_MISMATCH"),
    ([{"op": "set_hardware", "item_id": "mnt-1", "opensci_sku": "OS-MECH-TEST-NOPE"}], "IDENTITY_UNKNOWN"),
    ([{"op": "set_fixation", "item_id": "mnt-1", "hole": [1, 1]}], "ROLE_MISMATCH"),
    ([{"op": "pin", "item_id": "mnt-1", "pinned": True}, {"op": "adjust", "item_id": "opt-1", "range_id": "rng-opt-1-nope", "delta": 1.0}],
     "RANGE_UNKNOWN"),
    ([{"op": "revert_to", "revision": 7}], "REVISION_UNKNOWN"),
    ([{"op": "teleport", "item_id": "opt-1"}], "OP_MALFORMED"),
])
def test_an_invalid_op_writes_nothing(tmp_path, ops, op_code):
    plan = derive(tmp_path)
    refused = _refused(plan, ops)
    assert refused.code is AssemblyPlanErrorCode.PLAN_OP_INVALID and refused.details["op_code"] == op_code
    assert plan.head.revision == 1 and len(plan.history()) == 1


def test_apply_plan_ops_is_the_same_edit_in_memory(tmp_path):
    plan = derive(tmp_path)
    ops = [{"op": "set_purchase", "item_id": "pst-1", "selected": True, "spare_units": 1}]
    nxt, project, results, check = ap.apply_plan_ops(plan.head, plan.project(), ops, context=plan.context())
    written = plan.edit(ops).revision
    assert nxt.content_digest == written.content_digest and [r.op for r in results] == ["set_purchase"]
    assert check.status == written.status and project.content_hash() == plan.project().content_hash()
