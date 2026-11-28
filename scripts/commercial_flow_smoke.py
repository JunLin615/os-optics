"""End-to-end smoke test of the local-design commercial workflow against a running OpenSci instance, through
``os_optics.client`` only (what a customer's Agent uses).

Configuration is the customer's: ``OPENSCI_API_URL`` and the Account token (``OPENSCI_TOKEN`` / ``OPENSCI_TOKEN_FILE`` /
``~/.opensci/token``) of a token carrying the ``agent`` profile scopes. The token is never printed or written.

Two phases, because OpenSci confirms the payment outside this API (the operator console of the instance)::

    python scripts/commercial_flow_smoke.py phase1 --work-dir WORK --transfer-made
        token status -> local run of the reference Project -> submit -> resolution (+ presentation) -> Assembly
        Preview opened in memory -> local assembly validation -> Assembly Detail refused (no Order) -> create the Order
        (no confirmation step) -> Order presentations -> report the bank transfer (only with --transfer-made: the
        tester states the transfer was made) -> Assembly Detail refused (payment not confirmed)
    # now confirm the payment of the printed Order Number with the instance's operator console
    python scripts/commercial_flow_smoke.py phase2 --work-dir WORK [--wait-for-confirmation SECONDS]
        (poll the Order until OpenSci confirmed it) -> Assembly Detail in memory -> Detailed Assembly Drawing written
        to WORK/drawing (drawings are never uploaded: they stay on this device)

The local run uses the public optical catalog of the local catalog store (synchronized from the instance's catalog
service, ``OPENSCI_CATALOG_URL`` / ``OPENSCI_API_URL``), or the release file given with ``--catalog``; the report names
the ``catalog_version`` the run was bound to. ``all`` runs both phases (use it with ``--wait-for-confirmation`` and
confirm while it waits). Each phase prints a
JSON report and exits 0 when every expectation held, 1 otherwise. WORK keeps ``state.json`` (ids only), the run
directory, the validation JSON and the drawing; nothing decrypted is written.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from os_optics.assembly import result_file_bytes
from os_optics.client import AssemblyDetailNotReleasedError, OpenSciApiError, OpenSciClient, Recovery
from os_optics.local import LocalRunError, example_project, load_project, run_optical_project, verify_run_artifacts

POLL_SECONDS = 5.0


class Expectation(Exception):
    pass


def expect(condition: bool, what: str) -> None:
    if not condition:
        raise Expectation(what)


def detail_refused(client: OpenSciClient, resolution_id: str, preview: Any, recoveries: tuple[Recovery, ...]) -> str:
    try:
        client.assembly_detail(resolution_id, preview=preview)
    except AssemblyDetailNotReleasedError as exc:
        expect(exc.recovery in recoveries, f"detail refused with recovery {exc.recovery}, expected one of {recoveries}")
        expect(exc.http_status == 403, f"detail refused with HTTP {exc.http_status}")
        return exc.recovery.value
    raise Expectation("the Assembly Detail was delivered before OpenSci confirmed the payment")


def phase1(client: OpenSciClient, work: Path, args: argparse.Namespace) -> dict[str, Any]:
    report: dict[str, Any] = {"phase": 1}
    status = client.token_status()
    missing = [name for name, held in status["capabilities"].items() if not held and name != "manage_tokens"]
    expect(not missing, f"the token lacks capabilities {missing}")
    report["token"] = {"token_id": status["token_id"], "organization": status["organization"]["name"],
                       "expires_at": status["expires_at"]}

    catalog = Path(args.catalog) if args.catalog else None
    try:
        project = load_project(Path(args.project)) if args.project else example_project(catalog)
        run = run_optical_project(project, catalog=catalog, output_dir=work / "run")
    except LocalRunError as exc:
        raise Expectation(f"the local run was refused: {exc}") from None
    expect(verify_run_artifacts(work / "run") == () and run.binding_problems() == (), "the run directory does not verify")
    if run.status.value == "UNKNOWN":
        # A real public glass without stated bulk absorption does not give a measured optical power.
        # It does not excuse an undecidable/failing focus or a different UNKNOWN reason.
        issues = {issue.code.value for issue in run.result.issues}
        targets = {target.target_id: target for target in run.result.targets}
        expect(issues == {"BULK_LOSS_NOT_ESTABLISHED"} and set(targets) == {"focus_position", "spot_radius"}
               and all(target.status.value == "PASS" and target.assessment.value == "MET" and target.value is not None
                       for target in targets.values()), f"the local run is {run.status.value}: {sorted(issues)}, "
                                                     f"targets {[(t.target_id, t.status.value) for t in targets.values()]}")
        expect(any(a.beam.power_W is None and a.power_upper_bound_W is not None
                   for o in run.result.observations for a in o.arrivals), "bulk loss UNKNOWN lacks a finite beam-power bound")
    else:
        expect(run.status.value in ("PASS", "WARNING"), f"the local run is {run.status.value}")
    report["local_run"] = {"status": run.status.value, "project_hash": project.content_hash(), "catalog_version": run.catalog_version,
                           "targets": {t.target_id: t.status.value for t in run.result.targets}}

    submitted = client.submit(work / "run")
    resolution_id = submitted["resolution_id"]
    again = client.submit(run)
    expect(again["replayed"] is True and again["resolution_id"] == resolution_id, "a repeated submit did not replay")
    read = client.resolution(resolution_id)
    expect(read["resolution_id"] == resolution_id, "resolution read mismatch")
    presentations = {}
    for locale in ("zh-CN", "en"):
        try:
            presentations[locale] = bool(client.resolution_presentation(resolution_id, locale=locale))
        except OpenSciApiError as exc:
            presentations[locale] = exc.code
    report["resolution"] = {"resolution_id": resolution_id, "replayed_first_submit": submitted["replayed"],
                            "presentations": presentations}

    preview = client.assembly_preview(resolution_id)
    validation = client.validate_assembly(preview)
    (work / "assembly_local_validation.json").write_bytes(result_file_bytes(validation))
    report["preview"] = {"package_content_hash": preview.content_hash, "validation_status": validation.status.value}

    existing = [o for o in client.orders()["orders"]]
    report["detail_before_order"] = detail_refused(client, resolution_id, preview,
                                                   (Recovery.CREATE_ORDER_FIRST, Recovery.WAIT_FOR_PAYMENT_CONFIRMATION)
                                                   if existing else (Recovery.CREATE_ORDER_FIRST,))

    placed = client.create_order(resolution_id)
    order = placed["order"]
    expect(client.create_order(resolution_id)["order"]["order_id"] == order["order_id"], "a repeated create_order "
           "returned another Order")
    for locale in ("zh-CN", "en"):
        for document in ("order", "quotation", "contract"):
            client.order_presentation(order["order_id"], locale=locale, document=document)
    payment = order["payment"]
    report["order"] = {"order_id": order["order_id"], "order_number": order["order_number"], "replayed": placed["replayed"],
                       "total": order.get("total"), "currency": order.get("currency"), "payment_state": payment["state"],
                       "transfer_reference": payment.get("transfer_reference")}

    if args.transfer_made:
        reported = client.report_bank_transfer(order["order_id"])
        expect(reported["order"]["payment"]["state"] in ("PENDING_CONFIRMATION", "CONFIRMED"),
               f"payment state after the report: {reported['order']['payment']['state']}")
        report["transfer_report"] = {"replayed": reported["replayed"], "payment_state": reported["order"]["payment"]["state"]}
        if reported["order"]["payment"]["state"] != "CONFIRMED":
            report["detail_after_report"] = detail_refused(client, resolution_id, preview,
                                                           (Recovery.WAIT_FOR_PAYMENT_CONFIRMATION,))
    else:
        report["transfer_report"] = "not reported (run with --transfer-made once the transfer was made)"
    state = {"resolution_id": resolution_id, "order_id": order["order_id"], "order_number": order["order_number"]}
    (work / "state.json").write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    report["next"] = f"confirm the payment of Order {order['order_number']} with the instance's operator console, then run phase2"
    return report


def phase2(client: OpenSciClient, work: Path, args: argparse.Namespace) -> dict[str, Any]:
    state = json.loads((work / "state.json").read_text(encoding="utf-8"))
    resolution_id, order_id = state["resolution_id"], state["order_id"]
    report: dict[str, Any] = {"phase": 2, "resolution_id": resolution_id, "order_number": state["order_number"]}
    deadline = time.monotonic() + (args.wait_for_confirmation or 0)
    while True:
        payment_state = client.order(order_id)["payment"]["state"]
        if payment_state == "CONFIRMED" or time.monotonic() >= deadline:
            break
        time.sleep(POLL_SECONDS)
    expect(payment_state == "CONFIRMED", f"the payment is {payment_state}; confirm it with the operator console first")

    preview = client.assembly_preview(resolution_id)
    detail = client.assembly_detail(resolution_id, preview=preview)
    out = work / "drawing"
    drawing = client.assembly_drawing(resolution_id, out, preview=preview)
    expect(drawing.delivery_ready, f"the drawing is {drawing.drawing_status.value}")
    report["drawing"] = {"status": drawing.drawing_status.value, "detail_content_hash": detail.content_hash,
                         "files": sorted(p.name for p in out.iterdir())}
    # (drawings are never uploaded: they stay on this device)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("phase", choices=("phase1", "phase2", "all"))
    parser.add_argument("--work-dir", required=True, help="where state.json, the run, the validation and the drawing go")
    parser.add_argument("--project", help="a ProjectDocumentV2 JSON file (default: the reference example Project)")
    parser.add_argument("--catalog", help="a public optical catalog release file to design and run against (default: the "
                                          "local catalog store)")
    parser.add_argument("--transfer-made", action="store_true",
                        help="phase1: report the bank transfer (state that the transfer was made)")
    parser.add_argument("--wait-for-confirmation", type=float, default=0.0, metavar="SECONDS",
                        help="phase2: poll the Order up to this long until OpenSci has confirmed the payment")
    args = parser.parse_args(argv)
    work = Path(args.work_dir)
    work.mkdir(parents=True, exist_ok=True)
    reports = []
    try:
        client = OpenSciClient.from_environment()
        if args.phase in ("phase1", "all"):
            reports.append(phase1(client, work, args))
        if args.phase in ("phase2", "all"):
            reports.append(phase2(client, work, args))
    except Expectation as exc:
        reports.append({"status": "FAILED", "expectation": str(exc)})
    except OpenSciApiError as exc:
        reports.append({"status": "FAILED", "error": exc.as_dict()})
    ok = not any(r.get("status") == "FAILED" for r in reports)
    print(json.dumps({"status": "PASSED" if ok else "FAILED", "api_url": client.api_url if "client" in locals() else None,
                      "reports": reports}, indent=2, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
