"""``os-optics assembly plan ...``: a local assembly plan of a Project, on this device, without a token or a network
connection (DESIGN 3.1). ``derive`` writes a plan directory, ``show`` / ``history`` read it, ``check`` re-checks a
revision, ``edit`` applies plan ops as one new revision, ``preview`` writes the LOCAL_PLAN Assembly Preview Package,
``draw`` the assembly drawing of your design, ``bom`` the design BOM or the purchase BOM (the body ``os-optics order
quote`` sends), ``run-optics`` runs the plan's Project and binds the run.

Every command prints one JSON document. Exit codes: 0 = done, plan READY or READY_WITH_WARNINGS; 1 = done, plan
HAS_ERRORS (the revision / output is written); 2 = done, plan INCOMPLETE (written; ``draw``: the diagnostic sheet);
64 = usage error, an unreadable or unwritable file; 65 = input refused (an invalid Project or op, no mechanical
catalog, a drawing refused), nothing written; 66 = the plan directory is inconsistent; 70 = internal error; 130 =
interrupted.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

USAGE_ERROR = 64
INPUT_REFUSED = 65
PLAN_DIR_INCONSISTENT = 66
STATE_EXIT = {"READY": 0, "READY_WITH_WARNINGS": 0, "HAS_ERRORS": 1, "INCOMPLETE": 2}
DEFAULT_TIME_LIMIT_S = 120.0
DEFAULT_TABLE_PITCH_MM = 25.0
DEFAULT_TABLE_EDGE_MM = 12.5
DEFAULT_TABLE_THREAD = "M6"
#: Mechanical codes of a file that cannot be read (a usage error, not a refused input).
UNREADABLE_CODES = ("MECHANICAL_FILE_UNREADABLE",)
SUMMARY_SEVERITIES = ("ERROR", "UNKNOWN", "WARNING")
#: A thread the plan's interface designations can carry (``InterfaceDesignation``).
DESIGNATION = re.compile(r"^[A-Za-z0-9.\-x]+$")

PLAN_DESCRIPTION = (
    "A local assembly plan of a Project: which mount, post, holder and board hole hold each optic, who supplies each "
    "part and what you buy. Everything runs on this device from the public optical catalog and the public mechanical "
    "catalog ('os-optics catalog sync', or --mechanical FILE / OS_OPTICS_MECHANICAL_CATALOG for your own documents); no "
    "token, no network. A plan directory holds every revision (revisions/r0001.json ...), the Projects they bind, the "
    "journal (history.jsonl) and plan.json, a copy of the head. Typical flow: derive -> show -> edit (move an optic, swap "
    "hardware, set supply and purchase) -> check -> preview / draw -> bom --purchase -> 'os-optics order quote BOM.json'."
)
PLAN_EPILOG = ("exit codes: 0 = done, plan READY or READY_WITH_WARNINGS; 1 = plan HAS_ERRORS (written); 2 = plan INCOMPLETE "
               "(written; draw: diagnostic sheet); 64 = usage / unreadable or unwritable file; 65 = refused, nothing written; "
               "66 = plan directory inconsistent; 70 = internal error; 130 = interrupted")


def _out(doc: Any) -> None:
    from .cli import _json_out

    _json_out(doc)


def _note(text: str) -> None:
    sys.stderr.write(text.rstrip("\n") + "\n")


def _error(exc: Any) -> int:
    from .assembly_plan.contract import AssemblyPlanErrorCode

    error = exc.as_dict()
    _out({"status": "ERROR", "error": error})
    if exc.code is AssemblyPlanErrorCode.PLAN_DIR_INCONSISTENT:
        return PLAN_DIR_INCONSISTENT
    if (exc.details or {}).get("mechanical_code") in UNREADABLE_CODES:
        return USAGE_ERROR
    return INPUT_REFUSED


def _state_exit(revision: Any) -> int:
    return STATE_EXIT[revision.status.state.value]


def _findings_summary(findings: Any) -> list[dict[str, Any]]:
    return [{"finding_id": f.finding_id, "code": f.code.value, "severity": f.severity.value, "item_ids": list(f.item_ids),
             **({"interface": f.interface.value} if f.interface is not None else {}),
             **({"rule": f.details["rule"]} if "rule" in f.details else {})}
            for f in findings if f.severity.value in SUMMARY_SEVERITIES]


def _summary(revision: Any, plan_dir: Any, *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    board = revision.item(revision.board_item_id)
    return {"plan_dir": None if plan_dir is None else str(plan_dir), "plan_id": revision.plan_id, "revision": revision.revision,
            "origin": revision.origin.value, "content_digest": revision.content_digest, "status": revision.status.state.value,
            "optics_results": revision.status.optics_results.value,
            "layout_validation": None if revision.status.layout_validation is None else revision.status.layout_validation.status.value,
            "counts": revision.status.counts.model_dump(mode="json"), "findings": _findings_summary(revision.findings),
            "items": len(revision.items), "board": board.identity.opensci_sku if board is not None else None, **(extra or {})}


def _next(plan_dir: Any) -> list[str]:
    return [f"os-optics assembly plan show {plan_dir} --items", f"os-optics assembly plan check {plan_dir}",
            f"os-optics view {plan_dir}", f"os-optics assembly plan draw {plan_dir} --out DRAWING_DIR",
            f"os-optics assembly plan bom {plan_dir} --purchase --out BOM.json"]


def _guard(work):
    def run(args: argparse.Namespace) -> int:
        from .assembly_plan.contract import AssemblyPlanError
        from .assembly_plan.mechanical import MechanicalCatalogError

        try:
            return work(args)
        except AssemblyPlanError as exc:
            return _error(exc)
        except MechanicalCatalogError as exc:
            _out({"status": "ERROR", "error": exc.as_dict()})
            return USAGE_ERROR if exc.code.value in UNREADABLE_CODES else INPUT_REFUSED
        except OSError as exc:
            _note(f"error: {exc}")
            return USAGE_ERROR

    return run


def _catalog(args: argparse.Namespace) -> Any:
    from .cli import _read

    if not getattr(args, "catalog", None):
        return None
    data = _read(args.catalog, "catalog")
    if data is None:
        raise OSError(f"cannot read the catalog file {args.catalog}")
    return data


def _open(args: argparse.Namespace) -> Any:
    from .assembly_plan import open_plan

    return open_plan(args.plan_dir, catalog=_catalog(args))


# ------------------------------------------------------------------------------------------------ commands


def _derive(args: argparse.Namespace) -> int:
    from .assembly_plan import derive_plan
    from .cli import _read

    data = _read(args.project, "project")
    if data is None:
        return USAGE_ERROR
    options: dict[str, Any] = {"time_limit_s": args.time_limit if args.time_limit > 0 else None}
    if args.max_board_size_mm:
        options["max_board_size_mm"] = list(args.max_board_size_mm)
    if args.beam_height_mm is not None:
        options["beam_height_mm"] = args.beam_height_mm
    if args.customer_table:
        if DESIGNATION.fullmatch(args.table_thread) is None:
            _note(f"error: --table-thread {args.table_thread!r}: write the thread with letters, digits, '.', '-' or 'x' "
                  "(e.g. M6, or 1-4-20 for 1/4-20)")
            return USAGE_ERROR
        options["customer_table"] = {"size_x_mm": args.customer_table[0], "size_y_mm": args.customer_table[1],
                                     "pitch_mm": args.table_pitch_mm, "edge_offset_mm": args.table_edge_offset_mm,
                                     "thread": args.table_thread}
    if args.customer_owned:
        options["customer_owned"] = sorted(set(args.customer_owned))
    out = args.out if args.out else (args.from_plan if args.from_plan else None)
    if out is None:
        _note("error: give --out PLAN_DIR (or --from PLAN_DIR to derive that plan again)")
        return USAGE_ERROR
    result = derive_plan(data, out_dir=out, run=args.run, from_plan=args.from_plan, mechanical=args.mechanical or None,
                         catalog=_catalog(args), options=options, plan_id=args.plan_id)
    revision = result.revision
    _out(revision.payload() if args.json else _summary(revision, result.plan_dir, extra={"next": _next(result.plan_dir)}))
    return _state_exit(revision)


def _items(revision: Any) -> list[dict[str, Any]]:
    from .assembly_plan.interfaces import chain_items

    rows = []
    for item in revision.items:
        row = {"item_id": item.item_id, "role": item.role.value, "identity": item.identity.opensci_sku or item.identity.kind.value,
               "display_name": item.identity.display_name, "supply": item.supply.value,
               "purchase": item.purchase.model_dump(mode="json"), "pinned": item.pinned}
        if item.design_instance_id is not None:
            row["design_instance_id"] = item.design_instance_id
        if item.mounting is not None:
            row["mounting"] = item.mounting.value
            chain = chain_items(revision, item.item_id)
            if chain:
                row["chain"] = [chain[r].item_id for r in sorted(chain, key=lambda r: ("MOUNT", "POST", "POST_HOLDER").index(r.value))]
            if item.placement is not None and item.placement.ranges:
                row["ranges"] = [{"range_id": r.range_id, "min": r.min, "max": r.max, "unit": r.unit.value} for r in item.placement.ranges]
        if item.placement is not None and item.placement.fixation is not None:
            row["hole"] = list(item.placement.fixation.board_holes[0])
        rows.append(row)
    return rows


def _show(args: argparse.Namespace) -> int:
    plan = _open(args)
    revision = plan.revision(args.revision)
    if args.json:
        _out(revision.payload())
    else:
        doc = _summary(revision, plan.path, extra={"head": plan.head.revision})
        if args.items:
            doc["items"] = _items(revision)
        _out(doc)
    return _state_exit(revision)


def _check(args: argparse.Namespace) -> int:
    from .assembly import tolerances_with

    plan = _open(args)
    try:
        tolerances = tolerances_with(dict(args.tolerance or ())) if args.tolerance else None
    except ValueError as exc:
        _note(f"error: --tolerance: {exc}")
        return USAGE_ERROR
    target = plan.revision(args.revision)
    outcome = plan.check(target.revision, mechanical=args.mechanical or None, run=args.run, tolerances=tolerances)
    revision = outcome.revision(target)
    document = {"plan": {"plan_dir": str(plan.path), "plan_id": revision.plan_id, "revision": revision.revision,
                         "content_digest": revision.content_digest},
                "status": revision.status.model_dump(mode="json", exclude_none=True),
                "findings": [f.model_dump(mode="json", exclude_none=True) for f in revision.findings],
                "layout_validation": None if outcome.validation is None else outcome.validation.payload(),
                "optics_freshness": outcome.freshness}
    if args.out:
        Path(args.out).write_text(json.dumps(document, indent=2, sort_keys=True, ensure_ascii=True) + "\n", encoding="utf-8")
    _out(document if args.json else _summary(revision, plan.path, extra={"recorded_status": target.status.state.value,
                                                                        "out": args.out}))
    return _state_exit(revision)


def _ops_of(args: argparse.Namespace) -> list[Any] | None:
    ops: list[Any] = []
    if args.ops:
        try:
            document = json.loads(Path(args.ops).read_bytes())
        except (OSError, ValueError) as exc:
            _note(f"error: cannot read --ops {args.ops}: {exc}")
            return None
        ops += document["ops"] if isinstance(document, dict) and "ops" in document else (document if isinstance(document, list) else [document])
    for text in args.op or ():
        try:
            ops.append(json.loads(text))
        except ValueError as exc:
            _note(f"error: --op is one JSON object: {exc}")
            return None
    if not ops:
        _note("error: give --ops OPS.json or --op JSON")
        return None
    return ops


def _edit(args: argparse.Namespace) -> int:
    ops = _ops_of(args)
    if ops is None:
        return USAGE_ERROR
    plan = _open(args)
    result = plan.edit(ops, op_id=args.op_id, expected_base=args.expect_base, require_check=args.require_check,
                       mechanical=args.mechanical or None)
    written = None
    if args.write_project and result.result.project_changed:
        Path(args.write_project).write_text(json.dumps(result.project.payload(), indent=2, sort_keys=True, ensure_ascii=True) + "\n",
                                            encoding="utf-8")
        written = args.write_project
    call = result.result.model_dump(mode="json", exclude_none=True)
    call["project_written"] = written
    if not args.json:
        call["findings"] = _findings_summary(result.revision.findings)
        call["status"] = result.revision.status.state.value
    _out(call)
    return _state_exit(result.revision)


def _history(args: argparse.Namespace) -> int:
    plan = _open(args)
    lines = plan.history(args.op_id, args.limit)
    _out({"plan_dir": str(plan.path), "head": plan.head.revision,
          "lines": [line.model_dump(mode="json", exclude_none=True) for line in lines]})
    return 0


def _preview(args: argparse.Namespace) -> int:
    from .assembly_plan import plan_preview

    plan = _open(args)
    revision = plan.revision(args.revision)
    outcome = plan.check(revision.revision, mechanical=args.mechanical or None)
    package = plan_preview(plan, revision=revision.revision, out=args.out, as_directory=args.dir, mechanical=args.mechanical or None)
    checked = outcome.revision(revision)
    _out({**_summary(checked, plan.path), "out": args.out, "package_content_hash": package.content_hash,
          "manifest_schema_version": package.manifest.schema_version, "parts": len(package.scene.parts)})
    return _state_exit(checked)


def _draw(args: argparse.Namespace) -> int:
    from .assembly_drawing import AssemblyDrawingError
    from .assembly_plan import plan_drawing

    plan = _open(args)
    try:
        outcome = plan_drawing(plan, args.out, locale=args.locale, diagnostic=args.diagnostic, revision=args.revision,
                               mechanical=args.mechanical or None)
    except AssemblyDrawingError as exc:
        _out({"status": "ERROR", "error": exc.as_dict()})
        return INPUT_REFUSED
    _out({**outcome.summary(), "out": args.out})
    return outcome.exit_code


def _bom(args: argparse.Namespace) -> int:
    from .assembly_plan import design_bom, purchase_bom

    plan = _open(args)
    if args.design:
        document, report = design_bom(plan, revision=args.revision), None
    else:
        document, report = purchase_bom(plan, revision=args.revision, schema_version=args.schema_version)
    text = json.dumps(document, indent=2, sort_keys=False, ensure_ascii=True) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    if args.report and report is not None:
        Path(args.report).write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    if args.out:
        _out({"out": args.out, "kind": "design" if args.design else "purchase",
              **({"report": report} if report is not None else {"lines": len(document["lines"]),
                                                                  "bom_digest": document["bom_digest"]}),
              **({"next_command": f"os-optics order quote {args.out}"} if not args.design else {})})
    else:
        _out(document if report is None else {"bom": document, "report": report})
    return 0


def _run_optics(args: argparse.Namespace) -> int:
    from .local import LocalRunError, run_optical_project
    from .run_control import RunStopped

    plan = _open(args)
    head = plan.head
    if args.revision is not None and args.revision != head.revision:
        _note("error: run-optics binds the run to the head revision; give no --revision or the head's number")
        return USAGE_ERROR
    project = plan.project(head)
    context = plan.context(head, mechanical=args.mechanical or None)
    try:
        run = run_optical_project(project, catalog=context.catalog, output_dir=args.out)
    except LocalRunError as exc:
        _out(exc.as_dict())
        return INPUT_REFUSED
    except RunStopped:
        _note("the optics run stopped; the plan is unchanged")
        return INPUT_REFUSED
    result = plan.bind_run(run.local_validation)
    revision = result.revision
    _out(_summary(revision, plan.path, extra={"run_dir": args.out, "run_status": run.status.value,
                                              "local_validation_hash": run.local_validation.record_hash()}))
    return _state_exit(revision)


# ------------------------------------------------------------------------------------------------ registration


def _common(parser: argparse.ArgumentParser, *, revision: bool = True) -> None:
    parser.add_argument("--mechanical", action="append", metavar="FILE",
                        help="a public mechanical catalog document to use (repeatable: one OPENSCI document and your USER "
                             "documents); default: OS_OPTICS_MECHANICAL_CATALOG, the plan's own copies, the local catalog store")
    parser.add_argument("--catalog", help="use this public optical catalog JSON instead of the release the plan binds")
    if revision:
        parser.add_argument("--revision", type=int, metavar="N", help="the revision to use (default: the head)")


def register(assembly: Any) -> None:
    """Add ``assembly plan ...`` to the ``assembly`` subcommands."""

    from .cli import add_tolerance_argument

    p_plan = assembly.add_parser("plan", help="a local assembly plan: derive, show, check, edit, preview, draw, bom, run-optics "
                                              "(no token, no network)", description=PLAN_DESCRIPTION, epilog=PLAN_EPILOG)
    sub = p_plan.add_subparsers(dest="plan_command", required=True)

    def add(name: str, help_text: str, work) -> argparse.ArgumentParser:
        parser = sub.add_parser(name, help=help_text, description=help_text, epilog=PLAN_EPILOG)
        parser.set_defaults(handler=_guard(work))
        return parser

    derive = add("derive", "derive a plan of a Project into a new plan directory (or again into --from PLAN_DIR)", _derive)
    derive.add_argument("project", help="the Project JSON file")
    derive.add_argument("--out", help="the plan directory to write (absent or empty)")
    derive.add_argument("--from", dest="from_plan", metavar="PLAN_DIR",
                        help="derive again from this plan: its item ids, supply, purchase and pinned hardware are kept "
                             "(written into PLAN_DIR itself unless --out names a new directory)")
    derive.add_argument("--run", metavar="RUN_DIR", help="bind this optics run (a 'project run --out-dir' directory)")
    derive.add_argument("--customer-owned", action="append", metavar="INSTANCE_ID", help="a design instance you supply (repeatable)")
    derive.add_argument("--max-board-size-mm", nargs=2, type=float, metavar=("X", "Y"), help="the largest board to use")
    derive.add_argument("--beam-height-mm", type=float, help="the lab height of design z = 0 above the board")
    derive.add_argument("--customer-table", nargs=2, type=float, metavar=("X", "Y"),
                        help="place the plan on your own optical table of this size instead of an OpenSci breadboard")
    derive.add_argument("--table-pitch-mm", type=float, default=DEFAULT_TABLE_PITCH_MM)
    derive.add_argument("--table-edge-offset-mm", type=float, default=DEFAULT_TABLE_EDGE_MM)
    derive.add_argument("--table-thread", default=DEFAULT_TABLE_THREAD)
    derive.add_argument("--time-limit", type=float, default=DEFAULT_TIME_LIMIT_S, metavar="S",
                        help="stop the hardware search after S seconds with the best candidate (SEARCH_BUDGET_EXHAUSTED); "
                             "0 = only the work budget (default 120)")
    derive.add_argument("--plan-id", help="the plan id (default: derived from the inputs)")
    derive.add_argument("--json", action="store_true", help="print the whole revision document")
    _common(derive, revision=False)

    show = add("show", "verify the plan directory and print a revision (default the head)", _show)
    show.add_argument("plan_dir")
    show.add_argument("--items", action="store_true", help="list every item with its supply, purchase, chain and ranges")
    show.add_argument("--json", action="store_true", help="print the whole revision document")
    _common(show)

    check = add("check", "re-check a revision with this Engine and the catalogs supplied now (nothing is written)", _check)
    check.add_argument("plan_dir")
    check.add_argument("--run", metavar="RUN_DIR", help="report the optics freshness against this run")
    check.add_argument("--out", help="write the check result (plan, status, findings, layout validation) to this file")
    check.add_argument("--json", action="store_true", help="print the whole check result")
    add_tolerance_argument(check)
    _common(check)

    edit = add("edit", "apply plan ops as one new revision (atomic; journaled with its op id)", _edit)
    edit.add_argument("plan_dir")
    edit.add_argument("--ops", metavar="OPS.json", help="a JSON list of ops (or {\"ops\": [...]})")
    edit.add_argument("--op", action="append", metavar="JSON", help="one op as JSON (repeatable), e.g. "
                      "'{\"op\": \"set_hardware\", \"item_id\": \"mnt-2\", \"opensci_sku\": \"OS-MECH-...\"}'")
    edit.add_argument("--op-id", help="the id of this edit: the same id with the same ops is answered from the journal")
    edit.add_argument("--expect-base", metavar="DIGEST", help="refuse the edit unless the head's content_digest is DIGEST")
    edit.add_argument("--require-check", action="store_true", help="refuse an edit that adds an ERROR or UNKNOWN finding")
    edit.add_argument("--write-project", metavar="FILE", help="write the edited Project here when the edit moved an optic")
    edit.add_argument("--json", action="store_true", help="print every finding in full")
    _common(edit, revision=False)

    history = add("history", "print the plan's journal (oldest first)", _history)
    history.add_argument("plan_dir")
    history.add_argument("--op-id", help="only the lines of this op id")
    history.add_argument("--limit", type=int, help="the last N lines")
    _common(history, revision=False)

    preview = add("preview", "write the LOCAL_PLAN Assembly Preview Package of a revision", _preview)
    preview.add_argument("plan_dir")
    preview.add_argument("--out", required=True, help="the package .zip (or, with --dir, a directory)")
    preview.add_argument("--dir", action="store_true", help="write the package files into a directory instead of a .zip")
    _common(preview)

    draw = add("draw", "draw the assembly drawing of your design (SVG, PDF, result, manifest; never uploaded)", _draw)
    draw.add_argument("plan_dir")
    draw.add_argument("--out", required=True, help="the output directory (the four drawing files, preview.zip, assembly-detail.json)")
    draw.add_argument("--locale", choices=("en", "zh-CN"), default="en")
    draw.add_argument("--diagnostic", action="store_true",
                      help="draw a plan whose layout check did not pass as a diagnostic sheet (exit 2)")
    _common(draw)

    bom = add("bom", "write the design BOM or the purchase BOM ('os-optics order quote' sends the purchase BOM as it is)", _bom)
    bom.add_argument("plan_dir")
    kind = bom.add_mutually_exclusive_group(required=True)
    kind.add_argument("--design", action="store_true", help="every item of the plan, grouped into lines D1..Dn")
    kind.add_argument("--purchase", action="store_true", help="what you buy: PLAN and SPARE lines L1..Ln")
    bom.add_argument("--out", help="write the BOM here (default: print it)")
    bom.add_argument("--report", help="purchase: write the report (source revision, excluded items) here")
    bom.add_argument("--schema-version", choices=("2", "1"), default="2",
                     help="purchase: 2 (default) or 1 (spares merged, for a service that reads only version 1)")
    _common(bom)

    run = add("run-optics", "run the plan's Project on this device and bind the run (a new revision)", _run_optics)
    run.add_argument("plan_dir")
    run.add_argument("--out", required=True, metavar="RUN_DIR", help="the run directory to write")
    _common(run)


__all__ = ["register"]
