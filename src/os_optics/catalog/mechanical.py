"""The public mechanical catalog on this device: which mechanical documents a command uses (``resolve_sources``).

Documents (``opensci.public_mechanical_catalog`` 1.0, ``os_optics.assembly_plan.mechanical``) come from, in order:

1. ``--mechanical FILE`` (repeatable; the ``cli_files`` a command passes);
2. else ``OS_OPTICS_MECHANICAL_CATALOG``: one or more paths separated by ``os.pathsep``;
3. for a plan command, what the plan binds: its OPENSCI release from the store (``MECHANICAL_CATALOG_NOT_AVAILABLE``
   when it is not stored) and its USER documents from the plan's own copies (``<sha256>.json`` in the plan's
   directory for them);
4. else the store's active OPENSCI release, when there is one.

Explicit documents (1 or 2) win per publisher: an explicit OPENSCI document replaces the store's / the plan's, and
explicit USER documents replace the plan's copies. At most one OPENSCI and ``MAX_USER_DOCUMENTS`` USER documents with
disjoint identities are used together (``MechanicalSources``). Nothing here needs a token or the network.

Store layout (inside the catalog store directory, ``store_directory``)::

    mechanical/releases/<mechanical_version>.json   exact release bytes
    mechanical/state.json   {"schema": "opensci.engine.mechanical_store", "version": "1", "active": <version> | null,
                             "stored": {<version>: {"sha256", "size_bytes", "publisher", "source", "stored_at"}},
                             "last_check": {...} | null}

A release is written once (exact bytes, temporary file + rename) after its SHA-256, its size and its content were
verified, and never replaced by different bytes (a mechanical version always denotes the same document). Every change of
``state.json`` is a read-modify-write of the current index under the catalog store's lock (``catalog.locking``, the
same lock as the optical store), so concurrent processes never lose a stored release. ``catalog sync`` and ``catalog
import`` add releases (``MechanicalStore.add_release``); only OPENSCI documents are stored (a USER document is passed
with ``--mechanical FILE`` and copied into the plan that binds it).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from ..assembly_plan.mechanical.model import (
    OPENSCI_VERSION_PATTERN,
    PUBLISHER_OPENSCI,
    PUBLISHER_USER,
    LoadedMechanicalDocument,
    MechanicalCatalogError,
    MechanicalErrorCode,
    MechanicalPlanBinding,
    MechanicalSourceKind,
    MechanicalSources,
    load_mechanical_document,
)
from .errors import CatalogError, CatalogErrorCode
from .store import CatalogStore, store_directory, utc_text

ENV_MECHANICAL_CATALOG = "OS_OPTICS_MECHANICAL_CATALOG"
MECHANICAL_DIR = "mechanical"
MECHANICAL_STATE_FILE = "state.json"
MECHANICAL_RELEASES_DIR = "releases"
MECHANICAL_STATE_SCHEMA = "opensci.engine.mechanical_store"
MECHANICAL_STATE_VERSION = "1"
RECOVERY = ("os-optics catalog sync", "--mechanical FILE")
SOURCE_SYNC = "sync"
SOURCE_IMPORT = "import"
SOURCES = (SOURCE_SYNC, SOURCE_IMPORT)


def _version_ok(version: Any) -> bool:
    return isinstance(version, str) and re.fullmatch(OPENSCI_VERSION_PATTERN, version) is not None


def empty_state() -> dict[str, Any]:
    """The index of a store that holds no mechanical release."""

    return {"schema": MECHANICAL_STATE_SCHEMA, "version": MECHANICAL_STATE_VERSION, "active": None, "stored": {},
            "last_check": None}


def _stored_record(record: Any) -> bool:
    return (isinstance(record, dict) and isinstance(record.get("sha256"), str) and isinstance(record.get("size_bytes"), int)
            and record.get("publisher") == PUBLISHER_OPENSCI)


def _file_has(path: Path, digest: str) -> bool:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest() == digest
    except OSError:
        return False


class MechanicalStore:
    """The mechanical part of the catalog store at ``root`` (``<catalog store>/mechanical``), read side."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> MechanicalStore:
        return cls(store_directory(environ) / MECHANICAL_DIR)

    def __repr__(self) -> str:
        return f"MechanicalStore({str(self.root)!r})"

    def release_file(self, mechanical_version: str) -> Path:
        if not _version_ok(mechanical_version):
            raise MechanicalCatalogError(MechanicalErrorCode.MECHANICAL_CATALOG_INVALID,
                                         f"{mechanical_version!r} is not an OpenSci mechanical catalog version")
        return self.root / MECHANICAL_RELEASES_DIR / f"{mechanical_version}.json"

    def state(self) -> dict[str, Any]:
        """The store index; an absent or unreadable index reads as an empty store (nothing active)."""

        try:
            document = json.loads((self.root / MECHANICAL_STATE_FILE).read_bytes().decode("utf-8"))
        except (OSError, UnicodeDecodeError, ValueError, RecursionError):
            return empty_state()
        if not isinstance(document, dict) or document.get("schema") != MECHANICAL_STATE_SCHEMA:
            return empty_state()
        stored = document.get("stored")
        stored = {v: r for v, r in stored.items() if _version_ok(v) and _stored_record(r)} if isinstance(stored, dict) else {}
        active = document.get("active")
        last_check = document.get("last_check")
        return {"schema": MECHANICAL_STATE_SCHEMA, "version": MECHANICAL_STATE_VERSION,
                "active": active if active in stored else None, "stored": stored,
                "last_check": last_check if isinstance(last_check, dict) else None}

    def active_version(self) -> str | None:
        active = self.state().get("active")
        return active if _version_ok(active) else None

    def release(self, mechanical_version: str, *, expected_sha256: str | None = None) -> LoadedMechanicalDocument | None:
        """The stored OPENSCI release ``mechanical_version``, verified (bytes against the recorded and the expected
        SHA-256, then the document itself); None when it is not stored."""

        path = self.release_file(mechanical_version)
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise MechanicalCatalogError(MechanicalErrorCode.MECHANICAL_FILE_UNREADABLE,
                                         f"stored mechanical release {mechanical_version} cannot be read: {exc}",
                                         {"mechanical_version": mechanical_version, "location": str(path)}) from None
        digest = hashlib.sha256(data).hexdigest()
        record = self.state()["stored"].get(mechanical_version)
        recorded = record.get("sha256") if isinstance(record, dict) else None
        for expected in (recorded, expected_sha256):
            if expected is not None and expected != digest:
                raise MechanicalCatalogError(
                    MechanicalErrorCode.MECHANICAL_CATALOG_DIGEST_MISMATCH,
                    f"stored mechanical release {mechanical_version} differs from the release it was recorded as; "
                    "run 'os-optics catalog sync' to repair it",
                    {"mechanical_version": mechanical_version, "expected_sha256": expected, "sha256": digest})
        loaded = load_mechanical_document(data, source=MechanicalSourceKind.STORE, location=path, publisher=PUBLISHER_OPENSCI)
        if loaded.document.mechanical_version != mechanical_version:
            raise MechanicalCatalogError(MechanicalErrorCode.MECHANICAL_CATALOG_INVALID,
                                         f"stored file {path.name} holds mechanical release {loaded.document.mechanical_version}",
                                         {"location": str(path)})
        return loaded

    def active(self) -> LoadedMechanicalDocument | None:
        version = self.active_version()
        return None if version is None else self.release(version)

    # ------------------------------------------------------------------------------------------------ writer

    def _catalog_store(self) -> CatalogStore:
        """The catalog store this mechanical store lives in (its lock and its atomic file writes)."""

        return CatalogStore(self.root.parent)

    def update_state(self, change: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
        """Apply ``change`` to the current index under the catalog store's lock and write the result when it differs;
        ``change`` may raise to refuse (nothing is written)."""

        store = self._catalog_store()
        with store.lock():
            current = self.state()
            updated = change(current)
            if updated != current:
                data = (json.dumps(updated, indent=2, sort_keys=True) + "\n").encode("utf-8")
                store._write_atomic(self.root / MECHANICAL_STATE_FILE, data)
            return updated

    def add_release(self, data: bytes, *, source: str, expected_sha256: str | None = None,
                    expected_size: int | None = None, expected_version: str | None = None,
                    activate: bool = True) -> tuple[dict[str, Any], LoadedMechanicalDocument]:
        """Verify ``data`` and store it (unless already held), making it the active release when ``activate`` (or when
        no release is active). Returns the written index and the loaded document.

        Refuses bytes that differ from ``expected_sha256`` / ``expected_size`` (``CATALOG_INTEGRITY_FAILED``), a document
        that is not a valid OPENSCI mechanical catalog (``MechanicalCatalogError``), a release whose version differs from
        ``expected_version`` (``CATALOG_INTEGRITY_FAILED``), and different bytes under a version this device already
        holds (``CATALOG_RELEASE_CONFLICT``; the stored release is kept). The bytes are verified before the lock is
        taken; the release file and the index are written under it."""

        if source not in SOURCES:
            raise ValueError(f"source must be one of {', '.join(SOURCES)}")
        digest = hashlib.sha256(data).hexdigest()
        if expected_sha256 is not None and digest != expected_sha256:
            raise CatalogError(CatalogErrorCode.CATALOG_INTEGRITY_FAILED,
                               f"the downloaded mechanical catalog has SHA-256 {digest}, not the published {expected_sha256}; "
                               "it was not used", {"expected_sha256": expected_sha256, "sha256": digest})
        if expected_size is not None and len(data) != expected_size:
            raise CatalogError(CatalogErrorCode.CATALOG_INTEGRITY_FAILED,
                               f"the downloaded mechanical catalog has {len(data)} bytes, not the published {expected_size}; "
                               "it was not used", {"expected_size_bytes": expected_size, "size_bytes": len(data)})
        loaded = load_mechanical_document(data, source=MechanicalSourceKind.STORE, publisher=PUBLISHER_OPENSCI)
        version = loaded.document.mechanical_version
        if expected_version is not None and version != expected_version:
            raise CatalogError(CatalogErrorCode.CATALOG_INTEGRITY_FAILED,
                               f"the downloaded document is mechanical catalog {version}, not the published "
                               f"{expected_version}; it was not used",
                               {"mechanical_version": version, "expected_mechanical_version": expected_version})
        path = self.release_file(version)
        store = self._catalog_store()

        def change(state: dict[str, Any]) -> dict[str, Any]:
            held = state["stored"].get(version)
            if held is not None and held["sha256"] != digest:
                raise CatalogError(CatalogErrorCode.CATALOG_RELEASE_CONFLICT,
                                   f"this device already holds a different mechanical catalog {version}; a mechanical "
                                   "version always denotes the same document, so the stored release was kept",
                                   {"mechanical_version": version, "stored_sha256": held["sha256"], "offered_sha256": digest})
            if not _file_has(path, digest):  # absent, or damaged on disk: (re)write the verified bytes
                store._write_atomic(path, data)
            record = held if held is not None else {"sha256": digest, "size_bytes": len(data), "publisher": PUBLISHER_OPENSCI,
                                                    "source": source, "stored_at": utc_text(time.time())}
            return {**state, "stored": {**state["stored"], version: record},
                    "active": version if activate or state["active"] is None else state["active"]}

        state = self.update_state(change)
        return state, loaded

    def record_check(self, check: Mapping[str, Any]) -> dict[str, Any]:
        """Record the outcome of the last mechanical manifest request (``last_check``); an unwritable store keeps its
        index (the next check records again)."""

        try:
            return self.update_state(lambda state: {**state, "last_check": dict(check)})
        except CatalogError:
            return self.state()

    def verify(self) -> list[dict[str, Any]]:
        """Every stored release re-hashed against its recorded SHA-256 and re-validated (no network)."""

        rows = []
        state = self.state()
        for version in sorted(state["stored"]):
            record = state["stored"][version]
            row: dict[str, Any] = {"mechanical_version": version, "sha256": record["sha256"], "ok": True}
            try:
                if self.release(version) is None:
                    raise MechanicalCatalogError(MechanicalErrorCode.MECHANICAL_CATALOG_NOT_AVAILABLE,
                                                 f"stored mechanical release {version} is missing from {self.root}",
                                                 {"mechanical_version": version})
            except MechanicalCatalogError as exc:
                row.update(ok=False, problem=exc.as_dict())
            rows.append(row)
        return rows


def environment_files(environ: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """The paths named by ``OS_OPTICS_MECHANICAL_CATALOG`` (``os.pathsep``-separated; empty entries ignored)."""

    env = os.environ if environ is None else environ
    value = str(env.get(ENV_MECHANICAL_CATALOG, "") or "")
    return tuple(part.strip() for part in value.split(os.pathsep) if part.strip())


def read_document_file(path: str | Path, source: MechanicalSourceKind) -> LoadedMechanicalDocument:
    """One document from a file a caller named (``MECHANICAL_FILE_UNREADABLE`` when it cannot be read)."""

    file = Path(path).expanduser()
    try:
        data = file.read_bytes()
    except OSError as exc:
        raise MechanicalCatalogError(MechanicalErrorCode.MECHANICAL_FILE_UNREADABLE,
                                     f"cannot read mechanical catalog file {str(path)!r}: {exc.strerror or exc}",
                                     {"location": str(path)}) from None
    return load_mechanical_document(data, source=source, location=str(path))


def _plan_user_copy(binding: MechanicalPlanBinding, sha256: str) -> LoadedMechanicalDocument:
    location = None if binding.user_copies_dir is None else Path(binding.user_copies_dir) / f"{sha256}.json"
    if location is None or not location.is_file():
        raise MechanicalCatalogError(
            MechanicalErrorCode.MECHANICAL_CATALOG_NOT_AVAILABLE,
            f"the plan's USER mechanical document {sha256} is not in the plan directory; pass it with --mechanical FILE",
            {"sha256": sha256, "location": None if location is None else str(location), "recovery": ["--mechanical FILE"]})
    loaded = read_document_file(location, MechanicalSourceKind.PLAN)
    if loaded.sha256 != sha256 or loaded.publisher != PUBLISHER_USER:
        raise MechanicalCatalogError(MechanicalErrorCode.MECHANICAL_CATALOG_DIGEST_MISMATCH,
                                     f"the plan's copy {location.name} is not the USER document the plan binds",
                                     {"expected_sha256": sha256, "sha256": loaded.sha256, "location": str(location)})
    return loaded


def resolve_sources(cli_files: Iterable[str | Path] = (), environ: Mapping[str, str] | None = None,
                    plan_binding: MechanicalPlanBinding | None = None, *,
                    store: MechanicalStore | None = None) -> MechanicalSources:
    """The ``MechanicalSources`` one command uses (module docstring). ``environ`` defaults to the process environment,
    ``store`` to the store of ``environ``'s catalog directory. An empty result is possible (no document anywhere): the
    caller that needs one calls ``MechanicalSources.require()``."""

    files = tuple(cli_files)
    source = MechanicalSourceKind.FILE
    if not files:
        files, source = environment_files(environ), MechanicalSourceKind.ENVIRONMENT
    explicit = [read_document_file(path, source) for path in files]
    documents: list[LoadedMechanicalDocument] = list(explicit)
    explicit_opensci = any(d.publisher == PUBLISHER_OPENSCI for d in explicit)
    explicit_users = any(d.publisher == PUBLISHER_USER for d in explicit)
    store = MechanicalStore.from_environment(environ) if store is None else store
    if not explicit_opensci:
        bound = None if plan_binding is None else plan_binding.opensci
        if bound is not None:
            loaded = store.release(bound.mechanical_version, expected_sha256=bound.sha256)
            if loaded is None:
                raise MechanicalCatalogError(
                    MechanicalErrorCode.MECHANICAL_CATALOG_NOT_AVAILABLE,
                    f"the plan binds mechanical catalog {bound.mechanical_version}, which is not on this device: "
                    "run 'os-optics catalog sync' or pass --mechanical FILE",
                    {"mechanical_version": bound.mechanical_version, "sha256": bound.sha256, "recovery": list(RECOVERY)})
            documents.append(loaded)
        else:
            active = store.active()
            if active is not None:
                documents.append(active)
    if plan_binding is not None and not explicit_users:
        documents.extend(_plan_user_copy(plan_binding, ref.sha256) for ref in plan_binding.users)
    return MechanicalSources(tuple(documents))
