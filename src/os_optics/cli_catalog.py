"""``os-optics catalog status|check|sync|verify|import|use|materials|material`` and ``os-optics project update-catalog``.

Material discovery (``os_optics.catalog_materials``; ``--catalog FILE`` before the subcommand browses that release):

* ``catalog materials [--manufacturer M] [--designation NAME [--loose]] [--search TEXT] [--covers-nm MIN MAX |
  --wavelength-nm NM] [--with-bulk-loss] [--availability CURRENT|LEGACY|NOT_STATED] [--at-nm NM] [--band-nm LO HI]
  [--thickness-mm MM] [--temperature-c C] [--offset N] [--limit N]``: ``{catalog_version, schema_version, total, offset,
  limit, filters, materials: [summary (+ ir)]}``; exit 0, 65 without a usable catalog;
* ``catalog material NAME [--manufacturer M] [--glass-catalogs 'CDGM SCHOTT'] [IR options]``: ``{catalog_version,
  resolution: {query, manufacturer, catalogue_order, resolved, how, candidates, also_matches}, material: summary, ir}``;
  an unknown or ambiguous name: ``candidates`` (summaries) and ``next_step``, exit 1.

A summary: ``material_id, display_name, status, identity {manufacturer, designation, aliases, catalogue_tags, variant_of,
variant, ordering_status {code, design_availability, as_of}, source_edition} | null, valid_band_nm (vacuum), dispersion
{kind, ...}, convention {wavelength, index, stated_by, reference_air, source_band_nm | evaluated_as}, bulk_loss {kind,
points, band_nm (vacuum), sample_thickness_mm} | null with bulk_loss_note, thermo_optic, crystal, basis``. ``ir``:
``{available, temperature_c, at: {wavelength_nm, n, n_group, gdd_mm2_per_mm, convention, reason, derivative_limitation,
transmission {thickness_mm, internal_transmittance | null, reason}}, band: {min_nm, mid_nm, max_nm, points,
dispersion_figure = (n_mid - 1) / (n_lo - n_hi), transmission [...]}}`` -- the Engine's own evaluation.

The public optical catalog lives in a local store synchronized with the OpenSci catalog service
(``os_optics.catalog``). Every command prints one JSON document:

* ``catalog status``: the active release, the stored releases and the last check against the service (no network:
  ``service.reachable`` is the outcome of the last recorded attempt, with its time); exit 0 when a catalog is
  available, 1 otherwise;
* ``catalog check``: ask the configured service which release is current (nothing downloaded); exit 0 when it answered,
  1 when it could not be reached (``up_to_date`` and ``service.current`` are then null; ``service.last_known`` keeps
  what the service said before);
* ``catalog sync [--release CATALOG_VERSION]``: download the service's current release when it is not on this device
  and make it active (``--release``: fetch that retained release, e.g. to reproduce a Project pinned to it; it becomes
  the active release only when none is active, ``notes`` says which is active); exit 0 with an active release, or 1
  with the error document;
* ``catalog verify [--deep]``: re-check every stored release's SHA-256 (``--deep``: and its content); exit 0, or 66;
* ``catalog import FILE [--no-activate]``: add a release from a file (an offline device) and make it active; exit 0,
  65 when the file is not a usable catalog release, 64 when it cannot be read;
* ``catalog use CATALOG_VERSION``: make a release stored on this device the active one (no network; e.g. to select
  another release than the newest stored one a run took when none was active); exit 0, or 1 with the error document;
* the public mechanical catalog (``opensci.public_mechanical_catalog``; mounts, posts, post holders and breadboards
  for local assembly plans) lives in the same store: ``catalog status`` / ``check`` / ``verify`` add a ``mechanical``
  block, ``catalog sync`` (without ``--release``) also fetches the service's current mechanical release when the
  service offers one (``mechanical.outcome`` ``DOWNLOADED`` / ``ALREADY_STORED`` / ``NOT_OFFERED`` / ``UNAVAILABLE``
  / ``FAILED``; a ``FAILED`` integrity or validation check exits 1, an ``UNAVAILABLE`` service never changes the
  optical exit code), ``catalog import FILE`` stores a mechanical document as well as an optical release (detected by
  its schema), and ``catalog mechanical list|show|status`` browse the mechanical documents in effect (``--mechanical
  FILE``, else ``OS_OPTICS_MECHANICAL_CATALOG``, else the active stored release; no token, no network);
* ``project update-catalog project.json [--out FILE] [--catalog FILE] [--include-changed]``: re-pin the Project's
  catalog components to the active release where their product is unchanged; exit 0 (``CURRENT`` / ``UPDATED`` /
  ``UPDATED_UNVERIFIED``: re-pinned, but some products changed or could not be compared, so run the Project again and
  review it; a component whose old release can never be compared is moved without the flag), 1 (``NEEDS_ATTENTION``:
  changed or withdrawn products, or unverified ones whose old release may still be obtained, keep their pin), 65 / 64 as above. The
  report's ``summary`` and ``next_steps`` say what to do next; without ``--out`` the Project file is not changed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

USAGE_ERROR = 64
LOCAL_INPUT_ERROR = 65
VERIFY_FAILED = 66
NOT_READY = 1
CATALOG_HELP = (
    "The public optical catalog on this device: a local store (OPENSCI_CATALOG_DIR, default ~/.opensci/catalog) "
    "synchronized with the OpenSci catalog service (OPENSCI_CATALOG_URL, else OPENSCI_API_URL, else the OpenSci service): "
    "once on first use, then at most every OPENSCI_CATALOG_CHECK_INTERVAL seconds (default 3600, 0 = never automatically). "
    "'list' / 'show' browse the active release, 'materials' / 'material' its materials; 'status', 'check', 'sync', 'verify', "
    "'import' and 'use' manage the store; 'mechanical list|show|status' browse the public mechanical catalog (mounts, posts, "
    "post holders, breadboards) that 'catalog sync' or 'catalog import' stored, or a --mechanical FILE. "
    "When a command downloads a release by itself, one line on stderr says so."
)
UPDATE_DESCRIPTION = (
    "Re-pin every catalog component whose product is published unchanged in the active (or --catalog) release. A "
    "component whose old release can never be compared (the catalog service no longer distributes it, NOT_DISTRIBUTED, "
    "or this Engine no longer reads its format, NOT_READABLE) is re-pinned too and listed in unverified with that reason. "
    "A changed product, and one whose old release is not on this device but may still be obtained (NOT_OBTAINED), keep "
    "their pin unless --include-changed re-pins them. Withdrawn products are listed and keep their pin. Statuses: "
    "CURRENT (every component already pins the release), UPDATED (re-pinned, every product unchanged), "
    "UPDATED_UNVERIFIED (re-pinned, with products that changed or could not be compared: the simulation may differ), "
    "NEEDS_ATTENTION (some components keep their old pin). Prints the report with summary and next_steps (the run and "
    "the submission that follow); --out writes the updated Project."
)


MECHANICAL_DESCRIPTION = (
    "The public mechanical catalog: the OS-MECH identities (mounts, posts, post holders, breadboards) a local assembly plan "
    "is derived from, with their engineering facts and the basis of each fact. The documents in effect are the --mechanical "
    "FILE documents (repeatable: at most one OPENSCI document and up to 8 USER documents with your own hardware, "
    "USER-MECH-*), else those OS_OPTICS_MECHANICAL_CATALOG names (paths separated by the system path separator), else the "
    "active release 'catalog sync' or 'catalog import' stored. No token and no network are used. Exit codes: 0, 64 = an "
    "unreadable file, 65 = no usable mechanical catalog (or an invalid document), 1 = 'show' of an unknown identity."
)
MECHANICAL_KINDS = ("POST", "POST_HOLDER", "MOUNT", "BREADBOARD")
#: The engineering facts a one-line listing shows for each kind (``catalog mechanical list``).
MECHANICAL_KEY_FACTS = {
    "POST": ("diameter_mm", "length_mm", "top_thread"),
    "POST_HOLDER": ("post_diameter_mm", "height_mm", "insertion_min_mm", "insertion_max_mm"),
    "MOUNT": ("mount_kind", "centre_height_mm", "post_thread"),
    "BREADBOARD": ("size_x_mm", "size_y_mm", "pitch_mm", "thread"),
}
#: How close a mount's held optic size must be to ``--holds-size`` (mm).
HOLDS_SIZE_MATCH_MM = 0.05


MATERIALS_DESCRIPTION = (
    "List the published materials of the active catalog release (or --catalog FILE) matching every filter given, each with its "
    "identity (manufacturer, designation, aliases, catalogue tags, variant base, ordering status, source edition), valid band "
    "(vacuum wavelengths), dispersion law, wavelength/index convention, measured bulk loss and thermo-optic data. --at-nm, "
    "--band-nm and --thickness-mm add the Engine's own evaluation (no visible nd/Vd stand-in): the index at a wavelength, the "
    "dispersion across a band ((n_mid - 1) / (n_lo - n_hi)) and the internal transmittance of a thickness from the measured "
    "bulk loss (null with the reason when no loss is stated: never 100 %)."
)
MATERIAL_DESCRIPTION = (
    "Resolve one name as prescription import does (the shared resolver: an explicit stable id; MAKER:DESIGNATION; a catalogue "
    "order; a unique designation or alias; a loose spelling, reported) and print the material in full, or the candidates of an "
    "ambiguous name (exit 1). --at-nm / --band-nm / --thickness-mm add the IR selection properties."
)
MATERIALS_EXAMPLES = (
    "examples: os-optics catalog materials --manufacturer CDGM --covers-nm 8000 12000 --with-bulk-loss --band-nm 8000 12000 "
    "--at-nm 10000 --thickness-mm 2 | os-optics catalog materials --designation F5 | os-optics catalog material CDGM:F5 | "
    "os-optics catalog material S-FPL53 --at-nm 587.6. Exit codes: 0, 1 = an unknown or ambiguous name (material), "
    "64 = usage error, 65 = no usable catalog."
)


def _add_ir_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--at-nm", type=float, metavar="NM", help="the index (and group index, GDD) at this vacuum wavelength in nm, e.g. 10000")
    parser.add_argument("--band-nm", nargs=2, type=float, metavar=("LO", "HI"),
                        help="the index at LO, the middle and HI, and the dispersion figure (n_mid - 1) / (n_lo - n_hi)")
    parser.add_argument("--thickness-mm", type=float, metavar="MM",
                        help="with --at-nm / --band-nm: the internal transmittance of this thickness from the measured bulk loss")
    parser.add_argument("--temperature-c", type=float, default=None, help="the material temperature (default 20 C)")


def _ir_arguments(args: argparse.Namespace) -> dict:
    return {"at_nm": args.at_nm, "band_nm": args.band_nm, "thickness_mm": args.thickness_mm, "temperature_c": args.temperature_c}


def _load_catalog(args: argparse.Namespace):
    """The catalog the material commands browse, or an exit code."""

    from .local import LocalRunError, load_public_catalog

    source = None
    if getattr(args, "catalog", None):
        try:
            source = Path(args.catalog).read_bytes()
        except OSError as exc:
            print(f"error: cannot read the catalog: {exc}", file=sys.stderr)
            return USAGE_ERROR
    try:
        return load_public_catalog(source)
    except LocalRunError as exc:
        _out(exc.as_dict())
        return LOCAL_INPUT_ERROR


def _materials(args: argparse.Namespace) -> int:
    from .catalog_materials import MaterialQueryError, list_materials

    catalog = _load_catalog(args)
    if isinstance(catalog, int):
        return catalog
    try:
        document = list_materials(catalog, manufacturer=args.manufacturer, designation=args.designation, loose=args.loose,
                                  search=args.search, covers_nm=args.covers_nm, wavelength_nm=args.wavelength_nm,
                                  with_bulk_loss=args.with_bulk_loss, availability=args.availability, offset=args.offset,
                                  limit=args.limit, **_ir_arguments(args))
    except MaterialQueryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return USAGE_ERROR
    _out(document)
    return 0


def _material(args: argparse.Namespace) -> int:
    from .catalog_materials import MaterialQueryError, lookup_material

    catalog = _load_catalog(args)
    if isinstance(catalog, int):
        return catalog
    try:
        document = lookup_material(catalog, args.name, manufacturer=args.manufacturer, glass_catalogs=args.glass_catalogs,
                                   **_ir_arguments(args))
    except MaterialQueryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return USAGE_ERROR
    _out(document)
    return 0 if document["resolution"]["resolved"] is not None else NOT_READY

def _out(document: object) -> None:
    try:
        print(json.dumps(document, indent=2, ensure_ascii=True))
    except UnicodeEncodeError:
        print(json.dumps(document, indent=2))


def register(catalog_commands: argparse._SubParsersAction) -> None:
    """Add the store commands to ``os-optics catalog``."""

    status = catalog_commands.add_parser("status", help="the active catalog release and the local store (no network)")
    status.set_defaults(handler=_status)
    check = catalog_commands.add_parser("check", help="ask the catalog service which release is current (no download)")
    check.set_defaults(handler=_check)
    sync = catalog_commands.add_parser("sync", help="download the service's current release if needed and make it active")
    sync.add_argument("--release", metavar="CATALOG_VERSION",
                      help="fetch this retained release instead; it becomes the active release only when none is active "
                           "('catalog use' selects it otherwise)")
    sync.set_defaults(handler=_sync)
    verify = catalog_commands.add_parser("verify", help="re-check the SHA-256 of every stored release (no network)")
    verify.add_argument("--deep", action="store_true", help="also re-validate each release's content")
    verify.set_defaults(handler=_verify)
    imported = catalog_commands.add_parser("import", help="add a catalog release from a file (offline devices) and make it active")
    imported.add_argument("file", help="a public optical catalog release JSON file")
    imported.add_argument("--no-activate", action="store_true", help="store it without making it the active release")
    imported.set_defaults(handler=_import)
    use = catalog_commands.add_parser("use", help="make a release stored on this device the active one (no network)")
    use.add_argument("catalog_version", metavar="CATALOG_VERSION", help="a stored release, e.g. 2026-10-04.1 ('catalog status' lists them)")
    use.set_defaults(handler=_use)
    materials = catalog_commands.add_parser(
        "materials", help="find published materials by maker, designation, wavelength coverage and IR properties",
        description=MATERIALS_DESCRIPTION, epilog=MATERIALS_EXAMPLES)
    materials.add_argument("--manufacturer", help="only this maker's materials (a maker such as CDGM, or a catalogue tag)")
    materials.add_argument("--designation", help="only materials answering to this name exactly (id, designation or a documented alias)")
    materials.add_argument("--loose", action="store_true", help="with --designation: ignore case, spaces, '-', '_' and '.'")
    materials.add_argument("--search", help="only materials one of whose names contains this text (loose spelling)")
    materials.add_argument("--covers-nm", nargs=2, metavar=("MIN", "MAX"),
                           help="only materials whose valid band (vacuum wavelengths, nm) covers MIN-MAX, e.g. 8000 12000")
    materials.add_argument("--wavelength-nm", help="only materials whose valid band covers this vacuum wavelength (nm)")
    materials.add_argument("--with-bulk-loss", action="store_true",
                           help="only materials whose measured bulk loss covers the --covers-nm / --wavelength-nm band (or any, without one)")
    materials.add_argument("--availability", choices=("CURRENT", "LEGACY", "NOT_STATED"), help="the maker's design availability")
    _add_ir_arguments(materials)
    materials.add_argument("--offset", type=int, default=0, help="skip this many matching materials (paging)")
    materials.add_argument("--limit", type=int, default=None, help="list at most this many materials (default: all)")
    materials.set_defaults(handler=_materials)
    material = catalog_commands.add_parser(
        "material", help="resolve one material name as prescription import does and show it in full (optionally its IR properties)",
        description=MATERIAL_DESCRIPTION, epilog=MATERIALS_EXAMPLES)
    material.add_argument("name", help="a stable id (SCHOTT_F5), a designation (S-FPL53) or alias, or MAKER:DESIGNATION (CDGM:F5)")
    material.add_argument("--manufacturer", help="the maker (or catalogue tag) of the material (the same as MAKER:)")
    material.add_argument("--glass-catalogs", help="a catalogue order deciding a name several makers use, e.g. 'CDGM SCHOTT'")
    _add_ir_arguments(material)
    material.set_defaults(handler=_material)
    mechanical = catalog_commands.add_parser("mechanical", help="browse the public mechanical catalog (mounts, posts, holders, "
                                             "breadboards) for local assembly plans", description=MECHANICAL_DESCRIPTION)
    mechanical_sub = mechanical.add_subparsers(dest="mechanical_command", required=True)
    listing = mechanical_sub.add_parser("list", help="one entry per identity: sku, kind, name, key facts, profile",
                                        description=MECHANICAL_DESCRIPTION)
    listing.add_argument("--kind", choices=MECHANICAL_KINDS, help="only this kind")
    listing.add_argument("--holds-size", type=float, metavar="MM", help="only mounts that hold an optic of this size (mm)")
    listing.add_argument("--json", action="store_true", help="print the identity documents in full")
    shown = mechanical_sub.add_parser("show", help="one identity document", description=MECHANICAL_DESCRIPTION)
    shown.add_argument("sku", metavar="SKU", help="an OS-MECH-* (or USER-MECH-*) identity")
    status_mechanical = mechanical_sub.add_parser("status", help="the mechanical documents in effect and the stored releases",
                                                  description=MECHANICAL_DESCRIPTION)
    for parser, handler in ((listing, _mechanical_list), (shown, _mechanical_show), (status_mechanical, _mechanical_status)):
        parser.add_argument("--mechanical", action="append", default=[], metavar="FILE",
                            help="a public mechanical catalog document to use (repeatable)")
        parser.set_defaults(handler=handler)


def register_project(project_commands: argparse._SubParsersAction) -> None:
    """Add ``os-optics project update-catalog``."""

    update = project_commands.add_parser(
        "update-catalog", help="re-pin the Project's catalog components to the active catalog release where their product is unchanged",
        description=UPDATE_DESCRIPTION,
        epilog="exit codes: 0 = CURRENT / UPDATED / UPDATED_UNVERIFIED, 1 = NEEDS_ATTENTION, 64 = unreadable or unwritable "
               "file, 65 = invalid Project or no catalog")
    update.add_argument("project", help="path to a Project JSON file")
    update.add_argument("--out", help="write the updated Project here (may be the same file)")
    update.add_argument("--catalog", help="re-pin to this catalog release file instead of the active release")
    update.add_argument("--include-changed", action="store_true",
                        help="also re-pin components whose product changed, and those whose old release may still be "
                             "obtained (status UPDATED_UNVERIFIED)")
    update.set_defaults(handler=_update_project)


def _failure(exc) -> int:
    _out({"status": "ERROR", "error": exc.as_dict()})
    return NOT_READY


def _status(args: argparse.Namespace) -> int:
    from .catalog import CatalogError, status
    from .catalog.service import mechanical_status

    try:
        report = status()
    except CatalogError as exc:
        return _failure(exc)
    document = {**report.as_dict(), "mechanical": mechanical_status()}
    _out(document)
    # (an active release in an earlier schema is not usable; releases stored with none active are: the next use takes the newest)
    return 0 if document["available"] else NOT_READY


def _check(args: argparse.Namespace) -> int:
    from .catalog import CatalogError, check

    from .catalog.service import check_mechanical

    try:
        report = check()
    except CatalogError as exc:
        return _failure(exc)
    _out({**report.as_dict(), "mechanical": check_mechanical()})
    return 0 if report.reachable else NOT_READY


def _sync(args: argparse.Namespace) -> int:
    from .catalog import CatalogError, sync

    from .catalog.service import MECHANICAL_FAILED, sync_mechanical

    try:
        report = sync(args.release)
    except CatalogError as exc:
        return _failure(exc)
    document = report.as_dict()
    if args.release is None:  # (a retained optical release fetched for a pinned Project leaves the mechanical catalog alone)
        document["mechanical"] = sync_mechanical()
    _out(document)
    if not report.state.usable_active:
        return NOT_READY  # never a success that leaves no usable catalog
    # a mechanical download that failed its integrity or validation check is not a success; an unavailable service is
    return NOT_READY if document.get("mechanical", {}).get("outcome") == MECHANICAL_FAILED else 0


def _verify(args: argparse.Namespace) -> int:
    from .catalog import CatalogError, verify

    from .catalog.service import verify_mechanical

    try:
        report = verify(deep=args.deep)
    except CatalogError as exc:
        return _failure(exc)
    mechanical = verify_mechanical()
    report = {**report, "ok": report["ok"] and mechanical["ok"], "mechanical": mechanical}
    _out(report)
    return 0 if report["ok"] else VERIFY_FAILED


def _import(args: argparse.Namespace) -> int:
    from .catalog import CatalogError, import_release

    from .catalog.service import import_mechanical, is_mechanical_document

    try:
        data = Path(args.file).read_bytes()
    except OSError as exc:
        print(f"error: cannot read the catalog file: {exc}", file=sys.stderr)
        return USAGE_ERROR
    if is_mechanical_document(data):
        return _import_mechanical(data, activate=not args.no_activate, importer=import_mechanical)
    try:
        report = import_release(data, activate=not args.no_activate)
    except CatalogError as exc:
        _out({"status": "ERROR", "error": exc.as_dict()})
        return LOCAL_INPUT_ERROR
    _out(report.as_dict())
    return 0


def _import_mechanical(data: bytes, *, activate: bool, importer) -> int:
    from .assembly_plan.mechanical import MechanicalCatalogError
    from .catalog import CatalogError

    try:
        imported = importer(data, activate=activate)
    except (CatalogError, MechanicalCatalogError) as exc:
        error = exc.as_dict()
        if isinstance(exc, MechanicalCatalogError):
            error["hint"] = ("'catalog import' stores OPENSCI mechanical releases; a USER document (your own hardware, "
                             "USER-MECH-*) is used with --mechanical FILE or OS_OPTICS_MECHANICAL_CATALOG")
        _out({"status": "ERROR", "error": error})
        return LOCAL_INPUT_ERROR
    _out({"status": "IMPORTED", "mechanical": imported})
    return 0


def _mechanical_sources(args: argparse.Namespace):
    """The mechanical documents in effect (``--mechanical``, else the environment, else the store), or an exit code."""

    from .assembly_plan.mechanical import MechanicalCatalogError
    from .assembly_plan.mechanical.model import MechanicalErrorCode
    from .catalog.mechanical import resolve_sources

    try:
        return resolve_sources(args.mechanical).require()
    except MechanicalCatalogError as exc:
        _out({"status": "ERROR", "error": exc.as_dict()})
        return USAGE_ERROR if exc.code is MechanicalErrorCode.MECHANICAL_FILE_UNREADABLE else LOCAL_INPUT_ERROR


def _documents_in_effect(sources) -> list[dict]:
    return [{"publisher": d.publisher, "mechanical_version": d.document.mechanical_version, "sha256": d.sha256,
             "source": d.source.value, "location": None if d.location is None else str(d.location),
             "identities": len(d.document.identities)} for d in sources.documents]


def _key_facts(identity) -> dict:
    payload = identity.payload()
    facts = {name: payload.get(name) for name in MECHANICAL_KEY_FACTS.get(payload["kind"], ())}
    if payload["kind"] == "MOUNT":
        facts["holds"] = f"{payload['holds']['shape']} {payload['holds']['size_mm']:g} mm"
        facts["mechanisms"] = [m["kind"] for m in payload.get("mechanisms", ())]
    return facts


def _mechanical_list(args: argparse.Namespace) -> int:
    sources = _mechanical_sources(args)
    if isinstance(sources, int):
        return sources
    rows = []
    for identity in sources.identities():
        payload = identity.payload()
        if args.kind and payload["kind"] != args.kind:
            continue
        if args.holds_size is not None and (payload["kind"] != "MOUNT"
                                            or abs(payload["holds"]["size_mm"] - args.holds_size) > HOLDS_SIZE_MATCH_MM):
            continue
        rows.append(payload if args.json else {
            "opensci_sku": payload["opensci_sku"], "kind": payload["kind"], "display_name": payload["display_name"],
            "publisher": sources.publisher_of(payload["opensci_sku"]), "facts": _key_facts(identity),
            "profile": payload["profile"]["kind"]})
    _out({"documents": _documents_in_effect(sources), "filters": {"kind": args.kind, "holds_size_mm": args.holds_size},
          "total": len(rows), "identities": rows})
    return 0


def _mechanical_show(args: argparse.Namespace) -> int:
    sources = _mechanical_sources(args)
    if isinstance(sources, int):
        return sources
    identity = sources.identity(args.sku)
    if identity is None:
        _out({"status": "NOT_FOUND", "opensci_sku": args.sku, "documents": _documents_in_effect(sources),
              "next_step": "'os-optics catalog mechanical list' lists the identities in effect"})
        return NOT_READY
    _out({"publisher": sources.publisher_of(args.sku), "identity": identity.payload()})
    return 0


def _mechanical_status(args: argparse.Namespace) -> int:
    from .assembly_plan.mechanical import MechanicalCatalogError
    from .assembly_plan.mechanical.model import MechanicalErrorCode
    from .catalog.mechanical import environment_files, resolve_sources
    from .catalog.service import mechanical_status

    document = {"store": mechanical_status(), "environment_files": list(environment_files())}
    try:
        sources = resolve_sources(args.mechanical)
    except MechanicalCatalogError as exc:
        _out({**document, "status": "ERROR", "error": exc.as_dict()})
        return USAGE_ERROR if exc.code is MechanicalErrorCode.MECHANICAL_FILE_UNREADABLE else LOCAL_INPUT_ERROR
    document.update(status="AVAILABLE" if not sources.empty else "NOT_AVAILABLE", documents=_documents_in_effect(sources),
                    users=[d.document.mechanical_version for d in sources.users])
    if sources.empty:
        document["next_steps"] = ["os-optics catalog sync", "os-optics catalog import FILE", "--mechanical FILE"]
    _out(document)
    return 0 if not sources.empty else NOT_READY


def _use(args: argparse.Namespace) -> int:
    from .catalog import CatalogError, use_release

    try:
        report = use_release(args.catalog_version)
    except CatalogError as exc:
        return _failure(exc)
    _out(report.as_dict())
    return 0


def _same_file(a: str, b: str) -> bool:
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return False


def _update_project(args: argparse.Namespace) -> int:
    from .local import LocalRunError, update_project_catalog

    try:
        project = Path(args.project).read_bytes()
        catalog = Path(args.catalog).read_bytes() if args.catalog else None
    except OSError as exc:
        print(f"error: cannot read the input: {exc}", file=sys.stderr)
        return USAGE_ERROR
    try:
        update = update_project_catalog(project, catalog, include_changed=args.include_changed)
    except LocalRunError as exc:
        _out(exc.as_dict())
        return LOCAL_INPUT_ERROR
    # nothing re-pinned and --out names the input itself: the Project is unchanged, so it is not rewritten
    unchanged_in_place = bool(args.out) and not update.repinned and _same_file(args.out, args.project)
    if args.out and not unchanged_in_place:
        try:
            Path(args.out).write_text(json.dumps(update.project.payload(), indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        except OSError as exc:
            print(f"error: cannot write the Project: {exc}", file=sys.stderr)
            return USAGE_ERROR
    document = {**update.as_dict(), "out": args.out, "project_written": bool(args.out) and not unchanged_in_place}
    if not args.out and update.status in ("UPDATED", "UPDATED_UNVERIFIED"):
        document["next_steps"] = [{"action": "WRITE_PROJECT",
                                   "message": "nothing was written: run the command again with --out to save the updated "
                                              "Project (may be the same file)",
                                   "command": f"os-optics project update-catalog {args.project} --out {args.project}"
                                              + (" --include-changed" if args.include_changed else "")},
                                  *document["next_steps"]]
    _out(document)
    return NOT_READY if update.status == "NEEDS_ATTENTION" else 0
