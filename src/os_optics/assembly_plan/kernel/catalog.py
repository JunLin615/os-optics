"""The kernel's read-only view of the public mechanical catalog (``opensci.public_mechanical_catalog`` 1.0).

A ``KernelCatalog`` holds one frozen view per catalog identity (``KPost``, ``KHolder``, ``KMount``, ``KBoard``): exactly
the engineering parameters the kernel's rules read, the per-parameter basis, the neutral visual primitives and the
identity itself (``OS-MECH-*`` or a customer's ``USER-MECH-*``), which is also the kernel's last tie-break. One identity is
one profile: a published identity realized by several equivalent parts carries the conservative profile of them all
(``IdentityProfileV1``), so a fit judged on the identity holds for each of them.

Mechanical part frames (``frames = opensci.mechanical_part_frames.v1``) are yaw-only frames with +z up:

* POST: origin at the bottom face centre; the top stud face is at z = length;
* POST_HOLDER: origin at the bottom face centre on the post axis; the bore opens at z = height; an integrated fork
  extends along +x;
* MOUNT: origin at the post-interface point (the bottom tapped hole, on the post axis); +x is the optical axis of the
  held optic (for a reflector, the outward face normal); the optic's seat centre is at ``(seat_x, 0, centre_height)``;
* BREADBOARD: origin at a board corner on the board's bottom face; the top is at z = thickness, holes lie on the
  top at ``(edge + i * pitch, edge + j * pitch)``.

``OpticMechanicsV1`` is the kernel's view of one OpenSci optical product (``assembly_plan.optics`` derives it from the
public optical catalog).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import Literal

from ..mechanical.model import (
    DEFAULT_MAX_GEOMETRY_CHECKS,
    FACT_BASES,
    BoxV1,
    BreadboardIdentityV1,
    ForkBaseV1,
    MechanicalIdentityBaseV1,
    MechanicalSources,
    MechanismKindV1,
    MountHoldsV1,
    MountIdentityV1,
    MountKindV1,
    MountMechanismV1,
    OpticShapeV1,
    PostFasteningV1,
    PostHolderIdentityV1,
    PostIdentityV1,
    PublicBasisV1,
    PublicResolverPolicyV1,
    VisualPrimitiveV1,
)

#: The kernel's policy is the public derivation policy of the mechanical catalog.
ResolverPolicyV1 = PublicResolverPolicyV1
#: The default colour of a board view built from a customer table (a neutral dark grey).
CUSTOMER_TABLE_COLOR = (0.25, 0.26, 0.28)
#: The kernel's role of each catalog kind (the part roles of a realization).
ROLE_POST = "POST"
ROLE_HOLDER = "POST_HOLDER"
ROLE_MOUNT = "OPTIC_MOUNT"
ROLE_BOARD = "BREADBOARD"

OpticCategory = Literal["LENS", "MIRROR", "BEAMSPLITTER_CUBE", "BEAMSPLITTER_PLATE", "FILTER", "WAVEPLATE", "POLARIZER", "WINDOW"]


@dataclass(frozen=True, kw_only=True)
class KMember:
    """What every catalog identity view carries. ``basis`` maps a parameter to its ``PublicBasisV1`` value; a parameter
    without an entry is not recorded (``basis_of`` is None, which the kernel's fact rule treats as no assumption)."""

    identity: str
    publisher: str = "OPENSCI"
    display_name: str = ""
    display_name_zh_cn: str | None = None
    profile: str = "SINGLE"
    conservative_parameters: tuple[str, ...] = ()
    basis: tuple[tuple[str, str], ...] = ()
    visual: tuple[VisualPrimitiveV1, ...] = ()
    color_rgb: tuple[float, float, float] = CUSTOMER_TABLE_COLOR

    def basis_of(self, parameter: str) -> PublicBasisV1 | None:
        value = dict(self.basis).get(parameter)
        return None if value is None else PublicBasisV1(value)

    @property
    def bases(self) -> frozenset[PublicBasisV1]:
        """Every basis any parameter of the identity rests on."""
        return frozenset(PublicBasisV1(value) for _, value in self.basis)

    @property
    def conservative(self) -> bool:
        return self.profile == "CONSERVATIVE"


@dataclass(frozen=True, kw_only=True)
class KPost(KMember):
    role: Literal["POST"] = ROLE_POST
    diameter_mm: float
    length_mm: float
    top_thread: str  # the stud that enters the mount's post interface


@dataclass(frozen=True, kw_only=True)
class KHolder(KMember):
    role: Literal["POST_HOLDER"] = ROLE_HOLDER
    post_diameter_mm: float
    height_mm: float
    tube_diameter_mm: float
    #: How deep a post may be inserted (mm below the bore opening).
    insertion_min_mm: float
    insertion_max_mm: float
    base: ForkBaseV1


@dataclass(frozen=True, kw_only=True)
class KMount(KMember):
    """``post_fastening`` is the one way the mount is fixed to its post when it is not the plain stud joint (None: the
    plain stud); ``fastening_options`` lists every way the identity allows (several for a conservative profile)."""

    role: Literal["OPTIC_MOUNT"] = ROLE_MOUNT
    mount_kind: MountKindV1
    holds: MountHoldsV1
    post_thread: str  # the bottom tapped hole the post stud enters
    post_fastening: str | None = None
    fastening_options: tuple[str, ...] = (PostFasteningV1.PLAIN_STUD.value,)
    centre_height_mm: float
    #: Position along +x of the stop the optic's rear face rests against (None: the optic is centred on the post axis).
    seat_stop_x_mm: float | None = None
    envelope: BoxV1  # in the mount frame
    mechanisms: tuple[MountMechanismV1, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "mount_kind", MountKindV1(self.mount_kind))  # (the kernel compares kinds by identity)


@dataclass(frozen=True, kw_only=True)
class KBoard(KMember):
    role: Literal["BREADBOARD"] = ROLE_BOARD
    size_x_mm: float
    size_y_mm: float
    thickness_mm: float
    pitch_mm: float
    edge_offset_mm: float  # outermost hole centre to the board edge
    thread: str

    @property
    def hole_count(self) -> tuple[int, int]:
        def count(size: float) -> int:
            return int(round((size - 2 * self.edge_offset_mm) / self.pitch_mm)) + 1

        return count(self.size_x_mm), count(self.size_y_mm)


@dataclass(frozen=True, kw_only=True)
class OpticMechanicsV1:
    """The mechanical facts of one OpenSci optical product (``optical_sku``).

    ``axial_min_mm``/``axial_max_mm``: the optic's extent along its local +Z (optical axis / face normal) in its own
    design frame, e.g. [0, 6.79] for a lens whose frame origin is its first vertex, [-6, 0] for a mirror whose frame
    origin is its reflecting face."""

    optical_sku: str
    category: OpticCategory
    shape: OpticShapeV1
    size_mm: float
    thickness_mm: float
    axial_min_mm: float
    axial_max_mm: float
    transmissive: bool
    visual: tuple[VisualPrimitiveV1, ...]
    color_rgb: tuple[float, float, float]
    #: a mirror with a curved surface (concave, off-axis parabolic): optical power, so it is kept in a linked group
    curved: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "shape", OpticShapeV1(self.shape))  # (the kernel compares shapes by identity)
        if not self.axial_max_mm > self.axial_min_mm:
            raise ValueError("axial extent must be positive")
        if not (self.size_mm > 0 and self.thickness_mm > 0):
            raise ValueError("an optic has a positive size and thickness")
        if not self.visual:
            raise ValueError("an optic has at least one visual primitive")

    @property
    def identity(self) -> str:
        return self.optical_sku


KMechanical = KPost | KHolder | KMount | KBoard


def _common(identity: MechanicalIdentityBaseV1, publisher: str) -> dict[str, object]:
    return {"identity": identity.opensci_sku, "publisher": publisher, "display_name": identity.display_name,
            "display_name_zh_cn": identity.display_name_zh_cn, "profile": identity.profile.kind,
            "conservative_parameters": tuple(identity.profile.conservative_parameters),
            "basis": tuple(sorted(identity.basis.items())), "visual": tuple(identity.visual),
            "color_rgb": tuple(float(c) for c in identity.color_rgb)}


def _fastening(options: Iterable[str]) -> tuple[str | None, tuple[str, ...]]:
    values = tuple(sorted({str(o) for o in options}))
    other = [v for v in values if v != PostFasteningV1.PLAIN_STUD.value]
    return (other[0] if other else None), values


def view_of(identity: MechanicalIdentityBaseV1, publisher: str = "OPENSCI") -> KMechanical:
    """The kernel view of one catalog identity."""

    common = _common(identity, publisher)
    if isinstance(identity, PostIdentityV1):
        return KPost(**common, diameter_mm=identity.diameter_mm, length_mm=identity.length_mm, top_thread=identity.top_thread)
    if isinstance(identity, PostHolderIdentityV1):
        return KHolder(**common, post_diameter_mm=identity.post_diameter_mm, height_mm=identity.height_mm,
                       tube_diameter_mm=identity.tube_diameter_mm, insertion_min_mm=identity.insertion_min_mm,
                       insertion_max_mm=identity.insertion_max_mm, base=identity.base)
    if isinstance(identity, MountIdentityV1):
        fastening, options = _fastening(o.value for o in identity.post_fastening)
        return KMount(**common, mount_kind=identity.mount_kind, holds=identity.holds, post_thread=identity.post_thread,
                      post_fastening=fastening, fastening_options=options, centre_height_mm=identity.centre_height_mm,
                      seat_stop_x_mm=identity.seat_stop_x_mm, envelope=identity.envelope, mechanisms=tuple(identity.mechanisms))
    if isinstance(identity, BreadboardIdentityV1):
        return KBoard(**common, size_x_mm=identity.size_x_mm, size_y_mm=identity.size_y_mm, thickness_mm=identity.thickness_mm,
                      pitch_mm=identity.pitch_mm, edge_offset_mm=identity.edge_offset_mm, thread=identity.thread)
    raise TypeError(f"not a mechanical identity: {type(identity).__name__}")


def table_board(identity: str, *, size_x_mm: float, size_y_mm: float, pitch_mm: float, edge_offset_mm: float, thread: str,
                thickness_mm: float, display_name: str = "Customer optical table") -> KBoard:
    """A board view of a customer's own optical table (no catalog identity; ``identity`` names it in the plan)."""

    box = VisualPrimitiveV1(kind="BOX", finish="DARK", min_mm=(0.0, 0.0, 0.0), max_mm=(size_x_mm, size_y_mm, thickness_mm))
    return KBoard(identity=identity, publisher="CUSTOMER", display_name=display_name, visual=(box,), size_x_mm=size_x_mm,
                  size_y_mm=size_y_mm, thickness_mm=thickness_mm, pitch_mm=pitch_mm, edge_offset_mm=edge_offset_mm,
                  thread=thread)


@dataclass(frozen=True)
class KernelCatalog:
    """The hardware one derivation may select from, and its policy. Every tuple is sorted by identity."""

    policy: PublicResolverPolicyV1
    posts: tuple[KPost, ...]
    holders: tuple[KHolder, ...]
    mounts: tuple[KMount, ...]
    boards: tuple[KBoard, ...]
    _index: dict[str, KMechanical] = field(default_factory=dict, compare=False, repr=False, hash=False)

    def __post_init__(self) -> None:
        for name in ("posts", "holders", "mounts", "boards"):
            items = tuple(sorted(getattr(self, name), key=lambda m: m.identity))
            object.__setattr__(self, name, items)
        members = [*self.posts, *self.holders, *self.mounts, *self.boards]
        index: dict[str, KMechanical] = {}
        for member in members:
            if member.identity in index:
                raise ValueError(f"duplicate identity {member.identity} in the kernel catalog")
            index[member.identity] = member
        if not self.boards:
            raise ValueError("a kernel catalog needs at least one board")
        object.__setattr__(self, "_index", index)

    @classmethod
    def from_identities(cls, identities: Iterable[tuple[MechanicalIdentityBaseV1, str]], policy: PublicResolverPolicyV1,
                        *, boards: Iterable[KBoard] | None = None) -> KernelCatalog:
        """The catalog of ``(identity, publisher)`` pairs; ``boards`` replaces the catalog's boards (a customer table)."""

        views = [view_of(identity, publisher) for identity, publisher in identities]
        own_boards = tuple(v for v in views if isinstance(v, KBoard))
        return cls(policy=policy, posts=tuple(v for v in views if isinstance(v, KPost)),
                   holders=tuple(v for v in views if isinstance(v, KHolder)),
                   mounts=tuple(v for v in views if isinstance(v, KMount)),
                   boards=tuple(boards) if boards is not None else own_boards)

    @classmethod
    def from_sources(cls, sources: MechanicalSources, *, boards: Iterable[KBoard] | None = None) -> KernelCatalog:
        """The catalog of every identity of ``sources`` (the OPENSCI document and the USER documents), under the
        OPENSCI policy (else the policy defaults)."""

        pairs = []
        for loaded in sources.documents:
            for identity in loaded.document.identities:
                pairs.append((identity, loaded.document.publisher))
        return cls.from_identities(pairs, sources.policy, boards=boards)

    def member(self, identity: str) -> KMechanical | None:
        return self._index.get(identity)

    def with_boards(self, boards: Iterable[KBoard]) -> KernelCatalog:
        """This catalog with only ``boards`` to stand on."""
        return replace(self, boards=tuple(boards), _index={})

    def with_policy(self, policy: PublicResolverPolicyV1) -> KernelCatalog:
        return replace(self, policy=policy, _index={})

    def restricted_to(self, identities: Mapping[str, object] | Iterable[str]) -> KernelCatalog:
        """This catalog with only the members named in ``identities`` (boards kept when none is named)."""

        keep = set(identities)
        boards = tuple(b for b in self.boards if b.identity in keep) or self.boards
        return replace(self, posts=tuple(p for p in self.posts if p.identity in keep),
                       holders=tuple(h for h in self.holders if h.identity in keep),
                       mounts=tuple(m for m in self.mounts if m.identity in keep), boards=boards, _index={})


__all__ = [
    "CUSTOMER_TABLE_COLOR", "DEFAULT_MAX_GEOMETRY_CHECKS", "FACT_BASES", "BoxV1", "ForkBaseV1", "KBoard", "KHolder", "KMechanical",
    "KMember", "KMount", "KPost", "KernelCatalog", "MechanismKindV1", "MountHoldsV1", "MountKindV1", "MountMechanismV1",
    "OpticCategory", "OpticMechanicsV1", "OpticShapeV1", "PublicBasisV1", "ResolverPolicyV1", "VisualPrimitiveV1", "table_board",
    "view_of",
]
