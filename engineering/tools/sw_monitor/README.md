# sw_monitor — 外部监控器（工程模式内置副本）

这是独立服务 **DSH-SW-Monitor** 的随代码分发副本。工程模式用它作为
**SolidWorks 使用权的唯一权威**，替代原先的进程内锁。

| 文件 | 用途 |
|---|---|
| `monitor.py` | HTTP 服务：FIFO 票据、命令许可、逐零件状态机、验收协调、SQLite 持久化 |
| `dsh_evidence.py` | 复用 DSH 三大防线与台账审核，读取 BOM，仿真验证接口 |
| `host-adapter.mjs` | DSH 宿主侧适配器（被 `lib/sw-monitor.js` 动态加载） |
| `selfcheck.py` / `selfcheck-host.mjs` | 脱离 DSH 的可重复自检（5/5） |
| `docs/` | 接入教程 / 覆盖验收 / 原项目 README |

## 为什么工程模式里已经没有"SW 锁"了

旧实现（`mode_gate` 的 `sw_lock`）有两个结构性问题，都留过现场事故：

1. **「SW 进程没在跑」＝「锁空闲」**
   房间刚取到锁、SLIDWORKS 还没冷启动时，第二个房间同样能拿到使用权。
   2026-11-11 验收复跑实测 **8 对构建区间重叠**，最长 216 秒；两个脚本
   同时驱动同一个 SW 实例写不同零件，后写者覆盖先写者。
2. **「等锁」必须占着回合**
   忙时只能返回 `sw_busy` 让子代理 sleep 后重试，于是主对话挂在
   `wait-rooms --timeout 900` 上一动不动 —— 也就是图一那屏。

监控器把这两件事拆开了：

| 关注点 | 现在由谁回答 |
|---|---|
| 谁有权用 SW | 票据状态机（按 `epoch + task + room + session` 绑定） |
| 排队要等多久 | DSH 后台 job（模型拿到 `job_id`，不占回合） |
| 房间还活着吗 | 交付层的 `room-board`（心跳/平台/产物证据），与"谁能用 SW"无关 |
| 零件建好了吗 | `part-saved` 事件（带文件指纹与 generation） |
| 能不能交付 | `review-part` / `verify-task`（材料/物理/领域 + 台账 + 邻接） |

## 启动

```powershell
python -B .\monitor.py --data .\运行数据 --workspace-root 'D:\CAD交付' ^
    --dsh-tools "<工程模式 tools 目录>"
```

首次启动生成 `运行数据\host.token` 与 SQLite。默认监听 `127.0.0.1:18779`。
需要真实的 DSH 证据核实时再加 `--dsh-tools`。

## 接入（工程模式侧）

在 `<tools>\sw_monitor.json` 写入：

```json
{"enabled": true, "url": "http://127.0.0.1:18779", "tokenFile": "<运行数据>\host.token"}
```

然后：

```powershell
python "<根>\tools\mode_gate.py" sw-monitor attach --drained   # 首次接管
python "<根>\tools\mode_gate.py" sw-monitor status             # 只读查看
```

**没有接入会怎样**：所有建模命令返回 `MONITOR_NOT_CONFIGURED` 并拒绝执行。
这是有意的 fail-closed —— 旧锁已删除，不存在"退回进程内队列"这条路。

## 自检

```powershell
$env:PYTHONUTF8 = '1'
python -B .\selfcheck.py           # 监控器自身 5/5
python -B ..\test_regression_sw_monitor_e2e.py   # 端到端接入 13/13
```

## 契约文档

- `docs/DeepSeek接入教程.md` —— 接入顺序、每条路由、示例
- `docs/覆盖验收.md` —— 去重场景矩阵 + 99% 真实任务判据
