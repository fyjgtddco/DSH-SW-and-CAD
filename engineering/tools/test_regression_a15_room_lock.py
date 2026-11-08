#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""【A15 回归测试】跨房间并发建模：房间级独占锁 + 阻断型 CONCURRENT_BUILD 守卫。

对应缺陷（01-缺陷清单与日志定位.md · A15 / ★P4，P0）：

  现场（2026-11-07 会话 session-dcb91b26，任务=比赛用轨迹竞速小车）：
    · 由 job/registered → job/settled 还原构建区间，跨房间求交得到 **8 对重叠**，
      最长 06:05:32–06:11:01（传感 329 s）× 06:05:38–06:09:14（行走 216 s）；
    · 13:59:59 时 4 个房间同时在世，与"并发硬上限=3"不符；
    · 240 次 room-board 读取里 waiting_sw 出现 **0 次** —— 锁只在命令之间生效。

  根因：sw-request 后 hold_mode 恒为 "command"，sw_bridge 每条命令结束即 sw-release，
        于是"排队"这个状态永远观察不到，只看到两个脚本同时在建。

三条回归判据（本文件逐条断言）：
  ① room-start 取锁、room-end 释放，hold_mode="room"；
  ② 甲持有房间级锁时，乙 sw-request 必须 acquired=False 并报出持有者；
  ③ room-board --json 必须给出 build_overlap_count / concurrent_build 明细。

另外回归两处"会让房间级锁静默失效"的暗坑：
  ④ _lock_owner_alive 不得在"SW 尚未启动"时判持有者死亡；
  ⑤ room-start 的 save_state 不得覆盖刚取得的锁（甲取锁后乙仍拿不到）。

用法：
    python test_regression_a15_room_lock.py
退出码 0 = 全部通过。
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

_checks = []


def check(name, ok, detail=None):
    _checks.append({"name": name, "ok": bool(ok), "detail": detail})
    print(("  [PASS] " if ok else "  [FAIL] ") + name)
    if not ok and detail is not None:
        print("         detail: %r" % (detail,))
    return bool(ok)


def _run(args, state_dir, extra_env=None):
    env = dict(os.environ)
    env["DSH_STATE_DIR"] = state_dir
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env.pop("DSH_SW_ROOM_HOLD", None)
    env.pop("DSH_CONCURRENT_BUILD_STRICT", None)
    if extra_env:
        env.update(extra_env)
    p = subprocess.run([PY, os.path.join(HERE, "mode_gate.py")] + args,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env, timeout=180)
    out = (p.stdout or "").strip()
    try:
        return json.loads(out), p
    except Exception:
        return {"_raw": out, "_err": (p.stderr or "")[-2000:]}, p


def main():
    state_dir = tempfile.mkdtemp(prefix="dsh-a15-")
    try:
        print("=== 准备隔离状态目录 ===")
        print("  DSH_STATE_DIR =", state_dir)
        d, _ = _run(["declare", "2"], state_dir)
        check("declare 2 生效", str(d.get("mode")) == "2", d)

        # ── ① room-start 取锁，hold_mode="room" ───────────────────────────
        print("\n=== ① room-start 取房间级锁（hold_mode=room）===")
        a, pa = _run(["room-start", "甲"], state_dir)
        check("甲 room-start ok", a.get("ok") is True, a)
        check("甲 取得 SW 房间级独占权",
              "已取得" in str(a.get("sw_room_lock") or ""), a.get("sw_room_lock"))
        check("甲 hold_mode == room", a.get("hold_mode") == "room", a.get("hold_mode"))

        # ── ② 乙必须排队（房间级互斥的核心判据）──────────────────────────
        print("\n=== ② 甲持锁期间乙必须排队 ===")
        b, pb = _run(["room-start", "乙"], state_dir)
        check("乙 room-start 仍可登记（房间表要能建起来）", b.get("ok") is True, b)
        check("乙 未取得独占权",
              "未能取得" in str(b.get("sw_room_lock") or ""), b.get("sw_room_lock"))
        q, pq = _run(["sw-request", "乙"], state_dir)
        check("乙 sw-request acquired=False", q.get("acquired") is False, q)
        check("乙 报出的持有者是甲", q.get("owner") == "甲", q.get("owner"))
        check("乙 队列位置 = 1", q.get("position") == 1, q.get("position"))

        # ── ⑤ room-start 的 save_state 不得覆盖刚取得的锁 ─────────────────
        print("\n=== ⑤ room-start 不得覆盖已取得的锁（回归：save_state 回冲）===")
        s, _ = _run(["sw-status"], state_dir)
        check("sw-status owner 仍是甲", s.get("owner") == "甲", s.get("owner"))
        check("sw-status hold_mode == room", s.get("hold_mode") == "room",
              s.get("hold_mode"))

        # ── ③ room-board 必须给出并发指标 ─────────────────────────────────
        print("\n=== ③ room-board 暴露 build_overlap_count ===")
        board, _ = _run(["room-board", "--json"], state_dir)
        summ = board.get("summary") or {}
        cb = board.get("concurrent_build") or {}
        check("summary.build_overlap_count 字段存在", "build_overlap_count" in summ, summ)
        check("summary.build_overlap_count 是整数",
              isinstance(summ.get("build_overlap_count"), int), summ.get("build_overlap_count"))
        check("concurrent_build 明细存在", isinstance(cb, dict) and "overlaps" in cb, cb)
        check("concurrent_build.hold_mode == room", cb.get("hold_mode") == "room", cb)

        # ── ④ SW 未启动不得判持有者死亡 ───────────────────────────────────
        print("\n=== ④ SW 尚未启动时不得判持有者死亡（回归：_lock_owner_alive）===")
        alive_py = (
            "import sys, json\n"
            "sys.path.insert(0, %r)\n"
            "import mode_gate as mg\n"
            "st = mg.load_state()\n"
            "lk = mg._sw_lock(st)\n"
            "print(json.dumps({\n"
            "  'owner': lk.get('owner'),\n"
            "  'seen': bool(lk.get('sw_seen_running')),\n"
            "  'alive': mg._lock_owner_alive(st, lk.get('owner')),\n"
            "  'reclaim': list(mg._reclaim_lock(st, 'test', requester='乙')),\n"
            "}, ensure_ascii=False))\n"
        ) % HERE
        env = dict(os.environ)
        env["DSH_STATE_DIR"] = state_dir
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        p = subprocess.run([PY, "-c", alive_py], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=env, timeout=120)
        try:
            info = json.loads((p.stdout or "").strip().splitlines()[-1])
        except Exception:
            info = {"_raw": p.stdout, "_err": p.stderr[-1000:]}
        check("持有者仍被判为存活", info.get("alive") is True, info)
        check("_reclaim_lock 未回收（reclaimed=False）",
              (info.get("reclaim") or [None])[0] is False, info)
        check("sw_seen_running 为 False（SW 确实没启动过）",
              info.get("seen") is False, info)

        # ── room-end 释放房间级锁 ─────────────────────────────────────────
        print("\n=== room-end 必须释放房间级锁 ===")
        e, pe = _run(["room-end", "甲", "--force"], state_dir)
        check("甲 room-end ok", e.get("ok") is True, e)
        check("room-end 回传 hold_mode=room", e.get("hold_mode") == "room",
              e.get("hold_mode"))
        after, _ = _run(["sw-status"], state_dir)
        check("room-end 后锁已释放（owner 不再是甲）",
              after.get("owner") != "甲", after.get("owner"))
        z, pz = _run(["sw-request", "乙"], state_dir)
        check("甲下线后乙能拿到锁", z.get("acquired") is True, z)

        # ── 阻断型守卫：严格模式下 room-start 直接拒绝 ────────────────────
        print("\n=== 阻断型 CONCURRENT_BUILD 守卫（DSH_CONCURRENT_BUILD_STRICT=1）===")
        strict_dir = tempfile.mkdtemp(prefix="dsh-a15s-")
        try:
            _run(["declare", "2"], strict_dir)
            _run(["room-start", "甲"], strict_dir)
            r2, _ = _run(["room-start", "乙"], strict_dir,
                         extra_env={"DSH_CONCURRENT_BUILD_STRICT": "1"})
            check("严格模式下乙被 CONCURRENT_BUILD 拒绝", r2.get("ok") is False, r2)
            check("gate == CONCURRENT_BUILD", r2.get("gate") == "CONCURRENT_BUILD", r2)
            check("列出在世房间", r2.get("active_rooms") == ["甲"], r2.get("active_rooms"))
            vp = os.path.join(strict_dir, "concurrent_build_violations.json")
            check("并发违规已留痕", os.path.exists(vp), vp)
            if os.path.exists(vp):
                with io.open(vp, encoding="utf-8") as f:
                    recs = (json.load(f) or {}).get("records") or []
                check("留痕里有房间名", any(r.get("room") == "乙" for r in recs), recs[-1:])
        finally:
            shutil.rmtree(strict_dir, ignore_errors=True)

    finally:
        shutil.rmtree(state_dir, ignore_errors=True)

    total = len(_checks)
    passed = sum(1 for c in _checks if c["ok"])
    print("\n=== 汇总：%d/%d 通过 ===" % (passed, total))
    if passed != total:
        print("失败项：")
        for c in _checks:
            if not c["ok"]:
                print("  ✗", c["name"], "->", repr(c["detail"])[:300])
        return 1
    print("A15 回归：全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
