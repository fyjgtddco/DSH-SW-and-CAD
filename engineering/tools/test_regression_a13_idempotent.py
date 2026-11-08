#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""【A13 回归测试】同一写命令窗口内重复执行必须被幂等键识别。

对应缺陷（01-缺陷清单与日志定位.md · A13，P2 · ★§13.4#2）：

  现场：`:1217` 与 `:1225` 相隔 **6 秒**执行了完全相同的
        `room-end 传感支架间 --force --reason "…"`，两次都真的执行了
        （两次留痕、两次冻结产物、两次自动收尾尝试）。
  根因：状态机命令没有幂等键，调用方在长上下文里重复同一动作，系统只会照做第二遍。

判据：
  ① 同一 (命令, 目标, flag) 在窗口内第二次调用 → duplicate=true，
     gate=IDEMPOTENT_REPLAY，且【不再产生副作用】（不留第二遍痕）；
  ② 参数不同（--force vs 不带）→ 不判重，正常执行；
  ③ 窗口外（>A13_IDEMPOTENT_WINDOW）→ 不判重；
  ④ dry-run 不参与判重（预演零副作用，重复预演无害）。

用法：
    python test_regression_a13_idempotent.py
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


def _run(args, state_dir, timeout=180):
    env = dict(os.environ)
    env["DSH_STATE_DIR"] = state_dir
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    p = subprocess.run([PY, os.path.join(HERE, "mode_gate.py")] + args,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env, timeout=timeout)
    out = (p.stdout or "").strip()
    try:
        return json.loads(out), p
    except Exception:
        return {"_raw": out, "_err": (p.stderr or "")[-1500:]}, p


def _bypass_count(state_dir):
    p = os.path.join(state_dir, "defense_bypass.json")
    if not os.path.exists(p):
        return 0
    try:
        with io.open(p, encoding="utf-8") as f:
            recs = (json.load(f) or {}).get("records") or []
        return len(recs)
    except Exception:
        try:
            with io.open(p, encoding="utf-8") as f:
                recs = (json.load(f) or {}).get("items") or []
            return len(recs)
        except Exception:
            return -1


def main():
    state_dir = tempfile.mkdtemp(prefix="dsh-a13-")
    try:
        _run(["declare", "2"], state_dir)
        _run(["room-start", "甲"], state_dir)

        print("=== ① 同一命令窗口内第二次 → duplicate ===")
        r1, _ = _run(["room-end", "甲", "--force"], state_dir)
        check("第一次正常执行", r1.get("ok") is True and not r1.get("duplicate"), r1.get("gate"))
        n1 = _bypass_count(state_dir)
        r2, _ = _run(["room-end", "甲", "--force"], state_dir)
        check("第二次被判为重复", r2.get("duplicate") is True, r2.get("gate"))
        check("gate == IDEMPOTENT_REPLAY", r2.get("gate") == "IDEMPOTENT_REPLAY",
              r2.get("gate"))
        check("返回 duplicate_of 证据", isinstance(r2.get("duplicate_of"), dict),
              r2.get("duplicate_of"))
        n2 = _bypass_count(state_dir)
        check("第二次没有产生第二遍留痕（可见副作用为零）", n1 == n2, (n1, n2))
        check("message 说明了是幂等保护",
              "幂等" in str(r2.get("message") or ""), r2.get("message"))

        print("\n=== ② 参数不同不判重 ===")
        _run(["room-start", "乙"], state_dir)
        a, _ = _run(["room-end", "乙", "--force"], state_dir)
        b, _ = _run(["room-end", "乙"], state_dir)   # 不带 --force → 不同指纹
        check("带 force 的执行成功", a.get("ok") is True, a.get("gate"))
        check("不带 force 的没被判重", not b.get("duplicate"), b.get("gate"))

        print("\n=== ③ 窗口外不判重 ===")
        _run(["room-start", "丙"], state_dir)
        _run(["room-end", "丙", "--force"], state_dir)
        # 直接改写幂等表把时间戳提前到窗口之外，避免真的等 8 秒
        p = os.path.join(state_dir, "cmd_idempotency.json")
        check("幂等表已落盘", os.path.exists(p), p)
        if os.path.exists(p):
            with io.open(p, encoding="utf-8") as f:
                d = json.load(f)
            for k, v in d.items():
                if isinstance(v, dict):
                    v["ts"] = float(v.get("ts") or 0) - 3600
            with io.open(p, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=2)
            _run(["room-start", "丙"], state_dir)
            d2, _ = _run(["room-end", "丙", "--force"], state_dir)
            check("窗口外不判重", not d2.get("duplicate"), d2.get("gate"))

        print("\n=== ④ dry-run 不参与判重 ===")
        _run(["room-start", "丁"], state_dir)
        # 注意：不带 --force 的 dry-run 仍会【真的跑三道防线检查】，
        # 本隔离环境没有防线凭据 → 返回 DEFENSE_REQUIRED（这是正确行为）。
        # 要走到 DRY_RUN 预演结论，必须带 --force（预演"强制下线会做什么"）。
        x, _ = _run(["room-end", "丁", "--force", "--dry-run"], state_dir)
        y, _ = _run(["room-end", "丁", "--force", "--dry-run"], state_dir)
        check("第一次 dry-run ok", x.get("ok") is True, x.get("gate"))
        check("第二次 dry-run 也不判重", not y.get("duplicate"), y.get("gate"))
        check("dry-run 仍是 DRY_RUN 结论", y.get("gate") == "DRY_RUN", y.get("gate"))

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
    print("A13 回归：全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
