
import os, sys
from win32com.client import gencache
G="{83A33D31-27C5-11CE-BFD4-00400513BB57}"
m=None
try:
    m = gencache.GetModuleForTypelib(G,0,33,0)
    print("module:", m)
except Exception as e:
    print("getmod err", e)
if m is None:
    try:
        m = gencache.EnsureModule(G,0,33,0)
        print("ensured:", m)
    except Exception as e:
        print("ensure err", e)
if m:
    print("file:", getattr(m,'__file__',None))
    for iface in ("IAssemblyDoc","IModelDoc2","IComponent2","IPartDoc","IMateData","IMate","IExtension","ISelectionMgr"):
        c = getattr(m, iface, None)
        print("===", iface, c)
        if c is None: continue
        for name in ("CreateMate","AddMate5","AddMate3","GetBodies","GetBodies2","GetModelDoc2","GetBody2","GetMates","SelectByID2","CreateLockMate","GetInterferenceCount","Save","GetTotalTransform"):
            f = getattr(c, name, None)
            if f is None: continue
            print("  ---", name)
            print("   ", (getattr(f,'__doc__','') or '')[:900].replace("\n"," | "))
