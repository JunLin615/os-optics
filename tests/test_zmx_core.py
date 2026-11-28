"""Located refusals and coordinate conventions of the sequential .zmx reader."""
from dataclasses import replace
from decimal import Decimal

import pytest

from os_optics.prescription import PrescriptionImportError, _import_models
from os_optics.prescription.ir import ConstantIndexDefinition, MaterialDefinition
from os_optics.prescription.zmx import read_zmx


def prescription(*surfaces, object_gap="INFINITY", image="DIAM 2 0", unit="MM", header=""):
    rows = ["MODE SEQ", f"UNIT {unit}", "FTYP 0 0 2 1 0 0 0", "XFLN 0 3 99", "YFLN 0 4 99",
            "WAVM 1 0.55 1", "WAVM 2 0.8 0", header, "SURF 0", f"DISZ {object_gap}"]
    for index, surface in enumerate(surfaces, 1):
        rows.extend([f"SURF {index}", surface])
    rows.extend([f"SURF {len(surfaces) + 1}", image])
    return "\n".join(rows)


MIRROR = "GLAS MIRROR\nDIAM 5\nDISZ -20"


def test_plane_mirror_has_single_physical_surface_and_backward_output():
    imported = _import_models(read_zmx(prescription(MIRROR)))
    compound, mirror = (doc.model for doc in imported.optical_models)
    assert compound.members[0].pose.quaternion_wxyz == (0, 0, 1, 0)
    assert compound.ports[1].direction == ("0", "0", "-1")
    assert mirror.surfaces[0].traced == "REFLECTED"
    assert mirror.surfaces[0].vertex_z_mm == "0"
    assert imported.report["image_plane_z_mm"] == "-20"
    assert imported.report["counts"]["mirrors"] == 1
    assert not any(w["code"] == "IMAGE_BEFORE_LAST_SURFACE" for w in imported.report["warnings"])


def test_conic_mirror_shape_hole_and_units():
    rx = read_zmx(prescription(MIRROR + "\nCLAP 0.5 2 0\nCURV -.1\nCONI -1\nTYPE EVENASPH\nPARM 2 .001", unit="CM"))
    compound, mirror = (doc.model for doc in _import_models(rx).optical_models)
    face = mirror.surfaces[0]
    assert compound.members[0].pose.quaternion_wxyz == (0, 0, 1, 0)
    assert face.radius_mm == "100" and face.conic == "-1"
    assert face.aspheric_coefficients == ("-0.000001",)
    assert face.holes[0].radius_mm == "5" and face.clear_radius_mm == "20"


@pytest.mark.parametrize(("record", "expected"), [("DIAM 12 0", None), ("DIAM 12 1", 12),
                                                   ("DIAM 12 0\nCLAP 0 4 0", 4)])
def test_explicit_image_aperture_and_active_field_counts(record, expected):
    rx = read_zmx(prescription(MIRROR, image=record, header="FWGN 40 5 1"))
    assert rx.field_weights == (40, 5)
    assert rx.image_semi_diameter_mm == expected
    assert rx.fields == ((0, 0), (3, 4))
    assert len(rx.wavelengths_nm) == 1
    assert rx.wavelengths_nm[0] > 550


def test_signed_dummy_step_and_aperture_plane_keep_distinct_positions():
    rx = read_zmx(prescription("DIAM 8\nDISZ -2", "CLAP 0 1 0\nDIAM 8\nMEMA 3 1\nDISZ 8", MIRROR.replace("-20", "-4")))
    result = _import_models(rx)
    assert [(m["member_id"], m["z_mm"]) for m in result.report["members"]] == [("STOP", "-2"), ("M1", "6")]
    stop = result.optical_models[1].model
    assert (stop.opening_radius_mm, stop.edge_radius_mm) == ("1", "3")
    assert result.report["image_plane_z_mm"] == "2"


def test_automatic_stop_outer_radius_leaves_an_opaque_plate():
    rx = read_zmx(prescription("STOP\nDIAM 2 1\nMEMA 2 0\nDISZ 8", MIRROR.replace("-20", "-4")))
    stop = _import_models(rx).optical_models[1].model
    assert stop.opening_radius_mm == "2" and stop.edge_radius_mm == "5"


def test_propagation_order_after_reflection_is_checked_before_physical_sort():
    rx = read_zmx(prescription(MIRROR.replace("-20", "20"), MIRROR))
    with pytest.raises(PrescriptionImportError) as error:
        _import_models(rx)
    assert error.value.problems[0].location.surface == "2"
    assert "propagation order" in str(error.value)


@pytest.mark.parametrize("outgoing_gap", ["20", "30", "INFINITY"])
def test_outgoing_leg_encountering_solid_primary_requires_a_hole(outgoing_gap):
    rx = read_zmx(prescription(MIRROR, MIRROR.replace("-20", outgoing_gap)))
    with pytest.raises(PrescriptionImportError, match="central opening") as error:
        _import_models(rx)
    assert error.value.problems[0].location == rx.surfaces[0].location


def test_transmitting_lenses_cannot_be_reordered_by_signed_gap():
    front = "GLAS TEST\nDIAM 4\nDISZ 2"
    back = "DIAM 4\nDISZ -8"
    rx = read_zmx(prescription(front, back, front, back))
    definition = MaterialDefinition("TEST", ConstantIndexDefinition(Decimal("1.5")), Decimal(400), Decimal(700), rx.surfaces[0].location)
    with pytest.raises(PrescriptionImportError, match="propagation order") as error:
        _import_models(replace(rx, materials=(definition,)))
    assert error.value.problems[0].location.surface == "3"


def test_unlisted_glass_crossing_on_a_second_leg_is_diagnosed():
    rx = read_zmx(prescription(MIRROR, "GLAS TEST\nDIAM 6\nDISZ -3", "DIAM 6\nDISZ -7"))
    glass = MaterialDefinition("TEST", ConstantIndexDefinition(Decimal("1.5")), Decimal(400), Decimal(700), rx.surfaces[1].location)
    with pytest.raises(PrescriptionImportError, match="multi-pass encounter model") as error:
        _import_models(replace(rx, materials=(glass,)))
    assert error.value.problems[0].location.surface in ("2", "3")


def test_signed_reference_step_cannot_put_stop_behind_real_object():
    rx = read_zmx(prescription("DIAM 8\nDISZ -4", "STOP\nDIAM 2\nMEMA 5 1\nDISZ 8", object_gap="2"))
    with pytest.raises(PrescriptionImportError, match="behind the stated real object"):
        _import_models(rx)


@pytest.mark.parametrize(("record", "column"), [("TYPE TILTSURF", "TYPE"), ("DIFFRACT 1", "DIFFRACT"),
                                                 ("CLAP 3 2", "CLAP"), ("COORDBRK 1", "COORDBRK"),
                                                 ("PARM 2 .1", "PARM"), ("CLAP 0 2 1", "CLAP")])
def test_unsupported_or_malformed_surface_record_is_located(record, column):
    with pytest.raises(PrescriptionImportError) as error:
        read_zmx(prescription(MIRROR + "\n" + record))
    where = error.value.problems[0].location
    assert where.surface == "1" and where.column == column and where.row > 0


def test_aperture_flags_and_saved_solve():
    with pytest.raises(PrescriptionImportError, match="telecentricity"):
        read_zmx(prescription(MIRROR, header="OBNA .2 1"))
    rx = read_zmx(prescription(MIRROR + "\nMAZH 0 0"))
    assert any(w.code == "FROZEN_SURFACE_SOLVE" and w.location.column == "MAZH" for w in rx.warnings)
