
# -*- coding: utf-8 -*-
"""【Bug-34 回归测试】尺寸 API 与几何降级清单。"""
import sys, os, types
sys.stdout.reconfigure(encoding='utf-8')
for n in ("pythoncom","win32com","win32com.client","win32con"):
    sys.modules[n]=types.ModuleType(n)
sys.modules["win32com"].client=sys.modules["win32com.client"]
sys.modules["win32com.client"].VARIANT=lambda *a,**k: None
sys.modules["win32com.client"].dynamic=types.SimpleNamespace(Dispatch=lambda *a: None)
sys.modules["win32com.client"].gencache=types.SimpleNamespace(EnsureDispatch=lambda *a: None)
sys.modules["pythoncom"].VT_DISPATCH=9; sys.modules["pythoncom"].VT_ARRAY=8192
sys.modules["pythoncom"].VARIANT=lambda *a,**k: None
# ── 【换机修复】不要写死作者本机路径：按本文件位置推导 tools 目录 ────────
# 旧写法 TOOLS = r'C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools'
# 换一台电脑（微机房/别人 clone）直接 FileNotFoundError，回归测试全废。
# 现按 __file__ 推导；仍可用 argv[1] 或 DSH_ENGINEERING_ROOT 覆盖。
TOOLS = (sys.argv[1] if len(sys.argv) > 1 and os.path.isdir(sys.argv[1])
         else os.environ.get('DSH_ENGINEERING_ROOT') or
         os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOLS)
import swapi

PASS=[];FAIL=[]
def check(n,c,d=""):
    (PASS if c else FAIL).append(n); print(("  [PASS] " if c else "  [FAIL] ")+n+(("  -> "+str(d)) if d else ""))

print("=== Bug-34: swapi 现在有尺寸 API ===")
check("有 add_dimension", hasattr(swapi.SWModel, "add_dimension"))
check("有 add_key_dimensions", hasattr(swapi.SWModel, "add_key_dimensions"))
check("有 annotate_geometry_fallback", hasattr(swapi.SWModel, "annotate_geometry_fallback"))

print("\n=== Bug-34: 无激活草图时给出明确错误（不静默）===")
m = object.__new__(swapi.SWModel)
class _Sk:
    def __init__(self): self.ActiveSketch=None
    def AddDimension2(self,*a): return None
m.skm=_Sk()
r = m.add_dimension(0,0,10,0,value_mm=10)
check("无草图 → ok=False", r["ok"] is False, r.get("error","")[:50])
check("错误信息可操作", "begin_sketch" in (r.get("error") or ""), r.get("error"))

print("\n=== Bug-34: 有草图时走 AddDimension2 并设值 ===")
class _Dim:
    def __init__(self): self.val=None
    def SetSystemValue3(self,v,c,d): self.val=v
class _Sk2:
    def __init__(self): self.ActiveSketch=object(); self._d=_Dim()
    def AddDimension2(self,*a): return self._d
m2 = object.__new__(swapi.SWModel); m2.skm=_Sk2()
r2 = m2.add_dimension(0,0,100,0,value_mm=100)
check("AddDimension2 成功", r2["ok"] is True, r2.get("api"))
check("尺寸值已驱动", r2.get("value_applied") is True)
check("换算为米(100mm→0.1)", abs(m2.skm._d.val-0.1)<1e-9, m2.skm._d.val)

print("\n=== Bug-34: 几何降级清单（annotate 空转时的兜底）===")
m3 = object.__new__(swapi.SWModel)
m3._bbox_extent_mm = lambda a: [300.0,180.0,120.0][a]
m3._sketch_circle_radii_mm = lambda: [3.0, 5.0]
g = m3.annotate_geometry_fallback()
check("降级清单成功", g["ok"] is True, g.get("error"))
check("含总长/总宽/总高", len([s for s in g["suggestions"] if s["item"].startswith("总")])==3)
check("含孔径建议(Ø6/Ø10)", any(s["item"]=="孔径" for s in g["suggestions"]))
check("说明是几何推算", "几何推算" in g.get("note",""), g.get("note","")[:40])

print("\nPASS=%d FAIL=%d" % (len(PASS),len(FAIL)))
sys.exit(1 if FAIL else 0)
