"""The beam the customer is shown, line by line, from the design's published spectral data.

The nominal beam paths (``trace_beam_paths``) are geometric: the kernel keeps every arm of every splitter clear of
hardware. What the assembly view, its snapshots and the assembly drawing draw and list as light is narrower: each
spectral line of each source only on the branches it takes. ``design_beam_lines`` follows each line with the public
catalog's coating data of the simulation model the customer's run lowered (``LocalValidationRecordV1.simulated_parts``):
a coated surface's band reflectance / transmittance at the line, the splitting surface for a reflected branch or a
cube's ``OUT_R`` port, the product of the coated surfaces for a transmitted one, each with the best share the same
coatings state at any wavelength (so a filter's stop band blocks a line and a neutral density filter never does), the
source's line powers and the Project's targets (``kernel.beam_lines`` holds the display rule). An element without
published coating data at the line (a user-defined component, a polarization element, a wavelength outside every band)
is not known to stop it.

Each line also follows its source's declared polarization. ``polarized_response`` routes it through the
Engine request of the design (``local.lower_project``: the design's own elements, catalog and declared alike) with the Engine's routing (``os_optics.optics.polarization.routing``: Fresnel and coating s / p
amplitudes at the nominal angle of incidence, splitter and mirror phases, polarizer and retarder Jones matrices about
their posed axes). Nothing is traced: the no-oracle rule holds (the share of an element along the nominal path is
computed, not the design's run). An element the routing cannot follow keeps the published unpolarized shares above, and
the polarization beyond it is not known; such a share, and one the routing computed outside the element's characterized
angles of incidence, wavelengths or polarizations (with the design's compiled elements: the run's own validity
rule), is not resolved, and the axis carries the note
``POLARIZATION_NOT_RESOLVED``.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from decimal import Decimal
from typing import Any

from ..errors import EngineInputError
from ..local.validity import surface_aoi_checks
from ..optics.polarization.routing import DIRECTION_COSINE, ROUTE_PASS, ROUTE_SPLIT_REFLECT, PolarizationRouter
from .kernel.beam import (
    STEP_REFLECT,
    STEP_SPLIT_PORT,
    STEP_SPLIT_REFLECT,
    STEP_SPLIT_TRANSMIT,
    STEP_TRANSMIT,
)
from .kernel.beam_lines import BeamLineAxisV1, LinePassage, Response, route_beam_lines
from .kernel.models import BeamPathV2
from .._vendor.shared_contracts.design_catalog import PublicOpticalCatalogV2, TracedBranches
from .._vendor.shared_contracts.optical_design import ProjectDocumentV2
from .._vendor.shared_contracts.optical_view import coating_peak

_REFLECTED = (STEP_REFLECT, STEP_SPLIT_REFLECT)
_TRANSMITTED = (STEP_TRANSMIT, STEP_SPLIT_TRANSMIT)
#: The outputs of a cube splitter (``beam.cube_ports``): through and off its splitting surface.
_PORT_TRANSMITTED, _PORT_REFLECTED = ":OUT_T", ":OUT_R"


def catalog_response(catalog: PublicOpticalCatalogV2, simulated_parts: Iterable[Any]) -> Response:
    """The share each catalog element passes per step and wavelength, and the best share it passes along that step at any
    wavelength (``coating_peak``), from the coatings of the model each part was simulated with (``SimulatedPartV1``)."""

    models: dict[str, Any] = {}
    for part in simulated_parts:
        product = catalog.get(part.optical_sku)
        ref = part.simulation_model
        model = None if product is None else next(
            (m for m in product.simulation_models if (m.model_kind, m.model_version) == (ref.model_kind, ref.model_version)), None)
        if model is not None and hasattr(model, "surfaces"):
            models[part.instance_id] = model

    def value(surface: Any, reflected: bool, wavelength: Decimal) -> tuple[float, float | None] | None:
        coating = catalog.coating(surface.coating_id) if surface.coating_id is not None else None
        band = None if coating is None else coating.band_at(wavelength)
        if band is None:
            return None
        return float(Decimal(band.reflectance if reflected else band.transmittance)), coating_peak(coating, reflected=reflected)

    def response(element: str, step: str, wavelength_nm: float) -> tuple[float, float | None] | None:
        model = models.get(element)
        if model is None:
            return None
        wavelength = Decimal(repr(float(wavelength_nm)))
        if step in _REFLECTED or step.endswith(_PORT_REFLECTED):
            split = [s for s in model.surfaces if s.traced is TracedBranches.TRANSMITTED_AND_REFLECTED]
            reflectors = split or [s for s in model.surfaces if s.traced is TracedBranches.REFLECTED]
            values = [v for v in (value(s, True, wavelength) for s in reflectors) if v is not None]
            return values[0] if values else None
        if step in _TRANSMITTED or step.endswith(_PORT_TRANSMITTED):
            values = [v for v in (value(s, False, wavelength) for s in model.surfaces if s.traced is not TracedBranches.REFLECTED)
                      if v is not None]
            if not values:
                return None
            peaks = [best for _, best in values]
            return math.prod(share for share, _ in values), (None if None in peaks else math.prod(peaks))  # type: ignore[arg-type]
        return None

    return response


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    return 1.0 if na == 0.0 or nb == 0.0 else sum(x * y for x, y in zip(a, b, strict=True)) / (na * nb)


class PolarizedCatalogResponse:
    """The ``PolarizedResponse`` of a design (module docstring): the Engine's routing through the lowered elements, the
    published unpolarized shares (``catalog_response``) where it cannot follow an element."""

    def __init__(self, router: PolarizationRouter, fallback: Response) -> None:
        self._router = router
        self._fallback = fallback

    def start(self, source_id: str) -> Any:
        return self._router.source_state(source_id)

    def step(self, element: str, step: str, wavelength_nm: float, point: Any, direction_in: Any, direction_out: Any,
             state: Any) -> LinePassage | None:
        if direction_out is not None:
            # a splitter's branch that turns away from the arriving direction is its reflected branch
            split = step in (STEP_SPLIT_REFLECT, STEP_SPLIT_TRANSMIT) or step.startswith(STEP_SPLIT_PORT)
            reflected = split and _cosine(direction_in, direction_out) < DIRECTION_COSINE
        else:
            reflected = step == STEP_SPLIT_REFLECT or step.endswith(_PORT_REFLECTED)
        passage = self._router.step(element, ROUTE_SPLIT_REFLECT if reflected else ROUTE_PASS, wavelength_nm, point,
                                    direction_in, direction_out, state)
        if passage is None:
            known = self._fallback(element, step, wavelength_nm)
            # the published unpolarized share: not a polarization-resolved value
            return None if known is None else LinePassage(share=known[0], best=known[1], resolved=False)
        if state is None:
            return LinePassage(share=passage.unpolarized_share, best=passage.best, state=passage.state, share_range=passage.share_range,
                               resolved=passage.resolved)
        return LinePassage(share=passage.share, best=passage.best, state=passage.state, resolved=passage.resolved)


def polarized_response(engine_request: Any, catalog: PublicOpticalCatalogV2, simulated_parts: Iterable[Any], *,
                       engine_elements: Sequence[Any] = ()) -> PolarizedCatalogResponse | None:
    """The polarized response of the design whose Engine request (``ValidationRequest``) the Project lowered to, with
    the angle ranges its compiled elements (``engine_elements``) are characterized for; ``None`` without a request."""

    if engine_request is None:
        return None
    try:
        router = PolarizationRouter(engine_request.project, aoi_checks=surface_aoi_checks(tuple(engine_elements)))
    except EngineInputError:
        return None  # the Engine cannot place the design's elements: the published unpolarized shares route it
    return PolarizedCatalogResponse(router, catalog_response(catalog, simulated_parts))


def design_beam_lines(paths: Sequence[BeamPathV2], project: ProjectDocumentV2, catalog: PublicOpticalCatalogV2,
                      simulated_parts: Iterable[Any], *, engine_request: Any = None,
                      engine_elements: Sequence[Any] = ()) -> tuple[BeamLineAxisV1, ...]:
    """Each spectral line of each light source of ``project`` along the nominal ``paths``, only where it goes; with the
    design's lowered ``engine_request`` (and its compiled ``engine_elements``), polarization-resolved (module
    docstring)."""

    # one beam per source: a Gaussian beam's spectral lines. The light of an extended emitter, an emitting sample or a
    # sampled source is no beam and is not drawn (its source is named by the SOURCE_WITHOUT_BEAM_PATH note).
    sources = [c for c in project.components if c.source_model is not None and c.source_model.model_kind == "GAUSSIAN_BEAM"]
    lines = {c.instance_id: tuple(c.source_model.wavelengths_nm) for c in sources}
    powers = {c.instance_id: tuple(line.power_w for line in c.source_model.emission_lines) for c in sources}
    absorbing = frozenset(o.observation_id for o in project.observations if o.absorbing)
    simulated_parts = tuple(simulated_parts)
    return route_beam_lines(paths, lines, catalog_response(catalog, simulated_parts), powers=powers, absorbing=absorbing,
                            targets=project.targets,
                            polarized=polarized_response(engine_request, catalog, simulated_parts, engine_elements=engine_elements))


def beam_axes_record(axes: Sequence[BeamLineAxisV1]) -> list[dict[str, Any]]:
    """The persisted form (``internal_resolution["beam_axes"]``): design-frame points, the element at each point; the
    display role of each stretch (``segment_roles``) and the axis's notes (``notes``) only when it has any (a record
    written before them reads as every stretch primary, no note)."""

    out = []
    for a in axes:
        record: dict[str, Any] = {"path_key": a.path_key, "wavelength_nm": a.wavelength_nm,
                                  "points_design_mm": [list(p) for p in a.points_design], "through": list(a.through)}
        if a.segment_roles is not None:
            record["segment_roles"] = list(a.segment_roles)
        if a.notes:
            record["notes"] = list(a.notes)
        out.append(record)
    return out


__all__ = ["PolarizedCatalogResponse", "beam_axes_record", "catalog_response", "design_beam_lines", "polarized_response"]
