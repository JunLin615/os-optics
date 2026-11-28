"""Catalog synchronization: status, check, sync, verify, import, and the release a local run uses.

Policy (offline-first):

* a catalog-consuming entry point (``load_public_catalog()`` without an explicit catalog) checks the service's manifest
  at most once per ``OPENSCI_CATALOG_CHECK_INTERVAL`` seconds (default 3600; ``0`` turns automatic checks off), with a
  conditional request; it downloads only when the service's current release is not on this device;
* when that check fails (no network, service down), the stored release stays in use, silently;
* an empty store is synchronized once on first use; when that fails too, the run stops with ``CATALOG_NOT_AVAILABLE``
  and a message naming ``os-optics catalog sync`` / ``os-optics catalog import <file>``;
* a release a command downloads by itself (first use, a newer current release, a release a Project pins) is announced
  by one line on stderr naming the release, its size and the service; standard output is never touched;
* a release written in a catalog schema this Engine cannot read is never downloaded: the status reports
  ``ENGINE_UPGRADE_REQUIRED`` and the stored compatible release stays active (an empty store takes the newest
  compatible release the service still distributes);
* a Project pinning a ``catalog_version`` runs against exactly that release: from the store, or fetched once when it is
  missing and automatic access is on (into an empty store, that first release becomes the active one, as on first use;
  an active release is never changed by it); when the service does not distribute it, the run stops with
  ``CATALOG_NOT_AVAILABLE`` naming ``os-optics project update-catalog`` (not a fetch that cannot work);
* ``sync(catalog_version)`` (``catalog sync --release``) makes the fetched release active only when no release is
  active (an explicit choice); otherwise the active release stays and the report's ``notes`` say so;
* status reports never present stale service facts as current: ``service.current`` / ``up_to_date`` are null unless
  the configured service's last answer confirms them (``CatalogStatus``);
* releases stored with none active (an index rebuilt after it was lost, or releases imported without activation): the
  next check selects the service's current release; when no check settles it, an unpinned use makes the newest stored
  release this Engine reads the active one (``active_basis`` ``NEWEST_STORED``), announces it once on stderr, and the
  next successful check replaces it with the service's current release as always. A fetch of a pinned release never
  chooses. When this Engine reads none of the stored releases the run stops with ``CATALOG_ACTIVE_UNKNOWN`` naming them
  and ``os-optics catalog sync`` / ``catalog use <catalog_version>`` / ``catalog import <file>``;
* the browser runtime (Pyodide) never uses the network: its host installs the catalog (``json_api.install_catalog``).

The public mechanical catalog (``opensci.public_mechanical_catalog``, ``catalog.mechanical``) is synchronized only
by an explicit ``catalog sync`` (``sync_mechanical``, after the optical release): the service's mechanical manifest is
asked for once; ``404`` means the service does not offer it (``NOT_OFFERED``, not an error); a release not on this
device is downloaded (SHA-256, size and content verified) and made active. A connection failure, a timeout or a ``5xx``
answer is ``UNAVAILABLE``: one line on stderr says so and the stored active mechanical release stays in use; the
mechanical route never fails an optical synchronization. Automatic checks never touch the mechanical catalog.

Concurrency: network requests run without the store lock; what they learned is then merged into the *current* index
under the lock (``CatalogStore.update_state``). A check never overwrites what a newer check recorded, and a
synchronization activates its target only while no newer check named a different current release, so concurrent
operations never lose a stored release or move the active release back.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .._vendor.shared_contracts.design_catalog import CatalogManifestV1, CatalogReleaseEntryV1
from . import remote
from .errors import CatalogError, CatalogErrorCode
from .mechanical import SOURCE_IMPORT as MECHANICAL_SOURCE_IMPORT
from .mechanical import SOURCE_SYNC as MECHANICAL_SOURCE_SYNC
from .mechanical import MechanicalStore
from .store import (
    SOURCE_IMPORT,
    SOURCE_SYNC,
    SUPPORTED_SCHEMA_VERSIONS,
    CatalogStore,
    StoredRelease,
    StoreState,
    check_compatible,
    is_retired_schema,
    sha256_hex,
    utc_text,
    validate_release,
)

ENV_CHECK_INTERVAL = "OPENSCI_CATALOG_CHECK_INTERVAL"
DEFAULT_CHECK_INTERVAL_SECONDS = 3600
#: Timeout of one automatic (background) request: short, so an offline device is not held up.
AUTOMATIC_TIMEOUT_SECONDS = 5.0
#: An empty store retries the service at most this often after a failed attempt (offline: not on every call).
EMPTY_STORE_RETRY_SECONDS = 60.0
#: Timeout of one request of an explicit ``check`` / ``sync``.
EXPLICIT_TIMEOUT_SECONDS = 60.0
STATUS_SCHEMA = "opensci.engine.catalog_status"
STATUS_SCHEMA_VERSION = "1"
VERIFY_SCHEMA = "opensci.engine.catalog_verify"
#: The recovery commands, each for its own situation: ``catalog sync`` downloads the service's current release
#: and makes it active; ``catalog sync --release V`` fetches one retained release; ``catalog use V`` selects a release
#: already stored on this device; ``catalog import FILE`` adds a release file on a computer without access to the service.
GET_CATALOG_HELP = ("run 'os-optics catalog sync' to download the current release and make it active (needs access to "
                    "the OpenSci catalog service; OPENSCI_CATALOG_URL or OPENSCI_API_URL selects another service), or, on a "
                    "computer without that access, 'os-optics catalog import <catalog file>' with a release file copied "
                    "to it")
CHOOSE_CATALOG_HELP = ("choose one: 'os-optics catalog use <catalog_version>' selects a release stored on this device (no "
                       "network), 'os-optics catalog sync' downloads the catalog service's current release and makes it "
                       "active, 'os-optics catalog import <catalog file>' adds a release file and makes it active")
#: The size of a release in a notice (MB, one decimal).
BYTES_PER_MB = 1_000_000
MECHANICAL_STATUS_SCHEMA = "opensci.engine.mechanical_catalog_status"
#: The outcome of the mechanical part of ``catalog sync`` (``sync_mechanical``) and of ``catalog check``
#: (``check_mechanical``: ``CHECKED`` instead of a download outcome).
MECHANICAL_DOWNLOADED = "DOWNLOADED"
MECHANICAL_ALREADY_STORED = "ALREADY_STORED"
MECHANICAL_NOT_OFFERED = "NOT_OFFERED"
MECHANICAL_FAILED = "FAILED"
MECHANICAL_UNAVAILABLE = "UNAVAILABLE"
MECHANICAL_CHECKED = "CHECKED"
#: The lowest HTTP status of a service-side failure (a transient condition for the mechanical route).
HTTP_SERVER_ERROR = 500


def _environ(environ: Any) -> Any:
    return os.environ if environ is None else environ


def network_available() -> bool:
    """False inside the browser runtime (Pyodide), whose host installs the catalog instead."""

    return sys.platform != "emscripten"


def check_interval(environ: Any = None) -> int:
    """Seconds between automatic checks (``OPENSCI_CATALOG_CHECK_INTERVAL``; 0 = never automatic; default 3600)."""

    raw = str(_environ(environ).get(ENV_CHECK_INTERVAL, "") or "").strip()
    try:
        value = int(raw) if raw else DEFAULT_CHECK_INTERVAL_SECONDS
    except ValueError:
        return DEFAULT_CHECK_INTERVAL_SECONDS
    return max(0, value)


def automatic_access(environ: Any = None) -> bool:
    return network_available() and check_interval(environ) > 0


def _entry_dict(entry: CatalogReleaseEntryV1) -> dict[str, Any]:
    return entry.model_dump(mode="json")


@dataclass(frozen=True)
class CatalogStatus:
    """What this device holds and what the service last said (``as_dict()`` is the JSON report).

    ``manifest_url`` is the service configured now (the one the next check contacts). ``reachable`` is the answer of
    this command's own request (``check`` / ``sync``), None when this command did not contact the service; the report's
    ``service.reachable`` falls back to the outcome of the last recorded attempt, with its time. The service's current
    release (``service.current``, ``up_to_date``) is reported only while it is confirmed: recorded from the configured
    service, and its last attempt answered. Otherwise those are null and ``service.last_known`` keeps what the service
    said, and when."""

    store: str
    manifest_url: str | None
    state: StoreState
    automatic_check_interval: int
    reachable: bool | None = None
    problems: tuple[dict[str, Any], ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def active(self) -> StoredRelease | None:
        return self.state.active_release

    @property
    def checked_now(self) -> bool:
        """Whether this command contacted the service."""

        return self.reachable is not None

    @property
    def service_reachable(self) -> bool | None:
        """This command's answer, else the outcome of the last recorded attempt to the configured service (None: never
        attempted, or the recorded attempt was to another service)."""

        if self.reachable is not None:
            return self.reachable
        state = self.state
        if state.checked_at is None or self.manifest_url is None:
            return None
        if state.last_check_error is not None:
            # a failure against another service says nothing about this one
            return False if failure_concerns(state.last_check_error, self.manifest_url) else None
        if state.manifest_url is None or remote.origin_of(state.manifest_url) != remote.origin_of(self.manifest_url):
            return None  # the recorded answer came from another service
        return True

    @property
    def current_confirmed(self) -> bool:
        """Whether the recorded current release is the configured service's latest answer (class docstring): recorded
        from it, and no later attempt to it failed (a failure against another service says nothing about this one)."""

        state = self.state
        return (state.server_current is not None and self.reachable is not False
                and not failure_concerns(state.last_check_error, self.manifest_url)
                and state.manifest_url == self.manifest_url)

    @property
    def server_current(self) -> dict[str, Any] | None:
        """The service's current release while it is confirmed (``current_confirmed``), else None."""

        return self.state.server_current if self.current_confirmed else None

    @property
    def upgrade_required(self) -> bool:
        """Whether the configured service's confirmed current release needs a newer os-optics (never from a stale
        or other service's answer, which stays under ``service.last_known``)."""

        current = self.server_current
        return current is not None and current.get("schema_version") not in SUPPORTED_SCHEMA_VERSIONS \
            and not is_retired_schema(current.get("schema_version"))

    @property
    def up_to_date(self) -> bool | None:
        """Whether the active release is the service's current one (None while that is not confirmed)."""

        current = self.server_current
        if current is None:
            return None
        active = self.active
        return active is not None and active.catalog_version == current.get("catalog_version") and active.sha256 == current.get("sha256")

    def as_dict(self) -> dict[str, Any]:
        active = self.active
        state = self.state
        reachable = self.service_reachable
        confirmed = self.current_confirmed
        last_known = None
        if state.server_current is not None and not confirmed:
            last_known = {"manifest_url": state.manifest_url, "current": state.server_current, "as_of": utc_text(state.contacted_at)}
        return {
            "schema": STATUS_SCHEMA,
            "schema_version": STATUS_SCHEMA_VERSION,
            "store": self.store,
            # a release this Engine reads (an active release in an earlier catalog schema is listed, not available); with
            # releases stored and none active, the newest one it reads, which the next unpinned use makes active
            "available": state.usable_active or newest_stored_note(state) is not None,
            "active": active.as_dict() if active is not None else None,
            "active_basis": state.active_basis if active is not None else None,
            "releases": [state.releases[version].as_dict() for version in state.stored_versions()],
            "supported_schema_versions": list(SUPPORTED_SCHEMA_VERSIONS),
            "service": {
                "manifest_url": self.manifest_url,
                "checked_by_this_command": self.checked_now,
                "reachable": reachable,
                "reachable_as_of": utc_text(state.checked_at) if reachable is not None else None,
                "current": state.server_current if confirmed else None,
                "current_as_of": utc_text(state.contacted_at) if confirmed else None,
                "last_contact_at": utc_text(state.contacted_at),
                "checked_at": utc_text(state.checked_at),
                "last_check_error": state.last_check_error,
                "last_known": last_known,
            },
            "up_to_date": self.up_to_date,
            "upgrade_required": self.upgrade_required,
            "automatic_check_interval_s": self.automatic_check_interval,
            "automatic_checks": self.automatic_check_interval > 0 and network_available(),
            "notes": list(self.notes),
            "problems": list(self.problems),
        }


def failure_concerns(error: dict[str, Any] | None, manifest_url: str | None) -> bool:
    """Whether a recorded failed attempt (``last_check_error``) was an attempt to the service at ``manifest_url``: a
    failure against another service (``OPENSCI_API_URL`` changed since) says nothing about this one."""

    if error is None or manifest_url is None:
        return False
    attempted = (error.get("details") or {}).get("url")
    return attempted is None or remote.origin_of(attempted) == remote.origin_of(manifest_url)


def _manifest_url_or_none(environ: Any) -> str | None:
    try:
        return remote.manifest_url(_environ(environ))
    except CatalogError:
        return None


def retired_active_error(active: StoredRelease) -> CatalogError:
    """``CATALOG_NOT_AVAILABLE`` for an active release in an earlier catalog schema (kept from before an Engine
    upgrade): this Engine cannot use it, and the service's current release replaces it."""

    return CatalogError(
        CatalogErrorCode.CATALOG_NOT_AVAILABLE,
        f"the active public catalog {active.catalog_version} is written in catalog schema {active.schema_version}, an "
        f"earlier format this os-optics no longer reads (it reads schema {', '.join(SUPPORTED_SCHEMA_VERSIONS)}); "
        "download the current release with 'os-optics catalog sync' (or 'os-optics catalog import <catalog "
        "file>' on a computer without access to the catalog service)",
        {"catalog_version": active.catalog_version, "schema_version": active.schema_version, "retired": True,
         "next_steps": ["os-optics catalog sync"]})


def active_unknown_error(state: StoreState) -> CatalogError:
    """``CATALOG_ACTIVE_UNKNOWN`` for an index with stored releases and none active (module docstring)."""

    why = " (the store index was rebuilt from the release files; which release was active could not be recovered)" \
        if state.recovered_at is not None else ""
    reason = state.last_check_error or {}
    attempt = f"; the catalog service could not settle it (last attempt: {reason.get('code')}: {reason.get('message')})" if reason else ""
    stored = state.stored_versions()
    return CatalogError(CatalogErrorCode.CATALOG_ACTIVE_UNKNOWN,
                        f"no public optical catalog release is selected as the active one on this device{why}; stored releases: "
                        f"{', '.join(stored)}{attempt}; {CHOOSE_CATALOG_HELP}",
                        {"stored_releases": stored, "index_recovered_at": utc_text(state.recovered_at)})


def newest_stored_note(state: StoreState) -> str | None:
    """What an unpinned use will do with releases stored and none active: take the newest one this Engine reads
    (``None``: an active release exists, the store is empty, or this Engine reads none of them)."""

    newest = state.newest_readable() if state.active_unknown else None
    if newest is None:
        return None
    stored = state.stored_versions()
    others = f" (stored: {', '.join(stored)})" if len(stored) > 1 else ""
    return (f"no release is selected as the active one{others}: the next catalog or design command uses public catalog {newest}, "
            f"the newest stored release; 'os-optics catalog use <catalog_version>' selects another")


def _status(store: CatalogStore, state: StoreState, environ: Any, *, reachable: bool | None = None,
            problems: tuple[dict[str, Any], ...] = (), notes: tuple[str, ...] = ()) -> CatalogStatus:
    pending = newest_stored_note(state)
    if pending is not None:
        notes = notes + (pending,)
    elif state.active_unknown:
        problems = problems + (active_unknown_error(state).as_dict(),)
    active = state.active_release
    if active is not None and is_retired_schema(active.schema_version):
        problems = problems + (retired_active_error(active).as_dict(),)
    # the service configured now (the one the next check contacts), never a URL recorded by an earlier check
    report = CatalogStatus(str(store.root), _manifest_url_or_none(environ), state, check_interval(environ), reachable, problems, notes)
    if report.upgrade_required:  # only from the configured service's confirmed answer
        current = report.server_current or {}
        report = replace(report, problems=problems + ({
            "code": CatalogErrorCode.ENGINE_UPGRADE_REQUIRED.value,
            "message": f"the service's current public catalog {current.get('catalog_version')} uses catalog schema "
                       f"{current.get('schema_version')}, which this os-optics cannot read; upgrade os-optics to "
                       "use it (the stored release stays in use)"},))
    return report


def status(*, environ: Any = None) -> CatalogStatus:
    """The local store's state and the last known service state. Never uses the network."""

    store = CatalogStore.from_environment(environ)
    return _status(store, store.read_state(), environ)


def _require_network() -> None:
    if not network_available():
        raise CatalogError(CatalogErrorCode.CATALOG_SYNC_UNAVAILABLE,
                           "this runtime has no network access for the catalog; the host installs it (json_api.install_catalog)")


@dataclass(frozen=True)
class _Check:
    """One answer of the catalog service's manifest endpoint, to be merged into the store index (``_record_check``)."""

    manifest_url: str
    sent_etag: str | None
    etag: str | None
    release_path_template: str | None
    server_current: dict[str, Any] | None
    checked_at: float
    manifest: CatalogManifestV1 | None  # None: 304, the recorded current release is still the service's current one


def _fetch_manifest(state: StoreState, url: str, timeout: float, conditional: bool) -> _Check:
    """Ask the service for its manifest (conditionally when ``state`` recorded one from ``url``). No store access."""

    usable_etag = conditional and state.manifest_url == url and state.server_current is not None and state.release_path_template is not None
    sent = state.manifest_etag if usable_etag else None
    response = remote.fetch_manifest(url, etag=sent, timeout=timeout)
    if response.not_modified:
        return _Check(url, sent, response.etag, state.release_path_template, state.server_current, time.time(), None)
    manifest = response.manifest
    assert manifest is not None
    return _Check(url, sent, response.etag, manifest.release_path_template, _entry_dict(manifest.current), time.time(), manifest)


def _newer_check_recorded(state: StoreState, at: float) -> bool:
    return state.checked_at is not None and state.checked_at > at


def _record_check(state: StoreState, check: _Check) -> StoreState:
    """``state`` with what ``check`` learned -- unless a newer check is recorded, or (a 304) the recorded manifest is no
    longer the one the conditional request named."""

    if _newer_check_recorded(state, check.checked_at):
        return state
    if check.manifest is None:
        if state.manifest_etag != check.sent_etag:
            return state
        return replace(state, checked_at=check.checked_at, last_check_error=None, manifest_etag=check.etag,
                       contacted_at=check.checked_at)
    return replace(state, manifest_url=check.manifest_url, manifest_etag=check.etag, release_path_template=check.release_path_template,
                   server_current=check.server_current, checked_at=check.checked_at, last_check_error=None,
                   contacted_at=check.checked_at)


def _record_failure(state: StoreState, error: CatalogError, at: float) -> StoreState:
    if _newer_check_recorded(state, at):
        return state
    return replace(state, checked_at=at, last_check_error=error.as_dict())


def _ensure_release(store: CatalogStore, state: StoreState, url: str, entry: CatalogReleaseEntryV1, template: str, timeout: float,
                    *, activate_first: bool = False) -> bool:
    """Store release ``entry`` unless ``state`` already holds it intact (downloaded without the store lock; stored under
    it, against the current index; ``activate_first``: it becomes the active release when the store held no release,
    ``CatalogStore.add_release``). True when it was downloaded."""

    held = state.releases.get(entry.catalog_version)
    if held is not None and held.sha256 != entry.sha256:
        raise CatalogError(
            CatalogErrorCode.CATALOG_RELEASE_CONFLICT,
            f"the service publishes catalog {entry.catalog_version} with different content than the release this device "
            "holds under that version; the stored release was kept",
            {"catalog_version": entry.catalog_version, "stored_sha256": held.sha256, "published_sha256": entry.sha256},
        )
    if held is not None and store.file_intact(held):
        return False
    location = remote.release_url(url, template.replace("{catalog_version}", entry.catalog_version))
    data = remote.fetch_release(location, entry, timeout=timeout)
    store.add_release(data, source=SOURCE_SYNC, expected_sha256=entry.sha256, expected_size=entry.size_bytes,
                      expected_version=entry.catalog_version, activate_first=activate_first)
    return True


def _announce_download(store: CatalogStore, url: str, entry: CatalogReleaseEntryV1, outcome: str) -> None:
    """One line on stderr when a command downloaded a catalog release by itself: a person or an Agent sees that
    the network was used, from where and how much. Standard output (the JSON documents) is untouched."""

    notice = (f"os-optics: downloaded public optical catalog {entry.catalog_version} "
              f"({entry.size_bytes / BYTES_PER_MB:.1f} MB) from {remote.origin_of(url)} into {store.root}; {outcome}\n")
    try:
        sys.stderr.write(notice)
        sys.stderr.flush()
    except (OSError, ValueError, AttributeError):  # no usable stderr (a closed stream, an embedding host)
        pass

def _target(manifest: CatalogManifestV1 | None, server_current: dict[str, Any] | None, has_active: bool) -> CatalogReleaseEntryV1 | None:
    """The release to make active: the service's current one when this Engine can read it, else (only with no active
    release) the newest compatible retained release."""

    if manifest is None:  # 304: the recorded current release
        current = CatalogReleaseEntryV1.model_validate(server_current) if server_current else None
        return current if current is not None and current.schema_version in SUPPORTED_SCHEMA_VERSIONS else None
    if manifest.current.schema_version in SUPPORTED_SCHEMA_VERSIONS:
        return manifest.current
    if has_active:
        return None
    return next((entry for entry in manifest.retained if entry.schema_version in SUPPORTED_SCHEMA_VERSIONS), None)


def _refresh(store: CatalogStore, environ: Any, timeout: float, *, conditional: bool, automatic: bool = False) -> StoreState:
    url = remote.manifest_url(_environ(environ))
    state = store.read_state()
    check = _fetch_manifest(state, url, timeout, conditional)
    target = _target(check.manifest, check.server_current, state.usable_active)
    downloaded = False
    if target is not None and check.release_path_template is not None:
        downloaded = _ensure_release(store, state, url, target, check.release_path_template, timeout)
    fallback = target is not None and check.manifest is not None and target is not check.manifest.current

    def change(current: StoreState) -> StoreState:
        updated = _record_check(current, check)
        superseded = _newer_check_recorded(current, check.checked_at) and current.server_current != check.server_current
        if target is None or superseded or target.catalog_version not in updated.releases:
            return updated
        if fallback and updated.usable_active:  # the retained fallback applies to a store with no (readable) active release only
            return updated
        return updated.activated(target.catalog_version, "SERVICE_CURRENT")

    state = store.update_state(change)
    if downloaded and automatic and target is not None:
        _announce_download(store, url, target, "it is the active release now" if state.active == target.catalog_version
                           else "stored; the active release is unchanged")
    # this command's own answer (a concurrent check may have recorded another service's in the store meanwhile)
    if target is None and not state.usable_active and check.server_current is not None \
            and check.server_current.get("schema_version") not in SUPPORTED_SCHEMA_VERSIONS:
        current = check.server_current
        if is_retired_schema(current.get("schema_version")):
            raise CatalogError(CatalogErrorCode.CATALOG_NOT_AVAILABLE,
                               f"the service's public catalog {current.get('catalog_version')} uses catalog schema "
                               f"{current.get('schema_version')}, an earlier format this os-optics no longer reads; "
                               "no release this Engine reads is available from the service yet",
                               {"supported_schema_versions": list(SUPPORTED_SCHEMA_VERSIONS)})
        raise CatalogError(CatalogErrorCode.ENGINE_UPGRADE_REQUIRED,
                           f"the service's public catalog {current.get('catalog_version')} uses catalog schema {current.get('schema_version')}, "
                           f"which this os-optics cannot read; upgrade os-optics",
                           {"supported_schema_versions": list(SUPPORTED_SCHEMA_VERSIONS)})
    return state


def check(*, environ: Any = None, timeout: float = EXPLICIT_TIMEOUT_SECONDS) -> CatalogStatus:
    """Ask the service which release is current (nothing is downloaded): ``up_to_date``, ``upgrade_required`` and the
    service's current release in the returned status. A service that cannot be reached is reported, not raised."""

    _require_network()
    store = CatalogStore.from_environment(environ)
    state = store.read_state()
    try:
        check_ = _fetch_manifest(state, remote.manifest_url(_environ(environ)), timeout, conditional=True)
    except CatalogError as exc:
        at = time.time()
        state = store.update_state(lambda current: _record_failure(current, exc, at))
        return _status(store, state, environ, reachable=False, problems=(exc.as_dict(),))
    state = store.update_state(lambda current: _record_check(current, check_))
    return _status(store, state, environ, reachable=True)


def sync(catalog_version: str | None = None, *, environ: Any = None, timeout: float = EXPLICIT_TIMEOUT_SECONDS) -> CatalogStatus:
    """Bring the store up to date with the service now: download its current release when it is not on this device
    (verified by SHA-256 and size) and make it active. With ``catalog_version``, fetch that retained release instead
    (for reproducing a Project pinned to it): it becomes the active release only when no release is active (an explicit
    choice, e.g. after the store was lost); otherwise the active release stays and ``notes`` says how to select the
    fetched one (``catalog use``). Raises ``CatalogError`` when the service cannot be reached or refuses (the store is
    left as it was)."""

    _require_network()
    store = CatalogStore.from_environment(environ)
    if catalog_version is None:
        state = _refresh(store, environ, timeout, conditional=False)
        return _status(store, state, environ, reachable=True)
    store.release_file(catalog_version)  # a release label (CATALOG_INVALID otherwise)
    held = store.read_state().releases.get(catalog_version)
    contacted = held is None or not store.file_intact(held)  # an intact stored release is not fetched again
    fetch_release(catalog_version, store=store, environ=environ, timeout=timeout)
    activated: list[bool] = []

    def choose(current: StoreState) -> StoreState:
        if current.active is None and catalog_version in current.releases:
            activated.append(True)
            return current.activated(catalog_version)
        return current

    state = store.update_state(choose)
    if activated:
        note = f"public catalog {catalog_version} is the active release now (no release was active)"
    elif state.active == catalog_version:
        note = f"public catalog {catalog_version} is the active release"
    else:
        note = (f"public catalog {catalog_version} is stored for the Projects that pin it; the active release stays "
                f"{state.active} ('os-optics catalog use {catalog_version}' makes {catalog_version} the active one)")
    return _status(store, state, environ, reachable=True if contacted else None, notes=(note,))


def fetch_release(catalog_version: str, *, store: CatalogStore, environ: Any, timeout: float, automatic: bool = False) -> StoreState:
    """Store the retained release ``catalog_version`` from the service; an active release is never changed.
    ``automatic``: a command fetched it by itself (one line on stderr says so), and into a store that held no release
    it becomes the active release, as the first-use synchronization makes its release active."""

    store.release_file(catalog_version)  # a release label (CATALOG_INVALID otherwise)
    state = store.read_state()
    held = state.releases.get(catalog_version)
    if held is not None and store.file_intact(held):
        return state
    url = remote.manifest_url(_environ(environ))
    check_ = _fetch_manifest(state, url, timeout, conditional=False)
    manifest = check_.manifest
    assert manifest is not None
    entry = manifest.release(catalog_version)
    if entry is None:
        store.update_state(lambda current: _record_check(current, check_))
        raise CatalogError(CatalogErrorCode.CATALOG_RELEASE_NOT_FOUND,
                           f"the catalog service does not distribute public catalog {catalog_version}",
                           {"catalog_version": catalog_version, "retained": [e.catalog_version for e in manifest.retained]})
    check_compatible(entry.schema_version, entry.catalog_version)
    downloaded = _ensure_release(store, state, url, entry, manifest.release_path_template, timeout, activate_first=automatic)
    state = store.update_state(lambda current: _record_check(current, check_))
    if downloaded and automatic:
        _announce_download(store, url, entry, "it is the active release now" if state.active == catalog_version
                           else "it is used for the Project that pins it (the active release is unchanged)")
    return state


def import_release(source: Any, *, activate: bool = True, environ: Any = None) -> CatalogStatus:
    """Add a release from a file (path) or its bytes -- e.g. for an offline device -- after full validation, and make
    it active (unless ``activate=False``). A different document under a version already held is refused."""

    if isinstance(source, (bytes, bytearray, memoryview)):
        data = bytes(source)
    else:
        try:
            data = Path(source).read_bytes()
        except OSError as exc:
            raise CatalogError(CatalogErrorCode.CATALOG_INVALID, f"cannot read the catalog file: {exc}") from None
    store = CatalogStore.from_environment(environ)
    state, _ = store.add_release(data, source=SOURCE_IMPORT, activate=activate)
    return _status(store, state, environ)


def use_release(catalog_version: str, *, environ: Any = None) -> CatalogStatus:
    """Make the stored release ``catalog_version`` the active one (no network). Refused with ``CATALOG_NOT_AVAILABLE``
    when it is not stored (``os-optics catalog sync --release`` fetches a retained release) and with
    ``CATALOG_INTEGRITY_FAILED`` when its stored file is damaged. The next catalog check against the service moves the
    active release to the service's current one again, as after an import."""

    store = CatalogStore.from_environment(environ)
    store.release_file(catalog_version)  # a release label (CATALOG_INVALID otherwise)

    def change(current: StoreState) -> StoreState:
        held = current.releases.get(catalog_version)
        if held is None:
            stored = current.stored_versions()
            then = "it becomes the active release" if current.active is None \
                else f"then run 'os-optics catalog use {catalog_version}' to make it the active one"
            raise CatalogError(CatalogErrorCode.CATALOG_NOT_AVAILABLE,
                               f"public catalog {catalog_version} is not stored on this device (stored: {', '.join(stored) or 'none'}); "
                               f"download it with 'os-optics catalog sync --release {catalog_version}' ({then}), or add a "
                               "release file with 'os-optics catalog import <catalog file>'. 'os-optics catalog "
                               "sync' alone downloads the service's current release and makes it active",
                               {"catalog_version": catalog_version, "stored_releases": stored})
        check_compatible(held.schema_version, held.catalog_version)  # never make an unreadable release the active one
        store.read_release(held)
        return current.activated(catalog_version)

    state = store.update_state(change)
    return _status(store, state, environ)


def verify(*, environ: Any = None, deep: bool = False) -> dict[str, Any]:
    """Re-check every stored release against its recorded SHA-256 (``deep``: also re-validate its content). Never uses
    the network. ``ok`` is False when any stored release is damaged; ``os-optics catalog sync`` repairs a damaged
    current release by downloading it again."""

    store = CatalogStore.from_environment(environ)
    state = store.read_state()
    rows = []
    for version in state.stored_versions():
        release = state.releases[version]
        row: dict[str, Any] = {"catalog_version": version, "sha256": release.sha256, "ok": True}
        try:
            data = store.read_release(release)
            if deep and is_retired_schema(release.schema_version) and version != state.active:
                # kept from before an Engine upgrade and no longer used: its integrity is checked, its content is not
                row["note"] = (f"catalog schema {release.schema_version}: an earlier format this os-optics no longer "
                               "reads; kept on this device, not used")
            elif deep:
                validate_release(data)
        except CatalogError as exc:
            row.update(ok=False, problem=exc.as_dict())
        rows.append(row)
    problems = [row["problem"] for row in rows if not row["ok"]]
    if state.active_unknown and state.newest_readable() is None:
        problems.append(active_unknown_error(state).as_dict())
    elif state.active_unknown:
        pass  # (an unpinned use takes the newest stored release: ``release_bytes``)
    elif state.active is None:
        problems.append({"code": CatalogErrorCode.CATALOG_NOT_AVAILABLE.value, "message": f"no active catalog release; {GET_CATALOG_HELP}"})
    return {"schema": VERIFY_SCHEMA, "schema_version": STATUS_SCHEMA_VERSION, "store": str(store.root), "ok": not problems,
            "active": state.active, "releases": rows, "problems": problems}


def _heartbeat(store: CatalogStore, state: StoreState, environ: Any) -> StoreState:
    """The automatic check of a catalog-consuming entry point (module docstring); never raises for a network failure."""

    if not automatic_access(environ):
        return state
    since = time.time() - state.checked_at if state.checked_at is not None else None
    if not state.usable_active:  # no (readable) active release: try on first use, and again a minute after a failed
        # attempt (at once when that attempt was to another service)
        due = (since is None or state.last_check_error is None or since >= EMPTY_STORE_RETRY_SECONDS
               or not failure_concerns(state.last_check_error, _manifest_url_or_none(environ)))
    else:
        due = since is None or since >= check_interval(environ)
    if not due:
        return state
    try:
        return _refresh(store, environ, AUTOMATIC_TIMEOUT_SECONDS, conditional=True, automatic=True)
    except CatalogError as exc:
        at = time.time()
        try:
            return store.update_state(lambda current: _record_failure(current, exc, at))
        except CatalogError:  # an unwritable store: the next use checks again
            return replace(state, last_check_error=exc.as_dict())


def _use_newest_stored(store: CatalogStore, state: StoreState) -> StoreState:
    """Releases are stored and none is active (a rebuilt index, imports without activation), and the automatic check did
    not settle it: the newest stored release this Engine reads becomes the active one (``active_basis``
    ``NEWEST_STORED``), announced once on stderr. With one stored release there is nothing to choose; with several the
    choice is stated, ``catalog use`` selects another, and the next successful check makes the service's current release
    active as it always does. A store none of whose releases this Engine reads is left as it is
    (``CATALOG_ACTIVE_UNKNOWN``)."""

    chosen: list[str] = []

    def choose(current: StoreState) -> StoreState:
        newest = current.newest_readable() if current.active_unknown else None
        if newest is None:
            return current
        chosen.append(newest)
        return current.activated(newest, "NEWEST_STORED")

    try:
        state = store.update_state(choose)
    except CatalogError:  # an unwritable store: this use still runs on that release, and the next use chooses again
        newest = state.newest_readable()
        return state if newest is None else state.activated(newest, "NEWEST_STORED")
    if chosen:
        stored = state.stored_versions()
        among = f" of the stored releases {', '.join(stored)}" if len(stored) > 1 else ""
        notice = (f"os-optics: no public optical catalog release was selected as the active one in {store.root}; using "
                  f"{chosen[0]}, the newest{among} (select another with 'os-optics catalog use <catalog_version>')\n")
        try:
            sys.stderr.write(notice)
            sys.stderr.flush()
        except (OSError, ValueError, AttributeError):  # no usable stderr (a closed stream, an embedding host)
            pass
    return state


def _not_available(state: StoreState, catalog_version: str | None, environ: Any) -> CatalogError:
    if catalog_version is None and state.active_unknown:
        return active_unknown_error(state)
    reason = state.last_check_error or {}
    why = f" (last attempt: {reason.get('code')}: {reason.get('message')})" if reason else ""
    if not network_available():
        how = "install one with json_api.install_catalog"
    elif check_interval(environ) == 0:
        how = f"automatic catalog access is off ({ENV_CHECK_INTERVAL}=0); {GET_CATALOG_HELP}"
    else:
        how = GET_CATALOG_HELP
    if catalog_version is not None:
        if network_available() and check_interval(environ) == 0:
            why = f" (automatic catalog access is off: {ENV_CHECK_INTERVAL}=0)"
        return _pinned_not_available(state, catalog_version, why)
    return CatalogError(CatalogErrorCode.CATALOG_NOT_AVAILABLE, f"no public optical catalog is available on this device{why}; {how}")


def _pinned_not_available(state: StoreState, catalog_version: str, why: str) -> CatalogError:
    """``CATALOG_NOT_AVAILABLE`` for a Project pinned to a release this device does not hold. When the service
    said it does not distribute that release, fetching it cannot work: the Project moves to the current release."""

    reason = state.last_check_error or {}
    update = ["os-optics project update-catalog PROJECT --out PROJECT",
              "os-optics project run PROJECT --out-dir RUN_DIR"]
    if state.active is None:
        update.insert(0, "os-optics catalog sync")
    move = (f"move the Project to the current catalog: {', then '.join(repr(c) for c in update)}; update-catalog lists the "
            f"components whose product data it cannot compare without {catalog_version} as unverified and says what to do "
            "with each (next_steps)")
    if reason.get("code") == CatalogErrorCode.CATALOG_RELEASE_NOT_FOUND.value:
        message = (f"public catalog {catalog_version} (pinned by this Project) is not on this device and the catalog service "
                   f"no longer distributes it; {move}")
        steps = update
    else:
        message = (f"public catalog {catalog_version} (pinned by this Project) is not on this device and could not be "
                   f"obtained{why}; when the catalog service is reachable, 'os-optics catalog sync --release "
                   f"{catalog_version}' downloads it (or 'os-optics catalog import <catalog file>' adds it from a "
                   f"file); or {move}")
        steps = [f"os-optics catalog sync --release {catalog_version}", *update]
    return CatalogError(CatalogErrorCode.CATALOG_NOT_AVAILABLE, message,
                        {"catalog_version": catalog_version, "last_attempt": reason.get("code"), "next_steps": steps})


def _pinned_unreadable(refusal: CatalogError) -> CatalogError:
    """``CATALOG_NOT_AVAILABLE`` for a Project pinned to a release in an earlier catalog schema: no download or retry can
    make this Engine read it, so the Project moves to the current release."""

    version = refusal.details.get("catalog_version")
    steps = ["os-optics project update-catalog PROJECT --out PROJECT",
             "os-optics project run PROJECT --out-dir RUN_DIR"]
    return CatalogError(
        CatalogErrorCode.CATALOG_NOT_AVAILABLE,
        f"public catalog {version} (pinned by this Project) is written in catalog schema {refusal.details.get('schema_version')}, "
        f"an earlier format this os-optics no longer reads; move the Project to the current catalog: "
        f"{', then '.join(repr(c) for c in steps)} (its components are moved and listed as unverified: their product data "
        "cannot be compared with the old release)",
        {**refusal.details, "last_attempt": RELEASE_SCHEMA_RETIRED, "next_steps": steps})


#: ``last_attempt`` of a pinned release refused because it is in an earlier catalog schema.
RELEASE_SCHEMA_RETIRED = "CATALOG_SCHEMA_RETIRED"


def release_bytes(catalog_version: str | None = None, *, environ: Any = None) -> tuple[StoredRelease, bytes]:
    """The stored release a local run uses, and its exact bytes: ``catalog_version`` (fetched when missing and
    automatic access is on), else the active release (after the automatic check when one is due). Raises
    ``CatalogError`` ``CATALOG_NOT_AVAILABLE`` when there is none (or it is in an earlier catalog schema this Engine no
    longer reads) and ``ENGINE_UPGRADE_REQUIRED`` when it is in a newer one."""

    store = CatalogStore.from_environment(environ)
    state = store.read_state()
    if catalog_version is not None:
        held = state.releases.get(catalog_version)
        if held is None and automatic_access(environ):
            try:
                state = fetch_release(catalog_version, store=store, environ=environ, timeout=AUTOMATIC_TIMEOUT_SECONDS,
                                      automatic=True)
            except CatalogError as exc:
                if exc.details.get("retired"):  # the service has it, in a format this Engine no longer reads
                    raise _pinned_unreadable(exc) from None
                state = replace(state, last_check_error=exc.as_dict())
            held = state.releases.get(catalog_version)
        if held is None:
            raise _not_available(state, catalog_version, environ)
        try:
            check_compatible(held.schema_version, held.catalog_version)
        except CatalogError as exc:
            raise (_pinned_unreadable(exc) if exc.details.get("retired") else exc) from None
        return held, store.read_release(held)
    state = _heartbeat(store, state, environ)
    if state.active_unknown:
        state = _use_newest_stored(store, state)
    active = state.active_release
    if active is None:
        raise _not_available(state, None, environ)
    check_compatible(active.schema_version, active.catalog_version)
    return active, store.read_release(active)


# ------------------------------------------------------------------------------------------------ mechanical catalog


def _mechanical_store(environ: Any) -> MechanicalStore:
    return MechanicalStore.from_environment(_environ(environ))


def _mechanical_record(store: MechanicalStore, version: str | None) -> dict[str, Any] | None:
    record = store.state()["stored"].get(version) if version is not None else None
    return None if record is None else {"mechanical_version": version, **record}


def mechanical_status(*, environ: Any = None) -> dict[str, Any]:
    """The mechanical releases on this device (no network): the active one, every stored one and the last check."""

    store = _mechanical_store(environ)
    state = store.state()
    return {"schema": MECHANICAL_STATUS_SCHEMA, "schema_version": STATUS_SCHEMA_VERSION, "store": str(store.root),
            "active": _mechanical_record(store, state["active"]),
            "stored": [_mechanical_record(store, version) for version in sorted(state["stored"])],
            "last_check": state["last_check"]}


def _mechanical_transient(error: CatalogError) -> bool:
    """A failure that says nothing about the mechanical release: no answer, a timeout, or a ``5xx`` (``UNAVAILABLE``)."""

    if error.code is CatalogErrorCode.CATALOG_SERVICE_UNREACHABLE:
        return True
    status = error.details.get("http_status")
    return error.code is CatalogErrorCode.CATALOG_SERVICE_ERROR and isinstance(status, int) and status >= HTTP_SERVER_ERROR


def _mechanical_manifest(environ: Any, timeout: float) -> tuple[str | None, Any]:
    """``(manifest URL, manifest)`` of the configured service: ``(None, None)`` for a mirror's manifest URL (no
    mechanical route), ``(url, None)`` when the service answered 404 (not offered)."""

    url = remote.mechanical_manifest_url(remote.manifest_url(_environ(environ)))
    if url is None:
        return None, None
    return url, remote.fetch_mechanical_manifest(url, timeout=timeout)


def _mechanical_failure(block: dict[str, Any], error: CatalogError | Exception) -> dict[str, Any]:
    transient = isinstance(error, CatalogError) and _mechanical_transient(error)
    detail = error.as_dict() if hasattr(error, "as_dict") else {"code": type(error).__name__, "message": str(error)}
    return {**block, "outcome": MECHANICAL_UNAVAILABLE if transient else MECHANICAL_FAILED, "error": detail}


def _announce_mechanical_unavailable(block: dict[str, Any]) -> None:
    """One line on stderr when the mechanical catalog could not be checked (the optical result is unaffected)."""

    active = block.get("active") or "none"
    error = block.get("error") or {}
    notice = (f"os-optics: the public mechanical catalog could not be checked ({error.get('code')}); the stored "
              f"mechanical release ({active}) stays in use\n")
    try:
        sys.stderr.write(notice)
        sys.stderr.flush()
    except (OSError, ValueError, AttributeError):  # no usable stderr (a closed stream, an embedding host)
        pass


def check_mechanical(*, environ: Any = None, timeout: float = EXPLICIT_TIMEOUT_SECONDS) -> dict[str, Any]:
    """Ask the service for its mechanical manifest (nothing downloaded): ``outcome`` ``CHECKED`` (``current``,
    ``up_to_date``), ``NOT_OFFERED``, ``UNAVAILABLE`` or ``FAILED`` (``error``). Never raises for the service's answer."""

    _require_network()
    store = _mechanical_store(environ)
    active = store.active_version()
    block: dict[str, Any] = {"offered": None, "manifest_url": None, "active": active, "current": None, "up_to_date": None}
    try:
        url, manifest = _mechanical_manifest(environ, timeout)
    except CatalogError as exc:
        block = _mechanical_failure(block, exc)
    else:
        block["manifest_url"] = url
        if manifest is None:
            block.update(offered=False, outcome=MECHANICAL_NOT_OFFERED)
        else:
            current = manifest.current.model_dump(mode="json")
            held = store.state()["stored"].get(active) if active else None
            block.update(offered=True, outcome=MECHANICAL_CHECKED, current=current,
                         up_to_date=active == current["mechanical_version"] and held is not None
                         and held["sha256"] == current["sha256"])
    store.record_check({"checked_at": utc_text(time.time()), "outcome": block["outcome"], "manifest_url": block["manifest_url"],
                        "current": block["current"]})
    if block["outcome"] == MECHANICAL_UNAVAILABLE:
        _announce_mechanical_unavailable(block)
    return block


def sync_mechanical(*, environ: Any = None, timeout: float = EXPLICIT_TIMEOUT_SECONDS) -> dict[str, Any]:
    """The mechanical part of ``catalog sync`` (module docstring): ``{offered, mechanical_version, sha256, outcome,
    active, error?}`` with outcome ``DOWNLOADED`` / ``ALREADY_STORED`` (either way the service's current release is the
    active one now), ``NOT_OFFERED``, ``UNAVAILABLE`` or ``FAILED`` (integrity or validation; nothing stored). Never
    raises for the service's answer."""

    _require_network()
    store = _mechanical_store(environ)
    block: dict[str, Any] = {"offered": None, "mechanical_version": None, "sha256": None, "active": store.active_version()}
    try:
        url, manifest = _mechanical_manifest(environ, timeout)
        if manifest is None:
            block.update(offered=False, outcome=MECHANICAL_NOT_OFFERED)
        else:
            entry = manifest.current
            block.update(offered=True, mechanical_version=entry.mechanical_version, sha256=entry.sha256)
            held = store.state()["stored"].get(entry.mechanical_version)
            intact = False
            if held is not None and held["sha256"] == entry.sha256:
                try:
                    intact = store.release(entry.mechanical_version, expected_sha256=entry.sha256) is not None
                except ValueError:  # MechanicalCatalogError (a damaged file): the release is downloaded again below
                    intact = False
            if intact:
                store.update_state(lambda state: {**state, "active": entry.mechanical_version})
                block["outcome"] = MECHANICAL_ALREADY_STORED
            else:
                location = remote.release_url(url, manifest.release_path(entry.mechanical_version))
                data = remote.fetch_release(location, entry, timeout=timeout)
                store.add_release(data, source=MECHANICAL_SOURCE_SYNC, expected_sha256=entry.sha256,
                                  expected_size=entry.size_bytes, expected_version=entry.mechanical_version, activate=True)
                block["outcome"] = MECHANICAL_DOWNLOADED
    except CatalogError as exc:
        block = _mechanical_failure(block, exc)
    except ValueError as exc:  # MechanicalCatalogError: the downloaded document is not a valid mechanical catalog
        block = _mechanical_failure(block, exc)
    block["active"] = store.active_version()
    store.record_check({"checked_at": utc_text(time.time()), "outcome": block["outcome"],
                        "mechanical_version": block["mechanical_version"], "sha256": block["sha256"]})
    if block["outcome"] == MECHANICAL_UNAVAILABLE:
        _announce_mechanical_unavailable(block)
    return block


def is_mechanical_document(data: bytes) -> bool:
    """Whether ``data`` is a JSON document that names the public mechanical catalog schema (``catalog import``)."""

    from ..assembly_plan.mechanical.model import MECHANICAL_CATALOG_SCHEMA_ID

    try:
        document = json.loads(data)
    except (UnicodeDecodeError, ValueError, RecursionError):
        return False
    return isinstance(document, dict) and document.get("schema") == MECHANICAL_CATALOG_SCHEMA_ID


def import_mechanical(source: Any, *, activate: bool = True, environ: Any = None) -> dict[str, Any]:
    """Store an OPENSCI mechanical catalog document from a file (path) or its bytes and make it the active one (unless
    ``activate=False``; a store with none active takes it anyway). A USER document is refused
    (``MechanicalCatalogError``): pass it with ``--mechanical FILE``."""

    data = bytes(source) if isinstance(source, (bytes, bytearray, memoryview)) else Path(source).read_bytes()
    store = _mechanical_store(environ)
    state, loaded = store.add_release(data, source=MECHANICAL_SOURCE_IMPORT, activate=activate)
    return {"status": "IMPORTED", "mechanical_version": loaded.document.mechanical_version, "sha256": loaded.sha256,
            "size_bytes": loaded.size_bytes, "active": state["active"]}


def verify_mechanical(*, environ: Any = None) -> dict[str, Any]:
    """Every stored mechanical release re-hashed and re-validated (no network): ``{ok, active, releases}``."""

    store = _mechanical_store(environ)
    rows = store.verify()
    return {"ok": all(row["ok"] for row in rows), "active": store.active_version(), "releases": rows}


def mark_release_validated(release: StoredRelease, *, environ: Any = None) -> None:
    """Record in the store index that this Engine's catalog contract validated the whole document of ``release``
    (``CatalogStore.mark_validated``)."""

    CatalogStore.from_environment(environ).mark_validated(release)


__all__ = [
    "AUTOMATIC_TIMEOUT_SECONDS",
    "DEFAULT_CHECK_INTERVAL_SECONDS",
    "ENV_CHECK_INTERVAL",
    "EXPLICIT_TIMEOUT_SECONDS",
    "RELEASE_SCHEMA_RETIRED",
    "CatalogStatus",
    "active_unknown_error",
    "automatic_access",
    "check",
    "check_interval",
    "check_mechanical",
    "fetch_release",
    "import_mechanical",
    "import_release",
    "is_mechanical_document",
    "mark_release_validated",
    "mechanical_status",
    "network_available",
    "newest_stored_note",
    "release_bytes",
    "sha256_hex",
    "status",
    "sync",
    "sync_mechanical",
    "use_release",
    "verify",
    "verify_mechanical",
]
