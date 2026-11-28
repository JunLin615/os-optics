"""HTTP access to the catalog service (standard library ``urllib`` only; never inside the browser runtime).

* ``GET <origin>/api/v1/catalog``: the ``CatalogManifestV1`` (conditional with ``If-None-Match``: ``304`` = unchanged);
* ``GET <release path>``: the exact bytes of one release (``gzip`` accepted), checked against the manifest's SHA-256
  and size before anything is stored;
* ``GET <origin>/api/v1/catalog/mechanical``: the public mechanical catalog manifest
  (``opensci.public_mechanical_catalog.manifest``), when the service offers one (``404``: not offered, never an error),
  and ``GET <its release path>``: the exact bytes of one mechanical release. A mirror's manifest URL (any other path)
  names no mechanical manifest: there the mechanical catalog comes from ``catalog import`` or ``--mechanical FILE``.

The service origin is ``OPENSCI_CATALOG_URL``, else ``OPENSCI_API_URL``, else the OpenSci service named in the packaged
deployment defaults (``os_optics/deployment.toml``, see :mod:`os_optics.deployment`). A value that is a
bare origin (optionally followed by ``/api/v1``) means the service's manifest at ``/api/v1/catalog``; a value with any
other path is the manifest URL itself (e.g. a mirror's ``.../manifest.json``). Plain ``http`` is accepted for this
device (loopback) only. These requests carry no credential.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
import zlib
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from .._vendor.shared_contracts.design_catalog import CatalogManifestV1
from .._version import DISTRIBUTION_NAME, __version__
from ..deployment import default_service_origin
from .errors import CatalogError, CatalogErrorCode

ENV_CATALOG_URL = "OPENSCI_CATALOG_URL"
ENV_API_URL = "OPENSCI_API_URL"
API_PREFIX = "/api/v1"
MANIFEST_PATH = API_PREFIX + "/catalog"
MECHANICAL_MANIFEST_PATH = MANIFEST_PATH + "/mechanical"
USER_AGENT = f"{DISTRIBUTION_NAME}/{__version__}"
LOOPBACK_NAMES = frozenset({"localhost"})
HTTP_OK = 200
HTTP_NOT_MODIFIED = 304
HTTP_NOT_FOUND = 404
#: The largest manifest accepted (a manifest lists a handful of releases).
MAX_MANIFEST_BYTES = 1 << 20
#: gzip framing for zlib (header + trailer).
GZIP_WBITS = 31
MAX_REASON_CHARS = 300

logger = logging.getLogger("os_optics.catalog")


def _is_loopback(host: str) -> bool:
    name = host.strip("[]").lower()
    if name in LOOPBACK_NAMES or name.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def manifest_url(environ: Any) -> str:
    """The manifest URL from ``OPENSCI_CATALOG_URL`` / ``OPENSCI_API_URL`` / the deployment default (module docstring)."""

    variable = next((name for name in (ENV_CATALOG_URL, ENV_API_URL) if str(environ.get(name, "") or "").strip()), None)
    raw = str(environ.get(variable, "")).strip() if variable else default_service_origin()

    def invalid(reason: str) -> CatalogError:
        return CatalogError(CatalogErrorCode.CATALOG_ORIGIN_INVALID, f"the catalog service URL is not usable: {reason}",
                            {"variable": variable})

    parts = urllib.parse.urlsplit(raw)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise invalid("expected http(s)://host[:port][/path]")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise invalid("it must not carry credentials, a query or a fragment")
    if parts.scheme == "http" and not _is_loopback(parts.hostname):
        raise invalid("plain http is accepted for this device (localhost) only; use https")
    try:
        port = parts.port
    except ValueError:
        raise invalid("the port is not a number") from None
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    origin = f"{parts.scheme}://{host}" + (f":{port}" if port is not None else "")
    path = parts.path.rstrip("/")
    return origin + (MANIFEST_PATH if path in ("", API_PREFIX) else parts.path)


@dataclass(frozen=True)
class ManifestResponse:
    """A manifest request's outcome: ``manifest`` (None when ``not_modified``) and the response ``etag``."""

    not_modified: bool
    manifest: CatalogManifestV1 | None
    etag: str | None


def _opener(url: str) -> urllib.request.OpenerDirector:
    host = urllib.parse.urlsplit(url).hostname or ""
    handlers: list[Any] = [urllib.request.ProxyHandler({})] if _is_loopback(host) else []
    return urllib.request.build_opener(*handlers)


def _get(url: str, headers: dict[str, str], timeout: float, limit: int) -> tuple[int, dict[str, str], bytes]:
    """``(status, lower-cased headers, body)`` of one GET; ``CATALOG_SERVICE_UNREACHABLE`` when nothing came back."""

    request = urllib.request.Request(url, method="GET", headers={"User-Agent": USER_AGENT, **headers})
    try:
        with _opener(url).open(request, timeout=timeout) as response:
            return response.status, {k.lower(): v for k, v in response.headers.items()}, response.read(limit + 1)
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read(MAX_MANIFEST_BYTES)
        except OSError:
            body = b""
        exc.close()
        return exc.code, {k.lower(): v for k, v in (exc.headers or {}).items()}, body
    except (urllib.error.URLError, OSError, ValueError) as exc:
        reason = type(exc).__name__ + (f": {exc.reason}" if isinstance(exc, urllib.error.URLError) else f": {exc}")
        logger.debug("GET %s -> unreachable", urllib.parse.urlsplit(url).path)
        raise CatalogError(CatalogErrorCode.CATALOG_SERVICE_UNREACHABLE, "the catalog service could not be reached",
                           {"url": url, "reason": reason[:MAX_REASON_CHARS]}) from None


def _service_error(url: str, status: int, body: bytes, *, release: bool) -> CatalogError:
    code = None
    try:
        document = json.loads(body) if body else None
        if isinstance(document, dict) and isinstance(document.get("error"), dict):
            code = document["error"].get("code")
    except (UnicodeDecodeError, ValueError, RecursionError):
        pass
    if status == HTTP_NOT_FOUND and release:
        return CatalogError(CatalogErrorCode.CATALOG_RELEASE_NOT_FOUND, "the catalog service does not distribute this catalog release",
                            {"url": url, "http_status": status, "service_code": code})
    return CatalogError(CatalogErrorCode.CATALOG_SERVICE_ERROR, f"the catalog service answered HTTP {status}",
                        {"url": url, "http_status": status, "service_code": code})


def fetch_manifest(url: str, *, etag: str | None, timeout: float) -> ManifestResponse:
    """The catalog manifest (conditional on ``etag``)."""

    headers = {"Accept": "application/json"}
    if etag:
        headers["If-None-Match"] = etag
    status, response_headers, body = _get(url, headers, timeout, MAX_MANIFEST_BYTES)
    logger.debug("GET %s -> %s", urllib.parse.urlsplit(url).path, status)
    if status == HTTP_NOT_MODIFIED and etag:
        return ManifestResponse(True, None, response_headers.get("etag") or etag)
    if status != HTTP_OK:
        raise _service_error(url, status, body, release=False)
    if len(body) > MAX_MANIFEST_BYTES:
        raise CatalogError(CatalogErrorCode.CATALOG_SERVICE_ERROR, "the catalog manifest is too large", {"url": url})
    try:
        manifest = CatalogManifestV1.model_validate_json(body)
    except ValidationError as exc:
        raise CatalogError(CatalogErrorCode.CATALOG_SERVICE_ERROR, "the catalog service answered with an invalid catalog manifest",
                           {"url": url, "problems": [e["msg"] for e in exc.errors()[:3]]}) from None
    return ManifestResponse(False, manifest, response_headers.get("etag"))


def origin_of(url: str) -> str:
    """``scheme://host[:port]`` of a service URL (what a notice names), else the URL itself."""

    parts = urllib.parse.urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else url


def release_url(manifest_location: str, path: str) -> str:
    """A release path resolved against the manifest URL, like a link."""

    return urllib.parse.urljoin(manifest_location, path)


def fetch_release(url: str, entry: Any, *, timeout: float) -> bytes:
    """The exact bytes of one release (decoded from ``gzip`` when the service compressed them), at most
    ``entry.size_bytes`` long (``entry``: a ``CatalogReleaseEntryV1`` or a mechanical ``MechanicalReleaseEntryV1``); the
    caller verifies their SHA-256 before storing them."""

    status, headers, body = _get(url, {"Accept": "application/json", "Accept-Encoding": "gzip"}, timeout, entry.size_bytes * 2 + MAX_MANIFEST_BYTES)
    logger.debug("GET %s -> %s", urllib.parse.urlsplit(url).path, status)
    if status != HTTP_OK:
        raise _service_error(url, status, body, release=True)
    if "gzip" in headers.get("content-encoding", "").lower():
        decoder = zlib.decompressobj(GZIP_WBITS)
        try:
            body = decoder.decompress(body, entry.size_bytes + 1)
        except zlib.error as exc:
            raise CatalogError(CatalogErrorCode.CATALOG_INTEGRITY_FAILED, f"the catalog download is not valid gzip: {exc}", {"url": url}) from None
    return body


def mechanical_manifest_url(manifest_location: str) -> str | None:
    """The mechanical manifest URL of the service whose optical manifest is ``manifest_location``: ``<origin>/api/v1/
    catalog/mechanical`` when that is the service's own manifest route, else None (a mirror's manifest file names none)."""

    parts = urllib.parse.urlsplit(manifest_location)
    if parts.path.rstrip("/") != MANIFEST_PATH or not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}{MECHANICAL_MANIFEST_PATH}"


def fetch_mechanical_manifest(url: str, *, timeout: float) -> Any:
    """The service's ``MechanicalCatalogManifestV1``, or None when it answers ``404`` (the service does not offer the
    mechanical catalog). Any other answer that is not a valid manifest raises ``CATALOG_SERVICE_ERROR`` (with
    ``details.http_status``), and a request that got no answer ``CATALOG_SERVICE_UNREACHABLE``."""

    from ..assembly_plan.mechanical.model import MechanicalCatalogManifestV1

    status, _, body = _get(url, {"Accept": "application/json"}, timeout, MAX_MANIFEST_BYTES)
    logger.debug("GET %s -> %s", urllib.parse.urlsplit(url).path, status)
    if status == HTTP_NOT_FOUND:
        return None
    if status != HTTP_OK:
        raise _service_error(url, status, body, release=False)
    if len(body) > MAX_MANIFEST_BYTES:
        raise CatalogError(CatalogErrorCode.CATALOG_SERVICE_ERROR, "the mechanical catalog manifest is too large", {"url": url})
    try:
        return MechanicalCatalogManifestV1.model_validate_json(body)
    except ValidationError as exc:
        raise CatalogError(CatalogErrorCode.CATALOG_SERVICE_ERROR,
                           "the catalog service answered with an invalid mechanical catalog manifest",
                           {"url": url, "http_status": status, "problems": [e["msg"] for e in exc.errors()[:3]]}) from None
