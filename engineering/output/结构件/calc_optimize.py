# -*- coding: utf-8 -*-
"""截面寻优：在满足 δ_total<=0.07mm(留30%裕度给关节) 且 n>=2.5 前提下最小化质量"""
import math
E = 68900.0; SY = 276.0; RHO = 2700.0; G = 9.81
F = 130.0; ALLOW = SY / 2.5
LB, LS, LW = 320.0, 260.0, 70.0


def I(B, H, t):
    return (B * H ** 3 - (B - 2 * t) * (H - 2 * t) ** 3) / 12.0


def A(B, H, t):
    return B * H - (B - 2 * t) * (H - 2 * t)


def defl(F, Lload, x, Ix):
    return F * x * x * (3.0 * Lload - x) / (6.0 * E * Ix)


best = None
for B1, H1, t1 in [(70, 50, 5), (80, 56, 5), (80, 60, 6), (90, 64, 6), (100, 70, 8)]:
    m1 = A(B1, H1, t1) * LB * 1e-9 * RHO + 0.15
    I1 = I(B1, H1, t1)
    M1 = F * (LB + LS + LW) + m1 * G * LB / 2
    s1 = M1 / (I1 / (H1 / 2))
    d1 = defl(F, LB + LS + LW, LB, I1)
    for B2, H2, t2 in [(55, 40, 4), (64, 44, 5), (70, 48, 5), (80, 54, 6)]:
        m2 = A(B2, H2, t2) * LS * 1e-9 * RHO + 0.10
        I2 = I(B2, H2, t2)
        M2 = F * (LS + LW) + m2 * G * LS / 2
        s2 = M2 / (I2 / (H2 / 2))
        d2 = defl(F, LS + LW, LS, I2)
        # 腕部固定 46x28 槽16
        Iw = 2 * ((46 - 16) / 2 * 28 ** 3 / 12)
        m3 = (46 * 28 - 16 * 28) * LW * 1e-9 * RHO
        M3 = F * LW + m3 * G * LW / 2
        s3 = M3 / (Iw / 14)
        d3 = defl(F, LW, LW, Iw)
        dt = d1 + d2 + d3
        mt = m1 + m2 + m3 + 0.076
        n1, n2, n3 = ALLOW / s1, ALLOW / s2, ALLOW / s3
        ok = (dt <= 0.07) and min(n1, n2, n3) >= 2.5
        if ok and (best is None or mt < best[0]):
            best = (mt, B1, H1, t1, B2, H2, t2, dt, n1, n2, n3, s1, s2, s3, d1, d2, d3, m1, m2, m3)

print("### 寻优结果（约束 δ_total<=0.07mm, n>=2.5, 目标min质量）")
if best:
    mt, B1, H1, t1, B2, H2, t2, dt, n1, n2, n3, s1, s2, s3, d1, d2, d3, m1, m2, m3 = best
    print(f"  大臂 {B1}x{H1}x{t1} : σ={s1:.3f} n={n1:.1f} δ={d1:.4f} m={m1:.3f}kg")
    print(f"  小臂 {B2}x{H2}x{t2} : σ={s2:.3f} n={n2:.1f} δ={d2:.4f} m={m2:.3f}kg")
    print(f"  腕部 46x28 槽16: σ={s3:.3f} n={n3:.1f} δ={d3:.4f} m={m3:.3f}kg")
    print(f"  δ_total={dt:.4f} mm (<=0.07) ; 总质量={mt:.3f} kg")
