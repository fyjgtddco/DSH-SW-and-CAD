# -*- coding: utf-8 -*-
"""E4 任务三：差分实验 —— 同一零件、同一工况，只换 --material，看 σy 用的是哪个值。

用 physics_bridge.py 直调（绕开 sw_bridge 的 --material 管道缺陷），
从而检验【材料解析逻辑本身】是否正确（国标值优先 vs 等效兜底）。
"""
import json
import os
import subprocess
import sys

TOOLS = r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools"
D = os.path.join(TOOLS, "_verify_e4")
PY = r"C:\Users\j1877\AppData\Local\Programs\Python\Python313\python.exe"
PART = os.path.join(D, "E4_承重支架_45.SLDPRT")
CASE = os.path.join(D, "e2e_case_45.json")

sys.path.insert(0, TOOLS)
sys.path.insert(0, os.path.join(TOOLS, "physics"))
import material_db as M  # noqa: E402

PAIRS = [
    ("45#", "ST_API_4140", "GB σy=355 vs 兜底 4140 σy=655"),
    ("40Cr", "ST_API_4140", "GB σy=785（本轮修正）vs 兜底 655"),
    ("2A12", "AL_7075", "GB_SCAN σy=275（本轮修正）vs 兜底 503"),
    ("Q235", "ST_API_S235", "GB σy=235 vs 兜底 S235 σy=235（应相同）"),
]

print("=" * 88)
print("差分实验：material_db 里的期望值（解析真值）")
print("=" * 88)
for a, b, _n in PAIRS:
    ma, mb = M.get_material(a), M.get_material(b)
    print("  %-14s σy=%-7s (%s)" % (a, (ma or {}).get("yield_mpa"),
                                    ((ma or {}).get("field_provenance") or {}).get(
                                        "yield_mpa", {}).get("source")))
    print("  %-14s σy=%-7s" % (b, (mb or {}).get("yield_mpa")))

rows = []
for a, b, note in PAIRS:
    print("\n" + "=" * 88)
    print("### %s  →  %s   (%s)" % (a, b, note))
    for grade in (a, b):
        out = os.path.join(D, "diff_%s.json" % grade.replace("#", "").replace("/", "_"))
        p = subprocess.run(
            [PY, "physics_bridge.py", "optimize", CASE, "--part", PART,
             "--max-iter", "1", "--material", grade],
            cwd=TOOLS, capture_output=True)
        t = p.stdout.decode("utf-8", "replace")
        i = t.find("{")
        try:
            j, _ = json.JSONDecoder().raw_decode(t[i:])
        except Exception as e:
            print("   解析失败 %s: %r" % (grade, e))
            continue
        open(out, "w", encoding="utf-8").write(t)
        f = j.get("final_report") or {}
        mat = f.get("material") or {}
        fr = f.get("fea_result") or {}
        sy = (f.get("load_case_summary") or {}).get("yield_strength_mpa")
        smax = fr.get("max_von_mises_mpa")
        sf = f.get("safety_factor")
        rec = {"grade": grade, "material_id": mat.get("id"),
               "sigma_y": sy, "sigma_max": smax, "sf": sf,
               "resolution": f.get("material_resolution"),
               "provenance": (f.get("material") or {}).get("field_provenance"),
               "values_source": (f.get("material") or {}).get("values_source"),
               "raw_file": out}
        rows.append(rec)
        print("   [%s] material.id=%-14s σy=%-7s σmax=%-8s SF=%s"
              % (grade, mat.get("id"), sy, smax, sf))
        print("        resolution.source = %s"
              % (rec["resolution"] or {}).get("source"))
        _fp = (rec["provenance"] or {}).get("yield_mpa")
        print("        field_provenance[yield_mpa] = %s"
              % json.dumps(_fp, ensure_ascii=False))
    _ra = [r for r in rows if r["grade"] == a]
    _rb = [r for r in rows if r["grade"] == b]
    if _ra and _rb:
        _sa, _sb = _ra[-1]["sigma_y"], _rb[-1]["sigma_y"]
        _fa, _fb = _ra[-1]["sf"], _rb[-1]["sf"]
        print("   ── 对比 ──")
        print("      σy:  %s (%s)  vs  %s (%s)" % (a, _sa, b, _sb))
        if _sa and _sb:
            print("      σy 相对差 = %.1f%%" % (abs(_sa - _sb) / _sb * 100))
        print("      SF:  %s  vs  %s" % (_fa, _fb))
        if _fa and _fb:
            print("      SF 相对差 = %.1f%%" % (abs(_fa - _fb) / _fb * 100))
        print("      => 报告用的是 %s"
              % ("各自的真实值（国标值优先）" if _sa != _sb
                 else "同一个值（★差分无效）"))

with open(os.path.join(D, "out_diff.json"), "w", encoding="utf-8") as fh:
    json.dump(rows, fh, ensure_ascii=False, indent=1, default=str)
print("\n[saved] out_diff.json")
