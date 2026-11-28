# From design to hardware

[User guide](README.md) · English · [简体中文](../zh-CN/hardware.md)

[OpenSci](https://opensci-optics.com/) supplies the parts of your optical system. The engine designs and simulates the system on your computer and plans how it is mounted. It turns the plan into a purchase list (a BOM) that your OpenSci Account quotes and orders. The plan, its preview and its drawing are made on your computer, and none of them is uploaded.

## The workflow

**Design → simulate → local assembly plan → purchase BOM → quote → order → delivery**

The BOM decides what you buy:

| You buy | The BOM lists |
| --- | --- |
| Optical components only | The `OS-OPT-*` lines of your optics |
| Optics with OpenSci mechanics | The optics plus the mounts, posts, post holders and breadboard of the plan (`OS-MECH-*`) |
| Spare units | Extra lines with `"purpose": "SPARE"` |
| A build on your own optical table | The plan's parts without the breadboard line |

OpenSci checks that every line can be supplied. It does not check whether the parts fit together: the local plan does that, and so do its checks and drawing. You assemble the system yourself.

## Connect your Account

Start at the [OpenSci website](https://opensci-optics.com/). Your Account gives you the service URL and the Agent-token setup. Set `OPENSCI_API_URL` to that URL, complete the token setup shown in your Account, and check the connection:

```bash
os-optics account status
```

You can plan and write the BOM before you have an Account. If no token is on this device, `os-optics order quote` sends nothing and keeps your BOM in its file. It prints the registration and sign-in pages of your Account and says where to save the token. Then you run the command it prints: the same BOM, review and output files as absolute paths and the same `--schema-version`, quoted for your shell (PowerShell on Windows, sh elsewhere), so it sends the same request. `OPENSCI_ACCOUNT_URL` selects another Account website. Sending the same BOM again gives the same request, so a repeated command never creates a second quote.

## Get a configuration

Plan the mounting of a saved Project on your computer, then write the purchase BOM. The plan uses the [mechanical catalog](mechanical-catalog.md) and works without a token or a network connection:

```bash
os-optics assembly plan derive project.json --out plan
os-optics assembly plan preview plan --out preview.zip
os-optics view plan
os-optics assembly plan draw plan --out drawing --locale en
os-optics assembly plan bom plan --purchase --out bom.json
```

You can also write the BOM yourself. Each line has an OpenSci part number and a quantity; `line_id` and `purpose` (`PLAN` or `SPARE`) are optional:

```json
{"schema": "opensci.commercial.part_list_submission", "schema_version": "2",
 "lines": [{"opensci_sku": "OS-OPT-...", "quantity": 2},
           {"opensci_sku": "OS-MECH-...", "quantity": 2, "purpose": "SPARE"}]}
```

Quote it:

```bash
os-optics order quote bom.json
```

The quote reviews every line. The result is one of these:

- **Every line can be supplied** (`ORDERABLE`, exit 0). The output prints the prices and the next command, `os-optics order create RESOLUTION_ID`.
- **Some lines cannot be supplied as listed** (`CHANGES_REQUIRED`, exit 1). A line may not be an orderable OpenSci product, may be temporarily unavailable, or may ask for more units than one line allows. The output lists those lines and writes `bom.review.json`. That file proposes to keep the other lines and to remove the changed ones. You can edit its decisions: for example, a smaller quantity for a line above the limit, or another part number. Then send it with `os-optics order quote bom.json --confirm bom.review.json`. The quote cannot be ordered until its changes are confirmed.
  - The review file records the BOM it was written for. `--confirm` needs that same BOM: a review of another BOM, or a BOM file that does not exist, is refused before anything is sent (`CONFIRMATION_BOM_MISMATCH`, `INPUT_FILE_UNREADABLE`).
  - If supply changes again before you confirm, the answer is a new review, `bom.review-2.json`. Confirm it the same way, with the same BOM: `os-optics order quote bom.json --confirm bom.review-2.json`.
  - The review file is never written over the BOM or the file you confirm (`REVIEW_OUT_COLLISION`; nothing is sent). A `--review-out` in a folder that does not exist, or that cannot be created, is refused too (`REVIEW_OUT_UNWRITABLE`; nothing is sent).
- **No line can be supplied** (`BOM_NOT_ORDERABLE`, exit 67). The review file is written, nothing is stored, and you change the BOM before quoting again.

`--schema-version 1` sends the earlier part-list form to a service that does not quote BOMs yet. That form has no `purpose` and no confirmation.

## Place the order

Choose the shipping address in your OpenSci Account. Review the lines, the total and the agreement of the quote, then create the order:

```bash
os-optics order create RESOLUTION_ID
```

Follow the payment instructions in the order. After the transfer, report it with `os-optics order report-transfer ORDER_ID`. OpenSci confirms receipt and ships the parts.

The assembly drawing comes from your local plan (`os-optics assembly plan draw`), in English or, with `--locale zh-CN`, in Chinese, as SVG and PDF. It is never uploaded.

OpenSci no longer orders designs, selects mounts, or delivers hosted previews, drawings, layouts and mounting sheets. The commands `resolve`, `assembly preview` and `assembly drawing` remain for an earlier service. Against the current service they print the local command that replaces them.

Explore the platform and available products at **[opensci-optics.com](https://opensci-optics.com/)**.
