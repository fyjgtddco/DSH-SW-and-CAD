# 七阶段插件 · search_fill 后 knowledge 不动（2026-11-26）

> 现场报告：`C:\Users\j1877\Desktop\test\bug_report_seven_stage_stuck.md`
> 任务：`20261125_211324_905E57B6`（6 轴机械臂）· step 1.2 · knowledge 阶段
> 报告结论："知识角色未响应 submit，疑似调度/通信故障"

---

## 一句话结论

**报告的结论不成立。** 我逐条核对了 knowledge 会话日志（zstd 分帧解压，284 条事件），
证据显示：**插件唤醒正常、status 数据完整、submit 也能成功** —— 缺的是一句

"**回填已完成，现在该 submit 了**"。

```
01:53:31  插件唤醒 knowledge          ← 通道正常
01:53:32  knowledge 调 yield          ← 它以为还在等回填
          → PENDING_JOB_USE_SUBMIT_OR_REPORT_BLOCKER（被拒）
01:53:36  knowledge 调 status         ← 拿到完整 job（6 条 hits 全在）
          → 但 next 只有一句通用指引，没说"现在可以提交"
01:53:36~01:54:12  停在那里 38 秒，零产出   ← 真正的卡点
01:54:12  root 手动 stop → 广播"进入终态"  ← 这条广播把 inbox 撞起来
01:54:14  knowledge 才去 submit（带旧上下文）
          → NO_ACTIVE_JOB_FOR_ROLE（任务已 partial）  ← 自毁
```

---

## 报告的四条推测，逐条核对

| 报告推测 | 核对结果 |
|---|---|
| 1. 知识角色会话是否正常响应外部消息 | ✅ **正常**。`agent/inbox/spliced target=next-step` 在 01:53:31 成功投递，模型确实被唤起 |
| 2. 状态机转换逻辑有问题 | ❌ 状态机无误。01:53:36 的 status 返回 `phase=knowledge`、完整 job、6 条 hits、`search.order.status=filled` |
| 3. search_fill 后是否正确触发下一步 | ⚠️ **触发正确，但"下一步是什么"没说清** —— 这是唯一真问题 |
| 4. 可能需要重启知识角色会话 | ❌ 重启无用。与会话无关，是信息缺口 |

---

## 真正的两处缺口

### 缺口 A：`status` 不回答"现在能不能提交"

`job.nextAction` 只有动作模板（`{jobId,content,sources,gaps}`）和一句

"正文先用 read 取，再提交" —— **没有区分"还在等回填"与"回填好了可以交"**。

对一个只会看 status 的模型来说，这两种情况的 status 长得几乎一样，
于是它选了保守做法：再 yield 等一等。

**修法**：给 knowledge 的 `nextAction` 加一个 `ready` 闸门，把检索进度直接翻成可执行判断：

| 状态 | `ready.state` | note 里写什么 |
|---|---|---|
| 还没发单 | `no-search-yet` | 先用 `action=search` 发布工单 |
| 工单 pending | `awaiting-search-fill` | **不要 submit**，现在没有来源可用 |
| 已回填 | `ready-to-submit` | **现在就可以 submit**，sources 用 `hits[].id` |

这是**投影**，不是放宽判据 —— `submit` 仍由插件校验来源合法性。

### 缺口 B：等回填时 `yield` 被误拒

```js
// 修复前
check(!s.jobs[role], 'PENDING_JOB_USE_SUBMIT_OR_REPORT_BLOCKER');
```

这条把「持有 job」等同于「现在就该提交」。但对 knowledge 来说，
**发完工单后它确实在等外部回执** —— 这时逼它 submit 只会得到 `SEARCH_REQUIRED`。

现场它就是因为被这一句拒掉，才结束回合、然后在下一回合迷路。

**修法**：有 job 仍不许 yield（防偷懒），但**等外部回执的合法状态放行**：

| 情形 | yield | 理由 |
|---|---|---|
| 没发单 | ✅ 允许 | 确实还不知道干什么 |
| 工单 pending | ✅ **允许**（本次新增） | 这正是"我在等" |
| 已回填 | ❌ 拒绝 | 现在就该 submit，文案带 jobId 并指向 submit |

---

## 为什么这不算"打破插件独立性"

两处修改都只做**信息表达**，没有一条改变判据：

| 检查项 | 是否变 |
|---|---|
| `submit` 是否校验 sources 来自本 job 的回填结果 | ❌ 没变 |
| `submit` 是否校验 jobId / revision | ❌ 没变 |
| knowledge 能否自己造来源 | ❌ 仍然不能 |
| 谁能调 `interpret` | ❌ 仍然只有 planner |
| 有 job 时能否 yield 跳过 | ❌ 已回填时仍然拒绝 |

改的只是：**把插件已经知道的事实（检索到哪一步了）说出来**。

---

## 回归

| 测试 | 结果 |
|---|---|
| `smoke.mjs` | ✅ OK |
| `test_regression_seven_stage_wiring.py` | ✅ **PASS=290 / FAIL=0**（281 → 290，新增 F11 段 9 项） |

### F11 覆盖

| 断言 | 防的是 |
|---|---|
| 未发单 → `no-search-yet` | 缺口 A |
| 工单 pending → `awaiting-search-fill` 且写"不要 submit" | 缺口 A |
| **工单 pending 时 yield 被允许** | 缺口 B（现场就死在这） |
| 回填后 → `ready-to-submit` 且写"现在就可以 submit" | 缺口 A |
| **回填后 yield 仍被拒**，且带 jobId 指向 submit | 判据没被放宽 |
| 按指引 submit 成功并离开 knowledge 阶段 | 端到端可用 |
| 往轮回归 3 条（盲审/stop/attach 接管） | 别改回去 |

---

## 当前任务怎么救

任务已 `phase=partial`（被 root 正常 stop 收尾），**不需要也不能自动续跑**。两条路：

| 方案 | 操作 | 代价 |
|---|---|---|
| **A. 续跑**（推荐） | 重开 DSH 后 planner 调 `reopen {reason:"插件已修复检索回填后的推进指引"}` | 从 step 1.2 的 frame 重来，已提交的 1.1 保留 |
| B. 重开 | 新 init + 完整七阶段 | 63 步从头 |

> 续跑后 knowledge 会在 `status.job.nextAction.ready.state` 看到 `ready-to-submit`，
> 直接按提示提交即可，不会再停住。

---

## 教训

1. **"通道通了"不等于"说清楚了"。** 这次每条链路都正常：唤醒正常、数据完整、工具可用。
   失败在于 status 没有回答"下一步具体是什么"，于是模型用保守策略（再等等）自我停摆。

2. **同一个 status 不同状态必须可区分。** "等回填"和"回填好了"在旧 status 里几乎一模一样，
   模型无法判断该不该动手 —— 这是设计缺陷，不是模型能力问题。

3. **"持有 job" ≠ "现在就该干活"。** 有 job 时禁止 yield 的初衷是防偷懒，
   但它把"我在等外部回执"也一起禁了，逼模型在"违规"和"瞎交"之间二选一。

4. **报告的现象描述与根因可以完全相反。** 报告说"知识角色未响应"，实际是
   "插件叫醒了它、给了数据、却没告诉它下一步" —— 只有翻原始会话日志才能分清。