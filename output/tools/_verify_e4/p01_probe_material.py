# -*- coding: utf-8 -*-
"""E4 复验 01：材料解析探针 —— 复验 BUG-E（身份冒充/物理不可能/密度误标）。只读。"""
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from physics import material_db as M  # noqa: E402

print("=" * 78)
print("material_db 状态")
print("=" * 78)
st = M.gb_materials_status()
for k in ("loaded", "count", "identity_count", "ready_count",
          "equivalent_ready_count", "pending_count"):
    print(f"  {k:26} = {st.get(k)}")
print(f"  file                       = {st.get('file')}")
print(f"  error                      = {st.get('error')}")
print()

FIELDS = ("E_mpa", "nu", "yield_mpa", "uts_mpa", "density_kg_m3")
IDS = sorted(M.GB_MATERIALS.keys())

print("=" * 78)
print("逐牌号：解析结果 + 来源台账（复验 BUG-E1 身份冒充 / E2 物理不可能 / E4 密度误标）")
print("=" * 78)
rows = []
for mid in IDS:
    r = M.get_material(mid)
    if not r:
        print(f"[{mid}] get_material -> None")
        continue
    vfg = r.get("values_from_gb")
    vs = r.get("values_source")
    sy, sb = r.get("yield_mpa"), r.get("uts_mpa")
    # 物理不可能检查
    impossible = (sy is not None and sb is not None and float(sb) < float(sy))
    fp = r.get("field_provenance") or {}
    # 统计来源
    srcs = {f: (fp.get(f) or {}).get("source") for f in FIELDS}
    filled = [f for f in FIELDS if r.get(f) is not None]
    # E1 身份冒充：是否"纯兜底"却报 values_from_gb=True
    pure_fallback = bool(filled) and all(srcs[f] == "EQUIVALENT_FALLBACK" for f in filled)
    rows.append(dict(id=mid, vfg=vfg, pure_fallback=pure_fallback, impossible=impossible,
                     sy=sy, sb=sb, srcs=srcs, vs=vs,
                     guard=r.get("consistency_guard"),
                     eqf=r.get("equivalent_filled"),
                     eqn=r.get("equivalent_name")))
    print(f"\n── {mid} ──")
    print(f"   σy={sy}  σb={sb}  E={r.get('E_mpa')}  ν={r.get('nu')}  ρ={r.get('density_kg_m3')}")
    print(f"   values_from_gb = {vfg}   纯兜底={pure_fallback}   σb<σy物理不可能={impossible}")
    print(f"   values_source  = {vs}")
    print(f"   field_provenance = {json.dumps(srcs, ensure_ascii=False)}")
    if r.get("consistency_guard"):
        print(f"   consistency_guard = {r.get('consistency_guard')}")
    if r.get("equivalent_filled"):
        print(f"   equivalent_filled = {r.get('equivalent_filled')} <- {r.get('equivalent_name')}")

print()
print("=" * 78)
print("汇总")
print("=" * 78)
print("E1 身份冒充（纯兜底但 values_from_gb=True）:")
bad1 = [r["id"] for r in rows if r["pure_fallback"] and r["vfg"]]
print("   ", bad1 or "无 —— 已修")
print("   values_from_gb=True 的牌号:", [r["id"] for r in rows if r["vfg"]])
print("E2 物理不可能（σb < σy）:")
bad2 = [r["id"] for r in rows if r["impossible"]]
print("   ", bad2 or "无 —— 已修")
print("E4 密度来源标注（应区分 HANDBOOK / PROJECT_NATIVE / EQUIVALENT_FALLBACK）:")
from collections import Counter
c = Counter((r["srcs"].get("density_kg_m3") or "None") for r in rows)
print("   ", dict(c))
print("   标为 EQUIVALENT_FALLBACK 的密度:", [r["id"] for r in rows if r["srcs"].get("density_kg_m3") == "EQUIVALENT_FALLBACK"] or "无 —— 已修")
print()
print("不可用牌号（has_props 为 False 且无兜底）:")
for mid in IDS:
    ok, ent, why = M.gb_material_ready(mid)
    if not ok:
        print(f"   {mid}: {why}")

with open(os.path.join(os.path.dirname(__file__), "out_p01_probe.json"), "w", encoding="utf-8") as f:
    json.dump(dict(status=st, rows=rows), f, ensure_ascii=False, indent=1)
print("\n[saved] out_p01_probe.json")
