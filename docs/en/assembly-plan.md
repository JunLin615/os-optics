# Local assembly plans

[User guide](README.md) · English · [简体中文](../zh-CN/assembly-plan.md)

A local assembly plan says how your Project is built on a breadboard: which mount holds each optic, on which post, in which post holder, fixed on which board hole, who supplies each part and what you buy. `os-optics assembly plan` derives, edits, checks and draws it on your computer. It needs no OpenSci Account token and makes no network connection. Its inputs are the Project, the [public optical catalog](catalog.md) and the [public mechanical catalog](mechanical-catalog.md).

The plan is checked whenever it changes. Every revision is kept, so you can reopen any of them, compare them and go back to one. When it is ready, the purchase BOM it writes is the file `os-optics order quote` sends ([Continue to hardware](hardware.md)).

## Derive a plan

```bash
os-optics catalog sync                  # or: os-optics catalog import FILE (optical and mechanical releases)
os-optics project example --kind focus > project.json
os-optics assembly plan derive project.json --out plan
```

`derive` places each catalog optic in a mount, post and holder at its design height and turns the holder towards the nearest free hole of the smallest breadboard that holds the layout. A light source and any other part that is not an OpenSci product stay at their design positions as your own components, with space reserved around them. When no hardware fits, the plan is still written: the finding says what did not fit and why.

Options: `--max-board-size-mm X Y` limits the board, `--beam-height-mm H` sets the height of the design plane, `--customer-table X Y` places the plan on your own optical table, `--customer-owned INSTANCE_ID` marks an optic you already have, `--run RUN_DIR` binds an optics run, and `--from PLAN` derives again from an earlier plan, keeping its item ids, supply, purchase choices and pinned hardware. `--mechanical FILE` (repeatable) or `OS_OPTICS_MECHANICAL_CATALOG` uses mechanical documents you name, including your own `USER-MECH-*` hardware.

The command prints a summary: the plan id, the revision, its `content_digest`, the status and the findings that need attention, and the next commands to run.

## The plan directory

| Entry | Content |
| --- | --- |
| `revisions/r0001.json` ... | every revision, never changed once written |
| `plan.json` | a copy of the newest revision (the head) |
| `projects/<hash>.json` | every Project a revision is bound to |
| `mechanical/<sha256>.json` | copies of your own mechanical documents, so the plan reopens offline |
| `runs/<hash>.json` | the Local Validation record of each bound optics run |
| `history.jsonl` | the journal: one line per derive, edit or rebind, with its op id |

`os-optics assembly plan show PLAN` checks the whole directory first and exits 66 when a file was changed or is missing. `--items` lists every item with its supply, purchase, chain, board hole and adjustment ranges; `--revision N` shows an earlier revision; `--json` prints the revision document. `history PLAN` prints the journal.

Every item has a stable id (`opt-1`, `mnt-1`, `pst-1`, `hld-1`, `brd-1`, `cmp-1`, `env-1`). An id is never reused: an edit that replaces a part keeps its id, and a removed part's id is retired.

## Check a plan

Each revision records the check made when it was written. `os-optics assembly plan check PLAN` checks it again with this Engine and the catalogs you have now; nothing is written (`--out FILE` saves the result). The check covers:

- the interfaces of each chain: the optic in its mount (shape, size, thickness range, clear aperture for a transmissive optic), the mount's thread on the post, the post diameter in the holder, the insertion depth and the height it closes, the holder's thread in the board;
- the layout: every holder on a board hole within its fork slot, no hole shared, nothing inside a declared envelope;
- the layout and collision check of the plan's preview package: interference between parts, parts outside the board, the beam axis blocked by hardware;
- supply and catalogs: parts nobody supplies, optics OpenSci supplies without a mount, identities missing from the catalogs in use;
- optics: whether the optics run bound to the plan is still current for its Project.

A finding names its part or the pair of parts it is about, the interface when it is about one, the check that found it, and its basis. Its severity sets the status:

| Status | Meaning | Exit code |
| --- | --- | --- |
| `READY` | no finding but information | 0 |
| `READY_WITH_WARNINGS` | warnings only | 0 |
| `INCOMPLETE` | a check could not be evaluated (`UNKNOWN`), e.g. a missing fact or your own hardware the preview cannot show | 2 |
| `HAS_ERRORS` | at least one error | 1 |

A finding whose part rests on a conservative profile of the mechanical catalog has basis `CONSERVATIVE_PROFILE`: the real part may fit better than the profile says.

## Edit a plan

`os-optics assembly plan edit PLAN --op JSON` (repeatable) or `--ops OPS.json` applies operations as one new revision. Either all of them are applied or none is.

| Operation | What it does |
| --- | --- |
| `move_optic` | moves an optic (`delta_design_mm` or `position_design_mm`, optional `quaternion_wxyz`) in the Project; its chain follows and the holder is fixed again when its slot no longer reaches the hole (`refix`) |
| `adjust` | moves an optic within one of its declared ranges (`range_id`, `delta`) |
| `set_hardware` | replaces a mount, post or holder (`opensci_sku`, OS-MECH or USER-MECH) |
| `set_fixation` | fixes a holder on another board hole (`hole: [i, j]`) |
| `set_board` | replaces the board (`opensci_sku` or `customer_table`), `recentre` to centre the layout on it |
| `set_supply` | `OPENSCI` or `CUSTOMER_OWNED` for an item |
| `set_purchase` | whether an item is bought, its quantity and its spare units |
| `set_mounting` | `ANCHOR` (you mount the optic) or `CHAIN` (OpenSci hardware) |
| `rederive_chain` | selects an optic's hardware again (`keep` names hardware to keep) |
| `pin` | keeps an item's hardware when the plan is derived again |
| `declare_envelope` / `remove_envelope` | reserves (or frees) space for a part of yours known only by its size: a `BOX` or `CYLINDER` in lab coordinates |
| `rebind_catalogs` | binds the plan to the catalogs in use now |
| `revert_to` | makes an earlier revision's decisions the new head |

```bash
os-optics assembly plan edit plan --op '{"op": "set_hardware", "item_id": "mnt-2", "opensci_sku": "OS-MECH-..."}'
os-optics assembly plan edit plan --op '{"op": "move_optic", "item_id": "opt-2", "delta_design_mm": [-25, 0, 0]}' --write-project moved.json
```

`--require-check` refuses an edit that adds an error or an unknown finding. `--op-id ID` names the edit: sending the same id with the same operations again answers from the journal and writes nothing; the same id with other operations is refused. `--expect-base DIGEST` refuses the edit when the head is no longer the revision you read. A refused edit writes nothing and exits 65.

Moving an optic changes the Project, so the optics results bound to the plan become stale. The edit reports `project_changed` and `run_freshness`, and the status says `optics_results: STALE`. `os-optics assembly plan run-optics PLAN --out RUN_DIR` runs the Project on your computer and binds the new run; the results are then `CURRENT` again.

## Preview, view and draw

```bash
os-optics assembly plan preview plan --out preview.zip      # the Assembly Preview Package of the plan
os-optics view plan                                         # the 3D assembly in your browser, coloured by the checks
os-optics view plan --finding f-1a2b3c4d5e6f                # frames the parts of one finding
os-optics assembly plan draw plan --out drawing             # the assembly drawing of your design (SVG and PDF)
```

The preview package (manifest `3.1`) is bound to the plan revision and its design BOM. A plan that holds your own `USER-MECH` hardware has no preview package yet: the plan checks still run on it. `draw` writes the Detailed drawing (schema `4.2`) when the layout check passes, refuses a plan with plan-level errors or unknown findings, and with `--diagnostic` writes a diagnostic sheet for a layout check that did not pass (exit 2). Drawings are never uploaded. `--locale zh-CN` draws in Simplified Chinese.

## Bills of materials

```bash
os-optics assembly plan bom plan --design                         # every item of the plan
os-optics assembly plan bom plan --purchase --out bom.json        # what you buy
os-optics order quote bom.json
```

The design BOM lists every item, in lines `D1`, `D2`, ...: OpenSci optics, then OpenSci hardware by role, then your own hardware, your optical table and your own components, with who supplies each. The purchase BOM is exactly the request body of a quote: one `PLAN` line per part number you buy and one `SPARE` line per part number with spare units. Items you own or did not select are left out and listed in the report (`--report FILE`). `--schema-version 1` writes the earlier body for a service that reads only version 1 (spares are added to the quantities). A plan that buys nothing has no purchase BOM (exit 65, `PURCHASE_BOM_EMPTY`).

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | done; the plan is `READY` or `READY_WITH_WARNINGS` (`draw`: the Detailed drawing) |
| 1 | done; the plan `HAS_ERRORS` (the revision or output is written) |
| 2 | done; the plan is `INCOMPLETE` (`draw`: a diagnostic sheet) |
| 64 | usage error, or a file that cannot be read or written |
| 65 | refused: an invalid Project or operation, no mechanical catalog, a refused drawing; nothing was written |
| 66 | the plan directory is inconsistent |
| 70 | an internal error of os-optics |

## Python and JSON

```python
from os_optics import assembly_plan as ap

result = ap.derive_plan("project.json", out_dir="plan")
plan = ap.open_plan("plan")
plan.edit([{"op": "set_purchase", "item_id": "pst-1", "selected": True, "spare_units": 1}])
body, report = ap.purchase_bom(plan)
```

`ap.check_plan`, `ap.plan_preview`, `ap.plan_drawing` and `ap.design_bom` take a plan directory or an open plan; `ap.apply_plan_ops` edits a revision in memory. A host that is not a Python program uses the JSON API methods `derive_assembly_plan`, `check_assembly_plan`, `apply_assembly_plan_ops`, `assembly_plan_preview`, `assembly_plan_drawing`, `assembly_plan_bom` and `mechanical_catalog`. `os-optics schema assembly-plan` (also `assembly-plan-ops`, `design-bom`, `purchase-bom`, `bom-review`, `public-mechanical-catalog`) prints the JSON Schema of each document.
