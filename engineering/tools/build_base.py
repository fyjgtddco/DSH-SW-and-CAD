import os, math
import swapi

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "DSH_基座.sldprt")

m = swapi.new_part()

# ---------- Step 1: 底部安装法兰 Φ420 x 30 ----------
m.begin_sketch("Front Plane")
m.circle(0, 0, 210)          # 法兰半径 210 (Φ420)
m.end_sketch()
m.extrude(30)                # z: 0 -> 30

# ---------- Step 2: 主体空心筒 外径Φ340 内径Φ300 高280 ----------
# 在法兰顶面 z=30 开草图，画同心双圆(环) 拉伸成筒
m.begin_sketch_on_face(0, 0, 30)
m.circle(0, 0, 170)          # 外 R170 (Φ340)
m.circle(0, 0, 150)          # 内 R150 (Φ300) -> 壁厚20
m.end_sketch()
m.extrude(280)               # z: 30 -> 310

# ---------- Step 3: 顶盖 厚25 (实心盘, 外径Φ340) ----------
m.begin_sketch_on_face(0, 0, 310)
m.circle(0, 0, 170)
m.end_sketch()
m.extrude(25)                # z: 310 -> 335

# ---------- Step 4: 顶部回转轴承座沉孔 Φ200 深15 (盲孔) ----------
m.begin_sketch_on_face(0, 0, 335)
m.circle(0, 0, 100)          # 轴承座 R100 (Φ200)
m.end_sketch()
m.cut(depth=15, through=False)   # 盲孔深15

# ---------- Step 5: 4×M16 地脚螺栓过孔 Φ18 均布 Φ360 ----------
# 在法兰顶面 z=30 画四孔, 完全贯穿
m.begin_sketch_on_face(0, 0, 30)
Rb = 180.0                   # 螺栓圆 Φ360
for ang in [0, 90, 180, 270]:
    a = math.radians(ang)
    m.circle(Rb*math.cos(a), Rb*math.sin(a), 9.0)   # Φ18
m.end_sketch()
m.cut(through=True)

# ---------- Step 6: 圆角 降应力集中 ----------
# 内壁顶缘 R10 (0,150,310)
m.fillet(10, [(0, 150, 310)])
# 外壁顶缘 R15 (0,170,310)
m.fillet(15, [(0, 170, 310)])
# 法兰-主体过渡 R10 (0,170,30)
m.fillet(10, [(0, 170, 30)])

m.rebuild()
m.bring_to_front()
m.set_view_iso()
m.screenshot(os.path.join(os.path.dirname(os.path.abspath(__file__)), "DSH_基座_preview.png"))
res = m.save(OUT)
print("SAVE:", res)
