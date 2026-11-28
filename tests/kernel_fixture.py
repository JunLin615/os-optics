"""A small synthetic mechanical catalog for the assembly kernel's tests (``os_optics.assembly_plan.kernel``).

Round, invented numbers: a post family (one diameter, eight lengths), an integrated-fork holder family (seven
heights), fixed / XY / kinematic / rotation mounts and a board family, as kernel views (``KPost``, ``KHolder``,
``KMount``, ``KBoard``). Identities are ``OS-MECH-TEST-*`` and ``OS-OPT-TEST-*``. Nothing here is a catalog value.
"""

from __future__ import annotations

import math
from dataclasses import replace

from os_optics.assembly_plan.kernel import (
    AssemblyResolutionRequestV2,
    DofRequest,
    ExternalRequest,
    KBoard,
    KernelCatalog,
    KHolder,
    KMount,
    KPost,
    ObservationRequest,
    OpticMechanicsV1,
    OpticRequest,
    ResolverPolicyV1,
)
from os_optics.assembly_plan.mechanical.model import BoxV1, ForkBaseV1, MountHoldsV1, MountMechanismV1, VisualPrimitiveV1

POST_LENGTHS = (25, 30, 40, 50, 75, 100, 150, 200)
HOLDER_HEIGHTS = (25, 30, 40, 50, 75, 100, 150)
BOARD_SIZES = ((150, 100), (200, 100), (150, 150), (200, 200), (300, 150), (450, 150), (300, 300), (600, 300), (600, 600))
SQRT_HALF = math.sqrt(0.5)
#: The distance from the test lens to its focal plane (mm).
FOCUS_MM = 76.5


def _box(lo, hi, finish="BODY"):
    return VisualPrimitiveV1(kind="BOX", finish=finish, min_mm=lo, max_mm=hi)


def post_id(length: float) -> str:
    return f"OS-MECH-TEST-POST-D12P7-L{length:g}"


def holder_id(height: float) -> str:
    return f"OS-MECH-TEST-HOLDER-L{height:g}"


def mount_id(key: str) -> str:
    return f"OS-MECH-TEST-MOUNT-{key}"


def board_id(x: float, y: float) -> str:
    return f"OS-MECH-TEST-BREADBOARD-{x:g}X{y:g}-M6G25"


def post(length: float) -> KPost:
    return KPost(identity=post_id(length), display_name=f"Post {length:g}", basis=(("length_mm", "DATASHEET"),),
                 visual=(VisualPrimitiveV1(kind="CYLINDER", finish="METAL", base_mm=(0, 0, 0), axis="z", length_mm=float(length),
                                           radius_mm=6.35),),
                 diameter_mm=12.7, length_mm=float(length), top_thread="M4")


def holder(height: float) -> KHolder:
    slot = "REVIEWED_REFERENCE" if height == 75 else "FAMILY_RULE"
    return KHolder(identity=holder_id(height), display_name=f"Holder {height:g}",
                   basis=(("base.slot", slot), ("insertion_max_mm", "PREVIEW_ASSUMPTION"), ("insertion_min_mm", "PREVIEW_ASSUMPTION")),
                   visual=(VisualPrimitiveV1(kind="CYLINDER", finish="BODY", base_mm=(0, 0, 0), axis="z", length_mm=float(height),
                                             radius_mm=12.5, inner_radius_mm=6.35),),
                   post_diameter_mm=12.7, height_mm=float(height), tube_diameter_mm=25.0,
                   insertion_min_mm=max(10.0, round(height / 3, 3)), insertion_max_mm=float(height),
                   base=ForkBaseV1(kind="INTEGRATED_FORK", slot_start_mm=20.0, slot_end_mm=45.0, slot_width_mm=6.5, thread="M6",
                                   body=BoxV1(min_mm=(0.0, -12.5, 0.0), max_mm=(50.0, 12.5, 11.0))))


def mount(key: str, kind: str, *, size=25.4, centre=22.0, envelope=((-5.0, -15.0, 0.0), (5.0, 15.0, 37.0)), tmax=7.0, tmin=None,
          transmissive=True, mechanisms=(), seat=None, reference=False, clear_aperture=None, retainer_depth=None,
          rear_depth=None, fastening=None) -> KMount:
    basis = (("centre_height_mm", "REVIEWED_REFERENCE" if reference else "DATASHEET"),
             ("envelope", "REVIEWED_REFERENCE" if reference else "CAD_DERIVED"))
    lo, hi = (tuple(float(c) for c in envelope[0]), tuple(float(c) for c in envelope[1]))
    return KMount(identity=mount_id(key), display_name=key, basis=basis, visual=(_box(lo, hi),), mount_kind=kind,
                  holds=MountHoldsV1(shape="ROUND", size_mm=size, thickness_min_mm=tmin, thickness_max_mm=tmax, transmissive=transmissive,
                                     clear_aperture_mm=clear_aperture, retainer_depth_mm=retainer_depth, rear_depth_mm=rear_depth),
                  post_thread="M4", post_fastening=fastening,
                  fastening_options=(fastening,) if fastening else ("PLAIN_STUD",),
                  centre_height_mm=float(centre), seat_stop_x_mm=seat, envelope=BoxV1(min_mm=lo, max_mm=hi),
                  mechanisms=tuple(mechanisms))


def mechanism(kind: str, travel: float, adjustment: str = "FINE_ADJUSTER") -> MountMechanismV1:
    return MountMechanismV1(kind=kind, travel=travel, control="MANUAL", adjustment=adjustment)


FIXED = mount("FIXED25", "FIXED", seat=4.0, reference=True)
FIXED_ALT = mount("FIXED25B", "FIXED", centre=20.0)
XY = mount("XY25", "XY_TRANSLATION", centre=20.0, envelope=((-6.0, -20.0, 0.0), (6.0, 20.0, 40.0)), tmax=10.0,
           mechanisms=(mechanism("TRANSVERSE_TRANSLATION", 1.0),))
KINEMATIC = mount("KIN25", "KINEMATIC", centre=25.4, envelope=((-15.0, -20.0, 0.0), (3.0, 20.0, 45.0)), tmax=None, tmin=3.0, transmissive=False,
                  mechanisms=(mechanism("TIP_TILT", 4.0),))
ROTATION = mount("ROT25", "ROTATION", centre=27.0, envelope=((-8.0, -22.0, 0.0), (8.0, 22.0, 50.0)), tmax=9.5,
                 mechanisms=(mechanism("AXIAL_ROTATION", 180.0),))
FIXED_50 = mount("FIXED50", "FIXED", size=50.8, centre=39.0, envelope=((-6.0, -30.0, 0.0), (6.0, 30.0, 70.0)), tmax=10.0)
#: a kinematic tip/tilt mount with a through bore (transmissive: lenses, filters and plates may use it), with its
#: recorded clear aperture and bore depths
KINEMATIC_T = mount("KIN25T", "KINEMATIC", centre=25.4, envelope=((-15.0, -20.0, 0.0), (3.0, 20.0, 45.0)), tmax=8.0, tmin=1.0,
                    transmissive=True, mechanisms=(mechanism("TIP_TILT", 4.0),),
                    seat=0.0, clear_aperture=23.0, retainer_depth=0.0, rear_depth=6.0)
#: a rotation mount with a long bore (44 mm): fine on axis, it clips a beam crossing it at 45 degrees
ROTATION_DEEP = mount("ROT25D", "ROTATION", centre=35.0, envelope=((-22.0, -25.0, 0.0), (22.0, 25.0, 60.0)), tmax=12.0,
                      mechanisms=(mechanism("AXIAL_ROTATION", 180.0),))


def board(x: float, y: float) -> KBoard:
    return KBoard(identity=board_id(x, y), display_name=f"Board {x:g} x {y:g}", basis=(("size_x_mm", "DATASHEET"),),
                  visual=(_box((0.0, 0.0, 0.0), (float(x), float(y), 12.7), "DARK"),), size_x_mm=float(x), size_y_mm=float(y),
                  thickness_mm=12.7, pitch_mm=25.0, edge_offset_mm=12.5, thread="M6")


def _cylinder(length, radius, base_z=0.0):
    return (VisualPrimitiveV1(kind="CYLINDER", finish="GLASS", base_mm=(0.0, 0.0, base_z), axis="z", length_mm=length, radius_mm=radius),)


def optic(sku: str, category: str, *, size=25.4, thickness=6.8, axial=(0.0, None), transmissive=True, curved=False) -> OpticMechanicsV1:
    lo = axial[0]
    hi = axial[1] if axial[1] is not None else lo + thickness
    return OpticMechanicsV1(optical_sku=sku, category=category, shape="ROUND", size_mm=size, thickness_mm=thickness, axial_min_mm=lo,
                            axial_max_mm=hi, transmissive=transmissive, visual=_cylinder(hi - lo, size / 2.0, lo),
                            color_rgb=(0.62, 0.82, 0.9) if transmissive else (0.78, 0.8, 0.84), curved=curved)


LENS = optic("OS-OPT-TEST-LENS-D25-F75", "LENS")
MIRROR = optic("OS-OPT-TEST-MIR-FLAT-D25", "MIRROR", thickness=6.0, axial=(-6.0, 0.0), transmissive=False)
LENS_50 = optic("OS-OPT-TEST-LENS-D50", "LENS", size=50.8, thickness=8.0)
ND_FILTER = optic("OS-OPT-TEST-FLT-ND-D25", "FILTER", thickness=2.0)
DICHROIC = optic("OS-OPT-TEST-FLT-DICHROIC-D25", "BEAMSPLITTER_PLATE", thickness=3.2, axial=(-3.2, 0.0))
ODD_OPTIC = optic("OS-OPT-TEST-ODD-D17", "LENS", size=17.0, thickness=3.0)


def catalog(*, mounts=(FIXED, FIXED_ALT, XY, KINEMATIC, ROTATION, FIXED_50), boards=BOARD_SIZES, posts=POST_LENGTHS, holders=HOLDER_HEIGHTS,
            policy: ResolverPolicyV1 | None = None) -> KernelCatalog:
    return KernelCatalog(policy=policy or ResolverPolicyV1(), posts=tuple(post(length) for length in posts),
                         holders=tuple(holder(h) for h in holders), mounts=tuple(mounts),
                         boards=tuple(board(x, y) for x, y in boards))


def renamed(view, identity: str, **changes):
    """``view`` under another identity (and other parameters)."""

    return replace(view, identity=identity, **changes)


# ------------------------------------------------------------------------------------------------ poses


def look(direction, up=(0.0, 0.0, 1.0)):
    """The quaternion taking local +Z onto ``direction`` and local +Y onto ``up`` (orthonormal inputs)."""

    z = direction
    y = up
    x = (y[1] * z[2] - y[2] * z[1], y[2] * z[0] - y[0] * z[2], y[0] * z[1] - y[1] * z[0])
    m = [[x[0], y[0], z[0]], [x[1], y[1], z[1]], [x[2], y[2], z[2]]]
    trace = m[0][0] + m[1][1] + m[2][2]
    if trace > 0:
        s = math.sqrt(trace + 1) * 2
        return (s / 4, (m[2][1] - m[1][2]) / s, (m[0][2] - m[2][0]) / s, (m[1][0] - m[0][1]) / s)
    if m[0][0] > m[1][1] and m[0][0] > m[2][2]:
        s = math.sqrt(1 + m[0][0] - m[1][1] - m[2][2]) * 2
        return ((m[2][1] - m[1][2]) / s, s / 4, (m[0][1] + m[1][0]) / s, (m[0][2] + m[2][0]) / s)
    if m[1][1] > m[2][2]:
        s = math.sqrt(1 + m[1][1] - m[0][0] - m[2][2]) * 2
        return ((m[0][2] - m[2][0]) / s, (m[0][1] + m[1][0]) / s, s / 4, (m[1][2] + m[2][1]) / s)
    s = math.sqrt(1 + m[2][2] - m[0][0] - m[1][1]) * 2
    return ((m[1][0] - m[0][1]) / s, (m[0][2] + m[2][0]) / s, (m[1][2] + m[2][1]) / s, s / 4)


BEAM_X = look((1.0, 0.0, 0.0))
#: a Project-style quaternion: local +Z -> design +X, but local +X points down (-Z)
BEAM_X_EXAMPLE = (SQRT_HALF, 0.0, SQRT_HALF, 0.0)
SCREEN = ((-12.5, -12.5), (12.5, 12.5), (-12.5, 12.5), (12.5, -12.5))


def lens(instance="L1", position=(0.0, 0.0, 0.0), rotation=BEAM_X, dofs=(), mechanics=LENS) -> OpticRequest:
    return OpticRequest(instance, mechanics.optical_sku, "lens", position, rotation, tuple(DofRequest(a, c) for a, c in dofs), mechanics)


def mirror(instance="M1", position=(0.0, 0.0, 0.0), normal=(-SQRT_HALF, SQRT_HALF, 0.0), dofs=()) -> OpticRequest:
    return OpticRequest(instance, MIRROR.optical_sku, "mirror", position, look(normal), tuple(DofRequest(a, c) for a, c in dofs), MIRROR)


def source(instance="SRC", position=(-200.0, 0.0, 0.0), rotation=BEAM_X) -> ExternalRequest:
    return ExternalRequest(instance, "source", position, rotation, ())


def screen(observation="focal_plane", position=(FOCUS_MM, 0.0, 0.0), rotation=BEAM_X) -> ObservationRequest:
    return ObservationRequest(observation, position, rotation, SCREEN)


def reference_request(dofs=(), rotation=BEAM_X, **kwargs) -> AssemblyResolutionRequestV2:
    """The reference design: one lens on a collimated beam along design +X, focal plane at ``FOCUS_MM``."""

    return AssemblyResolutionRequestV2(optics=(lens(rotation=rotation, dofs=dofs),), externals=(source(rotation=rotation),),
                                       observations=(screen(rotation=rotation),), **kwargs)


def fold_request(**kwargs) -> AssemblyResolutionRequestV2:
    """Source along +X -> mirror at the origin folding to +Y -> lens 100 mm up the fold -> focal plane."""

    beam_y = look((0.0, 1.0, 0.0))
    return AssemblyResolutionRequestV2(
        optics=(mirror(dofs=(("Rx", "MANUAL"), ("Ry", "MANUAL"))), lens(position=(0.0, 100.0, 0.0), rotation=beam_y, dofs=(("Tx", "MANUAL"), ("Ty", "MANUAL")))),
        externals=(source(),), observations=(screen(position=(0.0, 100.0 + FOCUS_MM, 0.0), rotation=beam_y),), **kwargs)
