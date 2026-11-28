"""The kernel's optic mechanics from the public optical catalog (``assembly_plan.optics``): the form table and the
geometry rules, on the fixture catalog and on synthetic design geometries."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from derive_helpers import optical_catalog

from os_optics._vendor.shared_contracts.design_catalog.enums import OpticalForm
from os_optics.assembly_plan.kernel import OpticMechanicsV1
from os_optics.assembly_plan.optics import FORM_CATEGORY, MissingMechanics, mechanics_of, mounted_by_opensci, orderable

#: Forms the kernel holds no post mount for (prisms, retroreflectors, birefringent splitting prisms).
UNHELD_FORMS = {"RIGHT_ANGLE_PRISM", "PENTA_PRISM", "DOVE_PRISM", "EQUILATERAL_PRISM", "WEDGE_PRISM", "ROOF_PRISM",
                "CORNER_CUBE_RETROREFLECTOR", "WOLLASTON_PRISM", "ROCHON_PRISM"}


def test_every_public_form_is_either_held_by_a_kernel_category_or_listed_as_not_held():
    forms = {f.value for f in OpticalForm}
    assert set(FORM_CATEGORY) <= forms
    assert forms - set(FORM_CATEGORY) == UNHELD_FORMS
    assert {c for c, _ in FORM_CATEGORY.values()} == {"LENS", "MIRROR", "BEAMSPLITTER_PLATE", "BEAMSPLITTER_CUBE", "FILTER", "WINDOW",
                                                       "WAVEPLATE", "POLARIZER"}
    assert {f for f, (c, t) in FORM_CATEGORY.items() if not t} == {"FLAT_MIRROR", "CONCAVE_MIRROR", "OFF_AXIS_PARABOLIC_MIRROR"}


def test_the_fixture_catalog_products_have_mechanics_of_their_design_geometry():
    catalog = optical_catalog()
    for product in catalog.products:
        mech = mechanics_of(product)
        assert isinstance(mech, OpticMechanicsV1), product.optical_sku
        g = product.design_geometry
        assert mech.shape.value == "ROUND" and mech.size_mm == pytest.approx(float(g.diameter_mm))
        assert (mech.axial_min_mm, mech.axial_max_mm) == pytest.approx((float(g.z_min_mm), float(g.z_max_mm)))
        assert mech.thickness_mm == pytest.approx(float(g.z_max_mm) - float(g.z_min_mm))
        assert mech.transmissive == (product.form.value not in ("FLAT_MIRROR", "CONCAVE_MIRROR"))
        assert mech.curved == (product.form.value == "CONCAVE_MIRROR")
        (visual,) = mech.visual
        assert (visual.kind, visual.finish, visual.axis, visual.base_mm[2]) == ("CYLINDER", "GLASS", "z", mech.axial_min_mm)


def _product(form: str, geometry: dict, sku: str = "OS-OPT-TEST-X") -> SimpleNamespace:
    return SimpleNamespace(optical_sku=sku, form=form, design_geometry=SimpleNamespace(**geometry))


def test_a_cube_box_is_a_cube_and_another_box_a_rectangle_of_its_shorter_edge():
    cube = mechanics_of(_product("CUBE_BEAMSPLITTER", {"kind": "BOX", "center_mm": ("0", "0", "0"), "half_extents_mm": ("5", "5", "5")}))
    assert (cube.shape.value, cube.size_mm, cube.axial_min_mm, cube.axial_max_mm, cube.category) == ("CUBE", 10.0, -5.0, 5.0, "BEAMSPLITTER_CUBE")
    plate = mechanics_of(_product("PLANE_WINDOW", {"kind": "BOX", "center_mm": ("0", "0", "-0.620127"),
                                                   "half_extents_mm": ("15", "7.5", "2.620127")}))
    assert (plate.shape.value, plate.size_mm) == ("RECTANGULAR", 15.0)
    # decimal arithmetic before the rounding: the extent is exactly twice the half extent
    assert (plate.axial_min_mm, plate.axial_max_mm, plate.thickness_mm) == (-3.240254, 2.0, 5.240254)
    (visual,) = plate.visual
    assert (visual.kind, visual.min_mm, visual.max_mm) == ("BOX", (-15.0, -7.5, -3.240254), (15.0, 7.5, 2.0))


def test_an_unheld_form_or_a_polyhedron_has_no_mechanics_and_says_what_is_missing():
    prism = mechanics_of(_product("RIGHT_ANGLE_PRISM", {"kind": "BOX", "center_mm": ("0", "0", "0"), "half_extents_mm": ("5", "5", "5")}))
    assert prism == MissingMechanics("OS-OPT-TEST-X", "NO_KERNEL_CATEGORY", "OS-OPT-TEST-X:mechanics")
    glan = mechanics_of(_product("GLAN_TAYLOR_PRISM", {"kind": "CONVEX_POLYHEDRON", "vertices_mm": ()}))
    assert glan == MissingMechanics("OS-OPT-TEST-X", "GEOMETRY_NOT_HELD", "OS-OPT-TEST-X:design_geometry")


def test_supply_facts_orderable_and_mounted_by_opensci():
    catalog = optical_catalog(**{"OS-OPT-TEST-MIR-FLAT": {"delivered_form": "HOUSED", "opensci_mounting": "NOT_AVAILABLE"},
                                 "OS-OPT-TEST-WP-HWP-633": {"opensci_mounting": "NOT_AVAILABLE"}})
    by = {p.optical_sku: p for p in catalog.products}
    assert orderable(by["OS-OPT-TEST-LENS-PCX-F100"]) and mounted_by_opensci(by["OS-OPT-TEST-LENS-PCX-F100"])
    assert orderable(by["OS-OPT-TEST-MIR-FLAT"]) and not mounted_by_opensci(by["OS-OPT-TEST-MIR-FLAT"])
    assert orderable(by["OS-OPT-TEST-WP-HWP-633"]) and not mounted_by_opensci(by["OS-OPT-TEST-WP-HWP-633"])
    assert not orderable(by["OS-OPT-TEST-POL-LIN"]) and not mounted_by_opensci(by["OS-OPT-TEST-POL-LIN"])
    # a schema 3.0 release without the facts mounts every ORDERABLE product
    plain = {p.optical_sku: p for p in optical_catalog().products}
    assert mounted_by_opensci(plain["OS-OPT-TEST-MIR-FLAT"]) and not mounted_by_opensci(plain["OS-OPT-TEST-WIN-NBK7-T3"])
