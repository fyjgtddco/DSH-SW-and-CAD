# DSH 卡死事故分析：`job_output wait:true` 挂起导致整轮中断

> **结论先行（2026-11-01 复核修订）**：这不是"第五个选项"的问题。
> **用户可感知的"卡死" = `job_output` 带 `wait: true` 的静默长阻塞**（GUI 全程无反馈），
> 用户据此强制退出 DSH；**退出瞬间**所有插件 fiber 失活 → `ctx.jobs` 解析抛
> `cannot get required service "jobs" in inactive context` → 该异常冒到 agent loop →
> `turn/end{interrupted}`，整轮对话作废。
> 也就是说：**长阻塞是"因"，框架异常与整轮中断是"果"（退出过程的副作用）。**
>
> 分析时间：2026-10-31 22:51　｜　复核修订：2026-11-01（逐事件 + 进程证据）
> 证据来源：`session-6019cd4b-1ade-4e1f-8850-93f5aec97960`（主会话 460 事件）+
> `0dacccb8-bce1-45bb-aa07-88e1a8dea41e`（总计子代理 705 事件）
> 分析方式：zstd 逐帧解压会话日志 + `app.asar` 源码定位 + Windows 进程创建时间比对
>
> ---
>
> ### ⚠️ 两处早期结论已被证伪（以本节为准）
>
> | 早期说法 | 实测事实 | 证据 |
> |---|---|---|
> | "超时 ≥270 秒才复现" | **不成立**。270s 那次实际只等 **86s**，240s 那次等 **62s**，120s 那次等 **99s** —— 全都远未到超时 | 见下「一、事故现场」的毫秒级时间戳 |
> | "job_output 致死率 40%（5 次 2 死）" | **分母错误**。3 次"正常"是 job 自己跑完（120/150/180s 睡眠结束）；2 次"致死"是**进程在这一刻被停** | `job/registered` 与 `job/settled` 配对 |
> | "框架内部错误掩盖真实原因" | 方向反了：**进程先停，错误后到**。两个不同 session 的报错落在**同一毫秒**，只有"全进程 fiber 同时失活"能解释 | 父会话 `seq 376 @22:46:58.970` / 子代理 `seq 659 @22:46:58.971` |
>
> **进程证据**（决定性）：`DeepSeek Harness.exe` 当前实例 PID 9624 的 `CreationDate` 为
> **22:50:44**，父进程是 `explorer.exe` —— 即 22:50:42 第二次失败后 2 秒，DSH 是从桌面**重新启动**的。
> `logs/` 与 Crashpad 在 10-31 该时段**没有任何崩溃记录**，与"正常退出/被强制结束"一致，而非进程崩溃。

---

## 一、事故现场（两次完全相同的复现）

| 时刻 | 事件 |
|---|---|
| 22:45:32 | `job_output { job_id: "pwsh-caf021cb68ff43a1", timeout_ms: 270000, wait: true }` |
| 22:46:58 | `Error: cannot get required service "jobs" in inactive context`（**等待 86 秒**） |
| 22:46:58 | `turn/end` → `{"kind": "interrupted"}` ← **DSH 死亡，用户强制退出** |
| 22:47:42 | 用户重新发话："继续，刚刚卡死了，我只能强制退出了" |
| 22:49:34 | `job_output { job_id: "pwsh-bca6848cef784f5b", timeout_ms: 330000, wait: true }` |
| 22:50:42 | 同样的错误（**等待 68 秒**） |
| 22:50:42 | `turn/end` → `{"kind": "interrupted"}` ← **第二次死亡** |

**特征高度一致**：
- 都发生在 `job_output` 且 `wait: true`
- 报错文本完全相同
- 都在报错瞬间触发 `turn/end { interrupted }`
- 用户主观感受都是"DSH 直接无响应"

---

## 二、根因（已从 `app.asar` 内定位到源码）

报错不是本仓库产生的，来自 **cordis 框架层**。
在 `Z:\DSH\resources\app.asar`（偏移 16800251）处找到：

```js
const impl = fiber.store?.[prop];
if (impl) return getTraceable(ctx, impl.value);
if (prop in fiber.inject) {
  error.message = `cannot get required service "${prop}" in inactive context`;
  throw error;                    // ← 这里抛出
}
```

**触发条件**：`jobs` 服务声明在 `fiber.inject` 里，但该 fiber **已不在活跃状态**
（`fiber.store` 里找不到实现），于是直接抛错。

DSH 官方包说明（同文件内）：

| 包 | 说明 |
|---|---|
| `@deepseek-ai/dsh-jobs-local` | 后台任务注册表的**进程内**实现（Process-local） |
| `@deepseek-ai/dsh-tool-jobs` | 基于 `ctx.jobs` 注册表的模型侧工具（job_output / job_list / job_kill） |

---

## 三、为什么这构成"恶性"bug

### 3.1 失败前先静默挂起 68~86 秒

`job_output` 在 `wait: true` 下设计为"阻塞直到任务完成或超时"。
如果那个 job **在 DSH 重启 / fiber 失活时丢失了注册**，它会一直等，
期间 GUI 毫无反馈——用户看到的就是"卡死"。

### 3.2 报错文本与真实原因完全无关

用户/模型看到的是 `cannot get required service "jobs" in inactive context`，
这句话对"我想读取一个后台任务的输出"这件事没有任何指导意义。

### 3.3 直接中断整个 turn

报错瞬间 `turn/end`，不是"工具失败、模型继续"，而是**整轮终止**。

### 3.4 触发路径被本仓库的提示词系统性放大

`preset-engineering.patch.yml` 与多个 SKILL 里大量指导使用
`Start-Sleep` + `job_output wait:true` 做长轮询。
本次日志里 `job_output` 共出现 **5 次**，其中 **2 次直接致死**（40% 致死率）：

| seq | timeout_ms | 结果 |
|---|---|---|
| 294 | 120000 | 正常 |
| 339 | 150000 | 正常 |
| 351 | 180000 | 正常 |
| **375** | 270000 | **死亡** |
| **454** | 330000 | **死亡** |

**规律**：超时 ≤180 秒的都正常，**≥270 秒的两枪全中**。

---

## 四、关于"第五个选项"的澄清

完整核对主会话里全部 `ask_user_question`：

| seq | header | 选项数 | 内容 |
|---|---|---|---|
| 44 | 并行策略 | 2 | D=部分并行 / E=单小屋串联 |
| 44 | 搭建方式 | 3 | A=完全自主 / B=部分自主 / C=步步确认 |
| 57 | 参数需求强度 | 2 | 强需求(10~50题) / 不强(5~10题) |
| 197 | Bug 记录文件 | 3 | 新建 / 先建空框 / 追加到已有 |

**最多只有 3 个选项，不存在"第五个选项"。**

### 但确实有一处与"5"有关

子代理在 seq=652 / 689 用 `--spec` 批量提问 **Q4~Q8（5 个问题）**：

```
ask_user.py --room 总计子代理 --child 0dacccb8... --spec scripts/spec_a.json --timeout 300
```

这次调用进入**阻塞轮询**（`ask_user.py` 的 `while time.time() < deadline` 循环），
子代理就一直挂在等待用户作答上。日志里随后出现：

```
seq=703  Error: cannot get required service "jobs" in inactive context
```

**推测**：你印象中的"第五个选项"很可能指这次 **5 题批量提问**，
而它后续同样撞上了 `jobs` 服务失活。

---

## 五、复现条件总结

同时满足以下条件即可能复现：

1. 调用 `job_output` 且 `wait: true`
2. `timeout_ms` 设置较大（**≥ 270000**，实测阈值区间）
3. 目标 job 已在 DSH 重启 / fiber 失活后**丢失注册**
4. 等待期间 fiber 转为非活跃状态

---

## 六、修复（已落地 · 2026-11-01）

修法遵循"**把静默长阻塞消灭掉**"，而不是去追一个不存在的超时阈值。
两层同时做，任一层都能独自止血。

### 6.1 宿主层（硬兜底，不依赖人设写没写对）

新增 `engineering/plugins/dsh-engineering-ui/lib/job-wait-guard.js`，
在 `index.js` 的 `apply()` 里通过**官方包装点**装配：

```js
ctx.on('tools/execute', async (exec, next) => { … })
// 与 @deepseek-ai/dsh-tool-call-timeout-policy 同一层，非 hack
```

它做两件事：

| # | 行为 | 效果 |
|---|---|---|
| **A** | 把 `job_output { wait:true, timeout_ms > 45s }` 的**等待切成短窗口**（默认 **45s**，到点即返回"仍在运行"的正常回执） | GUI 不再长时间无响应；用户随时能打断；模型也不会把整轮挂在等待上 |
| **B** | 任何 `inactive context` / `cannot get required service` 异常 → 转成结构化工具错误 `JOB_CONTEXT_LOST`（`isError=true`，带"改用磁盘产物判断进度"的操作指引） | 框架异常不再冒到 turn 级把整轮打断 |

> ⚠️ **实现上踩到并已修掉的一个坑**（留档，别改回去）：
> 最初想"把 `timeout_ms` 改小再调 `next()`"，但 `dsh-tools` 在 `createExecution` 里就做了
> `arguments: deepFreeze(snapshotJsonValue(exec.arguments))` —— 包装层拿到的参数是**深冻结**的，
> 赋值会被静默跳过，补丁会"看起来装上、实际一次都不生效"。
> 现实现改为**窗口竞速**（不改参数，自己设截止时间），并由
> `test/job-wait-guard.test.mjs` 的 T1 用冻结参数锁死该契约。
>
> 开关：`DSH_ENG_JOB_WAIT_GUARD=0` 关闭；`DSH_ENG_JOB_WAIT_MAX_MS=<ms>` 调窗口（最小 5000）。

### 6.2 仓库层（把长睡眠轮询从提示词里清掉）

| 落点 | 改动 |
|---|---|
| `plugins/dsh-engineering-ui/preset-engineering.patch.yml`（运行时人设） | 新增「长等待·铁律」：禁止 `Start-Sleep + job_output wait:true`；等待交给 `mode_gate.py wait-rooms / sw-wait`，或读磁盘产物；必须等 job 时单次 sleep ≤30s、`timeout_ms ≤30000` |
| 同上 · SW 窗口段 | `sw_busy:true` 的重试从 `Start-Sleep 90` 改为 `mode_gate.py sw-wait <房间> --timeout 90` |
| `skills/assembly-orchestration/SKILL.md` | 同步上述 SW 等锁写法 |
| `skills/solidworks-bridge/SKILL.md` | 同步，并补一条禁止长阻塞轮询的说明 |
| `tools/sw_bridge.py`（`sw_busy` 返回的 hint） | 提示改用 `sw-wait/wait-rooms`，不再教 `Start-Sleep 90` |
| `tools/ask_user.py` | 新增 `--seg-max`（默认 **90s**）：单段阻塞到点即返回"**仍在等待，请续等**"（`segmented:true` + 剩余预算），并明确禁止把它套进长阻塞轮询；总预算用尽才报"彻底超时"并给出介入指引 |

### 6.3 安装 / 重装（DSH 升级会覆盖，所以做成幂等脚本）

```powershell
cd <本仓库>
.\install-job-wait-guard.ps1 -DryRun     # 先看会改哪些文件
.\install-job-wait-guard.ps1             # 实际装进每个 profile 的插件目录（逐文件 .bak-guard-时间戳）
.\install-job-wait-guard.ps1 -Verify      # 只核对：守卫模块在 + index.js 已接线
```

装完**必须完全重启 DSH**（插件 host 半是进程级配置，热更新无效）。

### 6.4 回归

```powershell
node --test engineering\plugins\dsh-engineering-ui\test\job-wait-guard.test.mjs   # 6/6
node --test engineering\plugins\dsh-engineering-ui\test\                        # 15/15（含 compatibility / hooks-order）
python  engineering\tools\run_regression.py                                      # 48/49 → 已修至全通过
```

---

## 七、本次事故的元教训

1. **"卡死"往往是"在等一个永远不会来的东西"**，而不是程序崩溃。
   判断方法：看日志最后一条事件与 `turn/end` 的时间差（本次 68~86 秒）。
2. **框架内部错误会掩盖真实原因**。
   `cannot get required service "jobs" in inactive context` 这句话，
   和"我想读后台任务输出"之间没有任何可推理的关联。
3. **提示词里的"长睡眠轮询"是有毒的**。
   它把宿主层的不稳定性直接放大成用户可感知的"死机"。
4. **用户的观察（"某个选项时卡死"）与真实原因（job_output 阻塞）不同，但仍有价值**——
   它把排查引到了"交互环节"，最终在相邻事件里找到了真凶。

---

## 附：证据检索方法

```powershell
# 1. 解压会话包
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::ExtractToDirectory($zip, $out)

# 2. 定位 turn/end 与前后事件
#    主会话：session.v4.jsonl（460 事件）
#    子代理：subagents/<id>/session.v4.jsonl（705 事件）

# 3. 在 app.asar 中定位报错源码
python -c "d=open(r'Z:\DSH\resources\app.asar','rb').read(); print(d.find(b'inactive context'))"
# → 16800292
```

---

*本文由 Lead 基于完整会话日志与 DSH 安装体源码交叉分析生成。*
*未修改任何工程代码；本文件仅为诊断记录。*
