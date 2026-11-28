# 公开机械目录

[用户指南](README.md) · [English](../en/mechanical-catalog.md) · 简体中文

本地装配方案由两份公开目录推导。[光学目录](catalog.md)描述光学元件；机械目录描述承载它们的 OpenSci 镜架、立杆、杆座和面包板（`OS-MECH-*` 料号）。它是公开文档 `opensci.public_mechanical_catalog`，版本 `1.0`。每个料号列出：

- 种类以及中英文名称；
- 方案推导读取的工程参数，例如立杆的直径、长度和螺纹，镜架的中心高度、所夹持的光学元件和调整行程；
- 每项参数的依据（`DATASHEET`、`REVIEWED_REFERENCE`、`CAD_DERIVED`、`FAMILY_RULE` 或 `PREVIEW_ASSUMPTION`）；
- 供查看器使用的简单形状。

文档不含价格，也不含供应商数据。

有些 OpenSci 料号由多个供应商产品实现。对这些料号，文档给出一份保守参数，标记为 `CONSERVATIVE`：外包络取最大，通光孔径和行程取最小，厚度和插入深度范围取最窄。各产品之间不同的参数列在 `profile.conservative_parameters` 中。

## 获取目录

`os-optics catalog sync` 下载当前的光学目录版本。OpenSci 服务提供机械目录时，同一命令也会下载当前的机械目录版本，校验其 SHA-256、大小和内容，并设为当前使用的版本。输出中的 `mechanical` 部分给出 `outcome`，取值如下：

| 结果 | 含义 | 退出码 |
| --- | --- | --- |
| `DOWNLOADED` | 已保存当前版本并设为当前使用。 | 0 |
| `ALREADY_STORED` | 本机已有当前版本，并已设为当前使用。 | 0 |
| `NOT_OFFERED` | 服务不提供机械目录。这不是错误。 | 取决于光学同步 |
| `UNAVAILABLE` | 服务无法连接、超时或返回服务器错误。stderr 上有一行说明，本机已存的版本继续使用。 | 取决于光学同步 |
| `FAILED` | 下载内容与公布的 SHA-256 或大小不符，或不是有效文档。没有保存任何内容。 | 1 |

无法连接服务的计算机可以从文件导入版本。命令根据 schema 识别机械目录文档：

```bash
os-optics catalog import public_mechanical_catalog.json
```

以下命令也会在 `mechanical` 部分报告机械目录：

- `catalog status` 显示当前使用的版本和已存版本，不使用网络；
- `catalog check` 向服务查询当前版本；
- `catalog verify` 重新校验每个已存文件，有文件损坏时退出码为 66。

版本保存在目录存储中，即 `OPENSCI_CATALOG_DIR` 下的 `mechanical/`。每个版本只写入一次，之后不会被不同的内容替换。这些命令都不需要令牌。

## 使用自己的硬件

方案也可以使用你自己的硬件，用 USER 文档描述。在 USER 文档中：

- `publisher` 为 `USER`；
- 料号形如 `USER-MECH-*`；
- `mechanical_version` 是一个简短的小写标签；
- 没有 `policy`；
- `content_digest` 是文档自身的摘要。

用 `--mechanical FILE` 传入。该选项可以重复使用：最多一份 OPENSCI 文档和八份 USER 文档，同一料号不能出现两次。也可以把文件列在 `OS_OPTICS_MECHANICAL_CATALOG` 中，用系统路径分隔符分隔（Windows 为 `;`，其他系统为 `:`）。

命令按以下顺序使用文档：

1. `--mechanical FILE` 文档；
2. 否则，`OS_OPTICS_MECHANICAL_CATALOG` 列出的文档；
3. 对方案而言，推导该方案时使用的 OPENSCI 版本，以及方案自带的 USER 文档副本；
4. 否则，本机当前使用的已存版本。

因此方案可以离线重新打开。

## 浏览目录

```bash
os-optics catalog mechanical list --kind MOUNT
os-optics catalog mechanical list --holds-size 25.4
os-optics catalog mechanical show OS-MECH-...
os-optics catalog mechanical status
```

各命令的作用如下：

- `list` 每个料号输出一条，含种类、名称、关键参数、参数类型和发布方。加 `--json` 时输出完整的料号文档。
- `show` 输出一个料号文档。料号不存在时退出码为 1。
- `status` 列出当前生效的文档和已存版本。

没有任何文档时，这些命令的退出码为 65（`list` 和 `show`）或 1（`status`），输出会说明如何获取。
