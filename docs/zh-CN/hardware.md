# 从设计到硬件

[用户指南](README.md) · [English](../en/hardware.md) · 简体中文

[OpenSci](https://opensci-optics.com/) 供应光学系统所需的零件。引擎在你的计算机上设计、仿真光学系统，并规划其安装方式，再把方案转成采购清单（BOM），由 OpenSci Account 报价和下单。方案、预览和图纸都在你的计算机上生成，不会上传。

## 工作流程

**设计 → 仿真 → 本地装配方案 → 采购清单 → 报价 → 订单 → 交付**

清单决定你购买的内容：

| 购买内容 | 清单列出 |
| --- | --- |
| 仅光学元件 | 光学元件的 `OS-OPT-*` 行 |
| 光学元件及 OpenSci 机械件 | 光学元件，以及方案中的镜架、立杆、杆座和面包板（`OS-MECH-*`） |
| 备件 | 另起一行，标 `"purpose": "SPARE"` |
| 在自己的光学平台上搭建 | 方案中的零件，不含面包板一行 |

OpenSci 检查每一行能否供应，但不检查零件能否装配在一起：这由本地方案及其检查和图纸完成。系统由你自行装配。

## 连接 Account

从 [OpenSci 官网](https://opensci-optics.com/) 进入。Account 提供服务地址与 Agent Token 配置方式。将 `OPENSCI_API_URL` 设置为该地址，按 Account 中的说明完成 Token 配置，然后检查连接：

```bash
os-optics account status
```

没有 Account 时也可以先做方案、写好清单。本机没有令牌时，`os-optics order quote` 不发送任何内容，清单保留在文件中。它会给出 Account 的注册和登录页面，并说明令牌的保存位置；之后运行它给出的命令即可：清单、审核文件和输出文件均为绝对路径，`--schema-version` 相同，并按你的 shell 加好引号（Windows 上为 PowerShell，其他系统为 sh），因此发送的是同一请求。`OPENSCI_ACCOUNT_URL` 可指定其他 Account 网站。同一清单再次发送时请求相同，因此重复运行不会产生第二个报价。

## 获取配置方案

在本机为已保存的 Project 规划安装方式，然后写出采购清单。方案使用[机械目录](mechanical-catalog.md)，不需要令牌，也不需要网络：

```bash
os-optics assembly plan derive project.json --out plan
os-optics assembly plan preview plan --out preview.zip
os-optics view plan
os-optics assembly plan draw plan --out drawing --locale zh-CN
os-optics assembly plan bom plan --purchase --out bom.json
```

也可以自己编写清单。每一行包含 OpenSci 料号和数量；`line_id` 和 `purpose`（`PLAN` 或 `SPARE`）可选：

```json
{"schema": "opensci.commercial.part_list_submission", "schema_version": "2",
 "lines": [{"opensci_sku": "OS-OPT-...", "quantity": 2},
           {"opensci_sku": "OS-MECH-...", "quantity": 2, "purpose": "SPARE"}]}
```

报价：

```bash
os-optics order quote bom.json
```

报价会逐行审核，结果为以下之一：

- **每一行都可以供应**（`ORDERABLE`，退出码 0）。输出给出价格和下一条命令 `os-optics order create RESOLUTION_ID`。
- **部分行无法按清单供应**（`CHANGES_REQUIRED`，退出码 1）。某一行可能不是可订购的 OpenSci 产品、暂时无法供应，或数量超过单行上限。输出列出这些行，并写出 `bom.review.json`。该文件建议保留其他行、删除有变化的行。你可以修改其中的决定，例如为超过上限的行减少数量，或换成其他料号。然后用 `os-optics order quote bom.json --confirm bom.review.json` 发送。确认之前，该报价不能下单。
  - 审核文件记录了它所属的清单。`--confirm` 需要同一份清单：其他清单的审核文件，或不存在的清单文件，都会在发送任何内容之前被拒绝（`CONFIRMATION_BOM_MISMATCH`、`INPUT_FILE_UNREADABLE`）。
  - 如果确认之前供应又发生变化，回复是新的审核文件 `bom.review-2.json`。用同一份清单以同样方式确认：`os-optics order quote bom.json --confirm bom.review-2.json`。
  - 审核文件绝不会覆盖清单或被确认的文件（`REVIEW_OUT_COLLISION`；不发送任何内容）。`--review-out` 所在的文件夹不存在，或该文件无法创建时，同样会被拒绝（`REVIEW_OUT_UNWRITABLE`；不发送任何内容）。
- **没有任何一行可以供应**（`BOM_NOT_ORDERABLE`，退出码 67）。审核文件已写出，服务不保存任何内容；修改清单后再报价。

`--schema-version 1` 向尚不支持清单报价的服务发送旧的零件清单格式。该格式没有 `purpose`，也没有确认步骤。

## 创建订单

在 OpenSci Account 中选择收货地址，核对报价的各行、总价和协议，然后创建订单：

```bash
os-optics order create RESOLUTION_ID
```

按订单中的付款说明转账。转账后，用 `os-optics order report-transfer ORDER_ID` 报告。OpenSci 确认收款后发货。

装配图由本地方案生成（`os-optics assembly plan draw`），默认英文，加 `--locale zh-CN` 为中文，格式为 SVG 和 PDF，不会上传。

OpenSci 不再按设计下单、选配镜架，也不再在线提供预览、图纸、布局图和安装单。`resolve`、`assembly preview` 和 `assembly drawing` 命令保留给旧版服务；对当前服务，它们会给出替代的本地命令。

更多平台信息与产品请访问 **[opensci-optics.com](https://opensci-optics.com/)**。
