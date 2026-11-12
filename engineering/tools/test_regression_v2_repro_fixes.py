#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""回归：v2 复测报告（2026-11-09 22:16-22:50）逐条验收

对应《回归复测报告_v2_新版.md》里【仍未修好】的项：
  A. _parallel_mode_now 认不出 sequential/parallel（P0，E 模式严格闸失效）
  B. sw-release 不清 fair_start + room 模式参与公平轮转（P0，D 流程仍并发）
  C. 看护杀建模中的 SW（P0 环境杀手）+ GUARD_OFF 不停存量看护
  D. 子进程 MBSC 编码（GBK 崩溃）
  E. check-part / cleanup / self-check 抢 SW 锁
  F. 去重闸：变量路径 / 中文路径提取不到 + 台账不自动写
  G. new_part 关别人未命名文档
  H. 回执制（receipt id）缺失

运行: python test_regression_v2_repro_fixes.py
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
    tmp = tempfile.mkdtemp(prefix="v2_repro_")
    old_state = os.environ.get("DSH_STATE_DIR")
    old_cwd = os.getcwd()
    try:
        os.environ["DSH_STATE_DIR"] = tmp
        os.chdir(tmp)
        import mode_gate as mg
        import sw_bridge as sb
        import sw_guard as sg
        import swapi
        import mode2_flow as m2

        # ---------------- A. 并行模式识别 ----------------
        section("A. P0-1 _parallel_mode_now 必须认 sequential/parallel")
        check("A1 sequential -> E",
              mg._parallel_mode_now({"parallel_mode": "sequential"}) == "E",
              mg._parallel_mode_now({"parallel_mode": "sequential"}))
        check("A2 parallel -> D",
              mg._parallel_mode_now({"parallel_mode": "parallel"}) == "D")
        check("A3 字面 E 仍可用",
              mg._parallel_mode_now({"parallel_mode": "E"}) == "E")
        check("A4 嵌套 selection 也认",
              mg._parallel_mode_now({"selection": {"parallel_mode": "sequential"}}) == "E")
        check("A5 未知 -> None", mg._parallel_mode_now({}) is None)
        _s, _why = mg._build_exclusive_strict({"parallel_mode": "sequential"})
        check("A6 E -> 严格拒绝", _s is True and "E" in _why, (_s, _why))
        _s2, _ = mg._build_exclusive_strict({"parallel_mode": "parallel"})
        check("A7 D -> 只观测", _s2 is False)

        # E 严格下第二个房间取锁必须被拒
        _st = {"parallel_mode": "sequential",
               "rooms": {"甲": {"active": True}, "乙": {"active": True}}}
        r = mg._require_build_exclusive("乙", _st)
        check("A8 E 模式第二房间取锁被拒", isinstance(r, dict), r)
        check("A9 拒绝理由 CONCURRENT_BUILD",
              (r or {}).get("gate") == "CONCURRENT_BUILD", r)

        # ---------------- B. 锁释放 / 公平轮转 ----------------
        # ---------------- B. 锁释放 / 公平轮转 —— 实现已删除 ----------------
        section("B. P0-2 锁释放 / 公平轮转（已由监控器取代，改为逆向断言）")
        # 这里原本断言 cmd_sw_release 会清 fair_start / cmd_seq、_reclaim_lock
        # 有 room 模式分支。那些实现已随"进程内锁"一起删除：
        #   · 没有"公平轮转" —— 监控器的 FIFO 票据本来就保证顺序；
        #   · 没有"陈旧计时器" —— 不存在本地计时器；
        #   · 没有"让位" —— 已领取的使用权不会自动过期。
        # 断言改成【它们必须不存在】：有人把旧实现加回来必须被当场发现。
        _src = open(os.path.join(HERE, "mode_gate.py"), encoding="utf-8").read()
        import mode_gate as _mg_v2
        for _gone in ("_reclaim_lock", "_lock_owner_alive", "_purge_orphan_lock",
                      "SW_FAIR_CMDS", "SW_FAIR_SECONDS", "SW_MAX_HOLD",
                      "SW_CMD_HOLD", "SW_STALE_KILL"):
            check("B %s 已删除" % _gone, not hasattr(_mg_v2, _gone), _gone)
        check("B sw-release 仍存在（归还使用权的唯一入口）",
              "def cmd_sw_release(" in _src)
        section("C. P0-3 看护不得杀建模中的 SW")
        check("C1 _active_modeling_rooms 存在",
              callable(getattr(sg, "_active_modeling_rooms", None)))
        # 造一个 active 房间，确认能被识别
        with open(os.path.join(HERE, "mode_state.json"), "w", encoding="utf-8") as f:
            json.dump({"rooms": {"结构件": {"active": True}}}, f, ensure_ascii=False)
        _busy = sg._active_modeling_rooms()
        check("C2 能识别出在建模的房间", _busy == ["结构件"], _busy)
        with open(os.path.join(HERE, "mode_state.json"), "w", encoding="utf-8") as f:
            json.dump({"rooms": {"结构件": {"active": True, "ended_at": 1.0}}}, f,
                      ensure_ascii=False)
        check("C3 已结束的房间不算", sg._active_modeling_rooms() == [])
        try:
            os.remove(os.path.join(HERE, "mode_state.json"))
        except Exception:
            pass
        _gsrc = open(os.path.join(HERE, "sw_guard.py"), encoding="utf-8").read()
        check("C4 超阈值分支会检查建模中",
              "deferred_modeling" in _gsrc, "缺延迟处置 = 建模被腰斩")
        # GUARD_OFF 要能停掉存量看护
        _msrc = open(os.path.join(HERE, "mode_gate.py"), encoding="utf-8").read()
        check("C5 GUARD_OFF 会停掉存量看护",
              "stopped_existing" in _msrc, "只挡新拉起的 = 老的照杀")

        # ---------------- D. 编码 ----------------
        section("D. 子进程输出必须指定 encoding（GBK 崩溃）")
        _bad = []
        for _f in ("mode_gate.py", "sw_bridge.py", "conn_state.py"):
            _t = open(os.path.join(HERE, _f), encoding="utf-8").read()
            for _mm in re.finditer(r"subprocess\.run\([\s\S]{0,400}?\)", _t):
                _blk = _mm.group(0)
                if "text=True" in _blk and "encoding" not in _blk:
                    _bad.append(_f)
        check("D1 无缺 encoding 的 subprocess.run", not _bad, _bad)

        # ---------------- E. 免锁命令 ----------------
        section("E. 纯文件操作不抢 SW 锁")
        _nolock_src = open(os.path.join(HERE, "sw_bridge.py"), encoding="utf-8").read()
        # 注意：_NO_LOCK_CMDS 是函数内局部变量，且集合体里含大量注释与花括号，
        #   用 \{.*?\} 会在第一个注释花括号处截断（实测截断后什么也匹配不到）。
        #   正确做法：从定义行开始，一直取到"真正闭合这个集合"的那一行 ——
        #   即下一个以 "}" 开头的行（本仓库的写法）。
        _start = _nolock_src.find("_NO_LOCK_CMDS = {")
        _set = ""
        if _start >= 0:
            _tail = _nolock_src[_start:]
            _endm = re.search(r"\n\s*\}", _tail)
            _set = _tail[:_endm.end()] if _endm else _tail[:20000]
        for _cmd in ("check-part", "cleanup", "self-check"):
            check("E1 %s 在 _NO_LOCK_CMDS" % _cmd, '"%s"' % _cmd in _set,
                  "会抢房间锁（集合体长度 %d）" % len(_set))

        # ---------------- F. 去重取数 ----------------
        section("F. 去重闸：变量/中文路径可提取 + 自动写台账")
        _d = os.path.join(tmp, "scripts")
        os.makedirs(_d, exist_ok=True)

        def _w(n, c):
            fp = os.path.join(_d, n)
            with open(fp, "w", encoding="utf-8") as f:
                f.write(c)
            return fp

        _cases = {
            "lit.py": 'm.save(r"C:\\out\\DSH_a.SLDPRT")\n',
            "cn.py": 'm.save(r"C:\\out\\DSH_回归A.SLDPRT")\n',
            "var.py": 'od = r"C:\\out"\nt = os.path.join(od, "DSH_b.SLDPRT")\nm.save(t)\n',
            "readonly.py": '# 只读 C:\\out\\DSH_x.SLDPRT\nprint(1)\n',
        }
        for _n, _c in _cases.items():
            _w(_n, _c)
        check("F1 字面 ASCII 路径",
              sb._extract_output_paths(os.path.join(_d, "lit.py")) == ["C:\\out\\DSH_a.SLDPRT"],
              sb._extract_output_paths(os.path.join(_d, "lit.py")))
        _cn = sb._extract_output_paths(os.path.join(_d, "cn.py"))
        check("F2 ★中文文件名可提取", any("回归A" in p for p in _cn), _cn)
        _var = sb._extract_output_paths(os.path.join(_d, "var.py"))
        check("F3 ★变量路径可提取", any("DSH_b" in p for p in _var), _var)
        check("F4 只读探针不误报",
              sb._extract_output_paths(os.path.join(_d, "readonly.py")) == [])
        check("F5 _auto_register_parts 存在",
              callable(getattr(sb, "_auto_register_parts", None)))
        _rsrc = open(os.path.join(HERE, "sw_bridge.py"), encoding="utf-8").read()
        check("F6 run 成功后调用自动登记",
              "_auto_register_parts(" in _rsrc and "part_registry_auto" in _rsrc)

        # ---------------- G. 文档身份锁 ----------------
        section("G. new_part 不关别人的未命名文档")
        check("G1 _MY_UNNAMED_DOCS 存在",
              isinstance(getattr(swapi, "_MY_UNNAMED_DOCS", None), set))
        _ssrc = open(os.path.join(HERE, "swapi.py"), encoding="utf-8").read()
        check("G2 未命名文档要看归属",
              "_title not in _MY_UNNAMED_DOCS" in _ssrc, "会关掉别人的半成品")
        check("G3 新建后登记标题",
              "_MY_UNNAMED_DOCS.add(" in _ssrc)

        # ---------------- H. 回执制 ----------------
        section("H. 回执制 —— prime-report 已删除；改由监控器 part-saved 记账")
        # 这一节原本断言 record_prime_report 返回 PR-xxxx 回执、幂等、
        # 尺寸变化产生新回执。整套上报机制随总理子代理一起删除：
        # 零件回执现在是监控器的 part-saved 事件（带文件指纹与 generation）；
        # 对同一 generation 重复保存会被 STALE_GENERATION 拒绝 ——
        # 这比"内容哈希产生新回执"更硬，因为它绑定的是磁盘上的真实文件。
        for _gone in ("record_prime_report", "prime_receipts",
                      "check_part_receipted", "prime_reports", "prime_board"):
            check("H %s 已删除" % _gone, not hasattr(m2, _gone), _gone)
        import sw_monitor_client as _mc_v2
        check("H 监控器客户端提供 part-saved 记账",
              callable(getattr(_mc_v2, "part_saved", None)))
        check("H 监控器客户端提供 part-failed 记账",
              callable(getattr(_mc_v2, "part_failed", None)))
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
