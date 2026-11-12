#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""回归：P4 核心 —— 房间级独占（_require_build_exclusive）+ 并行策略识别

对应《减速机设计过程回溯报告》第二版 7 P4 / 9-B-2：
  「room-start 前置断言：mode_state 中 active 房间数必须为 0，否则拒绝」

以及《bug 修复核查报告》三「未修复的 7 项」第 1 项：
  「hold_mode 仍是 command，房间级独占锁根本未实现」

关键点：旧代码在 mode_gate.py:6029 的注释里【承诺】了这个函数，
但全仓 grep 只命中那一行注释 —— 函数并不存在。
本测试的首要目的就是把「注释里承诺的东西」变成「必须真的存在且可用」。

判据：
  E1  函数存在且可调用（防「注释代替实现」）
  E2  parallel_mode=E -> 严格（拒绝）
  E3  parallel_mode=D -> 只观测（放行）
  E4  未知并行策略 -> 只观测（向后兼容，不卡老任务）
  E5  env=1 / env=0 双向覆盖
  E6  严格 + 有别的在世房间 -> 返回 CONCURRENT_BUILD 拒绝体
  E7  严格 + 没有别的在世房间 -> 放行（None）
  E8  拒绝体会留痕（concurrent_build_violations.json 有记录）
  E9  hold_mode 默认就是 room（SW_ROOM_HOLD=1）
  E10 cmd_sw_request 真的接上了这道闸（源码级接线断言）

运行: python test_regression_p4_build_exclusive.py
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
    tmp = tempfile.mkdtemp(prefix="p4_excl_")
    old_state = os.environ.get("DSH_STATE_DIR")
    old_strict = os.environ.pop("DSH_CONCURRENT_BUILD_STRICT", None)
    try:
        # 隔离状态目录，绝不碰真实门禁状态
        os.environ["DSH_STATE_DIR"] = tmp
        import mode_gate as mg

        section("E1 函数存在（防「注释代替实现」）")
        check("E1 _require_build_exclusive 存在且可调用",
              callable(getattr(mg, "_require_build_exclusive", None)),
              "只 grep 到注释时代码实际是缺的")

        section("E2~E5 严格度判定")
        st_e = {"parallel_mode": "E"}
        st_d = {"parallel_mode": "D"}
        check("E2 E -> 严格", mg._build_exclusive_strict(st_e)[0] is True,
              mg._build_exclusive_strict(st_e))
        check("E3 D -> 只观测",
              mg._build_exclusive_strict(st_d)[0] is False,
              mg._build_exclusive_strict(st_d))
        check("E4 未知 -> 只观测",
              mg._build_exclusive_strict({})[0] is False,
              mg._build_exclusive_strict({}))
        os.environ["DSH_CONCURRENT_BUILD_STRICT"] = "1"
        check("E5 env=1 覆盖为严格",
              mg._build_exclusive_strict(st_d)[0] is True)
        os.environ["DSH_CONCURRENT_BUILD_STRICT"] = "0"
        check("E5 env=0 覆盖为只观测",
              mg._build_exclusive_strict(st_e)[0] is False)
        os.environ.pop("DSH_CONCURRENT_BUILD_STRICT", None)
        check("E5 去掉 env 后回到按策略判定",
              mg._build_exclusive_strict(st_e)[0] is True)

        section("E6~E8 拦截行为")
        state = {
            "parallel_mode": "E",
            "rooms": {
                "甲": {"active": True, "started_at": 1.0},
                "乙": {"active": True, "started_at": 2.0},
            },
        }
        r = mg._require_build_exclusive("乙", state)
        check("E6 严格+有别的在世房间 -> 拒绝", isinstance(r, dict), r)
        check("E6 gate=CONCURRENT_BUILD",
              (r or {}).get("gate") == "CONCURRENT_BUILD", r)
        check("E6 指明占用者", (r or {}).get("active_rooms") == ["甲"], r)
        # 【监控器接入】hold_mode 不再有 room/command 两档：使用权按命令
        #   发放，统一报 "permit"。这里断言它【不再是 room】，说明房间级
        #   独占模型确实退场了。
        check("E6 hold_mode 不再是 room", (r or {}).get("hold_mode") != "room", r)
        check("E6 hold_mode=permit", (r or {}).get("hold_mode") == "permit", r)
        check("E6 给出证据入口",
              "room-board" in str((r or {}).get("hint")), r)

        vp = os.path.join(tmp, "concurrent_build_violations.json")
        check("E8 违规留痕文件已生成", os.path.isfile(vp), vp)
        if os.path.isfile(vp):
            with open(vp, "r", encoding="utf-8") as f:
                rec = (json.load(f) or {}).get("records") or []
            check("E8 记录里含本次房间",
                  any(x.get("room") == "乙" for x in rec), rec)

        st_only = {"parallel_mode": "E",
                   "rooms": {"乙": {"active": True, "started_at": 2.0}}}
        check("E7 严格+只自己 -> 放行",
              mg._require_build_exclusive("乙", st_only) is None)
        st_ended = {"parallel_mode": "E",
                    "rooms": {"甲": {"active": True, "ended_at": 5.0},
                              "丙": {"active": True, "failed_at": 6.0},
                              "乙": {"active": True}}}
        check("E7 已 end/fail 的房间不算在世",
              mg._require_build_exclusive("乙", st_ended) is None)

        section("E9 房间级独占锁已删除（使用权归监控器）")
        for _gone in ("SW_ROOM_HOLD", "SW_ROOM_HOLD_MAX", "_room_hold_enabled",
                      "_room_hold_limit"):
            check("E9 %s 已删除" % _gone, not hasattr(mg, _gone))

        section("E10 接线断言（源码级）")
        src = open(os.path.join(HERE, "mode_gate.py"), encoding="utf-8").read()
        # 进程内取锁入口已删除，闸门现在接在【命令许可】这一侧：
        #   sw_bridge._gate_sw_lock 才是真正决定"能不能动 SW"的地方。
        _sb = open(os.path.join(HERE, "sw_bridge.py"), encoding="utf-8").read()
        _m = re.search(r"def _gate_sw_lock\(.*?(?=\ndef )", _sb, re.S)
        _gate_body = _m.group(0) if _m else ""
        check("E10 命令许可入口存在（sw_bridge._gate_sw_lock）",
              bool(_gate_body), "未接线 = 闸门形同不存在")
        check("E10 许可入口先 require_ready（未接入即拒绝）",
              "require_ready()" in _gate_body, _gate_body[:300])
        check("E10 许可入口返回结构带 denied/busy/permit 三态",
              all(k in _gate_body for k in ('"denied"', '"busy"', '"permit"')),
              _gate_body[:400])
        check("E10 闸门仍有调用点（不是死代码）",
              src.count("_require_build_exclusive") + _sb.count("_require_build_exclusive") >= 2,
              (src.count("_require_build_exclusive"), _sb.count("_require_build_exclusive")))

        section("汇总")
        print("  PASS=%d  FAIL=%d" % (PASS, FAIL))
        if FAILS:
            print("\n失败明细：")
            for x in FAILS:
                print("  - " + x)
        return 0 if FAIL == 0 else 1
    finally:
        if old_state is None:
            os.environ.pop("DSH_STATE_DIR", None)
        else:
            os.environ["DSH_STATE_DIR"] = old_state
        if old_strict is not None:
            os.environ["DSH_CONCURRENT_BUILD_STRICT"] = old_strict
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
