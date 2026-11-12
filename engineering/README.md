# DSH 工程模式预设（Engineering Mode Preset）

面向 CAD / SolidWorks 机械设计的 DeepSeek Harness 工程模式预设。
以**完成工图**为唯一核心目标，执行严格的零件设计流程：分析 → 设计 → 验证。

---

## 架构（很重要，别再踩坑）

DSH 的插件分三个平面，**放错位置会互相连坐**：

| 平面 | 位置 | 内容 | 生效方式 |
|------|------|------|----------|
| **宿主层**（进程级） | `profiles/web/cordis.patch.yml` | 权限预设表、全局服务 | **必须重启 DSH** |
| **preset 层**（会话子作用域） | `.agent-presets/<id>/agent.cordis.yml` | persona、工具、技能 | 热生效 |
| **profile bundle** | `profiles/web/package.json` 的 `dependencies` + `dsh.profile.bundles` | UI 插件（客户端半） | **必须重启 DSH** |

> ⛔ **绝对不要把 `- id: permission` 写进 `agent.cordis.yml`！**
> `@deepseek-ai/dsh-permission-presets` 是宿主层进程级服务，dsh-base bundle 已在宿主组合里注册过。
> preset 是会话下的子作用域，再声明一次 = 重复注册同名服务，loader 直接拒绝：
>
> ```
> failed to apply loader entry permission (@deepseek-ai/dsh-permission-presets):
> service "permissionPresets" has been registered
> ```
>
> 后果是**新建会话 / 切预设 / 选模型三件事一起坏**，看起来像"整个 DSH 挂了"。

---

## 安装

```powershell
cd <preset目录>
.\install-plugin.ps1
```

脚本会自动：
1. 【问题2 修复】权限预设只保留 DSH 标准三档，**不再注入 SW单行模式**
2. 把 `plugins/dsh-engineering-ui` 复制进 profile 的 `node_modules`
3. 把插件登记进 profile 的 `package.json`（`dependencies` + `dsh.profile.bundles`）

> ⚠️ 脚本依赖 **`<preset目录>/plugins/`** 下的插件源码。
> 若该目录缺失，安装脚本会直接失败（报找不到源路径）——请确保插件源码随预设一起分发。

然后**重启 DSH Web**。

---

## Webots 仿真接入（2026-11-05，可选 / 默认关闭）

外部交付补丁 `f04ac75a` 已并入本仓库，改为**挂在工程模式主流程上**而不是独立跑。

### 改动落点（六处）

| 文件 | 改了什么 |
|---|---|
| `engineering/tools/webots_mcp/` | 新增（80 项之一）：MCP server、转换器、证据读取、物理门、修复试验 |
| `engineering/docs/webots-mcp/` | 新增 14 份契约文档（原文照收，作证据口径） |
| `engineering/skills/webots-simulation/` | 新增技能，并在末尾补「与主流程的接口」 |
| `engineering/tools/webots_env.py` | 新增：可用性自检（只读，零副作用） |
| `lib/index.js` + `lib/webots.js` | 装配只读宿主门：4 条 GET 路由 + `ctx.tools.guard` 归属校验 |
| `preset-engineering.patch.yml` | 新增 `mcp-webots` 行（默认 `disabled`）+ persona 的「## 仿真」段 |
| `sync-to-dsh.ps1` / `verify_sync.py` | 新增运行现场排除项（`webots_state.json` / 作业账本 / 锁 / `webots_runs/`） |

### 与既有契约的接口

1. **身份**：`resolveWebotsContext()` 从 `workflow_state.json.task_id` +
   `mode_state.json` 里唯一 `active` 且 `subagents[房间].id == agent.id` 的房间推导；
   `room_id = "room-" + sha256(房间名 UTF-8)`。模型自报一律不采信。
2. **SW 锁**：仿真链**不**调 `sw_bridge` 的建模命令，因此不进 `mode_gate` 的 FIFO；
   新增的 `sim-*` 命令归入 `_LOCAL_CMDS`（`_NO_SW_CMDS`），实测不会把 SW 拉起来。
3. **防线**：仿真**不能**替代材料/物理/领域三大防线凭据；`room-end` 的校验逻辑未改。
4. **状态目录**：沿用 `_store.state_dir()`，不另造一套；`webots_state.json` 等按运行时数据排除同步。

### 已验证 / 未验证（2026-11-05 本机实测）

- ✅ 离线管线：`publish-part → convert-part → host report` 跑通，产出 URDF + PROTO + 两份报告
- ✅ `webots_env.py` 32 项断言、插件 15 个 `.mjs`（含 webots-host / webots-preset）全绿
- ✅ 插件路由 33 条（原 29 + 4 条 Webots 只读）
- ❌ 真实 DSH 装配下的 MCP 启动、真实 Webots 作业、原生 SW→URDF、物理 PASS —— **均未验证**
- ✅ Python 侧 101 项全绿（含 `test_a04_transport`；原先 Windows 上
  `WinError 32` 的临时目录清理失败已修：`Transport.close()` 现在等
  `writer.wait_closed()`，测试夹具也改成可重试清理）

> 完整验收门槛（G1–G5）见 `engineering/docs/webots-mcp/a04-runtime.md` 与接入手册。
> **不要**把"装好了"当成"仿真通过"。

---

## 权限模式（重要）

### 默认权限 = Full access（不要被锁住）

宿主层 `cordis.patch.yml` **必须显式声明 `defaultPreset`**：

```yaml
- id: permission
  name: "@deepseek-ai/dsh-permission-presets"
  config:
    defaultPreset: danger-full-access     # ← 与设置页"默认权限模式"保持一致
    presets:
      read-only:          { sandbox: read-only,          approval: ask   }
      workspace-write:    { sandbox: workspace-write,    approval: ask   }
      danger-full-access: { sandbox: danger-full-access, approval: never, name: Full access }
```

**为什么必须写 `defaultPreset`**：权限服务的行为是

```js
const inferredDefault = this.derive(EMPTY_KNOBS);
const defaultPreset = config.defaultPreset ?? inferredDefault;
```

patch 是**整体替换该行 config**，一旦覆盖了 `presets` 表又没给 `defaultPreset`，
服务只能靠推导——推不出来时 UI 默认值会落到表内任意项，
表现为"设置了 Full access，新会话却被锁在某个非预期权限档"。

### 标准三档权限（问题2 修复后）

| 预设 | sandbox | approval | 什么时候用 |
|------|---------|----------|-----------|
| **Full access** | `danger-full-access` | `never` | 默认；完全访问，不弹审批 |
| `workspace-write` | `workspace-write` | `ask` | 只在工作区内写，越界要审批 |
| `read-only` | `read-only` | `ask` | 只读 |

> 【问题2 修复】原「SW单行模式」权限档、配套插件
> `dsH-engineering-sw-single-line` 及非工程模式警告横幅已**整体移除**。
> 工程模式现在直接使用 DSH 标准权限档，不再自动切换自定义预设。

**修改 preset 表必须重启 DSH**（进程级配置，不热生效）。

---

## 工作流门禁（Mode 2 · 大型复杂器械装配）

> ⚠️ **【BUG-03/04 修复】真实入口是命令行脚本，不是 DSH 工具**
>
> 历史文档曾写 `workflow-gate-init` / `workflow-gate-provide-context` /
> `workflow-gate-select` 这些"DSH 工具名"，但**本环境并未注册这些工具**，
> 照文档调用必然失败。真实入口是 `tools/workflow_gate.py` 的命令行：
>
> ```powershell
> $WG = "<工程模式根目录>\tools\workflow_gate.py"
> python $WG init "<任务描述>"              # 第 1 步：返回第 0 题
> python $WG provide_context "<回答原文>"   # 第 2 步：第一次 → 第二段问题
> python $WG provide_context "<回答原文>"   #          第二次 → 力学估算
> python $WG select C,E                    # 第 3 步：搭建方式+并行策略
> python $WG confirm-assembly "<零件清单>"  # 总装前确认
> python $WG status / recover / reset
> ```
>
> `select` 接受多种写法（**BUG-03 修复**，不再只有单一格式）：
> `select C E` / `select C,E` / `select "C, E"` / `select c e` /
> `select --choice C --parallel E`

### 三步门禁（命令行脚本）

1. `workflow_gate.py init "<任务>"` — 返回**第 0 题：参数需求强度**，必须原样问给用户
2. `workflow_gate.py provide_context "<回答>"` — **需调用两次**：
   第一次判深度并返回第二段问题；第二次做力学估算，
   返回 A/B/C 搭建方式 + D/E 并行策略
3. `workflow_gate.py select C,E` — 用户选定后生成子代理房间配置

### 数值口径一致性（BUG-01 / 07 修复）

`init → provide_context` 阶段的力学估算现在：

| 项目 | 修复前 | 修复后 |
|------|--------|--------|
| 额定载荷 | 只看关键词，**忽略用户写的数值**；500N 起算 | **优先读取用户明确给出的数值**（`100N`/`10kg`/`0.1kN`…） |
| 冲击关键词 | 直接把载荷 ×2（100N → 1000N，放大 10 倍） | 只作用于**动载系数**，不改用户给的额定值 |
| 与 physics 口径 | 门禁一套数、physics 另一套数（BUG-07） | 门禁**直接落盘载荷工况**，physics 直接读该文件 |

落盘文件：`tools/load_cases/gate_load_case.json`，其中
`magnitude_n = nominal_load_n`（额定载荷）；设计校核另乘 `impact_factor`。
另含 `acceptance.design_life_years` / `operating_cycles_per_year`，
供 physics 疲劳模块做 S-N + Miner 累积损伤校核。

> 若用户未给出载荷数值，返回体会带 `load_is_estimate=true` 与醒目提示 ——
> **必须向用户复核**，不得拿估算值当设计基准。

### 疲劳 / 设计寿命校核（BUG-02 修复）

修复前整条工具链只有**线性静力**，没有疲劳与寿命校核。现在：

```powershell
# 疲劳校核（默认按 30 年设计寿命）
python sw_bridge.py physics-fatigue <case.json>
python sw_bridge.py physics-fatigue --report <run_id>
python physics_bridge.py fatigue <case.json> --stress 45   # 直接给应力
```

- 方法：Basquin S-N + Marin 修正（ka/kb/kc/kd/ke）+ Goodman/Soderberg/Gerber
  平均应力修正 + **Miner 线性累积损伤**
- 每次 `solve_fea` 都会**自动追加**疲劳校核，结论并入 `gates` 与 `overall`；
  静力过了但寿命不够的零件会被判 `FAIL`，不再误判为 PASS
- 铝合金按无真实疲劳极限处理（5×10⁸ 次条件极限）

### 材料健全性（BUG-05 / 08 修复）

- `swapi.new_part()` **默认赋 Q235 碳钢**（7850 kg/m³），
  杜绝"密度恒为 1000（水）"；可用 `new_part(material="6061-T6")` 覆盖
- physics 报告在材料缺失/密度=1000 时给出**显式告警**并阻断疲劳结论，
  不再显示 `?` 让人误以为正常
- SW 导出 DXF 后**自动整理图层名**：中文图层（"可见边线"/"尺寸"…）
  映射为 CADX 标准层 `OUTLINE/THIN/CENTER/HIDDEN/DIM/TEXT/HATCH`，
  并补齐缺失的标准层 —— 消除每次出图的一堆 MISSING_LAYER WARNING

### 统一问题集（17 题 · 单一事实来源）

> ⚠️ **旧版"固定 17 题题库（QUESTION_SPEC）"已删除**（2026-10 改造）。
> 现在没有"标准题面"可照抄 —— 想提问就必须先分析。

**题数硬约束 + 按需提问**（定义在 `mode2_flow.py`，代码强制）：

| 参数需求强度 | 允许题数（闭区间） |
|---|---|
| 强需求 `full` | 10 ~ 50 |
| 弱需求 `lite` | 5 ~ 10 |

- 每道题必须挂在**已 `begin-stage` 的十阶段之一**上，并写明 `--reason`
  （= "分析到那里才问"的机器判据），否则拒绝登记；
- 题数由 `validate_question_budget` 校验，超界/收尾不足都拒绝交付；
- 用户答复必须逐条落盘 `answer-question`，且会让受影响阶段的产物变为
  `stale` —— 必须 `refresh-stage` 重做并提交**新产物**，产物写回旧文本会被拒绝。

### 波次调度（房间数量由总计子代理决定）

- **前置**：`provide_context` 第 2 段后 → 主对话 `subagent_fork` 创建
  **【总计子代理】** → 它 `analyst-bind`、跑完十阶段、按需提问、
  `set-subagent-plan` 登记编制；未完成时 `select` 直接返回
  `MODE2_ANALYST_REQUIRED`。
- **第 1 波·独立爆发期**：编制里的**全部建模房间**同时启动
  （每个 fork 后立刻 `subagent-assign`）。**没有总理子代理** —— 零件建成由监控器
  按 `part-saved` 事件判定，逐件验收走 `review-part`。
- **第 2 波·收敛汇合期**：全部建模房间 `done` 后 → 总装确认门禁 → 总装小屋
  （总装前核对由监控器 `verify-task` 一次性完成：台账 + 邻接尺寸 + 未决冲突）。
- **第 3 波·出图期**：总装完成后 → 工程图小屋；导出 docx、房间 `room-end` 后，
  监控器 `verify-task` 通过，门禁才允许归档 `finished`。

---

## PowerShell 调用约定（【BUG-10/11】中文化与退出码噪音）

工程模式的所有门禁脚本都会输出**中文 JSON**。在 Windows PowerShell 5.1 里
直接用管道（`| Select-String`、`| Select-Object -First N`）处理时有两个坑，
都在实测中真实踩到过，写在此处固定下来：

### 1. 必须先设 UTF-8，否则中文变乱码/丢行

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding          = [System.Text.Encoding]::UTF8
```

如果不设，PS 5.1 会按当前代码页（通常是 GBK/936）解码脚本输出的 UTF-8 字节，
表现为房间名、阶段名变成 `�����복���ܳ�` 这样的乱码，主对话据此误判房间状态。
脚本侧已做双保险（`sys.stdout.reconfigure(encoding='utf-8')` + 失败时退回
`ensure_ascii=True`），但**在 PS 一侧设置仍然是正确做法**。

### 2. `[exit code: 1]` 多数是管道截断，不是命令失败

```powershell
python mode_gate.py room-board --json | Select-Object -First 20   # 可能 exit 1
```

原因：`Select-Object -First` 取够 N 行后会主动关闭上游管道，Python 侧写入
随即收到 broken pipe → 以非 0 退出。**这表示"输出被截断了"，不表示门禁失败。**

判据：
- 命令**本身**报错 → JSON 里会有 `"ok": false` + `"error"` 字段；
- 只是想少看点输出 → 用 JSON 里的字段筛选，或干脆跑完整命令后再看。

### 3. 推荐：别截断，直接读结构化字段

门禁脚本输出的 JSON 都很短，且关键结论都在顶层字段里：

```powershell
python mode_gate.py room-board --json        # 一次拿全，别 Select-Object
python mode_gate.py wait-rooms "<房间>" --timeout 900   # 阻塞等待，零轮询
```

> 【BUG-03 修复】父代理**不要**再用 `Start-Sleep` + 反复读盘的方式等房间。
> fork 出来的子代理不是 Team member（`wait_agent` / `list_agents` 看不到它们），
> 正确做法是 `mode_gate.py wait-rooms` 原地阻塞，或用
> `room-report-read <房间>` 旁路读取进度。

---

## 子代理（小屋）机制

### 身份自查（开工第一步）

DSH 平台限制**子代理无权向用户提问**（`userQuestions.ask()` 校验 `agents.roots()`，
子代理必抛 `DELEGATED_CALLER`）。而提问需要知道自己的 sessionId，
所以小屋**开工第一件事**必须自查身份：

```powershell
python "<工程模式根目录>\tools\mode_gate.py" whoami "<房间名>"
```

返回的 `subagent_id` 即后续 `ask_user.py` 的 `--child` 参数。

### 提问通道（阻塞式，不结束回合）

```powershell
python "<工程模式根目录>\tools\ask_user.py" --room "<房间名>" --child "<sessionId>" `
       --header "零件确认" --question "以下参数将用于建模<零件名>，要用吗？" `
       --option "确认，开始建模" --option "需要修改"
```

脚本会：登记问题到本小屋 → 选项卡片出现在**右侧子代理面板** →
**原地阻塞轮询**直到用户作答 → 打印 JSON 到 stdout 并退出。

小屋读到答案即视为"用户已确认"，据此继续建模——**全程不结束回合**。

### C 模式逐零件确认（代码级强制）

每个零件必须**单独**提问、**单独**得到用户答复，**绝不允许沿用上一个零件的确认**。

代码强制：`params_confirmed` 需要**用户授权令牌**——用户答复后由主对话执行

```powershell
python mode_gate.py confirm-part <房间名> <零件名>
```

写入授权。未授权的上报会被拒绝并记入 `violations.json`。

### 进度上报（旁路，不打断小屋）

```powershell
python mode_gate.py room-report <房间名> <阶段> <详情>
```

阶段顺序：`registered` → `params_shown` → `params_confirmed` → `modeling`
→ `part_done` → `validating` → `done` / `failed`

> ⚠️ `registered` 是**硬性要求**且必须最先上报。
> 否则主对话侧永远看到 `report=null`，会误判小屋从未启动。
> 任何卡点必须先 `room-report failed '<原因>'` 再结束，**禁止静默停摆**。

---

## SW 使用权（外部监控器，进程内锁已删除）

> **2026-11-12 起，工程模式不再自己维护 SW 锁。** 这一节记录删除了什么、为什么、
> 以及现在由谁负责。改动前请先读，不要"顺手加回来一个 owner 字段"。

### 删掉了什么

| 旧实现 | 行数 | 删除原因 |
|---|---|---|
| `sw-request` / `sw-wait` / `sw-release` / `lock-doctor` 命令 | ~470 | 排队、等待、体检全部由监控器的票据状态机负责 |
| `_lock_owner_alive` / `_purge_orphan_lock` / `_reclaim_lock` | ~290 | 用心跳/产物 mtime/prev_owner 三层"猜"持锁者死活 |
| `SW_FAIR_CMDS` / `SW_FAIR_SECONDS` / `SW_MAX_HOLD` / `SW_CMD_HOLD` / `SW_STALE_KILL` | 常量 | 公平轮转与超时接管在票据模型下不存在 |
| `_monitor_bg_impl` 常驻进程 + `sw_monitor.pid` | ~120 | 只记录 SLDWORKS.EXE 在不在；实测跨任务泄漏 4 个进程 |

### 两个必须记住的现场事故

**① 「SW 进程没在跑」被当成「锁空闲」（2026-11-11）**

`can_fast` 当时写的是 `(not running) or (not sw_by_room)`。房间刚取到锁、
SW 还没冷启动时 `running=False`，于是第二个房间直接 `acquired=True`。
room-board 自报 `hold_mode=room`，实际 **8 对构建区间重叠**，最长 216 秒。
→ 现在的判据里**没有任何进程探活**：授权只看监控器的票据。

**② 「等锁」必须占着回合（图一）**

忙时只能返回 `sw_busy`，让子代理 sleep 后重试同一条命令。主对话于是挂在
`wait-rooms --timeout 900` 上一动不动，退出即被收尾守卫判"没干活"。
→ 现在排队时命令立刻返回 **`job_id`**，等待发生在 DSH 后台任务里。

### 现在谁负责

| 关注点 | 入口 | 位置 |
|---|---|---|
| 命令许可 | `sw_bridge._gate_sw_lock` → `sw_monitor_client.permit` | `sw_bridge.py` |
| 宿主侧票据/租约 | `MonitorBridge.acquire` | `lib/sw-monitor.js` |
| 许可路由 | `POST /dsh-engineering-ui/sw-monitor-permit` | `lib/index.js` |
| 运维（接管/恢复/查看） | `mode_gate.py sw-monitor {status,attach,recover,plan}` | `mode_gate.py` |
| 逐零件看板 | `mode2_flow.py parts-board` → `rpc('board')` | `mode2_flow.py` |
| 交付总核实 | `pre_delivery_check` → `rpc('verify-task')` | `mode2_flow.py` |

### 代码约束（改之前先看）

1. **不要在 `mode_gate.py` 里再写 owner/queue 判断。**
   `mode_state.sw_lock` 现在是**只读镜像**（`_refresh_sw_lock_mirror`
   从监控器 board 单向同步，带 `source="sw_monitor"`）。任何"写 owner"
   的代码都会造成双权威，而双权威正是这次改造要根除的病。
2. **不要给 `sw_bridge` 加"跳过取锁"的开关。**
   旧代码里的 `DSH_SW_LOCK_OFF` 已删除 —— 它的语义是"绕过唯一权威"。
3. **`_SW_GUARD_PID_FILE` / `SW_GUARD_*` 属于另一个组件**（SW 健康看护，
   CPU/内存 > 85% 立即关 SW），不要跟着"旧监控"一起删。清理时踩过这个坑。
4. **排队返回 `job_id` 后不要 sleep 轮询**：等待在后台任务里，
   模型该做的是 `job_output` 等它自然结束，再重试同一条命令。

## 文件结构

```
engineering/
├── agent.cordis.yml           # preset 组合（persona + 工具 + 技能），不含宿主层服务
├── preset.yml                 # 显示元数据
├── install-plugin.ps1         # 一键安装（宿主层 patch + 插件注册）
├── README.md
├── 常见SW启动失败问题.md        # SW 启动排查手册
├── skills/                    # 技能（8 个）
│   ├── mode-selection/        # 强制入口：模式选择 + 门禁
│   ├── assembly-orchestration/# 总装编排
│   ├── cad-workflow/          # CAD 工作流
│   ├── sw-design/             # SW 设计（含 GB/T 制图、AutoCAD 参考）
│   ├── solidworks-bridge/     # SW 桥接
│   ├── sw-to-cad/             # SW → CAD 转换
│   └── physics-in-loop/       # 物理在环仿真
├── tools/                     # Python 工具
│   ├── workflow_gate.py       # 门禁大脑：init → provide-context → select → 收尾
│   ├── mode_gate.py           # 房间状态机 + SW 锁 + 心跳 + whoami/confirm-part
│   ├── ask_user.py            # 子代理阻塞式提问 CLI
│   ├── sw_bridge.py           # SolidWorks 桥接执行器
│   ├── swapi.py               # 高层建模 API
│   ├── ac_bridge.py           # AutoCAD 桥接
│   ├── ac_validate.py         # DXF 几何验证
│   ├── settings.py            # 【换机修复】本机设置（交付目录/软件路径，带机器指纹）
│   ├── env_check.py           # 【换机修复】环境自检 + 给新对话的修复提示词
│   ├── physics/               # 物理仿真（GB/T 规则库）
│   ├── sw_monitor/            # ★ 外部监控器（SW 使用权唯一权威）+ docs/
│   ├── sw_monitor_client.py   # ★ Python 侧接入点（permit/done/board）
│   └── examples/              # 建模样例
└── plugins/
    └── dsh-engineering-ui/            # 三栏 UI + 提问卡片 + 守卫 + 提问记账
```

---

## 子代理三栏工作区

- **主对话 3/4**：检测到子代理时自动把主对话压到 3/4 宽
- **中间竖排频道列表**：每个子代理一个频道按钮，绿点 = 运行中，点击切换右侧屏幕
- **右侧 1/4 工作区**：所选子代理的实时输出、工具调用/参数、工具结果、运行状态，
  以及**可直接作答的提问卡片**
- **无子代理时全部隐藏**，回到全宽正常聊天

---

## 已修复的问题

| # | 问题 | 修复 |
|---|------|------|
| 1 | 主对话误判子代理状态 | `mode_gate.py` 六态分类 + 旁路命令 |
| 2 | 总装未等全部零件完成就启动 | `confirm-assembly` 门禁 |
| 3 | C模式无代码级强制 | `CONFIRMATION_PROTOCOL_C` 逐零件循环 |
| 4 | 任务完成后循环交代 | `_archive_finished()` 状态机收敛 |
| 5 | 跨任务状态污染 | `_reset_mode_rooms()` 仅首次 select 清理 |
| 6 | `send_message` 插队打断小屋 | persona 禁止插队铁律 |
| 7 | 等待 SW 关闭时被误判 stale | `_finalize_room` 等待期间持续写心跳 |
| 8 | preset 挂载失败导致三件事一起坏 | 移除 preset 内的宿主层服务声明 |
| 9 | **子代理越权确认**（沿用上个零件的确认） | `confirm-part` 授权令牌 + 越权留痕 |
| 10 | **SW 锁饥饿**（单房间独占致队列饿死） | 公平轮转（命令数 + 独占时长双判据） |
| 11 | **C+D 被强制降级为 E** | 移除 `conflict_warning` 自动改写逻辑 |
| 12 | **`entry.resolve is not a function`** | `entry` 持有真正的 resolve + 桥接 `ask()` |
| 13 | **权限默认被自定义预设锁死** | 【问题2】移除自定义档，只留标准三档 + 显式 `defaultPreset` |
| 14 | **问题集不一致**（有时5问有时10问） | `QUESTION_SPEC` 单一事实来源，统一 17 题 |
| 15 | **子代理不知自己 sessionId 而卡死** | 新增 `whoami` 身份自查命令 |
| 16 | **report 始终为 null** | `registered` 改为开工必做第一件事 |
| 17 | **规则自相矛盾**（禁止调 sw_bridge vs 须 close-all） | 明确 `close-all` 为唯一例外 |
| 18 | **skill 文档路径约定错误**（假设脚本在 cwd） | 全部改为绝对路径占位符 |
| 19 | **设计依据薄弱**（尺寸与载荷不符、公差不规范） | 参数表强制量化依据 + 标准配合代号 |
| 20 | **前端"伪交互"与真实通信脱节** | 提问经 `/ask-child` 绑定 pendingId → tool_result |
| 21 | **桌面凭空多出 `test` 文件夹**（换台电脑同样发生） | 交付目录改由 `settings.py` 决定；桌面 `test` 仅当**已存在**时沿用 |
| 22 | **连接区沿用上一台电脑的 Z 盘路径** | 设置与 `connection_state.paths` 均带机器指纹，跨机自动忽略并重探 |
| 23 | **缺依赖时「设置静默失效」**（python 3.5 → SyntaxError） | 新增 `env_check.py` 自检 + `sw_bridge.py` 版本闸门 + 插件 `resolvePython` 版本校验 |
| 24 | **提醒弹窗不出现** | 失败可重试（成功才封顶）+ 设置页「立即查看提醒」确定性入口 |
| 25 | 设置页中文乱码（Python 子进程按 GBK 输出） | 所有 Python 调用注入 `PYTHONIOENCODING=utf-8` / `PYTHONUTF8=1` |
| A1-① | **提示词自相矛盾**："宁可多轮短等"被照抄成单轮 477 次 `sleep(30)` / 4h02m 轮询（占墙钟 66%） | persona「长等待·铁律」重写：禁止 sleep 轮询，只准阻塞原语（2026-11-12 起等 SW 改成等 `job_id`）；无原语时同目标短 sleep ≤3 次；补 `[WAITING]` 结束回合边界 |
| A6 | **工具输出 U+FFFD 乱码 180 条**，模型据乱码脑补出错误设计前提（"差速+舵机转向"） | persona「工具输出编码·铁律」：每条 python 调用强制 UTF-8 前置模板；出现 `\uFFFD`/"�" 禁下结论，必须重跑 + read 原文核对（注入式根治在 `lib/index.js`） |
| P1-①② | **坐标系与方向语义没写死**：Top Plane 镜像、`cut()` 方向、"上方向"无声明 | `skills/sw-design` 新增「〇、开工前必读」操作手册（ZSIGN/`flip=True`/`<零件>.SLDPRT.pr.json`/零旋转装配报警），persona 同步压缩版 |
| C1 | **三套"上"方向**（零件 +Z / 总装 +Y / 图纸 +Z）→ 整机错位 90° | `skills/assembly-orchestration` 新增「二点五、『上』方向契约」：任务级 `up_contract.json` 单点声明、三波共享、六条机器判据 J1~J6 |
| A12 | 任务书 `rooms` 示例缺 `type`，子代理照抄必被拒 | `rooms-template` 子命令（代码侧）+ `mode-selection` §3.5.4 写入含 `type` 的完整示例与字段表 |
| **P2** | **停止令下达后子代理仍在下一条命令**（回溯报告 §7 P2：interrupt 只是"请求"，建模进程照跑） | 新增 `tools/_stop.py`（`STOP_ALL` 文件 + `DSH_KILL_SWITCH` 环境变量）；`sw_bridge` / `mode_gate` / `workflow_gate` / `physics_bridge` / `visual_check` 五个入口在**任何副作用之前** guard，命中即 `TASK_STOPPED`（退出码 4），只放行 status/room-board/close-all/sw-kill 等只读清理类；`mode_gate.py stop-all` / `resume` |
| **P3** | **同一零件 4 版脚本反复重建**（回溯报告 §7 P3：无已完成零件台账查重） | `sw_bridge.run` 执行前按脚本声明的产物名查 `part_registry`：已建好且尺寸一致 → `PART_ALREADY_BUILT` 并跳过重建（`--force-rebuild` 可覆盖）；台账缺 `dims` → `PART_REUSE_UNKNOWN`，**unknown 绝不当作已建好** |
| **P5 / M1** | **没有齿的"齿轮"被宣布建模成功**（回溯报告 §7 P5：大齿轮=轮廓 revolve、小齿轮轴=φda 圆柱段） | `swapi.SWModel.gear()` 新增 `_verify_gear_geometry`：回读**齿顶圆**（≈ m·z+2m，偏差 ≤0.05 mm）与**实体体积**（≈ 齿廓解析面积×齿宽，偏差 ≤2%），任一不符 → `ok=false` |
| **D2** | 可见窗口 + 人工操作打断 COM（回溯报告 §12.2） | `swapi` 新增可选无头模式 `DSH_SW_VISIBLE=0`，按官方配方 `UserControl=False → Visible=False → Frame.KeepInvisible=True`；**恒不改文档级 Visible**（`SelectByID2` 要求 "open and visible document"，且官方警告 `IModelDoc2.Visible=false` 会导致资源耗尽） |
| **D3** | **`ActiveDoc` 被当作文档身份** → 多房间串写、未命名零件凭空消失（回溯报告 §12.1） | 新增 `doc_identity` / `same_document` / `find_open_document` / `from_path` / `get_model(expect=…, strict=)`，走官方显式句柄通路（`GetOpenDocumentByName` / `GetDocuments`）；`SWModel.close()` 在取不到标题时**拒绝调用** `CloseDoc("")`（官方语义：空串=关掉当前活动文档）；`sw_bridge.run` 前后比对文档快照，误关他人文档 → `CROSS_ROOM_DOC_CLOSED` |
| **D4** | **全仓无 COM 忙/被拒重试** → 用户点一下 SW 就导致小屋改脚本重跑（回溯报告 §12.2） | 新增 `swapi.com_retry`：只对 `RPC_E_CALL_REJECTED`(0x80010001) / `RPC_E_SERVERCALL_RETRYLATER`(0x8001010A) / `RPC_E_RETRY` 等**忙类** HRESULT 做指数退避（300 ms→5 s，≤5 次），业务错误原样抛出；`extrude()` / `cut()` 已内置。另加 `batch_mode()` 包装官方 `CommandInProgress`（跨进程批处理减刷新） |

---

## 校验

```powershell
# 检查"宿主层插件混入 preset"等架构违规（会把问题直接标红）
node C:\Users\j1877\.workbuddy\dsh_backup\check-dsh-presets.js engineering
```

### 自查命令

```powershell
$T = "<工程模式根目录>\tools"
python "$T\mode_gate.py" status          # 关卡状态
python "$T\mode_gate.py" room-status     # 房间六态
python "$T\mode_gate.py" whoami          # 身份对照表
python "$T\mode_gate.py" sw-monitor status  # 监控器接入状态/排队
python "$T\sw_bridge.py" doctor          # SW 环境自检
```

---

## 同步须知

本预设存在**三处副本**，改动后需保持一致：

| 位置 | 用途 |
|------|------|
| `.dsh/.agent-presets/engineering/` | 预设运行位置 |
| `.dsh/profiles/web/node_modules/` | 插件生效位置（真正加载的） |
| `Desktop/DSH-SW-and-CAD-main/engineering/` | 源码仓库 |

> **实测修正（2026-10-03）**：本机实际路径与上表略有出入，以实际为准：
> - 插件真正加载的位置是 `.dsh/profiles/**desktop**/node_modules/dsh-engineering-ui/`
>   （`profiles/web/` 下**不存在**该目录；`profiles/desktop/` 才是生效的那份）。
> - `.dsh/.agent-presets/engineering/` 只含 `tools/` + `README.md` +
>   `常见SW启动失败问题.md`，**没有** `agent.cordis.yml`、`plugins/`、`skills/`。
> - 因此**智能体提示词的实际加载源**是插件目录里的
>   `preset-engineering.patch.yml`（由 `package.json` 的 `dsh.bundle.patch` 声明），
>   而仓库根的 `agent.cordis.yml` 是**同内容的源码镜像**。
>   ⇒ 改提示词时**两处都要改**，否则重启后生效的仍是旧文案。
> - `skills/` 的生效位置是 `~/.dsh/skills/`（第三处，不在上表内），
>   当前与仓库 `engineering/skills/` 完全一致。

插件 host 半（`index.js`）与宿主层配置（`cordis.patch.yml`）是**进程级**，
改完**必须重启 DSH**；client 半（`client.js`）改动刷新页面即可。

> 运行时状态文件（`mode_state.json` / `workflow_state.json` / `sw_state.json`
> / `connection_state.json`）
> 是会话现场数据，**三处保持独立**，不要互相同步，否则会覆盖现场导致流程误判。

## 工程环境检查（设置页「工程环境检查」，在「连接区」上方）

> 这条栏目是为**换电脑**场景加的。用户在学校微机房实测到三件事：
> ① 桌面莫名其妙多了个 `test` 文件夹；② 连接区记着上一台电脑的 Z 盘路径；
> ③ 缺依赖时「某些设置静默失效」，界面上看不出少了什么。

### 它做什么

一次扫描 8~10 项，逐项给**绿/黄/红**三色 + 一行「怎么修」：

| 项 | 判据 |
|----|------|
| 工程模式核心脚本 | `tools/` 下 8 个关键 .py 是否齐全 |
| Python 解释器 | **用目标解释器起子进程跑探针**，≥ 3.6 才采纳 |
| pywin32 / numpy / ezdxf / shapely / Pillow | 由**同一个解释器**真 import |
| SolidWorks / AutoCAD | 注册表 → 环境变量 → 本机设置 → 全盘符扫描 |
| 交付目录 | 存在且**可写**（实地写一个探针文件再删） |
| 跨机残留设置 | 指纹不符的设置会被列出来并说明「已忽略」 |

### 「生成修复提示词」怎么用

点一下 → 后端 `tools/env_check.py prompt` 用**本机实测结果**现场生成一段提示词
（含缺失项、正确的解释器绝对路径、逐条 pip 命令、验收标准），
再点「发给新对话」→ 复用既有通道新建一个**工程模式**会话并把这段作为首轮发出，
新的 Agent 就照着清单自己去查、自己去装。

### 交付目录（不再写死桌面 test）

旧行为：只要用户桌面存在就返回 `桌面\test`，并在首次建任务时**创建它** ——
于是任何人第一次用工程模式，桌面都会多一个 `test`。

现在的优先级（`tools/settings.py: delivery_root()`）：

```
① 环境变量 DSH_DELIVERY_ROOT
② settings.json 的 delivery_root（设置页可改，带机器指纹）
③ 桌面 test —— 【仅当它本来就存在】时沿用（老用户不丢历史成果）
④ <工程模式根>/output
```

### 本机设置与「跨机器自动忽略」

`tools/settings.py` 把「跟这台电脑有关」的设置收敛到**一份** `settings.json`
（交付目录 / SW / CAD / Python 路径），每条都带 `machine` 指纹
（主机名 + 用户名 + 盘符集合）。**指纹不符 = 这条设置来自别的电脑 → 自动忽略**，
并在报告里明说原因。于是：

- 不会把 A 电脑的 `Z:\...\SLDWORKS.exe` 带到没有 Z 盘的 B 电脑；
- `connection_state.json` 的 `paths` 也带同一枚指纹，跨机自动作废并重新探测；
- 设置页里能直接改，改完**下一轮**就生效（无需重启）。

```powershell
python tools/settings.py report                 # 每条设置在本机的生效状态
python tools/settings.py set delivery_root D:/dsh-out
python tools/settings.py set python_exe C:/Python312/python.exe
python tools/env_check.py probe                 # 完整自检（JSON）
python tools/env_check.py prompt                # 生成给新对话的修复提示词
```

### Python 版本闸门

`sw_bridge.py` 使用 f-string（3.6+）。微机房实测：默认 `python` 若是 3.5.x，
会在**第一行**就 `SyntaxError`，stdout 没有任何 JSON —— 上层只看到「解析失败」，
根因完全看不出来。因此 `sw_bridge.py` 顶部加了一段**不依赖 f-string** 的版本闸门：
旧解释器上也会打印中文说明 + 机读 JSON（`hint` 指向 `py -3` / 安装 3.11+）。

插件侧 `resolvePython()` 同步加固：

- 候选顺序改为 `py`（Windows 官方启动器，会挑最新 3.x）**先于** `python`；
- 每个候选都跑真探针（打印版本 + `import win32com.client`），**≥3.6 才采纳**；
- 旧实现只看 `--version` 的 exitCode（3.5 也返回 0）→ 永远选中跑不起来的那个。

### 提醒弹窗（第5项）为什么之前没出来

两条真实缺陷，均已修：

1. **一次性机会被烧掉**：旧 `engLimitAutoTick` 无论成败都写 `_engLimitLastSession`。
   而 `apply()` 那一刻会话往往还没被保留、`sessions.scope` 拿不到 → 首次注定失败，
   之后**再也不弹**。现在失败计入重试额度（最多 3 次），**成功才封顶**。
2. **没有确定性入口**：自动弹窗依赖会话列表订阅 + 保留 scope，时序不巧就静默失效。
   现在设置页「工程环境检查」里有「立即查看提醒」按钮，随时能把同一条提醒调出来
   （官方弹窗外壳 → 不可用时自动降级为自绘卡片，两按钮语义不变）。

## 连接区（设置页「连接区」）

设置页的「SW 连接」/「CAD连接」按钮与 AI 流程共读同一份状态文件
`<状态目录>/connection_state.json`（由 `tools/conn_state.py` 维护）。
放着 localStorage 不用是因为 AI 读不到浏览器存储，无法做"流程中代码判断"。

| 命令 | 作用 |
|------|------|
| `python tools/sw_bridge.py conn-probe [--target sw\|cad] [--no-com]` | 探测并落盘（`--no-com` 只查进程，绝不发 COM） |
| `python tools/sw_bridge.py conn-status` | 只读状态摘要 |
| `python tools/sw_bridge.py conn-gate --target sw` | ★流程判断：`skip_startup` 决定是否跳过启动排障 |

- **路径发现覆盖任意盘符**：`swapi._drive_roots()` 枚举 A~Z 全部真实盘符，
  `swapi._sw_template_dirs()` 按「盘符 × Program Files × SOLIDWORKS 变体」展开。
  ⇒ SW/CAD 换装到 C/D/G/Q 等**任意盘**都能被找到。
- **CAD 安全前置**：`ac_bridge` 在 `acad.exe` 未运行时**绝不激活 COM**
  （`Dispatch` 会尝试启动 AutoCAD，可能挂起数分钟甚至在安装/许可异常时把 CAD 弄崩）。
- **过期保守回退**：状态超过 900 秒即视为未连接，`skip_startup` 返回 `false`，
  宁可多启动一次也不因陈旧状态跳过必要步骤。

