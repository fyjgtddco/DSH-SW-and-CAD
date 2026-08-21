---
name: solidworks-bridge
description: >-
  SolidWorks 自动化建模桥接工具（DSH_SW 通用版）。
  通过 Python win32com 驱动 SolidWorks，支持 SW 2018~2024 任意版本。
  单位：毫米（mm），默认前缀：DSH_。
whenToUse: >-
  当用户要求用 SolidWorks 建模、画图、生成 3D 零件、输出 .sldprt 文件时使用。
source: engineering
provider: filesystem
---

# SolidWorks 建模桥接（DSH_SW 通用版）

> **本文件是通用版**：不写死任何电脑路径或 SolidWorks 版本号。
> 复制到其他电脑后，把 `<包目录>` 替换成实际路径即可。

## 0. 核心架构

```
DSH (pwsh 工具)
  │  python <包目录>\sw_bridge.py run <脚本.py>
  ▼
sw_bridge.py (win32com 执行器)
  │  win32com.client.dynamic.Dispatch('SldWorks.Application')
  ▼
SolidWorks（任意版本，自动探测模板）
```

## 1. 单位约定

**所有尺寸单位默认毫米（mm）**，用户没说单位就是 mm。
脚本中直接写 `m.rect(0, 0, 120, 80)` 就是 120mm×80mm。

## 2. 保存命名约定

**保存文件名按用户提供的模型名称命名**：
- 前缀统一 `DSH_` + 用户提供的名称 + `.sldprt`
- 用户说"建模一个圆球"，就保存为 `DSH_圆球.sldprt`
- 用户给了明确文件名则优先用用户指定的名字

## 3. 完整 API 速查

```python
import swapi

# ── 文档操作 ───────────────────────────────────────
m = swapi.new_part()          # 新建零件（模板自动探测）
m = swapi.from_active()       # 包装当前活动文档
m.save(path)                  # 另存为（返回 {"ok": True/False, "path": ...}）
m.massprops()                 # 质量属性（体积/重量/重心）
m.close()                     # 关闭文档
m.export_pdf(path)            # 导出 PDF

# ── 展示 / 可视化 ─────────────────────────────────
m.bring_to_front()            # 窗口置前 + 最大化
m.set_view_iso()              # 等轴测视角 + 居中 + 缩放 1.2x
m.zoom_to_fit()               # 缩放适应窗口
m.screenshot(path)            # 窗口截图 PNG
m.export_image(path)          # 导出位图
m.rebuild()                   # 重建模型

# ── 基准面 / 草图 ────────────────────────────────
m.begin_sketch("Front Plane")        # 在基准面开始草图（自动正视+居中）
m.begin_sketch_on_face(x, y, z)      # 在实体表面开草图（坐标 mm）
m.end_sketch()                       # 结束草图（自动 MergePoints 闭合端点）

# ── 草图图元（坐标单位 mm）───────────────────────
m.rect(cx, cy, w, h)                # 中心矩形
m.circle(cx, cy, r)                 # 圆
m.line(x1, y1, x2, y2)              # 直线
m.polyline([(x1,y1), (x2,y2), ...]) # 折线
m.centerline(x1, y1, x2, y2)        # 中心线（旋转轴）

# ── 特征操作（尺寸单位 mm）──────────────────────
m.extrude(depth, symmetric=False)   # 拉伸凸台
m.cut(depth=10, through=True)       # 切除 / 完全贯穿
m.revolve(angle_deg=360, cut=False) # 旋转（需先画中心线）
m.fillet(radius, edge_points)       # 圆角：边上的采样点坐标
m.chamfer(width, edge_points, 45)   # 倒角
```

## 4. 调用方式（DSH AI 使用）

AI 通过 DSH 的 `pwsh` 工具执行：

```powershell
# 检查 SolidWorks 连接状态
python "<包目录>\sw_bridge.py" status

# 执行建模脚本
python "<包目录>\sw_bridge.py" run "<脚本路径>"

# 展示成品
python "<包目录>\sw_bridge.py" show

# 生成工程图（强制中文视图名 *前视/*上视/*右视/*等轴测）
python "<包目录>\sw_bridge.py" drawing "零件路径" [输出路径]

# 导出 DWG
python "<包目录>\sw_bridge.py" dwg "零件路径" [输出路径]

# 清理临时文件
python "<包目录>\sw_bridge.py" cleanup "目录路径"

# Vision 不可用时截图供前端识图
python "<包目录>\sw_bridge.py" vision-fallback "描述"
```

## 5. 已踩坑与解决方案

### 坑 1：草图吸附导致坐标不精确
- **现象**：输入 17.5mm 被吸附到 18mm
- **解决**：swapi 连接时自动禁用推理/吸附（`_disable_snapping`）

### 坑 2：对称拉伸枚举随版本变化
- **现象**：2022 用枚举 6，2018 用枚举 5
- **解决**：`swapi._get_midplane_enum(sw)` 自动适配

### 坑 3：圆角必须指定 Options=2
- **现象**：调用 FeatureFillet3 但不传 Options 参数会静默失败
- **解决**：`FeatureFillet3(2, R1_m, 0, 0, 0, 0, 0, None×7)`

### 坑 4：键槽 API 语义
- **现象**：CreateSketchSlot 的 CenterCenter 模式在 pywin32 下生成错误几何
- **解决**：只用 FullLength 模式，X2 传右圆弧圆心而非最外端点

### 坑 5：中文草图名跨语言不可靠
- **现象**：英文版 SW 草图叫 "Sketch2"，中文版叫 "草图2"
- **解决**：用 `swapi.select_sketch_by_index(sw, model, 2)` 按序号选

### 坑 6：欢迎页 vs 主窗口混淆
- **现象**：SolidWorks 2022 有两个大窗口，主窗口标题含版本号
- **解决**：`swapi._find_sw_windows()` 排除纯 "SOLIDWORKS" 标题

## 5.5 关键 Bug 修复（2025-08-21）

### Bug 6: FACE vs PLANE 选择
- **现象**: 第一个特征后，`begin_sketch("Front Plane")` 失效，ActiveSketch 始终为 None
- **根因**: 在已有特征的文档中，选择 PLANE 基准面无法激活草图
- **解决**: 改用 `begin_sketch_on_face(x, y, z)` 选择实体表面
- **示例**:
  ```python
  # 错误：底座创建后继续用基准面会失败
  m.begin_sketch("Front Plane")  # ActiveSketch = None

  # 正确：在实体表面开草图
  m.begin_sketch_on_face(25, 0, 10)  # 底座顶面中心
  ```

### Bug 7: CreateLine 返回 None
- **现象**: 线段创建静默失败
- **解决**: 确保 `ActiveSketch is not None` 后再调用 CreateLine
- **代码**: `_ensure_sketch_active()` 方法

### Bug 8: 三角形轮廓不闭合
- **现象**: 3条线画完但 FeatureExtrusion3 返回 None
- **解决**: 在 end_sketch() 前调用 `MergePoints(0.0005)` 闭合端点
- **代码**:
  ```python
  active_sk = self.skm.ActiveSketch
  active_sk.MergePoints(0.0005)  # Bug 8 关键！
  self.skm.InsertSketch(True)
  ```

### Bug 4: FeatureCut3 完全无效
- **现象**: 所有参数组合都返回 None，体积不变
- **解决**: 改用 FeatureExtrusion3 的切除模式（AddPad=False）
- **代码**:
  ```python
  def cut(self, depth=10, through=False):
      T1 = SW_END_THROUGH if through else SW_END_BLIND
      d = depth * MM
      feat = self.fm.FeatureExtrusion3(
          False, False, False, T1, 0, d, 0,  # AddPad=False 即切除
          ...)
  ```

### Bug 3: extrude-to-point (T1=2) 参数类型不匹配
- **现象**: 无论传 list/tuple/None/VARIANT 都报类型不匹配
- **解决**: 放弃此方法，改用标准 FeatureExtrusion3

### Bug 10: 遗留文档干扰
- **现象**: 新建零件时有13个旧文档打开
- **解决**: `new_part()` 前先关闭所有文档
  ```python
  for _ in range(50):
      try:
          if sw.ActiveDoc: sw.ActiveDoc.CloseDoc(0)
      except: pass
  ```

## 6. 建模方法论

### 旋转体（轴、球、法兰）
```python
m.begin_sketch("Front Plane")
m.polyline([(0,R1), (x1,R1), (x1,R2), (x2,R2), (x2,0), (0,0)])
m.centerline(-20, 0, 200, 0)   # 旋转轴略超出轮廓
m.end_sketch()
m.revolve(360)
```

### 板/箱体
```python
m.begin_sketch("Front Plane")
m.rect(0, 0, 120, 80)
m.end_sketch()
m.extrude(10)
# 面上画孔
m.begin_sketch_on_face(0, 0, 10)
m.circle(0, 0, 10)
m.end_sketch()
m.cut(through=True)
```

### 圆角/倒角
```python
# 圆角：取棱边上一点坐标
m.fillet(5, [(60, 40, 5), (-60, 40, 5)])
# 倒角
m.chamfer(1, [(0, 40, 10)], angle_deg=45)
```

## 7. 典型工作流（用户约定）

1. **新建零件** → `m = swapi.new_part()`（自动清理遗留文档）
2. **画草图** → 第一个特征用 `m.begin_sketch("Front Plane")`，后续用 `m.begin_sketch_on_face(x, y, z)`
3. **画轮廓** → 使用 `rect()`、`circle()`、`line()` 等，每步检查 ActiveSketch
4. **闭合端点** → `end_sketch()` 前自动调用 `MergePoints(0.0005)`
5. **创建特征** → `m.extrude(10)` / `m.cut(through=True)` / `m.revolve(360)`
6. **循环** 步骤 2-5 直到完成
7. **保存** → `m.save(os.path.join(dir, "DSH_零件名.sldprt"))`
8. **生成工程图** → `python sw_bridge.py drawing "零件路径"`
9. **导出 DWG** → `python sw_bridge.py dwg "零件路径"`
10. **展示** → `python sw_bridge.py show`

**关键规则**：
- 第一个特征后用 `begin_sketch_on_face()` 而不是 `begin_sketch()`
- `cut()` 已改用 FeatureExtrusion3 切除模式，不再依赖 FeatureCut3
- `MergePoints()` 在 end_sketch() 内自动调用，无需手动调用