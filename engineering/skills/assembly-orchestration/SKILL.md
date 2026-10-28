---
name: assembly-orchestration
description: >-
  大型复杂器械装配的多屋协作架构。
  主对话（大屋）编排多个子代理（小屋）串行/并行工作，
  每个小屋有独立历史记录，结果压缩传递。
  触发关键词：装配体、多零件、传动系统、大型器械、机构设计、总装。
whenToUse: >-
  当任务被识别为 ② 型（大型复杂器械/多零件装配体）时自动激活。
  大屋（主对话）使用本流程编排各小屋完成设计。

## ██ 强制执行规则 ██

**以下规则必须遵守，不可跳过：**

1. **必须使用 subagent 创建小屋**
   - 每个设计任务必须用 `subagent` 或 `subagent_fork` 启动独立子代理
   - 禁止在主对话中直接完成小屋的设计工作
   - 每个小屋有独立历史，避免记忆堆积
   - 🚫【铁律】所有小屋创建完毕前，主对话禁止调用 sw_bridge.py 做任何建模操作

2. **小屋完成后必须清理**
   - 任务完成或失败后，立即调用 `interrupt_agent` 停止小屋
   - 禁止保留已完成的小屋会话

3. **重试必须创建新小屋**
   - 失败重试时，必须用 `subagent` 创建全新子代理
   - 新 prompt 必须包含失败原因和修正要求
   - 禁止在原子代理上 `send_message` 重试

disable-model-invocation: false
user-invocable: true
source: engineering
provider: filesystem
---

# ② 大型复杂器械 - 多屋协作架构

> 本技能定义了 DSH 工程模式下，**②型大型复杂器械任务**的执行架构。
> 核心思想：**大屋（主对话）总控 + 小屋（子代理）独立执行**。

## ██ 【强制前置检查】模式验证 ██

**⚠️ 未通过模式验证，禁止执行本技能任何内容！**

### 验证流程（必须按顺序执行）

```
步骤1: 查找对话开头的【模式选择】声明
        │
        ├─→ 未找到？ → 立即停止，询问用户选择模式
        │
        └─→ 找到？ → 检查声明内容
                 │
                 ├─→ 模式 = ① 或 ③？ → 立即退出，不执行本技能
                 │
                 └─→ 模式 = ②？ → 继续验证
                          │
                          ├─→ 已用 subagent 创建小屋？ → 继续执行
                          │
                          └─→ 未创建小屋？ → 立即创建小屋，再执行
```

**验证不通过 = 禁止执行**

检查步骤：
1. 查看对话开头的【模式选择】声明
2. 如果模式是 ① 或 ③，**立即停止**，不执行本技能
3. 如果未声明模式，**立即询问用户**选择模式

```
模式验证通过 → 继续执行多屋协作架构
模式验证失败 → 退出，回到主对话流程
```

**违规后果**：在 ①/③ 模式下使用本架构，会导致：
- 不必要的子代理创建
- 执行流程混乱
- 资源浪费

## 一、架构总览

> 🔴 **【2026-10-28】** 房间表**不再固定**。数量与细分由【总计子代理】按功能分解决定，
> 主对话只照着 `select` 返回的 `wave1` 开房间。下图是**流程形状**，不是固定房间清单。

大屋（主对话）
  |  用户交互 | 门禁 | fork 编排 | 结果汇总 | 最终交付
  |
  +---> [总计子代理] 十阶段分析 + 登记房间编制（analyst-bind / set-subagent-plan）
  |        | 产出：编制（rooms.json）—— **房间数由它决定**
  |        v
  +---> wave1（同一条回复一次性 fork 全部）：
  |       [建模房间1] [建模房间2] ... [建模房间N]   ← 数量来自编制，不是固定 3/5 个
  |       [总理子代理]（与建模同批启动，长生命周期监工）
  |         每个 fork 后立刻 mode_gate.py subagent-assign <房间> <id>
  |
  +---> wave2：[总装与验证]   （wave1 全部 room-end 后）
  +---> wave3：[工程图输出]   （wave2 room-end 后）
  |
  +---> 大屋汇总交付（收尾前 room-end 总理子代理）

### 错误路由（红线）
小屋N FAIL
    |
    v
大屋收到 FAIL -> 分析原因 -> 生成修正指令
    |
    +---> 启动新小屋N-1（修正指令 + 原始输入 + 错误信息）
    |         |
    |         v
    |      新小屋N-1 重新设计 -> 输出修正结果
    |         |
    |         v
    +---> 将修正结果传递给小屋N -> 重试
    |
    +---> 连续2次 FAIL -> 升级用户，请求人工决策

### 子代理生命周期管理

> **核心原则**：小屋完成任务或失败后，立即清理旧会话，避免记忆堆积。

#### 1. 小屋完成/失败后的清理流程

```
小屋N 状态: PASS/FAIL/TIMEOUT
    |
    v
大屋收到结果
    |
    +---> 用 interrupt_agent 停止小屋N（如果还在运行）
    |         ↓
    +---> 用 job_kill 终止后台任务
    |         ↓
    +---> 记录结果到 todo_write
    |         ↓
    +---> 继续下一个任务
```

#### 2. 重试时的"新对话"策略

当小屋N失败需要重试时：**不要**在原子代理上 `send_message` 重试，而是创建**全新子代理**：

```python
# 错误做法（旧会话堆积）:
send_message(subagent_id=N, message="重新做...")

# 正确做法（新对话，继承错误信息）:
new_prompt = f"""
【任务目标】{原始任务}
【历史错误】{N失败的错误信息}
【修正要求】根据错误调整设计
【约束】{{
材料: xxx,
尺寸: xxx,
}}
"""
subagent(description="小屋N-重试", prompt=new_prompt)
```

#### 3. 超时处理

子代理配置了 **2分钟空闲超时**（`idleTimeout: 120000`）和 **最多16轮对话**。

当出现 `pi-ai stream idle timeout` 错误时：
1. 立即调用 `interrupt_agent` 停止该子代理
2. 记录超时原因
3. 创建新子代理重试（使用新 prompt，包含错误信息）

#### 4. 大屋的职责清单

每次启动小屋时，用 `todo_write` 记录：小屋ID / 启动时间 / 任务目标。
每次小屋完成后，把 todo 更新为 `completed` 或 `failed`。

**判活只认看板，不认"跑了多久"**：
```powershell
python "<工程模式根目录>\tools\mode_gate.py" room-board --json
```
- 只看每个房间的 `state`：
  · `starting` / `running` / `idle` → 正常（`running` 时禁止说"没反应"）；
  · `waiting_sw` → **在排 SW 锁，属正常等待，禁止判死、禁止 reset**；
  · `unbound` → 平台 alive 但没 `subagent-assign` → **补 assign**；
  · `dead` → 才允许 `recover` / `restart`，且先看 `safe_to_restart`；
  · `done` → 收结果、更新 todo。

> 🔴 旧版这里写着"每 5 分钟扫一遍，`running` 且 `startedAt` 超过 3 分钟就 `interrupt_agent`"——
> **该做法已删除**：它会把排队等 SW 锁（`waiting_sw`）的正常小屋当超时杀掉。

## 二、三大功能运行区（黄线 & 蓝线）

这三个功能是贯穿多屋协作流程的**验证层**，运行在每个小屋内部：

> 注：本节用「小屋1/2/3」指代编制里的前几个**建模房间**（示例叫法）。
> 实际房间名与数量**以总计子代理的编制为准**，不是固定三间。

### ① 领域特定验证代理（蓝线）— 作用于小屋1/2/3

**触发时机**：每个小屋完成零件设计后
**作用**：根据小屋的设计域类型，注入对应领域的验证规则

| 小屋 | 设计域 | 注入规则文件 | 检查内容 |
|------|--------|------------|---------|
| 小屋1 | 结构件 | structural_rules.json | 梁/板/支架的强度与刚度规则 |
| 小屋2 | 传动件 | transmission_rules.json | 轴/齿轮/轴承的接触疲劳与弯曲强度规则 |
| 小屋3 | 壳体/机架 | housing_rules.json | 箱体的刚度、壁厚、铸造工艺规则 |

扩展方式：在 `rules/<domain>_rules.json` 中定义规则，通过 `refine_rules.py` 自动加载。
当前框架已就绪（refine_rules.py + design_state.py），规则文件待填充。

### ② 生成-仿真-优化的物理在环迭代架构（黄线）— 作用于小屋1/2/3

**触发时机**：每个小屋完成单个零件建模后，自动进入此循环
**作用范围**：小屋1/2/3 各自内部的零件级 FEA 验证

流程：
```
零件建模完成 -> 自动生成载荷工况 -> FEA求解 -> 结果判定 -> (FAIL时)自动优化迭代
```

- **Step 1**：自动推导载荷工况，生成 load_case.json
  `python "<工程模式根目录>\tools\sw_bridge.py" physics-validate-case <case.json>`
- **Step 2**：FEA 求解
  - 优先使用解析解（Level 0，无需外部软件）
  - 次优先纯Python FEA（Level 1，numpy CST三角形单元）
  - 若有 Gmsh/CalculiX 则使用完整3D求解（Level 2+）
- **Step 3**：结果判定
  - **PASS**（安全系数在 2~5 之间）-> 通过，进入下一零件
  - **WARNING**（安全系数 > 5，过度设计）-> 记录但允许通过
  - **FAIL**（安全系数 < 2，强度不足）-> 进入 Step 4
- **Step 4**：自动优化迭代（最多5轮）
  `python "<工程模式根目录>\tools\sw_bridge.py" physics-optimize <case.json> --max-iter 5`
  自动调整厚度/圆角/加强筋，重新求解

### ③ 设计规则检查与几何拓扑验证框架（黄线）— 作用于小屋4/5

**触发时机**：小屋4 总装后 + 小屋5 出图后

**A. 几何拓扑验证**（小屋4 总装阶段）：
- 干涉检查：全量配对干涉检测
- 运动学间隙：运动部件间隙是否满足最小要求
- 装配约束合理性：配合类型是否正确（重合/同轴/距离等）
- 调用：`python "<工程模式根目录>\tools\sw_bridge.py" status` + SW 内置干涉检查工具

**B. GB/T 制图规范检查**（小屋5 出图阶段）：
每次生成工程图（.slddrw）后**必须**自动运行：
1. 视图完整性：简单零件>=2个视图，复杂结构需剖视/局部放大图
2. 尺寸标注：外径、孔径、孔位、总长/宽/高必须标注
3. 公差标注：配合面需标注公差（GB/T 1800）
4. 表面粗糙度：关键配合面需标注粗糙度（GB/T 131）
5. 形位公差：同轴度、垂直度等必要时标注（GB/T 1182）
6. 标题栏：材料、比例、图号、设计者信息

**C. CADX 几何验证**（小屋5 出图后）：
`python "<工程模式根目录>\tools\sw_bridge.py" cad-validate <图纸.dxf>`
- 7项检查：未闭合多段线、重叠几何、尺寸过小、长宽比、墙厚、缺失元素、标注不一致
- CRITICAL（红线）：必须修复 -> 触发红线路由回小屋5
- WARNING（黄线）：建议修复

## 三、大屋（主对话）职责清单

### 3.1 系统分解（进入②模式后立即执行）
1. 理解用户需求的整体功能目标
2. 识别系统的**主要功能模块**（结构/传动/壳体等）
3. 确定模块间的**接口关系**（哪些尺寸/位置需要耦合）
4. 确定模块间的**依赖关系**（哪些必须串行，哪些可以并行）
5. 用 todo_write 列出各小屋的任务清单

### 3.2 小屋编排（串行模式必须严格逐项执行）

⚠️ 【串行模式铁律】严禁一次性创建所有小屋！必须按以下顺序逐项执行：

对每个小屋（串行）：
1. **先**调 `mode_gate.py room-start <房间名>` 登记（在创建 subagent 之前！）
2. 用 **subagent_fork** 启动小屋（继承主对话上下文）
3. **等待**该小屋完成（list_agents 检查 status=completed/idle）
4. 调 **interrupt_agent** 停止小屋
5. 调 **mode_gate.py room-end <房间名>** 解锁串行锁
6. 将结果压缩后，再处理下一个小屋

并行模式（D）—— 三波式推进，**房间表由总计子代理决定**：

> 🔴 **【2026-10-28】旧版这里写死了「第1波 = 结构件+传动机构+壳体机架 三个房间」的固定表**，
> 该表**已废弃（deprecated）**，连同它那个"爆发期"式叫法一起停用。
> 房间表的唯一来源 = `mode2_flow.py set-subagent-plan`
> 登记的编制（总计子代理产出）；`select` 的 `wave1` 只是把它读出来，并固定追加「总理子代理」。
> **代码默认表不得作为兜底。**

```
wave1 = select.wave1 = 编制里的全部建模房间 + 总理子代理   ← 同时启动（同一条回复）
wave2 = 总装与验证                                       ← wave1 全部 room-end 后
wave3 = 工程图输出                                       ← wave2 room-end 后
```

**wave1 步骤（不可打乱）**：
1. 对每个房间 `mode_gate.py room-start <房间名>`（登记，**在创建 subagent 之前**）
2. **同一条回复里一次性 `subagent_fork` 全部房间**（含总理子代理）——⚠️ 严禁只开一个
3. **每个 fork 后立刻** `mode_gate.py subagent-assign <房间名> <fork 返回的 id>`
   （漏了 → `room-board` 永远 `unbound`，上报与判定全部失真）
4. 建模小屋 prompt：只做本房间零件 + 本房间 `part_ownership` keywords

报错回退：定位出错源房间（房间名以**编制为准**）→ `mode_gate.py room-fail <源房间>`
→ 重新 `select` → 重做该房间 → 再进 wave2。

**总理子代理（与 wave1 同批启动，长生命周期监工）**：
- 开工 `mode2_flow.py prime-board` / `prime-room`；
- 循环读 `mode_gate.py room-board --json`，**只看 `state`**：
  `running`/`starting`/`idle` 正常；**`waiting_sw` = 正常等待（不许判死）**；
  `unbound` → 提示父代理补 `subagent-assign`；`dead` 才报；`done` 收报告；
- 收件 `prime-report` → `check-consistency` → `prime-verify`；
- **不计入 wave 完成判定**（长生命周期），但**收尾前必须 `room-end 总理子代理`**。

SW 使用窗口互斥（进程级强制，检测 SLDWORKS.EXE 进程）+ 锁排队：
  - 每个小屋的 sw_bridge.py 调用自动检测 SW 进程并 FIFO 排队（谁先排队谁先用）
  - 某个小屋在用 SW 时，其他小屋的调用返回 sw_busy:true → 执行 Start-Sleep -Seconds 90
    后重试同一条命令（必须带 --room 房间名），队列位置保留，**禁止放弃**
    （`room-board` 里这时显示 `waiting_sw`，**属正常等待，不是死亡**）
  - 队首完全关闭 SW 进程后，下一个房间才获得 SW 使用权
  - 小屋完成建模后必须 sw_bridge.py close-all 完全关闭 SW 再收工
  - room-end 兜底：发现本房间启动的残留 SW 进程，等 20 秒后强制关闭
  - 建模必须写单个脚本用 sw_bridge.py run 一次执行多步（一次调用规划多步操作）
  - 开下一个子代理（同级或下一级）前必须先收尾 SW（close-all）
  - **🔴【Bug-23 修复 + 2026-10-28】存活判定只认 `room-board`**：
    用户明确要求"去掉心跳机制，直接采用 DSH 平台 running 状态判定子代理是否死亡"。
    实测：小屋心跳 age 涨到 1222~1777s，但平台侧 list_agents 始终 running、
    且持续产出零件 —— 心跳完全失真（小屋忙于 SW 建模没空调 mode_gate）。
    · **唯一权威看板**：`python "<工程模式根目录>\tools\mode_gate.py" room-board --json`，
      每房间给 `state` / `detail` / `subagent_id` / `platform` / `report_stage` /
      `sw_queue_pos` / `safe_to_restart` / `next_action`；
    · 只看 tuple（`state` / `safe_to_restart` / `next_action`）：
      `running`/`starting`/`idle` 正常；**`waiting_sw` = 在等 SW 锁，属正常，禁止判死**；
      `unbound` = 平台 alive 但没 assign → **补 assign，禁止 reset**；`dead` 才可 recover/restart；
    · 主对话**不再**必须手写 `platform-sync`（保留为人工覆盖手段，不是心跳义务）；
    · 小屋 **不需要** room-heartbeat（命令保留兼容，已降级为辅助证据）；
    · 小屋只需在关键节点 room-report，并在 save 后 room-artifact 登记产物归属。

## 四、小屋工作规范

### 🚫 小屋铁律（不可违反）🚫

**大屋（主对话）独占：mode_gate / workflow_gate / subagent 相关命令**
小屋（子代理）在任何情况下都**绝对禁止**调用以下命令——调用了就是夺舍，主流程会被劫持：
- ❌ 禁止调用 mode_gate.py **编排命令**（room-start / room-end / room-fail / sw-request / sw-wait / sw-release / declare / check）
- ❌ 禁止调用 workflow_gate.py（init / provide_context / select / status）
- ❌ 禁止调用 list_agents / interrupt_agent / send_message / subagent_fork 等编排命令
- ❌ 禁止修改 mode_state.json / workflow_state.json

✅ **小屋允许调用的 mode_gate.py 命令（仅进度上报通道）**：
- room-report <房间名> <阶段> <详情>   —— 上报建模进度
- room-artifact <房间名> <文件路径> [零件名]  —— 【Bug-16/17】登记本房间产物归属
- room-report-read                     —— 读取进度报告
- whoami <房间名>                      —— 【Bug#5 修复】自查身份，**无需任何参数**
- room-heartbeat <房间名>              —— 【Bug-23】兼容保留，**已非必需**

这些命令**只写入 reports/ 与 heartbeats/ 文件**，不修改 mode_state.json 的
房间状态、SW 锁、队列，因此不属于编排命令，小屋可安全调用。
（【B2 修复】旧版铁律笼统写"禁止调用 mode_gate.py"，与 C 模式要求的
 "小屋进度旁路上报" 直接矛盾，导致 C 模式无法合规执行。现按命令语义区分。）

> 🔴 **【2026-10-28】「总计子代理」是唯一例外**（它不是建模小屋，职责是十阶段分析）：
> **允许**调 `mode2_flow.py` 的阶段 / 提问 / 编制命令 ——
> `analyst-bind` / `begin-stage` / `mark-stage-done` / `ask` / `answer-question` /
> `refresh-stage` / `set-subagent-plan` / `stage-status` / `questions`，
> 以及 `tools/ask_user.py`（子代理**唯一**的提问桥；子代理直调 `ask_user_question` 必抛 `DELEGATED_CALLER`）。
> 它**仍然禁止**调 `mode_gate.py` 的编排命令（room-start / room-end / subagent-assign / recover 等）——
> 那是主对话与总理子代理的职责。**别的命令都不许由小屋碰。**

小屋的唯一职责：在本小屋负责的房间内完成建模 / 验证 / 输出文件，并上报进度。
所有房间登记、锁管理、波次推进、子代理编排，**全部由大屋统一调度**，小屋一概不参与。

### 🆔 身份自查（Bug#5 修复 · 一条命令搞定）

小屋开工第一件事：

```
python "<工程模式根目录>\tools\mode_gate.py" whoami "<你的房间名>"
```

**不需要传 --session-id！** 本命令自动从 DSH 注入的环境变量
（`DSH_SESSION_JSONL` / `DSH_SESSION_ID`）反解出你自己的 sessionId 并就地登记。
返回的 `subagent_id` 即 `ask_user.py --child` 的值。

> 【Bug#5 修复背景】旧流程要求小屋"知道自己的 sessionId"，但该 id 由 DSH 在
> fork 时分配、**根本不在小屋 prompt 里可见** → 小屋只能走 `--wait 30` 兜底，
> 一旦主对话尚未 `subagent-assign` 就超时失败（时序竞态）。
> 现在 whoami 自给自足，竞态被彻底消除。

### 🔒 并发脚本隔离（Bug-39 修复 · 已自动生效）

**小屋无需做任何事**：`sw_bridge.py run` 已修复并行脚本串号问题。

> 【Bug-39 背景】原实现把执行包装器写成固定名 `_sw_run_wrapper.py` 且放在
> **脚本同目录** —— 多小屋并行时互相覆盖，导致
> "执行 build_transmission_6.py 实际跑的是 build_servo_v6.py"（**无任何报错**）。
> 修复后：① 包装器名含 pid+脚本哈希，写在**临时目录**；
> ② 包装器内**自校验**脚本路径与内容哈希，不符即拒绝执行（exit 3/4）；
> ③ 返回值新增 `executed_script` / `executed_script_sha1` 供核对。
> 若看到 `script_identity_verified: false`，说明脚本在运行前被改动，需排查。

### 📋 零件归属清单（Bug-38 修复 · 大屋 + 小屋都用）

`select` 现在额外返回 `part_ownership`（machine-readable JSON）并落盘到交付目录：

```json
{
  "schema": "dsh-engineering/part-ownership@1",
  "rooms": { "结构件": {"keywords": ["车架底板","横梁","悬架臂"]} },
  "part_to_room": { "车架底板": "结构件", "电机座": "传动机构" }
}
```

**大屋**：把每个房间的 keywords 写进小屋 prompt（"只允许产出这些零件"）。
**小屋**：保存前自检 —— `python sw_bridge.py check-part <文件> --room <房间名>`；
越界零件会被**拒绝保存**并记入 `part_ownership_violations.json`。
**总装前**：`python "<工程模式根目录>\tools\workflow_gate.py" verify-ownership`
做归属核对与**去重**（递归扫描各小屋子目录，检出同名重复零件）。

> 【Bug-38 背景】实测结构件小屋误产"电机座"（属传动机构）、
> 传动机构小屋误产"转向支撑座"（属支撑结构），产出目录混入别房间零件；
> 若小屋不自纠，总装时会出现重复或缺失零件。

### 🧭 产物归属登记（Bug-16/17 修复 · 小屋必做）

小屋每次 `save` 出零件后，**主动登记产物归属**，取代 mode_gate 的 mtime 猜测：

```
python "<工程模式根目录>\tools\mode_gate.py" room-artifact <房间名> "<文件绝对路径>" [零件名]
```

> 【Bug-16/17 修复背景】原实现只按"工作目录里最近 mtime 的文件"推断产物归属，
> 导致结构件刚产出的 `DSH_车架底板.SLDPRT` 被**同时归到 4 个房间**名下
> （各房间 `disk_artifact=true`、`age_sec=2.5`）。后果：房间完成判定过早、
> recover 误判、无法追溯"哪个房间造了哪个零件"。
> 登记后 room-status 只显示**本房间登记过**的文件，归属确定。

### 💓 心跳（Bug-23 / 2026-10-28 · 已停用；判活只认 room-board）

**小屋不需要心跳，主对话也不需要手写 `platform-sync`**。唯一权威看板：

```powershell
python "<工程模式根目录>\tools\mode_gate.py" room-board --json
```

每房间给 `state` / `detail` / `subagent_id` / `subagent_label` / `platform` /
`report_stage` / `sw_queue_pos` / `safe_to_restart` / `next_action`。

- **只看 tuple（`state` / `safe_to_restart` / `next_action`）**，不要拿 mtime、心跳 age、
  `list_agents.startedAt` 自行推算死活；
- `waiting_sw` = 在等 SW 锁，**属正常等待**；
- `unbound` = 平台 alive 但没 assign → **补 assign**，**禁止 reset**；
- `dead` 才允许 `recover` / `restart`。

> 【Bug-23 修复背景】旧设计依赖心跳，实测心跳 age 涨到 1222~1777s 而平台侧
> 始终 running、且持续产出零件 —— 心跳完全失真，据此 recover 只会误杀
> 正在干活的屋子。`sw_bridge.py` 仍会在取锁/释放时顺手刷新心跳（兼容），
> 但它**不再是判定死活的前提**。

### 🧹 残留检测（本轮新增 · 开始前 + 结束前各一次）

大屋在**开工前**与**收尾前**各跑一次体检，确保没有上一个对话的子对话残留干扰：

```
python "<工程模式根目录>\tools\mode_gate.py" residue-check          # 只体检
python "<工程模式根目录>\tools\mode_gate.py" residue-check --clean  # 体检并清理
```

- **开始前**：`workflow_gate.py init` 会自动调用。
  若检出上一轮残留（僵尸房间/孤儿 subagent 映射/step 卡在 user_selected），
  **自动清理**后放行；若检出仍有**活着的**子对话，则拒绝 init 并告诉你谁在跑。
- **结束前**：归档 `finished` 之前自动体检。
  仍有活动的子对话 → **拒绝宣告完成**（防止"活没干完就说结束"）。

> 【修复背景】旧版新任务会被上一轮的 `step=user_selected` 拦截（Bug#1），
> 或把上一轮的 `ended_at` 误判为本轮完成（假 finished，Bug#6 同源）。

---

### 4.1 启动时接收的输入格式
【任务目标】
{本小屋的设计目标描述}

【上游输入】
- 用户需求摘要: {从大屋压缩的关键需求}
- 小屋N-1结果: {上一个/多个小屋的压缩结果}

【约束条件】
- 材料: {指定材料或 TBD}
- 接口尺寸: {来自上游的必须遵守的尺寸}
- 载荷工况: {如有}
- 其他约束: {如空间限制、重量限制等}

【交付要求】
- 输出文件: {必须生成的文件}
- 验证标准: {必须通过的验证项}
- 输出格式: {JSON摘要 + 文件路径}

### 4.2 小屋必须完成的工作
每个小屋在其独立历史中完成：
1. **设计**：根据输入进行三维建模（调用 DSH_SW）
2. **验证**：
   - 几何门禁检查（geometry_gate.py）
   - FEA 物理验证（physics_bridge.py）
   - 领域规则检查（refine_rules.py）
   - 三大功能运行区按上述规则自动触发
3. **输出**：
   - 零件文件（.sldprt）
   - 压缩结果摘要（JSON 格式）
   - 若有 FAIL，必须填写 errors 字段

### 4.3 小屋输出格式
{
  "room": "小屋N名称",
  "status": "PASS",
  "design_summary": "设计要点简述（<=200字）",
  "files": {
    "part_path": "C:\...\DSH_xxx.sldprt",
    "drawing_path": "C:\...\DSH_xxx.SLDDRW"
  },
  "verification": {
    "safety_factor": 3.2,
    "interference": false,
    "notes": "验证通过，无干涉"
  },
  "interface_requirements": [
    "下一屋必须保持安装孔中心距 120mm",
    "下一屋必须匹配轴径 d=25mm"
  ],
  "errors": null
}

## 五、小屋配置模板（示例 · 非固定表，实际以编制为准）

> 🔴 **【2026-10-28】** 下面只是**常见示例**，**不是默认编制**。
> 实际房间表来自总计子代理的 `set-subagent-plan`（数量/细分由它决定），
> 可能与下面的 5 个房间完全不同 —— **禁止把本节当成固定房间表来开局**。

### 小屋1：结构件设计（示例）
- **输入**：用户需求 -> 系统分解 -> 载荷推断
- **工作**：设计承力结构零件（支架、梁、板等）
- **验证**：FEA 强度校核（安全系数 >= 2）+ 领域规则检查（structural_rules）
- **输出接口**：安装面位置、孔径、关键尺寸

### 小屋2：传动机构设计
- **输入**：用户需求 + 小屋1的结构接口
- **工作**：设计轴、齿轮、轴承、连杆等传动零件
- **验证**：强度校核 + 运动学间隙检查 + 领域规则检查（transmission_rules）
- **输出接口**：轴径、中心距、齿轮参数

### 小屋3：壳体/机架设计
- **输入**：用户需求 + 小屋1+2 的接口尺寸
- **工作**：设计外壳、机架、安装基板
- **验证**：刚度校核 + 干涉预检查 + 领域规则检查（housing_rules）
- **输出接口**：安装孔位、容纳空间

### 小屋4：总装与验证
- **输入**：用户需求 + 小屋1+2+3 的所有零件
- **工作**：
  1. 在 SolidWorks 中组装所有零件
  2. 全量干涉检查（几何拓扑验证）
  3. 运动模拟（如有运动副）
  4. 设计规则检查（GB/T）
  5. 物理在环验证（整体 FEA）
- **输出**：最终装配体（.sldasm）+ 验证报告

### 小屋5：工程图输出
- **输入**：小屋4 的最终装配体
- **工作**：
  1. 生成总装工程图（三视图 + 剖视 + 明细栏）
  2. 各零件工程图
  3. GB/T 制图规范检查
  4. CADX 几何验证
  5. SW->DWG/PDF 导出
- **输出**：SLDDRW + DWG + PDF

## 六、进度标注规范

大屋必须在每个关键节点用 [进度: X%] 标注（下表房间名是示例，按实际编制替换）：

| 阶段 | 进度 | 标注示例 |
|------|------|---------|
| 系统分解完成 | 10% | [进度: 10%] 系统分解完成，识别到3个设计域 |
| 小屋1启动 | 20% | [进度: 20%] 小屋1（结构件）已启动 |
| 小屋1完成 | 30% | [进度: 30%] 小屋1完成，安全系数 3.2，传递中... |
| 小屋2启动 | 35% | [进度: 35%] 小屋2（传动）已启动 |
| 小屋2完成 | 50% | [进度: 50%] 小屋2完成，轴径 d=25mm 已确认 |
| 小屋3启动 | 55% | [进度: 55%] 小屋3（壳体）已启动 |
| 小屋3完成 | 70% | [进度: 70%] 小屋3完成，安装孔位已确定 |
| 小屋4启动 | 75% | [进度: 75%] 小屋4（总装）已启动 |
| 小屋4完成 | 90% | [进度: 90%] 小屋4完成，无干涉，干涉检查通过 |
| 小屋5启动 | 92% | [进度: 92%] 小屋5（出图）已启动 |
| 全部完成 | 100% | [进度: 100%] 全部完成，汇总交付 |

## 七、并行优化策略（三波式，代码级强制）

并行模式由 workflow_gate.py select 按波次放行，AI 无法跳波。
**房间表的唯一来源 = 总计子代理的编制**（`mode2_flow.py set-subagent-plan`），
**代码默认表不得作为兜底**。

| 波次 | 名称 | 房间 | 启动方式 | 前置条件 |
|------|------|------|---------|---------|
| wave1 | 建模爆发期 | **select 返回的编制全部建模房间 + 总理子代理**（数量由编制决定） | **同时启动**（一条回复连续 fork 全部） | 无 |
| wave2 | 收敛汇合期 | 总装与验证 | 单独创建 | wave1 全部 room-end |
| wave3 | 出图期 | 工程图输出 | 单独创建 | 总装 room-end |

> 🔴 旧版「第1波 = 结构件+传动机构+壳体机架 三个房间」的固定表
> **已废弃（deprecated）**：房间数与细分由总计子代理决定，可能是 2 个，也可能是 7 个。

依赖原理：
- wave1 各建模房间彼此独立设计，互不等待（SW 调用由互斥锁自动排队，排队中即 `waiting_sw`）
- **总理子代理与 wave1 同批启动**，长生命周期持续盯 `room-board`，但**不计入 wave 完成判定**
- wave2 总装需要 wave1 的全部零件文件 → 必须等 wave1 全部 room-end
- wave3 出图需要总装体 → 必须等 wave2 完成

报错回退（wave2 失败时）：
1. 大屋分析总装错误，定位出错源房间（房间名以**编制**为准）
2. 调用 `python "<工程模式根目录>\tools\mode_gate.py" room-fail <源房间名>` —— 该房间回到待处理状态
3. 重新调用 select → 返回 wave1（只含失败房间）→ 重做
4. 重做完成后再次 select → 重新进入 wave2

SW 使用窗口（多屋并行时）：
- 多个建模房间并行时，某个房间正在用 SW，其他房间到达 SW 窗口时自动停下等待
- 期间 `room-board` 显示 `waiting_sw` —— **这是正常等待，不是死亡，禁止 reset**
- 用完释放后，按 FIFO 顺序：谁先暂停排队谁先用
- 每次占用有 4 小时防死锁上限（`SW_MAX_HOLD`，超时自动接管）

## 八、典型执行示例

用户："帮我设计一个减速箱"

大屋执行流程（房间示例：结构件 / 传动机构 / 壳体机架 —— **由编制决定，非固定**）：
1. [进度: 10%] init → 问两问 → provide_context #1/#2（模式、强度）
2. [进度: 15%] **spawn 总计子代理**（analyst-bind → 十阶段 → 按需提问 → set-subagent-plan）
   主对话只等它结束（`wait_agent` / `list_agents`）
3. [进度: 20%] provide_context #3（力学估算）→ 问 A/B/C 与 D/E → select
   → wave1 = 编制全部建模房间 + 总理子代理
4. [进度: 25%] wave1：各房间 room-start → **一条回复一次性 fork 全部**
   → 每个 fork 后立刻 `subagent-assign <房间> <id>`
   → 总理子代理同批启动，循环读 `room-board --json`（`waiting_sw` 不算死）
5. [进度: 60%] wave1 各房间建模 → FEA → 领域规则 → 压缩摘要 → room-end
   （SW 排队时 `waiting_sw`，正常等待，**禁止判死/reset**）
6. [进度: 75%] wave2：总装与验证（等 wave1 全部 room-end）→ 干涉检查 + 整体 FEA
7. [进度: 92%] wave3：工程图输出 → GB/T 检查 → CADX 验证
8. [进度: 100%] 汇总交付：`room-end 总理子代理` → 文件清单 + 验证报告