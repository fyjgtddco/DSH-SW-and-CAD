# 修复：会话"一直在载入历史" —— 宿主事件循环被同步子进程冻死

**日期**：2026-11-17
**报告**：用户 —— 「DSH 会话要加载历史很久，工程模式的会话一直显示会话历史加载中；
从工程模式会话换到普通会话，也要卡差不多 10 秒」

---

## 一、现场取证（真实 DSH，端口 53755）

| 请求 | 耗时 | 说明 |
|---|---|---|
| `GET /dsh-engineering-ui/compaction-history?limit=400` | **268 ms** | 单独打很快 |
| `GET /dsh-engineering-ui/mem-guard` | 459 ms | 单独打也快 |
| `GET /dsh-engineering-ui/parts-board` | **30,287 ms** | ← 常年超时 |
| `parts-board` + `compaction-history` **并发** | 30,278 / **30,025** ms | 两条同时被拖死 |

第三条是关键判据：**本来 268 ms 的历史接口，只要和 parts-board 并发就变成 30 秒**。
这说明不是"某条路由慢"，而是**整个宿主事件循环被冻住**。

会话总数 413 / 236 MB，最大单文件 3.4 MB —— 与加载慢无关（先排除掉这个怀疑方向）。

---

## 二、根因：自死锁

四处事实拼起来：

1. `pyEnv()` 会把 `DSH_WEB_URL=http://127.0.0.1:<真实端口>` 注入每个 Python 子进程
   （2026-11-16 的修复，为了让 Python 找到宿主真实端口）。
2. `mode2_flow.py parts-board` 的实现是 **rpc('board')** —— 要**回连宿主**的
   `/dsh-engineering-ui/sw-monitor-op` 去问外部监控器。
3. 宿主用 **`spawnSync`** 起这个 Python —— 同步 API 会**独占事件循环**。
4. Python 侧 `CALL_TIMEOUT_S = 30`。

于是：

```
① 宿主收到 /parts-board → spawnSync 起 Python → 事件循环冻结
② Python 要回连 127.0.0.1:<port> → 宿主无法 accept（冻着）
③ Python 等满 30 秒 → 超时 → 返回 degraded
④ spawnSync 返回 → 宿主解冻
```

**它自己把自己等死了。** 而且这 30 秒里，**所有会话的所有 HTTP 请求**（包括历史加载、
子代理面板、设置页）全部排队。

### 实证（同一命令，只改宿主忙闲）

| 条件 | `mode2_flow.py parts-board` 耗时 |
|---|---|
| 宿主空闲 | **232 ms** |
| 宿主正忙（在服务同一路由） | **29,349 ms** |

差 **126 倍**。这就是"一直在载入历史"的直接来源。

---

## 三、修复

### 3.1 路由内一律异步

新增 `execFileAsync()`（Promise 包装 `child_process.execFile`），并把**所有会在
请求路径里起子进程**的入口改成 `await`：

| 位置 | 改法 |
|---|---|
| `/parts-board` | `spawnSync` → `await execFileAsync`（本次事故落点） |
| `runConnProbe` | → `async`，内部 `await execFileAsync` |
| `/conn-launch` | → `await execFileAsync`（它最长 240s，同步等于把宿主挂死 4 分钟） |
| `runEnvCheck` / `runSettings` | → `async` + `await execFileAsync` |
| `probePythonExe` | → `async`（每个候选要起 2 个 Python 进程） |

### 3.2 Python 选择移出请求路径

`resolvePython()` 保持同步签名（几十处调用，整体改 async 牵动太大），但：

- **缓存优先**：命中 `pyCache` 直接返回，零子进程；
- **预热通道**：`warmPythonCache()` 在 `apply()` 启动时跑一次，之后每 5 分钟刷新，
  结果存 `pyWarm`；请求路径只吃缓存；
- **同步探针只做浅探测**（`probePythonExeSync`，只问版本号），
  耗时的 `import win32com.client` 深探测留在异步版。

> ⚠️ 顺序即契约：`detail` 分支曾被写在缓存判断之前，导致"环境检查"每次都同步起
> N 个 Python 进程。现已把缓存判断提到最前，并有测试钉住。

### 3.3 顺带修掉的脆弱断言

`env-check.test.mjs` 的 ④/⑩ 两条用了"字符窗口 + 只认 spawnSync"的写法，
新增异步通道后立刻假失败。已改为按**函数边界**取段、并同时覆盖两条通道。

---

## 四、验证

| 项目 | 结果 |
|---|---|
| 事件循环实测（子进程 2s 期间定时器跳动） | **32 次 / 2s** ✅ 未阻塞（同步会是 0） |
| 新增 `host-nonblocking.test.mjs` | **6 / 6** |
| UI 插件全量 | **267 / 267** |
| 安装副本 SHA-256 | 完全同步 |

新增的 6 条断言（防止再犯）：

1. 路由处理器内**不得出现** `spawnSync`（覆盖 7 条会起子进程的路由）；
2. `execFileAsync` 必须存在且真的调异步 `execFile`、防重复 resolve；
3. `/parts-board` 必须 `await execFileAsync`；
4. 相关函数必须声明为 `async`（否则 `await` 无效）；
5. `resolvePython` 必须先查缓存/预热再退回同步探测，且 `apply()` 启动期预热；
6. 同步探针不得做 `pywin32` 深探测。

---

## 五、教训

1. **同步子进程 + 回调自己的服务 = 必然自死锁。**
   只要子进程可能回连宿主（本项目的 Python 大量这么做：监控器桥、记忆契约、
   端口探测），宿主侧就**绝不能用 `spawnSync`**。
2. **"某条接口慢"要先验证是不是"全局慢"。**
   并发打两条、看**互不相关的那条**是否也慢 —— 这是区分"这条有问题"和
   "宿主被冻住"最便宜的判据（本次就是靠它定位的）。
3. **测试要断言"用了哪条通道"，不只是"结果对不对"。**
   同步/异步都能返回正确 JSON，但只有异步不会冻死宿主 —— 这类缺陷单看返回值发现不了。
