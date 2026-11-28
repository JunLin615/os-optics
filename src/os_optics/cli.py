"""Command line interface of os-optics.

Local pure-optical design workflow (``os_optics.local``), on this device against the public optical
catalog in the local catalog store (``os_optics.catalog``: synchronized from the OpenSci catalog service on first
use and then at most hourly; offline, the stored release is used):

* ``os-optics catalog status|check|sync|verify|import FILE|use VERSION``: the local catalog store (``cli_catalog``);
* ``os-optics catalog list [--category LENS] [--form PLANO_CONVEX_LENS] [--wavelength-nm 780]
  [--focal-length-mm F [--focal-tolerance-mm T] | --focal-min-mm MIN --focal-max-mm MAX] [--focal-sign positive|negative]
  [--readiness ORDERABLE|SIMULATION_ONLY] [--mounting AVAILABLE|NOT_AVAILABLE] [--offset N] [--limit N]``: published optical
  products as compact rows (JSON; ``total`` counts every match; each row states whether OpenSci supplies the product,
  whether OpenSci mounts it and its delivered form; the focal filters compare the signed nominal focal length:
  ``FocalLengthQuery``);
* ``os-optics catalog show OS-OPT-...``: one product's public document (specifications, simulation models, geometry);
* ``os-optics project example [--kind KIND | --folded]``: a complete reference ``ProjectDocumentV2`` to copy and edit
  (kinds: focus, folded, beam_expander, relay_4f, folded_expander, dichroic_routing, polarization,
  two_beam_interference, two_color_dichroic, dichroic_combiner; ``--folded`` is
  ``--kind folded``: source -> fold mirror -> lens -> focal plane);
* ``os-optics project check project.json``: the Project's identity (``project_hash``, ``engine_input_hash``), problems
  and advisory warnings, without a run (``cli_project``);
* ``os-optics project update project.json --ops ops.json [--expect-hash H] [-o OUT]``: apply headless Project
  edit operations and check the result (``cli_project``, ``os_optics.local.project_ops``);
* ``os-optics project run project.json --out-dir run``: simulate the Project with the Engine (one run) and write
  the run directory: ``manifest.json``, ``project.json``, ``engine_request.json``, ``simulation_result.json``,
  ``ray_data.json`` (every traced ray), ``local_validation.json`` (``LocalValidationRecordV1``), ``optical_scene.json`` and ``optical_scene.glb`` (the 3D
  Optical Simulation View). The run summary is printed as JSON; the exit code follows ``--status-contract``
  (``cli_exit``: by default 0 when every declared target is met, 1 unmet, 2 undetermined, 3 incomplete);
* ``os-optics project evaluate project.json [--outputs readings,requirements] [--exclude C] [--targets T1,T2]``: compute
  selected quantities and only what they require, and print the scope, the readings and the evaluated targets as JSON
  (``cli_evaluate``, ``os_optics.local.evaluate``); the exit code follows ``--status-contract`` over the targets it
  assessed (by default ``requirements`` when it assesses targets and ``execution`` when it assesses none; the reply's
  ``requirements.not_assessed`` names the declared targets it did not assess); ``--describe [ID]`` lists the capabilities;
* ``os-optics project verify run``: re-check every hash and cross-reference of a run directory;
* ``os-optics project import-prescription lens.csv [--model-id ID] [--into project.json --instance-id ID --pose x,y,z
  --out project.json]``: import a lens prescription (CSV surface table) as Project-native optical models
  (``cli_prescription``, ``os_optics.prescription``);
* ``os-optics project update-catalog project.json --out project.json``: re-pin the Project's catalog components to
  the active catalog release where their product is unchanged (changed / withdrawn products are reported);
* ``os-optics project model-from-catalog OS-OPT-... [--model-id ID]``: a catalog product's simulation model as a
  declared optical model (``optical_model``, ``model_materials``, ``model_coatings``) to insert into a Project and edit;
* ``os-optics project paraxial project.json --components L1 L2 --wavelength-nm 532 [--solve afocal|collimate]``:
  thick-lens ABCD, focal distances and principal planes of placed components, the afocal vertex gap or the collimating
  distance (``cli_paraxial``, ``os_optics.local.paraxial``);
* ``os-optics project cavity project.json --components M1 M2 --wavelength-nm 1064``: stability, Gaussian eigenmode, Gouy
  phase, free spectral range, transverse-mode spacing, finesse and mode matching of a linear cavity (``cli_paraxial``,
  ``os_optics.local.cavity``);
* ``os-optics view run [--no-browser] [--write-html view.html] [--snapshot shots]``: the local 3D viewer
  (``os_optics.viewer``): a browser page served from 127.0.0.1, one self-contained HTML file, or PNG snapshots
  with ``scene_summary.json`` for an Agent without a browser; ``--section ALL`` adds the system's 2D layout (a true-scale
  section with the stop, the image plane and the rays) and ``--frame optics|scene|=X0,Y0,Z0,X1,Y1,Z1`` sets what the images show;
* ``os-optics results describe|targets|freshness|monitors|monitor|paths|power-budget|rays|wavefront|irradiance|handoff|
  exposures|grating-orders|metrics|columns``: the scientific results of a run directory (``cli_results``, ``os_optics.results``);
  ``results freshness RUN --project P`` says whether the run is current for a Project; ``os-optics
  export KIND run --format csv|jsonl|json|npz [--out FILE]``: one kind of result as a file;
* ``os-optics schema project|local-validation|catalog|optical-scene|assembly-validation|linear-optical-network|pupil-field-request|detector-model``: JSON
  Schemas;
* ``os-optics native list|describe CLASS|build CLASS --param NAME=VALUE ... [--into project.json]``: the native
  component library (``cli_native``, ``os_optics.native_library``);
* ``os-optics workspace init|import|select|transform|group|replace|update-project|batch|export|status|run|record|
  history``: compose optical systems from reusable layouts (``cli_workspace``, ``os_optics.composition``);
* ``os-optics doctor``: check this installation offline; ``os-optics capabilities``: what this runtime offers
  (``json_api.capabilities()``);
* ``os-optics interop list|show ENTRY``: external scientific tools that work with OpenSci data (``cli_interop``,
  ``os_optics.interop``).

Local assembly check (``os_optics.assembly``) of an Assembly Preview Package OpenSci resolved for a Project:

* ``os-optics assembly verify assembly-preview.zip``: verify the package (manifest, digests, schema, geometry);
* ``os-optics assembly validate assembly-preview.zip --out assembly-validation.json``: verify, then run the local
  layout and collision checks and write the ``AssemblyLocalValidationV1`` result; the exit code is its status;
* ``os-optics assembly draw assembly-preview.zip --detail assembly-detail.json --out drawing
  [--locale zh-CN|en]``: the local Detailed Assembly Drawing (``os_optics.assembly_drawing``): SVG, PDF, result and
  manifest in ``drawing/``, in English (default) or Simplified Chinese;
* ``os-optics assembly verify-drawing drawing``: re-check a drawing output directory (only the four drawing files
  are read and strictly verified; any other entry is reported under ``warnings``).

Commercial workflow with the OpenSci service (``os_optics.cli_commerce``, ``os_optics.client``):
``account``, ``resolve`` (a validated design), ``resolve-parts`` (a part list), ``products mechanical``,
``resolution``, ``order``, ``assembly preview``, ``assembly drawing`` and ``artifacts``; ``feedback submit`` sends a
problem or a suggestion to OpenSci.

Optical Layout Drawing of a design bought without OpenSci mechanics (``os_optics.optical_layout``): ``os-optics layout draw
optical-layout.json --out layout [--locale zh-CN|en]`` renders a package file offline; ``order layout ORDER_ID`` /
``resolution layout RESOLUTION_ID`` fetch it from the OpenSci service first.

Low-level Engine: ``os-optics validate request.json`` runs one ``ValidationRequest``. The CLI reads and writes
the files; the physics engine itself performs no I/O.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from . import __version__
# (COMMAND_NAMES: the command names of this command line -- ``os-optics``, and its earlier name, installed by the alias
# distribution; defined once, in the module the command imports first)
from ._command import COMMAND_NAMES, INTERRUPTED_EXIT, INTERRUPTED_WHILE_STARTING
from .cli_commerce import ArgumentParser
from .cli_exit import (
    DEFAULT_STATUS_CONTRACT,
    DIAGNOSTIC_SHEET_WRITTEN,
    EXIT_CODES,
    EXIT_CONTRACT_EPILOG,
    INTERNAL_ERROR_EXIT,
    StatusContractError,
    add_status_contract_argument,
    exit_code,
    internal_error_document,
    internal_error_traceback,
    chosen_status_contract,
    resolve_status_contract,
    configurations_exit_code,
    run_exit_code,
    stopped_exit_code,
)
from .compare import compare_results
from .contracts.outcome import ResultOutcome
from .contracts.request import ValidationRequest
from .contracts.results import ValidationResult
from .validation import validate_json

USAGE_ERROR = 64
LOCAL_INPUT_ERROR = 65
#: What a command interrupted by Ctrl-C prints on stderr (exit code 130); ``project run`` prints its stopped run's record.
INTERRUPTED_COMMAND = "os-optics: interrupted (Ctrl-C); the command stopped before it completed"
EXIT_CODE_HELP = (
    EXIT_CONTRACT_EPILOG + " 'assembly validate': 0 = PASS / WARNING / NOT_APPLICABLE, 1 = FAIL, 2 = UNKNOWN, "
    "3 = OUT_OF_MODEL_SCOPE. Every command: 64 = usage error / unreadable or unwritable file, 65 = a Project or catalog "
    "that cannot be simulated as given, or an assembly package that does not verify (JSON with status ERROR and structured "
    "problems on stdout), 66 = project verify found an inconsistent run directory / assembly verify found an invalid "
    "package, 70 = an internal error of os-optics (JSON with code INTERNAL_ERROR and the exception type on stdout, the "
    "traceback on stderr), 130 = Ctrl-C"
)
ASSEMBLY_PACKAGE_INVALID = "ASSEMBLY_PACKAGE_INVALID"
ASSEMBLY_DESCRIPTION = (
    "Local check of a resolved assembly (its Assembly Preview Package: the parts, poses, supports and breadboard of one "
    "Project). 'verify' checks the package's integrity; 'validate' verifies it, then runs the layout and collision checks "
    "(poses, part geometry, support attachment, breadboard containment and fixation, support engagement, part-to-part "
    "collision, beam-axis clearance) on this device and prints (or with --out writes) the assembly check result as JSON: "
    "status, every check with its basis, issues, per-part status, the tolerances used and a summary. PACKAGE is the .zip "
    "archive or a directory holding manifest.json, scene.json and geometry/*.glb. The package of a resolution is "
    "checked in memory, without writing it to disk, by 'os-optics assembly preview RESOLUTION_ID'."
)
VERIFY_FAILED = 66
DRAWING_DESCRIPTION = (
    "Detailed Assembly Drawing of a resolved assembly, generated on this device: verifies the Assembly Preview Package and "
    "its Assembly Detail Package, runs the local assembly validation on the same package, then writes assembly_drawing.svg, "
    "assembly_drawing.pdf, drawing_result.json and drawing_manifest.json into --out: FRONT, SIDE, PLAN and PICTORIAL views, "
    "dimensions in mm with their basis, item balloons with the Customer Purchase BOM lines, parts legend, optical spacing, "
    "alignment, assembly steps and notes, in English or Simplified Chinese (--locale en | zh-CN, default en). When the "
    "local assembly check passed (PASS / WARNING) the Detailed Assembly Drawing is written, ready for delivery (exit 0). "
    "When it FAILED or could not be completed (UNKNOWN / OUT_OF_MODEL_SCOPE) the drawing is refused and nothing is written "
    "(exit 65, the JSON error lists the issues); with --diagnostic a diagnostic sheet with a banner, a watermark and the "
    "issues behind the status is written instead, not ready for delivery (exit 2). The JSON reply names the sheet "
    "(status, delivery_ready). An earlier drawing's four files in --out are replaced. Reads both packages "
    "from disk; the drawing of a resolution's delivered packages is 'os-optics assembly drawing RESOLUTION_ID'."
)
MAIN_DESCRIPTION = (
    "OpenSci optical engine: local pure-optical design on this device. Typical flow: 'catalog list' -> 'catalog show "
    "OS-OPT-...' -> 'project example > project.json' (edit it, or apply edit operations with 'project update project.json "
    "--ops ops.json -o project.json'; 'project check project.json' checks it without a run) -> 'project run project.json "
    "--out-dir run' -> 'results targets run' (every target outcome and the model-scope issues), 'results exposures run' "
    "(each element's exposure -- power, peak irradiance, pulsed peak fluence -- and its power ratings condition by condition), "
    "read run/manifest.json "
    "(status, files) and run/local_validation.json, 'results describe run' / 'export arrivals run' for the "
    "scientific results (beams at every monitor, rays, wavefronts, power budget), then 'view run' to look at "
    "run/optical_scene.glb in 3D; 'results freshness run --project project.json' says whether the run is still current "
    "after an edit. 'project evaluate project.json' computes selected quantities and only what they require, without "
    "writing a run; 'results imaging', 'results ghosts', 'results focal-field' and 'results field' compute the point "
    "spread function and MTF, ghost images, focal fields and coherent fields from a run's light. Run "
    "'os-optics <command> --help' for details; 'capabilities' prints what this runtime offers as JSON. Python: "
    "print(os_optics.local.__doc__). The public optical catalog "
    "is downloaded into a local store on first use (one line on stderr says so; 'catalog sync' does it now; 'catalog status', "
    "'catalog import FILE' offline, 'catalog use VERSION'). 'doctor' checks this installation offline."
)
# Kinds of 'project example' (os_optics.local.example.EXAMPLE_KINDS; kept here so --help needs no import of the runtime).
EXAMPLE_KIND_CHOICES = ("focus", "folded", "beam_expander", "relay_4f", "folded_expander", "dichroic_routing", "polarization",
                        "two_beam_interference", "two_color_dichroic", "dichroic_combiner")
CATALOG_DESCRIPTION = (
    "The public optical catalog on this device: a local store (OPENSCI_CATALOG_DIR, default ~/.opensci/catalog) synchronized "
    "with the OpenSci catalog service (OPENSCI_CATALOG_URL, else OPENSCI_API_URL): once on first use, then at most every "
    "OPENSCI_CATALOG_CHECK_INTERVAL seconds (default 3600; 0 = never automatically); offline, the stored release is used. "
    "A download a command makes by itself is announced by one line on stderr. 'list' / 'show' browse the active release; "
    "commercial_readiness ORDERABLE: OpenSci supplies the product; opensci_mounting AVAILABLE: OpenSci also supplies "
    "mounting hardware for it (NOT_AVAILABLE: it is supplied without, and you mount it); delivered_form HOUSED: it comes "
    "in its manufacturer's housing, whose outline differs from the design geometry. "
    "'materials' finds published materials (maker, designation, wavelength coverage, bulk loss; --at-nm / --band-nm / "
    "--thickness-mm add the index, the dispersion across a band and the internal transmittance); 'material NAME' resolves "
    "one name as prescription import does (S-FPL53, CDGM:F5, ID:F2). "
    "'status', 'check', 'sync' (the current release, made active), 'sync --release V' (one retained release), 'use V' (a "
    "stored release), 'import FILE' (offline computers) and 'verify' manage the store."
)
CATALOG_LIST_DESCRIPTION = (
    "Published optical products as compact JSON rows, in optical_sku order; 'total' counts every match, --offset and --limit "
    "page them. The focal filters compare the signed nominal focal length a product publishes (focal_length_mm), at its "
    "design_wavelength_nm when it states one, in millimetres. Bounds are signed: negative lenses lie below zero. "
    "A product that publishes no focal length (a flat mirror, an axicon) never matches. The response then "
    "carries 'focal_filter': the bounds compared, what the value means and 'excluded_without_focal_length', how many "
    "otherwise matching products publish none. --wavelength-nm selects on simulation-model validity and does not change the "
    "value compared; 'project paraxial' gives a placed component's focal distances at a chosen wavelength."
)
CATALOG_LIST_EXAMPLE = "example: lenses near -50 mm that are valid at 633 nm: --category LENS --focal-length-mm -50 --focal-tolerance-mm 5 --wavelength-nm 633"
#: What starts a negative value on the command line (before Python 3.14, argparse takes -1e2 or -50mm for an option).
NEGATIVE_VALUE = re.compile(r"-\.?\d")


def _negative_values_attached(argv: list[str] | None) -> list[str]:
    """``argv`` with each negative value of ``catalog list`` attached to its option (``--focal-length-mm=-1e2``), the form argparse takes
    as a value on every supported Python, so -1e2 is a value and -50mm a value to refuse. Every option of that command takes a value."""

    tokens = list(sys.argv[1:] if argv is None else argv)
    if "catalog" not in tokens or "list" not in tokens[tokens.index("catalog"):]:
        return tokens
    attached: list[str] = []
    for token in tokens:
        if attached and attached[-1].startswith("--") and "=" not in attached[-1] and NEGATIVE_VALUE.match(token):
            attached[-1] = f"{attached[-1]}={token}"
        else:
            attached.append(token)
    return attached


PROJECT_DESCRIPTION = (
    "Pure-optical Projects. 'example' prints a complete reference design ('--kind': focus, folded, "
    "beam_expander, relay_4f, folded_expander, dichroic_routing, polarization, two_beam_interference, two_color_dichroic, "
    "dichroic_combiner); 'check' checks a Project without running it; 'update' applies edit operations, among them "
    "set_compute_budget (how far the trace follows the light) and set_numerical_settings (the validity limits and levels "
    "under which a value is reported), both Project state and part of its identity. "
    "'run' simulates a Project with the real Engine on this device and, with --out-dir, writes the run directory: "
    "manifest.json (start here), project.json, engine_request.json, simulation_result.json, local_validation.json (the "
    "Local Validation record), optical_scene.json and optical_scene.glb (3D Optical Simulation View) and ray_data.json "
    "(every traced chief and pupil ray; read it with 'results' / 'export'). 'evaluate' computes selected quantities and "
    "only what they require (default 'readings': the plane readings without the bounds of untraced light, the record or "
    "the view; 'complete': everything 'run' computes; or capability ids, with '--exclude' for those that must not run) "
    "and prints what was computed and what was not; '--describe' lists the capabilities. 'verify' re-checks a run "
    "directory. The exit code follows --status-contract (default requirements: 0 every declared target met or none "
    "declared, 1 one unmet, 2 one undetermined or not applicable, 3 the calculation did not complete or was stopped; "
    "'evaluate' reads the targets it assessed, and with no contract named takes execution when it assesses none; see "
    "'project evaluate --help'). A run answers "
    "separately whether the calculation completed (outcome.execution), whether the targets are met (outcome.requirements "
    "and each target's assessment) and whether each number is the model's value (each quantity's state and value_basis); "
    "the run status is a diagnostic summary. A model used outside its validity is named in model_scope_issues and on the "
    "quantities and targets it affects; 'results targets RUN' prints in_model_scope and lists them in "
    "out_of_model_issues. A Project with configurations (zoom positions, switched states) runs each configuration as its "
    "own Project: --out-dir then holds configurations/<id>/ (an ordinary run directory per configuration), "
    "comparison.json (the configurations side by side; 'results configurations RUN') and manifest.json; --configuration "
    "ID runs only some. A run uses the public catalog release its components pin; 'update-catalog' moves a Project to "
    "the active release. 'model-from-catalog' prints a catalog product's model as a declared optical model "
    "(optical_models, model_materials, model_coatings) for a custom_part component's optical_model_id; a Project of "
    "declared models needs no catalog."
)
RUN_DESCRIPTION = (
    "Simulate a Project with the real Engine on this device and print the run summary as JSON: execution (whether the "
    "calculation completed), requirements (the declared targets MET, UNMET, INDETERMINATE or NOT_APPLICABLE), the "
    "diagnostic summary, the targets, the model-scope findings and the identity. Every quantity of the result states its "
    "state; a number that is a bound or an estimate carries its value_basis entry (kind LOWER_BOUND, UPPER_BOUND or "
    "ESTIMATE, with what it omits, assumes or exceeds): a bound decides a target only in the direction it proves; an "
    "estimate's number alone decides none, and a proven interval the result states beside it (the target's message) decides "
    "the target when the whole interval meets or misses it. The Project's compute_budget and numerical_settings are part of the run: the values in "
    "force are in the result's manifest (numerical_tolerances) and in outcome.execution.budgets.limits. With --out-dir the run directory is written: manifest.json (start here), project.json, "
    "engine_request.json, simulation_result.json, local_validation.json (the Local Validation record), optical_scene.json "
    "and optical_scene.glb (3D Optical Simulation View) and ray_data.json; read it with 'results', 'export' and 'view'. "
    "A Project with configurations runs each configuration as its own Project (--configuration selects). The exit code "
    "follows --status-contract (below)."
)


def _json_out(doc: object) -> None:
    # ASCII JSON (non-ASCII text as \\u escapes): readable with any default file encoding and console code page
    print(json.dumps(doc, indent=2, ensure_ascii=True))


def _read(path: str, what: str) -> bytes | None:
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        print(f"error: cannot read the {what}: {exc}", file=sys.stderr)
        return None


def _catalog_command(args: argparse.Namespace) -> int:
    from ._vendor.shared_contracts.design_catalog import FocalLengthQuery
    from .local import LocalRunError, LocalRunErrorCode, LocalRunProblem, load_public_catalog
    from .local.json_api import product_summary

    source = None
    if args.catalog:
        source = _read(args.catalog, "catalog")
        if source is None:
            return USAGE_ERROR
    try:
        catalog = load_public_catalog(source)
        if args.catalog_command == "show":
            product = catalog.get(args.sku)
            if product is None:
                raise LocalRunError([LocalRunProblem(
                    LocalRunErrorCode.OPTICAL_SKU_NOT_PUBLISHED, f"{args.sku} is not published in public catalog {catalog.catalog_version}"
                )])
            _json_out(product.model_dump(mode="json", by_alias=True, exclude_none=True))
            return 0
        focal = FocalLengthQuery.of(focal_length_mm=args.focal_length_mm, focal_tolerance_mm=args.focal_tolerance_mm,
                                    focal_min_mm=args.focal_min_mm, focal_max_mm=args.focal_max_mm, focal_sign=args.focal_sign)
        products, focal_filter = catalog.select_by_focal_length(focal, category=args.category, form=args.form, wavelength_nm=args.wavelength_nm,
                                                                readiness=args.readiness, mounting=args.mounting)
    except LocalRunError as exc:
        _json_out(exc.as_dict())
        return LOCAL_INPUT_ERROR
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return USAGE_ERROR
    if args.offset < 0 or (args.limit is not None and args.limit < 0):
        print("error: --offset and --limit must be non-negative", file=sys.stderr)
        return USAGE_ERROR
    page = products[args.offset:] if args.limit is None else products[args.offset:args.offset + args.limit]
    _json_out({
        "catalog_version": catalog.catalog_version,
        "mechanical_accessories": catalog.mechanical_accessories.value,
        "total": len(products),
        "offset": args.offset,
        "limit": args.limit,
        **({"focal_filter": focal_filter} if focal_filter is not None else {}),
        "products": [product_summary(p) for p in page],
    })
    return 0


def _project_command(args: argparse.Namespace) -> int:
    if args.project_command == "example":
        from .local import LocalRunError, example_project_of_kind

        if args.folded and args.kind not in (None, "folded"):
            print("error: --folded is --kind folded; give one of them", file=sys.stderr)
            return USAGE_ERROR
        try:
            project = example_project_of_kind(args.kind or ("folded" if args.folded else "focus"))
        except LocalRunError as exc:
            _json_out(exc.as_dict())
            return LOCAL_INPUT_ERROR
        _json_out(project.payload())
        return 0
    if args.project_command == "verify":
        from .local import verify_run_artifacts

        from .local.artifacts import later_attempt_in

        problems = verify_run_artifacts(args.run_dir)
        attempt = later_attempt_in(args.run_dir) if not problems else None
        _json_out({"run_dir": args.run_dir, "status": "CONSISTENT" if not problems else "INCONSISTENT", "problems": list(problems),
                   **({"later_attempt": attempt} if attempt is not None else {})})
        return 0 if not problems else VERIFY_FAILED
    return _project_run(args)


def _project_run(args: argparse.Namespace) -> int:
    """``project run``. A Ctrl-C before the run starts (while the local workflow is imported, the Project and catalog files
    are read and the Project is checked) stops it as one in the run: the record of a run stopped before lowering, exit
    130. Only a run that completes changes the run in ``--out-dir``; a stop adds its record (``local/artifacts.py``)."""

    from .run_control import RunStopped, monitored
    from .run_monitor import RunMonitor

    monitor = RunMonitor(_progress_printer(args.progress), max_memory_bytes=_mib(args.max_memory_mib),
                         max_artifact_bytes=_mib(args.max_artifact_mib), preflight=not args.no_preflight)
    document = None
    contract = None
    try:
        contract = _status_contract(args)
        if contract is None:
            return USAGE_ERROR
        from .local import (
            LocalRunError,
            OpticalViewSettingsV1,
            PupilRaySelectionV1,
            load_project,
            run_configured_project,
            run_optical_project,
        )

        if args.out_dir:
            from .local.artifacts import _is_link

            if _is_link(Path(args.out_dir)):
                print(f"error: --out-dir {args.out_dir} is a link (a symbolic link or a junction); a run never writes "
                      "through a link: give the directory it points to, or another directory", file=sys.stderr)
                return USAGE_ERROR
        project = _read(args.project, "project")
        catalog = _read(args.catalog, "catalog") if args.catalog else None
        if project is None or (args.catalog and catalog is None):
            return USAGE_ERROR
        settings = OpticalViewSettingsV1(pupil_ray_selection=PupilRaySelectionV1(args.pupil_rays))
        try:
            document = load_project(project)
        except LocalRunError as exc:
            _json_out(exc.as_dict())
            return LOCAL_INPUT_ERROR
    except KeyboardInterrupt:
        return _stopped(monitor.stopped_by_interrupt(), document, monitor, contract or DEFAULT_STATUS_CONTRACT, args.out_dir)
    if document.configurations or args.configuration:
        if args.output:
            print("error: -o writes the Local Validation record of one run; a Project with configurations writes one per "
                  "configuration with --out-dir (configurations/<id>/local_validation.json)", file=sys.stderr)
            return USAGE_ERROR
        try:
            with monitored(monitor):
                configured = run_configured_project(document, catalog=catalog, model_kind=args.model_kind, view_settings=settings,
                                                    ray_data=args.ray_data, configuration_ids=args.configuration,
                                                    output_dir=args.out_dir)
        except RunStopped as stopped:
            return _stopped(stopped, document, monitor, contract, args.out_dir)
        except LocalRunError as exc:
            _json_out(exc.as_dict())
            return LOCAL_INPUT_ERROR
        except OSError as exc:
            print(f"error: cannot write the run directory: {exc}", file=sys.stderr)
            return USAGE_ERROR
        _json_out({**configured.summary(), "exit_contract": contract})
        return configurations_exit_code(contract, configured)
    try:
        # (the run directory is written inside the run, so the monitor stops a write that would pass the artifact limit)
        run = run_optical_project(document, catalog=catalog, model_kind=args.model_kind, view_settings=settings,
                                  ray_data=args.ray_data, monitor=monitor, output_dir=args.out_dir or None)
    except RunStopped as stopped:
        return _stopped(stopped, document, monitor, contract, args.out_dir)
    except LocalRunError as exc:
        _json_out(exc.as_dict())
        return LOCAL_INPUT_ERROR
    except OSError as exc:
        print(f"error: cannot write the run directory: {exc}", file=sys.stderr)
        return USAGE_ERROR
    _json_out({**run.summary(), "exit_contract": contract})
    if args.output:
        try:
            Path(args.output).write_text(json.dumps(run.local_validation.payload(), indent=2, sort_keys=True, ensure_ascii=True) + "\n", encoding="utf-8")
        except OSError as exc:
            print(f"error: cannot write the record: {exc}", file=sys.stderr)
            return USAGE_ERROR
    return run_exit_code(contract, run)


def _stopped(stopped, document, monitor, contract: str, out_dir: str | None) -> int:
    """Print the record of a run that stopped before it completed (``StoppedRun``), write it into ``out_dir``
    (``run_stopped.json``, beside an earlier run there, which stays as it was) and return the exit code."""

    from .local.run import stop_run

    stop_run(stopped, document, monitor, output_dir=out_dir or None)
    record = stopped.stopped.model_dump(mode="json")
    _json_out({"status": "CANCELLED", **record, "exit_contract": contract})
    interrupted = stopped.cause == "CALLER"
    print(f"os-optics: the run stopped in phase {stopped.phase}: {stopped.message}; no result of it is reported", file=sys.stderr)
    return stopped_exit_code(contract, interrupted=interrupted)


def _mib(value: int | None) -> int | None:
    return None if value is None else value << 20


def _positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a whole number") from None
    if value < 1:
        raise argparse.ArgumentTypeError(f"{value} is not positive")
    return value


#: ``project run --progress``: how progress lines reach stderr.
PROGRESS_MODES = ("auto", "text", "json", "none")


def _progress_text(event: dict) -> str:
    """One progress line: ``os-optics: <phase> [started | done] [<done>[/<total>] <unit>], <s> s[, <MiB> MiB]``."""

    done, total, unit = event.get("done"), event.get("total"), event.get("unit")
    kind = event.get("event")
    count = ""
    if kind != "phase_started" and done is not None and unit and unit not in ("steps", "estimate") and (done or total):
        count = f" {done}/{total} {unit}" if total is not None else f" {done} {unit}"
    verb = {"phase_started": " started", "phase_ended": " done"}.get(kind, "")
    estimate = event.get("estimate")
    if estimate:
        mib = 1 << 20
        memory_estimate = estimate.get("memory_bytes")
        if estimate.get("stage") == "after_trace":  # (from what the trace counted: what it makes certain, up to the high end)
            # (the certain low end is counted only for a run with a run-file limit that refuses on it)
            low = estimate.get("artifact_bytes_low")
            count = (f": after the trace, {estimate.get('paths')} beam paths of {estimate.get('steps')} steps; run files "
                     f"estimated at {estimate.get('artifact_bytes', 0) / mib:.1f} MiB ("
                     + (f"at least {low / mib:.2f}, " if low is not None else "")
                     + f"up to {estimate.get('artifact_bytes_high', 0) / mib:.1f})"
                     + (f", memory at {memory_estimate / mib:.0f} MiB (up to {estimate.get('memory_bytes_high', 0) / mib:.0f})"
                        if memory_estimate else ""))
        else:
            count = (f": {estimate.get('beams')} beams, run files estimated at {estimate.get('artifact_bytes', 0) / mib:.1f} MiB"
                     + (f", memory at {memory_estimate / mib:.0f} MiB" if memory_estimate else ""))
    memory = event.get("memory_bytes")
    memory_text = f", {memory / (1 << 20):.0f} MiB" if memory else ""
    phase = " > ".join(event.get("phases") or [event.get("phase") or "run"])
    return f"os-optics: {phase}{verb}{count}, {event.get('elapsed_s', 0.0):.1f} s{memory_text}"


def _progress_printer(mode: str):
    """The monitor's progress callback for ``--progress``: text lines, JSON lines or nothing on stderr (``auto``: text
    lines when stderr is a terminal)."""

    if mode == "auto":
        mode = "text" if sys.stderr.isatty() else "none"
    if mode == "none":
        return None
    if mode == "json":
        return lambda event: print(json.dumps(event, sort_keys=True), file=sys.stderr, flush=True)
    return lambda event: print(_progress_text(event), file=sys.stderr, flush=True)


def _status_contract(args: argparse.Namespace) -> str | None:
    """The status contract of a run command (``cli_exit``: ``--status-contract``, else ``OS_OPTICS_STATUS_CONTRACT``, else
    the default); None, after saying why on stderr, when the environment names none."""

    try:
        return resolve_status_contract(args, os.environ)
    except StatusContractError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return None


def _named_status_contract(args: argparse.Namespace) -> tuple[bool, str | None]:
    """The contract the command line names (``cli_exit.chosen_status_contract``: ``--status-contract``, else
    ``OS_OPTICS_STATUS_CONTRACT``; ``None`` when neither does), and False, after saying why on stderr, when the
    environment names none."""

    try:
        return True, chosen_status_contract(args, os.environ)
    except StatusContractError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return False, None


def _model_from_catalog(args: argparse.Namespace) -> int:
    """``project model-from-catalog SKU``: the product's model as a declared optical model (``json_api.catalog_model``)."""

    from .local import json_api

    if args.catalog:
        data = _read(args.catalog, "catalog")
        if data is None:
            return USAGE_ERROR
        installed = json_api.install_catalog(data)
        if not installed["ok"]:
            _json_out({"status": "ERROR", "error": installed["error"]})
            return LOCAL_INPUT_ERROR
    try:
        result = json_api.catalog_model(args.sku, model_id=args.model_id, model_kind=args.model_kind)
    finally:
        if args.catalog:
            from .local import clear_installed_catalogs

            clear_installed_catalogs()
    if not result["ok"]:
        _json_out({"status": "ERROR", "error": result["error"]})
        return LOCAL_INPUT_ERROR
    _json_out({key: value for key, value in result.items() if key != "ok"})
    return 0


def _tolerance_value(text: str) -> tuple[str, int | float]:
    from .assembly import tolerance_argument

    try:
        return tolerance_argument(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def add_tolerance_argument(parser: argparse.ArgumentParser) -> None:
    """``--tolerance NAME=VALUE`` (repeatable): a threshold of the local assembly check other than its default."""

    parser.add_argument("--tolerance", action="append", type=_tolerance_value, metavar="NAME=VALUE",
                        help="set a threshold of the assembly check (repeatable; any field of its recorded tolerances, e.g. "
                             "max_total_triangles=250000, the work limit); the values used are recorded in the result")


def _assembly_command(args: argparse.Namespace) -> int:
    from .assembly import AssemblyPreviewPackageError, load_assembly_preview, result_file_bytes, tolerances_with, validate_assembly_layout

    try:
        tolerances = tolerances_with(dict(getattr(args, "tolerance", None) or ()))
    except ValueError as exc:
        print(f"error: --tolerance: {exc}", file=sys.stderr)
        return USAGE_ERROR
    try:
        if args.assembly_command == "verify":
            package = load_assembly_preview(args.package)
            _json_out({"package": args.package, "status": "VERIFIED", "package_content_hash": package.content_hash, "problems": []})
            return 0
        result = validate_assembly_layout(args.package, tolerances=tolerances)
    except AssemblyPreviewPackageError as exc:
        if args.assembly_command == "verify":
            _json_out({"package": args.package, "status": "INVALID", "problems": list(exc.problems)})
            return VERIFY_FAILED
        _json_out({"status": "ERROR", "error": {"code": ASSEMBLY_PACKAGE_INVALID, "problems": list(exc.problems)}})
        return LOCAL_INPUT_ERROR
    except OSError as exc:
        print(f"error: cannot read the package: {exc}", file=sys.stderr)
        return USAGE_ERROR
    if args.out:
        try:
            Path(args.out).write_bytes(result_file_bytes(result))
        except OSError as exc:
            print(f"error: cannot write the result: {exc}", file=sys.stderr)
            return USAGE_ERROR
        _json_out({
            "status": result.status.value,
            "interference": result.summary.interference.value,
            "package_content_hash": result.package.package_content_hash,
            "result_hash": result.result_hash,
            "checks": {c.check.value: c.status.value for c in result.checks},
            "issues": len(result.issues),
            "out": args.out,
        })
    else:
        sys.stdout.write(result_file_bytes(result).decode("utf-8"))
    return EXIT_CODES[result.status.value]


def _drawing_command(args: argparse.Namespace) -> int:
    from .assembly_drawing import AssemblyDrawingError, generate_assembly_drawing, verify_assembly_drawing
    from .assembly_drawing.generate import drawing_output_files, unrelated_output_entries

    if args.assembly_command == "verify-drawing":
        # Only the four drawing files are read and verified (strictly); anything else in the directory is reported as
        # a warning, not read and not an error.
        warnings: list[str] = []
        try:
            files = drawing_output_files(args.drawing_dir, known_only=True)
            warnings = [f"{name}: not a drawing file, ignored" for name in unrelated_output_entries(args.drawing_dir)]
            problems = verify_assembly_drawing(files)
        except AssemblyDrawingError as exc:
            problems = exc.problems
        except OSError as exc:
            problems = (f"cannot read the drawing outputs: {type(exc).__name__}",)
        _json_out({"drawing_dir": args.drawing_dir, "status": "VERIFIED" if not problems else "INVALID", "problems": list(problems),
                   "warnings": warnings})
        return 0 if not problems else VERIFY_FAILED
    try:
        drawing = generate_assembly_drawing(args.package, args.detail, locale=args.locale, diagnostic=args.diagnostic)
    except AssemblyDrawingError as exc:
        _json_out({"status": "ERROR", "error": exc.as_dict()})
        return LOCAL_INPUT_ERROR
    except RecursionError:  # pragma: no cover - the generator converts every nesting failure to an AssemblyDrawingError
        _json_out({"status": "ERROR", "error": {"code": "INPUT_INVALID", "problems": [
            {"code": "INPUT_INVALID", "message": "an input document is nested too deeply"}]}})
        return LOCAL_INPUT_ERROR
    try:
        drawing.write(args.out)
    except OSError as exc:
        print(f"error: cannot write the drawing: {exc}", file=sys.stderr)
        return USAGE_ERROR
    result = drawing.result
    _json_out({
        "status": result.drawing_status.value,
        "delivery_ready": result.delivery_ready,
        "locale": result.locale,
        "validation_status": result.validation.status.value,
        "preview_package_content_hash": result.binding.preview_package_content_hash,
        "detail_content_hash": result.binding.detail_content_hash,
        "result_hash": result.result_hash,
        "manifest_content_hash": drawing.manifest.manifest_content_hash,
        "files": {f.path: f.sha256 for f in drawing.manifest.files},
        "out": args.out,
    })
    # (the drawing is written either way: status and delivery_ready say which sheet it is)
    return 0 if result.delivery_ready else DIAGNOSTIC_SHEET_WRITTEN


def _design_schema(which: str) -> dict:
    from .assembly_plan.schemas import PLAN_SCHEMA_KINDS, plan_schema

    if which in PLAN_SCHEMA_KINDS:
        return plan_schema(which)
    from .assembly import AssemblyLocalValidationV1
    from .local import LocalValidationRecordV1, OpticalSimulationSceneV1, ProjectDocumentV2, PublicOpticalCatalogV2

    from ._vendor.shared_contracts.optical_design import DetectorModelV1, PupilFieldRequestV1
    from .contracts.analysis import AnalysisResultV1
    from .contracts.focal_field import FocalFieldRequestV1
    from .contracts.ghosts import GhostRequestV1
    from .contracts.imaging import ImagingRequestV1
    from .contracts.network import LinearOpticalNetworkV1
    from .contracts.source_field import FieldRequestV1

    model = {"project": ProjectDocumentV2, "local-validation": LocalValidationRecordV1, "catalog": PublicOpticalCatalogV2,
             "optical-scene": OpticalSimulationSceneV1, "assembly-validation": AssemblyLocalValidationV1,
             "linear-optical-network": LinearOpticalNetworkV1, "pupil-field-request": PupilFieldRequestV1, "detector-model": DetectorModelV1,
             "focal-field-request": FocalFieldRequestV1, "imaging-request": ImagingRequestV1, "ghost-request": GhostRequestV1,
             "field-request": FieldRequestV1, "analysis-result": AnalysisResultV1}[which]
    return model.model_json_schema(by_alias=True)


def _register_command_modules(sub: argparse._SubParsersAction, *, assembly: argparse._SubParsersAction,
                              catalog: argparse._SubParsersAction, project: argparse._SubParsersAction) -> None:
    """Command modules add their own commands here; each sets ``handler`` on its parsers (``main`` calls it).

    One call per module, each on its own line."""

    from . import (cli_catalog, cli_commerce, cli_doctor, cli_evaluate, cli_interop, cli_layout, cli_native, cli_network, cli_paraxial,
                   cli_prescription, cli_results, cli_solids, cli_view)
    from . import cli_assembly_plan, cli_project, cli_workspace

    cli_doctor.register(sub)  # doctor
    cli_project.register(sub)  # capabilities
    cli_project.register_project(project)  # project check|update
    cli_layout.register(sub)  # layout draw
    cli_results.register(sub)  # results describe|targets|freshness|monitors|monitor|paths|power-budget|rays|wavefront|irradiance|metrics|columns, export
    cli_catalog.register(catalog)  # catalog status|check|sync|verify|import|use
    cli_catalog.register_project(project)  # project update-catalog
    cli_solids.register_project(project)  # project solids
    cli_prescription.register_project(project)  # project import-prescription
    cli_network.register_project(project)  # project network
    cli_paraxial.register_project(project)  # project paraxial|cavity
    cli_evaluate.register_project(project)  # project evaluate
    cli_commerce.register_view(cli_view.register(sub))  # view RUN_DIR | view --resolution ID
    cli_commerce.register(sub, assembly=assembly)  # account, resolve, resolution, order, artifacts, feedback, assembly preview|drawing
    cli_assembly_plan.register(assembly)  # assembly plan derive|show|check|edit|history|preview|draw|bom|run-optics
    cli_native.register(sub)  # native list|describe|build
    cli_interop.register(sub)  # interop list|show
    cli_workspace.register(sub)  # workspace init|import|select|transform|group|replace|batch|export|status|run|record|history


def _utf8_output() -> None:
    """Command output is JSON, and JSON is UTF-8: when stdout is a file or a pipe (not a console), write UTF-8
    whatever the system code page is, unless PYTHONIOENCODING chooses the output encoding explicitly."""

    try:
        if not sys.stdout.isatty() and not os.environ.get("PYTHONIOENCODING"):
            sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError, ValueError):
        pass




def _command_name() -> str:
    """The name this command was started with (its console script), ``os-optics`` for ``python -m`` and anything else."""

    started = Path(sys.argv[0]).name.lower() if sys.argv and sys.argv[0] else ""
    for suffix in (".exe", "-script.py", ".py"):
        started = started.removesuffix(suffix)
    return started if started in COMMAND_NAMES else COMMAND_NAMES[0]


def main(argv: list[str] | None = None, *, prog: str | None = None) -> int:
    """The ``os-optics`` command (``prog``: the name its usage and ``--version`` show; default: the name it was
    started with). A reader that closes the output early (``| head``) ends the command quietly. Ctrl-C ends it with exit
    code 130: ``project run`` prints the stopped run's record (``_project_run``); before a command has started its work
    (while it is imported, ``os_optics._command``, or reads its arguments) one line says that no run started and nothing
    was written or removed; any other command interrupted says it stopped before it completed. Any other exception a
    command lets through is an internal error: its JSON document (``cli_exit.internal_error_document``) on stdout, its
    traceback on stderr, exit code 70, so a defect is never read as the exit code of a result."""

    started = {"work": False}  # (set once the arguments are read and the command begins its work)
    try:
        return _main(argv, prog or _command_name(), started)
    except KeyboardInterrupt:
        try:
            print(INTERRUPTED_COMMAND if started["work"] else INTERRUPTED_WHILE_STARTING, file=sys.stderr, flush=True)
        except (OSError, ValueError):
            pass
        return INTERRUPTED_EXIT
    except BrokenPipeError:
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
        except (OSError, ValueError, AttributeError):
            pass
        return 0
    except Exception as exc:  # (an internal defect: named as one)
        return _internal_error(exc)


def _internal_error(exc: BaseException) -> int:
    """Print an internal error (``cli_exit.internal_error_document`` on stdout, the traceback on stderr); exit code 70."""

    try:
        print(internal_error_traceback(exc), end="", file=sys.stderr, flush=True)
        print(json.dumps(internal_error_document(exc), indent=2), flush=True)
    except (OSError, ValueError):
        pass
    return INTERNAL_ERROR_EXIT


def _main(argv: list[str] | None, prog: str, started: dict | None = None) -> int:
    _utf8_output()
    parser = ArgumentParser(prog=prog, description=MAIN_DESCRIPTION, epilog=EXIT_CODE_HELP)
    parser.add_argument("--version", action="version", version=f"{prog} {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    p_val = sub.add_parser("validate", help="low-level: run one Engine request JSON file",
                           description="Run one Engine ValidationRequest and print its ValidationResult (the document itself, "
                                       "unchanged; with -o it is written to that file). The exit code follows --status-contract.",
                           epilog=EXIT_CODE_HELP)
    p_val.add_argument("request", help="path to request JSON ('-' for stdin)")
    p_val.add_argument("-o", "--output", help="write the Engine result JSON here (default: stdout)")
    p_val.add_argument("--pretty", action="store_true", help="indent the output JSON")
    add_status_contract_argument(p_val)
    p_schema = sub.add_parser("schema", help="print a JSON Schema: design project / local-validation / catalog / optical-scene / "
                                             "assembly-validation / linear-optical-network / pupil-field-request / detector-model, an analysis request "
                                             "(focal-field-request / imaging-request / ghost-request / field-request) or an analysis-result, or Engine request / result")
    p_schema.add_argument("which", choices=["project", "local-validation", "catalog", "optical-scene", "assembly-validation",
                                            "linear-optical-network", "pupil-field-request", "detector-model", "focal-field-request",
                                            "imaging-request", "ghost-request", "field-request", "analysis-result", "assembly-plan",
                                            "assembly-plan-ops", "design-bom", "purchase-bom", "bom-review", "public-mechanical-catalog",
                                            "request", "result"])
    p_cmp = sub.add_parser("compare", help="compare two Engine result JSON files within the cross-runtime tolerances")
    p_cmp.add_argument("left")
    p_cmp.add_argument("right")
    p_cat = sub.add_parser("catalog", help="the public optical catalog: browse the active release, manage the local store",
                           description=CATALOG_DESCRIPTION)
    p_cat.add_argument("--catalog", help="list / show: use this public optical catalog JSON instead of the active release")
    cat_sub = p_cat.add_subparsers(dest="catalog_command", required=True)
    p_list = cat_sub.add_parser("list", help="list published optical products", description=CATALOG_LIST_DESCRIPTION, epilog=CATALOG_LIST_EXAMPLE)
    p_list.add_argument("--category", help="e.g. LENS, MIRROR, BEAMSPLITTER, PRISM")
    p_list.add_argument("--form", help="e.g. PLANO_CONVEX_LENS, CUBE_BEAMSPLITTER, RIGHT_ANGLE_PRISM")
    p_list.add_argument("--wavelength-nm", help="only products with a simulation model valid at this wavelength; the focal filters "
                                                "still compare the nominal focal length")
    p_list.add_argument("--focal-length-mm", metavar="F",
                        help="only products of this signed nominal focal length in mm (a negative lens: -50); exact unless "
                             "--focal-tolerance-mm is given; not with --focal-min-mm / --focal-max-mm")
    p_list.add_argument("--focal-tolerance-mm", metavar="T",
                        help="accept F - T to F + T mm; your own tolerance, separate from the focal_length_tolerance_percent a product publishes")
    p_list.add_argument("--focal-min-mm", metavar="MIN", help="lowest signed nominal focal length in mm, inclusive")
    p_list.add_argument("--focal-max-mm", metavar="MAX", help="highest signed nominal focal length in mm, inclusive")
    p_list.add_argument("--focal-sign", choices=("positive", "negative"), help="only products whose nominal focal length has this sign")
    p_list.add_argument("--readiness", choices=("ORDERABLE", "SIMULATION_ONLY"),
                        help="ORDERABLE: products OpenSci supplies; SIMULATION_ONLY: design and simulation only")
    p_list.add_argument("--mounting", choices=("AVAILABLE", "NOT_AVAILABLE"),
                        help="AVAILABLE: OpenSci also supplies mounting hardware for the product; NOT_AVAILABLE: it does not "
                             "(an ORDERABLE product is then supplied without mounting hardware)")
    p_list.add_argument("--offset", type=int, default=0, help="skip this many matching products (paging)")
    p_list.add_argument("--limit", type=int, default=None, help="list at most this many products (default: all)")
    p_show = cat_sub.add_parser("show", help="print one product's public document")
    p_show.add_argument("sku", help="an OS-OPT-... optical SKU")
    p_proj = sub.add_parser("project", help="create, run and verify a pure-optical design (a Project)", description=PROJECT_DESCRIPTION,
                            epilog=EXIT_CODE_HELP)
    proj_sub = p_proj.add_subparsers(dest="project_command", required=True)
    from .cli_paraxial import EXAMPLE_POINTER

    example = proj_sub.add_parser("example", help="print a complete reference Project to copy and edit", epilog=EXAMPLE_POINTER)
    example.add_argument("--kind", choices=list(EXAMPLE_KIND_CHOICES), help="which reference design (default: focus)")
    example.add_argument("--folded", action="store_true", help="the folded design (--kind folded): source -> fold mirror -> lens -> focal plane")
    p_run = proj_sub.add_parser("run", help="simulate the Project; with --out-dir write results, local validation record and 3D optical view",
                                description=RUN_DESCRIPTION, epilog=EXIT_CODE_HELP)
    p_run.add_argument("project", help="path to a Project JSON file")
    p_run.add_argument("--out-dir", help="write the run directory here (manifest.json, simulation_result.json, local_validation.json, "
                                         "optical_scene.json, optical_scene.glb, ray_data.json, ...). A run that completes "
                                         "replaces the run there: its files are written to temporary names first and moved "
                                         "into place once all are written, and files of the earlier run the new one does not "
                                         "write are removed. A run that does not complete -- a usage error, a refused Project, "
                                         "a stop, a failure before the run is moved into place -- leaves the directory as it "
                                         "found it: the earlier run there still verifies, and 'results freshness' says whether "
                                         "it is current for a Project. A failure or Ctrl-C while the run is moved into place (a "
                                         "file held open elsewhere) leaves what a process killed there leaves: past the move's "
                                         "first step, no manifest.json, so no run that verifies, and the Engine's records of what "
                                         "is there (.manifest.json.earlier, the staged .manifest.json.partial). The first step "
                                         "removes the run files of each configuration that did not run this time, then the stop "
                                         "records, then the configurations' manifests, then the directory's own. Stopped while "
                                         "those run files are removed (one held open elsewhere), it leaves the directory's "
                                         "manifest.json and that configuration's, without the run files removed before: "
                                         "'project verify' then reports INCONSISTENT (exit 66), naming them as missing. Stopped "
                                         "while the configurations' manifests are removed (a configuration's manifest.json held "
                                         "open elsewhere), it leaves the directory's manifest.json beside a configuration whose "
                                         "manifest is gone, recorded by its copy (configurations/<id>/.manifest.json.earlier): "
                                         "'project verify' then reports INCONSISTENT (exit 66), naming that manifest. Either "
                                         "way any run, the same one or the earlier design, then completes there. The "
                                         "run replaces only what the Engine wrote: a file at a run-file name that no Engine manifest there "
                                         "lists (nor the Engine's record of what a completed run could not remove, "
                                         ".manifest.json.leftover), or a run kept under configurations/<id> that the manifest "
                                         "does not name, is named and the run exits 64; other files are never touched. project.json is also the "
                                         "name of a design file: one holding another design that no run manifest there lists "
                                         "with its bytes is refused the same way (one holding the Project being run is written "
                                         "again in canonical form). A run never writes through a symbolic link "
                                         "or junction: --out-dir itself, or one under it (exit 64)")
    p_run.add_argument("-o", "--output", help="also write only the Local Validation record (JSON) to this file")
    add_status_contract_argument(p_run)
    p_run.add_argument("--pupil-rays", choices=["OUTER_RING", "ALL", "NONE"], default="OUTER_RING",
                       help="which Engine pupil rays the 3D view draws (display only; default OUTER_RING)")
    p_run.add_argument("--ray-data", choices=["full", "summary"], default="full",
                       help="what ray_data.json records: full (default) every chief and pupil ray; summary the chief rays and "
                            "each path's pupil sampling, about 1%% of the size, for runs of hundreds of beams (the trace, the "
                            "results and every hash are the same; 'results rays' and 'results wavefront' then need a full run)")
    p_run.add_argument("--progress", choices=list(PROGRESS_MODES), default="auto",
                       help="progress lines on stderr while the run goes through its phases (lowering, trace, observation results, "
                            "untraced light, return-light analysis, bounds, exposures, beam pairs, targets, result, ray paths, model scope, scene, "
                            "writing): text, json (one JSON object a line), none; auto (default) prints text when stderr is a "
                            "terminal. Ctrl-C stops the run: it prints its record (execution CANCELLED, the phase it stopped in, "
                            "every observation and target unevaluated) and exits 130; --out-dir then holds that record, "
                            "run_stopped.json, beside whatever was there: an earlier run stays as it was, and 'project "
                            "verify' and 'results' name the later attempt (later_attempt)")
    p_run.add_argument("--max-memory-mib", type=_positive_int, metavar="MIB",
                       help="stop the run when this process's memory is found at or above MIB mebibytes (read at the run's checkpoints, "
                            "at most every 0.1 s, so a short allocation between two reads can pass it; a run whose "
                            "estimate from its beams already exceeds it is refused before the trace); the run prints its record "
                            "(execution CANCELLED, cause MEMORY_LIMIT) instead of a result")
    p_run.add_argument("--max-artifact-mib", type=_positive_int, metavar="MIB",
                       help="stop the run before its run files exceed MIB mebibytes (a run whose estimate from its beams already exceeds "
                            "it is refused before the trace, one whose traced paths and steps make its files larger for certain "
                            "when the trace ends); the run prints its record (execution CANCELLED, cause ARTIFACT_LIMIT) and "
                            "--out-dir holds its record beside whatever was there, as after Ctrl-C (see --progress). --ray-data summary and --pupil-rays NONE make the "
                            "files smaller")
    p_run.add_argument("--no-preflight", action="store_true",
                       help="go on with the run even when an estimate (from its beams before the trace, from its traced paths after it) "
                            "exceeds --max-memory-mib or --max-artifact-mib (an estimate can exceed what a run needs); the "
                            "limits then hold during the run only")
    p_run.add_argument("--catalog", help="use this public optical catalog JSON instead of the release the Project pins / the active one")
    p_run.add_argument("--model-kind", help="simulation model kind to use when a product publishes several")
    p_run.add_argument("--configuration", action="append", metavar="ID",
                       help="a Project with configurations: run only this configuration (repeat for several; default: every "
                            "configuration). Each configuration runs as its own Project into --out-dir/configurations/ID, "
                            "beside comparison.json (the configurations side by side) and manifest.json")
    p_model = proj_sub.add_parser("model-from-catalog", help="print a catalog product's simulation model as a declared optical model to "
                                                             "insert into a Project (optical_models, model_materials, model_coatings)",
                                  description="Print a catalog product's simulation model as a declared optical model: the "
                                              "optical_model with the model_materials and model_coatings it uses, all copied "
                                              "into the JSON so the Project needs no catalog. Insert them into a Project and "
                                              "give a custom_part component that optical_model_id: it traces exactly like the "
                                              "product, and you can edit its radii, thicknesses and materials. The component is "
                                              "then your own optic (customer-supplied), not ordered from OpenSci.",
                                  epilog="exit codes: 0 = printed, 64 = unreadable catalog file, 65 = unknown SKU / invalid model id / no catalog")
    p_model.add_argument("sku", help="an OS-OPT-... optical SKU")
    p_model.add_argument("--model-id", help="the declared model's id (default: the SKU)")
    p_model.add_argument("--model-kind", help="simulation model kind to copy when the product publishes several")
    p_model.add_argument("--catalog", help="use this public optical catalog JSON instead of the active release")
    p_model.set_defaults(handler=_model_from_catalog)
    p_verify = proj_sub.add_parser("verify", help="re-check the hashes and cross-references of a run directory",
                                   epilog="exit codes: 0 = consistent, 66 = inconsistent (the problems are listed on stdout), 64 = usage error. "
                                          "The run that is there is checked; when a later run into the directory stopped and left it "
                                          "as it was, later_attempt states that attempt (execution, cause, phase, project_hash, "
                                          "same_project)")
    p_verify.add_argument("run_dir", help="a directory written by 'project run --out-dir'")
    p_asm = sub.add_parser("assembly", help="verify an Assembly Preview Package and run the local layout / collision checks",
                           description=ASSEMBLY_DESCRIPTION, epilog=EXIT_CODE_HELP)
    asm_sub = p_asm.add_subparsers(dest="assembly_command", required=True)
    p_asm_verify = asm_sub.add_parser("verify", help="verify the package only (exit 0 = verified, 66 = invalid, problems on stdout)",
                                      description=ASSEMBLY_DESCRIPTION)
    p_asm_verify.add_argument("package", help="assembly-preview .zip archive or package directory")
    p_asm_validate = asm_sub.add_parser("validate", help="verify, then run the local layout and collision checks",
                                        description=ASSEMBLY_DESCRIPTION, epilog=EXIT_CODE_HELP)
    p_asm_validate.add_argument("package", help="assembly-preview .zip archive or package directory")
    p_asm_validate.add_argument("--out", help="write the assembly check result (JSON) here and print a short summary "
                                              "(default: print the full result)")
    add_tolerance_argument(p_asm_validate)
    p_asm_draw = asm_sub.add_parser("draw", help="generate the local Detailed Assembly Drawing (SVG, PDF, result, manifest)",
                                    description=DRAWING_DESCRIPTION,
                                    epilog="exit codes: 0 = Detailed Assembly Drawing written, 2 = diagnostic sheet written "
                                           "(--diagnostic: local assembly check FAILED or incomplete; status and delivery_ready "
                                           "say which sheet), 65 = refused (JSON error on stdout; also a local assembly check "
                                           "that did not pass without --diagnostic, nothing written), 64 = usage error / "
                                           "unwritable output, 70 = an internal error of os-optics")
    p_asm_draw.add_argument("package", help="assembly-preview .zip archive or package directory")
    p_asm_draw.add_argument("--detail", required=True, help="the Assembly Detail Package JSON of that preview package")
    p_asm_draw.add_argument("--out", required=True, help="output directory (created if needed); only the four drawing files "
                            "are written, replacing an earlier drawing's files there")
    p_asm_draw.add_argument("--overwrite", action="store_true", help=argparse.SUPPRESS)  # accepted; the files are always replaced
    p_asm_draw.add_argument("--diagnostic", action="store_true",
                            help="draw a package whose local assembly check FAILED or could not be completed as a diagnostic "
                                 "sheet that is not for delivery (without it such a package is refused, exit 65)")
    p_asm_draw.add_argument("--locale", choices=("en", "zh-CN"), default="en",
                            help="the language of the sheet: en (default) or zh-CN (Simplified Chinese)")
    p_asm_vd = asm_sub.add_parser("verify-drawing", help="re-check a drawing output directory (exit 0 = verified, 66 = invalid)")
    p_asm_vd.add_argument("drawing_dir", help="a directory written by 'assembly draw'")
    _register_command_modules(sub, assembly=asm_sub, catalog=cat_sub, project=proj_sub)
    try:
        args = parser.parse_args(_negative_values_attached(argv))
    except SystemExit as exc:
        if exc.code == USAGE_ERROR and started is not None:
            started["work"] = True
        raise
    if started is not None:
        started["work"] = True

    handler = getattr(args, "handler", None)
    if handler is not None:  # a command registered by a command module
        # (workspace run: the contract is resolved here, where the environment is read; project evaluate resolves its own,
        # whose default follows the selection)
        if hasattr(args, "status_contract") and not getattr(args, "contract_by_selection", False):
            args.status_contract = _status_contract(args)
            if args.status_contract is None:
                return USAGE_ERROR
        return handler(args)
    if args.command == "catalog":
        return _catalog_command(args)
    if args.command == "assembly":
        if args.assembly_command in ("draw", "verify-drawing"):
            return _drawing_command(args)
        return _assembly_command(args)
    if args.command == "project":
        return _project_command(args)
    if args.command == "schema" and args.which not in ("request", "result"):
        print(json.dumps(_design_schema(args.which), indent=2, sort_keys=True))
        return 0

    if args.command == "schema":
        model = ValidationRequest if args.which == "request" else ValidationResult
        print(json.dumps(model.model_json_schema(), indent=2, sort_keys=True))
        return 0
    if args.command == "compare":
        try:
            diffs = compare_results(Path(args.left).read_text(encoding="utf-8"), Path(args.right).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return USAGE_ERROR
        for d in diffs:
            print(d)
        print("equivalent" if not diffs else f"{len(diffs)} difference(s)")
        return 0 if not diffs else 1
    contract = _status_contract(args)
    if contract is None:
        return USAGE_ERROR
    try:
        # bytes: a request that is not UTF-8 JSON is answered as a structured FAIL (INVALID_INPUT), never a traceback
        text = sys.stdin.buffer.read() if args.request == "-" else Path(args.request).read_bytes()
    except (OSError, ValueError) as exc:
        print(f"error: cannot read request: {exc}", file=sys.stderr)
        return USAGE_ERROR
    out = validate_json(text)
    doc = json.loads(out)
    rendered = json.dumps(doc, indent=2) if args.pretty else out
    if args.output:
        try:
            Path(args.output).write_text(rendered, encoding="utf-8")
        except OSError as exc:
            print(f"error: cannot write the result: {exc}", file=sys.stderr)
            return USAGE_ERROR
    else:
        print(rendered)
    outcome = doc.get("outcome")
    return exit_code(contract, status=doc["status"], outcome=None if outcome is None else ResultOutcome.model_validate(outcome))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
