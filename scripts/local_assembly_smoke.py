"""Smoke test of the local assembly journey on an INSTALLED os-optics (a built wheel, or a release).

Uses nothing but the installed package and two catalog files: no repository checkout, no test helpers, no OpenSci
Account token and no network. Run it with the Python of a fresh virtual environment, from a directory outside any
source checkout::

    python -m pip install dist/os_optics-*.whl
    python -I local_assembly_smoke.py --catalog test_optical_catalog.v2.json --mechanical test_mechanical_catalog.v1.json

The smoke uses a catalog store of its own (``<workdir>/catalog-store``, automatic checks off), removes every token
variable from the environment and points the commercial service at an unroutable loopback port. It imports both
catalog files (``os-optics catalog import``), writes the example Project, then runs ``assembly plan`` derive, show,
edit (a spare post and a declared envelope), check, preview, ``view PLAN --snapshot``, draw, bom ``--design`` and
``--purchase`` and history, and checks each exit code and document: the plan reopens, every revision verifies, the
preview is a LOCAL_PLAN package (manifest 3.1), the drawing is the Detailed drawing (4.2), the purchase BOM is the
version 2 request body with a SPARE line and without the customer's parts. It prints one JSON evidence summary and
exits 0 only if every check passed.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

TOKEN_VARIABLES = ("OPENSCI_TOKEN", "OPENSCI_TOKEN_FILE")


class Smoke:
    def __init__(self, workdir: Path) -> None:
        self.workdir = workdir
        self.failed: list[str] = []
        self.steps: list[dict] = []
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", *TOKEN_VARIABLES)}
        env.update(PYTHONIOENCODING="utf-8", OPENSCI_CATALOG_DIR=str(workdir / "catalog-store"), OPENSCI_CATALOG_CHECK_INTERVAL="0",
                   OPENSCI_API_URL="http://127.0.0.1:9", OPENSCI_CATALOG_URL="http://127.0.0.1:9/api/v1/catalog",
                   HOME=str(workdir / "home"), USERPROFILE=str(workdir / "home"))
        (workdir / "home").mkdir(parents=True, exist_ok=True)
        self.env = env

    def check(self, name: str, condition: bool, detail: object = None) -> bool:
        if not condition:
            self.failed.append(name if detail is None else f"{name}: {detail}")
        return condition

    def run(self, *argv: str, expect: tuple[int, ...] = (0,)) -> dict:
        result = subprocess.run([sys.executable, "-m", "os_optics", *argv], cwd=self.workdir, env=self.env, capture_output=True,
                                text=True, encoding="utf-8", timeout=900)
        self.steps.append({"command": " ".join(argv[:3]), "exit": result.returncode})
        self.check(f"exit code of {' '.join(argv[:4])}", result.returncode in expect,
                   f"{result.returncode}: {result.stdout[-600:]} {result.stderr[-600:]}")
        try:
            return json.loads(result.stdout) if result.stdout.strip() else {}
        except ValueError:
            return {}


def journey(smoke: Smoke, catalog: Path, mechanical: Path) -> dict:
    work = smoke.workdir
    smoke.run("catalog", "import", str(catalog))
    imported = smoke.run("catalog", "import", str(mechanical))
    smoke.check("mechanical release imported", imported.get("mechanical", {}).get("status") == "IMPORTED", imported)
    example = smoke.run("project", "example", "--kind", "focus")
    project = work / "project.json"
    project.write_text(json.dumps(example), encoding="utf-8")
    plan = work / "plan"
    derived = smoke.run("assembly", "plan", "derive", str(project), "--out", str(plan))
    smoke.check("derived revision 1", derived.get("revision") == 1, derived)
    smoke.check("derived plan is ready", derived.get("status") in ("READY", "READY_WITH_WARNINGS"), derived.get("status"))
    shown = smoke.run("assembly", "plan", "show", str(plan), "--items")
    smoke.check("the plan reopens", shown.get("content_digest") == derived.get("content_digest"))
    items = {i["item_id"]: i for i in shown.get("items", [])}
    post = items.get("pst-1", {})
    smoke.check("the lens stands on a post", post.get("role") == "POST", sorted(items))
    full = json.loads((plan / "plan.json").read_bytes())
    x, y, _ = next(i for i in full["items"] if i["item_id"] == "pst-1")["placement"]["pose_in_lab"]["position_mm"]
    box = {"shape": "BOX", "min_mm": [x - 10.0, y + 40.0, 0.0], "max_mm": [x + 10.0, y + 60.0, 40.0]}
    ops = [{"op": "set_purchase", "item_id": "pst-1", "selected": True, "spare_units": 1},
           {"op": "declare_envelope", "label": "Customer camera", "envelope": box}]
    edited = smoke.run("assembly", "plan", "edit", str(plan), "--ops", str(_write(work / "ops.json", ops)), "--op-id", "smoke-1")
    smoke.check("edit wrote revision 2", edited.get("revision") == 2, edited)
    replay = smoke.run("assembly", "plan", "edit", str(plan), "--ops", str(work / "ops.json"), "--op-id", "smoke-1")
    smoke.check("the same op id replays", replay.get("replayed") is True)
    checked = smoke.run("assembly", "plan", "check", str(plan), "--out", str(work / "check.json"))
    smoke.check("check result written", (work / "check.json").is_file() and checked.get("revision") == 2)
    preview = smoke.run("assembly", "plan", "preview", str(plan), "--out", str(work / "preview.zip"))
    smoke.check("a LOCAL_PLAN preview package", preview.get("manifest_schema_version") == "3.1", preview)
    smoke.run("view", str(plan), "--snapshot", str(work / "snapshot"))
    smoke.check("view snapshot written", (work / "snapshot" / "iso.png").is_file())
    drawn = smoke.run("assembly", "plan", "draw", str(plan), "--out", str(work / "drawing"))
    smoke.check("the Detailed drawing", drawn.get("delivery_ready") is True and drawn.get("schema_version") == "4.2", drawn)
    design = smoke.run("assembly", "plan", "bom", str(plan), "--design")
    roles = {line.get("role") for line in design.get("lines", [])}
    smoke.check("the design BOM lists the declared envelope", "DECLARED_ENVELOPE" in roles, roles)
    smoke.run("assembly", "plan", "bom", str(plan), "--purchase", "--out", str(work / "bom.json"))
    body = json.loads((work / "bom.json").read_bytes()) if (work / "bom.json").is_file() else {}
    purposes = [line.get("purpose") for line in body.get("lines", [])]
    smoke.check("a version 2 purchase BOM", body.get("schema_version") == "2", body)
    smoke.check("a SPARE line", purposes.count("SPARE") == 1, purposes)
    smoke.check("no customer part bought", all(line["opensci_sku"].startswith("OS-") for line in body.get("lines", [])))
    history = smoke.run("assembly", "plan", "history", str(plan))
    smoke.check("the journal", [line.get("kind") for line in history.get("lines", [])] == ["DERIVE", "EDIT"], history)
    for revision in (1, 2):
        smoke.run("assembly", "plan", "show", str(plan), "--revision", str(revision))
    return {"plan_id": derived.get("plan_id"), "revisions": 2, "purchase_lines": len(purposes),
            "package_content_hash": preview.get("package_content_hash"), "drawing_status": drawn.get("drawing_status")}


def _write(path: Path, document: object) -> Path:
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--catalog", required=True, type=Path, help="a public optical catalog release JSON to import")
    parser.add_argument("--mechanical", required=True, type=Path, help="a public mechanical catalog document (OPENSCI) to import")
    parser.add_argument("--workdir", type=Path, help="where to work (default: a new temporary directory)")
    args = parser.parse_args(argv)
    workdir = args.workdir or Path(tempfile.mkdtemp(prefix="os-optics-assembly-smoke-"))
    workdir.mkdir(parents=True, exist_ok=True)
    smoke = Smoke(workdir)
    evidence: dict = {}
    try:
        import os_optics

        evidence["version"] = os_optics.__version__
        evidence["journey"] = journey(smoke, args.catalog.resolve(), args.mechanical.resolve())
    except Exception as exc:  # noqa: BLE001 - every failure is evidence
        smoke.failed.append(f"{type(exc).__name__}: {exc}")
        traceback.print_exc(file=sys.stderr)
    evidence.update(ok=not smoke.failed, failed=smoke.failed, steps=smoke.steps, workdir=str(workdir))
    print(json.dumps(evidence, sort_keys=True))
    return 0 if not smoke.failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
