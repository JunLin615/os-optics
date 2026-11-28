"""Leak scan of the local assembly check: nothing supplier-, private-catalog- or pipeline-internal ships or is emitted.

Scanned for the forbidden tokens below (test-side only; case-insensitive, and for tokens of 5+ alphanumerics also
separator-blind). The tokens are synthetic stand-ins for supplier, part and pipeline identities plus the field names of
private records; the scan for the real private identities runs where those are kept:

* the built wheel: every file name and file content (the assembly checker, the local assembly drawing generator and
  their vendored contracts strictly, the rest of the wheel against an explicit list of known, reviewed pre-existing
  hits); the assembly drawing generator additionally carries no forbidden token hashed or as a regex shape (its
  runtime output check is a positive allow-list with no supplier vocabulary), and no test file is in the wheel;
* the reference result JSON (and its SKUs, part refs and 64-hex values);
* the problems / error envelopes / CLI error JSON of corrupted packages, and the result of a tampered-but-re-signed one;
* the reference package archive itself (names, JSON, GLB JSON chunks, raw bytes).

Builds the wheel (a few seconds, no install), so it is not marked ``install``.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from os_optics._vendor.shared_contracts.assembly_preview import (
    AssemblyPreviewManifestV1,
    AssemblyPreviewSceneV1,
    content_hash_of,
    json_file_bytes,
    package_archive_bytes,
    parse_glb,
)
from os_optics.assembly import AssemblyPreviewPackageError, package_files, result_file_bytes, validate_assembly_layout
from os_optics.cli import main as cli_main
from os_optics.local import json_api

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/assembly_preview/reference.assembly-preview.zip"

FORBIDDEN_TOKENS = (
    "ACME-OPTICS", "XQ-412B", "ZK-75A", "QB-3", "OS-DEMO", "acme:", "product_show", ".STP", ".stl", "supplier_sku",
    "candidate_key", "placement_key",
)
ASSEMBLY_WHEEL_PREFIXES = ("os_optics/assembly/", "os_optics/_vendor/shared_contracts/assembly_preview/",
                           "os_optics/assembly_drawing/", "os_optics/_vendor/shared_contracts/assembly_detail/",
                           "os_optics/assembly_plan/")
# Reviewed pre-existing hits elsewhere in the wheel (not assembly code). Any hit not listed here fails.
KNOWN_WHEEL_HITS = {
    ("os_optics/_vendor/shared_contracts/common/frozen_string_map.py", "candidate_key"),  # a loop variable name
}
HEX64 = re.compile(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])")


def _flat(text: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", text.lower())


def scan_texts(texts: dict[str, str], tokens=FORBIDDEN_TOKENS) -> list[tuple[str, str]]:
    """(location, token) hits. A location whose key contains ``raw`` is binary decoded as latin-1: only tokens of 5+
    characters, matched literally (case-insensitive). Every other location: literal, and separator-blind for tokens of
    5+ alphanumerics."""

    hits = []
    for where, text in texts.items():
        lowered, flat = text.lower(), _flat(text)
        for token in tokens:
            needle = token.lower()
            if "raw" in where and len(needle) < 5:
                continue
            reduced = _flat(needle)
            if needle in lowered or ("raw" not in where and len(reduced) >= 5 and reduced in flat):
                hits.append((where, token))
    return hits


def package_texts(files: dict[str, bytes]) -> dict[str, str]:
    """The texts of a package, as the project-api scan reads them."""

    archive = package_archive_bytes(files)
    texts = {f"name:{n}": n for n in zipfile.ZipFile(io.BytesIO(archive)).namelist()}
    for path, data in files.items():
        if path.endswith(".glb"):
            texts[f"glb-json:{path}"] = json.dumps(parse_glb(data)[0])
            texts[f"glb-raw:{path}"] = data.decode("latin-1")
        else:
            texts[f"text:{path}"] = data.decode("utf-8")
    texts["archive-raw"] = archive.decode("latin-1")
    return texts


def test_the_scan_itself_is_case_insensitive_and_separator_blind():
    assert {t for _, t in scan_texts({"text:x": "made by a.c.m.e optics, acme: XQ"})} == {"ACME-OPTICS", "acme:"}
    assert scan_texts({"text:x": "Zk 75 a"}) == [("text:x", "ZK-75A")]
    assert scan_texts({"text:x": "Placement-Key"}) == [("text:x", "placement_key")]
    assert scan_texts({"text:x": "qb-3"}) == [("text:x", "QB-3")]
    assert scan_texts({"text:x": "qb 3"}) == []  # short tokens are matched literally only
    assert scan_texts({"raw:x": "qb-3 PLACEMENT_KEY"}) == [("raw:x", "placement_key")]  # short tokens skipped in raw
    assert scan_texts({"text:x": "OS-OPT-ACHROMAT OS-MECH-POST b01-optic"}) == []


# ---- (a) the wheel ----------------------------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def wheel_texts(tmp_path_factory) -> dict[str, dict[str, str]]:
    out = tmp_path_factory.mktemp("privacy_dist")
    proc = subprocess.run([sys.executable, "-m", "build", "--wheel", "--no-isolation", "--outdir", str(out), str(ROOT)],
                          capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    (wheel,) = out.glob("os_optics-*.whl")
    texts: dict[str, dict[str, str]] = {}
    with zipfile.ZipFile(wheel) as z:
        for name in z.namelist():
            data = z.read(name)
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                text = None
            if name.endswith(".dist-info/RECORD"):  # the file list; the base64 digests are noise that changes with every edit
                text = "\n".join(line.split(",", 1)[0] for line in text.splitlines())
            entry = {f"name:{name}": name}
            entry[f"text:{name}" if text is not None else f"raw:{name}"] = text if text is not None else data.decode("latin-1")
            texts[name] = entry
    return texts


def test_the_wheel_ships_the_assembly_checker_and_contract(wheel_texts):
    for prefix in ASSEMBLY_WHEEL_PREFIXES:
        assert any(name.startswith(prefix) and name.endswith(".py") for name in wheel_texts), prefix


def test_assembly_checker_and_vendored_contract_in_the_wheel_carry_no_forbidden_token(wheel_texts):
    scoped = {name: entry for name, entry in wheel_texts.items() if name.startswith(ASSEMBLY_WHEEL_PREFIXES)}
    assert len(scoped) >= 10
    hits = [(name, token) for name, entry in scoped.items() for _, token in scan_texts(entry)]
    assert hits == [], hits


def test_the_drawing_generator_in_the_wheel_carries_no_supplier_vocabulary_in_any_form(wheel_texts):
    from drawing_helpers import encoded_vocabulary_hits

    drawing = {name: entry[f"text:{name}"] for name, entry in wheel_texts.items()
               if name.startswith("os_optics/assembly_drawing/") and name.endswith(".py")}
    assert len(drawing) >= 10
    # the output text gate is a build-time test helper (tests/drawing_text_check.py), not shipped
    assert "os_optics/assembly_drawing/privacy.py" not in drawing
    assert {name: encoded_vocabulary_hits(text) for name, text in drawing.items()} == {name: [] for name in drawing}


def test_the_wheel_ships_no_test_file(wheel_texts):
    assert not [n for n in wheel_texts if n.startswith("tests/") or "/tests/" in n or n.rsplit("/", 1)[-1].startswith(("test_", "conftest"))
                or n.endswith(("drawing_helpers.py", "assembly_helpers.py"))]


def test_every_file_of_the_wheel_is_clean_but_for_known_reviewed_hits(wheel_texts):
    hits = {(name, token) for name, entry in wheel_texts.items() for _, token in scan_texts(entry)}
    assert hits <= KNOWN_WHEEL_HITS, sorted(hits - KNOWN_WHEEL_HITS)
    assert not any(where.startswith("raw:") for entry in wheel_texts.values() for where in entry)  # pure text wheel


# ---- (b) the reference result -----------------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def reference_files() -> dict[str, bytes]:
    return package_files(FIXTURE)


@pytest.fixture(scope="module")
def reference_result():
    return validate_assembly_layout(FIXTURE)


def test_the_reference_result_carries_no_forbidden_token(reference_result):
    text = result_file_bytes(reference_result).decode("utf-8")
    assert scan_texts({"text:result": text}) == []


def test_the_reference_result_names_only_opensci_identities_and_package_part_refs(reference_result):
    payload = reference_result.payload()
    text = json.dumps(payload)
    skus = set(re.findall(r"\bOS-[A-Z0-9]+(?:-[A-Z0-9]+)*", text))
    assert skus and all(sku.startswith(("OS-OPT-", "OS-MECH-")) for sku in skus), skus
    assert {p["opensci_sku"] for p in payload["parts"]} == skus
    part_refs = [p["part_ref"] for p in payload["parts"]]
    assert [ref[:3] for ref in part_refs] == ["b01", "b02", "b03", "b04", "b05"], part_refs
    assert all(re.fullmatch(r"b0[1-5](-[a-z0-9]+)+", ref) for ref in part_refs), part_refs
    assert [p["commercial_line_ref"] for p in payload["parts"]] == ["B01", "B02", "B03", "B04", "B05"]
    for issue in payload["issues"]:
        assert set(issue["part_refs"]) <= set(part_refs), issue


def test_the_reference_result_holds_no_64_hex_value_but_bindings_and_its_own_hash(reference_result, reference_files):
    manifest = json.loads(reference_files["manifest.json"])
    binding = manifest["binding"]
    allowed = {
        manifest["package_content_hash"],
        binding["project"]["project_hash"]["digest"],
        binding["resolution"]["resolution_hash"]["digest"],
        binding["local_validation_hash"]["digest"],
        reference_result.result_hash,
    }
    text = result_file_bytes(reference_result).decode("utf-8")
    found = set(HEX64.findall(text))
    assert found <= allowed, found - allowed
    assert reference_result.package.package_content_hash in found and reference_result.result_hash in found
    # the engine / checker identity is not a 64-hex value (version + 16 hex of the sources)
    assert re.fullmatch(r"\d+\.\d+\.\d+[0-9a-z.]*\+[0-9a-f]{16}", reference_result.checker.engine_build_id)


# ---- (c) errors of corrupted packages, and the result of a tampered-but-re-signed one -----------------------------------------
def _flip(data: bytes, at: int) -> bytes:
    flipped = bytearray(data)
    flipped[at] ^= 0x01
    return bytes(flipped)


def _resigned(files: dict[str, bytes], scene_payload: dict) -> dict[str, bytes]:
    """A package whose scene was edited and whose manifest was re-signed: it verifies, so it is checked."""

    scene_bytes = json_file_bytes(AssemblyPreviewSceneV1.model_validate(scene_payload).payload())
    manifest = json.loads(files["manifest.json"])
    for entry in manifest["files"]:
        if entry["path"] == "scene.json":
            entry["sha256"], entry["size_bytes"] = hashlib.sha256(scene_bytes).hexdigest(), len(scene_bytes)
    del manifest["package_content_hash"]
    manifest["package_content_hash"] = content_hash_of(manifest)
    manifest_bytes = json_file_bytes(AssemblyPreviewManifestV1.model_validate(manifest).payload())
    return {**files, "scene.json": scene_bytes, "manifest.json": manifest_bytes}


def corrupted_packages(files: dict[str, bytes]) -> dict[str, dict[str, bytes] | bytes]:
    glb = "geometry/b02-optic-mount.glb"
    scene = files["scene.json"]
    return {
        "flipped_glb_byte": {**files, glb: _flip(files[glb], len(files[glb]) // 2)},
        "flipped_glb_header": {**files, glb: _flip(files[glb], 1)},
        "flipped_scene_byte": {**files, "scene.json": _flip(scene, len(scene) // 3)},
        "flipped_manifest_byte": {**files, "manifest.json": _flip(files["manifest.json"], 200)},
        "dropped_geometry": {k: v for k, v in files.items() if k != glb},
        "dropped_scene": {k: v for k, v in files.items() if k != "scene.json"},
        "dropped_manifest": {k: v for k, v in files.items() if k != "manifest.json"},
        "extra_file": {**files, "geometry/extra.glb": files[glb]},
        "tampered_scene_value": {**files, "scene.json": scene.replace(b'"thickness_mm": 12.7', b'"thickness_mm": 12.8')},
        "tampered_scene_not_json": {**files, "scene.json": b"{" + scene},
        "truncated_archive": package_archive_bytes(files)[:5000],
        "not_an_archive": b"PK\x03\x04 definitely not a package",
    }


def _problem_texts(name: str, package, tmp_path: Path, capsys) -> dict[str, str]:
    with pytest.raises(AssemblyPreviewPackageError) as caught:
        validate_assembly_layout(package)
    problems = list(caught.value.problems)
    assert problems, name
    envelope = json_api.validate_assembly_package(package)
    assert envelope["ok"] is False and envelope["error"]["code"] == "ASSEMBLY_PACKAGE_INVALID", name
    # the CLI reads the archive form (a file) or the directory form
    target = tmp_path / name
    if isinstance(package, bytes):
        target = target.with_suffix(".zip")
        target.write_bytes(package)
    else:
        for path, data in package.items():
            (target / path).parent.mkdir(parents=True, exist_ok=True)
            (target / path).write_bytes(data)
    capsys.readouterr()
    assert cli_main(["assembly", "validate", str(target), "--out", str(tmp_path / f"{name}.out.json")]) == 65, name
    cli_error = json.loads(capsys.readouterr().out)
    assert cli_error["status"] == "ERROR" and cli_error["error"]["code"] == "ASSEMBLY_PACKAGE_INVALID", name
    assert cli_main(["assembly", "verify", str(target)]) == 66, name
    cli_verify = json.loads(capsys.readouterr().out)
    assert cli_verify["status"] == "INVALID" and cli_verify["problems"], name
    for doc in (cli_error, cli_verify):
        doc.pop("package", None)  # the path the user typed (a temp dir), not package content
    return {"text:problems": "\n".join(problems), "text:envelope": json.dumps(envelope), "text:cli_error": json.dumps(cli_error),
            "text:cli_verify": json.dumps(cli_verify)}


def test_errors_of_corrupted_packages_carry_no_forbidden_token(reference_files, tmp_path, capsys):
    hits = []
    for name, package in corrupted_packages(reference_files).items():
        texts = _problem_texts(name, package, tmp_path, capsys)
        hits += [(name, where, token) for where, token in scan_texts(texts)]
        assert not HEX64.search(texts["text:problems"]) or name == "flipped_manifest_byte", (name, texts["text:problems"])
    assert hits == []


def test_an_archive_with_a_crc_error_is_reported_not_raised(tmp_path, capsys):
    archive = FIXTURE.read_bytes()
    info = zipfile.ZipFile(io.BytesIO(archive)).getinfo("scene.json")
    flipped = _flip(archive, info.header_offset + 30 + len(info.filename) + 100)  # inside the stored scene.json bytes
    texts = _problem_texts("crc_error", flipped, tmp_path, capsys)
    assert scan_texts(texts) == []


def test_a_tampered_but_resigned_package_is_checked_and_its_findings_carry_no_forbidden_token(reference_files):
    scene = json.loads(reference_files["scene.json"])
    part = next(p for p in scene["parts"] if p["part_ref"].startswith("b02-"))
    part["pose_in_world"]["position_mm"][2] += 40.0  # lift the lens mount off its post
    tampered = _resigned(reference_files, scene)
    result = validate_assembly_layout(tampered)
    assert result.status.value == "FAIL", result.status
    assert result.issues and any(i.metrics for i in result.issues)
    assert scan_texts({"text:result": result_file_bytes(result).decode("utf-8")}) == []
    envelope = json_api.validate_assembly_package(tampered)
    assert envelope["ok"] and scan_texts({"text:envelope": json.dumps(envelope["result"])}) == []


# ---- (d) the reference package archive itself ------------------------------------------------------------------------------------
def test_the_reference_package_archive_carries_no_forbidden_token(reference_files):
    assert package_archive_bytes(reference_files) == FIXTURE.read_bytes()  # the scan below reads exactly the fixture's bytes
    texts = package_texts(reference_files)
    assert {w for w in texts if w.startswith("glb-json:")} == {f"glb-json:{p}" for p in reference_files if p.endswith(".glb")}
    assert scan_texts(texts) == []
    names = zipfile.ZipFile(FIXTURE).namelist()
    assert names[0] == "manifest.json" and set(names) == set(reference_files)
    skus = set(re.findall(rb"\bOS-[A-Z0-9]+(?:-[A-Z0-9]+)*", reference_files["scene.json"]))
    assert skus and all(sku.startswith((b"OS-OPT-", b"OS-MECH-")) for sku in skus), skus
