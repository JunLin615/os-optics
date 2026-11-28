"""A Project as the assembly kernel's request, and who supplies each of its components.

``classify`` decides, for every design component in design order, how the plan holds it and who supplies it, from the
public optical catalog and the Project only:

* a component that is no catalog product: a COMPONENT anchor the customer supplies -- ``CUSTOMER_LIGHT_SOURCE`` (a
  source) or ``NOT_IN_CATALOG`` (anything else; a declared optical model brings its geometry and beam facts,
  ``declared.py``);
* a catalog product that is not ORDERABLE: an ANCHOR optic, supply UNRESOLVED (``NOT_ORDERABLE``);
* a product the customer owns (``customer_owned``): an ANCHOR optic, CUSTOMER_OWNED (``CUSTOMER_OWNED``);
* an ORDERABLE product OpenSci does not mount (``opensci_mounting`` NOT_AVAILABLE, or delivered in its housing): an
  ANCHOR optic OpenSci supplies unmounted (``OPENSCI_SUPPLIED_UNMOUNTED``);
* an ORDERABLE product the kernel cannot hold (no kernel category for its form, or a polyhedral design geometry): an
  ANCHOR optic OpenSci supplies, with ``MISSING_MECHANICAL_DATA``;
* every other product: a CHAIN optic OpenSci supplies, on mount, post and holder.

An anchor keeps its design pose; a catalog product's design geometry is the space reserved for it (its envelope, and the
column below it down to the board), and the nominal beam visits it as its public form says (a mirror folds the beam, a
plate splitter branches it, a cube, a prism or an off-axis mirror sends it out of its published ports). ``kernel_request``
builds the kernel's request from the CHAIN optics, the anchors as external design anchors, and the Project's observation
planes (beam-path endpoints only).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any

from .._vendor.shared_contracts.design_catalog import FACE_NORMAL_FORMS, PORTS_FORMS, REFLECT_ONLY_FORMS
from .._vendor.shared_contracts.design_catalog.enums import OpticalCategory
from .._vendor.shared_contracts.optical_design import OpticalRoleV1
from .contract import MountingV1, SupplyReasonV1, SupplyV1
from .declared import PROJECT_DECLARED_GEOMETRY, declared_optic
from .kernel import (AssemblyResolutionRequestV2, ChainPins, DofRequest, ExternalRequest, ObservationRequest, OpticMechanicsV1, OpticRequest,
                     PortRequest, ReservedEnvelope)
from .kernel.beam import PORTS_CATEGORY
from .optics import MissingMechanics, mechanics_of, mounted_by_opensci, orderable

#: The envelope basis of a catalog product's reserved space.
CATALOG_DESIGN_GEOMETRY = "CATALOG_DESIGN_GEOMETRY"

#: A public catalog category -> the kernel's optic category of a transmissive product of it (a face-normal form is a
#: mirror or a plate splitter whatever its category).
_KERNEL_CATEGORY = {
    OpticalCategory.LENS: "LENS",
    OpticalCategory.MIRROR: "MIRROR",
    OpticalCategory.BEAMSPLITTER: "BEAMSPLITTER_PLATE",
    OpticalCategory.FILTER: "FILTER",
    OpticalCategory.WINDOW: "WINDOW",
    OpticalCategory.WAVEPLATE: "WAVEPLATE",
    OpticalCategory.POLARIZER: "POLARIZER",
}


@dataclass(frozen=True)
class ClassifiedComponent:
    """One design component as the plan holds it (module docstring)."""

    instance_id: str
    optical_role: str
    #: OPTIC (a catalog product) or COMPONENT (anything else)
    item_role: str
    supply: SupplyV1
    supply_reason: SupplyReasonV1 | None
    mounting: MountingV1 | None
    opensci_sku: str | None = None
    custom_part_key: str | None = None
    display_name: str | None = None
    display_name_zh_cn: str | None = None
    #: the kernel's mechanics of a catalog optic (None: not held, ``missing``)
    mechanics: OpticMechanicsV1 | None = None
    missing: MissingMechanics | None = None
    #: how the nominal beam visits an anchor (a kernel optic category) and its outer size (mm); None: not traced
    beam_category: str | None = None
    size_mm: float | None = None
    #: the geometry reserved for an anchor; None: only its position is kept
    envelope: ReservedEnvelope | None = None
    envelope_basis: str | None = None
    ports: tuple[PortRequest, ...] = ()
    optical_model_id: str | None = None
    #: a declared model that does not say where the beam leaves it (``declared.FRAME_REQUIRED`` / ``PORTS_REQUIRED``)
    beam_path_undefined: str | None = None
    #: the reason a CHAIN optic became an anchor during the derivation (``UNSUPPORTED_OPTIC_ORIENTATION``,
    #: ``MOUNT_NOT_FOUND``), with its details
    demoted: str | None = None
    demoted_details: dict[str, Any] = field(default_factory=dict)

    @property
    def chain(self) -> bool:
        return self.mounting is MountingV1.CHAIN

    def as_anchor(self, reason: str, details: dict[str, Any] | None = None) -> ClassifiedComponent:
        """This CHAIN optic placed as an anchor instead (the customer mounts it)."""

        return replace(self, mounting=MountingV1.ANCHOR, demoted=reason, demoted_details=dict(details or {}))


def _decimal(value: Any) -> float:
    return float(Decimal(str(value)))


def envelope_of(geometry: Any) -> ReservedEnvelope:
    """The cylinder about local +Z that holds a product's design geometry: the geometry itself when it is a cylinder;
    for a box or a convex polyhedron, the cylinder through its corner farthest from local Z over its z range."""

    if geometry.kind == "CYLINDER":
        return ReservedEnvelope(diameter_mm=_decimal(geometry.diameter_mm), z_min_mm=_decimal(geometry.z_min_mm),
                                z_max_mm=_decimal(geometry.z_max_mm))
    if geometry.kind == "BOX":
        cx, cy, cz = (_decimal(v) for v in geometry.center_mm)
        hx, hy, hz = (_decimal(v) for v in geometry.half_extents_mm)
        corners = [(cx + sx * hx, cy + sy * hy, cz + sz * hz) for sx in (-1.0, 1.0) for sy in (-1.0, 1.0) for sz in (-1.0, 1.0)]
    else:
        corners = [tuple(_decimal(c) for c in vertex) for vertex in geometry.vertices_mm]
    radius = max(math.hypot(x, y) for x, y, _ in corners)
    return ReservedEnvelope(diameter_mm=2.0 * radius, z_min_mm=min(z for *_, z in corners), z_max_mm=max(z for *_, z in corners))


def outer_size_mm(specifications: Any) -> float:
    """The outer size the kernel's beam check uses: a round element's diameter, else its shorter outer edge across local
    X / Y (the aperture a beam along local Z meets)."""

    if specifications.outer_diameter_mm is not None:
        return _decimal(specifications.outer_diameter_mm.value)
    x, y, _ = (_decimal(v) for v in specifications.outer_dimensions_mm.value)
    return min(x, y)


def public_ports(product: Any) -> tuple[PortRequest, ...]:
    """The published ports of a catalog product placed by its ports (its own frame, mm); empty for every other form."""

    frame = getattr(product, "frame", None)
    if frame is None or not frame.has_ports:
        return ()
    return tuple(PortRequest(port.port_id, tuple(_decimal(v) for v in port.point_mm), tuple(_decimal(v) for v in port.direction))  # type: ignore[arg-type]
                 for port in frame.ports or ())


def engineering_facts(product: Any) -> dict[str, Any]:
    """The public facts of a catalog product the kernel needs for an anchor: how the beam visits it, its outer size and
    the cylinder holding its design geometry."""

    ports: tuple[PortRequest, ...] = ()
    if product.form in PORTS_FORMS:
        ports = public_ports(product)
        category = PORTS_CATEGORY if ports else None
    elif product.form in REFLECT_ONLY_FORMS:
        category = "MIRROR"
    elif product.form in FACE_NORMAL_FORMS:
        category = "BEAMSPLITTER_PLATE"
    else:
        category = _KERNEL_CATEGORY.get(product.category)
    return {"beam_category": category, "size_mm": outer_size_mm(product.specifications), "envelope": envelope_of(product.design_geometry),
            "envelope_basis": CATALOG_DESIGN_GEOMETRY, "ports": ports}


def classify(project: Any, catalog: Any, customer_owned: frozenset[str] | set[str] | tuple[str, ...] = ()) -> tuple[ClassifiedComponent, ...]:
    """Every design component of ``project`` with how the plan holds it and who supplies it (module docstring), in
    design order. ``catalog``: the public optical catalog (``PublicOpticalCatalogV2``) the Project's products are in."""

    owned = set(customer_owned)
    out: list[ClassifiedComponent] = []
    for component in project.components:
        role = component.optical_role.value
        if component.product is None:
            reason = (SupplyReasonV1.CUSTOMER_LIGHT_SOURCE if component.optical_role is OpticalRoleV1.SOURCE
                      else SupplyReasonV1.NOT_IN_CATALOG)
            key = component.custom_part.custom_part_key if component.custom_part is not None else None
            base = ClassifiedComponent(component.instance_id, role, "COMPONENT", SupplyV1.CUSTOMER_OWNED, reason, None,
                                       custom_part_key=key)
            declared = declared_optic(project, component)
            if declared is not None:
                base = replace(base, beam_category=declared.beam_category, size_mm=declared.size_mm, envelope=declared.envelope,
                               envelope_basis=PROJECT_DECLARED_GEOMETRY if declared.envelope is not None else None,
                               ports=declared.ports, optical_model_id=declared.optical_model_id,
                               beam_path_undefined=declared.beam_path_undefined)
            out.append(base)
            continue
        sku = component.product.optical_sku
        product = catalog.get(sku)
        if product is None:
            raise KeyError(f"the optical catalog has no product {sku} (component {component.instance_id})")
        facts = engineering_facts(product)
        names = {"display_name": product.display_name, "display_name_zh_cn": product.display_name_zh_cn}
        mechanics = mechanics_of(product)
        held = mechanics if isinstance(mechanics, OpticMechanicsV1) else None
        missing = mechanics if isinstance(mechanics, MissingMechanics) else None
        common = {"opensci_sku": sku, **names, "mechanics": held, "missing": missing, **facts}
        if not orderable(product):
            out.append(ClassifiedComponent(component.instance_id, role, "OPTIC", SupplyV1.UNRESOLVED, SupplyReasonV1.NOT_ORDERABLE,
                                           MountingV1.ANCHOR, **common))
        elif component.instance_id in owned:
            out.append(ClassifiedComponent(component.instance_id, role, "OPTIC", SupplyV1.CUSTOMER_OWNED, SupplyReasonV1.CUSTOMER_OWNED,
                                           MountingV1.ANCHOR, **common))
        elif not mounted_by_opensci(product):
            out.append(ClassifiedComponent(component.instance_id, role, "OPTIC", SupplyV1.OPENSCI,
                                           SupplyReasonV1.OPENSCI_SUPPLIED_UNMOUNTED, MountingV1.ANCHOR, **common))
        elif held is None:
            out.append(ClassifiedComponent(component.instance_id, role, "OPTIC", SupplyV1.OPENSCI, None, MountingV1.ANCHOR, **common))
        else:
            out.append(ClassifiedComponent(component.instance_id, role, "OPTIC", SupplyV1.OPENSCI, None, MountingV1.CHAIN, **common))
    return tuple(out)


def _dofs(component: Any) -> tuple[DofRequest, ...]:
    return tuple(DofRequest(d.axis.value, d.control.value) for d in component.retained_dofs)


def aperture_corners(aperture: Any) -> tuple[tuple[float, float], ...]:
    """The extreme points of an observation plane's aperture in its own (u, v) (its centre when it has none)."""

    if aperture is None:
        return ((0.0, 0.0),)
    cx, cy = (float(v) for v in aperture.center_offset_mm)
    if getattr(aperture, "shape", None) == "rectangle":
        hu, hv = float(aperture.half_width_mm), float(aperture.half_height_mm)
    else:
        hu = hv = float(aperture.radius_mm)
    return tuple((cx + su * hu, cy + sv * hv) for su in (-1.0, 1.0) for sv in (-1.0, 1.0))


def optic_request(component: Any, classified: ClassifiedComponent, product: Any, pins: ChainPins | None = None) -> OpticRequest:
    """The kernel's request of one CHAIN optic."""

    pose = component.nominal_pose
    return OpticRequest(component.instance_id, classified.opensci_sku, component.optical_role.value, tuple(pose.position_mm),  # type: ignore[arg-type]
                        tuple(pose.quaternion_wxyz), _dofs(component), classified.mechanics, ports=public_ports(product),  # type: ignore[arg-type]
                        pins=pins)


def chain_optic(component: Any, product: Any, pins: ChainPins | None = None) -> OpticRequest:
    """The kernel's request of one Project component holding a catalog ``product``, for a single-chain search
    (``chain.place_single_chain``): its mechanics (``optics.mechanics_of``; ``ValueError`` when the kernel cannot hold the
    product), its published ports and its retained DOFs."""

    mechanics = mechanics_of(product)
    if not isinstance(mechanics, OpticMechanicsV1):
        raise ValueError(f"the kernel holds no mechanics of {product.optical_sku} ({mechanics.reason})")
    pose = component.nominal_pose
    return OpticRequest(component.instance_id, product.optical_sku, component.optical_role.value, tuple(pose.position_mm),  # type: ignore[arg-type]
                        tuple(pose.quaternion_wxyz), _dofs(component), mechanics, ports=public_ports(product), pins=pins)  # type: ignore[arg-type]


def external_request(component: Any, classified: ClassifiedComponent) -> ExternalRequest:
    """The kernel's external design anchor of one anchored component (a light source starts the beam)."""

    pose = component.nominal_pose
    role = "source" if classified.supply_reason is SupplyReasonV1.CUSTOMER_LIGHT_SOURCE else classified.optical_role
    return ExternalRequest(component.instance_id, role, tuple(pose.position_mm), tuple(pose.quaternion_wxyz), _dofs(component),  # type: ignore[arg-type]
                           beam_category=classified.beam_category, size_mm=classified.size_mm, envelope=classified.envelope,
                           ports=classified.ports)


def observations(project: Any) -> tuple[ObservationRequest, ...]:
    """The Project's observation planes at the design-frame pose the run evaluates (a plane attached to a component
    composed with that component's pose): nominal beam-path endpoints only."""

    return tuple(ObservationRequest(o.observation_id, tuple(pose.position_mm), tuple(pose.quaternion_wxyz),  # type: ignore[arg-type]
                                    aperture_corners(o.aperture))
                 for o in project.observations for pose in (project.observation_design_pose(o),))


def kernel_request(project: Any, catalog: Any, classified: tuple[ClassifiedComponent, ...], *,
                   max_board_size_mm: tuple[float, float] | None = None, beam_height_mm: float | None = None,
                   pins: dict[str, ChainPins] | None = None) -> AssemblyResolutionRequestV2:
    """The kernel's request of ``project``: every CHAIN optic of ``classified`` with its mechanics (and its plan's pinned
    hardware, ``pins`` by instance id), every other component as an external design anchor, and the observation planes."""

    by_instance = {c.instance_id: c for c in classified}
    optics, externals = [], []
    for component in project.components:
        c = by_instance[component.instance_id]
        if c.chain:
            optics.append(optic_request(component, c, catalog.get(c.opensci_sku), (pins or {}).get(c.instance_id)))
        else:
            externals.append(external_request(component, c))
    return AssemblyResolutionRequestV2(optics=tuple(optics), externals=tuple(externals), observations=observations(project),
                                       max_board_size_mm=max_board_size_mm, beam_height_mm=beam_height_mm)


__all__ = ["CATALOG_DESIGN_GEOMETRY", "ClassifiedComponent", "aperture_corners", "chain_optic", "classify", "engineering_facts", "envelope_of",
           "external_request", "kernel_request", "observations", "optic_request", "outer_size_mm", "public_ports"]
