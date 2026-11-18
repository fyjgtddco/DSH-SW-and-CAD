---
name: mode-selection
description: Task mode selector with mandatory workflow gate for design tasks
whenToUse: Every design task must pass through this skill before any work begins
disable-model-invocation: false
user-invocable: true
source: engineering
provider: filesystem
---

# Task Mode Selector with Code-Enforced Workflow Gate

## ⛔ 先自查：你是不是被主对话派出来的子代理？

运行 `echo $env:DSH_SESSION_ID`，或看自己的 system prompt：
**如果本次对话是父会话用 `subagent_fork` 派出来的子代理（delegationDepth>0），
不要执行本技能的门禁流程**，按主对话给你的房间任务直接干活：

| 你的身份 | 允许做什么 | 禁止做什么 |
|---|---|---|
| **建模小屋** | 本房间建模、验证、`room-report`、`room-artifact` | `init` / `provide_context` / `select` / `confirm-assembly` 等门禁命令 —— 门禁已在主对话完成 |
| **总计子代理** | 只调 `engineering_seven_stage`（launch / frame / platform commit / question / interpret / propose_rooms / end） | `init` / `provide_context` / `select`（那是主对话的活） |
| **知识 / 设计 / 反驳（二级）** | 只调 `engineering_seven_stage`（bind / status / search / calculate / submit / yield / blocked / end） | 任何 shell / 文件写入 / 旧门禁 / 再委派 —— 插件会 `SEVEN_STAGE_TOOL_GATE` 拒绝 |

> 实测教训（会话 `session-31c56a28`）：三个建模小屋各自加载本技能后又跑了一遍
> `init → provide_context → …`，把主对话的分析状态覆盖成四份。
> 现在代码侧已加两道闸：`ROOT_SESSION_REQUIRED`（子代理调编排命令直接拒绝）
> 与七阶段插件的 `SEVEN_STAGE_TOOL_GATE`（分析会话只能用插件那一个工具）。

## Rules (Code-Enforced, Not Just Prompts)

**Rule 0: Any design work MUST go through this gate first.**（仅限主对话）

## Step 1: Analyze Task Type

| Feature | Mode | Architecture |
|---------|------|--------------|
| Single part, simple geometry | **Mode 1: Basic Part Building** | Single-thread direct modeling |
| Multi-part assembly, mechanism, transmission | **Mode 2: Complex Assembly** | Multi-room subagent collaboration |
| User sent image/screenshot | **Mode 3: Image Analysis** | Single-thread recognition rebuild |
| 用户要**仿真 / 运动学 / 碰撞 / 传感器 / 机器人跑起来**（不要求新图纸） | **附加动作，不是新模式** | 见下方「Webots 仿真分支」 |

> 🟦 **仿真不是第四种模式**：它挂在已有模式的输出端。先照旧判定 1/2/3 并走完该走的门禁，
> 再按下面的「Webots 仿真分支」把**已交付的模型**送进仿真。**绝不允许**因为"要仿真"
> 就跳过建模/出图，也绝不允许用仿真结果反过来替代三大防线。

## Step 2: Output Mode Declaration

Must output BEFORE any design work:
```
[MODE SELECTED] Mode 1 / Mode 2 / Mode 3
```

## Step 3: Mode-Specific Execution

- **Mode 1**: Direct modeling, NO subagent allowed
- **Mode 2**: Must use assembly-orchestration skill, MUST create subagent rooms
- **Mode 3**: Single-thread image analysis, NO subagent allowed

## 🔴 Mode 2 强制门禁流程（用命令行脚本，不是 DSH 工具！）

> ⚠️ **【BUG-03/04 修复 · 必读】**
>
> 旧版本文档把门禁入口写成了各种**门禁类 DSH 工具名**，照那些名字去调用必然失败。
> **本环境没有任何门禁类 DSH 工具；真实入口一律是命令行脚本** ——
> 照旧文档调用会直接失败（工具不存在），任务在第 1 步就卡死。
>
> **真实入口是命令行脚本**（下面所有命令都用这个）：
>
> ```powershell
> $WG = "<工程模式根目录>\tools\workflow_gate.py"
> ```

---

### 🔴 【BUG-01 修复】`<工程模式根目录>` 到底是什么？怎么找？

旧文档只写占位符 `<工程模式根目录>` 却从未定义它 —— 用户按字面理解常指向
`C:\Users\<user>\.dsh\skills\mode-selection\`（那里**只有 SKILL.md**），
于是第 1 步就 `[Errno 2] No such file or directory`，新用户直接卡死。

**判定标准**：工程模式根目录 = **同时包含 `tools\mode_gate.py` 与
`tools\workflow_gate.py` 的那个目录**。

**先跑这个自检**（打印根目录 + 全部门禁脚本路径 + 候选列表）：
```powershell
python "<任一已知的 tools 目录>\_root.py"
```

**三种可靠找法（按优先级）**：
1. **环境变量**（最稳，推荐设一次）：
   ```powershell
   $env:DSH_ENGINEERING_ROOT = "C:\Users\<你>\Desktop\DSH-SW-and-CAD-main\engineering"
   # 永久设置：setx DSH_ENGINEERING_ROOT "C:\...\engineering"
   ```
   设好后所有门禁脚本都会优先用它（见 `tools\_root.py`）。
2. **从脚本自身位置向上回溯**：找到 `engineering\tools\` 后，其**上一级**即根目录。
3. **常见备选搜索路径**（找不到时依次试）：
   - `%USERPROFILE%\.dsh\.agent-presets\engineering\`（**安装副本，最权威**，实测常见）
   - `<仓库根>\engineering\`
   - `%USERPROFILE%\.dsh\engineering\`

⚠️ 注意：`.dsh\skills\*` 下**只有 SKILL.md，没有 tools/** —— 那不是工程模式根目录。

下面所有 `<工程模式根目录>` 都请替换成上面找到的**绝对路径**。

### ⚠️ 读取含中文的 JSON 凭据：别用 PS 5.1 的 `Get-Content`

Windows PowerShell **5.1** 的 `Get-Content` 默认按系统 ANSI 代码页（简中 = cp936）
解码，UTF-8 的 JSON 会被误解码，`ConvertFrom-Json` 随即报
**「传入的对象无效，应为":"或"}"」**。**这不是文件坏了，是读法不对。**
文件名/内容含中文时尤其容易踩（`gate_load_case.json`、`mode_state.json` 等都可能这样）。

- ❌ `Get-Content x.json -Raw -Encoding UTF8 | ConvertFrom-Json`
  （PS 5.1 下 `-Encoding UTF8` 不足以保证按 UTF-8 解码）
- ✅ 首选：`python -c "import json,io;print(json.load(io.open(r'x.json',encoding='utf-8')))"`
- ✅ 或：直接用 **DSH 的 `read` 工具**读该文件（自动按 UTF-8 解码）
- ✅ 坚持用 PowerShell：先 `[Console]::OutputEncoding=[System.Text.Encoding]::UTF8`
  与 `$OutputEncoding=[System.Text.Encoding]::UTF8`，再用 `Get-Content -Encoding UTF8`
  （或改用 PowerShell 7 `pwsh`，默认即 UTF-8）

---

**Mode 2（大型复杂器械装配）任务，必须在主对话中用 pwsh 调用以下命令：**

> ══ ★【流程已重排 · 2026-10-13】第一步不再是"参数需求强度" ★ ══════════
> 用户明确要求：**第一步 = 确认模式 + 问并行/串行 + 问是否 AI 自主设计**；
> 参数需求强度题**后移为第 2 步**；第 3 步创建【总计子代理】并接上七阶段插件（63 个小步），
> 真人澄清由插件按决策缺口触发。原"第一步（多子代理并行建零件）"整体后移。
>
> ⚠️ 如果你（AI）按旧顺序问"参数需求强度"，用户会看到**和改造前一模一样**的行为 ——
>    这正是本次改造要消除的。**必须以 `init` 命令的返回为准**，见下方第 1 步。

### 第 1 步：`init`（确认模式 + 两个固定问题）

```powershell
python "<工程模式根目录>\tools\workflow_gate.py" init "<设计任务描述>"
```

- **作用**: 返回 **新流程的第一步** —— 确认模式 + 两个固定问题，**必须原样呈现给用户**
- 返回内容包含（以命令输出为准，勿自行改写）：
  1. 【模式确认】按**模式二 · 大型复杂器械/多零件装配体**执行
  2. **① 并行策略（D / E）**
     - D = 部分小屋并行（按波次分批启动）
     - E = 单小屋串联（一次一个房间）
  3. **② 搭建方式（A / B / C）** —— 即「**是否 AI 自主设计**」
     - A = 完全自主搭建（AI 自主决策）
     - B = 部分自主搭建（关键节点询问）
     - C = 步步确认（每个零件参数表逐件确认）
- 返回里还有 `flow_order` 字段，列出完整 5 步顺序，可直接呈现给用户。
- **MUST 等用户回答这两个问题后才能进入第 2 步！**

### 第 2 步：`provide_context` #1（收模式确认 → 返回参数需求强度题）

```powershell
python "<工程模式根目录>\tools\workflow_gate.py" provide_context "<用户对并行策略/搭建方式的回答原文>"
```

- 作用: 记录模式确认结果（并尽力解析出 D/E 与 A/B/C 预选项），
  返回**第 2 步的问题：参数需求强度**（强需求 / 不强）。
- **MUST 把返回的问题原样呈现给用户，等用户作答！**

### 第 3 步：`provide_context` #2（定设计范围 → 创建总计子代理并接上七阶段）

```powershell
python "<工程模式根目录>\tools\workflow_gate.py" provide_context "<用户对参数需求强度的回答原文>"
```

- 判定**设计范围**（详细 / 精简）并返回七阶段分析指引。
  - 回答含「强需求 / 严格校核」→ **详细设计范围**
  - 回答含「不强 / 精简」或含糊 → **精简设计范围**
  - ⚠️ **题数区间已废除**：七阶段没有「强 10~50 / 弱 5~10」这种配额。
    真人澄清由插件按**明确决策缺口**触发（question → capture → interpret），
    数量取决于任务本身，不取决于档位。`mode2_flow.validate_question_budget`
    与 `QUESTION_LIMITS` 已随十阶段引擎一起删除。
- ★ 返回内容会要求你**先创建总计子代理并接上七阶段插件**（见 §3.6）。
- **MUST 按返回指引执行：立刻 spawn 总计子代理（见 §3.6）。**
  主对话自己去调分析工具会被插件拒（`ROOT_REQUIRED` / `REAL_DIRECT_CHILD_REQUIRED`）。

### 第 3.6 步：★★ 主对话 spawn【总计子代理】→ 接上七阶段插件 → 它再开三个子代理

> 🔴 **【2026-11-18 · 十阶段 → 七阶段】本节整段重写。**
> 旧版这里的 `analyst-bind` / `begin-stage` / `mark-stage-done` / `ask` /
> `set-subagent-plan` **全部已删除**（连同十阶段引擎本体）。
> 现在分析由独立插件 `dsh-engineering-seven-stage` 承担，工具名
> **`engineering_seven_stage`**。

**会话谱系（用户口径，按这个来）：**

```
主对话 ──subagent_fork──► 总计子代理 ──launch──► 知识子代理 ┐
                                             ├─► 设计子代理 ├─ 三者都是真实二级会话
                                             └─► 反驳子代理 ┘
```

> 七阶段文档里写的「主对话」= 这里的**总计子代理**（它编排、它提问、它提房间草案）。
> 「真实根会话」= **DSH 主对话**（只有它能 attach / capture 真人原话）。

#### ① 主对话：先用 `subagent_fork` 建总计子代理

```python
subagent_fork(description="总计子代理（七阶段分析）",
  prompt="你是七阶段分析的总计子代理。开工第一步用 engineering_seven_stage 接上插件"
         "（taskId=<本任务 task_id>，action=status 先看状态）。"
         "收到 attach 通知后立即 action=launch 启动知识/设计/反驳三个子代理。"
         "不要自己创建任何其它子代理，不要调 sw_bridge / 门禁命令。")
```

**初始 prompt 只让它「等 attach」**，其余工作等接入后再做 —— 否则它会先把活干完再发现没接上。

#### ② 主对话：立刻 attach（唯一的真人入口登记）

```python
engineering_seven_stage(
  taskId="<workflow_state.task_id>",
  action="attach",
  dataJson='{"planner":"<总计子代理的真实 sessionId>","messageIds":["<原始需求消息ID>"]}')
```

- `planner` 必须是**真实直系子会话**：插件会核对 `actorOf(ctx.agents.get(planner)).parent === 我`。
  填错 → `REAL_DIRECT_CHILD_REQUIRED`。
- `messageIds` 省略时捕获最近一条真人原文。**多轮输入时必须指定原始需求那条**，
  否则会把最后一句模式确认当成需求。
- 只有**根会话**能 attach（`ROOT_REQUIRED`）。

#### ③ 总计子代理：`action=launch` 启动三个二级会话

```python
engineering_seven_stage(taskId="<task_id>", action="launch")
```

- 插件用宿主原生 `startActivation` 建三个会话（`maxDepth=2`），每个预留独立 sessionId；
- 只有**三个真实会话全部 bind 成功**，流程才进入 1.1；
- 三个角色各自收到一次性邀请码，**第一次调用必须是 `action=bind`**：
  `dataJson='{"role":"knowledge","token":"<邀请码>"}'`。
- ⚠️ 启动前插件要求「本任务没有其它活跃房间」：有别的建模小屋在跑 →
  `OTHER_ROOMS_ACTIVE_DRAIN_BEFORE_ANALYSIS`。**先排空建模会话再开分析。**

#### ④ 按 `action=status` 的 `stepContract` 逐步推进

每一步固定这条路（**顺序由插件强制，跳步会被拒**）：

| 角色 | 动作 | 关键约束 |
|---|---|---|
| 总计 | `frame` | 对象 / 当前决策 / 边界 / **searchTopic** / 复核方案 / 假设 / 依赖 |
| 知识 | `search` → `submit` | 必须先真实联网；来源必须来自本 job 的搜索结果 |
| 反驳 | `submit`（1.1 先盲审） | 只提交可复现、可行动的异议；空反对会被拒 |
| 设计 | `submit` | 方案 + 参数 + 计算依据 + 缺口 |
| 反驳 | `submit`（复审） | `verdict=pass/conditional/blocked`；已解决异议须逐条写关闭依据 |
| 总计 | `commit` | 有 blocking 缺口 → 自动转 `evidence_wait` |

- 一轮返工上限 **2 次**，超限转 `evidence_wait`（不是死锁，是要求补证）。
- 想回更早节点用 `rewind`（同一证据不能重复回退）；回退后下游消息凭据作废。
- 数值计算用 `action=calculate`（插件内置 add/sub/mul/div/pow/sqrt/abs/sin/cos/tan，
  三角用弧度）—— **不要心算**，算完的 `calculation` 会自动挂进本步记录。

#### ⑤ 真人缺口：question → capture → interpret（原话必须来自宿主消息）

| 谁 | 动作 | 说明 |
|---|---|---|
| 总计 | `question` | 写清 当前决策 / 所需信息 / 为什么缺 / 不知道时怎么取得 |
| **真实主对话** | 把问题展示给用户 | 用 `ask_user_question` 或 `yield` 暂停 |
| **真实主对话** | `capture` | 从**宿主消息**取用户原话（`messageId` 可选） |
| 总计 | `interpret` | 分类 `fact/preference/authorization/estimate/unknown/no_change`，并给出受影响最早步骤 `fromStep` |

- **模型自己写的文本不能充当真人原话** —— `capture` 只认 `source.kind='user'` 的消息。
- 分类不是 `no_change` 时，插件会把 `fromStep` 及其下游全部作废、`revision+1`。
  这也意味着**已登记的建模编制立即失效**，必须重新 `register-design`。

#### ⑥ 正常完成：propose_rooms → 各角色 end → 主对话登记编制

```python
# 总计子代理：按最终规格提交建模编制草案（房间数由分析结论决定）
engineering_seven_stage(taskId="<task_id>", action="propose_rooms",
  dataJson='{"rooms":[{"name":"结构件","type":"structural","parts":["DSH_底座"],
            "why":"承载主要载荷","depends_on":[]}],"rationale":"<分解理由>","scope":"<范围>"}')

# 三个角色依次 end，然后总计、最后真实根会话各自 end（都必须写 reason）
engineering_seven_stage(taskId="<task_id>", action="end",
  dataJson='{"kind":"complete","reason":"<本角色的明确完成理由>"}')

# 主对话：把设计结论登记为本轮编制（唯一入口）
python "<根>\tools\mode2_flow.py" register-design
```

- 需要完整设计记录时加 `--include-records`（会在交付目录写
  `seven_stage_design_<revision>.json`）。
- 登记成功后 `select` 才会放行；未登记 → `SEVEN_STAGE_HANDOFF_REQUIRED`。
- **设计版本变过**（`revision` 变化）→ `SEVEN_STAGE_DESIGN_CHANGED`，必须重新登记。

#### ⑦ 部分结果 / 中断

- 暂时停止（还有任务）用 `yield`；有任务但遇故障用 `blocked`；
  用户主动终止 → 用户在对话里说「停止分析」，真实主对话 `partial` 才会被认。
- `partial` 产物可用于给用户看已完成对象、补证责任与下一步，
  **但不能解锁建模**（read_design 会拒绝）。
- 角色没有可靠恢复路径时：**保留部分结果，另建新任务复跑**，别覆盖原审查历史。

#### ⑧ 权限边界（插件强制，违反直接被拒）

七阶段接管期间，这些会话**只有** `engineering_seven_stage` 一个工具可用：

```
SEVEN_STAGE_TOOL_GATE: 分析期间由 engineering_seven_stage 分派任务、搜索、转发和结束。
当前工具不在本阶段许可内。
```

即：**不能**跑 shell / 写文件 / 调旧门禁 / 改工作流状态 / 再委派 / 任意发信。
普通根会话与建模小屋不受影响，照旧用原有工具。

### 第 4 步：力学估算（`provide_context` #3）

```powershell
python "<工程模式根目录>\tools\workflow_gate.py" provide_context "<全部提问回答原文>"
```
- 执行力学估算，返回 A/B/C 搭建方式 + D/E 并行策略选项
- **MUST wait for user to choose A/B/C and D/E!**
- 🔴 **调用前必须确认：总计子代理已接上七阶段插件并到达 `ready`（三角色 + 总计都已 end）。**
  自查：`python "<根>\tools\mode2_flow.py" seven-status`（看 `seven_progress`），
  再用 `list_agents` 确认总计子代理已结束。
  **它没跑完就调 #3，后面的 `select` 会直接报 `SEVEN_STAGE_HANDOFF_REQUIRED`。**

> 📌 状态流转（**新流程，2026-10-13 重排**）：
> `mode_confirm_asked`（确认模式+D/E+A/B/C）
>   →（传模式确认答案）→ `depth_asked`（强/弱需求）
>   →（传强度答案）→ `context_asked`（总计子代理 + 七阶段 63 小步 + 真人澄清）
>   →（传全部提问答案）→ `mechanics_done`（力学估算 + A/B/C、D/E 选项）。
> 不要跳步；`step` 不对时命令会直接报错拒绝。

> 📌 **【BUG-01/07 修复】载荷数值口径**
> 力学估算会**优先读取用户明确写出的载荷数值**（如「100N」「10kg」「0.1kN」），
> 关键词（冲击/动态）只用于确定**动载系数**，不再放大用户给的额定值。
> 若返回值带 `load_is_estimate=true`，说明用户没给数值、当前是估算基准，
> **必须向用户复核后再继续**。
> 命令同时把载荷工况落盘到 `tools/load_cases/gate_load_case.json`，
> **physics 校核必须用该文件**（额定载荷 = `magnitude_n`），
> 严禁另起一套数值（那正是 BUG-07「门禁 1000N / physics 100N」的根因）。

### 第 5 步：`select` 确认选择（前置：七阶段 ready + 已 register-design）

```powershell
python "<工程模式根目录>\tools\workflow_gate.py" select <A|B|C>,<D|E>
```

- **参数**: 搭建方式 A/B/C 与并行策略 D/E
- **A/B/C**: 搭建方式（完全自主/部分自主/步步确认）
- **D/E**: 并行策略（部分小屋并行 / 单小屋串联）
- **【BUG-03 修复】** 以下写法**全部等价可用**（旧文档只写了一种，极易踩坑）：
  ```powershell
  ... select C,E        # 推荐
  ... select C E
  ... select "C, E"
  ... select c e
  ... select --choice C --parallel E
  ```
- **MUST wait for user to confirm selection!**
- **【BUG-04 修复】** 并行策略的权威来源已统一：
  `select` 当场写入 `workflow_state.json`，并自动与 `mode_state.json` **对齐**，
  不再出现两个命令读到相反结论的情况。
- 🔴 **【2026-11-18】`select` 的硬前置：七阶段已 `ready` 且编制已登记。**
  未跑完 / 未登记 → `select` 返回 **`SEVEN_STAGE_HANDOFF_REQUIRED`**（凭证不可用）
  或 **`MODE2_SUBAGENT_PLAN_REQUIRED`**（凭证有了但没 `register-design`）；
  用户中途改答 → **`SEVEN_STAGE_DESIGN_CHANGED`**（ticket 对不上）。
  三种都要按提示补齐，**不要试图改状态文件绕过**。
- 🔴 **任务引擎在 `init` 时就被固定为 `seven_v03`**，模型不能切换、也没有回退分支：
  旧任务请用旧版本工具链收尾。
- 🔴 **`select` 返回的 `wave1` = 编制里的【全部建模房间】。**
  房间数与细分**由总计子代理决定**，主对话**不得**改用固定表（旧固定 3 房间 / 5 房间已废弃）。

### 第 3.5 步：★ 模式二新流程（全部为【代码强制】，不是提示词约定）

> 用户要求：「所有改动都要写在代码里面，强制执行的」。
> 以下每一条都由 `workflow_gate.py` / `mode2_flow.py` 的代码校验，**违反会被拒绝**。

#### 3.5.1 真人澄清：没有题数配额（十阶段的 10~50 / 5~10 已废除）

> 🔴 **【2026-11-18】`validate_question_budget` / `QUESTION_LIMITS` / `ask` /
> `answer-question` 全部随十阶段引擎删除。** 不再有"必须问够 N 题"的机器判据。

七阶段的澄清链路是 `question → capture → interpret`：

| 步 | 谁做 | 判据 |
|---|---|---|
| 提出缺口 | 总计子代理 `action=question` | 必须写清 当前决策 / 所需信息 / 为什么缺 / 不知道时怎么取得 |
| 把问题交给用户 | **真实主对话** | 用 `ask_user_question`，或用 `action=yield` 暂停等用户 |
| 采集原话 | **真实主对话** `action=capture` | 只认 `source.kind='user'` 的宿主消息；**模型自写文本无效** |
| 分类并回退 | 总计子代理 `action=interpret` | `fact / preference / authorization / estimate / unknown / no_change` + `fromStep` |

查看现状：`python "<根>\tools\mode2_flow.py" questions`（只读，含 `open_question`）。

#### 3.5.2 七阶段流水线（63 个小步，顺序由插件强制）

| 阶段 | 小步 | 主题 |
|---|---|---|
| 1 | 1.1 ~ 1.6 | 看懂真实现场，确定机器边界 |
| 2 | 2.1 ~ 2.6 | 看懂工件与工艺，查清正常与异常 |
| 3 | 3.1 ~ 3.8 | 把目标变成可验证的要求，决定怎样验收 |
| 4 | 4.1 ~ 4.10 | 拆功能、比较路线，检查完整任务可行性 |
| 5 | 5.1 ~ 5.9 | 搭内部骨架，确定载荷/运动/基准/接口 |
| 6 | 6.1 ~ 6.12 | 逐件设计，补齐连接、螺钉、支撑与细节 |
| 7 | 7.1 ~ 7.12 | 把机械/电控/安全/制造/装配/维修整合起来 |

- 每一步都必须有「知识（含真实联网来源）→ 设计 → 独立复审」三件套；
- 1.1 先**盲审**原始需求（此时还没有设计结论），再进入设计发布；
- 空反对、未填理由、省略未关闭的硬异议 → 一律拒绝；
- 一轮返工上限 2 次，超限转 `evidence_wait`；
- `7.12` 的复审必须 `pass`，否则 `read_design` 拒绝交接。

进度：`python "<根>\tools\mode2_flow.py" seven-status`（旧名 `stage-status` 仍可用作别名）。

#### 3.5.3 房间草案 = 七阶段的 `roomProposal`（不是模型手写的 rooms.json）

> 🔴 **`set-subagent-plan` 已删除。** 编制只能来自
> `seven_stage_gate.read_design()` 读到的房间草案 —— 模型无法自报 ticket / revision /
> 分析者会话 ID。

**① 总计子代理用插件提交草案**

```python
engineering_seven_stage(taskId="<task_id>", action="propose_rooms",
  dataJson='{"rooms":[{"name":"结构件","type":"structural","parts":["DSH_车架底板"],
            "why":"承载整车与电池","depends_on":[]}],"rationale":"<分解理由>","scope":"<范围>"}')
```

**每个房间必须有的 5 个字段**（与校验器同源，缺 `type` 必被拒）：

| 字段 | 必填 | 说明 |
|---|---|---|
| `name` | ✅ | 房间名，唯一、中文可读 |
| `type` | ✅ | 规范类型之一（可写同义词，会被归一化）：`structural` / `transmission` / `housing` / `support` / `spring` / `thermal` / `corrosion` / `assembly` / `drafting` |
| `parts` | ✅ | 本屋负责的零件名清单；**非建模屋也要写占位名**，空数组会被拒 |
| `why` | ✅ | 为什么需要这个房间（可审计的存在理由） |
| `depends_on` | ✅（可空数组） | 依赖的房间名；指向不存在的房间会被拒 |

拿一份现成示例：`python "<根>\tools\mode2_flow.py" rooms-template`
（只说明字段长什么样，**不**用来投喂登记命令）。

**② 真实主对话登记编制（唯一入口）**

```powershell
python "<根>\tools\mode2_flow.py" register-design
python "<根>\tools\mode2_flow.py" register-design --include-records   # 附带完整设计记录
```

- 它会 `read_design` 复核 63 个小步 + 四角色完成理由 + 房间草案指纹；
- 通过后把 `ticket / revision / analyst_session_id` 一起写进 `mode2_plan.json`；
- 房间数 1~24；**总装(assembly) 与出图(drafting) 若草案没写会自动补齐**
  （这是流程决定的，不需要模型记）；
- 🔴 **他人不能代登记**：`register-design` 用的是 `workflow_state` 里的
  `task_id` + `seven_stage_root_session_id`，模型改不了这两个值。
- 🔴 **编制 = 全系统房间表的唯一来源。** `select` 的 `wave1` 直接取编制里的全部建模房间；
  旧的代码默认房间表已废弃，不得作为兜底。

#### 3.5.5 零件验收：外部监控器逐件核验（无「总理子代理」）

> 🔴 **2026-11-12 起，「总理子代理」已整体删除。**
> 它原本负责"收各屋上报 → 校验相邻尺寸 → 统一核实 → 汇总 docx"。
> 这四件事现在全部由外部服务 **DSH-SW-Monitor** 以代码方式完成：
> 不需要一个额外的模型会话去重复确认同一批事实（那既慢又多一个可以自说自话的判定点）。

| 原先总理子代理做什么 | 现在谁做 | 怎么判 |
|---|---|---|
| 收「零件已建好」的上报 | `sw_bridge` 保存后自动发 `part-saved` | 带文件路径 + 文件指纹 + generation |
| 校验三道防线凭据 | 监控器 `review-part` | 材料/物理/领域逐件核对哈希、run_id、同房间 |
| 校验相邻零件尺寸 | 监控器 `verify-task` | 调 `part_registry` 审计台账与邻接表 |
| 汇总 docx | 仍然需要，但由谁导出都行 | 交付总闸核实磁盘上真的有一份合法 docx |

```powershell
# 只读：看当前任务每个零件到了哪一步（状态来自监控器）
python "<根>\tools\mode2_flow.py" parts-board
# 验收一个零件（receipts 省略时取 <任务>/reports/<房间>.physics.json / .domain.json）
python "<根>\tools\mode_gate.py" sw-monitor status     # 先确认监控器接上了
```

**"核实"不是自我声明**，而是监控器 `verify-task` 从磁盘重算：

| 核实项 | 判据 |
|---|---|
| 零件台账非空 | 空台账 → 拒绝（`fail-closed`） |
| 相邻尺寸一致性 | 有 mismatch → 拒绝结束 |
| 未决记录 | `unresolved` 还有 open → 拒绝结束 |
| **数据防篡改** | 逐零件重算 SHA-256 指纹；对不上 → 「真实性无法保证」，拒绝结束 |
| 可复现指纹 | 结论落盘 `digest`，事后任何人可重算验证 |

**"才能结束"的强制点**：`pre_delivery_check` 每次都**重算**一遍（不采信旧的
"已通过"结论），任一项不过 → `workflow_gate` 的交付总闸**拒绝归档**，
即**任务无法宣告完成**。

> 另：`part_registry.py` 自身还有一道**写入前**闸门 ——
> 改动若导致已登记的配合尺寸不一致，**默认拒绝落盘**（`CONSISTENCY_VERIFICATION_FAILED`），
> 并做内存回滚（磁盘字节不变）。确属设计允许时须 `--allow-mismatch "理由"`
> 如实说明，放行后记入 `unresolved` 台账，绝不静默消失。

#### 3.5.6 建模之后必须「看图」（第 6 项）

建模子代理必须以 **「看图 → 矫正 → 继续看 → 继续矫正」** 作为主要矫正方式：

```powershell
python "<根>\tools\visual_check.py" gate --part "<零件>.SLDPRT" `
       --expect '{"bbox_mm":[xmin,xmax,ymin,ymax,zmin,zmax]}'
```

- `room-end` 时会**强制核实**本房间每个零件是否留有经核实的看图证据
  （截图真实存在且非空 + 闭环 PASS）；缺失 → `VISUAL_EVIDENCE_REQUIRED`，**拒绝下线**。
- 截图被事后删除/替换也会被重新拦下。

#### 3.5.7 SW 健康看护（第 4 项，超阈值立即关 SW）

**两条路并行（互补），都在取锁那一刻自动触发，无需模型记得启动：**

| 路径 | 机制 | 优点 | 局限 |
|---|---|---|---|
| ① OS 子进程 | `mode_gate.py sw-guard-start`（`sw_bridge` 取锁时自动调） | 不依赖 DSH 运行时，**必然**有看护 | 没有 DSH job id |
| ② **`ctx.jobs`** | 插件 `POST /dsh-engineering-ui/sw-guard-job` | **有 job id**，可 `job_list`/`job_output`/`job_kill` 管理 | 需 DSH 运行时 + jobs 服务 |

用户要求的两点分别由它们满足：
- 「利用 DSH 的后台任务运行时（ctx.jobs）…这个 job 会立即返回一个 job id」→ 路径 ②
- 「一旦内存、CPU超85％阈值就输出一行警报…立马关闭SW」→ 两条路都实现

```powershell
# 查 OS 子进程看护状态（含最近采样值）
python "<根>\tools\mode_gate.py" sw-guard-status
# 手动启动 / 停止
python "<根>\tools\mode_gate.py" sw-guard-start
python "<根>\tools\mode_gate.py" sw-guard-stop
```
```powershell
# ctx.jobs 版：启动（幂等，返回 job id）/ 查状态
curl -X POST http://127.0.0.1:19387/dsh-engineering-ui/sw-guard-job
curl        http://127.0.0.1:19387/dsh-engineering-ui/sw-guard-job
```

- 间隔 **10 秒**、阈值 **CPU/内存 85%** —— 均为代码常量
  （`SW_GUARD_INTERVAL_MS` / `SW_GUARD_CPU_LIMIT` / `SW_GUARD_MEM_LIMIT`）。
- 恰好等于 85% **不触发**（避免临界误杀，与 `sw_guard.py` 口径一致）。
- 显式关闭：设 `DSH_SW_GUARD_OFF=1`。
- ⚠️ **`ctx.jobs` 缺失时显式降级**（不假装成功）：返回 `job_id=null` +
  `degraded=true` + 说明「① 那条路仍在工作」。
- ⚠️ **插件 host 半改动是进程级的** —— 改完必须**重启 DSH** 才生效；
  验证方法：`/agents` 返回 200 而 `/sw-guard-job` 返回 404，即"代码已就位、待重启"。

#### 3.5.8 交付前总闸

```powershell
python "<根>\tools\workflow_gate.py" pre-delivery
```

任一不满足即**拒绝宣告完成**（拒绝归档）：
七阶段凭证不可用或与已登记 ticket 不一致 / 编制缺失或非七阶段草案 /
监控器 `verify-task` 未通过 / 存在未处理的相邻尺寸不一致 / 缺 docx 汇总。

自查：`python "<根>\tools\mode2_flow.py" seven-status` —— 看
`current.ok` / `registered.ticket` / `subagent_plan.ready` 三处是否都对得上。

### 第 6 步：创建子代理小屋（房间表由总计子代理决定！）

> 🔴 **房间表 = `select` 返回的 `wave1` = 编制里的全部建模房间。**
> **数量与细分由总计子代理决定** —— 不要写死旧的固定五房间表
> （结构件 / 传动机构 / 壳体机架 / 总装与验证 / 工程图输出），
> 也不要写死「第1波 = 结构件 + 传动机构 + 壳体机架」这种固定三房间说法。

**并行模式 D —— 三波式：**

```
wave1 = select.wave1 = 全部建模房间   ← 同时 fork
wave2 = 总装与验证（等 wave1 全部 room-end）
wave3 = 工程图输出（等 wave2 room-end）
```

**wave1 执行顺序（不可打乱）：**
1. **先**对每个房间 `python "<根>\tools\mode_gate.py" room-start <房间名>`
   （登记，**BEFORE** 创建 subagent；未 room-start 的房间 `subagent-assign` 会报 `ROOM_NOT_REGISTERED`）
2. **再**在同一条回复里**一次性 `subagent_fork` 该批全部房间**——
   ⚠️ 严禁只开一个、严禁分多条回复慢慢开：**同批房间必须同时启动**。
   ⚠️ **每批最多 3 个**：宿主对同一 root 的子代理有名额上限，
      插件在 `tools/pre-execute` 做了硬闸门，第 4 个会直接返回
      `SUBAGENT_LIMIT_REACHED`。编制超过 3 个房间就**分批**：本批全部
      `room-end`（名额释放）后再开下一批，不要原样重试被拒的那次调用。
3. **每个 fork 之后立刻**用返回的 id 绑定（漏了就永远 `unbound`）：
   ```powershell
   python "<根>\tools\mode_gate.py" subagent-assign <房间名> <subagent_fork 返回的 id>
   ```
4. 建模小屋 prompt 必须写「只做本房间零件」，并带上 `select` 返回的 `part_ownership` keywords。

**监控器接替「总理」的验收职责（不需要额外的模型会话）：**
- 零件建成 = 监控器收到 `part-saved`（sw_bridge 保存后自动发，带文件指纹与 generation）；
- 逐件验收 = 监控器 `review-part`（材料/物理/领域凭据核对哈希、run_id、同房间）；
- 房间收尾 = `review-room` → `close-room`；全任务总核实 = `verify-task`；
- 只读进度：`python "<根>\tools\mode2_flow.py" parts-board`（数据源就是监控器 board）；

**串行模式 E 严格顺序（不可打乱）：**
1. **先**调 `mode_gate.py room-start <房间名>`（登记， BEFORE 创建 subagent）
2. **再**用 subagent/subagent_fork 创建该小屋，**并立刻** `subagent-assign <房间> <id>`
3. 等待该小屋完成（`room-board` 看 `state=done`，或 `list_agents` 检查）
4. 调 `mode_gate.py room-end <房间名>`（解锁，才能开下一个）
5. 重复 1~4 处理下一个房间

**严禁一次性创建多个 subagent（串行模式）！** 串行锁会拒绝第二个 room-start。
- 🚫【铁律】所有小屋创建完毕前，主对话禁止调用 sw_bridge.py 做任何建模操作！

## 🟦 Webots 仿真分支（Mode 1/2/3 的附加产物，不改变原门禁）

> **一句话**：仿真只在**模型已经存在**之后发生；它消费 `select` 建立的 task/room 身份，
> 自己**不建房间、不改 workflow_state.json、不申请 SW 写锁**。

### 什么时候走这一支

用户明确说要「仿真 / 跑起来 / 看运动 / 查干涉 / 传感器数据 / 机器人行为」时。
只是要强度、疲劳、FEA、出图 —— **不走这里**，走 physics-in-loop。

### 硬约束（代码级，不是提示词）

| 约束 | 由谁保证 |
|---|---|
| 归属必须由**当前 live 的工程 agent + 唯一活跃房间**推导 | `lib/webots.js` 的 `resolveWebotsContext()`，不符即 `CONTEXT_UNAVAILABLE` |
| room_id = `room-` + 房间名 UTF-8 的**完整 SHA-256** | 同上；ASCII 与中文走同一映射，禁止给 ASCII 名称开直通域 |
| 每次调用必须带**真实** `task_id` / `room_id` | `ctx.tools.guard` 拦截 `mcp__webots__*`，自报值对不上直接拒 |
| 未配置 runtime 时一律 `not_started` / `core_ready=false` | 状态投影取保守值，**不许**把"装好了"说成 READY |

### 执行顺序

1. **先有 run**：`sim_status` 只读；真正要跑先 `sim_launch`（需要 pinned runtime）。
2. **未知结果不许重发**：返回 `OUTCOME_UNKNOWN` / 超时 / 断连时，保留
   operation_id / job_id / run_id / session_generation，用原身份查 `sim_job`，**禁止重发动作**。
3. **读证据而不是猜**：`sim_report`（model_manifest / conversion_report / experiment_report）
   与 `sim_evidence`（UTF-8 字节分页；翻页要带上一页的 `sha256` 作 `expected_sha256`）。
4. **归因只列候选**：`sim_attribute` 只读；`fake` / `fixture` / `synthetic` /
   静态校验 / 空接触 **都不等于物理 PASS**。
5. **回写 SW 要单独授权**：本版未实现原生回写（`authorized_native_repair` 在
   `blocked_native` 里），只能报告缺口，**不许**自行改质量/摩擦/重力去凑通过。

### 启用条件（缺一即视为未启用，不是"坏了"）

```powershell
$env:DSH_WEBOTS_ENABLE     = '1'                       # 唯一开关，不是 1 就整行禁用
$env:DSH_WEBOTS_PYTHON     = '<绝对路径>\python.exe'   # 已装 engineering/tools/webots_mcp/requirements.txt
$env:DSH_WEBOTS_INPUT_ROOT = '<已存在的只读输入根>'
$env:DSH_STATE_DIR         = '<已存在的绝对 state 目录>' # Node 与 Python 必须解析到同一目录
$env:DSH_ENGINEERING_ROOT  = '<工程模式根>\engineering'
$env:WEBOTS_HOME           = '<Webots 安装根>'          # 例：D:\Webots
# 可选：$env:DSH_WEBOTS_RUNTIME_CONFIG = '<绝对路径>\runtime.json'
```

**先跑自检**（只读，不启动任何东西）：

```powershell
python "<工程模式根>\tools\webots_env.py"        # 人类可读
python "<工程模式根>\tools\webots_env.py" --json # AI 读 blocking 字段
```

任一缺失 → MCP 行被 `disabled`，或进程起不来但 `failOnStartupError:false` 让预设照常加载。
**这时正确行为是如实报告"仿真不可用"，不是伪造结果。**

### 报告里必须保留的四件事

`provenance`（fixture / static-derived / real-sw-export / real-webots）、
`core_ready`、`execution_state`、`stale` + `snapshot_at`。
把这四项抹掉、只说"仿真通过"，等同于交付虚假证据。

---

## ⚠️ 关键规则

- **【BUG-03 修复】必须用命令行脚本；本环境没有任何门禁类 DSH 工具！**
  - ✅ 正确：`python "<工程模式根目录>\tools\workflow_gate.py" init "<任务>"`
  - ❌ 错误：凭印象调用任意门禁类 DSH 工具（**本环境不存在，必然失败**）
- 子命令名用**下划线**：`init` / `provide_context` / `select` / `confirm-assembly`
- **`provide_context` 必须调用三次**（新流程）：
  ① 传「并行策略 D/E + 是否 AI 自主 A/B/C」的答案；
  ② 传「参数需求强度（强/不强）」的答案；
  ③ 传「全部按需提问」的答案（此时才出力学估算）。
  ⚠️ 顺序不能颠倒：模式确认在前、强度在后（2026-10-13 重排）。
- **`select` 支持 `C,E` / `C E` / `"C, E"` / `--choice C --parallel E` 等写法**（BUG-03）
- 🔴 **谁调什么（2026-10-28 重排）**：
  · `workflow_gate.py`（init / provide_context / select）与 `mode_gate.py`（room-start / room-end /
    subagent-assign / room-board / recover）**由主对话调**；
  · `engineering_seven_stage` 的 `attach` / `capture` **只能由真实主对话调**；
    `launch` / `frame` / `question` / `interpret` / `commit` / `propose_rooms`
    **只能由总计子代理调**；`search` / `submit` 由三个二级角色各调各的。
  · `mode2_flow.py` 的 `register-design` / `recheck-design` / `seven-status` 由**主对话**调；
    `parts-board` / `export-docx` / `pre-delivery` 谁调都行（只读或汇总）。
  · 旧规则「门禁每一步都在主对话完成、不进子代理」**已废除（deprecated）**。
- 🔴 **等房间/等锁禁止 sleep 轮询（【A1-①】）**：用一次性接口
  `room-board --wait 900` / `wait-rooms "<房间>" --timeout 20`（返回 `job_required=true` 时改挂宿主后台 job）；
  等 SW 使用权则用 `job_output` 等 sw_busy 返回的 `job_id`。判据只读 `state`，不看"等了多久"。
- 🔴 **子代理死活只看 `python "<根>\tools\mode_gate.py" room-board --json` 的 `state`**：
  · `waiting_sw`（在等监控器发使用权）/ `running` / `starting` / `idle` = **正常，禁止判死**；
  · `unbound` = 平台 alive 但没 `subagent-assign` → **补 assign**（不是僵尸）；
  · `dead` = 才允许 `recover` / `restart`，且先看 `safe_to_restart`；
  · **禁止对 `running` / `waiting_sw` / `idle` 的房间 `reset`**（会把还在干活的小屋清掉）。
- 严禁跳过门禁直接开始设计

## Violation Handling

- Skip workflow_gate = **CODE BLOCKED**
- Mode 2 without subagent rooms = **CODE BLOCKED**
- **没 spawn 总计子代理就 attach** = **CODE BLOCKED**（`REAL_DIRECT_CHILD_REQUIRED`）
- **非总计子代理调 `launch` / `propose_rooms` / `commit`** = **CODE BLOCKED**（`ROLE_ACTION_DENIED`）
- **没走完七阶段 / 没 `register-design` 就 `select`** = **CODE BLOCKED**
  （`SEVEN_STAGE_HANDOFF_REQUIRED` / `MODE2_SUBAGENT_PLAN_REQUIRED`）
- **用户改答后不重登记就继续建模** = **CODE BLOCKED**（`SEVEN_STAGE_DESIGN_CHANGED`）
- **`fork` 后漏 `subagent-assign`** = 房间显示 `unbound`，上报与判定全部失真
- Used subagent in Mode 1/3 = **FAIL**

## Example Flow

User: "Design a planetary reducer"

AI must（门禁命令在主对话；七阶段分析在**总计子代理 + 三个二级角色**里）：
1. Output: [MODE SELECTED] Mode 2
2. 执行 `python "<工程模式根目录>\tools\workflow_gate.py" init "Design a planetary reducer"`
   → 返回**第 1 步：确认模式 + ① 并行策略 D/E + ② 是否 AI 自主 A/B/C**
   → **把这两问呈现给用户，必须等用户回答！**
3. 用户回答后，执行
   `... workflow_gate.py provide_context "<并行策略+搭建方式的答案>"`
   → 返回**第 2 步：参数需求强度（强需求 / 不强）** → 呈现给用户，等作答
4. 用户答完参数强度后，**再次**执行
   `... workflow_gate.py provide_context "<参数强度的答案>"`
   → 返回**创建总计子代理 + 接上七阶段**的指引
5. ★ **spawn 总计子代理**（`subagent_fork`，description="总计子代理（七阶段分析）"），
   prompt 只让它「等 attach」，然后主对话立刻：
   `engineering_seven_stage(taskId=..., action="attach",
   dataJson='{"planner":"<总计子代理 sessionId>","messageIds":["<原始需求消息ID>"]}')`
   - 总计子代理收到通知 → `action=launch` 建三个二级会话（知识 / 设计 / 反驳）
   - 三角色各自 `action=bind`，然后按 `status` 的 `stepContract` 逐步提交
   - 真人缺口：总计 `question` → **主对话 `capture`** → 总计 `interpret`
   - 结束时总计 `propose_rooms`，四角色依次 `end`（都要写 reason）
6. 主对话 `wait_agent` / `list_agents` 等它结束，再执行
   `... provide_context "<全部澄清的答案>"` → 力学估算 + 落盘 `tools/load_cases/gate_load_case.json`
7. 主对话登记编制：`python "<根>\tools\mode2_flow.py" register-design`；
   再问 A/B/C 与 D/E → 执行 `... workflow_gate.py select C,E`
   → `wave1` = 编制里的**全部建模房间**（数量由房间草案决定）
   → 返回 **`SEVEN_STAGE_HANDOFF_REQUIRED`** = 七阶段没跑完 / 没登记（回第 5~7 步补齐）
8. **wave1 一次性 fork 全部建模房间**（写进同一条回复）；
   每个 fork 后立刻 `python "<根>\tools\mode_gate.py" subagent-assign <房间> <id>`
9. 每个小屋开工先 `mode_gate.py room-board --json` 确认自己是 running
   （`waiting_sw` 只是排队，不许判死）；wave2 = 总装、wave3 = 出图；
   零件验收由监控器逐件完成，**没有总理子代理需要收尾**。