# -*- coding: utf-8 -*-
"""E3 Step3c-refined: per-FIELD mislabel detection.

The 'detail' string is shared across all 5 fields of a material, so we must
extract only the sentence that speaks about the FIELD IN QUESTION.
"""
import sys, json, io, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
ROOT = r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools"
raw = json.load(open(ROOT + r"\physics\gb_materials.json", encoding="utf-8"))["materials"]

CLAIMABLE = ("GB_ORIGINAL", "GB_GRADE_DEF")
SY_MARK = ("σy", "Rp0.2", "屈服", "Re≥", "Re ")
SB_MARK = ("σb", "抗拉", "Rm")
HB_MARK = ("手册", "估算", "AZOM", "机械数控人", "速查", "≈0.7×")

def sentences(txt):
    return [s.strip() for s in re.split(r"[;；]", txt) if s.strip()]

print("=" * 118)
print("STEP 3c-refined: 逐字段核对 source_layer 与该字段自述是否矛盾")
print("  判据：字段被判为【可宣称国标】(GB_ORIGINAL/GB_GRADE_DEF)，")
print("        但描述该字段的那句话自己承认来自手册/估算 → 标错层级")
print("=" * 118)
bad = []
for mid, m in raw.items():
    for f, marks in (("yield_mpa", SY_MARK), ("uts_mpa", SB_MARK)):
        e = (m.get("gb_data") or {}).get(f)
        if not isinstance(e, dict):
            continue
        lay, det = e.get("source_layer"), e.get("detail") or ""
        if lay not in CLAIMABLE:
            continue
        rel = [s for s in sentences(det) if any(k in s for k in marks)]
        hit = [s for s in rel if any(h in s for h in HB_MARK)]
        # exclude the case where the handbook mention is about E/nu/rho in the same sentence
        real = []
        for s in hit:
            # strip a trailing "E/ν/ρ=手册(...)" clause before judging
            s2 = re.sub(r"E/ν/ρ\s*=\s*手册[^;；]*", "", s)
            s2 = re.sub(r"E/ν/ρ[^;；]*手册[^;；]*", "", s2)
            if any(h in s2 for h in HB_MARK):
                real.append(s)
        status = "★ 标错层级" if real else "OK"
        if real:
            bad.append((mid, f, lay, real))
        print(f"  {mid:<14}{f:<12}layer={lay:<14}{status}")
        if real:
            for s in real:
                print(f"        └ 自述: {s}")

print()
print("=" * 118)
print("汇总：标错层级的字段（%d 个）" % len(bad))
print("=" * 118)
for mid, f, lay, s in bad:
    print(f"  {mid}.{f}  layer={lay}  ← 实际来源见自述")
