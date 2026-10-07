# -*- coding: utf-8 -*-
"""Step3e: 复现 BUG —— values_from_gb 对『纯兜底』材料误报 True"""
import sys, json
sys.path.insert(0, r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools")
sys.path.insert(0, r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools\physics")
import material_db as mdb
import workflow_gate as wg
from simulation_report import _material_provenance_note

print("=" * 100)
print("BUG 复现：material_db 说『全兜底』，workflow_gate 却报 values_from_gb=True")
print("=" * 100)
for g in ["40Cr", "20CrMnTi", "Cr12MoV", "6061-T6", "7075", "2A12",
          "Q235", "45#", "65Mn", "GCr15"]:
    m = mdb.get_material(g)
    if not m:
        print("%-10s [mdb 未收录]" % g); continue
    gate = wg._gb_grade_props(g)
    note = _material_provenance_note(gate) if gate else None
    mdb_gb = m.get("gb_sourced_fields") or []
    mdb_fb = m.get("fallback_fields") or []
    print("-" * 100)
    print("牌号 %-10s" % g)
    print("  material_db : gb_sourced_fields=%-28s fallback_fields=%s"
          % (mdb_gb, mdb_fb))
    print("  material_db : values_from_gb=%-6s values_pending=%s"
          % (m.get("values_from_gb"), m.get("values_pending")))
    print("  workflow_gate: values_from_gb=%-6s values_pending=%s"
          % (gate.get("values_from_gb"), gate.get("values_pending")))
    print("  report level = %s   claimable_as_gb = %s"
          % (note.get("level"), note.get("claimable_as_gb")))
    print("  report note  = %s" % note.get("note"))
    # 判定
    if not mdb_gb and gate.get("values_from_gb") is True:
        print("  >>> [BUG 确认] GB 原文来源字段为空（%s），却标记 values_from_gb=True"
              % (mdb_gb or "[]"))
        print("      → 报告级别 = %s，会被下游当成『国标原文值』引用！" % note.get("level"))
    elif gate.get("values_from_gb") is True:
        print("  >>> OK（部分字段确来自 GB 原文）")
