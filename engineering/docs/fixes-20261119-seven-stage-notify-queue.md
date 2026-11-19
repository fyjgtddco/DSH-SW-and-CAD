# 七阶段分析 · 通知排队堆积（2026-11-19）

> 现场：截图显示主对话/子会话侧堆了 **4 条**「七阶段插件任务…有新任务包」
> 「…检索工单已回填…」，卡在队列里逐个 turn 跑，界面显示"深度求索中，用时 16 分 14 秒"。
> 前序：[slow-1of63](./fixes-20261119-seven-stage-slow-1of63.md) ·
> [handshake](./fixes-20261119-seven-stage-handshake.md) · [speed](./fixes-20261119-seven-stage-speed.md)

---

## 一句话结论

**投递方式选错了。** 插件用的是 `delivery:'queue'`，宿主把它翻译成 `agent.followup()`，
而 followup 的语义是"**每条消息独占一个完整的 turn**"。
于是 4 条状态通知 = 4 轮完整的 LLM 往返，而不是"提醒一下"。

---

## 宿主的两种投递语义（源码原文）

`@deepseek-ai/dsh-agent/src/runtime-types.ts`：

```ts
/** Queue an ordinary follow-up turn and wake the driver.
 *  The item becomes the sole ordinary message of its own turn. */
followup(message: UserMessage): void

/** Submit steering for the nearest step. An idle driver starts a turn;
 *  a running driver consumes it at its next step boundary. */
steer(message: UserMessage): void
```

`@deepseek-ai/dsh-subagent/src/manager.ts:657`：

```ts
if (delivery === 'steer') local.handle.agent.steer(message)
else local.handle.agent.followup(message)
```

| 投递方式 | 宿主行为 | 后果 |
|---|---|---|
| `queue` → `followup` | 每条消息**独占一个 turn** | 4 条通知 = 4 轮往返 |
| `steer` → `steer` | 插队到**最近一步**边界；空闲时直接开 turn | N 条通知最多 1 轮 |

---

## 两个原因叠加

### ① 投递方式：`queue` 逐条排队

`lib/index.js` notify()（修复前）：

```js
Promise.resolve(ctx.subagents.prompt({
  requestId:randomUUID(), childSessionId:sid, delivery:'queue',   // ← 问题在这
  content:[{type:'text',text:message}]
}, AbortSignal.timeout(15_000))).catch(failed);
```

root 侧同理，用的是 `live.followup(msg(message))`。

### ② 去重失效：key 一变就重发

`route()` 在**每次工具调用后**都会跑，靶心 key 是：

```js
const key = targetPhase(s) + '|' + searchKey(s);
// targetPhase 含 revision / cursor / phase / jobs / ended / failures / facts ...
```

状态一动 key 就变 → `wakeups.get(n) === key` 不成立 → 再发一条。
而所有通知的**实质都是同一件事**："去调 status 读最新状态"。
status 读的是磁盘当下值，所以**只要一条就够了**，早发的必然过时。

---

## 修复

### ① `queue` → `steer`

```js
// 子会话
delivery:'steer',
// root：steer 优先，失败才退回 followup
try { live.steer(msg(message)); } catch { live.followup(msg(message)); }
```

### ② 在途合并：同一 (task, sid) 未消费前不再投递

```js
const inflight = force ? undefined : inFlight.get(n);
if (inflight) {
  inflight.text = message;     // 只保留最后一条
  inflight.key = key ?? inflight.key;
  wakeups.set(n, key);
  return;                      // ← 不投递
}
inFlight.set(n, {text:message, key, at:Date.now()});
```

**为什么保留最后一条而不是把所有条拼接**：每条通知都以"请先调 status 读当前状态"收尾，
status 永远给最新值，旧的必然过时 —— 拼进去只会让 prompt 更长。

**消费点**：角色只要真的调用了一次工具，就说明它醒了：

```js
// opAction 最前面
markNotifiedConsumed(taskId, a.id);
```

位置放在最前面是刻意的：即使这次调用会因为别的 check 被拒，也证明它醒了。

**失败必须清标记**，否则这个 (task,sid) 会被永久静音：

```js
const failed=()=>{ inFlight.delete(n); ... };
```

### ③ `deliver` 保留强制重发

`deliver` 是**人工显式重发**（有人主动想再催一次），必须绕过在途合并：

```js
notify(taskId, recipient, '重读待办和已落盘的消息。', targetPhase(prior), true);
```

> 用显式形参 `force = false` 而不是读 `arguments`：后者在参数增删时会静默失效，
> 是最难查的那种 bug。

---

## 效果

| 场景 | 修复前 | 修复后 |
|---|---|---|
| 一次状态推进触发 4 条通知 | 4 个独立 turn，4 轮 LLM 往返 | **1 轮**（steer 插队，其余合并） |
| 角色空闲时收到通知 | 排到下一个 turn | steer **直接开 turn** |
| 角色正在跑 | 等当前 turn 完全结束 | 在**下一个 step 边界**插入，不打断当前步 |
| 人工 deliver | 可能被去重吃掉 | 强制发送 |

配合上一轮的 `wait_agent` 禁用指引（不再有角色睡死等待），
"通知发出去了但没人动"和"通知发太多把人淹了"两个极端都堵上了。

---

## 回归

```powershell
node engineering\plugins\dsh-engineering-seven-stage\test\smoke.mjs
python engineering\tools\test_regression_seven_stage_wiring.py
```

| 测试 | 结果 |
|---|---|
| `smoke.mjs` | ✅ OK |
| `test_regression_seven_stage_wiring.py` | ✅ **PASS=178 / FAIL=0**（上轮 171 + 本轮 7 项） |

新增断言：
- 子会话投递用 `steer`，**可执行代码里已不存在** `delivery:'queue'`（注释里解释旧写法是允许的）；
- root 用 `agent.steer` 而非 `followup`；
- 同一 (task,sid) 未消费前不再重复投递（在途合并）；
- 角色调用工具即解除在途标记；投递失败必须清标记；
- `deliver` 显式重发可绕过合并。

> 顺带修了 `smoke.mjs` 一处**因语义变化而过时**的断言：
> 原来断言"恰好 1 条 steer"，现在改为"至少 1 条且正文非空" ——
> 条数是实现细节，而"不许一条都没有"才是这个用例真正要守的东西。

---

## 部署

与之前一致，三处都要同步（仓库 / 交付目录 / DSH 安装副本），**完全退出并重开 DSH**。
