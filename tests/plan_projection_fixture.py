"""A synthetic, geometrically consistent local assembly plan for the local-output tests (projection, detail, drawing,
viewer): the ``focus`` example Project on the fixture optical catalog, one lens on one support chain of the synthetic
``OS-MECH-TEST-*`` mechanical fixture, the customer's own light source, and optional extras (a declared envelope, an
unresolved chain, a customer-owned lens, the customer's own table, a USER-MECH post).

Hand-built, not derived (the derivation kernel is another lane's): every pose is computed here from the fixture
facts and the part frames of ``opensci.mechanical_part_frames.v1``, so the chain closes exactly:

* lens at lab (135.5, 87.5, 125), optical axis along lab +X (the Project's lens frame carried by the plan frame);
* XY lens mount (centre height 20) with its seat on the lens's seat datum (the lens's top face, z = +2 in its frame);
* post 75 mm on the mount, inserted 20 mm into a 50 mm holder standing on hole (6, 3) of a 300 x 150 breadboard whose
  top is at lab z = 0, the hole 25 mm along the holder's fork.
"""

from __future__ import annotations

import copy
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from catalog_store_helpers import FIXTURE_CATALOG  # noqa: E402
from mechanical_fixture import OPENSCI_FIXTURE, USER_FIXTURE, fixture_path  # noqa: E402

from os_optics._vendor.shared_contracts.common.hashes import osc1_digest  # noqa: E402
from os_optics.assembly_plan import contract as c  # noqa: E402
from os_optics.assembly_plan.glb import Primitive  # noqa: E402
from os_optics.assembly_plan.mechanical.model import MechanicalSources, load_mechanical_document  # noqa: E402
from os_optics.assembly_plan.projection import BeamAxisInput, PlanProjectionInputs  # noqa: E402
from os_optics.local import load_public_catalog  # noqa: E402
from os_optics.local.example import example_project_of_kind  # noqa: E402

LENS_AT = (135.5, 87.5, 125.0)
LENS_Q = (0.5, 0.5, 0.5, 0.5)  # local +Z (optical axis) -> lab +X, local +X -> lab +Y, local +Y -> lab +Z
IDENTITY = (1.0, 0.0, 0.0, 0.0)
LENS_HALF_THICKNESS = 2.0
LENS_RADIUS = 12.7
MOUNT_CENTRE = 20.0
POST_LENGTH = 75.0
HOLDER_HEIGHT = 50.0
SLOT_OFFSET = 25.0
BOARD_THICKNESS = 12.7
HOLE = (6, 3)
MOUNT_SKU = "OS-MECH-TEST-LENS-MOUNT-XY-D25"
POST_SKU = "OS-MECH-TEST-POST-D12P7-L75"
HOLDER_SKU = "OS-MECH-TEST-HOLDER-D12P7-H50"
BOARD_SKU = "OS-MECH-TEST-BREADBOARD-300X150-M6G25"
USER_POST_SKU = "USER-MECH-TEST-POST-D12P7-L60"
LENS_SKU = "OS-OPT-TEST-LENS-PCX-F100"
CHECKED = c.CheckedWithV1(checker_version="0.15.0.dev0", kernel_version="1.0.0", plan_checks_version="1.0.0")


@dataclass(frozen=True)
class LensMechanics:
    """The lens's design geometry as the optic-mechanics derivation gives it (a CYLINDER about local +Z, GLASS)."""

    visual: tuple[Primitive, ...]
    color_rgb: tuple[float, float, float] = (0.62, 0.82, 0.90)


LENS = LensMechanics(visual=(Primitive(kind="CYLINDER", finish="GLASS", base_mm=(0.0, 0.0, -LENS_HALF_THICKNESS), axis="z",
                                       length_mm=2 * LENS_HALF_THICKNESS, radius_mm=LENS_RADIUS),))


def pose(position, quaternion=IDENTITY) -> dict:
    return {"position_mm": [float(v) for v in position], "quaternion_wxyz": [float(v) for v in quaternion]}


def project():
    return example_project_of_kind("focus", load_public_catalog(FIXTURE_CATALOG))


def mechanical(*, user: bool = False) -> MechanicalSources:
    docs = [load_mechanical_document(fixture_path(OPENSCI_FIXTURE).read_bytes())]
    if user:
        docs.append(load_mechanical_document(fixture_path(USER_FIXTURE).read_bytes()))
    return MechanicalSources(tuple(docs))


def _purchase(selected: bool) -> dict:
    return {"selected": selected, "quantity": 1 if selected else 0, "spare_units": 0}


def _identity(kind: str, sku: str | None, name: str, zh: str | None = None) -> dict:
    out = {"kind": kind, "display_name": name}
    if sku is not None:
        out["opensci_sku"] = sku
    if zh is not None:
        out["display_name_zh_cn"] = zh
    return out


def _support(item_id: str, parent: str, relation: str) -> dict:
    return {"parent_item_id": parent, "relation": relation, "link_id": f"lnk-{item_id}-{parent}"}


def _link(kind: str, a: str, b: str, datum_a: str, datum_b: str, designation: str, **interface) -> dict:
    return {"link_id": f"lnk-{a}-{b}", "kind": kind, "a_item_id": a, "b_item_id": b, "datum_a": datum_a, "datum_b": datum_b,
            "interface": {"designation": designation, **interface}, "b_in_a": pose((0.0, 0.0, 0.0))}


def content(prj=None, *, source_envelope: bool = False) -> dict:
    """The decisions of the reference plan revision (one chain, the customer's light source)."""

    prj = prj or project()
    x, y, z = LENS_AT
    mount_at = (x + LENS_HALF_THICKNESS, y, z - MOUNT_CENTRE)
    post_at = (mount_at[0], y, mount_at[2] - POST_LENGTH)
    holder_at = (mount_at[0], y, 0.0)
    depth = HOLDER_HEIGHT - post_at[2]
    frame_t = LENS_AT  # the lens is the design origin
    source = next(comp for comp in prj.components if comp.instance_id == "SRC")
    sx = source.nominal_pose.position_mm
    lens = {"item_id": "opt-1", "role": "OPTIC", "identity": _identity("OS_OPT", LENS_SKU, "Test plano-convex lens f 100 mm"),
            "design_instance_id": "L1", "mounting": "CHAIN", "supply": "OPENSCI", "purchase": _purchase(True),
            "placement": {"pose_in_lab": pose(LENS_AT, LENS_Q), "assembly_order": 5,
                          "support": _support("opt-1", "mnt-1", "SEATED_IN"),
                          "ranges": [
                              {"range_id": "rng-opt-1-mount-translation-y", "kind": "MOUNT_TRANSLATION_Y", "axis_lab": [0.0, 1.0, 0.0],
                               "min": -1.0, "max": 1.0, "unit": "mm", "provided_by": "mnt-1", "moves": ["opt-1"],
                               "basis": "RESOLVED", "adjustment": "FINE_ADJUSTER", "delivers": ["Tx"]},
                              {"range_id": "rng-opt-1-mount-translation-z", "kind": "MOUNT_TRANSLATION_Z", "axis_lab": [0.0, 0.0, 1.0],
                               "min": -1.0, "max": 1.0, "unit": "mm", "provided_by": "mnt-1", "moves": ["opt-1"],
                               "basis": "RESOLVED", "adjustment": "FINE_ADJUSTER", "delivers": ["Ty"]},
                              {"range_id": "rng-opt-1-slot-travel", "kind": "SLOT_TRAVEL", "axis_lab": [1.0, 0.0, 0.0],
                               "min": -10.0, "max": 10.0, "unit": "mm", "provided_by": "hld-1",
                               "moves": ["hld-1", "mnt-1", "opt-1", "pst-1"], "basis": "RESOLVED",
                               "adjustment": "COARSE_REPOSITION", "delivers": ["Tz"]}],
                          "dofs": [
                              {"axis": "Tx", "requested_control": "MANUAL", "status": "DELIVERED", "mechanism": "TRANSLATION_MOUNT",
                               "range_id": "rng-opt-1-mount-translation-y"},
                              {"axis": "Ty", "requested_control": "MANUAL", "status": "DELIVERED", "mechanism": "TRANSLATION_MOUNT",
                               "range_id": "rng-opt-1-mount-translation-z"},
                              {"axis": "Tz", "requested_control": "NUMERICAL", "status": "DELIVERED_AS_MANUAL_FALLBACK",
                               "mechanism": "BASE_SLOT_TRAVEL", "range_id": "rng-opt-1-slot-travel"}]}}
    items = [
        {"item_id": "brd-1", "role": "BREADBOARD",
         "identity": _identity("OS_MECH", BOARD_SKU, "Test breadboard 300 x 150 mm, M6, 25 mm grid"),
         "supply": "OPENSCI", "purchase": _purchase(True),
         "placement": {"pose_in_lab": pose((0.0, 0.0, -BOARD_THICKNESS)), "assembly_order": 1}},
        {"item_id": "cmp-1", "role": "COMPONENT", "identity": _identity("CUSTOMER_PART", None, "Your light source"),
         "design_instance_id": "SRC", "supply": "CUSTOMER_OWNED", "supply_reason": "CUSTOMER_LIGHT_SOURCE",
         "purchase": _purchase(False),
         "placement": {"pose_in_lab": pose((frame_t[0] + sx[0], frame_t[1] + sx[1], frame_t[2] + sx[2]), LENS_Q), "assembly_order": 1}},
        {"item_id": "hld-1", "role": "POST_HOLDER", "identity": _identity("OS_MECH", HOLDER_SKU, "Test post holder 12.7 mm, 50 mm high"),
         "supply": "OPENSCI", "purchase": _purchase(True),
         "placement": {"pose_in_lab": pose(holder_at), "assembly_order": 2, "support": _support("hld-1", "brd-1", "STANDS_ON"),
                       "settings": [{"kind": "BASE_YAW", "value": "0", "unit": "deg", "basis": "RESOLVED"},
                                    {"kind": "FIXATION_THREAD", "value": "M6x1.0", "unit": "NONE", "basis": "RESOLVED"}],
                       "fixation": {"board_holes": [list(HOLE)], "thread": "M6", "slot_offset_mm": SLOT_OFFSET}}},
        {"item_id": "mnt-1", "role": "MOUNT", "identity": _identity("OS_MECH", MOUNT_SKU, "Test XY translation lens mount 25.4 mm"),
         "supply": "OPENSCI", "purchase": _purchase(True),
         "placement": {"pose_in_lab": pose(mount_at), "assembly_order": 4, "support": _support("mnt-1", "pst-1", "MOUNTED_ON"),
                       "settings": [{"kind": "MOUNT_ORIENTATION_YAW", "value": "0", "unit": "deg", "basis": "RESOLVED"}]}},
        lens,
        {"item_id": "pst-1", "role": "POST", "identity": _identity("OS_MECH", POST_SKU, "Test post 12.7 mm, 75 mm long"),
         "supply": "OPENSCI", "purchase": _purchase(True),
         "placement": {"pose_in_lab": pose(post_at), "assembly_order": 3, "support": _support("pst-1", "hld-1", "INSERTED_INTO"),
                       "settings": [{"kind": "INSERTION_DEPTH", "value": f"{depth:g}", "unit": "mm", "basis": "RESOLVED"}]}},
    ]
    if source_envelope:
        items[1]["envelope"] = {"shape": "CYLINDER", "basis": "PROJECT_DECLARED_GEOMETRY", "diameter_mm": 20.0,
                                "z_min_mm": -30.0, "z_max_mm": 0.0}
    links = [
        _link("HOLDER_ON_BOARD", "hld-1", "brd-1", "bottom", "board_top", "M6x1.0-GRID25", thread="M6"),
        _link("MOUNT_ON_POST", "mnt-1", "pst-1", "support", "top", "M4x0.7", thread="M4"),
        _link("OPTIC_IN_MOUNT", "opt-1", "mnt-1", "seat", "seat", "OD25.4-SEAT", diameter_mm=25.4, depth_mm=4.0),
        _link("POST_IN_HOLDER", "pst-1", "hld-1", "bottom", "bore_top", "OD12.7-POST", diameter_mm=12.7, depth_mm=depth),
    ]
    mech = mechanical()
    return {"schema": "os-optics.assembly_plan", "schema_version": "1.0", "plan_id": "ap-0123456789abcdef", "revision": 1,
            "origin": "DERIVE",
            "bindings": {"project": {"project_hash": {"profile": "osc1", "digest": prj.content_hash(), "subject": "project"},
                                     "design_intent": "LAB_ASSEMBLY"},
                         "optical_catalog": {"catalog_version": "2026-01-01.1"},
                         "mechanical": [d.ref().model_dump(mode="json") for d in mech.documents]},
            "frame": {"yaw_deg": 0.0, "translation_mm": list(frame_t), "basis": "DERIVED"},
            "board_item_id": "brd-1", "items": items, "links": links,
            "id_counters": {"opt": 1, "cmp": 1, "mnt": 1, "pst": 1, "hld": 1, "brd": 1, "env": 0},
            "derive_options": {"budget": {"max_chain_placements": 1000, "max_geometry_checks": 100000}}}


def item(data: dict, item_id: str) -> dict:
    return next(i for i in data["items"] if i["item_id"] == item_id)


def with_declared_envelope(data: dict) -> dict:
    """A customer camera known only by its envelope (``declare_envelope``): a 20 x 20 x 40 mm box beside the post."""

    data = copy.deepcopy(data)
    x, y, _ = LENS_AT
    data["items"].append({"item_id": "env-1", "role": "DECLARED_ENVELOPE", "identity": _identity("CUSTOMER_PART", None, "Customer camera"),
                          "supply": "CUSTOMER_OWNED", "supply_reason": "NOT_IN_CATALOG", "purchase": _purchase(False),
                          "placement": {"pose_in_lab": pose((x + 60.0, y + 40.0, 0.0)), "assembly_order": 1},
                          "envelope": {"shape": "BOX", "basis": "USER_DECLARED", "min_mm": [-10.0, -10.0, 0.0],
                                       "max_mm": [10.0, 10.0, 40.0]}})
    data["items"].sort(key=lambda i: i["item_id"])
    data["id_counters"]["env"] = 1
    return data


def with_unresolved_chain(data: dict) -> dict:
    """The lens's post and holder unresolved (identity NONE; no compatible hardware), its mount resolved."""

    data = copy.deepcopy(data)
    for item_id in ("pst-1", "hld-1"):
        entry = item(data, item_id)
        entry.update({"identity": {"kind": "NONE", "display_name": "No compatible hardware"}, "supply": "UNRESOLVED",
                      "supply_reason": "NO_COMPATIBLE_HARDWARE", "purchase": _purchase(False)})
        entry.pop("placement")
    item(data, "mnt-1")["placement"].pop("support")
    lens = item(data, "opt-1")
    lens["placement"]["ranges"] = []
    lens["placement"]["dofs"] = []
    for link in data["links"]:
        if link["kind"] in ("MOUNT_ON_POST", "POST_IN_HOLDER", "HOLDER_ON_BOARD"):
            link.pop("interface")
            link.pop("b_in_a")
    return data


def with_user_post(data: dict) -> dict:
    data = copy.deepcopy(data)
    entry = item(data, "pst-1")
    entry.update({"identity": _identity("USER_MECH", USER_POST_SKU, "Your post 12.7 mm, 60 mm long"), "supply": "CUSTOMER_OWNED",
                  "supply_reason": "USER_HARDWARE", "purchase": _purchase(False)})
    return data


def with_customer_owned_lens(data: dict) -> dict:
    data = copy.deepcopy(data)
    entry = item(data, "opt-1")
    entry.update({"supply": "CUSTOMER_OWNED", "supply_reason": "CUSTOMER_OWNED", "purchase": _purchase(False)})
    return data


def revision(data: dict | None = None) -> c.AssemblyPlanRevisionV1:
    status = c.PlanStatusV1.model_validate({"state": "READY", "counts": {"error": 0, "unknown": 0, "warning": 0, "info": 0},
                                            "optics_results": "NOT_RUN", "evidence": {},
                                            "purchase": {"selected_lines": 5, "excluded_items": 1, "unresolved_items": 0}})
    return c.build_revision(content() if data is None else data, findings=[], status=status, checked_with=CHECKED)


#: Grouping order of a design BOM line (DESIGN 1.4): OS_OPT, OS_MECH by role, then the customer's identities.
_ROLE_ORDER = {"MOUNT": 0, "POST": 1, "POST_HOLDER": 2, "BREADBOARD": 3}
_KIND_ORDER = {"OS_OPT": 0, "OS_MECH": 1, "USER_MECH": 2, "CUSTOMER_TABLE": 3, "CUSTOMER_PART": 4, "NONE": 5}


def design_bom(rev: c.AssemblyPlanRevisionV1) -> dict[str, Any]:
    """The design BOM of DESIGN 1.4, built here for the tests (the plan's own BOM module is another lane's)."""

    groups: dict[tuple, list] = {}
    for entry in rev.items:
        key = (entry.supply.value, entry.identity.kind.value,
               entry.identity.opensci_sku or entry.identity.custom_part_key or entry.item_id, entry.role.value)
        groups.setdefault(key, []).append(entry)

    def order(key: tuple) -> tuple:
        _, kind, ident, role = key
        return (_KIND_ORDER[kind], _ROLE_ORDER.get(role, 0) if kind == "OS_MECH" else 0, ident)

    lines = []
    for n, key in enumerate(sorted(groups, key=order), start=1):
        members = groups[key]
        first = members[0]
        line = {"line_id": f"D{n}", "role": first.role.value, "identity_kind": first.identity.kind.value,
                "opensci_sku": first.identity.opensci_sku, "display_name": first.identity.display_name,
                "display_name_zh_cn": first.identity.display_name_zh_cn, "item_ids": sorted(m.item_id for m in members),
                "quantity": len(members), "supply": first.supply.value,
                "supply_reason": first.supply_reason.value if first.supply_reason else None,
                "purchase": {"quantity": sum(m.purchase.quantity for m in members),
                             "spare_units": sum(m.purchase.spare_units for m in members)}}
        lines.append(line)
    body = {"schema": "os-optics.design_bom", "schema_version": "1.0",
            "plan": {"plan_id": rev.plan_id, "revision": rev.revision, "content_digest": rev.content_digest},
            "project_hash": rev.bindings.project.project_hash.digest, "lines": lines}
    return {**body, "bom_digest": osc1_digest(body)}


def context(rev: c.AssemblyPlanRevisionV1, *, prj=None, user: bool = False, beam: bool = True) -> PlanProjectionInputs:
    prj = prj or project()
    axes = ()
    if beam:
        plane = next(o for o in prj.observations if o.observation_id == "focal_plane")
        end = prj.observation_design_pose(plane).position_mm
        source = next(comp for comp in prj.components if comp.instance_id == "SRC").nominal_pose.position_mm
        axes = (BeamAxisInput(path_key="beam-1", points_design_mm=(tuple(source), (0.0, 0.0, 0.0), tuple(end)),
                              through=("SRC", "L1", "focal_plane")),)
    return PlanProjectionInputs(project=prj, mechanical=mechanical(user=user), design_bom=design_bom(rev),
                                optics={"opt-1": LENS}, beam_axes=axes)
