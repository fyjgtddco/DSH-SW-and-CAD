# 🔧 DSH Engineering Mode — 工程模式

[English](#english) | [中文](#中文)

---

## 中文

### 概述

**工程模式** 是 [DeepSeek Harness](https://github.com/deepseek-ai/dsh) 的一个 Agent Preset（代理预设），专门用于 **CAD/SolidWorks 机械设计**。

它为核心三大模式（标准模式、PTC 模式、极简模式）新增了一个**工程模式**，让 AI 化身为专业的机械设计工程师，以"**完成工图**"为唯一核心目标。

### 特性

- 🎯 **目标驱动**：以"完成工图"为唯一核心目标，所有行动都围绕这一目标展开
- 📐 **专业流程**：严格执行"分析→设计→验证"三部曲
- 🔄 **闭环验证**：每完成一个零件必须验证，每完成一次装配必须检查干涉
- 🛠️ **CAD/SW 就绪**：内置 AutoCAD 和 SolidWorks 工作流 Skill
- 📋 **计划模式**：工程设计导向的计划模式，强调设计评审和方案验证
- 🎯 **Goal 追踪**：使用 Goal 系统追踪"完成工图"的总体目标

### 设计流程

```
第一阶段：需求分析与规划
  ├── 理解需求
  ├── 尺寸合理性分析
  ├── 运行可行性分析
  ├── 制定设计计划
  └── 创建 Goal 目标

第二阶段：详细设计
  ├── 草图绘制
  ├── 特征建模
  ├── 零件验证
  ├── 组合零件
  └── 输出中间结果

第三阶段：装配与验证
  ├── 总装配
  ├── 干涉检查
  ├── 运动模拟
  ├── 最终验证
  ├── 输出工程图纸
  └── 更新 Goal 为完成
```

### 安装

#### 一键安装（推荐）

**Windows（PowerShell）：**

```powershell
# 以管理员身份运行 PowerShell，然后执行：
.\install.ps1
```

**macOS / Linux：**

```bash
chmod +x install.sh
./install.sh
```

#### 手动安装

1. 将 `engineering/` 目录复制到 DSH 的 Agent Presets 目录：

   **Windows：**
   ```powershell
   Copy-Item -Recurse .\engineering\ $env:USERPROFILE\.dsh\.agent-presets\engineering\
   ```

   **macOS / Linux：**
   ```bash
   cp -r ./engineering/ ~/.dsh/.agent-presets/engineering/
   ```

2. **可选**：安装 CAD 工作流 Skill：

   **Windows：**
   ```powershell
   Copy-Item -Recurse .\engineering\skills\cad-workflow\ $env:USERPROFILE\.dsh\skills\cad-workflow\
   Copy-Item -Recurse .\engineering\skills\sw-design\ $env:USERPROFILE\.dsh\skills\sw-design\
   ```

   **macOS / Linux：**
   ```bash
   cp -r ./engineering/skills/cad-workflow/ ~/.dsh/skills/cad-workflow/
   cp -r ./engineering/skills/sw-design/ ~/.dsh/skills/sw-design/
   ```

### 使用

1. 安装完成后，重启 DeepSeek Harness
2. 在会话界面中，选择模式为 **工程模式**（Engineering Mode）
3. AI 将以机械设计工程师的身份开始工作
4. 告诉它你的设计需求，它会自动执行"分析→设计→验证"流程

### QQ 机器人任务通知

本预设内置了 **QQ 机器人任务通知** 功能，会自动将 DSH 任务状态推送到你的 QQ 群或私聊。

**消息格式：**
```
📁 {项目名}
📋 {状态/内容}
```

**支持监听的事件：**
- `goal/changed` — 目标创建、完成、阻塞、恢复
- `subagent/start` — 子代理启动
- `subagent/end` — 子代理结束（成功/失败）
- `agent/error` — Agent 出错

**配置方法：**

**群聊通知：**
```powershell
$env:QQ_GROUP_OPEN_ID = "你的群group_open_id"
```

**私聊通知（两种方式）：**

方式一：直接用 open_id（需先在 QQ 开放平台获取）
```powershell
$env:QQ_USER_OPEN_ID = "用户的open_id"
```

方式二：用 QQ 号（需用户先给机器人发一条消息，机器人会自动记录 open_id）
```powershell
$env:QQ_PRIVATE_TARGET = "639424706"
```

**获取 open_id 的方法：**
1. 让机器人加入群后，在群里 @机器人 发送任意消息
2. 或在 QQ 开放平台 → 你的应用 → 事件订阅 → 查看群/私聊消息事件中的 `member.openId` / `author.openid`

> 私聊模式下，用户需要**先给机器人发一条消息**，机器人才能获取其 open_id 并用于后续通知。

### 自定义

你可以修改 `engineering/agent.cordis.yml` 中的 `persona` 配置，自定义 AI 的行为和指令。

---

## English

### Overview

**Engineering Mode** is an Agent Preset for [DeepSeek Harness](https://github.com/deepseek-ai/dsh), specifically designed for **CAD/SolidWorks mechanical design**.

It adds an **Engineering Mode** alongside the three core modes (Standard, PTC Code, Minimal), transforming the AI into a professional mechanical design engineer with a single core mission: **"Complete the Engineering Drawing"**.

### Features

- 🎯 **Goal-Driven**: Everything revolves around the singular goal of completing the engineering drawing
- 📐 **Professional Workflow**: Strictly follows the "Analyze → Design → Verify" trilogy
- 🔄 **Closed-Loop Validation**: Every part is verified, every assembly is checked for interference
- 🛠️ **CAD/SW Ready**: Built-in AutoCAD and SolidWorks workflow skills
- 📋 **Plan Mode**: Engineering-oriented planning mode with design review emphasis
- 🎯 **Goal Tracking**: Uses the Goal system to track the overall objective

### Design Process

```
Phase 1: Requirements Analysis & Planning
  ├── Understand requirements
  ├── Dimensional feasibility analysis
  ├── Operational feasibility analysis
  ├── Create design plan
  └── Set Goal

Phase 2: Detailed Design
  ├── Sketching
  ├── Feature modeling
  ├── Part validation
  ├── Assembly
  └── Intermediate results

Phase 3: Assembly & Verification
  ├── Final assembly
  ├── Interference check
  ├── Motion simulation
  ├── Final verification
  ├── Engineering drawings
  └── Mark Goal as complete
```

### Installation

**Windows (PowerShell):**
```powershell
.\install.ps1
```

**macOS / Linux:**
```bash
chmod +x install.sh
./install.sh
```

### Usage

1. Restart DeepSeek Harness after installation
2. In the session interface, select **Engineering Mode**
3. The AI will act as a mechanical design engineer
4. Tell it your design requirements, and it will follow the "Analyze → Design → Verify" workflow

### License

MIT