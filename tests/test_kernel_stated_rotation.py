"""A design rotation is taken by its direction: a quaternion the Project accepts within 1e-6 of unit length gives the
assembly a unit quaternion gives.

``rotate`` (``q v q*``) scales a vector by ``|q|^2``: a fold mirror's axis stated with ``|q| = 1 + 8e-7`` came out 1.6e-6
long, which survives the 1e-9 snap of a reported DOF direction. The kernel takes every request rotation by ``q / |q|``.
"""

from __future__ import annotations

import math
from dataclasses import replace

import pytest

from os_optics.assembly_plan.kernel import AssemblyResolutionRequestV2, resolve_assembly
from os_optics.assembly_plan.kernel.geometry import q_unit, rotate

from kernel_fixture import SQRT_HALF, catalog, fold_request, look, mirror, screen, source

SCALES = (1.0 - 8e-7, 1.0 + 8e-7)
FOLD_NORMAL = (-SQRT_HALF, SQRT_HALF, 0.0)


def _scaled(q, s):
    return tuple(c * s for c in q)


def _close(a, b, rel=1e-12, floor=1e-12):
    return all(abs(x - y) <= rel * max(abs(x), abs(y)) + floor for x, y in zip(a, b, strict=True))


def _assert_same_assembly(scaled, reference):
    """Every reported (snapped) fact equal; the unrounded nominal beam points within rel 1e-12 + 1e-12 mm (they carry the
    rounding of the unit quaternions, which differ from the scaled ones' in the last bits)."""

    assert replace(scaled, beam_paths=()) == replace(reference, beam_paths=())
    for a, b in zip(scaled.beam_paths, reference.beam_paths, strict=True):
        assert replace(a, points_design=()) == replace(b, points_design=())
        assert all(_close(p, q) for p, q in zip(a.points_design, b.points_design, strict=True))


@pytest.mark.parametrize("s", SCALES)
@pytest.mark.parametrize("q", [look(FOLD_NORMAL), look((1.0, 0.0, 0.0)), (0.9, 0.1, -0.3, 0.2)])
def test_rotate_takes_a_quaternion_by_its_direction(q, s):
    unit = q_unit(q)
    assert abs(math.sqrt(sum(c * c for c in unit)) - 1.0) <= 1e-15
    for v in ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0), (30.0, -12.5, 4.0)):
        assert _close(rotate(_scaled(unit, s), v), rotate(unit, v))
    assert q_unit((0.5, 0.5, 0.5, 0.5)) == (0.5, 0.5, 0.5, 0.5) and q_unit((-2.0, 0.0, 0.0, 0.0)) == (-1.0, 0.0, 0.0, 0.0)


def _fold(s: float) -> AssemblyResolutionRequestV2:
    m = mirror(dofs=(("Rx", "MANUAL"), ("Ry", "MANUAL")))
    m = replace(m, rotation_design=_scaled(m.rotation_design, s))
    src = source()
    src = replace(src, rotation_design=_scaled(src.rotation_design, s))
    beam_y = look((0.0, 1.0, 0.0))
    return AssemblyResolutionRequestV2(optics=(m,), externals=(src,),
                                       observations=(screen(position=(0.0, 100.0, 0.0), rotation=_scaled(beam_y, s)),))


@pytest.mark.parametrize("s", SCALES)
def test_a_request_states_its_rotations_by_direction(s):
    request = _fold(s)
    for rotation in (request.optics[0].rotation_design, request.externals[0].rotation_design, request.observations[0].rotation_design):
        assert abs(math.sqrt(sum(c * c for c in rotation)) - 1.0) <= 1e-15
    assert _close(request.optics[0].rotation_design, _fold(1.0).optics[0].rotation_design)


@pytest.mark.parametrize("s", SCALES)
def test_an_in_tolerance_design_rotation_resolves_to_the_unit_rotation_assembly(s):
    reference = resolve_assembly(_fold(1.0), catalog())
    scaled = resolve_assembly(_fold(s), catalog())
    for a, b in zip(scaled.dofs, reference.dofs, strict=True):
        assert (a.instance_id, a.axis, a.status, a.mechanism) == (b.instance_id, b.axis, b.status, b.mechanism)
        assert a.lab_direction == b.lab_direction and a.design_direction == b.design_direction
        # unit within the 6-decimal rounding of a reported direction (the stated length gave 1 + 1.6e-6)
        assert all(abs(math.sqrt(sum(c * c for c in d)) - 1.0) <= 1e-6 for d in (a.lab_direction, a.design_direction) if d is not None)
    assert [c.mount for c in scaled.chains] == [c.mount for c in reference.chains]
    _assert_same_assembly(scaled, reference)


@pytest.mark.parametrize("s", SCALES)
def test_the_reference_fold_design_resolves_alike_with_scaled_rotations(s):
    reference = fold_request()

    def scaled_request(request):
        return replace(request,
                       optics=tuple(replace(o, rotation_design=_scaled(o.rotation_design, s)) for o in request.optics),
                       externals=tuple(replace(e, rotation_design=_scaled(e.rotation_design, s)) for e in request.externals),
                       observations=tuple(replace(o, rotation_design=_scaled(o.rotation_design, s)) for o in request.observations))

    _assert_same_assembly(resolve_assembly(scaled_request(reference), catalog()), resolve_assembly(reference, catalog()))
