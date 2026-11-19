# 七阶段分析插件 · 第三轮缺陷修复（2026-11-19）

> 现场报告：`C:\Users\j1877\Desktop\test\20261119_092838_C9B6D3FD\DSH_任务缺陷记录.md`
> 任务：单级圆柱齿轮减速器 · `20261119_092838_C9B6D3FD` · 插件 sha256 `9f680118…`
> 结局：任务在 **step 1.1 终止，63 步一步未 commit**
> 前两轮：[fixes-20261118-seven-stage-bugs.md](./fixes-20261118-seven-stage-bugs.md) · [fixes-20261119-seven-stage-bugs2.md](./fixes-20261119-seven-stage-bugs2.md)

---

## 一句话结论

**这份任务没有失败在设计，失败在通道。**
两条阻断级缺陷叠加（BUG-01 + BUG-02），任一条单独修都不够。

---

## 结论速览

| 编号 | 标题 | 严重度 | 处置 |
|---|---|---|---|
| **BUG-01** | `interpret.parameters` 文档说对象、代码要字符串 | 阻断 | ✅ `parameters` 走宽松通道，接受任意 JSON |
| **BUG-02** | `capture` 读不到 `ask_user_question` 的答案 | 阻断 | ✅ 新增结构化答案采集通道 |
| **BUG-03** | `SEARCH_REQUIRED` × 无联网权 = 死锁循环 | 高 | ✅ 证据按小步累计 |
| **BUG-04** | `blocked` 无自有 job 时报 `ACTIVE_JOB_REQUIRED` | 中 | ✅ planner 在任何非终态都可登记 |
| **BUG-05** | 门禁拦掉 `wait_agent`/`send_message` | 中 | ✅ 只读工具白名单 |
| **BUG-06** | `rework` 拒 owner=planner 的异议 | 高 | ✅ 支持回 frame 重写 |
| **BUG-07** | `jobId`/`orderId` 语义重叠 | 中 | ✅ actionHelp 写死取值 |
| **BUG-08** | `gate_load_case.json` 跨任务残留 | 高 | ✅ init 归档 stale |
| **BUG-09** | `design_domain` Z 向偏小 | 中 | ✅ 支持显式三向覆盖 |
| **BUG-10** | `flow_order` 残留「十阶段」 | 低 | ✅ 5 处文案统一 |
| **BUG-11** | `orphanedJob` 不自愈 | 中 | ✅ cause 分类 + 自动重派一次 |
| **BUG-12** | 已 filled 工单被重推 | 中 | ✅ 通知前核实时状态 + 幂等 |
| **BUG-13** | `retry` 丢弃已回填证据 | 高 | ✅ 继承小步级证据 |
| **REV08** | `cycles_per_year` 默认值错两个数量级 | **最危险** | ✅ 按 task_type 区分，拿不到就留空 |

---

## BUG-01 · `parameters` 类型黑洞

**根因**：`fields()` 对所有字段调 `named()`，后者只收"非空字符串"。
而 `actionHelp` 里写的是裸字段名 `parameters`，与 `outputs:{…}` 这类确实要对象的字段排版一致，
极易被理解成结构化数据。报错又只有 `INVALID_FIELD: parameters`——
**不说期望什么类型，也不说收到什么**，模型只能盲试（现场试了 8 种形态全灭）。

**修法**：
```js
const LOOSE_FIELDS = new Set(['parameters']);   // 只有这一个字段放宽
const fields = (obj, keys) => Object.fromEntries(keys.map(k =>
  [k, LOOSE_FIELDS.has(k) ? loose(obj?.[k], k) : named(obj?.[k], k)]));
```

- `loose()` 接受 字符串 / 对象 / 数组 / 数字 → 对象与数组内部 `JSON.stringify` 后按字符串存；
- `named()` 的报错改成带**实际类型 + 期望类型 + 值摘要**：

```
INVALID_FIELD: parameters 必须是【非空字符串】，实际收到 array（[1,2]）。
若这是结构化数据，请用 JSON 字符串形式传入。
```

- 为什么只放宽 `parameters`：其余字段（`conflict/changed/impact/reason`）是"必须有人写清
  的自然语言理由"，放宽会直接毁掉七阶段的证据质量。

---

## BUG-02 · 人机通道被堵死（最关键）

**根因**（`lib/index.js` 旧 `directUser`）：

```js
.filter(m => m.role==='user' && m.source?.kind==='user' && m.id)
```

而 `ask_user_question` 的答复是 **tool result**：
`role:'tool'` + `source:{kind:'tool',callId}` + `content` 里一段 `{answers:[{id,selected,custom}]}`。
旧过滤器把它整条丢弃 —— 用户答得再明确，插件也收不到。

**修法**：增加第二条采集通道，但**严格限定为 `ask_user_question` 的成功输出**。

```js
const askAnswerText = m => {
  if (m?.role !== 'tool') return null;
  if (m?.source?.kind !== 'tool') return null;
  const raw = (m.content ?? []).filter(x=>x.type==='text').map(x=>x.text).join('').trim();
  const json = JSON.parse(raw);            // 失败就 return null
  const answers = json?.answers;
  if (!Array.isArray(answers) || !answers.length) return null;
  // 严格形状：任何一项不对就整条不收（宁可漏，不可误收）
  if (!answers.every(a => typeof a.id === 'string' && Array.isArray(a.selected))) return null;
  return answers.map(a => '[' + a.id + '] ' + pick + (custom ? ' / ' + custom : '')).join('\n');
};
```

采集结果示例：

```
[delivery] 数字化交付
[power] 3kW / n1=1450 r/min
[skipme] （用户跳过该题）
```

**三条安全约束**（为什么没放宽到"任意 tool result"）：

| 约束 | 强制点 | 理由 |
|---|---|---|
| 只认 `ask_user_question` 的输出形状 | `{answers:[{id,selected,custom?}]}` 精确匹配 | 放宽 = 模型自己写的工具输出也会被当"真人原话"，capture 的全部意义就没了 |
| 仍然只有 `root` 能 `capture` | `as('root')` 不变 | 传话员地位不动 |
| 来源单独标注 | `source:'user-question'` | 复核时要能分清"用户打的字"和"卡片上点/填的答案" |

`capture` 失败时的报错也改了：会回显**最近 5 条可采集记录**，而不是一句冷冰冰的
`ROOT_USER_MESSAGE_NOT_AVAILABLE`。

---

## BUG-03 / BUG-13 · 检索证据必须按"小步"而不是"job"存活

**根因**：`job.searches` 挂在 job 上，而 `retry` 的 `reissue()` 新建 job（`searches:[]`）。
于是"发单→回填→failed→retry"之后，**本步明明搜过**却被判 `SEARCH_REQUIRED`，
只能再发一张工单 —— 现场同一主题连发 3 张，step 1.1 空转 4 轮。

**修法（双层）**：

1. `reissue()` 同小步继承：取 `slot.searches` ∪ `prev.searches` 的并集（按 orderId+query 去重）；
2. `submit` 的判据改成**小步级**：

```js
const stepSearches = (s.slot?.searches ?? []);
const allSearches  = [...stepSearches, ...(job.searches ?? [])];
if (!allSearches.length) throw Error('SEARCH_REQUIRED: ...');
```

`slot = {frame, issues, reviews, rework, searches:[], calculations:[]}` ——
`searches/calculations` 挂在**小步**上，因为"这一步搜过什么"是既成事实，不该随委派作废。

---

## BUG-11 · 失败必须分"谁的问题"

**根因**：三类完全不同的失败混在同一个 `failed` 字段里：

| 类型 | 含义 | 正确处置 |
|---|---|---|
| `host-disposed` | 宿主回收会话 | **自动重派一次** |
| `role-blocked` | 角色自己判断干不下去 | 必须人管 |
| `run-failed` | 子代理运行失败 | 看情况 |

**修法**：`interrupt(a, task, reason, {cause})` + `autoReissue()`：

```js
function autoReissue(a, task, role) {
  const f = s.failures?.[role];
  if (!f || f.cause !== 'host-disposed') return null;   // 只处理宿主回收
  if (f.autoRetried) return null;                        // 一生只一次
  if ((s.retryCounts?.[role] ?? 0) >= 1) return null;
  ...
}
```

**边界不放宽的理由**：`role-blocked` 自动重派等于把人的决策权抢走；
`host-disposed` 不自动重派则会让"宿主回收"变成"必须人工介入"，白耗轮次。
所以严格二分，各走各的路。

---

## BUG-12 · 已完成的工单不该再被推送

**现场**：主对话被引导去回填一张 `2026-11-19T01:40:17` **已经 filled** 的工单
（`hitCount:8, outcome:ok`），白跑一次 `web_search` + 一次失败的 `search_fill`，
还会误判成"我漏填了"。

**修法两道**：

1. 推送前**读磁盘实时状态**（不能用动作开始前的快照）：
```js
const liveNow = flow.load(s.taskId);
const order = liveNow?.jobs?.knowledge?.searchOrder;
if (order && order.status === 'pending' && ...) notify(...);
```
2. `search_fill` 对**同一 orderId** 做幂等：
```js
const already = allSearches.find(x => x?.orderId === data.orderId);
if (already) return {...already, idempotent:true, note:'该 orderId 已经回填过'};
```

注意幂等必须核对 **orderId**，否则就变成"随便塞一份结果也认"。

---

## BUG-04 / 05 / 06 · 三个"明明是合法操作却被拒"

### BUG-04：planner 不能登记阻塞

`blocked` 只认 `s.jobs[role]`，而 planner 在 `resolve`/`frame` 阶段本来就不持有 job。
结果 planner 想上报"我卡了"被拒，只能退化成 `yield` ——
而 `yield`（我在等）和 `blocked`（我卡了）对上游的含义**完全相反**。

```js
const plannerCanReport = role === 'planner' && !terminal.has(s.phase);
check(s.jobs[role] || owns || plannerCanReport || ..., 'ACTIVE_JOB_REQUIRED');
```

三个执行角色仍要求"确实持有活"——它们没活的时候不该刷故障。

### BUG-05：只读工具白名单

```js
const READONLY_TOOLS = new Set(['wait_agent', 'send_message', 'list_agents']);
```

**为什么只有这三个**：它们不改任何流程状态。
- `wait_agent` —— 只阻塞等待
- `send_message` —— 给已存在会话发消息
- `list_agents` —— 纯只读，用来确认上游是否还活着

**明确不放**：`write/edit/pwsh/run_code`（能改磁盘）、`todo_write`（会把计划写进会话可见面，
容易被当成"已登记的结论"，与插件里的 `records` 混淆）。

### BUG-06：owner=planner 的异议无处置路径

原判据 `['knowledge','design'].includes(issue.owner)` 把 owner=planner 的硬异议挡在门外，
而 `commit` 在 `resolve` 阶段又被 `WRONG_PHASE` 拒 —— 只剩 `question` 一条路，
可"漏掉用户第二句需求"根本不需要问用户。

**修法分流**（仍是插件决定，不是模型自由发挥）：

| owner | 处置 |
|---|---|
| knowledge / design | 派回该角色（原路径） |
| **planner** | **回到 frame，由 planner 重写本步对象/边界** |
| human | 明确拒绝：`HUMAN_OBJECTION_NEEDS_QUESTION`，必须走 question |

`slot.issues` 不清空 —— 那些异议要留到下一次 review 逐条核销。

---

## BUG-08 / 09 / 10 · 静默的错误基准

### BUG-08：loadCase 跨任务残留（最隐蔽）

**现场**：新任务 `init` 后，`gate_load_case.json` 里躺着上一轮"小型压装机"的内容
（Q235 / 50000N / `[100,100,100]` / 汽车语义三向工况）。
不手动重跑 `provide_context`，减速器就会被按 **50kN 压装力、100mm 立方域、Q235** 校核，
**强度与寿命结论全错，而且门禁不报错**。

> "静默的错误基准"比报错危险得多 —— 报错会停，错基准会一路跑到底。

**修法**：`init` 时检查 `meta.task_id` / `title`，判定属于其它任务就**归档到
`load_cases/.stale/<时间戳>-gate_load_case.json`**（不是删除 —— 保留证据可追溯），
并把 `load_case_stale_archived` 写进 state。

### BUG-09：设计域 Z 向偏小

自动推算的 `design_domain_mm` 来自"零件包络"提取，分不清**平面尺寸**与**轴向/高度尺寸**。
实测减速器算成 `[420,300,160]`（160 来自中心距/齿宽），而箱体总高实际 340。
域给小的后果：物理校核判"零件出域"，而零件其实是对的 —— **排查方向被彻底带偏**。

**修法**：支持显式三向覆盖，四种写法都认：

```
设计域 420x300x340 mm
设计域 420×300×340
包络 L=420 W=300 H=340
设计域 420 300 340
```

命中就覆盖自动值并在 state 留痕（`design_domain_override` + `design_domain_auto`）。
**拒绝只给一个方向** —— 部分覆盖比全覆盖更容易出错。

### BUG-10：文案残留

`flow_order` 等 5 处用户可见文案里的「十阶段」改成「七阶段」。
历史注释里的「十阶段」保留 —— 那是在说明"删掉了什么"，是对的。

---

## REV08 · 最危险的一条：疲劳校核虚假通过

> 这是本轮 review 在 preflight 抓到的 **hard 异议**，报告 4.6 节。
> 它比所有其他缺陷都危险 —— 因为**报错会停，假通过不会**。

**现场计算**：
```
cycles_per_year = 200000，n2 = 362.5 r/min
→ 200000 / (362.5 × 60) = 9.2 小时/年
→ 30 年合计 275.9 小时
```
而 frame 里写的是「10 年单班 ≈ 20000 h」，**差两个数量级**。
后果：疲劳校核因"循环次数太少"而**平凡通过**，结论写着"寿命充裕"，实际远超。

**根因**：默认值 `200000 次/年` 是按**间歇动作机构**（气缸/夹爪/机械臂每次动作算一次）
的口径给的，套到**连续回转**的传动件上直接错两个数量级。

**修法**：

```python
if task_type == "transmission":          # 连续回转件
    _rpm = _extract_rpm_for_cycles(t, context)
    _hours = _extract_work_hours_per_year(t)
    if _rpm and _hours:
        cycles_per_year = _rpm * 60 * _hours       # 由转速推导
    else:
        cycles_per_year = None                     # 拒绝给错值
        cycles_unknown_reason = "传动类任务拿不到转速，不采用间歇机构默认值……"
```

并且把落盘处的 `or 200000.0` 也删掉：

```python
# 旧（静默兜底，最危险）
"operating_cycles_per_year": float(mechanics.get("cycles_per_year") or 200000.0),
# 新（拿不到就是 None，physics 会跳过并报"未执行"）
"operating_cycles_per_year": (float(mechanics["cycles_per_year"])
    if mechanics.get("cycles_per_year") is not None else None),
"operating_cycles_unknown_reason": mechanics.get("cycles_per_year_unknown"),
"fatigue_check_blocked": (cycles is None and load_type in ("cyclic","impact")),
```

**验证**（与报告 4.7 节的人工修正值完全一致）：

| 场景 | task_type | cycles_per_year | 自洽校验 |
|---|---|---|---|
| 减速器，无转速 | transmission | `None` + 原因 | — |
| 减速器 + `n2=362.5 r/min` + 单班 | transmission | **43,500,000** | 43500000/362.5/60 = **2000 h/年** ✓ |
| 气动夹爪，间歇运行 | robot_arm | 200,000（合理） | — |

> **宁可不给，也不给错**：留空会让疲劳校核跳过并报"未执行"（诚实的失败），
> 填错值会给"寿命充裕"（虚假的通过）。

---

## 回归

```powershell
$env:PYTHONIOENCODING='utf-8'; $env:PYTHONUTF8='1'
node engineering\plugins\dsh-engineering-seven-stage\test\smoke.mjs
python engineering\tools\test_regression_seven_stage_wiring.py
```

| 测试 | 结果 |
|---|---|
| `smoke.mjs` | ✅ OK |
| `test_regression_seven_stage_wiring.py` | ✅ **PASS=148 / FAIL=0**（新增 F6 段：25 项源码断言 + 24 项行为断言） |
| 63 步端到端 | ✅ `phase=ready` / `progress 63/63` / `completions=5` |

F6 行为验证覆盖：

- **BUG-02**：capture 读到 3 个答案、自定义答案被带上、跳过项被标注、**普通 tool 输出未被误收**；
- **BUG-01**：parameters 的 对象/数组/数字/字符串/嵌套 五种类型全部接受；
- **BUG-05**：wait_agent/send_message/list_agents 放行；write/pwsh/todo_write 仍被拦；
- **BUG-12**：同一 orderId 重复回填返回 `idempotent:true`；
- **BUG-13/03**：retry 后本小步证据仍在、新 job 继承证据、无需重搜即可 submit；
- **BUG-11**：角色自报阻塞标 `cause:'role-blocked'`。

---

## 部署

```powershell
.\sync-to-dsh.ps1 -DryRun -Prune
.\sync-to-dsh.ps1 -Prune
python engineering\tools\verify_sync.py
# 然后【完全退出并重开 DSH】
```

---

## 对第 4.5 节自我纠正的回应

报告里 planner 曾误判"死锁已确认（代码级证据）"，理由是
`retry(review)` 返 `NO_ROLE_FAILURE_TO_RETRY`、`read(1.1)` 返 `CURRENT_RECORD_NOT_FOUND`。
主对话直接读磁盘状态文件推翻了它，并总结：

> `NO_ROLE_FAILURE_TO_RETRY` 的含义是「没有失败，所以没东西可 retry」，
> **不是**「角色死了」。`status` 里的 `waitingOn` 才是权威判据，
> `failures=[]` 是**健康信号**而非死锁证据。

**这个判断完全正确，已作为设计口径保留**。本轮修复也沿用了同一原则：
- `waitingOn` 继续作为唯一权威判据；
- 新增的 `cause` 字段只是把"哪类失败"写清楚，不改变"先看 waitingOn"的优先级；
- `autoReissue` 也必须先看 `cause`，绝不因为"有 failed 就自动重派"。
