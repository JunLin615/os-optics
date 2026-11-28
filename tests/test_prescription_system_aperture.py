"""Saved prescription pupils compared with independent signed-surface intersections and Snell refraction."""

import json
from pathlib import Path

import pytest

from os_optics.local import load_project, load_public_catalog, trace_system_pupil
from os_optics.prescription import PrescriptionImportError, read_prescription_project


FIXTURES = Path(__file__).parent / 'fixtures' / 'zmx'
REILEY = FIXTURES / 'US04384765-1.zmx'
MERCADO = FIXTURES / '5000548a.zmx'
ZOOM = FIXTURES / 'zoom' / 'US20130335830_Example01P.zmx'
PRIMARY = 587.721865  # d line in vacuum, 20 °C, 101325 Pa


def variant(directory, original, before, after):
    text = original.read_text('utf-16')
    assert text.count(before) == 1
    path = directory / original.name
    path.write_text(text.replace(before, after), encoding='utf-16')
    return path


def reopened(directory, path, catalog):
    document = read_prescription_project(path, catalog=catalog)
    saved = directory / 'optics.project.json'
    saved.write_text(json.dumps(document.payload()), encoding='utf-8')
    return load_project(saved)


def axial(project, fraction=1, configuration_id=None):
    return trace_system_pupil(project, source_id='SRC1', pupil_coordinates=[(0, fraction)],
                              wavelength_nm=PRIMARY, configuration_id=configuration_id)


@pytest.mark.parametrize(('fnumber', 'radius', 'stop_y', 'specimen_y', 'specimen_sine'), [
    (0.6, 1.5257829202, 3.1723052130, .0000364288, -.8343212812),
    (2.4, .3814457300, .7538057947, .0000040008, -.2081258256),
    (4.8, .1907228650, .3759482274, .0000053090, -.1040624683),
])
def test_reiley_image_f_number_sets_actual_marginal_ray(tmp_path, fnumber, radius, stop_y, specimen_y, specimen_sine):
    path = REILEY if fnumber == .6 else variant(tmp_path, REILEY, 'FNUM 6.0E-1', f'FNUM {fnumber}')
    project = reopened(tmp_path, path, load_public_catalog(FIXTURES / 'microscope-glasses.json'))
    result = axial(project, .999)
    assert result['image_f_number'] == fnumber
    assert result['entrance_pupil_radius_mm'] == pytest.approx(radius, abs=5e-8)
    ray, = result['rays']
    assert ray['origin_mm'][1] == pytest.approx(.999 * radius, abs=5e-8)
    stop = next(s for s in ray['paths'][0]['segments'] if s['end_surface_id'] == 'STOP/stop')
    assert stop['end_mm'][1:] == pytest.approx((stop_y, 32.8244476161923), abs=2e-7)
    lineage = ray['paths'][0]['lineage']
    first_lens_hit = lineage.index('L4/S1:refract')
    stop_hit = lineage.index('STOP/stop:aperture_stop')
    assert (first_lens_hit < stop_hit) == (fnumber == 4.8)
    observation, = ray['observations']
    assert (observation['v_mm'], observation['direction_v']) == pytest.approx((specimen_y, specimen_sine), abs=2e-7)


def test_folded_annular_pupil_blocks_the_center_and_delivers_an_off_center_ray(tmp_path):
    project = reopened(tmp_path, FIXTURES / 'Keck_f13.zmx', None)
    result = trace_system_pupil(project, source_id='SRC1', pupil_coordinates=[(0, 0), (.6, 0)], wavelength_nm=1000.269461)
    center, annulus = result['rays']
    assert center['origin_mm'][0] == 0
    assert not center['observations']
    assert annulus['origin_mm'][0] == pytest.approx(.6 * 10949 / 2)
    assert annulus['paths'][0]['lineage'] == ['M1/S1:reflect', 'M2/S1:reflect', 'observation/image:observe']
    assert annulus['observations']


def test_returning_marginal_ray_must_fit_the_primary_mirror_hole(tmp_path):
    path = variant(tmp_path, FIXTURES / 'Keck_f13.zmx', 'CLAP 1.0E+3 5.48E+3 0', 'CLAP 80 5.48E+3 0')
    with pytest.raises(PrescriptionImportError, match=r'returns to source surface 2 \(M1/S1\)') as err:
        read_prescription_project(path)
    assert err.value.problems[0].location.column == 'ENPD'


def test_zoom_pupil_follows_only_the_selected_position(tmp_path):
    smaller = variant(tmp_path, ZOOM, 'APER   0   3 5.690000000000E+00', 'APER   0   3 3.00')
    catalog = load_public_catalog(ZOOM.with_name('catalog.json'))
    for path in (ZOOM, smaller):
        project = reopened(tmp_path, path, catalog)
        tele = (2.845, 2.8992484076, -.0019615418, -.0533920053) if path == ZOOM else (1.5, 1.5158606163, -.0009582090, -.0281355839)
        for state, (radius, stop_y, image_y, image_sine) in enumerate([
                (1.8, 3.361115009, -.0053760791, -.0971186027), (2.135, 3.1367465575, -.0061446893, -.0769463452), tele], 1):
            measurement = axial(project, configuration_id=f'config-{state}')
            assert measurement['entrance_pupil_radius_mm'] == pytest.approx(radius)
            ray, = measurement['rays']
            stop = next(s for s in ray['paths'][0]['segments'] if s['end_surface_id'] == 'STOP/stop')
            observed, = ray['observations']
            assert (stop['end_mm'][1], observed['v_mm'], observed['direction_v']) == pytest.approx((stop_y, image_y, image_sine), abs=2e-7)


@pytest.mark.parametrize(('na', 'direction_y', 'off_axis_image_y'), [
    (.25, .24993191295, -5.034166868), (.125, .12496595647, -5.109392734),
])
def test_finite_object_na_aims_real_rays_from_each_field(tmp_path, na, direction_y, off_axis_image_y):
    path = MERCADO if na == .25 else variant(tmp_path, MERCADO, 'OBNA 2.5E-1', 'OBNA .125')
    doc = reopened(tmp_path, path, load_public_catalog(FIXTURES / 'microscope-glasses.json'))
    result = axial(doc)
    ray, = result['rays']
    assert ray['origin_mm'] == pytest.approx((0, 0, -12.893))
    assert ray['direction'][1] == pytest.approx(direction_y, abs=1e-9)
    assert result['object_space_index'] * ray['direction'][1] == pytest.approx(na)
    field, = trace_system_pupil(doc, source_id='SRC2', pupil_coordinates=[(0, 1)], wavelength_nm=PRIMARY)['rays']
    assert field['origin_mm'] == pytest.approx((0, .5, -12.893))
    observed, = field['observations']
    assert observed['v_mm'] == pytest.approx(off_axis_image_y, abs=2e-7)


@pytest.mark.parametrize(('original', 'before', 'after', 'catalog_path', 'command', 'state', 'surface'), [
    (REILEY, 'FNUM 6.0E-1', 'FNUM .5', FIXTURES / 'microscope-glasses.json', 'FNUM', None, '9'),
    (ZOOM, 'APER   0   3 5.690000000000E+00', 'APER   0   3 9.4', ZOOM.with_name('catalog.json'), 'APER', 3, '13'),
])
def test_conflicting_aperture_locates_actual_control_record_and_physical_surface(tmp_path, original, before, after, catalog_path, command, state, surface):
    path = variant(tmp_path, original, before, after)
    row = next(i for i, line in enumerate(path.read_text('utf-16').splitlines(), 1) if after in line)
    with pytest.raises(PrescriptionImportError, match=f'source surface {surface} ') as err:
        read_prescription_project(path, catalog=load_public_catalog(catalog_path))
    where = err.value.problems[0].location
    assert (where.row, where.column, where.configuration) == (row, command, state)
