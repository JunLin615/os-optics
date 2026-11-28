"""What a plan check, edit or output reads besides the revision: the Project, the public optical catalog, the mechanical
documents (and their kernel view), the design BOM of the revision, the nominal beam axes and the bound run's record.

``PlanContext`` is the concrete ``projection.ProjectionContext`` of the plan commands: ``optic_mechanics(item)`` derives an
optic's mechanics from the public optical catalog (``optics.mechanics_of``), ``design_bom`` is the revision's own design
BOM (``bom.design_bom``) and ``beam_axes`` are the nominal beam paths of the Project (the kernel's beam trace, in the
design frame), so the preview package carries them and the layout check can test them. ``for_revision`` binds a
context to one revision (its design BOM); ``with_project`` to an edited Project. Pure: nothing is read or written here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from .._vendor.shared_contracts.optical_design import LocalValidationRecordV1, ProjectDocumentV2
from .contract import AssemblyPlanRevisionV1, PlanItemV1
from .kernel import AssemblyResolutionRefusal, KernelCatalog, OpticMechanicsV1, beam_trace_settings, trace_beam_paths
from .mechanical import MechanicalSources
from .optics import mechanics_of
from .projection import BeamAxisInput
from .request import classify, kernel_request


@dataclass(frozen=True)
class PlanContext:
    """The inputs of one plan revision's checks and outputs (module docstring)."""

    project: ProjectDocumentV2
    catalog: Any
    mechanical: MechanicalSources
    kcatalog: KernelCatalog
    design_bom: Mapping[str, Any] = field(default_factory=dict)
    beam_axes: tuple[BeamAxisInput, ...] = ()
    local_validation: LocalValidationRecordV1 | None = None

    def product(self, sku: str | None) -> Any:
        return None if sku is None else self.catalog.get(sku)

    def optic_mechanics(self, item: PlanItemV1) -> OpticMechanicsV1 | None:
        product = self.product(item.identity.opensci_sku)
        mechanics = None if product is None else mechanics_of(product)
        return mechanics if isinstance(mechanics, OpticMechanicsV1) else None

    def component(self, instance_id: str | None) -> Any:
        return next((c for c in self.project.components if c.instance_id == instance_id), None)

    def for_revision(self, revision: AssemblyPlanRevisionV1) -> PlanContext:
        """This context with the design BOM of ``revision``."""

        from .bom import design_bom

        return replace(self, design_bom=design_bom(revision))

    def with_project(self, project: ProjectDocumentV2) -> PlanContext:
        """This context for an edited Project (beam axes traced again; the bound run's record is kept)."""

        return replace(self, project=project, beam_axes=nominal_beam_axes(project, self.catalog, self.kcatalog))


def nominal_beam_axes(project: ProjectDocumentV2, catalog: Any, kcatalog: KernelCatalog) -> tuple[BeamAxisInput, ...]:
    """The nominal beam paths of ``project`` in its design frame (the kernel's beam trace over the Project's optics,
    anchors and observation planes); none when the trace cannot follow the design."""

    try:
        request = kernel_request(project, catalog, classify(project, catalog))
        paths = trace_beam_paths(request.externals, request.optics, request.observations, **beam_trace_settings(kcatalog.policy))
    except (AssemblyResolutionRefusal, KeyError, ValueError, ZeroDivisionError):
        return ()
    return tuple(BeamAxisInput(path_key=p.path_key, points_design_mm=tuple(tuple(pt) for pt in p.points_design),  # type: ignore[misc]
                               through=tuple(p.through), wavelength_nm=p.wavelength_nm)
                 for p in paths if len(p.points_design) >= 2)


def plan_context(project: ProjectDocumentV2, catalog: Any, mechanical: MechanicalSources, *,
                 local_validation: LocalValidationRecordV1 | None = None,
                 revision: AssemblyPlanRevisionV1 | None = None) -> PlanContext:
    """A context from its inputs (``mechanical`` must hold a document: ``MECHANICAL_CATALOG_NOT_AVAILABLE`` otherwise)."""

    kcatalog = KernelCatalog.from_sources(mechanical.require())
    context = PlanContext(project=project, catalog=catalog, mechanical=mechanical, kcatalog=kcatalog,
                          beam_axes=nominal_beam_axes(project, catalog, kcatalog), local_validation=local_validation)
    return context if revision is None else context.for_revision(revision)


__all__ = ["PlanContext", "nominal_beam_axes", "plan_context"]
