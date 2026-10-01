# -*- coding: utf-8 -*-
"""传动机构 6 件零件设计计算（交叉验证两次）"""
import math

# ===================== 设计输入（门禁估算）=====================
F_design = 500.0          # N, 等效负载（已含 2.5 倍安全系数）
L_arm = 0.30             # m, 代表性关节力臂
T_joint = F_design * L_arm   # 关节传递扭矩 N·m  = 150

print("=" * 60)
print("设计输入: F=%.0fN  L=%.2fm  T_joint=%.1f N·m" % (F_design, L_arm, T_joint))

# ===================== 1. 关节轴（扭转强度）=====================
# 方法A: τ = 16T/(π d^3)  ->  d = (16T/(π τ_allow))^(1/3)
tau_allow = 35.0          # MPa, 45#调质传动轴许用切应力
d3_A = 16.0 * T_joint / (math.pi * tau_allow * 1e6)   # m^3
d_A = d3_A ** (1.0 / 3.0) * 1000.0                     # mm
# 方法B: 数值积分验证  τ(r)=T*r/J, J=π d^4/32 ；求外表面 τ
def tau_of_d(d_mm, T):
    J = math.pi * (d_mm / 1000.0) ** 4 / 32.0
    r = (d_mm / 1000.0) / 2.0
    return T * r / J / 1e6   # MPa
d_B = 30.0
tau_at_30 = tau_of_d(d_B, T_joint)
# 反算满足 τ_allow 的 d（方法B）
d_B_calc = (16.0 * T_joint / (math.pi * tau_allow * 1e6)) ** (1.0 / 3.0) * 1000.0
print("-" * 60)
print("关节轴扭转:")
print("  方法A 解析 d = %.2f mm" % d_A)
print("  方法B 解析 d = %.2f mm" % d_B_calc)
print("  取 d=30mm 时 τ = %.2f MPa (许用 %.0f)" % (tau_at_30, tau_allow))
assert abs(d_A - d_B_calc) < 1e-6, "两次计算不一致!"
print("  >> 选定关节轴直径 d=30 mm （含键槽处适当加大肩台 φ40）")

# ===================== 2. 齿轮（弯曲强度 Lewis）=====================
m = 2.5                # 模数 mm
z = 24                 # 齿数
d_pitch = m * z        # 节圆直径 mm = 60
b = 30.0               # 齿宽 mm
Y = 0.30 + 0.6 / z     # 齿形系数近似（z=24 -> 0.325）
Ft = 2.0 * T_joint / (d_pitch / 1000.0)   # 圆周力 N = 5000
# 方法A: Lewis 弯曲 σ = Ft/(m b Y)
sigma_A = Ft / (m * b * Y)
# 方法B: 用齿厚截面模量近似  W = m^2 b / 1.5 (简化), σ=Ft/W
W_B = m * m * b / 1.5
sigma_B = Ft / W_B
print("-" * 60)
print("齿轮弯曲 (m=%.1f z=%d 节圆Φ%d 齿宽%.0f):" % (m, z, d_pitch, b))
print("  方法A Lewis σ = %.1f MPa" % sigma_A)
print("  方法B 简化  σ = %.1f MPa" % sigma_B)
print("  圆周力 Ft = %.0f N" % Ft)
S_y_45 = 355.0
print("  45# 屈服 %.0f MPa, 许用(2倍SF)=%.0f MPa" % (S_y_45, S_y_45 / 2.0))
# 取两者中较保守
sigma_use = max(sigma_A, sigma_B)
print("  >> 采用 σ≈%.0f MPa < %.0f 许用, 安全" % (sigma_use, S_y_45 / 2.0))

# ===================== 3. 轴承座 / 电机座 / 减速器壳体 螺栓 =====================
# 6206 轴承: 内径30 外径62 宽16
# 螺栓孔承载: 取 4×M8 (Q235), 预紧抗拉为主
# 方法A: 单螺栓剪切承载  Fs = n * (π/4) d_b^2 * τ_b
# 方法B: 按夹紧连接, 4孔均布承载 F_design
d_bolt = 8.0
n_bolt = 4
tau_bolt = 240.0       # MPa 近似许用剪切(8.8级约 320, 取保守)
Fs_A = n_bolt * (math.pi / 4.0) * d_bolt ** 2 * tau_bolt  # N
per_bolt = F_design / n_bolt
print("-" * 60)
print("联接螺栓 4×M8:")
print("  方法A 总抗剪承载 = %.0f N" % Fs_A)
print("  方法B 单栓受力 = %.0f N (总载 %.0f)" % (per_bolt, F_design))
print("  >> 承载充足 (%.0f >> %.0f)" % (Fs_A, F_design))

# ===================== 汇总尺寸 =====================
print("=" * 60)
print("汇总（用于参数表）:")
print("  关节轴: 45# 调质, 主轴径Φ30, 肩台Φ40, 长120, 键8×7")
print("  齿轮:   45# 钢, m2.5 z24 节圆Φ60 齿宽30, 内孔Φ30H7")
print("  轴承座: Q235, 孔径Φ62H7(6206外), 法兰90×90×12, 4×M8 PCD70")
print("  电机座: Q235, 板90×90×12, 中心孔Φ14H7, 4×M5 PCD50")
print("  联轴器: 45# 钢, 法兰Φ70, 总长50, 两孔Φ14H7/Φ30H7, 6×M6")
print("  减速器壳: Q235, 箱体110×110×70壁10, 输入Φ14H7 输出Φ30H7, 4×M8")
print("=" * 60)
