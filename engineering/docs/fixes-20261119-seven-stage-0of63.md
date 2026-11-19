# 七阶段分析 · 126 分钟只推进 0/63（2026-11-19 第二轮）

> 现场：会话 `session-1566d8e6-f18e-440f-a24f-f1dce56d57d1`，任务 `20261119_173044_AAF031E4`
> （"设计一台单级圆柱齿轮减速器"）
> 17:30 开工 → 19:36 收尾，**126 分钟，progress 全程 0/63**。
> 前序：[slow-1of63](./fixes-20261119-seven-stage-slow-1of63.md) ·
> [speed](./fixes-20261119-seven-stage-speed.md) ·
> [notify-queue](./fixes-20261119-seven-stage-notify-queue.md)

---

## 一句话结论

**这一次不是"慢"，是"白跑"。** 五个可归因的浪费叠在一起：

| # | 根因 | 实测代价 |
|---|---|---|
| ① | `wait_agent` 唤不醒，分析角色回合内空睡 | **52.1 min** |
| ② | `interpret` 每触发一次就整步重跑 | **~65 min**（5 个 revision） |
| ③ | `init` 把本任务的载荷工况误归档 → 3 轮 commit 注定过不去 | 结构性死结 |
| ④ | `review.submit` 裸错误码，15 连败 | **~4 min** + 困惑 |
| ⑤ | `root` 调 `interpret` 被拒，人机闭环多绕两跳 | ~3 min |

---

## ① `wait_agent` 白等 52.1 分钟

### 现场

knowledge 角色调用 `wait_agent(600000)` 共 10 次，合计睡眠 **70.9 分钟**。
逐次核对唤醒源（会话日志的 `agent/inbox/spliced` 记录）：

```
17:33:43 等198s  插件通知17:34:11 已到 → 直到 send_message 17:37:01 才醒   白等 170s
17:38:45 等 17s  插件通知17:38:50 已到 → 直到 send_message 17:39:02 才醒   白等  12s
17:40:04 等391s  插件通知17:46:24 已到 → 直到 send_message 17:46:35 才醒   白等  11s
17:48:57 等600s  全程无任何输入 → 纯空转                                     600s
17:59:05 等600s  插件通知18:05:45 已在队列 → 仍睡满                         白等 200s
18:12:12 等600s  全程无任何输入 → 纯空转                                     600s
18:23:03 等600s  全程无任何输入 → 纯空转                                     600s
18:33:26 等600s  插件通知18:37:53 已在队列 → 仍睡满                         白等 333s
18:43:56 等600s  插件通知18:44:17 已在队列 → 仍睡满                         白等 580s
```

### 根因

宿主的两个投递**不是同一个事件**：

| 来源 | 落到哪个队列 | 能否唤醒 `wait_agent` |
|---|---|---|
| `send_message` | `target='next-step'` | ✅ 能 |
| 插件 `notify`（`subagents.prompt`） | `target='next-turn'` | ❌ 不能 |

前 4 次之所以能醒，全靠 planner 手动 `send_message` 去戳它。
后 6 次没人戳，就每次睡满整 10 分钟。

### 修复

**把 `wait_agent` 从分析角色的工具集里摘掉**（不只是 guard 拦）：

```js
const READONLY_TOOLS = new Set(['send_message', 'list_agents']);          // 去掉 wait_agent
const ANALYSIS_TOOL_ALLOW = ['engineering_seven_stage', 'send_message', 'list_agents'];
```

**为什么是"撤掉能力"而不是"再写一遍文档"**：上一轮已经在每条通知正文里写了
「不要用 wait_agent 等待任务包」，现场照样用了 10 次 —— 工具列在那里，模型就会试。

**为什么不改投递方式**：`delivery` 只影响落到哪个队列，而 `wait_agent` 监听的是
另一个事件。这是宿主内部语义，插件改不动；能在插件侧做的只有"不给它这个机会"。

**口径**：分析角色唯一的等待姿势是 **yield 结束回合** ——
回合结束后插件再投递就会开新回合。这是插件与角色之间唯一的合法同步点，
也是"插件是大脑"这条设计的物理基础。`wait_agent` 恰好相反：它把模型锁在回合内，
**等于拒绝把控制权交回插件**。所以在分析期间它不是"只读工具"，而是"对抗调度的工具"。

---

## ② `interpret` 每触发一次就整步重跑

### 现场

真人分 4 次点选项（每次只答 1~3 个问题），于是 step 1.1 跑了 **5 个 revision**：

| revision | 起止 | 耗时 | 备注 |
|---|---|---|---|
| rev1 | 17:32:24 → 17:46:06 | 13.7 min | |
| rev2 | 17:46:06 → 18:03:01 | 16.9 min | |
| **rev3** | **18:03:01 → 18:03:15** | **0.2 min** | **只活 14 秒** |
| rev4 | 18:03:15 → 18:37:40 | 34.4 min | |
| rev5 | 18:37:40 → 19:36:03 | 58.4 min | 未 commit |

rev3 那 14 秒最典型：第一条 `interpret` 刚把 phase 打回 frame，
planner 刚写完 frame，第二条 `interpret`（同一批真人答复的第二段）又把刚写的作废。

每轮重跑的全套是：`knowledge（发工单→web_search→submit）→ preflight → design（含 calculate）→ review`。
全任务累计：search_order 4 次、search_fill 4 次、knowledge submit 4 次、
review submit 30 次、design submit 3 次 —— 全是同一小步的重复劳动。

### 修复

`interpret` 只有在**真的作废了既有产物**时才回退：

```js
const wouldInvalidate = s.records.length > from;   // 比 from 更早的已提交记录会被作废
const hasWork = wouldInvalidate || !!s.slot;       // 或者本步已有产物

if (hasWork) {
  // 原路径：bump revision、清 slot、清 jobs、回 frame
} else {
  // 纯增量：只登记事实，不动 revision / cursor / phase / slot / jobs
  // 唯一例外：evidence_wait 必须能被打断（缺证补上了却出不去会是新死锁）
  if (s.phase === 'evidence_wait' && info.classification !== 'unknown') {
    s.phase = 'frame'; s.waitReason = null; s.jobs = {};
  }
}
```

**这条判定由插件自己做，模型无权覆盖** —— 模型只能 `interpret` 一条事实并说明分类，
"要不要推翻已有工作"由插件按"有没有产物"这个客观事实决定。

效果：连答 4 次真人问题，最多回退 1 次（第一次真有产物时），而不是吃 4 次全套重跑。

---

## ③ `init` 把本任务的载荷工况误归档（结构性死结）

### 现场

`workflow_gate.cmd_init` 的残留判据（修复前）：

```python
elif _old_title and task_desc and _old_title[:20] not in task_desc:
    _is_stale, _why = True, "标题与本次任务不符：%s" % _old_title
```

- 载荷文件 `meta.task_id` **缺失**（老格式），只有 `title`：
  `"建造一个单级圆柱齿轮减速器（含箱体、箱盖…"`
- 本次任务描述：`"设计一台单级圆柱齿轮减速器（含箱体、齿轮轴…"`
- `title[:20]` = `"建造一个单级圆柱齿轮减速器（含箱体、箱盖"` → **不在**任务描述里
  （一个说"建造"、一个说"设计"）→ 判定为残留 → 归档进 `.stale/`

后果链：

```
17:32–19:23   gate_load_case.json 全程不存在（文件 CreationTime = 19:23:09，是后来手动拷回的）
   ↓
review 每轮登记同一条 blocking 异议 rv11-loadcase-unreadable
   ↓
commit 的硬规则：knowledge 或 design 的 gaps 里有 blocking:true 就不许 commit
   ↓
rev1 / rev2 / rev4 三轮注定过不去 —— 这才是 0/63 的直接原因
```

而且 `loadCaseOf()` 把 ENOENT 也吞进 catch，统一返回 `{unreadable:true}`，
错误提示写成"文件存在但读取失败"，把排查方向引到了"权限/编码"上，
真正的"这一局还没生成"反而看不出来。

### 修复 A：判据换成时间，不用标题

```python
if _old_tid:
    if str(_old_tid) != _this_task_id:
        _is_stale = True          # 有 task_id 就只认 task_id
else:
    _STALE_MIN_AGE_SEC = 3600     # 无 task_id 时看生成时间
    _age = 解析 generated_at 或文件 mtime
    if _age is None:
        _is_stale = False         # 判不了就不归档
    elif _age > _STALE_MIN_AGE_SEC:
        _is_stale = True
```

**为什么标题判据天生不可靠**：载荷文件的 `title` 来自**上一次任务**的描述文本，
两次任务只要措辞不同（"设计一台…" vs "建造一个…"），前 20 字必然不匹配。
而"是不是本任务的残留"这件事，**时间戳能给出确定性答案，标题只能猜**。

**为什么"判不了就不归档"**：归档错了 = 本任务必死（commit 永远过不去）；
不归档 = 可能沿用旧基准（还有机会被发现）。**必死比"可能偏"严重得多。**

### 修复 B：`loadCaseOf` 区分"尚未生成"与"读不了"

```js
if (!existsSync(p)) {
  return {path:p, pending:true,
    note:'载荷工况文件尚未生成 —— 这是流程的已知时序（workflow_gate 在 '
       + 'provide_context 第 3 次、即力学估算那一步才落盘），不是故障。'
       + '本步请把"额定载荷/材料/安全系数"登记为【非 blocking 的待补项】…'};
}
```

两者的含义完全不同：
- `pending` → **流程的已知时序**（分析在力学估算之前就开跑了），
  分析角色应当记为待补项、继续做不依赖载荷的工作；
- `unreadable` → **真故障**，该由主对话去查权限/编码。

---

## ④ `review.submit` 裸错误码，15 连败

### 现场

```
19:09:20  INVALID_FIELD: units 必须是【非空字符串】，实际收到 undefined
19:09:25  INVALID_LIST: operations          ← 不说收到什么、也不说为什么不行
19:09:34  INVALID_LIST: operations
19:09:40  INVALID_LIST: operations
19:09:48  INVALID_LIST: operations
19:10:35  STALE_OR_WRONG_JOB
19:10:49  INVALID_FIELD: content …
19:11:14  INVALID_FIELD: object …
19:11:33  INVALID_FIELD …
19:12:15 / 19:12:43 / 19:13:02 / 19:13:32 / 19:13:44 / 19:13:51 / 19:13:59 / 19:14:06 / 19:14:13 / 19:14:21
```

`issues` 一项就有 **10 个必填字段**（`key/object/requirement/mechanism/evidence/impact/needed/owner/close/kind`），
错一个就整单重来，模型只能一个字段一个字段地补。

### 修复

**一次性报全 + 逐项定位**：

| 错误码 | 修复前 | 修复后 |
|---|---|---|
| `INVALID_ISSUE_KIND_OR_ID` | 一个码 | `issues[0]` 的 key 是…，kind 必须是 hard\|unknown\|…（收到 "X"） |
| `INVALID_ISSUE_OWNER` | 一个码 | `issues[0]` 的 owner 必须是 knowledge\|design\|planner\|human（收到 "X"）；review 不能派给自己 |
| `DUPLICATE_ISSUES` | 一个码 | 列出重复的 key |
| `INVALID_CLOSURE_REFERENCE` | 一个码 | 列出不能关闭的 key **+ 可关闭的 key 清单** |
| `OPEN_OBJECTION_NEEDS_EXPLICIT_REVIEW_CLOSURE` | 一个码 | 列出未核销的 key **+ 两种正确处置** |
| `VERDICT_ISSUE_MISMATCH` | 一个码 | 报出 verdict 实际值、是否含 hard/unknown、**以及正确规则** |
| `CONDITIONS_REQUIRED` | 一个码 | 报出当前 issues 的 kind 列表 |
| `INVALID_LIST` | `INVALID_LIST: operations` | 报出实际类型 + 该项的正确形态（`LIST_HINTS`） |

---

## ⑤ `root` 不能 `interpret`，人机闭环多绕两跳

### 现场

真人答完 → root `capture` 成功 → root 接着调 `interpret`
→ `ROLE_ACTION_DENIED`（只有一句码）→ root 以为链路坏了，花约 1 分钟排查，
再改用 `send_message` 把 messageId 转给 planner。

### 修复（**没有放开权限**）

`interpret` 的实质是"给真人原话定性、并决定回退到哪一小步"，这是**判断**而不是**传话**。
交给 root 等于让模型自己决定"这条答复要不要推翻设计"，插件就不再是大脑了
—— **这条口径必须守住**。

改的是两处：
1. **拒绝理由写清"该谁做、怎么做"**：
   ```
   ROLE_ACTION_DENIED: interpret 归总计子代理（planner）—— 它负责给真人原话定性、
   并决定回退到哪一小步，这是判断而不是传话。
   你（真实主对话 / 传话员）的职责到 capture 为止，不需要也不应该自己做 interpret。
   现在该做的：什么都不用做。capture 成功时插件已经自动通知 planner 了；
   如果它迟迟不动，用 send_message 把回执里的 messageId="…" 转给它即可。
   ```
2. **capture 成功后插件主动叫醒 planner**：不再让传话员兼职跑腿。
   只对新捕获发通知（幂等返回不重复叫，避免制造无谓往返）。

---

## 回归

```powershell
node engineering\plugins\dsh-engineering-seven-stage\test\smoke.mjs
python engineering\tools\test_regression_seven_stage_wiring.py
```

| 测试 | 结果 |
|---|---|
| `smoke.mjs` | ✅ OK（63 步完整链不受影响） |
| `test_regression_seven_stage_wiring.py` | ✅ **PASS=191 / FAIL=0** |
| `run_regression.py` 全量 | 65 / 68 通过；3 个失败项在 **HEAD 基线（git worktree，未含本轮改动）上同样失败**，属既有失败，与本轮无关：`test_regression_a2_b3_wait_stall` / `test_regression_b8_heartbeat_archive` / `test_regression_dryrun_and_encoding`（这三项单独跑都是几秒内 PASS，只在 runner 汇总时有环境相关的顺序依赖） |

新增断言（12 项源码 + 6 项行为）：

**源码级**
- `wait_agent` 不在 `ANALYSIS_TOOL_ALLOW`、也不在 `READONLY_TOOLS`；
- `interpret` 有 `wouldInvalidate` / `additive` 分支；
- `init` 的可执行代码里不再有 `_old_title[:20] not in task_desc`（注释里解释旧写法是允许的）；
- 改用 `STALE_MIN_AGE_SEC` + `getmtime`；
- `loadCase` 区分 `pending` / `unreadable`；
- submit 错误带 `issues[idx]`、`LIST_HINTS`；
- capture 后通知 planner；
- **root 仍然不能 interpret**（口径不许漂移）。

**行为级**
- 有产物时 `interpret` 仍然回退（revision+1 且回 frame）；
- slot 为空时连发两条 `interpret` **只回退一次**；
- `INVALID_LIST` / `INVALID_ISSUE_KIND_OR_ID` / `VERDICT_ISSUE_MISMATCH` 都报出实际值。

---

## 权限边界核查（确保大脑与传话员地位不变）

修复后重新验证五条边界，全部保持：

| 检查 | 结果 |
|---|---|
| knowledge 冒充 `commit` | `ROLE_ACTION_DENIED` ✅ |
| root 调 `interpret`（判定权） | `ROLE_ACTION_DENIED` + 说明归 planner ✅ |
| knowledge 自己 `search_fill`（自造证据） | 被拒 ✅ |
| planner `interpret`（正常判断） | 正常执行 ✅ |
| design 调 `frame`（绕过插件改 phase） | `ROLE_ACTION_DENIED` ✅ |

**结论**：插件仍是唯一能改状态的通道；判定权仍归总计子代理；
传话员仍只能搬运真人原话。这一轮改的都是**通道与提示**，没有动权限。

---

## 部署

```powershell
.\sync-to-dsh.ps1 -DryRun -Prune
.\sync-to-dsh.ps1 -Prune
python engineering\tools\verify_sync.py
# 然后【完全退出并重开 DSH】（插件 host 半是进程级）
```
