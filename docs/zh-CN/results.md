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

以下精简控制接口对应 0.12.0；请按
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
保留原始运行及其哈希。修改目标不会把旧判定变成新判定。只要 Project 与转换后的引擎输入一致，
其他引擎构建生成的运行仍可能为 `CURRENT`；复现时还应查看 `engine_build_matches`。
详见 os-optics-kit 中的[比较工作流程](https://github.com/JunLin615/os-optics-kit/blob/main/docs/workflow-recipes.md#compare-and-freshness)。

`project verify` 检查记录中的文件哈希及一致性，不会与独立光学解或实验进行比较。
完整案例中的解析参考提供单独的校验，并注明适用假设。

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
