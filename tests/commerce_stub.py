"""A local stand-in for the OpenSci Commercial API v1 (``http.server`` on 127.0.0.1) for ``os_optics.client`` tests.

It implements the routes, bodies, idempotency and error documents the client relies on (integration spec 4.2): Account
token introspection / issuance / revocation, submissions with the ``Idempotency-Key`` header, resolutions, the protected
Assembly Preview / Assembly Detail deliveries (the committed reference pair sealed with the vendored canonical contract,
``delivery_helpers.sealed_reference_deliveries``), the shipping address book, Orders (shipped to the chosen or the
default address), bank-transfer reports, payment confirmation (a test-side call, never an HTTP route), the multipart
artifact upload with read-back, agent feedback (``POST /api/v1/feedback``: any token, strict body, credential-shaped
text refused, ``feedback_rate_limited`` answers 429 with ``Retry-After``) and the order options: the
mechanical product listing (``GET /api/v1/mechanical-products``), part-list submissions (``POST
/api/v1/part-list-submissions``: unknown part numbers refused with ``PART_NOT_ORDERABLE``, derived mechanics without a
beam height with ``MOUNTING_INPUT_REQUIRED``) and the mounting sheets of a resolution / an Order. Every request is
recorded.

BOM-only ordering (DESIGN_P section 1, the facts the Engine relies on): a version-2 purchase BOM gets a line review
(``resolution.bom_review``; ``NOT_ORDERABLE`` for an unknown part number, ``ABOVE_LIMIT`` above ``max_line_quantity``,
``TEMPORARILY_UNAVAILABLE`` for ``bom_unavailable``); every line OK -> 201 ``ORDERABLE``; some OK -> 201
``CHANGES_REQUIRED`` (persisted, its own ``resolution_id``; ``POST /orders`` refuses it with 409
``BOM_CHANGES_UNCONFIRMED``); none OK -> 422 ``BOM_NOT_ORDERABLE`` with the review in ``details``; a confirmed
resubmission is checked against the review it names (422 ``BOM_CONFIRMATION_INVALID`` / 404 ``RESOLUTION_NOT_FOUND``);
a replay answers 201 ``replayed: true``. With ``retired`` the stub is the BOM-only service: the retired routes answer 410
``ROUTE_RETIRED`` / ``ORDERING_BASIS_RETIRED`` before authentication, a version-1 part list with derived mechanics or
mounting 422 ``ORDER_OPTION_RETIRED``, and ``POST /orders`` of the design resolution 410 ``ORDERING_BASIS_RETIRED``.
"""

from __future__ import annotations

import email.parser
import email.policy
import hashlib
import json
import re
import threading
import urllib.parse
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from delivery_helpers import TEST_ORGANIZATION_ID, sealed_reference_deliveries
from os_optics._vendor.shared_contracts.client_assembly_artifact import ClientAssemblyArtifactManifestV1

AGENT_SCOPES = ["commercial:resolve", "commercial:assembly-preview:read", "commercial:order:place", "commercial:order:read",
                "commercial:payment:report", "commercial:assembly-detail:read", "commercial:assembly-artifact:write",
                "commercial:assembly-artifact:read"]
REVIEW_SCOPES = ["commercial:resolve", "commercial:assembly-preview:read", "commercial:order:read",
                 "commercial:assembly-artifact:read"]
MANAGE_SCOPE = "account:token:manage"
CAPABILITY_SCOPES = {
    "resolve": "commercial:resolve", "view_assembly_preview": "commercial:assembly-preview:read",
    "place_order": "commercial:order:place", "read_orders": "commercial:order:read",
    "report_bank_transfer": "commercial:payment:report", "obtain_assembly_detail": "commercial:assembly-detail:read",
    "upload_artifacts": "commercial:assembly-artifact:write", "read_artifacts": "commercial:assembly-artifact:read",
    "manage_tokens": MANAGE_SCOPE,
}
RECOVERY_BY_CODE = {
    "AUTHORIZATION_MISSING": "PROVIDE_TOKEN", "AUTHORIZATION_INVALID": "REQUEST_NEW_TOKEN",
    "AUTHORIZATION_REVOKED": "REQUEST_NEW_TOKEN", "AUTHORIZATION_SCOPE_MISSING": "REQUEST_SCOPE",
    "IDEMPOTENCY_KEY_CONFLICT": "USE_NEW_IDEMPOTENCY_KEY", "IDEMPOTENCY_KEY_REQUIRED": "FIX_REQUEST",
    "RESOLUTION_NOT_FOUND": "FIX_REQUEST", "ORDER_NOT_FOUND": "FIX_REQUEST", "REQUEST_INVALID": "FIX_REQUEST",
    "ARTIFACT_SET_NOT_FOUND": "FIX_REQUEST", "SERVICE_UNAVAILABLE": "RETRY",
    "SHIPPING_ADDRESS_REQUIRED": "ADD_SHIPPING_ADDRESS", "SHIPPING_ADDRESS_NOT_FOUND": "FIX_REQUEST",
    "SHIPPING_ADDRESS_INVALID": "FIX_REQUEST", "ORDER_NOT_WITHDRAWABLE": "CONTACT_OPENSCI",
    "FEEDBACK_INVALID": "FIX_REQUEST", "FEEDBACK_RATE_LIMITED": "RETRY_LATER",
    "PART_NOT_ORDERABLE": "LIST_MECHANICAL_PRODUCTS", "MOUNTING_INPUT_REQUIRED": "FIX_REQUEST",
    "MOUNTING_SHEET_NOT_PART_OF_PURCHASE": "FIX_REQUEST", "ORDER_OPTION_ON_HOLD": "FIX_REQUEST",
    "ORDERING_BASIS_RETIRED": "PURCHASE_BOM", "ROUTE_RETIRED": "USE_LOCAL_PLAN", "ORDER_OPTION_RETIRED": "PURCHASE_BOM",
    "BOM_CHANGES_UNCONFIRMED": "CONFIRM_BOM_CHANGES", "BOM_NOT_ORDERABLE": "CHOOSE_ANOTHER_PRODUCT",
    "BOM_CONFIRMATION_INVALID": "FIX_REQUEST", "SUBMISSION_SCHEMA_INVALID": "FIX_REQUEST",
}
#: The routes the BOM-only service retired: (method, path pattern with ``*`` segments) -> code (answered with 410).
RETIRED_ROUTES = (
    ("POST", ("commercial-submissions",), "ORDERING_BASIS_RETIRED"),
    ("GET", ("resolutions", "*", "mounting-sheet"), "ROUTE_RETIRED"),
    ("GET", ("orders", "*", "mounting-sheet"), "ROUTE_RETIRED"),
    ("GET", ("resolutions", "*", "optical-layout"), "ROUTE_RETIRED"),
    ("GET", ("orders", "*", "optical-layout"), "ROUTE_RETIRED"),
    ("POST", ("resolutions", "*", "assembly-preview-deliveries"), "ROUTE_RETIRED"),
    ("POST", ("resolutions", "*", "assembly-detail-deliveries"), "ROUTE_RETIRED"),
    ("POST", ("artifact-sets",), "ROUTE_RETIRED"),
    ("GET", ("artifact-sets", "*"), "ROUTE_RETIRED"),
    ("GET", ("resolutions", "*", "artifact-sets"), "ROUTE_RETIRED"),
    ("GET", ("artifact-sets", "*", "files", "*"), "ROUTE_RETIRED"),
)
BOM_LINE_FIELDS = {"line_id", "opensci_sku", "quantity", "purpose"}
#: The stub's mechanical products (``GET /api/v1/mechanical-products``) and the part numbers a part list may name.
MECHANICAL_PRODUCTS = [
    {"opensci_sku": "OS-MECH-BB-300", "kind": "BREADBOARD", "display_name": "Breadboard 300 x 300 mm",
     "display_name_zh_cn": "面包板 300 x 300 mm", "description": "Aluminium breadboard, M6 holes on a 25 mm grid.",
     "description_zh_cn": "铝面包板，M6 螺孔，25 mm 网格。",
     "facts": {"size_x_mm": 300, "size_y_mm": 300, "hole_pitch_mm": 25, "thread": "M6"}},
    {"opensci_sku": "OS-MECH-KM-1", "kind": "MOUNT", "display_name": "Kinematic mount 25.4 mm",
     "display_name_zh_cn": "精密调整镜架 25.4 mm", "description": "Two-axis kinematic mount for 25.4 mm optics.",
     "description_zh_cn": "两轴调整镜架，适用于 25.4 mm 光学元件。",
     "facts": {"centre_height_mm": 15, "holds": "25.4 mm round optic", "adjustment_axes": ["Rx", "Ry"],
               "post_thread": "M4"}},
    {"opensci_sku": "OS-MECH-PH-40", "kind": "POST_HOLDER", "display_name": "Post holder 40 mm",
     "display_name_zh_cn": "立杆套筒 40 mm", "description": "Post holder for 12.7 mm posts.",
     "description_zh_cn": "适用于 12.7 mm 立杆的套筒。", "facts": {"length_mm": 40, "thread": "M6"}},
    {"opensci_sku": "OS-MECH-TR-40", "kind": "POST", "display_name": "Post 40 mm", "display_name_zh_cn": "立杆 40 mm",
     "description": "Stainless steel post, 12.7 mm diameter.", "description_zh_cn": "不锈钢立杆，直径 12.7 mm。",
     "facts": {"length_mm": 40, "diameter_mm": 12.7, "thread": "M4"}},
]
ORDERABLE_OPTICS = ("OS-OPT-TEST", "OS-OPT-MIRROR")
#: The address every stub organization starts with (``StubState(addresses=[])`` for an empty address book).
DEFAULT_ADDRESS = {"address_id": "00000000-0000-4000-8000-0000000add01", "label": "Lab", "recipient_name": "张三",
                   "mobile_phone": "13800138000", "region": "CN_MAINLAND", "province": "广东省", "city": "深圳市",
                   "district": "南山区", "street_address": "科技园路 1 号", "postal_code": "518000", "is_default": True}
ADDRESS_FIELDS = ("recipient_name", "mobile_phone", "province", "city", "district", "street_address", "postal_code")
FAMILY_BY_STATUS = {401: "UNAUTHENTICATED", 403: "FORBIDDEN", 404: "NOT_FOUND", 409: "CONFLICT", 410: "GONE", 422: "REJECTED",
                    429: "RATE_LIMITED", 503: "SERVER_ERROR"}
#: The feedback contract (integration spec item 4): categories, text limits, context fields and their limits.
FEEDBACK_CATEGORIES = ("PROBLEM", "FEATURE_REQUEST", "DOCUMENTATION")
FEEDBACK_CONTEXT_CHARS = {"engine_version": 40, "runtime": 80, "catalog_version": 40, "command": 500, "error_code": 80,
                          "resolution_id": 36, "order_id": 36, "locale": 5, "agent": 80}
#: Credential-shaped text the service refuses in feedback (an Account token, a staff session).
FEEDBACK_CREDENTIAL = re.compile(r"oat_[0-9a-f]{32}_[0-9a-f]{64}|oss_[0-9A-Za-z_-]{16,}")
FEEDBACK_RETRY_AFTER_SECONDS = 1800


def credential_for(label: str) -> str:
    digest = hashlib.sha256(f"opensci-engine test credential: {label}".encode()).hexdigest()
    token_id = hashlib.sha256(f"id:{label}".encode()).hexdigest()[:32]
    return f"oat_{token_id}_{digest}"


def _uuid(hex32: str) -> str:
    return f"{hex32[:8]}-{hex32[8:12]}-{hex32[12:16]}-{hex32[16:20]}-{hex32[20:]}"


class Refusal(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict | None = None,
                 headers: dict[str, str] | None = None) -> None:
        super().__init__(code)
        self.status, self.code, self.message, self.details = status, code, message, details or {}
        self.headers = headers


@dataclass
class Token:
    credential: str
    scopes: list[str]
    label: str | None = None
    status: str = "ACTIVE"
    expires_at: str = "2099-01-01T00:00:00Z"

    @property
    def token_id(self) -> str:
        return _uuid(self.credential[4:36])

    def document(self) -> dict[str, Any]:
        return {"schema": "opensci.account.token_status", "schema_version": "1", "token_id": self.token_id,
                "organization": {"id": TEST_ORGANIZATION_ID, "name": "Test Optics Lab"}, "scopes": list(self.scopes),
                "issued_at": "2026-09-30T00:00:00Z", "expires_at": self.expires_at, "status": self.status,
                "label": self.label, "capabilities": {**{name: scope in self.scopes for name, scope in CAPABILITY_SCOPES.items()},
                                                      "send_feedback": True}}


@dataclass
class StubState:
    resolution_id: str
    tokens: dict[str, Token] = field(default_factory=dict)
    idempotency: dict[tuple[str, str], tuple[str, Any]] = field(default_factory=dict)
    orders: dict[str, dict[str, Any]] = field(default_factory=dict)
    addresses: list[dict[str, Any]] = field(default_factory=lambda: [dict(DEFAULT_ADDRESS)])
    artifact_sets: dict[str, dict[str, Any]] = field(default_factory=dict)
    artifact_files: dict[tuple[str, str], bytes] = field(default_factory=dict)
    requests: list[dict[str, Any]] = field(default_factory=list)
    submissions: int = 0
    include_recovery: bool = True
    fail_next: list[int] = field(default_factory=list)  # statuses to answer (transient failures) before routing
    deliveries: tuple[dict, dict] | None = None
    redirect_to: str | None = None
    confirm_on_report: bool = False  # OpenSci confirms the payment as soon as it is reported (README example)
    feedback: list[dict[str, Any]] = field(default_factory=list)  # stored feedback items (with the token id)
    feedback_rate_limited: bool = False  # answer a new feedback submission with 429 FEEDBACK_RATE_LIMITED
    #: refusals to answer the next artifact uploads with, in order, after authorization (upload limits);
    #: ``None`` lets that upload through
    artifact_upload_refusals: list[Refusal | None] = field(default_factory=list)
    #: Optical Layout Packages served at ``GET /api/v1/{resolutions|orders}/{id}/optical-layout`` (key: "resolutions/<id>"
    #: or "orders/<id>"); a missing key answers 404 OPTICAL_LAYOUT_NOT_AVAILABLE.
    optical_layouts: dict[str, Any] = field(default_factory=dict)
    #: Mounting sheets served at ``GET /api/v1/{resolutions|orders}/{id}/mounting-sheet`` (key as ``optical_layouts``);
    #: a missing key answers 409 MOUNTING_SHEET_NOT_PART_OF_PURCHASE.
    mounting_sheets: dict[str, Any] = field(default_factory=dict)
    part_list_submissions: int = 0
    #: BOM-only ordering (module docstring)
    max_line_quantity: int = 100
    bom_unavailable: set[str] = field(default_factory=set)
    bom_reviews: dict[str, dict[str, Any]] = field(default_factory=dict)  # resolution_id -> its bom_review
    retired: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add_token(self, name: str, scopes: list[str], **kw: Any) -> str:
        credential = credential_for(name)
        self.tokens[credential] = Token(credential, list(scopes), **kw)
        return credential

    def confirm_payment(self) -> None:
        """What OpenSci finance does outside the API (the operator console)."""

        for order in self.orders.values():
            order["payment"]["state"] = "CONFIRMED"


class Handler(BaseHTTPRequestHandler):
    server: StubServer  # type: ignore[assignment]

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 -- quiet test server
        return

    def _body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def _send(self, status: int, document: Any = None, *, raw: bytes | None = None, content_type: str = "application/json",
              headers: dict[str, str] | None = None) -> None:
        body = raw if raw is not None else json.dumps(document).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Request-Id", f"req-{len(self.server.state.requests):04d}")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _handle(self, method: str) -> None:
        state = self.server.state
        body = self._body()
        parts = urllib.parse.urlsplit(self.path)
        state.requests.append({"method": method, "path": parts.path, "query": parts.query, "headers": dict(self.headers),
                               "body": body})
        if state.redirect_to:
            self._send(307, {}, headers={"Location": state.redirect_to})
            return
        if state.fail_next:
            status = state.fail_next.pop(0)
            if status == 0:  # drop the connection without an answer
                self.close_connection = True
                self.connection.close()
                return
            self._error(Refusal(status, "SERVICE_UNAVAILABLE", "the commercial service is temporarily unavailable"))
            return
        try:
            with state.lock:
                status, document, extra = route(state, method, parts.path, urllib.parse.parse_qs(parts.query),
                                                dict(self.headers), body)
        except Refusal as refusal:
            self._error(refusal)
            return
        if isinstance(document, bytes):
            self._send(status, raw=document, content_type="application/octet-stream", headers=extra)
        else:
            self._send(status, document, headers=extra)

    def _error(self, refusal: Refusal) -> None:
        error = {"code": refusal.code, "family": FAMILY_BY_STATUS.get(refusal.status, "REJECTED"),
                 "message": refusal.message, "details": refusal.details}
        recovery = RECOVERY_BY_CODE.get(refusal.code)
        if refusal.code == "ASSEMBLY_DETAIL_NOT_RELEASED":
            recovery = "CREATE_ORDER_FIRST" if refusal.details.get("reason") == "NO_ORDER" \
                else "WAIT_FOR_PAYMENT_CONFIRMATION"
        if self.server.state.include_recovery and recovery:
            error["recovery"] = recovery
        self._send(refusal.status, {"error": error, "request_id": f"req-{len(self.server.state.requests):04d}"},
                   headers=refusal.headers)

    def do_GET(self) -> None:  # noqa: N802
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle("DELETE")


class StubServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, state: StubState) -> None:
        super().__init__(("127.0.0.1", 0), Handler)
        self.state = state
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"

    def __enter__(self) -> StubServer:
        self.thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.shutdown()
        self.server_close()


# ------------------------------------------------------------------------------------------ routing


def _authorize(state: StubState, headers: dict[str, str], scope: str | None) -> Token:
    value = next((v for k, v in headers.items() if k.lower() == "authorization"), None)
    if not value:
        raise Refusal(401, "AUTHORIZATION_MISSING", "an Account token is required")
    token = state.tokens.get(value.removeprefix("Bearer "))
    if token is None or not value.startswith("Bearer "):
        raise Refusal(401, "AUTHORIZATION_INVALID", "the Account token is not valid")
    if token.status == "REVOKED":
        raise Refusal(401, "AUTHORIZATION_REVOKED", "the Account token has been revoked")
    if scope is not None and scope not in token.scopes:
        raise Refusal(403, "AUTHORIZATION_SCOPE_MISSING", f"the Account token does not carry the {scope!r} scope",
                      {"required_scope": scope})
    return token


def _idempotent(state: StubState, headers: dict[str, str], operation: str, body: bytes, create: Any,
                digest: str | None = None) -> tuple[int, Any]:
    """Like the service: the request identity is the parsed JSON body (or ``digest``: an artifact set's own digest)."""

    key = next((v for k, v in headers.items() if k.lower() == "idempotency-key"), None)
    if not key:
        raise Refusal(422, "IDEMPOTENCY_KEY_REQUIRED", "exactly one Idempotency-Key header is required",
                      {"header": "Idempotency-Key"})
    if digest is None:
        parsed = _json(body)
        digest = hashlib.sha256(json.dumps(parsed, sort_keys=True).encode("utf-8")).hexdigest()
    seen = state.idempotency.get((operation, key))
    if seen is not None:
        if seen[0] != digest:
            raise Refusal(409, "IDEMPOTENCY_KEY_CONFLICT", "this idempotency key was used with another request")
        return 200, {**seen[1], "replayed": True}
    result = create()
    state.idempotency[(operation, key)] = (digest, result)
    return 201, {**result, "replayed": False}


def _json(body: bytes) -> Any:
    try:
        return json.loads(body) if body else None
    except ValueError:
        raise Refusal(422, "REQUEST_INVALID", "the request body is not JSON") from None


def _resolution(state: StubState, submission_id: str = "00000000-0000-4000-8000-00000000a001") -> dict[str, Any]:
    return {"schema": "opensci.commercial_submission_response", "schema_version": "1.0", "submission_id": submission_id,
            "resolution_id": state.resolution_id,
            "resolution": {"customer_bom": {"currency": "CNY", "total": "1234.00",
                                            "lines": [{"opensci_sku": "OS-OPT-TEST", "quantity": 1, "unit_price": "1234.00"}]}}}


def _order_for(state: StubState, resolution_id: str, address: dict[str, Any]) -> dict[str, Any]:
    number = f"OS-ORD-2026-{len(state.orders) + 1:06d}"
    order_id = f"00000000-0000-4000-8000-{len(state.orders) + 1:012d}"
    order = {"schema": "opensci.commercial.customer_order", "schema_version": "6", "order_id": order_id,
             "order_number": number, "currency": "CNY", "total": "1234.00",
             "payment": {"method": "CORPORATE_BANK_TRANSFER", "state": "AWAITING_TRANSFER", "amount_due": "1234.00",
                         "currency": "CNY", "transfer_reference": number},
             "shipping_address": {"region": "CN_MAINLAND", **{k: address.get(k) for k in ADDRESS_FIELDS}},
             "integrity": {"commercial_resolution_id": resolution_id}}
    state.orders[order_id] = order
    return order


def _multipart(headers: dict[str, str], body: bytes) -> tuple[bytes, dict[str, bytes]]:
    content_type = next(v for k, v in headers.items() if k.lower() == "content-type")
    message = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
        b"Content-Type: " + content_type.encode("ascii") + b"\r\n\r\n" + body)
    manifest, files = None, {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        data = part.get_payload(decode=True)
        if name == "manifest":
            manifest = data
        elif name == "file":
            files[part.get_filename()] = data
        else:
            raise Refusal(422, "REQUEST_INVALID", "unknown multipart part")
    if manifest is None:
        raise Refusal(422, "REQUEST_INVALID", "the manifest part is required")
    return manifest, files


def _feedback_invalid(reason: str, fields: list[str]) -> Refusal:
    return Refusal(422, "FEEDBACK_INVALID", "the feedback is not valid", {"reason": reason, "invalid_fields": fields})


def _check_feedback(request: Any) -> None:
    """The service's strict feedback body (``FeedbackSubmitRequestV1``): sizes, known fields, no credential-shaped text."""

    if not isinstance(request, dict) or set(request) - {"category", "summary", "details", "context"} \
            or not {"category", "summary", "details"} <= set(request):
        raise Refusal(422, "REQUEST_INVALID", "the feedback body is not a FeedbackSubmitRequestV1")
    invalid = [name for name, (low, high) in (("summary", (5, 200)), ("details", (1, 8000)))
               if not isinstance(request[name], str) or not low <= len(request[name]) <= high]
    if request["category"] not in FEEDBACK_CATEGORIES:
        invalid.insert(0, "category")
    context = request.get("context") or {}
    if not isinstance(context, dict) or set(context) - set(FEEDBACK_CONTEXT_CHARS) - {"request_ids"}:
        raise Refusal(422, "REQUEST_INVALID", "unknown feedback context fields")
    invalid += [f"context.{name}" for name, limit in FEEDBACK_CONTEXT_CHARS.items()
                if name in context and (not isinstance(context[name], str) or len(context[name]) > limit)]
    ids = context.get("request_ids", [])
    if not isinstance(ids, list) or len(ids) > 10 or not all(isinstance(i, str) and len(i) <= 80 for i in ids):
        invalid.append("context.request_ids")
    if invalid:
        raise _feedback_invalid("FIELD_INVALID", invalid)
    texts = {"summary": request["summary"], "details": request["details"],
             **{f"context.{k}": v for k, v in context.items() if isinstance(v, str)},
             **{f"context.request_ids[{i}]": v for i, v in enumerate(ids)}}
    holding = [name for name, text in texts.items() if FEEDBACK_CREDENTIAL.search(text)]
    if holding:
        raise _feedback_invalid("CONTAINS_CREDENTIAL", holding)


def _retired_code(method: str, p: list[str]) -> str | None:
    for route_method, pattern, code in RETIRED_ROUTES:
        if route_method == method and len(pattern) == len(p) and all(a in ("*", b) for a, b in zip(pattern, p)):
            return code
    return None


def _bom_resolution_id(number: int) -> str:
    return f"00000000-0000-4000-8000-{0xb0b000 + number:012x}"


def _review_lines(state: StubState, lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    known = {m["opensci_sku"] for m in MECHANICAL_PRODUCTS} | set(ORDERABLE_OPTICS)
    reviewed = []
    for line in lines:
        sku = line["opensci_sku"]
        status = ("NOT_ORDERABLE" if sku not in known else "ABOVE_LIMIT" if line["quantity"] > state.max_line_quantity
                  else "TEMPORARILY_UNAVAILABLE" if sku in state.bom_unavailable else "OK")
        reviewed.append({"line_id": line["line_id"], "opensci_sku": sku, "quantity": line["quantity"],
                         "purpose": line.get("purpose", "PLAN"), "status": status, "line_key": None,
                         "limit": state.max_line_quantity if status == "ABOVE_LIMIT" else None, "proposed": None})
    ok = 0
    for line in reviewed:
        if line["status"] == "OK":
            ok += 1
            line["line_key"] = f"B{ok:02d}"
    return reviewed


def _confirmation_problems(review: dict[str, Any], request: dict[str, Any]) -> list[dict[str, Any]]:
    """DESIGN_P 1.5 ``verify_confirmation``: the decisions must turn the reviewed lines into the submitted lines."""

    lines = {l["line_id"]: l for l in review["lines"]}
    decisions = {d["line_id"]: d for d in request["confirmed_changes"]["decisions"]}
    problems = []
    for line_id, d in decisions.items():
        line = lines.get(line_id)
        if line is None:
            problems.append({"line_id": line_id, "reason": "UNKNOWN_LINE"})
        elif line["status"] == "OK":
            problems.append({"line_id": line_id, "reason": "NOT_FLAGGED"})
        elif d["decision"] == "QUANTITY_CHANGED" and line["status"] in ("NOT_ORDERABLE", "TEMPORARILY_UNAVAILABLE"):
            problems.append({"line_id": line_id, "reason": "DOES_NOT_RESOLVE"})
    problems += [{"line_id": l["line_id"], "reason": "MISSING_DECISION"} for l in review["lines"]
                 if l["status"] != "OK" and l["line_id"] not in decisions]
    expected = []
    for l in review["lines"]:
        d = decisions.get(l["line_id"])
        if l["status"] == "OK" or d is None:
            expected.append((l["line_id"], l["opensci_sku"], l["quantity"], l["purpose"]))
        elif d["decision"] == "QUANTITY_CHANGED":
            expected.append((l["line_id"], l["opensci_sku"], d["quantity"], l["purpose"]))
        elif d["decision"] == "SUBSTITUTED":
            expected.append((l["line_id"], d["opensci_sku"], d["quantity"], l["purpose"]))
    submitted = [(x["line_id"], x["opensci_sku"], x["quantity"], x.get("purpose", "PLAN")) for x in request["lines"]]
    if not problems and submitted != expected:
        problems.append({"reason": "LINES_DIFFER", "expected_line_ids": [e[0] for e in expected],
                         "submitted_line_ids": [x[0] for x in submitted]})
    return problems


def _part_list_v2(state: StubState, headers: dict[str, str], body: bytes, document: dict[str, Any]) -> tuple[int, Any, None]:
    if set(document) - {"schema", "schema_version", "lines", "confirmed_changes", "mechanical_supply"} \
            or document.get("mechanical_supply", "NONE") != "NONE" or not isinstance(document.get("lines"), list) \
            or any(set(line) - BOM_LINE_FIELDS for line in document["lines"]):
        if document.get("mechanical_supply") == "DERIVED":
            raise Refusal(422, "ORDER_OPTION_RETIRED", "OpenSci no longer selects mounting hardware",
                          {"option": "mechanical_supply", "value": "DERIVED"})
        raise Refusal(422, "SUBMISSION_SCHEMA_INVALID", "the part-list submission is invalid")
    lines = [dict(line) for line in document["lines"]]
    if all("line_id" not in line for line in lines):
        if document.get("confirmed_changes"):
            raise Refusal(422, "BOM_CONFIRMATION_INVALID", "the confirmed changes do not match",
                          {"problems": [{"reason": "LINE_IDS_REQUIRED"}]})
        for index, line in enumerate(lines):
            line["line_id"] = f"L{index + 1}"
    changes = document.get("confirmed_changes")
    if changes:
        reviewed = state.bom_reviews.get(changes.get("resolution_id"))
        if reviewed is None:
            raise Refusal(404, "RESOLUTION_NOT_FOUND", "no such resolution")
        if reviewed["status"] != "CHANGES_REQUIRED":
            raise Refusal(422, "BOM_CONFIRMATION_INVALID", "the confirmed changes do not match",
                          {"problems": [{"reason": "REVIEW_NEEDS_NO_CHANGES"}]})
        problems = _confirmation_problems(reviewed, {**document, "lines": lines})
        if problems:
            raise Refusal(422, "BOM_CONFIRMATION_INVALID", "the confirmed changes do not turn the reviewed lines into the "
                          "submitted lines", {"problems": problems})
    reviewed_lines = _review_lines(state, lines)
    ok = [line for line in reviewed_lines if line["status"] == "OK"]
    review = {"schema_version": "1", "status": "ORDERABLE" if len(ok) == len(reviewed_lines) else "CHANGES_REQUIRED",
              "lines": reviewed_lines, "review_digest": hashlib.sha256(json.dumps(reviewed_lines, sort_keys=True).encode()).hexdigest(),
              "confirmed_changes": None if not changes else {"reviewed_resolution_id": changes["resolution_id"],
                                                             "decisions": changes["decisions"]}}
    if not ok:
        raise Refusal(422, "BOM_NOT_ORDERABLE", "No line of this BOM can be supplied now",
                      {"bom_review": review, "line_ids": [line["line_id"] for line in reviewed_lines]})

    def quote() -> dict[str, Any]:
        state.part_list_submissions += 1
        resolution_id = _bom_resolution_id(state.part_list_submissions)
        state.bom_reviews[resolution_id] = review
        return {"schema": "opensci.commercial_submission_response", "schema_version": "1.1",
                "submission_id": f"00000000-0000-4000-8000-{0xc0c000 + state.part_list_submissions:012x}",
                "resolution_id": resolution_id,
                "resolution": {"schema_version": "7", "basis": "PART_LIST", "bom_review": review,
                               "next_steps": ["CREATE_ORDER"] if review["status"] == "ORDERABLE" else ["CONFIRM_BOM_CHANGES"],
                               "customer_bom": {"currency": "CNY", "total": f"{100 * sum(l['quantity'] for l in ok)}.00",
                                                "lines": [{"line_key": l["line_key"], "opensci_sku": l["opensci_sku"],
                                                           "quantity": l["quantity"], "purpose": l["purpose"],
                                                           "unit_price": "100.00"} for l in ok]}}}

    _, answer = _idempotent(state, headers, "part-list", body, quote)
    return 201, answer, None  # (DESIGN_P I3: a replay is 201 with replayed: true too)


def route(state: StubState, method: str, path: str, query: dict[str, list[str]], headers: dict[str, str],
          body: bytes) -> tuple[int, Any, dict[str, str] | None]:
    prefix = "/api/v1"
    if not path.startswith(prefix):
        raise Refusal(404, "ROUTE_NOT_FOUND", "no such route")
    p = path[len(prefix):].split("/")[1:]
    rid = state.resolution_id
    retired = _retired_code(method, p) if state.retired else None
    if retired is not None:  # answered before authentication, as the BOM-only service does
        raise Refusal(410, retired, "This route is retired: assembly previews, drawings, optical layouts and mounting "
                      "sheets are made on your machine with os-optics." if retired == "ROUTE_RETIRED" else
                      "Ordering from a design submission is retired: submit the parts list (BOM).")

    if p == ["account", "token"]:
        token = _authorize(state, headers, None)
        if method == "DELETE":
            token.status = "REVOKED"
        return 200, token.document(), None
    if p == ["account", "tokens"]:
        issuer = _authorize(state, headers, MANAGE_SCOPE)
        if method == "GET":
            return 200, {"schema": "opensci.account.token_list", "schema_version": "1",
                         "tokens": [t.document() for t in state.tokens.values()]}, None
        request = _json(body) or {}
        if (request.get("profile") is None) == (request.get("scopes") is None):
            raise Refusal(422, "REQUEST_INVALID", "exactly one of scopes / profile")
        scopes = request["scopes"] if request.get("scopes") is not None else \
            {"agent": AGENT_SCOPES, "review": REVIEW_SCOPES}[request["profile"]]
        if MANAGE_SCOPE in scopes:
            raise Refusal(422, "REQUEST_INVALID", "a token cannot issue a token that manages tokens")
        issued: dict[str, str] = {}

        def create() -> dict[str, Any]:
            credential = state.add_token(f"issued-{len(state.tokens)}", scopes, label=request.get("label"),
                                         expires_at=issuer.expires_at)
            issued["credential"] = credential
            return {"token": state.tokens[credential].document()}

        status, document = _idempotent(state, headers, "token", body, create)
        return status, {**document, "credential": issued.get("credential")}, {"Cache-Control": "no-store"}
    if p[:2] == ["account", "tokens"] and len(p) == 3 and method == "DELETE":
        _authorize(state, headers, MANAGE_SCOPE)
        token = next((t for t in state.tokens.values() if t.token_id == p[2]), None)
        if token is None:
            raise Refusal(404, "TOKEN_NOT_FOUND", "no such token")
        token.status = "REVOKED"
        return 200, token.document(), None

    if p == ["account", "addresses"]:
        token = _authorize(state, headers, None)
        if method == "GET":
            if not {"commercial:order:place", "commercial:order:read"} & set(token.scopes):
                raise Refusal(403, "AUTHORIZATION_SCOPE_MISSING", "the token can neither place nor read Orders")
            default = next((a["address_id"] for a in state.addresses if a["is_default"]), None)
            return 200, {"schema": "opensci.commercial.shipping_address_list", "schema_version": "1",
                         "default_address_id": default, "max_addresses": 20,
                         "addresses": sorted(state.addresses, key=lambda a: not a["is_default"])}, {"Cache-Control": "no-store"}
        _authorize(state, headers, "commercial:order:place")
        request = _json(body) or {}
        invalid = sorted(name for name in ADDRESS_FIELDS[:-1] if not str(request.get(name) or "").strip())
        if not str(request.get("mobile_phone", "")).isdigit():
            invalid = sorted({*invalid, "mobile_phone"})
        if invalid:
            raise Refusal(422, "SHIPPING_ADDRESS_INVALID", "the shipping address is not valid", {"invalid_fields": invalid})

        def add() -> dict[str, Any]:
            first = not state.addresses
            if request.get("make_default") and not first:
                for address in state.addresses:
                    address["is_default"] = False
            address = {"address_id": f"00000000-0000-4000-8000-{len(state.addresses) + 0xadd10:012x}",
                       "label": request.get("label"), "region": "CN_MAINLAND",
                       **{k: request.get(k) for k in ADDRESS_FIELDS}, "is_default": first or bool(request.get("make_default"))}
            state.addresses.append(address)
            return {"address": address}

        status, document = _idempotent(state, headers, "address", body, add)
        return status, {"schema": "opensci.commercial.shipping_address_creation", "schema_version": "1", **document}, \
            {"Cache-Control": "no-store"}

    if p == ["feedback"] and method == "POST":
        token = _authorize(state, headers, None)  # any active Account token, no scope
        request = _json(body)
        _check_feedback(request)
        user_agent = next((v for k, v in headers.items() if k.lower() == "user-agent"), None)
        key = next((v for k, v in headers.items() if k.lower() == "idempotency-key"), None)
        if state.feedback_rate_limited and not (key and ("feedback", key) in state.idempotency):  # a replay is not counted
            raise Refusal(429, "FEEDBACK_RATE_LIMITED", "too many feedback submissions; send it again later",
                          {"limit": "per_token_per_hour", "retry_after_seconds": FEEDBACK_RETRY_AFTER_SECONDS},
                          headers={"Retry-After": str(FEEDBACK_RETRY_AFTER_SECONDS)})

        def receive() -> dict[str, Any]:
            feedback_id = f"00000000-0000-7000-8000-{len(state.feedback) + 0xfeed0:012x}"
            received_at = f"2026-10-05T00:00:{len(state.feedback):02d}Z"
            state.feedback.append({"feedback_id": feedback_id, "token_id": token.token_id, "user_agent": user_agent,
                                   "received_at": received_at, **request})
            return {"feedback_id": feedback_id, "received_at": received_at, "status": "RECEIVED"}

        status, document = _idempotent(state, headers, "feedback", body, receive)
        return status, document, {"Cache-Control": "no-store"}
    if p == ["commercial-submissions"] and method == "POST":
        _authorize(state, headers, "commercial:resolve")
        document = _json(body)
        if not isinstance(document, dict) or "idempotency_key" in document or document.get("schema") != "opensci.commercial_submission" \
                or set(document) - {"schema", "schema_version", "project_hash", "project", "local_validation", "customer_constraints"}:
            raise Refusal(422, "SUBMISSION_SCHEMA_INVALID", "the submission envelope is invalid")

        def submit() -> dict[str, Any]:
            state.submissions += 1
            return _resolution(state)

        status, document = _idempotent(state, headers, "submit", body, submit)
        return status, document, None
    if p == ["mechanical-products"] and method == "GET":
        _authorize(state, headers, "commercial:resolve")
        kind = query.get("kind", [None])[0]
        if kind not in (None, "MOUNT", "POST", "POST_HOLDER", "BREADBOARD"):
            raise Refusal(422, "REQUEST_INVALID", "kind must be MOUNT, POST, POST_HOLDER or BREADBOARD")
        return 200, {"schema": "opensci.commercial.mechanical_products", "schema_version": "1",
                     "catalog_version": "2026-10-14.1",
                     "products": [m for m in MECHANICAL_PRODUCTS if kind is None or m["kind"] == kind]}, None
    if p == ["part-list-submissions"] and method == "POST":
        _authorize(state, headers, "commercial:resolve")
        document = _json(body)
        if isinstance(document, dict) and document.get("schema_version") == "2":
            return _part_list_v2(state, headers, body, document)
        if state.retired and isinstance(document, dict) and (document.get("mechanical_supply") == "DERIVED" or document.get("mounting")):
            raise Refusal(422, "ORDER_OPTION_RETIRED", "OpenSci no longer selects mounting hardware or assembles part lists",
                          {"option": "mechanical_supply", "value": document.get("mechanical_supply")})
        if not isinstance(document, dict) or document.get("schema") != "opensci.commercial.part_list_submission"                 or set(document) - {"schema", "schema_version", "lines", "mechanical_supply", "mounting", "optics_source",
                                    "platform", "assembly"}:
            raise Refusal(422, "SUBMISSION_SCHEMA_INVALID", "the part-list submission is invalid")
        held = [name for name, value in (("optics_source", "CUSTOM"), ("platform", "INTEGRATED_BASE"))
                if document.get(name) == value]
        if held:
            raise Refusal(422, "ORDER_OPTION_ON_HOLD", "this order option is on hold", {"options": held})
        known = {m["opensci_sku"] for m in MECHANICAL_PRODUCTS} | set(ORDERABLE_OPTICS)
        unknown = [line.get("line_id") for line in document["lines"] if line.get("opensci_sku") not in known]
        if unknown:
            raise Refusal(422, "PART_NOT_ORDERABLE", "these lines name no part number OpenSci supplies",
                          {"line_ids": unknown})
        if document.get("mechanical_supply") == "DERIVED":
            mounting = document.get("mounting") or {}
            missing = [line["line_id"] for line in document["lines"]
                       if line["opensci_sku"].startswith("OS-OPT-") and line["line_id"] not in mounting]
            if missing:
                raise Refusal(422, "MOUNTING_INPUT_REQUIRED", "every optic line needs its beam height",
                              {"line_ids": missing})

        def submit_parts() -> dict[str, Any]:
            state.part_list_submissions += 1
            resolution = _resolution(state, "00000000-0000-4000-8000-00000000b001")
            return {**resolution, "resolution": {**resolution["resolution"], "order_options": {
                "basis": "PART_LIST", "optics_source": "CATALOG", "mechanical_supply": document["mechanical_supply"],
                "platform": "NONE", "assembly": "CUSTOMER"}}}

        status, document = _idempotent(state, headers, "part-list", body, submit_parts)
        return status, document, None
    if len(p) == 3 and p[0] in ("resolutions", "orders") and p[2] == "mounting-sheet" and method == "GET":
        _authorize(state, headers, "commercial:resolve" if p[0] == "resolutions" else "commercial:order:read")
        document = state.mounting_sheets.get(f"{p[0]}/{p[1]}")
        if document is None:
            raise Refusal(409, "MOUNTING_SHEET_NOT_PART_OF_PURCHASE", "this purchase has no mounting sheet",
                          {"deliverable": "MOUNTING_SHEET"})
        return 200, document, None
    if len(p) == 3 and p[0] in ("resolutions", "orders") and p[2] == "optical-layout" and method == "GET":
        _authorize(state, headers, "commercial:resolve" if p[0] == "resolutions" else "commercial:order:read")
        document = state.optical_layouts.get(f"{p[0]}/{p[1]}")
        if document is None:
            raise Refusal(404, "OPTICAL_LAYOUT_NOT_AVAILABLE", "no optical layout here")
        return 200, document, None
    if p[:1] == ["resolutions"] and len(p) >= 2:
        if p[1] != rid:
            _authorize(state, headers, None)
            raise Refusal(404, "RESOLUTION_NOT_FOUND", "no such resolution")
        if len(p) == 2 and method == "GET":
            _authorize(state, headers, "commercial:resolve")
            return 200, {**_resolution(state), "replayed": False}, None
        if p[2:] == ["presentation"] and method == "GET":
            _authorize(state, headers, "commercial:resolve")
            locale = query.get("locale", [""])[0]
            if locale not in ("zh-CN", "en"):
                raise Refusal(422, "REQUEST_INVALID", "the locale query parameter must be exactly 'zh-CN' or 'en'")
            return 200, {"schema": "opensci.commercial.localized_presentation", "locale": locale,
                         "title": "Resolution" if locale == "en" else "解析结果"}, None
        if p[2:] == ["assembly-preview-deliveries"] and method == "POST":
            _authorize(state, headers, "commercial:assembly-preview:read")
            state.deliveries = state.deliveries or sealed_reference_deliveries()
            return 201, state.deliveries[0], {"Cache-Control": "no-store"}
        if p[2:] == ["assembly-detail-deliveries"] and method == "POST":
            _authorize(state, headers, "commercial:assembly-detail:read")
            orders = [o for o in state.orders.values() if o["integrity"]["commercial_resolution_id"] == rid]
            if not any(o["payment"]["state"] == "CONFIRMED" for o in orders):
                raise Refusal(403, "ASSEMBLY_DETAIL_NOT_RELEASED", "the Detailed Assembly Drawing of this design is "
                              "available once OpenSci has confirmed the payment of its Order",
                              {"reason": "PAYMENT_NOT_CONFIRMED" if orders else "NO_ORDER"})
            state.deliveries = state.deliveries or sealed_reference_deliveries()
            return 201, state.deliveries[1], {"Cache-Control": "no-store"}
        if p[2:] == ["artifact-sets"] and method == "GET":
            _authorize(state, headers, "commercial:assembly-artifact:read")
            kind = query.get("artifact_kind", [None])[0]
            sets = [s for s in state.artifact_sets.values() if kind is None or s["artifact_kind"] == kind]
            return 200, {"schema": "opensci.commercial.client_artifact_set_list", "schema_version": "1",
                         "commercial_resolution_id": rid, "artifact_sets": sets}, None

    if p == ["orders"]:
        if method == "POST":
            _authorize(state, headers, "commercial:order:place")
            request = _json(body) or {}
            review = state.bom_reviews.get(request.get("resolution_id"))
            if review is not None and review["status"] == "CHANGES_REQUIRED":
                raise Refusal(409, "BOM_CHANGES_UNCONFIRMED", "This quote has lines that cannot be supplied as listed",
                              {"resolution_id": request["resolution_id"],
                               "line_ids": [l["line_id"] for l in review["lines"] if l["status"] != "OK"]})
            if review is not None:
                bom_rid = request["resolution_id"]
                status, document = _idempotent(state, headers, "order", body, lambda: {"order": _order_for(
                    state, bom_rid, next(a for a in state.addresses if a["is_default"]))},
                    digest=hashlib.sha256(bom_rid.encode("utf-8")).hexdigest())
                return status, {"schema": "opensci.commercial.order_placement", "schema_version": "1",
                                "replayed": document["replayed"], "order": state.orders[document["order"]["order_id"]]}, None
            if state.retired and request.get("resolution_id") == rid:
                raise Refusal(410, "ORDERING_BASIS_RETIRED", "Design resolutions can no longer be ordered")
            if request.get("resolution_id") != rid:
                raise Refusal(404, "RESOLUTION_NOT_FOUND", "no such resolution")

            def place() -> dict[str, Any]:
                # like the service: the chosen address or the default; the first successful call fixes it
                chosen = request.get("shipping_address_id")
                if chosen is not None:
                    address = next((a for a in state.addresses if a["address_id"] == chosen), None)
                    if address is None:
                        raise Refusal(404, "SHIPPING_ADDRESS_NOT_FOUND", "no such shipping address")
                else:
                    address = next((a for a in state.addresses if a["is_default"]), None)
                    if address is None:
                        raise Refusal(409, "SHIPPING_ADDRESS_REQUIRED", "the Order needs a shipping address",
                                      {"field": "shipping_address_id"})
                return {"order": _order_for(state, rid, address)}

            # the request identity is the resolution only (the service hashes resolution_id, not the address)
            status, document = _idempotent(state, headers, "order", body, place,
                                           digest=hashlib.sha256(rid.encode("utf-8")).hexdigest())
            order = state.orders[document["order"]["order_id"]]
            return status, {"schema": "opensci.commercial.order_placement", "schema_version": "1",
                            "replayed": document["replayed"], "order": order}, None
        _authorize(state, headers, "commercial:order:read")
        return 200, {"schema": "opensci.commercial.customer_order_list", "schema_version": "3",
                     "orders": [{"order_id": o["order_id"], "order_number": o["order_number"]} for o in state.orders.values()]}, None
    if p == ["orders", "presentation"]:
        _authorize(state, headers, "commercial:order:read")
        return 200, {"schema": "opensci.commercial.localized_presentation", "locale": query["locale"][0]}, None
    if p[:1] == ["orders"] and len(p) >= 2:
        order = state.orders.get(p[1])
        if p[2:] == ["withdrawal"] and method == "POST":
            _authorize(state, headers, "commercial:order:place")
            if order is None:
                raise Refusal(404, "ORDER_NOT_FOUND", "no such order")
            if order["payment"]["state"] != "AWAITING_TRANSFER":
                raise Refusal(409, "ORDER_NOT_WITHDRAWABLE", "a transfer was reported or the order is paid",
                              {"payment_state": order["payment"]["state"], "transfer_reported": True})
            del state.orders[p[1]]
            return 200, {"schema": "opensci.commercial.order_withdrawal", "schema_version": "1",
                         "order_id": order["order_id"], "order_number": order["order_number"],
                         "order_status": "WITHDRAWN", "data_deleted": True, "resolution_deleted": True,
                         "next_steps": ["SUBMIT_DESIGN_AGAIN_TO_ORDER"]}, None
        if p[2:] == ["bank-transfer-reports"] and method == "POST":
            _authorize(state, headers, "commercial:payment:report")
            if order is None:
                raise Refusal(404, "ORDER_NOT_FOUND", "no such order")

            def report() -> dict[str, Any]:
                order["payment"]["state"] = "CONFIRMED" if state.confirm_on_report else "PENDING_CONFIRMATION"
                return {}

            status, document = _idempotent(state, headers, "report", body, report)
            return status, {"schema": "opensci.commercial.bank_transfer_report", "schema_version": "1",
                            "replayed": document["replayed"], "order": order}, None
        _authorize(state, headers, "commercial:order:read")
        if order is None:
            raise Refusal(404, "ORDER_NOT_FOUND", "no such order")
        if len(p) == 2:
            return 200, order, None
        return 200, {"schema": "opensci.commercial.localized_presentation", "locale": query["locale"][0],
                     "document": "/".join(p[2:]), "order_number": order["order_number"]}, None

    if p == ["artifact-sets"] and method == "POST":
        _authorize(state, headers, "commercial:assembly-artifact:write")
        if state.artifact_upload_refusals:
            refusal = state.artifact_upload_refusals.pop(0)
            if refusal is not None:
                raise refusal
        manifest_bytes, files = _multipart(headers, body)
        manifest = ClientAssemblyArtifactManifestV1.model_validate(json.loads(manifest_bytes))
        for entry in manifest.files:
            data = files.get(entry.filename)
            if data is None or hashlib.sha256(data).hexdigest() != entry.sha256:
                raise Refusal(422, "ARTIFACT_FILE_INTEGRITY_FAILED", "a file does not match the manifest")

        def store() -> dict[str, Any]:
            set_id = f"00000000-0000-4000-8000-{len(state.artifact_sets) + 1:012d}"
            view = {"schema": "opensci.client_assembly_artifact_set", "schema_version": "1.0", "artifact_set_id": set_id,
                    "artifact_kind": manifest.artifact_kind.value, "commercial_resolution_id": manifest.binding.commercial_resolution_id,
                    "artifact_set_digest": manifest.artifact_set_digest,
                    "files": [{"role": e.role.value, "filename": e.filename, "size_bytes": e.size_bytes, "sha256": e.sha256}
                              for e in manifest.files]}
            state.artifact_sets[set_id] = view
            for entry in manifest.files:
                state.artifact_files[(set_id, entry.role.value)] = files[entry.filename]
            return view

        status, document = _idempotent(state, headers, "artifact", body, store, digest=manifest.artifact_set_digest)
        return status, document, None
    if p[:1] == ["artifact-sets"] and len(p) >= 2:
        _authorize(state, headers, "commercial:assembly-artifact:read")
        view = state.artifact_sets.get(p[1])
        if view is None:
            raise Refusal(404, "ARTIFACT_SET_NOT_FOUND", "no such artifact set")
        if len(p) == 2:
            return 200, {**view, "replayed": False}, None
        if p[2] == "files" and len(p) == 4 and (p[1], p[3]) in state.artifact_files:
            return 200, state.artifact_files[(p[1], p[3])], {"Content-Disposition": "attachment"}
        raise Refusal(404, "ARTIFACT_SET_NOT_FOUND", "no such file")
    raise Refusal(404, "ROUTE_NOT_FOUND", "no such route")
