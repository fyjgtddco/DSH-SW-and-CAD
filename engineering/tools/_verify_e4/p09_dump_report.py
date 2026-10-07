# -*- coding: utf-8 -*-
"""E4：把 optimize 报告的关键结论完整 dump 出来（SF/位移/疲劳/几何门禁/材料来源/局限）。"""
import json
import os
import sys

D = r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools\_verify_e4"
f = sys.argv[1] if len(sys.argv) > 1 else os.path.join(D, "opt_45_out.json")
t = open(f, encoding="utf-8").read()
i = t.find("{")
j, _ = json.JSONDecoder().raw_decode(t[i:])

print("=" * 84)
print("文件:", os.path.basename(f))
print("=" * 84)
print("top-level keys:", sorted(j.keys()))
print("ok=%s converged=%s iterations=%s reason=%s"
      % (j.get("ok"), j.get("converged"), j.get("iterations"), j.get("reason")))

fr = j.get("final_report") or {}
print("\n--- final_report keys ---")
print(sorted(fr.keys()))
print("\nrun_id=%s iteration=%s overall=%s" % (fr.get("run_id"), fr.get("iteration"),
                                              fr.get("overall_status") or fr.get("overall")))
print("\n--- load_case_summary ---")
print(json.dumps(fr.get("load_case_summary"), ensure_ascii=False, indent=1, default=str)[:1200])
print("\n--- material ---")
print(json.dumps(fr.get("material"), ensure_ascii=False, indent=1, default=str)[:2500])
print("\n--- material_resolution ---")
print(json.dumps(fr.get("material_resolution"), ensure_ascii=False, indent=1, default=str)[:900])
print("\n--- material_source_info / provenance 类字段 ---")
for k in sorted(fr.keys()):
    if "materi" in k.lower() or "proven" in k.lower() or "source" in k.lower():
        print("  %-28s = %s" % (k, json.dumps(fr[k], ensure_ascii=False, default=str)[:400]))

print("\n--- fea_result ---")
fea = fr.get("fea_result") or {}
print("  keys:", sorted(fea.keys()))
for k in ("method", "backend", "safety_factor", "max_von_mises_mpa",
          "max_displacement_mm", "volume_mm3", "mass_kg", "overall",
          "gates", "geometry_used", "limitations"):
    if k in fea:
        print("  %-24s = %s" % (k, json.dumps(fea[k], ensure_ascii=False, default=str)[:700]))

print("\n--- 顶层 safety_factor 候选位置 ---")
for path in (("safety_factor",), ("fea_result", "safety_factor"),
             ("summary", "safety_factor"), ("result", "safety_factor")):
    cur = fr
    ok = True
    for p in path:
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            ok = False
            break
    print("  %-34s -> %s" % (".".join(path), cur if ok else "<ABSENT>"))

print("\n--- limitations（全部）---")
def walk(o, pre=""):
    if isinstance(o, dict):
        for k, v in o.items():
            if k == "limitations":
                print("  [%s%s] %s" % (pre, k, json.dumps(v, ensure_ascii=False, default=str)))
            walk(v, pre + k + ".")
    elif isinstance(o, list):
        for n, v in enumerate(o):
            walk(v, pre + "[%d]." % n)
walk(fr)

print("\n--- warnings ---")
print(json.dumps(fr.get("warnings"), ensure_ascii=False, indent=1, default=str)[:1500])

print("\n--- 全文搜索 '等效' / 'bbox' / '悬臂' / 'limitation' ---")
for kw in ("等效", "bbox", "悬臂", "limitation", "实心", "包围盒"):
    _n = t.count(kw)
    print("  %-12s 出现 %d 次" % (kw, _n))
