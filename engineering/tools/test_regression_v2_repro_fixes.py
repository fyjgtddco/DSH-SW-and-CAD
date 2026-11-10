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
        section("B. P0-2 锁释放清 fair_start；room 模式不参与轮转")
        # 源码级断言：cmd_sw_release 里必须有 fair_start 清零
        _src = open(os.path.join(HERE, "mode_gate.py"), encoding="utf-8").read()
        _m = re.search(r"def cmd_sw_release\(.*?\n(?=def )", _src, re.S)
        _body = _m.group(0) if _m else ""
        check("B1 sw-release 清 fair_start",
              'lk["fair_start"] = None' in _body, "未清零 = 陈旧计时器导致抢锁")
        check("B2 sw-release 清 cmd_seq", 'lk["cmd_seq"] = 0' in _body)
        # _reclaim_lock 里 room 模式不参与公平轮转
        _m2 = re.search(r"def _reclaim_lock\(.*?\n(?=def )", _src, re.S)
        _b2 = _m2.group(0) if _m2 else ""
        check("B3 _reclaim_lock 有 room 模式分支",
              "_room_mode" in _b2, "缺分支 = 排队即抢锁")
        check("B4 room 模式下公平轮转被排除",
              "(not _room_mode) and has_waiter" in _b2, _b2[:300])

        # ---------------- C. 看护 ----------------
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
        section("H. 总理回执 receipt_id")
        _plan_dir = os.path.join(tmp, "plan")
        os.makedirs(_plan_dir, exist_ok=True)
        _report = {
            "part_name": "DSH_测试件", "room": "结构件",
            "file": os.path.join(tmp, "DSH_测试件.SLDPRT"),
            "dims": {"length_mm": 100, "width_mm": 50, "height_mm": 10},
            "material": "45",
            "credentials": {"material": "a.json", "physics": "b.json", "domain": "c.json"},
            "adjacent": [],
        }
        try:
            r1 = m2.record_prime_report(_report, work_dir=_plan_dir)
        except Exception as e:
            r1 = {"ok": False, "error": repr(e)}
        check("H1 上报成功", r1.get("ok") is True, r1)
        check("H2 ★返回 receipt_id", bool(r1.get("receipt_id")), r1)
        check("H3 receipt_id 形如 PR-",
              str(r1.get("receipt_id", "")).startswith("PR-"), r1)
        # 幂等：同内容重复上报 -> 同一 id
        r2 = m2.record_prime_report(_report, work_dir=_plan_dir)
        check("H4 同内容重复上报 -> 同一 receipt_id",
              r2.get("receipt_id") == r1.get("receipt_id"), (r1, r2))
        # 内容变化 -> 新 id
        _report2 = dict(_report, dims={"length_mm": 200, "width_mm": 50, "height_mm": 10})
        r3 = m2.record_prime_report(_report2, work_dir=_plan_dir)
        check("H5 尺寸变化 -> 新 receipt_id",
              r3.get("receipt_id") != r1.get("receipt_id"), (r1, r3))
        check("H6 prime_receipts 可查",
              callable(getattr(m2, "prime_receipts", None)))
        _rc = m2.prime_receipts(work_dir=_plan_dir)
        check("H7 回执台账有记录", _rc.get("count", 0) >= 2, _rc)
        _ck = m2.check_part_receipted("DSH_测试件", work_dir=_plan_dir)
        check("H8 零件回执判定可用", _ck.get("receipted") is True, _ck)

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
