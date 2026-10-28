# -*- coding: utf-8 -*-
import sys, os, traceback
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) or ".")
import pythoncom, win32com.client, swapi
pythoncom.CoInitialize()
sw = win32com.client.Dispatch("SldWorks.Application")
ASM = r"C:\Users\j1877\Desktop\test\20261027_171033_17A0C9D5\parts\DSH_С��װ��.SLDASM"
err = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
warn = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
doc = sw.OpenDoc6(ASM, 2, 1, "", err, warn)
print("doc:", doc.GetTitle if doc else None, err.value)
m = swapi.SWModel(sw, doc)
c = m._assembly_components()[0]
print("comp:", c.Name2)
bodies = m._component_bodies(c)
print("bodies:", len(bodies))
b = bodies[0]
faces = b.GetFaces()
print("faces:", type(faces), len(faces))
for f in list(faces)[:4]:
    try:
        n = f.Normal
        a = float(f.GetArea())
        print("  normal=", list(n), "area_mm2=%.1f" % (a * 1e6))
    except Exception:
        traceback.print_exc()
print("helper ->", m._best_planar_face(c))
