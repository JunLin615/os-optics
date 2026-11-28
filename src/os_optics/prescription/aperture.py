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
            expected.append((f'{component}/{surface}:{event}', label, role == 'aperture'))
    labels = {event.split(':')[0]: label for event, label, _ in expected}
    for ray in trace['rays']:
        failures = []
        for path in ray['paths']:
            failure = _path_conflict(path, expected, labels)
            if failure is None:
                break
            failures.append(failure)
        else:
            # If a coating splits the ray, report the path that reached the most prescribed interfaces.
            _, surface, reason = max(failures, default=(0, next(iter(labels)), 'cannot reach'))
            raise refuse(E.UNSUPPORTED_FEATURE,
                         f'The primary-wavelength axial marginal ray at entrance pupil radius '
                         f'{trace["entrance_pupil_radius_mm"]:g} mm {reason} source surface {labels[surface]} '
                         f'({surface}). Reduce the declared system aperture or verify the physical clear aperture.', where)


def _path_conflict(path, expected, labels):
    lineage = path['lineage']
    observed = 'observation/image:observe' in lineage
    if observed:
        lineage = lineage[:lineage.index('observation/image:observe')]
    interfaces = iter(lineage)
    for i, (event, _, is_stop) in enumerate(expected):
        # A curved lens can extend through a nearby stop's opening. Its front face may precede the stop
        # for an inner ray and follow it for an outer ray. Both rays must traverse the same optical interfaces.
        if event not in (lineage if is_stop else interfaces):
            return i, event.split(':')[0], 'cannot pass'
    seen = set()
    for event in lineage:
        surface, _, action = event.partition(':')
        if action in {'reflect', 'refract'} and surface in labels:
            if surface in seen:
                return len(expected), surface, 'returns to'
            seen.add(surface)
    blocker = path['termination_surface_id']
    if not observed and path['termination'] == 'CLIPPED' and blocker in labels:
        return len(expected), blocker, 'is blocked on its return at'
    return None
