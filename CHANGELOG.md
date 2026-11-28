# Changelog

Earlier releases are summarized in `src/os_optics/_version.py` and on
[PyPI](https://pypi.org/project/os-optics/#history).

## Unreleased (0.15.0, not published)

### Added

- **Local assembly plans** (`os_optics.assembly_plan`, `os-optics assembly plan derive | show | check | edit | history |
  preview | draw | bom | run-optics`, schema `os-optics.assembly_plan` 1.0). A plan says which mount, post, post holder
  and board hole hold each optic, who supplies each part and what is bought. It is derived, edited, checked, previewed
  and drawn on this computer, with no token and no network. A plan directory keeps every revision, the Projects they
  bind and a journal with op ids (`--op-id`, `--expect-base`, `--require-check`). Item ids stay stable across revisions.
  A candidate with findings is still written.
- **Public mechanical catalog** (`opensci.public_mechanical_catalog` 1.0). It covers posts, post holders, mounts and
  breadboards (`OS-MECH-*`) with their engineering facts and the basis of each fact, as one conservative profile per
  identity. `catalog sync` also fetches it when the service offers it (verified by SHA-256; its absence is not an
  error). Also new: `catalog mechanical list | show | status`, and `--mechanical FILE` / `OS_OPTICS_MECHANICAL_CATALOG`
  for user-provided documents, including your own hardware (`USER-MECH-*`).
- **Assembly kernel** (`os_optics.assembly_plan.kernel`): mount, post and holder selection, beam-height lift, layout
  search and diagnosis over the public mechanical catalog.
  - Ties are broken by delivered degrees of freedom, surplus, evidence basis and identity.
  - A refusal becomes a finding on a fallback candidate (`DERIVE_FELL_BACK` with the refusal's finding).
  - Work budgets end in `SEARCH_BUDGET_EXHAUSTED`, never a hang.
- **Plan checks**, plan-level and on every edit:
  - interface: shape, size, thickness range, transmissive aperture, thread, post diameter, board thread, insertion
    depth, height closure;
  - DOF delivery, fixation holes and declared envelopes (`declare_envelope`, `RESERVED_SPACE_INTRUSION`);
  - supply and optics freshness (an optic move marks the bound run `STALE`; `run-optics` binds a new one);
  - the unchanged local layout check of the plan's preview package.
- **Local outputs of a plan:**
  - the Assembly Preview Package with a `LOCAL_PLAN` binding (manifest 3.1, local usage terms);
  - Assembly Detail 3.2;
  - the Detailed drawing 4.2, titled "Assembly drawing of your design", with a parts legend from the design BOM (who
    supplies each row);
  - `assembly draw PACKAGE --design-bom FILE`.
  Existing 3.0 / 3.1 / 4.1 packages still verify and draw byte-identically.
- **The viewer opens a plan without a token** (`os-optics view PLAN_DIR` or a plan package; `--finding ID`): parts are
  coloured by their check status, and finding pairs are highlighted.
- **Bills of materials:**
  - the design BOM (`os-optics.design_bom` 1.0, every item, lines `D1..Dn`);
  - the purchase BOM, which is exactly the version-2 quote body (`PLAN` and `SPARE` lines; customer-owned and
    unselected items are excluded and reported). `--schema-version 1` writes the earlier body.
- **Purchase BOM quotes** (`os-optics order quote BOM.json [--confirm REVIEW.json]`, `client.quote_bom`). The service's
  line review (`ORDERABLE` / `CHANGES_REQUIRED`) gets a review file with a proposed confirmation, which is checked
  locally before it is sent. The review file names the BOM it belongs to (`bom_request_sha256`, `round`):
  `--confirm` with another BOM, a missing BOM or an unbound review is refused before anything is sent
  (`CONFIRMATION_BOM_MISMATCH`, `INPUT_FILE_UNREADABLE`, `CONFIRMATION_NOT_POSSIBLE`). A confirmation answered with
  new changes writes the next round (`BOM.review-2.json`), confirmed the same way; a later review's `submitted`
  confirmation must be the BOM with the recorded decisions applied (`earlier_decisions`; `REVIEW_INCONSISTENT`
  otherwise). `--review-out` never replaces an input file, directly or through its temporary name
  (`REVIEW_OUT_COLLISION`), and one that cannot be created is refused (`REVIEW_OUT_UNWRITABLE`), both before sending; a
  review that cannot be written after sending still prints the answer and its `resolution_id` (exit 73).
- **Account handoff without a token:** the registration and sign-in URLs (`OPENSCI_ACCOUNT_URL`), the token location
  and the resume command (`resume_argv`; `resume_command` with `resume_shells`, the shells it is quoted for -- sh, and
  on Windows PowerShell and sh with one text whenever one exists, paths written with `/`; `resume_commands` per
  shell). It keeps every option that changes the request (`--schema-version`, `--confirm`, `--review-out`,
  `--api-url`, `--json`) with absolute paths; the `next_command` of a review is printed the same way and keeps
  `--json`. The same BOM gives the same idempotency key.
- **JSON API 1.9:**
  - new methods `derive_assembly_plan`, `check_assembly_plan`, `apply_assembly_plan_ops`, `assembly_plan_preview`,
    `assembly_plan_drawing`, `assembly_plan_bom` and `mechanical_catalog`;
  - the `design_bom` option of `generate_assembly_drawing`;
  - JSON Schemas (`os-optics schema assembly-plan | assembly-plan-ops | design-bom | purchase-bom | bom-review |
    public-mechanical-catalog`).
- `scripts/local_assembly_smoke.py` runs the plan journey on an installed wheel. CI runs it on the candidate wheel.

### Changed

- The BOM-only OpenSci service retires the following. The client maps each retirement by its code and prints the local
  command that replaces it, with recovery `USE_LOCAL_PLAN` and exit 67:
  - design ordering: `resolve`, and `order create` of a design resolution (`ORDERING_BASIS_RETIRED`);
  - hosted previews and drawings, optical layouts, mounting sheets and artifact sets (`ROUTE_RETIRED`);
  - derived mechanics and other order options (`ORDER_OPTION_RETIRED`).
- Drawings are never uploaded. `assembly drawing` keeps the files on this computer, and `--no-upload` is accepted and
  has no effect.
- `BOM_CHANGES_UNCONFIRMED` (409) has the recovery `CONFIRM_BOM_CHANGES`.

### Unchanged

- Engine schema 1.12.0, numerical config 1.7.0 and every Project and Engine input hash.
- The public optical catalog schemas.
