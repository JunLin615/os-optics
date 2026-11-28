"""File-level diagnostics that protect the native object's and image's meaning."""
from pathlib import Path

import pytest

from os_optics.prescription import PrescriptionImportError, read_prescription_project


# A finite object and a single physical stop. The observation can only record forward crossings.
STOP = """MODE SEQ
UNIT MM
FTYP 0 0 1 1
WAVM 1 0.55 1
SURF 0
 DISZ 20
SURF 1
 STOP
 CURV 0
 DISZ 10
 DIAM 2 1
 MEMA 4 1
SURF 2
 DISZ 0
"""


def test_standalone_refuses_virtual_image(tmp_path):
    path = tmp_path / "virtual.ZMX"
    path.write_text(STOP.replace("DISZ 10", "DISZ -10"), encoding="utf-16")
    with pytest.raises(PrescriptionImportError, match="virtual image") as err:
        read_prescription_project(path)
    assert err.value.problems[0].location.surface == "2"


def test_two_mirror_image_before_primary_has_two_crossings(tmp_path):
    path = tmp_path / "fold.zmx"
    text = STOP.replace("DISZ 20", "DISZ INF").replace(" STOP", " GLAS MIRROR").replace("DISZ 10", "DISZ -5")
    path.write_text(text.replace("SURF 2\n DISZ 0", "SURF 2\n GLAS MIRROR\n DIAM 1 1\n DISZ 2\nSURF 3\n DISZ 0"))
    with pytest.raises(PrescriptionImportError, match="earlier reflection leg") as err:
        read_prescription_project(path)
    assert err.value.problems[0].location.surface == "3"


def test_configuration_refuses_changing_detector_aperture(tmp_path):
    path = tmp_path / "detector.zmx"
    path.write_text(STOP + ' DIAM 1 1\nMNUM 2 1\n'
                    'SDIA 2 1 1 0 0 0 1 1 1 0 0 "" 0\n'
                    'SDIA 2 2 3 0 0 0 1 1 1 0 0 "" 0\n', encoding="utf-8")
    with pytest.raises(PrescriptionImportError, match="physical image aperture changes") as err:
        read_prescription_project(path)
    where = err.value.problems[0].location
    assert (where.surface, where.configuration) == ("2", 2)


@pytest.mark.parametrize("name", ["session.zda", "archive.zar", "layout.step"])
def test_refuses_nonprescription_extensions_before_reading(name):
    with pytest.raises(PrescriptionImportError) as err:
        read_prescription_project(Path(name))
    assert err.value.codes == ("FORMAT_UNSUPPORTED",)


def test_invalid_encoded_file_locates_byte(tmp_path):
    path = tmp_path / "binary.zmx"
    path.write_bytes(b"MODE SEQ\n\x80")
    with pytest.raises(PrescriptionImportError, match="byte 9") as err:
        read_prescription_project(path)
    assert err.value.codes == ("TEXT_INVALID",)
