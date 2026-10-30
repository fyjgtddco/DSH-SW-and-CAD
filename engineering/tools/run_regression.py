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

# ── 【BUG-10/11 同类修复】本 runner 自己也会因 GBK 控制台崩溃 ──────────────
# 实测：子测试输出里出现 "⚠"（U+26A0）等非 GBK 字符时，
#   print() 直接抛 UnicodeEncodeError，整个 runner 崩在中途 ——
#   表现为"回归跑了一半就没了"，而且堆栈指向 runner 自己的 print，
#   很容易被误读成"某个测试带崩了框架"。
# 修复：输出统一走 UTF-8（失败则用 backslashreplace 兜底，保证绝不抛）。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))

# ★ 部分测试脚本把 tools 目录作为 argv[1] 读取（写成 `TOOLS = sys.argv[1]`）。
#   原先 runner 一律不传参 → 它们在 __main__ 处 IndexError 直接崩，
#   表现为"单独跑通过、汇总里却 ❌"的假失败（question_contract 就是这种）。
_NEEDS_TOOLS_ARG = ("test_regression_question_contract.py",)


def _needs_tools_arg(filename):
    return filename in _NEEDS_TOOLS_ARG


def discover(only=None):
    out = []
    # ── 【2026-10-30】把 node 侧测试（test_*.mjs）也纳入回归 ──────────
    # 插件 Host/Client 半是 JS，纯 Python 断言覆盖不到；
    # 那些用例用 node:test 写，这里一并收进来跑。
    for n in sorted(os.listdir(HERE)):
        _is_py = n.startswith("test_regression_") and n.endswith(".py")
        _is_mjs = n.startswith("test_") and n.endswith(".mjs")
        if not (_is_py or _is_mjs):
            continue
        if only:
            if _is_mjs:
                continue
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
            if f.endswith(".mjs"):
                # node 侧测试用 node --test 跑
                cmd = ["node", "--test", os.path.join(HERE, f)]
            else:
                cmd = [sys.executable, os.path.join(HERE, f)]
                if _needs_tools_arg(f):
                    cmd.append(HERE)
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=600,
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
