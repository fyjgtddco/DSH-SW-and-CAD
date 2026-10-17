#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_regression.py — 工程模式回归测试总入口（第二轮 Bug-34~46 修复验收）

用法：
    python run_regression.py            # 跑全部
    python run_regression.py 36 39      # 只跑指定 Bug 号

每个 test_regression_*.py 都是独立可执行脚本，退出码 0 = 通过。
本 runner 逐个调用它们并汇总，便于"每次改动后回归"。
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# ★ 部分测试脚本把 tools 目录作为 argv[1] 读取（写成 `TOOLS = sys.argv[1]`）。
#   原先 runner 一律不传参 → 它们在 __main__ 处 IndexError 直接崩，
#   表现为"单独跑通过、汇总里却 ❌"的假失败（question_contract 就是这种）。
_NEEDS_TOOLS_ARG = ("test_regression_question_contract.py",)


def _needs_tools_arg(filename):
    return filename in _NEEDS_TOOLS_ARG


def discover(only=None):
    out = []
    for n in sorted(os.listdir(HERE)):
        if not (n.startswith("test_regression_") and n.endswith(".py")):
            continue
        if only:
            if not any(("bug%s" % b) in n for b in only):
                continue
        out.append(n)
    return out


def main():
    only = [a for a in sys.argv[1:] if a.isdigit()]
    files = discover(only)
    if not files:
        print("未找到回归测试文件")
        return 1
    results = []
    for f in files:
        print("\n" + "=" * 72)
        print("RUN  " + f)
        print("=" * 72)
        try:
            # ★ 部分测试需要 tools 目录作为 argv[1]（见 _NEEDS_TOOLS_ARG）。
            #   原先 runner 一律不传参 → 那些测试在 __main__ 处 IndexError 直接崩，
            #   表现为"汇总里 ❌ 但单独跑却通过"的假失败。这里按需补上。
            cmd = [sys.executable, os.path.join(HERE, f)]
            if _needs_tools_arg(f):
                cmd.append(HERE)
            p = subprocess.run(cmd,
                               capture_output=True, text=True, timeout=600,
                               encoding="utf-8", errors="replace")
            tail = (p.stdout or "").strip().splitlines()
            for line in tail:
                if "PASS=" in line or "[FAIL]" in line:
                    print("  " + line.strip())
            if p.returncode != 0 and not any("PASS=" in l for l in tail):
                # 崩了且没有结论行 → 打印尾部便于定位
                for line in (tail or (p.stderr or "").strip().splitlines())[-6:]:
                    print("  ! " + line.strip())
            results.append((f, p.returncode == 0))
        except Exception as e:
            print("  异常: %r" % (e,))
            results.append((f, False))

    print("\n" + "=" * 72)
    print("汇总")
    print("=" * 72)
    ok = 0
    for f, passed in results:
        print("  %s %s" % ("✅" if passed else "❌", f))
        ok += 1 if passed else 0
    print("\n通过 %d / %d" % (ok, len(results)))
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
