"""A local stand-in for the catalog distribution API (``http.server`` on 127.0.0.1) for ``os_optics.catalog`` tests.

Routes (as the commercial API serves them): ``GET /api/v1/catalog`` -> ``CatalogManifestV1`` with a strong ``ETag``
(the SHA-256 of the body) and ``If-None-Match`` -> 304; ``GET /api/v1/catalog/releases/<catalog_version>`` -> the exact
release bytes (gzip ``Content-Encoding`` when the client accepts it and ``gzip`` is on), 404 for an unknown version.
Every request is recorded (path, ``If-None-Match``, ``Accept-Encoding``).

The public mechanical catalog: ``GET /api/v1/catalog/mechanical`` -> ``MechanicalCatalogManifestV1`` once a mechanical
release is published (``publish_mechanical``), else ``404`` (not offered); ``mechanical_status`` other than 200 answers
that status instead; ``GET /api/v1/catalog/mechanical/<mechanical_version>.json`` -> the exact release bytes (or
``mechanical_served[version]`` when a test serves other bytes), 404 for an unknown version.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

RELEASE_PREFIX = "/api/v1/catalog/releases/"
MECHANICAL_MANIFEST = "/api/v1/catalog/mechanical"
MECHANICAL_PREFIX = MECHANICAL_MANIFEST + "/"


def release_entry(data: bytes, *, status: str = "RETAINED", published_at: str = "2026-10-01T00:00:00Z", schema_version: str | None = None,
                  sha256: str | None = None, size_bytes: int | None = None) -> dict[str, Any]:
    document = json.loads(data)
    return {"catalog_version": document["catalog_version"], "schema_version": schema_version or document["schema_version"],
            "sha256": sha256 or hashlib.sha256(data).hexdigest(), "size_bytes": size_bytes or len(data),
            "published_at": published_at, "status": status}


@dataclass
class CatalogServiceState:
    releases: dict[str, bytes] = field(default_factory=dict)  # catalog_version -> bytes served
    entries: list[dict[str, Any]] = field(default_factory=list)  # retained, newest first (current included)
    current: str | None = None
    gzip: bool = False
    release_path_template: str = "/api/v1/catalog/releases/{catalog_version}"
    requests: list[dict[str, Any]] = field(default_factory=list)
    manifest_status: int = 200
    mechanical: dict[str, bytes] = field(default_factory=dict)  # mechanical_version -> published bytes
    mechanical_served: dict[str, bytes] = field(default_factory=dict)  # mechanical_version -> bytes served instead
    mechanical_entries: list[dict[str, Any]] = field(default_factory=list)
    mechanical_current: str | None = None
    mechanical_status: int = 200

    def publish_mechanical(self, data: bytes, *, published_at: str = "2026-10-01T00:00:00Z", **entry: Any) -> dict[str, Any]:
        """Publish ``data`` as the current mechanical release (``entry`` overrides manifest fields, e.g. ``sha256``)."""

        document = json.loads(data)
        row = {"mechanical_version": document["mechanical_version"], "schema_version": document["schema_version"],
               "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data), "published_at": published_at,
               "status": "RETAINED", **entry}
        self.mechanical[row["mechanical_version"]] = data
        self.mechanical_entries = [e for e in self.mechanical_entries if e["mechanical_version"] != row["mechanical_version"]] + [row]
        self.mechanical_entries.sort(key=lambda e: e["published_at"], reverse=True)
        self.mechanical_current = row["mechanical_version"]
        return row

    def mechanical_manifest(self) -> dict[str, Any]:
        entries = [{**e, "status": "CURRENT" if e["mechanical_version"] == self.mechanical_current else "RETAINED"}
                   for e in self.mechanical_entries]
        current = next(e for e in entries if e["status"] == "CURRENT")
        return {"schema": "opensci.public_mechanical_catalog.manifest", "manifest_version": "1.0", "current": current,
                "retained": entries, "release_path_template": MECHANICAL_PREFIX + "{mechanical_version}.json"}

    def publish(self, data: bytes, *, current: bool = True, published_at: str = "2026-10-01T00:00:00Z", **entry: Any) -> dict[str, Any]:
        """Serve ``data`` and list it (as the current release unless ``current=False``), newest first."""

        row = release_entry(data, status="RETAINED", published_at=published_at, **entry)
        self.releases[row["catalog_version"]] = data
        self.entries = [e for e in self.entries if e["catalog_version"] != row["catalog_version"]] + [row]
        self.entries.sort(key=lambda e: e["published_at"], reverse=True)
        if current:
            self.current = row["catalog_version"]
        return row

    def manifest(self) -> dict[str, Any]:
        entries = [{**e, "status": "CURRENT" if e["catalog_version"] == self.current else "RETAINED"} for e in self.entries]
        current = next(e for e in entries if e["status"] == "CURRENT")
        return {"schema": "opensci.design_catalog.catalog_manifest", "manifest_version": "1.0", "current": current,
                "retained": entries, "release_path_template": self.release_path_template}

    def paths(self) -> list[str]:
        return [r["path"] for r in self.requests]


class _Handler(BaseHTTPRequestHandler):
    server: _Server

    def log_message(self, format: str, *args: Any) -> None:  # quiet
        return

    def _send(self, status: int, body: bytes = b"", headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        state = self.server.state
        state.requests.append({"path": self.path, "if_none_match": self.headers.get("If-None-Match"),
                               "accept_encoding": self.headers.get("Accept-Encoding")})
        path = self.path.split("?")[0]
        if path in ("/api/v1/catalog", "/mirror/manifest.json"):
            if state.manifest_status != 200:
                self._send(state.manifest_status, json.dumps({"error": {"code": "SERVICE_UNAVAILABLE"}}).encode())
                return
            body = json.dumps(state.manifest(), sort_keys=True).encode("utf-8")
            etag = '"' + hashlib.sha256(body).hexdigest() + '"'
            if self.headers.get("If-None-Match") == etag:
                self._send(304, headers={"ETag": etag})
                return
            self._send(200, body, {"Content-Type": "application/json", "ETag": etag, "Cache-Control": "no-cache"})
            return
        if path == MECHANICAL_MANIFEST:
            if state.mechanical_status != 200:
                self._send(state.mechanical_status, json.dumps({"error": {"code": "SERVICE_UNAVAILABLE"}}).encode())
            elif state.mechanical_current is None:
                self._send(404, json.dumps({"error": {"code": "ROUTE_NOT_FOUND"}}).encode())
            else:
                self._send(200, json.dumps(state.mechanical_manifest(), sort_keys=True).encode("utf-8"),
                           {"Content-Type": "application/json"})
            return
        if path.startswith(MECHANICAL_PREFIX):
            version = path[len(MECHANICAL_PREFIX):].removesuffix(".json")
            data = state.mechanical_served.get(version, state.mechanical.get(version))
            if data is None:
                self._send(404, json.dumps({"error": {"code": "MECHANICAL_CATALOG_RELEASE_NOT_FOUND"}}).encode())
            else:
                self._send(200, data, {"Content-Type": "application/json"})
            return
        version = path[len(RELEASE_PREFIX):] if path.startswith(RELEASE_PREFIX) else path.rsplit("/", 1)[-1].removesuffix(".json")
        data = state.releases.get(version)
        if data is None:
            self._send(404, json.dumps({"error": {"code": "CATALOG_RELEASE_NOT_FOUND"}}).encode())
            return
        headers = {"Content-Type": "application/json", "Cache-Control": "public, max-age=31536000, immutable"}
        if state.gzip and "gzip" in (self.headers.get("Accept-Encoding") or ""):
            data = gzip.compress(data)
            headers["Content-Encoding"] = "gzip"
        self._send(200, data, headers)


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, state: CatalogServiceState) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.state = state


class CatalogServiceStub:
    """``with CatalogServiceStub() as stub: stub.url`` (origin), ``stub.state``."""

    def __init__(self, state: CatalogServiceState | None = None) -> None:
        self.state = state or CatalogServiceState()
        self._server = _Server(self.state)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def __enter__(self) -> CatalogServiceStub:
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._server.shutdown()
        self._server.server_close()
