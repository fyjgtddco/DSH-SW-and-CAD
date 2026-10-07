# -*- coding: utf-8 -*-
"""Step5: 跨文件口径一致性 + ZL104 物理不可能性影响面（只读）"""
import sys, json, os
sys.path.insert(0, r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools")
sys.path.insert(0, r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools\physics")
import material_db as mdb
import workflow_gate as wg
import defense_gate as dg
import swapi

print("=" * 100)
print("Step5.1  跨文件一致性：同一牌号在 4 个来源里的数值")
print("=" * 100)
GRADES = ["Q235", "45#", "Q355", "65Mn", "40Cr", "Cr12MoV", "6061-T6", "2A12",
          "304", "316", "GCr15", "HT200", "QT500-7", "ZCuSn10P1", "H62", "ZL104",
          "20CrMnTi", "7075"]

def _swapi_entry(name):
    try:
        swapi._ensure_gb_merged()
    except Exception:
        pass
    cm = getattr(swapi, "COMMON_MATERIALS", {}) or {}
    return cm.get(name)

print("%-12s | %-28s | %-28s | %-22s | %s"
      % ("牌号", "material_db(σy/σb/E)", "workflow_gate(σy/σb/E)",
         "swapi(σy/σb/dens)", "defense_gate(σy)"))
print("-" * 130)
issues = []
for g in GRADES:
    m = mdb.get_material(g)
    gt = wg._gb_grade_props(g)
    sw = _swapi_entry(g)
    dfn = dg._gb_grade_props(g) if hasattr(dg, "_gb_grade_props") else None
    a = "%s/%s/%s" % (m.get("yield_mpa"), m.get("uts_mpa"), m.get("E_mpa")) if m else "—"
    b = "%s/%s/%s" % (gt.get("yield_strength_mpa"), gt.get("uts_mpa"),
                      gt.get("youngs_modulus_mpa")) if gt else "—"
    c = "%s/%s/%s" % (sw.get("yield_mpa"), sw.get("uts_mpa"),
                      sw.get("density")) if sw else "（未合并）"
    d = "%s" % (dfn.get("yield_mpa") if dfn else "—")
    print("%-12s | %-28s | %-28s | %-22s | %s" % (g, a, b, c, d))
    # 一致性判定
    if m and gt:
        if m.get("yield_mpa") != gt.get("yield_strength_mpa"):
            issues.append("σy 分裂 %s: mdb=%s gate=%s"
                          % (g, m.get("yield_mpa"), gt.get("yield_strength_mpa")))
        if m.get("E_mpa") != gt.get("youngs_modulus_mpa"):
            issues.append("E 分裂 %s: mdb=%s gate=%s"
                          % (g, m.get("E_mpa"), gt.get("youngs_modulus_mpa")))
        if m.get("uts_mpa") != gt.get("uts_mpa"):
            issues.append("σb 分裂 %s: mdb=%s gate=%s"
                          % (g, m.get("uts_mpa"), gt.get("uts_mpa")))
    if m and sw:
        if m.get("yield_mpa") != sw.get("yield_mpa"):
            issues.append("σy 分裂(mdb/swapi) %s: %s vs %s"
                          % (g, m.get("yield_mpa"), sw.get("yield_mpa")))
        if m.get("density_kg_m3") != sw.get("density"):
            issues.append("密度分裂(mdb/swapi) %s: %s vs %s"
                          % (g, m.get("density_kg_m3"), sw.get("density")))

print()
print("  一致性结论：", "全部一致" if not issues else "发现 %d 处分裂" % len(issues))
for i in issues:
    print("    -", i)

print()
print("=" * 100)
print("Step5.2  ZL104 物理不可能（σy=276 > σb=150）的影响面")
print("=" * 100)
zl = mdb.get_material("ZL104")
print("  σy=%s  σb=%s  E=%s  ν=%s  ρ=%s"
      % (zl.get("yield_mpa"), zl.get("uts_mpa"), zl.get("E_mpa"),
         zl.get("nu"), zl.get("density_kg_m3")))
print("  σy 来源 = %s (%s)" % (zl["field_provenance"]["yield_mpa"]["source"],
                              zl["field_provenance"]["yield_mpa"].get("equivalent")))
print("  σb 来源 = %s" % zl["field_provenance"]["uts_mpa"]["source"])
print("  → 强度校核会用 σy=276 判 SF，但材料真实抗拉只有 150 MPa；")
print("     即『许用应力高于材料断裂强度』，SF 被系统性高估 276/150 = %.2f 倍"
      % (276.0 / 150.0))

# 疲劳：Se' = 0.5*Su
try:
    import fatigue
    print("  疲劳 Se'=0.5*σb = %.1f MPa（基于 GB σb=150）" % (0.5 * 150))
    print("  静态 SF 用 σy=276 → 若应力 200MPa，SF=1.38『通过』；")
    print("  而按 σb=150 实际已断裂 → 危险放行。")
except Exception as e:
    print("  fatigue import err:", repr(e))

print()
print("=" * 100)
print("Step5.3  GB 标称 vs 兜底 数值差异表（哪些牌号兜底显著偏离国标）")
print("=" * 100)
print("%-12s %-10s %-10s %-12s %s" % ("牌号", "GB σy", "兜底 σy", "偏差", "风险"))
for g in GRADES:
    raw = None
    for k, v in mdb.GB_MATERIALS.items():
        if g in (v.get("aliases") or []) or v.get("name_cn", "").startswith(g):
            raw = v; break
    if not raw:
        continue
    gbd = raw.get("gb_data") or {}
    gy = (gbd.get("yield_mpa") or {}).get("value")
    eid = raw.get("equivalent_id")
    fy = mdb.ALL_MATERIALS.get(str(eid).upper(), {}).get("yield_mpa") if eid else None
    if gy is None and fy is None:
        continue
    dev = ("%.1f%%" % ((fy - gy) / gy * 100)) if (gy and fy) else "—"
    risk = ""
    if gy and fy and abs(fy - gy) / gy > 0.30:
        risk = "★ 兜底严重偏离国标（>30%）"
    print("%-12s %-10s %-10s %-12s %s" % (g, gy, fy, dev, risk))
