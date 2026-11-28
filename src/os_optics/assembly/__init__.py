"""Local layout and collision check of a resolved assembly (Assembly Preview Package).

After a commercial submission, OpenSci resolves the assembly and sends this device its Assembly Preview Package
(``manifest.json``, ``scene.json``, ``geometry/<part_ref>.glb``: OpenSci product identities, poses, support tree, mates,
breadboard and part geometry); a local assembly plan generates the same package on the device itself
(``os_optics.assembly_plan``, binding LOCAL_PLAN). This module checks that assembly on the device::

    from os_optics.assembly import load_assembly_preview, validate_assembly_layout

    package = load_assembly_preview("assembly-preview.zip")    # verify FIRST: manifest, digests, schema, GLBs
    result = validate_assembly_layout(package)                  # AssemblyLocalValidationV1
    result.status                   # PASS / WARNING / FAIL / UNKNOWN / OUT_OF_MODEL_SCOPE (UNKNOWN is never a pass)
    result.checks                   # every check, in a fixed order, with its status, basis and evaluated count
    result.issues                   # code, severity, part_refs, metrics and a fixed message
    result.parts                    # per-part status, for colouring the same package's parts in a viewer
    result.summary                  # what was checked, on which geometry, and whether interference was found
    result.tolerances               # every threshold the checks used (a caller setting: tolerances=, tolerances_with)
    result.payload()                # the JSON document; result.result_hash is its osc1 digest

``load_assembly_preview`` accepts a path (the single-file ``.zip`` archive, or a directory holding the package
files), the archive ``bytes``, or a ``{path: bytes}`` file map -- e.g. the bytes of an opened delivery.
A package that does not verify raises ``AssemblyPreviewPackageError`` (``.problems`` lists why) and is never
checked. Bytes are verified where they enter (a path, archive bytes, a file map); an ``AssemblyPreviewPackageV1`` is
the verifier's own output and is used as it is (its constructor is not a verification path).

Checks: PACKAGE_INTEGRITY, SCENE_TRANSFORMS, PART_GEOMETRY, SUPPORT_STRUCTURE, SUPPORT_ATTACHMENT (mates close, no
floating part), BOARD_CONTAINMENT (OpenSci-supplied hardware on the resolved breadboard; your own light source is
placed by your design), BOARD_FIXATION, EXPECTED_ENGAGEMENT (a part and its support overlap only inside their
interface's engagement allowance), PART_COLLISION (parts not expected to touch do not interpenetrate beyond the
collision thresholds) and BEAM_AXIS_CLEARANCE (the nominal beam centre line passes through no mechanical part).

Shell: ``os-optics assembly validate assembly-preview.zip --out assembly-validation.json`` and
``os-optics assembly verify assembly-preview.zip``.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from .._vendor.shared_contracts.assembly_preview import (
    AssemblyCheckKindV1,
    AssemblyIssueCodeV1,
    AssemblyLocalValidationV1,
    AssemblyPreviewPackageError,
    AssemblyPreviewPackageV1,
    AssemblyPreviewSceneV1,
    CheckerTolerancesV1,
    InterferenceV1,
    binding_pin_problems,
    json_file_bytes,
    load_assembly_preview_package,
    read_package_archive,
)
from .checks import AssemblyChecker
from .result import build_result
from .tolerances import ASSEMBLY_CHECKER_VERSION, DEFAULT_TOLERANCES, TOLERANCE_NAMES, tolerance_argument, tolerances_with

PackageSource = str | Path | bytes | bytearray | Mapping[str, bytes] | AssemblyPreviewPackageV1


def _files_from_directory(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def package_files(source: PackageSource) -> dict[str, bytes]:
    """The raw package files of any accepted source (nothing is verified here)."""

    if isinstance(source, AssemblyPreviewPackageV1):
        return dict(source.files)
    if isinstance(source, (bytes, bytearray)):
        return read_package_archive(bytes(source))
    if isinstance(source, Mapping):
        return {str(path): data for path, data in source.items()}
    path = Path(source)
    if path.is_dir():
        return _files_from_directory(path)
    if path.is_file():
        return read_package_archive(path.read_bytes())
    raise AssemblyPreviewPackageError((f"no package file or directory at {path.as_posix()!r}",))


def load_assembly_preview(source: PackageSource, *, expected_project_hash: str | None = None,
                          expected_resolution_hash: str | None = None,
                          expected_content_hash: str | None = None,
                          expected_plan_digest: str | None = None) -> AssemblyPreviewPackageV1:
    """Read and VERIFY an Assembly Preview Package with the canonical contract verifier.

    Verifies the file set, sizes and SHA-256 digests against the manifest, the manifest's content hash, both JSON
    documents against their schemas and canonical bytes, scene <-> manifest cross references, and every part GLB.
    ``expected_*`` optionally pins the package to a Project hash, a resolution hash, a content hash or -- a package
    generated on this device from a local assembly plan (LOCAL_PLAN binding) -- a plan digest. Raises
    ``AssemblyPreviewPackageError`` listing every problem.

    A verified ``AssemblyPreviewPackageV1`` is returned unchanged after its manifest is compared with ``expected_*``.
    """

    if isinstance(source, AssemblyPreviewPackageV1):
        problems = binding_pin_problems(source.manifest, expected_project_hash=expected_project_hash,
                                        expected_resolution_hash=expected_resolution_hash,
                                        expected_content_hash=expected_content_hash, expected_plan_digest=expected_plan_digest)
        if problems:
            raise AssemblyPreviewPackageError(tuple(problems))
        return source
    return load_assembly_preview_package(package_files(source), expected_project_hash=expected_project_hash,
                                         expected_resolution_hash=expected_resolution_hash,
                                         expected_content_hash=expected_content_hash, expected_plan_digest=expected_plan_digest)


def validate_assembly_layout(package: PackageSource, *, tolerances: CheckerTolerancesV1 | None = None) -> AssemblyLocalValidationV1:
    """Run the local layout and collision checks on a package and return the structured result.

    Package bytes are verified first (``load_assembly_preview``; a verified package object is used as it is); an
    unverifiable package raises ``AssemblyPreviewPackageError`` and produces no result. ``tolerances`` defaults to
    ``DEFAULT_TOLERANCES``.
    """

    verified = load_assembly_preview(package)
    thresholds = tolerances or DEFAULT_TOLERANCES
    outcomes = AssemblyChecker(verified.scene, dict(verified.files), thresholds).run()
    return build_result(verified, outcomes, thresholds)


def result_file_bytes(result: AssemblyLocalValidationV1) -> bytes:
    """Deterministic JSON file bytes of a result (sorted keys, 2-space indent, LF, trailing newline)."""

    return json_file_bytes(result.payload())


__all__ = [
    "ASSEMBLY_CHECKER_VERSION",
    "DEFAULT_TOLERANCES",
    "AssemblyCheckKindV1",
    "AssemblyIssueCodeV1",
    "AssemblyLocalValidationV1",
    "AssemblyPreviewPackageError",
    "AssemblyPreviewPackageV1",
    "AssemblyPreviewSceneV1",
    "CheckerTolerancesV1",
    "InterferenceV1",
    "TOLERANCE_NAMES",
    "load_assembly_preview",
    "package_files",
    "result_file_bytes",
    "tolerance_argument",
    "tolerances_with",
    "validate_assembly_layout",
]
