# 七阶段插件 · review 读取卡死（盲审死锁）修复 · 2026-11-20

> 现场报告：`C:\Users\j1877\Desktop\test\BUG-七阶段插件-review读取卡死-20261120.md`
> 任务：`DSH-ENG-MEMORY-V2`（`20261120_174510_D364ACCE`）· 一级圆柱齿轮减速器 · 七阶段
> 结局：**step 1.1（preflight）终止，63 步一步未 commit**，review 已 `action=blocked` 停机
> 本机状态文件佐证：`~/.dsh/.agent-presets/engineering/tools/seven_stage/20261120_174510_D364ACCE.json`
> （`phase=preflight` / `records=0` / `failures.review.cause=role-blocked` / 两次新 job 同样死锁）

---

## 一句话结论

**不是模型不肯审，是"读"这个动作被守卫整条封了 —— 而 status 同时又说它可用。**

```js
// flow.js（修复前）
if (action === 'read') {
  check(role !== 'review' || s.phase !== 'preflight', 'BLIND_REVIEW_INPUTS_ONLY');
}
```

盲审（preflight）的本意是 **"提交预审结论前不许看 knowledge/design 的正文"**，
但这条判据落成了 **"只要是 review 在 preflight，任何 read 一律拒"** ——
连"读原始需求"（盲审唯一且必须的输入）也一起封了。
于是 review 拿到了一个自相矛盾的现场：

| 出处 | 说的是 |
|---|---|
| `status.allowed` | 含 `read` |
| `nextAction` | "先 read 正文、再 submit" |
| 实际 `read` | **任何参数都是 `BLIND_REVIEW_INPUTS_ONLY`** |

现场实测盲试 **15+ 种参数变体**（`step` / `part=frame|knowledge|design|issues|…` / `jobId` / 空参）
全部同错，`token` 96h 有效且无法撤销 ⇒ 重派 `retry(review)` 也只是把同一个死锁重跑一遍。

> 与 [`fixes-20261119-seven-stage-slow-1of63.md`](./fixes-20261119-seven-stage-slow-1of63.md) 同源：
> 上一轮修好了"read 读不到未 commit 的小步"，这一轮暴露的是"read 在盲审阶段被整条拒"。
> 两次都指向同一条设计口径 —— **动作放行 ≠ 参数放行，两件事必须分开表达**。

---

## 结论速览

| 编号 | 标题 | 严重度 | 处置 |
|---|---|---|---|
| **Bug 1** | preflight 盲审把 `read` 整条拒掉（15+ 参数变体全灭） | **阻断/死锁** | ✅ 盲审改为**可读面白名单** |
| **Bug 1b** | `judge_output` 只有 0/1 两种终态，没有"我拒绝"的合法出口 | **阻断** | ✅ 新增 `verdict:"refused"`（仅 preflight） |
| **Bug 2** | 重复 `bind` 返回 `ROLE_BIND_DENIED`（实际已绑定） | 中 | ✅ 幂等 `ALREADY_BOUND` + 回显 id |
| **Bug 3** | note 要求"按 error 修正"，error 体无任何可操作字段 | 高 | ✅ `allowedParts` / `forbiddenParts` / `expectedParams` |
| **Bug 4** | `action=end` 报 `WORKER_END_REASONS_REQUIRED`，真实原因是"还有角色没登记结束" | 高 | ✅ 报 `pendingRoles` / `endedRoles` / `failedRoles` |

---

## Bug 1 · 盲审守卫：从"整条拒"改成"可读面白名单"

### 根因

`lib/flow.js` 的 read 守卫按 **角色 × 阶段** 一刀切，没有区分 **part**：

```js
check(role !== 'review' || s.phase !== 'preflight', 'BLIND_REVIEW_INPUTS_ONLY');
```

而 `status.allowed` 只表达 **动作名**（`allowedActions()` 里 `out.add('read')`），
完全不表达 **参数面** —— 于是 status 与守卫各说各话。
这正是报告里的推测之一："**allowed 与守卫逻辑两处未同步**"，**确认成立**。

### 修复：一个权威函数，两处共用

```js
// ── 唯一权威（lib/flow.js）────────────────────────────────────
const BLIND_PHASES = new Set(['preflight']);
const BLIND_READ_PARTS     = ['requirements','facts','step','frame','rules','outputs','searches','calculations'];
const BLIND_FORBIDDEN_PARTS= ['knowledge','design','review','issues','scope','preflight'];
const blindParts = () => BLIND_READ_PARTS.slice();
```

| 消费方 | 用法 |
|---|---|
| **守卫**（`action==='read'`） | `if (wanted && role==='review' && !blindParts().includes(wanted)) throw …` |
| **status**（`view()`） | `allowedParts:blindParts(), forbiddenParts:…, blind:true, blindNote:…` |
| **read 目录**（不带 part） | `{allowedParts, forbiddenParts, counts:{requirements,facts}}` |

**为什么白名单是这几个**：

| part | 该不该给盲审 | 理由 |
|---|---|---|
| `requirements` / `facts` | ✅ | 盲审的**唯一输入**就是原始需求与已确认事实 |
| `step` | ✅ | 本小步的七阶段原文（不是别人的结论） |
| `frame` | ✅ | planner 写的本步对象/边界，属于"要审什么"的定义 |
| `rules` / `outputs` / `searches` / `calculations` | ✅ | 规则原文、本步验收项、检索证据、本步计算 —— 均无他人结论 |
| `knowledge` / `design` | ❌ | **待审结论**。提交预审结论前看到它，独立复审就变成复读知识稿 |
| `review` / `issues` / `scope` | ❌ | 同上，属于"别人的判断" |

> 目录模式（不带 `part`）**放行且不泄露**：只返回 allowedParts 与计数，
> 不列 knowledge/design 的键名与字数。

### 修复后行为（行为级实测）

| 调用 | 修复前 | 修复后 |
|---|---|---|
| `read {part:"frame"}` | `BLIND_REVIEW_INPUTS_ONLY` | ✅ 返回正文 |
| `read {part:"requirements"}` | `BLIND_REVIEW_INPUTS_ONLY` | ✅ 返回原始需求 |
| `read {part:"facts"}` / `{part:"step"}` / `{part:"rules"}` | 全拒 | ✅ 返回正文 |
| `read {}`（目录） | 拒 | ✅ 返回 allowedParts + 计数 |
| `read {part:"knowledge"}` | 拒 | ❌ **仍拒**（独立性未破坏） |
| `read {part:"design"}` | 拒 | ❌ **仍拒** |

---

## Bug 1b · `judge_output` 的第三终态：`refused`

### 根因

旧实现的 review 结论只有 `pass | conditional | blocked` 三种，**没有任何合法出口能表达"我拒绝这个输入"**。
现场 reviewer 无论怎么措辞（列号、口语化、仅陈述、说明性回复、明确拒绝）都被同一条码拒掉，
最后只剩两条路：**违反盲审硬着头皮 submit（假通过）** 或 **`action=blocked` 停机**。
两条都是坏结局 —— 这正是"报错会停，假通过不会"的另一个变种。

### 修复

```js
const refusedNow = verdict === 'refused';
if (refusedNow) {
  check(job.kind === 'preflight', 'REFUSAL_NOT_ALLOWED: …');   // ← 只在盲审受理
  const refuseReason = named(data.content, 'content');
  s.slot.review  = {…, verdict:'refused', refused:true};
  s.slot.reviews.push(entry);
  evidenceWait(s, '反驳（review）明确拒绝本次盲审输入，理由：…；插件不会因此放行，也不改判 verdict。');
  return {refused:{step, reason, by:role, jobId}, phase:'evidence_wait', next:'…'};
}
```

### ⚠️ 为什么这不破坏插件独立性（关键设计取舍）

| 约束 | 强制点 | 理由 |
|---|---|---|
| **只在 `preflight` 受理** | `check(job.kind === 'preflight', 'REFUSAL_NOT_ALLOWED')` | 正式复审（phase=review）**必须**给出真实结论，那里没有"我拒绝"这个选项 |
| **受理 ≠ 放行** | `evidenceWait(s, …)`，**不** `advance` / **不** 改 `verdict` | 拒绝只把问题抬到上游，绝不把"没通过"变成"通过" |
| **不推进任何小步** | 实测 `progress.done === 0`、`ready === false`、`revision` 不变 | 拒绝不产生任何"已完成"的假象 |
| **出口仍在插件手里** | 转 `evidence_wait` 后只能走既有三个出口：`rework` / `frame` / `question`（真做不下去才 `partial`） | 处置权回到 planner（大脑）手上，不由 review 自己决定 |

**一句话**：模型获得了**"说不行"**的权利，但没有获得**"说通过"**的权利 —— 后者永远只在插件手里。

> 报告里给的三个候选方案（① 加第三终态；② 允许脱敏引用；③ 只在逐字命中时拦截），
> 本修复采纳 ①，**不采纳 ②③**：②③ 都是"放宽对输出的检查"，
> 而本插件成立的根基正是"结论只能由插件按证据裁定"。① 是唯一不削弱这条根基的做法。

---

## Bug 2 · 重复 `bind` 幂等

**修复前**：`check(a.parent===s.planner && s.phase==='setup' && roles.includes(d.role), 'ROLE_BIND_DENIED')`
—— "已经绑好了"与"你无权绑"共用一个码，调用方只能盲试。

**修复后**：先判"这一绑定是不是**确实属于你**"，是则幂等成功：

```js
const alreadyMine = roles.includes(role2) && s.members[role2] === a.id && a.parent === s.planner;
if (alreadyMine) return {...view(s, role2), alreadyBound:true, code:'ALREADY_BOUND',
  boundSessionId:a.id, note:'该角色已经绑定在【当前这个会话】上，本次按幂等处理…'};
```

其余情况**仍然拒绝**，并分成五种可区分的原因：

| 情形 | 错误信息 |
|---|---|
| 不是 planner 的直系子会话 | "只有【总计子代理（planner）的直系子会话】才能 bind" + 两边 parent/planner 实际值 |
| 阶段已过 `setup` | "bind 只在 setup 阶段受理，当前 phase=…" + `members` 实况，让调用方先对账 |
| role 非法 | 列出合法取值 |
| 角色已被别的会话占用 | 回显占用的 sessionId，并区分"你就是它"与"邀请已被用" |
| 不是为这个角色预留的会话 / 邀请码不对 | 分别报 `不是本任务为 … 预留的那个子会话` 与 `INVALID_OR_USED_INVITATION` |

**边界没放宽**：幂等只对"确实是它自己的绑定"成立，绝不因为"角色已被占"就放行别的会话（有回归锁定）。

---

## Bug 3 · 所有"参数被拒"的错误必须带可操作字段

报告原话："note 要求按 error 修正重试，但 error 体没有可操作字段 → 盲试 15+ 变体浪费回合。"

**两层修复**：

1. **flow 层**：`BLIND_REVIEW_INPUTS_ONLY` 与 `UNKNOWN_RECORD_PART` 都在异常对象上挂结构化线索
   （`e.allowedParts` / `e.forbiddenParts` / `e.expectedParams`），错误文本里同时用中文写出；
2. **index.js 层**：统一把异常上的线索**原样搬运**到错误体（**只搬运，不新增任何判定**）：

```js
const HINTS = ['allowedParts','forbiddenParts','expectedParams','pendingRoles','endedRoles','failedRoles'];
```

修复后的 `read` 拒绝体（实测）：

```json
{
  "ok": false,
  "code": "BLIND_REVIEW_INPUTS_ONLY",
  "allowedParts": ["requirements","facts","step","frame","rules","outputs","searches","calculations"],
  "forbiddenParts": ["knowledge","design","review","issues","scope","preflight"],
  "note": "…本次调用【参数】允许的取值见 allowedParts。期望的参数名见 expectedParams。…"
}
```

---

## Bug 4 · `action=end` 要说真实原因

**现场**：planner 按 `stepContract.required=["kind","reason"]` 原样传参，**连续 4 次**
返回 `WORKER_END_REASONS_REQUIRED` —— 这个码字面意思是"理由缺失"，而真实原因是
**还有角色没登记结束**。报告推测"疑似校验只在特定 phase/role 放行 end"，**部分成立**：
判据本身没错（确实要等三个角色收尾），错的是**它报了一个看不出真实原因的错误码**。

**修复后**：

```
WORKER_END_REASONS_REQUIRED: 你的 kind/reason 参数是合法的，被拒的真实原因是
【还有角色没有登记结束】：["design"]。这不是参数问题。
处置（二选一）：
  ① 先用 send_message 让这些角色各自 end{kind,reason}，等它们登记完再 end；
  ② 若它们确实干不下去，planner 可先 partial{reason} 收尾，
     或让对应角色 blocked{reason} 登记故障后再按 partial 结束。
```

并附 `pendingRoles` / `endedRoles` / `failedRoles` 三个结构化字段。

---

## 回归

```powershell
$env:PYTHONIOENCODING='utf-8'; $env:PYTHONUTF8='1'
node engineering\plugins\dsh-engineering-seven-stage\test\smoke.mjs
python engineering\tools\test_regression_seven_stage_wiring.py
python engineering\tools\run_regression.py     # 全量
```

| 测试 | 结果 |
|---|---|
| `smoke.mjs` | ✅ OK（63 步完整链不受影响） |
| `test_regression_seven_stage_wiring.py` | ✅ **PASS=223 / FAIL=0**（原 148 → 本轮新增 **F7 段 75 项**） |

### **先复现、再修复**的证据链

本轮刻意做了"同一份探针脚本分别跑旧代码与新代码"，确保修的是真问题：

| 探针项 | HEAD（修复前） | 修复后 |
|---|---|---|
| `status.allowedParts` | `undefined` | 8 项白名单 |
| `read part=frame/requirements/facts/step/rules` | 全部 `BLIND_REVIEW_INPUTS_ONLY` | 全部返回正文 |
| `read` 不带 part | `BLIND_REVIEW_INPUTS_ONLY` | 返回目录 + allowedParts |
| 重复 `bind` | `ROLE_BIND_DENIED` | `ALREADY_BOUND` |
| `submit{verdict:"refused"}`（preflight） | `INVALID_VERDICT` | 受理 → `evidence_wait`，`progress 0/63`、`ready=false` |
| 正式复审 `refused` | — | `REFUSAL_NOT_ALLOWED` |
| `end` 被拒 | 裸码 | `pendingRoles:["design"]` |

### F7 段覆盖（75 项）

- **源码断言 14 项**：盲审权威函数唯一、守卫与 status 同源、旧一刀切已删（按可执行代码判定，
  注释里提到旧写法是允许的）、待审结论仍在禁读面、bind 幂等分支、refused 只在 preflight、
  submit 回执必须转发、end 的 pendingRoles…
- **行为断言 61 项**：真跑一遍 `attach → launch → bind → frame → knowledge submit → preflight`，
  把上表逐条钉死；并额外锁定三条**反向**边界：
  - 盲审读 `knowledge` **仍被拒**（独立性）；
  - 正式复审 `refused` **仍被拒**（不能拿"我拒绝"逃避给结论）；
  - 非 planner 直系 / 已过 setup 的 `bind` **仍被拒**（幂等没放宽成"随便绑"）。

---

## 部署

```powershell
.\sync-to-dsh.ps1 -DryRun -Prune
.\sync-to-dsh.ps1 -Prune
python engineering\tools\verify_sync.py
# 然后【完全退出并重开 DSH】（插件 host 半是进程级）
```

> 现场任务的恢复方式（报告 §"当前状态与恢复步骤"）：
> 重开 DSH 后让 planner 执行 `retry {role:"review", reason:"插件已修复盲审读面"}`，
> 从 **step 1.1（preflight）** 继续；此前 `records=0`，没有任何已提交产物需要作废。

---

## 教训

1. **"动作放行"与"参数放行"是两件事，必须分开表达。**
   `allowed` 里有 `read` 不等于"任何 part 都能读" —— 而旧实现没有任何地方表达这个差别。
   现在 `allowed`（动作）与 `allowedParts`（面）并列出现，两者都来自插件、都不由模型决定。

2. **守卫与 status 必须同源。** 只要两边各写一份判断，就一定会漂移；
   而漂移的表现是"status 说可以、守卫说不行"这种**最消耗回合**的失败模式。

3. **"拒绝"必须是一个合法终态，否则模型只能撒谎或停机。**
   没有 `refused` 时，reviewer 的真实处境（"我看不到该看的东西"）无法表达，
   它要么硬着头皮 `pass`（假通过），要么 `blocked`（停机）—— 两条都比拒绝更糟。

4. **给"说不行"的权利，但不给"说通过"的权利。**
   `refused` 只在盲审受理、且只把问题抬给上游，处置权仍在 planner；
   这条边界是七阶段"插件是大脑"的物理基础，不能为了"让流程走通"而放宽。

5. **错误码要说真实原因，而不是最近的那个判据名。**
   `WORKER_END_REASONS_REQUIRED` 真实含义是"还有人没收尾"，
   叫成"理由缺失"会把调用方引到完全错误的方向（现场连试 4 次改参数）。
