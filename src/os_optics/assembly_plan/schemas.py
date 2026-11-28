"""JSON Schemas of the local assembly plan documents (``os-optics schema KIND`` and the JSON API's ``json_schema``):
``assembly-plan`` (a revision), ``assembly-plan-ops`` (an edit call), ``design-bom``, ``purchase-bom`` (the request body
``order quote`` sends), ``bom-review`` (the review file ``order quote`` writes) and ``public-mechanical-catalog``.

The BOM models here state the documents ``bom.py`` and the commerce client write; the tests validate every written
document against them.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, StrictInt

from .._vendor.shared_contracts.base import StrictContractModel
from .._vendor.shared_contracts.common.hashes import Sha256Digest

SKU_MAX = 120
PLAN_SCHEMA_KINDS = ("assembly-plan", "assembly-plan-ops", "design-bom", "purchase-bom", "bom-review", "public-mechanical-catalog")


class DesignBomPlanRefV1(StrictContractModel):
    plan_id: str
    revision: StrictInt = Field(ge=1)
    content_digest: Sha256Digest


class DesignBomPurchaseV1(StrictContractModel):
    quantity: StrictInt = Field(ge=0)
    spare_units: StrictInt = Field(ge=0)


class DesignBomLineV1(StrictContractModel):
    line_id: str = Field(pattern=r"^D[1-9][0-9]*$")
    role: Literal["OPTIC", "COMPONENT", "MOUNT", "POST", "POST_HOLDER", "BREADBOARD", "DECLARED_ENVELOPE"]
    identity_kind: Literal["OS_OPT", "OS_MECH", "USER_MECH", "CUSTOMER_TABLE", "CUSTOMER_PART", "NONE"]
    opensci_sku: str | None = None
    display_name: str
    display_name_zh_cn: str | None = None
    item_ids: tuple[str, ...] = Field(min_length=1)
    quantity: StrictInt = Field(ge=1)
    supply: Literal["OPENSCI", "CUSTOMER_OWNED", "UNRESOLVED"]
    supply_reason: str | None = None
    purchase: DesignBomPurchaseV1


class DesignBomV1(StrictContractModel):
    """``os-optics.design_bom`` 1.0: every item of one plan revision, by line."""

    schema_id: Literal["os-optics.design_bom"] = Field("os-optics.design_bom", alias="schema")
    schema_version: Literal["1.0"] = "1.0"
    plan: DesignBomPlanRefV1
    project_hash: Sha256Digest
    lines: tuple[DesignBomLineV1, ...] = Field(min_length=1)
    bom_digest: Sha256Digest = Field(description="osc1 digest of the document without bom_digest.")


class PurchaseLineV2(StrictContractModel):
    line_id: str = Field(pattern=r"^L[1-9][0-9]*$")
    opensci_sku: str = Field(pattern=r"^OS(-[A-Z0-9]+)+$", max_length=SKU_MAX)
    quantity: StrictInt = Field(ge=1)
    purpose: Literal["PLAN", "SPARE"] = "PLAN"


class PurchaseBomV2(StrictContractModel):
    """The purchase BOM: exactly the ``POST /api/v1/part-list-submissions`` body (schema version 2)."""

    schema_id: Literal["opensci.commercial.part_list_submission"] = Field("opensci.commercial.part_list_submission", alias="schema")
    schema_version: Literal["2"] = "2"
    lines: tuple[PurchaseLineV2, ...] = Field(min_length=1)
    mechanical_supply: Literal["NONE"] | None = None
    confirmed_changes: dict[str, Any] | None = Field(default=None, description="The decisions on a reviewed quote (order quote --confirm).")


class BomReviewV1(StrictContractModel):
    """``os-optics.bom_review`` 1.0: the review file ``os-optics order quote`` writes when lines changed."""

    schema_id: Literal["os-optics.bom_review"] = Field("os-optics.bom_review", alias="schema")
    schema_version: Literal["1.0"] = "1.0"
    resolution_id: str | None = None
    submitted: dict[str, Any] = Field(description="The body as sent.")
    bom_review: dict[str, Any] = Field(description="The service's line review, verbatim.")
    proposed_confirmation: dict[str, Any] | None = Field(default=None, description="A purchase BOM with confirmed_changes.")


def plan_schema(kind: str) -> dict[str, Any]:
    """The JSON Schema of one of ``PLAN_SCHEMA_KINDS``."""

    from .contract import EditRequestV1, plan_json_schema
    from .mechanical.model import mechanical_catalog_json_schema

    if kind == "assembly-plan":
        return plan_json_schema()
    if kind == "assembly-plan-ops":
        return EditRequestV1.model_json_schema(by_alias=True)
    if kind == "public-mechanical-catalog":
        return mechanical_catalog_json_schema()
    model = {"design-bom": DesignBomV1, "purchase-bom": PurchaseBomV2, "bom-review": BomReviewV1}[kind]
    return model.model_json_schema(by_alias=True)


__all__ = ["PLAN_SCHEMA_KINDS", "BomReviewV1", "DesignBomV1", "PurchaseBomV2", "plan_schema"]
