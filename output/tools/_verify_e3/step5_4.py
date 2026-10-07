# -*- coding: utf-8 -*-
"""E3 Step5.4: 四方一致性核对 material_db / swapi / defense_gate / workflow_gate (READ ONLY)."""
import sys, json, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
ROOT = r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools"
sys.path.insert(0, ROOT)
sys.path.insert(0, ROOT + r"\physics")
import material_db as mdb

FIELDS = ["E_mpa", "nu", "yield_mpa", "uts_mpa", "density_kg_m3"]
GRADES = ["Q235", "45#", "40Cr", "6061-T6", "304", "Q355", "20CrMnTi", "GCr15",
          "HT200", "QT500-7", "7075-T6", "2A12", "65Mn", "Cr12MoV", "316",
          "ZCuSn10P1", "H62", "ZL104"]

def row(tag, d, mapping=None):
    if not isinstance(d, dict):
        return None
    mapping = mapping or {}
    out = {}
    for f in FIELDS:
        out[f] = d.get(mapping.get(f, f))
    return out

print("=" * 120)
print("STEP 5.4: 四方一致性核对")
print("=" * 120)

# --- side 1: material_db
mdb_rows = {}
for g in GRADES:
    m = mdb.get_material(g) or {}
    mdb_rows[g] = row("mdb", m)
print("material_db 载入 OK, 牌号数 =", len([g for g in GRADES if mdb_rows[g]]))

# --- side 2: swapi.COMMON_MATERIALS
sw_rows = {}
sw_err = None
try:
    import swapi
    st = swapi.gb_materials_status()
    print("swapi.gb_materials_status() =", json.dumps({k: v for k, v in st.items() if k != "searched"}, ensure_ascii=False)[:900])
    for g in GRADES:
        e = swapi.COMMON_MATERIALS.get(g)
        if e is None:
            # try alias / normalized
            for k, v in swapi.COMMON_MATERIALS.items():
                if "".join(c for c in str(k).lower() if c.isalnum()) == "".join(c for c in g.lower() if c.isalnum()):
                    e = v; break
        if isinstance(e, dict):
            sw_rows[g] = {
                "E_mpa": e.get("e_mpa"), "nu": e.get("poissons_ratio"),
                "yield_mpa": e.get("yield_mpa"), "uts_mpa": e.get("uts_mpa"),
                "density_kg_m3": e.get("density"),
            }
except Exception as ex:
    sw_err = repr(ex)
    print("swapi 载入失败:", sw_err)

# --- side 3: workflow_gate
wg_rows = {}
wg_err = None
try:
    import workflow_gate
    fn = None
    for cand in ("_gb_grade_props", "gb_grade_props", "_gb_props"):
        if hasattr(workflow_gate, cand):
            fn = getattr(workflow_gate, cand); print("workflow_gate 取数函数 =", cand); break
    if fn:
        import inspect
        print("  signature:", inspect.signature(fn))
        for g in GRADES:
            try:
                r = fn(g)
            except Exception as e:
                r = None
            if isinstance(r, dict):
                wg_rows[g] = {
                    "E_mpa": r.get("youngs_modulus_mpa", r.get("E_mpa")),
                    "nu": r.get("poissons_ratio", r.get("nu")),
                    "yield_mpa": r.get("yield_strength_mpa", r.get("yield_mpa")),
                    "uts_mpa": r.get("uts_mpa"),
                    "density_kg_m3": r.get("density_kg_m3", r.get("density")),
                }
    else:
        wg_err = "未找到 _gb_grade_props"
except Exception as ex:
    wg_err = repr(ex)
print("workflow_gate err =", wg_err, " rows =", len(wg_rows))

# --- side 4: defense_gate
dg_rows = {}
dg_err = None
try:
    import defense_gate
    names = [n for n in dir(defense_gate) if "gb" in n.lower() or "material" in n.lower()]
    print("defense_gate GB/material 相关符号:", names)
except Exception as ex:
    dg_err = repr(ex)
print("defense_gate err =", dg_err)

print()
print("=" * 120)
print("逐牌号逐字段对比（mdb 为基准）")
print("=" * 120)
diffs = []
for g in GRADES:
    base = mdb_rows.get(g)
    if not base:
        print(f"  {g}: material_db 无数据"); continue
    line = []
    for f in FIELDS:
        b = base[f]
        cells = [f"mdb={b}"]
        if g in sw_rows:
            s = sw_rows[g][f]
            if (s is None) != (b is None) or (s is not None and b is not None and abs(float(s) - float(b)) > 1e-6):
                cells.append(f"swapi={s}  <<<DIFF")
        if g in wg_rows:
            w = wg_rows[g][f]
            if (w is None) != (b is None) or (w is not None and b is not None and abs(float(w) - float(b)) > 1e-6):
                cells.append(f"wgate={w}  <<<DIFF")
        if len(cells) > 1:
            line.append(" | ".join(cells))
    if line:
        print(f"  [{g}]")
        for l in line:
            print("      ", l)
        diffs.append(g)
print()
print("不一致牌号:", diffs if diffs else "无")
