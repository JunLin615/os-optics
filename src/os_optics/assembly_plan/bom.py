"""The plan's bills of materials (DESIGN 1.4): the design BOM of every item and the purchase BOM of what is bought.

Design BOM (``os-optics.design_bom`` 1.0, ``design_bom``): every placed or anchored item of a revision, grouped by
``(supply, identity kind, SKU or custom part key or item id, role)`` into lines ``D1..Dn``, ordered OS-OPT optics by
SKU, then OS-MECH hardware by role (mount, post, holder, board) and SKU, then the user's hardware, the customer's table,
customer parts and unresolved hardware. ``bom_digest`` is the osc1 digest of the document without it; a LOCAL_PLAN
preview package binds it and its scene names each part's design BOM line.

Purchase BOM (``purchase_bom``): exactly the request body of ``POST /api/v1/part-list-submissions`` -- schema version
``2`` (default): one ``PLAN`` line per selected SKU (the sum of the purchased quantities) and one ``SPARE`` line per SKU
with spares (the sum of the spare units), PLAN lines first, each group by SKU, ``L1..Ln``, no line of quantity 0; schema
version ``1`` (for a service that reads only version 1): spares merged into the quantities and
``mechanical_supply: "NONE"``. The report beside it states the plan revision it came from and every item it leaves out
(``CUSTOMER_OWNED``, ``UNRESOLVED`` or ``NOT_SELECTED``). A plan that selects nothing has no purchase BOM
(``PURCHASE_BOM_EMPTY``).
"""

from __future__ import annotations

from typing import Any

from .._vendor.shared_contracts.common.hashes import osc1_digest
from .contract import (AssemblyPlanError, AssemblyPlanErrorCode, AssemblyPlanRevisionV1, ItemIdentityKindV1, ItemRoleV1,
                       SupplyV1)

DESIGN_BOM_SCHEMA = "os-optics.design_bom"
DESIGN_BOM_SCHEMA_VERSION = "1.0"
PURCHASE_BOM_SCHEMA = "opensci.commercial.part_list_submission"
PURCHASE_BOM_SCHEMA_VERSIONS = ("2", "1")
PURPOSE_PLAN = "PLAN"
PURPOSE_SPARE = "SPARE"
MECHANICAL_SUPPLY_NONE = "NONE"
DESIGN_LINE_PREFIX = "D"
PURCHASE_LINE_PREFIX = "L"
EXCLUDED_CUSTOMER_OWNED = "CUSTOMER_OWNED"
EXCLUDED_UNRESOLVED = "UNRESOLVED"
EXCLUDED_NOT_SELECTED = "NOT_SELECTED"
#: Line order of the design BOM: identity kinds, and OS-MECH roles within their kind.
_KIND_ORDER = {ItemIdentityKindV1.OS_OPT: 0, ItemIdentityKindV1.OS_MECH: 1, ItemIdentityKindV1.USER_MECH: 2,
               ItemIdentityKindV1.CUSTOMER_TABLE: 3, ItemIdentityKindV1.CUSTOMER_PART: 4, ItemIdentityKindV1.NONE: 5}
_ROLE_ORDER = {ItemRoleV1.MOUNT: 0, ItemRoleV1.POST: 1, ItemRoleV1.POST_HOLDER: 2, ItemRoleV1.BREADBOARD: 3}
_SUPPLY_ORDER = {SupplyV1.OPENSCI: 0, SupplyV1.CUSTOMER_OWNED: 1, SupplyV1.UNRESOLVED: 2}


def _group_key(item: Any) -> tuple[str, str, str, str]:
    identity = item.identity
    return (item.supply.value, identity.kind.value, identity.opensci_sku or identity.custom_part_key or item.item_id, item.role.value)


def _line_order(item: Any) -> tuple[int, int, str, int, str]:
    kind = item.identity.kind
    role = _ROLE_ORDER.get(item.role, 0) if kind is ItemIdentityKindV1.OS_MECH else 0
    ident = item.identity.opensci_sku or item.identity.custom_part_key or item.item_id
    return (_KIND_ORDER[kind], role, ident, _SUPPLY_ORDER[item.supply], item.item_id)


def design_bom(revision: AssemblyPlanRevisionV1) -> dict[str, Any]:
    """The design BOM of ``revision`` (module docstring)."""

    groups: dict[tuple[str, str, str, str], list[Any]] = {}
    for item in sorted(revision.items, key=_line_order):
        groups.setdefault(_group_key(item), []).append(item)
    lines = []
    for n, members in enumerate(groups.values(), start=1):
        first = members[0]
        lines.append({
            "line_id": f"{DESIGN_LINE_PREFIX}{n}", "role": first.role.value, "identity_kind": first.identity.kind.value,
            "opensci_sku": first.identity.opensci_sku, "display_name": first.identity.display_name,
            "display_name_zh_cn": first.identity.display_name_zh_cn, "item_ids": sorted(m.item_id for m in members),
            "quantity": sum(m.quantity for m in members), "supply": first.supply.value,
            "supply_reason": None if first.supply_reason is None else first.supply_reason.value,
            "purchase": {"quantity": sum(m.purchase.quantity for m in members),
                         "spare_units": sum(m.purchase.spare_units for m in members)},
        })
    body = {"schema": DESIGN_BOM_SCHEMA, "schema_version": DESIGN_BOM_SCHEMA_VERSION,
            "plan": {"plan_id": revision.plan_id, "revision": revision.revision, "content_digest": revision.content_digest},
            "project_hash": revision.bindings.project.project_hash.digest, "lines": lines}
    return {**body, "bom_digest": osc1_digest(body)}


def _excluded(revision: AssemblyPlanRevisionV1) -> list[dict[str, Any]]:
    groups: dict[tuple[str | None, str], list[str]] = {}
    for item in sorted(revision.items, key=_line_order):
        if item.purchase.selected:
            continue
        reason = (EXCLUDED_CUSTOMER_OWNED if item.supply is SupplyV1.CUSTOMER_OWNED
                  else EXCLUDED_UNRESOLVED if item.supply is SupplyV1.UNRESOLVED else EXCLUDED_NOT_SELECTED)
        sku = item.identity.opensci_sku if item.identity.kind is not ItemIdentityKindV1.CUSTOMER_PART else None
        groups.setdefault((sku, reason), []).append(item.item_id)
    return [{"item_ids": sorted(ids), "opensci_sku": sku, "reason": reason} for (sku, reason), ids in groups.items()]


def _bought(items: Any) -> tuple[dict[str, int], dict[str, int]]:
    """``(plan units, spare units)`` by SKU of the items selected for purchase."""

    plan: dict[str, int] = {}
    spare: dict[str, int] = {}
    for item in items:
        if not item.purchase.selected or item.identity.opensci_sku is None:
            continue
        sku = item.identity.opensci_sku
        plan[sku] = plan.get(sku, 0) + item.purchase.quantity
        spare[sku] = spare.get(sku, 0) + item.purchase.spare_units
    return plan, spare


def purchase_bom(revision: AssemblyPlanRevisionV1, *, schema_version: str = "2") -> tuple[dict[str, Any], dict[str, Any]]:
    """``(body, report)``: the purchase BOM of ``revision`` (module docstring) and the report of what it holds and
    leaves out. ``PURCHASE_BOM_EMPTY`` when no unit is bought."""

    if schema_version not in PURCHASE_BOM_SCHEMA_VERSIONS:
        raise ValueError(f"schema_version is one of {', '.join(PURCHASE_BOM_SCHEMA_VERSIONS)}")
    plan, spare = _bought(revision.items)
    warnings: list[str] = []
    if schema_version == "2":
        rows = [(sku, plan[sku], PURPOSE_PLAN) for sku in sorted(plan) if plan[sku] > 0]
        rows += [(sku, spare[sku], PURPOSE_SPARE) for sku in sorted(spare) if spare[sku] > 0]
        lines = [{"line_id": f"{PURCHASE_LINE_PREFIX}{n}", "opensci_sku": sku, "quantity": qty, "purpose": purpose}
                 for n, (sku, qty, purpose) in enumerate(rows, start=1)]
        body: dict[str, Any] = {"schema": PURCHASE_BOM_SCHEMA, "schema_version": "2", "lines": lines}
    else:
        totals = {sku: plan.get(sku, 0) + spare.get(sku, 0) for sku in sorted(set(plan) | set(spare))}
        lines = [{"line_id": f"{PURCHASE_LINE_PREFIX}{n}", "opensci_sku": sku, "quantity": qty}
                 for n, (sku, qty) in enumerate(((s, q) for s, q in totals.items() if q > 0), start=1)]
        body = {"schema": PURCHASE_BOM_SCHEMA, "schema_version": "1", "lines": lines, "mechanical_supply": MECHANICAL_SUPPLY_NONE}
        if any(spare.values()):
            warnings.append("schema version 1 has no spare lines: the spare units are added to the quantities")
    if not lines:
        raise AssemblyPlanError(AssemblyPlanErrorCode.PURCHASE_BOM_EMPTY,
                                "the plan buys nothing: select items for purchase (set_purchase) to write a purchase BOM",
                                {"plan_id": revision.plan_id, "revision": revision.revision})
    design = design_bom(revision)
    report = {"source": {"plan_id": revision.plan_id, "revision": revision.revision, "content_digest": revision.content_digest,
                         "design_bom_digest": design["bom_digest"]},
              "schema_version": schema_version, "lines": len(lines), "units": sum(line["quantity"] for line in lines),
              "excluded": _excluded(revision), "warnings": warnings}
    return body, report


def purchase_line_count(items: Any) -> int:
    """How many lines the purchase BOM (schema version 2) of these items has (0: they buy nothing)."""

    plan, spare = _bought(items)
    return sum(1 for q in plan.values() if q > 0) + sum(1 for q in spare.values() if q > 0)


__all__ = ["DESIGN_BOM_SCHEMA", "PURCHASE_BOM_SCHEMA", "design_bom", "purchase_bom", "purchase_line_count"]
