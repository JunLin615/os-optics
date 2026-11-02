# Results, visualization, and export

[User guide](README.md) · English · [简体中文](../zh-CN/results.md)

One run produces a complete record of the design and its simulation. The results API reads that record directly, so inspecting or exporting data does not rerun the simulation.

## Read the run directory

| File | Contents |
| --- | --- |
| `manifest.json` | Overall status, target outcomes, identities, and file hashes |
| `project.json` | The optical project that was run |
| `engine_request.json` | The input passed to the physics engine |
| `simulation_result.json` | Target values, beams, observations, paths, power budget, and issues |
| `ray_data.json` | Traced chief and pupil rays, including surface crossings and optical path lengths |
| `local_validation.json` | Local Validation record with project, catalog, engine, and result identities |
| `optical_scene.json` | Structured optical scene |
| `optical_scene.glb` | 3D scene in glTF binary format |

```bash
os-optics results describe run
os-optics results monitor run output
os-optics project verify run
```

`output` is the observation-plane identifier in the beam-expander example. Use `results describe` to discover the planes available in your own run.

## Interpret the result

These compact controls are available in 0.12.0; install the matching package as
described in [version matching](installation.md#match-the-documentation-to-the-installed-version).

When a quantity lies outside a component model's declared wavelength, angle or beam
domain, the targets that depend on it are assessed `INDETERMINATE` and carry the
model-scope issue code in `applicability_issue_codes`. The quantity keeps its value,
unit and bounds, so you can read the number while the assessment states that it does
not establish validity outside the declared domain. Keep the calculation inside the
domain when you need a `MET` or `UNMET` assessment; the
[metasurface case](https://github.com/JunLin615/os-optics-kit/tree/main/cases/interop-metasurface) in os-optics-kit works through such a domain.

```bash
os-optics results targets run
os-optics results freshness run --project project.json
```

Read these dimensions separately:

| Dimension | What to inspect | Meaning |
| --- | --- | --- |
| Computation | `outcome.execution`: completion, termination and budgets | Whether the requested computation completed, and which limits were exhausted |
| Quantity | The metric's value, unit, validity, bounds and applicability issues | A usable point value, an interval, an unavailable quantity or a quantity outside its model |
| Customer requirement | Each target's `assessment` and `outcome.requirements` | `MET`, `UNMET`, `INDETERMINATE` or `NOT_APPLICABLE`; no targets means no requirement was assessed |
| Freshness | `CURRENT`, `HISTORICAL` or `UNDETERMINED`, with reason codes | Whether the saved run corresponds to the supplied Project |
| Commercial result | The response to configuration or order commands | What can be purchased or ordered under that operation's conditions |

An aggregate `PASS` does not answer all five questions. A completed run can have an
unmet optional target and still contain useful quantities. Read applicability on the
quantity or target you use: an unrelated scope issue does not invalidate every
quantity. Keep a bounded value as an interval, and preserve an indeterminate target
when its interval cannot determine the requested comparison.

A physical or computation-budget edit is reported as `PHYSICS_CHANGED`; a target-only
edit as `TARGETS_CHANGED`. Preserve the original run and its hashes. Changing targets
does not turn an old target assessment into a new one. Another engine build can leave a
run `CURRENT` when both Project and lowered input match; inspect `engine_build_matches`
when reproducing the calculation. See the [comparison recipes](https://github.com/JunLin615/os-optics-kit/blob/main/docs/workflow-recipes.md#compare-and-freshness) in os-optics-kit.

`project verify` checks recorded artifact hashes and consistency. It does not compare
the calculation with an independent optical solution or an experiment. The analytical
references in the worked cases provide separate checks with stated assumptions.

The read command `results targets` exits 0 when it answers, including an unmet target.
`results freshness` uses 0 for `CURRENT`, 1 for `HISTORICAL` and 2 for `UNDETERMINED`.
Read structured assessments to decide a scientific task's result; command completion
alone is insufficient.

## Compact summaries and worked cases

[os-optics-kit](https://github.com/JunLin615/os-optics-kit) adds compact JSON summaries of saved runs and worked cases with
verified target outcomes:

```bash
python -m pip install os-optics-kit
os-optics-kit summary run
os-optics-kit cases list
```

The summary reads the saved run without repeating the simulation. The
[beam-expander](https://github.com/JunLin615/os-optics-kit/tree/main/cases/beam-expander) and [ideal-lens focus](https://github.com/JunLin615/os-optics-kit/tree/main/cases/ideal-lens-focus) cases
show a catalog design and a catalog-free design with their target outcomes.

## View and share

```bash
os-optics view run
os-optics view run --write-html view.html
os-optics view run --snapshot shots
```

The browser viewer includes camera presets, selectable components, beam visibility controls, and English/Chinese labels. Snapshots include orthographic PNG views, `scene_summary.json`, and a legend.

## Export tables

```bash
os-optics export arrivals run --format csv --out arrivals.csv
os-optics export rays run --format jsonl --out rays.jsonl
os-optics results columns arrivals
os-optics results metrics --name waist_distance_x_mm
```

Exports include monitors, arrivals, rays, paths, segments, power budgets, superpositions, wavefronts, irradiance, and metric definitions. Use `export --help` for formats and selectors. Wavefront exports use traced optical-path differences; irradiance maps use the paraxial Gaussian superposition model.

From Python:

```python
from pathlib import Path
from os_optics.results import load_run

results = load_run("run")
print(results.describe())
print(results.power_budget())
Path("arrivals.csv").write_bytes(results.export("arrivals", "csv"))
```

## Units and conventions

The API uses millimetres for length, nanometres for vacuum wavelength, degrees for angles, and watts for power. Gaussian beam size `w` is the **1/e² intensity radius**. Coordinates are right-handed; a transmissive element's local `+z` is its optical axis, while a reflector's local `+z` is the coated-face outward normal. Export column names and metric descriptors identify the relevant units.

Next: [Continue from design to hardware](hardware.md).
