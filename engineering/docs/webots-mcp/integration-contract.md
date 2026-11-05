# S01 最小接入与数据合同 v0.1

本合同服务已授权 sol S03–S08 的外围开发。依据冻结 PLAN.md，不实现或替代 Astra A01–A04 的坐标/惯量、关节/身份、归因/回写、物理有效性与关键动作恢复。未来核心未接入时必须显示“尚未实现”，不能用假 worker 宣称 READY。

## 目录与责任

| 内容 | 唯一路径 / 责任 |
|---|---|
| MCP 与外围模块 | `engineering/tools/webots_mcp/`，S03；命名保持与已有 tools 包并列，不新建通用插件平台 |
| 输入夹具 / 索引 / 检查 | `engineering/tests/webots_mcp/`，S02；来源与解析金样不冒充真实 SW/Webots |
| 文档 | `engineering/docs/webots-mcp/`；S01 三份合同文档，S02 input-matrix，后续任务只新增其自身文档或在合并时协调 |
| 配方/场景静态源 | 后续按需置 `engineering/tools/webots_mcp/recipes/`、`scenes/`，S06；不提前创建空模块 |
| 运行状态投影 | `Path(state_dir()) / 'webots_state.json'`；单独命名，不覆盖 mode/workflow/connection 状态 |
| 作业账本 | 同目录 `webots_jobs.sqlite3`（及 SQLite 自有 -wal/-shm）；sqlite3 标准库。A04 负责持久接收、去重、派发及崩溃边界核心，S03 仅 fake/未实现承载 |
| 仿真实例锁 | 同目录 `webots_instance.lock` 预留归属；单实例、跨进程正确性由 A04 实现验收，不以文件存在证明锁有效 |
| 运行报告 / 证据 | `Path(state_dir()) / 'webots_runs' / run_id /`，S04；与代码包 webots_mcp 分开 |

run_id、job_id、operation_id 必须为受限单段标识符；解析后仍验证输出在 state_dir 内，拒绝 `..`、绝对路径和 reparse/symlink 逃逸。源模型为只读，生成文件进入本 run 输出；不得覆盖 SW 原件。路径/扩展名/有限数字/资源预算等信任边界在派发前检查。

## state_dir 与隔离环境

真实 Python 顺序为：**已存在的 DSH_STATE_DIR → 安装 tools → DSH_ENGINEERING_ROOT 推导 tools → _root.tools_dir() → _store 所在目录**。不会因为缺少状态文件改去另一个目录，也不会自动创建显式目录。Python 缓存有效目录；改环境后需 reset_cache 或重启子进程。Node findToolsDir 也认显式 DSH_STATE_DIR。

因此仅设置 DSH_ENGINEERING_ROOT 或 DSH_HOME 不足以防止回到用户安装目录。每次测试必须先建立并验证隔离的 DSH_STATE_DIR，回报解析后的绝对路径；不存在或解析偏离期望时在开始任何写入/派发前失败。不得调用 migrate_state 搬入真实运行现场。

S03/S07 的隔离目录建议固定在控制目录 `C:/Users/j1877/Documents/Codex/Webots-MCP-20261029/`：

| 设置 | 隔离值 |
|---|---|
| DSH_HOME | `<control>/isolated-dsh`，只在需要运行宿主接入时建立；不能是用户 `.dsh` |
| profile | `webots-test`；插件目标仅 `<DSH_HOME>/profiles/webots-test/node_modules/dsh-engineering-ui` |
| DSH_STATE_DIR | `<DSH_HOME>/.agent-presets/engineering/tools`；先建目录，再让 Python/Node 同时读取；预演前同样显式指定 |
| DSH_ENGINEERING_ROOT | 当前验收源码副本的 `<repo>/engineering`，不承担状态目录选择 |
| Python 候选 | `<control>/runtime/.venv/Scripts/python.exe`，需要时由后续任务建立并 smoke check；S01 未创建或安装 |
| cwd | 当前验收源码副本的 `<repo>/engineering/tools`，绝对路径 |
| args 候选 | `['-B', '-m', 'webots_mcp.server']`，由 S03 实现入口 |
| WEBOTS_HOME | `D:/Webots`，显式传入；实际 exe/launcher 的存在、版本和哈希另报 |
| 编码 | `PYTHONIOENCODING=utf-8`、`PYTHONUTF8=1`；禁把业务日志写 stdout |

未来调用同步时仅允许以进程私有环境赋值 `DSH_HOME=<isolated-dsh>`，使用 `sync-to-dsh.ps1 -DryRun -Profile webots-test`；不要修改用户/系统环境变量。原脚本即使 DryRun 也会 New-Item 目标目录；故只对隔离 DSH_HOME 运行，并在命令前验证所有解析目标包含于该隔离根。正式同步、真实安装重启另听用户指挥。

## 锁、房间与 DSH preset

1. 纯仿真状态/查询/动作仍绑定 task/room，但不连接 SW、不申请 SW FIFO、不启动 SW guard；SW 导出/改参才复用原 SW 锁。不能把“豁免 --room”和“豁免 SW 锁”绑定为同一个标志。
2. 当前 sw_bridge 先按 `_NO_LOCK_CMDS` 等决定是否走 `_gate_sw_lock`，后按 `_NO_SW_CMDS` 决定 get_sw。只加 `_PURE_COMPUTE_CMDS` 不能跳过 SW 锁。S07 若加入 sim-*，同时检查 help、参数剥离、房间归属、锁路径、SW 初始化与分发。
3. 不复用 `DSH_SW_LOCK_OFF` 全局关闭 SW 互斥；不要因仿真而更改已有 SW 释放策略。现锁默认单命令释放，`DSH_SW_UNLOCK_ON_EXIT=0` 才保持旧行为。
4. 活跃 preset 是插件中的 `preset-engineering.patch.yml` 的 engineering config.plugins；一行 `@deepseek-ai/dsh-mcp-client` 挂一个 serverName=webots 的 stdio server。保持 `failOnStartupError: false`，不重复声明宿主 permission 服务。
5. MCP 配置显式传 command/args/cwd/env；所有 DSH_* 经过宿主清洗后必须显式配置。房间/任务身份不硬编码成全局 preset 常量；每次请求携带 task_id/room_id，并由 S07 接入真实上下文校验，S03 fake 检查不等同授权证明。
6. 既有连接区 `connection_state.json.webots.connected` 仅表示检测到安装或进程；单独展示作业状态、快照时间和 stale。UI 新路由复用当前认证/限流，读取报告必须验证 task/room 所有权。

## 依赖与传输

数据/文件/哈希/作业承载/日志优先 json、pathlib、hashlib、sqlite3、subprocess 等标准库；MCP 协议复用 Python MCP SDK，转换复用官方 urdf2webots，不手写第二套通用协议或转换器。当前全局解释器无 mcp/urdf2webots；旧独立环境的 1.30.0/2025.0.0 只是候选，不当成本轮依赖已安装或转换已通过。

官方 dsh-v0.2.1-rc.3 已核实配置与环境合并，[来源索引见 environment-index](environment-index.md)。stdio stdout 只输出换行 JSON-RPC；日志走 stderr/文件。协议协商由 SDK 处理，不硬编码未经协商的 MCP tasks 扩展。宿主重连重启 server 时只重新发现工具/查询已有作业，不自动重发有副作用动作。

## 统一外层返回

以下字段先冻结语义；工具具体参数与约 12–18 个工具名由 S03 按原规格最小收口，设备/电机/传感器能力不可被字段位姿编辑替代。

| 字段 | 语义 |
|---|---|
| schema_version | 初版 `1`；这是外围承载合同版本，不是 MCP protocolVersion |
| ok / error_code | 请求结果与机器可读错误；核心未接入用 CORE_NOT_IMPLEMENTED，有歧义用 OUTCOME_UNKNOWN；不填假成功 |
| run_id / job_id | 运行与长作业身份；尚无运行时为 null |
| request_id | 本次请求关联，不等于可跨崩溃证明“恰好一次” |
| operation_id | 有副作用操作的持久身份；同 ID 不同参数拒绝。A04 冻结具体账本实现 |
| session_generation | reset/reload/重新连接现场后区分 handle 世代；未建立现场为 null |
| execution_state | `not_started / running / completed / unknown`；unknown 可能已执行，禁止盲重发 |
| sim_time / completed_steps | 已确认仿真秒数/完成步数；无实测值为 null，不能以 0 冒充已测 |
| task_id / room_id | 与当前上下文和报告所有权绑定；缺失身份不得派发变更 |
| evidence | 本 run 内相对路径/证据 ID 索引，附类型/hash；不直接信任外部任意路径 |
| hint | 下一项最小动作或缺失证据说明 |

状态查询额外给 snapshot_at、stale、job_state、core_ready、unsupported；缓存查询不排在阻塞 worker 动作后。job_state 为 NEW/PREPARING/STARTING/READY/RUNNING/PAUSED/STOPPING/COMPLETED 或 FAILED/CANCELLED/ORPHANED/UNKNOWN。READY 需要真实握手和身份确认；S03 fake 必须标 provenance=fake、core_ready=false，不冒充真实 READY。

MCP cancellation 是请求停止；不能等同动作撤销或进程已退出。持久接收先于变更派发，落盘失败不得派发；执行后未落账窗口保留 unknown。S03/S05 只提供接口与反例夹具，关键串行队列/去重/取消/归属/恢复实现交 A04。

## S04 报告承载

`webots_runs/<run_id>/` 下固定承载 `model_manifest.json`、`conversion_report.json`、`experiment_report.json`；日志在 `logs/`，截图/采样在 `evidence/`。静态校验可先有部分报告，缺失的源、数值、映射和物理结果填 null/unknown，并列 missing_information，不能编造默认质量或唯一映射。

公共头：schema_version、run_id、task_id、room_id、source_revision、created_at、provenance、status、evidence。provenance 区分 fixture/fake、static-derived、real-sw-export、real-webots；status 使用 supported/approximate/unsupported/unknown 等适合该记录的明确值。

- model_manifest 承载 source 文件/配置/hash、输入单位/坐标来源、组件实例及持久引用、原始质量属性/位姿、刚体和关节来源。保留 raw 值与方法标签，S04 不计算 A01/A02 结果。S02 fixture 索引的 source.units、coordinate_frame、components[id,instance_path,configuration,persistent_reference,urdf_link] 可原样嵌入，不升级其证据级别。
- conversion_report 承载输入/输出/hash、工具版本、源到输出映射、近似/合并/省略、支持范围、校验结果。静态校验通过不等于物理保真已通过。
- experiment_report 承载模型/场景/hash、动作、步长/种子/初态/预算、观测、问题和判定。问题含 observed/derived/suspected、候选零件集合、数值/单位/门限/时步/位置、证据与缺口；S04 不写 A03 根因算法或 A04 物理 PASS 门槛。

证据写入用同目录临时文件+replace，失败显式返回，日志有界和分页。后续 S07 同步排除必须新增 webots_state.json、webots_jobs.sqlite3 及 -wal/-shm、webots_instance.lock、webots_runs/，避免复制运行数据；现有脚本尚未支持这些新名字，S01 仅记录。

## 验收与未覆盖

S01 交付目录/版本/状态锁与最小数据合同；S02 提供输入支持矩阵与解析夹具；S03/S04 独立验证承载；S05 故障与资源；S06 配方；S07 隔离接入；S08 汇总交接。真实 SW 自动导出、物理变换/关节/归因/恢复、真实运行与完整回 SW 闭环留后续阶段，不借本合同提前计通过。
