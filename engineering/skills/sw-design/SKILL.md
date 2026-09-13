---
name: sw-design
description: SolidWorks 机械设计工作流。涵盖三维建模、装配设计、工程图输出、GB/T 制图规范、AutoCAD 几何验证（CADX）和 SolidWorks 桥接。
whenToUse: 当需要进行 SolidWorks 三维建模、装配设计、出工程图或将 SolidWorks 零件转换为 AutoCAD 图纸时使用
disable-model-invocation: false
user-invocable: true
source: engineering
provider: filesystem
---

## SolidWorks 机械设计工作流

### 一、设计前分析

1. **需求分析**
   - 明确零件的功能要求、材料、热处理、表面处理
   - 确定载荷类型和大小，评估应力和疲劳风险
   - 确定工作环境条件（温度、湿度、腐蚀性）

2. **尺寸合理性检查**
   - 壁厚是否均匀？是否有应力集中风险？
   - 加工工艺是否可行？脱模斜度是否合适？
   - 运动部件是否有足够间隙？配合公差是否合理？
   - 是否考虑热膨胀？

### 二、零件建模流程（solidworks-modeling.md 参考）

1. **草图绘制**
   - 选择合适的基准面（前视/上视/右视）
   - 使用几何约束（水平、垂直、共线、同心、相切等）
   - 使用尺寸约束标注尺寸
   - 确保草图完全定义（黑色，非蓝色）

2. **特征建模**
   - 拉伸凸台/基体、旋转凸台/基体
   - 拉伸切除、旋转切除
   - 扫描、放样
   - 添加圆角、倒角
   - 异形孔向导（标准孔、螺纹孔）
   - 镜像、阵列（圆周/线性）

   **⚠️ Bug-20 注意 — 多基准面切换后草图定位偏移：**
   - 肋板/三角加强筋等特征，优先在 **Front Plane**（前视基准面）上绘制轮廓再拉伸，
     而非在 Top Plane 上定位后再拉伸。Top Plane 上的坐标在多步操作中容易漂移。
   - 如果必须在非基准面上开草图，使用 `begin_sketch_on_face(x, y, z)` 明确指定坐标，
     并确保该面与目标特征位置对齐。
   - 复杂装配体（如 L 形支架），建议全部特征在同一基准面完成后再做装配体。

3. **零件验证**
   - 质量属性检查（质量、体积、重心）
   - 干涉检查
   - 简单应力分析（Simulation Xpress）

### 三、装配体设计

1. **装配顺序规划**
   - 确定基准零件
   - 规划装配路径
   - 考虑装配工具操作空间

2. **配合定义**
   - 标准配合：重合、平行、垂直、相切、同轴心、距离、角度
   - 高级配合：对称、宽度、路径配合、线性耦合
   - 机械配合：凸轮、齿轮、齿条小齿轮、螺旋、万向节

3. **装配体验证**
   - 干涉检查
   - 碰撞检查
   - 动态间隙检查
   - 爆炸视图生成

### 四、工程图输出

1. **视图选择**
   - 标准三视图（前视、上视、右视）+ 等轴测
   - 投影视图、剖视图（全剖、半剖、局部剖、阶梯剖）
   - 局部放大图、辅助视图

2. **标注规范（参考 references/gbt-drafting.md）**
   - 尺寸标注（GB/T 4458.4）
   - 公差标注（GB/T 1800 系列）
   - 表面粗糙度（GB/T 131）
   - 形位公差（GB/T 1182）
   - 标题栏（GB/T 10609.1）

3. **图层规范（参考 references/autocad-workflow.md）**
   - `OUTLINE` / `THIN` / `CENTER` / `HIDDEN` / `DIM` / `TEXT` / `HATCH`

### 五、AutoCAD 几何验证（CADX）

> 参考：`references/autocad-workflow.md`

从 SolidWorks 生成的工程图导出为 DWG/DXF 后，可使用 CADX 进行几何验证：

```powershell
# 导出 DXF（SolidWorks 工程图 → DXF）
python "<工程模式根目录>\tools\sw_bridge.py" export-dxf "<输出.dxf>"

# 运行 CADX 几何验证（7 项检查）
python "<工程模式根目录>\tools\sw_bridge.py" cad-validate "<输出.dxf>"
```

**验证流程：**
1. 读取 DXF 中所有实体（LWPOLYLINE、LINE、CIRCLE、DIMENSION 等）
2. 逐项执行 7 种检查，记录问题位置和严重等级
3. 返回 JSON 摘要：`{status: "PASSED/REVIEW/FAILED", critical, warning, info, issues: [...]}`
4. CRITICAL 项必须在发布前修复

### 六、参考文档

- `references/gbt-drafting.md` — GB/T 机械制图国家标准（线型、标注、视图、剖视）
- `references/autocad-workflow.md` — AutoCAD 工作流、CADX 验证命令、坐标约定
