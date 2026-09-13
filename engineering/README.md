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
1. 把 **SW单行模式** 权限预设 patch 进宿主层 `profiles/web/cordis.patch.yml`（幂等，已存在则跳过，自动备份）
2. 把 `plugins/dsh-engineering-ui` 复制进 profile 的 `node_modules`
3. 把插件登记进 profile 的 `package.json`（`dependencies` + `dsh.profile.bundles`）

> ⚠️ 脚本依赖 **`<preset目录>/plugins/`** 下的插件源码。
> 若该目录缺失，安装脚本会直接失败（报找不到源路径）——请确保插件源码随预设一起分发。

然后**重启 DSH Web**。

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
      sw-single-line:     { sandbox: danger-full-access, approval: never, name: SW单行模式 }
```

**为什么必须写 `defaultPreset`**：权限服务的行为是

```js
const inferredDefault = this.derive(EMPTY_KNOBS);
const defaultPreset = config.defaultPreset ?? inferredDefault;
```

patch 是**整体替换该行 config**，一旦覆盖了 `presets` 表又没给 `defaultPreset`，
服务只能靠推导——推不出来时 UI 默认值会落到表内任意项，
表现为"设置了 Full access，新会话却仍被 SW单行模式 锁住"。

### 两档工作模式（手动切换）

| 预设 | sandbox | approval | 什么时候用 |
|------|---------|----------|-----------|
| **Full access** | `danger-full-access` | `never` | 默认；完全访问，不弹审批 |
| **SW单行模式** | `danger-full-access` | `never` | 工程模式专用（SW 活） |
| `workspace-write` | `workspace-write` | `ask` | 与 SW 无关的本地杂活 |

| 字段 | 值 |
|------|-----|
| 预设 id | `sw-single-line` |
| 显示名 | SW单行模式 |
| 图标 | 随插件内联分发（`assets/sw-single-line-*.png`） |

**修改 preset 表必须重启 DSH**（进程级配置，不热生效）。

---

## 工作流门禁（Mode 2 · 大型复杂器械装配）

### 三步门禁（必须用 DSH 工具，不是命令行）

1. `workflow-gate-init` — 返回**统一问题集**，必须原样问给用户
2. `workflow-gate-provide-context` — 力学估算，返回 A/B/C 搭建方式 + D/E 并行策略
3. `workflow-gate-select` — 用户选定后生成子代理房间配置

### 统一问题集（17 题 · 单一事实来源）

定义在 `workflow_gate.py` 的 `QUESTION_SPEC`，代码 / 文档 / persona **全部引用它**，
杜绝"有时问 5 个、有时问 10 个"的分裂：

| 组 | 题目 |
|----|------|
| **A 结构形态** | 机构类型、臂长/行程、基座形式、末端执行器、整体包络 |
| **B 工况载荷** | 额定负载、负载类型、使用场合、设计寿命 |
| **C 驱动与运行** | 驱动方式、工作制度、精度要求 |
| **D 制造条件** | 材料倾向、加工方式、成本/重量倾向 |
| **E 特殊要求** | 防水防尘防爆等硬性要求 |
| **Z 开放补充** | **其他补充（开放式）** — 无选项，自由填写 |

每组均带枚举选项 + 「你决定」；标注「你决定」的题由门禁给默认值，
但默认值会在零件参数表中明确标注供复核。

### 波次调度

- **第 1 波·独立爆发期**：结构件 + 传动机构 + 壳体机架，**同时开 3 个小屋**
- **第 2 波·收敛汇合期**：全部建模房间 `room-end` 后 → 总装确认门禁 → 总装小屋
- **第 3 波·出图期**：总装完成后 → 工程图小屋

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

## SW 使用窗口（进程级 FIFO 锁）

SolidWorks 是单实例程序，`mode_gate.py` 在**进程级**检测 `SLDWORKS.EXE`
并给每个房间发 FIFO 排队锁。

### 公平轮转（防饥饿）

- 有人在排队时，单房间最多连续执行 `SW_FAIR_CMDS`（3）条命令
- 或连续独占 `SW_FAIR_SECONDS`（180）秒
- 超出即**强制让位**给队首，保证队列真正前进

### 免锁命令

`close-all` / `sw-release` / `rooms-reset` **无需 `--room`**——
它们本身就是释放动作，串行模式下更无房间概念。

---

## 文件结构

```
engineering/
├── agent.cordis.yml           # preset 组合（persona + 工具 + 技能），不含宿主层服务
├── preset.yml                 # 显示元数据
├── install-plugin.ps1         # 一键安装（宿主层 patch + 插件注册）
├── README.md
├── 常见SW启动失败问题.md        # SW 启动排查手册
├── skills/                    # 技能（7 个）
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
│   ├── physics/               # 物理仿真（GB/T 规则库）
│   └── examples/              # 建模样例
└── plugins/
    ├── dsh-engineering-ui/            # 三栏 UI + 提问卡片 + 守卫
    └── dsH-engineering-sw-single-line/# SW单行模式权限预设
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
| 13 | **权限默认被 SW单行模式锁死** | 显式声明 `defaultPreset: danger-full-access` |
| 14 | **问题集不一致**（有时5问有时10问） | `QUESTION_SPEC` 单一事实来源，统一 17 题 |
| 15 | **子代理不知自己 sessionId 而卡死** | 新增 `whoami` 身份自查命令 |
| 16 | **report 始终为 null** | `registered` 改为开工必做第一件事 |
| 17 | **规则自相矛盾**（禁止调 sw_bridge vs 须 close-all） | 明确 `close-all` 为唯一例外 |
| 18 | **skill 文档路径约定错误**（假设脚本在 cwd） | 全部改为绝对路径占位符 |
| 19 | **设计依据薄弱**（尺寸与载荷不符、公差不规范） | 参数表强制量化依据 + 标准配合代号 |
| 20 | **前端"伪交互"与真实通信脱节** | 提问经 `/ask-child` 绑定 pendingId → tool_result |

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
python "$T\mode_gate.py" lock-doctor     # SW 锁体检
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

插件 host 半（`index.js`）与宿主层配置（`cordis.patch.yml`）是**进程级**，
改完**必须重启 DSH**；client 半（`client.js`）改动刷新页面即可。

> 运行时状态文件（`mode_state.json` / `workflow_state.json` / `sw_state.json`）
> 是会话现场数据，**三处保持独立**，不要互相同步，否则会覆盖现场导致流程误判。
