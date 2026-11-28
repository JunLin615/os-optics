# Importing a lens prescription

Open a sequential optical design as an editable Project, or import a CSV surface table as a compound unit for an existing Project.

## Open a prescription file

```sh
os-optics-kit import-prescription telescope.zmx --out telescope.project.json
os-optics project check telescope.project.json
os-optics project run telescope.project.json --out-dir telescope-run
os-optics view telescope-run
```

The Engine API returns a validated native Project. All resolved material data, optical elements, sources, and observation planes travel with the saved JSON. Supply a matching public catalog with `--catalog` or the Python `catalog` argument when a design names historical glasses.

```python
import json
from pathlib import Path
from os_optics.prescription import read_prescription_project

project = read_prescription_project("telescope.zmx")
Path("telescope.project.json").write_text(json.dumps(project.payload(), indent=2), encoding="utf-8")
```

### Reading `.zmx`

The reader accepts sequential text in UTF-8 or byte-order-marked UTF-16. It supports centered standard and even-aspheric surfaces, conic mirrors in air, circular clear apertures, annular mirror openings, catalog glass, and real finite or infinite object conjugates. It converts design wavelengths in air to vacuum at the prescription's reference temperature and pressure. The image observation uses a clipping radius only when the file specifies a physical image aperture.

Vertex spacing is signed in the file's fixed Z frame. The first surface after the object sets the origin, including when that surface is an unpowered reference plane. A negative spacing near a stop can place a curved vertex through its opening. The importer tests clearance at the opaque rim. A mirror reverses the travel direction, so a negative spacing on the return leg moves the ray toward smaller Z. The next encounter must be reachable along that leg. An unsupported layout raises an error with its source location. Coordinate breaks, tilted or decentered surfaces, immersed mirrors and terminal glass immersion require additional optical models.

A multi-configuration file becomes one Project whose base geometry is the active state recorded by `MNUM`. Fixed spacings between elements, image-plane spacings, field coordinates and system apertures become native configuration overrides. Element identities and optical models are shared. Choose a state with `os-optics project run zoom.project.json --configuration config-2 --out-dir zoom-2`. Linked configuration solves, unresolved automatic clear radii, and changes to the shape or aperture of a shared element require an export with explicit supported values or a separate file per state. In a single-state file, surface solves `PZUP`, `MAZH` and `VDSZ` use their saved numeric geometry. Subsequent edits use those fixed values. A physical stop with an automatically calculated rim uses the available surrounding body extent and clearance to its neighboring lens.

Each field gets an editable Gaussian alignment probe aimed at the paraxial entrance pupil for the primary wavelength. For a central obscuration the probe samples the clear annulus. Probe power is 1 mW per field, distributed by the input spectral weights. Each state also stores its system aperture and signed entrance pupil position. `trace_system_pupil` takes normalized pupil coordinates and traces geometric rays from each field through the physical surfaces to the prescribed image plane. `ENPD` sets the entrance pupil diameter directly. `FNUM` sets the diameter to the absolute primary-wavelength paraxial effective focal length divided by the declared image f-number. For a finite object, `OBNA` sets the axial cone by `NA = n sin(u)` at the primary wavelength. With `FLOA`, the paraxial image of the marked stop supplies the pupil. The native trace resolves central obscuration and field- or wavelength-dependent clipping at individual surfaces. For a numerical aperture declaration the importer checks axial marginal rays at the primary wavelength and identifies the first conflicting surface at the declaration's source location.

RayOptics 0.9.10 opened a separate 16-interface zoom at its saved spacings of 17.79 and 27.89 mm and reported all 15 configuration operands as unhandled. On the 29-surface reference zoom its material lookup stopped at a missing catalog key. Inspection of Optiland 0.6.3 found no handler for the 40 configuration records in that file. Its 21 `THIC` entries change seven spacings across three states. Reading only its saved surface table loses the other two focal lengths. The Engine reader resolves those records before constructing native components. [RayOptics release](https://pypi.org/project/rayoptics/0.9.10/) · [Optiland release](https://pypi.org/project/optiland/0.6.3/)

See the [microscope and two-mirror reference traces](../ZMX_IMPORT.md) and the [three-position zoom comparison](../ZOOM_IMPORT.md) for measured intercepts, pupil checks and focal lengths.

### Reading `.len`

The same command accepts centered sequential `.len` files. The [`.len` reader guide](../LEN_IMPORT.md) lists supported records, glass lookup, and the three-wavelength comparison for a double Gauss design.

### Archive and session inputs

A `.zda` stores analysis-window and display settings. Open the adjacent `.zmx` with the same stem. All 460 session files in the inspected sample directory had a matching prescription and the same eight-byte binary prefix.

A `.zar` bundles a design with supporting files. Restore the archive locally, then open the extracted text `.zmx` with its matching materials. Nine public archives failed ZIP detection. They used two binary header versions and stored a compressed `.zmx.LZW` member with an ASCII or UTF-16LE name. Some packages include opaque component data that needs a separate surface export. The evaluated reader requires Python 3.13 and carries an AGPL-3.0-only license. A native archive reader would need member selection, dependency handling and verification against a paired text prescription. Mechanical CAD interchange is tracked separately in [Engine #92](https://github.com/JunLin615/os-optics/issues/92). [Archive reader and examples](https://github.com/tttom/zmxtools/tree/c14179f476af4dcac54a94e78b41135b3973360e)

## Import a CSV surface table

`csv-surface-table/1` produces one `COMPOUND` model whose members are the physical elements and aperture stops. Place the compound in a Project to trace and inspect it alongside the rest of the design.

**Command line**

```
os-optics project import-prescription lens.csv --model-id objective
os-optics project import-prescription lens.csv --model-id objective --into project.json --instance-id OBJ --pose 0,0,120 --out project.json
```

The first form prints the imported models, materials, coatings, a suggested component and an import report as JSON.
The second form also adds them to `project.json`: the component `OBJ` uses the imported unit and is placed at the pose
(`x,y,z` in mm, or `x,y,z,w,qx,qy,qz` with a unit quaternion). Other options: `--units` (the length unit to read the
file in, see [Units](#units)), `--wavelength-min-nm` / `--wavelength-max-nm`, `--glass-source catalog|inline`,
`--catalog FILE`, `--ignore-column NAME` (repeatable).

Exit codes: 0 imported, 65 refused (the problems are printed as JSON), 64 unreadable file or wrong arguments.

**JSON API** (Python and JSON hosts such as a browser running Pyodide)

```python
from os_optics.local import json_api
answer = json_api.import_prescription(csv_text, {"model_id": "objective"})
answer = json_api.import_prescription(csv_text, {"model_id": "objective", "into": project, "instance_id": "OBJ",
                                                 "pose": {"position_mm": [0, 0, 120], "quaternion_wxyz": [1, 0, 0, 0]}})
```

Options: `format`, `model_id`, `wavelength_band_nm` (`{"min_nm", "max_nm"}`), `units` (see [Units](#units)),
`glass_source`, `into`, `instance_id`, `pose`, `ignore_columns` (a list of column names). A successful import returns `{"ok": true, "format", "model_id", "optical_models",
"model_materials", "model_coatings", "component", "report"}`; with `into` it also returns the updated `project` and its
`project_hash`. `capabilities()["prescription_formats"]` lists the formats this engine reads.

## The CSV surface table

A UTF-8 text file with comma-separated cells. Cells may be quoted (`"..."`, with `""` for a quote inside a cell).
Spreadsheet exports work as they are: a byte-order mark at the start of the file, Windows line ends and trailing empty
cells are accepted. Numbers are plain decimals (`52`, `-0.5`, `1.2e-5`) of magnitude 0 or 1e-300 to 1e300.

- Lines starting with `#` are comments.
- Lines starting with `#!` are directives, written `#! key = value`.
- `[surfaces]`, `[materials]` and `[coatings]` start a table. The surface table is the default, so a file with only
  surfaces needs no section line. The first row of each table is its header.

### Directives

| Directive | Meaning |
|---|---|
| `format` | `csv-surface-table/1`; optional, recommended as the first line |
| `units` | length unit of the file: `mm`, `cm`, `m` or `in` (see [Units](#units)) |
| `name` | a name for the imported lens, shown in the model names (printable ASCII) |
| `wavelength_min_nm`, `wavelength_max_nm` | the wavelength band the lens is used in (give both or neither) |
| `edge_thickness_min_mm`, `edge_thickness_max_mm` | the range you expect for the glass thickness at an element's edge, in mm whatever the file's `units` (any one of them); an element outside it is refused |
| `mech_to_clear_ratio_min`, `mech_to_clear_ratio_max` | the range you expect for a surface's mechanical over clear semi-diameter (any one of them); a surface outside it is refused |
| `ignore_columns` | extra columns (of any table) the import does not read, comma-separated: `#! ignore_columns = vendor_pn, price` |

A column no table has is refused (`COLUMN_UNKNOWN`) unless it is named in `ignore_columns` (the directive, the
`ignore_columns` option or `--ignore-column NAME`) or blank in every row. A skipped column is listed in
`report.ignored_columns` as `{section, column, reason}` with reason `DECLARED` or `BLANK`. A column the import reads
cannot be ignored.

### Units

The file's `units` directive decides its length unit. A `units` option (`--units` on the command line) is used when the
file states none, and must name the same unit when it does: a file that says `units = in` imported with `units: "mm"`
is refused with `UNITS_CONFLICT` at the directive row, never read in either unit silently. Without both, the unit is mm.

### Surface table

One row per surface, in the order light meets them. Header names are not case-sensitive.

| Column | Required | Meaning |
|---|---|---|
| `surface` | yes | the surface label: `OBJ` (object), `IMA` (image), `STO` (aperture stop) or any other label, e.g. `1`, `2a`: up to 200 printable ASCII characters (a label that is not, such as `Fläche1` or a stray byte-order mark, is refused naming the character) |
| `radius` | yes, or `curvature` | signed radius of curvature at the vertex; blank or `inf` for a plane |
| `curvature` | instead of `radius` | 1 / radius; blank or `0` for a plane |
| `thickness` | yes | distance along the axis from this surface's vertex to the next one's, positive; for the last surface only the distance to the image: any number (0, or negative for an image before the last surface), `inf` (an image at infinity, an afocal lens) or blank |
| `material` | yes (the column) | the medium after this surface; blank or `AIR` for air |
| `semi_diameter` | yes | radius of the surface's clear aperture |
| `mech_semi_diameter` | no | the radius this surface is edged to (its mechanical semi-diameter); also accepted as `edge_semi_diameter` |
| `conic` | no | conic constant k |
| `a4`, `a6`, ..., `a18` | no | even-asphere coefficients (a4 multiplies r^4, ...) |
| `linear_sag` | no | the axicon term a (multiplies r: a cone of slope dz/dr = a, dimensionless); also accepted as `a1` or `axicon` |
| `cylinder_axis` | no | `X` or `Y`: the axis a cylindrical surface is straight along |
| `stop` | no | `yes` marks the aperture stop (same as the label `STO`) |
| `coating` | no | a coating defined in the file's `[coatings]` table, or a coating id published in the OpenSci catalog; blank for an uncoated surface |
| `type` | no | `STANDARD`, `PLANE`, `CONIC`, `EVENASPH`, `AXICON` or `CYLINDER` (also `SPHERE`, `CONE`), or a two-variable sag shape: `ZERNIKE`, `FZERNSAG`, `XY_POLY`, `BICONIC`, `TOROIDAL` (see below); other types are refused. The values decide the profile; a stated type is checked against them (see below) |
| `norm_radius` | with `ZERNIKE`, `FZERNSAG`, `XY_POLY` | the normalization radius |
| `sag_terms` | with `ZERNIKE`, `FZERNSAG`, `XY_POLY`; optional with `TOROIDAL` | the shape's terms, `name=value` separated by `;` (see below) |
| `radius_x`, `conic_x` | with `BICONIC` | the radius and conic constant of the section along X |
| `radius_of_rotation` | with `TOROIDAL` | the distance from the vertex to the axis the curve turns about; blank or `inf`: no rotation |
| `comment` | no | free text, not imported |
| `decenter_x`, `decenter_y`, `tilt_x`, `tilt_y`, `tilt_z`, `a2` | no | accepted only when blank or zero |

Accepted alternative header names: `glass` or `medium` for `material`; `sd` or `clear_semi_diameter` for
`semi_diameter` (also `sdia`); `r` for `radius`; `t` for `thickness`; `k` for `conic`; `mech_sd`, `mech` or `edge_sd` for
`mech_semi_diameter`.

The surface profile is `z(r) = c r^2 / (1 + sqrt(1 - (1 + k) c^2 r^2)) + a4 r^4 + a6 r^6 + ... + a r` with
`c = 1 / radius` and `a` = `linear_sag`. A cone alone (an axicon face) has `linear_sag` and no radius: a face of base
angle alpha (between the cone and a plane normal to the axis) rising toward +Z has `linear_sag = tan(alpha)`, falling
toward +Z `-tan(alpha)`. An odd-asphere prescription whose only term is r^1 (`.zmx` `ODDASPHE` with only its first
coefficient) is this axicon term. On a cylinder (`cylinder_axis`) r is the distance across the straight axis: a conic
and aspheric terms give an acylinder, `linear_sag` a roof.

The `type` column is a cross-check, refused (`VALUE_INVALID`) when the row contradicts it: `PLANE` with a radius, a conic
or a `cylinder_axis` or `linear_sag`; `SPHERE` or `CONIC` without a radius; `CYLINDER` without a radius or `linear_sag`;
`AXICON` without `linear_sag`; `SPHERE` with a conic; `STANDARD`, `SPHERE`, `CONIC`, `PLANE` or `CYLINDER` with aspheric
terms (they belong to `EVENASPH`); `STANDARD`, `SPHERE`, `CONIC` or `EVENASPH` with `linear_sag` (it belongs to
`AXICON`); a `cylinder_axis` on any type but `CYLINDER`. A blank type is not checked.

Two-variable sag shapes are chosen by `type` (values in the file's unit; `sag_terms` is one cell, quote it if it holds a
comma):

| `type` | Also | Profile | Columns |
|---|---|---|---|
| `ZERNIKE` | `SZERNSAG`, `ZERNIKE_STANDARD` | adds `sum value Z_i(rho, theta)`: Noll indices, each term of unit RMS over the unit disc | `radius`, `conic`, `a4` .. plus `norm_radius`, `sag_terms = Z4=0.01; Z6=-0.002` |
| `FZERNSAG` | `ZERNIKE_FRINGE` | adds `sum value Z_i(rho, theta)`: Fringe indices, each term of unit peak | as `ZERNIKE` |
| `XY_POLY` | `XY_POLYNOMIAL` | adds `sum value (x / R)^i (y / R)^j` | `radius`, `conic`, `a4` .. plus `norm_radius`, `sag_terms = X2Y0=0.01; X0Y2=0.01` |
| `BICONIC` | | replaces the profile | `radius`, `conic` for the section along Y; `radius_x`, `conic_x` for the one along X |
| `TOROIDAL` | `TOROID` | replaces the profile | `radius`, `conic`, `sag_terms = Y2=1e-4; Y4=-2e-7` (`alpha_i` of `y^(2i)`, in unit^(1 - 2i)) for the curve in the YZ plane; `radius_of_rotation` |

These columns belong to their types: on any other row they are refused (`VALUE_INVALID`). A shape does not combine with
`cylinder_axis` or `linear_sag`, and a biconic or a toroid does not take `a4` .. (`UNSUPPORTED_FEATURE`,
`SAG_SHAPE_COMBINATION_NOT_SUPPORTED`). Piston and tilt terms and a profile undefined in the clear aperture are refused
with their reason codes (`SAG_TERM_NOT_SUPPORTED`, `SAG_OUTSIDE_DOMAIN`). A Zernike surface whose clear aperture reaches
beyond its normalization radius is imported: the stated terms are evaluated there (`ρ > 1`), and a run states the reach
on the surface (`surface_profiles[].aperture_reach_mm`). The `.zmx` `TOROIDAL` and `BICONIC` conventions apply. The
`radius` is the Y section.

### Materials table

Glasses that the OpenSci catalog does not publish are defined in a `[materials]` table:

| Column | Required | Meaning |
|---|---|---|
| `name` | yes | the name used in the surface table (letters, digits, `.`, `_`, `-`) |
| `kind` | yes (or implied) | `SELLMEIER` or `CONSTANT_INDEX` |
| `b1`, `b2`, `b3`, `c1`, `c2`, `c3` | for `SELLMEIER` | n^2 = 1 + sum b_i L^2 / (L^2 - c_i), with L the vacuum wavelength in micrometres and c_i in um^2 |
| `index` | for `CONSTANT_INDEX` | the refractive index |
| `min_nm`, `max_nm` | yes | the vacuum wavelengths the definition is valid for |
| `absorption_per_mm` | no | bulk absorption coefficient alpha (power falls as exp(-alpha x), x in mm) |
| `display_name`, `basis` | no | a readable name, and where the data comes from (printable ASCII) |

A defined material is held to the same rule as every material of a Project: its refractive index must be real and at
least 1 throughout `min_nm` .. `max_nm`. A Sellmeier term with a pole inside the band (`c_i` between (min_nm/1000)^2 and
(max_nm/1000)^2 um^2, `b_i` not 0), `n^2 < 1` anywhere in the band (checked at 65 evenly spaced wavelengths) or a
constant index below 1 is refused (`MATERIAL_INVALID`, at the material's row).

### Coatings table

Coatings of your own (a measured AR curve, a polarization-resolved spectrum, an angle-resolved table, stated phases) are
defined in a `[coatings]` table, one row per tabulated point; the rows of one coating share its `name`:

| Column | Required | Meaning |
|---|---|---|
| `name` | yes | the coating id used in the surface table (letters, digits, `.`, `_`, `-`; not case-sensitive) |
| `wavelength_nm` | yes | the vacuum wavelength of the point |
| `aoi_deg` | for an angle-resolved table | the angle of incidence in the medium the light arrives from: on every row of the coating (a full wavelength x angle grid, each point once) or on none (a spectrum) |
| `reflectance`, `transmittance` | for an unpolarized spectrum | power fractions (for s / p data: their means, derived when left blank) |
| `reflectance_s`, `reflectance_p`, `transmittance_s`, `transmittance_p` | for s / p data (always for an angle-resolved table) | power fractions per polarization (s: field perpendicular to the plane of incidence) |
| `reflection_phase_s`, `reflection_phase_p`, `transmission_phase_s`, `transmission_phase_p` | no | phases in radians, s and p together, on every row: the same value on every row is a constant phase, otherwise one value per wavelength (the same at every angle of that wavelength), linear between wavelengths |
| `kind` | yes, on the first row | the coating kind, e.g. `ANTI_REFLECTION`, `BEAMSPLITTER`, `DIELECTRIC_HIGH_REFLECTOR` |
| `min_aoi_deg`, `max_aoi_deg` | for a spectrum, on the first row | the angles of incidence the coating holds for (an angle-resolved table: its own angles by default) |
| `aoi_reference` | no, first row | `AIR_EQUIVALENT` (default for a spectrum) or `INCIDENT_MEDIUM` (always for an angle-resolved table) |
| `status`, `display_name`, `basis` | no, first row | the status of the data (default `DECLARED`), a readable name, where the data comes from |

Values between points are interpolated linearly in wavelength (bilinearly in wavelength and angle); beyond the points'
wavelengths or angles the outermost values hold, for traced and left-out light alike, and a traced beam there is
reported out of the model's scope (`OUT_OF_MODEL_SCOPE`). Phases follow the convention
`r = sqrt(R) exp(i phi_r)`, `t = sqrt(T) exp(i phi_t)` in the s / p triad of the Engine with the time dependence
`exp(-i omega t)`: a coated refracting surface uses its transmission phases instead of the bare Fresnel phases. A
definition the coating rules refuse (a missing grid point, R + T above 1, phases that differ between angles) is
`COATING_INVALID` at its row; a defined coating no surface uses is reported (`COATING_UNUSED`) and not imported. A
coating defined in the file takes precedence over a published coating of the same id.

```
[coatings]
name,kind,wavelength_nm,aoi_deg,reflectance_s,reflectance_p,transmittance_s,transmittance_p,transmission_phase_s,transmission_phase_p
MY_AR,ANTI_REFLECTION,450,0,0.01,0.01,0.99,0.99,0.1,0.3
MY_AR,,450,30,0.02,0.005,0.97,0.994,0.1,0.3
MY_AR,,900,0,0.012,0.012,0.988,0.988,0.2,0.5
MY_AR,,900,30,0.022,0.006,0.968,0.993,0.2,0.5
```

## Conventions

- **Coaxial and sequential.** All surfaces are centred on one optical axis, in row order. Light travels along +Z.
- **Radius sign.** A positive radius has its centre of curvature after the surface (on its +Z side); a negative radius
  has it before the surface. This is the usual convention of lens design programs and of OpenSci's own models, so
  radii are copied unchanged.
- **Units.** Lengths are converted to millimetres exactly (1 in = 25.4 mm, 1 cm = 10 mm, 1 m = 1000 mm). Aspheric
  coefficients scale with the length unit (a4 by unit^-3, ...). A converted value that has no finite decimal form (a
  curvature or an inch-based aspheric coefficient) is rounded to 28 significant digits, and the report says so.
  Wavelengths are always in nm; `absorption_per_mm` is always per mm.
- **Object and image.** The object row is not geometry: its distance (`inf`, a number, or blank) is recorded in the
  report. The image row is not an element: the last surface's thickness tells where the image plane is, and the report
  gives its position (`image_plane_z_mm`), for example to place an observation plane there. That thickness is never
  geometry: `0` puts the image on the last vertex, a negative value before it (a virtual image; warning
  `IMAGE_BEFORE_LAST_SURFACE`), and `inf` at infinity (an afocal lens: no image plane is reported).
- **Media.** The medium before the first surface and after the last surface is air.
- **Values are kept exactly as written**: no number passes through a binary floating-point value, except the member
  positions in the unit, which are the nearest double of the exact sum of the thicknesses.

## What the import produces

- **Elements.** A run of surfaces from one with air before it to the next one with air after it is one element: a
  singlet, a cemented doublet, a cemented triplet. Each element is a `SURFACE_SEQUENCE` model with surfaces `S1`, `S2`,
  ... and the glasses between them, its first surface at its own origin.
- **Aperture stop.** A stop row with air on both sides becomes an `APERTURE_STOP` model: an opaque plate in that plane
  with a circular opening of the stop's semi-diameter. A stop on a refracting surface is that surface's clear aperture.
- **Other air surfaces.** A surface with air on both sides that is not the stop (a dummy or reference surface) has no
  effect; it is not imported and the spacing continues to the next surface. The report lists it.
- **Outline.** Each surface with a `mech_semi_diameter` is edged there: its profile reaches that radius, and beyond
  it the surface continues as a flat face at its rim height (the ground flange) out to the outline of its glass. The
  element's outer radius is the largest `mech_semi_diameter` of its surfaces; a surface without one counts with its
  `semi_diameter` and reaches the outer radius. So a meniscus whose steep surface is edged at 10.2 mm inside a 12 mm
  element keeps its prescribed thickness: only the glass between the surfaces, each held at its own edge, must have a
  positive thickness. The model states the edge on the surface (`edge_radius_mm`) when it is smaller than the outer
  radius, and the report lists every surface's edge (`surface_edge_radii_mm`). Light between a clear aperture and the
  outline of its glass is blocked.
- **Edge of a surface without `mech_semi_diameter`.** A surface that states no `mech_semi_diameter` and whose profile
  ends before the element's outer radius (a steep face beside a wider one), or leaves no glass there, is edged at its
  own `semi_diameter` and held flat beyond it, when that makes the element a valid solid. The report lists each such
  surface in the member's `derived_edges` (`surface`, `edge_radius_mm`, `basis` `CLEAR_SEMI_DIAMETER`); the model is the
  one the same table gives with that value written in `mech_semi_diameter`. A `mech_semi_diameter` you state is never
  replaced: a profile that does not reach it is `EDGE_INVALID`.
- **Edge check.** The report gives each element's `edge_thickness` (per glass: the thickness at that glass's outline, the
  clear diameter it serves and their ratio) and `mechanical_to_clear` (per surface: its edge over its clear semi-diameter).
  The import and the optical-solid check (`project check`, `project solids`) warn with the same limits
  (`EDGE_THICKNESS_IMPLAUSIBLE`, `EDGE_FAR_BEYOND_CLEAR_APERTURE`) when an element's edge is more than 1.5 clear
  diameters thick (beyond every published product: its molded aspheres with wide flanges reach 1.30) or its glass reaches
  beyond 3 clear radii: the element and the ratio are named. Between the review ratio (0.35 by default) and 1.5 the
  import marks the element's `edge_thickness` row `above_review_ratio: true` and the optical-solid check lists an INFO
  finding; of the published catalog's glass, half sits below 0.12 of its clear diameter and 93 % at or below 0.35. The
  review ratio is `edge_review_ratio` of the import (`import_prescription`, JSON option), of `inspect_optical_solids` /
  `check_project`, and `os-optics project solids --edge-review-ratio R`; declare a range with the directives above to
  have anything outside it refused.
- **Crossing profiles.** The optical-solid check also compares the two surfaces of every glass by their own profiles over
  the clear aperture of either, none held flat at its edge. Where they cross there (a surface edged smaller than the
  other's clear aperture, whose flat hold keeps the drawn glass positive), the Project is refused with
  `SURFACES_INTERSECT`, the thickness between the profiles and where it is; each gap's
  `clear_aperture_min_thickness_mm` reports the smallest one.
- **Stop plate.** The stop plate's outer radius is the stop row's `mech_semi_diameter`. Without one, the plate extends to
  the largest element outline, cut back where a neighbouring element would reach the plate, and never smaller than the
  opening. The report gives the radius used.
- **The unit.** One `COMPOUND` model holds every element and stop at its position along +Z; the unit's origin is the
  first surface's vertex. Its envelope is a cylinder around all members, and its ports are `IN` (front) and `OUT`
  (rear), both along +Z. The model ids are `<model_id>` for the unit and `<model_id>-L1`, `<model_id>-L2`, ...,
  `<model_id>-STOP` for the members.
- **Materials and coatings.** Every material and coating used is stored in the Project (`model_materials`,
  `model_coatings`), so the Project holds the complete lens and does not change when the catalog is updated. A glass
  copied from the catalog keeps the published basis with `; copied from public optical catalog <version>` appended:
  the same copy the native component library and `catalog_model` make. A coating the Project already declares with
  identical content is shared; the same id with different data is refused (`PROJECT_INVALID`).
- **Importing into a Project that already has the glass.** A material with the same id is shared when its physics is
  the same (dispersion, valid band and bulk absorption; its name and basis are only provenance), so a native N-BK7 lens,
  a declared copy of a catalog N-BK7 lens and an imported N-BK7 prescription use one N-BK7, even when copied from
  different catalog releases. The Project keeps its own entry. Copies whose physics differs are refused
  (`PROJECT_INVALID`, naming what differs); give one of them another material name.
- **Provenance in the Project.** The unit's `display_name` ends with the format and the SHA-256 of the imported text,
  `(csv-surface-table/1, source SHA-256 <64 hex digits>)`; each member's `display_name` names its source rows and the
  format. The same SHA-256 is the report's `source_sha256`.
- **Component.** A suggested component (`custom_part` with `optical_model_id`) places the unit; `--into` / `into` adds it
  to your Project.
- **Report.** Counts, the wavelength band and where it comes from, the object and image, every member with its source
  rows, the assumptions made, and warnings.

## Glass names

A material name in the surface table is found:

1. in the file's `[materials]` table (exact name, not case-sensitive);
2. in the OpenSci catalog's published materials: by exact name, or with spaces, `-`, `_` and `.` ignored (`NBK7` finds
   `N-BK7`; the report notes it). If such a loose name matches more than one published material, the import is refused
   and the candidates are listed.

The published dispersion of a catalog glass is copied into the Project as it is. With the glass source `inline`, only
the file's own materials are used. If a glass cannot be found, the error names it, lists the closest published names
and explains how to define it.

## Wavelength band

The imported models are valid over one wavelength band: the one you give (option or directives), or, if you give none,
the band that every material and coating used shares. A band you give that reaches beyond the valid band of a material
or coating is narrowed to the part all of them cover; the report's `wavelength_band_nm` holds the narrowed band and a
`BAND_NARROWED` warning names what limits it. A band none of which they all cover is refused (`MATERIAL_OUTSIDE_BAND`).

## Not imported

These are refused with an `UNSUPPORTED_FEATURE` error naming the row:

- decentres, tilts and coordinate breaks (place a decentred or tilted group as its own component);
- reflecting surfaces in the sequence (`MIRROR`): import the lens groups on each side and place a mirror component;
- non-sequential surfaces, gradient-index media, diffractive surfaces, ideal paraxial surfaces;
- profiles other than plane, sphere, conic, even asphere, axicon, cylinder, Zernike, XY polynomial, biconic and toroid
  (extended polynomial, Chebyshev, Q-type, odd asphere beyond its r^1 term, ...), and an `a2` term;
- an object or image space that is not air;
- a coated surface with air on both sides, an aperture stop included (its coating would have no substrate);
- more than 16 surfaces in one element or more than 64 elements and stops in one lens.

## Errors

An import is complete or refused; part of a lens is never imported. The file is checked in three steps -- the table
itself (layout, values, units), then its glasses and coatings, then the lens geometry -- and a refusal lists every
problem of the first step that has any, each with a code, a message saying what to change, and a location (`row`: the line of the file, counting from 1; `column`;
`surface`: the label; `section`).

| Code | Meaning |
|---|---|
| `OPTIONS_INVALID` | an unknown option or a bad option value |
| `FORMAT_UNSUPPORTED` | a format the import does not read |
| `TEXT_INVALID` | empty, not UTF-8, or a quoted cell that is not closed |
| `DIRECTIVE_INVALID` | an unknown directive or a bad directive value |
| `UNITS_INVALID` | a unit other than mm, cm, m, in |
| `UNITS_CONFLICT` | the `units` option names another unit than the file's `units` directive |
| `SECTION_INVALID` | an unknown or repeated `[section]` |
| `COLUMN_UNKNOWN` | a column the table does not have, holding values and not named in `ignore_columns` |
| `COLUMN_DUPLICATE` | a column given twice, or both `radius` and `curvature` |
| `COLUMN_MISSING` | a required column is absent |
| `FIELD_MISSING` | a required value is empty |
| `VALUE_INVALID` | not a number, a number beyond 1e-300 .. 1e300 in magnitude, a surface label that is not printable ASCII, a `type` the row contradicts, or not one of the accepted words |
| `ROW_INVALID` | a value in a cell that has no column header |
| `SURFACE_ORDER_INVALID` | the object row is not first, the image row not last, a label is repeated, or there is no element or stop |
| `THICKNESS_INVALID` | a zero, negative or infinite spacing between two surfaces (the last surface's thickness is only the image distance) |
| `GLASS_UNKNOWN` | a material neither defined in the file nor published |
| `GLASS_AMBIGUOUS` | a loose name that matches several published materials |
| `MATERIAL_INVALID` | an incomplete or inconsistent material definition, or one whose index is not real and at least 1 throughout its band |
| `MATERIAL_OUTSIDE_BAND` | no part of the band is inside the valid band of every material and coating used, or they share none |
| `COATING_UNKNOWN` | a coating neither defined in `[coatings]` nor published |
| `COATING_INVALID` | an incomplete coating definition (a missing grid point or value) or one the coating rules refuse (R + T above 1, phases that differ between angles, a spectrum without its angle range) |
| `SURFACE_INVALID` | a surface the model refuses, e.g. a radius smaller than its semi-diameter |
| `SURFACES_INTERSECT` | two consecutive surfaces meet inside their clear aperture |
| `EDGE_INVALID` | a `mech_semi_diameter` smaller than the `semi_diameter`, a surface whose profile does not reach its edge, glass with no thickness out to the outline, or an element outside a range the file declares |
| `BODIES_INTERSECT` | two elements, or an element and the stop plate, overlap |
| `UNSUPPORTED_FEATURE` | see "Not imported" |
| `MODEL_INVALID` | the imported model is refused by the Project contract (the message says why), e.g. cylinders along different axes in one element |
| `PROJECT_INVALID` | the Project to import into is not valid, or an id in it conflicts with the import (a material: different physics) |

## Examples

A Cooke triplet with a glass defined in the file and a stop in the air space:

```
#! format = csv-surface-table/1
#! name = Cooke triplet
#! units = mm
[surfaces]
surface,radius,thickness,material,semi_diameter,mech_semi_diameter,stop
OBJ,inf,inf,,,,
1,22.01359,3.25896,XSK16,9,10,
2,-435.7604,6.00756,,9,10,
3,-22.21328,0.99997,F2,6.5,7.5,
4,20.29192,2,,6.5,7.5,
STO,inf,2.75,,5,,yes
6,79.6836,2.95208,XSK16,7.5,8.2,
7,-18.39533,42.20778,,7.5,8.2,
IMA,,,,,,

[materials]
name,kind,b1,b2,b3,c1,c2,c3,min_nm,max_nm
XSK16,SELLMEIER,1.34317774,0.241144399,0.994317969,0.00704687339,0.0229005,92.7508526,310,2500
```

A front stop, a cemented doublet and a coated singlet, in inches:

```
#! units = in
#! wavelength_min_nm = 450
#! wavelength_max_nm = 850
surface,radius,thickness,material,semi_diameter,mech_semi_diameter,stop,coating
OBJ,inf,inf,,,,,
STO,,0.2,,0.25,0.5,yes,
1,2.4,0.25,N-BK7,0.4,0.5,,
2,-1.7,0.1,N-SF5,0.4,0.5,,
3,-5,0.5,,0.4,0.5,,
4,3,0.15,N-BK7,0.4,0.5,,AR_400-1100R0.01T0.99_AOI0-10
5,inf,2,,0.4,0.5,,AR_400-1100R0.01T0.99_AOI0-10
IMA,,,,,,,
```

These two files are part of the engine's tests (`tests/fixtures/prescriptions/`).
