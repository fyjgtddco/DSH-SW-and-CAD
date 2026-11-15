# 2026-11-13 COM 门卫版合入记录（2026-11-15 执行）

来源：`C:\Users\j1877\Desktop\DSH-SW-Monitor`（交付包）
目标：`DSH-SW-and-CAD-main` + DSH 安装副本
基线：`7f6594f5474b50fdfcc67e7980c3a3c4f027704b`（与本仓库 HEAD 一致）

## 合入方式

按《DeepSeek接入教程》0 节 A：

```powershell
git apply --check $patch   # 干净通过，无冲突
git apply $patch
# 再逐文件复制 engineering/ 下的 8 个新增文件
```

## 补丁规模（先核对再合，避免大删改）

| 文件 | 新增 | 删除 |
|---|---|---|
| plugins/dsh-engineering-compaction/lib/index.js | 47 | 0 |
| plugins/dsh-engineering-ui/lib/index.js | 17 | 0 |
| plugins/dsh-engineering-ui/lib/sw-monitor.js | 14 | 4 |
| plugins/dsh-engineering-ui/preset-engineering.patch.yml | 4 | 0 |
| tools/mode2_flow.py | 3 | 1 |
| tools/mode_gate.py | 20 | 6 |
| tools/workflow_gate.py | 21 | 2 |
| **合计** | **126** | **13** |

**结论：纯增量，无大面积删除。** 13 行删除逐条核对如下，全部是签名/参数扩展：

- `'status','attach','recover'` → 追加 `'page-status','audit'`
- `'reopen-room','room-failed'` → 追加 `'audit'`
- `async attach({...})` → 增加 `dirty_pages, observed_rev` 形参
- `if (!this.attached)` → 允许 `page-status` 在未 attach 时读取
- `r = stage_status(wd)` → 改为 `fidelity_view(stage_status(wd), ...)`
- `cmd_sw_monitor_attach(...)` / `cmd_sw_monitor_recover(...)` → 增加 `dirty_rev`
- `"sw-monitor": (...)` → 增加 `--dirty-rev`
- `cmd_gate_summary()` / `_m2c.stage_status()` → 包一层 `fidelity_view`

## 新增文件（8 个 + 内核 6 个）

工程侧（接入包）：`memory-contract.js`、`memory-contract.test.mjs`、`fault_codes.json`、
`memory_gate.py`、`pages_manifest.json`、`page_contract.py`、
`test_regression_clawvm_manifest.py`、`trace_replay.py`

监控器内核（vendor 进 `tools/sw_monitor/`）：`monitor.py`(51.9KB)、`com_gate.py`(12.6KB)、
`selfcheck-com.py`、`fault_codes.json`、`README.md`、`docs/错误码.md`，以及新版
`DeepSeek接入教程.md`、`覆盖验收.md`

## 合入后修的两个真实缺陷

### ① 内嵌布局下 `load_contract` 路径拼错（9 个自检全 ERROR）

`monitor.py` 的兜底路径写死独立交付包布局：
`<包>/工程模式接入包/engineering/tools/`。但仓库/安装副本是内嵌布局
（`monitor.py` 在 `tools/sw_monitor/`，`page_contract.py` 在 `tools/`），
于是拼成 `tools/sw_monitor/工程模式接入包/...` → `FileNotFoundError`。

修复：新增 `_contract_dir()`，按 `tools → 上一级 → 自身 → 独立包` 依次探测。

### ② 业务拒绝码被吞成 `MONITOR_INTERNAL_ERROR`（可观测性）

`Fault` 在 `_attach` 的 Condition 上下文里抛出时，穿到 handler 的
`except Exception` 分支，一律变成 `500 MONITOR_INTERNAL_ERROR`。
真实原因（`UNMANAGED_SW_RUNNING` / `COM_WORKER_STILL_RUNNING`）全丢，
排查时只能看到"内部错误"。

修复：用 `fault_code()` 从异常文本里提取业务码，命中就返回 `409` + 真实码。

## 接口变更导致的测试适配

`monitor.py` 现在**强制** `--solidworks-exe`，且做两道 fail-closed 校验：
① 路径必须真实存在；② 文件名必须正好是 `sldworks.exe`。
`test_regression_sw_monitor_e2e.py` 已相应调整（造一个同名占位文件）。

同时新增 SW 运行预检：本机有 `SLDWORKS.exe` 时**显式跳过**并说明原因
（门卫按设计拒绝 `UNMANAGED_SW_RUNNING`，那是安全行为，不是缺陷）。

## 验收

| 项目 | 结果 |
|---|---|
| `sw_monitor/selfcheck.py` | **9 / 9**（仓库 + 安装副本均通过）|
| `sw_monitor/selfcheck-com.py` | **3 / 3** |
| 新增 `test_regression_clawvm_manifest.py` | **4 / 4** |
| 新增 `memory-contract.test.mjs` | **2 / 2** |
| Python 全量回归 | **73 / 74**（唯一"失败"是 e2e 在 SW 运行时的跳过，实际 exit 0）|
| `verify_sync.py` | 内容不同 0 / 仅仓库有 0 → 完全同步 |

## 仍需人工在目标部署验收的

1. **真实 SolidWorks 保存/关档**：本机自检用替身，未做真实 COM 建模。
2. **COM 门卫切换**：旧 `sw-monitor-permit/done` 建模入口还没换成
   `com-build/com-status`（教程 0 节 D）——这一步**未做**，需要先把宿主侧
   建模工具注册好，再封住任意脚本/COM 入口。
3. **装配 / 工程图 / 几何读回 / 真实 Simulation**：本版白名单不含，需保持阻塞。
4. **覆盖率与 `compaction.committed` 证据**：需要 DSH 在已核实的宿主提交点写入。
