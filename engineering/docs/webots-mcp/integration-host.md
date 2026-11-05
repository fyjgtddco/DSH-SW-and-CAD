# S07 工程模式隔离接入

本阶段在既有工程插件中接入可选 Webots stdio 配置、真实上下文守卫和只读报告/日志入口。A01–A04 尚未实现；默认 `CORE_NOT_IMPLEMENTED`、`core_ready=false`，没有真实作业 READY、物理 PASS、SW 转换或自动回写结论。

## 配置与状态根

有效配置位于 `engineering/plugins/dsh-engineering-ui/preset-engineering.patch.yml` 的 engineering `config.plugins`；新增一行 `@deepseek-ai/dsh-mcp-client`，命名空间 `webots`。默认禁用，只有宿主进程私有环境 `DSH_WEBOTS_ENABLE=1` 才启用。`failOnStartupError=false`，不声明第二份宿主 permission 服务。旧 `agent.cordis.yml` 不承担该接入。

配置使用目标 `dsh-v0.2.1-rc.3` loader 的 `!!js` 环境表达式，不写入开发者绝对路径。启动前应明确提供以下值，并预建状态目录；不能依赖安装副本回退：

| 环境 | 要求 |
|---|---|
| `DSH_WEBOTS_PYTHON` | 已具备冻结 MCP 依赖的绝对 Python 路径；本轮只读借用 S03 验证过的独立解释器，未安装全局依赖 |
| `DSH_ENGINEERING_ROOT` | 当前验收副本的 engineering 绝对目录；cwd 为其 tools 子目录 |
| `DSH_WEBOTS_INPUT_ROOT` | 既存的只读模型输入根；由 S03 检查相对输入和解析后边界 |
| `DSH_STATE_DIR` | 既存绝对目录；Node 与 Python 必须解析到同一路径 |
| `DSH_HOME` / profile | 单独隔离 DSH_HOME 和 `webots-test` profile，不能为真实用户 `.dsh` |
| `WEBOTS_HOME` | 显式 Webots 安装根；本轮核对为 R2025a，不等于本次启动过 |

stdio args 为 `-B -m webots_mcp.server --input-root <输入根>`，UTF-8 环境显式传入。宿主先清洗环境，再合并配置 env，因此 DSH_HOME、DSH_STATE_DIR 和工程根都在配置中明确传入。没有 task/room 常量、`DSH_SW_LOCK_OFF` 或生产 fake-worker 开关。

本轮测试所有宿主边界、同步和回退均显式隔离环境；没有启动/重启真实 DSH，没有同步原安装、桌面原源或 output Junction。真实 DSH loader 装配、环境清洗后的真实进程、当前桌面进入工程模式等结论仍未验证；配置求值测试及官方源码核对不能代替该实测。

## 动态身份与锁

宿主复用官方全局 `ctx.tools.guard`。每个 `mcp__webots__*` 执行必须携带真实 `exec.agent`，并由现存 `ctx.agents.get(agent.id)` 返回同一 live 对象；预设必须为 engineering。然后只读共享状态：

1. `workflow_state.json.task_id` 为当前 task。
2. `mode_state.json.rooms[房间]` 必须 active 且明确属于同一 task。
3. `mode_state.json.subagents[房间].id` 必须等于调用代理身份，且唯一绑定。
4. 请求 task_id/room_id 必须与宿主推导值相同。串房、过期、缺失、重复绑定、伪造 agent 对象全部拒绝。

所有房间统一使用 `room-` 加 **原房间名 UTF-8 字节的完整 SHA-256** 作为 room_id，中文/ASCII 走同一映射。room_label 保留原名。不能给 ASCII 名称开直通域，否则可与中文编码后的名称碰撞。这个 ID 不建立第二份状态或权限表，也不将模型的自报身份视为授权。

未绑定房间的根代理返回 `CONTEXT_UNAVAILABLE`；本版不猜全局任务、不把其他房间授权给根代理，也不允许为了读取而重写/补造房间状态。报告需由绑定的 live 工程房间访问；已结束或已撤销绑定保守拒绝。这是当前支持边界。

未加入 sw_bridge 的 sim-* 命令，纯仿真入口不调用 SW、SW FIFO 或 guard。现有 SW 修改/导出仍按原共享锁路径执行。`webots_instance.lock` 只按 S01 预留和排除同步；真正独占实例/动作去重/恢复仍由 A04 实现，不能把宿主身份守卫当作实例锁。

## 复用连接区的只读接口

沿用 `lib/index.js` 的 `guardRoute`：现有 loopback、Origin/Referer 和限流包装均生效。没有另造鉴权或连接管理体系。既有 conn-state/probe 仍表示安装/进程可用，不能据此生成仿真 READY。

路由前缀为 `/dsh-engineering-ui/`，全部只接受 GET：

| 路由 | 查询 | 返回 |
|---|---|---|
| `webots-context` | sessionId | 动态 task_id、room_id、room_label、实际 state_dir 与保守未执行快照 |
| `webots-report` | sessionId、run_id、report_type | data 为 S04 完整报告；类型仅 model_manifest / conversion_report / experiment_report |
| `webots-evidence` | sessionId、run_id，可选 name / offset / limit / expected_sha256 | data 为 S04 原始 UTF-8 日志页；limit 4..16384，字节游标与快照 hash 保留 |

上下文快照为 `provenance=unavailable`、`execution_state=not_started`、`job_state=NEW`、`stale=true`、无实测时间/步数，并列 unsupported。这是本发布没有执行核心的声明，不是假读真实 worker 状态。

服务器从 sessionId 解析 live agent，逐请求推导归属；拒绝额外 task_id/room_id、重复查询键、非法单段 ID、页界及 hash。S04 再核对 run 自身归属、证据 hash 和路径边界。报告数据与候选集合原样承载，不推断根因。

读取使用标准库异步 execFile，15 秒截止、2 MiB 输出上限、隐藏子进程窗口；只调用 `webots_mcp.host` 中的 S04 读取器，不启动 MCP/Webots/SW。等待期间不阻塞其他状态请求，返回数据前再次校验 live/task/room/state。绑定中途变更则 403，无旧报告 data。

常见响应：403 CONTEXT_UNAVAILABLE / OWNERSHIP_MISMATCH；404 EVIDENCE_NOT_FOUND；400 INVALID_ARGUMENT / EVIDENCE_INVALID；503 READER_UNAVAILABLE。现有本机路由守卫的同账户安全边界保持原样，不声称隔离恶意本地进程。

## 同步、备份和回退

`sync-to-dsh.ps1` 新增排除 `webots_state.json`、`webots_jobs.sqlite3`、`-wal`、`-shm`、`webots_instance.lock` 和任意层级 `webots_runs/`。既有 mode/workflow/connection 等运行排除仍保留。复制代码不能覆盖现场、作业、锁或报告。

DryRun 仍可能创建目录，必须提前核对显式隔离 DSH_HOME/profile。测试使用 Windows PowerShell 5.1，并为子进程清除继承自 PowerShell 7 的 PSModulePath，让 Windows PowerShell 自行建立兼容模块路径；否则 Get-FileHash 可能不可用，旧脚本会吞掉该错误。源脚本的通用哈希错误处理未在本任务扩大修改。

可复现隔离步骤：

1. 建新的隔离 DSH_HOME、`profiles/webots-test` 及显式 DSH_STATE_DIR。记录目标绝对路径，确认其包含于预期隔离根，无 Junction 逃逸。
2. 保存目标已有文件清单/hash；执行 `sync-to-dsh.ps1 -DryRun -Profile webots-test`，核对文件内容/hash不变，目录创建单独记录。
3. 在隔离副本执行同步，逐文件比对源码/hash，并核对运行文件未变、备份包含旧字节；再次同步验证幂等。
4. 回退时只按本次清单恢复已覆盖文件的准确 `.bak-sync-*`，逐项校验旧 hash；删除本次新建且仍属于清单的代码文件。保留备份和运行现场。若同名备份或文件 hash 不明确，停止覆盖，保留证据。
5. 正式部署或重启另听用户指挥。本版没有自动对真实安装执行这些动作。

控制根 `evidence/S07/verify_actual_sync.py` 对本任务源码执行上述文件级验证，记录隔离 home/profile/state、命令、关键文件 hash、回退清单与结果；空目录可保留，不声称恢复整个文件系统元数据或实际宿主运行现场。最终提交对应的文件哈希以 final 同步记录和 handoff 为准。

## 检查与证据

- `node --test engineering/plugins/dsh-engineering-ui/test/webots-host.test.mjs engineering/plugins/dsh-engineering-ui/test/webots-preset.test.mjs`：真实上下文、异步子进程、归属撤销、现有宿主 apply 及路由守卫边界、配置求值。
- 使用冻结 SDK 解释器运行 `python -B -m unittest discover -s engineering/tests/webots_mcp -p "test_*.py" -v`：既有全部安全子系统检查，加 S04 读取器及真实同步脚本隔离夹具。
- `node --test --test-force-exit engineering/plugins/dsh-engineering-ui/test/*.mjs`：完整现有插件断言。旧测试会留下计时器，所以用 runner 的 force-exit；此结论不等于宿主卸载/退出通过。729ee18c 原始归档对照 100 测 97 过 3 失败，旧 Map 事件适配器覆盖同名后续 listener 是其根因；本版修正两个测试适配器，保留生产守卫。

控制证据保留 RED/GREEN、真实 SDK 既有回归、同步/回退、源字节、peer 及完整日志；不把夹具、VM/apply 或 Python 子进程成功计为真实桌面 E2E。未启动真实 SW/Webots、未实现物理核心、未执行真实安装或硬件验证。

官方资料于 2026-10-30 重核并归档 URL、tag/版本、访问时间及内容 SHA-256：

- [DSH MCP config](https://raw.githubusercontent.com/deepseek-ai/deepseek-harness/dsh-v0.2.1-rc.3/packages/mcp/mcp-client/src/index.ts) 与 [transport](https://raw.githubusercontent.com/deepseek-ai/deepseek-harness/dsh-v0.2.1-rc.3/packages/mcp/mcp-client/src/transport.ts)。
- [DSH tools guard / ToolExecution](https://raw.githubusercontent.com/deepseek-ai/deepseek-harness/dsh-v0.2.1-rc.3/packages/core/tools/src/index.ts)。
- [DSH loader expressions](https://raw.githubusercontent.com/deepseek-ai/deepseek-harness/dsh-v0.2.1-rc.3/vendor/loader/src/config/utils.ts) 与 [disabled evaluation](https://raw.githubusercontent.com/deepseek-ai/deepseek-harness/dsh-v0.2.1-rc.3/vendor/loader/src/config/entry.ts)。
- [Webots R2025a extern](https://raw.githubusercontent.com/cyberbotics/webots/R2025a/docs/guide/running-extern-robot-controllers.md)。
- [MCP 2025-11-25 transport](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)。

官方 tag 源码与本机打包客户端字节的对应仍未证明；资料访问不代替本机真实运行。

## I01 integrated offline entry

The optional engineering preset forwards an operator-provided absolute `DSH_WEBOTS_RUNTIME_CONFIG` as `--runtime-config`. With no config, status and owned evidence reads remain available. The existing room authorization applies to `sim_attribute` and the host attribute route. The current session readiness is separate from historical job completion. A context projection from another room or past its validity time cannot report readiness.

For a trusted, explicit state directory and a part-local A01 snapshot, run from `engineering/tools` with the pinned requirements installed:

```text
python -B -m webots_mcp.pipeline publish-part --state <absolute-state> --task <task> --room <room> --run <fresh-run> --snapshot <capture.json>
python -B -m webots_mcp.pipeline convert-part --state <absolute-state> --task <task> --room <room> --run <run> --operation-id <id>
```

The offline publisher writes an approximate model manifest and conversion report through S04. The converter only accepts a published preview from the operator-controlled store. Each run has one conversion attempt. A failed attempt keeps its files for diagnosis, and a new attempt needs a new run. Check `precision.expected`, `precision.observed`, and `precision.changed` in the report. The audit is an exact comparison of emitted numeric fields in the single-link format. An omitted field is marked changed. The report always retains the collision omission and `physical_validation=not_run`. Windows path separators emitted by the pinned converter are checked against the one expected relative mesh path.

`sim_convert` for native integration and `pipeline native-status` return `CALIBRATION_REQUIRED`. Native mesh frame, global mate completeness, persistent identity, calibrated feedback and authorized repair remain required. A standalone offline preview does not provide those inputs.

Converter behavior was checked against the [Cyberbotics 2025.0.0 importer](https://raw.githubusercontent.com/cyberbotics/urdf2webots/2025.0.0/urdf2webots/importer.py) and the installed 2025.0.0 wheel on 2026-11-05. The URDF-to-PROTO subprocess checks used synthetic part captures.
