"""Errors of the OpenSci commercial client.

Every failure of an ``OpenSciClient`` call is an ``OpenSciApiError`` (or a subclass) with:

* ``code``: a stable machine code (the service's error code, or one of the client codes below);
* ``family``: the service's error family (``UNAUTHENTICATED``, ``FORBIDDEN``, ``NOT_FOUND``, ``CONFLICT``,
  ``TOO_LARGE``, ``REJECTED``, ``RATE_LIMITED``, ``SERVER_ERROR``, ``GONE``) or a client family (``CONFIGURATION``,
  ``CONNECTION``, ``PROTECTED_CONTENT``, ``LOCAL_INPUT``, ``INTEGRITY``);
* ``message`` and ``details``: what went wrong, as the service stated it;
* ``recovery``: what to do next (a ``Recovery`` value; ``hint`` is one sentence saying the same);
* ``http_status`` and ``request_id``: of the HTTP response, when there was one (quote ``request_id`` to OpenSci);
* ``retry_after_seconds``: the wait the service advised (``Retry-After``), when it sent one (also in ``as_dict()``).

Retired routes and options: the service answers ``ORDERING_BASIS_RETIRED`` and ``ROUTE_RETIRED`` with ``410`` (family
``GONE``) and ``ORDER_OPTION_RETIRED`` with ``422``. The client maps them by code, whatever the status, to the recovery
``USE_LOCAL_PLAN`` (the service's own recovery stays in ``details.service_recovery``); a command sets ``local_hint`` to
the local command that replaces the retired one. ``BOM_CHANGES_UNCONFIRMED`` (409) is ``CONFIRM_BOM_CHANGES``.

No error ever carries the Account token or a decryption grant.
"""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Any


class Recovery(StrEnum):
    """What a client does next after a refusal."""

    NONE = "NONE"
    FIX_REQUEST = "FIX_REQUEST"
    RETRY = "RETRY"
    RETRY_LATER = "RETRY_LATER"
    RETRY_WITH_SAME_KEY = "RETRY_WITH_SAME_KEY"
    USE_NEW_IDEMPOTENCY_KEY = "USE_NEW_IDEMPOTENCY_KEY"
    PROVIDE_TOKEN = "PROVIDE_TOKEN"
    REQUEST_NEW_TOKEN = "REQUEST_NEW_TOKEN"
    REQUEST_SCOPE = "REQUEST_SCOPE"
    WAIT_FOR_PAYMENT_CONFIRMATION = "WAIT_FOR_PAYMENT_CONFIRMATION"
    CREATE_ORDER_FIRST = "CREATE_ORDER_FIRST"
    REQUEST_NEW_DELIVERY = "REQUEST_NEW_DELIVERY"
    UPLOAD_DEPENDENCY_FIRST = "UPLOAD_DEPENDENCY_FIRST"
    CONTACT_OPENSCI = "CONTACT_OPENSCI"
    ADD_SHIPPING_ADDRESS = "ADD_SHIPPING_ADDRESS"
    # design changes after a mechanical Assembly Resolution refusal (details: instance_id, reason, with, suggestion)
    ADJUST_COMPONENT_SPACING = "ADJUST_COMPONENT_SPACING"
    MOVE_COMPONENT_OFF_BEAM = "MOVE_COMPONENT_OFF_BEAM"
    ADJUST_COMPONENT_HEIGHT = "ADJUST_COMPONENT_HEIGHT"
    RELAX_RETAINED_DOF = "RELAX_RETAINED_DOF"
    REDUCE_TILT = "REDUCE_TILT"
    CHANGE_COMPONENT = "CHANGE_COMPONENT"
    ALLOW_LARGER_BREADBOARD = "ALLOW_LARGER_BREADBOARD"
    SIMPLIFY_DESIGN = "SIMPLIFY_DESIGN"
    # catalog and supply changes between the local design and the server's current state
    UPDATE_CATALOG_AND_RERUN = "UPDATE_CATALOG_AND_RERUN"
    CHOOSE_ANOTHER_PRODUCT = "CHOOSE_ANOTHER_PRODUCT"
    RESUBMIT_DESIGN = "RESUBMIT_DESIGN"
    # this engine version is not accepted by the service: upgrade, update the Project's catalog, run it again
    UPGRADE_ENGINE_AND_RERUN = "UPGRADE_ENGINE_AND_RERUN"
    # the organization already has an Order of this design (details.orders): order again only on purpose
    CONFIRM_ADDITIONAL_ORDER = "CONFIRM_ADDITIONAL_ORDER"
    # an optics-only resolution has no assembly preview or detail: use its optical layout
    USE_OPTICAL_LAYOUT = "USE_OPTICAL_LAYOUT"
    # the design is an optical system (design_intent OPTICAL_SYSTEM), with no laboratory layout: buy it without a
    # platform or assembly service (the optics only, or OpenSci mounts per optic)
    CHOOSE_OPTICS_ONLY = "CHOOSE_OPTICS_ONLY"
    # a part number of a part list is not one OpenSci supplies (details.line_ids): look the part numbers up again
    LIST_MECHANICAL_PRODUCTS = "LIST_MECHANICAL_PRODUCTS"
    # BOM-only ordering (the service's values): buy the parts as a BOM; plan, preview and draw on this device; confirm
    # the changed lines of a quote before ordering it
    PURCHASE_BOM = "PURCHASE_BOM"
    USE_LOCAL_PLAN = "USE_LOCAL_PLAN"
    CONFIRM_BOM_CHANGES = "CONFIRM_BOM_CHANGES"


#: The upgrade path after ``ENGINE_VERSION_NOT_ACCEPTED``; the service's message and details say the same.
ENGINE_UPGRADE_HINT = ("This engine version is not accepted by the service (details.accepted_engine_versions): upgrade "
                       "with python -m pip install --upgrade \"os-optics[protected]\" "
                       "(the same command applies to an installation under the earlier name opensci-engine; use the "
                       "os-optics command afterwards), run 'os-optics catalog sync', move the Project to the current "
                       "catalog with "
                       "'os-optics project update-catalog PROJECT --out PROJECT' (a component whose earlier release can "
                       "no longer be obtained or read is moved and listed as unverified with the reason; if the status is "
                       "NEEDS_ATTENTION, add --include-changed to move the changed components and those whose earlier "
                       "release may still be obtained), run the Project again and submit the new run (details.next_steps).")

RECOVERY_HINTS: dict[Recovery, str] = {
    Recovery.NONE: "No further action is needed for this request.",
    Recovery.FIX_REQUEST: "Correct the request as described in the error details, then send it again.",
    Recovery.RETRY: "Send the same request again after a short wait.",
    Recovery.RETRY_LATER: "The service cannot take this request right now (it is busy, or a rate limit was reached) and "
                          "did nothing; send the same request again after the advised wait (retry_after_seconds, from "
                          "Retry-After).",
    Recovery.RETRY_WITH_SAME_KEY: "Send the same request again with the same idempotency key; it returns the original result.",
    Recovery.USE_NEW_IDEMPOTENCY_KEY: "This idempotency key was used for a different request; send this request with a new key.",
    Recovery.PROVIDE_TOKEN: "Provide an OpenSci Account token: OPENSCI_TOKEN, OPENSCI_TOKEN_FILE or the token file "
                            "~/.opensci/token.",
    Recovery.REQUEST_NEW_TOKEN: "Use another valid token from your OpenSci Account for this action. An expired, revoked "
                                "or consumed single-use ordering token cannot place another Order.",
    Recovery.REQUEST_SCOPE: "This token does not carry the scope this operation needs; use a token that carries it.",
    Recovery.WAIT_FOR_PAYMENT_CONFIRMATION: "The Assembly Detail becomes available once OpenSci has confirmed receipt of "
                                            "the Order's payment.",
    Recovery.CREATE_ORDER_FIRST: "Create the Order of this resolution first.",
    Recovery.REQUEST_NEW_DELIVERY: "Request a new delivery and open it right away.",
    Recovery.UPLOAD_DEPENDENCY_FIRST: "Upload the local assembly validation of the same package first, then this artifact.",
    Recovery.CONTACT_OPENSCI: "Contact OpenSci and quote the request_id.",
    Recovery.ADD_SHIPPING_ADDRESS: "The Order needs a shipping address in mainland China: ask the person for one, add it "
                                   "(OpenSciClient.add_address or 'os-optics account address add'), then create "
                                   "the Order again.",
    Recovery.ADJUST_COMPONENT_SPACING: "Apply the first of these that details.suggestion gives: move "
                                       "details.suggestion.instance_id (else details.instance_id) to "
                                       "suggested_position_design_mm, with the components in moved_with moved by the "
                                       "same offset; or set each component and observation plane in "
                                       "suggested_positions_design_mm to its position; or widen every pair in "
                                       "conflicting_pairs: move its instance_id with its moved_with along its "
                                       "beam_direction_design by min_support_pitch_mm minus spacing_mm (the moves add up; "
                                       "with pairs_confirmed true the design is laid out with all of them; a pair marked "
                                       "linked changes the spacing of a lens and the next lens or observation plane, so the "
                                       "optics change); or move the named component along beam_direction_design, away from "
                                       "details.with, by min_extra_spacing_mm (with moved_with). For "
                                       "RETAINED_DOF_UNSATISFIED, details.suggestion is the move of the "
                                       "retained_dofs[].fallback_cause that gives the most to apply. Re-run the Local "
                                       "Validation and submit again.",
    Recovery.MOVE_COMPONENT_OFF_BEAM: "Move the named component's hardware out of the other beam: to the positions in "
                                      "details.suggestion as for ADJUST_COMPONENT_SPACING, or by "
                                      "details.suggestion.min_lateral_clearance_mm along free_direction_design with the "
                                      "components in moved_with. Re-run and submit again.",
    Recovery.ADJUST_COMPONENT_HEIGHT: "Change the named component's height within the reachable range in "
                                      "details.suggestion, re-run and submit again.",
    Recovery.RELAX_RETAINED_DOF: "Drop or relax the retained adjustments listed in details, then submit again.",
    Recovery.REDUCE_TILT: "Reduce the named optic's tilt to its beam to at most details.suggestion.max_incidence_deg, "
                          "re-run and submit again.",
    Recovery.CHANGE_COMPONENT: "Choose another optical product for the named component, re-run and submit again.",
    Recovery.ALLOW_LARGER_BREADBOARD: "Allow a larger breadboard (details.suggestion.smallest_fitting_board_mm, the "
                                      "smallest board the layout was found on; else smallest_board_for_footprint_mm, the "
                                      "smallest board that takes its footprint with any of its compatible mounts) or make the layout smaller, then submit again.",
    Recovery.SIMPLIFY_DESIGN: "Spread out or simplify the design; details name the most informative failure found.",
    Recovery.UPDATE_CATALOG_AND_RERUN: "The design was made against an older or unknown catalog release: run "
                                       "'os-optics catalog sync' and 'os-optics project update-catalog', re-run "
                                       "the design, then submit again.",
    Recovery.CHOOSE_ANOTHER_PRODUCT: "A product named in details is not available right now: choose another product for "
                                     "that component (or supply it yourself), re-run and submit again.",
    Recovery.RESUBMIT_DESIGN: "This resolution can no longer be ordered as it stands (its supply or, with "
                              "details.reason AGREEMENT_CHANGED, the agreement terms changed): submit the design again "
                              "for a new price, then create the Order from the new resolution.",
    Recovery.UPGRADE_ENGINE_AND_RERUN: ENGINE_UPGRADE_HINT,
    Recovery.CONFIRM_ADDITIONAL_ORDER: "Your organization already has an Order of this design (details.orders). Show it to "
                                       "the person; only if they want to buy the design again, create the Order with "
                                       "'os-optics order create RESOLUTION_ID --additional-order'.",
    Recovery.USE_OPTICAL_LAYOUT: "This resolution buys the optical elements only and has no assembly: use its optical "
                                 "layout ('os-optics resolution layout RESOLUTION_ID --out DIR', or 'order layout' "
                                 "for an Order).",
    Recovery.CHOOSE_OPTICS_ONLY: "The design has no laboratory layout (an optical system, design_intent OPTICAL_SYSTEM, "
                                 "or a source whose light is not one beam, details.source_instance_ids), so it takes no "
                                 "platform and no assembly service: submit the same run again as an optics-only "
                                 "purchase ('os-optics resolve RUN_DIR --optics-only'; its deliverable is the relative "
                                 "Optical Layout Drawing). An optical system can also take OpenSci mounts for each "
                                 "optic ('os-optics resolve RUN_DIR --mechanics derived --platform none --mounting "
                                 "FILE'; its deliverable is the mounting sheet).",
    Recovery.LIST_MECHANICAL_PRODUCTS: "A part number of the part list is not one OpenSci supplies (details.line_ids): "
                                       "take mechanical part numbers from 'os-optics products mechanical' and "
                                       "optical ones from 'os-optics catalog list', correct those lines and submit "
                                       "the part list again.",
}

#: What replaces the retired design-ordering path (the hint of ``PURCHASE_BOM`` / ``USE_LOCAL_PLAN``).
LOCAL_PLAN_HINT = ("Ordering from a design, hosted assembly previews and drawings, optical layouts, mounting sheets and "
                   "artifact uploads are retired: plan the assembly on this device and buy the parts as a BOM -- os-optics "
                   "assembly plan derive PROJECT.json --out PLAN; os-optics assembly plan bom PLAN --purchase --out "
                   "BOM.json; os-optics order quote BOM.json (previews and drawings: os-optics assembly plan preview / "
                   "draw PLAN).")
CONFIRM_BOM_HINT = ("This quote has changed lines that are not confirmed yet: review them and run 'os-optics order quote "
                    "BOM.json --confirm REVIEW.json', then order the new resolution.")
RECOVERY_HINTS[Recovery.PURCHASE_BOM] = LOCAL_PLAN_HINT
RECOVERY_HINTS[Recovery.USE_LOCAL_PLAN] = LOCAL_PLAN_HINT
RECOVERY_HINTS[Recovery.CONFIRM_BOM_CHANGES] = CONFIRM_BOM_HINT

#: The retirement codes of the BOM-only service (mapped by code, not by status: see the module docstring).
ORDERING_BASIS_RETIRED = "ORDERING_BASIS_RETIRED"
ROUTE_RETIRED = "ROUTE_RETIRED"
ORDER_OPTION_RETIRED = "ORDER_OPTION_RETIRED"
RETIREMENT_CODES = frozenset({ORDERING_BASIS_RETIRED, ROUTE_RETIRED, ORDER_OPTION_RETIRED})
#: The BOM review codes the commerce client acts on.
BOM_CHANGES_UNCONFIRMED = "BOM_CHANGES_UNCONFIRMED"
BOM_NOT_ORDERABLE = "BOM_NOT_ORDERABLE"
BOM_CONFIRMATION_INVALID = "BOM_CONFIRMATION_INVALID"
RESOLUTION_NOT_FOUND = "RESOLUTION_NOT_FOUND"

#: Hints of codes whose recovery alone would mislead.
CODE_HINTS: dict[str, str] = {
    "API_URL_MISSING": "Set OPENSCI_API_URL to the OpenSci service URL your OpenSci Account shows with your Agent token, "
                       "then run the command again.",
    "API_URL_INVALID": "Set OPENSCI_API_URL to the OpenSci service URL exactly as your OpenSci Account shows it with your "
                       "Agent token (https://host, nothing after it), then run the command again.",
    "MOUNTING_INPUT_REQUIRED": "OpenSci mounts per optic need the beam height of each optic OpenSci supplies (details "
                               "name them): in a part list, a 'mounting' entry per optic line (beam_height_mm, "
                               "optionally retained_dofs and incidence_deg); for an optical-system design, 'resolve "
                               "RUN_DIR --mounting FILE' with an entry per instance id. Add them and submit again.",
    "MOUNTING_NOT_AVAILABLE": "No OpenSci mount, post and post holder fit the named optic at the stated beam height "
                              "(details: line_id, reason, the reachable range when known): choose a beam height in that "
                              "range, keep fewer adjustment axes, or order that line without mechanics, then submit "
                              "again.",
    "ORDER_OPTION_ON_HOLD": "This order option is not offered yet (custom optics, an integrated base): list OpenSci "
                            "catalog optics and leave the platform out (or NONE; a breadboard is ordered as a line of "
                            "the part list), then submit the part list again.",
    "RESOLUTION_HAS_OPEN_ORDER": "This resolution already has an unpaid Order (details.order_number): read it with "
                                 "'os-optics order show ORDER_ID'; it can be ordered again only after that Order is "
                                 "paid or withdrawn.",
}

#: The refusals of a deliverable the purchase does not have; ``details.delivery_mode`` / ``details.deliverables`` say
#: what kind of purchase it is and what it delivers.
DELIVERABLE_REFUSALS = ("ASSEMBLY_NOT_PART_OF_PURCHASE", "OPTICAL_LAYOUT_NOT_PART_OF_PURCHASE",
                        "MOUNTING_SHEET_NOT_PART_OF_PURCHASE")
COMPONENTS = "COMPONENTS"
#: How each deliverable is obtained (``details.deliverables`` kinds).
DELIVERABLE_COMMANDS = {
    "ASSEMBLY_PREVIEW": "the assembly preview ('os-optics assembly preview RESOLUTION_ID')",
    "DETAILED_ASSEMBLY_DRAWING": "the Detailed Assembly Drawing ('os-optics assembly drawing RESOLUTION_ID --out "
                                 "DIR', once the payment is confirmed)",
    "OPTICAL_LAYOUT_DRAWING": "the Optical Layout Drawing ('os-optics resolution layout RESOLUTION_ID --out DIR', or "
                              "'order layout ORDER_ID --out DIR')",
    "MOUNTING_SHEET": "the mounting sheet ('os-optics resolution mounting-sheet RESOLUTION_ID', or 'order "
                      "mounting-sheet ORDER_ID')",
}
OPTICS_ONLY_HINT = ("This purchase buys the optical elements of a design without mechanics: its deliverable is the "
                    "Optical Layout Drawing ('os-optics resolution layout RESOLUTION_ID --out DIR', or 'order layout "
                    "ORDER_ID --out DIR'); it has no assembly preview, assembly drawing or mounting sheet.")
LAB_ASSEMBLY_HINT = ("This purchase includes OpenSci mechanics on a laboratory layout: its deliverables are the assembly "
                     "preview ('os-optics assembly preview RESOLUTION_ID') and the Detailed Assembly Drawing "
                     "('os-optics assembly drawing RESOLUTION_ID --out DIR'); it has no optical layout or mounting "
                     "sheet.")


def purchase_kind_hint(code: str, details: dict[str, Any]) -> str | None:
    """The hint of a refusal of a deliverable the purchase does not have, by the kind of purchase: products delivered
    as separate components (a part list, or an optical-system design with mounts per optic) have the mounting sheet
    when OpenSci chose their mounts and otherwise no deliverable besides their lines; a design bought without mechanics
    has its optical layout; a design with mechanics on a laboratory layout has its assembly preview and drawing.
    ``details.deliverables`` (when the service names them) is the list. None for any other code."""

    if code not in DELIVERABLE_REFUSALS:
        return None
    details = details if isinstance(details, dict) else {}
    kinds = details.get("deliverables")
    if isinstance(kinds, list):
        known = [DELIVERABLE_COMMANDS[k] for k in kinds if k in DELIVERABLE_COMMANDS]
        if not known:
            return "This purchase has no deliverable besides its lines: the products ship as listed."
        return "This purchase delivers " + "; ".join(known) + ". Use that instead of the refused request."
    if details.get("delivery_mode") == COMPONENTS:
        return ("This purchase ships its products as separate components, without a laboratory layout: it has no "
                "assembly preview, assembly drawing or optical layout. When OpenSci chose the mounts per optic, its "
                "deliverable is " + DELIVERABLE_COMMANDS["MOUNTING_SHEET"] + "; otherwise the products ship as listed.")
    if code == "ASSEMBLY_NOT_PART_OF_PURCHASE":
        return OPTICS_ONLY_HINT
    if code == "OPTICAL_LAYOUT_NOT_PART_OF_PURCHASE":
        return LAB_ASSEMBLY_HINT
    return ("The mounting sheet comes with OpenSci mounts chosen per optic without a layout; this purchase does not "
            "have one: read its deliverables with 'os-optics resolution show RESOLUTION_ID'.")


#: Artifact uploads paused for an Account member or its organization (429, recovery ``RETRY_LATER``): ``details.scope``
#: (``MEMBER`` / ``ORGANIZATION``) and ``details.retry_after_seconds`` (also ``Retry-After``).
ARTIFACT_UPLOAD_SUSPENDED = "ARTIFACT_UPLOAD_SUSPENDED"
_PAUSED_UPLOADS = {"MEMBER": "this Account member", "ORGANIZATION": "this Account member's organization"}
SECONDS_PER_HOUR = 3600.0
SECONDS_PER_MINUTE = 60.0


def _wait_text(seconds: float) -> str:
    """A wait in the unit a reader plans with: hours, minutes or seconds."""
    if seconds >= SECONDS_PER_HOUR:
        return f"{seconds / SECONDS_PER_HOUR:.1f} h"
    if seconds >= SECONDS_PER_MINUTE:
        return f"{seconds / SECONDS_PER_MINUTE:.0f} min"
    return f"{seconds:.0f} s"


def upload_pause_hint(code: str, details: dict[str, Any], retry_after_seconds: float | None) -> str | None:
    """The hint of ``ARTIFACT_UPLOAD_SUSPENDED``: whose artifact uploads are paused (``details.scope``) and for how long
    (``details.retry_after_seconds``, else the ``Retry-After`` wait). None for any other code."""

    if code != ARTIFACT_UPLOAD_SUSPENDED:
        return None
    details = details if isinstance(details, dict) else {}
    who = _PAUSED_UPLOADS.get(details.get("scope"), "this Account member or its organization")
    seconds = details.get("retry_after_seconds")
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool):
        seconds = retry_after_seconds
    until = (f"for {_wait_text(float(seconds))} (retry_after_seconds {float(seconds):g})" if seconds is not None
             else "until the time the service gives (Retry-After)")
    return (f"Artifact uploads of {who} are paused {until}; upload again after that. Requests that upload no artifact "
            "are not affected.")


#: Client-side error codes (the service's codes are passed through unchanged).
API_URL_MISSING = "API_URL_MISSING"
API_URL_INVALID = "API_URL_INVALID"
TOKEN_MISSING = "TOKEN_MISSING"
TOKEN_MALFORMED = "TOKEN_MALFORMED"
TOKEN_FILE_UNREADABLE = "TOKEN_FILE_UNREADABLE"
SERVICE_UNREACHABLE = "SERVICE_UNREACHABLE"
UNEXPECTED_RESPONSE = "UNEXPECTED_RESPONSE"
RUN_DIRECTORY_INCONSISTENT = "RUN_DIRECTORY_INCONSISTENT"
ARTIFACT_DOWNLOAD_MISMATCH = "ARTIFACT_DOWNLOAD_MISMATCH"
ARTIFACT_BINDING_OTHER_RESOLUTION = "ARTIFACT_BINDING_OTHER_RESOLUTION"
ASSEMBLY_DETAIL_NOT_RELEASED = "ASSEMBLY_DETAIL_NOT_RELEASED"

#: Service codes the client acts on. ``IDEMPOTENCY_IN_PROGRESS`` (409): the request with this idempotency key is still
#: being processed; ``RESOLUTION_CAPACITY_BUSY`` (503): the service is at capacity for resolutions right now; both are
#: repeated by the client after the advised wait. ``IDEMPOTENCY_KEY_CONFLICT`` (409) is never repeated.
IDEMPOTENCY_IN_PROGRESS = "IDEMPOTENCY_IN_PROGRESS"
IDEMPOTENCY_KEY_CONFLICT = "IDEMPOTENCY_KEY_CONFLICT"
RESOLUTION_CAPACITY_BUSY = "RESOLUTION_CAPACITY_BUSY"
#: Feedback refusals: ``FEEDBACK_INVALID`` (422, recovery ``FIX_REQUEST``) and ``FEEDBACK_RATE_LIMITED`` (429 with
#: ``Retry-After``, recovery ``RETRY_LATER``; never repeated by the client: the advised wait can be long).
FEEDBACK_INVALID = "FEEDBACK_INVALID"
FEEDBACK_RATE_LIMITED = "FEEDBACK_RATE_LIMITED"

#: HTTP statuses of a transient service condition.
RETRYABLE_STATUSES = frozenset({502, 503, 504})
#: Service error codes of a transient condition (repeated whatever their status; the key never changes between attempts).
RETRYABLE_CODES = frozenset({IDEMPOTENCY_IN_PROGRESS, RESOLUTION_CAPACITY_BUSY})
#: ``ARTIFACT_STORAGE_NOT_CONFIGURED`` (503, recovery ``CONTACT_OPENSCI``): the service has no artifact storage configured,
#: and sending the request again does not help.
ARTIFACT_STORAGE_NOT_CONFIGURED = "ARTIFACT_STORAGE_NOT_CONFIGURED"
#: Service error codes never repeated, whatever their status (a 503 the service declares not transient).
NON_TRANSIENT_CODES = frozenset({ARTIFACT_STORAGE_NOT_CONFIGURED})
HTTP_UNAUTHORIZED = 401
HTTP_FORBIDDEN = 403
HTTP_CONFLICT = 409
HTTP_TOO_LARGE = 413
HTTP_TOO_MANY_REQUESTS = 429
HTTP_SERVER_ERROR = 500
#: The longest service message / details document an error keeps (a misbehaving endpoint cannot flood logs).
MAX_MESSAGE_CHARS = 2000
MAX_DETAILS_CHARS = 16000


class OpenSciApiError(Exception):
    """A refused or failed OpenSci client call (see the module docstring)."""

    default_family = "REJECTED"
    #: The wait (seconds) the service advised in its ``Retry-After`` header, when it sent one.
    retry_after_seconds: float | None = None
    #: What a call of several steps had completed when this error stopped it; ``None`` for an error of a call that has
    #: no such steps.
    partial_results: dict[str, Any] | None = None
    #: The hint of the command that met this error, when it says more than the code's hint (e.g. the local command that
    #: replaces a retired one); ``hint`` returns it first.
    local_hint: str | None = None

    def __init__(self, code: str, message: str, *, family: str | None = None, details: dict[str, Any] | None = None,
                 recovery: Recovery | str = Recovery.NONE, http_status: int | None = None,
                 request_id: str | None = None) -> None:
        self.code = str(code)
        self.message = str(message)
        self.family = family or self.default_family
        self.details = dict(details or {})
        try:
            self.recovery = Recovery(recovery)
        except ValueError:
            self.recovery = Recovery.CONTACT_OPENSCI
        self.http_status = http_status
        self.request_id = request_id
        super().__init__(self._text())

    def _text(self) -> str:
        where = f" (HTTP {self.http_status}" + (f", request {self.request_id})" if self.request_id else ")") \
            if self.http_status is not None else ""
        return f"{self.code}: {self.message}{where}; recovery: {self.recovery.value}"

    @property
    def hint(self) -> str:
        """One sentence saying what to do next: the command's own ``local_hint`` when it set one; for a deliverable this
    purchase does not have, what it delivers instead
        (``purchase_kind_hint``); for paused artifact uploads whose uploads and for how long (``upload_pause_hint``);
        else ``CODE_HINTS[code]`` when the code says more than its recovery, else ``RECOVERY_HINTS[recovery]``."""

        return (self.local_hint or purchase_kind_hint(self.code, self.details)
                or upload_pause_hint(self.code, self.details, self.retry_after_seconds)
                or CODE_HINTS.get(self.code, RECOVERY_HINTS[self.recovery]))

    def as_dict(self) -> dict[str, Any]:
        """The error as a JSON object (what the ``os-optics`` commands print); ``retry_after_seconds`` only when
        the service advised a wait."""

        document = {"code": self.code, "family": self.family, "message": self.message, "details": self.details,
                    "recovery": self.recovery.value, "hint": self.hint, "http_status": self.http_status,
                    "request_id": self.request_id}
        if self.retry_after_seconds is not None:
            document["retry_after_seconds"] = self.retry_after_seconds
        return document


class OpenSciConfigurationError(OpenSciApiError):
    """The client is not configured: no service URL, or no usable Account token."""

    default_family = "CONFIGURATION"


class OpenSciConnectionError(OpenSciApiError):
    """The OpenSci service could not be reached (network, TLS or timeout); nothing was received."""

    default_family = "CONNECTION"


class AssemblyDetailNotReleasedError(OpenSciApiError):
    """The Assembly Detail of this resolution is not available yet. ``recovery`` is ``CREATE_ORDER_FIRST`` (no Order of
    this resolution exists) or ``WAIT_FOR_PAYMENT_CONFIRMATION`` (the Order's payment awaits OpenSci's confirmation of
    receipt); ``details["reason"]`` names the condition."""

    default_family = "FORBIDDEN"


class ProtectedContentError(OpenSciApiError):
    """A delivered Assembly Preview / Assembly Detail did not open on this device. ``code`` is the
    ``os_optics.protected_delivery.ProtectedDeliveryErrorCode`` value; ``details["problems"]`` lists verifier
    findings where there are any."""

    default_family = "PROTECTED_CONTENT"


class LocalInputError(OpenSciApiError):
    """A local input of a client call is not usable (e.g. an inconsistent run directory); nothing was sent."""

    default_family = "LOCAL_INPUT"


class ArtifactIntegrityError(OpenSciApiError):
    """Bytes read back from the service do not match what the service's own record says."""

    default_family = "INTEGRITY"


def derive_recovery(status: int, code: str, details: dict[str, Any]) -> Recovery:
    """The recovery of a service refusal that states none (by status and code)."""

    if code == ASSEMBLY_DETAIL_NOT_RELEASED:
        return Recovery.WAIT_FOR_PAYMENT_CONFIRMATION if details.get("reason") == "PAYMENT_NOT_CONFIRMED" \
            else Recovery.CREATE_ORDER_FIRST
    if code == "AUTHORIZATION_MISSING":
        return Recovery.PROVIDE_TOKEN
    if code == "SHIPPING_ADDRESS_REQUIRED":
        return Recovery.ADD_SHIPPING_ADDRESS
    if code == "ENGINE_VERSION_NOT_ACCEPTED":
        return Recovery.UPGRADE_ENGINE_AND_RERUN
    if code == "DESIGN_ALREADY_ORDERED":
        return Recovery.CONFIRM_ADDITIONAL_ORDER
    if code == "ASSEMBLY_NOT_PART_OF_PURCHASE":
        # a purchase delivered as components has no optical layout either: its hint names what it has
        return Recovery.FIX_REQUEST if details.get("delivery_mode") == COMPONENTS else Recovery.USE_OPTICAL_LAYOUT
    if code in ("DESIGN_INTENT_HAS_NO_LAB_ASSEMBLY", "EXTENDED_EMITTER_NOT_RESOLVABLE"):
        return Recovery.CHOOSE_OPTICS_ONLY
    if code == "PART_NOT_ORDERABLE":
        return Recovery.LIST_MECHANICAL_PRODUCTS
    if code in RETIREMENT_CODES:
        return Recovery.USE_LOCAL_PLAN
    if code == BOM_CHANGES_UNCONFIRMED:
        return Recovery.CONFIRM_BOM_CHANGES
    suggestion = details.get("suggestion") if isinstance(details, dict) else None
    action = suggestion.get("action") if isinstance(suggestion, dict) else None
    if isinstance(action, str) and action in Recovery.__members__:
        return Recovery(action)  # a mechanical refusal names its corrective action
    if code in ("AUTHORIZATION_SCOPE_MISSING",):
        return Recovery.REQUEST_SCOPE
    if status == HTTP_UNAUTHORIZED or code in ("AUTHORIZATION_INVALID", "AUTHORIZATION_EXPIRED", "AUTHORIZATION_REVOKED",
                                              "AUTHORIZATION_CONSUMED"):
        return Recovery.REQUEST_NEW_TOKEN
    if code == IDEMPOTENCY_KEY_CONFLICT:
        return Recovery.USE_NEW_IDEMPOTENCY_KEY
    if code == IDEMPOTENCY_IN_PROGRESS:
        return Recovery.RETRY_WITH_SAME_KEY
    if code in (RESOLUTION_CAPACITY_BUSY, FEEDBACK_RATE_LIMITED) or status == HTTP_TOO_MANY_REQUESTS:
        return Recovery.RETRY_LATER
    if status in RETRYABLE_STATUSES:
        return Recovery.RETRY
    if status >= HTTP_SERVER_ERROR or status == HTTP_FORBIDDEN:
        return Recovery.CONTACT_OPENSCI
    if status == HTTP_CONFLICT:
        return Recovery.NONE
    return Recovery.FIX_REQUEST


def _bounded(value: Any, limit: int) -> Any:
    try:
        text = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return {}
    return value if len(text) <= limit else {"truncated": True}


def error_from_response(status: int, body: bytes, request_id: str | None) -> OpenSciApiError:
    """The typed error of a non-2xx service response. The service error body is
    ``{"error": {"code", "family", "message", "details", "recovery"}, "request_id"}``."""

    error: Any = None
    try:
        document = json.loads(body.decode("utf-8")) if body else None
    except (UnicodeDecodeError, ValueError, RecursionError):
        document = None
    if isinstance(document, dict):
        error = document.get("error")
        if isinstance(document.get("request_id"), str):
            request_id = document["request_id"]
    if not isinstance(error, dict) or not isinstance(error.get("code"), str):
        family = "SERVER_ERROR" if status >= HTTP_SERVER_ERROR else "REJECTED"
        return OpenSciApiError(UNEXPECTED_RESPONSE, f"the service answered HTTP {status} without an error document",
                               family=family, recovery=Recovery.RETRY if status in RETRYABLE_STATUSES else Recovery.CONTACT_OPENSCI,
                               http_status=status, request_id=request_id)
    code = error["code"]
    details = error.get("details") if isinstance(error.get("details"), dict) else {}
    details = _bounded(details, MAX_DETAILS_CHARS)
    message = str(error.get("message") or code)[:MAX_MESSAGE_CHARS]
    family = error.get("family") if isinstance(error.get("family"), str) else None
    recovery = error.get("recovery")
    if code in RETIREMENT_CODES:
        # by code, whatever the status (410 GONE or 422): the local plan replaces every retired route and option
        details = {**details, "service_recovery": recovery if isinstance(recovery, str) else None}
        recovery = Recovery.USE_LOCAL_PLAN
    elif not isinstance(recovery, str) or recovery not in Recovery.__members__:
        recovery = derive_recovery(status, code, details)
    elif recovery == Recovery.USE_OPTICAL_LAYOUT and details.get("delivery_mode") == COMPONENTS:
        recovery = Recovery.FIX_REQUEST  # products delivered as components have no optical layout to fall back to
    kind = AssemblyDetailNotReleasedError if code == ASSEMBLY_DETAIL_NOT_RELEASED else OpenSciApiError
    return kind(code, message, family=family or ("SERVER_ERROR" if status >= HTTP_SERVER_ERROR else None), details=details,
                recovery=recovery, http_status=status, request_id=request_id)


__all__ = [
    "RECOVERY_HINTS",
    "RETIREMENT_CODES",
    "ArtifactIntegrityError",
    "AssemblyDetailNotReleasedError",
    "LocalInputError",
    "OpenSciApiError",
    "OpenSciConfigurationError",
    "OpenSciConnectionError",
    "ProtectedContentError",
    "Recovery",
    "derive_recovery",
    "error_from_response",
]
