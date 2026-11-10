#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""回归：room-end 两道新门（三段自检门 + 朝向声明门）真的会拦

对应《减速机设计过程回溯报告》第二版：
  7 P5   —— 无齿「齿轮」被宣布建模成功
  15.2 S3/S4、J1/J2/J6 —— 回执制 / 去重 / 朝向断言

本测试只验「门禁的判定逻辑与接线」，不拉 SW：
  F1  selfcheck 门：没有 self_check.json -> 拦（CONCURRENT 之外的 SELF_CHECK_REQUIRED）
  F2  selfcheck 门：记录 PASS -> 放行（非齿轮名零件）
  F3  selfcheck 门：记录 FAIL -> 拦，且回传失败段落
  F4  selfcheck 门：记录 verdict=INCOMPLETE（有段落被跳过）-> 拦
  F5  pose 门：总装房间零件缺 .pr.json -> 拦（PART_POSE_REQUIRED）
  F6  pose 门：.pr.json 齐全且与契约一致 -> 放行
  F7  pose 门：up_axis 与契约冲突 -> 拦
  F8  pose 门：非装配房间不受这条门约束
  F9  接线断言：room-end 源码里两门都被调用且 return
  F10~F14 【P5 收口】齿轮类零件必须带真齿形断言（缺 stages.gear 或未过 = 拦）；
      transmission 房间全部零件按齿轮类对待；齿轮名/房间类型判据来自
      _gear_class_part（不采信小屋自写的 expect.gear，防欺骗路径）

运行: python test_regression_room_gates_p5.py
"""
import json
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PASS = 0
FAIL = 0
FAILS = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [OK]   %s" % name)
    else:
        FAIL += 1
        FAILS.append("%s  %s" % (name, detail))
        print("  [FAIL] %s  %s" % (name, detail))


def section(t):
    print("\n" + "=" * 72)
    print(t)
    print("=" * 72)


def main():
    tmp = tempfile.mkdtemp(prefix="room_gates_")
    old_state = os.environ.get("DSH_STATE_DIR")
    old_cwd = os.getcwd()
    try:
        os.environ["DSH_STATE_DIR"] = tmp
        os.chdir(tmp)
        import mode_gate as mg
        import part_pose as pp

        work = os.path.join(tmp, "任务")
        os.makedirs(os.path.join(work, "reports"), exist_ok=True)
        os.makedirs(os.path.join(work, "parts"), exist_ok=True)
        # 【P5 收口】F1~F4 用非齿轮名零件测三段自检门本身；
        #   齿轮名零件的强制断言见新增的 F10~F13。
        part = os.path.join(work, "parts", "DSH_方块.SLDPRT")
        with open(part, "wb") as f:
            f.write(b"x")

        # 造一个「建模房间」+ 一条 artifact 登记
        st = {"work_dir": work,
              "rooms": {"结构件": {"active": True, "type": "structural"}}}
        with open(os.path.join(tmp, "mode_state.json"), "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False)
        with open(os.path.join(tmp, "artifacts_registry.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"结构件": [{"path": part}]}, f, ensure_ascii=False)

        section("F1~F4 三段自检门")

        # F1 没有 self_check.json
        r = mg._selfcheck_gate_for_room("结构件")
        check("F1 缺自检记录 -> 拦", isinstance(r, dict), r)
        check("F1 gate=SELF_CHECK_REQUIRED",
              (r or {}).get("gate") == "SELF_CHECK_REQUIRED", r)
        check("F1 指明零件",
              any("方块" in b for b in ((r or {}).get("blockers") or [])), r)

        sc_path = os.path.join(work, "reports", "DSH_方块.self_check.json")

        def _w(rec):
            with open(sc_path, "w", encoding="utf-8") as f:
                json.dump(rec, f, ensure_ascii=False)

        # F2 PASS -> 放行
        _w({"ok": True, "verdict": "PASS", "failed": [], "skipped": []})
        check("F2 PASS -> 放行", mg._selfcheck_gate_for_room("结构件") is None)

        # F3 FAIL -> 拦且回传段落
        _w({"ok": False, "verdict": "FAIL", "failed": ["volume"],
            "skipped": []})
        r = mg._selfcheck_gate_for_room("结构件")
        check("F3 FAIL -> 拦", isinstance(r, dict), r)
        check("F3 回传失败段落 volume",
              any("volume" in b for b in ((r or {}).get("blockers") or [])), r)

        # F4 INCOMPLETE（有段落被跳过）-> 拦
        _w({"ok": False, "verdict": "INCOMPLETE", "failed": [],
            "skipped": ["features"]})
        r = mg._selfcheck_gate_for_room("结构件")
        check("F4 跳过段落 -> 拦", isinstance(r, dict), r)
        check("F4 明确说「跳过不等于通过」",
              any("跳过" in b for b in ((r or {}).get("blockers") or [])), r)

        # 清理，进入 pose 门
        if os.path.isfile(sc_path):
            os.remove(sc_path)

        section("F5~F8 朝向声明门")

        # 总装房间 + 契约
        st2 = {"work_dir": work,
               "rooms": {"总装": {"active": True, "type": "assembly"}}}
        with open(os.path.join(tmp, "mode_state.json"), "w", encoding="utf-8") as f:
            json.dump(st2, f, ensure_ascii=False)
        with open(os.path.join(tmp, "artifacts_registry.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"总装": [{"path": part}]}, f, ensure_ascii=False)
        os.makedirs(os.path.join(work, "scripts"), exist_ok=True)
        with open(os.path.join(work, "scripts", "up_contract.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"world_up": "Z", "world_up_sign": 1,
                       "rooms": {"总装": {"up_axis": "Z", "up_sign": 1}}},
                      f, ensure_ascii=False)

        # F5 缺 pr.json -> 拦
        r = mg._pose_gate_for_room("总装")
        check("F5 缺 pr.json -> 拦", isinstance(r, dict), r)
        check("F5 gate=PART_POSE_REQUIRED",
              (r or {}).get("gate") == "PART_POSE_REQUIRED", r)
        check("F5 提到 J6",
              any("J6" in b for b in ((r or {}).get("blockers") or [])), r)

        # F6 齐全且一致 -> 放行
        pp.write_pr_record(part, up_axis="Z", up_sign=1, main_axis="Z",
                           base_plane="Top Plane")
        check("F6 pr.json 一致 -> 放行", mg._pose_gate_for_room("总装") is None)

        # F7 与契约冲突 -> 拦
        pp.write_pr_record(part, up_axis="Y", up_sign=1, main_axis="Y")
        r = mg._pose_gate_for_room("总装")
        check("F7 up_axis=Y 与契约 Z 冲突 -> 拦", isinstance(r, dict), r)
        check("F7 提到 J1/J2",
              any(("J1" in b or "J2" in b)
                  for b in ((r or {}).get("blockers") or [])), r)

        # F8 非装配房间不受约束
        st3 = dict(st2)
        st3["rooms"] = {"结构件": {"active": True, "type": "structural"}}
        with open(os.path.join(tmp, "mode_state.json"), "w", encoding="utf-8") as f:
            json.dump(st3, f, ensure_ascii=False)
        check("F8 非装配房间 -> 跳过", mg._pose_gate_for_room("结构件") is None)

        section("F10~F13 【P5 收口】齿轮类零件必须带真齿形断言")

        # 齿轮名零件（房间 type=structural）
        gear_part = os.path.join(work, "parts", "DSH_齿轮.SLDPRT")
        with open(gear_part, "wb") as f:
            f.write(b"x")
        st4 = {"work_dir": work,
               "rooms": {"结构件": {"active": True, "type": "structural"}}}
        with open(os.path.join(tmp, "mode_state.json"), "w", encoding="utf-8") as f:
            json.dump(st4, f, ensure_ascii=False)
        with open(os.path.join(tmp, "artifacts_registry.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"结构件": [{"path": gear_part}]}, f, ensure_ascii=False)
        gsc = os.path.join(work, "reports", "DSH_齿轮.self_check.json")

        def _wg(rec):
            with open(gsc, "w", encoding="utf-8") as f:
                json.dump(rec, f, ensure_ascii=False)

        # 直接断言 _gear_class_part 的判定
        check("F10 齿轮名零件被识别为齿轮类",
              mg._gear_class_part("DSH_齿轮.SLDPRT", "structural") is True)
        check("F10b 普通零件不算齿轮类",
              mg._gear_class_part("DSH_方块.SLDPRT", "structural") is False)
        check("F10c transmission 房间全部按齿轮类",
              mg._gear_class_part("DSH_圆盘.SLDPRT", "transmission") is True)

        # F11 齿轮名零件 PASS 但【没有 gear 段】→ 拦（欺骗路径被堵）
        _wg({"ok": True, "verdict": "PASS", "failed": [], "skipped": [],
             "stages": {"bbox": {"ok": True}, "volume": {"ok": True},
                        "features": {"ok": True}}})
        r = mg._selfcheck_gate_for_room("结构件")
        check("F11 齿轮名+缺 gear 段 -> 拦", isinstance(r, dict), r)
        check("F11 提到真齿形断言",
              any("齿轮类零件缺少通过的真齿形断言" in b
                  for b in ((r or {}).get("blockers") or [])), r)

        # F12 齿轮名 + gear 段 ok → 放行
        _wg({"ok": True, "verdict": "PASS", "failed": [], "skipped": [],
             "stages": {"bbox": {"ok": True}, "volume": {"ok": True},
                        "features": {"ok": True},
                        "gear": {"ok": True, "teeth": 24}}})
        check("F12 齿轮名+gear 段通过 -> 放行",
              mg._selfcheck_gate_for_room("结构件") is None)

        # F13 齿轮名 + gear 段 FAIL → 拦（并沿用"段未过"口径）
        _wg({"ok": False, "verdict": "FAIL", "failed": ["gear"], "skipped": [],
             "stages": {"bbox": {"ok": True}, "volume": {"ok": True},
                        "features": {"ok": True},
                        "gear": {"ok": False, "error": "体积偏差 25.3%"}}})
        r = mg._selfcheck_gate_for_room("结构件")
        check("F13 齿轮名+gear 段 FAIL -> 拦", isinstance(r, dict), r)

        # F14 transmission 房间：非齿轮名零件也强制 gear 段（防整房替换）
        st5 = {"work_dir": work,
               "rooms": {"传动机构": {"active": True, "type": "transmission"}}}
        with open(os.path.join(tmp, "mode_state.json"), "w", encoding="utf-8") as f:
            json.dump(st5, f, ensure_ascii=False)
        disk_part = os.path.join(work, "parts", "DSH_圆盘.SLDPRT")
        with open(disk_part, "wb") as f:
            f.write(b"x")
        with open(os.path.join(tmp, "artifacts_registry.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"传动机构": [{"path": disk_part}]}, f, ensure_ascii=False)
        dsc = os.path.join(work, "reports", "DSH_圆盘.self_check.json")
        with open(dsc, "w", encoding="utf-8") as f:
            json.dump({"ok": True, "verdict": "PASS", "failed": [],
                       "skipped": [],
                       "stages": {"bbox": {"ok": True},
                                  "volume": {"ok": True},
                                  "features": {"ok": True}}},
                      f, ensure_ascii=False)
        r = mg._selfcheck_gate_for_room("传动机构")
        check("F14 transmission 房间缺 gear 段 -> 拦", isinstance(r, dict), r)

        section("F9 接线断言（源码级）")
        src = open(os.path.join(HERE, "mode_gate.py"), encoding="utf-8").read()
        m = re.search(r"def cmd_room_end\(.*?(?=\ndef )", src, re.S)
        body = m.group(0) if m else ""
        check("F9 room-end 调用了 _selfcheck_gate_for_room",
              "_selfcheck_gate_for_room(" in body, "未接线")
        check("F9 room-end 调用了 _pose_gate_for_room",
              "_pose_gate_for_room(" in body, "未接线")
        check("F9 两道门的结果都被 return（真的拦住）",
              len(re.findall(r"return\s+_sc", body)) >= 1
              and len(re.findall(r"return\s+_pg", body)) >= 1, body[:200])

        section("汇总")
        print("  PASS=%d  FAIL=%d" % (PASS, FAIL))
        if FAILS:
            print("\n失败明细：")
            for x in FAILS:
                print("  - " + x)
        return 0 if FAIL == 0 else 1
    finally:
        os.chdir(old_cwd)
        if old_state is None:
            os.environ.pop("DSH_STATE_DIR", None)
        else:
            os.environ["DSH_STATE_DIR"] = old_state
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
