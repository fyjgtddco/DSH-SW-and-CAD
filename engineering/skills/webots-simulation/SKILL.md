---
name: webots-simulation
description: Use when an engineering task requests Webots simulation, robot drive or sensor experiments, simulation reports, collision evidence, or tracing a simulation issue to a SolidWorks assembly. Not for strength, fatigue, FEA or arbitrary 2D-to-3D reconstruction.
---

# Webots simulation

This release supplies sol peripheral contracts, stored reports and bounded fixtures. Real execution, CAD conversion and safe return-to-SW cores A01–A04 are **not implemented**. Do not claim a completed engineering loop.

Read [integration-contract](../../docs/webots-mcp/integration-contract.md) for state/context and [report-viewer](../../docs/webots-mcp/report-viewer.md) for the live reader contract. Use [input-matrix](../../docs/webots-mcp/input-matrix.md) for supported input and [recipes-scenes](../../docs/webots-mcp/recipes-scenes.md) when preparing a bounded experiment.

1. Discover the actual MCP server/tools; configured serverName is `webots` (`mcp__webots__sim_status`, `sim_report`, `sim_evidence`, `sim_job`). A missing tool is unavailable, never a prompt to emulate successful execution. Supply the **live authenticated** task/room binding and a request ID, not copied fixture IDs. The UI reader obtains it from `/dsh-engineering-ui/webots-context?sessionId=<live agent id>`; 403 `CONTEXT_UNAVAILABLE` means no authorized read. Never create a room or rewrite workflow state to bypass it.
2. Read status and current owned reports first. The connection green light only proves installation/process availability. Keep `provenance`, `core_ready`, `execution_state`, `snapshot_at`, `stale`, `unsupported`, source revision and missing information visible. `fake`, `fixture`, `synthetic`, static validation, screenshots and empty contacts do not prove physical PASS or READY. `supported` describes support, not an experiment verdict.
3. Use `sim_report` with `run_id` and one of `model_manifest`, `conversion_report`, `experiment_report`. Use `sim_evidence` for bounded worker-log pages: forward `next_offset` and the prior `sha256` as `expected_sha256`. A changed snapshot requires a fresh offset 0; do not concatenate different snapshots. Keep all candidate components and observed/derived/suspected evidence levels; a suspected or merged-body candidate is not a root cause.
4. Prepare, rather than dispatch, currently unsupported experiments. Capture source/configuration/hash, model/world versions, units/frame sources, collision coverage, initial state, drive, sensor observations, seed/step and resource/stop budgets. Generated terrain worlds have no robot or calibrated physics. Missing mass, inertia, mapping or thresholds remain unknown, not invented defaults. Coordinate/inertia A01, joint/identity A02, attribution/SW changes A03 and physical/worker recovery A04 require their real implementation and verification.
5. On `OUTCOME_UNKNOWN`, disconnect or action timeout, preserve operation/job/run/generation and the last confirmed progress; read status/job and evidence. Do **not** resend the action, even after reconnect. Cancellation acknowledgement does not prove rollback or process exit. Cleanup only verified session-owned processes; never terminate all Webots processes.

SW changes require current source revision and unique persistent instance identity, evidence-based attribution, authorized parameter scope, snapshot/rollback and same-condition retest. This version cannot satisfy that chain: report the missing core and stop automatic modification. A user request to investigate is not permission to select an arbitrary candidate or change gravity, mass, friction, collision switches or pass thresholds to manufacture improvement.

## 与工程模式主流程的接口（D1 接通说明）
上面那段是原交付自带的「S07 阶段」说明，保留作证据口径。下面是**本仓库实际接入**后的走法，
两者冲突时以本节为准（本节描述的是已经落地的代码）。

### 接口面
| 方向 | 接口 | 位置 |
|---|---|---|
| 模型进仿真 | `webots_mcp.pipeline publish-part` / `convert-part` | `engineering/tools/webots_mcp/` |
| 归属授权 | `resolveWebotsContext()` + `ctx.tools.guard` | `engineering/plugins/dsh-engineering-ui/lib/webots.js` |
| 只读证据 | `/dsh-engineering-ui/webots-context|-report|-evidence|-attribute` | 同上，复用 `guardRoute` |
| 设置页状态 | 连接区「Webots连接」（只显示安装/进程可用） | `lib/client.js` |
| 报告面板 | 组件 `EngWebotsPanel`（**已从设置页移除入口**，组件保留可复用） | `lib/client.js` |
| 人设约束 | persona 的「## 仿真（Webots，可选）」段 | `preset-engineering.patch.yml` |
| 模式入口 | mode-selection 的「🟦 Webots 仿真分支」 | `engineering/skills/mode-selection/SKILL.md` |

### 三个必须记住的耦合点
1. **身份不是猜的**：`task_id` 来自 `workflow_state.json`，`room_id` 来自
   `mode_state.json` 里 `active=true` 且 `subagents[房间].id == 当前 agent.id` 的**唯一**房间。
   所以 —— 仿真必须发生在 `select` 之后、`room-end` 之前；房间已经收尾就没有合法身份了。
2. **不抢 SW 锁**：仿真链路不调 `sw_bridge`，因此不会进 `mode_gate` 的 FIFO 队列。
   但**生成输入的那一步**（SW 导出）照旧要走原来的 SW 锁，不能借仿真名义绕开。
3. **失败要说失败**：`RUNTIME_NOT_READY` / `CALIBRATION_REQUIRED` / `CONTEXT_UNAVAILABLE`
   都是**正常返回值**，不是异常。把它们改写成"成功"是本项目最严重的一类 bug。

### 当前能力边界（2026-11-05 实测）
| 能做 | 不能做 |
|---|---|
| 离线把合成/真实零件捕获转成 URDF + PROTO（`urdf2webots 2025.0.0` 已跑通） | 原生 SW→URDF 的坐标系/惯量标定（A01） |
| 读已存报告与日志证据（字节分页 + SHA-256） | 自动关节推断（A02，`blocked_native`） |
| 归属校验、代际、操作幂等 | 自动根因回写 SW（A03/A04） |
| `sim_status` 等只读查询 | 真实物理 PASS 判定（本版恒 `physical_validation=not_run`） |

### 快速自检（不碰 DSH、不碰 SW、不碰 Webots）
```powershell
& $python -B -m unittest discover -s engineering/tests/webots_mcp -p 'test_*.py'   # 101 项
node --test --test-force-exit engineering/plugins/dsh-engineering-ui/test/webots-*.test.mjs
```

For isolated tests, first create and verify an existing absolute `DSH_STATE_DIR`; explicitly isolate `DSH_HOME`, profile and engineering root. Do not migrate live state or sync/deploy/restart the real installation. Preserve raw evidence and its hash, and distinguish local fixtures, the bounded historical Webots probe, real CAD evidence and unexecuted work in the result.
