# 安装

[用户指南](README.md) · [English](../en/installation.md) · 简体中文

OS Optics 支持 Windows、macOS 和 Linux，要求 **Python 3.11 及以上版本**。编写 Python 脚本或使用 Notebook 时选择 pip；只需要独立命令行工具时可选择 uv。

## 让文档与安装版本一致

本文档对应 os-optics **0.14.0**，该版本记录在 [engine-baseline.json](../../engine-baseline.json)。
请在全新环境中安装该精确版本：

```bash
python -m pip install "os-optics==0.14.0"
```

`os-optics doctor` 显示已安装版本。[os-optics-kit](https://github.com/JunLin615/os-optics-kit)
中的每个案例都注明了验证时使用的引擎版本。

## Python 环境

创建虚拟环境：

```bash
python -m venv .venv
```

macOS 或 Linux 激活方式：

```bash
source .venv/bin/activate
```

Windows PowerShell 激活方式：

```powershell
.venv\Scripts\Activate.ps1
```

安装并检查引擎：

```bash
python -m pip install os-optics
os-optics doctor
```

命令行也可以通过 `python -m os_optics` 调用。如果 PowerShell 无法激活环境，可直接运行 `.venv\Scripts\python.exe -m pip install os-optics` 和 `.venv\Scripts\python.exe -m os_optics doctor`。

`protected` 扩展是可选的，何时需要见[打开加密的商业交付](#打开加密的商业交付)。

## 使用 uv 安装独立命令行工具

安装 [uv](https://docs.astral.sh/uv/) 后运行：

```bash
uv tool install os-optics
os-optics doctor
```

如果终端找不到命令，运行 `uv tool update-shell`，然后重新打开终端。也可以临时运行一条命令：

```bash
uvx --from os-optics os-optics doctor
```

## 准备光学目录

```bash
os-optics catalog sync
os-optics catalog status
```

目录会下载到本地存储中，后续仿真可以使用已保存的版本，也支持离线运行。`doctor` 检查本地安装是否就绪；`catalog sync` 从 OpenSci 获取当前目录。

## 升级

使用 pip 安装时：

```bash
python -m pip install --upgrade os-optics
```

使用 uv 工具安装时：

```bash
uv tool upgrade os-optics
```

0.9.0 及更早版本以 `opensci-engine` 名称发布（命令 `opensci-engine`，`import opensci_engine`）。
以该名称安装的环境同样运行 `python -m pip install --upgrade os-optics`，两个软件包没有共用文件。
之后使用 `os-optics` 命令和 `import os_optics`。以该名称通过 `uv tool` 安装的环境运行
`uv tool install os-optics`，之后使用 `os-optics`。

升级到 0.14.0 时，Project 文件无需修改。0.13.0 生成的运行仍可读取：`os-optics results freshness`
将其报告为 `HISTORICAL`（原因 `ENGINE_CHANGED`），`os-optics project verify` 将其目录报告为
`INCONSISTENT`，因为 0.14.0 改变了每个引擎输入（引擎 schema 1.12.0）。用 0.14.0 重新运行各 Project，
即可得到当前且可通过校验的运行。读取 `project run` 退出码的脚本请按 0.14.0 的[退出码](results.md#退出码)处理。

运行 `os-optics doctor` 查看已安装版本。需要固定本文档对应的版本时，安装 `"os-optics==0.14.0"`。

工具、案例和 Agent 技能是独立的软件包。`python -m pip install os-optics-kit` 将它们安装到同一环境中；该软件包声明了它支持的 os-optics 版本。

## 打开加密的商业交付

`protected` 扩展会安装 `cryptography`，用于解开 AES-256-GCM 信封，仅用于打开商业方案的加密装配预览或装配详情：

```bash
python -m pip install "os-optics[protected]==0.14.0"
uv tool install "os-optics[protected]==0.14.0"      # 使用 uv 工具安装时
```

本地设计与仿真、光学目录、三维视图、本地验证以及本地装配校验与图纸都不需要该扩展。`os-optics doctor` 会报告
`protected_extra.installed`；未安装该扩展时打开交付会以 `CRYPTO_UNAVAILABLE` 失败，并给出需要安装的扩展。

## 常见安装问题

| 现象 | 处理方式 |
| --- | --- |
| 安装提示 Python 版本不符 | 检查 `python --version`，使用 Python 3.11+ 或 uv 安装方式 |
| 找不到 `os-optics` 命令 | 激活虚拟环境，或使用 `python -m os_optics` |
| doctor 显示 `DOWNLOAD_ON_FIRST_USE` | 运行 `os-optics catalog sync`，或开始使用目录创建设计 |
| 目录下载失败 | 用 `os-optics catalog check` 检查连接；离线导入方式见[目录指南](catalog.md) |
| 服务要求更新引擎 | 升级软件包、更新项目目录版本，重新运行设计后再提交 |
| `project verify` 将 0.13.0 的运行报告为 `INCONSISTENT` | 用 0.14.0 重新运行该 Project |

下一步：[运行第一个光学设计](getting-started.md)。
