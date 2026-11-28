"""Local pure-optical design workflow on this device: public optical catalog -> Project -> Engine -> results, 3D view and
local validation record. Everything runs on this device from ``pip install os-optics``; only the public optical
catalog is synchronized from the OpenSci catalog service into a local store (``os_optics.catalog``: once on first
use, then at most hourly; offline the stored release is used).

Quick start::

    from os_optics.local import example_project, load_public_catalog, run_optical_project

    catalog = load_public_catalog()                        # the active public optical catalog (local catalog store)
    catalog.select(category="LENS", wavelength_nm="780")   # products usable at 780 nm
    catalog.select(category="LENS", focal_min_mm="-60", focal_max_mm="-40")  # by signed nominal focal length (FocalLengthQuery)
    lens = catalog.select(category="LENS", form="PLANO_CONVEX_LENS")[0]
    lens.specifications.focal_length_mm                    # what a designer compares products by
    lens.simulation_models[0]                              # its public SURFACE_SEQUENCE model: surfaces, media, band
    catalog.material("N-BK7"), catalog.coatings            # published dispersion / coating models products refer to

    project = example_project()                            # a complete reference design (ProjectDocumentV2)
    folded = example_folded_project()                      # the same with a 45 deg fold mirror (source -> mirror -> lens)
    example_project_of_kind("beam_expander")               # also: relay_4f, folded_expander, dichroic_routing, polarization,
                                                           # two_beam_interference, two_color_dichroic, dichroic_combiner
    run = run_optical_project(project, output_dir="run")   # ONE real Engine run; writes the run directory
    run.local_validation.simulation.outcome   # what was decided: execution (COMPLETED / PARTIAL / FAILED) and the
                                # declared targets' requirements (met / unmet / indeterminate / not_applicable)
    run.status                  # the diagnostic summary: PASS / WARNING / FAIL / UNKNOWN / OUT_OF_MODEL_SCOPE / NOT_APPLICABLE
    run.result                  # the full Engine ValidationResult (targets with values, beams, issues)
    run.local_validation        # LocalValidationRecordV1 (Project / catalog / Engine hashes, retained DOFs)
    run.optical_view            # OpticalSimulationSceneV1: the 3D Optical Simulation View of this same run
    run.model_scope_issues      # where the run left a published model (or a beam the trace cannot follow); a FAIL run can
                                # have them too, and a tabulated thin lens reports its domain in run.result.issues instead
    run.artifacts.paths         # run/manifest.json, project.json, engine_request.json, simulation_result.json,
                                # ray_data.json, local_validation.json, optical_scene.json, optical_scene.glb
    verify_run_artifacts("run") == ()                      # re-check every hash and cross-reference
    edited = update_project(project, [                     # headless edit operations, applied atomically
        {"op": "set_source_model", "instance_id": "SRC", "changes": {"wavelength_nm": 850}}],
        expected_project_hash=project.content_hash())     # refused PROJECT_CHANGED when the Project moved on
    edited.project, edited.project_hash, edited.changed, edited.affected, edited.warnings
    edited.check_ok, edited.check_problems                 # whether a run accepts the edited Project, and what it refuses
                                                           # (require_check=True refuses the edit instead)
    run_freshness(edited.project, "run")["freshness"]      # HISTORICAL (PHYSICS_CHANGED): "run" is the old design's

    from os_optics.results import load_run            # the scientific results of the run directory
    results = load_run("run")
    results.describe()          # what the run holds, frame and sign conventions
    results.monitor("focal_plane"), results.rays(monitor="focal_plane"), results.power_budget()
    results.export("arrivals", "csv")                      # also jsonl / json; 'os-optics export ...' in a shell

    from os_optics.local import EvaluationSession     # selected quantities, and nothing else, for a candidate loop
    session = EvaluationSession()                     # keeps catalogs, compiled elements and Engine products
    ev = session.evaluate(project, outputs=["readings"])   # the plane readings with their model-scope findings
    ev.results().monitor("focal_plane")                    # a part not computed is refused (NOT_COMPUTED, naming it)
    ev.extend(outputs=["plane_bounds"]); ev.write_run("run")   # computes the rest, reusing what ran
    # the ids: capabilities()["evaluation"]; 'os-optics project evaluate project.json --outputs readings' in a shell

Catalog releases: a component's ``product.catalog_version`` pins the release it was designed against, and a run
uses exactly that release. ``update_project_catalog(project)`` re-pins the components whose product is unchanged in the
active release and reports changed / withdrawn products. ``os-optics catalog status|sync|import FILE`` (and
``os_optics.catalog``) manage the local store; ``install_catalog(bytes)`` installs a release in memory.

The same from a shell: ``os-optics catalog list``, ``os-optics catalog show OS-OPT-...``,
``os-optics project example > project.json`` (``--kind KIND`` for another reference design, ``--folded`` for the
folded one), ``os-optics project run project.json --out-dir run``, ``os-optics project verify run``,
``os-optics results describe run`` (``results monitors|monitor|paths|power-budget|rays|wavefront|irradiance|metrics``)
and ``os-optics export arrivals run --format csv --out arrivals.csv``.

(``help(os_optics.local)`` also lists every re-exported contract class; ``print(os_optics.local.__doc__)``
shows just this guide.)

How catalog products are simulated: every product of every category is compiled by the same generic rules
(``os_optics.local.compile``): its published ``SURFACE_SEQUENCE`` model becomes real Engine surfaces -- spherical
and plane refracting interfaces between the catalog's dispersive materials, coated reflectors, splitting surfaces --
and a ``POLARIZATION_ELEMENT`` model a Jones-matrix retarder or polarizer. Refraction, dispersion, Fresnel losses, coatings,
Gaussian beams and polarization all come from the Engine's general physics; nothing is specific to a product. So lenses
may be combined freely (telescopes, relays, 4f systems, a lens behind a diverging beam), several source wavelengths may
share any element, and each product only has to be used inside what its model is characterized for: its wavelength
band, its coatings' bands and their angle-of-incidence range (judged on the chief ray).

Your own optics (declared models): a Project may declare optical models by their physics in ``optical_models`` -- the
catalog's model kinds (``SURFACE_SEQUENCE``: coaxial surfaces and the media between them, air gaps included, so a
multi-element objective is one model; ``SURFACE_SET``: posed surfaces and their solid; ``POLARIZATION_ELEMENT``) plus an
``APERTURE_STOP`` (iris, pinhole, slit, annulus), an ``IDEAL_THIN_LENS``, a ``FARADAY_ROTATOR`` (rotation and
transmittance per wavelength, nonreciprocal), a ``DIFFRACTION_GRATING`` (order directions from its period and grooves, order
powers from a stated response), a ``TABULATED_THIN_LENS`` (focal length and
delivered power per wavelength from a table, with its validity domain, validated metrics and evidence) and a ``COMPOUND``
unit of member models at fixed poses -- with their own ``model_materials`` (Sellmeier, tabulated or constant index, optional bulk absorption) and
``model_coatings``. A ``custom_part`` component names one with ``optical_model_id``. It is compiled by the same rules into
the same Engine surfaces (``compile_model``), traced, checked and drawn like a catalog product, and needs no catalog.
``declared_copy(product, catalog)`` (``os-optics project model-from-catalog SKU``) turns a catalog product's model
into a declared one to start from.

What a run follows: each beam along its chief ray, with pupil sample rays traced exactly through every surface (they
show real aberrations), one power and one paraxial Gaussian beam. The run reports where that picture breaks down
(``run.model_scope_issues``; status at least ``OUT_OF_MODEL_SCOPE``): part of the beam falls on an element its chief ray misses (``BEAM_PARTIALLY_INTERCEPTED``), light leaves or
crosses an element through its edge (``RAY_LEFT_ELEMENT_THROUGH_EDGE``, ``RAY_CROSSES_ELEMENT_BODY``), or the pupil rays
spread more than twice as wide as the Gaussian beam allows (Engine issue ``GAUSSIAN_ABERRATION_DOMINATED``: that beam's
Gaussian metrics are withheld; smaller aberrations are not flagged).

Writing your own Project (``ProjectDocumentV2``: ``schema="opensci.project"``, ``project_schema_version="2.0"``;
``os-optics schema project`` prints its JSON Schema; the easiest start is to edit ``example_project().payload()``):

* Design frame: right-handed, millimetres, +Z is "up" (the 3D view keeps +Z up). Lay the beam line in the XY plane;
  the example runs it along +X at z = 0. A pose's quaternion ``(w, x, y, z)`` turns the element's local frame into the
  design frame: ``(0.5, 0.5, 0.5, 0.5)`` points local +Z along design +X with local +Y up (design +Z),
  ``(0, 0, sqrt(0.5), sqrt(0.5))`` points local +Z along design +Y with local +Y up, and the identity ``(1, 0, 0, 0)``
  keeps local +Z along design +Z. Keeping local +Y up makes Tx / Rx horizontal and Ty / Ry vertical on every element;
  ``os_optics.local.example`` documents the quaternions of every example (turned-around lenses, fold mirrors,
  waveplates turned about the beam).

* ``components``: optical elements only. A catalog element has ``product={"optical_sku": "OS-OPT-...",
  "catalog_version": ...}`` and an ``optical_role`` its category may play (``product.design_roles``: a LENS is a
  ``lens``, a MIRROR a ``mirror``, a BEAMSPLITTER a ``splitter``, a FILTER a ``filter`` or a ``splitter`` (a dichroic
  mirror), a WINDOW a ``window``, a WAVEPLATE a ``waveplate``, a POLARIZER a ``polarizer``); a light source is a
  ``custom_part`` with ``optical_role="source"`` and an explicit ``source_model``: an ``ExtendedEmitterSourceV1`` (a finite
  planar emitter: ``emitting_surface``, ``angular_law``, spectrum, total radiant power, ``sampling``; its class states the
  frame and normalization), a ``FluorescenceSourceV1`` / ``RamanSourceV1`` (a sample whose emitted line powers follow from
  the excitation the run delivers to its observation plane, ``RunResults.sample_emission``; a plane's ``detection``
  (``DetectionV1``) reads the light with a stated detector and lock-in, ``RunResults.detection``), a sampled source --
  ``SampledIntensitySourceV1`` (an arbitrary intensity pattern on a grid),
  ``SampledFieldSourceV1`` (a coherent complex field on a grid) or ``SpeckleSourceV1`` (an illumination through seeded random
  phase screens): traced as rays, a field propagated by ``RunResults.field`` -- or a ``GaussianBeamSourceV1`` (wavelength,
  power, 1/e^2 waist radius, waist position, M^2, polarization -- nothing is defaulted -- and, for interference, its
  coherence: ``linewidth_nm`` or ``coherence_length_mm``, see below). A pulsed source adds ``pulse``
  (``repetition_rate_hz``, ``pulse_duration_s`` -- the FWHM of the intensity, ``duration_definition="FWHM"`` -- and
  optionally ``total_pulses``, the pulses it sends while the optics stay in place: a whole number, or ``"UNLIMITED"``
  when it keeps pulsing as long as they are in use); its powers are then average powers and a pulse's energy is the
  average power over the repetition rate. Without ``pulse`` the source is continuous. A source of several spectral lines (a 532 + 1064 nm
  laser, a multi-line ion laser, a combined RGB source) lists them in ``spectral_lines`` (each ``wavelength_nm`` and
  ``power_w``, distinct wavelengths) instead of ``wavelength_nm`` and ``power_w``: every line is traced as its own beam
  with its own power, dispersion and coating response, every observation reports each line's beam with its
  ``wavelength_nm`` (and ``power_by_wavelength``), and the power budget is per line. Waist, M^2, polarization and
  coherence apply to every line. The user's own camera, spectrometer, power meter
  (``optical_role="detector"``) or beam dump (``"dump"``) may be a ``custom_part`` too when an absorbing observation plane
  is attached to it (``device_instance_id``, ``absorbing=true``: its sensitive or absorbing face); it has no optical
  surfaces of its own. An attached plane's ``pose`` is in the instrument's local frame, not the design frame: the
  identity pose puts the plane at the instrument's ``nominal_pose`` with its normal along the instrument's local +Z
  (OpenSci's assembly resolution and Assembly Preview place it at that same point). Every element has a
  ``nominal_pose`` (position in mm, unit quaternion w, x, y, z) placing its local frame (``product.frame``):

  - a transmissive element (lens, window, filter, waveplate, polarizer): the origin is its first surface vertex and
    local +Z its optical axis; light normally arrives from local -Z. Turn it around (local +Z against the beam) and the
    light meets its last surface first -- the Engine traces that too;
  - a reflector or plate splitter (``frame.reference_axis`` ``FACE_NORMAL``: flat / concave mirror, plate beamsplitter,
    beam sampler, dichroic mirror): the origin is the centre of its coated face and local +Z the face's outward normal;
    light must arrive from the +Z side (a 45 deg fold turns the beam by 90 deg); a splitter's substrate lies behind the
    face (local z < 0) and transmits too;
  - a waveplate's fast axis / a polarizer's transmission axis is local +X: turn the element about its local Z to set it.

  ``retained_dofs`` (``Tx..Rz`` in that local frame, ``MANUAL`` or ``NUMERICAL``) record which degrees of freedom stay
  adjustable (design intent). ``MANUAL``: adjusted by hand when the system is aligned; ``NUMERICAL``: intended to be
  driven by a numerically controlled actuator. Choose per axis; the commercial resolution of a submitted design states
  how each retained DOF is delivered. A retained DOF may state the limits of the stage that provides it, in the axis's
  unit (mm for ``T*``, deg for ``R*``) relative to the nominal pose: ``travel`` ``{min <= 0, max >= 0}``, and with it
  ``hard_limits`` (containing the travel), ``repeatability`` (+/-), ``drift`` ``{per_kelvin, per_hour}`` and ``unit``.
  The run records them per DOF; the targets ``dof_travel_margin_mm|deg`` (parameters ``axis``, ``required_min`` /
  ``required_max``: the adjustment the design needs), ``dof_drift_mm|deg`` (``axis``, ``temperature_span_K`` /
  ``duration_h``) and ``dof_repeatability_mm|deg`` (``axis``) on ``device_instance_id`` compare them; a DOF that states
  no such limit leaves its target ``INDETERMINATE`` (``MECHANICAL_LIMIT_NOT_DECLARED``).
* ``observations``: planes (their local +Z is the plane normal) where beams are measured. Every observation reports
  the beams arriving there in ``run.result.observations`` (``simulation_result.json``) -- position, Gaussian radius,
  waist distance and radius, power, polarization -- whether or not a target uses it; ``os_optics.results``
  (``load_run``) gives them, the pupil rays at each plane, wavefronts and the power budget as tables and exports.
* ``targets``: a ``metric`` from ``target_metrics()`` (``metric_descriptors()``: sign conventions, operands, frames) on
  an observation, with ``minimum`` / ``maximum`` or
  ``target_value`` plus ``tolerance_abs`` / ``tolerance_rel`` (a target must set at least one bound; to only measure,
  use an observation without a target; its beams are still reported). With several beams on one plane, select one
  with ``source_id`` and / or ``wavelength_nm`` (the line of a multi-line source); ``power_W`` sums every matching beam.
  ``parameters`` take only the keys the metric lists (the ``parameters.*`` entries of its descriptor's ``selectors``:
  ``path_id`` a string such as ``"SRC#0"``, ``crossing_index`` an integer; two-beam ``path_id_a`` / ``path_id_b`` and,
  with both, ``crossing_index_a`` / ``crossing_index_b``; ``dose_mj_per_cm2``, a number, for
  ``estimated_exposure_time_s``). A target with any other key or value type is not assessed: the check warns
  (``TARGET_PARAMETER_INVALID``) and the run reports it ``UNKNOWN`` with that code.
* Interference (two-beam metrics ``fringe_period_nm``, ``fringe_visibility``, ``coherence_margin``, ... on an
  observation where two beams of one source meet; ``source_id`` / ``source_id_b`` select the pair): beams of one source
  are mutually coherent within its coherence length, beams of two source components are not. Declare the coherence on
  the source model, ``linewidth_nm`` (spectral FWHM) or ``coherence_length_mm`` (one of the two; for a multi-line source
  it holds for each line, and a linewidth gives each line the coherence length of its own wavelength). Lines of one
  source have different frequencies and do not interfere with each other: only beams of the same line form fringes.
  Without it the
  line is taken as monochromatic: the coherence-dependent metrics (``COHERENCE_METRICS``: visibility, peak irradiance,
  coherence factor) have values with a ``value_basis`` entry (assumed ``FULL_COHERENCE``) at a degree of coherence of 1
  where neither line of the pair states its coherence (a target on one of them is judged on the interval every degree of
  coherence from 0 to 1 gives) and at the other line's coherence length where that line states it (a target is judged from
  0 up to that value; the peak irradiance from the mean irradiance), and the coherence length and margin are not
  reported. Where the two paths' relative group delay is not determined (an element without a
  thickness, a coating without a stated phase or group-delay bound) the metrics are stated at no added delay at those
  passes, with an ``ESTIMATE`` entry naming them (assumed ``UNSTATED_GROUP_DELAY_ZERO``), and decide no target; they are
  not computed (issue ``COHERENCE_UNDEFINED``, informational; a target on one of them is ``UNKNOWN``) where a medium
  states no group index at the wavelength. Coatings whose catalog data bound their group delay (``group_delay_bound_fs``)
  leave it known within the sum of the bounds the two paths do not share: the metrics of a stated coherence are then
  stated with the interval of the degree of coherence (``InterferenceResult.coherence_factor_min`` /
  ``coherence_factor_max``), whatever its width, and a target is judged on that interval. Where one line of the pair
  states its coherence length and the other none, the metrics behind such coatings are the values at the traced delay,
  ``ESTIMATE`` (assumed ``FULL_COHERENCE`` and ``UNSTATED_GROUP_DELAY_ZERO``), ``coherence_factor_max`` is 1 (the bounds
  allow any degree of coherence at a shorter length of the unstated line), no ``coherence_factor_min`` is stated, and a
  target is judged from 0 up to the value at ``coherence_factor_max`` -- as it is where the two paths' group-delay
  dispersions differ, ``coherence_factor_max`` then the largest any shorter length gives: unlike the estimate at an undetermined delay, this one decides the targets that interval
  decides. ``example_project_of_kind("two_beam_interference")`` is a complete example;
  ``example_project_of_kind("two_color_dichroic")`` is one of a two-line source.
* Where to expect a focus: ``compile_element`` + ``paraxial_matrix`` give a lens's paraxial matrix at any wavelength
  (effective focal length ``-1/C``, back focal distance ``-A/C`` from its last vertex); ``example_project()`` places its
  focal plane that way. ``paraxial_analysis(project, {"components": [...], "wavelength_nm": ..., "solve": "afocal" |
  "collimate"})`` does it for placed components of a Project: system ABCD, focal distances, principal planes, the afocal
  vertex gap of two elements and the collimating distance from a source waist, with where to move the component
  (``os-optics project paraxial``); ``cavity_analysis(project, {"components": [M1, ..., M2], "wavelength_nm": ...})`` gives a
  linear cavity's stability, Gaussian eigenmode, Gouy phase, free spectral range and finesse, and with ``injection`` the resonant
  build-up of light fed through its first mirror (``os-optics project cavity``). The Gaussian waist of a collimated beam sits slightly before the paraxial ray focus; the targets
  and ``waist_distance_*`` report the Gaussian waist.

Reading the outcome:

* What was decided is in ``run.local_validation.simulation.outcome`` (also ``summary()["outcome"]``): ``execution`` says
  whether the calculation completed, ``requirements`` lists the declared targets met, unmet, indeterminate and not
  applicable; each target's ``assessment`` (MET / UNMET / INDETERMINATE / NOT_APPLICABLE) and each plane's
  ``quantity_state`` (KNOWN / KNOWN_ZERO / BOUNDED / UNKNOWN / ...) say the same per item. A Project without targets has
  no requirement assessment, and its completed run is not an approval of the design.
* ``run.status`` is the diagnostic summary, the most severe of every target, issue and stage, never an approval of the
  design: ``PASS`` / ``WARNING``: nothing more severe was found (``WARNING`` lists issues worth reading, and may stand
  beside a MET target); ``FAIL``: a target missed or the beam was lost (see ``run.result.targets`` and
  ``run.result.issues``); ``UNKNOWN``: something could not be evaluated; ``OUT_OF_MODEL_SCOPE``: the question is outside
  what the published models can answer faithfully -- e.g. a
  wavelength outside a product's band or in a dichroic's transition gap, a coating met beyond its published angle of
  incidence, a beam partly intercepted or leaving an element through its edge (``run.model_scope_issues`` says which
  element, surface and value), or an aberration-dominated focus (an Engine issue) -- never treat it as a pass.
* A run used a model outside its validity when ``run.model_scope_issues`` is not empty or any issue in
  ``run.result.issues`` or target has status ``OUT_OF_MODEL_SCOPE`` (a tabulated thin lens reports its table's domain
  as Engine issues, also listed in ``run.model_scope_issues``). The status is the worst of all outcomes (``FAIL`` > ``OUT_OF_MODEL_SCOPE`` > ``UNKNOWN`` >
  ``WARNING`` > ``PASS``), so such a run is ``FAIL`` when a target also fails, and a target outside the model's band may
  read ``PASS`` while the run is not. ``results.load_run(run).targets()`` answers it in one call: ``in_model_scope``
  and ``out_of_model_issues``.
* A Project that cannot be simulated as given raises ``LocalRunError``; its ``problems`` carry stable
  ``LocalRunErrorCode`` values (unknown optical SKU, a product in a role its category cannot play, missing source
  model, unsupported metric, ...).

A design holds optical elements, sources, observation planes and targets; OpenSci configures the mounting hardware
when a design is submitted. The 3D view of a run is its Optical Simulation View. The design contracts re-exported here
are the OpenSci contract definitions bundled with this distribution. The low-level Engine API
(``os_optics.validate``, ``os_optics.validate_with_ray_paths``, ``ValidationRequest``, ``builders``) remains
available.
"""

from .._vendor.shared_contracts.common.geometry import PoseV1
from .._vendor.shared_contracts.design_catalog import (
    CatalogQueryError,
    FocalLengthQuery,
    FocalQueryRefusal,
    OpticalCategory,
    OpticalForm,
    PolarizationElementModelV1,
    PublicCoatingV1,
    PublicMaterialV1,
    PublicOpticalCatalogV2,
    PublicOpticalProductV2,
    SurfaceSequenceModelV1,
)
from .._vendor.shared_contracts.optical_design.design_content import CustomPartRefV1
from .._vendor.shared_contracts.optical_design.design_content import CircularApertureV1, ObservationV1, RectangularApertureV1, TargetV1
from .._vendor.shared_contracts.optical_design import (
    DofAxisV1,
    DetectionV1,
    DofControlIntentV1,
    ExtendedEmitterSourceV1,
    FluorescenceSourceV1,
    SampledFieldSourceV1,
    SampledIntensitySourceV1,
    SpeckleSourceV1,
    GaussianBeamSourceV1,
    LinearPolarizationV1,
    LocalRuntimeKindV1,
    LocalValidationRecordV1,
    LocalValidationStatusV1,
    OpticalComponentInstanceV1,
    OpticalProductRefV1,
    OpticalRoleV1,
    ProjectDocumentV2,
    RamanSourceV1,
    RetainedDofV1,
    SimulatedPartV1,
    check_local_validation_binding,
    resolve_simulated_parts,
)
from .._vendor.shared_contracts.optical_view import OpticalSimulationSceneV1, OpticalViewSettingsV1, PupilRaySelectionV1
from .artifacts import (
    ENGINE_REQUEST_FILE,
    LOCAL_VALIDATION_FILE,
    MANIFEST_FILE,
    PROJECT_FILE,
    SIMULATION_RESULT_FILE,
    RunArtifacts,
    verify_run_artifacts,
    verify_run_files,
    write_run_artifacts,
)
from .catalog import (
    InstalledCatalog,
    catalog_resolution_status,
    clear_installed_catalogs,
    install_catalog,
    installed_catalogs,
    load_project,
    load_public_catalog,
    pinned_catalog_version,
)
from .catalog_update import ProjectCatalogUpdate, update_project_catalog
from .compile import (
    CatalogResolver,
    CompiledElement,
    ElementIdentity,
    ModelResolver,
    ProjectResolver,
    SurfaceEnvelope,
    compile_declared,
    compile_element,
    compile_model,
    declared_design_geometry,
    declared_frame,
    engine_coating,
    engine_material,
    engine_material_id,
    paraxial_matrix,
)
from .declared import declared_copy
from .errors import LocalRunError, LocalRunErrorCode, LocalRunProblem
from .example import (
    EXAMPLE_KINDS,
    example_beam_expander_project,
    example_dichroic_combiner_project,
    example_dichroic_routing_project,
    example_folded_expander_project,
    example_folded_project,
    example_polarization_project,
    example_project,
    example_project_of_kind,
    example_relay_4f_project,
    example_two_beam_interference_project,
    example_two_color_dichroic_project,
)
from .network import export_linear_optical_network, export_request_network
from .paraxial import ParaxialError, paraxial_analysis
from .system_aperture import SystemApertureV1, system_aperture_analysis, trace_system_pupil
from .cavity import CavityError, cavity_analysis
from .freshness import RunNotReadable, run_freshness
from .project_ops import PROJECT_OPS, ProjectOpError, ProjectUpdate, ProjectUpdateError, apply_project_ops, update_project
from .lowering import COHERENCE_METRICS, LoweredProject, lower_project, metric_descriptors, pure_optical_engine_configuration, target_metrics
from .record import detect_runtime_kind, local_validation_record
from .run import LocalOpticalRun, run_optical_project
from .evaluate import Evaluation, EvaluationSession, evaluate_project
from .configurations import (
    ConfigurationRun,
    ConfiguredProjectRun,
    configurations_run_files,
    run_configured_project,
    verify_configurations_run,
)
from .validity import AOI_TOLERANCE_DEG, MODEL_SCOPE_CODES, ModelScopeIssue

__all__ = [
    "AOI_TOLERANCE_DEG",
    "COHERENCE_METRICS",
    "ENGINE_REQUEST_FILE",
    "EXAMPLE_KINDS",
    "LOCAL_VALIDATION_FILE",
    "MANIFEST_FILE",
    "MODEL_SCOPE_CODES",
    "PROJECT_FILE",
    "PROJECT_OPS",
    "SIMULATION_RESULT_FILE",
    "CatalogQueryError",
    "CatalogResolver",
    "CavityError",
    "CircularApertureV1",
    "ConfigurationRun",
    "ConfiguredProjectRun",
    "CompiledElement",
    "CustomPartRefV1",
    "DetectionV1",
    "DofAxisV1",
    "DofControlIntentV1",
    "ElementIdentity",
    "Evaluation",
    "EvaluationSession",
    "ExtendedEmitterSourceV1",
    "FluorescenceSourceV1",
    "SampledFieldSourceV1",
    "SampledIntensitySourceV1",
    "SpeckleSourceV1",
    "FocalLengthQuery",
    "FocalQueryRefusal",
    "GaussianBeamSourceV1",
    "InstalledCatalog",
    "LinearPolarizationV1",
    "LocalOpticalRun",
    "LocalRunError",
    "LocalRunErrorCode",
    "LocalRunProblem",
    "LocalRuntimeKindV1",
    "LocalValidationRecordV1",
    "LocalValidationStatusV1",
    "LoweredProject",
    "ModelResolver",
    "ModelScopeIssue",
    "ParaxialError",
    "ObservationV1",
    "OpticalCategory",
    "OpticalComponentInstanceV1",
    "OpticalForm",
    "OpticalProductRefV1",
    "OpticalRoleV1",
    "OpticalSimulationSceneV1",
    "OpticalViewSettingsV1",
    "PolarizationElementModelV1",
    "PoseV1",
    "ProjectCatalogUpdate",
    "ProjectOpError",
    "ProjectUpdate",
    "ProjectUpdateError",
    "ProjectDocumentV2",
    "ProjectResolver",
    "PublicCoatingV1",
    "PublicMaterialV1",
    "PublicOpticalCatalogV2",
    "PublicOpticalProductV2",
    "PupilRaySelectionV1",
    "RamanSourceV1",
    "RectangularApertureV1",
    "RetainedDofV1",
    "RunArtifacts",
    "RunNotReadable",
    "SimulatedPartV1",
    "SurfaceEnvelope",
    "SurfaceSequenceModelV1",
    "SystemApertureV1",
    "system_aperture_analysis",
    "trace_system_pupil",
    "TargetV1",
    "catalog_resolution_status",
    "check_local_validation_binding",
    "apply_project_ops",
    "clear_installed_catalogs",
    "compile_declared",
    "configurations_run_files",
    "cavity_analysis",
    "compile_element",
    "compile_model",
    "declared_copy",
    "declared_design_geometry",
    "declared_frame",
    "detect_runtime_kind",
    "engine_coating",
    "engine_material",
    "engine_material_id",
    "evaluate_project",
    "export_linear_optical_network",
    "export_request_network",
    "example_beam_expander_project",
    "example_dichroic_combiner_project",
    "example_dichroic_routing_project",
    "example_folded_expander_project",
    "example_folded_project",
    "example_polarization_project",
    "example_project",
    "example_project_of_kind",
    "example_relay_4f_project",
    "example_two_beam_interference_project",
    "example_two_color_dichroic_project",
    "install_catalog",
    "installed_catalogs",
    "load_project",
    "load_public_catalog",
    "local_validation_record",
    "lower_project",
    "metric_descriptors",
    "paraxial_analysis",
    "paraxial_matrix",
    "pinned_catalog_version",
    "pure_optical_engine_configuration",
    "resolve_simulated_parts",
    "run_configured_project",
    "run_freshness",
    "run_optical_project",
    "target_metrics",
    "update_project",
    "update_project_catalog",
    "verify_configurations_run",
    "verify_run_artifacts",
    "verify_run_files",
    "write_run_artifacts",
]
