# -*- coding: utf-8 -*-
"""E3 Step3: independent physical-sanity audit of the 18 GB grades (READ ONLY)."""
import sys, json, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
ROOT = r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools"
sys.path.insert(0, ROOT)
sys.path.insert(0, ROOT + r"\physics")
import material_db as mdb

raw = json.load(open(ROOT + r"\physics\gb_materials.json", encoding="utf-8"))["materials"]

# expected bands by category keyword
def band(cat):
    c = cat
    if "铝" in c:
        return dict(rho=(2600, 2900), E=(65000, 75000), sy=(60, 600), name="铝")
    if "铜" in c or "青铜" in c or "黄铜" in c:
        return dict(rho=(8300, 9000), E=(90000, 130000), sy=(60, 400), name="铜合金")
    if "铸铁" in c or "球墨" in c or "灰铸" in c:
        return dict(rho=(7000, 7500), E=(100000, 180000), sy=(150, 500), name="铸铁")
    if "不锈钢" in c:
        return dict(rho=(7800, 8100), E=(190000, 200000), sy=(150, 400), name="不锈钢")
    if "钢" in c:
        return dict(rho=(7700, 7900), E=(200000, 215000), sy=(150, 1600), name="钢")
    return dict(rho=(0, 99999), E=(0, 99999), sy=(0, 99999), name="?")

print("=" * 118)
print("STEP 3: 独立物理合理性审计（用 JSON 原始值 + material_db 解析值双视角）")
print("=" * 118)
hdr = (f"{'material':<14}{'cat':<22}{'E':>8}{'nu':>6}{'sy':>8}{'sb':>8}{'rho':>7}"
       f"{'sy/sb':>7}  判定")
print(hdr)
print("-" * 118)
findings = []
for mid, m in raw.items():
    cat = m.get("category", "")
    b = band(cat)
    E, nu, sy, sb, rho = m.get("E_mpa"), m.get("nu"), m.get("yield_mpa"), m.get("uts_mpa"), m.get("density_kg_m3")
    issues = []
    if sy is not None and sb is not None:
        if float(sb) < float(sy):
            issues.append(f"★物理不可能 σb({sb})<σy({sy})")
    ratio = (float(sy) / float(sb)) if (sy and sb) else None
    if ratio is not None:
        if ratio > 1:
            issues.append("★σy/σb>1")
        elif ratio < 0.3:
            issues.append(f"⚠σy/σb={ratio:.2f}<0.3 可疑")
        elif ratio > 0.95:
            issues.append(f"⚠σy/σb={ratio:.2f}>0.95 可疑")
    if rho is not None and not (b["rho"][0] <= float(rho) <= b["rho"][1]):
        issues.append(f"⚠密度{rho} 超出{b['name']}常规{b['rho']}")
    if E is not None and not (b["E"][0] <= float(E) <= b["E"][1]):
        issues.append(f"⚠E={E} 超出{b['name']}常规{b['E']}")
    if sy is not None and not (b["sy"][0] <= float(sy) <= b["sy"][1]):
        issues.append(f"⚠σy={sy} 超出{b['name']}常规{b['sy']}")
    # 状态一致性
    tn = (m.get("table_note") or "") + (m.get("gb_data", {}).get("yield_mpa", {}).get("detail") or "")
    if ("F态" in tn and "T6" in tn) or ("T6" in tn and "F态" in tn):
        issues.append("★状态混用: 文本同时出现 F态 与 T6")
    print(f"{mid:<14}{cat[:20]:<22}{str(E):>8}{str(nu):>6}{str(sy):>8}{str(sb):>8}{str(rho):>7}"
          f"{(f'{ratio:.2f}' if ratio is not None else '-'):>7}  " + ("; ".join(issues) if issues else "OK"))
    for i in issues:
        findings.append((mid, i))

print()
print("=" * 118)
print("STEP 3b: 详细发现清单")
print("=" * 118)
if not findings:
    print("无")
for mid, i in findings:
    print(f"  [{mid}] {i}")

print()
print("=" * 118)
print("STEP 3c: 逐字段 source_layer 与 detail 文本自述是否一致（独立核对，不看代码注释）")
print("=" * 118)
# Independent rule: if the detail text admits the value came from a handbook/estimate
# while source_layer claims a GB-claimable layer -> MISLABEL.
CLAIMABLE = ("GB_ORIGINAL", "GB_GRADE_DEF")
HAND_BOOKISH = ("手册", "估算", "AZOM", "机械数控人", "速查", "等效")
mislabels = []
for mid, m in raw.items():
    for f, e in (m.get("gb_data") or {}).items():
        if not isinstance(e, dict):
            continue
        lay = e.get("source_layer"); det = e.get("detail") or ""
        if lay in CLAIMABLE and any(h in det for h in HAND_BOOKISH):
            mislabels.append((mid, f, lay, det[:150]))
            print(f"  ★ MISLABEL {mid}.{f}: layer={lay} 但 detail 自述含手册/估算字样")
            print(f"      detail: {det[:200]}")
for t in mislabels:
    pass
if not mislabels:
    print("  无")

print()
print("=" * 118)
print("STEP 3d: ZL104 状态不一致复验（已知问题）")
print("=" * 118)
zl_raw = raw["GB_ZL104"]
print("  JSON 原始值 : E=%s nu=%s sy=%s sb=%s rho=%s" % (
    zl_raw.get("E_mpa"), zl_raw.get("nu"), zl_raw.get("yield_mpa"),
    zl_raw.get("uts_mpa"), zl_raw.get("density_kg_m3")))
print("  JSON 声明层级: sy=%s sb=%s" % (
    zl_raw["gb_data"]["yield_mpa"]["source_layer"], zl_raw["gb_data"]["uts_mpa"]["source_layer"]))
zl = mdb.get_material("ZL104")
print("  mdb 解析后 : sy=%s sb=%s" % (zl.get("yield_mpa"), zl.get("uts_mpa")))
print("  consistency_guard = %s" % zl.get("consistency_guard"))
print("  values_pending    = %s" % zl.get("values_pending"))
from physics.simulation_report import _material_provenance_note
print("  report note       = %s" % json.dumps(_material_provenance_note(zl), ensure_ascii=False, indent=2))

print()
print("=" * 118)
print("STEP 3e: 2A12 修正复验（原误用 7075 σy=503）")
print("=" * 118)
a = mdb.get_material("2A12")
a7075 = mdb.get_material("7075")
print("  2A12  : sy=%s sb=%s E=%s rho=%s" % (a.get("yield_mpa"), a.get("uts_mpa"), a.get("E_mpa"), a.get("density_kg_m3")))
print("  7075  : sy=%s sb=%s E=%s rho=%s" % (a7075.get("yield_mpa"), a7075.get("uts_mpa"), a7075.get("E_mpa"), a7075.get("density_kg_m3")))
print("  2A12 == 7075 σy? %s" % (a.get("yield_mpa") == a7075.get("yield_mpa")))
print("  2A12 equivalent_id 仍为 %r (未清理!)" % a.get("equivalent_id"))
print("  2A12 equivalent_note = %r" % a.get("equivalent_note"))
