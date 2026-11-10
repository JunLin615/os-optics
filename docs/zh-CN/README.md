# 用户指南

[首页](../../README.zh-CN.md) · [English](../en/README.md) · 简体中文

从一个可运行的光学设计开始，逐步了解结果，并将它调整为自己的实验方案。

1. [安装 OS Optics](installation.md)：Python、虚拟环境、uv 和版本升级。
2. [运行第一个设计](getting-started.md)：生成项目、运行仿真、打开三维视图。
3. [使用光学目录](catalog.md)：查找元件、管理版本、复现项目。
4. [读取与导出结果](results.md)：验证状态、退出码、光束数据、成像质量与鬼像分析、可视化和 CSV 导出。
5. [继续完成硬件实现](hardware.md)：元件选型、装配预览和订购。
6. [设计研究与工作流程](https://github.com/JunLin615/os-optics-kit/blob/main/docs/zh-CN/workflows.md)（os-optics-kit）：按标识修改项目、参数扫描、运行比较、绘图、报告、灵敏度与公差分析，以及三个参数化光学工作流程。
7. [分析测量数据](https://github.com/JunLin615/os-optics-kit/blob/main/docs/zh-CN/measurements.md)（os-optics-kit）：背景扣除、参考归一化、线性拟合及残差图。
8. [联用外部科学工具](https://github.com/JunLin615/os-optics-kit/blob/main/docs/zh-CN/interop.md)（os-optics-kit）：用 SciPy、Optuna、SALib、LightPipes、prysm、POPPY、Perceval、Piquasso 和 grcwa 完成优化、衍射、量子光子学和超表面元件计算。

## 案例、工具与技能

[os-optics-kit](https://github.com/JunLin615/os-optics-kit) 收录基于引擎的完整案例、可复用工具和 Agent 技能。使用 `python -m pip install os-optics-kit` 安装；`os-optics-kit cases list` 列出全部案例。

| 案例 | 指南 |
| --- | --- |
| [扩束系统](https://github.com/JunLin615/os-optics-kit/tree/main/cases/beam-expander) | [第一个设计](getting-started.md) |
| [理想透镜聚焦](https://github.com/JunLin615/os-optics-kit/tree/main/cases/ideal-lens-focus) | [结果](results.md) |
| [扩束与聚焦](https://github.com/JunLin615/os-optics-kit/tree/main/cases/expansion-focus)、[偏振控制](https://github.com/JunLin615/os-optics-kit/tree/main/cases/polarization-control)、[反射镜对准](https://github.com/JunLin615/os-optics-kit/tree/main/cases/mirror-alignment) | [设计研究与工作流程](https://github.com/JunLin615/os-optics-kit/blob/main/docs/zh-CN/workflows.md) |
| [探测器测量数据](https://github.com/JunLin615/os-optics-kit/tree/main/cases/detector-measurements) | [测量数据分析](https://github.com/JunLin615/os-optics-kit/blob/main/docs/zh-CN/measurements.md) |
| [优化](https://github.com/JunLin615/os-optics-kit/tree/main/cases/interop-optimization)、[衍射](https://github.com/JunLin615/os-optics-kit/tree/main/cases/interop-diffraction)、[量子光子学](https://github.com/JunLin615/os-optics-kit/tree/main/cases/interop-quantum)、[超表面](https://github.com/JunLin615/os-optics-kit/tree/main/cases/interop-metasurface) | [外部工具联用](https://github.com/JunLin615/os-optics-kit/blob/main/docs/zh-CN/interop.md) |

每个案例注明是否使用光学目录，以及验证时使用的引擎和目录版本。os-optics-kit 需安装在 os-optics 所在的同一 Python 环境中：`python -m pip install os-optics-kit`。

这些指南对应 **os-optics 0.13.0**；匹配的安装方式见[版本匹配说明](installation.md#让文档与安装版本一致)。命令参数可通过 `--help` 查看；Python API 说明可查阅已安装的软件包和 [PyPI 参考文档](https://pypi.org/project/os-optics/)。

[OpenSci 官网](https://opensci-optics.com/) · [软件包版本](https://pypi.org/project/os-optics/#history) · [交流与反馈](https://github.com/JunLin615/os-optics/issues/new/choose)
