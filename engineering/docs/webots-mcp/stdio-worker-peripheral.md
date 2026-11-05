# S03 stdio / worker 外围 v0.1

S03 交付官方 MCP SDK stdio 入口、精简工具 schema、统一返回和显式 fake 作业夹具。真实 worker 核心尚未实现，默认动作返回 `CORE_NOT_IMPLEMENTED`。所有返回均带 `core_ready=false`，无实测 `sim_time/completed_steps` 为 `null`。官方样例探针是独立证据脚本，不是产品 worker。

## 启动与依赖

Python 3.13.15 / MCP 1.30.0 已验证；固定依赖见 `engineering/tools/webots_mcp/requirements.txt`。MCP 为 MIT 许可，协议与 JSON schema 校验复用 SDK 及其现有 jsonschema 依赖（本次4.26.0），没有手写 JSON-RPC。依赖安装可使用控制目录内独立 venv；本次只读借用旧 venv，未安装/升级全局或旧环境。`urdf2webots` 尚未被此外围调用，转换工具返回核心未实现，不提前增加转换依赖。Webots controller SDK 来自本地 R2025a 安装；pywin32 312 只用于证据脚本的自有进程保护，不是此包依赖。

调用方先建立独立状态目录，显式设置 `DSH_STATE_DIR`、`PYTHONIOENCODING=utf-8`、`PYTHONUTF8=1`；cwd 为验收副本 `engineering/tools`，以绝对 Python 路径运行：

```text
python.exe -B -m webots_mcp.server --input-root <只读模型根目录>
```

`python.exe` 在实际配置中必须换成绝对路径。入口复用 `_store.state_dir()`，拒绝不存在的显式目录及解析不一致，避免回退至安装现场。stdout 只给 SDK 协议，业务日志走 stderr。没有创建作业数据库、实例锁、运行状态投影或迁移用户状态。

测试时才加 `--fake-worker complete|error|timeout|unknown`。每次返回 `provenance=fake`；fake 的 completed 只代表夹具返回，绝不代表 Webots READY、物理 PASS 或真实清理。timeout/unknown 均返回 `OUTCOME_UNKNOWN/execution_state=unknown`，保留作业和操作身份，查询仍为 unknown。夹具没有真实延时、线程、进程、队列或持久恢复。

## 工具表与请求边界

17个工具共用 task_id、room_id、request_id；单段ASCII标识符最多96字符。run_id/job_id/operation_id 同样受限。JSON schema 与运行时共用同一正则，拒绝末尾换行、非有限数字、布尔世代/步数、额外参数。真实 DSH 上下文鉴权由 S07 接入，fake 的相等检查不构成鉴权证明。

| 工具 | 额外输入 / 语义 |
|---|---|
| sim_status | 无；读取外围可用性，含快照时间/stale/unsupported |
| sim_launch | world_path、operation_id；相对 input_root 的既存 .wbt |
| sim_convert | urdf_path、operation_id；相对 input_root 的既存 .urdf |
| sim_shutdown / sim_reset | run_id、session_generation、operation_id |
| sim_step | 同上及 steps=1..1000 |
| sim_scene / sim_devices | run_id、session_generation |
| sim_field | 同上及 node、field；只读 |
| sim_screenshot / sim_contacts | run_id、session_generation、operation_id；contacts另需node |
| sim_motor | 同上及device、有限velocity、可选有限position |
| sim_sensor | 同上及device；预留采样，不用位姿修改替代传感器 |
| sim_job | run_id、session_generation、job_id；查询自有夹具作业 |
| sim_cancel | 同上及operation_id；即使fake也拒绝伪造取消成功 |
| sim_report | run_id、report_type；读取S04三种报告之一 |
| sim_evidence | run_id；可选name、offset、limit=4..16384、expected_sha256；读取S04日志页 |

路径必须相对输入根、存在、扩展名匹配；拒绝绝对路径、`..`、Windows drive/反斜杠和解析后逃出根的 symlink/junction。外围不读写 SW 源模型内容。报告读取交给 S04 的 EvidenceStore，复用其run归属/路径/hash/有界UTF-8分页；缺失、越权、坏格式、I/O错误显式分类返回。外层 provenance 描述本外围的执行来源，原报告的 provenance 保留在 data 内，不提升证据级别。

## worker 接缝与 A04 边界

`PeripheralServer.dispatch` 在派发前校验 schema、有限值与输入根；显式 fake 才创建 `FakeWorker`。fake 保存 task/room/run/generation/job 的内存关系，身份或世代不符先拒绝；operation ID 重用一律 `OPERATION_REUSED`，不会返回伪造的持久去重成功。重启即丢失这些夹具，不应据此重发动作。

长动作承载使用自有 job_id，通过 sim_job 查询；不依赖未协商 MCP tasks。A04 仍须实现持久接受先于派发、真实握手/身份确认、同worker串行队列、参数冲突去重、执行后未落账的unknown窗口、缓存快照、非阻塞取消、世代失效、归属清理和崩溃恢复。接入这些核心前，真实工具继续明确未实现；不要把此内存夹具扩写成恢复算法。

## 当前验证与限制

标准库 unittest 覆盖默认拒绝、身份/路径/有限值/预算边界、fake作业与错误/超时/unknown、重放拒绝、取消/重置未实现；官方SDK客户端真实子进程完成 initialize/list/call，默认和四种fake均校验过。S04桥接在临时组合目录测试报告、跨房间拒绝、中文/emoji分页、旧hash与坏报告，未修改或复制S04文件到本产品分支。运行：

```text
python.exe -B -m unittest discover -s engineering/tests/webots_mcp -v
```

S03 单独分支在未合入 S04 时，可用 `WEBOTS_MCP_EVIDENCE_MODULE` 指向已审查的 S04 evidence.py 运行桥接测试；否则该联合检查明确 skip，不能算已集成。最终共享树需重新跑完整子系统suite。历史DSH/SW实机回归不属于本次离线suite。

官方R2025a Supervisor样例副本探针：首次文件重定向使日志为空，目标IPC文件实际已创建；依原规格§3.2改持续排空PIPE后，经父明确授权只纠正复验一次。复验保留同world SHA，官方webots-controller实连，观察8个32ms步进、仿真时间0→0.256s、normal quit请求返回、两启动器exit0及专用Job关闭前active0；清理复查无残留。**安装缺少 TexturedBackground / RectangleArena PROTO，Webots日志明确跳过它们**；该结果仅证明extern/步进/退出链，完整样例环境、碰撞、物理有效性、设计PASS均未验证。没有再启动应用、SW或DSH。

证据在控制目录 `evidence/S03/`，初次失败、纠正复验、SDK统计、RED/GREEN及官方源哈希分别保留。直接导入SDK只完成非构造smoke；官方launcher已真实连接，直接构造方式未测试。探针进程保护仅为该有界诊断设计，不是A04生产清理实现。

官方联网核查日期2026-10-29/30，固定tag及哈希见控制证据 official-sources.json：

- [MCP v1.30.0 SDK Server](https://raw.githubusercontent.com/modelcontextprotocol/python-sdk/v1.30.0/src/mcp/server/lowlevel/server.py)：list/call和stdio入口。
- [Webots R2025a extern controllers](https://raw.githubusercontent.com/cyberbotics/webots/R2025a/docs/guide/running-extern-robot-controllers.md)：官方launcher、port和robot选择。
- [启动选项](https://raw.githubusercontent.com/cyberbotics/webots/R2025a/docs/guide/starting-webots.md)、[Supervisor API](https://raw.githubusercontent.com/cyberbotics/webots/R2025a/docs/reference/supervisor.md)、[官方样例](https://raw.githubusercontent.com/cyberbotics/webots/R2025a/projects/samples/devices/worlds/supervisor.wbt)：探针依据，联网本身不算实测。
- [官方webots-server的PIPE排空示例](https://raw.githubusercontent.com/cyberbotics/webots-server/main/async_process.py)：读取两个流与进程退出的现有做法；main未固定版本，仅作旁证。
- [官方问题7040](https://github.com/cyberbotics/webots/issues/7040)：Windows Python controller重定向退出线程问题；本探针没有启用controller stdout-redirect/stderr-redirect，不能用该问题解释首轮的启动器捕获缺陷。
