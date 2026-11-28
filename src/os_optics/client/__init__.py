"""OpenSci commercial client: resolution, Order, payment report, Assembly Preview / Assembly Detail and the Detailed
Assembly Drawing from Python, with the Account token and the protected deliveries handled inside the client.

Configure once (or use the ``os-optics account ...`` commands)::

    OPENSCI_API_URL=<OpenSci service URL>   # shown with your Agent token in your OpenSci Account
    OPENSCI_TOKEN_FILE=/path/to/token             # or OPENSCI_TOKEN=oat_..., or the token file ~/.opensci/token

The whole workflow::

    from os_optics.client import OpenSciClient
    from os_optics.local import example_project, run_optical_project

    client = OpenSciClient.from_environment()
    client.token_status()                                   # organization, scopes, expiry, what the token allows
    run = run_optical_project(example_project(), output_dir="run")   # design, simulate, Local Validation (this device)
    resolution = client.submit(run)                         # or client.submit("run"); the same run -> the same resolution
    rid = resolution["resolution_id"]                       # resolution["resolution"]["customer_bom"]: lines and prices
    preview = client.assembly_preview(rid)                  # the Experimental Lab Assembly, opened in memory
    validation = client.validate_assembly(preview)          # layout / collision check on this device
    placed = client.create_order(rid)                       # the token authorizes the Order
    order = placed["order"]                                 # Order Number, total, quotation, agreement, payment
    client.order_presentation(order["order_id"], locale="zh-CN")   # the Order for people (zh-CN or en)
    # The customer pays by corporate bank transfer with the exact Order Number as the transfer remark.
    client.report_bank_transfer(order["order_id"])          # only once the person who paid says it was made
    # After OpenSci has confirmed receipt of the payment:
    drawing = client.assembly_drawing(rid, "drawing", preview=preview)  # detail in memory -> drawing files in drawing/
    # (drawings are never uploaded: they stay on this device)

The BOM-only OpenSci service retires the design path above (``ORDERING_BASIS_RETIRED`` / ``ROUTE_RETIRED``, recovery
``USE_LOCAL_PLAN``): plan the assembly on this device (``os-optics assembly plan``) and buy its parts as a purchase BOM,
quoted with OpenSci's line review (``os_optics.client.bom_review``)::

    quote = client.quote_bom(json.load(open("BOM.json")))   # 201: resolution_id, resolution.bom_review
    quote["resolution"]["bom_review"]["status"]             # ORDERABLE -> client.create_order(quote["resolution_id"])
    # CHANGES_REQUIRED: bom_review.review_document(...) proposes the confirmation; quote_bom(confirmed) sends it

Order options: a design order chooses mechanical supply (``NONE``: the optics only; ``DERIVED``: OpenSci mounts,
posts and post holders), platform (``NONE`` or the OpenSci ``BREADBOARD``) and assembly (``CUSTOMER`` or ``OPENSCI``),
sent as ``customer_constraints`` (``design_order_constraints``). Without a design, a part list of OpenSci part numbers
is priced and ordered the same way::

    client.mechanical_products(kind="MOUNT")                # OS-MECH-* part numbers with their facts
    resolution = client.submit_part_list({"lines": [{"line_id": "L1", "opensci_sku": "OS-OPT-...", "quantity": 2}],
                                          "mechanical_supply": "DERIVED",
                                          "mounting": {"L1": {"beam_height_mm": 125}}})
    client.resolution_mounting_sheet(resolution["resolution_id"])   # per optic: mount, post, holder, steps, height

Feedback to OpenSci (any Account token, Agent or Review profile): a problem met while doing what the user needed, a
feature request or a documentation gap, one per call::

    client.submit_feedback(category="PROBLEM", summary="order create refused after resolve",
                           details="What the user needed, the commands run, what happened, what was expected.",
                           context={"error_code": "SHIPPING_ADDRESS_REQUIRED", "request_ids": ["req-..."]})
    # -> {"feedback_id", "received_at", "status": "RECEIVED", "replayed"}; the same feedback again replays

Include no tokens, passwords, payment data or personal data beyond what the problem needs, and tell the user what you
sent. ``engine_version`` and ``runtime`` are added to ``context``; ``agent`` comes from ``OPENSCI_AGENT_NAME`` when not
given.

Repeating a call is safe: submissions, Orders, payment reports, feedback and uploads carry an idempotency key derived from
their input, so a repeat returns the original result (``replayed: true``) instead of creating a second one. The client
itself repeats a request after a transient condition (unreachable service, ``502`` / ``503`` / ``504``,
``RESOLUTION_CAPACITY_BUSY``, ``IDEMPOTENCY_IN_PROGRESS``) with bounded exponential back-off, honouring ``Retry-After``
(``retries``, ``max_retry_seconds``; see ``os_optics.client.transport``). A rate limit (HTTP 429, e.g.
``FEEDBACK_RATE_LIMITED``) is not repeated: its recovery is ``RETRY_LATER`` with the advised wait in
``retry_after_seconds``. Nor is ``503 ARTIFACT_STORAGE_NOT_CONFIGURED`` (recovery ``CONTACT_OPENSCI``: the service has no
artifact storage).

Errors are ``OpenSciApiError`` with ``code``, ``family``, ``message``, ``details``, ``recovery`` (what to do next,
e.g. ``WAIT_FOR_PAYMENT_CONFIRMATION`` for the Assembly Detail before the payment is confirmed), ``hint``,
``http_status`` and ``request_id``.

Protected content: the Assembly Preview and Assembly Detail are opened in memory; the client writes no decrypted
package. It writes only what you ask for: drawing files (``out_dir``), and a token file
(``save_token_file``). ``repr`` / ``str`` of the client, the token and every error never show the token.
Opening the deliveries needs ``pip install "os-optics[protected]"``.
"""

from .api import (
    ASSEMBLY_OPTIONS,
    ENV_AGENT_NAME,
    FEEDBACK_CATEGORIES,
    MECHANICAL_KINDS,
    MECHANICAL_SUPPLY_OPTIONS,
    PART_LIST_SCHEMA,
    PART_LIST_SCHEMA_VERSION,
    PLATFORM_OPTIONS,
    SUBMISSION_SCHEMA,
    SUBMISSION_SCHEMA_VERSION,
    IssuedToken,
    OpenSciClient,
    canonical_json_bytes,
    design_order_constraints,
    feedback_document,
    idempotency_key_for,
    part_list_document,
    runtime_label,
    submission_document,
)
from .bom_review import confirmation_body, purchase_bom_body, review_document, review_of
from .credentials import (
    ENV_TOKEN,
    ENV_TOKEN_FILE,
    AccountToken,
    default_token_file,
    read_token_file,
    remove_token_file,
    resolve_token,
    save_token_file,
)
from .errors import (
    RECOVERY_HINTS,
    ArtifactIntegrityError,
    AssemblyDetailNotReleasedError,
    LocalInputError,
    OpenSciApiError,
    OpenSciConfigurationError,
    OpenSciConnectionError,
    ProtectedContentError,
    Recovery,
)
from .transport import ENV_API_URL, normalize_api_url

__all__ = [
    "ASSEMBLY_OPTIONS",
    "ENV_AGENT_NAME",
    "ENV_API_URL",
    "ENV_TOKEN",
    "ENV_TOKEN_FILE",
    "FEEDBACK_CATEGORIES",
    "MECHANICAL_KINDS",
    "MECHANICAL_SUPPLY_OPTIONS",
    "PART_LIST_SCHEMA",
    "PART_LIST_SCHEMA_VERSION",
    "PLATFORM_OPTIONS",
    "RECOVERY_HINTS",
    "SUBMISSION_SCHEMA",
    "SUBMISSION_SCHEMA_VERSION",
    "AccountToken",
    "ArtifactIntegrityError",
    "AssemblyDetailNotReleasedError",
    "IssuedToken",
    "LocalInputError",
    "OpenSciApiError",
    "OpenSciClient",
    "OpenSciConfigurationError",
    "OpenSciConnectionError",
    "ProtectedContentError",
    "Recovery",
    "canonical_json_bytes",
    "confirmation_body",
    "default_token_file",
    "design_order_constraints",
    "feedback_document",
    "idempotency_key_for",
    "normalize_api_url",
    "part_list_document",
    "purchase_bom_body",
    "read_token_file",
    "remove_token_file",
    "resolve_token",
    "review_document",
    "review_of",
    "runtime_label",
    "save_token_file",
    "submission_document",
]
