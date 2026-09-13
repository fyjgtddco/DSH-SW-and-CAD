import os
import swapi

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "DSH_大臂.sldprt")

m = swapi.new_part()

# ---------- Step 1: 外箱 100(W) x 70(H) x 450(L) ----------
m.begin_sketch("Front Plane")
m.rect(0, 0, 100, 70)
m.end_sketch()
m.extrude(450)                # z: 0 -> 450

# ---------- Step 2: 抽空成箱型梁，两端留 30 端壁 ----------
# 从 z=0 面内切 深度420 -> 保留 z=420..450 实心端壁
m.begin_sketch_on_face(0, 0, 0)
m.rect(0, 0, 76, 46)          # 内腔 76x46
m.end_sketch()
m.cut(depth=420, through=False)

# 从 z=450 面内切 深度30 -> 保留 z=0..30 实心端壁 (中心空腔 30..420)
m.begin_sketch_on_face(0, 0, 450)
m.rect(0, 0, 76, 46)
m.end_sketch()
m.cut(depth=30, through=False)

# ---------- Step 3: 中央关节轴孔 Φ40 沿臂长(Z)贯穿 (两端同轴) ----------
m.begin_sketch_on_face(0, 0, 0)
m.circle(0, 0, 20)            # R20 (Φ40)
m.end_sketch()
m.cut(through=True)

# ---------- Step 4: 关节端加强圆角/倒角 ----------
m.fillet(8, [(0, 50, 0)])     # 角部圆角
m.fillet(8, [(50, 0, 0)])
m.fillet(8, [(0, 50, 450)])
m.fillet(8, [(-50, 0, 450)])
# 端壁孔口倒角
m.chamfer(2, [(0, 20, 0)], 45)

m.rebuild()
m.bring_to_front()
m.set_view_iso()
m.screenshot(os.path.join(os.path.dirname(os.path.abspath(__file__)), "DSH_大臂_preview.png"))
res = m.save(OUT)
print("SAVE:", res)
