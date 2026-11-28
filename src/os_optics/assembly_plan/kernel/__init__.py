"""The assembly kernel: one optic's support chain and a whole design's laboratory assembly from the public mechanical
catalog.

``resolve_assembly(request, catalog)`` realizes every OpenSci optic of one design as one support chain (optic, mount,
post, post holder) on one shared breadboard; ``resolve_optic_chain(optic, catalog, beam_height_mm=...)`` gives one optic's
chain at a beam height without a layout. Both are pure and deterministic: the same request and catalog give the same
result on every installation, and a design the kernel cannot realize is refused with a stable code and reason
(``AssemblyResolutionRefusal``), which ``assembly_plan.derive`` turns into plan findings. See ``resolve.py`` for the rules.

The stack helpers a plan edit re-checks hardware with: ``stack_pairs(mount, catalog, pins)`` (the posts and holders that
fit a mount), ``height_intervals`` (the axis heights a mount reaches with each pair), ``height_span`` (their range within
the policy) and ``choose_stack(mount, axis_height, catalog, pins)`` (the post, holder and insertion depth for a height).
"""

from __future__ import annotations

from .assemble import chain_bodies, interface_designations, link_poses, part_datums
from .beam import is_child_key, trace_beam_paths
from .catalog import (
    FACT_BASES,
    KBoard,
    KernelCatalog,
    KHolder,
    KMechanical,
    KMember,
    KMount,
    KPost,
    OpticMechanicsV1,
    ResolverPolicyV1,
    table_board,
    view_of,
)
from .geometry import Pose, YawBox
from .models import (
    RESOLVER_KERNEL_ID,
    RESOLVER_KERNEL_VERSION,
    AssemblyRealizationV2,
    AssemblyResolutionRefusal,
    AssemblyResolutionRequestV2,
    BeamPathV2,
    BoardDecisionV2,
    ChainJointV1,
    ChainPins,
    ChainTraceV2,
    CheckV2,
    DofRequest,
    DofResultV2,
    ExternalRequest,
    ObservationRequest,
    OpticChainV1,
    OpticRequest,
    PortRequest,
    RealizedFixationV2,
    RealizedMateV2,
    RealizedPartV2,
    RealizedSettingV2,
    ReservedEnvelope,
    ReservedSpaceV2,
    ResolutionBudget,
)
from .resolve import _choose_stack as choose_stack
from .resolve import _height_intervals as height_intervals
from .resolve import _height_span as height_span
from .resolve import _stack_pairs as stack_pairs
from .resolve import (
    BOARD_KEY,
    OPTIC_CHAIN_ROTATION,
    Incidence,
    beam_trace_settings,
    curved_mirror_sku,
    diagnose_kept_fallbacks,
    dof_frame,
    mount_incompatibility,
    oblique_clearance,
    oblique_status,
    optic_incidences,
    rank_mounts,
    resolve_assembly,
    resolve_optic_chain,
)

__all__ = [
    "BOARD_KEY", "FACT_BASES", "OPTIC_CHAIN_ROTATION", "RESOLVER_KERNEL_ID", "RESOLVER_KERNEL_VERSION", "AssemblyRealizationV2",
    "AssemblyResolutionRefusal", "AssemblyResolutionRequestV2", "BeamPathV2", "BoardDecisionV2", "ChainJointV1", "ChainPins",
    "ChainTraceV2", "CheckV2", "DofRequest", "DofResultV2", "ExternalRequest", "Incidence", "KBoard", "KHolder", "KMechanical",
    "KMember", "KMount", "KPost", "KernelCatalog", "ObservationRequest", "OpticChainV1", "OpticMechanicsV1", "OpticRequest",
    "PortRequest", "Pose", "RealizedFixationV2", "RealizedMateV2", "RealizedPartV2", "RealizedSettingV2", "ReservedEnvelope",
    "ReservedSpaceV2", "ResolutionBudget", "ResolverPolicyV1", "YawBox", "beam_trace_settings", "chain_bodies", "choose_stack", "curved_mirror_sku",
    "diagnose_kept_fallbacks", "dof_frame", "height_intervals", "height_span", "interface_designations", "is_child_key", "link_poses",
    "mount_incompatibility", "stack_pairs",
    "oblique_clearance", "oblique_status", "optic_incidences", "part_datums", "rank_mounts", "resolve_assembly",
    "resolve_optic_chain", "table_board", "trace_beam_paths", "view_of",
]
