# -*- coding: utf-8 -*-
"""【Bug⑦ + Bug⑩ 回归测试】薄壁/镂空件的等效模型诚实性 + part_load_share 零件级覆盖

覆盖两条被实测确认的缺陷：

Bug⑦｜物理模型对薄壁/镂空件高估安全系数
  L0（解析梁）/L1（numpy CST 2D）都把零件【包围盒排序三边】等效成【实心矩形
  棱柱】悬臂梁。薄壁/镂空件的等效实心体积比真实体积大数倍 → 应力虚低 → SF 虚高
  （实测：底盘 SF=31.55、传感器支架 SF=61.93，合理区间 2~5）。
  要求：
    · 判定为薄壁/镂空 且 房间类型 ∈ {structural, housing, ...承力类} 时，
      limitations 必须【显式写明】"该件属薄壁/镂空，L0 等效模型不适用"；
    · 该房间的 overall 至少降为 REVIEW（不得 PASS），且【不得】新增 FAIL
      （不能把现有房间全部判失败）；
    · 有 L1 时优先升级 L1 而不是只用 L0（本机无 3D 求解器时不得假装做过 3D FEA）。

Bug⑩｜part_load_share 默认 0.25 不适配独立承力件
  · 支持【零件级覆盖】（load_case 顶层 part_load_share 优先于 acceptance 默认值）；
  · 凭据 limitations 里声明本次实际用的 share 值；
  · "share<1 但该件是独立悬臂件"→ WARNING 提示复核。

运行：python test_regression_bug7_thinwall.py   （退出码 0 = 通过）
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = HERE
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)
_PHYS = os.path.join(TOOLS, "physics")
if _PHYS not in sys.path:
    sys.path.insert(0, _PHYS)

PASS = []
FAIL = []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (("  -> " + str(detail)) if (detail and not cond) else ""))


import load_case as LC            # noqa: E402
import simulation_report as SR    # noqa: E402
import physics_bridge as PB       # noqa: E402
import fea_solver                 # noqa: E402


# ══════════════════════════════════════════════════════════════════════
#  共用夹具：一个"薄壁 + 镂空"的典型 3D 打印件（底盘 150×120×3 板）
# ══════════════════════════════════════════════════════════════════════
def _thin_geo():
    return {
        "ok": True,
        "volume_mm3": 13090.0,            # 真实实体体积（薄壁+减重孔）
        "surface_area_mm2": 9000.0,       # → 2V/A ≈ 2.9mm（薄壁）
        "bbox_mm": [0.0, 150.0, 0.0, 120.0, 0.0, 3.0],   # 等效实心 54000mm³
        "mass_kg": 0.0166,
        "density_kg_m3": 1270.0,
        "source": "test_fixture",
    }


def _thick_geo():
    """实心块（对照）：等效体积 ≈ 真实体积 → 不得降级。"""
    return {
        "ok": True,
        "volume_mm3": 120000.0,
        "surface_area_mm2": 34000.0,
        "bbox_mm": [0.0, 120.0, 0.0, 100.0, 0.0, 10.0],   # 120000mm³
        "surface_area_mm2_ratio": None,
        "source": "test_fixture",
    }


def _case(share=None, standalone=False, min_wall=2.0):
    c = LC.default_load_case("BUG7_FIXTURE", "薄壁底盘夹具")
    c["acceptance"]["min_wall_thickness_mm"] = min_wall
    c["acceptance"]["min_safety_factor"] = 2.0
    c["acceptance"]["target_safety_factor_max"] = 5.0
    if share is not None:
        c["part_load_share"] = share
    if standalone:
        c["meta"]["standalone_cantilever"] = True
    return c


def _fea(sf=31.55, backends=None, used_l1=False, overall="REVIEW",
         inflation=4.13):
    """构造一份"薄壁件 L0 等效梁"结果（数值取自现场报告）。"""
    be = {"calculix": False, "code_aster": False, "elmerfem": False,
          "gmsh": False, "feapy": True, "skfem": False}
    if backends:
        be.update(backends)
    return {
        "ok": True,
        "method": "feapy_numpy" if used_l1 else "analytical",
        "backend": "pure_python_cst_triangle" if used_l1 else "closed_form_beam_theory",
        "safety_factor": sf,
        "max_von_mises_mpa": 3.2,
        "max_displacement_mm": 2.193,
        "volume_mm3": 13090.0,
        "mass_kg": 0.0166,
        "gates": {"SAFETY_FACTOR": {"status": ("PASS" if 2.0 <= sf <= 5.0
                                               else "OVER_DESIGN"),
                                    "actual": sf,
                                    "required_min": 2.0, "required_max": 5.0},
                  "MAX_DISPLACEMENT": {"status": "PASS"},
                  "MESH_QUALITY": {"status": "PASS"}},
        "overall": overall,
        "limitations": ["Euler-Bernoulli beam assumptions",
                        "【适用边界】L 形 / 薄壁 / 镂空 / 变截面件的安全系数会被显著高估"],
        "geometry_used": {"model": "equivalent_solid_cantilever_from_bbox",
                          "L_mm": 150.0, "W_mm": 120.0, "H_mm": 3.0,
                          # 等效体积 = 真实体积 × 失真倍数（口径自洽，
                          # 否则"填充率"与"失真倍数"会互相矛盾）
                          "equivalent_solid_volume_mm3": round(13090.0 * inflation, 2),
                          "real_volume_mm3": 13090.0,
                          "volume_inflation_x": inflation},
        "load_model": {"rated_force_n": 500.0, "design_force_n": 125.0,
                       "part_load_share": 0.25,
                       "load_share_source": "acceptance.part_load_share"},
        "fatigue": {"verdict": "PASS", "fatigue_sf": 30.0},
        "l1_available": bool(be.get("feapy")),
        "solver_3d_available": any(bool(be.get(k)) for k in
                                   ("calculix", "code_aster", "elmerfem", "gmsh")),
        "solver_backends": be,
    }


def _gc(min_wall_est=2.91, min_wall_req=2.0):
    return {
        "status": "PASS",
        "gates": [{"id": "DESIGN_SPACE", "status": "PASS"},
                  {"id": "CONNECTIVITY", "status": "PASS"},
                  {"id": "WALL_THICKNESS", "status": "PASS",
                   "estimated_mm": min_wall_est, "required_mm": min_wall_req}],
        "wall_thickness_estimate": {"ok": True,
                                    "estimated_min_thickness_mm": min_wall_est},
        "connectivity": {"status": "PASS"},
    }


# ══════════════════════════════════════════════════════════════════════
#  Bug⑦-1：薄壁/镂空判定（只用已有真实几何，不编造）
# ══════════════════════════════════════════════════════════════════════
print("=== Bug⑦-1: _thin_hollow_assessment 判定（薄壁 + 镂空）===")
_a = PB._thin_hollow_assessment(_thin_geo(), _case(), 2.91, 2.0)
check("薄壁件被判 thin_wall", _a["thin_wall"] is True, _a.get("evidence"))
check("镂空件被判 hollow（等效/真实 > 1.5 倍）", _a["hollow"] is True,
      _a.get("evidence"))
check("=> 等效模型判定为【不适用】",
      _a["equivalent_model_applicable"] is False, _a.get("reasons"))
check("判定理由里含中文可读说明",
      any("薄壁" in r for r in _a["reasons"])
      and any("镂空" in r for r in _a["reasons"]), _a.get("reasons"))
check("★ 变截面如实标注【未评估】（不假装测过）",
      _a.get("variable_section") is None
      and _a.get("variable_section_evaluated") is False, _a.get("variable_section"))

_b = PB._thin_hollow_assessment(_thick_geo(), _case(), 17.6, 2.0)
check("实心厚件【不】被判薄壁", _b["thin_wall"] is False, _b.get("evidence"))
check("实心厚件【不】被判镂空（等效/真实≈1.0）",
      _b["hollow"] is False, _b.get("evidence"))
check("实心厚件 → 等效模型仍适用",
      _b["equivalent_model_applicable"] is True, _b.get("reasons"))

_c = PB._thin_hollow_assessment({"ok": True}, _case(), None, 2.0)
check("★ 缺证据时如实记 missing_evidence（不默认判合格）",
      bool(_c.get("missing_evidence")), _c.get("missing_evidence"))


# ══════════════════════════════════════════════════════════════════════
#  Bug⑦-2：报告层 limitations + overall 降级（承力类房间不得 PASS）
# ══════════════════════════════════════════════════════════════════════
print("\n=== Bug⑦-2: 薄壁件报告 limitations + overall 至少 REVIEW ===")
_fea_thin = _fea(sf=31.55)
_fea_thin["overall"] = "PASS"          # 最危险的情形：本该被放行
_rep = SR.build_report("bug7_r1", _case(), {}, _gc(), _fea_thin,
                       room_type="structural")
_lims = _rep["release_readiness"]["limitations"]
check("★ overall 由 PASS 被强制降为 REVIEW（不得 PASS）",
      _rep["overall_status"] == "REVIEW", _rep["overall_status"])
check("★ 未新增 FAIL（只降级不判负，现场安全）",
      _rep["overall_status"] != "FAIL", _rep["overall_status"])
check("★ limitations 显式写明『该件属薄壁/镂空，L0 等效模型不适用』",
      any("薄壁/镂空" in str(x) and "不适用" in str(x) for x in _lims),
      [x for x in _lims if "薄壁" in str(x)][:1])
check("limitations 写明『不得仅凭本结论判定设计合格』",
      any("不得仅凭本结论判定设计合格" in str(x) for x in _lims),
      [x for x in _lims if "不得仅凭" in str(x)][:1])
check("★ 无 3D 求解器时显式写 UNKNOWN，不假装做过 3D FEA",
      any("3D" in str(x) and ("UNKNOWN" in str(x) or "未执行" in str(x))
          for x in _lims),
      [x for x in _lims if "3D" in str(x)][:1])
check("报告落盘可审计的 model_equivalence 事实",
      isinstance(_rep.get("model_equivalence"), dict)
      and _rep["model_equivalence"].get("thin_wall") is True,
      _rep.get("model_equivalence"))
check("报告落盘 thin_hollow 判定证据（degraded=True）",
      (_rep.get("model_equivalence") or {}).get("degraded") is True)
check("release_readiness 要求人工复核",
      _rep["release_readiness"]["human_review_required"] is True)

print("\n=== Bug⑦-3: 房间类型决定是否降级（承力类 vs 纯出图类）===")
# 用"实心厚件且 SF 落在合理区间"的 PASS 结果，单看房间类型是否触发降级
_fea_pass = _fea(sf=3.0, overall="PASS", inflation=1.02)
_rep_draft = SR.build_report("bug7_r2", _case(), {}, _gc(min_wall_est=17.6),
                             _fea_pass, room_type="drafting")
check("drafting（纯出图类）房间不强制降级到 REVIEW",
      _rep_draft["overall_status"] == "PASS", _rep_draft["overall_status"])
check("drafting 房间仍保留等效模型 limitations（如实披露，不隐藏）",
      any("薄壁" in str(x) for x in
          _rep_draft["release_readiness"]["limitations"]))
for _alias, _canon in (("drivetrain", "transmission"), ("sensor", "housing")):
    # 薄壁件（SF 虚高）在别名房间里同样不得 PASS
    _r = SR.build_report("bug7_" + _alias, _case(), {}, _gc(),
                         _fea(sf=31.55, overall="PASS"), room_type=_alias)
    check("★ 别名 %s（→%s）同样属承力类 → 不得 PASS" % (_alias, _canon),
          _r["overall_status"] == "REVIEW", _r["overall_status"])

_rep_thick2 = SR.build_report("bug7_r4", _case(), {}, _gc(min_wall_est=17.6),
                              _fea(sf=3.0, overall="PASS", inflation=1.02),
                              room_type="structural")
check("实心厚件（SF=3 落在区间）不因 Bug⑦ 被降级",
      _rep_thick2["overall_status"] == "PASS", _rep_thick2["overall_status"])


# ══════════════════════════════════════════════════════════════════════
#  Bug⑦-4：L1 优先升级（有 L1 就不用 L0，且不冒充 3D FEA）
# ══════════════════════════════════════════════════════════════════════
print("\n=== Bug⑦-4: 薄壁件优先升级 L1（numpy CST），且不冒充 3D FEA ===")
_be = fea_solver.detect_available_backends()
if _be.get("feapy"):
    _fea_l1, _note = PB._prefer_l1_backend(_case(), os.path.dirname(__file__))
    check("有 L1(feapy) 时实际走 L1 数值解",
          _fea_l1.get("solver_level") == "L1", _fea_l1.get("backend"))
    check("L1 结果标注 l1_available=True", _fea_l1.get("l1_available") is True)
    check("★ 未安装 3D 求解器时 is_3d_fea 必须为 False（不得冒充）",
          _fea_l1.get("is_3d_fea") is False, _fea_l1.get("is_3d_fea"))
    check("说明文字明确指出『仍不是 3D FEA』",
          "3D FEA" in _note, _note)
else:
    check("（本机无 L1，跳过 L1 升级用例）", True)
check("★ 求解层级/3D 可用性字段随结果回显（供报告层如实披露）",
      "solver_level" in (_fea_l1 if _be.get("feapy") else {"solver_level": None}))


# ══════════════════════════════════════════════════════════════════════
#  Bug⑩：part_load_share 零件级覆盖 + 实际值声明
# ══════════════════════════════════════════════════════════════════════
print("\n=== Bug⑩-1: load_case.part_load_share 零件级覆盖优先级 ===")
_c1 = _case(share=1.0)
_c1["acceptance"]["part_load_share"] = 0.25      # 门禁全局默认值
_s1, _m1 = LC.part_load_share(_c1)
check("★ 零件级 part_load_share=1.0 覆盖 acceptance 默认 0.25",
      abs(_s1 - 1.0) < 1e-9, (_s1, _m1.get("source")))
check("口径来源标为 part_level.*（可追溯到零件级覆盖）",
      str(_m1.get("source", "")).startswith("part_level"), _m1.get("source"))

_c2 = _case()
_c2["acceptance"]["part_load_share"] = 0.25
_c2["acceptance"]["parallel_load_paths"] = 4
_s2, _m2 = LC.part_load_share(_c2)
check("未给零件级时沿用 acceptance 值（向后兼容）",
      abs(_s2 - 0.25) < 1e-9 and _m2.get("source") == "acceptance.part_load_share",
      (_s2, _m2.get("source")))

_c3 = _case()
_c3["acceptance"]["parallel_load_paths"] = 4
_s3, _m3 = LC.part_load_share(_c3)
check("仅有 parallel_load_paths 时按 1/4 折算（旧行为不变）",
      abs(_s3 - 0.25) < 1e-9, (_s3, _m3.get("source")))

print("\n=== Bug⑩-2: 独立承力件 + share<1 → WARNING 提示复核 ===")
_c4 = _case(share=0.25, standalone=True)
_s4, _m4 = LC.part_load_share(_c4)
check("★ 独立悬臂件被折减到 0.25 → review_required=True",
      _m4.get("review_required") is True, _m4)
check("★ 给出人类可读 WARNING（要求复核载荷路径）",
      "复核" in str(_m4.get("warning") or ""), _m4.get("warning"))
_c5 = _case(share=1.0, standalone=True)
_s5, _m5 = LC.part_load_share(_c5)
check("独立件按 share=1.0 承担 → 不再告警",
      _m5.get("review_required") is False, _m5)
_c6 = _case(share=0.25, standalone=False)
_s6, _m6 = LC.part_load_share(_c6)
check("非独立件折减 → 不误报（避免噪音告警）",
      _m6.get("review_required") is False, _m6)

print("\n=== Bug⑩-3: 实际 share 值必须写进凭据 limitations ===")
_fea10 = _fea(sf=3.0)
_fea10["load_model"].update({
    "part_load_share": 0.25,
    "load_share_source": "part_level.part_load_share",
    "part_load_share_review_required": True,
    "part_load_share_warning": "本件为独立承力件，但校核按 part_load_share=0.2500 折减",
})
_rep10 = SR.build_report("bug10_r1", _case(share=0.25, standalone=True), {},
                         _gc(min_wall_est=17.6), _fea10, room_type="structural")
_lims10 = _rep10["release_readiness"]["limitations"]
check("★ limitations 声明本次实际使用的 part_load_share=0.2500",
      any("part_load_share=0.2500" in str(x) for x in _lims10),
      [x for x in _lims10 if "part_load_share" in str(x)][:1])
check("limitations 同时声明该值来源（可追溯）",
      any("part_level.part_load_share" in str(x) for x in _lims10),
      [x for x in _lims10 if "part_load_share" in str(x)][:1])
check("独立承力件被折减 → 报告 warnings 里有复核提示",
      any("独立承力" in str(w) or "复核" in str(w)
          for w in (_rep10.get("warnings") or [])), _rep10.get("warnings"))
check("承力类房间内该情形至少 REVIEW（不得因 share 折减给 PASS）",
      _rep10["overall_status"] == "REVIEW", _rep10["overall_status"])

print("\n=== Bug⑩-4: 任意求解后端（含 L1）都必须回显本次实际 share ===")
_fe_l1 = {"ok": True, "solver_level": "L1", "backend": "pure_python_cst_triangle",
          "load_model": {"rated_force_n": 500.0, "design_force_n": 500.0}}
PB._ensure_load_share_disclosure(_case(share=1.0, standalone=True), _fe_l1)
check("★ L1 结果缺 share 时被补齐（否则 limitations 无法声明实际值）",
      _fe_l1["load_model"].get("part_load_share") == 1.0,
      _fe_l1["load_model"])
check("补齐时同时带上口径来源与独立件标记",
      _fe_l1["load_model"].get("load_share_source") == "part_level.part_load_share"
      and _fe_l1["load_model"].get("part_load_share_standalone") is True,
      _fe_l1["load_model"])
check("已有 share 的结果不被改写（口径不变）",
      PB._ensure_load_share_disclosure(
          _case(share=0.5),
          {"ok": True, "load_model": {"part_load_share": 0.25}})["load_model"]
      .get("part_load_share") == 0.25)

print("\n=== Bug⑩-5: governing_design_force 把 share 口径一并回显 ===")
_c7 = _case(standalone=True)
_c7["acceptance"]["part_load_share"] = 0.25   # 默认按 4 条并联路径折减
_force, _meta = LC.governing_design_force(_c7)
check("governing_design_force 回显 part_load_share=0.25",
      abs(float(_meta.get("part_load_share") or 0) - 0.25) < 1e-9,
      _meta.get("part_load_share"))
check("governing_design_force 回显 share 来源",
      _meta.get("load_share_source") == "acceptance.part_load_share",
      _meta.get("load_share_source"))
check("独立件按 1.0 承担时不告警（对照）",
      LC.governing_design_force(_case(share=1.0, standalone=True))[1]
      .get("part_load_share_review_required") is False)
check("★ 独立件折减标记随载荷口径回显（供报告/防线复核）",
      _meta.get("part_load_share_review_required") is True,
      _meta.get("part_load_share_review_required"))

print("\nPASS=%d FAIL=%d" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：")
    for f in FAIL:
        print("  - " + f)
sys.exit(1 if FAIL else 0)
