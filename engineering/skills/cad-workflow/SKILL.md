---
name: cad-workflow
description: AutoCAD 机械设计工作流。支持 SolidWorks 三维建模、AutoCAD 二维图纸设计、DXF 几何验证（CADX）、GB/T 机械制图规范、工程图导出（DWG/PDF）。
whenToUse: 当需要进行 AutoCAD/SolidWorks 机械设计、生成工程图、进行几何验证或需要将 SolidWorks 零件输出为 DWG/DXF 时使用
disable-model-invocation: false
user-invocable: true
source: engineering
provider: filesystem
---

## AutoCAD / SolidWorks 机械设计工作流

### 一、设计前分析

1. **需求分析**
   - 明确零件的功能要求、材料、表面处理
   - 确定载荷类型和大小，评估加工工艺可行性
   - 区分 SolidWorks（3D）和 AutoCAD（2D）的适用场景

2. **尺寸合理性检查**
   - 壁厚是否均匀？是否有应力集中风险？
   - 是否有标准件可替代？脱模斜度是否合适？
   - 运动部件是否有足够间隙？配合公差是否合理？

3. **几何验证（CADX）**
   - 生成 DWG/DXF 后，运行 `cad-validate` 进行 7 项几何检查
   - 关注 UNCLOSED_POLYLINE / OVERLAPPING_GEOMETRY / DIMENSION_MISMATCH 等 CRITICAL 项
   - 验证通过后再进入后续流程

### 二、SolidWorks 三维建模

1. **草图绘制**
   - 选择合适的基准面（前视/上视/右视）
   - 使用几何约束（水平、垂直、共线、同心等）
   - 确保草图完全定义（黑色，非蓝色）

2. **特征建模**
   - 拉伸凸台/基体、旋转凸台/基体
   - 拉伸切除、旋转切除
   - 扫描、放样
   - 添加圆角、倒角、异形孔向导
   - 镜像、阵列（圆周/线性）

3. **零件验证**
   - 质量属性检查（质量、体积、重心）
   - 干涉检查
   - 简单应力分析（Simulation Xpress）

### 三、AutoCAD 二维图纸设计

1. **视图选择**
   - 标准三视图（前视、上视、右视）
   - 投影视图、剖视图（全剖、半剖、局部剖）
   - 局部放大图、辅助视图

2. **标注规范（GB/T）**
   - 尺寸标注：粗实线轮廓 + 细实线尺寸线
   - 公差标注：按 GB/T 1800 系列
   - 表面粗糙度：按 GB/T 131
   - 形位公差：按 GB/T 1182

3. **图层管理**
   - `OUTLINE`：可见轮廓（粗实线）
   - `THIN`：尺寸线、剖面线（细实线）
   - `CENTER`：轴线、对称中心线（细点画线）
   - `HIDDEN`：不可见轮廓（细虚线）
   - `DIM` / `TEXT` / `HATCH`

### 四、工程图纸输出

#### 方式 A：SolidWorks 生成工程图（推荐）
```powershell
# 从 SolidWorks 零件生成 SLDDRW（含三视图+等轴测）
python "<工程模式根目录>\tools\sw_bridge.py" drawing "<零件路径>"

# 导出为 DWG（AutoCAD 可读）
python "<工程模式根目录>\tools\sw_bridge.py" dwg "<零件路径>"

# 导出为 PDF
python "<工程模式根目录>\tools\sw_bridge.py" export-pdf "<输出.pdf>"
```

#### 方式 B：AutoCAD 直接绘制（CADX 能力）
```powershell
# 启动 AutoCAD 并导出 DXF
python "<工程模式根目录>\tools\sw_bridge.py" ac-export "<输出.dxf>"

# 运行几何验证（7 项检查）
python "<工程模式根目录>\tools\sw_bridge.py" cad-validate "<图纸.dxf>"
```

### 五、CADX 几何验证命令

每次 AutoCAD 图纸修改后必须运行验证：

```powershell
# 验证已保存的 DXF/DWG（需先导出为 DXF）
python "<工程模式根目录>\tools\sw_bridge.py" cad-validate <图纸.dxf> [规则文件]

# 验证当前 AutoCAD 活动文档（自动导出 DXF）
python "<工程模式根目录>\tools\sw_bridge.py" cad-validate-live
```

**验证结果解读：**
- `CRITICAL`（红色）：必须修复，否则图纸不可发布
- `WARNING`（黄色）：建议修复，影响可读性
- `INFO`（绿色）：参考建议，不影响功能

**验证覆盖的 7 项检查：**
1. `UNCLOSED_POLYLINE` — 闭合多段线未闭合
2. `OVERLAPPING_GEOMETRY` — 实体重叠/交叉
3. `ROOM_TOO_SMALL` — 尺寸低于最低标准
4. `ROOM_ASPECT_RATIO` — 长宽比异常
5. `WALL_TOO_THIN` — 壁厚低于最小值
6. `MISSING_ELEMENTS` — 缺少门/窗等元素
7. `DIMENSION_MISMATCH` — 标注与实际不符

### 六、调用模板

```powershell
# SolidWorks：新建零件 → 建模 → 出图 → 验证
python "<工程模式根目录>\tools\sw_bridge.py" run my_script.py           # 建模脚本
python "<工程模式根目录>\tools\sw_bridge.py" drawing part.sldprt         # 生成工程图
python "<工程模式根目录>\tools\sw_bridge.py" dwg part.sldprt             # 导出 DWG
python "<工程模式根目录>\tools\sw_bridge.py" cad-validate part.dxf       # 几何验证
python "<工程模式根目录>\tools\sw_bridge.py" show                         # 查看结果

# AutoCAD：连接 → 绘制 → 验证
python "<工程模式根目录>\tools\sw_bridge.py" ac-status                   # 检查 AutoCAD 连接
python "<工程模式根目录>\tools\sw_bridge.py" ac-export output.dxf        # 导出 DXF
python "<工程模式根目录>\tools\sw_bridge.py" cad-validate output.dxf     # 验证几何
```
