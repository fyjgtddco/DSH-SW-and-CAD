# 2026-11-10 复检第二轮：R1~R9 收口 + 工程模式压缩器失效修复

> 依据：`20261110_162559_1DE66556/reports/BUG回归验证报告_20261106回溯报告.md`
> 与用户反馈「压缩插件好像没有生效，根本就没有压缩」。
> 上一轮（P5/N1~N5）之后，防线类修复已实证守住；本轮收口**流程层残留**。

---

## 0. 一句话结论

**去重闸门的数据源（R1/R2）、材料试错写入（R3）、齿轮建模默认超时（R4）
全部落地并各有回归；R5~R8 一并修正；压缩器 14 次失败的真因（单请求超
100K prompt 上限）用【分段摘要】修好。**

| 项 | 修复前 | 修复后 | 落点 |
|---|---|---|---|
| R1 | 自动登记 `part` ≠ `name` 必败 | `name` 与 `part` 双写 | sw_bridge._register_parts_after_run |
| R2 | 默认台账指向上一任务，TASK_ID_MISMATCH 拒写 | 无 env/work_dir 时自动解析本任务 work_dir | part_registry.registry_path |
| R3 | 候选"写后再验"把零件留在被拒牌号（1.2312） | 先验后写 + 被拒回滚 + 收尾兜底 | swapi.set_material |
| R4 | 600s 默认超时卡死真齿形；超时无断点信息 | 齿轮类默认 1800s；超时返回 build_progress 末段 | sw_bridge.cmd_run |
| R5 | 完工房间仍占 SW 队首（挡下一个房间） | report done/delivered/failed 即释放/移出队列 | mode_gate.cmd_room_report |
| R6 | 并发台账含上一任务窗口（假阳性） | 窗口/违规带 task_id，按本任务过滤 | mode_gate._build_overlaps |
| R7 | 静态回溯猜错目录（HERE→intermediate\parts） | 运行窗口磁盘新增件按 basename 校正 | sw_bridge._verify_run_artifacts |
| R8 | ended_at 粘滞：收尾后又有上报仍显示 done | 显式 `ended_at_stale` + detail 标注 | mode_gate._board_entry |
| R9 | PowerShell 管道落 JSON 变 UTF-16 | 工具读写全程 utf-8/utf-8-sig；证据用 --out/Python 直写 | （口径约定，见 §7） |

---

## 1. R1 / R2 —— P3 去重闸门的"数据源"终于接对

### 现场（复检报告 §2 P3）
`part_registry_auto: {"registered": [], "failed": [{"part":"小齿轮轴",
"error":"缺少必填字段：name"}]}` —— `run` 成功自动喂台账**一次都没写进去**，
查重闸默认永远查不到，**verdict 恒 new**（改了判据没改数据源）。

### 修复
1. `sw_bridge._register_parts_after_run`：`_rec` 增加 `"name": _name`
   （保留 `part` 兼容旧消费方），并显式传 `work_dir=_task_work_dir()`。
2. `part_registry.registry_path(None)`：无 `DSH_PART_REGISTRY` / 无显式
   work_dir 时，从 `workflow_state.json` 取本任务 work_dir，台账落到
   `<work_dir>/load_cases/part_registry.json`（与显式 work_dir 同口径）；
   取不到才回退旧默认。新增 `_current_work_dir()`（与 `_current_task_id` 同源）。

### 回归
`test_regression_20261110_repro_fixes.py` E1~E5：`registry_path()` 指向本任务、
自动登记 `failed` 为空、registered 非空、台账带 task_id、台账里有该零件。

---

## 2. R3 —— 材料：先验后写 + 被拒回滚

### 现场（probe_material_readback.json）
请求 `40Cr`（GB/T 3077），SW 库无此牌号；候选链里的 DIN
`1.2083/1.2311/1.2312` 被**逐个真实写入**、读回不符后再 `continue`。
SW 不会自动恢复 → 零件最终停在**最后一个被拒候选 1.2312** 上：
防线正确 BLOCK、函数返回 ok=false，但 `.sldprt` 属性栏是一个
"精确得像真的"错误牌号，拿去做加工/打印会误导。

### 修复（swapi.set_material）
1. **先验后写**：`_material_names_match(real, name)` 不成立，或
   `_detect_cross_standard(name, real)` 命中且未显式放行 →
   **根本不下笔**（记入 `prefilter_rejected` / `cross_standard_rejected`）。
2. **被拒回滚**：写入后被读回/密度/跨标准判拒 → 立即
   `_restore_before_material()` 回到写入前材质（`material_restore_log` 留痕）。
3. **收尾兜底**：Bug15 自定义降级路径若仍把模型写脏，最终再回滚一次；
   返回体带 `restored_material` / `material_left_unchanged`。

### 回归
`test_regression_com_material.py` 新增 J 节（98/98 全绿）：
J1 请求 40Cr → **不写入任何 DIN 近似候选**，零件保持原材质；
J2 SW 静默替换 → 读回拒后**回滚到写入前材质**（不是 AISI 1020）。

> 根治路线（建议下一步）：把 GB 牌号写进 SW 自定义材质库（`.sldmat`），
> 走 `SetMaterialPropertyName2("", "DSH_GB", "40Cr")` 精确命中。
> 复检任务里测试员已手工建过 `DSH_GB.sldmat`（含 Q235/HT200），
> 但插件还没有"自动生成/注册 GB 材质库"的工具。

---

## 3. R4 —— 真齿形建模的默认超时 + 断点取证

### 现场（tooling_timeout_observation.md）
z=24 真渐开线斜齿：9 段 × 288 点 polyline，每段 76~117s，单件 ≈15~20 分钟。
默认 600s 必然 `RUN_TIMEOUT`（只够 4~5 段），只能手工设 1800；且超时
返回**不带"执行到第几步"**，取证成本高。

### 修复（sw_bridge.cmd_run）
1. 未显式设 `DSH_SW_RUN_TIMEOUT_SEC` 时，脚本判定为齿轮类
   （`_is_gear_like_script`：文件名/内容含齿轮语义且确有 polyline/
   extrude/revolve）→ 默认 **1800s**；普通脚本仍 600s。
2. 超时返回增加 `timeout_sec` / `timeout_source` /
   `progress`（读 `build_progress.json` 等断点日志的最后 6 条），
   hint 里直接给出断点，便于从断点续建。

---

## 4. R5 —— 完工房间不再占 SW 队首

现场：箱体房间 report done 后仍占队首 15.7 分钟，挡住队 2。
修复：`cmd_room_report` 在 stage ∈ {done, delivered, part_done, failed} 时
调用 `_release_sw_after_report`：owner 是自己 → `sw-release`；
只在队列里 → 从队列移除。返回体带 `sw_lock_released_on_report`。
（room-end 的释放逻辑保持不变，双保险。）

## 5. R6 —— 并发台账按任务过滤

现场：新任务 room-board 开局 `build_overlap_count=1`（432s 重叠），
实为上一任务"回归室A/B"的历史窗口。
修复：构建窗口与违规记录写 `task_id`；`_build_overlaps()` 在
未显式传 windows 时只统计**本任务**窗口；room-board 的违规计数同样过滤。
无 workflow_state（测试/手工）时不臆造过滤。

## 6. R7 —— 猜错目录的产物路径按磁盘实际落盘校正

现场：`HERE = os.path.dirname(...)` 变量回溯解析不出盘符时，
相对名被拼到脚本目录（`intermediate\parts\...`）。
修复：N2 的 `_sweep_run_window` + basename 校正覆盖该场景；
本轮 F1/F2 回归钉住（present 命中真实 `<任务>\parts\`，missing 为空）。

## 7. R8 —— ended_at 粘滞显式化

现场：房间已收尾（ended_at），之后又上报 `validating`，看板 state 仍 done，
state 与 report_stage 不一致。
修复：不篡改终态；`_board_entry` 增加 `ended_at_stale` 字段，
detail 里写明"已收尾但之后又上报 stage=..."。

## 8. R9 —— JSON 编码口径

排查结论：工具代码写状态/报告一律 utf-8（读用 utf-8-sig 容 BOM），
**没有** utf-16 写入；复检里的 `ff fe` 来自 PowerShell 管道
（`Tee-Object`/`>` 默认 UTF-16）。
口径约定：证据落盘用 `--out` 或 Python `json.dump(..., encoding="utf-8")`；
不要用 PS 重定向保存 JSON。

---

## 9. 压缩器失效（用户反馈：根本没有压缩）

### 真因（会话日志 57278639 实证）
14 次 `compaction/start` 全部以 `compaction/end error` 收场：
```
buddy: 内容过长，请精简或新建任务
prompt is too long: 100001 tokens > 100000 maximum
```
摘要请求把**整段历史（≈100K tokens）**一次性发给模型，超过网关 100K
prompt 上限 → 压缩永远失败 → 会话涨到"内容过长"。
（压缩阈值按 contextWindow 200K 推得 ~128K，而真实网关上限 100K，
所以每当要压缩时摘要请求必然超限。）

### 修复（dsh-engineering-compaction/lib/summarize.js）
`summarizeWithLlm` 增加**分段摘要**：
1. 按字符预算（默认 80K chars，可用 `config.chunkChars` /
   `DSH_ENG_COMPACTION_CHUNK_CHARS` 覆盖）切分消息（保持顺序；
   超长单条按行边界拆开）；
2. 每段单独摘要（段指令强调"只归纳本段、数字/路径逐字保留"）；
3. 段摘要再合并为最终 L0–L3 checkpoint；合并请求仍超限则递归二分；
4. 单次请求仍被网关判超长 → **自动二分重试**（≤3 层）；
5. 未超限时保持**单请求**旧行为（保住 KV 前缀缓存）。

### 回归
新增 `test/summarize-chunking.test.mjs`（3/3）：
① 超长输入按预算分段；② 单次超限自动二分重试；③ 未超限仍只发一次。
另修 `compaction-wiring.test.mjs` 的仓库路径假设（装在 profile 下时
skip 而不是误报失败）。

---

## 10. 修改文件清单

| 文件 | 改动 |
|---|---|
| tools/swapi.py | R3：候选先验后写、被拒回滚、收尾兜底 |
| tools/sw_bridge.py | R1/R2 自动登记、R4 齿轮超时+断点证据、R7 校正复用 |
| tools/part_registry.py | R2 默认台账解析到本任务 work_dir |
| tools/mode_gate.py | R5 report 终态释放 SW、R6 按 task_id 过滤、R8 ended_at_stale |
| tools/test_regression_20261110_repro_fixes.py | 新增 E/F/G/H/I（R1/R2/R7/R6/R5/R8） |
| tools/test_regression_com_material.py | 新增 J（R3） |
| plugins/dsh-engineering-compaction/lib/summarize.js | 分段摘要 + 二分重试 |
| plugins/dsh-engineering-compaction/test/* | 新增 chunking 测试；wiring 测试布局兼容 |
| docs/fixes-20261110-repro-r2.md | 本文 |

## 11. 未做 / 建议下一步

1. **自动生成 GB 材质库**（`.sldmat`）并注册到 SW，让
   `SetMaterialPropertyName2("", "DSH_GB", "40Cr")` 精确命中 ——
   这是 R3 的根治路线（本轮只保证"不写脏"）。
2. 分段摘要的"段数×延迟"可再优化（并行分段？），本轮先保正确性。
3. 压缩阈值与网关真实上限的口径：若网关上限低于声明窗口，
   建议在 preset 里调大 `headroomTokens`（128K 阈值 → 88K），
   让压缩在"普通请求"超限前发生；分段摘要已保证压缩本身不再失败。
