#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""【A2 / B3 回归测试】等待原语与停滞判据。

对应缺陷（01-缺陷清单与日志定位.md）：

  A2（P0·◆）轮询风暴
      · 一次会话 477 次 `sleep(30)`，累计 14,554 s = 4 h 02 m，占墙钟 66%；
      · 同一会话 `wait-rooms` 用了 0 次、`sw-wait` 用了 0 次；
      · 连续 44 次、34 次逐字相同的轮询命令。
      修复判据：room-board 必须支持进程内阻塞（--wait），
               且返回理由分三态（state_changed / no_alive_rooms / timeout）。

  B3（P1·◆）停滞判据只看 progress_age → 两次误杀
      · :4076 "idle plat 0.6 safe True prog 9519.6"（158 分钟）→ 判停滞杀掉；
      · 19d94e45 那次父代理自己写 "SW CPU is growing — script may be genuinely
        running"，**然后还是杀了**。
      修复判据：① 看板必须给出 activity_age_sec（心跳年龄）作为第二条证据；
               ② 正在持锁（正在用 SW）的房间不得被判停滞；
               ③ 新增 room-lease：房间里一条阻塞命令持续刷心跳，
                  让"真的在长时间建模"和"卡死"能被区分开。

用法：
    python test_regression_a2_b3_wait_stall.py
退出码 0 = 全部通过。
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

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


def _env(state_dir):
    env = dict(os.environ)
    env["DSH_STATE_DIR"] = state_dir
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env.pop("DSH_BOARD_NO_PROGRESS_SEC", None)
    return env


def _run(args, state_dir, timeout=180):
    p = subprocess.run([PY, os.path.join(HERE, "mode_gate.py")] + args,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=_env(state_dir), timeout=timeout)
    out = (p.stdout or "").strip()
    try:
        return json.loads(out), p
    except Exception:
        return {"_raw": out, "_err": (p.stderr or "")[-1500:]}, p


def main():
    state_dir = tempfile.mkdtemp(prefix="dsh-a2b3-")
    try:
        _run(["declare", "2"], state_dir)
        _run(["room-start", "甲"], state_dir)

        # ── A2：room-board --wait 阻塞模式 ────────────────────────────────
        print("\n=== A2 · room-board --wait 进程内阻塞 ===")
        t0 = time.time()
        r, p = _run(["room-board", "--wait", "3"], state_dir, timeout=120)
        elapsed = time.time() - t0
        check("--wait 被接受（不是 UNKNOWN_FLAG）", r.get("gate") != "UNKNOWN_FLAG", r)
        check("返回体带 returned_because", "returned_because" in r, r)
        check("确实阻塞了约 3 秒（>=2.5s）", elapsed >= 2.5, round(elapsed, 2))
        check("没阻塞过头（<15s）", elapsed < 15, round(elapsed, 2))
        check("超时返回理由正确",
              r.get("returned_because") in ("timeout", "state_changed",
                                            "no_alive_rooms"),
              r.get("returned_because"))
        if r.get("returned_because") == "timeout":
            check("超时返回带 wait_note（提示不要 sleep 重试）",
                  "不要" in str(r.get("wait_note") or ""), r.get("wait_note"))

        print("\n=== A2 · 无在世房间时立即返回 no_alive_rooms ===")
        empty_dir = tempfile.mkdtemp(prefix="dsh-a2e-")
        try:
            _run(["declare", "2"], empty_dir)
            t1 = time.time()
            r2, _ = _run(["room-board", "--wait", "20"], empty_dir, timeout=120)
            el2 = time.time() - t1
            check("无房间时提前返回（<10s）", el2 < 10, round(el2, 2))
            check("理由为 no_alive_rooms", r2.get("returned_because") == "no_alive_rooms",
                  r2.get("returned_because"))
        finally:
            shutil.rmtree(empty_dir, ignore_errors=True)

        # ── B3：看板给出 activity_age_sec 与 holds_sw ────────────────────
        print("\n=== B3 · 看板必须有第二条证据 activity_age_sec ===")
        b, _ = _run(["room-board", "--room", "甲", "--json"], state_dir)
        row = (b.get("rooms") or [{}])[0]
        check("room 行含 activity_age_sec 字段", "activity_age_sec" in row, sorted(row.keys()))
        check("room 行含 holds_sw 字段", "holds_sw" in row, sorted(row.keys()))
        # 【监控器接入】room-start 不再预取 SW 使用权（使用权按命令发放），
        #   所以刚登记完的房间 holds_sw 必然是 False。原断言"正持锁=True"
        #   测的是已删除的房间级独占锁，留着就会永久红。
        check("room-start 后不再预取使用权（holds_sw=False）",
              row.get("holds_sw") is False, row.get("holds_sw"))

        # ── B3：room-lease 真的在续心跳 ───────────────────────────────────
        print("\n=== B3 · room-lease 持续刷新心跳 ===")
        p3 = subprocess.run(
            [PY, os.path.join(HERE, "mode_gate.py"), "room-lease", "甲",
             "--heartbeat", "5", "--ttl", "6"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=_env(state_dir), timeout=120)
        try:
            lease = json.loads((p3.stdout or "").strip())
        except Exception:
            lease = {"_raw": p3.stdout, "_err": p3.stderr[-800:]}
        check("room-lease 返回 ok", lease.get("ok") is True, lease)
        check("至少刷了 2 次心跳", int(lease.get("beats") or 0) >= 2, lease.get("beats"))
        # 租约结束后心跳应该很新（< 阈值），看板据此判"没停滞"
        b2, _ = _run(["room-board", "--room", "甲", "--json"], state_dir)
        row2 = (b2.get("rooms") or [{}])[0]
        aa = row2.get("activity_age_sec")
        check("activity_age_sec 已被刷新为较小值（<120s）",
              aa is not None and float(aa) < 120, aa)

        # ── 停滞判据仍然有效：人为把阈值调到 0 秒 ────────────────────────
        print("\n=== B3 · 停滞判据未被削弱（阈值极小时仍能判出）===")
        stall_dir = tempfile.mkdtemp(prefix="dsh-b3s-")
        try:
            env = _env(stall_dir)
            # 注意：阈值必须 > 0（_board_no_progress_sec 对非正值回落默认 1800），
            #   这里用 1 秒，等价于"任何没动静都算停滞"。
            env["DSH_BOARD_NO_PROGRESS_SEC"] = "1"
            subprocess.run([PY, os.path.join(HERE, "mode_gate.py"), "declare", "2"],
                           capture_output=True, text=True, env=env, timeout=120)
            subprocess.run([PY, os.path.join(HERE, "mode_gate.py"), "room-start", "乙"],
                           capture_output=True, text=True, env=env, timeout=120)
            # 房间不持锁 = 没有在途 SW 命令。监控器接管后 holds_sw 直接由
            #   sw_lock 镜像（source=sw_monitor）决定，未知来源一律视为不持锁，
            #   所以这里【不需要】再调一条释放命令（sw-release 现在要连监控器，
            #   在隔离环境里必然失败）。
            time.sleep(2.5)
            bp = subprocess.run(
                [PY, os.path.join(HERE, "mode_gate.py"), "room-board", "--room", "乙",
                 "--json"], capture_output=True, text=True, encoding="utf-8",
                errors="replace", env=env, timeout=120)
            rb = json.loads((bp.stdout or "{}").strip() or "{}")
            rb_row = (rb.get("rooms") or [{}])[0]
            check("阈值=0 且不持锁时能判出停滞",
                  rb_row.get("progress_stalled") is True, rb_row.get("progress_stalled"))
            check("判停滞时 safe_to_restart 翻为 True",
                  rb_row.get("safe_to_restart") is True, rb_row.get("safe_to_restart"))
        finally:
            shutil.rmtree(stall_dir, ignore_errors=True)

    finally:
        shutil.rmtree(state_dir, ignore_errors=True)

    total = len(_checks)
    passed = sum(1 for c in _checks if c["ok"])
    print("\n=== 汇总：%d/%d 通过 ===" % (passed, total))
    if passed != total:
        for c in _checks:
            if not c["ok"]:
                print("  ✗", c["name"], "->", repr(c["detail"])[:300])
        return 1
    print("A2/B3 回归：全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
