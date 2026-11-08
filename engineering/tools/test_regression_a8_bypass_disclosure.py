#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""【A8 回归测试】--force / DSH_DEFENSE_BYPASS 强放行必须留痕且进交付结论。

对应缺陷（01-缺陷清单与日志定位.md · A8，P1 · ★§9E）：

  现场（2026-11-07 会话）：
    · `:1217` `room-end 传感支架间 --force --reason "…L1等效模型失真8.81x…"`，
      `:1225` 6 秒后又跑了一遍；
    · `:1705` `DSH_DEFENSE_BYPASS=1` 走 confirm-assembly；
    · 留痕 defense_bypass.json 确实有 14:32:53 / 15:09:31 两条；
    · **但最终 BUG 总结仍写"三防线全部签发"** —— 报告侧从没读过这份留痕。

  测试员 §9E 的整改要求原文：`--force` 应在报告里标注为"未获真几何 FEA 证据"。

判据：
  ① log_bypass() 落盘的结构化记录含 bypassed=true / evidence_quality=NOT_VERIFIED / scope；
  ② bypass_summary() 能按房间归集，并给出【必须原样渲染】的 line；
  ③ count>0 时 must_render=true（交付文案不得再宣称"全部签发"）；
  ④ pre_delivery_check() 的返回体必须带 bypass / bypass_count / bypass_line /
     banned_phrases / delivery_wording_rule —— 报告侧没有理由再说"看不见"。

用法：
    python test_regression_a8_bypass_disclosure.py
退出码 0 = 全部通过。
"""
import io
import json
import os
import shutil
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
_checks = []


def check(name, ok, detail=None):
    _checks.append({"name": name, "ok": bool(ok), "detail": detail})
    print(("  [PASS] " if ok else "  [FAIL] ") + name)
    if not ok and detail is not None:
        print("         detail: %r" % (detail,))
    return bool(ok)


def main():
    state_dir = tempfile.mkdtemp(prefix="dsh-a8-")
    os.environ["DSH_STATE_DIR"] = state_dir
    os.environ["PYTHONIOENCODING"] = "utf-8"
    os.environ["PYTHONUTF8"] = "1"
    sys.path.insert(0, HERE)
    try:
        import defense_gate as dg

        print("=== ① log_bypass 结构化留痕 ===")
        # 干净起点：把可能的旧留痕清掉（隔离目录内，安全）
        try:
            if os.path.exists(dg.BYPASS_LOG_FILE):
                os.remove(dg.BYPASS_LOG_FILE)
        except Exception:
            pass
        rec1 = dg.log_bypass("mode_gate.room-end:传感支架间",
                             reason="L1 等效模型失真 8.81x", by="cli-force")
        rec2 = dg.log_bypass("workflow_gate.confirm-assembly",
                             reason="DSH_DEFENSE_BYPASS=1", by="env")
        check("log_bypass 返回记录", isinstance(rec1, dict), rec1)
        check("记录含 bypassed=True", (rec1 or {}).get("bypassed") is True, rec1)
        check("记录含 evidence_quality=NOT_VERIFIED",
              (rec1 or {}).get("evidence_quality") == "NOT_VERIFIED", rec1)
        check("scope 从 where 反推出房间名",
              (rec1 or {}).get("scope") == "传感支架间", (rec1 or {}).get("scope"))
        check("任务级事件 scope=task",
              (rec2 or {}).get("scope") == "task", (rec2 or {}).get("scope"))

        print("\n=== ② bypass_summary 汇总 ===")
        summ = dg.bypass_summary()
        check("count == 2", summ.get("count") == 2, summ.get("count"))
        check("房间被归集", "传感支架间" in (summ.get("rooms") or []),
              summ.get("rooms"))
        check("must_render=True", summ.get("must_render") is True, summ.get("must_render"))
        line = summ.get("line") or ""
        check("line 里明写『强放行』", "强放行" in line, line)
        check("line 里明写『未获真几何 FEA 证据』", "FEA" in line, line)
        check("line 里点名了房间", "传感支架间" in line, line)

        print("\n=== ③ 无强放行时不得误报 ===")
        try:
            os.remove(dg.BYPASS_LOG_FILE)
        except Exception:
            pass
        summ0 = dg.bypass_summary()
        check("count == 0", summ0.get("count") == 0, summ0.get("count"))
        check("must_render=False", summ0.get("must_render") is False,
              summ0.get("must_render"))
        check("line 为空", (summ0.get("line") or "") == "", summ0.get("line"))

        print("\n=== ④ pre_delivery_check 必须披露强放行 ===")
        import mode2_flow as m2
        dg.log_bypass("mode_gate.room-end:行走传动间", reason="测试", by="cli-force")
        r = m2.pre_delivery_check(work_dir=state_dir, require_primes=False)
        for k in ("bypass", "bypass_count", "bypass_line", "banned_phrases",
                  "delivery_wording_rule"):
            check("返回体含 %s" % k, k in r, sorted(r.keys()))
        check("bypass_count 已回填", r.get("bypass_count") == 1, r.get("bypass_count"))
        check("bypass_must_render=True", r.get("bypass_must_render") is True,
              r.get("bypass_must_render"))
        check("banned_phrases 含『三防线全部签发』",
              "三防线全部签发" in (r.get("banned_phrases") or []),
              r.get("banned_phrases"))
        check("delivery_wording_rule 明确禁止全部签发措辞",
              "不得" in str(r.get("delivery_wording_rule") or ""),
              r.get("delivery_wording_rule"))

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
    print("A8 回归：全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
