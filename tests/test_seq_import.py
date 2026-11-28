"""Open a published rotationally symmetric asphere and trace its prescribed image plane."""

import json
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

import numpy as np
import pytest

from pydantic import ValidationError

from os_optics._vendor.shared_contracts.design_catalog.optical_catalog import PublicMaterialV1, TabulatedDispersionV1
from os_optics.config import DEFAULT_NUMERICAL_CONFIG
from os_optics.local import load_project
from os_optics.local.lowering import lower_project
from os_optics.local.paraxial import paraxial_analysis
from os_optics.optics.materials.group import group_dispersion
from os_optics.prescription import PrescriptionErrorCode as E, PrescriptionImportError, read_prescription_project
from os_optics.prescription.seq import read_seq
from os_optics.results.imaging_trace import LaunchGrid, trace_fields

ASPHERE = Path(__file__).parent / 'fixtures' / 'seq' / 'asphere.seq'


def test_published_asphere_preserves_apertures_profile_and_material_table():
    text = ASPHERE.read_bytes().decode('utf-8')
    rx = read_seq(text)
    assert rx.source_sha256 == sha256(text.encode()).hexdigest() == 'ab8131f445b5161928d98060d7749edc6fb5a922a5c902a9d360f7a21b2e26ce'
    assert rx.source_format == '.seq' and rx.object.infinite
    assert rx.wavelengths_nm == (Decimal('656.450687'), Decimal('587.721865'), Decimal('486.266176'))
    assert rx.primary_wavelength_index == 1 and rx.entrance_pupil_diameter_mm == 12
    project = read_prescription_project(ASPHERE)
    assert [c.optical_role.value for c in project.components] == ['source', 'lens']
    assert project.observations[0].pose.position_mm[2] == pytest.approx(16.64603127272)
    profile = project.optical_models[0].model
    assert [s.radius_mm for s in profile.surfaces] == ['9.93', '-48.3']
    assert [s.clear_radius_mm for s in profile.surfaces] == ['6', '6']
    assert profile.edge_radius_mm == '6'
    assert profile.surfaces[0].conic == '-0.71' and rx.surfaces[0].is_stop
    assert profile.media == ('E48',)
    glass = project.model_materials[0]
    assert glass.index_reference.index == 'RELATIVE_TO_AIR'
    curve = glass.dispersion
    assert curve.kind == 'TABULATED' and len(curve.wavelength_nm) == 20
    assert curve.wavelength_nm[7] == '587.6' and curve.refractive_index[7] == '1.53116'
    table = curve.model_dump(mode='json')
    assert table['origin'] == 'SOURCE_TABLE'
    assert 'measurement' not in table and 'max_interpolation_error' not in table
    for extra in ({'measurement': {'basis': 'A measurement'}}, {'max_interpolation_error': '0'}):
        with pytest.raises(ValidationError, match='SOURCE_TABLE'):
            TabulatedDispersionV1.model_validate({**table, **extra})
    with pytest.raises(ValidationError, match='SOURCE_TABLE'):
        PublicMaterialV1(material_id='E48', display_name='E48', dispersion=table, valid_band_nm=glass.valid_band_nm,
                         status='SPECIFIED', basis='Source index table')


@pytest.mark.golden
def test_reference_meridional_rays_and_focal_lengths(tmp_path):
    # Independently traced with RayOptics 0.9.10. See docs/SEQ_IMPORT.md for launch conditions.
    # The two interpolators differ slightly between the private material's tabulated nodes.
    imported = read_prescription_project(ASPHERE)
    saved = tmp_path / 'asphere.project.json'
    saved.write_text(json.dumps(imported.payload()), encoding='utf-8')
    project = load_project(saved)
    assert project.payload() == imported.payload()
    request = lower_project(project).request
    request = type(request).model_validate_json(request.model_dump_json())
    material = next(m for m in request.project.materials if m.material_id == 'project:E48')
    assert material.origin == 'source_table'
    dispersion = group_dispersion(material, 587.721865, DEFAULT_NUMERICAL_CONFIG)
    assert dispersion.n == pytest.approx(1.5316, abs=0.0001)
    assert dispersion.n_group is None and dispersion.k2 is None
    assert 'SOURCE_TABLE' in dispersion.limitation
    source = next(c.source for c in request.project.components if c.source)
    heights = np.array([1, 3, 5])
    offsets = np.column_stack((np.zeros(3), heights / source.waist_radius_y_mm))
    grid = LaunchGrid(offsets, np.array([[0, 0], [0, 1], [0, 2]]), 1.0, 3, 1.0)
    actual = trace_fields(request, 'image', {source.source_id}, {}, grid, None)
    oracle = {
        656.450687: [0.059002315, 0.178583040, 0.306997858],
        587.721865: [0.054313352, 0.164053870, 0.280974523],
        486.266176: [0.043171755, 0.129500200, 0.218959032],
    }
    assert set(actual) == {(source.source_id, wave) for wave in oracle}
    for wave, intercepts in oracle.items():
        field = actual[source.source_id, wave]
        assert field.arrived.all()
        np.testing.assert_allclose(field.points[:, 1], intercepts, rtol=0, atol=0.00003)
    lens = next(c for c in project.components if c.optical_role == 'lens')
    system = paraxial_analysis(project, {'components': [lens.instance_id], 'wavelength_nm': 587.721865})['system']
    assert system['efl_mm'] == pytest.approx(15.85633336, abs=3e-6)
    assert system['bfl_mm'] == pytest.approx(13.80677168, abs=3e-6)


# These small inputs exercise syntax and refusals separately from the upstream regression.
SOURCE = """TITLE 'Lens ! with ; punctuation'
RDM N; DIM C; WL 500 600; REF 2; WTW 1 2
SO 0 20
S 0.2 0.5 G1_CATALOG
  STO; CIR 0.4; CIR EDG 0.5
S -0.25 6
  CIR 0.4
SI 0 0
XOB 0 1; YOB 0 2
"""


def test_curvature_mode_cm_object_fields_and_catalog_qualification():
    rx = read_seq('\ufeff' + SOURCE)
    assert rx.name == 'Lens ! with ; punctuation'
    assert rx.object.distance_mm == 200 and rx.field_type == 'height'
    assert rx.fields == ((0, 0), (10, 20))
    assert rx.wavelength_weights == (1, 2) and rx.primary_wavelength_index == 1
    front, back = rx.surfaces
    assert front.radius_mm == 50 and back.radius_mm == -40
    assert front.clear_semi_diameter_mm == 4 and front.mechanical_semi_diameter_mm == 5
    assert front.thickness_mm == 5 and back.thickness_mm == 60
    assert front.material.name == 'G1' and front.material.manufacturer == 'CATALOG'
    assert front.location.row == 4 and rx.field_location.row == 9


@pytest.mark.parametrize(('original', 'edited', 'code', 'row'), [
    ('S -0.25 6', 'S -0.25 6\n XDE 1', E.UNSUPPORTED_FEATURE, 7),
    ('G1_CATALOG', 'REFL', E.UNSUPPORTED_FEATURE, 4),
    ('G1_CATALOG', '516.642', E.UNSUPPORTED_FEATURE, 4),
    ('CIR 0.4; CIR EDG', 'CIR EDG', E.FIELD_MISSING, 4),
    ('CIR EDG 0.5', 'CIR EDG 0.1', E.EDGE_INVALID, 5),
    ('S 0.2 0.5', 'S 0.2 0', E.THICKNESS_INVALID, 4),
    ('STO; CIR', 'CYL; CIR', E.UNSUPPORTED_FEATURE, 5),
    ('REF 2', 'REF 3', E.VALUE_INVALID, 2),
    ('WTW 1 2', 'WTW 2', E.VALUE_INVALID, 2),
    ('XOB 0 1', 'XAN 0 1', E.VALUE_INVALID, 9),
    ('XOB 0 1; YOB 0 2', 'YAN 0 2; VUY 0 0.2', E.UNSUPPORTED_FEATURE, 9),
    ('SI 0 0', 'SI 0 3', E.UNSUPPORTED_FEATURE, 8),
    ('CIR 0.4; CIR EDG', 'CIR bad; CIR EDG', E.VALUE_INVALID, 5),
    ('XOB 0 1; YOB 0 2', 'GO; IN other.seq', E.UNSUPPORTED_FEATURE, 9),
])
def test_invalid_or_unsupported_input_names_physical_line(original, edited, code, row):
    with pytest.raises(PrescriptionImportError) as exc:
        read_seq(SOURCE.replace(original, edited))
    problem, = exc.value.problems
    assert problem.code == code and problem.location.row == row


def test_default_air_import_and_named_private_glass(tmp_path):
    # Standard air is required for the native import even when TEM is omitted.
    path = tmp_path / 'small.seq'
    path.write_text("rdm; dim m; wl 550; epd 2; prv; pwl 700 400; 'GLASS_A' 1.5 1.5; end; "
                    "so 0 inf; s 20 3 'GLASS_A'; sto; cir 2; s -20 18; cir 2; si 0 0; go", encoding='utf-8')
    project = read_prescription_project(path)
    assert [m.material_id for m in project.model_materials] == ['GLASS_A']
    assert project.components[0].source_model.wavelength_nm == pytest.approx(550.150198, abs=0.001)


def test_private_catalog_rejects_wrong_count_and_unclosed_continuation():
    with pytest.raises(PrescriptionImportError) as exc:
        read_seq("PRV\nPWL 500 550 600\n'G1' 1.5 1.49\nEND\n" + SOURCE)
    assert exc.value.codes == (E.MATERIAL_INVALID,)
    assert exc.value.problems[0].location.row == 3
    with pytest.raises(PrescriptionImportError) as exc:
        read_seq(SOURCE + 'EPD &')
    assert exc.value.codes == (E.TEXT_INVALID,)
