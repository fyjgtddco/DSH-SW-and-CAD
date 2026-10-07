# -*- coding: utf-8 -*-
"""E/ν 敏感度 + SW 材质库对国标牌号的接受情况。

只读：临时工况写在 _verify_e2e 下，绝不改 gb_materials.json / 源码。
"""
import io, json, os, sys
TOOLS = r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools"
sys.path.insert(0, TOOLS)
sys.path.insert(0, os.path.join(TOOLS, "physics"))
import material_db as mdb
import swapi
from physics import fea_solver

G = os.path.join(TOOLS, "_verify_e2e")
base = json.load(io.open(os.path.join(G, "case_45.json"), encoding="utf-8"))

print("=" * 92)
print("A) E 对位移/应力的敏感度（同一零件几何，仅改 E；ν 固定）")
print("=" * 92)
# 用真实零件的真实几何
geo = {"bbox_mm": [0.0, 140.0, 0.0, 90.0, -40.0, 40.0],
       "volume_mm3": 203736.35333353045}
for E in (205000, 100000, 410000, 72000):
    case = json.loads(json.dumps(base))
    case["material"]["youngs_modulus_mpa"] = E
    case["material"]["poissons_ratio"] = 0.29
    case["material"]["yield_strength_mpa"] = 355.0
    case["material"]["density_kg_m3"] = 7850
    case["real_geometry"] = geo
    r = fea_solver.solve_feapy(case, output_dir=os.path.join(G, "_fea_tmp"))
    print("  E=%-7s -> max_von_mises=%-8s max_disp=%-10s SF=%s"
          % (E, r.get("max_von_mises_mpa"), r.get("max_displacement_mm"),
             r.get("safety_factor")))
print("  说明：位移 ∝ 1/E（线性），应力与 E 无关 —— 故 E 只影响刚度/位移判据。")

print()
print("=" * 92)
print("B) ν 对结果的影响")
print("=" * 92)
for nu in (0.29, 0.30, 0.33, 0.25):
    case = json.loads(json.dumps(base))
    case["material"]["youngs_modulus_mpa"] = 205000
    case["material"]["poissons_ratio"] = nu
    case["material"]["yield_strength_mpa"] = 355.0
    case["material"]["density_kg_m3"] = 7850
    case["real_geometry"] = geo
    r = fea_solver.solve_feapy(case, output_dir=os.path.join(G, "_fea_tmp"))
    print("  nu=%-5s -> max_von_mises=%-8s max_disp=%-10s"
          % (nu, r.get("max_von_mises_mpa"), r.get("max_displacement_mm")))

print()
print("=" * 92)
print("C) 国标牌号在 SolidWorks 材质库里的可赋材性（真实探测，不写零件）")
print("=" * 92)
sw = swapi.get_sw()
try:
    dbs = list(sw.GetMaterialDatabases or [])
except Exception as e:
    dbs = []
    print("  GetMaterialDatabases EXC:", e)
print("  SW 材质库:", [os.path.basename(str(d)) for d in dbs])

GRADES = ["Q235", "45#", "40Cr", "6061-T6", "304", "Q355", "20CrMnTi",
          "GCr15", "HT200", "QT500-7", "7075-T6", "2A12", "65Mn",
          "Cr12MoV", "316", "ZCuSn10P1", "H62", "ZL104"]
print()
print("  %-12s %-9s %-10s %-8s %-24s %s"
      % ("牌号", "库中精确名", "期望密度", "期望族", "SW 实际命中", "判定"))
print("  " + "-" * 92)
for g in GRADES:
    m = mdb.get_material(g) or {}
    exp_rho = m.get("density_kg_m3")
    exp_fam = None
    try:
        exp_fam = swapi.material_family(exp_rho) if exp_rho else None
    except Exception:
        pass
    # 探测 SW 库中是否存在同名材料（只查库，不赋到零件）
    hit = None
    exact = None
    try:
        exact = swapi.find_material_in_library(g) if hasattr(
            swapi, "find_material_in_library") else None
    except Exception:
        exact = None
    if exact is None:
        # 退而用 list_materials 在库里找同名
        try:
            for db in ("SolidWorks Materials", "SolidWorks DIN Materials",
                       "自定义材料", "Sustainability Extras"):
                lst = swapi.list_materials(db) or []
                names = [x if isinstance(x, str) else x.get("name")
                         for x in lst]
                if g in names:
                    hit = (db, g)
                    break
        except Exception as e:
            hit = ("ERR", repr(e))
    print("  %-12s %-9s %-10s %-8s %-24s"
          % (g, "yes" if hit else ("?" if exact is None else "no"),
             exp_rho, exp_fam, str(hit)))
print()
print("  注：'?' 表示未能通过公开 API 直接判定，需看实际赋材结果。")
