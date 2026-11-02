# Agent guide

OS Optics simulates optical designs locally: PyPI `os-optics`, command `os-optics`,
`import os_optics`. Tools, cases and skills are in `os-optics-kit`. Human guides:
[English](docs/en/README.md) · [简体中文](docs/zh-CN/README.md).

## Install and check

```bash
python -m pip install "os-optics[protected]==0.12.0"
os-optics doctor
os-optics catalog sync
```

Python 3.11+. This guide describes 0.12.0, recorded in [engine-baseline.json](engine-baseline.json).
`os-optics <command> --help` and `os-optics capabilities` describe every command.

## Design, run, read

```bash
os-optics project example --kind beam_expander > project.json
os-optics project check project.json
os-optics project run project.json --out-dir run
os-optics results targets run
os-optics view run --no-browser --snapshot shots
```

Edit with `os-optics project update project.json --ops ops.json -o revised.json`: all
operations and the check pass, or nothing is written. `results freshness RUN --project P`
says whether a saved run matches an edited Project. `results describe` and `export` read
the scientific data without rerunning. Details: [results](docs/en/results.md).

## Tools, cases and skills

```bash
python -m pip install os-optics-kit
os-optics-kit skills list
os-optics-kit skills install --dest DIR
os-optics-kit cases list
```

`skills list` prints the skill index as compact JSON; each tool (summary, edit, sweep,
compare, plot, report, sensitivity, tolerance, candidates, measurements) has a `SKILL.md`,
plus skills for running and contributing cases. Source:
[JunLin615/os-optics-kit](https://github.com/JunLin615/os-optics-kit).

## Interpret before deciding

Read computation completion, quantity validity, optional target assessment and
freshness separately. Target assessments are `MET`, `UNMET`, `INDETERMINATE` or
`NOT_APPLICABLE`. Retain bounds and scope diagnostics: a bounded quantity is an
interval, and an unrelated unavailable quantity does not invalidate every result.
No configured target means no requirement was assessed. `project run` exits with the
run status; `results targets` exits 0 whenever it answers, including an unmet target.

Saved results retain their identities after edits. Freshness reasons identify
physical, target or computation-budget changes. Inspect older results as historical
evidence and rerun when the task needs the edited design. Hash checks establish
artifact consistency; analytical or experimental checks establish other claims.
Purchasing eligibility is reported by the commercial operation separately.

## Inputs, limits and recovery

Establish wavelength (nm), power (W), Gaussian 1/e² intensity **radius** and position
(mm), object IDs, poses and the customer's actual target bounds. Coordinates are
right-handed; quaternions are `[w, x, y, z]`. Discover local axes before translating
or rotating. Example geometry is a starting point to adapt.

The Project's computation budget (`set_compute_budget`) limits paths and trace steps.
Inspect limited or partial results before increasing a limit.

Invalid edits are refused atomically. Repeating absolute operations changes nothing;
use `--expect-hash` so a changed Project is refused with its current hash. After an
interrupted response, inspect the saved Project and its hash before retrying a
relative `transform`. Report the command, versions, units, selected assessments,
freshness and artifact paths.
