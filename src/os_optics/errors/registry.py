"""The refusal census: every code vocabulary and every exception class of the Engine, and what kind of refusal each is.

The policy the table records:

* **Whole operation** (``WHOLE_OPERATION``). The operation ends with a structured error and no result: malformed input or
  input that violates a contract, an integrity or security check, an edit operation (atomic by design), catalog access
  and import parsing, a request to a command that cannot be read.
* **Per item** (``PER_ITEM``). A valid request that contains a construct, a source, an element or an analysis quantity
  outside the model is answered: an issue at that item, a state on the quantities that depend on it, and every other
  quantity computed. A refusal of this kind that still ends its whole operation is listed in ``PENDING`` until it is
  converted; the list becoming shorter is the measure of progress.
* **Resource limit** (``RESOURCE_LIMIT``). A budget or a monitor ended part of the work: the execution outcome says so
  (``PARTIAL`` / ``STOPPED``), the budget record names what ran out, and what was computed is kept.
* **Control** (``CONTROL``). An internal signal that never reaches a caller as it is.

The names are kept as text (``module:Name``) so that this module imports nothing of the packages it lists.
``tests/test_refusal_registry.py`` holds it true: a code vocabulary or an exception class the tables do not name, a code
string two vocabularies share without a declared alias, and a pending code its vocabulary does not hold each fail it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class RefusalClass(StrEnum):
    """What a refusal does to the operation it occurs in (module docstring)."""

    WHOLE_OPERATION = "WHOLE_OPERATION"
    PER_ITEM = "PER_ITEM"
    RESOURCE_LIMIT = "RESOURCE_LIMIT"
    CONTROL = "CONTROL"


@dataclass(frozen=True)
class Vocabulary:
    """A closed set of machine-readable codes: where it is defined, the family of operations that issues it and the
    classes its codes occur in."""

    name: str
    family: str
    classes: tuple[RefusalClass, ...]


@dataclass(frozen=True)
class Raised:
    """An exception class: the family of operations that raises it, its class, and the vocabulary its codes come from
    (``None``: it carries no code of a vocabulary)."""

    name: str
    family: str
    refusal: RefusalClass
    vocabulary: str | None = None


@dataclass(frozen=True)
class Pending:
    """Refusals of class ``PER_ITEM`` that still end their whole operation: the exception that carries them, the codes
    (of ``Raised.vocabulary``; empty: every refusal of the class that is not a malformed request, where the reasons are
    not a vocabulary yet) and what the per-item form is."""

    exception: str
    codes: tuple[str, ...]
    per_item_form: str


A, B, C, X = RefusalClass.WHOLE_OPERATION, RefusalClass.PER_ITEM, RefusalClass.RESOURCE_LIMIT, RefusalClass.CONTROL

ERROR_CODE = "os_optics.errors.codes:ErrorCode"
LOCAL_RUN = "os_optics.local.errors:LocalRunErrorCode"
PARAXIAL = "os_optics.local.paraxial:ParaxialErrorCode"
CAVITY = "os_optics.local.cavity:CavityErrorCode"
NETWORK = "os_optics.network.errors:NetworkErrorCode"
CATALOG = "os_optics.catalog.errors:CatalogErrorCode"
COMPOSITION = "os_optics.composition.errors:CompositionErrorCode"
NATIVE = "os_optics.native_library.errors:NativeErrorCode"
PRESCRIPTION = "os_optics.prescription.errors:PrescriptionErrorCode"
DRAWING = "os_optics.assembly_drawing.errors:DrawingErrorCode"
LAYOUT = "os_optics.optical_layout.generate:OpticalLayoutErrorCode"
MESH = "os_optics.assembly.mesh:MeshErrorCode"
MECHANICAL = "os_optics.assembly_plan.mechanical.model:MechanicalErrorCode"
PLAN = "os_optics.assembly_plan.contract:AssemblyPlanErrorCode"
PLAN_OP = "os_optics.assembly_plan.contract:PlanOpErrorCode"

#: Every code vocabulary of the Engine (a ``StrEnum`` whose name ends in ``Code``).
VOCABULARIES: tuple[Vocabulary, ...] = (
    # the issue codes of a result: a finding at an item (B), a budget (C); as the code of an EngineInputError, class A
    Vocabulary(ERROR_CODE, "engine result and request", (A, B, C)),
    Vocabulary(LOCAL_RUN, "local run", (A, B)),
    Vocabulary(PARAXIAL, "paraxial analysis", (A, B)),
    Vocabulary(CAVITY, "cavity analysis", (A, B)),
    Vocabulary(NETWORK, "network export", (A, B)),
    Vocabulary(CATALOG, "catalog", (A,)),
    Vocabulary(COMPOSITION, "workspace edit", (A,)),
    Vocabulary("os_optics.composition.errors:CompositionWarningCode", "workspace edit", (B,)),
    Vocabulary(NATIVE, "native component library", (A,)),
    Vocabulary(PRESCRIPTION, "prescription import", (A,)),
    Vocabulary(DRAWING, "assembly drawing", (A,)),
    Vocabulary(LAYOUT, "optical layout drawing", (A,)),
    Vocabulary(MESH, "assembly mesh", (A,)),
    Vocabulary(MECHANICAL, "mechanical catalog", (A,)),
    Vocabulary(PLAN, "assembly plan", (A,)),
    Vocabulary(PLAN_OP, "assembly plan", (A,)),
)

#: Every exception class defined in the Engine.
EXCEPTIONS: tuple[Raised, ...] = (
    # ----- the Engine request and its evaluation
    Raised("os_optics.errors.exceptions:EngineInputError", "engine request", A, ERROR_CODE),
    Raised("os_optics.errors.exceptions:ModelScopeRefusal", "engine request", B, ERROR_CODE),
    Raised("os_optics.evaluation.plan:EvaluationPlanError", "engine request", A),
    Raised("os_optics.optics.materials.index:_MaterialEvalError", "engine evaluation", X),  # becomes a MATERIAL_INVALID issue
    # becomes an INVALID_INPUT issue at the node-count setting (a Project run: PROJECT_INVALID naming it)
    Raised("os_optics.optics.gaussian.truncation:QuadratureRuleTooLarge", "engine evaluation", X),
    Raised("os_optics.validation.return_work:OutOfReturnWork", "engine evaluation", C),
    Raised("os_optics.run_control:RunStopped", "engine evaluation", C),
    # ----- the local run of a Project
    Raised("os_optics.local.errors:LocalRunError", "local run", A, LOCAL_RUN),
    Raised("os_optics.local.project_ops:ProjectOpError", "project edit", A),
    Raised("os_optics.local.project_ops:ProjectUpdateError", "project edit", A),
    Raised("os_optics.local.artifacts:RunDirectoryLinkError", "run directory", A),
    Raised("os_optics.local.artifacts:RunDirectoryOccupiedError", "run directory", A),
    Raised("os_optics.local.freshness:RunNotReadable", "run directory", A),
    Raised("os_optics.local.submission:RunDirectoryInconsistentError", "run directory", A),
    Raised("os_optics.local.submission:ConfigurationsNotSubmittableError", "run directory", A),
    # ----- analyses
    Raised("os_optics.local.paraxial:ParaxialError", "paraxial analysis", A, PARAXIAL),
    Raised("os_optics.local.cavity:CavityError", "cavity analysis", A, CAVITY),
    Raised("os_optics.local.compile:ParaxialSurfaceNotSupported", "paraxial analysis", X),  # becomes a ParaxialError problem
    Raised("os_optics.results.model:ResultsError", "results query", A),
    Raised("os_optics.results.model:RunProjectRefused", "results query", A, LOCAL_RUN),
    Raised("os_optics.results.imaging:ImagingError", "imaging analysis", A),
    Raised("os_optics.results.ghosts:GhostError", "ghost analysis", A),
    Raised("os_optics.results.field:FieldError", "field analysis", A),
    Raised("os_optics.results.field:_OutOfModel", "field analysis", X),  # becomes a quantity out of model scope
    Raised("os_optics.results.focal_field:FocalFieldError", "focal-field analysis", A),
    Raised("os_optics.results.pupil_field:PupilFieldError", "pupil-field analysis", A),
    Raised("os_optics.results.pupil_field:_Unavailable", "pupil-field analysis", X),  # becomes an unavailable quantity
    Raised("os_optics.results.detector:DetectorError", "detector response", A),
    Raised("os_optics.network.errors:NetworkExportError", "network export", A, NETWORK),
    # ----- catalog, import, edit, native library
    Raised("os_optics.catalog.errors:CatalogError", "catalog", A, CATALOG),
    Raised("os_optics.catalog_materials:MaterialQueryError", "catalog material discovery", A),
    Raised("os_optics.prescription.errors:PrescriptionImportError", "prescription import", A, PRESCRIPTION),
    Raised("os_optics.composition.errors:CompositionError", "workspace edit", A, COMPOSITION),
    Raised("os_optics.composition.operations:_Unchanged", "workspace edit", X),
    Raised("os_optics.native_library.errors:NativeComponentError", "native component library", A, NATIVE),
    Raised("os_optics.interop:InteropEntryUnknown", "interchange", A),
    # ----- drawings, views, assembly
    Raised("os_optics.assembly_drawing.errors:AssemblyDrawingError", "assembly drawing", A, DRAWING),
    Raised("os_optics.assembly_drawing.sheet:LayoutError", "assembly drawing", A),
    Raised("os_optics.optical_layout.generate:OpticalLayoutError", "optical layout drawing", A, LAYOUT),
    Raised("os_optics.assembly.mesh:MeshError", "assembly mesh", A, MESH),
    Raised("os_optics.assembly_plan.mechanical.model:MechanicalCatalogError", "mechanical catalog", A, MECHANICAL),
    Raised("os_optics.assembly_plan.contract:AssemblyPlanError", "assembly plan", A, PLAN),
    Raised("os_optics.optical_view.builder:OpticalViewBindingError", "optical view", A),
    Raised("os_optics.optical_view.glb:GlbFormatError", "optical view", A),
    Raised("os_optics.optical_view.section:SectionError", "optical view", A),
    Raised("os_optics.viewer.assembly:AssemblyViewError", "viewer", A),
    Raised("os_optics.viewer.optical:ViewerInputError", "viewer", A),
    # ----- the service client and protected content (integrity and security)
    Raised("os_optics.client.errors:OpenSciApiError", "service client", A),
    Raised("os_optics.client.errors:OpenSciConfigurationError", "service client", A),
    Raised("os_optics.client.errors:OpenSciConnectionError", "service client", A),
    Raised("os_optics.client.errors:AssemblyDetailNotReleasedError", "service client", A),
    Raised("os_optics.client.errors:ProtectedContentError", "service client", A),
    Raised("os_optics.client.errors:LocalInputError", "service client", A),
    Raised("os_optics.client.errors:ArtifactIntegrityError", "service client", A),
    Raised("os_optics.protected_delivery:ProtectedDeliveryDocumentError", "protected delivery", A),
    Raised("os_optics.protected_delivery:CryptographyUnavailableError", "protected delivery", A),
    # ----- the command line
    Raised("os_optics.cli_exit:StatusContractError", "command line", A),
    Raised("os_optics.cli_view:_Usage", "command line", A),
    Raised("os_optics.cli_workspace:_Usage", "command line", A),
)

#: Code strings that two or more vocabularies hold on purpose: the same fact reported by another family (a local run
#: restates the Engine's refusal of its lowered request; two drawing generators share their sheet checks). The strings are
#: in stored artifacts and published documents, so neither side is renamed.
ALIASES: dict[str, tuple[str, ...]] = {
    "BIREFRINGENT_MEDIUM_UNSUPPORTED": (ERROR_CODE, LOCAL_RUN),
    "GRADIENT_INDEX_UNSUPPORTED": (ERROR_CODE, LOCAL_RUN),
    "MEDIUM_BOUNDARY_INCONSISTENT": (ERROR_CODE, LOCAL_RUN),
    "MEMBER_OUTSIDE_ENVELOPE": (ERROR_CODE, LOCAL_RUN),
    "OPTICAL_BODIES_OVERLAP": (ERROR_CODE, LOCAL_RUN),
    "OPTICAL_BODIES_TOUCH": (ERROR_CODE, LOCAL_RUN),
    "SAG_DOMAIN_INSIDE_OUTLINE": (ERROR_CODE, LOCAL_RUN),
    "SURFACES_INTERSECT": (ERROR_CODE, LOCAL_RUN, PRESCRIPTION),
    "MATERIAL_INVALID": (ERROR_CODE, PRESCRIPTION),
    "NUMERICAL_FAILURE": (ERROR_CODE, NETWORK),
    "OUT_OF_MODEL_SCOPE": (ERROR_CODE, NETWORK),
    "CATALOG_INVALID": (CATALOG, LOCAL_RUN),
    "CATALOG_NOT_AVAILABLE": (CATALOG, LOCAL_RUN),
    "ENGINE_UPGRADE_REQUIRED": (CATALOG, LOCAL_RUN),
    "ENGINE_INPUT_INVALID": (LOCAL_RUN, NETWORK),
    "PROJECT_INVALID": (LOCAL_RUN, PRESCRIPTION),
    "OPTIONS_INVALID": (NETWORK, PRESCRIPTION),
    "INPUT_INVALID": (DRAWING, LAYOUT),
    "LAYOUT_DOES_NOT_FIT": (DRAWING, LAYOUT),
    "BASE_CHANGED": (COMPOSITION, PLAN),
    "OP_ID_REUSED": (COMPOSITION, PLAN),
    "MECHANICAL_CATALOG_INVALID": (MECHANICAL, PLAN),
    "MECHANICAL_CATALOG_NOT_AVAILABLE": (MECHANICAL, PLAN),
    "MECHANICAL_IDENTITY_DUPLICATE": (MECHANICAL, PLAN),
    "MECHANICAL_SCHEMA_UNSUPPORTED": (MECHANICAL, PLAN),
}

#: The codes of ``ErrorCode`` an ``EngineInputError`` is raised with for an input that violates the request contract
#: (class ``WHOLE_OPERATION``). Any other code raised that way is in ``PENDING``.
INPUT_CONTRACT_CODES: tuple[str, ...] = (
    "INVALID_INPUT", "ZERO_LENGTH_VECTOR", "NON_UNIT_VECTOR", "NON_UNIT_QUATERNION", "DEGENERATE_SURFACE", "DUPLICATE_ID",
    "UNKNOWN_REFERENCE", "UNKNOWN_MATERIAL",
)

#: Refusals of class ``PER_ITEM`` that still end their whole operation.
PENDING: tuple[Pending, ...] = (
    Pending("os_optics.errors.exceptions:ModelScopeRefusal", ("FACET_SURFACE_NOT_TRACED",),
            "the paths that reach the facet surface end there out of model scope; the rest of the scene is traced"),
    Pending("os_optics.errors.exceptions:EngineInputError", ("BIREFRINGENT_MEDIUM_UNSUPPORTED", "GRADIENT_INDEX_UNSUPPORTED"),
            "the observation plane in that medium is out of model scope; the other planes are evaluated"),
    Pending("os_optics.local.errors:LocalRunError",
            ("SIMULATION_MODEL_UNSUPPORTED", "CUSTOM_PART_UNSUPPORTED", "SELECTED_PARAMETERS_UNSUPPORTED", "SOURCE_PRODUCT_UNSUPPORTED",
             "BIREFRINGENT_MEDIUM_UNSUPPORTED", "GRADIENT_INDEX_UNSUPPORTED", "SAMPLE_EXCITATION_NOT_KNOWN"),
            "the component, source or plane is named in an issue and the quantities that depend on it are out of model scope; "
            "the rest of the Project runs"),
    Pending("os_optics.local.paraxial:ParaxialError",
            ("PARAXIAL_ELEMENT_UNSUPPORTED", "PARAXIAL_NOT_COAXIAL", "PARAXIAL_CYLINDER_ROTATED", "PARAXIAL_COMPOUND_MEMBER_NOT_COAXIAL",
             "PARAXIAL_REFLECTOR_BACK_FACE", "PARAXIAL_CURVED_REFLECTOR_TILTED", "PARAXIAL_REFLECTOR_BEHIND_SURFACES",
             "PARAXIAL_BIREFRINGENT_ELEMENT", "PARAXIAL_SURFACE_NOT_SUPPORTED", "PARAXIAL_NO_SOLUTION"),
            "the analysis document is returned with the quantities that depend on the element or the solve stated as out of "
            "model scope or without a solution"),
    Pending("os_optics.local.cavity:CavityError",
            ("CAVITY_ELEMENT_UNSUPPORTED", "CAVITY_NOT_CLOSED", "CAVITY_SOURCE_UNSUPPORTED", "CAVITY_TOO_SHORT"),
            "the analysis document is returned with the quantities the element, the source or the length affects out of model scope"),
    Pending("os_optics.network.errors:NetworkExportError",
            ("CAVITY_NOT_SUPPORTED", "PARTIAL_MODE_OVERLAP", "PHASE_UNDEFINED_IDEAL_LENS", "PHASE_UNDEFINED_ATTENUATOR",
             "PHASE_UNDEFINED_FARADAY_ROTATOR", "PHASE_UNDEFINED_GRATING", "PHASE_UNDEFINED_SAMPLE", "MODE_UNDEFINED", "MODE_TRUNCATED",
             "POLARIZING_NORMAL_INCIDENCE", "NETWORK_NOT_PASSIVE", "OUT_OF_MODEL_SCOPE"),
            "the network is exported with the connections the element affects stated as not modelled"),
    Pending("os_optics.results.imaging:ImagingError", (), "the imaging document with the quantity out of model scope"),
    Pending("os_optics.results.ghosts:GhostError", (), "the ghost document with the quantity out of model scope"),
    Pending("os_optics.results.field:FieldError", (), "the field document with the quantity out of model scope"),
    Pending("os_optics.results.focal_field:FocalFieldError", (), "the focal-field document with the quantity out of model scope"),
    Pending("os_optics.results.pupil_field:PupilFieldError", (), "the pupil-field quantities stated as unavailable"),
    Pending("os_optics.results.detector:DetectorError", (), "the detector quantities stated as unavailable"),
)

__all__ = ["ALIASES", "EXCEPTIONS", "INPUT_CONTRACT_CODES", "PENDING", "Pending", "Raised", "RefusalClass", "VOCABULARIES", "Vocabulary"]
