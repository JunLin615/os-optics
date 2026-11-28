"""The public mechanical catalog in the local store and its synchronization (``os_optics.catalog.service``:
``sync_mechanical``, ``check_mechanical``, ``import_mechanical``, ``verify_mechanical``; ``catalog.mechanical``'s store
writer), against the stand-in catalog service (``catalog_service_stub``: ``GET /api/v1/catalog/mechanical`` and its
release route).

Covered: a service without the route (404: NOT_OFFERED, never an error, the optical sync unaffected); a download that
becomes the active release; a release already stored (no second download); bytes that differ from the manifest
(FAILED, nothing stored, exit 1); a 5xx / unreachable service (UNAVAILABLE: one stderr line, the stored release stays,
the optical exit code stands); import of an OPENSCI document and refusal of a USER one; verify of a damaged file;
the source precedence after a sync (file > environment > store); a conflicting document under a stored version; and
concurrent writers under the store lock.
"""

from __future__ import annotations

import contextlib
import io
import json
import threading
from pathlib import Path

import pytest

from catalog_service_stub import CatalogServiceStub
from catalog_store_helpers import FIXTURE_CATALOG
from mechanical_fixture import OPENSCI_FIXTURE, OPENSCI_VERSION, USER_FIXTURE, fixture_document, fixture_path
from os_optics._vendor.shared_contracts.common.osc1 import canonicalize_osc1_bytes
from os_optics.assembly_plan.mechanical import MechanicalCatalogError, seal_mechanical_document
from os_optics.catalog import CatalogError, CatalogErrorCode
from os_optics.catalog import service as catalog_service
from os_optics.catalog.mechanical import ENV_MECHANICAL_CATALOG, MechanicalStore, resolve_sources
from os_optics.cli import main as cli_main

OPENSCI_BYTES = fixture_path(OPENSCI_FIXTURE).read_bytes()
USER_BYTES = fixture_path(USER_FIXTURE).read_bytes()
NEXT_VERSION = "m2.0123456789ab"


def _release(version: str = NEXT_VERSION, *, rename: str | None = None) -> bytes:
    document = fixture_document(OPENSCI_FIXTURE)
    document["mechanical_version"] = version
    if rename is not None:
        document["identities"][0]["display_name"] = rename
    return canonicalize_osc1_bytes(seal_mechanical_document(document))


def _cli(*argv: str) -> tuple[int, dict]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli_main(list(argv))
    return code, json.loads(out.getvalue())


@pytest.fixture
def stub():
    with CatalogServiceStub() as service:
        service.state.publish(FIXTURE_CATALOG.read_bytes())
        yield service


@pytest.fixture
def online(empty_catalog_store, stub, monkeypatch) -> MechanicalStore:
    monkeypatch.setenv("OPENSCI_CATALOG_URL", stub.url)
    monkeypatch.setenv("OPENSCI_CATALOG_CHECK_INTERVAL", "3600")
    monkeypatch.delenv(ENV_MECHANICAL_CATALOG, raising=False)
    return MechanicalStore(Path(empty_catalog_store) / "mechanical")


def _mechanical_paths(stub) -> list[str]:
    return [p for p in stub.state.paths() if "/mechanical" in p]


def test_a_service_without_the_mechanical_route_is_not_offered_and_the_optical_sync_succeeds(online, stub, capsys):
    capsys.readouterr()
    code, synced = _cli("catalog", "sync")
    assert code == 0 and synced["active"] is not None
    assert synced["mechanical"]["outcome"] == "NOT_OFFERED" and synced["mechanical"]["offered"] is False
    assert _mechanical_paths(stub) == ["/api/v1/catalog/mechanical"]
    assert capsys.readouterr().err == ""  # not offered is not an error: nothing on stderr
    assert online.active_version() is None and online.state()["last_check"]["outcome"] == "NOT_OFFERED"


def test_sync_downloads_the_current_release_once_and_makes_it_active(online, stub):
    stub.state.publish_mechanical(OPENSCI_BYTES)
    code, synced = _cli("catalog", "sync")
    block = synced["mechanical"]
    assert code == 0 and block["outcome"] == "DOWNLOADED" and block["active"] == OPENSCI_VERSION
    assert block["sha256"] == stub.state.mechanical_entries[0]["sha256"]
    assert online.release_file(OPENSCI_VERSION).read_bytes() == OPENSCI_BYTES
    record = online.state()["stored"][OPENSCI_VERSION]
    assert record["source"] == "sync" and record["publisher"] == "OPENSCI" and record["stored_at"].endswith("Z")
    code, again = _cli("catalog", "sync")
    assert code == 0 and again["mechanical"]["outcome"] == "ALREADY_STORED"
    assert _mechanical_paths(stub).count(f"/api/v1/catalog/mechanical/{OPENSCI_VERSION}.json") == 1
    # a newer current release replaces the active one; the earlier stays stored
    stub.state.publish_mechanical(_release(), published_at="2026-10-02T00:00:00Z")
    code, newer = _cli("catalog", "sync")
    assert code == 0 and newer["mechanical"]["outcome"] == "DOWNLOADED" and online.active_version() == NEXT_VERSION
    assert sorted(online.state()["stored"]) == [OPENSCI_VERSION, NEXT_VERSION]
    # the stored active release is what a command uses without --mechanical
    assert resolve_sources().opensci.document.mechanical_version == NEXT_VERSION


def test_bytes_that_differ_from_the_manifest_fail_and_are_never_stored(online, stub):
    stub.state.publish_mechanical(OPENSCI_BYTES)
    stub.state.mechanical_served[OPENSCI_VERSION] = _release(OPENSCI_VERSION, rename="Other name")
    code, synced = _cli("catalog", "sync")
    block = synced["mechanical"]
    assert code == 1 and block["outcome"] == "FAILED" and block["error"]["code"] == "CATALOG_INTEGRITY_FAILED"
    assert synced["active"] is not None  # the optical release was synchronized all the same
    assert online.state()["stored"] == {} and not online.release_file(OPENSCI_VERSION).exists()


def test_an_invalid_manifest_fails_without_breaking_the_optical_sync(online, stub):
    stub.state.publish_mechanical(OPENSCI_BYTES, size_bytes=0)  # (a manifest the Engine refuses)
    code, synced = _cli("catalog", "sync")
    assert code == 1 and synced["mechanical"]["outcome"] == "FAILED"
    assert synced["mechanical"]["error"]["code"] == "CATALOG_SERVICE_ERROR" and synced["active"] is not None


@pytest.mark.parametrize("status", [500, 503])
def test_an_unavailable_mechanical_route_keeps_the_stored_release_and_the_optical_exit_code(online, stub, capsys, status):
    stub.state.publish_mechanical(OPENSCI_BYTES)
    assert _cli("catalog", "sync")[0] == 0
    stub.state.mechanical_status = status
    capsys.readouterr()
    code, synced = _cli("catalog", "sync")
    assert code == 0 and synced["mechanical"]["outcome"] == "UNAVAILABLE" and synced["mechanical"]["active"] == OPENSCI_VERSION
    err = capsys.readouterr().err
    assert len(err.splitlines()) == 1 and "mechanical catalog could not be checked" in err and OPENSCI_VERSION in err
    assert online.active_version() == OPENSCI_VERSION


def test_an_unreachable_service_is_unavailable_for_the_mechanical_check(online, stub, monkeypatch, capsys):
    stub.state.publish_mechanical(OPENSCI_BYTES)
    calls = []

    def unreachable(url, *, timeout):
        calls.append(url)
        raise CatalogError(CatalogErrorCode.CATALOG_SERVICE_UNREACHABLE, "the catalog service could not be reached", {"url": url})

    monkeypatch.setattr(catalog_service.remote, "fetch_mechanical_manifest", unreachable)
    block = catalog_service.sync_mechanical()
    assert calls == [f"{stub.url}/api/v1/catalog/mechanical"] and block["outcome"] == "UNAVAILABLE" and block["active"] is None
    assert "could not be checked" in capsys.readouterr().err


def test_check_reports_the_current_release_and_whether_the_device_is_up_to_date(online, stub):
    code, checked = _cli("catalog", "check")
    assert code == 0 and checked["mechanical"]["outcome"] == "NOT_OFFERED"
    stub.state.publish_mechanical(OPENSCI_BYTES)
    code, checked = _cli("catalog", "check")
    block = checked["mechanical"]
    assert code == 0 and block["outcome"] == "CHECKED" and block["up_to_date"] is False
    assert block["current"]["mechanical_version"] == OPENSCI_VERSION and online.state()["stored"] == {}  # nothing downloaded
    _cli("catalog", "sync")
    assert _cli("catalog", "check")[1]["mechanical"]["up_to_date"] is True


def test_a_mirror_manifest_url_makes_no_mechanical_request(online, stub, monkeypatch):
    stub.state.publish_mechanical(OPENSCI_BYTES)
    monkeypatch.setenv("OPENSCI_CATALOG_URL", stub.url + "/mirror/manifest.json")
    code, synced = _cli("catalog", "sync")
    assert code == 0 and synced["mechanical"]["outcome"] == "NOT_OFFERED" and _mechanical_paths(stub) == []


def test_status_reports_the_store_without_the_network(online, stub):
    stub.state.publish_mechanical(OPENSCI_BYTES)
    _cli("catalog", "sync")
    before = len(stub.state.requests)
    code, status = _cli("catalog", "status")
    assert code == 0 and len(stub.state.requests) == before
    block = status["mechanical"]
    assert block["active"]["mechanical_version"] == OPENSCI_VERSION and block["active"]["source"] == "sync"
    assert [row["mechanical_version"] for row in block["stored"]] == [OPENSCI_VERSION]
    assert status["problems"] == []  # (the optical problems list is not the mechanical one)


def test_import_stores_an_opensci_document_and_refuses_a_user_one(empty_catalog_store, tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_MECHANICAL_CATALOG, raising=False)
    code, imported = _cli("catalog", "import", str(fixture_path(OPENSCI_FIXTURE)))
    assert code == 0 and imported["status"] == "IMPORTED" and imported["mechanical"]["active"] == OPENSCI_VERSION
    store = MechanicalStore(Path(empty_catalog_store) / "mechanical")
    assert store.state()["stored"][OPENSCI_VERSION]["source"] == "import"
    code, refused = _cli("catalog", "import", str(fixture_path(USER_FIXTURE)))
    assert code == 65 and refused["error"]["code"] == "MECHANICAL_CATALOG_INVALID" and "--mechanical FILE" in refused["error"]["hint"]
    newer = tmp_path / "newer.json"
    newer.write_bytes(_release())
    code, inactive = _cli("catalog", "import", str(newer), "--no-activate")
    assert code == 0 and inactive["mechanical"]["active"] == OPENSCI_VERSION and NEXT_VERSION in store.state()["stored"]
    # the same version with other bytes is refused; the stored release is kept
    with pytest.raises(CatalogError) as conflict:
        catalog_service.import_mechanical(_release(OPENSCI_VERSION, rename="Other name"))
    assert conflict.value.code is CatalogErrorCode.CATALOG_RELEASE_CONFLICT
    assert store.release_file(OPENSCI_VERSION).read_bytes() == OPENSCI_BYTES
    # an optical release is still imported as one
    code, optical = _cli("catalog", "import", str(FIXTURE_CATALOG))
    assert code == 0 and optical["active"] is not None and "mechanical" not in optical


def test_verify_detects_a_damaged_mechanical_file_and_sync_repairs_it(online, stub):
    stub.state.publish_mechanical(OPENSCI_BYTES)
    _cli("catalog", "sync")
    code, verified = _cli("catalog", "verify")
    assert code == 0 and verified["ok"] is True and verified["mechanical"]["releases"][0]["ok"] is True
    online.release_file(OPENSCI_VERSION).write_bytes(OPENSCI_BYTES + b" ")
    code, damaged = _cli("catalog", "verify")
    row = damaged["mechanical"]["releases"][0]
    assert code == 66 and damaged["ok"] is False and row["problem"]["code"] == "MECHANICAL_CATALOG_DIGEST_MISMATCH"
    code, repaired = _cli("catalog", "sync")
    assert code == 0 and repaired["mechanical"]["outcome"] == "DOWNLOADED"
    assert online.release_file(OPENSCI_VERSION).read_bytes() == OPENSCI_BYTES and _cli("catalog", "verify")[0] == 0


def test_explicit_documents_take_precedence_over_the_synchronized_release(online, stub, tmp_path, monkeypatch):
    stub.state.publish_mechanical(OPENSCI_BYTES)
    _cli("catalog", "sync")
    newer = tmp_path / "newer.json"
    newer.write_bytes(_release())
    monkeypatch.setenv(ENV_MECHANICAL_CATALOG, str(newer))
    assert resolve_sources().opensci.document.mechanical_version == NEXT_VERSION  # environment over the store
    assert resolve_sources([fixture_path(OPENSCI_FIXTURE)]).opensci.document.mechanical_version == OPENSCI_VERSION  # file first


def test_concurrent_writers_store_one_release_under_the_lock(empty_catalog_store):
    store = MechanicalStore(Path(empty_catalog_store) / "mechanical")
    other = _release()
    errors: list[BaseException] = []

    def add(data: bytes) -> None:
        try:
            store.add_release(data, source="import", activate=False)
        except BaseException as exc:  # noqa: BLE001 -- reported by the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=add, args=(data,)) for data in (OPENSCI_BYTES, other) * 4]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    state = store.state()
    assert sorted(state["stored"]) == [OPENSCI_VERSION, NEXT_VERSION] and state["active"] in (OPENSCI_VERSION, NEXT_VERSION)
    assert [row["ok"] for row in store.verify()] == [True, True]


def test_the_store_refuses_a_document_that_is_not_an_opensci_release(empty_catalog_store):
    store = MechanicalStore(Path(empty_catalog_store) / "mechanical")
    with pytest.raises(MechanicalCatalogError):
        store.add_release(USER_BYTES, source="import")
    with pytest.raises(CatalogError) as size:
        store.add_release(OPENSCI_BYTES, source="sync", expected_size=len(OPENSCI_BYTES) + 1)
    assert size.value.code is CatalogErrorCode.CATALOG_INTEGRITY_FAILED
    with pytest.raises(CatalogError) as version:
        store.add_release(OPENSCI_BYTES, source="sync", expected_version=NEXT_VERSION)
    assert version.value.code is CatalogErrorCode.CATALOG_INTEGRITY_FAILED and store.state()["stored"] == {}
