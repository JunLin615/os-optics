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

This module reads the store; adding releases (``catalog sync`` / ``catalog import``) belongs to the store writer.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterable, Mapping
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
from .store import store_directory

ENV_MECHANICAL_CATALOG = "OS_OPTICS_MECHANICAL_CATALOG"
MECHANICAL_DIR = "mechanical"
MECHANICAL_STATE_FILE = "state.json"
MECHANICAL_RELEASES_DIR = "releases"
MECHANICAL_STATE_SCHEMA = "opensci.engine.mechanical_store"
MECHANICAL_STATE_VERSION = "1"
RECOVERY = ("os-optics catalog sync", "--mechanical FILE")


def _version_ok(version: Any) -> bool:
    return isinstance(version, str) and re.fullmatch(OPENSCI_VERSION_PATTERN, version) is not None


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
            return {"schema": MECHANICAL_STATE_SCHEMA, "version": MECHANICAL_STATE_VERSION, "active": None, "stored": {},
                    "last_check": None}
        if not isinstance(document, dict) or document.get("schema") != MECHANICAL_STATE_SCHEMA:
            return {"schema": MECHANICAL_STATE_SCHEMA, "version": MECHANICAL_STATE_VERSION, "active": None, "stored": {},
                    "last_check": None}
        stored = document.get("stored")
        return {**document, "stored": stored if isinstance(stored, dict) else {}}

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
