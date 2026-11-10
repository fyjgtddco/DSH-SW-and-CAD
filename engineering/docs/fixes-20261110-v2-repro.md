# 第三轮修复（2026-11-10）：v2 复测报告的 9 项逐条落地

> 依据：《回归复测报告_v2_新版.md》（2026-11-09 22:16-22:50）
> 方法：真实门禁状态 + 真实 SW 双进程并发 + 逐条触发点复测

---

## 0. 复测结论回顾

报告开头写得很准：
「这次确实修了不少（停机/COM重试/直齿断言/三段自检门/查重闸都真落地了），
但最核心的"房间级串行"在真实 D 流程里仍会并发；E 模式的严格闸
因为一个取值 bug 根本没生效。」

本轮就是针对「仍未修好」的那 9 条。

---

## 1. P0-1：E 模式严格闸失效（取值 bug）

报告原文：_parallel_mode_now() 只认字面 "D"/"E"；而真实落盘值是
"parallel" / "sequential"。实测 set-parallel-mode E 后 sw-request 仍返回
acquired:true —— 串行硬闸形同虚设。

根因：状态文件里存的是 _norm_pmode() 归一化后的英文串
（D -> parallel，E -> sequential），而函数只认字面 D/E。

修复（mode_gate._parallel_mode_now）：
- 一律走 _norm_pmode() 归一（它是全仓唯一事实源），再映射回 D/E；
- 兼容 并行/串行/大小写 等变体；
- 保留对嵌套结构（selection/plan）的读取；
- 最后回退到 get_parallel_mode()（它会自己对齐两份状态文件）。

验证：
    sequential -> E      parallel -> D      字面 E -> E
    未知 -> None         E -> 严格拒绝      D -> 只观测
    E 模式第二房间取锁 -> gate=CONCURRENT_BUILD

---

## 2. P0-2：D 流程仍并发（fair_start 泄漏 + 轮转不看 hold_mode）

报告原文：A 还没保存，锁已易手给 B；mode_state.sw_lock.fair_start 仍是陈旧的
两小时前时间戳。根因：cmd_sw_release() 只清 owner/acquired_at，
不清 fair_start/cmd_seq；_reclaim_lock() 的公平轮转不判断 hold_mode。

修复（两处）：

(1) cmd_sw_release() 释放时把 fair_start、cmd_seq 一起清零
    （它们与 owner 是同一个语义单元 —— 公平轮转的计时起点，
     不重置就等于给下一个持有者伪造了一个"我已经占了很久"的计时器），
    并回传 fair_reset:true。

(2) _reclaim_lock() 增加 _room_mode 分支：房间级独占时，公平轮转整体停用，
    只保留两个真判据 —— 持有者已死、或超过房间级兜底上限（默认 6 h）。
    has_waiter 只作为可审计信息，不再触发 reclaim。

---

## 3. P0-3：SW 看护杀掉正在建模的进程（环境杀手）

报告原文：本机内存常年 86-88%，阈值 85%，ensure_sw_guard 每次取锁后自动拉起
看护 -> 第 1 个 tick 就 taskkill SLDWORKS -> 建模被腰斩、无产物。
DSH_SW_GUARD_OFF=1 只阻止"新拉起"，不杀已在跑的看护。

修复（三处）：

(1) sw_guard._active_modeling_rooms()：读 mode_state.rooms，
    识别 "active 且未 ended/failed" 的房间。

(2) 超阈值分支：建模进行中只告警不杀（method="deferred_modeling"），
    等房间收尾后再按原策略处置。高内存在本机是常态而非尖峰，
    "先把活干完"必须优先。

(3) ensure_sw_guard() 遇到 DSH_SW_GUARD_OFF=1 时，
    同时停掉已在跑的看护，回传 stopped_existing / stopped_pid。
    语义上 OFF = "我不要看护"，就该连存量一起停。

---

## 4. P2：_readerthread GBK 解码崩溃

报告原文：mode_gate 多处 subprocess.run(..., text=True) 未写 encoding，
在 PYTHONUTF8=1 下按 UTF-8 解码 tasklist 的 GBK 输出 -> UnicodeDecodeError。
实测 sw-guard-stop / sw-guard-status 每次都抛。

修复：统一补 encoding="mbcs", errors="replace"。
  mode_gate.py   7 处
  sw_bridge.py   4 处
  conn_state.py  5 处
（mbcs 在 Windows 上就是系统 ANSI 代码页，正是 tasklist 的真实编码。）

---

## 5. P2：check-part / cleanup / self-check 抢 SW 锁

报告原文：_NO_LOCK_CMDS 不含这三个，调用 check-part 后 sw_lock.owner 变化
—— 纯文件操作抢了房间锁。

修复：三者加入 _NO_LOCK_CMDS。它们只读写本地 JSON / 开关文档，
不需要 SW 独占权（原先只登记在 _SW_TOLERANT_CMDS 里被允许 sw=None，
但仍会走取锁路径 —— 那是两回事）。

---

## 6. P1：去重闸「取数」与「写台账」两端没接全

报告原文：
  反例(1) 变量路径 m.save(target) -> 提取不到 -> 放行重建；
  反例(2) 中文文件名 -> 也提取不到；
  反例(3) run 成功后不写 part_registry -> 台账永远空 -> 查重永远 verdict=new。

修复（两端都补）：

取数端 _extract_output_paths() 重写为三层：
  1) 字面量：不再要求"以盘符开头"，允许中文文件名；
  2) 变量回溯：save(x) 里的 x，回脚本找 x = "....SLDPRT" 的赋值；
     二级回溯（解决 outdir = r"C:\out"; target = os.path.join(outdir, "x.SLDPRT")）；
  3) 相对路径：用脚本目录补全。

写台账端 新增 _auto_register_parts()，在 run 产物校验通过后自动登记：
  part 名 / room / file / material（读 .material.json）/ bbox（读 .origin.json）
  / dims / sha256。失败只记 warning，绝不反过来把建模判成失败。

实测（5 个用例）：
    literal_ascii.py   -> ['C:\out\DSH_repro_part.SLDPRT']
    chinese_literal.py -> ['C:\out\DSH_回归并发A.SLDPRT']       <- 反例(2) 修好
    var_path.py        -> ['C:\out\DSH_var_part.SLDPRT', ...]   <- 反例(1) 修好
    relative.py        -> [<脚本目录>\DSH_相对.SLDPRT]
    readonly_probe.py  -> []                                     <- 只读探针不误报

---

## 7. P2：new_part() 仍会关别人的未命名文档

报告原文：B 的 new_part 对 A 的未命名文档调 CloseDoc，抛
AttributeError('<unknown>.CloseDoc')（被吞掉只告警）。
A 侥幸保存成功，但结构上仍可能关掉别房间未保存的 doc。

修复：引入进程级文档身份锁 _MY_UNNAMED_DOCS。
  - new_part 新建成功后登记自己的文档标题；
  - 清理循环只关"标题在本集合里"的未命名文档；
  - 不在集合里 = 别人建的（可能是另一个房间的半成品）-> 绝不关，
    记入 _kept 让调用方知道。

真机验证（本次斜齿测试日志实录）：
    [swapi][WARN] new_part: 检测到 1 个已命名文档仍在 SW 中打开，
      按共享会话保护策略【未关闭】它们：零件2（未命名但非本进程创建）

---

## 8. S3：回执制（receipt id）缺失

报告原文：record_prime_report() 仍只写 plan["prime_reports"]，无 receipt id。
判定：未修。判据 15.2 S3「每个零件有总理回执 id；无回执的不计入完成」。

修复（mode2_flow.py）：
  - 每次上报生成 receipt_id（PR- + 内容摘要前 16 位）；
    同零件同内容幂等 -> 同一 id；尺寸变了 -> 新 id（可追踪是新一版）；
  - 新增 plan["prime_receipts"] 作为只增不减的回执台账；
  - 新增 prime_receipts() / check_part_receipted() 两个查询口，
    后者就是"能否计入完成"的唯一依据。

---

## 9. P1：斜齿造不出（_make_offset_plane 选不中）

报告原文：helix_gear(Beta=14.5337 度) 耗时 473 s 后返回失败：
「第 4 段偏移基准面创建失败: 基准面 'Top Plane' 选不中」。

修复（两层，都是真机迭代出来的）：

(1) _make_offset_plane：选面前强清场（ClearSelection2 + 退出残留草图），
    给选面 2 次机会。原因：前几段留下的残留选中对象会让 SelectByID2 失败。

(2) begin_sketch_on_feature：InsertSketch 后重试 3 次 + 等待激活。
    原因：InsertSketch 是异步的，上一段刚 extrude 完立刻建下一段草图，
    容易拿到 ActiveSketch=None（第 1-3 段恰好赶上，第 4 段就挂）。

真机验证（SW2025 SP05，本机实测）：
    m = swapi.new_part(sw, material="45")
    r = m.helix_gear(module_mm=2.0, teeth=24, thickness_mm=20.0,
                     helix_angle_deg=14.5337)
    -> ok: true, segments: 5, path: "segmented_loft"
       saved: true, size: 1,554,954 B, 耗时 214 s
       bbox: [-25.9968, 25.9968, 0, 20, -25.9968, 25.9968]
             (da 约 52 符合 m(z+2)=52；齿宽 20 符合)

    修正前：ok:false「第 4 段偏移基准面创建失败: 基准面 'Top Plane' 选不中」

斜齿从"造不出"变成"能造出"，且仍然保持 fail-closed（失败绝不退回圆柱）。

---

## 10. 回归

新增 tools/test_regression_v2_repro_fixes.py（39 项），覆盖 A~H 全部条目。

  新增 v2 复测回归                          39 / 39
  全量回归 run_regression.py                61 / 61 文件通过
  安装副本（~/.dsh/.agent-presets/engineering/tools）   一致，39 / 39 通过

---

## 11. 本轮仍未覆盖（如实记录）

1. helix_gear 耗时 214 s：分段放样 5 段，几何正确但慢。
   若要更快需实现真正的 3D 路径草图 + sweep（报告建议的路径 A）。
2. COM 重试仍未全量接线：extrude/cut 已包，revolve/save/选面等未包。
3. D 模式的并发：本轮修的是"锁不被抢走"（room 模式不参与轮转），
   但 D 模式本身允许多房间同时在世 —— 这是设计选择，不是缺陷。
   真要单房间串行仍需 select A E。
4. 报告边界提到的 总装 Mate/干涉、工程图 GB/T、三道防线全链路、Webots 未覆盖。
