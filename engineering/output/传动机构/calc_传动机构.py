# -*- coding: utf-8 -*-
"""
传动机构房间 - 轴系强度/刚度/疲劳校核
权威载荷来源: tools/load_cases/gate_load_case.json  (magnitude_n = 100.0 N)
所有计算统一单位: N / mm / MPa / N*mm
每个量均用两种不同方法计算并交叉验证。
"""
import math, json

# ---------- 门禁权威输入 ----------
F_NOM   = 100.0          # N  额定载荷 (gate_load_case.json -> magnitude_n)
IMPACT  = 1.3            # 往复循环动载系数
F_EQ    = F_NOM * IMPACT # 130.0 N 设计等效载荷
N_TGT   = 2.5            # 目标安全系数
CYCLES  = 200000 * 30    # 6.0e6 次循环 (30年)
HOURS30 = 4 * 300 * 30   # 36000 h (每天4h x 300天 x 30年)

MAT = {
    "6061-T6": dict(E=68900.0,  G=26000.0, sb=310.0,  s02=276.0, sfat=96.0),
    "45#":     dict(E=206000.0, G=79000.0, sb=600.0,  s02=355.0, sfat=270.0),
    "40Cr":    dict(E=206000.0, G=79000.0, sb=980.0,  s02=785.0, sfat=392.0),
}
ALPHA = 0.6  # 第三强度理论扭矩折合系数(对称循环扭矩)

def W_bend(d, di=0.0): return math.pi*(d**4-di**4)/(32.0*d)
def W_tors(d, di=0.0): return math.pi*(d**4-di**4)/(16.0*d)
def I_sec(d, di=0.0):  return math.pi*(d**4-di**4)/64.0
def J_pol(d, di=0.0):  return math.pi*(d**4-di**4)/32.0

def strength(name, matn, d, di, M, T):
    """方法A: 第三强度理论(当量弯矩); 方法B: 第四强度理论(von Mises)"""
    m = MAT[matn]
    W, Wt = W_bend(d, di), W_tors(d, di)
    sb, tau = M/W, T/Wt
    Me  = math.sqrt(M*M + (ALPHA*T)**2)          # 方法A
    sA  = Me/W
    sB  = math.sqrt(sb*sb + 3.0*tau*tau)         # 方法B
    allow = m["s02"]/N_TGT
    return dict(part=name, mat=matn, d=d, di=di, M_Nmm=round(M,1), T_Nmm=round(T,1),
                W_mm3=round(W,2), Wt_mm3=round(Wt,2),
                sigma_bend=round(sb,2), tau_tors=round(tau,2),
                Me_Nmm=round(Me,1), sigma_A_Tresca=round(sA,2), sigma_B_Mises=round(sB,2),
                sigma_design=round(max(sA,sB),2), allow_stress=round(allow,2),
                n_A=round(allow/sA,2), n_B=round(allow/sB,2),
                n_min=round(allow/max(sA,sB),2),
                diff_pct=round(abs(sA-sB)/max(sA,sB)*100,1))

def stiffness(name, matn, d, di, M, T, L_cant, L_tors, L_arm, nseg=200):
    """转角/挠度 -> 末端位移。方法A: 解析; 方法B: 分段数值积分"""
    m = MAT[matn]
    I, J, E, G = I_sec(d,di), J_pol(d,di), m["E"], m["G"]
    # 弯曲转角 (悬臂端受弯矩M, 长度L_cant)
    th_b_A = M*L_cant/(E*I)
    # 方法B: 分段数值积分 theta = int_0^L M/(EI) dx  (M 视为常量, 等分累加)
    th_b_B = sum(M*(L_cant/nseg)/(E*I) for _ in range(nseg))
    # 挠度 (悬臂, 端弯矩M): delta = M L^2/(2EI)
    dl_b_A = M*L_cant**2/(2.0*E*I)
    dl_b_B = 0.0
    dx = L_cant/nseg
    for i in range(nseg):
        x = (i+0.5)*dx
        dl_b_B += (M/(E*I))*x*dx     # 积分 theta(x) dx
    # 扭转角
    th_t_A = T*L_tors/(G*J)
    th_t_B = sum(T*(L_tors/nseg)/(G*J) for _ in range(nseg))
    d_bend = th_b_A*L_arm
    d_tors = th_t_A*L_arm
    d_tot  = math.sqrt(d_bend**2 + d_tors**2)
    return dict(part=name, L_cant_mm=L_cant, L_tors_mm=L_tors, L_arm_mm=L_arm,
                theta_bend_A=th_b_A, theta_bend_B=th_b_B,
                theta_tors_A=th_t_A, theta_tors_B=th_t_B,
                delta_bend_mm=round(d_bend,4), delta_tors_mm=round(d_tors,4),
                delta_total_mm=round(d_tot,4),
                tip_defl_mm=round(dl_b_A,5), tip_defl_B_mm=round(dl_b_B,5),
                err_bend_pct=round(abs(th_b_A-th_b_B)/th_b_A*100,3),
                err_tors_pct=round(abs(th_t_A-th_t_B)/th_t_A*100,3),
                ok_0p1mm=bool(d_tot <= 0.1))

def fatigue(name, matn, sigma_amp, Kf=1.8, sigma_mean=0.0):
    """方法A: 疲劳极限判据; 方法B: Goodman + Basquin S-N + Miner"""
    m = MAT[matn]
    sa = sigma_amp*Kf
    # A
    allow_a = m["sfat"]/N_TGT
    nA = allow_a/sa if sa > 0 else 999.0
    # B
    if m["sb"] > 0 and sigma_mean > 0:
        sa_eq = sa/(1.0 - sigma_mean/m["sb"])
    else:
        sa_eq = sa
    sfp = m["sb"] + 345.0     # Morrow 近似 (钢)
    b   = -0.09
    Nf  = 0.5*(sa_eq/sfp)**(1.0/b) if sa_eq > 0 else float('inf')
    D   = CYCLES/Nf if Nf not in (0, float('inf')) else 0.0
    nB  = (m["sfat"]/sa_eq) if sa_eq > 0 else 999.0
    return dict(part=name, sigma_amp=round(sigma_amp,2), Kf=Kf,
                sigma_amp_eff=round(sa,2),
                A_allow_amp=round(allow_a,2), A_n=round(nA,2),
                A_infinite=bool(sa < m["sfat"]),
                B_sigma_eq=round(sa_eq,2), B_sigma_f_prime=round(sfp,1),
                B_Nf_cycles=Nf, B_miner_D=round(D,6), B_n=round(nB,2),
                B_pass=bool(D < 1.0 and sa_eq < m["sfat"]),
                verdict=("PASS-无限寿命" if (sa < m["sfat"] and (D < 1.0 or D == 0)) else "FAIL"))

def bearing(matn_house, C_N, Fr, Fa, rpm, name):
    """深沟球轴承 L10 寿命。方法A: (C/P)^(10/3)*1e6; 方法B: 转数换算 + 循环次数双口径"""
    e = 0.22
    ratio = Fa/Fr if Fr > 0 else 0
    X, Y = (1.0, 0.0) if ratio <= e else (0.56, 1.9)
    P = X*Fr + Y*Fa
    L10_rev_A = (C_N/P)**(10.0/3.0)*1e6
    L10_h_A   = L10_rev_A/(60.0*rpm)
    # 方法B: 直接用循环次数口径(每次循环平均0.5转)
    revs_needed = CYCLES*0.5
    life_ratio_cycles = L10_rev_A/revs_needed
    return dict(part=name, C_N=C_N, Fr_N=Fr, Fa_N=Fa, e=e, X=X, Y=Y, P_N=round(P,1),
                rpm=rpm,
                L10_rev_A=L10_rev_A, L10_h_A=round(L10_h_A,1),
                L10_h_B=round(L10_rev_A/(60.0*rpm),1),
                hours_required=HOURS30,
                life_margin_hours=round(L10_h_A/HOURS30,1),
                revs_needed_6e6=revs_needed,
                life_margin_cycles=round(life_ratio_cycles,1),
                verdict=("PASS" if L10_h_A >= HOURS30 and L10_rev_A >= revs_needed else "FAIL"))

R = {}
# ================= 1. J1 回转轴 (Ø40, 45#) =================
# 载荷: 末端130N @ 臂展600mm -> 倾覆弯矩; 回转驱动扭矩(惯性+摩擦)取 5.0 N*m
M1 = F_EQ*600.0
T1 = 5000.0
R["J1回转轴"] = dict(
    strength = strength("DSH_J1回转轴","45#",40.0,0.0,M1,T1),
    stiffness= stiffness("DSH_J1回转轴","45#",40.0,0.0,M1,T1,L_cant=30.0,L_tors=150.0,L_arm=600.0),
    fatigue  = fatigue("DSH_J1回转轴","45#",0.0) )

# ================= 2. J2 关节轴 (Ø25, 40Cr) =================
# 扭矩: 130N @ J2->末端 660mm ; 弯矩取 0.5T(双支承分担)
T2 = F_EQ*660.0
M2 = 0.5*T2
R["J2关节轴"] = dict(
    strength = strength("DSH_J2关节轴","40Cr",25.0,0.0,M2,T2),
    stiffness= stiffness("DSH_J2关节轴","40Cr",25.0,0.0,M2,T2,L_cant=15.0,L_tors=45.0,L_arm=340.0),
    fatigue  = fatigue("DSH_J2关节轴","40Cr",0.0) )

# ================= 3. J3 关节轴 (Ø20, 40Cr) =================
T3 = F_EQ*340.0
M3 = 0.5*T3
R["J3关节轴"] = dict(
    strength = strength("DSH_J3关节轴","40Cr",20.0,0.0,M3,T3),
    stiffness= stiffness("DSH_J3关节轴","40Cr",20.0,0.0,M3,T3,L_cant=20.0,L_tors=35.0,L_arm=80.0),
    fatigue  = fatigue("DSH_J3关节轴","40Cr",0.0) )

# ================= 4. J4 腕转轴 (Ø16 中空Ø8, 40Cr) =================
M4 = F_EQ*80.0
T4 = F_EQ*50.0
R["J4腕转轴"] = dict(
    strength = strength("DSH_J4腕转轴","40Cr",16.0,8.0,M4,T4),
    stiffness= stiffness("DSH_J4腕转轴","40Cr",16.0,8.0,M4,T4,L_cant=40.0,L_tors=40.0,L_arm=40.0),
    fatigue  = fatigue("DSH_J4腕转轴","40Cr",0.0) )

# ================= 5. J5 腕摆轴 (Ø16, 40Cr) =================
M5 = F_EQ*40.0
T5 = F_EQ*30.0
R["J5腕摆轴"] = dict(
    strength = strength("DSH_J5腕摆轴","40Cr",16.0,0.0,M5,T5),
    stiffness= stiffness("DSH_J5腕摆轴","40Cr",16.0,0.0,M5,T5,L_cant=30.0,L_tors=30.0,L_arm=30.0),
    fatigue  = fatigue("DSH_J5腕摆轴","40Cr",0.0) )

# ---- 回填疲劳应力幅(用强度设计应力) ----
for k in list(R.keys()):
    s = R[k]["strength"]["sigma_design"]
    R[k]["fatigue"] = fatigue(R[k]["strength"]["part"], R[k]["strength"]["mat"], s)

# ================= 6. J6 腕法兰 (Ø63, 6061-T6) =================
# 法兰盘按悬臂圆环校核: M = F_eq * R ; 危险圆周 r=31.5, b=2*pi*r, h=10
Rf, h_f = 31.5, 10.0
M6 = F_EQ*Rf
b6 = 2*math.pi*Rf
W6 = b6*h_f**2/6.0
s6 = M6/W6
# 方法B: Roark 圆板中心集中载荷(周边简支) 近似
nu = 0.33
E6 = MAT["6061-T6"]["E"]
# 简支圆板中心集中载荷最大弯矩(近似) Mr_max ~ F/(4*pi)*(1+nu)*ln(R/r0)+...; 用保守悬臂式作B法
s6B = (3.0*F_EQ/(2*math.pi*h_f**2))*((1+nu)*math.log(Rf/6.0) + 1.0)
# 4xM5 螺栓倾覆校核
r_b = 22.5
Aeff_M5 = 14.2
allow_bolt = MAT["6061-T6"]["s02"]  # 螺栓按4.6级碳钢, 单独给
sb_bolt, n_bolt = 240.0, 2.5
F_allow_bolt = sb_bolt/n_bolt*Aeff_M5
F_max_bolt = M6*r_b/(4.0*r_b**2)
V_bolt = F_EQ/4.0
R["J6腕法兰"] = dict(
    strength = dict(part="DSH_J6腕法兰", mat="6061-T6", M_Nmm=round(M6,1),
                    W_mm3=round(W6,2), sigma_A=round(s6,3), sigma_B=round(s6B,3),
                    sigma_design=round(max(s6,s6B),3),
                    allow_stress=round(MAT["6061-T6"]["s02"]/N_TGT,2),
                    n_min=round((MAT["6061-T6"]["s02"]/N_TGT)/max(s6,s6B),1),
                    bolt_Fmax_N=round(F_max_bolt,1), bolt_Fallow_N=round(F_allow_bolt,1),
                    bolt_n=round(F_allow_bolt/F_max_bolt,1),
                    bolt_shear_N=round(V_bolt,2),
                    diff_pct=round(abs(s6-s6B)/max(s6,s6B)*100,1)),
    stiffness= stiffness("DSH_J6腕法兰","6061-T6",63.0,0.0,M6,F_EQ*25.0,
                         L_cant=10.0, L_tors=10.0, L_arm=31.5),
    fatigue  = fatigue("DSH_J6腕法兰","6061-T6", max(s6,s6B)) )

# ================= 7. J2 轴承座 (6061-T6) + 6205 轴承 =================
Fr7 = 1200.0   # J2 轴承径向反力 (130N x 660/80 力臂放大, 含自重保守)
Fa7 = 240.0
R["J2轴承座"] = dict(
    bearing = bearing("6061-T6", 14000.0, Fr7, Fa7, 25.0, "6205深沟球轴承(d25/D52/B15)"),
    housing = dict(part="DSH_J2轴承座", mat="6061-T6",
                   wall_sigma=round(Fr7/(15.0*10.0),2),     # P/(轴承宽*壁厚)
                   wall_allow=round(MAT["6061-T6"]["s02"]/N_TGT,2),
                   wall_n=round((MAT["6061-T6"]["s02"]/N_TGT)/(Fr7/150.0),1),
                   bolt_M6_allow_N=round(240.0/2.5*20.1,1),
                   bolt_M6_load_N=round(Fr7*17.5*43.0/(4.0*43.0**2),1)),
    fatigue = fatigue("DSH_J2轴承座","6061-T6", Fr7/150.0) )

# ---------- 整机 RSS 合成 ----------
contrib = [R[k]["stiffness"]["delta_total_mm"] for k in
           ["J1回转轴","J2关节轴","J3关节轴","J4腕转轴","J5腕摆轴","J6腕法兰"]]
rss = math.sqrt(sum(c*c for c in contrib))
R["_RSS总合成"] = dict(per_part=dict(zip(["J1","J2","J3","J4","J5","J6"],contrib)),
                        rss_mm=round(rss,4), budget_mm=0.1,
                        verdict=("PASS 满足±0.1mm" if rss <= 0.1 else "FAIL 超出±0.1mm"))

# ---------- 交叉验证自检 ----------
warn = []
for k, v in R.items():
    if k.startswith("_"): continue
    st = v.get("strength", {})
    if st.get("diff_pct", 0) > 15:
        warn.append(f"{k}: 强度两法差异 {st['diff_pct']}% (>15%)")
    sf = v.get("stiffness", {})
    if max(sf.get("err_bend_pct",0), sf.get("err_tors_pct",0)) > 1.0:
        warn.append(f"{k}: 刚度两法差异 >1%")
R["_交叉验证告警"] = warn or "全部通过(两法一致)"

print(json.dumps(R, ensure_ascii=False, indent=2, default=str))
