# -*- coding: utf-8 -*-
"""生成【承重支架】载荷工况 JSON（材料值取自 material_db，只读不改数据）。

用法: python gen_case.py <牌号> <输出.json> [problem_id]
"""
import io, json, os, sys

TOOLS = r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools"
sys.path.insert(0, TOOLS)
sys.path.insert(0, os.path.join(TOOLS, "physics"))
import material_db as mdb


def build(grade, problem_id):
    m = mdb.get_material(grade)
    if not m:
        raise SystemExit("牌号未收录: %s" % grade)
    ready, _e, why = mdb.gb_material_ready(grade)
    mat = {
        "id": grade,
        "name": m.get("name"),
        "youngs_modulus_mpa": m.get("E_mpa"),
        "poissons_ratio": m.get("nu"),
        "yield_strength_mpa": m.get("yield_mpa"),
        "uts_mpa": m.get("uts_mpa"),
        "density_kg_m3": m.get("density_kg_m3"),
        "standard": m.get("standard"),
        # 来源标注（如实透传，供报告审计）
        "values_source": m.get("values_source"),
        "values_from_gb": m.get("values_from_gb"),
        "values_pending": m.get("values_pending"),
        "equivalent_name": m.get("equivalent_name"),
        "gb_source": m.get("gb_source") or "《国标材料官方标准汇总》+ 项目原有牌号数据",
        "identity_source": "GB（标准身份已确认）",
        "source": "material_db.get_material(%s)" % grade,
    }
    case = {
        "schema_version": "1.0",
        "meta": {
            "problem_id": problem_id,
            "title": "承重支架（L 型，GB %s）" % grade,
            "revision": "A",
            "generated_by": "E2E 验证子代理（真实设计流程）",
            "task_type": "structural",
            "design_domain_mm": [140.0, 90.0, 80.0],
            "design_domain_note": "零件包络：底座长 140 × 立臂高 90 × 宽 80 mm",
        },
        "units": {"length": "mm", "force": "N", "stress": "MPa", "mass": "kg"},
        "design_domain": {
            "bounds": {"x_min": 0.0, "x_max": 140.0, "y_min": 0.0, "y_max": 90.0,
                       "z_min": -40.0, "z_max": 40.0},
            "keep_in_regions": [], "keep_out_regions": [],
        },
        "material": mat,
        "spatial_selectors": [
            {"id": "fixed_end", "type": "box",
             "bounds": {"x_min": 100.0, "x_max": 150.0, "y_min": -5.0, "y_max": 20.0,
                        "z_min": -45.0, "z_max": 45.0},
             "selection_rule": "faces_intersecting_region"},
            {"id": "load_face", "type": "box",
             "bounds": {"x_min": -5.0, "x_max": 20.0, "y_min": 80.0, "y_max": 95.0,
                        "z_min": -45.0, "z_max": 45.0},
             "selection_rule": "faces_intersecting_region"},
        ],
        "boundary_conditions": [
            {"id": "bc_fixed", "spatial_selector_id": "fixed_end",
             "type": "fixed_displacement",
             "dof_lock": {"x": True, "y": True, "z": True,
                          "rx": True, "ry": True, "rz": True}},
        ],
        "loads": [
            {"id": "load_rated", "spatial_selector_id": "load_face",
             "type": "distributed_force", "magnitude_n": 3000.0,
             "direction": [0.0, 0.0, -1.0], "case_name": "额定垂向工况",
             "note": "立臂顶端垂向额定载荷 3000 N"},
            {"id": "load_lateral", "spatial_selector_id": "load_face",
             "type": "distributed_force", "magnitude_n": 1500.0,
             "direction": [1.0, 0.0, 0.0], "case_name": "侧向工况",
             "note": "侧向力 = 额定 × 0.5"},
        ],
        "load_cases": [
            {"id": "rated", "load_ids": ["load_rated"], "description": "额定垂向工况（主工况）"},
            {"id": "lateral", "load_ids": ["load_lateral"], "description": "侧向工况"},
            {"id": "combined", "load_ids": ["load_rated", "load_lateral"],
             "description": "组合工况（最恶劣）"},
        ],
        "acceptance": {
            "analysis_type": "linear_static",
            "min_safety_factor": 2.0,
            "target_safety_factor_max": 4.0,
            "anisotropy_factor": 1.0,
            "process": "机加工",
            "max_displacement_mm": 1.0,
            "min_wall_thickness_mm": 6.0,
            "max_mass_kg": None,
            "must_remain_in_design_domain": True,
            "mesh_quality_min": 0.1,
            "design_life_years": 5.0,
            "operating_cycles_per_year": 100000.0,
            "load_type": "impact",
            "impact_factor": 1.5,
            "fatigue_check_required": True,
            "required_load_cases": ["rated", "lateral"],
        },
        "optimization": {"primary": "minimize_mass",
                         "secondary": ["minimize_max_displacement"]},
    }
    return case, ready, why


if __name__ == "__main__":
    grade = sys.argv[1]
    out = sys.argv[2]
    pid = sys.argv[3] if len(sys.argv) > 3 else ("E2E_" + grade.replace("#", "").replace("-", "_"))
    case, ready, why = build(grade, pid)
    io.open(out, "w", encoding="utf-8").write(
        json.dumps(case, ensure_ascii=False, indent=2))
    print("已写出:", out)
    print("  material.ready =", ready, "| reason =", why)
    print("  σy=%s σb=%s E=%s ν=%s ρ=%s"
          % (case["material"]["yield_strength_mpa"], case["material"]["uts_mpa"],
             case["material"]["youngs_modulus_mpa"],
             case["material"]["poissons_ratio"],
             case["material"]["density_kg_m3"]))
    print("  values_source =", case["material"]["values_source"])
