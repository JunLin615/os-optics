# Importing a `.seq` lens

Open a centred, transmissive sequential design with the Python API. Each glass element and aperture stop becomes an editable Project component. An image plane follows the last surface at its prescribed distance.

```python
from os_optics.prescription import read_prescription_project

project = read_prescription_project("lens.seq")
```

## Supported commands

| Commands | Meaning |
| --- | --- |
| `DIM M`, `DIM C`, `DIM I` | Millimetres, centimetres or inches. `MM`, `CM` and `IN` also work. The default length unit is mm. |
| `RDM`, `RDM Y`, `RDM N` | `RDM` and `RDM Y` select signed radii. `RDM N` selects signed curvature in reciprocal file units, the default. Zero describes a plane in either mode. |
| `SO 0 distance` | Plane object in air. `INF` or a positive distance of at least `1e10` in file units represents a collimated object. A positive finite distance places the object upstream of the first vertex. |
| `S radius_or_curvature thickness [glass]` | One surface followed by its distance to the next vertex. Internal spacings must be positive. The optional glass is the transmitted medium. Omitted glass or `AIR` selects air. |
| `CIR radius`, `CIR EDG radius`, `STO` | Clear radius, optional mechanical edge radius and optional stop flag on the current surface. Give a positive `CIR` for every physical surface. A plane air-space aperture must be marked `STO` to become a separate stop. |
| `SPH`, `CON`, `ASP`, `K`, `A` through `H` | Spherical, conic and rotationally symmetric even-asphere profiles. `K` is the conic constant. On an `ASP` surface, `A` multiplies radius to the fourth power, `B` the sixth, through `H` the eighteenth. Coefficients are converted to mm units. `CUF` must be zero. |
| `SI 0 0` | Plane image in air. The last `S` row specifies its distance. |
| `EPD`, `NAO`, `FNO` | Entrance pupil diameter, finite object-space numerical aperture or nominal image-space f-number. Supply one pupil specification. `FNO` gives the infinity-conjugate ratio of effective focal length to entrance pupil diameter. The native pupil trace applies this entrance diameter at the actual object conjugate, with physical clipping at the declared surfaces. |
| `XAN`, `YAN`, `XOB`, `YOB` | Paired angular fields in degrees or object heights in file units. Heights apply to a finite object. An omitted axis defaults to zero at every field. |
| `WL`, `WTW`, `REF` | Air wavelengths in nm, relative line weights and a one-based primary line. The native probe spectrum uses vacuum wavelengths. `TEM 20` declares the supported reference temperature of 20 °C. Standard pressure is 101325 Pa. |
| `PRV`, `PWL`, named rows, `END` | A private glass catalog with a shared grid of air wavelengths in nm and one relative refractive index per wavelength for each named material. The native Project records the nodes with origin `SOURCE_TABLE` and uses monotone cubic interpolation for phase index. The file provides no measurement conditions, reference formula or bound on interpolation error. Group index and group-delay dispersion are unavailable for these tables. Named catalog glasses outside the block resolve through the active local catalog. A `designation_catalog` suffix qualifies the lookup. |
| `LEN`, `TITLE` or `TIT`, `INI`, `SLB`, `GO` | Lens header with optional version comment, title, designer initials, surface label and final command. Initials have no optical effect. |

Command names are case insensitive. Separate commands with newlines or semicolons, enclose labels in single or double quotes, start comments with `!` and continue a line with trailing `&`. Unit field weights (`WTF`) and zero vignetting (`VUX`, `VLX`, `VUY`, `VLY`) are accepted.

The reader reports the physical source line when it encounters unsupported input. This includes scripted or included commands, private dispersion formulas, numeric glass codes, aperture solves, noncircular apertures, image-height fields, coordinate breaks, mirrors and diffractive surfaces. An element with automatic clear apertures needs an export containing its explicit `CIR` values.

## Published asphere and trace comparison

The checked-in example is the published rotationally symmetric variant of design 65988, whose title records removal of the hybrid surface. It has two surfaces, 3.7 mm centre thickness, 6 mm clear radius on each face and a 20-node private material table covering 400–1600 nm. Its SHA-256 is `ab8131f445b5161928d98060d7749edc6fb5a922a5c902a9d360f7a21b2e26ce`. The file comes unchanged from RayOptics 0.9.10, under its BSD 3-Clause license. [Prescription](../tests/fixtures/seq/asphere.seq) · [License](../tests/fixtures/seq/LICENSE) · [Upstream release](https://github.com/mjhoptics/ray-optics/releases/tag/v0.9.10)

RayOptics 0.9.10 traced collimated meridional rays through the original file at entrance heights of 1, 3 and 5 mm. The table gives their reference heights where they cross the prescribed image plane, 12.94603127272 mm behind the rear vertex. The regression imports the file via `read_prescription_project`, saves its native Project as JSON, reopens it with `load_project` and traces the same rays through the reopened model. [Regression test](../tests/test_seq_import.py)

| Source wavelength in air (nm) | 1 mm ray (mm) | 3 mm ray (mm) | 5 mm ray (mm) |
| ---: | ---: | ---: | ---: |
| 656.2725 | 0.059002315 | 0.178583040 | 0.306997858 |
| 587.5618 | 0.054313352 | 0.164053870 | 0.280974523 |
| 486.1327 | 0.043171755 | 0.129500200 | 0.218959032 |

At the primary wavelength the independent effective focal length is 15.85633336 mm and the back focal length is 13.80677168 mm. The native values are 15.85633199 mm and 13.80677037 mm. Across all nine rays the largest image-height difference is 0.000022 mm. RayOptics evaluates the private table with a cubic spline and the native engine uses PCHIP. The tests allow 0.000003 mm for focal length and 0.000030 mm for intercept height.

To reproduce the reference in an environment with RayOptics 0.9.10 and opticalglass 2.0.2, run this from the repository root.

```python
import numpy as np
from rayoptics.gui.appcmds import open_model
from rayoptics.raytr.raytrace import trace

model = open_model("tests/fixtures/seq/asphere.seq")
first_order = model["analysis_results"]["parax_data"].fod
print(first_order.efl, first_order.bfl)
for wave in model["osp"]["wvls"].wavelengths:
    for height in (1.0, 3.0, 5.0):
        ray, _, _ = trace(model["sm"], np.array([0.0, height, 0.0]),
                          np.array([0.0, 0.0, 1.0]), wave, check_apertures=True)
        print(wave, height, ray[-1][0][1])
```

## Reader evaluation

RayOptics 0.9.10 reads this private glass and traces the asphere. Its importer logs unsupported commands and continues, and it can compute missing clear apertures. It served as the independent optical reference. Optiland 0.6.3 includes a `.seq` parser whose private-catalog handler skips the `PRV` block. Using that parser on this design would lose its source glass definition. The native reader therefore translates the supported commands directly into the engine's prescription representation, keeps explicit apertures and index nodes, and reports instructions it cannot represent. The tests run offline without either reference package installed. [RayOptics 0.9.10](https://pypi.org/project/rayoptics/0.9.10/) · [Optiland 0.6.3](https://pypi.org/project/optiland/0.6.3/)
