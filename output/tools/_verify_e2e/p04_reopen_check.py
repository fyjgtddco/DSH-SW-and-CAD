# -*- coding: utf-8 -*-
"""独立复核：打开每个零件，读回【真实】材料名与密度/质量，与凭据比对。"""
import os, sys, json
sys.path.insert(0, r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools")
import swapi

G = r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools\_verify_e2e"
sw = swapi.get_sw()
print("SW revision:", sw.RevisionNumber)

for tag in ("4545", "65Mn", "Q355", "GCr15", "ZL104"):
    part = os.path.join(G, "DSH_承重支架_%s.SLDPRT" % tag)
    print("=" * 84)
    print("零件:", os.path.basename(part), "存在=", os.path.exists(part))
    cred_p = part + ".material.json"
    cred = {}
    if os.path.exists(cred_p):
        import io
        cred = json.load(io.open(cred_p, encoding="utf-8"))
    print("  凭据: applied_name=%r density_kg_m3=%r ok=%r mismatch_rejected=%r"
          % (cred.get("applied_name"), cred.get("density_kg_m3"),
             cred.get("ok"), cred.get("material_mismatch_rejected")))
    try:
        r = swapi.SWModel.open_part(sw, part, doc_type=1)
        if not r.get("ok"):
            print("  打开失败:", r.get("error"))
            continue
        m = swapi.SWModel(sw, r.get("doc"))
        gm = m.get_material()
        mp = m.massprops(safe=True)
        print("  零件实际 get_material  =", json.dumps(gm, ensure_ascii=False, default=str))
        print("  零件实际 massprops     =", json.dumps(
            {k: mp.get(k) for k in ("ok", "volume_mm3", "density_kg_m3", "mass_kg")},
            ensure_ascii=False, default=str))
        print("  体积 =", m._body_volume_mm3(), " bbox =", m.body_box_mm())
        m.close()
    except Exception as e:
        print("  异常:", repr(e))
print("=" * 84)
print("完成")
