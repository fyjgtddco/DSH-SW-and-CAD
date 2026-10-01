# -*- coding: utf-8 -*-
"""真实情景装配：机械臂 9 零件 + 关节轴/电机壳/夹爪，含干涉检查。"""
import os
import swapi

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "DSH_机械臂总装.sldasm")

# 零件路径
P = {
    "底座法兰":   os.path.join(BASE, "DSH_底座法兰.sldprt"),
    "底座立柱":   os.path.join(BASE, "DSH_底座立柱.sldprt"),
    "回转台":     os.path.join(BASE, "DSH_回转台.sldprt"),
    "关节轴1":    os.path.join(BASE, "DSH_关节轴.sldprt"),
    "大臂":       os.path.join(BASE, "DSH_大臂.sldprt"),
    "关节轴2":    os.path.join(BASE, "DSH_关节轴.sldprt"),
    "小臂":       os.path.join(BASE, "DSH_小臂.sldprt"),
    "关节轴3":    os.path.join(BASE, "DSH_关节轴.sldprt"),
    "腕法兰":     os.path.join(BASE, "DSH_腕法兰.sldprt"),
    "关节电机壳": os.path.join(BASE, "DSH_关节电机壳.sldprt"),
    "夹爪":       os.path.join(BASE, "DSH_夹爪.sldprt"),
}

# 新建装配体
asm = swapi.new_assembly()
print("装配体已建:", asm.model.GetTitle if hasattr(asm.model, "GetTitle") else "ok")

added = {}
def place(name, x, y, z):
    r = asm.add_component(P[name], x, y, z)
    ok = bool(r.get("ok"))
    print("  + %-12s ok=%s 实际位置=%s" % (name, ok, r.get("actual_position_mm", r.get("position_mm"))))
    added[name] = r.get("component")
    return r

# ── 定位（包围盒中心坐标，单位 mm）──
# 底座法兰厚度40 -> 中心 z=20
place("底座法兰",   0,   0,   20)
# 立柱高170,底坐法兰顶z=40 -> 立柱中心 z=40+85=125
place("底座立柱",   0,   0,  125)
# 回转台厚30,顶面立柱顶z=210 -> 中心 z=210+15=225
place("回转台",     0,   0,  225)
# 关节轴1 长60(沿Z) 穿过回转台与立柱接合处 中心z=210
place("关节轴1",    0,   0,  210)
# 大臂: 箱梁沿X 320长, 根在回转台上方 z=240, 沿X正方向伸展 -> 中心 x=160 z=240
place("大臂",     160,   0,  240)
# 关节轴2 长70(沿Y) 穿大臂根 -> 中心 (0,0,240)
place("关节轴2",    0,   0,  240)
# 小臂: 260长, 铰接大臂末端 x=320附近 -> 中心 x=320+130=450 z=240
place("小臂",     450,   0,  240)
# 关节轴3 长60(沿Y) 穿小臂根 -> 中心 (450,0,240)
place("关节轴3",  450,   0,  240)
# 腕法兰 厚20 在小臂末端 x=580 -> 中心 x=580 z=240
place("腕法兰",   580,   0,  240)
# 关节电机壳 Φ90 管 套关节2处 -> 中心 (0,0,240)
place("关节电机壳", 0,   0,  240)
# 夹爪 在小臂末端 x=580 z=250
place("夹爪",     580,   0,  255)

# SW2025 下 GetComponentCount() 需参数，安全读组件数
try:
    _n = asm.model.GetComponentCount(False)
except Exception:
    try:
        _comps = asm.model.GetComponents(False) or []
        _n = len(list(_comps))
    except Exception:
        _n = "?"
print("组件总数:", _n)

# ── 干涉检查 ──
print("=== 干涉检查 ===")
inter = asm.check_interference(include_multibody=True, treat_coincident=True)
print("  干涉检查可用:", inter.get("available"))
print("  干涉数量:", inter.get("count"))
if inter.get("interferences"):
    for it in inter["interferences"][:10]:
        print("    干涉:", it)
else:
    print("    无干涉 ✅")

# 保存装配体
asm.rebuild()
res = asm.save_as(OUT)
print("SAVE 装配体:", res)

# 截图
try:
    asm.set_view_iso()
    asm.screenshot(os.path.join(BASE, "DSH_机械臂总装_preview.png"))
except Exception as e:
    print("截图失败(非致命):", repr(e)[:80])

print("ASSEMBLY_DONE")
