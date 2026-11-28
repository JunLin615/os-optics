"""Customer-supplied optical components in the generalized kernel.

A customer-supplied component is an external design anchor (``ExternalRequest``) without an OpenSci support chain.
Its beam category makes the nominal beam visit it like an OpenSci optic (a customer mirror folds the beam toward the
OpenSci optics after it), and its known envelope is reserved: no OpenSci body comes closer than the inter-chain
clearance to it or to the column below it, and the reserved space lies on the board. Its retained DOFs are the
customer's (``customer_supplied``, never delivered by OpenSci hardware).
"""

from __future__ import annotations

import dataclasses
import itertools

import pytest

from os_optics.assembly_plan.kernel import (
    AssemblyResolutionRefusal,
    AssemblyResolutionRequestV2,
    DofRequest,
    ExternalRequest,
    ReservedEnvelope,
    resolve_assembly,
)
from os_optics.assembly_plan.kernel.geometry import overlap_depth

from kernel_fixture import SQRT_HALF, catalog, lens, look, screen, source

MIRROR_ENVELOPE = ReservedEnvelope(diameter_mm=25.4, z_min_mm=-6.0, z_max_mm=0.0)


def customer_mirror(instance="M1", position=(0.0, 0.0, 0.0), normal=(-SQRT_HALF, SQRT_HALF, 0.0), envelope=MIRROR_ENVELOPE,
                    dofs=()) -> ExternalRequest:
    return ExternalRequest(instance, "mirror", position, look(normal), tuple(DofRequest(a, c) for a, c in dofs),
                           beam_category="MIRROR", size_mm=25.4, envelope=envelope)


def customer_fold(**kwargs) -> AssemblyResolutionRequestV2:
    """Source along +X -> the CUSTOMER's mirror at the origin folding to +Y -> an OpenSci lens 100 mm up the fold."""

    beam_y = look((0.0, 1.0, 0.0))
    externals = kwargs.pop("externals", (source(), customer_mirror(dofs=(("Rx", "MANUAL"),))))
    return AssemblyResolutionRequestV2(
        optics=(lens(position=(0.0, 100.0, 0.0), rotation=beam_y),), externals=externals,
        observations=(screen(position=(0.0, 176.5, 0.0), rotation=beam_y),), **kwargs)


def _reserved_box(r, instance="M1"):
    space = next(s for s in r.reserved if s.instance_id == instance)
    return space.bodies


def test_a_customer_mirror_folds_the_beam_and_its_space_is_reserved():
    r = resolve_assembly(customer_fold(), catalog())
    # the beam visits the customer's mirror and is folded onto the OpenSci lens
    assert r.beam_paths[0].through == ("SRC", "M1", "L1", "focal_plane")
    # only the OpenSci lens gets a support chain; the mirror is an anchor with its own DOFs
    assert [c.instance_id for c in r.chains] == ["L1"]
    assert {p.instance_id for p in r.parts if p.instance_id is not None} == {"L1"}
    assert "M1" in r.external_lab_poses
    m1 = [d for d in r.dofs if d.instance_id == "M1"]
    assert [(d.axis, d.status, d.customer_supplied) for d in m1] == [("Rx", "NOT_DELIVERED", True)]
    # its reserved space: the envelope and the column below it, on the board, clear of every OpenSci body
    (box,) = _reserved_box(r)
    lo_z, hi_z = box.z_range()
    assert lo_z == pytest.approx(0.0) and hi_z == pytest.approx(r.external_lab_poses["M1"].position[2] + 25.4 / 2)
    margin = catalog().policy.board_edge_margin_mm
    for x, y in box.plan_corners():
        assert margin - 1e-6 <= x <= r.board.board.size_x_mm - margin + 1e-6
        assert margin - 1e-6 <= y <= r.board.board.size_y_mm - margin + 1e-6
    clearance = catalog().policy.inter_chain_clearance_mm
    for part in r.parts:
        for body in part.bodies:
            assert overlap_depth(box.inflated(clearance / 2), body.inflated(clearance / 2)) <= 0.0, part.slot_key
    # the footprint covers the reserved space
    x0, y0 = r.board.footprint_min_mm
    x1, y1 = r.board.footprint_max_mm
    bx0, by0, _, bx1, by1, _ = box.bounds
    assert x0 <= bx0 + 1e-6 and y0 <= by0 + 1e-6 and bx1 <= x1 + 1e-6 and by1 <= y1 + 1e-6
    assert "customer-supplied component" in next(c.detail for c in r.checks if c.kind == "INTER_CHAIN_CLEARANCE")


def test_without_an_envelope_the_component_is_an_anchor_only():
    externals = (source(), dataclasses.replace(customer_mirror(), envelope=None))
    r = resolve_assembly(customer_fold(externals=externals), catalog())
    assert r.beam_paths[0].through == ("SRC", "M1", "L1", "focal_plane")
    assert r.reserved == ()
    assert "customer-supplied component" not in next(c.detail for c in r.checks if c.kind == "INTER_CHAIN_CLEARANCE")


def test_an_unknown_component_takes_no_part_in_the_beam_trace():
    externals = (source(), ExternalRequest("X1", "other_optical", (300.0, 0.0, 0.0), look((1.0, 0.0, 0.0)), ()))
    request = AssemblyResolutionRequestV2(optics=(lens(),), externals=externals, observations=(screen(),))
    r = resolve_assembly(request, catalog())
    assert r.beam_paths[0].through == ("SRC", "L1", "focal_plane")
    assert "X1" in r.external_lab_poses and r.reserved == ()


def test_opensci_hardware_is_never_placed_through_a_reserved_space():
    """A customer lens right behind an OpenSci lens: its reserved space occupies where the OpenSci support would
    otherwise not care. With an envelope that is too large to clear, the design is refused naming the customer part."""

    big = ReservedEnvelope(diameter_mm=25.4, z_min_mm=-20.0, z_max_mm=20.0)
    external = ExternalRequest("C1", "lens", (12.0, 0.0, 0.0), look((1.0, 0.0, 0.0)), (), beam_category="LENS", size_mm=25.4,
                               envelope=big)
    request = AssemblyResolutionRequestV2(optics=(lens(),), externals=(source(), external), observations=(screen(),))
    with pytest.raises(AssemblyResolutionRefusal) as refusal:
        resolve_assembly(request, catalog())
    assert refusal.value.code == "MECHANICAL_LAYOUT_CONFLICT"
    assert refusal.value.details["instance_id"] == "L1"
    assert refusal.value.details["with"] == "C1"
    assert refusal.value.details["reason"] == "SUPPORTS_COLLIDE"
    # moved far enough away, the same design resolves and keeps the clearance
    far = dataclasses.replace(external, position_design=(60.0, 0.0, 0.0))
    r = resolve_assembly(dataclasses.replace(request, externals=(source(), far),
                                             observations=(screen(position=(120.0, 0.0, 0.0)),)), catalog())
    (box,) = _reserved_box(r, "C1")
    clearance = catalog().policy.inter_chain_clearance_mm
    assert all(overlap_depth(box.inflated(clearance / 2), b.inflated(clearance / 2)) <= 0.0 for p in r.parts for b in p.bodies)


def test_a_reserved_space_sizes_the_board():
    """A customer mirror far from the OpenSci optic: the board must cover its reserved space."""

    beam_y = look((0.0, 1.0, 0.0))
    near = AssemblyResolutionRequestV2(optics=(lens(position=(0.0, 100.0, 0.0), rotation=beam_y),),
                                       externals=(source(), customer_mirror()),
                                       observations=(screen(position=(0.0, 176.5, 0.0), rotation=beam_y),))
    far = AssemblyResolutionRequestV2(optics=(lens(position=(0.0, 100.0, 0.0), rotation=beam_y),),
                                      externals=(source(position=(-200.0, -250.0, 0.0)), customer_mirror(position=(0.0, -250.0, 0.0))),
                                      observations=(screen(position=(0.0, 176.5, 0.0), rotation=beam_y),))
    a, b = resolve_assembly(near, catalog()), resolve_assembly(far, catalog())
    area = lambda r: r.board.board.size_x_mm * r.board.board.size_y_mm  # noqa: E731
    assert area(b) > area(a)


def test_a_tilted_customer_component_reserves_a_world_aligned_box():
    """A customer window tilted out of the horizontal plane: its reserved box bounds the turned envelope."""

    tilted = look((SQRT_HALF, 0.0, SQRT_HALF), up=(-SQRT_HALF, 0.0, SQRT_HALF))
    external = ExternalRequest("W1", "window", (-100.0, 0.0, 0.0), tilted, (), beam_category="WINDOW", size_mm=25.4,
                               envelope=ReservedEnvelope(diameter_mm=25.4, z_min_mm=0.0, z_max_mm=3.0))
    request = AssemblyResolutionRequestV2(optics=(lens(),), externals=(source(position=(-250.0, 0.0, 0.0)), external),
                                          observations=(screen(),))
    r = resolve_assembly(request, catalog())
    (box,) = _reserved_box(r, "W1")
    assert box.pose.rotation == (1.0, 0.0, 0.0, 0.0)
    x0, y0, z0, x1, y1, z1 = box.bounds
    assert z0 == pytest.approx(0.0) and y1 - y0 == pytest.approx(25.4)
    assert x1 - x0 > 25.4 * SQRT_HALF


def test_customer_supplied_components_keep_the_result_deterministic():
    results = [resolve_assembly(customer_fold(), catalog()) for _ in range(2)]
    a, b = results
    assert [p.pose for p in a.parts] == [p.pose for p in b.parts]
    assert a.reserved == b.reserved
    for x, y in itertools.pairwise(results):
        assert x.board.board.identity == y.board.board.identity
