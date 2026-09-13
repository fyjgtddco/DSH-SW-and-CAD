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

> **路径查找（新电脑/未知环境）**：先运行 `python "<工程模式根目录>\tools\sw_bridge.py" self-path`，返回 JSON 中的 `.dir` 字段即为 `<包目录>`。无需手动替换占位符。

```powershell
# 查找 sw_bridge.py 路径（推荐第一步）
python "<工程模式根目录>\tools\sw_bridge.py" self-path
# 返回: {"ok": true, "path": "C:\\...\\sw_bridge.py", "dir": "C:\\..."}

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

## 4.5 SW 使用窗口互斥 + 批量调用（多小屋并行时必须遵守）

### SW 进程级互斥窗口（代码级强制，自动生效）

每个 sw_bridge.py 调用都会自动检测 SLDWORKS.EXE 进程并排队（FIFO 先到先用）：

```powershell
# 推荐：带上房间名（来自小屋 prompt 中的房间名）
python "<包目录>\sw_bridge.py" run "script.py" --room "结构件"

# 或用环境变量
$env:DSH_ROOM = "结构件"; python "<包目录>\sw_bridge.py" run "script.py"
```

- 并行模式下多个小屋同时跑：SW 调用自动检测进程并 FIFO 排队（谁先排队谁先用）
- 若返回 sw_busy:true：执行 Start-Sleep -Seconds 90 后重试同一条命令（必须带 --room），队列位置已保留，禁止放弃
- 前一个小屋完全关闭 SW 进程后，队首小屋自动获得 SW 使用权
- 串行模式 / 非模式2：锁自动跳过，零影响
- 锁随小屋生命周期保持，由主对话的 room-end 统一释放并确保 SW 进程关闭
- 小屋收工前必须 close-all 完全关闭 SW，否则 room-end 等 20 秒后会强制关闭

### 一次调用规划多步操作（批量执行，禁止逐步调用）

❌ **禁止**这样逐步调用（每次都要重新连接 SW，又慢又占锁窗口）：
```powershell
python "<工程模式根目录>\tools\sw_bridge.py" new          # 连接1
python "<工程模式根目录>\tools\sw_bridge.py" sketch-rect 120 80 30   # 连接2
python "<工程模式根目录>\tools\sw_bridge.py" save "x.sldprt"    # 连接3
```

✅ **必须**把全部建模步骤写进一个脚本，一次 run 执行（一次调用规划多步）：
```powershell
python "<工程模式根目录>\tools\sw_bridge.py" run "D:\work\build_part.py" --room "结构件"
```
```python
# build_part.py —— 所有步骤一个脚本内完成
import swapi
m = swapi.new_part()
m.begin_sketch("Front Plane"); m.rect(0, 0, 120, 80); m.extrude(30)
m.begin_sketch("Top Plane"); m.circle(0, 0, 12); m.cut(through=True)
m.set_view_iso()
m.save(r"C:\output\DSH_底座.sldprt")
print("DONE")
```

### 开新子代理前的 SW 收尾（铁律）

在创建任何下一个子代理（同级小屋或下一级子代理孩子）之前：
1. 当前建模必须已完成并保存（`m.save(...)`）
2. 调用 `python "<工程模式根目录>\tools\sw_bridge.py" close-all` 保存并关闭 SW
3. 确认 SW 锁已释放（room-end 会自动释放；或 `python "<工程模式根目录>\tools\mode_gate.py" sw-release <房间名>`）
**严禁开着 SW 去开下一个子代理！**

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

### Bug 16: begin_sketch 多特征后永久失效
- **现象**: 第一个特征后，后续所有 `begin_sketch("Front Plane")` 调用失败
- **根因**: `clear_selection()` 过早清除平面选择状态，干扰 `InsertSketch(True)` 的选面逻辑；残留草图模式未退出
- **解决**: 移除 `select_plane()` 中的 `clear_selection()`；在 `begin_sketch()` 开头先 `InsertSketch(False)` 退出残留模式；草图激活成功后再清理选择；fallback 改为动态范围搜索（±5~200mm）

### Bug 17: begin_sketch_on_face 最大边界面不可选
- **现象**: 最小边界（x=0/y=0/z=0）可选，最大边界（x=max/y=max）完全不可选
- **根因**: `SelectByID2(FACE, x,y,z)` 使用射线拾取，在最大边界处射线穿过边/顶点而非面；且 face.GetBox 返回 SW 内部坐标（居中），与用户局部坐标不匹配
- **解决**: 两步匹配法——第一遍用原始坐标尝试；失败后聚合所有面 GetBox 计算实体整体包围盒，将用户局部坐标转换为内部坐标（`internal = body_min + local`）再匹配；匹配后用 `face.Select(True)` 直接选面
- **验证**: 9项测试全部通过 ✅（含最大边界 x=100/y=60、球面、多特征后）

### Bug 18: 多特征后 on_face 成功率下降
- **现象**: 随特征数量增加，`begin_sketch_on_face` 失败率上升
- **根因**: SW COM 内部选择状态累积导致退化
- **解决**: 每次 `SelectByID2` 尝试前调用 `clear_selection()`；特征创建后自动 `rebuild()`

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

### 💓 小屋心跳（代码级强制）

每个小屋必须定期记录存活心跳，防止断联时主对话无法判断状态：

```powershell
# 每 60 秒调用一次（放入后台循环或定时任务）
python "<包目录>\mode_gate.py" room-heartbeat "结构件"

# 检查心跳状态
python "<包目录>\mode_gate.py" room-heartbeat-check "结构件"
# 返回: {"alive": true/false, "age_seconds": 45}
```

⚠️ 若心跳超过 180 秒未更新，主对话会判定该小屋死亡并自动重启（通过 workflow_gate.py restart）

---

## 7. 典型工作流（用户约定）

1. **新建零件** → `m = swapi.new_part()`（自动清理遗留文档）
2. **画草图** → 第一个特征用 `m.begin_sketch("Front Plane")`，后续用 `m.begin_sketch_on_face(x, y, z)`
3. **画轮廓** → 使用 `rect()`、`circle()`、`line()` 等，每步检查 ActiveSketch
4. **闭合端点** → `end_sketch()` 前自动调用 `MergePoints(0.0005)`
5. **创建特征** → `m.extrude(10)` / `m.cut(through=True)` / `m.revolve(360)`
6. **循环** 步骤 2-5 直到完成
7. **保存** → `m.save(os.path.join(dir, "DSH_零件名.sldprt"))`
8. **生成工程图** → `python "<工程模式根目录>\tools\sw_bridge.py" drawing "零件路径"`
9. **导出 DWG** → `python "<工程模式根目录>\tools\sw_bridge.py" dwg "零件路径"`
10. **展示** → `python "<工程模式根目录>\tools\sw_bridge.py" show`

**关键规则**：
- `begin_sketch()` 在多特征后已修复（Bug 16），先退出残留草图模式再重新选面
- `begin_sketch_on_face()` 优先用**面包围盒匹配+face.Select(True)**选面（Bug 17），可可靠命中最大边界和旋转体曲面
- 每个 SelectByID2 尝试前自动清除选择状态（Bug 18）
- 特征创建后自动 rebuild() 刷新 COM 状态（Bug 18）
- `cut()` 已改用 FeatureExtrusion3 切除模式，不再依赖 FeatureCut3
- `MergePoints()` 在 end_sketch() 内自动调用，无需手动调用