# 七阶段分析插件 · 第二轮缺陷修复（2026-11-19）

> 现场报告：`C:\Users\j1877\Desktop\test\减速器任务_七阶段插件缺陷报告.md`
> 任务：单级圆柱齿轮减速器 · `20261118_225652_07A37243`
> 上一轮：[fixes-20261118-seven-stage-bugs.md](./fixes-20261118-seven-stage-bugs.md)

---

## 结论速览

| BUG | 报告现象 | 真实状态 | 修法 |
|---|---|---|---|
| **1** search/submit 死锁 | 致命 | 上一轮已修（检索外包）；本轮**补强留痕** | `search_fill` 增加 `outcome`/`error` 四态；失败不许携带 hits |
| **2** dataJson 传对象→INPUT_TOO_LARGE | 高 | **确认未修** | 三类错误分别给专属码 + 正确写法示例 |
| **3** 终态不解锁工具 | 高 | **确认未修** | `enforce()` 对 `ready`/`partial` 放行 |
| **4** 终态无限唤醒 | 中 | **确认未修** | 终态通知每任务只发一次；文案带"还差谁" |
| **5** 终态 propose_rooms/retry 无出口 | 高 | **确认未修** | 新增 `reopen`（仅 planner/root，恢复点由插件推导） |
| **6** attach 用显示名当 planner | 中 | **确认未修** | 报错点明 sessionId/UUID + 按显示名唯一解析 |
| **7** 1.1 模板产线叙事 | 低 | **确认**（29/63 步含产线词） | 改写 7 个最扎眼步骤（方案 B） |
| **8** 环境前置未暴露 | 中 | 搜索 key 依赖已随 BUG-1 消失 | `provide_context` 返回 `env_check` + 醒目警告 |

---

## BUG-2 · dataJson 类型错误必须说成"类型错"

**根因**：`check(!dataJson || dataJson.length<350_000)` 对对象取 `.length` 得 `undefined`，
`undefined < 350000` 为 false，`!false` 成立 → 抛"太大"。真因被完全掩盖。
同处 `JSON.parse` 还会把数组/坏字符串的原始异常直接冒给宿主。

**修法**：三种情况分别给码。

| 传入 | 旧行为 | 新行为 |
|---|---|---|
| 对象 `{a:1}` | `INPUT_TOO_LARGE` | `DATAJSON_MUST_BE_STRING` + 正确写法示例 |
| 数字 `42` | `INPUT_TOO_LARGE` | `DATAJSON_MUST_BE_STRING` |
| 数组 `[1,2]` | 裸 `SyntaxError` 冒给宿主 | `DATAJSON_MUST_BE_STRING` |
| 坏 JSON `'{bad'` | 裸 `SyntaxError` | `DATAJSON_INVALID_JSON` |
| 超长字符串 | `INPUT_TOO_LARGE` | `INPUT_TOO_LARGE`（唯一正确的用法） |

---

## BUG-3 · 终态必须解锁分析角色

**现场**：`phase=partial` 后 knowledge/design/review 仍被 `SEVEN_STAGE_TOOL_GATE` 锁死，
连 `glob`/`read` 都调不了，磁盘上的产物读不回来 —— "保留部分结果"这条承诺落空。

**根因**：`enforce()` 只看角色、**不看 phase**。`terminal` 集合早就定义好了，guard 没用它。

**修法**：
```js
const TERMINAL_PHASES = new Set(['ready','partial']);
function enforce(exec) {
  ...
  if (bound.role === 'root') return;
  if (isTerminal(bound.s)) return;   // ← 终态解锁
  return 'SEVEN_STAGE_TOOL_GATE: ...';
}
```

**为什么解锁是安全的**：终态下所有推进动作本身就被 `flow.call()` 拒
（`ALREADY_TERMINAL` / `WRONG_PHASE`），所以解开的是"读写自己产物的能力"，
不是"改变分析结论的能力"。`reopen` 回到非终态后会自动重新上锁（有回归断言）。

---

## BUG-4 · 终态通知只发一次

**现场**：停在 `partial` 后连续收到 7 次同一句
「收到需要真人参与或核对的分析消息」，每次 status 逐字节相同，模型被反复唤醒。

**根因（已用模拟宿主复现，不是猜的）**：
`targetPhase()` 的 key 里含 `Object.keys(s.ended).join(',')` 与 `failures`。
终态下每有一个角色补登记 end，key 就变一次 → 去重失效 → 同一条通知重发一次。
四个角色 + root 陆续 end，正好 6~7 次。**不是无限循环，是"每次状态推进重发同一句"。**

**修法**：终态单独分支，每任务只发一条 `terminalNotified`，文案带"还差谁"：

```
分析已进入终态（phase=partial）。还差 design、review 登记 end（kind=partial）。
这是本任务最后一条自动通知，之后不会重复；需要时你自己调 status 即可。
```

非终态的通知逻辑保留不动（那部分是有用的状态推进信号），但文案改为带原因
（"planner 提了一个需要真人回答的问题" / "有角色登记了故障：knowledge"）。

---

## BUG-5 · partial 不再是单向门：新增 `reopen`

**现场**：`propose_rooms` 要 `ready` 拒、`retry` 要非终态拒 —— **既做不完，也救不回**，
只能另建 taskId 从第 1 步重跑 63 步。

**修法**：新增 `reopen`，**恢复点完全由插件推导**，模型只说"我要续跑"：

```js
const resume = !s.slot ? 'frame'
  : !s.slot.knowledge ? 'knowledge'
  : (s.cursor === 0 && !s.slot.preflight) ? 'preflight'
  : !s.slot.design ? 'design'
  : !s.slot.review ? 'review'
  : 'commit';
```

| 约束 | 值 | 理由 |
|---|---|---|
| 谁能调 | 仅 planner / root | 大脑与传话员；分析角色不能自己重启流程 |
| 从哪能调 | 仅终态 | `REOPEN_ONLY_FROM_TERMINAL` |
| 恢复点 | 插件按 slot 推导 | **模型不能指定 phase / job / 结论** —— 状态机权威仍在插件 |
| 历史故障 | 搬进 `history.reopened` | 审计可回溯，不静默丢弃 |
| 失败预算 | 重置 | 人工介入视为一次全新开始 |
| 工具门禁 | 自动重新上锁 | 回到非终态，BUG-3 的解锁不再适用 |

---

## BUG-6 · attach 的 planner 必须是 sessionId

**现场**：传 `{"planner":"agent-1"}`（`subagent_fork` 的显示名）→
`REAL_DIRECT_CHILD_REQUIRED`，报错里**一个字都没提**要填 sessionId。
实测是靠翻 `~/.dsh/sessions/` 的 mtime 才猜出正确 UUID，卡了很多轮。

**修法两道**：
1. 报错明确写「planner 要填【子代理的 sessionId】，形如 `16713df1-09d5-…`，
   【不要】填显示名」，并给出两个获取方法（`list_agents` / `~/.dsh/sessions/`）；
   还回显"本次收到的 planner = xxx"以及它为什么没通过。
2. 传入的不是 UUID 时，先在直系子会话里**按显示名唯一匹配**自动解析；
   匹配到多个则报 `PLANNER_NAME_AMBIGUOUS` 列出候选，**绝不瞎猜**。

---

## BUG-7-B · 模板改写（方案 B）+ 可复现指纹

**改写的 7 个最扎眼步骤**（从"压装机/产线工艺"改为领域中立）：

| 步骤 | 改动 |
|---|---|
| 1.1 | 「一件产品进入工位」→「要做的东西怎么用」；例子换成减速器 |
| 1.2 | 「人太累/产量不够/良率低/招不到人」→ 通用痛点表述 |
| 1.3 | 「现场边界」→「安装与使用边界」；表格改为对接件/载荷/工作制/环境 |
| 2.1 | 「产品拆成零件」→「要做的东西拆成零件」；来料姿态→接口关系 |
| 2.3 | 「每道工艺的窗口」→「关键特征的关键量及允许范围」；压装/测漏→齿轮/轴/箱体 |
| 2.4 | 产线异常→失效模式（过载/润滑/装配偏差/疲劳/密封…） |
| 5.1 | 「压装工位」教学图→「齿轮副传动与受力链」 |

**同时修掉一个隐藏地雷**：`steps.json` 哈希锁定 + `load()` 对 hash 不匹配直接抛，
而 `tracked()` 会遍历目录里**所有**状态文件 —— 意味着**改一次 steps.json，
目录里任何一个旧任务都会变成地雷**，让 `trackedActor()` → `enforce()` 全线崩溃。
已实测复现并修复：`load(task, strict)`，`tracked()` 用非严格模式跳过旧版本文件。

**指纹改为可复现**（三处同源同算法）：

```python
sha256( json.dumps({opening, crossCutting, version, steps},
                   ensure_ascii=False, separators=(",", ":")) )
```

- `flow.js` 的 `computeSpecHash()` 现算；
- `steps.json` 的 `sha256` 字段存同值（给人看的指纹）；
- `seven_stage_gate._SPEC_HASH` 存同值；
- 三者不一致 → attach 时 `SPEC_HASH_DRIFT` **fail-fast**，不等 63 步跑完才废。

> 旧值 `65d8374b…` 无法用任何可复现公式重算（是手填的），所以每次改模板都对不上。
> 新值：`9f68011877d0c8f74971de4ad837d227ba71da2e3076b02268712e4e8ebdfdae`

---

## BUG-8 · 环境体检前置

`provide_context` 第 3 次返回新增 `env_check`：

```json
"env_check": {
  "ok": true,
  "checked": ["seven_stage_state_dir", "plugin_loaded", "load_case_file"],
  "issues": [],
  "note": "联网不再需要任何 key：七阶段插件自己【不联网】，由真实主对话用宿主内置 web_search 执行检索工单。"
}
```

- 状态目录复用 `seven_stage_gate.state_dir_for()` 解析 —— 与 attach 写的是**同一口径**；
- 载荷工况文件缺失给 `warn`（不阻断，但提前说）；
- 是只读探测，不阻断流程（避免误报把新用户卡死）。

---

## 附带修复 · 载荷工况随任务包下发

**现场**：knowledge 抱怨 `gate_load_case.json 读不到 → 全部强度/寿命数值无输入`。
根因不是文件不存在（它就在 `workflow_state.load_case_file`），
而是分析期间它们的 `shell`/`read` 全被门禁关死。

**修法**：不是给它们开 shell（那会破坏隔离），而是**插件在任务包里投影一份只读载荷数据**：

```json
"loadCase": {
  "path": "...\\gate_load_case.json",
  "nominalLoadN": 50000, "designLoadN": 75000,
  "impactFactor": 1.5, "safetyFactor": 2.0,
  "material": {"id":"Q235","yieldMpa":235,"utsMpa":375,...},
  "note": "数值权威以 path 指向的文件为准（physics 校核读同一份）。不要另起一套数值。"
}
```

**数值权威仍是 workflow_gate 写的那个文件**，插件只做投影 —— BUG-07 的老坑不会重开。

---

## BUG-1 补强 · 失败的检索必须留痕

`search_fill` 新增 `outcome` + `error`：

| outcome | 含义 | hits 约束 |
|---|---|---|
| `ok` | 正常检索（0 条或多条都算） | 任意 |
| `unavailable` | 宿主没可用搜索能力（无 provider / 未登录 / 被墙） | **必须为空** |
| `error` | 调用了但报错（timeout / 限流） | **必须为空** |
| `blocked` | 策略/权限不允许 | **必须为空** |

- `FAILED_SEARCH_CANNOT_CARRY_HITS` 挡住"把失败包装成有结果"；
- 审计记 `outcome` + `error`，**不伪造成"搜到 0 条"**；
- 无来源 + blocking gap 的降级路径已实测可达（上一轮修好，本轮补了留痕）。

---

## 回归

```powershell
$env:PYTHONIOENCODING='utf-8'; $env:PYTHONUTF8='1'
node engineering\plugins\dsh-engineering-seven-stage\test\smoke.mjs
python engineering\tools\test_regression_seven_stage_wiring.py
```

| 测试 | 结果 |
|---|---|
| `smoke.mjs` | OK（检索工单 + **插件零联网** + 63 步完整链） |
| `test_regression_seven_stage_wiring.py` | **PASS=124 / FAIL=0**（新增 F5 段：24 项源码断言 + 39 项行为断言） |
| 63 步端到端（新模板） | `phase=ready` / `progress 63/63` / `completions=5` |

F5 行为验证覆盖：dataJson 三类错误、终态解锁（4 角色）、终态通知 ≤1 条、
reopen 出口（含"分析角色不能 reopen"、历史保留、门禁重新上锁）、
loadCase 投影数值一致、旧版状态文件不崩守卫、spec 哈希可复现。

---

## 部署

```powershell
.\sync-to-dsh.ps1 -DryRun -Prune
.\sync-to-dsh.ps1 -Prune
python engineering\tools\verify_sync.py
# 然后【完全退出并重开 DSH】
```

> ⚠️ 本次改了 `steps.json`（spec 指纹变化），**旧任务的状态文件全部失效**。
> 已做的防护：旧文件只会被安静跳过，不会打崩守卫；但旧任务本身无法续跑，
> 需要新建 taskId。这是换模板的固有代价，不是 bug。
