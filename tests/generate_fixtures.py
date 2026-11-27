"""Regenerate tests/fixtures/requests/*.json, tests/fixtures/snapshots/*.json and (with --freeze) the contract-freeze baseline.

    python tests/generate_fixtures.py            # requests + snapshots
    python tests/generate_fixtures.py --freeze   # additionally rewrite contract_freeze.json (refused without a SCHEMA_VERSION bump)
    python tests/generate_fixtures.py --freeze --unreleased   # re-record it within a SCHEMA_VERSION no release has published
    python tests/generate_fixtures.py mechanical # only tests/fixtures/mechanical/*.json (mechanical_fixture.py)

The request files are the shared inputs of the native tests, the CLI test and the Pyodide parity harness. The snapshot files are
regression snapshots of the engine's own output (independent physical verification lives in the golden tests, not here).

Neither holds the identity of the build that wrote it (``identity_mask.py``), so regenerating changes a file only where content
moved. A version bump or a source edit needs no regeneration; a SCHEMA_VERSION bump needs ``--freeze``, which changes one file.

SCHEMA_VERSION moves once per release (``_version.py``): the first change of a frozen contract after a published release
bumps it (``--freeze``); a further change before that version is published re-records the baseline within it
(``--freeze --unreleased``). A published version's contracts never change in place, so ``--unreleased`` is for a version no
release of os-optics carries: it is refused unless ``__version__`` and ``SCHEMA_VERSION`` are both past the last published
release recorded in ``_version.py`` (``LAST_PUBLISHED_VERSION``, ``LAST_PUBLISHED_SCHEMA_VERSION``)."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from os_optics import SCHEMA_VERSION, __version__, validate  # noqa: E402
from os_optics._version import LAST_PUBLISHED_SCHEMA_VERSION, LAST_PUBLISHED_VERSION  # noqa: E402
from os_optics.contracts import ValidationRequest  # noqa: E402
from fixture_scenes import FIXTURES  # noqa: E402
from identity_mask import mask_result_identity, request_without_identity  # noqa: E402

REQ_DIR = HERE / "fixtures" / "requests"
SNAP_DIR = HERE / "fixtures" / "snapshots"
FREEZE_FILE = HERE / "fixtures" / "contract_freeze.json"


def canonical_request_text(request: ValidationRequest) -> str:
    return json.dumps(request_without_identity(json.loads(request.model_dump_json())), indent=1, sort_keys=True) + "\n"


def snapshot_text(request: ValidationRequest) -> str:
    result = json.loads(validate(request).model_dump_json())
    return json.dumps(mask_result_identity(result, request), indent=1, sort_keys=True) + "\n"


def frozen_models() -> dict[str, type]:
    """Contracts declared FROZEN in docs/engine/ENGINE_ARCHITECTURE.md, section 4.1."""
    from os_optics import NumericalConfig, ValidationIssue
    from os_optics import contracts as c

    return {
        "Pose3D": c.Pose3D, "JonesVector": c.JonesVector, "PolarizationBasis": c.PolarizationBasis,
        "ConstantIndexMaterial": c.ConstantIndexMaterial, "SellmeierMaterial": c.SellmeierMaterial,
        "TabulatedIndexMaterial": c.TabulatedIndexMaterial, "ConstantCoating": c.ConstantCoating, "TabulatedCoating": c.TabulatedCoating,
        "PlaneSurface": c.PlaneSurface, "SphereSurface": c.SphereSurface, "OpticalPort": c.OpticalPort, "Ray": c.Ray,
        "RaySegment": c.RaySegment, "OpticalPath": c.OpticalPath, "BeamState": c.BeamState, "GaussianBeamState": c.GaussianBeamState,
        "Target": c.Target, "Observation": c.Observation, "ValidationResult": c.ValidationResult, "ValidationIssue": ValidationIssue,
        "ValidationManifest": c.ValidationManifest, "NumericalConfig": NumericalConfig,
    }


def freeze_hashes() -> dict[str, str]:
    out = {}
    for name, model in sorted(frozen_models().items()):
        schema = json.dumps(model.model_json_schema(), sort_keys=True, separators=(",", ":"))
        out[name] = hashlib.sha256(schema.encode("utf-8")).hexdigest()
    return out


#: A release number: ``MAJOR.MINOR.PATCH``, digits only (no ``.devN``, ``rcN`` or local part).
RELEASE_NUMBER = re.compile(r"\d+\.\d+\.\d+")


def _release(version: str) -> tuple[int, ...] | None:
    """``version`` as numbers to compare, or ``None`` when it is no plain release number (``RELEASE_NUMBER``)."""
    return tuple(int(part) for part in version.split(".")) if RELEASE_NUMBER.fullmatch(version) else None


def published_release_problem(version: str = __version__, schema_version: str = SCHEMA_VERSION) -> str | None:
    """Why ``--unreleased`` may not re-record the baseline of ``version`` / ``schema_version``: either is not past the last
    published release (``LAST_PUBLISHED_VERSION`` / ``LAST_PUBLISHED_SCHEMA_VERSION``), so its contracts are a published
    release's, or one of them is no plain release number ``MAJOR.MINOR.PATCH`` (a ``.devN`` or ``rcN`` version), which
    cannot be compared with the published one. ``None``: neither is published."""
    for name, value in (("version", version), ("SCHEMA_VERSION", schema_version), ("LAST_PUBLISHED_VERSION", LAST_PUBLISHED_VERSION),
                        ("LAST_PUBLISHED_SCHEMA_VERSION", LAST_PUBLISHED_SCHEMA_VERSION)):
        if _release(value) is None:
            return (f"{name} {value} is no release number MAJOR.MINOR.PATCH, so it cannot be compared with the last published "
                    "release; set it to the release it becomes")
    if _release(version) <= _release(LAST_PUBLISHED_VERSION):
        return f"version {version} is published (the last published release is {LAST_PUBLISHED_VERSION})"
    if _release(schema_version) <= _release(LAST_PUBLISHED_SCHEMA_VERSION):
        return (f"SCHEMA_VERSION {schema_version} is a published release's (os-optics {LAST_PUBLISHED_VERSION} carries "
                f"{LAST_PUBLISHED_SCHEMA_VERSION})")
    return None


def write_freeze_baseline(unreleased: bool = False) -> None:
    """Rewrite the baseline. A changed contract under an unchanged SCHEMA_VERSION is what the baseline exists to refuse,
    unless ``unreleased``: the recorded SCHEMA_VERSION is the one the next release introduces, which no published release
    carries (module docstring; refused when it is a published one, ``published_release_problem``)."""
    if unreleased and (problem := published_release_problem()):
        raise SystemExit(f"--unreleased refused: {problem}; a published release's contracts never change in place: bump "
                         "SCHEMA_VERSION in _version.py and run --freeze")
    hashes = freeze_hashes()
    if FREEZE_FILE.exists() and not unreleased:
        baseline = json.loads(FREEZE_FILE.read_text(encoding="utf-8"))
        changed = sorted(k for k in set(hashes) | set(baseline["hashes"]) if hashes.get(k) != baseline["hashes"].get(k))
        if changed and baseline["schema_version"] == SCHEMA_VERSION:
            raise SystemExit(f"frozen contract(s) {changed} changed: bump SCHEMA_VERSION in _version.py before --freeze (or, "
                             "while this SCHEMA_VERSION is in no published release, --freeze --unreleased)")
    payload = {"schema_version": SCHEMA_VERSION, "hashes": hashes}
    FREEZE_FILE.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print("wrote contract_freeze.json for schema", SCHEMA_VERSION)


def write_mechanical_fixtures() -> None:
    """The synthetic public mechanical catalog fixtures (``mechanical_fixture.py``; osc1 canonical bytes)."""
    from mechanical_fixture import write_fixtures

    for path in write_fixtures():
        print("wrote", path.relative_to(HERE).as_posix())


def main() -> None:
    if "mechanical" in sys.argv[1:]:
        write_mechanical_fixtures()
        return
    REQ_DIR.mkdir(parents=True, exist_ok=True)
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    for name, factory in FIXTURES.items():
        request = factory()
        (REQ_DIR / f"{name}.json").write_text(canonical_request_text(request), encoding="utf-8", newline="\n")
        (SNAP_DIR / f"{name}.json").write_text(snapshot_text(request), encoding="utf-8", newline="\n")
        print("wrote", name)
    if "--freeze" in sys.argv:
        write_freeze_baseline(unreleased="--unreleased" in sys.argv)


if __name__ == "__main__":
    main()
