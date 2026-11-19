# 七阶段分析 · 提速方案（2026-11-19）

> 依据：会话 `session-a332529d`（任务 F6C63358）与 `session-c9c2fd56`（任务 A37604C4）的实测数据。
> 前序：[slow-1of63](./fixes-20261119-seven-stage-slow-1of63.md) · [handshake](./fixes-20261119-seven-stage-handshake.md)

---

## 一、先看数据：慢在哪

### 实测基线（会话 1 · planner 子会话）

| 指标 | 实测值 |
|---|---|
| LLM 步数 | **91** |
| 输入 token 合计 | **4.04 M** |
| 单步输入 token | p50 **40,077** / p90 55,616 / max 59,390 |
| 单步耗时 | p50 **7.6 s** |
| 每次请求 tools schema | **29,440 字符**（43 个工具） |
| system prompt | **26,810 字符** |
| 完成 1 个小步 | 平均 **22.8 个 LLM 步** |

### 步的构成（关键发现）

| 类型 | 次数 | 占比 |
|---|---|---|
| 纯 `status` | 22 | |
| 纯 `yield` | 14 | |
| 纯 `send_message` | 11 | |
| **小计（纯协调，零产出）** | **47 / 91** | **52%** |
| 其消耗输入 token | 2.03 M / 4.04 M | **50%** |

**结论：一半的算力花在"问一句、答一句"上，而不是分析本身。**

### 三个可归因的浪费

1. **每次请求都背 29.4 KB 工具 schema** —— 而分析角色实际只能用 4 个工具。
2. **每次干活前先 status 一轮** —— status 只给状态，不给"该填什么"，于是"看 → 想 → 再动手"。
3. **一个 63 步流程，p50 单步 7.6 s** —— 63 步 × 每步多次往返 = 分钟级到十几分钟级的固定开销。

---

## 二、插件端能做的三件事（本次已实现）

### ① 把分析角色的工具集收窄到 4 个

**问题**：`guard()` 只在**调用时**拒绝，工具 schema 依然逐字进入每一次请求。
43 个工具 = 29,440 字符 ≈ 每次请求白烧 ~7K token。

**做法**：用宿主原生的 `agent.ctx.tools.restrict({allow:[...]})`
（实现见 `@deepseek-ai/dsh-tools/src/index.ts:restrict()`，
作用在**声明层**："Restrict global tools for the calling agent scope"）。

```js
const ANALYSIS_TOOL_ALLOW = ['engineering_seven_stage', 'wait_agent', 'send_message', 'list_agents'];
```

在 `bind` 成功与 `launch` 之后立刻调用。用 **allow 白名单**而不是 deny：
新工具默认不可见，与"分析角色只能读、结论归插件"的口径一致。

**预期收益**：schema 29.4 KB → 约 1 KB，**每次请求省 ~28 KB（约 7K token）**。
按 91 步算，planner 单角色可省 **~0.6 M 输入 token**。

> 失败必须静默：`restrict` 只是优化，拿不到 `agent.ctx` 或工具名对不上时
> 只记一条 info，行为退回"全量可见 + guard 拦" —— 不能让可选项把主流程带崩。

### ② 任务包直接带 `nextAction`

**问题**：status 返回的是**状态**，不含"下一步该填哪些字段"。
模型拿到状态后还要自己想模板，于是"status → 思考 → 动手"成了固定两轮。

**做法**：新增 `nextActionOf(s, role)`，随任务包下发**动作名 + dataJson 模板 + 必填字段**：

```js
// planner / phase=frame
{action:'frame', required:['object','decision','boundaries','searchTopic','verification','assumptions','prior'],
 dataJson:{object:'…', decision:'…', boundaries:'…', searchTopic:'…', verification:'…', assumptions:'…', prior:'…', blockedBy:[]}}
```

覆盖 planner 的 `frame / commit / resolve / evidence_wait / ready`，
以及三个执行角色的 `submit`。这是**纯投影**，不改变任何判据 —— 填错照样被 check() 拒。

**预期收益**：把 22 次纯 status 往返压掉大半。

### ③ 工具输出只给索引（上一轮已做，本轮延续）

status 从 24.6 KB 降到 4.0 KB（review）/ 3.4 KB（planner），
正文走 `read {step, part}` 分段取（≤6000 字/次）。

---

## 三、还需要宿主侧配合的（插件改不了）

### 1. `wait_agent` 唤不醒 —— 已在插件侧绕开

宿主 `wait_agent` 监听 `agent/inbox/inserted`，而插件用 `delivery:'queue'` 投递走的是
`agent/inbox/spliced`（next-turn 队列）。两者不是同一个事件。

**插件侧缓解**：通知里明确写「不要用 wait_agent 等任务包，没活就 yield 结束回合」。
**根因在宿主**：若要彻底解决，需 `@deepseek-ai/dsh-tool-subagent-control` 在
`agent/inbox/spliced` 上也挂一条监听。

### 2. 每个小步仍然是一次完整往返

63 步 × 每步 4 个角色各一轮 ≈ 250+ 次 LLM 请求，这是架构决定的固定成本。
插件能削减的是**每次请求的体积**（①②），削减不了**请求次数**本身。
真要再快，只有改流程粒度（比如允许 planner 一次 frame 多步）——
那会削弱七阶段"每步都必须有人签字"的设计初衷，**不建议动**。

---

## 四、预期效果（保守估算）

| 项 | 现状 | 改造后 | 依据 |
|---|---|---|---|
| tools schema / 请求 | 29,440 字符 | ~1,000 字符 | allow 白名单 4 个工具 |
| planner 单步输入 p50 | 40,077 token | ~33,000 token | 省掉 schema + 部分 status 往返 |
| 纯协调步占比 | 52% | 预计 30~35% | nextAction 消掉大部分 status |
| **planner 总输入 token** | **4.04 M** | **预计 2.3~2.8 M** | 两者叠加 |

> 这些是**按体积推算**的估算值，不是实测。要拿到真数，下一次跑真实任务时对比
> 会话日志里的 `assistant/message.usage.inputTokens` 即可。

---

## 五、回归

```powershell
node engineering\plugins\dsh-engineering-seven-stage\test\smoke.mjs
python engineering\tools\test_regression_seven_stage_wiring.py
```

| 测试 | 结果 |
|---|---|
| `smoke.mjs` | ✅ OK（63 步完整链不受影响） |
| `test_regression_seven_stage_wiring.py` | ✅ **PASS=171 / FAIL=0**（上轮 165 + 本轮 6 项） |

新增断言：
- 分析角色用 `tools.restrict` + allow 白名单收窄；
- restrict 失败静默（不阻断主流程）；
- bind / launch 后立刻收窄；
- 任务包带 `nextAction`，且覆盖 planner 五个阶段。

---

## 六、部署（三处必须同步）

本插件现在有三个存放点，**改完必须同时同步**：

| 位置 | 路径 |
|---|---|
| 仓库源码（唯一真源） | `engineering/plugins/dsh-engineering-seven-stage/` |
| DSH 实际加载 | `~/.dsh/profiles/{web,desktop}/node_modules/dsh-engineering-seven-stage/` |
| 交付目录 | `C:\Users\j1877\Desktop\DSH-七阶段分析插件-交付\源码\` |

```powershell
.\sync-to-dsh.ps1 -Prune                   # 仓库 -> DSH 安装副本
python engineering\tools\verify_sync.py   # 逐文件 SHA-256 比对
# 交付目录同样要复制（lib/ + test/ + package.json）
# 完全退出并重开 DSH（插件 host 半是进程级）
```

> ⚠️ 踩过的坑：
> ① `sync-to-dsh.ps1` 只同步 lib/，**不含 test/**，曾导致 desktop 的 `test/smoke.mjs` 落后；
> ② `verify_sync.py` 原先**根本不比对七阶段插件**，改完照样报"完全同步"。
> 两处都已补：verify_sync 现已纳入 `seven-stage@web` / `seven-stage@desktop`。

### 2026-11-19 三处同步结果（逐字节 SHA-256 前缀）

| 文件 | 仓库 | 交付 | web | desktop |
|---|---|---|---|---|
| lib/flow.js | 9EA08F6D | 9EA08F6D | 9EA08F6D | 9EA08F6D |
| lib/index.js | CF9FB19B | CF9FB19B | CF9FB19B | CF9FB19B |
| lib/steps.json | 112E4104 | 112E4104 | 112E4104 | 112E4104 |
| package.json | F48BA29A | F48BA29A | F48BA29A | F48BA29A |
| test/smoke.mjs | BB261FAE | BB261FAE | BB261FAE | BB261FAE |
| seven_stage_gate.py | EA444BCD | EA444BCD | — | EA444BCD |

文档 **10 份**在 交付目录 / 仓库 docs / DSH docs 三处逐字节相同。
交付目录另有 3 份独有文档（运行验收手册 / 交接注意事项 / 设计依据），已反向补进仓库与 DSH。

