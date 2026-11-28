"""Real sequential prescriptions. Reference wavelengths are in vacuum for the native analyses.
See docs/ZMX_IMPORT.md for source reports, ray definitions, glass conventions and tolerances.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from os_optics.local import load_project, load_public_catalog, paraxial_analysis, run_optical_project
from os_optics.prescription import read_prescription_project

FIXTURES = Path(__file__).parent / "fixtures" / "zmx"
D_LINE = 587.721865  # 587.5618 nm in air, 20 °C, 101325 Pa


@pytest.fixture(scope="module")
def microscope_catalog():
    return load_public_catalog(FIXTURES / "microscope-glasses.json")


def _reopen(filename, directory, catalog=None):
    imported = read_prescription_project(FIXTURES / filename, catalog=catalog)
    saved = directory / "project.json"
    saved.write_text(json.dumps(imported.payload()), encoding="utf-8")
    return load_project(saved).payload()


def test_keck_cassegrain_returns_through_primary_hole(tmp_path):
    doc = _reopen("Keck_f13.zmx", tmp_path)
    optics = paraxial_analysis(doc, {"components": ["M1", "M2"], "wavelength_nm": 1000.269461})
    system = optics["system"]
    assert system["efl_mm"] == pytest.approx(149583.028437, abs=1e-6)
    assert system["bfl_mm"] == pytest.approx(17895.0, abs=0.005)
    assert system["input_plane_mm"][2] == 17500.0
    assert system["output_plane_mm"][2] == pytest.approx(2105.015, abs=1e-9)
    assert [s["direction"][2] for s in optics["axis_path"]] == [1, -1, 1]
    assert doc["observations"][0]["pose"]["position_mm"][2] == pytest.approx(20000.019428058, abs=1e-8)

    run = run_optical_project(doc).result
    for path in run.paths:
        primary, secondary, image = [s for s in path.segments if s.end_surface_id]
        assert [(s.end_surface_id, s.end_event) for s in (primary, secondary, image)] == [
            ("M1/S1", "reflect"), ("M2/S1", "reflect"), ("observation/image", "observed")]
        assert 1000 < math.hypot(*primary.end_mm[:2]) < 5480
        assert math.hypot(*secondary.end_mm[:2]) < 662.55
        # Where the final segment crosses the primary's vertex plane, its radius clears the central hole.
        t = (17500 - image.start_mm[2]) / (image.end_mm[2] - image.start_mm[2])
        assert math.hypot(*[a + t * (b - a) for a, b in zip(image.start_mm[:2], image.end_mm[:2])]) < 1000
    arrivals = {a.beam.source_id: a for a in run.observations[0].arrivals}
    assert arrivals["SRC1"].uv_mm == pytest.approx((0, 0), abs=5e-6)
    assert arrivals["SRC2"].uv_mm[1] == pytest.approx(52.21433, abs=0.001)  # report's paraxial image at +0.02°


def test_mercado_finite_object_and_image(microscope_catalog, tmp_path):
    doc = _reopen("5000548a.zmx", tmp_path, microscope_catalog)
    assert {m["copied_from"]["material_id"] for m in doc["model_materials"]} == {"HOYA_LAC7", "INFRARED_CAF2"}
    assert doc["components"][1]["nominal_pose"]["position_mm"] == [0, 0.5, -12.893]
    z_image = doc["observations"][0]["pose"]["position_mm"][2]
    assert z_image == pytest.approx(185.048, abs=1e-8)
    system = paraxial_analysis(doc, {"components": ["L1", "L2", "L3", "L4"], "wavelength_nm": D_LINE})["system"]
    assert system["efl_mm"] == pytest.approx(16.32088, abs=5e-6)
    assert system["bfl_mm"] == pytest.approx(6.822254, abs=5e-7)
    c, d = system["abcd"][1]
    # Conjugate-plane magnification at paraxial focus. The prescribed image plane is 0.41 mm closer.
    assert 1 / (c * 12.893 + d) == pytest.approx(-10.0232, abs=5e-5)
    run = run_optical_project(doc).result
    # Surface 3 follows the first air gap. Check its measured index relative to ambient air at all three lines.
    relative_indices = {}
    for ray in run.paths:
        if ray.source_id == "SRC1":
            media = {s.end_surface_id: s.refractive_index for s in ray.segments if s.end_surface_id}
            relative_indices[ray.wavelength_nm] = media["L2/S2"] / media["L2/S1"]
    assert relative_indices == pytest.approx({486.266176: 1.4370250504, D_LINE: 1.4338492788,
                                             656.450687: 1.4324580165}, abs=1e-9)
    off_axis = [a for a in run.observations[0].arrivals if a.beam.source_id == "SRC2"]
    assert len(off_axis) == 3
    primary = next(a for a in off_axis if a.beam.wavelength_nm == D_LINE)
    assert primary.uv_mm[1] == pytest.approx(-5.0030913, abs=1e-6)


def test_reiley_dry_objective_signed_stop_and_five_degree_field(microscope_catalog, tmp_path):
    doc = _reopen("US04384765-1.zmx", tmp_path, microscope_catalog)
    assert {m["copied_from"]["material_id"] for m in doc["model_materials"]} == {
        "HOYA_E-FD1", "MISC_CAF2", "SCHOTT_BAF52", "SCHOTT_LAK31", "SCHOTT_N-SF56", "SCHOTT_N-SK11",
        "SUMITA_LASFN16", "SUMITA_LASFN7"}
    components = {c["instance_id"]: c for c in doc["components"]}
    z_stop = components["STOP"]["nominal_pose"]["position_mm"][2]
    assert components["L4"]["nominal_pose"]["position_mm"][2] - z_stop == pytest.approx(-0.02444761619235, abs=1e-12)
    lenses = [c["instance_id"] for c in components.values() if c["optical_role"] == "lens"]
    system = paraxial_analysis(doc, {"components": lenses, "wavelength_nm": D_LINE})["system"]
    assert system["efl_mm"] == pytest.approx(1.83093950, abs=5e-8)
    specimen_z = doc["observations"][0]["pose"]["position_mm"][2]
    assert specimen_z - system["output_plane_mm"][2] == pytest.approx(0.2788013426282, abs=1e-12)
    run = run_optical_project(doc).result
    heights = {a.beam.wavelength_nm: a.uv_mm[1] for a in run.observations[0].arrivals if a.beam.source_id == "SRC2"}
    assert heights == pytest.approx({486.266176: 0.1566543730, D_LINE: 0.1576703090, 656.450687: 0.1582325256}, abs=1e-7)


@pytest.mark.parametrize("pupil_fraction", [0.999, 1.001])
def test_reiley_marginal_ray_and_specimen_na(microscope_catalog, pupil_fraction, tmp_path):
    doc = _reopen("US04384765-1.zmx", tmp_path, microscope_catalog)
    # Use the on-axis source at a specified entrance height. Leave every optical surface and the stop as imported.
    probe = doc["components"][0]
    doc["components"] = [probe] + [c for c in doc["components"] if c["optical_role"] != "source"]
    probe["nominal_pose"]["position_mm"][1] = pupil_fraction * 1.525782920539282
    probe["source_model"].pop("spectral_lines")
    probe["source_model"].update(wavelength_nm=D_LINE, power_w=0.001, waist_radius_mm=0.0001)
    run = run_optical_project(doc).result
    arrivals = run.observations[0].arrivals
    if pupil_fraction > 1:
        assert run.paths[0].termination_surface_id.startswith("STOP/")
        assert not arrivals
    else:
        (arrival,) = arrivals
        assert arrival.uv_mm[1] == pytest.approx(0.00003642877, abs=1e-7)
        # Wet/dry medium matters: NA = n * sin(u), evaluated in the air of the specimen.
        segment = next(s for s in run.paths[0].segments if s.end_surface_id == "observation/image")
        assert segment.refractive_index * abs(arrival.beam.direction[1]) == pytest.approx(0.83454856, abs=2e-7)
