"""The vendored contract minor versions of a package generated on this device from a local assembly plan: preview
manifest 3.1 (binding LOCAL_PLAN, ``LocalUsageTermsV1``), Local Validation 2.1, detail 3.2 (release LOCAL_PLAN) and
drawing 4.2 (legend source DESIGN_BOM with a supply on every row) -- and that every committed 3.0 / 3.1 / 4.1 document
still reads and dumps to exactly its bytes."""

from __future__ import annotations

import copy
import json

import pytest
from pydantic import ValidationError

import plan_projection_fixture as fx
from drawing_helpers import CUSTOMER_DETAIL_JSON, CUSTOMER_PREVIEW_ZIP, DETAIL_JSON, MULTI_CHAIN, PREVIEW_ZIP
from os_optics._vendor.shared_contracts.assembly_detail import (
    ASSEMBLY_DETAIL_SCHEMA_VERSIONS,
    LOCAL_DRAWING_DESCRIPTION,
    AssemblyDetailPackageV1,
    AssemblyDrawingManifestV1,
    AssemblyDrawingResultV1,
    DetailBindingV1,
    DetailReleaseRefV1,
    DrawingBindingV1,
    PartsLegendV1,
    ReleaseBasisV1,
    build_assembly_detail,
    detail_file_bytes,
    load_assembly_detail,
)
from os_optics._vendor.shared_contracts.assembly_preview import (
    AssemblyLocalValidationV1,
    AssemblyPreviewManifestV1,
    LocalPlanRefV1,
    LocalUsageTermsV1,
    PackageBindingV1,
    UsageTermsV1,
    build_assembly_preview_package,
    json_file_bytes,
    load_assembly_preview_package,
    read_package_archive,
    verify_assembly_preview_package,
)
from os_optics.assembly import load_assembly_preview, validate_assembly_layout
from os_optics.assembly_plan.drawing import plan_drawing
from os_optics.assembly_plan.projection import project_plan

PROJECT_REF = {"project_hash": {"profile": "osc1", "digest": "a" * 64, "subject": "project_document"}}


def _plan_ref(**over) -> dict:
    return {"plan_id": "ap-0123456789abcdef", "revision": 2,
            "plan_digest": {"profile": "osc1", "digest": "b" * 64, "subject": "assembly_plan_revision"},
            "design_bom": {"bom_hash": {"profile": "osc1", "digest": "c" * 64, "subject": "design_bom"}}, **over}


def _commercial_binding() -> dict:
    return {"project": PROJECT_REF, "resolution": {"resolution_hash": {"profile": "osc1", "digest": "d" * 64,
                                                                        "subject": "assembly_resolution"}}}


@pytest.fixture(scope="module")
def local_outcome():
    rev = fx.revision()
    ctx = fx.context(rev)
    return rev, ctx, plan_drawing(rev, None, context=ctx)


# ------------------------------------------------------------------------------------------------ committed bytes


@pytest.mark.parametrize("archive", [PREVIEW_ZIP, CUSTOMER_PREVIEW_ZIP, *(v[0] for v in MULTI_CHAIN.values())],
                         ids=lambda p: p.name)
def test_every_committed_preview_package_still_verifies_and_dumps_to_its_bytes(archive):
    files = read_package_archive(archive.read_bytes())
    assert verify_assembly_preview_package(files) == ()
    package = load_assembly_preview_package(files)
    manifest = package.manifest
    assert manifest.schema_version == "3.0" and manifest.binding.basis is None and isinstance(manifest.usage, UsageTermsV1)
    assert json_file_bytes(manifest.payload()) == files["manifest.json"]
    assert "basis" not in json.loads(files["manifest.json"])["binding"]
    rebuilt = build_assembly_preview_package(scene=package.scene, geometry={k: v for k, v in files.items() if k.startswith("geometry/")},
                                             binding=manifest.binding, package_revision=manifest.package_revision)
    assert rebuilt.files == package.files  # the 3.0 builder output is byte for byte what it always was
    validation = validate_assembly_layout(package)
    assert validation.schema_version == "2.0" and validation.package.local_plan is None


@pytest.mark.parametrize(("archive", "detail"), [(PREVIEW_ZIP, DETAIL_JSON), (CUSTOMER_PREVIEW_ZIP, CUSTOMER_DETAIL_JSON),
                                                 *((v[0], v[1]) for v in MULTI_CHAIN.values())], ids=lambda p: p.name)
def test_every_committed_detail_still_verifies_as_3_1(archive, detail):
    package = load_assembly_preview(archive)
    data = detail.read_bytes()
    verified = load_assembly_detail(data, package)
    assert verified.detail.schema_version == "3.1" and not verified.detail.binding.local
    assert detail_file_bytes(verified.detail) == data
    again = build_assembly_detail(preview=package, release=verified.detail.release, dimensions=verified.detail.dimensions,
                                  customer_supplied_models=verified.detail.customer_supplied_models,
                                  line_display_names_zh_cn=verified.detail.line_display_names_zh_cn)
    assert detail_file_bytes(again) == data


# ------------------------------------------------------------------------------------------------ preview manifest 3.1


def test_the_binding_is_commercial_or_local_never_both():
    assert PackageBindingV1.model_validate(_commercial_binding()).local is False
    local = PackageBindingV1.model_validate({"project": PROJECT_REF, "basis": "LOCAL_PLAN", "local_plan": _plan_ref()})
    assert local.local and local.resolution is None
    for bad in ({"project": PROJECT_REF},  # commercial without a resolution
                {**_commercial_binding(), "local_plan": _plan_ref()},  # a commercial binding naming a plan
                {"project": PROJECT_REF, "basis": "LOCAL_PLAN"},  # a local binding without its plan
                {**_commercial_binding(), "basis": "LOCAL_PLAN", "local_plan": _plan_ref()},  # local with a resolution
                {"project": PROJECT_REF, "basis": "LOCAL_PLAN", "local_plan": _plan_ref(), "commercial_resolution_id": "res-1"},
                {"project": PROJECT_REF, "basis": "OTHER", "local_plan": _plan_ref()}):
        with pytest.raises(ValidationError):
            PackageBindingV1.model_validate(bad)


def test_local_plan_refs_name_their_subjects_and_grammar():
    for bad in (_plan_ref(plan_digest={"profile": "osc1", "digest": "b" * 64, "subject": "assembly_resolution"}),
                _plan_ref(design_bom={"bom_hash": {"profile": "osc1", "digest": "c" * 64, "subject": "customer_purchase_bom"}}),
                _plan_ref(plan_id="AP_1"), _plan_ref(revision=0), _plan_ref(revision="2")):
        with pytest.raises(ValidationError):
            LocalPlanRefV1.model_validate(bad)


def test_the_local_usage_terms_are_fixed():
    terms = LocalUsageTermsV1()
    assert terms.scope == "THIS_PLAN" and terms.redistribution == "PERMITTED"
    assert [u.value for u in terms.permitted_uses] == ["VIEW_ASSEMBLY", "VALIDATE_ASSEMBLY", "GENERATE_ASSEMBLY_DRAWING"]
    with pytest.raises(ValidationError):
        LocalUsageTermsV1.model_validate({"permitted_uses": ["VIEW_ASSEMBLY"]})


def test_a_local_package_is_manifest_3_1_with_the_local_terms(local_outcome):
    _, _, outcome = local_outcome
    package = outcome.package
    manifest = package.manifest
    assert manifest.schema_version == "3.1" and isinstance(manifest.usage, LocalUsageTermsV1)
    payload = manifest.payload()
    assert {"resolution", "commercial_bom", "commercial_submission_hash", "order_ref"}.isdisjoint(payload["binding"])
    # the version, the terms and the binding go together: each other combination is refused
    for change in ({"schema_version": "3.0"}, {"usage": UsageTermsV1().model_dump(mode="json")}):
        bad = {**payload, **change}
        with pytest.raises(ValidationError):
            AssemblyPreviewManifestV1.model_validate(bad)
    commercial = load_assembly_preview(PREVIEW_ZIP).manifest.payload()
    for change in ({"schema_version": "3.1"}, {"usage": LocalUsageTermsV1().model_dump(mode="json")}):
        with pytest.raises(ValidationError):
            AssemblyPreviewManifestV1.model_validate({**commercial, **change})


def test_the_verifier_pins_a_plan_digest(local_outcome):
    rev, _, outcome = local_outcome
    files = dict(outcome.package.files)
    assert verify_assembly_preview_package(files, expected_plan_digest=rev.content_digest) == ()
    assert any("plan digest" in p for p in verify_assembly_preview_package(files, expected_plan_digest="e" * 64))
    assert any("resolution hash" in p for p in verify_assembly_preview_package(files, expected_resolution_hash="e" * 64))
    commercial = read_package_archive(PREVIEW_ZIP.read_bytes())
    assert any("plan digest" in p for p in verify_assembly_preview_package(commercial, expected_plan_digest=rev.content_digest))
    assert load_assembly_preview(outcome.package, expected_plan_digest=rev.content_digest) is outcome.package


def test_the_layout_check_of_a_local_package_is_validation_2_1(local_outcome):
    rev, _, outcome = local_outcome
    validation = outcome.validation
    assert validation.schema_version == "2.1"
    assert validation.package.resolution is None and validation.package.local_plan.plan_digest.digest == rev.content_digest
    payload = validation.payload()
    with pytest.raises(ValidationError):
        AssemblyLocalValidationV1.model_validate({**payload, "schema_version": "2.0"})


# ------------------------------------------------------------------------------------------------ detail 3.2


def test_a_local_detail_is_3_2_released_local_plan(local_outcome):
    _, _, outcome = local_outcome
    detail = outcome.detail
    assert detail.schema_version == "3.2" and detail.release.basis is ReleaseBasisV1.LOCAL_PLAN
    assert detail.usage.scope == "THIS_PLAN" and detail.binding.local_plan == outcome.package.manifest.binding.local_plan
    assert detail.binding.commercial_resolution_id is None and detail.binding.commercial_bom is None
    assert ASSEMBLY_DETAIL_SCHEMA_VERSIONS == ("3.1", "3.2")
    payload = detail.payload()
    for change in ({"schema_version": "3.1"}, {"release": {"basis": "DIRECT_RELEASE"}},
                   {"usage": AssemblyDetailPackageV1.model_fields["usage"].default.model_dump(mode="json")}):
        with pytest.raises(ValidationError):
            AssemblyDetailPackageV1.model_validate({**payload, **change})


def test_the_detail_binding_takes_exactly_one_form():
    base = {"project": PROJECT_REF, "preview_package_content_hash": "f" * 64, "preview_package_revision": 1}
    commercial = {"commercial_resolution_id": "res-1",
                  "commercial_bom": {"bom_hash": {"profile": "osc1", "digest": "c" * 64, "subject": "customer_purchase_bom"}}}
    assert not DetailBindingV1.model_validate({**base, **commercial}).local
    assert DetailBindingV1.model_validate({**base, "local_plan": _plan_ref()}).local
    for bad in (base, {**base, "commercial_resolution_id": "res-1"}, {**base, **commercial, "local_plan": _plan_ref()}):
        with pytest.raises(ValidationError):
            DetailBindingV1.model_validate(bad)


def test_a_local_detail_is_refused_against_another_package(local_outcome):
    _, _, outcome = local_outcome
    commercial = load_assembly_preview(PREVIEW_ZIP)
    with pytest.raises(ValueError):
        load_assembly_detail(detail_file_bytes(outcome.detail), commercial)
    with pytest.raises(ValueError, match="LOCAL_PLAN"):
        build_assembly_detail(preview=outcome.package, release=DetailReleaseRefV1(basis=ReleaseBasisV1.DIRECT_RELEASE),
                              dimensions=outcome.detail.dimensions)


# ------------------------------------------------------------------------------------------------ drawing 4.2


def test_a_local_drawing_is_4_2_with_the_design_bom_legend(local_outcome):
    _, ctx, outcome = local_outcome
    result = outcome.drawing.result
    assert result.schema_version == "4.2" and outcome.drawing.manifest.schema_version == "4.2"
    assert result.description == LOCAL_DRAWING_DESCRIPTION
    legend = result.parts_legend
    assert legend.source == "DESIGN_BOM" and legend.commercial_bom is None
    assert legend.design_bom.bom_hash.digest == ctx.design_bom["bom_digest"]
    assert all(row.supply == "OPENSCI" for row in legend.rows)
    payload = result.payload()
    for change in ({"schema_version": "4.1"},
                   {"description": AssemblyDrawingResultV1.model_fields["description"].default}):
        with pytest.raises(ValidationError):
            AssemblyDrawingResultV1.model_validate({**payload, **change})
    with pytest.raises(ValidationError):
        AssemblyDrawingManifestV1.model_validate({**outcome.drawing.manifest.payload(), "schema_version": "4.1"})


def test_a_legend_names_exactly_its_bom_and_states_supply_only_for_a_design_bom():
    row = {"commercial_line_ref": "D1", "part_ref": "opt-1", "opensci_sku": "OS-OPT-TEST-A", "display_name": "Lens"}
    design = {"bom_hash": {"profile": "osc1", "digest": "c" * 64, "subject": "design_bom"}}
    commercial = {"bom_hash": {"profile": "osc1", "digest": "c" * 64, "subject": "customer_purchase_bom"}}
    assert PartsLegendV1.model_validate({"source": "DESIGN_BOM", "design_bom": design, "rows": [{**row, "supply": "CUSTOMER_OWNED"}]})
    assert PartsLegendV1.model_validate({"commercial_bom": commercial, "rows": [row]})
    for bad in ({"source": "DESIGN_BOM", "design_bom": design, "rows": [row]},
                {"commercial_bom": commercial, "rows": [{**row, "supply": "OPENSCI"}]},
                {"source": "DESIGN_BOM", "commercial_bom": commercial, "rows": [{**row, "supply": "OPENSCI"}]},
                {"design_bom": design, "rows": [row]}):
        with pytest.raises(ValidationError):
            PartsLegendV1.model_validate(bad)


def test_the_drawing_binding_takes_exactly_one_form(local_outcome):
    _, _, outcome = local_outcome
    binding = outcome.drawing.result.binding.model_dump(mode="json", by_alias=True, exclude_none=True)
    assert DrawingBindingV1.model_validate(binding).local
    bad = copy.deepcopy(binding)
    bad["release"] = {"basis": "DIRECT_RELEASE"}
    with pytest.raises(ValidationError):
        DrawingBindingV1.model_validate(bad)
    bad = copy.deepcopy(binding)
    bad["commercial_resolution_id"] = "res-1"
    with pytest.raises(ValidationError):
        DrawingBindingV1.model_validate(bad)


def test_the_customer_owned_lens_is_a_customer_owned_legend_row():
    rev = fx.revision(fx.with_customer_owned_lens(fx.content()))
    outcome = plan_drawing(rev, None, context=fx.context(rev))
    supply = {row.part_ref: row.supply for row in outcome.drawing.result.parts_legend.rows}
    assert supply["opt-1"] == "CUSTOMER_OWNED" and supply["mnt-1"] == "OPENSCI"
    assert b"your own part" in outcome.drawing.files["assembly_drawing.svg"]
    assert project_plan(rev, fx.context(rev)).package.scene.part("opt-1").commercial_line_ref == "D1"
