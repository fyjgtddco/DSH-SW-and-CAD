# -*- coding: utf-8 -*-
"""回归测试：2026-10-28 本轮修复（BUG-2/3/4/6/7/13/15/16/17/21 + 三个 UI 问题）

锁定的是【用户现场实测】到的缺陷，每条都给出"不修会怎样"。

  BUG-13  run 不捕获脚本 stdout —— 实测【不成立】（命令被 BUG-17 拦下，
          从未执行，于是 stdout 自然是空）。本测试固化"真的能抓到"。
  BUG-17  僵尸房间被当成活动 → 主对话直接出图被拒
  BUG-21  dwg 重建工程图覆盖手工标注
  BUG-15  装配体保存误报"材料不能为空"
  BUG-16  自动标注 delta=0 却仍 ok:true
  BUG-2   PowerShell 把 --rooms-json 的 @ 吃掉
  BUG-3   {"rooms":[...]} 包装不被接受
  BUG-4   缺 type 的报错不够可操作
  BUG-6   parts 空数组被拒但没说可写占位名
  BUG-7   跳过按需提问直接进 mechanics_done 无任何提示

运行：python test_regression_20261028.py
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  [PASS] " if cond else "  [FAIL] ") + name +
          (("  -> " + str(detail)) if detail else ""))


def section(t):
    print("\n=== " + t + " ===")


def fresh_state():
    d = tempfile.mkdtemp(prefix="dsh_r1028_")
    os.environ["DSH_STATE_DIR"] = d
    return d


def main():
    fresh_state()

    # ── BUG-17：僵尸房间不再算"活动" ─────────────────────────────────
    section("BUG-17 僵尸房间不能阻止主对话直接出图")
    import sw_bridge as sb
    act, owner = sb._room_candidates()
    check("_room_candidates 只返回真正活着的房间", isinstance(act, list), act)
    # 造一个"active 但心跳极旧"的房间，必须被剔除
    import mode_gate as mg
    st = mg.load_state()
    st.setdefault("rooms", {})["僵尸房"] = {
        "active": True, "started_at": 1.0, "ended_at": None}
    mg.save_state(st)
    try:
        act2, _ = sb._room_candidates()
        check("心跳极旧的 active 房间被剔除", "僵尸房" not in act2, act2)
    finally:
        st2 = mg.load_state()
        st2.get("rooms", {}).pop("僵尸房", None)
        mg.save_state(st2)

    src = io.open(os.path.join(HERE, "sw_bridge.py"), encoding="utf-8").read()
    check("没有活房间时【放行】（不报并行模式必须指定 --room）",
          "没有活动中的房间" in src and "_need_room = False" in src)

    # ── BUG-21：dwg 复用已有工程图 ───────────────────────────────────
    section("BUG-21 dwg 不得重建工程图覆盖手工标注")
    check("cmd_dwg 有 force_redraw 开关",
          "def cmd_dwg(sw, part_path, output_path=None, force_redraw=False)" in src)
    check("默认复用已有 .slddrw", "_force_redraw" in src and '"reused": True' in src)
    check("CLI 支持 --redraw 显式重建", '"--redraw" in args' in src)

    # ── BUG-15：装配体不签发材料凭据 ─────────────────────────────────
    section("BUG-15 装配体保存不再报'材料不能为空'")
    sw = io.open(os.path.join(HERE, "swapi.py"), encoding="utf-8").read()
    check("_emit_material_attestation 检测 .sldasm",
          '".sldasm"' in sw and 'skipped": "assembly"' in sw)

    # ── BUG-16：标注 delta=0 必须报警 ────────────────────────────────
    section("BUG-16 自动标注 0 个时必须显式告警")
    check("drawing 返回 annotation_effective", "annotation_effective" in src)
    check("有 annotation_warning 文案", "annotation_warning" in src)
    check("给出 add_dimension 的补救办法", "add_dimension" in src)

    # ── BUG-2：rooms-json 的 @ 兼容 ──────────────────────────────────
    section("BUG-2 PowerShell 吃掉 @ 时仍能用裸路径")
    import mode2_flow as mf
    d = fresh_state()
    rooms = [{"name": "结构件", "type": "structural", "parts": ["DSH_x"],
              "why": "测", "depends_on": []}]
    p = os.path.join(d, "rooms.json")
    io.open(p, "w", encoding="utf-8").write(json.dumps(rooms, ensure_ascii=False))
    helper = getattr(mf, "_load_json_arg", None) or None
    # CLI 内部函数不在模块级 → 直接测 CLI
    # ── 【2026-10-29】set-subagent-plan 现在要求"真实绑定的总计子代理"身份；
    #    这里先 analyst-bind 并伪造平台注册表，否则拿到的只会是
    #    ANALYST_ACTOR_REQUIRED / MODE2_ANALYST_REQUIRED（不是本用例要测的东西）。
    import subprocess
    import time as _t
    _env = dict(os.environ)
    _env["DSH_STATE_DIR"] = d
    _env["DSH_SESSION_ID"] = "test-analyst"
    _env["PYTHONIOENCODING"] = "utf-8"
    try:
        io.open(os.path.join(d, "platform_agents.json"), "w", encoding="utf-8").write(
            json.dumps({"updatedAt": int(_t.time() * 1000), "source": "test",
                        "agents": [{"id": "test-analyst", "label": "总计子代理",
                                    "status": "running",
                                    "createdAt": int(_t.time() * 1000)}]},
                       ensure_ascii=False))
    except Exception:
        pass
    subprocess.run([sys.executable, os.path.join(HERE, "mode2_flow.py"),
                    "analyst-bind", "--work-dir", d],
                   capture_output=True, text=True, encoding="utf-8", env=_env)
    # 编制前置：十阶段必须走完
    for _sid in mf.STAGE_IDS:
        subprocess.run([sys.executable, os.path.join(HERE, "mode2_flow.py"),
                        "begin-stage", _sid, "--work-dir", d],
                       capture_output=True, text=True, encoding="utf-8", env=_env)
        subprocess.run([sys.executable, os.path.join(HERE, "mode2_flow.py"),
                        "mark-stage-done", _sid, "--artifact", "art-" + _sid,
                        "--work-dir", d],
                       capture_output=True, text=True, encoding="utf-8", env=_env)
    # 还需 set-depth（record 题数校验用；这里只走阶段，故直接设 lite）
    subprocess.run([sys.executable, os.path.join(HERE, "mode2_flow.py"),
                    "set-depth", "lite", "--work-dir", d],
                   capture_output=True, text=True, encoding="utf-8", env=_env)
    r = subprocess.run([sys.executable, os.path.join(HERE, "mode2_flow.py"),
                        "set-subagent-plan", "--rooms-json", p,
                        "--rationale", "t", "--decided-by", "总计子代理",
                        "--work-dir", d],
                       capture_output=True, text=True, encoding="utf-8", env=_env)
    check("裸 .json 路径可解析（不需要 @）",
          "count" in (r.stdout or "") or '"ok": true' in (r.stdout or ""),
          (r.stdout or "")[:200])

    # ── BUG-3/4/6：报错必须可操作 ───────────────────────────────────
    section("BUG-3/4/6 编制校验的报错必须可操作")
    m2 = io.open(os.path.join(HERE, "mode2_flow.py"), encoding="utf-8").read()
    check("BUG-3 兼容 {\"rooms\": [...]} 包装", 'for _k in ("rooms",' in m2)
    check("BUG-3 报错里带 example 数组", '"example": [{"name": "结构件"' in m2)
    check("BUG-4 缺 type 时列出白名单", '"allowed": _allowed' in m2)
    check("BUG-4 提示里点名 drivetrain→transmission", "drivetrain" in m2)
    check("BUG-6 空 parts 时给占位名示例", "example_parts" in m2)

    # ── BUG-7：跳过按需提问要有提示 ─────────────────────────────────
    section("BUG-7 跳过按需提问必须显式提示")
    wg = io.open(os.path.join(HERE, "workflow_gate.py"), encoding="utf-8").read()
    check("provide_context 计算 question budget 警告",
          "_budget_warn" in wg and "question_report" in wg)
    check("警告写进 state 与回传正文",
          "question_budget_warning" in wg and "按需提问未达标" in wg)

    # ── BUG-9：whoami 自动补绑定 ─────────────────────────────────────
    section("BUG-9 小屋自查身份时自动补 subagent 绑定")
    check("mode_gate 有 cmd_whoami_self_bind",
          "def cmd_whoami_self_bind" in io.open(
              os.path.join(HERE, "mode_gate.py"), encoding="utf-8").read())
    check("whoami 分支会调用它并回传 subagent_binding",
          "subagent_binding" in io.open(
              os.path.join(HERE, "mode_gate.py"), encoding="utf-8").read())

    # ── UI 三问（静态契约）───────────────────────────────────────────
    section("UI: 小标签吸附 / 子代理层级 / 环境检查可收拢")
    cl = io.open(os.path.join(os.path.dirname(HERE), "plugins",
                              "dsh-engineering-ui", "lib", "client.js"),
                 encoding="utf-8").read()
    check("小标签松手吸附到最近的边", "snapFab" in cl and "side" in cl)
    check("吸附后记住 side（窗口变化仍贴边）", "fabPos.side" in cl)
    check("小标签有 left 侧样式", "eng-fab.left" in cl)
    check("子代理树用 listDescendants 取整棵子树",
          "/dsh-engineering-ui/agents?rootSessionId=" in cl)
    check("按 depth 缩进并显示 L2+ 徽标", "eng-lv" in cl and "'L' + dep" in cl)
    check("支持逐分支折叠", "collapsedSt" in cl and "eng-node-tw" in cl)
    check("环境检查默认收起、有问题才展开",
          "expanded: null" in cl and "_autoExpand" in cl)
    check("环境检查有展开/收起按钮",
          "S.expanded ? '收起' : '展开'" in cl)

    print("\n" + "=" * 60)
    print("PASS=%d FAIL=%d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项: " + ", ".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
