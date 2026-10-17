# -*- coding: utf-8 -*-
"""回归测试：【第6项】建模后"看图门"必须在 room-end 处强制生效

★ 锁定的真实缺陷（联调时发现）：
  1. `visual_check._default_out_dir()` 每次都生成**带时间戳的新目录**。
     门禁若自己拼一个固定路径去查证据，将**永远找不到** → 所有房间被误拦
     （比不拦更糟）。修法：按零件名扫 `<root>/visual_check/<零件名>_*` 取最新。
  2. `_find_gate()` 找不到时返回 None，而代码直接 `os.path.exists(None)`
     → TypeError，整个 room-end 崩掉。必须先判空。

本测试锁定：
  · 无看图证据 → 拦截（VISUAL_EVIDENCE_REQUIRED）
  · 非建模房间 → 不拦（避免误锁出图/总装）
  · 有合格证据 → 放行
  · 证据合格但截图被事后删除 → 重新拦下（防伪造）
  · 证据文件损坏 → 拦下
  · 没有登记零件 → 不拦（避免早期误锁）

运行：python test_regression_visual_gate_roomend.py
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import mode_gate as mg  # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []


def check(label, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] %s" % label)
    else:
        FAIL += 1
        FAILURES.append(label)
        print("  [FAIL] %s %s" % (label, extra))


def section(t):
    print("\n" + "=" * 70)
    print(t)
    print("=" * 70)


def _setup(work):
    """在临时目录里造：一个建模房间 + 一个已登记零件。"""
    part = os.path.join(work, "DSH_TestPart.SLDPRT")
    with open(part, "wb") as f:
        f.write(b"\x00" * 128)
    mg._save_artifact_registry({"结构件": [{"path": part, "name": "TestPart"}]})
    st = mg.load_state()
    st["rooms"] = dict(st.get("rooms") or {})
    st["rooms"]["结构件"] = {"type": "structural", "active": True}
    mg.save_state(st)
    return part


def _write_evidence(work, part, passed=True, with_shot=True, corrupt=False):
    """造一份看图证据（可选：合格 / 无截图 / 损坏）。"""
    stem = os.path.splitext(os.path.basename(part))[0]
    vd = os.path.join(work, "visual_check", "%s_20260101_000000" % stem)
    os.makedirs(vd, exist_ok=True)
    shots = {}
    if with_shot:
        png = os.path.join(vd, "%s__front.png" % stem)
        with open(png, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"0" * 900)
        shots["front"] = {"path": png}
    loop = {"rounds": [{"round": 1, "views": {"shots": shots}, "verdict": "PASS"}],
            "final_verdict": "PASS" if passed else "FAIL"}
    gf = os.path.join(vd, mg.os.path.join("visual_gate.json"))
    if corrupt:
        with open(gf, "w", encoding="utf-8") as f:
            f.write("{ not json")
    else:
        with open(gf, "w", encoding="utf-8") as f:
            json.dump({"ok": bool(passed), "passed": bool(passed),
                       "final_verdict": loop["final_verdict"], "loop": loop},
                      f, ensure_ascii=False)
    return vd, (shots.get("front") or {}).get("path")


def main():
    work = tempfile.mkdtemp(prefix="vgate_")
    cwd0 = os.getcwd()
    try:
        os.chdir(work)
        part = _setup(work)

        # ═══════════════════════════════════════════════════════════
        section("1. 无看图证据 → 必须拦截（这是「必须让模型看到」的强制点）")
        r = mg._visual_gate_for_room("结构件")
        check("★ 无证据时返回拦截体（非 None）", r is not None, r)
        check("gate == VISUAL_EVIDENCE_REQUIRED",
              (r or {}).get("gate") == "VISUAL_EVIDENCE_REQUIRED", r)
        check("ok=False（上游据此拒绝 room-end）", (r or {}).get("ok") is False, r)
        check("blockers 指明了是哪个零件",
              any("TestPart" in b for b in ((r or {}).get("blockers") or [])),
              (r or {}).get("blockers"))

        # ═══════════════════════════════════════════════════════════
        section("2. 非建模房间 → 不拦（避免误锁出图/总装）")
        st = mg.load_state()
        st["rooms"]["出图"] = {"type": "drafting"}
        st["rooms"]["总装"] = {"type": "assembly"}
        mg.save_state(st)
        check("出图房间放行", mg._visual_gate_for_room("出图") is None)
        check("总装房间放行", mg._visual_gate_for_room("总装") is None)

        # ═══════════════════════════════════════════════════════════
        section("3. 合格证据 → 放行")
        _vd, png = _write_evidence(work, part, passed=True, with_shot=True)
        check("★ 有合格看图证据时放行（返回 None）",
              mg._visual_gate_for_room("结构件") is None,
              mg._visual_gate_for_room("结构件"))
        check("截图文件确实存在（前置条件）", os.path.isfile(png), png)

        # ═══════════════════════════════════════════════════════════
        section("4. ★ 事后删除截图 → 必须重新拦下（防「假装看过图」）")
        os.remove(png)
        r4 = mg._visual_gate_for_room("结构件")
        check("★ 证据里引用的截图被删 → 重新拦截", r4 is not None, r4)
        check("blockers 说明是证据复核失败",
              any("复核失败" in b or "不存在" in b
                  for b in ((r4 or {}).get("blockers") or [])),
              (r4 or {}).get("blockers"))

        # ═══════════════════════════════════════════════════════════
        section("5. 闭环未 PASS → 拦下")
        shutil.rmtree(os.path.join(work, "visual_check"), ignore_errors=True)
        _write_evidence(work, part, passed=False, with_shot=True)
        r5 = mg._visual_gate_for_room("结构件")
        check("final_verdict=FAIL 时拦截", r5 is not None, r5)
        check("blockers 说明闭环未通过",
              any("未通过" in b for b in ((r5 or {}).get("blockers") or [])),
              (r5 or {}).get("blockers"))

        # ═══════════════════════════════════════════════════════════
        section("6. 证据文件损坏 → 拦下（不得当成通过）")
        shutil.rmtree(os.path.join(work, "visual_check"), ignore_errors=True)
        _write_evidence(work, part, corrupt=True)
        r6 = mg._visual_gate_for_room("结构件")
        check("★ 损坏的证据文件 → 拦截", r6 is not None, r6)
        check("blockers 说明文件损坏",
              any("损坏" in b for b in ((r6 or {}).get("blockers") or [])),
              (r6 or {}).get("blockers"))

        # ═══════════════════════════════════════════════════════════
        section("7. 无登记零件 → 不拦（避免早期阶段误锁）")
        mg._save_artifact_registry({})
        check("没有登记零件时放行（没有可看的对象）",
              mg._visual_gate_for_room("结构件") is None)

        # ═══════════════════════════════════════════════════════════
        section("8. ★ 时间戳目录不影响定位（锁定「固定路径拼接」缺陷）")
        # 造两个时间戳目录，只有较新的那份合格 → 必须取到较新的
        shutil.rmtree(os.path.join(work, "visual_check"), ignore_errors=True)
        mg._save_artifact_registry({"结构件": [{"path": part, "name": "TestPart"}]})
        stem = os.path.splitext(os.path.basename(part))[0]
        _old = os.path.join(work, "visual_check", "%s_20200101_000000" % stem)
        _new = os.path.join(work, "visual_check", "%s_20301231_235959" % stem)
        os.makedirs(_old, exist_ok=True)
        with open(os.path.join(_old, "visual_gate.json"), "w", encoding="utf-8") as f:
            json.dump({"passed": False, "final_verdict": "FAIL"}, f)
        os.makedirs(_new, exist_ok=True)
        _png = os.path.join(_new, "%s__front.png" % stem)
        with open(_png, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"1" * 900)
        with open(os.path.join(_new, "visual_gate.json"), "w", encoding="utf-8") as f:
            json.dump({"passed": True, "final_verdict": "PASS",
                       "loop": {"rounds": [{"round": 1,
                                            "views": {"shots": {"front": {"path": _png}}},
                                            "verdict": "PASS"}],
                                "final_verdict": "PASS"}}, f, ensure_ascii=False)
        # 把旧的设成更早的 mtime，新的更晚
        os.utime(os.path.join(_old, "visual_gate.json"), (1_000_000, 1_000_000))
        check("★ 多个时间戳目录时取最新的合格证据（放行）",
              mg._visual_gate_for_room("结构件") is None,
              mg._visual_gate_for_room("结构件"))
        # 把新证据改成 FAIL → 应拦截（证明确实读的是最新那份）
        with open(os.path.join(_new, "visual_gate.json"), "w", encoding="utf-8") as f:
            json.dump({"passed": False, "final_verdict": "FAIL"}, f)
        check("最新证据为 FAIL 时拦截（证明读的是最新那份）",
              mg._visual_gate_for_room("结构件") is not None)

    finally:
        os.chdir(cwd0)
        shutil.rmtree(work, ignore_errors=True)
        # 恢复 artifact registry，避免污染后续测试
        try:
            mg._save_artifact_registry({})
        except Exception:
            pass

    print("\n" + "=" * 70)
    print("汇总：PASS=%d  FAIL=%d" % (PASS, FAIL))
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  - " + f)
    print("=" * 70)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
