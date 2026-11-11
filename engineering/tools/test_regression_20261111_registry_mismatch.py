# -*- coding: utf-8 -*-
"""test_regression_20261111_registry_mismatch.py — 【P3 台账张冠李戴】回归

═══════════════════════════════════════════════════════════════════════════
现场（验收复跑 20261111_131815_580E38FA，磁盘实锤）
═══════════════════════════════════════════════════════════════════════════
传动轴系小屋上报台账时把【输出轴】写成了【平键】的尺寸：

  scripts/report_DSH_输出轴.json      dims={volume:6300, bbox:[-25,25,0,9,-7,7]}
  scripts/report_DSH_输出轴平键.json  dims={volume:6300, bbox:[-25,25,0,9,-7,7]}
                                      ↑ 逐字节相同

而输出轴磁盘上的真实包围盒是 [-109, 64, -25, 25, -25, 25]
（parts\\DSH_输出轴.SLDPRT.origin.json / .pr.json，swapi 在 save() 时实测写入）。

三个平键的 dims 也完全相同、未做区分。

总理子代理靠人眼当场发现并拦下了，但【代码层没有判据】——
同一类错误换个人/换一轮就未必发现，所以必须由代码拦。

本测试钉住：上报 bbox 与磁盘实测不符时必须报
reported_bbox_vs_disk_mismatch 并 escalate_to_parent=True。

运行：python test_regression_20261111_registry_mismatch.py
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import part_registry as PR

PASS = []
FAIL = []

#: 真实零件目录（有 origin.json / pr.json 实测包围盒）
REAL_PARTS = r"C:\Users\j1877\Desktop\test\20261111_131815_580E38FA\parts"
SHAFT = os.path.join(REAL_PARTS, "DSH_输出轴.SLDPRT")
SHAFT_BBOX = [-109.0, 64.0, -25.0, 25.0, -25.0, 25.0]
KEY_BBOX = [-25.0, 25.0, 0.0, 9.0, -7.0, 7.0]      # 平键的（被误抄给输出轴）


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print("  [PASS] %s" % name)
    else:
        FAIL.append((name, detail))
        print("  [FAIL] %s %s" % (name, detail))


def _has_kind(r, kind):
    for w in (r.get("warnings") or []):
        if isinstance(w, dict) and w.get("kind") == kind:
            return w
    return None


def main():
    print("=" * 70)
    print("前置：确认测试依赖的真实零件与磁盘记录存在")
    print("=" * 70)
    if not os.path.isfile(SHAFT):
        print("  [SKIP] 真实零件不存在（%s）—— 本测试需要真实任务目录" % SHAFT)
        print("         这不是失败：换机器后没有该任务目录时自然跳过。")
        return 0
    _org = SHAFT + ".origin.json"
    check("输出轴 origin.json 存在", os.path.isfile(_org), _org)
    if os.path.isfile(_org):
        with open(_org, "r", encoding="utf-8-sig") as f:
            _b = (json.load(f) or {}).get("bbox_mm")
        check("★ 磁盘实测包围盒是输出轴的（不是平键的）",
              _b == SHAFT_BBOX, "实际=%r" % (_b,))

    print("=" * 70)
    print("A. ★ 上报 bbox 抄成别的零件 → 必须被抓出来")
    print("=" * 70)
    wd = tempfile.mkdtemp(prefix="reg_mismatch_")
    try:
        rec = {
            "name": "DSH_输出轴", "room": "传动轴系", "file": SHAFT,
            "dims": "50x9x14",            # 平键的尺寸
            "bbox_mm": KEY_BBOX,          # 平键的包围盒（张冠李戴）
        }
        r = PR.add_part(rec, work_dir=wd,
                        reason="复跑现场：输出轴被上报成平键尺寸（验证能抓出）",
                        skip_verification=True)
        w = _has_kind(r, "reported_bbox_vs_disk_mismatch")
        check("★ 命中 reported_bbox_vs_disk_mismatch", w is not None,
              "warnings=%r" % (r.get("warnings"),))
        if w:
            check("★ 必须 escalate_to_parent=True（不能只当普通提示）",
                  w.get("escalate_to_parent") is True, repr(w))
            check("警告里带磁盘实测 bbox",
                  w.get("disk_bbox_mm") == SHAFT_BBOX, repr(w.get("disk_bbox_mm")))
            check("警告里带上报 bbox",
                  w.get("reported_bbox_mm") == [float(v) for v in KEY_BBOX],
                  repr(w.get("reported_bbox_mm")))
            check("偏差量算对了（84mm：X 方向 -109 vs -25）",
                  abs(float(w.get("max_delta_mm") or 0) - 84.0) < 1e-6,
                  "delta=%r" % (w.get("max_delta_mm"),))
            check("文案点明可能抄错零件",
                  ("抄错" in str(w.get("detail")) or "张冠李戴" in str(w.get("detail"))),
                  str(w.get("detail")))
    finally:
        shutil.rmtree(wd, ignore_errors=True)

    print("=" * 70)
    print("B. 正确上报（bbox 与磁盘一致）→ 不得误报")
    print("=" * 70)
    wd = tempfile.mkdtemp(prefix="reg_ok_")
    try:
        rec = {
            "name": "DSH_输出轴", "room": "传动轴系", "file": SHAFT,
            "dims": "173x50x50",
            "bbox_mm": SHAFT_BBOX,
        }
        r = PR.add_part(rec, work_dir=wd,
                        reason="正确上报，bbox 与磁盘实测一致",
                        skip_verification=True)
        check("正确上报不报 mismatch",
              _has_kind(r, "reported_bbox_vs_disk_mismatch") is None,
              "warnings=%r" % (r.get("warnings"),))
    finally:
        shutil.rmtree(wd, ignore_errors=True)

    print("=" * 70)
    print("C. dims 里混入 bbox_mm 键 → 必须拒绝落盘（不是静默接收）")
    print("=" * 70)
    wd = tempfile.mkdtemp(prefix="reg_mixed_")
    try:
        rec = {
            "name": "DSH_输出轴", "room": "传动轴系", "file": SHAFT,
            "dims": {"volume_mm3": 6300, "bbox_mm": KEY_BBOX},
        }
        r = PR.add_part(rec, work_dir=wd,
                        reason="dims 里混了 bbox_mm（现场原始形状）",
                        skip_verification=True)
        check("★ 混入结构体的 dims 被拒绝", r.get("ok") is False, repr(r)[:300])
        check("错误信息指向 dims 字段", r.get("field") == "dims", repr(r)[:200])
    finally:
        shutil.rmtree(wd, ignore_errors=True)

    print("=" * 70)
    print("D. 未上报 bbox 但磁盘有实测值 → 给 INFO 提示（帮人补正）")
    print("=" * 70)
    wd = tempfile.mkdtemp(prefix="reg_info_")
    try:
        rec = {"name": "DSH_输出轴", "room": "传动轴系", "file": SHAFT,
               "dims": "173x50x50"}
        r = PR.add_part(rec, work_dir=wd, reason="未上报 bbox，磁盘有实测值",
                        skip_verification=True)
        w = _has_kind(r, "bbox_available_on_disk")
        check("给出 bbox_available_on_disk 提示", w is not None,
              "warnings=%r" % (r.get("warnings"),))
        if w:
            check("提示里带磁盘实测 bbox",
                  w.get("disk_bbox_mm") == SHAFT_BBOX, repr(w))
    finally:
        shutil.rmtree(wd, ignore_errors=True)

    print("=" * 70)
    print("汇总：PASS=%d  FAIL=%d" % (len(PASS), len(FAIL)))
    print("=" * 70)
    if FAIL:
        for n, d in FAIL:
            print("  FAILED: %s %s" % (n, d))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
