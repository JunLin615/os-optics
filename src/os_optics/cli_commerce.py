"""Commercial commands of ``os-optics``: the OpenSci service from the command line (``os_optics.client``).

Configuration: ``OPENSCI_API_URL`` (or ``--api-url``) is the OpenSci service; the Account token comes from
``--token-file PATH``, else ``OPENSCI_TOKEN``, else ``OPENSCI_TOKEN_FILE``, else ``~/.opensci/token``. A token is never
a command-line value. Every command prints one JSON document on stdout; a refusal prints
``{"status": "ERROR", "error": {"code", "family", "message", "details", "recovery", "hint", "http_status",
"request_id"}}``.

* ``account status``: the token in use (organization, lifecycle/spend, scopes, optional expiry and permissions);
* ``account token create [--profile agent|review | --scopes S ...] [--lifecycle single-use|time-limited|permanent]
  [--lifetime-hours N] [--label L] [--save [PATH]]``:
  issue a token (needs a token carrying ``account:token:manage``). With ``--save`` the new token is written to PATH
  (default ``~/.opensci/token``) readable only by you and is not printed; without ``--save`` it is printed once, in
  ``credential``;
* ``account token list`` / ``account token revoke TOKEN_ID``;
* ``account token rotate --issuer-token-file PATH``: replace the token in use by a new one with the same scopes (issued
  with the token in PATH), store it where the old one was read from, then revoke the old one;
* ``account logout``: revoke the token in use and delete the token file it was read from;
* ``account address list`` / ``account address add --recipient NAME --phone MOBILE --province P --city C --district D
  --street S [--postal-code CODE] [--label L] [--default]``: the organization's shipping addresses (mainland China);
* ``resolve RUN_DIR [--mechanics none|derived] [--platform none|breadboard] [--assembly customer|opensci] [--mounting
  FILE] [--customer-supplied INSTANCE_ID ...] [--spare SKU=N ...]``: submit a verified run directory (``project run
  --out-dir``) for commercial resolution with the chosen order options (``--optics-only`` / ``--no-breadboard`` /
  ``--assembly-service`` are the same options as presets); components OpenSci does not supply (and the ones marked
  ``--customer-supplied``) are listed apart from the Customer Purchase BOM (``customer_supplied_components``);
* ``order quote BOM.json [--confirm REVIEW.json] [--review-out FILE] [--schema-version 2|1] [--json]``: quote a
  purchase BOM (OpenSci part numbers and quantities, e.g. from ``assembly plan bom PLAN --purchase``) with OpenSci's line
  review; changed lines are confirmed with ``--confirm`` before ordering (``os_optics.client.bom_review``). Exit 0 =
  every line orderable (next: ``order create RESOLUTION_ID``), 1 = changes to confirm (the review file is written), 64 =
  an unreadable input file or a review output that is an input (nothing sent), 65 = a BOM or confirmation refused
  locally, a review of another BOM included (nothing sent), 67 = refused by the service, 77 = no token (the BOM stays in
  its file; the output says how to register or sign in and the exact command to resume with, quoted for this device's
  shell, and as an argument list);
* ``resolve-parts PARTS.json [--mechanics none|derived]``: price a part list (the version-1 body; ``order quote`` is its
  successor, and the BOM-only service refuses derived mechanics with ``ORDER_OPTION_RETIRED``);
* ``products mechanical [--kind MOUNT|POST|POST_HOLDER|BREADBOARD] [--json]``: OpenSci's mechanical part numbers and
  their facts;
* ``resolution show ID [--locale zh-CN|en]``;
* ``resolution mounting-sheet ID`` / ``order mounting-sheet ORDER_ID`` ``[--locale en|zh-CN] [--out FILE]``: the
  mounting sheet of OpenSci mounts chosen per optic without a layout (JSON; with ``--locale`` as text);
* ``assembly preview ID [--out validation.json] [--view] [--snapshot DIR] [--tolerance NAME=VALUE ...]``: the
  Experimental Lab Assembly, opened in memory, checked on this device (thresholds of your own with ``--tolerance``);
* ``order layout ORDER_ID --out DIR [--locale zh-CN|en]`` / ``resolution layout RESOLUTION_ID --out DIR``:
  the Optical Layout Drawing of a design bought without OpenSci mechanics, its package fetched from the service,
  saved as
  ``optical-layout.json`` and rendered here (``os_optics.optical_layout``);
* ``order create ID [--address ADDRESS_ID] [--additional-order]`` / ``order show ORDER_ID [--locale L] [--document order|quotation|contract]``
  / ``order list`` / ``order report-transfer ORDER_ID`` / ``order withdraw ORDER_ID`` (only while unpaid and no transfer
  was reported; the Order and its data are deleted);
* ``assembly drawing ID --out DIR [--locale en|zh-CN]``: the Detailed Assembly Drawing of a resolution on this device
  (in English or Simplified Chinese); nothing is uploaded (``--no-upload`` is accepted and has no effect);
* ``artifacts list ID`` / ``artifacts show SET_ID`` / ``artifacts download SET_ID ROLE --out FILE`` (ROLE as the
  manifest names it: DRAWING_SVG, DRAWING_PDF, DRAWING_RESULT, DRAWING_MANIFEST, VALIDATION_RESULT; the drawing schema
  3.0 names ASSEMBLY_DRAWING_SVG / ASSEMBLY_DRAWING_PDF are accepted too);
* ``feedback submit --category PROBLEM|FEATURE_REQUEST|DOCUMENTATION --summary TEXT (--details TEXT | --details-file
  FILE) [--error-code CODE] [--request-id ID ...] [--resolution-id ID] [--order-id ID] [--command TEXT] [--agent NAME]
  [--locale zh-CN|en]``: send feedback to OpenSci (any Account token); the engine version, the Python runtime and the
  active local catalog release are added. Prints the receipt ``{"feedback_id", "received_at", "status", "replayed"}``.

Retired by the BOM-only OpenSci service (they stay here for an earlier service, which still answers them): ``resolve``,
derived mechanics in ``resolve-parts``, ``assembly preview`` / ``drawing``, ``view --resolution``, ``resolution|order
layout``, ``resolution|order mounting-sheet``, ``artifacts``, and ``order create`` of a design resolution. Their refusal
(``ORDERING_BASIS_RETIRED`` / ``ROUTE_RETIRED`` with 410, ``ORDER_OPTION_RETIRED`` with 422) has the recovery
``USE_LOCAL_PLAN`` and a hint naming the local command that replaces the command (exit 67); without a token these commands
are sent anonymously, as the service answers a retired route before it authenticates (``resolve`` then sends an empty
probe, never the design). Without a token any other command
prints its refusal ``TOKEN_MISSING`` with ``details.handoff`` (the Account registration / sign-in URLs, the token file, the
command to resume with) and the same as text on stderr.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

API_REFUSED = 67
SERVICE_UNAVAILABLE = 69
NOT_YET_AVAILABLE = 75
NOT_AUTHORIZED = 77
NOT_CONFIGURED = 78
USAGE_ERROR = 64
LOCAL_INPUT_ERROR = 65
MANAGE_SCOPE = "account:token:manage"
LOCALE_CHOICES = ("zh-CN", "en")
PROFILE_CHOICES = ("agent", "review")
#: The order options of the command line (lower case; the service names them in upper case).
MECHANICS_CHOICES = ("none", "derived")
PLATFORM_CHOICES = ("none", "breadboard")
ASSEMBLY_CHOICES = ("customer", "opensci")
MECHANICAL_KIND_CHOICES = ("MOUNT", "POST", "POST_HOLDER", "BREADBOARD")
DEFAULT_LIFETIME_HOURS = 168
TOKEN_LIFECYCLES = ("single-use", "time-limited", "permanent")
HTTP_SERVER_ERROR = 500
#: ``order quote``: the review needs changes confirmed before ordering (the review file is written).
QUOTE_CHANGES_REQUIRED = 1
BOM_SCHEMA_VERSION_CHOICES = ("2", "1")
REVIEW_SUFFIX = ".review.json"
#: ``order quote``: an input file that cannot be read (``details.argument`` / ``details.reason``; exit 64).
INPUT_FILE_UNREADABLE = "INPUT_FILE_UNREADABLE"
#: The options of ``order quote`` that the command to resume with after sign-in (and ``next_command``) repeats, in this
#: order after the BOM, and the ones it leaves out (a new option must be added to one of the two: test_token_handoff).
RESUME_PRESERVED = ("--schema-version", "--confirm", "--review-out", "--api-url", "--json")
RESUME_EXCLUDED = ("--help", "--token-file")
#: The local command that replaces each command the BOM-only service retired (the refusal's ``hint``).
RETIRED_HINTS = {
    "resolve": "Ordering from a Project run is retired. Plan locally and buy the parts: os-optics assembly plan derive "
               "PROJECT.json --out PLAN; os-optics assembly plan bom PLAN --purchase --out BOM.json; os-optics order quote "
               "BOM.json.",
    "mechanics": "OpenSci no longer selects mounts for a part list: derive the chains locally (assembly plan derive) and "
                 "list the mounts, posts and holders as lines.",
    "preview": "Hosted assembly previews are retired: os-optics assembly plan preview PLAN --out preview.zip, then "
               "os-optics view PLAN.",
    "drawing": "Hosted assembly details are retired: os-optics assembly plan draw PLAN --out DIR.",
    "layout": "Optical layout drawings are no longer delivered: the local assembly drawing (assembly plan draw) replaces "
              "them.",
    "mounting-sheet": "Mounting sheets are no longer delivered: os-optics assembly plan show PLAN lists every support "
                      "chain.",
    "artifacts": "Uploaded assembly artifacts are retired: drawings stay on your machine.",
    "order-design": "Design resolutions can no longer be ordered. Plan locally and buy the parts: os-optics assembly plan "
                    "bom PLAN --purchase --out BOM.json; os-optics order quote BOM.json.",
}
#: The help sentence of a command the BOM-only service retired.
RETIRED_NOTE = ("Retired by the BOM-only OpenSci service (answered with the hint of the local command that replaces it); "
                "an earlier service still answers it. ")
QUOTE_REPEAT_NOTE = "Sending the same BOM again gives the same request, so a repeated command never creates a second quote."

#: Anything shaped like an Account token (usage errors echo the command line; a token typed there is not repeated).
TOKEN_SHAPED = re.compile(r"oat_[0-9A-Za-z]{8,}(?:_[0-9A-Za-z]*)?")

COMMERCE_EXIT_HELP = (
    "exit codes: 0 = done, 64 = usage error / unwritable file, 65 = a local input or a delivery that is not usable "
    "(JSON error on stdout), 67 = refused by the OpenSci service, 69 = the service is unreachable or unavailable, or "
    "asks you to send the request again later (recovery RETRY_LATER: busy or a rate limit; wait retry_after_seconds), "
    "75 = not available yet (e.g. the Assembly Detail before OpenSci has confirmed the payment), 77 = no token, or a "
    "token that is expired, revoked or lacks the needed scope, 78 = OPENSCI_API_URL is not set or not usable; every "
    "error is printed as JSON with its 'recovery'"
)
#: ``assembly drawing`` has one outcome code more, the rule of ``assembly draw``.
DRAWING_EXIT_HELP = COMMERCE_EXIT_HELP.replace(
    "exit codes: 0 = done, ",
    "exit codes: 0 = Detailed Assembly Drawing written, 2 = diagnostic sheet written (--diagnostic: local assembly check "
    "FAILED or incomplete; status and delivery_ready say which sheet), ", 1).replace(
    "65 = a local input or a delivery that is not usable ",
    "65 = a local input or a delivery that is not usable, or a local assembly check that did not pass without "
    "--diagnostic (nothing written or uploaded) ", 1)
CONNECTION_HELP = (
    "The OpenSci service is OPENSCI_API_URL (or --api-url). The Account token comes from --token-file, else "
    "OPENSCI_TOKEN, else OPENSCI_TOKEN_FILE, else ~/.opensci/token."
)
ACCOUNT_DESCRIPTION = (
    "Your OpenSci Account token on this device. 'status' shows the token in use; 'token create' issues a token for "
    "your Agent with the Account owner credential (a token that carries account:token:manage), for example 'token "
    "create --profile agent --save --token-file OWNER_CREDENTIAL_FILE': '--profile agent' carries every customer "
    "commercial scope, '--profile review' resolves, opens the assembly preview and reads Orders and artifacts. "
    "Choose --lifecycle single-use (one successful new Order; the token can still read and track afterwards), "
    "time-limited (default, up to 30 days) or permanent (no expiry, revocable; needs a permanent issuer). "
    "'--save' writes the new token to ~/.opensci/token (or PATH), readable only by you, and does not print it; without "
    "'--save' it is printed once. 'token rotate --issuer-token-file OWNER_CREDENTIAL_FILE' replaces the token in use "
    "with a new one issued by the owner credential; 'logout' revokes the token in use and deletes its token file. "
    + CONNECTION_HELP
)
RESOLVE_DESCRIPTION = (
    RETIRED_NOTE + "Locally: 'os-optics assembly plan derive PROJECT.json --out PLAN', 'os-optics assembly plan bom PLAN "
    "--purchase --out BOM.json', 'os-optics order quote BOM.json'. "
    "Submit a locally validated run for commercial resolution: RUN_DIR is the directory written by 'os-optics "
    "project run --out-dir' (verified first). Prints the resolution: resolution_id, order_options, the Customer "
    "Purchase BOM with prices (with the Shipping line and, when chosen, the OpenSci assembly and alignment service line) "
    "and the delivery terms. The order options are independent: --mechanics derived (default) has OpenSci select the "
    "mount, post and post holder for each optic it supplies, --mechanics none buys only the optical elements the design "
    "selected; --platform breadboard (default) adds the OpenSci breadboard, --platform none uses your own optical table "
    "(M6 holes on a 25 mm grid); --assembly customer (default) means you assemble, --assembly opensci has OpenSci "
    "assemble and align the system on the OpenSci breadboard (it needs --mechanics derived and --platform breadboard). "
    "The presets are the same options: --optics-only is --mechanics none (no mounts, posts, holders or breadboard, no "
    "assembly service: use it when the person mounts the optics in their own mechanics -- the deliverable is the "
    "relative Optical Layout Drawing, 'resolution layout' / 'order layout'), --no-breadboard is --platform none, "
    "--assembly-service is --assembly opensci; a combination that cannot be supplied is refused before anything is sent "
    "(PURCHASE_OPTIONS_CONFLICT, ASSEMBLY_SERVICE_REQUIRES_BREADBOARD). A Project designed as an optical system "
    "(design_intent OPTICAL_SYSTEM, no laboratory layout) takes no platform and no assembly service (otherwise "
    "DESIGN_INTENT_HAS_NO_LAB_ASSEMBLY): buy it with --optics-only, or with OpenSci mounts per optic: --mechanics derived "
    "--platform none --mounting FILE, where FILE is a JSON object {INSTANCE_ID: {\"beam_height_mm\": H, "
    "\"incidence_deg\": A}} giving the beam height above the surface the holders stand on for every OpenSci optic "
    "(without it: MOUNTING_INPUT_REQUIRED); its deliverable is the mounting sheet ('resolution mounting-sheet'). "
    "--spare SKU=N adds N spare units of an "
    "OpenSci optic of the design (separate lines, not part of the assembly). Before ordering, show the person "
    "design_deviations (retained adjustments the hardware provides otherwise than the design asks, with the reason), "
    "agreement (the clauses the Order will be written under; 'resolution show ID --locale zh-CN|en' renders them), "
    "deliverables and design_orders (Orders of this design the organization already has). With a laboratory layout the "
    "beam height is derived from the design, not chosen; the resolution reports it: frame.optic_axis_heights gives each "
    "OpenSci optic's axis_height_mm above the breadboard top (above your own table top with --no-breadboard), and "
    "customer_supplied_components[].axis_height_mm the height of each of your own components' design position (for "
    "your light source, where its beam starts) -- the height to set up your own equipment at. 'resolution show ID "
    "--locale zh-CN|en' and 'order show ORDER_ID --locale zh-CN|en' state it for people; the Order keeps it as "
    "assembly.optic_axis_heights. Components OpenSci does not supply -- your light source, a component that "
    "is not an OpenSci catalog product (such as an optic whose model the Project declares: optical_models, "
    "optical_model_id), a simulation-only or currently unavailable product, and every component you mark "
    "with --customer-supplied INSTANCE_ID (one you already own) -- stay in the design but are not on the BOM: "
    "customer_supplied_components lists each with its reason_code; they are outside OpenSci's delivery and warranty. "
    "The assembly keeps the space and beam path of a catalog product or a declared model free; a declared model must "
    "say where the beam leaves it (its frame, or a unit's ports), else a complete system is refused with "
    "DECLARED_OPTIC_BEAM_PATH_UNDEFINED. "
    "A design whose source is an extended emitter, an emitting sample or a sampled source (SAMPLED_INTENSITY, "
    "SAMPLED_FIELD, SPECKLE, FLUORESCENCE, RAMAN) is bought with --optics-only: its Optical Layout Drawing gives every "
    "element and draws beam paths for Gaussian sources only; a laboratory layout of it is refused with "
    "EXTENDED_EMITTER_NOT_RESOLVABLE (its light is not one beam to keep clear). "
    "A customer-supplied component of the inline optical train (a lens, mirror, splitter, filter, window, polarizer, "
    "waveplate, prism, or an aperture the beam continues through to OpenSci optics) makes the design self-assembly only "
    "(assembly_service.available is false and names it; --assembly-service is refused with ASSEMBLY_SERVICE_UNAVAILABLE); "
    "your light source, detectors and beam dumps do not. An ORDERABLE optic OpenSci has no mount for (catalog "
    "opensci_mounting NOT_AVAILABLE) is supplied unmounted with OpenSci mechanics: its BOM line carries the notice "
    "SUPPLIED_UNMOUNTED, you mount it at its design position, and the assembly service is not offered for that design. "
    "Submitting the same run with the same options "
    "again returns the same resolution; after OpenSci changed its catalog, prices or supply, it returns a new resolution "
    "on the current basis (also the step after RESOLUTION_NO_LONGER_AVAILABLE). To buy listed parts without a design, "
    "use 'resolve-parts'. " + CONNECTION_HELP
)
PARTS_DESCRIPTION = (
    "Price a part list for an Order, without a design: PARTS.json is {\"lines\": [{\"line_id\": \"L1\", "
    "\"opensci_sku\": \"OS-OPT-...\", \"quantity\": 2}, ...], \"mechanical_supply\": \"NONE\" | \"DERIVED\", "
    "\"mounting\": {\"L1\": {\"beam_height_mm\": 125, \"retained_dofs\": [...], \"incidence_deg\": 0}}}. Lines are "
    "OpenSci part numbers: optics (OS-OPT-*, 'os-optics catalog list'), mounts, posts, post holders and "
    "breadboards (OS-MECH-*, 'os-optics products mechanical'); the most lines and units a list may have are the "
    "service's purchase limits (GET /api/v1, commercial_rules.purchase_limits). Mechanical "
    "supply NONE (default) supplies exactly the listed parts; DERIVED has OpenSci select a mount, post and post holder "
    "for each optic unit at the beam height its mounting entry states (measured from the surface the holders stand on; "
    "every optic line needs one, else MOUNTING_INPUT_REQUIRED), keeping the adjustment axes listed in retained_dofs "
    "(default: none) at the angle of incidence (default 0 degrees, 45 for mirrors and plate splitters); when the "
    "first mount cannot reach the height the other compatible mounts are tried, else the line is refused with "
    "MOUNTING_NOT_AVAILABLE (details: line_id, reason, the reachable range when known). --mechanics none|derived "
    "replaces the file's choice (none leaves the mounting entries out). The file may also state the other order "
    "options, \"optics_source\": \"CATALOG\", \"platform\": \"NONE\" and \"assembly\": \"CUSTOMER\" (a part list takes "
    "these; a listed breadboard is an ordinary line; other values are refused with ORDER_OPTION_ON_HOLD or "
    "PURCHASE_OPTIONS_CONFLICT). A part number OpenSci does not supply is "
    "refused with PART_NOT_ORDERABLE (details.line_ids). Prints the resolution: resolution_id, order_options (basis "
    "PART_LIST), the Customer Purchase BOM with per-part prices and the Shipping line, the delivery terms (parts "
    "shipped unassembled) and the agreement: OpenSci supplies the listed products and has not checked that they work "
    "together as an optical system; with DERIVED mechanics it checked that each optic's mount, post and holder fit "
    "together at the stated beam height, and the deliverable is the mounting sheet ('resolution mounting-sheet'). "
    "Order it with 'order create RESOLUTION_ID'. Submitting the same list again returns the same resolution. "
    "The BOM-only OpenSci service refuses mechanical supply DERIVED and mounting entries (ORDER_OPTION_RETIRED): use "
    "'os-optics order quote BOM.json', which quotes a purchase BOM with its line review. "
    + CONNECTION_HELP
)
PRODUCTS_DESCRIPTION = (
    "OpenSci's mechanical products, the part numbers a part list ('resolve-parts') may name besides catalog optics: "
    "mounts, posts, post holders and breadboards (OS-MECH-*), sorted by part number, with their names (en / zh-CN) and "
    "facts -- a mount's centre height, the optic it holds, its adjustment axes and post thread; a post's length, "
    "diameter and thread; a breadboard's size, hole pitch and thread. --kind lists one kind; --json prints the "
    "service's document (default: one line per product). Prices come with a resolution. " + CONNECTION_HELP
)
MOUNTING_SHEET_DESCRIPTION = (
    RETIRED_NOTE + "Locally: 'os-optics assembly plan show PLAN' lists every support chain. "
    "The mounting sheet of OpenSci mounts chosen per optic without a laboratory layout (a part list with mechanical "
    "supply DERIVED, or an optical-system design bought with --mechanics derived --platform none): for each optic unit "
    "the number of sets, the angle of incidence and where it comes from, its mount, post and post holder, the assembly "
    "steps (optic into mount, mount onto post, post into holder, holder to the table), the beam-height arithmetic, the "
    "adjustments the hardware provides, the checks made, the checks not made with their reason, the assumed values and "
    "the conditions of use (a check is made only when its status is PASS). Prints the "
    "JSON document (English and Simplified Chinese text); --locale en|zh-CN prints it as text in that language; --out "
    "FILE writes it there instead (the text with --locale, else the JSON) and prints a summary. " + CONNECTION_HELP
)
RESOLUTION_DESCRIPTION = (
    "Read a commercial resolution: 'show RESOLUTION_ID' prints the order options (order_options), the Customer Purchase "
    "BOM with its prices, how each retained degree of freedom is provided, the breadboard when there is one, the "
    "optical-axis height (frame.optic_axis_heights) and the configuration notes; with --locale zh-CN or en the resolution presented for people, with the optical-axis height "
    "and the height to set up each of your own components at. " + CONNECTION_HELP
)
ADDRESS_DESCRIPTION = (
    "Your organization's shipping addresses (mainland China). 'list' shows them, the default first (a token with "
    "commercial:order:place or commercial:order:read); 'add' adds one (commercial:order:place): the recipient, an "
    "11-digit mobile number, the province (in Chinese, e.g. 广东省), city, district and street address, optionally the "
    "postal code and a label; the first address becomes the default, --default makes this one the default. Editing "
    "and deleting addresses is done on the OpenSci Account website. " + CONNECTION_HELP
)
QUOTE_DESCRIPTION = (
    "Quote a purchase BOM: BOM.json is {\"schema\": \"opensci.commercial.part_list_submission\", \"schema_version\": "
    "\"2\", \"lines\": [{\"line_id\": \"L1\", \"opensci_sku\": \"OS-OPT-...\", \"quantity\": 2, \"purpose\": \"PLAN\"}, "
    "...]} -- OpenSci part numbers (optics OS-OPT-*, mounts, posts, post holders and breadboards OS-MECH-*) and quantities, "
    "as 'os-optics assembly plan bom PLAN --purchase --out BOM.json' writes it or written by hand (line_id and purpose "
    "PLAN|SPARE are optional). OpenSci checks that every line can be supplied now (never whether the parts fit together) "
    "and prices the lines that can. Every line orderable: prints the quote and the next command, 'os-optics order create "
    "RESOLUTION_ID' (exit 0). Some lines not suppliable as listed (NOT_ORDERABLE, TEMPORARILY_UNAVAILABLE, ABOVE_LIMIT): "
    "prints the changed lines and writes the review file (default BOM.review.json beside the BOM, or --review-out) with a "
    "proposed confirmation that keeps the other lines and removes the changed ones; edit its decisions if the person wants "
    "(QUANTITY_CHANGED with a quantity for an ABOVE_LIMIT line, SUBSTITUTED with another part number and quantity, REMOVED) "
    "and send it with 'order quote BOM.json --confirm REVIEW.json' (exit 1 until then; the quote cannot be ordered before "
    "its changes are confirmed). --confirm needs the BOM the review was written for: the review file records that BOM "
    "(bom_request_sha256), and a review of another BOM, a missing BOM or a review file of an earlier os-optics is "
    "refused before anything is sent (CONFIRMATION_BOM_MISMATCH, INPUT_FILE_UNREADABLE, CONFIRMATION_NOT_POSSIBLE). If "
    "supply changed again, the confirmation is answered with a new review (the second one is BOM.review-2.json, and so on), "
    "confirmed the same way with the same BOM. No line suppliable: BOM_NOT_ORDERABLE, the review file is written, nothing "
    "is stored. The confirmation is checked here first (CONFIRMATION_INCONSISTENT, nothing sent). The review file is "
    "never written over the BOM or the --confirm file (REVIEW_OUT_COLLISION, nothing sent). Without a token nothing is "
    "sent: the output names the Account registration and sign-in pages and the token file, and the command to resume "
    "with (details.handoff.resume_command, quoted for this device's shell -- PowerShell on Windows, sh elsewhere -- and "
    "resume_argv, the same as an argument list): the same BOM, review and output files as absolute paths and the same "
    "--schema-version, so it sends the same request; repeating it never creates a second quote. --schema-version 1 sends "
    "the earlier part-list body for a service before BOM-only ordering (line ids filled in, no purpose, no confirmation). "
    + CONNECTION_HELP
)
QUOTE_EXIT_HELP = (
    "exit codes: 0 = every line orderable, 1 = changes to confirm (review file written), 64 = usage error, unreadable "
    "file (INPUT_FILE_UNREADABLE) or a review output that is an input (REVIEW_OUT_COLLISION); nothing sent, "
    "65 = BOM or confirmation refused locally, e.g. a review of another BOM (nothing sent), 67 = refused by the OpenSci "
    "service (BOM_NOT_ORDERABLE writes "
    "the review file), 69 = the service is unreachable or unavailable, 77 = no token (see the handoff), 78 = OPENSCI_API_URL "
    "is not set or not usable"
)
ORDER_DESCRIPTION = (
    "Orders of a resolution. 'quote BOM.json' quotes a purchase BOM with OpenSci's line review (see 'order quote --help'). "
    "'create' creates the Order directly: the token (commercial:order:place) authorizes it. "
    "It ships to --address ADDRESS_ID ('account address list') or, without it, to the default address; with no "
    "address at all it is refused (recovery ADD_SHIPPING_ADDRESS: 'account address add'). "
    "The Order of a design and of a part list is created the same way. "
    "When the organization already has an Order of the same design or part list (the resolution's design_orders), "
    "'create' is "
    "refused with DESIGN_ALREADY_ORDERED (recovery CONFIRM_ADDITIONAL_ORDER): show the person those Orders and only if "
    "they want to buy the design again run it with --additional-order. "
    "A single-use ordering token is spent only when a new Order succeeds; retrying the same idempotent request "
    "returns that Order, and the token can still read and track Orders. "
    "The Order fixes the lines, prices and total and returns its Order Number, quotation, agreement and corporate "
    "bank-transfer instructions; present them to the customer. Its output also names the 'feedback submit' command "
    "for sending OpenSci feedback about ordering (optional). 'report-transfer' reports that the customer made the "
    "bank transfer: run it only after the person who paid says it was made. OpenSci then confirms receipt; the "
    "Assembly Detail is available after that confirmation. When OpenSci could not match the transfer and reopened the "
    "payment (state AWAITING_TRANSFER again), run it again after the next transfer: it is recorded as a new report. "
    "'withdraw' withdraws an Order while it is unpaid and no "
    "transfer was reported, only when the person asks (to change a design after ordering: withdraw, run the design "
    "again, order again); the Order and its data are deleted, so tell the person it was withdrawn. After a reported "
    "transfer or a payment it is refused (ORDER_NOT_WITHDRAWABLE): the person contacts OpenSci. A quote with changed "
    "lines is refused until they are confirmed (BOM_CHANGES_UNCONFIRMED: 'order quote BOM.json --confirm REVIEW.json'); "
    "a design resolution can no longer be ordered from the BOM-only service (ORDERING_BASIS_RETIRED). " + CONNECTION_HELP
)
PREVIEW_DESCRIPTION = (
    RETIRED_NOTE + "Locally: 'os-optics assembly plan preview PLAN --out preview.zip' and 'os-optics view PLAN'. "
    "Fetch the Experimental Lab Assembly of a resolution, open it in memory and run the local layout / collision check "
    "on this device. Prints a summary; --out writes the assembly check result (JSON); --view opens it in the local "
    "viewer; --snapshot writes PNG views. No decrypted package is written. " + CONNECTION_HELP
)
DRAWING_COMMAND_DESCRIPTION = (
    RETIRED_NOTE + "Locally: 'os-optics assembly plan draw PLAN --out DIR'. "
    "The Detailed Assembly Drawing of a resolution, generated on this device: opens the Assembly Preview and the "
    "Assembly Detail in memory, runs the local assembly validation and writes assembly_drawing.svg, assembly_drawing.pdf, "
    "drawing_result.json and drawing_manifest.json into --out; nothing is uploaded (drawings stay on this device; "
    "--no-upload is accepted and has no effect). When the local assembly check passed, the Detailed Assembly Drawing is "
    "written, ready for delivery (exit 0). When it FAILED or could not be completed the drawing is refused, nothing is "
    "written (exit 65, the JSON error lists the issues); with --diagnostic a diagnostic sheet with the issues "
    "behind the status is written instead, not ready for delivery (exit 2). The summary names the sheet "
    "(status, delivery_ready). --locale zh-CN writes the sheet in "
    "Simplified Chinese (default en). The "
    "Assembly Detail is available once OpenSci has confirmed the payment of an Order of this resolution (exit 75 until "
    "then). An Order without OpenSci mechanics has no assembly drawing: use 'order layout ORDER_ID' for its Optical "
    "Layout Drawing; OpenSci mounts chosen per optic without a layout come with the mounting sheet ('order "
    "mounting-sheet ORDER_ID'). "
    "The summary states the drawing (status, delivery_ready, validation, files, out). "
    + CONNECTION_HELP
)
FEEDBACK_CATEGORY_CHOICES = ("PROBLEM", "FEATURE_REQUEST", "DOCUMENTATION")
FEEDBACK_DESCRIPTION = (
    "Send feedback to OpenSci: a problem you met while doing what the user needed (PROBLEM), a feature the user or you "
    "would need (FEATURE_REQUEST), or documentation that was missing or wrong (DOCUMENTATION). Needs an OpenSci Account "
    "token (Agent or Review profile). One problem or suggestion per submission: --summary is one line (5 to 200 "
    "characters); --details or --details-file (UTF-8; '-' reads standard input) says what the user needed, the commands "
    "you ran, what happened and what you expected (1 to 8000 characters). The engine version, the Python runtime and the "
    "active local catalog release are added; --error-code, --request-id, --resolution-id, --order-id, --command, --agent "
    "(default: OPENSCI_AGENT_NAME) and --locale add context. Include no tokens, passwords, payment data or personal data "
    "beyond what the problem needs (text holding an Account token is refused before sending). Tell the user what you "
    "sent. Limits: 10 submissions per token per hour and 50 per organization per day; above them the service answers "
    "FEEDBACK_RATE_LIMITED (exit 69, recovery RETRY_LATER, retry_after_seconds). Prints the receipt: feedback_id, "
    "received_at, status RECEIVED; sending the same feedback again returns the same receipt (replayed: true). "
    + CONNECTION_HELP
)
ARTIFACT_ROLES = ("DRAWING_PDF", "DRAWING_SVG", "DRAWING_RESULT", "DRAWING_MANIFEST", "VALIDATION_RESULT")
ARTIFACTS_DESCRIPTION = (
    RETIRED_NOTE + "Drawings stay on your machine. "
    "Artifacts stored for a resolution (local assembly validations and drawings uploaded from this device). 'show' lists "
    "a set's files with their role, file name, size and SHA-256 (roles: DRAWING_PDF assembly_drawing.pdf, DRAWING_SVG "
    "assembly_drawing.svg, DRAWING_RESULT drawing_result.json, DRAWING_MANIFEST drawing_manifest.json, VALIDATION_RESULT "
    "assembly_local_validation.json); 'download SET_ID ROLE --out FILE' writes one of them, checked against its recorded "
    "size and SHA-256. " + CONNECTION_HELP
)


class ArgumentParser(argparse.ArgumentParser):
    """``argparse.ArgumentParser`` whose usage errors exit with the documented usage-error code (64, not argparse's 2,
    which is UNKNOWN here) and never repeat an Account token typed on the command line."""

    def error(self, message: str) -> Any:
        self.print_usage(sys.stderr)
        self.exit(USAGE_ERROR, f"{self.prog}: error: {TOKEN_SHAPED.sub('oat_<redacted>', message)}\n")


def _out(doc: Any) -> None:
    print(json.dumps(doc, indent=2, ensure_ascii=True))


def exit_code_for(error: Any) -> int:
    """The exit code of an ``OpenSciApiError``."""

    from .client import (
        LocalInputError,
        OpenSciConfigurationError,
        OpenSciConnectionError,
        ProtectedContentError,
        Recovery,
    )

    token_recovery = (Recovery.PROVIDE_TOKEN, Recovery.REQUEST_NEW_TOKEN, Recovery.REQUEST_SCOPE)
    if isinstance(error, OpenSciConfigurationError):
        return NOT_AUTHORIZED if error.recovery in token_recovery else NOT_CONFIGURED
    if isinstance(error, OpenSciConnectionError):
        return SERVICE_UNAVAILABLE
    if isinstance(error, (LocalInputError, ProtectedContentError)):
        return LOCAL_INPUT_ERROR
    if error.recovery in (Recovery.WAIT_FOR_PAYMENT_CONFIRMATION, Recovery.CREATE_ORDER_FIRST):
        return NOT_YET_AVAILABLE
    if error.recovery in token_recovery or error.family == "UNAUTHENTICATED":
        return NOT_AUTHORIZED
    if error.recovery in (Recovery.RETRY, Recovery.RETRY_LATER) or (
            error.http_status is not None and error.http_status >= HTTP_SERVER_ERROR):
        return SERVICE_UNAVAILABLE
    return API_REFUSED


def _error(error: Any, *, kept: str | None = None, repeat_note: str | None = None) -> int:
    """The JSON error document on stdout and its exit code; a ``TOKEN_MISSING`` refusal also prints its handoff
    (``details.handoff``: how to register or sign in, where to save the token, the command to resume with) on stderr."""

    from .client.credentials import handoff_text
    from .client.errors import TOKEN_MISSING

    _out({"status": "ERROR", "error": error.as_dict()})
    handoff = error.details.get("handoff") if error.code == TOKEN_MISSING else None
    if isinstance(handoff, dict):
        sys.stderr.write(handoff_text(handoff, kept=kept, repeat_note=repeat_note))
    return exit_code_for(error)


def _client(args: argparse.Namespace, token_file: str | None = None, *, anonymous: bool = False) -> Any:
    """The client of the command; ``anonymous``: a command the BOM-only service retired, sent without a token when
    there is none (``OpenSciClient`` ``anonymous``); the client is kept on ``args`` for ``_retired``."""

    from .client import OpenSciClient

    path = token_file if token_file is not None else getattr(args, "token_file", None)
    client = OpenSciClient.from_environment(Path(path) if path else None, api_url=getattr(args, "api_url", None),
                                            anonymous=anonymous)
    if anonymous:
        args.anonymous_client = client
    return client


def _retired(work: Any, hint: str) -> Any:
    """A command the BOM-only service retired: its retirement refusal carries ``RETIRED_HINTS[hint]`` (the local command
    that replaces it), and a ``401`` of an earlier service to an anonymous request is ``TOKEN_MISSING``."""

    def run(args: argparse.Namespace) -> int:
        from .client import OpenSciApiError
        from .client.errors import RETIREMENT_CODES

        try:
            return work(args)
        except OpenSciApiError as exc:
            client = getattr(args, "anonymous_client", None)
            error = client.anonymous_refusal(exc) if client is not None else exc
            if error.code in RETIREMENT_CODES:
                error.local_hint = RETIRED_HINTS[hint]
            if error is exc:
                raise
            raise error from None

    return run


def _guarded(work: Any) -> Any:
    """Run a command body; every client error becomes the JSON error document and its exit code."""

    def handler(args: argparse.Namespace) -> int:
        from .assembly import AssemblyPreviewPackageError
        from .assembly_drawing import AssemblyDrawingError
        from .client import OpenSciApiError

        try:
            return work(args)
        except OpenSciApiError as exc:
            return _error(exc)
        except AssemblyDrawingError as exc:
            _out({"status": "ERROR", "error": exc.as_dict()})
            return LOCAL_INPUT_ERROR
        except AssemblyPreviewPackageError as exc:
            _out({"status": "ERROR", "error": {"code": "ASSEMBLY_PACKAGE_INVALID", "problems": list(exc.problems)}})
            return LOCAL_INPUT_ERROR
        except (FileExistsError, IsADirectoryError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return USAGE_ERROR
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return USAGE_ERROR
        except OSError as exc:
            print(f"error: cannot read or write a file: {type(exc).__name__}: {exc.strerror or exc}", file=sys.stderr)
            return USAGE_ERROR

    return handler


# ------------------------------------------------------------------------------------------ account


def _credential_doc(token: Any) -> dict[str, Any]:
    return {"token_id": token.token_id, "source": token.source, "path": str(token.path) if token.path else None}


def _account_status(args: argparse.Namespace) -> int:
    client = _client(args)
    _out({"api_url": client.api_url, "credential": _credential_doc(client.token), "token": client.token_status()})
    return 0


def _token_create(args: argparse.Namespace) -> int:
    from .client import default_token_file, save_token_file

    client = _client(args)
    target = None
    if args.save is not None:
        target = Path(args.save).expanduser() if args.save else default_token_file()
        if client.token.path is not None and target.resolve() == client.token.path.resolve():
            print("error: the token in use was read from this file; save the new token to another file, or use "
                  "'os-optics account token rotate'", file=sys.stderr)
            return USAGE_ERROR
        if target.exists() and not args.overwrite:
            print(f"error: {target} already exists (add --overwrite to replace it)", file=sys.stderr)
            return USAGE_ERROR
    issued = client.issue_token(profile=args.profile, scopes=args.scopes, lifetime_hours=args.lifetime_hours,
                                label=args.label, lifecycle=args.lifecycle.replace("-", "_").upper())
    doc: dict[str, Any] = {"status": "ISSUED", "token": issued.status}
    if issued.credential is None:
        doc["credential"] = None
        doc["notice"] = "This request repeated an earlier issuance; its token is shown only at first issuance."
    elif target is not None:
        try:
            doc["saved_to"] = str(save_token_file(issued.credential, target, overwrite=args.overwrite))
        except OSError as exc:  # issued but not stored: show it once rather than lose it
            doc["credential"] = issued.credential.reveal()
            doc["notice"] = (f"The token could not be written to {target} ({type(exc).__name__}). It is shown only now "
                             "and OpenSci keeps no copy: store it, or revoke it with 'account token revoke'.")
    else:
        doc["credential"] = issued.credential.reveal()
        doc["notice"] = ("This token is shown only now and OpenSci keeps no copy: store it (e.g. in a token file for "
                         "OPENSCI_TOKEN_FILE). Next time use --save to write it without printing it.")
    _out(doc)
    return 0


def _token_list(args: argparse.Namespace) -> int:
    _out(_client(args).tokens())
    return 0


def _token_revoke(args: argparse.Namespace) -> int:
    _out(_client(args).revoke_token(args.token_id))
    return 0


def _token_rotate(args: argparse.Namespace) -> int:
    from .client import OpenSciApiError, Recovery, save_token_file

    current = _client(args)
    status = current.token_status()
    scopes = list(status.get("scopes") or [])
    if MANAGE_SCOPE in scopes:
        return _error(OpenSciApiError(
            "TOKEN_NOT_ROTATABLE", f"a token carrying {MANAGE_SCOPE} is replaced from your OpenSci Account",
            family="REJECTED", details={"token_id": current.token.token_id}, recovery=Recovery.REQUEST_NEW_TOKEN))
    issuer = _client(args, token_file=args.issuer_token_file)
    issued = issuer.issue_token(scopes=scopes, lifetime_hours=args.lifetime_hours, label=status.get("label"),
                                lifecycle=status.get("lifecycle", "TIME_LIMITED"))
    if issued.credential is None:
        return _error(OpenSciApiError("TOKEN_NOT_ISSUED", "the service returned no new token", family="SERVER_ERROR",
                                      recovery=Recovery.RETRY))
    doc: dict[str, Any] = {"status": "ROTATED", "token": issued.status, "previous_token_id": current.token.token_id}
    if current.token.path is not None:
        doc["saved_to"] = str(save_token_file(issued.credential, current.token.path, overwrite=True))
    else:
        doc["credential"] = issued.credential.reveal()
        doc["notice"] = (f"The previous token came from {current.token.source}: replace it there with this token, which "
                         "is shown only now.")
    try:
        doc["previous_token"] = current.revoke_current_token()
    except OpenSciApiError as exc:
        doc["previous_token"] = None
        doc["previous_token_error"] = exc.as_dict()
    _out(doc)
    return 0


def _address(args: argparse.Namespace) -> int:
    client = _client(args)
    if args.address_command == "list":
        _out(client.addresses())
    else:
        _out(client.add_address(recipient_name=args.recipient, mobile_phone=args.phone, province=args.province,
                                city=args.city, district=args.district, street_address=args.street,
                                postal_code=args.postal_code, label=args.label, make_default=args.default))
    return 0


def _logout(args: argparse.Namespace) -> int:
    from .client import OpenSciApiError, remove_token_file

    client = _client(args)
    doc: dict[str, Any] = {"status": "LOGGED_OUT", "token_id": client.token.token_id}
    try:
        doc["token"] = client.revoke_current_token()
    except OpenSciApiError as exc:
        if exc.family != "UNAUTHENTICATED":  # an already expired / revoked token is logged out all the same
            return _error(exc)
        doc["token"] = None
    path = client.token.path
    doc["removed_token_file"] = str(path) if path is not None and remove_token_file(path) else None
    if path is None:
        doc["notice"] = f"The token came from {client.token.source}; remove it from there."
    _out(doc)
    return 0


# ------------------------------------------------------------------------------------------ resolution / order


def _local_refusal(error: Any) -> int:
    """A combination of options refused before anything is sent: the JSON error (the service's code) and exit 64."""

    _out({"status": "ERROR", "error": error.as_dict()})
    return USAGE_ERROR


def _read_json_file(path: str, what: str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        raise ValueError(f"{what} {path} is not UTF-8 text") from None
    except json.JSONDecodeError as exc:
        raise ValueError(f"{what} {path} is not JSON: {exc.msg} (line {exc.lineno})") from None


def _mounting(path: str) -> dict[str, Any]:
    """The ``--mounting FILE`` of an optical-system design: ``{instance_id: {"beam_height_mm", "incidence_deg"?}}``."""

    document = _read_json_file(path, "--mounting")
    if not isinstance(document, dict) or not document or not all(
            isinstance(key, str) and isinstance(entry, dict) for key, entry in document.items()):
        raise ValueError(f"--mounting {path} must be a JSON object {{INSTANCE_ID: {{\"beam_height_mm\": H, ...}}}} with "
                         "an entry for each OpenSci optic")
    return document


def _design_options(args: argparse.Namespace) -> tuple[dict[str, str | None], dict[str, list[str]]]:
    """``(options, flags)``: the order options of ``resolve`` (``--mechanics`` / ``--platform`` / ``--assembly``), with the
    presets ``--optics-only`` / ``--no-breadboard`` / ``--assembly-service`` as the same options, and the flags that gave
    each. A preset and an option that disagree raise ``LocalInputError`` ``PURCHASE_OPTIONS_CONFLICT``; ``--mounting``
    means mechanics without a platform. Which options go together is the rule of ``client.design_order_constraints``
    (``_resolve`` names its refusal by the flags given)."""

    from .client import LocalInputError, Recovery

    chosen: dict[str, str | None] = {"mechanics": None, "platform": None, "assembly": None}
    flags: dict[str, list[str]] = {name: [] for name in chosen}
    for name, value, flag in (("mechanics", "NONE" if args.optics_only else None, "--optics-only"),
                              ("platform", "NONE" if args.no_breadboard else None, "--no-breadboard"),
                              ("assembly", "OPENSCI" if args.assembly_service else None, "--assembly-service"),
                              ("mechanics", args.mechanics and args.mechanics.upper(), f"--mechanics {args.mechanics}"),
                              ("platform", args.platform and args.platform.upper(), f"--platform {args.platform}"),
                              ("assembly", args.assembly and args.assembly.upper(), f"--assembly {args.assembly}")):
        if value is None:
            continue
        if chosen[name] not in (None, value):
            raise LocalInputError("PURCHASE_OPTIONS_CONFLICT", f"{' and '.join([*flags[name], flag])} choose different "
                                  f"{name} options: give one", details={"conflicting_options": [*flags[name], flag]},
                                  recovery=Recovery.FIX_REQUEST)
        chosen[name] = value
        flags[name].append(flag)
    without_platform = {"mechanics": "NONE", "platform": "BREADBOARD", "assembly": "OPENSCI"}
    if args.mounting:
        clashing = [flag for name, value in without_platform.items() if chosen[name] == value for flag in flags[name]]
        if clashing:
            raise LocalInputError("PURCHASE_OPTIONS_CONFLICT", "--mounting gives the beam heights of OpenSci mounts per "
                                  "optic without a platform (an optical-system design): it goes with --mechanics derived "
                                  f"and --platform none, not with {', '.join(clashing)}",
                                  details={"conflicting_options": ["--mounting", *clashing]}, recovery=Recovery.FIX_REQUEST)
        chosen["platform"] = "NONE"
    # without OpenSci mechanics a board-size limit and the retained-DOF requirement do not apply: the service leaves them
    # out and lists them in the answer (options_not_applied)
    return chosen, flags


#: The option each field of ``client.design_order_constraints`` is on the command line (``details.conflicting_options``).
_OPTION_OF_FIELD = {"mechanical_supply": "mechanics", "platform": "platform", "assembly": "assembly"}


def _named_by_flags(error: Any, flags: dict[str, list[str]]) -> Any:
    """The refusal of ``client.design_order_constraints`` with its conflicting fields named by the flags given: the same
    code, recovery and family; ``details.conflicting_options`` the flags, in the client's order; the message led by them."""

    from .client import LocalInputError

    given = [flag for name in error.details.get("conflicting_options", ()) for flag in flags.get(_OPTION_OF_FIELD.get(name, ""), ())]
    if not given:
        return error
    return LocalInputError(error.code, f"{', '.join(given)} cannot be combined: {error.message}",
                           details={**error.details, "conflicting_options": given}, recovery=error.recovery)


def _resolve(args: argparse.Namespace) -> int:
    from .client import LocalInputError, design_order_constraints

    try:
        options, flags = _design_options(args)
        try:
            option_keys = design_order_constraints(mechanical_supply=options["mechanics"], platform=options["platform"],
                                                   assembly=options["assembly"])
        except LocalInputError as exc:
            raise _named_by_flags(exc, flags) from None
    except LocalInputError as exc:
        return _local_refusal(exc)
    constraints: dict[str, Any] = {}
    if "include_breadboard" in option_keys:
        constraints["include_breadboard"] = False
    if "assembly_service" in option_keys:
        constraints["assembly_service"] = True
    if args.max_board_size_mm is not None:
        constraints["max_board_size_mm"] = list(args.max_board_size_mm)
    if args.require_all_retained_dofs:
        constraints["require_all_retained_dofs"] = True
    if args.customer_supplied:
        constraints["customer_supplied_components"] = sorted(set(args.customer_supplied))
    if "purchase_scope" in option_keys:
        constraints["purchase_scope"] = option_keys["purchase_scope"]
    if args.spare:
        constraints["spare_units"] = dict(sorted(dict(args.spare).items()))
    if args.mounting:
        constraints["mounting"] = _mounting(args.mounting)
    _out(_client(args, anonymous=True).submit(args.run_dir, customer_constraints=constraints or None))
    return 0


def _resolve_parts(args: argparse.Namespace) -> int:
    document = _read_json_file(args.parts, "the part list")
    mechanics = args.mechanics.upper() if args.mechanics else None
    _out(_client(args).submit_part_list(document, mechanical_supply=mechanics))
    return 0


def _fact_text(value: Any) -> str:
    if isinstance(value, list):
        return "/".join(str(v) for v in value) if value else "none"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _products(args: argparse.Namespace) -> int:
    document = _client(args).mechanical_products(kind=args.kind)
    if args.json:
        _out(document)
        return 0
    products = document.get("products", []) if isinstance(document, dict) else []
    name_field = "display_name_zh_cn" if args.locale == "zh-CN" else "display_name"
    for product in products:
        if not isinstance(product, dict):
            continue
        facts = product.get("facts") if isinstance(product.get("facts"), dict) else {}
        name = product.get(name_field) or product.get("display_name") or ""
        print("  ".join(part for part in (str(product.get("opensci_sku", "")), str(product.get("kind", "")), str(name),
                                          ", ".join(f"{k}={_fact_text(v)}" for k, v in facts.items())) if part))
    if isinstance(document, dict) and document.get("catalog_version"):
        print(f"({len(products)} products, catalog {document['catalog_version']}; --json for the full document)")
    return 0


def _mounting_sheet(args: argparse.Namespace) -> int:
    from .mounting_sheet import render_mounting_sheet

    client = _client(args, anonymous=True)
    order_id = getattr(args, "order_id", None)
    document = client.order_mounting_sheet(order_id) if order_id else client.resolution_mounting_sheet(args.resolution_id)
    text = render_mounting_sheet(document, args.locale) if args.locale else None
    if not args.out:
        if text is None:
            _out(document)
        else:
            sys.stdout.write(text)
        return 0
    from .output_files import replace_files

    target = Path(args.out)
    data = text if text is not None else json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    replace_files(target.parent, {target.name: data.encode("utf-8")})  # a regenerable sheet: an earlier one is replaced
    units = next((document.get(k) for k in ("units", "optics") if isinstance(document, dict) and k in document), None)
    _out({"out": str(target), "format": "text" if text is not None else "json", "locale": args.locale,
          "units": len(units) if isinstance(units, list) else None,
          **({"order_id": order_id} if order_id else {"resolution_id": args.resolution_id})})
    return 0


def _spare(value: str) -> tuple[str, int]:
    """One ``--spare SKU=N`` argument."""

    sku, _, count = value.partition("=")
    try:
        number = int(count)
    except ValueError:
        number = 0
    if not sku or number < 1:
        raise argparse.ArgumentTypeError(f"takes SKU=N with N >= 1, got {value!r}")
    return sku, number


def _resolution_show(args: argparse.Namespace) -> int:
    client = _client(args)
    if args.locale:
        _out(client.resolution_presentation(args.resolution_id, locale=args.locale))
    else:
        _out(client.resolution(args.resolution_id))
    return 0


def _resolution_layout(args: argparse.Namespace) -> int:
    from .cli_layout import write_layout

    package = _client(args, anonymous=True).resolution_optical_layout(args.resolution_id)
    return write_layout(package, args.out, locale=args.locale, extra={"resolution_id": args.resolution_id}, save_package=True)


def _order_layout(args: argparse.Namespace) -> int:
    from .cli_layout import write_layout

    package = _client(args, anonymous=True).order_optical_layout(args.order_id)
    return write_layout(package, args.out, locale=args.locale, extra={"order_id": args.order_id}, save_package=True)


def _with_feedback_hint(document: dict[str, Any]) -> dict[str, Any]:
    """The Order as the service returned it, plus the command for feedback about ordering (optional; nothing is sent)."""

    order_id = (document.get("order") or {}).get("order_id") if isinstance(document, dict) else None
    if not order_id:
        return document
    order_number = (document.get("order") or {}).get("order_number")
    return {**document, "account_handoff": {
        "path": f"/orders/{order_id}",
        "order_number": order_number,
        "message": f"Open Account → Orders → {order_number} to read the quotation, contract, payment instructions and status."},
        "feedback": {
        "message": "To send OpenSci feedback about ordering, use this command (optional).",
        "command": f"os-optics feedback submit --category PROBLEM --summary TEXT --details-file FILE --order-id {order_id}"}}


#: The hint of each refusal of ``order create`` that names a local step (by code).
ORDER_CREATE_HINTS = {"ORDERING_BASIS_RETIRED": RETIRED_HINTS["order-design"], "ROUTE_RETIRED": RETIRED_HINTS["order-design"],
                      "ORDER_OPTION_RETIRED": RETIRED_HINTS["mechanics"]}


def _create_order(client: Any, args: argparse.Namespace) -> dict[str, Any]:
    from .client import OpenSciApiError
    from .client.errors import BOM_CHANGES_UNCONFIRMED, CONFIRM_BOM_HINT

    try:
        return client.create_order(args.resolution_id, shipping_address_id=args.address, additional_order=args.additional_order)
    except OpenSciApiError as exc:
        exc.local_hint = CONFIRM_BOM_HINT if exc.code == BOM_CHANGES_UNCONFIRMED else ORDER_CREATE_HINTS.get(exc.code)
        raise


def _write_review(target: Path, document: dict[str, Any]) -> Path:
    from .output_files import replace_files

    replace_files(target.parent, {target.name: (json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode("utf-8")})
    return target


def _quote_input(path: str, argument: str) -> Any:
    """An input file of ``order quote`` as JSON; ``LocalInputError`` ``INPUT_FILE_UNREADABLE`` (``details.argument``,
    ``details.reason``: ``NOT_FOUND``, ``NOT_A_FILE``, ``NOT_READABLE``, ``NOT_UTF8`` or ``NOT_JSON``) otherwise."""

    from .client import LocalInputError, Recovery

    def unreadable(reason: str, what: str) -> LocalInputError:
        return LocalInputError(INPUT_FILE_UNREADABLE, f"{argument} {path} {what}; nothing was sent",
                               details={"argument": argument, "path": path, "reason": reason}, recovery=Recovery.FIX_REQUEST)

    file = Path(path)
    if not file.exists():
        raise unreadable("NOT_FOUND", "does not exist")
    if not file.is_file():
        raise unreadable("NOT_A_FILE", "is not a file")
    try:
        text = file.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        raise unreadable("NOT_UTF8", "is not UTF-8 text") from None
    except OSError as exc:
        raise unreadable("NOT_READABLE", f"cannot be read ({type(exc).__name__})") from None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise unreadable("NOT_JSON", f"is not JSON: {exc.msg} (line {exc.lineno})") from None


def _quote_argv(bom: str, *, schema_version: str, confirm: str | None = None, review_out: str | None = None,
                api_url: str | None = None, as_json: bool = False) -> list[str]:
    """The argument list of an ``order quote`` command (``RESUME_PRESERVED``, in that order), its paths absolute so it
    runs from any directory."""

    argv = ["os-optics", "order", "quote", str(Path(bom).absolute()), "--schema-version", schema_version]
    if confirm:
        argv += ["--confirm", str(Path(confirm).absolute())]
    if review_out:
        argv += ["--review-out", str(Path(review_out).absolute())]
    if api_url:
        argv += ["--api-url", api_url]
    if as_json:
        argv.append("--json")
    return argv


def _review_path(bom: Path, round_number: int) -> Path:
    """The default file of review ``round_number``: ``BOM.review.json`` for the first, ``BOM.review-<n>.json`` after."""

    return bom.with_name(bom.stem + (REVIEW_SUFFIX if round_number == 1 else f".review-{round_number}.json"))


def _order_quote(args: argparse.Namespace) -> int:
    """``order quote BOM.json [--confirm REVIEW.json]`` (``QUOTE_DESCRIPTION``). Every local check -- the input files,
    the BOM, the binding of the review to the BOM, the confirmation and the review output -- runs before the token is
    looked up, and nothing is sent unless all of them pass."""

    from .client import LocalInputError, OpenSciApiError, OpenSciConfigurationError, Recovery
    from .client.bom_review import (CHANGES_REQUIRED, CONFIRMATION_BOM_MISMATCH, IS_DIRECTORY, ORDERABLE,
                                    REVIEW_OUT_COLLISION, SAME_AS_BOM, SAME_AS_REVIEW, bom_request_digest, changed_lines,
                                    check_review_binding, confirmation_body, purchase_bom_body, review_document, review_of,
                                    review_out_problem, review_round)
    from .client.credentials import resume_with
    from .client.errors import (BOM_CONFIRMATION_INVALID, BOM_NOT_ORDERABLE, RESOLUTION_NOT_FOUND, RETIREMENT_CODES,
                                TOKEN_MISSING)
    from .client.shell_words import command_line, device_shell

    if args.confirm and args.schema_version == "1":
        print("error: --confirm sends a confirmed BOM in schema version 2; leave out --schema-version 1", file=sys.stderr)
        return USAGE_ERROR
    bom = Path(args.bom)
    requote = command_line(["os-optics", "order", "quote", str(bom.absolute())])
    # phase A: local checks, nothing resolved or sent
    try:
        bom_document = _quote_input(args.bom, "BOM.json")
        review_file = _quote_input(args.confirm, "--confirm") if args.confirm else None
    except LocalInputError as exc:
        return _local_refusal(exc)
    bom_body = purchase_bom_body(bom_document, schema_version=args.schema_version)
    if review_file is not None:
        try:
            check_review_binding(bom_document, review_file)
        except LocalInputError as exc:
            if exc.code == CONFIRMATION_BOM_MISMATCH:
                exc.local_hint = (f"This review was written for another BOM: quote {args.bom} ({requote}) or name the BOM "
                                  "this review belongs to.")
            raise
        body = confirmation_body(review_file)
        round_number = review_round(review_file) + 1
        request_digest = review_file["bom_request_sha256"]
    else:
        body, round_number, request_digest = bom_body, 1, bom_request_digest(bom_document)
    review_out = Path(args.review_out) if args.review_out else _review_path(bom, round_number)
    inputs = {SAME_AS_BOM: bom, **({SAME_AS_REVIEW: Path(args.confirm)} if args.confirm else {})}
    collision = review_out_problem(review_out, inputs)
    if collision is not None:
        return _local_refusal(LocalInputError(
            REVIEW_OUT_COLLISION, f"the review file would be written to {review_out}, which is "
            + ("a directory" if collision["reason"] == IS_DIRECTORY else f"the input file {collision['input']}")
            + "; name another --review-out (nothing was sent)", details=collision, recovery=Recovery.FIX_REQUEST))
    # phase B: the token (a missing one hands off with the command that sends this same request)
    kept = f"BOM stays in {args.bom}"
    try:
        client = _client(args)
    except OpenSciConfigurationError as exc:
        if exc.code == TOKEN_MISSING and isinstance(exc.details.get("handoff"), dict):
            resume_with(exc.details["handoff"], _quote_argv(args.bom, schema_version=args.schema_version,
                                                            confirm=args.confirm, review_out=args.review_out,
                                                            api_url=args.api_url, as_json=args.json))
            return _error(exc, kept=kept, repeat_note=QUOTE_REPEAT_NOTE)
        raise
    # phase C: the request
    try:
        response = client.quote_bom(body, schema_version=body["schema_version"])
    except OpenSciApiError as exc:
        review = exc.details.get("bom_review") if exc.code == BOM_NOT_ORDERABLE else None
        if isinstance(review, dict):
            written = _write_review(review_out, review_document(body, review, None, bom_request_sha256=request_digest,
                                                                round_number=round_number))
            exc.local_hint = (f"No line of this BOM can be supplied now (the review is in {written}); change the BOM and "
                              "quote it again.")
            _out({"status": "ERROR", "error": exc.as_dict(), "review": str(written)})
            return exit_code_for(exc)
        if exc.code == BOM_CONFIRMATION_INVALID:
            exc.local_hint = ("The confirmed changes do not match the reviewed lines (details.problems): run --confirm again "
                              f"with the review file as 'order quote' wrote it, or quote the BOM again ({requote}).")
        elif exc.code == RESOLUTION_NOT_FOUND and args.confirm:
            exc.local_hint = (f"The reviewed quote no longer exists (it is kept for 7 days) or belongs to another "
                              f"organization: quote the BOM again ({requote}) and confirm the new review.")
        elif exc.code in RETIREMENT_CODES:
            exc.local_hint = RETIRED_HINTS["mechanics"]
        raise
    review = review_of(response)
    resolution_id = response.get("resolution_id")
    resolution = response.get("resolution") if isinstance(response.get("resolution"), dict) else {}
    status = review.get("status") if review else ORDERABLE
    summary: dict[str, Any] = {"status": status, "resolution_id": resolution_id, "replayed": bool(response.get("replayed")),
                               "schema_version": body["schema_version"], "lines": len(body["lines"]),
                               "confirmed": bool(body.get("confirmed_changes")), "bom_review": review,
                               "customer_bom": resolution.get("customer_bom")}
    code = 0
    if status == CHANGES_REQUIRED:
        written = _write_review(review_out, review_document(body, review, resolution_id, bom_request_sha256=request_digest,
                                                            round_number=round_number))
        next_argv = _quote_argv(args.bom, schema_version="2", confirm=str(written), api_url=args.api_url)
        summary.update(changed_lines=changed_lines(review), review=str(written), round=round_number,
                       message="Some lines cannot be supplied as listed: show them to the person, adjust the proposed "
                               "confirmation in the review file if they want, then confirm it; this quote cannot be "
                               "ordered before.",
                       next_command=command_line(next_argv), next_argv=next_argv, next_shell=device_shell())
        code = QUOTE_CHANGES_REQUIRED
    else:
        summary["next_command"] = f"os-optics order create {resolution_id}"
    if args.json:
        summary["response"] = response
    _out(summary)
    return code


def _order(args: argparse.Namespace) -> int:
    client = _client(args)
    command = args.order_command
    if command == "create":
        _out(_with_feedback_hint(_create_order(client, args)))
    elif command == "show":
        _out(client.order_presentation(args.order_id, locale=args.locale, document=args.document) if args.locale
             else client.order(args.order_id))
    elif command == "list":
        _out(client.orders(locale=args.locale))
    elif command == "withdraw":
        _out(client.withdraw_order(args.order_id))
    else:
        _out(client.report_bank_transfer(args.order_id))
    return 0


# ------------------------------------------------------------------------------------------ assembly


def _validation_summary(result: Any) -> dict[str, Any]:
    payload = result.payload()
    return {"status": result.status.value, "result_hash": result.result_hash,
            "package_content_hash": result.package.package_content_hash,
            "checks": {c.check.value: c.status.value for c in result.checks}, "issues": payload.get("issues", [])}


def _assembly_preview(args: argparse.Namespace) -> int:
    from .assembly import result_file_bytes, tolerances_with

    try:
        tolerances = tolerances_with(dict(args.tolerance or ()))
    except ValueError as exc:
        print(f"error: --tolerance: {exc}", file=sys.stderr)
        return USAGE_ERROR
    client = _client(args, anonymous=True)
    preview = client.assembly_preview(args.resolution_id)
    validation = client.validate_assembly(preview, tolerances=tolerances)
    doc: dict[str, Any] = {"resolution_id": args.resolution_id, "validation": _validation_summary(validation)}
    if args.out:
        Path(args.out).write_bytes(result_file_bytes(validation))
        doc["out"] = args.out
    if args.snapshot or args.view:
        try:
            from . import viewer  # the first-party local viewer
        except ImportError:
            print("error: the local viewer is not available in this installation", file=sys.stderr)
            return USAGE_ERROR
        if args.snapshot:
            written = viewer.render_snapshots(preview, args.snapshot)
            doc["snapshots"] = {name: Path(path).as_posix() for name, path in written.items()}
        if args.view:  # the page is served from memory until Ctrl+C
            server = viewer.view_assembly(preview)
            doc["viewer"] = {"url": server.url, "stop": "press Ctrl+C to stop the local page"}
            _out(doc)
            sys.stdout.flush()
            server.wait()
            return 0
    _out(doc)
    return 0


def _view_resolution(args: argparse.Namespace) -> int:
    from . import cli_view
    from .viewer import assembly_view_scene

    if not args.resolution:
        return cli_view.command(args)
    if args.run_dir:
        print("error: give either a run directory or --resolution, not both", file=sys.stderr)
        return USAGE_ERROR
    preview = _client(args, anonymous=True).assembly_preview(args.resolution)
    return cli_view.present(assembly_view_scene(preview), args, allow_html=False)


def register_view(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Extend the ``view`` command: ``view --resolution ID`` shows a resolution's Experimental Lab Assembly (opened in
    memory with the Account token); ``view RUN_DIR`` stays the Optical Simulation View of a local run."""

    parser.add_argument("--resolution", metavar="RESOLUTION_ID",
                        help="show the Experimental Lab Assembly of this resolution (uses your Account token; nothing "
                             "is written to disk except --snapshot images); retired by the BOM-only service: "
                             "'os-optics view PLAN' shows a local assembly plan")
    for action in _connection_options()._actions:
        if action.option_strings and action.dest != "help":
            parser.add_argument(*action.option_strings, dest=action.dest, help=action.help)
    parser.set_defaults(handler=_guarded(_retired(_view_resolution, "preview")))
    return parser


def _assembly_drawing(args: argparse.Namespace) -> int:
    client = _client(args, anonymous=True)
    preview = client.assembly_preview(args.resolution_id)
    drawing = client.assembly_drawing(args.resolution_id, args.out, preview=preview, locale=args.locale,
                                      diagnostic=args.diagnostic)
    result = drawing.result
    doc: dict[str, Any] = {
        "resolution_id": args.resolution_id,
        "status": result.drawing_status.value,
        "delivery_ready": result.delivery_ready,
        "locale": result.locale,
        "validation": _validation_summary(drawing.validation),
        "result_hash": result.result_hash,
        "files": {f.path: f.sha256 for f in drawing.manifest.files},
        "out": args.out,
    }
    # (nothing is uploaded: the drawing stays on this device; status and delivery_ready say which sheet it is)
    _out(doc)
    from .cli_exit import DIAGNOSTIC_SHEET_WRITTEN

    return 0 if result.delivery_ready else DIAGNOSTIC_SHEET_WRITTEN


# ------------------------------------------------------------------------------------------ artifacts


def _artifacts(args: argparse.Namespace) -> int:
    client = _client(args, anonymous=True)
    if args.artifacts_command == "list":
        _out(client.artifact_sets(args.resolution_id, artifact_kind=args.kind))
        return 0
    if args.artifacts_command == "show":
        record = client.artifact_set(args.artifact_set_id)
        files = record.get("files", ()) if isinstance(record, dict) else ()
        _out({**record, "roles": [f.get("role") for f in files if isinstance(f, dict)]} if isinstance(record, dict) else record)
        return 0
    from .client.api import artifact_role
    from .output_files import replace_files

    target = Path(args.out)
    role = artifact_role(args.role)
    data = client.artifact_file(args.artifact_set_id, role)
    replace_files(target.parent, {target.name: data})  # the stored file again: an earlier download is replaced
    _out({"artifact_set_id": args.artifact_set_id, "role": role, "out": str(target), "size_bytes": len(data)})
    return 0


# ------------------------------------------------------------------------------------------ feedback


def _read_details(source: str) -> str:
    if source == "-":
        return sys.stdin.read()
    try:
        return Path(source).read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise ValueError(f"--details-file {source} is not UTF-8 text") from None


def _active_catalog_version() -> str | None:
    """The active release of the local catalog store (no network); None when there is none or the store is unusable."""

    from .catalog import CatalogError, status

    try:
        active = status().active
    except (CatalogError, OSError, ValueError):
        return None
    return active.catalog_version if active is not None else None


def _feedback_submit(args: argparse.Namespace) -> int:
    details = args.details if args.details is not None else _read_details(args.details_file)
    context = {"catalog_version": _active_catalog_version(), "error_code": args.error_code,
               "request_ids": list(args.request_id or []), "resolution_id": args.resolution_id,
               "order_id": args.order_id, "command": args.command_text, "agent": args.agent, "locale": args.locale}
    _out(_client(args).submit_feedback(category=args.category, summary=args.summary, details=details,
                                       context={k: v for k, v in context.items() if v not in (None, [])}))
    return 0


# ------------------------------------------------------------------------------------------ registration


def _mounting_sheet_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--locale", choices=LOCALE_CHOICES, help="print (or write) the sheet as text in this language")
    parser.add_argument("--out", metavar="FILE", help="write the sheet to FILE (text with --locale, else JSON; an earlier "
                                                       "sheet there is replaced)")
    parser.add_argument("--overwrite", action="store_true", help=argparse.SUPPRESS)  # accepted; the file is always replaced


def _connection_options() -> argparse.ArgumentParser:
    common = ArgumentParser(add_help=False, allow_abbrev=False)
    common.add_argument("--api-url", help="the OpenSci service URL (default: OPENSCI_API_URL)")
    common.add_argument("--token-file", help="read the Account token from this file (default: OPENSCI_TOKEN, "
                                             "OPENSCI_TOKEN_FILE, then ~/.opensci/token)")
    return common


def register(sub: Any, *, assembly: Any) -> None:
    """Add the commercial commands to the ``os-optics`` parser: the ``account``, ``resolve``, ``resolve-parts``,
    ``products``, ``resolution``, ``order``, ``artifacts`` and ``feedback`` commands to ``sub`` and ``preview`` / ``drawing`` to the ``assembly``
    command's sub-commands. Each command sets ``handler``."""

    common = _connection_options()

    def leaf(parent: Any, name: str, work: Any, help_text: str, description: str, *,
             retired: str | None = None) -> argparse.ArgumentParser:
        parser = parent.add_parser(name, parents=[common], help=help_text, description=description,
                                   epilog=COMMERCE_EXIT_HELP, allow_abbrev=False)
        parser.set_defaults(handler=_guarded(_retired(work, retired) if retired else work))
        return parser

    p_account = sub.add_parser("account", help="your OpenSci Account on this device: token status, create, rotate, logout; shipping addresses",
                               description=ACCOUNT_DESCRIPTION, epilog=COMMERCE_EXIT_HELP)
    account_sub = p_account.add_subparsers(dest="account_command", required=True)
    leaf(account_sub, "status", _account_status, "the token in use: organization, scopes, expiry, what it allows",
         ACCOUNT_DESCRIPTION)
    p_token = account_sub.add_parser("token", help="create, list, revoke or rotate tokens", description=ACCOUNT_DESCRIPTION)
    token_sub = p_token.add_subparsers(dest="token_command", required=True)
    create = leaf(token_sub, "create", _token_create, "issue a token (needs account:token:manage)", ACCOUNT_DESCRIPTION)
    what = create.add_mutually_exclusive_group()
    what.add_argument("--profile", choices=PROFILE_CHOICES, help="agent (default): every customer commercial scope; "
                                                                 "review: resolve, preview, read Orders and artifacts")
    what.add_argument("--scopes", nargs="+", metavar="SCOPE", help="explicit scopes instead of a profile")
    create.add_argument("--lifecycle", choices=TOKEN_LIFECYCLES, default="time-limited",
                        help="single-use spends after the first new Order (reads remain); time-limited expires; "
                             "permanent has no expiry and remains revocable (requires a permanent issuer)")
    create.add_argument("--lifetime-hours", type=int, default=DEFAULT_LIFETIME_HOURS, help="1 to 720 (default 168); never beyond the "
                                                                        "issuing token's expiry")
    create.add_argument("--label", help="a name for the token (e.g. the Agent it is for)")
    create.add_argument("--save", nargs="?", const="", metavar="PATH",
                        help="write the new token to PATH (default ~/.opensci/token), readable only by you, instead of "
                             "printing it")
    create.add_argument("--overwrite", action="store_true", help="with --save: replace an existing file")
    p_address = account_sub.add_parser("address", help="list or add your organization's shipping addresses",
                                       description=ADDRESS_DESCRIPTION, epilog=COMMERCE_EXIT_HELP)
    address_sub = p_address.add_subparsers(dest="address_command", required=True)
    leaf(address_sub, "list", _address, "the shipping addresses, the default first", ADDRESS_DESCRIPTION)
    add = leaf(address_sub, "add", _address, "add a shipping address in mainland China", ADDRESS_DESCRIPTION)
    add.add_argument("--recipient", required=True, help="the recipient's name")
    add.add_argument("--phone", required=True, help="the recipient's mobile number (11 digits, mainland China)")
    add.add_argument("--province", required=True, help="province-level division in Chinese, e.g. 广东省 or 北京市")
    add.add_argument("--city", required=True, help="city, e.g. 深圳市")
    add.add_argument("--district", required=True, help="district or county, e.g. 南山区")
    add.add_argument("--street", required=True, help="street, building and room")
    add.add_argument("--postal-code", help="6-digit postal code (optional)")
    add.add_argument("--label", help="your name for the address, e.g. Lab (optional)")
    add.add_argument("--default", action="store_true", help="make it the default address")
    leaf(token_sub, "list", _token_list, "list your organization's tokens (non-secret records)", ACCOUNT_DESCRIPTION)
    revoke = leaf(token_sub, "revoke", _token_revoke, "revoke one token by its token id", ACCOUNT_DESCRIPTION)
    revoke.add_argument("token_id")
    rotate = leaf(token_sub, "rotate", _token_rotate, "replace the token in use by a new one with the same scopes",
                  ACCOUNT_DESCRIPTION)
    rotate.add_argument("--issuer-token-file", required=True,
                        help="a token file whose token carries account:token:manage; it issues the replacement")
    rotate.add_argument("--lifetime-hours", type=int, default=DEFAULT_LIFETIME_HOURS, help="lifetime of the new token, 1 to 720 (default 168)")
    leaf(account_sub, "logout", _logout, "revoke the token in use and delete its token file", ACCOUNT_DESCRIPTION)

    p_resolve = leaf(sub, "resolve", _resolve, "submit a run directory for commercial resolution (retired by the service; "
                     "use assembly plan + order quote)", RESOLVE_DESCRIPTION, retired="resolve")
    p_resolve.add_argument("run_dir", help="a directory written by 'os-optics project run --out-dir'")
    options = p_resolve.add_mutually_exclusive_group()
    options.add_argument("--optics-only", action="store_true",
                         help="buy only the optical elements of the design: no mounts, posts or breadboard; you mount "
                              "them yourself, guided by the relative Optical Layout Drawing")
    options.add_argument("--no-breadboard", action="store_true",
                         help="use your own optical table (M6 holes on a 25 mm grid) instead of the OpenSci breadboard")
    options.add_argument("--assembly-service", action="store_true",
                         help="OpenSci assembles and aligns the system on the OpenSci breadboard and delivers it assembled")
    p_resolve.add_argument("--max-board-size-mm", type=float, nargs=2, metavar=("X", "Y"),
                           help="largest acceptable board (with --no-breadboard: the free area on your table)")
    p_resolve.add_argument("--require-all-retained-dofs", action="store_true",
                           help="refuse a resolution that cannot provide every retained adjustment")
    p_resolve.add_argument("--customer-supplied", action="append", default=[], metavar="INSTANCE_ID",
                           help="a design component you supply yourself (e.g. an optic you already own); repeat for "
                                "several: it stays in the design but is not on the Customer Purchase BOM")
    p_resolve.add_argument("--spare", action="append", default=[], metavar="SKU=N", type=_spare,
                           help="also buy N spare units of an OpenSci optical product of the design; repeat for several")
    p_resolve.add_argument("--mechanics", choices=MECHANICS_CHOICES,
                           help="derived (default): OpenSci selects mount, post and post holder for each optic it "
                                "supplies; none: the optical elements only (as --optics-only)")
    p_resolve.add_argument("--platform", choices=PLATFORM_CHOICES,
                           help="breadboard (default): the OpenSci breadboard; none: your own optical table (as "
                                "--no-breadboard), or no layout at all for an optical-system design")
    p_resolve.add_argument("--assembly", choices=ASSEMBLY_CHOICES,
                           help="customer (default): you assemble; opensci: OpenSci assembles and aligns the system on "
                                "the OpenSci breadboard (as --assembly-service)")
    p_resolve.add_argument("--mounting", metavar="FILE",
                           help="an optical-system design bought with OpenSci mounts per optic and no platform: a JSON "
                                "file {INSTANCE_ID: {\"beam_height_mm\": H, \"incidence_deg\": A}} for every OpenSci "
                                "optic (implies --platform none)")

    p_parts = leaf(sub, "resolve-parts", _resolve_parts,
                   "price a part list (version-1 body; 'order quote' quotes a purchase BOM)", PARTS_DESCRIPTION,
                   retired="mechanics")
    p_parts.add_argument("parts", metavar="PARTS.json", help="the part list: lines, mechanical_supply, mounting")
    p_parts.add_argument("--mechanics", choices=MECHANICS_CHOICES,
                         help="replace the file's mechanical_supply: none (the listed parts only) or derived (OpenSci "
                              "selects mount, post and post holder for each optic at its stated beam height)")

    p_products = sub.add_parser("products", help="OpenSci's mechanical part numbers for a part list",
                                description=PRODUCTS_DESCRIPTION, epilog=COMMERCE_EXIT_HELP)
    products_sub = p_products.add_subparsers(dest="products_command", required=True)
    mechanical = leaf(products_sub, "mechanical", _products, "mounts, posts, post holders and breadboards with their facts",
                      PRODUCTS_DESCRIPTION)
    mechanical.add_argument("--kind", choices=MECHANICAL_KIND_CHOICES, help="only this kind")
    mechanical.add_argument("--json", action="store_true", help="print the service's JSON document")
    mechanical.add_argument("--locale", choices=LOCALE_CHOICES, default="en",
                            help="the language of the names in the one-line listing (default en)")

    p_resolution = sub.add_parser("resolution", help="read a commercial resolution", description=RESOLUTION_DESCRIPTION)
    resolution_sub = p_resolution.add_subparsers(dest="resolution_command", required=True)
    show = leaf(resolution_sub, "show", _resolution_show, "a resolution with its Customer Purchase BOM",
                RESOLUTION_DESCRIPTION)
    show.add_argument("resolution_id")
    show.add_argument("--locale", choices=LOCALE_CHOICES, help="the resolution presented for people in this language")
    from .cli_layout import FETCH_DESCRIPTION, add_output_options

    resolution_layout = leaf(resolution_sub, "layout", _resolution_layout,
                             "the Optical Layout Drawing of a resolution without OpenSci mechanics (retired by the service)",
                             RETIRED_NOTE + FETCH_DESCRIPTION + " " + CONNECTION_HELP, retired="layout")
    resolution_layout.add_argument("resolution_id")
    add_output_options(resolution_layout)
    resolution_sheet = leaf(resolution_sub, "mounting-sheet", _mounting_sheet,
                            "the mounting sheet of OpenSci mounts chosen per optic (retired by the service)",
                            MOUNTING_SHEET_DESCRIPTION, retired="mounting-sheet")
    resolution_sheet.add_argument("resolution_id")
    _mounting_sheet_options(resolution_sheet)

    p_order = sub.add_parser("order", help="create and read Orders; report the bank transfer; withdraw an unpaid Order",
                             description=ORDER_DESCRIPTION,
                             epilog=COMMERCE_EXIT_HELP)
    order_sub = p_order.add_subparsers(dest="order_command", required=True)
    quote = leaf(order_sub, "quote", _order_quote, "quote a purchase BOM with OpenSci's line review (confirm changed lines "
                 "with --confirm)", QUOTE_DESCRIPTION)
    quote.epilog = QUOTE_EXIT_HELP
    quote.add_argument("bom", metavar="BOM.json", help="the purchase BOM (assembly plan bom PLAN --purchase, or by hand)")
    quote.add_argument("--confirm", metavar="REVIEW.json",
                       help="send the proposed confirmation of this review file (as written by order quote for this "
                            "BOM, optionally with edited decisions); refused when the review belongs to another BOM")
    quote.add_argument("--review-out", metavar="FILE", help="write the review file here (default: BOM.review.json beside "
                                                            "the BOM; the review of a later confirmation round n: "
                                                            "BOM.review-n.json); never the BOM or the --confirm file")
    quote.add_argument("--schema-version", choices=BOM_SCHEMA_VERSION_CHOICES, default="2",
                       help="2 (default): the purchase BOM with its review; 1: the earlier part-list body for a service "
                            "before BOM-only ordering (no purpose, no confirmation)")
    quote.add_argument("--json", action="store_true", help="also print the service's whole answer (response)")
    order_create = leaf(order_sub, "create", _order, "create the Order of a resolution", ORDER_DESCRIPTION)
    order_create.add_argument("resolution_id")
    order_create.add_argument("--address", metavar="ADDRESS_ID",
                              help="ship to this address of 'account address list' (default: the default address)")
    order_create.add_argument("--additional-order", action="store_true",
                              help="the organization already has an Order of this design and the person wants to buy "
                                   "it again (otherwise refused with DESIGN_ALREADY_ORDERED)")
    order_show = leaf(order_sub, "show", _order, "an Order; with --locale presented for people", ORDER_DESCRIPTION)
    order_show.add_argument("order_id")
    order_show.add_argument("--locale", choices=LOCALE_CHOICES)
    order_show.add_argument("--document", choices=("order", "quotation", "contract"), default="order",
                            help="with --locale: the Order, its quotation or its agreement")
    leaf(order_sub, "list", _order, "your organization's Orders, newest first", ORDER_DESCRIPTION).add_argument(
        "--locale", choices=LOCALE_CHOICES)
    leaf(order_sub, "report-transfer", _order,
         "report that the corporate bank transfer was made (only after the person who paid says so)",
         ORDER_DESCRIPTION).add_argument("order_id")
    leaf(order_sub, "withdraw", _order,
         "withdraw an unpaid Order before any transfer is reported (only when the person asks); its data is deleted",
         ORDER_DESCRIPTION).add_argument("order_id")
    order_layout = leaf(order_sub, "layout", _order_layout,
                        "the Optical Layout Drawing of an Order without OpenSci mechanics (retired by the service)",
                        RETIRED_NOTE + FETCH_DESCRIPTION + " " + CONNECTION_HELP, retired="layout")
    order_layout.add_argument("order_id")
    add_output_options(order_layout)
    order_sheet = leaf(order_sub, "mounting-sheet", _mounting_sheet,
                       "the mounting sheet of an Order with OpenSci mounts chosen per optic (retired by the service)",
                       MOUNTING_SHEET_DESCRIPTION, retired="mounting-sheet")
    order_sheet.add_argument("order_id")
    _mounting_sheet_options(order_sheet)

    preview = leaf(assembly, "preview", _assembly_preview, "open a resolution's Experimental Lab Assembly (retired by the "
                   "service; use assembly plan preview)", PREVIEW_DESCRIPTION, retired="preview")
    preview.add_argument("resolution_id")
    preview.add_argument("--out", help="write the assembly check result (JSON) here")
    preview.add_argument("--view", action="store_true", help="open it in the local viewer")
    preview.add_argument("--snapshot", metavar="DIR", help="write PNG views (iso, top, side, front) into DIR")
    from .cli import add_tolerance_argument

    add_tolerance_argument(preview)
    drawing = leaf(assembly, "drawing", _assembly_drawing, "the Detailed Assembly Drawing of a resolution (retired by the "
                   "service; use assembly plan draw)", DRAWING_COMMAND_DESCRIPTION, retired="drawing")
    drawing.epilog = DRAWING_EXIT_HELP
    drawing.add_argument("resolution_id")
    drawing.add_argument("--out", required=True, help="output directory for the four drawing files")
    drawing.add_argument("--no-upload", action="store_true", help=argparse.SUPPRESS)  # deprecated: nothing is uploaded
    drawing.add_argument("--overwrite", action="store_true", help=argparse.SUPPRESS)  # accepted; the files are always replaced
    drawing.add_argument("--diagnostic", action="store_true",
                         help="draw a package whose local assembly check FAILED or could not be completed as a diagnostic "
                              "sheet that is not for delivery (without it such a package is refused, exit 65)")
    drawing.add_argument("--locale", choices=LOCALE_CHOICES, default="en",
                         help="the language of the sheet: en (default) or zh-CN (Simplified Chinese)")

    p_artifacts = sub.add_parser("artifacts", help="artifacts stored for a resolution (retired by the service)",
                                 description=ARTIFACTS_DESCRIPTION,
                                 epilog=COMMERCE_EXIT_HELP)
    artifacts_sub = p_artifacts.add_subparsers(dest="artifacts_command", required=True)
    listing = leaf(artifacts_sub, "list", _artifacts, "a resolution's artifact sets", ARTIFACTS_DESCRIPTION,
                   retired="artifacts")
    listing.add_argument("resolution_id")
    listing.add_argument("--kind", help="only this artifact kind (e.g. ASSEMBLY_DRAWING)")
    leaf(artifacts_sub, "show", _artifacts, "one artifact set", ARTIFACTS_DESCRIPTION,
         retired="artifacts").add_argument("artifact_set_id")
    download = leaf(artifacts_sub, "download", _artifacts, "download one file of an artifact set", ARTIFACTS_DESCRIPTION,
                    retired="artifacts")
    download.add_argument("artifact_set_id")
    download.add_argument("role", metavar="ROLE", help="the file's role, as 'artifacts show' lists it: " + ", ".join(ARTIFACT_ROLES)
                          + " (ASSEMBLY_DRAWING_PDF / ASSEMBLY_DRAWING_SVG are accepted for DRAWING_PDF / DRAWING_SVG)")
    download.add_argument("--out", required=True, help="write the file here")
    download.add_argument("--overwrite", action="store_true", help=argparse.SUPPRESS)  # accepted; the file is always replaced

    p_feedback = sub.add_parser("feedback", help="send feedback to OpenSci: a problem, a feature request, a documentation gap",
                                description=FEEDBACK_DESCRIPTION, epilog=COMMERCE_EXIT_HELP)
    feedback_sub = p_feedback.add_subparsers(dest="feedback_command", required=True)
    submit = leaf(feedback_sub, "submit", _feedback_submit, "send one problem or suggestion (any Account token)",
                  FEEDBACK_DESCRIPTION)
    submit.add_argument("--category", required=True, choices=FEEDBACK_CATEGORY_CHOICES,
                        help="PROBLEM: something did not work; FEATURE_REQUEST: something is missing; DOCUMENTATION: "
                             "the documentation was missing or wrong")
    submit.add_argument("--summary", required=True, help="one line, 5 to 200 characters: what went wrong / what is wanted")
    text = submit.add_mutually_exclusive_group(required=True)
    text.add_argument("--details", help="1 to 8000 characters: what the user needed, the commands you ran, what "
                                        "happened, what you expected")
    text.add_argument("--details-file", metavar="FILE", help="read the details from this UTF-8 file ('-': standard input)")
    submit.add_argument("--error-code", metavar="CODE", help="the error code you received (e.g. SHIPPING_ADDRESS_REQUIRED)")
    submit.add_argument("--request-id", nargs="+", action="extend", metavar="ID",
                        help="request_id of the failed request(s) (up to 10)")
    submit.add_argument("--resolution-id", metavar="ID", help="the resolution concerned")
    submit.add_argument("--order-id", metavar="ID", help="the Order concerned")
    submit.add_argument("--command", dest="command_text", metavar="TEXT",
                        help="the command you ran (up to 500 characters; leave out tokens and personal data)")
    submit.add_argument("--agent", metavar="NAME", help="your Agent product's name (default: OPENSCI_AGENT_NAME)")
    submit.add_argument("--locale", choices=LOCALE_CHOICES, help="the language the feedback is written in")


__all__ = ["COMMERCE_EXIT_HELP", "ArgumentParser", "exit_code_for", "register", "register_view"]
