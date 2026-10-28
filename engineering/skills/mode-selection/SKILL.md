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
| **建模小屋 / 总理子代理** | 本房间建模、验证、`room-report`、`room-artifact`、`prime-report`（总理） | `init` / `provide_context` / `select` / `confirm-assembly` / `begin-stage` 等门禁命令 —— 门禁已在主对话完成 |
| **总计子代理** | 只调 `mode2_flow.py` 的阶段/提问/编制命令 + `ask_user.py` | `init` / `provide_context` / `select`（那是主对话的活） |

> 实测教训（会话 `session-31c56a28`）：三个建模小屋各自加载本技能后又跑了一遍
> `init → provide_context → begin-stage → ask`，把主对话的十阶段状态覆盖成四份。
> 现在代码侧已加 `ROOT_SESSION_REQUIRED`：子代理调这些编排命令会被直接拒绝并提示。

## Rules (Code-Enforced, Not Just Prompts)

**Rule 0: Any design work MUST go through this gate first.**（仅限主对话）

## Step 1: Analyze Task Type

| Feature | Mode | Architecture |
|---------|------|--------------|
| Single part, simple geometry | **Mode 1: Basic Part Building** | Single-thread direct modeling |
| Multi-part assembly, mechanism, transmission | **Mode 2: Complex Assembly** | Multi-room subagent collaboration |
| User sent image/screenshot | **Mode 3: Image Analysis** | Single-thread recognition rebuild |

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
> 参数需求强度题**后移为第 2 步**；第 3 步启动【总计子代理】按十阶段流水线分析，
> 由 AI **按需提问**（不再一股脑全问）。原"第一步（多子代理并行建零件）"整体后移。
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

### 第 3 步：`provide_context` #2（定题数区间 → 启动总计子代理）

```powershell
python "<工程模式根目录>\tools\workflow_gate.py" provide_context "<用户对参数需求强度的回答原文>"
```

- 判定参数需求强度并返回**按需提问指引**：
  - 回答含「强需求 / 严格校核」→ **强需求**：题数须落在 **10 ~ 50** 之间
  - 回答含「不强 / 精简」或含糊 → **弱需求**：题数须落在 **5 ~ 10** 之间
  - ⚠️ **旧口径（强需求固定 17 题 / 弱需求固定 5 题）已废除**：
    现在题库只作【候选池】，实际问几题由 AI 按十阶段分析进度**按需决定**，
    但必须落在上述区间内（见 3.5.1 / 3.5.2）。
- ★ 返回内容会要求你**先启动总计子代理**按十阶段分析，**分析到缺信息时才提问**
  （不允许开头就把候选题目全部抛出）。命令返回里有完整的十阶段清单与调用示例。
- **MUST 按返回指引执行：立刻 spawn 总计子代理（见 §3.6），由它跑十阶段 + 按需提问。**
  主对话自己 `begin-stage` / `ask` 会被 `ANALYST_ACTOR_REQUIRED` 拒绝 —— 别自己上手。

### 第 3.6 步：★★ 在主对话里 spawn【总计子代理】（十阶段分析的唯一执行者）

> 🔴 **【2026-10-28 修复 · 与旧文档直接矛盾处已删除】**
> 旧版本文档末尾有两条**已废弃（deprecated）**的旧规则（原文已删除，这里只留一句指认，
> 免得被再照抄）：①「门禁每一步都要在主对话里做完、不要进子代理」；
> ②「子代理只用来建模、不负责提问」。—— 两条都与「第 3 步启动总计子代理」**直接矛盾**。
> 实测后果：主对话据此自己把
> 十阶段走完、`set-subagent-plan` 也自己调（只写了 `--decided-by "总计子代理"` 这个字符串），
> **总计子代理从未被真实创建**。**本节取代那两句旧规则。**

`provide_context` #2 返回后，主对话**立刻**用 `subagent_fork` 创建【总计子代理】
（**真实子代理**，不是主对话自演）：

```python
subagent_fork(description="总计子代理（十阶段分析）", prompt="<按下面 5 条写>")
```

**总计子代理 prompt 必须原样包含这 5 条（缺一条就会退化成"主对话自演"）：**

**① 第一步：绑定分析身份** —— 不绑定，后面每条阶段命令都会被 `ANALYST_ACTOR_REQUIRED` 拒绝：
```powershell
python "<根>\tools\mode2_flow.py" analyst-bind
```
   - 自动从环境变量 `DSH_SESSION_ID` 取**它自己**的 sessionId 落盘，**不用手写 id**。
   - 自检：`python "<根>\tools\mode2_flow.py" analyst-status`（谁绑的、什么时候绑的）。

**② 按序跑完十阶段**（`begin-stage` → `mark-stage-done`，禁止跳阶段）：
```powershell
python "<根>\tools\mode2_flow.py" begin-stage TaskUnderstanding
python "<根>\tools\mode2_flow.py" mark-stage-done TaskUnderstanding --artifact "<本阶段产出>"
```
   - 十阶段清单见 §3.5.3；进度用 `python "<根>\tools\mode2_flow.py" stage-status` 查。
   - 只有 `DesignIteration` 允许回退（`refresh-stage` / `redo-stage`）。
   - 等价写法：`python "<根>\tools\workflow_gate.py" mode2 <子命令>`（透传同一个模块）。

**③ 缺信息时提问 —— 走宿主代问桥 `ask_user.py`**
（**子代理不能直接调 `ask_user_question`**：`userQuestions.ask` 校验 roots，
子代理必抛 `DELEGATED_CALLER`，**只能用这个桥**）：
```powershell
python "<根>\tools\ask_user.py" --room "总计子代理" --child "<它自己的 sessionId>" `
       --header "使用场合" --question "使用场合是什么？" --option "室内" --option "户外"
```
   - 阻塞等待；用户的答案以 **tool_result** 回到它当前调用栈，可继续原逻辑。
   - **不许自己编答案**，也不许用正文代替提问。
   - 问之前先登记：`python "<根>\tools\mode2_flow.py" ask "<问题>" --stage <当前阶段> --reason "<为什么现在问>"`；
     用户答完再 `answer-question <seq> "<答复>"` 落账。
   - 题数区间：强需求 10~50 / 弱需求 5~10（代码硬校验，见 §3.5.1）。

**④ 分析完成后登记房间编制**（房间数 / 细分**由它决定**，禁止照抄固定表）：
```powershell
python "<根>\tools\mode2_flow.py" set-subagent-plan `
       --rooms-json "<rooms.json 的绝对路径>" --rationale "<按功能分解的理由>" `
       --decided-by "总计子代理"
```
   - `--decided-by` 必须写「总计子代理」；每房间必须写 `parts`（负责哪些零件）与 `why`；数量 1~24。
   - 十阶段没走完就调会被拒 —— 先 `stage-status` 自查（`require_stages_complete` 强制前置）。

**⑤ 结束回合**，返回值里带**编制摘要**：房间名 × 每房间零件 × 该房间属于第几波。

**这整段时间，主对话只做一件事：等它结束**（`wait_agent` / `list_agents`）。
主对话**不要**去跑 `begin-stage` / `mark-stage-done` / `ask` —— 代码会以 `ANALYST_ACTOR_REQUIRED` 拒绝；
而且一旦主对话自己跑，`select` 之后的「编制由分析得出」就全是假的。

### 第 4 步：力学估算（`provide_context` #3）

```powershell
python "<工程模式根目录>\tools\workflow_gate.py" provide_context "<全部提问回答原文>"
```
- 执行力学估算，返回 A/B/C 搭建方式 + D/E 并行策略选项
- **MUST wait for user to choose A/B/C and D/E!**
- 🔴 **调用前必须确认：总计子代理已 `analyst-bind`、十阶段走完、已 `set-subagent-plan`、回合已结束。**
  自查三连：`analyst-status` / `stage-status` / `subagent-plan`，再用 `list_agents` 确认它结束。
  **它没跑完就调 #3，后面的 `select` 会直接报 `MODE2_ANALYST_REQUIRED`。**

> 📌 状态流转（**新流程，2026-10-13 重排**）：
> `mode_confirm_asked`（确认模式+D/E+A/B/C）
>   →（传模式确认答案）→ `depth_asked`（强/弱需求）
>   →（传强度答案）→ `context_asked`（总计子代理十阶段 + 按需提问）
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

### 第 5 步：`select` 确认选择（前置：总计子代理已完成十阶段 + 编制）

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
- 🔴 **【2026-10-28】`select` 的硬前置：总计子代理已 `analyst-bind` 且十阶段走完。**
  未 spawn / 未绑定 / 阶段缺项 → `select` 返回 **`MODE2_ANALYST_REQUIRED`**（绑定缺失的那次）
  或子代理自己调阶段命令被 **`ANALYST_ACTOR_REQUIRED`** 拒。
  **看到 `MODE2_ANALYST_REQUIRED` 就说明第 3.6 步没做**（八成是主对话自己把十阶段跑了）：
    → spawn 总计子代理（§3.6）→ 让它 `analyst-bind` 并补齐缺的阶段 → 再 `select`。
- 🔴 **`select` 返回的 `wave1` = 编制里的【全部建模房间】+【总理子代理】。**
  房间数与细分**由总计子代理决定**，主对话**不得**改用固定表（旧固定 3 房间 / 5 房间已废弃）。

### 第 3.5 步：★ 模式二新流程（全部为【代码强制】，不是提示词约定）

> 用户要求：「所有改动都要写在代码里面，强制执行的」。
> 以下每一条都由 `workflow_gate.py` / `mode2_flow.py` 的代码校验，**违反会被拒绝**。

#### 3.5.1 题数硬约束（原「强需求 17 题 / 弱需求 5 题」已取消）

| 参数需求强度 | 允许题数（闭区间，可等于上下界） |
|---|---|
| 强需求 `full` | **10 ~ 50** |
| 弱需求 `lite` | **5 ~ 10** |

- 由 `mode2_flow.validate_question_budget()` 强制；超上限直接拒绝，
  收尾时未达下限同样拒绝交付。
- `provide_context` 第 1 次的返回里会带 `question_budget`（含 min/max/候选池大小）。

#### 3.5.2 取消「一股脑全问」→ 改为【按需提问】

题目必须**挂在已经分析到的阶段**上，并写明理由：

```powershell
# 1) 先推进到某阶段（必须按序，跳阶段会被拒）
python "<根>\tools\workflow_gate.py" mode2 begin-stage TaskUnderstanding
# 2) 分析到确实缺信息时，才问对应的问题
python "<根>\tools\workflow_gate.py" mode2 ask "使用场合是什么" `
       --stage TaskUnderstanding --reason "决定材料与防护等级"
```

- 阶段未 `begin-stage` 就提问 → **拒绝**（这就是「不许开头把题全抛出去」的机器判据）。
- 无理由提问 → **拒绝**。
- 查看进度：`python workflow_gate.py stage-status`

#### 3.5.3 十阶段设计流水线（必须按序）

```
TaskUnderstanding → FunctionalDecomposition → PhysicsReasoning →
PrincipleSynthesis → MechanismSynthesis → RecursivePartReasoning →
PartSynthesis → EngineeringValidation → DesignIteration → ExplainableDesignOutput
```

- 跳阶段被拒；只有 `DesignIteration` 可以发起回退。

#### 3.5.4 子代理数量【不再固定 3 个】→ 由「总计子代理」决定

```powershell
python "<根>\tools\workflow_gate.py" mode2 set-subagent-plan `
       --rooms-json "@rooms.json" --rationale "按功能分解得到 N 个模块" `
       --decided-by "总计子代理"
```

- `--decided-by` **必须是「总计子代理」**，否则报「越权」并拒绝。
- 每个房间必须写 `parts`（负责哪些零件）与 `why`（为什么需要它）。
- 依赖不得悬空。数量范围 1~24。
- 🔴 **本条命令只能由【总计子代理本人】执行**：`mode2_flow.py` 会比对调用方的
  `DSH_SESSION_ID` 与 `analyst-bind` 落盘的 analyst（也可显式 `--actor`），
  **主对话直接调 → `ANALYST_ACTOR_REQUIRED`**；`select` 也会校验 analyst，缺失 → `MODE2_ANALYST_REQUIRED`。
- 🔴 **编制 = 全系统房间表的唯一来源。** `select` 的 `wave1` 直接取编制里的全部建模房间
  （+ 代码固定追加的「总理子代理」）。**旧的代码默认房间表已废弃，不得作为兜底**；
  房间数量与细分由总计子代理按功能分解决定（可能是 2 个，也可能是 7 个）。
- 「总理子代理」（`type=prime`，`fixed=True`）由代码自动追加，**不要写进编制 JSON**。

#### 3.5.5 固定增设「总理子代理」（不可被编制顶掉）

房间名常量 `总理子代理`，类型 `prime`，`fixed=True`。职责（代码化）：

1. 统一规划关键尺寸，给各建模小屋下发同一套尺寸口径；
2. 接收各建模小屋上报（**缺字段即拒绝**）：
   三道防线凭据链接 / 尺寸 / 名称 / 直接接触配合的零件；
3. 校验相邻零件尺寸一致性；不一致 → `escalate_to_parent=true`，交父代理干涉；
4. 持久维护零件数据台账，支持按零件反查关联；
5. 全部完成后汇总导出 docx 交主对话。

```powershell
# 建模小屋 save() 之后必须上报（缺任一字段都会被拒）
python "<根>\tools\workflow_gate.py" mode2 prime-report --json "@report.json"
# 总理子代理做一致性校验
python "<根>\tools\workflow_gate.py" mode2 check-consistency
```

#### 3.5.5.1 ★★ 总理子代理必须【统一核实】数据后才能结束

用户要求：「总理子代理要统一核实过数据没有错误了，才能结束」
　　　　　「其总理子代理也要核实的相关数据是否一致，必须要保证真实」

```powershell
python "<根>\tools\workflow_gate.py" mode2 prime-verify          # 统一核实
python "<根>\tools\workflow_gate.py" mode2 prime-verify-status   # 读回结论
```

**"核实"不是自我声明**（谁都能写 `verified=True`），而是代码从磁盘重算：

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

任一不满足即**拒绝宣告完成**（拒绝归档）：十阶段未走完 / 题数不达标 /
编制缺失或非总计子代理决定 / 有零件未上报总理 / 存在未处理的相邻尺寸不一致。

### 第 6 步：创建子代理小屋（房间表由总计子代理决定！）

> 🔴 **【2026-10-28】** 房间表 = `select` 返回的 `wave1` = **编制里的全部建模房间 + 总理子代理**。
> **数量与细分由总计子代理决定** —— 不要写死旧的固定五房间表
> （结构件 / 传动机构 / 壳体机架 / 总装与验证 / 工程图输出），
> 也不要写死「第1波 = 结构件 + 传动机构 + 壳体机架」这种固定三房间说法。

**并行模式 D —— 三波式（总理与建模同波一起开）：**

```
wave1 = select.wave1 = 全部建模房间 + 总理子代理   ← 同时 fork
wave2 = 总装与验证（等 wave1 全部 room-end）
wave3 = 工程图输出（等 wave2 room-end）
```

**wave1 执行顺序（不可打乱）：**
1. **先**对每个房间 `python "<根>\tools\mode_gate.py" room-start <房间名>`
   （登记，**BEFORE** 创建 subagent；未 room-start 的房间 `subagent-assign` 会报 `ROOM_NOT_REGISTERED`）
2. **再**在同一条回复里**一次性 `subagent_fork` 全部房间**（含总理子代理）——
   ⚠️ 严禁只开一个、严禁分多条回复慢慢开：**波内房间必须同时启动**
3. **每个 fork 之后立刻**用返回的 id 绑定（漏了就永远 `unbound`）：
   ```powershell
   python "<根>\tools\mode_gate.py" subagent-assign <房间名> <subagent_fork 返回的 id>
   ```
4. 建模小屋 prompt 必须写「只做本房间零件」，并带上 `select` 返回的 `part_ownership` keywords。

**总理子代理（与 wave1 同时启动，长生命周期的监工）：**
- 开工：`python "<根>\tools\mode2_flow.py" prime-board` 看全局，`prime-room` 看职责。
- **循环**：每轮读 `python "<根>\tools\mode_gate.py" room-board --json`，**只看每个房间的 `state`**：
  - `running` / `starting` / `idle` → 正常在干活，**不许催、不许判死**；
  - `waiting_sw` → **在等 SW 锁，属正常等待**，只能等，**禁止判死、禁止 reset**；
  - `unbound` → 平台 alive 但没 `subagent-assign`，**提示父代理补 assign**（不是死亡）；
  - `dead` → **只有这个才允许报**，并建议父代理 `recover` / `restart`；
  - `done` → 收报告。
- 收件：建模小屋 `save()` 后 `prime-report`；总理做 `check-consistency` → `prime-verify`。
- 结束：wave3 出图完成后 `python "<根>\tools\mode_gate.py" room-end 总理子代理`。
- **prime 房间不计入 wave 完成判定**（长生命周期），但**收尾前必须 room-end**。

**串行模式 E 严格顺序（不可打乱）：**
1. **先**调 `mode_gate.py room-start <房间名>`（登记， BEFORE 创建 subagent）
2. **再**用 subagent/subagent_fork 创建该小屋，**并立刻** `subagent-assign <房间> <id>`
3. 等待该小屋完成（`room-board` 看 `state=done`，或 `list_agents` 检查）
4. 调 `mode_gate.py room-end <房间名>`（解锁，才能开下一个）
5. 重复 1~4 处理下一个房间

**严禁一次性创建多个 subagent（串行模式）！** 串行锁会拒绝第二个 room-start。
- 🚫【铁律】所有小屋创建完毕前，主对话禁止调用 sw_bridge.py 做任何建模操作！

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
  · `mode2_flow.py` 的十阶段与编制命令（analyst-bind / begin-stage / mark-stage-done / ask /
    answer-question / refresh-stage / set-subagent-plan）**只能由总计子代理调**。
  · 旧规则「门禁每一步都在主对话完成、不进子代理」**已废除（deprecated）** —— 它与
    "spawn 总计子代理"直接矛盾，实测导致主对话自演十阶段、总计子代理从未创建。
- 🔴 **子代理死活只看 `python "<根>\tools\mode_gate.py" room-board --json` 的 `state`**：
  · `waiting_sw`（在排 SW 锁）/ `running` / `starting` / `idle` = **正常，禁止判死**；
  · `unbound` = 平台 alive 但没 `subagent-assign` → **补 assign**（不是僵尸）；
  · `dead` = 才允许 `recover` / `restart`，且先看 `safe_to_restart`；
  · **禁止对 `running` / `waiting_sw` / `idle` 的房间 `reset`**（会把还在干活的小屋清掉）。
- 严禁跳过门禁直接开始设计

## Violation Handling

- Skip workflow_gate = **CODE BLOCKED**
- Mode 2 without subagent rooms = **CODE BLOCKED**
- **主对话自己跑十阶段 / 自己调 `set-subagent-plan`** = **CODE BLOCKED**（`ANALYST_ACTOR_REQUIRED`）
- **没 spawn 总计子代理就 `select`** = **CODE BLOCKED**（`MODE2_ANALYST_REQUIRED`）
- **`fork` 后漏 `subagent-assign`** = 房间显示 `unbound`，上报与判定全部失真
- Used subagent in Mode 1/3 = **FAIL**

## Example Flow

User: "Design a planetary reducer"

AI must（门禁命令在主对话；十阶段在**总计子代理**里）：
1. Output: [MODE SELECTED] Mode 2
2. 执行 `python "<工程模式根目录>\tools\workflow_gate.py" init "Design a planetary reducer"`
   → 返回**第 1 步：确认模式 + ① 并行策略 D/E + ② 是否 AI 自主 A/B/C**
   → **把这两问呈现给用户，必须等用户回答！**
3. 用户回答后，执行
   `... workflow_gate.py provide_context "<并行策略+搭建方式的答案>"`
   → 返回**第 2 步：参数需求强度（强需求 / 不强）** → 呈现给用户，等作答
4. 用户答完参数强度后，**再次**执行
   `... workflow_gate.py provide_context "<参数强度的答案>"`
   → 返回**先启动总计子代理（十阶段）+ 按需提问指引**
5. ★ **spawn 总计子代理**（`subagent_fork`，description="总计子代理（十阶段分析）"）：
   - 它第一步 `python "<根>\tools\mode2_flow.py" analyst-bind`（绑自己 sessionId）
   - 按序 `begin-stage` / `mark-stage-done` 走完十阶段（缺阶段会被拒）
   - 缺信息时 `python "<根>\tools\ask_user.py" --room "总计子代理" --child <自己的 sessionId>
     --header ... --question ...`（子代理不能直接 ask_user_question）
   - 分析完 `python "<根>\tools\mode2_flow.py" set-subagent-plan --rooms-json <file>
     --rationale "..." --decided-by "总计子代理"`
   - 结束回合，返回编制摘要
6. 主对话 `wait_agent` / `list_agents` 等它结束，再执行
   `... provide_context "<全部提问的答案>"` → 力学估算 + 落盘 `tools/load_cases/gate_load_case.json`
7. 问 A/B/C 与 D/E → 执行 `... workflow_gate.py select C,E`
   → `wave1` = 编制里的**全部建模房间 + 总理子代理**（数量由总计子代理决定）
   → 返回 **`MODE2_ANALYST_REQUIRED`** = 第 5 步没做（没 spawn 总计子代理）
8. **wave1 一次性 fork 全部**（建模房间 + 总理子代理写在同一条回复里）；
   每个 fork 后立刻 `python "<根>\tools\mode_gate.py" subagent-assign <房间> <id>`
9. 总理子代理持续 `mode_gate.py room-board --json` 盯各房间 `state`
   （`waiting_sw` 只是排队，不许判死）；wave2 = 总装、wave3 = 出图；
   收尾前 `room-end 总理子代理`。