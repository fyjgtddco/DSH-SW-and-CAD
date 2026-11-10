# 2026-11-10 复检修复：P5 收口 + N1~N5（对应《回归复检报告_20261110.md》）

> 依据：`_repro_20261110\回归复检报告_20261110.md`（真实门禁状态机 + 真实 SW 建模复检）。
> 报告结论：核心执行层 bug 主干已真修好，但 **P3/P5 各留一条可绕过的支路**，
> 且新发现 3 个问题。本文档对应报告第 6 节「给作者的修复建议」逐条落地。

---

## 0. 一句话结论

**P5 的欺骗路径（不传 gear 期望 → 无齿"齿轮"全链路 PASS 下线）已用代码级强制堵死；
N1~N4 全部修复并各有回归用例钉住；N5 基线已重建。全量回归 62/62 通过。**

| 项 | 修复前 | 修复后 | 落点 |
|---|---|---|---|
| P5 | gear 断言 opt-in，小屋自写 expect 可绕过 | 齿轮名/传动房间零件 **缺 stages.gear 即 FAIL** | mode_gate._selfcheck_gate_for_room |
| N1 | save 失败把【已冻结好件】误移 parts_rejects | 只挪"本次新建的失败产物"，好件拒绝挪走并留痕 | task_workspace.reject_part |
| N2 | 变量拼路径 → expected 猜成 scripts\，成功建模误报 MISSING_ARTIFACT | 静态解析内联 os.path.join(变量,…) + 磁盘新增件按 basename 校正 | sw_bridge._extract_output_paths / _verify_run_artifacts |
| N3 | 窗口 room-start 开、room-end 关 → 排队等待算 overlap（假阳性） | 窗口只在【实际 SW 命令执行】开合，排队不计 | sw_bridge.main 取锁后开 / finally 关 |
| N4 | self-check 门禁读 mode_state.work_dir（恒 None），只认 cwd\reports | work_dir 与门禁读根同源（_default_work_dir → workflow_state） | mode_gate._selfcheck/_pose 两门 |
| N5 | defense_gate 相对 2026-11-01 基线被改（sha b8c9…≠8c49…） | 审阅 diff（均为加固）后重建基线，check 通过 | gate_integrity baseline |

---

## 1. P5 收口 —— 齿轮类零件必须带真齿形断言（缺段=FAIL）

### 现场（报告 §2 P5-门2/门3）
小屋给无齿圆柱写【圆柱自己的期望】、不带 gear 参数 → bbox/体积/特征 三段
全 PASS → room-end（无 --force）ok:true 正常下线。断言是 opt-in，欺骗路径原样保留。

### 修复
`mode_gate._selfcheck_gate_for_room` 的 PASS 分支内新增强制检查：

- **判据来自零件身份，不采信小屋自写的 expect**：
  - 房间 `type == "transmission"` → 该房间**全部**零件按齿轮类对待（防"把齿圈画成圆柱"的整房替换）；
  - 零件文件名含齿轮语义标记（齿轮/gear/pinion/齿圈/齿条/蜗轮/蜗杆/链轮/棘轮/齿廓…）。
- 齿轮类零件的 self_check.json **必须含 `stages.gear` 且 `ok == true`**；
  缺段或未过 → 拦（SELF_CHECK_REQUIRED，fail-closed）。
- 新增 `_gear_class_part(name, room_type)` 助手（单点判定，可复测）。

### 回归
`test_regression_room_gates_p5.py` 新增 F10~F14（26/26 通过）：
- F10/F10b/F10c：`_gear_class_part` 的三种判定；
- F11：齿轮名 + PASS 但缺 gear 段 → 拦；
- F12：齿轮名 + gear 段 ok → 放行；
- F13：齿轮名 + gear 段 FAIL → 拦；
- F14：transmission 房间非齿轮名零件缺 gear 段 → 拦。

> 后续（报告建议的更深一层）：期望值由总计子代理的 part_spec 单一真源下发，
> 小屋无权自定 —— 本轮先用"身份强制缺段=FAIL"把欺骗路径堵死，part_spec 属下一步。

---

## 2. N1 —— save 失败改道不得误伤已交付好件

### 现场（报告 §2 P3-a 副作用）
台账未登记的零件同名二次 save：旧件已被 room-end 冻结只读 + SW 占用，
SaveAs3 静默未写（被正确识别为失败）→ 失败处理把磁盘上的**好件**
（首版已 PASS 自检 + 看图 + 冻结）移进 parts_rejects/，新几何反而没落盘。

### 修复
`task_workspace.reject_part` 增加好件守卫 `_is_good_artifact(work_dir, src)`，
三条独立证据任一命中即拒绝挪走（只留痕 skipped_good 到 rejected.json）：
1. **冻结台账**：mode_state.json 任一房间 `frozen.files` 含本路径；
2. **三段自检 PASS**：work_dir/reports 或零件附近的 reports/ 有
   `<零件>.self_check.json` 且 verdict=PASS + ok=true；
3. **材料凭据**：`<零件>.SLDPRT.material.json` 存在。

swapi._reject_failed_part 与 sw_bridge._reject_failed_save 都委托本函数，
单点收口，两处调用点同时受保护。

### 回归
`test_regression_20261110_repro_fixes.py` 第 D 节（D1~D4，全绿）：
D1 PASS 好件 / D2 冻结件 / D3 凭据件 → skipped_good 且文件仍在；
D4 无凭据失败件 → 照常进 rejects。

---

## 3. N2 —— run 产物核对路径解析：不再猜错目录

### 现场（报告 §2 N2）
`m.save(os.path.join(E, "parts", "DSH_回归A方块.SLDPRT"))` 中 E 是变量：
旧 `_extract_output_paths` 把 `os.path.join` 整体当"变量名"去查赋值（查不到），
只剩字面量文件名拼到【脚本目录】→ expected=`scripts\DSH_xxx.SLDPRT`，
而实际件在 `parts\` 且 save ok:true → 4 次成功建模全部误报 MISSING_ARTIFACT
（"狼来了"，真失败会被假失败淹没）。

### 修复（两层）
1. **静态解析加强**（`_extract_output_paths` + 新 `_resolve_save_expr`）：
   - 正则收紧为纯标识符（`os.path.join` 不再误当变量）；
   - 新增 ②b 分支：`save(...)` 实参整段括号平衡抓取，交给
     `_resolve_save_expr` 递归解析（字面量 / 简单变量回溯 / 内联 join 多级回溯）。
2. **磁盘校正兜底**（`_verify_run_artifacts` + 新 `_sweep_run_window`）：
   - 无论 expected 是否为空，都扫运行窗口内磁盘新增/更新的 CAD 产物
     （脚本目录 + 3 级父目录 + 任务 work_dir + cwd 的 parts/output/drawings）；
   - expected 中【磁盘上缺失】的路径按 basename 与磁盘新增件配对，
     命中的用**真实落盘路径**替换（path_corrected 留痕）；仍找不到才判 missing。
   - cmd_run 记录 `_started_at` 精确圈定窗口（无记录放宽 1h）。
   - 真缺件仍会被拦（C4 回归：不因校正放水）。

### 回归
`test_regression_20261110_repro_fixes.py` 第 C 节（C1~C4，全绿）：
C1 内联变量路径解析到 parts\；C2 旧反例（二级变量回溯）仍绿；
C3 静态猜错 + 磁盘新增件校正（present 命中、missing 空、留痕）；
C4 真缺件仍 missing。

---

## 4. N3 —— build_overlap_count 只统计实际 SW 命令执行区间

### 现场（报告 §2 N3）
窗口在 room-start 开、room-end 关：B 房间 room-start 后一直排队（从未建模），
其窗口与 A 的整轮窗口相交 → build_overlap_count=1（重叠 432s），
而 SW 层零并发 —— 已修好的并发防护永远报违规（假阳性）。

### 修复
- `mode_gate.cmd_room_start` **不再**调用 `_record_build_start`；
- `sw_bridge.main` 取锁成功、且命令**真的要用 SW**（不在 `_NO_SW_CMDS`）时
  才 `_record_build_start(room)`，命令结束（finally）即 `_record_build_end(room)`；
- room-end 的 `_record_build_end` 保留作安全网（未开窗时为 no-op，不凭空造窗）。

### 回归
`test_regression_20261110_repro_fixes.py` 第 B 节（B1~B7，全绿）：
B1 room-start 不开窗（源码级）；B2/B3/B4 sw_bridge 开合钩子（源码级）；
B5 两段不相交窗口 → overlap=0；B6 重叠两段 → 1 对；
B7 未开窗时 _record_build_end 不造窗口。

---

## 5. N4 —— self-check / pose 门禁的 work_dir 与门禁读根同源

### 现场（报告 §3 N4）
`_selfcheck_gate_for_room` 只读 `mode_state.work_dir`（该键**不存在**，
work_dir 由 workflow_gate 写在 workflow_state.json）→ 只认 cwd\reports，
小屋按提示把自检记录落【任务目录】reports\ 后被误拦"没有三段自检记录"。

### 修复
- `_selfcheck_gate_for_room` 的 `_roots` 增加 `_default_work_dir()`
  （mode_state → workflow_state → settings.delivery_root() 逐级回退，与门禁其余读根同口径）；
- `_pose_gate_for_room` 的 up_contract.json 查找同口径补上 `_default_work_dir()`。

### 回归
`test_regression_20261110_repro_fixes.py` 第 A 节（A1~A3，全绿）：
mode_state 无 work_dir、workflow_state 有 work_dir → 缺记录拦截、
任务目录 reports\ 里的 PASS 记录放行、pose 门经 workflow_state 找到契约。

---

## 6. N5 —— gate_integrity 基线重建（审阅后）

### 现场（报告 §3 N5）
每次 room-end 都报 `defense_gate.py sha b8c9…≠8c49…`（基线 2026-11-01 20:11:38）。

### 审阅结论
defense_gate.py 相对基线（157,726 B）的两批改动均来自仓库已提交的历史：
- **ce606ef**（2026-11-04，+10KB）：A-17/A-74 材料凭据与 SW 内部实测
  （sw_internal.json）交叉核对、双向材料身份查询、密度物理事实硬判 —— 加固；
- **9be46e8**（2026-11-08，+3.7KB）：`log_bypass` 增加 scope、
  新增 `load_bypasses` / `bypass_summary` 审计函数 —— 加固。
无任何弱化门禁的改动。

### 处置
`python gate_integrity.py baseline --reason "2026-11-10 复检 N5：审阅 defense_gate diff（ce606ef/9be46e8 均为加固）后重建基线"`
→ 基线写至 `~/.dsh/.agent-presets/engineering/tools/.defense/gate_baseline.json`，
`python gate_integrity.py check` 现报「未被改动」。

---

## 7. 回归

- 新增 `test_regression_20261110_repro_fixes.py`（23 项：A N4 / B N3 / C N2 / D N1）。
- 更新 `test_regression_room_gates_p5.py`（F1~F4 改用非齿轮名零件；新增 F10~F14 齿轮强制）。
- 全量：`python run_regression.py` → **62 / 62 文件通过**（含本轮新增）。

## 8. 修改文件清单

| 文件 | 改动 |
|---|---|
| mode_gate.py | P5 收口（_gear_class_part + 强制 gear 段）；N4 两门 work_dir 同源；N3 room-start 不再开窗 |
| sw_bridge.py | N2（_resolve_save_expr / ②b 内联解析 / _sweep_run_window / _verify_run_artifacts 校正 / _started_at）；N3（main 取锁后开窗、finally 关窗） |
| task_workspace.py | N1（_is_good_artifact + reject_part 好件守卫，留痕 skipped_good） |
| test_regression_room_gates_p5.py | F1~F4 零件改名 + F10~F14 新增 |
| test_regression_20261110_repro_fixes.py | 新增（N1/N2/N3/N4 回归） |
| gate_baseline.json（已安装副本 .defense/） | N5 重建 |

## 9. 未做 / 建议下一步（如实记录）

1. P5 的 part_spec 单一真源下发期望（本轮只堵了"缺段"路径；小屋仍可给
   错误的 gear 期望，但那是把真齿轮说成别的齿轮——判据改为 part_spec 下发可根治）。
2. 齿轮断言 `gear()` 的 verify 结果并入 self_check.json 的"关键特征存在性"
   （room-end 已能读到 gear 段，但"特征数=z、da/df 实测"仍在 gear 段内）。
3. 装配朝向断言（J2/J4/J5）尚未嵌入总装房间 room-end（需 read_component_pose
   真实输出，属下一步）。
4. N2 的磁盘校正依赖 mtime 窗口，极端情况下可能把"运行窗口内被其它进程
   新建的同名文件"当成本次产物 —— 概率低、且 basename 必须与脚本声明一致，
   已用 path_corrected 留痕保证可追溯。
