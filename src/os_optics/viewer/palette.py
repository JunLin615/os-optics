"""Display classes and colours of the local viewer: one palette for the browser view and the PNG snapshots.

Presentation only. Colours are sRGB triples in [0, 1]; ``alpha`` < 1 marks a see-through class (optics, planes). Every
class has a distinct hue, so neighbouring parts (an optic in its mount on a post in a holder on a breadboard) read as
separate objects instead of grey on grey.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DisplayClass:
    key: str
    rgb: tuple[float, float, float]
    alpha: float
    en: str
    zh: str


GLASS_ALPHA = 0.5
PLANE_ALPHA = 0.22
TARGET_PLANE_ALPHA = 0.5

# Experimental Lab Assembly: part classes (PreviewPartV1.category -> class below), plus the customer's own hardware.
ASSEMBLY_CLASSES = (
    DisplayClass("OPTIC", (0.42, 0.74, 0.93), GLASS_ALPHA, "Optics", "光学元件"),
    DisplayClass("MOUNT", (0.93, 0.56, 0.2), 1.0, "Optic mounts", "镜架"),
    DisplayClass("STAGE", (0.79, 0.3, 0.24), 1.0, "Adjustment stages", "调整台"),
    DisplayClass("ADAPTER", (0.86, 0.72, 0.28), 1.0, "Adapters", "转接件"),
    DisplayClass("POST", (0.72, 0.76, 0.8), 1.0, "Posts", "支杆"),
    DisplayClass("POST_HOLDER", (0.38, 0.47, 0.62), 1.0, "Post holders", "支杆座"),
    DisplayClass("BASE", (0.2, 0.56, 0.48), 1.0, "Bases", "底座"),
    DisplayClass("OTHER_SUPPORT", (0.62, 0.52, 0.76), 1.0, "Other supports", "其他支撑件"),
    DisplayClass("BREADBOARD", (0.27, 0.3, 0.34), 1.0, "Breadboard", "面包板"),
    DisplayClass("CUSTOMER_SUPPLIED", (0.6, 0.36, 0.84), 1.0, "Your own hardware", "客户自备设备"),
    DisplayClass("OBSERVATION", (0.05, 0.55, 0.62), 1.0, "Observation planes", "观测面"),
)
ASSEMBLY_CLASS_BY_CATEGORY = {
    "OPTICAL_ELEMENT": "OPTIC",
    "OPTIC_MOUNT": "MOUNT",
    "ADJUSTMENT_STAGE": "STAGE",
    "ADAPTER": "ADAPTER",
    "POST": "POST",
    "POST_HOLDER": "POST_HOLDER",
    "BASE": "BASE",
    "OTHER_SUPPORT": "OTHER_SUPPORT",
    "BREADBOARD": "BREADBOARD",
}

# Optical Simulation View: element classes (``optical_view.glb.element_part_class``) and planes. The browser and the
# snapshots draw each element with the Engine GLB's own surface materials; these colours are the legend swatches.
OPTICAL_CLASSES = (
    DisplayClass("SOURCE", (0.9, 0.36, 0.1), 1.0, "Light sources", "光源"),
    DisplayClass("LENS", (0.62, 0.8, 0.95), GLASS_ALPHA, "Lenses", "透镜"),
    DisplayClass("MIRROR", (0.74, 0.79, 0.88), 1.0, "Mirrors", "反射镜"),
    DisplayClass("BEAMSPLITTER", (0.96, 0.78, 0.35), GLASS_ALPHA, "Beamsplitters", "分束镜"),
    DisplayClass("FILTER", (0.45, 0.85, 0.55), GLASS_ALPHA, "Filters", "滤光片"),
    DisplayClass("WINDOW", (0.7, 0.82, 0.92), GLASS_ALPHA, "Windows", "窗口片"),
    DisplayClass("WAVEPLATE", (0.78, 0.68, 0.95), GLASS_ALPHA, "Waveplates", "波片"),
    DisplayClass("POLARIZER", (0.42, 0.42, 0.48), GLASS_ALPHA, "Polarizers", "偏振片"),
    # drawn with the GLB's stop_plate material (an aperture stop's opaque plate and central obstruction)
    DisplayClass("APERTURE", (0.22, 0.22, 0.25), 1.0, "Apertures and stops", "光阑"),
    # a compound unit is drawn by its members (lenses, stops, ...): its legend swatches are the members' drawn colours
    DisplayClass("OPTICAL_UNIT", (0.62, 0.8, 0.95), GLASS_ALPHA, "Compound optical units", "复合光学单元"),
    DisplayClass("DETECTOR", (0.6, 0.6, 0.66), 1.0, "Detectors", "探测器"),
    DisplayClass("OTHER_OPTICAL", (0.6, 0.6, 0.66), 1.0, "Other optical elements", "其他光学元件"),
    DisplayClass("OBSERVATION_PLANE", (0.75, 0.78, 0.82), PLANE_ALPHA, "Observation planes", "观测面"),
    DisplayClass("TARGET_PLANE", (0.98, 0.72, 0.25), TARGET_PLANE_ALPHA, "Target planes", "目标面"),
)

# Beams: PRIMARY stretches thick and opaque, SECONDARY stretches (weak branches) thin, dashed and faint; the assembly's
# beam reference is the design's nominal axis. Widths are screen pixels (snapshots) / millimetres (browser tubes).
BEAM_STYLES = {
    "PRIMARY": {"width_px": 3.0, "alpha": 1.0, "dash": False, "tube_radius_mm": 0.45, "en": "Main beam", "zh": "主光束"},
    "SECONDARY": {"width_px": 1.5, "alpha": 0.55, "dash": True, "tube_radius_mm": 0.15,
                  "en": "Weak branch (leak / ghost)", "zh": "弱分支（泄漏/鬼像）"},
    "PUPIL": {"width_px": 1.0, "alpha": 0.55, "dash": False, "tube_radius_mm": 0.1, "en": "Pupil rays", "zh": "光瞳光线"},
    "SECONDARY_PUPIL": {"width_px": 1.0, "alpha": 0.15, "dash": False, "tube_radius_mm": 0.06, "en": "Pupil rays of a weak branch",
                        "zh": "弱分支的光瞳光线"},
    "DESIGN_AXIS": {"width_px": 3.0, "alpha": 1.0, "dash": False, "tube_radius_mm": 0.9, "en": "Beam (design axis)", "zh": "光束（设计光轴）"},
    "REFERENCE": {"width_px": 1.0, "alpha": 0.6, "dash": True, "tube_radius_mm": 0.06, "rgb": (0.45, 0.47, 0.52),
                  "en": "Beam axis behind a central obstruction (no light)", "zh": "中心遮挡后的光束轴（无光）"},
}

# An assembly coloured by its checks (a local assembly plan, or a package with its layout check): a part whose worst
# check status is not PASS takes the status colour instead of its class colour; a part of the finding being looked at
# (``--finding``) is highlighted.
CHECK_STATUS_RGB = {
    "WARNING": (0.96, 0.66, 0.12),  # amber
    "FAIL": (0.86, 0.16, 0.16),  # red (a FAIL check or an ERROR finding)
    "UNKNOWN": (0.55, 0.56, 0.6),  # grey (UNKNOWN / OUT_OF_MODEL_SCOPE check, UNKNOWN finding: not evaluated)
}
HIGHLIGHT_RGB = (0.98, 0.2, 0.62)

BACKGROUND_RGB = (0.965, 0.972, 0.98)
EDGE_DARKEN = 0.38  # outline colour = class colour x this
GLASS_EDGE_RGB = (0.16, 0.36, 0.56)
TEXT_RGB = (0.1, 0.12, 0.15)
HALO_RGB = (1.0, 1.0, 1.0)
AXIS_RGB = {"X": (0.85, 0.15, 0.15), "Y": (0.1, 0.6, 0.2), "Z": (0.15, 0.35, 0.9)}


#: Surface materials of the Engine GLB that show what an element is (the legend swatches of a compound unit name them).
IDENTITY_MATERIALS = ("optic_glass", "mirror_face", "splitter_face", "filter_face", "polarization_plane", "thin_lens", "stop_plate",
                      "attenuator_plane", "faraday_plane", "grating_face")


def class_table(view_kind: str) -> dict[str, DisplayClass]:
    classes = ASSEMBLY_CLASSES if view_kind == "EXPERIMENTAL_LAB_ASSEMBLY" else OPTICAL_CLASSES
    return {c.key: c for c in classes}
