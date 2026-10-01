# -*- coding: utf-8 -*-
"""变体对比: 现有方案 vs 加粗方案 vs 减速机主导刚度方案"""
import math, json, io, os
F_EQ = 130.0
G_40Cr = 79000.0
def Jp(d, di=0.0): return math.pi*(d**4-di**4)/32.0

def theta_tors(segs, T):
    return sum(L/Jp(d,di) for d,di,L in segs)*T/G_40Cr

# 现有方案
cur = {
 "J2": theta_tors([(25.0,0.0,20.0),(32.0,0.0,30.0),(25.0,0.0,20.0)], F_EQ*660.0),
 "J3": theta_tors([(20.0,0.0,20.0),(26.0,0.0,25.0),(20.0,0.0,20.0)], F_EQ*340.0),
}
# 加粗方案 B: J2 全段Ø32, J3 全段Ø26
alt = {
 "J2": theta_tors([(32.0,0.0,70.0)], F_EQ*660.0),
 "J3": theta_tors([(26.0,0.0,65.0)], F_EQ*340.0),
}
# 减速机主导方案 C: J2/J3 轴只作定位销, 扭矩由减速机输出法兰承受
# 谐波减速机扭转刚度典型值 K = 3.0e4 N*m/rad (SHF-20 级)
K_red = 3.0e4   # N*m/rad
red = {
 "J2": (F_EQ*660.0/1000.0)/K_red,     # rad  (T in N*m)
 "J3": (F_EQ*340.0/1000.0)/K_red,
}

def tip(th, L): return th*L

out = {}
for nm, d in [("A_现有(Ø25/Ø20)", cur), ("B_加粗(Ø32/Ø26全段)", alt), ("C_减速机主导(K=3e4 N*m/rad)", red)]:
    t2, t3 = d["J2"], d["J3"]
    out[nm] = dict(
        J2_theta_rad=t2, J2_tip_mm_660=round(tip(t2,660.0),4),
        J3_theta_rad=t3, J3_tip_mm_340=round(tip(t3,340.0),4),
        J2J3_only_rss_mm=round(math.sqrt(tip(t2,660.0)**2+tip(t3,340.0)**2),4))

# 其余关节(J1/J4/J5/J6)贡献固定, 取自 result.json 口径
other = math.sqrt(0.0584**2 + 0.0846**2 + 0.0155**2 + 0.0**2)
for nm in out: out[nm]["加上其余关节后_RSS_mm"] = round(
    math.sqrt(out[nm]["J2J3_only_rss_mm"]**2 + other**2), 4)
for nm in out: out[nm]["是否满足0.1mm"] = "PASS" if out[nm]["加上其余关节后_RSS_mm"] <= 0.1 else "FAIL"

p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "variant.json")
with io.open(p,"w",encoding="utf-8") as f: json.dump(out,f,ensure_ascii=False,indent=2)
print("WROTE", p)
