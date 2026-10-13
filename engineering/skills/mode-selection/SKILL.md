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

## Rules (Code-Enforced, Not Just Prompts)

**Rule 0: Any design work MUST go through this gate first.**

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

### 第 1 步：`init`（两段式 · 第 1 段）

```powershell
python "<工程模式根目录>\tools\workflow_gate.py" init "<设计任务描述>"
```

- **作用**: 返回 **第 0 题（唯一一题）** —— 参数需求强度，**必须原样呈现给用户**
- 第 0 题：
  1. 【参数需求强度】本次设计是否需要严格把关参数（载荷/尺寸/公差/材料都要有据可依）？
     - 选项1：**强需求**：需要严格校核，参数要有设计依据（追问详细信息）
     - 选项2：**不强**：先出大致结构即可，细节由你按经验定（只问关键几项）
- **MUST wait for user to answer this question before proceeding!**

> ⚠️ **代码是唯一事实来源**：题目内容由 `workflow_gate.py` 的 `QUESTION_SPEC` /
> `question_spec_lite()` 生成，本文件不再硬编码题目列表。
> 若命令返回的题目与此处描述不一致，**以命令返回为准**。

### 第 2 步：`provide_context`（第 2 段发题 + 力学估算）

**本命令会被调用两次，靠状态机 step 自动区分：**

**第 1 次**（参数 = 用户对第 0 题的回答原文）
```powershell
python "<工程模式根目录>\tools\workflow_gate.py" provide_context "<第0题回答原文>"
```
- 判定参数需求强度并返回**第二段问题**：
  - 回答含「强需求 / 严格校核」→ **强需求**：题数须落在 **10 ~ 50** 之间
  - 回答含「不强 / 精简」或含糊 → **弱需求**：题数须落在 **5 ~ 10** 之间
  - ⚠️ **旧口径（强需求固定 17 题 / 弱需求固定 5 题）已废除**：
    现在题库只作【候选池】，实际问几题由 AI 按十阶段分析进度**按需决定**，
    但必须落在上述区间内（见 3.5.1 / 3.5.2）。
- **MUST 把第二段问题原样呈现给用户，等用户完整作答！**

**第 2 次**（参数 = 用户对第二段全部问题的回答原文）
```powershell
python "<工程模式根目录>\tools\workflow_gate.py" provide_context "<第二段回答原文>"
```
- 执行力学估算，返回 A/B/C 搭建方式 + D/E 并行策略选项
- **MUST wait for user to choose A/B/C and D/E!**

> 📌 状态流转：`depth_asked` →（传第0题答案）→ `context_asked` →（传第二段答案）→ `mechanics_done`。
> 不要跳步；`step` 不对时命令会直接报错拒绝。

> 📌 **【BUG-01/07 修复】载荷数值口径**
> 力学估算会**优先读取用户明确写出的载荷数值**（如「100N」「10kg」「0.1kN」），
> 关键词（冲击/动态）只用于确定**动载系数**，不再放大用户给的额定值。
> 若返回值带 `load_is_estimate=true`，说明用户没给数值、当前是估算基准，
> **必须向用户复核后再继续**。
> 命令同时把载荷工况落盘到 `tools/load_cases/gate_load_case.json`，
> **physics 校核必须用该文件**（额定载荷 = `magnitude_n`），
> 严禁另起一套数值（那正是 BUG-07「门禁 1000N / physics 100N」的根因）。

### 第 3 步：`select` 确认选择

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

### 第 4 步：创建子代理小屋（必须创建全部小屋！）

**串行模式严格顺序（不可打乱）：**
1. **先**调 `mode_gate.py room-start <房间名>`（登记， BEFORE 创建 subagent）
2. **再**用 subagent/subagent_fork 创建该小屋
3. 等待该小屋完成（list_agents 检查）
4. 调 interrupt_agent 停止
5. 调 `mode_gate.py room-end <房间名>`（解锁，才能开下一个）
6. 重复 1~5 处理下一个房间

**严禁一次性创建多个 subagent！** 串行锁会拒绝第二个 room-start。
- 并行模式：可同时创建多个小屋，各自独立 room-start
- 🚫【铁律】所有小屋创建完毕前，主对话禁止调用 sw_bridge.py 做任何建模操作！

## ⚠️ 关键规则

- **【BUG-03 修复】必须用命令行脚本；本环境没有任何门禁类 DSH 工具！**
  - ✅ 正确：`python "<工程模式根目录>\tools\workflow_gate.py" init "<任务>"`
  - ❌ 错误：凭印象调用任意门禁类 DSH 工具（**本环境不存在，必然失败**）
- 子命令名用**下划线**：`init` / `provide_context` / `select` / `confirm-assembly`
- **`provide_context` 必须调用两次**：第一次传第0题答案，第二次传第二段答案
- **`select` 支持 `C,E` / `C E` / `"C, E"` / `--choice C --parallel E` 等写法**（BUG-03）
- 所有门禁步骤必须在主对话完成，不要进子代理
- 严禁跳过门禁直接开始设计

## Violation Handling

- Skip workflow_gate = **CODE BLOCKED**
- Mode 2 without subagent rooms = **CODE BLOCKED**
- Used subagent in Mode 1/3 = **FAIL**

## Example Flow

User: "Design a planetary reducer"

AI must (all in MAIN conversation, NOT in subagent):
1. Output: [MODE SELECTED] Mode 2
2. 执行 `python "<工程模式根目录>\tools\workflow_gate.py" init "Design a planetary reducer"`
   → 返回**第 0 题：参数需求强度**
   → **把这一题呈现给用户，必须等用户回答！**
3. 用户回答第 0 题后，执行
   `... workflow_gate.py provide_context "<第0题答案>"`
   → 返回第二段问题（强需求=17题 / 不强=精简5题）
   → **把第二段问题呈现给用户，等用户完整作答！**
4. 用户答完第二段后，**再次**执行
   `... workflow_gate.py provide_context "<第二段答案>"`
   → 返回力学估算 + A/B/C + D/E 选项，等用户选择
   → 同时落盘 `tools/load_cases/gate_load_case.json`（physics 校核必须用这一份数值）
5. 执行 `... workflow_gate.py select C,E`（choice=用户选择的 A/B/C，并行=D/E）
6. 创建全部 5 个小屋（结构件→传动机构→壳体机架→总装与验证→工程图输出），串行模式逐个创建
7. 每个小屋完成后用 interrupt_agent 清理，再创建下一个

**IMPORTANT: All workflow_gate steps must be done in MAIN conversation first!**
Subagents are ONLY for actual modeling work, NOT for asking questions.