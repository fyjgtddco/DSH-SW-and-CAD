# -*- coding: utf-8 -*-
"""E4 复验 BUG-A 关键判定：若凭据【如实】记录实测密度 1000，防线是否拦得住？

做法：复制一个真实零件 → 为该副本申请【宿主签发】的凭据，其中
  density_kg_m3 = 1000（如实实测值）
  ok = True（模拟当前 swapi 的 save_ok 口径）
然后跑 check_material_attestation，看"等同水"告警是否触发、是否 BLOCK。

这直接回答：BUG-A 的修复到底有没有把防线补上。
"""
import json
import os
import shutil
import sys

TOOLS = r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools"
sys.path.insert(0, TOOLS)
import defense_gate as DG  # noqa: E402

SRC = os.path.join(TOOLS, "_verify_e4", "bugA_parts", "DSH_bugA_65Mn.SLDPRT")
DST = os.path.join(TOOLS, "_verify_e4", "bugA_parts", "DSH_probe_water.SLDPRT")
shutil.copy2(SRC, DST)
print("copied ->", DST, "size =", os.path.getsize(DST))

CASES = [
    {"label": "如实1000 + ok=True（模拟当前 swapi 口径）",
     "spec": {"kind": "material", "part_path": DST, "applied_name": "65Mn",
              "density_kg_m3": 1000.0, "source": "probe",
              "ok": True, "requested_material": "65Mn",
              "attested_material_name": "65Mn", "attested_family": "steel",
              "expected_density_kg_m3": 7850.0, "applied_density_kg_m3": 7850.0,
              "approximate_match": False, "material_mismatch_rejected": False,
              "cross_standard": False}},
    {"label": "如实1000 + ok=False（P0 修复的【应然】口径）",
     "spec": {"kind": "material", "part_path": DST, "applied_name": "65Mn",
              "density_kg_m3": 1000.0, "source": "probe",
              "ok": False, "requested_material": "65Mn",
              "attested_material_name": "65Mn", "attested_family": "steel",
              "expected_density_kg_m3": 7850.0, "applied_density_kg_m3": 7850.0,
              "approximate_match": False, "material_mismatch_rejected": False,
              "cross_standard": False}},
]

for c in CASES:
    print("\n" + "=" * 74)
    print("CASE:", c["label"])
    res = DG.request_signed_credential(c["spec"])
    if not (res or {}).get("ok"):
        print("  签发失败:", res)
        continue
    cred = res["credential"]
    DG.write_material_attestation(DST, cred)
    _on_disk = json.load(open(DST + ".material.json", "r", encoding="utf-8"))
    print("  凭据落盘: ok=%s density_kg_m3=%s attested_density_kg_m3=%s signed=%s"
          % (_on_disk.get("ok"), _on_disk.get("density_kg_m3"),
             _on_disk.get("attested_density_kg_m3"), bool(_on_disk.get("_sig"))))
    r = DG.check_material_attestation(DST)
    print("  防线判定: ok=%s level=%s" % (r.get("ok"), r.get("level")))
    print("  reasons =", json.dumps(r.get("reasons"), ensure_ascii=False))
    print("  -> 结论:", "放行(PASS/WARN)" if r.get("ok") else "拦截(BLOCK)")

# 清理
try:
    for _f in (DST, DST + ".material.json"):
        if os.path.exists(_f):
            os.remove(_f)
    print("\n[cleaned] probe part removed")
except Exception as e:
    print("cleanup warn:", e)
