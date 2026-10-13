# -*- coding: utf-8 -*-
"""回归测试：SW 健康看护的【进程存活探测】与幂等启动

★ 锁定的真实缺陷（现场实测发现）：
  `_sw_guard_alive()` 原本用 `os.kill(pid, 0)` 探测看护进程是否存活。
  在 Windows + Python 3.13 上，对一个【确实活着】的进程，该调用会抛
  `OSError(22, '参数错误', None, 87, None)` —— 而同一时刻
  `tasklist /FI "PID eq <pid>"` 与 `OpenProcess` 都证明它活着。

  即 os.kill(pid,0) 是【假阴性】，导致：
    · `already_running` 恒为 False；
    · **每次取锁都重复拉起一个新的看护进程**（现场已累积 3 个）；
    · 每个重复进程都会在超阈值时 taskkill SolidWorks。

  修复：改用 OpenProcess 拿句柄判定，失败退回 tasklist 文本匹配。

本测试确保该缺陷不会回归。

运行：python test_regression_sw_guard_lifecycle.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

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


def main():
    # ═══════════════════════════════════════════════════════════════
    section("1. ★ 进程存活探测必须是【真阳性】（锁死 os.kill 假阴性缺陷）")
    # 起一个真实长命进程
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                         creationflags=mg.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    try:
        time.sleep(1.0)
        check("★ _pid_alive(真实存活的 pid) 必须返回 True（os.kill 写法在此为 False）",
              mg._pid_alive(p.pid) is True, "pid=%s" % p.pid)

        # 对照：证明 os.kill(pid,0) 在本机确实不可靠（把它记录下来当证据）
        _oskill_ok = True
        try:
            os.kill(p.pid, 0)
        except Exception as _e:
            _oskill_ok = False
            print("     （现场证据：os.kill(pid,0) 对存活进程抛 %r，"
                  "而 _pid_alive 返回 True）" % (_e,))
        check("_pid_alive 不依赖 os.kill（本机 os.kill 不可靠=%s）" % (not _oskill_ok),
              mg._pid_alive(p.pid) is True)
    finally:
        try:
            p.kill()
        except Exception:
            pass

    # 不存在的 pid 必须返回 False
    check("_pid_alive(不存在的 pid) 返回 False", mg._pid_alive(999999) is False)
    check("_pid_alive(0) 返回 False", mg._pid_alive(0) is False)
    check("_pid_alive(负数) 返回 False", mg._pid_alive(-7) is False)
    check("_pid_alive(非数字) 返回 False", mg._pid_alive("abc") is False)
    check("_pid_alive(None) 返回 False", mg._pid_alive(None) is False)

    # ═══════════════════════════════════════════════════════════════
    section("2. ★ 看护启动必须【幂等】（否则每次取锁都多一个进程）")
    _real_pid_file = mg._SW_GUARD_PID_FILE
    _tmpd = tempfile.mkdtemp(prefix="guardtest_")
    try:
        # 把 pid 文件重定向到临时目录，避免污染真实状态
        mg._SW_GUARD_PID_FILE = os.path.join(_tmpd, "sw_guard.pid")

        check("没有 pid 文件时 _sw_guard_alive() 返回 None",
              mg._sw_guard_alive() is None)

        r1 = mg.ensure_sw_guard()
        check("首次 ensure_sw_guard 应真的启动（started=True）",
              r1.get("ok") and r1.get("started") is True, r1)
        _pid1 = r1.get("pid")
        check("返回了 pid", bool(_pid1), r1)

        time.sleep(2.5)   # 给进程起来的时间

        check("★ pid 文件已落盘", os.path.exists(mg._SW_GUARD_PID_FILE),
              mg._SW_GUARD_PID_FILE)
        check("★ _sw_guard_alive() 能认出它（修复前恒为 None）",
              mg._sw_guard_alive() == _pid1,
              "alive=%s expect=%s" % (mg._sw_guard_alive(), _pid1))

        r2 = mg.ensure_sw_guard()
        check("★ 第二次 ensure_sw_guard 必须 already_running=True（幂等）",
              r2.get("already_running") is True, r2)
        check("★ 第二次不得再启动新进程", r2.get("started") is False, r2)
        check("两次返回同一个 pid（没有泄漏进程）",
              r2.get("pid") == _pid1, "r1=%s r2=%s" % (_pid1, r2.get("pid")))

        r3 = mg.ensure_sw_guard()
        check("第三次仍幂等", r3.get("already_running") is True and
              r3.get("pid") == _pid1, r3)

        # 停止应真的能停
        rs = mg.cmd_sw_guard_stop()
        check("cmd_sw_guard_stop 报告 stopped=True", rs.get("stopped") is True, rs)
        time.sleep(1.0)
        check("停止后 _sw_guard_alive() 返回 None",
              mg._sw_guard_alive() is None)

        # 显式关闭开关
        os.environ["DSH_SW_GUARD_OFF"] = "1"
        try:
            r_off = mg.ensure_sw_guard()
            check("DSH_SW_GUARD_OFF=1 时跳过启动（且标记 skipped）",
                  r_off.get("skipped") is True and r_off.get("started") is False,
                  r_off)
        finally:
            os.environ.pop("DSH_SW_GUARD_OFF", None)
    finally:
        mg._SW_GUARD_PID_FILE = _real_pid_file
        shutil.rmtree(_tmpd, ignore_errors=True)

    # ═══════════════════════════════════════════════════════════════
    section("3. 状态查询接口")
    st = mg.cmd_sw_guard_status()
    check("sw-guard-status 返回 ok=True", st.get("ok") is True, st)
    check("含 running / pid / thresholds 三个关键字段",
          all(k in st for k in ("running", "pid", "thresholds")), list(st.keys()))
    check("阈值与需求一致（85 / 85 / 10 秒）",
          st["thresholds"] == {"cpu_limit": 85.0, "mem_limit": 85.0, "interval_sec": 10},
          st.get("thresholds"))

    # ═══════════════════════════════════════════════════════════════
    section("4. ★ sw_guard.py 不得因「SW 不在运行」而永久退出")
    # 现场缺陷：一次瞬时内存尖峰（95.77%）时 SW 并未运行，
    #   看护却退出 → 真正开始建模时已无守护。
    # 这里用极小阈值强制每 tick 都超阈值，验证它会持续运行。
    _sg = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sw_guard.py")
    if os.path.exists(_sg):
        _state = os.path.join(_tmpd2 := tempfile.mkdtemp(prefix="guardst_"), "st.json")
        try:
            out = subprocess.run(
                [sys.executable, _sg, "run", "--interval", "1",
                 "--cpu", "0.1", "--mem", "0.1", "--max-ticks", "3",
                 "--no-kill", "--log", os.path.join(_tmpd2, "g.log")],
                capture_output=True, text=True, encoding="utf-8", timeout=90)
            # 告警行会先打印到 stdout，JSON 摘要在最后 —— 因此不能直接 loads 整个
            # stdout，要定位到最后一个 JSON 对象（从最后一个以 "{" 开头的行开始）。
            _raw = out.stdout or ""
            _j = {}
            _idx = _raw.find("\n{")
            if _idx < 0 and _raw.lstrip().startswith("{"):
                _idx = _raw.find("{") - 1
            try:
                _j = json.loads(_raw[_idx + 1:] if _idx >= 0 else _raw)
            except Exception as _e:
                _j = {"_parse_error": repr(_e), "_raw_tail": _raw[-300:]}
            check("★ 连续超阈值时跑满 max_ticks（而非 1 tick 就退）",
                  _j.get("ticks") == 3, "ticks=%s raw=%s"
                  % (_j.get("ticks"), _j.get("_raw_tail", "")[:120]))
            check("告警次数与 tick 数一致（每次都报警）",
                  len(_j.get("alerts") or []) == 3, len(_j.get("alerts") or []))
            check("stopped_reason 标明是 max_ticks 而非误报成功",
                  _j.get("stopped_reason") == "max_ticks", _j.get("stopped_reason"))
        finally:
            shutil.rmtree(_tmpd2, ignore_errors=True)
    else:
        check("sw_guard.py 存在", False, _sg)

    # ═══════════════════════════════════════════════════════════════
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
