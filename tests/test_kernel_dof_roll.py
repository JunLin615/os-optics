"""the requested retained DOFs of a round optic do not depend on the roll it was drawn with.

A round optic's roll about its own optical axis has no optical or mechanical meaning, so its DOF frame is the drawn
rotation turned to the nearest quarter turn (``dof_frame``): local +X and +Y horizontal and vertical. Before, a requested
axis was matched to a mechanism only when parallel within 1e-9, so a fold mirror drawn with a 0.01, 30 or 45 degree roll
(or a shortest-arc quaternion) lost its tip/tilt mount to a fixed ring, reported ``NO_COMPATIBLE_HARDWARE``.
"""

from __future__ import annotations

import math
from dataclasses import replace

import pytest

from os_optics.assembly_plan.kernel import AssemblyResolutionRequestV2, resolve_assembly
from os_optics.assembly_plan.kernel.geometry import q_mul, q_normalize, rotate

from kernel_fixture import SQRT_HALF, catalog, lens, look, mirror, screen, source

FOLD_NORMAL = (-SQRT_HALF, SQRT_HALF, 0.0)
BEAM_Y = look((0.0, 1.0, 0.0))


def rolled(rotation, degrees: float):
    a = math.radians(degrees) / 2.0
    return q_normalize(q_mul(rotation, (math.cos(a), 0.0, 0.0, math.sin(a))))


def shortest_arc(direction):
    """The quaternion taking +Z onto ``direction`` by the shortest arc (its roll is whatever that arc gives)."""

    z = (0.0, 0.0, 1.0)
    axis = (z[1] * direction[2] - z[2] * direction[1], z[2] * direction[0] - z[0] * direction[2], z[0] * direction[1] - z[1] * direction[0])
    n = math.sqrt(sum(c * c for c in axis))
    angle = math.acos(max(-1.0, min(1.0, sum(a * b for a, b in zip(z, direction)))))
    s = math.sin(angle / 2.0)
    return (math.cos(angle / 2.0), axis[0] / n * s, axis[1] / n * s, axis[2] / n * s)


def fold(rotation, dofs):
    request = replace(mirror(dofs=dofs), rotation_design=rotation)
    return AssemblyResolutionRequestV2(optics=(request,), externals=(source(),),
                                       observations=(screen(position=(0.0, 100.0, 0.0), rotation=BEAM_Y),))


def inline_lens(rotation, dofs):
    return AssemblyResolutionRequestV2(optics=(lens(rotation=rotation, dofs=dofs),), externals=(source(),), observations=(screen(),))


def outcome(realization, instance):
    chain = next(c for c in realization.chains if c.instance_id == instance)
    dofs = tuple((d.axis, d.status, d.mechanism, d.lab_direction, d.design_direction, d.not_delivered_reason)
                 for d in realization.dofs if d.instance_id == instance)
    return chain.mount, dofs


UPRIGHT_MIRROR = look(FOLD_NORMAL)
UPRIGHT_LENS = look((1.0, 0.0, 0.0))
#: rolls that turn back to the upright frame, and those that turn to the quarter turn (local +X vertical)
TO_UPRIGHT = (0.01, -0.01, 30.0, 44.99, 45.0, -45.0, 180.0)
TO_QUARTER = (60.0, 89.99, 90.0)


@pytest.mark.parametrize("dofs", [(("Rx", "MANUAL"), ("Ry", "MANUAL")), (("Rx", "MANUAL"),)])
def test_a_fold_mirror_keeps_its_tip_tilt_hardware_at_any_roll(dofs):
    upright = outcome(resolve_assembly(fold(UPRIGHT_MIRROR, dofs), catalog()), "M1")
    assert upright[0] == "OS-MECH-TEST-MOUNT-KIN25" and all(d[1] == "DELIVERED" for d in upright[1])
    for degrees in TO_UPRIGHT:
        assert outcome(resolve_assembly(fold(rolled(UPRIGHT_MIRROR, degrees), dofs), catalog()), "M1")[0] == upright[0], degrees
        if degrees != 180.0:  # a half turn reverses the local axes: same hardware, opposite directions
            assert outcome(resolve_assembly(fold(rolled(UPRIGHT_MIRROR, degrees), dofs), catalog()), "M1") == upright, degrees
    arc = outcome(resolve_assembly(fold(shortest_arc(FOLD_NORMAL), dofs), catalog()), "M1")
    assert arc[0] == "OS-MECH-TEST-MOUNT-KIN25" and all(d[1] == "DELIVERED" for d in arc[1])


def test_a_mirror_pair_of_axes_is_the_same_span_at_a_quarter_turn():
    dofs = (("Rx", "MANUAL"), ("Ry", "MANUAL"))
    upright = resolve_assembly(fold(UPRIGHT_MIRROR, dofs), catalog())
    for degrees in TO_QUARTER:
        turned = resolve_assembly(fold(rolled(UPRIGHT_MIRROR, degrees), dofs), catalog())
        assert outcome(turned, "M1")[0] == "OS-MECH-TEST-MOUNT-KIN25"
        span = {tuple(abs(c) for c in d.lab_direction) for d in turned.dofs}
        assert span == {tuple(abs(c) for c in d.lab_direction) for d in upright.dofs}, degrees


@pytest.mark.parametrize("dofs", [
    (("Tx", "MANUAL"), ("Ty", "MANUAL")), (("Ty", "MANUAL"),), (("Ry", "MANUAL"),), (("Ty", "MANUAL"), ("Ry", "MANUAL")),
])
def test_a_lens_keeps_its_hardware_and_dof_directions_at_any_roll(dofs):
    upright = outcome(resolve_assembly(inline_lens(UPRIGHT_LENS, dofs), catalog()), "L1")
    quarter = outcome(resolve_assembly(inline_lens(rolled(UPRIGHT_LENS, 90.0), dofs), catalog()), "L1")
    for degrees in (0.01, -0.01, 30.0, 45.0, -45.0):
        assert outcome(resolve_assembly(inline_lens(rolled(UPRIGHT_LENS, degrees), dofs), catalog()), "L1") == upright, degrees
    for degrees in (60.0, 89.99):
        assert outcome(resolve_assembly(inline_lens(rolled(UPRIGHT_LENS, degrees), dofs), catalog()), "L1") == quarter, degrees
    if dofs == (("Tx", "MANUAL"), ("Ty", "MANUAL")):
        assert upright[0] == quarter[0] == "OS-MECH-TEST-MOUNT-XY25"
        assert all(d[1] == "DELIVERED" for d in upright[1] + quarter[1])


def test_the_dof_frame_is_a_quarter_turn_of_the_drawn_rotation():
    from os_optics.assembly_plan.kernel import dof_frame

    for degrees, expected in ((0.0, 0.0), (0.01, 0.0), (30.0, 0.0), (45.0, 0.0), (-45.0, 0.0), (60.0, 90.0), (135.0, 180.0),
                              (-135.0, 180.0), (90.0, 90.0)):
        frame = dof_frame(rolled(UPRIGHT_LENS, degrees))
        assert rotate(frame, (0.0, 0.0, 1.0)) == pytest.approx(rotate(UPRIGHT_LENS, (0.0, 0.0, 1.0)), abs=1e-12)
        x = rotate(frame, (1.0, 0.0, 0.0))
        want = rotate(rolled(UPRIGHT_LENS, expected), (1.0, 0.0, 0.0))
        assert x == pytest.approx(want, abs=1e-9), degrees
    assert dof_frame(UPRIGHT_LENS) == UPRIGHT_LENS  # a quarter turn is returned unchanged
