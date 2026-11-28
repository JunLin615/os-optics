"""``OpenSciClient``: the OpenSci Commercial API from Python, with the Account token and protected deliveries handled
inside the client. See ``os_optics.client`` for the workflow."""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import sys
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..local.submission import (
    MAX_REPORTED_PROBLEMS,
    SUBMISSION_SCHEMA,
    SUBMISSION_SCHEMA_VERSION,
    RunDirectoryInconsistentError,
    canonical_json_bytes,
    idempotency_key_for,
)
from .._version import __version__ as _engine_version
from ..local.submission import submission_document as _submission_document
from .bom_review import purchase_bom_body
from .credentials import AccountToken, resolve_token
from .errors import (
    API_URL_MISSING,
    ARTIFACT_DOWNLOAD_MISMATCH,
    RUN_DIRECTORY_INCONSISTENT,
    TOKEN_MISSING,
    ArtifactIntegrityError,
    LocalInputError,
    OpenSciApiError,
    OpenSciConfigurationError,
    ProtectedContentError,
    Recovery,
)
from .transport import (
    DEFAULT_MAX_RETRY_SECONDS,
    DEFAULT_RETRIES,
    DEFAULT_TIMEOUT_SECONDS,
    ENV_API_URL,
    HttpResponse,
    Transport,
)

if TYPE_CHECKING:
    from .._vendor.shared_contracts.assembly_detail import VerifiedAssemblyDetailV1
    from .._vendor.shared_contracts.assembly_preview import AssemblyLocalValidationV1, AssemblyPreviewPackageV1, CheckerTolerancesV1
    from ..assembly_drawing import AssemblyDrawing

LOCALES = ("zh-CN", "en")
#: The status of a service refusal for a request sent without a token (a service before the BOM-only release).
HTTP_UNAUTHORIZED = 401
ORDER_DOCUMENTS = {"order": "presentation", "quotation": "quotation/presentation", "contract": "contract/presentation"}
TOKEN_PROFILES = ("agent", "review")
DEFAULT_TOKEN_LIFETIME_HOURS = 168
MAX_TOKEN_LIFETIME_HOURS = 720
BOUNDARY_BYTES = 16
MAX_SEGMENT_CHARS = 200
#: How many OpenSci-cancelled Orders of one resolution ``create_order`` steps past before returning the last one.
MAX_REORDERS_AFTER_CANCELLATION = 8

#: Feedback to OpenSci (``OpenSciClient.submit_feedback``): the categories and the length limits the service enforces.
FEEDBACK_CATEGORIES = ("PROBLEM", "FEATURE_REQUEST", "DOCUMENTATION")
FEEDBACK_SUMMARY_CHARS = (5, 200)
FEEDBACK_DETAILS_CHARS = (1, 8000)
#: The context fields of a feedback item and the longest text of each (``request_ids``: up to 10 ids of up to 80 chars).
FEEDBACK_CONTEXT_CHARS = {"engine_version": 40, "runtime": 80, "catalog_version": 40, "command": 500, "error_code": 80,
                          "resolution_id": 36, "order_id": 36, "locale": 5, "agent": 80}
FEEDBACK_MAX_REQUEST_IDS = 10
FEEDBACK_REQUEST_ID_CHARS = 80
#: The variable naming the Agent product that sends feedback (``context.agent`` when the call names none).
ENV_AGENT_NAME = "OPENSCI_AGENT_NAME"
#: An OpenSci Account token anywhere in a text (feedback holding one is refused before anything is sent).
CREDENTIAL_IN_TEXT = re.compile(r"oat_[0-9a-fA-F]{32}_[0-9a-fA-F]{64}")
UUID_TEXT = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")

#: The recovery of each protected-delivery refusal (``ProtectedDeliveryErrorCode`` value -> ``Recovery``).
PROTECTED_RECOVERY = {
    "GRANT_EXPIRED": Recovery.REQUEST_NEW_DELIVERY,
    "DECRYPTION_FAILED": Recovery.REQUEST_NEW_DELIVERY,
    "ENVELOPE_MALFORMED": Recovery.REQUEST_NEW_DELIVERY,
    "GRANT_MALFORMED": Recovery.REQUEST_NEW_DELIVERY,
    "GRANT_ENVELOPE_MISMATCH": Recovery.REQUEST_NEW_DELIVERY,
    "PREVIEW_PACKAGE_MISMATCH": Recovery.REQUEST_NEW_DELIVERY,
    "CRYPTO_UNAVAILABLE": Recovery.FIX_REQUEST,
}


#: The drawing schema 3.0 names of the drawing file roles -> the role the artifact store and drawing schema 4.0 use.
LEGACY_ARTIFACT_ROLES = {"ASSEMBLY_DRAWING_SVG": "DRAWING_SVG", "ASSEMBLY_DRAWING_PDF": "DRAWING_PDF"}


def artifact_role(role: str) -> str:
    """The stored role of an artifact file role (a drawing schema 3.0 role name is mapped to its current name)."""

    return LEGACY_ARTIFACT_ROLES.get(role, role)


def _segment(value: str, what: str) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_SEGMENT_CHARS:
        raise ValueError(f"{what} must be a non-empty string")
    return urllib.parse.quote(value, safe="")


def submission_document(run: Any, *, customer_constraints: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The commercial submission of one local run: its Project, Project hash and Local Validation record
    (``os_optics.local.submission.submission_document``, which also documents the transport-free helpers
    ``canonical_json_bytes`` and ``idempotency_key_for`` re-exported here).

    ``run`` is the ``LocalOpticalRun`` of ``os_optics.local.run_optical_project``, the run directory written by
    ``os-optics project run --out-dir`` (checked with ``verify_run_artifacts`` first) or its ``{file name: bytes}``
    files. A run that does not verify raises ``LocalInputError`` ``RUN_DIRECTORY_INCONSISTENT``, the run of a Project with
    configurations ``CONFIGURATIONS_NOT_SUBMITTABLE`` (submit one configuration's run directory instead); nothing is sent.
    ``customer_constraints`` (optional): ``max_board_size_mm`` ``[x, y]``, ``require_all_retained_dofs``,
    ``include_breadboard`` (false: your own optical table), ``assembly_service`` (requires the breadboard),
    ``purchase_scope`` (``OPTICS_ONLY``: the optical elements only), ``spare_units`` (``{OpenSci SKU: count}``) and
    ``mounting`` (``{instance_id: {"beam_height_mm", "incidence_deg"?}}``: for a design whose ``design_intent`` is
    ``OPTICAL_SYSTEM`` bought with OpenSci mounts and no platform, the beam height of each OpenSci optic). These keys
    encode the order options mechanical supply / platform / assembly (``purchase_options``)."""

    try:
        return _submission_document(run, customer_constraints=customer_constraints)
    except RunDirectoryInconsistentError as exc:
        raise LocalInputError(exc.code or RUN_DIRECTORY_INCONSISTENT, str(exc), details=exc.details,
                              recovery=Recovery.FIX_REQUEST) from None


#: The order options of a design order (``customer_constraints``) -- mechanical supply, platform, assembly -- and the
#: ``customer_constraints`` keys each combination is sent as (the purchase presets; absent keys keep their defaults).
MECHANICAL_SUPPLY_OPTIONS = ("NONE", "DERIVED")
PLATFORM_OPTIONS = ("NONE", "BREADBOARD")
ASSEMBLY_OPTIONS = ("CUSTOMER", "OPENSCI")
#: The kinds of OpenSci mechanical products (``OpenSciClient.mechanical_products``).
MECHANICAL_KINDS = ("MOUNT", "POST", "POST_HOLDER", "BREADBOARD")
PART_LIST_SCHEMA = "opensci.commercial.part_list_submission"
PART_LIST_SCHEMA_VERSION = "1"
#: The part-list fields; ``optics_source`` / ``platform`` / ``assembly`` are the other order options (with a part list
#: only ``CATALOG`` / ``NONE`` / ``CUSTOMER`` are supplied: a listed breadboard is an ordinary line).
PART_LIST_FIELDS = ("schema", "schema_version", "lines", "mechanical_supply", "mounting", "optics_source", "platform",
                    "assembly")
PART_LIST_OPTION_FIELDS = ("optics_source", "platform", "assembly")
#: A part list that is not a part-list document (checked here; nothing is sent).
PART_LIST_INVALID = "PART_LIST_INVALID"
#: The service's refusal of a combination of order options (the same code names are used for a local refusal).
PURCHASE_OPTIONS_CONFLICT = "PURCHASE_OPTIONS_CONFLICT"
ASSEMBLY_SERVICE_REQUIRES_BREADBOARD = "ASSEMBLY_SERVICE_REQUIRES_BREADBOARD"


def _option_conflict(code: str, message: str, fields: list[str]) -> LocalInputError:
    return LocalInputError(code, message, details={"conflicting_options": fields}, recovery=Recovery.FIX_REQUEST)


def design_order_constraints(*, mechanical_supply: str | None = None, platform: str | None = None,
                             assembly: str | None = None) -> dict[str, Any]:
    """The ``customer_constraints`` keys of the order options of a design order (``None``: not chosen).

    Mechanical supply ``NONE`` is ``purchase_scope: OPTICS_ONLY`` (no platform and no assembly service go with it);
    ``DERIVED`` (OpenSci selects mount, post and post holder for each optic it supplies) is the default and sends no
    key. Platform ``NONE`` with derived mechanics is ``include_breadboard: false`` (your own table, or no layout at all for
    an ``OPTICAL_SYSTEM`` design); ``BREADBOARD`` is the default. Assembly ``OPENSCI`` is ``assembly_service: true`` and
    needs derived mechanics and the breadboard; ``CUSTOMER`` is the default. A combination the service refuses raises
    ``LocalInputError`` with the service's code (``PURCHASE_OPTIONS_CONFLICT``, ``ASSEMBLY_SERVICE_REQUIRES_BREADBOARD``);
    nothing is sent."""

    for value, allowed, name in ((mechanical_supply, MECHANICAL_SUPPLY_OPTIONS, "mechanical_supply"),
                                 (platform, PLATFORM_OPTIONS, "platform"), (assembly, ASSEMBLY_OPTIONS, "assembly")):
        if value is not None and value not in allowed:
            raise ValueError(f"{name} must be one of {', '.join(allowed)}")
    constraints: dict[str, Any] = {}
    if mechanical_supply == "NONE":
        chosen = [name for name, value in (("platform", platform == "BREADBOARD"), ("assembly", assembly == "OPENSCI"))
                  if value]
        if chosen:
            raise _option_conflict(PURCHASE_OPTIONS_CONFLICT, "without OpenSci mechanics (mechanical supply NONE) there "
                                   "is no platform and no assembly service: leave out " + " and ".join(chosen)
                                   + ", or choose mechanical supply DERIVED", ["mechanical_supply", *chosen])
        constraints["purchase_scope"] = "OPTICS_ONLY"
        return constraints
    if platform == "NONE":
        if assembly == "OPENSCI":
            raise _option_conflict(ASSEMBLY_SERVICE_REQUIRES_BREADBOARD, "the OpenSci assembly and alignment service "
                                   "assembles the system on the OpenSci breadboard: choose platform BREADBOARD or "
                                   "assembly CUSTOMER", ["platform", "assembly"])
        constraints["include_breadboard"] = False
    if assembly == "OPENSCI":
        constraints["assembly_service"] = True
    return constraints


def part_list_document(document: Mapping[str, Any], *, mechanical_supply: str | None = None) -> dict[str, Any]:
    """The request body of a part-list order (``POST /api/v1/part-list-submissions``) from a part-list document.

    ``document``: ``{"lines": [{"line_id", "opensci_sku", "quantity"}], "mechanical_supply": "NONE"|"DERIVED",
    "mounting": {line_id: {"beam_height_mm", "retained_dofs"?, "incidence_deg"?}}}`` (``schema`` /
    ``schema_version`` are added when absent). Lines are OpenSci part numbers: optics (``OS-OPT-*``, from ``catalog
    list``) and mechanical products and breadboards (``OS-MECH-*``, from ``mechanical_products``), lines with unique
    ``line_id`` and quantities of at least 1, within the service's purchase limits (``GET /api/v1``
    ``commercial_rules.purchase_limits``). With ``mechanical_supply`` ``DERIVED`` OpenSci selects mount, post
    and post holder for each optic unit at the beam height its ``mounting`` entry states (measured from the surface
    the holders stand on); ``NONE`` (the default) supplies exactly the listed parts. ``optics_source`` (``CATALOG``),
    ``platform`` (``NONE``) and ``assembly`` (``CUSTOMER``) may be stated too and are sent as given: a part list takes
    catalog optics, no platform (a breadboard is a listed line) and your own assembly; other values are refused by the
    service (``ORDER_OPTION_ON_HOLD``, ``PURCHASE_OPTIONS_CONFLICT``).

    ``mechanical_supply`` (optional) replaces the document's choice; ``NONE`` then leaves the ``mounting`` entries out.
    A document that is not a JSON object with a list of lines raises ``LocalInputError`` ``PART_LIST_INVALID``
    (nothing is sent); everything else is checked by the service."""

    if not isinstance(document, Mapping) or not isinstance(document.get("lines"), list):
        raise LocalInputError(PART_LIST_INVALID, "a part list is a JSON object with a list of lines",
                              details={"fields": ["lines"]}, recovery=Recovery.FIX_REQUEST)
    unknown = sorted(set(document) - set(PART_LIST_FIELDS))
    if unknown:
        raise LocalInputError(PART_LIST_INVALID, f"unknown part-list fields: {', '.join(unknown)}",
                              details={"fields": unknown}, recovery=Recovery.FIX_REQUEST)
    if mechanical_supply is not None and mechanical_supply not in MECHANICAL_SUPPLY_OPTIONS:
        raise ValueError(f"mechanical_supply must be one of {', '.join(MECHANICAL_SUPPLY_OPTIONS)}")
    body: dict[str, Any] = {"schema": document.get("schema", PART_LIST_SCHEMA),
                            "schema_version": document.get("schema_version", PART_LIST_SCHEMA_VERSION),
                            "lines": list(document["lines"]),
                            "mechanical_supply": mechanical_supply or document.get("mechanical_supply") or "NONE"}
    if document.get("mounting") is not None and mechanical_supply != "NONE":
        body["mounting"] = document["mounting"]
    body.update({name: document[name] for name in PART_LIST_OPTION_FIELDS if name in document})
    return body


def runtime_label() -> str:
    """This Python runtime in a few words, e.g. ``python 3.13 linux`` (``context.runtime`` of feedback)."""

    return f"python {sys.version_info.major}.{sys.version_info.minor} {sys.platform}"


#: The service's control-character rules (C0, DEL, C1, line/paragraph separators; details keep tab and line feed).
_FEEDBACK_SUMMARY_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f  ]")
_FEEDBACK_DETAILS_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f  ]")


def _feedback_text(value: Any, what: str, limits: tuple[int, int], *, multiline: bool) -> str:
    """Trimmed text within ``limits``; control characters are refused (the service's rule), except line breaks and
    tabs in multi-line text (the details)."""

    if not isinstance(value, str):
        raise ValueError(f"{what} must be text")
    text = value.strip()
    if not limits[0] <= len(text) <= limits[1]:
        raise ValueError(f"{what} must be {limits[0]} to {limits[1]} characters (it is {len(text)})")
    if multiline:
        text = text.replace("\r\n", "\n")
    if (_FEEDBACK_DETAILS_CONTROL if multiline else _FEEDBACK_SUMMARY_CONTROL).search(text):
        raise ValueError(f"{what} must be one line without control characters" if not multiline
                         else f"{what} must not contain control characters other than line breaks and tabs")
    return text


def _feedback_context(context: Mapping[str, Any] | None, environ: Mapping[str, str]) -> dict[str, Any]:
    if context is not None and not isinstance(context, Mapping):
        raise ValueError("context must be a mapping of feedback context fields")
    given = {k: v for k, v in (context or {}).items() if v is not None and v != []}
    merged: dict[str, Any] = {"engine_version": _engine_version, "runtime": runtime_label()}
    agent = (environ.get(ENV_AGENT_NAME) or "").strip()
    if agent and "agent" not in given:
        if len(agent) > FEEDBACK_CONTEXT_CHARS["agent"]:
            raise ValueError(f"{ENV_AGENT_NAME} must be at most {FEEDBACK_CONTEXT_CHARS['agent']} characters")
        merged["agent"] = agent
    merged.update(given)
    unknown = sorted(set(merged) - set(FEEDBACK_CONTEXT_CHARS) - {"request_ids"})
    if unknown:
        raise ValueError(f"unknown feedback context fields: {', '.join(unknown)} (known: "
                         f"{', '.join(sorted([*FEEDBACK_CONTEXT_CHARS, 'request_ids']))})")
    for name, limit in FEEDBACK_CONTEXT_CHARS.items():
        value = merged.get(name)
        if value is not None and (not isinstance(value, str) or not value or len(value) > limit):
            raise ValueError(f"context.{name} must be text of 1 to {limit} characters")
    for name in ("resolution_id", "order_id"):
        if name in merged and not UUID_TEXT.fullmatch(merged[name]):
            raise ValueError(f"context.{name} must be a UUID (as the service printed it)")
    if "locale" in merged and merged["locale"] not in LOCALES:
        raise ValueError(f"context.locale must be one of {', '.join(LOCALES)}")
    if "request_ids" in merged:
        ids = merged["request_ids"]
        if isinstance(ids, str) or not isinstance(ids, (list, tuple)) or len(ids) > FEEDBACK_MAX_REQUEST_IDS or not all(
                isinstance(i, str) and 0 < len(i) <= FEEDBACK_REQUEST_ID_CHARS for i in ids):
            raise ValueError(f"context.request_ids must be a list of up to {FEEDBACK_MAX_REQUEST_IDS} request ids "
                             f"(each up to {FEEDBACK_REQUEST_ID_CHARS} characters)")
        merged["request_ids"] = list(ids)
    return merged


def feedback_document(*, category: str, summary: str, details: str, context: Mapping[str, Any] | None = None,
                      environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """The request body of one feedback item (``POST /api/v1/feedback``), checked here before anything is sent.

    ``summary`` and ``details`` are trimmed of surrounding whitespace. ``context`` always carries ``engine_version``
    and ``runtime`` (this installation; a given value wins), and ``agent`` from ``OPENSCI_AGENT_NAME`` (``environ``,
    default the process environment) when the call names none. Raises ``ValueError`` for an unknown category, a text
    outside its limits, an unknown context field, or text that holds an OpenSci Account token (the token is never
    repeated in the message)."""

    if category not in FEEDBACK_CATEGORIES:
        raise ValueError(f"category must be one of {', '.join(FEEDBACK_CATEGORIES)}")
    body: dict[str, Any] = {"category": category,
                            "summary": _feedback_text(summary, "summary", FEEDBACK_SUMMARY_CHARS, multiline=False),
                            "details": _feedback_text(details, "details", FEEDBACK_DETAILS_CHARS, multiline=True),
                            "context": _feedback_context(context, os.environ if environ is None else environ)}
    texts = {"summary": body["summary"], "details": body["details"],
             **{f"context.{k}": v for k, v in body["context"].items() if isinstance(v, str)},
             **{f"context.request_ids[{i}]": v for i, v in enumerate(body["context"].get("request_ids", ()))}}
    holding = [name for name, text in texts.items() if CREDENTIAL_IN_TEXT.search(text)]
    if holding:
        raise ValueError(f"the feedback contains an OpenSci Account token ({', '.join(holding)}): remove it and send "
                         "again; never include tokens, passwords or other credentials")
    return body


@dataclass(frozen=True)
class IssuedToken:
    """A newly issued Account token: its non-secret record (``status``) and, on the first issuance only, the
    credential (``None`` on a replay: OpenSci keeps no copy of it). ``repr`` never shows the credential."""

    status: dict[str, Any]
    credential: AccountToken | None

    @property
    def token_id(self) -> str | None:
        value = self.status.get("token_id")
        return value if isinstance(value, str) else None


def _protected_error(exc: Any) -> ProtectedContentError:
    code = getattr(exc.code, "value", str(exc.code))
    details: dict[str, Any] = {}
    if getattr(exc, "problems", ()):
        details["problems"] = list(exc.problems)[:MAX_REPORTED_PROBLEMS]
    if code == "CRYPTO_UNAVAILABLE":
        from ..protected_delivery import PROTECTED_EXTRA

        details["install"] = f'python -m pip install "{PROTECTED_EXTRA}"'
    return ProtectedContentError(code, exc.message, details=details,
                                 recovery=PROTECTED_RECOVERY.get(code, Recovery.CONTACT_OPENSCI))


class OpenSciClient:
    """A client of one OpenSci service with one Account token.

    ``OpenSciClient.from_environment()`` reads ``OPENSCI_API_URL`` and finds the token (``os_optics.client.
    credentials``). Every call returns the service's JSON document (a ``dict``) or a verified local object, and raises
    ``OpenSciApiError`` (with ``code``, ``recovery``, ``http_status``, ``request_id``) when the service refuses or
    cannot be reached.

    ``anonymous=True`` (the commands of routes the service has retired): without a token the client still sends its
    requests, unauthenticated, because the service answers a retired route (``410``) before it authenticates; an
    earlier service that answers ``401`` instead is reported as ``TOKEN_MISSING`` with its handoff
    (``anonymous_refusal``). ``token`` is then None."""

    def __init__(self, api_url: str, token: Any = None, *, timeout: float = DEFAULT_TIMEOUT_SECONDS,
                 retries: int = DEFAULT_RETRIES, allow_insecure_http: bool = False,
                 environ: Mapping[str, str] | None = None,
                 max_retry_seconds: float = DEFAULT_MAX_RETRY_SECONDS, anonymous: bool = False) -> None:
        self._missing: OpenSciConfigurationError | None = None
        try:
            self._token: AccountToken | None = resolve_token(token, environ=environ)
        except OpenSciConfigurationError as exc:
            if not anonymous or exc.code != TOKEN_MISSING:
                raise
            self._token, self._missing = None, exc
        self._environ: Mapping[str, str] = os.environ if environ is None else environ
        self._http = Transport(api_url, self._token, timeout=timeout, retries=retries,
                               allow_insecure_http=allow_insecure_http, max_retry_seconds=max_retry_seconds)

    @classmethod
    def from_environment(cls, token: Any = None, *, api_url: str | None = None, environ: Mapping[str, str] | None = None,
                         **options: Any) -> OpenSciClient:
        """A client of ``api_url`` (default ``OPENSCI_API_URL``) with ``token`` (default: ``OPENSCI_TOKEN``, then
        ``OPENSCI_TOKEN_FILE``, then ``~/.opensci/token``)."""

        env = os.environ if environ is None else environ
        url = api_url or env.get(ENV_API_URL, "").strip()
        if not url:
            raise OpenSciConfigurationError(API_URL_MISSING, f"no OpenSci service URL: set {ENV_API_URL} to the "
                                            "OpenSci service URL your OpenSci Account shows with your Agent token",
                                            details={"variable": ENV_API_URL},
                                            recovery=Recovery.FIX_REQUEST)
        return cls(url, token, environ=env, **options)

    @property
    def api_url(self) -> str:
        return self._http.api_url

    @property
    def token(self) -> AccountToken | None:
        """The Account token in use (``repr`` shows only its token id and source); None for an anonymous client."""

        return self._token

    def anonymous_refusal(self, error: OpenSciApiError) -> OpenSciApiError:
        """For an anonymous client: the ``TOKEN_MISSING`` error (with its handoff) instead of a service's ``401``;
        any other error unchanged."""

        if self._missing is not None and error.http_status == HTTP_UNAUTHORIZED:
            return self._missing
        return error

    def __repr__(self) -> str:
        return f"OpenSciClient(api_url={self.api_url!r}, token={self._token!r})"

    # ------------------------------------------------------------------------------------------ plumbing

    def _get(self, path: str, query: Mapping[str, str] | None = None) -> Any:
        return self._http.request("GET", path, query=query).json()

    def _post(self, path: str, body: Any = None, *, key: str | None = None) -> HttpResponse:
        return self._http.request("POST", path, json_body=body, idempotency_key=key)

    @staticmethod
    def _locale(locale: str) -> dict[str, str]:
        if locale not in LOCALES:
            raise ValueError(f"locale must be one of {', '.join(LOCALES)}")
        return {"locale": locale}

    # ------------------------------------------------------------------------------------------ Account token

    def token_status(self) -> dict[str, Any]:
        """The token in use: organization, lifecycle, spend status, scopes and current capabilities.

        ``expires_at`` is null for a permanent-until-revoked token; a consumed single-use token
        still permits reads when it carries the relevant read scope.
        """

        return self._get("/account/token")

    def tokens(self) -> dict[str, Any]:
        """The organization member's tokens, newest first (non-secret records; scope ``account:token:manage``)."""

        return self._get("/account/tokens")

    def issue_token(self, *, profile: str | None = None, scopes: list[str] | None = None,
                    lifetime_hours: int = DEFAULT_TOKEN_LIFETIME_HOURS, label: str | None = None,
                    idempotency_key: str | None = None, lifecycle: str = "TIME_LIMITED") -> IssuedToken:
        """Issue a new token for the same user and organization (scope ``account:token:manage``).

        Give ``profile`` (``"agent"``: every customer commercial scope; ``"review"``: resolve, preview, read Orders
        and artifacts) or an explicit ``scopes`` list; with neither, ``profile="agent"``. ``lifetime_hours`` is 1 to
        720 for bounded tokens. ``SINGLE_USE`` spends placement after the first new Order (reads remain);
        ``PERMANENT`` has no expiry and requires a permanent issuing credential. The credential is returned once,
        in ``IssuedToken.credential``;
        store it with ``os_optics.client.save_token_file``."""

        if profile is not None and scopes is not None:
            raise ValueError("give profile or scopes, not both")
        if profile is None and scopes is None:
            profile = "agent"
        if profile is not None and profile not in TOKEN_PROFILES:
            raise ValueError(f"profile must be one of {', '.join(TOKEN_PROFILES)}")
        if not 1 <= int(lifetime_hours) <= MAX_TOKEN_LIFETIME_HOURS:
            raise ValueError(f"lifetime_hours must be 1 to {MAX_TOKEN_LIFETIME_HOURS}")
        if lifecycle not in ("SINGLE_USE", "TIME_LIMITED", "PERMANENT"):
            raise ValueError("lifecycle must be SINGLE_USE, TIME_LIMITED or PERMANENT")
        body = {"scopes": list(scopes) if scopes is not None else None, "profile": profile,
                "lifetime_hours": int(lifetime_hours), "label": label, "lifecycle": lifecycle}
        # A fresh key per call: issuing twice on purpose must give two tokens. A retry inside this call (transport
        # retries) reuses it, so a lost response never mints a second token.
        key = idempotency_key or f"opensci-token-issue-{secrets.token_hex(BOUNDARY_BYTES)}"
        response = self._post("/account/tokens", body, key=key)
        document = response.json()
        response.take_body()
        credential = document.pop("credential", None)
        status = document.get("token") if isinstance(document.get("token"), dict) else {}
        return IssuedToken(status=status, credential=AccountToken(credential, source="issued") if credential else None)

    def revoke_token(self, token_id: str) -> dict[str, Any]:
        """Revoke one of the organization's tokens by its token id (scope ``account:token:manage``)."""

        return self._http.request("DELETE", f"/account/tokens/{_segment(token_id, 'token_id')}").json()

    def revoke_current_token(self) -> dict[str, Any]:
        """Revoke the token this client uses (any valid token may revoke itself). The client cannot be used afterwards."""

        return self._http.request("DELETE", "/account/token").json()

    # ------------------------------------------------------------------------------------------ shipping addresses

    def addresses(self) -> dict[str, Any]:
        """The organization's shipping addresses (mainland China), the default first, with ``default_address_id``
        (scope ``commercial:order:place`` or ``commercial:order:read``). Addresses are personal data: show them to the
        person you work for; never log them."""

        return self._get("/account/addresses")

    def add_address(self, *, recipient_name: str, mobile_phone: str, province: str, city: str, district: str,
                    street_address: str, postal_code: str | None = None, label: str | None = None,
                    make_default: bool = False, idempotency_key: str | None = None) -> dict[str, Any]:
        """Add a shipping address in mainland China to the organization's address book (scope
        ``commercial:order:place``). ``mobile_phone``: 11 digits starting 13-19; ``province``: a mainland
        province-level division in Chinese (广东省 or 广东). The first address becomes the default; ``make_default``
        makes this one the default. The idempotency key is derived from the address, so repeating the call returns the
        same address (``replayed: true``). Returns ``{"replayed", "address"}``."""

        body = {"recipient_name": recipient_name, "mobile_phone": mobile_phone, "province": province, "city": city,
                "district": district, "street_address": street_address, "postal_code": postal_code, "label": label,
                "make_default": bool(make_default)}
        key = idempotency_key or idempotency_key_for("shipping-address", canonical_json_bytes(body))
        return self._post("/account/addresses", body, key=key).json()

    # ------------------------------------------------------------------------------------------ feedback

    def submit_feedback(self, *, category: str, summary: str, details: str, context: Mapping[str, Any] | None = None,
                        idempotency_key: str | None = None) -> dict[str, Any]:
        """Send feedback to OpenSci: a problem met while doing what the user needed, a feature request, or a
        documentation gap (any OpenSci Account token: Agent or Review profile).

        ``category``: ``PROBLEM``, ``FEATURE_REQUEST`` or ``DOCUMENTATION``; ``summary``: one line, 5 to 200
        characters; ``details``: 1 to 8000 characters -- what the user needed, what you did (commands), what happened,
        what you expected. ``context`` (optional): ``error_code``, ``request_ids`` (up to 10), ``resolution_id``,
        ``order_id``, ``command``, ``catalog_version``, ``locale`` (``zh-CN`` or ``en``), ``agent``; ``engine_version``
        and ``runtime`` are added (``feedback_document``). One problem or suggestion per call; include no tokens,
        passwords, payment data or personal data beyond what the problem needs, and tell the user what you sent.

        The idempotency key is derived from the feedback itself, so sending the same feedback again returns the first
        receipt (``replayed: true``). Returns the receipt ``{"feedback_id", "received_at", "status": "RECEIVED",
        "replayed"}``. Refusals: ``FEEDBACK_INVALID`` (recovery ``FIX_REQUEST``; ``details.reason`` and
        ``details.invalid_fields``) and ``FEEDBACK_RATE_LIMITED`` (HTTP 429, recovery ``RETRY_LATER``; the advised wait
        is ``retry_after_seconds``): at most 10 per token per hour and 50 per organization per day."""

        body = feedback_document(category=category, summary=summary, details=details, context=context,
                                 environ=self._environ)
        key = idempotency_key or idempotency_key_for("feedback", canonical_json_bytes(body))
        return self._post("/feedback", body, key=key).json()

    # ------------------------------------------------------------------------------------------ resolution

    def submit(self, run: Any, *, customer_constraints: Mapping[str, Any] | None = None,
               idempotency_key: str | None = None) -> dict[str, Any]:
        """Submit a locally validated run for commercial resolution (scope ``commercial:resolve``).

        ``run``: a ``LocalOpticalRun`` or its run directory (it needs the manifest, the Project, the Engine request and
        result and the Local Validation record; the optical scene, its GLB and the ray data may be absent). The idempotency key is derived from the submission itself,
        so submitting the same run again returns the same resolution (``replayed: true``); after OpenSci changed its
        catalog, prices or supply since, the same call returns a new resolution on the current basis (the step after
        ``RESOLUTION_NO_LONGER_AVAILABLE``). Returns the resolution document: ``resolution_id`` and the Customer
        Purchase BOM with prices."""

        document = submission_document(run, customer_constraints=customer_constraints)
        key = idempotency_key or idempotency_key_for("submit", canonical_json_bytes(document))
        return self._post("/commercial-submissions", document, key=key).json()

    def submit_part_list(self, document: Mapping[str, Any], *, mechanical_supply: str | None = None,
                         idempotency_key: str | None = None) -> dict[str, Any]:
        """Price a part list for an Order (scope ``commercial:resolve``): OpenSci part numbers and quantities, without a
        design (``part_list_document`` states the document and ``mechanical_supply``). The BOM-only service refuses
        ``mechanical_supply`` ``DERIVED`` and ``mounting`` (``ORDER_OPTION_RETIRED``); ``quote_bom`` sends a purchase BOM
        with its review and confirmation.

        The idempotency key is derived from the request body, so submitting the same list again returns the same
        resolution (``replayed: true``). Returns the resolution document, as ``submit`` does: ``resolution_id``, the
        Customer Purchase BOM with per-part prices, ``order_options`` (``basis`` ``PART_LIST``), the delivery terms
        and the agreement. Order it with ``create_order``. Refusals name the lines: ``PART_NOT_ORDERABLE``
        (``details.line_ids``: part numbers OpenSci does not supply), ``MOUNTING_INPUT_REQUIRED`` (an optic line
        without its beam height) and ``MOUNTING_NOT_AVAILABLE`` (``details``: ``line_id``, ``reason``, the reachable
        range when known)."""

        body = part_list_document(document, mechanical_supply=mechanical_supply)
        key = idempotency_key or idempotency_key_for("part-list-submit", canonical_json_bytes(body))
        return self._post("/part-list-submissions", body, key=key).json()

    def quote_bom(self, document: Mapping[str, Any], *, schema_version: str = "2",
                  idempotency_key: str | None = None) -> dict[str, Any]:
        """Quote a purchase BOM (scope ``commercial:resolve``): OpenSci part numbers and quantities, with their review
        (``os_optics.client.bom_review``; the body is ``purchase_bom_body(document, schema_version=...)``).

        The idempotency key is derived from the request body, so sending the same BOM again returns the same quote
        (``replayed: true``), also after signing in. Returns the answer (HTTP 201): ``resolution_id`` and the
        resolution with its ``bom_review`` (``ORDERABLE`` or ``CHANGES_REQUIRED``), the Customer Purchase BOM with prices
        of the lines that can be supplied, and the agreement. A BOM none of whose lines can be supplied raises
        ``BOM_NOT_ORDERABLE`` (422, ``details.bom_review``); a confirmation that does not match its review
        ``BOM_CONFIRMATION_INVALID`` (422, ``details.problems``); a reviewed quote that no longer exists
        ``RESOLUTION_NOT_FOUND`` (404)."""

        body = purchase_bom_body(document, schema_version=schema_version)
        key = idempotency_key or idempotency_key_for("part-list-submit", canonical_json_bytes(body))
        return self._post("/part-list-submissions", body, key=key).json()

    def mechanical_products(self, *, kind: str | None = None) -> dict[str, Any]:
        """OpenSci's mechanical products (scope ``commercial:resolve``): mounts, posts, post holders and breadboards
        that can be listed in a part list, sorted by part number (``OS-MECH-*``), with their names (en / zh-CN), a
        description and their engineering facts (``facts``: the centre height and the optic a mount holds, its
        adjustment axes and post thread; a post's length, diameter and thread; a breadboard's size, hole pitch and
        thread). ``kind`` (optional): ``MOUNT``, ``POST``, ``POST_HOLDER`` or ``BREADBOARD``. Prices come with a
        resolution (``submit_part_list``)."""

        if kind is not None and kind not in MECHANICAL_KINDS:
            raise ValueError(f"kind must be one of {', '.join(MECHANICAL_KINDS)}")
        return self._get("/mechanical-products", {"kind": kind} if kind else None)

    def resolution_mounting_sheet(self, resolution_id: str) -> dict[str, Any]:
        """The mounting sheet of a resolution whose OpenSci mounts are chosen per optic, without a layout (a part list
        with mechanical supply ``DERIVED``, or an ``OPTICAL_SYSTEM`` design with derived mounts): for each optic unit its
        mount, post and post holder, the assembly steps, the beam-height arithmetic, the adjustment axes and the checks
        made, with English and Simplified Chinese text (scope ``commercial:resolve``). ``os_optics.mounting_sheet.
        render_mounting_sheet`` writes it as text."""

        return self._get(f"/resolutions/{_segment(resolution_id, 'resolution_id')}/mounting-sheet")

    def order_mounting_sheet(self, order_id: str) -> dict[str, Any]:
        """The mounting sheet of an Order (as ``resolution_mounting_sheet``; scope ``commercial:order:read``)."""

        return self._get(f"/orders/{_segment(order_id, 'order_id')}/mounting-sheet")

    def resolution(self, resolution_id: str) -> dict[str, Any]:
        """One resolution with its Customer Purchase BOM (scope ``commercial:resolve``)."""

        return self._get(f"/resolutions/{_segment(resolution_id, 'resolution_id')}")

    def resolution_presentation(self, resolution_id: str, *, locale: str = "zh-CN") -> dict[str, Any]:
        """One resolution presented for people in ``zh-CN`` or ``en``."""

        return self._get(f"/resolutions/{_segment(resolution_id, 'resolution_id')}/presentation", self._locale(locale))

    # ------------------------------------------------------------------------------------------ protected deliveries

    def _open_delivery(self, response: HttpResponse, opener: Any) -> Any:
        from ..protected_delivery import ProtectedDelivery, ProtectedDeliveryError

        failure: ProtectedContentError | None = None
        result: Any = None
        delivery: Any = None
        try:
            delivery = ProtectedDelivery.from_response(response.take_body())
            result = opener(delivery)
        except ProtectedDeliveryError as exc:
            failure = _protected_error(exc)
        finally:
            if delivery is not None:
                delivery.discard()
        if failure is not None:
            raise failure
        return result

    def _delivery_body(self, grant_lifetime_seconds: int | None) -> dict[str, Any] | None:
        return None if grant_lifetime_seconds is None else {"grant_lifetime_seconds": int(grant_lifetime_seconds)}

    def assembly_preview(self, resolution_id: str, *, grant_lifetime_seconds: int | None = None) -> AssemblyPreviewPackageV1:
        """The resolution's Experimental Lab Assembly: fetched, opened and verified in memory (scope
        ``commercial:assembly-preview:read``). Returns the ``AssemblyPreviewPackageV1``; nothing is written to disk."""

        from ..protected_delivery import open_preview_delivery

        path = f"/resolutions/{_segment(resolution_id, 'resolution_id')}/assembly-preview-deliveries"
        response = self._post(path, self._delivery_body(grant_lifetime_seconds))
        return self._open_delivery(response, lambda delivery: open_preview_delivery(
            delivery, expected_resolution_id=resolution_id))

    @staticmethod
    def validate_assembly(preview: AssemblyPreviewPackageV1,
                          tolerances: Mapping[str, float] | CheckerTolerancesV1 | None = None) -> AssemblyLocalValidationV1:
        """The local layout / collision check of an opened preview (``os_optics.assembly.validate_assembly_layout``).
        ``tolerances``: thresholds other than the defaults (``{name: value}`` over ``DEFAULT_TOLERANCES``, or a
        ``CheckerTolerancesV1``), recorded in the result; ``ValueError`` for an unknown name or a refused value."""

        from ..assembly import tolerances_with, validate_assembly_layout

        return validate_assembly_layout(preview, tolerances=tolerances_with(tolerances))

    def assembly_detail(self, resolution_id: str, *, preview: AssemblyPreviewPackageV1 | None = None,
                        grant_lifetime_seconds: int | None = None) -> VerifiedAssemblyDetailV1:
        """The resolution's Assembly Detail, opened in memory and verified against ``preview`` (fetched when not given;
        scope ``commercial:assembly-detail:read``).

        Available once OpenSci has confirmed receipt of the payment of an Order of this resolution. Before that it
        raises ``AssemblyDetailNotReleasedError`` with ``recovery`` ``CREATE_ORDER_FIRST`` or
        ``WAIT_FOR_PAYMENT_CONFIRMATION``."""

        from ..protected_delivery import open_detail_delivery

        package = preview if preview is not None else self.assembly_preview(resolution_id)
        path = f"/resolutions/{_segment(resolution_id, 'resolution_id')}/assembly-detail-deliveries"
        response = self._post(path, self._delivery_body(grant_lifetime_seconds))
        return self._open_delivery(response, lambda delivery: open_detail_delivery(
            delivery, preview=package, expected_resolution_id=resolution_id))

    def assembly_drawing(self, resolution_id: str, out_dir: str | Path | None = None, *,
                         preview: AssemblyPreviewPackageV1 | None = None,
                         validation: AssemblyLocalValidationV1 | None = None, locale: str = "en",
                         diagnostic: bool = False) -> AssemblyDrawing:
        """The Detailed Assembly Drawing of a resolution, generated on this device.

        Opens the preview (unless given) and the Assembly Detail in memory, runs the local assembly validation and
        generates the drawing (``os_optics.assembly_drawing.generate_assembly_drawing``). With ``out_dir`` the
        four drawing files (SVG, PDF, result, manifest) are written there, replacing an earlier drawing's; nothing else
        is written. ``diagnostic``, the drawing status (with it, a diagnostic sheet for a check that FAILED or was not
        completed; without it, a refusal) and the refusals are those of
        ``generate_assembly_drawing`` (``AssemblyDrawingError``); ``locale`` is the language of the sheet (``en`` or
        ``zh-CN``). ``validation``: a local assembly validation of the same preview already run on this device (e.g.
        with your own tolerances), used instead of running the check again. Nothing is uploaded (drawings stay on this
        device)."""

        from ..assembly_drawing import generate_assembly_drawing

        package = preview if preview is not None else self.assembly_preview(resolution_id)
        detail = self.assembly_detail(resolution_id, preview=package)
        drawing = generate_assembly_drawing(package, detail, locale=locale, validation=validation, diagnostic=diagnostic)
        if out_dir is not None:
            drawing.write(out_dir)
        return drawing

    # ------------------------------------------------------------------------------------------ artifacts
    # (Uploads of local assembly validations and drawings are retired: drawings stay on this device. The read routes
    # below are answered by a service before the BOM-only release; the BOM-only service answers 410 ROUTE_RETIRED.)

    def artifact_sets(self, resolution_id: str, *, artifact_kind: str | None = None) -> dict[str, Any]:
        """The resolution's artifact sets, oldest first (optionally of one ``artifact_kind``)."""

        query = {"artifact_kind": artifact_kind} if artifact_kind else None
        return self._get(f"/resolutions/{_segment(resolution_id, 'resolution_id')}/artifact-sets", query)

    def artifact_set(self, artifact_set_id: str) -> dict[str, Any]:
        """One stored artifact set: kind, bindings, generator and its files (role, filename, size, SHA-256)."""

        return self._get(f"/artifact-sets/{_segment(artifact_set_id, 'artifact_set_id')}")

    def artifact_file(self, artifact_set_id: str, role: str) -> bytes:
        """The exact bytes of one stored file, checked against the set's recorded size and SHA-256. ``role`` is the
        role the set lists (``DRAWING_PDF`` ...); a drawing schema 3.0 name (``ASSEMBLY_DRAWING_PDF``) is accepted."""

        role = artifact_role(role)
        record = self.artifact_set(artifact_set_id)
        entry = next((f for f in record.get("files", ()) if isinstance(f, dict) and f.get("role") == role), None)
        if entry is None:
            raise LocalInputError("ARTIFACT_ROLE_ABSENT", f"the artifact set has no file with role {role!r}",
                                  details={"artifact_set_id": artifact_set_id,
                                           "roles": [f.get("role") for f in record.get("files", ()) if isinstance(f, dict)]},
                                  recovery=Recovery.FIX_REQUEST)
        path = f"/artifact-sets/{_segment(artifact_set_id, 'artifact_set_id')}/files/{_segment(role, 'role')}"
        data = self._http.request("GET", path, accept="*/*").body
        if len(data) != entry.get("size_bytes") or hashlib.sha256(data).hexdigest() != entry.get("sha256"):
            raise ArtifactIntegrityError(ARTIFACT_DOWNLOAD_MISMATCH, "the downloaded file does not match its record",
                                         details={"artifact_set_id": artifact_set_id, "role": role},
                                         recovery=Recovery.RETRY)
        return data

    # ------------------------------------------------------------------------------------------ Order and payment

    def _orders_before(self, resolution_id: str) -> list[str]:
        """The ids of the design's Orders an additional Order of ``resolution_id`` is added to (``create_order``)."""

        orders = self.resolution(resolution_id).get("design_orders", ())
        return sorted({str(o["order_id"]) for o in orders if isinstance(o, dict) and o.get("order_id")
                       and (not o.get("same_resolution") or o.get("payment_state") == "CONFIRMED")})

    def create_order(self, resolution_id: str, *, shipping_address_id: str | None = None,
                     additional_order: bool = False, idempotency_key: str | None = None) -> dict[str, Any]:
        """Create the Order of a resolution (scope ``commercial:order:place``).

        The token is the customer's authorization for this call. The Order fixes the Customer Purchase BOM lines,
        prices and total of the resolution and returns its Order Number, quotation, agreement and corporate
        bank-transfer instructions (``order.payment``); present those to the customer. It ships to
        ``shipping_address_id`` (an ``address_id`` of ``addresses()``) or, without it, to the default address; with
        no address at all the service refuses with ``SHIPPING_ADDRESS_REQUIRED`` (recovery ``ADD_SHIPPING_ADDRESS``).
        The idempotency key is derived from the resolution and the chosen ``shipping_address_id`` (none: the default
        address), so repeating the same call returns the same Order (``replayed: true``), while a call with another
        address -- or with / without one -- is a different request (the service binds the key to both; reusing a key
        with another address is refused with ``IDEMPOTENCY_KEY_CONFLICT``). When the Order that key returns was
        cancelled by OpenSci, the call orders the resolution again as a new Order only with an unspent placement
        credential; a consumed single-use token must be replaced through Account before placing another Order.

        ``additional_order``: the organization already has an Order of this design (the resolution's
        ``design_orders``) and the person wants to buy it again; without it the service refuses with
        ``DESIGN_ALREADY_ORDERED``. The key then also binds the design's Orders this one is added to (``_orders_before``):
        the Orders of other resolutions and the paid ones of this resolution -- never an unpaid Order of this resolution,
        which is the one a lost answer created (the resolution has at most one unpaid Order) -- so a retry replays the
        Order it created, while a further deliberate Order once that one is paid gets a new key."""

        body: dict[str, Any] = {"resolution_id": resolution_id}
        if shipping_address_id is not None:
            body["shipping_address_id"] = shipping_address_id
        if additional_order:
            body["additional_order"] = True
        if idempotency_key is not None:
            return self._post("/orders", body, key=idempotency_key).json()
        parts: tuple[str, ...] = (
            (resolution_id,) if shipping_address_id is None else (resolution_id, "shipping_address_id", shipping_address_id))
        if additional_order:
            parts = (*parts, "additional_order", ",".join(self._orders_before(resolution_id)))
        key = idempotency_key_for("order", *parts)
        for _ in range(MAX_REORDERS_AFTER_CANCELLATION + 1):
            result = self._post("/orders", body, key=key).json()
            order = result.get("order") if isinstance(result, dict) else None
            payment = order.get("payment") if isinstance(order, dict) else None
            if not (result.get("replayed") and isinstance(payment, dict) and payment.get("state") == "CANCELLED"):
                return result
            # The key replays a cancelled Order: the next key is derived from that Order, so a repeated call after
            # a new Order again replays the new one.
            key = idempotency_key_for("order", *parts, str(order.get("order_id")))
        return result

    def order(self, order_id: str) -> dict[str, Any]:
        """One Order: lines, total, quotation, agreement, payment state and next steps (scope ``commercial:order:read``)."""

        return self._get(f"/orders/{_segment(order_id, 'order_id')}")

    def orders(self, *, locale: str | None = None) -> dict[str, Any]:
        """The organization's Orders, newest first; with ``locale`` the list presented for people."""

        if locale is None:
            return self._get("/orders")
        return self._get("/orders/presentation", self._locale(locale))

    def order_presentation(self, order_id: str, *, locale: str = "zh-CN", document: str = "order") -> dict[str, Any]:
        """An Order (``document="order"``), its quotation (``"quotation"``) or its agreement (``"contract"``) presented
        for people in ``zh-CN`` or ``en``."""

        if document not in ORDER_DOCUMENTS:
            raise ValueError(f"document must be one of {', '.join(ORDER_DOCUMENTS)}")
        return self._get(f"/orders/{_segment(order_id, 'order_id')}/{ORDER_DOCUMENTS[document]}", self._locale(locale))

    def order_optical_layout(self, order_id: str) -> dict[str, Any]:
        """The Optical Layout Package of an Order of a design bought without OpenSci mechanics (mechanical supply
        ``NONE``; ``OpticalLayoutPackageV1`` JSON: the relative optical layout, bound to the Order Number and the
        customer; scope ``commercial:order:read``). Render it on this device
        with ``os_optics.optical_layout.generate_optical_layout``."""

        return self._get(f"/orders/{_segment(order_id, 'order_id')}/optical-layout")

    def resolution_optical_layout(self, resolution_id: str) -> dict[str, Any]:
        """The Optical Layout Package of a resolution (``OpticalLayoutPackageV1`` JSON; scope ``commercial:resolve``)."""

        return self._get(f"/resolutions/{_segment(resolution_id, 'resolution_id')}/optical-layout")

    def report_bank_transfer(self, order_id: str, *, idempotency_key: str | None = None) -> dict[str, Any]:
        """Report that the corporate bank transfer for this Order was made (scope ``commercial:payment:report``).

        Call it only after the person who paid says the transfer was made. The payment state becomes
        ``PENDING_CONFIRMATION``; OpenSci then confirms receipt, and only that confirmation makes the Assembly Detail
        available. Repeating the call returns the first report (``replayed: true``); after OpenSci could not match the
        transfer and reopened the payment (``AWAITING_TRANSFER`` again), the same call records a new report."""

        key = idempotency_key or idempotency_key_for("bank-transfer-report", order_id)
        return self._post(f"/orders/{_segment(order_id, 'order_id')}/bank-transfer-reports", key=key).json()

    def withdraw_order(self, order_id: str) -> dict[str, Any]:
        """Withdraw an Order (scope ``commercial:order:place``): only while it is unpaid and no transfer was reported
        (its ``available_actions`` lists ``WITHDRAW_ORDER``). Call it only when the person asks to withdraw the Order,
        or to change the design after ordering (withdraw, run the design again, order again).

        The service deletes the Order with its quotation, agreement, payment and shipping-address copy, and -- unless
        another Order names it -- its resolution and design data; its Order Number is never reused. Returns the
        withdrawal (``order_status: WITHDRAWN``, ``resolution_deleted``, ``next_steps``); tell the person the Order was
        withdrawn. After a reported transfer or a payment the service refuses with ``ORDER_NOT_WITHDRAWABLE`` (recovery
        ``CONTACT_OPENSCI``). The Order no longer exists afterwards, so repeating the call raises ``ORDER_NOT_FOUND``
        (HTTP 404)."""

        return self._post(f"/orders/{_segment(order_id, 'order_id')}/withdrawal").json()


__all__ = [
    "ASSEMBLY_OPTIONS",
    "ENV_AGENT_NAME",
    "FEEDBACK_CATEGORIES",
    "MECHANICAL_KINDS",
    "MECHANICAL_SUPPLY_OPTIONS",
    "PART_LIST_SCHEMA",
    "PART_LIST_SCHEMA_VERSION",
    "PLATFORM_OPTIONS",
    "SUBMISSION_SCHEMA",
    "SUBMISSION_SCHEMA_VERSION",
    "IssuedToken",
    "OpenSciClient",
    "canonical_json_bytes",
    "design_order_constraints",
    "feedback_document",
    "idempotency_key_for",
    "part_list_document",
    "runtime_label",
    "submission_document",
]
