# -*- coding: utf-8 -*-
"""
physics/ — Physics-in-the-Loop 物理验证子系统
==============================================
从 IJCAI 论文 "Physics-in-the-Loop: A Hybrid Agentic Architecture for
Validated CAD Engineering Design" 中提取的工程化实现。

核心能力：
  - 载荷工况 JSON 解析与校验（load_case.py）
  - 材料数据库与覆盖策略（material_db.py）
  - 几何门禁检查（geometry_gate.py）
  - STEP 导出 + 网格数据生成（mesh_adapter.py）
  - 解析/数值 FEA 求解（fea_solver.py）
  - 结构化仿真报告（simulation_report.py）
  - 迭代状态管理（design_state.py）
  - 自动修正规则引擎（refine_rules.py）

分层策略（按可用工具自动降级）：
  Level 0  解析解：悬臂梁 / 简支梁 / 轴受载 / 薄板近似
  Level 1  杆系求解器：2D truss 框架
  Level 2  Gmsh + CalculiX / Code_Aster 外部求解器
  Level 3  SolidWorks Simulation API（若有授权）

命令入口：
  python sw_bridge.py physics-validate-case <load_case.json>
  python sw_bridge.py physics-build <part.sldprt> <load_case.json> [params.json]
  python sw_bridge.py physics-simulate <part.sldprt> <load_case.json>
  python sw_bridge.py physics-report <run_id>
  python sw_bridge.py physics-recommend <report.json>
  python sw_bridge.py physics-refine <load_case.json> [max_iter=N]
  python sw_bridge.py physics-status
"""
import os
import sys

# 让本目录可被 import（无论调用方工作目录）
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

__all__ = [
    "load_case",
    "material_db",
    "geometry_gate",
    "mesh_adapter",
    "fea_solver",
    "simulation_report",
    "design_state",
    "refine_rules",
]
