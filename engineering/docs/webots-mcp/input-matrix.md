# S02：输入矩阵、解析金样与旧包复用边界

日期：2026-10-29。计划依据：冻结 PLAN.md 的 S02/G0；未扩大至 Astra A01–A04。所有夹具都为人工合成，CAD 文件名、配置和实例身份均是假数据；没有真实 SW 导出或 Webots 运行证明。

## 输入支持矩阵

“本阶段已检查”仅指目录下的离线样例，不能据此把任意用户模型判为支持。

| 输入 | 在最终链路中的用途 | S02 的实际出口 | 后续责任 / 必须保留的损失 |
|---|---|---|---|
| `.SLDPRT`、`.SLDASM` + 指定配置 | 主要三维源；最终应自动读取/导出 | 读取现有接口；合成来源/实例字段，无原生解析或导出实现 | A01/A02：真实 SW API 捕获、组件位姿、配合、配置、持久引用；不得要求用户手工URDF作为最终替代 |
| URDF 1.0 + 本地 mesh + 来源元数据 | 首批中间格式 | 非对称盒、固定树、单转动/移动关节及故障样例；XML/引用等最小检查 | A01/A02/A04：坐标、惯量、动力学、碰撞、真实驱动；fixture 检查通过不等于转换/仿真通过 |
| STEP | 三维几何辅助入口 | 仅矩阵登记，未实现导入 | 不能推定完整实例身份、配合、驱动或材料；具体SW导入能力另验 |
| STL/OBJ 单网格 | 可视/碰撞几何 | 本地 OBJ 非对称盒；读取顶点核对尺寸 | 不能单凭文件恢复单位、关节、质量；可视网格与碰撞近似要分别记录 |
| `.SLDDRW` / PDF / 图片 | 尺寸核对、三维重建依据 | 未实现等价三维恢复 | 缺少形状/运动/物理信息不能补成已知；按独立重建任务处理 |
| 闭环、过约束、柔性、强度/疲劳 | 特定专业链或专项验收 | 未实现、未验收 | A02 闭环专项；已有专业物理链继续负责柔性/强度/疲劳 |
| URI `package://` | 包内本地 mesh 引用 | 旧解析器纯格式探针当前可规范化 | 新生产入口尚未集成；必须显式包根与路径边界。fixture guard 不提供通用 URI resolver |

## 可消费的最小数据

样例入口：`engineering/tests/webots_mcp/fixtures/cases.json`，版本 `webots-mcp-input-fixtures/v1`。这是一份测试数据索引，不取代 S01 的全局接口合同，也不是 S04 的 model_manifest 实现。

| 字段 | 含义 / 消费规则 |
|---|---|
| `fixture_id` | 该样例的稳定测试标识；不得用它代替真实 CAD 身份 |
| `input.provenance` | `kind=synthetic`、`real_sw_export=false`、`real_webots_run=false`；展示/报告必须保留 |
| `input.source.kind/path` | `urdf` 的 path 相对 fixtures 根；`analytic_box` 仅用于单位来源金样，不是运行工具的新增输入类型 |
| `input.source.units` | 显式 length/mass/angle/time/inertia；原值不就地改写。m/mm来源分别保留，不做“统一再除1000” |
| `input.source.coordinate_frame` | 原始坐标来源标签；不是 SW→Webots 变换定义 |
| `input.components[]` | id、instance_path、source_file、configuration、persistent_reference、identity_status、urdf_link；同一 source_file 可以对应不同实例 |
| `persistent_reference=null` | 本样例没有真实持久引用。不能序列化成伪造字节，也不能由实例名推定重建后稳定 |
| `expected.error_code/summary` | 本地 fixture oracle；null error 只表示指定离线检查可读。生产错误码后续按全局合同映射，不直接将该摘要当 PASS |

真实捕获还需要 model/source revision、源文件/mesh 哈希、SW/导出器版本、具体 API 方法/参数/状态、坐标与单位来源、选中/抑制范围；缺失时应明确 unknown。S02 不伪造这些值。S03/S04 可直接消费原始输入与 provenance，不应导入测试 guard 当生产校验器。

## 夹具与检查

运行：`python engineering/tests/webots_mcp/test_input_fixtures.py`。只用标准库，不导入 SW/转换器/Webots/DSH；不改写输入。5 个检查方法覆盖 22 个 case（6个可读样例、16个拒绝样例）以及3份方法数组金样。

| 样例 | 固定事实 / 本地拒绝 |
|---|---|
| `asymmetric_box` | OBJ 为 8 顶点/12 三角面的闭合合成盒，XYZ尺寸 0.1×0.05×0.02 m；collision 为同尺寸 box |
| `box_metadata_m` / `box_metadata_mm` | 原尺寸分别 `[0.1,0.05,0.02] m`、`[100,50,20] mm`；期望相同物理尺寸是解析依据，本阶段不执行换算 |
| `repeated_instances` | 同一合成 Box.SLDPRT/配置的两份实例；不同 id/path/link；第二实例带显式 Z 旋转 π/2；不合并身份 |
| `single_revolute` | 显式 Z 轴，范围 −0.5~0.75 rad；effort=1 N·m、velocity=0.5 rad/s |
| `single_prismatic` | 显式 X 轴，范围 0~0.05 m；effort=10 N、velocity=0.02 m/s |
| `bad_xml` / `dtd` | `INVALID_XML` / `UNSAFE_XML`；拒绝 DTD/实体声明 |
| `missing_mesh` / `outside_mesh` / `remote_mesh` | `MESH_NOT_FOUND` / `PATH_OUTSIDE_FIXTURES` / `REMOTE_MESH`；无下载、无外部文件读取 |
| `duplicate_link` / `duplicate_joint` / `duplicate_component_id` | 分别 `DUPLICATE_LINK_ID` / `DUPLICATE_JOINT_ID` / `DUPLICATE_COMPONENT_ID` |
| `missing_units` / `missing_frame` | `MISSING_UNITS` / `MISSING_COORDINATE_FRAME`；不补默认来源 |
| `negative_mass` / `zero_mass` | `INVALID_MASS`；不填默认质量 |
| `nonfinite_mass` / `nonfinite_mesh_scale` | `NONFINITE_NUMBER`；拒绝非有限字段 |
| `invalid_inertia` | `INVALID_INERTIA_DIAGONAL`；仅拒绝已知负对角项，不证明其他惯量物理有效 |
| `missing_joint_limit` | `MISSING_JOINT_LIMIT`；不推断限位 |

guard 故意只检查此 corpus 对应的静态边界：不是完整 JSON schema/URDF parser；尚不覆盖任意损坏字段、所有几何、拓扑、非对角惯量、真实碰撞或数值稳定性。完整质量/惯量合法性、惯性积符号、坐标变换、平行轴、刚体合并和关节推断均留 A01/A02/A04。正对角项不能当作惯量正定/物理可实现证明。

## 质量属性数组金样

入口：`fixtures/sw-mass-properties.json`，版本 `webots-mcp-mass-array-goldens/v1`。三份 raw 是人为挑选的唯一槽位哨兵值，`real_sw_export=false`、`sw_version=null`；非零惯性积用于分辨槽位，不宣称它是上面的均质盒或实测张量。

| 具体 SW 方法 | 官方数组契约 | 坐标来源 / 限制 |
|---|---|---|
| `IModelDocExtension.GetMassProperties2(Accuracy, Status, UseSelected)` | 12项；质心XYZ、体积、面积、质量、MomXX/MomYY/MomZZ/MomXY/MomZX/MomYZ | 零件默认坐标；装配按装配坐标。惯量在质心、与输出坐标轴对齐；保留UseSelected/Status |
| `IBody2.GetMassProperties(Density)`，solid body | 12项，与上面相同槽位 | 实体建立时的坐标；从装配组件取body不代表质心已转到装配坐标。sheet/wire的第3/4项含义不同，不套用solid布局 |
| `IModelDocExtension.GetMassProperties(Accuracy, Status)` | 13项；前12项相同，最后Accuracy | 旧扩展方法；不能与12项方法混同 |
| `IModelDoc2.GetMassProperties()` | **布局未核实**；官方该页只给无参/已弃用信息 | 源码两个入口实际调用此旧方法；不据扩展方法文档宣称旧方法数组已确认 |

`MomZX` 原样记录；本阶段不将其解释为URDF惯性积符号，不做坐标或平行轴转换。通用 [API Units 2024](https://help.solidworks.com/2024/english/api/sldworksapiprogguide/Overview/Units.htm?id=1.4.71) 列 m/rad/kg/m²/m³；体积/面积/惯量单位据此明确为 m³/m²/kg·m²。A01仍需真实方法调用和版本捕获验证。

当前源码事实：`sw_bridge.py:1873` 的 CLI 把 raw[0..3]解释为体积/面积/质量/密度，并只取5个惯量；`swapi.py:5238`把同名旧方法前6项解释为质心XYZ/体积/面积/质量。两种解释不同，S02只记录冲突，不改旧读取核心。逐实体降级路径只是体积×密度估算，不能充当已捕获质心/惯量。

## 旧包复用审计

旧目录：`C:/Users/j1877/Documents/Codex/2026-10-09/ban-2/outputs/webots-mcp`。本次只加载其标准库 `common.inspect_urdf` 并读取代码；未加载 server/controller，未调用 converter，未启动任何仿真。对本批20个URDF输入审计后，输入字节未改变；额外 package:// 临时探针可规范化。结果在控制目录 `evidence/S02/old-parser-audit.json`。

| 可复用 | 本次证据 / 采用边界 |
|---|---|
| 路径根限制、缺mesh、XML/DTD、重复link/joint和显式非法质量/惯量拒绝 | 同一故障样例能被旧解析器拒绝；可沿用防护经验与标准库做法 |
| 本地 package URI | 纯格式探针当前成功；新resolver集成仍需单独检查根目录/缺包策略 |
| 官方 converter 参数和设备样例 | 只作历史经验；旧 VALIDATION.md 记录的转换/MCP 11项不继承为本轮通过数 |

已确认缺口：旧解析器接受 nonfinite mesh scale、缺少关节limit；不读取外部单位/坐标/组件实例元数据。其惯量正定检查也不是物理可实现的完整证明。旧server内TCP controller、固定世界/启动与当前extern worker路线不同，不能整体迁入；worker关键恢复仍由A04负责。未直接复制旧完整核心。

## 官方来源与核验边界

检索日期均为2026-10-29；SW按具体方法页面区分；Webots固定R2025a；ROS官方文档为rolling，样例有意只用URDF 1.0已有字段。

- [SW GetMassProperties2 / IModelDocExtension，2024](https://help.solidworks.com/2024/english/api/sldworksapi/solidworks.interop.sldworks~solidworks.interop.sldworks.imodeldocextension~getmassproperties2.html)：12项布局、选取范围、输出坐标及质心惯量。
- [SW GetMassProperties / IBody2，2024](https://help.solidworks.com/2024/english/api/sldworksapi/SolidWorks.Interop.sldworks~SolidWorks.Interop.sldworks.IBody2~GetMassProperties.html)：Density、实体类型差异、局部来源。
- [SW GetMassProperties / IModelDocExtension，2024](https://help.solidworks.com/2024/english/api/sldworksapi/SolidWorks.Interop.sldworks~SolidWorks.Interop.sldworks.IModelDocExtension~GetMassProperties.html?format=P&value=)：13项含Accuracy。
- [SW GetMassProperties / IModelDoc2，2021](https://help.solidworks.com/2021/English/api/sldworksapi/SolidWorks.Interop.sldworks~SolidWorks.Interop.sldworks.IModelDoc2~GetMassProperties.html)：旧方法无参数，页面不提供布局。
- [ROS2 官方可动URDF教程原文](https://raw.githubusercontent.com/ros2/ros2_documentation/rolling/source/Capabilities/Simulation/URDF/Building-a-Movable-Robot-Model-with-URDF.rst)：revolute的rad限位、prismatic的m限位及显式effort/velocity。
- [Webots Physics，R2025a](https://raw.githubusercontent.com/cyberbotics/webots/R2025a/docs/reference/physics.md)：质量、质心、惯量与boundingObject的真实语义；本轮没有Webots物理验收。

访问限制：SW 2025动态页本次只返回导航，改用可检索的官方2024正文；docs.ros.org对应教程被Anubis拒绝，使用ROS2官方仓库原文替代。

官方文档另有单位表述冲突：IMassProperty 的 [Mass Properties总览2024](https://help.solidworks.com/2024/English/api/sldworksapiprogguide/Overview/Mass_Properties.htm?id=a4ca2b0e71ea418bb71f5111aa5cb8a1) 写grams，而 [UseSystemUnits专页2026](https://help.solidworks.com/2026/English/api/sldworksapi/SolidWorks.Interop.sldworks~SolidWorks.Interop.sldworks.IMassProperty~UseSystemUnits.html) 写kilograms。不能把总览句子移植到当前具体数组方法；真实捕获前保留这项A01核对事项。

本阶段没有验证SW原生导出、URDF→PROTO、Webots物理、DSH端到端、持久身份恢复或真实设备。下一阶段应消费这些金样/反例和来源字段，不能消费一个虚构的物理PASS。
