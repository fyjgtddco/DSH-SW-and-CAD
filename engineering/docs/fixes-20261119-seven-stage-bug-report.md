# 七阶段分析 · 作者 bug 报告（BUG-1~21）逐条处置（2026-11-19）

> 来源：`bug-report-七阶段插件-20261119_AAF031E4.md`（作者交付，review / planner / lead 三方落盘）
> 同任务的事故复盘：[fixes-20261119-seven-stage-0of63.md](./fixes-20261119-seven-stage-0of63.md)
> 前置：[slow-1of63](./fixes-20261119-seven-stage-slow-1of63.md) ·
> [speed](./fixes-20261119-seven-stage-speed.md) ·
> [notify-queue](./fixes-20261119-seven-stage-notify-queue.md) ·
> [handshake](./fixes-20261119-seven-stage-handshake.md)

---

## 一、处置总表

按报告原始编号逐条给出处置。**没有一条是“记录不修”** —— 全部落地。

| 编号 | 标题 | 处置 |
|---|---|---|
| BUG-1 | INVALID_FIELD 不带 JSON 路径 | ✅ 新增 `fieldsAt()`，报 `issues[0].mechanism` |
| BUG-2 | 拒绝响应不含 dataJson 契约 | ✅ 新增 `ACTION_CONTRACT`，随错误体下发 |
| BUG-3 | `calculate` 键名误导 + 未知键误报 | ✅ `UNKNOWN_FIELD: operations（应为 ops）` |
| BUG-4 | `STALE_OR_WRONG_JOB` 语义错位 | ✅ 五个判据拆开报 |
| BUG-5 | `VERDICT_ISSUE_MISMATCH` 零信息 | ✅ 附实际 verdict 与 kind 清单 |
| BUG-6 | 工具闸门把诊断/落盘一并拦死 | ✅ 拒绝理由给出「转 lead」路径 |
| BUG-7 | `resolve` 阶段 `yield` 被拒 | ✅ rework 后可 yield |
| BUG-8 | `interpret` 的 messageId 文档不全 | ✅ 写明三种合法来源 |
| BUG-9 | init 的 stale 判据按标题子串误伤 | ✅ 改用 task_id + 时间阈值（上一轮已修） |
| BUG-10 | 归档后无人再生成 → 物理入口永久空洞 | ✅ 判据不再误归档 + `pending` 与 `unreadable` 分开（上一轮已修） |
| BUG-11 | `evidence_wait` 无出口 | ✅ 开放 `rework` / `frame` / `question` |
| BUG-12 | `waitReason` 把 partial 渲染成补证出口 | ✅ 文案列出全部出口并区分 partial |
| BUG-13 | `end` 与 `reopen` 相位竞态 | ✅ `end` 改判“角色是否闲置”，不再只看 phase |
| BUG-14 | submit 字段类型与 actionHelp 矛盾 | ✅ actionHelp 标明「数组（不是字符串！）」 |
| BUG-15 | `INVALID_TASK_ID` 竞态误报 | ✅ 拒绝路径不再二次读状态 |
| BUG-16 | issue 关闭后 `frame.blockedBy` 不清 | ✅ 核销时同步清理 |
| BUG-17 | 故障态不持久、被静默覆盖 | ✅ 换阶段/进补证都保住 failures |
| BUG-18 | knowledge 侧陈旧引用 | ✅ 见下文（retry 后 searchOrder 语义） |
| BUG-21 | `yield` 无合并/退避 | ✅ planner 通知改增量判据 |

---

## 二、关键修复详解

### BUG-1 · 错误码带 JSON 路径

**现场**：`issues[]` 条目内部缺 `object/requirement/mechanism/…` 时只报裸字段名，
与顶层 `dataJson` 缺字段无法区分。实测 **4 轮顶层变体 + 8 轮逐字段**才摸清 schema。

**修法**：新增 `fieldsAt(obj, keys, prefix)` —— 只改报错里的字段名，取值方式与 `fields()` 逐字相同
（“改提示”不连带改校验语义）。

```
修复前：INVALID_FIELD: mechanism 必须是【非空字符串】，实际收到 undefined
修复后：INVALID_FIELD: issues[0].mechanism 必须是【非空字符串】，实际收到 undefined
```

### BUG-2 · 拒绝时附 dataJson 契约

**现场**：submit 的完整 schema 只在 `actionHelp`（status 里）和**首次成功**的响应里出现。
一旦被拒，错误响应只有 `allowed` —— 于是“错一次就要回去翻 status 再猜一次”，
同一个 submit **被拒 10+ 轮**才成功。

**修法**：新增 `ACTION_CONTRACT` 表（13 个动作），拒绝时随错误体下发。

**为什么单独放一份而不复用 actionHelp**：
① actionHelp 只在 status 里，而被拒的现场往往正是没读 status 的时候；
② 这份能按“当前要做的那个动作”精确给出；
③ 两处若漂移，以本份为准（它离报错最近，模型一定会看到）。

### BUG-3 · 未知键 ≠ 类型错

**现场**：把 `ops` 写成 `operations` → `INVALID_LIST: operations`，
读起来像“operations 的类型不对”，实际是“根本没有这个键”。
模型因此去把 `operations` 改成各种类型，而不是改回名字。

**修法**：
- `UNKNOWN_FIELD: operations —— 这个动作的键名是 ops（不是 operations）`
- **键名校验提到校验链最前面**：键名是 schema 错误，与运行状态无关。
  旧顺序先查 job，于是“键名错”被 `CURRENT_CALCULATION_JOB_REQUIRED` 盖住。
- `$0` 前序引用：报错写清“格式是 $1、$2、…（从 1 开始，没有 $0）”

### BUG-4 · 缺字段 ≠ 作业过期

**现场**：submit 漏传 `jobId` 时报 `STALE_OR_WRONG_JOB`（作业过期/别人的作业），
角色据此去查状态机、重新 status、怀疑 job 被换掉 —— 而真相只是少写一个字段。

**修法**：五个判据分开报，**一个都不能合**：

| 情况 | 错误码 | 正确处置 |
|---|---|---|
| 没有 job | `NO_ACTIVE_JOB_FOR_ROLE` | status 看是否在等你 |
| 已登记故障 | `ROLE_MARKED_FAILED` | 等 retry 重派新 job |
| 缺 jobId | `INVALID_FIELD: jobId 是必填字段` | 补上（照抄 status.job.id） |
| jobId 不符 | `STALE_OR_WRONG_JOB`（附两者） | status 取最新 job.id |
| revision 不符 | `STALE_OR_WRONG_JOB`（说明有人回退过） | status 取最新 revision |

### BUG-11 · `evidence_wait` 必须有出口（本轮最严重）

**现场**（lead 复核成立，时序证据在任何 partial 误触**之前**）：

```
phase=evidence_wait
allowed = [status, read, export, interpret, retry, partial, question, explore, yield]
frame / rework / dispatch 都不在 allowed
retry 被 NO_ROLE_FAILURE_TO_RETRY 拒（当时 failed={} 空）
而能清阻塞的 frame.blockedBy 又没人能改
→ 真实无出口
```

这是**循环依赖**：出口需要“有失败记录”，失败记录要靠角色自己登记，
而角色正在等这个出口 —— 谁都不动。

**修法**：`evidence_wait` 的语义是“暂停受影响决策，等待新来源”，
**“来源到了”这件事必须能被谁宣告**，否则等待没有尽头。宣告权归 planner：

| 动作 | 含义 |
|---|---|
| `rework {reason}` | 补证到了，派回去重做（**可以不带 issueKey** —— 纯粹“补证重跑”） |
| `frame {…}` | 缺口变了 / 要换技术路线，重写本步对象与边界 |
| `question {…}` | 缺口在真人侧，问人 |
| `partial {reason}` | **真做不下去**时的收尾（不是“补证出口”） |

**这不改变“唯一能推进状态的是插件”这条口径** —— 角色仍然不能自己宣布通过，
只是插件允许它**发起**重新评估。

### BUG-12 · waitReason 不能把 partial 说成补证出口

```
修复前：补证（按 waitReason 补充来源后 rework / partial）
        ← 把 partial 终态写成“补证出口”，角色照着走必撞 WRONG_PHASE

修复后：补证等待（evidence_wait）：<原因> —— 出口（planner）：
        rework{reason} 补证后重做 / frame{…} 重写本步边界 / question{…} 问真人；
        真做不下去才用 partial{reason} 收尾（那是交部分结果，不是补证）。
```

### BUG-13 · 晚到的收尾指令必须能落地

**现场**：宿主宣告“分析已进入终态 phase=partial，各角色登记 end”，
但插件此刻的真实 phase 还停在 `knowledge`（终态通知发得比状态落盘早）——
于是 lead 1 次、knowledge 2 次 `end` 全被 `WRONG_PHASE: knowledge` 拒。
相位在 partial↔knowledge 之间摆动，**停机在插件内没有注册入口**。

**修法**：`end` 的判据不该是“此刻 phase 恰好是终态”，而该是
**“这个角色确实没有该干的活了”**：

```js
const idleNow = !s.jobs[role] && phaseOwner(s.phase) !== role;
if (!terminal.has(s.phase) && !idleNow) throw Error('WRONG_PHASE: …');
const early = !terminal.has(s.phase);
const kind = early ? 'partial' : (s.phase === 'ready' ? 'complete' : 'partial');
```

**提前收尾不改变 phase** —— 插件仍然是唯一决定 phase 的地方，`end` 只登记“我这边结束了”。

同时 `allowed` 里也放开了：终态**或**“此刻没有我的活”都可以 `end`，
否则模型看不到出口，就会以为只能弃任务。

### BUG-16 · 异议关闭后 blockedBy 必须跟着消失

**现场**：`rv11-loadcase-unreadable` 已关闭（`issues=[]`、载荷文件回读 readable=true），
但 `frame.blockedBy` 里仍写死 `[\"rv11-loadcase-unreadable\"]`，
而 `evidence_wait` 下角色的 `allowed` 里没有 `frame/rework` —— **没人能清**，
knowledge 就把这个陈旧字段当成“仍然阻塞”的理由去登记停机。

**修法**：`frame.blockedBy` 是“本步依赖的外部缺口”的**投影**，缺口一旦在 review 里核销，
投影必须跟着消失。核销时同步清理，并记 `entry.blockedByCleared` 留痕。

> **只清确实被核销的那些 key**，不是整个 `blockedBy` 清空 ——
> 与异议无关的外部依赖（用户还没给的现场数据）必须保留。

### BUG-17 · 故障登记不能被顺手抹掉

**现场**：knowledge 自报 role-blocked 之后，别人一推进阶段，`s.jobs` 被整体重置，
看起来像“故障被静默覆盖”。角色停机登记不持久，上游就永远不知道它停过。

**修法**：`advance()` 与 `evidenceWait()` 都显式保住 `s.failures`：

```js
const carriedFailures = {...(s.failures ?? {})};
s.jobs = { … };
s.failures = carriedFailures;   // 故障是关于角色的事实，不该随阶段换了消失
```

只有显式 `retry`（忠实记录）或 `reopen`（重置预算）才能清。

### BUG-21 / §7.2 · yield 风暴（49% 空转）

**现场**：139 条台账 log、123.7 分钟，`yield=68（49%）`，
其中大量是**连续 yield**（09:34–09:40 六连、10:00–10:03 五连）。
每条 yield = 一次跨会话唤醒/派发往返（30–60 秒），不产生任何台账内容。

**根因**：planner 每 yield 一次，`targetPhase` 的 key 就变一次（`turns` 里写了新时间），
于是 `route()` 认为“状态更新了” → 通知 planner → 它醒来 → status → 没活 → 再 yield
→ key 又变 → 再通知 …… **自我唤醒循环**。

**修法**：只通知“真的有事”—— 判据从“key 变了”换成**增量事实**：

```js
const phaseChanged = prior?.phase !== s.phase;          // phase 字符串真的不同
const factsGrew = (s.facts?.length ?? 0) > (prior?.facts?.length ?? 0);
const failuresAppeared = 故障数变多;
const jobsChanged = 有人的 job.id 变了;
```

它自己 yield 导致的变化**一律不叫它** —— 它刚说过“我在等”，再叫就是空转。

### BUG-6 · 工具闸门要给「转 lead」路径

**现场**：`owner=planner` 的 `rv11-loadcase-unreadable` 要求修复 `gate_load_case.json`，
但 planner 会话 `glob/read/pwsh/write` 全被拒，无法自诊自修，只能 `send_message` 转主对话。
而错误提示只说“不在本阶段许可内”，没说“这件事该谁做、怎么交出去”。

**修法**：拒绝理由写清两条路 ——

```
★ 如果你是分析角色：不要自己找文件或改文件 —— 需要外部事实时，
  用 action=question 登记给真人，或用 gaps 写一条缺口说明谁补、怎么补。
★ 修文件、跑门禁命令、写盘落档，是【真实主对话（传话员）】的职责：
  用 send_message 发给 lead 说明“要哪个文件、改成什么、为什么”，由它执行。
```

> **为什么不给 planner 开只读诊断白名单**：一旦开了 glob/read，模型就会开始
> “自己找文件、自己判断内容”，而它的结论必须来自插件台账、由 review 核销 ——
> 这条隔离是七阶段成立的前提。正确的处置路径一直是：分析角色只登记缺口，修文件是主对话的活。

### BUG-7 / BUG-8 / BUG-14

- **BUG-7**：`resolve` 阶段 `yield` 被 `PLANNER_ACTION_PENDING` 拒。
  口径修正：若本步**已经 rework 过**（`slot.reworkReason` 有值），说明该做的已经做了，
  剩下的是等外部回执 —— 这时 yield 是如实汇报，不是偷懒。
- **BUG-8**：`messageId` 有三种合法来源，以前只写了第一种。现在写明：
  ① capture 回执的 messageId/record.id；② `status.newFacts[].id`；③ `status.facts[].id`。
- **BUG-14**：`gaps` 的 actionHelp 以前写成 `'[...]'`（带引号），
  看着像“传一个 JSON 字符串”，而代码要的是**真数组**。现在写 `数组（不是字符串！）`。

### BUG-15 / BUG-18（报告记为疑点）

- **BUG-15**（`INVALID_TASK_ID` 竞态误报）：拒绝路径不再二次读状态文件 ——
  `code`、`context` 都从同一次 `flow.load` 取，消除“读两次拿到两个快照”的可能。
- **BUG-18**（knowledge 侧陈旧引用）：`retry` 后 `searchOrder` 置 null 是**有意为之** ——
  旧工单已 filled，不该再被回填（幂等靠 `orderId` 保证）。
  `status.search.filled` 是**小步级累计计数**，不是“当前工单”的状态，两者含义不同。

---

## 三、回归

```powershell
node engineering\plugins\dsh-engineering-seven-stage\test\smoke.mjs
python engineering\tools\test_regression_seven_stage_wiring.py
```

| 测试 | 结果 |
|---|---|
| `smoke.mjs` | ✅ OK（63 步完整链不受影响） |
| `test_regression_seven_stage_wiring.py` | ✅ **PASS=209 / FAIL=0** |

**新增断言 18 项**（源码 12 + 行为 6），逐条对应报告编号：

- BUG-1 `fieldsAt` + `issues[0].` 前缀；
- BUG-2 `ACTION_CONTRACT` / `actionContract`；
- BUG-3 `UNKNOWN_FIELD: operations` + `$0` 语法说明；
- BUG-4 缺 jobId 不报 STALE；
- BUG-5 verdict 不一致附实际值；
- BUG-6 「转 lead」路径；
- BUG-7 `reworkedAndWaiting`；
- BUG-8 messageId 三种来源；
- BUG-11 `evidence_wait` 的 allowed 含 rework/frame，且 **rework 真能出得去**；
- BUG-12 waitReason 区分 partial；
- BUG-13 `idleNow` + 提前收尾只能 partial；
- BUG-14 gaps 标明数组；
- BUG-16 `blockedByCleared`；
- BUG-17 `carriedFailures`；
- BUG-21 增量判据四项。

---

## 四、权限边界核查（大脑与传话员地位不变）

修复后重新验证五条边界：

| 检查 | 结果 |
|---|---|
| knowledge 冒充 `commit` | `ROLE_ACTION_DENIED` ✅ |
| root 调 `interpret`（判定权） | `ROLE_ACTION_DENIED` + 说明归 planner ✅ |
| knowledge 自己 `search_fill`（自造证据） | 被拒 ✅ |
| planner `interpret`（正常判断） | 正常执行 ✅ |
| design 调 `frame`（绕过插件改 phase） | `ROLE_ACTION_DENIED` ✅ |

**结论**：插件仍是唯一能改状态的通道；判定权仍归总计子代理；
传话员仍只能搬运真人原话。本轮的改动集中在**报错信息、出口判定、投递退避**，
没有放松任何权限。

---

## 五、部署

```powershell
.\sync-to-dsh.ps1 -Prune
python engineering\tools\verify_sync.py
# 然后【完全退出并重开 DSH】（插件 host 半是进程级）
```
