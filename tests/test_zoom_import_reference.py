"""First-order reference and native ray measurements for a published three-position zoom."""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from os_optics.local import evaluate_project, load_project, load_public_catalog
from os_optics.prescription import read_prescription_project


FIXTURE = Path(__file__).parent / 'fixtures' / 'zmx' / 'zoom'
SOURCE = FIXTURE / 'US20130335830_Example01P.zmx'
CATALOG = FIXTURE / 'catalog.json'
HEIGHT_MM = 0.001


def _source_transfer(state, air_um, materials):
    """Trace a unit-height parallel ray in (height, relative index * slope) coordinates.

    Re-read CURV, DISZ, GLAS and THIC from the original independently of the prescription reader.
    Source and image spaces are air. The even aspheres have zero quadratic deformation.
    """
    surfaces = []
    spacings = {}
    for line in SOURCE.read_text(encoding='utf-16').splitlines():
        words = line.split()
        if not words:
            continue
        key = words[0]
        if key == 'SURF':
            surfaces.append({})
        elif key in {'CURV', 'GLAS', 'DISZ'}:
            surfaces[-1][key] = words[1]
        elif key == 'PARM' and words[1] == '1':
            assert float(words[2]) == 0
        elif key == 'THIC' and int(words[2]) == state:
            spacings[int(words[1])] = float(words[3])

    height, momentum, before, image_z = 1.0, 0.0, 1.0, 0.0
    for number, surf in enumerate(surfaces[1:-1], 1):
        after = 1.0
        if 'GLAS' in surf:
            law = materials[surf['GLAS']]
            if law['kind'] == 'SELLMEIER':
                l2 = air_um**2
                n2 = 1 + sum(float(b)*l2/(l2-float(c)) for b, c in zip(law['b'], law['c_um2'], strict=True))
            else:
                assert law['kind'] == 'POWER_SERIES'
                n2 = sum(float(a)*air_um**p for a, p in zip(law['coefficients'], law['exponents'], strict=True))
            after = math.sqrt(n2)
        momentum -= height * (after - before) * float(surf['CURV'])
        distance = spacings.get(number, float(surf['DISZ']))
        height += distance * momentum / after
        image_z += distance
        before = after
    assert before == 1.0
    return -1 / momentum, image_z, image_z - height / momentum


@pytest.fixture(scope='module')
def reopened_zoom(tmp_path_factory):
    catalog = load_public_catalog(CATALOG)
    project = read_prescription_project(SOURCE, catalog=catalog)
    path = tmp_path_factory.mktemp('zoom') / 'project.json'
    path.write_text(json.dumps(project.payload()), encoding='utf-8')
    reopened = load_project(path)
    assert [c.configuration_id for c in reopened.configurations] == ['config-1', 'config-2', 'config-3']
    assert len(reopened.model_materials) == 11
    assert reopened.observations[0].pose.position_mm[2] == pytest.approx(86.23)  # MNUM selects position 2.
    return reopened


@pytest.mark.parametrize('state', [1, 2, 3])
def test_reopened_zoom_ray_at_each_source_wavelength(reopened_zoom, state):
    configured = reopened_zoom.configured(f'config-{state}')
    stop, = (m.model for m in configured.optical_models if m.model.model_kind == 'APERTURE_STOP')
    assert float(stop.opening_radius_mm) == pytest.approx(4.93)
    assert float(stop.edge_radius_mm) > float(stop.opening_radius_mm)
    project = configured.payload()
    expected_angles = [(0, 25.431, 36.33), (0, 18.305, 26.15), (0, 10.052, 14.36)][state - 1]
    fields = [c for c in project['components'] if c.get('source_model')]
    for field, angle in zip(fields, expected_angles, strict=True):
        qw, qx, qy, qz = field['nominal_pose']['quaternion_wxyz']
        assert (qy, qz) == (0, 0)
        assert math.degrees(-2 * math.atan2(qx, qw)) == pytest.approx(angle, abs=1e-10)
        # The importer samples 2% of the configuration's declared ENPD with each collimated probe.
        enpd = (3.60, 4.27, 5.69)[state - 1]
        assert field['source_model']['waist_radius_mm'] == pytest.approx(0.02 * enpd)

    # Translate the axial source to measure differential optical power at all three wavelengths.
    project['components'] = [c for c in project['components'] if c.get('source_model') is None or c['instance_id'] == 'SRC1']
    source = next(c for c in project['components'] if c['instance_id'] == 'SRC1')
    assert source['nominal_pose']['quaternion_wxyz'] == [1, 0, 0, 0]
    source['nominal_pose']['position_mm'][0] += HEIGHT_MM
    result = evaluate_project(project, outputs=('readings',)).result
    beams = sorted((a.beam for a in result.observations[0].arrivals), key=lambda beam: beam.wavelength_nm)
    materials = {m['material_id']: m['dispersion'] for m in json.loads(CATALOG.read_text(encoding='utf-8'))['materials']}
    for beam, air_um, vacuum_nm in zip(beams, (0.4861327, 0.5875618, 0.6562725),
                                       (486.266176, 587.721865, 656.450687), strict=True):
        assert beam.wavelength_nm == pytest.approx(vacuum_nm, abs=2e-6, rel=0)
        efl, image_z, focus_z = _source_transfer(state, air_um, materials)
        x, _, z = beam.position_mm
        dx, _, dz = beam.direction
        if air_um == 0.5875618:
            assert efl == pytest.approx((18.569494622, 27.821005229, 53.343970415)[state - 1], abs=1e-8, rel=0)
        slope = dx / dz
        assert -HEIGHT_MM / slope == pytest.approx(efl, abs=1e-6, rel=0)
        assert z == pytest.approx(image_z, abs=1e-9, rel=0)
        assert z - x / slope == pytest.approx(focus_z, abs=1e-6, rel=0)
