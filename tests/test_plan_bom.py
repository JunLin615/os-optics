"""The design BOM and the purchase BOM of a plan (DESIGN 1.4)."""

from __future__ import annotations

import pytest
from plan_helpers import LENS, derive, hardware, item_of

from os_optics import assembly_plan as ap
from os_optics._vendor.shared_contracts.common.hashes import osc1_digest
from os_optics.assembly_plan.contract import AssemblyPlanErrorCode
from os_optics.assembly_plan.schemas import DesignBomV1, PurchaseBomV2
from os_optics.client.bom_review import purchase_bom_body


def _subset(tmp_path):
    plan = derive(tmp_path, "folded")
    head = plan.head
    lens = item_of(head, LENS)
    mirror = item_of(head, "OS-OPT-TEST-MIR-FLAT")
    post = hardware(head, mirror.item_id)["POST"]
    plan.edit([{"op": "set_supply", "item_id": lens.item_id, "supply": "CUSTOMER_OWNED"},
               {"op": "set_purchase", "item_id": post.item_id, "selected": True, "spare_units": 1},
               {"op": "set_purchase", "item_id": head.board_item_id, "selected": False}])
    return plan, lens, post


def test_the_design_bom_lists_every_item_once_in_line_order(tmp_path):
    plan = derive(tmp_path, "folded")
    bom = ap.design_bom(plan)
    DesignBomV1.model_validate(bom)
    assert bom["bom_digest"] == osc1_digest({k: v for k, v in bom.items() if k != "bom_digest"})
    listed = sorted(i for line in bom["lines"] for i in line["item_ids"])
    assert listed == sorted(i.item_id for i in plan.head.items)
    assert [line["line_id"] for line in bom["lines"]] == [f"D{n}" for n in range(1, len(bom["lines"]) + 1)]
    kinds = [line["identity_kind"] for line in bom["lines"]]
    assert kinds == sorted(kinds, key=["OS_OPT", "OS_MECH", "USER_MECH", "CUSTOMER_TABLE", "CUSTOMER_PART", "NONE"].index)
    posts = next(line for line in bom["lines"] if line["role"] == "POST")
    assert posts["quantity"] == 2 and len(posts["item_ids"]) == 2  # the two chains' identical posts on one line


def test_the_purchase_bom_is_the_v2_request_body(tmp_path):
    plan = derive(tmp_path)
    body, report = ap.purchase_bom(plan)
    PurchaseBomV2.model_validate(body)
    assert purchase_bom_body(body) == body  # the commerce client sends it as it is
    assert [line["line_id"] for line in body["lines"]] == ["L1", "L2", "L3", "L4", "L5"]
    assert all(line["purpose"] == "PLAN" and line["quantity"] == 1 for line in body["lines"])
    assert report["source"]["content_digest"] == plan.head.content_digest and report["units"] == 5
    assert report["excluded"] == [{"item_ids": ["cmp-1"], "opensci_sku": None, "reason": "CUSTOMER_OWNED"}]


def test_a_subset_with_a_spare_excludes_the_customer_owned_lens_and_the_board(tmp_path):
    plan, lens, post = _subset(tmp_path)
    body, report = ap.purchase_bom(plan)
    skus = [(line["opensci_sku"], line["quantity"], line["purpose"]) for line in body["lines"]]
    assert LENS not in {s for s, _, _ in skus}
    assert ("OS-MECH-TEST-POST-D12P7-L75", 1, "SPARE") == skus[-1]
    assert not any("BREADBOARD" in s for s, _, _ in skus)
    reasons = {(tuple(e["item_ids"]), e["reason"]) for e in report["excluded"]}
    assert ((lens.item_id,), "CUSTOMER_OWNED") in reasons and ((plan.head.board_item_id,), "NOT_SELECTED") in reasons
    design = ap.design_bom(plan)
    lens_line = next(line for line in design["lines"] if line["opensci_sku"] == LENS)
    assert lens_line["supply"] == "CUSTOMER_OWNED" and lens_line["purchase"] == {"quantity": 0, "spare_units": 0}


def test_schema_version_1_merges_spares(tmp_path):
    plan, _, _ = _subset(tmp_path)
    body, report = ap.purchase_bom(plan, schema_version="1")
    assert body["schema_version"] == "1" and body["mechanical_supply"] == "NONE"
    assert all("purpose" not in line for line in body["lines"])
    posts = next(line for line in body["lines"] if line["opensci_sku"] == "OS-MECH-TEST-POST-D12P7-L75")
    assert posts["quantity"] == 3 and report["warnings"]
    assert purchase_bom_body(body, schema_version="1")["lines"] == body["lines"]


def test_a_plan_that_buys_nothing_has_no_purchase_bom(tmp_path):
    plan = derive(tmp_path)
    ops = [{"op": "set_purchase", "item_id": i.item_id, "selected": False} for i in plan.head.items if i.purchase.selected]
    plan.edit(ops)
    with pytest.raises(ap.AssemblyPlanError) as caught:
        ap.purchase_bom(plan)
    assert caught.value.code is AssemblyPlanErrorCode.PURCHASE_BOM_EMPTY
    assert plan.head.status.purchase.selected_lines == 0
