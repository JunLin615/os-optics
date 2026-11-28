"""Who supplies each design component and how the plan holds it (``assembly_plan.request``), and the kernel request
built from it."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from derive_helpers import optical_catalog, project

from os_optics.assembly_plan.contract import MountingV1, SupplyReasonV1, SupplyV1
from os_optics.assembly_plan.request import CATALOG_DESIGN_GEOMETRY, chain_optic, classify, kernel_request


def _by(classified):
    return {c.instance_id: c for c in classified}


def test_the_focus_design_has_a_customer_source_and_one_chain_optic():
    catalog = optical_catalog()
    c = _by(classify(project("focus"), catalog))
    assert (c["SRC"].item_role, c["SRC"].supply, c["SRC"].supply_reason, c["SRC"].mounting) == (
        "COMPONENT", SupplyV1.CUSTOMER_OWNED, SupplyReasonV1.CUSTOMER_LIGHT_SOURCE, None)
    lens = c["L1"]
    assert (lens.item_role, lens.supply, lens.supply_reason, lens.mounting) == ("OPTIC", SupplyV1.OPENSCI, None, MountingV1.CHAIN)
    assert lens.mechanics is not None and lens.mechanics.optical_sku == "OS-OPT-TEST-LENS-PCX-F100" and lens.display_name


def test_a_simulation_only_product_is_an_unresolved_anchor_with_its_catalog_geometry():
    c = _by(classify(project("polarization"), optical_catalog()))
    pol = c["POL"]
    assert (pol.supply, pol.supply_reason, pol.mounting) == (SupplyV1.UNRESOLVED, SupplyReasonV1.NOT_ORDERABLE, MountingV1.ANCHOR)
    assert pol.envelope is not None and pol.envelope_basis == CATALOG_DESIGN_GEOMETRY and pol.beam_category == "POLARIZER"
    assert c["HWP"].chain


def test_customer_owned_products_are_anchors_the_customer_supplies():
    c = _by(classify(project("folded"), optical_catalog(), customer_owned={"M1"}))
    assert (c["M1"].supply, c["M1"].supply_reason, c["M1"].mounting) == (SupplyV1.CUSTOMER_OWNED, SupplyReasonV1.CUSTOMER_OWNED,
                                                                            MountingV1.ANCHOR)
    assert c["M1"].beam_category == "MIRROR"
    assert c["L1"].chain


@pytest.mark.parametrize("patch", [{"delivered_form": "HOUSED", "opensci_mounting": "NOT_AVAILABLE"}, {"opensci_mounting": "NOT_AVAILABLE"}])
def test_an_orderable_product_opensci_does_not_mount_is_supplied_unmounted(patch):
    catalog = optical_catalog(**{"OS-OPT-TEST-MIR-FLAT": patch})
    m1 = _by(classify(project("folded", catalog), catalog))["M1"]
    assert (m1.supply, m1.supply_reason, m1.mounting) == (SupplyV1.OPENSCI, SupplyReasonV1.OPENSCI_SUPPLIED_UNMOUNTED, MountingV1.ANCHOR)


def test_the_kernel_request_holds_chain_optics_anchors_and_observations():
    catalog = optical_catalog()
    p = project("polarization")
    classified = classify(p, catalog)
    request = kernel_request(p, catalog, classified, max_board_size_mm=(300.0, 300.0), beam_height_mm=100.0)
    assert [o.instance_id for o in request.optics] == ["HWP"]
    assert {e.instance_id: e.role for e in request.externals} == {"SRC": "source", "POL": "polarizer"}
    pol = next(e for e in request.externals if e.instance_id == "POL")
    assert pol.envelope is not None and pol.size_mm and pol.beam_category == "POLARIZER"
    assert len(request.observations) == len(p.observations) and request.observations
    assert (request.max_board_size_mm, request.beam_height_mm) == ((300.0, 300.0), 100.0)
    hwp = request.optics[0]
    assert [(d.axis, d.control) for d in hwp.retained_dofs] == [("Rz", "MANUAL")]
    assert hwp.mechanics.category == "WAVEPLATE" and hwp.pins is None


def test_an_unknown_product_is_refused():
    p = project("focus")
    with pytest.raises(KeyError):
        classify(p, SimpleNamespace(get=lambda sku: None))


def test_chain_optic_is_the_kernel_request_of_one_component():
    catalog = optical_catalog()
    p = project("focus")
    lens = next(c for c in p.components if c.instance_id == "L1")
    request = chain_optic(lens, catalog.get(lens.product.optical_sku))
    assert (request.instance_id, request.optical_sku, request.mechanics.category) == ("L1", "OS-OPT-TEST-LENS-PCX-F100", "LENS")
    assert [d.axis for d in request.retained_dofs] == ["Tx", "Ty", "Tz"]
    with pytest.raises(ValueError):
        chain_optic(lens, SimpleNamespace(optical_sku="OS-OPT-TEST-PRISM", form="RIGHT_ANGLE_PRISM", design_geometry=None))
