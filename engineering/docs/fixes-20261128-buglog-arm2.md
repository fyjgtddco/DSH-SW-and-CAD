# BUG记录.md（2026-11-28 机械臂测试）核对与修复

> 现场报告：`C:\Users\j1877\Desktop\test\BUG记录.md`（任务 `20261128_131202_2462AEBA`）
> 本文逐条核对 12 条：**哪些已修、哪些是上一轮修复没生效、哪些不该改**。

---

## 一句话结论

12 条里 **8 条已改代码**、**1 条是上一轮修复的正则没认全（已补）**、
**2 条属于宿主/机制设计不必改**、**1 条是信息项**。

最值得说的是 BUG-10：它上一轮就"修过"，但现场仍然复现 —— 因为
frame 写的是「无需**新**检索」，而旧正则 `/无需(发布)?检索/` 中间容不下那个"新"字。
**改对了逻辑，却用错了匹配方式，等于没修。**

---

## 已改（8 条，均有行为回归）

| 编号 | 现象 | 根因 | 修法 |
|---|---|---|---|
| **BUG-1** | `init.next_command` 说"先问第 0 题"，与 `message`"第 1 步"矛盾 | 文案残留，参数强度其实在第 2 步 | next_command 改指模式确认两问，并注明"不是参数强度" |
| **BUG-2** | `flow_order` 仍写「题数区间 强 10~50 / 弱 5~10」 | 十阶段配额早已删除 | 改为「决定设计范围详略；不设题数配额」 |
| **BUG-6** | `interpret` 提示"把原文写进 resolved"，但 `blockedBy=[]`，照做必被拒 | 提示无条件生成，没看 blockedBy 是否为空 | 空格时明确说"**不要传 resolved**" |
| **BUG-9** | status 被 pruner 截断，来源 id 丢失 → submit 被拒 | 必需信息藏在**长正文**里，而长正文正是被裁的部分 | 新增 `ready.sourceIds`（id 是 uuid，短，永不被裁） |
| **BUG-10** | frame 写"无需检索"仍被强制发工单 | 正则 `/无需(发布)?检索/` 认不出"无需**新**检索" | 放宽为「无需/不需要/不用/不必 + 0~8 字 + 检索」 |
| **BUG-11** | query 560 字符 → 宿主 400（上限 500） | 插件发单时不校验长度，等 root 撞墙 | 发单时拦截，教用 `queryParts` 拆 1~4 条 |
| **BUG-8/12** | planner 反复催已交付的角色（累计 ≥6 次） | 拿到的 status 无新鲜度标记 | status 增加 `observedAt` |
| **BUG-4** | 每步都唤醒子代理，实际多余 | 唤醒基于旧快照；对照实验证明不唤醒也能自行完成 | 角色阶段 `nextAction` 直接给 **yield** 指引 |

---

## 上一轮"修了但没生效"的一条

**BUG-10** 是这次的重点教训。2026-11-26 那一轮已经加了"frame 标注无需检索
就豁免"的逻辑，判据本身完全正确。但豁免靠正则识别 frame 的措辞：

```js
// 旧：只认「无需检索」「无需发布检索」「不需要检索」
/无需(发布)?检索|不需要检索|不用检索|无需外部检索/
```

现场 frame 写的是「无需**新**检索，直接汇总前四步既有记录即可」——
一个"新"字，整条豁免就失效了，任务照样被逼去发工单。

> **教训：不要让模型去适配正则，要让正则去吃人话。**
> 判据正确 ≠ 生效；识别面太窄，等于没修。

新正则现在认：无需/不需要/不用/不必 + 0~8 字 + 检索，以及
"无需外部资料/来源/文献"。同时保留底线：**豁免仍要求一条 blocking gap
说清"为什么不查"**，不查可以，但要留书面理由。

---

## 不该改的（3 条）

| 编号 | 为什么 |
|---|---|
| **BUG-3** | 载荷工况文件初始缺失是**预期行为**（报告自己也标了"待第 3 次 provide_context 后复核"），门禁会在该生成时生成。 |
| **BUG-5** | `yield` 撞上 `PLANNER_ACTION_PENDING` 是竞态：上一轮已经把文案改成"先排除竞态，重读 status 即可"，不改判据是对的 —— 放宽 yield 会让"跳过 commit"变得容易。 |
| **BUG-7** | `VERDICT_ISSUE_MISMATCH`（含 unknown 必须判 blocked）是**有意设计**，报告自己也标注"非 bug"。 |

---

## 本轮抓到的两个真 bug（只有跑起来才暴露）

这两个都通过了 `node --check` 语法检查，是**行为测试**抓出来的：

1. **`orderSearch` 里引用了不存在的 `data`** —— 我把校验写进该函数时，
   沿用了外层 `opAction` 的变量名，但 `orderSearch(a,task,jobId,query)`
   里根本没有 `data`。`ReferenceError: data is not defined`，
   直接把发布工单整条打挂。
2. **豁免分支里 `audit(s, a.id, …)`** —— `finishPacket(s, data, role)`
   的作用域内没有 `a`。同样是 `ReferenceError`，但只在"豁免成立"这条
   分支上触发，所以正常流程跑不到，**第一次跑豁免用例才炸**。

> 这两条说明：源码字符串断言（`"xxx" in src`）能防"被人删掉"，
> 但防不了"写错了还能通过语法检查"。**关键路径必须有行为测试。**

---

## 验证

```powershell
$env:PYTHONIOENCODING='utf-8'; $env:PYTHONUTF8='1'
node engineering\plugins\dsh-engineering-seven-stage\test\smoke.mjs
node engineering\plugins\dsh-engineering-seven-stage\test\e2e-buglog-20261128.mjs
python engineering\tools\test_regression_buglog_20261128.py
python engineering\tools\test_regression_seven_stage_wiring.py
```

| 测试 | 结果 |
|---|---|
| `smoke.mjs` | ✅ OK |
| `e2e-buglog-20261128.mjs`（新增，端到端） | ✅ **19 / 0** |
| `test_regression_buglog_20261128.py`（新增，判据） | ✅ **23 / 0** |
| `test_regression_seven_stage_wiring.py` | ✅ **290 / 0** |
| `test_regression_buglog_20261126.py` | ✅ **29 / 0** |
| `test_log_panels.py` | ✅ **33 / 0** |

### 顺手修掉的两条"永远红"的测试

- `test_regression_seven_stage_wiring.py` 里断言 `jobsChanged` 存在 —— 该变量
  在 2026-11-27 的重构（`ecd1c2b`）中已被 `plannerWorkReady` 取代。
  一条永远红的测试比没有测试更糟（没人再信它），已改为检查现有真实判据。
- `test_regression_buglog_20261126.py` 里 4 条断言是逐字匹配旧写法，
  代码重构后必然失效。改为锁**语义**（"hits 来自命中数组而非工单数"）
  而不是锁表达式。

---

## 部署

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\sync-to-dsh.ps1
python engineering\tools\verify_sync.py
# 然后【完全退出并重开 DSH】（flow.js / workflow_gate.py 都是宿主侧）
```
