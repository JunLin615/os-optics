# Importing a sequential `.len` prescription

Open the Kingslake double Gauss design from the repository root. The accompanying reference catalog supplies the archived SK4 and F4 glass records. An import with an active catalog that lacks either glass returns `GLASS_UNKNOWN` at its source row.

```python
from pathlib import Path
from os_optics.local import load_public_catalog, paraxial_analysis
from os_optics.prescription import read_prescription_project

example = Path("tests/fixtures/len")
catalog = load_public_catalog((example / "catalog.json").read_bytes())
project = read_prescription_project(example / "kingslake.len", catalog=catalog)
lenses = [c.instance_id for c in project.components if c.optical_role == "lens"]
result = paraxial_analysis(project.payload(), {
    "components": lenses, "wavelength_nm": 587.720064,
}, catalog=catalog)
print(result["system"]["efl_mm"])  # 100.00393925 mm
```

The file describes four glass elements, a separate central stop, a 12.5 mm entrance pupil diameter and an image plane 89.832 mm behind the last vertex. The Project carries the three spectral lines, editable Gaussian alignment probes at 0° and 14°, and a circular image detector of radius 25.172634 mm. Each probe samples a narrow beam. `trace_system_pupil` uses the separately saved system aperture. The positive meridional rim of the axial `EBR 6.25` pupil launches a parallel ray at +6.25 mm. For finite objects `NAO` determines the launch cone by `n sin(u)` at the primary wavelength. Import checks the declared axial rim against the physical apertures and reports a conflict at the `EBR` or `NAO` command. The field-edge checks below measure chief-ray position.

## Supported records

| Records | Interpretation |
| --- | --- |
| `LEN NEW "title" [focal-length count]`, `NXT`, `END count` | The first block describes the object. Each `NXT` advances one surface. The final block describes the image. The reader checks any stated count. The optional header focal length is a reference value. |
| `UNI` | Millimetres per lens unit. The supported scales are 1, 10, 1000 and 25.4. The default is millimetres. |
| `RD`, `TH`, `CC` | Signed vertex radius, distance to the next vertex, conic constant. An absent or zero radius specifies a plane. Axial gaps between optical surfaces must be positive. The final optical thickness locates the image. |
| `AD`, `AE`, `AF`, `AG` | Rotationally symmetric coefficients of radial powers 4, 6, 8 and 10. Coefficients scale by the length conversion raised to `1 − power`. |
| `AIR`, `GLA name` | Medium immediately after the surface. An omitted glass means air. Named glasses must resolve in the supplied or active catalog. |
| `AP CHK r`, `AST` | Circular clear semi-diameter and aperture stop. Each optical surface needs an explicit checked radius. Plain `AP r` is also accepted at the stop. `AST` defaults to surface 1. An optional checked radius on the image sets the detector radius. |
| `EBR`, `NAO` | Axial beam radius for an object at infinity or object-space numerical aperture for a finite real object in air. The reader treats object distance magnitudes of at least 10⁸ lens units as infinity. |
| `ANG`, `OBH` | Maximum signed meridional half-field angle in degrees at infinity, or finite object height in lens units. A nonzero maximum creates an on-axis field and a field at the specified maximum. |
| `WV`, `WW` | One list of wavelengths in micrometres and a corresponding list of relative weights. The first wavelength is primary. The default spectral lines are 0.58756, 0.48613 and 0.65627 µm, with equal weights. This subset interprets the visible spectral line labels as air wavelengths at 20 °C and 101325 Pa and converts them to vacuum wavelengths for the Project. |
| `DES`, `SNO…`, `//` | Designer and system-note text and line comments. A semicolon separates commands outside quoted strings. |

Solves, pickups, configurations, decentered or reflective surfaces, model glass commands, obscurations and noncircular apertures return a located error. An unchecked drawing radius away from the stop also returns an error, since treating it as a clipping radius changes the trace. Object and image must be planes in air.

## Optical references

The archive publishes a primary effective focal length of **100.003939 mm** for this design. The first-order reference values in the table come from applying each surface power `(n_after − n_before) / R` to a unit-height collimated ray, with translation in the intervening medium. Refractive indices come from the archived glass coefficients. Full-field references use signed sphere intersections and Snell’s law. At each wavelength the reference ray is aimed to cross the center of the physical stop. The native alignment probe uses primary-wavelength paraxial aiming. [Archived design and Table I](https://github.com/jaruiz6363/AberrationCalculator/tree/859b9c7c16ac37b57f3ace2b8b5e978eb56b1316)

| Air wavelength (µm) | Reference EFL (mm) | Reference back focal length (mm) | Reference 14° image height (mm) | Traced 14° image height (mm) |
| ---: | ---: | ---: | ---: | ---: |
| 0.58756 | 100.003939248 | 89.834403227 | 24.949460 | 24.949506 |
| 0.48613 | 100.185883347 | 90.035266670 | 24.945679 | 24.945790 |
| 0.65627 | 100.010052075 | 89.833627504 | 24.951058 | 24.951107 |

The regression allows 0.000003 mm for the focal distances and 0.0002 mm for the full-field image heights. It checks that both probes reach the declared image at all three wavelengths. [Acceptance test](../tests/test_len_import.py)

## Reader choice and fixture provenance

The `rayopt` reader provided an independent check of the basic record syntax. It discards wavelength and pupil records, writes messages for unknown commands and interprets thicknesses greater than 100 as infinity. The `optiland` reader supports more operations and offers strict conversion. Its package requires a separate optical model, VTK and a scientific plotting stack. The TypeScript `optical-format-parser` produces normalized declarations for a JavaScript runtime. This reader converts the scoped records directly to the existing Python prescription representation and uses the same catalog resolver and native geometry checks as the other formats. [rayopt source tree](https://github.com/quartiq/rayopt/tree/a51f1dbd7c11bb79157e23624951c44443e02070/rayopt) · [optiland source and dependency list](https://github.com/optiland/optiland/tree/cd0a75693bd7258d1fa23db17b9a4d652423cbd9) · [TypeScript parser](https://github.com/ConvexCoding/optical-format-parser/tree/c943bafdfe9c9b3c53bddd2f4fc984f2b1406dbb)

The fixture reproduces the archive’s `KingslakeDG.len`, omitting its two introductory software-identification comments and using LF line endings. All optical commands are preserved. The `SK4` and `F4` material records transcribe the archive’s six Sellmeier coefficients and source validity intervals. The small catalog includes a synthetic window to satisfy the catalog schema. Only the two glass records enter the imported Project. [Source prescription](https://github.com/jaruiz6363/AberrationCalculator/blob/859b9c7c16ac37b57f3ace2b8b5e978eb56b1316/tests/fixtures/lenses/KingslakeDG.len) · [Glass archive](https://github.com/jaruiz6363/AberrationCalculator/tree/859b9c7c16ac37b57f3ace2b8b5e978eb56b1316/catalogs/Glass) · [Fixture license](../tests/fixtures/len/LICENSE.txt)
