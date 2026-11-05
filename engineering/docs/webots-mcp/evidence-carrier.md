# S04：报告与证据承载 v1

本模块只读写三类报告和日志；不启动 SW/Webots/DSH，不转换物理量、推断关节或根因，也不恢复 CAD 身份。人工夹具保持 `synthetic`，未捕获版本/数值/持久引用保留 `null` 和 `missing_information`；任何默认报告都没有物理 PASS。

## 接口与目录

调用方先核定 `_store.state_dir()` 的实际路径，再传入已存在的绝对目录和真实请求上下文。构造器不创建目录；本模块核对归属，真实上下文的认证由宿主/S07负责。

```python
from webots_mcp.evidence import EvidenceStore

store = EvidenceStore(verified_state_dir, task_id=task_id, room_id=room_id)
report = store.new_report("model_manifest", run_id, provenance="unknown")
saved = store.write_report(report)
loaded = store.read_report(run_id, "model_manifest")
store.append_log(run_id, "尚未取得真实仿真结果\n")
page = store.read_log(run_id, limit=16384)
next_page = store.read_log(run_id, offset=page["next_offset"],
                           expected_sha256=page["sha256"])
```

| 公共调用 | 结果 / 约束 |
|---|---|
| `EvidenceStore(state_dir, *, task_id, room_id)` | 无写入；状态根必须已存在且为绝对路径，task/room 是非空字符串 |
| `new_report(report_type, run_id, *, provenance, source_revision=None, status="unknown", data=None, evidence=None, missing_information=None)` | 仅内存对象；固定三类报告，原始数据深复制，不解释质量数组/坐标/身份 |
| `write_report(report)` | 创建运行目录和归属；返回 `{path, sha256, size_bytes}`，path 相对状态根 |
| `read_report(run_id, report_type)` | 返回完整报告，并验证头、归属、证据的现存文件与 hash/size |
| `file_evidence(run_id, relative_path, *, kind, provenance, producer=None)` | 只读已有 run 内文件，返回 `{path, sha256, size_bytes, kind, provenance, producer}`；path 相对 run，producer 可记录 name/version/source_revision，未知默认 null |
| `append_log(run_id, text, *, name="worker", max_bytes=1048576)` | 保留有界 UTF-8 尾部，返回 `{path, size_bytes, dropped_bytes}`；先写一份报告确立 run |
| `read_log(run_id, *, name="worker", offset=0, limit=16384, expected_sha256=None)` | 返回 `{path, text, offset, next_offset, size_bytes, sha256, eof}`；offset 是 UTF-8 字符边界的字节位置 |

`webots_runs/<run_id>/` 固定承载 `model_manifest.json`、`conversion_report.json`、`experiment_report.json`。日志位于 `logs/<name>.log`，截图/采样/源快照通常置 `evidence/`。内部 `.owner/` 目录只用于原生独占认领，`.owner.json` 记录 task/room；写入失败保留不完整现场并拒绝自动接管。不能手动删除它来规避归属核验。

公共头为 `schema_version=1`、report_type、run_id、task_id、room_id、source_revision、created_at、provenance、status、evidence、missing_information、data。provenance 可传字符串或 `{kind: ...}` 及原有附属证据字段；kind 允许 synthetic/fixture/fake/static-derived/real-sw-export/real-webots/unknown。status 为 supported/approximate/unsupported/unknown，表示该条记录的支持状态，不是实验通过结论。

`data` 默认承载：manifest 的 source/components/raw_mass_properties/rigid_bodies/joints；conversion 的 inputs/outputs/tools/mappings/approximations/omissions/support/validations/physical_validation；experiment 的 model/scene/parameters/actions/observations/issues/verdict。业务数据保留原样；问题可记录 observed/derived/suspected 等级、多候选零件、参数单位、时步、证据与缺口，承载器不把候选变成根因。完整物理合法性、可信来源判定和业务门槛留 Astra。

## 写入、分页与失败

JSON 先按 UTF-8 严格序列化，拒绝非有限数值；报告读写上限 1 MiB。报告/日志以同目录唯一临时文件、flush/fsync、关闭句柄、replace 写入，异常向调用方传播，不能返回“已保存”。这是原子可见性和本机错误注入检查，不是断电耐久性的认证；Windows 打开文件被占用时可能拒绝替换，原错误必须保留。

单份日志上限 1 MiB，max_bytes 可在 4～1048576 范围内指定；单页 limit 为 4～65536 字节。分页保留完整 UTF-8 字符；下一页传回前页的 sha256，文件新增/裁剪后会拒绝旧快照，调用方应从 offset=0 重新读取。dropped_bytes 表示本次丢弃量；需长期保留的失败日志先另存为 `evidence/` 下不可变快照，再索引 hash。

run/name 为最多 128 字符的受限 ASCII 单段标识符；拒绝遍历、绝对路径、Windows 设备名/ADS、静态 symlink/junction/reparse 逃逸。run 归属通过原生 mkdir 独占认领，后续同 run 的报告/日志更新要求单写者；未实现跨进程读改写队列、恶意本地进程的并发文件系统替换防护或 A04 动作/实例锁。

| 异常 | 调用方处理 |
|---|---|
| PermissionError | task/room 不符或操作系统拒绝；不改用另一个路径绕过 |
| FileNotFoundError | run/report/log/证据不存在；不能显示已有结果 |
| ValueError | 路径/JSON/字段非法、证据 hash 变化、不完整认领或过期页；保留现场，明确失败原因 |
| 其他 OSError | 写盘/读取失败原样传播；不派发依赖“已保存”的后续动作 |

## 可复现检查与来源

```text
python -B -m unittest discover -s engineering/tests/webots_mcp -p "test_*.py" -v
```

S04 9 项检查加 S02 5 项夹具检查：22 个原始来源记录和具体方法数组不改写，三类报告往返，跨 task/room 与并发首次认领拒绝，不完整认领、非有限 JSON、写盘失败保留上一报告，缺失/漂移证据，中文路径/日志/UTF-8分页，junction 与路径边界，多候选零件不推断根因。S03 真 SDK 接口检查由 S03 另记，当前没有 SW/Webots 物理或 DSH 全链验证。

2026-10-30 已访问并归档官方 [os](https://docs.python.org/3.13/library/os.html)、[tempfile](https://docs.python.org/3.13/library/tempfile.html)、[JSON](https://docs.python.org/3.13/library/json.html)、[hashlib](https://docs.python.org/3.13/library/hashlib.html)、[codecs](https://docs.python.org/3.13/library/codecs.html)、[pathlib](https://docs.python.org/3.13/library/pathlib.html) 和 [CPython v3.13.15 tempfile 源码](https://raw.githubusercontent.com/python/cpython/v3.13.15/Lib/tempfile.py)。3.13 文档分支当前显示 3.13.18，与本机运行解释器 3.13.15 分记；访问日期、URL、标签、内容 hash 和用途见控制目录 `evidence/S04/official-sources.json`。

控制目录 `evidence/S04/` 保存 RED/GREEN 日志、`generate_samples.py` 和实际三报告样例 `sample-state/webots_runs/synthetic-example/`。样例明确不含真实 SW 导出、Webots 观测或工程通过结论；原 PLAN 未修改。
