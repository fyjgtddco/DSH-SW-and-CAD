# 第二次修复（2026-11-09）：7 项核心缺口落地为代码

> 依据：
> 1. 《减速机设计过程回溯报告·第二版》第7节 P1~P5、第9节整改 A~E、第15节验收清单
> 2. 《bug 修复核查报告》第三节「未修复的 7 项」、第四节「核心问题仍未解决」
> 3. SW API 联网调研 + 本机实测（SW2025 rev 33.5.0.53 / Python 3.13.15 / pywin32）

---

## 0. 先说一个前提：核查报告用的不是本仓库

核查报告的环境是 test 目录下解压的 DSH-SolidWorks-MCP-main 副本，其 mode_gate.py
为 414,903 B（2026-11-07 12:52）。

本仓库 mode_gate.py 为 457,103 B（2026-11-08 13:37），比它新一天。

因此核查报告里标「未找到」的部分项目，本仓库其实已有实现：

| 项 | 核查报告结论 | 本仓库实际状态 |
|---|---|---|
| 一键停机开关 | 未找到 | 已存在 tools/_stop.py，5 个入口全部接线 |
| hold_mode=room | 仍是 command | SW_ROOM_HOLD = 1，_room_hold_enabled() 生效 |
| 去重闸门 | 未找到 | part_registry.check_reusable + sw_bridge._check_part_reuse_before_run |
| ZSIGN | 未找到 | 改为「基准面姿态代码查询」（plane_axes_world，M5） |
| 齿轮断言 | 未找到 | swapi._verify_gear_geometry（齿顶圆 + 体积双重回读） |

但本次核查仍抓出了真问题，其中两条是「注释/文档里承诺了、代码里没有」：

- _require_build_exclusive：只存在于 mode_gate.py:6029 的注释里，函数根本不存在；
- .pr.json 朝向声明：只写在 SKILL.md 与 preset 提示词里，代码零实现。

这两条正是用户强调的「必须写在代码里，不能只写在提示词」。

---

## 1. 本轮实际改了什么（全部为代码级强制）

### 1.1 新增 tools/part_pose.py —— pr.json 朝向声明（对应核查第 6 项）

- write_pr_record / read_pr_record / validate_pr_record
- check_assembly_pose：J2 / J4 / J5 三条判据
  - J4 零旋转装配报警：声明 up_axis != world_up 却读到单位阵 -> FAIL
  - J2 声明冲突；J5 声明对齐却被转过 -> WARNING（strict 时升级 FAIL）
  - 缺 pr.json 直接 FAIL（不是 UNKNOWN）—— 对齐第15.2节 J6「无声明 = 不可审计」
- assert_room_contract：房间级断言（J1/J2/J6）
- CLI：write / read / validate / check-assembly / check-room

接入点：swapi.save() 与 save_as() 在材料凭据、origin 记录之后
自动调用 _attach_pr_record()，落 <零件>.SLDPRT.pr.json。
新增 SWModel.set_part_pose() 供建模脚本显式声明朝向。

### 1.2 新增 tools/part_verify.py —— 三段自检（对应核查第 5 项）

- 第一段 compare_bbox：包围盒逐面容差（默认 0.1 mm，硬上限 1.0）
- 第二段 compare_volume：体积偏差（默认 2%，硬上限 10%）
- 第三段 compare_features：用户特征计数 + 关键特征存在性
- three_stage_check：任何一段被跳过 -> INCOMPLETE，绝不判 PASS
- self_check_model：对已开模型真实回读并落盘 reports/<零件>.self_check.json

为什么体积段是关键（报告第7节 P5 的机理）：
phi-da 圆盘与真齿齿轮的包围盒几乎一样（齿顶圆就是最大外径），
只验 bbox 必然放过无齿件；体积差 15%~25% 才抓得住。回归用例 C7 就是这条的锚点。

接入点：
- sw_bridge.py 新增 self-check 命令（只读，停机期间也放行）
- mode_gate._selfcheck_gate_for_room()：room-end 前逐个零件核验
  reports/<零件>.self_check.json，缺记录或不 PASS 则拒绝下线

### 1.3 visual_check 补齐体积与特征数断言（P5 的机制性根除）

normalize_expect 新增 volume_mm3 / volume_tolerance_pct / feature_count；
闭环每轮多做两项断言：体积偏差、用户特征计数。
读不到体积/特征树 -> 判 MISMATCH（「读不到」不等于「没问题」）。

注意：修这条时回归当场抓到一个真实绕过：这些新字段原先放在 bbox is None
的提前 return 之后，导致「只传体积期望、不传 bbox」时体积判据被静默忽略。
已把解析挪到提前返回之前。这正是「不可用被折叠成通过」的同一条机理。

### 1.4 补上 _require_build_exclusive —— 房间级独占（核查第 1 项的真缺口）

核查报告说「hold_mode 仍是 command」，本仓库其实已是 room；
但 _require_build_exclusive 只有注释没有实现（mode_gate.py:6029 提到、
全仓 grep 仅命中该注释）。本轮实现：

- _parallel_mode_now：读并行策略（D/E）
- _build_exclusive_strict：E -> 默认严格拒绝；D -> 只观测（不废掉用户可选策略）；
  DSH_CONCURRENT_BUILD_STRICT 可双向覆盖
- _require_build_exclusive：严格模式下有别的房间在世 -> 拒绝取锁并留痕
  （写 concurrent_build_violations.json）
- 接到 cmd_sw_request 取锁路径（回归 E10 做源码级接线断言）

---

## 2. 来自「SW API 联网调研 + 本机实测」的四处修正

子代理在 SW2025 rev 33.5.0.53 上实测，纠正了我最初写错的三处，并澄清一处。

### 2.1 IMathTransform.ArrayData 布局（原先写错）

实测（由三个默认基准面的原始数组反推）：

    d[0:3]  = 局部 X 轴（世界系方向）
    d[3:6]  = 局部 Y 轴
    d[6:9]  = 局部 Z 轴 = 法向
    d[9:12] = 原点平移
    d[12]   = 缩放（恒 1.0）

上视基准面实测 [1,0,0, 0,0,-1, 0,1,0, 0,0,0, 1,0,0,0]
-> 法向 +Y，草图 (x,y) -> 世界 (x,0,-y)，与官方「Top Plane 会镜像」的结论一致。

原先按「行主序 4x4、平移在 3/7/11」解析 —— 会把平移混进旋转、
又漏掉真正的两列，判定必错。已修正，并加回归 B9 钉住真实数组。

### 2.2 体积回读：GetMassProperties2 必须查 Status

实测返回 12 个 double：[0..2]质心 [3]体积 [4]表面积 [5]质量 [6..11]惯性矩；
单位是 m^3，乘 1e9 得 mm^3。
Status：0=OK，2=NoBody —— NoBody 时数组可能全 0，
直接当体积会得出「体积 0」的假结论，比失败更糟。已在 read_volume_mm3 检查。

另外实测澄清：IModelDoc2.GetMassProperties 是属性不是方法（无括号）。
本仓库 _body_volume_mm3 已按此处理。

### 2.3 特征计数必须用白名单（实测：切除返回 ICE）

实测：空零件 GetFeatureCount = 18（13 文件夹 + 3 基准面 + 原点 + Tables）；
做一个切除，GetTypeName2 返回 "ICE"（Instant3D）而不是 "Cut"。

若用「排除式」计数，ICE 会被当成用户特征、真正的 Cut 又不出现 ——
口径两处都错。已改为 USER_FEATURE_TYPES 白名单，
并把未知类型单独归类（不冒充用户特征、也不静默丢弃）。回归 C8 钉住这条。

### 2.4 顺带修正：save() 的 SaveAs3 Options（2 -> 1）

实测四组 Options：
- Options=2（swSaveAsOptions_Copy）：rc=0、文件确实落盘，
  但 GetPathName 保持空串，文档并未绑定到新路径；
- Options=0/1/4：GetPathName 正常更新。

原代码用 SaveAs3(target, 0, 2)。后果：save() 成功后紧接着要写
.material.json / .origin.json / .pr.json 三份旁挂文件、还要 body_box_mm
回读几何；若文档没绑到 target，pr.json 里的 bbox 可能取自另一个文档。
已改为 Options=1（Silent），并在 _attach_pr_record 里加了
「GetPathName 与 target 不一致就不写 bbox」的保护。

---

## 3. 回归

新增两个测试文件，全部纳入 run_regression.py：

| 文件 | 覆盖 | 结果 |
|---|---|---|
| test_regression_p1_pose_selfcheck.py | pr.json 写入/读取/校验、J2/J4/J5、三段自检、visual_check expect 扩展、ArrayData 布局、ICE 白名单 | 74 / 74 |
| test_regression_p4_build_exclusive.py | 房间级独占、严格度判定、留痕、接线断言 | 22 / 22 |

全量：python run_regression.py -> 59 / 59 文件通过。

---

## 4. 仍未做 / 未验证（如实记录）

1. 未在本机真机跑过完整建模链路：本轮全部为代码级 + 离线回归，
   self-check 命令在真实 SW 上的端到端表现、pr.json 自动落盘的真机效果
   尚未实跑（需要 SW 会话与一个真实零件）。
2. 齿轮专用断言已存在（_verify_gear_geometry），但本轮没有把它接到
   room-end 的强制路径上 —— 目前靠通用的三段自检（体积）覆盖。
   建议下一步把 gear() 的 verify 结果并入 self_check.json。
3. 装配朝向断言（J2/J4/J5）目前是独立工具，尚未嵌入总装房间的 room-end。
   需要 read_component_pose() 的真实输出才能接，属下一步。
4. 报告第15.3节的上下文经济性（C1~C3）与第15.4节的设计校核（V1~V3）不在本轮范围。

---

## 5. 真机端到端验证（SW2025 SP05 / rev 33.5.0.53，本机实测）

在 SW 已连接（0 个文档、visible=false）的干净状态下实跑，结论如下。

### 5.1 齿轮定义断言（真机）

    齿轮 z=24, m=2, b=40
      齿顶圆 设计 52.0 mm -> 实测 51.9804 mm（偏差 0.0196 mm，判据 <=0.05）
      体积   解析 71996.23 mm3 -> 实测 71996.23 mm3（偏差 0%）
      verdict = ok

### 5.2 无齿圆盘对照 —— P5 机理被真机证实

用同一个齿顶圆 da=52 拉一个实心圆盘（模拟「把齿圈画成圆柱」的假件），
再拿真齿轮的期望值去判：

    包围盒：真齿轮 [-25.99,25.99, 0,40, -25.99,25.99]
            圆盘   [-26.00,26.00, 0,40, -26.00,26.00]   -> bbox 段 ok（抓不到）
    体积  ：真齿轮 71996.23 mm3，圆盘 84948.67 mm3
            偏差 17.99% > 容差 2%                        -> 体积段 FAIL
    总判  ：FAIL

这直接证实了报告第7节 P5 的判断：只验包围盒抓不到无齿件，体积才抓得住。
C7 回归用例与本次真机结果一致。

### 5.3 pr.json 自动落盘（真机）

    save() -> DSH_小齿轮_live.SLDPRT.pr.json 已生成
    内容：up_axis=Z, up_sign=1, main_axis=Z, base_plane=Top Plane,
          sketch_y_sign=-1, bbox_mm=[-25.9902,25.9902,0,40,-25.9902,25.9902],
          source=explicit
    validate -> ok（无 errors，无 warnings）

即 set_part_pose() 的显式声明被 save() 正确带到了落盘路径旁。

### 5.4 真机暴露的一个新缺陷（已修）

首轮真机自检出现：

    feature_types: [""]        user_feature_count: 0
    self_check verdict: PASS   <-- 又是「读不到被折叠成通过」

诊断结论（真机实测）：

    f.GetTypeName2      -> 属性，返回 str（is_callable = False）
    f.GetTypeName2()    -> TypeError: 'str' object is not callable
    mdl.GetFeatureCount -> 属性（int），加括号同样 TypeError

旧代码写死 f.GetTypeName2()：异常被 bare except 吞掉 -> 所有 type = None
-> 全部落进 unknown -> 用户特征数恒为 0 -> 而"期望 0 个"恰好判通过。

修复：新增 _prop_or_call()（part_verify 与 visual_check 各一份，同口径），
兼容属性/方法两种形态。修复后真机复测：

    total_features = 22
    user_count = 2   -> ["Extrusion", "ICE"]      <-- 切除确实是 ICE
    sketch_count = 2 -> ["ProfileFeature", "ProfileFeature"]
    infra_count = 18
    unknown_count = 0
    self_check verdict = PASS（三段全部 checked=true）

这条同时独立复现了子代理的实测结论：本机 SW2025 上"切除"的
GetTypeName2 返回 "ICE" 而不是 "Cut"，因此特征计数必须用白名单。

### 5.5 总装组件变换（真机）

    new_assembly() -> "装配体1"
    AddComponent5 -> 组件已加入
    Transform2.ArrayData =
      [1,0,0,  0,1,0,  0,0,1,  0,-0.005,0,  1,0,0,0]

对照子代理给的布局完全吻合：d[0:3]=X轴 d[3:6]=Y轴 d[6:9]=Z轴 d[9:12]=平移。
check_assembly_pose 判定 PASS、identity_like=true、axes 解析正确。

注意：本次测试件声明 main_axis=Z 而包围盒最长维是 X（60mm），
校验器如实地给出一条 warning —— 说明"声明与几何自洽"这条检查在工作。

### 5.6 齿轮断言并入 self_check（真机）

    executed = [bbox, volume, features, gear]
    gear_stage: ok=true, teeth=24, da 实测 51.9804 / 设计 52.0, volume_dev 0%
    verdict = PASS

即 15.1 M1 要求的「特征计数=z、da/df 实测、体积偏差」已进入 room-end
可读的证据文件，不再是 gear() 内部的临时返回字段。

---

## 6. 本轮新增/修改文件清单

新增：
- tools/part_pose.py                            pr.json 写入/校验/装配断言
- tools/part_verify.py                          三段自检 + 齿轮第四段
- tools/test_regression_p1_pose_selfcheck.py    80 项
- tools/test_regression_p4_build_exclusive.py   22 项
- tools/test_regression_room_gates_p5.py        18 项
- engineering/docs/fixes-20261109-round2-core.md 本文

修改：
- tools/swapi.py          _attach_pr_record / set_part_pose / SaveAs3 Options 2 -> 1
- tools/sw_bridge.py      self-check 命令 + help 条目 + 停机白名单
- tools/mode_gate.py      _selfcheck_gate_for_room / _pose_gate_for_room /
                          _require_build_exclusive / 并行策略识别
- tools/visual_check.py   体积与特征数断言 + normalize_expect 提前返回修复
                          + _vc_prop_or_call
- engineering/plugins/dsh-engineering-ui/preset-engineering.patch.yml
                          人设补「代码已强制」说明与命令用法

---

## 7. 回归过程中再抓到的三个真实缺陷（顺手修掉）

全量回归从 59/60 反复出现 test_regression_sw_guard_lifecycle 失败，
单独跑却 25/25 通过。按「单独过、汇总挂」这条线索挖下去，
发现不是测试问题，而是**看护进程（sw_guard）的三处真实缺陷**。

### 7.1 看护进程被父进程的管道带死（stdio 继承）

- 现象：ensure_sw_guard() 起了看护（pid 8440），2.5 s 后它已不在，
  pid 文件指向死进程 -> _sw_guard_alive() 返回 None ->
  下一次调用再起一个（8440 -> 8496）-> 后台进程泄漏。
- 根因：run_regression.py 用 subprocess.run(capture_output=True) 跑每个用例，
  用例进程的 stdout 是一根管道；看护进程默认继承它。
  runner 收完输出就关管道 -> 看护下一次 _utf8_out() 写 stdout
  立刻 broken pipe -> 退出。
- 修复：Popen(..., stdin=DEVNULL, stdout=DEVNULL, stderr=DEVNULL, close_fds=True)。
  看护自己的留痕本来就走 --log 文件，不依赖 stdout。
- 这条与报告 9-A-2「子代理禁止起后台进程、不许泄漏」是同一条纪律：
  后台守护绝不能挂在调用方的管道上。

### 7.2 超阈值但「没 SW 可杀」时提前退出

- 原逻辑：连续 3 个 tick 只在"没 SW 可杀"的情况下超阈值就退出，
  理由是"继续空转没有意义"。
- 真机实测推翻了这个判断：本机常驻内存约 89%，阈值 85% ——
  每一次正常建模都会立刻触发告警，并在约 30 s 后退出。
  后果与上面一样：用户真正开始建模时守护已不在，且 pid 文件指向死进程。
- 修复：只要没真的杀掉 SW，就一直守着；把"为什么一直在告警"记进
  stopped_reason（breach_sw_not_running_continue），由 status 暴露。

### 7.3 杀掉 SW 之后看护自己也退出

- 原逻辑：killed=True -> stopped_reason="killed" -> break 退出。
- 真机实测（日志 pid=22220）：内存 92.6% 触发真实 taskkill，
  看护随即退出。但用户会重新打开 SW 继续建模，而机器内存仍是 89%~92%
  （本机常态，不是瞬时尖峰）—— 保护在最需要的时刻恰好缺席。
- 修复：改为 stopped_reason="killed_continue"，不退出，继续守护。
  杀过几次由 state.killed / alerts 记录，不靠"退出"表达。

### 7.4 测试隔离（配套）

test_regression_sw_guard_lifecycle.py 原先直接用默认阈值（85%），
于是本机真实内存水位（约 89%）与"SW 是否恰好在跑"直接决定用例成败。
现已显式传 cpu_limit=1000.0, mem_limit=1000.0，让看护只采样不击杀 ——
被测的性质（幂等、存活、pid 复用）才唯一。

这三条都不在本轮原计划里，是回归逼出来的。
但它们恰好都在报告第9节整改 A「停机开关 / 进程泄漏」的射程内，属同一类问题：
后台进程的生命周期没被认真对待。

---

## 8. 最终状态

- 全量回归：60 / 60 文件通过（含新增 3 个测试文件）。
- 新增测试用例数：80 + 22 + 18 = 120 项。
- 工作区已清理：运行时状态文件（load_cases/*.json）已还原，
  临时探针脚本已删除，测试遗留的看护进程已终止。
