# 外部监控器"接入失败"复盘（2026-11-12 真机会话 630e2e4d）

> 结论：**文件确实导入成功了**，失败发生在【接线】而不是【安装】。
> 用户在 22:29 问"没发挥作用，没导入成功吗"，当时的判断是对的 ——
> 功能确实没生效。但原因是三个代码缺陷，任何一个都足以让整条链路失效。

## 现场证据（会话 630e2e4d 导出）

| 时刻 | 事件 |
|---|---|
| [12] | 工作目录 `C:\\Users\\j1877\\Desktop\\test`，GUI 端口 **57496** |
| [348] | `mode_gate.py sw-monitor status` → `enabled: false` |
| [373] | `sw_monitor.json` 存在性检查 = **False** |
| [381] | `sw_monitor/` 目录**内容完整**（monitor.py / dsh_evidence.py / host-adapter.mjs …） |
| [395] | `sw-monitor attach --drained` → `MONITOR_BRIDGE_UNREACHABLE: 连不上 DSH 宿主桥（127.0.0.1:19387）` |
| [530] | 用户："所以现在是没发挥作用？那你停掉，我去修一下" |

关键矛盾：**目录齐全、代码也对，但连不上。** 这句话就是定位起点。

## 缺陷 1（致命）：宿主端口写死成 19387

**位置**：`tools/sw_monitor_client.py`

```python
ENV_PORT = "DSH_WEB_PORT"
DEFAULT_PORT = "19387"          # ← 死的
def _port(): return os.environ.get(ENV_PORT) or DEFAULT_PORT
```

**真机事实**：DSH 的 Web GUI 端口**每次启动都变**（本次 57496），
而 `DSH_WEB_PORT` 在真实会话里**根本没被注入**。

实测：
```
57496 → HTTP 200 {"ok":true,"enabled":true,"attached":false}
19387 → 无法连接到远程服务器
```

**后果**：`sw-monitor attach` 永远失败 → 监控器永远 `attached: false` →
所有建模命令 `MONITOR_NOT_CONFIGURED` 拒绝 → 看起来像"没导入成功"。

**根因（我的疏忽）**：`ask_user.py` 早就踩过同一个坑（默认 3080，实测全部
WinError 10061），修法是读宿主注入的 `DSH_WEB_URL`。我写监控器客户端时
用了同样的 `DSH_WEB_PORT` 口径，却没意识到它同样不被注入。

**修复**：按 `DSH_WEB_URL` → `DSH_WEB_URL_ALT` → `DSH_WEB_PORT` →
固定端口 的优先级解析，并把 `bridge_source` 暴露在 status 里
（排障时一眼看出"是不是回落到固定端口了"）。

## 缺陷 2：任务/编制从未自动登记

**位置**：`workflow_gate.py` 的 `select`

监控器对未知 task 一律 `TASK_UNKNOWN`。原实现只在 `mode_gate.py sw-monitor plan`
里提供手动登记，而**没有任何流程会调用它**。于是即使 attach 成功，
看板与许可仍然全部被拒。

**修复**：`select` 落盘后自动把 task + 编制登记进监控器（这是房间表定型的
唯一时刻）。失败不阻断 select，但结果写进返回体的 `monitor_plan`。

## 缺陷 3：`cmd_sw_monitor_plan` 里 `work_dir()` 未定义

```
NameError: name 'work_dir' is not defined
  at mode_gate.py::cmd_sw_monitor_plan
```

手动 plan 也必崩 —— 等于"唯一的登记入口也是坏的"。
**修复**：改用既有的 `_default_work_dir()`（它本来就是"产物在哪"的权威推断，
且已修过双目录问题）。

## 附带修复：`--rooms-json` 不认文件路径

只认内联 JSON，PowerShell 下引号被吃掉就报
`Expecting property name enclosed in double quotes`，使用者无从下手。
现与 `mode2_flow.py` 统一：内联 / 裸路径 / `@路径` 三种都支持。

## 验证（修复后，真机）

```
status  → bridge: http://127.0.0.1:57496/dsh-engineering-ui/sw-monitor-op
          bridge_source: env:DSH_WEB_URL
attach  → {"state": "ready"}            ← 之前必失败的那一步
plan    → {"ok": true, "rooms": 1}
board   → ok=true, pulse_alive=true, parts: [DSH_测试件 / 未开始 / 第0代]
```

## 教训

1. **"文件装好了"不等于"功能生效了"。** 验收必须跑到
   `attach → ready` + `board → ok`，而不是 `verify_sync` 通过就收工。
2. **本地端口一律不许写死。** DSH 每次启动换端口，唯一的可靠来源是宿主注入的
   `DSH_WEB_URL`。这条对 `ask_user.py` 成立，对任何新加的本地 HTTP 客户端同样成立。
3. **登记类入口必须在主流程里自动调用。** 提供 `plan` 命令但没人调，
   等于没有 —— 和"写了函数没人调用 = 没写"是同一条纪律。
4. **我自己的 e2e 测试没抓住这些**：它用 `DSH_WEB_PORT` 注入假端口，
   恰好绕开了"真实环境不注入"这个前提。测试替身替得太好，反而屏蔽了真实缺陷。
