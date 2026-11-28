# OS Optics (os-optics)

OS Optics is OpenSci's local optical design engine. Your Agent edits a Project through the CLI or Python API,
evaluates candidates, runs the design and reads structured results. A saved run has a local read-only 3D view and a
**Local Validation** record; design and simulation happen on your computer. When the design is ready to buy, the same
package uses a customer token from Account to resolve a priced Customer Purchase BOM and create an unpaid Order. The
person reviews the quotation, agreement and bank-transfer instructions in Account; OpenSci releases the protected
assembly detail after confirming receipt. The product site is <https://opensci-optics.com/>.

* **What it computes.** Every beam of every source line traced through the design (3D geometric optics, Gaussian beams,
  Jones polarization), and at every observation plane each arriving beam's position, direction, size, waist, phase,
  polarization and power, the plane's power with its bounds, two-beam interference and coherent sums, irradiance maps;
  the exposure of every element surface against its power ratings; and, from a run's light, the point spread function
  and MTF of an image plane (diffraction and geometric), ghost images, the vector focal field of an objective, the
  coherent field of a sampled source, paraxial spacing and optical cavities.
* **How a number states its basis.** Every quantity carries its state (`KNOWN`, `KNOWN_ZERO`, `BOUNDED`, `UNKNOWN`,
  `OUT_OF_MODEL_SCOPE`, ...). A number that is not simply the model's value -- a lower or upper bound, or an estimate
  outside a validity limit or with a stand-in for an input the design does not state -- carries a `value_basis` entry
  that says which and why. A target is decided only on what is proven.
* **Selective and settable.** `project evaluate` computes the quantities you ask for and only what they require; the
  Project's `compute_budget` and `numerical_settings` state how far the light is followed and the limits a value is
  reported under. Same input + same engine version + same settings = same result within the declared tolerance.
* Pure Python for **Python 3.11 or newer** (3.11, 3.12, 3.13, 3.14) on Windows, Linux and macOS; depends on `numpy`,
  `scipy`, `pydantic>=2` and `shapely` (the `protected` extra adds `cryptography`). No compiler, GPU or account is needed
  to design.
* You, or your own Agent, operate this package. OpenSci does not provide, host or embed an Agent; any coding Agent that
  can run Python or a shell can drive the commands and functions below. Every command prints JSON.

## Install

With **uv** (it selects or downloads a compatible Python by itself):

```bash
uv tool install os-optics
os-optics doctor
```

or run without installing: `uvx --from os-optics os-optics doctor`.

With **pip**, in a virtual environment made from Python 3.11 or newer:

```bash
python --version                                  # 3.11 or newer
python -m venv .venv
.venv/bin/python -m pip install os-optics                  # Windows: .venv\Scripts\python -m pip install ...
.venv/bin/os-optics doctor                                      # Windows: .venv\Scripts\os-optics doctor
```

If pip reports "requires a different Python" or "No matching distribution found for os-optics", the interpreter
is older than 3.11: use the uv command above, or create the environment from a newer Python.

Up to version 0.9.0 OS Optics was published as `opensci-engine` (`import opensci_engine`, command
`opensci-engine`). From 0.10.0 it is published as `os-optics`: install it with the commands above (also where
`opensci-engine` is installed; the two share no file), import `os_optics` and run `os-optics`.

**Local viewing and the companion kit.** This wheel includes `os-optics view RUN` for read-only 3D viewing of a saved
run. If a published `os-optics-kit` wheel includes a local Project/run-follow viewer and declares compatibility with
this installed `os-optics` version, use the kit's documented command and install it with normal dependency resolution.
The kit viewer is not a command in this Engine wheel.

**When the service asks for a newer engine.** A submission validated with an engine version the OpenSci service does
not accept is refused with `ENGINE_VERSION_NOT_ACCEPTED` (recovery `UPGRADE_ENGINE_AND_RERUN`). Upgrade, then move each
saved Project to the current catalog and run it again:

```bash
python -m pip install --upgrade os-optics
os-optics catalog sync
os-optics project update-catalog project.json --out project.json   # its next_steps say what to do with each component
os-optics project run project.json --out-dir run
os-optics resolve run
```

`update-catalog` lists components as `unverified` when their product data cannot be compared with the current release.
Where the old release can never be compared (the service no longer distributes it, or it is in a catalog format this
os-optics no longer reads) it moves them at once (status `UPDATED_UNVERIFIED`); where the old release may still be
obtained it keeps them, and `--include-changed` moves them. Review the new Local Validation before you submit.

`os-optics doctor` (or `python -m os_optics doctor`) prints one JSON report: the engine version, Python and
platform, package versions, whether the `[protected]` extra is installed, the local public optical catalog, and whether
`OPENSCI_API_URL` and an Account token are set up. `ready.local_design: true` (exit code 0) means this interpreter can
run a Project; `ready.catalog: true` means Projects that use catalog products (`OS-OPT-...`) and the catalog commands
can start (a Project of declared optical models or native components needs no catalog); `next_steps` lists the exact
commands for anything missing. The token itself is never shown. Doctor is local
and offline: it does not contact OpenSci or test the connection (`os-optics catalog check` asks the catalog
service, `os-optics account status` checks your token with the service). On a fresh install `catalog.status` is
`DOWNLOAD_ON_FIRST_USE`, a warning, not a failure: the catalog is downloaded by the first command that needs it, or now
with `os-optics catalog sync`.

### The `protected` extra is optional

Plain `os-optics` covers the local workflow: Projects, simulation, Local Validation, the 3D view, the local assembly
check and drawing, and the catalog (downloaded on first use). The extra adds `cryptography`, which opens the
AES-256-GCM envelope, and is needed only to open the encrypted Assembly Preview (Experimental Lab Assembly) and
Assembly Detail deliveries of a commercial resolution:

```bash
python -m pip install "os-optics[protected]"      # uv: uv tool install "os-optics[protected]"
```

### The public optical catalog: synchronized, versioned, offline-first

The package does not contain the product catalog. OpenSci publishes catalog releases (`catalog_version`, e.g.
`2026-09-30.3`) on its service, and the engine keeps verified copies in a local catalog store, so new products and
catalog corrections arrive without a new engine release:

* **First run.** The first command that needs the catalog downloads the current release (a few MB, verified by
  SHA-256) into `~/.opensci/catalog/` (or `OPENSCI_CATALOG_DIR`); one line on stderr names the release, its size and
  the service it came from (standard output stays JSON). After that the engine checks the service at most once an hour
  with a small conditional request and downloads only a release it does not have (`OPENSCI_CATALOG_CHECK_INTERVAL` in
  seconds; `0` turns automatic checks off). Offline, the stored release is used.
* **Explicit control.** `os-optics catalog status` (local, no network: the last contact with the service and its
  time), `catalog check` (ask the configured service now; when it cannot be reached, `up_to_date` is null rather than a
  stale answer), `catalog sync` (download the current release and make it active), `catalog verify` (re-check the
  stored files). From Python: `from os_optics import catalog; catalog.status(); catalog.check(); catalog.sync();
  catalog.verify()`.
* **Which recovery command.** `catalog sync` downloads the service's current release and makes it active (the usual
  fix); `catalog sync --release V` fetches one release the service still distributes (it becomes active only when no
  release is active); `catalog use V` selects a release already stored on this computer; `catalog import FILE` adds a
  release file on a computer without access to the service.
* **Offline computers.** Copy a catalog release file to the computer and run `os-optics catalog import
  public_optical_catalog.json`.
* **Several programs at once.** Agents and commands on one computer may share the catalog store; its changes are
  serialized by a lock file, so no release or setting is lost. If the store's index is lost, it is rebuilt from the
  stored releases with none active: the next check selects the service's current release, and offline the next
  catalog or design command uses the newest stored release and says so on stderr (`catalog status` shows
  `active_basis: NEWEST_STORED`; `os-optics catalog use <catalog_version>` selects another).
* **Reproducible designs.** Each catalog component of a Project pins the `catalog_version` it was designed with, and a
  run uses exactly that release (fetched once if it is missing; on a computer with no stored release it also becomes the
  active release, and an active release is never replaced by it). `os-optics project update-catalog project.json
  --out project.json` moves a Project to the current release: components whose product is unchanged are re-pinned, and
  so are `unverified` ones whose old release can never be compared (status `UPDATED_UNVERIFIED`); changed and withdrawn
  products, and unverified ones whose old release may still be obtained, keep their pin and are listed with
  `next_steps`; `--include-changed` moves the changed and unverified ones too. Run an updated Project again before you
  submit it.
* The service is `OPENSCI_CATALOG_URL`, else `OPENSCI_API_URL`, else <https://opensci-optics.com/>. If the service
  publishes a release in a newer catalog format than this engine reads, `catalog status` / `doctor` say
  `ENGINE_UPGRADE_REQUIRED` and the stored release stays in use until you upgrade.
* **Minimum os-optics per catalog schema.** A release states its catalog schema (`schema_version` in the service's
  manifest; `catalog status` lists the schemas this engine reads as `supported_schema_versions`). Catalog schema 3.5
  (polarizing prisms) needs os-optics 0.13.0 or later; catalog schema 3.6, a release that also publishes design
  materials (materials no product is made of, published for designing with them), needs os-optics 0.14.0 or later.
  os-optics 0.13.0 sees a 3.6 release in the manifest, reports `ENGINE_UPGRADE_REQUIRED` before downloading it and keeps
  its stored release; with no stored release it takes the newest release of schema 3.5 or earlier the service still
  distributes.

```bash
os-optics catalog sync                     # first run (or let the first design command do it)
os-optics catalog status                   # active release, stored releases, last check
os-optics catalog import catalog.json      # an offline computer: use a release file you copied
os-optics catalog use 2026-09-30.3         # make a stored release the active one (no network)
os-optics catalog mechanical list          # the mechanical catalog in effect (--kind, --holds-size MM, --mechanical FILE)
os-optics catalog mechanical show OS-MECH-...   # one identity's facts
os-optics catalog mechanical status        # which mechanical documents are in effect, and from where
```

`catalog sync` also fetches the public mechanical catalog when the service offers it (verified by SHA-256; a service
without it is not an error), and `catalog status | check | verify` report it in a `mechanical` block. See the
[mechanical catalog](../en/mechanical-catalog.md) page.

## Quick start

```bash
os-optics catalog list --category LENS --wavelength-nm 780 --limit 20   # products usable at 780 nm (paged rows)
os-optics catalog list --category LENS --focal-length-mm -50 --focal-tolerance-mm 5   # negative lenses near -50 mm
os-optics catalog show OS-OPT-...                            # one product: specifications, surfaces, band, geometry
os-optics project example --kind beam_expander > project.json   # a complete two-lens reference design to copy and edit
os-optics project run project.json --out-dir run             # simulate; exit 0 targets met, 1 unmet, 2 undetermined, 3 incomplete
os-optics view run                                           # the 3D Optical Simulation View in a local browser page
os-optics view run --snapshot shots                          # or PNG views + scene_summary.json, without a browser
os-optics project verify run                                 # re-check the run directory's hashes and bindings
```

Reference designs (`project example --kind ...`): `focus` (a single lens), `folded` (a fold mirror and a lens),
`beam_expander` (two-lens telescope), `relay_4f`, `folded_expander`, `dichroic_routing` (two wavelengths split by a
dichroic mirror), `polarization` (waveplate + polarizer), `two_beam_interference` (one laser split into two arms
that cross on a plane: targets on the fringe period, the intensity balance, the polarization overlap and the coherence
length), `two_color_dichroic` (one source of two
spectral lines whose lines a dichroic mirror sends to two detectors) and `dichroic_combiner` (two lasers combined onto
one beam by a dichroic mirror, one meeting its coated face and the other its back face). Each is built from the active
catalog release by query.

The same from Python:

```python
import tempfile
from os_optics.local import example_project_of_kind, load_public_catalog, run_optical_project, verify_run_artifacts
from os_optics.viewer import render_snapshots

catalog = load_public_catalog()                              # the active catalog release (local catalog store)
print(catalog.catalog_version, len(catalog.products), "products")
lens = catalog.select(category="LENS", form="PLANO_CONVEX_LENS", wavelength_nm="780")[0]
print(lens.optical_sku, "|", lens.display_name, "|", lens.display_name_zh_cn)

project = example_project_of_kind("beam_expander")           # a ProjectDocumentV2: source -> L1 -> L2 -> output plane
run = run_optical_project(project, output_dir=tempfile.mkdtemp())   # or a JSON document / JSON text / file path
print(run.status.value, [(t.target_id, t.status.value, round(t.value, 3)) for t in run.result.targets])
print(run.local_validation.validation_status.value, run.local_validation.input_hash.digest[:12])
assert verify_run_artifacts(run.artifacts.directory) == ()
render_snapshots(run.artifacts.directory, "shots")           # iso / top / front / side PNGs, scene_summary.json, legend.md
```

### Read the result

A run answers four questions in four places. **Did the calculation complete?** `outcome.execution` (`COMPLETED`, or
`PARTIAL` when a budget stopped the beam trace, `FAILED`, `CANCELLED`). **Are the targets met?** Each target's
`assessment` (`MET`, `UNMET`, `INDETERMINATE`, `NOT_APPLICABLE`) and `outcome.requirements`, which lists them; the exit
code reads these two. **Is a number usable?** The quantity's state and its `value_basis`. **How severe are the findings
together?** The diagnostic summary `status`, which sorts and flags and decides nothing.

A number is the model's value when it has no `value_basis` entry or the entry's `kind` is `MODEL`. Otherwise the entry
says what the number is: `LOWER_BOUND` (what it does not count can only add), `UPPER_BOUND` (what it counts may not all
contribute) or `ESTIMATE` (the model's number outside a validity limit, or with a stand-in for an input the design does
not state), and lists what it leaves out (`omitted`: the part, where, and the most it can add), what it assumes
(`assumed`) and the validity checks it failed (`validity`: the parameter, the limit, the observed value and the setting
that holds the limit). A bound decides a target only in the direction it proves. An estimate's number alone decides no
target: where the result also states a proven interval the value lies in (the interval in the target's message, the
range over degrees of coherence 0 to 1, the range coating group-delay bounds leave, the interval light the trace did not
follow leaves), the target is `MET` or `UNMET` when that whole interval meets or misses it and otherwise
`INDETERMINATE`; the value keeps its `ESTIMATE` entry either way, and the target reports the value, its error against
the target and the entry. `os_optics.results.basis_of(record, field)`
returns the entry, also for runs written before the field existed.

```python
import tempfile
from os_optics.local import example_project_of_kind, run_optical_project
from os_optics.results import basis_of

run = run_optical_project(example_project_of_kind("beam_expander"), output_dir=tempfile.mkdtemp())
print(run.result.outcome.execution.outcome.value, run.result.outcome.requirements.met)   # COMPLETED ('expanded_waist',)
for target in run.result.targets:
    print(target.target_id, target.assessment.value, target.value, target.unit, basis_of(target, "value"))
plane = run.result.observations[0]                           # the output plane
print(plane.quantity_state.value, plane.total_power_W, plane.power_upper_bound_W)   # state, known power and upper bound
power_basis = basis_of(plane, "total_power_W")              # the public glass can have unstated bulk loss
print(power_basis.kind.value if power_basis is not None else None)
print(plane.arrivals[0].beam.gaussian.beam_radius_x_mm)      # the arriving beam's 1/e^2 radius in mm
```

From a shell, `os-optics results targets run` prints every target with its assessment, value and entry, and
`os-optics results monitors run` every plane's power with its state, bounds and entry.

### Computation budgets

The Project stores its computation settings in `compute_budget`. The defaults are 64 physical interactions per path,
512 paths, 2,048 total trace steps, 256 interference pairs per observation and 4,000,000 work units of return-light
analysis for the whole run. Increase the relevant budget for a longer optical chain, more source lines or branching paths,
or more pair calculations. Free propagation and observation planes do not consume physical interactions; they do consume
trace steps. The primary trace and auxiliary light probes each use the stated path and step allowances; the execution
result identifies which phase exhausted its budget. The return-light analysis bounds the light the trace leaves out as
negligible where surfaces facing each other, or a closed path of three or more surfaces, may return it; it runs only for
a wavelength whose left-out light a power or exposure upper bound reads, and its cost grows with the reflecting surfaces
of the scene (about 4 µs per unit). `max_return_analysis_work` bounds the analysis of the whole run: the wavelengths
share it in the order the run reads them (each record's `work_before`), so a run of many source lines does at most that
many units. Where it is reached, the bounds that depend on the analysis are withheld (`RETURN_ANALYSIS_BUDGET`, a
notice): a plane's `power_upper_bound_W` is null, a rating that read the bound stays `UNKNOWN`, and the execution stays
`COMPLETED` with the budget listed in `outcome.execution.budgets.exhausted_by_phase` (`return_analysis`). Lower it to
keep a large scene quick, raise it to obtain those bounds. Each wavelength the analysis ran for is recorded in
`outcome.execution.budgets.return_analysis`: whether a closed return of three or more surfaces was ruled out
(`COMPLETED_NO_RETURN`), found possible (`POTENTIAL_RETURN`, with its surfaces; also a plane-faced solid whose internal
folds have no certified sum) or not decided within the limit
(`INCOMPLETE`), with the work done and the search's storage. Only beam pairs that can interfere -- of one coherence group, or named by a pair-metric
target -- count against `max_interference_pairs`; pairs of independent sources are counted per plane in
`incoherent_pairs`.

The same block sets the pupil sampling of every Gaussian beam source: `pupil_ring_count` (default 8) equal-power
rings of `pupil_points_per_ring` (default 12) points. The pupil rays give the spot, the angles of incidence, the
aperture use and the per-ray paths, and the power of a beam without a valid Gaussian state that an edge cuts (the
geometric pupil-sample basis, `power_sampling_resolution_W`). A beam carries `1 + rings x points + 8` rays, so the trace's
time, memory and full ray data grow in proportion; the sampling resolution of a cut power shrinks about as `1 / rings`
when both settings rise together (16 x 24 halves it at four times the rays, 32 x 48 quarters it at sixteen times). Two
more work limits live here: `max_scatter_origins` (default 64), the scattering origins whose scattered light is traced as
rays, and `max_mesh_patches` (default 1024), the traced patches one faceted surface may have. A run's size is bounded by
its memory and run-file limits (`--max-memory-mib`, `resource_limits`), whose estimate before the trace refuses a run that
would exceed them, not by these fields.

Use the same edit operation in Python, the CLI or a workspace:

```python
from os_optics.local import example_project_of_kind, json_api
project = example_project_of_kind("beam_expander")
edited = json_api.update_project(project, [{
    "op": "set_compute_budget",
    "compute_budget": {"max_interactions_per_path": 800, "max_paths": 4096,
                       "max_trace_steps": 12000, "max_interference_pairs": 1024},
}])
assert edited["ok"], edited
project = edited["project"]
print(edited["compute_budget"])  # every effective setting, including defaults
```

Save the operation array as `budget-ops.json`, then run:

```bash
os-optics project update project.json --ops budget-ops.json -o project.json
os-optics project check project.json
os-optics workspace update-project system.json --ops budget-ops.json
```

`set_compute_budget` replaces the budget block; omitted fields use defaults. Set `compute_budget` to `null` to reset it.
`capabilities` lists the defaults and accepted fields. The settings persist through workspace export, are included in
the run manifest and input identity, and changing them makes older runs historical. Each budget is a positive integer
up to 9,007,199,254,740,991, the largest integer preserved exactly by both Python and browser JSON; this representation
limit is not a suggested workload. Increase one limit at a time and inspect execution completeness and artifact size.
The defaults keep ordinary runs bounded; larger path and step budgets can produce large ray files and 3D scenes.
View selection changes only displayed rays: it never reduces the physical pupil sampling or the saved ray data.
A run of hundreds of beams can record its ray data as a summary: `os-optics project run project.json --out-dir run
--ray-data summary` (or `run_optical_project(..., ray_data="summary")`) writes the chief rays and each path's pupil sampling
without the pupil rays, about 1 % of the full `ray_data.json`. The trace, every result and every hash are the same as
with the default `--ray-data full`; `ray_data.json` and the manifest state the level, and `results rays` and
`results wavefront`, which need the pupil rays, refuse such a run (`PUPIL_RAYS_NOT_RECORDED`).

`project run` reports its progress on stderr phase by phase (lowering, trace, observation results, untraced light,
return-light analysis, bounds, exposures, beam pairs, targets, result, ray paths, model scope, record, scene, writing) with counts, elapsed
time and memory: `--progress text|json|none` (default: text when stderr is a terminal). Ctrl-C stops the run: it prints
the run's record -- execution `CANCELLED`, the phase it stopped in, every observation and target unevaluated -- and exits
130; `--out-dir` then holds that record, `run_stopped.json`, beside whatever was there. The run directory follows one
rule: a run that completes replaces the run there (its files are written to temporary names and moved into place once all
are written; files of the earlier run the new one does not write are removed by name), and an attempt that does not
complete -- a usage error, a refused Project, a stop, `--max-artifact-mib`, a failure or Ctrl-C before the run is moved
into place -- leaves the directory as it found it, so an earlier run there still verifies and `project verify` and
`results` name the later attempt (`later_attempt`). A failure or Ctrl-C while the run is moved into place (a file held
open elsewhere) leaves what a process killed there leaves: past the move's first step, no `manifest.json`, so no run that
verifies and none that `project verify` or `results` can read, and the records below. The first step removes the run
files of each configuration that did not run this time, then the stop records, then the configurations' manifests, then
the directory's own. Stopped while those run files are removed (one held open elsewhere), it leaves the directory's
`manifest.json` and that configuration's, without the run files removed before: `project verify` then reports
`INCONSISTENT` (exit 66) and names them as missing. Stopped while the configurations' manifests are removed (a
configuration's `manifest.json` held open elsewhere), it leaves the directory's `manifest.json` beside a configuration
whose manifest is gone, recorded by its copy (`configurations/<id>/.manifest.json.earlier`): `project verify` then
reports `INCONSISTENT` (exit 66) and names the missing configuration manifest. `results freshness RUN --project P` says whether a kept run is current for a Project. A run replaces
only what the Engine wrote: a file at a run-file name is replaced only when the Engine manifest there, the copy of it
(`.manifest.json.earlier`) or a staged manifest an interrupted commit left (`.manifest.json.partial`, kept as
`.manifest.json.interrupted-<n>` by the next run that stages its own there, so a second interruption loses no record)
lists it by name;
otherwise the run names the file, exits 64 and leaves it untouched. A run killed, failing or stopped by Ctrl-C while it
moves its files into place leaves those records (also where it leaves the directory's `manifest.json` at the move's
first step), so the next run there, the same one or the earlier design, completes and
removes by name what the earlier run left. Files at other
names are never touched. A file of the earlier run a completed run could not remove (held open by another program)
stays, recorded by name in `.manifest.json.leftover`, and the next run there removes it. `run_stopped.json`, the copy,
the kept staged manifests, that record and the temporary names are the Engine's own. `project.json` is also the name of a design file, so one holding another design that no run manifest there lists
with its bytes is named and the run exits 64 (one holding the Project being run, `project run project.json --out-dir .`,
is written again in the canonical form its manifest records). Under `configurations/` a run of configurations replaces
the `configurations/<id>/` folders whose run the directory's manifest (or its staged manifest) names; a
`configurations/<id>/` it would write or remove that holds a run the manifest does not name is named, the run exits 64,
and that folder stays. It removes the folders the earlier manifest names that it does not write (a configuration that
failed included, by the same rule for each file); another `configurations/<x>/` stays. A run never
writes through a link: if `--out-dir` itself, `configurations/`, a `configurations/<id>/` it writes or a run file name
under `--out-dir` is a symbolic link or a junction, it refuses (exit 64), and the link and its
target stay as they were. `--max-memory-mib N` stops a
run when the process's memory is found at or above N MiB (read at the run's checkpoints, at most every 0.1 s, so a short
allocation between two reads can pass it) and `--max-artifact-mib N` before its run files exceed N MiB (cause
`MEMORY_LIMIT` / `ARTIFACT_LIMIT`, exit 3); a run whose estimate from its beams already exceeds a limit is refused before
the trace, and one whose run files the beam paths and steps its trace counted make larger than the limit for certain (the
Project and Engine request files and, in the result, each path and step at no fewer bytes than its shortest record), or
whose memory read then is above the limit, is refused when the trace ends (the progress lines state both estimates, the
second with the high end measured runs fell under and, when a run-file limit is set, those certain bytes), unless `--no-preflight` lets it go on (the limits then hold during the run only). In Python, `run_optical_project(..., monitor=RunMonitor(on_progress, max_memory_bytes=..., max_artifact_bytes=...))`
(`os_optics.run_monitor`) does the same; `monitor.cancel()` stops the run at its next checkpoint, which raises
`RunStopped` with the record in `.stopped`. The monitor never changes a number of the run.

`run.status` is the diagnostic summary, `run.result` the full Engine result, `run.local_validation` the `LocalValidationRecordV1`,
`run.optical_view` the 3D scene and `run.artifacts.paths` the written files. One run writes one directory:

| File | What it is |
|---|---|
| `manifest.json` | start here: validation / simulation status, per-target status, Project / catalog / Engine identities and hashes, retained DOFs, the file list with SHA-256, and the binding checks |
| `project.json` | the `ProjectDocumentV2` that was run |
| `engine_request.json` | the Engine `ValidationRequest` lowered from it (`os-optics validate` can re-run it) |
| `simulation_result.json` | the Engine `ValidationResult`: target values and statuses, beams at each observation plane, paths, power budget, issues |
| `ray_data.json` | every chief and pupil ray the Engine traced: point, direction and optical path length at each surface reached (read it with `os_optics.results`) |
| `local_validation.json` | the `LocalValidationRecordV1`: Project hash, Engine input / result hashes, Engine version and build id, optical SKUs + catalog version + simulation model, retained DOF intent, target outcomes, simulation and validation status |
| `optical_scene.json` | the Optical Simulation View as JSON (`os-optics schema optical-scene`) |
| `optical_scene.glb` | the same view as glTF 2.0: `os-optics view` shows it; any glTF viewer reads it too (nodes carry part classes and ids) |

All files come from **one** Engine execution: the scene and the record carry the same Engine input and result hashes,
and `verify` checks that. The view shows the optical elements with their design geometry, the sources, the
Engine-traced chief and pupil rays, and the observation / target planes with the beam footprints. The rays are real
rays traced through every published surface (the drawn pupil rays are the Engine's outer equal-power ring, just outside
the 1/e² radius), so they show the lenses' real aberrations; the Gaussian beam numbers (waist, radius) are paraxial, in
`simulation_result.json` and the footprints. Where the rays spread more than twice as wide as the Gaussian beam allows
(an aberration-dominated focus), the Engine reports that beam's Gaussian numbers as `OUT_OF_MODEL_SCOPE`
(`GAUSSIAN_ABERRATION_DOMINATED`) and `pupil_rms_radius_mm` gives the rays' own spot.

**Four answers, four fields** (section "Read the result"): `run.summary()`, the `run_project` and `evaluate_project` replies and the `project run` JSON list `execution`, `requirements` and the diagnostic summary in that order.

**Reading the outcome.** `run.result.outcome.execution` reports whether the calculation completed or stopped with partial results. The local validation record and result queries also incorporate post-trace model applicability: affected targets have assessment `INDETERMINATE`, and affected observations have quantity state `OUT_OF_MODEL_SCOPE`, with the specific scope issues retained. Other target assessments are `MET`, `UNMET` or `NOT_APPLICABLE`; no declared targets means no requirement assessment. Observation quantity states distinguish known power (`KNOWN`; also a sampled zero, 0 W within `power_sampling_resolution_W`, where no counted ray of an extended emitter arrives), known zero, a bounded power (`BOUNDED`: a lit plane between its traced value and its upper bound, or with an open upper end; a plane only light the trace followed may reach, from 0 to that light's bound; a plane that only light left out as negligible may reach above its level, from the traced 0 to that light's bound), unknown power and power outside the model. The raw Engine values and result hash remain unchanged by local applicability checks. A missed optional target does not by itself prevent purchasing.

`total_power_W` counts traced beam contributions. If light the trace does not follow may also arrive (a ghost reflection beside the main beam), `power_complete` is false, `untraced_power_W` states the followed ghost light, and `power_upper_bound_W` records the supported upper bound of the total beam power (not the separate coherent-power metric): the same upper end a `power_W` target on the plane is graded on. Light left out below the negligible level of its source line is the stated truncation of a value while the most it brings to the plane stays within the lines' negligible level: then it changes no state or status of the plane. A plane whose only uncounted light is of that kind reads `KNOWN` (`KNOWN_ZERO` where no beam arrives) with `power_complete` true, `power_upper_bound_W` adds the most that light brings, and a `power_W` target there reports the traced value with that amount in its `value_basis` entry (`BELOW_NEGLIGIBLE_LEVEL`, `FAINT_LIGHT_BOUNDED`) and is graded on [value, value + amount] (the left-out light can only add). A `coherent_power_W` target adds fields, so it reads the bound of that light's field at the sum instead: the rise (sqrt(P) + r)^2 - P on a lit plane and r^2 on a dark one, P the traced coherent sum and r the summed field amplitude of the left-out pieces (r^2 is at least their power), whether or not the plane's power stays within the level. While that rise stays within the level, the target reports the traced value with the rise in a `BELOW_NEGLIGIBLE_LEVEL` entry (`FAINT_LIGHT_BOUNDED`) and is graded on [max(0, value - rise), value + rise]. Above it, on a lit plane the value is an `ESTIMATE` whose `POSSIBLE_RETURN` entry holds the larger of the rise and the fall, graded on [(sqrt(P) - r)^2 (0 where r exceeds sqrt(P)), (sqrt(P) + r)^2]; on a dark plane the target has no value and no entry and is graded on [0, r^2]: a maximum at or above r^2 is `MET`, a minimum above it `UNMET`, any other limit `INDETERMINATE`. Either way the plane keeps `KNOWN` / `KNOWN_ZERO` and `power_complete` true. A limit inside an interval is `INDETERMINATE`; a limit outside it is decided as the whole interval decides it. Above that level the light may come back across the plane (facing surfaces, such as a plain window's two faces, or many crossings return it): a plane with no traced arrival then reads `BOUNDED` [0, `power_upper_bound_W`] with `total_power_W` 0.0 (the traced zero), `power_lower_bound_W` null, `followed_light` empty, `untraced_power_W` 0, `power_complete` false and status `UNKNOWN`, the `value_basis` entry of `total_power_W` a `LOWER_BOUND` whose omission is that light's `POSSIBLE_RETURN` up to the bound; its `power_W` targets name `FAINT_LIGHT_BOUNDED` and are decided only where the whole interval decides them, and a limit inside it is `INDETERMINATE`. Without a finite bound the plane reads `UNKNOWN`: `total_power_W` and `power_upper_bound_W` null, its targets `INDETERMINATE` with `FAINT_LIGHT_BOUNDED`. A null bound means no finite upper bound is established; older results may omit the field. Removing an optional target or changing its threshold does not change this quantity evidence.

The aggregate status (`status`; the same value under `diagnostic_summary` in the run summary and the replies) summarizes diagnostic severity: it sorts and flags, and the exit code reads it only under `--status-contract aggregate`. `FAIL`: a target was missed, or the scene contradicts itself (overlapping solids, an element axis along the ray, a material that gives no index) or the calculation failed numerically; a chief ray stopped by a rim is a `WARNING`. `UNKNOWN`: something could not be evaluated. `OUT_OF_MODEL_SCOPE`: the question is outside what the published models answer (for example a wavelength outside a product's band or in a dichroic's transition gap, part of the beam falling on an element its chief ray misses, light leaving or crossing an element through its edge, or an aberration-dominated focus; `run.model_scope_issues`, the Engine issues and `manifest.json` say which element, surface and value). A finding whose whole content a quantity carries is a notice (status `NOT_APPLICABLE`) at that quantity and raises no status. A run used a model outside its validity when `model_scope_issues` is not empty or any Engine issue or target has status `OUT_OF_MODEL_SCOPE` (a tabulated thin lens reports its table's domain as Engine issues, also listed in `model_scope_issues`). The status is the worst of all outcomes (`FAIL` > `OUT_OF_MODEL_SCOPE` > `UNKNOWN` > `WARNING` > `PASS`), so a target outside the model's band can read `PASS` while its `assessment` is `INDETERMINATE`: the assessment decides. `results targets RUN` answers it with every target outcome -- its `assessment` beside its diagnostic `status`; `counts` per status and `assessments` per assessment -- and with `in_model_scope` and each finding in `out_of_model_issues`. A Project that cannot be simulated as given is refused before
anything runs (`LocalRunError`; the CLI prints `{"status": "ERROR", "problems": [...]}` and exits 65, and writes no run
directory) with structured problem codes such as `OPTICAL_SKU_NOT_PUBLISHED`, `PRODUCT_ROLE_MISMATCH` or
`SOURCE_MODEL_MISSING`. `ERROR` is not a result status: there is no result to report.

**Writing your own Project.** Start from `os-optics project example` (or `example_project().payload()` in Python)
and edit it; `os-optics schema project` prints the JSON Schema and `print(os_optics.local.__doc__)` explains
every part (design frame, poses, sources, DOFs, observations, targets, where to expect the focus). In short:
`components` are optical elements -- catalog products by `OS-OPT-*` SKU and your own sources with an explicit Gaussian
`source_model` (an extended source is `model_kind` `EXTENDED_EMITTER`: an `emitting_surface`, an `angular_law`, total radiant power and `sampling`, with collection fractions and binned irradiance in the results, see `docs/engine/RESULTS_AND_EXPORT.md`; an arbitrary intensity pattern is `SAMPLED_INTENSITY` (an `intensity_map` on a grid, its unit and interpolation, an angular law and `sampling`), a coherent field on a grid is `SAMPLED_FIELD` (`amplitude`, `phase_rad`, polarization) and a speckle source is `SPECKLE` (an illumination through seeded random phase screens and their `realizations`): the run traces their rays, `os-optics results field` propagates a field to a plane, see `docs/engine/PHYSICS_SCOPE.md` P8b; a beam is `GAUSSIAN_BEAM`: wavelength and power, or several `spectral_lines`; 1/e² waist radius and position, M², polarization with its angle from the source's local +X toward local +Y (or `ELLIPTICAL`, or partially polarized: `PARTIAL` with a `polarized` part and its `degree_of_polarization`, `UNPOLARIZED`; such light is traced as two incoherent orthogonal components and each plane reports its Stokes parameters and degree of polarization per route in `polarization_mixtures`); for a pulsed source `pulse`: `repetition_rate_hz`, `pulse_duration_s` -- the FWHM of the intensity, `duration_definition` `"FWHM"` -- and optionally `total_pulses`, the powers then average powers; without `pulse` the source is continuous); each has a `nominal_pose` (mm,
unit quaternion `w, x, y, z`; local +Z is the optical axis, a catalog lens frame starts at its first surface vertex and
light enters from local -Z) and optional `retained_dofs` (`Tx..Rz`, `MANUAL` or `NUMERICAL`: which degrees of freedom
stay adjustable, by hand or motorized). The design frame is right-handed, in mm, with +Z up;
the examples lay the beam along +X. `observations` are planes where beams are measured (every observation reports its
beams in `simulation_result.json`, target or not); `targets` put a metric (`os_optics.local.target_metrics()`;
`help()` on it gives units and sign conventions) on an observation with at least one bound. A target's `parameters`
take only the keys its metric lists in `os-optics results metrics` (`parameters.path_id`, a string such as
`"SRC#0"`, and `parameters.crossing_index`, an integer; two-beam `path_id_a` / `path_id_b` / `crossing_index_a` /
`crossing_index_b`; `dose_mj_per_cm2`, a number greater than 0 that `estimated_exposure_time_s` requires). A target
with any other key or value type, a dose that is not positive or missing, a plane metric without `observation_id`, an
unknown metric or a selector its metric cannot read is not assessed: `project check` lists it as a warning
(`TARGET_PARAMETER_INVALID`, `TARGET_INVALID`, `UNKNOWN_SOURCE_REFERENCE`), and the run reports that target `UNKNOWN`
(`INDETERMINATE`, exit 2) with the code and everything else as without it. Ids you choose
(`instance_id`, `observation_id`, `target_id`) are copied verbatim into every artifact. Mounts, posts, stages and the
breadboard are not part of a design: OpenSci configures them for each design at commercial resolution.

A retained DOF may state the limits of the stage that provides the adjustment, in the axis's unit (mm for `T*`, deg for
`R*`) relative to the nominal pose: `travel` (`{"min": -2, "max": 3}`, the nominal pose inside it) and, with it,
`hard_limits` (the end stops, containing the travel), `repeatability` (+/-), `drift` (`{"per_kelvin": ..., "per_hour":
...}`) and `unit`. `project check` refuses contradictory limits; the run records them per DOF (`manifest.json`,
`local_validation.json`, `results describe`). Targets on `device_instance_id` compare them with what the design needs:
`dof_travel_margin_mm` / `_deg` (parameters `axis`, `required_min` / `required_max`: the needed adjustment; a target
`minimum: 0` means it stays inside the travel), `dof_drift_mm` / `_deg` (`axis`, `temperature_span_K` / `duration_h`;
a `maximum` on the drift over that span) and `dof_repeatability_mm` / `_deg` (`axis`). A DOF that states no such limit
leaves the target `INDETERMINATE` (`MECHANICAL_LIMIT_NOT_DECLARED`): the adjustment is not verified. The limits are
stated inputs; the trace does not read them.

**Edit, check and run again.** `project update` applies the Engine's Project edit operations
(`set_component_pose`, `set_source_model {instance_id, changes}`, `set_target {target_id, changes}`,
`set_observation`, `add_*` / `remove_*`, the declared-model operations, ...; in `changes` a `null` removes a field)
plus `transform` (a rigid move of selected components about an explicit frame and pivot, as `workspace transform`
computes it). All operations and the Project check pass, or nothing is written:

```bash
cat > ops.json <<'EOF'
[{"op": "set_source_model", "instance_id": "SRC", "changes": {"wavelength_nm": 532}},
 {"op": "transform", "selector": {"ids": ["L2"]}, "translation_mm": [0, 0, 5], "frame": {"component": "L2"}}]
EOF
os-optics project check project.json                  # identity (project_hash, engine_input_hash), problems, warnings
os-optics project update project.json --ops ops.json --expect-hash HASH -o edited.json
os-optics project run edited.json --out-dir run2
os-optics results targets run2                        # every target: assessment, value, unit, bounds, status; in_model_scope
os-optics results freshness run --project edited.json # HISTORICAL (PHYSICS_CHANGED): the design's optics, sources, planes or compute_budget changed
os-optics capabilities                                # versions, metrics with units, operations, result queries (JSON)
```

`--expect-hash` refuses the update (`PROJECT_CHANGED`, with the current `project_hash`) when the Project is no longer
the one the operations were written for. A refused operation names its index and code (`PROJECT_OP_INVALID`:
`NOT_FOUND`, `DUPLICATE_ID`, `BAD_ARGUMENT`, `IN_USE`, `UNKNOWN_OP`, `TRANSFORM_REFUSED`); an edit whose result is not a
Project is refused with its problems (`PROJECT_REJECTED`). The edit and the check are two results: an edit that leaves a
Project is written, and the report's `check` says whether a run accepts it (`ok`) and what it refuses (`problems`; then
`engine_input_hash`, `catalog_version` and `compute_budget` are null), so a design is built and repaired one edit at a
time. `--require-check` refuses the update when the check does not pass. Applying the same absolute operations again changes nothing (`changed: false`),
and adding an item identical to the one already present is reported in `already_present`. `transform` is relative, and a
list that adds an item and then changes or removes it (`[add_target X, set_target X ...]`) is refused when applied again
to its own result (`DUPLICATE_ID` / `NOT_FOUND`), so after a lost response retry with `--expect-hash`: `PROJECT_CHANGED`
then reports the current `project_hash`; if the first application took effect, it is the hash the lost reply carried.
The check's advisory `warnings` name what decides part of the outcome before a run: a target whose unit is not its
metric's (`TARGET_UNIT_MISMATCH`: the run reports that target's value in the metric's unit and does not compare its
bounds; its status is `UNKNOWN`, and a target the run cannot evaluate at all keeps that outcome) and a component none of the Project's source lines fits
(`SOURCE_WAVELENGTH_OUTSIDE_COMPONENT_BAND`, one per component: no line lies inside the wavelengths its model is
characterized for, so every beam that meets it is outside its model and the run reports it in `model_scope_issues`;
for a tabulated thin lens or a Faraday rotator also as an Engine issue in `issues`, and `details.reported_in` is then
`engine_issues`). A component some line fits has no warning: which of the other lines reach it is known after the
trace, and the run reports them per element and surface. An input the simulation does not read -- a `selected_parameters` entry that is not an attenuator
position, an entry of the Project's `materials` list -- is listed as `INPUT_NOT_SIMULATED` (`details.field`,
`details.key`): the run's results are those of the Project without it, and its summary and manifest carry the same list
(`inputs_not_simulated`). From Python:
`json_api.update_project(project, ops, {"expected_project_hash": h})`, `json_api.check_project(project)["warnings"]`,
`json_api.run_freshness(project, "run")` and `load_run("run").targets()`. Tools built on these calls -- parameter
sweeps, run comparisons, plots and reports --, engine-reproducible cases and agent skills are in the separate
os-optics-kit package (<https://github.com/JunLin615/os-optics-kit>). Install and use a kit release only when its
declared `os-optics` dependency includes the Engine version on your machine; its own guide gives the compatible
installation and commands.


### Numerical settings

The Project stores the limits and levels its results are computed under in `numerical_settings`: the power floor
(`power_epsilon_W`, default 1e-15 W; 0 sets none), the share of a source line's power below which light is not followed, probed or
listed (`negligible_power_fraction`, 1e-9), the validity limits of the paraxial Gaussian model (`gaussian_*`) and of
second-harmonic generation (`nonlinear_*`), the capture and interference thresholds, the windows within which a power
rating's test conditions count as matched (`rating_*`), and in `design_point` how far from a single stated design
wavelength or angle of published coating data its values are used as they are:

```json
"numerical_settings": {
  "power_epsilon_W": 1e-20,
  "gaussian_scope_divergence_rad": 0.35,
  "design_point": {"single_wavelength_rel": 0.01, "single_angle_deg": {"dielectric_high_reflector": 15}}
}
```

Every field is optional; an absent field is the Engine default, and a Project without the block keeps its bytes and
hash. `set_numerical_settings` replaces the block (`null` resets it), `project check` and `check_project` print every value
in force (`numerical_settings`, design-point fields under their `design_point_*` names), and `capabilities` lists each
field with its default, unit and which way of changing it admits more (`numerical_settings.fields`; the ranges are the
Project schema's, `os-optics schema project`). The settings are part of the Engine input like the budgets: a run made with other values
is not current, and the run records every value in force in `manifest.numerical_tolerances`. A number computed under a
setting other than the default names that setting in its `value_basis`. Not settable: the reach of the trace beyond the
scene, the result-hash and cross-runtime quantization and the reported resolutions.

```python
from os_optics.local import example_project_of_kind, json_api
project = example_project_of_kind("beam_expander")
edited = json_api.update_project(project, [{"op": "set_numerical_settings",
                                            "numerical_settings": {"power_epsilon_W": 1e-20}}])
assert edited["ok"], edited
print(edited["numerical_settings"]["power_epsilon_W"])  # 1e-20; every other setting at its default
```

## Evaluate selected quantities

A run computes everything; an evaluation computes what you ask for and what that requires, nothing else. The
capabilities an evaluation is made of -- the scene, the beam trace, the secondary rays, the plane readings, the power
ledger, the untraced light, the plane bounds, the surface exposures, the beam pairs, the requirements, and locally the
optical-solid check, the ray paths, the model scope, the record and the view -- each have one implementation;
`run_optical_project` is the evaluation of all of them, and an evaluation writes nothing (`ev.write_run(dir)` writes the
run directory). The preset `readings` computes each beam's
arrival at each observation plane with its traced power and the model-scope findings that say whether a number applies,
without the probe traces of light the beam trace does not follow, which take most of a complete run of a multi-element
lens: on a six-element double Gauss objective lit on axis, with one image plane, a `readings` evaluation takes about a
fortieth of the time of the complete one (0.023), whose probe traces stop at the default compute budget (`max_trace_steps`,
listed in `outcome.execution.budgets.exhausted_by_phase` under `untraced_light`; the execution is `COMPLETED`). Each design of a candidate loop in one session takes about a seventieth of its
`run_optical_project` run when the lens moves along the axis (0.014), and about a fortieth (0.023) when a radius of its
first element changes, since each such design compiles the lens again. Basis of these ratios: medians of five
evaluations, each in a fresh session, of `uv run --no-sync python benchmarks/bench_evaluation.py --fixtures double_gauss`
(fixture `benchmarks/evaluation/double_gauss/project.json` and its `candidates.json`, the default compute budget); the
ratios, not the seconds, carry over to another machine. `complete` is the run.

```python
from os_optics.local import EvaluationSession, example_project
from os_optics.results import ResultsError

session = EvaluationSession()          # keeps the catalog, the compiled elements and the Engine's products
ev = session.evaluate(example_project(), outputs=("readings", "requirements"))
print(ev.scope.executed)               # scene, beam_trace, secondary_rays, plane_readings, requirements, ray_paths, model_scope
print([(t.target_id, t.value) for t in ev.result.targets])
print(ev.results().monitor("focal_plane")["quantity_state"])   # NOT_COMPUTED: the plane bounds were not computed
try:
    ev.results().power_budget()
except ResultsError as exc:
    print(exc.code, exc.message)       # NOT_COMPUTED, naming the capability to request (power_ledger)
full = ev.extend(outputs=("complete",))   # computes the rest; the trace is reused
run = full.run()                       # the complete LocalOpticalRun (record, 3D view); full.write_run("run") writes it
print(session.statistics()["executions"]["beam_trace"])        # 1
```

A candidate loop keeps one session: a moved element invalidates the Engine's products and reuses the compiled elements; a
changed prescription or material of a declared model compiles the declared elements again, and the session holds the
compiled elements of the latest Project it lowered, so they do not accumulate over the loop; a
changed target threshold reuses every product and evaluates the targets again. A catalog file given to the session
(`EvaluationSession(catalog="catalog.json")`) is read at each evaluation and known by its content: the file edited on disk
is used by the next evaluation. `ev.scope` / `ev.describe_scope()` state
what the caller asked for and what ran (requested, executed, added, not computed with what each produces, the targets not
selected); `ev.result.evaluation` is the Engine-level scope of the result, whose `requested` and `executed` list the
Engine capabilities of the plan by id. Every target an evaluation is asked to assess has a result; one whose verdict a
capability outside the evaluation would decide is `UNKNOWN` with issue code `CAPABILITY_NOT_COMPUTED`, its message naming
the capability to add. An evaluation makes a Local Validation record only when `record` is among its outputs, which
brings every capability the request enables, the optical-solid check and the model scope for every declared target
(the view is not part of it), and it writes no run directory. `solid_check` named without the record reports:
`ev.optical_solids` holds the report -- each finding with its code, `zone` (`CLEAR_APERTURE`, `OUTSIDE_CLEAR_APERTURE`,
`UNIT_ENVELOPE`) and measured value, and the centre, minimum and edge thickness of every gap -- beside the readings, also
when a body is not a valid solid; the record and the complete run are refused for such a design.
`ev.run()` / `ev.write_run(dir)` compute the rest first,
every declared target included. `exclude=` refuses a selection that needs what it excludes (`EvaluationPlanError`
`EXCLUDED_CAPABILITY_REQUIRED`, naming what requires it; the plane readings and the requirements always come with the
model scope and its ray paths). `ev.run()` / `ev.write_run(dir)` of an evaluation made with `exclude=` are refused the
same way before anything runs, because the complete run computes every capability: evaluate the Project again without
the exclusion, or call `run_optical_project`. `record` with a selection of targets that leaves one out is refused
(`TARGET_SELECTION_INCOMPLETE`). From a shell:
`os-optics project evaluate project.json --outputs readings,requirements` prints the scope, each plane's compact
readings (power, position, size, waist; direction, phase and polarization are read from `ev.results()`) and the
assessed targets (`--status-contract` applies to those targets: the ones named by `--targets`, every declared target
when `requirements` is among the outputs, none for the default `readings`; with no contract named it is `requirements`
when targets are assessed and `execution` when none is, so `readings` exits 0 once the calculation completed and says
nothing about a target, and `--status-contract requirements` for an evaluation that assesses none of the declared
targets is refused with 64, `REQUIREMENTS_NOT_SELECTED`; the reply's `requirements` names every declared target once,
the ones it did not assess in `not_assessed`, and its `status` is `NOT_APPLICABLE` rather than `PASS` when it assessed
none), and
`os-optics project evaluate --describe [ID]` the capabilities: what each requires and produces, the target metrics that
read it, its numerical controls and phases (for a preset: the capabilities it runs and those it leaves out). JSON hosts:
`json_api.evaluate_project(document, outputs, exclude, targets, view_settings)`, `json_api.evaluation_capabilities(capability_id)` (a
capability, analysis, export or preset id) and `capabilities()["evaluation"]`.

The 3D view of a design without a complete run: `outputs=("view",)` traces the beams and builds the Optical Simulation
View of what was traced -- no record, plane bounds or targets -- in the run's scene contract, its `engine_run` naming that
evaluation's result (`ev.optical_view`; `os_optics.optical_view.scene_glb_bytes(ev.optical_view)` for the glTF binary). The JSON reply carries it
as `optical_scene`; `os-optics project evaluate project.json --outputs view` prints it, and `--scene-out scene.json
--glb-out scene.glb` writes the scene JSON and its glTF binary (the view computed beside the other outputs), with
`--pupil-rays` choosing the pupil rays it draws. A JSON host turns that `optical_scene` into its glTF binary with
`json_api.optical_scene_glb(scene)` (`files["optical_scene.glb"]`: the bytes a run directory holds for that scene).

A JSON host's candidate loop keeps its session between calls through a prepared handle:
`json_api.prepare_evaluation(document)` lowers the Project in a session this runtime keeps and returns `handle`;
`json_api.evaluate_prepared(handle, outputs, exclude, targets, view_settings, document)` evaluates the candidate `document` (default: the
handle's current Project) through it and replies exactly as `evaluate_project` does for that document, with `handle`,
`reuse` (the capabilities this call ran and those whose products it read) and `notices`;
`json_api.cancel_evaluation(handle)` stops that handle's evaluation in progress, `json_api.release_evaluation(handle)`
forgets it, and `json_api.evaluation_handles(max_handles)` lists the handles and sets their bound (default 4, at most 64).
The least recently used idle handle beyond the bound is evicted with the notice `EVALUATION_HANDLE_EVICTED`; a handle the
runtime does not hold is refused with `EVALUATION_HANDLE_UNKNOWN` and the reason (`RELEASED`, `EVICTED`, `NOT_HELD`):
prepare the Project again. Handles live in the process's memory; a fresh process holds none.

## Scientific results and export

A run directory is the complete scientific record of one Engine execution, and `os_optics.results` reads all of it
on your machine -- nothing is simulated again: every observation plane with its resolved frame (origin, `u`, `v`,
normal, active area, absorbing behaviour), every arriving beam's full state (position, direction, power, optical path,
phase, Jones vector and Stokes parameters, Gaussian q / waist / radius, first-order ray-transfer matrix and image
orientation, lineage), the beam-path tree, the power budget, the chief and pupil rays at each plane, geometric
wavefronts, irradiance maps and the meaning of every metric. Your own Agent can then plot, fit and report whatever it
needs.

```python
import tempfile
from pathlib import Path
from os_optics.local import example_project_of_kind, run_optical_project
from os_optics.results import load_run

run_dir = tempfile.mkdtemp()
run_optical_project(example_project_of_kind("two_beam_interference"), output_dir=run_dir)
results = load_run(run_dir)                                  # also: the run's {file name: bytes}, or the run itself
print(results.describe()["available"]["monitors"])           # the observation planes of this run
for arrival in results.monitor("crossing_plane")["arrivals"]:
    beam = arrival["beam"]
    print(beam["path_id"], beam["power_W"], beam["gaussian"]["beam_radius_x_mm"], arrival["polarization_state"]["s1"])
wave = results.wavefront("crossing_plane", "SRC#0")          # OPD per pupil ray (waves, piston removed), RMS, PV
print(wave["status"], wave["rms_waves"] < 1e-3)
Path("arrivals.csv").write_bytes(results.export("arrivals", "csv"))   # one row per arrival, units in the column names
budget = results.power_budget()                              # where the power went: input, sinks, residual
print(round(budget["total_input_W"], 6), sorted(budget["total_sinks"]))
```

From a shell:

```bash
os-optics results describe run                    # identity, status, available results, conventions, files
os-optics results monitor run focal_plane         # one plane: every arriving beam's state, coherent sums, targets
os-optics results wavefront run --monitor focal_plane --path 'SRC#0' --reference sphere --center ray_focus
os-optics results metrics --name waist_distance_x_mm   # unit, definition, sign convention, validity
os-optics export arrivals run --format csv --out arrivals.csv
os-optics export rays run --monitor focal_plane --format jsonl --out rays.jsonl
```

Every `results` and `export` command checks every file's SHA-256 and the cross-references of the run first, as
`os-optics project verify run` does, and names a run that does not verify on standard error; `--no-verify` reads the
files as stored (`integrity.checked` false), for a run whose ray data make the check slow. Two read as stored:
`results freshness`, which checks the files only with `--verify` (otherwise `integrity.checked` is false), and
`results configurations`, which reads the run of configurations (check it with `project verify`). In Python `load_run(run)`
reads the files as stored and `load_run(run, verify=True)` checks them. `os-optics results freshness run --project
project.json` says from the recorded hashes whether the run is current for a Project (`CURRENT` / `HISTORICAL` with the
reasons; `physics` and `requirements` separately), whether or not the files were checked.

Export kinds: `monitors`, `arrivals`, `rays`, `paths`, `segments`, `power-budget`, `superpositions`, `wavefront`,
`irradiance`, `metrics`, `handoff`, `exposures`, `power-ratings`, as CSV, JSON Lines or JSON (`os-optics results
columns KIND` documents every column and its unit); the dense irradiance grid also as NPZ. The wavefront is geometric optics (optical path differences of the
traced pupil rays, measured from the source's Gaussian phase front) and the irradiance map is the paraxial Gaussian
superposition: neither includes diffraction. A wavefront the data cannot support -- after an ideal or tabulated thin lens, or with
too few rays -- is refused with a reason code instead of a number, and so is an irradiance map, a handoff power or anything else
computed from a beam whose power is out of model scope (`POWER_OUT_OF_MODEL_SCOPE`; the plane's `quantity_state` reads
`OUT_OF_MODEL_SCOPE`). Each metric descriptor names the result field that
carries its value (`result_fields`: `divergence_half_angle_x_rad` is `arrivals.far_field_half_angle_x_rad`).

For a tool that propagates fields itself, `results.handoff(monitor, element=None)` (`os-optics results handoff RUN
--monitor ID [--element ID]`) gives the beams at a plane in its (u, v, n) frame -- complex q per axis, waist, curvature,
M², Jones vector and basis, power, medium -- with the TEM00 field-construction convention, each beam's validity (an
aperture upstream keeps the power exact but not the diffracted spot) and a named element's clear apertures in the same
frame. A run exports beam parameters, not a sampled field: intensity alone does not determine a field, and M² > 1 alone
does not determine a beam's modes.

## Sources, spectra and interference

A source is a Gaussian beam of one or several spectral lines, a continuous spectrum traced as quadrature lines, or an
extended or sampled source (section "Writing your own Project"). Two beams of one source interfere within its
coherence length; what the design does not state about coherence, group delay or coating phase is taken by a stated
convention, and every number that rests on one names it in its `value_basis`.

Multi-line sources: a source that emits several lines (a 532 + 1064 nm laser, a multi-line ion laser, an RGB source)
lists them as `spectral_lines` (`[{"wavelength_nm": 532, "power_w": 0.001}, ...]`, distinct wavelengths) instead of
`wavelength_nm` and `power_w`. Every line is traced as its own beam with its own power, dispersion and coating response;
each observation reports every line's beam with its wavelength and its `power_by_wavelength`; the power budget is per
line; a target selects a line with `wavelength_nm`. Waist, M², polarization and coherence apply to every line.

Continuous spectra: a broadband source (an SLD, a white-light or a filtered lamp beam) states `spectral_density` instead:
a `shape` (`GAUSSIAN` or `LORENTZIAN` with `center_nm` and `fwhm_nm`, a `TOP_HAT` of width `fwhm_nm`, or a `TABULATED`
density linear between its rows), an optional `band_nm` and a `line_count`, with `power_w` the power within the band
(`{"spectral_density": {"shape": {"kind": "GAUSSIAN", "center_nm": 840, "fwhm_nm": 50}, "line_count": 32}, "power_w": 0.002}`).
It is traced as `line_count` quadrature lines: the centres of equal bins, each with its bin's power and the bin's width as
its linewidth. The coherence follows from the spectrum: the run's `source_spectra` reports the coherence length of the
spectrum, that of the traced lines and of twice as many lines, and how far the lines' degree of coherence departs from the
spectrum's (the quadrature's convergence); a power the run sums over the lines is the midpoint quadrature of the band
(`docs/engine/PHYSICS_SCOPE.md`, P2).

Interference: two beams of one source interfere within its coherence length. Declare it on the source model, as
`linewidth_nm` (spectral FWHM) or `coherence_length_mm` (one of the two). Without it the source is taken as
monochromatic: fringe visibility, peak irradiance, the coherence factor and the coherent power have values, each with a
`value_basis` entry that names the stand-in (assumed `FULL_COHERENCE`). Where neither source of the pair states its
coherence they are at a degree of coherence of 1, and a target on one of them is judged on the interval every degree of
coherence from 0 to 1 gives. Two sources form such a pair only where they share a `coherence_group`, which only the
low-level Engine API sets (`os_optics.builders`, `SourceSpec.coherence_group` of a `ProjectSnapshot`); a `source_model`
Project gives each source its own group. At the other source's coherence length where that source states it, fringe
visibility, peak irradiance and the coherence factor are upper ends (`UPPER_BOUND`, with `FULL_COHERENCE` naming only the
unstated source), and a target is judged from 0 up to that value (the peak irradiance from the mean irradiance); the
coherent power (`combined_power_collinear_W`), which the cross term may lower or raise, is an `ESTIMATE` (assumed
`FULL_COHERENCE`) judged from 0 up to the larger of the value and the beams' incoherent power sum. Where the two paths'
group-delay dispersions differ (glass on one path only), a shorter coherence length of the unstated source can give a
larger degree of coherence; where those beams meet coatings of bounded group delay differently, the bounds allow any
degree of coherence up to 1. There the values are `ESTIMATE`s at the stated length and the traced delay, the pair states
`coherence_factor_max` and no `coherence_factor_min` (`coherence_factor_max`: the largest degree of coherence any length
of the unstated source up to the stated one gives, 1 behind the bounded coatings), and a target is judged from 0 up to
the value at `coherence_factor_max`; the coherent power's range reaches the same end at every phase
(`combined_power_collinear_min_W` / `_max_W`, `total_power_min_W` / `_max_W`): the dispersion turns the phase of the
degree of coherence as well. Stating the width gives the reduced values.
A pulse train without a stated coherence keeps them not computed (`COHERENCE_UNDEFINED`). Fringe period, path difference
and overlap do not need it. Beams of two different source components are mutually incoherent, unless they share a `coherence_group` (low-level Engine API), and so are
the different lines of one multi-line source; a linewidth gives each line the coherence length of its own wavelength.
Two beams of the same wavelength from different source components form no lasting fringe: a `fringe_visibility`
target on them reads 0. Beams of different wavelengths, including the lines of one source, are not paired.
The coherence follows the beams' relative group delay, which is known only where both beams cross the same elements and
coatings whose own delay the design does not state: an element without a thickness (an ideal or tabulated thin lens, a
splitter plate, a polarizer, a retarder, an attenuator, an aperture that attenuates) and a coating without a stated phase -- also met beyond the
critical angle -- delay light by an unknown amount (`arrivals[].group_delay_unknown_at`), a coating met obliquely, a
retarder and a polarizer that passes light along both axes by an amount that may differ between polarizations (the
record carries the beam's state there). Beams that cross such passes differently -- the two arms of an interferometer
through a coated splitter, coated mirrors or a thin filter in one arm, or two beams in different polarization states
through a common retarder or oblique coating -- have no known degree of coherence: the same metrics are then stated at
no added delay at those passes, with `value_basis` of kind `ESTIMATE` naming them (`UNSTATED_GROUP_DELAY_ZERO`), and a
target on one of them reports the value without deciding; the exposure bounds take them as fully coherent.
Model the element with its thickness and material, or state the coating's phases (constant over wavelength), to make it
known.
The same passes leave the phase between the two beams open: the trace gives a coating that states no phase a default
one and an element without a thickness none, and neither is a statement about the part. Where one beam makes such a pass
and the other does not, their `phase_difference_rad` is stated at those default phases with `value_basis`
(`UNSTATED_PHASE_AS_INTERFACE`, the passes) and a target on it is reported and not decided; behind an element that
states no optical path (an ideal or tabulated thin lens, a Faraday rotator, a grating) it is not computed
(`PHASE_UNDEFINED`). Passes both beams make alike cancel, and a coating that states its phases gives the value (a phase
table states the phase without its group delay: such a pass is listed in `group_delay_unknown_at` ending in `#phase`).
The fringe period and the beams' spatial overlap do not depend on it.
A coating may also state a bound of its group delay, `group_delay_bound_fs` (catalog coatings from catalog schema 3.2,
per coating; a declared coating the same way). Where every pass two beams do not share has such a bound, their relative
group delay is known within the sum `B` of those bounds, and their degree of coherence within `δ = 2B / (√π L_c)` of its
value at the traced delay, whatever the coatings' dispersion within the bounds. The fringe visibility, peak irradiance
and coherence factor and margin are given at the traced delay together with the range the bounds leave
(`group_delay_bound_mm`, `coherence_factor_min` / `coherence_factor_max`), and a target on one of them is met or not met
only when its whole range is. A Mach–Zehnder of two plate splitters and two dielectric mirrors whose coatings state
1000 fs (splitter and mirror coatings) and 50 fs (the plates' anti-reflection faces) has `B` = 6100 fs, 1.83 mm of group
path. A pulsed source keeps such pairs undetermined only where a revival of its train lies within `B` of the traced delay.
Delays in time are targets too: `group_delay_ps` (a beam's envelope arrival time from its source, `Σ n_g L / c`),
`phase_delay_ps` (`Σ n L / c`) and `time_delay_ps` (two beams' relative envelope delay `t_a − t_b`, the delay of a delay
line or a pump-probe pair). The same bounds give a delay's interval. A pass without a stated group delay or bound is
left out of the number: the value is the delay of the modelled path (for a pair, of the two modelled paths), `UNKNOWN`
with `GROUP_DELAY_UNDEFINED` and not graded, its `value_basis` an `ESTIMATE` naming `UNSTATED_GROUP_DELAY_ZERO` at those
passes. A medium without a group index, and two beams of different sources, leave no value. The pulse shape and its
dispersion broadening are not part of these values.
The bound says how the phase between the two beams changes across the line, not its value: the phase difference, and
the power of beams that overlap on a plane (`coherent_power_W`, `superpositions[].total_power_W`,
`combined_power_collinear_W`), depend on the reflection and transmission phases of the coatings they meet differently. Where those coatings state their phases,
the coherent power is given with the range the bounds leave (`total_power_min_W` / `total_power_max_W`); where they do
not, it is given at the default phases (`value_basis`, `UNSTATED_PHASE_AS_INTERFACE`) beside its range over every phase
-- for the Mach–Zehnder above, from 0 to the sum of the two arms' fields in phase -- and a target on it is decided only
when that whole range meets or misses it. An oblique coating of unstated phase that a beam meets in a state mixing s and
p also changes its polarization by an unstated amount: the visibility is then given on the default phases with that
entry. Send s- or p-polarized light to such coatings, or state their phases, to make them known.

## A pupil mask, its focal field and a detector

Two calculations continue from the light a run delivers, or from a light you state, stage by stage. Each quantity says how it is known: `MODELED`, `EXTERNALLY_CHARACTERIZED`
(a value you stated, with its source) or `UNAVAILABLE` (with a reason; what does not need it keeps its value).

```bash
os-optics results pupil-field --request request.json > field.json       # add RUN --monitor PUPIL for a run's beams
os-optics results detector --field field.json --detector camera.json    # or RUN --monitor ID for a plane's irradiance
```

`pupil_field` takes a mask -- a ramp, a binary grating, a sampled phase and amplitude, a file with its SHA-256, or a weighted Gerchberg-Saxton mask for a list of spots -- with its pixel pitch and fill, to the
scalar focal field behind an ideal lens, with the power at every stage. It holds up to a marginal-ray sine of 0.2; above that the focal quantities are `UNAVAILABLE` and the pupil stage and its power ledger stay: a tight focus
is the vector focal field below. `detector_response` takes an irradiance map to photons, electrons, ADU, saturation, noise and SNR per pixel; every detector parameter is optional, and what is not stated leaves what
needs it `UNAVAILABLE`. `os-optics schema pupil-field-request` and `os-optics schema detector-model` print the inputs; `docs/engine/PHYSICS_SCOPE.md` (P10e) has the conventions, the validity limit and the closed forms they were validated against.

## The vector focus of an objective

```bash
os-optics results focal-field --request focus.json > focal.json        # add RUN --monitor PUPIL for a run's beams
```

`focal_field` takes an ideal aplanatic objective -- numerical aperture, effective focal length, the index of the focal medium and
optionally a window between it and the focus -- and the light on its pupil (uniform, annular, a Gaussian beam, a set of TEM00 beams for
several foci, or the beams a run delivers to the pupil plane) with its polarization (linear, circular, a Jones vector, unpolarized) and an
optional Zernike pupil phase, to the vector field near the focus by the Richards-Wolf / Debye integral. The reply is an analysis document
(`os-optics schema analysis-result`): the power ledger from the pupil to the focal plane with its energy balance, per spot the peak `|E|^2`
in W/um^2 and its position, the full widths at half maximum along x, y and z, the half widths at 1/e^2, the radii holding 50 % and 80 % of
the power, the Strehl ratio, the share of the electric energy in `E_z`, and maps of `|E|^2` and `S_z` (with the complex `E_x`, `E_y`,
`E_z` on request). Every value comes with its model, the validity checks of that model, and convergence evidence: it is computed again with
every node count doubled and checked by an independent evaluation of the same integral. A Gaussian waist is reported only for a Gaussian
beam within the paraxial Gaussian scope; at higher numerical aperture it is `OUT_OF_MODEL_SCOPE` and the widths describe the spot.
`docs/engine/PHYSICS_SCOPE.md` (P10g) has the conventions and the references it was validated against.

## Point spread function and MTF of an image plane

```bash
os-optics results imaging RUN --monitor image [--request imaging.json] > imaging.json
```

`imaging` traces every field of a run (each beam source's spectral line) again with a dense square grid of rays that fills the
system's aperture stop uniformly -- a point object -- and reads the rays at the image plane. Per field it reports the image-space
numerical aperture, the geometric spot, the wavefront about the image point (referred to infinity; RMS and peak-to-valley), the point
spread function by the Debye integral over the traced rays (scalar, or vector above an image-space sine of 0.2), the Strehl ratio,
the tangential and sagittal MTF with the diffraction-limited MTF of the same pupil, the radii holding 50 % and 80 % of the light, and
the geometric MTF where the wavefront error is at least one wave. Each quantity names its model and that model's validity checks,
and is the value from twice `launch_grid` rays per axis, with its difference from the `launch_grid` value as its convergence. A
request with `"convergence": false` analyses each field once, on `launch_grid` rays: its values are that grid's, none carries a
convergence, and on the coarser pupil a diffraction quantity can leave its model's domain (`PUPIL_UNDERSAMPLED`) where the default
reports it; raise `launch_grid` for a finer pupil. A request with `"diffraction": false` computes no point spread
function (its quantities are `NOT_COMPUTED`), for a candidate loop that reads the spot and the wavefront only; a field without a
wavefront states it as the full analysis does. Outside a model's domain a quantity is `OUT_OF_MODEL_SCOPE` with
a reason (`RAY_OPL_UNDEFINED` behind an ideal thin lens, `PUPIL_UNDERSAMPLED`, `PSF_WINDOW_TOO_SMALL`, `GEOMETRIC_REGIME_NOT_MET`; the
geometric MTF is `UNKNOWN`, `GEOMETRIC_REGIME_UNKNOWN`, where no wavefront is defined; in both regime states its curve is stated with a
`value_basis` of kind `ESTIMATE` that carries the regime check) and
the geometric quantities stay. The run's own result is not changed. `docs/engine/PHYSICS_SCOPE.md` (P10h) has the model and its checks.

## Ghost images of an image plane

```bash
os-optics results ghosts RUN --monitor image [--request ghosts.json] > ghosts.json
os-optics results field RUN --monitor screen [--request field.json] [--field-out field.npz] > field.json
```

`ghosts` traces a run again with the reflection of every lens or window face followed as a ghost branch -- the bare Fresnel reflection,
or the coating's stated reflectance -- up to a bounce order (`max_bounce_order`, default 2: every two-surface ghost). Each ghost path
is listed with its bounce surfaces, its direction (an even order travels on to the image side, an odd one back toward the source),
and where it crosses the image plane: its power relative to the primary image, its focus as a distance from the plane (the rays'
least-squares focus, and the Gaussian waists where the Gaussian beam is valid), its spot and its peak irradiance relative to the
primary image. Every reflection beyond the bounce order, below the floor or left out by the trace budget, and light scattered or ended
outside the model, enters a bound on the power of every ghost not listed, with each ghost surface's reflectance and transmittance taken
at their largest over the beam's angles of incidence. The bound is proven where every ghost surface met a valid Gaussian beam on a plane
or a sphere; elsewhere it rests on the beam's pupil rays and says it is not proven. With `"convergence": false` in the request the
ghosts are traced once, with the sources' own pupil sampling: the values are that trace's, not those of the trace at twice the
sampling that the default reports, and none carries a convergence. Powers are ratios to the primary image or to the
source line; the ghosts of scattered light are not enumerated: `completeness.not_followed` names that light and the bound counts it. The analysis's execution is `COMPLETED` unless a budget stopped it (`PARTIAL`, exit 2); what a quantity lacks is stated at that quantity. The run's own result is not changed. `docs/engine/PHYSICS_SCOPE.md` (P10i) has the model and its checks.

## Spacing lenses and mirrors: paraxial analysis

`paraxial_analysis` evaluates placed components with their real prescriptions and material dispersion at a wavelength
(the Engine's thick-lens matrices): per element and for the system the ABCD matrix, effective focal length, back and
front focal distances and principal planes; `solve: "afocal"` gives the vertex gap that makes two elements afocal and
where to move the second one, `solve: "collimate"` the distance from a point source or a source's waist to the front
focal point (with each source axis's output waist, divergence and state: `COLLIMATED`, or `FOCUSED` when the source's
Rayleigh range exceeds the focal length). For cylindrical lenses, `axis: "X"` or `"Y"` names one section of the whole
system (the source's x / y, else the first component's local X / Y); a cylinder turned to any other angle is refused. The
Project needs a light source only when the request names `source_id`.

Mirrors take part directly. A mirror of vertex radius `R` is `((1, 0), (-2 / R, 1))` (`R > 0` is concave, focal length
`R / 2`), a plane mirror the identity; a mirror reflects on the side its local +Z faces. A curved mirror lies at normal
incidence, a plane mirror at any tilt folds the axis. Every distance is on the unfolded axis (`*_along_axis_mm`, `vertex_gaps_mm`,
`bfl_mm`), every `*_mm` point on the real folded path, and `axis_path` lists its straight stretches with their directions
and section axes. A two-mirror telescope with primary R = +800 mm and secondary R = -150 mm 350 mm apart reports EFL 1200 mm and
BFL 150 mm, its rear focal point 150 mm from the secondary along the returning light.

The light path decides which way the light travels, with or without `source_id`: the named source's emission direction,
else that of the Project's source whose emission line passes through the first element. A list against that light is
refused the same way both times (the telescope's mirrors listed secondary first give `PARAXIAL_REFLECTOR_BACK_FACE`).
With no source on that line the direction is assumed and `light_direction.rule` is `ASSUMED`: from the first element to
the last, a curved first mirror met on its reflecting side along its axis, a plane first mirror from the direction that
reflects towards the next element; a list in reverse order is then the same elements met by light travelling the other
way. A mirror is listed once (`PARAXIAL_REFLECTOR_REPEATED`): light that returns to a mirror circulates between mirrors,
which the cavity analysis below describes.

A `COMPOUND` unit (an imported prescription, for example) is composed from its members' poses: the entry has the unit's
matrix and its `members`. Lenses meet the light in pose order; a unit with a mirror takes its members in the order it declares
them. A lens the light returns through after a mirror is listed again where it meets it (`["L1", "M1", "L1"]`, a double pass); a list that
leaves such a crossing out is refused with `PARAXIAL_ELEMENT_CROSSED_AGAIN`, which gives the list to send. Refusals name the actual reason:
`PARAXIAL_COMPOUND_MEMBER_NOT_COAXIAL` (the member and its pose),
`PARAXIAL_BIREFRINGENT_ELEMENT` (an element holding a crystal: its ordinary and extraordinary waves see different matrices),
`PARAXIAL_REFLECTOR_BACK_FACE`, `PARAXIAL_CURVED_REFLECTOR_TILTED`, `PARAXIAL_REFLECTOR_BEHIND_SURFACES` (a second-surface
mirror), `PARAXIAL_REFLECTOR_REPEATED`, `PARAXIAL_LIGHT_DIRECTION_AMBIGUOUS` (sources on the line through the first element
that emit in different directions: name one), `PARAXIAL_NOT_COAXIAL`, `PARAXIAL_ORDER_INVALID`, and `PARAXIAL_ELEMENT_UNSUPPORTED` for a `SURFACE_SET` element or an
axicon.

```python
from os_optics.local import example_project_of_kind, paraxial_analysis

project = example_project_of_kind("beam_expander").payload()   # SRC -> L1 (f ~ 50 mm) -> L2 (f ~ 100 mm) -> output plane
analysis = paraxial_analysis(project, {"components": ["L1", "L2"], "wavelength_nm": 780, "solve": "afocal"})
solution = analysis["solution"]
print(round(solution["vertex_gap_mm"], 3), [round(v, 3) for v in solution["position_mm"]])   # where L2 goes: afocal pair
print(round(solution["beam_size_magnification"], 2))                                          # about -2: expanded, inverted
```

From a shell: `os-optics project paraxial project.json --components L1 L2 --wavelength-nm 780 --solve afocal`, or
`--components L1 --wavelength-nm 780 --solve collimate --source SRC`, or `--components M1 M2 --wavelength-nm 633` for a mirror pair. Run the Project afterwards for the traced beams.

## Optical cavities: stability, eigenmode and resonances

`cavity_analysis` (`os-optics project cavity`) describes a linear (standing-wave) cavity of placed components, listed once
each from one end mirror to the other: `["M1", "M2"]`, or `["M1", "FM", "L1", "M2"]` with fold mirrors and lenses between.
The elements are placed as the paraxial analysis places them, with their real prescriptions at the wavelength; the end
mirrors meet the light at normal incidence. From the round-trip ray-transfer matrix `M1 P_r M2 P` it reports:

* stability: the half trace `(A + D) / 2` (stable inside -1 .. 1), `g1 g2`, and `g1`, `g2` of two bare mirrors;
* the fundamental Gaussian eigenmode: its 1/e² radius at every element and its waist (radius and position on the unfolded
  axis) in every gap, and the round-trip Gouy phase;
* the free spectral range `c / round-trip group path` (glass counted with its group index) and the transverse-mode spacing
  `FSR × Gouy / 2π`;
* each mirror's reflectance (its coating at the wavelength and angle of incidence, or `--reflectance ID=R`), the round-trip
  power fraction, the finesse `π √g / (1 − g)` with `g` the round-trip field fraction (`π √R / (1 − R)` for two equal mirrors)
  and the Airy linewidth;
* with a stated TEM00 input beam at the first mirror, its mode-matching efficiency.

A symmetric confocal cavity (R = L = 100 mm, 1064 nm) reports a 0.130 mm waist at its centre, a Gouy phase of π per round
trip, a free spectral range of 1.499 GHz with the transverse modes half of it (0.749 GHz) apart, and with two R = 0.99 mirrors a finesse of 312.6. An
unstable or marginally stable cavity has no Gaussian eigenmode: its radii, waists, Gouy phase and mode spacing are
`OUT_OF_MODEL_SCOPE` with the reason (`CAVITY_UNSTABLE`, `CAVITY_MARGINALLY_STABLE`); the free spectral range and reflectances keep
their values. A mode the apertures clip (clear radius below `aperture_over_mode_min`, three, mode radii) or one beyond the paraxial
limits (the Project's `numerical_settings`) keeps the round-trip matrix's values as estimates: `OUT_OF_MODEL_SCOPE` with a
`value_basis` that states the failed condition, its limit and the element. Where losses besides the mirrors' are not modelled (an
element inside the cavity, clipping, an unstable cavity) the finesse is a proven upper bound and the linewidth a lower bound, each
with the mirrors' value beside the bound. An element without a stated thickness (an ideal thin lens) adds no group path, and a
coating outside its published data gives its held nearest value: the quantities that read them are estimates that name the
stand-in. `--polarization S` or `P` chooses a fold mirror's reflectance where its coating reflects the two differently;
`--lateral-tolerance-mm` / `--tilt-tolerance-rad` state how far an element may lie off the axis. A crystal between the mirrors gives two
families of modes: `--polarization ORDINARY` or `EXTRAORDINARY` analyses one (its index, group index and, for the extraordinary
wave, its diffraction along and across the optic axis), where every crystal's optic axis lies in its faces; without it the
quantities that read the crystal's index are `OUT_OF_MODEL_SCOPE` (`POLARIZATION_NOT_STATED`). A 10 mm calcite plate in a
200 mm cavity at 1064 nm gives 725.41 MHz for the ordinary and 731.70 MHz for the extraordinary modes. The result is an analysis document
(`json_schema("analysis-result")`, analysis `CAVITY`); the exit code is 0 for an answered analysis (a quantity outside the
model or not known says so in its own state and reason), 1 for a refused request (`CAVITY_ANALYSIS_INVALID`: the paraxial placement's codes,
`CAVITY_ELEMENT_UNSUPPORTED`, `CAVITY_NOT_CLOSED`).

```bash
os-optics project cavity project.json --components M1 M2 --wavelength-nm 1064
os-optics project cavity project.json --components M1 M2 --wavelength-nm 1064 --input-waist-mm 0.13 --input-waist-after-mm 50
```

### Light injected through a mirror: resonant build-up

With `injection` (`--inject-power-w P` or `--inject-source SRC`, and `--inject-beam`), light is fed into the cavity through the
first listed mirror and the analysis gives its steady state: the injected, circulating (the forward field leaving the input
mirror), transmitted (through the last listed mirror), reflected (back through the input mirror) and lost powers, the build-up,
transmission and reflection, and a power budget whose channels add up to the injected power (`arrays.injection.power_budget`).
The mirrors' transmittances are their coatings' at the wavelength and angle of incidence, or `--transmittance ID=T` (stated
with `--reflectance` for the same mirror); `--round-trip-loss` states the loss of the elements between the mirrors. Each
transverse mode builds up by the Airy function of its round-trip phase: on resonance a mode-matched beam circulates
`T1 / (1 − √(R1 R2))²` times the injected power. The reflection on resonance vanishes where `√R1 = (1 − A1) √R2 × F` (`A1` the
input mirror's own loss, `F` the power fraction of one pass): `R1 = (1 − A1)² R2 F²` with that loss held
(`input_loss_matching_reflectance`), `R1 = R2 F²` for a lossless input mirror (`impedance_matching_reflectance`); a larger `R1` is
under-coupled, a smaller one over-coupled (`arrays.injection.coupling`). The detuning is `--detuning-hz` from the nearest TEM00
resonance, or `--length-offset-nm`, the cavity's length less that resonance's. A Project's positions do not fix it: the round-trip
phase turns by 2π per half wavelength of length, far below a placement's precision. Without either, the powers are `BOUNDED`
over every detuning (the sum over the resolved modes, each at its Gouy-shifted phase, bounded by branch and bound over the
detuning, plus the bounds of the light not resolved) and `arrays.injection.at_tem00_resonance` gives the resolved modes'
channels with TEM00 on resonance (`not_resolved_W` the power of the modes not resolved, which they leave out). `--inject-beam EIGENMODE`
injects `--inject-power-w` in a beam matched to the fundamental mode; `INPUT_BEAM` the stated input beam; `SOURCE` the beam of
`--inject-source`. A beam that is not the eigenmode is split into the cavity's Hermite-Gauss modes `HG_mn` (a coaxial beam has even
orders only; a round one its Laguerre-Gauss `LG_p0`) up to `--mode-order-max` (default 10), each at its Gouy-shifted resonance
`(m θx + n θy) / 2π` FSR above TEM00. The light in modes above the resolved order, or beyond the order the apertures and the
paraxial limits admit, is bounded for any detuning and loss: the powers are then `BOUNDED` with a proven interval. A source of the
Project feeds the cavity when it lies on the axis behind the first mirror and emits along it: its light is traced to the mirror,
whose arriving power is injected, and its own Gaussian beam, carried through the mirror's substrate, is the injected beam (its mode
matching is reported). With anything else on its way (a stop, a lens) the cavity's own quantities stand, the injected power is
the traced arriving power and the powers are bounded by it; its mode matching is outside the model. With a crystal in the
cavity its polarization divides its power between the crystal's two families (`polarization_family_power_fraction`). A source
that is pulsed or has a linewidth above `--source-linewidth-fraction-max` (1 %) of the cavity's has its powers bounded over every
detuning; one with an `M²` above 1 (a higher-order or multimode beam) has them bounded as light not resolved. A bounded power
carries its value beside the interval where one exists, with a `value_basis` that says what the number is.

Two R = 0.99 / T = 0.009 mirrors 300 mm apart (R = 500 mm) with 2 W injected on resonance: 180 W circulate (a build-up of 90),
1.62 W are transmitted, 0.0198 W reflected and 0.3602 W lost in the mirrors' coatings. A request file (`--request FILE`, the json_api
`cavity_analysis` request) adds a detuning scan (`injection.scan`) and targets on the quantities (`targets`: `quantity_id` with
`minimum` / `maximum` / `target_value`), assessed `MET` / `UNMET` / `INDETERMINATE` with the Project's targets of a cavity metric
(`cavity_transmission`, `cavity_circulating_power_W`, `cavity_power_build_up`, ...: `capabilities()["cavity_analysis"]["target_metrics"]`); their thresholds
are the analysis's requirements identity, not its physical one. `os-optics results cavity RUN --request FILE` (json_api
`run_results` kind `cavity`) analyses the Project of a run, bound to the run's Engine input and result hashes.

```bash
os-optics project cavity project.json --components M1 M2 --wavelength-nm 1064 --inject-power-w 2 --inject-beam EIGENMODE --detuning-hz 0
os-optics results cavity run/ --request cavity_request.json
```

The run and the cavity analysis stand side by side. A run traces single passes: a beam that meets the back of a mirror whose
coating transmits ends there with `OUT_OF_MODEL_SCOPE` (cause `MIRROR_TRANSMISSION_NOT_TRACED`) (an opaque mirror's back stays `BLOCKED_BY_BACK_SIDE`), a
path between two mirrors stops at the interaction limit (`MAX_INTERACTIONS`, a partial outcome), and a target with a cavity metric
is `OUT_OF_MODEL_SCOPE` in the run, naming the analysis that evaluates it. The run's bound on light returned between two facing
mirrors (`FAINT_LIGHT_RECIRCULATES`) uses their round-trip field gain `g`, at least the analysis's `√(R1 R2)`: its power factor
`1 / (1 − g)²` covers the analysis's build-up at every detuning and equals it on resonance for coatings that do not change with
the angle.

## Several states of one design: configurations

A zoom lens at its focal lengths, a system with a mirror turned in or out of the path, a source switched to another
wavelength: one Project declares the states as `configurations`. Each configuration names what differs from the Project
as written:

* `component_poses`: a component's pose in this configuration (`{"instance_id", "nominal_pose"}`; a moved lens group, a
  turned mirror, a source turned to another field angle). An observation plane attached to a component moves with it;
* `observation_poses`: an observation plane's pose (`{"observation_id", "pose"}`, an image plane refocused);
* `source_models`: a source's emission model (`{"instance_id", "source_model"}`: another wavelength set, power or beam);
* `compute_budget`: budget fields of this configuration; each replaces the Project's field, the others stay the Project's;
* `numerical_settings`: settings of this configuration; each replaces the Project's field (a `design_point` field by
  field), the others stay the Project's.

A target applies to every configuration, or to those its `configuration_ids` names. `ProjectDocumentV2.configured(id)` is
the ordinary Project of one configuration. Every configuration is simulated as declared. An assembly of a laboratory
assembly can move a component only along and about the DOFs it retains (`retained_dofs`): a configuration that moves one
otherwise is an advisory of `project check` (`CONFIGURATION_MOVES_FIXED_DOF`, located `configuration <id> component
<instance_id>`) and refuses an assembly request with the same code; an `OPTICAL_SYSTEM` design places each configuration
freely. Configuration ids name directories: a letter, then letters, digits, `_` or `-`.

`os-optics project run project.json --out-dir run` (`run_configured_project` in Python, `run_project` in the JSON API) runs
every configuration as its own Project -- its own Engine input, budgets and run -- in one invocation, and writes:

```text
run/manifest.json             the Project hash, the configurations input hash, per configuration its directory, Engine
                              input and result hashes, execution (outcome, termination, exhausted budgets) and problems
run/project.json              the Project with its configurations
run/comparison.json           the configurations side by side (analysis document, analysis CONFIGURATIONS)
run/configurations/<id>/      an ordinary run directory of that configuration
```

Every command that reads a run directory reads a configuration's: `os-optics results targets run/configurations/tele`,
`os-optics view run/configurations/wide`. `os-optics results configurations run` prints the comparison: per configuration
its execution and requirements, each target's value and assessment per configuration, and per configuration the power on
every observation plane (with its quantity state) and every arriving beam's chief-ray position, Gaussian radii and spot
RMS radius; `--table` gives one row per quantity and one column per configuration, and `--paraxial G1,G2,G3
--wavelength-nm 587.6` adds the effective and back focal lengths of those components in each configuration.
`--configuration ID` (repeatable) runs only some configurations; `project verify run` checks the whole directory;
`results freshness run --project project.json` answers per configuration (`CONFIGURATIONS_CHANGED` when the Project's
configurations are not the ones run); `coverage` lists the configurations declared, run, not run and no longer declared.
Beside the aggregate, every freshness reply states `physics` (`CURRENT` when the Engine input without its targets is the
run's) and `requirements`: after an edit of the targets alone, or for the configurations a subset run holds, `physics`
is `CURRENT` and the stored quantities are the Project's; a re-run is needed only where it is not.

```bash
os-optics project run zoom.json --out-dir run
os-optics results configurations run --table --paraxial G1,G2,G3 --wavelength-nm 633
os-optics results targets run/configurations/tele
```

Budgets apply per configuration: a configuration whose primary trace runs out of one is `PARTIAL` with its termination
and exhausted budgets (a budget of an auxiliary analysis is listed and leaves it `COMPLETED`), and the other
configurations keep their complete results. A configuration whose Project cannot be lowered (for
example a zoom position whose groups overlap) is kept as `FAILED` with termination `INVALID_INPUT`, its problems and no
`engine_input_hash`, and the others run; `project check` lists each configuration with `ok` and names those in
`not_lowered`. When no selected configuration can be lowered the run is refused and nothing is traced. There is no overall PASS: the exit code applies `--status-contract` over every
configuration (requirements: 3 when any configuration did not complete, else 1 when a target of any configuration is not
met, else 2 when one is undetermined, else 0). Analyses and evaluations of one state -- `project paraxial`, `project
cavity`, `project network`, `project evaluate` -- take the configuration by its id: `--configuration ID` (the request
field `configuration_id` of a paraxial or cavity analysis, `configuration_id=` of `evaluate_project` and of the network
export) analyses `configured(id)`, and the paraxial reply and the cavity document state the configuration. Without an id
a Project with configurations is refused (`CONFIGURATION_NOT_SELECTED`): no configuration is chosen for the caller. A
workspace run takes one configuration's Project (`configured(id)`). A commercial submission carries one Project and one run: a configuration's run directory is
submitted, the run of a Project with configurations is refused (`CONFIGURATIONS_NOT_SUBMITTABLE`).

## External scientific tools

OpenSci works with optimizers, wave-optics and full-wave solvers, quantum-photonics simulators and CAD kernels you run
yourself. `os-optics interop list` names each workflow with its status, how far it is checked: `GUIDANCE_ONLY`,
`DATA_CONTRACT_AVAILABLE`, `VERIFIED_EXAMPLE` (an executed example with pinned versions), `PLANNED` or `DEPRECATED`.
`os-optics interop show ID` (for example `interop show quantum-photonics`; Python:
`os_optics.interop.interop_catalog()`) gives an entry in full: its candidate tools and official links, what OpenSci
provides and what the tool computes, the OpenSci exports and commands to use, the inputs you provide, and the
assumptions and limits. External results stay in their own files and
never change a run or its Local Validation. `os-optics interop list --status VERIFIED_EXAMPLE` lists the entries
with an executed example, and `os-optics interop show ID` names each of them: the tool and its version, the Python
and Engine versions, the date, the example's path and the companion-repository commit it was run at, and its outcome
against the independent reference.

**Power on your optics.** At a glance:

* **What the bounds count.** Every beam the trace follows; beams of one source and line add their fields with open
  phases, so a surface's exposure is a range. With power ratings also the light the trace does not follow (ghost
  reflections, a coating's transmission, light passing an aperture or an element), followed to every surface, and the
  light left out as negligible or of unknown fate whose power is known: at the diffraction limit of its in-phase power,
  times its open paths' crossings and its returns between surfaces facing each other.
* **Closed returns.** Left-out light that may circulate through three or more surfaces of separate elements (a ring, a
  folded or coupled cavity, a periscope's windows through its folds) leaves every surface it may reach without an upper
  bound: its rating comparison and `power_rating_utilization` target stay undecided, an `OUT_OF_MODEL_SCOPE` issue per
  surface names the return's surfaces (`closed_return_surfaces`), and the traced hits and incident power are still
  reported. A stack of parallel planes (windows, plates, flat mirrors on one axis) keeps a bound that counts all its
  returns where a series of them converges, and is treated like a ring where none does.
* **Internal folds** of a plane-faced solid (a cube's diagonal and faces, a penta prism's mirrors, total internal
  reflection there) are counted: the closed walks inside it add to the open paths' count, and a solid whose walks have
  no certified sum is a closed return. **Not counted:** total internal reflection in a curved lens body and light that
  refraction turns back onto a lens or a refracting surface it already passed, before it is reflected again: those
  surfaces keep the pair and open-path bounds. There is no thermal or damage model.
* **Coatings.** A catalog or Project coating keeps its published values beyond its published angles and wavelengths (held
  flat out to 0 / 90° and 100 / 20000 nm), for traced and left-out light alike; a traced beam that meets it beyond its
  published data is listed in `model_scope_issues` (the run at least `OUT_OF_MODEL_SCOPE`, `FAIL` when a target also fails).
* **What decides.** `WITHIN_RATING` when the exposure's upper bound is within a rating that applies as stated, or whose
  test beam is at least as large as the light, and within every rating that may apply; `EXCEEDS_RATING` when its lower
  bound is above a rating that applies as stated, or whose test beam is smaller than the light. A smaller-beam rating --
  and a rating that cannot be checked, whose threshold may be at most its value -- leaves the surface no upper bound
  unless a rating of the same unit that applies as stated or with a larger test beam has a lowest damage threshold no
  larger than its highest (`W/cm` values compared as P/d of the 1/e² diameter).
* **Several elements.** Ghosts whose Gaussian state is not valid and the light left out as negligible count at their
  diffraction limit, so the exposure's upper bound lies far above the traced beams' and a utilization target there
  passes only with a large rating margin. An exceedance in W/cm² or J/cm² is decided at a much smaller margin, since that
  light, of small power, cannot undo a traced beam over the area it covers; in W/cm the margin is larger, since a thin
  sheet of that light meeting a face at a shallow angle keeps its P/d along a strip of the face. Coatings of low
  reflectance on the faces (declared or from the catalog) and fewer surfaces that may reflect the light make the bound
  smaller; where that light alone keeps a rating undecided, the run follows it deeper by itself.

`results.exposures()` (`os-optics results exposures run [--element ID]`) lists every
element surface the beams meet: incident power, both Gaussian beam radii, angle of incidence, footprint, peak irradiance
`2P/(π w_x w_y)`, `P/d` and, for a pulsed source (`source_model.pulse`: repetition rate and FWHM duration; powers stay
average powers; pulse energy `P / f_rep`), the peak fluence. The pulse train changes nothing else: the Engine does not
broaden a pulse by dispersion or separate its lines in time (group-velocity walk-off), reads coatings at the line's
wavelength and does not model a mirror's group-delay
dispersion. A peak fluence is the energy of one pulse per area. A pulse holds every line of its source along one path;
the pulses of different sources, or of one source along different paths, need not arrive together: they are bounded
from below by the strongest single pulse and from above by their sum.
Light of different lines or different sources adds; beams of one source and line are
mutually coherent and add their fields -- also when they cross the surface in opposite directions (a beam and its own
reflection returning: a standing wave) --, so two equal branches of an interferometer reach four times one branch in
phase and nothing in antiphase. With the phases open, such a surface's exposure is a range (`coherent_groups`, reason
`COHERENT_BEAMS_BOUNDED`): at most `Σv + 2Σγ√(v_i v_j)` (the in-phase maximum `(Σ√v)²` when the source declares no
coherence length; otherwise the degree of coherence of the Gaussian spectrum of `L_c` after the two beams' relative
group delay `τ` -- the difference of their group optical paths `Σ n_g L`, not of their phase paths -- and group-delay
dispersion, at its largest over the surface: collinear beams keep about their chief rays' value, beams crossing at an
angle are fully coherent somewhere in their overlap, and a beam that crossed glass other than a plate -- a lens, a prism,
a cube -- is fully coherent with its line, its envelope delay across it not being followed, and so are beams that cross
passes of undetermined group delay differently; a pulsed source's pulses may be mutually coherent, so two of its beams
count as coherent again near every multiple of its period length `c / f_rep`, where the coherence metrics then have no
value), at least `max(0, √v_max − Σ√v_others)²` at a traced beam's centre -- and at least that beam's mean over a window
on its area less what light of unknown place (a ghost of unknown size, the light left out as negligible) can take away
there with its small power; in P/d that light counts as beams crossing the face from one side, each over the strip of
the window it covers. The plane analyses report the same coherence (`group_path_difference_mm`,
`coherence_factor`; `arrivals[].group_path_length_mm`). Light weaker than `negligible_power_fraction` times its line's
input is not followed and paths stop at the power floor; that light still goes somewhere, and focused it can matter
however faint. It is left out in pieces of one line, mutually coherent: where they meet their fields add, so the bounds
use their power together in phase, `P = (Σ√p)²` (`exposures.faint_light[].in_phase_power_W`). With power ratings that `P`
per line -- with the light whose fate the trace does not follow but
whose power it knows: its budget, a path ended out of model scope (`exposures.faint_light`) -- bounds every surface by its
diffraction limit `2πn²P/λ²` (the most light of power `P` crossing a surface from one side can concentrate; `n` the
larger index of the surface's media; for a broad spectrum the mean of `(λ/λ')²` over it, 1.0025 for a 10 fs pulse at
800 nm), times the most its open paths' crossings bring onto a surface or across a plane, `√2 m Σ_{j=0..N} a_j`, at
least 2 (between two reflections the light meets each lens and each refracting surface between
media of different index at most once and travels in at most `L = T + 1` straight legs, `T` those lenses and refracting
surfaces, each meeting any other surface as often as a straight line can, `K` times: a plane once, a sphere or a conic
twice, an asphere by the degree of its sag; so it meets a lens or a refracting surface between different media at most
`m = 1` time and any other surface at most `m = K L` times; after `j` reflections it is made of pieces from different
sequences of `j` of the `N` surfaces that may reflect it, mutually coherent, whose fields add: `a_j` is the smaller of the
sum over those sequences of the products of `m_s √R_s` and the square root of their number times the power they share,
`R_s` a surface's reflectance bound -- 2.26 on the faces of four lenses with 0.25 % coatings, 5.41 on an uncoated
window's faces; a transparent plane's power gains at most `m Σ_{j=0..N} min(1, R^j)`, at least 2, times the pieces'
power, `R` the sum of the reflectance bounds, each times its `m`), and more where two reflecting surfaces facing each other
return it again and again -- a window's, a lens's or a cube's faces, a window and a mirror, the mirrors of a linear
cavity, also with lenses or plates between them: each round trip keeps at most `g = r_a r_b` of its field (the two
amplitude reflectances over every angle at which the pair's light can meet them, below the critical angle), so all its
returns bring at most `g/(1 − g)` times one crossing more, and what each return sends out of the pair -- through its
surfaces, past an aperture's edge, reflected by a surface between them -- is at most that return's field and may reach
any surface: every surface and plane, from either side, gets `f = 1 + Σ g/(1 − g)` over the scene's pairs
(`faint_light_return_factor`; about 1.04 in a scene whose only pair is an uncoated window; `FAINT_LIGHT_RECIRCULATES`):
it adds `2A Σ√v + A²` to the upper bound and
takes `A` off the amplitudes of the bound at a beam's centre (`FAINT_LIGHT_BOUNDED`, `faint_light_peak_W_per_cm2`); a rated surface no
other light reaches lies between 0 and `A²`. The Engine computes with its coating model: a catalog or Project coating
compiles to one that holds its published values beyond its published angles and wavelengths, for traced and left-out
light alike. A pair whose gain bound is 1 (two surfaces that reflect everything, a coating met outside its model's data
-- a declared coating with a stated angle limit --, the faces of an uncoated lens, which light inside it can meet up to
the critical angle) and light
at the per-path interaction limit (light that may be stored in a cavity the traced light shows) bound nothing: no upper
bound on any surface.
Closed returns through three or more surfaces of separate elements (a ring, a folded or coupled cavity, a periscope's
windows through its folds) withhold the upper bound of every surface they may reach, with an `OUT_OF_MODEL_SCOPE` issue
per surface naming the return (`closed_return_surfaces`); a stack of parallel planes keeps an all-orders series of its
returns where one converges (`1/(1 − A)` with `A = Σ m_s r_s < 1`, else the alternating series of its reflections along
the planes' normal) and is withheld the same way where none does (reference: rings, folded and coupled cavities, plate
stacks, `tests/test_closed_returns.py`, `tests/test_closed_return_stacks.py`). A plane-faced solid's internal folds
(and total internal reflection in it) add their certified closed walks to the open paths' count, or make it a closed
return where they have no certified sum (`validation/solid_folds.py`, `tests/test_solid_folds.py`). Not counted: total
internal reflection in a curved lens body; light that refraction turns back onto a lens or a refracting surface it already
passed, before it is reflected again.
Between two parallel planes a refracting
plane parallel to them keeps the light's momentum and counts in its walk; a tilted plate or a wedge beside them, which
shifts the light sideways, is screened as a surface that turns it. Where that light alone keeps a comparison from being decided,
the run follows the line's light deeper (down to the power floor at most) so that the probes' known spots replace the
bound; the probes carry the pupil rays of two whole rings of the beam they leave, so an aberrated ghost or leak counts
with the diffraction limit of its power, never as a paraxial spot. Each surface is compared
with its element's power ratings -- a catalog product's published `power_ratings` or the `power_ratings` you declare for
your own model -- condition by condition (`ratings[].conditions`: `VERIFIED`, `DIFFERS` with its `effect`,
`NOT_VERIFIABLE` with its `reason`, or `NOT_STATED` for a condition that does not apply to the rating, such as a pulse
count to a CW rating). A test condition the rating does not state -- its test beam diameter, a pulsed rating's
repetition rate or pulse count, its polarization, its value's definition -- is not verifiable
(`RATING_INFORMATION_MISSING`): the threshold depends on it.
* regime, wavelength within 2 %, pulse duration (a rated duration read as a FWHM) and repetition rate within 10 % --
  otherwise the rating does not describe
  this light (no scaling between wavelengths, durations or rates); followed light of a line the rating is not for that
  carries at most 1 % of the most light the surface can receive, in peak irradiance and every quantity that gives a
  comparison its direction, counts in the exposure but not in these conditions (a note names it); light left out as
  negligible of a line the rating is not for leaves each condition that line differs in not verifiable once it may carry
  more than 1 % of the most light on the surface (in peak irradiance or in the rating's own quantity, whichever share is
  larger); a surface only that light
  reaches is compared with its lines' regime, wavelength and pulse train, and where light may be stored between surfaces
  facing each other (nothing bounds it) a line the rating is not for leaves its conditions not verifiable whatever its
  power;
* the test beam on the sample (`beam_diameter_mm` and its definition, a diameter without its definition read as the 1/e²
  to the FWHM width) against the 1/e² size of every beam on the surface:
  within 5 % of a stated `1/E2` or `FWHM` diameter it is verified; a beam no larger than the test beam (its footprint on
  the surface included) has a damage threshold at least the rated value; a beam narrower nowhere than the test beam has a
  threshold at most the rated value (small test spots overstate the threshold of larger beams). A diameter whose
  definition the rating does not state is read as a 1/e² diameter from the stated value to 1.70 times it (FWHM, 1/e,
  second-moment and 1/e² readings of a near-Gaussian beam): a beam between those readings, or one a reading would
  verify, is not verifiable. On a metallic
  reflector a pulsed rating keeps no direction across beam sizes unless it is a single-pulse rating applied to one pulse
  per site; a `W/cm` rating keeps none for a larger beam. Light whose size is not known (light left out as negligible,
  untraced light bounded by its diffraction limit) and the weakest beams that carry, with the followed light of unknown
  size, at most 1 % of the most light the surface can receive in every quantity that gives the size its direction (the
  rating's own, and for a CW
  rating the power and P/d, for a pulsed one the peak fluence and the pulse energy: a faint ghost of another size; a
  beam whose chief ray misses the surface counts with its tail there) do not decide the size; beyond that share such light
  keeps a smaller-beam or matching rating from applying (`BEAM_SIZE_UNKNOWN`);
* the pulse count against the pulses one site of the surface receives: every crossing of a source's light there brings
  that source's `pulse.total_pulses` (how many pulses it sends while the optics stay in place: a whole number, or
  `UNLIMITED` for a source that keeps pulsing as long as they are in use; the lines of one source along one path are one
  crossing); crossings that together carry at most 1 % of the most light on the surface do not count. A rating's count
  is compared per tested site where that is what it means -- a single-pulse rating, or a count the rating states as
  applied at one fluence level to each tested site (`test_protocol` `S_ON_1`) --, and damage is irreversible: every site
  receiving the test's count is verified, more pulses give a threshold at most the rated value (only an exceedance is
  decided), fewer a threshold at least the rated value (only "within"); crossings that may bring a site fewer or more are
  `PULSES_PER_SITE_UNDETERMINED`; a source that states no `total_pulses` leaves it not verifiable
  (`DESIGN_INPUT_MISSING`). A rating that states no count, or a count above 1 without saying how it was applied -- every
  multi-pulse catalog rating --, is not verifiable whatever the totals (`RATING_INFORMATION_MISSING`); continuous light
  differs, as its regime does;
* the test polarization (`s-polarized` / `s` / `TE`, `p-polarized` / `p` / `TM`, `linear`, `circular`, `unpolarized`:
  every beam within 1 % of one common state, so that any mixture of them holds it -- beams of another state that carry at
  most 1 % of the most light count in the exposure, not in the state; a rating whose text names another state
  than its machine-readable `polarization` is refused; one stated in other or more words, s or p at normal incidence,
  beams of different states whose mixture is not decided, or light left out as negligible, of a state the trace does not
  know, that may carry more than 1 % of the most light (in peak irradiance or in the rating's own quantity, whichever
  share is larger) is not verifiable).

The rated value is a damage threshold for the peak exposure the Engine computes as its definition establishes one: a
`J/cm2` or `W/cm2` value stated as a peak value (`value_definition` `PEAK`) is the threshold; one stated as the total
energy (or power) over the area of the test beam's 1/e² diameter (`TOTAL_PER_1E2_AREA`) is half a Gaussian beam's peak,
so the threshold is twice it (`ratings[].rated_threshold_low` / `_high`). A value whose definition is not stated -- every
catalog rating -- establishes no threshold: its `value_definition` condition is not verifiable and the comparison is
`CONDITIONS_NOT_VERIFIED`, with the exposure reported. A `W/cm` value (P/d) is its own threshold. A `TYPICAL` value bounds
the threshold of no particular part. Fluence and irradiance are
compared in the beam's transverse plane (on a 45° face the fluence on the surface is cos 45° of it); no rating states the
angle of incidence or the plane of its test.
The rating's `applicability` follows: `AS_RATED` (every stated condition verified: `WITHIN_RATING` when the exposure's
upper bound is at most `rated_threshold_low`, `EXCEEDS_RATING` when its lower bound is above `rated_threshold_high`, else
`UNKNOWN`),
`THRESHOLD_AT_LEAST_RATED` (only `WITHIN_RATING` is decided), `THRESHOLD_AT_MOST_RATED` (only `EXCEEDS_RATING`), a
`TYPICAL` value (neither),
`NOT_VERIFIED` (`CONDITIONS_NOT_VERIFIED`, the exposure -- exact, or its bounds -- still reported) or `NOT_APPLICABLE` (a condition
differs with no established relationship, or two in opposite directions: `CONDITIONS_MISMATCH`, never an exceedance). A
`W/cm` rating that does not state its diameter definition is compared with P/d from the 1/e² to the FWHM diameter. An
exceedance raises the notice `POWER_RATING_EXCEEDED`, which does not change the run's status; a surface is `UNKNOWN` when
the range straddles the rating (no valid Gaussian beam there, coherent beams with open phases, an incomplete trace, or
light the trace does not follow -- ghost reflections, a mirror coating's transmission -- that may reach it: with ratings
that light is followed to the elements), and `RATING_MISSING` without a rating. Light whose power is out of model scope (behind
a crystal whose conversion of a pulsed source is out of model scope, a variable attenuator varying across the beam, an element
part of the beam meets beside its chief ray) has no exposure there, and a `power_rating_utilization` target of the element
is `OUT_OF_MODEL_SCOPE`. A target on `power_rating_utilization`
(`device_instance_id` = the element) makes the rating a requirement: the element's utilization (exposure / damage
threshold, from `rated_threshold_high` below to `rated_threshold_low` above) lies between the largest
lower bound of the ratings that apply as stated or with a threshold at most rated and the largest upper bound of those
that apply as stated or with a threshold at least rated and of every rating that may apply (none while a rated surface
has none: a rating with a threshold at most rated, or one not verified, leaves none unless a rating of the same unit that
applies as stated or with a threshold at least rated has a lowest threshold no larger than its highest, W/cm values
compared as P/d of the 1/e² diameter; where a rating with a threshold at most rated has only larger ones the ratings
disagree and the lower bound stands); the target PASSes
when that interval is within the limit and FAILs when its lower end is not, its value is the largest exposure / damage
threshold of the ratings that apply (the exposure exact, or its lower bound; no value when none of those exposures is
known or bounded from below), and its message names what makes the utilization an interval. For a pulsed
source the peak irradiance is the spatial peak of the average irradiance; the pulse is described by its energy and peak
fluence. There is no thermal or damage model.

**Two-colour alignment.** `centroid_separation_mm`, `normalized_centroid_separation`, `angular_separation_rad`,
`geometric_overlap_ratio` and `waist_position_separation_mm` compare two beams on a plane -- two lines of one source
(`wavelength_nm`, `wavelength_nm_b`) or two sources (`source_id`, `source_id_b`) -- by their chief rays and Gaussian
footprints. They are geometric co-alignment measures, not interference: beams of different wavelengths are compared
directly.

## The catalog and the model basis

`os-optics catalog list` / `load_public_catalog()` give the active release of the OpenSci optical catalog: lenses
(plano-convex / -concave, bi-convex / -concave, positive / negative meniscus, best-form, aspheric, cylindrical, cemented
achromatic doublets), flat, concave and off-axis parabolic mirrors, plate and cube beamsplitters, polarizing cube
beamsplitters, beam samplers, dichroic mirrors, long-pass / short-pass / band-pass / notch / neutral-density filters,
right-angle and equilateral prisms, windows, half- and quarter-wave plates and linear polarizers. Catalog schema 3.5
describes polarizing prisms (Glan-Taylor, Glan-laser, Glan-Thompson, Wollaston, Rochon): two uniaxial crystal prisms with
their optic axes, joined face to face or across an air gap, traced like a Project's cemented crystal prisms and drawn as a unit of
their bodies. A release in catalog schema 3.6 also publishes design materials: materials no product is made of,
published for designing with them (they are in the release's materials, not in its product list). Every product has its specifications, published simulation model and design
geometry, an English name (`display_name`) and a Simplified Chinese name (`display_name_zh_cn`); `optical_sku`
(`OS-OPT-...`) is the product identity. On Windows, set `PYTHONIOENCODING=utf-8` to see the Chinese names directly in
`catalog list` output; otherwise they are printed as JSON `\u` escapes.

**Orderable, mounted, housed.** Every product states its `commercial_readiness`: `ORDERABLE` (OpenSci supplies it)
or `SIMULATION_ONLY` (for design and simulation; in an ordered design you supply it yourself, and a design made only of
simulation-only products cannot be ordered). Prefer `ORDERABLE` products when the design is to be bought from OpenSci.
An ORDERABLE product also states `opensci_mounting`: `AVAILABLE` -- bought with OpenSci mechanics (a complete system,
mounts per optic, or a part list with derived mechanics) it comes with an OpenSci mount, post and post holder; or
`NOT_AVAILABLE` -- OpenSci has no mount for it, so it is supplied without mounting hardware: bought as optics only or on a
part list it ships like any optic; in a design bought with OpenSci mechanics it ships unmounted and you mount it at its
design position (the layout keeps its space and beam path clear, and the OpenSci assembly service is not offered for that
design); a part list with derived mechanics cannot derive its mount (`MOUNTING_NOT_AVAILABLE`). `delivered_form`
`HOUSED` means the delivered part is the optic in its manufacturer's housing: the optics are those of the element, the
outline is the housing's, so allow for its size. `os-optics catalog list` shows the readiness in each row and, for
an ORDERABLE product, its OpenSci mounting (`--readiness ORDERABLE`, `--mounting AVAILABLE` filter by them).

**Finding a lens by focal length.** The focal filters compare the signed nominal focal length a product publishes
(`focal_length_mm`, at its `design_wavelength_nm` when it states one), in millimetres. Negative lenses lie below zero, so
bounds are signed. Compose them with every other filter and with paging; `total` counts the matches.

```bash
os-optics catalog list --category LENS --focal-length-mm -50                           # exactly -50 mm
os-optics catalog list --category LENS --focal-length-mm -50 --focal-tolerance-mm 5    # -55 mm to -45 mm
os-optics catalog list --category LENS --focal-min-mm 140 --focal-max-mm 160           # 140 mm to 160 mm, inclusive
os-optics catalog list --category LENS --focal-sign negative --limit 20                # any negative lens
```

The same in Python: `catalog.select(category="LENS", focal_min_mm="-60", focal_max_mm="-40", focal_sign="negative")`, and in
the JSON API: `catalog_products(focal_length_mm=..., focal_tolerance_mm=..., focal_min_mm=..., focal_max_mm=..., focal_sign=...)`.
`--focal-tolerance-mm` is your own search tolerance, separate from the `focal_length_tolerance_percent` a product publishes.
A product that publishes no focal length (a flat mirror, an axicon) never matches. A filtered response carries
`focal_filter`: your query, the bounds compared and `excluded_without_focal_length`, how many otherwise matching products
publish none. A malformed or self-contradictory query is refused with a reason (`FOCAL_VALUE_INVALID`,
`FOCAL_BOUNDS_CONFLICT`, `FOCAL_TOLERANCE_WITHOUT_VALUE`, `FOCAL_TOLERANCE_NEGATIVE`, `FOCAL_RANGE_INVERTED`,
`FOCAL_SIGN_UNKNOWN`, `FOCAL_SIGN_CONTRADICTS_RANGE`): on stderr with exit code 64 from the command line, in
`details.reason` of `CATALOG_QUERY_INVALID` from the JSON API. `--wavelength-nm` keeps its meaning (a simulation model valid at
that wavelength) and does not change the value compared; `project paraxial` gives a placed component's focal distances at a
chosen wavelength.

**Finding a material.** `os-optics catalog materials` lists published materials -- with their identity (maker,
designation, aliases, catalogue tags, anneal or other variant base, the maker's ordering status, source edition), valid
band (vacuum wavelengths), dispersion law, wavelength / index convention, measured bulk loss and thermo-optic data --
filtered by maker, designation (exact or `--loose`), text, the wavelength range the material covers and stated bulk loss
there. `os-optics catalog material NAME` resolves one name exactly as prescription import does and prints it (an
ambiguous name: its candidates, exit 1). Selection on infrared (or any) wavelengths uses the Engine's own material
evaluation, never the visible nd / Vd: `--at-nm` the index (and group index / GDD where the law supports a derivative
claim) at a wavelength, `--band-nm LO HI` the index at both ends and the middle and the dispersion figure
`(n_mid - 1) / (n_lo - n_hi)`, `--thickness-mm` the internal transmittance of that thickness from the measured bulk loss
(`null` with the reason where no loss is stated: missing loss data is not 100 % transmission; surface reflection and
coatings are separate).

```bash
os-optics catalog materials --manufacturer CDGM --designation D-K9-25                       # one maker's grade
os-optics catalog materials --covers-nm 8000 12000 --with-bulk-loss \
    --at-nm 10000 --band-nm 8000 12000 --thickness-mm 2                                    # long-wave IR candidates
os-optics catalog material CDGM:F5                                                          # F5 of one maker
```

**Model basis.**

* Surfaces are planes, spheres and sag surfaces (conic, even asphere, cylinder, off-axis sections). Lenses, windows and
  refracting prisms are traced surface by surface through their prescription with the dispersion of their glass
  (`SURFACE_PRESCRIPTION`); mirrors, beamsplitters, beam samplers, prisms with a coated face and some band-pass filters
  and dichroic mirrors answer with their specified coating envelope (`SPECIFICATION_ENVELOPE`); waveplates, polarizers
  and the other filters and dichroic mirrors are ideal thin elements (`IDEAL_THIN_ELEMENT`, Jones matrices). Each
  product's `fidelity` names its basis. Cubes, prisms, wedged plates and off-axis mirrors are traced as a set of
  surfaces in 3D (`SURFACE_SET`).
* Rays follow each beam's chief ray plus deterministic pupil samples, with one optional Fresnel reflection per
  interface; `os-optics results ghosts` follows every interface's reflections up to a bounce order.
* Gaussian beams are paraxial (x/y-independent q-parameters); polarization is Jones calculus (a partially polarized or
  unpolarized source is traced as two mutually incoherent orthogonal components whose Stokes parameters add);
  interference is an analytic two-beam model; materials are evaluated at their catalogue temperature.
* Uniaxial birefringent crystals (calcite, quartz, YVO4, ...) are a material kind of the Engine request (`UniaxialMaterial`:
  ordinary and extraordinary index laws and an optic axis, in the scene frame or a component's frame): light entering a
  plane crystal face splits into an ordinary and an extraordinary beam, the extraordinary ray walks off its wave normal,
  each beam carries its own index along its ray, polarization and share of the power, and the faces recombine directions
  (beam displacers, Wollaston, Rochon and air-spaced Glan prisms). The split is exact at normal incidence; elsewhere it is
  approximate and warned (`docs/engine/PHYSICS_SCOPE.md`, P5). A Project declares a crystal as a material with `birefringence`
  (its extraordinary index; `dispersion` is the ordinary one) and each `SURFACE_SEQUENCE` / `SURFACE_SET` model made of it
  states the optic axis in its own frame (`optic_axes`); the native classes `splitter.beam_displacer` and `polarizer.glan`
  build such elements. Cemented prisms are `COMPOUND` units whose members are joined face to face (`joints`: an optical
  contact between two crystals cut differently, or a declared cement layer between parallel faces); the native classes
  `splitter.wollaston`, `splitter.rochon` and `polarizer.glan_thompson` build them. An observation plane is placed outside
  crystals: one whose `medium` is a declared crystal is refused (`BIREFRINGENT_MEDIUM_UNSUPPORTED`).
* Radial gradient-index rods (GRIN lenses): a Project material with `gradient` (`n(r)^2 = n0^2 (1 - (g r)^2)`, `g` constant or
  per wavelength) fills the gap between two faces of a round `SURFACE_SEQUENCE`; every ray crosses the rod in closed
  form (`x(z) = x0 cos(W z) + (x0' / W) sin(W z)`, meridional and skew alike), with its optical path, length, polarization and
  the Gaussian beam's first-order transfer, and the paraxial analysis uses the GRIN matrix (`docs/engine/PHYSICS_SCOPE.md`, P2).
  The native class `lens.grin_rod` builds one from a length or a pitch.
* Axial gradient-index media: a Project material with `axial_gradient` (`n(z) = n0 + a1 z + a2 z^2 + a3 z^3` along the
  element's local Z from the medium's entrance, `n0` its `dispersion`) in the same places; a ray keeps its transverse optical
  direction, its path, length and optical path are integrals of smooth functions (Gauss-Legendre), its polarization turns with
  it in one plane, and the paraxial gap is `((1, int dz / n), (0, 1))`.
* A gradient rod's end faces are planes across its axis, spheres or conics centred on it (a `SURFACE_SEQUENCE`), or planes at
  any slant bounding a round body of the medium (a `SURFACE_SET` with `side_radius_mm`: an angled fibre-coupling face). Light
  reaching the rod's side, an element or observation plane inside the rod, a ray that would turn back in an axial medium and a
  ray too oblique to a slanted or curved face to cross it once are outside the model (`GRADIENT_INDEX_UNSUPPORTED`): where
  part of a beam does so, its power behind the rod is out of model scope.
* Wave-optics quantities as run targets (propagation through apertures and pinholes, PSF, MTF, far-field patterns), stray light and
  scattering are reported `OUT_OF_MODEL_SCOPE`. The scalar low-NA focal field of a pupil is `os-optics results pupil-field`, the vector
  focal field of an ideal objective `os-optics results focal-field`, the point spread function and MTF of a run's image plane
  `os-optics results imaging`, its interface-reflection ghost images `os-optics results ghosts`.
* Diffuse scatter of a rough surface (`scatter` on a declared model's surface: `LAMBERTIAN` or `COSINE_POWER`, the scattered
  share and side) is traced as rays from where each beam meets it and adds to every plane's power (`sampled_light`); bulk
  scattering of a declared material (`scattering_coefficient_per_mm`, `anisotropy_g`) attenuates the beams and is booked
  `BULK_SCATTER`, not followed, so the planes its light reaches read a power that is not complete
  (`SCATTERED_LIGHT_NOT_FOLLOWED`). A thin sample of stated amplitude and phase (uniform, linear ramp, step, Zernike terms or a
  sampled map) sits in an `APERTURE_STOP`'s opening (`sample`): rays take its optical path and phase gradient, the beam its
  Gaussian-weighted transmittance. Triangle-facet surfaces (`MeshSurface` in an Engine request, `FACET_SOLID` in a
  Project) are traced facet by facet: coplanar edge-connected facets of one interaction form one planar face whose clear
  aperture is its outline (up to 1024 corners: a round face of a tessellated window or rod is one face), so a polyhedral
  part (a prism, a corner cube, a penta prism with mirrored faces) is traced exactly. Rounded or single-precision CAD
  coordinates keep a flat face whole: facets are coplanar when their corners lie within a tolerance read from the
  coordinates: 4 sqrt(3) times their rounding (half a unit of the last decimal given, or half the single-precision
  spacing for float32 data such as binary STL), at least 1e-10 mm and at most 1e-4 of the part's size; the face is
  traced as the plane fitted to its corners, each within the face's stated deviation of it. Facets approximating a
  curved surface (`TESSELLATED_FROM_CAD`) are traced as the facets, with no Gaussian beam
  behind them (`GAUSSIAN_UNDEFINED_AFTER_FACETS`). The share of a beam beyond a face's edge is not followed by that beam
  (`FACET_EDGE_DIVIDES_BEAM`, booked with an unknown fate). An open facet sheet between two different media, a uniaxial
  crystal on a side of the facets, more than 1024 planar faces and a closed surface that does not bound consistent solids
  (shells that cross, overlap or touch, a corner where the surface touches itself, a shell facing the wrong way for its nesting)
  are refused as out of the model's scope (`FACET_SURFACE_NOT_TRACED`, termination `OUT_OF_MODEL_SCOPE`, with the reason);
  separate solids, hollow solids (the cavity's facets facing into it) and solids in cavities are traced. The faces are found
  from the vertices as written; a `placement` (a rigid pose of the vertices) moves them after. A face fitted to facets whose
  corners lie farther from it than 1e-5 of the part's size is stated in a notice (`FACET_FACE_FITTED`). A mesh that breaks
  the contract's checks is invalid input.
* The weak-signal chain: extended emitters, bulk absorption, the camera response (`os-optics results detector`), the
  static acousto-optic deflector, fluorescence and Raman emission of a sample (`FLUORESCENCE` / `RAMAN` sources whose line
  powers follow from the excitation the run delivers: `os-optics results sample-emission`), and a plane's detector with its
  bandwidth, noise and a chopper-and-lock-in measurement (`detection`: `os-optics results detection`, target metrics
  `detector_bandwidth_Hz`, `detector_snr`, `lock_in_snr`) are modelled. Modulator timing and a spectrometer's instrument
  function are not: a target on them (`spectral_resolution_nm`) reads `OUT_OF_MODEL_SCOPE`, and the model kinds `CHOPPER`
  and `LOCK_IN` are refused (`SIMULATION_MODEL_UNSUPPORTED`) with the route that declares them (an observation plane's
  `detection`); `capabilities()["model_scope_boundary"]` lists every item with its disposition.
* Second-harmonic generation in a declared `NONLINEAR_CRYSTAL` (a uniaxial crystal slab) is computed for a TEM00 beam with
  an undepleted pump (near field, or Boyd-Kleinman focusing) and traced as a new spectral line; pump depletion beyond
  `nonlinear_depletion_rel_tol`, a fundamental of several lines or polarization components, a pulsed source, a line wider
  than the phase matching allows (`nonlinear_linewidth_rel_tol`), cascaded crystals and second passes, biaxial and cubic
  crystals, and other nonlinear processes (sum- and difference-frequency mixing, parametric processes, chi(3)) are out of the
  model's scope (`docs/engine/PHYSICS_SCOPE.md` P10l). No crystal data are built in: the index laws, `d` (with a quasi-phase-matching
  grating the unpoled coefficient, e.g. `d33`; the run applies `2 / (m pi)`) and thermo-optic data are stated.

## A fluorescence measurement with a chopper and a lock-in

A sample excited by your own laser is a source whose emission the run derives; a plane with a stated detector reads it:

```json
{"instance_id": "S", "optical_role": "source", "custom_part": {"custom_part_key": "cuvette", "description": "Dye cuvette."},
 "nominal_pose": {"position_mm": [0, 0, 0], "quaternion_wxyz": [0.5, 0.5, 0.5, 0.5]},
 "source_model": {"model_kind": "FLUORESCENCE", "excitation": {"observation_id": "sample_plane", "wavelength_nm": 488},
                  "absorbance": 0.0001, "quantum_yield": 0.9,
                  "emission_lines": [{"wavelength_nm": 520, "photon_fraction": 0.6}, {"wavelength_nm": 540, "photon_fraction": 0.4}],
                  "emitting_surface": {"shape": "DISC", "diameter_mm": 0.02}, "angular_law": "ISOTROPIC",
                  "sampling": {"sample_count": 16384, "seed": 1}}}
```

The power of 488 nm arriving at `sample_plane` gives each line `P_abs x quantum yield x photon share x 488 / lambda` (photon
numbers, each photon at its own wavelength; photon fractions that do not add to 1 are read as relative shares), traced as
an isotropic emitter. The model holds for a thin, optically dilute sample (`absorbance` at most 0.1, `absorbed_fraction` at
most `1 - 10^-0.1`); a denser sample is computed by the same formula and its `sample_emission` record carries `validity`:
the exceeded condition with the observed value and `within: false`. On the detector plane:

```json
"detection": {"detector": {"quantum_efficiency": {"rows": [{"wavelength_nm": 400, "qe": 0.8}, {"wavelength_nm": 700, "qe": 0.8}],
                                                  "source": {"kind": "DATASHEET", "reference": "photodiode datasheet"}},
                           "electrical": {"bandwidth_Hz": 10000, "bandwidth_definition": "FIRST_ORDER_3DB", "dark_current_A": 1e-12,
                                          "load_resistance_ohm": 1e8, "temperature_K": 295,
                                          "source": {"kind": "DATASHEET", "reference": "photodiode datasheet"}}},
              "lock_in": {"modulation": {"waveform": "SQUARE", "frequency_Hz": 1000, "duty_cycle": 0.5, "source_ids": ["LASER"]},
                          "time_constant_s": 0.1, "filter_order": 2}}
```

`os-optics results sample-emission RUN` shows the derivation, `os-optics results detection RUN --monitor D` the photocurrent,
shot / dark / thermal noise, `detector_snr` and the lock-in's signal, noise and `lock_in_snr`; targets on `detector_snr`,
`lock_in_snr` and `detector_bandwidth_Hz` are graded on them (`docs/engine/PHYSICS_SCOPE.md` P9c, P9d). The photocurrent and
`detector_snr` are those of the light as traced: a chopped source in its open state. The modulation must lie within the
detector's bandwidth (here 1 kHz against a 10 kHz first-order response, whose `|H(f)|` the lock-in's signal and noise carry);
above it the lock-in's noise and SNR are not stated and a target on `lock_in_snr` reads `UNKNOWN`; a square chop above it
never lets the detector reach its open state, so `detector_snr` and the shot and total noise are not stated either. An optional
`electrical.saturation_current_A` states the photocurrent's linear range; above it the photocurrent and what reads it are not
stated. Light left out as negligible may still reach the detector: a target on `detector_snr` or `lock_in_snr` is graded on
the interval that light leaves (as steady current it lowers `lock_in_snr`, as signal it raises it); on a plane whose power is
an upper end (a share taken before it, an annular beam counted whole) the `detector_snr` interval starts at the SNR of the
least power the plane's entry allows, below the traced value, and a target is graded on that interval whether or not light
was left out as negligible (that light only widens its upper end). Where that light may
pass the saturation current or be chopped above the bandwidth, the target names the open check in `validity` and is graded
only on what holds without the detector model. Quantities read from a sample's sampled rays carry `sampling_relative_sigma`.

## Your own optics: declared optical models

A Project can declare optical models by their physics, without a catalog product: a lens of your own prescription, a
multi-element objective, a pinhole, a slit, an ideal lens. A declared model is compiled by the same rules into the same
Engine surfaces as a catalog product, traced by the same Engine, checked against its own band and coatings, and drawn in
the 3D view as its real body. A Project made only of declared models needs no catalog.

* `optical_models`: each model has a `model_id` and a `model`:
  * `SURFACE_SEQUENCE`: coaxial surfaces along local +Z (plane, sphere, conic, even asphere, cylinder and acylinder,
    the axicon cone term `linear_sag_coefficient`, and the two-variable sag shapes `sag`: Zernike, XY polynomial, biconic,
    toroid; circular or rectangular, optionally decentred clear apertures) and
    the media between them. `clear_shape` makes a clear aperture an ellipse (inscribed in `clear_half_widths_mm`), a
    D shape (`clear_radius_mm` cut by a flat: `flat_mm`, `flat_side`) or a simple polygon (`vertices_mm`, a triangle
    included, counter-clockwise inside the stated circle or rectangle); `holes` open holes through the element (a holed
    mirror, a perforated plate): there is no surface in a hole, light passes straight through it. `AIR` is a valid gap medium, so an air-spaced objective of up to 16 surfaces is one model.
    Each surface may state its own mechanical edge (`edge_radius_mm` on the surface; the model's `edge_radius_mm` is
    the largest one): the surface reaches its edge and is a flat flange beyond it, so the lenses of an air-spaced group
    keep their own diameters and edge thicknesses (the trace blocks light on that flange zone at the surface's continued
    profile: `KNOWN_LIMITATIONS.md`). Behind an axicon the light forms a Bessel-like zone: rays and powers are traced, Gaussian beam metrics are out of
    model scope (`GAUSSIAN_UNDEFINED_AFTER_AXICON`);
  * `SURFACE_SET`: surfaces posed anywhere in the element frame, optionally inside a convex solid (cubes, prisms,
    off-axis mirror sections);
  * `POLARIZATION_ELEMENT`: a retarder, linear polarizer or circular polarizer (a linear polarizer and a retarder
    behind it) with its axis on local +X; a retarder's retardance may be stated per wavelength (`TABULATED`: a
    dual-wavelength plate), and `position_parameter` `axis_rotation_deg` turns the axes by the component's selected
    angle (a rotation mount, the half-wave plate of a tunable splitter);
  * `APERTURE_STOP`: an opaque plate with a circular opening (iris, pinhole), optionally with a central obstruction
    (an annulus), or a rectangular opening (a slit), decentred if you like; `opening_shape` makes the opening an
    ellipse, a D shape or a polygon (a triangle, a hexagon). Light inside the opening passes, light on
    the plate is blocked, light beyond the plate's outline passes by. It is a geometric stop: diffraction at the
    opening is not modelled;
  * `IDEAL_THIN_LENS`: a perfect lens of a given focal length at one plane (no thickness, aberration or optical-path
    phase);
  * `TABULATED_THIN_LENS`: a thin focusing element whose response comes from a table -- per wavelength its focal length
    and the fraction of power delivered into the focused beam -- for example a metalens response computed with an
    external RCWA or wave-optics tool. Each source line is focused with its own focal length; the undelivered power is
    booked `UNMODELLED_RESPONSE`. The table alone is a complete model. The model may state where it holds
    (`max_field_angle_deg`, `max_beam_radius_mm`, `validated_polarizations`), which result metrics it was validated for
    (`supported_metrics`, each with its error; `transmittance` covers the power metrics) and its `evidence` (method,
    tools and versions, source-data SHA-256, fit, validation summary, date). A limit or a metric list it does not
    state is not checked, and a table without `evidence` is an assumed response: the run summary's
    `element_models[].response.data_kind` reads `ASSUMED` (`EVIDENCED` with evidence) and shows each unstated item as
    `null`. Light outside a stated domain is an Engine issue of status `OUT_OF_MODEL_SCOPE`
    (`MODEL_WAVELENGTH_OUTSIDE_BAND`, `MODEL_FIELD_ANGLE_EXCEEDED`, `MODEL_BEAM_SIZE_EXCEEDED`,
    `MODEL_POLARIZATION_NOT_VALIDATED`; `model_scope_issues` also lists each with the observations and targets that
    light reaches, whose assessment is then `INDETERMINATE`), and a target on a metric the model was not
    validated for is `OUT_OF_MODEL_SCOPE` (`METRIC_NOT_VALIDATED_FOR_ELEMENT`): it reports the value computed with the
    ideal thin-lens step, marked `ESTIMATE` in `value_basis`, and is not graded; the run is then at least
    `OUT_OF_MODEL_SCOPE`, `FAIL` when a target also fails. The run summary carries the model's domain and evidence. It has no
    optical-path phase, so wavefronts and network exports through it are refused;
  * `DIFFRACTION_GRATING`: a thin periodic plane, `REFLECTION` or `TRANSMISSION`, stated by `period_nm` or `line_density_per_mm`, the grating
    vector's angle, `orders` or `all_propagating_orders`, its media and aperture. Every order's direction follows from the vector grating
    equation, at any wavelength and angle of incidence (`docs/engine/PHYSICS_SCOPE.md`, section P10d). Its `response` gives the order powers,
    with their normalization, polarization basis, domain, interpolation and provenance; without one, or outside it, an order's `power_W` is
    `null` with `power_upper_bound_W`, and its position and direction stay valid. Phase is not stated, so wavefronts and network exports
    through it are refused;
  * `FARADAY_ROTATOR`: a nonreciprocal polarization rotator stated by its response: `rows` of wavelength, `rotation_deg` and
    transmittance, and, when the author has them, `valid_aoi_deg`, `operating_conditions` and `evidence` (without
    `valid_aoi_deg` no angle of incidence is checked; without `evidence` the run summary's `response.data_kind` reads
    `ASSUMED`). Local +Z is the field axis: the field turns
    right-handed about it whichever way the light travels, so a round trip adds the rotation. Light of a wavelength the table
    does not cover stops at the element (`MODEL_WAVELENGTH_OUTSIDE_BAND`, status `OUT_OF_MODEL_SCOPE`). It has no
    optical-path phase, so wavefronts and network exports through it are refused. A polarizer, a rotator and an analyzer make
    an isolator: launch one source from each end and read each on its own (`docs/engine/PHYSICS_SCOPE.md`, section P10c);
  * `NEUTRAL_DENSITY`: an attenuator plane whose optical density may vary across it: `FIXED`, a continuously variable
    wheel (`LINEAR_ROTARY`), a stepped wheel (`STEPPED_ROTARY`) or a linear gradient (`LINEAR_GRADIENT`), with an
    optional uncoated transmittance and reflectance. The density is evaluated where the beam crosses the plane; the
    component's `selected_parameters` entry named by `position_parameter` (`nd_rotation_deg` or `nd_offset_mm`) turns
    the wheel or shifts the gradient. A beam whose density varies by more than 0.2 OD across its footprint is an Engine
    issue of status `OUT_OF_MODEL_SCOPE` (`ND_DENSITY_VARIES_ACROSS_BEAM`);
  * `COMPOUND`: one optical unit (an objective, a relay, a telescope) made of member models at fixed poses in the
    unit's frame, with the unit's envelope and optional ports. The unit is placed as a whole; every member surface is
    traced. Member surfaces are named `<member_id>-<surface_id>` in the Engine (`L1-S1`, `STOP-stop`). A member with
    an `array` (`layout` `RECTANGULAR` or `HEXAGONAL`, `pitch_mm`, `counts`) is a lens array: one cell per lattice
    point, member `<member_id>_<row>_<column>`, each traced as its own element;
  * `FACET_SOLID`: a solid of a declared material bounded by flat triangular facets in the component's frame
    (`vertices_mm`, `triangles` counter-clockwise seen from outside): a prism, a corner cube, a polyhedral window or a
    CAD part exported as facets. The facets close around the solid (every edge on two facets, facing outward); outside
    is air. `approximation` states what they stand for: `FACETED_EXACT` (the part is these facets) or
    `TESSELLATED_FROM_CAD` with `max_chord_deviation_mm` (flat facets approximating curved surfaces); `source` says
    where they come from. Every facet refracts with bare Fresnel power, or with `coating_id`; `facet_groups` give facets
    their own surface: `TRANSMITTED` with a coating, `REFLECTED` (a mirrored face, ideal without a coating) or
    `ABSORBED` (a blackened face). The run traces the facets: edge-connected facets of one group in one plane are one
    planar face, named `<instance>/facets.t<k>` in the results (`k` its first triangle). Facets count as in one plane
    within a tolerance read from the coordinates as given: 4 sqrt(3) times their rounding (half a unit of the last decimal
    of `vertices_mm`), at least 1e-10 mm and at most 1e-4 of the part's size; a face is traced as the plane fitted to its
    corners, and the run summary's `element_models` row states the tolerance (`facets.coplanar_tolerance_mm`) and the
    largest distance of a corner from its face's plane (`facets.max_face_deviation_mm`). A face's outline has up to 1024
    corners (more: its facets are traced one by one). A face whose corners lie farther from its plane than 1e-5 of the part's
    size is stated in a notice (`FACET_FACE_FITTED`): a bend that small is traced flat. As a member of a `COMPOUND` unit the part
    has the faces it has placed directly (the member pose moves the faces found from `vertices_mm` as written). Several separate
    shells, a hollow solid (its cavity's triangles counter-clockwise seen from the cavity) and a solid in a cavity are traced;
    shells that cross, overlap or touch are refused with the reason (`FACET_SURFACE_NOT_TRACED`): state parts in contact as
    separate components. Behind a tessellated surface
    the Gaussian beam metrics are out of model scope (`GAUSSIAN_UNDEFINED_AFTER_FACETS`; rays and powers are traced on
    the facets), and the part of a beam beyond a face's edge is not followed by that beam
    (`FACET_EDGE_DIVIDES_BEAM`: its share is booked with an unknown fate). A part with more than 1024 planar faces is
    refused (`FACET_SURFACE_NOT_TRACED`). Inside a glass solid every pair of faces may return light by total internal
    reflection, so the return-light analysis of the light left out of the trace examines every pair of faces within its
    work limit (`compute_budget.max_return_analysis_work`, 4,000,000 units by default for the whole run, about 15 s on a
    desktop CPU); a part of many faces reaches it and the planes' power upper bounds are withheld (`RETURN_ANALYSIS_BUDGET`).
    One beam through a rod of 50 / 191 / 284 faces runs in 4 / 15 / 18 s (native, one thread). Its body in the views is
    its facets; its design geometry, when not stated, is
    the box around them.
  * `NONLINEAR_CRYSTAL`: a rectangular slab (`length_mm`, `width_mm`, `height_mm`; entrance face at z = 0, light along +Z) of a
    declared uniaxial crystal (`medium`, a material with `birefringence`; its optic axis in `optic_axes`) that generates the
    second harmonic. State `d_eff_pm_per_V`, or `d_tensor_pm_per_V` (contracted 3 x 6, crystal frame: Z the optic axis, X
    `crystal_x_axis`) and the run contracts it with the traced fields; `fundamental_waves` (`OO`, `EE`: type I, or type 0
    with a grating; `OE`: type II) and `harmonic_wave` (`O`, `E`); `qpm_period_um` / `qpm_order` for a periodically poled
    crystal (the stated coefficient is the unpoled one; the run applies `2 / (m pi)`); `temperature_c` with `thermo_optic`
    (`reference_temperature_c`, `dn/dT` rows per wavelength, `thermal_expansion_per_K`); `focusing` `NEAR_FIELD` (thin
    crystal; checked against the focused result) or `BOYD_KLEINMAN`; `nonlinear_basis` says where the coefficients come
    from. The run traces the slab as a crystal, computes the phase mismatch of the traced waves (angle, temperature,
    wavelength, grating), generates the harmonic as spectral line `<source>#<n + j>` (`n` the source's line count) at half
    the wavelength, and lowers the fundamental's power by it (`NONLINEAR_CONVERSION` in its ledger, the harmonic line's
    input). `os-optics results nonlinear` (json_api `run_results` kind `nonlinear`, export kind `nonlinear`) states each pass:
    harmonic power, efficiency, `dk L / 2`, `sinc^2`, walk-off, the depletion and near-field checks and the angular,
    temperature and spectral acceptance; targets `shg_conversion_efficiency` (`nonlinear_conversion_efficiency`) and
    `shg_power_W` name the crystal by `device_instance_id`. Outside the model's conditions the pass is
    `OUT_OF_MODEL_SCOPE` with its reason (`NONLINEAR_CONVERSION_OUT_OF_SCOPE`) and the powers behind the crystal are out of
    model scope.
* `power_ratings` (optional, on any model but a `COMPOUND` unit, whose members carry their own): your element's
  power-handling ratings with their conditions (`regime` `CW` / `PULSED`, `value`, `unit` `W/cm` / `W/cm2` / `J/cm2`,
  `wavelength_nm`, pulse duration and repetition rate, test beam diameter, pulse count, `test_protocol` (`S_ON_1` when
  each tested site received the count at one fluence level), `value_definition` (`PEAK` or `TOTAL_PER_1E2_AREA` for a
  `J/cm2` / `W/cm2` value, when your test report states it), `polarization_condition`, `status` `DECLARED`, `basis`),
  compared with the run's per-surface exposure condition by condition (above). The test beam diameter is the beam on the
  sample. A
  model whose reflecting surface carries a metallic-reflector coating counts as a metallic reflector; a
  reflecting neutral-density element or an aperture stop is of unknown kind, so a pulsed rating with a test beam there
  is not verifiable where a metallic surface would change the result (`RATING_INFORMATION_MISSING`); the polarization is
  checked when written as `s-polarized`, `p-polarized`, `linear`, `circular` or `unpolarized`.
* `model_materials`: the materials of your models (Sellmeier of 1–10 terms, power series, Herzberger, temperature
  Sellmeier, tabulated -- a source formula's table or a measured one -- or constant index; optional bulk
  `absorption_coefficient_per_mm`, or a spectral `bulk_loss`: measured `k` or internal transmittance at a thickness;
  optional `index_reference`: the law's wavelength (`VACUUM` / `AIR`) and index (`ABSOLUTE` / `RELATIVE_TO_AIR`) with the
  source's reference air -- absent, vacuum wavelength and absolute index; optional Schott `thermo_optic`; a catalog copy also
  `copied_from`); `model_coatings`: their coatings (the catalog's coating format). A declared model
  only uses these; in the Engine its materials are `project:<MATERIAL_ID>`, separate from `catalog:<MATERIAL_ID>`.
* `environment`: the medium the optics operate in -- air at `temperature_c` and `pressure_pa` (zero pressure: vacuum),
  default air at 20 °C and 101325 Pa (the glass catalogues' reference air). `AIR` outside the elements and in every `AIR`
  gap is this air (absolute index by the Edlén 1953 formula with OpticStudio's T / P scaling); a declared model's gap may
  state `VACUUM` (a window with room air outside and vacuum inside). Refraction and optical path use absolute indices, so
  replacing an air segment of length `L` by vacuum changes the optical path by `(1 − n_air) L`. Wavelengths are vacuum
  wavelengths in every environment: changing the environment is physics, not notation. Materials with thermo-optic data
  are evaluated at the environment temperature.
  A coating is piecewise-constant `bands`, a tabulated `spectrum` (unpolarized, or with `reflectance_s` / `_p` and
  `transmittance_s` / `_p` per wavelength) or an `angular_spectrum`: s / p tables over wavelength × angle of incidence,
  read at the angle in the medium the light arrives from and interpolated bilinearly. An optional `phase` states the
  coating's reflection and / or transmission phases per polarization (constant, or tabulated over wavelength), with
  `r = √R e^{iφ}`, `t = √T e^{iφ}` in the Engine's s / p triad and the time dependence `exp(−iωt)`: a mirror, splitter
  or coated refracting face then uses them instead of its default phases, and the linear optical network export carries
  them. A coating keeps its published values (and its stated phases) beyond its published angles and wavelengths, for
  traced and left-out light alike; a traced beam that meets it beyond its published bands, spectrum or angles is listed
  in `model_scope_issues` (`COATING_WAVELENGTH_OUTSIDE_BANDS`, `COATING_AOI_OUTSIDE_RANGE`) and makes the Local
  Validation at least `OUT_OF_MODEL_SCOPE`. A coating or model published for a single design wavelength or angle is
  matched within the tolerance in force (`numerical_settings.design_point`); a finding there carries
  `details.design_point`: `quantity` (`angle_of_incidence_deg` / `wavelength_nm`), `design_values`, `observed`, for an
  angle its `reading` (`AIR_EQUIVALENT` / `INCIDENT_MEDIUM`), `tolerance_deg` / `tolerance_rel`, the `setting` that
  holds it and its `source` (`PROJECT` / `ENGINE_DEFAULT`), the `coating_kind`, and `assumed:
  HELD_AT_NEAREST_STATED_POINT` where the run used the coefficient published at the point.
* A reflecting surface (`traced: REFLECTED`) that states no `coating_id` is an ideal reflector: reflectance 1 for both
  polarizations, reflection phases π (s) and 0 (p). The run summary's `element_models` row lists such surfaces in
  `ideal_reflectors`; the light behind them carries no coating loss. A splitting surface with the same medium on both
  sides states its coating (`SURFACE_COATING_REQUIRED` without one).
* A `POLARIZATION_ELEMENT` may resolve its transmittance (and a polarizer's extinction ratio) by wavelength with
  `transmittance_bands`: each spectral line uses its own band.
* Write numbers and text as you have them. A number of a declared model, material or coating is a JSON number or a
  decimal string in any spelling (`100`, `"100.0"`, `"1e2"`); names, descriptions and bases take any Unicode text (no
  control characters). A value that means "absent" may be written or left out: a zero `conic`, axicon term
  (`linear_sag_coefficient`), aperture centre `[0, 0]`, trailing zero aspheric or toroid terms, a zero Zernike / XY sag term,
  an axial gradient of zero terms. A value that follows from others may be left out: a band's or spectrum's unpolarized
  reflectance / transmittance (the mean of its s and p values), a banded polarization element's scalar transmittance /
  extinction ratio (its smallest band value), a sequence's outline (its largest surface edge), and the labels
  `TYPICAL_SPECTRAL_CURVE` / `UNTRACED_FACES_BLOCK` (the run summary's `element_models[].approximations` lists them as they
  hold). `project check` returns the Project in one canonical form and its hash: every spelling of one design has one hash.
  Two statements that contradict each other (a stated mean that is not the mean, a listed label that does not hold) are
  refused.
* A source's `polarization` is linear (`{"kind": "LINEAR", "angle_deg": ...}`) or elliptical
  (`{"kind": "ELLIPTICAL", "orientation_deg": ..., "ellipticity_deg": ...}`; −45° is right and +45° left circular, the
  handedness seen by an observer looking toward the source, the same sign as the results' `polarization_ellipticity_deg`). Both angles are measured in the source's own frame, from
  its local +X toward its local +Y (the beam travels along local +Z), so the source's `nominal_pose` turns them: with the
  examples' pose `quaternion_wxyz = [0.5, 0.5, 0.5, 0.5]` (beam along design +X) local +X is design +Y and local +Y is
  design +Z, so `angle_deg: 0` is horizontal (design Y) and `90` vertical (design Z).
* A component uses a model through a `custom_part` and `optical_model_id`. Its `optical_role` must fit the model:
  an aperture stop is an `aperture`, a thin lens a `lens`, a retarder a `waveplate`, a polarizer a `polarizer`, an attenuator a `filter`, a Faraday rotator or a diffraction grating
  `other_optical`; surface
  models and compound units may play any optical role; `other_optical` fits every model.
* `frame` and `design_geometry` are optional. Without a frame, a transmissive element has its first surface vertex at
  the origin and a reflector or plate splitter its coated face (the last surface, at z = 0); an element placed by its
  ports states its frame. Without a design geometry, the body is the outline between the surfaces' extreme z
  (`NOMINAL`); thin elements (stops, thin lenses, polarization elements, a single plane) have zero thickness.
* `design_intent`: `LAB_ASSEMBLY` (the meaning when absent: a layout built from individually mounted components) or
  `OPTICAL_SYSTEM` (an optical design simulated, inspected and exported without laboratory mechanics). The run summary
  records it; physics does not depend on it.

Start from a catalog product with `os-optics project model-from-catalog OS-OPT-... --model-id my-lens` (or
`declared_copy(product, catalog)` in Python): it prints the product's model, materials and coatings ready to paste into
your Project, and it traces exactly like the product until you edit it.

The following *declared demonstration material* assumes zero bulk loss to illustrate a lossless ideal lens; this is not a measurement of real catalog N-BK7. Without bulk-loss evidence, a real catalog glass yields bounded/unknown transmitted power.

```python
from os_optics.local import run_optical_project

band = {"min_nm": "400", "max_nm": "1100", "status": "SPECIFIED"}
glass = {"material_id": "N-BK7", "display_name": "N-BK7", "valid_band_nm": band,
         "basis": "Illustrative N-BK7 dispersion; zero bulk loss assumed for this ideal example, not measured",
         "absorption_coefficient_per_mm": "0",
         "dispersion": {"kind": "SELLMEIER", "b": ["1.03961212", "0.231792344", "1.01046945"],
                        "c_um2": ["0.00600069867", "0.0200179144", "103.560653"]}}
lens = {"model_id": "my-lens", "model": {
    "model_kind": "SURFACE_SEQUENCE", "model_version": "2", "fidelity": "SURFACE_PRESCRIPTION", "status": "SPECIFIED",
    "surfaces": [{"surface_id": "S1", "vertex_z_mm": "0", "radius_mm": "51.5", "clear_radius_mm": "11", "traced": "TRANSMITTED", "status": "SPECIFIED"},
                 {"surface_id": "S2", "vertex_z_mm": "3.6", "clear_radius_mm": "11", "traced": "TRANSMITTED", "status": "SPECIFIED"}],
    "media": ["N-BK7"], "edge_radius_mm": "12.7", "valid_band_nm": band, "approximations": ["NOMINAL_PRESCRIPTION"]}}
pinhole = {"model_id": "pinhole", "model": {"model_kind": "APERTURE_STOP", "model_version": "1", "status": "SPECIFIED",
                                            "opening_radius_mm": "0.1", "edge_radius_mm": "12.7"}}
along_x = [0.5, 0.5, 0.5, 0.5]  # local +Z along design +X
project = {
    "schema": "opensci.project", "project_schema_version": "2.0", "design_intent": "OPTICAL_SYSTEM",
    "optical_models": [lens, pinhole], "model_materials": [glass],
    "components": [
        {"instance_id": "SRC", "optical_role": "source", "custom_part": {"custom_part_key": "laser", "description": "My laser."},
         "nominal_pose": {"position_mm": [-200, 0, 0], "quaternion_wxyz": along_x},
         "source_model": {"model_kind": "GAUSSIAN_BEAM", "wavelength_nm": 632.8, "power_w": 0.001, "waist_radius_mm": 1.0,
                          "waist_position_mm": 0.0, "m2": 1.0, "polarization": {"kind": "LINEAR", "angle_deg": 0}}},
        {"instance_id": "L1", "optical_role": "lens", "custom_part": {"custom_part_key": "my-lens", "description": "My lens."},
         "optical_model_id": "my-lens", "nominal_pose": {"position_mm": [0, 0, 0], "quaternion_wxyz": along_x}},
        {"instance_id": "PH", "optical_role": "aperture", "custom_part": {"custom_part_key": "pinhole", "description": "My pinhole."},
         "optical_model_id": "pinhole", "nominal_pose": {"position_mm": [101.2, 0, 0], "quaternion_wxyz": along_x}},
    ],
    "observations": [{"observation_id": "after", "pose": {"position_mm": [150, 0, 0], "quaternion_wxyz": along_x}}],
}
run = run_optical_project(project)  # no catalog needed: every optic is declared
print(run.status.value, run.catalog_version, [e["optical_model_id"] for e in run.summary()["element_models"]])
```

### Import, build, inspect and export

* **Import a lens prescription.** `os-optics project import-prescription design.csv --model-id objective --into
  project.json --instance-id OBJ --out project.json` reads a `csv-surface-table/1` surface table (radius, thickness,
  glass, semi-diameters, stop, conic and aspheric terms; mm, cm, m or inch) and adds one `COMPOUND` unit with an element
  per glass group and the stop as an `APERTURE_STOP` member. Glasses the file does not define are copied from the
  catalog. Each problem of the first failing step (table, then glasses and coatings, then geometry) is reported with its
  row and column; nothing is imported from a file with problems. The format (sections, columns, directives):
  `help(os_optics.prescription.csv_table)`.

  *Glass names* resolve with the catalog's own resolver (the one the catalog builder uses): an exact stable id
  (`SCHOTT_F5`); a maker's designation, written `MAKER:DESIGNATION` (`CDGM:F5`) or with a `glass_manufacturer` column; a
  designation or documented alias only one record has (`S-FPL53`, `D-K9-25`); a name several makers use (`F5`) is refused
  as `GLASS_AMBIGUOUS` naming every candidate, unless the file states its catalogue order (`#! glass_catalogs = CDGM
  SCHOTT`, Zemax's `GCAT` line). `ID:F2` names the stable id `F2` only. A loose spelling (`nbk7`) is accepted only when
  unique, with a warning; an exact id another record shares as its designation is reported (`GLASS_ALSO_MATCHES`). The
  report's `glasses` says how each glass resolved. The Project receives the glass's complete optical definition
  (dispersion, valid band, index convention, bulk loss, thermo-optic data) and `copied_from` (catalog release, chosen
  id, source edition): the saved Project reopens and runs with no catalog and no second name lookup.

  *Wavelengths and environment.* The file's wavelengths are vacuum wavelengths unless it states `#! wavelengths_in =
  AIR` (OpticStudio's convention: measured in air at the system temperature and pressure, `#! temperature_c = 20`,
  `#! pressure_atm = 1` or `#! pressure_pa = 101325`; zero pressure is vacuum); air wavelengths are converted once to
  vacuum wavelengths with the shared air model. A file that states any of these carries its environment into the import:
  `--into` sets it on the Project and refuses a Project whose environment differs. Without them the Project's own
  environment applies (air at 20 C and 101325 Pa unless it states another). A `[materials]` law states its own convention
  (`index_reference` `VACUUM` or `AIR`, the glass catalogues' air wavelengths and relative index).
* **Build a supplier-independent component.** `os-optics native list` lists the component classes (stops, slits,
  annular apertures, central obstructions, ideal thin lenses, singlets designed for a focal length, cemented achromats,
  axicons, wedge prisms, windows, layered plates (a cuvette), mirrors, hollow roof mirrors and corner-cube
  retroreflectors, splitters, polarizers, waveplates, Faraday rotators of a stated response, diffraction gratings, variable neutral-density filters, lenses of a tabulated response,
  telescopes and a 4f relay); `os-optics native describe CLASS` gives its parameters and `os-optics
  native build CLASS --param focal_length_mm=100 ... --into project.json --instance-id L1` adds it to a Project.
* **Check the optical bodies.** Every run checks each optical body before tracing: a profile that ends inside its
  outline, glass thinner than zero anywhere across the outline, intersecting surfaces, an inconsistent medium chain,
  overlapping bodies of different elements and members outside their unit's envelope are refused with the element,
  member and surface named. Two surfaces whose own profiles cross inside either one's clear aperture are refused too,
  even where a surface held flat beyond its own edge keeps the drawn glass positive. `os-optics project solids
  project.json` prints the full report, knife edges, touching bodies and edge-thickness findings included: glass thicker
  at its edge than 1.5 clear diameters is a warning, and a lens of your own above 0.35 of its clear diameter is listed
  with its ratio as an INFO finding (`--edge-review-ratio` sets another review ratio). These are checks of the optics themselves; no mount, post
  or breadboard is needed.
* **Look inside.** `os-optics view run --section ALL --snapshot shots` draws the 2D layout of a lens: one exact
  true-scale section through every element on the axis, with each surface's profile, the glass between them,
  clear-aperture marks, the stop, the image plane and the run's rays, framed on the optics. `--section OBJ` does the same
  referred to one element or unit (repeat the flag or separate ids by commas), and `--frame scene` or
  `--frame=X0,Y0,Z0,X1,Y1,Z1` changes what it shows. The 3D view lists every surface of the selected element to show, hide
  or click.
* **Export a linear optical network.** `os-optics project network project.json --out network.json` writes the
  design's optics as an `opensci-linear-optical-network/v1` document: input modes (each source in two polarizations),
  output modes on the observation planes, the complex transfer matrix with its conventions, the path terms behind it
  and a check against the Engine's own coherent result, for use in your own analysis or quantum-optics tools. Optics
  that are not a linear network (a cavity, mixed wavelengths, partly overlapping beams, an ideal or tabulated thin lens on an
  interfering path, ...) are refused with every reason. Each document states its conventions (`conventions`);
  `os-optics schema linear-optical-network` prints the format.

The same functions are available through `os_optics.local.json_api` (`import_prescription`, `native_components`,
`build_native_model`, `catalog_model`, `inspect_optical_solids`, `optical_section`, `linear_optical_network`) for local
JSON callers.

`json_api.project_layout(project)` draws the layout of a Project without a run (`os_optics.optical_view.layout`): its
elements, sources and observation planes at their design poses, drawn as the run's Optical Simulation View draws them,
and each source's nominal beam axis, with a GLB (`optical_layout.glb`). Nothing is traced: the layout holds no beam path,
power, result status or target result (only the design inputs it draws, such as a model's status or an observation
plane's target ids). The nominal axis runs from the source along its emission axis to the surfaces it meets; it turns
at mirrors (law of reflection) and ideal thin lenses, goes straight through a surface that transmits where that needs no
refractive index (the same medium on both sides, or normal incidence) and ends where it would need one or a trace
(`NOT_FOLLOWED`: oblique refraction, a grating, a crystal), at an absorbing plane or the back of a mirror (`ABSORBED`), when
it meets one surface a third time (`RETURNS`), or as it leaves the design (`FREE`). Where it is followed it is the start of
the chief ray a run traces from that source. The JSON API provides it before the Project has a complete run.

`os_optics.local.json_api` (`opensci.local_json_api` 1.9, `capabilities()["json_api_version"]`) answers every call with JSON-safe data.
`json_api.capabilities()["json_api_methods"]` lists its methods, and `json_api.call_method(method, params, file_map=...,
data=...)` calls one by name with its parameters as a JSON object (the files or bytes a method takes go in `file_map` /
`data`; the reply's files come back beside it): the entry point for a local host that is not a Python program.
An unknown method answers `METHOD_NOT_IN_RUNTIME`, parameters that do not fit it `INPUT_INVALID`.

## Compose systems from reusable layouts

Design a large system as modules: author and validate each stage as its own Project, export it as a module, import it
into the system as many times as you need, arrange the instances and validate the whole system.

```
os-optics workspace init fold.json --module fold.descriptor.json -o fold.ws.json   # mark test fixtures, interfaces
os-optics workspace run fold.ws.json                                               # the module on its own
os-optics workspace export fold.ws.json --descriptor fold.descriptor.json -o fold.module.json
os-optics workspace init system.json -o system.ws.json
os-optics workspace import system.ws.json fold.module.json --key a --rebind SRC=LASER --op-id import-a
os-optics workspace import system.ws.json fold.module.json --key b --rebind SRC=LASER --position 0,100,0 --op-id import-b
os-optics workspace transform system.ws.json --instance b --axis 0,0,1 --angle 90 --pivot component:b_M1 --op-id turn-b
os-optics workspace run system.ws.json --out-dir run
os-optics workspace status system.ws.json      # which validations are current, instances, groups
```

* Each import is an independent instance: ids become `<key>_<id>`, also those of the models, materials and coatings it
  copies (every instance owns its definitions, so editing one instance never changes another), the module's test
  sources, screens and targets are removed, kept or rebound to the system's own objects, and its requirements are
  carried. The result lists the id map and every fixture outcome.
* Editing groups (one per import to start with) can be redefined at any time, across imports and with your own elements;
  they never change the Project. `transform` moves any selection as one rigid body with an explicit frame and pivot, and
  reports every moved pose. An instance moved as a whole carries the instances nested in it, also one whose members
  were all deleted, so replacing that one later puts its members where the enclosing module has them.
* Every change is atomic, and `--op-id` / `--expect-base` make retries safe: an operation repeated with the same `--op-id`
  is never applied twice. A command without `--op-id` is always applied as a new operation.
* `workspace update-project system.ws.json edited.project.json` takes a Project you edited elsewhere; groups drop the
  objects it no longer has. A deleted member of an instance stays recorded as an edit of that instance: `status` lists
  it, and `replace` brings it back only with `--discard-edits`. The edits of the instances nested in an instance of a
  composed module (also the members a nested instance gained when it was replaced on its own) are edits of the enclosing
  instance too.
* `workspace replace` writes the new revision's content exactly, however small a changed value; only an unedited
  component or observation plane whose module object the revision has unchanged keeps its exact content (so a
  whole-instance move or rotation never turns into an edit). A model, material or coating of the instance that an object
  outside it uses is never changed or removed: it stays with its content, the instance keeps it while its revision
  lacks it, and the result names it with those objects (`kept_definitions`). A revision with the same definition uses
  it as imported; one that defines its id differently is refused (`ID_CONFLICT`, `used_by`) until those objects use
  another definition. Replacing a nested instance by a revision that changes no object (also after a whole-instance
  rotation and a catalog re-pin), discarding an edit by replacing the nested instance, and bringing a nested instance
  back to the revision the enclosing module has (also after a whole-instance rotation made before) leave the instances
  enclosing it unedited. When the enclosing instance was moved or turned as a whole between the nested replacement and
  the return, the members that replacement changed stay edited until the enclosing instance is replaced with
  `--discard-edits`. A revision whose objects differ only by their catalog pin keeps every member exactly.
* A system validation stays current until the physics or the targets change; regrouping does not affect it. A module's own
  validation never counts for the system.

The workspace file (`opensci.optical_workspace`) carries the organization and provenance; `project.json` inside it is an
ordinary Project. Python (`os_optics.composition`) and `json_api.workspace_*` use the same operations:
`os-optics workspace --help` lists the commands, `help(os_optics.composition)` the Python workflow.

## Look at your result in 3D

`os-optics view run` opens the run's Optical Simulation View in your browser. The page is served only to this
computer (127.0.0.1, a free port, a private random path) and stops with Ctrl+C. Every element sits exactly where your
design puts it, in millimetres with +Z up, coloured by kind. Main beams are drawn thick; weak branches such as a leak
through a dichroic coating or a ghost reflection are thin and dashed. Use the presets (iso, top, front, side, along the
beam), click a part for its id, name, SKU and size, toggle classes and beams, and switch between English and Chinese.

* `--no-browser` prints the local address.
* `--write-html view.html` writes one self-contained file.
* Glass, metal and the camera views are described in `docs/engine/VIEWER_MATERIALS.md`: `path_horizontal` runs the main beam from left to right
  and `plane_xy`, `plane_zx`, `plane_yz` look square-on at a coordinate plane; the page also has Roll and Turn about beam.
* `--snapshot shots` writes PNG images (orthographic, with labels, legend, axes and scale bar) plus
  `scene_summary.json` (parts by class with ids and bounding boxes in mm, beam paths, camera presets) and `legend.md`,
  for an Agent that works without a browser.
* `--section ALL` adds the 2D layout of the system to `--snapshot`: `section-ALL-XZ.png`, `.svg` and `.json`, one
  true-scale section with every element on the axis, the stop, the image plane and the traced rays (coloured by source
  when several sources share a wavelength, so a source per field angle reads as the fields of a lens). A folded system
  gives one file per axis. `--section ELEMENT` refers the section to that element; repeat it or separate ids by commas.
* `--frame` sets what those images show, for `--section` and `--snapshot` alike; the image takes its aspect within
  `--size` and the scale bar follows. `optics` fits the optical elements and the observation planes near them, without
  the source (the default of `--section`); `scene` fits everything, with the whole beam and far planes;
  `--frame=X0,Y0,Z0,X1,Y1,Z1` fits that box of the design frame in millimetres. Without `--frame`, `--snapshot` shows the
  whole setup with the source and leaves far planes out.
* A framed snapshot is never larger than `--size` and fills it on the side its content fills (`--size-mode fit`, the
  default); `--size-mode exact` keeps `--size` exactly and centres the framed content on it. `scene_summary.json` states
  for every image its `image_px`, `requested_px`, `content_box_px`, `pixels_per_mm` and `scale_bar_mm`.
* `--camera 86,8` writes `camera.png` from a camera stated by angles to the main beam's axis: 86 degrees between the
  line of sight and the axis (0 looks along the beam from behind its start, 90 square-on, 180 back towards the source)
  and the eye turned 8 degrees about the axis towards +Z; `--camera 86,8,ROLL,DISTANCE` also turns the picture clockwise
  by ROLL degrees and records an eye distance (mm, or `3.5x`: 3.5 times the size of what is framed). Every snapshot's
  `camera` entry in `scene_summary.json` records the pose it was drawn from: `view_direction`, `up`, `target_mm`,
  `distance_mm`, `angle_to_axis_deg`, `elevation_deg`, `roll_deg` and the `axis` they are measured against. Snapshots
  are orthographic, so the pose does not depend on the image size.
* `--camera-file shots.json` writes one image per camera of a JSON list, each `[NAME, ANGLE, ELEVATION, ROLL?,
  DISTANCE?]` or `{"name", "angle_to_axis_deg", "elevation_deg", "roll_deg", "distance", "focus", "frame"}` (or
  `{"name", "view": "top"}` for a preset); several run directories on one command (`os-optics view runA runB ...
  --snapshot shots --camera-file shots.json`) write `shots/runA/`, `shots/runB/`, ... with the same cameras.
* `--focus element:L2` frames one element, `element:M1+M2+image` several parts, `axis:L1` the element and every element
  and observation plane on its axis. `--background transparent` writes RGBA PNGs (alpha is the coverage of what is drawn),
  `--background '#ffffff'` a flat colour.
* The page takes the same camera from its address, `...#camera=86,8&focus=L2` (also `,ROLL,DISTANCE_MM` and
  `&projection=orthographic`), and from a script: `window.__opensciViewer.setCamera({angle_to_axis_deg: 86,
  elevation_deg: 8})`, `setView("iso")` and `frame(direction, up, box)` (three.js vectors and box).

From Python: `os_optics.viewer.view_run("run")`, `render_snapshots("run", "shots")`, `scene_summary("run")`.
`os-optics view PLAN_DIR` (or a plan's preview package `.zip`) shows the assembly of a local assembly plan (next
section) without a token: each part on the board with the beam through it, coloured by the status of its checks, the
parts of a finding highlighted (`--finding ID` frames them, `--revision N` shows an earlier revision); `--snapshot` works
as for a run. From Python: `view_plan(revision, context)`, `view_assembly(preview)`.

## From design to hardware: local assembly plan, purchase BOM, Order

Designing, simulating, planning the assembly, checking it and drawing it need no account and no network: `os-optics
assembly plan` does it on this computer from your Project, the public optical catalog and the public mechanical catalog.
OpenSci sells parts. The purchase BOM the plan writes (or one you write by hand) is quoted, ordered and paid through the
OpenSci Commercial Service, which checks that every line can be supplied -- never whether the parts fit together: the
local plan and its checks do that -- and ships the parts for you to assemble.

### 1. Plan the assembly on this computer

```bash
os-optics catalog sync                                       # the optical and, when offered, the mechanical catalog
os-optics assembly plan derive project.json --out plan       # a mount, post, holder and board hole for each optic
os-optics assembly plan show plan --items                    # every item: supply, purchase, chain, board hole, ranges
os-optics assembly plan edit plan --op '{"op": "set_purchase", "item_id": "pst-1", "selected": true, "spare_units": 1}'
os-optics assembly plan check plan                           # interfaces, layout, collisions, supply, optics freshness
os-optics view plan                                          # the assembly in 3D, each part coloured by its checks
os-optics assembly plan draw plan --out drawing --locale zh-CN   # the assembly drawing of your design (SVG, PDF)
os-optics assembly plan bom plan --purchase --out bom.json   # what you buy: exactly the request body of a quote
```

`derive` places each catalog optic in a mount, post and post holder at its design height and fixes the holder on the
nearest free hole of the smallest breadboard that holds the layout (`--max-board-size-mm X Y`, `--customer-table X Y`
for your own optical table, `--customer-owned INSTANCE_ID` for an optic you already have, `--beam-height-mm H`). A light
source or a part that is not an OpenSci product keeps its design position as your own component, with space reserved
around it. When no hardware fits, the plan is still written and its findings say what did not fit and why. The plan
directory keeps every revision (`revisions/r0001.json` ...), the Projects they bind, the journal (`history.jsonl`) and
`plan.json`, a copy of the head; `show` verifies the whole directory first (exit 66 when a file was changed or is
missing), `history` prints the journal, and every item keeps its id (`opt-1`, `mnt-1`, `pst-1`, `hld-1`, `brd-1`,
`cmp-1`, `env-1`) across revisions.

`edit` applies operations as one new revision or writes nothing: `move_optic`, `adjust` (within a declared range),
`set_hardware` (another `OS-MECH-*` or your own `USER-MECH-*` identity), `set_fixation`, `set_board`, `set_supply`,
`set_purchase`, `set_mounting`, `rederive_chain`, `pin`, `declare_envelope` / `remove_envelope` (space reserved for a
part of yours known only by its size), `rebind_catalogs` and `revert_to`. `--op-id ID` makes an edit replay safely,
`--expect-base DIGEST` refuses it when the head moved, `--require-check` refuses one that adds an error. Moving an optic
edits the plan's Project, so a bound optics run reads `STALE` until `assembly plan run-optics plan --out RUN_DIR` runs it
again on this computer. `derive --from plan` derives again, keeping item ids, supply, purchase choices and pinned
hardware.

Each finding names its part or pair of parts, the interface when it is about one, the check that found it and its
basis (`CONSERVATIVE_PROFILE` when the hardware rests on a conservative profile of the mechanical catalog). The status and
exit code of every `assembly plan` command: 0 `READY` or `READY_WITH_WARNINGS`, 1 `HAS_ERRORS` (written), 2 `INCOMPLETE`
(written: a check could not be evaluated; `draw`: a diagnostic sheet), 64 usage or file error, 65 refused (nothing
written), 66 inconsistent plan directory, 70 internal error. `preview --out preview.zip` writes the plan's Assembly
Preview Package (manifest 3.1, bound to the revision and its design BOM), `draw` the Detailed drawing 4.2 (title
"Assembly drawing of your design", its parts legend from the design BOM with who supplies each row); neither is ever
uploaded. `bom --design` lists every item of the plan; `bom --purchase` writes one `PLAN` line per part number you buy
and one `SPARE` line per part number with spare units, and leaves out what you own or did not select (listed in its
report). A plan with your own `USER-MECH` hardware is checked at plan level, but has no preview package or drawing yet.

The mechanical catalog in effect is the active release `catalog sync` or `catalog import` stored, or the documents you
name with `--mechanical FILE` (repeatable) or `OS_OPTICS_MECHANICAL_CATALOG`, including your own hardware (`USER-MECH-*`,
publisher `USER`); `os-optics catalog mechanical list | show | status` reads it. The user guide pages
`docs/en/assembly-plan.md` and `docs/en/mechanical-catalog.md` describe the plan and the catalog in full. From Python:

```python
from os_optics import assembly_plan as ap
from os_optics.local import example_project

ap.derive_plan(example_project(), out_dir="plan")      # the focus example: a source, a lens, a focal plane
plan = ap.open_plan("plan")
plan.edit([{"op": "set_purchase", "item_id": "pst-1", "selected": True, "spare_units": 1}], op_id="spare-post")
checked = ap.check_plan(plan)
print(checked.status.state.value, sorted({f.code.value for f in checked.findings}))
drawing = ap.plan_drawing(plan, "drawing")           # the assembly drawing of your design, on this computer
body, report = ap.purchase_bom(plan)                 # the purchase BOM: the request body of a quote
print([(line["opensci_sku"], line["quantity"], line["purpose"]) for line in body["lines"]])
```

A host that is not a Python program uses the JSON API methods `derive_assembly_plan`, `check_assembly_plan`,
`apply_assembly_plan_ops`, `assembly_plan_preview`, `assembly_plan_drawing`, `assembly_plan_bom` and
`mechanical_catalog`; `os-optics schema assembly-plan` (also `assembly-plan-ops`, `design-bom`, `purchase-bom`,
`bom-review`, `public-mechanical-catalog`) prints each document's JSON Schema.

### 2. Point the engine at OpenSci and give your Agent its own token

Sign in to the OpenSci Account and create an Agent token. Choose **single-use** (one successful new Order),
**time-limited** (up to 30 days) or **permanent until revoked**. Download `opensci-token.txt`: this is the token
file consumed directly by the CLI. Keep it where only you and the Agent you authorize can read it.

```bash
export OPENSCI_API_URL=https://opensci-optics.com
export OPENSCI_TOKEN_FILE=/path/to/opensci-token.txt
os-optics account status             # organization, lifecycle, status, scopes and permissions
os-optics order quote bom.json       # the line review and prices of a purchase BOM
os-optics order create RESOLUTION_ID # unpaid Order Number, quotation, contract, Account handoff
os-optics order show ORDER_ID        # read and track even after a single-use token placed its Order
```

On Windows, set `OPENSCI_TOKEN_FILE` to your downloaded file's path in your shell. The engine finds a token in this
order: `--token-file PATH` (commands) or the `token=` argument (Python), `OPENSCI_TOKEN`,
`OPENSCI_TOKEN_FILE`, then `~/.opensci/token`. A token is never a command-line value. The `agent` profile can price,
place and read Orders; `review` can price and read without placing one. A single-use token spends its **placement**
authority in the same transaction as its new Order. The same idempotent request recovers that Order; unrelated new
placements are refused. Read and tracking scopes remain available until expiry or revocation. A permanent token has
no synthetic far-future expiry and can be revoked in Account. Reporting a bank transfer is not payment confirmation;
OpenSci confirms receipt separately. Account shows the quotation, contract and payment instructions.

You can plan and write the BOM before you have an Account. Without a token `order quote` sends nothing, keeps the BOM in
its file and names the Account registration and sign-in pages (`OPENSCI_ACCOUNT_URL` selects another Account website),
where to save the token and the same command to run again; it sends the same request, so it never creates a second
quote.

The secondary `os-optics account token create --profile agent --lifecycle time-limited --save --token-file
OWNER_CREDENTIAL_FILE` path is for a credential carrying `account:token:manage`. A bounded issuer cannot issue a
permanent token; use the signed-in Account website for that. `--save` writes to `~/.opensci/token` without printing the
secret. `os-optics account token rotate --issuer-token-file OWNER_CREDENTIAL_FILE` replaces a token, while Account
can revoke any of its tokens. `os-optics account logout` revokes the token in use and deletes its token file.

### 3. Quote and order a purchase BOM

```bash
os-optics order quote bom.json                              # review + quote (resolution_id when a line can be supplied)
os-optics order quote bom.json --confirm bom.review.json    # confirm the changes the review lists
os-optics account address list                              # the shipping addresses (mainland China), the default first
os-optics account address add --recipient 张三 --phone 13800138000 --province 广东省 --city 深圳市 \
    --district 南山区 --street "科技园路 1 号"                      # add one (the first becomes the default)
os-optics order create RESOLUTION_ID                        # the Order: Order Number, total, quotation, agreement, payment
os-optics order create RESOLUTION_ID --address ADDRESS_ID   # ... shipped to another address of the list
os-optics order show ORDER_ID --locale zh-CN                # also --document quotation | contract
```

A purchase BOM lists OpenSci part numbers and quantities -- optics (`OS-OPT-*`), mounts, posts, post holders and
breadboards (`OS-MECH-*`) -- with `line_id` and `purpose` `PLAN | SPARE` optional:

```json
{"schema": "opensci.commercial.part_list_submission", "schema_version": "2",
 "lines": [{"opensci_sku": "OS-OPT-...", "quantity": 2},
           {"opensci_sku": "OS-MECH-...", "quantity": 1, "purpose": "SPARE"}]}
```

`order quote` exits 0 when every line can be supplied (`ORDERABLE`, with the next command), 1 when some lines cannot be
supplied as listed (`CHANGES_REQUIRED`: a line `NOT_ORDERABLE`, `TEMPORARILY_UNAVAILABLE` or `ABOVE_LIMIT`; it writes
`bom.review.json`, or `--review-out FILE`, whose proposed confirmation keeps the other lines and removes the changed ones
-- edit its decisions to change a quantity or substitute a part number; `order create` on that quote is refused with
`BOM_CHANGES_UNCONFIRMED` until the changes are confirmed), 65 for a BOM or confirmation refused locally (nothing is
sent), 67 when the service refuses it (`BOM_NOT_ORDERABLE` when no line can be supplied: the review is written) and 77
without a token (the other codes: `order quote --help`). `--schema-version 1` sends the earlier part-list body to a
service that does not quote BOMs yet (no `purpose`, no confirmation); `resolve-parts PARTS.json` also sends that body.
From Python: `client.quote_bom(document)` (`os_optics.client.purchase_bom_body`, `review_of`, `review_document`,
`confirmation_body`).

A token with the `agent` profile is the authorization to create the Order: `order create` creates it directly. It ships
to the default address of your organization's address book, or to `--address ADDRESS_ID`; ask the person which address
to use. With no address at all, `order create` is refused with `SHIPPING_ADDRESS_REQUIRED` (recovery
`ADD_SHIPPING_ADDRESS`): add one with `account address add` (editing and deleting addresses is done on the OpenSci
Account website). The Order keeps a copy of the address. Present the Order Number, the total, the shipping address and
the bank-transfer instructions to the person who pays. `order create --additional-order` orders a BOM your organization
already ordered, again.

To withdraw an Order -- only when the person asks, and only before a transfer is reported:

```bash
os-optics order withdraw ORDER_ID                      # the Order and its data are deleted
```

The Order, its quotation, agreement and shipping-address copy and (unless another Order uses it) the resolution are
deleted; its Order Number is never used again. Tell the person it was withdrawn. To change what you buy after ordering:
withdraw, edit the plan or the BOM, quote and order again. After a reported transfer or a payment the service refuses
(`ORDER_NOT_WITHDRAWABLE`, recovery `CONTACT_OPENSCI`): the person contacts OpenSci. Repeating `order withdraw` after a
withdrawal answers `ORDER_NOT_FOUND` (the Order no longer exists). A quote that was never ordered is deleted after 7
days: quote the BOM again to order it later.

### 4. Pay and report the transfer

Pay the full amount by corporate bank transfer with the exact Order Number as the transfer remark. When the person who
paid says the transfer was made:

```bash
os-optics order report-transfer ORDER_ID
```

A report is not a confirmation: OpenSci confirms receipt of the payment and ships the parts.

OpenSci no longer orders designs, selects mounts, assembles systems or delivers hosted previews, drawings, optical
layouts and mounting sheets, and accepts no uploads. Against the current service `resolve`, `assembly preview
RESOLUTION_ID`, `assembly drawing RESOLUTION_ID`, `view --resolution`, `resolution layout` / `order layout`, `resolution
mounting-sheet` / `order mounting-sheet` and `artifacts` exit 67 with the recovery `USE_LOCAL_PLAN` and the local
command that replaces them in `hint`; `order create` of a design resolution is refused with `ORDERING_BASIS_RETIRED`
and `resolve-parts` with derived mechanics or another order option with `ORDER_OPTION_RETIRED`. Earlier Orders stay
readable with `order show`.

Chinese text in the drawing PDFs (`assembly_drawing.pdf`, `optical_layout.pdf`) uses the standard Chinese PDF font
`STSong-Light` (Adobe-GB1) without embedding a font file, so the PDFs stay small and identical on every computer. A
viewer with Chinese font support renders it: Adobe Acrobat / Reader (with its Asian font pack, installed by default in
Chinese editions), Chrome / Edge (PDFium), Firefox (pdf.js), macOS Preview, SumatraPDF and PyMuPDF. Poppler-based tools
(`pdftotext`, Evince, Okular) need the `poppler-data` package for it. The SVG of the same sheet
(`assembly_drawing.svg`, `optical_layout.svg`) needs no PDF font: open it in any browser when a PDF viewer shows no
Chinese text.

### The same from Python

```python
from os_optics import assembly_plan as ap
from os_optics.client import OpenSciClient
from os_optics.local import example_project

ap.derive_plan(example_project(), out_dir="plan")      # the focus example: a source, a lens, a focal plane
body, report = ap.purchase_bom("plan")                  # the plan's purchase BOM
client = OpenSciClient.from_environment()
quote = client.quote_bom(body)                          # its line review and prices
order = client.create_order(quote["resolution_id"])["order"]
# client.withdraw_order(order["order_id"])  # only when the person asks, before a transfer is reported
# ... the customer pays; when they say the transfer was made:
client.report_bank_transfer(order["order_id"])
drawing = ap.plan_drawing("plan", "drawing")           # the drawing stays on this computer
```

Repeating a call is safe: BOM quotes, Orders and payment reports carry an idempotency key derived from their input, so
a repeat returns the first result (`replayed: true`). Every failure is an `OpenSciApiError` with `code`, `message`,
`details`, `recovery` (what to do next), `http_status` and `request_id`. Keep your token out of chats, logs and shared
files; if it leaks, `os-optics account logout` revokes it. The engine writes only what you ask for: the plan directory,
run directories, drawings, BOM and review files, snapshot images and your token file (`--save`, readable only by you).

Exit codes of the commercial commands: 0 done, 1 `order quote` answered `CHANGES_REQUIRED`, 64 usage / file error, 65
unusable local input (nothing is sent), 67 refused by OpenSci (also a retired route or option), 69 OpenSci unreachable,
busy or rate-limited (recovery `RETRY_LATER`: send the same request again after `retry_after_seconds`), 75 not available
yet, 77 token missing, expired, revoked or without the needed scope, 78 `OPENSCI_API_URL` missing.

### Send feedback to OpenSci

When something did not work while you were doing what the user needed, when a feature is missing, or when this
documentation was missing or wrong, send it to OpenSci. Any OpenSci Account token works (Agent or Review profile):

```bash
os-optics feedback submit --category PROBLEM     --summary "order create refused although an address exists"     --details-file feedback.md     --error-code SHIPPING_ADDRESS_REQUIRED --request-id REQUEST_ID --resolution-id RESOLUTION_ID
```

`--category` is `PROBLEM`, `FEATURE_REQUEST` or `DOCUMENTATION`; `--summary` is one line (5 to 200 characters);
`--details TEXT` or `--details-file FILE` (UTF-8, `-` for standard input; 1 to 8000 characters) says what the user
needed, the commands you ran, what happened and what you expected. The engine version, the Python runtime and the
active local catalog release are added; `--order-id`, `--command`, `--locale` and `--agent NAME` (default:
`OPENSCI_AGENT_NAME`) add more context. It prints the receipt `{"feedback_id", "received_at", "status": "RECEIVED",
"replayed"}`; sending the same feedback again returns the same receipt. From Python:
`client.submit_feedback(category=..., summary=..., details=..., context={...})`.

One problem or suggestion per submission. Include no tokens, passwords, payment data or personal data beyond what the
problem needs (text holding an Account token is refused before anything is sent; the service refuses credential-shaped
text with `FEEDBACK_INVALID`, exit 67). Tell the user what you sent. At most 10 submissions per token per hour and 50
per organization per day: above that the service answers `FEEDBACK_RATE_LIMITED` (exit 69, recovery `RETRY_LATER`,
`retry_after_seconds`).

### Working with package files

`os_optics.assembly`, `os_optics.assembly_drawing` and `os_optics.client_artifacts` also work on
package objects and files directly: `load_assembly_preview(source)` verifies a package (manifest, digests, schema,
geometry) and `validate_assembly_layout(package)` runs the local assembly check (`AssemblyLocalValidationV1`);
`generate_assembly_drawing(preview, detail)` draws; `validation_artifact_upload` / `drawing_artifact_upload` build
the upload (manifest + exact file bytes) an earlier service's artifact routes accepted (the current service accepts no
uploads). The `os-optics assembly verify | validate | draw | verify-drawing` commands do the same on files; `assembly
draw PACKAGE --design-bom FILE` draws a local plan's package with its design BOM.

## Low-level Engine API

Below the design workflow is the stateless Engine itself: `validate(request)` on a `ValidationRequest` (3D geometric
optics, Gaussian beams with x/y-independent q-parameters, Jones polarization, analytic two-beam interference, 2.5D
layout, control / recoverability analysis), `os-optics validate request.json --pretty` on the command line. Build a
scene programmatically with the factories in `os_optics.builders`:

```python
from os_optics import builders as B
from os_optics.contracts import ProjectSnapshot, Target
from os_optics import ValidationRequest, validate

project = ProjectSnapshot(
    components=(
        B.laser("L", wavelength_nm=532.0, waist_radius_mm=1.0, position=(0, 0, 20), direction=(1, 0, 0)),
        B.thin_lens("Lens", focal_length_mm=100.0, clear_radius_mm=12.7, position=(10, 0, 20), axis=(1, 0, 0)),
    ),
    observations=(B.observation_plane("focus", position=(110, 0, 20), normal=(1, 0, 0), radius_mm=5.0),),
    targets=(Target(target_id="w", metric="beam_radius_mm", observation_id="focus", maximum=0.05),),
)
result = validate(ValidationRequest(project=project))
print(result.status.value, result.targets[0].value)    # the focused 1/e² beam radius in mm
```

## Public API

| Object | Purpose |
|---|---|
| `os_optics.local` | local pure-optical design workflow: public catalog, `run_optical_project`, selective evaluation (`EvaluationSession`, `evaluate_project`, `json_api.evaluate_project`), run directory, `verify_run_artifacts`, `update_project_catalog`; Project edit operations: `project_ops.update_project` / `json_api.update_project`; run freshness: `freshness.run_freshness` / `json_api.run_freshness`; declared optical models: `compile_model` (the one compile path), `declared_copy`, `json_api.catalog_model` |
| `os_optics.composition` | reusable layouts: workspaces, `import_layout`, `select`, `transform`, editing groups, `replace_instance`, `batch`, `export_module`, `run_workspace`, `workspace_status` |
| `os_optics.results` | the scientific results of a run: `load_run`, target outcomes (`targets()`), monitors, beams, paths, power budget, rays, wavefront, irradiance map, metric descriptors, CSV / JSONL / JSON / NPZ export |
| `os_optics.catalog` | the local catalog store: `status`, `check`, `sync`, `verify`, `import_release` |
| `os_optics.viewer` | first-party local 3D viewer: `view_run`, `view_plan`, `view_assembly`, `render_snapshots`, `scene_summary` |
| `os_optics.assembly_plan` | local assembly plans: `derive_plan`, `open_plan` (edit, check, history), `check_plan`, `plan_preview`, `plan_drawing`, `design_bom`, `purchase_bom`, `apply_plan_ops`, `load_mechanical_catalog` |
| `os_optics.client` | `OpenSciClient`: the commercial workflow with your Account token (purchase BOM quote and review, Order, payment report, addresses, tokens, feedback) |
| `os_optics.optical_view` | the Optical Simulation View builder and GLB writer (used by `run_optical_project`); the layout of a Project without a run (`layout.py`, `json_api.project_layout`) |
| `os_optics.assembly` | local assembly check of an Assembly Preview Package |
| `os_optics.assembly_drawing` | local Detailed Assembly Drawing (SVG / PDF + result / manifest) from a preview + detail package |
| `os_optics.protected_delivery` | open an encrypted assembly delivery in memory (extra `[protected]`); used by the client |
| `os_optics.client_artifacts` | `validation_artifact_upload` / `drawing_artifact_upload`: the artifact upload (manifest + files, multipart parts) |
| `validate(request)` / `validate_json(text)` | pure function: request -> `ValidationResult` (never raises for physical / contract validation outcomes represented by the result model) |
| `evaluate(request, capabilities=..., exclude=..., targets=..., state=...)` / `os_optics.evaluation` | a selected part of `validate`: the capability table, the plan (`plan_evaluation`) and an `EvaluationState` that keeps each capability's product for the next evaluation |
| `validate_with_ray_paths(request)` / `trace_ray_paths(request)` | the same single run plus a `RayPathTrace`: chief and pupil sample ray polylines of every traced path; `input_hash` / `trace_hash` equal the run's manifest |
| `ValidationRequest`, `ProjectSnapshot`, `EngineConfiguration`, `NumericalConfig` | inputs (Pydantic v2, JSON friendly) |
| `ValidationResult`, `ValidationIssue`, `ValidationManifest` | outputs (finite numbers only, structured issues, hashes) |
| `os_optics.builders` | element factories producing contract objects |
| `os_optics.compare.compare_results` | cross-runtime comparison within `cross_runtime_rel_tol/abs_tol` |
| `os-optics doctor` / `capabilities` / `catalog` / `project` / `native` / `workspace` / `interop` / `results` / `export` / `view` / `layout` / `account` / `products` / `resolve` / `resolve-parts` / `resolution` / `order` / `assembly` / `artifacts` / `feedback` / `schema` / `validate` / `compare` | command line: every top-level command (`os-optics --help`, `os-optics COMMAND --help`) |

Status vocabulary: `PASS`, `WARNING`, `FAIL`, `UNKNOWN`, `NOT_APPLICABLE`, `OUT_OF_MODEL_SCOPE`.

Exit codes of `project run`, `workspace run`, `validate` and `project evaluate` follow `--status-contract` (default
`requirements`, for `project evaluate` by its selection as below; the environment variable `OS_OPTICS_STATUS_CONTRACT` sets it for scripts that cannot change their
command line). The JSON summary of `project run` and `workspace run` and the reply of `project evaluate` name the
contract used as `exit_contract`; `validate` prints the Engine result itself. Under `requirements` the code reads the
execution and the lists of the requirement record, nothing else.

| Contract | 0 | 1 | 2 | 3 |
|---|---|---|---|---|
| `requirements` (default) | the calculation completed and no assessed target is `UNMET`, `INDETERMINATE` or `NOT_APPLICABLE`: every assessed target is `MET`, or the Project declares no targets | an assessed target is `UNMET` | an assessed target is `INDETERMINATE` or `NOT_APPLICABLE`, none `UNMET` | the calculation did not complete (`PARTIAL` / `FAILED` / `CANCELLED`), a result that states no execution, or a run a memory or run-file limit stopped |
| `execution` | the calculation completed | anything else, a stopped run included | | |
| `aggregate` | `PASS` / `WARNING` / `NOT_APPLICABLE` | `FAIL` | `UNKNOWN`; a stopped run | `OUT_OF_MODEL_SCOPE` |

| Command | Targets assessed | Requirement record | Status under `aggregate` |
|---|---|---|---|
| `validate` | every declared target of the request | the Engine result's | `ValidationResult.status` |
| `project run`, `workspace run` | every declared target | the Local Validation record's (a target a model-scope finding affects is `INDETERMINATE`) | the run's validation status |
| `project run`, Project with configurations | every declared target of each configuration that ran | per configuration; combined 3 before 1 before 2 before 0 | per configuration; `FAIL` before `OUT_OF_MODEL_SCOPE` before `UNKNOWN`; a configuration without a result is not completed |
| `project evaluate` | the targets named by `--targets`, every declared target when `requirements` is among the outputs, none otherwise (the default `readings`) | the reply's | of what was computed |

`project evaluate` takes, when neither `--status-contract` nor the environment names a contract, `requirements` when it
assesses targets and `execution` when it assesses none (the default `readings`): its exit 0 then says that the
calculation completed, nothing about a target. `requirements` named for an evaluation that assesses none of the
declared targets is refused before anything is computed (64, `REQUIREMENTS_NOT_SELECTED`, naming the targets: add
`requirements` to `--outputs`, name them with `--targets`, or use `--status-contract execution`); a Project that
declares no targets exits 0 under it once the calculation completed. The declared targets it did not assess are in
the reply's `requirements.not_assessed` (and `evaluation.targets_not_evaluated`) and take no part; when it assessed
none, its `status` is `NOT_APPLICABLE` where it would read `PASS`. A target left undetermined because a capability was
outside the evaluation (`CAPABILITY_NOT_COMPUTED`) is `INDETERMINATE`: 2.

Under `requirements` a quantity out of the model's scope changes the exit code only through the targets that depend on
it; `aggregate` (the worst-of diagnostic severity, `ValidationResult.status`) reports any of them. Input that is not a
valid request keeps its code under every contract: `validate` exits 1 (a structured `FAIL` with `INVALID_INPUT`). A valid
request that states something outside the traced model (an open facet sheet between two media) is refused with status `OUT_OF_MODEL_SCOPE` and
termination `OUT_OF_MODEL_SCOPE`: exit 3 under `requirements` and `aggregate`, 1 under `execution`. The execution is
`PARTIAL` only when the primary trace exhausted a budget. An auxiliary analysis that reached its budget -- the probes of
untraced light, the pair listing, a return-light analysis that reached `max_return_analysis_work`
(`RETURN_ANALYSIS_BUDGET`) -- reports a notice, is listed in `outcome.execution.budgets.exhausted_by_phase` and leaves
the execution `COMPLETED`: the exit code is then the targets' under `requirements`, and the quantities that lost a bound
say so themselves (a plane's `power_upper_bound_W` null, a rating status `UNKNOWN`).
`assembly validate` exits by its status (0 PASS/WARNING/NOT_APPLICABLE, 1 FAIL, 2 UNKNOWN, 3 OUT_OF_MODEL_SCOPE). Every
command: 64 usage error (also an `OS_OPTICS_STATUS_CONTRACT` that names no contract; for `project evaluate` also an empty
`--outputs` / `--targets`, a selection that cannot be planned or a `--scene-out` / `--glb-out` file that cannot be
written), 65 a Project or catalog that cannot be simulated as
given (for `project evaluate` also a Project that declares configurations), or an assembly package that does not verify,
66 `project verify` found an inconsistent run directory / `assembly verify` an invalid package, 70 an internal error of
os-optics (an exception no command documents, on input that is good: JSON `{"status": "ERROR", "error": {"code":
"INTERNAL_ERROR", "type", "message"}}` on stdout with the exception's class as `type`, the traceback on stderr), 130
Ctrl-C. A `results` / `export` command that fails on a run directory that does not verify answers `RUN_NOT_READABLE`
(64) with the `project verify` command to run; `json_api.run_results` with `verify` true answers `INTERNAL_ERROR` for a
failure on files that verify, and refuses a query value of the wrong JSON type by name (`RESULTS_QUERY_INVALID`).

The "never raises" guarantee above applies to the `validate()` / `validate_json()` entry points: physical and
contract-validation outcomes are always represented by the `ValidationResult` model, never raised as exceptions.
Direct construction of the frozen Pydantic contract models (e.g. `builders.laser(wavelength_nm=float("nan"))` or
`SourceSpec(...)`) can still raise a Pydantic `ValidationError` *before* those entry points are called, because the
contract models deliberately set `allow_inf_nan=False`. Pass such input through `validate_json` (or the CLI) to receive
it as a structured `FAIL` / `INVALID_INPUT` result instead.

## Conventions

Right-handed 3D, lengths mm, wavelengths nm (vacuum), angles degrees at the API, power W, `w` = 1/e² intensity **radius**.
Element local `+z` = optical axis; surface normals are explicit; Jones vectors live in a transported transverse basis
`(e1, e2)`, `e1 × e2 = d`. `E ∝ exp[i(k·r − ωt)]`. Every numerical tolerance is a documented field of `NumericalConfig`
(`help(os_optics.NumericalConfig)`); results record the configuration version they were computed with.
`input_hash` / `result_hash` are SHA-256 digests of the canonical request and result (results quantized to 9 significant
digits): the same content gives the same hash, and any change of the content changes it. They are not signatures and
say nothing about who computed a result.

## License

OS Optics is open-source software under the **MIT License** from version 0.14.0 on (`License-Expression: MIT`; the full
text is `LICENSE`, in every wheel and source distribution). The source, its tests and its release workflow are in the
public repository <https://github.com/JunLin615/os-optics>; issues go to <https://github.com/JunLin615/os-optics/issues>.

- Versions 0.13.0 and earlier (and `opensci-engine` up to 0.9.0) were published under the OpenSci Engine License, which
  continues to apply to those releases.
- The names OpenSci and OS Optics and the OpenSci logo are trademarks; the MIT License grants no right to use them
  (`TRADEMARK.md` in the repository).
- Third-party dependencies and bundled components keep their own licenses (three.js:
  `os_optics/viewer/assets/THREE_LICENSE.txt`).
