"""Plan revision -> Assembly Preview Package (preview manifest 3.1, binding ``LOCAL_PLAN``), on this device.

``project_plan(revision, context)`` builds every public field of the package explicitly from one plan revision, the
Project it binds and the public catalogs, the way OpenSci's server-side projector builds a delivered package from a
resolution -- with no private input, so nothing needs redacting:

1. refuse what the scene contract cannot carry: an item of the user's own hardware (``USER_MECH``,
   ``PLAN_USER_HARDWARE_NOT_PROJECTABLE``; the plan-level checks still run on such a plan);
2. parts: every item of a complete support chain (optic -> mount -> post -> holder -> board) and the board, with
   ``part_ref`` = the plan's ``item_id``, its identity, its design BOM line as ``commercial_line_ref`` (the customer's
   own optical table has none), its pose, support, datums, settings, fixation and geometry (the public mechanical
   catalog's neutral primitives; an optic's from the public optical catalog's design geometry);
3. design anchors: every optic, component, declared envelope and observation of the plan. An optic whose chain
   holds unresolved hardware (identity NONE) is an unrealized anchor with the ``DESIGN_COMPONENT_NOT_SUPPLIED`` note, its
   resolved chain hardware is left out of the scene (``PlanProjection.omitted``: it cannot stand on the board without
   the rest of its chain). Anchors placed by the customer (an ANCHOR optic, a component, a declared envelope) are
   customer-supplied anchors with their reason and reserved space (``ANCHOR_REASON``);
4. DOF delivery from each optic's DOF results and declared ranges, notes, bounds, beam axes (from the context);
5. one GLB per part (``glb``), the manifest (``build_assembly_preview_package``: schema 3.1, ``LocalUsageTermsV1``) and
   the package verification.

The binding names the plan revision (plan id, revision, plan digest) and the digest of its design BOM; the Local
Validation hash and the Engine hashes of a run are bound only when the context carries a run of exactly this Project.
Pure: no file, network, clock or environment access.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from .._vendor.shared_contracts.assembly_preview import (
    CUSTOMER_OPTICAL_TABLE_SKU,
    DESIGN_BOM_HASH_SUBJECT,
    LOCAL_PLAN_BASIS,
    LOCAL_VALIDATION_HASH_SUBJECT,
    NOTE_SUBJECTS,
    OPTICAL_CENTRE_DATUM,
    PLAN_REVISION_HASH_SUBJECT,
    TRANSMISSIVE_ROLES,
    WARNING_NOTE_CODES,
    AdjustmentCharacterV1,
    AppearanceV1,
    AssemblyNoteCodeV1,
    AssemblyNoteV1,
    AssemblyPreviewPackageV1,
    AssemblyPreviewSceneV1,
    BeamAxisV1,
    BeamReferenceV1,
    BoardHoleGridV1,
    BreadboardV1,
    CustomerSupplyReasonV1,
    CustomerSupplyV1,
    DatumFrameV1,
    DeliveredControlV1,
    DesignAnchorKindV1,
    DesignAnchorV1,
    DesignBindingV1,
    DesignBomRefV1,
    DofDeliveryStatusV1,
    DofImplementationV1,
    DofMechanismV1,
    FastenerNoteV1,
    FinishV1,
    FixationModeV1,
    FixationV1,
    GeometryFidelityV1,
    IdentityKindV1,
    LocalPlanRefV1,
    MateFastenerV1,
    MateV1,
    NoteSubjectV1,
    PackageBindingV1,
    PartCategoryV1,
    PartGeometryV1,
    PartIdentityV1,
    PreviewPartV1,
    ReservedEnvelopeV1,
    ResolutionSummaryV1,
    SceneBoundsV1,
    SettingBasisV1,
    SettingV1,
    SupportV1,
    TravelBasisV1,
    build_assembly_preview_package,
    dof_note_code,
    note_message,
    required_scene_schema_version,
)
from .._vendor.shared_contracts.common.geometry import PoseV1
from .._vendor.shared_contracts.common.hashes import HashProfile, HashRef, osc1_digest
from .._vendor.shared_contracts.optical_design import (DofControlIntentV1, LocalValidationRecordV1, ProjectDocumentV2,
                                                        check_local_validation_binding, validated_project_ref)
from .contract import (AssemblyPlanError, AssemblyPlanErrorCode, AssemblyPlanRevisionV1, ChainLinkKindV1, DofResultStatusV1,
                       DofResultV1, ItemIdentityKindV1, ItemRoleV1, MountingV1, PlanFactBasisV1, PlanItemV1,
                       ReservedEnvelopeV1 as PlanEnvelopeV1, SupplyReasonV1, SupplyV1)
from .glb import Primitive, board_hole_markers, part_glb_bytes, tessellate

PROJECTOR_ID = "os-optics.assembly_plan.projection"
PROJECTOR_VERSION = "1.0.0"
POSITION_DECIMALS = 6
DIRECTION_DECIMALS = 12
#: Radius of the dark hole markers drawn on a breadboard's top (mm), as OpenSci's delivered packages draw them.
BOARD_HOLE_MARKER_RADIUS_MM = 3.0
#: The customer's own optical table is drawn as a mounting area this thick (mm; the plan does not know the table).
CUSTOMER_TABLE_THICKNESS_MM = 12.5
CUSTOMER_TABLE_RGB = (0.32, 0.34, 0.37)
#: A delivered DOF moves at least this much along the DOF's own axis (cosine) for its travel to be stated.
PARALLEL_COSINE = 0.999999
_AXIS_VECTORS = {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0), "Z": (0.0, 0.0, 1.0)}
_DOF_ORDER = ("Tx", "Ty", "Tz", "Rx", "Ry", "Rz")
#: Coarse metric thread pitches: the hole-grid and fastener designations OpenSci's packages print ("M6x1.0").
METRIC_COARSE_PITCH = {"M3": "0.5", "M4": "0.7", "M5": "0.8", "M6": "1.0", "M8": "1.25"}
#: The optical role a declared envelope's anchor takes (the customer's part is known only by its envelope).
DECLARED_ENVELOPE_ROLE = "other_optical"

#: Scene category of each part role (a post holder with its integrated fork is a BASE, as in OpenSci's packages).
CATEGORY = {ItemRoleV1.OPTIC: PartCategoryV1.OPTICAL_ELEMENT, ItemRoleV1.MOUNT: PartCategoryV1.OPTIC_MOUNT,
            ItemRoleV1.POST: PartCategoryV1.POST, ItemRoleV1.POST_HOLDER: PartCategoryV1.BASE,
            ItemRoleV1.BREADBOARD: PartCategoryV1.BREADBOARD}
SETTING_BASIS = {PlanFactBasisV1.RESOLVED: SettingBasisV1.RESOLVED, PlanFactBasisV1.PREVIEW_ASSUMPTION: SettingBasisV1.NOMINAL}
TRAVEL_BASIS = {PlanFactBasisV1.RESOLVED: TravelBasisV1.RESOLVED, PlanFactBasisV1.PREVIEW_ASSUMPTION: TravelBasisV1.NOMINAL}
#: The scene reason of a customer-supplied anchor, by (role, supply, plan supply reason). An ANCHOR optic OpenSci
#: supplies (no compatible mount, or no mechanical data for one) is supplied without mounting hardware.
ANCHOR_REASON: dict[tuple[ItemRoleV1, SupplyV1, SupplyReasonV1 | None], CustomerSupplyReasonV1] = {
    (ItemRoleV1.OPTIC, SupplyV1.OPENSCI, None): CustomerSupplyReasonV1.OPENSCI_SUPPLIED_UNMOUNTED,
    (ItemRoleV1.OPTIC, SupplyV1.OPENSCI, SupplyReasonV1.OPENSCI_SUPPLIED_UNMOUNTED): CustomerSupplyReasonV1.OPENSCI_SUPPLIED_UNMOUNTED,
    (ItemRoleV1.OPTIC, SupplyV1.CUSTOMER_OWNED, SupplyReasonV1.CUSTOMER_OWNED): CustomerSupplyReasonV1.CUSTOMER_OWNED,
    (ItemRoleV1.OPTIC, SupplyV1.CUSTOMER_OWNED, SupplyReasonV1.NOT_ORDERABLE): CustomerSupplyReasonV1.NOT_ORDERABLE,
    (ItemRoleV1.OPTIC, SupplyV1.CUSTOMER_OWNED, SupplyReasonV1.NOT_IN_CATALOG): CustomerSupplyReasonV1.NOT_IN_CATALOG,
    (ItemRoleV1.OPTIC, SupplyV1.UNRESOLVED, SupplyReasonV1.NOT_ORDERABLE): CustomerSupplyReasonV1.NOT_ORDERABLE,
    (ItemRoleV1.OPTIC, SupplyV1.UNRESOLVED, SupplyReasonV1.NOT_IN_CATALOG): CustomerSupplyReasonV1.NOT_IN_CATALOG,
    (ItemRoleV1.COMPONENT, SupplyV1.CUSTOMER_OWNED, SupplyReasonV1.CUSTOMER_LIGHT_SOURCE): CustomerSupplyReasonV1.CUSTOMER_LIGHT_SOURCE,
    (ItemRoleV1.COMPONENT, SupplyV1.CUSTOMER_OWNED, SupplyReasonV1.NOT_IN_CATALOG): CustomerSupplyReasonV1.NOT_IN_CATALOG,
    (ItemRoleV1.DECLARED_ENVELOPE, SupplyV1.CUSTOMER_OWNED, SupplyReasonV1.NOT_IN_CATALOG): CustomerSupplyReasonV1.NOT_IN_CATALOG,
}
#: Customer-supply reasons whose anchor names its OpenSci catalog product.
CATALOG_REASONS = frozenset({CustomerSupplyReasonV1.NOT_ORDERABLE, CustomerSupplyReasonV1.CUSTOMER_OWNED,
                             CustomerSupplyReasonV1.OPENSCI_SUPPLIED_UNMOUNTED, CustomerSupplyReasonV1.TEMPORARILY_UNAVAILABLE})
#: Why an item of the plan is not a part of the scene (``PlanProjection.omitted``).
OMITTED_UNRESOLVED_CHAIN = "CHAIN_NOT_RESOLVED"
OMITTED_NOT_PLACED = "NOT_PLACED"

Vec3 = tuple[float, float, float]
Quat = tuple[float, float, float, float]


# ------------------------------------------------------------------------------------------------ inputs


@dataclass(frozen=True)
class BeamAxisInput:
    """A nominal beam line in the PROJECT DESIGN frame (projected into the assembly world by the plan frame):
    ``through`` names the design element at each point; ``segment_roles`` / ``notes`` as ``BeamAxisV1``."""

    path_key: str
    points_design_mm: tuple[Vec3, ...]
    through: tuple[str, ...] | None = None
    wavelength_nm: float | None = None
    segment_roles: tuple[str, ...] | None = None
    notes: tuple[str, ...] | None = None


class OpticMechanicsLike(Protocol):
    """What the projection reads of an optic's mechanics (``optics.mechanics_of``): neutral primitives in the optic's
    own frame (local +Z its axis) and a colour."""

    visual: tuple[Any, ...]
    color_rgb: tuple[float, float, float]


class ProjectionContext(Protocol):
    """What ``project_plan`` reads besides the revision. ``project`` is the Project the revision binds,
    ``mechanical`` the documents it binds (``MechanicalSources``: ``identity(sku)``), ``design_bom`` the revision's
    design BOM document (``bom.design_bom``), ``optic_mechanics(item)`` the optic's mechanics (None: unknown). Optional
    attributes (``getattr``): ``beam_axes`` (``BeamAxisInput``), ``local_validation`` (the Local Validation record of
    a run of this Project), ``dof_frame`` (the kernel's DOF frame of an optic's design rotation) and ``optic_faces(item)``
    (``OpticFacesV1`` of an optic whose faces differ)."""

    project: ProjectDocumentV2
    mechanical: Any
    design_bom: Mapping[str, Any]

    def optic_mechanics(self, item: PlanItemV1) -> OpticMechanicsLike | None: ...


@dataclass(frozen=True)
class PlanProjectionInputs:
    """A ``ProjectionContext`` from plain values (the Python API's and the tests' context)."""

    project: ProjectDocumentV2
    mechanical: Any
    design_bom: Mapping[str, Any]
    optics: Mapping[str, OpticMechanicsLike] = field(default_factory=dict)
    beam_axes: tuple[BeamAxisInput, ...] = ()
    local_validation: LocalValidationRecordV1 | None = None
    dof_frame: Callable[[Quat], Quat] | None = None
    faces: Mapping[str, Any] = field(default_factory=dict)

    def optic_mechanics(self, item: PlanItemV1) -> OpticMechanicsLike | None:
        return self.optics.get(item.item_id) or (self.optics.get(item.identity.opensci_sku or "") if item.identity.opensci_sku else None)

    def optic_faces(self, item: PlanItemV1) -> Any:
        return self.faces.get(item.item_id)


@dataclass(frozen=True)
class OmittedItem:
    item_id: str
    reason: str


@dataclass(frozen=True)
class PlanProjection:
    """The package of one revision and how its items map into it: ``parts`` (the items that are scene parts),
    ``anchors`` (design anchor instance id -> item id) and ``omitted`` (items placed in the plan but not in the scene)."""

    package: AssemblyPreviewPackageV1
    parts: tuple[str, ...]
    anchors: Mapping[str, str]
    omitted: tuple[OmittedItem, ...]
    lines: Mapping[str, str]


def _fail(code: AssemblyPlanErrorCode, message: str, **details: Any) -> AssemblyPlanError:
    return AssemblyPlanError(code, message, details)


# ------------------------------------------------------------------------------------------------ math


def _rotation(q: Quat) -> tuple[Vec3, Vec3, Vec3]:
    norm = math.sqrt(sum(c * c for c in q))
    w, x, y, z = (c / norm for c in q)
    return (
        (1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)),
        (2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)),
        (2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)),
    )


def _rotate(q: Quat, v: Vec3) -> Vec3:
    r = _rotation(q)
    return tuple(sum(r[i][k] * v[k] for k in range(3)) for i in range(3))  # type: ignore[return-value]


def _apply(pose: PoseV1, p: Vec3) -> Vec3:
    rotated = _rotate(tuple(pose.quaternion_wxyz), p)  # type: ignore[arg-type]
    return tuple(rotated[i] + pose.position_mm[i] for i in range(3))  # type: ignore[return-value]


def _inverse_apply(pose: PoseV1, p: Vec3) -> Vec3:
    r = _rotation(tuple(pose.quaternion_wxyz))  # type: ignore[arg-type]
    d = tuple(p[i] - pose.position_mm[i] for i in range(3))
    return tuple(sum(r[k][i] * d[k] for k in range(3)) for i in range(3))  # type: ignore[return-value]


def _q_mul(a: Quat, b: Quat) -> Quat:
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return (aw * bw - ax * bx - ay * by - az * bz, aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx, aw * bz + ax * by - ay * bx + az * bw)


def _q_conj(q: Quat) -> Quat:
    return (q[0], -q[1], -q[2], -q[3])


def _clean(value: float, decimals: int) -> float:
    return round(value, decimals) + 0.0  # + 0.0 turns -0.0 into 0.0


def _vec(values: tuple[float, ...], decimals: int = POSITION_DECIMALS) -> Vec3:
    return tuple(_clean(v, decimals) for v in values)  # type: ignore[return-value]


def _unit_q(q: Quat) -> Quat:
    norm = math.sqrt(sum(c * c for c in q))
    return tuple(_clean(c / norm, DIRECTION_DECIMALS) for c in q)  # type: ignore[return-value]


def _unit(v: Vec3) -> Vec3:
    norm = math.sqrt(sum(c * c for c in v))
    return _vec(tuple(c / norm for c in v), DIRECTION_DECIMALS)


def _pose_of(position: Vec3, quaternion: Quat) -> PoseV1:
    return PoseV1(position_mm=_vec(position), quaternion_wxyz=tuple(quaternion))


def _compose(outer: PoseV1, inner: PoseV1) -> PoseV1:
    q = _q_mul(tuple(outer.quaternion_wxyz), tuple(inner.quaternion_wxyz))  # type: ignore[arg-type]
    return PoseV1(position_mm=_apply(outer, tuple(inner.position_mm)), quaternion_wxyz=_unit_q(q))  # type: ignore[arg-type]


def _relative(a: PoseV1, b: PoseV1) -> PoseV1:
    """``b`` expressed in ``a``'s frame (``a^-1 b``), positions and quaternion rounded."""

    q = _q_mul(_q_conj(_unit_q(tuple(a.quaternion_wxyz))), tuple(b.quaternion_wxyz))  # type: ignore[arg-type]
    return PoseV1(position_mm=_vec(_inverse_apply(a, tuple(b.position_mm))), quaternion_wxyz=_unit_q(q))  # type: ignore[arg-type]


def design_frame_pose(revision: AssemblyPlanRevisionV1) -> PoseV1:
    """The plan frame as a pose: ``p_lab = Rz(yaw_deg) p_design + translation_mm``."""

    half = math.radians(revision.frame.yaw_deg) / 2.0
    return PoseV1(position_mm=_vec(tuple(revision.frame.translation_mm)),
                  quaternion_wxyz=_unit_q((math.cos(half), 0.0, 0.0, math.sin(half))))


def metric_designation(thread: str) -> str:
    """A metric coarse thread with its pitch (``M6`` -> ``M6x1.0``); any other designation unchanged."""

    return f"{thread}x{METRIC_COARSE_PITCH[thread]}" if thread in METRIC_COARSE_PITCH else thread


def envelope_cylinder(envelope: PlanEnvelopeV1) -> tuple[float, float, float]:
    """``(diameter, z_min, z_max)`` of the scene's reserved cylinder about local +Z for a plan envelope: the cylinder
    itself, or the smallest cylinder about local +Z holding a BOX (conservative)."""

    if envelope.shape == "CYLINDER":
        return envelope.diameter_mm, envelope.z_min_mm, envelope.z_max_mm  # type: ignore[return-value]
    lo, hi = envelope.min_mm, envelope.max_mm
    radius = max(math.hypot(x, y) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]))  # type: ignore[index]
    return 2.0 * radius, lo[2], hi[2]  # type: ignore[index]


# ------------------------------------------------------------------------------------------------ design BOM


def design_bom_lines(design_bom: Mapping[str, Any]) -> tuple[dict[str, str], dict[str, str], str]:
    """``({item_id: line_id}, {item_id: supply}, bom_digest)`` of a design BOM document whose ``bom_digest`` is its
    osc1 digest without that field; a document that does not hold that is refused."""

    try:
        body = {k: v for k, v in design_bom.items() if k != "bom_digest"}
        digest = design_bom["bom_digest"]
        lines = design_bom["lines"]
        if design_bom.get("schema") != "os-optics.design_bom" or osc1_digest(body) != digest:
            raise ValueError("the design BOM digest does not match the document")
        line_of: dict[str, str] = {}
        supply_of: dict[str, str] = {}
        for line in lines:
            for item_id in line["item_ids"]:
                if item_id in line_of:
                    raise ValueError(f"item {item_id} is on two design BOM lines")
                line_of[item_id] = line["line_id"]
                supply_of[item_id] = line["supply"]
    except (KeyError, TypeError, ValueError) as exc:
        raise _fail(AssemblyPlanErrorCode.PLAN_DIR_INCONSISTENT, f"the design BOM cannot be used: {exc}") from None
    return line_of, supply_of, digest


# ------------------------------------------------------------------------------------------------ projection


def project_plan_package(revision: AssemblyPlanRevisionV1, context: ProjectionContext) -> AssemblyPreviewPackageV1:
    """The verified LOCAL_PLAN Assembly Preview Package of ``revision`` (``project_plan(...).package``)."""

    return project_plan(revision, context).package


def project_plan(revision: AssemblyPlanRevisionV1, context: ProjectionContext) -> PlanProjection:
    """Project one plan revision into its Assembly Preview Package (see the module docstring)."""

    return _Projection(revision, context).run()


class _Projection:
    def __init__(self, revision: AssemblyPlanRevisionV1, context: ProjectionContext):
        self.r = revision
        self.c = context
        self.items = {item.item_id: item for item in revision.items}
        user = sorted(i.item_id for i in revision.items if i.identity.kind is ItemIdentityKindV1.USER_MECH)
        if user:
            raise _fail(AssemblyPlanErrorCode.PLAN_USER_HARDWARE_NOT_PROJECTABLE,
                        "a plan with your own hardware (USER-MECH identities) has no preview package in this Engine; "
                        "the plan checks still run", item_ids=user)
        project = context.project
        if project.content_hash() != revision.bindings.project.project_hash.digest:
            raise _fail(AssemblyPlanErrorCode.PROJECT_REJECTED, "the Project is not the one the plan revision binds",
                        expected=revision.bindings.project.project_hash.digest, found=project.content_hash())
        self.project = project
        self.components = {c.instance_id: c for c in project.components}
        self.frame = design_frame_pose(revision)
        self.line_of, self.supply_of, self.bom_digest = design_bom_lines(context.design_bom)
        self.links_by_a = {link.a_item_id: link for link in revision.links}
        self.board = self.items[revision.board_item_id]
        self.omitted: list[OmittedItem] = []
        self.chain_ok = self._resolved_chains()

    # ---------------------------------------------------------------------------------- chains

    def _chain_of(self, optic_id: str) -> list[str]:
        """The items of an optic's chain, optic first, up to (not including) the board."""

        chain, ref = [optic_id], optic_id
        while ref in self.links_by_a and self.links_by_a[ref].b_item_id != self.board.item_id:
            ref = self.links_by_a[ref].b_item_id
            chain.append(ref)
        return chain

    def _resolved_chains(self) -> dict[str, bool]:
        status = {}
        for item in self.r.items:
            if item.role is ItemRoleV1.OPTIC and item.mounting is MountingV1.CHAIN:
                chain = self._chain_of(item.item_id)
                resolved = all(self.items[i].placement is not None for i in chain[1:])
                status[item.item_id] = resolved
                if not resolved:
                    self.omitted.extend(OmittedItem(i, OMITTED_UNRESOLVED_CHAIN) for i in chain[1:]
                                        if self.items[i].placement is not None)
        return status

    def _is_part(self, item: PlanItemV1) -> bool:
        if item.role is ItemRoleV1.BREADBOARD:
            return True
        if item.role is ItemRoleV1.OPTIC:
            return item.mounting is MountingV1.CHAIN and self.chain_ok[item.item_id]
        if item.role in (ItemRoleV1.MOUNT, ItemRoleV1.POST, ItemRoleV1.POST_HOLDER):
            if item.placement is None:
                return False
            optic = next((o for o, ok in self.chain_ok.items() if item.item_id in self._chain_of(o)), None)
            return optic is not None and self.chain_ok[optic]
        return False

    # ---------------------------------------------------------------------------------- run

    def run(self) -> PlanProjection:
        part_items = [i for i in self.r.items if self._is_part(i)]
        datums = {i.item_id: self._datums(i) for i in part_items}
        meshes = {i.item_id: self._mesh(i) for i in part_items}
        mates = self._mates(part_items, datums)
        mate_of = {frozenset((m.part_a_ref, m.part_b_ref)): m.mate_ref for m in mates}
        parts = [self._part(i, datums[i.item_id], meshes[i.item_id], mate_of)
                 for i in sorted(part_items, key=lambda i: (i.placement.assembly_order, i.item_id))]  # type: ignore[union-attr]
        board = self._breadboard()
        beam, anchors = self._beam_reference(board, {p.part_ref for p in parts})
        dofs = self._dofs({p.part_ref for p in parts}, beam)
        scene = AssemblyPreviewSceneV1(
            schema_version=required_scene_schema_version(beam.beam_axes),
            resolution=ResolutionSummaryV1(passed_checks=()),
            parts=tuple(parts),
            mates=mates,
            settings=self._settings(part_items),
            fixations=self._fixations(part_items),
            breadboard=board,
            beam_reference=beam,
            dof_implementations=dofs,
            notes=self._notes(dofs, beam, board),
            bounds=self._bounds(parts, beam),
        )
        geometry = {part.geometry.asset_path: part_glb_bytes(part.part_ref, meshes[part.part_ref], part.appearance)
                    for part in scene.parts}
        package = build_assembly_preview_package(scene=scene, geometry=geometry, binding=self._binding())
        lines = {i.item_id: self.line_of[i.item_id] for i in part_items if i.item_id in self.line_of}
        return PlanProjection(package=package, parts=tuple(sorted(i.item_id for i in part_items)), anchors=anchors,
                              omitted=tuple(sorted(self.omitted, key=lambda o: o.item_id)), lines=lines)

    # ---------------------------------------------------------------------------------- binding

    def _binding(self) -> PackageBindingV1:
        record = getattr(self.c, "local_validation", None)
        lv = None
        if record is not None and not check_local_validation_binding(record, self.project):
            lv = HashRef(profile=HashProfile.OSC1, digest=record.record_hash(), subject=LOCAL_VALIDATION_HASH_SUBJECT)
        plan = LocalPlanRefV1(
            plan_id=self.r.plan_id, revision=self.r.revision,
            plan_digest=HashRef(profile=HashProfile.OSC1, digest=self.r.content_digest, subject=PLAN_REVISION_HASH_SUBJECT),
            design_bom=DesignBomRefV1(bom_hash=HashRef(profile=HashProfile.OSC1, digest=self.bom_digest,
                                                       subject=DESIGN_BOM_HASH_SUBJECT)))
        return PackageBindingV1(project=validated_project_ref(self.project), local_validation_hash=lv,
                                basis=LOCAL_PLAN_BASIS, local_plan=plan)

    def _bound_record(self) -> LocalValidationRecordV1 | None:
        record = getattr(self.c, "local_validation", None)
        return record if record is not None and not check_local_validation_binding(record, self.project) else None

    # ---------------------------------------------------------------------------------- parts

    def _identity_doc(self, item: PlanItemV1) -> Any:
        found = self.c.mechanical.identity(item.identity.opensci_sku)
        if found is None:
            raise _fail(AssemblyPlanErrorCode.MECHANICAL_CATALOG_NOT_AVAILABLE,
                        f"{item.identity.opensci_sku} ({item.item_id}) is not in the mechanical documents the plan binds",
                        item_id=item.item_id, opensci_sku=item.identity.opensci_sku)
        return found

    def _optic(self, item: PlanItemV1) -> OpticMechanicsLike:
        mechanics = self.c.optic_mechanics(item)
        if mechanics is None:
            raise _fail(AssemblyPlanErrorCode.MECHANICAL_CATALOG_NOT_AVAILABLE,
                        f"no design geometry for the optic {item.identity.opensci_sku} ({item.item_id})", item_id=item.item_id)
        return mechanics

    def _board_size(self) -> tuple[float, float, float]:
        board = self.board
        if board.identity.kind is ItemIdentityKindV1.CUSTOMER_TABLE:
            table = self.r.derive_options.customer_table
            if table is None:
                raise _fail(AssemblyPlanErrorCode.PLAN_DIR_INCONSISTENT, "a customer table board without its table options")
            return table.size_x_mm, table.size_y_mm, CUSTOMER_TABLE_THICKNESS_MM
        doc = self._identity_doc(board)
        return doc.size_x_mm, doc.size_y_mm, doc.thickness_mm

    def _datums(self, item: PlanItemV1) -> dict[str, PoseV1]:
        zero = (1.0, 0.0, 0.0, 0.0)
        role = item.role
        if role is ItemRoleV1.BREADBOARD:
            return {"board_top": _pose_of((0.0, 0.0, self._board_size()[2]), zero)}
        doc = None if role is ItemRoleV1.OPTIC else self._identity_doc(item)
        if role is ItemRoleV1.POST_HOLDER:
            return {"bottom": _pose_of((0.0, 0.0, 0.0), zero), "bore_top": _pose_of((0.0, 0.0, doc.height_mm), zero)}
        if role is ItemRoleV1.POST:
            return {"bottom": _pose_of((0.0, 0.0, 0.0), zero), "top": _pose_of((0.0, 0.0, doc.length_mm), zero)}
        if role is ItemRoleV1.OPTIC:
            mechanics = self._optic(item)
            seat_z = max(_top_z(p) for p in mechanics.visual)
            component = self.components.get(item.design_instance_id or "")
            centre = 0.0
            if component is not None and component.optical_role.value in TRANSMISSIVE_ROLES:
                low = min(_bottom_z(p) for p in mechanics.visual)
                centre = _clean((low + seat_z) / 2.0, POSITION_DECIMALS)
            return {"seat": _pose_of((0.0, 0.0, seat_z), zero), OPTICAL_CENTRE_DATUM: _pose_of((0.0, 0.0, centre), zero)}
        # MOUNT: the post interface at its origin; the seat where its optic's seat datum is
        optic_link = next(link for link in self.r.links if link.b_item_id == item.item_id
                          and link.kind is ChainLinkKindV1.OPTIC_IN_MOUNT)
        optic = self.items[optic_link.a_item_id]
        seat_world = _compose(optic.placement.pose_in_lab, self._datums(optic)["seat"])  # type: ignore[union-attr]
        return {"support": _pose_of((0.0, 0.0, 0.0), zero), "seat": _relative(item.placement.pose_in_lab, seat_world)}  # type: ignore[union-attr]

    def _mesh(self, item: PlanItemV1) -> Any:
        if item.role is ItemRoleV1.OPTIC:
            return tessellate(tuple(self._optic(item).visual))
        if item.role is ItemRoleV1.BREADBOARD:
            size_x, size_y, thickness = self._board_size()
            if item.identity.kind is ItemIdentityKindV1.CUSTOMER_TABLE:
                primitives: tuple[Any, ...] = (Primitive(kind="BOX", finish=FinishV1.DARK.value, min_mm=(0.0, 0.0, 0.0),
                                                         max_mm=(size_x, size_y, thickness)),)
            else:
                primitives = tuple(self._identity_doc(item).visual)
            grid = self._hole_grid()
            pose = item.placement.pose_in_lab  # type: ignore[union-attr]
            origin = _inverse_apply(pose, (grid.origin_xy_mm[0], grid.origin_xy_mm[1], self._board_top()))
            markers = board_hole_markers((origin[0], origin[1]), grid.pitch_mm, grid.count, origin[2], BOARD_HOLE_MARKER_RADIUS_MM)
            return tessellate(primitives + markers, marker_primitives=len(markers))
        return tessellate(tuple(self._identity_doc(item).visual))

    def _appearance(self, item: PlanItemV1) -> AppearanceV1:
        if item.role is ItemRoleV1.OPTIC:
            mechanics = self._optic(item)
            return AppearanceV1(base_color_rgb=tuple(mechanics.color_rgb), finish=FinishV1(mechanics.visual[0].finish))
        if item.identity.kind is ItemIdentityKindV1.CUSTOMER_TABLE:
            return AppearanceV1(base_color_rgb=CUSTOMER_TABLE_RGB, finish=FinishV1.DARK)
        doc = self._identity_doc(item)
        return AppearanceV1(base_color_rgb=tuple(doc.color_rgb), finish=FinishV1(doc.visual[0].finish))

    def _scene_identity(self, item: PlanItemV1) -> PartIdentityV1:
        kind = item.identity.kind
        if kind is ItemIdentityKindV1.CUSTOMER_TABLE:
            size_x, size_y, _ = self._board_size()
            table = self.r.derive_options.customer_table
            name = (f"Your optical table, mounting area {size_x:g} mm x {size_y:g} mm, {table.thread} holes on a "  # type: ignore[union-attr]
                    f"{table.pitch_mm:g} mm grid")  # type: ignore[union-attr]
            return PartIdentityV1(opensci_sku=CUSTOMER_OPTICAL_TABLE_SKU, identity_kind=IdentityKindV1.CUSTOMER_SUPPLIED,
                                  display_name=name)
        namespace = IdentityKindV1.PUBLIC_OPTICAL_SKU if kind is ItemIdentityKindV1.OS_OPT else IdentityKindV1.COMMERCIAL_SKU
        return PartIdentityV1(opensci_sku=item.identity.opensci_sku, identity_kind=namespace,
                              display_name=item.identity.display_name)

    def _part(self, item: PlanItemV1, datums: dict[str, PoseV1], mesh: Any, mate_of: dict[frozenset[str], str]) -> PreviewPartV1:
        placement = item.placement
        assert placement is not None
        support = None
        if placement.support is not None:
            parent = placement.support.parent_item_id
            support = SupportV1(parent_ref=parent, mate_ref=mate_of[frozenset((item.item_id, parent))],
                                relation=placement.support.relation)
        binding = None
        faces = None
        if item.role is ItemRoleV1.OPTIC:
            component = self.components.get(item.design_instance_id or "")
            if component is None or component.product is None:
                raise _fail(AssemblyPlanErrorCode.PROJECT_REJECTED,
                            f"{item.item_id}: {item.design_instance_id!r} is not a catalog component of the Project")
            version = component.product.catalog_version or self.r.bindings.optical_catalog.catalog_version
            binding = DesignBindingV1(instance_id=component.instance_id, optical_sku=component.product.optical_sku,
                                      catalog_version=version)
            faces_of = getattr(self.c, "optic_faces", None)
            faces = faces_of(item) if faces_of is not None else None
        table = item.identity.kind is ItemIdentityKindV1.CUSTOMER_TABLE
        return PreviewPartV1(
            part_ref=item.item_id,
            category=CATEGORY[item.role],
            identity=self._scene_identity(item),
            design_binding=binding,
            commercial_line_ref=None if table else self._line(item),
            pose_in_world=_pose_of(tuple(placement.pose_in_lab.position_mm), tuple(placement.pose_in_lab.quaternion_wxyz)),
            support=support,
            assembly_order=placement.assembly_order,
            datums=tuple(DatumFrameV1(datum_key=k, pose_in_part=v) for k, v in sorted(datums.items())),
            geometry=PartGeometryV1(asset_path=f"geometry/{item.item_id}.glb", fidelity=GeometryFidelityV1.NOMINAL_SHAPE,
                                    bounds_min_mm=_vec(mesh.bounds_min_mm), bounds_max_mm=_vec(mesh.bounds_max_mm),
                                    triangle_count=mesh.triangle_count),
            appearance=self._appearance(item),
            faces=faces,
        )

    def _line(self, item: PlanItemV1) -> str:
        line = self.line_of.get(item.item_id)
        if line is None:
            raise _fail(AssemblyPlanErrorCode.PLAN_DIR_INCONSISTENT, f"{item.item_id} is on no line of the design BOM")
        return line

    def _mates(self, part_items: list[PlanItemV1], datums: dict[str, dict[str, PoseV1]]) -> tuple[MateV1, ...]:
        refs = {i.item_id for i in part_items}
        rows = []
        for link in self.r.links:
            if link.a_item_id not in refs or link.b_item_id not in refs:
                continue
            a, b = self.items[link.a_item_id], self.items[link.b_item_id]
            try:
                da, db = datums[a.item_id][link.datum_a], datums[b.item_id][link.datum_b]
            except KeyError:
                raise _fail(AssemblyPlanErrorCode.PLAN_DIR_INCONSISTENT,
                            f"link {link.link_id} names a datum its parts do not have") from None
            frame_a = _compose(a.placement.pose_in_lab, da)  # type: ignore[union-attr]
            frame_b = _compose(b.placement.pose_in_lab, db)  # type: ignore[union-attr]
            interface = link.interface
            fastener = None
            if interface is not None and interface.fastener is not None:
                fastener = MateFastenerV1(kind=interface.fastener.kind.value,
                                          thread_designation=metric_designation(interface.fastener.thread))
            rows.append((a.item_id, link.datum_a, b.item_id, link.datum_b, _relative(frame_a, frame_b),
                         interface.designation if interface is not None else None, fastener))
        return tuple(MateV1(mate_ref=f"mate-{n}", part_a_ref=a, datum_a=da, part_b_ref=b, datum_b=db, b_in_a=pose,
                            interface_designation=designation, fastener=fastener)
                     for n, (a, da, b, db, pose, designation, fastener) in enumerate(sorted(rows, key=lambda r: r[:4]), start=1))

    def _settings(self, part_items: list[PlanItemV1]) -> tuple[SettingV1, ...]:
        rows = [SettingV1(part_ref=i.item_id, kind=s.kind, value=s.value, unit=s.unit.value, basis=SETTING_BASIS[s.basis])
                for i in part_items for s in i.placement.settings]  # type: ignore[union-attr]
        return tuple(sorted(rows, key=lambda s: (s.part_ref, s.kind.value)))

    def _fixations(self, part_items: list[PlanItemV1]) -> tuple[FixationV1, ...]:
        rows = []
        for item in part_items:
            fixation = item.placement.fixation  # type: ignore[union-attr]
            if fixation is None:
                continue
            # the fixing screws are standard hardware, not lines of the design BOM
            rows.append(FixationV1(part_ref=item.item_id, mode=FixationModeV1.SLOT,
                                   board_holes=tuple(tuple(h) for h in fixation.board_holes),
                                   fastener=FastenerNoteV1(thread_designation=metric_designation(fixation.thread),
                                                           on_commercial_bom=False)))
        return tuple(sorted(rows, key=lambda f: f.part_ref))

    # ---------------------------------------------------------------------------------- board

    def _board_top(self) -> float:
        pose = self.board.placement.pose_in_lab  # type: ignore[union-attr]
        return _clean(pose.position_mm[2] + self._board_size()[2], POSITION_DECIMALS)

    def _hole_grid(self) -> BoardHoleGridV1:
        pose = self.board.placement.pose_in_lab  # type: ignore[union-attr]
        if self.board.identity.kind is ItemIdentityKindV1.CUSTOMER_TABLE:
            table = self.r.derive_options.customer_table
            size = (table.size_x_mm, table.size_y_mm)  # type: ignore[union-attr]
            pitch, edge, thread = table.pitch_mm, table.edge_offset_mm, table.thread  # type: ignore[union-attr]
        else:
            doc = self._identity_doc(self.board)
            size, pitch, edge, thread = (doc.size_x_mm, doc.size_y_mm), doc.pitch_mm, doc.edge_offset_mm, doc.thread
        count = tuple(int(round((s - 2.0 * edge) / pitch)) + 1 for s in size)
        return BoardHoleGridV1(origin_xy_mm=(_clean(pose.position_mm[0] + edge, POSITION_DECIMALS),
                                             _clean(pose.position_mm[1] + edge, POSITION_DECIMALS)),
                               pitch_mm=(pitch, pitch), count=count, thread_designation=metric_designation(thread))

    def _breadboard(self) -> BreadboardV1:
        pose = self.board.placement.pose_in_lab  # type: ignore[union-attr]
        size_x, size_y, thickness = self._board_size()
        x0, y0 = pose.position_mm[0], pose.position_mm[1]
        return BreadboardV1(part_ref=self.board.item_id, top_z_mm=self._board_top(), thickness_mm=thickness,
                            outline_min_xy_mm=(_clean(x0, POSITION_DECIMALS), _clean(y0, POSITION_DECIMALS)),
                            outline_max_xy_mm=(_clean(x0 + size_x, POSITION_DECIMALS), _clean(y0 + size_y, POSITION_DECIMALS)),
                            hole_grid=self._hole_grid(),
                            supplied_by="CUSTOMER" if self.board.identity.kind is ItemIdentityKindV1.CUSTOMER_TABLE else None)

    # ---------------------------------------------------------------------------------- anchors

    def _anchor_position(self, item: PlanItemV1) -> tuple[Vec3, Quat]:
        if item.placement is not None:
            pose = item.placement.pose_in_lab
            return _vec(tuple(pose.position_mm)), _unit_q(tuple(pose.quaternion_wxyz))  # type: ignore[arg-type]
        component = self.components[item.design_instance_id]  # type: ignore[index]
        q = _q_mul(tuple(self.frame.quaternion_wxyz), tuple(component.nominal_pose.quaternion_wxyz))  # type: ignore[arg-type]
        return _vec(_apply(self.frame, tuple(component.nominal_pose.position_mm))), _unit_q(q)  # type: ignore[arg-type]

    def _customer_supply(self, item: PlanItemV1, orientation: Quat) -> CustomerSupplyV1:
        reason = ANCHOR_REASON.get((item.role, item.supply, item.supply_reason))
        if reason is None:
            raise _fail(AssemblyPlanErrorCode.PLAN_DIR_INCONSISTENT,
                        f"{item.item_id}: no scene reason for supply {item.supply.value} / {item.supply_reason}")
        if item.role is ItemRoleV1.DECLARED_ENVELOPE:
            role = DECLARED_ENVELOPE_ROLE
        else:
            role = self.components[item.design_instance_id].optical_role.value  # type: ignore[index]
        catalog = reason in CATALOG_REASONS
        sku = item.identity.opensci_sku if catalog else None
        envelope = None
        if item.envelope is not None and (catalog or reason is CustomerSupplyReasonV1.NOT_IN_CATALOG):
            diameter, z_min, z_max = envelope_cylinder(item.envelope)
            envelope = ReservedEnvelopeV1(basis="CATALOG_DESIGN_GEOMETRY" if catalog else "PROJECT_DECLARED_GEOMETRY",
                                          diameter_mm=_clean(diameter, POSITION_DECIMALS), z_min_mm=_clean(z_min, POSITION_DECIMALS),
                                          z_max_mm=_clean(z_max, POSITION_DECIMALS))
        return CustomerSupplyV1(reason=reason, optical_role=role, opensci_sku=sku, orientation_wxyz=orientation,
                                reserved_envelope=envelope)

    def _beam_reference(self, board: BreadboardV1, parts: set[str]) -> tuple[BeamReferenceV1, dict[str, str]]:
        anchors: list[DesignAnchorV1] = []
        anchor_items: dict[str, str] = {}
        for item in self.r.items:
            if item.role not in (ItemRoleV1.OPTIC, ItemRoleV1.COMPONENT, ItemRoleV1.DECLARED_ENVELOPE):
                continue
            instance = item.design_instance_id or item.item_id  # a declared envelope's anchor is its item id
            position, orientation = self._anchor_position(item)
            customer = item.is_anchor
            anchors.append(DesignAnchorV1(
                instance_id=instance, anchor_kind=DesignAnchorKindV1.COMPONENT, position_mm=position,
                realized_by_part_ref=item.item_id if item.item_id in parts else None, customer_supplied=customer,
                customer_supply=self._customer_supply(item, orientation) if customer else None))
            anchor_items[instance] = item.item_id
        for observation in self.project.observations:
            pose = self.project.observation_design_pose(observation)
            anchors.append(DesignAnchorV1(instance_id=observation.observation_id, anchor_kind=DesignAnchorKindV1.OBSERVATION,
                                          position_mm=_vec(_apply(self.frame, tuple(pose.position_mm)))))  # type: ignore[arg-type]
        heights = {_clean(a.position_mm[2] - board.top_z_mm, POSITION_DECIMALS) for a in anchors if a.realized_by_part_ref}
        record = self._bound_record()
        axes = tuple(sorted((BeamAxisV1(path_key=a.path_key, basis="DESIGN_NOMINAL_AXIS", wavelength_nm=a.wavelength_nm,
                                        through=a.through, segment_roles=a.segment_roles, notes=a.notes,
                                        points_mm=tuple(_vec(_apply(self.frame, tuple(p))) for p in a.points_design_mm))
                             for a in getattr(self.c, "beam_axes", ())), key=lambda a: _natural(a.path_key)))
        beam = BeamReferenceV1(
            design_frame_in_world=self.frame,
            engine_input_hash=record.input_hash if record is not None else None,
            engine_result_hash=record.result_hash if record is not None else None,
            optical_axis_height_mm=heights.pop() if len(heights) == 1 else None,
            design_anchors=tuple(sorted(anchors, key=lambda a: (a.anchor_kind.value, a.instance_id))),
            beam_axes=axes,
        )
        return beam, anchor_items

    # ---------------------------------------------------------------------------------- DOFs

    def _dofs(self, parts: set[str], beam: BeamReferenceV1) -> tuple[DofImplementationV1, ...]:
        dof_frame = getattr(self.c, "dof_frame", None)
        customer = {a.instance_id for a in beam.design_anchors if a.customer_supplied}
        out = []
        for item in self.r.items:
            if item.role not in (ItemRoleV1.OPTIC, ItemRoleV1.COMPONENT) or item.design_instance_id is None:
                continue
            instance = item.design_instance_id
            component = self.components.get(instance)
            if component is None:
                continue
            realized = item.item_id in parts
            if not realized and instance not in customer:
                continue  # an optic of an unresolved chain: no hardware delivers or holds its DOFs here
            rotation = tuple(component.nominal_pose.quaternion_wxyz)
            if realized and dof_frame is not None:
                rotation = tuple(dof_frame(rotation))
            world_q = _q_mul(tuple(self.frame.quaternion_wxyz), rotation)  # type: ignore[arg-type]
            results = {d.axis: d for d in item.placement.dofs} if realized and item.placement is not None else {}
            ranges = {r.range_id: r for r in item.placement.ranges} if realized and item.placement is not None else {}
            for dof in sorted(component.retained_dofs, key=lambda d: _DOF_ORDER.index(d.axis.value)):
                direction = _unit(_rotate(world_q, _AXIS_VECTORS[dof.axis.value[1].upper()]))  # type: ignore[arg-type]
                out.append(self._dof(instance, item.item_id if realized else None, dof.axis, dof.control, direction,
                                     results.get(dof.axis), ranges))
        return tuple(sorted(out, key=lambda d: (d.design_instance_id, _DOF_ORDER.index(d.axis.value))))

    @staticmethod
    def _dof(instance: str, optic_ref: str | None, axis: Any, requested: DofControlIntentV1, direction: Vec3,
             result: DofResultV1 | None, ranges: Mapping[str, Any]) -> DofImplementationV1:
        undelivered = DofImplementationV1(design_instance_id=instance, optic_part_ref=optic_ref, axis=axis,
                                          requested_control=requested, delivered_control=DeliveredControlV1.NONE,
                                          status=DofDeliveryStatusV1.NOT_DELIVERED, mechanism=DofMechanismV1.NONE,
                                          world_direction=direction)
        if result is None or result.status is DofResultStatusV1.NOT_DELIVERED or result.range_id not in ranges:
            return undelivered
        declared = ranges[result.range_id]
        cosine = sum(a * b for a, b in zip(direction, declared.axis_lab, strict=True))
        travel: tuple[float, float] | None = None
        if cosine >= PARALLEL_COSINE:
            travel = (_clean(declared.min, POSITION_DECIMALS), _clean(declared.max, POSITION_DECIMALS))
        elif cosine <= -PARALLEL_COSINE:
            travel = (_clean(-declared.max, POSITION_DECIMALS), _clean(-declared.min, POSITION_DECIMALS))
        manual = requested is DofControlIntentV1.NUMERICAL
        return DofImplementationV1(
            design_instance_id=instance, optic_part_ref=optic_ref, axis=axis, requested_control=requested,
            delivered_control=DeliveredControlV1.MANUAL,
            status=DofDeliveryStatusV1.DELIVERED_AS_MANUAL if manual else DofDeliveryStatusV1.DELIVERED,
            mechanism=result.mechanism, implemented_by_part_ref=declared.provided_by, moved_part_ref=declared.provided_by,
            world_direction=direction, travel_min=travel[0] if travel else None, travel_max=travel[1] if travel else None,
            travel_basis=TRAVEL_BASIS[declared.basis] if travel else None,
            adjustment=AdjustmentCharacterV1(declared.adjustment.value))

    # ---------------------------------------------------------------------------------- notes / bounds

    @staticmethod
    def _notes(dofs: tuple[DofImplementationV1, ...], beam: BeamReferenceV1, board: BreadboardV1) -> tuple[AssemblyNoteV1, ...]:
        items: set[tuple[AssemblyNoteCodeV1, str | None, str | None, Any]] = set()
        (x0, y0), (x1, y1) = board.outline_min_xy_mm, board.outline_max_xy_mm
        external = set()
        for anchor in beam.design_anchors:
            if anchor.anchor_kind is not DesignAnchorKindV1.COMPONENT:
                continue
            if anchor.customer_supplied:
                external.add(anchor.instance_id)
                source = anchor.customer_supply.optical_role == "source"  # type: ignore[union-attr]
                code = AssemblyNoteCodeV1.EXTERNAL_LIGHT_SOURCE if source else AssemblyNoteCodeV1.CUSTOMER_SUPPLIED_COMPONENT
                items.add((code, None, anchor.instance_id, None))
                continue
            if anchor.realized_by_part_ref is None:
                items.add((AssemblyNoteCodeV1.DESIGN_COMPONENT_NOT_SUPPLIED, None, anchor.instance_id, None))
            x, y, _ = anchor.position_mm
            if not (x0 <= x <= x1 and y0 <= y <= y1):
                items.add((AssemblyNoteCodeV1.DESIGN_COMPONENT_OUTSIDE_BREADBOARD, None, anchor.instance_id, None))
        for dof in dofs:
            code = dof_note_code(dof, customer_supplied=dof.design_instance_id in external)
            if code is not None:
                items.add((code, None, dof.design_instance_id, dof.axis))
        requested = {(d.design_instance_id, d.axis): d.requested_control for d in dofs}
        reserved = {a.instance_id for a in beam.design_anchors
                    if a.customer_supplied and a.customer_supply.reserved_envelope is not None}  # type: ignore[union-attr]
        unmounted = {a.instance_id for a in beam.design_anchors if a.customer_supplied
                     and a.customer_supply.reason is CustomerSupplyReasonV1.OPENSCI_SUPPLIED_UNMOUNTED}  # type: ignore[union-attr]
        out = []
        for code, part_ref, instance, axis in items:
            is_dof = NOTE_SUBJECTS[code] is NoteSubjectV1.DOF
            out.append(AssemblyNoteV1(
                code=code, severity="WARNING" if code in WARNING_NOTE_CODES else "INFO",
                part_ref=part_ref, design_instance_id=instance, dof_axis=axis,
                message=note_message(code, axis=axis, instance=instance,
                                     requested=requested.get((instance, axis)) if is_dof else None,
                                     reserved_space=instance in reserved, supplied_unmounted=instance in unmounted)))
        order = list(AssemblyNoteCodeV1)
        return tuple(sorted(out, key=lambda d: (order.index(d.code), d.part_ref or "", d.design_instance_id or "",
                                                d.dof_axis.value if d.dof_axis else "")))

    @staticmethod
    def _bounds(parts: list[PreviewPartV1], beam: BeamReferenceV1) -> SceneBoundsV1:
        points: list[Vec3] = [tuple(a.position_mm) for a in beam.design_anchors]  # type: ignore[misc]
        points += [tuple(p) for axis in beam.beam_axes for p in axis.points_mm]  # type: ignore[misc]
        for part in parts:
            lo, hi = part.geometry.bounds_min_mm, part.geometry.bounds_max_mm
            for x in (lo[0], hi[0]):
                for y in (lo[1], hi[1]):
                    for z in (lo[2], hi[2]):
                        points.append(_apply(part.pose_in_world, (x, y, z)))
        return SceneBoundsV1(min_mm=_vec(tuple(min(p[i] for p in points) for i in range(3))),
                             max_mm=_vec(tuple(max(p[i] for p in points) for i in range(3))))


def _top_z(primitive: Any) -> float:
    if primitive.kind == "BOX":
        return float(primitive.max_mm[2])
    if primitive.axis == "z":
        return float(primitive.base_mm[2]) + float(primitive.length_mm)
    return float(primitive.base_mm[2]) + float(primitive.radius_mm)


def _bottom_z(primitive: Any) -> float:
    if primitive.kind == "BOX":
        return float(primitive.min_mm[2])
    if primitive.axis == "z":
        return float(primitive.base_mm[2])
    return float(primitive.base_mm[2]) - float(primitive.radius_mm)


def _natural(key: str) -> tuple[Any, ...]:
    """A beam axis key in reading order: ``beam-2`` before ``beam-10``."""

    return tuple(int(part) if part.isdigit() else part for part in re.split(r"(\d+)", key))


__all__ = [
    "PROJECTOR_ID",
    "PROJECTOR_VERSION",
    "BeamAxisInput",
    "OmittedItem",
    "PlanProjection",
    "PlanProjectionInputs",
    "ProjectionContext",
    "design_bom_lines",
    "design_frame_pose",
    "envelope_cylinder",
    "metric_designation",
    "project_plan",
    "project_plan_package",
]
