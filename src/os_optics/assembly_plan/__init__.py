"""Local assembly plans (``os-optics.assembly_plan`` 1.0): derive, edit, check and order the mechanical build of a
Project on the user's device, without a token or a network connection.

``contract`` holds the plan documents and their digest rule, ``findings`` the finding rules. The Python API of
DESIGN 2.4 is exported here and imported on first use: ``derive_plan(project, out_dir=...)`` writes a plan directory,
``open_plan(plan_dir)`` reopens it (``.head``, ``.revision(n)``, ``.history()``, ``.verify()``, ``.edit(ops)``,
``.check()``, ``.bind_run(run)``), ``check_plan``, ``plan_preview``, ``plan_drawing``, ``design_bom`` and ``purchase_bom``
take a plan directory or an open plan, ``apply_plan_ops`` edits a revision in memory, ``open_plan_view`` is what
``os-optics view PLAN_DIR`` shows, and ``load_mechanical_catalog`` reads a public mechanical catalog document.
"""

from __future__ import annotations

from .contract import (PLAN_SCHEMA_ID, PLAN_SCHEMA_VERSION, AssemblyPlanContentV1, AssemblyPlanError, AssemblyPlanErrorCode,
                       AssemblyPlanOpV1, AssemblyPlanRevisionV1, EditRequestV1, FindingV1, HistoryLineV1, PlanStatusV1,
                       build_revision, plan_content_digest, plan_json_schema)
from .mechanical import MechanicalCatalogError, MechanicalSources, PublicMechanicalCatalogV1, load_mechanical_catalog


def open_plan_view(plan_dir, *, revision=None, mechanical=()):
    """``(revision, context, findings)`` of a plan directory for ``os-optics view PLAN_DIR``
    (``workflow.open_plan_view``; a module attribute of its own, so the viewer finds it without loading the workflow)."""

    from .workflow import open_plan_view as opener

    return opener(plan_dir, revision=revision, mechanical=mechanical)


def __getattr__(name: str):
    """The plan workflow API, imported on first use (it loads the kernel, the projection and the local workflow)."""

    if name in _LAZY:
        from importlib import import_module

        return getattr(import_module(_LAZY[name], __name__), name)
    raise AttributeError(name)


#: Lazily imported names -> their module.
_LAZY = {
    "derive_plan": ".workflow", "open_plan": ".workflow", "apply_plan_ops": ".workflow",
    "OpenPlan": ".workflow", "PlanResult": ".workflow", "check_plan": ".workflow", "plan_preview": ".workflow",
    "plan_drawing": ".workflow", "design_bom": ".workflow", "purchase_bom": ".workflow", "PlanContext": ".context", "plan_context": ".context",
    "project_plan_package": ".projection", "project_plan": ".projection", "PlanProjectionInputs": ".projection",
    "project_plan_detail": ".detail", "DrawingOutcome": ".drawing",
}

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
    "MechanicalCatalogError",
    "MechanicalSources",
    "PlanStatusV1",
    "PublicMechanicalCatalogV1",
    "DrawingOutcome",
    "OpenPlan",
    "PlanContext",
    "PlanProjectionInputs",
    "PlanResult",
    "apply_plan_ops",
    "build_revision",
    "check_plan",
    "derive_plan",
    "design_bom",
    "open_plan",
    "open_plan_view",
    "plan_context",
    "plan_drawing",
    "plan_preview",
    "project_plan",
    "project_plan_detail",
    "project_plan_package",
    "purchase_bom",
    "load_mechanical_catalog",
    "plan_content_digest",
    "plan_json_schema",
]
