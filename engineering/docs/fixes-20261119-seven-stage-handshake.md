# 七阶段分析 · 开工前握手卡死（2026-11-19 · 第二轮）

> 现场：DSH 会话 `session-c9c2fd56-50c5-4e5e-8732-343c733b2473`
> 任务：`20261119_113825_A37604C4`（单级圆柱齿轮减速器）
> 结局：11:37:33 开工 → 11:44:58 才真正跑起来（**7.4 分钟**白白耗在握手上），
> 之后 41 分钟三个分析角色空等，12:26 用户手动中断。
> 上一轮：[fixes-20261119-seven-stage-slow-1of63.md](./fixes-20261119-seven-stage-slow-1of63.md)

---

## 一句话结论

**上一轮修好了"分析中"的通道，这一轮暴露的是"分析前"的握手。**
三条错误提示互相矛盾、且都不可操作，模型只能靠 grep 源码自救 —— 15 个回合全花在这上面。

> 说明：12:26 之后的 `trae: transport error` 与长时间静默是**用户手动中断**，不是缺陷。
> 本文只分析中断之前（11:37:33 ~ 12:26:00）的链路。

---

## 时间线

| 时刻 | 事件 | 判读 |
|---|---|---|
| 11:37:33 | turn 1 开始，用户下达任务 | — |
| 11:38:09~11:39:13 | mode-selection → init → 两轮问卷 → provide_context ×2 | 正常，4 分钟 |
| 11:39:12 | `mode2_flow.py analyst-guard` → **invalid choice** | ❌ 命令早已随十阶段删除，**preset 却还在教它** |
| 11:39:23 | `subagent_fork` 建总计子代理 | — |
| 11:39:27 | `attach` with `messageIds: []` → **ROOT_USER_MESSAGE_NOT_AVAILABLE** | ❌ 见根因① |
| 11:39:27~11:43:46 | 主对话 **15 个回合** grep 源码 / 翻目录 / 读 asar | ❌ 全在排障，零推进 |
| 11:39:27 | planner 抢先 `launch` → **WRONG_WORKFLOW_ROOT** | ❌ 见根因② |
| 11:40:39~11:43:25 | planner 连等 3 次 wait_agent（60/60/90s） | ❌ 见根因④ |
| 11:43:53 | `attach` with `{"planner":"planner"}`（不传 messageIds）→ **成功** | 同一件事，换写法就好了 |
| 11:44:04 | planner 重新 launch → **成功**，三角色会话建好 | — |
| 11:44:08 | planner 照 `next` 去 `invite` → **WRONG_PHASE: frame** | ❌ 见根因③ |
| 11:44:58 | planner 提交 frame，流程进入 knowledge | 这时才真正开始 |
| 11:44:42~12:26:20 | **三个角色各调一次 `wait_agent(600000)`，全部空等 ~41 分钟** | ❌ 见根因④ |
| 12:26:00+ | 用户手动中断 | 不计入缺陷 |

**代价统计**：真正用于分析的时间 = **0**；握手与空等 = **46 分钟**。

---

## 根因

### ① `attach` 的 `messageIds: []` 把"没指定"当成了"指定了空"

```js
// lib/index.js（修复前）
const wanted = Array.isArray(data.messageIds) ? data.messageIds : original.slice(-1).map(m=>m.id);
const requirements = wanted.map(id=>original.find(m=>m.id===id));   // [] → []
check(requirements.length && requirements.every(Boolean),'ROOT_USER_MESSAGE_NOT_AVAILABLE');
```

模型按 preset/SKILL 的模板原样写 `{"planner":"...","messageIds":[]}`，
`wanted` 就是空数组，于是必然失败。**而这个错误码一个字都没说要传什么 id** ——
主对话只能去 grep、翻 `~/.dsh`、甚至试图读 `app.asar`（路径还不存在）。

而 11:43:53 那次**不传 messageIds 就成功了** —— 说明代码里本来就有"退回最近一条"的逻辑，
只是被"显式传了空数组"这个写法绕过去了。

### ② attach 未落地时的 `WRONG_WORKFLOW_ROOT` 是错误的名字

```js
if (!['status','read','export'].includes(action)) {
  const wf=currentWorkflow(taskId);
  check(wf.seven_stage_root_session_id===(prior?.root ?? a.id), 'WRONG_WORKFLOW_ROOT');
}
```

`prior` 为 null（任务还没 attach）时，退化成比 `wf.root === a.id`，对 planner 当然不等。
于是**"还没接上"被报成了"身份不对"**。planner 拿到这个码后判断"不可恢复"，
还专门发消息向主对话报告 —— 又浪费一个回合。

### ③ `launch` 的 `next` 指了一条会被拒的路

`launch` 内部已经跑完 `invite`（三个 `childForRole` 都已预留），
但它返回的 `next` 仍照抄 setup 阶段的文案「invite → 为每个角色创建独立二级子代理」。
planner 照做 → `WRONG_PHASE: frame`。

**根因不在文案，在 `at('setup')` 写在了幂等分支前面**，
导致"已经 invite 过"这个事实永远走不到幂等出口。

### ④ `wait_agent` 与插件的驱动模型对不上

宿主的 `wait_agent` 语义是「等**下一个 step** 的输入」：

```ts
const offInbox = ctx.on('agent/inbox/inserted', ...)   // 收到消息就返回
```

但实测：11:44:58 任务包已经插入 knowledge 的 inbox（`agent/inbox/spliced` seq 42），
而 11:44:42 开始的 `wait_agent` **并没有被唤醒**，一直等到 12:26:20 超时。

原因是插件的投递走 `subagents.prompt({delivery:'queue'})` ——
消息进的是 **next-turn 队列**，不打断当前 step。两套模型错位：

| 谁 | 期望 |
|---|---|
| 插件 | 投递消息 = 唤醒角色 |
| 角色 | `wait_agent` = 等到新任务 |

结果就是三个角色各睡 41 分钟、零产出。

### ⑤ `seven-status` 恒返回空（连带把主对话带进沟里）

`workflow_gate._seven_stage_state()` 只认环境变量 `DSH_SEVEN_STAGE_STATE_DIR`，
而插件实际用的是 `<workflow_state 同级>/seven_stage`。
两处口径不一致 → `seven_progress: {}`。

现场 11:44:29 主对话查进度，看到的就是"什么都没有"，
自然会怀疑"分析根本没启动"。

---

## 修复

| # | 位置 | 改法 |
|---|---|---|
| ① | `lib/index.js` attach | `messageIds` 为空数组 / 全无效 → **退回最近一条真人原文**；彻底找不到时列出**可选 id 清单**（id + 60 字预览） |
| ② | `lib/index.js` 权限前置 | attach 未落地时报 `TASK_NOT_ATTACHED`（含完整顺序指引）；`WRONG_WORKFLOW_ROOT` 只在**任务已存在但根会话对不上**时抛，并给出两边真实值；`attach` 自身豁免该检查（它就是在创建状态文件） |
| ③ | `lib/flow.js` invite | `at('setup')` 移到幂等分支**之后**；`phase!=='setup'` 时幂等返回邀请码状态 + 说明 |
| ④ | `lib/index.js` notify | 通知文案明确写「**不要用 wait_agent 等待任务包**」+ 正确姿势（`status` → 有活就干 → 没活就 `yield` 结束回合） |
| ⑤ | `tools/workflow_gate.py` | `_seven_stage_state()` 改走 `seven_stage_gate.state_dir_for()`（env → 同级 `seven_stage`），与 Node 侧 `resolvePaths()`、`read_design()` 三处逐字一致 |
| ⑥ | preset + SKILL | 删掉已随十阶段删除的 `analyst-guard` / `analyst-status` 指令，改指 `seven-status` / `room-board`；写明 `messageIds` 可省略、`launch` 后别 invite、别用 `wait_agent` 等任务包 |

---

## 回归

```powershell
node engineering\plugins\dsh-engineering-seven-stage\test\smoke.mjs
python engineering\tools\test_regression_seven_stage_wiring.py
python engineering\tools\test_regression_wiring_audit.py
python engineering\tools\test_regression_preset_keys.py
```

| 测试 | 结果 |
|---|---|
| `smoke.mjs` | ✅ OK |
| `test_regression_seven_stage_wiring.py` | ✅ **PASS=165 / FAIL=0**（上轮 156 + 本轮 9 项） |
| `test_regression_wiring_audit.py` | ✅ PASS=51 / FAIL=0 / WARN=0 |
| `test_regression_preset_keys.py` | ✅ PASS=18 / FAIL=0 |

本轮新增的行为断言：
- attach 前 launch 必须是 `TASK_NOT_ATTACHED`，且报错含 `action=attach`；
- attach 传 `messageIds:[]` 能成功，且落盘的 requirements 是**真人原文**（不是 tool 输出）；
- 三角色 bind 后重复 invite 幂等不报错、带说明；
- `seven-status` 在未设环境变量时也能读到状态文件。

---

## 仍然存在的边界（宿主侧，本插件改不了）

`wait_agent` 不打断当前 step，是**宿主 `@deepseek-ai/dsh-tool-subagent-control` 的设计**：

```ts
// src/wait-agent.ts
const offInbox = ctx.on('agent/inbox/inserted', ({ agent: recipient }) => {
  if (recipient === agent) onInput()
})
```

它监听的是 `agent/inbox/inserted`，而插件用 `subagents.prompt({delivery:'queue'})` 投递时
走的是 `agent/inbox/spliced`（写进 next-turn 队列）。
两者不是同一个事件，所以"投递成功" ≠ "唤醒等待"。

插件侧的缓解是**不让角色去等**（见 ④），但如果宿主希望 `wait_agent` 真的能被
`delivery:'queue'` 唤醒，需要在宿主那个事件上补一条 —— 属于 DSH 本体改动，不在本仓库范围。

---

## 部署

```powershell
.\sync-to-dsh.ps1 -Prune
python engineering\tools\verify_sync.py
# 然后【完全退出并重开 DSH】（插件 host 半是进程级）
```
