"""Local assembly plans (``os-optics.assembly_plan`` 1.0): derive, edit, check and order the mechanical build of a
Project on the user's device, without a token or a network connection.

``contract`` holds the plan documents and their digest rule, ``findings`` the finding rules. The Python API of
DESIGN 2.4 (``derive_plan``, ``open_plan``, ``check_plan``, ``plan_preview``, ``plan_drawing``, ``design_bom``,
``purchase_bom``, ``apply_plan_ops``, ``load_mechanical_catalog``) is exported here as its modules land.
"""

from __future__ import annotations

from .contract import (PLAN_SCHEMA_ID, PLAN_SCHEMA_VERSION, AssemblyPlanContentV1, AssemblyPlanError, AssemblyPlanErrorCode,
                       AssemblyPlanOpV1, AssemblyPlanRevisionV1, EditRequestV1, FindingV1, HistoryLineV1, PlanStatusV1,
                       build_revision, plan_content_digest, plan_json_schema)

__all__ = [
    "PLAN_SCHEMA_ID",
    "PLAN_SCHEMA_VERSION",
    "AssemblyPlanContentV1",
    "AssemblyPlanError",
    "AssemblyPlanErrorCode",
    "AssemblyPlanOpV1",
    "AssemblyPlanRevisionV1",
    "EditRequestV1",
    "FindingV1",
    "HistoryLineV1",
    "PlanStatusV1",
    "build_revision",
    "plan_content_digest",
    "plan_json_schema",
]
