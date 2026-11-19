# 七阶段分析插件 · BUG-1~4 修复记录（2026-11-18 实机）

> 现场报告：`C:\Users\j1877\Desktop\test\20261118_211912_BA97EB57\reports\bugs_七阶段分析.md`
> 任务：一级圆柱齿轮减速器设计建造 · 终态 `phase=partial` / `progress 0/63` / `job=null`
> 相关：[七阶段换代_合并说明.md](./七阶段换代_合并说明.md)

---

## 结论速览

| BUG | 现象 | 根因 | 修法 | 回归 |
|---|---|---|---|---|
| **1** | `search`→`SEARCH_KEY_MISSING`；`web_search` 又被门禁拦 → 知识无任何联网途径 | 插件**自己**联网且要求部署方配 Brave key / 自建端点；同时知识工具被 `SEVEN_STAGE_TOOL_GATE` 关死 = 双重断路 | **把联网外包给传话员**：知识发检索工单 → 真实主对话用宿主内置 `web_search` 执行 → `search_fill` 回填 | `test_regression_seven_stage_wiring.py` F4（+ smoke 零联网断言） |
| **2** | status 显示 job `4729feef`，但四个动作回四个**互相矛盾**的错误码 | `interrupt()` 只登记 failures、**不清僵尸 job**；`retry()` 只清 failures、**不重发 job**；`blocked` 要求持有 job 因而拒绝"我卡住了" | interrupt 作废 job（记 `orphanedJob`）；retry 重新发 job（`reissuedJobId`）；blocked 放宽到「处于自己的阶段即可」；拒绝时回传 `allowed` 清单 | F4 |
| **3** | planner 全程 `job=null`、progress 0/63、三角色 0 产出，"等待任务包"看不出在等谁 | 流水线本就串行（knowledge→preflight→design→review），上游卡住下游就是 `job=null`；status 却不说明**在等谁** | `view()` 增加 `waitingOn`；planner 的 status 增加 `dispatch` 派发矩阵 | F4 |
| **4** | 四角色都 `end(partial)` 后，`next` 文案不刷新（复现 5 次） | `next` 只按 `s.phase` 查固定字符串，**完全不看 `s.ended`** | `nextText()` 按「还剩谁没登记」实时生成 | F4 |

---

## BUG-1 · 联网搜索改为「外包给传话员」

### 用户口径（本次修改的唯一依据）

> 「bug1 是改成由那个外部插件去发布指令让去调用 DSH 的插件去，将联网搜索外包出去，
> 然后去给 DSH 联网搜索了之后，再返回会出插件」
>
> 「记得，不能打破插件现有的大脑和传话员角色的绝对地位」

### 修复前（错在哪）

```js
// lib/index.js（修复前）
async function braveSearch(query, signal) {
  const token = process.env.DSH_SEVEN_STAGE_BRAVE_KEY;
  check(token,'SEARCH_KEY_MISSING: 需要为 DSH 宿主配置 DSH_SEVEN_STAGE_BRAVE_KEY');
  ...
}
function webSearch(query, signal) {
  if (process.env.DSH_SEVEN_STAGE_SEARCH_URL) return hostSearch(query, signal);
  return braveSearch(query, signal);   // ← 部署方没配就是死
}
```

三重问题叠加：

1. 插件**自己**发 HTTP 请求，把联网能力绑在部署配置上；
2. 宿主没配 `DSH_SEVEN_STAGE_BRAVE_KEY` / `DSH_SEVEN_STAGE_SEARCH_URL` → `SEARCH_KEY_MISSING`；
3. 与此同时 `knowledge` 的 `web_search` 被 `SEVEN_STAGE_TOOL_GATE` 拒 → **没有任何联网途径**。

### 修复后（现在的架构）

```
知识(knowledge)  ──action=search──────►  插件落盘检索工单 job.searchOrder
                                          （不联网、不读任何 env）
真实主对话(root) ──status.search.order──►  调【宿主内置 web_search】实际检索
真实主对话(root) ──action=search_fill──►  插件收下来源 → job.searches
知识(knowledge)  ──action=submit──────►  按来源 ID 写依据 / 出处 / 边界 / 缺口
```

代码落点：

| 文件 | 新增/改动 |
|---|---|
| `lib/index.js` | **删除** `braveSearch` / `hostSearch` / `webSearch` / `pendingLookups`；`search` 改为发布工单；**新增** `search_fill`；`route()` 把工单状态变化通知到 root（待回填）与 knowledge（已回填） |
| `lib/flow.js` | **新增** `orderSearch()` / `fillSearch()`；`call()` 增加 `search` / `search_fill` 分支；`view()` 增加 `search.order` |

### 三条红线（改之前先读）

| # | 红线 | 强制点 | 为什么 |
|---|---|---|---|
| 1 | 插件进程内**绝不**联网 | 文件里已无 `fetch`，回归断言"没有 brave/host 后端" | 联网能力不该绑在部署配置上；也不该让插件进程成为网络出口 |
| 2 | 只有 `root` 能 `search_fill` | `SEARCH_FILL_RELAY_ONLY` | 若允许检索者自己回填 = 自己出证据，**知识→设计→独立复审**三件套直接失效 |
| 3 | 传话员不能改查询词 | `SEARCH_QUERY_MISMATCH`（query 必须与工单原文逐字一致） | 传话员只搬运，不替大脑定题、不替知识下结论 |

### 废弃的配置

`DSH_SEVEN_STAGE_SEARCH_URL`、`DSH_SEVEN_STAGE_SEARCH_TOKEN`、
`DSH_SEVEN_STAGE_BRAVE_KEY` —— **全部不再读取**。设置它们不会有任何效果。

> 旧的还原步骤「宿主配置 `DSH_SEVEN_STAGE_BRAVE_KEY`」已作废，
> 不要再按那份指引去配环境变量。

### 新的操作节奏（真实主对话）

1. `action=status` → 看到 `next` 里提到检索工单，或 `search.order.status==='pending'`；
2. 用宿主内置 `web_search` 搜 `search.order.query`（可拆多条，但每条都要单独发工单）；
3. `action=search_fill` 回填 `{jobId, orderId, query, hits:[{title,url,snippet}]}`；
4. 回到等待；知识的 `status` 会变成 `search.order.status==='filled'`。

---

## BUG-2 · 僵尸 job 与互相矛盾的错误码

### 现场证据（状态文件 `20261118_211912_BA97EB57.json`）

```
failures.knowledge.job = "4729feef-…"      ← 故障里记着这个 job
jobs = {}                                   ← 但 jobs 里已经没有它
phase = "partial"                           ← 任务早已部分收束
```

日志里 `13:29:46 interrupted knowledge 宿主释放会话，未办理结束`，
之后 `retry` 两次仍无法恢复 —— 因为 `retry` 只 `delete s.failures[role]`，
**没有重发 job**；而 `interrupt` 又把旧 job 留在 `s.jobs` 里，
于是 status 显示一个"看得见摸不着"的 job。

### 修复

```js
// interrupt()：会话被宿主释放 = 这个 job 没有任何活人能做，必须作废
const orphaned = s.jobs[role]?.id;
s.failures[role] = {reason, job:orphaned, step, ...(orphaned?{orphanedJob:orphaned}:{})};
if (orphaned) delete s.jobs[role];       // ← 不再留僵尸

// retry()：清完故障必须把活重新派出去
const issued = reissue(s, d.role);       // ← 新 job（新 id）
out.reissuedJobId = issued;
```

另外两处配套：

- `blocked` 判据从「必须持有 job」放宽成「处于自己的阶段即可」。原来 knowledge
  发布工单后想登记"我卡住了"会被 `ACTIVE_JOB_REQUIRED` 拒，只能谎报别的状态。
- `execute()` 的 catch 里现在回传 `phase` / `role` / `currentJob` / `searchOrder` /
  `waitingOn` / `allowed` —— 一次拒绝就能看清当前状态机允许什么。

---

## BUG-3 · 派发可视化

流水线本来就是**串行**的：`knowledge → preflight → design → review`。
上游不给活，下游就是 `job=null` —— 这是设计，不是 bug。

问题是 `status` 只说「等待任务包」，看不出「是我该派活，还是上游卡着」：

```js
// 现在 status 多两样东西
waitingOn: 'knowledge 推进 step 1.1'        // 当前卡在谁身上
dispatch: [                                  // 仅 planner 可见
  {role:'knowledge', jobId:'…', state:'working', needsSearch:true},
  {role:'design',    jobId:null, state:'idle'},
  {role:'review',    jobId:null, state:'idle'},
]
```

> `needsSearch:true` 是关键信号：知识手里有活但还没拿到来源，
> 说明**该真实主对话去执行工单了**。

---

## BUG-4 · next 文案跟着 ended 刷新

修复前只按 `s.phase` 查固定表：

```js
partial:'三个角色说明已有工作和遗留项，然后总计子代理、真实主对话分别 end'
// ↑ 四个角色全 end 了，这句话还是原样，主对话照做又问一遍（现场 5 次）
```

修复后按「还剩谁没登记」实时生成：

```js
function nextText(s, role) {
  if (terminal.has(s.phase)) {
    const open = unfinished(s);
    if (!open.length) return '全部角色已登记结束，分析交接完成…';
    return '还差 ' + open.join('、') + ' 登记结束（end，写清 kind 和 reason）。';
  }
  ...
}
```

---

## 回归与验证

```powershell
$env:PYTHONIOENCODING='utf-8'; $env:PYTHONUTF8='1'

# 插件本体（模拟宿主，含"插件零联网"断言）
node engineering\plugins\dsh-engineering-seven-stage\test\smoke.mjs

# 接线回归（新增 F4 段：BUG-1~4）
python engineering\tools\test_regression_seven_stage_wiring.py
```

本次结果：

| 测试 | 结果 |
|---|---|
| `smoke.mjs` | OK（检索工单外包链路 + 插件零联网 + 63 步完整链） |
| `test_regression_seven_stage_wiring.py` | **PASS=103 / FAIL=0**（其中 F4 段 11 项源码断言 + 23 项行为断言） |

F4 行为验证覆盖：

- 知识能发工单、不能自己回填、planner 也不能回填；
- 回填 query 与工单不一致被拒；
- 回填后知识拿得到来源并能 submit；
- `interrupt` 后 job 消失、`status.job===null`、`orphanedJob` 留痕；
- `retry` 发新 job 且 status 与之一致、检索/计算从零开始；
- `dispatch` / `waitingOn` 存在且形状正确；
- `partial` 后 `next` 不再是旧文案、会点名剩余角色、全部登记后说明交接完成。

---

## 部署（改完必须做）

```powershell
.\sync-to-dsh.ps1 -DryRun -Prune     # 先看要复制/删除什么
.\sync-to-dsh.ps1 -Prune             # 实际同步
python engineering\tools\verify_sync.py
# 然后【完全退出并重开 DSH】（插件在宿主进程内加载）
```

> 只改 `engineering/` 下的源码不会影响正在跑的 DSH —— 必须同步 + 重启。
