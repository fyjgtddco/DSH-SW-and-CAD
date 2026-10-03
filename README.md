# 🔧 DSH Engineering Mode · 工程模式

> **面向 CAD / SolidWorks 机械设计的 DeepSeek Harness Agent 预设**
> An Agent Preset for DeepSeek Harness, built for CAD / SolidWorks mechanical design.

以 **「完成工图」** 为唯一核心目标，把「分析 → 设计 → 验证」做成**代码级强制**的闭环 ——
不是写在提示词里靠模型自觉，而是用 Python 门禁脚本到点拦截、不通过不放行。

> **语言 / Languages** ·
> [中文](#中文) · [English](#english) · [Français](#français) · [Español](#español) · [Русский](#русский) ·
> [技术附录 / Appendix](#技术附录--appendix)

| 项目 | 值 |
|------|-----|
| 目标平台 | DeepSeek Harness **0.2.0**（Electron 桌面版 / `dsh web`） |
| 预设 ID | `engineering`（显示名「工程模式」，`order: 4`） |
| UI 插件 | `dsh-engineering-ui` **v1.1.0** |
| 技能数 | **7** 个 |
| 门禁/工具脚本 | **12** 个（另 3 个为纯库模块） |
| 物理仿真模块 | **14** 个 |
| 回归测试 | **8** 个（覆盖 Bug-34 ~ Bug-44） |
| 许可 | MIT |

---

## 中文

### 这是什么

**工程模式**是 [DeepSeek Harness](https://github.com/deepseek-ai/dsh) 的一个 Agent Preset。
装上之后，AI 不再是"通用的聊天助手"，而是**一名机械设计工程师**：

- 它的**唯一使命是产出合格的工程图纸**；
- 它**真的能驱动 SolidWorks 建模**（通过 Python `win32com`，支持 SW 2018~2024）；
- 它**自己会做有限元校核**（纯 Python + numpy，不需要外部 FEA 软件）；
- 它**会自己判断能不能交付**——三大防线不过，它不放行。

### 核心特性

| 特性 | 说明 |
|------|------|
| 🎯 **目标驱动** | 一切围绕「完成工图」；用 Goal 系统追踪总目标 |
| 📐 **三段式流程** | 强制「分析 → 设计 → 验证」，不允许跳步 |
| 🔒 **代码级门禁** | 门禁是 **Python 脚本**，不是提示词。绕过会留痕 |
| 🛡️ **三大防线** | 材料 / 物理 / 领域，逐房间 + 全任务双重校验 |
| 🧩 **多屋协作** | 大型装配体拆成"房间"，子代理串行或并行建模 |
| 🔬 **物理在环** | 生成 → FEA → 疲劳校核 → 反馈修正 → 再生成 |
| 📏 **疲劳/寿命** | Basquin S-N + Marin 修正 + Miner 累积损伤（默认 30 年） |
| 🖥️ **三栏工作区** | 主对话 + 频道列表 + 子代理实时工作区 |
| ❓ **阻塞式代问** | 子代理无法直接提问？本插件把它桥接到主对话真正挂起 |
| 🧰 **7 个技能** | 模式选择、装配编排、CAD、SW 设计、桥接、SW→CAD、物理在环 |
| 🚫 **不猜参数** | 材料、公差、配合类型不确定时必须问用户 |

### 设计流程

```
第一阶段：需求分析与规划
  ├── 模式选择（强制入口 · mode-selection）
  ├── 理解需求 → 尺寸合理性 → 运行可行性
  ├── 制定计划（todo_write）
  └── 创建 Goal

第二阶段：详细设计
  ├── 草图（完全定义）→ 特征建模 → 圆角/倒角/孔
  ├── 零件验证（质量属性 / 干涉 / 应力）
  ├── 组合成子装配体
  └── 输出中间结果给用户确认

第三阶段：装配与验证
  ├── 总装配 → 干涉检查 → 运动学分析
  ├── 最终验证（尺寸 / 公差 / 配合）
  ├── 输出工程图（三视图 / 剖视 / 局部放大）
  ├── 导出 SLDDRW / DWG / PDF / DXF
  └── 更新 Goal 为完成
```

### 三大防线（本项目的核心价值）

这是本项目与"普通 CAD 助手"最大的区别：**质量校验不再是建议，而是硬门禁**。

| # | 防线 | 产出凭据 | 判定 | 强制点 |
|---|------|---------|------|--------|
| ① | **材料防线** | 每个 `.sldprt` 旁的 `<零件>.material.json` | 材料缺失 / 密度=1000(水) / 跨族静默替代 → 不通过 | `sw_bridge.py run` 落盘即校验 |
| ② | **物理防线** | `reports/<房间>.physics.json` | 综合 FEA gates，`overall == FAIL` → 不通过 | `room-end` / `confirm-assembly` / 收尾 `select` |
| ③ | **领域防线** | `reports/<房间>.domain.json` | 按 `rules/<domain>_rules.json`，存在 CRITICAL 违规 → 不通过 | 同上 |

**四个强制点**（AI 走到这里必须过）：

```
sw_bridge.py run                 → 校验 ①（材料）
mode_gate.py room-end <房间>      → 校验 ①②③（该房间）
workflow_gate.py confirm-assembly → 开总装前校验全部建模房间 ①②③
workflow_gate.py select（收尾）   → 宣告完成前校验全任务 ①②③
```

**唯一合法绕行**：环境变量 `DSH_DEFENSE_BYPASS=1` 或命令 `--force`，
且**必定留痕**到 `reports/defense_bypass.json` —— 绝不静默放行。

**凭据防伪**：物理/领域凭据由 DSH 宿主用 **HMAC 签名**，宿主不可用时
**fail-closed**（验不了签 = 不通过）。所以手改 JSON 伪造校核结果是无效的。

### 子代理「多屋」架构（Mode 2）

大型装配体（如多关节机械臂）拆成若干**房间**，每个房间一个小屋（子代理）：

```
主对话（大屋）
   │
   ├── 第 1 波 · 独立爆发期（并行）
   │     ├── 结构件小屋
   │     ├── 传动机构小屋
   │     └── 壳体机架小屋
   │
   ├── 第 2 波 · 收敛汇合期（1 个）
   │     └── 总装与验证小屋     ← 需 confirm-assembly 门禁
   │
   └── 第 3 波 · 出图期（1 个）
         └── 工程图输出小屋
```

**关键约束**：

- **SolidWorks 是单实例程序** → `mode_gate.py` 在**进程级**做 FIFO 排队，
  公平轮转（单房间最多连占 3 条命令 / 180 秒，超时强制让位）；
- **零件归属强制**：`check-part` 校验"这个零件属于哪个房间"，
  越界产出会被拒绝并记录（Bug-38）；
- **禁止插队**：`send_message` 会打断小屋正在进行的建模，属违规；
  查进度一律用**旁路只读命令** `room-status` / `room-report-read`；
- **反夺舍**：小屋禁止调用 `mode_gate.py` / `workflow_gate.py` / `subagent_fork`
  等编排命令 —— 那些只能由主对话调用。

### 界面增强（`dsh-engineering-ui` 插件）

| 功能 | 说明 |
|------|------|
| **三栏工作区** | 有子代理时，主对话压到 3/4，右侧 1/4 显示所选子代理的实时输出、工具调用与运行状态；中间竖排频道列表可点击切换。**无子代理时整体隐藏**，回到全宽聊天 |
| **阻塞式代问** | DSH 平台禁止子代理直接提问（`userQuestions.ask()` 校验 `agents.roots()`，子代理必抛 `DELEGATED_CALLER`）。本插件把提问路由到主对话真正挂起，用户作答后答案以 **tool_result** 回到提问方 |
| **收尾守卫** | 仅对 `engineering` 生效：任务未进入代码级终态时阻止对话提前结束（放行条件见插件 README） |
| **设置页「工程模式」** | 三层结构（概览 / 工作流与门禁 / 界面与显示）。第三层含 **「使用工程模式自带的子代理显示页」** 开关：开启则右侧子代理面板由本插件接管（并接管官方 `subagentchat` 入口），关闭则完全交回 DSH 官方 |

> 本插件**不注册任何模型工具**，不改变子代理调度、模型、权限或上下文。

### 安装

#### 方式一：DSH 0.2.0 一键安装（推荐）

```powershell
# Windows PowerShell（无需管理员）
cd <仓库目录>
.\install-0.2.0.ps1

# 只装指定 profile
.\install-0.2.0.ps1 -Profile desktop
```

> ⚠️ **0.2.0 的关键变化**：DSH 0.2.0 **不再**从 `~/.dsh/.agent-presets/` 发现预设。
> 预设现在是 `package.json` 的 `dsh.profile.bundles` 声明的普通条目。
> 而且 **Electron 桌面版启动的是保留 profile `desktop`，不是 `web`**
> （`dsh web` 才启动 `web`）。所以 `install-0.2.0.ps1` **默认装进
> `~/.dsh/profiles/` 下的每一个 profile**，避免"装到 web 却在桌面版看不到"。

#### 方式二：DSH 0.1.x 安装脚本

```powershell
.\install.ps1          # Windows
```
```bash
chmod +x install.sh && ./install.sh    # macOS / Linux
```

#### 方式三：只装 UI 插件

```powershell
cd engineering
.\install-plugin.ps1
```

脚本会：① 清理历史权限残留 → ② 复制插件进 profile 的 `node_modules`
→ ③ 把插件登记进 profile 的 `package.json`（`dependencies` + `dsh.profile.bundles`）。
**profile 的 `cordis.patch.yml` 永不被覆盖。**

#### 安装后

**必须重启 DSH**（插件 host 半与宿主层配置都是进程级的）。
装好后新建会话，在模式选择里选 **「工程模式」**。

### 环境要求

| 组件 | 要求 | 必需性 |
|------|------|--------|
| DeepSeek Harness | 0.2.0（桌面版或 `dsh web`） | **必需** |
| Python | 3.8+ / 64 位 | **必需** |
| numpy | 任意近期版本 | **必需**（FEA） |
| `pywin32` | — | **必需**（驱动 SW / CAD） |
| SolidWorks | 2018 ~ 2024 | 建模需要 |
| AutoCAD | 任意近期版本 | 出 DWG / CADX 验证需要 |
| `Pillow`, `mss` | — | 可选（截图） |
| Gmsh + CalculiX / Code_Aster / Elmer | — | 可选（完整 3D FEA） |
| `skfem` | — | 可选（增强 2D 精度） |

### 使用示例

```
你：帮我画一个 11mm 的正方形，然后转成 CAD 图纸

AI：[MODE SELECTED] Mode 1
     [进度: 10%] 正在确认参数…
     → 生成建模脚本 → sw_bridge.py run
     → sw_bridge.py drawing  DSH_正方形.sldprt
     → sw_bridge.py dwg      DSH_正方形.sldprt
     → sw_bridge.py show     （截图验收）
     [进度: 100%] 零件已保存 / 工程图已生成 / DWG 已导出
```

```
你：帮我设计一个三自由度机械臂

AI：[MODE SELECTED] Mode 2
     强制门禁流程（命令行脚本）：
       1. workflow_gate.py init "<任务>"
       2. 把第 0 题（参数需求强度）用 ask_user_question 问给用户
       3. workflow_gate.py provide_context "<第0题回答>"   ← 返回第二段问题
       4. workflow_gate.py provide_context "<第二段回答>"   ← 力学估算 + A/B/C + D/E
       5. workflow_gate.py select C,E
     → 按 select 返回的房间数创建小屋（返回几个就开几个）
     → 波次推进 → 总装门禁 → 出图
```

### 常见问题

**Q：SolidWorks 启动失败？**
见 [`engineering/常见SW启动失败问题.md`](engineering/常见SW启动失败问题.md)。
按顺序排查：Python 位数 → `pywin32` → `sw_d.lic` → loader `netapi32.dll`
→ SW 是否装在非 C 盘 → 是否已有 SW 实例在跑 → 是否 DSH 沙盒拦截。
自检命令：`python engineering\tools\sw_bridge.py doctor`

**Q：`workflow-gate-init` 工具找不到？**
**本环境不存在这些 DSH 工具。** 历史文档曾这样写，照做必然失败。
真实入口是命令行脚本：`python engineering\tools\workflow_gate.py init "<任务>"`。

**Q：门禁报"缺少凭据"？**
先确认真实落盘目录。`_store.py` 是状态目录的**单一事实源**，
优先级：`DSH_STATE_DIR` → `DSH_ENGINEERING_ROOT` → 探测到的工程模式根 → 脚本所在目录。
建议显式设置：

```powershell
$env:DSH_ENGINEERING_ROOT = "C:\...\DSH-SW-and-CAD-main\engineering"
```

**Q：找不到"工程模式根目录"？**
根目录 = **同时包含 `tools\mode_gate.py` 与 `tools\workflow_gate.py` 的那个目录**。
先跑 `python engineering\tools\_root.py` 让它打印出来。
注意 `~/.dsh/skills/*` 下**只有 SKILL.md，没有 tools/**，那不是根目录。

**Q：可以把 `- id: permission` 写进 `agent.cordis.yml` 吗？**
**绝对不要。** `@deepseek-ai/dsh-permission-presets` 是宿主层进程级服务，
preset 里再声明一次 = 重复注册同名服务，loader 直接拒绝，后果是
**新建会话 / 切预设 / 选模型三件事一起坏**。

---

## English

### What this is

**Engineering Mode** is an Agent Preset for [DeepSeek Harness](https://github.com/deepseek-ai/dsh).
Once installed, the AI stops being a general chat assistant and becomes a
**professional mechanical design engineer**:

- Its **single mission** is to deliver a valid engineering drawing;
- It **actually drives SolidWorks** (via Python `win32com`, SW 2018–2024);
- It **runs its own FEA** (pure Python + numpy — no external FEA package needed);
- It **decides for itself whether delivery is allowed** — fail the three defense
  lines and it will not release the work.

### Key features

| Feature | Description |
|---------|-------------|
| 🎯 **Goal-driven** | Everything serves "complete the drawing"; tracked via the Goal system |
| 📐 **Three-phase flow** | Enforced "Analyze → Design → Verify", no step skipping |
| 🔒 **Code-level gates** | Gates are **Python scripts**, not prompt suggestions; bypassing leaves an audit trail |
| 🛡️ **Three defense lines** | Material / Physics / Domain — checked per room *and* per task |
| 🧩 **Multi-room** | Large assemblies split into "rooms", modeled by serial or parallel subagents |
| 🔬 **Physics-in-the-loop** | Generate → FEA → fatigue check → refine → regenerate |
| 📏 **Fatigue & life** | Basquin S-N + Marin factors + Miner cumulative damage (30-year default) |
| 🖥️ **Three-column workspace** | Main chat + channel list + live subagent workspace |
| ❓ **Blocking ask bridge** | Subagents cannot ask the user directly — this plugin routes the question to the main chat and truly suspends |
| 🧰 **7 skills** | Mode selection, assembly orchestration, CAD, SW design, bridge, SW→CAD, physics-in-the-loop |
| 🚫 **Never guesses** | Materials, tolerances, fit types are always confirmed with the user |

### Design workflow

```
Phase 1 — Requirements & planning
  ├── Mode selection (mandatory entry · mode-selection)
  ├── Requirements → dimensional feasibility → operational feasibility
  ├── Plan (todo_write)
  └── Create Goal

Phase 2 — Detailed design
  ├── Sketch (fully defined) → feature modeling → fillets/chamfers/holes
  ├── Part validation (mass properties / interference / stress)
  ├── Assemble into sub-assemblies
  └── Report intermediate results for user confirmation

Phase 3 — Assembly & verification
  ├── Final assembly → interference check → kinematic analysis
  ├── Final verification (dimensions / tolerances / fits)
  ├── Engineering drawings (three views / sections / details)
  ├── Export SLDDRW / DWG / PDF / DXF
  └── Mark Goal complete
```

### The three defense lines

This is what separates the project from an ordinary "CAD assistant":
**quality verification is a hard gate, not a suggestion.**

| # | Defense line | Credential | Fail condition | Enforced at |
|---|--------------|-----------|----------------|-------------|
| ① | **Material** | `<part>.material.json` beside each `.sldprt` | missing material / density = 1000 (water) / silent cross-family substitution | `sw_bridge.py run`, on write |
| ② | **Physics** | `reports/<room>.physics.json` | aggregated FEA gates; `overall == FAIL` | `room-end` / `confirm-assembly` / final `select` |
| ③ | **Domain** | `reports/<room>.domain.json` | per `rules/<domain>_rules.json`; any CRITICAL violation | same as ② |

**Four enforcement points** — the AI cannot pass without satisfying them:

```
sw_bridge.py run                  → check ① (material)
mode_gate.py room-end <room>      → check ①②③ for that room
workflow_gate.py confirm-assembly → check ①②③ for every modeling room before assembly
workflow_gate.py select (final)   → check ①②③ for the whole task before declaring done
```

**The only legal bypass** is the `DSH_DEFENSE_BYPASS=1` environment variable or a
command's `--force` — and it is **always recorded** to
`reports/defense_bypass.json`. Nothing is ever waved through silently.

**Credentials are signed.** Physics and domain credentials carry an **HMAC
signature** issued by the DSH host. If the host is unavailable the gate is
**fail-closed** (unverifiable = not passed), so hand-editing the JSON to fake a
passing check does not work.

### Multi-room subagent architecture (Mode 2)

A large assembly (say a multi-joint robotic arm) is split into **rooms**,
each handled by one subagent ("hut"):

```
Main chat (big hut)
   │
   ├── Wave 1 · independent burst (parallel)
   │     ├── structural parts hut
   │     ├── transmission hut
   │     └── housing & frame hut
   │
   ├── Wave 2 · convergence (one hut)
   │     └── assembly & verification      ← gated by confirm-assembly
   │
   └── Wave 3 · drawing output (one hut)
         └── engineering drawing hut
```

**Hard constraints:**

- **SolidWorks is single-instance** → `mode_gate.py` enforces a **process-level
  FIFO queue** with fair rotation (a room may hold SW for at most 3 consecutive
  commands or 180 seconds, then must yield);
- **Part ownership is enforced**: `check-part` verifies which room a part belongs
  to; out-of-scope output is rejected and logged (Bug-38);
- **Queue-jumping is forbidden**: `send_message` would interrupt a hut mid-build,
  so progress is read exclusively through side-channel read-only commands
  (`room-status` / `room-report-read`);
- **No usurpation**: huts may not call orchestration commands
  (`mode_gate.py`, `workflow_gate.py`, `subagent_fork`, …) — those belong to the
  main chat only.

### UI enhancements (the `dsh-engineering-ui` plugin)

| Feature | Description |
|---------|-------------|
| **Three-column workspace** | When subagents exist, the main chat is squeezed to 3/4 and the right 1/4 shows the selected subagent's live output, tool calls and status; a vertical channel list in between lets you switch. **Hidden entirely when there are no subagents**, returning to full-width chat |
| **Blocking ask bridge** | DSH forbids subagents from asking the user (`userQuestions.ask()` checks `agents.roots()`; a subagent always throws `DELEGATED_CALLER`). The plugin routes the question to the main chat, suspends there, and returns the answer to the asker as a **tool_result** |
| **Turn-stopping guard** | `engineering` only: prevents the conversation from ending before the task reaches a code-level terminal state |
| **Settings section** | Three layers (Overview / Workflow & gates / Interface & display). The third layer holds the **"use Engineering Mode's own subagent pane"** switch: when on, the plugin takes over the right-side subagent pane *and* the official `subagentchat` entry; when off, everything reverts to stock DSH |

> The plugin **registers no model tools** and does not alter subagent scheduling,
> model, permissions or context.

### Installation

#### Option 1 — DSH 0.2.0 one-liner (recommended)

```powershell
cd <repo>
.\install-0.2.0.ps1                 # installs into every profile under ~/.dsh/profiles
.\install-0.2.0.ps1 -Profile desktop  # or target specific profiles
```

> ⚠️ **What changed in 0.2.0**: DSH 0.2.0 **no longer** discovers presets from
> `~/.dsh/.agent-presets/`. Presets are now ordinary rows declared by
> `package.json`'s `dsh.profile.bundles`. Also, the **Electron desktop shell boots
> the reserved profile `desktop`, not `web`** (`dsh web` boots `web`) — so the
> script installs into **every** profile by default, avoiding the "installed into
> web but invisible in the desktop app" trap.

#### Option 2 — DSH 0.1.x script

```powershell
.\install.ps1                          # Windows
```
```bash
chmod +x install.sh && ./install.sh    # macOS / Linux
```

#### Option 3 — UI plugin only

```powershell
cd engineering
.\install-plugin.ps1
```

The script ① cleans up legacy permission residue, ② copies the plugin into the
profile's `node_modules`, ③ registers it in the profile's `package.json`
(`dependencies` + `dsh.profile.bundles`). **The profile's `cordis.patch.yml` is
never overwritten.**

#### Afterwards

**Restart DSH** — both the plugin host half and the host-layer config are
process-level. Then start a new session and pick **Engineering Mode**.

### Requirements

| Component | Requirement | Needed? |
|-----------|-------------|---------|
| DeepSeek Harness | 0.2.0 (desktop or `dsh web`) | **Required** |
| Python | 3.8+ / 64-bit | **Required** |
| numpy | any recent | **Required** (FEA) |
| `pywin32` | — | **Required** (drives SW / CAD) |
| SolidWorks | 2018 – 2024 | For modeling |
| AutoCAD | any recent | For DWG export / CADX validation |
| `Pillow`, `mss` | — | Optional (screenshots) |
| Gmsh + CalculiX / Code_Aster / Elmer | — | Optional (full 3D FEA) |
| `skfem` | — | Optional (better 2D accuracy) |

### Usage

```
You: Draw an 11 mm square and turn it into a CAD drawing.

AI:  [MODE SELECTED] Mode 1
     [Progress: 10%] confirming parameters…
     → generate modeling script → sw_bridge.py run
     → sw_bridge.py drawing  DSH_square.sldprt
     → sw_bridge.py dwg      DSH_square.sldprt
     → sw_bridge.py show     (screenshot for sign-off)
     [Progress: 100%] part saved / drawing generated / DWG exported
```

```
You: Design a 3-DOF robotic arm.

AI:  [MODE SELECTED] Mode 2
     Mandatory gate (command-line scripts, NOT DSH tools):
       1. workflow_gate.py init "<task>"
       2. ask the user question 0 (parameter rigor) via ask_user_question
       3. workflow_gate.py provide_context "<answer to Q0>"   → second question batch
       4. workflow_gate.py provide_context "<second batch>"   → mechanics + A/B/C + D/E
       5. workflow_gate.py select C,E
     → create exactly as many huts as select returns
     → advance waves → assembly gate → drawings
```

### Troubleshooting

**SolidWorks fails to start?**
See [`engineering/常见SW启动失败问题.md`](engineering/常见SW启动失败问题.md).
Check in order: Python bitness → `pywin32` → `sw_d.lic` → loader `netapi32.dll`
→ SW installed off the C: drive → an existing SW instance → DSH sandbox blocking.
Self-check: `python engineering\tools\sw_bridge.py doctor`

**The `workflow-gate-init` tool doesn't exist?**
Correct — **those DSH tool names do not exist in this environment.** Older docs
claimed otherwise; following them always fails. The real entry point is a
command-line script: `python engineering\tools\workflow_gate.py init "<task>"`.

**"Missing credential" from a gate?**
First confirm where state actually lands. `_store.py` is the **single source of
truth** for the state directory; priority order is `DSH_STATE_DIR` →
`DSH_ENGINEERING_ROOT` → detected engineering root → script directory. Setting it
explicitly is recommended:

```powershell
$env:DSH_ENGINEERING_ROOT = "C:\...\DSH-SW-and-CAD-main\engineering"
```

**Can't find the "engineering mode root"?**
It is **the directory containing both `tools\mode_gate.py` and
`tools\workflow_gate.py`**. Run `python engineering\tools\_root.py` to have it
printed. Note `~/.dsh/skills/*` contains **only SKILL.md, no `tools/`** — that is
not the root.

**May I put `- id: permission` into `agent.cordis.yml`?**
**Never.** `@deepseek-ai/dsh-permission-presets` is a host-layer, process-level
service; declaring it again inside a preset means registering a duplicate service
name and the loader rejects it outright — the fallout is that **new sessions,
preset switching and model selection all break at once**.

---

## Français

### Présentation

**Mode Ingénierie** est un Agent Preset pour [DeepSeek Harness](https://github.com/deepseek-ai/dsh).
Une fois installé, l'IA cesse d'être un assistant généraliste pour devenir un
**ingénieur en conception mécanique** :

- Sa **mission unique** est de produire un plan d'usinage valide ;
- Elle **pilote réellement SolidWorks** (via Python `win32com`, SW 2018–2024) ;
- Elle **exécute son propre calcul FEA** (Python pur + numpy, sans logiciel externe) ;
- Elle **décide elle-même si la livraison est autorisée** — si les trois lignes de
  défense échouent, elle ne livre pas.

### Fonctionnalités clés

| Fonctionnalité | Description |
|----------------|-------------|
| 🎯 **Piloté par objectif** | Tout sert « terminer le plan » ; suivi via le système Goal |
| 📐 **Flux en trois phases** | « Analyser → Concevoir → Vérifier » imposé, aucune étape sautée |
| 🔒 **Verrous au niveau du code** | Les verrous sont des **scripts Python**, pas des suggestions ; tout contournement laisse une trace |
| 🛡️ **Trois lignes de défense** | Matériau / Physique / Domaine — vérifiées par salle *et* par tâche |
| 🧩 **Multi-salles** | Les grands assemblages sont découpés en « salles », traitées par des sous-agents série ou parallèle |
| 🔬 **Physique dans la boucle** | Générer → FEA → fatigue → corriger → régénérer |
| 📏 **Fatigue et durée de vie** | Basquin S-N + coefficients de Marin + dommage cumulé de Miner (30 ans par défaut) |
| 🖥️ **Espace de travail à trois colonnes** | Conversation principale + liste de canaux + espace de travail du sous-agent |
| ❓ **Pont de question bloquant** | Les sous-agents ne peuvent pas interroger l'utilisateur — le plugin route la question vers la conversation principale et suspend réellement |
| 🧰 **7 compétences** | Sélection de mode, orchestration d'assemblage, CAD, conception SW, pont, SW→CAD, physique dans la boucle |
| 🚫 **Ne devine jamais** | Matériaux, tolérances et types d'ajustement sont toujours confirmés |

### Flux de conception

```
Phase 1 — Exigences et planification
  ├── Sélection du mode (entrée obligatoire · mode-selection)
  ├── Exigences → faisabilité dimensionnelle → faisabilité opérationnelle
  ├── Plan (todo_write)
  └── Créer l'objectif Goal

Phase 2 — Conception détaillée
  ├── Esquisse (entièrement définie) → modélisation → congés/chanfreins/trous
  ├── Validation de la pièce (propriétés de masse / interférences / contraintes)
  ├── Assemblage en sous-ensembles
  └── Résultats intermédiaires soumis à validation

Phase 3 — Assemblage et vérification
  ├── Assemblage final → contrôle d'interférences → analyse cinématique
  ├── Vérification finale (dimensions / tolérances / ajustements)
  ├── Plans techniques (trois vues / coupes / détails)
  ├── Export SLDDRW / DWG / PDF / DXF
  └── Marquer l'objectif comme terminé
```

### Les trois lignes de défense

C'est ce qui distingue ce projet d'un « assistant CAO » ordinaire :
**la vérification qualité est un verrou dur, pas une suggestion.**

| # | Ligne de défense | Justificatif | Échec si | Appliqué à |
|---|------------------|--------------|----------|-----------|
| ① | **Matériau** | `<pièce>.material.json` à côté de chaque `.sldprt` | matériau absent / densité = 1000 (eau) / substitution silencieuse entre familles | `sw_bridge.py run`, à l'écriture |
| ② | **Physique** | `reports/<salle>.physics.json` | agrégation des verrous FEA ; `overall == FAIL` | `room-end` / `confirm-assembly` / `select` final |
| ③ | **Domaine** | `reports/<salle>.domain.json` | selon `rules/<domain>_rules.json` ; toute violation CRITICAL | idem ② |

**Quatre points d'application** — l'IA ne peut pas passer outre :

```
sw_bridge.py run                  → vérifie ① (matériau)
mode_gate.py room-end <salle>     → vérifie ①②③ pour cette salle
workflow_gate.py confirm-assembly → vérifie ①②③ pour toutes les salles avant assemblage
workflow_gate.py select (final)   → vérifie ①②③ pour toute la tâche avant de conclure
```

**Seul contournement légal** : la variable d'environnement `DSH_DEFENSE_BYPASS=1`
ou l'option `--force` d'une commande — et il est **toujours enregistré** dans
`reports/defense_bypass.json`. Rien ne passe jamais en silence.

**Les justificatifs sont signés.** Les justificatifs physique et domaine portent
une **signature HMAC** émise par l'hôte DSH. Si l'hôte est indisponible, le verrou
est **fail-closed** (non vérifiable = non validé) : modifier le JSON à la main
pour simuler une réussite ne fonctionne pas.

### Architecture multi-salles (Mode 2)

Un grand assemblage (par exemple un bras robotisé multi-axes) est découpé en
**salles**, chacune confiée à un sous-agent :

```
Conversation principale (grande salle)
   │
   ├── Vague 1 · explosion indépendante (parallèle)
   │     ├── salle pièces structurelles
   │     ├── salle transmission
   │     └── salle carter et châssis
   │
   ├── Vague 2 · convergence (une salle)
   │     └── assemblage et vérification     ← verrou confirm-assembly
   │
   └── Vague 3 · sortie des plans (une salle)
         └── salle plans techniques
```

**Contraintes fortes :**

- **SolidWorks est mono-instance** → `mode_gate.py` impose une **file FIFO au
  niveau du processus** avec rotation équitable (une salle garde SW au maximum
  3 commandes consécutives ou 180 secondes, puis doit céder) ;
- **L'appartenance des pièces est contrôlée** : `check-part` vérifie à quelle
  salle appartient une pièce ; toute production hors périmètre est rejetée et
  journalisée (Bug-38) ;
- **Interdiction de doubler la file** : `send_message` interromprait une salle en
  pleine modélisation ; la progression se lit uniquement via des commandes
  latérales en lecture seule (`room-status` / `room-report-read`) ;
- **Aucune usurpation** : les salles ne peuvent pas appeler les commandes
  d'orchestration (`mode_gate.py`, `workflow_gate.py`, `subagent_fork`, …) —
  celles-ci appartiennent à la conversation principale.

### Améliorations d'interface (plugin `dsh-engineering-ui`)

| Fonctionnalité | Description |
|----------------|-------------|
| **Espace à trois colonnes** | En présence de sous-agents, la conversation principale est réduite à 3/4 et le 1/4 droit affiche la sortie en direct, les appels d'outils et l'état du sous-agent sélectionné ; une liste verticale de canaux permet de basculer. **Entièrement masqué sans sous-agent** |
| **Pont de question bloquant** | DSH interdit aux sous-agents d'interroger l'utilisateur (`userQuestions.ask()` vérifie `agents.roots()` ; un sous-agent lève toujours `DELEGATED_CALLER`). Le plugin route la question vers la conversation principale, y suspend, et renvoie la réponse à l'auteur sous forme de **tool_result** |
| **Garde de fin de tour** | Uniquement pour `engineering` : empêche la conversation de se terminer avant que la tâche n'atteigne un état terminal au niveau du code |
| **Section de réglages** | Trois couches (Aperçu / Flux et verrous / Interface et affichage). La troisième contient le commutateur **« utiliser l'affichage de sous-agents propre au mode Ingénierie »** : activé, le plugin reprend le panneau droit *et* l'entrée officielle `subagentchat` ; désactivé, tout revient à DSH d'origine |

> Le plugin **n'enregistre aucun outil de modèle** et ne modifie ni
> l'ordonnancement des sous-agents, ni le modèle, ni les permissions, ni le contexte.

### Installation

#### Option 1 — script DSH 0.2.0 (recommandé)

```powershell
cd <dépôt>
.\install-0.2.0.ps1                    # installe dans tous les profils de ~/.dsh/profiles
.\install-0.2.0.ps1 -Profile desktop   # ou cibler des profils précis
```

> ⚠️ **Ce qui change en 0.2.0** : DSH 0.2.0 **ne découvre plus** les presets dans
> `~/.dsh/.agent-presets/`. Les presets sont désormais des entrées ordinaires
> déclarées par `dsh.profile.bundles` dans `package.json`. De plus, **le shell
> Electron démarre le profil réservé `desktop`, pas `web`** (`dsh web` démarre
> `web`) — le script installe donc dans **tous** les profils par défaut.

#### Option 2 — script DSH 0.1.x

```powershell
.\install.ps1                          # Windows
```
```bash
chmod +x install.sh && ./install.sh    # macOS / Linux
```

#### Option 3 — plugin d'interface seul

```powershell
cd engineering
.\install-plugin.ps1
```

Le script ① nettoie les résidus de permissions hérités, ② copie le plugin dans le
`node_modules` du profil, ③ l'enregistre dans le `package.json` du profil
(`dependencies` + `dsh.profile.bundles`). **Le `cordis.patch.yml` du profil n'est
jamais écrasé.**

#### Ensuite

**Redémarrez DSH** — la moitié hôte du plugin et la configuration de la couche
hôte sont toutes deux au niveau du processus. Puis ouvrez une nouvelle session et
choisissez **Mode Ingénierie**.

### Prérequis

| Composant | Exigence | Requis ? |
|-----------|----------|----------|
| DeepSeek Harness | 0.2.0 (bureau ou `dsh web`) | **Requis** |
| Python | 3.8+ / 64 bits | **Requis** |
| numpy | toute version récente | **Requis** (FEA) |
| `pywin32` | — | **Requis** (pilote SW / CAO) |
| SolidWorks | 2018 – 2024 | Pour la modélisation |
| AutoCAD | toute version récente | Pour l'export DWG / la validation CADX |
| `Pillow`, `mss` | — | Optionnel (captures) |
| Gmsh + CalculiX / Code_Aster / Elmer | — | Optionnel (FEA 3D complète) |
| `skfem` | — | Optionnel (meilleure précision 2D) |

### Utilisation

```
Vous : Dessine un carré de 11 mm puis convertis-le en plan CAO.

IA  : [MODE SELECTED] Mode 1
      [Progression : 10 %] confirmation des paramètres…
      → générer le script → sw_bridge.py run
      → sw_bridge.py drawing  DSH_carre.sldprt
      → sw_bridge.py dwg      DSH_carre.sldprt
      → sw_bridge.py show     (capture pour validation)
      [Progression : 100 %] pièce enregistrée / plan généré / DWG exporté
```

```
Vous : Conçois un bras robotisé à 3 degrés de liberté.

IA  : [MODE SELECTED] Mode 2
      Verrou obligatoire (scripts en ligne de commande, PAS des outils DSH) :
       1. workflow_gate.py init "<tâche>"
       2. poser la question 0 (rigueur des paramètres) via ask_user_question
       3. workflow_gate.py provide_context "<réponse Q0>"  → deuxième série
       4. workflow_gate.py provide_context "<2e série>"     → mécanique + A/B/C + D/E
       5. workflow_gate.py select C,E
      → créer exactement autant de salles que select en renvoie
      → avancer par vagues → verrou d'assemblage → plans
```

### Dépannage

**SolidWorks ne démarre pas ?**
Voir [`engineering/常见SW启动失败问题.md`](engineering/常见SW启动失败问题.md).
Vérifier dans l'ordre : architecture de Python → `pywin32` → `sw_d.lic` → chargeur
`netapi32.dll` → SW installé hors du disque C: → instance SW déjà lancée → bac à
sable DSH. Auto-contrôle : `python engineering\tools\sw_bridge.py doctor`

**L'outil `workflow-gate-init` n'existe pas ?**
Exact — **ces noms d'outils DSH n'existent pas dans cet environnement.** D'anciens
documents affirmaient le contraire ; les suivre échoue systématiquement. Le vrai
point d'entrée est un script : `python engineering\tools\workflow_gate.py init "<tâche>"`.

**Un verrou signale « justificatif manquant » ?**
Vérifiez d'abord où l'état est réellement écrit. `_store.py` est la **source unique
de vérité** du répertoire d'état ; ordre de priorité : `DSH_STATE_DIR` →
`DSH_ENGINEERING_ROOT` → racine détectée → répertoire du script. Il est conseillé
de la définir explicitement :

```powershell
$env:DSH_ENGINEERING_ROOT = "C:\...\DSH-SW-and-CAD-main\engineering"
```

**Impossible de trouver la « racine du mode Ingénierie » ?**
C'est **le répertoire contenant à la fois `tools\mode_gate.py` et
`tools\workflow_gate.py`**. Lancez `python engineering\tools\_root.py` pour
l'afficher. Attention : `~/.dsh/skills/*` ne contient **que SKILL.md, pas de
`tools/`** — ce n'est pas la racine.

**Puis-je mettre `- id: permission` dans `agent.cordis.yml` ?**
**Jamais.** `@deepseek-ai/dsh-permission-presets` est un service de couche hôte au
niveau du processus ; le redéclarer dans un preset revient à enregistrer un nom de
service en double, et le loader le refuse — conséquence : **création de session,
changement de preset et sélection de modèle cassent tous en même temps**.

---

## Español

### Qué es esto

**Modo Ingeniería** es un Agent Preset para [DeepSeek Harness](https://github.com/deepseek-ai/dsh).
Una vez instalado, la IA deja de ser un asistente genérico y pasa a ser un
**ingeniero de diseño mecánico**:

- Su **única misión** es entregar un plano de fabricación válido;
- **Realmente controla SolidWorks** (mediante Python `win32com`, SW 2018–2024);
- **Ejecuta su propio cálculo FEA** (Python puro + numpy, sin software externo);
- **Decide por sí misma si se puede entregar**: si fallan las tres líneas de
  defensa, no libera el trabajo.

### Características principales

| Característica | Descripción |
|----------------|-------------|
| 🎯 **Guiado por objetivos** | Todo sirve a «completar el plano»; seguimiento con el sistema Goal |
| 📐 **Flujo en tres fases** | «Analizar → Diseñar → Verificar» obligatorio, sin saltarse pasos |
| 🔒 **Barreras a nivel de código** | Las barreras son **scripts Python**, no sugerencias; todo rodeo deja rastro |
| 🛡️ **Tres líneas de defensa** | Material / Física / Dominio — verificadas por sala *y* por tarea |
| 🧩 **Multi-sala** | Los conjuntos grandes se dividen en «salas», atendidas por subagentes en serie o en paralelo |
| 🔬 **Física en el bucle** | Generar → FEA → fatiga → corregir → regenerar |
| 📏 **Fatiga y vida útil** | Basquin S-N + factores de Marin + daño acumulado de Miner (30 años por defecto) |
| 🖥️ **Área de tres columnas** | Conversación principal + lista de canales + área de trabajo del subagente |
| ❓ **Puente de preguntas bloqueante** | Los subagentes no pueden preguntar al usuario: el plugin enruta la pregunta a la conversación principal y la suspende de verdad |
| 🧰 **7 habilidades** | Selección de modo, orquestación de ensamblaje, CAD, diseño SW, puente, SW→CAD, física en el bucle |
| 🚫 **Nunca adivina** | Materiales, tolerancias y tipos de ajuste siempre se confirman |

### Flujo de diseño

```
Fase 1 — Requisitos y planificación
  ├── Selección de modo (entrada obligatoria · mode-selection)
  ├── Requisitos → viabilidad dimensional → viabilidad operativa
  ├── Plan (todo_write)
  └── Crear el objetivo Goal

Fase 2 — Diseño detallado
  ├── Croquis (totalmente definido) → modelado → redondeos/chaflanes/taladros
  ├── Validación de la pieza (propiedades másicas / interferencias / tensiones)
  ├── Ensamblaje en subconjuntos
  └── Resultados intermedios para confirmación

Fase 3 — Ensamblaje y verificación
  ├── Ensamblaje final → comprobación de interferencias → análisis cinemático
  ├── Verificación final (dimensiones / tolerancias / ajustes)
  ├── Planos técnicos (tres vistas / cortes / detalles)
  ├── Exportar SLDDRW / DWG / PDF / DXF
  └── Marcar el objetivo como completado
```

### Las tres líneas de defensa

Esto es lo que distingue al proyecto de un «asistente CAD» corriente:
**la verificación de calidad es una barrera dura, no una sugerencia.**

| # | Línea de defensa | Credencial | Falla si | Se aplica en |
|---|------------------|-----------|----------|--------------|
| ① | **Material** | `<pieza>.material.json` junto a cada `.sldprt` | material ausente / densidad = 1000 (agua) / sustitución silenciosa entre familias | `sw_bridge.py run`, al escribir |
| ② | **Física** | `reports/<sala>.physics.json` | agregación de barreras FEA; `overall == FAIL` | `room-end` / `confirm-assembly` / `select` final |
| ③ | **Dominio** | `reports/<sala>.domain.json` | según `rules/<domain>_rules.json`; cualquier violación CRITICAL | igual que ② |

**Cuatro puntos de aplicación** — la IA no puede saltárselos:

```
sw_bridge.py run                  → verifica ① (material)
mode_gate.py room-end <sala>      → verifica ①②③ de esa sala
workflow_gate.py confirm-assembly → verifica ①②③ de todas las salas antes del ensamblaje
workflow_gate.py select (final)   → verifica ①②③ de toda la tarea antes de concluir
```

**Único rodeo legal**: la variable de entorno `DSH_DEFENSE_BYPASS=1` o el `--force`
de un comando — y **siempre queda registrado** en `reports/defense_bypass.json`.
Nunca se deja pasar nada en silencio.

**Las credenciales están firmadas.** Las credenciales de física y dominio llevan una
**firma HMAC** emitida por el host DSH. Si el host no está disponible, la barrera es
**fail-closed** (no verificable = no superada): editar el JSON a mano para simular
un aprobado no funciona.

### Arquitectura multi-sala (Modo 2)

Un conjunto grande (por ejemplo un brazo robótico multiarticulado) se divide en
**salas**, cada una a cargo de un subagente:

```
Conversación principal (sala grande)
   │
   ├── Oleada 1 · explosión independiente (paralelo)
   │     ├── sala de piezas estructurales
   │     ├── sala de transmisión
   │     └── sala de carcasa y bastidor
   │
   ├── Oleada 2 · convergencia (una sala)
   │     └── ensamblaje y verificación     ← barrera confirm-assembly
   │
   └── Oleada 3 · salida de planos (una sala)
         └── sala de planos técnicos
```

**Restricciones duras:**

- **SolidWorks es monoinstancia** → `mode_gate.py` impone una **cola FIFO a nivel de
  proceso** con rotación justa (una sala retiene SW como máximo 3 comandos
  consecutivos o 180 segundos, luego debe ceder);
- **La pertenencia de piezas está controlada**: `check-part` verifica a qué sala
  pertenece una pieza; la producción fuera de alcance se rechaza y se registra (Bug-38);
- **Prohibido colarse en la cola**: `send_message` interrumpiría una sala en pleno
  modelado, así que el progreso se lee solo con comandos laterales de solo lectura
  (`room-status` / `room-report-read`);
- **Sin usurpación**: las salas no pueden llamar a los comandos de orquestación
  (`mode_gate.py`, `workflow_gate.py`, `subagent_fork`, …) — esos pertenecen
  únicamente a la conversación principal.

### Mejoras de interfaz (plugin `dsh-engineering-ui`)

| Característica | Descripción |
|----------------|-------------|
| **Área de tres columnas** | Con subagentes presentes, la conversación principal se reduce a 3/4 y el 1/4 derecho muestra la salida en vivo, las llamadas de herramientas y el estado del subagente seleccionado; una lista vertical de canales permite cambiar. **Se oculta por completo sin subagentes** |
| **Puente de preguntas bloqueante** | DSH prohíbe que los subagentes pregunten al usuario (`userQuestions.ask()` comprueba `agents.roots()`; un subagente siempre lanza `DELEGATED_CALLER`). El plugin enruta la pregunta a la conversación principal, se suspende allí y devuelve la respuesta al preguntante como **tool_result** |
| **Guarda de fin de turno** | Solo para `engineering`: impide que la conversación termine antes de que la tarea alcance un estado terminal a nivel de código |
| **Sección de ajustes** | Tres capas (Resumen / Flujo y barreras / Interfaz y visualización). La tercera contiene el interruptor **«usar la vista de subagentes propia del Modo Ingeniería»**: activado, el plugin toma el panel derecho *y* la entrada oficial `subagentchat`; desactivado, todo vuelve al DSH original |

> El plugin **no registra ninguna herramienta de modelo** y no altera la
> planificación de subagentes, el modelo, los permisos ni el contexto.

### Instalación

#### Opción 1 — script DSH 0.2.0 (recomendado)

```powershell
cd <repositorio>
.\install-0.2.0.ps1                    # instala en todos los perfiles de ~/.dsh/profiles
.\install-0.2.0.ps1 -Profile desktop   # o apuntar a perfiles concretos
```

> ⚠️ **Qué cambia en 0.2.0**: DSH 0.2.0 **ya no descubre** presets en
> `~/.dsh/.agent-presets/`. Ahora los presets son entradas normales declaradas por
> `dsh.profile.bundles` en `package.json`. Además, **el shell Electron arranca el
> perfil reservado `desktop`, no `web`** (`dsh web` arranca `web`) — por eso el
> script instala en **todos** los perfiles por defecto.

#### Opción 2 — script DSH 0.1.x

```powershell
.\install.ps1                          # Windows
```
```bash
chmod +x install.sh && ./install.sh    # macOS / Linux
```

#### Opción 3 — solo el plugin de interfaz

```powershell
cd engineering
.\install-plugin.ps1
```

El script ① limpia residuos de permisos heredados, ② copia el plugin al
`node_modules` del perfil, ③ lo registra en el `package.json` del perfil
(`dependencies` + `dsh.profile.bundles`). **El `cordis.patch.yml` del perfil nunca
se sobrescribe.**

#### Después

**Reinicia DSH** — tanto la mitad host del plugin como la configuración de la capa
host son a nivel de proceso. Luego abre una sesión nueva y elige **Modo Ingeniería**.

### Requisitos

| Componente | Requisito | ¿Necesario? |
|------------|-----------|-------------|
| DeepSeek Harness | 0.2.0 (escritorio o `dsh web`) | **Necesario** |
| Python | 3.8+ / 64 bits | **Necesario** |
| numpy | cualquiera reciente | **Necesario** (FEA) |
| `pywin32` | — | **Necesario** (controla SW / CAD) |
| SolidWorks | 2018 – 2024 | Para modelar |
| AutoCAD | cualquiera reciente | Para exportar DWG / validar CADX |
| `Pillow`, `mss` | — | Opcional (capturas) |
| Gmsh + CalculiX / Code_Aster / Elmer | — | Opcional (FEA 3D completa) |
| `skfem` | — | Opcional (mejor precisión 2D) |

### Uso

```
Tú : Dibuja un cuadrado de 11 mm y conviértelo en un plano CAD.

IA : [MODE SELECTED] Mode 1
     [Progreso: 10%] confirmando parámetros…
     → generar script → sw_bridge.py run
     → sw_bridge.py drawing  DSH_cuadrado.sldprt
     → sw_bridge.py dwg      DSH_cuadrado.sldprt
     → sw_bridge.py show     (captura para validación)
     [Progreso: 100%] pieza guardada / plano generado / DWG exportado
```

```
Tú : Diseña un brazo robótico de 3 grados de libertad.

IA : [MODE SELECTED] Mode 2
     Barrera obligatoria (scripts de línea de comandos, NO herramientas DSH):
       1. workflow_gate.py init "<tarea>"
       2. plantear la pregunta 0 (rigor de parámetros) con ask_user_question
       3. workflow_gate.py provide_context "<respuesta P0>"  → segunda tanda
       4. workflow_gate.py provide_context "<segunda tanda>" → mecánica + A/B/C + D/E
       5. workflow_gate.py select C,E
     → crear exactamente tantas salas como devuelva select
     → avanzar por oleadas → barrera de ensamblaje → planos
```

### Solución de problemas

**¿SolidWorks no arranca?**
Consulta [`engineering/常见SW启动失败问题.md`](engineering/常见SW启动失败问题.md).
Comprueba en orden: bits de Python → `pywin32` → `sw_d.lic` → cargador
`netapi32.dll` → SW instalado fuera del disco C: → instancia de SW ya en ejecución
→ sandbox de DSH. Autocomprobación: `python engineering\tools\sw_bridge.py doctor`

**¿No existe la herramienta `workflow-gate-init`?**
Correcto — **esos nombres de herramientas DSH no existen en este entorno.**
Documentación antigua afirmaba lo contrario; seguirla siempre falla. El punto de
entrada real es un script: `python engineering\tools\workflow_gate.py init "<tarea>"`.

**¿Una barrera dice «falta credencial»?**
Confirma primero dónde se escribe realmente el estado. `_store.py` es la **fuente
única de verdad** del directorio de estado; orden de prioridad: `DSH_STATE_DIR` →
`DSH_ENGINEERING_ROOT` → raíz detectada → directorio del script. Se recomienda
definirla explícitamente:

```powershell
$env:DSH_ENGINEERING_ROOT = "C:\...\DSH-SW-and-CAD-main\engineering"
```

**¿No encuentro la «raíz del Modo Ingeniería»?**
Es **el directorio que contiene a la vez `tools\mode_gate.py` y
`tools\workflow_gate.py`**. Ejecuta `python engineering\tools\_root.py` para que la
imprima. Ojo: `~/.dsh/skills/*` contiene **solo SKILL.md, sin `tools/`** — esa no es
la raíz.

**¿Puedo poner `- id: permission` en `agent.cordis.yml`?**
**Nunca.** `@deepseek-ai/dsh-permission-presets` es un servicio de capa host a nivel
de proceso; volver a declararlo dentro de un preset equivale a registrar un nombre
de servicio duplicado, y el loader lo rechaza — la consecuencia es que **crear
sesiones, cambiar de preset y elegir modelo se rompen a la vez**.

---

## Русский

### Что это такое

**Инженерный режим** — это Agent Preset для [DeepSeek Harness](https://github.com/deepseek-ai/dsh).
После установки ИИ перестаёт быть универсальным помощником и становится
**инженером-конструктором**:

- Его **единственная задача** — выпустить корректный рабочий чертёж;
- Он **действительно управляет SolidWorks** (через Python `win32com`, SW 2018–2024);
- Он **сам выполняет расчёт МКЭ** (чистый Python + numpy, без внешнего ПО);
- Он **сам решает, можно ли сдавать работу**: если три линии защиты не пройдены,
  работа не выпускается.

### Ключевые возможности

| Возможность | Описание |
|-------------|----------|
| 🎯 **Управление целью** | Всё служит задаче «выполнить чертёж»; отслеживание через систему Goal |
| 📐 **Три этапа** | Обязательный цикл «Анализ → Конструирование → Проверка», без пропуска шагов |
| 🔒 **Барьеры на уровне кода** | Барьеры — это **скрипты Python**, а не подсказки; любой обход оставляет след |
| 🛡️ **Три линии защиты** | Материал / Физика / Предметная область — проверка по комнате *и* по задаче |
| 🧩 **Мультикомнаты** | Крупные сборки делятся на «комнаты», обрабатываемые субагентами последовательно или параллельно |
| 🔬 **Физика в контуре** | Генерация → МКЭ → усталость → коррекция → повторная генерация |
| 📏 **Усталость и ресурс** | Basquin S-N + поправки Марина + накопление повреждений по Майнеру (по умолчанию 30 лет) |
| 🖥️ **Три колонки** | Основной диалог + список каналов + рабочая область субагента |
| ❓ **Блокирующий мост вопросов** | Субагенты не могут спрашивать пользователя — плагин направляет вопрос в основной диалог и реально приостанавливает работу |
| 🧰 **7 навыков** | Выбор режима, оркестрация сборки, CAD, проектирование в SW, мост, SW→CAD, физика в контуре |
| 🚫 **Никогда не угадывает** | Материалы, допуски и типы посадок всегда уточняются у пользователя |

### Процесс проектирования

```
Этап 1 — Требования и планирование
  ├── Выбор режима (обязательный вход · mode-selection)
  ├── Требования → проверка размеров → проверка работоспособности
  ├── План (todo_write)
  └── Создание цели Goal

Этап 2 — Детальное проектирование
  ├── Эскиз (полностью определённый) → моделирование → скругления/фаски/отверстия
  ├── Проверка детали (массовые свойства / пересечения / напряжения)
  ├── Сборка в подузлы
  └── Промежуточные результаты на подтверждение

Этап 3 — Сборка и проверка
  ├── Итоговая сборка → проверка пересечений → кинематический анализ
  ├── Итоговая проверка (размеры / допуски / посадки)
  ├── Рабочие чертежи (три вида / разрезы / выносные элементы)
  ├── Экспорт SLDDRW / DWG / PDF / DXF
  └── Отметить цель как выполненную
```

### Три линии защиты

Именно это отличает проект от обычного «CAD-помощника»:
**проверка качества — жёсткий барьер, а не рекомендация.**

| # | Линия защиты | Подтверждение | Провал, если | Применяется |
|---|--------------|---------------|--------------|-------------|
| ① | **Материал** | `<деталь>.material.json` рядом с каждым `.sldprt` | материал отсутствует / плотность = 1000 (вода) / молчаливая подмена между семействами | `sw_bridge.py run`, при записи |
| ② | **Физика** | `reports/<комната>.physics.json` | агрегация барьеров МКЭ; `overall == FAIL` | `room-end` / `confirm-assembly` / финальный `select` |
| ③ | **Предметная область** | `reports/<комната>.domain.json` | по `rules/<domain>_rules.json`; любое нарушение CRITICAL | так же, как ② |

**Четыре точки принудительной проверки** — обойти нельзя:

```
sw_bridge.py run                  → проверка ① (материал)
mode_gate.py room-end <комната>   → проверка ①②③ для этой комнаты
workflow_gate.py confirm-assembly → проверка ①②③ всех комнат до сборки
workflow_gate.py select (финал)   → проверка ①②③ всей задачи до объявления готовности
```

**Единственный законный обход** — переменная окружения `DSH_DEFENSE_BYPASS=1`
или ключ `--force` — и он **всегда фиксируется** в `reports/defense_bypass.json`.
Ничего не пропускается молча.

**Подтверждения подписаны.** Подтверждения физики и предметной области несут
**подпись HMAC**, выдаваемую хостом DSH. Если хост недоступен, барьер работает
**fail-closed** (непроверяемое = не пройдено), поэтому правка JSON вручную для
имитации успеха не работает.

### Архитектура «мультикомнат» (Режим 2)

Крупная сборка (например, многозвенный манипулятор) делится на **комнаты**,
каждой занимается один субагент:

```
Основной диалог (большая комната)
   │
   ├── Волна 1 · независимый запуск (параллельно)
   │     ├── комната структурных деталей
   │     ├── комната передачи
   │     └── комната корпуса и рамы
   │
   ├── Волна 2 · сведение (одна комната)
   │     └── сборка и проверка            ← барьер confirm-assembly
   │
   └── Волна 3 · выпуск чертежей (одна комната)
         └── комната рабочих чертежей
```

**Жёсткие ограничения:**

- **SolidWorks — одноэкземплярное приложение** → `mode_gate.py` обеспечивает
  **FIFO-очередь на уровне процесса** со справедливым чередованием (комната
  удерживает SW максимум 3 команды подряд или 180 секунд, затем обязана уступить);
- **Принадлежность деталей контролируется**: `check-part` определяет, какой
  комнате принадлежит деталь; вывод за пределами области отклоняется и
  логируется (Bug-38);
- **Запрещено влезать в очередь**: `send_message` прервал бы комнату посреди
  моделирования, поэтому прогресс читается только боковыми командами только для
  чтения (`room-status` / `room-report-read`);
- **Никакой узурпации**: комнаты не могут вызывать команды оркестрации
  (`mode_gate.py`, `workflow_gate.py`, `subagent_fork`, …) — они принадлежат
  только основному диалогу.

### Улучшения интерфейса (плагин `dsh-engineering-ui`)

| Возможность | Описание |
|-------------|----------|
| **Три колонки** | При наличии субагентов основной диалог сжимается до 3/4, а правые 1/4 показывают вывод выбранного субагента в реальном времени, вызовы инструментов и состояние; вертикальный список каналов позволяет переключаться. **Полностью скрывается без субагентов** |
| **Блокирующий мост вопросов** | DSH запрещает субагентам спрашивать пользователя (`userQuestions.ask()` проверяет `agents.roots()`; субагент всегда выбрасывает `DELEGATED_CALLER`). Плагин направляет вопрос в основной диалог, приостанавливается там и возвращает ответ спрашивающему как **tool_result** |
| **Страж завершения хода** | Только для `engineering`: не даёт диалогу завершиться, пока задача не достигнет терминального состояния на уровне кода |
| **Раздел настроек** | Три уровня (Обзор / Процесс и барьеры / Интерфейс и отображение). На третьем — переключатель **«использовать собственную панель субагентов Инженерного режима»**: включён — плагин забирает правую панель *и* официальный вход `subagentchat`; выключен — всё возвращается к штатному DSH |

> Плагин **не регистрирует инструменты модели** и не меняет планирование
> субагентов, модель, права доступа или контекст.

### Установка

#### Вариант 1 — скрипт для DSH 0.2.0 (рекомендуется)

```powershell
cd <репозиторий>
.\install-0.2.0.ps1                    # установка во все профили в ~/.dsh/profiles
.\install-0.2.0.ps1 -Profile desktop   # или в конкретные профили
```

> ⚠️ **Что изменилось в 0.2.0**: DSH 0.2.0 **больше не обнаруживает** пресеты в
> `~/.dsh/.agent-presets/`. Теперь пресеты — обычные записи, объявляемые
> `dsh.profile.bundles` в `package.json`. Кроме того, **оболочка Electron
> запускает зарезервированный профиль `desktop`, а не `web`** (`dsh web`
> запускает `web`) — поэтому скрипт по умолчанию ставит во **все** профили.

#### Вариант 2 — скрипт для DSH 0.1.x

```powershell
.\install.ps1                          # Windows
```
```bash
chmod +x install.sh && ./install.sh    # macOS / Linux
```

#### Вариант 3 — только UI-плагин

```powershell
cd engineering
.\install-plugin.ps1
```

Скрипт ① очищает остатки прежних прав, ② копирует плагин в `node_modules` профиля,
③ регистрирует его в `package.json` профиля (`dependencies` + `dsh.profile.bundles`).
**`cordis.patch.yml` профиля никогда не перезаписывается.**

#### После установки

**Перезапустите DSH** — и host-половина плагина, и конфигурация хостового слоя
работают на уровне процесса. Затем откройте новую сессию и выберите
**Инженерный режим**.

### Требования

| Компонент | Требование | Обязательно? |
|-----------|------------|--------------|
| DeepSeek Harness | 0.2.0 (настольная версия или `dsh web`) | **Обязательно** |
| Python | 3.8+ / 64 бита | **Обязательно** |
| numpy | любая свежая версия | **Обязательно** (МКЭ) |
| `pywin32` | — | **Обязательно** (управление SW / CAD) |
| SolidWorks | 2018 – 2024 | Для моделирования |
| AutoCAD | любая свежая версия | Для экспорта DWG / проверки CADX |
| `Pillow`, `mss` | — | Опционально (снимки экрана) |
| Gmsh + CalculiX / Code_Aster / Elmer | — | Опционально (полный 3D МКЭ) |
| `skfem` | — | Опционально (точнее 2D) |

### Использование

```
Вы: Нарисуй квадрат 11 мм и переведи его в CAD-чертёж.

ИИ: [MODE SELECTED] Mode 1
    [Прогресс: 10%] уточняю параметры…
    → сгенерировать скрипт → sw_bridge.py run
    → sw_bridge.py drawing  DSH_square.sldprt
    → sw_bridge.py dwg      DSH_square.sldprt
    → sw_bridge.py show     (снимок для приёмки)
    [Прогресс: 100%] деталь сохранена / чертёж создан / DWG экспортирован
```

```
Вы: Спроектируй манипулятор с тремя степенями свободы.

ИИ: [MODE SELECTED] Mode 2
    Обязательный барьер (скрипты командной строки, НЕ инструменты DSH):
      1. workflow_gate.py init "<задача>"
      2. задать вопрос 0 (строгость параметров) через ask_user_question
      3. workflow_gate.py provide_context "<ответ на В0>"  → второй блок вопросов
      4. workflow_gate.py provide_context "<второй блок>"  → механика + A/B/C + D/E
      5. workflow_gate.py select C,E
    → создать ровно столько комнат, сколько вернул select
    → продвижение по волнам → барьер сборки → чертежи
```

### Решение проблем

**SolidWorks не запускается?**
См. [`engineering/常见SW启动失败问题.md`](engineering/常见SW启动失败问题.md).
Проверяйте по порядку: разрядность Python → `pywin32` → `sw_d.lic` → загрузчик
`netapi32.dll` → SW установлен не на диск C: → уже запущенный экземпляр SW →
песочница DSH. Самопроверка: `python engineering\tools\sw_bridge.py doctor`

**Инструмент `workflow-gate-init` не найден?**
Верно — **этих имён инструментов DSH в данной среде не существует.** Старые
документы утверждали обратное; следование им всегда приводит к ошибке. Реальная
точка входа — скрипт: `python engineering\tools\workflow_gate.py init "<задача>"`.

**Барьер сообщает «нет подтверждения»?**
Сначала выясните, куда реально пишется состояние. `_store.py` — **единственный
источник истины** для каталога состояния; порядок приоритета: `DSH_STATE_DIR` →
`DSH_ENGINEERING_ROOT` → обнаруженный корень → каталог скрипта. Рекомендуется
задать явно:

```powershell
$env:DSH_ENGINEERING_ROOT = "C:\...\DSH-SW-and-CAD-main\engineering"
```

**Не могу найти «корень Инженерного режима»?**
Это **каталог, содержащий одновременно `tools\mode_gate.py` и
`tools\workflow_gate.py`**. Запустите `python engineering\tools\_root.py`, чтобы
он его вывел. Учтите: в `~/.dsh/skills/*` есть **только SKILL.md, без `tools/`** —
это не корень.

**Можно ли добавить `- id: permission` в `agent.cordis.yml`?**
**Никогда.** `@deepseek-ai/dsh-permission-presets` — это сервис хостового слоя
уровня процесса; повторное объявление внутри пресета означает регистрацию
дублирующего имени сервиса, и загрузчик это отвергает — следствие: **создание
сессий, переключение пресетов и выбор модели ломаются одновременно**.

---

## 技术附录 / Appendix

> 本附录是**语言无关的技术参考**（命令名、路径、模块名），故只保留一份，
> 不做 5 语言重复。 / This appendix is **language-neutral technical reference**
> (command names, paths, module names), kept in a single copy on purpose.

### 架构：DSH 插件的三个平面

DSH 的插件分三个平面，**放错位置会互相连坐**：

| 平面 | 位置 | 内容 | 生效方式 |
|------|------|------|----------|
| **宿主层**（进程级） | `profiles/<profile>/cordis.patch.yml` | 权限预设表、全局服务 | **必须重启 DSH** |
| **preset 层**（会话子作用域） | `.agent-presets/<id>/agent.cordis.yml` | persona、工具、技能 | 热生效 |
| **profile bundle** | `profiles/<profile>/package.json` 的 `dependencies` + `dsh.profile.bundles` | UI 插件（客户端半） | **必须重启 DSH** |

> ⛔ **绝对不要把 `- id: permission` 写进 `agent.cordis.yml`！**
> `@deepseek-ai/dsh-permission-presets` 是宿主层进程级服务。
> preset 里再声明一次 = 重复注册同名服务，loader 直接拒绝：
>
> ```
> failed to apply loader entry permission (@deepseek-ai/dsh-permission-presets):
> service "permissionPresets" has been registered
> ```
>
> 后果是**新建会话 / 切预设 / 选模型三件事一起坏**。

### 同步须知（三处副本）

改动本预设后需保持三处一致：

| 位置 | 用途 |
|------|------|
| `~/.dsh/.agent-presets/engineering/` | preset 运行位置（工具脚本、技能） |
| `~/.dsh/profiles/<profile>/node_modules/dsh-engineering-ui/` | 插件**真正加载**的位置 |
| `Desktop/DSH-SW-and-CAD-main/engineering/` | 源码仓库 |

- 插件 host 半（`index.js`）与宿主层配置（`cordis.patch.yml`）是**进程级** → **必须重启 DSH**；
- client 半（`client.js`）改动**刷新页面**即可。
- 运行时状态文件（`mode_state.json` / `workflow_state.json` / `sw_state.json`）
  是**会话现场数据**，三处**保持独立**，不要互相同步，否则会覆盖现场导致流程误判。

### 目录结构

```
DSH-SW-and-CAD-main/
├── README.md                   # 本文件
├── RUNNING_WORKFLOW.md         # 源码级完整运行流程追踪（39 KB）
├── LICENSE                     # MIT
├── install-0.2.0.ps1           # DSH 0.2.0 安装（装进所有 profile）
├── install.ps1                 # DSH 0.1.x 安装（Windows）
├── install.sh                  # DSH 0.1.x 安装（macOS / Linux）
├── patch-peer-ranges.mjs       # 依赖 peer range 修补
├── engineering/                # ★ 预设本体
│   ├── agent.cordis.yml        # persona + 工具 + 技能（不含宿主层服务！）
│   ├── preset.yml              # 显示元数据（name/description/order）
│   ├── install-plugin.ps1      # 只装 UI 插件
│   ├── README.md               # 架构与门禁细节（必读）
│   ├── 常见SW启动失败问题.md      # SW 启动排查手册
│   ├── skills/                 # 7 个技能
│   │   ├── mode-selection/         # 强制入口：模式选择 + 门禁
│   │   ├── assembly-orchestration/ # 多屋编排
│   │   ├── cad-workflow/           # AutoCAD 工作流
│   │   ├── sw-design/              # SW 设计（GB/T 制图、CADX）
│   │   ├── solidworks-bridge/      # SW 桥接
│   │   ├── sw-to-cad/              # SW → CAD 转换
│   │   └── physics-in-loop/        # 物理在环
│   ├── tools/                  # ★ Python 工具（118 个文件）
│   │   ├── _root.py                # 根目录探测
│   │   ├── _store.py               # 状态目录单一事实源
│   │   ├── workflow_gate.py        # 门禁大脑（13 子命令）
│   │   ├── mode_gate.py            # 房间状态机 + SW 锁（33 子命令）
│   │   ├── defense_gate.py         # 三大防线（3 子命令）
│   │   ├── sw_bridge.py            # SolidWorks 桥接（38 子命令）
│   │   ├── physics_bridge.py       # 物理验证 CLI（11 子命令）
│   │   ├── ac_bridge.py            # AutoCAD 桥接（库）
│   │   ├── ac_validate.py          # DXF 几何验证引擎（库）
│   │   ├── ask_user.py             # 子代理阻塞式提问 CLI
│   │   ├── choice_contract.py      # 固定两问题面（库）
│   │   ├── mode_checker.py         # 模式声明审计
│   │   ├── run_regression.py       # 回归测试总入口
│   │   ├── physics/                # 物理子系统（14 模块）
│   │   ├── examples/               # 建模样例
│   │   ├── load_cases/             # 载荷工况（含 gate_load_case.json）
│   │   ├── reports/                # 防线凭据 + 房间报告
│   │   ├── heartbeats/             # 子代理心跳
│   │   ├── mode_logs/              # 模式声明审计日志
│   │   └── test_regression_*.py    # 8 个回归测试
│   ├── plugins/
│   │   └── dsh-engineering-ui/     # 三栏 UI 插件 v1.1.0
│   │       ├── lib/index.js        # Host 半（13 条 HTTP 路由）
│   │       ├── lib/client.js       # 浏览器半
│   │       ├── lib/defense-sign.js # HMAC 凭据签名
│   │       ├── cordis.patch.yml
│   │       ├── preset-engineering.patch.yml
│   │       └── test/               # 4 个测试文件
│   ├── output/                 # 交付产物输出
│   └── DSH_*.SLDPRT / .png     # 示例零件（大臂/小臂/横梁/立柱）
├── output/                     # 运行产物（physics_runs / demo_run）
├── tools/                      # 顶层工具目录（mode_state.json 等）
├── third-party-patched/        # 打过补丁的第三方包
├── _sync_backup/               # 同步前备份
└── profiles/                   # profile 相关
```

### 三大防线 · 强制点与绕行

```
强制点（代码级，AI 走到就必须过）
  sw_bridge.py run                    → ① 材料
  mode_gate.py room-end <房间>         → ①②③（该房间）
  workflow_gate.py confirm-assembly   → ①②③（全部建模房间）
  workflow_gate.py select（收尾）      → ①②③（全任务）

唯一合法绕行
  DSH_DEFENSE_BYPASS=1  或  命令 --force
  → 必定留痕 reports/defense_bypass.json（绝不静默）
```

### 闸口唯一归属层（`GATE_OWNER`）

同一闸口 ID 曾被几何层与 FEA 层同时判定并给出**相反结论**。
`physics/simulation_report.py` 用 `GATE_OWNER` 为每个闸口声明**唯一归属层**，
聚合 passed / failed 时**只采信归属层**：

| 闸口 ID | 归属层 |
|---------|--------|
| `DESIGN_SPACE` / `CONNECTIVITY` / `WALL_THICKNESS` | `geometry` |
| `SAFETY_FACTOR` / `MAX_DISPLACEMENT` / `FATIGUE` / `MESH_QUALITY` | `fea` |

> 规则：**一个闸口 ID 只有一个归属层，任一层都不得输出别层的闸口。**

### FEA 求解器降级链

按可用性自动降级（`physics/fea_solver.py`）：

| 级别 | 后端 | 依赖 |
|------|------|------|
| Level 0 | 解析解（梁理论） | 无 |
| Level 1 | 纯 Python FEA（numpy CST 三角形 / CTE 四面体） | numpy |
| Level 2 | Gmsh + CalculiX | Gmsh, CalculiX |
| Level 3 | Gmsh + Code_Aster | Gmsh, Code_Aster |
| Level 4 | Gmsh + Elmer | Gmsh, Elmer |
| Level 5 | SolidWorks Simulation API | SW Premium 许可 |

**网格适配模式**（`physics/mesh_adapter.py`）：Level 0 解析（无网格）→
Level 1 桁架/梁网格 → Level 2 Gmsh STEP→MSH → Level 3 SW Simulation 原生。

### 物理子系统模块（14 个）

| 模块 | 职责 |
|------|------|
| `load_case.py` | 载荷工况 JSON 解析、校验与标准化 |
| `material_db.py` | 工程材料数据库（钢/铝/钛/工程塑料，零外部依赖） |
| `geometry_gate.py` | 几何门禁：设计空间 / 连通性 / 最小壁厚 / 流形 / 配合面 |
| `mesh_adapter.py` | STEP 导出 + 网格生成适配层 |
| `fea_solver.py` | 多后端 FEA 适配层（含 `aggregate_overall`、材料守卫） |
| `feapy_solver.py` | 纯 Python FEA（2D CST + 3D CTE） |
| `fatigue.py` | 疲劳强度与设计寿命（Basquin S-N + Marin + Miner） |
| `simulation_report.py` | 结构化仿真报告 + `GATE_OWNER` 归属层表 |
| `design_state.py` | 迭代状态管理（参数变更/结果/回滚点） |
| `refine_rules.py` | 自动修正规则引擎 |
| `domain_validator.py` | 领域规则校验（structural / transmission / housing / mold） |
| `_compat.py` | 向后兼容别名 |
| `__init__.py` / `_test_imports.py` | 包初始化与导入自检 |

### 命令行参考

> 除 `physics_bridge.py` 用标准 argparse 外，其余均为手写 `sys.argv` 分派。

#### `workflow_gate.py` — 门禁大脑（13 个规范子命令，含别名）

| 子命令 | 用途 |
|--------|------|
| `init <任务描述> [--force]` | 初始化任务，返回第 0 题（参数需求强度） |
| `provide_context <回答原文>` | **需调用两次**：① 判深度返回第二段问题 ② 力学估算返回 A/B/C + D/E |
| `select <A\|B\|C>[,D\|E]` | 选定搭建方式与并行策略（`C,E` / `C E` / `--choice C --parallel E` 均可） |
| `confirm-assembly <零件清单>` | 第 1 波完成后确认总装（缺此步流程卡死） |
| `check-part <文件> --room <房间>` | 校验零件归属 |
| `verify-ownership [目录]` | 总装前核对归属与去重 |
| `status` / `gate-summary` / `check` | 门禁状态 / 总览 / 是否允许建模 |
| `work-dir [新目录]` | 查询/设置本任务交付目录 |
| `recover` / `restart <房间>` / `reset` | 恢复卡住状态 / 重做房间 / 清空重来 |

#### `mode_gate.py` — 房间状态机 + SW 锁（33 个规范子命令，节选）

| 类别 | 子命令 |
|------|--------|
| 模式 | `declare <1\|2\|3>` |
| 房间 | `room-start` / `room-end` / `room-fail` / `room-status` / `room-report` / `room-report-read` |
| SW 锁 | `sw-request` / `sw-wait` / `sw-release` / `sw-status` / `sw-proc` / `lock-doctor` |
| 子代理 | `subagent-assign` / `subagent-free` / `subagent-status` / `platform-sync` / `whoami` |
| 心跳 | `room-heartbeat` / `room-heartbeat-check` |
| 归属 | `room-artifact` / `confirm-part`（C 模式零件授权） |
| 监控 | `sw-monitor-start` / `sw-monitor-stop` / `sw-monitor-status` / `sw-monitor-bg` |
| 维护 | `doctor` / `rooms-reset` / `stale-artifacts` / `residue-check` / `set-parallel-mode` |

#### `sw_bridge.py` — SolidWorks 桥接（38 子命令，节选）

| 类别 | 子命令 |
|------|--------|
| 环境 | `doctor` / `status` / `self-path` |
| 文档 | `new` / `open` / `show` / `list` / `info` / `close` / `close-all` / `save` |
| 建模 | `run <script.py>` / `sketch-rect <w> <h> <depth>` / `cleanup` |
| 出图 | `drawing` / `dwg` / `dxf` / `export-pdf` / `annotate` / `title-block` / `reading` |
| 归属 | `check-part` |
| AutoCAD | `ac-status` / `ac-export` / `cad-validate` / `cad-validate-live` |
| 物理 | `physics-demo` / `physics-status` / `physics-validate-case` / `physics-optimize` / `physics-report` / `physics-recommend` / `physics-fatigue` / `physics-validate-domain` / `physics-list-domains` |
| 视觉 | `vision-fallback` / `check-vision` |

> `_READONLY_CMDS`（不连 SW、不取锁、不强制 `--room`）：`self-path, doctor, status,
> info, list, sw-proc, help, version, ping` + 全部 `physics-*` + `cad-validate`。
> `_NO_ROOM_CMDS`：`close-all, sw-release, rooms-reset`。
> 退出码：`0` 正常 / `1` 未预期异常 / `2` 调用方式错误 / `3` SW 忙需重试。

#### `physics_bridge.py` — 物理验证 CLI（11 子命令，argparse）

| 子命令 | 用途 |
|--------|------|
| `demo` / `status` | 悬臂梁演示 / 求解器后端状态 |
| `validate-case <case.json> [--relaxed]` | 校验载荷工况 |
| `build <case.json>` / `simulate <run_dir>` | 初始化仿真环境 / 运行仿真 |
| `report <run_id>` / `recommend <run_id> [--max-iter N]` | 查看报告 / 生成修正建议 |
| `optimize <case.json> [--max-iter N]` | 自动迭代优化（默认 5 轮） |
| `fatigue [case.json] [--stress <MPa>] [--report <run_id>]` | 疲劳 / 设计寿命（默认 30 年） |
| `validate-domain [domain] [--room] [--param k=v] [--params-file]` | 领域规则（GB/T）校验 |
| `set-design-params [--param k=v] [--file] [--show]` | 写入 `tools/design_params.json` |

#### `defense_gate.py` — 三大防线（3 子命令）

| 子命令 | 用途 |
|--------|------|
| `check-room <房间名>` | 校验单房间 ①②③（缺房间名 → exit 2；不通过 → exit 1） |
| `check-task` | 全任务三防线汇总（交付前总门禁） |
| `status` | 签名宿主信息 + 房间表 + reports_dir + bypass_env（只读） |

#### 其他

| 脚本 | 用法 |
|------|------|
| `ask_user.py` | 全为选项参数：`--child <sessionId>`（必填）/ `--room` / `--question` / `--option`（可多次）/ `--multi` / `--timeout`（默认 540s）。POST `/ask-child` 后原地轮询 |
| `mode_checker.py` | `python mode_checker.py <1\|2\|3> [--verify]` → 写 `mode_logs/` 审计 |
| `run_regression.py` | `python run_regression.py`（全部）或 `python run_regression.py 36 39`（指定 Bug） |
| `_store.py` | `--migrate` / `--dry-run` / 无参打印状态报告 |
| `_root.py` | 打印根目录 + 全部门禁脚本路径 + 候选列表 |

### 插件 HTTP 路由（13 条，与 Harness Web 同源）

| 路由 | 方法 | 用途 |
|------|------|------|
| `/dsh-engineering-ui/agents` | GET | 子代理树 |
| `/dsh-engineering-ui/log` | GET | 单个子代理实时日志 |
| `/dsh-engineering-ui/verify-subagent` | GET | 子代理启动校验 |
| `/dsh-engineering-ui/notify-room` | — | 房间通知（不打断建模） |
| `/dsh-engineering-ui/ask` / `answer` | POST | 主对话阻塞式提问 / 作答 |
| `/dsh-engineering-ui/ask-child` | POST | 子代理提问（挂起到主对话） |
| `/dsh-engineering-ui/pending-child` / `result-child` | GET | 查询待答 / 取回结果 |
| `/dsh-engineering-ui/defense/sign` / `verify` | — | 防线凭据签发 / 验签 |
| `/dsh-engineering-ui/defense/info` / `judge` | — | 签名宿主信息 / 总判定 |

### 技能清单（7 个）

| 技能 | 说明 |
|------|------|
| `mode-selection` | **强制入口**：模式 1/2/3 选择 + Mode 2 门禁全流程 |
| `assembly-orchestration` | 多屋协作架构（大屋编排小屋，串行/并行，结果压缩传递） |
| `cad-workflow` | AutoCAD 机械设计工作流（含 DXF 几何验证 CADX、GB/T 规范、DWG/PDF 导出） |
| `sw-design` | SolidWorks 机械设计工作流（建模、装配、工程图、GB/T 制图、CADX） |
| `solidworks-bridge` | SW 自动化建模桥接（`win32com`，SW 2018~2024，通用不硬编码路径） |
| `sw-to-cad` | SW 零件一键转 CAD 工程图（三视图 + 等轴测 → DWG / PDF） |
| `physics-in-loop` | 物理仿真驱动闭环（载荷工况 → FEA → 迭代优化） |

### 回归测试（8 个）

```powershell
python engineering\tools\run_regression.py        # 全部
python engineering\tools\run_regression.py 36 39  # 指定 Bug 号
```

| 测试 | 覆盖 |
|------|------|
| `test_regression_bug34_dimension.py` | 尺寸 API 与几何降级清单 |
| `test_regression_bug36_roomend.py` | `_finalize_room` 返回分支数量一致性 |
| `test_regression_bug37_42_43_cut.py` | 薄壁/小孔/方向偏的成功切除拓扑判定 |
| `test_regression_bug38_ownership.py` | 零件归属清单 + 越界校验 + 去重 |
| `test_regression_bug39_script.py` | wrapper 唯一性 + 脚本身份自校验（sha1） |
| `test_regression_bug41_gear.py` | 参数化齿轮齿廓几何正确性 + 点数可控 |
| `test_regression_scope_leak.py` | 跨作用域别名泄漏（`NameError` 类缺陷静态防线） |
| `test_regression_question_contract.py` | 固定两问（A/B/C + D/E）题面契约 |

### 已修复问题（节选）

| # | 问题 | 修复 |
|---|------|------|
| 1 | 主对话误判子代理状态 | `mode_gate.py` 六态分类 + 旁路只读命令 |
| 2 | 总装未等全部零件完成就启动 | `confirm-assembly` 门禁 |
| 3 | C 模式无代码级强制 | `CONFIRMATION_PROTOCOL_C` 逐零件循环 |
| 4 | 任务完成后循环交代 | `_archive_finished()` 状态机收尾 |
| 5 | 跨任务状态污染 | `_reset_mode_rooms()` 仅在首次 `select` 清理 |
| 6 | `send_message` 插队打断小屋 | persona 禁止插队铁律 |
| 9 | **子代理越权确认**（沿用上一个零件的确认） | `confirm-part` 授权令牌 + 越权留痕 |
| 10 | **SW 锁饥饿**（单房间独占致队列饿死） | 公平轮转（命令数 + 独占时长双判据） |
| 14 | **问题集不一致**（有时 5 问有时 10 问） | `QUESTION_SPEC` 单一事实来源，统一 17 题 |
| 15 | 子代理不知自己 sessionId 而卡死 | 新增 `whoami` 身份自查 |
| 16 | 报告始终为 `null` | `registered` 改为开工必做第一件事 |
| 17 | 让位时把官方右列压扁（`0px !important`） | 撤掉整条覆盖，交回宿主内联样式 |
| 18 | `min-width` 用 `>*` 命中左栏致边框跳位 | 改用 `:nth-child(2)` 只作用于主对话列 |
| 19 | body class 残留致"设置不生效" | `engAllowed` 纳入 effect 依赖 + 无条件清理 |
| 20 | 让位判据 `openTabs` 跨会话持久化致永久让位 | 只认官方 `isExpanded()` |

### 版本历史

| 提交 | 日期 | 说明 |
|------|------|------|
| `eb55f4a` | 2026-10-01 | **已适配 DSH 0.2.0 桌面版**（bundle 路径、desktop profile、插件三栏 UI） |
| `d2e6828` | 2026-09-13 | 工程工作流更新：技能、mode gate、插件、SW 桥接改进 |
| `aa37a7a` | 2026-09-04 | 开学前最后更新 |
| `e9f42a5` | — | 大幅改进大型复杂器械装配模式的工作原理 |
| `b93fa01` | — | 新增物理规则、领域校验器；更新 SW 桥接与文档 |

### 已知限制

1. **`sw_bridge.py` 帮助文本列出 `bore <孔径> [--axis Z]`，但 dispatch 中无对应分支** ——
   调用会落入 `unknown command`。请勿当作可用子命令。
2. **SolidWorks 未安装时无法验证 SW 相关行为**（`massprops`、`GetBodyBox` 等）。
3. **CADX 7 项几何检查**中的重叠检查依赖 `ezdxf` + `shapely`，缺失时自动跳过。
4. **疲劳校核中铝合金按无真实疲劳极限处理**（用 5×10⁸ 次条件极限）。
5. `engineering/README.md` 与部分脚本文件为 **UTF-8 with BOM**，
   个别工具（如 Windows PowerShell 5.x 的 `ConvertFrom-Json`）可能误读。

### 技术栈

| 组件 | 技术 |
|------|------|
| 核心框架 | [DeepSeek Harness](https://github.com/deepseek-ai/dsh)（Cordis 插件架构） |
| SW 自动化 | Python `win32com`（`SldWorks.Application`） |
| CAD 自动化 | Python `pyautocad` / `comtypes` |
| FEA 求解 | numpy（纯 Python CST/CTE），可选 skfem / Gmsh / CalculiX |
| DXF 验证 | `ezdxf` + `shapely`（可选） |
| 图像 | `Pillow` + `mss`（可选） |
| 界面插件 | ES module，React 18/19 peer，`window.__ModuleLoader__.load()` |

---

## License

MIT — 见 [LICENSE](LICENSE)。

本项目是 [DeepSeek Harness](https://github.com/deepseek-ai/dsh) 的**第三方 Agent Preset**，
与 DeepSeek 官方无隶属关系。SolidWorks 与 AutoCAD 是 Dassault Systèmes 与
Autodesk 的商标，本项目仅通过其公开 COM 接口进行自动化调用。
