---
name: physics-in-loop
description: >-
  物理仿真驱动的结构设计闭环（Physics-in-the-Loop）。
  从载荷工况 JSON 出发，自动进行参数化建模、有限元验证、迭代优化，
  直到满足安全系数、位移、体积等约束。
  触发关键词：力学验证、强度校核、安全系数、FEA、应力分析、拓扑优化、
  载荷工况、静力分析、迭代设计、结构验证。
whenToUse: >-
  当用户要求对零件进行力学/结构验证、指定了力/压力/约束条件、
  或询问"这个零件在载荷下会不会坏"时使用。
  如果用户未提供材料、约束或载荷，必须先追问再开始。
disable-model-invocation: false
user-invocable: true
source: engineering
provider: filesystem
---

# Physics-in-the-Loop 物理验证工作流

> 基于 IJCAI 2026 论文 "Physics-in-the-Loop: A Hybrid Agentic Architecture
> for Validated CAD Engineering Design" 的工程化实现。

## 核心思想

LLM 擅长生成设计方案，但不擅长计算物理真实性。
本系统把**确定性物理工具**（解析解/FEA）嵌入设计决策循环：
`LLM 生成 → 工具验证 → 反馈修正 → 重新生成`，直到满足安全门槛。

```
用户工程需求
    ↓
结构化载荷工况 JSON（load_case）
    ↓
[Generate] LLM 提出设计参数（厚度、圆角、筋板…）
    ↓
[Simulate] 解析解 / Gmsh+CalculiX / SolidWorks Simulation
    ↓
[Refine]   若 SAFETY_FACTOR ∉ [min, max] → 生成修正建议
    ↓
循环直到 PASS 或达到 max_iterations
    ↓
生成 release_manifest（含局限性声明 + 人工复核要求）
```

## 支持的求解等级（自动降级）

| 等级 | 方法 | 依赖 | 适用场景 |
|------|------|------|---------|
| L0 | 解析解（悬臂梁/简支梁/扭转轴） | 无 | 快速初判、回归测试 |
| L1 | 杆系 truss 求解 | 无（预留接口） | 简单框架结构 |
| L2 | Gmsh + CalculiX | gmsh + ccx | 一般 3D 零件 |
| L3 | SolidWorks Simulation API | SW Simulation 授权 | 生产级验证 |

第一版默认使用 L0，检测到 Gmsh/CalculiX 后自动升级。

## 命令速查

```powershell
# 1. 先运行演示，理解工作流
python sw_bridge.py physics-demo

# 2. 检查可用求解器
python sw_bridge.py physics-status

# 3. 校验你的载荷工况文件
python sw_bridge.py physics-validate-case my_load_case.json

# 4. 自动迭代优化（最多 5 轮）
python sw_bridge.py physics-optimize my_load_case.json --max-iter 5

# 5. 查看某轮结果
python sw_bridge.py physics-report opt_iter_3

# 6. 获取下轮修正建议
python sw_bridge.py physics-recommend opt_iter_3
```

## 载荷工况文件格式（load_case.json）

```json
{
  "schema_version": "1.0",
  "meta": {
    "problem_id": "L_BRACKET_001",
    "title": "L 型安装支架",
    "revision": "A"
  },
  "units": { "length": "mm", "force": "N", "stress": "MPa", "mass": "kg" },

  "design_domain": {
    "bounds": {
      "x_min": 0, "x_max": 120,
      "y_min": 0, "y_max": 80,
      "z_min": 0, "z_max": 60
    }
  },

  "material": {
    "id": "AL_6061_T6",
    "youngs_modulus_mpa": 68900,
    "poissons_ratio": 0.33,
    "yield_strength_mpa": 276,
    "density_kg_m3": 2700
  },

  "spatial_selectors": [
    {
      "id": "fixed_end",
      "type": "box",
      "bounds": { "x_min": 0, "x_max": 10, "y_min": 0, "y_max": 80, "z_min": 0, "z_max": 60 },
      "selection_rule": "faces_intersecting_region"
    },
    {
      "id": "load_face",
      "type": "box",
      "bounds": { "x_min": 110, "x_max": 120, "y_min": 20, "y_max": 60, "z_min": 10, "z_max": 50 },
      "selection_rule": "faces_intersecting_region"
    }
  ],

  "boundary_conditions": [
    {
      "id": "bc_fixed",
      "spatial_selector_id": "fixed_end",
      "type": "fixed_displacement",
      "dof_lock": { "x": true, "y": true, "z": true, "rx": true, "ry": true, "rz": true }
    }
  ],

  "loads": [
    {
      "id": "payload",
      "spatial_selector_id": "load_face",
      "type": "distributed_force",
      "magnitude_n": 1500,
      "direction": [0, 0, -1]
    }
  ],

  "acceptance": {
    "analysis_type": "linear_static",
    "min_safety_factor": 2.0,
    "target_safety_factor_max": 5.0,
    "max_displacement_mm": 0.5,
    "min_wall_thickness_mm": 3.0
  },

  "optimization": {
    "primary": "minimize_mass",
    "secondary": ["minimize_max_displacement"]
  }
}
```

## 设计参数格式（design_parameters.json）

```json
{
  "thickness_mm": {
    "id": "thickness_mm",
    "value": 5.0,
    "min": 2.0,
    "max": 15.0,
    "step": 0.5
  },
  "height_mm": {
    "id": "height_mm",
    "value": 60.0,
    "min": 30.0,
    "max": 150.0,
    "step": 5.0
  },
  "fillet_mm": {
    "id": "fillet_mm",
    "value": 2.0,
    "min": 0.5,
    "max": 10.0,
    "step": 0.5
  },
  "mount_hole_diameter_mm": {
    "id": "mount_hole_diameter_mm",
    "value": 10.0,
    "locked": true,
    "source": "interface_requirement"
  }
}
```

**注意**：`locked: true` 的参数不会被自动优化修改，用于保护接口尺寸。

## Agent 必须遵守的流程规则

1. **载荷工况缺失时不得开始仿真**
   - 缺少材料屈服强度 → 必须询问用户或标 TBD
   - 缺少固定约束位置 → 必须询问
   - 缺少载荷大小/方向 → 必须询问

2. **仿真通过后必须给出 release_manifest**
   ```json
   {
     "status": "CANDIDATE_AFTER_HUMAN_REVIEW",
     "passed_gates": ["CAD_BUILD", "DESIGN_SPACE", "MESH_QUALITY", "STATIC_FEA", "SAFETY_FACTOR"],
     "not_evaluated": ["fatigue", "buckling", "contact_non_linearity"],
     "human_review_required": true
   }
   ```

3. **禁止的行为**
   - 不得在无载荷数据时声称"结构安全"
   - 不得修改 locked 参数
   - 不得无限迭代（默认上限 5 次，最多 10 次）
   - 不得将"解析解通过"等同于"FEA 通过"——两者结论应一致标注

4. **何时停止迭代**
   - `overall_status == "PASS"` → 通过
   - 达到 `max_iterations` → REVIEW_REQUIRED
   - 连续 2 轮无改善 → STALLED
   - 网格化/求解连续失败 → UNSIMULATABLE
   - 工况/材料/约束缺失 → BLOCKED_BY_INPUT

## 可用材料速查

```
铝合金：AL_6061_T6 (σy=276MPa), AL_7075 (σy=503MPa), AL_5052 (σy=193MPa)
结构钢：ST_API_S235 (σy=235MPa), ST_API_A36 (σy=250MPa), ST_API_4140 (σy=655MPa)
不锈钢：ST_STAINLESS_304 (σy=205MPa), ST_STAINLESS_316 (σy=170MPa)
钛合金：TI_GRADE5 (σy=880MPa)
塑料：PC_POLYCARBONATE (σy=65MPa), ABS (σy=45MPa)
```

完整列表：`material_db.list_materials()`

## 参考文档

- `references/gbt-drafting.md` — GB/T 机械制图国家标准
- `references/autocad-workflow.md` — AutoCAD 工作流与 CADX 验证
- `../tools/physics/` — 完整源码（load_case / fea_solver / refine_rules 等）
