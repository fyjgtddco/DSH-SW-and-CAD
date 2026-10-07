# -*- coding: utf-8 -*-
"""E4 复验 BUG-A / BUG-D：对真实零件跑材料防线 check_material_attestation。

BUG-A 关注：赋材失败（密度=1000）时防线是否【误放行】。
BUG-D 关注：走完正确流程是否仍被【跨标准体系替代】BLOCK。
"""
import json
import os
import sys

TOOLS = r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools"
sys.path.insert(0, TOOLS)
sys.path.insert(0, os.path.join(TOOLS, "physics"))
import defense_gate as DG  # noqa: E402

TARGETS = sys.argv[1:] or []
if not TARGETS:
    _d = os.path.join(TOOLS, "_verify_e4", "bugA_parts")
    TARGETS = [os.path.join(_d, f) for f in sorted(os.listdir(_d))
               if f.upper().endswith(".SLDPRT")]

print("=" * 78)
print("check_material_attestation 逐零件")
print("=" * 78)
out = []
for p in TARGETS:
    r = DG.check_material_attestation(p)
    # 读凭据做交叉核对
    _ap = p + ".material.json"
    _att = {}
    if os.path.exists(_ap):
        try:
            _att = json.load(open(_ap, "r", encoding="utf-8"))
        except Exception:
            pass
    rec = {"part": os.path.basename(p), "verdict": r}
    out.append(rec)
    print("\n── %s" % os.path.basename(p))
    print("   防线判定 ok=%s level=%s" % (r.get("ok"), r.get("level")))
    print("   reasons = %s" % json.dumps(r.get("reasons"), ensure_ascii=False))
    print("   凭据实测密度 density_kg_m3        = %s" % _att.get("density_kg_m3"))
    print("   凭据期望密度 expected_density_kg_m3 = %s" % _att.get("expected_density_kg_m3"))
    print("   凭据 ok=%s  material_applied=%s" % (_att.get("ok"), _att.get("material_applied")))
    print("   attested_density_kg_m3 = %s | attested_family = %s"
          % (_att.get("attested_density_kg_m3"), _att.get("attested_family")))
    if r.get("attestation"):
        print("   defense 读到: %s" % json.dumps(r["attestation"], ensure_ascii=False))
    for _k in ("density_deviation_pct", "yield_deviation_pct",
               "material_density_mismatch", "material_yield_mismatch",
               "cross_standard", "material_substitution",
               "material_mismatch_note", "material_substitution_note", "hint"):
        if _k in r:
            print("   %-26s = %s" % (_k, json.dumps(r[_k], ensure_ascii=False)))

with open(os.path.join(TOOLS, "_verify_e4", "out_bugAD_defense.json"), "w",
          encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1, default=str)
print("\n[saved] out_bugAD_defense.json")
