"""Run the README / getting-started quickstart against a clean installation.

Creates a fresh virtual environment, installs the engine release recorded in
engine-baseline.json (or a local wheel with --engine-wheel), then runs the documented
commands in order: doctor, catalog sync, project example, project check, project run,
results describe / targets, export arrivals, project verify, view --snapshot and the
README's Python example. Any non-zero exit fails the check. The catalog store lives
inside the work directory; --catalog-file imports a catalog release file instead of
downloading one. Prints one compact JSON line; per-step output is kept in WORK/logs.

    python scripts/check_quickstart.py [--work DIR]
    python scripts/check_quickstart.py --engine-wheel os_optics-X.Y.Z-py3-none-any.whl --catalog-file catalog.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STEP_TIMEOUT_S = 900


def readme_python_example() -> str:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    match = re.search(r"```python\n(.*?)```", text, re.S)
    if not match:
        raise SystemExit("README.md has no Python example")
    return match.group(1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--work", type=Path, help="work directory (default: a new temporary directory)")
    parser.add_argument("--engine-wheel", type=Path, help="install this wheel instead of the PyPI release")
    parser.add_argument("--catalog-file", type=Path, help="import this catalog release file instead of 'catalog sync'")
    parser.add_argument("--python", default=sys.executable, help="interpreter for the virtual environment")
    args = parser.parse_args()

    baseline = json.loads((ROOT / "engine-baseline.json").read_text(encoding="utf-8"))
    extra = baseline["extra"]
    if args.engine_wheel:
        requirement = f"{args.engine_wheel.resolve()}[{extra}]"
    else:
        requirement = f"{baseline['distribution']}[{extra}]=={baseline['version']}"

    work = (args.work or Path(tempfile.mkdtemp(prefix="os-optics-quickstart-"))).resolve()
    project_dir, logs = work / "project", work / "logs"
    project_dir.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    venv = work / ".venv"
    bin_dir = venv / ("Scripts" if os.name == "nt" else "bin")
    python = bin_dir / ("python.exe" if os.name == "nt" else "python")
    cli = str(bin_dir / "os-optics")

    env = dict(os.environ, OPENSCI_CATALOG_DIR=str(work / "catalog"), PYTHONIOENCODING="utf-8")
    if args.catalog_file:
        env["OPENSCI_CATALOG_CHECK_INTERVAL"] = "0"
        catalog_step = [cli, "catalog", "import", str(args.catalog_file.resolve())]
    else:
        catalog_step = [cli, "catalog", "sync"]

    steps = [
        ("venv", [args.python, "-m", "venv", str(venv)], None),
        ("install", [str(python), "-m", "pip", "install", "--disable-pip-version-check", requirement], None),
        ("doctor", [cli, "doctor"], None),
        ("catalog", catalog_step, None),
        ("example", [cli, "project", "example", "--kind", "beam_expander"], "project.json"),
        ("check", [cli, "project", "check", "project.json"], None),
        ("run", [cli, "project", "run", "project.json", "--out-dir", "run"], None),
        ("describe", [cli, "results", "describe", "run"], None),
        ("targets", [cli, "results", "targets", "run"], None),
        ("export", [cli, "export", "arrivals", "run", "--format", "csv", "--out", "arrivals.csv"], None),
        ("verify", [cli, "project", "verify", "run"], None),
        ("snapshot", [cli, "view", "run", "--snapshot", "shots"], None),
        ("python", [str(python), "-c", readme_python_example()], None),
    ]

    if args.catalog_file:
        # With automatic catalog access off, doctor reports a missing catalog; import first.
        steps[2], steps[3] = steps[3], steps[2]

    results = []
    status = "OK"
    for index, (name, command, stdout_file) in enumerate(steps, 1):
        started = time.monotonic()
        try:
            completed = subprocess.run(command, cwd=project_dir, env=env, capture_output=True,
                                       timeout=STEP_TIMEOUT_S, encoding="utf-8", errors="replace")
            code, out, err = completed.returncode, completed.stdout, completed.stderr
        except (OSError, subprocess.TimeoutExpired) as error:
            code, out, err = -1, "", f"{type(error).__name__}: {error}"
        (logs / f"{index:02d}-{name}.log").write_text(
            f"$ {' '.join(command[:-1] if name == 'python' else command)}\nexit {code}\n"
            f"--- stdout\n{out}\n--- stderr\n{err}\n", encoding="utf-8")
        if stdout_file and code == 0:
            (project_dir / stdout_file).write_text(out, encoding="utf-8")
        results.append({"step": name, "exit": code, "seconds": round(time.monotonic() - started, 1)})
        if code != 0:
            status = "FAIL"
            break

    print(json.dumps({"status": status, "requirement": requirement if not args.engine_wheel else
                      f"{args.engine_wheel.name}[{extra}]", "catalog": "import" if args.catalog_file else "sync",
                      "work": str(work), "steps": results}, separators=(",", ":")))
    return 0 if status == "OK" else 1


if __name__ == "__main__":
    sys.exit(main())
