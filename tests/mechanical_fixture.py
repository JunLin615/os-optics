"""Synthetic public mechanical catalog fixtures (``OS-MECH-TEST-*`` / ``USER-MECH-TEST-*``).

Every number here is a round, invented value: nothing is copied from a private family catalog, a supplier datasheet or
the private resolver's test fixture. The fixtures exist so the public Engine's assembly plan code and tests have a
complete mechanical document to work with; ``tests/fixtures/mechanical/README.md`` states what each identity is for.

    python tests/generate_fixtures.py mechanical     # rewrites tests/fixtures/mechanical/*.json

The committed files are the osc1 canonical bytes of the documents built here (``test_mechanical_fixtures`` keeps them
equal), so a change of this module needs a regeneration.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from os_optics.assembly_plan.mechanical import (
    IDENTITY_MODELS,
    PublicMechanicalCatalogV1,
    PublicResolverPolicyV1,
    VisualPrimitiveV1,
    canonical_mechanical_bytes,
    canonical_visual,
    seal_mechanical_document,
)

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "mechanical"
OPENSCI_FIXTURE = "test_mechanical_catalog.v1.json"
USER_FIXTURE = "user_hardware.v1.json"
OPENSCI_VERSION = "m1.0123456789ab"
USER_VERSION = "test-user-hardware.1"

POST_COLOR = (0.78, 0.79, 0.8)
HOLDER_COLOR = (0.2, 0.21, 0.23)
MOUNT_COLOR = (0.25, 0.26, 0.28)
BOARD_COLOR = (0.15, 0.15, 0.16)

POST_DIAMETER = 12.7
POST_LENGTHS = (25, 40, 50, 75, 100)
HOLDER_HEIGHTS = (25, 50, 75)
BOARD_SIZES = ((150, 150), (300, 150), (450, 150), (300, 300))
BOARD_THICKNESS = 12.7

DATASHEET = "DATASHEET"
CAD = "CAD_DERIVED"
RULE = "FAMILY_RULE"
ASSUMED = "PREVIEW_ASSUMPTION"


def _box(lo, hi, finish="BODY") -> dict[str, Any]:
    return {"kind": "BOX", "finish": finish, "min_mm": list(lo), "max_mm": list(hi)}


def _cyl(base, axis, length, radius, finish="BODY", inner=0.0) -> dict[str, Any]:
    return {"kind": "CYLINDER", "finish": finish, "base_mm": list(base), "axis": axis, "length_mm": length,
            "radius_mm": radius, "inner_radius_mm": inner}


def _identity(kind: str, sku: str, name: str, name_zh: str, visual: list[dict[str, Any]], color, basis: dict[str, str],
              *, profile: tuple[str, ...] = (), description: str | None = None, description_zh: str | None = None,
              **facts: Any) -> dict[str, Any]:
    """One identity in the generator's form (validated by the model; defaults explicit, None omitted)."""

    primitives = canonical_visual(VisualPrimitiveV1.model_validate(v) for v in visual)
    payload = {
        "kind": kind, "opensci_sku": sku, "display_name": name, "display_name_zh_cn": name_zh,
        "description": description, "description_zh_cn": description_zh,
        "profile": {"kind": "CONSERVATIVE" if profile else "SINGLE", "conservative_parameters": sorted(profile)},
        "basis": basis, "visual": [p.payload() for p in primitives], "color_rgb": list(color), **facts,
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    return IDENTITY_MODELS[kind].model_validate(payload).payload()


def _post(sku: str, diameter: float, length: float, thread: str, *, name: str, name_zh: str,
          basis: dict[str, str] | None = None) -> dict[str, Any]:
    return _identity("POST", sku, name, name_zh, [_cyl((0, 0, 0), "z", length, diameter / 2, "METAL")], POST_COLOR,
                     {"diameter_mm": DATASHEET, "length_mm": DATASHEET, "top_thread": DATASHEET} if basis is None else basis,
                     diameter_mm=diameter, length_mm=length, top_thread=thread)


def posts() -> list[dict[str, Any]]:
    out = [_post(f"OS-MECH-TEST-POST-D12P7-L{length}", POST_DIAMETER, length, "M4",
                 name=f"Test post 12.7 mm, {length} mm long", name_zh=f"测试立杆 12.7 mm, 长 {length} mm")
           for length in POST_LENGTHS]
    out.append(_post("OS-MECH-TEST-POST-D6-L50", 6.0, 50, "M3", name="Test post 6 mm, 50 mm long",
                     name_zh="测试立杆 6 mm, 长 50 mm"))
    return out


def holders() -> list[dict[str, Any]]:
    out = []
    for height in HOLDER_HEIGHTS:
        body = {"min_mm": [-12.5, -12.5, 0], "max_mm": [40, 12.5, 6]}
        out.append(_identity(
            "POST_HOLDER", f"OS-MECH-TEST-HOLDER-D12P7-H{height}", f"Test post holder 12.7 mm, {height} mm high",
            f"测试立杆架 12.7 mm, 高 {height} mm",
            [_cyl((0, 0, 0), "z", height, 12.5, "BODY", inner=POST_DIAMETER / 2), _box(body["min_mm"], body["max_mm"])],
            HOLDER_COLOR,
            {"post_diameter_mm": DATASHEET, "height_mm": DATASHEET, "tube_diameter_mm": DATASHEET,
             "insertion_min_mm": RULE, "insertion_max_mm": RULE, "base.slot": CAD, "base.thread": DATASHEET, "envelope": CAD},
            description="Synthetic test holder with an integrated fork base.", description_zh="合成测试立杆架, 带一体式叉形底座。",
            post_diameter_mm=POST_DIAMETER, height_mm=height, tube_diameter_mm=25.0, insertion_min_mm=5.0,
            insertion_max_mm=height - 5.0,
            base={"kind": "INTEGRATED_FORK", "slot_start_mm": 15.0, "slot_end_mm": 35.0, "slot_width_mm": 6.6,
                  "thread": "M6", "body": body}))
    return out


MOUNT_BASIS = {"centre_height_mm": DATASHEET, "envelope": CAD, "holds.size_mm": DATASHEET, "holds.thickness": DATASHEET,
               "holds.thickness_min_mm": DATASHEET, "holds.thickness_max_mm": DATASHEET, "holds.transmissive": DATASHEET,
               "post_thread": DATASHEET}


def _holds(size: float, t_min: float, t_max: float, transmissive: bool, aperture: float | None, retainer: float | None,
           rear: float | None) -> dict[str, Any]:
    holds = {"shape": "ROUND", "size_mm": size, "size_tolerance_mm": 0.1, "thickness_min_mm": t_min, "thickness_max_mm": t_max,
             "transmissive": transmissive, "clear_aperture_mm": aperture, "retainer_depth_mm": retainer, "rear_depth_mm": rear}
    return {k: v for k, v in holds.items() if v is not None}


def _mechanism(kind: str, travel: float, adjustment: str = "FINE_ADJUSTER") -> dict[str, Any]:
    return {"kind": kind, "travel": travel, "control": "MANUAL", "adjustment": adjustment}


def _mount(sku: str, mount_kind: str, name: str, name_zh: str, visual, envelope, holds, basis, *, centre: float = 20.0,
           mechanisms=(), profile: tuple[str, ...] = ()) -> dict[str, Any]:
    return _identity("MOUNT", sku, name, name_zh, visual, MOUNT_COLOR, {**MOUNT_BASIS, **basis}, profile=profile,
                     mount_kind=mount_kind, holds=holds, post_thread="M4", post_fastening=["PLAIN_STUD"],
                     centre_height_mm=centre, envelope={"min_mm": list(envelope[0]), "max_mm": list(envelope[1])},
                     mechanisms=list(mechanisms))


def mounts() -> list[dict[str, Any]]:
    lens_basis = {"holds.clear_aperture_mm": DATASHEET, "holds.retainer_depth_mm": CAD, "holds.rear_depth_mm": CAD}
    return [
        _mount("OS-MECH-TEST-LENS-MOUNT-FIXED-D25", "FIXED", "Test fixed lens mount 25.4 mm", "测试固定透镜架 25.4 mm",
               [_cyl((-5, 0, 20), "x", 10, 16, inner=11), _box((-5, -6, 0), (5, 6, 4))], ((-5, -16, 0), (5, 16, 36)),
               _holds(25.4, 0.0, 8.0, True, 22.0, 1.0, 1.0), lens_basis),
        _mount("OS-MECH-TEST-LENS-MOUNT-XY-D25", "XY_TRANSLATION", "Test XY translation lens mount 25.4 mm",
               "测试 XY 平移透镜架 25.4 mm",
               [_box((-4, -22, 0), (4, 22, 42)), _cyl((-14, 0, 20), "x", 28, 15, "DARK", inner=11)],
               ((-14, -22, 0), (14, 22, 42)), _holds(25.4, 0.0, 8.0, True, 22.0, 1.0, 1.0),
               {**lens_basis, "mechanism.TRANSVERSE_TRANSLATION.travel": DATASHEET},
               mechanisms=[_mechanism("TRANSVERSE_TRANSLATION", 1.0)]),
        _mount("OS-MECH-TEST-ROTATION-MOUNT-D25", "ROTATION", "Test rotation mount 25.4 mm", "测试旋转架 25.4 mm",
               [_box((-6, -25, 0), (6, 25, 45)), _cyl((-18, 0, 20), "x", 36, 18, "METAL", inner=11)],
               ((-18, -25, 0), (18, 25, 45)), _holds(25.4, 0.0, 10.0, True, 22.0, 1.0, 1.0),
               {**lens_basis, "mechanism.AXIAL_ROTATION.travel": DATASHEET},
               mechanisms=[_mechanism("AXIAL_ROTATION", 180.0, "CONTINUOUS_CLAMPED")]),
        _mount("OS-MECH-TEST-MIRROR-MOUNT-KIN-D25", "KINEMATIC", "Test kinematic mirror mount 25.4 mm",
               "测试精密反射镜架 25.4 mm",
               [_box((-14, -20, 0), (-8, 20, 40)), _box((-8, -20, 0), (0, 20, 40), "DARK")], ((-14, -20, 0), (0, 20, 40)),
               _holds(25.4, 3.0, 12.0, False, None, 0.0, None),
               {"holds.retainer_depth_mm": CAD, "mechanism.TIP_TILT.travel": DATASHEET},
               mechanisms=[_mechanism("TIP_TILT", 3.0)], profile=("envelope", "holds.thickness_max_mm")),
        _mount("OS-MECH-TEST-PLATE-MOUNT-KIN-D25-T", "KINEMATIC", "Test kinematic plate mount 25.4 mm, transmissive",
               "测试精密透射平板架 25.4 mm",
               [_box((-6, -8, 0), (6, 8, 5)), _cyl((-6, 0, 20), "x", 12, 20, inner=11)], ((-6, -20, 0), (6, 20, 40)),
               _holds(25.4, 1.0, 6.0, True, None, 1.0, 1.0),
               {"holds.clear_aperture_mm": ASSUMED, "holds.retainer_depth_mm": CAD, "holds.rear_depth_mm": CAD,
                "mechanism.TIP_TILT.travel": DATASHEET},
               mechanisms=[_mechanism("TIP_TILT", 3.0)]),
        _mount("OS-MECH-TEST-LENS-MOUNT-FIXED-D50", "FIXED", "Test fixed lens mount 50.8 mm", "测试固定透镜架 50.8 mm",
               [_cyl((-6, 0, 35), "x", 12, 30, inner=23), _box((-6, -8, 0), (6, 8, 5))], ((-6, -30, 0), (6, 30, 65)),
               _holds(50.8, 0.0, 12.0, True, 46.0, 1.0, 1.0), lens_basis, centre=35.0),
    ]


def boards() -> list[dict[str, Any]]:
    basis = {k: DATASHEET for k in ("size_x_mm", "size_y_mm", "thickness_mm", "pitch_mm", "edge_offset_mm", "thread", "envelope")}
    return [_identity("BREADBOARD", f"OS-MECH-TEST-BREADBOARD-{x}X{y}-M6G25", f"Test breadboard {x} x {y} mm, M6, 25 mm grid",
                      f"测试面包板 {x} x {y} mm, M6, 25 mm 孔距", [_box((0, 0, 0), (x, y, BOARD_THICKNESS), "DARK")],
                      BOARD_COLOR, basis, size_x_mm=float(x), size_y_mm=float(y), thickness_mm=BOARD_THICKNESS,
                      pitch_mm=25.0, edge_offset_mm=12.5, thread="M6")
            for x, y in BOARD_SIZES]


def opensci_document() -> dict[str, Any]:
    identities = sorted(posts() + holders() + mounts() + boards(), key=lambda i: i["opensci_sku"])
    document = {"schema": "opensci.public_mechanical_catalog", "schema_version": "1.0", "publisher": "OPENSCI",
                "mechanical_version": OPENSCI_VERSION, "frames": "opensci.mechanical_part_frames.v1",
                "policy": PublicResolverPolicyV1().model_dump(mode="json"), "identities": identities}
    return seal_mechanical_document(PublicMechanicalCatalogV1.model_validate({**document, "content_digest": "0" * 64})
                                    .payload())


def user_document() -> dict[str, Any]:
    post = _post("USER-MECH-TEST-POST-D12P7-L60", POST_DIAMETER, 60, "M4", name="Customer post 12.7 mm, 60 mm long",
                 name_zh="客户立杆 12.7 mm, 长 60 mm", basis={})
    document = {"schema": "opensci.public_mechanical_catalog", "schema_version": "1.0", "publisher": "USER",
                "mechanical_version": USER_VERSION, "frames": "opensci.mechanical_part_frames.v1", "identities": [post]}
    return seal_mechanical_document(PublicMechanicalCatalogV1.model_validate({**document, "content_digest": "0" * 64})
                                    .payload())


def fixture_bytes() -> dict[str, bytes]:
    """File name -> the exact committed bytes (osc1 canonical)."""

    return {OPENSCI_FIXTURE: canonical_mechanical_bytes(opensci_document()),
            USER_FIXTURE: canonical_mechanical_bytes(user_document())}


def write_fixtures(directory: Path = FIXTURE_DIR) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for name, data in fixture_bytes().items():
        path = directory / name
        path.write_bytes(data)
        written.append(path)
    return written


def fixture_path(name: str = OPENSCI_FIXTURE) -> Path:
    return FIXTURE_DIR / name


def fixture_document(name: str = OPENSCI_FIXTURE) -> dict[str, Any]:
    """The committed fixture parsed as JSON (a fresh, mutable copy)."""

    return json.loads(fixture_path(name).read_bytes())
