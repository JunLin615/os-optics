"""Assembly Preview Package: file layout, deterministic bytes, archive form and verification.

One package is a flat set of files::

    manifest.json              identity, binding, usage terms, file digests, package_content_hash
    scene.json                 AssemblyPreviewSceneV1
    geometry/<part_ref>.glb    one glTF 2.0 binary per resolved part (PART_LOCAL, metres, +Z up)

Everything is plain bytes in memory (``Mapping[str, bytes]``); callers decide whether to hold it in
memory, write a directory, or use the single-file archive (``package_archive_bytes``, a ZIP with fixed
entry metadata and no compression, so the same package always gives the same archive bytes).

``verify_assembly_preview_package`` is what a client (or the server before sending) runs on received
bytes: file set, sizes, digests, content hash, schema validity, scene <-> manifest cross-references, and a
structural + string allow-list check of every GLB's JSON chunk. It returns the list of problems (empty =
valid) and never raises on bad input.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import struct
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from pydantic import ValidationError

from .manifest import (
    GLB_MEDIA_TYPE,
    JSON_MEDIA_TYPE,
    MANIFEST_PATH,
    MAX_PACKAGE_FILES,
    SCENE_PATH,
    AssemblyPreviewManifestV1,
    PackageBindingV1,
    PackageFileV1,
    content_hash_of,
    manifest_schema_version_for,
    usage_terms_for,
)
from .scene import AssemblyPreviewSceneV1, FinishV1


GLB_GENERATOR = "opensci assembly-preview geometry 1"
GLB_MAGIC = b"glTF"
GLB_VERSION = 2
GLB_JSON_CHUNK = 0x4E4F534A
GLB_BIN_CHUNK = 0x004E4942
GLB_HEADER_BYTES = 12
GLB_CHUNK_HEADER_BYTES = 8
GLB_MATERIAL_NAMES = frozenset(f.value.lower() for f in FinishV1)
GLB_FIXED_STRINGS = frozenset({"2.0", GLB_GENERATOR, "OPAQUE", "BLEND", "SCALAR", "VEC3"}) | GLB_MATERIAL_NAMES
GLB_FORBIDDEN_KEYS = frozenset({"extras", "extensions", "extensionsUsed", "extensionsRequired", "uri", "images", "textures",
                                "samplers", "matrix", "translation", "rotation", "scale", "cameras", "animations", "skins"})
GLB_POSITION_TOLERANCE_M = 1e-6

MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_PACKAGE_BYTES = 64 * 1024 * 1024
ARCHIVE_DATE_TIME = (1980, 1, 1, 0, 0, 0)
ARCHIVE_UNIX_SYSTEM = 3
ARCHIVE_FILE_MODE = 0o100644
_PACKAGE_NAME = re.compile(r"^(manifest\.json|scene\.json|geometry/[a-z][a-z0-9]*(-[a-z0-9]+)*\.glb)$")


class AssemblyPreviewPackageError(ValueError):
    """Raised when package bytes do not form a valid Assembly Preview Package; ``problems`` lists why."""

    def __init__(self, problems: tuple[str, ...]):
        super().__init__("invalid assembly preview package: " + "; ".join(problems))
        self.problems = problems


def json_file_bytes(payload: Any) -> bytes:
    """Deterministic, human-readable JSON file bytes (sorted keys, 2-space indent, LF, trailing newline)."""

    return (json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class AssemblyPreviewPackageV1:
    """A verified package held in memory: the raw files plus their parsed manifest and scene."""

    files: Mapping[str, bytes]
    manifest: AssemblyPreviewManifestV1
    scene: AssemblyPreviewSceneV1

    @property
    def content_hash(self) -> str:
        return self.manifest.package_content_hash

    def archive_bytes(self) -> bytes:
        return package_archive_bytes(self.files)


def build_assembly_preview_package(
    *,
    scene: AssemblyPreviewSceneV1,
    geometry: Mapping[str, bytes],
    binding: PackageBindingV1,
    package_revision: int = 1,
) -> AssemblyPreviewPackageV1:
    """Assemble scene + geometry files into a package, compute the manifest, and verify the result. The manifest
    version and usage terms follow from ``binding``: 3.0 / ``UsageTermsV1`` for the commercial binding, 3.1 /
    ``LocalUsageTermsV1`` for the LOCAL_PLAN binding."""

    files: dict[str, bytes] = {SCENE_PATH: json_file_bytes(scene.payload())}
    for path in sorted(geometry):
        files[path] = bytes(geometry[path])
    entries = tuple(
        PackageFileV1(path=path, media_type=JSON_MEDIA_TYPE if path.endswith(".json") else GLB_MEDIA_TYPE,
                      size_bytes=len(data), sha256=sha256_hex(data))
        for path, data in sorted(files.items())
    )
    content = {
        "schema": "opensci.assembly_preview.manifest",
        "schema_version": manifest_schema_version_for(binding),
        "package_revision": package_revision,
        "usage": usage_terms_for(binding).model_dump(mode="json", by_alias=True, exclude_none=True),
        "binding": binding.model_dump(mode="json", by_alias=True, exclude_none=True),
        "files": [e.model_dump(mode="json", by_alias=True, exclude_none=True) for e in entries],
    }
    manifest = AssemblyPreviewManifestV1.model_validate({**content, "package_content_hash": content_hash_of(content)})
    files[MANIFEST_PATH] = json_file_bytes(manifest.payload())
    return load_assembly_preview_package(files)


def load_assembly_preview_package(files: Mapping[str, bytes], **expected: str | None) -> AssemblyPreviewPackageV1:
    """Verify ``files`` and return the parsed package, or raise ``AssemblyPreviewPackageError``."""

    problems = verify_assembly_preview_package(files, **expected)
    if problems:
        raise AssemblyPreviewPackageError(problems)
    manifest = AssemblyPreviewManifestV1.model_validate_json(files[MANIFEST_PATH])
    scene = AssemblyPreviewSceneV1.model_validate_json(files[SCENE_PATH])
    ordered = {path: bytes(files[path]) for path in sorted(files)}
    return AssemblyPreviewPackageV1(files=MappingProxyType(ordered), manifest=manifest, scene=scene)


def verify_assembly_preview_package(
    files: Mapping[str, bytes],
    *,
    expected_project_hash: str | None = None,
    expected_resolution_hash: str | None = None,
    expected_content_hash: str | None = None,
    expected_plan_digest: str | None = None,
) -> tuple[str, ...]:
    """Every problem found in a package (empty tuple = valid). Never raises on malformed input. ``expected_*`` pin the
    package to a Project hash, a resolution hash (commercial binding), a content hash or a plan digest (LOCAL_PLAN
    binding); a pin the binding cannot meet (a resolution hash of a local package, a plan digest of a commercial one) is
    a problem."""

    problems: list[str] = []
    if len(files) > MAX_PACKAGE_FILES:
        return (f"too many files ({len(files)} > {MAX_PACKAGE_FILES})",)
    total = 0
    for path, data in files.items():
        if not isinstance(data, (bytes, bytearray)):
            return (f"{path!r}: file content must be bytes",)
        total += len(data)
        if len(data) > MAX_FILE_BYTES:
            problems.append(f"{path!r}: file exceeds {MAX_FILE_BYTES} bytes")
    if total > MAX_PACKAGE_BYTES:
        problems.append(f"package exceeds {MAX_PACKAGE_BYTES} bytes")
    if MANIFEST_PATH not in files:
        return tuple(problems + ["manifest.json is missing"])
    try:
        manifest = AssemblyPreviewManifestV1.model_validate_json(files[MANIFEST_PATH])
    except (ValidationError, ValueError) as exc:
        return tuple(problems + [f"manifest.json is not a valid manifest: {_first_error(exc)}"])
    if files[MANIFEST_PATH] != json_file_bytes(manifest.payload()):
        problems.append("manifest.json is not in canonical file form")

    listed = {entry.path: entry for entry in manifest.files}
    present = set(files) - {MANIFEST_PATH}
    for path in sorted(present - set(listed)):
        problems.append(f"{path!r} is in the package but not in the manifest")
    for path in sorted(set(listed) - present):
        problems.append(f"{path!r} is listed in the manifest but missing")
    for path in sorted(present & set(listed)):
        data, entry = files[path], listed[path]
        if len(data) != entry.size_bytes or sha256_hex(bytes(data)) != entry.sha256:
            problems.append(f"{path!r}: size or SHA-256 does not match the manifest")

    problems.extend(binding_pin_problems(manifest, expected_project_hash=expected_project_hash,
                                         expected_resolution_hash=expected_resolution_hash,
                                         expected_content_hash=expected_content_hash,
                                         expected_plan_digest=expected_plan_digest))

    if SCENE_PATH not in files:
        return tuple(problems)
    try:
        scene = AssemblyPreviewSceneV1.model_validate_json(files[SCENE_PATH])
    except (ValidationError, ValueError) as exc:
        return tuple(problems + [f"scene.json is not a valid scene: {_first_error(exc)}"])
    if files[SCENE_PATH] != json_file_bytes(scene.payload()):
        problems.append("scene.json is not in canonical file form")

    geometry_paths = {part.geometry.asset_path: part for part in scene.parts}
    for path in sorted(set(listed) - {SCENE_PATH} - set(geometry_paths)):
        problems.append(f"{path!r} is not the geometry of any scene part")
    for path, part in sorted(geometry_paths.items()):
        if path not in files:
            if path in listed:
                continue  # already reported as missing
            problems.append(f"part {part.part_ref!r}: geometry {path!r} is not in the package")
            continue
        problems.extend(f"{path!r}: {p}" for p in glb_problems(bytes(files[path]), part.part_ref,
                                                              part.geometry.triangle_count,
                                                              part.geometry.bounds_min_mm, part.geometry.bounds_max_mm))
    return tuple(problems)


def binding_pin_problems(manifest: AssemblyPreviewManifestV1, *, expected_project_hash: str | None = None,
                         expected_resolution_hash: str | None = None, expected_content_hash: str | None = None,
                         expected_plan_digest: str | None = None) -> list[str]:
    """The manifest values that differ from the caller's pins (a pin the binding has no value for differs)."""

    binding = manifest.binding
    resolution = binding.resolution.resolution_hash.digest if binding.resolution is not None else None
    plan = binding.local_plan.plan_digest.digest if binding.local_plan is not None else None
    return [f"{label} {got} is not the expected {want}" for label, got, want in (
        ("project hash", binding.project.project_hash.digest, expected_project_hash),
        ("resolution hash", resolution, expected_resolution_hash),
        ("package content hash", manifest.package_content_hash, expected_content_hash),
        ("plan digest", plan, expected_plan_digest),
    ) if want is not None and got != want]


def _first_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        err = exc.errors()[0]
        return f"{'.'.join(str(p) for p in err.get('loc', ()))}: {err.get('msg')}"
    return str(exc).splitlines()[0]


# ------------------------------------------------------------------------------------------------- GLB


def parse_glb(data: bytes) -> tuple[dict[str, Any], bytes]:
    """Split a GLB into (JSON document, BIN chunk). Raises ``ValueError`` on a malformed container."""

    if len(data) < GLB_HEADER_BYTES + GLB_CHUNK_HEADER_BYTES:
        raise ValueError("too short for a GLB")
    magic, version, length = struct.unpack_from("<4sII", data, 0)
    if magic != GLB_MAGIC or version != GLB_VERSION or length != len(data):
        raise ValueError("bad GLB header (magic, version 2 or length)")
    offset, chunks = GLB_HEADER_BYTES, []
    while offset < len(data):
        if offset + GLB_CHUNK_HEADER_BYTES > len(data):
            raise ValueError("truncated GLB chunk header")
        size, kind = struct.unpack_from("<II", data, offset)
        start = offset + GLB_CHUNK_HEADER_BYTES
        if start + size > len(data) or size % 4:
            raise ValueError("bad GLB chunk length")
        chunks.append((kind, data[start:start + size]))
        offset = start + size
    if not chunks or chunks[0][0] != GLB_JSON_CHUNK or any(k != GLB_BIN_CHUNK for k, _ in chunks[1:]) or len(chunks) > 2:
        raise ValueError("a GLB must hold one JSON chunk and at most one BIN chunk")
    document = json.loads(chunks[0][1].decode("utf-8").rstrip(" "))
    if not isinstance(document, dict):
        raise ValueError("GLB JSON chunk is not an object")
    return document, chunks[1][1] if len(chunks) > 1 else b""


def _walk(value: Any, keys: set[str], strings: set[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            keys.add(key)
            _walk(item, keys, strings)
    elif isinstance(value, list):
        for item in value:
            _walk(item, keys, strings)
    elif isinstance(value, str):
        strings.add(value)


def glb_problems(data: bytes, part_ref: str, triangle_count: int, bounds_min_mm: tuple[float, float, float],
                 bounds_max_mm: tuple[float, float, float]) -> list[str]:
    """Structural and content allow-list check of one part's GLB (see ``PartGeometryV1``)."""

    try:
        document, binary = parse_glb(data)
    except (ValueError, UnicodeDecodeError, struct.error) as exc:
        return [str(exc)]
    keys: set[str] = set()
    strings: set[str] = set()
    _walk(document, keys, strings)
    problems = []
    if bad_keys := sorted(keys & GLB_FORBIDDEN_KEYS):
        problems.append(f"GLB JSON uses disallowed key(s) {bad_keys}")
    if bad_strings := sorted(strings - GLB_FIXED_STRINGS - {part_ref}):
        problems.append(f"GLB JSON holds {len(bad_strings)} string value(s) outside the allow-list")
    try:
        if document["asset"] != {"version": "2.0", "generator": GLB_GENERATOR}:
            problems.append("GLB asset block is not the assembly-preview generator block")
        nodes, meshes, buffers = document["nodes"], document["meshes"], document["buffers"]
        if len(nodes) != 1 or nodes[0] != {"mesh": 0, "name": part_ref} or len(meshes) != 1 or meshes[0].get("name") != part_ref:
            problems.append("a part GLB holds exactly one untransformed node and mesh named after the part")
        length = buffers[0].get("byteLength") if len(buffers) == 1 else None
        if not isinstance(length, int) or not 0 <= len(binary) - length < 4:  # the BIN chunk is padded to 4 bytes
            problems.append("GLB buffer does not match its BIN chunk")
        accessors = document["accessors"]
        triangles, lo, hi = 0, [float("inf")] * 3, [float("-inf")] * 3
        for primitive in meshes[0]["primitives"]:
            if primitive.get("mode", 4) != 4:
                problems.append("GLB primitives must be triangle lists")
            triangles += accessors[primitive["indices"]]["count"] // 3
            position = accessors[primitive["attributes"]["POSITION"]]
            lo = [min(a, b) for a, b in zip(lo, position["min"])]
            hi = [max(a, b) for a, b in zip(hi, position["max"])]
        if triangles != triangle_count:
            problems.append(f"GLB has {triangles} triangles, the scene declares {triangle_count}")
        for got, want_mm in ((lo, bounds_min_mm), (hi, bounds_max_mm)):
            if any(abs(g - w / 1000.0) > GLB_POSITION_TOLERANCE_M for g, w in zip(got, want_mm)):
                problems.append("GLB position bounds do not match the scene geometry bounds")
                break
    except (KeyError, IndexError, TypeError) as exc:
        problems.append(f"GLB JSON is incomplete: {exc!r}")
    return problems


# --------------------------------------------------------------------------------------------- archive


def package_archive_bytes(files: Mapping[str, bytes]) -> bytes:
    """Deterministic single-file form: a stored (uncompressed) ZIP, manifest first, then sorted paths."""

    buffer = io.BytesIO()
    order = [MANIFEST_PATH] + sorted(p for p in files if p != MANIFEST_PATH)
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        for path in order:
            info = zipfile.ZipInfo(path, date_time=ARCHIVE_DATE_TIME)
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = ARCHIVE_UNIX_SYSTEM
            info.external_attr = ARCHIVE_FILE_MODE << 16
            archive.writestr(info, bytes(files[path]))
    return buffer.getvalue()


def read_package_archive(data: bytes) -> dict[str, bytes]:
    """Read the archive form back into files. Refuses any name that is not a package path (so no absolute,
    parent or backslash path), duplicates, directories, compression and oversize."""

    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise AssemblyPreviewPackageError((f"not a package archive: {exc}",)) from exc
    files: dict[str, bytes] = {}
    total = 0
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_PACKAGE_FILES:
            raise AssemblyPreviewPackageError((f"too many archive entries ({len(infos)})",))
        for info in infos:
            name = info.filename
            if name in files or not _PACKAGE_NAME.match(name) or info.is_dir():
                raise AssemblyPreviewPackageError((f"unsafe or duplicate archive entry {name!r}",))
            if info.compress_type != zipfile.ZIP_STORED or info.file_size > MAX_FILE_BYTES:
                raise AssemblyPreviewPackageError((f"archive entry {name!r} is compressed or too large",))
            total += info.file_size
            if total > MAX_PACKAGE_BYTES:
                raise AssemblyPreviewPackageError(("archive exceeds the package size limit",))
            try:
                files[name] = archive.read(info)
            except (zipfile.BadZipFile, ValueError, EOFError) as exc:  # CRC mismatch, truncated or inconsistent entry
                raise AssemblyPreviewPackageError((f"archive entry {name!r} is corrupt: {exc}",)) from exc
    return files
