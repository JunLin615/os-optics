"""Model tests of the assembly plan contract (``os_optics.assembly_plan.contract`` / ``findings``): item, link and
revision invariants, the digest rule (findings excluded, number rounding), id rules, finding rules, edit operations and
the history line. Synthetic data only (``OS-MECH-TEST-*``)."""

from __future__ import annotations

import copy
import json

import pytest
from pydantic import TypeAdapter, ValidationError

from os_optics._vendor.shared_contracts.assembly_preview.validation import ISSUE_RULES, AssemblyIssueCodeV1, AssemblyIssueV1
from os_optics._vendor.shared_contracts.common.hashes import canonicalize_osc1_bytes, osc1_digest
from os_optics.assembly_plan import contract as c
from os_optics.assembly_plan import findings as f

ZERO_POSE = {"position_mm": [0.0, 0.0, 0.0], "quaternion_wxyz": [1.0, 0.0, 0.0, 0.0]}


def _ref(subject: str, ch: str) -> dict:
    return {"profile": "osc1", "digest": ch * 64, "subject": subject}


def _purchase(selected: bool = True) -> dict:
    return {"selected": selected, "quantity": 1 if selected else 0, "spare_units": 0}


def _hw(item_id: str, role: str, sku: str, name: str, parent: str, relation: str, order: int, **placement) -> dict:
    return {"item_id": item_id, "role": role, "identity": {"kind": "OS_MECH", "opensci_sku": sku, "display_name": name},
            "supply": "OPENSCI", "purchase": _purchase(),
            "placement": {"pose_in_lab": ZERO_POSE, "assembly_order": order,
                          "support": {"parent_item_id": parent, "relation": relation, "link_id": f"lnk-{item_id}-{parent}"},
                          **placement}}


def _link(kind: str, a: str, b: str, designation: str, **interface) -> dict:
    return {"link_id": f"lnk-{a}-{b}", "kind": kind, "a_item_id": a, "b_item_id": b, "datum_a": "base", "datum_b": "top",
            "interface": {"designation": designation, **interface}, "b_in_a": ZERO_POSE}


def content() -> dict:
    """One CHAIN optic (mount, post, holder on the board), a customer light source and a declared envelope."""

    optic = {"item_id": "opt-1", "role": "OPTIC", "identity": {"kind": "OS_OPT", "opensci_sku": "OS-OPT-TEST-LENS-25",
                                                              "display_name": "Lens 25.4 mm"},
             "design_instance_id": "lens1", "mounting": "CHAIN", "supply": "OPENSCI", "purchase": _purchase(),
             "placement": {"pose_in_lab": {"position_mm": [100.0, 50.0, 75.0], "quaternion_wxyz": [1.0, 0.0, 0.0, 0.0]},
                           "assembly_order": 5,
                           "support": {"parent_item_id": "mnt-1", "relation": "SEATED_IN", "link_id": "lnk-opt-1-mnt-1"},
                           "ranges": [{"range_id": "rng-opt-1-post-height", "kind": "POST_HEIGHT", "axis_lab": [0.0, 0.0, 1.0],
                                       "min": -10.0, "max": 20.0, "unit": "mm", "provided_by": "hld-1",
                                       "moves": ["mnt-1", "opt-1", "pst-1"], "basis": "RESOLVED",
                                       "adjustment": "CONTINUOUS_CLAMPED", "delivers": ["Ty"]}],
                           "dofs": [{"axis": "Ty", "requested_control": "MANUAL", "status": "DELIVERED",
                                     "mechanism": "POST_HEIGHT_CLAMP", "range_id": "rng-opt-1-post-height"}]}}
    board = {"item_id": "brd-1", "role": "BREADBOARD",
             "identity": {"kind": "OS_MECH", "opensci_sku": "OS-MECH-TEST-BB-300", "display_name": "Breadboard 300 x 300"},
             "supply": "OPENSCI", "purchase": _purchase(), "placement": {"pose_in_lab": ZERO_POSE, "assembly_order": 1}}
    source = {"item_id": "cmp-1", "role": "COMPONENT",
              "identity": {"kind": "CUSTOMER_PART", "display_name": "Laser source", "custom_part_key": "laser_part"},
              "design_instance_id": "laser", "supply": "CUSTOMER_OWNED", "supply_reason": "CUSTOMER_LIGHT_SOURCE",
              "purchase": _purchase(False), "placement": {"pose_in_lab": ZERO_POSE, "assembly_order": 6},
              "envelope": {"shape": "CYLINDER", "basis": "PROJECT_DECLARED_GEOMETRY", "diameter_mm": 30.0,
                           "z_min_mm": -40.0, "z_max_mm": 0.0}}
    envelope = {"item_id": "env-1", "role": "DECLARED_ENVELOPE",
                "identity": {"kind": "CUSTOMER_PART", "display_name": "Customer camera"},
                "supply": "CUSTOMER_OWNED", "supply_reason": "NOT_IN_CATALOG", "purchase": _purchase(False),
                "placement": {"pose_in_lab": ZERO_POSE, "assembly_order": 7},
                "envelope": {"shape": "BOX", "basis": "USER_DECLARED", "min_mm": [10.0, 10.0, 0.0], "max_mm": [30.0, 30.0, 40.0]}}
    items = [board, source, envelope,
             _hw("hld-1", "POST_HOLDER", "OS-MECH-TEST-HOLDER-40", "Post holder 40", "brd-1", "STANDS_ON", 2,
                 fixation={"board_holes": [[4, 2]], "thread": "M6", "slot_offset_mm": 0.0}),
             _hw("mnt-1", "MOUNT", "OS-MECH-TEST-MOUNT-25", "Lens mount 25.4", "pst-1", "MOUNTED_ON", 4),
             optic,
             _hw("pst-1", "POST", "OS-MECH-TEST-POST-50", "Post 50", "hld-1", "INSERTED_INTO", 3,
                 settings=[{"kind": "INSERTION_DEPTH", "value": "10", "unit": "mm", "basis": "RESOLVED"}])]
    links = [_link("HOLDER_ON_BOARD", "hld-1", "brd-1", "M6", thread="M6"),
             _link("MOUNT_ON_POST", "mnt-1", "pst-1", "M4", thread="M4"),
             _link("OPTIC_IN_MOUNT", "opt-1", "mnt-1", "OD25.4-SEAT", diameter_mm=25.4, depth_mm=4.0),
             _link("POST_IN_HOLDER", "pst-1", "hld-1", "OD12.7-POST", diameter_mm=12.7, depth_mm=10.0)]
    return {"schema": "os-optics.assembly_plan", "schema_version": "1.0", "plan_id": "ap-0123456789abcdef", "revision": 1,
            "origin": "DERIVE",
            "bindings": {"project": {"project_hash": _ref("project", "a"), "design_intent": "LAB_ASSEMBLY"},
                         "optical_catalog": {"catalog_version": "2026-11-20.1"},
                         "mechanical": [{"publisher": "OPENSCI", "mechanical_version": "m1.0123456789ab", "sha256": "b" * 64}]},
            "frame": {"yaw_deg": 0.0, "translation_mm": [150.0, 150.0, 0.0], "basis": "DERIVED"},
            "board_item_id": "brd-1", "items": items, "links": links,
            "id_counters": {"opt": 1, "cmp": 1, "mnt": 1, "pst": 1, "hld": 1, "brd": 1, "env": 1},
            "derive_options": {"budget": {"max_chain_placements": 1000, "max_geometry_checks": 100000}}}


def _status(findings=(), **over) -> c.PlanStatusV1:
    counts = c.finding_counts(list(findings))
    return c.PlanStatusV1.model_validate({"state": c.plan_state(counts).value, "counts": counts.model_dump(),
                                          "optics_results": "NOT_RUN", "evidence": {},
                                          "purchase": {"selected_lines": 5, "excluded_items": 2, "unresolved_items": 0}, **over})


CHECKED = c.CheckedWithV1(checker_version="0.15.0.dev0", kernel_version="1.0.0", plan_checks_version="1.0.0")


def build(data: dict | None = None, findings=()) -> c.AssemblyPlanRevisionV1:
    return c.build_revision(content() if data is None else data, findings=list(findings), status=_status(findings),
                            checked_with=CHECKED)


def _item(data: dict, item_id: str) -> dict:
    return next(i for i in data["items"] if i["item_id"] == item_id)


def _refused(data: dict, fragment: str) -> None:
    with pytest.raises(ValidationError) as caught:
        build(data)
    assert fragment in str(caught.value), str(caught.value)


# ------------------------------------------------------------------------------------------------ revision and digest


def test_a_built_revision_validates_round_trips_and_is_canonical():
    revision = build()
    payload = revision.payload()
    assert payload["schema"] == "os-optics.assembly_plan" and "parent_digest" not in payload
    again = c.AssemblyPlanRevisionV1.model_validate(json.loads(canonicalize_osc1_bytes(payload)))
    assert again == revision and canonicalize_osc1_bytes(again.payload()) == canonicalize_osc1_bytes(payload)
    assert revision.content_digest == c.plan_content_digest(payload) == osc1_digest(revision.content())
    assert set(revision.content()) == set(c.CONTENT_FIELDS) - {"parent_digest"}
    assert revision.hash_ref().subject == "assembly_plan_revision"


def test_the_digest_covers_decisions_only():
    finding = f.make_finding(c.PlanFindingCodeV1.DERIVE_FELL_BACK, check=c.FindingCheckV1.DERIVE, source=c.FindingSourceV1.DERIVE,
                             details={"kernel_refusal": {"code": "X"}})
    other = c.build_revision(content(), findings=[finding], status=_status([finding], optics_results="STALE"),
                             checked_with=c.CheckedWithV1(checker_version="9", kernel_version="9", plan_checks_version="9"))
    assert other.content_digest == build().content_digest
    changed = content()
    _item(changed, "opt-1")["purchase"]["spare_units"] = 1
    assert build(changed).content_digest != build().content_digest


def test_numbers_are_rounded_before_hashing_and_stored_rounded():
    data = content()
    _item(data, "opt-1")["placement"]["pose_in_lab"]["position_mm"] = [100.0000004, 50.0, 75.0]
    data["frame"]["yaw_deg"] = -0.0000001
    revision = build(data)
    assert revision.content_digest == build().content_digest
    assert revision.item("opt-1").placement.pose_in_lab.position_mm == (100.0, 50.0, 75.0)
    assert c.normalize_numbers({"quaternion_wxyz": [0.70710678118654757, 0.1234567890123456], "x": -1e-9}) == {
        "quaternion_wxyz": [0.707106781187, 0.123456789012], "x": 0.0}
    stored = revision.payload()
    stored["items"][0]["placement"]["pose_in_lab"]["position_mm"] = [0.0000004, 0.0, 0.0]
    stored["content_digest"] = c.plan_content_digest(stored)
    with pytest.raises(ValidationError, match="numbers are not normalized"):
        c.AssemblyPlanRevisionV1.model_validate(stored)


def test_a_wrong_digest_and_a_wrong_parent_are_refused():
    payload = build().payload()
    payload["content_digest"] = "0" * 64
    with pytest.raises(ValidationError, match="content_digest does not match"):
        c.AssemblyPlanRevisionV1.model_validate(payload)
    data = content()
    data["parent_digest"] = "c" * 64
    _refused(data, "parent_digest is absent exactly for revision 1")
    data = content()
    data.update(revision=2, origin="DERIVE", parent_digest="c" * 64)
    _refused(data, "origin DERIVE writes revision 1")
    data["origin"] = "EDIT"
    assert build(data).revision == 2


def test_derived_plan_ids_and_revision_file_names():
    plan_id = c.derived_plan_id("a" * 64, "2026-11-20.1", [{"publisher": "OPENSCI"}], {"beam_height_mm": 75.00000001})
    assert plan_id == c.derived_plan_id("a" * 64, "2026-11-20.1", [{"publisher": "OPENSCI"}], {"beam_height_mm": 75.0})
    assert plan_id.startswith("ap-") and len(plan_id) == len("ap-") + c.PLAN_ID_DIGEST_DIGITS
    assert c.revision_file_name(1) == "r0001.json" and c.revision_file_name(12345) == "r12345.json"
    assert c.parse_revision_file_name("r0042.json") == 42 and c.parse_revision_file_name("r12345.json") == 12345
    assert [c.parse_revision_file_name(n) for n in ("r0000.json", "r00001.json", "r1.json", "plan.json")] == [None] * 4


def test_the_json_schema_is_generated():
    schema = c.plan_json_schema()
    assert schema["properties"]["schema"]["const"] == "os-optics.assembly_plan"
    json.dumps(schema)


# ------------------------------------------------------------------------------------------------ ids


def test_item_ids_follow_role_prefix_counter_and_are_never_reused():
    data = content()
    data["id_counters"]["mnt"] = 0
    _refused(data, "mnt-1 is above id_counters.mnt")
    data = content()
    data["retired_item_ids"] = ["opt-1"]
    _refused(data, "a retired item id is never used again")
    data = content()
    data["retired_item_ids"] = ["mnt-2"]
    _refused(data, "mnt-2 is above id_counters.mnt")
    data["id_counters"]["mnt"] = 2
    assert build(data).retired_item_ids == ("mnt-2",)
    data = content()
    data["items"].reverse()
    _refused(data, "items must be unique and sorted by item_id")
    data = content()
    _item(data, "cmp-1")["item_id"] = "opt-9"
    _refused(data, "a COMPONENT item id is 'cmp-<n>'")
    assert c.item_id_parts("hld-12") == (c.ItemPrefixV1.HLD, 12) and c.item_id_parts("hld-0") is None


def test_one_board_and_unique_design_instances():
    data = content()
    data["board_item_id"] = "hld-1"
    _refused(data, "exactly one BREADBOARD item")
    data = content()
    _item(data, "cmp-1")["design_instance_id"] = "lens1"
    _refused(data, "each design instance is one item")


# ------------------------------------------------------------------------------------------------ items


@pytest.mark.parametrize(("item_id", "change", "fragment"), [
    ("cmp-1", {"purchase": {"selected": True, "quantity": 1, "spare_units": 0}}, "only an OPENSCI item can be selected"),
    ("mnt-1", {"identity": {"kind": "USER_MECH", "opensci_sku": "USER-MECH-MY-MOUNT", "display_name": "My mount"}},
     "OPENSCI supplies OS-OPT and OS-MECH identities only"),
    ("mnt-1", {"identity": {"kind": "OS_OPT", "opensci_sku": "OS-OPT-TEST-LENS-25", "display_name": "Lens"}},
     "a MOUNT item has identity"),
    ("opt-1", {"mounting": None}, "mounting is stated exactly for OPTIC items"),
    ("cmp-1", {"design_instance_id": None}, "design_instance_id is stated exactly"),
    ("cmp-1", {"supply_reason": "NOT_ORDERABLE"}, "a COMPONENT is CUSTOMER_OWNED"),
    ("env-1", {"envelope": None}, "a DECLARED_ENVELOPE states its USER_DECLARED envelope"),
    ("mnt-1", {"envelope": {"shape": "BOX", "basis": "USER_DECLARED", "min_mm": [0.0, 0.0, 0.0], "max_mm": [1.0, 1.0, 1.0]}},
     "only an anchor reserves an envelope"),
    ("opt-1", {"supply_reason": "OPENSCI_SUPPLIED_UNMOUNTED"}, "OPENSCI_SUPPLIED_UNMOUNTED is the reason of an ANCHOR optic"),
    ("brd-1", {"identity": {"kind": "CUSTOMER_TABLE", "opensci_sku": "OS-CUSTOMER-OPTICAL-TABLE", "display_name": "Table"}},
     "a customer table is CUSTOMER_OWNED with reason CUSTOMER_TABLE"),
])
def test_item_invariants(item_id, change, fragment):
    data = content()
    _item(data, item_id).update(change)
    _refused(data, fragment)


def test_identity_sku_grammar_and_purchase_rules():
    with pytest.raises(ValidationError, match="needs an opensci_sku"):
        c.ItemIdentityV1(kind="OS_MECH", opensci_sku="OS-OPT-X", display_name="X")
    with pytest.raises(ValidationError, match="has no opensci_sku"):
        c.ItemIdentityV1(kind="NONE", opensci_sku="OS-MECH-X", display_name="X")
    with pytest.raises(ValidationError, match="an unselected item buys nothing"):
        c.PurchaseV1(selected=False, quantity=1, spare_units=0)
    with pytest.raises(ValidationError):
        c.PurchaseV1(selected=True, quantity="1", spare_units=0)
    assert c.PurchaseV1(selected=True, quantity=2, spare_units=1).spare_units == 1


def test_a_customer_table_board_and_user_hardware_are_customer_owned():
    data = content()
    _item(data, "brd-1").update(identity={"kind": "CUSTOMER_TABLE", "opensci_sku": "OS-CUSTOMER-OPTICAL-TABLE",
                                          "display_name": "Optical table"},
                                supply="CUSTOMER_OWNED", supply_reason="CUSTOMER_TABLE", purchase=_purchase(False))
    _item(data, "mnt-1").update(identity={"kind": "USER_MECH", "opensci_sku": "USER-MECH-MY-MOUNT", "display_name": "My mount"},
                                supply="CUSTOMER_OWNED", supply_reason="USER_HARDWARE", purchase=_purchase(False))
    data["bindings"]["mechanical"].append({"publisher": "USER", "mechanical_version": "my-lab.1", "sha256": "d" * 64})
    revision = build(data)
    assert revision.item("mnt-1").supply is c.SupplyV1.CUSTOMER_OWNED


def test_mechanical_bindings_order_and_versions():
    data = content()
    data["bindings"]["mechanical"].insert(0, {"publisher": "USER", "mechanical_version": "lab", "sha256": "d" * 64})
    _refused(data, "the OPENSCI document first")
    with pytest.raises(ValidationError, match="is not a OPENSCI version"):
        c.MechanicalBindingV1(publisher="OPENSCI", mechanical_version="lab", sha256="d" * 64)
    with pytest.raises(ValidationError, match="osc1 HashRef of subject 'project'"):
        c.ProjectBindingV1(project_hash=_ref("workspace", "a"), design_intent="LAB_ASSEMBLY")


def test_declared_ranges_and_dof_results():
    good = _item(content(), "opt-1")["placement"]["ranges"][0]
    for change, fragment in [({"min": 1.0}, "min <= 0 <= max"), ({"unit": "deg"}, "is in mm"),
                             ({"axis_lab": [0.0, 0.0, 2.0]}, "unit vector"), ({"pivot_lab": [0.0, 0.0, 0.0]}, "a pivot exactly"),
                             ({"moves": ["pst-1", "opt-1"]}, "moves must be unique and sorted")]:
        with pytest.raises(ValidationError, match=fragment):
            c.DeclaredRangeV1.model_validate({**good, **change})
    data = content()
    _item(data, "opt-1")["placement"]["ranges"][0]["range_id"] = "rng-opt-1-slot-travel"
    _item(data, "opt-1")["placement"]["dofs"][0]["range_id"] = "rng-opt-1-slot-travel"
    _refused(data, "is named 'rng-opt-1-<kind>'")
    with pytest.raises(ValidationError, match="not_delivered_reason is stated exactly"):
        c.DofResultV1(axis="Tx", requested_control="MANUAL", status="NOT_DELIVERED", mechanism="NONE")
    with pytest.raises(ValidationError, match="a manual fallback answers a NUMERICAL request"):
        c.DofResultV1(axis="Tx", requested_control="MANUAL", status="DELIVERED_AS_MANUAL_FALLBACK", mechanism="NONE")
    data = content()
    _item(data, "opt-1")["placement"]["dofs"][0]["range_id"] = "rng-opt-1-mount-tip"
    _refused(data, "a DOF result names a range of this placement")


def test_envelopes_by_shape():
    with pytest.raises(ValidationError, match="a CYLINDER envelope states"):
        c.ReservedEnvelopeV1(shape="CYLINDER", basis="CATALOG_DESIGN_GEOMETRY", min_mm=(0, 0, 0), max_mm=(1, 1, 1))
    with pytest.raises(ValidationError, match="min_mm < max_mm"):
        c.ReservedEnvelopeV1(shape="BOX", basis="USER_DECLARED", min_mm=(0, 0, 0), max_mm=(1, 0, 1))


# ------------------------------------------------------------------------------------------------ links and chains


def test_links_carry_the_interface_diameter_and_depth():
    link = next(l for l in build().links if l.kind is c.ChainLinkKindV1.POST_IN_HOLDER)
    assert (link.interface.diameter_mm, link.interface.depth_mm) == (12.7, 10.0)


def test_a_chain_optic_needs_its_whole_chain_and_an_anchor_none():
    data = content()
    data["links"] = [lnk for lnk in data["links"] if lnk["kind"] != "HOLDER_ON_BOARD"]
    _item(data, "hld-1")["placement"]["support"] = None
    _refused(data, "CHAIN optic opt-1 needs its mount, post and holder linked down to the board")
    data = content()
    _item(data, "opt-1").update(mounting="ANCHOR")
    _item(data, "opt-1")["placement"]["support"] = None
    _refused(data, "ANCHOR optic opt-1 has no chain links")
    data = content()
    data["links"][0] = {**data["links"][0], "kind": "POST_IN_HOLDER"}
    _refused(data, "joins a POST to a POST_HOLDER")


def test_supports_follow_the_chain_links():
    data = content()
    _item(data, "pst-1")["placement"]["support"]["relation"] = "STANDS_ON"
    _refused(data, "pst-1: support must be its chain link lnk-pst-1-hld-1")
    data = content()
    _item(data, "cmp-1")["placement"]["support"] = {"parent_item_id": "brd-1", "relation": "STANDS_ON", "link_id": "lnk-cmp-1-brd-1"}
    _refused(data, "the board and anchors have no support")
    with pytest.raises(ValidationError, match="link_id is 'lnk-'"):
        c.ChainLinkV1.model_validate({**_link("MOUNT_ON_POST", "mnt-1", "pst-1", "M4"), "link_id": "lnk-mnt-1-pst-2"})


def test_unresolved_chain_hardware_keeps_its_links_without_interface():
    data = content()
    for item_id in ("pst-1", "hld-1"):
        _item(data, item_id).update(identity={"kind": "NONE", "display_name": "Unresolved"}, supply="UNRESOLVED",
                                    supply_reason="NO_COMPATIBLE_HARDWARE", purchase=_purchase(False), placement=None)
    for link in data["links"]:
        if link["kind"] != "OPTIC_IN_MOUNT":
            link.update(interface=None, b_in_a=None)
    _item(data, "mnt-1")["placement"]["support"] = None
    _item(data, "opt-1")["placement"]["ranges"] = []
    _item(data, "opt-1")["placement"]["dofs"] = []
    finding = f.make_finding(c.PlanFindingCodeV1.HEIGHT_NOT_REACHABLE, ["opt-1", "pst-1"], check=c.FindingCheckV1.DERIVE,
                             source=c.FindingSourceV1.DERIVE, details={"reachable_axis_height_mm": 60.0})
    revision = build(data, [finding])
    assert revision.status.state is c.PlanStateV1.HAS_ERRORS
    assert revision.item("pst-1").placement is None
    broken = copy.deepcopy(data)
    _item(broken, "pst-1")["placement"] = {"pose_in_lab": ZERO_POSE, "assembly_order": 3}
    _refused(broken, "placement is absent exactly for unresolved chain hardware")
    broken = copy.deepcopy(data)
    next(lnk for lnk in broken["links"] if lnk["kind"] == "MOUNT_ON_POST").update(
        interface={"designation": "M4"}, b_in_a=ZERO_POSE)
    _refused(broken, "absent exactly when an end is unresolved")
    broken = copy.deepcopy(data)
    _item(broken, "mnt-1")["placement"]["support"] = {"parent_item_id": "pst-1", "relation": "MOUNTED_ON",
                                                      "link_id": "lnk-mnt-1-pst-1"}
    _refused(broken, "no support while its carrier is unresolved")


# ------------------------------------------------------------------------------------------------ findings and status


def test_every_code_has_a_rule_with_a_bounded_message():
    assert set(f.FINDING_RULES) == set(c.PlanFindingCodeV1) | set(AssemblyIssueCodeV1)
    assert not set(c.PlanFindingCodeV1.__members__) & set(AssemblyIssueCodeV1.__members__)
    for code, rule in f.FINDING_RULES.items():
        assert 0 < len(rule.message) <= c.FINDING_MESSAGE_MAX, code
        assert rule.checks and rule.item_counts and max(rule.item_counts) <= 2, code
    assert f.FINDING_RULES[AssemblyIssueCodeV1.RESERVED_SPACE_INTRUSION].checks == {c.FindingCheckV1.LAYOUT_VALIDATION,
                                                                                   c.FindingCheckV1.PLAN_LAYOUT}
    assert f.kernel_refusal_code("NO_COMPATIBLE_MOUNT") is c.PlanFindingCodeV1.MOUNT_NOT_FOUND
    assert f.kernel_refusal_code("X", "MECHANICAL_RESOLUTION_BOUND_EXCEEDED") is c.PlanFindingCodeV1.SEARCH_BUDGET_EXHAUSTED
    assert f.kernel_refusal_code("SOMETHING_ELSE") is c.PlanFindingCodeV1.DERIVE_FELL_BACK


def test_finding_ids_are_stable_and_keyed_by_the_rule_details():
    def mismatch(rule: str, found: str = "M6") -> c.FindingV1:
        return f.make_finding(c.PlanFindingCodeV1.INTERFACE_MISMATCH, ["pst-1", "mnt-1"], check=c.FindingCheckV1.PLAN_INTERFACES,
                              source=c.FindingSourceV1.CHECK, interface=c.ChainLinkKindV1.MOUNT_ON_POST,
                              details={"rule": rule, "expected": "M4", "found": found})

    thread = mismatch("THREAD")
    assert thread.item_ids == ("mnt-1", "pst-1") and thread.severity is c.FindingSeverityV1.ERROR
    assert thread.finding_id == mismatch("THREAD", found="8-32").finding_id != mismatch("SIZE").finding_id
    assert mismatch("THICKNESS_RANGE_UNKNOWN").severity is c.FindingSeverityV1.UNKNOWN
    with pytest.raises(ValueError, match="details.rule is one of"):
        mismatch("COLOUR")
    defaulted = f.make_finding(c.PlanFindingCodeV1.MOUNT_NOT_FOUND, ["opt-1"], check=c.FindingCheckV1.DERIVE,
                               source=c.FindingSourceV1.DERIVE)
    assert defaulted.interface is c.ChainLinkKindV1.OPTIC_IN_MOUNT
    with pytest.raises(ValueError, match="names 1 item"):
        f.make_finding(c.PlanFindingCodeV1.MOUNT_NOT_FOUND, check=c.FindingCheckV1.DERIVE, source=c.FindingSourceV1.DERIVE)
    with pytest.raises(ValueError, match="source DERIVE exactly for check DERIVE"):
        f.make_finding(c.PlanFindingCodeV1.MOUNT_NOT_FOUND, ["opt-1"], check=c.FindingCheckV1.DERIVE, source=c.FindingSourceV1.CHECK)
    with pytest.raises(ValueError, match="names the missing data"):
        f.make_finding(c.PlanFindingCodeV1.MISSING_MECHANICAL_DATA, ["mnt-1"], check=c.FindingCheckV1.PLAN_INTERFACES,
                       source=c.FindingSourceV1.CHECK)
    missing = f.make_finding(c.PlanFindingCodeV1.MISSING_MECHANICAL_DATA, ["mnt-1"], check=c.FindingCheckV1.PLAN_INTERFACES,
                             source=c.FindingSourceV1.CHECK, missing_data=["OS-MECH-TEST-MOUNT-25:holds.thickness_max_mm"])
    assert missing.severity is c.FindingSeverityV1.UNKNOWN


def test_finding_details_are_bounded():
    with pytest.raises(ValidationError, match="nest deeper"):
        f.make_finding(c.PlanFindingCodeV1.DERIVE_FELL_BACK, check=c.FindingCheckV1.DERIVE, source=c.FindingSourceV1.DERIVE,
                       details={"a": {"b": {"c": {"d": {"e": 1}}}}})
    with pytest.raises(ValidationError, match="exceed"):
        f.make_finding(c.PlanFindingCodeV1.DERIVE_FELL_BACK, check=c.FindingCheckV1.DERIVE, source=c.FindingSourceV1.DERIVE,
                       details={"text": "x" * c.FINDING_DETAILS_MAX_BYTES})


def test_recorded_findings_are_checked_against_their_rules_and_the_status():
    finding = f.make_finding(c.PlanFindingCodeV1.UNRESOLVED_SUPPLY, ["cmp-1"], check=c.FindingCheckV1.PLAN_SUPPLY,
                             source=c.FindingSourceV1.CHECK)
    revision = build(findings=[finding])
    assert revision.status.state is c.PlanStateV1.READY_WITH_WARNINGS and revision.status.counts.warning == 1
    payload = revision.payload()
    payload["findings"][0]["message"] = "Something else."
    with pytest.raises(ValidationError, match="message is the code's fixed message"):
        c.AssemblyPlanRevisionV1.model_validate(payload)
    payload = revision.payload()
    payload["findings"][0]["item_ids"] = ["mnt-9"]
    payload["findings"][0]["finding_id"] = f.finding_id(c.PlanFindingCodeV1.UNRESOLVED_SUPPLY, ["mnt-9"], None)
    with pytest.raises(ValidationError, match="names an item the revision does not have"):
        c.AssemblyPlanRevisionV1.model_validate(payload)
    payload = revision.payload()
    payload["status"] = _status().model_dump(mode="json", exclude_none=True)
    with pytest.raises(ValidationError, match="status.counts must count the recorded findings"):
        c.AssemblyPlanRevisionV1.model_validate(payload)
    with pytest.raises(ValidationError, match="does not follow from the counts"):
        _status(state="READY", counts={"error": 1, "unknown": 0, "warning": 0, "info": 0})


def test_status_state_order():
    def state(**counts) -> c.PlanStateV1:
        return c.plan_state(c.FindingCountsV1(**{"error": 0, "unknown": 0, "warning": 0, "info": 0, **counts}))

    assert [state(error=1, unknown=1), state(unknown=1, warning=1), state(warning=1, info=1), state(info=1)] == [
        c.PlanStateV1.HAS_ERRORS, c.PlanStateV1.INCOMPLETE, c.PlanStateV1.READY_WITH_WARNINGS, c.PlanStateV1.READY]


def _issue(code: AssemblyIssueCodeV1, part_refs, **extra) -> AssemblyIssueV1:
    rule = ISSUE_RULES[code]
    return AssemblyIssueV1(code=code, check=rule.check, severity=rule.severity, part_refs=tuple(sorted(part_refs)),
                           message=rule.message, **extra)


def test_layout_issues_become_findings():
    items = {"mnt-1": "mnt-1", "pst-1": "pst-1", "hld-1": "hld-1"}
    finding = f.finding_from_layout_issue(_issue(AssemblyIssueCodeV1.PART_INTERFERENCE, ["mnt-1", "hld-1"]), item_of_part=items,
                                          conservative_items=["mnt-1"])
    assert (finding.code, finding.severity, finding.item_ids, finding.basis) == (
        AssemblyIssueCodeV1.PART_INTERFERENCE, c.FindingSeverityV1.ERROR, ("hld-1", "mnt-1"), c.FindingBasisV1.CONSERVATIVE_PROFILE)
    mate = f.finding_from_layout_issue(_issue(AssemblyIssueCodeV1.ENGAGEMENT_NOT_EVALUATED, ["pst-1", "hld-1"],
                                              mate_ref="mate-pst-1"), item_of_part=items,
                                       link_kind_of_mate={"mate-pst-1": c.ChainLinkKindV1.POST_IN_HOLDER})
    assert (mate.severity, mate.interface, mate.basis) == (c.FindingSeverityV1.UNKNOWN, c.ChainLinkKindV1.POST_IN_HOLDER,
                                                           c.FindingBasisV1.NOT_EVALUATED)
    intrusion = f.finding_from_layout_issue(_issue(AssemblyIssueCodeV1.RESERVED_SPACE_INTRUSION, ["hld-1"], design_instance_id="laser"),
                                            item_of_part=items, item_of_instance={"laser": "cmp-1"})
    assert intrusion.item_ids == ("cmp-1", "hld-1") and intrusion.check is c.FindingCheckV1.LAYOUT_VALIDATION
    info = f.finding_from_layout_issue(_issue(AssemblyIssueCodeV1.CUSTOMER_SUPPLIED_COMPONENT, [], design_instance_id="laser"),
                                       item_of_part=items, item_of_instance={"laser": "cmp-1"})
    assert info.severity is c.FindingSeverityV1.INFO and info.item_ids == ("cmp-1",)
    revision = build(findings=[finding, mate, intrusion, info])
    assert [x.severity for x in revision.findings] == ["ERROR", "ERROR", "UNKNOWN", "INFO"]


def test_merge_carry_and_require_check_rules():
    derive = f.make_finding(c.PlanFindingCodeV1.NO_FIXING_HOLE, ["hld-1"], check=c.FindingCheckV1.DERIVE,
                            source=c.FindingSourceV1.DERIVE)
    layout = f.make_finding(c.PlanFindingCodeV1.NO_FIXING_HOLE, ["hld-1"], check=c.FindingCheckV1.PLAN_LAYOUT,
                            source=c.FindingSourceV1.CHECK)
    plan_wide = f.make_finding(c.PlanFindingCodeV1.DERIVE_FELL_BACK, check=c.FindingCheckV1.DERIVE, source=c.FindingSourceV1.DERIVE)
    assert derive.finding_id == layout.finding_id
    assert f.merge_findings([derive, plan_wide], [layout]) == f.sort_findings([layout, plan_wide])
    assert f.carry_derive_findings([derive, plan_wide, layout], touched_items=["hld-1"]) == (plan_wide,)
    assert f.carry_derive_findings([derive], touched_items=[], retired_items=["hld-1"]) == ()
    stale = f.make_finding(c.PlanFindingCodeV1.OPTICS_RESULTS_STALE, ["opt-1"], check=c.FindingCheckV1.PLAN_OPTICS,
                           source=c.FindingSourceV1.CHECK)
    assert f.blocking_new_findings([plan_wide], [plan_wide, layout, stale]) == (layout,)
    assert f.findings_diff([plan_wide, derive], [plan_wide, stale]) == ((stale.finding_id,), (derive.finding_id,))


# ------------------------------------------------------------------------------------------------ edit operations


OPS = TypeAdapter(c.AssemblyPlanOpV1)


@pytest.mark.parametrize("op", [
    {"op": "move_optic", "item_id": "opt-1", "delta_design_mm": [1, 0, 0]},
    {"op": "move_optic", "item_id": "opt-1", "position_design_mm": [1.0, 2.0, 3.0], "quaternion_wxyz": [1, 0, 0, 0],
     "refix": "KEEP_HOLE"},
    {"op": "adjust", "item_id": "opt-1", "range_id": "rng-opt-1-post-height", "delta": 2.5},
    {"op": "set_hardware", "item_id": "mnt-1", "opensci_sku": "USER-MECH-MY-MOUNT"},
    {"op": "set_fixation", "item_id": "hld-1", "hole": [3, 4], "base_yaw_deg": 90.0},
    {"op": "set_board", "customer_table": {"size_x_mm": 900.0, "size_y_mm": 600.0, "pitch_mm": 25.0, "edge_offset_mm": 12.5,
                                           "thread": "M6"}, "recentre": True},
    {"op": "set_supply", "item_id": "opt-1", "supply": "CUSTOMER_OWNED"},
    {"op": "set_purchase", "item_id": "opt-1", "selected": True, "quantity": 2, "spare_units": 1},
    {"op": "set_mounting", "item_id": "opt-1", "mounting": "ANCHOR"},
    {"op": "rederive_chain", "item_id": "opt-1", "keep": ["hld-1", "mnt-1"]},
    {"op": "pin", "item_id": "mnt-1", "pinned": True},
    {"op": "rebind_catalogs"},
    {"op": "declare_envelope", "label": "Camera", "envelope": {"shape": "BOX", "min_mm": [0, 0, 0], "max_mm": [20, 20, 40]}},
    {"op": "declare_envelope", "item_id": "env-4", "label": "Tube", "envelope": {"shape": "CYLINDER", "diameter_mm": 20,
                                                                                 "z_min_mm": 0, "z_max_mm": 80, "axis": "x"}},
    {"op": "remove_envelope", "item_id": "env-1"},
    {"op": "revert_to", "revision": 1},
])
def test_every_op_parses(op):
    parsed = OPS.validate_python(op)
    assert parsed.op == op["op"]
    assert OPS.validate_python(parsed.model_dump(mode="json", exclude_none=True)) == parsed


@pytest.mark.parametrize(("op", "fragment"), [
    ({"op": "move_optic", "item_id": "opt-1"}, "exactly one of delta_design_mm"),
    ({"op": "move_optic", "item_id": "opt-1", "delta_design_mm": [0, 0, 0], "position_design_mm": [0, 0, 0]}, "exactly one"),
    ({"op": "set_board", "recentre": True}, "exactly one of opensci_sku / customer_table"),
    ({"op": "set_hardware", "item_id": "mnt-1", "opensci_sku": "OS-OPT-TEST-LENS-25"}, "String should match pattern"),
    ({"op": "set_purchase", "item_id": "opt-1", "selected": False, "quantity": 1}, "an unselected item buys nothing"),
    ({"op": "set_purchase", "item_id": "opt-1", "selected": "yes"}, "Input should be a valid boolean"),
    ({"op": "adjust", "item_id": "opt-1", "range_id": "rng-opt-1-post-height", "delta": "2"}, "Input should be a valid number"),
    ({"op": "declare_envelope", "item_id": "cmp-2", "label": "X", "envelope": {"shape": "BOX", "min_mm": [0, 0, 0],
                                                                               "max_mm": [1, 1, 1]}}, "is 'env-<n>'"),
    ({"op": "rederive_chain", "item_id": "opt-1", "keep": ["mnt-1", "hld-1"]}, "keep must be unique and sorted"),
    ({"op": "explode"}, "does not match any of the expected tags"),
    ({"op": "pin", "item_id": "mnt-1", "pinned": True, "extra": 1}, "Extra inputs are not permitted"),
])
def test_invalid_ops_are_refused(op, fragment):
    with pytest.raises(ValidationError, match=fragment):
        OPS.validate_python(op)


def test_the_request_digest_covers_the_ops_only():
    ops = [{"op": "move_optic", "item_id": "opt-1", "delta_design_mm": [1.0, 0.0, 0.0]}]
    plain = c.EditRequestV1.model_validate({"ops": ops})
    named = c.EditRequestV1.model_validate({"ops": ops, "op_id": "move-lens-1", "expected_base": "e" * 64, "require_check": True})
    assert plain.request_digest() == named.request_digest() == osc1_digest(
        {"ops": [{"op": "move_optic", "item_id": "opt-1", "delta_design_mm": [1.0, 0.0, 0.0], "refix": "NEAREST_HOLE"}]})
    with pytest.raises(ValidationError):
        c.EditRequestV1.model_validate({"ops": []})
    with pytest.raises(ValidationError):
        c.EditRequestV1.model_validate({"ops": ops, "op_id": "has space"})


# ------------------------------------------------------------------------------------------------ history and errors


def _summary(revision: c.AssemblyPlanRevisionV1, **over) -> dict:
    return {"ok": True, "plan_dir": "plan", "plan_id": revision.plan_id, "revision": revision.revision,
            "content_digest": revision.content_digest, "op_id": "auto-" + "0" * 32, "project_hash": "a" * 64,
            "status": revision.status.model_dump(mode="json", exclude_none=True), **over}


def test_history_lines_have_no_timestamp_and_a_consistent_base():
    revision = build()
    line = {"seq": 1, "op_id": "auto-" + "0" * 32, "kind": "DERIVE", "result_revision": 1, "result_digest": revision.content_digest,
            "project_hash": "a" * 64, "result": _summary(revision), "engine_version": "0.15.0.dev0", "engine_build_id": "b" * 16}
    assert c.HistoryLineV1.model_validate(line).kind is c.PlanOriginV1.DERIVE
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        c.HistoryLineV1.model_validate({**line, "at": "2026-11-28T00:00:00Z"})
    with pytest.raises(ValidationError, match="both stated or both absent"):
        c.HistoryLineV1.model_validate({**line, "base_revision": 1})
    with pytest.raises(ValidationError, match="above its base"):
        c.HistoryLineV1.model_validate({**line, "kind": "EDIT", "base_revision": 1, "base_digest": revision.content_digest})
    result = c.PlanCallResultV1.model_validate({**_summary(revision), "findings": []})
    assert result.findings == ()


def test_plan_errors_carry_code_details_and_problems():
    error = c.AssemblyPlanError(c.AssemblyPlanErrorCode.BASE_CHANGED, "the plan moved", {"head": "e" * 64}, ["one"])
    assert error.as_dict() == {"code": "BASE_CHANGED", "message": "the plan moved", "details": {"head": "e" * 64},
                               "problems": ["one"]}
    assert str(error) == "the plan moved"
