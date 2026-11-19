# BUG-seven-stage-tool-gate

> 记录日期：2026-11-18 · 状态：**P0 / P1 / P2 已修复并加回归**
> 相关：[`七阶段换代_合并说明.md`](./七阶段换代_合并说明.md)

---

## P0 · `SEVEN_STAGE_TOOL_GATE` 把真实主对话的全部工具一起锁死，整个任务卡死

### 现象

`engineering_seven_stage(action="attach")` 成功（返回 `revision:1, phase:"setup"`）之后，
**主对话自身**的 `pwsh` / `read` / `write` / `grep` / `list_agents` / `todo_write` /
`ask_user_question` 全部被拒，返回同一句 `SEVEN_STAGE_TOOL_GATE`。

### 后果链路

```
无法运行 workflow_gate.py / mode_gate.py  → 门禁无法推进
无法调用 write                            → 连"把 bug 写进文件"这个自救动作都做不到
无法调用 ask_user_question                → 连"请人介入"都做不到
唯一出路只剩让七阶段自己跑完 63 个小步才能自动解锁
```

### 根因（源码）

```js
// lib/index.js（修复前）
function enforce(exec) {
  const a = actorOf(exec.agent);
  if (!a) return;
  const bound = flow.trackedActor(a);
  if (!bound || exec.name===TOOL) return;   // ← 只要"被接管"就一律拦
  return 'SEVEN_STAGE_TOOL_GATE: ...';
}
```

`attach` 会把**真实根会话本身**登记成 `role='root'` 成员，于是
`trackedActor(rootActor)` 返回真值，主对话被当成"分析期间会话"一并限制。
而 `mode-selection` 技能明确承诺「普通根会话与建模小屋不受影响」——**实现与文档相反**。

### 修复

```js
function enforce(exec) {
  const a = actorOf(exec.agent);
  if (!a) return;
  const bound = flow.trackedActor(a);
  if (!bound || exec.name===TOOL) return;
  if (bound.role === 'root') return;        // ← 真实主对话放行
  return 'SEVEN_STAGE_TOOL_GATE: ...';
}
```

口径（**不要改回去**）：

| 角色 | 是否限制 | 理由 |
|---|---|---|
| `root` | **永不限制** | 它是用户的真实主对话：跑门禁、读写文件、提问、todo 都是本职 |
| `planner` / `knowledge` / `design` / `review` | 限制 | 分析角色，只能用插件工具 |
| `unbound` | 限制 | 已被创建但还没 bind 的二级会话 |
| 其它会话（建模小屋、普通根） | 天然不受影响 | `trackedActor` 返回 null |

### 附带修复（同源，不修会继续咬人）

| 位置 | 原行为 | 现行为 |
|---|---|---|
| `agent/turn-stopping` | root 结束回合被 steer「结束请求未登记」，两次后 `flow.interrupt(root)` 把 root 记进 `failures` | **root 直接 return** —— 用户正常回一句话不该导致任务被标故障 |
| `agent/disposed` | root 会话被释放（换窗口/重开）就把任务判成中断 | **root 直接 return** —— 状态文件还在，重进同一会话即可继续 |
| 工具内部 `check()` 抛错 | 异常冒到宿主，模型只看到 "tool failed"，看不到可操作指引 | `execute()` 统一把异常转成 `{ok:false, code, error, note}`，指引能真正到达模型 |

---

## P1 · `subagent_fork` 建的总计子代理一旦自行结束，`attach` 就永久不可能成功

### 现象

首个 planner（`767a61f8…`）在 `attach` 落地前就结束了；此时 `ctx.agents.get(planner)`
已查不到该 actor，原校验必然失败，且**不可恢复**——只能重新 fork。
最终换 `planner2`（`e4d561f6…`）并在它存活期间立即 attach 才成功。

### 根因

```js
// 修复前
check(typeof data.planner==='string' && actorOf(ctx.agents.get(data.planner))?.parent===a.id,
      'REAL_DIRECT_CHILD_REQUIRED');
```

只认「此刻仍活着且 parent === root」。而子代理结束回合后就会从 `agents` 消失，
**但宿主谱系里通常还留着记录** —— 这条本可用于放行的证据被忽略了。

### 修复

证据按可信度排序，任一成立即放行：

1. `ctx.agents.get(planner)` 仍活着且 `parent === root`（原路径，保留）；
2. `ctx.subagents.listDescendants(root)` 里仍有 `kind='child'`、`id === planner` 的记录。

两者都拿不到 → **照旧拒绝**（不采信模型自报）。

### 附带：报错改成可操作的

原来只回一个 `REAL_DIRECT_CHILD_REQUIRED` 代码。现在把正确做法直接写进消息：

> 把 `subagent_fork` 与 `engineering_seven_stage(action=attach)` 写在
> **同一条回复**里连续执行，并在初始 prompt 里明写
> 「收到 attach 通知前不得结束回合」。如果它已经结束，请重新 fork 一个新的子代理。

同时 `mode-selection/SKILL.md` §3.6 补了 **两条铁律**（同一回合、prompt 里明写不许结束），
以及 `wait_user` 动作（root 表示"这一回合在等用户回答"，不必冒充 `yield`）。

---

## P2 · `mode-selection` 技能文档身份判据自相矛盾

### 现象

开头写「若你是 `subagent_fork` 派出来的子代理（`delegationDepth>0`），
**不要执行门禁流程**」，但同一份文档又把 `attach` / `capture` 规定为
**"只能由真实主对话调"**。子代理按字面理解会误判自己该不该走门禁。

### 修复

改成**四类身份 + 一个判据表**，每类只回答一个问题："我这一回合该不该跑门禁？"

| # | 判据 | 结论 |
|---|---|---|
| 1 | `DSH_SESSION_ID` == `workflow_state.seven_stage_root_session_id` | **主对话** → 跑门禁 |
| 2 | 父会话是主对话，且被 `subagent_fork` 创建 | **总计子代理** → 不跑门禁，只跑七阶段 |
| 3 | 父会话是总计子代理，且在插件里 `bind` 过 | **知识/设计/反驳** → 不跑门禁，只跑七阶段 |
| 4 | 在 `mode_gate` 的房间表里 | **建模小屋** → 不跑门禁，只建本房间 |

并显式澄清：**"子代理不跑门禁" ≠ "子代理不参与门禁"** ——
总计子代理与三个二级角色是流水线里必须存在的一环，只是它们不调 `workflow_gate`。

---

## 回归（锁死这三条，防止复发）

`engineering/tools/test_regression_seven_stage_wiring.py` 新增 **F3 段**：

| 断言 | 防的是 |
|---|---|
| attach 后主对话可调 `pwsh/read/write/grep/list_agents/todo_write/ask_user_question` | P0 |
| 分析角色（planner + 三个二级）**仍然被锁** | P0 反向：别把豁免写宽 |
| 建模小屋 / 无关会话不受影响 | P0 反向 |
| `bound.role === 'root'` 出现在 `enforce()` 里 | P0 实现漂移 |
| `turn-stopping` / `disposed` 对 root 直接 return | P0 附带 |
| 已结束的直系子会话仍可 attach；伪造的仍被拒 | P1 |
| 报错含"同一条回复"、"重新 fork" | P1 可操作性 |
| `execute()` 把异常转成 JSON（不崩宿主） | 附带 |
| 技能文档含四类身份判据、不再写"子代理一律不跑门禁" | P2 |

---

## 教训

1. **"接管"必须精确到角色，不能按"是否被登记"一刀切。** 主对话一旦被登记进
   分析任务，就同时拥有了"分析角色"和"用户代理"两重身份；后者必须永远畅通。
2. **门禁的失败模式要分等级。** 分析没做完只该挡住"下一步"，**不该挡住
   "报告问题"和"求助"** —— 那会把可诊断的失败变成不可诊断的死锁。
3. **"可选能力"绝不能有连坐面。** 参见 [`七阶段换代_合并说明.md` §5.3](./七阶段换代_合并说明.md)：
   同一个插件之前因为配置缺失抛异常，导致整个工程预设挂载失败、
   `/dsh-engineering-ui/agents` 重复注册、工程模式打不开。
4. **文档里的"承诺"要能对照出实现。** P2 的矛盾写法根因是文档按"段落"演进，
   没人把"身份"这一类事实收敛成一张表。
