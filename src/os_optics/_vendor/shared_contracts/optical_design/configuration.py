"""`ProjectConfigurationV1`: one named state of a multi-configuration Project (a zoom position, a switched state).

A Project may declare ``configurations`` (``ProjectDocumentV2.configurations``): N named states of one design. Each
configuration states what differs from the Project as written:

* ``component_poses`` -- a component's ``nominal_pose`` in this configuration (a zoom group's position, a turned
  mirror, a source turned to another field angle). An observation plane attached to a component moves with it;
* ``observation_poses`` -- an observation plane's ``pose`` in this configuration (an image plane refocused), in the
  plane's own frame (the design frame, or its component's local frame for an attached plane);
* ``source_models`` -- a source's ``source_model`` in this configuration (another wavelength set, power or beam);
* ``compute_budget`` -- budgets of this configuration: each stated field replaces the Project's, the others stay the
  Project's (``ComputeBudgetV1``);
* ``numerical_settings`` -- validity limits, levels and match windows of this configuration: each stated field (and
  each stated field of its ``design_point``) replaces the Project's, the others stay the Project's
  (``NumericalSettingsV1``).

A target applies to every configuration unless it names some (``TargetV1.configuration_ids``).

``ProjectDocumentV2.configured(configuration_id)`` applies one configuration and returns an ordinary Project without
``configurations``: the components with their configuration poses, the observation planes with theirs, the sources with
their models, the merged budget and settings and the targets that apply, each without ``configuration_ids``. Every runtime derives a
configuration's input from that one function, so a configuration evaluates exactly as that Project does on its own.

Laboratory assemblies (``design_intent`` ``LAB_ASSEMBLY``): every configuration is simulated as declared; an assembly
request (the built system) needs each configuration to move a component only along and about the DOFs it retains once
the system is built (``retained_dofs``; ``ProjectDocumentV2.configuration_fixed_dof_motions`` lists the moves that do
not, ``CONFIGURATION_MOVES_FIXED_DOF``): the change of position, expressed in the component's
nominal local frame, has no component along a fixed translation axis; a component with no retained rotation keeps its
orientation, and one with a single retained rotation axis turns about that axis only (with two or three retained
rotation axes the reachable orientations depend on the order of the stage's rotations and are not checked). An optical
system without laboratory mechanics (``OPTICAL_SYSTEM``) places each configuration freely.

Configuration ids name run directories (``configurations/<id>``): a letter, then letters, digits, ``_`` or ``-`` (at
most 64), distinct ignoring case, and none of the device names ``CON``, ``PRN``, ``AUX``, ``NUL``, ``COM1``-``COM9``,
``LPT1``-``LPT9``.
"""

from __future__ import annotations

import math
from typing import Annotated, Any, ClassVar

from pydantic import Field, StringConstraints, field_validator, model_validator

from ..base import OmitAbsentFields, StrictContractModel
from ..common.geometry import PoseV1
from ..common.identifiers import InstanceKey, OpenSciId
from ..common.ordering import duplicate_keys
from ._ordering import sort_raw
from .compute_budget import ComputeBudgetV1
from .numerical_settings import NumericalSettingsV1
from .source import SourceModelV1
from .system_aperture import SystemApertureV1


ConfigurationId = Annotated[
    str,
    StringConstraints(strict=True, pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$"),
    Field(description="Configuration id: a letter, then letters, digits, '_' or '-' (at most 64); it names the "
                      "configuration's run directory."),
]

#: Names a configuration id may not take, ignoring case: device names that cannot name a directory on every system.
RESERVED_CONFIGURATION_IDS = frozenset(
    {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)})
#: The change of position, in mm, a fixed translation axis of a laboratory component tolerates between the Project's
#: nominal pose and a configuration's (rounding of a pose written in decimals).
FIXED_TRANSLATION_TOLERANCE_MM = 1e-6
#: The component of a configuration's relative rotation quaternion a fixed rotation axis tolerates: the Project's own
#: quaternion tolerance (``component.UNIT_QUATERNION_TOLERANCE``).
FIXED_ROTATION_TOLERANCE = 1e-6


class ComponentPoseOverrideV1(StrictContractModel):
    """A component's ``nominal_pose`` in one configuration."""

    instance_id: InstanceKey
    nominal_pose: PoseV1 = Field(description="The component's pose in this configuration (design frame).")


class ObservationPoseOverrideV1(StrictContractModel):
    """An observation plane's ``pose`` in one configuration, in the plane's own frame."""

    observation_id: OpenSciId
    pose: PoseV1 = Field(description="The plane's pose in this configuration: in the design frame, or in its component's "
                                     "local frame for a plane attached to a component.")


class SourceModelOverrideV1(StrictContractModel):
    """A source's ``source_model`` in one configuration."""

    instance_id: InstanceKey
    source_model: SourceModelV1 = Field(description="The source's emission model in this configuration.")


class ProjectConfigurationV1(OmitAbsentFields, StrictContractModel):
    """One named state of the design (module docstring). Lists are keyed by id and kept sorted by it."""

    # Serialized only when given: a configuration without it keeps its earlier payload and hash.
    _omit_when_none: ClassVar[tuple[str, ...]] = ("numerical_settings", "system_aperture")

    system_aperture: SystemApertureV1 | None = Field(None, description="The complete system aperture of this configuration.")

    configuration_id: ConfigurationId
    label: str | None = Field(None, min_length=1, max_length=200, description="A short name to show for this configuration.")
    component_poses: tuple[ComponentPoseOverrideV1, ...] = Field(
        (), description="Component poses of this configuration; components not listed keep their nominal_pose.")
    observation_poses: tuple[ObservationPoseOverrideV1, ...] = Field(
        (), description="Observation plane poses of this configuration; planes not listed keep their pose.")
    source_models: tuple[SourceModelOverrideV1, ...] = Field(
        (), description="Source models of this configuration; sources not listed keep their source_model.")
    compute_budget: ComputeBudgetV1 | None = Field(
        None, description="Budgets of this configuration: each stated field replaces the Project's compute_budget field; "
                          "absent fields stay the Project's.")
    numerical_settings: NumericalSettingsV1 | None = Field(
        None, description="Validity limits, levels and match windows of this configuration: each stated field replaces the "
                          "Project's numerical_settings field (design_point field by field); absent fields stay the Project's.")

    @field_validator("component_poses", "source_models", mode="before")
    @classmethod
    def _sort_by_instance(cls, value: Any) -> Any:
        return sort_raw(value, lambda get, item: get(item, "instance_id"))

    @field_validator("observation_poses", mode="before")
    @classmethod
    def _sort_by_observation(cls, value: Any) -> Any:
        return sort_raw(value, lambda get, item: get(item, "observation_id"))

    @model_validator(mode="after")
    def _unique(self) -> ProjectConfigurationV1:
        for label, items, key in (("component_poses instance_id", self.component_poses, "instance_id"),
                                  ("observation_poses observation_id", self.observation_poses, "observation_id"),
                                  ("source_models instance_id", self.source_models, "instance_id")):
            duplicates = duplicate_keys(items, key)
            if duplicates:
                raise ValueError(f"configuration {self.configuration_id!r}: duplicate {label}: {duplicates}")
        if self.configuration_id.lower() in RESERVED_CONFIGURATION_IDS:
            raise ValueError(f"configuration id {self.configuration_id!r} is a device name that cannot name a directory; choose another")
        return self


def _normalized(q: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    norm = math.sqrt(sum(c * c for c in q))
    return (q[0] / norm, q[1] / norm, q[2] / norm, q[3] / norm)


def _conjugate_product(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    """``conj(a) * b`` (Hamilton): the rotation from ``a``'s frame to ``b``'s, expressed in ``a``'s frame."""

    w1, x1, y1, z1 = a[0], -a[1], -a[2], -a[3]
    w2, x2, y2, z2 = b
    return (w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2, w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
            w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2, w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2)


def _to_local(q: tuple[float, float, float, float], v: tuple[float, float, float]) -> tuple[float, float, float]:
    """``R(q)^T v``: a design-frame vector in the frame of unit quaternion ``q``."""

    w, x, y, z = q[0], -q[1], -q[2], -q[3]
    vx, vy, vz = v
    cx, cy, cz = y * vz - z * vy + w * vx, z * vx - x * vz + w * vy, x * vy - y * vx + w * vz
    return (vx + 2.0 * (y * cz - z * cy), vy + 2.0 * (z * cx - x * cz), vz + 2.0 * (x * cy - y * cx))


def fixed_dof_motions(nominal: PoseV1, pose: PoseV1, retained_axes: frozenset[str]) -> list[str]:
    """How moving a component from ``nominal`` to ``pose`` uses DOFs it does not retain (module docstring): the
    ``T*`` axes along which its position changes and the rotation it makes outside its retained rotation axes, each as
    words; empty when the move stays within ``retained_axes`` (``"Tx"`` ... ``"Rz"``)."""

    q_nominal = _normalized(nominal.quaternion_wxyz)
    shift = tuple(p - n for p, n in zip(pose.position_mm, nominal.position_mm, strict=True))
    local_shift = _to_local(q_nominal, shift)  # type: ignore[arg-type]
    problems = []
    for axis, amount in zip(("Tx", "Ty", "Tz"), local_shift, strict=True):
        if axis not in retained_axes and abs(amount) > FIXED_TRANSLATION_TOLERANCE_MM:
            problems.append(f"moves {amount:.6g} mm along its fixed {axis}")
    relative = _conjugate_product(q_nominal, _normalized(pose.quaternion_wxyz))
    if relative[0] < 0.0:
        relative = (-relative[0], -relative[1], -relative[2], -relative[3])
    rotations = [axis for axis in ("Rx", "Ry", "Rz") if axis in retained_axes]
    if len(rotations) <= 1:
        for axis, part in zip(("Rx", "Ry", "Rz"), relative[1:], strict=True):
            if axis not in rotations and abs(part) > FIXED_ROTATION_TOLERANCE:
                angle = math.degrees(2.0 * math.asin(min(1.0, abs(part))))
                problems.append(f"turns {angle:.6g} deg about its fixed {axis}")
    return problems


__all__ = [
    "FIXED_ROTATION_TOLERANCE",
    "FIXED_TRANSLATION_TOLERANCE_MM",
    "RESERVED_CONFIGURATION_IDS",
    "ComponentPoseOverrideV1",
    "ConfigurationId",
    "ObservationPoseOverrideV1",
    "ProjectConfigurationV1",
    "SourceModelOverrideV1",
    "fixed_dof_motions",
]
