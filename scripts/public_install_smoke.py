"""Smoke test of an INSTALLED os-optics (a built wheel / sdist, or the public PyPI release).

Uses nothing but the installed package: no repository checkout and no test helpers. Run it with the Python of a fresh
virtual environment, from a directory outside any source checkout::

    python -m pip install os-optics                # or "os-optics[protected]", or a wheel / sdist file
    python -I public_install_smoke.py --catalog public_optical_catalog.v2.json   # offline: import this catalog release
    python -I public_install_smoke.py                   # or: synchronize the catalog from the service (network)

The package bundles no public optical catalog: the smoke uses a catalog store of its own (``<workdir>/catalog-store``,
automatic checks off), checks that the fresh store has none, then imports ``--catalog FILE`` (``os-optics catalog
import``) or synchronizes it from the catalog service (``os-optics catalog sync``; ``OPENSCI_CATALOG_URL`` /
``OPENSCI_API_URL`` choose the service). It then checks that the package is imported from that environment's
site-packages, the version, the command line (every subcommand's help, ``doctor`` through the console script and
``python -m os_optics``, catalog status / verify / list / show, project example / run / verify / update-catalog,
schema), the public catalog from Python, every reference design through local simulation + Local Validation + the
Optical Simulation View (JSON and
GLB), and the low-level Engine API. With ``--protected`` it also checks the optional AES-256-GCM dependency; with
``--deliveries DIR`` (holding ``preview-delivery.json`` and ``detail-delivery.json`` delivery documents that are valid
now) it opens both in memory, validates the assembly, generates the drawing and builds the artifact uploads -- nothing
decrypted is written to disk. It prints one JSON evidence summary and exits 0 only if every check passed.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import sysconfig
import tempfile
import traceback
from importlib import metadata
from pathlib import Path

HELP_COMMANDS = (
    [], ["catalog"], ["catalog", "list"], ["catalog", "show"], ["project"], ["project", "example"], ["project", "run"],
    ["project", "verify"], ["project", "update-catalog"], ["catalog", "status"], ["catalog", "check"], ["catalog", "sync"],
    ["catalog", "verify"], ["catalog", "import"], ["assembly"], ["assembly", "verify"], ["assembly", "validate"], ["assembly", "draw"],
    ["assembly", "verify-drawing"], ["schema"], ["validate"], ["compare"], ["doctor"],
)


def _value(member: object) -> object:
    return getattr(member, "value", member)


class Smoke:
    def __init__(self) -> None:
        self.failed: list[str] = []

    def check(self, name: str, condition: bool, detail: object = None) -> bool:
        if not condition:
            self.failed.append(name if detail is None else f"{name}: {detail}")
        return condition

    def guard(self, name: str, fn):  # noqa: ANN001 - small helper
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - every failure is evidence
            self.failed.append(f"{name}: {type(exc).__name__}: {exc}")
            traceback.print_exc(file=sys.stderr)
            return None


def _cli(exe: Path, args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP")}
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run([str(exe), *args], cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", timeout=600)


def installation(smoke: Smoke) -> dict:
    import os_optics
    from os_optics.manifest.build import engine_build_id

    module_file = Path(os_optics.__file__).resolve()
    purelib = Path(sysconfig.get_paths()["purelib"]).resolve()
    smoke.check("imported from this environment's site-packages", purelib in module_file.parents, str(module_file))
    dist = metadata.distribution("os-optics")
    direct_url = dist.read_text("direct_url.json")
    editable = bool(direct_url and json.loads(direct_url).get("dir_info", {}).get("editable"))
    smoke.check("not an editable install", not editable)
    requires = sorted(dist.requires or [])
    return {
        "python": sys.version.split()[0],
        "isolated_mode": bool(sys.flags.isolated),
        "os_optics_file": str(module_file),
        "distribution_version": dist.version,
        "install_source": "direct URL / local file" if direct_url else "package index",
        "requires_dist": requires,
        "version": os_optics.__version__,
        "engine_build_id": engine_build_id(),
        "schema_version": os_optics.SCHEMA_VERSION,
        "numerical_config_version": os_optics.NUMERICAL_CONFIG_VERSION,
    }


def catalog_store(smoke: Smoke, work: Path, catalog_file: Path | None) -> dict:
    """A catalog store of the smoke's own: empty first, then the given release imported (or the service's synchronized)."""

    os.environ["OPENSCI_CATALOG_DIR"] = str(work / "catalog-store")
    os.environ["OPENSCI_CATALOG_CHECK_INTERVAL"] = "0"
    scripts = Path(sysconfig.get_paths()["scripts"])
    exe = next((p for p in (scripts / "os-optics.exe", scripts / "os-optics") if p.is_file()), None)
    if not smoke.check("console script installed", exe is not None, str(scripts)):
        return {}
    empty = _cli(exe, ["catalog", "status"], work)
    smoke.check("a fresh store holds no catalog (catalog status exit 1)", empty.returncode == 1 and not json.loads(empty.stdout)["available"],
                empty.stdout[-300:] + empty.stderr[-300:])
    refused = _cli(exe, ["project", "example"], work)
    problems = json.loads(refused.stdout).get("problems", []) if refused.stdout.strip().startswith("{") else []
    smoke.check("no catalog -> CATALOG_NOT_AVAILABLE (exit 65)", refused.returncode == 65 and [p["code"] for p in problems] == ["CATALOG_NOT_AVAILABLE"],
                refused.stdout[-300:])
    command = ["catalog", "import", str(catalog_file.resolve())] if catalog_file is not None else ["catalog", "sync"]
    added = _cli(exe, command, work)
    status = json.loads(added.stdout) if added.stdout.strip().startswith("{") else {}
    smoke.check(f"catalog {command[1]}", added.returncode == 0 and bool(status.get("available")), added.stdout[-500:] + added.stderr[-300:])
    verify = _cli(exe, ["catalog", "verify", "--deep"], work)
    smoke.check("catalog verify", verify.returncode == 0 and json.loads(verify.stdout)["ok"], verify.stdout[-300:])
    return {"source": "import" if catalog_file is not None else "sync", "store": status.get("store"), "active": status.get("active")}


def command_line(smoke: Smoke, work: Path) -> dict:
    scripts = Path(sysconfig.get_paths()["scripts"])
    exe = next((p for p in (scripts / "os-optics.exe", scripts / "os-optics") if p.is_file()), None)
    if not smoke.check("console script installed", exe is not None, str(scripts)):
        return {}
    out: dict = {"console_script": str(exe)}
    version = _cli(exe, ["--version"], work)
    out["version_output"] = version.stdout.strip()
    helps = {}
    for args in HELP_COMMANDS:
        proc = _cli(exe, [*args, "--help"], work)
        helps[" ".join(args) or "(main)"] = proc.returncode
        smoke.check(f"help: {' '.join(args) or '(main)'}", proc.returncode == 0 and "usage:" in proc.stdout, proc.stderr[-300:])
    out["help_exit_codes"] = helps
    doctor = _cli(exe, ["doctor"], work)
    report = json.loads(doctor.stdout) if doctor.stdout.strip().startswith("{") else {}
    smoke.check("doctor exit 0 / local design ready", doctor.returncode == 0 and report.get("ready", {}).get("local_design") is True,
                doctor.stdout[-300:] + doctor.stderr[-300:])
    smoke.check("doctor: this Python is supported", report.get("python", {}).get("supported") is True)
    # (the catalog commands and the catalog Project below need it: its own readiness fact, not the exit code)
    smoke.check("doctor: the catalog is ready", report.get("ready", {}).get("catalog") is True, doctor.stdout[-300:])
    module = subprocess.run([sys.executable, "-m", "os_optics", "doctor"], cwd=work, capture_output=True, text=True,
                            encoding="utf-8", timeout=600)
    smoke.check("python -m os_optics doctor", module.returncode == 0 and json.loads(module.stdout)["ready"]["local_design"],
                module.stderr[-300:])
    out["doctor"] = {"exit": doctor.returncode, "python": report.get("python"), "ready": report.get("ready"),
                     "install_kind": report.get("install", {}).get("kind"),
                     "protected_extra": report.get("protected_extra", {}).get("installed"),
                     "next_steps": [step["check"] for step in report.get("next_steps", [])]}
    listing = _cli(exe, ["catalog", "list", "--category", "LENS", "--wavelength-nm", "780", "--limit", "3"], work)
    rows = json.loads(listing.stdout) if listing.returncode == 0 else {}
    smoke.check("catalog list", listing.returncode == 0 and len(rows.get("products", [])) == 3, listing.stderr[-300:])
    out["catalog_list"] = {"exit": listing.returncode, "total_lens_780": rows.get("total"), "catalog_version": rows.get("catalog_version")}
    if rows.get("products"):
        sku = rows["products"][0]["optical_sku"]
        show = _cli(exe, ["catalog", "show", sku], work)
        smoke.check("catalog show", show.returncode == 0 and json.loads(show.stdout)["optical_sku"] == sku, show.stderr[-300:])
        out["catalog_show"] = {"exit": show.returncode, "sku": sku}
        missing = _cli(exe, ["catalog", "show", "OS-OPT-NOT-A-PRODUCT"], work)
        smoke.check("catalog show refuses an unknown SKU with exit 65", missing.returncode == 65)
    example = _cli(exe, ["project", "example", "--kind", "beam_expander"], work)
    smoke.check("project example", example.returncode == 0, example.stderr[-300:])
    (work / "project.json").write_text(example.stdout, encoding="utf-8")
    run = _cli(exe, ["project", "run", "project.json", "--out-dir", "cli-run"], work)
    summary = json.loads(run.stdout) if run.stdout.strip().startswith("{") else {}
    # The default requirements exit contract is 0 when the beam expander's geometric target PASSes,
    # whether catalog glass has measured bulk loss (known power) or no loss evidence (bounded unknown power).
    targets = {t["target_id"]: t["status"] for t in summary.get("targets", [])}
    document = json.loads(example.stdout) if example.returncode == 0 else {}
    sources = [component["source_model"] for component in document.get("components", []) if "source_model" in component]
    from os_optics.local import load_public_catalog

    catalog = load_public_catalog()
    model_media = {medium for component in document.get("components", []) if "product" in component
                   for medium in catalog.get(component["product"]["optical_sku"]).simulation_models[0].media}
    wavelength = sources[0].get("wavelength_nm") if len(sources) == 1 else None
    measured_loss = bool(model_media and wavelength is not None) and all(
        (loss := catalog.material(medium).bulk_loss) is not None
        and float(loss.wavelength_nm[0]) <= wavelength <= float(loss.wavelength_nm[-1])
        for medium in model_media)
    smoke.check("project uses one characterized line and published lens material", len(sources) == 1 and bool(model_media))
    expected_status = "PASS" if measured_loss else "UNKNOWN"
    smoke.check("project run exit 0 / strict geometric target / evidence-based power status",
                run.returncode == 0 and summary.get("status") == expected_status
                and summary.get("exit_contract") == "requirements"
                and targets == {"expanded_waist": "PASS"},
                {"status": summary.get("status"), "expected": expected_status, "targets": summary.get("targets"),
                 "materials": sorted(model_media), "wavelength_nm": wavelength, "stderr": run.stderr[-300:]})
    result_path = work / "cli-run" / "simulation_result.json"
    result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.is_file() else {}
    issue_codes = {issue["code"] for issue in result.get("issues", [])}
    arrivals = [a for plane in result.get("observations", []) for a in plane.get("arrivals", [])]
    bounded_arrivals = [a for a in arrivals if a["beam"]["power_W"] is None
                        and isinstance(a.get("power_upper_bound_W"), (int, float))]
    known_arrivals = [a for a in arrivals if isinstance(a["beam"]["power_W"], (int, float))]
    smoke.check("project power agrees with published bulk-loss evidence, never presumed zero",
                (bool(known_arrivals) and not bounded_arrivals and "BULK_LOSS_NOT_ESTABLISHED" not in issue_codes
                 if measured_loss else
                 bool(bounded_arrivals) and not known_arrivals and issue_codes == {"BULK_LOSS_NOT_ESTABLISHED"}),
                {"issues": sorted(issue_codes), "known_arrivals": len(known_arrivals),
                 "bounded_arrivals": len(bounded_arrivals), "measured_loss": measured_loss})
    verify = _cli(exe, ["project", "verify", "cli-run"], work)
    smoke.check("project verify", verify.returncode == 0 and json.loads(verify.stdout)["status"] == "CONSISTENT", verify.stdout[-300:])
    update = _cli(exe, ["project", "update-catalog", "project.json"], work)
    smoke.check("project update-catalog: already current", update.returncode == 0 and json.loads(update.stdout)["status"] == "CURRENT",
                update.stdout[-300:])
    files = sorted(p.name for p in (work / "cli-run").iterdir()) if (work / "cli-run").is_dir() else []
    schema = _cli(exe, ["schema", "project"], work)
    smoke.check("schema project", schema.returncode == 0 and "properties" in json.loads(schema.stdout), schema.stderr[-300:])
    out["project_run"] = {"exit": run.returncode, "status": summary.get("status"), "exit_contract": summary.get("exit_contract"),
                          "targets": [(t["target_id"], t["status"]) for t in summary.get("targets", [])],
                          "verify_exit": verify.returncode, "files": files}
    return out


def catalog_api(smoke: Smoke) -> dict:
    from collections import Counter

    from os_optics.local import load_public_catalog

    catalog = load_public_catalog()
    lenses = catalog.select(category="LENS", form="PLANO_CONVEX_LENS", wavelength_nm="780")
    smoke.check("catalog has products", len(catalog.products) > 0)
    smoke.check("catalog query returns lenses at 780 nm", len(lenses) > 0)
    lens = lenses[0]
    smoke.check("catalog get", catalog.get(lens.optical_sku) == lens)
    return {
        "catalog_version": catalog.catalog_version,
        "products": len(catalog.products),
        "by_category": dict(sorted(Counter(_value(p.category) for p in catalog.products).items())),
        "by_commercial_readiness": dict(sorted(Counter(_value(p.commercial_readiness) for p in catalog.products).items())),
        "plano_convex_lenses_at_780nm": len(lenses),
        "inspected": {"optical_sku": lens.optical_sku, "display_name": lens.display_name,
                      "focal_length_mm": lens.specifications.focal_length_mm.value,
                      "model_kind": _value(lens.simulation_models[0].model_kind)},
    }


def designs(smoke: Smoke, work: Path) -> dict:
    from os_optics.local import EXAMPLE_KINDS, example_project_of_kind, run_optical_project, verify_run_artifacts

    results = {}
    for kind in EXAMPLE_KINDS:
        run = run_optical_project(example_project_of_kind(kind), output_dir=work / f"run-{kind}")
        problems = verify_run_artifacts(run.artifacts.directory)
        glb = run.artifacts.paths["optical_scene_glb"].read_bytes()
        scene = json.loads(run.artifacts.paths["optical_scene"].read_text(encoding="utf-8"))
        record = json.loads(run.artifacts.paths["local_validation"].read_text(encoding="utf-8"))
        statuses = {t.target_id: t.status.value for t in run.result.targets}
        bulk_unknown = any(issue.code.value == "BULK_LOSS_NOT_ESTABLISHED" for issue in run.result.issues)
        phase_undefined = any("PHASE_UNDEFINED" in t.issue_codes for t in run.result.targets)
        unknown_targets = any(t.status.value == "UNKNOWN" for t in run.result.targets)
        # An undefined coating phase does not defeat a requirement whose whole allowed range meets its bound.
        expected_status = "UNKNOWN" if bulk_unknown or unknown_targets else "PASS"
        smoke.check(f"design {kind}: geometric targets remain met; unknown loss/phase stays unknown",
                    run.status.value == expected_status
                    and all(t.status.value == "PASS" for t in run.result.targets if t.metric in
                            ("waist_distance_x_mm", "waist_radius_x_mm", "beam_radius_mm"))
                    and all(t.status.value in ("PASS", "UNKNOWN") and
                            (t.status.value == "PASS" or (t.value is None and
                             bool(set(t.issue_codes) & {"BULK_LOSS_NOT_ESTABLISHED", "PHASE_UNDEFINED"})))
                            for t in run.result.targets)
                    and (not bulk_unknown or any(a.beam.power_W is None and a.power_upper_bound_W is not None
                                                 for o in run.result.observations for a in o.arrivals)),
                    {"status": run.status.value, "targets": statuses, "bulk_unknown": bulk_unknown,
                     "phase_undefined": phase_undefined, "unknown_targets": unknown_targets})
        smoke.check(f"design {kind}: run directory verifies", problems == (), problems)
        smoke.check(f"design {kind}: record binding", run.binding_problems() == (), run.binding_problems())
        smoke.check(f"design {kind}: GLB", glb[:4] == b"glTF" and len(glb) > 1000)
        smoke.check(f"design {kind}: scene and record share the Engine run",
                    scene["engine_run"]["result_hash"]["digest"] == record["result_hash"]["digest"])
        results[kind] = {
            "status": run.status.value,
            "validation_status": run.local_validation.validation_status.value,
            "optical_elements": sum(1 for c in run.project.components if c.product is not None),
            "targets": [(t.target_id, t.metric, t.status.value, round(t.value, 6) if t.value is not None else None)
                        for t in run.result.targets],
            "scene_elements": len(scene["elements"]),
            "beam_paths": len(scene["beam_paths"]),
            "glb_bytes": len(glb),
        }
    return results


def low_level(smoke: Smoke) -> dict:
    from os_optics import ValidationRequest, validate
    from os_optics import builders as B
    from os_optics.contracts import ProjectSnapshot, Target

    project = ProjectSnapshot(
        components=(B.laser("L", wavelength_nm=532.0, waist_radius_mm=1.0, position=(0, 0, 20), direction=(1, 0, 0)),
                    B.thin_lens("Lens", focal_length_mm=100.0, clear_radius_mm=12.7, position=(10, 0, 20), axis=(1, 0, 0))),
        observations=(B.observation_plane("focus", position=(110, 0, 20), normal=(1, 0, 0), radius_mm=5.0),),
        targets=(Target(target_id="w", metric="beam_radius_mm", observation_id="focus", maximum=0.05),),
    )
    result = validate(ValidationRequest(project=project))
    smoke.check("low-level validate PASS", result.status.value == "PASS", result.status.value)
    return {"status": result.status.value, "focused_radius_mm": round(result.targets[0].value, 6)}


def protected(smoke: Smoke, require: bool, deliveries: Path | None) -> dict:
    from os_optics.protected_delivery import ProtectedDeliveryError, cryptography_available, open_preview_delivery

    available = cryptography_available()
    out: dict = {"cryptography_available": available}
    if require:
        smoke.check("[protected] extra: cryptography importable", available)
        from os_optics.cli_doctor import doctor_report

        smoke.check("doctor reports the [protected] extra", doctor_report()["protected_extra"]["installed"] is True)
    try:
        open_preview_delivery(b"{}")
        smoke.check("a malformed delivery is refused", False)
    except ProtectedDeliveryError as exc:
        out["malformed_delivery_refusal"] = exc.code.value
    if deliveries is None:
        return out
    from os_optics.assembly import validate_assembly_layout
    from os_optics.assembly_drawing import generate_assembly_drawing
    from os_optics.client_artifacts import drawing_artifact_upload, validation_artifact_upload
    from os_optics.protected_delivery import open_detail_delivery

    before = sorted(p.name for p in deliveries.iterdir())
    preview = open_preview_delivery((deliveries / "preview-delivery.json").read_bytes())
    detail = open_detail_delivery((deliveries / "detail-delivery.json").read_bytes(), preview=preview)
    validation = validate_assembly_layout(preview)
    drawing = generate_assembly_drawing(preview, detail)
    uploads = [validation_artifact_upload(preview, drawing.validation), drawing_artifact_upload(drawing)]
    smoke.check("protected round trip wrote nothing", sorted(p.name for p in deliveries.iterdir()) == before)
    smoke.check("drawing delivery ready", drawing.result.delivery_ready)
    out["round_trip"] = {
        "assembly_validation": validation.status.value,
        "drawing_status": drawing.result.drawing_status.value,
        "drawing_files": sorted(drawing.files),
        "uploads": [u.manifest.artifact_kind.value for u in uploads],
    }
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--expect-version", default="0.15.0", help="the version that must be installed (default %(default)s)")
    parser.add_argument("--catalog", type=Path, help="import this public optical catalog release file (offline) instead of "
                                                     "synchronizing from the catalog service")
    parser.add_argument("--protected", action="store_true", help="require the [protected] extra")
    parser.add_argument("--deliveries", type=Path, help="directory with preview-delivery.json and detail-delivery.json")
    parser.add_argument("--workdir", type=Path, help="scratch directory for run outputs (default: a new temporary one)")
    args = parser.parse_args(argv)
    smoke = Smoke()
    work = (args.workdir or Path(tempfile.mkdtemp(prefix="opensci-smoke-"))).resolve()
    work.mkdir(parents=True, exist_ok=True)
    evidence: dict = {"workdir": str(work)}
    evidence["installation"] = smoke.guard("installation", lambda: installation(smoke)) or {}
    smoke.check("version", evidence["installation"].get("version") == args.expect_version, evidence["installation"].get("version"))
    evidence["catalog_store"] = smoke.guard("catalog_store", lambda: catalog_store(smoke, work, args.catalog))
    evidence["cli"] = smoke.guard("cli", lambda: command_line(smoke, work))
    evidence["catalog"] = smoke.guard("catalog", lambda: catalog_api(smoke))
    evidence["designs"] = smoke.guard("designs", lambda: designs(smoke, work))
    evidence["low_level"] = smoke.guard("low_level", lambda: low_level(smoke))
    evidence["protected"] = smoke.guard("protected", lambda: protected(smoke, args.protected, args.deliveries))
    evidence["failed_checks"] = smoke.failed
    evidence["ok"] = not smoke.failed
    print(json.dumps(evidence, indent=2, ensure_ascii=False))
    return 0 if evidence["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
