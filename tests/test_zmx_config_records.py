"""Fixed configuration cells, dependencies and source coordinates in .zmx records."""

from decimal import Decimal

import pytest

from os_optics.prescription.errors import PrescriptionImportError
from os_optics.prescription.zmx_config import configuration_states, configuration_texts


BASE = '''VERS 150514
MODE SEQ
FNUM 3.5 0
FTYP 0 0 3 1 0 0 0
XFLN 0 0 0 0
YFLN 0 4 9 0
SURF 0
  DIAM 0 0 0 0 1 ""
  DISZ INFINITY
SURF 1
  DISZ 25
  CURV 0.02 0 0 0 0 ""
  DIAM 3 1 0 0 1 ""
SURF 2
  DISZ 10
  DIAM 4 3 0 0 1 ""
SURF 3
  DIAM 5 0 0 0 1 ""
CONF 3 0 0 0 0 0 0 0 0 0
BLNK
TOL TOFF 0 0 0 0 0 0 0 0
'''


def cell(key, target, state, value):
    return f'{key} {target} {state} {value} 0 0 0 {state} 1 1.000E+00 0.000E+00 0 "" 0\n'


def test_fixed_cells_explicit_configuration_order_and_original_base_rows():
    text = BASE + 'MNUM 3 2\n'
    for state in (3, 1, 2):
        text += cell('THIC', 1, state, ('1.0000000000000000001', '25', '37')[state - 1])
        text += cell('APER', 0, state, state + 1)
        text += cell('YFIE', 2, state, -state)
        text += cell('YFIE', 3, state, state * 2)
        text += cell('XFIE', 1, state, '.05')
        text += cell('SDIA', 1, state, state + 4)
        text += cell('CRVT', 1, state, state * .125)
    active, states = configuration_states(text)
    assert active == 2
    assert len(states) == 3
    for n, result in enumerate(states, 1):
        lines = result.splitlines()
        assert len(lines) == len(BASE.splitlines())
        assert lines[2] == f'FNUM {n+1} 0'
        assert lines[4] == 'XFLN .05 0 0 0'
        assert lines[5] == f'YFLN 0 {-n} {n*2} 0'
        assert lines[10] == '  DISZ ' + ('1.0000000000000000001', '25', '37')[n - 1]
        assert Decimal(lines[11].split()[1]) == n * Decimal('.125')
        assert lines[12] == f'  DIAM {n+4} 1 0 0 1 ""'
        assert lines[18:] == ['', 'BLNK', 'TOL TOFF 0 0 0 0 0 0 0 0']
        assert 'MNUM' not in result and 'THIC' not in result


@pytest.mark.parametrize('end', ['""', '"" 0'])
def test_no_table_and_single_disabled_operand(end):
    original = BASE.replace('\n', '\r\n')
    assert configuration_states(original) == (1, [original])
    [state] = configuration_texts(BASE + f'MNUM 1 1\nMOFF 0 1 "" 0 0 0 1 1 0 0.0 {end}\n')
    assert 'MNUM' not in state and 'MOFF' not in state
    assert state.splitlines()[10] == '  DISZ 25'


@pytest.mark.parametrize('operand,error,column,config,surface', [
    (cell('PRAM', 1, 1, '2'), 'UNSUPPORTED_FEATURE', 'PRAM', 1, None),
    (cell('THIC', 1, 2, '2'), 'VALUE_INVALID', 'THIC', 2, '1'),
    (cell('THIC', 8, 1, '2'), 'UNSUPPORTED_FEATURE', 'THIC', 1, '8'),
    (cell('THIC', 1, 1, 'NaN'), 'VALUE_INVALID', 'THIC', 1, '1'),
    (cell('YFIE', 4, 1, '3'), 'VALUE_INVALID', 'YFIE', 1, None),
    (cell('THIC', 1, 1, '2').replace('2 0 0 0', '2 2 0 0'), 'UNSUPPORTED_FEATURE', 'THIC', 1, '1'),
    (cell('THIC', 1, 1, '2').replace('0.000E+00', '8.0'), 'UNSUPPORTED_FEATURE', 'THIC', 1, '1'),
    ('THIC 1 1 2\n', 'ROW_INVALID', 'THIC', 1, '1'),
    ('MOFF 0 1 "" 2 0 0 1 1 0 0.0 "" 0\n', 'UNSUPPORTED_FEATURE', 'MOFF', 1, None),
])
def test_operand_errors_are_at_original_cell(operand, error, column, config, surface):
    with pytest.raises(PrescriptionImportError) as caught:
        configuration_texts(BASE + 'MNUM 1 1\n' + operand)
    [problem] = caught.value.problems
    assert problem.code.value == error
    assert problem.location.row == len(BASE.splitlines()) + 2
    assert (problem.location.column, problem.location.configuration, problem.location.surface) == (column, config, surface)


def test_orphan_operand_cannot_be_mistaken_for_a_merit_record():
    text = BASE + cell('APMN', 1, 1, '2')
    with pytest.raises(PrescriptionImportError, match='precedes its MNUM') as caught:
        configuration_texts(text)
    assert caught.value.problems[0].location.row == len(text.splitlines())


def test_refuses_missing_and_duplicate_cell():
    text = BASE + 'MNUM 2 1\n' + cell('THIC', 1, 1, '1') + cell('APER', 0, 1, '2') + cell('APER', 0, 2, '3')
    with pytest.raises(PrescriptionImportError, match='Missing value for THIC') as caught:
        configuration_texts(text)
    assert caught.value.problems[0].location.configuration == 2
    with pytest.raises(PrescriptionImportError, match='Duplicate THIC') as caught:
        configuration_texts(text + cell('THIC', 1, 1, '2'))
    assert caught.value.problems[0].location.row == len(text.splitlines()) + 1


@pytest.mark.parametrize(('record', 'column'), [
    ('DIAM 3 0 0 0 1 ""', 'DIAM'),
    ('DIAM 3 2 7 0 1 ""', 'DIAM'),
    ('MEMA 3 0 0 0 1 ""', 'MEMA'),
    ('PZUP 2 -1 0 0', 'PZUP'),
    ('MAZH 0 0', 'MAZH'),
    ('CURV 0.01 4 2 1 0 ""', 'CURV'),
    ('GLAS BK7 2 3 1 0 0 0 0 0 0 0', 'GLAS'),
])
def test_refuses_stale_surface_solve_for_other_state(record, column):
    text = BASE.replace('DIAM 3 1 0 0 1 ""', record) + 'MNUM 2 2\n'
    with pytest.raises(PrescriptionImportError, match='configuration 1') as caught:
        configuration_texts(text + cell('THIC', 1, 1, '7') + cell('THIC', 1, 2, '25'))
    location = caught.value.problems[0].location
    assert (location.row, location.column, location.surface, location.configuration) == (13, column, '1', 1)


def test_auto_clear_aperture_with_explicit_states():
    text = BASE.replace('DIAM 3 1', 'DIAM 3 0') + 'MNUM 2 1\n' + cell('SDIA', 1, 1, '3') + cell('SDIA', 1, 2, '4')
    assert configuration_texts(text)[1].splitlines()[12] == '  DIAM 4 1 0 0 1 ""'


def test_auto_mechanical_edges_follow_largest_fixed_clear_radius_of_element():
    source = BASE.replace('  DIAM 3 1', '  GLAS BK7\n  MEMA 4 0 0 0 1 ""\n  DIAM 3 1')
    table = 'MNUM 2 1\n' + cell('THIC', 2, 1, 10) + cell('THIC', 2, 2, 12)
    assert all('  MEMA 4 0 0 0 1 ""' in state for state in configuration_texts(source + table))
    with pytest.raises(PrescriptionImportError, match='MEMA'):
        configuration_texts(source.replace('MEMA 4', 'MEMA 5') + table)


def test_separate_aperture_cannot_hide_a_changing_diameter():
    text = BASE.replace('  DIAM 3 1', '  FLAP 0 3 0\n  DIAM 3 1') + 'MNUM 2 2\n'
    with pytest.raises(PrescriptionImportError, match='separate aperture') as caught:
        configuration_texts(text + cell('SDIA', 1, 1, 6) + cell('SDIA', 1, 2, 3))
    assert caught.value.problems[0].location.configuration == 1


def test_single_state_override_preserves_pickup_conflict_diagnostic():
    text = BASE.replace('DIAM 3 1', 'DIAM 3 2') + 'MNUM 1 1\n' + cell('SDIA', 1, 1, '3')
    with pytest.raises(PrescriptionImportError, match='conflicts with the DIAM solve') as caught:
        configuration_texts(text)
    assert caught.value.problems[0].location.row == len(text.splitlines())
