# -*- coding: utf-8 -*-
"""回归测试：【2026-11-04 对点审计】9 类根因中 STILL-REPRO 项的修复锁定

对应审计报告：对点测试总汇报_9类.md（51 条，16 条 STILL-REPRO）。
本文件逐条钉死本次修掉的缺陷，**全部为静态/纯函数断言**，
不连接 SolidWorks、不启动任何进程、不改动任何被测文件。

覆盖条目（按报告编号）：
  A-09  defense_gate 支持 --help（与 mode_gate/workflow_gate 同一套 CLI 行为）
  A-17  / A-74  材料防线必须【回读零件真实材质】，不能只信凭据自报值
  A-22  JS 侧台账路径/键名按候选集合解析（不再硬编码裸名）
  A-30  三段式材质名归一化不再 split("|")[-1]，改候选集合比对
  A-33  create_sphere 的 cz 必须真正生效（平移实体），不能算了不用
  A-52  room-end 收尾后必须冻结产物（指纹 + 只读）
  A-85  modeling 阶段不能被 --force 静默收尾
  A-90  出图成功分支必须关闭自己打开的图纸文档
  A-91  截图默认文件名必须可归因且不互相覆盖
  A-94  领域违规必须按 severity 分级并读取 message
  A-97  工况路径不得写死（按候选集合解析）
  A-106 subagent-assign 覆盖绑定必须冲突检测 / 显式 --force
  A-107 end_sketch 提交必须校验；并提供公开 exit_sketch
  A-20  判定本体可置只读 + 异议登记通道

运行：python test_regression_20261104_audit_fixes.py
退出码：0 = 全部通过；1 = 有失败
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

PASS = 0
FAIL = 0
FAILURES = []
HERE = os.path.dirname(os.path.abspath(__file__))


def check(label, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] %s" % label)
    else:
        FAIL += 1
        FAILURES.append(label)
        print("  [FAIL] %s %s" % (label, extra))


def section(title):
    print("\n" + "-" * 70)
    print(title)
    print("-" * 70)


def read_src(name):
    with open(os.path.join(HERE, name), "r", encoding="utf-8",
              errors="replace") as f:
        return f.read()


def main():
    import swapi
    import defense_gate
    import mode_gate
    import gate_integrity

    # ═══════════════════════════════════════════════════════════════
    section("A-09 defense_gate CLI：--help 必须可用且不判未知命令")
    dg_src = read_src("defense_gate.py")
    check("★ 源码含 --help 白名单分支",
          '("--help", "-h", "-?", "help", "--usage")' in dg_src)
    check("★ 未知命令的 usage 提示里带上 --help",
          "--help" in dg_src.split("unknown command")[1][:600])
    check("帮助文案覆盖 check-room / check-task / status",
          all(k in dg_src for k in ("check-room <房间名>", "check-task", "status")))

    # ═══════════════════════════════════════════════════════════════
    section("A-30 三段式材质名：候选集合比对（不再 split('|')[-1]）")
    m = swapi._material_names_match
    check("★ 库名|材料名 与 纯材料名 视为同一材料",
          m("SolidWorks Materials|普通碳钢", "普通碳钢") is True)
    check("★ 三段式（库|类别|牌号）能取到牌号",
          m("SolidWorks Materials|钢|Q235", "Q235") is True)
    check("★ 顺序不同也能匹配（库|材料|规格 vs 纯材料）",
          m("SolidWorks Materials|普通碳钢|Q235", "普通碳钢") is True)
    check("★ 大小写/空白差异仍匹配",
          m("solidworks materials|AISI 1020", "AISI  1020") is True)
    check("★ 跨标准替代【不得】被归一化吸收（Q235 vs AISI 1020）",
          m("Q235", "AISI 1020") is False)
    check("空值不匹配（不吃空字符串）", m("", "Q235") is False)
    check("defense_gate 与 swapi 同口径",
          defense_gate._material_names_equivalent("a|b|c", "c") is True
          and defense_gate._material_names_equivalent("Q235", "AISI 1020") is False)

    # ═══════════════════════════════════════════════════════════════
    section("A-17 / A-74 材料防线必须回读零件真实材质")
    check("★ defense_gate 提供旁挂记录读取函数",
          callable(getattr(defense_gate, "_read_sw_internal_sidecar", None)))
    check("★ 旁挂路径不存在时返回 None（不抛、不造文件）",
          defense_gate._read_sw_internal_sidecar(
              os.path.join(HERE, "_不存在的零件_.sldprt")) is None)
    dg_src2 = dg_src
    check("★ check_material_attestation 内含 sw_internal 回读判据",
          "sw_internal_evaluated" in dg_src2)
    check("★ 矛盾时置 model_trustworthy=False",
          "model_trustworthy" in dg_src2 and "= False" in dg_src2)
    check("★ 只以密度/swapi 声明为硬证据（不靠纯名字猜别名）",
          "_declared is False" in dg_src2 and "_dens_mismatch" in dg_src2)
    check("★ 名称差异降级为显式告警（可追溯但不误杀）",
          "sw_internal_name_mismatch" in dg_src2)
    check("★ 缺旁挂时如实标注未评估（不静默当通过）",
          "sw_internal_note" in dg_src2 and "未评估" in dg_src2)
    check("swapi 仍然写出旁挂记录（供上条读取）", "sw_internal_record_path" in read_src("swapi.py"))

    # ═══════════════════════════════════════════════════════════════
    section("A-22 JS 侧台账不再硬编码裸名 registry")
    js = read_src(os.path.join("..", "plugins", "dsh-engineering-ui", "lib",
                               "defense-sign.js"))
    check("★ 台账按候选集合枚举（regCands）", "regCands" in js)
    check("★ 支持按任务隔离的 artifacts_registry.<task_id>.json",
          "artifacts_registry.' + String(_tid)" in js or "artifacts_registry." in js)
    check("★ 键名支持房间名/词干/rooms 子表三种写法",
          "_stem" in js and "reg.rooms" in js)
    check("★ 找不到台账时显式标注未评估（防空集合恒真）",
          "evaluated = false" in js and "未评估" in js)

    # ═══════════════════════════════════════════════════════════════
    section("A-33 create_sphere 的 cz 必须生效")
    check("★ 新增实体平移实现 _translate_solid",
          callable(getattr(swapi.SWModel, "_translate_solid", None)))
    cs = inspect.getsource(swapi.SWModel.create_sphere)
    # ── 【S-06 真机修正】第一轮用"实体平移"实现 cz，实测
    #   IModelDocExtension 上并没有 InsertMoveCopyBody2（AttributeError），
    #   那条路走不通。现在改为【偏移基准面】：cz≠0 时插入偏移面并在其上
    #   画球，球心 z 由几何本身决定。断言随之更新。
    check("★ create_sphere 用偏移基准面实现 cz",
          "_make_offset_plane" in cs and "begin_sketch_on_feature" in cs)
    check("★ 偏移面失败必须告警（不静默留下 z=0 的球）",
          "请勿按已定位处理" in cs and "_warn" in cs)
    check("★ 建好后实测球心并与请求比对（不符即告警）",
          "GetPartBox" in cs and "球心实测" in cs)
    check("★ begin_sketch 确实被调用（曾因编辑误删导致草图不存在）",
          'self.begin_sketch("Front Plane")' in cs)
    check("★ 新增 _make_offset_plane / begin_sketch_on_feature",
          callable(getattr(swapi.SWModel, "_make_offset_plane", None))
          and callable(getattr(swapi.SWModel, "begin_sketch_on_feature", None)))
    check("偏移面用 InsertRefPlane 距离约束（8）",
          "InsertRefPlane" in inspect.getsource(swapi.SWModel._make_offset_plane)
          and "_CONSTRAINT_DISTANCE = 8" in inspect.getsource(swapi.SWModel._make_offset_plane))

    # ═══════════════════════════════════════════════════════════════
    section("A-52 room-end 收尾后冻结产物")
    check("★ 新增 _freeze_room_artifacts",
          callable(getattr(mode_gate, "_freeze_room_artifacts", None)))
    check("★ 冻结记录含 sha256 与只读结果",
          "sha256" in inspect.getsource(mode_gate._freeze_room_artifacts)
          and "readonly" in inspect.getsource(mode_gate._freeze_room_artifacts))
    check("★ room-end 写入 mode_state.rooms.<房间>.frozen",
          '"frozen"' in inspect.getsource(mode_gate.cmd_room_end))
    check("★ 返回体回传 artifacts_frozen（只读失败也可见）",
          "artifacts_frozen" in inspect.getsource(mode_gate.cmd_room_end))

    # ═══════════════════════════════════════════════════════════════
    section("A-85 modeling 阶段不得被 --force 静默收尾")
    re_src = inspect.getsource(mode_gate.cmd_room_end)
    check("★ 存在 FORCE_ROOM_END_WHILE_MODELING 闸门",
          "FORCE_ROOM_END_WHILE_MODELING" in re_src)
    check("★ 需要显式确认环境变量才放行",
          "DSH_ROOM_END_FORCE_MODELING" in re_src)
    check("★ 非 --force 的正常收尾不受影响（闸门在 force 分支内）",
          re_src.index("FORCE_ROOM_END_WHILE_MODELING")
          > re_src.index("if force and not dry_run:"))

    # ═══════════════════════════════════════════════════════════════
    section("A-90 出图成功分支必须关档")
    sb = read_src("sw_bridge.py")
    check("★ 新增 _close_drawing_doc", "def _close_drawing_doc(" in sb)
    check("★ 成功分支也走关档（finally / drawing_doc_closed）",
          "drawing_doc_closed" in sb)
    check("★ 关档失败只记 warning，不改判出图结论",
          "图纸文档关闭失败" in sb)

    # ═══════════════════════════════════════════════════════════════
    section("A-91 截图文件名可归因、不互相覆盖")
    shot = inspect.getsource(swapi.SWModel.screenshot)
    check("★ 默认名不再写死 solidworks_live.png",
          '"solidworks_live.png"' not in shot)
    check("★ 文件名含时间戳与 pid",
          "%Y%m%d-%H%M%S" in shot and "os.getpid()" in shot)
    check("★ 支持 tag / for_part 归因",
          "tag" in inspect.signature(swapi.SWModel.screenshot).parameters
          and "for_part" in inspect.signature(swapi.SWModel.screenshot).parameters)
    check("★ 显式传 path 时行为不变（向后兼容）",
          "if path is None:" in shot)

    # ═══════════════════════════════════════════════════════════════
    section("A-94 领域违规按 severity 分级 + 读取 message")
    cd = inspect.getsource(defense_gate.check_domain_attestation)
    check("★ 存在 violation_breakdown 分级统计",
          "violation_breakdown" in cd)
    check("★ 读取 message 字段（不只 rule_id）",
          '"message"' in cd and "msg" in cd)
    check("★ 严重度未知时 fail-closed",
          "无法判定严重度" in cd)
    check("★ WARNING/INFO 不再一律当 CRITICAL 阻断",
          "non_blocking_violations" in cd)

    # ═══════════════════════════════════════════════════════════════
    section("A-97 工况路径不得写死")
    geo = inspect.getsource(defense_gate._material_geometry_check)
    check("★ 工况文件按候选集合查找（_lc_cands）", "_lc_cands" in geo)
    check("★ 回显实际使用的工况文件路径", "load_case_file" in geo)
    check("★ 不再只有 STATE_DIR/load_cases 一条路径",
          '_lc_path = os.path.join(STATE_DIR, "load_cases", "gate_load_case.json")'
          not in geo)

    # ═══════════════════════════════════════════════════════════════
    section("A-106 subagent-assign 覆盖必须冲突检测")
    sig = inspect.signature(mode_gate.cmd_subagent_assign)
    check("★ 新增 force 形参", "force" in sig.parameters)
    sa = inspect.getsource(mode_gate.cmd_subagent_assign)
    check("★ 存在 SUBAGENT_BINDING_CONFLICT", "SUBAGENT_BINDING_CONFLICT" in sa)
    check("★ 改绑必须留痕 subagent_rebind_log", "subagent_rebind_log" in sa)
    check("★ 拒绝覆盖时回传双方 id",
          "existing_subagent_id" in sa and "incoming_subagent_id" in sa)

    # ═══════════════════════════════════════════════════════════════
    section("A-107 end_sketch 提交校验 + 公开 exit_sketch")
    es = inspect.getsource(swapi.SWModel.end_sketch)
    check("★ 记录 InsertSketch 返回值与异常", "_end_sketch_commit" in es)
    check("★ 提交未确认时告警（不静默）", "_warn" in es and "未确认成功" in es)
    check("★ 提供公开 exit_sketch", callable(getattr(swapi.SWModel, "exit_sketch", None)))
    check("exit_sketch 是 end_sketch 的别名（语义一致）",
          "return self.end_sketch" in inspect.getsource(swapi.SWModel.exit_sketch))
    wrapper_src = sb[sb.index("_ns_docs, _ns_parts"):][:900] if "_ns_docs, _ns_parts" in sb else ""
    check("★ run 的 wrapper 回传 open_docs（失败清场线索）",
          "open_docs" in sb and "_ns_docs" in sb)
    check("★ 失败时给出 cleanup_hint（不自动强杀别的文档）",
          "cleanup_hint" in sb)

    # ═══════════════════════════════════════════════════════════════
    section("A-20 判定本体可置只读 + 异议通道")
    check("★ 新增 protect_gate_files",
          callable(getattr(gate_integrity, "protect_gate_files", None)))
    check("★ 新增 register_objection",
          callable(getattr(gate_integrity, "register_objection", None)))
    pr = gate_integrity.protect_gate_files(scope="all", dry_run=True)
    check("★ dry-run 能列出受保护文件且不落盘",
          pr.get("ok") is True and len(pr.get("protected") or []) >= 5,
          pr)
    ob = gate_integrity.register_objection("", "")
    check("★ 无理由的异议被拒绝（不制造空记录）", ob.get("ok") is False)
    check("★ 异议按 JSONL 追加（可审计）",
          "objections.jsonl" in inspect.getsource(gate_integrity.register_objection))
    gi_src = read_src("gate_integrity.py")
    check("★ CLI 挂载 protect / objection 子命令",
          '"protect"' in gi_src and '"objection"' in gi_src)

    # ═══════════════════════════════════════════════════════════════
    section("【本次新发现】双副本遮蔽：mode_gate 必须优先导入同目录模块")
    mg = read_src("mode_gate.py")
    check("★ _BASE_DIR 被显式提到 sys.path 最前",
          "sys.path.insert(0, _BASE_DIR)" in mg)
    check("★ STATE_DIR 排在 _BASE_DIR 之后（不再抢在前面）",
          "sys.path.insert(1, STATE_DIR)" in mg)
    check("★ 实际导入到的就是工作区这份（不是安装副本）",
          os.path.normcase(os.path.abspath(gate_integrity.__file__))
          == os.path.normcase(os.path.join(HERE, "gate_integrity.py")),
          gate_integrity.__file__)

    # ═══════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("PASS=%d FAIL=%d" % (PASS, FAIL))
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  - " + f)
    print("=" * 70)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
