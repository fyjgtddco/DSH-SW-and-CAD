# -*- coding: utf-8 -*-
"""实机验证（子任务 B）：打开现成装配体，用早期绑定 create_mate /
create_lock_mate 各建一个配合，并验证 BUG-12 的组件文档/实体访问。
只读不改模型（加完配合后不保存）。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) or ".")
import pythoncom, win32com.client
import swapi

ASM = r"C:\Users\j1877\Desktop\test\20261027_171033_17A0C9D5\parts\DSH_小车装配体.SLDASM"

pythoncom.CoInitialize()
sw = win32com.client.Dispatch("SldWorks.Application")
print("SW rev:", sw.RevisionNumber)
print("early available:", swapi.is_early_available(), swapi.early_status())

err = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
warn = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
doc = sw.OpenDoc6(ASM, 2, 1, "", err, warn)
if doc is None:
    print("OPEN FAILED err=%s warn=%s" % (err.value, warn.value))
    sys.exit(2)
m = swapi.SWModel(sw, doc)
print("title:", doc.GetTitle)

comps = m._assembly_components()
print("components:", [str(c.Name2) for c in comps])
if len(comps) < 2:
    print("SKIP: 组件不足 2 个")
    sys.exit(3)

# ── BUG-12 验证：组件文档 / 实体 ────────────────────────────────────────
c0 = comps[0]
print("--- BUG-12 ---")
late_md = None
try:
    late_md = c0.GetModelDoc2()
except Exception as e:
    late_md = "late-error:%r" % (e,)
print("late  GetModelDoc2 ->", type(late_md), late_md if isinstance(late_md, str) else "OK")
md = m._component_doc(c0)
print("swapi _component_doc ->", "OK" if md is not None else "None")
bodies = m._component_bodies(c0)
print("swapi _component_bodies -> %d 个实体" % len(bodies))
face, area = m._best_planar_face(c0)
print("swapi _best_planar_face ->", "FACE" if face is not None else "None",
      "area=%.1f mm2" % (area * 1e6))

# ── create_mate：同心（两个组件）────────────────────────────────────────
print("--- create_mate(concentric) ---")
r1 = m.create_mate("concentric", (str(comps[0].Name2), str(comps[1].Name2)))
print("ok=%s used_api=%s selections=%s mate=%s err=%s" % (
      r1.get("ok"), r1.get("used_api"), r1.get("selections"),
      r1.get("mate_name"), r1.get("error")))

# ── create_lock_mate：锁定 2 个组件 ─────────────────────────────────────
print("--- create_lock_mate ---")
r2 = m.create_lock_mate((str(comps[0].Name2), str(comps[1].Name2)))
print("ok=%s used_api=%s mate=%s err=%s" % (
      r2.get("ok"), r2.get("used_api"), r2.get("mate_name"), r2.get("error")))

print("--- create_mate(distance) ---")
r3 = m.create_mate("distance", (str(comps[0].Name2), str(comps[1].Name2)),
                   distance_mm=10.0)
print("ok=%s used_api=%s mate=%s err=%s" % (
      r3.get("ok"), r3.get("used_api"), r3.get("mate_name"), r3.get("error")))

# 不保存：只验证 API 能建起来
print("DONE (未保存)")
