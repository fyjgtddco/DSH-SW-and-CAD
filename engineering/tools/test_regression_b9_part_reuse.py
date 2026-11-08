#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""【B9 回归测试】建模前置查重：同一零件不允许被反复重建。

对应缺陷（01-缺陷清单与日志定位.md · B9，P1 · ★P3）：

  现场（2026-11-07 会话，与测试员 P3 完全同构）：
    · 底盘 05:47 用 scripts/rebuild_chassis_7mm.py 重建（8mm→7mm）；
    · 电机安装验证块 14:54:46 与 14:57:16 **两次写盘**（中间 14:56:12 还改了脚本）；
    · parts/ 里多出 DSH_万向轮转接板1.SLDPRT（83 KB，无 .material.json）；
    · parts_rejects/ 与 parts/ 同时存在 DSH_电机安装验证块（81,386 B / 81,479 B）。
  测试员原话：「建模入口没有『目标零件是否已存在 + 是否同规格』的前置检查，
    save() 直接覆盖同名文件」。

判据（part_registry.check_reusable / CLI check-reusable）：
  ① 台账无此件                  → verdict=new，skip_modeling=False
  ② 台账有、文件在盘、规格一致   → verdict=reuse，skip_modeling=True
  ③ 台账有、但落盘文件不在      → verdict=rebuild（绝不允许"复用空气"）
  ④ 台账有、但尺寸/材料不一致    → verdict=rebuild，diffs 写明差异
  ⑤ 台账有、但没给可比字段      → verdict=unknown（不得当作一致 —— P5 同源机理）

用法：
    python test_regression_b9_part_reuse.py
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
    work = tempfile.mkdtemp(prefix="dsh-b9-")
    os.environ["DSH_STATE_DIR"] = work
    os.environ["PYTHONIOENCODING"] = "utf-8"
    os.environ["PYTHONUTF8"] = "1"
    sys.path.insert(0, HERE)
    try:
        import part_registry as pr

        # 造一个真实存在的零件文件
        partfile = os.path.join(work, "DSH_底盘.SLDPRT")
        with open(partfile, "wb") as f:
            f.write(b"\0" * 4096)

        print("=== ① 台账无此件 → new ===")
        r = pr.check_reusable("底盘", dims="180x140x8", work_dir=work)
        check("verdict=new", r.get("verdict") == "new", r.get("verdict"))
        check("skip_modeling=False", r.get("skip_modeling") is False,
              r.get("skip_modeling"))

        print("\n=== 登记进台账 ===")
        add = pr.add_part({"name": "底盘", "room": "车架结构间", "file": partfile,
                           "dims": "180x140x8", "material": "6061-T6",
                           "bbox_mm": [-90, 90, -70, 70, 0, 8]},
                          work_dir=work, reason="B9 回归：登记底盘", skip_verification=True)
        check("add_part 成功", add.get("ok") is True, add.get("error"))

        print("\n=== ② 规格一致 → reuse（不要再建模）===")
        r = pr.check_reusable("底盘", dims="180x140x8",
                              bbox_mm=[-90, 90, -70, 70, 0, 8],
                              material="6061-T6", work_dir=work)
        check("verdict=reuse", r.get("verdict") == "reuse", r.get("verdict"))
        check("skip_modeling=True", r.get("skip_modeling") is True,
              r.get("skip_modeling"))
        check("why 提到『不要重复建模』",
              "重复" in str(r.get("why") or ""), r.get("why"))

        print("\n=== ③ 尺寸不一致 → rebuild ===")
        r = pr.check_reusable("底盘", dims="180x140x7", work_dir=work)
        check("verdict=rebuild", r.get("verdict") == "rebuild", r.get("verdict"))
        check("diffs 指出 dims", any(d.get("field") == "dims" for d in r.get("diffs") or []),
              r.get("diffs"))
        check("skip_modeling=False", r.get("skip_modeling") is False,
              r.get("skip_modeling"))

        print("\n=== ④ 材料不一致 → rebuild ===")
        r = pr.check_reusable("底盘", dims="180x140x8", material="Q235", work_dir=work)
        check("verdict=rebuild", r.get("verdict") == "rebuild", r.get("verdict"))
        check("diffs 指出 material",
              any(d.get("field") == "material" for d in r.get("diffs") or []),
              r.get("diffs"))

        print("\n=== ⑤ 落盘文件不存在 → rebuild（不许复用空气）===")
        os.remove(partfile)
        r = pr.check_reusable("底盘", dims="180x140x8", material="6061-T6",
                              work_dir=work)
        check("verdict=rebuild", r.get("verdict") == "rebuild", r.get("verdict"))
        check("diffs 指出 file",
              any(d.get("field") == "file" for d in r.get("diffs") or []),
              r.get("diffs"))
        check("skip_modeling=False", r.get("skip_modeling") is False,
              r.get("skip_modeling"))

        print("\n=== ⑥ 没给可比字段 → unknown（不得当作一致）===")
        with open(partfile, "wb") as f:
            f.write(b"\0" * 4096)
        r = pr.check_reusable("底盘", work_dir=work)
        check("verdict=unknown", r.get("verdict") == "unknown", r.get("verdict"))
        check("skip_modeling=False", r.get("skip_modeling") is False,
              r.get("skip_modeling"))
        check("why 明说不得据此判定已建好",
              "不得据此判定" in str(r.get("why") or ""), r.get("why"))

    finally:
        shutil.rmtree(work, ignore_errors=True)

    total = len(_checks)
    passed = sum(1 for c in _checks if c["ok"])
    print("\n=== 汇总：%d/%d 通过 ===" % (passed, total))
    if passed != total:
        for c in _checks:
            if not c["ok"]:
                print("  ✗", c["name"], "->", repr(c["detail"])[:300])
        return 1
    print("B9 回归：全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
