"""Check a numerical system aperture against the original sequential surfaces on axis."""

from .._vendor.shared_contracts.optical_design import ProjectDocumentV2
from ..local.system_aperture import trace_system_pupil
from . import PrescriptionImport
from .errors import PrescriptionErrorCode as E, refuse


# Use a point just inside the rim so coincident calculated and machined boundaries can pass.
_RIM_FRACTION = 1.0 - 1e-7


def validate_aperture(project: ProjectDocumentV2, imported: PrescriptionImport) -> None:
    definition = project.system_aperture
    if definition is None or definition.kind == 'FLOAT_BY_STOP':
        return
    rx = imported.prescription
    where = rx.aperture_location or next((s.location for s in rx.surfaces if s.is_stop), rx.surfaces[0].location)
    # An explicitly off-axis-only design still defines its pupil on the optical axis. A separate axial launch also
    # keeps a field's annular Gaussian alignment point from becoming the center of a system aperture.
    source, *rest = project.components
    pose = source.nominal_pose.model_copy(update={'position_mm': (0.0, 0.0, source.nominal_pose.position_mm[2]),
                                                 'quaternion_wxyz': (1.0, 0.0, 0.0, 0.0)})
    axial = project.model_copy(update={'components': (source.model_copy(update={'nominal_pose': pose}), *rest)})
    try:
        trace = trace_system_pupil(axial, source_id=source.instance_id, wavelength_nm=definition.primary_wavelength_nm,
                                   pupil_coordinates=[(_RIM_FRACTION, 0.0), (0.0, _RIM_FRACTION)])
    except ValueError as exc:
        raise refuse(E.UNSUPPORTED_FEATURE, f'Cannot sample the declared system aperture. {exc}', where) from None

    # A ray may miss a glass element altogether and still cross the image plane. Follow the ordered optical
    # interactions to find the first missed, blocked or internally reflected surface, including in folded systems.
    expected = []
    roles = {c.instance_id: c.optical_role for c in project.components}
    for member in imported.report['members']:
        component = member['member_id']
        role = roles[component]
        event = {'aperture': 'aperture_stop', 'mirror': 'reflect', 'lens': 'refract'}[role]
        for i, label in enumerate(member['surfaces'], 1):
            surface = 'stop' if role == 'aperture' else f'S{i}'
            expected.append((f'{component}/{surface}:{event}', label))
    for ray in trace['rays']:
        prefix = 0
        for path in ray['paths']:
            hits = iter(path['lineage'])
            count = 0
            for interaction, _ in expected:
                if interaction not in hits:
                    break
                count += 1
            prefix = max(prefix, count)
        if prefix < len(expected):
            interaction, label = expected[prefix]
            raise refuse(E.UNSUPPORTED_FEATURE,
                         f'The primary-wavelength axial marginal ray at entrance pupil radius '
                         f'{trace["entrance_pupil_radius_mm"]:g} mm cannot pass source surface {label} '
                         f'({interaction.split(":")[0]}). Reduce the declared system aperture or verify the physical clear aperture.', where)
