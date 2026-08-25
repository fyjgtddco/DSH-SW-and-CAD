# -*- coding: utf-8 -*-
"""
material_db.py — 工程材料数据库（纯 Python，零外部依赖）
==========================================================
涵盖常用机械材料。所有单位：MPa（弹性模量/屈服强度）、kg/m³（密度）。
材料属性来自公开工程手册，实际设计请以供应商技术数据书为准。
"""
from typing import Any

# ── 铝合金 ──────────────────────────────────────────────
AL_ALLOYS = {
    "AL_1060": {"name": "Aluminum 1060 (annealed)",
                "E_mpa": 69000, "nu": 0.33, "yield_mpa": 35, "uts_mpa": 90, "density_kg_m3": 2700},
    "AL_6061": {"name": "Aluminum 6061 (T6)",
                "E_mpa": 68900, "nu": 0.33, "yield_mpa": 276, "uts_mpa": 310, "density_kg_m3": 2700},
    "AL_6061_T6": {"name": "Aluminum 6061-T6",
                   "E_mpa": 68900, "nu": 0.33, "yield_mpa": 276, "uts_mpa": 310, "density_kg_m3": 2700},
    "AL_7075": {"name": "Aluminum 7075-T6",
                "E_mpa": 72000, "nu": 0.33, "yield_mpa": 503, "uts_mpa": 572, "density_kg_m3": 2810},
    "AL_5052": {"name": "Aluminum 5052-H32",
                "E_mpa": 70000, "nu": 0.33, "yield_mpa": 193, "uts_mpa": 228, "density_kg_m3": 2680},
}

# ── 结构钢 ──────────────────────────────────────────────
STEEL_ALLOYS = {
    "ST_API_S235": {"name": "Steel S235JR (EN 10025)",
                    "E_mpa": 210000, "nu": 0.30, "yield_mpa": 235, "uts_mpa": 360, "density_kg_m3": 7850},
    "ST_API_A36": {"name": "Steel A36 (ASTM)",
                   "E_mpa": 200000, "nu": 0.30, "yield_mpa": 250, "uts_mpa": 400, "density_kg_m3": 7850},
    "ST_API_1020": {"name": "Steel AISI 1020 (cold drawn)",
                    "E_mpa": 205000, "nu": 0.29, "yield_mpa": 350, "uts_mpa": 420, "density_kg_m3": 7850},
    "ST_API_4140": {"name": "Steel AISI 4140 (quenched & tempered)",
                    "E_mpa": 205000, "nu": 0.29, "yield_mpa": 655, "uts_mpa": 760, "density_kg_m3": 7850},
    "ST_API_4340": {"name": "Steel AISI 4340 (quenched & tempered)",
                    "E_mpa": 200000, "nu": 0.29, "yield_mpa": 745, "uts_mpa": 860, "density_kg_m3": 7850},
    "ST_STAINLESS_304": {"name": "Stainless Steel 304",
                         "E_mpa": 193000, "nu": 0.30, "yield_mpa": 205, "uts_mpa": 515, "density_kg_m3": 8000},
    "ST_STAINLESS_316": {"name": "Stainless Steel 316",
                         "E_mpa": 193000, "nu": 0.30, "yield_mpa": 170, "uts_mpa": 515, "density_kg_m3": 8000},
}

# ── 钛合金 ──────────────────────────────────────────────
TI_ALLOYS = {
    "TI_GRADE1": {"name": "Titanium Grade 1 (commercially pure)",
                  "E_mpa": 105000, "nu": 0.34, "yield_mpa": 170, "uts_mpa": 240, "density_kg_m3": 4500},
    "TI_GRADE5": {"name": "Titanium Ti-6Al-4V (Grade 5)",
                  "E_mpa": 114000, "nu": 0.34, "yield_mpa": 880, "uts_mpa": 950, "density_kg_m3": 4430},
}

# ── 工程塑料 ────────────────────────────────────────────
PLASTICS = {
    "PC_POLYCARBONATE": {"name": "Polycarbonate (PC)",
                         "E_mpa": 2400, "nu": 0.37, "yield_mpa": 65, "uts_mpa": 70, "density_kg_m3": 1200},
    "NYLON_6_6": {"name": "Nylon 6/6 (dry)",
                  "E_mpa": 2800, "nu": 0.39, "yield_mpa": 80, "uts_mpa": 85, "density_kg_m3": 1140},
    "ABS": {"name": "ABS Plastic",
            "E_mpa": 2400, "nu": 0.40, "yield_mpa": 45, "uts_mpa": 50, "density_kg_m3": 1050},
}

# ── 合并字典 ────────────────────────────────────────────
ALL_MATERIALS: dict[str, dict[str, Any]] = {}
ALL_MATERIALS.update(AL_ALLOYS)
ALL_MATERIALS.update(STEEL_ALLOYS)
ALL_MATERIALS.update(TI_ALLOYS)
ALL_MATERIALS.update(PLASTICS)


def get_material(material_id: str) -> dict | None:
    """通过 ID 获取材料属性；ID 不区分大小写。"""
    key = material_id.upper().strip()
    return ALL_MATERIALS.get(key)


def list_materials(category: str = "") -> list[dict]:
    """列出材料；可按类别过滤：'al' / 'steel' / 'ti' / 'plastic'。"""
    cats = {"al": AL_ALLOYS, "steel": STEEL_ALLOYS, "ti": TI_ALLOYS, "plastic": PLASTICS}
    if category:
        c = cats.get(category.lower(), {})
        return [{"id": k, **v} for k, v in c.items()]
    return [{"id": k, **v} for k, v in ALL_MATERIALS.items()]


def material_to_json(mat: dict) -> dict:
    """将材料字典转为标准 JSON 格式（供 load_case 使用）。"""
    return {
        "id": mat.get("id", ""),
        "name": mat.get("name", ""),
        "youngs_modulus_mpa": mat["E_mpa"],
        "poissons_ratio": mat["nu"],
        "yield_strength_mpa": mat["yield_mpa"],
        "uts_mpa": mat.get("uts_mpa", 0),
        "density_kg_m3": mat["density_kg_m3"],
        "source": "material_db",
    }


def resolve_material(load_case: dict) -> dict:
    """从 load_case 解析材料，缺失时尝试从 id 匹配，仍缺则返回错误。"""
    mat = load_case.get("material", {})
    if mat and mat.get("youngs_modulus_mpa"):
        return {"ok": True, "material": mat}
    mat_id = load_case.get("material", {}).get("id", "")
    if mat_id:
        found = get_material(mat_id)
        if found:
            return {"ok": True, "material": material_to_json({"id": mat_id, **found})}
    return {"ok": False, "error": f"material not found: {mat_id!r}; check available IDs via material_db.list_materials()"}
