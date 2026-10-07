
# -*- coding: utf-8 -*-
"""【Bug-41 回归测试】参数化齿轮齿廓：几何正确性 + 点数可控。"""
import sys, os, types, math
sys.stdout.reconfigure(encoding='utf-8')
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
m = object.__new__(swapi.SWModel)

PASS=[];FAIL=[]
def check(n,c,d=""):
    (PASS if c else FAIL).append(n); print(("  [PASS] " if c else "  [FAIL] ")+n+(("  -> "+str(d)) if d else ""))

print("=== Bug-41: 渐开线齿廓几何正确性 ===")
# 台账场景：m=1, z=30（首轮小车减速齿轮就是 m=1 z=30）
prof = m.involute_gear_profile(1.0, 30)
check("生成成功", prof.get("ok") is True, prof.get("error"))
p = prof["params"]
check("分度圆 Ø=30", abs(p["pitch_dia_mm"]-30.0)<1e-6, p["pitch_dia_mm"])
check("齿顶圆 Ø=32 (m*z+2m)", abs(p["tip_dia_mm"]-32.0)<1e-6, p["tip_dia_mm"])
check("齿根圆 Ø=27.5 (m*z-2.5m)", abs(p["root_dia_mm"]-27.5)<1e-6, p["root_dia_mm"])
check("基圆 Ø=28.19 (d*cos20)", abs(p["base_dia_mm"]-30*math.cos(math.radians(20)))<1e-3, p["base_dia_mm"])
check("单齿点数可控(<=40)", p["point_count"] <= 40, p["point_count"])

print("\n=== Bug-41: 齿廓确实落在齿顶/齿根圆之间 ===")
radii = [math.hypot(x,y) for (x,y) in prof["points"]]
r_max, r_min = max(radii), min(radii)
check("最大半径≈齿顶圆", abs(r_max - 16.0) < 0.01, round(r_max,4))
check("最小半径≈齿根圆", abs(r_min - 13.75) < 0.05, round(r_min,4))

print("\n=== Bug-41: 首尾闭合（polyline 能形成闭合轮廓）===")
f, l = prof["points"][0], prof["points"][-1]
check("首尾点重合", abs(f[0]-l[0])<1e-9 and abs(f[1]-l[1])<1e-9, (f,l))

print("\n=== Bug-41: 齿数自适应（z=10 也正常）===")
prof10 = m.involute_gear_profile(1.0, 10)
check("z=10 生成成功", prof10.get("ok") is True, prof10.get("error"))
check("z=10 分度圆 Ø=10", abs(prof10["params"]["pitch_dia_mm"]-10.0)<1e-6)

print("\n=== Bug-41: 完整齿圈点数（z=30, steps=4）===")
# 模拟 gear() 里的旋转复制
single = prof["points"]; z = 30
full_count = len(single) * z + 1
check("齿圈总点数在 SW 可接受范围", full_count < 2000, full_count)
print("     (单齿 %d 点 × %d 齿 = %d 点；旧方案 300+ 点/齿)" % (len(single), z, full_count))

print("\nPASS=%d FAIL=%d" % (len(PASS),len(FAIL)))
sys.exit(1 if FAIL else 0)
