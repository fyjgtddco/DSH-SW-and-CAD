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
> 旧版本文档写的是 `workflow-gate-init` / `workflow-gate-provide-context` /
> `workflow-gate-select` 这类**DSH 工具名**。**本环境并不存在这些工具** ——
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
   - `<仓库根>\engineering\`
   - `%USERPROFILE%\.dsh\engineering\`
   - `%USERPROFILE%\.dsh\.agent-presets\engineering\`

⚠️ 注意：`.dsh\skills\*` 下**只有 SKILL.md，没有 tools/** —— 那不是工程模式根目录。

下面所有 `<工程模式根目录>` 都请替换成上面找到的**绝对路径**。

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
  - 回答含「强需求 / 严格校核」→ 返回 **全量 17 题**
    （A 结构形态 5 题 + B 工况载荷 4 题 + C 驱动与运行 3 题 + D 制造条件 3 题 + E 特殊要求 1 题 + Z 开放补充 1 题）
  - 回答含「不强 / 精简」或含糊 → 返回 **精简 5 题**
    （机构类型 / 臂长行程 / 额定负载 / 负载类型 / 开放补充）
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

- **【BUG-03 修复】必须用命令行脚本，本环境没有 `workflow-gate-*` 这些 DSH 工具！**
  - ✅ 正确：`python "<工程模式根目录>\tools\workflow_gate.py" init "<任务>"`
  - ❌ 错误：调用名为 `workflow-gate-init` 的 DSH 工具（**不存在，必然失败**）
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