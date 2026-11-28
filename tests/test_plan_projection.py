"""Plan revision -> LOCAL_PLAN Assembly Preview Package (``os_optics.assembly_plan.projection`` and ``glb``): part refs
are the plan's item ids, the binding names the plan revision and its design BOM, design BOM lines are the parts' line
refs, anchors carry the customer's own parts (a declared envelope included), an unresolved chain becomes an unrealized
anchor, the user's own hardware is refused, and the bytes are deterministic. Synthetic data only (``OS-MECH-TEST-*``)."""

from __future__ import annotations

import copy

import pytest

import plan_projection_fixture as fx
from os_optics._vendor.shared_contracts.assembly_preview import GLB_GENERATOR, parse_glb, verify_assembly_preview_package
from os_optics.assembly import validate_assembly_layout
from os_optics.assembly_plan import glb
from os_optics.assembly_plan.contract import AssemblyPlanError, AssemblyPlanErrorCode
from os_optics.assembly_plan.projection import (OMITTED_UNRESOLVED_CHAIN, design_bom_lines, envelope_cylinder,
                                                metric_designation, project_plan, project_plan_package)


@pytest.fixture(scope="module")
def reference():
    rev = fx.revision()
    ctx = fx.context(rev)
    return rev, ctx, project_plan(rev, ctx)


def test_parts_are_the_plan_items_with_their_design_bom_lines(reference):
    rev, ctx, projection = reference
    scene = projection.package.scene
    assert projection.parts == ("brd-1", "hld-1", "mnt-1", "opt-1", "pst-1") and projection.omitted == ()
    assert sorted(p.part_ref for p in scene.parts) == list(projection.parts)
    lines = {i: line["line_id"] for line in ctx.design_bom["lines"] for i in line["item_ids"]}
    for part in scene.parts:
        assert part.commercial_line_ref == lines[part.part_ref]
        item = next(i for i in rev.items if i.item_id == part.part_ref)
        assert part.identity.opensci_sku == item.identity.opensci_sku and part.identity.display_name == item.identity.display_name
        assert tuple(part.pose_in_world.position_mm) == tuple(item.placement.pose_in_lab.position_mm)
    assert scene.part("hld-1").category.value == "BASE" and scene.part("opt-1").design_binding.instance_id == "L1"
    assert scene.part("opt-1").design_binding.catalog_version == "2026-01-01.1"


def test_the_binding_names_the_plan_revision_and_its_design_bom(reference):
    rev, ctx, projection = reference
    binding = projection.package.manifest.binding
    assert binding.basis == "LOCAL_PLAN" and binding.resolution is None and binding.commercial_bom is None
    assert binding.local_plan.plan_id == rev.plan_id and binding.local_plan.revision == rev.revision
    assert binding.local_plan.plan_digest.digest == rev.content_digest
    assert binding.local_plan.design_bom.bom_hash.digest == ctx.design_bom["bom_digest"]
    assert binding.project.project_hash.digest == rev.bindings.project.project_hash.digest
    assert binding.local_validation_hash is None  # no run in the context: no run is bound


def test_the_chain_closes_and_the_layout_check_runs_on_it(reference):
    _, _, projection = reference
    scene = projection.package.scene
    mates = {(m.part_a_ref, m.part_b_ref): m for m in scene.mates}
    assert set(mates) == {("hld-1", "brd-1"), ("mnt-1", "pst-1"), ("opt-1", "mnt-1"), ("pst-1", "hld-1")}
    assert mates[("opt-1", "mnt-1")].interface_designation == "OD25.4-SEAT"
    # datum b in datum a: the seat and the stud coincide, the post's bottom is its insertion depth below the bore top,
    # and the board's top datum (at its corner) is where the holder stands, seen from the holder's bottom
    holder = scene.part("hld-1").pose_in_world.position_mm
    closes = {("opt-1", "mnt-1"): (0.0, 0.0, 0.0), ("mnt-1", "pst-1"): (0.0, 0.0, 0.0), ("pst-1", "hld-1"): (0.0, 0.0, 20.0),
              ("hld-1", "brd-1"): (-holder[0], -holder[1], 0.0)}
    for (a, b), mate in mates.items():
        expected = closes[(a, b)]
        assert tuple(mate.b_in_a.position_mm) == pytest.approx(expected, abs=1e-6)
    validation = validate_assembly_layout(projection.package)
    statuses = {c.check.value: c.status.value for c in validation.checks}
    assert statuses["SUPPORT_ATTACHMENT"] == "PASS" and statuses["PART_COLLISION"] == "PASS"
    assert statuses["BOARD_FIXATION"] == "PASS" and statuses["EXPECTED_ENGAGEMENT"] == "PASS"
    assert scene.breadboard.top_z_mm == 0.0 and scene.breadboard.hole_grid.count == (12, 6)
    assert scene.breadboard.hole_grid.thread_designation == "M6x1.0"
    assert scene.beam_reference.optical_axis_height_mm == fx.LENS_AT[2]
    assert [s.value for s in scene.settings if s.kind.value == "INSERTION_DEPTH"] == ["20"]


def test_dofs_come_from_the_plan_ranges(reference):
    _, _, projection = reference
    dofs = {d.axis.value: d for d in projection.package.scene.dof_implementations}
    assert set(dofs) == {"Tx", "Ty", "Tz"}
    assert dofs["Tx"].mechanism.value == "TRANSLATION_MOUNT" and dofs["Tx"].implemented_by_part_ref == "mnt-1"
    assert (dofs["Tx"].travel_min, dofs["Tx"].travel_max) == (-1.0, 1.0)
    assert dofs["Tz"].status.value == "DELIVERED_AS_MANUAL" and dofs["Tz"].moved_part_ref == "hld-1"
    assert (dofs["Tz"].travel_min, dofs["Tz"].travel_max) == (-10.0, 10.0)
    notes = {(n.code.value, n.dof_axis.value if n.dof_axis else None) for n in projection.package.scene.notes}
    assert ("DOF_MANUAL_ADJUSTMENT", "Tz") in notes and ("EXTERNAL_LIGHT_SOURCE", None) in notes


def test_the_customer_light_source_is_a_customer_supplied_anchor(reference):
    _, _, projection = reference
    anchors = {a.instance_id: a for a in projection.package.scene.beam_reference.design_anchors}
    source = anchors["SRC"]
    assert source.customer_supplied and source.realized_by_part_ref is None
    assert source.customer_supply.reason.value == "CUSTOMER_LIGHT_SOURCE" and source.customer_supply.reserved_envelope is None
    assert anchors["L1"].realized_by_part_ref == "opt-1" and anchors["focal_plane"].anchor_kind.value == "OBSERVATION"
    assert projection.anchors == {"L1": "opt-1", "SRC": "cmp-1"}


def test_a_declared_envelope_is_a_customer_supplied_anchor_the_layout_check_keeps_clear():
    """XR check: a plan-only design anchor (the declared envelope's item id, no Project instance) verifies in the scene,
    the detail and the drawing, and the layout check reserves its space."""

    data = fx.with_declared_envelope(fx.content())
    rev = fx.revision(data)
    projection = project_plan(rev, fx.context(rev))
    anchor = next(a for a in projection.package.scene.beam_reference.design_anchors if a.instance_id == "env-1")
    supply = anchor.customer_supply
    assert anchor.customer_supplied and supply.reason.value == "NOT_IN_CATALOG" and supply.optical_role == "other_optical"
    envelope = supply.reserved_envelope
    assert envelope.basis == "PROJECT_DECLARED_GEOMETRY" and (envelope.z_min_mm, envelope.z_max_mm) == (0.0, 40.0)
    assert envelope.diameter_mm == pytest.approx(2 * (10.0 ** 2 * 2) ** 0.5, abs=1e-6)  # the box's circumscribed cylinder
    assert validate_assembly_layout(projection.package).status.value == "WARNING"
    # moved onto the post, the envelope's space is intruded on by the chain
    entry = fx.item(data, "env-1")
    entry["placement"]["pose_in_lab"]["position_mm"] = [fx.LENS_AT[0] + fx.LENS_HALF_THICKNESS, fx.LENS_AT[1] + 15.0, 0.0]
    rev = fx.revision(data)
    issues = validate_assembly_layout(project_plan_package(rev, fx.context(rev))).issues
    intrusions = [i for i in issues if i.code.value == "RESERVED_SPACE_INTRUSION"]
    assert intrusions and {i.design_instance_id for i in intrusions} == {"env-1"}


def test_an_unresolved_chain_is_an_unrealized_anchor_and_its_hardware_is_omitted():
    rev = fx.revision(fx.with_unresolved_chain(fx.content()))
    projection = project_plan(rev, fx.context(rev))
    assert projection.parts == ("brd-1",)
    assert [(o.item_id, o.reason) for o in projection.omitted] == [("mnt-1", OMITTED_UNRESOLVED_CHAIN)]
    anchor = next(a for a in projection.package.scene.beam_reference.design_anchors if a.instance_id == "L1")
    assert anchor.realized_by_part_ref is None and not anchor.customer_supplied
    notes = {(n.code.value, n.design_instance_id) for n in projection.package.scene.notes}
    assert ("DESIGN_COMPONENT_NOT_SUPPLIED", "L1") in notes
    assert all(d.design_instance_id != "L1" for d in projection.package.scene.dof_implementations)


def test_user_hardware_is_not_projectable():
    rev = fx.revision(fx.with_user_post(fx.content()))
    with pytest.raises(AssemblyPlanError) as caught:
        project_plan(rev, fx.context(rev, user=True))
    assert caught.value.code is AssemblyPlanErrorCode.PLAN_USER_HARDWARE_NOT_PROJECTABLE
    assert caught.value.details == {"item_ids": ["pst-1"]}


def test_the_context_must_be_the_revisions():
    rev = fx.revision()
    ctx = fx.context(rev)
    data = fx.content()
    data["bindings"]["project"]["project_hash"]["digest"] = "0" * 64  # a plan of another Project
    with pytest.raises(AssemblyPlanError) as caught:
        project_plan(fx.revision(data), ctx)
    assert caught.value.code is AssemblyPlanErrorCode.PROJECT_REJECTED
    bom = copy.deepcopy(ctx.design_bom)
    bom["lines"][0]["quantity"] += 1  # the digest no longer matches
    with pytest.raises(AssemblyPlanError):
        project_plan(rev, type(ctx)(**{**ctx.__dict__, "design_bom": bom}))
    with pytest.raises(AssemblyPlanError) as caught:
        project_plan(rev, type(ctx)(**{**ctx.__dict__, "optics": {}}))
    assert caught.value.code is AssemblyPlanErrorCode.MECHANICAL_CATALOG_NOT_AVAILABLE


def test_projection_is_deterministic(reference):
    rev, ctx, projection = reference
    again = project_plan(fx.revision(), fx.context(fx.revision()))
    assert again.package.files == projection.package.files
    assert again.package.archive_bytes() == projection.package.archive_bytes()
    assert verify_assembly_preview_package(dict(projection.package.files), expected_plan_digest=rev.content_digest) == ()


def test_part_glbs_are_the_preview_generator_s_neutral_meshes(reference):
    _, _, projection = reference
    data = projection.package.files["geometry/mnt-1.glb"]
    document, _ = parse_glb(data)
    assert document["asset"] == {"version": "2.0", "generator": GLB_GENERATOR}
    assert document["nodes"] == [{"mesh": 0, "name": "mnt-1"}]
    board = projection.package.scene.part("brd-1")
    # the board's 72 hole markers (8-segment discs) are part of its mesh
    assert board.geometry.triangle_count > 12 * 6 * 8


def test_glb_tessellation_is_exact_and_closed():
    box = glb.tessellate((glb.Primitive(kind="BOX", finish="BODY", min_mm=(0.0, 0.0, 0.0), max_mm=(1.0, 2.0, 3.0)),))
    assert box.triangle_count == 12 and box.bounds_max_mm == (1.0, 2.0, 3.0)
    tube = glb.tessellate((glb.Primitive(kind="CYLINDER", finish="METAL", base_mm=(0.0, 0.0, 0.0), axis="z", length_mm=5.0,
                                         radius_mm=2.0, inner_radius_mm=1.0),))
    assert tube.triangle_count == glb.SEGMENTS * 8
    assert glb.UNIT_CIRCLE[8] == (0.0, 1.0) and glb.UNIT_CIRCLE[16] == (-1.0, -0.0)
    with pytest.raises(ValueError):
        glb.tessellate(())


def test_helpers():
    assert metric_designation("M6") == "M6x1.0" and metric_designation("1/4-20") == "1/4-20"
    from os_optics.assembly_plan.contract import ReservedEnvelopeV1
    assert envelope_cylinder(ReservedEnvelopeV1(shape="CYLINDER", basis="USER_DECLARED", diameter_mm=5.0, z_min_mm=0.0,
                                                z_max_mm=1.0)) == (5.0, 0.0, 1.0)
    with pytest.raises(AssemblyPlanError):
        design_bom_lines({"schema": "os-optics.design_bom", "lines": [], "bom_digest": "0" * 64})
