# BUG记录.md（2026-11-26 机械臂任务）核对与修复

> 现场报告：`C:\Users\j1877\Desktop\test\BUG记录.md`（19 条）
> 任务：建造一个机械臂 · Mode 2 · progress 4/63 时点的观察
> 本文逐条核对：**哪些是真缺陷、哪些只是提示、哪些不该改**。

---

## 一句话结论

19 条里 **8 条已改代码**，**7 条是提示/文案问题（已顺手改）**，
**4 条属于宿主环境或机制设计，不该在插件里改**。

最严重的一条不是报错，是**沉默的误导**：BUG-012 让 planner 在角色早已提交后
反复催办约 10 次，而它依据的 `current.knowledge/review` 字段**本来就是滞后的**。

---

## 已改（8 条，均有回归锁定）

| 编号 | 原现象 | 根因（代码级） | 修法 |
|---|---|---|---|
| **BUG-013** | `hits:1`，实际 8 条 | `filled = searches.length` 是**工单数**，被当成了**命中数** | `hits` 改真实命中总数，另给 `searchRuns` |
| **BUG-007** | `reopenedKeys` 恒等于 `current` | 它其实是"本轮 review 重提的"，不是"曾关闭又打开" | 拆成 `reopenedKeys` / `restatedKeys` / `stillOpenKeys` |
| **BUG-009** | `conditions` 传数组被拒 | `actionHelp` 只写字段名，没写类型 | 明写**「字符串，不是数组」** |
| **BUG-011** | frame 说"无需检索"，引擎仍硬拒 | 引擎不看 `frame.searchTopic` | 加**显式豁免**：命中"无需检索"允许 `sources:[]`，但**必须**一条 blocking gap 说明理由，并写审计 |
| **BUG-002** | setup 的 next 指 `invite` | 真动作是 `launch`（invite 已并入） | next 改指 launch 并注明"不单独调 invite" |
| **BUG-012 / 019** | 约 10 次重复催办 | `current.*` 滞后是**设计使然**，却无权威判据 | 新增 `status.handoff`：每角色 `delivered/working/idle` + `shouldNudge` + **一句话 verdict** |
| **BUG-010** | yield 撞 phase 漂移被拒 | 报错把竞态说成"想跳过" | 先讲"这可能是竞态，重读 status 即可" |
| **BUG-014** | yield 与 search_fill 撞车 | 同上 | 同上，并补"不要重试 yield" |

### 为什么 BUG-011 的豁免不是"放宽证据要求"

豁免成立需要**两个**条件同时满足：

1. planner 在 frame 里显式写了"无需检索"；
2. knowledge 提交时带了**至少一条 blocking gap**解释为什么不查。

否则仍然抛 `NO_SEARCH_DECLARED_NEEDS_BLOCKING_GAP`。
**不查可以，但要留下"为什么不查"的书面理由** —— 否则就等于允许"什么都不查、
也不解释"直接交卷，那会把七阶段的证据要求架空。

---

## 已改文案（7 条，不影响判据）

BUG-001（status 提示未按角色裁剪）、BUG-003（launch 异步需重读）、
BUG-004（单工单约束被当阻塞）、BUG-005（verdict/issue 类型互斥缺引导）、
BUG-017（会话回收致工单作废）、BUG-018（interpret 文案疑似反转，未复现）。

其中 **BUG-004 改动最实用**：原文案只说"上一张还没回填"，
现在明说**「这是正常的单工单约束，不是故障」**，并列出能做什么/不要做什么 ——
现场就是因为不知道这点，把正常约束当成阻塞上报了。

---

## 不该改的（4 条）

| 编号 | 为什么不该在插件里改 |
|---|---|
| **BUG-006** | commit 遇 `blocking:true` 的 human gap 转 `evidence_wait` 是**设计**：未确认的关键事实不该被判"通过"。BUG-009 的补充记录也证明——按协议走 question→授权→interpret 后 commit 能顺利放行，**这是契约陷阱易踩，不是死锁**。 |
| **BUG-008** | interpret 非 no_change 一律 revision+1，是七阶段"改答即失效"的红线。放宽成"authorization 只改边界"会让"用户改了口、旧结论却还在"成为可能 —— 那比返工危险得多。代价高，但方向正确。 |
| **BUG-015** | 宿主 `web_search` 端点偶发 fetch failed，是**网络**问题，插件已提供 `search_fill outcome=unavailable` 的兜底。 |
| **BUG-016** | `snippet` 含特殊字符破坏 JSON，是**调用方拼串**的问题。真要用程序拼，应 `JSON.stringify` 而不是手写引号。 |

---

## 回归

```powershell
$env:PYTHONIOENCODING='utf-8'; $env:PYTHONUTF8='1'
node engineering\plugins\dsh-engineering-seven-stage\test\smoke.mjs
python engineering\tools\test_regression_buglog_20261126.py    # 新增 28 项
python engineering\tools\test_regression_seven_stage_wiring.py  # PASS=290
```

| 测试 | 结果 |
|---|---|
| `smoke.mjs` | ✅ OK |
| `test_regression_buglog_20261126.py` | ✅ **PASS=28 / FAIL=0** |
| `test_regression_seven_stage_wiring.py` | ✅ **PASS=290 / FAIL=0** |
| `test_log_panels.py` | ✅ **PASS=30 / FAIL=0** |

新增测试里有一条**反向断言**值得保留：
`invite → 为每个角色创建独立二级子代理` 这句话**在注释里必须还在**（它是"为什么改"的现场记录），
只在**可执行代码**里不许出现。第一次写测试时我就是没区分注释与代码，导致假失败。

---

## 部署

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\sync-to-dsh.ps1
python engineering\tools\verify_sync.py
# 然后【完全退出并重开 DSH】（flow.js 是宿主侧，进程级）
```

> 本机 PowerShell 执行策略禁止直接跑 .ps1，必须带 `-ExecutionPolicy Bypass`。
