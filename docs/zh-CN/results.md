# 结果、可视化与导出

[用户指南](README.md) · [English](../en/results.md) · 简体中文

每次运行都会生成完整的设计与仿真记录。结果 API 直接读取该记录，查看或导出数据时无需重新仿真。

## 阅读运行目录

| 文件 | 内容 |
| --- | --- |
| `manifest.json` | 整体状态、目标结果、标识和文件哈希 |
| `project.json` | 本次运行的光学项目 |
| `engine_request.json` | 传入物理引擎的输入 |
| `simulation_result.json` | 目标数值、光束、观测结果、路径、功率收支和问题记录 |
| `ray_data.json` | 主光线与瞳面采样光线的追迹数据，包括表面交点和光程 |
| `local_validation.json` | 本地验证记录，关联项目、目录、引擎和结果标识 |
| `optical_scene.json` | 结构化光学场景 |
| `optical_scene.glb` | glTF 二进制格式的三维场景 |

```bash
os-optics results describe run
os-optics results monitor run output
os-optics project verify run
```

`output` 是扩束示例中的观测平面标识。自己的项目可以通过 `results describe` 查看可用观测平面。

## 理解结果

以下精简控制接口对应 0.13.0；请按
[版本匹配说明](installation.md#让文档与安装版本一致)安装匹配的软件包。

当某个科学量超出元件模型声明的波长、角度或光束范围时，依赖它的目标判定为 `INDETERMINATE`，
并在 `applicability_issue_codes` 中给出适用范围问题代码。该科学量仍保留数值、单位和上下界：
数值可读，但判定明确表示它不能说明声明范围外的结果有效。需要 `MET` 或 `UNMET` 判定时，
请将计算保持在适用范围内；os-optics-kit 中的
[超表面案例](https://github.com/JunLin615/os-optics-kit/tree/main/cases/interop-metasurface)演示了这一适用范围。

```bash
os-optics results targets run
os-optics results freshness run --project project.json
```

分别读取以下信息：

| 维度 | 查看内容 | 含义 |
| --- | --- | --- |
| 计算执行 | `outcome.execution` 中的完成情况、终止原因和预算 | 请求的计算是否完成，哪些限额已用尽 |
| 科学量 | 指标的数值、单位、有效性、上下界及适用性问题 | 可用点值、区间、不可用的量或超出模型范围的量 |
| 客户要求 | 各目标的 `assessment` 与 `outcome.requirements` | `MET`、`UNMET`、`INDETERMINATE` 或 `NOT_APPLICABLE`；未配置目标表示未评估要求 |
| 结果时效 | `CURRENT`、`HISTORICAL` 或 `UNDETERMINED` 及原因代码 | 保存的运行是否对应给定 Project |
| 商业结果 | 配置或订单命令的响应 | 在该操作条件下哪些内容可以采购或下单 |

一个汇总 `PASS` 不能同时回答这五个问题。计算完成时，可选目标可能未满足，但仍有可用的科学量。
应读取所用科学量或目标的适用性：无关的模型范围问题不会使所有结果失效。
有界量应保留为区间；如果区间无法确定比较结果，目标应保留为无法判定。

物理参数或计算预算修改会报告 `PHYSICS_CHANGED`，仅修改目标会报告 `TARGETS_CHANGED`。
保留原始运行及其哈希。修改目标不会把旧判定变成新判定。较早引擎版本生成且输入不同的运行报告为
`HISTORICAL`，原因为 `ENGINE_CHANGED`。只要 Project 与转换后的引擎输入一致，
其他引擎构建生成的运行仍可能为 `CURRENT`；复现时还应查看 `engine_build_matches`。
详见 os-optics-kit 中的[比较工作流程](https://github.com/JunLin615/os-optics-kit/blob/main/docs/workflow-recipes.md#compare-and-freshness)。

`project verify` 检查记录中的文件哈希及一致性，不会与独立光学解或实验进行比较。
完整案例中的解析参考提供单独的校验，并注明适用假设。

## 退出码

`project run`、`workspace run` 和 `validate` 通过 `--status-contract` 选择退出码规则；运行的
JSON 摘要在 `exit_contract` 中注明所用规则。无法修改命令行的脚本可通过环境变量
`OS_OPTICS_STATUS_CONTRACT` 设置。

| 规则 | 0 | 1 | 2 | 3 |
| --- | --- | --- | --- | --- |
| `requirements`（默认） | 计算完成，所有已声明目标均为 `MET`（或未声明目标） | 有目标为 `UNMET` | 有目标为 `INDETERMINATE` 或 `NOT_APPLICABLE` | 计算未完成（`PARTIAL` 或 `FAILED`） |
| `aggregate` | `PASS`、`WARNING` 或 `NOT_APPLICABLE` | `FAIL` | `UNKNOWN` | `OUT_OF_MODEL_SCOPE` |
| `execution` | 计算完成 | 部分完成或失败 | | |

回返光分析达到 Project `compute_budget` 中的 `max_return_analysis_work` 时，运行报告
`RETURN_ANALYSIS_BUDGET`：依赖该分析的上界保持 `UNKNOWN`，运行为 `PARTIAL`，因此在
`requirements` 下退出码为 3，在 `aggregate` 下为 2。提高该限额即可得到这些上界。

所有命令中，64 表示用法错误，65 表示 Project 或目录无法按给定内容仿真，66 表示 `project verify`
发现运行目录不一致。按 Ctrl+C 停止运行时退出码为 130，目录中只留下 `run_stopped.json`，不包含运行结果。
运行不会覆盖不是由它写入的文件：`--out-dir` 中与运行文件同名的用户文件会使运行以退出码 64 停止，
该文件保持不变。

`results targets` 成功返回时退出码为 0，即使目标未满足；`results freshness` 的退出码
0、1、2 分别表示 `CURRENT`、`HISTORICAL`、`UNDETERMINED`。科学任务是否完成应依据结构化
判定，不能仅根据命令退出成功。

## 精简摘要与完整案例

[os-optics-kit](https://github.com/JunLin615/os-optics-kit) 提供已保存运行的精简 JSON 摘要，以及附带已验证目标判定的完整案例：

```bash
python -m pip install os-optics-kit
os-optics-kit summary run
os-optics-kit cases list
```

摘要直接读取已保存的运行记录，无需重新仿真。[扩束系统](https://github.com/JunLin615/os-optics-kit/tree/main/cases/beam-expander)和
[理想透镜聚焦](https://github.com/JunLin615/os-optics-kit/tree/main/cases/ideal-lens-focus)案例分别展示使用目录的设计和无需目录的设计及其目标判定。

## 查看与分享

```bash
os-optics view run
os-optics view run --write-html view.html
os-optics view run --snapshot shots
```

浏览器查看器提供预设视角、元件选择、光束显隐和中英文标签。截图输出包含正交视图 PNG、`scene_summary.json` 和图例。

## 导出表格

```bash
os-optics export arrivals run --format csv --out arrivals.csv
os-optics export rays run --format jsonl --out rays.jsonl
os-optics results columns arrivals
os-optics results metrics --name waist_distance_x_mm
```

可导出的内容包括观测面、到达光束、光线、路径、路径分段、功率收支、叠加结果、波前、辐照度和指标定义。使用 `export --help` 查看格式与筛选参数。波前导出采用追迹光程差，辐照度图采用近轴高斯光束叠加模型。

## 基于运行的分析

```bash
os-optics project example --kind focus > focus.json
os-optics project run focus.json --out-dir run-focus
os-optics results imaging run-focus --monitor focal_plane
os-optics results ghosts run-focus --monitor focal_plane
```

`results imaging` 给出像面上每个视场的点扩散函数、MTF 和环围能量；`results ghosts` 列出透镜和窗口
表面反射形成的鬼像，并给出未列出鬼像光功率的上界。每条命令输出一个 `opensci.analysis_result`
文档：各物理量及其状态、区间和收敛依据，所用模型及其有效性检查，以及完整性说明。

| 命令 | 分析内容 |
| --- | --- |
| `results focal-field RUN --request FILE` | 任意数值孔径下理想齐明物镜的矢量焦场 |
| `results field RUN --monitor ID` | 采样场或散斑光源的相干场：辐照度、功率和散斑对比度 |
| `project cavity PROJECT --components M1 M2 --wavelength-nm NM` | 线性谐振腔的稳定性、本征模和谐振，以及经腔镜注入光的共振增强 |
| `results configurations RUN` | 含 `configurations` 的 Project 各配置并列比较；每个配置运行到 `RUN/configurations/ID` |

`os-optics schema focal-field-request` 输出请求文件的结构定义。分析完成时退出码为 0；有物理量超出其模型
有效范围时为 2（原因记录在 JSON 中）；请求被拒绝时为 1。

Python 调用方式：

```python
from pathlib import Path
from os_optics.results import load_run

results = load_run("run")
print(results.describe())
print(results.power_budget())
Path("arrivals.csv").write_bytes(results.export("arrivals", "csv"))
```

## 单位与约定

API 使用毫米表示长度、纳米表示真空波长、角度制表示角度、瓦特表示功率。高斯光束尺寸 `w` 表示**光强降至 1/e² 处的半径**。坐标系为右手系，透射元件的局部 `+z` 为光轴，反射元件的局部 `+z` 为镀膜表面向外的法线。导出列名和指标说明会标明相应单位。

下一步：[从设计到硬件](hardware.md)。
