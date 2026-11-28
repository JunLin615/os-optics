"""The mechanical facts of an OpenSci optical product, from the public optical catalog (``OpticMechanicsV1``).

The kernel holds an optic by its shape, outer size, axial extent and whether light passes through it; all of these are
public catalog facts of the product (``design_geometry``, ``form``), so no per-optic mechanics are published apart from
it. The derivation:

* category: the kernel optic category of the product's form (``FORM_CATEGORY``: every lens form is a ``LENS``; flat,
  concave and off-axis parabolic mirrors a ``MIRROR``; plate splitters, beam samplers and dichroic mirrors a
  ``BEAMSPLITTER_PLATE``; cube splitters a ``BEAMSPLITTER_CUBE``; the band, edge, notch and neutral-density filters a
  ``FILTER``; the plane window a ``WINDOW``; the wave plates a ``WAVEPLATE``; the linear polarizer and the Glan prisms a
  ``POLARIZER``). Another form (a prism, a retroreflector, a Wollaston or Rochon prism) has no kernel category: the plan
  places it as an anchor with ``MISSING_MECHANICAL_DATA``;
* geometry: a CYLINDER design geometry is a ROUND optic of its diameter over its z range; a BOX one of three equal half
  extents of a cube splitter is a CUBE of that edge; any other BOX a RECTANGULAR optic of its shorter edge across local
  X / Y over its z range; a CONVEX_POLYHEDRON is not held (``MISSING_MECHANICAL_DATA``);
* the visual: one GLASS primitive of the design geometry; the colour by whether light passes through it.

Numbers are the catalog's decimals as floats rounded to ``MECHANICS_DECIMALS``.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .._vendor.shared_contracts.design_catalog.enums import CommercialReadiness, DeliveredForm, OpenSciMounting, OpticalForm
from .kernel.catalog import OpticMechanicsV1, OpticShapeV1, VisualPrimitiveV1

#: Decimals the mechanics keep (mm).
MECHANICS_DECIMALS = 6
#: The colour of a transmissive optic and of a reflector.
TRANSMISSIVE_COLOR = (0.62, 0.82, 0.9)
REFLECTOR_COLOR = (0.78, 0.8, 0.84)

_LENS_FORMS = ("PLANO_CONVEX_LENS", "BICONVEX_LENS", "PLANO_CONCAVE_LENS", "BICONCAVE_LENS", "POSITIVE_MENISCUS_LENS",
               "NEGATIVE_MENISCUS_LENS", "BEST_FORM_LENS", "ACHROMATIC_DOUBLET", "ASPHERIC_LENS", "CYLINDRICAL_LENS", "AXICON")
#: The kernel optic category and whether light passes through, of each public form the kernel holds.
FORM_CATEGORY: dict[str, tuple[str, bool]] = {
    **{form: ("LENS", True) for form in _LENS_FORMS},
    "FLAT_MIRROR": ("MIRROR", False), "CONCAVE_MIRROR": ("MIRROR", False), "OFF_AXIS_PARABOLIC_MIRROR": ("MIRROR", False),
    "PLATE_BEAMSPLITTER": ("BEAMSPLITTER_PLATE", True), "BEAM_SAMPLER": ("BEAMSPLITTER_PLATE", True),
    "POLARIZING_PLATE_BEAMSPLITTER": ("BEAMSPLITTER_PLATE", True), "DICHROIC_MIRROR": ("BEAMSPLITTER_PLATE", True),
    "CUBE_BEAMSPLITTER": ("BEAMSPLITTER_CUBE", True), "POLARIZING_CUBE_BEAMSPLITTER": ("BEAMSPLITTER_CUBE", True),
    "BANDPASS_FILTER": ("FILTER", True), "LONGPASS_FILTER": ("FILTER", True), "SHORTPASS_FILTER": ("FILTER", True),
    "NOTCH_FILTER": ("FILTER", True), "NEUTRAL_DENSITY_FILTER": ("FILTER", True),
    "PLANE_WINDOW": ("WINDOW", True),
    "HALF_WAVE_PLATE": ("WAVEPLATE", True), "QUARTER_WAVE_PLATE": ("WAVEPLATE", True),
    "LINEAR_POLARIZER": ("POLARIZER", True), "GLAN_TAYLOR_PRISM": ("POLARIZER", True), "GLAN_LASER_PRISM": ("POLARIZER", True),
    "GLAN_THOMPSON_PRISM": ("POLARIZER", True),
}
#: Mirror forms with a curved surface (optical power).
CURVED_MIRROR_FORMS = frozenset({"CONCAVE_MIRROR", "OFF_AXIS_PARABOLIC_MIRROR"})


@dataclass(frozen=True)
class MissingMechanics:
    """Why the kernel cannot hold a product: ``reason`` ``NO_KERNEL_CATEGORY`` (its form) or ``GEOMETRY_NOT_HELD`` (a
    convex polyhedron), and the missing-data key (``<sku>:mechanics`` / ``<sku>:design_geometry``)."""

    optical_sku: str
    reason: str
    missing_data: str


def _d(value: Any) -> Decimal:
    return Decimal(str(value))


def _f(value: Any) -> float:
    """A catalog decimal (or a Decimal computed from them) as a float of ``MECHANICS_DECIMALS`` decimals."""

    rounded = round(float(Decimal(str(value))), MECHANICS_DECIMALS)
    return 0.0 if rounded == 0.0 else rounded


def mechanics_of(product: Any) -> OpticMechanicsV1 | MissingMechanics:
    """The kernel's mechanics of one ``PublicOpticalProductV2`` (module docstring), or why there are none."""

    sku = product.optical_sku
    form = product.form.value if hasattr(product.form, "value") else str(product.form)
    known = FORM_CATEGORY.get(form)
    if known is None:
        return MissingMechanics(sku, "NO_KERNEL_CATEGORY", f"{sku}:mechanics")
    category, transmissive = known
    geometry = product.design_geometry
    color = TRANSMISSIVE_COLOR if transmissive else REFLECTOR_COLOR
    if geometry.kind == "CYLINDER":
        diameter, lo_d, hi_d = _d(geometry.diameter_mm), _d(geometry.z_min_mm), _d(geometry.z_max_mm)
        lo, hi, size = _f(lo_d), _f(hi_d), _f(diameter)
        visual = VisualPrimitiveV1(kind="CYLINDER", finish="GLASS", base_mm=(0.0, 0.0, lo), axis="z", length_mm=_f(hi_d - lo_d),
                                   radius_mm=_f(diameter / 2))
        shape = OpticShapeV1.ROUND
    elif geometry.kind == "BOX":
        cx, cy, cz = (_d(v) for v in geometry.center_mm)
        hx, hy, hz = (_d(v) for v in geometry.half_extents_mm)
        lo_d, hi_d = cz - hz, cz + hz
        lo, hi = _f(lo_d), _f(hi_d)
        if category == "BEAMSPLITTER_CUBE" and hx == hy == hz:
            shape, size = OpticShapeV1.CUBE, _f(2 * hx)
        else:
            shape, size = OpticShapeV1.RECTANGULAR, _f(2 * min(hx, hy))
        visual = VisualPrimitiveV1(kind="BOX", finish="GLASS", min_mm=(_f(cx - hx), _f(cy - hy), lo), max_mm=(_f(cx + hx), _f(cy + hy), hi))
    else:
        return MissingMechanics(sku, "GEOMETRY_NOT_HELD", f"{sku}:design_geometry")
    return OpticMechanicsV1(optical_sku=sku, category=category, shape=shape, size_mm=size, thickness_mm=_f(hi_d - lo_d), axial_min_mm=lo,
                            axial_max_mm=hi, transmissive=transmissive, visual=(visual,), color_rgb=color,
                            curved=form in CURVED_MIRROR_FORMS)


def orderable(product: Any) -> bool:
    """OpenSci supplies the product (``commercial_readiness`` ORDERABLE)."""

    return product.commercial_readiness is CommercialReadiness.ORDERABLE


def mounted_by_opensci(product: Any) -> bool:
    """OpenSci supplies mounting hardware for the product: ORDERABLE, ``opensci_mounting`` AVAILABLE (a release without
    the fact mounts every ORDERABLE product) and not delivered in its own housing."""

    return (orderable(product) and product.mounting is OpenSciMounting.AVAILABLE
            and product.delivery_form is not DeliveredForm.HOUSED)


__all__ = ["CURVED_MIRROR_FORMS", "FORM_CATEGORY", "MECHANICS_DECIMALS", "MissingMechanics", "mechanics_of", "mounted_by_opensci",
           "orderable"]
