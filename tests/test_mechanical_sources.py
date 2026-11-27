"""Which mechanical documents a command uses (``os_optics.catalog.mechanical.resolve_sources`` and
``os_optics.local.catalog.mechanical_sources``): files, environment, plan binding, the store's active release."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from mechanical_fixture import OPENSCI_FIXTURE, OPENSCI_VERSION, USER_FIXTURE, fixture_document, fixture_path
from os_optics._vendor.shared_contracts.common.osc1 import canonicalize_osc1_bytes
from os_optics.assembly_plan.mechanical import (
    MechanicalCatalogError,
    MechanicalErrorCode,
    MechanicalPlanBinding,
    MechanicalSourceKind,
    load_mechanical_document,
    seal_mechanical_document,
)
from os_optics.catalog.mechanical import ENV_MECHANICAL_CATALOG, MechanicalStore, environment_files, resolve_sources
from os_optics.local.catalog import mechanical_sources

E = MechanicalErrorCode
OTHER_VERSION = "m2.0123456789ab"


def _store(root: Path, versions: dict[str, bytes], active: str | None) -> MechanicalStore:
    releases = root / "mechanical" / "releases"
    releases.mkdir(parents=True)
    stored = {}
    for version, data in versions.items():
        (releases / f"{version}.json").write_bytes(data)
        loaded = load_mechanical_document(data)
        stored[version] = {"sha256": loaded.sha256, "size_bytes": loaded.size_bytes, "publisher": "OPENSCI", "source": "import",
                           "stored_at": "2026-11-28T00:00:00Z"}
    state = {"schema": "opensci.engine.mechanical_store", "version": "1", "active": active, "stored": stored, "last_check": None}
    (root / "mechanical" / "state.json").write_text(json.dumps(state), encoding="utf-8")
    return MechanicalStore(root / "mechanical")


def _other_release() -> bytes:
    document = fixture_document(OPENSCI_FIXTURE)
    document["mechanical_version"] = OTHER_VERSION
    return canonicalize_osc1_bytes(seal_mechanical_document(document))


def _user_file(tmp_path: Path, name: str = "mine.json", sku: str | None = None) -> Path:
    document = fixture_document(USER_FIXTURE)
    if sku is not None:
        document["identities"][0]["opensci_sku"] = sku
    path = tmp_path / name
    path.write_text(json.dumps(seal_mechanical_document(document), indent=1, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def opensci_bytes() -> bytes:
    return fixture_path(OPENSCI_FIXTURE).read_bytes()


def test_files_win_over_the_environment_and_the_store(tmp_path, opensci_bytes):
    store = _store(tmp_path / "store", {OTHER_VERSION: _other_release()}, OTHER_VERSION)
    user = _user_file(tmp_path)
    env = {ENV_MECHANICAL_CATALOG: str(tmp_path / "nonexistent.json")}
    sources = resolve_sources([fixture_path(OPENSCI_FIXTURE), user], env, store=store)
    assert [(d.publisher, d.source) for d in sources.documents] == [("OPENSCI", MechanicalSourceKind.FILE), ("USER", MechanicalSourceKind.FILE)]
    assert sources.opensci.document.mechanical_version == OPENSCI_VERSION
    assert sources.users[0].location == str(user)


def test_the_environment_names_several_files_and_a_user_only_list_keeps_the_stores_release(tmp_path):
    store = _store(tmp_path / "store", {OTHER_VERSION: _other_release()}, OTHER_VERSION)
    a, b = _user_file(tmp_path, "a.json"), _user_file(tmp_path, "b.json", "USER-MECH-TEST-OTHER")
    env = {ENV_MECHANICAL_CATALOG: f"{a}{os.pathsep}{os.pathsep} {b} "}
    assert environment_files(env) == (str(a), str(b))
    sources = resolve_sources((), env, store=store)
    assert [d.source for d in sources.documents] == [MechanicalSourceKind.STORE, MechanicalSourceKind.ENVIRONMENT,
                                                     MechanicalSourceKind.ENVIRONMENT]
    assert sources.opensci.document.mechanical_version == OTHER_VERSION


def test_nothing_anywhere_is_an_empty_set(tmp_path):
    sources = resolve_sources((), {}, store=MechanicalStore(tmp_path / "empty"))
    assert sources.empty and sources.binding() == ()


def test_a_plan_binding_reads_its_release_from_the_store_and_its_user_copies(tmp_path, opensci_bytes):
    store = _store(tmp_path / "store", {OPENSCI_VERSION: opensci_bytes, OTHER_VERSION: _other_release()}, OTHER_VERSION)
    copies = tmp_path / "plan" / "mechanical"
    copies.mkdir(parents=True)
    user = load_mechanical_document(fixture_path(USER_FIXTURE).read_bytes())
    (copies / f"{user.sha256}.json").write_bytes(fixture_path(USER_FIXTURE).read_bytes())
    opensci = load_mechanical_document(opensci_bytes)
    binding = MechanicalPlanBinding((opensci.ref(), user.ref()), copies)
    sources = resolve_sources((), {}, binding, store=store)
    assert sources.binding() == binding.refs  # the bound release, not the store's active one
    assert [d.source for d in sources.documents] == [MechanicalSourceKind.STORE, MechanicalSourceKind.PLAN]
    # explicit documents replace the bound ones per publisher
    explicit = resolve_sources([_user_file(tmp_path, sku="USER-MECH-TEST-NEW")], {}, binding, store=store)
    assert [i.opensci_sku for i in explicit.users[0].document.identities] == ["USER-MECH-TEST-NEW"]
    assert explicit.opensci.sha256 == opensci.sha256


def test_a_bound_release_that_is_not_stored_or_a_missing_copy_is_not_available(tmp_path, opensci_bytes):
    store = _store(tmp_path / "store", {OTHER_VERSION: _other_release()}, OTHER_VERSION)
    opensci = load_mechanical_document(opensci_bytes)
    with pytest.raises(MechanicalCatalogError) as caught:
        resolve_sources((), {}, MechanicalPlanBinding((opensci.ref(),)), store=store)
    assert caught.value.code is E.MECHANICAL_CATALOG_NOT_AVAILABLE
    assert caught.value.details["mechanical_version"] == OPENSCI_VERSION and "catalog sync" in caught.value.message
    user = load_mechanical_document(fixture_path(USER_FIXTURE).read_bytes())
    with pytest.raises(MechanicalCatalogError) as caught:
        resolve_sources([fixture_path(OPENSCI_FIXTURE)], {}, MechanicalPlanBinding((user.ref(),), tmp_path), store=store)
    assert caught.value.code is E.MECHANICAL_CATALOG_NOT_AVAILABLE and caught.value.details["sha256"] == user.sha256


def test_stored_bytes_are_verified(tmp_path, opensci_bytes):
    store = _store(tmp_path / "store", {OPENSCI_VERSION: opensci_bytes}, OPENSCI_VERSION)
    (store.root / "releases" / f"{OPENSCI_VERSION}.json").write_bytes(opensci_bytes.replace(b"Test post", b"Test Post"))
    with pytest.raises(MechanicalCatalogError) as caught:
        resolve_sources((), {}, store=store)
    assert caught.value.code is E.MECHANICAL_CATALOG_DIGEST_MISMATCH
    other = load_mechanical_document(_other_release())
    store = _store(tmp_path / "store2", {OPENSCI_VERSION: opensci_bytes}, None)
    binding = MechanicalPlanBinding((other.ref().model_copy(update={"mechanical_version": OPENSCI_VERSION}),))
    with pytest.raises(MechanicalCatalogError) as caught:
        resolve_sources((), {}, binding, store=store)
    assert caught.value.code is E.MECHANICAL_CATALOG_DIGEST_MISMATCH


def test_an_unreadable_index_reads_as_an_empty_store(tmp_path, opensci_bytes):
    store = _store(tmp_path / "store", {OPENSCI_VERSION: opensci_bytes}, OPENSCI_VERSION)
    (store.root / "state.json").write_text("{not json", encoding="utf-8")
    assert store.active_version() is None and resolve_sources((), {}, store=store).empty
    assert store.release(OPENSCI_VERSION).document.mechanical_version == OPENSCI_VERSION  # still readable when bound


def test_an_unreadable_file_and_an_invalid_file_name_their_location(tmp_path):
    with pytest.raises(MechanicalCatalogError) as caught:
        resolve_sources([tmp_path / "missing.json"], {})
    assert caught.value.code is E.MECHANICAL_FILE_UNREADABLE and caught.value.details["location"].endswith("missing.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    with pytest.raises(MechanicalCatalogError) as caught:
        resolve_sources([bad], {})
    assert caught.value.code is E.MECHANICAL_CATALOG_INVALID and caught.value.details["location"] == str(bad)


def test_duplicate_identities_across_files_are_refused_and_identical_bytes_count_once(tmp_path):
    a = _user_file(tmp_path, "a.json")
    same = tmp_path / "same.json"
    same.write_bytes(a.read_bytes())
    assert len(resolve_sources([a, same], {}, store=MechanicalStore(tmp_path / "none")).documents) == 1
    other = fixture_document(USER_FIXTURE)
    other["mechanical_version"] = "another-label"
    b = tmp_path / "b.json"
    b.write_text(json.dumps(seal_mechanical_document(other)), encoding="utf-8")
    with pytest.raises(MechanicalCatalogError) as caught:
        resolve_sources([a, b], {}, store=MechanicalStore(tmp_path / "none"))
    assert caught.value.code is E.MECHANICAL_IDENTITY_DUPLICATE
    assert caught.value.details["opensci_sku"] == "USER-MECH-TEST-POST-D12P7-L60"


def test_local_mechanical_sources_reads_the_process_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENSCI_CATALOG_DIR", str(tmp_path / "catalog-store"))
    monkeypatch.setenv(ENV_MECHANICAL_CATALOG, str(fixture_path(OPENSCI_FIXTURE)))
    sources = mechanical_sources()
    assert sources.opensci.source is MechanicalSourceKind.ENVIRONMENT
    monkeypatch.delenv(ENV_MECHANICAL_CATALOG)
    assert mechanical_sources().empty
    assert mechanical_sources([fixture_path(USER_FIXTURE)]).users[0].source is MechanicalSourceKind.FILE
