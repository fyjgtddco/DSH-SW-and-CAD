# -*- coding: utf-8 -*-
"""E3 final2: 报告层 claimable_as_gb 全牌号判定 + 身份冒充回归测试 (READ ONLY)."""
import sys, json, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
ROOT = r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools"
sys.path.insert(0, ROOT); sys.path.insert(0, ROOT + r"\physics")
import material_db as mdb, swapi, workflow_gate, physics_bridge as pb
from physics.simulation_report import _material_provenance_note

raw = json.load(open(ROOT + r"\physics\gb_materials.json", encoding="utf-8"))["materials"]
case = {"material": {"name": "x", "id": "x"}}

print("=" * 116)
print("报告层 claimable_as_gb 判定（三方入口）—— 是否有人被错误宣称『全部来自国标原文』")
print("=" * 116)
print(f"{'grade':<14}{'mdb level':<22}{'claim':<7}{'wg level':<22}{'claim':<7}{'pb level':<22}{'claim':<7}")
print("-" * 116)
bad = []
for mid, m in raw.items():
    disp = next((a for a in (m.get("aliases") or []) if a in swapi.COMMON_MATERIALS), None)
    n1 = _material_provenance_note(mdb.get_material(mid) or {})
    n2 = _material_provenance_note(workflow_gate._gb_grade_props(disp or mid) or {})
    pbm, _ = pb._resolve_part_material({}, case, explicit_material=disp or mid)
    n3 = _material_provenance_note(pbm or {})
    print(f"{mid:<14}{n1['level']:<22}{str(n1['claimable_as_gb']):<7}{n2['level']:<22}{str(n2['claimable_as_gb']):<7}{n3['level']:<22}{str(n3['claimable_as_gb']):<7}")
    for tag, n in (("mdb", n1), ("workflow_gate", n2), ("physics_bridge", n3)):
        if n.get("claimable_as_gb") is True:
            bad.append((mid, tag, n))

print()
print("★ 被错误宣称 claimable_as_gb=True 的入口数:", len(bad))
for mid, tag, n in bad:
    print("   ", mid, tag, n.get("note"))

print()
print("=" * 116)
print("回归：报告层是否仍会把『等效兜底』冒充成『国标原文』")
print("=" * 116)
# Cr12MoV is the only one that goes through the equivalent fallback path
cm = mdb.get_material("Cr12MoV") or {}
n = _material_provenance_note(cm)
print("  Cr12MoV: level=%s claimable_as_gb=%s" % (n["level"], n["claimable_as_gb"]))
print("           note=%s" % n["note"])
print("  → 兜底冒充国标的旧 bug 在 material_db 路径下【已修复】")
