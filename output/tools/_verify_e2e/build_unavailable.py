# -*- coding: utf-8 -*-
"""阶段3：用【不可用牌号】GCr15 / ZL104 走同样的真实建模流程，看系统行为。"""
import os, sys, json
sys.path.insert(0, r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools")
import swapi

OUT = r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools\_verify_e2e"
RESULTS = {}

for GRADE, TAG in (("GCr15", "GCr15"), ("ZL104", "ZL104")):
    print("#" * 78)
    print("# 牌号 = %s" % GRADE)
    print("#" * 78)
    rec = {"grade": GRADE}
    PART = os.path.join(OUT, "DSH_承重支架_%s.SLDPRT" % TAG)
    try:
        print("STEP1 new_part(material=%r)" % GRADE)
        m = swapi.new_part(material=GRADE)
        print("  pending_material =", getattr(m, "_pending_material", None))

        print("STEP2 L 形轮廓 -> 对称拉伸 80")
        m.begin_sketch("Front Plane")
        m.polyline([(0, 0), (140, 0), (140, 12), (12, 12), (12, 90), (0, 90)])
        m.end_sketch()
        m.extrude(80, symmetric=True)
        print("  体积 =", m._body_volume_mm3())

        print("STEP3 赋材质")
        _sm = m.set_material(GRADE)
        print("  set_material.ok =", (_sm or {}).get("ok"))
        print("  set_material.error =", (_sm or {}).get("error"))
        print("  material_result.ok =", (getattr(m, "material_result", None) or {}).get("ok"))
        rec["set_material_ok"] = (_sm or {}).get("ok")
        rec["set_material_error"] = (_sm or {}).get("error")

        print("STEP4 读取真实几何")
        _mp = m.massprops(safe=True)
        print("  massprops =", json.dumps(_mp, ensure_ascii=False, default=str))
        _gm = m.get_material()
        print("  get_material =", json.dumps(_gm, ensure_ascii=False, default=str))
        rec["massprops"] = _mp
        rec["get_material"] = _gm

        print("STEP5 保存")
        _res = m.save(PART)
        print("  save.ok =", (_res or {}).get("ok"), "| err =", (_res or {}).get("error"))
        rec["save_ok"] = (_res or {}).get("ok")
        rec["part_exists"] = os.path.exists(PART)
        rec["part"] = PART
    except Exception as e:
        import traceback
        print("  !!! 异常:", repr(e))
        print(traceback.format_exc()[-1500:])
        rec["exception"] = repr(e)
    RESULTS[GRADE] = rec

print("#" * 78)
print("汇总")
print(json.dumps(RESULTS, ensure_ascii=False, default=str, indent=2))
__RESULT__ = RESULTS
