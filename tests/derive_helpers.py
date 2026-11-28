"""Shared inputs of the derive tests: the synthetic optical and mechanical fixtures, the reference Projects and options."""

from __future__ import annotations

import json
from pathlib import Path

from mechanical_fixture import USER_FIXTURE, fixture_path

from os_optics.assembly_plan.contract import (AssemblyPlanContentV1, CheckedWithV1, DeriveOptionsV1, PlanStatusV1, build_revision,
                                              finding_counts, normalize_numbers, plan_state)
from os_optics.assembly_plan.mechanical import MechanicalSources
from os_optics.local import example_project_of_kind, load_public_catalog, update_project

ROOT = Path(__file__).resolve().parent
OPTICAL_FIXTURE = ROOT / "fixtures" / "catalog" / "test_optical_catalog.v2.json"
BUDGET = {"max_chain_placements": 12000, "max_geometry_checks": 3_000_000}
HWP = "OS-OPT-TEST-WP-HWP-633"
ROTATION_MOUNT = "OS-MECH-TEST-ROTATION-MOUNT-D25"


def optical_catalog(**patch):
    """The fixture optical catalog; ``patch``: sku -> product fields replaced (a schema 3.1 variant with every product's
    mounting facts published)."""

    if not patch:
        return load_public_catalog(OPTICAL_FIXTURE)
    document = json.loads(OPTICAL_FIXTURE.read_bytes())
    document["schema_version"] = "3.1"
    for product in document["products"]:
        product.setdefault("opensci_mounting", "AVAILABLE" if product["commercial_readiness"] == "ORDERABLE" else "NOT_AVAILABLE")
        product.setdefault("delivered_form", "OPTIC")
        product.update(patch.get(product["optical_sku"], {}))
    return load_public_catalog(document)


def sources(*, user: bool = False) -> MechanicalSources:
    documents = [fixture_path().read_bytes()] + ([fixture_path(USER_FIXTURE).read_bytes()] if user else [])
    return MechanicalSources.of(*documents)


def options(**changes) -> DeriveOptionsV1:
    return DeriveOptionsV1(**{"budget": BUDGET, **changes})


def project(kind: str = "focus", catalog=None):
    return example_project_of_kind(kind, catalog=catalog or optical_catalog())


def with_hwp(base, gap_mm: float = 30.0):
    """``base`` (the focus design) with the half-wave plate ``gap_mm`` before the lens, retaining its Rz."""

    hwp = {"instance_id": "HWP", "optical_role": "waveplate",
           "product": {"optical_sku": HWP, "catalog_version": base.components[1].product.catalog_version},
           "nominal_pose": {"position_mm": [-gap_mm, 0.0, 0.0], "quaternion_wxyz": [0.5, 0.5, 0.5, 0.5]},
           "retained_dofs": [{"axis": "Rz", "control": "MANUAL"}]}
    return update_project(base, [{"op": "add_component", "component": hwp}]).project


def content_model(content) -> AssemblyPlanContentV1:
    return AssemblyPlanContentV1.model_validate(normalize_numbers(content))


def revision_of(outcome):
    """A revision document of a derived content, with its derive findings as the recorded check (as ``directory.py``
    writes one after its check)."""

    findings = list(outcome.derive_findings)
    counts = finding_counts(findings)
    status = PlanStatusV1(state=plan_state(counts), counts=counts, optics_results="NOT_RUN", evidence={},
                          purchase={"selected_lines": 0, "excluded_items": 0, "unresolved_items": 0})
    return build_revision(outcome.content, findings=findings, status=status,
                          checked_with=CheckedWithV1(checker_version="1.0.0", kernel_version="1.0.0", plan_checks_version="1.0.0"))


def item(content, *, instance: str | None = None, item_id: str | None = None):
    items = content["items"] if isinstance(content, dict) else content.items
    for i in items:
        get = i.get if isinstance(i, dict) else (lambda k, i=i: getattr(i, k))
        if (instance is not None and get("design_instance_id") == instance) or (item_id is not None and get("item_id") == item_id):
            return i
    raise KeyError(instance or item_id)


def chain_of(content, optic_id: str) -> dict[str, dict]:
    """``{role: item}`` of the chain an optic item stands on (mount, post, holder)."""

    links = {link["a_item_id"]: link["b_item_id"] for link in content["links"]}
    items = {i["item_id"]: i for i in content["items"]}
    out, current = {}, optic_id
    while current in links:
        current = links[current]
        out[items[current]["role"]] = items[current]
    return out
