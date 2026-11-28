<p align="center">
  <a href="https://opensci-optics.com/"><img src="assets/logo.svg" alt="OS Optics" width="84"></a>
</p>

<h1 align="center">OS Optics</h1>

<p align="center">
  <strong>Open-source Python optical design &amp; simulation engine — built for engineers, parallel optimization, and AI coding agents.</strong>
</p>

<p align="center">
  English · <a href="README.zh-CN.md">简体中文</a>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-16a34a.svg" alt="MIT License"></a>
  <a href="https://pypi.org/project/os-optics/"><img src="https://img.shields.io/pypi/v/os-optics.svg" alt="PyPI"></a>
  <a href="https://pypi.org/project/os-optics/"><img src="https://img.shields.io/pypi/pyversions/os-optics.svg" alt="Python 3.11+"></a>
  <a href="https://github.com/JunLin615/os-optics/stargazers"><img src="https://img.shields.io/github/stars/JunLin615/os-optics?style=social" alt="GitHub Stars"></a>
  <a href="https://opensci-optics.com/"><img src="https://img.shields.io/badge/website-opensci--optics.com-2563eb" alt="Website"></a>
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a> ·
  <a href="#why-os-optics">Why OS Optics</a> ·
  <a href="#visual-gallery">Gallery</a> ·
  <a href="#capabilities">Capabilities</a> ·
  <a href="#scientific-ecosystem--interop">Interop</a> ·
  <a href="#ecosystem-os-optics--os-optics-kit">Toolkit</a> ·
  <a href="#contributing--roadmap">Roadmap</a>
</p>

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/images/hero-dual-dark.png">
    <source media="(prefers-color-scheme: light)" srcset="docs/images/hero-dual-light.png">
    <img src="docs/images/hero-dual-light.png" alt="OS Optics: 3D multi-element imaging lens prescription (left) and 160+ optic Rb-87 cold-atom free-space quantum bench (right)" width="100%">
  </picture>
  <br>
  <sub><b>One engine, two worlds:</b> multi-element imaging lens prescriptions (left) and 160+ optic 3D free-space quantum benches (right).</sub>
</p>

**OS Optics** (`os-optics`) is a deterministic, local-first Python optical design and physical simulation engine released under the **MIT License**. Design multi-element imaging lenses, catadioptric telescopes, and 100-optic free-space benches; trace 3D rays, propagate Gaussian beams, analyze polarization, interference, PSF/MTF, ghosts, and cavities; and interoperate cleanly with Python diffraction, quantum photonics, and optimization libraries — directly from Python, the CLI, or coding agents such as **DeepSeek Harness, Claude Code, Codex, Cursor, and WorkBuddy**.

```bash
python -m pip install os-optics
```

| Package / Repo | Where | Role |
| --- | --- | --- |
| [`os-optics`](https://pypi.org/project/os-optics/) | PyPI | The MIT-licensed optical engine: catalog, physics simulation, structured results & 3D viewer |
| [`os-optics-kit`](https://pypi.org/project/os-optics-kit/) | PyPI | Community toolkit: 18 reproducible cases, agent skills, parallel sweeps, tolerance, live viewer & plots |
| [`os-optics`](https://github.com/JunLin615/os-optics) | GitHub | This repository: engine source (`src/os_optics`), tests, documentation & roadmap |
| [`os-optics-kit`](https://github.com/JunLin615/os-optics-kit) | GitHub | Community hub: worked cases, vendor-neutral agent skills, workflows & contributions |

## Why OS Optics

Most optical tools force a trade-off between closed, seat-locked commercial GUIs and lightweight scripts that lack real component data or honest validity bounds. **OS Optics** is engineered from the ground up for **reproducible optical design, high parallelism, scientific ecosystem interop, and multi-agent workflows**:

- **Natively parallel & built for multi-agent swarms** — The simulation core is completely stateless, I/O-free, and free of GUI or license-seat locks. Spawn dozens of parallel worker processes, containers, or an **agent swarm** to explore competing lens topologies and parameter branches concurrently — then merge and compare candidates objectively via canonical JSON outputs, deterministic exit codes, and SHA-256 hashes.
- **Seamless interop with Python diffraction, quantum & optimization libraries** — Keep the engine core ultra-light while plugging into the wider scientific Python stack through built-in data contracts (`os-optics interop list`): hand off transverse beam fields to **LightPipes / prysm / POPPY / Diffractio**, export lossy linear optical networks with per-path group delays to **Perceval / Piquasso / QuTiP**, import **grcwa / meent** metasurface unit-cell models, or drive loops with **SciPy / Optuna / SALib**.
- **Deterministic physics with honest validity bounds (`value_basis`)** — Every number reports whether it is a `MODEL` value, a proven `LOWER_BOUND` / `UPPER_BOUND`, or an `ESTIMATE` beyond a model limit. Targets outside model scope are flagged `INDETERMINATE`, never silently disguised as `PASS`.
- **Real component catalog + custom prescriptions** — Sync a versioned, offline-first catalog of 3,000+ real optical components (`OS-OPT-*`), or declare custom lenses, even aspheres, freeforms (Zernike / XY polynomial / biconic), crystals, and CSV lens prescriptions in the same engine. [Open a sequential prescription as a Project](docs/engine/PRESCRIPTION_IMPORT.md).
- **Agent-native CLI & in-memory evaluation loops** — Every command speaks structured JSON with self-describing schemas (`os-optics capabilities`, `os-optics schema`). Use selective evaluation (`os-optics project evaluate` / `EvaluationSession`) to score candidates in memory while reusing compiled optics and catalog data across iterations.
- **Ultra-light pure Python core** — Installs in seconds on Windows, macOS, and Linux (Python 3.11+) with only `numpy`, `scipy`, `pydantic`, and `shapely`. No compiler, no license server, no GPU requirement, and no account needed.

| Dimension | Commercial Optical GUIs | Pure Academic Scripts | **OS Optics (`os-optics`)** |
| --- | --- | --- | --- |
| License & cost | Proprietary, expensive seats | Open source | **MIT License, free & local** |
| Interface | GUI-first, macro scripting | Python-only | **Python API + JSON CLI + Headless/3D Viewer** |
| **Parallelism & Agent Swarms** | **Dongle/seat-locked, fragile COM/GUI** | Ad-hoc scripts | **Stateless core, unlimited workers, native for multi-agent swarms** |
| **Scientific Ecosystem Interop** | Closed binary formats | Manual glue code | **Built-in `interop` contracts: diffraction, quantum, metasurfaces, optimizers** |
| Coding Agent readiness | Hard to drive reliably | No standard contract | **JSON schemas, deterministic exit codes, vendor-neutral skills (`kit`)** |
| Component library | Fragmented vendor files | Mostly ideal/custom | **Versioned offline catalog (3,000+ parts) + custom models** |
| Model validity honesty | Often silent extrapolation | Varies | **Explicit `value_basis`, power bounds & model-scope issues** |
| Reproducibility | Binary project files | Script-dependent | **Canonical JSON Projects + SHA-256 input/result hashes** |

## Visual Gallery

Each row pairs an **optical system design** (left) with its **physics or tolerance analysis** (right), spanning imaging lens prescriptions and 3D free-space quantum benches:

<table>
  <tr>
    <td width="50%" valign="top">
      <img src="docs/images/case1-18mm-layout.png" alt="18mm f/2 full-frame mirrorless 14-element 12-group optical layout" width="100%">
      <br><sub><b>Case 1 — 18 mm f/2 Full-Frame Mirrorless (Design):</b> 14-element / 12-group lens prescription spanning 49.6° half-field with cemented doublets and cover glass.</sub>
    </td>
    <td width="50%" valign="top">
      <img src="docs/images/case1-18mm-tolerance.png" alt="18mm f/2 128-trial Monte Carlo MTF tolerance histogram" width="100%">
      <br><sub><b>Case 1 — Monte Carlo Tolerance Analysis:</b> 128-trial manufacturing MTF@40 lp/mm yield distribution evaluated against the <code>[0.6, 1.0]</code> specification band.</sub>
    </td>
  </tr>
  <tr>
    <td width="50%" valign="top">
      <img src="docs/images/case2-85mm-layout.png" alt="85mm full-frame all-spherical portrait lens meridional ray layout" width="100%">
      <br><sub><b>Case 2 — 85 mm All-Spherical Portrait Lens (Design):</b> full-frame catalog-glass meridional ray trace from axis to 21.63 mm corner image height.</sub>
    </td>
    <td width="50%" valign="top">
      <img src="docs/images/case2-85mm-canon-mtf.png" alt="85mm all-spherical portrait objective polychromatic MTF benchmarked against Canon RF85mm F2" width="100%">
      <br><sub><b>Case 2 — Polychromatic Diffraction MTF Benchmark:</b> 10 &amp; 30 lp/mm full-field MTF directly compared against digitized official Canon RF85 F2 data.</sub>
    </td>
  </tr>
  <tr>
    <td width="50%" valign="top">
      <img src="docs/images/case3-rb87-platform.png" alt="Full 1.96m Rb-87 cold-atom 3D free-space quantum optical bench" width="100%">
      <br><sub><b>Case 3 — Rb-87 Cold-Atom 3D Quantum Bench (Global Layout):</b> 1.96 m composed workspace routing 160+ optics for MOT, optical tweezers, Rydberg excitation, and readout.</sub>
    </td>
    <td width="50%" valign="top">
      <img src="docs/images/case3-rb87-tolerance-en.png" alt="Rb-87 free-space beam pointing tolerance distribution" width="100%">
      <br><sub><b>Case 3 — 3D Alignment &amp; Pointing Tolerance:</b> seeded Monte Carlo mirror-tilt perturbations verifying beam-position stability within <code>±0.05 mm</code> bounds.</sub>
    </td>
  </tr>
</table>

<details>
<summary>More visuals: semiconductor RDL 3D objective, diffraction MTF, folded telescope &amp; barrel envelope</summary>
<br>
<table>
  <tr>
    <td width="50%" valign="top">
      <img src="docs/images/extra-rdl-3d.png" alt="Semiconductor RDL inspection microscopy objective 3D render" width="100%">
      <br><sub>Semiconductor RDL high-NA microscopy objective (3D studio glass shading).</sub>
    </td>
    <td width="50%" valign="top">
      <img src="docs/images/extra-diffraction-mtf.png" alt="Tangential and sagittal polychromatic diffraction MTF" width="100%">
      <br><sub>Polychromatic tangential &amp; sagittal diffraction MTF against the diffraction limit.</sub>
    </td>
  </tr>
  <tr>
    <td width="50%" valign="top">
      <img src="docs/images/extra-catadioptric-3d.png" alt="Catadioptric Schmidt-Cassegrain folded mirror telescope" width="100%">
      <br><sub>Non-sequential reflective folded Schmidt-Cassegrain telescope.</sub>
    </td>
    <td width="50%" valign="top">
      <img src="docs/images/extra-18mm-barrel.png" alt="18mm f/2 mechanical barrel envelope and active adjustment stations" width="100%">
      <br><sub>18 mm f/2 barrel envelope with active alignment stations.</sub>
    </td>
  </tr>
</table>
</details>

## Quick Start

### 1. Install

```bash
python -m pip install os-optics
os-optics doctor
```

Or install as an isolated CLI tool with [uv](https://docs.astral.sh/uv/): `uv tool install os-optics`.

### 2. Run your first optical design in 4 commands

```bash
os-optics catalog sync
os-optics project example --kind beam_expander > project.json
os-optics project run project.json --out-dir run
os-optics view run
```

This syncs the offline optical catalog, generates a two-lens beam expander, runs deterministic simulation and validation, and opens the interactive 3D optical scene in your browser. Inspect or export results headlessly:

```bash
os-optics results describe run
os-optics results targets run
os-optics export arrivals run --format csv --out arrivals.csv
os-optics project verify run
```

### 3. Use from Python (including fast candidate evaluation)

```python
from os_optics.local import EvaluationSession, example_project_of_kind, run_optical_project
from os_optics.results import load_run

project = example_project_of_kind("beam_expander")

# Fast in-memory selective evaluation for optimization & parallel agent loops
session = EvaluationSession()
ev = session.evaluate(project, outputs=("readings", "requirements"))
print(ev.requirements())

# Full verified run with saved artifacts and 3D scene
run = run_optical_project(project, output_dir="run-python")
results = load_run("run-python")
print(run.status.value, results.power_budget())
```

### 4. Drive it with your Coding Agent (or Agent Swarm)

Paste this into **DeepSeek Harness, Claude Code, Codex, Cursor, WorkBuddy**, or any agent that can run Python:

> Install `os-optics` and `os-optics-kit` from PyPI, run `os-optics-kit skills list`, and design a 532 nm 3× beam expander using real catalog lenses with spot radius < 50 µm at the target plane. Verify all targets and show me the 3D views.

See [AGENT_GUIDE.md](AGENT_GUIDE.md) for the concise agent entry point.

## Capabilities

| Category | What OS Optics computes |
| --- | --- |
| **3D Ray Tracing & Geometry** | Non-sequential 3D tracing through planes, spheres, conics, even aspheres, cylinders, axicons, Zernike / XY-poly / biconic / toroid freeforms, GRIN rods, faceted solids, cubes, prisms, wedges, holes, and lens arrays. |
| **Materials, Coatings & Spectra** | Dispersion formulas (Sellmeier, Herzberger, power-series, thermo-optic `dn/dT`, tabulated), angle- & polarization-resolved s/p coatings with phase & group-delay bounds, multi-line and continuous spectra. |
| **Gaussian Beams & Polarization** | Astigmatic Gaussian `q`-parameter propagation (waist, beam radius, divergence, Rayleigh range, `M²`), Jones & Stokes calculus, partial/unpolarized light, uniaxial birefringence, Faraday rotators, SHG slabs. |
| **Interference, Power & Safety** | Full power ledger with proven upper/lower bounds, two-beam interference (fringe period, visibility, coherence margin), per-surface peak irradiance & pulsed fluence checked against damage ratings. |
| **Imaging & Wave Analyses** | Diffraction & geometric PSF / MTF, encircled energy, geometric wavefront, 4-bounce interface ghost analysis with remainder bounds, coherent/speckle field propagation, high-NA vector focal fields, linear optical cavities, linear optical network export. |
| **Parallelism, Evaluation & Workspaces** | Stateless engine for multi-process & multi-agent swarm exploration, `project evaluate` / `EvaluationSession`, multi-configuration comparison, modular `workspace` composition, headless 3D/2D snapshots (`--section ALL`), and SHA-256 verified runs. |

## Scientific Ecosystem & Interop

`os-optics` stays dependency-light by design and acts as the **deterministic hub** connecting to specialized Python physics libraries via versioned exports (`os-optics interop list`):

| Domain | Third-Party Tools | OpenSci Handoff Contract | Status |
| --- | --- | --- | --- |
| **Optimization & Sensitivity** | `SciPy`, `Optuna`, `SALib`, `BoTorch` | Stateless `EvaluationSession` / JSON runs with verified hashes | Verified example |
| **Scalar Diffraction & Wave Optics** | `LightPipes`, `prysm`, `POPPY`, `Diffractio` | `os-optics results handoff` (transverse beam state, apertures, field convention) | Verified example |
| **Quantum Photonics & Cold Atoms** | `Perceval`, `Piquasso`, `QuTiP`, `NetSquid` | `os-optics project network` (lossy transfer matrix `T[out,in]` & per-path group delays) | Verified example |
| **Metasurfaces & Nanophotonics** | `grcwa`, `meent`, `torcwa`, `DFlat`, `metabox` | `TABULATED_THIN_LENS` with validated wavelength/angle domain checks | Verified example |
| **Cavities, PICs, Coatings, AO & Lab** | `Finesse 3`, `Meep`, `Tidy3D`, `gdsfactory`, `SAX`, `TorchOptics`, `Chromatix`, `tmm`, `PyMoosh`, `HCIPy`, `MCX`, `PyVISA` | Standard exports & conventions documented in [INTEROP.md](docs/engine/INTEROP.md) | Guidance catalog |

```bash
os-optics interop list
os-optics interop show quantum-photonics
```

## Ecosystem: `os-optics` + `os-optics-kit`

We intentionally keep the **engine** and the **community toolkit** in two focused MIT repositories:

- **`os-optics` (this repo)** — Minimal dependencies (`numpy`, `scipy`, `pydantic`, `shapely`), strict physical determinism, stateless parallelism, fast CI, and clean Python/CLI APIs.
- **[`os-optics-kit`](https://github.com/JunLin615/os-optics-kit)** — Builds on `os-optics` with:
  - **18 engine-reproducible optical cases** (Sonnar telephoto, aspheric f/3.5, MWIR 3–5 µm imager, beam expander, polarization control, Hong-Ou-Mandel quantum optics, metasurface interop, flat-top shaping, and more).
  - **18 vendor-neutral Agent Skills** (`os-optics-kit skills install --dest ...`) ready for DeepSeek Harness, Claude Code, Codex, WorkBuddy, and other agent hosts.
  - **Parallel studies & visualization**: resumable multi-worker parameter sweeps (`--workers N`), sensitivity ranking, Monte Carlo tolerance, live read-only viewer (`os-optics-kit view`), publication plots, ISO 12233 e-SFR, and slide-ready 3D renders/animations.

```bash
python -m pip install os-optics-kit
os-optics-kit cases list
os-optics-kit skills list
```

## Documentation

| Guide | English | 简体中文 |
| --- | --- | --- |
| Project website | [opensci-optics.com](https://opensci-optics.com/en/) | [opensci-optics.com](https://opensci-optics.com/) |
| Agent entry guide | [AGENT_GUIDE.md](AGENT_GUIDE.md) | — |
| Installation and upgrades | [Read](docs/en/installation.md) | [阅读](docs/zh-CN/installation.md) |
| Your first optical design | [Read](docs/en/getting-started.md) | [阅读](docs/zh-CN/getting-started.md) |
| Catalog and reproducible projects | [Read](docs/en/catalog.md) | [阅读](docs/zh-CN/catalog.md) |
| Results, visualization, and export | [Read](docs/en/results.md) | [阅读](docs/zh-CN/results.md) |
| External scientific tools (interop) | [INTEROP.md](docs/engine/INTEROP.md) | [外部工具联用 (kit)](https://github.com/JunLin615/os-optics-kit/blob/main/docs/zh-CN/interop.md) |
| Engine architecture & module boundaries | [ENGINE_ARCHITECTURE.md](docs/engine/ENGINE_ARCHITECTURE.md) | — |
| Physics scope & model limits | [PHYSICS_SCOPE.md](docs/engine/PHYSICS_SCOPE.md) · [KNOWN_LIMITATIONS.md](docs/engine/KNOWN_LIMITATIONS.md) | — |
| Complete engine CLI & Python reference | [docs/engine/README.md](docs/engine/README.md) | — |

## Contributing & Roadmap

Contributions and discussions are welcome in English or Chinese!

- **Questions, ideas & optical design discussions:** [GitHub Discussions](https://github.com/JunLin615/os-optics/discussions)
- **Bug reports & feature requests:** [GitHub Issues](https://github.com/JunLin615/os-optics/issues/new/choose)
- **Pull requests & local test suite:** see [CONTRIBUTING.md](CONTRIBUTING.md) and [docs/engine/DEVELOPMENT.md](docs/engine/DEVELOPMENT.md)
- **Security reports:** report privately via the repository's **Security** tab

**Key areas where community issues & PRs are especially welcome:**

- **Surface & medium extensibility** — Modularizing tracer/surface dispatch and adding new optical elements (e.g., DMD / SLM models, diffractive optical elements).
- **File format interoperability** — Additional prescription records, export formats, and STEP/CAD alignment helpers.
- **Optimization & merit-function helpers** — Native optimization and multi-agent search workflows built on `EvaluationSession`.
- **Worked optical cases & agent skills** — Contribute reproducible designs and skills directly to [os-optics-kit](https://github.com/JunLin615/os-optics-kit) (your agent can package a finished design as a PR with `os-optics-kit contrib case`).

If OS Optics helps your research, engineering, or agent workflow, please consider **starring the repository** — it helps more optical engineers and open-source contributors discover the project.

## License

This repository — engine source, tests, documentation, and scripts — is licensed under the **[MIT License](LICENSE)** (covering `os-optics` releases from 0.14.0 onward; releases 0.13.0 and earlier on PyPI remain under the licence they were published under). The OpenSci and OS Optics names, as well as the brand logos in `assets/`, are trademarks excluded from the MIT License ([assets/LICENSE](assets/LICENSE), [TRADEMARK.md](TRADEMARK.md)). [os-optics-kit](https://github.com/JunLin615/os-optics-kit) is also under the MIT License. Bundled third-party components retain their own licenses (three.js: `src/os_optics/viewer/assets/THREE_LICENSE.txt`).
