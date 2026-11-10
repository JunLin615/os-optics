# User guide

[Home](../../README.md) · English · [简体中文](../zh-CN/README.md)

Start with a working optical design, then explore its results and adapt it to your experiment.

1. [Install OS Optics](installation.md) — Python, virtual environments, uv, and upgrades.
2. [Run your first design](getting-started.md) — generate a project, simulate it, and open the 3D view.
3. [Work with the catalog](catalog.md) — find optics, manage releases, and reproduce a project.
4. [Read and export results](results.md) — validation, exit codes, beam data, image-quality and ghost analyses, visualization, and CSV exports.
5. [Continue to hardware](hardware.md) — component selection, assembly preview, and ordering.
6. [Run design studies and workflows](https://github.com/JunLin615/os-optics-kit/blob/main/docs/en/workflows.md) (os-optics-kit) — edit by ID, parameter sweeps, run comparison, plots, reports, sensitivity and tolerance studies, and three parameterized optical workflows.
7. [Analyze measurements](https://github.com/JunLin615/os-optics-kit/blob/main/docs/en/measurements.md) (os-optics-kit) — background subtraction, reference normalization, linear fits and residual plots.
8. [Use external scientific tools](https://github.com/JunLin615/os-optics-kit/blob/main/docs/en/interop.md) (os-optics-kit) — optimization, diffraction, quantum photonics and metasurface components with SciPy, Optuna, SALib, LightPipes, prysm, POPPY, Perceval, Piquasso and grcwa.

## Cases, tools and skills

[os-optics-kit](https://github.com/JunLin615/os-optics-kit) holds worked cases, reusable tools and agent skills built on the engine. Install it with `python -m pip install os-optics-kit`; `os-optics-kit cases list` lists the cases.

| Case | Guide |
| --- | --- |
| [Beam expander](https://github.com/JunLin615/os-optics-kit/tree/main/cases/beam-expander) | [Your first design](getting-started.md) |
| [Ideal-lens focus](https://github.com/JunLin615/os-optics-kit/tree/main/cases/ideal-lens-focus) | [Results](results.md) |
| [Expansion and focus](https://github.com/JunLin615/os-optics-kit/tree/main/cases/expansion-focus), [polarization control](https://github.com/JunLin615/os-optics-kit/tree/main/cases/polarization-control), [mirror alignment](https://github.com/JunLin615/os-optics-kit/tree/main/cases/mirror-alignment) | [Design studies and workflows](https://github.com/JunLin615/os-optics-kit/blob/main/docs/en/workflows.md) |
| [Detector measurements](https://github.com/JunLin615/os-optics-kit/tree/main/cases/detector-measurements) | [Measurement analysis](https://github.com/JunLin615/os-optics-kit/blob/main/docs/en/measurements.md) |
| [Optimization](https://github.com/JunLin615/os-optics-kit/tree/main/cases/interop-optimization), [diffraction](https://github.com/JunLin615/os-optics-kit/tree/main/cases/interop-diffraction), [quantum photonics](https://github.com/JunLin615/os-optics-kit/tree/main/cases/interop-quantum), [metasurface](https://github.com/JunLin615/os-optics-kit/tree/main/cases/interop-metasurface) | [External tools](https://github.com/JunLin615/os-optics-kit/blob/main/docs/en/interop.md) |

Each case states whether it uses the optical catalog and the engine and catalog releases it was verified with. Install os-optics-kit into the same Python environment as os-optics: `python -m pip install os-optics-kit`.

These guides describe **os-optics 0.13.0**; see [version matching](installation.md#match-the-documentation-to-the-installed-version) for the matching installation. Commands expose their options through `--help`; Python APIs are documented in the installed package and the [PyPI reference](https://pypi.org/project/os-optics/).

[OpenSci website](https://opensci-optics.com/) · [Package releases](https://pypi.org/project/os-optics/#history) · [Ask a question](https://github.com/JunLin615/os-optics/issues/new/choose)
