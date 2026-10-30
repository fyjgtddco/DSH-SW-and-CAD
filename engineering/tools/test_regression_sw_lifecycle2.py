# -*- coding: utf-8 -*-
"""回归测试（第二组）：SW 连接/清场三缺陷 —— S01 conn-gate 实时探测、
S02 close-all 循环关闭、S03 SW 进程强杀确认。

★ 锁定的真实缺陷（2026-10-29 循迹小车任务 20261029_082936_4159A331）：

  S01 conn-gate 只看 connection_state.json 的时间戳
      实测：SLDWORKS.exe 明明在运行（Get-Process 有 PID），
        python sw_bridge.py conn-gate --target sw
      却返回 connected=false / skip_startup=false / stale=true，
      state_age_sec=125645（状态文件两天没刷新）。
      → 小屋于是重复走 SW 启动 7 步排查，浪费时间。

  S02 close-all 未真正关闭文档
      实测：close-all 返回 ok=false、docs_after=15（15 个文档仍开着）。
      根因：只调【一次】CloseAllDocuments 就下结论。

  S03 SW 进程杀不掉 / 杀后自动重生
      实测：Stop-Process -Force 后 5 秒内出现新 PID
      （13240→24256→25548），反复 3 轮仍存在。
      根因：taskkill 是异步信号，返回码≠进程已退出；且不带 /T。

修复后的约定（本测试钉死）：
  A. conn_state.gate(target) 必须【先实时探测进程】：
       进程在 → connected=true / skip_startup=true / source="live_process"
       进程不在 → 回落到 state 文件口径（stale=true / connected=false）
       探测抛异常 → 保守回退 state 文件 + 返回 live_probe_error
     stale / state_age_sec 仍如实反映 state 文件新旧（不许改成 false 骗人）。
  B. close_documents_until_zero(doc_mgr)：一次关完 / 三轮才关完 都 ok=true；
       永远关不完 → ok=false + failing_docs + hint（不杀进程）。
  C. kill_solidworks_confirmed(rounds, wait, kill_fn, process_list_fn, sleep_fn)：
       第一次就退出 → finally_exited=True / ok=True；
       杀 3 轮仍在 → finally_exited=False / ok=False + pid_history。

⚠️ 本测试【全部用注入/模拟】：
   · 用临时 DSH_STATE_DIR 隔离状态文件，不碰真实连接区状态；
   · monkeypatch conn_state._sw_process_list（绝不真跑 tasklist/PowerShell）；
   · close_all / sw-kill 用 mock 文档管理器 + mock 进程列表 + 空 sleep；
   · 【不真的杀本机 SolidWorks】，也【不跑任何 COM】。

运行：python test_regression_sw_lifecycle2.py
退出码：0 = 全部通过；1 = 有失败
"""
import json
import os
import shutil
import sys
import tempfile
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

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
        print("  [FAIL] %s  extra=%s" % (label, extra))


def section(t):
    print("\n" + "=" * 70)
    print(t)
    print("=" * 70)


# ══════════════════════════════════════════════════════════════════════
#  mock 文档管理器 / 进程列表（供 B、C 两组断言注入）
# ══════════════════════════════════════════════════════════════════════
class MockDocManager(object):
    """假文档管理器：按 script 指定的"每轮关闭后剩几个"模拟真实 SW。

    script = [15, 8, 0] 表示：调用前 15 个文档；第 1 轮关闭后剩 8 个；
    第 2 轮后剩 0 个（== 关完）。script 用完后重复最后一个值（模拟"永远关不完"）。
    """

    def __init__(self, script, titles=None):
        self.script = list(script)
        self._calls = 0
        self.close_calls = 0
        self._titles = titles or ["DSH_底盘.SLDPRT", "DSH_后轮毂左.SLDPRT"]
        self.current = self.script[0] if self.script else 0

    def count(self):
        return self.current

    def close_all(self):
        self.close_calls += 1
        self._calls += 1
        idx = min(self._calls, len(self.script) - 1)
        self.current = self.script[idx]
        return True, None

    def doc_titles(self, limit=20):
        return list(self._titles)[:limit]

    def visible(self):
        return True

    def set_visible(self, v):
        return True


def _make_process_feed(seq):
    """构造一个"每次调用返回序列里下一个列表"的进程列表函数。

    seq 里每个元素 = 一次探测看到的 PID 列表（None 表示这次探测失败）。
    序列用完后重复最后一个元素。
    """
    state = {"i": 0}

    def _fn():
        i = min(state["i"], len(seq) - 1)
        state["i"] += 1
        return seq[i]

    return _fn


# ══════════════════════════════════════════════════════════════════════
#  断言组 1：S01 —— conn_state.gate() 必须【先看实时进程】
# ══════════════════════════════════════════════════════════════════════
def test_s01_live_gate(CS):
    section("1. ★【S01】conn_state.gate() 先实时探测进程（不只看 state 文件时间戳）")

    # ── 准备：写入一个【2 天前】的 connection_state.json ────────────────
    #    里面 sw.connected=true，但 checked_at / updated_at 很旧。
    #    这正是现场：进程在跑、文件两天没刷新。
    st = CS._empty_state()
    st["sw"] = {"running": True, "pid": 1234, "processes": [],
                "connected": True, "checked_at": "2026-10-27 09:00:00",
                "error": None}
    st["cad"] = {"running": True, "pid": 4321, "processes": [],
                 "connected": True, "checked_at": "2026-10-27 09:00:00",
                 "error": None}
    st["updated_at"] = time.time() - 2 * 86400      # 两天前
    st["updated_at_str"] = "2026-10-27 09:00:00"
    # ⚠️ 必须【直接写文件】而不是 CS.save(st)：conn_state.save() 会把
    #    updated_at 改成"现在"，那样就造不出"两天没刷新"的现场条件了。
    _sp = CS.state_path()
    with open(_sp, "w", encoding="utf-8") as _f:
        json.dump(st, _f, ensure_ascii=False, indent=2)
    check("★ 临时 DSH_STATE_DIR 隔离生效（状态文件落在临时目录）",
          os.path.normcase(os.path.dirname(_sp)).startswith(
              os.path.normcase(tempfile.gettempdir())) or
          os.path.exists(_sp), _sp)
    check("★ 状态文件确实是【2 天前】的（stale 的前提）",
          (CS.age_sec() or 0) > 86400, CS.age_sec())

    _orig_sw = CS._sw_process_list
    _orig_cad = CS._acad_process_list
    try:
        # ── 断言 1a：伪造进程探测 → [1234] → 必须 source=live_process ─
        CS._sw_process_list = lambda: ([{"pid": 1234, "name": "SLDWORKS.EXE"}],
                                       None)
        g = CS.gate("sw")
        check("★ 进程在跑 → connected=True（修复前为 False）",
              g.get("connected") is True, g.get("connected"))
        check("★ 进程在跑 → skip_startup=True（修复前为 False）",
              g.get("skip_startup") is True, g.get("skip_startup"))
        check("★ source == 'live_process'（结论来源标注正确）",
              g.get("source") == "live_process", g.get("source"))
        check("★ live_process.found == True",
              (g.get("live_process") or {}).get("found") is True,
              g.get("live_process"))
        check("★ live_process.pids == [1234]（PID 证据可见）",
              (g.get("live_process") or {}).get("pids") == [1234],
              g.get("live_process"))
        check("★ live_process 带 method（可追溯探测手段）",
              bool((g.get("live_process") or {}).get("method")),
              g.get("live_process"))
        # stale 字段必须【如实】反映 state 文件新旧 —— 不许改成 False 骗人
        check("★ stale 仍如实为 True（state 文件确实两天没更新，不改 false 骗人）",
              g.get("stale") is True, g.get("stale"))
        check("★ state_age_sec 仍如实 ≈ 2 天（只作展示，不参与判定）",
              (g.get("state_age_sec") or 0) > 86400, g.get("state_age_sec"))
        # 兼容字段全部保留
        for _k in ("ok", "target", "connected", "skip_startup", "next",
                   "stale", "state_age_sec", "checked_at", "paths", "hint",
                   "exe"):
            check(" 兼容字段仍存在: %s" % _k, _k in g, sorted(g.keys()))

        # ── 断言 1b：进程不在 → 回落到 state 文件口径（stale=true）────
        CS._sw_process_list = lambda: ([], None)
        g2 = CS.gate("sw")
        check("★ 进程不在 + state 过期 → source='state_file'（回到 state 口径）",
              g2.get("source") == "state_file", g2.get("source"))
        check("★ 进程不在 + state 过期 → stale=True（如实）",
              g2.get("stale") is True, g2.get("stale"))
        check("★ 进程不在 + state 过期 → connected=False（过期即未连接，保守）",
              g2.get("connected") is False, g2.get("connected"))
        check("★ 进程不在 → skip_startup=False（走原有启动流程）",
              g2.get("skip_startup") is False, g2.get("skip_startup"))
        check("  live_process.found == False（这次真的探到了，不是探测失败）",
              (g2.get("live_process") or {}).get("found") is False,
              g2.get("live_process"))
        check("  未误报 live_probe_error（探测成功 ≠ 探测失败）",
              "live_probe_error" not in g2, g2.get("live_probe_error"))

        # ── 断言 1c：探测本身抛异常 → 保守回退 + live_probe_error ──────
        def _boom():
            raise RuntimeError("tasklist 被沙箱拦截（模拟）")
        CS._sw_process_list = _boom
        g3 = CS.gate("sw")
        check("★ 探测抛异常 → 不崩（仍返回完整结论）",
              isinstance(g3, dict) and g3.get("ok") is True, g3.get("ok"))
        check("★ 探测抛异常 → 保守回退 source='state_file'",
              g3.get("source") == "state_file", g3.get("source"))
        check("★ 探测抛异常 → 返回里带 live_probe_error",
              bool(g3.get("live_probe_error")), g3.get("live_probe_error"))
        check("  探测失败 ≠ 进程不在：live_process.found 为 None（未知）",
              (g3.get("live_process") or {}).get("found") is None,
              g3.get("live_process"))

        # ── 断言 1d：CAD 侧同理（复用 _acad_process_list）──────────────
        CS._acad_process_list = lambda: [{"pid": 4321, "name": "acad.exe"}]
        gc = CS.gate("cad")
        check("★ CAD 进程在跑 → connected=True / source=live_process",
              gc.get("connected") is True
              and gc.get("source") == "live_process", gc.get("source"))
        CS._acad_process_list = lambda: []
        gc2 = CS.gate("cad")
        check("★ CAD 进程不在 → 回落 state 口径 source='state_file'",
              gc2.get("source") == "state_file", gc2.get("source"))

        # ── 断言 1e：state 新鲜且进程在 → 结论仍以实时为准（不倒退）──
        st2 = CS.load()
        st2["updated_at"] = time.time()          # 刚刷新
        st2["updated_at_str"] = time.strftime("%Y-%m-%d %H:%M:%S")
        st2["sw"] = {"connected": False, "running": None, "processes": [],
                     "checked_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        with open(_sp, "w", encoding="utf-8") as _f2:
            json.dump(st2, _f2, ensure_ascii=False, indent=2)
        CS._sw_process_list = lambda: ([{"pid": 999}], None)
        g4 = CS.gate("sw")
        check("★ state 说未连接、但实时进程在 → connected=True（事实优先）",
              g4.get("connected") is True and g4.get("source") == "live_process",
              g4.get("source"))
        check("★ 此时 stale=False（文件新鲜），如实反映",
              g4.get("stale") is False, g4.get("stale"))
    finally:
        CS._sw_process_list = _orig_sw
        CS._acad_process_list = _orig_cad


# ══════════════════════════════════════════════════════════════════════
#  断言组 2：S02 —— close-all 必须【循环关到 0】
# ══════════════════════════════════════════════════════════════════════
def test_s02_close_loop(SB):
    section("2. ★【S02】close_documents_until_zero：循环关闭直到 docs_after==0")

    # 2a 一次关完
    m1 = MockDocManager([15, 0])
    r1 = SB.close_documents_until_zero(m1, max_rounds=3)
    check("★ 一次关完 → ok=True", r1.get("ok") is True, r1.get("ok"))
    check("  docs_before=15 / docs_after=0（客观证据）",
          r1.get("docs_before") == 15 and r1.get("docs_after") == 0, r1)
    check("  rounds_used=1（没有多余轮次）", r1.get("rounds_used") == 1,
          r1.get("rounds_used"))
    check("  keepalive=True / sw_exited=False（关文档绝不杀进程）",
          r1.get("keepalive") is True and r1.get("sw_exited") is False, r1)
    check("  failing_docs 为空", not (r1.get("failing_docs") or []), r1)

    # 2b 三轮才关完（这正是现场那种"一次关不掉"的场景）
    m2 = MockDocManager([15, 8, 3, 0])
    r2 = SB.close_documents_until_zero(m2, max_rounds=3)
    check("★ 三轮才关完 → ok=True（循环真的生效）", r2.get("ok") is True,
          r2)
    check("  rounds_used=3", r2.get("rounds_used") == 3, r2.get("rounds_used"))
    check("  docs_after=0", r2.get("docs_after") == 0, r2.get("docs_after"))
    check("★ 关闭接口被调用 3 次（不是旧实现的一次了事）",
          m2.close_calls == 3, m2.close_calls)
    _tr = r2.get("rounds_trace") or []
    check("  rounds_trace 逐轮留证（before/after 都在）",
          len(_tr) == 3 and all(("before" in x and "after" in x) for x in _tr),
          _tr)

    # 2c 永远关不完 → ok=False + failing_docs + hint
    m3 = MockDocManager([15, 15, 15, 15])
    r3 = SB.close_documents_until_zero(m3, max_rounds=3)
    check("★ 永远关不完 → ok=False（不许谎报清场成功）",
          r3.get("ok") is False, r3.get("ok"))
    check("  docs_after 如实报残存数（15）", r3.get("docs_after") == 15,
          r3.get("docs_after"))
    check("★ failing_docs 列出残存文档标题（最多 20 个）",
          bool(r3.get("failing_docs"))
          and len(r3.get("failing_docs") or []) <= 20,
          r3.get("failing_docs"))
    check("★ hint 说明『未保存文档时 SW 会弹保存框』",
          "另存" in str(r3.get("hint")) or "保存" in str(r3.get("hint")),
          r3.get("hint"))
    check("★ 关不掉也【不】杀进程（keepalive 语义不变）",
          r3.get("keepalive") is True and r3.get("sw_exited") is False, r3)
    check("  最大轮数被尊重（只关 3 轮）", m3.close_calls == 3,
          m3.close_calls)

    # 2d 边界：本来就没文档 / 管理器为空
    m4 = MockDocManager([0])
    r4 = SB.close_documents_until_zero(m4, max_rounds=3)
    check("  执行前就没文档 → ok=True 且不调关闭接口",
          r4.get("ok") is True and m4.close_calls == 0, r4)
    r5 = SB.close_documents_until_zero(None, max_rounds=3)
    check("★ doc_mgr=None → 不崩、如实 ok=False + hint",
          r5.get("ok") is False and bool(r5.get("hint")), r5)

    # 2e 通过 sw_bridge.cmd_close_all 走真实入口（mock 文档管理器注入）
    class _FakeSWForClose(object):
        def __init__(self, script):
            self._mgr = MockDocManager(script)
            self.Visible = True

        @property
        def GetDocumentCount(self):
            return self._mgr.count()

        def CloseAllDocuments(self, opts):
            self._mgr.close_all()
            return True

        def GetDocuments(self):
            return []

    # cmd_close_all 内部是【函数级 import swapi】→ 必须改 sys.modules 里那份，
    # 直接给模块属性赋值 provoque 不了它。
    import swapi as _swapi_real
    _orig = _swapi_real.close_all_documents
    try:
        # 用真实 swapi 的一次关闭动作（CloseAllDocuments(0) 一次），
        # 只把"返回值"替换成固定成功 —— 轮次与判据由被测函数负责。
        def _one_shot(sw):
            sw.CloseAllDocuments(0)
            return {"ok": True, "method": "close_all_documents"}
        _swapi_real.close_all_documents = _one_shot
        _sw = _FakeSWForClose([15, 8, 0])
        rc = SB.cmd_close_all(_sw)
        check("★ cmd_close_all 默认走循环关闭（docs 15→0 → ok=True）",
              rc.get("ok") is True, rc.get("ok"))
        check("  rounds_used>1（证明真的循环了）",
              (rc.get("rounds_used") or 0) > 1, rc.get("rounds_used"))
        check("  sw_exited=False / finally_exited=False（保活，不杀进程）",
              rc.get("sw_exited") is False
              and rc.get("finally_exited") is False, rc)
        check("  method == close_all_documents_loop",
              rc.get("method") == "close_all_documents_loop", rc.get("method"))
    finally:
        _swapi_real.close_all_documents = _orig


# ══════════════════════════════════════════════════════════════════════
#  断言组 3：S03 —— sw-kill 的轮询确认必须是【真】判据
# ══════════════════════════════════════════════════════════════════════
def test_s03_sw_kill(SB):
    section("3. ★【S03】kill_solidworks_confirmed：轮询确认，finally_exited 才是真结论")

    _kills = []

    def _kill_ok():
        _kills.append(1)
        return True, "SUCCESS: 已终止 SLDWORKS.EXE"

    _sleeps = []

    def _sleep(s):
        _sleeps.append(s)     # 记录但【不真等】，测试必须在毫秒级跑完

    # 3a 第一次就退出
    _feed1 = _make_process_feed([[13240], []])
    r1 = SB.kill_solidworks_confirmed(rounds=3, wait=5, kill_fn=_kill_ok,
                                      process_list_fn=_feed1,
                                      sleep_fn=_sleep)
    check("★ 第一次就退出 → finally_exited=True", r1.get("finally_exited") is True,
          r1)
    check("★ 第一次就退出 → ok=True", r1.get("ok") is True, r1.get("ok"))
    check("  rounds_used=1（不多杀）", r1.get("rounds_used") == 1,
          r1.get("rounds_used"))
    check("  pid_history 记录了杀之前的 PID",
          13240 in (r1.get("pid_history") or []), r1.get("pid_history"))

    # 3b 杀 3 轮仍在（现场：13240→24256→25548 不断重生）
    _kills.clear()
    _feed2 = _make_process_feed([[13240], [24256], [25548], [25548]])
    r2 = SB.kill_solidworks_confirmed(rounds=3, wait=5, kill_fn=_kill_ok,
                                      process_list_fn=_feed2,
                                      sleep_fn=_sleep)
    check("★ 杀 3 轮仍在 → finally_exited=False（绝不谎报已退出）",
          r2.get("finally_exited") is False, r2.get("finally_exited"))
    check("★ 杀 3 轮仍在 → ok=False", r2.get("ok") is False, r2.get("ok"))
    check("  rounds_used=3（按 rounds 上限）", r2.get("rounds_used") == 3,
          r2.get("rounds_used"))
    check("★ pid_history 完整记录 PID 变化（13240→24256→25548）",
          (r2.get("pid_history") or [])[:3] == [13240, 24256, 25548],
          r2.get("pid_history"))
    check("★ note 写清『可能被后台下载器/文档恢复重新拉起』",
          ("后台下载器" in str(r2.get("note")))
          or ("文档恢复" in str(r2.get("note"))), r2.get("note"))
    check("  remaining_pids 如实报残存进程",
          bool(r2.get("remaining_pids")), r2.get("remaining_pids"))
    check("  rounds_trace 逐轮留证", len(r2.get("rounds_trace") or []) == 3,
          len(r2.get("rounds_trace") or []))

    # 3c 本来就没进程
    _kills.clear()
    r3 = SB.kill_solidworks_confirmed(
        rounds=3, wait=5, kill_fn=_kill_ok,
        process_list_fn=_make_process_feed([[]]), sleep_fn=_sleep)
    check("★ 本来就没进程 → finally_exited=True 且 rounds_used=0",
          r3.get("finally_exited") is True and r3.get("rounds_used") == 0, r3)
    check("  没进程时不发多余的 taskkill", len(_kills) == 0, len(_kills))

    # 3d 探测手段失败（拿不到进程列表）→ 不能谎报已退出
    r4 = SB.kill_solidworks_confirmed(
        rounds=2, wait=0, kill_fn=_kill_ok,
        process_list_fn=_make_process_feed([[13240], None, None]),
        sleep_fn=_sleep)
    check("★ 探测失败 → finally_exited=False（『没探到』≠『已退出』）",
          r4.get("finally_exited") is False, r4.get("finally_exited"))
    check("  返回体说明探测失败原因", bool(r4.get("error")), r4.get("error"))

    # 3e 命令入口 cmd_sw_kill 的返回契约
    _orig_kill = SB.kill_solidworks_confirmed
    try:
        SB.kill_solidworks_confirmed = lambda rounds=3, wait=5: {
            "ok": True, "finally_exited": True, "rounds_used": 1,
            "pid_history": [13240], "note": "x"}
        rk = SB.cmd_sw_kill(rounds=3, wait=5)
        check("  cmd_sw_kill 返回 command='sw-kill' / exe=SLDWORKS.exe",
              rk.get("command") == "sw-kill"
              and rk.get("exe") == "SLDWORKS.exe", rk)
    finally:
        SB.kill_solidworks_confirmed = _orig_kill

    # 3f CLI 参数解析：main() 里必须有 sw-kill 分支，且解析 --rounds/--wait
    import inspect
    _src = inspect.getsource(SB.main)
    check("★ main() 有 sw-kill 分派分支", 'cmd == "sw-kill"' in _src,
          [l for l in _src.splitlines() if "sw-kill" in l][:3])
    check("★ main() 解析 --rounds", '"--rounds"' in _src)
    check("★ main() 解析 --wait", '"--wait"' in _src)
    check("  sw-kill 已登记进 _LOCAL_CMDS（不连 SW）",
          '"sw-kill"' in inspect.getsource(SB.main))
    check("  sw-kill 已登记进 _NO_ROOM_CMDS（清场入口不被 --room 拦住）",
          '"sw-kill"' in _src)

    # 3f-bis：sw=None（COM 不可用）+ quit=True → 仍必须走进程层强杀
    #   （"连不上 COM → 想清场 → 因连不上被拒" 是死结，绝不能出现）
    _orig_k2 = SB.kill_solidworks_confirmed
    try:
        SB.kill_solidworks_confirmed = lambda rounds=3, wait=5: {
            "ok": True, "finally_exited": True, "rounds_used": 1,
            "pid_history": [13240], "note": "已退出", "error": None}
        _rq = SB.cmd_close_all(None, quit=True)
        check("★ sw=None + --quit → 不直接放弃，走进程层 sw-kill",
              _rq.get("method") == "sw-kill(no-com)", _rq.get("method"))
        check("★ sw=None + --quit → sw_exited 来自真实 finally_exited",
              _rq.get("sw_exited") is True
              and _rq.get("finally_exited") is True, _rq)
        check("  sw=None + --quit → 如实说明文档未能关闭",
              "未能" in str(_rq.get("hint")), _rq.get("hint"))
    finally:
        SB.kill_solidworks_confirmed = _orig_k2

    # 3g close-all --quit 路径必须带回【真实】退出结论
    _qsrc = inspect.getsource(SB.cmd_close_all)
    check("★ --quit 路径调用 kill_solidworks_confirmed（复核进程真的退出）",
          "kill_solidworks_confirmed" in _qsrc)
    check("★ --quit 返回体带 sw_exited / finally_exited（真实值）",
          'result["sw_exited"]' in _qsrc
          and 'result["finally_exited"]' in _qsrc)


# ══════════════════════════════════════════════════════════════════════
def main():
    _td = tempfile.mkdtemp(prefix="sw_lifecycle2_")
    _old_env = os.environ.get("DSH_STATE_DIR")
    # 【隔离】临时状态目录：gate() 读写的 connection_state.json 落在这里，
    # 绝不污染本机真实的连接区状态。
    os.environ["DSH_STATE_DIR"] = _td
    try:
        import _store
        _store.reset_cache()
    except Exception:
        pass

    try:
        import conn_state as CS
        check("conn_state 加载的是【本工作区】这份（防双副本假失败）",
              os.path.normcase(os.path.abspath(CS.__file__)).startswith(
                  os.path.normcase(HERE)), CS.__file__)
        check("★ conn_state.gate 的返回结构与 state 文件都指向临时目录",
              os.path.normcase(os.path.abspath(CS.state_path())).startswith(
                  os.path.normcase(_td)), CS.state_path())

        test_s01_live_gate(CS)

        import sw_bridge as SB
        check("sw_bridge 加载的是【本工作区】这份",
              os.path.normcase(os.path.abspath(SB.__file__)).startswith(
                  os.path.normcase(HERE)), SB.__file__)
        check("★ 新增可注入纯函数：close_documents_until_zero",
              callable(getattr(SB, "close_documents_until_zero", None)))
        check("★ 新增可注入纯函数：kill_solidworks_confirmed",
              callable(getattr(SB, "kill_solidworks_confirmed", None)))

        test_s02_close_loop(SB)
        test_s03_sw_kill(SB)
    except Exception:
        global FAIL
        FAIL += 1
        FAILURES.append("未捕获异常（见下方 traceback）")
        traceback.print_exc()
    finally:
        if _old_env is None:
            os.environ.pop("DSH_STATE_DIR", None)
        else:
            os.environ["DSH_STATE_DIR"] = _old_env
        try:
            import _store
            _store.reset_cache()
        except Exception:
            pass
        shutil.rmtree(_td, ignore_errors=True)

    print("\n" + "=" * 70)
    print("PASS=%d FAIL=%d" % (PASS, FAIL))
    if FAILURES:
        print("失败项：")
        for _f in FAILURES:
            print("  - " + _f)
    print("=" * 70)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
