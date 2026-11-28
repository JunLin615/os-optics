"""The trace of an extended emitter's samples: independent chief-only rays, with the Tracer's physics and a compact record.

A sample (``emitter_samples.py``) is a packet of one ray and no Gaussian beam: it refracts, reflects, splits and is stopped
by the same handlers as every packet (``Tracer._interact``), and a clear aperture takes or passes it whole. ``SampleTracer``
replaces only what a beam needs and a ray does not: the arrival records (one row per crossing instead, ``SampledRows``),
surface hits (one sum per surface), the per-packet ledger entries (one sum per sink), untraced branches and the Gaussian
bounds that come with them.

Composition: samples have no phase relation. Their powers add; nothing here holds or compares a phase.

Budget: a sample takes no part in ``max_paths`` and ``max_trace_steps``, which bound beams and their run files. Its rays are
bounded by the declared ``sample_count`` (``EMITTER_SAMPLE_COUNT_MAX`` per line), by ``max_interactions_per_path`` per
ray, and by a ray-step budget of ``max_interactions_per_path`` steps per declared sample over the run, shared by the samples
and their branches (only a partially reflecting surface that sends rays back and forth reaches it). When it is spent the
ray being traced stops, the samples not launched yet are not launched, and their power is booked to the ledger's
``PATH_LIMIT`` with one ``MAX_PATHS`` issue: nothing is dropped from the budget and the estimates stay relative to the
emitted power (``SampledLine.launched`` says how many rays stand behind them).

A ray reflected by an interface whose reflection the trace does not follow leaves the model as for a beam (the ledger's
``UNTRACED_REFLECTION``), but no probe follows it per sample: ``validation/sampled_light.py`` states that light as not
followed on every plane.

Model scope: the rays kept as whole paths are a display, so what the validity check (``local/validity.py``) needs of every ray is kept here
in a compact form (``SampledScope``), outside the result: per surface, the range of angles the rays met it at; where rays stopped for want of
data; between which surfaces rays left a medium; and each ray's straight segments, which the check clips against the bodies.

A ray that meets a diffraction grating goes on in the orders whose power its response gives; the power of the others goes to the
ledger's ``UNMODELLED_RESPONSE``, with no ray: a sampled result is a sum of powers, and a ray of unknown power has none to add.
A ray that loses its known power further on (an order of mixed TE and TM from a table with TE != TM, at a surface whose share depends on it) adds nothing either.

A ray meeting a surface that scatters goes on with the unscattered share; the share it scatters is booked ``SURFACE_SCATTER`` and
not followed (``optics/rays/scatter.py``: one scatter per traced beam).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ...config import NumericalConfig
from ...contracts.optics import OpticalPath
from ...errors import ErrorCode, Status, ValidationIssue, make_issue
from ...math.exact_trig import incidence_deg
from ...math.vectors import correctly_rounded_sum, dot, transverse_basis
from ...scene.model import EmitterG, Scene, SurfaceG
from ..gaussian import initial_state
from ..materials.library import MaterialLibrary
from ..surfaces import Hits
from .decisions import counts_on_active_area
from .emitter_samples import EmitterSamples, global_samples
from .hits import SurfaceHit
from .path_ids import root_path_id
from .scheduler import TraceScheduler
from .tracer import (
    DETECTED,
    GRATING_ORDERS,
    NOT_ACCEPTED,
    TERMINATION_SINKS,
    SINK_PATH_LIMIT,
    SINK_SOURCE_NOT_TRACED,
    SINK_TERMINATED_OUT_OF_MODEL,
    TRACE_BUDGET,
    PowerFlow,
    SourceInput,
    TraceObserver,
    TakenShares,
    Tracer,
    _Packet,
)

#: Rays of each line kept as whole paths (``ValidationResult.paths``, the ray data): the first ones of the sequence, which
#: are themselves evenly spread over the emitter and its angular law.
DISPLAY_SAMPLES = 32
SAMPLES_NOT_LAUNCHED = "emitter samples not launched (ray-step budget)"
SAMPLE_NOT_GAUSSIAN = "a sampled ray of an extended emitter is not a Gaussian beam"
_PATH_PREFIX = "path:"
_NO_PATH_LIMIT = 2 ** 62
_PERCENT = 100.0
#: Below this length the emitter's x projected off a ray is taken as lying along the ray (its y is used instead).
_BASIS_DEGENERATE = 1e-12


@dataclass
class SampledRows:
    """The crossings of one observation plane by the rays of one source line, as columns (one row per crossing)."""

    sample_index: list[int] = field(default_factory=list)
    u_mm: list[float] = field(default_factory=list)
    v_mm: list[float] = field(default_factory=list)
    direction_u: list[float] = field(default_factory=list)
    direction_v: list[float] = field(default_factory=list)
    direction_n: list[float] = field(default_factory=list)
    # Geometric pupil analyses also retain a crossing with unknown power (None).
    power_W: list[float | None] = field(default_factory=list)
    counted: list[bool] = field(default_factory=list)
    # Additional crossing data retained by an explicit geometric pupil analysis.
    path_ids: list[str] = field(default_factory=list)
    refractive_indices: list[float] = field(default_factory=list)


@dataclass
class SampledLine:
    """One spectral line of one emitter as it was sampled."""

    source_id: str
    owner_id: str
    spectral_index: int
    wavelength_nm: float
    emitted_W: float
    weight_W: float
    sample_count: int
    seed: int
    launched: int = 0
    medium_unknown: bool = False


@dataclass
class SampledScope:
    """What the model-scope check reads of every ray, kept rays or not. ``meets``: (surface id, source id, spectral index, end event, index of the
    medium the light arrives from) -> [smallest angle of incidence, largest, rays] (``None`` angles: an event that states none). ``stops``: (surface
    id, source id, spectral index, termination). ``exits``: (surface entered by, surface the medium ends on or ``None``, source id, spectral index,
    medium) -> rays. ``segments``: (source id, spectral index, medium) -> the straight segments as a flat list of ``x0, y0, z0, x1, y1, z1``."""

    meets: dict[tuple[str, str, int, str, float], list] = field(default_factory=dict)
    stops: set[tuple[str, str, int, str]] = field(default_factory=set)
    exits: dict[tuple[str | None, str | None, str, int, str], int] = field(default_factory=dict)
    segments: dict[tuple[str, int, str], list[float]] = field(default_factory=dict)


@dataclass
class SampledTrace:
    """The sampled light of a run. ``rows``: (observation id, source id, spectral index) -> its crossings; ``hit_W``:
    (surface id, source id, spectral index) -> the power the rays brought to that physical surface."""

    lines: list[SampledLine] = field(default_factory=list)
    rows: dict[tuple[str, str, int], SampledRows] = field(default_factory=dict)
    hit_W: dict[tuple[str, str, int], float] = field(default_factory=dict)
    # the path id of the one surface hit each (surface, line) keeps
    hit_paths: set[str] = field(default_factory=set)
    scope: SampledScope = field(default_factory=SampledScope)
    ray_steps: int = 0
    step_budget: int = 0
    budget_reached: bool = False


def field_bases(directions: np.ndarray, e1: np.ndarray, e2: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The transverse basis of a coherent field's rays: the emitter's x projected off each ray (its y where x lies along the
    ray) and ``d x e1``, so the field's Jones vector keeps its meaning in the emitter frame for rays tilted from its +z."""
    first = e1[None, :] - dot(directions, e1)[:, None] * directions
    norm = np.sqrt(dot(first, first))
    fallback = e2[None, :] - dot(directions, e2)[:, None] * directions
    first = np.where((norm > _BASIS_DEGENERATE)[:, None], first, fallback)
    first = first / np.sqrt(dot(first, first))[:, None]
    return first, np.cross(directions, first)


class _NoEvents(list):
    """The trace's event list of a sampled run: each ray's events are not kept (the line's sums go into the trace hash)."""

    def append(self, _item: object) -> None:
        return None


class SampleTracer(Tracer):
    """Traces the samples of ``scene.emitters`` (module docstring). One instance serves one run."""

    #: a ray's own scatter at a scattering surface is booked (``SURFACE_SCATTER``) and not followed
    _follows_scatter = False
    #: an incoherent sampled ray carries no beam to convert: it passes a crystal that generates a second harmonic as a linear
    #: crystal (``Tracer._second_harmonic`` is for Gaussian beams)
    _converts_harmonics = False

    def __init__(self, scene: Scene, library: MaterialLibrary, cfg: NumericalConfig, *, recorder: TraceObserver | None = None,
                 quad_rule=None) -> None:
        super().__init__(scene, library, cfg, include_pupil=False, recorder=None, quad_rule=quad_rule)
        self._display_recorder = recorder
        self.sampled = SampledTrace()
        declared = sum(e.sample_count * len(e.spectrum) for e in scene.emitters)
        self.sampled.step_budget = declared * cfg.max_interactions_per_path
        self.schedule = TraceScheduler(_NO_PATH_LIMIT, self.sampled.step_budget, cfg.max_interactions_per_path, self.order_quantum_W)
        self.out.trace_events = _NoEvents()
        self._line: SampledLine | None = None
        self._sample_index = 0
        self._keep = False
        self._geometric_pupil = False
        self._seen: dict[tuple[str, str | None], int] = {}
        self._meeting: tuple[str, float] | None = None  # the surface being met and the index of the medium the light arrives from
        self._entered: dict[str, str | None] = {}  # path id -> the surface its current run through a medium began on
        self._flows: dict[tuple[str, str, int, float, str | None, str | None, str], float] = {}
        self._unit_offsets[(0, 0)] = np.zeros((0, 2))  # (a ray has no pupil rings)

    # ------------------------------------------------------------------------------------------------- the run
    def run_samples(self, *, pupil_samples: EmitterSamples | None = None) -> SampledTrace:
        """Trace normal emitter samples or the supplied geometric pupil rays of a single analysis emitter.

        A system pupil supplies each launch point, direction and polarization, and retains every requested path.
        """
        self._geometric_pupil = pupil_samples is not None
        if self._geometric_pupil and len(self.scene.emitters) != 1:
            raise ValueError("explicit pupil samples belong to one emitter")
        cfg = self.cfg
        for emitter in self.scene.emitters:
            coherent = emitter.pattern is not None and emitter.pattern.coherent
            samples = pupil_samples
            if samples is None and not coherent:
                samples = global_samples(emitter)
            for j, (wavelength_nm, weight) in enumerate(emitter.spectrum):
                power = emitter.power_W * weight
                self.out.inputs.append(SourceInput(emitter.source_id, emitter.owner_id, j, wavelength_nm, power))
                line = SampledLine(emitter.source_id, emitter.owner_id, j, wavelength_nm, power, power / emitter.sample_count,
                                   emitter.sample_count, emitter.seed)
                self.sampled.lines.append(line)
                self._line = line
                state = self._sample_state(emitter, wavelength_nm)
                if state is None:
                    line.medium_unknown = True
                    self.out.power_flows.append(PowerFlow(SINK_SOURCE_NOT_TRACED, power, emitter.source_id, j, wavelength_nm,
                                                          None, None, emitter.owner_id, "source"))
                    continue
                gstate, n = state
                if coherent:  # (one line: its rays follow the field's wavevector in this medium)
                    samples = global_samples(emitter, n)
                if coherent or pupil_samples is not None:
                    bases = field_bases(samples.directions, emitter.e1, emitter.e2)
                else:
                    bases = transverse_basis(samples.directions, cfg.zero_vector_tol)
                for k in range(emitter.sample_count):
                    if self.schedule.steps >= self.sampled.step_budget:
                        self.sampled.budget_reached = True
                        self._book(SINK_PATH_LIMIT, line.weight_W * (emitter.sample_count - k), emitter.source_id, j, wavelength_nm,
                                   None, emitter.owner_id, SAMPLES_NOT_LAUNCHED)
                        break
                    line.launched = k + 1
                    self._sample_index = k
                    self._keep = pupil_samples is not None or k < DISPLAY_SAMPLES
                    self.recorder = self._display_recorder if self._keep else None
                    pk = self._packet(emitter, j, k, wavelength_nm, line.weight_W, gstate, n, samples, bases)
                    if self.recorder is not None:
                        self.recorder.packet_started(pk, None)
                    self._enqueue(pk)
                    self._drain()
        self.recorder = None
        self.sampled.ray_steps = self.schedule.steps
        self._close()
        return self.sampled

    def _sample_state(self, emitter: EmitterG, wavelength_nm: float):
        """The invalid Gaussian state every ray of a line carries and the medium's index (``None``: no medium data there, or a
        sampled field whose rays the model does not take, ``PatternG.out_of_scope``: the line is not traced, with the issue)."""
        if emitter.pattern is not None and emitter.pattern.out_of_scope is not None:
            self._issue(emitter.pattern.out_of_scope)
            return None
        n, issue = self.library.evaluator(emitter.medium).index(wavelength_nm, location=f"source:{emitter.source_id}")
        if issue is not None or n is None:
            if issue is not None:
                self._issue(issue)
            return None
        state = initial_state(wavelength_nm=wavelength_nm, n=n, w0x=1.0, w0y=1.0, z_waist=0.0, m2x=1.0, m2y=1.0, bx=emitter.e1,
                              by=emitter.e2, cfg=self.cfg)
        return state.invalidated(ErrorCode.OUT_OF_MODEL_SCOPE, SAMPLE_NOT_GAUSSIAN), n

    def _packet(self, emitter: EmitterG, j: int, k: int, wavelength_nm: float, weight_W: float, gstate, n: float, samples,
                bases) -> _Packet:
        d = samples.directions[k:k + 1]
        e1, e2 = bases[0][k:k + 1].copy(), bases[1][k:k + 1].copy()
        if samples.jones is not None:
            jones = samples.jones[k:k + 1].copy()
        else:
            jones = np.array([[0.0 + 0.0j, 1.0 + 0.0j]]) if samples.second_state[k] else np.array([[1.0 + 0.0j, 0.0 + 0.0j]])
        opl = np.zeros(1) if samples.opl is None else samples.opl[k:k + 1].copy()
        return _Packet(
            path_id=f"{root_path_id(emitter.source_id, j, emitter.polarization_component)}.{k}", src=emitter, spectral_index=j,  # type: ignore[arg-type]
            wavelength_nm=wavelength_nm, power0_W=weight_W, medium=emitter.medium, n=n, o=samples.points[k:k + 1].copy(), d=d.copy(),
            e1=e1, e2=e2, jones=jones, alive=np.ones(1, dtype=bool), opl=opl, length=np.zeros(1), gstate=gstate, lineage=[],
        )

    def _close(self) -> None:
        """Turn the sums into the run's ledger entries, surface hits and its one budget issue."""
        for (kind, source_id, j, wavelength_nm, surface_id, owner_id, detail), power in self._flows.items():
            self.out.power_flows.append(PowerFlow(kind, power, source_id, j, wavelength_nm, None, surface_id, owner_id, detail))
        if not self.sampled.budget_reached and not self.schedule.exhausted:
            return
        self.sampled.budget_reached = True
        untraced_W = correctly_rounded_sum(f.power_W for f in self.out.power_flows if f.kind == SINK_PATH_LIMIT)
        launched_W = correctly_rounded_sum(i.power_W for i in self.out.inputs)
        fraction = untraced_W / launched_W if launched_W > 0.0 else 0.0
        self.out.issues.append(make_issue(
            ErrorCode.MAX_PATHS, Status.WARNING,
            f"the sampled rays reached their budget of {self.sampled.step_budget} ray steps ({self.cfg.max_interactions_per_path} "
            f"per declared sample): {untraced_W:.4g} W ({_PERCENT * fraction:.4g} % of the emitted power) was not traced, which the "
            "results do not include", budget=["max_interactions_per_path"], untraced_power_W=untraced_W,
            untraced_power_fraction=fraction, phase="primary", affected_source_ids=sorted({i.source_id for i in self.out.inputs}),
            suggested_action="reduce what multiplies the rays (partially reflecting surfaces facing each other) or raise "
                             "max_interactions_per_path"))

    # --------------------------------------------------------------------------------- what a ray records instead
    def _issue(self, issue: ValidationIssue) -> None:
        """One issue per code and place, with how many rays met it (a per-ray location names the source's rays together)."""
        location = None if issue.location is None or issue.location.startswith(_PATH_PREFIX) else issue.location
        key = (issue.code.value, location)
        seen = self._seen.get(key, 0)
        self._seen[key] = seen + 1
        if seen == 0:
            self.out.issues.append(issue.model_copy(update={"location": location}))
            return
        for i, kept in enumerate(self.out.issues):
            if kept.code is issue.code and kept.location == location:
                context = {**kept.numerical_context, "sampled_ray_events": seen + 1}
                self.out.issues[i] = kept.model_copy(update={"numerical_context": context})
                return

    def _book(self, kind: str, power_W: float, source_id: str, j: int, wavelength_nm: float, surface_id: str | None,
              owner_id: str | None, detail: str) -> None:
        key = (kind, source_id, j, wavelength_nm, surface_id, owner_id, detail)
        self._flows[key] = self._flows.get(key, 0.0) + float(power_W)

    def _flow(self, pk: _Packet, kind: str, power_W: float, surface: SurfaceG | None, detail: str) -> None:
        if pk.power_bound_W is not None:
            return
        for kind, power_W in self._bulk_split(pk, kind, power_W):
            self._book(kind, power_W, pk.src.source_id, pk.spectral_index, pk.wavelength_nm,
                       None if surface is None else surface.surface_id, None if surface is None else surface.owner_id, detail)

    def _finish(self, pk: _Packet, termination: str, surface_id: str | None) -> None:
        self._entered.pop(pk.path_id, None)
        if surface_id is not None:
            self.sampled.scope.stops.add((surface_id, pk.src.source_id, pk.spectral_index, termination))
        surface = None if surface_id is None else self._surface_by_id.get(surface_id)
        if termination != GRATING_ORDERS:  # (a grating's handler booked all of it)
            self._flow(pk, TERMINATION_SINKS.get(termination, SINK_TERMINATED_OUT_OF_MODEL), pk.chief_power(), surface, termination)
        if termination == TRACE_BUDGET:
            self.sampled.budget_reached = True
        if not self._keep:
            return
        self.out.paths.append(OpticalPath(
            path_id=pk.path_id, source_id=pk.src.source_id, spectral_index=pk.spectral_index, wavelength_nm=pk.wavelength_nm,
            parent_path_id=pk.parent_path_id, branch_surface_id=pk.branch_surface_id, branch_kind=pk.branch_kind,
            lineage=tuple(pk.lineage), segments=tuple(pk.segments), termination=termination, termination_surface_id=surface_id,
            final_power_W=None if pk.power_bound_W is not None else pk.chief_power(), geometric_path_length_mm=float(pk.length[0]),
            optical_path_length_mm=None if pk.opl_undefined else float(pk.opl[0])))
        if self.recorder is not None:
            self.recorder.path_finished(pk, self.out.paths[-1])

    def _interact(self, pk: _Packet, surf: SurfaceG, h: Hits) -> str | None:
        self._meeting = (surf.surface_id, pk.n)
        termination = super()._interact(pk, surf, h)
        self._meeting = None
        return termination

    def _end_event(self, pk: _Packet, event: str, aoi_deg: float | None = None) -> None:
        super()._end_event(pk, event, aoi_deg)
        if self._meeting is None:
            return
        key = (self._meeting[0], pk.src.source_id, pk.spectral_index, event, self._meeting[1])
        met = self.sampled.scope.meets.get(key)
        if met is None:
            self.sampled.scope.meets[key] = [aoi_deg, aoi_deg, 1]
            return
        met[2] += 1
        if aoi_deg is not None:
            met[0] = aoi_deg if met[0] is None else min(met[0], aoi_deg)
            met[1] = aoi_deg if met[1] is None else max(met[1], aoi_deg)

    def _note_segment(self, pk: _Packet, start, end, length, surf) -> None:
        """What the model-scope check needs of this segment (``SampledScope``): its ends, and the medium's run from surface to surface."""
        scope, source_id, j = self.sampled.scope, pk.src.source_id, pk.spectral_index
        if end is not None and length:
            begin = pk.o[0] if start is None else start
            scope.segments.setdefault((source_id, j, pk.medium), []).extend(
                (float(begin[0]), float(begin[1]), float(begin[2]), float(end[0]), float(end[1]), float(end[2])))
        if surf is not None and surf.interaction.kind == "observe":
            return
        surface_id = None if surf is None else surf.surface_id
        key = (self._entered.get(pk.path_id, pk.branch_surface_id), surface_id, source_id, j, pk.medium)
        scope.exits[key] = scope.exits.get(key, 0) + 1
        self._entered[pk.path_id] = surface_id

    def _segment(self, pk: _Packet, start, end, length, surf, *, power_start=None, w_start=None, w_end=None) -> None:
        self._note_segment(pk, start, end, length, surf)
        if self._keep:
            super()._segment(pk, start, end, length, surf, power_start=power_start, w_start=w_start, w_end=w_end)

    def _record_hit(self, pk: _Packet, surf: SurfaceG, h: Hits, p_in: float) -> None:
        """One ``SurfaceHit`` per surface and line, the first ray's, whose power becomes the sum over the rays
        (``validation/sampled_light.combine_surface_hits``): the exposure reads a power without a beam radius."""
        key = (surf.surface_id, pk.src.source_id, pk.spectral_index)
        if key not in self.sampled.hit_W:
            self.sampled.hit_paths.add(pk.path_id)
            super()._record_hit(pk, surf, h, p_in)
        self.sampled.hit_W[key] = self.sampled.hit_W.get(key, 0.0) + p_in

    def _do_observe(self, pk: _Packet, surf: SurfaceG, h: Hits, inter) -> str | None:
        if pk.power_bound_W is not None and not self._geometric_pupil:
            return None
        cos_i = abs(float(dot(pk.d[0], surf.normal)))
        if inter.observation_medium is not None and inter.observation_medium != pk.medium:
            self._issue(make_issue(ErrorCode.MEDIUM_MISMATCH, Status.WARNING,
                                   "the beam medium at the observation plane differs from the declared plane medium",
                                   location=f"observation:{inter.observation_id}", tracked_medium=pk.medium,
                                   declared_medium=inter.observation_medium))
        on_area, _fraction = counts_on_active_area(pk.gstate, surf, h, cos_i, self.cfg, self.quad_rule)
        half = inter.acceptance_half_angle_deg
        accepted = None if half is None else float(incidence_deg(cos_i, pk.d[0], surf.normal)) <= half
        key = (inter.observation_id or "", pk.src.source_id, pk.spectral_index)
        rows = self.sampled.rows.get(key)
        if rows is None:
            rows = self.sampled.rows[key] = SampledRows()
        d = pk.d[0]
        rows.sample_index.append(self._sample_index)
        rows.u_mm.append(float(h.u[0]))
        rows.v_mm.append(float(h.v[0]))
        rows.direction_u.append(float(dot(d, surf.u_axis)))
        rows.direction_v.append(float(dot(d, surf.v_axis)))
        rows.direction_n.append(float(dot(d, surf.normal)))
        rows.power_W.append(pk.chief_power() if pk.power_bound_W is None else None)
        rows.counted.append(on_area and accepted is not False)
        if self._geometric_pupil:
            rows.path_ids.append(pk.path_id)
            rows.refractive_indices.append(pk.n)
        pk.lineage.append(f"{surf.surface_id}:observe")
        self._end_event(pk, "observed")
        if inter.absorbing and on_area:
            pk.alive[:] = False
            return DETECTED if accepted is not False else NOT_ACCEPTED
        return None

    # ------------------------------------------------------------------------- what a beam needs and a ray does not
    def _geometry_orders(self) -> bool:
        return False

    def _branch(self, *_args, **_kwargs) -> None:
        return None

    def _leave_out(self, pk: _Packet, power_W: float) -> None:
        return None

    def _clipped_branch(self, *_args, **_kwargs) -> None:
        return None

    def _report_clip(self, *_args, **_kwargs) -> None:
        return None

    def _report_gaussian_state(self, *_args, **_kwargs) -> None:
        return None

    def _leg_end(self, *_args, **_kwargs) -> None:
        return None

    def _taken_before(self, *_args, **_kwargs) -> TakenShares:
        return TakenShares((), ())

    def _close_segment(self, *_args, **_kwargs) -> None:
        return None

    def _advance_group(self, pk: _Packet, step: float) -> None:
        return None


__all__ = ["DISPLAY_SAMPLES", "SampleTracer", "SampledLine", "SampledRows", "SampledScope", "SampledTrace"]
