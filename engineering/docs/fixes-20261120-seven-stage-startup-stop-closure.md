# 七阶段插件 · 启动期堵死 / stop 语义 / closed 台账（2026-11-20 第四轮）

> 现场文档（三份，同一轮测试）：
> · `C:\Users\j1877\Desktop\test\BUG测试结果_20261120.md`（BUG-1~5）
> · `C:\Users\j1877\Desktop\test\BUG-总结-七阶段插件测试.md`（A~F 节）
> · `C:\Users\j1877\Desktop\test\BUG-七阶段插件-preflight-part白名单与verdict判据-20261120.md`（Bug A~F）
>
> 前一轮：[fixes-20261120-seven-stage-blind-review.md](./fixes-20261120-seven-stage-blind-review.md)（盲审读死锁）

---

## 一句话结论

**四条都指向同一个设计缺口：插件把"状态"和"会话/意志"混在了一起。**

| 混淆 | 表现 |
|---|---|
| 状态文件已落盘 ↔ 会话还活着 | 僵尸 planner 让新任务 `TASK_ALREADY_BOUND`，只能手删文件 |
| 活跃会话 ↔ 建模房间 | `OTHER_ROOMS_ACTIVE` 报了"房间"，真相是会话 |
| 用户叫停 ↔ 角色故障 | `stop` 缺失，`blocked` 被当成停机手段，planner 被标 failed |
| 上一版产物 ↔ 本轮 frame | frame 重写把异议台账抹掉，`closed` 永远关不了 |

修法的共同口径：**把这些区分显式建模成插件里的判定**，而不是让模型去猜或让人去手删文件。

---

## 结论速览

| 编号 | 标题 | 严重度 | 处置 |
|---|---|---|---|
| **1** | 残留状态 → `TASK_ALREADY_BOUND`（僵尸 planner 绑死新任务） | **P0** | ✅ attach 识别失效绑定并接管；新增 `clean` 动作 |
| **1b** | `OTHER_ROOMS_ACTIVE` 不说是谁在挡 | **P0** | ✅ 列出 `blockers`（sessionId/label/activity）+ 处置步骤 |
| **2** | 双/多总计子代理竞争同一 taskId | **P0** | ✅ launch 只认绑定的那个 planner；attach 明确竞争诊断 |
| **3** | `BLIND_REVIEW_INPUTS_ONLY` 全量拒 read | 死锁级 | ✅ **上一轮已修**，本轮加 5 条回归锁死 |
| **4** | 缺正式 stop/abort 语义 | **P0** | ✅ 新增 `stop`（不写 failures）+ blocked 进 allowed |
| **5** | `closed[]` 恒 `INVALID_CLOSURE_REFERENCE` | P1（潜在死循环） | ✅ frame 保台账 + 错误体给真实可关闭集 |
| **5b** | preflight 下 `kind=unknown` 必被强制 blocked | 待确认 | ✅ **确认是有意设计**；但降级必须留痕（`downgrades`） |

---

## 1 · 残留状态导致 TASK_ALREADY_BOUND

### 根因

`attach` 的旧判据是一行：

```js
if (s) {
  check(s.root === a.id && s.planner === d.planner, 'TASK_ALREADY_BOUND');
  return view(s,'root');
}
```

只要状态文件存在且 planner 对不上，就**无差别拒绝**。而现场恰恰是：
上一轮 `attach` 成功后只 `yield` 就被 `OTHER_ROOMS_ACTIVE` 挡下 —— **状态文件已经落盘，planner 却已经 inactive**。
新 planner 接不上，文件里也没有"可以覆盖"的标记，唯一出路是 `Remove-Item seven_stage\*.json`。

### 修复

attach 现在分四种情况，**判定依据来自宿主证据，不由模型自报**：

| 情形 | 处置 |
|---|---|
| 同 root + 同 planner | 幂等返回（原路径不变） |
| **不同 root** | 拒绝 —— 那是另一个真实会话名下的任务，不能抢 |
| 同 root + 旧 planner **仍存活** | 拒绝，并说明"同一 taskId 不允许两个大脑" |
| 同 root + 旧 planner **已确认失效** | **接管**：换绑 planner，旧账归档进 history，已提交小步全部保留 |

关键实现：`index.js` 把宿主侧的存活情况作为**输入**传给 flow：

```js
let livePlanners;
if (oldPl && ctx.agents && typeof ctx.agents.get === 'function') {
  const live = actorOf(ctx.agents.get(oldPl));
  livePlanners = (live && live.parent === a.id) ? [String(oldPl)] : [];
}   // 查不到就保持 undefined
```

> ⚠️ **`[]` 与 `undefined` 含义相反，绝不能混**：
> `[]` = "查过了，它不在"（**可以**接管）；
> `undefined` = "没法查"（**不许**接管，保守拒绝并指出 `clean`）。
> 这个区分有专门的回归断言。

### 新增 `clean` 动作（替代手删文件）

```
action=clean {}                                  → dry-run，列出目录里所有任务
action=clean {taskId:"…", force:true}            → 归档到 .stale/ 后删除
```

三条硬约束：

1. **默认只看不删**（`dryRun`），真删必须显式 `force:true`；
2. **一次只删一个 taskId** —— 不提供"清空目录"的批量入口（那是事故放大器）；
3. **只有该任务自己的 root / planner 能删**，且删除前先归档到 `.stale/`（保留证据）。

`clean` 被刻意放在**所有绑定校验之前**：它要处理的正是"绑定已经烂掉"的现场，
否则 `WRONG_BOUND_TASK` 会先把清理入口挡死。

---

## 1b · OTHER_ROOMS_ACTIVE 不说是谁在挡

**现场**：launch 报"其他房间未排空"，而 `room-board --json` 显示 rooms 为空。

**根因**：这条判据看的是**活跃子会话**，不是建模房间 —— 两套词汇混用，排查方向被彻底带偏。
而错误里又没有任何"哪个 session / 怎么处置"的信息。

**修复**：把挡路的会话逐个列出来，并显式辟谣：

```
OTHER_ROOMS_ACTIVE_DRAIN_BEFORE_ANALYSIS: 分析开始前还有 2 个活跃子会话不是本任务的成员。
⚠️ 注意：这条判据看的是【活跃会话】，不是建模房间 —— room-board 显示 rooms 为空并不代表这里会放行。
挡路的会话（sessionId / label / activity）：[{"sessionId":"…","label":"agent-1","activity":"working"}]
处置（按顺序试）：① … ② … ③ action=clean …
```

错误体同时带 `blockers` 与 `howToFix` 两个结构化字段。

---

## 2 · 多总计子代理竞争同一 taskId

**根因**：`launch` 只查 `bound?.role==='planner'`。而 `bound` 来自 `trackedActor`，
它对"planner 的子会话"一律认角色 —— 于是**任何**一个 fork 出来的子代理都可能发大脑动作。

**修复**：launch 前显式核对"你就是状态里记的那一个"：

```js
if (bound?.role === 'planner' && prior && String(a.id) !== String(prior.planner)) {
  // PLANNER_SETUP_REQUIRED，并说明：
  //   你是新 fork 的替代 planner → 先 attach（会接管）
  //   你是误开的第二个         → 直接结束本会话
}
```

> 关于 `subagent_fork` 不返回 sessionId（报告 BUG-4）：那是**宿主**的行为，
> 插件改不了。但插件已经把取号路径写进 `REAL_DIRECT_CHILD_REQUIRED` 的报错里
> （list_agents / `~/.dsh/sessions/`），并支持按显示名唯一自动解析 —— `attach` 传 `"agent-1"` 也能用。

---

## 3 · BLIND_REVIEW_INPUTS_ONLY（回归项）

上一轮已修：盲审从"整条拒 read"改成"**可读面白名单**"，守卫与 `status.allowedParts` 同源。

本轮**没有改它**，并加了 5 条回归断言，防止后续改动把它改回去：

```python
ck("★ 回归：盲审白名单仍在（不许为了修别的把它放开）", …)
ck("★ 回归：盲审仍挡住待审结论", …)
ck("★ 回归：refused 仍只在 preflight 受理", …)
ck("★ 回归：重复 bind 仍幂等", …)
ck("★ 回归：end 仍给 pendingRoles", …)
```

---

## 4 · 缺正式 stop/abort 语义

### 根因

旧实现里，planner 想停下来只有两条路，**两条都是错的**：

| 尝试 | 结果 | 问题 |
|---|---|---|
| `yield` | `PLANNER_ACTION_PENDING` | 提示"先 rework 再 yield"，而 rework 会把小步打回 design **继续烧 token** |
| `blocked` | 接受，但登记 `failed.planner` | **"我想停"被记成"我失败了"** |

而且 `allowed` 里**始终没有 blocked**，与"实际能调成功"自相矛盾 —— 模型只能靠试。

### 修复：把三件事彻底分开

| 动作 | 语义 | 写进哪 |
|---|---|---|
| `stop` | **用户/上游叫停** | `history[].stopped`，**不写 failures** |
| `yield` | 我在等外部回执 | `turns` |
| `blocked` | 我判断干不下去（故障） | `failures` |

```js
case 'stop': {
  as('planner','root');          // ← 分析角色不能自己 stop
  check(!terminal.has(s.phase), 'ALREADY_TERMINAL: …用 reopen');
  const why = named(d.reason,'reason');
  s.history.push({…, stopped:true, stoppedBy:role, stoppedByActor:a.id});
  s.phase = 'partial'; s.jobs = {};
  // 关键：**不写 failures** —— 停止不是故障
  out.stopped = {by:role, reason:why, step, progress};
}
```

同时修正三处：

1. `allowed` 现在**按真实实现**列出 `blocked`（planner 任何非终态都能 blocked）与 `stop`；
2. `yield` 的拒绝提示改成三分支指路，**不再让人去 rework**；
3. `stop` 的契约写进 `actionHelp` / `ACTION_CONTRACT`，明确"停止≠故障≠等待"。

### 恢复路径不变

stop 之后 phase 是 `partial`（与旧 partial 一致），
用 `reopen {reason}` 即可从当前小步恢复 —— **已提交的小步全部保留**。

---

## 5 · closed[] 恒不可用（潜在死循环）

### 根因（本轮最隐蔽的一条）

`rework`（owner=planner）→ phase=frame → planner 调 `frame` 重写本步对象与边界：

```js
// 修复前
s.slot = {frame:frameFields(d), issues:{}, reviews:[], rework:0, searches:[], calculations:[]};
//          ↑ 整块替换 —— 上一轮的异议台账被一起抹掉
```

于是下一轮 review 提交 `closed:[…]` 时，`prior = Object.values(s.slot.issues)` 是**空数组**：

- 任何 key 都"没提过" → `INVALID_CLOSURE_REFERENCE`；
- 错误信息里的"可关闭 key"永远是 `[]` → 调用方无从自纠。

**现场只能把"已解决"写进正文**；一旦有谁按 `closed` 计数判放行，就是死循环。

### 修复 A：frame 保留台账

```js
const carried = s.slot ? {
  issues: s.slot.issues ?? {},
  reviews: s.slot.reviews ?? [],
  preflight: s.slot.preflight ?? null,
  rework / reworkReason / searches / calculations,
  reframes: (s.slot.reframes ?? 0) + (s.slot.frame ? 1 : 0),
} : {issues:{}, …};
s.slot = {frame:frameFields(d), ...carried};
```

**口径**：frame 改的是"本步对象与边界"，不是"把上轮的账抹掉"。
异议是对**上一版产物**的判定，必须活到下一次 review 逐条核销为止。
只保留**同一小步内**的账 —— cursor 前进后 commit 会置 `slot=null`，新小步天然拿不到旧的。

重写 frame 时如有未核销异议，回执会带 `carriedIssues` 提醒。

### 修复 B：错误体给真实可关闭集合

三种 key 分开报，每种都带"为什么"：

| 字段 | 含义 |
|---|---|
| `closableKeys` | 上一轮提出、本轮未重提 ⇒ **现在就能关**（带 kind/owner） |
| `reopenedKeys` | 本轮又提 ⇒ 仍成立，不能用 closed 抹掉 |
| `unknownKeys` | 上一轮没提过 ⇒ 不是有效关闭依据 |

若 `closableKeys` 为空，额外说明："必须重新提出并写明理由，或等下一轮再核销；
把'已解决'只写进正文不会被记入 closed 台账。"

---

## 5b · kind=unknown 必被强制 blocked —— **确认是有意设计**

报告的疑问成立（"降级成了唯一推进手段会掩盖真 unknown"），但**判据本身保持不变**：

> `hard/unknown` 意味着"这件事现在没有依据"，而七阶段的口径是"没有依据就不许登记通过"。
> 若允许 unknown 走 conditional，等于给"我不知道"发通行证，后面 62 步都会建立在它上面。

**改为加留痕而不是放宽**：本轮把上一轮 `hard|unknown` 的异议以 `condition/preference` 重新提出 →
记进 review 条目的 `downgrades`（`{key, from, to}`），并在 `VERDICT_ISSUE_MISMATCH`
的报错里一并回显。这样"谁在哪一步把 unknown 降级了"事后可查，但降级**不产生任何放行**。

报错同时加了一句引导："如果你认为某条 unknown 其实已有依据，请在 issues 里给出 evidence，
而不是改成 condition。"

---

## 回归

```powershell
$env:PYTHONIOENCODING='utf-8'; $env:PYTHONUTF8='1'
node engineering\plugins\dsh-engineering-seven-stage\test\smoke.mjs
python engineering\tools\test_regression_seven_stage_wiring.py
python engineering\tools\run_regression.py
```

| 测试 | 结果 |
|---|---|
| `smoke.mjs` | ✅ OK（63 步完整链不受影响） |
| `test_regression_seven_stage_wiring.py` | ✅ **PASS=246 / FAIL=0**（223 → 246，新增 F8 段 23 项） |

### 行为级实测（探针，非字符串断言）

| 场景 | 结果 |
|---|---|
| commit 阶段 `yield`（有待办） | `PLANNER_ACTION_PENDING`，提示含 `action=stop` 与 `blocked`，**不再只推 rework** |
| `stop` | `phase=partial`、`failures={}`、`history.stopped=true`、`stopped.by='planner'` |
| 分析角色调 `stop` | `ROLE_ACTION_DENIED`（只有 planner/root 能停） |
| 旧 planner 仍存活时 attach | `TASK_ALREADY_BOUND` + `plannerAlive=true` + `howToFix` |
| 旧 planner 失效后 attach | **接管成功**，planner 换绑，旧账进 history，产物保留 |
| 非绑定 planner 的 launch | 被拒（`PLANNER_SETUP_REQUIRED`） |
| `clean {}`（dry-run） | 列出目录任务，**不删** |
| `clean {taskId, force:true}` | 归档到 `.stale/` 后删除 |
| rework → frame 重写 | **issues 台账仍在**，回执带 `carriedIssues` |
| 用 `closed` 关闭上一轮异议 | ✅ **接受**，落入 `slot.review.closed` |
| 关闭本轮重提的 key | 拒绝 + `reopenedKeys` |
| 关闭不存在的 key | 拒绝 + `unknownKeys` |

---

## 部署

```powershell
.\sync-to-dsh.ps1 -DryRun -Prune
.\sync-to-dsh.ps1 -Prune
python engineering\tools\verify_sync.py
# 然后【完全退出并重开 DSH】（插件 host 半是进程级）
```

> 现场任务已无残留可救（状态文件此前被手工删除），重开后：
> `workflow_gate.py init` → 单次 `subagent_fork` + `attach`（同一条回复里连着做）→ `launch`。

---

## 教训

1. **"状态文件存在"不等于"会话还活着"。** 绑定关系必须能随时间失效，
   而失效判定只能来自宿主证据 —— 让模型自报"我才是真的 planner"等于取消隔离。

2. **错误码要点名"谁在挡"。** `OTHER_ROOMS_ACTIVE` 说"房间"而实际是"会话"，
   直接把排查引到了 `room-board` 这条错路上，白烧了很多轮。

3. **停止是一种意志，不是故障。** 缺 `stop` 时，用户叫停只能伪装成 `blocked`，
   于是"我想停"被永久写进 `failed` 台账 —— 这条污染会跟着任务走到最后。

4. **`allowed` 必须与实现严格一致。** `blocked` 能调成功却不在清单里，
   会让模型在"能不能做"上反复试探；清单与实现不一致本身就是 bug。

5. **"重写本步"不等于"抹掉上轮的账"。** 异议台账是对上一版产物的判定，
   它必须活到被逐条核销为止 —— 否则 `closed` 机制形同虚设，而依赖它计数的地方就会死循环。

6. **判据不弱化，但降级要留痕。** unknown → condition 是真实存在的规避手段，
   与其假装没有，不如把它记下来：既能事后追溯，又不给"我不知道"发通行证。
---

# 附录 · 测试方复核后的残余修复（第五轮）

> 测试方复核结论（`BUG-总结-七阶段插件测试.md` 2026-11-20 19:28 版）：
> - **F 节 → 🔶 部分修复**：✅ allowed 新增 blocked+stop、yield 实测可过；❌ 残余 a/b/c 三条。
> - **D14 → 🔶 修复待验证**：只读路径不可达，需作者给验证入口。
>
> **三条残余全部成立，已修完并用实调验证。**

## 残余 a · `actionHelp` 没有 stop 的契约

**现象**：`allowed` 里已经有 `stop`，但 `actionHelp` 的键只到 `partial` / `end`。
子代理知道「有个叫 stop 的动作」，但不知道要不要传 reason、和 blocked 差在哪 ——
于是要么不敢用，要么**拿它当 blocked 用**，而那正是 F 节要消灭的语义污染。

**修复**：补 `stop` / `clean` / `clearFailed` 三个契约，并把三种停止语义的差别写进文本：

| 动作 | 契约里写明的语义 |
|---|---|
| `stop` | **用户/上游叫停**：不写 failures（停止≠故障），产物保留，phase→partial |
| `blocked` | **我判断干不下去**（故障）：写进 failures，需要人 retry |
| `yield` | **我在等外部回执**：回合结束，靠插件投递开新回合 |

> 同时立了一条规矩并加断言：**凡出现在 `allowed` 里的动作，`actionHelp` 必须有对应条目**。
> 这条断言会自己扫描 `status.allowed` 与 `status.actionHelp`，以后新增动作漏写 help 会直接红。

## 残余 b · `failed.planner` 旧脏记录未清

**现象**：修复前登记的 `role-blocked` 一直挂在 `status.failed.planner` 里。
条目里**没有时间戳、没有 revision、没有 specHash** ——
分不清它是「刚才登记的」还是「上一版代码遗留的脏数据」，因此也没法判断修复是否生效。

**修法（三条，缺一不可）**：

1. **登记时打戳**：`blocked` 与 `interrupt` 写入 `at` / `revision` / `specHash`；
2. **status 标注而不删除**：新增 `annotateFailures()`，把不适用于当前版本的标 `stale:true`，并给 `staleReason`：

   | staleReason | 判定 |
   |---|---|
   | `legacy：修复前登记的旧条目（没有时间戳/版本号）` | 无 `at` |
   | `specHash：模板已更换，旧判定依据已不成立` | hash 不匹配 |
   | `revision：期间回退过，这条属于上一版产物` | revision 不匹配 |

3. **给显式清理路径** `clearFailed`，并在 `status.failed.__staleHint` 里直接写怎么用。

> **为什么不自动清**：故障登记是**审计事实**（「某一刻某个角色说我干不下去了」），
> 自动消失等于篡改历史。而且「还适不适用」只有大脑能判断 —— 这正是 planner 的职责。

**`clearFailed` 与 `retry` 的分工**（这是本节的全部意义）：

| | 清账 | 派新活 | 用途 |
|---|---|---|---|
| `retry` | ✅ | ✅ | 故障仍成立，让它再干一次 |
| `clearFailed` | ✅ | ❌ | 故障已不适用（旧版本遗留 / 回退过 / 角色换人） |

实测：`clearFailed` 前后 `jobs` 完全不变（不派新活），清掉的原条目归档进 `history.clearedFailure`。

## 残余 c · stop 是否还写 failed（实调验证）

**已实调验证**：`stop` 前 `failed=[]`，`stop` 后 **`failed` 仍为 `[]`**，
状态文件里 `failures` 为空，`status.failed` 里也没有任何实质条目。

对应回归断言（F9-c，4 条）：

```
F9-c stop 前没有 failed                    -> true
F9-c stop 受理                             -> true
F9-c stop 后状态文件里 failed 为空          -> true
F9-c stop 后 status.failed 里没有实质条目   -> true
```

> 测试方担心的「需实调一次 stop 才能验证，但会改状态」——
> 这个顾虑现在**不必再有了**：验证路径本身就是只读的（见下节 D14）。

## D14 · closed 契约的**只读验证入口**

**测试方原话**：「只读路径不可达（status 返回体无 closed/closable 字段，
触发动作不在当前 allowed 白名单），需作者给一个只读/幂等验证入口。」

**这话完全正确**：想确认「closed 现在能不能用」，旧版只能真的发一次 `submit` 去撞，
而 submit 会改状态 —— **验证一个只读契约却要写状态，本身就不合理。**

**修复**：`status` 新增 `issueLedger`（纯只读投影，由插件按状态算，模型看不到也改不了）：

```json
"issueLedger": {
  "current":      [{"key":"k-hard","kind":"hard","owner":"planner"}],
  "closableKeys": [{"key":"k-hard","kind":"hard","owner":"planner"}],
  "reopenedKeys": [],
  "lastClosed":   [{"key":"verbatim-missing","evidence":"frame 已补入用户原话"}],
  "lastVerdict":  "pass",
  "note": "这份台账是【只读投影】：closableKeys 就是下一轮 submit 时 closed[] 会被接受的集合。想验证 closed 契约不必真的提交 —— 看这里即可。"
}
```

| 字段 | 回答什么问题 |
|---|---|
| `current` | 本轮还挂着哪些异议 |
| `closableKeys` | **现在就能关**的 key（= 下一轮 `closed[]` 会被接受的集合） |
| `reopenedKeys` | 本轮已重提、不能用 closed 抹掉的 key |
| `lastClosed` | 上一次 review **实际关闭**了哪些（`closed` 真的生效过的直接证据） |

**验证路径（完全不改状态）**：

1. `action=status` → 看 `issueLedger.closableKeys`；
2. rework + frame 重写后再 `status` → 该 key 应出现在 `closableKeys` 里（证明台账没被抹掉）；
3. 真发一次 `closed` 之后再 `status` → 它会出现在 `lastClosed` 里。

实测（F9-D14，6 条断言）：

```
无异议时 current 为空                          -> true
提出异议后 current 有一条 k-hard                -> true
本轮刚提 => closableKeys 为空（还没到下一轮）    -> true
frame 重写后 ledger 仍有该条                    -> true
frame 重写后它成了 closable（本轮未重提）        -> true
lastClosed 是数组                               -> true
```

---

## 回归（第五轮）

| 测试 | 结果 |
|---|---|
| `smoke.mjs` | ✅ OK |
| `test_regression_seven_stage_wiring.py` | ✅ **PASS=266 / FAIL=0**（246 → 266，新增 **F9 段 20 项**） |

F9 段覆盖：

- **a) 契约完整性 5 项**：`actionHelp` 有 stop/clean/clearFailed；stop 契约写明不写 failures；
  **「allowed 里每个动作都有 actionHelp」的自扫描断言**（以后漏写 help 会红）。
- **b) 脏记录**：legacy 条目被标 `stale`、`staleReason` 说明原因、`__staleHint` 指路、
  `clearFailed` 能清、清账进 history、**清账不派新活**、无账可清时报 `NO_SUCH_FAILURE_TO_CLEAR`、
  分析角色不能 `clearFailed`。
- **c) stop 不写 failed 4 项**：实调验证。
- **D14 只读入口 6 项**：`issueLedger` 各字段与状态变化的对应关系。
- **往轮回归 5 项**：盲审白名单、refused 只在 preflight、frame 保台账、attach 接管、clean dry-run。

---

## 状态对照（给测试方）

| 条目 | 复核前 | 现在 |
|---|---|---|
| F 节 | 🔶 部分修复（a/b/c 残余） | ✅ **a/b/c 全部修完并实调验证** |
| D14 | 🔶 修复待验证（只读路径不可达） | ✅ **只读入口已给（`status.issueLedger`）** |

> 测试方当时提「stop 是否还写 failed 需实调验证，但会改状态」——
> 现在这类验证可以只读完成：看 `status.issueLedger` 与 `status.failed[*].stale` 即可，
> 不必为了验证一个契约而改动任务状态。