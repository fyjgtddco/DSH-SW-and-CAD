---
name: sw-to-cad
description: >-
  SolidWorks 零件一键转换为 CAD 工程图（DWG/PDF）。
  支持从 .sldprt 零件生成标准三视图工程图，并导出为 DWG（AutoCAD 可用）或 PDF。
whenToUse: >-
  当用户要求把 SolidWorks 零件转成 CAD 图纸、导出工程图、生成三视图、
  或需要 .dwg/.pdf 格式的图纸时使用。
source: engineering
provider: filesystem
---

# SW → CAD 图纸转换指南

## 功能概览

将 SolidWorks 3D 零件（.sldprt）一键转换为：
- **工程图**（.slddrw）：含标准三视图 + 等轴测
- **DWG**（.dwg）：可直接用 AutoCAD 打开编辑
- **PDF**（.pdf）：适合打印和分享

## 使用方法

### 命令格式

```powershell
# 生成工程图
python "<包目录>\sw_bridge.py" drawing <零件路径> [输出路径]

# 生成 DWG（推荐用于 AutoCAD）
python "<包目录>\sw_bridge.py" dwg <零件路径> [输出路径]

# 导出 PDF（快速预览）
python "<包目录>\sw_bridge.py" export-pdf <输出路径>

# 展示当前窗口
python "<包目录>\sw_bridge.py" show
```

### 示例

```powershell
# 从零件生成 DWG
python "C:\Users\...\tools\sw_bridge.py" dwg "C:\Users\...\DSH_法兰盘.sldprt" "C:\Users\...\DSH_法兰盘.dwg"

# 从零件生成工程图
python "C:\Users\...\tools\sw_bridge.py" drawing "C:\Users\...\DSH_法兰盘.sldprt"
```

## AI 调用流程

当用户说"把这个零件转成 CAD 图纸"时：

### 步骤 1：确认零件路径
```powershell
python "sw_bridge.py" list
# 获取当前打开的零件列表
```

### 步骤 2：生成工程图
```powershell
python "sw_bridge.py" drawing "<零件路径>"
```
输出：
- 自动创建三视图（前/俯/右）+ 等轴测
- 保存为 `.slddrw` 文件
- 返回截图路径

### 步骤 3：导出 DWG（可选）
```powershell
python "sw_bridge.py" dwg "<零件路径>" "<输出.dwg>"
```
输出：
- 自动转换为 DWG 格式
- 可直接用 AutoCAD 打开

### 步骤 4：展示结果
```powershell
python "sw_bridge.py" show
```
展示当前 SolidWorks 窗口截图给用户验收。

## 技术原理

1. **打开零件**：通过 COM 接口启动 SolidWorks 并打开零件
2. **创建工程图**：使用 `NewDocument` 创建绘图文档
3. **插入视图**：
   - `CreateDrawViewFromModelView3` 插入标准视图
   - 前视图居中，俯视图在下，右视图在右，等轴测在右上
4. **保存**：`SaveAs3` 保存为 .slddrw
5. **导出 DWG**：重新打开工程图，用 `SaveAs3` 导出为 DWG

## 已知限制

1. **自动标注**：当前版本不自动添加尺寸标注，需要手动标注
2. **图框**：使用默认 A3 图纸大小，不包含标题栏
3. **复杂装配体**：仅支持单零件，装配体需要拆解后分别出图
4. **模板**：自动探测绘图模板，如果找不到可能失败

## 后续改进方向

- 自动添加尺寸标注
- 自定义图纸大小（A4/A3/A2/A1/A0）
- 添加标题栏和材料明细表
- 支持直接导出 DXF 格式