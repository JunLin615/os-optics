"""Archive prescription, chromatic first-order optics and full-field chief-ray regression."""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from os_optics.local import load_public_catalog, paraxial_analysis, run_optical_project, trace_system_pupil
from os_optics.prescription import PrescriptionImportError, read_prescription_project
from os_optics.prescription.len_reader import read_len


EXAMPLE = Path(__file__).parent / "fixtures" / "len" / "kingslake.len"
SOURCE = EXAMPLE.read_text(encoding="utf-8")
CATALOG = load_public_catalog(EXAMPLE.with_name("catalog.json").read_bytes())


def test_archive_geometry_units_spectrum_and_field():
    rx = read_len(SOURCE)
    assert rx.source_format == ".len"
    assert rx.object.infinite and rx.entrance_pupil_diameter_mm == Decimal("12.5")
    assert rx.fields == ((0, 0), (0, 14)) and rx.field_type == "angle"
    assert rx.wavelengths_nm == tuple(map(Decimal, ("587.720064", "486.263475", "656.448186")))
    assert rx.wavelength_weights == (1, 1, 1) and rx.primary_wavelength_index == 0
    assert [s.label for s in rx.surfaces if s.is_stop] == ["5"]
    assert rx.surfaces[3].radius_mm == Decimal("17.34")
    assert rx.surfaces[5].radius_mm == Decimal("-17.34")
    assert rx.surfaces[-1].thickness_mm == Decimal("89.832")
    assert rx.surfaces[0].material.name == "SK4"
    assert rx.surfaces[2].material.name == "F4"


def test_archive_project_traces_to_reference_foci_and_image_heights(tmp_path):
    project = read_prescription_project(EXAMPLE, catalog=CATALOG)
    saved = tmp_path / "double-gauss.project.json"
    saved.write_text(json.dumps(project.payload()), encoding="utf-8")
    document = json.loads(saved.read_text(encoding="utf-8"))
    pupil = trace_system_pupil(document, source_id='SRC1', pupil_coordinates=[(0, 1)], wavelength_nm=587.720064)
    ray, = pupil['rays']
    assert ray['origin_mm'][1] == pytest.approx(6.25)
    assert ray['observations']
    lenses = [c.instance_id for c in project.components if c.optical_role == "lens"]
    assert len(lenses) == 4 and sum(c.optical_role == "aperture" for c in project.components) == 1
    assert project.observations[0].pose.position_mm == (0, 0, 113.18)
    assert project.observations[0].aperture.radius_mm == 25.172634
    # At each source wavelength, independently propagate y and n*u from (1,0) using the
    # archived Sellmeier coefficients. The first EFL is also given in the archive's Table I.
    expected = [
        (587.720064, 100.003939248, 89.834403227, 24.949460),
        (486.263475, 100.185883347, 90.035266670, 24.945679),
        (656.448186, 100.010052075, 89.833627504, 24.951058),
    ]
    run = run_optical_project(document, catalog=CATALOG).result
    arrivals = run.observations[0].arrivals
    assert len(arrivals) == 6
    for wave, efl, bfl, field_height in expected:
        request = {"components": lenses, "wavelength_nm": wave}
        system = paraxial_analysis(document, request, catalog=CATALOG)["system"]
        assert (system["efl_mm"], system["bfl_mm"]) == pytest.approx((efl, bfl), abs=3e-6)
        on_axis, off_axis = sorted((a for a in arrivals if a.beam.wavelength_nm == wave), key=lambda a: a.uv_mm[1])
        assert on_axis.uv_mm == pytest.approx((0, 0), abs=1e-10)
        # Reference traces intersect each signed sphere, apply Snell refraction and aim the 14-degree
        # chief ray through the stop center at its own wavelength. The native source aims paraxially.
        assert off_axis.uv_mm == pytest.approx((0, field_height), abs=0.0002)
        assert all(a.pupil_rays_arrived == a.pupil_ray_count > 0 for a in (on_axis, off_axis))


@pytest.mark.parametrize(("old", "new", "code", "row", "command"), [
    ("RD 25.907", "TY 1", "UNSUPPORTED_FEATURE", 9, "TY"),
    ("GLA SK4", "GLA MOD X 1.60", "UNSUPPORTED_FEATURE", 10, "GLA"),
    ("AP CHK 8.7071934", "AP 8.7071934", "UNSUPPORTED_FEATURE", 11, "AP"),
    ("AP CHK 8.7071934", "AP CHK nan", "VALUE_INVALID", 11, "AP"),
    ("AP CHK 8.7071934", "AP CHK -1", "VALUE_INVALID", 11, "AP"),
    ("TH 5.083", "TH -5.083", "THICKNESS_INVALID", 12, "TH"),
    ("RD 25.907", "RD 1e1000000", "VALUE_INVALID", 9, "RD"),
    ("WW  1 1 1", "WW 1", "ROW_INVALID", 52, "WW"),
    ("WW  1 1 1", "WW 0 1 1", "VALUE_INVALID", 52, "WW"),
    ("END 10", "END 9", "SURFACE_ORDER_INVALID", 53, "END"),
    ("END 10", "END 10\nNXT", "SURFACE_ORDER_INVALID", 54, "NXT"),
])
def test_rejects_unsupported_or_malformed_input_at_source(old, new, code, row, command):
    with pytest.raises(PrescriptionImportError) as err:
        read_len(SOURCE.replace(old, new, 1))
    problem = err.value.problems[0]
    assert (problem.code, problem.location.row, problem.location.column) == (code, row, command)


def test_units_finite_conjugate_asphere_and_quotes():
    text = '''LEN NEW "one; // quoted design" 0 3
      UNI 10; OBH -0.5; NAO .25; TH 5
      NXT; GLA SK4; RD 4; TH 0.2; AP .8; CC -0.75; AD 0.4; AF 1
      NXT; RD -10; TH 8; AP CHK 0.8
      NXT; END 3 // final image
    '''
    rx = read_len(text, requested_unit="cm")
    assert rx.name == "one; // quoted design" and rx.input_unit == "cm"
    assert rx.object.distance_mm == 50 and rx.object_na == Decimal("0.25")
    assert rx.field_type == "height" and rx.fields == ((0, 0), (0, -5))
    surface = rx.surfaces[0]
    assert surface.is_stop and (surface.radius_mm, surface.thickness_mm, surface.clear_semi_diameter_mm) == (40, 2, 8)
    assert surface.conic == Decimal("-0.75")
    assert surface.aspheric_mm == (Decimal("0.0004"), 0, Decimal("1e-7"))
    with pytest.raises(PrescriptionImportError, match="requested units conflict"):
        read_len(text, requested_unit="mm")
    assert (rx.aperture_location.row, rx.aperture_location.column) == (2, 'NAO')


def test_unknown_historical_glass_names_fail_at_source():
    with pytest.raises(PrescriptionImportError) as err:
        read_prescription_project(EXAMPLE, catalog=load_public_catalog())
    failures = {(p.location.row, p.location.column, p.location.surface) for p in err.value.problems if p.code == "GLASS_UNKNOWN"}
    assert {(10, "GLA", "1"), (19, "GLA", "3")} <= failures
