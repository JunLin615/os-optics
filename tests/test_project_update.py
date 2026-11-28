"""``update_project``: headless Project edit operations applied atomically and the check of the result reported, as
``local.project_ops``, ``json_api.update_project`` and ``os-optics project update``; with them ``project check`` and
``capabilities``.

Covers the reply (normalized Project, hashes, ``changed``, ``affected``, ``already_present``, ``check``, ``warnings``),
the three refusals (``PROJECT_OP_INVALID`` with op index and code, ``PROJECT_REJECTED`` for a document that is no Project
and, with ``require_check``, for a Project the check refuses, ``PROJECT_CHANGED`` with the current hash), retry semantics,
the ``transform`` operation (the composition transform on the bare Project) and the command line (atomic write, the input
left alone, exit 65 on a refusal)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from os_optics.cli import main
from os_optics.local import ProjectUpdateError, example_project, json_api, update_project
from os_optics.local.project_ops import PROJECT_OPS


def project() -> dict:
    return example_project().payload()


def lens_id(doc: dict) -> str:
    return next(c["instance_id"] for c in doc["components"] if c.get("product"))


def source_id(doc: dict) -> str:
    return next(c["instance_id"] for c in doc["components"] if c.get("source_model"))


def test_update_project_applies_checks_and_reports_what_changed():
    doc = project()
    before = copy.deepcopy(doc)
    lens = lens_id(doc)
    reply = json_api.update_project(doc, [{"op": "set_component_pose", "instance_id": lens, "position_mm": [1.0, 0.0, 0.0]},
                                          {"op": "set_target", "target_id": doc["targets"][0]["target_id"], "changes": {"tolerance_abs": 0.2}}])
    assert doc == before
    assert reply["ok"] is True and reply["changed"] is True
    checked = json_api.check_project(reply["project"])
    assert reply["project_hash"] == checked["project_hash"] and reply["engine_input_hash"] == checked["engine_input_hash"]
    assert reply["base_project_hash"] == example_project().content_hash()
    assert reply["affected"] == [{"kind": "COMPONENT", "id": lens, "change": "MODIFIED", "fields": ["nominal_pose"]},
                                 {"kind": "TARGET", "id": doc["targets"][0]["target_id"], "change": "MODIFIED", "fields": ["tolerance_abs"]}]
    assert reply["already_present"] == [] and reply["warnings"] == []
    assert reply["optical_solids"] == checked["optical_solids"] and reply["catalog_version"] == checked["catalog_version"]
    assert reply["check"] == {"ok": True, "problems": []}
    assert reply["compute_budget"] == checked["compute_budget"] and reply["numerical_settings"] == checked["numerical_settings"]
    json.dumps(reply, allow_nan=False)


def test_a_retried_list_of_absolute_operations_changes_nothing():
    doc = project()
    ops = [{"op": "set_source_model", "instance_id": source_id(doc), "changes": {"waist_radius_mm": 0.9}}]
    first = json_api.update_project(doc, ops)
    again = json_api.update_project(first["project"], ops)
    assert first["changed"] is True and again["changed"] is False and again["affected"] == []
    assert again["project_hash"] == first["project_hash"] == again["base_project_hash"]


def test_a_retried_add_is_already_present_and_a_different_item_under_its_id_is_refused():
    doc = project()
    plane = copy.deepcopy(doc["observations"][0]) | {"observation_id": "extra"}
    first = json_api.update_project(doc, [{"op": "add_observation", "observation": plane}])
    retry = json_api.update_project(first["project"], [{"op": "add_observation", "observation": plane}])
    assert first["changed"] is True and retry["changed"] is False
    assert retry["already_present"] == [{"op_index": 0, "op": "add_observation", "kind": "OBSERVATION", "id": "extra"}]
    moved = copy.deepcopy(plane)
    moved["pose"]["position_mm"][0] += 1.0
    refused = json_api.update_project(first["project"], [{"op": "add_observation", "observation": moved}])
    assert refused["error"] | {"message": None, "problems": None} == {"code": "PROJECT_OP_INVALID", "op_index": 0, "op_code": "DUPLICATE_ID",
                                                                       "message": None, "problems": None}


def test_expected_project_hash_guards_the_update():
    doc = project()
    current = example_project().content_hash()
    ops = [{"op": "set_component_pose", "instance_id": lens_id(doc), "position_mm": [2.0, 0.0, 0.0]}]
    assert json_api.update_project(doc, ops, {"expected_project_hash": current})["ok"] is True
    stale = json_api.update_project(doc, ops, {"expected_project_hash": "0" * 64})
    assert stale["ok"] is False and stale["error"]["code"] == "PROJECT_CHANGED"
    assert stale["error"]["project_hash"] == current and stale["error"]["expected_project_hash"] == "0" * 64
    assert stale["error"]["problems"][0]["code"] == "PROJECT_CHANGED"


def test_an_operation_that_cannot_be_applied_names_its_index_and_code():
    doc = project()
    reply = json_api.update_project(doc, [{"op": "set_component_pose", "instance_id": lens_id(doc), "position_mm": [1, 0, 0]},
                                          {"op": "remove_target", "target_id": "nope"}])
    assert reply == {"ok": False, "error": {"code": "PROJECT_OP_INVALID", "message": 'target "nope" does not exist', "op_index": 1,
                                            "op_code": "NOT_FOUND", "problems": [{"code": "NOT_FOUND", "message": 'target "nope" does not exist'}]}}
    for ops in ([], "set", None):
        assert json_api.update_project(doc, ops)["error"]["op_index"] == -1
    bad_options = json_api.update_project(doc, [{"op": "set_design_intent", "design_intent": None}], {"op_id": "x"})
    assert bad_options["error"]["code"] == "PROJECT_OP_INVALID" and bad_options["error"]["op_code"] == "BAD_ARGUMENT"


@pytest.mark.parametrize("options", [None, {"require_check": False}, {"require_check": True}])
def test_a_result_that_is_no_project_is_project_rejected_with_its_problems(options):
    doc = project()
    reply = json_api.update_project(doc, [{"op": "set_source_model", "instance_id": source_id(doc), "changes": {"waist_radius_mm": -1}}],
                                    options)
    assert reply["ok"] is False and reply["error"]["code"] == "PROJECT_REJECTED"
    assert [p["code"] for p in reply["error"]["problems"]] == ["PROJECT_INVALID"]


def _without_source(doc: dict) -> list[dict]:
    return [{"op": "remove_component", "instance_id": source_id(doc)}]


def test_an_edit_that_leaves_a_project_is_applied_and_the_check_reported():
    """A design is built and repaired one edit at a time: the Project without its source is a Project, the reply says what
    a run refuses in it, and the next edit puts the source back."""

    doc = project()
    reply = json_api.update_project(doc, _without_source(doc))
    assert reply["ok"] is True and reply["changed"] is True
    assert [(a["kind"], a["id"], a["change"]) for a in reply["affected"]] == [("COMPONENT", source_id(doc), "REMOVED")]
    assert source_id(doc) not in [c["instance_id"] for c in reply["project"]["components"]]
    from os_optics.local import load_project

    assert reply["project_hash"] == load_project(reply["project"]).content_hash() != reply["base_project_hash"]
    assert reply["check"]["ok"] is False and [p["code"] for p in reply["check"]["problems"]] == ["SOURCE_MISSING"]
    # the same problems a run of that Project reports, and the same as 'project check'
    assert reply["check"]["problems"] == json_api.run_project(reply["project"])["error"]["problems"]
    assert reply["check"]["problems"] == json_api.check_project(reply["project"])["error"]["problems"]
    assert reply["engine_input_hash"] is None and reply["catalog_version"] is None
    assert reply["compute_budget"] is None and reply["numerical_settings"] is None and reply["warnings"] == []
    assert reply["design_intent"] == "LAB_ASSEMBLY"
    json.dumps(reply, allow_nan=False)
    source = next(c for c in doc["components"] if c["instance_id"] == source_id(doc))
    repaired = json_api.update_project(reply["project"], [{"op": "add_component", "component": source}])
    assert repaired["ok"] is True and repaired["check"] == {"ok": True, "problems": []}
    assert repaired["engine_input_hash"] == json_api.check_project(repaired["project"])["engine_input_hash"] is not None


def test_require_check_makes_the_check_a_condition_of_the_edit():
    doc = project()
    reported = json_api.update_project(doc, _without_source(doc))
    required = json_api.update_project(doc, _without_source(doc), {"require_check": True})
    assert required["ok"] is False and required["error"]["code"] == "PROJECT_REJECTED"
    assert required["error"]["problems"] == reported["check"]["problems"]
    assert required["error"]["message"] == "os-optics refused the edited Project; the Project is unchanged"
    with pytest.raises(ProjectUpdateError) as raised:
        update_project(doc, _without_source(doc), require_check=True)
    assert raised.value.code == "PROJECT_REJECTED" and raised.value.problems == reported["check"]["problems"]
    update = update_project(doc, _without_source(doc))
    assert update.check_ok is False and update.check_problems == reported["check"]["problems"]
    bad = json_api.update_project(doc, _without_source(doc), {"require_check": "yes"})
    assert bad["error"]["code"] == "PROJECT_OP_INVALID" and bad["error"]["op_index"] == -1


def test_a_project_that_already_fails_the_check_takes_further_edits():
    """A lowering problem the Project already has does not refuse the next edit: two problems are repaired one at a time."""

    doc = project()
    broken = json_api.update_project(doc, _without_source(doc))["project"]
    moved = json_api.update_project(broken, [{"op": "set_component_pose", "instance_id": lens_id(doc), "position_mm": [2.0, 0.0, 0.0]}])
    assert moved["ok"] is True and moved["changed"] is True
    assert [p["code"] for p in moved["check"]["problems"]] == ["SOURCE_MISSING"]
    assert json_api.update_project(broken, [{"op": "set_component_pose", "instance_id": lens_id(doc), "position_mm": [2.0, 0.0, 0.0]}],
                                   {"require_check": True})["error"]["code"] == "PROJECT_REJECTED"


def test_an_edit_the_solid_check_refuses_reports_the_solid_report():
    doc = project()
    lens = next(c for c in doc["components"] if c["instance_id"] == lens_id(doc))
    twin = {**copy.deepcopy(lens), "instance_id": "TWIN"}
    reply = json_api.update_project(doc, [{"op": "add_component", "component": twin}])
    assert reply["ok"] is True and reply["check"]["ok"] is False
    assert "OPTICAL_BODIES_OVERLAP" in [p["code"] for p in reply["check"]["problems"]]
    assert reply["optical_solids"] == json_api.check_project(reply["project"])["optical_solids"] is not None
    required = json_api.update_project(doc, [{"op": "add_component", "component": twin}], {"require_check": True})
    assert required["error"]["code"] == "PROJECT_REJECTED" and required["error"]["optical_solids"] == reply["optical_solids"]


def test_the_contract_decides_what_is_a_project():
    doc = project()
    # the spectral-line rule is the contract's: a source with both forms is refused
    both = json_api.update_project(doc, [{"op": "set_source_model", "instance_id": source_id(doc),
                                          "changes": {"spectral_lines": [{"wavelength_nm": 532, "power_w": 0.001}, {"wavelength_nm": 1064, "power_w": 0.001}]}}])
    assert both["error"]["code"] == "PROJECT_REJECTED"
    lines = json_api.update_project(doc, [{"op": "set_source_model", "instance_id": source_id(doc), "changes": {
        "wavelength_nm": None, "power_w": None, "spectral_lines": [{"wavelength_nm": 600, "power_w": 0.001}, {"wavelength_nm": 700, "power_w": 0.001}]}}])
    assert lines["ok"] is True


def test_operations_may_repair_a_document_that_is_not_yet_a_valid_project():
    broken = project()
    broken["components"] = [c for c in broken["components"] if not c.get("source_model")] + [
        {**next(c for c in project()["components"] if c.get("source_model")), "nominal_pose": {"position_mm": [0, 0, 0], "quaternion_wxyz": [2, 0, 0, 0]}}]
    assert json_api.check_project(broken)["ok"] is False
    reply = json_api.update_project(broken, [{"op": "set_component_pose", "instance_id": source_id(broken),
                                              "quaternion_wxyz": project()["components"][0]["nominal_pose"]["quaternion_wxyz"]}])
    assert reply["ok"] is True and reply["base_project_hash"] is None and reply["changed"] is True
    guarded = json_api.update_project(broken, [{"op": "set_design_intent", "design_intent": None}], {"expected_project_hash": "0" * 64})
    assert guarded["error"]["code"] == "PROJECT_CHANGED" and guarded["error"]["project_hash"] is None
    assert json_api.update_project([1, 2], [{"op": "set_design_intent", "design_intent": None}])["error"]["code"] == "PROJECT_REJECTED"


def test_transform_is_the_composition_transform_on_the_bare_project():
    doc = project()
    lens = lens_id(doc)
    request = {"selector": {"ids": [lens]}, "translation_mm": [0, 0, 5], "rotation": {"axis": [0, 1, 0], "angle_deg": 1},
               "frame": {"component": lens}, "pivot": {"component": lens}}
    workspace = json_api.workspace_transform(doc, request)
    reply = json_api.update_project(doc, [{"op": "transform", **request}])
    assert reply["ok"] is True and reply["project"] == workspace["workspace"]["project"]
    assert reply["affected"] == [{"kind": "COMPONENT", "id": lens, "change": "MODIFIED", "fields": ["nominal_pose"]}]
    mixed = json_api.update_project(doc, [{"op": "transform", "selector": {"ids": [lens]}, "translation_mm": [1, 0, 0]},
                                          {"op": "set_component_pose", "instance_id": lens, "quaternion_wxyz": doc["components"][1]["nominal_pose"]["quaternion_wxyz"]}])
    assert mixed["ok"] is True
    assert next(c for c in mixed["project"]["components"] if c["instance_id"] == lens)["nominal_pose"]["position_mm"][0] == \
        doc["components"][1]["nominal_pose"]["position_mm"][0] + 1


def test_a_refused_transform_carries_the_composition_problems():
    doc = project()
    reply = json_api.update_project(doc, [{"op": "set_design_intent", "design_intent": None},
                                          {"op": "transform", "selector": {"ids": ["NOPE"]}, "translation_mm": [0, 0, 1]}])
    assert reply["error"]["code"] == "PROJECT_OP_INVALID" and reply["error"]["op_index"] == 1 and reply["error"]["op_code"] == "TRANSFORM_REFUSED"
    assert reply["error"]["problems"][0]["code"] == "SELECTION_INVALID"
    for extra in ({"op_id": "a"}, {"expected_base": "0" * 64}):
        refused = json_api.update_project(doc, [{"op": "transform", "selector": {"ids": [lens_id(doc)]}, "translation_mm": [0, 0, 1], **extra}])
        assert refused["error"]["op_code"] == "BAD_ARGUMENT"
    rotation = json_api.update_project(doc, [{"op": "transform", "selector": {"ids": [lens_id(doc)]}, "rotation": {"axis": [0, 0, 0], "angle_deg": 1}}])
    assert rotation["error"]["problems"][0]["code"] == "ROTATION_INVALID"


def test_the_python_function_raises_the_same_refusals():
    with pytest.raises(ProjectUpdateError) as raised:
        update_project(project(), [{"op": "frobnicate"}])
    assert raised.value.code == "PROJECT_OP_INVALID" and raised.value.details == {"op_index": 0, "op_code": "UNKNOWN_OP"}
    update = update_project(example_project(), [{"op": "set_design_intent", "design_intent": "OPTICAL_SYSTEM"}])
    assert update.changed and update.check["design_intent"] == "OPTICAL_SYSTEM" == update.design_intent
    assert update.check_ok and update.project_hash == update.check["project_hash"] and update.project == update.check["project"]
    assert update.affected == ({"kind": "PROJECT", "id": None, "change": "MODIFIED", "fields": ["design_intent"]},)


def test_capabilities_name_the_operations_warnings_and_freshness():
    caps = json_api.capabilities()
    assert caps["json_api_version"] == "1.9"
    assert caps["project_ops"]["ops"] == list(PROJECT_OPS)
    assert caps["project_ops"]["options"] == ["expected_project_hash", "require_check"]
    assert caps["project_ops"]["error_codes"] == ["PROJECT_OP_INVALID", "PROJECT_REJECTED", "PROJECT_CHANGED"]
    assert caps["check_warnings"] == ["TARGET_INVALID", "TARGET_PARAMETER_INVALID", "UNKNOWN_SOURCE_REFERENCE", "TARGET_UNIT_MISMATCH",
                                      "SOURCE_WAVELENGTH_OUTSIDE_COMPONENT_BAND", "FRAME_SYMMETRY_DERIVED", "DESIGN_GEOMETRY_EXTENDED",
                                      "INPUT_NOT_SIMULATED", "CONFIGURATION_MOVES_FIXED_DOF"]
    assert caps["run_freshness"]["states"] == ["CURRENT", "HISTORICAL", "UNDETERMINED"]
    assert "targets" in caps["results"]["queries"]


# ------------------------------------------------------------------------------------------------ command line


def _write(path: Path, document) -> str:
    path.write_text(json.dumps(document), encoding="utf-8")
    return str(path)


def _printed(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


def test_cli_project_update_prints_or_writes_and_never_touches_the_input(tmp_path, capsys):
    doc = project()
    source = _write(tmp_path / "p.json", doc)
    original = Path(source).read_bytes()
    ops = _write(tmp_path / "ops.json", {"ops": [{"op": "set_component_pose", "instance_id": lens_id(doc), "position_mm": [3.0, 0.0, 0.0]}]})
    assert main(["project", "update", source, "--ops", ops]) == 0
    printed = _printed(capsys)
    assert printed["status"] == "UPDATED" and printed["project_written"] is False and printed["project"]["components"][1]["nominal_pose"]["position_mm"][0] == 3.0
    assert Path(source).read_bytes() == original
    out = tmp_path / "edited.json"
    assert main(["project", "update", source, "--ops", ops, "-o", str(out), "--expect-hash", example_project().content_hash()]) == 0
    written = _printed(capsys)
    assert written["project_written"] is True and "project" not in written and written["out"] == str(out)
    assert json.loads(out.read_text(encoding="utf-8")) == printed["project"] and b"\r\n" not in out.read_bytes()
    assert Path(source).read_bytes() == original
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    # applied again in place: nothing changed, so the file is not rewritten
    stamp = out.stat().st_mtime_ns
    assert main(["project", "update", str(out), "--ops", ops, "-o", str(out)]) == 0
    assert _printed(capsys)["status"] == "UNCHANGED" and out.stat().st_mtime_ns == stamp


def test_cli_project_update_refusals_exit_65_and_write_nothing(tmp_path, capsys):
    doc = project()
    source = _write(tmp_path / "p.json", doc)
    out = tmp_path / "edited.json"
    for ops, code in (([{"op": "remove_component", "instance_id": "NOPE"}], "PROJECT_OP_INVALID"),
                      ([{"op": "set_source_model", "instance_id": source_id(doc), "changes": {"m2": 0.5}}], "PROJECT_REJECTED")):
        assert main(["project", "update", source, "--ops", _write(tmp_path / "ops.json", ops), "-o", str(out)]) == 65
        assert _printed(capsys)["error"]["code"] == code and not out.exists()
    ops = _write(tmp_path / "ops.json", [{"op": "set_design_intent", "design_intent": None}])
    assert main(["project", "update", source, "--ops", ops, "--expect-hash", "0" * 64]) == 65
    assert _printed(capsys)["error"]["code"] == "PROJECT_CHANGED"
    (tmp_path / "bad.json").write_text("[", encoding="utf-8")
    assert main(["project", "update", source, "--ops", str(tmp_path / "bad.json")]) == 64
    assert main(["project", "update", str(tmp_path / "missing.json"), "--ops", ops]) == 64


def test_cli_project_update_reports_the_check_and_requires_it_on_request(tmp_path, capsys):
    doc = project()
    source = _write(tmp_path / "p.json", doc)
    ops = _write(tmp_path / "ops.json", _without_source(doc))
    out = tmp_path / "edited.json"
    assert main(["project", "update", source, "--ops", ops, "-o", str(out), "--require-check"]) == 65
    refused = _printed(capsys)
    assert refused["error"]["code"] == "PROJECT_REJECTED" and not out.exists()
    assert main(["project", "update", source, "--ops", ops, "-o", str(out)]) == 0
    report = _printed(capsys)
    assert report["status"] == "UPDATED" and report["project_written"] is True
    assert report["check"] == {"ok": False, "problems": refused["error"]["problems"]}
    assert report["engine_input_hash"] is None and report["catalog_version"] is None and report["compute_budget"] is None
    assert json.loads(out.read_text(encoding="utf-8")) == json_api.update_project(doc, _without_source(doc))["project"]
    assert main(["project", "check", str(out)]) == 65  # the check itself still refuses the Project
    capsys.readouterr()


def test_cli_project_check_prints_identity_and_warnings_without_a_run(tmp_path, capsys):
    doc = project()
    assert main(["project", "check", _write(tmp_path / "p.json", doc)]) == 0
    printed = _printed(capsys)
    checked = json_api.check_project(doc)
    assert printed["status"] == "ACCEPTED" and printed["project_hash"] == checked["project_hash"]
    assert printed["engine_input_hash"] == checked["engine_input_hash"] and printed["warnings"] == []
    assert printed["optical_solids"]["status"] == checked["optical_solids"]["status"]
    assert not (tmp_path / "run").exists()
    doc["targets"][0]["unit"] = "um"
    assert main(["project", "check", _write(tmp_path / "u.json", doc)]) == 0
    assert [w["code"] for w in _printed(capsys)["warnings"]] == ["TARGET_UNIT_MISMATCH"]
    doc["components"][1]["optical_role"] = "mirror"
    assert main(["project", "check", _write(tmp_path / "r.json", doc)]) == 65
    assert _printed(capsys)["problems"][0]["code"] == "PRODUCT_ROLE_MISMATCH"


def test_cli_capabilities_prints_the_json_api_capabilities(capsys):
    assert main(["capabilities"]) == 0
    printed = _printed(capsys)
    expected = {k: v for k, v in json_api.capabilities().items() if k != "ok"}
    assert printed == json.loads(json.dumps(expected))


def test_the_update_help_says_where_each_numerical_setting_s_default_and_range_are():
    """``project update --help`` names what ``capabilities`` lists for each numerical setting -- the keys it emits -- and
    sends the reader to the Project schema for the ranges, which ``capabilities`` does not list."""
    from os_optics.cli_project import UPDATE_DESCRIPTION

    fields = json_api.capabilities()["numerical_settings"]["fields"]
    keys = {key for entry in fields.values() for key in entry}
    assert keys <= {"default", "unit", "direction", "path"} and {"default", "direction"} <= keys  # (path: a design-point field's)
    assert not keys & {"minimum", "maximum", "range", "ge", "gt", "le", "lt"}
    text = " ".join(UPDATE_DESCRIPTION.split())
    assert ("'capabilities' lists each with its default, unit and direction under numerical_settings.fields; the ranges are "
            "the Project schema's, os-optics schema project") in text
    assert "default and range" not in text


# ------------------------------------------------------------------------------------------------ retry of add-then-edit lists


def test_a_list_that_adds_and_then_edits_an_item_is_guarded_by_the_expected_hash_on_retry():
    from os_optics.cli_project import UPDATE_DESCRIPTION
    from os_optics.local import project_ops

    doc = project()
    base_hash = example_project().content_hash()
    target = {**copy.deepcopy(doc["targets"][0]), "target_id": "t_extra"}
    ops = [{"op": "add_target", "target": target}, {"op": "set_target", "target_id": "t_extra", "changes": {"tolerance_abs": 0.3}}]
    first = json_api.update_project(doc, ops)
    assert first["ok"] is True and first["changed"] is True
    # applied again to its own result, the add meets the changed item: refused, nothing changes
    again = json_api.update_project(first["project"], ops)
    assert (again["error"]["code"], again["error"]["op_index"], again["error"]["op_code"]) == ("PROJECT_OP_INVALID", 0, "DUPLICATE_ID")
    # the guarded retry says the first application took effect: the current hash is the lost reply's
    guarded = json_api.update_project(first["project"], ops, {"expected_project_hash": base_hash})
    assert guarded["error"]["code"] == "PROJECT_CHANGED" and guarded["error"]["project_hash"] == first["project_hash"]
    # and the documents say so
    assert "adds an item and then changes or removes it" in project_ops.__doc__
    assert "adds an item and then changes or removes it" in UPDATE_DESCRIPTION


# ------------------------------------------------------------------------------------------------ documents outside the contract


def _with_model(doc: dict, **library) -> dict:
    return {**doc, "optical_models": [{"model_id": "M", "model": {"model_kind": "IDEAL_THIN_LENS"}}], **library}


@pytest.mark.parametrize("library", [{"model_materials": ["not-an-object"]}, {"model_coatings": [None]},
                                     {"model_materials": [7, {"material_id": "Y"}]}])
def test_non_object_library_entries_are_refused_with_an_envelope_never_an_exception(library):
    reply = json_api.update_project(_with_model(project(), **library), [{"op": "remove_optical_model", "model_id": "M"}])
    assert reply["ok"] is False and reply["error"]["code"] == "PROJECT_REJECTED"
    for op in ({"op": "remove_model_material", "material_id": "Y"}, {"op": "remove_model_coating", "coating_id": "C"}):
        doc = {**project(), "model_materials": ["x", {"material_id": "Y"}], "model_coatings": [None, {"coating_id": "C"}]}
        reply = json_api.update_project(doc, [op])
        assert reply["ok"] is False and reply["error"]["code"] in ("PROJECT_REJECTED", "PROJECT_OP_INVALID")


def test_a_library_that_is_not_a_list_is_a_refused_operation():
    reply = json_api.update_project(_with_model(project(), model_materials=5), [{"op": "remove_optical_model", "model_id": "M"}])
    error = reply["error"]
    assert (error["code"], error["op_index"], error["op_code"]) == ("PROJECT_OP_INVALID", 0, "BAD_ARGUMENT")
    assert error["message"] == ('op 0 ("remove_optical_model") cannot be applied: a field it reads does not follow the '
                                "Project contract; check the Project for the problems")


def test_cli_project_update_of_a_non_object_library_entry_exits_65(tmp_path, capsys):
    bad = _write(tmp_path / "bad.json", _with_model(project(), model_materials=["not-an-object"]))
    ops = _write(tmp_path / "ops.json", [{"op": "remove_optical_model", "model_id": "M"}])
    assert main(["project", "update", bad, "--ops", ops]) == 65
    assert _printed(capsys)["error"]["code"] == "PROJECT_REJECTED"
