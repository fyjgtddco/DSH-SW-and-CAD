# 七阶段插件 · evidence_wait 用户答完仍卡死（2026-11-25）

> 现场报告：`C:\Users\j1877\Desktop\test\BUG_REPORT_seven_stage_evidence_wait_stuck.md`
> 任务：`20261125_211324_905E57B6`（6 轴机械臂）· step 1.2 · 七阶段
> 结局：用户答完 + capture 成功，phase 永久停在 `evidence_wait`，**卡了 5 天**
> 前一轮：[fixes-20261120-seven-stage-startup-stop-closure.md](./fixes-20261120-seven-stage-startup-stop-closure.md)

---

## 一句话结论

**不是状态机没有转移逻辑，而是「从 evidence_wait 出去」这条路被四个小陷阱连续堵死。**

报告推测的「`question=null` 且 `facts` 非空时应自动推进」——**这个不建议采纳**：
capture 只说明「真人说了话」，说不说话、说什么、要不要改设计，是 planner 的判断。
插件若按这两个条件自动放行，等于让"用户随便回一句"就能推进状态机 —— 那是把大脑让位给副作用。

真正的根因是：**每一步都只差一点点信息，而错误提示不给**。四条叠加后，
planner 无论走哪条路都撞墙，最后只能放弃。

---

## 四条确定性陷阱（均已复现）

| # | 陷阱 | 表现 | 为什么致命 |
|---|---|---|---|
| ① | `fromStep` 文档写"可选"，代码事实上必填 | `INVALID_REWIND_TARGET` | `stepIndex(undefined) = -1`；错误里不说该填什么、也不说当前能回退到哪 |
| ② | 对 `question` 的答复填 `no_change` 必被拒 | `NEEDS_EVIDENCE_DISPOSITION` | 语义上必然失败（那条答复就是用来消除缺口的），但**没给任何替代分类** |
| ③ | 推进后 `waitReason` 不清 | 状态里长期挂着"本轮决策依赖未处理的缺口" | planner 以为缺口还在，反复 frame/rework 空转 |
| ④ | `frame` 照抄旧 `blockedBy` 立刻退回 `evidence_wait` | 无限闭环 | `interpret` 把 `s.slot` 清空后，planner **无从知道哪条依赖已被答复解决**，只能照抄 |

### 现场实际卡在哪

```
21:51 capture 成功（facts 写入、question=null）
  ↓ planner 试 interpret
  ├─ 不传 fromStep      → INVALID_REWIND_TARGET     ← 陷阱①
  ├─ 填 no_change       → NEEDS_EVIDENCE_DISPOSITION ← 陷阱②（而且它答的正是 question）
  └─ 填 fact + fromStep → 能推进，但 waitReason 不清 ← 陷阱③
        ↓ 按 waitingOn 指路「frame 重写本步边界」
        └─ 照抄 blockedBy → 立刻退回 evidence_wait  ← 陷阱④（闭环）
```

---

## 修复

### ① `fromStep` 缺省时给坐标，而不是给错误

```js
let from;
if (d.fromStep 为空) {
  from = s.cursor;                    // 默认回退到【当前在办小步】
  out.fromStepDefaulted = {step, note};
} else {
  from = stepIndex(d.fromStep);
  check(from>=0, '不是任何已知小步… 当前在办小步是 …');  // 给可填范围
  check(from<=s.cursor, '只能回退到【已走过的】小步');
}
```

> **这不是放宽校验**：该清 slot / 该 bump revision 的判据一个字没动。
> 它只消除"一个没有默认值的可选参数"这个设计缺陷 —— 而"默认回退到当前小步"
> 是唯一无歧义的选择（planner 说"这条答复影响本步"）。

### ② `no_change` 的拒绝必须带替代

判据保留（`no_change` 对 question 答复语义上确实不成立），但拒绝时给出：

```
NEEDS_EVIDENCE_DISPOSITION: 这条真人答复不能标 no_change ——
  当前 phase=evidence_wait（在等补证），标 no_change 等于把缺口留在原地，流程会永远卡在这里。
请按答复的实际性质重填 classification：
  · fact / preference / authorization / estimate / unknown
同时：如果这条答复消除了 frame.blockedBy 里的依赖，重发 frame 时带 resolved:[<被解决的依赖原文>]。
```

结构化字段：`suggestedClassifications`、`factQuestionId`。

### ③ `waitReason` 跟着 phase 走

```js
// interpret 推进后
s.waitReason = null;   // 它是【当前 phase 的注脚】，phase 变了就必须重算
```

> 真正的"还缺什么"在 `slot.frame.blockedBy` 里，不在这句文案里。

### ④ `blockedBy` 必须能被核销（打开闭环）

两件事一起做：

**a) 依赖的出处要交还给 planner。** `interpret` 推进时会清空 `s.slot`（产物作废，这是对的），
但清之前要把 `blockedBy` 抄出来放进回执：

```js
const carriedBlockedBy = s.slot?.frame?.blockedBy ?? [];
s.slot = null;
out.carriedBlockedBy = carriedBlockedBy;
out.next = '…本步原来的依赖已随产物清空，它们是：[…]
  如果其中某些已被这条答复解决，重写 frame 时写进 resolved:[…]；
  如果仍没解决，就继续留在 blockedBy 里。'
```

**b) `frame` 支持 `resolved` 核销。**

```js
frame { …, blockedBy:[…], resolved:[<被解决的依赖原文或前缀>] }
```

规则（**插件判定，不靠模型自觉**）：

| 情形 | 处置 |
|---|---|
| 没写 `resolved` | 照旧：`blockedBy` 非空 → `evidence_wait`，并回执 `stillBlocked` 列出还差哪几条 |
| 写了且匹配得上 | 从 `blockedBy` 摘除，回执 `blockedByCleared` |
| 写了但匹配不上 | `RESOLVED_DEPENDENCY_NOT_FOUND` + 回显当前 `blockedBy` |

> ⚠️ **实现坑（已踩）**：核销必须在**新建的** `s.slot.frame` 上做。
> 先改旧 `s.slot.frame` 会被随后的整块替换覆盖 —— 表现为 `blockedByCleared` 报"已摘除"，
> 但写回去的 `blockedBy` 还是原样，phase 仍掉回 `evidence_wait`。

---

## 关于报告的两条建议

| 报告建议 | 处置 |
|---|---|
| 「`question=null` 且 `facts` 非空时自动推进」 | ❌ **不采纳**。capture 只证明"真人说了话"；要不要改设计、回退到哪一步是判断，归 planner。自动放行等于让一句闲聊推进状态机 |
| 「直接改状态文件里的 phase」 | ❌ **不采纳**（报告自己也标了"高风险"）。现在不需要了 —— 用 `interpret` + `resolved` 就能正常推进 |
| 「排查 planner 消息通道」 | 已排除：日志显示 planner 一直在正常调用工具，卡点全在参数与判据上 |

---

## 回归

| 测试 | 结果 |
|---|---|
| `smoke.mjs` | ✅ OK |
| `test_regression_seven_stage_wiring.py` | ✅ **PASS=281 / FAIL=0**（266 → 281，新增 **F10 段 15 项**） |

### F10 段覆盖（真实时序复现，不是字符串断言）

| 断言 | 防的是 |
|---|---|
| `interpret` 不传 `fromStep` 能推进 | 陷阱① |
| `fromStep` 越界报"不是任何已知小步"并给当前步 | 陷阱① 的可操作性 |
| `interpret` 推进后 `waitReason` 已清空 | 陷阱③ |
| `no_change` 被拒且给 `suggestedClassifications` | 陷阱② |
| 换成 `fact` 后能推进 | 陷阱② 不是死路 |
| 照抄 `blockedBy` 仍回 `evidence_wait` | 陷阱④ **判据没被放宽** |
| 回执给 `stillBlocked` | 陷阱④ 的可操作性 |
| 带 `resolved` 能核销并推进 | 陷阱④ 闭环被打开 |
| `resolved` 填错报 `RESOLVED_DEPENDENCY_NOT_FOUND` 并回显 `blockedBy` | 陷阱④ 不自造规则 |
| 推进时把旧依赖交回 planner（`carriedBlockedBy`） | 陷阱④ 的根因 |
| 往轮回归 5 条（盲审/refused/frame 台账/attach 接管/stop） | 别改回去 |

---

## 部署

```powershell
.\sync-to-dsh.ps1 -DryRun -Prune
.\sync-to-dsh.ps1 -Prune
python engineering\tools\verify_sync.py
# 然后【完全退出并重开 DSH】（插件 host 半是进程级）
```

> 现有卡死任务 `20261125_211324_905E57B6` 的恢复方式（重开 DSH 后，**planner** 执行）：
>
> ```
> interpret {messageId:"43078fc0-52c8-47af-b7a0-e6351055e6d2", classification:"fact",
>   parameters:"10kg/880mm/±0.03mm…", conflict:"…", changed:"…", impact:"…",
>   reason:"真人答复工程指标"}          // 不再需要 fromStep
> frame {…, blockedBy:["REQ_1_1_01: …", "REQ_1_1_04: …"], resolved:["REQ_1_1_01: …"]}
> ```
>
> 第一条把状态从 `evidence_wait` 带回 `frame`；
> 第二条声明哪条依赖已被这条答复解决，剩下的（如基座法兰）继续留在 `blockedBy` 里。
> 若基座法兰也被这条答复覆盖，把它一并写进 `resolved` 即可继续推进到 knowledge。

---

## 教训

1. **"可选参数"必须真有默认值。** `actionHelp` 写 `fromStep?`，代码却 `stepIndex(undefined) = -1` ——
   一个文档与实现的错位，让模型连试 3 次同一个码，最终烧掉 5 天。

2. **错误的"语义必然失败"要配替代方案。** `no_change` 对 question 答复必然不成立，
   但只回一个错误码，等于告诉模型"这条路不能走"却不告诉它"哪条能走"。

3. **文案字段必须跟着状态重算。** `waitReason` 是"当前 phase 为什么停在这"的注脚，
   phase 一变它就该重算；否则它会变成一条永久的假阻塞。

4. **作废产物时，要把"作废了什么"交还给上游。** `interpret` 清 `slot` 是对的，
   但清完之后 planner 只剩记忆可用 —— 而模型没有可靠的跨轮记忆。
   把 `carriedBlockedBy` 明确交出去，闭环就断了。

5. **修复闭环不等于放宽判据。** 四条修复里没有一条改变"依赖没解决就不能推进"，
   改的全是"让调用方看得见、动得了"。