# -*- coding: utf-8 -*-
"""独立工程复核：报告里的 SF 与"真实 L 形支架"手算的对比。

L 形支架（本次真实建模）：
  底板 x:0..140, y:0..12；立臂 x:0..12, y:0..90；宽度 z:-40..+40
  载荷：额定 3000 N 沿 -Z（作用在立臂顶端 y≈90）
        侧向 1500 N 沿 +X
        impact_factor = 1.5
立臂根部截面（y=0 处）: x 0..12（厚 12）, z -40..40（宽 80）
"""
import io, json, os

F_Z = 3000.0 * 1.5     # 垂向设计载荷（沿 -Z）
F_X = 1500.0 * 1.5     # 侧向设计载荷（沿 +X）
H_ARM = 90.0           # 立臂高度（力臂）
T_X = 12.0             # 立臂沿 X 厚度
W_Z = 80.0             # 立臂沿 Z 宽度

# 绕 X 轴弯曲（载荷沿 Z）：I_x = t * w^3 / 12，c = w/2
I_x = T_X * W_Z ** 3 / 12.0
Z_x = I_x / (W_Z / 2.0)
sigma_x = F_Z * H_ARM / Z_x

# 绕 Z 轴弯曲（载荷沿 X）：I_z = w * t^3 / 12，c = t/2
I_z = W_Z * T_X ** 3 / 12.0
Z_z = I_z / (T_X / 2.0)
sigma_z = F_X * H_ARM / Z_z

# 双轴弯曲最不利角点（两分量在同一角点均产生拉应力）
sigma_bi = sigma_x + sigma_z

print("=" * 88)
print("独立手算（真实 L 形支架立臂根部截面，双轴弯曲）")
print("=" * 88)
print("  设计载荷：垂向 Fz = 3000 × 1.5 = %.0f N ；侧向 Fx = 1500 × 1.5 = %.0f N"
      % (F_Z, F_X))
print("  立臂根部截面：%g (X 向厚) × %g (Z 向宽) mm ；力臂 %g mm" % (T_X, W_Z, H_ARM))
print()
print("  绕 X 轴（垂向载荷 Z）: I_x = %.1f mm^4, Z_x = %.1f mm^3" % (I_x, Z_x))
print("     sigma_x = M/Z = %.0f / %.1f = %.2f MPa" % (F_Z * H_ARM, Z_x, sigma_x))
print("  绕 Z 轴（侧向载荷 X）: I_z = %.1f mm^4, Z_z = %.1f mm^3" % (I_z, Z_z))
print("     sigma_z = M/Z = %.0f / %.1f = %.2f MPa" % (F_X * H_ARM, Z_z, sigma_z))
print("  双轴叠加（最不利角点）: sigma = %.2f MPa" % sigma_bi)
print()
for name, sy in (("45#（GB σy=355）", 355.0), ("65Mn（GB σy=785）", 785.0),
                 ("Q355（GB σy=355）", 355.0), ("兜底 4140（σy=655）", 655.0)):
    print("  按 %-22s -> SF_手算 = %.2f" % (name, sy / sigma_bi))
print()
print("=" * 88)
print("与工具报告对比")
print("=" * 88)
G = r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools\_verify_e2e"
for fn, tag in (("r2_45_optimize.json", "45#"),
                ("r2_65Mn_optimize.json", "65Mn"),
                ("s1_Q355_optimize.json", "Q355")):
    raw = io.open(os.path.join(G, fn), encoding="utf-8-sig").read()
    j = json.loads(raw[raw.find("{"):])
    f = j["final_report"]["fea_result"]
    gu = f.get("geometry_used") or {}
    print("  %-6s 报告: SF=%-7s sigma_max=%-7s | 等效梁 L/W/H = %s/%s/%s (%s)"
          % (tag, f.get("safety_factor"), f.get("max_von_mises_mpa"),
             gu.get("L_mm"), gu.get("W_mm"), gu.get("H_mm"), gu.get("source")))
print()
print("  结论：报告把零件当作【%s】的实心等效悬臂梁计算，"
      % "140×90×80 mm")
print("        而真实 L 形支架的危险截面是 12 mm 厚立臂根部；")
print("        报告应力被严重低估、SF 被严重高估（倍数见上）。")
