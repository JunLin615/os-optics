"""An independent first-order pupil calculation and signed field checks for native alignment probes."""
from dataclasses import replace
from decimal import Decimal as D
import math

import pytest

from os_optics._vendor.shared_contracts.optical_design import OpticalComponentInstanceV1
from os_optics.local import run_optical_project
from os_optics.prescription import PrescriptionImportError, import_prescription, read_prescription_project
from os_optics.prescription.errors import SourceLocation
from os_optics.prescription.illumination import sources


@pytest.fixture(scope="module")
def lens():
    # A convex front face, plane rear face and an air-spaced stop, in vacuum.
    return import_prescription('''#! pressure_pa = 0
surface,radius,thickness,material,semi_diameter
OBJ,,100,,
1,20,3,G,4
2,,8,,4
STO,,20,,2
IMA,,,,
[materials]
name,kind,index,min_nm,max_nm
G,CONSTANT_INDEX,1.5,450,700
''', glass_source="inline")


def probes(lens, **changes):
    result = sources(replace(lens, prescription=replace(lens.prescription, **changes)))
    return [OpticalComponentInstanceV1.model_validate(c) for c in result]


def slopes(component):
    w, x, y, z = component.nominal_pose.quaternion_wxyz
    dx, dy, dz = 2 * (x*z + y*w), 2 * (y*z - x*w), 1 - 2 * (x*x + y*y)
    return dx/dz, dy/dz


# At the stop, a ray (y,u) on the first vertex plane reaches y + (3/1.5 + 8)*(u - 0.5/20*y).
# The entrance pupil is the apparent stop in object space, at z=(3/1.5 + 8)/(1 - 10*0.5/20).
PUPIL_Z = 10 / 0.75
FIELD_LOCATION = SourceLocation(row=8, column="FTYP", section="system")
WAVE_LOCATION = SourceLocation(row=10, column="WAVM", section="system")


def test_finite_angle_uses_primary_entrance_pupil_and_signed_incoming_slopes(lens):
    axial, angled = probes(lens, fields=((D(0), D(0)), (D(-11), D(7))), image_f_number=D('2.8'),
                          field_weights=(D(40), D(5)))
    assert (axial.instance_id, angled.instance_id) == ('SRC1', 'SRC2')
    assert 'Field merit weight 40.' in axial.custom_part.description
    assert 'Field merit weight 5.' in angled.custom_part.description
    assert axial.source_model.power_w == angled.source_model.power_w == 0.001
    assert axial.nominal_pose.position_mm == (0, 0, -100)
    expected = [math.tan(math.radians(v)) for v in (-11, 7)]
    assert slopes(angled) == pytest.approx(expected)
    assert angled.nominal_pose.position_mm == pytest.approx([*(-(100 + PUPIL_Z)*s for s in expected), -100])
    assert 'image-space f/2.8' in angled.custom_part.description


def test_signed_height_is_object_position_and_aperture_setting_is_identified(lens):
    field, = probes(lens, fields=((D('0.3'), D('-0.4')),), field_type='height', object_na=D('0.25'))
    assert field.nominal_pose.position_mm == (0.3, -0.4, -100)
    assert slopes(field) == pytest.approx([-0.3/(100 + PUPIL_Z), 0.4/(100 + PUPIL_Z)])
    assert 'object-space NA 0.25' in field.custom_part.description
    assert field.source_model.model_kind == 'GAUSSIAN_BEAM'


def test_annular_stop_probes_a_clear_radius_without_changing_field_angle(lens):
    rx = lens.prescription
    front, *rest = rx.surfaces
    front = replace(front, is_stop=True, hole_radius_mm=D('2'))
    rest[-1] = replace(rest[-1], is_stop=False)
    point, = probes(lens, fields=((D(0), D('-2')),), surfaces=(front, *rest),
                    object=replace(rx.object, infinite=True, distance_mm=None), entrance_pupil_diameter_mm=D(8))
    x, y, z = point.nominal_pose.position_mm
    dx, dy = slopes(point)
    assert (x - z*dx, y - z*dy) == pytest.approx((3, 0), abs=1e-12)
    assert dy == pytest.approx(math.tan(math.radians(-2)))
    assert 'entrance pupil diameter 8 mm' in point.custom_part.description
    assert z < 0


def test_finite_angular_field_keeps_its_object_point_when_sampling_an_annulus(lens):
    rx = lens.prescription
    front, *rest = rx.surfaces
    front = replace(front, is_stop=True, hole_radius_mm=D('2'))
    rest[-1] = replace(rest[-1], is_stop=False)
    point, = probes(lens, fields=((D(0), D(5)),), surfaces=(front, *rest))
    assert point.nominal_pose.position_mm == pytest.approx((0, -100*math.tan(math.radians(5)), -100))
    assert slopes(point) == pytest.approx((3/100, math.tan(math.radians(5))))


def test_infinite_field_scales_probe_waist_with_prescribed_pupil(lens):
    for diameter in (D('3.6'), D('5.69')):
        point, = probes(lens, object=replace(lens.prescription.object, infinite=True, distance_mm=None),
                        entrance_pupil_diameter_mm=diameter)
        assert point.source_model.waist_radius_mm == pytest.approx(float(diameter)/50)


def test_spectral_lines_keep_native_wavelengths_relative_weights_and_primary(lens):
    field, = probes(lens, wavelengths_nm=tuple(map(D, ('486.25', '587.7', '656.5', '486.25'))),
                    wavelength_weights=tuple(map(D, (1, 3, 0, 1))), primary_wavelength_index=1)
    assert [(line.wavelength_nm, line.power_w) for line in field.source_model.spectral_lines] == [(486.25, .0004), (587.7, .0006)]
    assert 'Primary wavelength 587.7 nm in vacuum' in field.custom_part.description


@pytest.mark.parametrize(('changes', 'where'), [
    ({'field_type': 'image_height', 'fields': ((D(0), D(1)),)}, FIELD_LOCATION),
    ({'field_type': 'height', 'fields': ((D(1), D(0)),), 'object': None}, FIELD_LOCATION),
    ({'field_type': 'angle', 'fields': ((D(-91), D(0)),)}, FIELD_LOCATION),
    ({'wavelengths_nm': (D(750),)}, WAVE_LOCATION),
    ({'wavelengths_nm': (D(600),), 'wavelength_weights': (D(0),)}, WAVE_LOCATION),
])
def test_unsupported_or_untraceable_field_reports_prescription_location(lens, changes, where):
    with pytest.raises(PrescriptionImportError) as err:
        probes(lens, field_location=FIELD_LOCATION, wavelength_location=WAVE_LOCATION, **changes)
    assert err.value.problems[0].location == where


def test_virtual_object_is_located_on_object_surface(lens):
    with pytest.raises(PrescriptionImportError) as err:
        probes(lens, object=replace(lens.prescription.object, distance_mm=D(-10)))
    assert err.value.problems[0].location == lens.prescription.object.location


@pytest.mark.parametrize('object_distance', ['INFINITY', '1', '10'])
def test_single_mirror_observation_records_only_returning_light(tmp_path, object_distance):
    design = tmp_path / 'single.zmx'
    design.write_text(f'''MODE SEQ
ENPD 8
WAVM 1 0.6 1
SURF 0
DISZ {object_distance}
SURF 1
STOP
GLAS MIRROR
DIAM 5
DISZ -2
SURF 2
DIAM 2 0
''', encoding='utf-8')
    if object_distance == '10':
        with pytest.raises(PrescriptionImportError, match='reflected image observation') as err:
            read_prescription_project(design)
        assert err.value.problems[0].location.surface == '2'
        return
    project = read_prescription_project(design)
    assert -2 < project.components[0].nominal_pose.position_mm[2] < 0
    if object_distance == '1':
        assert project.components[0].nominal_pose.position_mm[2] == -1
    arrival, = run_optical_project(project.payload()).result.observations[0].arrivals
    assert arrival.beam.direction == (0, 0, -1)
