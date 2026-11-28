"""Assembly Preview Package manifest: identity, binding, usage terms and file digests.

The manifest (``manifest.json``) is the package's single identity record:

* ``binding`` ties the package to exactly one design / resolution: the Project hash (osc1, the same hash
  the user's Local Validation names), the Local Validation record hash, the commercial submission hash
  (when present), the resolution hash, and the Customer Purchase BOM hash (when
  one exists);
* ``usage`` states the terms of use of the package (``UsageTermsV1``);
* ``files`` lists every other file with its size and SHA-256;
* ``package_content_hash`` is the osc1 digest of the manifest payload without that field, so it covers
  the binding, the revision, the usage terms and every file digest.

Schema 3.1 adds a second binding basis: ``binding.basis = LOCAL_PLAN``, a package the user's own runtime generated on
the user's device from a local assembly plan and public catalog data. It names the plan revision it was generated from
(``LocalPlanRefV1``: plan id, revision, the plan digest and the digest of the plan's design BOM, whose line ids the
scene's ``commercial_line_ref`` values are) instead of a resolution, a commercial submission, a Customer Purchase BOM
or an Order, and it carries the local terms of use (``LocalUsageTermsV1``: viewing, checking and drawing; the user may
pass it on). A manifest states 3.0 exactly for the commercial binding and 3.1 exactly for the local one, so every 3.0
package keeps its bytes and its hash.

The package is plain bytes addressed by relative paths. The protected delivery (``protected_assembly_delivery``)
wraps the archive bytes in an encrypted envelope bound to one Project and resolution; the inner
``package_content_hash`` stays the package identity.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, field_validator, model_validator

from ..base import StrictContractModel
from ..common.hashes import HashProfile, HashRef, Sha256Digest, osc1_digest, require_hash_profile
from ..common.identifiers import OpenSciId
from ..optical_design.local_validation import ValidatedProjectRefV1


ASSEMBLY_PREVIEW_MANIFEST_SCHEMA_ID: Literal["opensci.assembly_preview.manifest"] = "opensci.assembly_preview.manifest"
ASSEMBLY_PREVIEW_MANIFEST_SCHEMA_VERSION: Literal["3.0"] = "3.0"
#: 3.1: the LOCAL_PLAN binding basis and its usage terms (``LocalPlanRefV1``, ``LocalUsageTermsV1``).
ASSEMBLY_PREVIEW_MANIFEST_SCHEMA_VERSION_3_1: Literal["3.1"] = "3.1"
ASSEMBLY_PREVIEW_MANIFEST_SCHEMA_VERSIONS: tuple[str, ...] = (ASSEMBLY_PREVIEW_MANIFEST_SCHEMA_VERSION,
                                                              ASSEMBLY_PREVIEW_MANIFEST_SCHEMA_VERSION_3_1)
PREVIEW_USAGE_STATEMENT: Literal[
    "Assembly data of this Project's resolved assembly, for viewing and checking the assembly. Not for redistribution."
] = "Assembly data of this Project's resolved assembly, for viewing and checking the assembly. Not for redistribution."
LOCAL_PREVIEW_USAGE_STATEMENT: Literal[
    "Assembly data generated on this device from your own assembly plan and public catalog data, for viewing, checking "
    "and drawing the assembly."
] = (
    "Assembly data generated on this device from your own assembly plan and public catalog data, for viewing, checking "
    "and drawing the assembly."
)
#: The binding basis of a package generated on the user's device from a local assembly plan (schema 3.1).
LOCAL_PLAN_BASIS: Literal["LOCAL_PLAN"] = "LOCAL_PLAN"

MANIFEST_PATH = "manifest.json"
SCENE_PATH = "scene.json"
JSON_MEDIA_TYPE: Literal["application/json"] = "application/json"
GLB_MEDIA_TYPE: Literal["model/gltf-binary"] = "model/gltf-binary"
MAX_PACKAGE_FILES = 257

RESOLUTION_HASH_SUBJECT = "assembly_resolution"
LOCAL_VALIDATION_HASH_SUBJECT = "local_validation_record"
COMMERCIAL_SUBMISSION_HASH_SUBJECT = "commercial_submission"
COMMERCIAL_BOM_HASH_SUBJECT = "customer_purchase_bom"
PLAN_REVISION_HASH_SUBJECT = "assembly_plan_revision"
DESIGN_BOM_HASH_SUBJECT = "design_bom"

PackagePath = Annotated[
    str,
    StringConstraints(strict=True, max_length=80, pattern=r"^(scene\.json|geometry/[a-z][a-z0-9]*(-[a-z0-9]+)*\.glb)$"),
    Field(description="Relative path inside the package: 'scene.json' or 'geometry/<part_ref>.glb'."),
]


class PermittedUseV1(StrEnum):
    """What a delivered assembly package may be used for."""

    VIEW_ASSEMBLY = "VIEW_ASSEMBLY"
    VALIDATE_ASSEMBLY = "VALIDATE_ASSEMBLY"
    GENERATE_ASSEMBLY_DRAWING = "GENERATE_ASSEMBLY_DRAWING"


PREVIEW_PERMITTED_USES: tuple[PermittedUseV1, ...] = (PermittedUseV1.VIEW_ASSEMBLY, PermittedUseV1.VALIDATE_ASSEMBLY)
LOCAL_PREVIEW_PERMITTED_USES: tuple[PermittedUseV1, ...] = (PermittedUseV1.VIEW_ASSEMBLY, PermittedUseV1.VALIDATE_ASSEMBLY,
                                                            PermittedUseV1.GENERATE_ASSEMBLY_DRAWING)


class UsageTermsV1(StrictContractModel):
    """Terms of use of an Assembly Preview Package: this Project's assembly, viewing and checking, no redistribution."""

    scope: Literal["THIS_PROJECT"] = "THIS_PROJECT"
    permitted_uses: tuple[PermittedUseV1, ...] = PREVIEW_PERMITTED_USES
    redistribution: Literal["NOT_PERMITTED"] = "NOT_PERMITTED"
    statement: Literal[
        "Assembly data of this Project's resolved assembly, for viewing and checking the assembly. Not for redistribution."
    ] = PREVIEW_USAGE_STATEMENT

    @model_validator(mode="after")
    def _complete(self) -> UsageTermsV1:
        if self.permitted_uses != PREVIEW_PERMITTED_USES:
            raise ValueError("permitted_uses of an assembly preview package are exactly (VIEW_ASSEMBLY, VALIDATE_ASSEMBLY)")
        return self


class LocalUsageTermsV1(StrictContractModel):
    """Terms of use of a package generated on the user's device from the user's own assembly plan (schema 3.1): viewing,
    checking and drawing this plan's assembly; the user may pass the package on."""

    scope: Literal["THIS_PLAN"] = "THIS_PLAN"
    permitted_uses: tuple[PermittedUseV1, ...] = LOCAL_PREVIEW_PERMITTED_USES
    redistribution: Literal["PERMITTED"] = "PERMITTED"
    statement: Literal[
        "Assembly data generated on this device from your own assembly plan and public catalog data, for viewing, checking "
        "and drawing the assembly."
    ] = LOCAL_PREVIEW_USAGE_STATEMENT

    @model_validator(mode="after")
    def _complete(self) -> LocalUsageTermsV1:
        if self.permitted_uses != LOCAL_PREVIEW_PERMITTED_USES:
            raise ValueError("permitted_uses of a locally generated assembly preview package are exactly (VIEW_ASSEMBLY, "
                             "VALIDATE_ASSEMBLY, GENERATE_ASSEMBLY_DRAWING)")
        return self


def _osc1_subject(value: HashRef | None, subject: str, field_name: str) -> HashRef | None:
    if value is None:
        return None
    require_hash_profile(value, HashProfile.OSC1, field_name=field_name)
    if value.subject != subject:
        raise ValueError(f"{field_name} subject must be {subject!r}")
    return value


class ResolutionRefV1(StrictContractModel):
    """The Assembly Resolution this package realizes, by its hash."""

    resolution_hash: HashRef

    @field_validator("resolution_hash")
    @classmethod
    def _hash(cls, value: HashRef) -> HashRef:
        return _osc1_subject(value, RESOLUTION_HASH_SUBJECT, "resolution_hash")  # type: ignore[return-value]


class CommercialBomRefV1(StrictContractModel):
    """The authoritative Customer Purchase BOM this package's ``commercial_line_ref`` values point into."""

    bom_hash: HashRef

    @field_validator("bom_hash")
    @classmethod
    def _hash(cls, value: HashRef) -> HashRef:
        return _osc1_subject(value, COMMERCIAL_BOM_HASH_SUBJECT, "bom_hash")  # type: ignore[return-value]


class DesignBomRefV1(StrictContractModel):
    """The design BOM of a local assembly plan revision (every placed or anchored item, by line), by its hash: under a
    LOCAL_PLAN binding the scene's ``commercial_line_ref`` values are its line ids."""

    bom_hash: HashRef

    @field_validator("bom_hash")
    @classmethod
    def _hash(cls, value: HashRef) -> HashRef:
        return _osc1_subject(value, DESIGN_BOM_HASH_SUBJECT, "bom_hash")  # type: ignore[return-value]


PlanIdText = Annotated[
    str,
    StringConstraints(strict=True, pattern=r"^[a-z0-9][a-z0-9-]{2,63}$"),
    Field(description="Id of a local assembly plan: 3 to 64 lowercase letters, digits or '-'."),
]


class LocalPlanRefV1(StrictContractModel):
    """The local assembly plan revision a package (or its detail and drawing) was generated from: plan id, revision
    number, the plan digest (osc1 digest of the revision's decisions) and the plan's design BOM."""

    plan_id: PlanIdText
    revision: int = Field(strict=True, ge=1)
    plan_digest: HashRef
    design_bom: DesignBomRefV1

    @field_validator("plan_digest")
    @classmethod
    def _plan(cls, value: HashRef) -> HashRef:
        return _osc1_subject(value, PLAN_REVISION_HASH_SUBJECT, "plan_digest")  # type: ignore[return-value]


class PackageBindingV1(StrictContractModel):
    """What the package is bound to. The commercial binding (``basis`` absent, schema 3.0) names the resolution and,
    when they exist, the commercial submission, the Customer Purchase BOM, the Order and the commercial resolution id;
    the LOCAL_PLAN binding (schema 3.1) names the local plan revision (``local_plan``) and none of those."""

    project: ValidatedProjectRefV1
    local_validation_hash: HashRef | None = None
    commercial_submission_hash: HashRef | None = None
    resolution: ResolutionRefV1 | None = None
    commercial_bom: CommercialBomRefV1 | None = None
    order_ref: OpenSciId | None = None
    commercial_resolution_id: OpenSciId | None = Field(
        None, description="Id of the commercial resolution this package belongs to.")
    basis: Literal["LOCAL_PLAN"] | None = Field(
        None, description="LOCAL_PLAN: generated on the user's device from a local assembly plan (absent: commercial).")
    local_plan: LocalPlanRefV1 | None = Field(None, description="The local plan revision of a LOCAL_PLAN binding.")

    @field_validator("local_validation_hash")
    @classmethod
    def _lv(cls, value: HashRef | None) -> HashRef | None:
        return _osc1_subject(value, LOCAL_VALIDATION_HASH_SUBJECT, "local_validation_hash")

    @field_validator("commercial_submission_hash")
    @classmethod
    def _submission(cls, value: HashRef | None) -> HashRef | None:
        return _osc1_subject(value, COMMERCIAL_SUBMISSION_HASH_SUBJECT, "commercial_submission_hash")

    @model_validator(mode="after")
    def _basis(self) -> PackageBindingV1:
        if self.basis is None:
            if self.resolution is None or self.local_plan is not None:
                raise ValueError("a commercial binding names its resolution and no local plan")
        elif self.local_plan is None or any(v is not None for v in (
                self.resolution, self.commercial_submission_hash, self.commercial_bom, self.order_ref,
                self.commercial_resolution_id)):
            raise ValueError("a LOCAL_PLAN binding names its local plan and no resolution, commercial submission, Customer "
                             "Purchase BOM, Order or commercial resolution id")
        return self

    @property
    def local(self) -> bool:
        """True for a LOCAL_PLAN binding (a package generated on the user's device from a local plan)."""

        return self.basis == LOCAL_PLAN_BASIS


class PackageFileV1(StrictContractModel):
    path: PackagePath
    media_type: Literal["application/json", "model/gltf-binary"]
    size_bytes: int = Field(ge=0)
    sha256: Sha256Digest

    @model_validator(mode="after")
    def _media(self) -> PackageFileV1:
        expected = JSON_MEDIA_TYPE if self.path.endswith(".json") else GLB_MEDIA_TYPE
        if self.media_type != expected:
            raise ValueError(f"{self.path}: media_type must be {expected!r}")
        return self


class AssemblyPreviewManifestV1(StrictContractModel):
    schema_id: Literal["opensci.assembly_preview.manifest"] = Field(ASSEMBLY_PREVIEW_MANIFEST_SCHEMA_ID, alias="schema")
    schema_version: Literal["3.0", "3.1"] = Field(
        ASSEMBLY_PREVIEW_MANIFEST_SCHEMA_VERSION, description="3.0: the commercial binding; 3.1: the LOCAL_PLAN binding.")
    package_revision: int = Field(ge=1, description="Revision of the package for this resolution (1 = first revision).")
    usage: Annotated[UsageTermsV1 | LocalUsageTermsV1, Field(discriminator="scope")] = UsageTermsV1()
    binding: PackageBindingV1
    files: tuple[PackageFileV1, ...] = Field(min_length=1, max_length=MAX_PACKAGE_FILES - 1)
    package_content_hash: Sha256Digest

    @model_validator(mode="after")
    def _check(self) -> AssemblyPreviewManifestV1:
        paths = [f.path for f in self.files]
        if paths != sorted(set(paths)):
            raise ValueError("manifest files must be unique and sorted by path")
        if SCENE_PATH not in paths:
            raise ValueError("manifest files must include scene.json")
        if self.schema_version != manifest_schema_version_for(self.binding):
            raise ValueError("a manifest states 3.0 exactly for the commercial binding and 3.1 exactly for the LOCAL_PLAN "
                             "binding")
        if isinstance(self.usage, LocalUsageTermsV1) != self.binding.local:
            raise ValueError("the usage terms are THIS_PROJECT for the commercial binding and THIS_PLAN for the LOCAL_PLAN "
                             "binding")
        expected = content_hash_of(self.content_payload())
        if self.package_content_hash != expected:
            raise ValueError("package_content_hash does not match the manifest content")
        return self

    def payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)

    def content_payload(self) -> dict[str, Any]:
        """The hashed part of the manifest: everything except ``package_content_hash``."""

        payload = self.payload()
        del payload["package_content_hash"]
        return payload


def manifest_schema_version_for(binding: PackageBindingV1) -> Literal["3.0", "3.1"]:
    """The manifest version a writer states for ``binding``: 3.1 for a LOCAL_PLAN binding, else 3.0."""

    return ASSEMBLY_PREVIEW_MANIFEST_SCHEMA_VERSION_3_1 if binding.local else ASSEMBLY_PREVIEW_MANIFEST_SCHEMA_VERSION


def usage_terms_for(binding: PackageBindingV1) -> UsageTermsV1 | LocalUsageTermsV1:
    """The usage terms of a package with ``binding``."""

    return LocalUsageTermsV1() if binding.local else UsageTermsV1()


def content_hash_of(content_payload: dict[str, Any]) -> str:
    """``package_content_hash`` = osc1 digest of the manifest payload without that field."""

    return osc1_digest(content_payload)
