"""The detail (3.2) and the drawing (4.2) of a local assembly plan revision (``os_optics.assembly_plan.detail`` /
``drawing``): the fixed dimension rule table over the plan's package, the provenance cross-check, the delivery /
diagnostic / refusal matrix, the design BOM legend with supply rows, the "Assembly drawing of your design" wording in
English and Simplified Chinese, the written files and byte determinism. Every drawing generated here also passes the
build-time output text gate and the output verifier (tests/conftest.py). Synthetic data only."""

from __future__ import annotations

import copy

import pytest

import plan_projection_fixture as fx
from drawing_helpers import metadata, svg_tree
from os_optics._vendor.shared_contracts.assembly_detail import detail_file_bytes, load_assembly_detail
from os_optics.assembly_drawing import AssemblyDrawingError, generate_assembly_drawing, verify_assembly_drawing
from os_optics.assembly_drawing.errors import DrawingErrorCode
from os_optics.assembly_drawing.generate import drawing_output_files, unrelated_output_entries
from os_optics.assembly_plan import findings as f
from os_optics.assembly_plan.contract import (AssemblyPlanError, AssemblyPlanErrorCode, FindingBasisV1, FindingCheckV1,
                                              FindingSourceV1, PlanFindingCodeV1)
from os_optics.assembly_plan.detail import assembly_detail_dimensions, project_plan_detail, setting_bases_of
from os_optics.assembly_plan.drawing import EXIT_DIAGNOSTIC, EXIT_DRAWING, PREVIEW_ARCHIVE_NAME, blocking_findings, plan_drawing
from os_optics.assembly_plan.projection import project_plan


@pytest.fixture(scope="module")
def reference():
    rev = fx.revision()
    ctx = fx.context(rev)
    return rev, ctx, project_plan(rev, ctx).package


def _intruding() -> tuple:
    data = fx.with_declared_envelope(fx.content())
    fx.item(data, "env-1")["placement"]["pose_in_lab"]["position_mm"] = [fx.LENS_AT[0] + fx.LENS_HALF_THICKNESS,
                                                                         fx.LENS_AT[1] + 15.0, 0.0]
    rev = fx.revision(data)
    return rev, fx.context(rev)


# ------------------------------------------------------------------------------------------------ detail


def test_the_rule_table_dimensions_the_chain(reference):
    _, _, package = reference
    kinds = [(d.kind.value, d.subject_part_ref, d.axis.value) for d in assembly_detail_dimensions(package.scene)]
    assert kinds == [("FIXATION_HOLE_POSITION", "hld-1", "WORLD_X"), ("FIXATION_HOLE_POSITION", "hld-1", "WORLD_Y"),
                     ("FIXATION_OFFSET", "hld-1", "WORLD_X"), ("INSERTION_DEPTH", "pst-1", "WORLD_Z"),
                     ("OPTICAL_AXIS_HEIGHT", "opt-1", "WORLD_Z"), ("OPTIC_BOARD_POSITION", "opt-1", "WORLD_X"),
                     ("OPTIC_BOARD_POSITION", "opt-1", "WORLD_Y"), ("EXTERNAL_SOURCE_STANDOFF", "opt-1", "WORLD_X")]


def test_the_detail_verifies_and_measures_the_plan(reference):
    rev, ctx, package = reference
    detail = project_plan_detail(package, rev, ctx)
    verified = load_assembly_detail(detail_file_bytes(detail), package)
    values = {d.kind.value + ":" + d.axis.value: verified.measured[d.dim_ref].value_mm for d in detail.dimensions}
    assert values["OPTICAL_AXIS_HEIGHT:WORLD_Z"] == pytest.approx(fx.LENS_AT[2])
    assert values["INSERTION_DEPTH:WORLD_Z"] == pytest.approx(-20.0) or values["INSERTION_DEPTH:WORLD_Z"] == pytest.approx(20.0)
    assert values["FIXATION_OFFSET:WORLD_X"] == pytest.approx(fx.SLOT_OFFSET)
    assert detail.customer_supplied_models == ()
    assert setting_bases_of(rev)[("pst-1", "INSERTION_DEPTH")] == "RESOLVED"


def test_a_package_of_another_revision_or_basis_is_refused(reference):
    rev, ctx, package = reference
    data = fx.content()
    fx.item(data, "pst-1")["placement"]["settings"][0]["basis"] = "PREVIEW_ASSUMPTION"
    other = fx.revision(data)
    with pytest.raises(AssemblyPlanError) as caught:
        project_plan_detail(package, other, ctx)  # the package is of another plan digest
    assert caught.value.code is AssemblyPlanErrorCode.PLAN_DIR_INCONSISTENT
    forged = other.model_copy(update={"content_digest": rev.content_digest})  # same digest, other setting basis
    with pytest.raises(AssemblyPlanError) as caught:
        project_plan_detail(package, forged, ctx)
    assert caught.value.details["code"] == "DETAIL_PROVENANCE_MISMATCH"


def test_a_customer_owned_anchor_optic_names_its_catalog_model():
    data = fx.content()
    lens = fx.item(data, "opt-1")
    lens.update({"mounting": "ANCHOR", "supply": "CUSTOMER_OWNED", "supply_reason": "CUSTOMER_OWNED",
                 "purchase": {"selected": False, "quantity": 0, "spare_units": 0},
                 "envelope": {"shape": "CYLINDER", "basis": "CATALOG_DESIGN_GEOMETRY", "diameter_mm": 25.4, "z_min_mm": -2.0,
                              "z_max_mm": 2.0}})
    lens["placement"] = {"pose_in_lab": lens["placement"]["pose_in_lab"], "assembly_order": 1}
    data["items"] = [i for i in data["items"] if i["item_id"] not in ("mnt-1", "pst-1", "hld-1")]
    data["links"] = []
    rev = fx.revision(data)
    ctx = fx.context(rev)
    package = project_plan(rev, ctx).package
    anchor = next(a for a in package.scene.beam_reference.design_anchors if a.instance_id == "L1")
    assert anchor.customer_supply.reason.value == "CUSTOMER_OWNED" and anchor.customer_supply.opensci_sku == fx.LENS_SKU
    assert anchor.customer_supply.reserved_envelope.basis == "CATALOG_DESIGN_GEOMETRY"
    # a board alone has nothing to dimension
    with pytest.raises(AssemblyPlanError):
        project_plan_detail(package, rev, ctx)


# ------------------------------------------------------------------------------------------------ drawing


def test_the_detailed_drawing_of_your_design(reference, tmp_path):
    rev, ctx, _ = reference
    outcome = plan_drawing(rev, tmp_path / "out", context=ctx)
    assert outcome.exit_code == EXIT_DRAWING and outcome.drawing.delivery_ready
    assert sorted(p.name for p in outcome.written) == sorted(outcome.files())
    assert {p.name for p in (tmp_path / "out").iterdir()} == {"assembly-detail.json", "assembly_drawing.pdf", "assembly_drawing.svg",
                                                             "drawing_manifest.json", "drawing_result.json", PREVIEW_ARCHIVE_NAME}
    # the package and the detail lie beside the drawing files (``assembly verify-drawing`` reports them as not drawing files)
    assert verify_assembly_drawing(drawing_output_files(tmp_path / "out", known_only=True)) == ()
    assert unrelated_output_entries(tmp_path / "out") == ["assembly-detail.json", PREVIEW_ARCHIVE_NAME]
    svg = outcome.drawing.files["assembly_drawing.svg"]
    root = svg_tree(svg)
    meta = metadata(root)
    assert meta["schema_version"] == "4.2" and meta["plan_id"] == rev.plan_id and meta["commercial_resolution_id"] is None
    assert b"Assembly drawing of your design" in svg and b"Customer Purchase BOM" not in svg
    assert b'data-source="DESIGN_BOM"' in svg and b'data-supply="OPENSCI"' in svg
    assert b"generated on your device from your assembly plan" in svg
    assert outcome.summary()["schema_version"] == "4.2"


def test_the_chinese_sheet(reference):
    rev, ctx, _ = reference
    outcome = plan_drawing(rev, None, context=ctx, locale="zh-CN")
    svg = outcome.drawing.files["assembly_drawing.svg"].decode("utf-8")
    assert "您的设计的装配图" in svg and "设计物料清单" in svg and "客户采购清单" not in svg


def test_the_drawing_is_deterministic(reference):
    rev, ctx, _ = reference
    first = plan_drawing(rev, None, context=ctx)
    second = plan_drawing(fx.revision(), None, context=fx.context(fx.revision()))
    assert first.files() == second.files()


def test_a_failed_layout_check_is_refused_or_drawn_as_a_diagnostic_sheet():
    rev, ctx = _intruding()
    with pytest.raises(AssemblyDrawingError) as caught:
        plan_drawing(rev, None, context=ctx)
    assert caught.value.code is DrawingErrorCode.DRAWING_REFUSED_VALIDATION_FAILED
    outcome = plan_drawing(rev, None, context=ctx, diagnostic=True)
    assert outcome.exit_code == EXIT_DIAGNOSTIC and outcome.drawing.result.drawing_status.value == "DIAGNOSTIC_VALIDATION_FAILED"
    assert outcome.drawing.result.schema_version == "4.2"


def test_plan_findings_outside_the_layout_check_refuse_both_modes(reference):
    rev, ctx, _ = reference
    error = f.make_finding(PlanFindingCodeV1.HEIGHT_NOT_REACHABLE, item_ids=("opt-1", "pst-1"), check=FindingCheckV1.PLAN_INTERFACES,
                           basis=FindingBasisV1.FACT, source=FindingSourceV1.CHECK, details={"reachable_axis_height_mm": 100.0})
    assert blocking_findings([error]) == [error]
    for diagnostic in (False, True):
        with pytest.raises(AssemblyPlanError) as caught:
            plan_drawing(rev, None, context=ctx, diagnostic=diagnostic, findings=[error])
        assert caught.value.code is AssemblyPlanErrorCode.PLAN_HAS_FINDINGS
        assert caught.value.details["findings"][0]["code"] == "HEIGHT_NOT_REACHABLE"


def test_a_local_package_is_drawn_only_with_its_own_design_bom(reference):
    rev, ctx, package = reference
    detail = detail_file_bytes(project_plan_detail(package, rev, ctx))
    for bom in (None, {**ctx.design_bom, "bom_digest": "0" * 64}):
        with pytest.raises(AssemblyDrawingError) as caught:
            generate_assembly_drawing(package, detail, design_bom=bom)
        assert caught.value.code is DrawingErrorCode.MISSING_REQUIRED_FACT
    bom = copy.deepcopy(ctx.design_bom)
    drawing = generate_assembly_drawing(package, detail, design_bom=bom)
    assert drawing.result.parts_legend.source == "DESIGN_BOM"


def test_user_hardware_is_refused_before_drawing():
    rev = fx.revision(fx.with_user_post(fx.content()))
    with pytest.raises(AssemblyPlanError) as caught:
        plan_drawing(rev, None, context=fx.context(rev, user=True))
    assert caught.value.code is AssemblyPlanErrorCode.PLAN_USER_HARDWARE_NOT_PROJECTABLE
