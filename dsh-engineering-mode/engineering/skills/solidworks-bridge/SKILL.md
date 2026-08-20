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
m.end_sketch()                       # 结束草图（自动合并端点）

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

1. **新建零件** → `m = swapi.new_part()`
2. **画草图** → `m.begin_sketch("Front Plane")` → 画轮廓 → `m.end_sketch()`
3. **创建特征** → `m.extrude(10)` / `m.cut(through=True)` / `m.revolve(360)`
4. **循环** 步骤 2-3 直到完成
5. **保存** → `m.save(os.path.join(dir, "DSH_零件名.sldprt"))`
6. **展示** → `python sw_bridge.py show`

**注意**：建模完成后**不要**调用 massprops 校验——容易卡死 SolidWorks。


## 8. SW → CAD 图纸转换（一键生成工程图）

### 使用方法（命令行）

```powershell
# 从零件生成工程图（三视图 + 等轴测）
python "<包目录>\sw_bridge.py" drawing <零件路径> [输出路径]

# 从零件直接生成 DWG 文件
python "<包目录>\sw_bridge.py" dwg <零件路径> [输出路径]
```

### 示例

```powershell
# 生成工程图
python "C:\...\tools\sw_bridge.py" drawing "C:\...\DSH_正方形.sldprt"
# 输出: DSH_正方形.slddrw + 截图

# 生成 DWG
python "C:\...\tools\sw_bridge.py" dwg "C:\...\DSH_正方形.sldprt" "C:\...\output.dwg"
# 输出: DSH_正方形.slddrw + DSH_正方形.dwg + 截图
```

### SW→DWG 完整工作流（用户一句话搞定）

用户说："把这个零件变成 CAD 图纸"，AI 执行：

```powershell
# 第一步：找最近生成的 sldprt 文件
python "sw_bridge.py" list

# 第二步：生成工程图
python "sw_bridge.py" drawing "DSH_正方形.sldprt"

# 第三步：导出 DWG
python "sw_bridge.py" dwg "DSH_正方形.sldprt" "DSH_正方形.dwg"

# 第四步：展示截图给用户验收
python "sw_bridge.py" show
```

### AI 自动生成图纸的完整脚本

```python
import os
import swapi

# ── 生成 11mm 正方形 ──
m = swapi.new_part()
m.begin_sketch("Front Plane")
m.rect(0, 0, 11, 11)
m.end_sketch()
m.extrude(11)
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "DSH_正方形.sldprt")
m.save(out)
print(f"零件已保存: {out}")
```

然后执行：
```powershell
python sw_bridge.py run <脚本路径>
python sw_bridge.py drawing "DSH_正方形.sldprt"
python sw_bridge.py dwg "DSH_正方形.sldprt"
python sw_bridge.py show
```

### API 说明

| 命令 | 参数 | 说明 |
|------|------|------|
| `drawing` | `<part>` `[output]` | 从零件生成工程图，含标准三视图+等轴测 |
| `dwg` | `<part>` `[output]` | 生成工程图并导出为 DWG |
| `export-pdf` | `<output>` | 导出当前视图为 PDF |

### 注意事项

1. **工程图模板自动探测**：无需手动指定，自动查找 gb_a3/gb_a4 模板
2. **视图布局**：前视图居中，俯视图在下，右视图在右，等轴测在右上
3. **DWG 导出**：SolidWorks 自动完成图纸到 DWG 的转换
4. **截图**：每次操作后自动截图，方便 AI 向用户展示结果