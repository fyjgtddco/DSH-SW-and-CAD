# -*- coding: utf-8 -*-
"""E4 任务三.2：material_provenance 是否如实区分「国标值 / 手册值 / 兜底值」。"""
import json
import os
import sys

TOOLS = r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools"
sys.path.insert(0, TOOLS)
sys.path.insert(0, os.path.join(TOOLS, "physics"))
import material_db as M  # noqa: E402
from simulation_report import _material_provenance_note  # noqa: E402

print("=" * 100)
print("material_provenance 逐牌号（报告里写什么）")
print("=" * 100)
print("%-14s %-22s %-16s %s" % ("ID", "level", "claimable_as_gb", "note"))
print("-" * 100)
rows = []
for mid in sorted(M.GB_MATERIALS.keys()):
    r = M.get_material(mid) or {}
    # 模拟报告收到 material 段（id 化）
    mat = dict(r)
    mat["id"] = mid
    n = _material_provenance_note(mat)
    rows.append({"id": mid, "prov": n,
                 "gb_sourced": r.get("gb_sourced_fields"),
                 "handbook": r.get("handbook_fields"),
                 "values_source": r.get("values_source")})
    print("%-14s %-22s %-16s %s"
          % (mid, (n or {}).get("level"), (n or {}).get("claimable_as_gb"),
             ((n or {}).get("note") or "")[:60]))

print("\n" + "=" * 100)
print("问题识别：真实来源是「混合(GB σy/σb + 手册 E/ν/ρ)」但报告 level=UNKNOWN 的牌号")
print("=" * 100)
_mis = []
for r in rows:
    _lvl = (r["prov"] or {}).get("level")
    _gb = r["gb_sourced"] or []
    _hb = r["handbook"] or []
    if _lvl == "UNKNOWN" and _gb:
        _mis.append(r["id"])
        print("  ★ %-14s level=UNKNOWN 但实际有 %d 个字段来自国标 %s"
              % (r["id"], len(_gb), _gb))
        print("     values_source（数据库里其实已如实标注）: %s" % r["values_source"])
print("\n  合计 %d 个牌号：报告说『来源未标注』，而数据库里【已经】逐字段标注了。" % len(_mis))
print("  => 这些牌号的报告 note 与事实不符（应为『GB+手册 混合来源』）。")

with open(os.path.join(TOOLS, "_verify_e4", "out_provenance.json"), "w",
          encoding="utf-8") as f:
    json.dump(rows, f, ensure_ascii=False, indent=1, default=str)
print("\n[saved] out_provenance.json")
