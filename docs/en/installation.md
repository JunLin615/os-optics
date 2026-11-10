# Installation

[User guide](README.md) · English · [简体中文](../zh-CN/installation.md)

OS Optics runs on Windows, macOS, and Linux with **Python 3.11 or newer**. Choose pip for Python scripts and notebooks, or uv for a standalone command-line tool.

## Match the documentation to the installed version

This documentation describes os-optics **0.13.0**, the release recorded in
[engine-baseline.json](../../engine-baseline.json). Install that exact version in a fresh
environment:

```bash
python -m pip install "os-optics[protected]==0.13.0"
```

`os-optics doctor` reports the installed version. Each case in
[os-optics-kit](https://github.com/JunLin615/os-optics-kit) states the engine release it
was verified with.

## Python environment

Create a virtual environment:

```bash
python -m venv .venv
```

Activate it on macOS or Linux:

```bash
source .venv/bin/activate
```

On Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

Install and check the engine:

```bash
python -m pip install "os-optics[protected]"
os-optics doctor
```

You can also run the CLI as `python -m os_optics`. If PowerShell activation is unavailable, use `.venv\Scripts\python.exe -m pip install "os-optics[protected]"` and `.venv\Scripts\python.exe -m os_optics doctor` directly.

The `protected` extra adds support for opening assembly previews and assembly details in the hardware workflow. For optical design and simulation alone, `python -m pip install os-optics` is sufficient.

## Standalone CLI with uv

With [uv](https://docs.astral.sh/uv/) installed:

```bash
uv tool install "os-optics[protected]"
os-optics doctor
```

If the command is not on your PATH, run `uv tool update-shell` and open a new terminal. To run a command without a persistent tool installation:

```bash
uvx --from "os-optics[protected]" os-optics doctor
```

## Prepare the catalog

```bash
os-optics catalog sync
os-optics catalog status
```

The catalog downloads into a local store. Later simulations use stored releases, including when working offline. `doctor` checks local installation readiness; `catalog sync` retrieves the current catalog from OpenSci.

## Upgrade

For a pip installation:

```bash
python -m pip install --upgrade "os-optics[protected]"
```

For a uv tool installation:

```bash
uv tool upgrade os-optics
```

Up to 0.9.0 the package was published as `opensci-engine` (command `opensci-engine`,
`import opensci_engine`). An installation under that name takes the same
`python -m pip install --upgrade "os-optics[protected]"`; the two packages share no files.
Then use the `os-optics` command and `import os_optics`. A `uv tool` installation under the
earlier name installs the new one with `uv tool install "os-optics[protected]"`, then uses `os-optics`.

Projects need no change for 0.13.0. Runs made with 0.12.0 remain readable: they report
`HISTORICAL` in `os-optics results freshness` (reason `ENGINE_CHANGED`), and
`os-optics project verify` reports their directories `INCONSISTENT`, because 0.13.0 records
additional numerical settings in every engine input. Run each Project again with 0.13.0 for
a current run that verifies. Scripts that read the exit code of `project run` follow the
[exit codes](results.md#exit-codes) of 0.13.0.

Run `os-optics doctor` to see the installed version. To pin the documented version, install `"os-optics[protected]==0.13.0"`.

Tools, cases and agent skills are a separate package. `python -m pip install os-optics-kit` installs them into the same environment; the package declares the os-optics releases it supports.

## Common setup questions

| Symptom | Next step |
| --- | --- |
| Python version error during installation | Check `python --version`; use Python 3.11+ or the uv route |
| `os-optics` command not found | Activate the environment or use `python -m os_optics` |
| `DOWNLOAD_ON_FIRST_USE` in doctor | Run `os-optics catalog sync` or start a catalog-based design |
| Catalog download fails | Check connectivity with `os-optics catalog check`; offline catalog import is described in the [catalog guide](catalog.md) |
| Service requests a newer engine | Upgrade, update the project's catalog, and rerun the design before submitting it |
| `project verify` reports a 0.12.0 run `INCONSISTENT` | Run the Project again with 0.13.0 |

Next: [Run your first optical design](getting-started.md).
