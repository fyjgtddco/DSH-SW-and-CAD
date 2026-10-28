# 重构设计：总计子代理 / 总理子代理 / 子代理状态可信化

> 本文件是 2026-10-28 这次改造的**唯一契约**。所有改代码的人先读这里。
> 源码根：`C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering`（只改这里，不碰 `output/` 安装副本）

## 一、用户反馈的三个问题（有会话证据）

会话存档：`session-31c56a28`（主对话 + 3 个子代理）

1. **总计子代理没被真正启动**。主对话自己用 `mode2 begin-stage / mark-stage-done / ask / answer-question`
   把十阶段走完了；`set-subagent-plan` 也是主对话自己调、只写了 `--decided-by "总计子代理"` 这个字符串。
   根因：`skills/mode-selection/SKILL.md` 末尾写着
   “所有门禁步骤必须在主对话完成，不要进子代理”“Subagents are ONLY for actual modeling work”，
   与“第 3 步启动总计子代理”直接矛盾；persona 里 `反夺舍` 也禁止小屋调 mode_gate/workflow_gate。
2. **房间数/波次还是旧的固定表**。`select` 的 wave1 固定为
   `structural/transmission/housing/support/spring/thermal/corrosion`，默认房间表固定 5 个；
   `prime`（总理子代理）类型**不在任何一波**里，所以总理永远不会被启动。
   用户要求：房间数与细分由总计子代理决定；总理子代理与建模小屋**同波同时启动**，并持续盯着它们。
3. **主对话分不清子代理死活**。`platform-sync` 只有主对话手动写；主对话还开了个
   `while(true) platform-sync running` 假心跳；`subagent-assign` 从没调过；
   `recover` 因此把它们判成“僵尸残留”，紧接着 `reset` 把还在跑的小屋全清了。
   SW 锁排队中的小屋与“死了”看起来一样。

## 二、目标架构（必须按这个实现）

### 阶段 A：总计子代理（analyst）真实存在

- `provide_context #2` 之后，**主对话必须先 spawn 一个子代理「总计子代理」**，把十阶段分析交给它。
- 总计子代理开工第一件事：跑 `python <根>\tools\mode2_flow.py analyst-bind`
  → 从环境变量 `DSH_SESSION_ID` 取自己的 sessionId，落盘 `plan[current]["analyst"]`。
- 十阶段命令（begin-stage / mark-stage-done / ask / answer-question / refresh-stage / set-subagent-plan）
  **只接受 analyst 自己的 sessionId**（比对 `DSH_SESSION_ID`，或显式 `--actor`）。
  主对话直接调 → `ANALYST_ACTOR_REQUIRED` 拒绝。
- 提问：总计子代理用 `tools/ask_user.py --room 总计子代理 --child <自己的 sessionId> --question ...`
  走宿主代问桥（阻塞、答案以 tool_result 返回），**不要自己编答案**。
- 分析完 → `set-subagent-plan` 登记房间编制 → 结束回合（结果里带编制摘要）。

### 阶段 B：建模小屋 + 总理子代理同波启动

- 主对话拿 `select` 的 wave1 结果（= 编制里的全部建模房间 **+ 总理子代理**），
  **一次性 fork 全部**，每个 fork 后立刻 `subagent-assign <房间> <id>`。
- 总理子代理是**长生命周期监工**：
  - 开工：`mode2_flow.py prime-board` 看全局；`prime-room` 看职责；
  - 循环：每轮读 `mode_gate.py room-board --json`，对每个建模小屋的
    `state`（running / waiting_sw / starting / unbound / dead / done）做判断，
    对 `waiting_sw` 只能等、不许判死；对 `unbound` 提示父代理补 assign；
  - 收件：各建模小屋 `save()` 后 `prime-report`；总理 `check-consistency` → `prime-verify`；
  - 结束：wave3 出图完成后 `room-end 总理子代理`。
- prime 房间**不计入 wave 完成判定**（长生命周期），但收尾前必须 room-end。

### 阶段 C：状态可信化

- 插件 host 半自动维护 `<toolsdir>/platform_agents.json`：
  `{updatedAt, source, agents:[{id, label, parentId, createdAt, status:'running'|'inactive'|'disposed', lastEventAt}]}`
  —— 监听 `agent/created` / `agent/turn-stopping` / `agent/disposed`，原子写、节流。
- `mode_gate._platform_liveness(room)` 读取顺序：
  1) 手动 `platform_status.json`（600s 内，显式覆盖，保留）；
  2) 插件 `platform_agents.json`（自动，按已绑定 subagent id 或 label 包含房间名匹配）；
  3) None。
- `mode_gate.room-board [--room X] [--json]`：唯一权威看板。每房间输出
  `state`（`starting|running|waiting_sw|idle|done|failed|dead|unbound`）、
  `detail`、`subagent_id`、`subagent_label`、`platform`、`report_stage`、
  `sw_queue_pos`、`safe_to_restart`、`next_action`。
- `recover`：平台说 alive 且未绑定 → 归 `unbound`（不是僵尸），给下一步（补 assign），**绝不含 reset**；
  只有平台说 `inactive/disposed/missing` 且无产物证据、且超宽限，才判 `dead`。
- `room-status` 保持旧字段兼容，但内部走 board 的判定。

## 三、命令契约（最终）

```
mode2_flow.py analyst-bind [--session-id X] [--work-dir D]   # 在总计子代理里跑；不传则读 DSH_SESSION_ID
mode2_flow.py analyst-status                                  # 谁绑的、什么时候
mode2_flow.py prime-board [--json]                            # 总理看板：房间表 + 上报缺口 + 一致性
mode2_flow.py set-subagent-plan --rooms-json ... --rationale ... --decided-by 总计子代理 [--actor X]
mode2_flow.py begin-stage|mark-stage-done|ask|answer-question|refresh-stage ... [--actor X]
mode_gate.py room-board [--room X] [--json]                   # 唯一权威看板（别名 board）
mode_gate.py platform-refresh                                 # 从 plugin 注册表刷新（只读→合成视图）
mode_gate.py recover                                          # 不再误判 unbound 为僵尸
```


## 四、实现状态（2026-10-28 落地）

| 契约项 | 落点 | 状态 |
|---|---|---|
| analyst-bind / analyst-status | `mode2_flow.py` | ✅ 已实现；平台注册表存在时只允许真实子代理绑定 |
| 十阶段命令身份校验 | `mode2_flow.py` | ✅ 主对话调用返回 `ANALYST_ACTOR_REQUIRED` |
| select 前置：analyst 已绑定 | `workflow_gate.py` | ✅ 未绑定返回 `MODE2_ANALYST_REQUIRED` |
| 编排命令仅 root 会话 | `workflow_gate.py` | ✅ 子代理调用返回 `ROOT_SESSION_REQUIRED` |
| wave1 = 建模房间 + 总理 | `workflow_gate.py` | ✅ prime 进入 wave1、长生命周期不阻塞波次、收尾前必须 room-end |
| prime-board | `mode2_flow.py` | ✅ 房间状态 + 上报缺口 + 待办 |
| room-board / room-status | `mode_gate.py` | ✅ 9 态 + `safe_to_restart` + `next_action`；`waiting_sw` / `unbound` |
| recover 不再误杀 | `workflow_gate.py` | ✅ 改为复用 room-board；unbound 单列、禁止 reset |
| 插件自动平台登记 | `plugins/dsh-engineering-ui/lib/index.js` | ✅ `platform_agents.json`（created / turn-stopping / disposed） |
| sw_bridge 活房间集合 | `sw_bridge.py` | ✅ 加入 `waiting_sw` / `idle` |
| 人设 / 技能 / 文档 | persona、mode-selection、assembly-orchestration、README | ✅ 旧规则已删，grep 为 0 |
| 回归测试 | `tools/test_regression_*.py` | ✅ 新增 analyst_gate / room_board；全套 30/30 通过 |

> 生效方式：代码 → `sync-to-dsh.ps1`（已执行）；**插件 host 半与 preset 需重启 DSH 桌面端**，
> client 半刷新页面即可。

## 五、明确不做的事

- 不删除旧的 `platform-sync`（保留为人工覆盖），但不许主对话再拿它造假心跳；persona 里删掉“每 60s 心跳”。
- 不动 `ask_user.py` / `defense_gate.py` / `physics/`；`sw_bridge.py` 只改了活房间集合一处（+waiting_sw/idle）。
- 不改 `output/` 安装副本；改完由 Lead 统一 `sync-to-dsh.ps1`。
