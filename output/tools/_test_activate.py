# -*- coding: utf-8 -*-
import win32com.client
import pythoncom
pythoncom.CoInitialize()
sw = win32com.client.dynamic.Dispatch('SldWorks.Application')

# Enumerate all docs
print("=== All open documents ===")
for i in range(sw.GetDocumentCount):
    try:
        d = sw.GetDocumentByIndex(i)
        if d:
            path = d.GetPathName or "(unsaved)"
            title = d.GetTitle
            print(f"  [{i}] {title} | {path}")
    except Exception as e:
        print(f"  [{i}] error: {e}")

# Try to activate by name
target = "DSH_正方体长方体组合体 - 图纸1"
try:
    # Method 1: GetDocument with type
    d = sw.GetDocument(target, 3)  # type 3 = drawing
    print(f"\nGetDocument('{target}', 3): {'OK' if d else 'None'}")
    if d:
        print("  Activating...")
        sw.ActivateDoc(target)
        print(f"  Active doc now: {sw.ActiveDoc.GetTitle}")
except Exception as e:
    print(f"GetDocument error: {e}")

# Method 2: Try ActivateDoc2 with the doc title
try:
    sw.ActivateDoc2(target, False, 0)
    print(f"\nActivateDoc2('{target}'): OK")
    print(f"  Active doc: {sw.ActiveDoc.GetTitle}")
except Exception as e:
    print(f"ActivateDoc2 error: {e}")
