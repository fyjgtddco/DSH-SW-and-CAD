#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""【B8 回归测试】房间心跳不能只靠提示词约定；reset 必须归档而非删除。

对应缺陷（01-缺陷清单与日志定位.md · B8，P2）：

  现场（2026-11-07 会话）：
    · 总装 #2（`0b161dc4`）全程 **0 次 room-report**；
    · `:4098` `"history_preserved": false, "fresh_reason": "auto_fresh_after_fail"`；
    · `总装与验证间.report.json` 最早一条是收尾小屋 18:24:46 ——
      **之前 159 分钟的进度记录全被删掉**，事后无从复盘。

判据：
  ① ensure_room_heartbeat()：静默超阈值才补跳，且 via=auto_heartbeat（可与
     小屋主动跳的心跳区分），未超阈值时不重复刷；
  ② room-board 在被读取时，会给"静默太久且仍 active"的房间补一次心跳；
  ③ rooms-reset 在删除之前，把 reports/ heartbeats/ 与状态文件复制到
     <state>/archive/<时间戳>/，返回体带 archived_to / archived；
  ④ 归档失败 / 无文件时不崩（reset 是应急手段，不能因归档挂掉）。

用法：
    python test_regression_b8_heartbeat_archive.py
退出码 0 = 全部通过。
"""
import io
import json
import os
import shutil
import sys
import tempfile
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
_checks = []


def check(name, ok, detail=None):
    _checks.append({"name": name, "ok": bool(ok), "detail": detail})
    print(("  [PASS] " if ok else "  [FAIL] ") + name)
    if not ok and detail is not None:
        print("         detail: %r" % (detail,))
    return bool(ok)


def main():
    state = tempfile.mkdtemp(prefix="dsh-b8-")
    os.environ["DSH_STATE_DIR"] = state
    os.environ["PYTHONIOENCODING"] = "utf-8"
    os.environ["PYTHONUTF8"] = "1"
    sys.path.insert(0, HERE)
    try:
        import mode_gate as mg

        mg.set_mode("2") if hasattr(mg, "set_mode") else None
        st = mg.load_state()
        st["mode"] = "2"
        mg.save_state(st)
        mg.cmd_room_start("甲")

        print("=== ① ensure_room_heartbeat 静默超阈值才补跳 ===")
        hb = mg._heartbeat_path("甲")
        r1 = mg.ensure_room_heartbeat("甲", max_silence=600)
        check("首次调用会补跳", r1.get("beat") is True, r1)
        with io.open(hb, encoding="utf-8") as f:
            d1 = json.load(f)
        check("补跳标记 via=auto_heartbeat 或 report",
              d1.get("via") in ("auto_heartbeat", "report"), d1.get("via"))
        r2 = mg.ensure_room_heartbeat("甲", max_silence=600)
        check("刚跳完不会重复补", r2.get("beat") is False, r2)

        print("\n=== ①-b 人为把心跳改旧 → 会再次补跳 ===")
        old = time.time() - 3600
        os.utime(hb, (old, old))
        r3 = mg.ensure_room_heartbeat("甲", max_silence=600)
        check("静默超阈值会补跳", r3.get("beat") is True, r3)
        check("回报了上次静默时长", (r3.get("age_sec") or 0) > 600, r3.get("age_sec"))

        print("\n=== ② room-board 读取时自动补心跳 ===")
        os.utime(hb, (old, old))
        b = mg.cmd_room_board(room="甲", as_json=True)
        check("room-board 正常返回", b.get("ok") is True, b.get("error"))
        age = time.time() - os.path.getmtime(hb)
        check("心跳已被刷新（age<120s）", age < 120, round(age, 1))
        row = (b.get("rooms") or [{}])[0]
        # 【B3/B8 冲突后的正确口径】看板代补心跳【不算真实活动】——
        #   activity_age_sec 应为 None（"不知道上次真干活是什么时候"），
        #   而不是 0。否则 B3 的停滞判定会被永久喂饱（实测过）。
        # 真实活动由 room-lease / room-report / 小屋自跳心跳更新 last_real_ts。
        _aa = row.get("activity_age_sec")
        check("代补心跳不得伪造 activity_age（应为 None）", _aa is None, _aa)

        print("\n=== ③ rooms-reset 先归档再删除 ===")
        mg.cmd_room_report("甲", "modeling", "B8 归档回归")
        rep = mg._report_path("甲")
        check("报告文件已生成", os.path.exists(rep), rep)
        res = mg.cmd_rooms_reset()
        adir = res.get("archived_to")
        check("返回体带 archived_to", bool(adir), adir)
        check("归档目录真实存在", bool(adir) and os.path.isdir(adir), adir)
        check("返回体带 archived 计数", isinstance(res.get("archived"), dict),
              res.get("archived"))
        if adir and os.path.isdir(adir):
            found = []
            for root, _dirs, files in os.walk(adir):
                for fn in files:
                    found.append(os.path.join(root, fn))
            names = [os.path.basename(x) for x in found]
            check("归档里有报告文件", any("甲.report.json" in n for n in names), names)
            check("归档里有心跳文件", any(n.endswith(".heartbeat") for n in names), names)
            check("归档里有 mode_state.json", "mode_state.json" in names, names)
        check("原始报告已被清掉", not os.path.exists(rep), rep)
        check("rooms 已清空",
              not (mg.load_state().get("rooms") or {}), mg.load_state().get("rooms"))

        print("\n=== ④ 空目录 reset 不崩 ===")
        state2 = tempfile.mkdtemp(prefix="dsh-b8e-")
        try:
            os.environ["DSH_STATE_DIR"] = state2
            mg.reset_cache() if hasattr(mg, "reset_cache") else None
            import importlib
            importlib.reload(mg)
            r = mg.cmd_rooms_reset()
            check("空状态 reset 也 ok", r.get("ok") is True, r.get("error"))
        finally:
            os.environ["DSH_STATE_DIR"] = state
            shutil.rmtree(state2, ignore_errors=True)

    finally:
        shutil.rmtree(state, ignore_errors=True)

    total = len(_checks)
    passed = sum(1 for c in _checks if c["ok"])
    print("\n=== 汇总：%d/%d 通过 ===" % (passed, total))
    if passed != total:
        for c in _checks:
            if not c["ok"]:
                print("  ✗", c["name"], "->", repr(c["detail"])[:300])
        return 1
    print("B8 回归：全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
