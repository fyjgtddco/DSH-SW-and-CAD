
# -*- coding: utf-8 -*-
"""【Bug-37/42/43 回归测试】验证拓扑判定能正确识别"薄壁/小孔/方向偏"的成功切除。"""
import sys, os, types
sys.stdout.reconfigure(encoding='utf-8')
# stub pywin32
for n in ("pythoncom","win32com","win32com.client","win32con"):
    sys.modules[n]=types.ModuleType(n)
sys.modules["win32com"].client=sys.modules["win32com.client"]
sys.modules["win32com.client"].VARIANT=lambda *a,**k: None
sys.modules["win32com.client"].dynamic=types.SimpleNamespace(Dispatch=lambda *a: None)
sys.modules["win32com.client"].gencache=types.SimpleNamespace(EnsureDispatch=lambda *a: None)
sys.modules["pythoncom"].VT_DISPATCH=9; sys.modules["pythoncom"].VT_ARRAY=8192
sys.modules["pythoncom"].VARIANT=lambda *a,**k: None
TOOLS=r'C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools'
sys.path.insert(0, TOOLS)
import swapi

PASS=[];FAIL=[]
def check(n,c,d=""):
    (PASS if c else FAIL).append(n); print(("  [PASS] " if c else "  [FAIL] ")+n+(("  -> "+str(d)) if d else ""))

# 直接用 SWModel 实例的方法（不连 SW）
m = object.__new__(swapi.SWModel)

print("=== Bug-37: 薄壁件 Ø6 孔穿 2mm 壁（去料率 1.97%）===")
before = {"volume_mm3": 100000.0, "face_count": 6, "edge_count": 12,
          "body_count": 1, "bbox_mm": [84.0, 59.0, 29.0]}
after  = {"volume_mm3": 98030.0, "face_count": 8, "edge_count": 16,   # 新增内孔面
          "body_count": 1, "bbox_mm": [84.0, 59.0, 29.0]}
ok, reason, det = m._cut_geometry_verdict(before, after, through=True)
check("薄壁小孔判成功", ok is True, reason)
check("记录去料率 1.97%", abs(det["removed_pct"]-1.97) < 0.1, det["removed_pct"])

print("\n=== Bug-42/43: 方向偏但已贯穿（去料率 0.7%）===")
before2 = {"volume_mm3": 50000.0, "face_count": 10, "edge_count": 20,
           "body_count": 1, "bbox_mm": [40.0, 36.0, 22.0]}
after2  = {"volume_mm3": 49650.0, "face_count": 12, "edge_count": 24,
           "body_count": 1, "bbox_mm": [40.0, 36.0, 22.0]}
ok2, reason2, det2 = m._cut_geometry_verdict(before2, after2, through=True)
check("方向偏但已贯穿判成功", ok2 is True, reason2)
check("记录去料率 0.7%", abs(det2["removed_pct"]-0.7) < 0.05, det2["removed_pct"])

print("\n=== 真失败必须仍被拒（防放宽过度）===")
# 体积完全没变
ok3, r3, _ = m._cut_geometry_verdict(before, dict(before), through=True)
check("体积未减小 → 判失败", ok3 is False, r3)
# 体积微减但无任何拓扑变化、非贯穿 → 疑似失败
after4 = dict(before); after4["volume_mm3"] = 100000.0 - 0.0000001
ok4, r4, _ = m._cut_geometry_verdict(before, after4, through=False)
check("无拓扑变化且非贯穿 → 判失败", ok4 is False, r4)
# 面数减少（异常）
after5 = {"volume_mm3": 99000.0, "face_count": 4, "edge_count": 8,
          "body_count": 1, "bbox_mm": [84.0, 59.0, 29.0]}
ok5, r5, _ = m._cut_geometry_verdict(before, after5, through=False)
check("面数减少且无其它变化 → 判失败", ok5 is False, r5)

print("\n=== 实体数变化（切断）应判成功 ===")
after6 = {"volume_mm3": 99000.0, "face_count": 6, "edge_count": 12,
          "body_count": 2, "bbox_mm": [84.0, 59.0, 29.0]}
ok6, r6, _ = m._cut_geometry_verdict(before, after6, through=False)
check("实体数变化判成功", ok6 is True, r6)

print("\n=== 包围盒改变（切掉外形）应判成功 ===")
after7 = {"volume_mm3": 99000.0, "face_count": 6, "edge_count": 12,
          "body_count": 1, "bbox_mm": [84.0, 59.0, 20.0]}
ok7, r7, _ = m._cut_geometry_verdict(before, after7, through=False)
check("包围盒改变判成功", ok7 is True, r7)

print("\nPASS=%d FAIL=%d" % (len(PASS),len(FAIL)))
sys.exit(1 if FAIL else 0)
