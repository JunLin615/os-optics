"""`ProjectDocumentV2`: the pure-optical Project (``schema = "opensci.project"``, ``project_schema_version = "2.0"``).

A Project expresses::

    Optical Component + Nominal Pose + Retained DOF Intent

together with the observation planes and targets it is evaluated against. An observation plane attached to a component
(``device_instance_id``) is placed in that component's local frame (``observation_design_pose``).

``design_intent`` states what the design is for (``optical_model.DesignIntentV1``): a laboratory assembly (the
meaning when absent), whose mechanical implementation (mounts, posts, stages, breadboard) OpenSci configures for a
complete-system purchase and which is never part of the Project, or an optical system designed, simulated, inspected and
exported without laboratory mechanics.

``optical_models`` (with ``model_materials`` and ``model_coatings``) is the Project's library of declared optical models
(``optical_model``): optics defined by their physics rather than by a catalog product, used by ``custom_part``
components through ``optical_model_id``.

``configurations`` (``configuration``) declares named states of the design -- the positions of a zoom, the states of a
switched system --, each a set of component poses, observation plane poses, source models, budgets and settings that differ from
the Project as written; ``configured(configuration_id)`` is the ordinary Project of one configuration. A Project with
configurations is evaluated one configuration at a time.

``content_hash()`` is osc1 over ``payload()`` -- no self-reference.

Hash evolution rule: ``payload()`` OMITS every ``None``-valued field
(``exclude_none=True``, recursively). A later *optional* field added to any v2 model MUST therefore
default to ``None`` -- then every document that predates it keeps its exact bytes and osc1 hash, and the
change is a minor version. Adding a field with a non-``None`` default (e.g. an empty tuple) or changing
an existing default changes existing hashes and requires a new major version. The same rule applies to
``LocalValidationRecordV1.payload()``.
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from ..base import OmitAbsentFields, StrictContractModel
from ..common.geometry import PoseV1
from ..common.hashes import HashProfile, HashRef, osc1_digest
from ..design_catalog.optical_catalog import PublicCoatingV1
from .design_content import ObservationV1, ProjectMaterialV1, TargetV1
from .component import OpticalComponentInstanceV1, OpticalRoleV1, require_unit_quaternion
from .configuration import ProjectConfigurationV1, fixed_dof_motions
from .system_aperture import SystemApertureV1
from .compute_budget import ComputeBudgetV1
from .numerical_settings import NumericalSettingsV1, merge_numerical_settings
from .optical_model import (
    DEFAULT_ENVIRONMENT,
    DesignIntentV1,
    ProjectModelMaterialV1,
    ProjectOpticalModelV1,
    PropagationEnvironmentV1,
    check_optical_models,
)


PROJECT_SCHEMA_ID: Literal["opensci.project"] = "opensci.project"
PROJECT_SCHEMA_VERSION_V2: Literal["2.0"] = "2.0"
PROJECT_HASH_SUBJECT = "project_document"


class ProjectDocumentV2(OmitAbsentFields, StrictContractModel):
    """One immutable pure-optical Project revision: optical components with nominal poses and retained DOF intent,
    observation planes and targets."""

    # Omit unstated fields even in dumps without exclude_none to preserve existing documents.
    _omit_when_none: ClassVar[tuple[str, ...]] = ("environment", "numerical_settings", "system_aperture")

    system_aperture: SystemApertureV1 | None = None

    schema_id: Literal["opensci.project"] = Field(alias="schema")
    project_schema_version: Literal["2.0"]
    components: tuple[OpticalComponentInstanceV1, ...] = Field(
        (), description="Placed optical components; order is the author's and is never re-sorted."
    )
    observations: tuple[ObservationV1, ...] = ()
    targets: tuple[TargetV1, ...] = ()
    materials: tuple[ProjectMaterialV1, ...] = ()
    design_intent: DesignIntentV1 | None = Field(
        None, description="LAB_ASSEMBLY (the meaning when absent) or OPTICAL_SYSTEM (no laboratory mechanics)."
    )
    optical_models: tuple[ProjectOpticalModelV1, ...] | None = Field(
        None, min_length=1, description="Declared optical models, used by custom_part components through optical_model_id."
    )
    model_materials: tuple[ProjectModelMaterialV1, ...] | None = Field(
        None, min_length=1, description="Refractive materials of the declared optical models (their only material source)."
    )
    model_coatings: tuple[PublicCoatingV1, ...] | None = Field(
        None, min_length=1, description="Coatings of the declared optical models."
    )
    compute_budget: ComputeBudgetV1 | None = Field(
        None,
        description="The local computation budgets this Project runs with (max_interactions_per_path, max_paths, "
        "max_trace_steps, max_interference_pairs, max_return_analysis_work) and its pupil sampling (pupil_ring_count, "
        "pupil_points_per_ring). These are resource controls, not physical limits: the budgets decide how "
        "much of the light the trace may follow, the pupil sampling how finely the rays resolve a beam, never what the "
        "model is. Absent: the Engine's defaults. The budget "
        "is part of the Engine input, so a run made with one setting is not current for a Project that states "
        "another.",
    )
    environment: PropagationEnvironmentV1 | None = Field(
        None,
        description="Air at a stated temperature and pressure, with zero pressure representing vacuum. The default is air "
        "at 20 C and 101325 Pa, the glass catalogues' reference air. Wavelengths are vacuum wavelengths in every "
        "environment. A gap in a declared model may specify VACUUM.",
    )
    numerical_settings: NumericalSettingsV1 | None = Field(
        None,
        description="The validity limits, levels and match windows this Project's results are computed under: the power "
        "floor and the share of a line's power below which light is left out, the validity limits of the Gaussian and "
        "second-harmonic models, the tolerances of published design points, the match windows of power ratings. Each "
        "stated field replaces the Engine's default; a number computed under a setting other than the default names it in "
        "its value basis, and the run records every value in force. Absent: the Engine's defaults. The settings are part of "
        "the Engine input, so a run made with one setting is not current for a Project that states another.",
    )
    configurations: tuple[ProjectConfigurationV1, ...] | None = Field(
        None, min_length=1,
        description="Named states of the design (zoom positions, switched states) with component poses, observation plane poses, "
        "source models, budgets and numerical settings that differ from the Project as written. Each configuration "
        "is evaluated as its own run; a target applies to the configurations it names (configuration_ids), else to "
        "every one. Order is the author's.",
    )

    @field_validator("targets", mode="before")
    @classmethod
    def _accept_v1_targets(cls, value: Any) -> Any:
        """A target built with the v1 Project's ``TargetV1`` (the same fields, without the v2 additions) is read by its
        fields."""

        if not isinstance(value, (list, tuple)):
            return value
        return [item.model_dump() if isinstance(item, BaseModel) and not isinstance(item, TargetV1)
                and type(item).__name__ == "TargetV1" else item for item in value]

    @model_validator(mode="after")
    def _check(self) -> ProjectDocumentV2:
        instance_ids = [c.instance_id for c in self.components]
        observation_ids = [o.observation_id for o in self.observations]
        for label, ids in (
            ("component instance_id", instance_ids),
            ("observation_id", observation_ids),
            ("target_id", [t.target_id for t in self.targets]),
            ("material_id", [m.material_id for m in self.materials]),
        ):
            if len(set(ids)) != len(ids):
                raise ValueError(f"duplicate {label} in ProjectDocumentV2")
        models = self.optical_models or ()
        check_optical_models(models, self.model_materials or (), self.model_coatings or ())
        model_ids = {model.model_id for model in models}
        for component in self.components:
            if component.optical_model_id is not None and component.optical_model_id not in model_ids:
                raise ValueError(
                    f"component {component.instance_id!r} uses unknown optical model {component.optical_model_id!r}"
                )
        if (self.model_materials or self.model_coatings) and not models:
            raise ValueError("model_materials and model_coatings belong to declared optical_models")
        known_instances = set(instance_ids)
        known_observations = set(observation_ids)
        for observation in self.observations:
            require_unit_quaternion(observation.pose, f"observation {observation.observation_id!r} pose")
            if observation.device_instance_id is not None and observation.device_instance_id not in known_instances:
                raise ValueError(f"observation {observation.observation_id!r} references unknown component {observation.device_instance_id!r}")
        for target in self.targets:
            if target.device_instance_id is not None and target.device_instance_id not in known_instances:
                raise ValueError(f"target {target.target_id!r} references unknown component {target.device_instance_id!r}")
            if target.observation_id is not None and target.observation_id not in known_observations:
                raise ValueError(f"target {target.target_id!r} references unknown observation {target.observation_id!r}")
        for component in self.components:
            excitation = getattr(component.source_model, "excitation", None)
            if excitation is not None and excitation.observation_id not in known_observations:
                raise ValueError(f"source {component.instance_id!r} is excited at unknown observation {excitation.observation_id!r}")
        sources = {c.instance_id for c in self.components if c.optical_role is OpticalRoleV1.SOURCE}
        for observation in self.observations:
            lock_in = None if observation.detection is None else observation.detection.lock_in
            unknown = [] if lock_in is None else [s for s in lock_in.modulation.source_ids if s not in sources]
            if unknown:
                raise ValueError(f"observation {observation.observation_id!r}: the lock-in's modulation names {unknown}, which are "
                                 "not source components")
        self._check_configurations()
        return self

    def _check_configurations(self) -> None:
        configurations = self.configurations or ()
        ids = [c.configuration_id for c in configurations]
        folded = [i.lower() for i in ids]
        if len(set(folded)) != len(folded):
            raise ValueError("duplicate configuration_id in ProjectDocumentV2 (ids are compared ignoring case: they name directories)")
        for target in self.targets:
            if target.configuration_ids is None:
                continue
            if not configurations:
                raise ValueError(f"target {target.target_id!r} names configurations, but the Project declares none")
            unknown = [i for i in target.configuration_ids if i not in ids]
            if unknown:
                raise ValueError(f"target {target.target_id!r} references unknown configurations {unknown}")
        components = {c.instance_id: c for c in self.components}
        observations = {o.observation_id for o in self.observations}
        for configuration in configurations:
            where = f"configuration {configuration.configuration_id!r}"
            for override in configuration.component_poses:
                component = components.get(override.instance_id)
                if component is None:
                    raise ValueError(f"{where} poses unknown component {override.instance_id!r}")
                require_unit_quaternion(override.nominal_pose, f"{where} component {override.instance_id!r} pose")
            for override in configuration.observation_poses:
                if override.observation_id not in observations:
                    raise ValueError(f"{where} poses unknown observation {override.observation_id!r}")
                require_unit_quaternion(override.pose, f"{where} observation {override.observation_id!r} pose")
            for override in configuration.source_models:
                component = components.get(override.instance_id)
                if component is None:
                    raise ValueError(f"{where} gives a source model to unknown component {override.instance_id!r}")
                if component.optical_role is not OpticalRoleV1.SOURCE:
                    raise ValueError(f"{where}: component {override.instance_id!r} is not a source; only a source has a source_model")

    def configuration_fixed_dof_motions(self) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
        """For a ``LAB_ASSEMBLY`` Project, every ``(configuration_id, instance_id, motions)`` where a configuration moves a
        component along or about a DOF it does not retain (``fixed_dof_motions``: the motions in words); empty for an
        ``OPTICAL_SYSTEM``. Such a Project simulates every configuration; an assembly of it could not reach that
        configuration with the mounts its retained DOFs select (``CONFIGURATION_MOVES_FIXED_DOF``, checked at the assembly
        request)."""

        if self.effective_design_intent is not DesignIntentV1.LAB_ASSEMBLY:
            return ()
        components = {c.instance_id: c for c in self.components}
        found = []
        for configuration in self.configurations or ():
            for override in configuration.component_poses:
                component = components[override.instance_id]
                moves = fixed_dof_motions(component.nominal_pose, override.nominal_pose,
                                          frozenset(dof.axis.value for dof in component.retained_dofs))
                if moves:
                    found.append((configuration.configuration_id, override.instance_id, tuple(moves)))
        return tuple(found)

    def configuration(self, configuration_id: str) -> ProjectConfigurationV1:
        """The declared configuration ``configuration_id`` (``KeyError`` when the Project declares none of that id)."""

        for configuration in self.configurations or ():
            if configuration.configuration_id == configuration_id:
                return configuration
        raise KeyError(configuration_id)

    def configured(self, configuration_id: str) -> ProjectDocumentV2:
        """The ordinary Project of configuration ``configuration_id`` (``configuration`` module docstring): its component
        poses, observation plane poses, source models, merged ``compute_budget`` and merged ``numerical_settings`` applied,
        the targets that apply to it
        (without ``configuration_ids``), and no ``configurations``. ``KeyError`` for an id the Project does not declare."""

        configuration = self.configuration(configuration_id)
        payload = self.payload()
        payload.pop("configurations")
        poses = {o.instance_id: o.nominal_pose.model_dump(mode="json") for o in configuration.component_poses}
        models = {o.instance_id: o.source_model.model_dump(mode="json", by_alias=True, exclude_none=True)
                  for o in configuration.source_models}
        for component in payload["components"]:
            if component["instance_id"] in poses:
                component["nominal_pose"] = poses[component["instance_id"]]
            if component["instance_id"] in models:
                component["source_model"] = models[component["instance_id"]]
        planes = {o.observation_id: o.pose.model_dump(mode="json") for o in configuration.observation_poses}
        for observation in payload.get("observations", []):
            if observation["observation_id"] in planes:
                observation["pose"] = planes[observation["observation_id"]]
        targets = []
        for target in payload.get("targets", []):
            applies = target.pop("configuration_ids", None)
            if applies is None or configuration_id in applies:
                targets.append(target)
        payload["targets"] = targets
        stated = {} if configuration.compute_budget is None else configuration.compute_budget.model_dump(mode="json", exclude_none=True)
        if stated:
            payload["compute_budget"] = {**payload.get("compute_budget", {}), **stated}
        if configuration.system_aperture is not None:
            payload["system_aperture"] = configuration.system_aperture.model_dump(mode="json", exclude_none=True)
        if configuration.numerical_settings is not None:
            settings = merge_numerical_settings(payload.get("numerical_settings"), configuration.numerical_settings)
            if settings is not None:
                payload["numerical_settings"] = settings
        return type(self).model_validate(payload)

    def optical_model(self, model_id: str) -> ProjectOpticalModelV1 | None:
        """The declared optical model ``model_id``, or None."""

        return next((model for model in self.optical_models or () if model.model_id == model_id), None)

    @property
    def effective_environment(self) -> PropagationEnvironmentV1:
        """``environment``, or air at 20 C and 101325 Pa when the Project states none."""

        return self.environment or DEFAULT_ENVIRONMENT

    @property
    def effective_design_intent(self) -> DesignIntentV1:
        """``design_intent``, or ``LAB_ASSEMBLY`` when the Project states none."""

        return self.design_intent or DesignIntentV1.LAB_ASSEMBLY

    def observation_design_pose(self, observation: ObservationV1) -> PoseV1:
        """Where an observation plane is in the design frame: its ``pose`` itself, or, for a plane attached to a
        component (``device_instance_id``), that component's ``nominal_pose`` composed with the plane's ``pose`` (which
        is then in the component's local frame). This is the pose the local runtime evaluates; every consumer of a
        plane's position (assembly resolution, Assembly Preview, drawings) uses it."""

        if observation.device_instance_id is None:
            return observation.pose
        (device,) = (c for c in self.components if c.instance_id == observation.device_instance_id)
        return device.nominal_pose.compose(observation.pose)

    def payload(self) -> dict[str, Any]:
        """Canonical hashed/persisted form. ``None`` fields are omitted (module docstring: hash evolution rule)."""

        return self.model_dump(mode="json", by_alias=True, exclude_none=True)

    def content_hash(self) -> str:
        return osc1_digest(self.payload())

    def content_hash_ref(self) -> HashRef:
        return HashRef(profile=HashProfile.OSC1, digest=self.content_hash(), subject=PROJECT_HASH_SUBJECT)
