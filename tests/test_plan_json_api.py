"""The JSON API methods of local assembly plans (category ``assembly``, stateless)."""

from __future__ import annotations

import json

import derive_helpers as h
from mechanical_fixture import fixture_path

from os_optics.local import json_api

METHODS = ("derive_assembly_plan", "check_assembly_plan", "apply_assembly_plan_ops", "assembly_plan_preview", "assembly_plan_drawing",
           "assembly_plan_bom", "mechanical_catalog")


def _inputs():
    cat = h.optical_catalog()
    return cat, h.project("focus", cat).payload(), [json.loads(fixture_path().read_bytes())]


def test_the_plan_methods_are_registered_assembly_methods():
    described = json_api.capabilities()["json_api_methods"]
    for name in METHODS:
        assert described[name]["category"] == "assembly", name
    assert described["derive_assembly_plan"]["parameters"] == ["project", "mechanical", "options", "run"]
    assert described["apply_assembly_plan_ops"]["required"] == ["plan", "project", "ops", "mechanical"]


def test_derive_check_edit_bom_preview_and_drawing():
    cat, project, mechanical = _inputs()
    derived = json_api.derive_assembly_plan(project, mechanical, catalog=cat)
    assert derived["ok"] and derived["plan"]["revision"] == 1 and derived["status"]["state"] == "READY_WITH_WARNINGS"
    assert derived["layout_validation"]["status"] == "WARNING"
    plan = derived["plan"]
    checked = json_api.check_assembly_plan(plan, derived["project"], mechanical, catalog=cat)
    assert checked["ok"] and checked["status"] == derived["status"] and checked["findings"] == derived["findings"]
    edited = json_api.apply_assembly_plan_ops(plan, project, [{"op": "set_purchase", "item_id": "pst-1", "selected": True,
                                                               "spare_units": 1}], mechanical, catalog=cat)
    assert edited["ok"] and edited["plan"]["revision"] == 2 and edited["plan"]["parent_digest"] == plan["content_digest"]
    assert edited["op_results"][0]["changed_items"] == ["pst-1"] and edited["project_changed"] is False
    bom = json_api.assembly_plan_bom(edited["plan"], {"purchase": True})
    assert bom["ok"] and bom["bom"]["lines"][-1]["purpose"] == "SPARE" and bom["report"]["units"] == 6
    design = json_api.assembly_plan_bom(plan)
    assert design["ok"] and design["bom"]["schema"] == "os-optics.design_bom" and design["report"] is None
    preview = json_api.assembly_plan_preview(plan, project, mechanical, catalog=cat)
    assert preview["ok"] and preview["manifest"]["schema_version"] == "3.1" and "manifest.json" in preview["files"]
    drawing = json_api.assembly_plan_drawing(plan, project, mechanical, {"locale": "zh-CN"}, catalog=cat)
    assert drawing["ok"] and drawing["status"]["delivery_ready"] and "assembly_drawing.svg" in drawing["files"]


def test_the_plan_methods_refuse_bad_inputs():
    cat, project, mechanical = _inputs()
    assert json_api.derive_assembly_plan(project, [], catalog=cat)["error"]["code"] == "MECHANICAL_CATALOG_NOT_AVAILABLE"
    assert json_api.assembly_plan_bom({"schema": "nope"})["error"]["code"] == "INPUT_INVALID"
    plan = json_api.derive_assembly_plan(project, mechanical, catalog=cat)["plan"]
    refused = json_api.apply_assembly_plan_ops(plan, project, [{"op": "pin", "item_id": "nope-1", "pinned": True}], mechanical, catalog=cat)
    assert refused["ok"] is False and refused["error"]["code"] == "PLAN_OP_INVALID"
    assert json_api.assembly_plan_drawing(plan, project, mechanical, {"colour": "red"}, catalog=cat)["error"]["code"] == "INPUT_INVALID"


def test_mechanical_catalog_lists_identities():
    _, _, mechanical = _inputs()
    reply = json_api.mechanical_catalog(mechanical, {"kind": "BREADBOARD"})
    assert reply["ok"] and reply["documents"][0]["publisher"] == "OPENSCI"
    assert reply["identities"] and all(i["kind"] == "BREADBOARD" for i in reply["identities"])


def test_call_method_returns_the_package_files_apart():
    cat, project, mechanical = _inputs()
    plan = json_api.derive_assembly_plan(project, mechanical, catalog=cat)["plan"]
    json_api.install_catalog(json.dumps(cat.payload() if hasattr(cat, "payload") else cat.model_dump(mode="json", by_alias=True)).encode())
    reply, files = json_api.call_method("assembly_plan_preview", {"plan": plan, "project": project, "mechanical": mechanical})
    assert reply["ok"] and "files" not in reply and files and "manifest.json" in files
