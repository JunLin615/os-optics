"""JSON-in / JSON-out entry points of the local optical workflow, for hosts that are not Python programs.

These functions also work in a JSON host, including a local Pyodide runtime, as under CPython.
Each function takes and returns plain JSON-safe values (``dict`` / ``list`` /
``str`` / numbers / ``bool`` / ``None``; run files are ``bytes``), so a host never parses a Python exception or a
pydantic model. Everything is computed on this device by the functions documented in ``os_optics.local``: this
module adds no physics, no defaults and no second Project model. It only converts.

Every function returns an envelope:

* success: ``{"ok": True, ...}``;
* a Project / catalog the runtime refuses: ``{"ok": False, "error": {"code": "LOCAL_RUN_ERROR", "problems": [...]}}``
  where each problem has a stable ``LocalRunErrorCode`` ``code``, a ``message`` and an optional ``location``.

The methods are the functions registered in ``API_METHODS`` (``os_optics.local.api_registry``; listed with their
parameters by ``capabilities()["json_api_methods"]``); module-level helpers such as ``catalog_summary``,
``product_summary``, ``run_summary`` and ``composition_capability`` are not methods. A host that is not a Python program
calls one with ``call_method(method, params, file_map=None, data=None)``: ``params`` by name, a ``{file name: bytes}``
map and raw bytes bound to the parameters the method declares for them; it returns the JSON reply and its bytes apart.

Some refusals have their own codes: ``validate_assembly_package(package)`` (the local assembly check of
``os_optics.assembly``) refuses a package that does not verify with ``{"ok": False, "error": {"code":
"ASSEMBLY_PACKAGE_INVALID", "problems": [...]}}``; the local Detailed Assembly Drawing of ``os_optics.assembly_drawing``
(``verify_assembly_detail_package``, ``generate_assembly_drawing``, ``verify_assembly_drawing``) refuses with ``{"ok":
False, "error": {"code": <DrawingErrorCode>, "problems": [{"code": ..., "message": ...}]}}``; ``commercial_submission(run_files,
customer_constraints=None)`` (the exact body and ``Idempotency-Key`` of the commercial submission of a run the host holds,
``os_optics.local.submission``) refuses a file set that does not verify with ``{"ok": False, "error": {"code":
"RUN_DIRECTORY_INCONSISTENT", "problems": [...]}}``. ``update_project(document, ops, options=None)`` applies the
Project edit operations atomically and reports the check of the result (``os_optics.local.project_ops``),
``run_freshness(document, run, verify=False)`` says
whether a run is current for a Project, ``inspect_optical_solids(document)`` whether the optical bodies are possible
solids (``os_optics.optical_solids``), ``optical_section(document_or_run, instance_id, plane="XZ", svg=False)`` draws the
exact meridional section of an element and its axis (``os_optics.optical_view.section``) and
``generate_optical_layout(package, locale="en")`` the Optical Layout Drawing of an optics-only purchase
(``os_optics.optical_layout``).

``evaluate_project(document, outputs=None, exclude=None, targets=None, view_settings=None)`` evaluates selected quantities
of a Project (``os_optics.local.evaluate_project``): the capabilities and presets named in ``outputs`` (default
``["readings"]``) with what they require, nothing in ``exclude``; the reply states the scope (``evaluation``: requested,
executed, added with what required each, not computed with what each produces, the declared targets not selected) beside
``status``, ``outcome``, ``targets``, ``monitors``, ``issues``, ``model_scope_issues`` and ``identity``, and the scene
(``optical_scene``) when ``view`` was computed. It writes nothing and returns no run file.
``evaluation_capabilities(capability_id=None)`` lists the capabilities, analyses and presets, or describes one in full
(a preset: what it selects and what it leaves out).

Prepared evaluations keep an ``EvaluationSession`` in this runtime between calls (``os_optics.local.prepared``), so a
candidate loop reuses the compiled elements and the Engine's products: ``prepare_evaluation(document)`` -> ``{"ok": True,
"handle", "project_hash", "engine_input_hash", "catalog_version", "max_handles", "handles", "notices"}``;
``evaluate_prepared(handle, outputs=None, exclude=None, targets=None, view_settings=None, document=None)`` -> the reply of
``evaluate_project`` for that document (default: the handle's current Project) and selection, with ``handle``, ``reuse``
(``executed``, ``reused``) and ``notices``; ``cancel_evaluation(handle)`` stops that handle's evaluation in progress;
``release_evaluation(handle)`` forgets it; ``evaluation_handles(max_handles=None)`` lists the handles and sets their bound
(default 4). The least recently used idle handle beyond the bound is evicted (notice ``EVALUATION_HANDLE_EVICTED``); a
handle this runtime does not hold is refused with ``EVALUATION_HANDLE_UNKNOWN`` (``context.reason``: ``RELEASED``,
``EVICTED``, ``NOT_HELD``) -- prepare the Project again; ``EVALUATION_HANDLE_BUSY`` while the handle evaluates;
``EVALUATION_HANDLE_LIMIT`` when a prepare finds every handle at the bound evaluating. Handles live in this process's
memory: a restarted host process holds none.

``import_prescription(text, options=None)`` imports a lens prescription (``os_optics.prescription``; formats:
``capabilities()["prescription_formats"]``) as Project-native optical models: ``{"ok": True, "format", "model_id",
"optical_models", "model_materials", "model_coatings", "component", "report"}`` (with ``options["into"]``, a Project: also
``"project"`` and ``"project_hash"``); a prescription that cannot be imported gives ``{"ok": False, "error": {"code":
"PRESCRIPTION_IMPORT_INVALID", "problems": [{"code", "message", "location"}]}}`` (``os_optics.prescription.api``).

``linear_optical_network(document, options=None)``: the Project's optics as an ``opensci-linear-optical-network/v1``
document (``os_optics.network``; ``json_schema("linear-optical-network")``) -> ``{"ok": True, "network", "contract_hash"}``.
``document`` is a Project (or an Engine request); ``options`` ``{"wavelength_nm", "source_ids", "observation_ids",
"mode_overlap_tolerance"}``. Optics that are not a linear optical network give ``{"ok": False, "error": {"code":
"NETWORK_NOT_EXPORTABLE", "problems": [{"code", "message", "location"}]}}`` (never a partial network); a Project the
runtime cannot simulate gives the ``LOCAL_RUN_ERROR`` envelope.

The public optical catalog is versioned data, not part of the Engine: a JSON host that obtains a catalog
release itself can install its verified bytes with ``install_catalog(data,
expected_sha256)`` -> ``{"ok": True, "catalog_version", "schema_version", "sha256", "size_bytes", "product_count"}``
(refusals: ``CATALOG_INVALID``, ``ENGINE_UPGRADE_REQUIRED``). The installed release is used by every function below;
off the browser runtime, without an installed release, the local catalog store is used (``os_optics.catalog``).
``catalog_status()`` -> ``{"ok": True, "available", "active": {"catalog_version", "schema_version", "sha256",
"source": "installed" | "store"} | None, "installed": [...], "supported_schema_versions": [...]}`` (no network). Without
any catalog, catalog functions give the error envelope with ``CATALOG_NOT_AVAILABLE``; ``capabilities()`` still answers
(``catalog`` / ``catalog_version`` null). A Project whose components pin an installed release runs against it.

Catalog browsing scales to catalogs of thousands of products:

* ``capabilities()`` summarizes the active catalog (``catalog``: version, product / material / coating counts and the
  product counts per category, form and model kind) instead of listing every SKU;
* ``catalog_products(category=None, wavelength_nm=None, form=None, offset=0, limit=None, summary=False, readiness=None,
  mounting=None, focal_length_mm=None, focal_tolerance_mm=None, focal_min_mm=None, focal_max_mm=None, focal_sign=None)`` ->
  ``{"ok": True, "catalog_version", "mechanical_accessories", "total", "offset", "limit", "products": [...]}``:
  the products matching the filters (like ``PublicOpticalCatalogV2.select``: a category, a form, a wavelength at which a
  published simulation model is valid), in ``optical_sku`` order; ``total`` counts all matches, ``products`` holds the
  page ``[offset, offset + limit)`` (``limit=None``: to the end). The focal filters (``FocalLengthQuery``) compare the
  signed nominal focal length a product publishes, in millimetres: ``focal_length_mm`` with ``focal_tolerance_mm`` (default
  exact) or the inclusive ``focal_min_mm`` / ``focal_max_mm``, and ``focal_sign`` (``positive`` / ``negative``). A product
  without one never matches. A filtered reply also holds ``focal_filter``: the query, the bounds compared, ``basis`` (what
  the value is: ``wavelength_nm`` does not change it) and ``excluded_without_focal_length`` (how many otherwise matching
  products publish none). Each entry is the full public product document, or
  with ``summary=True`` a compact row: ``optical_sku``, ``category``, ``form``, ``display_name``,
  ``display_name_zh_cn`` (the Simplified Chinese name), ``commercial_readiness`` (``ORDERABLE``: OpenSci supplies it),
  for an ORDERABLE product ``opensci_mounting`` (``AVAILABLE``: OpenSci also supplies mounting hardware for it;
  ``NOT_AVAILABLE``: it is supplied without, you mount it), ``delivered_form`` ``HOUSED`` for a product delivered in its
  manufacturer's housing, whose outline differs from the design geometry (absent: the optic itself) -- as they apply to
  the release (one that does not state them mounts every ORDERABLE product and delivers the optic itself),
  ``design_roles`` (sorted), ``model_kind`` and ``fidelity`` (of the product's first published simulation model), and
  the key specification values present on the product -- ``outer_diameter_mm`` (a round element) or
  ``outer_dimensions_mm`` (``[x, y, z]``: a rectangular plate or a solid such as a cube or a prism), ``focal_length_mm``,
  ``design_wavelength_nm``, ``reflectance``, ``transmittance``, ``retardance_waves``, ``extinction_ratio``,
  ``parent_focal_length_mm`` / ``off_axis_angle_deg`` (an off-axis parabolic mirror), ``deviation_angle_deg`` (a prism),
  ``wedge_angle_deg``, ``thickness_mm``, ``clear_aperture_diameter_mm`` (or ``clear_aperture_dimensions_mm``, ``[x, y]``),
  ``center_wavelength_nm`` / ``bandwidth_fwhm_nm`` / ``edge_wavelength_nm`` / ``optical_density`` (a filter) (decimal
  strings: the facts that tell sibling products apart) and ``working_band_nm`` (``{"min_nm", "max_nm"}``). A bad filter or paging value gives the error envelope
  with code ``CATALOG_QUERY_INVALID`` (a refused focal query names its parameter in ``location`` and its reason in
  ``details.reason``: ``FocalQueryRefusal``);
* ``catalog_product(optical_sku)`` -> ``{"ok": True, "catalog_version", "product"}`` (the full document;
  ``OPTICAL_SKU_NOT_PUBLISHED`` otherwise); ``catalog_materials()`` / ``catalog_coatings()`` -> ``{"ok": True,
  "catalog_version", "materials" | "coatings": [...]}`` (the published material / coating models products refer to);
  ``catalog_find_materials(query)``: material discovery, or (with ``name``) one name resolved as
  prescription import does, with infrared selection properties (``os-optics catalog materials`` / ``catalog material``;
  ``os_optics.catalog_materials``);
* ``catalog_model(optical_sku, model_id=None, model_kind=None)`` -> ``{"ok": True, "catalog_version", "optical_sku",
  "optical_model", "model_materials", "model_coatings"}`` (materials / coatings ``null`` when none, like the Project
  fields): a product's simulation model as a declared model of the
  Project (``os_optics.local.declared.declared_copy``), ready to insert into ``optical_models`` /
  ``model_materials`` / ``model_coatings`` and to use from a ``custom_part`` component (``optical_model_id``); it
  traces exactly like the product.

Declared optical models (``ProjectDocumentV2.optical_models``) need no catalog: a Project of sources, instruments and
declared models runs without one. ``check_project`` lists them (``declared_models``); ``capabilities()`` names the
Project features this runtime supports (``project_features``).

``example_project_document(kind=...)`` returns one of the reference designs of ``os_optics.local.example``
(``EXAMPLE_KINDS``): ``{"ok": True, "kind", "project", "project_hash"}``; ``folded=True`` is ``kind="folded"``.

Scientific results of a run the host holds (``os_optics.results`` over the run's ``{file name: bytes}``; nothing is
simulated again):

* ``metric_descriptors(include_stage_metrics=False)`` -> ``{"ok": True, "descriptors": [...]}``: what every target metric
  means (unit, definition, sign convention, operands, frame, selectors, model basis, validity, version);
* ``run_results(files, query)`` -> ``{"ok": True, "query", "result"}``: ``query`` is ``{"kind": K, ...}`` with ``K`` one of
  ``describe``, ``targets`` (every target outcome with its assessment, value, unit, bounds, diagnostic status and issue
  codes, the run's outcome, the counts per status and per assessment, the model-scope issues and the run identity), ``monitors``, ``monitor`` (``monitor``), ``paths`` (``path``), ``power_budget``, ``rays`` (``monitor``,
  ``path``, ``crossing_index``, ``offset``, ``limit``: a page of the rows, ``total`` counts them all), ``wavefront``
  (``monitor``, ``path``, ``crossing_index``, ``reference``, ``center``, ``min_rays``), ``irradiance`` (``monitor``,
  ``wavelength_nm``, ``half_extent_mm``, ``samples``, ``source_id``, ``center_uv_mm``, ``roi``), ``sampled_light`` (``monitor``),
  ``superpositions`` (``monitor``), ``handoff`` (``monitor``, ``path``,
  ``crossing_index``, ``element``: the beams at the plane in its (u, v, n) frame with the TEM00 field-construction convention
  and an element's clear apertures there), ``exposures`` (``element``: the per-surface beam exposure and power-rating
  comparison), ``grating_orders`` (``element``: every diffraction order of a pass through a grating), ``nonlinear`` (``element``: every
  pass into a crystal that generates its second harmonic), ``imaging`` (``monitor``, ``request``: the
  ``imaging-request``; an analysis document with every field's point spread function, MTF, encircled energy and wavefront, the run's light traced again
  with a dense pupil grid), ``ghosts`` (``monitor``, ``request``: the ``ghost-request``; an analysis document with the ghost images of every lens or
  window face's reflections at the plane, up to a bounce order: bounce surfaces, direction, focus position, relative power and irradiance, and a
  bound on the ghosts not listed, proven where every ghost surface met a valid Gaussian beam), ``detection`` (``monitor``: a stated
  detector's photocurrent, noise and SNR and its lock-in's demodulated signal, noise and SNR, each with its status), ``sample_emission``
  (how each fluorescence or Raman sample's line powers follow from the excitation delivered to it), ``field`` (``monitor``, ``request``: the
  ``field-request``; an analysis document with the coherent field of a sampled-field or speckle source at the plane: irradiance maps, power,
  centroid and speckle contrast over realizations, by the angular spectrum or the paraxial transfer), ``cavity`` (``request``: a ``cavity_analysis``
  request; the cavity analysis of the run's Project, bound to the run), ``columns`` (``export_kind``) or
  ``export`` (``export_kind``, ``format``, and that kind's filters: ``result`` is ``{"file_name", "media_type", "data":
  bytes}``); ``"verify": true`` also checks the files' hashes (``describe``'s ``integrity``). A wavefront / irradiance the
  run cannot support is ``{"ok": True, "result": {"status": "UNAVAILABLE", "reason", "message"}}``; an unknown monitor,
  path, kind or filter gives ``{"ok": False, "error": {"code", "problems": [{"code", "message"}]}}``.

Native components (``os_optics.native_library``: parametric optics, independent of any catalog product, as Project optical models):

* ``native_components()`` -> ``{"ok": True, "classes": [...]}``: every class with its parameters (name, type, unit,
  constraints, default or default rule), produced model kind, physics, approximations and references;
* ``build_native_model(class_id, parameters=None, options=None)`` -> ``{"ok": True, "class_id", "optical_model",
  "member_models", "model_materials", "model_coatings", "component_template", "design"}``: the model a ``custom_part``
  component uses (``optical_model_id``), the member models of a compound unit, the materials / coatings to add to the
  Project (``null`` when there are none, like the Project fields), a ready component instance and the design report.
  ``options = {"into": <Project>, "instance_id"?, "position_mm"?, "quaternion_wxyz"?}`` also returns ``"project"``: that
  Project with the models, materials, coatings and the component added and checked (``native_library.add_to_project``).
  A request that cannot be built (or added) gives ``{"ok": False, "error": {"code": "NATIVE_COMPONENT_INVALID",
  "problems": [{"code", "message", "parameter"}]}}``.

Interop capability catalog (``os_optics.interop``: external scientific tools that work with OpenSci data):
``interop_catalog(entry_id=None)`` -> ``{"ok": True, "schema", "schema_version", "checked", "statuses", "directions",
"entries": [...]}`` (every entry: scenario, tools with official links and licenses as reported upstream, what OpenSci
provides and what the tool computes, entry points, data, user inputs, assumptions, limits, conventions, runtime, status and
the executed checks in ``verified``), or with ``entry_id`` ``{"ok": True, "entry": {...}}``; an unknown id gives ``{"ok":
False, "error": {"code": "INTEROP_ENTRY_UNKNOWN", "problems": [{"code", "message"}]}}``. ``capabilities()["interop_catalog"]``
names the schema and the entry ids.

A field and its detector, stage by stage, each quantity ``MODELED``, ``EXTERNALLY_CHARACTERIZED``, ``BOUNDED`` or ``UNAVAILABLE`` (``os_optics.results.quantity``;
``capabilities()["field_analyses"]``):

* ``pupil_field(request, mask_files=None, handoff=None)``: the scalar low-NA focal field of a pupil-plane field and its mask (``json_schema("pupil-field-request")``,
  ``os_optics.results.pupil_field``) -> ``{"ok": True, "status": "OK" | "PARTIAL" | "UNAVAILABLE", "power", "validity", "quantities", "grid",
  "coherent_W_per_mm2" | "incoherent_W_per_mm2", "spots"?, "array"?, ...}``. ``mask_files``: ``{file name: bytes}`` of a ``FILE`` mask; ``handoff``: the ``handoff``
  result of ``run_results`` for a ``HANDOFF_BEAMS`` illumination. ``PARTIAL``: above the model's validity limit, with the pupil stage and its power
  ledger and without a focal map. A request that is not valid gives ``{"ok": False, "error": {"code": "PUPIL_FIELD_INVALID", "problems": [{"code", "message"}]}}``;
* ``focal_field(request, handoff=None)``: the vector focal field of an ideal aplanatic objective at any numerical aperture (``json_schema("focal-field-request")``,
  ``os_optics.results.focal_field``) -> ``{"ok": True, ...}`` with an analysis document (``json_schema("analysis-result")``, analysis ``FOCAL_FIELD``): the power
  ledger and energy balance, per spot the peak ``|E|^2``, widths and encircled-energy radii with their convergence, and the ``|E|^2`` / ``S_z`` maps. ``handoff``: the
  ``handoff`` result of ``run_results`` for a ``HANDOFF_BEAMS`` illumination. A request that is not valid, or beams no field can be built from, give
  ``{"ok": False, "error": {"code", "problems"}}`` (``FOCAL_FIELD_INVALID``, ``BEAM_NOT_TEM00``, ``PHASE_NOT_DEFINED``, ...);
* ``detector_response(irradiance, detector, composition=None)``: the detector response (``json_schema("detector-model")``, ``os_optics.results.detector``) of an
  irradiance document (the ``irradiance`` result of ``run_results`` or a ``pupil_field`` reply) -> ``{"ok": True, "status", "inputs", "pixels", "quantities",
  "unavailable_quantities", "totals"}``; a parameter the detector does not state leaves what needs it ``UNAVAILABLE``. An invalid detector gives ``DETECTOR_INVALID``,
  an irradiance document that lacks a member the response reads or holds one of the wrong form ``IRRADIANCE_INVALID`` (the member named).

``paraxial_analysis(document, request)``: paraxial analysis of placed lenses, mirrors and compound units of a Project
(``os_optics.local.paraxial``): ``request`` ``{"components": [ids in the order light meets them], "wavelength_nm",
"axis"?, "solve"?: "afocal" | "collimate", "source_id"?, "coaxial_tolerance"?: {"lateral_mm"?, "tilt_rad"?}}`` -> ``{"ok": True,
"wavelength_nm", "optical_axis", "axis_path", "elements", "system", "sections"?, "section_frame", "light_direction", "coaxial_tolerance",
"solution"?, "conventions"}`` (``coaxial_tolerance``: how far an element may lie off the axis and how far its axis may be tilted and still
be analysed on the nominal axis, 1e-6 mm and about 1.4e-6 rad unless stated; every element states its measured ``decentre_mm`` and
``tilt_rad``) (ABCD per element and of the system, EFL,
BFL, FFD, principal planes, focal points; mirrors fold the axis and every distance is on the unfolded axis, ``axis_path``
lists the straight stretches of the real path; a compound unit's entry has its ``members``; ``axis`` names one section of a system with cylinders, in one frame for all elements; the
afocal vertex gap of two elements or the front-focal object distance, with where to move the component and, with a source,
each source axis's output beam and whether it is ``COLLIMATED``, ``FOCUSED`` or ``UNCOLLIMATED``; ``light_direction`` states where the light's
direction at the first element comes from: ``SOURCE`` (``source_id``), ``PROJECT_SOURCE`` (the Project's source on the line through the first element, the
same verdict as naming it) or ``ASSUMED`` (no source on that line, or sources there that do not decide it: ``light_direction.notes`` lists
``{"code", "source_ids"}`` of the sources set aside)); a request it
cannot answer gives ``{"ok": False, "error": {"code": "PARAXIAL_ANALYSIS_INVALID", "problems": [{"code", "message",
"location"}]}}``, a Project the runtime cannot lower the ``LOCAL_RUN_ERROR`` envelope.

``cavity_analysis(document, request)``: a linear cavity of placed mirrors (``os_optics.local.cavity``): ``request``
``{"components": [end mirror, ..., end mirror], "wavelength_nm", "axis"?, "reflectances"?: {instance_id: R}, "transmittances"?: {end mirror: T},
"round_trip_loss"?, "input_beam"?: {"waist_radius_mm", "waist_radius_y_mm"?, "waist_after_reference_mm"}, "polarization"?: "ORDINARY" |
"EXTRAORDINARY" (the eigen-polarization of the cavity's crystals) | "S" | "P" (relative to the tilted mirrors' common plane of incidence), "injection"?: {"power_w" | "source_id", "beam": "EIGENMODE" | "INPUT_BEAM" | "SOURCE",
"detuning_Hz"? | "length_offset_nm"?, "mode_order_max"?, "scan"?: {"from_Hz", "to_Hz", "points"}, "source_linewidth_fraction_max"?}, "targets"?: [{"target_id", "quantity_id",
"target_value"?, "tolerance_abs"?, "tolerance_rel"?, "minimum"?, "maximum"?}], "aperture_over_mode_min"?, "coaxial_tolerance"?}`` -> ``{"ok": True, ...}`` with an analysis document
(``json_schema("analysis-result")``, analysis ``CAVITY``): the round-trip half trace and g1 g2 (g1, g2 of two bare mirrors), the
fundamental eigenmode's radius at every element and its waist in every gap, the round-trip Gouy phase, the free spectral range,
the transverse-mode spacing, each mirror's reflectance, the round-trip power fraction, finesse and linewidth, and with
``input_beam`` its mode-matching efficiency; an unstable or marginal cavity has its eigenmode ``OUT_OF_MODEL_SCOPE``, and the
finesse is a proven upper bound where losses besides the mirrors' are not modelled. With ``injection``, light injected through the first
mirror: the injected, circulating, transmitted, reflected and lost powers in steady state (Airy functions per Hermite-Gauss eigenmode of
the injected beam, the modes above the resolved order bounded), the on-resonance build-up, transmission and reflection, the detuning, the
power budget and a detuning scan (``arrays.injection``). The detuning is stated (``detuning_Hz``, or ``length_offset_nm`` from the TEM00
resonance): a Project's positions do not fix it, and without it the powers are ``BOUNDED`` over every detuning. A source (``source_id``,
beam ``SOURCE``) is traced to the first mirror: the power arriving there and its own beam (its mode matching) are injected; with anything
else on its way the cavity's own quantities stand and the quantities that need the beam say ``CAVITY_SOURCE_UNSUPPORTED`` (the powers
bounded by the traced arriving power where the trace gives it). ``targets`` and the Project's targets with a cavity metric
(``capabilities()["cavity_analysis"]["target_metrics"]``) are assessed MET / UNMET / INDETERMINATE in ``arrays.targets``; their thresholds
are in ``identity.requirements``, not in the physical identity. A request it cannot answer gives
``{"ok": False, "error": {"code": "CAVITY_ANALYSIS_INVALID", "problems": [{"code", "message", "location"}]}}`` (the paraxial
placement's codes, ``CAVITY_ELEMENT_UNSUPPORTED``, ``CAVITY_NOT_CLOSED``, ``CAVITY_SOURCE_UNSUPPORTED``, ``CAVITY_TOO_SHORT``).
Composition of reusable layouts (``os_optics.composition``). A workspace
(``OpticalWorkspaceV1``, ``json_schema("optical-workspace")``) is a Project with its module description, imported
instances, editing groups, operation journal and validations; a bare Project is accepted wherever a workspace is. A call
that changes the workspace returns ``{"ok": True, "workspace", "workspace_digest", "replayed", "result"}``; its request
takes ``op_id`` (reused on a retry: the same request returns the recorded result, another request is refused
``OP_ID_REUSED``; without one the call is always applied as a new operation, recorded under a generated id) and
``expected_base`` (a ``workspace_digest``; a stale one is refused ``BASE_CHANGED``). ``result.check`` reports the local
check of the operation's result: ``{"checked": true, "ok", "new_problems", "carried_problems"}`` (``checked`` false when
the Project did not change); the operation is applied whatever it says, and a request with ``require_check`` true is
refused ``CHECK_PROJECT_REFUSED`` when the result has a problem the workspace did not have. A refusal
changes nothing: ``{"ok": False, "error": {"code": "WORKSPACE_OPERATION_REFUSED", "problems": [{"code", "message",
"location", "details"}]}, "workspace_digest"}``.

* ``workspace_init(document, options=None)`` (``options.module``: a module descriptor) -> ``{"ok", "workspace",
  "workspace_digest", "module_digest"}``;
* ``workspace_import(workspace, module, request)``: ``{instance_key, placement, label, group_id, fixtures: {policy,
  overrides, rebind, unbound_targets}, catalog_binding}`` -> ``result``: ``id_map``, ``fixtures``, ``requirements``,
  ``interfaces`` (workspace frame), ``catalog``, ``warnings``;
* ``workspace_select(workspace, selector)`` (``{ids, groups, instances, kinds}``) -> ``{"ok", "selection"}``;
* ``workspace_transform(workspace, request)``: ``{selector, translation_mm, rotation: {quaternion_wxyz} | {axis,
  angle_deg}, frame: "design" | {group} | {component} | {instance}, pivot: "frame_origin" | "selection_centroid" |
  {point_mm} | {component}}`` -> ``result.affected`` (each moved object's pose before and after),
  ``result.placements_updated`` (the instances moved as a whole, with the instances nested in them);
* ``workspace_group_create`` / ``workspace_group_dissolve`` / ``workspace_group_set_members`` / ``workspace_group_add`` /
  ``workspace_group_remove(workspace, request)`` (``{group_id, label, members: selector, frame}``);
* ``workspace_replace_instance(workspace, module, request)`` (``{instance_key, discard_edits, fixtures,
  catalog_binding}``; refused ``INSTANCE_EDITED`` when the instance or an instance nested in it was edited -- members
  changed, deleted or added -- unless ``discard_edits``; writes the new revision's content, except that an unedited
  component or observation plane whose module object the revision has unchanged keeps its exact content ->
  ``result.replaced``: ``edits_discarded``, ``kept_definitions``: the definitions objects outside the instance use, kept with
  their content, each with ``used_by`` [{kind, id}]; refused ``ID_CONFLICT`` with ``used_by`` when the new revision
  defines such a definition's id differently),
  ``workspace_update_project(workspace, {project})`` (-> ``result``: ``removed_group_members``,
  ``removed_instance_members``: deleted instance members, which their instances keep as edits,
  ``restored_instance_members``: deleted members added back), ``workspace_batch(workspace, {operations: [{kind,
  request, module}]})``;
* ``workspace_export_module(workspace, descriptor)`` -> ``{"ok", "module", "module_digest"}``;
* ``workspace_run(workspace, options=None)`` (``{view_settings, include_files}``) -> ``{"ok", "workspace",
  "workspace_digest", "status", "validation", "summary", "local_validation", "files"?}``;
  ``workspace_record_validation(workspace, {local_validation, design_projection_hash})`` records a run made elsewhere;
* ``workspace_status(workspace)`` -> ``{"ok", "status"}`` (validations with ``CURRENT`` / ``HISTORICAL`` freshness and
  reasons, instances with edited and deleted members and the definitions a replacement keeps, groups, the module);
  ``workspace_history(workspace, {op_id, limit})``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from .._vendor.shared_contracts.design_catalog import CatalogQueryError, FocalLengthQuery, OpticalCategory, OpticalForm, PublicOpticalCatalogV2, PublicOpticalProductV2
from .._vendor.shared_contracts.optical_design import (
    DetectorModelV1,
    DofAxisV1,
    OpticalWorkspaceV1,
    DofControlIntentV1,
    LocalValidationRecordV1,
    OpticalRoleV1,
    ProjectDocumentV2,
    PupilFieldRequestV1,
)
from .._vendor.shared_contracts.optical_view import OpticalSimulationSceneV1, OpticalViewSettingsV1, PupilRaySelectionV1
from .._vendor.shared_contracts.optical_design.compute_budget import BUDGET_RANGES as _BUDGET_RANGES
from .._vendor.shared_contracts.optical_design.numerical_settings import SETTING_NAMES as _SETTING_NAMES
from ..interop import INTEROP_SCHEMA, INTEROP_SCHEMA_VERSION, InteropEntryUnknown
from ..interop import entry_ids as interop_entry_ids
from ..interop import interop_catalog as _interop_catalog
from ..interop import interop_entry as _interop_entry
from ..composition.json_wrappers import (  # noqa: F401  (the composition calls of this API)
    WORKSPACE_FUNCTIONS,
    workspace_batch,
    workspace_export_module,
    workspace_group_add,
    workspace_group_create,
    workspace_group_dissolve,
    workspace_group_remove,
    workspace_group_set_members,
    workspace_history,
    workspace_import,
    workspace_init,
    workspace_record_validation,
    workspace_replace_instance,
    workspace_run,
    workspace_select,
    workspace_status,
    workspace_transform,
    workspace_update_project,
)
from ..native_library import NativeComponentError
from ..native_library import build_native_model as _build_native_model
from ..native_library import native_class_ids
from ..native_library import native_components as _native_components
from ..native_library import native_field_models
from .._version import ENGINE_VERSION
from ..contracts.analysis import AnalysisResultV1
from ..contracts.network import NETWORK_SCHEMA, LinearOpticalNetworkV1
from ..contracts.results import ValidationResult
from ..manifest import engine_build_id, quantize
from ..prescription import PRESCRIPTION_FORMATS
from ..prescription.api import import_prescription_json
from ..results import EXPORT_KINDS, FORMATS, NPZ_KINDS, ResultsError, results_from_files
from ..results.detector import DetectorError
from ..results.detector import detector_response as _detector_response
from ..results.basis import dump_with_bases, row_bases, scoped_observation
from ..results.model import RunProjectRefused, sampled_summary
from ..contracts.analysis import AnalysisResultV1
from ..contracts.focal_field import FocalFieldRequestV1
from ..contracts.imaging import ImagingRequestV1
from ..contracts.ghosts import MAX_BOUNCE_ORDER, GhostRequestV1
from ..contracts.source_field import FieldRequestV1
from ..results.focal_field import FOCAL_FIELD_ANALYSIS, FocalFieldError
from ..results.focal_field import focal_field as _focal_field
from ..results.pupil_field import SCALAR_SINE_LIMIT, PupilFieldError
from ..results.pupil_field import pupil_field as _pupil_field
from ..results.quantity import BOUNDED, EXTERNALLY_CHARACTERIZED, MODELED, UNAVAILABLE
from ..validation.descriptors import metric_descriptors as _metric_descriptors
from ..evaluation.capabilities import ENGINE_REQUEST_ONLY
from ..evaluation.plan import (
    EXCLUDED_CAPABILITY_REQUIRED,
    TARGET_SELECTION_INCOMPLETE,
    UNKNOWN_CAPABILITY,
    UNKNOWN_TARGET,
    EvaluationPlanError,
    plan_evaluation,
)
from ..validation.ray_paths import RAY_DATA_LEVELS
from ..validation.scope import CAVITY_METRIC_UNITS, CAVITY_METRICS
from .cavity_build_up import MAX_MODE_ORDER, MAX_SCAN_POINTS
from .artifacts import (
    MANIFEST_FILE,
    RUN_FILES,
    committed_run,
    run_artifact_files,
    write_run_files,
    written_run_problems,
)
from .catalog import StoredCatalog, _validation_message, catalog_resolution_status, load_project, load_public_catalog
from .catalog import install_catalog as _install_catalog
from .advisories import WARNING_CODES, configuration_warnings, project_warnings
from .api_registry import ApiRegistry, _binary, call_registered
from .catalog_update import update_project_catalog as _update_project_catalog
from .errors import LocalRunError, LocalRunErrorCode, LocalRunProblem
from .example import EXAMPLE_KINDS, example_project_of_kind
from .freshness import FRESHNESS_REASONS, RunNotReadable
from .freshness import run_freshness as _run_freshness
from .project_ops import OP_ERROR_CODES, PROJECT_OPS, ProjectUpdateError
from .project_ops import update_project as _update_project
from .declared import declared_copy, declared_model_summary
from .lowering import COHERENCE_METRICS, lower_project, target_metrics
from .evaluate import EvaluationSession, capability_descriptor, capability_index, preset_descriptor
from .prepared import DEFAULT_MAX_HANDLES, MAX_HANDLES_LIMIT, PREPARED_EVALUATIONS
from .prepared import ERROR_CODES as PREPARED_ERROR_CODES
from .prepared import NOTICE_CODES as PREPARED_NOTICE_CODES
from .record import detect_runtime_kind, scientific_projection, target_row_bases
from .run import LocalOpticalRun, outcome_lead, requirements_coverage, run_optical_project, stop_run
from ..run_control import RunStopped, active_monitor, begin_attempt, end_attempt, monitored, phase
from ..run_monitor import RunMonitor
from .configurations import (
    ConfiguredProjectRun,
    comparison_from_files,
    configuration_files,
    configured_project,
    configurations_input_hash,
    configurations_run_files,
    lower_configurations,
    run_configured_project,
    run_reader,
    write_configurations_files,
)
from .submission import (
    RUN_DIRECTORY_INCONSISTENT,
    SUBMISSION_FILE,
    RunDirectoryInconsistentError,
    canonical_json_bytes,
    idempotency_key_for,
    submission_document,
)

JSON_API = "opensci.local_json_api"
JSON_API_VERSION = "1.8"
#: Every method of this API (``os_optics.local.api_registry``): the functions below register themselves with
#: ``@api_method``, the composition functions are added after them. A host calls them through ``call_method``.
API_METHODS = ApiRegistry()
api_method = API_METHODS.method
for _name, _function in WORKSPACE_FUNCTIONS.items():
    if _function.__name__ != _name:  # pragma: no cover - the composition table names each function by its own name
        raise RuntimeError(f"WORKSPACE_FUNCTIONS names {_function.__name__} {_name}")
    API_METHODS.add(_function, "workspace")
LOCAL_RUN_ERROR = "LOCAL_RUN_ERROR"
ASSEMBLY_PACKAGE_INVALID = "ASSEMBLY_PACKAGE_INVALID"
ASSEMBLY_SCHEMA_KIND = "assembly-validation"
DRAWING_SCHEMA_KINDS = ("assembly-detail", "assembly-drawing-result", "assembly-drawing-manifest")
DRAWING_OPTIONS = frozenset({"diagnostic", "expected_preview_content_hash", "expected_detail_content_hash", "locale", "design_bom"})
#: The languages a drawing (Detailed Assembly Drawing, Optical Layout Drawing) can be written in.
DRAWING_LOCALES = ("en", "zh-CN")
#: Project features beyond catalog products that this runtime simulates (``capabilities()["project_features"]``).
PROJECT_FEATURES = ("design_intent", "compute_budget", "numerical_settings", "optical_models", "compound_units", "aperture_stops", "ideal_thin_lens", "tabulated_thin_lens",
                    "faraday_rotators", "diffraction_gratings", "axicon_surfaces", "acylinder_surfaces", "freeform_surfaces", "neutral_density_attenuators", "neutral_density_coated_annulus", "attenuator_positions",
                    "power_ratings", "pulsed_sources", "geometric_pair_metrics", "polarization_resolved_coatings", "angle_resolved_coatings",
                    "coating_phases", "polarization_transmittance_bands", "elliptical_sources", "source_total_pulses", "extended_emitters",
                    "rating_value_definition", "rating_test_protocol", "partially_polarized_sources", "retained_dof_limits", "aperture_shapes", "surface_holes", "lens_arrays",
                    "configurations", "uniaxial_crystals", "continuous_spectra", "facet_solids", "sampled_intensity_sources",
                    "sampled_field_sources", "speckle_sources", "nonlinear_crystals", "sample_emission", "detection",
                    "compound_joints", "gradient_index_media", "axial_gradient_media")
#: The ``json_schema`` kind of the linear optical network export.
NETWORK_SCHEMA_KIND = "linear-optical-network"
SCHEMA_MODELS = {
    "project": ProjectDocumentV2,
    "local-validation": LocalValidationRecordV1,
    "catalog": PublicOpticalCatalogV2,
    "optical-scene": OpticalSimulationSceneV1,
    "view-settings": OpticalViewSettingsV1,
    "optical-workspace": OpticalWorkspaceV1,
    "detector-model": DetectorModelV1,
    "pupil-field-request": PupilFieldRequestV1,
    "focal-field-request": FocalFieldRequestV1,
    "imaging-request": ImagingRequestV1,
    "ghost-request": GhostRequestV1,
    "field-request": FieldRequestV1,
    "analysis-result": AnalysisResultV1,
}


def call_method(method: str, params: Any = None, *, file_map: Any = None, data: Any = None) -> tuple[dict[str, Any], dict[str, bytes] | None]:
    """One call of a method of this API by a host that is not a Python program:
    ``(reply, files)``, the JSON-safe reply and the bytes it carries (``None`` when none). ``params``: the method's
    parameters by name; ``file_map`` / ``data``: the ``{file name: bytes}`` map / raw bytes for the parameter the method
    declares for them (``API_METHODS[method].file_map`` / ``.data``). An unknown method gives the error code
    ``METHOD_NOT_IN_RUNTIME``, parameters that do not fit it ``INPUT_INVALID`` (``os_optics.local.api_registry``)."""

    return call_registered(API_METHODS, method, params, file_map=file_map, data=data)


def _error(problems: tuple[LocalRunProblem, ...] | list[LocalRunProblem]) -> dict[str, Any]:
    return {"ok": False, "error": {"code": LOCAL_RUN_ERROR, "problems": [p.as_dict() for p in problems]}}


def _write_refused(write: Callable[[], Any]) -> dict[str, Any] | None:
    """Write a run directory (``write``); its refusals -- a file the Engine did not write, a link -- as the ``LOCAL_RUN_ERROR``
    reply (nothing was written: the refusal comes before), else ``None``."""
    from .artifacts import RunDirectoryLinkError, RunDirectoryOccupiedError

    try:
        write()
    except RunDirectoryOccupiedError as exc:
        return _error([LocalRunProblem(LocalRunErrorCode.RUN_DIRECTORY_OCCUPIED, str(exc))])
    except RunDirectoryLinkError as exc:
        return _error([LocalRunProblem(LocalRunErrorCode.RUN_DIRECTORY_LINK, str(exc))])
    return None


def _view_settings(settings: Any) -> OpticalViewSettingsV1 | None:
    if settings is None or isinstance(settings, OpticalViewSettingsV1):
        return settings
    try:
        return OpticalViewSettingsV1.model_validate(settings)
    except ValidationError as exc:
        raise LocalRunError([LocalRunProblem(LocalRunErrorCode.PROJECT_INVALID, f"invalid view settings: {exc.errors(include_url=False)}")]) from None


SUMMARY_SPECIFICATIONS = ("outer_diameter_mm", "outer_dimensions_mm", "focal_length_mm", "design_wavelength_nm", "reflectance", "transmittance",
                          "retardance_waves", "extinction_ratio", "parent_focal_length_mm", "off_axis_angle_deg", "deviation_angle_deg",
                          "wedge_angle_deg", "thickness_mm", "clear_aperture_diameter_mm", "clear_aperture_dimensions_mm",
                          "center_wavelength_nm", "bandwidth_fwhm_nm", "edge_wavelength_nm", "optical_density", "cone_angle_deg")


def _counts(values) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def catalog_summary(catalog: PublicOpticalCatalogV2) -> dict[str, Any]:
    """Counts describing a catalog release (no per-SKU list)."""

    if isinstance(catalog, StoredCatalog):  # (a store release: the same counts from its verified document)
        return catalog.summary()
    return {
        "catalog_version": catalog.catalog_version,
        "product_count": len(catalog.products),
        "material_count": len(catalog.materials),
        "coating_count": len(catalog.coatings),
        "categories": _counts(p.category.value for p in catalog.products),
        "forms": _counts(p.form.value for p in catalog.products),
        "model_kinds": _counts(m.model_kind for p in catalog.products for m in p.simulation_models),
    }


@api_method("runtime", data="data", runtime_state=True)
def install_catalog(data: Any, expected_sha256: str | None = None) -> dict[str, Any]:
    """Install a public optical catalog release from its exact bytes (module docstring); it becomes the active catalog."""

    try:
        installed = _install_catalog(data, expected_sha256=expected_sha256)
    except LocalRunError as exc:
        return _error(exc.problems)
    return {"ok": True, **installed.summary()}


@api_method("runtime")
def catalog_status() -> dict[str, Any]:
    """Which catalog release the functions of this module use (module docstring); never uses the network."""

    return {"ok": True, **catalog_resolution_status()}


@api_method("runtime")
def capabilities() -> dict[str, Any]:
    """What this runtime is and what it can do: identities, its methods (``json_api_methods``: per method its category,
    parameters, required parameters, the parameter taking a ``{file name: bytes}`` map (``file_map``) or raw bytes
    (``data``) and whether it changes the runtime's state; ``os_optics.local.api_registry``), the active catalog release (``catalog``: counts per category,
    form and model kind; never the SKU list -- page through ``catalog_products``; null before a catalog is available),
    roles, DOF axes and control intents, target metrics (name -> unit; ``metric_descriptors()`` says what each means) and
    those of them that need the source's declared coherence (``coherence_metrics``), view settings, example kinds, the
    schema kinds ``json_schema`` accepts, the result queries / export kinds of ``run_results`` (``results``) and the Project
    features beyond catalog products it simulates (``project_features``: declared ``optical_models`` with their own
    materials and coatings, ``compound_units``, ``aperture_stops``, ``ideal_thin_lens``, ``tabulated_thin_lens`` (a thin lens
    whose focal length and transmittance are tabulated per wavelength, with its validity domain and validated metrics),
    ``faraday_rotators`` (``FARADAY_ROTATOR`` models: a nonreciprocal polarization rotator stated by its rotation and
    transmittance per wavelength),
    ``diffraction_gratings`` (``DIFFRACTION_GRATING`` models: order directions by the vector grating equation, order powers from a
    stated response with its provenance, known separately),
    ``axicon_surfaces`` (the cone term ``linear_sag_coefficient`` of a surface profile), ``acylinder_surfaces`` (a conic or
    polynomial profile about a ``cylinder_axis``: a profile curved in one section only), ``freeform_surfaces`` (a surface's
    two-variable ``sag``: Zernike, XY polynomial, biconic or toroid), ``neutral_density_attenuators``
    (``NEUTRAL_DENSITY`` models: fixed, rotary, stepped and gradient density laws), ``neutral_density_coated_annulus``
    (``coated_inner_radius_mm``: a wheel coated around an uncoated hub), ``attenuator_positions`` (``selected_parameters``
    ``nd_rotation_deg`` / ``nd_offset_mm`` turning a wheel or shifting a gradient), ``power_ratings`` (catalog and declared
    ratings compared with each element's exposure; ``run_results`` query ``exposures``), ``pulsed_sources`` (a source's
    ``pulse`` train), ``source_total_pulses`` (``pulse.total_pulses``: the pulses a source sends while the optics stay in
    place, compared with a rating's pulse count), ``rating_value_definition`` (a rating's ``value_definition``: a peak value
    or the total over the test beam's 1/e^2 area), ``rating_test_protocol`` (a rating's ``test_protocol``: how its pulse
    count was applied), ``geometric_pair_metrics`` (targets comparing two beams, ``wavelength_nm_b`` for the second),
    ``polarization_resolved_coatings`` (s / p spectra), ``angle_resolved_coatings`` (tables over wavelength and angle of
    incidence), ``coating_phases`` (stated reflection / transmission phases), ``polarization_transmittance_bands``
    (band-resolved polarizers and retarders), ``elliptical_sources``, ``partially_polarized_sources`` (source polarization
    ``PARTIAL`` or ``UNPOLARIZED``: two incoherent polarization components, their Stokes sum and degree of polarization per
    route in ``polarization_mixtures``), ``uniaxial_crystals`` (a declared material with ``birefringence`` and the optic axis
    of each element made of it, ``optic_axes``: ordinary and extraordinary beams, walk-off), ``continuous_spectra`` (a
    Gaussian-beam source's ``spectral_density``: quadrature lines and the coherence derived from the spectrum, reported in
    ``source_spectra``), ``design_intent``, ``retained_dof_limits`` (a retained DOF's stated travel, hard
    limits, repeatability and drift, assessed by the ``dof_*`` target metrics)), the paraxial
    analysis call (``paraxial_analysis``), the interop capability catalog (``interop_catalog``: schema and entry ids) and the
    composition operations on workspaces (``composition``: the workspace contract, the ``workspace_*`` functions, the
    selector / frame / pivot vocabularies, fixture policies and stable codes), the Project edit operations of
    ``update_project`` (``project_ops``), the advisory warning codes of ``check_project`` (``check_warnings``), the model
    scope boundary of the measurement chain and nonlinear conversion (``model_scope_boundary``), the run freshness call
    (``run_freshness``) and the capabilities a selective evaluation is made of (``evaluation``: the capability and
    analysis ids, the presets with their members and the calls of ``evaluate_project`` and ``evaluation_capabilities``,
    which describes each; a results query of a part an evaluation did not compute is refused with ``NOT_COMPUTED``)."""

    from .._vendor.shared_contracts.optical_design.compute_budget import BUDGET_RANGES
    from .._vendor.shared_contracts.optical_design.numerical_settings import SETTING_NAMES
    from ..config import DEFAULT_NUMERICAL_CONFIG
    from ..settings_registry import SETTINGS

    try:
        catalog = load_public_catalog()
    except LocalRunError:
        catalog = None
    return {
        "ok": True,
        "json_api": JSON_API,
        "json_api_version": JSON_API_VERSION,
        "engine_version": ENGINE_VERSION,
        "engine_build_id": engine_build_id(),
        "runtime_kind": detect_runtime_kind().value,
        "json_api_methods": {name: API_METHODS[name].describe() for name in sorted(API_METHODS)},
        "project_schema": {"schema": "opensci.project", "project_schema_version": "2.0"},
        "project_features": list(PROJECT_FEATURES),
        "compute_budget": {
            "defaults": {name: getattr(DEFAULT_NUMERICAL_CONFIG, name) for name in BUDGET_RANGES},
            "fields": {name: {"minimum": limits[0], "maximum": limits[1],
                               "default": getattr(DEFAULT_NUMERICAL_CONFIG, name)}
                       for name, limits in BUDGET_RANGES.items()},
            "operation": "set_compute_budget",
            "persistence": "Project.compute_budget",
        },
        "numerical_settings": {
            # (unit omitted for a dimensionless setting; path given where it is not numerical_settings.<name>)
            "fields": {name: {"default": getattr(DEFAULT_NUMERICAL_CONFIG, name),
                              **({"unit": SETTINGS[name].unit} if SETTINGS[name].unit != "1" else {}),
                              "direction": SETTINGS[name].direction.value,
                              **({"path": SETTINGS[name].path} if name.startswith("design_point_") else {})}
                       for name in SETTING_NAMES},
            "operation": "set_numerical_settings",
            "persistence": "Project.numerical_settings",
        },
        "catalog_version": catalog.catalog_version if catalog is not None else None,
        "catalog": catalog_summary(catalog) if catalog is not None else None,
        "example_kinds": list(EXAMPLE_KINDS),
        "optical_roles": [role.value for role in OpticalRoleV1],
        "dof_axes": [axis.value for axis in DofAxisV1],
        "dof_control_intents": [intent.value for intent in DofControlIntentV1],
        "target_metrics": target_metrics(),
        "coherence_metrics": sorted(COHERENCE_METRICS),
        "pupil_ray_selections": [selection.value for selection in PupilRaySelectionV1],
        "schema_kinds": sorted([*SCHEMA_MODELS, ASSEMBLY_SCHEMA_KIND, *DRAWING_SCHEMA_KINDS, NETWORK_SCHEMA_KIND, *_plan_schema_kinds()]),
        "drawing_locales": list(DRAWING_LOCALES),
        "prescription_formats": list(PRESCRIPTION_FORMATS),
        "native_component_classes": native_class_ids(),
        "native_field_models": [model["model_id"] for model in native_field_models()],
        "field_analyses": {"pupil_field": {"call": API_METHODS["pupil_field"].signature(), "schema_kind": "pupil-field-request", "scalar_sine_limit": SCALAR_SINE_LIMIT},
                           "detector_response": {"call": API_METHODS["detector_response"].signature(), "schema_kind": "detector-model"},
                           "focal_field": {"call": API_METHODS["focal_field"].signature(), "schema_kind": "focal-field-request", "analysis": FOCAL_FIELD_ANALYSIS,
                                           "result_schema_kind": "analysis-result", "objective_models": ["IDEAL_APLANATIC"],
                                           "illuminations": ["UNIFORM", "ANNULAR", "GAUSSIAN", "BEAMS", "HANDOFF_BEAMS"],
                                           "polarizations": ["LINEAR", "CIRCULAR", "JONES", "UNPOLARIZED"]},
                           "imaging": {"call": "run_results(files, {'kind': 'imaging', 'monitor', 'request'})", "schema_kind": "imaging-request",
                                       "analysis": "IMAGING", "result_schema_kind": "analysis-result", "scalar_sine_limit": SCALAR_SINE_LIMIT},
                           "ghost_paths": {"call": "run_results(files, {'kind': 'ghosts', 'monitor', 'request'})", "schema_kind": "ghost-request",
                                           "analysis": "GHOST_PATHS", "result_schema_kind": "analysis-result", "max_bounce_order": MAX_BOUNCE_ORDER,
                                           "reflections": ["FRESNEL", "COATING"]},
                           "field": {"call": "run_results(files, {'kind': 'field', 'monitor', 'request'})", "schema_kind": "field-request",
                                     "analysis": "FIELD_PROPAGATION", "result_schema_kind": "analysis-result",
                                     "sources": ["SAMPLED_FIELD", "SPECKLE"], "methods": ["ANGULAR_SPECTRUM", "PARAXIAL_TRANSFER"],
                                     "scalar_sine_limit": SCALAR_SINE_LIMIT},
                           "quantity_statuses": [MODELED, EXTERNALLY_CHARACTERIZED, BOUNDED, UNAVAILABLE]},
        "linear_optical_network": NETWORK_SCHEMA,
        "paraxial_analysis": {"call": API_METHODS["paraxial_analysis"].signature(), "solves": ["afocal", "collimate"]},
        "cavity_analysis": {"call": API_METHODS["cavity_analysis"].signature(), "analysis": "CAVITY", "result_schema_kind": "analysis-result",
                            "cavities": ["LINEAR"], "transverse_modes": ["TEM00", "HG_EVEN_ORDERS_OF_A_COAXIAL_BEAM"],
                            "run_query": "run_results(files, {'kind': 'cavity', 'request'})",
                            "injection": {"beams": ["EIGENMODE", "INPUT_BEAM", "SOURCE"], "max_mode_order": MAX_MODE_ORDER, "max_scan_points": MAX_SCAN_POINTS,
                                          "quantities": ["injected_power_W", "circulating_power_W", "transmitted_power_W", "reflected_power_W",
                                                         "lost_power_W", "power_build_up", "transmission", "reflection", "resonant_power_build_up",
                                                         "resonant_transmission", "resonant_reflection", "impedance_matching_reflectance",
                                                         "input_loss_matching_reflectance", "detuning_Hz", "higher_order_mode_power_fraction",
                                                         "unresolved_mode_power_fraction", "polarization_family_power_fraction"],
                                          "detuning": "stated (detuning_Hz, or length_offset_nm from the TEM00 resonance); without it the powers are "
                                                      "BOUNDED over every detuning (a Project's positions do not fix it)",
                                          "bounded": ["M^2 > 1 (a higher-order or multimode beam): powers BOUNDED as light not resolved, "
                                                      "SOURCE_NOT_FUNDAMENTAL_MODE",
                                                      "pulsed, or a linewidth above source_linewidth_fraction_max (0.01) of the cavity's: "
                                                      "BOUNDED over every detuning, SOURCE_NOT_SINGLE_FREQUENCY",
                                                      "a beam wider than a third of the input mirror's clear radius: BOUNDED as light not "
                                                      "resolved, INPUT_BEAM_CLIPPED",
                                                      "a source whose light meets anything on its way to the input mirror: BOUNDED by the "
                                                      "traced arriving power, CAVITY_SOURCE_UNSUPPORTED"],
                                          "out_of_model": ["a tilted or offset input beam; a plane-plane (marginal) cavity's eigenmode and "
                                                           "build-up"]},
                            "target_metrics": dict(CAVITY_METRICS), "target_metric_units": dict(CAVITY_METRIC_UNITS)},
        "interop_catalog": {"schema": INTEROP_SCHEMA, "schema_version": INTEROP_SCHEMA_VERSION, "entries": interop_entry_ids(),
                            "call": API_METHODS["interop_catalog"].signature()},
        "composition": composition_capability(),
        "project_ops": {"call": API_METHODS["update_project"].signature(), "ops": list(PROJECT_OPS), "options": list(UPDATE_OPTIONS),
                        "op_codes": list(OP_ERROR_CODES), "error_codes": ["PROJECT_OP_INVALID", "PROJECT_REJECTED", "PROJECT_CHANGED"]},
        "check_warnings": list(WARNING_CODES),
        "model_scope_boundary": _model_scope_boundary(),
        "run_freshness": {"call": API_METHODS["run_freshness"].signature(), "states": ["CURRENT", "HISTORICAL", "UNDETERMINED"],
                          "reasons": list(FRESHNESS_REASONS),
                          "engine_build": "ENGINE_CHANGED appears only with another reason; a run of the same Project and Engine "
                                          "input made by another Engine build is CURRENT with engine_build_matches false"},
        "evaluation": _evaluation_capability(),
        "results": {"queries": list(RESULT_QUERIES), "export_kinds": list(EXPORT_KINDS),
                    "export_formats": {kind: list(FORMATS if kind in NPZ_KINDS else FORMATS[:-1]) for kind in EXPORT_KINDS},
                    "run_files": [name for _role, name, _media, _what in RUN_FILES]},
    }


def _evaluation_capability() -> dict[str, Any]:
    """What ``capabilities()["evaluation"]`` says, in brief: the capability ids a Project evaluation selects from, in
    execution order, the presets with their members, the analyses, the exports (the run directory: ``write_run``), the
    refusal codes of a selection and the two calls; ``evaluation_capabilities(id)`` describes each in full."""

    index = capability_index()
    return {"capabilities": [row["id"] for row in index["capabilities"]], "analyses": [row["id"] for row in index["analyses"]],
            "exports": [row["id"] for row in index["exports"]],
            "presets": {name: row["members"] for name, row in index["presets"].items()},
            "error_codes": [UNKNOWN_CAPABILITY, UNKNOWN_TARGET, EXCLUDED_CAPABILITY_REQUIRED, TARGET_SELECTION_INCOMPLETE],
            "evaluate_project": API_METHODS["evaluate_project"].signature(),
            "evaluation_capabilities": API_METHODS["evaluation_capabilities"].signature(),
            "prepared": {"calls": [API_METHODS[name].signature() for name in PREPARED_METHODS],
                         "default_max_handles": DEFAULT_MAX_HANDLES, "max_handles_limit": MAX_HANDLES_LIMIT,
                         "error_codes": list(PREPARED_ERROR_CODES), "notice_codes": list(PREPARED_NOTICE_CODES)}}


def _model_scope_boundary() -> dict[str, Any]:
    """What ``capabilities()["model_scope_boundary"]`` says: each item of the weak-signal measurement chain and of nonlinear
    conversion with its disposition (``items``: IN_SCOPE_SUPPORTED, OUT_OF_SCOPE_EXTERNAL or NOT_MODELLED) and the model
    kinds refused before a Project is read (``refused_source_kinds``, ``refused_optical_model_kinds``). A refusal states
    the item's missing model and route in its ``details``; an out-of-scope target metric states them in its message."""

    from ..validation.scope import MODEL_SCOPE_BOUNDARY, OUT_OF_SCOPE_OPTICAL_MODEL_KINDS, OUT_OF_SCOPE_SOURCE_KINDS

    return {"items": {entry["item"]: entry["disposition"] for entry in MODEL_SCOPE_BOUNDARY},
            "refused_source_kinds": sorted(OUT_OF_SCOPE_SOURCE_KINDS),
            "refused_optical_model_kinds": sorted(OUT_OF_SCOPE_OPTICAL_MODEL_KINDS)}


def composition_capability() -> dict[str, Any]:
    """What ``capabilities()["composition"]`` says: the workspace contract, the composition functions, the operations a
    batch may hold, the selector / frame / pivot vocabularies, the fixture policies and the stable codes."""

    from ..composition import BATCH_KINDS, CompositionErrorCode, CompositionWarningCode
    from ..composition.importing import CATALOG_BINDINGS, POLICIES, UNBOUND_TARGETS
    from ..composition.selection import SELECTOR_KEYS

    return {"workspace_schema": {"schema": "opensci.optical_workspace", "schema_version": "1.0"},
            "functions": API_METHODS.of_category("workspace"), "batch_kinds": list(BATCH_KINDS), "selector_keys": list(SELECTOR_KEYS),
            "frames": ["design", "group", "component", "instance"],
            "pivots": ["frame_origin", "selection_centroid", "point_mm", "component"],
            "fixture_policies": list(POLICIES), "unbound_targets": list(UNBOUND_TARGETS), "catalog_bindings": list(CATALOG_BINDINGS),
            "error_codes": [c.value for c in CompositionErrorCode], "warning_codes": [c.value for c in CompositionWarningCode],
            "rotation_arithmetic": "exact quarter turns; other angles by fixed polynomials (+ - * / sqrt only)"}


def _query_error(message: str) -> dict[str, Any]:
    return _error([LocalRunProblem(LocalRunErrorCode.CATALOG_QUERY_INVALID, message)])


def product_summary(product: PublicOpticalProductV2) -> dict[str, Any]:
    """The compact catalog row of ``catalog_products(summary=True)`` (module docstring)."""

    model = product.simulation_models[0]
    spec = product.specifications
    row: dict[str, Any] = {
        "optical_sku": product.optical_sku,
        "category": product.category.value,
        "form": product.form.value,
        "display_name": product.display_name,
        "display_name_zh_cn": product.display_name_zh_cn,
        "commercial_readiness": product.commercial_readiness.value,
        "design_roles": sorted(product.design_roles),
        "model_kind": model.model_kind,
        "fidelity": model.fidelity.value,
    }
    if product.commercial_readiness.value == "ORDERABLE":  # whether OpenSci also supplies mounting hardware for it
        row["opensci_mounting"] = product.mounting.value
    if product.delivery_form.value == "HOUSED":
        row["delivered_form"] = "HOUSED"
    for name in SUMMARY_SPECIFICATIONS:
        value = getattr(spec, name)
        if value is not None:
            row[name] = value.value if isinstance(value.value, str) else list(value.value)
    if spec.working_band_nm is not None:
        row["working_band_nm"] = {"min_nm": spec.working_band_nm.min_nm, "max_nm": spec.working_band_nm.max_nm}
    return row


def _paging(offset: Any, limit: Any) -> str | None:
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        return f"offset must be a non-negative integer, got {offset!r}"
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 0):
        return f"limit must be a non-negative integer or None, got {limit!r}"
    return None


@api_method("catalog")
def catalog_products(category: str | None = None, wavelength_nm: str | float | None = None, form: str | None = None, offset: int = 0,
                     limit: int | None = None, summary: bool = False, readiness: str | None = None,
                     mounting: str | None = None, focal_length_mm: str | float | None = None, focal_tolerance_mm: str | float | None = None,
                     focal_min_mm: str | float | None = None, focal_max_mm: str | float | None = None,
                     focal_sign: str | None = None) -> dict[str, Any]:
    """Public products of the active catalog, filtered like ``PublicOpticalCatalogV2.select`` (a ``category``, a ``form``,
    a ``wavelength_nm`` at which a published simulation model is valid, a ``readiness`` -- ``ORDERABLE`` /
    ``SIMULATION_ONLY`` --, an OpenSci ``mounting`` -- ``AVAILABLE`` / ``NOT_AVAILABLE`` -- and / or a signed nominal focal
    length: ``focal_length_mm`` with ``focal_tolerance_mm`` or ``focal_min_mm`` / ``focal_max_mm``, and ``focal_sign``) and
    paged (``offset``, ``limit``); full documents, or compact rows with ``summary=True`` (module docstring)."""

    try:
        catalog = load_public_catalog()
    except LocalRunError as exc:
        return _error(exc.problems)
    try:
        wanted = OpticalCategory(category) if category is not None else None
    except ValueError:
        return _query_error(f"unknown category {category!r}; known: {[c.value for c in OpticalCategory]}")
    try:
        wanted_form = OpticalForm(form) if form is not None else None
    except ValueError:
        return _query_error(f"unknown form {form!r}; known: {[f.value for f in OpticalForm]}")
    if readiness is not None and readiness not in ("ORDERABLE", "SIMULATION_ONLY"):
        return _query_error(f"unknown readiness {readiness!r}; known: ['ORDERABLE', 'SIMULATION_ONLY']")
    if mounting is not None and mounting not in ("AVAILABLE", "NOT_AVAILABLE"):
        return _query_error(f"unknown mounting {mounting!r}; known: ['AVAILABLE', 'NOT_AVAILABLE']")
    paging = _paging(offset, limit)
    if paging is not None:
        return _query_error(paging)
    try:
        focal = FocalLengthQuery.of(focal_length_mm=focal_length_mm, focal_tolerance_mm=focal_tolerance_mm, focal_min_mm=focal_min_mm,
                                    focal_max_mm=focal_max_mm, focal_sign=focal_sign)
        products, focal_filter = catalog.select_by_focal_length(focal, category=wanted, form=wanted_form, readiness=readiness, mounting=mounting,
                                                                wavelength_nm=str(wavelength_nm) if wavelength_nm is not None else None)
    except CatalogQueryError as exc:
        return _error([LocalRunProblem(LocalRunErrorCode.CATALOG_QUERY_INVALID, exc.message, location=exc.parameter, details={"reason": exc.reason.value})])
    except ValueError as exc:
        return _query_error(str(exc))
    page = products[offset:] if limit is None else products[offset:offset + limit]
    return {
        "ok": True,
        "catalog_version": catalog.catalog_version,
        "mechanical_accessories": catalog.mechanical_accessories.value,
        "total": len(products),
        "offset": offset,
        "limit": limit,
        **({"focal_filter": focal_filter} if focal_filter is not None else {}),
        "products": [product_summary(p) if summary else p.model_dump(mode="json", by_alias=True, exclude_none=True) for p in page],
    }


@api_method("catalog")
def catalog_product(optical_sku: str) -> dict[str, Any]:
    """One product's full public document: ``{"ok": True, "catalog_version", "product"}``."""

    try:
        catalog = load_public_catalog()
    except LocalRunError as exc:
        return _error(exc.problems)
    product = catalog.get(optical_sku) if isinstance(optical_sku, str) else None
    if product is None:
        return _error([LocalRunProblem(LocalRunErrorCode.OPTICAL_SKU_NOT_PUBLISHED, f"{optical_sku!r} is not published in public catalog {catalog.catalog_version}")])
    return {"ok": True, "catalog_version": catalog.catalog_version, "product": product.model_dump(mode="json", by_alias=True, exclude_none=True)}


@api_method("catalog")
def catalog_model(optical_sku: str, model_id: str | None = None, model_kind: str | None = None) -> dict[str, Any]:
    """A catalog product's simulation model as a declared model (module docstring): ``{"ok": True, "catalog_version",
    "optical_sku", "optical_model", "model_materials", "model_coatings"}`` (JSON documents)."""

    try:
        catalog = load_public_catalog()
    except LocalRunError as exc:
        return _error(exc.problems)
    product = catalog.get(optical_sku) if isinstance(optical_sku, str) else None
    if product is None:
        return _error([LocalRunProblem(LocalRunErrorCode.OPTICAL_SKU_NOT_PUBLISHED, f"{optical_sku!r} is not published in public catalog {catalog.catalog_version}")])
    try:
        model, materials, coatings = declared_copy(product, catalog, model_kind=model_kind, model_id=model_id)
    except LocalRunError as exc:
        return _error(exc.problems)
    except ValidationError as exc:  # a model_id the contract refuses
        return _error([LocalRunProblem(LocalRunErrorCode.PROJECT_INVALID, f"invalid model_id {model_id!r}: {exc.errors(include_url=False)[0]['msg']}")])
    return {"ok": True, "catalog_version": catalog.catalog_version, "optical_sku": product.optical_sku,
            "optical_model": model.model_dump(mode="json", exclude_none=True),
            "model_materials": [m.model_dump(mode="json", exclude_none=True) for m in materials] or None,
            "model_coatings": [c.model_dump(mode="json", exclude_none=True) for c in coatings] or None}


@api_method("catalog")
def catalog_materials() -> dict[str, Any]:
    """The published material models (dispersion, valid band) the catalog's products refer to."""

    try:
        catalog = load_public_catalog()
    except LocalRunError as exc:
        return _error(exc.problems)
    return {"ok": True, "catalog_version": catalog.catalog_version,
            "materials": [m.model_dump(mode="json", by_alias=True, exclude_none=True) for m in catalog.materials]}


#: The filters of ``catalog_find_materials`` (a list) and its options with ``name`` (one name resolved).
MATERIAL_QUERY_KEYS = ("manufacturer", "designation", "loose", "search", "covers_nm", "wavelength_nm", "with_bulk_loss", "availability",
                       "at_nm", "band_nm", "thickness_mm", "temperature_c", "offset", "limit")
MATERIAL_LOOKUP_KEYS = ("name", "manufacturer", "glass_catalogs", "at_nm", "band_nm", "thickness_mm", "temperature_c")


@api_method("catalog")
def catalog_find_materials(query: Any = None) -> dict[str, Any]:
    """Material discovery (``os_optics.catalog_materials``), the JSON form of ``os-optics catalog materials`` and ``catalog
    material NAME``.

    Without ``name``: the published materials matching every filter (``list_materials``) -- ``manufacturer``,
    ``designation``, ``loose``, ``search``, ``covers_nm`` ``[min, max]`` or ``wavelength_nm``, ``with_bulk_loss``,
    ``availability``, ``offset``, ``limit`` -> ``{"ok": True, catalog_version, schema_version, total, offset, limit, filters,
    materials}``. With ``name``: that name resolved as prescription import does (``lookup_material``; ``MAKER:DESIGNATION``
    and ``ID:<id>`` accepted; options ``manufacturer``, ``glass_catalogs``) -> ``{"ok": True, catalog_version, resolution,
    material}``, or for an unknown / ambiguous name ``resolution.resolved`` null with ``candidates`` and ``next_step``.
    Both take ``at_nm``, ``band_nm`` ``[lo, hi]``, ``thickness_mm``, ``temperature_c`` (the infrared selection properties,
    ``ir``). A bad query: ``CATALOG_QUERY_INVALID``."""

    from ..catalog_materials import MaterialQueryError, list_materials, lookup_material

    query = {} if query is None else query
    if not isinstance(query, dict):
        return _error([LocalRunProblem(LocalRunErrorCode.CATALOG_QUERY_INVALID, "the query is an object of filters")])
    keys = MATERIAL_LOOKUP_KEYS if "name" in query else MATERIAL_QUERY_KEYS
    unknown = sorted(set(query) - set(keys))
    if unknown:
        return _error([LocalRunProblem(LocalRunErrorCode.CATALOG_QUERY_INVALID, f"unknown key(s) {', '.join(unknown)}; "
                                       f"{'with name' if 'name' in query else 'without name'} the keys are {', '.join(keys)}")])
    try:
        catalog = load_public_catalog()
    except LocalRunError as exc:
        return _error(exc.problems)
    try:
        if "name" in query:
            options = dict(query)
            return {"ok": True, **lookup_material(catalog, options.pop("name"), **options)}
        return {"ok": True, **list_materials(catalog, **query)}
    except MaterialQueryError as exc:
        return _error([LocalRunProblem(LocalRunErrorCode.CATALOG_QUERY_INVALID, str(exc))])

@api_method("catalog")
def catalog_coatings() -> dict[str, Any]:
    """The published coating models (R / T bands, angle-of-incidence range) the catalog's products refer to."""

    try:
        catalog = load_public_catalog()
    except LocalRunError as exc:
        return _error(exc.problems)
    return {"ok": True, "catalog_version": catalog.catalog_version,
            "coatings": [c.model_dump(mode="json", by_alias=True, exclude_none=True) for c in catalog.coatings]}


@api_method("project")
def example_project_document(folded: bool = False, kind: str | None = None) -> dict[str, Any]:
    """A reference design (``os_optics.local.example``) as its JSON document and osc1 hash: ``kind`` one of
    ``EXAMPLE_KINDS`` (default ``focus``: source -> lens -> focal plane); ``folded=True`` means ``kind="folded"``."""

    chosen = kind if kind is not None else ("folded" if folded else "focus")
    try:
        project = example_project_of_kind(chosen)
    except LocalRunError as exc:
        return _error(exc.problems)
    return {"ok": True, "kind": chosen, "project": project.payload(), "project_hash": project.content_hash()}


#: The Project's resource budgets and pupil sampling (``ComputeBudgetV1``), in their contract order.
BUDGET_FIELDS = tuple(_BUDGET_RANGES)
#: The Project's validity limits, levels and match windows (``NumericalSettingsV1``, ``design_point`` flattened), in contract order.
SETTING_FIELDS = _SETTING_NAMES


def _effective(numerical: Any, names: tuple[str, ...]) -> dict[str, Any]:
    return {name: getattr(numerical, name) for name in names}


@api_method("project")
def check_project(document: Any, *, catalog: Any = None, edge_review_ratio: float | None = None) -> dict[str, Any]:
    """Validate a Project without running the Engine: the ``ProjectDocumentV2`` contract, then the same fail-closed
    lowering a run performs (catalog binding, declared models, source physics, metrics). On success returns the normalized
    document (``payload()``), its osc1 ``project_hash``, the Engine ``input_hash`` a run would use, the catalog binding
    (``catalog_version``: ``None`` without catalog components; ``simulated_parts``: the catalog components), the
    ``design_intent`` (``LAB_ASSEMBLY`` when the Project states none), the values in force of every budget
    (``compute_budget``) and every setting (``numerical_settings``, ``design_point`` flattened to its ``design_point_*``
    names: the Project's where it states one, else the Engine default), and ``declared_models``: per component with a
    declared model its ``optical_model_id``, model kind, Engine surfaces and Engine materials (a compound unit: its
    members), ``optical_solids``: the optical-solid report of the compiled bodies (its errors refuse the Project; its
    warnings / contacts are listed here) and ``warnings``: advisory facts that decide part of the run's outcome
    (``os_optics.local.advisories``: ``TARGET_INVALID`` / ``TARGET_PARAMETER_INVALID`` / ``UNKNOWN_SOURCE_REFERENCE``, a
    target that cannot be read as written, which the run reports ``UNKNOWN`` with that code; ``INPUT_NOT_SIMULATED``, an input the simulation does not read -- a selected parameter
    that is not an attenuator position, an entry of the Project's ``materials`` list --; ``TARGET_UNIT_MISMATCH``, a target whose unit is not its metric's, whose value the run
    reports in the metric's unit without comparing its bounds, so its status is ``UNKNOWN`` (a target the run cannot
    evaluate at all keeps that outcome);
    ``SOURCE_WAVELENGTH_OUTSIDE_COMPONENT_BAND``, one per optical body that no source line of the Project fits (its model
    band or, for a tabulated thin lens or a Faraday rotator, its table's domain): every beam that meets it is outside its
    model, which the run reports in ``model_scope_issues`` (for those two also as an Engine issue in ``issues``;
    ``details.reported_in`` is then ``engine_issues``). A body some line fits has no warning: which of the other lines
    reach it is known after the trace and reported by the run), each
    ``{code, message, location, details}``; they
    never refuse a Project. A Project the solid check refuses gives ``{"ok": False, "error": ...,
    "optical_solids": <the report>}``, each problem with its ``details``. ``catalog`` (Python hosts): a catalog release
    to check against instead of the one the Project pins. ``edge_review_ratio``: the review ratio of the reported
    ``optical_solids`` (``inspect_optical_solids``; default 0.35)."""

    try:
        band = _edge_band(edge_review_ratio)
        project = load_project(document)
        if project.configurations:
            return _check_configurations(project, catalog)
        lowered = lower_project(project, catalog)
    except LocalRunError as exc:
        refused = _error(exc.problems)
        if exc.optical_solids is not None:  # the whole solid report, as for an accepted Project
            refused["optical_solids"] = exc.optical_solids
        return refused
    solids = lowered.solids
    if band is not None and solids is not None:  # the same bodies, reviewed at the caller's ratio
        from ..optical_solids import inspect_lowered

        solids = inspect_lowered(lowered, project, edge_band=band)
    return {
        "ok": True,
        "project": project.payload(),
        "project_hash": project.content_hash(),
        "engine_input_hash": lowered.engine_input_hash,
        "catalog_version": lowered.catalog_version,
        "simulated_parts": [part.model_dump(mode="json", exclude_none=True) for part in lowered.simulated_parts],
        "design_intent": project.effective_design_intent.value,
        "compute_budget": _effective(lowered.request.configuration.numerical, BUDGET_FIELDS),
        "numerical_settings": _effective(lowered.request.configuration.numerical, SETTING_FIELDS),
        "declared_models": declared_model_summary(lowered.elements),
        "optical_solids": None if solids is None else solids.as_dict(),
        "warnings": project_warnings(project, lowered),
    }


def _check_configurations(project: ProjectDocumentV2, catalog: Any) -> dict[str, Any]:
    """``check_project`` of a Project with configurations: every configuration lowered on its own
    (``lower_configurations``). ``configurations`` lists per configuration ``ok``, its ``project_hash`` (of
    ``configured(id)``) and, when it can be lowered, its ``engine_input_hash``, ``compute_budget``,
    ``numerical_settings``, ``optical_solids`` and ``warnings``; one that cannot be lowered has ``ok`` false and its
    ``problems`` (located ``configuration <id> ...``) and is named in ``not_lowered``: a run keeps it as ``FAILED`` and runs
    the others. The Project is refused only when no configuration can be lowered. The reply's ``engine_input_hash`` is
    the run's configurations input hash; the catalog binding and declared models are those of the first configuration
    that lowers (configurations change poses, sources and budgets, never which models the components use);
    ``warnings`` collects every configuration's, each with its ``configuration_id``."""

    lowerings = lower_configurations(project, catalog)
    rows, warnings = [], []
    for item in lowerings:
        cid, low = item.configuration_id, item.lowered
        configured = project.configured(cid)
        if low is None:
            rows.append({"configuration_id": cid, "label": project.configuration(cid).label, "ok": False,
                         "project_hash": configured.content_hash(), "engine_input_hash": None,
                         "problems": [problem.as_dict() for problem in item.problems]})
            continue
        found = project_warnings(configured, low) + configuration_warnings(project, cid)
        warnings.extend({**warning, "configuration_id": cid} for warning in found)
        rows.append({"configuration_id": cid, "label": project.configuration(cid).label, "ok": True, "project_hash": configured.content_hash(),
                     "engine_input_hash": low.engine_input_hash,
                     "compute_budget": _effective(low.request.configuration.numerical, BUDGET_FIELDS),
                     "numerical_settings": _effective(low.request.configuration.numerical, SETTING_FIELDS),
                     "optical_solids": None if low.solids is None else low.solids.as_dict(), "warnings": found})
    first = next(item.lowered for item in lowerings if item.lowered is not None)
    return {
        "ok": True,
        "project": project.payload(),
        "project_hash": project.content_hash(),
        "engine_input_hash": configurations_input_hash(
            [(item.configuration_id, None if item.lowered is None else item.lowered.engine_input_hash) for item in lowerings]),
        "not_lowered": [item.configuration_id for item in lowerings if item.lowered is None],
        "catalog_version": first.catalog_version,
        "simulated_parts": [part.model_dump(mode="json", exclude_none=True) for part in first.simulated_parts],
        "design_intent": project.effective_design_intent.value,
        "compute_budget": None,
        "numerical_settings": None,
        "declared_models": declared_model_summary(first.elements),
        "optical_solids": None,
        "warnings": warnings,
        "configurations": rows,
    }


#: Options ``update_project`` takes.
UPDATE_OPTIONS = ("expected_project_hash", "require_check")


@api_method("project")
def update_project(document: Any, ops: Any, options: Any = None) -> dict[str, Any]:
    """Apply field-level Project edit operations (``os_optics.local.project_ops``) to a Project and report the check of the result (``check_project``) -- atomically.

    ``document``: the Project; ``ops``: a non-empty list of ``{"op": NAME, ...}``; ``options``: ``{"expected_project_hash":
    H, "require_check": B}`` (both optional; the Project's osc1 hash the operations were written against; ``require_check``
    true refuses an edit whose result the check does not accept).

    Success: ``{"ok": True, "project", "project_hash", "engine_input_hash", "base_project_hash", "changed", "affected":
    [{kind, id, change, fields}], "already_present": [{op_index, op, kind, id}], "check": {"ok", "problems"}, "warnings",
    "catalog_version", "design_intent", "optical_solids", "compute_budget", "numerical_settings"}``; ``changed`` is false
    when the operations left the Project as it was (a retried list of absolute operations), ``already_present`` lists the
    adds answered by an identical item already there. ``check.ok`` says whether a run accepts the edited Project; when it
    is false, ``check.problems`` are the problems ``run_project`` reports for it and ``engine_input_hash``,
    ``catalog_version``, ``compute_budget`` and ``numerical_settings`` are null.

    Refusals change nothing: ``{"ok": False, "error": {"code": "PROJECT_OP_INVALID", "message", "op_index", "op_code",
    "problems"}}`` (an operation that cannot be applied; ``op_code`` one of ``UNKNOWN_OP``, ``NOT_FOUND``,
    ``DUPLICATE_ID``, ``BAD_ARGUMENT``, ``IN_USE``, ``TRANSFORM_REFUSED``; ``op_index`` -1 for the list or the options),
    ``{"code": "PROJECT_REJECTED", "message", "problems", "optical_solids"?}`` (the edited document is no Project, or the
    check refused it and ``require_check`` is true: ``LocalRunErrorCode`` problems) or ``{"code": "PROJECT_CHANGED",
    "message", "project_hash", "expected_project_hash", "problems"}`` (the Project's hash is not
    ``expected_project_hash``: read it again)."""

    if options is None:
        options = {}
    if not isinstance(options, dict) or set(options) - set(UPDATE_OPTIONS):
        message = f"options is an object with keys among {list(UPDATE_OPTIONS)}"
        return {"ok": False, "error": {"code": "PROJECT_OP_INVALID", "message": message, "op_index": -1, "op_code": "BAD_ARGUMENT",
                                       "problems": [{"code": "BAD_ARGUMENT", "message": message}]}}
    try:
        update = _update_project(document, ops, expected_project_hash=options.get("expected_project_hash"),
                                 require_check=options.get("require_check", False))
    except ProjectUpdateError as exc:
        return {"ok": False, "error": exc.as_dict()}
    return {"ok": True, **update.as_dict()}


@api_method("run", file_map="run")
def run_freshness(document: Any, run: Any, verify: bool = False) -> dict[str, Any]:
    """Is ``run`` current for the Project ``document`` (``os_optics.local.freshness``)? ``run``: the run's
    ``{file name: bytes or text}`` (``run_project``'s ``files``), a ``run_project`` reply, a Local Validation record document, or
    (Python hosts) a run directory path. ``{"ok": True, "freshness": "CURRENT" | "HISTORICAL" | "UNDETERMINED",
    "reasons", "physics": {"freshness", "reasons"}, "requirements": {"freshness"}, "engine_build_matches", "run", "current",
    "integrity"}`` (and ``coverage`` for a run of configurations). ``physics`` is ``CURRENT`` when the Engine input without its
    targets is the run's: a re-run is needed only when it is not. ``verify`` true also verifies the run's files and
    reports it in ``integrity`` (``checked`` false without it; the freshness never depends on it). A document that is not
    a Project gives the ``LOCAL_RUN_ERROR`` envelope,
    a run that cannot be read ``{"ok": False, "error": {"code": "RUN_NOT_READABLE", "problems": [...]}}``."""

    try:
        return {"ok": True, **_run_freshness(document, run, verify=bool(verify))}
    except LocalRunError as exc:
        return _error(exc.problems)
    except RunNotReadable as exc:
        return {"ok": False, "error": exc.as_dict()}


def _observations(result: ValidationResult, sig_digits: int, abs_floor: float, scope_issues: Any = ()) -> list[dict[str, Any]]:
    """Per observation plane, its absorbing semantics (``absorbing_extent``: ``NONE``, ``ACTIVE_AREA`` or
    ``UNBOUNDED_PLANE``), the power it receives (``total_power_W``; ``power_complete`` false when part of the launched
    light was not followed, so 0 W is then not a known zero; ``power_basis`` where it is the incoherent sum beside a coherent
    quantity), ``message`` (the plane's own sentence, as in ``simulation_result.json`` and ``results monitors``: e.g. why its
    power is not known or has no upper bound; empty when there is nothing to add), the coherent sum per wavelength where two
    or more beams arrive (``superpositions``: its value, or its range over every phase with ``phase_statement``) and the beams
    that arrive there (Gaussian radius, waist, power): the numbers a UI shows. A beam
    path that comes back through the plane (a fold, a double pass, a retro-reflection) arrives once per crossing,
    numbered by ``crossing_index`` (0 = its first crossing).

    They are quantized exactly as the Engine quantizes its result for the result hash (``sig_digits`` / ``abs_floor``
    of the run's numerical configuration), so Python and the browser show the same digits whenever their result hashes agree
    (the full-precision values may differ in the last bits between CPython and Pyodide). ``scope_issues``: the run's
    model-scope findings, whose checks the powers of the light they reach carry in their ``value_basis``
    (``results.basis.scoped_observation``)."""

    rows = []
    for observation in (scoped_observation(o, scope_issues) for o in result.observations):
        arrivals = []
        for arrival in observation.arrivals:
            beam = arrival.beam
            gaussian = beam.gaussian
            arrivals.append({
                "source_id": beam.source_id,
                "path_id": beam.path_id,
                "crossing_index": arrival.crossing_index,
                "wavelength_nm": beam.wavelength_nm,
                "power_W": beam.power_W,
                **({} if arrival.power_upper_bound_W is None else {"power_upper_bound_W": arrival.power_upper_bound_W}),
                **({} if not arrival.grating_orders else {"grating_orders": [step.model_dump(mode="json") for step in arrival.grating_orders]}),
                # the plane passes through a physical surface's hit point: the surface, and whether this beam arrives at it or returns from it
                **({} if arrival.surface_coincidence is None else {"surface_coincidence": arrival.surface_coincidence.model_dump(mode="json")}),
                # the power is sampled by the pupil rays (an aperture cut them without a valid Gaussian state), within this resolution
                **({} if arrival.power_basis is None else {"power_basis": arrival.power_basis,
                                                           "power_sampling_resolution_W": arrival.power_sampling_resolution_W}),
                # surfaces take a bounded share of the beam before the plane: its crossing power lies in
                # [power_W - upper, power_W - lower] (lower 0 when none is certain)
                **({} if arrival.power_taken_before_upper_W is None else {
                    "power_taken_before_at": list(arrival.power_taken_before_at),
                    "power_taken_before_upper_W": arrival.power_taken_before_upper_W,
                    **({} if arrival.power_taken_before_lower_W is None else {
                        "power_taken_before_lower_W": arrival.power_taken_before_lower_W})}),
                # the chief ray fell outside a clear aperture there: a passing pupil ray carries the beam's reference from there
                **({} if not arrival.reference_ray_promoted_at else {"reference_ray_promoted_at": list(arrival.reference_ray_promoted_at)}),
                # its power is out of model scope behind these surfaces: power_W is its chief ray's, not the light's
                **({} if not arrival.power_out_of_scope_at else {"power_out_of_scope_at": list(arrival.power_out_of_scope_at)}),
                "detected_power_W": arrival.detected_power_W,
                "position_mm": list(beam.position_mm),
                "beam_radius_x_mm": gaussian.beam_radius_x_mm if gaussian is not None else None,
                "beam_radius_y_mm": gaussian.beam_radius_y_mm if gaussian is not None else None,
                "waist_radius_x_mm": gaussian.waist_radius_x_mm if gaussian is not None else None,
                "waist_distance_x_mm": gaussian.waist_distance_x_mm if gaussian is not None else None,
                "rayleigh_range_x_mm": gaussian.rayleigh_range_x_mm if gaussian is not None else None,
                # a Gaussian state beyond its model's scope publishes no radius, waist or range: its reason and failed check
                **({} if gaussian is None or gaussian.valid else {
                    **({} if gaussian.reason is None else {"gaussian_reason": str(getattr(gaussian.reason, "value", gaussian.reason))}),
                    **({} if not gaussian.validity else {"gaussian_validity": [v.model_dump(mode="json") for v in gaussian.validity]})}),
                **row_bases([("power_W", arrival, "beam.power_W"), ("detected_power_W", arrival, "detected_power_W"),
                             *((("position_mm", arrival, "beam.position_mm"),) if "beam.position_mm" in (arrival.value_basis or {}) else ()),
                             *((name, gaussian, name) for name in ("beam_radius_x_mm", "beam_radius_y_mm", "waist_radius_x_mm",
                                                                   "waist_distance_x_mm", "rayleigh_range_x_mm")
                               if gaussian is not None)]),
            })
        sampled = [sampled_summary(line) for line in observation.sampled_light]
        rows.append({"observation_id": observation.observation_id, "status": observation.status.value,
                     "message": observation.message,
                     "quantity_state": None if observation.quantity_state is None else observation.quantity_state.value,
                     "untraced_power_W": quantize(observation.untraced_power_W, sig_digits, abs_floor),
                     **({"power_upper_bound_W": observation.power_upper_bound_W}
                        if "power_upper_bound_W" in observation.model_fields_set else {}),
                     **({"power_lower_bound_W": observation.power_lower_bound_W} if observation.power_lower_bound_W is not None else {}),
                     **({"power_sampling_resolution_W": observation.power_sampling_resolution_W}
                        if observation.power_sampling_resolution_W is not None else {}),
                     "issue_codes": sorted({i.code.value for i in result.issues if i.location == f"observation:{observation.observation_id}"}),
                     "absorbing_extent": None if observation.plane is None else observation.plane.absorbing_extent,
                     # 0 W with power_complete is a known dark plane; without it the trace did not follow all light
                     "total_power_W": quantize(observation.total_power_W, sig_digits, abs_floor),
                     # INCOHERENT_SUM: total_power_W adds the beams' powers, superpositions states their coherent power
                     **({} if observation.power_basis is None else {"power_basis": observation.power_basis}),
                     "power_complete": observation.power_complete,
                     **({"sampled_light": quantize(sampled, sig_digits, abs_floor)} if sampled else {}),
                     **({"superpositions": quantize([dump_with_bases(s) for s in observation.superpositions], sig_digits, abs_floor)}
                        if observation.superpositions else {}),
                     **quantize(row_bases([("total_power_W", observation, "total_power_W")]), sig_digits, abs_floor),
                     "arrivals": quantize(arrivals, sig_digits, abs_floor)})
    return rows


def _power_budget(result: ValidationResult, sig_digits: int, abs_floor: float) -> dict[str, Any] | None:
    """Compact power budget of the run (``ValidationResult.power_budget``): per source spectral line its
    ``input_power_W``, the power of each sink kind (``sinks``, e.g. ``DETECTED``, ``ESCAPED``, ``COATING_ABSORPTION``),
    the ``residual_W`` the ledger leaves and whether it closes (``conserved``); plus the run's ``total_input_W``,
    ``total_sinks`` and ``conserved``. Quantized like ``_observations``; ``None`` when the Engine reported no budget."""

    budget = result.power_budget
    if budget is None:
        return None
    lines = [{"source_id": line.source_id, "spectral_index": line.spectral_index, "wavelength_nm": line.wavelength_nm,
              "input_power_W": line.input_power_W, "sinks": dict(line.sinks), "residual_W": line.residual_W, "conserved": line.conserved}
             for line in budget.lines]
    return quantize({"lines": lines, "total_input_W": budget.total_input_W, "total_sinks": dict(budget.total_sinks),
                     "conserved": budget.conserved}, sig_digits, abs_floor)


def run_summary(run: LocalOpticalRun) -> dict[str, Any]:
    """``run.summary()`` without the artifact paths, plus the Engine input / result hashes, the observed beams
    (``observations``) and the compact power budget (``power_budget``).

    Its Engine numbers -- the target values, the observed beams and the power budget -- carry the result hash's quantization
    (``_observations``), so the summary is identical in Python and in the browser whenever their result hashes agree; the full
    precision values are in ``simulation_result.json``."""

    numerical = run.request.configuration.numerical
    summary = run.summary()
    summary["targets"] = [{**target, "value": quantize(target["value"], numerical.hash_float_sig_digits, numerical.hash_float_abs_floor),
                           "error": quantize(target["error"], numerical.hash_float_sig_digits, numerical.hash_float_abs_floor),
                           **({"value_basis": quantize(target["value_basis"], numerical.hash_float_sig_digits,
                                                       numerical.hash_float_abs_floor)} if "value_basis" in target else {}),
                           **({"validity": quantize(target["validity"], numerical.hash_float_sig_digits,
                                                    numerical.hash_float_abs_floor)} if "validity" in target else {})}
                          for target in summary["targets"]]
    summary["issues"] = [{**issue, "numerical_context": quantize(issue["numerical_context"], numerical.hash_float_sig_digits,
                                                                 numerical.hash_float_abs_floor)} for issue in summary["issues"]]
    summary.pop("artifacts", None)
    summary["engine_input_hash"] = run.local_validation.input_hash.digest
    summary["engine_result_hash"] = run.local_validation.result_hash.digest
    summary["engine_version"] = run.local_validation.runtime.engine_version
    summary["engine_build_id"] = run.local_validation.runtime.engine_build_id
    summary["runtime_kind"] = run.local_validation.runtime.runtime_kind.value
    summary["observations"] = _observations(run.result, numerical.hash_float_sig_digits, numerical.hash_float_abs_floor,
                                          run.model_scope_issues or ())
    quantities = {q.observation_id: q for q in run.local_validation.simulation.observation_quantities or ()}
    for observation in summary["observations"]:
        quantity = quantities.get(observation["observation_id"])
        if quantity is not None:
            observation["quantity_state"] = quantity.quantity_state
            observation["issue_codes"] = list(quantity.issue_codes)
    summary["power_budget"] = _power_budget(run.result, numerical.hash_float_sig_digits, numerical.hash_float_abs_floor)
    return summary


#: ``run_project(resource_limits=...)``: the limits a host may set, in MiB (2^20 bytes), and their monitor arguments;
#: ``preflight`` (true or false, default true): whether a run whose estimate exceeds a limit is refused before the trace.
RESOURCE_LIMIT_KEYS = {"max_memory_mib": "max_memory_bytes", "max_artifact_mib": "max_artifact_bytes", "preflight": "refuse_on_estimate"}
MIB = 1 << 20


def _resource_limits(limits: Any) -> dict[str, Any]:
    """``resource_limits`` as ``RunMonitor`` arguments (bytes); refuses anything but positive whole MiB of the known keys."""
    if limits is None:
        return {}
    if not isinstance(limits, dict) or not set(limits) <= set(RESOURCE_LIMIT_KEYS):
        raise LocalRunError([LocalRunProblem(LocalRunErrorCode.PROJECT_INVALID,
                                             f"invalid resource_limits: {limits!r} (an object of {', '.join(RESOURCE_LIMIT_KEYS)})")])
    out: dict[str, Any] = {}
    for key, value in limits.items():
        if value is None:
            continue
        if key == "preflight":
            if not isinstance(value, bool):
                raise LocalRunError([LocalRunProblem(LocalRunErrorCode.PROJECT_INVALID,
                                                     f"invalid resource_limits.preflight: {value!r} (true or false)")])
            out[RESOURCE_LIMIT_KEYS[key]] = value
            continue
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise LocalRunError([LocalRunProblem(LocalRunErrorCode.PROJECT_INVALID,
                                                 f"invalid resource_limits.{key}: {value!r} (a positive whole number of MiB)")])
        out[RESOURCE_LIMIT_KEYS[key]] = value * MIB
    return out


def stopped_reply(stopped: RunStopped) -> dict[str, Any]:
    """The reply of a run that stopped before it completed: ``{"ok": False, "stopped": <StoppedRun>, "error": {"code":
    "RUN_STOPPED", "message", "problems": [{"code": <stop cause>, "message", "location": "phase <phase>"}]}}``."""
    record = stopped.stopped
    return {"ok": False, "stopped": record.model_dump(mode="json") if record is not None else None,
            "error": {"code": "RUN_STOPPED", "message": stopped.message,
                      "problems": [{"code": stopped.cause, "message": stopped.message, "location": f"phase {stopped.phase}"}]}}


@api_method("run")
def run_project(document: Any, view_settings: Any = None, output_dir: str | None = None, ray_data: str = "full",
                configuration_ids: Any = None, resource_limits: Any = None) -> dict[str, Any]:
    """One local run (``run_optical_project``): the Engine result, the Local Validation record and the Optical
    Simulation View from one Engine execution.

    Returns ``summary`` (``run_summary``), ``local_validation`` and ``optical_scene`` (JSON documents), the run
    directory ``manifest``, and ``files``: every run-directory file name -> ``bytes`` (``manifest.json``,
    ``project.json``, ``engine_request.json``, ``simulation_result.json``, ``ray_data.json``, ``local_validation.json``,
    ``optical_scene.json``, ``optical_scene.glb``). With ``output_dir`` the directory is also written there from the same
    bytes (each file serialized once) and read back to check it holds them (size and SHA-256, ``written_run_problems``);
    ``verify`` lists its problems (empty = consistent), else it is ``None``. The run itself is checked as it is built
    (``run_optical_project``), so nothing is parsed a second time. ``ray_data``: what ``ray_data.json`` records, ``full``
    (default) or ``summary`` (the chief rays only; ``run_optical_project``); another value is refused (``PROJECT_INVALID``).

    A Project with configurations (``ProjectDocumentV2.configurations``) runs every configuration, or those
    ``configuration_ids`` lists (``run_configured_project``; ``configuration_ids`` on a Project without configurations is
    refused, ``CONFIGURATION_UNKNOWN``): ``{"ok": True, "engine_input_hash", "execution", "configurations", "comparison",
    "manifest", "files", "verify"}``. ``configurations`` lists per configuration its ``configuration_id``, ``label``,
    ``directory``, ``engine_input_hash``, ``execution`` and ``problems`` and, for a configuration that ran, its ``status``,
    ``summary`` (``run_summary``), ``local_validation`` and ``optical_scene``; ``comparison`` is the ``CONFIGURATIONS``
    analysis document; ``files`` holds every file of the run directory (``configurations/<id>/<file>`` for each
    configuration's run, then ``project.json``, ``comparison.json`` and ``manifest.json``). There is no overall status.

    Progress, cancellation and resource limits (``os_optics.run_control``): the run is monitored by the host's
    ``RunMonitor`` when one is installed (``run_control.monitored``: the host can report progress and
    cancel it), else by one of its own when ``resource_limits`` is given: ``{"max_memory_mib": N, "max_artifact_mib":
    M, "preflight": true}`` (whole MiB; the run files of this reply are held in memory and count toward the memory;
    ``preflight`` false: a run whose estimate exceeds a limit starts anyway, the limits holding during the run only). A run that stops --
    cancelled, at a limit, or refused before the trace because its minimum exceeds a limit -- returns ``stopped_reply``:
    ``ok`` false, ``stopped`` its ``StoppedRun`` record (execution ``CANCELLED``, ``stop`` where and why, every observation
    and target unevaluated) and no run file; its record is written into ``output_dir`` (``run_stopped.json``). A run that
    completes replaces the run in ``output_dir``; a run that stops, is refused or fails before the run is moved into place
    leaves the directory as it found it, so an earlier run there still verifies (``local/artifacts.py``); a failure while
    the run is moved into place (a file held open elsewhere) leaves what a process killed there leaves -- past the move's
    first step, no manifest, so no run that verifies, and the Engine's records of what is there; at the first step (it
    removes the run files of each configuration that did not run this time, then the stop records, then the
    configurations' manifests, then the directory's own) the directory's manifest, beside that configuration's manifest
    without the run files removed before, or beside a configuration whose manifest is gone, recorded by its copy, a run
    that verifies INCONSISTENT -- and any run then completes there. An
    ``output_dir`` the run may not write into is a
    refusal with nothing written: ``LOCAL_RUN_ERROR`` with ``RUN_DIRECTORY_OCCUPIED`` (a file there the Engine did not
    write) or ``RUN_DIRECTORY_LINK`` (a link on the way). A monitored
    run interrupted by the host (``KeyboardInterrupt``: Pyodide's interrupt buffer) stops the same way; a stop or an
    interrupt that lands once the run's manifest is in place answers with the completed run, and no stop record is
    written. A monitored run that completes also returns ``progress`` (``RunMonitor.summary``: seconds per phase, elapsed time, peak memory read, run-file
    bytes, the estimate and the limits).
    """

    monitor = active_monitor()
    project = None
    held: dict[str, Any] = {}  # (a run being written: what a stop after its move into place answers with)
    attempt = begin_attempt()
    try:
        settings = _view_settings(view_settings)
        if ray_data not in RAY_DATA_LEVELS:
            raise LocalRunError([LocalRunProblem(LocalRunErrorCode.PROJECT_INVALID,
                                                 f"invalid ray_data: {ray_data!r} (one of {', '.join(RAY_DATA_LEVELS)})")])
        limits = _resource_limits(resource_limits)
        if monitor is None and limits:
            monitor = RunMonitor(**{("preflight" if k == "refuse_on_estimate" else k): v for k, v in limits.items()})
        elif monitor is not None:
            for name, value in limits.items():
                setattr(monitor, name, value)
        if monitor is not None:
            monitor.files_in_memory = True
        project = load_project(document)
        with monitored(monitor):
            if project.configurations or configuration_ids is not None:
                configured = run_configured_project(project, view_settings=settings, ray_data=ray_data,
                                                    configuration_ids=configuration_ids)
                files = configurations_run_files(configured)
                verify = None
                if output_dir is not None:
                    held.update(configured=configured, files=files)
                    refused = _write_refused(lambda: write_configurations_files(files, output_dir))
                    if refused is not None:
                        return refused
                    verify = list(written_run_problems(files, output_dir))
                return _configured_reply(configured, files, verify)
            run = run_optical_project(project, view_settings=settings, ray_data=ray_data)
            files = run_artifact_files(run)  # a view the run's coordinates cannot draw is a refusal too
            with phase("reply"):  # (the run's summary and documents: a monitored caller sees this step too)
                reply = {**outcome_lead(run.local_validation.simulation.outcome, run.status.value),
                         "status": run.status.value, "summary": run_summary(run),
                         "local_validation": run.local_validation.payload(), "optical_scene": run.optical_view.payload(),
                         "manifest": json.loads(files[MANIFEST_FILE])}
        verify = None
        if output_dir is not None:  # (in the attempt: a stop or an interrupt once its manifest is in place answers with it)
            held.update(files=files, reply=reply)
            refused = _write_refused(lambda: write_run_files(files, output_dir))
            if refused is not None:
                return refused
            verify = list(written_run_problems(files, output_dir))
    except LocalRunError as exc:
        return _error(exc.problems)
    except RunStopped as stopped:
        if held and committed_run(output_dir) is not None:  # (the stop came once the run was in place: it completed)
            return _completed_reply(held, output_dir, monitor)
        return stopped_reply(stop_run(stopped, project, monitor, output_dir=output_dir))
    except KeyboardInterrupt:
        if held and committed_run(output_dir) is not None:
            return _completed_reply(held, output_dir, monitor)
        if monitor is None:
            raise
        return stopped_reply(stop_run(monitor.stopped_by_interrupt(), project, monitor, output_dir=output_dir))
    finally:
        end_attempt(attempt)
    return _run_reply(reply, files, verify, monitor)


def _run_reply(reply: dict[str, Any], files: dict[str, bytes], verify: list[str] | None, monitor: Any) -> dict[str, Any]:
    return {
        "ok": True,
        **reply,
        "files": files,
        "verify": verify,
        # (a monitored run: its seconds per phase, peak memory read and run-file bytes -- facts of this execution, no hash)
        **({"progress": monitor.summary()} if monitor is not None and hasattr(monitor, "summary") else {}),
    }


def _completed_reply(held: dict[str, Any], output_dir: str, monitor: Any) -> dict[str, Any]:
    """The reply of a run whose move into ``output_dir`` completed before a stop or an interrupt (``held``: what it wrote)."""
    verify = list(written_run_problems(held["files"], output_dir))
    if "configured" in held:
        return _configured_reply(held["configured"], held["files"], verify)
    return _run_reply(held["reply"], held["files"], verify, monitor)


EVALUATION_INPUT_INVALID = "INPUT_INVALID"


def _names(value: Any, name: str) -> tuple[str, ...] | None:
    """A list of ids (or one id) given to ``evaluate_project``; ``None`` stays ``None``."""
    if value is None:
        return None
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return tuple(value)
    raise ValueError(f"{name} is a list of ids, not {value!r}")


def _plan_error(exc: EvaluationPlanError) -> dict[str, Any]:
    return {"ok": False, "error": {"code": exc.code, "message": exc.message,
                                   "problems": [{"code": exc.code, "message": exc.message,
                                                 **({"context": {k: v for k, v in exc.context.items()}} if exc.context else {})}]}}


def _evaluation_reply(evaluation: Any) -> dict[str, Any]:
    """The reply of ``evaluate_project`` for an ``os_optics.local.Evaluation``: its scope (``evaluation``), the lead
    ``execution``, ``requirements`` (``Evaluation.requirements()``: the assessed targets by assessment and ``not_assessed``,
    the declared targets it did not assess) and ``diagnostic_summary``, ``status`` (the diagnostic summary of what was
    computed; ``NOT_APPLICABLE`` rather than ``PASS`` when declared targets exist and none was assessed), ``outcome``
    (execution and the requirements of the evaluated targets, with
    the published models' applicability), ``targets`` (each evaluated target: assessment, status, value quantized like
    ``run_summary``, unit, message, issue codes), ``monitors`` (each plane's compact readings, quantized like
    ``run_summary``: its quantity state -- ``NOT_COMPUTED`` without the plane bounds -- power and arriving beams; empty
    when the plane readings were not computed), ``issues``, ``model_scope_issues`` (``None`` when not computed),
    ``identity``; when ``solid_check`` was computed, ``optical_solids`` (the optical-solid report: ``status``, each
    finding with its code, severity, location, ``zone`` and measured value, and per element the centre, minimum and edge
    thickness of every gap); when ``view`` was computed, ``optical_scene`` (the scene, ``OpticalSimulationSceneV1``, bound
    to this evaluation's result)."""

    result = evaluation.result
    numerical = evaluation.request.configuration.numerical
    sig, floor = numerical.hash_float_sig_digits, numerical.hash_float_abs_floor
    summary = scientific_projection(result, evaluation.model_scope_issues or ())
    assessed = {t.target_id: t for t in summary.target_outcomes}
    monitors = []
    if not result.evaluation or "plane_readings" in result.evaluation.executed:
        results = evaluation.results()
        states = {m["observation_id"]: m for m in results.monitors()}
        monitors = [{**row, "quantity_state": states[row["observation_id"]]["quantity_state"],
                     "issue_codes": states[row["observation_id"]]["issue_codes"]} for row in _observations(result, sig, floor, evaluation.model_scope_issues or ())]
    lead = outcome_lead(summary.outcome, evaluation.status)
    # (the declared targets by assessment and those it did not assess: Evaluation.requirements())
    lead["requirements"] = requirements_coverage(lead["requirements"], result.evaluation)
    return {
        "ok": True,
        "evaluation": evaluation.describe_scope(),
        **lead,
        "status": evaluation.status,
        "outcome": None if summary.outcome is None else summary.outcome.model_dump(mode="json"),
        "targets": [{"target_id": t.target_id, "metric": t.metric, "assessment": assessed[t.target_id].assessment,
                     "status": t.status.value, "value": quantize(t.value, sig, floor), "unit": t.unit, "message": t.message,
                     "target_value": t.target_value, "error": quantize(t.error, sig, floor),
                     "issue_codes": sorted(set(t.issue_codes) | set(assessed[t.target_id].applicability_issue_codes or ())),
                     **quantize(target_row_bases(t, evaluation.model_scope_issues or ()), sig, floor),
                     **(quantize({"validity": [v.model_dump(mode="json") for v in t.validity]}, sig, floor) if t.validity else {})}
                    for t in result.targets],
        "monitors": monitors,
        "issues": [{**issue, "numerical_context": quantize(issue["numerical_context"], sig, floor)}
                   for issue in (i.model_dump(mode="json") for i in result.issues)],
        "model_scope_issues": None if evaluation.model_scope_issues is None else [i.as_dict() for i in evaluation.model_scope_issues],
        "identity": evaluation.identity,
        **({} if evaluation.optical_solids is None else {"optical_solids": evaluation.optical_solids.as_dict()}),
        **({} if evaluation.optical_view is None else {"optical_scene": evaluation.optical_view.payload()}),
    }


@api_method("run")
def evaluate_project(document: Any, outputs: Any = None, exclude: Any = None, targets: Any = None, view_settings: Any = None, *,
                     catalog: Any = None, configuration_id: str | None = None) -> dict[str, Any]:
    """A selective evaluation of a Project (``os_optics.local.evaluate_project``): it computes the capabilities and presets
    named in ``outputs`` (default ``["readings"]``: each beam's arrival at each plane with its traced power and the
    model-scope findings; ``["complete"]``: everything a run computes) and what they require, nothing in ``exclude``, and
    the declared ``targets`` named (naming targets requests ``requirements``). ``evaluation_capabilities()`` lists the ids.
    ``view_settings``: what the view draws when ``view`` is computed, as for ``run_project`` (display only).
    ``catalog`` (Python hosts): the public catalog to use instead of the release the Project pins. ``configuration_id``
    (Python hosts and ``project evaluate --configuration``): the configuration to evaluate, of a Project that declares
    configurations.
    Nothing is written and no run file is returned: a run directory and a Local Validation record come from ``run_project``.
    ``record`` and ``ray_paths`` can be named (they are computed), but their documents -- the Local Validation record and
    the ray data -- are not part of this reply: they come from ``run_project``.

    Returns ``{"ok": True, "evaluation", "execution", "requirements", "diagnostic_summary", "status", "outcome", "targets",
    "monitors", "issues", "model_scope_issues", "identity"}`` (``_evaluation_reply``). ``requirements`` names every declared
    target once: ``met``, ``unmet``, ``indeterminate`` and ``not_applicable`` hold the targets assessed, ``not_assessed``
    each one this evaluation did not assess (``target_id``, ``reason``; the default ``readings`` assesses none). ``status``
    is the diagnostic summary of what was computed and no verdict on a target not assessed: ``NOT_APPLICABLE`` rather
    than ``PASS`` when the Project declares targets and none was assessed. With ``optical_solids`` when ``solid_check`` is
    among the outputs: the optical-solid report,
    whose ERROR findings (status ``INVALID``) are reported beside the readings and refuse the evaluation only when it
    makes the record (``record``, ``complete``); with ``optical_scene`` when ``view`` is computed (named, or in
    ``complete``): the scene of what this evaluation traced, in the run's scene contract (``OpticalSimulationSceneV1``), its
    ``engine_run`` naming this evaluation's result -- ``evaluation`` says what else was and was not computed.
    Refusals: a Project that cannot be simulated (``LOCAL_RUN_ERROR``; among them a Project that
    declares configurations, ``CONFIGURATION_NOT_SELECTED``: each configuration is evaluated as its own Project --
    ``run_project(document, configuration_ids=[ID])`` runs one completely, and in Python
    ``ProjectDocumentV2.configured(id)`` gives the Project of one configuration to evaluate), a selection that
    cannot be planned (the ``EvaluationPlanError`` code: ``UNKNOWN_CAPABILITY`` -- also for ``run_files``, which only
    ``run_project`` writes, and for the layout checks and the control analysis, capabilities of an Engine request --,
    ``UNKNOWN_TARGET``, ``EXCLUDED_CAPABILITY_REQUIRED`` -- its message names what requires
    the excluded capability and the two ways out --, ``TARGET_SELECTION_INCOMPLETE`` -- ``record`` with ``targets`` that
    leave a declared target out), parameters of the wrong type (``INPUT_INVALID``). Progress and cancellation as for
    ``run_project``: an evaluation a monitor stops returns ``stopped_reply`` (its record names the Engine input once the
    Project is lowered); a monitored one that completes also returns ``progress``.
    """

    monitor = active_monitor()
    project = None
    facts: dict[str, Any] = {}
    try:
        try:
            names = _names(outputs, "outputs")
            excluded = _names(exclude, "exclude") or ()
            selected = _names(targets, "targets")
        except ValueError as exc:
            return {"ok": False, "error": {"code": EVALUATION_INPUT_INVALID, "message": str(exc),
                                           "problems": [{"code": EVALUATION_INPUT_INVALID, "message": str(exc)}]}}
        settings = _view_settings(view_settings)
        project = configured_project(document, configuration_id)
        with monitored(monitor):
            evaluation = EvaluationSession(catalog=catalog).evaluation(project, names if names is not None else ("readings",), excluded,
                                                                       selected, view_settings=settings, facts=facts)
            with phase("reply"):
                reply = _evaluation_reply(evaluation)
    except LocalRunError as exc:
        return _error(exc.problems)
    except EvaluationPlanError as exc:
        return _plan_error(exc)
    except RunStopped as stopped:
        return stopped_reply(stop_run(stopped, project, monitor, engine_input_hash=facts.get("engine_input_hash")))
    except KeyboardInterrupt:
        if monitor is None:
            raise
        return stopped_reply(stop_run(monitor.stopped_by_interrupt(), project, monitor,
                                      engine_input_hash=facts.get("engine_input_hash")))
    return {**reply, **({"progress": monitor.summary()} if monitor is not None and hasattr(monitor, "summary") else {})}


@api_method("run")
def evaluation_capabilities(capability_id: Any = None) -> dict[str, Any]:
    """The capabilities a Project evaluation is made of. Without ``capability_id``: the index (``capabilities``: id,
    level, kind, summary, requires; ``presets`` with their members; ``analyses`` and ``exports`` with their Python call
    and command). With it: that capability, analysis or export in full (``capability``:
    ``os_optics.local.evaluate.capability_descriptor``: requires, produces, the target metrics that read it, numerical
    controls, phases, method, version), or that preset (``preset``: ``os_optics.local.evaluate.preset_descriptor``: id,
    summary, ``members`` -- the capabilities it runs -- and ``leaves_out`` -- those it does not). An id a Project
    evaluation does not hold: ``UNKNOWN_CAPABILITY`` (for the layout checks and the control analysis the message names
    ``os_optics.evaluate``, which runs them on an Engine request)."""

    if capability_id is None:
        return {"ok": True, **capability_index()}
    try:
        return {"ok": True, "preset": preset_descriptor(capability_id)}
    except KeyError:
        pass
    try:
        return {"ok": True, "capability": capability_descriptor(capability_id)}
    except (KeyError, TypeError):
        index = capability_index()
        known = [row["id"] for key in ("capabilities", "analyses", "exports") for row in index[key]]
        message = (f"{capability_id!r} is not a capability, analysis, export or preset id; the ids are {', '.join(known)}; "
                   f"the presets are {', '.join(index['presets'])}")
        if capability_id in ENGINE_REQUEST_ONLY:
            try:
                plan_evaluation((capability_id,), level="local", what="capability_id")
            except EvaluationPlanError as refused:
                message = refused.message
        return {"ok": False, "error": {"code": "UNKNOWN_CAPABILITY", "message": message,
                                       "problems": [{"code": "UNKNOWN_CAPABILITY", "message": message}]}}


#: The calls of prepared evaluations (``capabilities()["evaluation"]["prepared"]``; ``os_optics.local.prepared``).
PREPARED_METHODS = ("prepare_evaluation", "evaluate_prepared", "cancel_evaluation", "release_evaluation", "evaluation_handles")


def _input_invalid(message: str) -> dict[str, Any]:
    return {"ok": False, "error": {"code": EVALUATION_INPUT_INVALID, "message": message,
                                   "problems": [{"code": EVALUATION_INPUT_INVALID, "message": message}]}}


@api_method("run")
def prepare_evaluation(document: Any) -> dict[str, Any]:
    """Prepare evaluations of a Project (``os_optics.local.prepared``): the Project lowered in a new
    ``EvaluationSession`` that this runtime keeps under the returned handle, for ``evaluate_prepared``. Returns ``{"ok":
    True, "handle", "project_hash", "engine_input_hash", "catalog_version", "max_handles", "handles", "notices"}``
    (``handles``: how many this runtime holds now; ``notices``: an ``EVALUATION_HANDLE_EVICTED`` notice for each handle
    evicted to stay within ``max_handles``). Refusals: a Project that cannot be simulated (``LOCAL_RUN_ERROR``, as
    ``evaluate_project``; no handle is made), ``EVALUATION_HANDLE_LIMIT`` when the bound is reached and every handle is
    evaluating. Under a monitor, a stop while the Project is lowered returns ``stopped_reply``."""

    monitor = active_monitor()
    project = None
    refused = PREPARED_EVALUATIONS.room()
    if refused is not None:
        return refused
    try:
        project = configured_project(document, None)
        session = EvaluationSession()
        with monitored(monitor), phase("lower"):
            lowered = session.lowering(project, check_solids=False)
    except LocalRunError as exc:
        return _error(exc.problems)
    except RunStopped as stopped:
        return stopped_reply(stop_run(stopped, project, monitor))
    except KeyboardInterrupt:
        if monitor is None:
            raise
        return stopped_reply(stop_run(monitor.stopped_by_interrupt(), project, monitor))
    entry, notices, refused = PREPARED_EVALUATIONS.add(session, project)
    if refused is not None:
        return refused
    return {"ok": True, "handle": entry.handle, "project_hash": project.content_hash(), "engine_input_hash": lowered.engine_input_hash,
            "catalog_version": lowered.catalog_version, "max_handles": PREPARED_EVALUATIONS.max_handles,
            "handles": len(PREPARED_EVALUATIONS.listing()), "notices": notices}


@api_method("run")
def evaluate_prepared(handle: Any, outputs: Any = None, exclude: Any = None, targets: Any = None, view_settings: Any = None,
                      document: Any = None) -> dict[str, Any]:
    """A selective evaluation through a prepared handle (``prepare_evaluation``): ``outputs``, ``exclude``, ``targets`` and
    ``view_settings`` as for ``evaluate_project`` (its ``optical_scene`` when ``view`` is computed); ``document``: a candidate Project to evaluate instead of the handle's current one; once
    evaluated it is the handle's current one (default: the Project the handle was prepared with, or the last candidate it
    evaluated). The reply is
    ``evaluate_project``'s reply for that document and selection -- the same values and hashes -- with ``handle``,
    ``reuse`` (``executed``: the capabilities this call ran; ``reused``: those whose retained products it read) and
    ``notices``. Refusals: those of ``evaluate_project`` (a malformed ``outputs``, ``exclude`` or ``targets`` too), each with
    ``handle`` and ``notices``; ``EVALUATION_HANDLE_UNKNOWN`` for a handle this runtime does not hold (released, evicted,
    never issued here: ``context.reason``; checked first) -- prepare the Project again;
    ``EVALUATION_HANDLE_BUSY`` while another evaluation of the handle is in progress. ``cancel_evaluation(handle)`` stops
    this evaluation (``stopped_reply``); the handle keeps what finished before the stop. A monitored call that completes
    also returns ``progress``."""

    host = active_monitor()
    monitor = host if host is not None else RunMonitor(preflight=False)
    entry = PREPARED_EVALUATIONS.acquire(handle, monitor)
    if isinstance(entry, dict):
        return entry
    project, facts, evaluated = entry.project, {}, False
    try:
        # (after the handle: an unknown one is refused as such whatever the selection; a malformed selection is refused
        # with the handle, which stays usable)
        try:
            names = _names(outputs, "outputs")
            excluded = _names(exclude, "exclude") or ()
            selected = _names(targets, "targets")
        except ValueError as exc:
            reply = _input_invalid(str(exc))
        else:
            try:
                settings = _view_settings(view_settings)
                if document is not None:
                    project = configured_project(document, None)
                with monitored(monitor):
                    evaluation = entry.session.evaluation(project, names if names is not None else ("readings",), excluded,
                                                          selected, view_settings=settings, facts=facts)
                    with phase("reply"):
                        reply = _evaluation_reply(evaluation)
                entry.project, evaluated = project, True
            except LocalRunError as exc:
                reply = _error(exc.problems)
            except EvaluationPlanError as exc:
                reply = _plan_error(exc)
            except RunStopped as stopped:
                reply = stopped_reply(stop_run(stopped, project, monitor, engine_input_hash=facts.get("engine_input_hash")))
            except KeyboardInterrupt:
                if host is None:
                    raise
                reply = stopped_reply(stop_run(monitor.stopped_by_interrupt(), project, monitor,
                                               engine_input_hash=facts.get("engine_input_hash")))
    finally:
        notices = PREPARED_EVALUATIONS.finish(entry, evaluated=evaluated)
    if evaluated:
        reply = {**reply, "reuse": {"executed": list(evaluation.executed), "reused": list(evaluation.reused)},
                 **({"progress": host.summary()} if host is not None and hasattr(host, "summary") else {})}
    return {**reply, "handle": entry.handle, "notices": notices}


@api_method("run")
def cancel_evaluation(handle: Any) -> dict[str, Any]:
    """Stop the evaluation in progress on a prepared handle, at its next checkpoint (its ``evaluate_prepared`` call
    answers ``stopped_reply``; the evaluations of other handles go on): ``{"ok": True, "handle", "cancelled"}``
    (``cancelled`` false when none was in progress), or ``EVALUATION_HANDLE_UNKNOWN``."""

    return PREPARED_EVALUATIONS.cancel(handle)


@api_method("run")
def release_evaluation(handle: Any) -> dict[str, Any]:
    """Forget a prepared handle and what its session holds (an evaluation in progress on it is stopped): ``{"ok": True,
    "handle", "released": True, "cancelled"}``, or ``EVALUATION_HANDLE_UNKNOWN``."""

    return PREPARED_EVALUATIONS.release(handle)


@api_method("run")
def evaluation_handles(max_handles: Any = None) -> dict[str, Any]:
    """The prepared handles this runtime holds, least recently used first (``handle``, ``project_hash``, ``evaluations``,
    ``evaluating``), and their bound: ``{"ok": True, "max_handles", "default_max_handles", "handles", "notices"}``.
    ``max_handles`` (a whole number from 1 to ``MAX_HANDLES_LIMIT``) sets the bound for this runtime; the least recently
    used idle handles beyond it are evicted (``notices``), and a handle evaluating is evicted when its evaluation ends if
    the bound still requires it. Another value gives ``INPUT_INVALID``."""

    notices: list[dict[str, Any]] = []
    if max_handles is not None:
        if not isinstance(max_handles, int) or isinstance(max_handles, bool) or not 1 <= max_handles <= MAX_HANDLES_LIMIT:
            return _input_invalid(f"max_handles is a whole number from 1 to {MAX_HANDLES_LIMIT}, not {max_handles!r}")
        notices = PREPARED_EVALUATIONS.set_limit(max_handles)
    return {"ok": True, "max_handles": PREPARED_EVALUATIONS.max_handles, "default_max_handles": DEFAULT_MAX_HANDLES,
            "handles": PREPARED_EVALUATIONS.listing(), "notices": notices}


def _configured_reply(run: ConfiguredProjectRun, files: dict[str, bytes], verify: list[str] | None) -> dict[str, Any]:
    configurations = []
    for c in run.configurations:
        row: dict[str, Any] = {"configuration_id": c.configuration_id, "label": c.label, "directory": c.directory,
                               "engine_input_hash": c.engine_input_hash, "execution": c.execution,
                               "problems": [p.as_dict() for p in c.problems]}
        if c.run is not None:
            row.update(status=c.run.status.value, summary=run_summary(c.run), local_validation=c.run.local_validation.payload(),
                       optical_scene=c.run.optical_view.payload())
        configurations.append(row)
    return {
        "ok": True,
        "project_hash": run.project.content_hash(),
        "engine_input_hash": run.engine_input_hash,
        "execution": run.summary()["execution"],
        "configurations": configurations,
        "comparison": run.comparison.model_dump(mode="json", by_alias=True),
        "manifest": json.loads(files[MANIFEST_FILE]),
        "files": files,
        "verify": verify,
    }


@api_method("project")
def update_project_catalog(document: Any, include_changed: bool = False) -> dict[str, Any]:
    """Re-pin a Project's catalog components to the active catalog where their product is unchanged
    (``os_optics.local.update_project_catalog``): ``{"ok": True, "status", "summary", "catalog_version", "project",
    "project_hash", "repinned", "changed", "withdrawn", "unverified", "old_releases", "next_steps"}``; ``status`` is
    ``CURRENT``, ``UPDATED``, ``UPDATED_UNVERIFIED`` (re-pinned with changed or uncompared products: run the design again
    and review it) or ``NEEDS_ATTENTION``."""

    try:
        update = _update_project_catalog(document, include_changed=bool(include_changed))
    except LocalRunError as exc:
        return _error(exc.problems)
    return {"ok": True, **update.as_dict(), "project": update.project.payload()}


#: Problems a refused plan call lists at most.
MAX_PLAN_PROBLEMS = 20


def _plan_schema_kinds() -> tuple[str, ...]:
    from ..assembly_plan.schemas import PLAN_SCHEMA_KINDS  # lazy: the plan contracts load on first use

    return PLAN_SCHEMA_KINDS


@api_method("runtime")
def json_schema(kind: str) -> dict[str, Any]:
    """The JSON Schema of a contract: ``project``, ``local-validation``, ``catalog``, ``optical-scene``,
    ``view-settings``, ``optical-workspace``, ``assembly-validation``, ``linear-optical-network``, ``pupil-field-request``, ``focal-field-request``, ``imaging-request``, ``ghost-request``, ``field-request``, ``analysis-result`` or ``detector-model``."""

    if kind == NETWORK_SCHEMA_KIND:
        return {"ok": True, "kind": kind, "schema": LinearOpticalNetworkV1.model_json_schema(by_alias=True)}
    if kind in _plan_schema_kinds():
        from ..assembly_plan.schemas import plan_schema

        return {"ok": True, "kind": kind, "schema": plan_schema(kind)}
    if kind == ASSEMBLY_SCHEMA_KIND:
        from ..assembly import AssemblyLocalValidationV1  # lazy: os_optics.assembly imports this package

        return {"ok": True, "kind": kind, "schema": AssemblyLocalValidationV1.model_json_schema(by_alias=True)}
    if kind in DRAWING_SCHEMA_KINDS:
        from .._vendor.shared_contracts.assembly_detail import AssemblyDetailPackageV1, AssemblyDrawingManifestV1, AssemblyDrawingResultV1

        drawing_model = {"assembly-detail": AssemblyDetailPackageV1, "assembly-drawing-result": AssemblyDrawingResultV1,
                         "assembly-drawing-manifest": AssemblyDrawingManifestV1}[kind]
        return {"ok": True, "kind": kind, "schema": drawing_model.model_json_schema(by_alias=True)}
    model = SCHEMA_MODELS.get(kind)
    if model is None:
        known = sorted([*SCHEMA_MODELS, ASSEMBLY_SCHEMA_KIND, *DRAWING_SCHEMA_KINDS, NETWORK_SCHEMA_KIND, *_plan_schema_kinds()])
        return _error([LocalRunProblem(LocalRunErrorCode.PROJECT_INVALID, f"unknown schema kind {kind!r}; known: {known}")])
    return {"ok": True, "kind": kind, "schema": model.model_json_schema(by_alias=True)}


@api_method("assembly", file_map="package")
def validate_assembly_package(package: Any, options: Any = None) -> dict[str, Any]:
    """Verify an Assembly Preview Package, then run the local layout and collision checks
    (``os_optics.assembly.validate_assembly_layout``).

    ``package`` is the archive ``bytes`` or a ``{path: bytes}`` file map (the bytes of an opened delivery);
    ``options``: ``{"tolerances": {name: value}}`` (optional): thresholds of the check other than the defaults, e.g.
    ``max_total_triangles`` (the work limit); the values used are recorded in ``result.tolerances``.
    Returns ``status``, ``result`` (the ``AssemblyLocalValidationV1`` JSON document: checks, issues with part refs
    and fixed messages, per-part status, summary) and ``result_file`` (its canonical file bytes).
    """

    from ..assembly import AssemblyPreviewPackageError, result_file_bytes, tolerances_with, validate_assembly_layout

    options = {} if options is None else options
    if not isinstance(options, dict) or set(options) - {"tolerances"}:
        return {"ok": False, "error": {"code": "INPUT_INVALID", "problems": [
            {"code": "INPUT_INVALID", "message": "options is a dict of ['tolerances']"}]}}
    try:
        tolerances = tolerances_with(options.get("tolerances"))
    except ValueError as exc:
        return {"ok": False, "error": {"code": "INPUT_INVALID", "problems": [{"code": "INPUT_INVALID", "message": str(exc)}]}}

    if isinstance(package, memoryview):
        package = package.tobytes()
    if not isinstance(package, (bytes, bytearray, dict)):  # never a path: a host passes the bytes it holds
        return {"ok": False, "error": {"code": ASSEMBLY_PACKAGE_INVALID, "problems": [
            {"code": ASSEMBLY_PACKAGE_INVALID, "message": "a package is the archive bytes or a {path: bytes} file map"}]}}
    try:
        result = validate_assembly_layout(package, tolerances=tolerances)
    except AssemblyPreviewPackageError as exc:
        return {"ok": False, "error": {"code": ASSEMBLY_PACKAGE_INVALID,
                                       "problems": [{"code": ASSEMBLY_PACKAGE_INVALID, "message": p} for p in exc.problems]}}
    except (TypeError, ValueError) as exc:
        return {"ok": False, "error": {"code": ASSEMBLY_PACKAGE_INVALID,
                                       "problems": [{"code": ASSEMBLY_PACKAGE_INVALID, "message": f"not a package: {type(exc).__name__}"}]}}
    return {"ok": True, "status": result.status.value, "result": result.payload(), "result_file": result_file_bytes(result)}


# ---------------------------------------------------------------------------------------------- assembly drawing


NESTED_TOO_DEEPLY = "an input document is nested too deeply"


def _drawing_error(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "problems": [{"code": code, "message": message}]}}


def _file_map(files: Any, what: str, *, bytes_only: bool = True) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """``(files, error)``: a host's ``{file name: bytes}`` map with text keys and its memory views as bytes, or the
    ``INPUT_INVALID`` reply when it is no map (``bytes_only``: or holds a value that is not bytes). ``what`` names the map
    in the message."""

    if not isinstance(files, dict):
        return {}, _drawing_error("INPUT_INVALID", what)
    files = {str(name): _binary(data) for name, data in files.items()}
    if bytes_only and not all(isinstance(data, (bytes, bytearray)) for data in files.values()):
        return {}, _drawing_error("INPUT_INVALID", what)
    return files, None


def _drawing_inputs(package: Any, detail: Any) -> tuple[Any, Any, dict[str, Any] | None]:
    """``(package, detail, error)``: the bytes forms a host may pass (never a filesystem path)."""

    package, detail = _binary(package), _binary(detail)
    if isinstance(package, dict):
        package = {str(k): _binary(v) for k, v in package.items()}
        if not all(isinstance(v, (bytes, bytearray)) for v in package.values()):
            return None, None, _drawing_error("INPUT_INVALID", "a package file map holds bytes only")
    elif not isinstance(package, (bytes, bytearray)):
        return None, None, _drawing_error("INPUT_INVALID", "a package is the archive bytes or a {path: bytes} file map")
    if not isinstance(detail, (bytes, bytearray)):
        return None, None, _drawing_error("INPUT_INVALID", "a detail package is its canonical file bytes")
    return package, bytes(detail), None


@api_method("assembly", file_map="package", data="detail")
def verify_assembly_detail_package(package: Any, detail: Any) -> dict[str, Any]:
    """Verify an Assembly Detail Package against its Assembly Preview Package (both as bytes; the package also as a
    ``{path: bytes}`` map). Returns the bindings and every dimension measured from the preview poses."""

    from ..assembly_drawing import AssemblyDrawingError, load_assembly_detail_package

    package, detail, error = _drawing_inputs(package, detail)
    if error is not None:
        return error
    try:
        verified = load_assembly_detail_package(detail, package)
    except AssemblyDrawingError as exc:
        return {"ok": False, "error": exc.as_dict()}
    except RecursionError:  # pragma: no cover - the loader converts every nesting failure to an AssemblyDrawingError
        return _drawing_error("INPUT_INVALID", NESTED_TOO_DEEPLY)
    doc = verified.detail
    return {
        "ok": True,
        "detail_content_hash": doc.detail_content_hash,
        "preview_package_content_hash": verified.preview.content_hash,
        "preview_package_revision": verified.preview.manifest.package_revision,
        "commercial_resolution_id": doc.binding.commercial_resolution_id,
        "release": doc.release.model_dump(mode="json", by_alias=True, exclude_none=True),
        "dimensions": [{"dim_ref": d.dim_ref, "kind": d.kind.value, "subject_part_ref": d.subject_part_ref, "axis": d.axis.value,
                        "basis": d.basis.value, "value_mm": verified.measured[d.dim_ref].value_mm} for d in doc.dimensions],
    }


@api_method("assembly", file_map="package", data="detail")
def generate_assembly_drawing(package: Any, detail: Any, options: Any = None) -> dict[str, Any]:
    """The local Detailed Assembly Drawing (``os_optics.assembly_drawing.generate_assembly_drawing``).

    ``package``: the Assembly Preview Package archive bytes or ``{path: bytes}`` map; ``detail``: the Assembly Detail
    Package bytes (the bytes of an opened delivery); ``options``: ``{"diagnostic": bool, "expected_preview_content_hash":
    str, "expected_detail_content_hash": str, "locale": "en" | "zh-CN", "design_bom": {...}}`` (all optional; ``locale`` is
    the language of the sheet, default ``en``: pass the host's UI language; ``design_bom``: the design BOM a local plan's
    package binds, its parts legend -- required for such a package). A check that FAILED or was not completed is refused
    (``DRAWING_REFUSED_VALIDATION_FAILED`` / ``DRAWING_REFUSED_VALIDATION_INCONCLUSIVE``) unless ``diagnostic`` is true.
    Returns ``drawing_status`` (``DETAILED_ASSEMBLY_DRAWING``, or with ``diagnostic`` ``DIAGNOSTIC_VALIDATION_FAILED`` /
    ``DIAGNOSTIC_VALIDATION_INCOMPLETE``), ``delivery_ready``, ``validation_status``, ``result`` (``AssemblyDrawingResultV1``), ``manifest``
    (``AssemblyDrawingManifestV1``) and ``files``: ``assembly_drawing.svg`` / ``assembly_drawing.pdf`` /
    ``drawing_result.json`` / ``drawing_manifest.json`` -> ``bytes``."""

    from ..assembly_drawing import AssemblyDrawingError
    from ..assembly_drawing import generate_assembly_drawing as generate

    options = {} if options is None else options
    if not isinstance(options, dict) or set(options) - DRAWING_OPTIONS or not isinstance(options.get("diagnostic", False), bool):
        return _drawing_error("INPUT_INVALID", f"options is a dict of {sorted(DRAWING_OPTIONS)}; diagnostic is a bool")
    if options.get("locale", "en") not in DRAWING_LOCALES:
        return _drawing_error("INPUT_INVALID", f"locale is one of {', '.join(DRAWING_LOCALES)}")
    package, detail, error = _drawing_inputs(package, detail)
    if error is not None:
        return error
    try:
        drawing = generate(package, detail, diagnostic=options.get("diagnostic", False),
                           expected_preview_content_hash=options.get("expected_preview_content_hash"),
                           expected_detail_content_hash=options.get("expected_detail_content_hash"),
                           locale=options.get("locale", "en"), design_bom=options.get("design_bom"))
    except AssemblyDrawingError as exc:
        return {"ok": False, "error": exc.as_dict()}
    except RecursionError:  # pragma: no cover - the generator converts every nesting failure to an AssemblyDrawingError
        return _drawing_error("INPUT_INVALID", NESTED_TOO_DEEPLY)
    return {
        "ok": True,
        "drawing_status": drawing.result.drawing_status.value,
        "delivery_ready": drawing.result.delivery_ready,
        "locale": drawing.result.locale,
        "validation_status": drawing.result.validation.status.value,
        "result": drawing.result.payload(),
        "manifest": drawing.manifest.payload(),
        "files": dict(drawing.files),
    }


@api_method("assembly", file_map="files")
def verify_assembly_drawing(files: Any) -> dict[str, Any]:
    """Re-check a drawing output set given as ``{name: bytes}``: ``{"ok": True, "valid": bool, "problems": [...]}``."""

    from ..assembly_drawing import verify_assembly_drawing as verify

    files, error = _file_map(files, "drawing outputs are a {name: bytes} map", bytes_only=False)
    if error is not None:
        return error
    try:
        problems = list(verify(files))
    except RecursionError:  # pragma: no cover - verify_assembly_drawing never raises on malformed outputs
        problems = [NESTED_TOO_DEEPLY]
    return {"ok": True, "valid": not problems, "problems": problems}


# ---------------------------------------------------------------------------------------------- local assembly plans


def _plan_error(exc: Exception) -> dict[str, Any]:
    """The reply of a refused plan call (``AssemblyPlanError`` / ``MechanicalCatalogError`` / a malformed document)."""

    if isinstance(exc, ValidationError):
        problems = [{"code": "INPUT_INVALID", "message": f"{'.'.join(str(p) for p in e.get('loc', ()))}: {e.get('msg', '')}"}
                    for e in exc.errors()[:MAX_PLAN_PROBLEMS]]
        return {"ok": False, "error": {"code": "INPUT_INVALID", "message": "the document is not valid", "problems": problems}}
    return {"ok": False, "error": exc.as_dict()}  # type: ignore[attr-defined]


def _plan_sources(mechanical: Any) -> Any:
    """The ``MechanicalSources`` of a host's documents (each a JSON object, JSON text or bytes)."""

    from .._vendor.shared_contracts.common.hashes import canonicalize_osc1_bytes
    from ..assembly_plan.mechanical import MechanicalSources

    documents = mechanical if isinstance(mechanical, (list, tuple)) else [mechanical]
    data = []
    for doc in documents:
        doc = _binary(doc)
        if isinstance(doc, dict):
            data.append(canonicalize_osc1_bytes(doc))
        elif isinstance(doc, str):
            data.append(doc.encode("utf-8"))
        else:
            data.append(bytes(doc))
    return MechanicalSources.of(*data).require()


def _plan_inputs(plan: Any, project: Any, mechanical: Any, catalog: Any = None) -> tuple[Any, Any, Any]:
    """``(revision, project, context)`` of a host's plan revision, its Project and the mechanical documents."""

    from ..assembly_plan.context import plan_context
    from ..assembly_plan.contract import AssemblyPlanRevisionV1

    raw = _binary(plan)
    revision = AssemblyPlanRevisionV1.model_validate(json.loads(raw) if isinstance(raw, (bytes, bytearray, str)) else raw)
    document = load_project(project)
    optical = load_public_catalog(catalog, catalog_version=revision.bindings.optical_catalog.catalog_version)
    return revision, document, plan_context(document, optical, _plan_sources(mechanical), revision=revision)


def _plan_record(run: Any) -> Any:
    from ..assembly_plan.workflow import RUN_RECORD_FILE, read_run_record

    if run is None:
        return None
    if isinstance(run, dict) and RUN_RECORD_FILE in run:
        return read_run_record(json.loads(_binary(run[RUN_RECORD_FILE])))
    return read_run_record(run)


def _plan_reply(revision: Any, check: Any) -> dict[str, Any]:
    return {"findings": [f.model_dump(mode="json", exclude_none=True) for f in revision.findings],
            "status": revision.status.model_dump(mode="json", exclude_none=True),
            "layout_validation": None if check is None or check.validation is None else check.validation.payload()}


@api_method("assembly")
def derive_assembly_plan(project: Any, mechanical: Any, options: Any = None, run: Any = None, *, catalog: Any = None) -> dict[str, Any]:
    """Derive a local assembly plan of a Project in memory (``os_optics.assembly_plan.derive_plan``): ``mechanical`` is the
    list of public mechanical catalog documents to use (one OPENSCI document, optional USER documents); ``options`` the
    derive options (``max_board_size_mm``, ``beam_height_mm``, ``customer_table``, ``customer_owned``, ``time_limit_s``);
    ``run`` a run's Local Validation record (or a run's ``{file name: bytes}`` with ``local_validation.json``) to bind.
    Returns ``{"ok", "plan" (revision 1), "project", "findings", "status", "layout_validation"}``."""

    from ..assembly_plan import AssemblyPlanError, MechanicalCatalogError
    from ..assembly_plan.workflow import derive_plan

    try:
        result = derive_plan(project, mechanical=_plan_sources(mechanical), catalog=catalog, options=dict(options or {}),
                             run=_plan_record(run))
    except (AssemblyPlanError, MechanicalCatalogError, ValidationError) as exc:
        return _plan_error(exc)
    except LocalRunError as exc:
        return _error(exc.problems)
    return {"ok": True, "plan": result.revision.payload(), "project": result.project.payload(),
            **_plan_reply(result.revision, result.check)}


@api_method("assembly")
def check_assembly_plan(plan: Any, project: Any, mechanical: Any, options: Any = None, *, catalog: Any = None) -> dict[str, Any]:
    """Check a plan revision with this Engine: ``options``: ``{"run": record}`` (optional, the run to judge the optics
    freshness against). Returns ``{"ok", "findings", "status", "layout_validation"}``."""

    from ..assembly_plan import AssemblyPlanError, MechanicalCatalogError
    from ..assembly_plan.check import check_content

    try:
        revision, _, context = _plan_inputs(plan, project, mechanical, catalog)
        check = check_content(revision, context, derive_findings=[f for f in revision.findings if f.source.value == "DERIVE"],
                              run=_plan_record((options or {}).get("run")))
    except (AssemblyPlanError, MechanicalCatalogError, ValidationError) as exc:
        return _plan_error(exc)
    except LocalRunError as exc:
        return _error(exc.problems)
    return {"ok": True, **_plan_reply(check.revision(revision), check)}


@api_method("assembly")
def apply_assembly_plan_ops(plan: Any, project: Any, ops: Any, mechanical: Any, options: Any = None, *,
                            catalog: Any = None) -> dict[str, Any]:
    """Apply plan ops to a revision (``revert_to`` needs a plan directory): ``options``: ``{"require_check": bool}``.
    Returns ``{"ok", "plan" (the next revision), "project", "project_changed", "op_results", "findings", "status",
    "layout_validation"}``."""

    from ..assembly_plan import AssemblyPlanError, MechanicalCatalogError
    from ..assembly_plan.workflow import apply_plan_ops

    try:
        revision, document, context = _plan_inputs(plan, project, mechanical, catalog)
        nxt, edited, results, check = apply_plan_ops(revision, document, list(ops or ()), context=context,
                                                     require_check=bool((options or {}).get("require_check", False)))
    except (AssemblyPlanError, MechanicalCatalogError, ValidationError) as exc:
        return _plan_error(exc)
    except LocalRunError as exc:
        return _error(exc.problems)
    return {"ok": True, "plan": nxt.payload(), "project": edited.payload(),
            "project_changed": edited.content_hash() != document.content_hash(),
            "op_results": [r.model_dump(mode="json") for r in results], **_plan_reply(nxt, check)}


@api_method("assembly")
def assembly_plan_preview(plan: Any, project: Any, mechanical: Any, *, catalog: Any = None) -> dict[str, Any]:
    """The LOCAL_PLAN Assembly Preview Package of a plan revision and its layout check: ``{"ok", "manifest", "validation",
    "files": {path: bytes}}`` (refused for a plan holding the user's own hardware)."""

    from ..assembly import validate_assembly_layout
    from ..assembly_plan import AssemblyPlanError, MechanicalCatalogError
    from ..assembly_plan.projection import project_plan_package

    try:
        revision, _, context = _plan_inputs(plan, project, mechanical, catalog)
        package = project_plan_package(revision, context)
    except (AssemblyPlanError, MechanicalCatalogError, ValidationError) as exc:
        return _plan_error(exc)
    except LocalRunError as exc:
        return _error(exc.problems)
    validation = validate_assembly_layout(package)
    return {"ok": True, "manifest": package.manifest.model_dump(mode="json", by_alias=True, exclude_none=True),
            "validation": validation.payload(), "files": dict(package.files)}


@api_method("assembly")
def assembly_plan_drawing(plan: Any, project: Any, mechanical: Any, options: Any = None, *, catalog: Any = None) -> dict[str, Any]:
    """The assembly drawing of your design (drawing 4.2) of a plan revision: ``options``: ``{"locale": "en" | "zh-CN",
    "diagnostic": bool}``. Returns ``{"ok", "status", "files"}`` (the four drawing files, ``preview.zip`` and
    ``assembly-detail.json``)."""

    from ..assembly_drawing import AssemblyDrawingError
    from ..assembly_plan import AssemblyPlanError, MechanicalCatalogError
    from ..assembly_plan.check import check_content
    from ..assembly_plan.drawing import plan_drawing

    options = dict(options or {})
    if set(options) - {"locale", "diagnostic"} or options.get("locale", "en") not in DRAWING_LOCALES:
        return _drawing_error("INPUT_INVALID", f"options is a dict of ['diagnostic', 'locale']; locale is one of {', '.join(DRAWING_LOCALES)}")
    try:
        revision, _, context = _plan_inputs(plan, project, mechanical, catalog)
        check = check_content(revision, context, derive_findings=[f for f in revision.findings if f.source.value == "DERIVE"])
        outcome = plan_drawing(revision, None, context=context, locale=options.get("locale", "en"),
                               diagnostic=bool(options.get("diagnostic", False)), findings=check.findings, validation=check.validation)
    except (AssemblyPlanError, MechanicalCatalogError, ValidationError) as exc:
        return _plan_error(exc)
    except AssemblyDrawingError as exc:
        return {"ok": False, "error": exc.as_dict()}
    except LocalRunError as exc:
        return _error(exc.problems)
    return {"ok": True, "status": outcome.summary(), "files": outcome.files()}


@api_method("assembly")
def assembly_plan_bom(plan: Any, options: Any = None) -> dict[str, Any]:
    """The design BOM (default) or, with ``options {"purchase": true, "schema_version": "2" | "1"}``, the purchase BOM of a
    plan revision: ``{"ok", "bom", "report"}`` (the purchase BOM is the body ``order quote`` sends; ``report`` is null for
    a design BOM)."""

    from ..assembly_plan import AssemblyPlanError
    from ..assembly_plan.bom import design_bom, purchase_bom
    from ..assembly_plan.contract import AssemblyPlanRevisionV1

    options = dict(options or {})
    try:
        revision = AssemblyPlanRevisionV1.model_validate(plan)
        if options.get("purchase"):
            bom, report = purchase_bom(revision, schema_version=str(options.get("schema_version", "2")))
        else:
            bom, report = design_bom(revision), None
    except (AssemblyPlanError, ValidationError) as exc:
        return _plan_error(exc)
    except ValueError as exc:
        return _drawing_error("INPUT_INVALID", str(exc))
    return {"ok": True, "bom": bom, "report": report}


@api_method("assembly")
def mechanical_catalog(mechanical: Any, options: Any = None) -> dict[str, Any]:
    """The identities of public mechanical catalog documents: ``options``: ``{"kind": POST | POST_HOLDER | MOUNT |
    BREADBOARD, "sku": ...}`` (optional filters). Returns ``{"ok", "documents" (publisher, version, sha256), "identities"}``."""

    from ..assembly_plan import MechanicalCatalogError

    options = dict(options or {})
    try:
        sources = _plan_sources(mechanical)
    except MechanicalCatalogError as exc:
        return _plan_error(exc)
    identities = [i for i in sources.identities() if options.get("kind") in (None, i.kind) and options.get("sku") in (None, i.opensci_sku)]
    return {"ok": True, "documents": [r.model_dump(mode="json") for r in sources.binding()],
            "identities": [i.payload() for i in identities]}


# ---------------------------------------------------------------------------------------------- optical layout


@api_method("commerce")
def generate_optical_layout(package: Any, locale: Any = "en") -> dict[str, Any]:
    """The Optical Layout Drawing of an optics-only purchase (``os_optics.optical_layout.generate_optical_layout``).

    ``package``: the Optical Layout Package as the service returned it (a JSON object, or its bytes / text);
    ``locale``: ``en`` or ``zh-CN``. Returns ``{"ok": True, "locale", "sheet", "manifest", "files":
    {"optical_layout.svg" | "optical_layout.pdf" | "optical_layout_manifest.json": bytes}}``; a refusal gives
    ``{"ok": False, "error": {"code": <OpticalLayoutErrorCode>, "problems": [...]}}``."""

    from ..optical_layout import OpticalLayoutError
    from ..optical_layout import generate_optical_layout as generate

    if locale not in DRAWING_LOCALES:
        return _drawing_error("INPUT_INVALID", f"locale is one of {', '.join(DRAWING_LOCALES)}")
    package = _binary(package)
    if not isinstance(package, (dict, bytes, bytearray, str)):
        return _drawing_error("INPUT_INVALID", "a package is its JSON object or bytes")
    try:
        drawing = generate(package, locale=locale)
    except OpticalLayoutError as exc:
        return {"ok": False, "error": exc.as_dict()}
    except RecursionError:  # pragma: no cover - the loader converts every nesting failure
        return _drawing_error("INPUT_INVALID", NESTED_TOO_DEEPLY)
    return {"ok": True, "locale": drawing.locale, "sheet": drawing.manifest.sheet.value,
            "manifest": drawing.manifest.payload(), "files": dict(drawing.files)}


# ---------------------------------------------------------------------------------------------- commercial submission


INPUT_INVALID = "INPUT_INVALID"
RUN_FILES_SHAPE = "run files are a {file name: bytes} map"


@api_method("commerce", file_map="run_files")
def commercial_submission(run_files: Any, customer_constraints: Any = None) -> dict[str, Any]:
    """The commercial submission of a run the host holds in memory -- what ``OpenSciClient.submit`` sends for the same
    run directory, for a headless host that submits its own verified run files.

    ``run_files``: the run's ``{file name: bytes}`` (the ``files`` of ``run_project``); they are checked like a run
    directory (``verify_run_files``: hashes, the Project <-> Local Validation binding, one Engine execution) before
    anything is built. It needs ``manifest.json``, ``project.json``, ``engine_request.json``, ``simulation_result.json``
    and ``local_validation.json``; the optical scene, its GLB and the ray data may be left out (checked when given). ``customer_constraints`` (optional): a JSON object (``max_board_size_mm``,
    ``require_all_retained_dofs``, ``include_breadboard``, ``assembly_service``, ``customer_supplied_components``,
    ``purchase_scope``, ``spare_units``, ``mounting``) the service checks; ``purchase_scope`` / ``include_breadboard`` /
    ``assembly_service`` encode the order options mechanical supply / platform / assembly, and ``mounting``
    (``{instance_id: {"beam_height_mm", "incidence_deg"?}}``) gives the beam heights of an ``OPTICAL_SYSTEM`` design
    bought with OpenSci mounts and no platform.

    Returns ``{"ok": True, "idempotency_key", "project_hash", "files": {"submission.json": bytes}}``: the request body
    of ``POST /api/v1/commercial-submissions`` (``canonical_json_bytes`` of ``submission_document``) and its
    ``Idempotency-Key`` (``idempotency_key_for("submit", body)``), so the same run always gets the same resolution. Run
    files that do not verify give ``{"ok": False, "error": {"code": "RUN_DIRECTORY_INCONSISTENT", "problems":
    [{"code", "message"}]}}`` (the files of a run of a Project with configurations: the code
    ``CONFIGURATIONS_NOT_SUBMITTABLE``; one configuration's run files are submitted instead); run files that are not a ``{name: bytes}`` map, or constraints that are not a JSON
    object, give the code ``INPUT_INVALID``. Nothing is sent anywhere: this only builds bytes."""

    files, error = _file_map(run_files, RUN_FILES_SHAPE)
    if error is not None:
        return error
    if customer_constraints is not None and not isinstance(customer_constraints, dict):
        return _drawing_error(INPUT_INVALID, "customer_constraints is a JSON object or None")
    try:
        document = submission_document(files, customer_constraints=customer_constraints)
    except RunDirectoryInconsistentError as exc:  # (CONFIGURATIONS_NOT_SUBMITTABLE: the run of a Project with configurations)
        return {"ok": False, "error": {"code": exc.code, "problems": [
            {"code": exc.code, "message": problem} for problem in exc.problems]}}
    except LocalRunError as exc:  # pragma: no cover - verify_run_files already parsed the Project
        return _error(exc.problems)
    body = canonical_json_bytes(document)
    return {"ok": True, "idempotency_key": idempotency_key_for("submit", body), "project_hash": document["project_hash"],
            "files": {SUBMISSION_FILE: body}}


# ---------------------------------------------------------------------------------------------- scientific results


RESULTS_QUERY_INVALID = "RESULTS_QUERY_INVALID"
RESULT_QUERIES = ("describe", "targets", "monitors", "monitor", "paths", "power_budget", "rays", "wavefront", "irradiance", "sampled_light",
                  "superpositions", "handoff", "exposures", "grating_orders", "nonlinear", "imaging", "ghosts", "field", "cavity", "columns", "export",
                  "detection", "sample_emission")
# Query fields each result query accepts (besides "kind" and "verify").
EXPORT_FILTERS = ("monitor", "path", "crossing_index", "reference", "center", "min_rays", "wavelength_nm", "half_extent_mm", "samples",
                  "element", "source_id", "center_uv_mm", "roi", "max_untraced_fraction")
RESULT_QUERY_FIELDS = {
    "describe": (), "targets": (), "monitors": (), "monitor": ("monitor",), "paths": ("path",), "power_budget": (),
    "rays": ("monitor", "path", "crossing_index", "offset", "limit"),
    "wavefront": ("monitor", "path", "crossing_index", "reference", "center", "min_rays"),
    "irradiance": ("monitor", "wavelength_nm", "half_extent_mm", "samples", "source_id", "center_uv_mm", "roi", "max_untraced_fraction"),
    "sampled_light": ("monitor",), "superpositions": ("monitor",),
    "handoff": ("monitor", "path", "crossing_index", "element"), "exposures": ("element",), "grating_orders": ("element",),
    "nonlinear": ("element",), "imaging": ("monitor", "request"),
    "ghosts": ("monitor", "request"),
    "field": ("monitor", "request"),
    "cavity": ("request",),
    "columns": ("export_kind",), "export": ("export_kind", "format", *EXPORT_FILTERS),
    "detection": ("monitor",), "sample_emission": (),
}
EXPORT_MEDIA_TYPES = {"csv": "text/csv", "jsonl": "application/jsonl", "json": "application/json", "npz": "application/octet-stream"}
#: The JSON type of a query value (``None`` = absent), checked before the query runs; ``crossing_index``, ``min_rays``,
#: ``offset`` and ``limit`` are checked where they are read. What a query raises after that on run files that verify is
#: not the caller's: ``INTERNAL_ERROR``.
INTERNAL_ERROR = "INTERNAL_ERROR"
_TEXT_FIELDS = ("monitor", "path", "element", "source_id", "reference", "export_kind", "format")
_NUMBER_FIELDS = ("wavelength_nm", "half_extent_mm", "samples")


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _query_value_problem(query: dict[str, Any]) -> str | None:
    """Why a query value has the wrong JSON type (``None``: every value has its type)."""

    for name, value in query.items():
        if value is None:
            continue
        if name in _TEXT_FIELDS and not isinstance(value, str):
            return f"{name} is a string, got {value!r}"
        if name in _NUMBER_FIELDS and not _is_number(value):
            return f"{name} is a number, got {value!r}"
        if name in ("roi", "request") and not isinstance(value, dict):
            return f"{name} is a JSON object, got {value!r}"
        if name == "center_uv_mm" and not (isinstance(value, (list, tuple)) and len(value) == 2 and all(map(_is_number, value))):
            return f"center_uv_mm is [u, v] in mm, got {value!r}"
        if name == "center" and not (isinstance(value, str) or (isinstance(value, (list, tuple)) and len(value) == len("xyz")
                                                                  and all(map(_is_number, value)))):
            return f"center is [x, y, z] in mm, 'gaussian_waist' or 'ray_focus', got {value!r}"
    return None


@api_method("results")
def metric_descriptors(include_stage_metrics: bool = False) -> dict[str, Any]:
    """Every public target metric's descriptor (unit, definition, sign convention, operands, frame, selectors, model basis,
    validity, version): ``{"ok": True, "descriptors": [...]}``."""

    chosen = _metric_descriptors(include_stage_metrics=bool(include_stage_metrics))
    return {"ok": True, "descriptors": [d.model_dump(mode="json") for d in chosen]}


def _run_query(results: Any, kind: str, query: dict[str, Any]) -> Any:
    get = query.get
    if kind == "describe":
        return results.describe()
    if kind == "targets":
        return results.targets()
    if kind == "monitors":
        return results.monitors()
    if kind == "monitor":
        return results.monitor(get("monitor"))
    if kind == "paths":
        return results.paths(get("path"))
    if kind == "power_budget":
        return results.power_budget()
    if kind == "superpositions":
        return results.superpositions(get("monitor"))
    if kind == "exposures":
        return results.exposures(get("element"))
    if kind == "grating_orders":
        return results.grating_orders(get("element"))
    if kind == "nonlinear":
        return results.nonlinear(get("element"))
    if kind == "rays":
        offset, limit = get("offset", 0), get("limit")
        paging = _paging(offset, limit)
        if paging is not None:
            raise ResultsError(RESULTS_QUERY_INVALID, paging)
        rows = results.rays(get("monitor"), get("path"), get("crossing_index"))
        return {"total": len(rows), "offset": offset, "limit": limit, "rows": rows[offset:] if limit is None else rows[offset:offset + limit]}
    if kind == "wavefront":
        options = {k: get(k) for k in ("crossing_index", "reference", "center", "min_rays") if get(k) is not None}
        return results.wavefront(get("monitor"), get("path"), **options)
    if kind == "irradiance":
        options = {k: get(k) for k in ("wavelength_nm", "half_extent_mm", "samples", "source_id", "center_uv_mm", "roi") if get(k) is not None}
        if "max_untraced_fraction" in query:  # (stated null: no limit; not stated: the default)
            options["max_untraced_fraction"] = query["max_untraced_fraction"]
        return results.irradiance_map(get("monitor"), **options)
    if kind == "sampled_light":
        return results.sampled_light(get("monitor"))
    if kind == "detection":
        return results.detection(get("monitor"))
    if kind == "sample_emission":
        return results.sample_emission()
    if kind == "handoff":
        return results.handoff(get("monitor"), get("path"), get("crossing_index"), get("element"))
    if kind == "columns":
        return results.columns(get("export_kind"))
    if kind == "imaging":
        return results.imaging(get("monitor"), get("request")).model_dump(mode="json", by_alias=True)
    if kind == "ghosts":
        return results.ghosts(get("monitor"), get("request")).model_dump(mode="json", by_alias=True)
    if kind == "field":
        return results.field(get("monitor"), get("request")).model_dump(mode="json", by_alias=True)
    if kind == "cavity":
        return results.cavity(get("request")).model_dump(mode="json", by_alias=True)
    export_kind, file_format = get("export_kind"), get("format", "csv")
    filters = {k: get(k) for k in EXPORT_FILTERS if get(k) is not None}
    if "max_untraced_fraction" in query:  # (stated null: no limit; not stated: the default -- as the irradiance query)
        filters["max_untraced_fraction"] = query["max_untraced_fraction"]
    data = results.export(export_kind, file_format, **filters)
    return {"file_name": f"{export_kind}.{file_format}", "media_type": EXPORT_MEDIA_TYPES[file_format], "data": data}


@api_method("results", file_map="files")
def run_results(files: Any, query: Any = None) -> dict[str, Any]:
    """One scientific-results query over a run's in-memory files (module docstring): ``{"ok": True, "query", "result"}``.
    Every query also takes ``configuration``: the id of a configuration of a run of a Project with configurations
    (``run_project``'s ``files``), whose run is then queried."""

    files, error = _file_map(files, RUN_FILES_SHAPE)
    if error is not None:
        return error
    query = {"kind": "describe"} if query is None else query
    if not isinstance(query, dict) or query.get("kind") not in RESULT_QUERIES:
        return _drawing_error(RESULTS_QUERY_INVALID, f"a query is {{'kind': K, ...}} with K one of {list(RESULT_QUERIES)}")
    kind = query["kind"]
    unknown = sorted(set(query) - {"kind", "verify", "configuration", *RESULT_QUERY_FIELDS[kind]})
    if unknown:
        return _drawing_error(RESULTS_QUERY_INVALID, f"query kind {kind!r} takes {list(RESULT_QUERY_FIELDS[kind])}, not {unknown}")
    if query.get("configuration") is not None:  # one configuration's run out of the files of a run of configurations
        files = configuration_files(files, str(query["configuration"]))
        if not files:
            return _drawing_error(RESULTS_QUERY_INVALID, f"the run has no run directory of configuration {query['configuration']!r}")
    problem = _query_value_problem(query)
    if problem is not None:
        return _drawing_error(RESULTS_QUERY_INVALID, f"invalid query value: {problem}")
    results = None
    try:
        results = results_from_files(files, verify=bool(query.get("verify", False)))
        result = _run_query(results, kind, query)
    except RunProjectRefused as exc:  # (the run's Project lowered again -- the cavity analysis -- refused here)
        return _error(exc.refusal.problems)
    except ResultsError as exc:
        return _drawing_error(exc.code, exc.message)
    except (TypeError, ValueError, KeyError, AttributeError, RecursionError) as exc:
        if results is not None and results.integrity_problems == ():
            # (the files verify and every query value has its type: the failure is os-optics's own)
            reply = _drawing_error(INTERNAL_ERROR, f"os-optics failed with an internal error ({type(exc).__name__}: {exc}); "
                                                   "report it with the query you sent")
            reply["error"]["type"] = type(exc).__name__
            return reply
        if isinstance(exc, (AttributeError, RecursionError)):  # run files of an unexpected structure (not verified)
            return _drawing_error("RUN_NOT_READABLE", f"the run files have an unexpected structure ({type(exc).__name__})")
        return _drawing_error(RESULTS_QUERY_INVALID, f"invalid query value: {type(exc).__name__}: {exc}")
    return {"ok": True, "query": dict(query), "result": result}


CONFIGURATIONS_RUN_INVALID = "CONFIGURATIONS_RUN_INVALID"


@api_method("results", file_map="files")
def configurations_comparison(files: Any, paraxial: Any = None) -> dict[str, Any]:
    """The comparison of a run of a Project with configurations (``os_optics.analysis.configurations``), recomputed from
    its in-memory files (``run_project``'s ``files``): ``{"ok": True, **AnalysisResultV1}``. ``paraxial`` (optional): a
    paraxial request (``{"components": [...], "wavelength_nm": W}``, as ``paraxial_analysis`` takes it) evaluated in each
    configuration (``efl_mm``, ``bfl_mm``). Files that are not a run of configurations give ``{"ok": False, "error":
    {"code": "CONFIGURATIONS_RUN_INVALID", ...}}``."""

    files, error = _file_map(files, RUN_FILES_SHAPE, bytes_only=False)
    if error is not None:
        return error
    if paraxial is not None and not isinstance(paraxial, dict):
        return _drawing_error(INPUT_INVALID, "paraxial is a JSON object or None")
    read = run_reader(files)
    if read is None:
        return _drawing_error(CONFIGURATIONS_RUN_INVALID, "the files are not a run of a Project with configurations")
    try:
        comparison = comparison_from_files(read, paraxial=paraxial)
    except LocalRunError as exc:
        return _error(exc.problems)
    except (KeyError, TypeError, ValueError, OSError, RecursionError) as exc:
        return _drawing_error(CONFIGURATIONS_RUN_INVALID, f"the run files cannot be read ({type(exc).__name__})")
    return {"ok": True, **comparison.model_dump(mode="json", by_alias=True)}


# ---------------------------------------------------------------------------------------------- optical solids / section

SECTION_INVALID = "SECTION_INVALID"
SECTION_DIGITS = 6  # member poses of a unit drawn from a Project document carry the scene's rounding


def _edge_band(edge_review_ratio: Any) -> Any:
    """The optical-solid check's edge-thickness band for a caller's ``edge_review_ratio`` (``None``: the default band)."""

    from ..optical_solids.checks import EdgeThicknessBand

    if edge_review_ratio is None:
        return None
    if isinstance(edge_review_ratio, bool) or not isinstance(edge_review_ratio, (int, float)) or not 0 < edge_review_ratio < float("inf"):
        raise LocalRunError([LocalRunProblem(LocalRunErrorCode.PROJECT_INVALID,
                                             f"edge_review_ratio must be a positive number, got {edge_review_ratio!r}")])
    return EdgeThicknessBand(review_ratio=float(edge_review_ratio))


@api_method("analysis")
def inspect_optical_solids(document: Any, *, edge_review_ratio: float | None = None) -> dict[str, Any]:
    """Are the Project's optical bodies physically possible solids (``os_optics.optical_solids``)? The same lowering a
    run performs, then -- before any tracing -- every element's profile domain over its outline, thickness between
    consecutive surfaces over the whole outline, medium chain, and every pair of placed optical bodies (overlap / touch).
    ``{"ok": True, "status": "VALID" | "INVALID", "issues": [...], "elements": [...], ...}``; a Project the lowering refuses
    gives the ``LOCAL_RUN_ERROR`` envelope. ``edge_review_ratio``: a declared model's edge thickness over its clear
    diameter above which ``EDGE_THICKNESS_IMPLAUSIBLE`` is listed as an INFO finding (default 0.35)."""

    from ..optical_solids import inspect_lowered

    try:
        band = _edge_band(edge_review_ratio)
        project = load_project(document)
        lowered = lower_project(project, check_solids=False)
    except LocalRunError as exc:
        return _error(exc.problems)
    return {"ok": True, **inspect_lowered(lowered, project, edge_band=band).as_dict()}


def _section_elements(document_or_run: Any) -> tuple[list[Any], tuple[Any, ...], tuple[Any, ...]]:
    """The elements to cut, the beam paths to project and the observation planes to draw: from a run (a run directory
    path, its ``{file name: bytes}``, a ``run_project`` reply or an ``optical_scene`` document), else from a Project
    document (no rays, no planes)."""

    from pathlib import Path

    from ..optical_view.builder import declared_body, optic_body, unit_geometry
    from ..optical_view.section import scene_section_elements, section_elements_of
    from .catalog import pinned_catalog_version

    scene_doc = None
    if isinstance(document_or_run, (str, Path)):
        scene_doc = json.loads((Path(document_or_run) / "optical_scene.json").read_bytes().decode("utf-8"))
    elif isinstance(document_or_run, dict) and "optical_scene.json" in document_or_run:
        scene_doc = json.loads(bytes(_binary(document_or_run["optical_scene.json"])).decode("utf-8"))
    elif isinstance(document_or_run, dict) and isinstance(document_or_run.get("optical_scene"), dict):
        scene_doc = document_or_run["optical_scene"]
    elif isinstance(document_or_run, dict) and document_or_run.get("schema") == "opensci.optical_simulation_scene":
        scene_doc = document_or_run
    if scene_doc is not None:
        scene = OpticalSimulationSceneV1.model_validate(scene_doc)
        return scene_section_elements(scene), tuple(scene.beam_paths), tuple(scene.observation_planes)
    project = load_project(document_or_run)
    lowered = lower_project(project, check_solids=False)
    catalog = None
    if any(c.product is not None for c in project.components):
        catalog = load_public_catalog(None, catalog_version=pinned_catalog_version(project))
    compiled = {e.instance_id: e for e in lowered.elements}
    elements = []
    for component in project.components:
        element = compiled.get(component.instance_id)
        pose = (tuple(component.nominal_pose.position_mm), tuple(component.nominal_pose.quaternion_wxyz))
        if element is None:
            geometry, label = None, component.optical_role.value
        elif element.optical_sku is not None:
            geometry, label = optic_body(catalog.get(element.optical_sku), element.model, catalog), element.label  # type: ignore[union-attr]
        elif element.is_compound:
            geometry, label = unit_geometry(element, project, SECTION_DIGITS), component.optical_model_id
        else:
            geometry, label = declared_body(element, project), component.optical_model_id
        elements.extend(section_elements_of(component.instance_id, label, *pose, geometry))  # type: ignore[arg-type]
    return elements, (), ()


@api_method("analysis")
def optical_section(document_or_run: Any, instance_id: str, plane: str = "XZ", svg: bool = False) -> dict[str, Any]:
    """The exact meridional section of element ``instance_id`` in its local ``plane`` (``"XZ"`` or ``"YZ"``) and of every
    element on its axis (``os_optics.optical_view.section``): ``{"ok": True, "section": {...}}`` with each surface's
    exact profile polyline (clear-aperture and rim spans, clear-aperture ticks), the media polygons, the elements' axial
    offsets, the observation planes and rays of a run projected onto the plane and the bounds -- numbers in millimetres,
    ready to draw at one scale on both axes; ``svg=True`` adds ``"svg"``: the same drawing as an SVG document.
    ``instance_id`` ``"ALL"``: the whole system, referred to the element the light reaches first; a folded system's other
    axes follow in ``"other_axes"``, a list of ``{"section"[, "svg"]}``. ``document_or_run``: a Project
    document (geometry only) or a run (``run_project``'s reply or ``files``, an ``optical_scene`` document, or a run
    directory path). An unknown element or plane gives ``{"ok": False, "error": {"code": "SECTION_INVALID", ...}}``."""

    from ..optical_view.section import ALL_ELEMENTS, SectionError, section_svg, system_sections
    from ..optical_view.section import optical_section as _optical_section

    others: list[dict[str, Any]] = []
    try:
        elements, paths, planes = _section_elements(document_or_run)
        if instance_id == ALL_ELEMENTS:
            section, *others = system_sections(elements, plane, beam_paths=paths, observation_planes=planes)
        else:
            section = _optical_section(elements, instance_id, plane, beam_paths=paths, observation_planes=planes)
    except LocalRunError as exc:
        return _error(exc.problems)
    except SectionError as exc:
        return _drawing_error(SECTION_INVALID, str(exc))
    except (OSError, ValueError) as exc:
        return _drawing_error(SECTION_INVALID, f"not a Project document or a run: {str(exc).splitlines()[0]}")
    reply: dict[str, Any] = {"ok": True, "section": section}
    if svg:
        reply["svg"] = section_svg(section)
    if others:
        reply["other_axes"] = [{"section": s, **({"svg": section_svg(s)} if svg else {})} for s in others]
    return reply


#: The error code of a Project whose layout cannot be drawn (``project_layout``).
LAYOUT_INVALID = "LAYOUT_INVALID"


@api_method("analysis")
def project_layout(document: Any, view_settings: Any = None) -> dict[str, Any]:
    """The layout of a Project without a run (``os_optics.optical_view.layout``): its optical elements, sources and
    observation planes at their design poses, drawn as a run's Optical Simulation View draws them, and each source's
    nominal beam axis (straight, turned at mirrors and thin lenses, refracted between two media by Snell's law at the source's
    first line, ended where its direction would need a trace: a grating, a crystal, a gradient-index medium, a material
    without an index at that wavelength). Nothing is traced: no beam path, power, result status or target result (the design
    inputs it draws keep theirs, such as a model's status and an observation plane's target ids).

    ``{"ok": True, "layout": {...}, "files": {"optical_layout.glb": bytes}}``; ``layout`` holds ``schema``
    (``opensci.optical_layout_view``), ``project`` (the Project hash), ``elements``, ``observation_planes``,
    ``axis_markers``, ``nominal_axes`` (per source: ``wavelength_nm``, ``points_mm``, ``surface_ids``, ``end`` ``FREE`` / ``ABSORBED`` /
    ``NOT_FOLLOWED`` / ``RETURNS``, ``end_surface_id``, ``display_extension_to_mm``), ``bounds`` and ``camera_hint``. The
    Project is lowered as a run lowers it (optical-solid findings do not refuse a layout; a Project without a source has
    one). A Project the lowering refuses gives the ``LOCAL_RUN_ERROR`` envelope; one whose elements cannot be drawn
    ``LAYOUT_INVALID``."""

    from ..optical_view import OpticalViewBindingError
    from ..optical_view.layout import LAYOUT_GLB_FILE, build_project_layout
    from .nominal_axes import nominal_axes

    try:
        project = load_project(document)
        settings = _view_settings(view_settings) or OpticalViewSettingsV1()
        lowered = lower_project(project, check_solids=False, require_source=False)
        catalog = load_public_catalog(None, catalog_version=lowered.catalog_version) if lowered.catalog_version is not None else None
        axes, unavailable = nominal_axes(lowered.request, settings.free_ray_display_length_mm, settings.coordinate_decimals)
        layout = build_project_layout(project=project, catalog=catalog, request=lowered.request, compiled={e.instance_id: e for e in lowered.elements},
                                      nominal_axes=axes, nominal_axes_unavailable=unavailable, settings=settings)
    except LocalRunError as exc:
        return _error(exc.problems)
    except OpticalViewBindingError as exc:
        return _drawing_error(LAYOUT_INVALID, f"the layout cannot be drawn: {str(exc).splitlines()[0]}")
    except ValidationError as exc:
        return _drawing_error(LAYOUT_INVALID, _layout_validation_message(exc))
    except (ArithmeticError, ValueError, RecursionError):
        return _drawing_error(LAYOUT_INVALID, LAYOUT_NOT_DRAWABLE)
    return {"ok": True, "layout": layout.document, "files": {LAYOUT_GLB_FILE: layout.glb}}


@api_method("analysis")
def optical_scene_glb(scene: Any) -> dict[str, Any]:
    """The glTF 2.0 binary of an Optical Simulation View scene (``OpticalSimulationSceneV1``): the ``optical_scene`` that
    ``evaluate_project`` and ``evaluate_prepared`` return when ``view`` is computed, or that ``run_project`` returns.
    ``{"ok": True, "files": {"optical_scene.glb": bytes}}``: the bytes a run directory holds for that scene. A document
    that is not such a scene: ``INPUT_INVALID``, naming what does not fit."""

    from ..optical_view.artifacts import GLB_FILE
    from ..optical_view.glb import scene_glb_bytes

    try:
        document = OpticalSimulationSceneV1.model_validate(scene)
    except ValidationError as exc:
        first = exc.errors(include_url=False)[0]
        where = ".".join(str(part) for part in first["loc"]) or "the document"
        return _input_invalid(f"scene is not an optical scene (opensci.optical_simulation_scene): {where}: {first['msg']}; "
                              "give the optical_scene of an evaluation that computed view, or of a run")
    return {"ok": True, "files": {GLB_FILE: scene_glb_bytes(document)}}


#: A layout whose drawing fails at the design's positions and sizes (e.g. parts so far apart that its numbers overflow).
LAYOUT_NOT_DRAWABLE = "the layout cannot be drawn at the positions and sizes of the design's parts; check their positions"


def _layout_validation_message(exc: ValidationError) -> str:
    """A drawn layout value that is not valid, worded without the view's internal model names."""

    errors = exc.errors()
    if errors and all(e.get("type") == "finite_number" for e in errors):
        return ("the layout cannot be drawn: the design extends too far to draw (a coordinate of the drawing is not a finite "
                "number); check the positions of its parts")
    first = str(errors[0].get("msg", "")) if errors else ""
    return f"the layout cannot be drawn: a value of the drawing is not valid ({first})" if first else LAYOUT_NOT_DRAWABLE


@api_method("modelling")
def import_prescription(text: Any, options: Any = None) -> dict[str, Any]:
    """Import a lens prescription as Project-native optical models (module docstring; ``os_optics.prescription.api``).
    Glasses and coatings the file does not define are copied from the active public optical catalog."""

    return import_prescription_json(text, options, catalog=load_public_catalog)


NATIVE_COMPONENT_INVALID = "NATIVE_COMPONENT_INVALID"


def _native_error(exc: NativeComponentError) -> dict[str, Any]:
    return {"ok": False, "error": {"code": NATIVE_COMPONENT_INVALID, "problems": [p.as_dict() for p in exc.problems]}}


@api_method("modelling")
def native_components() -> dict[str, Any]:
    """Every native component class and its parameters (module docstring) and the field models, which have no optical model of their own:
    ``{"ok": True, "classes": [...], "field_models": [...]}``."""

    return {"ok": True, "classes": _native_components(), "field_models": native_field_models()}


NATIVE_BUILD_OPTIONS = ("into", "instance_id", "position_mm", "quaternion_wxyz")


@api_method("modelling")
def build_native_model(class_id: Any, parameters: Any = None, options: Any = None) -> dict[str, Any]:
    """One native component's Project optical model, its materials / coatings and a component instance using it; with
    ``options["into"]`` (a Project document) also that Project with everything added (module docstring). Refusals list
    every problem with its parameter."""

    from ..native_library import NativeErrorCode, NativeProblem, add_to_project

    options = {} if options is None else options
    if not isinstance(options, dict) or set(options) - set(NATIVE_BUILD_OPTIONS):
        return _native_error(NativeComponentError([NativeProblem(NativeErrorCode.PARAMETER_INVALID,
                                                                 f"options is an object with keys among {list(NATIVE_BUILD_OPTIONS)}", "options")]))
    try:
        build = _build_native_model(class_id, parameters)
        reply = {"ok": True, **build.as_dict()}
        if options.get("into") is not None:
            into = options["into"]
            if isinstance(into, (str, bytes, bytearray)):
                into = json.loads(into)
            reply["project"] = add_to_project(into, build, instance_id=options.get("instance_id"), position_mm=options.get("position_mm"),
                                              quaternion_wxyz=options.get("quaternion_wxyz"))
    except NativeComponentError as exc:
        return _native_error(exc)
    except (ValueError, TypeError) as exc:  # an "into" that is not a JSON object, a pose that is not numbers
        return _native_error(NativeComponentError([NativeProblem(NativeErrorCode.PARAMETER_INVALID, f"options: {exc}", "options")]))
    return reply


# ---------------------------------------------------------------------------------------------- linear optical network


def _engine_request_document(document: Any) -> Any:
    """``document`` as a JSON object when it is an Engine request (``{"project": {...}, "configuration": ...}``), else None."""

    if isinstance(document, (bytes, bytearray)):
        document = bytes(document).decode("utf-8", errors="replace")
    if isinstance(document, str) and document.lstrip().startswith("{"):
        try:
            document = json.loads(document)
        except (ValueError, RecursionError):
            return None
    if isinstance(document, dict) and document.get("schema") != "opensci.project" and isinstance(document.get("project"), dict):
        return document
    return None


@api_method("analysis")
def linear_optical_network(document: Any, options: Any = None, *, catalog: Any = None,
                           configuration_id: str | None = None) -> dict[str, Any]:
    """The linear optical network of a Project (module docstring): ``{"ok": True, "network", "contract_hash"}``.

    ``document``: a Project document (as ``check_project`` takes it) or an Engine request; ``options``: ``{"wavelength_nm",
    "source_ids", "observation_ids", "mode_overlap_tolerance"}`` (all optional); ``catalog``: a catalog release to use instead
    of the one the Project pins (Python hosts; the browser installs its catalog with ``install_catalog``);
    ``configuration_id`` (Python hosts and ``project network --configuration``): the configuration to export, of a Project
    that declares configurations."""

    from ..network import NetworkExportError
    from .network import export_linear_optical_network, export_request_network

    try:
        request = _engine_request_document(document)
        if request is not None:
            try:
                network = export_request_network(request, options=options)
            except ValidationError as exc:
                return _error([LocalRunProblem(LocalRunErrorCode.ENGINE_INPUT_INVALID, f"not a valid Engine request: {_validation_message(exc)}")])
        else:
            network = export_linear_optical_network(document, catalog=catalog, options=options, configuration_id=configuration_id)
    except LocalRunError as exc:
        return _error(exc.problems)
    except NetworkExportError as exc:
        return {"ok": False, "error": exc.as_dict()}
    return {"ok": True, "network": network.model_dump(mode="json", by_alias=True), "contract_hash": network.contract_hash}


# ---------------------------------------------------------------------------------------------- interop catalog

INTEROP_ENTRY_UNKNOWN = "INTEROP_ENTRY_UNKNOWN"


@api_method("runtime")
def interop_catalog(entry_id: Any = None) -> dict[str, Any]:
    """The interop capability catalog (module docstring): every entry, or the one ``entry_id`` names."""

    if entry_id is None:
        return {"ok": True, **_interop_catalog()}
    try:
        return {"ok": True, "entry": _interop_entry(entry_id)}
    except InteropEntryUnknown as exc:
        return _drawing_error(INTEROP_ENTRY_UNKNOWN, exc.message)


# ---------------------------------------------------------------------------------------------- field and detector stages


@api_method("analysis", file_map="mask_files")
def pupil_field(request: Any, mask_files: Any = None, handoff: Any = None) -> dict[str, Any]:
    """The scalar low-NA focal field of a pupil-plane field and its mask (module docstring; ``os_optics.results.pupil_field``)."""

    if mask_files is not None:
        mask_files, error = _file_map(mask_files, "mask_files is a {file name: bytes} map", bytes_only=False)
        if error is not None:
            return error
    try:
        return {"ok": True, **_pupil_field(request, mask_files, handoff)}
    except PupilFieldError as exc:
        return _drawing_error(exc.code, exc.message)


@api_method("analysis")
def focal_field(request: Any, handoff: Any = None) -> dict[str, Any]:
    """The vector focal field of an ideal aplanatic objective (module docstring; ``os_optics.results.focal_field``)."""

    try:
        doc = _focal_field(request, handoff)
    except FocalFieldError as exc:
        return _drawing_error(exc.code, exc.message)
    return {"ok": True, **doc.model_dump(mode="json", by_alias=True)}


@api_method("analysis")
def detector_response(irradiance: Any, detector: Any, composition: Any = None) -> dict[str, Any]:
    """The response of a stated detector to an irradiance document (module docstring; ``os_optics.results.detector``)."""

    try:
        return {"ok": True, **_detector_response(irradiance, detector, composition)}
    except DetectorError as exc:
        return _drawing_error(exc.code, exc.message)


# ---------------------------------------------------------------------------------------------- paraxial analysis


@api_method("analysis")
def paraxial_analysis(document: Any, request: Any, *, catalog: Any = None) -> dict[str, Any]:
    """Paraxial analysis of placed lenses, mirrors and compound units (module docstring; ``os_optics.local.paraxial``)."""

    from .paraxial import ParaxialError
    from .paraxial import paraxial_analysis as analyse

    try:
        return {"ok": True, **analyse(document, request, catalog=catalog)}
    except ParaxialError as exc:
        return {"ok": False, "error": exc.as_dict()}
    except LocalRunError as exc:
        return _error(exc.problems)


@api_method("analysis")
def cavity_analysis(document: Any, request: Any, *, catalog: Any = None) -> dict[str, Any]:
    """Stability, eigenmode, Gouy phase, free spectral range, finesse and mode matching of a linear cavity (module docstring;
    ``os_optics.local.cavity``)."""

    from .cavity import CavityError
    from .cavity import cavity_analysis as analyse

    try:
        return {"ok": True, **analyse(document, request, catalog=catalog).model_dump(mode="json", by_alias=True)}
    except CavityError as exc:
        return {"ok": False, "error": exc.as_dict()}
    except LocalRunError as exc:
        return _error(exc.problems)
