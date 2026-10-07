# -*- coding: utf-8 -*-
"""阶段4：逐个国标牌号走【真实赋材】，记录 SW 是否真的写入 + 密度是否生效。

只读 gb_materials.json / 源码；产出零件放 _verify_e2e/_matprobe。
"""
import os, sys, json
sys.path.insert(0, r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools")
sys.path.insert(0, r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools\physics")
import swapi
import material_db as mdb

OUT = r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools\_verify_e2e\_matprobe"
os.makedirs(OUT, exist_ok=True)

GRADES = ["Q235", "45#", "40Cr", "6061-T6", "304", "Q355", "20CrMnTi",
          "GCr15", "HT200", "QT500-7", "7075-T6", "2A12", "65Mn",
          "Cr12MoV", "316", "ZCuSn10P1", "H62", "ZL104"]

rows = []
for g in GRADES:
    tag = g.replace("#", "").replace("-", "_").replace("/", "_")
    part = os.path.join(OUT, "probe_%s.SLDPRT" % tag)
    m = mdb.get_material(g) or {}
    exp_rho = m.get("density_kg_m3")
    rec = {"grade": g, "expected_rho": exp_rho,
           "expected_sy": m.get("yield_mpa"),
           "gb_ready": mdb.gb_material_ready(g)[0]}
    try:
        mdl = swapi.new_part(material=g)
        mdl.begin_sketch("Front Plane")
        mdl.rect(0, 0, 40, 40)
        mdl.end_sketch()
        mdl.extrude(5)
        sm = mdl.set_material(g)
        mp = mdl.massprops(safe=True)
        gm = mdl.get_material()
        rec["set_ok"] = (sm or {}).get("ok")
        rec["applied_name"] = (sm or {}).get("applied_name")
        rec["match_level"] = (sm or {}).get("match_level")
        rec["cross_standard"] = (sm or {}).get("cross_standard")
        rec["mismatch_rejected"] = (sm or {}).get("material_mismatch_rejected")
        rec["real_rho"] = (mp or {}).get("density_kg_m3")
        rec["real_mass"] = (mp or {}).get("mass_kg")
        rec["real_vol"] = (mp or {}).get("volume_mm3")
        rec["sw_name"] = (gm or {}).get("name")
        rec["error"] = ((sm or {}).get("error") or "")[:150]
        mdl.save(part)
        rec["saved"] = os.path.exists(part)
        try:
            mdl.close()
        except Exception:
            pass
    except Exception as e:
        rec["exception"] = repr(e)
    rows.append(rec)
    print("  %-12s set_ok=%-5s applied=%-26s rho=%-8s (期望 %-7s) %s"
          % (g, rec.get("set_ok"), str(rec.get("applied_name"))[:26],
             rec.get("real_rho"), exp_rho,
             "OK" if rec.get("set_ok") else "FAIL"))

print()
print(json.dumps(rows, ensure_ascii=False, default=str, indent=2))
__RESULT__ = rows
