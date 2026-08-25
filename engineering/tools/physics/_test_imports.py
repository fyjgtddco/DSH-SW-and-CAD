# -*- coding: utf-8 -*-
"""
物理验证子系统模块列表
========================
（同 physics\_test_imports.py，保留兼容）
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "physics"))

modules = [
    "load_case", "material_db", "geometry_gate", "mesh_adapter",
    "fea_solver", "simulation_report", "design_state", "refine_rules",
]
print("=== physics 模块导入测试 ===")
for m in modules:
    try:
        __import__(m)
        print(f"  [OK] {m}")
    except Exception as e:
        print(f"  [FAIL] {m}: {e}")
print("=== 完成 ===")
