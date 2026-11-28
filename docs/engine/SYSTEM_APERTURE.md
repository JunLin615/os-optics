# Trace the system aperture of a saved Project

Use `project pupil` to trace selected field rays through a centered optical system. The command reads the aperture, field sources, spectrum, physical surfaces and observation planes from the saved Project. Each coordinate is a fraction of the entrance pupil radius in design X and Y. The origin is the pupil center. The default samples the center and the four cardinal rim points.

```sh
os-optics project pupil objective.json --source SRC1 --sample 0 0 --sample 0 0.999 --sample 0 1.001
```

For a zoom Project, pass `--configuration ID`. Add `--wavelength-nm W` to select one emitted line or the primary vacuum wavelength. With no wavelength selection each positive-weight line is traced. The JSON result contains the derived pupil dimensions and the launch point, unit direction, surface paths and observation crossings of each ray. Read the crossings of the required observation plane to find the image height and direction. A transparent plane can record a ray that later terminates elsewhere. `counted` reports whether the observation accepted the crossing.

```python
from os_optics.local import system_aperture_analysis, trace_system_pupil

field = {"source_id": "SRC1"}
pupil = system_aperture_analysis("objective.json", **field)
print(pupil["entrance_pupil_radius_mm"], pupil["primary_wavelength_nm"])
trace = trace_system_pupil("objective.json", **field, pupil_coordinates=[(0, .999), (0, 1.001)],
                          wavelength_nm=pupil["primary_wavelength_nm"])
for ray in trace["rays"]:
    for crossing in ray["observations"]:
        print(ray["pupil_coordinate"], crossing["observation_id"], crossing["v_mm"], crossing["numerical_aperture"])
```

The `system_aperture` Project field stores one of four definitions. A configuration can override the complete definition.

| `kind` | Entrance radius used in the trace |
| --- | --- |
| `ENTRANCE_PUPIL_DIAMETER` | Half the declared diameter in mm. |
| `OBJECT_NA` | For a finite object, the axial distance to the entrance pupil times `tan(asin(NA/n))`, where `n` is the object-space index at the primary wavelength in the Project environment. |
| `IMAGE_F_NUMBER` | The absolute effective focal length at the primary wavelength, divided by twice the declared paraxial f-number. The analysis computes focal length from the selected configuration’s optical train in component order, setting aside the source, apertures and terminal instruments. |
| `FLOAT_BY_STOP` | `entrance_pupil_radius_mm`, the projected radius of the physical stop in the selected state. |

`entrance_pupil_z_mm` is the signed Z coordinate of the pupil center on the design axis. A collimated field illuminates this plane with parallel rays at its saved field angle. At a finite conjugate, every ray starts at the field's saved object point and aims along the line through the pupil coordinate. Both use the shared plane for off-axis fields. A virtual pupil upstream of a finite object uses the forward extension of that line. The declared pupil geometry applies to every sampled wavelength. When editing the upstream optics, update the stored pupil plane and any projected stop radius to match the new first-order geometry.

Each sampled ray encounters the actual stops, lens surfaces and mirror holes. An explicit coordinate with radius greater than one has `inside_system_aperture` set to `false`. Its trace shows which physical surfaces a ray just outside the working aperture reaches. A declaration can underfill a large physical stop, and off-axis or nonprimary rays can be vignetted inside the nominal pupil.

`observations` gives local intercept coordinates, direction cosines, refractive index and `numerical_aperture` (`n sin θ` relative to the observation plane normal). `path_id` associates a crossing with its branch in `paths`. An unknown material transmission can leave `power_W` null while the crossing geometry is defined. Requested coordinates share the sampled line power equally and retain their relative spectral weights. Selecting a primary wavelength absent from the emitted spectrum produces `reference_only` geometry with zero spectral weight and zero emitted power. Normal beam runs use the source's own intensity and wavefront model.
