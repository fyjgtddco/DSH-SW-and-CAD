# -*- coding: utf-8 -*-
"""E4 复验 BUG-B：比较两次 physics-optimize 的输出，判断 --material 是否生效。"""
import hashlib
import json
import os
import re
import sys

D = r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools\_verify_e4"


def load_json_part(path):
    t = open(path, "r", encoding="utf-8").read()
    i = t.find("{")
    if i < 0:
        return None, t
    try:
        return json.loads(t[i:]), t
    except Exception:
        # 截到最后一个 } 再试
        j = t.rfind("}")
        try:
            return json.loads(t[i:j + 1]), t
        except Exception as e:
            return None, t


A = os.path.join(D, "opt_45_out.json")        # 不带 --material
B = os.path.join(D, "opt_45_4140.json")       # 带 --material ST_API_4140

ja, ta = load_json_part(A)
jb, tb = load_json_part(B)

print("=" * 78)
print("BUG-B 复验：sw_bridge.py physics-optimize ... --material ST_API_4140")
print("=" * 78)
print("  A 文件（不带 --material）  bytes=%d sha1=%s"
      % (len(ta), hashlib.sha1(ta.encode()).hexdigest()[:16]))
print("  B 文件（带 --material）    bytes=%d sha1=%s"
      % (len(tb), hashlib.sha1(tb.encode()).hexdigest()[:16]))
print("  【原始 stdout 完全相同】 =", ta == tb)

if ja and jb:
    fa = (ja.get("final_report") or {})
    fb = (jb.get("final_report") or {})
    for tag, j, f in (("A 不带 --material", ja, fa), ("B 带 --material", jb, fb)):
        m = f.get("material") or {}
        mr = f.get("material_resolution") or f.get("material_source") or {}
        print("\n  ── %s ──" % tag)
        print("     load_case_summary.yield_strength_mpa = %s"
              % (f.get("load_case_summary") or {}).get("yield_strength_mpa"))
        print("     material.id / name  = %s / %s" % (m.get("id"), m.get("name")))
        print("     material.yield      = %s" % m.get("yield_strength_mpa"))
        print("     material_resolution = %s"
              % json.dumps(f.get("material_resolution"), ensure_ascii=False, default=str)[:400])
        sf = f.get("safety_factor") or (f.get("fea_result") or {}).get("safety_factor")
        print("     safety_factor       = %s" % sf)
        fr = f.get("fea_result") or {}
        print("     max_von_mises_mpa   = %s" % fr.get("max_von_mises_mpa"))
    print("\n  【关键判定】B 里 material 是否变成 ST_API_4140 / σy 655 ?")
    _mb = (fb.get("material") or {})
    _syb = (fb.get("load_case_summary") or {}).get("yield_strength_mpa")
    print("     B material.id = %s | B σy = %s" % (_mb.get("id"), _syb))
    if str(_mb.get("id")) == "ST_API_4140" or _syb == 655.0:
        print("     => --material 生效")
    else:
        print("     => ★--material 被【静默忽略】（仍是载荷/零件材料）")

# 搜索是否任何地方出现 ST_API_4140
print("\n  文本中是否出现 'ST_API_4140'：A=%s  B=%s"
      % ("ST_API_4140" in ta, "ST_API_4140" in tb))
print("  文本中是否出现 '4140'：A=%s  B=%s"
      % ("4140" in ta, "4140" in tb))
