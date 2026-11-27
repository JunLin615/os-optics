"""The public mechanical catalog: ``opensci.public_mechanical_catalog`` 1.0 (an Engine-owned contract, not vendored).

The document lists the mechanical hardware a local assembly plan may use, one entry per OpenSci identity
(``OS-MECH-*``; a customer's own hardware document uses ``USER-MECH-*``), described only by the engineering parameters
the assembly resolver reads, neutral visual primitives and a colour, and -- per parameter -- the kind of source the
value rests on (``PublicBasisV1``; never the value of that source). It carries no supplier, supplier part number,
price, cost, currency, physical shipping fact, evidence attribute name, raw evidence value or CAD asset digest.

Two publishers write the format:

* ``OPENSCI``: the OpenSci release (``mechanical_version`` ``m<catalog_revision>.<12 hex>``, ``OS-MECH-*`` identities,
  at least one breadboard, the resolver policy). It is distributed as osc1 canonical bytes, and its ``sha256`` over
  those exact bytes is the release identity every plan binds.
* ``USER``: hardware a customer describes (``mechanical_version`` a short lower-case label, ``USER-MECH-*``
  identities, no policy: the OpenSci policy, else the ``PublicResolverPolicyV1`` defaults, applies). Any UTF-8 JSON
  layout is accepted; its ``sha256`` is taken over the bytes as given.

``content_digest`` is the osc1 digest of the parsed JSON document without ``content_digest`` (not of a model dump):
the OpenSci generator writes every field whose value is not None explicitly, defaults included, and omits None-valued
keys, and a reader treats an absent optional key as None. An identity whose members disagree is published as ONE
conservative profile (``IdentityProfileV1``; envelope union, thickness and insertion intersection, smallest clear
aperture and travel, ...); the Engine reads the profile only.

Part frames (``frames = "opensci.mechanical_part_frames.v1"``) are yaw-only with +z up. POST: origin at the bottom face
centre, the top stud face at z = length. POST_HOLDER: origin at the bottom face centre on the post axis, the bore
opening at z = height, the fork along +x. MOUNT: origin at the post-interface point; +x is the optical axis of the
held optic (a reflector: its outward face normal); the seat centre is at ``(seat_stop_x or 0, 0, centre_height)``.
BREADBOARD: origin at a board corner on its bottom face; the top at z = thickness; holes at
``(edge + i * pitch, edge + j * pitch)`` on the top.

Everything here is pure: documents arrive as bytes or parsed JSON (the local store, the ``--mechanical`` files and the
``OS_OPTICS_MECHANICAL_CATALOG`` variable are read by ``os_optics.catalog.mechanical``), and ``MechanicalSources`` is
the set of documents one command uses.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, ClassVar, Literal, Union

from pydantic import Field, StringConstraints, ValidationError, field_validator, model_validator

from ..._vendor.shared_contracts.base import StrictContractModel
from ..._vendor.shared_contracts.common.frozen_string_map import FrozenStringMap
from ..._vendor.shared_contracts.common.hashes import Sha256Digest
from ..._vendor.shared_contracts.common.osc1 import Osc1Error, canonicalize_osc1_bytes, canonicalize_osc1_text, osc1_digest
from ..._vendor.shared_contracts.common.timestamps import UtcTimestamp

MECHANICAL_CATALOG_SCHEMA_ID = "opensci.public_mechanical_catalog"
MECHANICAL_CATALOG_SCHEMA_VERSION = "1.0"
#: The document schema versions this Engine reads (a newer minor version is refused, never read partly).
SUPPORTED_MECHANICAL_SCHEMA_VERSIONS = ("1.0",)
MECHANICAL_PART_FRAMES = "opensci.mechanical_part_frames.v1"
MECHANICAL_MANIFEST_SCHEMA_ID = "opensci.public_mechanical_catalog.manifest"
MECHANICAL_MANIFEST_VERSION = "1.0"
#: The field ``MechanicalCatalogManifestV1.release_path_template`` names exactly once.
RELEASE_PATH_FIELD = "{mechanical_version}"

PUBLISHER_OPENSCI = "OPENSCI"
PUBLISHER_USER = "USER"
OPENSCI_SKU_PATTERN = r"^OS-MECH(-[A-Z0-9]+)+$"
USER_SKU_PATTERN = r"^USER-MECH(-[A-Z0-9]+)+$"
ANY_SKU_PATTERN = r"^(OS|USER)-MECH(-[A-Z0-9]+)+$"
OPENSCI_VERSION_PATTERN = r"^m[1-9][0-9]*\.[0-9a-f]{12}$"
USER_VERSION_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,63}$"
#: The scene's ``DisplayText`` grammar (an identity name is shown in scenes and drawings).
DISPLAY_NAME_PATTERN = r"^[A-Za-z0-9 ,.()/+=\-x]+$"
THREAD_PATTERN = r"^[A-Za-z0-9./\-x]+$"

MAX_SKU_CHARS = 120
MAX_VERSION_CHARS = 80
MAX_NAME_CHARS = 160
MAX_DESCRIPTION_CHARS = 240
MAX_THREAD_CHARS = 40
MAX_IDENTITIES = 4096
MAX_VISUAL_PRIMITIVES = 64
#: At most this many USER documents (besides one OPENSCI document) are used together.
MAX_USER_DOCUMENTS = 8
MAX_DOCUMENT_BYTES = 16 * 1024 * 1024
MAX_REPORTED_PROBLEMS = 10

# Resolver policy defaults and bounds (the private resolver policy's, without its price fields).
DEFAULT_PREFERRED_BEAM_HEIGHT_MM = 125.0
DEFAULT_BEAM_HEIGHT_STEP_MM = 0.5
DEFAULT_MIN_BEAM_HEIGHT_MM = 25.0
DEFAULT_MAX_BEAM_HEIGHT_MM = 400.0
DEFAULT_BOARD_EDGE_MARGIN_MM = 12.5
DEFAULT_INTER_CHAIN_CLEARANCE_MM = 0.5
DEFAULT_BEAM_CLEARANCE_MM = 0.0
DEFAULT_PARALLEL_TOLERANCE = 1e-9
DEFAULT_HORIZONTAL_TOLERANCE = 1e-6
DEFAULT_MAX_CHAIN_PLACEMENTS = 12000
DEFAULT_MAX_GEOMETRY_CHECKS = 3_000_000
DEFAULT_MAX_MOUNT_ATTEMPTS = 64
DEFAULT_MAX_TRANSLATIONS = 64
DEFAULT_MAX_LAYOUT_BACKTRACKS = 256
DEFAULT_DIAGNOSIS_MAX_GEOMETRY_CHECKS = 400_000
DEFAULT_MAX_BEAM_BRANCHES = 16
DEFAULT_MAX_BEAM_POINTS = 64
DEFAULT_OBLIQUE_BEAM_FOOTPRINT_MM = 1.0
DEFAULT_FALLBACK_CLEAR_APERTURE_RATIO = 0.85
DEFAULT_OBLIQUE_GLASS_INDEX = 1.4
DEFAULT_MAX_TILT_DEG = 30.0
MAX_TILT_BOUND_DEG = 30.0
DEFAULT_SIZE_TOLERANCE_MM = 0.1
MIN_BEAM_POINTS = 2
#: Floats that must agree (identity-defining parameters) agree within this (mm); the generator's rule, used by tests.
FACT_AGREEMENT_MM = 1e-6

Positive = Annotated[float, Field(gt=0, strict=True)]
NonNegative = Annotated[float, Field(ge=0, strict=True)]
Finite = Annotated[float, Field(strict=True)]
UnitInterval = Annotated[float, Field(ge=0, le=1, strict=True)]
Vector3 = tuple[Finite, Finite, Finite]
Thread = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=MAX_THREAD_CHARS, pattern=THREAD_PATTERN)]
IdentitySku = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=MAX_SKU_CHARS, pattern=ANY_SKU_PATTERN)]
MechanicalVersion = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=MAX_VERSION_CHARS)]
OpenSciMechanicalVersion = Annotated[str, StringConstraints(strict=True, pattern=OPENSCI_VERSION_PATTERN)]
DisplayName = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=MAX_NAME_CHARS, pattern=DISPLAY_NAME_PATTERN)]
LocalName = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=MAX_NAME_CHARS)]
Description = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=MAX_DESCRIPTION_CHARS)]
Publisher = Literal["OPENSCI", "USER"]


# ------------------------------------------------------------------------------------------------------------ errors


class MechanicalErrorCode(StrEnum):
    """Why a mechanical document or a set of them cannot be used.

    * ``MECHANICAL_CATALOG_INVALID``: not a valid ``opensci.public_mechanical_catalog`` document (``problems`` lists
      the validation errors), or more documents than may be used together.
    * ``MECHANICAL_SCHEMA_UNSUPPORTED``: a schema version this Engine does not read (``details.supported``).
    * ``MECHANICAL_CATALOG_DIGEST_MISMATCH``: ``content_digest`` differs from the document's own digest.
    * ``MECHANICAL_CATALOG_NOT_CANONICAL``: an OPENSCI document whose bytes are not its osc1 canonical form.
    * ``MECHANICAL_IDENTITY_DUPLICATE``: two documents used together describe the same identity.
    * ``MECHANICAL_CATALOG_NOT_AVAILABLE``: a document a command needs (the one a plan binds) is not on this device.
    * ``MECHANICAL_FILE_UNREADABLE``: a file a caller named cannot be read.
    """

    MECHANICAL_CATALOG_INVALID = "MECHANICAL_CATALOG_INVALID"
    MECHANICAL_SCHEMA_UNSUPPORTED = "MECHANICAL_SCHEMA_UNSUPPORTED"
    MECHANICAL_CATALOG_DIGEST_MISMATCH = "MECHANICAL_CATALOG_DIGEST_MISMATCH"
    MECHANICAL_CATALOG_NOT_CANONICAL = "MECHANICAL_CATALOG_NOT_CANONICAL"
    MECHANICAL_IDENTITY_DUPLICATE = "MECHANICAL_IDENTITY_DUPLICATE"
    MECHANICAL_CATALOG_NOT_AVAILABLE = "MECHANICAL_CATALOG_NOT_AVAILABLE"
    MECHANICAL_FILE_UNREADABLE = "MECHANICAL_FILE_UNREADABLE"


class MechanicalCatalogError(ValueError):
    """A mechanical document problem: ``code`` (``MechanicalErrorCode``), ``message``, ``details``, ``problems``."""

    def __init__(self, code: MechanicalErrorCode | str, message: str, details: Mapping[str, Any] | None = None,
                 problems: Iterable[Mapping[str, Any]] = ()) -> None:
        self.code = MechanicalErrorCode(code)
        self.message = message
        self.details = dict(details or {})
        self.problems = [dict(p) for p in problems]
        super().__init__(f"{self.code.value}: {message}")

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code.value, "message": self.message, "details": self.details, "problems": self.problems}


# ------------------------------------------------------------------------------------------------------------ enums


class MechanicalKindV1(StrEnum):
    """The identity kinds of schema 1.0 (``ADAPTER`` / ``OTHER`` are reserved for a later minor version)."""

    POST = "POST"
    POST_HOLDER = "POST_HOLDER"
    MOUNT = "MOUNT"
    BREADBOARD = "BREADBOARD"


class PublicBasisV1(StrEnum):
    """What a published parameter value rests on (never the value of that source).

    ``DATASHEET`` a structured supplier datasheet fact; ``REVIEWED_REFERENCE`` a reviewed engineering reference fact;
    ``CAD_DERIVED`` measured from a CAD model by a recorded derivation; ``FAMILY_RULE`` an explicit reviewed family rule;
    ``PREVIEW_ASSUMPTION`` an assumed value for preview-grade realization (always disclosed, no fact)."""

    DATASHEET = "DATASHEET"
    REVIEWED_REFERENCE = "REVIEWED_REFERENCE"
    CAD_DERIVED = "CAD_DERIVED"
    FAMILY_RULE = "FAMILY_RULE"
    PREVIEW_ASSUMPTION = "PREVIEW_ASSUMPTION"


BASIS_VALUES = frozenset(b.value for b in PublicBasisV1)
#: The bases that are facts (everything but an assumption).
FACT_BASES = frozenset({PublicBasisV1.DATASHEET, PublicBasisV1.REVIEWED_REFERENCE, PublicBasisV1.CAD_DERIVED,
                        PublicBasisV1.FAMILY_RULE})
#: Weakest first: a merged parameter takes the weakest basis of its members.
BASIS_STRENGTH_ORDER = (PublicBasisV1.PREVIEW_ASSUMPTION, PublicBasisV1.FAMILY_RULE, PublicBasisV1.CAD_DERIVED,
                        PublicBasisV1.REVIEWED_REFERENCE, PublicBasisV1.DATASHEET)


def weakest_basis(bases: Iterable[PublicBasisV1 | str]) -> PublicBasisV1 | None:
    """The weakest of ``bases`` (``BASIS_STRENGTH_ORDER``); None for none."""

    found = {PublicBasisV1(b) for b in bases}
    return next((b for b in BASIS_STRENGTH_ORDER if b in found), None)


class MountKindV1(StrEnum):
    FIXED = "FIXED"
    KINEMATIC = "KINEMATIC"
    XY_TRANSLATION = "XY_TRANSLATION"
    ROTATION = "ROTATION"
    CUBE_PLATFORM = "CUBE_PLATFORM"


class OpticShapeV1(StrEnum):
    ROUND = "ROUND"
    RECTANGULAR = "RECTANGULAR"
    CUBE = "CUBE"


class MechanismKindV1(StrEnum):
    TIP_TILT = "TIP_TILT"
    TRANSVERSE_TRANSLATION = "TRANSVERSE_TRANSLATION"
    AXIAL_ROTATION = "AXIAL_ROTATION"


class PostFasteningV1(StrEnum):
    """How a delivered mount is fixed to its post: the post's stud in the mount's tapped base hole (``PLAIN_STUD``), the
    mount's cap screw through its base into the post's top thread (``CAP_SCREW_THROUGH_BASE``), or the mount standing
    on the face with the tapped hole (``STUD_INTO_TAPPED_FACE``)."""

    CAP_SCREW_THROUGH_BASE = "CAP_SCREW_THROUGH_BASE"
    PLAIN_STUD = "PLAIN_STUD"
    STUD_INTO_TAPPED_FACE = "STUD_INTO_TAPPED_FACE"


#: The mechanisms a mount kind provides, exactly.
MOUNT_KIND_MECHANISMS = {
    MountKindV1.FIXED: frozenset(), MountKindV1.CUBE_PLATFORM: frozenset(),
    MountKindV1.KINEMATIC: frozenset({MechanismKindV1.TIP_TILT}),
    MountKindV1.XY_TRANSLATION: frozenset({MechanismKindV1.TRANSVERSE_TRANSLATION}),
    MountKindV1.ROTATION: frozenset({MechanismKindV1.AXIAL_ROTATION}),
}

#: The parameters (basis keys) of each kind: exactly what the assembly resolver reads.
PARAM_KEYS = {
    MechanicalKindV1.POST: frozenset({"diameter_mm", "length_mm", "top_thread"}),
    MechanicalKindV1.POST_HOLDER: frozenset({"post_diameter_mm", "height_mm", "tube_diameter_mm", "insertion_min_mm",
                                             "insertion_max_mm", "base.slot", "base.thread", "envelope"}),
    MechanicalKindV1.MOUNT: frozenset({
        "centre_height_mm", "envelope", "holds.size_mm", "holds.thickness", "holds.thickness_min_mm",
        "holds.thickness_max_mm", "holds.transmissive", "holds.clear_aperture_mm", "holds.retainer_depth_mm",
        "holds.rear_depth_mm", "post_thread", "seat_stop_x_mm", "mechanism.TIP_TILT.travel",
        "mechanism.TRANSVERSE_TRANSLATION.travel", "mechanism.AXIAL_ROTATION.travel"}),
    MechanicalKindV1.BREADBOARD: frozenset({"size_x_mm", "size_y_mm", "thickness_mm", "pitch_mm", "edge_offset_mm",
                                            "thread", "envelope"}),
}


# ------------------------------------------------------------------------------------------------------------ shapes


class BoxV1(StrictContractModel):
    """An axis-aligned box in a part's own frame (mm), ``min_mm < max_mm`` on every axis."""

    min_mm: Vector3
    max_mm: Vector3

    @model_validator(mode="after")
    def _ordered(self) -> BoxV1:
        if any(lo >= hi for lo, hi in zip(self.min_mm, self.max_mm, strict=True)):
            raise ValueError("a box needs min_mm < max_mm on every axis")
        return self


class VisualPrimitiveV1(StrictContractModel):
    """A neutral visual primitive in the part's own frame (mm): a BOX (``min_mm``, ``max_mm``) or a CYLINDER / tube
    (``base_mm``, ``axis``, ``length_mm``, ``radius_mm``, ``inner_radius_mm`` below ``radius_mm``)."""

    kind: Literal["BOX", "CYLINDER"]
    finish: Literal["BODY", "DARK", "GLASS", "METAL"]
    min_mm: Vector3 | None = None
    max_mm: Vector3 | None = None
    base_mm: Vector3 | None = None
    axis: Literal["x", "y", "z"] | None = None
    length_mm: Positive | None = None
    radius_mm: Positive | None = None
    inner_radius_mm: NonNegative = 0.0

    @model_validator(mode="after")
    def _shape(self) -> VisualPrimitiveV1:
        box = (self.min_mm, self.max_mm)
        cylinder = (self.base_mm, self.axis, self.length_mm, self.radius_mm)
        if self.kind == "BOX":
            if any(v is None for v in box) or any(v is not None for v in cylinder) or self.inner_radius_mm:
                raise ValueError("a BOX has min_mm and max_mm only")
            if any(lo >= hi for lo, hi in zip(self.min_mm, self.max_mm, strict=True)):  # type: ignore[arg-type]
                raise ValueError("a BOX needs min_mm < max_mm on every axis")
        else:
            if any(v is None for v in cylinder) or any(v is not None for v in box):
                raise ValueError("a CYLINDER has base_mm, axis, length_mm and radius_mm only")
            if self.inner_radius_mm >= self.radius_mm:  # type: ignore[operator]
                raise ValueError("a tube's inner_radius_mm must be below radius_mm")
        return self

    def payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)

    def sort_key(self) -> str:
        """The canonical order of a primitive list: by the osc1 text of the payload."""

        return canonicalize_osc1_text(self.payload())


def canonical_visual(primitives: Iterable[VisualPrimitiveV1]) -> tuple[VisualPrimitiveV1, ...]:
    """``primitives`` deduplicated (equal payloads) and in canonical order (the generator's rule)."""

    unique = {p.sort_key(): p for p in primitives}
    return tuple(unique[key] for key in sorted(unique))


class IdentityProfileV1(StrictContractModel):
    """``SINGLE``: one member, or all members agree. ``CONSERVATIVE``: the published values are the conservative merge
    of several members; ``conservative_parameters`` names the parameters whose value differs from some member's."""

    kind: Literal["SINGLE", "CONSERVATIVE"]
    conservative_parameters: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _parameters(self) -> IdentityProfileV1:
        params = self.conservative_parameters
        if list(params) != sorted(set(params)):
            raise ValueError("conservative_parameters must be sorted and unique")
        if (self.kind == "CONSERVATIVE") != bool(params):
            raise ValueError("conservative_parameters is non-empty exactly for a CONSERVATIVE profile")
        return self


class ForkBaseV1(StrictContractModel):
    """An integrated clamping fork: a slot along the holder's +x whose centreline runs from ``slot_start_mm`` to
    ``slot_end_mm`` (distances from the post axis), fixed by one screw into a board hole on that centreline."""

    kind: Literal["INTEGRATED_FORK"]
    slot_start_mm: Positive
    slot_end_mm: Positive
    slot_width_mm: Positive
    thread: Thread
    body: BoxV1

    @model_validator(mode="after")
    def _slot(self) -> ForkBaseV1:
        if self.slot_end_mm <= self.slot_start_mm:
            raise ValueError("a fork slot ends after it starts")
        return self


class MountMechanismV1(StrictContractModel):
    """An adjustment of the mount in its own frame: ``TIP_TILT`` rotations about +y and +z over ``[-travel, +travel]``
    degrees; ``TRANSVERSE_TRANSLATION`` translations along +y and +z over ``[-travel, +travel]`` mm;
    ``AXIAL_ROTATION`` rotation about +x, ``travel`` the half range in degrees (180 = a full turn)."""

    kind: MechanismKindV1
    travel: Positive
    control: Literal["MANUAL"]
    adjustment: Literal["FINE_ADJUSTER", "CONTINUOUS_CLAMPED"]


class MountHoldsV1(StrictContractModel):
    """What a mount accepts. ROUND: ``size_mm`` is the optic diameter; RECTANGULAR: its short edge; CUBE: the cube edge.
    ``thickness_*``: the optic's axial thickness range, a None bound unlimited on that side; both None on a ROUND or
    RECTANGULAR mount means the range is not recorded (the resolver then rejects the mount). ``clear_aperture_mm``
    None: unknown (the resolver assumes ``policy.fallback_clear_aperture_ratio`` of the size and discloses it);
    ``retainer_depth_mm`` / ``rear_depth_mm`` None: taken from the envelope."""

    shape: OpticShapeV1
    size_mm: Positive
    size_tolerance_mm: NonNegative = DEFAULT_SIZE_TOLERANCE_MM
    thickness_min_mm: NonNegative | None = None
    thickness_max_mm: Positive | None = None
    transmissive: Annotated[bool, Field(strict=True)]
    clear_aperture_mm: Positive | None = None
    retainer_depth_mm: NonNegative | None = None
    rear_depth_mm: NonNegative | None = None

    @model_validator(mode="after")
    def _thickness(self) -> MountHoldsV1:
        lo, hi = self.thickness_min_mm, self.thickness_max_mm
        if lo is not None and hi is not None and lo > hi:
            raise ValueError("thickness_min_mm must not exceed thickness_max_mm")
        return self


# ------------------------------------------------------------------------------------------------------------ identities


class MechanicalIdentityBaseV1(StrictContractModel):
    """Fields every identity carries. ``basis`` maps a parameter of the kind (``PARAM_KEYS``) to its
    ``PublicBasisV1``; a parameter without an entry is NOT_RECORDED (``basis_of`` returns None)."""

    KIND: ClassVar[MechanicalKindV1]

    opensci_sku: IdentitySku
    display_name: DisplayName
    display_name_zh_cn: LocalName
    description: Description | None = None
    description_zh_cn: Description | None = None
    profile: IdentityProfileV1
    basis: FrozenStringMap = Field(default_factory=FrozenStringMap)
    visual: tuple[VisualPrimitiveV1, ...] = Field(min_length=1, max_length=MAX_VISUAL_PRIMITIVES)
    color_rgb: tuple[UnitInterval, UnitInterval, UnitInterval]

    @model_validator(mode="after")
    def _identity_rules(self) -> MechanicalIdentityBaseV1:
        keys = PARAM_KEYS[self.KIND]
        unknown = sorted(set(self.basis) - keys)
        if unknown:
            raise ValueError(f"basis names parameters a {self.KIND.value} does not have: {unknown}")
        bad = sorted(k for k, v in self.basis.items() if v not in BASIS_VALUES)
        if bad:
            raise ValueError(f"basis values must be one of {[b.value for b in PublicBasisV1]} (parameters {bad})")
        unknown = sorted(set(self.profile.conservative_parameters) - keys)
        if unknown:
            raise ValueError(f"conservative_parameters names parameters a {self.KIND.value} does not have: {unknown}")
        keys_in_order = [p.sort_key() for p in self.visual]
        if keys_in_order != sorted(set(keys_in_order)):
            raise ValueError("visual primitives must be unique and in canonical order (sorted by their osc1 text)")
        return self

    def basis_of(self, parameter: str) -> PublicBasisV1 | None:
        value = self.basis.get(parameter)
        return None if value is None else PublicBasisV1(value)

    def is_fact(self, parameter: str) -> bool:
        return self.basis_of(parameter) in FACT_BASES

    def payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)


class PostIdentityV1(MechanicalIdentityBaseV1):
    KIND: ClassVar[MechanicalKindV1] = MechanicalKindV1.POST

    kind: Literal["POST"]
    diameter_mm: Positive
    length_mm: Positive
    top_thread: Thread


class PostHolderIdentityV1(MechanicalIdentityBaseV1):
    """``insertion_min_mm <= insertion_max_mm <= height_mm`` (mm below the bore opening a post may be inserted)."""

    KIND: ClassVar[MechanicalKindV1] = MechanicalKindV1.POST_HOLDER

    kind: Literal["POST_HOLDER"]
    post_diameter_mm: Positive
    height_mm: Positive
    tube_diameter_mm: Positive
    insertion_min_mm: Positive
    insertion_max_mm: Positive
    base: ForkBaseV1

    @model_validator(mode="after")
    def _insertion(self) -> PostHolderIdentityV1:
        if not self.insertion_min_mm <= self.insertion_max_mm <= self.height_mm:
            raise ValueError("the insertion interval must lie within the holder height")
        return self


class MountIdentityV1(MechanicalIdentityBaseV1):
    """A FIXED / CUBE_PLATFORM mount provides no mechanism, KINEMATIC exactly TIP_TILT, XY_TRANSLATION exactly
    TRANSVERSE_TRANSLATION, ROTATION exactly AXIAL_ROTATION. ``post_fastening``: every way a delivered unit is fixed
    to its post (sorted, unique, non-empty)."""

    KIND: ClassVar[MechanicalKindV1] = MechanicalKindV1.MOUNT

    kind: Literal["MOUNT"]
    mount_kind: MountKindV1
    holds: MountHoldsV1
    post_thread: Thread
    post_fastening: tuple[PostFasteningV1, ...] = Field(min_length=1)
    centre_height_mm: Positive
    seat_stop_x_mm: Finite | None = None
    envelope: BoxV1
    mechanisms: tuple[MountMechanismV1, ...] = ()

    @model_validator(mode="after")
    def _mount_rules(self) -> MountIdentityV1:
        if list(self.post_fastening) != sorted(set(self.post_fastening)):
            raise ValueError("post_fastening must be sorted and unique")
        kinds = [m.kind for m in self.mechanisms]
        expected = MOUNT_KIND_MECHANISMS[self.mount_kind]
        if len(kinds) != len(set(kinds)):
            raise ValueError("duplicate mount mechanism")
        if set(kinds) != expected:
            raise ValueError(f"a {self.mount_kind.value} mount provides exactly {sorted(k.value for k in expected)}")
        return self

    def mechanism(self, kind: MechanismKindV1 | str) -> MountMechanismV1 | None:
        return next((m for m in self.mechanisms if m.kind == kind), None)


class BreadboardIdentityV1(MechanicalIdentityBaseV1):
    KIND: ClassVar[MechanicalKindV1] = MechanicalKindV1.BREADBOARD

    kind: Literal["BREADBOARD"]
    size_x_mm: Positive
    size_y_mm: Positive
    thickness_mm: Positive
    pitch_mm: Positive
    edge_offset_mm: Positive
    thread: Thread

    @property
    def hole_count(self) -> tuple[int, int]:
        """Holes per axis: ``round((size - 2 * edge_offset) / pitch) + 1``."""

        def count(size: float) -> int:
            return int(round((size - 2 * self.edge_offset_mm) / self.pitch_mm)) + 1

        return count(self.size_x_mm), count(self.size_y_mm)


MechanicalIdentityV1 = Annotated[
    Union[PostIdentityV1, PostHolderIdentityV1, MountIdentityV1, BreadboardIdentityV1],
    Field(discriminator="kind"),
]
IDENTITY_MODELS = {MechanicalKindV1.POST: PostIdentityV1, MechanicalKindV1.POST_HOLDER: PostHolderIdentityV1,
                   MechanicalKindV1.MOUNT: MountIdentityV1, MechanicalKindV1.BREADBOARD: BreadboardIdentityV1}


# ------------------------------------------------------------------------------------------------------------ policy


class PublicResolverPolicyV1(StrictContractModel):
    """The deterministic preferences and work budgets of the assembly resolver (choices, not facts); no price field.

    Beam heights and margins in mm; ``max_*`` counts bound the search (a refusal at one names it);
    ``fallback_clear_aperture_ratio`` is the free fraction assumed for a mount without a recorded clear aperture;
    ``oblique_glass_index`` the conservative glass index of the oblique-clearance check; ``max_plate_tilt_deg`` /
    ``max_lens_tilt_deg`` the largest intentional tilt at which a plate / lens is still on the beam path."""

    preferred_beam_height_mm: Positive = DEFAULT_PREFERRED_BEAM_HEIGHT_MM
    beam_height_step_mm: Positive = DEFAULT_BEAM_HEIGHT_STEP_MM
    min_beam_height_mm: Positive = DEFAULT_MIN_BEAM_HEIGHT_MM
    max_beam_height_mm: Positive = DEFAULT_MAX_BEAM_HEIGHT_MM
    board_edge_margin_mm: NonNegative = DEFAULT_BOARD_EDGE_MARGIN_MM
    inter_chain_clearance_mm: NonNegative = DEFAULT_INTER_CHAIN_CLEARANCE_MM
    beam_clearance_mm: NonNegative = DEFAULT_BEAM_CLEARANCE_MM
    parallel_tolerance: Positive = DEFAULT_PARALLEL_TOLERANCE
    horizontal_tolerance: Positive = DEFAULT_HORIZONTAL_TOLERANCE
    max_chain_placements: Annotated[int, Field(ge=1, strict=True)] = DEFAULT_MAX_CHAIN_PLACEMENTS
    max_geometry_checks: Annotated[int, Field(ge=1, strict=True)] = DEFAULT_MAX_GEOMETRY_CHECKS
    max_mount_attempts: Annotated[int, Field(ge=1, strict=True)] = DEFAULT_MAX_MOUNT_ATTEMPTS
    max_translations: Annotated[int, Field(ge=1, strict=True)] = DEFAULT_MAX_TRANSLATIONS
    max_layout_backtracks: Annotated[int, Field(ge=0, strict=True)] = DEFAULT_MAX_LAYOUT_BACKTRACKS
    diagnosis_max_geometry_checks: Annotated[int, Field(ge=1, strict=True)] = DEFAULT_DIAGNOSIS_MAX_GEOMETRY_CHECKS
    max_beam_branches: Annotated[int, Field(ge=1, strict=True)] = DEFAULT_MAX_BEAM_BRANCHES
    max_beam_points: Annotated[int, Field(ge=MIN_BEAM_POINTS, strict=True)] = DEFAULT_MAX_BEAM_POINTS
    oblique_beam_footprint_mm: NonNegative = DEFAULT_OBLIQUE_BEAM_FOOTPRINT_MM
    fallback_clear_aperture_ratio: Annotated[float, Field(gt=0, le=1, strict=True)] = DEFAULT_FALLBACK_CLEAR_APERTURE_RATIO
    oblique_glass_index: Annotated[float, Field(ge=1.0, strict=True)] = DEFAULT_OBLIQUE_GLASS_INDEX
    max_plate_tilt_deg: Annotated[float, Field(ge=0, le=MAX_TILT_BOUND_DEG, strict=True)] = DEFAULT_MAX_TILT_DEG
    max_lens_tilt_deg: Annotated[float, Field(ge=0, le=MAX_TILT_BOUND_DEG, strict=True)] = DEFAULT_MAX_TILT_DEG

    @model_validator(mode="after")
    def _heights(self) -> PublicResolverPolicyV1:
        if not self.min_beam_height_mm <= self.preferred_beam_height_mm <= self.max_beam_height_mm:
            raise ValueError("min_beam_height_mm <= preferred_beam_height_mm <= max_beam_height_mm is required")
        return self


# ------------------------------------------------------------------------------------------------------------ document


class PublicMechanicalCatalogV1(StrictContractModel):
    """One mechanical catalog document (module docstring). Validators: identities sorted and unique by
    ``opensci_sku``; OPENSCI: ``OS-MECH-*`` identities, ``m<rev>.<12 hex>`` version, a policy, at least one breadboard;
    USER: ``USER-MECH-*`` identities, a short lower-case version, no policy. ``content_digest`` is checked against the
    parsed JSON by ``load_mechanical_catalog`` (a model dump may add defaults a USER document left out)."""

    schema_id: Literal["opensci.public_mechanical_catalog"] = Field(alias="schema")
    schema_version: Literal["1.0"]
    publisher: Publisher
    mechanical_version: MechanicalVersion
    frames: Literal["opensci.mechanical_part_frames.v1"]
    policy: PublicResolverPolicyV1 | None = None
    identities: tuple[MechanicalIdentityV1, ...] = Field(min_length=1, max_length=MAX_IDENTITIES)
    content_digest: Sha256Digest

    @model_validator(mode="after")
    def _document_rules(self) -> PublicMechanicalCatalogV1:
        skus = [i.opensci_sku for i in self.identities]
        if skus != sorted(set(skus)):
            raise ValueError("identities must be sorted by opensci_sku and unique")
        opensci = self.publisher == PUBLISHER_OPENSCI
        version_pattern, sku_pattern = ((OPENSCI_VERSION_PATTERN, OPENSCI_SKU_PATTERN) if opensci
                                        else (USER_VERSION_PATTERN, USER_SKU_PATTERN))
        if not re.fullmatch(version_pattern, self.mechanical_version):
            raise ValueError(f"a {self.publisher} mechanical_version must match {version_pattern}")
        foreign = [s for s in skus if not re.fullmatch(sku_pattern, s)]
        if foreign:
            raise ValueError(f"a {self.publisher} document lists identities matching {sku_pattern} only: {foreign[:3]}")
        if opensci:
            if self.policy is None:
                raise ValueError("an OPENSCI document carries the resolver policy")
            if not any(i.kind == MechanicalKindV1.BREADBOARD for i in self.identities):
                raise ValueError("an OPENSCI document lists at least one BREADBOARD")
        elif self.policy is not None:
            raise ValueError("a USER document carries no policy (the OpenSci policy applies)")
        return self

    def payload(self) -> dict[str, Any]:
        """The JSON document (None-valued keys omitted, defaults explicit: the generator's form)."""

        return self.model_dump(mode="json", by_alias=True, exclude_none=True)

    def identity(self, opensci_sku: str) -> MechanicalIdentityBaseV1 | None:
        return next((i for i in self.identities if i.opensci_sku == opensci_sku), None)

    def of_kind(self, kind: MechanicalKindV1 | str) -> tuple[MechanicalIdentityBaseV1, ...]:
        return tuple(i for i in self.identities if i.kind == kind)


def mechanical_content_digest(document: Mapping[str, Any]) -> str:
    """The osc1 digest of a parsed mechanical document without its ``content_digest`` field."""

    return osc1_digest({k: v for k, v in document.items() if k != "content_digest"})


def seal_mechanical_document(document: Mapping[str, Any]) -> dict[str, Any]:
    """A copy of ``document`` with ``content_digest`` set to its digest (for writers of a document)."""

    body = {k: v for k, v in document.items() if k != "content_digest"}
    return {**body, "content_digest": osc1_digest(body)}


def canonical_mechanical_bytes(document: Mapping[str, Any] | PublicMechanicalCatalogV1) -> bytes:
    """The osc1 canonical bytes of a document (the distribution form of an OPENSCI release)."""

    payload = document.payload() if isinstance(document, PublicMechanicalCatalogV1) else dict(document)
    return canonicalize_osc1_bytes(payload)


def mechanical_catalog_json_schema() -> dict[str, Any]:
    """The JSON Schema of ``opensci.public_mechanical_catalog`` 1.0 (``os-optics schema public-mechanical-catalog``)."""

    return PublicMechanicalCatalogV1.model_json_schema(by_alias=True)


def _problems(exc: ValidationError) -> list[dict[str, Any]]:
    return [{"loc": ".".join(str(p) for p in e["loc"]) or "document", "msg": e["msg"], "type": e["type"]}
            for e in exc.errors()[:MAX_REPORTED_PROBLEMS]]


def _parse(data: bytes | str) -> tuple[bytes, dict[str, Any]]:
    raw = data.encode("utf-8") if isinstance(data, str) else bytes(data)
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise MechanicalCatalogError(MechanicalErrorCode.MECHANICAL_CATALOG_INVALID,
                                     f"a mechanical document is at most {MAX_DOCUMENT_BYTES} bytes ({len(raw)} given)",
                                     {"size_bytes": len(raw), "max_bytes": MAX_DOCUMENT_BYTES})
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise MechanicalCatalogError(MechanicalErrorCode.MECHANICAL_CATALOG_INVALID,
                                     f"the mechanical document is not valid UTF-8 JSON: {exc}") from None
    if not isinstance(document, dict) or document.get("schema") != MECHANICAL_CATALOG_SCHEMA_ID:
        raise MechanicalCatalogError(MechanicalErrorCode.MECHANICAL_CATALOG_INVALID,
                                     f"not a mechanical catalog document (schema {MECHANICAL_CATALOG_SCHEMA_ID} expected)")
    return raw, document


def load_mechanical_catalog(data: bytes | str, *, publisher: Publisher | None = None) -> PublicMechanicalCatalogV1:
    """Read and verify one mechanical document from its bytes.

    Checks, in order: size, UTF-8 JSON object of this schema id, a schema version this Engine reads
    (``MECHANICAL_SCHEMA_UNSUPPORTED``), the expected ``publisher`` when one is given, the canonical form of an OPENSCI
    document (``MECHANICAL_CATALOG_NOT_CANONICAL``), the model (``MECHANICAL_CATALOG_INVALID`` with ``problems``) and
    the content digest over the parsed JSON (``MECHANICAL_CATALOG_DIGEST_MISMATCH``)."""

    return _load(data, publisher)[0]


def _load(data: bytes | str, publisher: Publisher | None) -> tuple[PublicMechanicalCatalogV1, bytes]:
    raw, document = _parse(data)
    version = document.get("schema_version")
    if version not in SUPPORTED_MECHANICAL_SCHEMA_VERSIONS:
        raise MechanicalCatalogError(
            MechanicalErrorCode.MECHANICAL_SCHEMA_UNSUPPORTED,
            f"mechanical catalog schema version {version!r} is not read by this os-optics version; upgrade os-optics",
            {"schema_version": version, "supported": list(SUPPORTED_MECHANICAL_SCHEMA_VERSIONS),
             "newest_supported": SUPPORTED_MECHANICAL_SCHEMA_VERSIONS[-1]})
    if publisher is not None and document.get("publisher") != publisher:
        raise MechanicalCatalogError(MechanicalErrorCode.MECHANICAL_CATALOG_INVALID,
                                     f"a {publisher} mechanical document was expected (publisher {document.get('publisher')!r})",
                                     {"expected_publisher": publisher, "publisher": document.get("publisher")})
    if document.get("publisher") == PUBLISHER_OPENSCI:
        try:
            canonical = canonicalize_osc1_bytes(document)
        except Osc1Error as exc:
            raise MechanicalCatalogError(MechanicalErrorCode.MECHANICAL_CATALOG_INVALID,
                                         f"the mechanical document is outside the osc1 JSON domain: {exc}") from None
        if canonical != raw:
            raise MechanicalCatalogError(MechanicalErrorCode.MECHANICAL_CATALOG_NOT_CANONICAL,
                                         "an OPENSCI mechanical document is distributed as its osc1 canonical bytes")
    try:
        model = PublicMechanicalCatalogV1.model_validate(document)
    except ValidationError as exc:
        raise MechanicalCatalogError(MechanicalErrorCode.MECHANICAL_CATALOG_INVALID,
                                     f"not a valid mechanical catalog document ({exc.error_count()} problem(s))",
                                     problems=_problems(exc)) from None
    try:
        expected = mechanical_content_digest(document)
    except Osc1Error as exc:
        raise MechanicalCatalogError(MechanicalErrorCode.MECHANICAL_CATALOG_INVALID,
                                     f"the mechanical document is outside the osc1 JSON domain: {exc}") from None
    if expected != model.content_digest:
        raise MechanicalCatalogError(MechanicalErrorCode.MECHANICAL_CATALOG_DIGEST_MISMATCH,
                                     "content_digest differs from the digest of the document",
                                     {"content_digest": model.content_digest, "computed": expected})
    return model, raw


# ------------------------------------------------------------------------------------------------------------ manifest


class MechanicalReleaseEntryV1(StrictContractModel):
    mechanical_version: OpenSciMechanicalVersion
    schema_version: Literal["1.0"]
    sha256: Sha256Digest
    size_bytes: Annotated[int, Field(gt=0, le=MAX_DOCUMENT_BYTES, strict=True)]
    published_at: UtcTimestamp
    status: Literal["CURRENT", "RETAINED"]


class MechanicalCatalogManifestV1(StrictContractModel):
    """``GET /api/v1/catalog/mechanical``: the current release and the retained ones (newest first, ``current`` its only
    CURRENT entry), and where a release is fetched (``release_path_template`` names ``{mechanical_version}`` once)."""

    schema_id: Literal["opensci.public_mechanical_catalog.manifest"] = Field(alias="schema")
    manifest_version: Literal["1.0"]
    current: MechanicalReleaseEntryV1
    retained: tuple[MechanicalReleaseEntryV1, ...] = Field(min_length=1)
    release_path_template: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=MAX_NAME_CHARS)]

    @field_validator("release_path_template")
    @classmethod
    def _template(cls, value: str) -> str:
        if value.count(RELEASE_PATH_FIELD) != 1 or not value.startswith("/"):
            raise ValueError(f"release_path_template is an absolute path naming {RELEASE_PATH_FIELD} exactly once")
        return value

    @model_validator(mode="after")
    def _entries(self) -> MechanicalCatalogManifestV1:
        if self.current.status != "CURRENT":
            raise ValueError("the current release has status CURRENT")
        if [e for e in self.retained if e.status == "CURRENT"] != [self.current]:
            raise ValueError("retained lists the current release as its only CURRENT entry")
        versions = [e.mechanical_version for e in self.retained]
        if len(versions) != len(set(versions)):
            raise ValueError("retained lists each release once")
        published = [e.published_at for e in self.retained]
        if published != sorted(published, reverse=True):
            raise ValueError("retained is ordered newest first")
        return self

    def release_path(self, mechanical_version: str) -> str:
        return self.release_path_template.replace(RELEASE_PATH_FIELD, mechanical_version)


# ------------------------------------------------------------------------------------------------------------ sources


class MechanicalDocumentRefV1(StrictContractModel):
    """A document a plan binds: publisher, version and the SHA-256 of its exact bytes."""

    publisher: Publisher
    mechanical_version: MechanicalVersion
    sha256: Sha256Digest


class MechanicalSourceKind(StrEnum):
    """Where a document of ``MechanicalSources`` came from."""

    FILE = "FILE"  # --mechanical FILE
    ENVIRONMENT = "ENVIRONMENT"  # OS_OPTICS_MECHANICAL_CATALOG
    STORE = "STORE"  # the local catalog store (the release a plan binds, else the active one)
    PLAN = "PLAN"  # a USER document copied into the plan directory
    DIRECT = "DIRECT"  # bytes a Python caller passed in


@dataclass(frozen=True)
class LoadedMechanicalDocument:
    """A verified document with the identity of its bytes and where it came from."""

    document: PublicMechanicalCatalogV1
    sha256: str
    size_bytes: int
    source: MechanicalSourceKind
    location: str | None = None

    @property
    def publisher(self) -> str:
        return self.document.publisher

    def ref(self) -> MechanicalDocumentRefV1:
        return MechanicalDocumentRefV1(publisher=self.document.publisher, mechanical_version=self.document.mechanical_version,
                                       sha256=self.sha256)


def load_mechanical_document(data: bytes | str, *, source: MechanicalSourceKind | str = MechanicalSourceKind.DIRECT,
                             location: str | Path | None = None,
                             publisher: Publisher | None = None) -> LoadedMechanicalDocument:
    """``load_mechanical_catalog`` plus the SHA-256 and size of the exact bytes; a failure names ``location``."""

    try:
        model, raw = _load(data, publisher)
    except MechanicalCatalogError as exc:
        if location is not None:
            exc.details.setdefault("location", str(location))
        raise
    return LoadedMechanicalDocument(document=model, sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw),
                                    source=MechanicalSourceKind(source), location=None if location is None else str(location))


@dataclass(frozen=True)
class MechanicalPlanBinding:
    """What a plan command passes to source resolution: the documents the plan binds and the directory holding the
    plan's copies of its USER documents (``<sha256>.json``), so a plan reopens offline."""

    refs: tuple[MechanicalDocumentRefV1, ...]
    user_copies_dir: Path | None = None

    @property
    def opensci(self) -> MechanicalDocumentRefV1 | None:
        return next((r for r in self.refs if r.publisher == PUBLISHER_OPENSCI), None)

    @property
    def users(self) -> tuple[MechanicalDocumentRefV1, ...]:
        return tuple(r for r in self.refs if r.publisher == PUBLISHER_USER)


def _ordered(documents: Iterable[LoadedMechanicalDocument]) -> tuple[LoadedMechanicalDocument, ...]:
    unique = {d.sha256: d for d in documents}
    return tuple(sorted(unique.values(), key=lambda d: (d.publisher != PUBLISHER_OPENSCI, d.sha256)))


@dataclass(frozen=True)
class MechanicalSources:
    """The mechanical documents one command uses: at most one OPENSCI document and up to ``MAX_USER_DOCUMENTS`` USER
    documents with disjoint identities, OPENSCI first, then by SHA-256 (the plan binding order). Identical bytes given
    twice count once. ``policy`` is the OPENSCI document's, else the ``PublicResolverPolicyV1`` defaults."""

    documents: tuple[LoadedMechanicalDocument, ...] = ()

    def __post_init__(self) -> None:
        documents = _ordered(self.documents)
        object.__setattr__(self, "documents", documents)
        opensci = [d for d in documents if d.publisher == PUBLISHER_OPENSCI]
        users = [d for d in documents if d.publisher == PUBLISHER_USER]
        if len(opensci) > 1:
            raise MechanicalCatalogError(MechanicalErrorCode.MECHANICAL_CATALOG_INVALID,
                                         "at most one OPENSCI mechanical document is used at a time",
                                         {"opensci_documents": [d.location or d.sha256 for d in opensci]})
        if len(users) > MAX_USER_DOCUMENTS:
            raise MechanicalCatalogError(MechanicalErrorCode.MECHANICAL_CATALOG_INVALID,
                                         f"at most {MAX_USER_DOCUMENTS} USER mechanical documents are used at a time",
                                         {"user_documents": len(users)})
        seen: dict[str, LoadedMechanicalDocument] = {}
        for doc in documents:
            for identity in doc.document.identities:
                first = seen.setdefault(identity.opensci_sku, doc)
                if first is not doc:
                    raise MechanicalCatalogError(
                        MechanicalErrorCode.MECHANICAL_IDENTITY_DUPLICATE,
                        f"{identity.opensci_sku} is described by two mechanical documents",
                        {"opensci_sku": identity.opensci_sku,
                         "documents": [first.location or first.sha256, doc.location or doc.sha256]})

    @classmethod
    def of(cls, *documents: LoadedMechanicalDocument | bytes | str) -> MechanicalSources:
        """Sources from loaded documents or raw bytes (a Python caller's direct input)."""

        return cls(tuple(d if isinstance(d, LoadedMechanicalDocument) else load_mechanical_document(d) for d in documents))

    @property
    def opensci(self) -> LoadedMechanicalDocument | None:
        return next((d for d in self.documents if d.publisher == PUBLISHER_OPENSCI), None)

    @property
    def users(self) -> tuple[LoadedMechanicalDocument, ...]:
        return tuple(d for d in self.documents if d.publisher == PUBLISHER_USER)

    @property
    def policy(self) -> PublicResolverPolicyV1:
        opensci = self.opensci
        policy = None if opensci is None else opensci.document.policy
        return PublicResolverPolicyV1() if policy is None else policy

    @property
    def empty(self) -> bool:
        return not self.documents

    def binding(self) -> tuple[MechanicalDocumentRefV1, ...]:
        """The refs a plan records (``PlanBindingsV1.mechanical``), in the binding order."""

        return tuple(d.ref() for d in self.documents)

    def identities(self) -> tuple[MechanicalIdentityBaseV1, ...]:
        """Every identity of every document, sorted by ``opensci_sku``."""

        return tuple(sorted((i for d in self.documents for i in d.document.identities), key=lambda i: i.opensci_sku))

    def identity(self, opensci_sku: str) -> MechanicalIdentityBaseV1 | None:
        for doc in self.documents:
            found = doc.document.identity(opensci_sku)
            if found is not None:
                return found
        return None

    def publisher_of(self, opensci_sku: str) -> str | None:
        return next((d.publisher for d in self.documents if d.document.identity(opensci_sku) is not None), None)

    def require(self) -> MechanicalSources:
        """``self`` when it holds a document, else ``MECHANICAL_CATALOG_NOT_AVAILABLE`` with the recovery."""

        if self.empty:
            raise MechanicalCatalogError(
                MechanicalErrorCode.MECHANICAL_CATALOG_NOT_AVAILABLE,
                "no mechanical catalog is available on this device: run 'os-optics catalog sync' or pass --mechanical FILE",
                {"recovery": ["os-optics catalog sync", "--mechanical FILE"]})
        return self
