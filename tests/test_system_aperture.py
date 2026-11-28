"""A native Project's system aperture survives serialization and launches rays through its physical stops."""
from __future__ import annotations

import json
import math

import pytest

from declared_helpers import declared_component, stop_model, thin_lens_model
from os_optics.local import SystemApertureV1, example_project, load_project, system_aperture_analysis, trace_system_pupil


FIELD = "SRC"
IDENTITY = (1, 0, 0, 0)
WAVELENGTH = 550


def aperture(kind="ENTRANCE_PUPIL_DIAMETER", value=4, conjugate="INFINITE", z=0, **fields):
    return {"kind": kind, "value": value, "object_conjugate": conjugate, "entrance_pupil_z_mm": z,
            "primary_wavelength_nm": WAVELENGTH, **fields}


def project(definition):
    source = example_project().payload()["components"][0]
    source["nominal_pose"] = {"position_mm": [0, 0, -10], "quaternion_wxyz": IDENTITY}
    model = source["source_model"]
    model.pop("wavelength_nm")
    model.pop("power_w")
    model["spectral_lines"] = [{"wavelength_nm": 500, "power_w": .003}, {"wavelength_nm": 550, "power_w": .001}]
    stop = declared_component("IRIS", "aperture", "iris", quaternion=IDENTITY)
    plane = {"observation_id": "image", "pose": {"position_mm": [0, 0, 20], "quaternion_wxyz": IDENTITY}, "absorbing": False}
    return {"schema": "opensci.project", "project_schema_version": "2.0", "design_intent": "OPTICAL_SYSTEM",
            "components": [source, stop], "optical_models": [stop_model("iris", opening_radius_mm="2", edge_radius_mm="6")],
            "observations": [plane], "system_aperture": definition}


def test_configured_spectrum_pupil_after_reopen(tmp_path):
    doc = project(aperture())
    doc["configurations"] = [{"configuration_id": "nominal"}, {"configuration_id": "small", "system_aperture": aperture(value=2)}]
    path = tmp_path / "project.json"
    path.write_text(json.dumps(load_project(doc).payload()), encoding="utf-8")
    coordinates = [(0, .75), (0, 1.5)]
    original = trace_system_pupil(path, source_id=FIELD, configuration_id="nominal", pupil_coordinates=coordinates)
    small = trace_system_pupil(path, source_id=FIELD, configuration_id="small", pupil_coordinates=coordinates)
    assert [row["spectral_weight"] for row in original["rays"]] == pytest.approx([.75, .25, .75, .25])
    assert [row["inside_system_aperture"] for row in original["rays"]] == [True, True, False, False]
    assert [row["observations"][0]["v_mm"] for row in original["rays"][:2]] == [1.5, 1.5]
    assert all(row["paths"][0]["termination_surface_id"] == "IRIS/stop" and not row["observations"] for row in original["rays"][2:])
    assert [row["observations"][0]["v_mm"] for row in small["rays"]] == [.75, .75, 1.5, 1.5]
    with pytest.raises(ValueError, match="configuration"):
        system_aperture_analysis(path, source_id=FIELD)


def test_finite_virtual_entrance_pupil_and_true_na():
    # A zero-pressure environment has n = 1. A 3/5 NA cone has a 3/4 tangent, also for an upstream virtual pupil.
    doc = project(aperture("OBJECT_NA", .6, "FINITE", z=-12))
    doc["environment"] = {"temperature_c": "20", "pressure_pa": "0"}
    doc["components"][0]["nominal_pose"]["position_mm"] = [.5, 0, -10]
    doc["system_aperture"]["primary_wavelength_nm"] = 600  # reference deliberately absent from the active spectrum
    result = trace_system_pupil(doc, source_id=FIELD, pupil_coordinates=[(1, 0)], wavelength_nm=600)
    row, = result["rays"]
    assert result["entrance_pupil_radius_mm"] == pytest.approx(1.5)
    assert result["object_na"] == pytest.approx(.6)
    assert row["origin_mm"] == [.5, 0, -10]
    assert row["direction"] == pytest.approx((-1 / math.sqrt(5), 0, 2 / math.sqrt(5)))
    assert row["paths"][0]["wavelength_nm"] == 600
    assert row["reference_only"] and row["spectral_weight"] == 0
    assert row["paths"][0]["segments"][0]["power_start_W"] == 0
    with pytest.raises(ValueError, match="vacuum wavelength"):
        trace_system_pupil(doc, source_id=FIELD, wavelength_nm=601)


def test_image_f_number_uses_current_native_efl_and_shared_plane():
    doc = project(aperture("IMAGE_F_NUMBER", 5))
    doc["optical_models"].append(thin_lens_model("lens", "20"))
    doc["components"].append(declared_component("LENS", "lens", "lens", position=(0, 0, 3), quaternion=IDENTITY))
    angle = math.radians(5)
    doc["components"][0]["nominal_pose"].update(position_mm=[5, 0, -10], quaternion_wxyz=[math.cos(angle/2), 0, math.sin(angle/2), 0])
    result = trace_system_pupil(doc, source_id=FIELD, pupil_coordinates=[(0, 0), (.5, .25)], wavelength_nm=550)
    assert result["focal_length_mm"] == pytest.approx(20)
    assert result["entrance_pupil_radius_mm"] == pytest.approx(2)
    for row, x, y in zip(result["rays"], [0, 1], [0, .5]):
        assert row["direction"] == pytest.approx([math.sin(angle), 0, math.cos(angle)])
        assert row["origin_mm"] == pytest.approx([x - 10 * math.tan(angle), y, -10])
    doc["optical_models"][1]["model"]["focal_length_mm"] = "10"
    assert system_aperture_analysis(doc, source_id=FIELD)["entrance_pupil_radius_mm"] == pytest.approx(1)


def test_geometric_branches_and_unknown_field_source():
    from os_optics.local import json_api

    doc = project(aperture())
    splitter = json_api.build_native_model("splitter.plate", {"diameter_mm": "25.4", "reflectance": "0.5",
                                           "valid_band_nm": ["400", "1000"], "model_id": "bs"})
    doc["optical_models"].append(splitter["optical_model"])
    doc["model_coatings"] = splitter["model_coatings"]
    doc["components"].append(declared_component("BS", "splitter", "bs", position=(0, 0, 5), quaternion=IDENTITY))
    doc["components"][0]["source_model"]["polarization"] = {"kind": "UNPOLARIZED"}
    result = trace_system_pupil(doc, source_id=FIELD, pupil_coordinates=[(0, .1), (.3, .4)], wavelength_nm=550)
    rays = json.loads(json.dumps(result))["rays"]
    for k, row in enumerate(rays):
        roots = {f"SRC#0.{k}", f"SRC#0:p1.{k}"}
        assert {p["parent_path_id"] for p in row["paths"] if p["parent_path_id"]} == roots
        assert {o["path_id"] for o in row["observations"]} == roots
        assert sum(o["power_W"] for o in row["observations"]) == pytest.approx(.00025)
    with pytest.raises(ValueError, match="field source"):
        trace_system_pupil(doc, source_id="BS")


def test_float_by_stop_and_located_invalid_combinations():
    doc = project(aperture("FLOAT_BY_STOP", None, entrance_pupil_radius_mm=2))
    result = system_aperture_analysis(doc, source_id=FIELD)
    assert result["entrance_pupil_diameter_mm"] == 4
    with pytest.raises(ValueError, match="finite object"):
        SystemApertureV1.model_validate(aperture("OBJECT_NA", .5))
    with pytest.raises(ValueError, match="projected stop radius"):
        SystemApertureV1.model_validate(aperture("FLOAT_BY_STOP", 1))
    with pytest.raises(ValueError, match="refractive index"):
        system_aperture_analysis(project(aperture("OBJECT_NA", 1.1, "FINITE")), source_id=FIELD)
