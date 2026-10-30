# 2026-10-29 第四轮：子代理误判死亡 / 重复 fork / 重复弹题

会话存档：`dsh-session-session-ceac3fff-da56-420e-b7db-65154970405a.zip`

## 事故现场（日志实测时间线）

```
18:20:52  fork #1  fff32ea0   ← 实际一直活到 18:28:34（462 秒）
18:21:10  wait_agent   → noProgress(no-active-peer)
18:21:22  list_agents  → 只看到 lead        ← 误判"已死"
18:23:24  fork #2  11de1d27   ← 此刻 #1 仍在跑
18:25:40  fork #3  6bf2ef59   ← 此刻 #1 #2 都还在跑
18:24~18:28  三个子代理互相 analyst-bind --force 抢身份 9 次
             各自调 ask_user.py 提问 → 用户看到已答过的选项反复弹出
```

## 根因（两条，缺一不可）

1. **平台层**：`subagent_fork` 出来的子代理**不是 Team member** ——
   `list_agents` 只返回 lead，`wait_agent` 返回 `noProgress(no-active-peer)`。
   主对话因此**没有任何手段**判断 fork 出去的子代理是否还活着，只能靠"没看到"来猜。
2. **门禁层**：`analyst-bind` 只记 sessionId，不记"它还活着吗"。
   绑定存在 ≠ 子代理在跑；反过来"绑定存在"也**没能阻止**再 fork 一个。

## 修复

### 一、主进程脉冲 pulse（唯一权威判活依据，≈99% 可靠）

心跳由 **DSH 主进程的 `setInterval`（每 10s）** 写，**与子代理在干什么完全无关**：

| 场景 | 脉冲行为 | 判定 |
|---|---|---|
| 子代理跑 SolidWorks / 等锁 / 发呆 | 照跳 | 活着 ✅ |
| 子代理被 `disposed` | 从内存台账移除 | 已结束 ✅ |
| DSH 整个进程没了 | 脉冲停 | 判死 **正确**（此时所有子代理也没了） |

关键设计：**"没有 pulse 字段" ≠ "pulse 过期"**
- `pulse_stale`（字段在但停了）→ DSH 进程没了 → 允许判死；
- `no_pulse_field`（老插件没这个字段）→ 判据缺失 → **绝不判死**，退回弱判据。

落点：`plugins/dsh-engineering-ui/lib/index.js`（`pulseTick` / `_liveAgents`）、
`mode_gate._pulse_state` / `_platform_liveness`、`room-board` 顶层 `pulse` 字段。

### 二、fork 前置闸门 analyst-guard

```powershell
python mode2_flow.py analyst-guard     # fork 之前必须先跑
```

活着 → 返回 `gate=ANALYST_ALREADY_ALIVE` 并**拒绝重复创建**，
同时给出 `next`（等它 / send_message 催 / 什么条件才允许重开）。

### 三、拒绝抢身份

`analyst-bind` 在"旧 analyst 心跳或平台证据显示仍存活"时返回
`gate=ANALYST_STILL_ALIVE`。**平台 disposed 是终态**，心跳不能覆盖它
（否则被 disposed 的子代理会因残留心跳永远挡着接班人）。

### 四、题目不再重复弹

两层防护：

| 层 | 机制 | 覆盖场景 |
|---|---|---|
| 跨会话去重 | 插件支持 `rootSessionId=*` 全局查；`ask_user.py` 比对**所有子代理**的未结卡片 | 三个子代理同时问同一题 |
| 已答台账 | `ask_state/ask_answered.json`，按题目语义指纹记录答案（TTL 2h） | 卡已结算，另一个子代理稍后又问同一题 |

命中别人的卡 → 返回 `gate=ASK_DUPLICATE_OWNED_BY_PEER`，不再打扰用户。

## 旧代码已删除（不留双份）

- 删 `mode_gate._plugin_liveness`（旧的"lastEventAt 单点判活"），
  全部改走 `_platform_liveness` + `_pulse_state`；
- 修掉 2 处残留调用（`mode_gate` 迁移表、`workflow_gate.cmd_recover`）；
- `ask_user.py` 的 `_norm_text` / `_opts_of` 从 `main()` 闭包提到模块级
  （原先台账在模块级调不到它们，指纹恒 None，台账形同虚设）。

## 回归

```
python engineering/tools/test_regression_20261029_liveness.py   # 49 / 49（新增）
python engineering/tools/run_regression.py                      # 34 / 34 全通过
```

## 生效方式

插件 host 半是**进程级** → **必须重启 DSH 桌面端**，pulse 才会开始跳。
重启后可用 `python mode_gate.py room-board --json` 看顶层 `pulse.alive` 是否 true。
