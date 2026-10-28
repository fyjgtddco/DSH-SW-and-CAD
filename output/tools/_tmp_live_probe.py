
# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools")
import pythoncom, win32com.client
pythoncom.CoInitialize()
import swapi
G = swapi._SLDWORKS_TLB_GUID
try:
    mod = swapi.get_early_module(None)
    print("early module:", mod)
except Exception as e:
    print("early err", e); mod=None
sw = win32com.client.Dispatch("SldWorks.Application")
try:
    print("rev", sw.RevisionNumber)
except Exception as e:
    print("rev err", e)
docs = []
try:
    docs = list(sw.GetDocuments or [])
except Exception as e:
    print("docs err", e)
print("docs:", len(docs))
asm = None
for d in docs:
    try:
        t = d.GetTitle
        ty = d.GetType
        print("  doc:", t, ty)
        if ty == 2 and asm is None:
            asm = d
    except Exception as e:
        print("  doc err", e)
if asm is None:
    print("NO ASSEMBLY OPEN - skip")
    sys.exit(0)
comps = list(asm.GetComponents(False) or [])
print("comps:", len(comps))
c = comps[0]
print("comp:", c.Name2)
for label, fn in (("late GetModelDoc2", lambda: c.GetModelDoc2()),
                  ("late GetBody2", lambda: c.GetBody2()),
                  ("late GetBodies2(0)", lambda: c.GetBodies2(0))):
    try:
        r = fn()
        print(label, "->", type(r), (len(list(r)) if isinstance(r, tuple) else r))
    except Exception as e:
        print(label, "ERR", repr(e))
if mod:
    ci = mod.IComponent2(c._oleobj_)
    for label, fn in (("early GetModelDoc2", lambda: ci.GetModelDoc2()),
                      ("early GetBody", lambda: ci.GetBody()),
                      ("early GetBodies2(0)", lambda: ci.GetBodies2(0)),
                      ("early GetBodies(0)", lambda: ci.GetBodies(0))):
        try:
            r = fn()
            if label.startswith("early GetBodies"):
                r = list(r) if r is not None else None
                print(label, "->", type(r), len(r) if r else r)
            else:
                print(label, "->", type(r), r)
        except Exception as e:
            print(label, "ERR", repr(e))
