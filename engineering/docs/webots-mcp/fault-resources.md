# S05：故障夹具、资源采样与断言

本阶段可运行的是实际 Windows Python 进程夹具、只读 Job 资源采样和供 A04 使用的失败断言。`real-process-fixture` 表示真实操作系统进程，**不表示真实 Webots worker、物理 PASS 或生产恢复已实现**。没有改写 server、fake worker、证据承载器、原安装或原源码，也没有再次启动 Webots、SW 或 DSH。

## 资源接口

```python
from webots_mcp.fault_resources import sample_job, budget_check

first = sample_job(owned_job_handle)
# 调用方等待下一采样时刻；采样器不阻塞等待，不改变进程状态。
second = sample_job(owned_job_handle, previous=first)
check = budget_check(second, calibrated_numeric_limits)
```

`sample_job(job, *, previous=None)` 仅查询调用方提供的已打开 Windows Job handle。不会发现/接管任意实例、创建或终止进程、设置 Job 限额或控制调度。拒绝 null handle，避免 Windows API 隐式查询宿主 Job；无效/关闭的 handle 的系统错误直接传播。所有权建立和重启后 PID/创建时间/路径核验仍由 A04 实现，不能把此 API 的参数当作授权证明。

| 输出 | 口径 |
|---|---|
| `schema_version=1`, `provenance=host-measured`, `scope=windows-job` | 仅宿主资源观测，不是仿真来源或工程判定 |
| `snapshot_at`, `sample_monotonic_s`, `started_monotonic_s`, `wall_time_s` | UTC快照时间、进程内单调时钟；首样本开始监测窗口，wall从0算起，不追溯整个进程生命周期 |
| `cpu_time_s` | Job累计user+kernel的100ns计数换算；包含已退出成员，不能只加当前PID CPU |
| `cpu_percent_one_core` | 两样本CPU秒数差÷单调墙钟秒数差×100；首样本null，按单核尺度，可超过100；不是整机归一化占用 |
| `process_count`, `processes[]` | Job实际active集合；每个条目保留PID、句柄查询到的创建时间、可执行路径和两类内存 |
| `working_set_bytes` | 当前成员工作集之和；共享页可能重复计数，不是唯一物理RAM量 |
| `private_bytes` | 当前成员私有提交字节之和；本机pywin32 312返回`PROCESS_MEMORY_COUNTERS.PagefileUsage`，不是磁盘交换文件实际大小或物理驻留量 |
| `complete`, `missing_information` | 无法打开、成员变动或归属复核失败时，内存汇总null并保留原因，不能用部分总和冒充完整树 |
| `gpu_percent=null` | 未测GPU；日志/截图/录像字节需调用方另行采集，未提供时预算检查视为unknown |
| `job_handle` | 进程内诊断关联，后续样本必须使用同一个持续打开的handle；不可作为跨重启或持久归属身份 |

采样使用已有 `pywin32==312`，惰性导入，未新增/安装依赖；预算和结果断言仅用标准库。只在Windows 11 / Python 3.13.15实测，其他系统或旧Windows未验收。内存、成员列表和CPU不是原子快照；开始/结束PID集合与active数不一致时拒绝完整内存汇总，极短暂且未被两次枚举看到的成员/峰值仍可能漏采。需要峰值/高频控制时由 A04 增加持续采样或原生限额，不能由这些离散样本推定峰值安全。

`budget_check(sample, limits)` 接收非空、显式、已校准的数值上限；不提供“低占用”默认阈值。当前指标为 wall_time_s、cpu_time_s、cpu_percent_one_core、working_set_bytes、private_bytes、process_count、gpu_percent、log_bytes、screenshot_bytes、video_bytes。拒绝未知指标和负数/布尔/非有限上限；缺失、null或非法测量归unknown。返回：

- 任一已测指标超上限：`within_budget=false`，并列 `exceeded` 和 `unknown`。
- 无超限但存在未测指标：`within_budget=null`。
- 仅全部指定指标已测且未超限：`within_budget=true`；这仅是这些资源上限的比较结果。

S06配方中的待校准null上限不能直接传入；调用方须先提供确切上限，并另外保留仍缺失的预算项，不得把它们悄悄计作已通过。

## 实际故障夹具

运行环境需已安装pywin32；完整子系统检查还需S03已固定的MCP/jsonschema依赖。

```text
python -B engineering/tests/webots_mcp/test_fault_resources.py
python -B engineering/tests/webots_mcp/test_fault_resources.py --record <绝对证据JSON路径>
python -B -m unittest discover -s engineering/tests/webots_mcp -p "test_*.py" -v
```

夹具仅建立随机名专用Job，以挂起状态创建自己的Python、加入Job后再恢复；关闭时只终止专用Job并确认active=0、两个排空线程均退出。同名外部Python对照进程由自己的Popen句柄单独收回，不按进程名扫描或终止。测试失败路径也关闭KILL_ON_JOB_CLOSE句柄。生产代码没有这些终止动作。

| 反例 | 实际断言 / 交 A04 的要求 |
|---|---|
| 单进程CPU/内存负载 | 实际Job可含venv启动器、conhost和Python内核；记录完整成员，CPU两次采样有增量，私有提交/工作集包含8MiB分配；不假定一个PID |
| 启动器exit0、子进程仍活着 | Job active仍大于0，采样不含已退出启动器；只清理专用Job后外部同名实例仍存活；不能把启动器exit0当清理成功 |
| 阻塞动作、50ms测试截止时间、取消ack | 动作副作用已落到临时文件，取消ack后进程仍活着，副作用仍一次；ack不能证明撤销，生产非阻塞取消/缓存快照仍待A04 |
| 派发后stdin断管 | 实际EOF已被进程观察，但临时动作记录仍存在、进程继续活着；断连不能支持盲重发 |
| 非零退出与双管道洪流 | stdout/stderr各排空超过200KB，分别只保留4KiB，exit37可观察；日志成功排空或进程退出都不是物理通过 |

这些临时文件是测试的副作用哨兵，**不是动作持久账本**。测试夹具不实现自动重试、恢复、握手、串行动作队列或真worker取消。50ms是反例夹具预算，不是产品默认超时，也没有将stdin EOF等同于完整MCP/DSH断连验收。已有官方SDK stdio回归继续独立运行。

`assert_unknown_action(result, *, operation_id, session_generation, completed_steps, sim_time)` 是供未来A04复用的结果oracle：对“已派发但最终结果丢失”要求 `ok=false / OUTCOME_UNKNOWN / execution_state=unknown`，准确保留操作身份、世代与调用方给出的最后确认进度。缺字段、布尔伪整数和虚构completed均失败。没有进度时必须显式传null，不能以0替代。该函数只断言调用方结果，不生成结果、不标READY、不触发重发。

## 已记录证据与边界

控制目录 `evidence/S05/` 保存首轮7项预期RED、非法结果与scope负例RED、GREEN、实际故障/资源JSON、完整安全子系统回归及官方抓取。历史DSH/SW实机回归可能操作用户状态，不在这个离线子系统suite中执行；不得把本检查数外推至整个宿主。

S03真实探针直接引用既有 `evidence/S03/probe-corrected/` 文件及hash：8×32ms、仿真时间0→0.256s、正常退出请求和Job active0，环境仍缺TexturedBackground/RectangleArena PROTO。该探针没有CPU/内存/GPU序列；本次不补算占用或加速比，也不把Python夹具数据写成Webots性能。真实worker恢复、全场景物理有效性、DSH端到端、真实SW/硬件均未验证。

官方核对日期2026-10-30，URL/版本/实际抓取字节SHA-256在控制证据 `official-sources.json`，内容在 `official/`：

- [Windows Job对象](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)、[Job累计计数](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_basic_accounting_information)、[Job查询](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-queryinformationjobobject)：成员、已退出进程CPU和本机归属夹具依据。
- [进程成员核验](https://learn.microsoft.com/en-us/windows/win32/api/jobapi/nf-jobapi-isprocessinjob)、[进程创建时间](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-getprocesstimes)：句柄观察；误用jobapi2路径曾404，改为实际jobapi页，不从失败页作结论。
- [PROCESS_MEMORY_COUNTERS](https://learn.microsoft.com/en-us/windows/win32/api/psapi/ns-psapi-process_memory_counters)、[扩展计数](https://learn.microsoft.com/en-us/windows/win32/api/psapi/ns-psapi-process_memory_counters_ex)、[pywin32 b312实际包装源码](https://raw.githubusercontent.com/mhammond/pywin32/b312/win32/src/win32process.i)：内存单位和本机字典字段依据；不凭EX文档假定包装器返回PrivateUsage。
- [MCP 2025-11-25 cancellation](https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/cancellation)：取消竞争窗口；联网文档本身不算本机MCP取消或物理撤销实测。
