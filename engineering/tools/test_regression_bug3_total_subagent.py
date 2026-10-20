# -*- coding: utf-8 -*-
"""回归测试：装配类任务必须先开【总计子代理】（问题3）

用户实测反馈："还是有 AI 不会开总计子代理的，必须改成代码强制它们开"。
根因：AI 直接 sw_bridge.py run <脚本> 开干，绕过 select / room-start 上
      全部编制校验 —— 强制链一次都不触发。

本测试锁死 sw_bridge._mode2_preflight_for_run 的行为：
  · 单零件任务（Mode 1）必须放行，不得误伤；
  · 多零件/装配类任务（未走 Mode 2、无编制）必须拒绝；
  · 已登记编制 / 已进入 Mode 2 时必须放行。
"""
import os
import sys
import json
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

pass_n = 0
fail_n = 0


def check(name, ok, detail=""):
    global pass_n, fail_n
    if ok:
        pass_n += 1
        print("  [PASS] " + name + (("  -> " + str(detail)) if detail else ""))
    else:
        fail_n += 1
        print("  [FAIL] " + name + (("  -> " + str(detail)) if detail else ""))


def main():
    # 隔离状态目录：绝不能读到现场遗留的 mode=2
    # ⚠️ 必须在【import sw_bridge / mode_gate 之前】设置，且用独立临时目录，
    #    否则会读到安装副本现场状态（那台机器的 mode_state 可能正处在 mode=2），
    #    导致"多零件拒绝"用例被短路成放行（实测：安装副本内 5 项失败）。
    state_dir = tempfile.mkdtemp(prefix="bug3_m2state_")
    os.environ["DSH_STATE_DIR"] = state_dir
    # ⚠️ 关键：mode_gate / sw_bridge 在【import 时】就把 STATE_PATH 固化成常量，
    #    之后再改环境变量不会生效。因此必须确保【没有任何模块在本函数之前
    #    被导入过】—— 所以 sw_bridge / mode_gate 一律在这里【延迟导入】。
    for _m in ("sw_bridge", "mode_gate", "workflow_gate", "_store", "_root",
               "defense_gate", "mode2_flow"):
        sys.modules.pop(_m, None)
    # 清掉可能被父进程继承的工程根（否则 _store 会从它推导出安装副本目录）
    os.environ.pop("DSH_ENGINEERING_ROOT", None)

    import sw_bridge as s

    check("sw_bridge 暴露 _mode2_preflight_for_run",
          hasattr(s, "_mode2_preflight_for_run"))

    # ── 场景1：单零件脚本 → 放行 ──────────────────────────────────────
    d1 = tempfile.mkdtemp(prefix="m2_single_")
    p1 = os.path.join(d1, "build.py")
    with open(p1, "w", encoding="utf-8") as f:
        f.write("import swapi\nm = swapi.new_part()\nm.rect(0,0,10,10)\n")
    r1 = s._mode2_preflight_for_run(p1)
    check("单零件任务放行（不误伤 Mode 1）", r1.get("ok") is True, r1.get("via"))

    # ── 场景2：同目录已有 2 个 .sldprt → 拒绝 ─────────────────────────
    d2 = tempfile.mkdtemp(prefix="m2_multi_")
    p2 = os.path.join(d2, "build.py")
    with open(p2, "w", encoding="utf-8") as f:
        f.write("import swapi\nm = swapi.new_part()\nm.save('a.sldprt')\n")
    open(os.path.join(d2, "A.SLDPRT"), "w").close()
    open(os.path.join(d2, "B.SLDPRT"), "w").close()
    r2 = s._mode2_preflight_for_run(p2)
    check("多零件（多个 .sldprt）拒绝直接建模", r2.get("ok") is False)
    check("拒绝时 gate = MODE2_TOTAL_SUBAGENT_REQUIRED",
          r2.get("gate") == "MODE2_TOTAL_SUBAGENT_REQUIRED", r2.get("gate"))
    check("拒绝时给出可执行 how 指引",
          isinstance(r2.get("how"), list) and len(r2.get("how") or []) >= 3)

    # ── 场景3：脚本内多次 new_part/save（装配语义）→ 拒绝 ──────────────
    d3 = tempfile.mkdtemp(prefix="m2_script_")
    p3 = os.path.join(d3, "asm.py")
    with open(p3, "w", encoding="utf-8") as f:
        f.write("import swapi\n"
                "a = swapi.new_part()\na.save('a.sldprt')\n"
                "b = swapi.new_part()\nb.save('b.sldprt')\n")
    r3 = s._mode2_preflight_for_run(p3)
    check("脚本含多零件语义 → 拒绝", r3.get("ok") is False, r3.get("gate"))

    # ── 场景4：装配关键词 → 拒绝 ──────────────────────────────────────
    d4 = tempfile.mkdtemp(prefix="m2_kw_")
    p4 = os.path.join(d4, "asm.py")
    with open(p4, "w", encoding="utf-8") as f:
        f.write("# 总装：把所有零件装配起来\nimport swapi\n")
    r4 = s._mode2_preflight_for_run(p4)
    check("脚本含『总装』关键词 → 拒绝", r4.get("ok") is False, r4.get("gate"))

    # ── 场景5：已进入 Mode 2 → 放行（room-start 已校验过编制）──────────
    import mode_gate as mg
    st = mg.load_state() or {}
    st["mode"] = "2"
    mg.save_state(st)
    r5 = s._mode2_preflight_for_run(p2)
    check("已声明 Mode 2 → 放行", r5.get("ok") is True, r5.get("via"))


    # ── 场景6：现场残留【上一轮】编制 → 仍必须拒绝 ────────────────────
    # 实测：安装副本里残留旧编制，守卫误判成"已登记"→ 多零件照样放行。
    try:
        import mode2_flow as _m2
        _plan = _m2.load_plan()
        _plan["subagent_plan"] = {
            "decided_by": "总计子代理",
            "task_id": "OLD_TASK_20200101",
            "rationale": "上一轮遗留",
            "count": 1,
            "rooms": [{"name": "旧房间", "type": "structural",
                       "parts": ["旧零件"], "why": "旧"}],
        }
        _m2.save_plan(_plan)
        # 把当前任务 id 设成另一个值
        st2 = mg.load_state() or {}
        st2["mode"] = "1"
        mg.save_state(st2)
        r6 = s._mode2_preflight_for_run(p2)
        check("残留旧编制（task_id 不匹配）仍拒绝", r6.get("ok") is False, r6.get("gate"))
    except Exception as _e:
        check("残留旧编制用例可执行", False, repr(_e))

    print("\n" + "=" * 70)
    print("PASS=%d FAIL=%d" % (pass_n, fail_n))
    print("=" * 70)
    return 0 if fail_n == 0 else 1


if __name__ == "__main__":
    sys.exit(main())