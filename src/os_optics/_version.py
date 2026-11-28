"""Single source of truth for engine / schema / model versions."""

# Package version on PyPI. 0.1.0 was the first public release (the low-level Engine only); 0.2.x adds the local design
# workflow (bundled public optical catalog, ProjectDocumentV2 runtime, Local Validation, Optical Simulation View, ray-path
# API, local assembly validation, protected-delivery opening, Detailed Assembly Drawing and client artifact uploads);
# 0.3.x adds Python 3.11+, `doctor`, the local 3D viewer and the commercial client (`os_optics.client`); 0.3.1 adds
# shipping addresses (`account address`, `order create --address`), `order withdraw`, the purchase options (`resolve
# --no-breadboard` / `--assembly-service`) and the browser commercial submission
# (`local.json_api.commercial_submission`); `resolve` no longer takes `--beam-height-mm`. 0.4.0 stops bundling the public
# optical catalog: the catalog is versioned data synchronized from the OpenSci catalog service into a local store
# (`os_optics.catalog`, `os-optics catalog status|check|sync|verify|import`; first use syncs, then at most
# hourly; offline the stored release is used), a Project runs against the catalog release its components pin,
# `project update-catalog` / `local.update_project_catalog` move a Project to the active release, the browser runtime
# installs the catalog it fetched (`local.json_api.install_catalog`), and the commercial client backs off on busy /
# unavailable responses (Retry-After, IDEMPOTENCY_IN_PROGRESS, RESOLUTION_CAPACITY_BUSY). Catalog releases need no
# Engine release. 0.5.0 adds the scientific result layer: self-describing observation planes (resolved frame,
# absorbing semantics), per-arrival crossing index / polarization ellipse / first-order ray-transfer matrix and image
# orientation, a power budget, metric descriptors, the ray data artifact (`ray_data.json`), `os_optics.results` and
# the `results` / `export` commands, and catalog schema 3.0 (general surface sets: tilted / rectangular faces, cubes,
# prisms, wedges, aspheres, cylinders, off-axis conics, polarization-resolved and tabulated coatings). 0.5.0 also adds the
# Optical Layout Drawing of an optics-only purchase (`os_optics.optical_layout`, `layout draw`, `order layout`,
# `resolution layout`, `local.json_api.generate_optical_layout`) and makes the Detailed Assembly Drawing a customer
# deliverable: drawing schema 4.0 (artifact-store file roles, `--locale zh-CN|en` with Chinese PDF text, Order Number and
# customer in the title block, optical spacing and alignment) over Assembly Detail schema 3.1; the purchase options
# `resolve --optics-only` and `--spare SKU=N`, and `order create --additional-order` (an explicit further Order of an
# already ordered design). 0.6.0 is a minor release (new result fields, a new example kind and new preview fields)
# with the audit fixes: light from behind a lossless coated face is in model scope (combiners, Michelson and
# Mach-Zehnder with plate splitters; the new `dichroic_combiner` example kind), counter-propagating beams add no
# interference term, an absorbing plane reports the power detected on its active area
# (`ArrivalResult.detected_power_W` and its coherent power, schema 1.3.0), a plane through an element's hit point
# records the beam and its rays on the plane, single design wavelengths and angles of non-filter forms have a tolerance
# and a single design angle of an interference coating (a dichroic) one derived from its edge margin, a run has a
# bounded trace budget that traces the strongest first (numerical config 1.4.0; one MAX_PATHS WARNING with counts, a
# target on untraced light is UNKNOWN), the beam trace follows the ports of cubes, prisms and off-axis mirrors in the
# assembly preview and drawing (new preview scene fields), the drawing identifies elements by id and states the
# optical-centre datum and the face of an asymmetric optic, the CLI states how `resolve` and `report-transfer` behave
# after OpenSci's basis changes (the server recognises the repeat), and malformed or extreme input, usage errors,
# damaged run directories and identifiers with control characters get structured refusals with their documented exit
# codes. 0.6.1 is a fix release: the Optical Simulation View draws a beam as a main beam only for an arrival a Project
# target asks for (`designed_arrivals`: its source and selected spectral line, a positive amount of light, not a leak
# bound), and light beyond an element that rejects its line spectrally is secondary; the snapshot legend prints each
# beam's display role; the commercial client's recovery hints for refused layouts name the refusal fields an Agent
# applies (`suggested_positions_design_mm`, `conflicting_pairs`, `smallest_board_for_footprint_mm`); a `project run`
# that does not complete leaves no earlier run looking current in `--out-dir`, and `results` refuses a directory
# without a manifest; target parameters are checked against the target's metric (`TARGET_PARAMETER_INVALID`: an
# unknown key, a non-string `path_id`, a non-integer `crossing_index`, a malformed parameter entry); an explicit
# `--samples` is never replaced by the default (out of range is refused); and the results queries take `crossing_index`
# and `min_rays` (at least 1) as integers only. 0.7.0 is a minor release: a Project can declare optical models by their
# physics (`optical_models` with `model_materials` / `model_coatings`, used by `custom_part` components through
# `optical_model_id`; the catalog's SURFACE_SEQUENCE / SURFACE_SET / POLARIZATION_ELEMENT vocabulary plus APERTURE_STOP,
# IDEAL_THIN_LENS and COMPOUND units), compiled by the same code as catalog products, drawn in the Optical Simulation
# View and checked for physical-solid validity; a Project states its `design_intent` (LAB_ASSEMBLY or OPTICAL_SYSTEM);
# `project import-prescription` / `local.json_api.import_prescription` import a lens prescription (csv-surface-table/1);
# `native` / `native_components` / `build_native_model` build supplier-independent components; `project network` /
# `linear_optical_network` export a linear optical network (opensci-linear-optical-network/v1); `project
# model-from-catalog` / `catalog_model` copy a catalog product's model. Results (schema 1.4.0): a fully traced plane no
# light reaches reads 0 W, an empty target selection is UNKNOWN (TARGET_SELECTION_EMPTY), light whose fate the trace
# did not follow makes a result UNKNOWN, observation planes no longer use the interaction budget, partial capture is a
# WARNING and light outside a detector's acceptance cone is not detected, a central obstruction passes the annulus,
# and a splitter reflects p with the sign of a mirror (r_p = -r_s): light a splitter reflects changes the sign of its p
# component, which changes the power after a polarization-sensitive element downstream and the interference of beams
# with different numbers of splitter reflections unless they are s-polarized. 0.8.0 is a minor release: the composition
# workspace (`opensci.optical_workspace` 1.0: reusable modules with test fixtures, interfaces and requirement scope,
# repeated imports as independent instances, editing groups moved by coordinated transforms about an explicit frame and
# pivot, an operation journal for safe retries, derived validation freshness); catalog schema 3.1 (3.0 still read) and
# the same Project vocabulary: polarization-resolved and angle-resolved coating tables with reflection / transmission
# phases, band-resolved polarizer and waveplate transmittance, attenuators of fixed or position-dependent optical
# density (NEUTRAL_DENSITY), axicon surfaces and acylinders, tabulated thin lenses for externally characterized optics
# (TABULATED_THIN_LENS) and power ratings compared with per-surface exposures; elliptical and pulsed sources; pair
# metrics across spectral lines (`wavelength_nm_b`); and the interop catalog of external scientific tools. Engine
# schema 1.5.0, json_api 1.2. 0.9.0 is a minor release: `json_api.update_project` (`local.project_ops.update_project`,
# `project update`) applies the browser Designer's Project edit operations (its `updateProject` vocabulary plus a rigid
# `transform`) atomically with the check of `check_project`, an optional `expected_project_hash` (`PROJECT_CHANGED`) and
# retry-safe adds (`already_present`); `check_project`, `project check` and the workspace status list advisory
# `warnings` (`TARGET_UNIT_MISMATCH`, `SOURCE_WAVELENGTH_OUTSIDE_COMPONENT_BAND`; nothing accepted before is refused);
# `json_api.run_freshness` / `results freshness` say in one call whether a run is current for a Project; `capabilities`
# prints `json_api.capabilities()`, `results targets` / `RunResults.targets()` (results query `targets`) give every
# target outcome with the model-scope issues, `results rays` takes `--offset` / `--limit`; and the documentation states
# the worst-of status rule (a FAIL run may also have model-scope issues). 0.9.0 adds the
# scientific-result contract: `ValidationResult.outcome` (execution outcome and termination, the resource budgets used
# and which ran out, and the requirement assessment of the declared targets), `TargetResult.assessment`
# (MET / UNMET / INDETERMINATE / NOT_APPLICABLE) and `ObservationResult.quantity_state` (KNOWN / KNOWN_ZERO / BOUNDED /
# UNKNOWN) -- the aggregate `status` stays, as a summary of diagnostic severity and no longer as design approval; a
# Project may state its local `compute_budget` (`ComputeBudgetV1`: max_interactions_per_path, max_paths,
# max_trace_steps, max_interference_pairs), which the Engine input hash binds; and the untraced-light probes follow the
# metrics a Project targets, never the bounds it sets on them, so changing a threshold changes no physical value;
# `Observation.power_upper_bound_W` retains faint-light bounds independently of targets, and its historical absence
# is preserved. Engine schema 1.6.0, json_api 1.3. 0.9.0 also adds the pulses a pulsed source sends while the optics
# stay in place (`pulse.total_pulses`: a whole number or UNLIMITED), compared with a rating's pulse count per site
# (`PULSES_PER_SITE_UNDETERMINED`; a single-pulse rating, or a count with its `test_protocol` S_ON_1, is compared per
# site; a rating without a count, or a count above 1 without a protocol, is not verifiable), a rating's `value_definition`
# (PEAK / TOTAL_PER_1E2_AREA; unstated: no threshold, the `value_definition` condition not verifiable) and
# `RatingComparison.rated_threshold_low` / `_high` (a TYPICAL value: none), a per-pulse lower bound of
# the peak fluence (independent pulse trains no longer add) and the rated conditions in the `power-ratings` export;
# omitting the new fields keeps every Project and Engine input hash. 0.10.0 is a minor release: the engine is published as os-optics
# (import `os_optics`, command `os-optics`; the opensci-engine releases up to 0.9.0 stay as they are, and the
# repository's alias distribution for the earlier name is not published with it). Light the
# untraced-light probes followed to a plane is coded UNTRACED_LIGHT_BOUNDED in every result, whether or not their trace
# budget ran out for other light (it was OUT_OF_MODEL_SCOPE when it did); json_api 1.4 adds the plane's `message` to the
# observation rows of `run_project` / `run_summary`; and the Engine reads catalog schema 3.2 (3.0 and 3.1 still read): a
# coating's `group_delay_bound_fs`, also on a declared coating and on the Engine's `ConstantCoating` /
# `TabulatedCoating`, bounds its group delay; two beams whose differing passes all carry such a bound have their
# degree of coherence, coherence margin, fringe visibilities and peak irradiance stated at the traced delay with the range
# the bounds leave (`InterferenceResult.group_delay_bound_mm`, `coherence_factor_min` / `_max`) where it moves the degree
# of coherence by at most 0.01, and targets on them are judged on that range; their coherent power has a value, with that
# range (`CoherentSuperposition.total_power_min_W` / `_max_W`), only where those coatings state their phases, and
# otherwise only its range over every phase (PHASE_UNDEFINED); likewise the two-beam `phase_difference_rad` has no
# value (PHASE_UNDEFINED, a target on it UNKNOWN) where a pass one beam makes and the other does not has no stated phase
# (an element without a thickness, a coating without a stated phase), and a coating pass that states its phase is
# recorded with `#phase`; omitting the new fields keeps every Project hash. Engine schema 1.7.0 (the coating and result
# fields above change the frozen ConstantCoating, TabulatedCoating, PlaneSurface, SphereSurface and ValidationResult;
# the Engine request's `project_schema_version`, hence every Engine input hash, moves with it), json_api 1.4. 0.11.0 is a minor release: the optical models
# DIFFRACTION_GRATING (customer-defined orders, with `GratingOrderResult`), FARADAY_ROTATOR (nonreciprocal) and extended
# emitters (seeded incoherent sample rays, `ObservationResult.sampled_light`); two-variable sag surfaces (Zernike, XY
# polynomial, biconic, toroid), traced, drawn and imported by `project import-prescription`, which also reads a surface's own
# mechanical edge; catalog schema 3.3 and 3.4 (3.0 to 3.2 still read), and a focal-length filter on catalog queries;
# paraxial analysis of mirrors, folded axes and compound units; a whole-system section with rays and `--frame` for sections
# and snapshots; the pupil field and detector response of `results`; `layout draw` names a missing package
# (`OPTICAL_LAYOUT_PACKAGE_REQUIRED`). A run reports the same bits on every supported Python: totals that used the built-in
# `sum` are correctly rounded, which moves last bits on Python 3.11 to the values 3.12 and later report. Engine schema
# 1.8.0, json_api 1.5. 0.12.0 is a minor release of the result semantics: a plane no traced beam reaches is UNKNOWN, with
# `untraced_power_W` beside it and every target on it INDETERMINATE, wherever followed or unknown-fate light may reach it (the
# supported bound is evidence only; only light left out as negligible leaves it BOUNDED), and part of a beam that crosses it
# is bounded along the beam's energy flow, also on a leg that leaves the scene; a plane through a surface's hit point records
# the power arriving at that surface (`ArrivalResult.surface_coincidence`, `SURFACE_COINCIDENT_ARRIVAL`); a plane's status,
# message and dark-plane issue no longer depend on the targets on it, so the aggregate may read WARNING beside a MET target;
# `ObservationResult.power_basis` and `POWER_INCOHERENT_SUM` label a power added over beams where a coherent power exists for
# them; where a coating states no phase two collinear beams report `combined_power_collinear_min_W` / `_max_W` and
# `phase_statement`; a coating record's angle range is carried into the Engine (`declared_validity`) and read at the light
# left out as negligible on its dense side, refusing the surface's exposure bound where that light leaves it or has no
# air-equivalent angle; a withheld exposure bound is named on the run (`EXPOSURE_BOUND_WITHHELD`); a run records the declared
# reach of a plane without an active area (`NumericalConfig.laboratory_reach_mm`, numerical config 1.5.0). 0.12.0 also names the
# os-optics distribution in every identity it writes or sends: `engine_package` of the Local Validation record, the
# scene, the assembly validation and the drawings, the scene's `builder_package`, the run manifest (1.2:
# `engine_run.engine_package`; a 1.1 directory still verifies), the `User-Agent` `os-optics/<version>` and the PDF producer;
# documents of earlier engines that name opensci-engine are still read. Engine schema 1.10.0. 0.13.0 is a minor release of
# the design engine: analyses derived from a run, each an `AnalysisResultV1` document (`opensci.analysis_result` 1.0: execution,
# model bases with their validity checks, quantities with their state, interval and convergence evidence, completeness, and an
# identity that keeps physical inputs apart from thresholds) -- the vector focal field of an ideal aplanatic objective with an
# optional window (`results focal-field`), the point spread function and MTF of a run's image plane (`results imaging`), the
# interface-reflection ghost paths of an image plane with a bound on those not listed (`results ghosts`), linear cavities
# (`project cavity`) and the comparison of a Project's configurations; a Project's `configurations` (pose, source and budget
# overrides, each run as an ordinary run in its own directory, `comparison.json` beside them); partially polarized and
# unpolarized sources (two incoherent components, Stokes parameters and degree of polarization per route), uniaxial crystals
# with plane faces (ordinary and extraordinary beams, walk-off; Project materials with `birefringence` and per-element
# `optic_axes`, beam displacer and Glan builders), dual-wavelength retarders, circular polarizers and the rotation of polarizers
# and retarders; continuous-spectrum sources lowered to quadrature lines with their coherence length; group, phase and time
# delay targets; elliptical, D-shaped and polygonal apertures, holes through an element and lens arrays; diffuse surface scatter
# traced as rays and bulk scattering booked and not followed; a thin amplitude and phase sample in an aperture stop;
# triangle-facet surfaces traced as their planar faces (Engine `MeshSurface`, Project `FACET_SOLID`); second-harmonic generation in
# a uniaxial crystal slab (Project `NONLINEAR_CRYSTAL`, undepleted pump, the harmonic a new spectral line); a beam with no valid
# Gaussian state that a clear aperture cuts continues on
# the share of its pupil samples that pass (`GEOMETRIC_POWER_BASIS`, `ArrivalResult.power_sampling_resolution_W`); a share a
# surface takes before an observation plane bounds its power on `[P - S, P]` (`power_taken_before_upper_W`,
# `ObservationQuantityV1.power_lower_bound_W`), on curved surfaces too; a retained DOF's travel, limits, repeatability and drift
# with their targets; the return-light analysis runs only where a bound reads it and within `max_return_analysis_work`, whose
# exhaustion withholds the bounds it would give; pairs of independent sources are counted per plane (`incoherent_pairs`),
# not listed; `--ray-data summary`; streamed run files; `project run`, `workspace run` and `validate` exit by
# `--status-contract` (default `requirements`; `aggregate` and `OS_OPTICS_STATUS_CONTRACT` select the earlier codes) and
# every run summary names its `exit_contract`; the paraxial analysis takes the light's direction from the Project's source and
# refuses a mirror listed twice; the viewer's camera, framing, background and batch capture, exact snapshot sizes; crossing
# surfaces inside a clear aperture are refused and edges thicker than 0.35 of the clear diameter are reviewed. Engine schema
# 1.11.0, numerical config 1.6.0, json_api 1.6. The new contract fields and numerical settings (the return-light analysis
# budget, the crystal and nonlinear tolerances) are part of every Engine request, so every Engine input hash moves while every
# Project hash stays: a run or workspace record made with 0.12.0 (or an earlier build of 0.13.0) reads as not current
# (`results freshness`, the workspace's derived validation freshness) until the Project is run again with 0.13.0, and
# `project verify` reports a run directory written by 0.12.0 INCONSISTENT ("engine_request.json: does not hash to the
# record's Engine input_hash": its request is read with the new fields at their defaults, which the 0.12.0 hash did not
# cover); run the Project again with 0.13.0 for a run directory that verifies. 0.14.0 is a minor release of composable
# evaluation: an evaluation is a plan over named capabilities (`os_optics.evaluation`: the scene, the beam trace, the secondary
# rays, the plane readings, the power ledger, the untraced light, the plane bounds, the surface exposures, the beam pairs,
# layout, control and the requirements), each with one implementation; `validate` is the complete plan and
# `os_optics.evaluate(request, capabilities=..., exclude=..., targets=..., state=...)` runs a selected one, adding what the
# selection requires and refusing a selection it cannot run (`EvaluationPlanError`: `EXCLUDED_CAPABILITY_REQUIRED`, and
# `TARGET_SELECTION_INCOMPLETE` for a record that would leave a declared target out); the surface exposures of a design
# that states a power rating require the untraced light; an `EvaluationState` keeps each capability's product for the next
# evaluation of the same physical inputs, numerical settings and build; a result of a narrowed evaluation names its scope
# (`ValidationResult.evaluation`: requested, executed, not computed with what each `produces`, the declared targets not
# selected), reports a plane whose bounds were not computed with `quantity_state` `NOT_COMPUTED` and `untraced_power_W`
# null, and gives every target it assesses a result: one a capability outside the scope would decide is `UNKNOWN` with
# issue code `CAPABILITY_NOT_COMPUTED`; every metric descriptor names the capabilities it requires
# (`MetricDescriptor.requires`). Locally a Project is evaluated the same way: `os_optics.local.EvaluationSession` /
# `evaluate_project`, `json_api.evaluate_project` / `evaluation_capabilities` and `os-optics project evaluate` compute
# selected quantities, the plane readings and the requirements always with their model-scope findings, keep catalog,
# compiled elements and Engine products across candidates, and answer results queries from memory (`NOT_COMPUTED`,
# naming every capability the query reads, for what was not computed); layout and control are selected on an Engine
# request (`os_optics.evaluate`), not in a Project evaluation; an evaluation writes nothing (`Evaluation.write_run(directory)` writes the run directory);
# `run_optical_project` is their complete composition; `project evaluate` exits by `--status-contract` over the targets it
# assessed (with no contract named, `requirements` when it assesses targets and `execution` when it assesses none, as the
# default `readings` does; `requirements` named for an evaluation that assesses none of the declared targets is refused,
# 64 `REQUIREMENTS_NOT_SELECTED`; its reply names the declared targets it did not assess in `requirements.not_assessed`,
# and its status is `NOT_APPLICABLE` rather than `PASS` when it assessed none), and the exit code of
# every run command reads, under `requirements`, the execution and the requirement record alone. The imaging request takes `convergence` and `diffraction` and the
# ghost request `convergence` (false: no repeat at twice the sampling; no point spread function). A run reports a number
# that is not simply the model's value with its basis (`value_basis`: MODEL, LOWER_BOUND, UPPER_BOUND or ESTIMATE, with
# what it omits, the stand-ins it assumes and the validity checks it stands on), a finding a quantity carries as a notice
# at that quantity, and the coatings' group-delay bounds give a stated degree of coherence its interval whatever its
# width; the limits and levels that decide whether or how a value is reported are Project settings
# (`numerical_settings`, below). JSON hosts keep an evaluation session between calls through prepared handles
# (`json_api.prepare_evaluation` / `evaluate_prepared` / `cancel_evaluation` / `release_evaluation` /
# `evaluation_handles`). The transverse handoff document is `opensci.transverse_handoff` 1.2 (0.13.0: 1.0): each
# aperture's cut share, and a beam a model-scope finding reaches lists it with its value basis. Engine schema 1.12.0,
# json_api 1.8. The Engine
# request's `project_schema_version` moves with the schema, so every Engine input hash moves while every Project hash
# stays: a run made with 0.13.0 reads as not current until the Project is run again with 0.14.0.
# This release also reads public catalog schema 3.6, a catalog that publishes design materials, materials no product uses (the catalog's
# product material ids are a subset of its material ids; a product naming an unpublished material is still refused, and
# a catalog of schema 3.5 or earlier publishes exactly its products' materials). A 3.6 release (one carrying the upstream
# material library) needs os-optics 0.14.0 or later: os-optics 0.13.0 sees 3.6 in the service's manifest, reports
# ENGINE_UPGRADE_REQUIRED before downloading it and keeps its stored release (an empty store takes the newest retained
# release of schema 3.5 or earlier). 0.14.0 also evaluates every material by one rule on the vacuum wavelength: a law stated
# on air wavelengths is evaluated at lambda / n_ref(lambda) and an index relative to air is multiplied by n_ref (the source's
# reference air), native formula kinds (1-10 term Sellmeier, power series, Herzberger, temperature Sellmeier), measured
# tables, spectral bulk loss and Schott dn/dT; and a native Project operates in its stated environment (air at a
# temperature and pressure, default 20 C and 101325 Pa; VACUUM gaps), so a native Project's Engine input states its air and
# every native Engine input hash moves (Engine schema 1.12.0, materials model 1.1).
# 0.15.0 (in development; source only, nothing published) adds local assembly plans (`os_optics.assembly_plan`, `os-optics
# assembly plan derive|show|check|edit|history|preview|draw|bom|run-optics`, schema `os-optics.assembly_plan` 1.0): the
# public mechanical catalog (`opensci.public_mechanical_catalog` 1.0, `catalog sync` / `catalog mechanical`, `--mechanical
# FILE` / OS_OPTICS_MECHANICAL_CATALOG for the user's own USER-MECH hardware), a plan derived on this device by the
# assembly kernel (mount, post, holder and board hole per optic; findings instead of refusals), revisioned edits with a
# journal, plan-level interface checks, the LOCAL_PLAN Assembly Preview Package (manifest 3.1), Assembly Detail 3.2 and the
# Detailed drawing 4.2 of the user's design, the local viewer of a plan, the design BOM and the purchase BOM; and the
# commerce client of BOM-only ordering: `order quote` with the line review and its confirmation, the Account handoff
# without a token, the retired design-ordering routes answered with the local command, and no drawing upload. json_api 1.9
# (the assembly plan methods and `mechanical_catalog`); the Engine schema (1.12.0) and every Project and Engine input hash
# are unchanged.
__version__ = "0.15.0"
ENGINE_VERSION = __version__

# The PyPI distribution this package is published as (``importlib.metadata.distribution(DISTRIBUTION_NAME)``; the import
# package is ``os_optics``). Every identity the engine writes or sends names it: ``engine_package`` of the Local Validation
# record, the run manifest, the scene, the drawings and the assembly validation, the scene's ``builder_package``, the
# ``User-Agent`` ``os-optics/<version>`` and the PDF producer. It equals the contracts' ``ENGINE_PACKAGE_NAME``
# (tests/test_distribution_identity.py keeps the two, and the installed metadata, in step).
DISTRIBUTION_NAME = "os-optics"

# Oldest supported Python (the package's ``requires-python``; tests/test_python_support.py keeps the two in step).
MIN_PYTHON = (3, 11)

# Public contract (JSON) schema version. It moves once per release: the first field-level change of a public contract after
# a published release bumps it, and further changes before the next release are recorded within that unreleased version (a
# published version's contracts never change in place; tests/generate_fixtures.py --freeze). 1.8.0: the Faraday rotator
# (`FaradayRotatorInteraction` on the frozen PlaneSurface), extended emitters (`EngineeringComponentSnapshot.emitter`,
# `ObservationResult.sampled_light`), the two-variable sag shapes (`SagSurface.sag_shape`), their evidence
# (`ValidationResult.surface_profiles`) and the SAG_* issue codes; a Project that uses none of them keeps its hash, and the
# Engine request's `project_schema_version`, hence every Engine input hash, moves with this. 1.9.0: the result fields of the
# observation semantics (`ArrivalResult.surface_coincidence`, `ObservationResult.power_basis`,
# `InterferenceResult.combined_power_collinear_min_W` / `_max_W`, `phase_statement`) and the coating use fields. 1.10.0:
# `NumericalConfig.laboratory_reach_mm`. 1.11.0: the analysis result contract, partially polarized sources and their Stokes
# results, uniaxial crystals, continuous spectra, aperture shapes and holes, scatter, complex transmission and facet surfaces,
# sampled sources (intensity maps, sampled fields, speckle fields: the sampled source lines' pattern fields),
# the geometric power basis and taken-before intervals, incoherent pair counts, the return-analysis budget and the crystal
# tolerances of NumericalConfig, detection on an observation plane (`Observation.detection`, `ObservationResult.detection`)
# and the sample emission record (`EngineeringComponentSnapshot.sample_emission`), and the issue codes of these. 1.12.0:
# the scope of a narrowed evaluation (`ValidationResult.evaluation`, omitted for a complete one), the quantity state
# `NOT_COMPUTED`, `untraced_power_W` null where the plane bounds did not run, the issue code `CAPABILITY_NOT_COMPUTED` and
# `MetricDescriptor.requires`.
# This schema also includes
# material physics (index reference, Sellmeier of 1-10 terms, power-series, Herzberger, temperature-Sellmeier and air models,
# measured tables, spectral bulk loss, Schott thermo-optic data and a material temperature) and BULK_LOSS_NOT_ESTABLISHED.
SCHEMA_VERSION = "1.12.0"

# The last release of DISTRIBUTION_NAME published on PyPI and the SCHEMA_VERSION it carries. Publishing a release sets
# both to that release's values. A published version's contracts never change in place, so tests/generate_fixtures.py
# --freeze --unreleased (re-recording the contract baseline within an unpublished SCHEMA_VERSION) refuses when __version__
# or SCHEMA_VERSION is not past them (tests/test_fixtures_cli_freeze.py).
LAST_PUBLISHED_VERSION = "0.14.0"
LAST_PUBLISHED_SCHEMA_VERSION = "1.12.0"
MANIFEST_VERSION = "1.0.0"
# The version of the NumericalConfig defaults. Like SCHEMA_VERSION it moves once per release: the first change of a default
# (or a new setting) after a published release bumps it, and later changes before the next release keep that unreleased
# version. A change moves every Engine input hash (the defaults are part of the Engine request), so runs made by an earlier
# build read as not current. 1.6.0 (0.13.0): the return-light analysis budget, the crystal and nonlinear tolerances.
# 1.7.0 (0.14.0): the caller settings -- the share of a line's power below which light is left out
# (negligible_power_fraction), the power-rating match windows (rating_*), the tolerances of published design points
# (design_point_*) and the scatter-origin and mesh-patch budgets (max_scatter_origins, max_mesh_patches) -- every default
# today's value; a Project sets them through numerical_settings and compute_budget.
NUMERICAL_CONFIG_VERSION = "1.7.0"

# Physics model versions recorded in every ValidationManifest.
MODEL_VERSIONS: dict[str, str] = {
    "ray_geometry": "1.2",
    "materials": "1.1",
    "fresnel": "1.0",
    "gaussian_q": "1.1",
    "polarization_jones": "1.2",
    "birefringence_uniaxial": "1.0",
    "continuous_spectrum": "1.0",
    "interference_two_beam": "1.0",
    "power_ledger": "1.2",
    "first_order_transfer": "1.0",
    "layout_2p5d": "1.0",
    "control_lm": "1.0",
    "surface_scatter": "1.0",
    "bulk_scatter": "1.0",
    "complex_transmission": "1.0",
}

PRNG_ID = "PCG64+BoxMuller-v1"
