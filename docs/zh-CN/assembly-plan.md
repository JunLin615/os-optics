# 本地装配方案

[用户指南](README.md) · [English](../en/assembly-plan.md) · 简体中文

本地装配方案说明你的 Project 如何搭建在面包板上：每个光学元件装在哪个镜架里、立在哪根立杆上、插在哪个杆座里、固定在哪个面包板孔上，每个零件由谁提供，以及你要购买什么。`os-optics assembly plan` 在你的电脑上推导、编辑、检查并绘制方案，不需要 OpenSci 账户令牌，也不建立任何网络连接。它的输入是 Project、[公开光学目录](catalog.md)和[公开机械目录](mechanical-catalog.md)。

方案每次改变都会重新检查。每个修订版都会保留，因此你可以重新打开、比较并回到任何一个修订版。方案就绪后，它写出的采购 BOM 正是 `os-optics order quote` 发送的文件（见[继续到硬件](hardware.md)）。

## 推导方案

```bash
os-optics catalog sync                  # 或：os-optics catalog import FILE（光学与机械目录版本）
os-optics project example --kind focus > project.json
os-optics assembly plan derive project.json --out plan
```

`derive` 在设计高度为每个目录光学元件选择镜架、立杆和杆座，并把杆座转向能容纳整个布局的最小面包板上最近的空闲孔。光源以及其他非 OpenSci 产品的零件作为你自己的部件留在设计位置，并在其周围预留空间。没有合适的硬件时仍然写出方案：发现项说明哪里不合适以及原因。

选项：`--max-board-size-mm X Y` 限制面包板尺寸，`--beam-height-mm H` 设定设计平面的高度，`--customer-table X Y` 把方案放到你自己的光学平台上，`--customer-owned INSTANCE_ID` 标记你已有的光学元件，`--run RUN_DIR` 绑定一次光学运行，`--from PLAN` 从已有方案重新推导，保留其条目 ID、供应方、采购选择和已固定的硬件。`--mechanical FILE`（可重复）或 `OS_OPTICS_MECHANICAL_CATALOG` 使用你指定的机械文档，包括你自己的 `USER-MECH-*` 硬件。

命令打印摘要：方案 ID、修订版号、其 `content_digest`、状态、需要处理的发现项以及接下来要运行的命令。

## 方案目录

| 条目 | 内容 |
| --- | --- |
| `revisions/r0001.json` ... | 每个修订版，写入后不再改变 |
| `plan.json` | 最新修订版（头版本）的副本 |
| `projects/<hash>.json` | 修订版所绑定的每个 Project |
| `mechanical/<sha256>.json` | 你自己的机械文档的副本，使方案可离线重新打开 |
| `runs/<hash>.json` | 每次绑定的光学运行的本地验证记录 |
| `history.jsonl` | 日志：每次推导、编辑或重新绑定一行，带操作 ID |

`os-optics assembly plan show PLAN` 先检查整个目录，文件被修改或缺失时以 66 退出。`--items` 列出每个条目的供应方、采购、链、面包板孔和调整范围；`--revision N` 显示较早的修订版；`--json` 打印修订版文档。`history PLAN` 打印日志。

每个条目都有稳定的 ID（`opt-1`、`mnt-1`、`pst-1`、`hld-1`、`brd-1`、`cmp-1`、`env-1`）。ID 不会被重复使用：替换零件的编辑保留其 ID，移除的零件的 ID 被注销。

## 检查方案

每个修订版都记录写入时所做的检查。`os-optics assembly plan check PLAN` 用当前的 Engine 和你现有的目录重新检查，不写入任何内容（`--out FILE` 保存结果）。检查包括：

- 每条链的接口：光学元件在镜架中（形状、尺寸、厚度范围、透射元件的通光孔径）、镜架螺纹与立杆、立杆直径与杆座、插入深度及其闭合的高度、杆座螺纹与面包板；
- 布局：每个杆座位于其叉槽范围内的面包板孔上，孔不共用，声明的包络内没有任何零件；
- 方案预览包的布局与碰撞检查：零件之间的干涉、零件超出面包板、光束轴被硬件遮挡；
- 供应与目录：无人提供的零件、OpenSci 不带镜架提供的光学元件、当前目录中缺失的标识；
- 光学：绑定到方案的光学运行对其 Project 是否仍然有效。

发现项指明它涉及的零件或零件对、涉及的接口、发现它的检查及其依据。严重程度决定状态：

| 状态 | 含义 | 退出码 |
| --- | --- | --- |
| `READY` | 除信息外没有发现项 | 0 |
| `READY_WITH_WARNINGS` | 只有警告 | 0 |
| `INCOMPLETE` | 某项检查无法评估（`UNKNOWN`），例如缺少参数，或预览无法显示你自己的硬件 | 2 |
| `HAS_ERRORS` | 至少一个错误 | 1 |

如果发现项涉及的零件依据机械目录中的保守配置，其依据为 `CONSERVATIVE_PROFILE`：实际零件可能比配置所示更合适。

## 编辑方案

`os-optics assembly plan edit PLAN --op JSON`（可重复）或 `--ops OPS.json` 把操作作为一个新修订版应用：要么全部应用，要么全不应用。

| 操作 | 作用 |
| --- | --- |
| `move_optic` | 在 Project 中移动光学元件（`delta_design_mm` 或 `position_design_mm`，可选 `quaternion_wxyz`）；其链随之移动，叉槽够不到原孔时杆座重新固定（`refix`） |
| `adjust` | 在声明的某个范围内移动光学元件（`range_id`、`delta`） |
| `set_hardware` | 替换镜架、立杆或杆座（`opensci_sku`，OS-MECH 或 USER-MECH） |
| `set_fixation` | 把杆座固定到另一个面包板孔（`hole: [i, j]`） |
| `set_board` | 替换面包板（`opensci_sku` 或 `customer_table`），`recentre` 使布局居中 |
| `set_supply` | 条目的供应方：`OPENSCI` 或 `CUSTOMER_OWNED` |
| `set_purchase` | 是否购买条目、数量及备件数 |
| `set_mounting` | `ANCHOR`（你自己安装光学元件）或 `CHAIN`（OpenSci 硬件） |
| `rederive_chain` | 为光学元件重新选择硬件（`keep` 指定保留的硬件） |
| `pin` | 重新推导方案时保留条目的硬件 |
| `declare_envelope` / `remove_envelope` | 为只知道尺寸的自有零件预留（或释放）空间：实验室坐标中的 `BOX` 或 `CYLINDER` |
| `rebind_catalogs` | 把方案绑定到当前使用的目录 |
| `revert_to` | 把较早修订版的决定作为新的头版本 |

```bash
os-optics assembly plan edit plan --op '{"op": "set_hardware", "item_id": "mnt-2", "opensci_sku": "OS-MECH-..."}'
os-optics assembly plan edit plan --op '{"op": "move_optic", "item_id": "opt-2", "delta_design_mm": [-25, 0, 0]}' --write-project moved.json
```

`--require-check` 拒绝会新增错误或未知发现项的编辑。`--op-id ID` 为编辑命名：用同一 ID 再次发送相同操作时直接从日志返回，不写入任何内容；同一 ID 配不同操作则被拒绝。`--expect-base DIGEST` 在头版本已不是你读到的修订版时拒绝编辑。被拒绝的编辑不写入任何内容，以 65 退出。

移动光学元件会改变 Project，因此绑定到方案的光学结果失效。编辑报告 `project_changed` 和 `run_freshness`，状态显示 `optics_results: STALE`。`os-optics assembly plan run-optics PLAN --out RUN_DIR` 在你的电脑上运行 Project 并绑定新的运行，结果重新变为 `CURRENT`。

## 预览、查看与绘图

```bash
os-optics assembly plan preview plan --out preview.zip      # 方案的装配预览包
os-optics view plan                                         # 在浏览器中按检查结果着色的 3D 装配
os-optics view plan --finding f-1a2b3c4d5e6f                # 聚焦某个发现项的零件
os-optics assembly plan draw plan --out drawing             # 你的设计的装配图（SVG 和 PDF）
```

预览包（清单 `3.1`）绑定到方案修订版及其设计 BOM。包含你自己的 `USER-MECH` 硬件的方案暂时没有预览包，但方案检查仍然运行。布局检查通过时 `draw` 写出详细装配图（schema `4.2`）；方案有方案级错误或未知发现项时拒绝绘图；加 `--diagnostic` 时对未通过的布局检查写出诊断图（退出码 2）。图纸从不上传。`--locale zh-CN` 以简体中文绘图。

## 物料清单

```bash
os-optics assembly plan bom plan --design                         # 方案的每个条目
os-optics assembly plan bom plan --purchase --out bom.json        # 你要购买的内容
os-optics order quote bom.json
```

设计 BOM 按行 `D1`、`D2`……列出每个条目：先是 OpenSci 光学元件，然后按角色列出 OpenSci 硬件，再是你自己的硬件、你的光学平台和你自己的部件，并注明每项的供应方。采购 BOM 正是报价请求的正文：每个购买的料号一行 `PLAN`，每个有备件的料号一行 `SPARE`。你自有或未选择的条目不计入，并列在报告中（`--report FILE`）。`--schema-version 1` 为只读取版本 1 的服务写出较早的正文（备件计入数量）。不购买任何东西的方案没有采购 BOM（退出码 65，`PURCHASE_BOM_EMPTY`）。

## 退出码

| 退出码 | 含义 |
| --- | --- |
| 0 | 完成；方案为 `READY` 或 `READY_WITH_WARNINGS`（`draw`：详细装配图） |
| 1 | 完成；方案 `HAS_ERRORS`（修订版或输出已写入） |
| 2 | 完成；方案 `INCOMPLETE`（`draw`：诊断图） |
| 64 | 用法错误，或文件无法读取或写入 |
| 65 | 被拒绝：无效的 Project 或操作、没有机械目录、绘图被拒绝；未写入任何内容 |
| 66 | 方案目录不一致 |
| 70 | os-optics 内部错误 |

## Python 与 JSON

```python
from os_optics import assembly_plan as ap

result = ap.derive_plan("project.json", out_dir="plan")
plan = ap.open_plan("plan")
plan.edit([{"op": "set_purchase", "item_id": "pst-1", "selected": True, "spare_units": 1}])
body, report = ap.purchase_bom(plan)
```

`ap.check_plan`、`ap.plan_preview`、`ap.plan_drawing` 和 `ap.design_bom` 接受方案目录或已打开的方案；`ap.apply_plan_ops` 在内存中编辑修订版。非 Python 程序的宿主使用 JSON API 方法 `derive_assembly_plan`、`check_assembly_plan`、`apply_assembly_plan_ops`、`assembly_plan_preview`、`assembly_plan_drawing`、`assembly_plan_bom` 和 `mechanical_catalog`。`os-optics schema assembly-plan`（以及 `assembly-plan-ops`、`design-bom`、`purchase-bom`、`bom-review`、`public-mechanical-catalog`）打印每种文档的 JSON Schema。
