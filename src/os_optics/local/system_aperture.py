"""Analyze a Project's declared pupil and trace selected geometric samples through its physical optics."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

import numpy as np

from .._vendor.shared_contracts.optical_design import SystemApertureV1
from ..optics.materials.library import MaterialLibrary
from ..optics.polarization.partial import source_components
from ..optics.rays.emitter_samples import EmitterSamples
from ..optics.rays.sampled import SampleTracer
from ..scene.lower import lower_scene
from ..scene.model import EmitterG
from .compile import environment_air, surrounding_index
from .configurations import configured_project
from .lowering import lower_project
from .paraxial import paraxial_analysis


_DEFAULT_PUPIL = ((0.0, 0.0), (1.0, 0.0), (-1.0, 0.0), (0.0, 1.0), (0.0, -1.0))


def _focal_length(project, wavelength_nm, catalog):
    # The first-order system is measured on its optical axis, including when every light source is an off-axis field.
    optics = tuple(c for c in project.components if c.optical_role != 'source')
    components = [c.instance_id for c in optics if c.optical_role in ('lens', 'mirror')]
    if not components:
        raise ValueError('IMAGE_F_NUMBER requires a system with optical power')
    axial = project.model_copy(update={'components': optics, 'targets': ()})
    result = paraxial_analysis(axial, {'components': components, 'wavelength_nm': wavelength_nm}, catalog=catalog)
    focal = result['system']['efl_mm']
    if focal is None:
        raise ValueError('IMAGE_F_NUMBER requires a finite effective focal length at the primary wavelength')
    return focal


def _geometry(project, source_id, catalog):
    aperture = project.system_aperture
    if project.configurations:
        raise ValueError('select a configuration_id for the system aperture')
    if aperture is None:
        raise ValueError('the Project has no declared system aperture')
    field = next((c for c in project.components if c.instance_id == source_id and c.optical_role == 'source'), None)
    if field is None or getattr(field.source_model, 'model_kind', None) != 'GAUSSIAN_BEAM':
        raise ValueError(f'{source_id!r} must identify a field source with an origin and a beam axis')
    n = surrounding_index(environment_air(project.effective_environment), aperture.primary_wavelength_nm)
    distance = abs(aperture.entrance_pupil_z_mm - float(field.nominal_pose.position_mm[2]))
    if distance == 0 and aperture.object_conjugate == 'FINITE':
        raise ValueError('the finite object coincides with the entrance pupil plane')
    focal = None
    if aperture.kind == 'ENTRANCE_PUPIL_DIAMETER':
        radius = aperture.value / 2
    elif aperture.kind == 'OBJECT_NA':
        if aperture.value >= n:
            raise ValueError(f'object NA must be smaller than the object-space refractive index ({n:g})')
        sine = aperture.value / n
        radius = distance * sine / math.sqrt((1 - sine) * (1 + sine))
    elif aperture.kind == 'IMAGE_F_NUMBER':
        focal = _focal_length(project, aperture.primary_wavelength_nm, catalog)
        radius = abs(focal) / (2 * aperture.value)
    else:
        radius = aperture.entrance_pupil_radius_mm
    if not math.isfinite(radius) or radius <= 0:
        raise ValueError('the declared system aperture has no finite positive entrance pupil radius')
    return {**aperture.model_dump(mode='json', exclude_none=True), 'source_id': source_id,
            'entrance_pupil_radius_mm': radius, 'entrance_pupil_diameter_mm': 2 * radius,
            'object_space_index': n, 'object_na': n * (radius / math.hypot(distance, radius))
            if aperture.object_conjugate == 'FINITE' else None,
            'focal_length_mm': focal, 'image_f_number': aperture.value if focal is not None else None}


def system_aperture_analysis(project: Any, *, source_id: str, configuration_id: str | None = None, catalog: Any = None) -> dict:
    """Resolve the centered entrance pupil in design coordinates at the stated primary vacuum wavelength.

    For a finite conjugate, ``object_na`` is the axial numerical aperture at the field's object plane. The pupil
    coordinate frame is shared by all fields. A paraxial image f-number uses the effective focal length of the selected
    state's ordered lenses and mirrors. ``catalog`` and ``project`` accept the same inputs as ``lower_project``.
    """
    document = configured_project(project, configuration_id)
    return {**_geometry(document, source_id, catalog), 'configuration_id': configuration_id}


def _launch_points(source, geometry, coordinates):
    z = geometry['entrance_pupil_z_mm']
    pupil = np.column_stack((coordinates * geometry['entrance_pupil_radius_mm'], np.full(len(coordinates), z)))
    dz = z - source.position[2]
    if geometry['object_conjugate'] == 'FINITE':
        origins = np.broadcast_to(source.position, pupil.shape)
        slopes = (pupil - origins) / dz  # virtual pupils behind the object still launch towards +Z
        return origins, slopes / np.linalg.norm(slopes, axis=1, keepdims=True)
    if source.direction[2] <= 0:
        raise ValueError('a centered infinite field must propagate towards increasing design Z')
    directions = np.broadcast_to(source.direction, pupil.shape)
    return pupil - directions * (dz / source.direction[2]), directions


def _trace_component(scene, library, cfg, src, spectrum, share, origins, directions, rays):
    count = len(origins)
    emitter = EmitterG(source_id=src.source_id, owner_id=src.owner_id, position=src.position, direction=src.direction,
                       e1=src.e1, e2=src.e2, spectrum=spectrum, power_W=src.power_W * share, medium=src.medium,
                       shape='disc', half_x=0, half_y=0, law='COLLIMATED', sample_count=count, seed=0,
                       coherence_lengths_mm=None, polarization_component=src.polarization_component)
    # A sampled geometric ray is passed or clipped whole, including when its axis meets a central obstruction.
    samples = EmitterSamples(origins, directions, np.zeros(count, dtype=bool), np.broadcast_to(src.jones, (count, 2)))
    tracer = SampleTracer(replace(scene, sources=[], emitters=[emitter]), library, cfg)
    trace = tracer.run_samples(pupil_samples=samples)
    parents = {path.path_id: path.parent_path_id for path in tracer.out.paths}
    for path in tracer.out.paths:
        root = path.path_id
        while parents.get(root) is not None:
            root = parents[root]
        sample = int(root.rsplit('.', 1)[1])
        rays[(path.wavelength_nm, sample)]['paths'].append(path.model_dump(mode='json'))
    for (plane, _, spectral_index), rows in trace.rows.items():
        wavelength = spectrum[spectral_index][0]
        for k, sample in enumerate(rows.sample_index):
            rays[(wavelength, sample)]['observations'].append({'observation_id': plane, 'u_mm': rows.u_mm[k],
                'v_mm': rows.v_mm[k], 'direction_u': rows.direction_u[k], 'direction_v': rows.direction_v[k],
                'direction_n': rows.direction_n[k], 'power_W': rows.power_W[k], 'counted': rows.counted[k]})
    return [issue.model_dump(mode='json') for issue in tracer.out.issues]


def trace_system_pupil(project: Any, *, source_id: str, pupil_coordinates: Sequence[Sequence[float]] = _DEFAULT_PUPIL,
                       wavelength_nm: float | None = None, configuration_id: str | None = None, catalog: Any = None) -> dict:
    """Trace geometric rays at normalized (X, Y) entrance-pupil coordinates through the selected native Project.

    The default samples the center and four cardinal rim points. Explicit radii greater than one probe physical clipping
    outside the declared pupil. All emitted lines share the primary aiming geometry. A named wavelength selects one line,
    or the primary reference at unit relative weight if it is absent from the source spectrum.
    """
    document = configured_project(project, configuration_id)
    geometry = _geometry(document, source_id, catalog)
    coords = np.asarray(pupil_coordinates, dtype=float)
    if coords.ndim != 2 or coords.shape[1] != 2 or not len(coords) or not np.isfinite(coords).all():
        raise ValueError('pupil_coordinates must be a nonempty sequence of finite (x, y) normalized coordinates')
    request = lower_project(document, catalog).request
    cfg = request.configuration.numerical
    library = MaterialLibrary(request.project.materials, cfg)
    scene, _ = lower_scene(request.project, cfg, library)
    source = next(s for s in scene.sources if s.source_id == source_id)
    origins, directions = _launch_points(source, geometry, coords)
    spectrum = source.spectrum
    if wavelength_nm is not None:
        spectrum = tuple((w, weight) for w, weight in spectrum if w == wavelength_nm)
        if not spectrum and wavelength_nm == geometry['primary_wavelength_nm']:
            spectrum = ((wavelength_nm, 1.0),)
        if not spectrum:
            raise ValueError('wavelength_nm must select an emitted vacuum wavelength or the primary wavelength')
    rays = {(w, k): {'pupil_coordinate': coord.tolist(), 'inside_system_aperture': math.hypot(*coord) <= 1,
                      'wavelength_nm': w, 'spectral_weight': weight, 'origin_mm': origins[k].tolist(),
                      'direction': directions[k].tolist(), 'paths': [], 'observations': []}
            for k, coord in enumerate(coords) for w, weight in spectrum}
    issues = []
    for component, share in source_components(source):
        issues.extend(_trace_component(scene, library, cfg, component, spectrum, share, origins, directions, rays))
    return {**geometry, 'configuration_id': configuration_id, 'rays': list(rays.values()), 'issues': issues}


__all__ = ['system_aperture_analysis', 'trace_system_pupil', 'SystemApertureV1']
