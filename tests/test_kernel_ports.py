"""Elements placed by their ports in the nominal beam trace.

A cube or polarizing cube splitter, a prism and an off-axis parabolic mirror publish ``IN`` and ``OUT`` ports instead of
a face normal. The kernel's nominal beam enters such an element along ``IN`` and leaves through every ``OUT`` port (a
cube's ``OUT_T`` continues the path, its ``OUT_R`` is a branch), so the arms after it are kept clear of OpenSci
hardware exactly like the arms after a plate splitter or a mirror, and the layout follows the folded path.
"""

from __future__ import annotations


import math
from dataclasses import replace

import pytest

from os_optics.assembly_plan.kernel import (
    AssemblyResolutionRefusal,
    AssemblyResolutionRequestV2,
    ExternalRequest,
    OpticRequest,
    PortRequest,
    ReservedEnvelope,
    resolve_assembly,
)
from os_optics.assembly_plan.kernel.beam import cube_ports, trace_beam_paths
from os_optics.assembly_plan.kernel.geometry import rotate
from os_optics.assembly_plan.kernel.resolve import _incidences

from kernel_fixture import BEAM_X, LENS, SQRT_HALF, catalog, lens, look, mirror, screen, source

EDGE = 25.4
BEAM_Y = look((0.0, 1.0, 0.0))


def customer_cube(position=(0.0, 0.0, 0.0)) -> ExternalRequest:
    """A customer-supplied cube splitter whose IN faces the beam along +X; its OUT_R points to +Y."""

    return ExternalRequest("BS", "splitter", position, look((1.0, 0.0, 0.0)), (), beam_category="PORTS", size_mm=EDGE,
                           envelope=ReservedEnvelope(diameter_mm=EDGE * math.sqrt(2.0), z_min_mm=-EDGE / 2, z_max_mm=EDGE / 2),
                           ports=cube_ports(EDGE))


def customer_plate() -> ExternalRequest:
    """The same split with a plate splitter at 45 degrees: transmits along +X, reflects to +Y."""

    return ExternalRequest("BS", "splitter", (0.0, 0.0, 0.0), look((-SQRT_HALF, SQRT_HALF, 0.0)), (), beam_category="BEAMSPLITTER_PLATE",
                           size_mm=EDGE, envelope=ReservedEnvelope(diameter_mm=EDGE, z_min_mm=-3.0, z_max_mm=0.0))


def split_design(splitter: ExternalRequest, *, blocker_x: float | None = None) -> AssemblyResolutionRequestV2:
    """SRC -> splitter at the origin -> L_T on the transmitted arm (+X) and L_R on the reflected arm (+Y), each with its
    focal plane; ``blocker_x``: an OpenSci lens L_X along +X at (blocker_x, 100) whose hardware stands across the
    reflected arm."""

    optics = [lens("L_T", position=(50.0, 0.0, 0.0)), lens("L_R", position=(0.0, 50.0, 0.0), rotation=BEAM_Y)]
    if blocker_x is not None:
        optics.append(lens("L_X", position=(blocker_x, 100.0, 0.0)))
    return AssemblyResolutionRequestV2(
        optics=tuple(optics), externals=(source(position=(-150.0, 0.0, 0.0)), splitter),
        observations=(screen("focus_T", position=(126.473, 0.0, 0.0)), screen("focus_R", position=(0.0, 126.473, 0.0), rotation=BEAM_Y)))


def _through(paths) -> list[tuple[str, ...]]:
    return [p.through for p in paths]


def test_a_customer_cube_splitter_sends_the_beam_out_of_both_ports():
    request = split_design(customer_cube())
    paths = trace_beam_paths(request.externals, request.optics, request.observations)
    assert _through(paths) == [("SRC", "BS", "L_T", "focus_T"), ("BS", "L_R", "focus_R")]
    # the element's point is the cube centre, where the input meets the coated diagonal
    assert paths[0].points_design[1] == pytest.approx((0.0, 0.0, 0.0))
    assert paths[1].points_design[0] == pytest.approx((0.0, 0.0, 0.0))
    # the same topology as the plate splitter
    plate = split_design(customer_plate())
    assert _through(trace_beam_paths(plate.externals, plate.optics, plate.observations)) == _through(paths)


def test_hardware_across_the_reflected_arm_of_a_cube_is_refused_like_behind_a_plate():
    outcomes = {}
    for name, splitter in (("plate", customer_plate()), ("cube", customer_cube())):
        with pytest.raises(AssemblyResolutionRefusal) as refused:
            resolve_assembly(split_design(splitter, blocker_x=-6.0), catalog())
        outcomes[name] = (refused.value.code, refused.value.details["instance_id"], refused.value.details["reason"],
                          refused.value.details["with"])
    assert outcomes["cube"] == outcomes["plate"] == ("MECHANICAL_LAYOUT_CONFLICT", "L_X", "HARDWARE_BLOCKS_BEAM", "L_R")


def test_a_resolved_cube_design_keeps_both_arms_in_the_realization():
    r = resolve_assembly(split_design(customer_cube()), catalog())
    assert _through(r.beam_paths) == [("SRC", "BS", "L_T", "focus_T"), ("BS", "L_R", "focus_R")]
    assert next(c.status for c in r.checks if c.kind == "BEAM_PATH_CLEARANCE") == "PASS"


def right_angle_prism(position=(0.0, 0.0, 0.0)) -> ExternalRequest:
    """A fold prism (the catalog's right-angle convention, leg ``a``: IN through the bottom leg along +Z, OUT through
    the side leg along +X) turned so that it takes the beam along +X and folds it to +Y."""

    a = EDGE
    ports = (PortRequest("IN", (-a / 6.0, 0.0, -a / 3.0), (0.0, 0.0, 1.0)), PortRequest("OUT", (a / 3.0, 0.0, a / 6.0), (1.0, 0.0, 0.0)))
    return ExternalRequest("P1", "prism", position, look((1.0, 0.0, 0.0)), (), beam_category="PORTS", size_mm=a,
                           envelope=ReservedEnvelope(diameter_mm=a * 1.5, z_min_mm=-a / 3.0, z_max_mm=2.0 * a / 3.0), ports=ports)


def prism_fold() -> AssemblyResolutionRequestV2:
    """SRC on the prism's IN line -> the prism folds to +Y at its hypotenuse centre -> OpenSci lens -> focal plane."""

    a = EDGE
    hyp = (a / 6.0, -a / 6.0, 0.0)  # the hypotenuse centre in the design frame
    return AssemblyResolutionRequestV2(
        optics=(lens("L1", position=(hyp[0], 100.0, 0.0), rotation=BEAM_Y),),
        externals=(source(position=(-150.0, hyp[1], 0.0)), right_angle_prism()),
        observations=(screen("focus", position=(hyp[0], 176.5, 0.0), rotation=BEAM_Y),))


def test_a_fold_prism_turns_the_beam_at_its_hypotenuse():
    request = prism_fold()
    (path,) = trace_beam_paths(request.externals, request.optics, request.observations)
    assert path.through == ("SRC", "P1", "L1", "focus")
    assert path.points_design[1] == pytest.approx((EDGE / 6.0, -EDGE / 6.0, 0.0))


def test_a_prism_fold_is_laid_on_the_hole_grid_like_a_mirror_fold():
    r = resolve_assembly(prism_fold(), catalog())
    assert r.beam_paths[0].through == ("SRC", "P1", "L1", "focus")
    # not turned off the grid: the design's +X and +Y stay board axes
    x = rotate(r.design_yaw, (1.0, 0.0, 0.0))
    assert min(abs(abs(x[0]) - 1.0), abs(abs(x[1]) - 1.0)) == pytest.approx(0.0, abs=1e-9)


def test_hardware_on_a_prism_fold_arm_is_refused():
    blocked = prism_fold()
    a = EDGE
    blocker = lens("L_X", position=(a / 6.0 - 6.0, 60.0, 0.0))  # along +X, its hardware across the folded arm
    request = AssemblyResolutionRequestV2(optics=(*blocked.optics, blocker), externals=blocked.externals,
                                          observations=blocked.observations)
    with pytest.raises(AssemblyResolutionRefusal) as refused:
        resolve_assembly(request, catalog())
    assert refused.value.details["instance_id"] == "L_X"
    assert refused.value.details["reason"] == "HARDWARE_BLOCKS_BEAM"


def test_an_off_axis_parabola_folds_the_beam_at_its_aperture_centre():
    oap = ExternalRequest("OAP", "mirror", (0.0, 0.0, 0.0), look((-1.0, 0.0, 0.0)), (), beam_category="PORTS", size_mm=EDGE,
                          ports=(PortRequest("IN", (0.0, 0.0, 0.0), (0.0, 0.0, -1.0)), PortRequest("OUT", (0.0, 0.0, 0.0), (-1.0, 0.0, 0.0))))
    (path,) = trace_beam_paths((source(), oap), (lens("L1", position=(0.0, 60.0, 0.0), rotation=BEAM_Y),), ())
    assert path.through == ("SRC", "OAP", "L1")
    assert path.points_design[1] == pytest.approx((0.0, 0.0, 0.0))


def test_an_offset_return_gives_the_entry_and_the_exit_point():
    """A retro-reflector returns the beam on a parallel line: the element is on the path where the beam enters and where
    it leaves."""

    retro = ExternalRequest("RR", "prism", (0.0, 0.0, 0.0), look((1.0, 0.0, 0.0)), (), beam_category="PORTS", size_mm=40.0,
                            ports=(PortRequest("IN", (-15.0, 0.0, 0.0), (0.0, 0.0, 1.0)), PortRequest("OUT", (15.0, 0.0, 0.0), (0.0, 0.0, -1.0))))
    # local X is design +Y: IN at y = -15 along +X, OUT at y = +15 along -X
    (path,) = trace_beam_paths((source(position=(-200.0, -15.0, 0.0)), retro), (), (screen("back", position=(-100.0, 15.0, 0.0),
                                                                                         rotation=look((-1.0, 0.0, 0.0))),))
    assert path.through == ("SRC", "RR", "RR", "back")
    assert path.points_design[1] == pytest.approx((0.0, -15.0, 0.0))
    assert path.points_design[2] == pytest.approx((0.0, 15.0, 0.0))


CUBE = replace(LENS, optical_sku="OS-OPT-TEST-BS-CUBE-A25", category="BEAMSPLITTER_CUBE", shape="CUBE", size_mm=EDGE,
               thickness_mm=EDGE, axial_min_mm=-EDGE / 2, axial_max_mm=EDGE / 2)


def test_an_opensci_cube_splitter_branches_by_the_cube_convention():
    cube = OpticRequest("BS", CUBE.optical_sku, "splitter", (0.0, 0.0, 0.0), look((1.0, 0.0, 0.0)), (), CUBE)
    request = split_design(customer_plate())
    paths = trace_beam_paths((request.externals[0],), (cube, *request.optics), request.observations)
    assert _through(paths) == [("SRC", "BS", "L_T", "focus_T"), ("BS", "L_R", "focus_R")]
    # the cube is met along its IN axis: a normal incidence, not the 90 degree turn of its reflected output
    assert _incidences((cube,), paths)["BS"].degrees == pytest.approx(0.0, abs=1e-6)


def test_a_double_pass_through_a_cube_and_a_quarter_wave_plate_routes_the_return_out_of_the_fourth_face():
    """SRC -> PBS (IN along +X) -> QWP -> mirror at normal incidence: the beam returns along itself, passes the plate again
    (a second visit, backwards), enters the cube backwards through OUT_T and leaves both back toward the source (IN
    reversed, the transmitted share) and through the fourth face along -Y (the mirror image of OUT_R: the reflected
    share), where a detector sees the isolated return. Without the mirror's return nothing is revisited."""

    qwp = OpticRequest("QWP", LENS.optical_sku, "waveplate", (40.0, 0.0, 0.0), BEAM_X, (), replace(LENS, category="WAVEPLATE"))
    retro = mirror("M", position=(80.0, 0.0, 0.0), normal=(-1.0, 0.0, 0.0))
    detector = screen("return", position=(0.0, -60.0, 0.0), rotation=look((0.0, -1.0, 0.0)))
    paths = trace_beam_paths((source(position=(-100.0, 0.0, 0.0)), customer_cube()), (qwp, retro), (detector,))
    main = paths[0]
    assert main.through == ("SRC", "BS", "QWP", "M", "QWP", "BS")
    assert main.steps[-1] == "SPLIT_PORT:OUT_T"  # back through IN: the transmitted share
    assert main.points_design[-1] == pytest.approx((0.0, 0.0, 0.0))
    branches = {p.path_key: p for p in paths[1:]}
    fourth = [p for p in branches.values() if p.through[-1] == "return"]
    assert len(fourth) == 1
    assert fourth[0].steps[0] == "SPLIT_PORT:OUT_R" and fourth[0].points_design[0] == pytest.approx((0.0, 0.0, 0.0))
    assert fourth[0].points_design[-1] == pytest.approx((0.0, -60.0, 0.0))
    # a tilted mirror (no return along the path): every element is met once
    folded = trace_beam_paths((source(position=(-100.0, 0.0, 0.0)), customer_cube()),
                              (qwp, mirror("M", position=(80.0, 0.0, 0.0), normal=(-SQRT_HALF, SQRT_HALF, 0.0))), ())
    assert all(len(set(p.through)) == len(p.through) for p in folded)
