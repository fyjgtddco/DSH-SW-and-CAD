# -*- coding: utf-8 -*-
"""E4 任务四：18 牌号在【真实流程】中的可用性总表（独立复算，不依赖 sw_bridge）。"""
import json
import os
import sys

TOOLS = r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools"
sys.path.insert(0, TOOLS)
sys.path.insert(0, os.path.join(TOOLS, "physics"))
import material_db as M  # noqa: E402
import swapi  # noqa: E402
import defense_gate as DG  # noqa: E402

IDS = sorted(M.GB_MATERIALS.keys())
rows = []
for mid in IDS:
    r = M.get_material(mid) or {}
    ok, _e, why = M.gb_material_ready(mid)
    # swapi 合法性
    _canon = None
    for _c, _i in swapi.COMMON_MATERIALS.items():
        _al = [_c] + [_i.get("name") or ""] + list(_i.get("aliases") or [])
        _nm = r.get("name_cn") or ""
        _short = _nm.split("（")[0]
        if _short and any(str(a) == _short for a in _al):
            _canon = _c
            break
    rows.append({
        "id": mid, "cn": r.get("name_cn"), "short": (r.get("name_cn") or "").split("（")[0],
        "sigma_y": r.get("yield_mpa"), "sigma_b": r.get("uts_mpa"),
        "E": r.get("E_mpa"), "nu": r.get("nu"), "rho": r.get("density_kg_m3"),
        "ready": ok, "why": why,
        "provenance_y": ((r.get("field_provenance") or {}).get("yield_mpa") or {}).get("source"),
        "provenance_E": ((r.get("field_provenance") or {}).get("E_mpa") or {}).get("source"),
        "swapi_canon": _canon,
        "equivalent_id": r.get("equivalent_id"),
        "consistency_guard": r.get("consistency_guard"),
    })

print("=" * 130)
print("18 个 GB 牌号：数据齐备性 + swapi 覆盖 + 赋材实测结果")
print("=" * 130)
hdr = ("%-14s %-9s %-9s %-8s %-7s %-8s %-6s %-10s %-14s %-9s"
       % ("ID", "σy", "σb", "E", "ν", "ρ", "就绪", "σy来源", "swapi牌号", "赋材实测"))
print(hdr)
print("-" * 130)

# 合并实机赋材结果
_live = {}
for fn in os.listdir(os.path.join(TOOLS, "_verify_e4", "matprobe")):
    if fn.startswith("R_") and fn.endswith(".json"):
        j = json.load(open(os.path.join(TOOLS, "_verify_e4", "matprobe", fn), encoding="utf-8"))
        _live[j.get("grade")] = j

for r in rows:
    _sh = r["short"]
    _lv = _live.get(_sh) or {}
    _mr = _lv.get("material_result") or {}
    _mp = _lv.get("massprops") or {}
    _dens = _mp.get("density_kg_m3")
    if not _lv:
        _stat = "(未测)"
    elif _mr.get("ok") and _dens and abs(float(_dens) - 1000.0) > 1:
        _stat = "OK ρ=%.0f" % float(_dens)
    else:
        _stat = "失败 ρ=%s" % (_dens if _dens is None else "%.0f" % float(_dens))
    print("%-14s %-9s %-9s %-8s %-7s %-8s %-6s %-10s %-14s %-9s"
          % (r["id"], r["sigma_y"], r["sigma_b"], r["E"], r["nu"], r["rho"],
             "是" if r["ready"] else "否", r["provenance_y"],
             r["swapi_canon"] or "★无", _stat))

print("\n" + "=" * 130)
print("不可用牌号（material_db 判定 ready=False）")
print("=" * 130)
for r in rows:
    if not r["ready"]:
        print("  %-14s %s" % (r["id"], r["why"]))

print("\n" + "=" * 130)
print("物理自洽性检查")
print("=" * 130)
print("  σb < σy 的牌号（物理不可能）:")
_bad = [r["id"] for r in rows if r["sigma_y"] and r["sigma_b"]
        and float(r["sigma_b"]) < float(r["sigma_y"])]
print("    ", _bad or "无")
print("  被一致性守卫撤回的牌号:")
for r in rows:
    if r["consistency_guard"]:
        print("     %s: %s" % (r["id"], r["consistency_guard"]))

print("\n" + "=" * 130)
print("E/ν/ρ 来源统计（任务四.2）")
print("=" * 130)
from collections import Counter
_pk = {"E_mpa": "provenance_E", "nu": "provenance_E", "density_kg_m3": "provenance_E"}
for _mid in IDS:
    _r = M.get_material(_mid) or {}
    _fp = _r.get("field_provenance") or {}
    for f in ("E_mpa", "nu", "density_kg_m3"):
        _pk[f] = ((_fp.get(f) or {}).get("source"))
for f in ("E_mpa", "nu", "density_kg_m3"):
    c = Counter(r["provenance_" + {"E_mpa": "E", "nu": "nu", "density_kg_m3": "rho"}[f]]
                for r in rows)
    print("  %-16s %s" % (f, dict(c)))

with open(os.path.join(TOOLS, "_verify_e4", "out_availability.json"), "w",
          encoding="utf-8") as fh:
    json.dump(rows, fh, ensure_ascii=False, indent=1, default=str)
print("\n[saved] out_availability.json")
