# -*- coding: utf-8 -*-
"""正向验证：若国标数值【已补全】，系统是否自动优先用国标数值并标记 values_from_gb=True。

不修改用户的 gb_materials.json —— 用 DSH_GB_MATERIALS 指向临时副本。
"""
import sys, os, json, copy, shutil, subprocess

TOOLS = r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools"
SRC = os.path.join(TOOLS, "physics", "gb_materials.json")
TMP = os.path.join(TOOLS, "_verify_out", "gb_materials_FILLED.json")

d = json.load(open(SRC, encoding="utf-8"))
# 只为 Q235 填入一组"国标原文"数值（仅存在于临时副本）
d["materials"]["GB_Q235"].update({
    "E_mpa": 206000, "nu": 0.30, "yield_mpa": 235, "uts_mpa": 370,
    "density_kg_m3": 7850.0,
})
# GCr15 也填上，验证 pending 牌号补全后即可算
d["materials"]["GB_GCr15"].update({
    "E_mpa": 208000, "nu": 0.30, "yield_mpa": 1700, "uts_mpa": 2000,
    "density_kg_m3": 7810.0,
})
json.dump(d, open(TMP, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("已写出临时副本（未改动用户文件）:", TMP)
print("用户文件 MD5 未变（下面对比）")

CODE = r'''
import sys, os, json
sys.path.insert(0, r"%(tools)s")
sys.path.insert(0, r"%(phys)s")
import material_db as mdb
import workflow_gate as wg
st = mdb.gb_materials_status()
print("STATUS:", json.dumps({k: st[k] for k in
      ("loaded","identity_count","ready_count","pending_count")}, ensure_ascii=False))
for nm in ("Q235", "GCr15"):
    m = wg._material_for(nm + " 碳钢" if nm == "Q235" else nm, "")
    print("%%s -> E=%%s sy=%%s rho=%%s values_source=%%r values_from_gb=%%s values_pending=%%s"
          %% (nm, m.get("youngs_modulus_mpa"), m.get("yield_strength_mpa"),
             m.get("density_kg_m3"), m.get("values_source"),
             m.get("values_from_gb"), m.get("values_pending")))
    print("   source =", m.get("source"))
import load_case as lc
c = {"schema_version":"1.0","meta":{"problem_id":"P","title":"p"},
     "design_domain":{"bounds":{"x_min":0,"x_max":100,"y_min":0,"y_max":60,"z_min":0,"z_max":10}},
     "material": wg._material_for("GCr15",""), "spatial_selectors":[],
     "boundary_conditions":[], "loads":[], "acceptance":{}}
try:
    r = lc.validate_load_case(c, strict=True)
    print("GCr15 工况校验 ok=%%s errors=%%s" %% (r.get("ok"), r.get("errors")))
except Exception as e:
    print("GCr15 工况校验 !! 崩溃 %%s: %%s" %% (type(e).__name__, e))
''' % {"tools": TOOLS, "phys": os.path.join(TOOLS, "physics")}

for label, env_extra in (("① 用【原始】gb_materials.json（国标数值待补全）", {}),
                         ("② 用【已补全国标数值】的临时副本", {"DSH_GB_MATERIALS": TMP})):
    print()
    print("=" * 78)
    print("### " + label)
    print("=" * 78)
    _env = dict(os.environ)
    _env["PYTHONIOENCODING"] = "utf-8"
    _env.update(env_extra)
    if not env_extra:
        _env.pop("DSH_GB_MATERIALS", None)
    p = subprocess.run([r"C:\Users\j1877\AppData\Local\Programs\Python\Python313\python.exe",
                        "-c", CODE], env=_env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    print(p.stdout.strip())
    if p.stderr.strip():
        print("[stderr]", p.stderr.strip()[-800:])

import hashlib
print()
print("=" * 78)
_h = hashlib.md5(open(SRC, "rb").read()).hexdigest()
print("用户 gb_materials.json 的 MD5 =", _h)
print("（应仍为 B757B8C6AA1BD2B27EF9C86D183F3D8B —— 未被本次验证改动）")
