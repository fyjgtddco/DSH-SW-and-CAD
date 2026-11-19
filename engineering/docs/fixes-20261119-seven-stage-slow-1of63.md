# 七阶段分析 · 36 分钟只推进 1/63（2026-11-19）

> 现场：DSH 会话 `session-a332529d-7d1a-4fc0-8f35-6751e7973cd2`
> 任务：`20261119_102048_F6C63358`（"帮我建造一个减速器"）
> 插件：`dsh-engineering-seven-stage` · spec `9f680118…`（V0.3 / 63 小步）
> 结局：10:20 开工 → 10:56 用户中止，**35.6 分钟只 commit 了 1 步（1/63）**

---

## 一句话结论

**不是模型慢，是"读正文"的通道断了。**

> 时间账（35.6 分钟里的 LLM 步进合计 28.5 分钟，单步 p50 = 7.8 s —— 模型本身不慢）：
> · **10:32:25 → 10:40:19（7.9 min）**：step 1.1 review 读不到待审正文
> · **10:45:18 → 10:49:17（4.0 min）**：step 1.2 同一个坑再踩一次
> · **10:50:31 → 10:56:12（5.7 min）**：capture / interpret 互锁，直到用户中止
> · 合计 **17.6 min / 35.6 min ≈ 49% 全部消耗在这两条通道缺陷上**，
>   而这 17.6 分钟只换来 **0 个**新的 commit。

`status` 一次 24~29KB，被宿主按 `thresholdChars=8192 / headChars=4096 / tailChars=1024`
**掐头去尾**，正好裁掉 knowledge / design 正文；而专门用来读正文的 `read`
又只查"已 commit"的小步 —— 当前在办的小步永远读不到。
于是 review 只能让 planner 用 `send_message` 把正文"人肉转发"，一步烧掉 19 分钟。

---

## 时间线（同一任务，UTC+8）

| 时刻 | 事件 | 耗时 |
|---|---|---|
| 10:20:35 | turn 1 开始，`workflow_gate init` + 两轮问卷 | — |
| 10:21:48 | planner 首次 status（phase=setup） | — |
| 10:25:28 | step 1.1 frame | — |
| 10:26:14 | knowledge 检索工单 → 主对话 `web_search` → `search_fill` | 2 min |
| 10:28:15 | knowledge submit → phase=preflight | — |
| **10:32:25** | **review status = 24667 字符 → 被中间裁剪到 5159** | — |
| 10:32:34 ~ 10:34:44 | review 连试 6 种 `read` 参数：`{step:"1.1"}` / `{}` / `"design"` / `{step,role}` / jobId / `{step,record}` —— **全部 CURRENT_RECORD_NOT_FOUND** | 2.2 min |
| 10:36:22 | planner status = **28877 字符** → 裁剪到 5159，design 正文丢失 | — |
| 10:37:20 ~ 10:39:50 | planner 让 design 把提交原文"重发"到 review，review 逐字核对 | 2.5 min |
| 10:40:19 | step 1.1 终于 commit（1/63） | **18.5 min** |
| 10:41:35 | step 1.2 frame | — |
| 10:45:18 | review 又读不到 1.2 正文，`read` 再试 2 次 | 1 min |
| 10:46:01 | review 登记 blocked（"待审正文不可达"） | — |
| 10:46:46 | planner `retry(review)`，改用 send_message 直投 | 2 min |
| 10:49:17 | step 1.2 commit 前进入 evidence_wait（缺额定工况） | — |
| 10:50:16 | 主对话 `ask_user_question` 问额定 P/n1/i，用户秒答 | — |
| 10:50:31 | `capture({})` **成功**，但**回执里没有记录 id** | — |
| 10:51:14 | `interpret({messageId:"rated_duty"})`（题目 id）→ `HOST_CAPTURED_MESSAGE_REQUIRED` | — |
| 10:53:02 | `capture({messageId:"rated_duty"})` → `ROOT_USER_MESSAGE_NOT_AVAILABLE` | — |
| 10:56:12 | 用户中止 | **15 min 停在 1/63** |

---

## 根因（四条，互相叠加）

### ① `read` 只查已 commit 的小步 —— 本次事故的主因

`lib/flow.js`（修复前）：

```js
if (action === 'read') {
  const entry = s.records.find(x => x.step === d.step);
  check(entry, 'CURRENT_RECORD_NOT_FOUND');   // records 只在 commit 时 push
  return {record: entry};
}
```

`s.records` 只在 `commit` 时写入，而 review 要审的、planner 要核对的，
恰恰是**还没 commit 的当前小步**（内容在 `s.slot` 里）。
语义上完全说不通：`next` 文案明写"反驳读取待审版本并 submit"，`read` 却读不到待审版本。

现场代价：review 盲试 6 种参数、planner 再试 2 种，全部同一个错误码；
最后只能靠 `send_message` 转发正文（约 2.5 分钟 + 两轮上下文膨胀）。

### ② `status` 太大 → 被宿主的 tool-result-pruner 中间裁剪

`preset-engineering.patch.yml`：

```yaml
- id: tool-result-pruner
  config: { thresholdChars: 8192, headChars: 4096, tailChars: 1024 }
```

而插件把**全文**塞进 status：

| status 字段 | 现场大小 |
|---|---|
| `job.knowledge`（content 1822 字 + sources/searches） | **11623** |
| `job.design`（content 2212 字） | 3681 |
| `job.objections` | 1971 |
| `job.rules`（八条规则，每次重复） | 2798 |
| planner `current.review` | 5436 |
| planner `current.issues` | 3750 |
| **review status 合计** | **24667** |
| **planner status 合计** | **28877** |

宿主保留 4096 + 1024，中间替换成 `[... tool result middle pruned ...]` ——
被裁掉的正是 design/knowledge 正文。会话日志里能直接看到这条裁剪标记。

### ③ `capture` 不回执记录 id，`interpret` 只能靠猜

`capture` 成功时返回的是 `view(s,role)`（状态快照），**没有刚捕获记录的任何标识**。
主对话手里只有 `ask_user_question` 的**题目 id**（`rated_duty`），
而 `interpret` 要的是**消息 id**。三条路同时被堵：

- `interpret({messageId:"rated_duty"})` → `HOST_CAPTURED_MESSAGE_REQUIRED`
- `capture({messageId:"rated_duty"})` → `ROOT_USER_MESSAGE_NOT_AVAILABLE`
- 再 `capture({})` → `USER_MESSAGE_ALREADY_RECORDED`

### ④ 错误提示不给出路

`CURRENT_RECORD_NOT_FOUND` / `HOST_CAPTURED_MESSAGE_REQUIRED` 都是裸错误码，
不列"已 commit 哪些步"、不列"可用的 messageId 是哪些"，
模型只能继续盲试 —— 而每次盲试都是一轮完整的 LLM 往返。

---

## 修复

### A. `read` 变成真正的"读正文"通道（flow.js）

```js
read {step?, part?, offset?, limit?}
```

- **已 commit** → 读 `s.records`；
- **未 commit 的当前小步** → 读 `s.slot`（frame/knowledge/design/review/issues/…）；
- 不带 `part` → 返回"目录"：`{step, pending, phase, parts:{knowledge:9267, design:128, …}}`；
- 带 `part` → 返回正文，**单次 ≤6000 字**，超长给 `nextOffset` 续读。

> 6000 是刻意压在 pruner 阈值 8192 之下（留 ~2KB JSON 余量），
> 保证正文**永远不会再被中间裁剪**。
> `review` 在 `preflight` 的盲审限制保留（`BLIND_REVIEW_INPUTS_ONLY`），不能借 read 偷看。

### B. `status` 只下发"正文索引"

新增 `bodyRef()`：

```js
{ id, revision, contentChars, sources?, gaps?, body:'read {step:"1.1", part:"knowledge"}' }
```

- `job.knowledge` / `job.design` → 索引（不再带全文）；
- planner 的 `current` 同样只给索引 + 异议三要素（key/owner/kind/object）；
- 八条规则（`rules`）从每次 status 里移除，改由 `launch` 的**初始 prompt 一次性下发**
  （`invite` 的 prompt 里拼上 `spec.crossCutting`），另留 `read {what:"rules"}` 可重读。

修复后实测：review status **4073 字符**（原 24667），planner **3441**（原 28877），
全部稳定低于 8192，不再触发裁剪。

### C. `capture` 回执 id + 幂等 + 认题目 id

flow.js：

```js
out.record = {id, text, source, questionId?};
out.messageId = fact.id;          // interpret 直接用这个
out.next = 'interpret 的 messageId 必须填 "…"';
```

重复捕获同一条消息 → `idempotent:true` + 返回原记录（不再抛 `USER_MESSAGE_ALREADY_RECORDED`）。

index.js：

- `askAnswer` 现在返回 `{text, ids}`，`directUser` 把 `questionIds` 挂在记录上；
- `capture` 的查找支持 **消息 id / 题目 id / toolCallId** 三种；
- 失败提示同时列出 `id` 与 `questionIds`。

### D. `interpret` 失败时列出可用 messageId

```
HOST_CAPTURED_MESSAGE_REQUIRED: messageId="rated_duty" 不在【已捕获且尚未解释】的事实里。
最近可用的 messageId: [{"messageId":"b450efc3-…","questionId":"3fcb262e-…","preview":"[rated_duty] …"}]
先调 action=capture（回执里的 messageId / record.id 就是这里要填的值）；
注意它不是 ask_user_question 的题目 id。
```

### E. 顺手修掉的三处隐患

- `workPacket` 里 `s.slot.frame` / `s.slot.issues` 没做空值保护 ——
  `reopen` 恢复到 `phase=frame` 时 `slot=null`，planner 一 `status` 就抛
  `TypeError`，宿主只显示 "tool failed"（真正的错因被吞）。
- `allowedActions` 里 `['status','read'].includes(role)` 是把**动作名**当成了角色名，
  真正的 `unbound` 子会话反而拿到空清单 → 改成 `role === 'unbound'`。
- root 的 `allowed` 现在显式包含 `status/read/export/capture`
  （原来只写 capture/wait_user，可 read 本来就是放行的，模型不知道）。

---

## 回归

```powershell
node engineering\plugins\dsh-engineering-seven-stage\test\smoke.mjs
python engineering\tools\test_regression_seven_stage_wiring.py
```

| 测试 | 结果 |
|---|---|
| `smoke.mjs` | ✅ OK（63 步完整链不受影响） |
| `test_regression_seven_stage_wiring.py` | ✅ **PASS=156 / FAIL=0**（原 148 + 新增 8 项源码断言 + 10 项行为断言） |
| `run_regression.py` 全量 | 65 / 68 通过；3 个失败项在 **HEAD 基线（git worktree，未含本轮改动）上同样失败**，属既有失败，与本轮修复无关：`test_regression_a2_b3_wait_stall` / `test_regression_b8_heartbeat_archive` / `test_regression_dryrun_and_encoding` |

F6 行为验证新增覆盖：

- `read {step}` 返回目录且 `pending:true`；`part=knowledge` 能拿到正文；
- 不传 `step` 默认当前小步；未知小步给出"已 commit 列表 + 当前在办小步"；
- review 在 phase=review 能读待审 knowledge/design；preflight 盲审仍被 `BLIND_REVIEW_INPUTS_ONLY` 挡住；
- `status` 长度 < 8192 且 `job.knowledge` 只有索引；
- `capture` 回执 messageId；重复 capture 幂等；按题目 id 捕获成功；用回执 id 解释成功。

---

## 部署

```powershell
.\sync-to-dsh.ps1 -DryRun -Prune
.\sync-to-dsh.ps1 -Prune
python engineering\tools\verify_sync.py
# 然后【完全退出并重开 DSH】（插件 host 半是进程级）
```

> 运行时状态文件（`seven_stage/*.json` 等）不会被覆盖；旧任务仍可用旧记录继续，
> 但新任务从下一次 `status` 起就是小载荷。
