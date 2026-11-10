# Your first optical design

[User guide](README.md) · English · [简体中文](../zh-CN/getting-started.md)

This walkthrough builds a two-lens beam expander, runs it locally, and opens its optical scene. Complete the [installation](installation.md) first.

## 1. Load the catalog

```bash
os-optics catalog sync
os-optics catalog list --category LENS --wavelength-nm 780 --limit 20
```

The catalog lists OpenSci optical components with their design specifications and simulation models. Copy a returned `optical_sku` into `os-optics catalog show OPTICAL_SKU` to inspect a component.

## 2. Create a project

```bash
os-optics project example --kind beam_expander > project.json
```

In Windows PowerShell 5.1, use:

```powershell
os-optics project example --kind beam_expander | Out-File -Encoding utf8 project.json
```

The generated JSON includes optical components, poses, sources, observation planes, and targets. Each catalog component carries the catalog version used by the design.

Other starting points:

| Example kind | Optical system |
| --- | --- |
| `focus` | A single focusing lens |
| `folded` | A fold mirror and a lens |
| `beam_expander` | A two-lens telescope |
| `relay_4f` | A 4f relay |
| `folded_expander` | A folded beam expander |
| `dichroic_routing` | Two wavelengths routed by a dichroic mirror |
| `polarization` | A waveplate and polarizer |
| `two_beam_interference` | Two arms from one laser crossing at an observation plane |
| `two_color_dichroic` | A two-line source routed to two detectors |
| `dichroic_combiner` | Two lasers combined onto one beam |

With a catalog release that states each coating's group-delay bound (catalog schema 3.2,
such as release `2026-10-28.1`), `two_beam_interference` reports fringe visibility and
coherence together with that delay bound. Where a coating does not state its phase,
`phase_difference_rad` has no value and the result lists `PHASE_UNDEFINED`.

## 3. Simulate

```bash
os-optics project run project.json --out-dir run
os-optics project verify run
```

The run command writes the result and its supporting artifacts into `run`, reporting its progress in the terminal; Ctrl+C stops it. It exits 0 when the calculation completes and every target is met (see [exit codes](results.md#exit-codes)). Start with `manifest.json` for the overall status and target outcomes. See the [results guide](results.md) for individual files and status meanings.

## 4. Explore the scene

```bash
os-optics view run
```

Use the camera presets, select an optical element to see its properties, and toggle beam paths. The viewer supports English and Chinese. Stop the local viewer with Ctrl+C in the terminal.

For a standalone HTML view or image snapshots:

```bash
os-optics view run --write-html view.html
os-optics view run --snapshot shots
```

## 5. Inspect, change, repeat

```bash
os-optics results describe run
os-optics export arrivals run --format csv --out arrivals.csv
os-optics schema project
```

OS Optics 0.13.0 includes semantic edits and compact reads; see
[version matching](installation.md#match-the-documentation-to-the-installed-version) for the
matching installation.
Save edit operations as `ops.json`, for example `set_component_pose` with an `instance_id`
and `position_mm` (`os-optics project update --help` lists every operation), then preserve
the original Project while running the revised design:

```bash
os-optics project update project.json --ops ops.json -o revised.json
os-optics project check revised.json
os-optics project run revised.json --out-dir run-revised
os-optics results targets run-revised
os-optics results freshness run --project revised.json
```

`project update` checks the result like `project check` and writes nothing when an
operation or the check is refused. The final command identifies how the old run differs
from the revised Project. Inspect execution, quantity validity and optional target
assessments separately. A target-free run can still calculate useful quantities. Keep each
run for comparison; the [budget recipe](https://github.com/JunLin615/os-optics-kit/blob/main/docs/workflow-recipes.md#computation-budgets)
in os-optics-kit explores how limits affect completion and available results, and its
[editing recipe](https://github.com/JunLin615/os-optics-kit/blob/main/docs/workflow-recipes.md#edit-a-project-by-id) edits by object ID with a
saved retry record.

## Working with your agent

Give your coding agent a concrete task:

> Use OS Optics to create a beam-expander example. Run it locally, inspect the target outcomes and power budget, and export the beam-arrival table as CSV. Show me the 3D view and explain the beam sizes and component spacing.

The CLI returns structured output, and the Python API lets an agent read the same project and scientific results. The [beam-expander case](https://github.com/JunLin615/os-optics-kit/tree/main/cases/beam-expander) in os-optics-kit shows the complete local workflow, and `os-optics-kit skills install --dest DIR` gives your agent the kit's task skills.

Next: [Read and export results](results.md) or [continue to hardware](hardware.md).
