# -*- coding: utf-8 -*-
"""E4 复验：宿主 /defense/sign 的材料凭据字段白名单（判定 BUG-A 修复为何不完整）。

做法：对一份【真实存在的零件】申请签发，把 swapi 声称已提交的字段全部塞进去，
看哪些字段在签名后的凭据里存活。存活=纳入待签体；消失=宿主白名单丢弃。
"""
import json
import os
import sys

TOOLS = r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools"
sys.path.insert(0, TOOLS)
import defense_gate as DG  # noqa: E402

PART = os.path.join(TOOLS, "_verify_e4", "bugA_parts", "DSH_bugA_65Mn.SLDPRT")
if not os.path.exists(PART):
    _d = os.path.join(TOOLS, "_verify_e4", "bugA_parts")
    _c = [os.path.join(_d, f) for f in os.listdir(_d) if f.upper().endswith(".SLDPRT")]
    PART = _c[0] if _c else ""
print("PART =", PART, "| exists =", os.path.exists(PART))

SPEC = {
    "kind": "material",
    "part_path": PART,
    "applied_name": "65Mn",
    "density_kg_m3": 1000.0,
    "source": "probe",
    # ── swapi 声称已提交（期望纳入待签体）的字段 ──
    "ok": False,
    "approximate_match": False,
    "material_mismatch_rejected": True,
    "attested_family": "steel",
    "requested_material": "65Mn",
    "attested_material_name": "65Mn",
    "cross_standard": False,
    "cross_standard_note": "probe-note",
    "expected_yield_mpa": 785.0,
    "expected_density_kg_m3": 7850.0,
    "applied_yield_mpa": 785.0,
    "applied_density_kg_m3": 7850.0,
    "yield_deviation_pct": 0.0,
    # ── P0 修复新引入的字段（BUG-A 的核心）──
    "material_applied": False,
    "material_apply_note": "PROBE: 赋材未生效，实测密度=1000",
    "warnings": ["PROBE: 密度=1000 等同水"],
    "density_kg_m3_measured": 1000.0,
    "save_ok": True,
    "density_before_kg_m3": 1000.0,
}
res = DG.request_signed_credential(SPEC)
print("\nrequest_signed_credential ->")
if not isinstance(res, dict):
    print("  NOT A DICT:", repr(res))
    sys.exit(1)
print("  ok      =", res.get("ok"))
print("  error   =", res.get("error"))
cred = res.get("credential") or {}
print("  credential keys:", sorted(cred.keys()))
print()
print("── 字段存活表（申请 → 凭据）──")
_all = list(SPEC.keys())
for k in _all:
    if k in ("kind", "part_path"):
        continue
    _in = k in cred
    print("  %-28s in_credential=%-5s  value=%s"
          % (k, _in, json.dumps(cred.get(k), ensure_ascii=False, default=str)[:90]))
print()
print("── 凭据里【额外出现】的字段 ──")
for k in sorted(cred.keys()):
    if k not in SPEC:
        print("  %-28s = %s" % (k, json.dumps(cred.get(k), ensure_ascii=False, default=str)[:110]))

with open(os.path.join(TOOLS, "_verify_e4", "out_sign_probe.json"), "w", encoding="utf-8") as f:
    json.dump({"spec": SPEC, "result": res}, f, ensure_ascii=False, indent=1, default=str)
print("\n[saved] out_sign_probe.json")
