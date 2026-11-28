"""``os_optics.local.json_api``: the headless JSON transport callable from a local host.

It must be a pure conversion of the native workflow: the same Project, the same single Engine run, the same record, scene
and run-directory bytes -- only as JSON-safe values and envelopes. Catalog browsing must scale to thousands of products:
paged, filterable, compact rows, no SKU list in ``capabilities``. Everything here uses the active catalog generically
(no SKU is named)."""

from __future__ import annotations

import collections
import json
from pathlib import Path

import pytest

from os_optics.local import (
    EXAMPLE_KINDS,
    LocalRunError,
    LocalRunErrorCode,
    example_project,
    json_api,
    load_public_catalog,
    run_optical_project,
    target_metrics,
)
from os_optics.local.artifacts import run_artifact_files
from os_optics.manifest import engine_build_id, quantize_float

RUN_FILES = {"manifest.json", "project.json", "engine_request.json", "simulation_result.json", "ray_data.json", "local_validation.json",
             "optical_scene.json", "optical_scene.glb"}
SUMMARY_KEYS = {"optical_sku", "category", "form", "display_name", "display_name_zh_cn", "commercial_readiness", "design_roles", "model_kind",
                "fidelity"}
OPTIONAL_SUMMARY_KEYS = {"opensci_mounting", "delivered_form", "outer_diameter_mm", "focal_length_mm", "working_band_nm", "design_wavelength_nm", "reflectance", "transmittance",
                         "retardance_waves", "extinction_ratio", "thickness_mm", "clear_aperture_diameter_mm", "clear_aperture_dimensions_mm",
                         "center_wavelength_nm", "bandwidth_fwhm_nm", "edge_wavelength_nm", "optical_density"}


def _json_safe(value) -> None:
    json.dumps(value, allow_nan=False)


def test_capabilities_describe_this_runtime_and_summarize_the_catalog():
    caps = json_api.capabilities()
    _json_safe(caps)
    catalog = load_public_catalog()
    assert caps["ok"] is True and caps["json_api"] == "opensci.local_json_api" and caps["json_api_version"] == "1.9"
    assert caps["engine_build_id"] == engine_build_id() and caps["runtime_kind"] == "PYTHON_LOCAL"
    assert caps["catalog_version"] == catalog.catalog_version
    # no per-SKU list, whatever the catalog size; the size bound grows only with the Engine's vocabulary (35 kB since the
    # prepared evaluation handles -- their five calls, bound and codes -- and the view settings of an evaluation joined it,
    # 31.2 kB measured, plus 10 %, rounded up to the next 1,000 bytes; 34 kB since the
    # descriptors of the settings block and of the value-basis contract joined it, 30.1 kB measured with every round-2
    # descriptor in place, plus 10 %, rounded up to the next 1,000 bytes; 30 kB since the
    # numerical settings -- every field with its default, unit and direction -- and their edit operation joined it; 26 kB
    # since the capabilities of a selective evaluation and its two methods joined it; 24 kB since the analyses -- focal field, imaging,
    # ghost paths, cavity, configurations -- and the model-scope boundary joined it; 20 kB before)
    assert "optical_skus" not in caps and len(json.dumps(caps)) < 35000
    summary = caps["catalog"]
    assert summary["catalog_version"] == catalog.catalog_version
    assert (summary["product_count"], summary["material_count"], summary["coating_count"]) == (len(catalog.products), len(catalog.materials), len(catalog.coatings))
    assert summary["categories"] == dict(sorted(collections.Counter(p.category.value for p in catalog.products).items()))
    assert sum(summary["forms"].values()) == len(catalog.products)
    assert set(summary["model_kinds"]) <= {"SURFACE_SEQUENCE", "POLARIZATION_ELEMENT"}
    assert caps["example_kinds"] == list(EXAMPLE_KINDS)
    assert caps["target_metrics"] == target_metrics()
    assert caps["dof_axes"] == ["Tx", "Ty", "Tz", "Rx", "Ry", "Rz"] and caps["dof_control_intents"] == ["MANUAL", "NUMERICAL"]
    assert set(caps["schema_kinds"]) == {"project", "local-validation", "catalog", "optical-scene", "view-settings", "optical-workspace", "assembly-validation",
                                         "assembly-detail", "assembly-drawing-result", "assembly-drawing-manifest", "linear-optical-network", "pupil-field-request", "detector-model",
                                         "focal-field-request", "analysis-result", "imaging-request", "ghost-request", "field-request",
                                         "assembly-plan", "assembly-plan-ops", "design-bom", "purchase-bom", "bom-review",
                                         "public-mechanical-catalog"}


def test_catalog_products_filter_like_the_catalog_select_and_page():
    catalog = load_public_catalog()
    everything = json_api.catalog_products()
    _json_safe(everything)
    assert everything["ok"] is True and everything["mechanical_accessories"] == "CONFIGURED_PER_DESIGN"
    assert everything["total"] == len(catalog.products) == len(everything["products"]) and everything["offset"] == 0 and everything["limit"] is None
    assert [p["optical_sku"] for p in everything["products"]] == sorted(p.optical_sku for p in catalog.products)
    assert everything["products"][0] == catalog.products[0].model_dump(mode="json", by_alias=True, exclude_none=True)
    lenses = json_api.catalog_products(category="LENS", wavelength_nm=780)
    expected = [p.optical_sku for p in catalog.select(category="LENS", wavelength_nm="780")]
    assert [p["optical_sku"] for p in lenses["products"]] == expected and lenses["total"] == len(expected) > 0
    page = json_api.catalog_products(category="LENS", wavelength_nm="780", offset=1, limit=2, summary=True)
    assert page["total"] == len(expected) and (page["offset"], page["limit"]) == (1, 2)
    assert [p["optical_sku"] for p in page["products"]] == expected[1:3]
    assert json_api.catalog_products(category="LENS", offset=10**6)["products"] == []
    assert json_api.catalog_products(limit=0)["products"] == [] and json_api.catalog_products(limit=0)["total"] == len(catalog.products)
    forms = json_api.catalog_products(form="PLANO_CONVEX_LENS")
    assert forms["total"] == sum(1 for p in catalog.products if p.form.value == "PLANO_CONVEX_LENS")
    assert json_api.catalog_products(wavelength_nm="1")["products"] == []


def test_summary_rows_are_compact_and_carry_the_key_specifications():
    catalog = load_public_catalog()
    rows = json_api.catalog_products(summary=True)["products"]
    assert len(rows) == len(catalog.products)
    for row, product in zip(rows, catalog.products, strict=True):
        assert SUMMARY_KEYS <= set(row) <= SUMMARY_KEYS | OPTIONAL_SUMMARY_KEYS
        assert row["design_roles"] == sorted(product.design_roles)
        assert (row["model_kind"], row["fidelity"]) == (product.simulation_models[0].model_kind, product.simulation_models[0].fidelity.value)
        spec = product.specifications
        assert row["outer_diameter_mm"] == spec.outer_diameter_mm.value
        if spec.focal_length_mm is not None:
            assert row["focal_length_mm"] == spec.focal_length_mm.value
        assert row["thickness_mm"] == spec.thickness_mm.value  # the facts that tell siblings apart
        for name in ("clear_aperture_diameter_mm", "center_wavelength_nm", "bandwidth_fwhm_nm", "edge_wavelength_nm", "optical_density"):
            assert row.get(name) == (None if getattr(spec, name) is None else getattr(spec, name).value)
        if spec.working_band_nm is not None:
            assert row["working_band_nm"] == {"min_nm": spec.working_band_nm.min_nm, "max_nm": spec.working_band_nm.max_nm}
    assert len(json.dumps(rows)) < len(json.dumps(json_api.catalog_products()["products"])) / 3


@pytest.mark.parametrize("kwargs", [{"category": "SPOON"}, {"form": "SPOON"}, {"offset": -1}, {"limit": -2}, {"offset": 1.5}, {"limit": True},
                                    {"wavelength_nm": "blue"}])
def test_bad_catalog_queries_are_query_errors(kwargs):
    reply = json_api.catalog_products(**kwargs)
    _json_safe(reply)
    assert reply["ok"] is False and [p["code"] for p in reply["error"]["problems"]] == ["CATALOG_QUERY_INVALID"]


def test_one_product_its_materials_and_its_coatings():
    catalog = load_public_catalog()
    product = catalog.products[0]
    reply = json_api.catalog_product(product.optical_sku)
    _json_safe(reply)
    assert reply == {"ok": True, "catalog_version": catalog.catalog_version, "product": product.model_dump(mode="json", by_alias=True, exclude_none=True)}
    missing = json_api.catalog_product("OS-OPT-NOT-PUBLISHED")
    assert missing["ok"] is False and [p["code"] for p in missing["error"]["problems"]] == ["OPTICAL_SKU_NOT_PUBLISHED"]
    assert json_api.catalog_product(42)["ok"] is False
    materials, coatings = json_api.catalog_materials(), json_api.catalog_coatings()
    _json_safe(materials)
    _json_safe(coatings)
    assert [m["material_id"] for m in materials["materials"]] == [m.material_id for m in catalog.materials]
    assert [c["coating_id"] for c in coatings["coatings"]] == [c.coating_id for c in catalog.coatings]


def test_check_project_normalizes_and_binds_without_running():
    example = json_api.example_project_document()
    assert example["project"] == example_project().payload() and example["project_hash"] == example_project().content_hash()
    checked = json_api.check_project(json.dumps(example["project"]))  # JSON text is accepted too
    _json_safe(checked)
    run = run_optical_project(example_project())
    assert checked["ok"] is True
    assert checked["project"] == example["project"] and checked["project_hash"] == example["project_hash"]
    assert checked["engine_input_hash"] == run.engine_input_hash
    assert checked["simulated_parts"] == [p.model_dump(mode="json", exclude_none=True) for p in run.simulated_parts]


def _as_mirror(document):
    document["components"][1]["optical_role"] = "mirror"
    return document


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (_as_mirror, "PRODUCT_ROLE_MISMATCH"),
        (lambda d: {**d, "unexpected": 1}, "PROJECT_INVALID"),
        (lambda d: {**d, "components": [d["components"][0], {**d["components"][1], "product": {"optical_sku": "OS-OPT-NOT-PUBLISHED"}}]}, "OPTICAL_SKU_NOT_PUBLISHED"),
    ],
)
def test_refusals_are_envelopes_with_the_runtime_error_codes(mutate, code):
    document = mutate(example_project().payload())
    with pytest.raises(LocalRunError) as raised:
        run_optical_project(document)
    for reply in (json_api.check_project(document), json_api.run_project(document)):
        _json_safe(reply)
        assert reply["ok"] is False and reply["error"]["code"] == "LOCAL_RUN_ERROR"
        assert [p["code"] for p in reply["error"]["problems"]] == [c.value for c in raised.value.codes]
        assert code in [p["code"] for p in reply["error"]["problems"]]


def test_run_project_is_the_mode_a_run_as_json(tmp_path):
    reply = json_api.run_project(example_project().payload(), output_dir=str(tmp_path / "run"))
    native = run_optical_project(example_project())
    files = reply.pop("files")
    _json_safe(reply)
    assert reply["ok"] is True and reply["status"] == native.status.value == "UNKNOWN"
    assert "BULK_LOSS_NOT_ESTABLISHED" in {issue.code.value for issue in native.result.issues}
    assert {t.target_id: t.status.value for t in native.result.targets} == {"focus_position": "PASS", "spot_radius": "PASS"}
    assert reply["verify"] == []
    assert reply["local_validation"] == native.local_validation.payload()
    assert reply["optical_scene"] == native.optical_view.payload()
    # the in-memory files are exactly the run directory the local design workflow writes, byte for byte
    assert set(files) == RUN_FILES
    assert files == run_artifact_files(native)
    assert {p.name for p in (tmp_path / "run").iterdir()} == RUN_FILES
    for name, data in files.items():
        assert (tmp_path / "run" / name).read_bytes() == data
    assert reply["manifest"] == json.loads(files["manifest.json"])
    summary = reply["summary"]
    assert summary["status"] == "UNKNOWN" and summary["project_hash"] == example_project().content_hash()
    assert summary["engine_input_hash"] == native.local_validation.input_hash.digest
    assert summary["engine_result_hash"] == native.local_validation.result_hash.digest
    assert summary["engine_build_id"] == engine_build_id() and summary["runtime_kind"] == "PYTHON_LOCAL"
    numerical = native.request.configuration.numerical  # JSON numbers carry the native result's quantization
    assert [(t["target_id"], t["status"], t["value"]) for t in summary["targets"]] == [
        (t.target_id, t.status.value, quantize_float(t.value, numerical.hash_float_sig_digits, numerical.hash_float_abs_floor)) for t in native.result.targets]
    # The target value and its error are the Engine's; a JSON client need not recompute them.
    assert [(t["target_value"], t["error"]) for t in summary["targets"]] == [
        (t.target_value, None if t.error is None else quantize_float(t.error, numerical.hash_float_sig_digits, numerical.hash_float_abs_floor))
        for t in native.result.targets]
    assert any(t["target_value"] is not None and t["error"] is not None for t in summary["targets"])
    [plane] = summary["observations"]
    [beam] = plane["arrivals"]
    assert plane["observation_id"] == "focal_plane" and beam["source_id"] == "SRC"
    raw = native.result.observations[0].arrivals[0].beam
    assert beam["beam_radius_x_mm"] == quantize_float(raw.gaussian.beam_radius_x_mm, numerical.hash_float_sig_digits, numerical.hash_float_abs_floor)
    assert beam["power_W"] is raw.power_W is None
    assert beam["power_upper_bound_W"] == quantize_float(native.result.observations[0].arrivals[0].power_upper_bound_W,
                                                          numerical.hash_float_sig_digits, numerical.hash_float_abs_floor)
    assert beam["crossing_index"] == 0 and plane["absorbing_extent"] == native.result.observations[0].plane.absorbing_extent
    # the compact power budget: per source line, quantized like the other summary numbers
    q = lambda value: quantize_float(value, numerical.hash_float_sig_digits, numerical.hash_float_abs_floor)  # noqa: E731
    budget = native.result.power_budget
    assert summary["power_budget"] == {
        "lines": [{"source_id": line.source_id, "spectral_index": line.spectral_index, "wavelength_nm": q(line.wavelength_nm),
                   "input_power_W": q(line.input_power_W), "sinks": {k: q(v) for k, v in line.sinks.items()},
                   "residual_W": q(line.residual_W), "conserved": line.conserved} for line in budget.lines],
        "total_input_W": q(budget.total_input_W), "total_sinks": {k: q(v) for k, v in budget.total_sinks.items()},
        "conserved": budget.conserved,
    }
    [line] = summary["power_budget"]["lines"]
    assert line["source_id"] == "SRC" and line["conserved"] is True
    assert line["input_power_W"] == pytest.approx(sum(line["sinks"].values()), rel=1e-9)
    assert "artifacts" not in summary  # no host-specific paths


def test_the_run_and_evaluation_replies_lead_with_execution_and_requirements():
    """The replies answer in reading order: execution, requirements, then the aggregate under ``diagnostic_summary`` beside
    the unchanged ``status``; the run summary inside the reply does the same."""
    document = example_project().payload()
    reply = json_api.run_project(document)
    assert list(reply)[:5] == ["ok", "execution", "requirements", "diagnostic_summary", "status"]
    assert reply["execution"] == {"outcome": "COMPLETED", "termination": "COMPLETED", "complete": True}
    assert reply["requirements"] == reply["summary"]["outcome"]["requirements"] == reply["summary"]["requirements"]
    assert reply["diagnostic_summary"] == reply["status"] == reply["summary"]["diagnostic_summary"]
    assert list(reply["summary"])[:4] == ["execution", "requirements", "diagnostic_summary", "status"]
    evaluated = json_api.evaluate_project(document, outputs=["requirements"])
    assert evaluated["ok"] and list(evaluated)[:6] == ["ok", "evaluation", "execution", "requirements", "diagnostic_summary", "status"]
    # (the evaluation's lead requirements are the record's lists and the declared targets it did not assess: none here)
    assert evaluated["execution"]["outcome"] == "COMPLETED" and evaluated["requirements"]["not_assessed"] == []
    assert {k: v for k, v in evaluated["requirements"].items() if k != "not_assessed"} == evaluated["outcome"]["requirements"]
    assert evaluated["diagnostic_summary"] == evaluated["status"]


def test_run_project_without_output_dir_writes_nothing_and_reports_no_verification():
    reply = json_api.run_project(example_project().payload())
    assert reply["ok"] is True and reply["verify"] is None and set(reply["files"]) == RUN_FILES


def test_engine_outcomes_are_returned_not_raised():
    """A FAIL and an OUT_OF_MODEL_SCOPE design come back as complete runs with a status, like the local design workflow."""

    failing = example_project().payload()
    failing["components"][1]["nominal_pose"]["position_mm"] = [5.0, 0.0, 0.0]  # the focus moves off the screen
    out_of_scope = example_project().payload()
    out_of_scope["targets"].append({"target_id": "psf_width", "metric": "psf", "observation_id": "focal_plane", "maximum": 0.05})
    for document, expected in ((failing, "FAIL"), (out_of_scope, "OUT_OF_MODEL_SCOPE")):
        reply = json_api.run_project(document)
        assert reply["ok"] is True
        assert reply["status"] == run_optical_project(document).status.value == expected


def test_view_settings_change_only_the_view():
    plain = json_api.run_project(example_project().payload())
    all_rays = json_api.run_project(example_project().payload(), view_settings={"pupil_ray_selection": "ALL"})
    assert all_rays["summary"]["engine_result_hash"] == plain["summary"]["engine_result_hash"]
    assert all_rays["local_validation"] == plain["local_validation"]
    assert all_rays["optical_scene"]["builder"]["settings"]["pupil_ray_selection"] == "ALL"
    bad = json_api.run_project(example_project().payload(), view_settings={"coordinate_decimals": 99})
    assert bad["ok"] is False and bad["error"]["problems"][0]["code"] == LocalRunErrorCode.PROJECT_INVALID.value


def test_json_schema_kinds():
    project = json_api.json_schema("project")
    assert project["ok"] is True and project["schema"]["title"] == "ProjectDocumentV2"
    assert json_api.json_schema("catalog")["schema"]["title"] == "PublicOpticalCatalogV2"
    assert json_api.json_schema("nope")["ok"] is False


# ---------------------------------------------------------------------------------------------- commercial submission


def _problem_codes(reply) -> set[str]:
    return {p["code"] for p in reply["error"]["problems"]}


def test_commercial_submission_is_the_client_submission_of_the_run(tmp_path):
    """The browser's Order button submits exactly what ``OpenSciClient.submit`` / ``os-optics resolve submit``
    would for the same run directory: the same canonical body and the same idempotency key."""

    from os_optics.client import canonical_json_bytes as client_canonical
    from os_optics.client import idempotency_key_for as client_key
    from os_optics.client import submission_document as client_submission
    from os_optics.local.submission import SUBMISSION_FILE, canonical_json_bytes, idempotency_key_for, submission_document

    reply = json_api.run_project(example_project().payload(), output_dir=str(tmp_path / "run"))
    prepared = json_api.commercial_submission(reply["files"])
    assert prepared["ok"] is True and set(prepared) == {"ok", "idempotency_key", "project_hash", "files"}
    assert SUBMISSION_FILE == "submission.json" and set(prepared["files"]) == {"submission.json"}
    _json_safe({key: value for key, value in prepared.items() if key != "files"})
    expected = submission_document(tmp_path / "run")
    body = prepared["files"]["submission.json"]
    assert isinstance(body, bytes) and body == canonical_json_bytes(expected)
    assert prepared["idempotency_key"] == idempotency_key_for("submit", body)
    assert prepared["project_hash"] == expected["project_hash"] == example_project().content_hash()
    assert json.loads(body) == expected and "customer_constraints" not in expected
    # The client package re-exports the same helpers: every headless transport derives identical bodies and keys.
    assert (client_canonical, client_key) == (canonical_json_bytes, idempotency_key_for)
    assert client_canonical(client_submission(tmp_path / "run")) == body
    assert client_canonical(client_submission(run_optical_project(example_project()))) == body
    assert submission_document(reply["files"]) == expected  # the in-memory files are a run directory too
    # bytes-like values (what a host's binding hands over) are accepted; the result does not change
    views = {name: memoryview(data) for name, data in reply["files"].items()}
    views["project.json"] = bytearray(reply["files"]["project.json"])
    assert json_api.commercial_submission(views) == prepared
    # the purchase options pass through unchanged: the service checks them
    for options in ({"include_breadboard": False}, {"assembly_service": True}):
        constrained = json_api.commercial_submission(reply["files"], customer_constraints=options)
        assert constrained["ok"] is True and constrained["idempotency_key"] != prepared["idempotency_key"]
        assert json.loads(constrained["files"]["submission.json"])["customer_constraints"] == options


def test_commercial_submission_refuses_run_files_that_do_not_verify():
    files = json_api.run_project(example_project().payload())["files"]
    tampered_record = dict(files, **{"local_validation.json": files["local_validation.json"].replace(b'"PASS"', b'"FAIL"', 1)})
    other = json.loads(files["project.json"])
    other["components"][1]["nominal_pose"]["position_mm"][0] += 1.0  # the design changed after the run
    tampered_project = dict(files, **{"project.json": json.dumps(other, sort_keys=True).encode("utf-8")})
    altered_glb = dict(files, **{"optical_scene.glb": files["optical_scene.glb"][:-4] + bytes(4)})
    missing_manifest = {name: data for name, data in files.items() if name != "manifest.json"}
    missing_result = {name: data for name, data in files.items() if name != "simulation_result.json"}
    for broken in (tampered_record, tampered_project, altered_glb, missing_manifest, missing_result, {}):
        reply = json_api.commercial_submission(broken)
        assert reply["ok"] is False and reply["error"]["code"] == "RUN_DIRECTORY_INCONSISTENT", sorted(broken)
        assert reply["error"]["problems"] and _problem_codes(reply) == {"RUN_DIRECTORY_INCONSISTENT"}
        assert all(isinstance(p["message"], str) and p["message"] for p in reply["error"]["problems"])
        assert "files" not in reply
        _json_safe(reply)
    for run_files, constraints in ((None, None), (b"zip bytes", None), ([files], None), ({"project.json": "text"}, None),
                                   (files, ["include_breadboard"]), (files, "include_breadboard=false")):
        reply = json_api.commercial_submission(run_files, customer_constraints=constraints)
        assert reply["ok"] is False and reply["error"]["code"] == "INPUT_INVALID"
        _json_safe(reply)


def test_a_submission_needs_no_display_file_of_the_run(tmp_path):
    """A submission sends the Project and the Local Validation record; the manifest, the Engine request and result and the
    record vouch for them. Without the optical scene, its GLB or the ray data (each in turn, and all three) the run files
    give the same submission bytes and Idempotency-Key as the complete run, in memory and as a run directory."""

    from os_optics.local.submission import canonical_json_bytes, submission_document

    files = json_api.run_project(example_project().payload())["files"]
    complete = json_api.commercial_submission(files)
    assert complete["ok"] is True
    display = ("optical_scene.glb", "optical_scene.json", "ray_data.json")
    for left_out in (*((name,) for name in display), display):
        reply = json_api.commercial_submission({name: data for name, data in files.items() if name not in left_out})
        assert reply == complete, left_out
    run_optical_project(example_project(), output_dir=tmp_path / "run")
    expected = submission_document(tmp_path / "run")
    for name in display:
        (tmp_path / "run" / name).unlink()
    assert submission_document(tmp_path / "run") == expected
    assert canonical_json_bytes(expected) == complete["files"]["submission.json"]


def test_the_client_refuses_an_inconsistent_run_directory_with_its_own_error(tmp_path):
    """``os_optics.client.submission_document`` keeps its ``LocalInputError`` (code RUN_DIRECTORY_INCONSISTENT)."""

    from os_optics.client import LocalInputError, Recovery, submission_document
    from os_optics.local.submission import RunDirectoryInconsistentError
    from os_optics.local.submission import submission_document as local_submission

    run_optical_project(example_project(), output_dir=tmp_path / "run")
    record = tmp_path / "run" / "local_validation.json"
    record.write_bytes(record.read_bytes().replace(b'"PASS"', b'"FAIL"', 1))
    with pytest.raises(LocalInputError) as refused:
        submission_document(tmp_path / "run")
    assert refused.value.code == "RUN_DIRECTORY_INCONSISTENT" and refused.value.recovery is Recovery.FIX_REQUEST
    assert refused.value.details["run_dir"] == str(tmp_path / "run") and refused.value.details["problems"]
    with pytest.raises(RunDirectoryInconsistentError) as local:
        local_submission(tmp_path / "run")
    assert local.value.details == refused.value.details
    with pytest.raises(TypeError):
        local_submission(42)


# ---------------------------------------------------------------------------------------------- golden key vectors

#: Committed headless idempotency-key vectors, checked against the actual Python implementation.
#: ``OPENSCI_WRITE_IDEMPOTENCY_VECTORS=1`` rewrites the file; otherwise any algorithm drift fails.
IDEMPOTENCY_VECTORS = Path(__file__).resolve().parent / "fixtures" / "idempotency-vectors.json"
_RESOLUTION = "0192b4d2-6c1e-7a3f-9d2b-3c4d5e6f7a8b"
_ORDER = "0192b4d3-0a1b-7c2d-8e3f-4a5b6c7d8e9f"


def _vector_parts() -> list[tuple[str, list[str | bytes]]]:
    from os_optics.local.submission import canonical_json_bytes

    body = canonical_json_bytes({"schema": "opensci.commercial_submission", "schema_version": "2.0",
                                 "project": {"name": "透镜 focus µm", "components": []}, "project_hash": "osc1:" + "ab" * 32})
    return [
        ("order", [_RESOLUTION]),
        ("order", [_RESOLUTION, _ORDER]),
        ("bank-transfer-report", [_ORDER]),
        ("artifact-set", ["sha256:" + "0f" * 32]),
        ("submit", [body]),
        ("submit", [b""]),
        ("order", []),
        ("order", [""]),
        ("x", ["透镜"]),
        ("x", ["透镜".encode("utf-8")]),
        ("x", ["a\x00b", "\U0001f600 µm", bytes([0, 255, 128]), ""]),
    ]


def _idempotency_vectors() -> dict:
    from os_optics.local.submission import idempotency_key_for

    vectors = []
    for operation, parts in _vector_parts():
        encoded = [{"bytes_hex": part.hex()} if isinstance(part, bytes) else {"text": part} for part in parts]
        vectors.append({"operation": operation, "parts": encoded, "key": idempotency_key_for(operation, *parts)})
    return {
        "schema": "opensci.idempotency_key_vectors",
        "schema_version": "1.0",
        "source": "os_optics.local.submission.idempotency_key_for (os-optics)",
        "generator": "tests/test_local_json_api.py (OPENSCI_WRITE_IDEMPOTENCY_VECTORS=1 rewrites)",
        "algorithm": "opensci-<operation>-hex(SHA-256(utf8(operation) || for each part: 0x00 || part)); a text part is "
                     "its UTF-8 bytes, a bytes part is used as given",
        "vectors": vectors,
    }


def test_headless_idempotency_key_vectors():
    import os

    from os_optics.local.submission import idempotency_key_for

    document = _idempotency_vectors()
    text = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    keys = [vector["key"] for vector in document["vectors"]]
    assert len(set(keys)) == len(keys) - 1  # "x" text and bytes parts agree; every other vector differs
    assert idempotency_key_for("x", "透镜") == idempotency_key_for("x", "透镜".encode("utf-8"))
    assert all(key.startswith("opensci-") and len(key) == len(f"opensci-{v['operation']}-") + 64
               for key, v in zip(keys, document["vectors"]))
    if os.environ.get("OPENSCI_WRITE_IDEMPOTENCY_VECTORS") == "1":
        IDEMPOTENCY_VECTORS.parent.mkdir(parents=True, exist_ok=True)
        IDEMPOTENCY_VECTORS.write_bytes(text.encode("utf-8"))
    assert IDEMPOTENCY_VECTORS.read_bytes().replace(b"\r\n", b"\n").decode("utf-8") == text


def test_run_summary_target_rows_carry_their_issue_codes():
    """PV-O2: each target row of the run summary carries the target's issue codes with its message (as the result does)."""

    from os_optics.local import json_api

    document = example_project().payload()
    document["targets"].append({"target_id": "too_much", "metric": "power_W", "observation_id": document["observations"][0]["observation_id"],
                                "minimum": 1000.0, "unit": "W"})
    reply = json_api.run_project(document)
    assert reply["ok"] is True
    result = json.loads(bytes(reply["files"]["simulation_result.json"]).decode("utf-8"))
    by_id = {t["target_id"]: t for t in result["targets"]}
    assert reply["summary"]["targets"]
    for row in reply["summary"]["targets"]:
        assert row["issue_codes"] == sorted(by_id[row["target_id"]]["issue_codes"]), row["target_id"]
        assert row["assessment"] == by_id[row["target_id"]]["assessment"]
        assert row["applicability_issue_codes"] == []
    assert any(row["issue_codes"] for row in reply["summary"]["targets"])


@pytest.mark.parametrize("interactions", [1, 2], ids=["dark-plane", "lit-plane"])
@pytest.mark.parametrize("lossless", [False, True], ids=["unstated-bulk-loss", "lossless-fixture"])
def test_run_summary_observation_rows_carry_the_planes_own_message(interactions, lossless):
    """An observation row carries the plane's ``message`` -- the same sentence ``simulation_result.json`` and
    ``results monitors`` give -- so a reader of the row learns why the plane's power is not known (here the focus example
    with the per-path interaction limit set so low that the beam stops before or right after the lens)."""

    from os_optics.results import load_run

    document = example_project().payload()
    if lossless:
        from declared_helpers import declared_project
        from local_helpers import catalog_document

        document = declared_project(document, catalog_document())
        for material in document["model_materials"]:
            material["absorption_coefficient_per_mm"] = "0"
    document["compute_budget"] = {"max_interactions_per_path": interactions}
    reply = json_api.run_project(document)
    assert reply["ok"] is True
    result = json.loads(bytes(reply["files"]["simulation_result.json"]).decode("utf-8"))
    by_id = {o["observation_id"]: o["message"] for o in result["observations"]}
    monitors = {m["observation_id"]: m["message"] for m in load_run(reply["files"]).monitors()}
    rows = {row["observation_id"]: row for row in reply["summary"]["observations"]}
    assert set(rows) == set(by_id) and rows
    for observation_id, row in rows.items():
        assert row["message"] == by_id[observation_id] == monitors[observation_id], observation_id
    (row,) = rows.values()
    if lossless:
        assert row["quantity_state"] == ("UNKNOWN" if interactions == 1 else "BOUNDED")
        assert "per-path interaction limit" in row["message"]
        assert ("whether light reaches it is not known" if interactions == 1 else "no upper bound") in row["message"]
    else:
        assert row["quantity_state"] == "UNKNOWN" and "bulk absorption is not established" in row["message"]
        assert result["outcome"]["execution"]["budgets"]["exhausted"] == (["max_interactions_per_path"] if interactions == 1 else [])
        assert ("whether light reaches it is not known" if interactions == 1 else "without an upper bound") in row["message"]
