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

## 🔴 Mode 2 强制门禁流程（必须用 DSH 工具调用，不是命令行！）

**Mode 2（大型复杂器械装配）任务，必须先在主对话中调用以下 DSH 工具：**

### 第 1 步：调用 `workflow-gate-init` 工具

- **工具名**: `workflow-gate-init`
- **参数**: `task` = 设计任务描述
- **作用**: 工具会生成 5 个背景问题，**你必须把这些问题原样呈现给用户**
- 5 个背景问题：
  1. 【使用场合】Environment? (outdoor/indoor/underwater/high temp/etc.)
  2. 【负载类型】Load type? (static/dynamic/impact/cyclic/vibration)
  3. 【运行方式】Drive method? (manual/electric/hydraulic/pneumatic/servo)
  4. 【工作频率】Operating frequency? (continuous/intermittent/occasional)
  5. 【特殊要求】Special requirements? (waterproof/dustproof/explosion-proof/lightweight)
- **MUST wait for user to answer ALL questions before proceeding!**

### 第 2 步：用户回答后，调用 `workflow-gate-provide-context` 工具

- **必须调用 `workflow-gate-provide-context` 工具**（参数 context=用户回答原文）
- 工具执行力学估算，返回 A/B/C 搭建方式 + D/E 并行策略选项
- **MUST wait for user to choose A/B/C and D/E!**

### 第 3 步：调用 `workflow-gate-select` 工具确认选择

- **工具名**: `workflow-gate-select`
- **参数**: `choice` = A / B / C
- **A/B/C**: 搭建方式（完全自主/部分自主/步步确认）
- **MUST wait for user to confirm selection!**

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

- **必须用 DSH 工具调用，不要用命令行 `python workflow_gate.py`！**
- 工具名是 `workflow-gate-init` 和 `workflow-gate-select`（带连字符，不是下划线）
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
2. 调用工具 `workflow-gate-init`（task="Design a planetary reducer"）
   → 工具返回 5 个背景问题
   → **把 5 个问题呈现给用户，必须等用户回答！**
3. 用户回答后，展示力学估算 + A/B/C 选项，等用户选择
4. 调用工具 `workflow-gate-select`（choice=用户选择的 A/B/C）
5. 创建全部 5 个小屋（结构件→传动机构→壳体机架→总装与验证→工程图输出），串行模式逐个创建
6. 每个小屋完成后用 interrupt_agent 清理，再创建下一个

**IMPORTANT: All workflow_gate steps must be done in MAIN conversation first!**
Subagents are ONLY for actual modeling work, NOT for asking questions.