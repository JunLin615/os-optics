# A three-position photographic zoom

The [published example](../tests/fixtures/zmx/zoom/US20130335830_Example01P.zmx) contains three positions of the zoom in US 2013/0335830 A1, example 1. Its 29 physical surfaces include four even aspheres, an aperture stop at surface 17 and two plane plates in front of the image. The object is at infinity. The file was saved in position 2. Its 29 optical semi-diameters are fixed, including the 4.93 mm radius of the stop. [Original file](https://drive.google.com/file/d/1mLOfOMnCn9E_MAktDrebmfL28Cx1qP-l/view) · [Collection notice](../tests/fixtures/zmx/zoom/LensDesigns-MIT.txt)

| Position | Entrance pupil diameter (mm) | Object-space vertical field angles (degrees) | Image plane z (mm) |
|---|---:|---|---:|
| 1 | 3.60 | 0, 25.431, 36.330 | 78.220 |
| 2 | 4.27 | 0, 18.305, 26.150 | 86.230 |
| 3 | 5.69 | 0, 10.052, 14.360 | 104.790 |

`THIC` sets the separations after surfaces 3, 9, 11, 13, 19, 21 and 25. `APER` sets the system entrance pupil diameter and `YFIE` sets the two nonzero vertical field angles. Image z is measured from the first surface vertex in each position. The final plate's rear face is 0.5 mm in front of the prescribed image plane in all three positions.

## Open, save and select a position

Run this Python example from the repository root. The first import resolves every named medium through the supplied [reference catalog](../tests/fixtures/zmx/zoom/catalog.json). The saved project contains the material laws and positions needed for subsequent use.

```python
import json
from pathlib import Path

from os_optics.local import load_project, load_public_catalog
from os_optics.prescription import read_prescription_project

fixture = Path('tests/fixtures/zmx/zoom')
catalog = load_public_catalog(fixture / 'catalog.json')
project = read_prescription_project(fixture / 'US20130335830_Example01P.zmx', catalog=catalog)
output = Path('zoom.project.json')
output.write_text(json.dumps(project.payload(), indent=2), encoding='utf-8')
telephoto = load_project(output).configured('config-3')
```

## Dispersion and wavelength conventions

The reference catalog carries the original six dispersion coefficients and specified wavelength band for each of the eleven named media. Seven use the three-term Sellmeier formula. The other four use the power series for squared index with exponents 0, 2, −2, −4, −6 and −8. Both take air wavelength in micrometres and give index relative to air. The calculation uses the nominal index formulas at the source's reference setting of 20 °C and 101325 Pa. Bulk attenuation and temperature variation are outside this first-order comparison.

| Source catalog | Edition | Media | SHA-256 of input catalog |
|---|---|---|---|
| OHARA | 2018-10-19 | L-BAL42, S-BSL7, S-LAH55, S-LAH66, S-LAL14, S-NPH2, S-NPH53 | `6443303c43900b13f9af1fc0734776ab1dc22dc2aa7b502d382af550bd281a74` |
| HOYA | 2017-04-01 | M-TAFD305, TAFD25, TAFD30 | `66b1682211329b5759217826a741ce8bdb174f227e9f2e766f255681582b1fa3` |
| ZEON | Undated | E48R | `ecfca0d4d15d2cb7b46e629839f5fe2d5afb60e560ad0c368979a2293ad320cd` |

The source prescription has SHA-256 `2fbb782d18e01826d44912a34c720e935f813c20e89405c45b83a9130d6e617f`. The importer takes the three active wavelengths from `FTYP` and ignores the unused wavelength slots. The primary line is 587.5618 nm in air, or 587.721865 nm in vacuum.

## Independent optical comparison

The [regression test](../tests/test_zoom_import_reference.py) reads the original surface curvatures, materials and per-position spacings independently of the importer. A unit-height parallel ray starts with reduced slope `p = 0`. At a surface of curvature `c`, the update is `p -= height * (n_after - n_before) * c`. Across a spacing `t`, the update is `height += t * p / n_after`. With relative indices and air at both ends, the effective focal length is `−1/p`. The paraxial focus lies at `image_z − height/p`.

| Position | Air wavelength (nm) | Effective focal length (mm) | Paraxial focus z (mm) |
|---|---:|---:|---:|
| 1 | 486.1327 | 18.561634 | 78.235499 |
| 1 | 587.5618 | 18.569495 | 78.214587 |
| 1 | 656.2725 | 18.601071 | 78.245179 |
| 2 | 486.1327 | 27.844927 | 86.281621 |
| 2 | 587.5618 | 27.821005 | 86.225005 |
| 2 | 656.2725 | 27.848914 | 86.247296 |
| 3 | 486.1327 | 53.496191 | 104.916044 |
| 3 | 587.5618 | 53.343970 | 104.776457 |
| 3 | 656.2725 | 53.333903 | 104.774787 |

For the native measurement, the test reopens the saved project and selects each position. It shifts the parallel axial source by 0.001 mm and traces its three chief rays to the image observation. Their exit slopes give effective focal length `−0.001/slope`. Their image hits give the actual plane z and, by extrapolation to the axis, the focus z. Across the nine comparisons, the largest observed absolute differences were 0.000000058 mm for effective focal length, 0.000000048 mm for focus z and less than 0.000000001 mm for the prescribed image plane. At the primary wavelength the native trace measured effective focal lengths of 18.569494565, 27.821005176 and 53.343970383 mm.
