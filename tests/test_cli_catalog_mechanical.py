"""``os-optics catalog mechanical list|show|status`` over the synthetic fixtures (no token, no network)."""

from __future__ import annotations

import contextlib
import io
import json
import socket

import pytest

from mechanical_fixture import OPENSCI_FIXTURE, OPENSCI_VERSION, USER_FIXTURE, fixture_path
from os_optics.catalog.mechanical import ENV_MECHANICAL_CATALOG
from os_optics.cli import main as cli_main

OPENSCI = str(fixture_path(OPENSCI_FIXTURE))
USER = str(fixture_path(USER_FIXTURE))


def _cli(*argv: str) -> tuple[int, dict]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli_main(list(argv))
    return code, json.loads(out.getvalue())


@pytest.fixture(autouse=True)
def offline(empty_catalog_store, monkeypatch):
    """An empty store, no mechanical environment variable, and any socket use fails the test."""

    monkeypatch.delenv(ENV_MECHANICAL_CATALOG, raising=False)

    def refuse(*args, **kwargs):
        raise AssertionError("a catalog mechanical command opened a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    return empty_catalog_store


def test_list_names_every_identity_with_its_key_facts_and_profile():
    code, listed = _cli("catalog", "mechanical", "list", "--mechanical", OPENSCI)
    assert code == 0 and listed["total"] == len(listed["identities"]) > 0
    assert [d["mechanical_version"] for d in listed["documents"]] == [OPENSCI_VERSION]
    skus = [row["opensci_sku"] for row in listed["identities"]]
    assert skus == sorted(skus) and all(s.startswith("OS-MECH-TEST-") for s in skus)
    kinds = {row["kind"] for row in listed["identities"]}
    assert kinds == {"POST", "POST_HOLDER", "MOUNT", "BREADBOARD"}
    mirror = next(r for r in listed["identities"] if r["opensci_sku"] == "OS-MECH-TEST-MIRROR-MOUNT-KIN-D25")
    assert mirror["profile"] == "CONSERVATIVE" and mirror["facts"]["mechanisms"] == ["TIP_TILT"]
    post = next(r for r in listed["identities"] if r["kind"] == "POST")
    assert set(post["facts"]) == {"diameter_mm", "length_mm", "top_thread"}


def test_list_filters_by_kind_and_held_optic_size_and_prints_full_documents_with_json():
    code, boards = _cli("catalog", "mechanical", "list", "--mechanical", OPENSCI, "--kind", "BREADBOARD")
    assert code == 0 and {r["kind"] for r in boards["identities"]} == {"BREADBOARD"} and boards["total"] == 4
    code, large = _cli("catalog", "mechanical", "list", "--mechanical", OPENSCI, "--holds-size", "50.8")
    assert code == 0 and [r["opensci_sku"] for r in large["identities"]] == ["OS-MECH-TEST-LENS-MOUNT-FIXED-D50"]
    code, full = _cli("catalog", "mechanical", "list", "--mechanical", OPENSCI, "--kind", "POST", "--json")
    assert code == 0 and all("visual" in row and "basis" in row for row in full["identities"])


def test_show_prints_one_identity_and_names_an_unknown_one():
    code, shown = _cli("catalog", "mechanical", "show", "OS-MECH-TEST-ROTATION-MOUNT-D25", "--mechanical", OPENSCI)
    assert code == 0 and shown["identity"]["mount_kind"] == "ROTATION" and shown["publisher"] == "OPENSCI"
    code, missing = _cli("catalog", "mechanical", "show", "OS-MECH-TEST-NOPE", "--mechanical", OPENSCI)
    assert code == 1 and missing["status"] == "NOT_FOUND" and "catalog mechanical list" in missing["next_step"]


def test_user_documents_are_used_beside_the_opensci_one_and_from_the_environment(monkeypatch):
    code, listed = _cli("catalog", "mechanical", "list", "--mechanical", OPENSCI, "--mechanical", USER, "--kind", "POST")
    users = [r for r in listed["identities"] if r["publisher"] == "USER"]
    assert code == 0 and [r["opensci_sku"] for r in users] == ["USER-MECH-TEST-POST-D12P7-L60"]
    monkeypatch.setenv(ENV_MECHANICAL_CATALOG, USER)
    code, status = _cli("catalog", "mechanical", "status")
    assert code == 0 and status["status"] == "AVAILABLE" and status["environment_files"] == [USER]
    assert [d["source"] for d in status["documents"]] == ["ENVIRONMENT"] and status["users"] == ["test-user-hardware.1"]


def test_without_any_document_the_commands_say_how_to_get_one(tmp_path):
    code, listed = _cli("catalog", "mechanical", "list")
    assert code == 65 and listed["error"]["code"] == "MECHANICAL_CATALOG_NOT_AVAILABLE"
    code, status = _cli("catalog", "mechanical", "status")
    assert code == 1 and status["status"] == "NOT_AVAILABLE" and "os-optics catalog sync" in status["next_steps"]
    code, unreadable = _cli("catalog", "mechanical", "list", "--mechanical", str(tmp_path / "absent.json"))
    assert code == 64 and unreadable["error"]["code"] == "MECHANICAL_FILE_UNREADABLE"
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    code, invalid = _cli("catalog", "mechanical", "show", "X", "--mechanical", str(bad))
    assert code == 65 and invalid["error"]["code"] == "MECHANICAL_CATALOG_INVALID"


def test_an_imported_release_is_what_the_commands_use_without_a_file():
    assert _cli("catalog", "import", OPENSCI)[0] == 0
    code, status = _cli("catalog", "mechanical", "status")
    assert code == 0 and [d["source"] for d in status["documents"]] == ["STORE"]
    assert status["store"]["active"]["mechanical_version"] == OPENSCI_VERSION
    code, listed = _cli("catalog", "mechanical", "list", "--kind", "MOUNT")
    assert code == 0 and listed["documents"][0]["source"] == "STORE" and listed["total"] > 0
