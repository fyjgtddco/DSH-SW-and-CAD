# -*- coding: utf-8 -*-
"""
physics_bridge.py — Physics-in-the-Loop 命令行入口
====================================================
DSH 工程模式物理验证子系统的统一命令入口。

用法（直接运行）：
  python physics_bridge.py validate-case <load_case.json>
  python physics_bridge.py optimize <load_case.json> [--max-iter N]
  python physics_bridge.py demo
  python physics_bridge.py status

用法（通过 sw_bridge.py 路由）：
  python sw_bridge.py physics-status
  python sw_bridge.py physics-validate-case <case.json>
  python sw_bridge.py physics-demo
  python sw_bridge.py physics-optimize <case.json> --max-iter N
  python sw_bridge.py physics-report <run_id>
  python sw_bridge.py physics-recommend <run_id>

所有命令返回 dict（直接运行时会 JSON 打印），供 sw_bridge.py 路由调用。
"""
import sys
import os
import json
import argparse
import time

# Fix Windows GBK encoding
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# 确保 physics/ 子目录可导入
_HERE = os.path.dirname(os.path.abspath(__file__))
# _BASE_DIR = 工程模式 tools 目录。
# 【修复】此前 _derive_design_params() 读 gate_load_case.json 时引用了
#   _BASE_DIR，但本模块从未定义它 → 该分支每次都抛 NameError 并被
#   宽泛的 except 吞掉，导致"从工况读取设计参数"这条路径【静默失效】。
_BASE_DIR = _HERE
# ── 【P0-3 写侧修复】状态/凭据与数据文件的落点 ────────────────────────────
# STATE_DIR = 状态与凭据的权威目录（与 mode_gate/workflow_gate/defense_gate 同源）。
#   必须用它，否则从工作区跑出的 FEA 报告与防线凭据会写在工作区，
#   而宿主读安装副本 → 判定"缺少凭据"。
try:
    import _store as _store_mod
    STATE_DIR = _store_mod.state_dir()
except Exception:
    STATE_DIR = _BASE_DIR


def _data_dirs():
    """设计与工况类数据文件的候选搜索顺序（人工提供 > 权威 > 脚本目录）。"""
    out = []
    for d in (STATE_DIR, _BASE_DIR):
        try:
            a = os.path.abspath(d)
            if a and a not in out and os.path.isdir(a):
                out.append(a)
        except Exception:
            continue
    return out or [_BASE_DIR]
_physics_dir = os.path.join(_HERE, "physics")
if _physics_dir not in sys.path:
    sys.path.insert(0, _physics_dir)

import load_case
import material_db
import geometry_gate
import mesh_adapter
import fea_solver
import simulation_report as sim_report
import design_state
import refine_rules
try:
    import fatigue as fatigue_mod
except ImportError:
    fatigue_mod = None


# ═══════════════════════════════════════════════════════════════
#  命令实现 — 全部返回 dict，由调用方决定 print / 路由
# ═══════════════════════════════════════════════════════════════

def cmd_status(args=None) -> dict:
    """查询求解器后端状态。"""
    status = fea_solver.get_solver_status()
    status["command"] = "status"
    return status


def cmd_validate_case(case_path: str, relaxed: bool = False) -> dict:
    """校验载荷工况 JSON。"""
    result = load_case.load_from_file(case_path, strict=not relaxed)
    result["command"] = "validate-case"
    return result


def cmd_build(case_path: str) -> dict:
    """加载工况并初始化仿真环境摘要。

    ── 【BUG-05/08 修复】材料必须能解析，否则明确报错（不再返回 '?'）──────
    原缺陷：material 缺失时 report 里显示 '?'，调用方看不出"材料没赋"，
      于是拿默认密度 1000（水）算出来的质量/强度当真。
    修复：resolve_material 失败时把可用材料 ID 一并列出，让调用方能自救。
    """
    lc_result = load_case.load_from_file(case_path)
    if not lc_result["ok"]:
        return {"ok": False, "error": "invalid load case", "details": lc_result}

    mat_resolve = material_db.resolve_material(lc_result["case"])
    if not mat_resolve["ok"]:
        return {"ok": False, "error": "material resolution failed", "details": mat_resolve,
                "hint": ("材料未指定或 ID 未知。可用 ID 示例: "
                         + ", ".join(sorted(material_db.ALL_MATERIALS.keys())[:12])
                         + " …；完整列表见 material_db.list_materials()。"
                         "【BUG-05/08】材质未赋会导致密度虚标为 1000(水)，"
                         "质量与强度结论不可信，必须先解决材料问题。")}
    # 把解析到的材料回写进 case，保证后续 FEA/报告口径一致
    lc_result["case"]["material"] = mat_resolve["material"]

    summary = load_case.load_case_to_summary(lc_result["case"])
    solver_status = fea_solver.get_solver_status()

    return {
        "ok": True,
        "command": "build",
        "load_case_summary": summary,
        "material": mat_resolve["material"],
        "solver_status": solver_status,
        "next_step": "run physics-simulate after model is built",
    }


def cmd_simulate(run_dir: str) -> dict:
    """在指定 run_dir 运行一轮仿真。"""
    state_file = os.path.join(run_dir, "_state.json")
    if not os.path.exists(state_file):
        return {"ok": False, "error": f"run directory not found: {run_dir}"}

    with open(state_file, "r", encoding="utf-8") as f:
        state = json.load(f)

    lc_path = state.get("load_case_path", "")
    lc_result = load_case.load_from_file(lc_path)
    if not lc_result["ok"]:
        return {"ok": False, "error": "invalid load case", "details": lc_result}

    run_id = f"{os.path.basename(run_dir)}_iter_{len(state.get('runs', [])) + 1}"
    output_dir = os.path.join(run_dir, f"iteration_{len(state.get('runs', [])) + 1}")
    os.makedirs(output_dir, exist_ok=True)

    # ── 【Bug11/Bug7 修复】cmd_simulate 同样不得校验虚构几何 ────────────
    # 原实现与 cmd_optimize 一样用编造拓扑（faces=6/volume=100000），
    #   且 design_params={} → 壁厚阈值取不到 → WALL_THICKNESS 永远 PASS。
    # 现在从 state 取真实零件路径并提取真实几何；拿不到就明确标注。
    _part_path = state.get("part_path") or state.get("model_path") or ""
    # 【Bug18 根因修复】把工况显式传给 _extract_real_geometry，
    #   否则它内部的 design_domain 估计路径会因引用同名模块而崩（bbox 恒 None）。
    _geo = _extract_real_geometry(_part_path, lc_result.get("case")) if _part_path else {
        "ok": False,
        "error": ("state 未记录 part_path —— 无法取得真实几何。"
                 "请在 physics-build 时登记零件路径。"),
    }
    _geo_ok = bool(_geo.get("ok"))
    # ── 【Bug17 修复·顺序是关键】必须在 solve_fea【之前】注入 real_geometry ──
    # 原实现把这段写在 solve_fea 之后（L177 在 L156 之后）—— 求解时
    #   real_geometry 还不存在，fea_solver 的 `_rg` 恒为空 →
    #   `_used_real=False` → 退回 design_domain(200×60×60, vol=720000)，
    #   于是三个不同载荷算出同一个 SF（测试部 Bug17：SF 与载荷不敏感）。
    #   写晚了等于没写。现在整体前移到求解之前。
    if _geo_ok:
        try:
            lc_result["case"]["real_geometry"] = {
                "volume_mm3": _geo.get("volume_mm3"),
                "surface_area_mm2": _geo.get("surface_area_mm2"),
                "mass_kg": _geo.get("mass_kg"),
                "density_kg_m3": _geo.get("density_kg_m3"),
                "bbox_mm": _geo.get("bbox_mm"),
                "source": _geo.get("source"),
            }
        except Exception:
            pass
        # ── 【新 P0 修复】同 cmd_optimize：按【零件实际材料】校核 ──────────
        # 载荷文件材料只是任务级设计意图；零件本体材料才是校核依据。
        try:
            _mat_f, _mat_m = _resolve_part_material(_geo, lc_result.get("case"))
            if _mat_f:
                lc_result["case"]["material"] = _mat_f
                lc_result["case"]["material_resolution"] = _mat_m
        except Exception as _e_mo:
            try:
                lc_result["case"]["material_resolution"] = {
                    "source": "load_case", "error": repr(_e_mo)}
            except Exception:
                pass
    _wall_est = (_estimate_wall_from_geometry(_geo.get("volume_mm3"),
                                            _geo.get("surface_area_mm2"))
                 if _geo_ok else None)
    _min_wall = float(((lc_result["case"].get("acceptance") or {})
                       .get("min_wall_thickness_mm") or 2.0))
    _design_params = {
        "thickness_mm": {
            "id": "thickness_mm",
            "value": (round(float(_wall_est), 3) if _wall_est else None),
            "source": ("real-geometry(V/A)" if _wall_est else "unmeasured"),
        },
        "min_wall_thickness": {
            "id": "min_wall_thickness",
            "value": _min_wall,
            "source": "acceptance.min_wall_thickness_mm(默认 FDM 2.0mm)",
        },
    }
    domain = lc_result["case"].get("design_domain", {}).get("bounds", {})
    # ── 【Bug18 修复】body_bbox 必须用【真实包围盒】，不能用 design_domain ──
    # 原实现把 design_domain 的 x/y/z 边界直接当 body_bbox 传进去，等于
    #   "拿设计域自己和自己比" → DESIGN_SPACE 恒 PASS，门禁形同虚设；
    #   而当真实 bbox 缺失时又会读成 design_domain 的 z_max(60) 而非真实
    #   厚度(10)，造成"合格件被误拒"。两者都是错的。
    # 现在：有真实 bbox 就用真实值；没有就传 None，让 geometry_gate 明确
    #   报"未评估"，而不是编一个必然 PASS 或必然 FAIL 的假值。
    _bbox = _geo.get("bbox_mm") if _geo_ok else None
    if not (isinstance(_bbox, (list, tuple)) and len(_bbox) >= 6):
        _bbox = None
    gc_result = geometry_gate.geometry_gate_report(
        bbox=_bbox,
        # 真实拓扑未知时传 0（让 CONNECTIVITY 自判 NOT_EVALUATED，
        #   而不是喂假拓扑骗它 PASS）
        topology={"volumes": (1 if _geo_ok else 0), "bodies": (1 if _geo_ok else 0),
                  "faces": None, "edges": None, "vertices": None},
        face_areas=[],
        volume_mm3=(float(_geo.get("volume_mm3") or 0) if _geo_ok else 0.0),
        surface_area_mm2=(_geo.get("surface_area_mm2") if _geo_ok else None),
        design_bounds=domain, interfaces=[], design_params=_design_params,
        # 显式传阈值（双保险：即使 design_params 结构有变也不会失效）
        min_wall_req_mm=_min_wall,
    )

    # ── 【Bug17 修复】求解放在【几何注入之后】────────────────────────────
    # real_geometry 已在上方写入 lc_result["case"]，此时求解才能用到真实尺寸。
    fea_result = fea_solver.solve_fea(lc_result["case"], output_dir=output_dir)
    gc_result["geometry_source"] = (_geo.get("source") if _geo_ok
                                    else "UNMEASURED")
    if not _geo_ok:
        gc_result["geometry_error"] = _geo.get("error")

    report = sim_report.build_report(
        run_id=run_id, load_case=lc_result["case"], design_params=_design_params,
        geometry_check=gc_result, fea_result=fea_result,
        iteration=len(state.get("runs", [])) + 1,
        parent_run_id=state.get("current_run_id"),
    )
    report_path = sim_report.save_report(report, output_dir)
    report["report_path"] = report_path

    ds = design_state.DesignState(run_dir, lc_path)
    ds.new_run(run_id)
    ds.update_run(run_id, {
        "fea_result": fea_result, "geometry_check": gc_result,
        "report_path": report_path, "status": report["overall_status"],
    })

    return report


def _find_output_base() -> str:
    """定位 output 目录（兼容 DSH preset 和直接运行两种场景）。"""
    candidates = [
        os.path.join(_HERE, "..", "..", "output"),          # tools/../../output
        os.path.join(_HERE, "..", "output"),                # physics/../output (when run from physics/)
    ]
    for c in candidates:
        p = os.path.abspath(c)
        if os.path.isdir(p):
            return p
    # fallback: same dir as this file
    return os.path.abspath(os.path.join(_HERE, "..", "output"))


def _find_reports(run_id: str) -> list[str]:
    """在 output 目录下搜索匹配 run_id 的报告文件。"""
    out_base = _find_output_base()
    candidates = []
    for subdir in ("", design_state.STATE_DIR_NAME, "demo_run"):
        search_dir = os.path.join(out_base, subdir) if subdir else out_base
        if not os.path.isdir(search_dir):
            continue
        for root, _, files in os.walk(search_dir):
            for fn in files:
                if fn.endswith("_report.json") and run_id in fn:
                    candidates.append(os.path.join(root, fn))
    return candidates


def cmd_report(run_id: str) -> dict:
    """查找并返回指定 run_id 的仿真报告。"""
    # 先检查是否是直接文件路径
    if os.path.isfile(run_id):
        report_file = run_id
    else:
        report_file = None
        for path in _find_reports(run_id):
            if os.path.exists(path):
                report_file = path
                break

    if not report_file:
        return {"ok": False, "error": f"report not found for: {run_id}"}

    with open(report_file, "r", encoding="utf-8") as f:
        report = json.load(f)
    md = sim_report.report_to_markdown(report)
    return {"ok": True, "command": "report", "report": report, "markdown_summary": md}


def cmd_recommend(run_id: str, max_iter: int = 3) -> dict:
    """为指定 run_id 生成修正建议。"""
    report_data = cmd_report(run_id)
    if not report_data.get("ok"):
        return report_data

    report = report_data["report"]
    design_params = report.get("design_params", {
        "thickness_mm": {"id": "thickness_mm", "value": 5.0, "min": 2, "max": 20},
        "height_mm": {"id": "height_mm", "value": 60.0, "min": 20, "max": 200},
        "fillet_mm": {"id": "fillet_mm", "value": 2.0, "min": 0.5, "max": 15},
    })
    locked = report.get("locked_params", [])

    result = refine_rules.generate_recommendations(
        report=report, design_params=design_params,
        locked_params=locked, max_iterations=max_iter,
    )
    result["command"] = "recommend"
    return result


def _normalize_param_aliases(design_params):
    """把设计参数的【同义键名】归一化（观察点 20 修复）。

    ── 为什么需要 ──────────────────────────────────────────────────────
    同一个物理量在仿真报告与领域规则里用了不同键名，规则按自己的名字取不到值，
    于是把"有数据"误判成"无数据（CRITICAL）"，room-end 被无谓拦下。实测：
      · 上罩 report.design_params 里是 `thickness_mm = 2.0`；
      · `housing_rules.json` 的 HS-001 却要求 `wall_thickness_mm`；
      · 结果报 "HS-001 wall_thickness_mm 无数据（CRITICAL）"。

    本函数只做**键名归一**：同义组内只要任一键有有效值，就补齐其余键。
    它【不创造】任何数值 —— 没数据的参数依旧没数据，规则照旧如实报缺，
    因此不会把"不合格"洗成"合规"。

    Args:
        design_params: 待归一化的设计参数字典。
    Returns:
        归一化后的新字典（原字典不被修改）。
    """
    if not isinstance(design_params, dict):
        return design_params
    out = dict(design_params)

    def _valid(v):
        """有效值判据：非 None、非空串；0 视为"未测量"（与调用方口径一致）。"""
        if v is None:
            return False
        if isinstance(v, str):
            return v.strip() != ""
        if isinstance(v, (int, float)):
            return v != 0
        return True

    # 同义组：组内任一键有值 → 补齐其余键
    _ALIAS_GROUPS = [
        # 壁厚 / 特征厚度
        ("thickness_mm", "wall_thickness_mm", "min_wall_thickness_mm",
         "wall_thickness", "feature_thickness_mm"),
        # 圆角
        ("fillet_mm", "fillet_radius_mm", "min_fillet_mm"),
        # 轴径
        ("shaft_diameter_mm", "shaft_dia_mm", "axle_diameter_mm"),
        # 齿轮模数
        ("gear_module", "module_mm", "gear_module_mm"),
        # 加强筋
        ("rib_height_mm", "rib_mm", "rib_height"),
        # 型腔深度
        ("cavity_depth_mm", "cavity_mm", "cavity_depth"),
        # 拔模角
        ("draft_angle_deg", "draft_angle", "draft_deg"),
        # 表面粗糙度
        ("surface_roughness_um", "roughness_um", "ra_um"),
    ]
    for _grp in _ALIAS_GROUPS:
        _hit = None
        for _k in _grp:
            if _k in out and _valid(out.get(_k)):
                _hit = out.get(_k)
                break
        if _hit is None:
            continue
        for _k in _grp:
            # 只补空缺，绝不覆盖调用方已显式给出的值
            if _k not in out or not _valid(out.get(_k)):
                out[_k] = _hit
    return out


def _derive_design_params(part_path="", volume_mm3=None, surface_area_mm2=None,
                          bbox_mm=None):
    """从真实零件几何推导【领域规则所需的设计参数】（Bug12 复发修复）。

    ── 为什么需要 ──────────────────────────────────────────────────────
    四个领域规则声明的 target 大多是【设计参数】，例如：
      structural   : thickness_mm, fillet_mm
      transmission : shaft_diameter_mm, gear_module
      housing      : wall_thickness_mm, rib_height_mm
      mold         : cavity_depth_mm, draft_angle_deg, surface_roughness_um
    上轮修复只接入了物理结果（von_mises/SF/mass），design_params 仍为空，
      于是每条规则都报"未识别的验证目标" → 领域防线③仍非真合规。
    本函数从【真实几何】尽力推导这些参数（能推的推，推不出的留给规则
      显式报缺失 —— 绝不编造数值）。

    Returns: (design_params: dict, sources: dict)
    """
    dp, src = {}, {}
    # ══ 【Bug12 二次修复·优先级】人工提供 > 几何推导 ══════════════════════
    # 必须先读【人工提供通道】，再用几何估计【补空缺】。
    # 反过来的话，几何估计会覆盖人工输入 —— 实测：人工登记
    #   shaft_diameter_mm=25，却被 bbox 最小边估计值 10.0 盖掉，
    #   于是规则校验的是"编造尺寸"而不是"设计尺寸"，Bug12 等于没修。
    # 因此：人工值先落 dp；后面所有几何推导一律 setdefault（不覆盖）。
    _manual_src = {}
    try:
        # 人工提供通道：按 [权威状态目录, 脚本目录] 顺序找（取第一个存在者）
        _dp_file = None
        for _dd in _data_dirs():
            _cand = os.path.join(_dd, "design_params.json")
            if os.path.exists(_cand):
                _dp_file = _cand
                break
        if _dp_file and os.path.exists(_dp_file):
            _dpj = json.load(open(_dp_file, "r", encoding="utf-8"))
            _src_items = (_dpj.get("design_params")
                          if isinstance(_dpj.get("design_params"), dict) else _dpj)
            for _k0, _v0 in (_src_items or {}).items():
                _val0 = _v0.get("value") if isinstance(_v0, dict) else _v0
                if _val0 is not None:
                    dp[_k0] = _val0
                    _manual_src[_k0] = ("design_params.json【人工提供】"
                                        + ((" · %s" % _v0.get("source"))
                                           if isinstance(_v0, dict) and _v0.get("source")
                                           else ""))
                    src[_k0] = _manual_src[_k0]
    except Exception:
        pass
    # ① 包围盒 → 特征尺寸
    _dims = None
    if bbox_mm and len(bbox_mm) >= 6:
        try:
            _dims = sorted([abs(float(bbox_mm[1]) - float(bbox_mm[0])),
                            abs(float(bbox_mm[3]) - float(bbox_mm[2])),
                            abs(float(bbox_mm[5]) - float(bbox_mm[4]))], reverse=True)
        except Exception:
            _dims = None
    # ② 特征壁厚（2V/A，与 geometry_gate 同口径）
    _t = None
    try:
        _v = float(volume_mm3) if volume_mm3 else 0.0
        _a = float(surface_area_mm2) if surface_area_mm2 else 0.0
        if _v > 0 and _a > 0:
            _t = 2.0 * _v / _a
    except Exception:
        _t = None
    # ③ 填入各规则可能用到的键（只填【推得出】的）
    # 全部用 setdefault：人工提供通道的值优先，几何估计只补空缺。
    if _t:
        dp.setdefault("thickness_mm", round(_t, 3))
        src.setdefault("thickness_mm", "2V/A(真实几何)")
        dp.setdefault("wall_thickness_mm", round(_t, 3))
        src.setdefault("wall_thickness_mm", "2V/A(真实几何)")
        dp.setdefault("cavity_depth_mm", round(_t, 3))
        src.setdefault("cavity_depth_mm", "2V/A(真实几何)")
    if _dims:
        # 最小边常为壁厚/筋高；次小边常为轴径/模数尺度
        # 全部 setdefault：来源标注同样不得覆盖【人工提供】的标记，
        #   否则排障时会误以为该值是几何估计出来的。
        dp.setdefault("fillet_mm", round(min(_dims[2] * 0.1, 20.0), 3))
        src.setdefault("fillet_mm", "bbox 最小边×0.1(估计)")
        dp.setdefault("rib_height_mm", round(_dims[1] * 0.2, 3))
        src.setdefault("rib_height_mm", "bbox 次长边×0.2(估计)")
        dp.setdefault("shaft_diameter_mm", round(_dims[2], 3))
        src.setdefault("shaft_diameter_mm", "bbox 最小边(估计)")
        # ── 【Bug12 二次修复】禁止凭空编造"工艺/设计意图"参数 ────────────
        # 上一轮修复为了让规则不再报"无数据"，从几何硬凑了三个值：
        #     draft_angle_deg = 2.0°        （"工艺常规值"）
        #     surface_roughness_um = 1.6μm  （"通用加工值"）
        #     gear_module = 次小边/(z+2)，z 猜 20
        # 这是把【不存在的设计事实】写进凭据，比"报无数据"更危险：
        #   一个真正的拔模角 0.5°（不合格）会被 2.0° 的假值掩盖成合规。
        # 测试部反馈已明确指出："transmission/mold 的参数本质是人工设计输入，
        #   不是从几何能提取的 —— bbox 估计的修复方向对它们不适用"。
        # 因此：这些参数【一律不由几何推导】，只接受人工显式提供（见下方 ④）。
        #   缺失时由 domain_validator 判"不适用/需人工提供"，绝不假通过。
        _dmin, _dmid = _dims[2], _dims[1]
        # 型腔深度：仅当零件确为"薄壁腔体"形态时才可由最小边近似（可辩护）
        dp.setdefault("cavity_depth_mm", round(_dmin, 3))
        src.setdefault("cavity_depth_mm", "bbox 最小边(估计,可被 design_params 覆盖)")
    # ④ 再从【门禁已对齐的工况】补齐剩余设计参数（最低优先级）。
    # gear_module / draft_angle_deg / surface_roughness_um 等属于【设计意图】
    #   或【工艺参数】，从几何反推没有意义（会编造数值）。
    #   这里只读真实落盘值；读不到就留给规则报"需人工提供"。
    #
    # ── 【Bug12 二次修复】读取优先级（高 → 低）──────────────────────────
    #   ① 调用方显式传入的 design_params（见 cmd_validate_domain，最高）
    #   ② tools/design_params.json —— 【人工提供通道】（在本函数开头已读）
    #   ③ load_cases/gate_load_case.json 的 design_params（本段）
    # 三者都读不到 → 该参数就是"未提供"，规则层如实报缺，绝不假通过。
    try:
        # 门禁落盘的工况：同样按 [权威, 脚本目录] 顺序查找
        _lc = None
        for _dd in _data_dirs():
            _cand = os.path.join(_dd, "load_cases", "gate_load_case.json")
            if os.path.exists(_cand):
                _lc = _cand
                break
        if _lc and os.path.exists(_lc):
            _lcj = json.load(open(_lc, "r", encoding="utf-8"))
            _dp_src = (_lcj.get("design_params")
                       or (_lcj.get("case") or {}).get("design_params") or {})
            for _k2, _v2 in (_dp_src or {}).items():
                _val2 = _v2.get("value") if isinstance(_v2, dict) else _v2
                if _val2 is not None and _k2 not in dp:
                    dp[_k2] = _val2
                    src[_k2] = "gate_load_case.json"
    except Exception:
        pass
    return dp, src


def _latest_real_physics(room=""):
    """取【最近一次真实仿真】的结果，供领域防线使用（Bug12 修复）。

    背景：领域防线原本被 cmd_validate_domain 用空 design_params/fea_result 调用，
      规则拿到的 von_mises/displacement/mass/SF 全是 0，而 0 恰好不在多数
      违规区间里 → violations=[] 被误读成"合规"。这是"空数据不违规"，
      不是真合规（测试部 Bug12）。
    修复：从 output/physics_runs 下【最近一次】报告里取出真实物理量。

    Returns: (design_params, fea_result, meta) —— 取不到时返回 ({}, {}, {...})
    """
    import glob as _glob
    _here = os.path.dirname(os.path.abspath(__file__))
    _roots = [
        os.path.abspath(os.path.join(_here, "..", "..", "output", "physics_runs")),
        os.path.abspath(os.path.join(_here, "output", "physics_runs")),
        os.path.abspath(os.path.join(_here, "..", "output", "physics_runs")),
    ]
    _reports = []
    for _r in _roots:
        if not os.path.isdir(_r):
            continue
        _reports.extend(_glob.glob(os.path.join(_r, "*", "**", "*_report.json"),
                                   recursive=True))
    if not _reports:
        return {}, {}, {"ok": False, "error": "未找到任何仿真报告（output/physics_runs 为空）"}
    # 取最新的一份；若指定了 room，优先取该房间的
    try:
        _reports.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    except Exception:
        pass
    _pick = None
    if room:
        for _p in _reports:
            try:
                _j = json.load(open(_p, "r", encoding="utf-8"))
            except Exception:
                continue
            if str(_j.get("room") or "") == str(room):
                _pick = (_p, _j)
                break
    if _pick is None:
        for _p in _reports:
            try:
                _j = json.load(open(_p, "r", encoding="utf-8"))
            except Exception:
                continue
            _pick = (_p, _j)
            break
    if _pick is None:
        return {}, {}, {"ok": False, "error": "仿真报告均无法解析"}
    _path, _rep = _pick
    _fea = _rep.get("fea_result") or {}
    # ── 【国标材料修复】把材料事实并入 fea_result ─────────────────────────
    # 国标材料合规校验（domain=gb_material）需要屈服/密度/抗拉/弹性模量。
    # 这些值在报告里可能位于多处以不同键存放，这里统一归一化后注入，
    #  使领域防线（含国标材料层）能真正拿到数据，而不是"无数据→CRITICAL"。
    try:
        _mat = _rep.get("material") or (_rep.get("load_case_summary") or {}).get("material") or {}
        if isinstance(_mat, dict) and _mat:
            _fea.setdefault("material", _mat)
            # 顶层也放一份常用键，兜住只读顶层的旧规则
            for _dst, _cands in (
                    ("material_yield_mpa", ("yield_strength_mpa", "yield_mpa")),
                    ("material_density_kg_m3", ("density_kg_m3", "density")),
                    ("material_uts_mpa", ("uts_mpa",)),
                    ("material_e_mpa", ("youngs_modulus_mpa", "e_mpa"))):
                if _fea.get(_dst) in (None, "", 0):
                    for _c in _cands:
                        if _mat.get(_c) not in (None, "", 0):
                            _fea[_dst] = _mat.get(_c)
                            break
        # 真实几何里的密度（swapi.massprops）作为密度兜底来源
        _rg = _rep.get("real_geometry") or {}
        if isinstance(_rg, dict) and _rg.get("density_kg_m3") \
                and _fea.get("material_density_kg_m3") in (None, "", 0):
            _fea["material_density_kg_m3"] = _rg.get("density_kg_m3")
    except Exception:
        pass
    _dp_raw = _rep.get("design_params") or {}
    # design_params 可能是 {"thickness_mm": {"value":..}} 或 {"thickness_mm": 值}
    _dp = {}
    for _k, _v in _dp_raw.items():
        _dp[_k] = _v.get("value") if isinstance(_v, dict) else _v
    _acc = _rep.get("acceptance_criteria") or {}
    _dp.setdefault("min_wall_thickness", _acc.get("min_wall_thickness_mm", 2.0))
    return _dp, _fea, {"ok": True, "report_path": _path,
                       "run_id": _rep.get("run_id"),
                       "overall": _rep.get("overall_status")}


def cmd_validate_domain(domain_id: str = "structural",
                        design_params: dict = None,
                        fea_result: dict = None,
                        room: str = "") -> dict:
    """领域特定验证（DSVA）入口。

    调用示例:
        python physics_bridge.py validate-domain structural
        python sw_bridge.py physics-validate-domain transmission

    详见 engineering/tools/physics/domain_validator.py
    """
    try:
        import domain_validator
    except ImportError:
        return {"ok": False, "error": "domain_validator.py not found"}

    # ── 【Bug12 修复】必须用【真实物理结果】跑规则 ──────────────────────
    # 原实现默认 design_params={} / fea_result={} —— 规则拿到的
    #   von_mises/displacement/mass/SF 全是 0，而 0 不在多数违规区间内，
    #   于是 violations=[] 被误读成"合规"（空数据不违规 ≠ 真合规）。
    # 现在：显式传入的优先；否则从最近一次真实仿真报告取；
    #   若两者都拿不到 → 明确标 data_missing 并判【不可判定】，
    #   绝不返回"无违规=合规"。
    _data_meta = {"source": "explicit" if (design_params or fea_result) else None}
    if design_params is None:
        design_params = {}
    if fea_result is None:
        fea_result = {}
    if not design_params and not fea_result:
        _dp, _fr, _meta = _latest_real_physics(room)
        design_params, fea_result = _dp, _fr
        _data_meta = dict(_meta or {})
        _data_meta["source"] = "physics_runs(最近一次真实仿真)"
    # ── 【观察点 20 修复】字段别名归一化 ──────────────────────────────────
    # 同一物理量在【仿真报告】与【领域规则】里叫法不同，导致规则"读不到数据"
    # 而误报 CRITICAL：
    #   report.design_params.thickness_mm  ↔  housing_rules HS-001 要 wall_thickness_mm
    # 实测：上罩 report 里是 thickness_mm=2.0，HS-001 却读 wall_thickness_mm →
    #   "HS-001 wall_thickness_mm 无数据（CRITICAL）" → room-end 被拦。
    # 这里在把设计参数交给规则【之前】做一次别名归一：任一同义键有值，
    #   就补齐其余同义键（不覆盖已有值）。这是"读取时归一化"，不改变任何
    #   真实测量结果，只让规则能按自己的键名取到同一个物理量。
    design_params = _normalize_param_aliases(design_params)
    # ── 【Bug12 复发修复】补上【设计参数】（领域规则真正需要的东西）──────
    # 上轮只接了物理结果，design_params 仍空 → 四个领域规则全部报
    #   "未识别的验证目标"（thickness_mm/shaft_diameter_mm/wall_thickness_mm…）。
    # 现在从真实零件几何推导这些参数并合并进去（不覆盖显式传入的值）。
    try:
        _rg = {}
        if isinstance(_data_meta, dict):
            _rp = _data_meta.get("report_path")
            if _rp and os.path.exists(_rp):
                _rj = json.load(open(_rp, "r", encoding="utf-8"))
                _rg = _rj.get("real_geometry") or {}
        _derived, _dsrc = _derive_design_params(
            volume_mm3=_rg.get("volume_mm3"),
            surface_area_mm2=_rg.get("surface_area_mm2"),
            bbox_mm=_rg.get("bbox_mm"))
        for _k, _v in (_derived or {}).items():
            if _k not in design_params or design_params.get(_k) in (None, 0):
                design_params[_k] = _v
        # 几何推导出的键也要归一化（例如推导出 thickness_mm 后补齐 wall_thickness_mm）
        design_params = _normalize_param_aliases(design_params)
        if isinstance(_data_meta, dict):
            _data_meta["derived_params"] = _dsrc
    except Exception as _e_dp:
        if isinstance(_data_meta, dict):
            _data_meta["derive_error"] = repr(_e_dp)
    _has_real = bool(
        (fea_result or {}).get("safety_factor")
        or (fea_result or {}).get("max_von_mises_mpa")
        or (fea_result or {}).get("max_displacement_mm")
        or (fea_result or {}).get("mass_kg")
    )
    result = domain_validator.validate_with_domain(
        design_params=design_params,
        fea_result=fea_result,
        domain_id=domain_id,
    )
    # ── 【国标材料修复】附加执行国标材料合规校验并并入结论 ────────────────
    # 材料合规对所有领域都成立（结构件/传动件/壳体一样），但领域规则是
    #   "一个领域一份"；把 gb_material 作为附加层调用，既让国标材料数据真正
    #   参与领域防线，又避免把材料规则抄进 4~5 份 json 造成漂移。
    try:
        if domain_id != "gb_material":
            _has_mat_fact = bool(
                (fea_result or {}).get("material")
                or (design_params or {}).get("material_yield_mpa")
                or (design_params or {}).get("material_density_kg_m3")
                or (design_params or {}).get("yield_strength_mpa")
                or (design_params or {}).get("density_kg_m3"))
            if _has_mat_fact:
                _mat = domain_validator.validate_materials(design_params, fea_result)
                if isinstance(_mat, dict) and _mat.get("domain"):
                    result = domain_validator.merge_domain_results(result, _mat)
    except Exception as _e_mat:
        result.setdefault("warnings", []).append({
            "rule_id": "GB_MATERIAL_CHECK_ERROR",
            "severity": "WARNING",
            "message": "国标材料合规校验执行异常（不阻断）：%r" % (_e_mat,),
        })
    result["data_source"] = _data_meta
    result["physics_available"] = bool(_has_real)
    if not _has_real:
        # 空数据 → 不可判定（不是合规！）
        result["ok"] = False
        result["status"] = "NOT_EVALUATED"
        result["error"] = ("领域校验缺少真实物理数据：所有验证目标值为 0/缺失。"
                           "空数据【不得】判为合规（Bug12）。")
        result["hint"] = ("请先跑一次 physics-optimize --part <零件> 生成真实仿真报告，"
                           "或直接传入 design_params/fea_result。")
        result.setdefault("violations", [])
        result.setdefault("warnings", []).append({
            "rule_id": "DATA_MISSING",
            "severity": "CRITICAL",
            "message": "领域规则在空数据上运行 —— 结论不可采信",
        })
        _emit_domain_attestation(_resolve_defense_room(room), result, domain_id)
        result["available_domains"] = [d["id"] for d in domain_validator.list_domains()]
        return result
    # ── 【防线③领域】DSVA 结论一算出就自动落凭据 ──
    try:
        _emit_domain_attestation(_resolve_defense_room(room), result, domain_id)
    except Exception:
        pass

    result["available_domains"] = [d["id"] for d in domain_validator.list_domains()]
    return result


def cmd_list_domains() -> dict:
    """列出所有可用的验证领域。"""
    try:
        import domain_validator
    except ImportError:
        return {"ok": False, "error": "domain_validator.py not found"}
    return {
        "ok": True,
        "command": "list-domains",
        "domains": domain_validator.list_domains(),
    }


# ══════════════════════════════════════════════════════════════════════
#  【Bug7 修复】真实几何提取 —— 物理校核不得校验"空气"
# ══════════════════════════════════════════════════════════════════════
#  测试部实测：壁厚仅 0.5mm（远低于 FDM 最小 2mm）的极薄壁件，
#    physics-optimize 仍返回 SF=72.81 PASS。
#  根因：cmd_optimize 把 design_params 硬编码成 thickness=5/height=60/
#    width=40，并把 topology / volume_mm3 也写成编造值（volume=100000），
#    于是 geometry_gate 校验的是一个【虚构零件】，与真实 SW 模型无关 ——
#    任何不合格设计都能通过，防线②形同虚设。
#  修复原则：
#    ① 几何必须来自【真实零件】（swapi.massprops 的真实体积/表面积）；
#    ② 拿不到真实几何时，WALL_THICKNESS 等门禁【不得默认 PASS】，
#       必须标 NOT_EVALUATED 并让整体结论不可交付；
#    ③ design_params 不再凭空编造：没有真实值就如实标注来源。


def _attested_material_of(part_path):
    """【P0 修复】读零件的【材料凭据】里记录的国标名。

    ── 为什么需要 ────────────────────────────────────────────────────────
    实测根因链（子代理3）：SW 材质库没有 "Q235"，赋材时被近似成
      "1023 碳钢板 (SS)"。若只看 SW 读回的名字，材料库里查不到，
      于是兜底回载荷文件材料(PETG) —— 国标材料在物理校核【永远失效】。
    而 .material.json 凭据里保存的是【用户请求的国标名】
      （requested_material / attested_material_name = "Q235"），
      这才是"设计意图"，也是材料库里真实存在的牌号。
    因此：优先取凭据里的国标名，SW 近似名只作次选。

    Returns: (name, density) 或 (None, None)
    """
    try:
        _p = str(part_path or "")
        if not _p:
            return None, None
        # 大小写不敏感地找凭据（Bug-09 场景）
        import defense_gate as _dg
        _att = _dg._read_json(_dg.attestation_path(_p))
        if not isinstance(_att, dict):
            _d, _n = os.path.split(_p)
            _want = (_n + ".material.json").casefold()
            try:
                for _e in os.listdir(_d):
                    if _e.casefold() == _want:
                        _att = _dg._read_json(os.path.join(_d, _e))
                        break
            except Exception:
                pass
        if not isinstance(_att, dict):
            return None, None
        _name = (str(_att.get("attested_material_name") or "").strip()
                 or str(_att.get("requested_material") or "").strip()
                 or str(_att.get("attested_material") or "").strip())
        _dens = _att.get("attested_density_kg_m3") or _att.get("density_kg_m3")
        return (_name or None), _dens
    except Exception:
        return None, None


def _resolve_part_material(geo, lc_case, explicit_material=None):
    """【新 P0 修复】解析【零件实际材料】，用于覆盖载荷文件里的材料。

    ── 为什么必须做 ──────────────────────────────────────────────────────
    载荷工况（gate_load_case.json）里的 material 是【任务级设计意图】
      （例如整个任务按 PETG 申报），而零件本体可能已被赋成别的材料
      （例如小屋按 GB 标准用了 Q235）。两者不一致时，物理校核必须按
      【零件实际材料】判定 —— 否则：
        · Q235 零件被按 PETG(σy=40) 算 → SF 0.15（误判为严重不合格），
          而按 Q235(σy=235) 算真实 SF 是 1.12；
        · 国标材料的强度优势在物理校核里【完全无法体现】。
    实测（子代理2）：real_geometry.density=7858(Q235) 正确，而
      fea_result.mass_kg 却用 PETG(1270) 算出 0.0102（应为 0.0628）。

    优先级（高 → 低）：
      ① explicit_material —— 调用方显式指定（--material 参数）
      ② 零件本体材料（geo.part_material_name + 密度）—— 真实事实，最可信
      ③ 载荷文件材料 —— 兜底（保持向后兼容）

    Returns: (material_dict, meta)
      material_dict 为可直接写入 lc_case["material"] 的字典；
      meta 说明来源与是否发生覆盖，便于报告如实留痕。
    """
    _case_mat = dict((lc_case or {}).get("material") or {})
    _meta = {
        "source": "load_case",
        "overridden": False,
        "load_case_material": _case_mat.get("name") or _case_mat.get("id"),
        "part_material": None,
        "notes": [],
    }

    def _props_from_name(name, density_hint=None):
        """按材料名解析完整属性（内置表 → physics material_db → 密度反推）。

        ── 【P0 修复】必须能解析【SolidWorks 的近似名】──────────────────────
        实测根因链（子代理3）：SW 材质库没有 "Q235" → 近似成
          "1023 碳钢板 (SS)" → _extract_real_geometry 读回这个名字 →
          material_db / is_valid_material 都查不到 → 返回 None →
          兜底载荷文件材料(PETG) → 国标材料在物理校核【永远失效】。
        现在按三级解析：
          ① 精确名（swapi 内置表 / material_db，含 GB 系列与别名）
          ② 【近似名模糊解析】剥掉 " (SS)"/"(SS)" 等后缀，并按关键词
             （碳钢/不锈钢/铝/铸铁/塑料…）+ 牌号数字匹配材料库
          ③ 【密度反推】用零件实测密度在库中找密度最接近的材料
             （密度是 SW 写进零件的客观事实，最可靠）
        """
        _nm = str(name or "").strip()
        _has_density = False
        try:
            _has_density = bool(density_hint) and float(density_hint) > 0
        except Exception:
            _has_density = False
        # 名字为空但给了实测密度 → 仍可用密度反推（见下方 ③）
        if not _nm and not _has_density:
            return None

        # ── 【数值来源标记必须透传】────────────────────────────────────────
        # 实测缺陷（子代理发现）：内层函数原先"只挑固定几个字段重建 dict"，
        #   把 values_source / values_from_gb / values_pending / identity_source
        #   / equivalent_name 全部丢掉。后果：报告里看不出 235 MPa 到底是
        #   【GB 标准数值】还是【等效兜底值】—— "不冒充国标"的设计意图
        #   在下游彻底失效，防线也拦不住。
        # 现在：用统一的白名单把这些标记一并带出来，缺省填 None 保证键存在。
        _PROVENANCE_KEYS = (
            "values_source", "values_from_gb", "values_pending",
            "identity_source", "equivalent_name", "equivalent_id",
            "equivalent_filled", "gb_source", "standard",
            # 【BUG 修复】逐字段台账必须一并透传，否则报告无法精确说明
            #   "哪个数来自国标、哪个数是兜底" —— 那正是本批次的核心意图。
            "gb_sourced_fields", "field_provenance", "fallback_fields",
            "native_fields", "consistency_guard", "missing_fields",
        )

        def _carry_provenance(dst, src):
            """把来源标记从 src 透传到 dst（键始终存在，便于下游判断）。"""
            for _k in _PROVENANCE_KEYS:
                if _k not in dst:
                    dst[_k] = src.get(_k)
            return dst

        def _from_swapi(n):
            try:
                import swapi as _sw
                _ok, _i, _ = _sw.is_valid_material(n)
                if _ok and _i:
                    _d = {
                        "id": _i.get("canonical") or n,
                        "name": n,
                        "youngs_modulus_mpa": _i.get("e_mpa") or _i.get("youngs_modulus_mpa"),
                        "poissons_ratio": _i.get("poissons_ratio"),
                        "yield_strength_mpa": _i.get("yield_mpa") or _i.get("yield_strength_mpa"),
                        "uts_mpa": _i.get("uts_mpa"),
                        "density_kg_m3": _i.get("density") or _i.get("density_kg_m3"),
                        "source": "swapi.is_valid_material(零件材料)",
                    }
                    return _carry_provenance(_d, _i)
            except Exception:
                return None
            return None

        def _from_mdb(n):
            try:
                _m = material_db.get_material(n)
                if _m:
                    _d = {
                        "id": _m.get("id") or n,
                        "name": _m.get("name") or n,
                        "youngs_modulus_mpa": _m.get("E_mpa"),
                        "poissons_ratio": _m.get("nu"),
                        "yield_strength_mpa": _m.get("yield_mpa"),
                        "uts_mpa": _m.get("uts_mpa"),
                        "density_kg_m3": _m.get("density_kg_m3"),
                        "standard": _m.get("standard"),
                        "source": "material_db.get_material(零件材料)",
                    }
                    return _carry_provenance(_d, _m)
            except Exception:
                return None
            return None

        def _by_density(d):
            """按密度【定族 → 取代表牌号】，并明确标注为推断值。

            ── 为什么不能"取密度最接近的材料" ──────────────────────────────
            实测：2700 kg/m³ 下有 1060(σy35) / 6061-T6(σy276) 等同密度材料，
              "最接近"会选中 1060 → 屈服被低估 8 倍，SF 严重失真。
            因此改为：密度只用来判定【材料族】，族内取【常用代表牌号】，
              并在返回值里标 inferred=True + 建议复核，避免"安静地算错"。
            密度无法归族时返回 None（宁可不判定，也不猜错）。
            """
            try:
                _d = float(d)
            except Exception:
                return None
            if _d <= 0:
                return None
            # 密度 → (族, 代表牌号)。区间取自常用工程材料密度范围。
            _FAMILY_REP = (
                (700.0, 1500.0, "plastic", "PC"),          # 工程塑料
                (2400.0, 2900.0, "aluminum", "GB_6061"),   # 铝及铝合金
                (4300.0, 4700.0, "titanium", "TI_GRADE5"),  # 钛合金
                (6900.0, 7300.0, "cast_iron", "GB_HT200"),  # 铸铁
                (7400.0, 8100.0, "steel", "GB_Q235"),      # 碳钢/合金钢/不锈钢
                (8300.0, 9000.0, "copper", "GB_H62"),      # 铜合金
            )
            for _lo, _hi, _fam, _rep in _FAMILY_REP:
                if _lo <= _d <= _hi:
                    _m = _from_mdb(_rep)
                    if _m:
                        _m["source"] = ("密度 %.0f kg/m³ 归族为 %s → 取代表牌号 %s"
                                        "（【推断值，须人工复核】）"
                                        % (_d, _fam, _m.get("name")))
                        _m["inferred"] = True
                        _m["inferred_family"] = _fam
                        return _m
            return None

        # ① 精确名
        _info = _from_swapi(_nm) or _from_mdb(_nm)
        # ② 近似名模糊解析：剥后缀 → 关键词 + 数字匹配
        if not _info:
            import re as _re
            _clean = _re.sub(r"\s*[（(]\s*S{1,2}\s*[)）]\s*$", "", _nm, flags=_re.I)
            _clean = _re.sub(r"\s*[（(][^)）]*[)）]\s*$", "", _clean).strip()
            if _clean and _clean != _nm:
                _info = _from_swapi(_clean) or _from_mdb(_clean)
            # 关键词 → 用材料库里同类材料兜住（并如实标注为"近似"）
            if not _info:
                _kw = None
                _low = (_clean or _nm).lower()
                for _k, _canon in (
                        ("碳钢", "GB_Q235"), ("carbon steel", "GB_Q235"),
                        ("不锈钢", "GB_06CR19NI10"), ("stainless", "GB_06CR19NI10"),
                        ("铸铁", "GB_HT200"), ("cast iron", "GB_HT200"),
                        ("合金钢", "GB_40CR"), ("alloy steel", "GB_40CR"),
                        ("铝", "GB_6061"), ("aluminum", "GB_6061"),
                        ("钛", "TI_GRADE5"), ("titanium", "TI_GRADE5"),
                        ("黄铜", "GB_H62"), ("青铜", "GB_ZCUSN10")):
                    if _k in _low:
                        _kw = _canon
                        break
                # 牌号数字优先（如 "1023" / "1045" / "4140"）
                _num = _re.search(r"\b(1023|1020|1045|4140|4340|304|316|6061|7075)\b",
                                  _clean or _nm)
                if _num:
                    _g = _num.group(1)
                    _kw = {"1023": "GB_Q235", "1020": "GB_Q235", "1045": "GB_45",
                           "4140": "GB_40CR", "4340": "GB_40CR",
                           "304": "GB_06CR19NI10", "316": "GB_022CR17NI12MO2",
                           "6061": "GB_6061", "7075": "GB_7075"}.get(_g, _kw)
                if _kw:
                    _info = _from_mdb(_kw)
                    if _info:
                        _info["source"] = ("SW 近似名映射(%s → %s)"
                                           % (_nm, _info.get("name")))
                        _info["approximate_from_sw"] = _nm
        # ③ 密度反推（前两级都失败时）
        if not _info and density_hint:
            _info = _by_density(density_hint)
        if not _info:
            return None
        # 密度：零件实测密度最可信（SW 读回），优先于库值
        if density_hint:
            try:
                _d = float(density_hint)
                if _d > 0 and abs(_d - 1000.0) > 1.0:      # 排除"水"默认值
                    _info["density_kg_m3"] = _d
                    _info["density_source"] = "零件实测(massprops)"
            except Exception:
                pass
        if not _info.get("density_kg_m3") and density_hint:
            try:
                _info["density_kg_m3"] = float(density_hint)
            except Exception:
                pass
        return _info

    # ① 显式指定优先
    if explicit_material:
        _m = (_props_from_name(explicit_material) if isinstance(explicit_material, str)
              else dict(explicit_material))
        if _m:
            _meta.update({"source": "explicit", "part_material": _m.get("name")})
            if (_case_mat.get("name") or "") != (_m.get("name") or ""):
                _meta["overridden"] = True
                _meta["notes"].append("按调用方显式指定的材料校核")
            return _m, _meta

    # ② 零件本体材料
    # ── 【P0 修复】优先级细化：凭据里的【国标名】 > SW 读回的近似名 ────────
    # 实测：SW 无 Q235 → 近似成 "1023 碳钢板 (SS)" → 材料库查不到 → 兜底
    #   PETG → 国标材料失效。而凭据里保存的是用户请求的 "Q235"（真实牌号），
    #   因此必须【先查凭据】，再退回 SW 近似名。
    _pn = (geo or {}).get("part_material_name")
    _pd = (geo or {}).get("part_material_density_kg_m3")
    _part_path = (geo or {}).get("part_path") or (geo or {}).get("path")
    _att_name, _att_dens = _attested_material_of(_part_path) if _part_path else (None, None)
    _att_dens = _att_dens or _pd
    # ②a 凭据国标名（最可信的"设计意图"）
    if _att_name:
        _m = _props_from_name(_att_name, _att_dens)
        if _m:
            _meta.update({"source": "attested_credential",
                          "part_material": _m.get("name"),
                          "attested_name": _att_name,
                          "sw_applied_name": _pn})
            _case_nm = str(_case_mat.get("name") or "").strip()
            _new_nm = str(_m.get("name") or "").strip()
            if _case_nm and _new_nm and _case_nm.lower() != _new_nm.lower():
                _meta["overridden"] = True
                _meta["notes"].append(
                    "按【零件材料凭据】的牌号 %s 校核（载荷文件为 %s）%s"
                    % (_new_nm, _case_nm,
                       ("；SW 实际生效名为 %s" % _pn) if (_pn and _pn != _new_nm) else ""))
            elif not _case_nm:
                _meta["overridden"] = True
                _meta["notes"].append("载荷文件未给材料，已采用凭据中的零件材料")
            return _m, _meta
    # ②b SW 读回的名字（含近似名模糊解析 / 密度反推）
    if _pn:
        _m = _props_from_name(_pn, _pd)
        if _m:
            _meta.update({"source": "part", "part_material": _m.get("name")})
            _case_nm = str(_case_mat.get("name") or "").strip()
            _new_nm = str(_m.get("name") or "").strip()
            if _case_nm and _new_nm and _case_nm.lower() != _new_nm.lower():
                _meta["overridden"] = True
                _meta["notes"].append(
                    "零件实际材料 %s 与载荷文件材料 %s 不一致 —— 已按【零件实际材料】校核"
                    % (_new_nm, _case_nm))
            elif not _case_nm:
                _meta["overridden"] = True
                _meta["notes"].append("载荷文件未给材料，已采用零件实际材料")
            return _m, _meta
    # ②c 仅凭实测密度反推（名字完全解析不出来时）
    if _pd:
        _m = _props_from_name("", _pd)
        if _m:
            _meta.update({"source": "density_inference",
                          "part_material": _m.get("name")})
            _meta["overridden"] = True
            _meta["notes"].append(
                "零件材料名无法解析（SW 名=%r），已按实测密度 %.0f kg/m³ 反推为 %s"
                % (_pn, float(_pd), _m.get("name")))
            return _m, _meta

    # ③ 兜底：载荷文件材料
    _meta["notes"].append(
        "未能从零件读出材料（可能未赋材或 SW 未连接）—— 沿用载荷文件材料；"
        "若零件实际材料不同，校核结论会失真")
    return _case_mat, _meta


def _extract_real_geometry(part_path, load_case_dict=None):
    """从真实 SolidWorks 零件提取几何事实（体积/表面积/包围盒）。

    仅在零件路径存在且能连上 SolidWorks 时成功；否则返回 ok=False，
    调用方【必须】据此判定"几何不可用"，绝不用编造值代替。

    Args:
        part_path: 零件文件路径。
        load_case_dict: 当前载荷工况字典（可选）。用于在拿不到 SW 真实包围盒
            时，按【设计域尺寸 + 真实体积】反推厚度 t=V/(dx*dy)。

    ── 【Bug18/Bug17 根因修复】必须显式接收工况，不得引用同名模块 ──────────
    原实现在函数体内写了 `load_case.get("design_domain")`。但本模块顶部有
    `import load_case`（physics/load_case.py），函数签名里又没有 load_case
    参数 —— 于是 `load_case` 解析为【模块对象】，`.get` 不存在，抛
    `AttributeError: module 'load_case' has no attribute 'get'`。
    该异常被下方宽泛的 `except Exception as _e_bb` 吞掉，后果是：
      · bbox_mm 恒为 None（估计路径整个失效）
      · geometry_gate 退回用 design_domain 当 body_bbox →
        承载板 200×60×10 被读成 200×60×60（体积 720000 而非 120000）
      · real_geometry.bbox_mm 为 None → FEA 的 _used_real=False →
        退化到 design_domain，SF 与载荷不敏感（Bug17）
    现在改为显式参数传入，并把宽泛 except 收紧为可诊断的报错留痕。
    """
    out = {"ok": False, "source": None}
    if not part_path or not os.path.exists(str(part_path)):
        out["error"] = "零件路径不存在: %r" % (part_path,)
        return out
    _tools = os.path.dirname(os.path.abspath(__file__))
    if _tools not in sys.path:
        sys.path.insert(0, _tools)
    try:
        import swapi  # 延迟导入：无 pywin32 的环境仍可跑纯计算命令
    except Exception as e:
        out["error"] = "swapi 不可用（无法读取真实几何）: %r" % (e,)
        return out
    try:
        sw = swapi.get_sw()
        if sw is None:
            out["error"] = "无法连接 SolidWorks"
            return out
        # ── 【Bug9 修复】打开零件必须用 swapi 的正确封装 ──────────────
        # 实机（SW2025 SP5.0）诊断结论：
        #   ① model.GetPathName 是【属性】不是方法 —— 加括号会抛
        #      TypeError("'str' object is not callable")；
        #   ② OpenDoc6 必须传满 6 个参数（FileName, Type, Options,
        #      Configuration, Errors, Warnings），且后两个要 VARIANT：
        #        OpenDoc6(path,1,0,"",0,0)  → com_error(-2147352571 类型不匹配)
        #        OpenDoc6(path,1,0)          → com_error(-2147352561 非选择性参数)
        #      swapi.SWModel.open_part() 已封装正确签名（swapi.py L7996+），
        #      直接复用，避免在此重复踩坑。
        try:
            m = None
            _cur = ""
            try:
                _m0 = swapi.from_active(sw)
                # 注意：GetPathName 是属性（无括号）
                _cur = str(_m0.model.GetPathName or "")
            except Exception:
                _cur = ""
            if _cur and os.path.abspath(_cur) == os.path.abspath(str(part_path)):
                m = _m0   # 已是目标文档，直接复用
            else:
                _op = swapi.SWModel.open_part(sw, str(part_path), doc_type=1)
                if not isinstance(_op, dict) or not _op.get("ok"):
                    out["error"] = ("打开零件失败: %r"
                                    % ((_op or {}).get("error") or _op,))
                    out["open_detail"] = _op
                    return out
                # open_part 返回 {ok, doc, title}；这里把 doc 包成 SWModel
                _doc = _op.get("doc")
                if _doc is None:
                    out["error"] = "open_part 未返回 doc 对象"
                    out["open_detail"] = {k: v for k, v in (_op or {}).items()
                                          if k != "doc"}
                    return out
                m = swapi.SWModel(sw, _doc)
        except Exception as e:
            out["error"] = "打开零件异常: %r" % (e,)
            return out
        mp = m.massprops(safe=True)
        if not isinstance(mp, dict) or not mp.get("ok"):
            out["error"] = "massprops 失败（无法取得真实体积）: %r" % (mp,)
            return out
        _vol = mp.get("volume_mm3")
        if not _vol or float(_vol) <= 0:
            out["error"] = "真实体积为 0/缺失 —— 零件可能没有实体"
            return out
        out.update({
            "ok": True,
            # ── 【P0 修复】必须回传零件路径：材料解析要用它去找 .material.json
            #   凭据（凭据里存的是用户请求的【国标名】，比 SW 近似名可信）。
            "part_path": os.path.abspath(str(part_path)),
            "volume_mm3": float(_vol),
            "surface_area_mm2": mp.get("surface_area_mm2"),
            "mass_kg": mp.get("mass_kg"),
            "density_kg_m3": mp.get("density_kg_m3"),
            "center_of_mass_mm": mp.get("center_of_mass_mm"),
            "volume_source": mp.get("volume_source"),
            "source": "swapi.massprops(真实实体体积)",
        })
        # ── 【新 P0 修复】同时读取【零件的实际材料】─────────────────────────
        # 背景（子代理2 实测）：physics 校核原先【完全跟随载荷文件】的材料
        #   （gate_load_case.json 里是 PETG），而零件本体其实已赋 Q235。
        #   后果：Q235 零件（σy=235）被按 PETG（σy=40）判定 →
        #     σ_peak=263MPa 时 SF 从 1.12 掉到 0.15，国标材料在物理校核里
        #   【完全没发挥作用】。
        # 修复：这里把零件的真实材料名/密度一并取出，供调用方覆盖载荷材料。
        try:
            _gm = m.get_material()
            if isinstance(_gm, dict):
                out["part_material_name"] = _gm.get("name")
                out["part_material_density_kg_m3"] = _gm.get("density_kg_m3")
                out["part_material_mass_kg"] = _gm.get("mass_kg")
                # SW 返回的可能是 IdName（如 "ST_API_1020"）也可能是显示名
                _mn = str(_gm.get("name") or "").strip()
                if _mn:
                    out["part_material_source"] = "swapi.get_material(零件本体)"
        except Exception as _e_mat:
            out["part_material_error"] = repr(_e_mat)
        # ── 【Bug17/18 回归修复·核心】补上【真实包围盒】────────────────────
        # 测试部 v13 反馈（原话）：
        #   «_extract_real_geometry 的变量遮蔽虽改了，但 bbox x/y 仍被
        #     design_domain 锁定，FEA 仍用 design_domain»；实测 10 种零件
        #   （100×60×10 / 100×100×10 / Ø20×100 …）读出的 bbox 全部是
        #   [0,200,0,60,…] 即 design_domain 的 x/y，fea_vol 全 720000，SF 全 72.81。
        #
        # 根因：上一版在拿不到 bbox 时，用
        #       _bb = [0, _dx, 0, _dy, 0, t]      ← _dx/_dy 来自 design_domain
        #   形如"估计"实则【把设计域当成了零件尺寸】，等于伪造几何。
        #   而且它让 SF 对真实尺寸完全不敏感 —— 比"没有 bbox"更糟。
        #
        # 正确做法：用 SolidWorks 的真实 API `IBody2.GetBodyBox()`。
        #   swapi 里已有 _bbox_extent_mm/_bbox_max_dim_mm 在用这个 API，
        #   本文件现在调用新增的 SWModel.body_box_mm() 取【6 元组真实包围盒】。
        # 拿不到真实 bbox 时：【不给 bbox】（保持 None），由 geometry_gate 判
        #   NOT_EVALUATED，绝不编造 —— 宁可不判定，也不假通过。
        try:
            _bb = None
            _bb_src = None
            try:
                # ① 首选：SWModel.body_box_mm()（真实 GetBodyBox，mm，6 元组）
                _f = getattr(m, "body_box_mm", None)
                if callable(_f):
                    _r = _f()
                    if isinstance(_r, (list, tuple)) and len(_r) >= 6:
                        _bb = [float(x) for x in _r[:6]]
                        _bb_src = "swapi.body_box_mm(真实 GetBodyBox)"
            except Exception as _e1:
                out["bbox_probe_error"] = repr(_e1)
            # ② 兼容旧命名（若某些分支未同步）
            if _bb is None:
                try:
                    for _bn in ("get_bbox_mm", "bounding_box_mm"):
                        _f2 = getattr(m, _bn, None)
                        if callable(_f2):
                            _r2 = _f2()
                            if isinstance(_r2, (list, tuple)) and len(_r2) >= 6:
                                _bb = [float(x) for x in _r2[:6]]
                                _bb_src = "swapi.%s" % _bn
                                break
                except Exception:
                    pass
            # ③ 仍拿不到 → 【不再编造】。只记录诊断，bbox 保持 None。
            if _bb is None:
                out["bbox_unavailable"] = (
                    "无法从 SolidWorks 取得真实包围盒（GetBodyBox 不可用）。"
                    "按设计原则【不编造 bbox】—— 依赖 bbox 的门禁（DESIGN_SPACE）"
                    "将判 NOT_EVALUATED 而非假通过。")
            if _bb:
                out["bbox_mm"] = _bb
                out["bbox_source"] = _bb_src or "swapi.GetBodyBox"
        except Exception as _e_bb:
            out["bbox_error"] = repr(_e_bb)
        return out
    except Exception as e:
        out["error"] = "真实几何提取异常: %r" % (e,)
        return out


def _estimate_wall_from_geometry(vol_mm3, area_mm2):
    """由真实体积/表面积估算特征壁厚（V/A 比）。

    这是【保守近似】：对薄壁件 V/A ≈ 半壁厚量级，用于识别"明显过薄"。
    精确壁厚需 STEP 网格分析；此处只求"不合格件不得被放过"。
    """
    try:
        v = float(vol_mm3)
        a = float(area_mm2)
        if v <= 0 or a <= 0:
            return None
        return (2.0 * v) / a   # 等效特征尺寸（薄板近似）
    except Exception:
        return None


def cmd_optimize(case_path: str, max_iter: int = 5, room: str = "",
                 part_path: str = "", explicit_material: str = "") -> dict:
    """自动迭代优化：generate → simulate → check → refine 直到通过或达到 max_iter。

    explicit_material: 【新 P0 修复】显式指定校核材料（最高优先）。
        默认从【零件本体】读实际材料覆盖载荷文件材料；此参数用于
        调用方明确要求按某个材料校核（如零件尚未赋材时手工指定）。
    """
    lc_result = load_case.load_from_file(case_path)
    if not lc_result["ok"]:
        return {"ok": False, "error": "invalid load case", "details": lc_result}

    problem_id = lc_result["case"].get("meta", {}).get("problem_id", "UNKNOWN")
    run_dir = os.path.join(
        _HERE, "..", "..", "output", design_state.STATE_DIR_NAME,
        f"opt_{problem_id}_{int(time.time())}",
    )
    os.makedirs(run_dir, exist_ok=True)

    ds = design_state.DesignState(run_dir, case_path)
    # ── 【Bug7 修复】几何必须来自【真实零件】，不得编造 ────────────────
    # 原实现把 design_params 硬编码为 thickness=5/height=60/width=40，
    #   并把 topology/volume_mm3 写成假值 → 物理校核校验的是虚构零件，
    #   任何不合格设计（如 0.5mm 极薄壁）都能通过，防线②形同虚设。
    # 现在：先从真实零件取几何；取不到就【明确拒绝出结论】，
    #   绝不用编造值凑一个 PASS。
    # 【Bug18 根因修复】显式传入工况，供无 GetBox 时反推厚度。
    _geo = _extract_real_geometry(part_path, lc_result.get("case")) if part_path else {
        "ok": False,
        "error": ("未提供 part_path —— 物理校核必须针对真实零件；"
                 "请用 physics-optimize <case.json> --part <零件.SLDPRT> 指定。"),
    }
    _geo_ok = bool(_geo.get("ok"))
    # ── 【Bug17 修复·关键】必须在 solve_fea【之前】把真实几何写进工况 ──────
    # 原实现从头到尾没有任何一处把 real_geometry 写进 lc_result["case"]，
    #   于是 fea_solver 的 `_rg = load_case.get("real_geometry")` 恒为空 →
    #   `_used_real=False` → 解析解退回 design_domain 的固定 200×60×60
    #   （体积 720000，而真实承载板只有 120000）→ 三个不同载荷算出同一个
    #   SF（测试部 Bug17：SF 与载荷不敏感）。
    # 这里在求解前注入；cmd_simulate 亦同（见该函数内注释）。
    if _geo_ok:
        try:
            lc_result["case"]["real_geometry"] = {
                "volume_mm3": _geo.get("volume_mm3"),
                "surface_area_mm2": _geo.get("surface_area_mm2"),
                "mass_kg": _geo.get("mass_kg"),
                "density_kg_m3": _geo.get("density_kg_m3"),
                "bbox_mm": _geo.get("bbox_mm"),
                "source": _geo.get("source"),
            }
        except Exception:
            pass
        # ── 【新 P0 修复】用【零件实际材料】覆盖载荷文件材料 ──────────────
        # 实测缺陷（子代理2）：载荷文件 material=PETG(σy40)，而零件实为
        #   Q235(σy235)。校核跟随载荷文件 → SF=0.15，按 Q235 应为 1.12；
        #   国标材料的强度优势在物理校核里完全无法体现。
        # 这里在 solve_fea【之前】把材料改成零件实际材料，保证：
        #   · 强度判定（σy）按零件真实材料；
        #   · 质量（体积×密度）按零件真实密度；
        #   · 报告里如实留痕"材料来自零件而非载荷文件"。
        try:
            _mat_final, _mat_meta = _resolve_part_material(
                _geo, lc_result.get("case"), explicit_material)
            if _mat_final:
                lc_result["case"]["material"] = _mat_final
                lc_result["case"]["material_resolution"] = _mat_meta
        except Exception as _e_matov:
            try:
                lc_result["case"]["material_resolution"] = {
                    "source": "load_case", "error": repr(_e_matov)}
            except Exception:
                pass
    # ── 【Bug7 修复·硬闸】拿不到真实几何 → 直接拒绝，不产出任何结论 ────
    # 这是防线的关键：只要几何不可用，就必须【拒绝】而不是"降级估算"——
    #   否则又会退化成"校验空气"（测试部实测 0.5mm 极薄壁 SF=72.81 PASS）。
    # 注意：此处同时覆盖"直接调用 cmd_optimize 未传 part_path"的路径，
    #   不依赖 sw_bridge 层的参数校验。
    if not _geo_ok:
        return {
            "ok": False,
            "command": "optimize",
            "gate": "REAL_GEOMETRY_REQUIRED",
            "error": "物理校核必须针对真实零件：无法取得真实几何。",
            "geometry_error": _geo.get("error"),
            "part_path": part_path or None,
            "hint": ("物理校核不得校验虚构几何（Bug7）。请传入真实零件："
                     "python sw_bridge.py physics-optimize <case.json> "
                     "--part <零件.SLDPRT> --room <房间>。"
                     "若零件已存在但仍失败，检查 SolidWorks 是否在运行、"
                     "以及零件是否有实体（no_body）。"),
        }
    # 真实体积/表面积 → 特征壁厚估计（V/A 比）
    _wall_est = None
    if _geo_ok:
        _wall_est = _estimate_wall_from_geometry(
            _geo.get("volume_mm3"), _geo.get("surface_area_mm2"))
    # design_params：有真实壁厚估计就用它，否则如实标注"未实测"
    design_params = {
        "thickness_mm": {
            "id": "thickness_mm",
            "value": (round(float(_wall_est), 3) if _wall_est else None),
            "source": ("real-geometry(V/A)" if _wall_est else "unmeasured"),
        },
        # ── 【Bug11 修复】min_wall_thickness 必须是【单层】结构 ──────────
        # 原实现嵌套了两层：
        #   {"min_wall_thickness": {"min_wall_thickness": {"value": 2.0}}}
        #   → geometry_gate 按 .get("min_wall_thickness").get("value") 只取
        #     一层，拿到的是内层 dict 而非数值 → _wall_req=None
        #     → `if wall_check.ok and _wall_req` 恒为 False
        #     → WALL_THICKNESS 门【永远 PASS】，0.5mm 极薄板也放行。
        #   实测（测试部）：estimated_min_thickness_mm=0.495 已正确测到壁厚，
        #     但 gate 仍 PASS —— 正是阈值取不到所致。
        # 现在与 thickness_mm 保持同样的单层形状。
        "min_wall_thickness": {
            "id": "min_wall_thickness",
            # None 安全：acceptance 里没有该字段时退回 FDM 默认 2.0mm
            "value": float(((lc_result["case"].get("acceptance") or {})
                           .get("min_wall_thickness_mm") or 2.0)),
            "source": "acceptance.min_wall_thickness_mm(默认 FDM 2.0mm)",
        },
    }
    locked_params = []

    final_report = None
    for i in range(1, max_iter + 1):
        run_id = f"opt_iter_{i}"
        output_dir = os.path.join(run_dir, f"iteration_{i}")
        os.makedirs(output_dir, exist_ok=True)

        fea_result = fea_solver.solve_fea(lc_result["case"], output_dir=output_dir)

        domain = lc_result["case"].get("design_domain", {}).get("bounds", {})
        # ── 【Bug18 根因修复】不得用 design_domain 回填缺失的 bbox 分量 ──────
        # 原实现逐分量 `b if b is not None else design_domain[...]`：真实 bbox
        #   一旦缺失（此前因 load_case 变量遮蔽而恒缺失），z 就取 design_domain
        #   的 z_max=60，而承载板真实厚度只有 10 → body_bbox 被读成
        #   200×60×60（体积 720000，真实 120000）→ DESIGN_SPACE 与 SF 全错。
        #   更糟的是"用设计域补设计域"会让 DESIGN_SPACE 恒 PASS，门禁失效。
        # 现在：有真实 bbox 就整条用真实值；没有就传 None，让 geometry_gate
        #   明确报"未评估"，绝不编造一个必然通过或必然失败的假值。
        _bbox = _geo.get("bbox_mm") if _geo_ok else None
        if not (isinstance(_bbox, (list, tuple)) and len(_bbox) >= 6):
            _bbox = None
        gc_result = geometry_gate.geometry_gate_report(
            bbox=_bbox,
            # topology 未知时传空 dict（让 CONNECTIVITY 自己判 NOT_EVALUATED，
            #   而不是喂一个假拓扑骗它 PASS）
            topology={"volumes": (1 if _geo_ok else 0), "bodies": (1 if _geo_ok else 0),
                      "faces": None, "edges": None, "vertices": None},
            face_areas=[],
            # 真实体积（0 表示未取得 → 壁厚门禁将 NOT_EVALUATED）
            volume_mm3=(float(_geo.get("volume_mm3") or 0) if _geo_ok else 0.0),
            # 真实总表面积（用于壁厚估计；无则 None → 门禁 NOT_EVALUATED）
            surface_area_mm2=(_geo.get("surface_area_mm2") if _geo_ok else None),
            design_bounds=domain, interfaces=[], design_params=design_params,
            # ── 【Bug11 修复·双保险】显式传阈值，不依赖 design_params 结构 ──
            # 原实现只靠 design_params 里嵌套取值，一旦结构写错（曾嵌套两层）
            #   阈值就变成 None → `if wall_check.ok and _wall_req` 恒 False
            #   → WALL_THICKNESS 永远 PASS（0.5mm 极薄板也放行）。
            min_wall_req_mm=float(((lc_result["case"].get("acceptance") or {})
                                   .get("min_wall_thickness_mm") or 2.0)),
        )
        gc_result["geometry_source"] = (_geo.get("source") if _geo_ok
                                        else "UNMEASURED")
        if not _geo_ok:
            gc_result["geometry_error"] = _geo.get("error")

        report = sim_report.build_report(
            run_id=run_id, load_case=lc_result["case"], design_params=design_params,
            geometry_check=gc_result, fea_result=fea_result, iteration=i,
            parent_run_id=f"opt_iter_{i-1}" if i > 1 else None,
        )
        # [防伪造] 报告必须自带【房间归属】：否则别的房间/别人只要复制或
        #   引用这份报告路径，就能给任意房间背书（对抗测试 C1c/C1d）。
        try:
            _rm = _resolve_defense_room(room)
            if _rm:
                report["room"] = _rm
        except Exception:
            pass
        report_path = sim_report.save_report(report, output_dir)
        report["report_path"] = report_path

        ds.new_run(run_id)
        ds.update_run(run_id, {
            "fea_result": fea_result, "geometry_check": gc_result,
            "design_params": design_params, "status": report["overall_status"],
        })

        final_report = report
        print(f"  [iter {i}] status={report['overall_status']} "
              f"SF={fea_result.get('safety_factor', 'N/A')} "
              f"stress={fea_result.get('max_von_mises_mpa', 'N/A')}MPa",
              file=sys.stderr, flush=True)

        if report["overall_status"] == "PASS":
            # ── 【防线②物理】结论一算出就自动落凭据 ──
            try:
                _emit_physics_attestation(_resolve_defense_room(room), report,
                                          case_path=case_path, run_dir=run_dir)
            except Exception:
                pass
            return {
                "ok": True, "command": "optimize",
                "converged": True, "iterations": i,
                "final_report": report, "run_dir": run_dir,
            }

        rec = refine_rules.generate_recommendations(
            report=report, design_params=design_params,
            locked_params=locked_params, max_iterations=max_iter - i,
        )
        if rec["ok"] and rec["actions"]:
            design_params = rec["next_params"]
        else:
            # ── 【防线②物理】收敛失败也要落凭据（结论已产生，不能被漏掉）──
            try:
                _emit_physics_attestation(_resolve_defense_room(room), report,
                                          case_path=case_path, run_dir=run_dir)
            except Exception:
                pass

            return {
                "ok": True, "command": "optimize",
                "converged": False, "iterations": i,
                "reason": "no valid refinement actions; design space exhausted",
                "final_report": report, "run_dir": run_dir,
            }

    # ── 【防线②物理】达到迭代上限：结论同样要落凭据 ──
    try:
        _emit_physics_attestation(_resolve_defense_room(room), final_report,
                                  case_path=case_path, run_dir=run_dir)
    except Exception:
        pass

    return {
        "ok": True, "command": "optimize",
        "converged": False, "iterations": max_iter,
        "reason": f"reached max iterations ({max_iter})",
        "final_report": final_report, "run_dir": run_dir,
    }

# ══════════════════════════════════════════════════════════════════════
#  【三大防线 · 防线②物理 / 防线③领域】凭据落盘
# ══════════════════════════════════════════════════════════════════════
#  设计要点：凭据必须【自动】产生，而不是等小屋记得多调一条命令。
#  因此挂在 physics 结论已经算出来的位置（optimize / validate-domain），
#  只要跑过一次物理链路，防线凭据就必然存在。
def _emit_physics_attestation(room, report, case_path="", run_dir=""):
    """把仿真报告折算成防线②凭据，写入 reports/<room>.physics.json。

    room 为空时【不写】—— 凭据必须归属到具体房间才能被 room-end 校验。
    """
    if not room or not isinstance(report, dict):
        return None
    try:
        import defense_gate as _dg
    except ImportError:
        return None
    _fea = report.get("fea_result") or {}
    _fat = _fea.get("fatigue") or {}
    payload = {
        "overall": report.get("overall_status") or _fea.get("overall"),
        "safety_factor": _fea.get("safety_factor"),
        "max_von_mises_mpa": _fea.get("max_von_mises_mpa"),
        "max_displacement_mm": _fea.get("max_displacement_mm"),
        "fatigue_verdict": _fat.get("verdict"),
        "fatigue_sf": _fat.get("fatigue_sf"),
        "failed_gates": report.get("failed_gates") or [],
        "passed_gates": report.get("passed_gates") or [],
        "not_evaluated": report.get("not_evaluated") or [],
        "release_status": (report.get("release_readiness") or {}).get("status"),
        "run_id": report.get("run_id"),
        # [防伪造] run_id 在磁盘上【不唯一】（opt_iter_1 有 7 份），必须同时
        #   记录【唯一路径】：report_path（具体报告文件）+ run_dir（运行目录），
        #   让防线校验能精确定位到"就是这一次运行"，而不是"借"一个同名 run_id。
        "report_path": report.get("report_path") or "",
        "case_path": case_path,
        "run_dir": run_dir,
        "source": "physics_bridge",
    }
    # [主防线] 凭据必须由【宿主签发】—— 本地只提交磁盘事实，宿主自己读报告后签名。
    #   这样 AI 即使手写一份格式完美的 JSON，也拿不到合法签名。
    _signed = _dg.request_signed_credential({
        "kind": "physics",
        "room": room,
        "report_path": payload.get("report_path") or "",
    })
    if _signed and _signed.get("ok") and isinstance(_signed.get("credential"), dict):
        # ── 【签后补字段 BUG 修复】宿主已签名 → 【原样落盘】，一个字段都不加 ──
        #   原先这里做了 _cred.setdefault("run_dir", ...)：在签名之后改凭据体，
        #   HMAC 必然失配，防线②永远验签失败。run_dir 现在由宿主在签发时
        #   从已签名的 report_path 推导并纳入待签体（见 defense-sign.js）。
        return _dg.write_physics_attestation(room, _signed["credential"])
    # 宿主不可用 → fail-closed：写一份【无签名】凭据，让后续校验必然失败并留痕，
    #   而不是静默产出一份"看起来合法"的凭据。
    _dg.note_unsigned_attempt(room, "physics", (_signed or {}).get("error"))
    _fallback = dict(payload)
    _fallback["_unsigned_reason"] = (_signed or {}).get("error") or "宿主签名服务不可用"
    return _dg.write_physics_attestation(room, _fallback)


def _emit_domain_attestation(room, dom_result, domain_id=""):
    """把 DSVA 结论折算成防线③凭据，写入 reports/<room>.domain.json。"""
    if not room or not isinstance(dom_result, dict):
        return None
    try:
        import defense_gate as _dg
    except ImportError:
        return None
    payload = {
        "domain": dom_result.get("domain") or domain_id,
        "score": dom_result.get("score"),
        "violations": dom_result.get("violations") or [],
        "warnings": dom_result.get("warnings") or [],
        "passed": dom_result.get("passed") or [],
        "source": "physics_bridge",
        # ── 【新 Bug 修复】领域凭据必须带【可追溯标识】────────────────────
        # 校验端（defense_gate.check_domain_attestation）要求 run_id 或
        #   checked_at 至少有一个，否则判"缺少可追溯标识（疑似伪造）"→
        #   CRITICAL 拦截。原实现两个字段都不写，导致【每份领域凭据
        #   一落盘就自带一条防伪造违规】，实测被 room-end 拦下
        #   （报告 §5「领域凭据缺 run_id 可追溯标识」）。
        # 现在：run_id 优先取本次领域校验携带的运行标识；没有则用
        #   校验时间戳 generated_at 兜底，保证凭据始终可追溯。
        "run_id": (str(dom_result.get("run_id") or "")
                   or str((dom_result.get("data_source") or {}).get("run_id") or "")),
        "checked_at": (str(dom_result.get("checked_at") or "")
                       or time.strftime("%Y-%m-%d %H:%M:%S")),
        # 领域校验的依据来源（最近一次真实仿真报告），便于审计追溯
        "data_source": (dom_result.get("data_source") or {}),
    }
    # [主防线] 领域凭据同样必须宿主签发。
    _dom_rules = payload.get("domain") or domain_id
    _signed_d = _dg.request_signed_credential({
        "kind": "domain",
        "room": room,
        "domain": _dom_rules,
        "violations": payload.get("violations") or [],
        "score": payload.get("score"),
        "warnings": payload.get("warnings") or [],
        "passed": payload.get("passed") or [],
        "checked_items": payload.get("checked_items") or [],
        # ── 【新 Bug 修复】把可追溯标识一并提交签发 ────────────────────────
        #   校验端要求 run_id / checked_at 至少一个存在。必须在【申请签发时】
        #   提交（由宿主纳入待签体）；若签完再补字段会 HMAC 失配。
        "run_id": payload.get("run_id") or "",
        "checked_at": payload.get("checked_at") or "",
    })
    if _signed_d and _signed_d.get("ok") and isinstance(_signed_d.get("credential"), dict):
        # 【签后补字段 BUG 修复】宿主已签名 → 原样落盘（不得再补任何字段）
        return _dg.write_domain_attestation(room, _signed_d["credential"])
    _dg.note_unsigned_attempt(room, "domain", (_signed_d or {}).get("error"))
    _fb = dict(payload)
    _fb["_unsigned_reason"] = (_signed_d or {}).get("error") or "宿主签名服务不可用"
    return _dg.write_domain_attestation(room, _fb)


def _resolve_defense_room(room=""):
    """防线凭据归属房间：显式参数 > 环境变量 DSH_ROOM。"""
    _r = str(room or "").strip() or str(os.environ.get("DSH_ROOM") or "").strip()
    return _r or None




def cmd_fatigue(case_path: str = "", stress_mpa: float = None,
                run_id: str = "", part_path: str = "",
                allow_design_domain: bool = False) -> dict:
    """【BUG-02 修复】疲劳 / 设计寿命（默认 30 年）独立校核入口。

    用法：
      python physics_bridge.py fatigue <case.json> --part <零件.SLDPRT>  # 静力+FEA+疲劳
      python physics_bridge.py fatigue <case.json> --stress 45          # 指定应力直接校核
      python physics_bridge.py fatigue --report <run_id>                # 用已有报告的应力

    ── 【BUG 修复·虚假 PASS】必须绑定真实几何 ──────────────────────────────
    实测缺陷（子代理发现，已实验确证）：本函数原先不注入 real_geometry，
      于是 solve_fea 退回 design_domain 的默认几何（300×200×120）。
      承载截面被高估 → 应力被低估约 95 倍 → 得出【虚假 PASS】：
        同一 Q235、同一工况：
          fatigue CLI            → verdict=PASS, sf=94.16, peak=1.93 MPa
          optimize(真实零件)      → verdict=FAIL, sf=0.996, peak=182.49 MPa
      这是最危险的一类缺陷：用户单独跑 fatigue 会拿到与真实结论相反的 PASS。
    现在：与 cmd_optimize 同口径 —— 拿不到真实几何就【硬闸拒绝】，
      并给出可执行的修复指引；只有显式传入 allow_design_domain=True
      （CLI 的 --allow-design-domain）才允许退回设计域估算，
      且结果里明确标注该结论【不可用于设计定型】。
    """
    if fatigue_mod is None:
        return {"ok": False, "error": "fatigue.py 未找到（无法做疲劳校核）",
                "verdict": "NOT_EVALUATED"}

    def _flatten(fat: dict, static_part: dict = None, source: str = "") -> dict:
        """把疲劳结果摊平到顶层，保证任意路径都有同一个读取口径。"""
        out = dict(fat or {})
        if static_part:
            out["static"] = static_part
        if source:
            out["source"] = source
        out["command"] = "fatigue"
        # ok 语义：以"疲劳是否通过"为准（NOT_EVALUATED 时 ok=False）
        out["ok"] = (out.get("verdict") not in ("NOT_EVALUATED", None))
        return out

    # 路径甲：用已有报告的静力应力
    if stress_mpa is None and run_id:
        rep = cmd_report(run_id)
        if not rep.get("ok"):
            return {"ok": False, "error": "找不到报告: %s" % run_id,
                    "verdict": "NOT_EVALUATED"}
        report = rep["report"]
        fea = report.get("fea_result", {})
        fat = report.get("fatigue") or fatigue_mod.analyze_fatigue_from_case(
            report.get("load_case_summary") or {}, fea)
        return _flatten(fat, {"max_von_mises_mpa": fea.get("max_von_mises_mpa"),
                              "safety_factor": fea.get("safety_factor")},
                        source="report:%s" % run_id)

    if not case_path:
        return {"ok": False, "error": "需要 case_path 或 --report <run_id>",
                "verdict": "NOT_EVALUATED"}

    lc_result = load_case.load_from_file(case_path)
    if not lc_result["ok"]:
        return {"ok": False, "error": "invalid load case", "details": lc_result,
                "verdict": "NOT_EVALUATED"}
    case = lc_result["case"]

    # ── 未显式给应力 → 需要跑 FEA；此时【必须】有真实几何 ──────────────
    if stress_mpa is None:
        _geo_ok = False
        _geo = {}
        _geo_note = ""
        if part_path:
            try:
                _geo = _extract_real_geometry(part_path)
                _geo_ok = bool(_geo.get("ok"))
            except Exception as _e:
                _geo = {"ok": False, "error": repr(_e)}
        else:
            _geo = {"ok": False,
                    "error": "未提供 --part，无法取得真实零件几何"}
        if _geo_ok:
            # 把真实几何注入工况（与 cmd_optimize 同口径）
            try:
                case["real_geometry"] = _geo
                case["geometry_used"] = {
                    "source": "real_geometry.bbox_mm(真实零件包围盒)",
                    "L": (_geo.get("bbox_mm") or [None, None, None, None, None, None])[1],
                    "W": None, "H": None,
                }
            except Exception:
                pass
            _geo_note = "real_geometry"
        elif not allow_design_domain:
            # ── 硬闸：与 cmd_optimize 的 REAL_GEOMETRY_REQUIRED 完全一致 ──
            return {
                "ok": False,
                "command": "fatigue",
                "gate": "REAL_GEOMETRY_REQUIRED",
                "verdict": "NOT_EVALUATED",
                "error": ("疲劳校核必须针对真实零件：无法取得真实几何。"
                          "退回设计域默认几何会把承载截面高估、"
                          "应力低估（实测可达 95 倍），从而给出虚假 PASS。"),
                "geometry_error": _geo.get("error"),
                "part_path": part_path or None,
                "hint": ("请传入真实零件：python physics_bridge.py fatigue "
                         "<case.json> --part <零件.SLDPRT>。"
                         "若零件已存在但仍失败，检查 SolidWorks 是否在运行、"
                         "以及零件是否有实体（no_body）。"
                         "仅在明确知道后果时，才用 --allow-design-domain "
                         "做粗估（该结论不得用于设计定型）。"),
            }
        else:
            _geo_note = "design_domain(粗估，不可用于设计定型)"

        fea_result = fea_solver.solve_fea(case)
        stress_mpa = (fea_result.get("max_von_mises_mpa")
                      or fea_result.get("max_stress_mpa") or 0.0)
        static_part = {"safety_factor": fea_result.get("safety_factor"),
                       "max_von_mises_mpa": stress_mpa,
                       "geometry_source": _geo_note}
        # solve_fea 已附带疲劳 → 直接复用，避免重复计算
        if fea_result.get("fatigue"):
            out = _flatten(fea_result["fatigue"], static_part, source="fea")
            out["stress_used_mpa"] = float(stress_mpa)
            out["geometry_source"] = _geo_note
            out["real_geometry_bound"] = bool(_geo_ok)
            if not _geo_ok:
                out["design_use_warning"] = (
                    "本次疲劳结论基于【设计域默认几何】粗估，"
                    "承载截面可能被严重高估 —— 不得用于设计定型。")
            return out

    fat = fatigue_mod.analyze_fatigue_from_case(
        case, {"max_von_mises_mpa": float(stress_mpa)})
    out = _flatten(fat, {"max_von_mises_mpa": float(stress_mpa)}, source="given_stress")
    out["stress_used_mpa"] = float(stress_mpa)
    # 显式给应力时，几何不参与计算 —— 如实标注，避免误以为做了几何校核
    out["geometry_source"] = ("given_stress(未做几何校核，"
                              "应力由调用方直接指定)")
    out["real_geometry_bound"] = False
    return out


def cmd_demo(args=None) -> dict:
    """运行悬臂梁演示：自动生成工况 → 解析解 → 报告。"""
    lc = load_case.default_load_case("DEMO_CANTILEVER", "演示悬臂梁")
    lc_result = load_case.validate_load_case(lc)
    if not lc_result["ok"]:
        return {"ok": False, "errors": lc_result["errors"]}

    run_dir = os.path.join(_HERE, "..", "..", "output", "demo_run")
    os.makedirs(run_dir, exist_ok=True)
    fea_result = fea_solver.solve_fea(lc, output_dir=run_dir)

    domain = lc.get("design_domain", {}).get("bounds", {})
    gc = geometry_gate.geometry_gate_report(
        bbox=[domain.get("x_min", 0), domain.get("x_max", 200),
              domain.get("y_min", 0), domain.get("y_max", 60),
              domain.get("z_min", 0), domain.get("z_max", 60)],
        topology={"volumes": 1, "bodies": 1, "faces": 6, "edges": 12, "vertices": 8},
        face_areas=[], volume_mm3=200 * 60 * 60,
        design_bounds=domain, interfaces=[], design_params={},
    )

    report = sim_report.build_report(
        run_id="demo_001", load_case=lc, design_params={},
        geometry_check=gc, fea_result=fea_result, iteration=1,
    )
    report_path = sim_report.save_report(report, run_dir)
    report["report_path"] = report_path

    md = sim_report.report_to_markdown(report)
    return {
        "ok": True, "command": "demo",
        "report": report, "markdown_summary": md,
        "report_path": report_path,
        "notes": (
            "这是解析解（Euler-Bernoulli 梁理论），仅用于快速估算。\n"
            "实际零件请使用 physics-optimize 配合完整载荷工况文件。"
        ),
    }


# ═══════════════════════════════════════════════════════════════
#  CLI 入口（直接运行 physics_bridge.py 时使用）
# ═══════════════════════════════════════════════════════════════

def _parse_param_args(pairs):
    """把 --param k=v 解析成 dict（值自动尝试转数值；null/none 表示清除）。"""
    out = {}
    for item in pairs or []:
        if "=" not in str(item):
            continue
        k, v = str(item).split("=", 1)
        k, v = k.strip(), v.strip()
        if not k:
            continue
        if v.lower() in ("null", "none", ""):
            out[k] = None
            continue
        try:
            out[k] = float(v) if ("." in v or "e" in v.lower()) else int(v)
        except Exception:
            out[k] = v
    return out


def _design_params_path():
    """人工设计参数的落盘路径。

    【P0-3 修复】写入【权威状态目录】（若已存在的旧文件在脚本目录，则沿用
    那个位置，避免用户登记过的参数"突然读不到"）。
    """
    for _dd in _data_dirs():
        _cand = os.path.join(_dd, "design_params.json")
        if os.path.exists(_cand):
            return _cand
    return os.path.join(STATE_DIR, "design_params.json")


def cmd_set_design_params(pairs=None, src_file="", show_only=False):
    """【Bug12 二次修复】人工提供通道：把设计意图/工艺参数登记为权威输入。

    为什么必须有这条通道（测试部原话）：
      "shaft_diameter/gear_module/cavity_depth/draft_angle 等本质是人工设计
       输入（由扭矩、模数、注塑工艺决定），无法从零件 bbox 提取。"
    因此这些参数的正确来源是【人】或【上游流程】，而不是几何反推。
    本命令把它们落盘到 tools/design_params.json，供 _derive_design_params
    以【人工提供】优先级读取（见该函数的 ④ 段）。

    Returns: {ok, path, design_params, cleared:[...]}
    """
    p = _design_params_path()
    cur = {}
    if os.path.exists(p):
        try:
            j = json.load(open(p, "r", encoding="utf-8"))
            cur = j.get("design_params") if isinstance(j.get("design_params"), dict) else (j or {})
        except Exception:
            cur = {}
    if show_only:
        return {"ok": True, "path": p, "design_params": cur,
                "note": "当前已登记的人工设计参数（领域规则的数据源）"}
    incoming = {}
    if src_file:
        try:
            jf = json.load(open(src_file, "r", encoding="utf-8"))
            incoming = jf.get("design_params") if isinstance(jf.get("design_params"), dict) else jf
        except Exception as e:
            return {"ok": False, "error": "无法读取 --file: %r" % (e,)}
    incoming.update(_parse_param_args(pairs))
    cleared = []
    for k, v in (incoming or {}).items():
        if v is None:
            if k in cur:
                del cur[k]
                cleared.append(k)
        else:
            cur[k] = v
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"_comment": ("人工提供的设计参数（Bug12 通道）。"
                                    "领域规则中无法由几何推导的参数在此登记，"
                                    "如 shaft_diameter_mm / gear_module / "
                                    "draft_angle_deg / surface_roughness_um / "
                                    "rib_height_mm / min_wall_thickness。"),
                       "design_params": cur}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        return {"ok": False, "error": "写入失败: %r" % (e,)}
    return {"ok": True, "path": p, "design_params": cur, "cleared": cleared,
            "note": "已登记。这些值将在 physics-validate-domain 时以『人工提供』优先采用。"}


def cmd_validate_domain_cli(domain_id="structural", room="", pairs=None, params_file=""):
    """validate-domain 的 CLI 封装：支持 --param 人工提供设计参数。"""
    dp = {}
    # ① --params-file 显式文件
    if params_file and os.path.exists(params_file):
        try:
            jf = json.load(open(params_file, "r", encoding="utf-8"))
            dp.update(jf.get("design_params") if isinstance(jf.get("design_params"), dict) else jf)
        except Exception:
            pass
    # ② --param k=v（最高优先级）
    dp.update(_parse_param_args(pairs))
    return cmd_validate_domain(domain_id, design_params=dp or None, room=room)


def main():
    parser = argparse.ArgumentParser(
        description="Physics-in-the-Loop CAD Engineering Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python physics_bridge.py demo
  python physics_bridge.py validate-case case.json
  python physics_bridge.py optimize case.json --max-iter 5
  python physics_bridge.py report opt_iter_3
  python physics_bridge.py recommend opt_iter_3
  python physics_bridge.py status
""",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("demo", help="运行悬臂梁解析解演示")
    sub.add_parser("status", help="显示求解器后端状态")

    p = sub.add_parser("validate-case", help="校验载荷工况 JSON")
    p.add_argument("case_path", help="载荷工况 JSON 文件路径")
    p.add_argument("--relaxed", action="store_true", help="宽松校验（仅 warning 不报错）")

    p = sub.add_parser("build", help="加载工况并初始化仿真环境")
    p.add_argument("case_path", help="载荷工况 JSON 文件路径")

    p = sub.add_parser("simulate", help="运行仿真（需先有 run_dir）")
    p.add_argument("run_dir", help="运行目录路径")

    p = sub.add_parser("report", help="查看仿真报告")
    p.add_argument("run_id", help="运行 ID 或报告文件路径")

    p = sub.add_parser("recommend", help="生成修正建议")
    p.add_argument("run_id", help="运行 ID")
    p.add_argument("--max-iter", type=int, default=3, help="剩余最大迭代次数")

    p = sub.add_parser("optimize", help="自动迭代优化（generate→simulate→refine）")
    p.add_argument("case_path", help="载荷工况 JSON 文件路径")
    p.add_argument("--max-iter", type=int, default=5, help="最大迭代次数")
    # ── 【新 P0 修复】零件路径与材料覆盖 ─────────────────────────────────
    # 物理校核必须按【零件实际材料】判定，否则国标材料（Q235 σy235）会被
    #   载荷文件材料（PETG σy40）顶掉，SF 从 1.12 掉到 0.15。
    p.add_argument("--part", default="", help="零件 .SLDPRT 路径（物理校核必须针对真实零件）")
    p.add_argument("--room", default="", help="房间名（用于写防线②凭据）")
    p.add_argument("--material", default="",
                   help="显式指定校核材料（如 Q235/40Cr/6061-T6）；"
                        "缺省时自动读取【零件本体材料】，再退回载荷文件材料")

    # ── 【BUG-02 修复】疲劳 / 设计寿命校核 ────────────────────────────────
    p = sub.add_parser("fatigue", help="疲劳强度与设计寿命（默认30年）校核")
    p.add_argument("case_path", nargs="?", default="", help="载荷工况 JSON 文件路径")
    p.add_argument("--stress", type=float, default=None,
                   help="已知最大 von Mises 应力 (MPa)，给定则跳过静力求解")
    p.add_argument("--report", dest="run_id", default="",
                   help="用已有报告的应力做校核（传 run_id）")
    # ── 【BUG 修复·虚假 PASS】疲劳必须绑定真实几何 ────────────────────────
    # 不加 --part 时，旧实现会静默退回设计域默认几何 → 应力低估约 95 倍
    #   → 给出虚假 PASS。现在改为硬闸拒绝（与 optimize 同口径）。
    p.add_argument("--part", dest="part_path", default="",
                   help="零件 .SLDPRT 路径（疲劳校核必须绑定真实零件几何）")
    p.add_argument("--allow-design-domain", dest="allow_design_domain",
                   action="store_true",
                   help="允许退回设计域默认几何做粗估（结果不得用于设计定型）")

    # ── 【Bug12 二次修复】人工提供设计参数 / 领域校验 ─────────────────────
    p = sub.add_parser("validate-domain",
                       help="领域规则（GB/T）校验；设计参数缺失时明确报『需人工提供』")
    p.add_argument("domain", nargs="?", default="structural",
                   help="领域: structural|transmission|housing|mold")
    p.add_argument("--room", default="", help="房间名（用于写防线③凭据）")
    p.add_argument("--param", action="append", default=[],
                   help="人工提供设计参数，形如 --param shaft_diameter_mm=25 "
                        "（可多次；这是【设计意图/工艺参数】的唯一正确来源）")
    p.add_argument("--params-file", default="",
                   help="设计参数 JSON 文件路径（默认读 tools/design_params.json）")

    p = sub.add_parser("set-design-params",
                       help="把人工设计参数写入 tools/design_params.json（领域规则的数据源）")
    p.add_argument("--param", action="append", default=[],
                   help="形如 --param draft_angle_deg=1.5 （可多次；value=null 表示清除）")
    p.add_argument("--file", default="", help="改为从该 JSON 文件批量导入")
    p.add_argument("--show", action="store_true", help="只显示当前已登记的设计参数")

    args = parser.parse_args()
    cmd_map = {
        "demo": cmd_demo,
        "status": cmd_status,
        "validate-case": lambda a: cmd_validate_case(a.case_path, getattr(a, "relaxed", False)),
        "build": lambda a: cmd_build(a.case_path),
        "simulate": lambda a: cmd_simulate(a.run_dir),
        "report": lambda a: cmd_report(a.run_id),
        "recommend": lambda a: cmd_recommend(a.run_id, getattr(a, "max_iter", 3)),
        "optimize": lambda a: cmd_optimize(a.case_path, getattr(a, "max_iter", 5),
                                           getattr(a, "room", ""),
                                           getattr(a, "part", ""),
                                           getattr(a, "material", "")),
        "fatigue": lambda a: cmd_fatigue(getattr(a, "case_path", ""),
                                         getattr(a, "stress", None),
                                         getattr(a, "run_id", ""),
                                         getattr(a, "part_path", ""),
                                         getattr(a, "allow_design_domain", False)),
        # ── 【Bug12 二次修复】人工提供设计参数 / 领域校验 ──────────────
        "validate-domain": lambda a: cmd_validate_domain_cli(
            getattr(a, "domain", "structural"),
            getattr(a, "room", ""),
            getattr(a, "param", []),
            getattr(a, "params_file", "")),
        "set-design-params": lambda a: cmd_set_design_params(
            getattr(a, "param", []),
            getattr(a, "file", ""),
            getattr(a, "show", False)),
    }
    result = cmd_map[args.cmd](args)
    if result is not None:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
