"""The local assembly plan contract ``os-optics.assembly_plan`` 1.0 (an Engine contract).

A plan is the Engine's local, editable statement of how a Project is built on a breadboard: which optic sits in which
mount on which post in which holder on which board hole, who supplies each part, and what the customer buys. The same
representation holds a derived plan and a manually edited one; a candidate with findings is still a plan.

Plan directory (``PLAN_DIR``; written by ``directory.py``, read by every ``assembly plan`` command)::

    plan.json                     copy of the head revision (convenience, never authoritative)
    revisions/r0001.json ...      immutable revision documents, canonical osc1 bytes, 1-based and contiguous
    projects/<project_hash>.json  every Project a revision binds (content-addressed by its osc1 project hash)
    mechanical/<sha256>.json      USER mechanical documents a revision binds (copied when bound)
    runs/<record_hash>.json       the Local Validation record of every optics run a revision binds
    history.jsonl                 append-only journal (``HistoryLineV1`` per line); its last line names the head
    plan.lock                     exclusive-create lock file held for every write (pathlib only, no ``os``)

Revision (``AssemblyPlanRevisionV1``): the decisions (bindings, frame, items, links, placements, supply, purchase,
derive options, id counters) and, recorded beside them, the check made when the revision was written (findings, status,
checker versions). ``content_digest`` -- the "plan digest" -- is the osc1 digest of the decisions only
(``CONTENT_FIELDS``, dumped ``mode="json", by_alias=True, exclude_none=True``) after the number rule
(``normalize_numbers``: every float rounded to 6 decimals -- mm and degrees --, quaternion components and unit axis
vectors to 12, ``-0.0`` written ``0.0``). A stored revision must already be normalized, so its bytes and its digest agree;
writers build one with ``build_revision``. Findings never enter the digest: a checker upgrade never changes a plan's
identity.

Item ids are ``<prefix>-<n>`` (``ItemPrefixV1`` by role), ``n`` taken from ``id_counters`` + 1 when the item is created and
never renumbered or reused (retired ids are listed in ``retired_item_ids``). An item id is the preview package's
``part_ref`` of the part.

Interfaces the other lanes provide and this contract is written against (frozen signatures)
---------------------------------------------------------------------------------------------

E1 (``assembly_plan/mechanical/model.py``, ``catalog/mechanical.py`` through ``local/catalog.py``)::

    load_mechanical_catalog(data: bytes, *, publisher=None) -> PublicMechanicalCatalogV1
    resolve_sources(cli_files, environ, plan_binding=None) -> MechanicalSources
        MechanicalSources{documents, opensci, users, policy, binding() -> tuple[MechanicalBindingV1-shaped dict, ...]}

E2 (kernel port and derive)::

    derive.derive_plan_content(project, catalog, sources, options: DeriveOptionsV1, from_revision=None, run=None)
        -> DeriveOutcome{content: dict (CONTENT_FIELDS of a revision, ids allocated, before the number rule),
                         derive_findings: tuple[FindingV1, ...] (source DERIVE), project}
    chain.place_single_chain(optic_item, pose_lab, kcatalog, board, occupied_holes, pins) -> ChainPlacement
    chain.replace_chain(...) -> ChainPlacement        (re-poses existing hardware without re-selection)
    kernel.assemble.part_datums(role, view) -> dict[str, Pose]
    kernel.assemble.chain_bodies(...) -> dict[ItemRoleV1, tuple[YawBox, ...]]
    optics.mechanics_of(product) -> OpticMechanicsV1 | MissingMechanics
    kernel.resolve.mount_incompatibility(...), stack helpers (re-exported by ``kernel``)

E4 (local outputs)::

    projection.project_plan_package(revision: AssemblyPlanRevisionV1, context) -> AssemblyPreviewPackageV1
        (part_ref = item_id; refuses USER_MECH plans with PLAN_USER_HARDWARE_NOT_PROJECTABLE)
    detail.project_plan_detail(package, revision, context) -> AssemblyDetailPackageV1
    drawing.plan_drawing(plan_or_dir, out_dir, *, locale="en", diagnostic=False, revision=None) -> DrawingOutcome
    viewer.view_plan(...)
    viewer.assembly.assembly_view_scene(package, *, validation=None, findings=None, focus_items=None)

E3-b (this lane's second half) builds on this module: ``directory.py`` (write protocol, ``HistoryLineV1``),
``ops.py`` (``AssemblyPlanOpV1`` / ``EditRequestV1`` -> ``PlanCallResultV1``), ``check.py`` (``findings.py`` builders ->
``PlanStatusV1``) and ``bom.py``.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, Field, StrictBool, StrictFloat, StrictInt, StringConstraints, model_validator

from .._vendor.shared_contracts.assembly_preview.scene import (DatumKey, DisplayText, DofMechanismV1, InterfaceDesignation,
                                                                SettingKindV1, SettingValue, SupportRelationV1)
from .._vendor.shared_contracts.assembly_preview.validation import AssemblyIssueCodeV1, AssemblyValidationStatusV1
from .._vendor.shared_contracts.base import StrictContractModel
from .._vendor.shared_contracts.common.geometry import PoseV1
from .._vendor.shared_contracts.common.hashes import (HashProfile, HashRef, Sha256Digest, canonicalize_osc1_bytes,
                                                      osc1_digest)
from .._vendor.shared_contracts.common.identifiers import BuildVersion, ComponentKey, InstanceKey
from .._vendor.shared_contracts.common.text import check_author_text
from .._vendor.shared_contracts.design_catalog.optical_catalog import CatalogVersion
from .._vendor.shared_contracts.optical_design.dof import DOF_AXIS_ORDER, DofAxisV1, DofControlIntentV1
from .._vendor.shared_contracts.optical_design.optical_model import DesignIntentV1
from .._vendor.shared_contracts.optical_design.workspace import OperationId

# ------------------------------------------------------------------------------------------------ constants

PLAN_SCHEMA_ID: Literal["os-optics.assembly_plan"] = "os-optics.assembly_plan"
PLAN_SCHEMA_VERSION: Literal["1.0"] = "1.0"
#: The plan schema versions this Engine reads.
SUPPORTED_PLAN_SCHEMA_VERSIONS = ("1.0",)

#: Plan directory entries (1.3.1).
PLAN_FILE = "plan.json"
REVISIONS_DIR = "revisions"
PROJECTS_DIR = "projects"
MECHANICAL_DIR = "mechanical"
#: The Local Validation records of the runs a revision binds (``runs/<record hash>.json``), so freshness is recomputed offline.
RUNS_DIR = "runs"
HISTORY_FILE = "history.jsonl"
LOCK_FILE = "plan.lock"
#: ``revisions/r<NNNN>.json``: at least this many digits, zero-padded.
REVISION_FILE_DIGITS = 4
_REVISION_FILE = re.compile(r"^r([0-9]{4,})\.json$")

#: Bounds of a revision.
MAX_ITEMS = 512
MAX_LINKS = 2048
MAX_MECHANICAL_BINDINGS = 9
MAX_RETIRED_ITEMS = 65536
MAX_FINDINGS = 4096
MAX_RANGES_PER_ITEM = 16
MAX_SETTINGS_PER_ITEM = 16
MAX_BOARD_HOLES = 64
MAX_CUSTOMER_OWNED = 512
#: Bounds of a finding (1.3.6).
FINDING_MESSAGE_MAX = 400
FINDING_DETAILS_MAX_BYTES = 4096
FINDING_DETAILS_MAX_DEPTH = 4
FINDING_ID_DIGITS = 12
MAX_MISSING_DATA = 64
#: ``plan_id`` of a derived plan: ``ap-`` + this many hex digits of the derive-input digest.
PLAN_ID_PREFIX = "ap-"
PLAN_ID_DIGEST_DIGITS = 16
#: Bounds of an edit call (1.3.8).
MAX_OPS_PER_EDIT = 256
MAX_KEEP_ITEMS = 3
#: Number rule before hashing (1.3.2): decimals of lengths (mm) and angles (deg), and of quaternion / unit-axis components.
LENGTH_DECIMALS = 6
ANGLE_DECIMALS = 6
UNIT_COMPONENT_DECIMALS = 12
#: Keys whose numbers are unit-quaternion or unit-vector components (rounded to ``UNIT_COMPONENT_DECIMALS``).
UNIT_COMPONENT_KEYS = frozenset({"quaternion_wxyz", "axis_lab"})
#: A declared axis is a unit vector within this tolerance (of its rounded components).
UNIT_AXIS_TOLERANCE = 1e-9
#: The identity a customer optical table takes in a plan.
CUSTOMER_TABLE_SKU = "OS-CUSTOMER-OPTICAL-TABLE"
#: Short display text (zh-CN names) of an item identity.
DISPLAY_TEXT_MAX = 160
#: Lengths of the plan's identifiers and designations.
ITEM_ID_MAX = 64
LINK_ID_MAX = 160
RANGE_ID_MAX = 128
SKU_MAX = 120
THREAD_MAX = 40
MECHANICAL_VERSION_MAX = 64
MISSING_DATA_KEY_MAX = 200

# ------------------------------------------------------------------------------------------------ scalar types

PlanId = Annotated[
    str,
    StringConstraints(strict=True, pattern=r"^[a-z0-9][a-z0-9-]{2,63}$"),
    Field(description="Plan id: 3 to 64 lowercase letters, digits or '-' (derived: 'ap-' + 16 hex digits)."),
]
ItemId = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=ITEM_ID_MAX, pattern=r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$"),
    Field(description="Stable item id '<prefix>-<n>' (the preview part_ref of the part); never renumbered or reused."),
]
LinkId = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=LINK_ID_MAX, pattern=r"^lnk-[a-z][a-z0-9]*(-[a-z0-9]+)*$"),
    Field(description="'lnk-' + carried item id + '-' + carrier item id."),
]
RangeId = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=RANGE_ID_MAX, pattern=r"^rng-[a-z][a-z0-9]*(-[a-z0-9]+)*$"),
    Field(description="'rng-' + optic item id + '-' + the range kind in lowercase with '-'."),
]
FindingId = Annotated[
    str,
    StringConstraints(strict=True, pattern=r"^f-[0-9a-f]{12}$"),
    Field(description="'f-' + 12 hex digits of the osc1 digest of {code, item_ids, interface, details_key}."),
]
OsOptSku = Annotated[str, StringConstraints(strict=True, min_length=4, max_length=SKU_MAX, pattern=r"^OS-OPT(-[A-Z0-9]+)+$")]
OsMechSku = Annotated[str, StringConstraints(strict=True, min_length=4, max_length=SKU_MAX, pattern=r"^OS-MECH(-[A-Z0-9]+)+$")]
HardwareSku = Annotated[
    str,
    StringConstraints(strict=True, min_length=4, max_length=SKU_MAX, pattern=r"^(OS|USER)-MECH(-[A-Z0-9]+)+$"),
    Field(description="An OpenSci (OS-MECH-*) or user-declared (USER-MECH-*) mechanical identity."),
]
Thread = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=THREAD_MAX, pattern=r"^[A-Za-z0-9./\-x]+$"),
    Field(description="Thread designation, e.g. 'M4', 'M6', '8-32', '1/4-20'."),
]
MechanicalVersion = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=MECHANICAL_VERSION_MAX, pattern=r"^(m[1-9][0-9]*\.[0-9a-f]{12}|[a-z0-9][a-z0-9._-]{0,63})$"),
    Field(description="OPENSCI: 'm<revision>.<12 hex>'; USER: 1 to 64 lowercase letters, digits, '.', '_' or '-'."),
]
_OPENSCI_MECHANICAL_VERSION = re.compile(r"^m[1-9][0-9]*\.[0-9a-f]{12}$")
_USER_MECHANICAL_VERSION = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
ZhCnText = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=DISPLAY_TEXT_MAX),
    AfterValidator(check_author_text),
    Field(description="Simplified Chinese display text."),
]
MissingDataKey = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=MISSING_DATA_KEY_MAX, pattern=r"^(OS|USER)(-[A-Z0-9]+)+:[a-z][A-Za-z0-9_.]*$"),
    Field(description="'<sku>:<ParamKey>' of a mechanical identity, or '<OS-OPT sku>:mechanics' / ':design_geometry'."),
]
Mm = StrictFloat
Deg = StrictFloat
Vec3 = tuple[StrictFloat, StrictFloat, StrictFloat]
Quaternion = tuple[StrictFloat, StrictFloat, StrictFloat, StrictFloat]
NonNegativeInt = Annotated[StrictInt, Field(ge=0)]
PositiveInt = Annotated[StrictInt, Field(ge=1)]
PositiveMm = Annotated[StrictFloat, Field(gt=0)]
NonNegativeMm = Annotated[StrictFloat, Field(ge=0)]
BoardHole = tuple[NonNegativeInt, NonNegativeInt]


# ------------------------------------------------------------------------------------------------ enums


class PlanOriginV1(StrEnum):
    DERIVE = "DERIVE"  # revision 1 of a new plan directory
    REDERIVE = "REDERIVE"  # derive --from an earlier plan
    EDIT = "EDIT"
    REVERT = "REVERT"
    REBIND = "REBIND"


class ItemRoleV1(StrEnum):
    OPTIC = "OPTIC"  # a catalog optic of the Project (design instance)
    COMPONENT = "COMPONENT"  # any other Project component (customer part / light source): an anchor
    MOUNT = "MOUNT"
    POST = "POST"
    POST_HOLDER = "POST_HOLDER"
    BREADBOARD = "BREADBOARD"
    DECLARED_ENVELOPE = "DECLARED_ENVELOPE"  # a customer part known only by its envelope (declare_envelope)


class ItemPrefixV1(StrEnum):
    OPT = "opt"
    CMP = "cmp"
    MNT = "mnt"
    PST = "pst"
    HLD = "hld"
    BRD = "brd"
    ENV = "env"


#: The id prefix of each role.
ROLE_PREFIX: dict[ItemRoleV1, ItemPrefixV1] = {
    ItemRoleV1.OPTIC: ItemPrefixV1.OPT,
    ItemRoleV1.COMPONENT: ItemPrefixV1.CMP,
    ItemRoleV1.MOUNT: ItemPrefixV1.MNT,
    ItemRoleV1.POST: ItemPrefixV1.PST,
    ItemRoleV1.POST_HOLDER: ItemPrefixV1.HLD,
    ItemRoleV1.BREADBOARD: ItemPrefixV1.BRD,
    ItemRoleV1.DECLARED_ENVELOPE: ItemPrefixV1.ENV,
}
_ALLOCATED_ID = re.compile(r"^(" + "|".join(p.value for p in ItemPrefixV1) + r")-([1-9][0-9]*)$")
CHAIN_HARDWARE_ROLES = frozenset({ItemRoleV1.MOUNT, ItemRoleV1.POST, ItemRoleV1.POST_HOLDER})
DESIGN_INSTANCE_ROLES = frozenset({ItemRoleV1.OPTIC, ItemRoleV1.COMPONENT})


class ItemIdentityKindV1(StrEnum):
    OS_OPT = "OS_OPT"  # a public optical catalog product
    OS_MECH = "OS_MECH"  # an OpenSci mechanical identity of the bound OPENSCI mechanical document
    USER_MECH = "USER_MECH"  # a user-declared mechanical identity of a bound USER document
    CUSTOMER_TABLE = "CUSTOMER_TABLE"  # the customer's own optical table (board only)
    CUSTOMER_PART = "CUSTOMER_PART"  # a customer part: a Project custom part, light source or declared envelope
    NONE = "NONE"  # chain hardware no compatible identity was found for


class SupplyV1(StrEnum):
    OPENSCI = "OPENSCI"
    CUSTOMER_OWNED = "CUSTOMER_OWNED"
    UNRESOLVED = "UNRESOLVED"


class SupplyReasonV1(StrEnum):
    """Why an item is not (simply) supplied by OpenSci. The first five are ``CustomerSupplyReasonV1`` values;
    ``TEMPORARILY_UNAVAILABLE`` is never set locally (only the server knows current supply: ``bom_review``)."""

    CUSTOMER_LIGHT_SOURCE = "CUSTOMER_LIGHT_SOURCE"
    NOT_IN_CATALOG = "NOT_IN_CATALOG"
    NOT_ORDERABLE = "NOT_ORDERABLE"
    CUSTOMER_OWNED = "CUSTOMER_OWNED"
    OPENSCI_SUPPLIED_UNMOUNTED = "OPENSCI_SUPPLIED_UNMOUNTED"
    NO_COMPATIBLE_HARDWARE = "NO_COMPATIBLE_HARDWARE"
    USER_HARDWARE = "USER_HARDWARE"
    CUSTOMER_TABLE = "CUSTOMER_TABLE"


#: The reasons each supply admits (``None`` = no reason).
SUPPLY_REASONS: dict[SupplyV1, frozenset[SupplyReasonV1 | None]] = {
    SupplyV1.OPENSCI: frozenset({None, SupplyReasonV1.OPENSCI_SUPPLIED_UNMOUNTED}),
    SupplyV1.CUSTOMER_OWNED: frozenset({SupplyReasonV1.CUSTOMER_LIGHT_SOURCE, SupplyReasonV1.NOT_IN_CATALOG,
                                        SupplyReasonV1.NOT_ORDERABLE, SupplyReasonV1.CUSTOMER_OWNED,
                                        SupplyReasonV1.USER_HARDWARE, SupplyReasonV1.CUSTOMER_TABLE}),
    SupplyV1.UNRESOLVED: frozenset({SupplyReasonV1.NOT_ORDERABLE, SupplyReasonV1.NOT_IN_CATALOG,
                                    SupplyReasonV1.NO_COMPATIBLE_HARDWARE}),
}


class MountingV1(StrEnum):
    CHAIN = "CHAIN"  # mount + post + holder on the board
    ANCHOR = "ANCHOR"  # placed at its design pose, mounted by the customer (reserved envelope)


class MechanicalPublisherV1(StrEnum):
    OPENSCI = "OPENSCI"
    USER = "USER"


class FrameBasisV1(StrEnum):
    DERIVED = "DERIVED"
    EDITED = "EDITED"


class ChainLinkKindV1(StrEnum):
    OPTIC_IN_MOUNT = "OPTIC_IN_MOUNT"
    MOUNT_ON_POST = "MOUNT_ON_POST"
    POST_IN_HOLDER = "POST_IN_HOLDER"
    HOLDER_ON_BOARD = "HOLDER_ON_BOARD"


#: (carried role, carrier role, support relation of the carried item) of each link kind, in chain order.
LINK_KINDS: dict[ChainLinkKindV1, tuple[ItemRoleV1, ItemRoleV1, SupportRelationV1]] = {
    ChainLinkKindV1.OPTIC_IN_MOUNT: (ItemRoleV1.OPTIC, ItemRoleV1.MOUNT, SupportRelationV1.SEATED_IN),
    ChainLinkKindV1.MOUNT_ON_POST: (ItemRoleV1.MOUNT, ItemRoleV1.POST, SupportRelationV1.MOUNTED_ON),
    ChainLinkKindV1.POST_IN_HOLDER: (ItemRoleV1.POST, ItemRoleV1.POST_HOLDER, SupportRelationV1.INSERTED_INTO),
    ChainLinkKindV1.HOLDER_ON_BOARD: (ItemRoleV1.POST_HOLDER, ItemRoleV1.BREADBOARD, SupportRelationV1.STANDS_ON),
}


class FastenerKindV1(StrEnum):
    CAP_SCREW_THROUGH_BASE = "CAP_SCREW_THROUGH_BASE"
    STUD_INTO_TAPPED_FACE = "STUD_INTO_TAPPED_FACE"


class SettingUnitV1(StrEnum):
    MM = "mm"
    DEG = "deg"
    NONE = "NONE"


class PlanFactBasisV1(StrEnum):
    """Fact basis of a placement setting or a declared range."""

    RESOLVED = "RESOLVED"
    PREVIEW_ASSUMPTION = "PREVIEW_ASSUMPTION"


class RangeKindV1(StrEnum):
    POST_HEIGHT = "POST_HEIGHT"  # mm along lab +Z (holder: insertion interval within the post length)
    POST_ROTATION = "POST_ROTATION"  # deg about the post axis
    SLOT_TRAVEL = "SLOT_TRAVEL"  # mm along the fork slot
    MOUNT_TIP = "MOUNT_TIP"  # deg
    MOUNT_TILT = "MOUNT_TILT"  # deg
    MOUNT_TRANSLATION_Y = "MOUNT_TRANSLATION_Y"  # mm
    MOUNT_TRANSLATION_Z = "MOUNT_TRANSLATION_Z"  # mm
    MOUNT_AXIAL_ROTATION = "MOUNT_AXIAL_ROTATION"  # deg


#: Range kinds that rotate (unit deg, a pivot); the others translate (unit mm, no pivot).
ROTATION_RANGE_KINDS = frozenset({RangeKindV1.POST_ROTATION, RangeKindV1.MOUNT_TIP, RangeKindV1.MOUNT_TILT,
                                  RangeKindV1.MOUNT_AXIAL_ROTATION})


class RangeUnitV1(StrEnum):
    MM = "mm"
    DEG = "deg"


class AdjustmentV1(StrEnum):
    FINE_ADJUSTER = "FINE_ADJUSTER"
    CONTINUOUS_CLAMPED = "CONTINUOUS_CLAMPED"
    COARSE_REPOSITION = "COARSE_REPOSITION"


class DofResultStatusV1(StrEnum):
    DELIVERED = "DELIVERED"
    DELIVERED_AS_MANUAL_FALLBACK = "DELIVERED_AS_MANUAL_FALLBACK"
    NOT_DELIVERED = "NOT_DELIVERED"


class DofNotDeliveredReasonV1(StrEnum):
    NO_COMPATIBLE_HARDWARE = "NO_COMPATIBLE_HARDWARE"
    HARDWARE_DID_NOT_FIT = "HARDWARE_DID_NOT_FIT"
    SLOT_NOT_ALONG_AXIS = "SLOT_NOT_ALONG_AXIS"


class EnvelopeBasisV1(StrEnum):
    CATALOG_DESIGN_GEOMETRY = "CATALOG_DESIGN_GEOMETRY"  # the public catalog's design geometry of an OpenSci product
    PROJECT_DECLARED_GEOMETRY = "PROJECT_DECLARED_GEOMETRY"  # the optical model the Project declares (scene spelling)
    USER_DECLARED = "USER_DECLARED"  # declare_envelope: the customer stated the envelope


class FindingSeverityV1(StrEnum):
    ERROR = "ERROR"
    UNKNOWN = "UNKNOWN"
    WARNING = "WARNING"
    INFO = "INFO"


#: Sort and status rank of a severity (lower = worse).
SEVERITY_RANK: dict[FindingSeverityV1, int] = {FindingSeverityV1.ERROR: 0, FindingSeverityV1.UNKNOWN: 1,
                                               FindingSeverityV1.WARNING: 2, FindingSeverityV1.INFO: 3}


class FindingCheckV1(StrEnum):
    PLAN_INTERFACES = "PLAN_INTERFACES"
    PLAN_LAYOUT = "PLAN_LAYOUT"
    PLAN_SUPPLY = "PLAN_SUPPLY"
    PLAN_OPTICS = "PLAN_OPTICS"
    DERIVE = "DERIVE"
    LAYOUT_VALIDATION = "LAYOUT_VALIDATION"  # an AssemblyIssueV1 of validate_assembly_layout, code unchanged


class FindingBasisV1(StrEnum):
    FACT = "FACT"
    PREVIEW_ASSUMPTION = "PREVIEW_ASSUMPTION"
    CONSERVATIVE_PROFILE = "CONSERVATIVE_PROFILE"
    NOT_EVALUATED = "NOT_EVALUATED"


class FindingSourceV1(StrEnum):
    DERIVE = "DERIVE"  # what only the search knew: copied forward until an op touches one of its items
    CHECK = "CHECK"  # recomputed by every check


class PlanFindingCodeV1(StrEnum):
    """The plan's own finding codes (``findings.FINDING_RULES``). Layout-validation findings keep their
    ``AssemblyIssueCodeV1`` code; ``RESERVED_SPACE_INTRUSION`` is also the code of the plan-level declared-envelope check."""

    UNSUPPORTED_OPTIC_ORIENTATION = "UNSUPPORTED_OPTIC_ORIENTATION"
    MOUNT_NOT_FOUND = "MOUNT_NOT_FOUND"
    MISSING_MECHANICAL_DATA = "MISSING_MECHANICAL_DATA"
    HEIGHT_NOT_REACHABLE = "HEIGHT_NOT_REACHABLE"
    NO_COMMON_BEAM_HEIGHT = "NO_COMMON_BEAM_HEIGHT"
    BOARD_TOO_SMALL = "BOARD_TOO_SMALL"
    SUPPORTS_COLLIDE = "SUPPORTS_COLLIDE"
    BEAM_CLEARANCE = "BEAM_CLEARANCE"
    NO_FIXING_HOLE = "NO_FIXING_HOLE"
    BEAM_CLIPPED_AT_INCIDENCE = "BEAM_CLIPPED_AT_INCIDENCE"
    OBLIQUE_CLEARANCE_UNVERIFIED = "OBLIQUE_CLEARANCE_UNVERIFIED"
    OPTIC_TILT_NOT_SUPPORTED = "OPTIC_TILT_NOT_SUPPORTED"
    DOF_NOT_DELIVERED = "DOF_NOT_DELIVERED"
    DOF_MANUAL_FALLBACK = "DOF_MANUAL_FALLBACK"
    SEARCH_BUDGET_EXHAUSTED = "SEARCH_BUDGET_EXHAUSTED"
    DERIVE_FELL_BACK = "DERIVE_FELL_BACK"
    INTERFACE_MISMATCH = "INTERFACE_MISMATCH"
    INSERTION_OUT_OF_RANGE = "INSERTION_OUT_OF_RANGE"
    HEIGHT_CLOSURE_MISMATCH = "HEIGHT_CLOSURE_MISMATCH"
    RANGE_EXCEEDED = "RANGE_EXCEEDED"
    FIXATION_HOLE_INVALID = "FIXATION_HOLE_INVALID"
    IDENTITY_NOT_IN_CATALOG = "IDENTITY_NOT_IN_CATALOG"
    FASTENING_VARIES = "FASTENING_VARIES"
    PLAN_PROJECT_MISMATCH = "PLAN_PROJECT_MISMATCH"
    OPTICS_RESULTS_STALE = "OPTICS_RESULTS_STALE"
    OPENSCI_SUPPLIED_UNMOUNTED = "OPENSCI_SUPPLIED_UNMOUNTED"
    UNRESOLVED_SUPPLY = "UNRESOLVED_SUPPLY"
    CATALOG_BINDING_CHANGED = "CATALOG_BINDING_CHANGED"
    LAYOUT_NOT_EVALUATED = "LAYOUT_NOT_EVALUATED"


FindingCode = PlanFindingCodeV1 | AssemblyIssueCodeV1


class PlanStateV1(StrEnum):
    READY = "READY"
    READY_WITH_WARNINGS = "READY_WITH_WARNINGS"
    INCOMPLETE = "INCOMPLETE"
    HAS_ERRORS = "HAS_ERRORS"


class OpticsResultsStateV1(StrEnum):
    CURRENT = "CURRENT"
    STALE = "STALE"
    UNDETERMINED = "UNDETERMINED"
    NOT_RUN = "NOT_RUN"


# ------------------------------------------------------------------------------------------------ number rule and digest


def normalize_numbers(value: Any, key: str | None = None) -> Any:
    """``value`` (a JSON payload) with the plan's number rule applied: floats under a ``UNIT_COMPONENT_KEYS`` key rounded
    to ``UNIT_COMPONENT_DECIMALS``, every other float to ``LENGTH_DECIMALS`` (lengths in mm, angles in deg alike), ``-0.0``
    written ``0.0``. Integers, booleans and strings are unchanged; tuples become lists."""

    if isinstance(value, dict):
        return {k: normalize_numbers(v, k) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalize_numbers(v, key) for v in value]
    if isinstance(value, float):
        rounded = round(value, UNIT_COMPONENT_DECIMALS if key in UNIT_COMPONENT_KEYS else LENGTH_DECIMALS)
        return 0.0 if rounded == 0.0 else rounded
    return value


#: The revision fields the plan digest covers (the decisions), in document order.
CONTENT_FIELDS = ("schema", "schema_version", "plan_id", "revision", "parent_digest", "origin", "bindings", "frame",
                  "board_item_id", "items", "links", "id_counters", "retired_item_ids", "derive_options")
#: The recorded check of a revision (never in the digest).
CHECK_FIELDS = ("findings", "status", "checked_with")


def content_payload(document: dict[str, Any]) -> dict[str, Any]:
    """The decision fields of a revision payload (``CONTENT_FIELDS`` present in ``document``)."""

    return {k: document[k] for k in CONTENT_FIELDS if k in document}


def plan_content_digest(content: dict[str, Any]) -> str:
    """The plan digest: osc1 digest of the normalized decision fields of ``content``."""

    return osc1_digest(normalize_numbers(content_payload(content)))


def derived_plan_id(project_hash: str, optical_catalog_version: str, mechanical: list[dict[str, Any]],
                    options: dict[str, Any]) -> str:
    """The ``plan_id`` ``derive`` gives a new plan without ``--plan-id``."""

    digest = osc1_digest({"project_hash": project_hash, "optical": optical_catalog_version, "mechanical": mechanical,
                          "options": normalize_numbers(options)})
    return PLAN_ID_PREFIX + digest[:PLAN_ID_DIGEST_DIGITS]


def revision_file_name(revision: int) -> str:
    """``r0001.json`` for revision 1 (at least ``REVISION_FILE_DIGITS`` digits)."""

    if revision < 1:
        raise ValueError("revision numbers start at 1")
    return "r" + str(revision).zfill(REVISION_FILE_DIGITS) + ".json"


def parse_revision_file_name(name: str) -> int | None:
    """The revision number of a revision file name, or None when ``name`` is not one (``r0000.json`` and non-canonical
    paddings such as ``r00001.json`` for revision 1 are not)."""

    match = _REVISION_FILE.fullmatch(name)
    if match is None:
        return None
    number = int(match.group(1))
    return number if number >= 1 and revision_file_name(number) == name else None


def item_id_parts(item_id: str) -> tuple[ItemPrefixV1, int] | None:
    """``(prefix, n)`` of an allocated item id ``<prefix>-<n>``, or None."""

    match = _ALLOCATED_ID.fullmatch(item_id)
    return None if match is None else (ItemPrefixV1(match.group(1)), int(match.group(2)))


def _dump(model: StrictContractModel) -> dict[str, Any]:
    return model.model_dump(mode="json", by_alias=True, exclude_none=True)


def _sorted_unique(values: tuple[Any, ...]) -> bool:
    return list(values) == sorted(set(values))


# ------------------------------------------------------------------------------------------------ bindings / frame / options


class ProjectBindingV1(StrictContractModel):
    project_hash: HashRef = Field(description="osc1 hash of the bound Project (subject 'project').")
    design_intent: DesignIntentV1

    @model_validator(mode="after")
    def _ref(self) -> ProjectBindingV1:
        _require_osc1(self.project_hash, "project", "bindings.project.project_hash")
        return self


class OpticalCatalogBindingV1(StrictContractModel):
    catalog_version: CatalogVersion = Field(description="The optical catalog release the Project pins / was checked with.")


class MechanicalBindingV1(StrictContractModel):
    publisher: MechanicalPublisherV1
    mechanical_version: MechanicalVersion
    sha256: Sha256Digest = Field(description="SHA-256 of the mechanical document bytes as distributed / given.")

    @model_validator(mode="after")
    def _version(self) -> MechanicalBindingV1:
        pattern = _OPENSCI_MECHANICAL_VERSION if self.publisher is MechanicalPublisherV1.OPENSCI else _USER_MECHANICAL_VERSION
        if pattern.fullmatch(self.mechanical_version) is None:
            raise ValueError(f"mechanical_version {self.mechanical_version!r} is not a {self.publisher.value} version")
        return self


class OpticsRunBindingV1(StrictContractModel):
    local_validation_hash: HashRef = Field(description="osc1 hash of the run's Local Validation Record.")
    project_hash: HashRef = Field(description="osc1 hash of the Project the run validated.")

    @model_validator(mode="after")
    def _refs(self) -> OpticsRunBindingV1:
        _require_osc1(self.local_validation_hash, "local_validation_record", "bindings.optics_run.local_validation_hash")
        _require_osc1(self.project_hash, "project", "bindings.optics_run.project_hash")
        return self


class PlanBindingsV1(StrictContractModel):
    project: ProjectBindingV1
    optical_catalog: OpticalCatalogBindingV1
    mechanical: tuple[MechanicalBindingV1, ...] = Field(min_length=1, max_length=MAX_MECHANICAL_BINDINGS)
    optics_run: OpticsRunBindingV1 | None = Field(
        default=None, description="The run the plan was derived / rechecked against (freshness is recomputed, not stored).")

    @model_validator(mode="after")
    def _order(self) -> PlanBindingsV1:
        opensci = [b for b in self.mechanical if b.publisher is MechanicalPublisherV1.OPENSCI]
        if len(opensci) > 1:
            raise ValueError("a plan binds at most one OPENSCI mechanical document")
        users = [b for b in self.mechanical if b.publisher is MechanicalPublisherV1.USER]
        if list(self.mechanical) != opensci + sorted(users, key=lambda b: b.sha256):
            raise ValueError("mechanical bindings: the OPENSCI document first, then the USER documents by sha256")
        if len({b.sha256 for b in self.mechanical}) != len(self.mechanical):
            raise ValueError("mechanical bindings: each document (sha256) once")
        return self


class PlanFrameV1(StrictContractModel):
    """Design -> lab: ``p_lab = Rz(yaw_deg) p_design + translation_mm``; ``translation_mm[2]`` is the lab height of
    design z = 0."""

    yaw_deg: Deg
    translation_mm: Vec3
    basis: FrameBasisV1


class CustomerTableV1(StrictContractModel):
    size_x_mm: PositiveMm
    size_y_mm: PositiveMm
    pitch_mm: PositiveMm
    edge_offset_mm: NonNegativeMm
    thread: Thread

    @model_validator(mode="after")
    def _grid(self) -> CustomerTableV1:
        if 2.0 * self.edge_offset_mm >= min(self.size_x_mm, self.size_y_mm):
            raise ValueError("customer table: the edge offset leaves no hole grid")
        return self


class DeriveBudgetV1(StrictContractModel):
    max_chain_placements: PositiveInt
    max_geometry_checks: PositiveInt
    time_limit_s: Annotated[StrictFloat, Field(gt=0)] | None = None


class DeriveOptionsV1(StrictContractModel):
    max_board_size_mm: tuple[PositiveMm, PositiveMm] | None = None
    beam_height_mm: Mm | None = Field(default=None, description="Requested lab height of design z = 0.")
    customer_table: CustomerTableV1 | None = None
    customer_owned: tuple[InstanceKey, ...] = Field(default=(), max_length=MAX_CUSTOMER_OWNED,
                                                    description="Design instances the customer supplies (sorted).")
    budget: DeriveBudgetV1

    @model_validator(mode="after")
    def _sorted(self) -> DeriveOptionsV1:
        if not _sorted_unique(self.customer_owned):
            raise ValueError("derive_options.customer_owned must be unique and sorted")
        return self


# ------------------------------------------------------------------------------------------------ items


class ItemIdentityV1(StrictContractModel):
    kind: ItemIdentityKindV1
    opensci_sku: str | None = Field(default=None, description="OS_OPT: OS-OPT-*; OS_MECH: OS-MECH-*; USER_MECH: USER-MECH-*; "
                                                              "CUSTOMER_TABLE: OS-CUSTOMER-OPTICAL-TABLE; else none.")
    display_name: DisplayText = Field(description="Copied from the catalog when bound (display only).")
    display_name_zh_cn: ZhCnText | None = None
    custom_part_key: ComponentKey | None = Field(default=None, description="CUSTOMER_PART only: the Project's custom_part key.")

    @model_validator(mode="after")
    def _sku(self) -> ItemIdentityV1:
        patterns = {ItemIdentityKindV1.OS_OPT: r"^OS-OPT(-[A-Z0-9]+)+$", ItemIdentityKindV1.OS_MECH: r"^OS-MECH(-[A-Z0-9]+)+$",
                    ItemIdentityKindV1.USER_MECH: r"^USER-MECH(-[A-Z0-9]+)+$",
                    ItemIdentityKindV1.CUSTOMER_TABLE: "^" + re.escape(CUSTOMER_TABLE_SKU) + "$"}
        pattern = patterns.get(self.kind)
        if pattern is None:
            if self.opensci_sku is not None:
                raise ValueError(f"identity {self.kind.value} has no opensci_sku")
        elif self.opensci_sku is None or re.fullmatch(pattern, self.opensci_sku) is None:
            raise ValueError(f"identity {self.kind.value} needs an opensci_sku matching {pattern}")
        if self.custom_part_key is not None and self.kind is not ItemIdentityKindV1.CUSTOMER_PART:
            raise ValueError("custom_part_key belongs to a CUSTOMER_PART identity only")
        return self


class PurchaseV1(StrictContractModel):
    selected: StrictBool
    quantity: NonNegativeInt = Field(description="Units bought for the plan.")
    spare_units: NonNegativeInt = Field(description="Extra units bought as spares.")

    @model_validator(mode="after")
    def _unselected(self) -> PurchaseV1:
        if not self.selected and (self.quantity or self.spare_units):
            raise ValueError("an unselected item buys nothing: quantity = spare_units = 0")
        return self


class ReservedEnvelopeV1(StrictContractModel):
    """Space reserved for a part the customer mounts, in the item's own frame (``COMPONENT_LOCAL``): a CYLINDER about
    local +Z (``diameter_mm``, ``z_min_mm``..``z_max_mm``, the scene's shape) or a BOX (``min_mm``..``max_mm``)."""

    shape: Literal["CYLINDER", "BOX"]
    frame: Literal["COMPONENT_LOCAL"] = "COMPONENT_LOCAL"
    basis: EnvelopeBasisV1
    reserve_column: StrictBool = Field(default=True, description="Also keep the column below it down to the board clear.")
    diameter_mm: PositiveMm | None = None
    z_min_mm: Mm | None = None
    z_max_mm: Mm | None = None
    min_mm: Vec3 | None = None
    max_mm: Vec3 | None = None

    @model_validator(mode="after")
    def _shape(self) -> ReservedEnvelopeV1:
        cylinder = (self.diameter_mm, self.z_min_mm, self.z_max_mm)
        box = (self.min_mm, self.max_mm)
        if self.shape == "CYLINDER":
            if any(v is None for v in cylinder) or any(v is not None for v in box):
                raise ValueError("a CYLINDER envelope states diameter_mm, z_min_mm and z_max_mm only")
            if not self.z_min_mm < self.z_max_mm:
                raise ValueError("a CYLINDER envelope needs z_min_mm < z_max_mm")
        else:
            if any(v is None for v in box) or any(v is not None for v in cylinder):
                raise ValueError("a BOX envelope states min_mm and max_mm only")
            if not all(lo < hi for lo, hi in zip(self.min_mm, self.max_mm, strict=True)):
                raise ValueError("a BOX envelope needs min_mm < max_mm on every axis")
        return self


class SupportRefV1(StrictContractModel):
    parent_item_id: ItemId
    relation: SupportRelationV1
    link_id: LinkId


class PlacementSettingV1(StrictContractModel):
    kind: SettingKindV1
    value: SettingValue
    unit: SettingUnitV1
    basis: PlanFactBasisV1


class FixationV1(StrictContractModel):
    mode: Literal["SLOT"] = "SLOT"
    board_holes: tuple[BoardHole, ...] = Field(min_length=1, max_length=MAX_BOARD_HOLES, description="(i, j) grid holes, sorted.")
    thread: Thread
    slot_offset_mm: Mm = Field(description="Position of the fixing hole along the fork slot.")

    @model_validator(mode="after")
    def _holes(self) -> FixationV1:
        if not _sorted_unique(self.board_holes):
            raise ValueError("fixation.board_holes must be unique and sorted")
        return self


class DeclaredRangeV1(StrictContractModel):
    range_id: RangeId
    kind: RangeKindV1
    axis_lab: Vec3 = Field(description="Unit translation direction or rotation axis in the lab.")
    pivot_lab: Vec3 | None = Field(default=None, description="Rotation ranges: a point of the rotation axis.")
    min: StrictFloat = Field(description="Relative to the current placement (min <= 0).")
    max: StrictFloat = Field(description="Relative to the current placement (max >= 0).")
    unit: RangeUnitV1
    provided_by: ItemId = Field(description="The item whose mechanism gives the range.")
    moves: tuple[ItemId, ...] = Field(min_length=1, description="The items that move with it, sorted.")
    basis: PlanFactBasisV1
    adjustment: AdjustmentV1
    delivers: tuple[DofAxisV1, ...] = Field(default=(), description="Retained DOFs this range implements (DOF axis order).")

    @model_validator(mode="after")
    def _range(self) -> DeclaredRangeV1:
        rotation = self.kind in ROTATION_RANGE_KINDS
        if self.unit is not (RangeUnitV1.DEG if rotation else RangeUnitV1.MM):
            raise ValueError(f"range {self.kind.value} is in {'deg' if rotation else 'mm'}")
        if (self.pivot_lab is not None) != rotation:
            raise ValueError(f"range {self.kind.value}: a pivot exactly for rotation ranges")
        if not self.min <= 0.0 <= self.max:
            raise ValueError("a declared range satisfies min <= 0 <= max (relative to the current placement)")
        if abs(sum(c * c for c in self.axis_lab) - 1.0) > UNIT_AXIS_TOLERANCE:
            raise ValueError("axis_lab must be a unit vector")
        if not _sorted_unique(self.moves):
            raise ValueError("moves must be unique and sorted")
        if list(self.delivers) != [a for a in DOF_AXIS_ORDER if a in set(self.delivers)]:
            raise ValueError("delivers must be unique and in DOF axis order")
        return self


class DofResultV1(StrictContractModel):
    axis: DofAxisV1
    requested_control: DofControlIntentV1
    status: DofResultStatusV1
    mechanism: DofMechanismV1
    range_id: RangeId | None = None
    not_delivered_reason: DofNotDeliveredReasonV1 | None = None
    blocking_item_id: ItemId | None = None

    @model_validator(mode="after")
    def _status(self) -> DofResultV1:
        not_delivered = self.status is DofResultStatusV1.NOT_DELIVERED
        if not_delivered != (self.not_delivered_reason is not None):
            raise ValueError("not_delivered_reason is stated exactly for a NOT_DELIVERED DOF")
        if not_delivered and self.range_id is not None:
            raise ValueError("a NOT_DELIVERED DOF names no range")
        if not not_delivered and self.blocking_item_id is not None:
            raise ValueError("blocking_item_id belongs to a NOT_DELIVERED DOF")
        if (self.status is DofResultStatusV1.DELIVERED_AS_MANUAL_FALLBACK
                and self.requested_control is not DofControlIntentV1.NUMERICAL):
            raise ValueError("a manual fallback answers a NUMERICAL request")
        return self


class PlacementV1(StrictContractModel):
    pose_in_lab: PoseV1 = Field(description="ASSEMBLY_WORLD mm. Authoritative for hardware and the board; for an optic or "
                                            "component it is frame o Project nominal pose (recorded and checked).")
    support: SupportRefV1 | None = None
    assembly_order: PositiveInt = Field(description="1 for the board, then chain order.")
    settings: tuple[PlacementSettingV1, ...] = Field(default=(), max_length=MAX_SETTINGS_PER_ITEM)
    fixation: FixationV1 | None = None
    ranges: tuple[DeclaredRangeV1, ...] = Field(default=(), max_length=MAX_RANGES_PER_ITEM)
    dofs: tuple[DofResultV1, ...] = ()

    @model_validator(mode="after")
    def _lists(self) -> PlacementV1:
        kinds = [s.kind for s in self.settings]
        if kinds != sorted(set(kinds)):
            raise ValueError("placement settings: one per kind, sorted by kind")
        range_ids = [r.range_id for r in self.ranges]
        if range_ids != sorted(set(range_ids)):
            raise ValueError("placement ranges: unique, sorted by range_id")
        axes = [d.axis for d in self.dofs]
        if axes != [a for a in DOF_AXIS_ORDER if a in set(axes)]:
            raise ValueError("placement dofs: one per axis, in DOF axis order")
        known = set(range_ids)
        if any(d.range_id is not None and d.range_id not in known for d in self.dofs):
            raise ValueError("a DOF result names a range of this placement")
        return self


#: Identity kinds each role takes.
ROLE_IDENTITIES: dict[ItemRoleV1, frozenset[ItemIdentityKindV1]] = {
    ItemRoleV1.OPTIC: frozenset({ItemIdentityKindV1.OS_OPT}),
    ItemRoleV1.COMPONENT: frozenset({ItemIdentityKindV1.CUSTOMER_PART}),
    ItemRoleV1.MOUNT: frozenset({ItemIdentityKindV1.OS_MECH, ItemIdentityKindV1.USER_MECH, ItemIdentityKindV1.NONE}),
    ItemRoleV1.POST: frozenset({ItemIdentityKindV1.OS_MECH, ItemIdentityKindV1.USER_MECH, ItemIdentityKindV1.NONE}),
    ItemRoleV1.POST_HOLDER: frozenset({ItemIdentityKindV1.OS_MECH, ItemIdentityKindV1.USER_MECH, ItemIdentityKindV1.NONE}),
    ItemRoleV1.BREADBOARD: frozenset({ItemIdentityKindV1.OS_MECH, ItemIdentityKindV1.CUSTOMER_TABLE}),
    ItemRoleV1.DECLARED_ENVELOPE: frozenset({ItemIdentityKindV1.CUSTOMER_PART}),
}


class PlanItemV1(StrictContractModel):
    item_id: ItemId
    role: ItemRoleV1
    identity: ItemIdentityV1
    design_instance_id: InstanceKey | None = Field(default=None, description="Exactly for OPTIC and COMPONENT items.")
    mounting: MountingV1 | None = Field(default=None, description="OPTIC only (required).")
    supply: SupplyV1
    supply_reason: SupplyReasonV1 | None = None
    quantity: Literal[1] = Field(default=1, description="Physical units placed by this item (1 in schema 1.0).")
    placement: PlacementV1 | None = Field(default=None, description="None exactly for unresolved chain hardware (identity NONE).")
    envelope: ReservedEnvelopeV1 | None = Field(default=None, description="ANCHOR optic / COMPONENT with known geometry; "
                                                                          "required for a DECLARED_ENVELOPE.")
    pinned: StrictBool = Field(default=False, description="derive --from keeps a pinned identity.")
    purchase: PurchaseV1

    @model_validator(mode="after")
    def _item(self) -> PlanItemV1:
        problems = _item_problems(self)
        if problems:
            raise ValueError(f"item {self.item_id}: " + "; ".join(problems))
        return self

    @property
    def is_anchor(self) -> bool:
        """Placed at its design pose without OpenSci hardware: an ANCHOR optic, a component or a declared envelope."""

        return (self.role in (ItemRoleV1.COMPONENT, ItemRoleV1.DECLARED_ENVELOPE)
                or (self.role is ItemRoleV1.OPTIC and self.mounting is MountingV1.ANCHOR))


def _item_problems(item: PlanItemV1) -> list[str]:
    role, kind, supply, reason = item.role, item.identity.kind, item.supply, item.supply_reason
    problems: list[str] = []
    parts = item_id_parts(item.item_id)
    if parts is None or parts[0] is not ROLE_PREFIX[role]:
        problems.append(f"a {role.value} item id is '{ROLE_PREFIX[role].value}-<n>'")
    if kind not in ROLE_IDENTITIES[role]:
        problems.append(f"a {role.value} item has identity {' or '.join(sorted(k.value for k in ROLE_IDENTITIES[role]))}")
    if (item.design_instance_id is not None) != (role in DESIGN_INSTANCE_ROLES):
        problems.append("design_instance_id is stated exactly for OPTIC and COMPONENT items")
    if (item.mounting is not None) != (role is ItemRoleV1.OPTIC):
        problems.append("mounting is stated exactly for OPTIC items")
    if reason not in SUPPLY_REASONS[supply]:
        problems.append(f"supply {supply.value} does not take reason {reason.value if reason else None}")
    if supply is SupplyV1.OPENSCI and kind not in (ItemIdentityKindV1.OS_OPT, ItemIdentityKindV1.OS_MECH):
        problems.append("OPENSCI supplies OS-OPT and OS-MECH identities only")
    if kind is ItemIdentityKindV1.USER_MECH and (supply, reason) != (SupplyV1.CUSTOMER_OWNED, SupplyReasonV1.USER_HARDWARE):
        problems.append("USER_MECH hardware is CUSTOMER_OWNED with reason USER_HARDWARE")
    if (kind is ItemIdentityKindV1.NONE) != ((supply, reason) == (SupplyV1.UNRESOLVED, SupplyReasonV1.NO_COMPATIBLE_HARDWARE)):
        problems.append("identity NONE is exactly UNRESOLVED with reason NO_COMPATIBLE_HARDWARE")
    if kind is ItemIdentityKindV1.CUSTOMER_TABLE and (supply, reason) != (SupplyV1.CUSTOMER_OWNED, SupplyReasonV1.CUSTOMER_TABLE):
        problems.append("a customer table is CUSTOMER_OWNED with reason CUSTOMER_TABLE")
    if role is ItemRoleV1.COMPONENT and (supply is not SupplyV1.CUSTOMER_OWNED or reason not in (
            SupplyReasonV1.CUSTOMER_LIGHT_SOURCE, SupplyReasonV1.NOT_IN_CATALOG)):
        problems.append("a COMPONENT is CUSTOMER_OWNED with reason CUSTOMER_LIGHT_SOURCE or NOT_IN_CATALOG")
    if role is ItemRoleV1.DECLARED_ENVELOPE and (supply, reason) != (SupplyV1.CUSTOMER_OWNED, SupplyReasonV1.NOT_IN_CATALOG):
        problems.append("a DECLARED_ENVELOPE is CUSTOMER_OWNED with reason NOT_IN_CATALOG")
    if reason is SupplyReasonV1.OPENSCI_SUPPLIED_UNMOUNTED and not (role is ItemRoleV1.OPTIC and item.mounting is MountingV1.ANCHOR):
        problems.append("OPENSCI_SUPPLIED_UNMOUNTED is the reason of an ANCHOR optic")
    if item.purchase.selected and supply is not SupplyV1.OPENSCI:
        problems.append("only an OPENSCI item can be selected for purchase")
    if (item.placement is None) != (kind is ItemIdentityKindV1.NONE):
        problems.append("placement is absent exactly for unresolved chain hardware (identity NONE)")
    placement = item.placement
    if placement is not None:
        if (item.is_anchor or role is ItemRoleV1.BREADBOARD) and placement.support is not None:
            problems.append("the board and anchors have no support")
        if placement.fixation is not None and role is not ItemRoleV1.POST_HOLDER:
            problems.append("a fixation belongs to a POST_HOLDER")
        if (placement.ranges or placement.dofs) and role is not ItemRoleV1.OPTIC:
            problems.append("declared ranges and DOF results belong to an OPTIC")
        if role is ItemRoleV1.BREADBOARD and placement.assembly_order != 1:
            problems.append("the board is assembled first (assembly_order 1)")
        prefix = "rng-" + item.item_id + "-"
        for declared in placement.ranges:
            if declared.range_id != prefix + declared.kind.value.lower().replace("_", "-"):
                problems.append(f"range {declared.range_id} is named '{prefix}<kind>'")
    if role is ItemRoleV1.DECLARED_ENVELOPE:
        if item.envelope is None or item.envelope.basis is not EnvelopeBasisV1.USER_DECLARED:
            problems.append("a DECLARED_ENVELOPE states its USER_DECLARED envelope")
    elif item.envelope is not None and not item.is_anchor:
        problems.append("only an anchor reserves an envelope")
    elif item.envelope is not None and item.envelope.basis is EnvelopeBasisV1.USER_DECLARED:
        problems.append("USER_DECLARED envelopes belong to DECLARED_ENVELOPE items")
    return problems


# ------------------------------------------------------------------------------------------------ links


class FastenerV1(StrictContractModel):
    kind: FastenerKindV1
    thread: Thread


class InterfaceV1(StrictContractModel):
    designation: InterfaceDesignation = Field(description="e.g. 'OD25.4-SEAT', 'M4', '8-32', 'OD12.7-POST', 'M6'.")
    diameter_mm: PositiveMm | None = Field(default=None, description="OPTIC_IN_MOUNT: optic size; POST_IN_HOLDER: post diameter.")
    depth_mm: NonNegativeMm | None = Field(default=None, description="OPTIC_IN_MOUNT: seated thickness; POST_IN_HOLDER: "
                                                                     "insertion depth.")
    thread: Thread | None = Field(default=None, description="MOUNT_ON_POST: post thread; HOLDER_ON_BOARD: fork / board thread.")
    fastener: FastenerV1 | None = None
    fastening_options: tuple[FastenerV1, ...] | None = Field(
        default=None, description="When the mount profile allows several fastenings (finding FASTENING_VARIES).")


class ChainLinkV1(StrictContractModel):
    """One joint of a chain: ``a_item_id`` (carried) on ``b_item_id`` (carrier). ``interface`` and ``b_in_a`` are absent
    exactly when either end is unresolved hardware (identity NONE): the link then only records chain membership."""

    link_id: LinkId
    kind: ChainLinkKindV1
    a_item_id: ItemId
    b_item_id: ItemId
    datum_a: DatumKey = Field(description="The kernel's datum name of the carried part role.")
    datum_b: DatumKey = Field(description="The kernel's datum name of the carrier part role ('board_top' for the board).")
    interface: InterfaceV1 | None = None
    b_in_a: PoseV1 | None = Field(default=None, description="Pose of the carrier in the carried part's frame (recomputed).")

    @model_validator(mode="after")
    def _link(self) -> ChainLinkV1:
        if self.link_id != f"lnk-{self.a_item_id}-{self.b_item_id}":
            raise ValueError("link_id is 'lnk-' + a_item_id + '-' + b_item_id")
        if (self.interface is None) != (self.b_in_a is None):
            raise ValueError("interface and b_in_a are both stated or both absent (unresolved end)")
        return self


# ------------------------------------------------------------------------------------------------ findings and status


class FindingV1(StrictContractModel):
    """One finding of a plan check (rules, templates and ids: ``findings.py``)."""

    finding_id: FindingId
    code: FindingCode
    severity: FindingSeverityV1
    item_ids: tuple[ItemId, ...] = Field(default=(), max_length=2, description="The part or pair (sorted); none = whole plan.")
    interface: ChainLinkKindV1 | None = None
    check: FindingCheckV1
    basis: FindingBasisV1
    missing_data: tuple[MissingDataKey, ...] = Field(default=(), max_length=MAX_MISSING_DATA)
    message: str = Field(min_length=1, max_length=FINDING_MESSAGE_MAX)
    details: dict[str, Any] = Field(default_factory=dict, description="Bounded JSON (<= 4 KiB canonical, depth <= 4).")
    source: FindingSourceV1

    @model_validator(mode="after")
    def _bounded(self) -> FindingV1:
        if not _sorted_unique(self.item_ids):
            raise ValueError("finding item_ids must be unique and sorted")
        if not _sorted_unique(self.missing_data):
            raise ValueError("finding missing_data must be unique and sorted")
        if _depth(self.details) > FINDING_DETAILS_MAX_DEPTH:
            raise ValueError(f"finding details nest deeper than {FINDING_DETAILS_MAX_DEPTH}")
        try:
            size = len(canonical_bytes(self.details))
        except ValueError as exc:
            raise ValueError(f"finding details are not JSON data: {exc}") from None
        if size > FINDING_DETAILS_MAX_BYTES:
            raise ValueError(f"finding details exceed {FINDING_DETAILS_MAX_BYTES} bytes")
        return self

    def sort_key(self) -> tuple[int, str, tuple[str, ...], str]:
        return (SEVERITY_RANK[self.severity], self.code.value, self.item_ids, self.finding_id)


def canonical_bytes(value: Any) -> bytes:
    """osc1 canonical bytes of a JSON value."""

    return canonicalize_osc1_bytes(value)


def _depth(value: Any) -> int:
    if isinstance(value, dict):
        return 1 + max((_depth(v) for v in value.values()), default=0)
    if isinstance(value, (list, tuple)):
        return 1 + max((_depth(v) for v in value), default=0)
    return 0


class FindingCountsV1(StrictContractModel):
    error: NonNegativeInt
    unknown: NonNegativeInt
    warning: NonNegativeInt
    info: NonNegativeInt


def plan_state(counts: FindingCountsV1) -> PlanStateV1:
    """Any ERROR -> HAS_ERRORS; else any UNKNOWN -> INCOMPLETE; else any WARNING -> READY_WITH_WARNINGS; else READY."""

    if counts.error:
        return PlanStateV1.HAS_ERRORS
    if counts.unknown:
        return PlanStateV1.INCOMPLETE
    return PlanStateV1.READY_WITH_WARNINGS if counts.warning else PlanStateV1.READY


class LayoutValidationRefV1(StrictContractModel):
    status: AssemblyValidationStatusV1
    result_hash: Sha256Digest = Field(description="result_hash of the AssemblyLocalValidationV1.")
    checker_version: BuildVersion


class EvidenceSummaryV1(StrictContractModel):
    conservative_profile_items: tuple[ItemId, ...] = ()
    preview_assumption_items: tuple[ItemId, ...] = ()

    @model_validator(mode="after")
    def _sorted(self) -> EvidenceSummaryV1:
        if not (_sorted_unique(self.conservative_profile_items) and _sorted_unique(self.preview_assumption_items)):
            raise ValueError("evidence item lists must be unique and sorted")
        return self


class PurchaseSummaryV1(StrictContractModel):
    selected_lines: NonNegativeInt
    excluded_items: NonNegativeInt
    unresolved_items: NonNegativeInt


class PlanStatusV1(StrictContractModel):
    state: PlanStateV1
    counts: FindingCountsV1
    optics_results: OpticsResultsStateV1
    layout_validation: LayoutValidationRefV1 | None = Field(default=None, description="None: not run (e.g. no package).")
    evidence: EvidenceSummaryV1
    purchase: PurchaseSummaryV1

    @model_validator(mode="after")
    def _state(self) -> PlanStatusV1:
        if self.state is not plan_state(self.counts):
            raise ValueError(f"status state {self.state.value} does not follow from the counts ({plan_state(self.counts).value})")
        return self


class CheckedWithV1(StrictContractModel):
    checker_version: BuildVersion
    kernel_version: BuildVersion
    plan_checks_version: BuildVersion


def finding_counts(findings: tuple[FindingV1, ...] | list[FindingV1]) -> FindingCountsV1:
    by = {s: 0 for s in FindingSeverityV1}
    for finding in findings:
        by[finding.severity] += 1
    return FindingCountsV1(error=by[FindingSeverityV1.ERROR], unknown=by[FindingSeverityV1.UNKNOWN],
                           warning=by[FindingSeverityV1.WARNING], info=by[FindingSeverityV1.INFO])


# ------------------------------------------------------------------------------------------------ revision


class IdCountersV1(StrictContractModel):
    """Highest number ever issued per item id prefix (ids are never reused)."""

    opt: NonNegativeInt = 0
    cmp: NonNegativeInt = 0
    mnt: NonNegativeInt = 0
    pst: NonNegativeInt = 0
    hld: NonNegativeInt = 0
    brd: NonNegativeInt = 0
    env: NonNegativeInt = 0

    def of(self, prefix: ItemPrefixV1) -> int:
        return int(getattr(self, prefix.value))


class AssemblyPlanContentV1(StrictContractModel):
    """The decision fields of a revision (``CONTENT_FIELDS``): what ``derive`` and the ops produce before the check. Its
    validator holds every structural cross-field rule; ``AssemblyPlanRevisionV1`` adds the digest and the recorded check."""

    schema_id: Literal["os-optics.assembly_plan"] = Field(PLAN_SCHEMA_ID, alias="schema")
    schema_version: Literal["1.0"] = PLAN_SCHEMA_VERSION
    plan_id: PlanId
    revision: PositiveInt
    parent_digest: Sha256Digest | None = Field(default=None, description="None exactly for revision 1.")
    origin: PlanOriginV1
    bindings: PlanBindingsV1
    frame: PlanFrameV1
    board_item_id: ItemId
    items: tuple[PlanItemV1, ...] = Field(min_length=1, max_length=MAX_ITEMS, description="Sorted by item_id.")
    links: tuple[ChainLinkV1, ...] = Field(default=(), max_length=MAX_LINKS, description="Sorted by link_id.")
    id_counters: IdCountersV1
    retired_item_ids: tuple[ItemId, ...] = Field(default=(), max_length=MAX_RETIRED_ITEMS)
    derive_options: DeriveOptionsV1

    @model_validator(mode="after")
    def _content(self) -> AssemblyPlanContentV1:
        problems = content_problems(self)
        if problems:
            raise ValueError("assembly plan content: " + "; ".join(problems))
        return self

    def payload(self) -> dict[str, Any]:
        """The JSON payload (``by_alias``, None fields omitted)."""

        return _dump(self)

    def item(self, item_id: str) -> PlanItemV1 | None:
        return next((i for i in self.items if i.item_id == item_id), None)


class AssemblyPlanRevisionV1(AssemblyPlanContentV1):
    """A revision document: the decisions, the plan digest over them, and the check recorded when it was written."""

    content_digest: Sha256Digest = Field(description="The plan digest: osc1 digest of CONTENT_FIELDS (number rule applied).")
    findings: tuple[FindingV1, ...] = Field(default=(), max_length=MAX_FINDINGS,
                                            description="Recorded check, sorted by (severity rank, code, item_ids, finding_id).")
    status: PlanStatusV1
    checked_with: CheckedWithV1

    @model_validator(mode="after")
    def _revision(self) -> AssemblyPlanRevisionV1:
        problems = revision_problems(self)
        if problems:
            raise ValueError("assembly plan revision: " + "; ".join(problems))
        return self

    def content(self) -> dict[str, Any]:
        """The decision fields of this revision's payload (what the digest covers)."""

        return content_payload(_dump(self))

    def hash_ref(self) -> HashRef:
        return HashRef(profile=HashProfile.OSC1, digest=self.content_digest, subject="assembly_plan_revision")


def content_problems(content: AssemblyPlanContentV1) -> list[str]:
    """Every structural cross-field rule of the decisions that their parts cannot check alone."""

    problems: list[str] = []
    if (content.parent_digest is None) != (content.revision == 1):
        problems.append("parent_digest is absent exactly for revision 1")
    if content.origin is PlanOriginV1.DERIVE and content.revision != 1:
        problems.append("origin DERIVE writes revision 1")
    if content.revision == 1 and content.origin not in (PlanOriginV1.DERIVE, PlanOriginV1.REDERIVE):
        problems.append("revision 1 comes from DERIVE or REDERIVE")
    items = {i.item_id: i for i in content.items}
    problems += _id_problems(content, items)
    boards = [i.item_id for i in content.items if i.role is ItemRoleV1.BREADBOARD]
    if boards != [content.board_item_id]:
        problems.append("exactly one BREADBOARD item, named by board_item_id")
    instances = [i.design_instance_id for i in content.items if i.design_instance_id is not None]
    if len(instances) != len(set(instances)):
        problems.append("each design instance is one item")
    problems += _link_problems(content, items)
    problems += _reference_problems(content, items)
    return problems


def revision_problems(revision: AssemblyPlanRevisionV1) -> list[str]:
    """The rules of a revision beyond its content: the recorded check and the digest."""

    items = {i.item_id: i for i in revision.items}
    problems = _finding_problems(revision, items)
    evidence = revision.status.evidence
    if any(i not in items for i in (*evidence.conservative_profile_items, *evidence.preview_assumption_items)):
        problems.append("status.evidence names an item the revision does not have")
    content = revision.content()
    if normalize_numbers(content) != content:
        problems.append("numbers are not normalized (build revisions with build_revision / normalize_numbers)")
    elif osc1_digest(content) != revision.content_digest:
        problems.append("content_digest does not match the revision content")
    return problems


def _id_problems(revision: AssemblyPlanContentV1, items: dict[str, PlanItemV1]) -> list[str]:
    problems: list[str] = []
    ids = [i.item_id for i in revision.items]
    if ids != sorted(set(ids)):
        problems.append("items must be unique and sorted by item_id")
    retired = revision.retired_item_ids
    if not _sorted_unique(retired):
        problems.append("retired_item_ids must be unique and sorted")
    if set(retired) & set(items):
        problems.append("a retired item id is never used again")
    for item_id in (*ids, *retired):
        parts = item_id_parts(item_id)
        if parts is None:
            problems.append(f"{item_id} is not an allocated item id '<prefix>-<n>'")
        elif parts[1] > revision.id_counters.of(parts[0]):
            problems.append(f"{item_id} is above id_counters.{parts[0].value}")
    return problems


def _link_problems(revision: AssemblyPlanContentV1, items: dict[str, PlanItemV1]) -> list[str]:
    problems: list[str] = []
    link_ids = [link.link_id for link in revision.links]
    if link_ids != sorted(set(link_ids)):
        problems.append("links must be unique and sorted by link_id")
    outgoing: dict[str, ChainLinkV1] = {}
    incoming: dict[str, list[ChainLinkV1]] = {}
    for link in revision.links:
        a, b = items.get(link.a_item_id), items.get(link.b_item_id)
        if a is None or b is None:
            problems.append(f"link {link.link_id} names an item the revision does not have")
            continue
        carried, carrier, _relation = LINK_KINDS[link.kind]
        if (a.role, b.role) != (carried, carrier):
            problems.append(f"link {link.link_id} ({link.kind.value}) joins a {carried.value} to a {carrier.value}")
        unresolved = ItemIdentityKindV1.NONE in (a.identity.kind, b.identity.kind)
        if unresolved != (link.interface is None):
            problems.append(f"link {link.link_id}: interface and b_in_a are absent exactly when an end is unresolved")
        if link.a_item_id in outgoing:
            problems.append(f"{link.a_item_id} is carried by more than one item")
        outgoing[link.a_item_id] = link
        incoming.setdefault(link.b_item_id, []).append(link)
    for item in revision.items:
        out, into = outgoing.get(item.item_id), incoming.get(item.item_id, [])
        if item.role in CHAIN_HARDWARE_ROLES:
            if out is None or len(into) != 1:
                problems.append(f"{item.item_id} belongs to exactly one chain (one carried item, one carrier)")
        elif item.role is ItemRoleV1.OPTIC:
            if item.mounting is MountingV1.CHAIN and not _complete_chain(item.item_id, outgoing, items, revision.board_item_id):
                problems.append(f"CHAIN optic {item.item_id} needs its mount, post and holder linked down to the board")
            if item.mounting is MountingV1.ANCHOR and out is not None:
                problems.append(f"ANCHOR optic {item.item_id} has no chain links")
        elif item.role is not ItemRoleV1.BREADBOARD and (out is not None or into):
            problems.append(f"{item.item_id} ({item.role.value}) takes no chain link")
        placement = item.placement
        if placement is None or item.is_anchor or item.role is ItemRoleV1.BREADBOARD or out is None:
            continue
        carrier_placed = items.get(out.b_item_id) is not None and items[out.b_item_id].placement is not None
        expected = (SupportRefV1(parent_item_id=out.b_item_id, relation=LINK_KINDS[out.kind][2], link_id=out.link_id)
                    if carrier_placed else None)
        if placement.support != expected:
            problems.append(f"{item.item_id}: support must be its chain link {out.link_id}" if carrier_placed
                            else f"{item.item_id}: no support while its carrier is unresolved")
    return problems


def _complete_chain(optic_id: str, outgoing: dict[str, ChainLinkV1], items: dict[str, PlanItemV1], board_id: str) -> bool:
    current = optic_id
    for kind in LINK_KINDS:
        link = outgoing.get(current)
        if link is None or link.kind is not kind:
            return False
        current = link.b_item_id
    return current == board_id and current in items


def _reference_problems(revision: AssemblyPlanContentV1, items: dict[str, PlanItemV1]) -> list[str]:
    problems: list[str] = []
    for item in revision.items:
        placement = item.placement
        if placement is None:
            continue
        for declared in placement.ranges:
            if declared.provided_by not in items or any(m not in items for m in declared.moves):
                problems.append(f"range {declared.range_id} names an item the revision does not have")
        for dof in placement.dofs:
            if dof.blocking_item_id is not None and dof.blocking_item_id not in items:
                problems.append(f"{item.item_id} DOF {dof.axis.value} names an unknown blocking item")
    return problems


def _finding_problems(revision: AssemblyPlanRevisionV1, items: dict[str, PlanItemV1]) -> list[str]:
    from .findings import finding_problems

    problems: list[str] = []
    findings = revision.findings
    if [f.sort_key() for f in findings] != sorted(f.sort_key() for f in findings):
        problems.append("findings must be sorted by (severity rank, code, item_ids, finding_id)")
    if len({f.finding_id for f in findings}) != len(findings):
        problems.append("each finding_id once")
    for finding in findings:
        if any(i not in items for i in finding.item_ids):
            problems.append(f"finding {finding.finding_id} names an item the revision does not have")
        problems += [f"finding {finding.finding_id}: {p}" for p in finding_problems(finding)]
    if revision.status.counts != finding_counts(findings):
        problems.append("status.counts must count the recorded findings")
    return problems


def build_revision(content: dict[str, Any], *, findings: tuple[FindingV1, ...] | list[FindingV1], status: PlanStatusV1,
                   checked_with: CheckedWithV1) -> AssemblyPlanRevisionV1:
    """A validated revision from its decision fields (``CONTENT_FIELDS``; validated as ``AssemblyPlanContentV1``, defaults
    filled, numbers normalized and ``content_digest`` computed here) and its recorded check (findings in any order)."""

    decisions = AssemblyPlanContentV1.model_validate(normalize_numbers(content_payload(content)))
    normalized = normalize_numbers(_dump(decisions))
    document = {**normalized, "content_digest": osc1_digest(normalized),
                "findings": [_dump(f) for f in sorted(findings, key=FindingV1.sort_key)],
                "status": _dump(status), "checked_with": _dump(checked_with)}
    return AssemblyPlanRevisionV1.model_validate(document)


def plan_json_schema() -> dict[str, Any]:
    """JSON Schema of a revision document (``os-optics schema assembly-plan``)."""

    return AssemblyPlanRevisionV1.model_json_schema(by_alias=True)


def _require_osc1(ref: HashRef, subject: str, name: str) -> None:
    if ref.profile is not HashProfile.OSC1 or ref.subject != subject or ref.params is not None:
        raise ValueError(f"{name} is an osc1 HashRef of subject {subject!r}")


# ------------------------------------------------------------------------------------------------ edit operations (1.3.8)


class RefixV1(StrEnum):
    NEAREST_HOLE = "NEAREST_HOLE"
    KEEP_HOLE = "KEEP_HOLE"


class MoveOpticOpV1(StrictContractModel):
    op: Literal["move_optic"] = "move_optic"
    item_id: ItemId
    delta_design_mm: Vec3 | None = None
    position_design_mm: Vec3 | None = None
    quaternion_wxyz: Quaternion | None = Field(default=None, description="New design orientation.")
    refix: RefixV1 = RefixV1.NEAREST_HOLE

    @model_validator(mode="after")
    def _one(self) -> MoveOpticOpV1:
        if (self.delta_design_mm is None) == (self.position_design_mm is None):
            raise ValueError("move_optic takes exactly one of delta_design_mm / position_design_mm")
        if self.quaternion_wxyz is not None and not any(c != 0.0 for c in self.quaternion_wxyz):
            raise ValueError("quaternion_wxyz must be non-zero")
        return self


class AdjustOpV1(StrictContractModel):
    op: Literal["adjust"] = "adjust"
    item_id: ItemId
    range_id: RangeId
    delta: StrictFloat = Field(description="In the unit of the range.")


class SetHardwareOpV1(StrictContractModel):
    op: Literal["set_hardware"] = "set_hardware"
    item_id: ItemId
    opensci_sku: HardwareSku


class SetFixationOpV1(StrictContractModel):
    op: Literal["set_fixation"] = "set_fixation"
    item_id: ItemId
    hole: BoardHole
    base_yaw_deg: Deg | None = None


class SetBoardOpV1(StrictContractModel):
    op: Literal["set_board"] = "set_board"
    opensci_sku: OsMechSku | None = None
    customer_table: CustomerTableV1 | None = None
    recentre: StrictBool = False

    @model_validator(mode="after")
    def _one(self) -> SetBoardOpV1:
        if (self.opensci_sku is None) == (self.customer_table is None):
            raise ValueError("set_board takes exactly one of opensci_sku / customer_table")
        return self


class SetSupplyOpV1(StrictContractModel):
    op: Literal["set_supply"] = "set_supply"
    item_id: ItemId
    supply: Literal["OPENSCI", "CUSTOMER_OWNED"]


class SetPurchaseOpV1(StrictContractModel):
    op: Literal["set_purchase"] = "set_purchase"
    item_id: ItemId
    selected: StrictBool
    quantity: NonNegativeInt | None = None
    spare_units: NonNegativeInt | None = None

    @model_validator(mode="after")
    def _unselected(self) -> SetPurchaseOpV1:
        if not self.selected and (self.quantity or self.spare_units):
            raise ValueError("an unselected item buys nothing")
        return self


class SetMountingOpV1(StrictContractModel):
    op: Literal["set_mounting"] = "set_mounting"
    item_id: ItemId
    mounting: MountingV1


class RederiveChainOpV1(StrictContractModel):
    op: Literal["rederive_chain"] = "rederive_chain"
    item_id: ItemId
    keep: tuple[ItemId, ...] = Field(default=(), max_length=MAX_KEEP_ITEMS, description="Pinned hardware to keep (sorted).")

    @model_validator(mode="after")
    def _sorted(self) -> RederiveChainOpV1:
        if not _sorted_unique(self.keep):
            raise ValueError("keep must be unique and sorted")
        return self


class PinOpV1(StrictContractModel):
    op: Literal["pin"] = "pin"
    item_id: ItemId
    pinned: StrictBool


class RebindCatalogsOpV1(StrictContractModel):
    op: Literal["rebind_catalogs"] = "rebind_catalogs"


class LabBoxV1(StrictContractModel):
    shape: Literal["BOX"] = "BOX"
    min_mm: Vec3
    max_mm: Vec3

    @model_validator(mode="after")
    def _box(self) -> LabBoxV1:
        if not all(lo < hi for lo, hi in zip(self.min_mm, self.max_mm, strict=True)):
            raise ValueError("a BOX needs min_mm < max_mm on every axis")
        return self


class LabCylinderV1(StrictContractModel):
    shape: Literal["CYLINDER"] = "CYLINDER"
    diameter_mm: PositiveMm
    z_min_mm: Mm = Field(description="Start along the axis (lab).")
    z_max_mm: Mm = Field(description="End along the axis (lab).")
    axis: Literal["x", "y", "z"] = "z"
    centre_mm: tuple[StrictFloat, StrictFloat] = Field(
        default=(0.0, 0.0), description="Lab position of the axis in the two other coordinates (in x, y, z order).")

    @model_validator(mode="after")
    def _span(self) -> LabCylinderV1:
        if not self.z_min_mm < self.z_max_mm:
            raise ValueError("a CYLINDER needs z_min_mm < z_max_mm")
        return self


class DeclareEnvelopeOpV1(StrictContractModel):
    op: Literal["declare_envelope"] = "declare_envelope"
    item_id: ItemId | None = Field(default=None, description="A new 'env-<n>' id; allocated when absent.")
    label: DisplayText
    envelope: Annotated[LabBoxV1 | LabCylinderV1, Field(discriminator="shape")] = Field(description="In lab coordinates.")
    reserve_column: StrictBool = True

    @model_validator(mode="after")
    def _env(self) -> DeclareEnvelopeOpV1:
        parts = None if self.item_id is None else item_id_parts(self.item_id)
        if self.item_id is not None and (parts is None or parts[0] is not ItemPrefixV1.ENV):
            raise ValueError("a declared envelope's item_id is 'env-<n>'")
        return self


class RemoveEnvelopeOpV1(StrictContractModel):
    op: Literal["remove_envelope"] = "remove_envelope"
    item_id: ItemId


class RevertToOpV1(StrictContractModel):
    op: Literal["revert_to"] = "revert_to"
    revision: PositiveInt


AssemblyPlanOpV1 = Annotated[
    MoveOpticOpV1 | AdjustOpV1 | SetHardwareOpV1 | SetFixationOpV1 | SetBoardOpV1 | SetSupplyOpV1 | SetPurchaseOpV1
    | SetMountingOpV1 | RederiveChainOpV1 | PinOpV1 | RebindCatalogsOpV1 | DeclareEnvelopeOpV1 | RemoveEnvelopeOpV1
    | RevertToOpV1,
    Field(discriminator="op"),
]


class EditRequestV1(StrictContractModel):
    """One edit call: ``ops`` applied in order, atomically (one new revision or nothing)."""

    ops: tuple[AssemblyPlanOpV1, ...] = Field(min_length=1, max_length=MAX_OPS_PER_EDIT)
    op_id: OperationId | None = None
    expected_base: Sha256Digest | None = Field(default=None, description="The head content_digest the edit is based on.")
    require_check: StrictBool = Field(default=False, description="Refuse new ERROR/UNKNOWN findings (not in the digest).")

    def request_digest(self) -> str:
        """osc1 digest of ``{"ops": ops}`` (``op_id``, ``expected_base`` and ``require_check`` excluded)."""

        return osc1_digest(normalize_numbers({"ops": [_dump(op) for op in self.ops]}))


class OpResultV1(StrictContractModel):
    op_index: NonNegativeInt
    op: str
    changed_items: tuple[ItemId, ...] = ()
    retired_items: tuple[ItemId, ...] = ()
    created_items: tuple[ItemId, ...] = ()
    project_changed: StrictBool = False


class PlanCallSummaryV1(StrictContractModel):
    """The result of one derive / edit call without its findings (what ``history.jsonl`` records)."""

    ok: StrictBool
    plan_dir: str
    plan_id: PlanId
    revision: PositiveInt
    content_digest: Sha256Digest
    base_digest: Sha256Digest | None = None
    replayed: StrictBool = False
    op_id: OperationId
    ops: tuple[OpResultV1, ...] = ()
    project_hash: Sha256Digest
    project_changed: StrictBool = False
    project_written: str | None = None
    run_freshness: dict[str, Any] | None = Field(default=None, description="local run_freshness result when the Project moved.")
    findings_added: tuple[FindingId, ...] = ()
    findings_resolved: tuple[FindingId, ...] = ()
    status: PlanStatusV1


class PlanCallResultV1(PlanCallSummaryV1):
    findings: tuple[FindingV1, ...] = ()


class HistoryLineV1(StrictContractModel):
    """One line of ``history.jsonl``. No timestamp (the Engine core has no clock): ``seq`` and the digest chain order the
    history. Engine identity is recorded here only, never in a revision."""

    seq: PositiveInt
    op_id: OperationId
    kind: PlanOriginV1
    request_digest: Sha256Digest | None = None
    base_revision: PositiveInt | None = None
    base_digest: Sha256Digest | None = None
    result_revision: PositiveInt
    result_digest: Sha256Digest
    project_hash: Sha256Digest
    result: PlanCallSummaryV1
    engine_version: BuildVersion
    engine_build_id: BuildVersion

    @model_validator(mode="after")
    def _base(self) -> HistoryLineV1:
        if (self.base_revision is None) != (self.base_digest is None):
            raise ValueError("base_revision and base_digest are both stated or both absent")
        if self.base_revision is not None and self.base_revision >= self.result_revision and not self.result.replayed:
            raise ValueError("a write produces a revision above its base")
        return self


# ------------------------------------------------------------------------------------------------ errors (2.4)


class AssemblyPlanErrorCode(StrEnum):
    PLAN_DIR_INCONSISTENT = "PLAN_DIR_INCONSISTENT"
    PLAN_DIR_NOT_EMPTY = "PLAN_DIR_NOT_EMPTY"
    PLAN_DIR_LOCKED = "PLAN_DIR_LOCKED"
    PLAN_OP_INVALID = "PLAN_OP_INVALID"
    OP_ID_REUSED = "OP_ID_REUSED"
    BASE_CHANGED = "BASE_CHANGED"
    PLAN_CHECK_REFUSED = "PLAN_CHECK_REFUSED"
    PLAN_HAS_FINDINGS = "PLAN_HAS_FINDINGS"
    PLAN_USER_HARDWARE_NOT_PROJECTABLE = "PLAN_USER_HARDWARE_NOT_PROJECTABLE"
    PURCHASE_BOM_EMPTY = "PURCHASE_BOM_EMPTY"
    MECHANICAL_CATALOG_NOT_AVAILABLE = "MECHANICAL_CATALOG_NOT_AVAILABLE"
    MECHANICAL_CATALOG_INVALID = "MECHANICAL_CATALOG_INVALID"
    MECHANICAL_SCHEMA_UNSUPPORTED = "MECHANICAL_SCHEMA_UNSUPPORTED"
    MECHANICAL_IDENTITY_DUPLICATE = "MECHANICAL_IDENTITY_DUPLICATE"
    PROJECT_REJECTED = "PROJECT_REJECTED"


class PlanOpErrorCode(StrEnum):
    """``details.op_code`` of a ``PLAN_OP_INVALID`` refusal (with ``details.op_index``)."""

    OP_MALFORMED = "OP_MALFORMED"
    ITEM_UNKNOWN = "ITEM_UNKNOWN"
    ROLE_MISMATCH = "ROLE_MISMATCH"
    KIND_MISMATCH = "KIND_MISMATCH"
    IDENTITY_UNKNOWN = "IDENTITY_UNKNOWN"
    RANGE_UNKNOWN = "RANGE_UNKNOWN"
    RANGE_EXCEEDED = "RANGE_EXCEEDED"
    SUPPLY_NOT_ALLOWED = "SUPPLY_NOT_ALLOWED"
    ITEM_ID_TAKEN = "ITEM_ID_TAKEN"
    REVISION_UNKNOWN = "REVISION_UNKNOWN"


class AssemblyPlanError(Exception):
    """A refused plan call: stable ``code``, a ``message``, structured ``details`` and the ``problems`` found."""

    def __init__(self, code: AssemblyPlanErrorCode, message: str, details: dict[str, Any] | None = None,
                 problems: list[str] | None = None) -> None:
        super().__init__(message)
        self.code = AssemblyPlanErrorCode(code)
        self.message = message
        self.details = dict(details or {})
        self.problems = list(problems or [])

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code.value, "message": self.message, "details": self.details, "problems": self.problems}
