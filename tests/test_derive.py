"""Deriving an assembly plan (``assembly_plan.derive``): the kernel's realization as plan content, refusals as
findings on a candidate, determinism, budgets, and ``--from`` id preservation."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest
from derive_helpers import (HWP, ROTATION_MOUNT, chain_of, content_model, item, optical_catalog, options, project, revision_of, sources,
                            with_hwp)

from os_optics.assembly_plan.contract import AssemblyPlanError, plan_content_digest
from os_optics.assembly_plan.derive import derive_plan_content
from os_optics.assembly_plan.kernel import KernelCatalog
from os_optics.local import update_project

KINDS = ("focus", "folded", "relay_4f", "beam_expander", "dichroic_routing", "polarization", "folded_expander",
         "two_color_dichroic", "dichroic_combiner")


def derive(p, **opts):
    return derive_plan_content(p, optical_catalog(), sources(), options(**opts))


def codes(outcome) -> list[tuple[str, tuple[str, ...]]]:
    return sorted((f.code.value, f.item_ids) for f in outcome.derive_findings)


# ------------------------------------------------------------------------------------------------ ordinary designs


@pytest.mark.parametrize("kind", KINDS)
def test_every_reference_design_derives_to_valid_plan_content(kind):
    outcome = derive(project(kind))
    plan = content_model(outcome.content)
    assert outcome.realization is not None and outcome.refusal is None
    assert plan.origin.value == "DERIVE" and plan.revision == 1 and plan.plan_id.startswith("ap-")
    assert plan.bindings.mechanical[0].publisher.value == "OPENSCI"
    for optic in (i for i in plan.items if i.role.value == "OPTIC" and i.mounting.value == "CHAIN"):
        chain = chain_of(outcome.content, optic.item_id)
        assert set(chain) == {"MOUNT", "POST", "POST_HOLDER", "BREADBOARD"}
        assert all(chain[r]["identity"]["kind"] == "OS_MECH" for r in ("MOUNT", "POST", "POST_HOLDER"))
        retained = next(c for c in outcome.project.components if c.instance_id == optic.design_instance_id).retained_dofs
        assert [d.axis.value for d in optic.placement.dofs] == [d.axis.value for d in retained]


def test_the_focus_lens_chain_is_closed_fixed_and_delivers_its_dofs():
    outcome = derive(project("focus"))
    content = outcome.content
    lens = item(content, instance="L1")
    chain = chain_of(content, lens["item_id"])
    mount, post, holder, board = chain["MOUNT"], chain["POST"], chain["POST_HOLDER"], chain["BREADBOARD"]
    assert mount["identity"]["opensci_sku"] == "OS-MECH-TEST-LENS-MOUNT-XY-D25"  # Tx / Ty retained: a translating mount
    assert board["item_id"] == content["board_item_id"] and board["placement"]["assembly_order"] == 1
    # height closure: holder height + post length + mount centre height - insertion = the optic's lab height
    depth = float(next(s["value"] for s in post["placement"]["settings"] if s["kind"] == "INSERTION_DEPTH"))
    holder_h = int(holder["identity"]["opensci_sku"].rsplit("-H", 1)[1])
    post_l = int(post["identity"]["opensci_sku"].rsplit("-L", 1)[1])
    assert holder_h + post_l + 20.0 - depth == pytest.approx(lens["placement"]["pose_in_lab"]["position_mm"][2])
    fixation = holder["placement"]["fixation"]
    assert fixation["thread"] == "M6x1.0" and 15.0 <= fixation["slot_offset_mm"] <= 35.0 and len(fixation["board_holes"]) == 1
    dofs = {d["axis"]: d for d in lens["placement"]["dofs"]}
    assert dofs["Tx"]["status"] == dofs["Ty"]["status"] == "DELIVERED" and dofs["Tx"]["mechanism"] == "TRANSLATION_MOUNT"
    assert dofs["Tz"]["status"] == "DELIVERED_AS_MANUAL_FALLBACK"
    ranges = {r["range_id"]: r for r in lens["placement"]["ranges"]}
    assert {d["range_id"] for d in dofs.values()} <= set(ranges)
    height = ranges[f"rng-{lens['item_id']}-post-height"]
    assert height["min"] <= 0.0 <= height["max"] and height["provided_by"] == holder["item_id"]
    links = {link["kind"]: link for link in content["links"] if link["a_item_id"] in {lens["item_id"], mount["item_id"], post["item_id"],
                                                                                       holder["item_id"]}}
    assert links["OPTIC_IN_MOUNT"]["interface"]["designation"].startswith("OD") and links["OPTIC_IN_MOUNT"]["interface"]["diameter_mm"]
    assert links["POST_IN_HOLDER"]["interface"]["depth_mm"] == pytest.approx(depth)
    assert links["HOLDER_ON_BOARD"]["datum_b"] == "board_top"
    src = item(content, instance="SRC")
    assert (src["role"], src["supply"], src["supply_reason"]) == ("COMPONENT", "CUSTOMER_OWNED", "CUSTOMER_LIGHT_SOURCE")
    purchase = [i["identity"].get("opensci_sku") for i in content["items"] if i["purchase"]["selected"]]
    assert len(purchase) == 5 and all(sku.startswith(("OS-OPT-", "OS-MECH-")) for sku in purchase)
    assert outcome.derive_findings == ()


def test_a_derived_plan_is_deterministic_in_this_process_and_another():
    first = derive(project("folded_expander"))
    second = derive(project("folded_expander"))
    assert first.content == second.content and first.derive_findings == second.derive_findings
    digest = plan_content_digest(first.content)
    script = ("import sys; sys.path.insert(0, 'tests');"
              "from derive_helpers import optical_catalog, options, project, sources;"
              "from os_optics.assembly_plan.derive import derive_plan_content;"
              "from os_optics.assembly_plan.contract import plan_content_digest;"
              "print(plan_content_digest(derive_plan_content(project('folded_expander'), optical_catalog(), sources(), options()).content))")
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=600,
                         cwd=Path(__file__).resolve().parents[1])
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == digest


# ------------------------------------------------------------------------------------------------ the refinement case


def test_a_rotation_mount_that_does_not_fit_leaves_the_waveplates_dof_blocked_by_the_lens_mount():
    outcome = derive(with_hwp(project("focus"), 30.0))
    content = outcome.content
    hwp = item(content, instance="HWP")
    lens_mount = chain_of(content, item(content, instance="L1")["item_id"])["MOUNT"]
    assert chain_of(content, hwp["item_id"])["MOUNT"]["identity"]["opensci_sku"] != ROTATION_MOUNT
    (rz,) = hwp["placement"]["dofs"]
    assert (rz["axis"], rz["status"], rz["not_delivered_reason"], rz["blocking_item_id"]) == (
        "Rz", "NOT_DELIVERED", "HARDWARE_DID_NOT_FIT", lens_mount["item_id"])
    assert codes(outcome) == [("DOF_NOT_DELIVERED", tuple(sorted((hwp["item_id"], lens_mount["item_id"]))))]
    # moved 25 mm upstream the rotation mount fits and delivers Rz
    moved = derive(with_hwp(project("focus"), 55.0))
    hwp = item(moved.content, instance="HWP")
    assert chain_of(moved.content, hwp["item_id"])["MOUNT"]["identity"]["opensci_sku"] == ROTATION_MOUNT
    assert hwp["placement"]["dofs"][0]["status"] == "DELIVERED" and moved.derive_findings == ()


# ------------------------------------------------------------------------------------------------ refusals become findings


def test_a_board_limit_too_small_falls_back_to_a_candidate_with_board_too_small():
    outcome = derive(project("relay_4f"), max_board_size_mm=(150.0, 150.0))
    assert outcome.refusal is not None and outcome.realization is None
    found = dict((c, i) for c, i in codes(outcome))
    assert {"BOARD_TOO_SMALL", "DERIVE_FELL_BACK"} <= set(found)
    assert found["BOARD_TOO_SMALL"] == (outcome.content["board_item_id"],)
    fell = next(f for f in outcome.derive_findings if f.code.value == "DERIVE_FELL_BACK")
    assert fell.details["kernel_refusal"]["code"] == outcome.refusal.code and fell.severity.value == "INFO"
    content_model(outcome.content)


def test_an_unreachable_beam_height_leaves_the_chain_unresolved_with_height_not_reachable():
    outcome = derive(project("focus"), beam_height_mm=10.0)
    lens = item(outcome.content, instance="L1")
    chain = chain_of(outcome.content, lens["item_id"])
    assert {chain[r]["identity"]["kind"] for r in ("MOUNT", "POST", "POST_HOLDER")} == {"NONE"}
    assert all(chain[r]["supply"] == "UNRESOLVED" and "placement" not in chain[r] for r in ("MOUNT", "POST", "POST_HOLDER"))
    assert ("HEIGHT_NOT_REACHABLE", (lens["item_id"],)) in codes(outcome)
    assert "support" not in lens["placement"] and all(d["status"] == "NOT_DELIVERED" for d in lens["placement"]["dofs"])
    content_model(outcome.content)


@pytest.mark.parametrize("budget", [{"max_chain_placements": 2, "max_geometry_checks": 3_000_000},
                                    {"max_chain_placements": 12000, "max_geometry_checks": 50}])
def test_a_spent_work_budget_is_search_budget_exhausted_and_still_a_plan(budget):
    started = time.perf_counter()
    outcome = derive(project("relay_4f"), budget=budget)
    assert time.perf_counter() - started < 30.0
    exhausted = next(f for f in outcome.derive_findings if f.code.value == "SEARCH_BUDGET_EXHAUSTED")
    assert exhausted.severity.value == "UNKNOWN" and exhausted.item_ids == () and exhausted.details["deadline_hit"] is False
    assert ("DERIVE_FELL_BACK", ()) in codes(outcome)
    content_model(outcome.content)


def test_a_time_limit_stops_the_search_through_the_run_monitor_without_hanging():
    started = time.perf_counter()
    outcome = derive(project("folded_expander"), budget={"max_chain_placements": 12000, "max_geometry_checks": 3_000_000,
                                                          "time_limit_s": 1e-6})
    assert time.perf_counter() - started < 30.0
    exhausted = next(f for f in outcome.derive_findings if f.code.value == "SEARCH_BUDGET_EXHAUSTED")
    assert exhausted.details["deadline_hit"] is True
    content_model(outcome.content)


def test_an_optic_whose_axis_is_not_horizontal_is_an_anchor_with_unsupported_orientation():
    p = project("focus")
    up = update_project(p, [{"op": "set_component_pose", "instance_id": "L1", "quaternion_wxyz": [1.0, 0.0, 0.0, 0.0]}]).project
    outcome = derive(up)
    lens = item(outcome.content, instance="L1")
    assert lens["mounting"] == "ANCHOR" and lens["supply"] == "OPENSCI" and "envelope" in lens
    assert ("UNSUPPORTED_OPTIC_ORIENTATION", (lens["item_id"],)) in codes(outcome)
    content_model(outcome.content)


def test_an_optic_no_mount_holds_is_an_anchor_with_mount_not_found_and_its_near_misses():
    full = KernelCatalog.from_sources(sources())
    thick_only = full.restricted_to([m.identity for m in (*full.posts, *full.holders, *full.boards)]
                                    + ["OS-MECH-TEST-MIRROR-MOUNT-KIN-D25"])
    outcome = derive_plan_content(project("focus"), optical_catalog(), sources(), options(), kcatalog=thick_only)
    lens = item(outcome.content, instance="L1")
    assert lens["mounting"] == "ANCHOR"
    finding = next(f for f in outcome.derive_findings if f.code.value == "MOUNT_NOT_FOUND")
    assert finding.item_ids == (lens["item_id"],) and finding.interface.value == "OPTIC_IN_MOUNT"
    assert finding.details["near_misses"] == [{"opensci_sku": "OS-MECH-TEST-MIRROR-MOUNT-KIN-D25", "reason": "NO_CLEAR_APERTURE"}]
    content_model(outcome.content)


def test_without_a_mechanical_catalog_the_derivation_is_refused():
    from os_optics.assembly_plan.mechanical import MechanicalSources

    with pytest.raises(AssemblyPlanError) as refused:
        derive_plan_content(project("focus"), optical_catalog(), MechanicalSources.of(), options())
    assert refused.value.code.value == "MECHANICAL_CATALOG_NOT_AVAILABLE"


# ------------------------------------------------------------------------------------------------ supply and boards


def test_customer_owned_optics_and_a_customer_table():
    table = {"size_x_mm": 600.0, "size_y_mm": 300.0, "pitch_mm": 25.0, "edge_offset_mm": 12.5, "thread": "M6"}
    outcome = derive(project("folded"), customer_owned=("M1",), customer_table=table)
    content = outcome.content
    m1 = item(content, instance="M1")
    assert (m1["mounting"], m1["supply"], m1["supply_reason"], m1["purchase"]["selected"]) == ("ANCHOR", "CUSTOMER_OWNED", "CUSTOMER_OWNED", False)
    board = item(content, item_id=content["board_item_id"])
    assert (board["identity"]["kind"], board["identity"]["opensci_sku"], board["supply"], board["supply_reason"]) == (
        "CUSTOMER_TABLE", "OS-CUSTOMER-OPTICAL-TABLE", "CUSTOMER_OWNED", "CUSTOMER_TABLE")
    assert not board["purchase"]["selected"]
    content_model(content)


def test_user_hardware_is_customer_owned():
    full = KernelCatalog.from_sources(sources(user=True))
    user_post = full.restricted_to([*(h.identity for h in full.holders), *(m.identity for m in full.mounts),
                                    *(b.identity for b in full.boards), "USER-MECH-TEST-POST-D12P7-L60"])
    outcome = derive_plan_content(project("focus"), optical_catalog(), sources(user=True), options(), kcatalog=user_post)
    post = chain_of(outcome.content, item(outcome.content, instance="L1")["item_id"])["POST"]
    assert (post["identity"]["kind"], post["supply"], post["supply_reason"], post["purchase"]["selected"]) == (
        "USER_MECH", "CUSTOMER_OWNED", "USER_HARDWARE", False)
    plan = content_model(outcome.content)
    assert [b.publisher.value for b in plan.bindings.mechanical] == ["OPENSCI", "USER"]


def test_an_opensci_optic_supplied_unmounted_is_an_anchor():
    catalog = optical_catalog(**{"OS-OPT-TEST-MIR-FLAT": {"delivered_form": "HOUSED", "opensci_mounting": "NOT_AVAILABLE"}})
    outcome = derive_plan_content(project("folded", catalog), catalog, sources(), options())
    m1 = item(outcome.content, instance="M1")
    assert (m1["mounting"], m1["supply"], m1["supply_reason"], m1["purchase"]["selected"]) == (
        "ANCHOR", "OPENSCI", "OPENSCI_SUPPLIED_UNMOUNTED", True)
    content_model(outcome.content)


# ------------------------------------------------------------------------------------------------ --from


def test_rederive_from_an_earlier_plan_keeps_ids_supply_purchase_and_pins():
    first = derive(with_hwp(project("focus"), 55.0))
    revision = revision_of(first)
    hwp_id = item(first.content, instance="HWP")["item_id"]
    payload = revision.model_dump(mode="json", by_alias=True, exclude_none=True)
    lens_id = item(first.content, instance="L1")["item_id"]
    lens_chain = chain_of(first.content, lens_id)
    for i in payload["items"]:
        if i["item_id"] == lens_chain["POST"]["item_id"]:
            i["pinned"] = True  # pin the lens's post
        if i["item_id"] == lens_chain["MOUNT"]["item_id"]:
            i["purchase"] = {"selected": True, "quantity": 1, "spare_units": 2}
    edited = type(revision).model_validate({**payload, "content_digest": plan_content_digest(payload)})
    again = derive_plan_content(with_hwp(project("focus"), 55.0), optical_catalog(), sources(), options(), from_revision=edited,
                                revision=2, parent_digest=edited.content_digest)
    content = again.content
    assert content["origin"] == "REDERIVE" and content["plan_id"] == first.content["plan_id"] and content["revision"] == 2
    assert {i["item_id"] for i in content["items"]} == {i["item_id"] for i in first.content["items"]}
    assert item(content, instance="HWP")["item_id"] == hwp_id
    chain = chain_of(content, lens_id)
    assert chain["POST"]["pinned"] is True and chain["POST"]["identity"] == lens_chain["POST"]["identity"]
    assert chain["MOUNT"]["purchase"]["spare_units"] == 2
    assert content["id_counters"] == first.content["id_counters"] and content["retired_item_ids"] == []
    content_model(content)


def test_rederive_with_a_removed_component_retires_its_items():
    first = derive(with_hwp(project("focus"), 55.0))
    revision = revision_of(first)
    again = derive_plan_content(project("focus"), optical_catalog(), sources(), options(), from_revision=revision, revision=2,
                                parent_digest=revision.content_digest)
    hwp_ids = {item(first.content, instance="HWP")["item_id"], *(i["item_id"] for i in chain_of(first.content, item(
        first.content, instance="HWP")["item_id"]).values() if i["role"] != "BREADBOARD")}
    assert set(again.content["retired_item_ids"]) == hwp_ids
    assert not hwp_ids & {i["item_id"] for i in again.content["items"]}
    content_model(again.content)


def test_the_plan_binds_the_project_the_catalogs_and_the_options():
    outcome = derive(project("focus"), beam_height_mm=125.0)
    plan = content_model(outcome.content)
    assert plan.bindings.project.project_hash.digest == outcome.project.content_hash()
    assert plan.bindings.optical_catalog.catalog_version == optical_catalog().catalog_version
    assert plan.derive_options.beam_height_mm == 125.0 and plan.frame.translation_mm[2] == pytest.approx(125.0)
    assert json.dumps(outcome.content)  # plain JSON data
