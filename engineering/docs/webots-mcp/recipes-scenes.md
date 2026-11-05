# S06 配方、原生场景与单实例基线

日期：2026-10-30；基线 `223925a356f6e2f86fff09eb50677d99c58eaf86`。实现仅依赖 Python 标准库和现有 S04 报告承载；不安装依赖、不启动应用、不改原源码或安装。配方是等待模型/设备/阈值绑定的实验计划；生成 `.wbt` 是离线合成产物。两者都没有 Webots 加载、物理通过或真实 SW 导出的证明。

## 使用与接缝

从隔离副本的 `engineering/tools` 运行；`output` 必须由调用方先核实为本次 run 的独立输出目录。下面只生成新文件，已有同名文件会报错；不会导入 controller 或触发 `sim_launch`。

```python
from pathlib import Path
from webots_mcp.recipes import generate_scene, load_recipes, prepare_experiment
from webots_mcp.evidence import EvidenceStore

scene = generate_scene("stairs", {"count": 3, "rise_m": 0.05, "tread_m": 0.2})
with (output / "stairs.wbt").open("x", encoding="utf-8", newline="\n") as file:
    file.write(scene["world"])

plan = prepare_experiment("terrain_traverse", "stairs", {"rise_m": 0.05})
store = EvidenceStore(verified_state_dir, task_id=task_id, room_id=room_id)
report = store.new_report("experiment_report", run_id, provenance="synthetic",
                          data=plan, missing_information=plan["missing_information"])
store.write_report(report)
```

`load_recipes()` 返回 6 份独立配方字典，展开 catalog 的公共字段；调用方修改返回值不会修改源文件。`generate_scene(kind, parameters=None)` 返回参数、单位、ENU 标记、原生几何描述、UTF-8 world 文本/sha256、synthetic 来源和 unknown 物理结论。`prepare_experiment(recipe_id, scene_kind, parameters=None)` 返回可嵌入 `experiment_report.data` 的完整计划；默认 `model=null`、`execution_state=not_started`、`core_ready=false`、空 observations，步骤条目均 `dispatchable=false`。API 不执行静态模型校验或任何物理动作；L0 记录所需检查，生产模型校验仍待集成。

所有外部参数必须是字典内允许的数值字段，拒绝 bool、NaN/Infinity、巨大越界整数、未知字段和非整数 count/seed/time_step_ms。未知场景/配方、不适用的配方与场景组合、超出地面范围的实体均抛 `ValueError`。模板本身是包内受控文件，不提供通用外部模板装载/节点文本注入入口。

## 场景参数和范围

场景源：`engineering/tools/webots_mcp/scenes/templates.json`。只直接描述 Webots ENU：X 东（本模板铺设方向）、Y 北（横向）、Z 向上；机器人自身前进轴待核实，没有定义或计算 SW/URDF 坐标变换。长度均为米，time_step_ms 以毫秒计，seed 为无量纲整数。模板每个三元组为 `[默认值, 最小值, 最大值]`，完整上下限以该文件为准。

| 场景 | 参数默认值 | 几何含义 |
|---|---|---|
| 全部 | length_m=4、width_m=2、thickness_m=0.1；time_step_ms=32、seed=0 | 地面中心 XY=(0,0)，顶面 Z=0，底面 Z=-thickness；有限地面，无围墙 |
| ground | 无额外字段 | 一个静态 Box |
| stairs | count=3、rise_m=0.05、tread_m=0.2、feature_width_m=1、start_x_m=0.5 | 每一级沿 +X 增加一个踏面和一级高度；第 i 级顶面 i×rise，盒底均在 Z=0；count 限 1–32 |
| slope | run_m=1、rise_m=0.3、feature_width_m=1、start_x_m=0.5 | 2×2 原生 ElevationGrid：局部 height=[0,rise,0,rise]，+X 上坡，坡角可由 atan(rise/run) 推导；直接输入 rise/run 避免单位歧义 |
| obstacle | x_m=0.8、y_m=0、length_obstacle_m=0.2、width_obstacle_m=0.5、height_m=0.15 | 独立 Box 障碍，底面 Z=0；XY 为中心位置 |

每个 Shape 的几何 DEF 原样被对应 Solid.boundingObject USE 复用，不用可视网格代替未声明碰撞体；环境 Solid 无 Physics，作为固定场景。坡面仅表示原生网格的上表面，`thickness` 使用官方向最低点下方扩展的语义，不声称具有楔体侧壁/底面支持。台阶接缝、坡面与地面的共面接触、薄小几何和实际接触数值均待真实验证。生成世界只使用内建节点，不使用 TexturedBackground、RectangleArena 或远程 PROTO。

`contactMaterial="terrain"` 是待绑定标签；当前不生成经校准的 ContactProperties 材料对。Webots 自身仍可能采用其默认接触参数，不能把默认值当实测摩擦。地形尺寸不是任意机器人的适配性保证；机器人尺寸、离地间隙、轮径、足端、碰撞覆盖和驱动必须先核实。

## 实验配方和预算

配方源：`engineering/tools/webots_mcp/recipes/catalog.json`。每份均包含目标、适用模型、单位、初态、动作、观测、碰撞覆盖、预算、取消条件、判定与缺失信息。无来源的模型姿态/关节初态/速度、设备名/驱动值/停机策略、材料对、物理门限均不补默认；A01/A02 校准模型，A04 负责真实执行与物理有效性门槛。

| 配方 | 范围 | 默认步骤 / 仿真预算 | 观测与判定边界 |
|---|---|---|---|
| static_preflight / L0 | 文件、单位、坐标来源、hash/复杂度静态预检 | 0 步，10s 墙钟 | 不启动 Webots；不证明惯量或模型物理合法 |
| conversion_calibration / L1 | 非对称校准模型的来源/字段/映射对照 | 8 步×32ms=0.256s，30s 墙钟 | 等待真实模型/来源；不猜坐标与质量解析 |
| drop_or_rest / L2 | 已验证单刚体的落体/静置采样 | 64 步×32ms=2.048s，60s 墙钟 | 位姿/速度/接触；不以最终静止姿态独立判稳定 |
| joint_contact_probe / L3 | 显式单电机/传感器与相关接触 | 128 步×32ms=4.096s，60s 墙钟 | 真实设备控制与反馈；不以改节点位姿代替运动 |
| terrain_traverse / L4 | ground/stairs/slope/obstacle | 128 步×32ms=4.096s，60s 墙钟 | 驱动反馈、位姿、候选接触、关键截图；适配机型后才运行 |
| paired_step_comparison / L5 | 上述同一场景的两次串行对照 | 32ms×64 与16ms×128，各2.048s，各60s 墙钟；max_trials=2 | 必须同源版本/模型/初态/动作与种子；两份 world hash 因步长不同应不同；A04 定义指标与收敛门槛 |

L5 分别调用 `prepare_experiment("paired_step_comparison", "slope", {"time_step_ms": 32, "seed": 17})` 与 16ms 参数，生成两个独立未执行计划；不自动启动、重置或重放。L5 只接受 32/16ms。其他配方允许模板范围内 1–64ms 整数步长，并保持步数上限、据此如实重算仿真时间；不会默默延长步骤。WorldInfo.basicTimeStep、计划 time_step 秒数和初态 seed 随参数保持一致。

每次最多一个仿真实例，步骤分成不超过 8 步的批次（低于 S03 单调用1000步上限），批间计划让出20ms墙钟；连续渲染计划关闭，最多3张关键截图，日志1MiB。它们是待 worker 强制的上限/策略，不是本模块已执行的限流。达到墙钟/步数/证据预算、取消、断连或动作unknown、非有限观测、来源漂移、经测量制定的资源上限超出时停止派发；取消不等于已回滚动作。

CPU 单核百分比、working_set_bytes、private_bytes、gpu_percent 默认 null。S05 `sample_job` 提供只读 Job 采样；CPU 单核百分比可超过100%，working set 求和可重复计共享页，private bytes 是私有提交量；GPU当前未测。S05 `budget_check` 只接受明确非空的已知数值上限，不能直接传本配方整个 budget，也不能把 null 筛掉后声称未知指标通过；每个缺失指标继续列 unknown/缺口。测量前不定义“低占用/平衡”阈值或承诺加速比。

## 本轮基线证据

控制目录 `evidence/S06/single-instance-baseline.json` 当前核对 S03 已捕获文件与原 world 的字节哈希，未再次启动 Webots。探针时间2026-10-30 02:46:30.343837–02:46:47.261745 UTC；一个有界诊断世界，实连 extern、8个32ms步骤、sim_time 0→0.256、正常退出和清理后残留0。完整探针墙钟由已记录时间戳相减为 **16.917908s**，包括SDK导入、启动、握手、步进与退出，不能用于计算物理步进吞吐或实时因子。

同次启动前采样逻辑CPU24、总物理内存16,763,248,640 bytes、可用物理内存6,229,344,256 bytes、内存负载62%；这些是主机时点数据，不是Webots内存峰值。CPU/进程树内存/GPU/纯步进墙钟/实时因子保持null。安装缺失两个环境PROTO，完整样例环境和物理有效性仍unknown；该诊断基线不能外推新生成地形或复杂CAD模型。补资源基线时复用S05采样、预留唯一实例、记录测量窗口，不为补空值重复开应用。

## 可复查证据与官方依据

离线命令：`python -B -m unittest discover -s engineering/tests/webots_mcp -p "test_*.py" -v`。新增7项检查：手算Box位置/尺寸、坡面高度索引/障碍底面、确定性world/hash与碰撞复用、类型/非有限/范围/数量/边界拒绝、预算/未知信息、等时减半步长、6配方与既有EvidenceStore往返。新场景尚未在Webots加载，字符串与结构检查不能算其语法/运行/物理验收。

控制目录 `evidence/S06/`：RED/GREEN、完整回归日志；7份官方原文和`official-sources.json`的URL/R2025a/访问UTC时间/hash；`generated-worlds/*.wbt`与6份`sample-state/` synthetic未执行报告；S03基线及来源hash；S05独立7项进程夹具复核日志/采样/审查。所有物理verdict均unknown；不涉及Astra核心、真实SW导出、DSH端到端或硬件通过。

2026-10-30 联网核对的官方R2025a依据：

- [Box](https://raw.githubusercontent.com/cyberbotics/webots/R2025a/docs/reference/box.md)：局部原点居中、XYZ尺寸。
- [ElevationGrid](https://raw.githubusercontent.com/cyberbotics/webots/R2025a/docs/reference/elevationgrid.md)：XY网格、Z高度索引、spacing与thickness。
- [Solid](https://raw.githubusercontent.com/cyberbotics/webots/R2025a/docs/reference/solid.md)：环境/碰撞体和允许的boundingObject节点。
- [WorldInfo](https://raw.githubusercontent.com/cyberbotics/webots/R2025a/docs/reference/worldinfo.md)：ENU、毫秒basicTimeStep与seed。
- [ContactProperties](https://raw.githubusercontent.com/cyberbotics/webots/R2025a/docs/reference/contactproperties.md)：材料配对及摩擦参数，需要来源与实测校准。
- [Motor](https://raw.githubusercontent.com/cyberbotics/webots/R2025a/docs/reference/motor.md) 与 [Robot](https://raw.githubusercontent.com/cyberbotics/webots/R2025a/docs/reference/robot.md)：真实设备单位、步进时长关系；联网核对不能替代本机实际动作验证。
