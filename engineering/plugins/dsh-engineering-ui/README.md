# dsh-engineering-ui

DSH「工程模式」配套 UI 插件。

## 功能

### 1. 子代理三栏工作区
- **主对话 3/4**：有子代理时自动把主对话压到 3/4 宽。
- **中间竖排频道列表**：每个子代理一个频道按钮，绿点=运行中，点击切换右侧屏幕。
- **右侧 1/4 工作区**：所选子代理的实时输出、工具调用/结果、运行状态。
- **无子代理时全部隐藏**，回到全宽正常聊天。

### 2. 非工程模式警告横幅 —— 【问题2 修复：已移除】
该横幅原本只为「SW单行模式」权限预设配套。随 SW单行模式 一并删除后，
本插件不再注册 `conversation.input.dock` 横幅插槽，也不再注入预设图标。
权限只保留 DSH 标准三档（read-only / workspace-write / danger-full-access）。

### 3. 阻塞式代问（子代理提问桥接）
DSH 平台禁止子代理直接向用户提问（`userQuestions.ask()` 校验 `agents.roots()`，
子代理必抛 `DELEGATED_CALLER`）。本插件把子代理的提问路由到**主对话**的
`userQuestions` 上真正挂起，用户在主对话框作答后，答案以 **tool_result**
形式回到提问方 —— 而不是用 `followup` 当"新消息"投递（那会造成自问自答/消息错乱）。

### 4. turn-stopping 收尾守卫
仅对 `engineering` 预设生效：任务未进入**代码级终态**时阻止对话提前结束，
并提示模型继续推进。

放行的唯一途径（任一即可）：
1. `workflow_state.json` 的 `step == 'finished'`；
2. `mode_state.json` 中无 active 房间（全房间 room-end/room-fail）；
3. `TASK_FINISHED.json` 标记 `finished`；
4. 正文【单独一行】写出 `[TASK_DONE]`；
5. 用户明确要求停止。

### 5. ~~重复输出自动阻断（Bug-47）~~ —— 【已移除】

该功能原本挂在 `agent/assistant-stream` 上，按"同一段文字在时间窗内出现
次数"判定病态循环，达阈值即 `agent.cancel()` 打断本轮。

**移除原因（用户决定）**：该判定在实际作业中误伤正常行为 ——
进度播报（`[进度: 60%]`）、状态复述、工具重试提示在正常工作节奏下本就会
重复出现，却被当成病态循环直接终止对话，表现为"模型自己就断了"。

若将来确需防死循环，应基于**回合数 / 无进展**这类结构性信号，
而不是"文字重复次数"。该监听器的注册已一并删除。

### 6. 设置页「工程模式」分区（三层结构 + 显示页开关）
在 DSH 设置页注册一个 `settings.section`（id=`engineering`，order=25，标签「工程模式」），
位于「Agent 预设」之后。内容分**三层**：

| 层 | 标题 | 内容 |
|----|------|------|
| 1 | **概览** | 分区用途 + 当前生效状态 |
| 2 | **工作流与门禁** | 流程约束说明（默认折叠） |
| 3 | **界面与显示** | ★ **「使用工程模式自带的子代理显示页」开关** |

#### 开关语义（用户需求原文：「是否使用工程模式自带的子代理显示页」）

- **开（默认）**
  1. 右侧子代理面板由本插件接管（三栏布局）；
  2. **官方右侧的子代理入口也指向本插件** —— 用同 kind + `priority:'extension'`
     接管官方 `subagentchat` tab 类型（官方档位规则
     `extension(3) > builtin(2) > fallback(1)`，属官方一等机制，非 hack）；
  3. `canOpen` 限定工程模式：非工程模式自动回退官方界面。
- **关** → 隐藏本插件面板，右侧**完全交回 DSH 官方**。

#### 持久化

`localStorage`，键 `dsh-engineering-ui/settings/v1`。
这是官方认可的做法（`packages/client/store` 的 `persist` 与 `shortcuts` 都这么做），
且**不需要改动 Host 半**，别人 clone 仓库后行为完全一致。

#### 两个已修的坑（不要改回去）

1. **必须把设置纳入 effect 依赖**：控制 `body` 类的 `useEffect` 若只依赖
   `[active, paneOpen, embedded]`，关闭开关后组件 `return null` 但依赖未变
   → **清理函数不执行** → `dsh-eng-dock` / `eng-pane-open` / `dsh-eng-mode`
   残留 → CSS 继续强改 `grid-template-columns` → **右侧留空白蓝带，
   连左侧项目栏的列宽也被改**。修复：加 `engAllowed` 依赖 + 无条件清理。
2. **让位判据只认官方 `isExpanded()`**：不要把 `openTabs` 或 `active()` 当
   "官方占用右列"的判据 —— 官方该快照**跨全部会话 + localStorage 持久化**，
   只要历史上开过任何 tab 就永远非空 → **永久让位** → 面板"该出没出"。

## HTTP 接口（13 条，与 Harness Web 同源）

只读（GET）：
- `/dsh-engineering-ui/agents?rootSessionId=...` — 子代理树
- `/dsh-engineering-ui/log?sessionId=...&since=...&limit=...` — 单个子代理的实时日志
- `/dsh-engineering-ui/verify-subagent?...` — 【Bug-32】子代理启动校验
- `/dsh-engineering-ui/notify-room` — 【Bug-21】房间通知（不打断建模）
- `/dsh-engineering-ui/pending-child` — 查询待答提问
- `/dsh-engineering-ui/result-child` — 取回已答结果

提问桥接：
- `/dsh-engineering-ui/ask` / `/answer` — 主对话阻塞式提问与作答
- `/dsh-engineering-ui/ask-child` — 子代理提问（挂起到主对话）

防线凭据（HMAC 签名信任根）：
- `/dsh-engineering-ui/defense/sign` — 宿主签发凭据
- `/dsh-engineering-ui/defense/verify` — 验签
- `/dsh-engineering-ui/defense/info` — 签名宿主信息
- `/dsh-engineering-ui/defense/judge` — 总判定

## 安装

本插件随工程模式预设一起分发。安装脚本（`install-0.2.0.ps1` 或
`engineering/install-plugin.ps1`）会把它复制进 profile 的 `node_modules`
并登记到 `package.json`（`dependencies` + `dsh.profile.bundles`）。

> ⚠️ 插件 host 半与宿主层配置都是**进程级** → 改完**必须重启 DSH**；
> client 半改动**刷新页面**即可。

## 边界

不注册任何模型工具，不改变子代理调度、模型、权限或上下文。

主机侧**唯一**的写行为：
- 【Bug-21】房间通知文件 `reports/<房间>.notify.json`；
- 防线凭据的**签名**（`defense/sign`，私钥仅存于宿主内存，不落盘）。

> 注：早期的 `question_log.json` 提问记账机制**已被移除**
> （判定价值低且反复卡住流程）。现行契约只保留
> `choice_contract` 提供的**固定两问题面**（A/B/C 搭建方式 + D/E 并行策略），
> 强制点移到 `workflow_gate.py select` 的参数校验。
