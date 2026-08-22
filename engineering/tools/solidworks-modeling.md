---
name: solidworks-modeling
description: >-
  通过 Python (win32com) 桥接驱动 SolidWorks 自动建模（通用版，跨电脑/跨版本）。
  包含连接方法、高层建模 API (swapi.py)、执行器 (sw_bridge.py)、全部已踩坑及
  解决方案、以及用户约定的建模流程（窗口前台实时展示 + 自动缩放截图 + 跳过校验）。
whenToUse: >-
  当用户要求用 SolidWorks 建模（轴/板/零件/装配等），或要求自动生成并执行
  SolidWorks 建模脚本，或询问 SolidWorks COM 连接/API 用法时使用。
---

# SolidWorks 建模（DeepSeek Harness 驱动）— 通用版

> **本文件是通用版**：不写死任何电脑路径或 SolidWorks 版本号。
> 复制到新电脑后，把文中 `<包目录>` 替换成实际路径即可。
> 包内文件：`sw_bridge.py`（执行器）、`swapi.py`（高层建模 API）、
> `examples\`（示例脚本）、`README_使用教程.md`（给新用户的教学文档）。

## 0. 单位约定（用户明确要求）

**所有尺寸单位默认毫米（mm）**，用户没说单位就是 mm。脚本中直接写
`m.rect(0, 0, 120, 80)` 就是 120mm×80mm，`m.extrude(10)` 就是 10mm 厚。

## 0.1 保存命名约定（用户明确要求）

**保存文件名按用户提供的模型名称命名**：用户说"建模一个圆球"，就保存为
`DSH_圆球.sldprt`；说"五段阶梯轴"就保存 `DSH_五段阶梯轴.sldprt`。
规则：
- 前缀统一 `DSH_` + 用户提供的名称 + `.sldprt`（中文名称保留中文）。
- 若用户给了明确的文件名/零件名，则优先用用户指定的名字。
- 保存路径统一在脚本所在目录（默认 `<包目录>\`，可在脚本里改）。

## 0.2 可视化建模（用户明确要求：要能看到建模过程）

用户要求**实时看到建模全过程**，包括 SolidWorks 启动、新建零件、每一步
拉伸/切除/旋转等特征。实现（已内置 swapi.py）：
- `VISUAL_MODE = True`：每个特征创建后窗口置前 + `time.sleep(VISUAL_PAUSE)`。
- **暂停时长 `VISUAL_PAUSE = 0.5` 秒**（用户设定）。
- **固定视角（用户约定）**：建模全程固定**等轴测视角 Isometric**
  （`ShowNamedView2("*Isometric", 7)`，即正面+右面+上面交叉的视角），
  **不做每步 zoom_to_fit**（关掉每步重建刷新视图）。
- **模型缩放（用户约定）**：`set_view_iso()` 用 `ZoomByFactor(1.2)`，
  模型显示为默认大小的 **1.2 倍**（用户要求比之前大一号/大一倍）。
- 首次启动 SolidWorks 时，等待主窗口出现（`_wait_sw_window`）并置前，
  用户能看到启动动画/加载过程。
- 新建零件后设置等轴测视角并暂停，让用户看到初始状态。
- **窗口最大化（用户约定）**：新建零件或打开文件后，SolidWorks 主窗口
  **最大化**（`_show_main_window(maximize=True)` / `bring_to_front()`）。
  关键：**只在窗口未最大化时才执行 RESTORE+MAXIMIZE**，已最大化则只
  `SetForegroundWindow` 置前——否则每步特征后窗口会晃动/闪动。
  已实测：建模全程窗口 rect 保持 (0,0,1920,1032) 不变。
- **画草图先正视于平面（用户约定）**：每次 `begin_sketch` 前先"正视于"
  该草图平面（`ShowNamedView2` 切到标准视图：Front Plane→`*Front`、
  Top Plane→`*Top`、Right Plane→`*Right`），用户能看到草图正面；
  `begin_sketch_on_face` 默认正视 `*Front`。画完特征后视角由
  `set_view_iso()` 恢复等轴测。
- **模型几何中心居中（用户约定）**：视图中心对准**零件的几何中心**，
  不是原点居中。用 `doc.ViewZoomtofit2()`（文档对象方法，实测可用——
  注意不是 `ActiveView.ViewZoomtofit2()`，后者在 dynamic 下报
  AttributeError）。`set_view_iso()` 已内置：等轴测 → ZoomToFit 居中 →
  ZoomByFactor(1.2) 放大。
- **实时居中（用户约定）**：**每一步特征创建后**（`_visual_step`）都调用
  `set_view_iso()` 使模型实时居中展示（不是建模完才居中）。画草图时是
  正视视图，特征创建后切回等轴测并居中。
- 调整节奏：改 `swapi.VISUAL_PAUSE`（秒）即可；若需快速建模（不看过程），
  临时设 `swapi.VISUAL_MODE = False`。

## 1. 核心架构

```
DSH (pwsh 工具)
  │  python <包目录>\sw_bridge.py <命令>
  ▼
sw_bridge.py (win32com 执行器)
  │  win32com.client.dynamic.Dispatch('SldWorks.Application')
  ▼
SolidWorks（任意版本，自动探测模板/枚举）
```

文件位置：`<包目录>\`（通用版所在文件夹，任何电脑均可）
- `sw_bridge.py` — 执行器（命令模式 + `run` 脚本执行 + `doctor` 自检）
- `swapi.py` — 高层建模封装（DSH 生成的脚本只调这个；自动探测模板、
  自动适配版本枚举、跨语言选草图）
- `examples\` — 示例建模脚本（板/轴/杯/齿轮等，教学用）
- `README_使用教程.md` — 给新用户的完整教学文档（含提示词写法）

**重要**：PowerShell 原生 `New-Object -ComObject SldWorks.Application` 在部分
电脑**不可用**（类型库注册不完整导致 TYPE_E_ELEMENTNOTFOUND）。必须用 Python
win32com 的 IDispatch 后期绑定（dynamic.Dispatch），它不依赖类型库——
这也是本包能"通用"的关键原因之一。

## 1.1 通用版做了什么（跨电脑/跨版本适配）

| 原版问题（只适配开发机） | 通用版方案 |
|---|---|
| 模板路径硬编码 `C:\ProgramData\SolidWorks\SOLIDWORKS 2022\templates` | `get_part_template()` 自动探测：① 查 SW 用户设置里的模板目录 ② 扫描 `C:\ProgramData\SolidWorks\SOLIDWORKS*` ③ 扫描任意盘符 `*SOLIDWORKS*` 目录 ④ 匹配 gb_part/Part/零件 等模板名 |
| 两侧对称枚举 `swEndCondMidPlane=6` 写死（2022 专用） | `_get_midplane_enum()` 按 `RevisionNumber` 主版本号换算：2020+ 用 6，2018 用 5 |
| 草图捕捉枚举 249/271/278 写死 | `_disable_snapping()` 尝试 249/271/278/200/201/202 多组，逐个 try，失败忽略 |
| 倒角用 `FeatureChamferType`（2022 没有，会报错） | 先 `InsertFeatureChamfer`，异常时回退 `FeatureChamferType`（兼容新旧） |
| 中文草图名"草图2"硬编码（仅中文版） | `select_sketch_by_name()` 自动试 `SketchN` / `草图N`；`select_sketch_by_index()` 按特征树序号选，完全与语言无关 |
| 窗口标题匹配 `'SOLIDWORKS PREMIUM'`（仅专业版） | `_find_sw_windows()` 匹配任意含 'SOLIDWORKS' 的非欢迎页窗口（`title.strip() != 'SOLIDWORKS'`），标准版/专业版/各版本通用 |
| 找不到模板/连不上时不知原因 | 新增 `python sw_bridge.py doctor` 自检命令：检查 Python、pywin32/mss/Pillow、SW 连接、模板探测，直接给出缺什么 |

## 2. 标准工作流（用户约定，必须遵守）

1. **新电脑先自检**：`python <包目录>\sw_bridge.py doctor`，确认
   `"ok": true`。缺依赖按提示 `pip install pywin32 mss Pillow`。
2. **窗口保持前台**：建模前/后调用 `swapi.SWModel.bring_to_front()`，
   确保 SolidWorks 主窗口可见，用户能实时看到建模过程。
   注意：SolidWorks 窗口可能被最小化到屏幕外 (-32000,-32000)，
   `bring_to_front()` 会用 MoveWindow 移回主屏。
3. **建模**：DSH 生成建模脚本 → `python sw_bridge.py run <script.py>`。
   `run` 会自动执行脚本并在完成后：`zoom_to_fit()` + `screenshot()`。
4. **截图展示**：`run` 结果 JSON 里带 `screenshot_path`（PNG），
   脚本产物是 `<包目录>\solidworks_live.png`。
5. **导出渲染图**（可选）：`m.export_image(path)` 用 SolidWorks 内置
   SaveBMP 导出位图。
6. **跳过校验（用户明确约定）**：建模完成后**不要**调用 massprops 或做
   质量/体积校验——容易卡死 SolidWorks。直接交付文件即可。
   如需独立展示用 `python sw_bridge.py show`（前台+缩放+截图）。

## 2.1 通用建模方法论（各类零件的建模思路，实测验证）

**建模核心思维：把零件拆成"草图轮廓 + 特征操作"，按形状选特征。**

### 各类零件的推荐建模方法
| 零件类型 | 推荐方法 | 关键要点 |
|---|---|---|
| **球体** | `create_sphere(cx, cy, cz, radius)`（Modeler 方法） | 不依赖草图旋转，直接通过 Modeler 创建球形曲面再转换为实体，避免几何条件限制 |
| **轴/回转体**（阶梯轴、法兰） | 旋转特征 `revolve(360)` | 在穿过轴心的平面画**上半截面轮廓**（底边贴轴）+ `centerline` 旋转轴；轮廓用 `polyline` 阶梯点 |
| **板/块/箱体** | 拉伸 `extrude` + 切除 `cut` | 画截面 → 拉伸 → 面上画孔/槽 → 切除 |
| **带孔零件** | 拉伸 + 圆孔切除 | 顶面 `begin_sketch_on_face` 画圆 → `cut(through=True)` |
| **圆角/倒角** | `fillet(r, edge_pts)` / `chamfer(w, edge_pts, angle)` | edge_pts 是目标棱边上任意一点坐标 |
| **键槽** | 直槽口 `CreateSketchSlot` + 等距切除 | 见坑 #16-18 |

### 建模流程模板（通用步骤）
```
1. 新建零件 new_part()
2. 选基准面 begin_sketch(plane) —— 自动正视+居中
3. 画轮廓（rect/circle/polyline/line/centerline）
4. end_sketch() —— 自动合并端点+刷新视图
5. 特征操作（extrude/cut/revolve/fillet/chamfer/create_sphere）
6. 重复 2-5 直到完成
7. 保存 save(路径) —— 按用户命名约定

# 特殊对象（如球体）可直接使用 Modeler 方法：
m.create_sphere(cx=0, cy=0, cz=0, radius=25)  # D50mm 球体
```

### 设计思路要点（DSH 生成代码时遵循）
- **先整体后细节**：先建立主要形体（拉伸/旋转），再切孔/槽/圆角。
- **旋转体优先用 revolve**：一个截面+旋转轴，比多次拉伸高效且精确。
- **对称拉伸**：`extrude(depth, symmetric=True)` 做两侧对称的形体。
- **孔用圆+切除**：`circle` 画在目标面 → `cut(through=True)` 贯穿。
- **选边技巧**：圆角/倒角用棱边上的坐标点定位（y 方向极值点、边中点）。
- **遇到失败**：先检查轮廓是否闭合（显式首尾相接）、特征参数（见坑清单）。

### 可视化节奏（观察者视角，用户要求）
- 画草图 → 正视于平面 + 居中（看到轮廓正面）
- 每个特征创建后 → 等轴测 + 模型居中 + 缩放1.2 + 暂停0.5s（看到特征效果）
- 建模完成 → 等轴测居中 + 截图展示

## 3. 常用命令（pwsh 执行）

```powershell
python "<包目录>\sw_bridge.py" doctor # 环境自检（新电脑第一步）
python "<包目录>\sw_bridge.py" run   <建模脚本.py>
python "<包目录>\sw_bridge.py" show   # 前台+缩放+截图
python "<包目录>\sw_bridge.py" status # 连接/版本/文档
python "<包目录>\sw_bridge.py" open   <模型.SLDPRT>
python "<包目录>\sw_bridge.py" save   <输出.SLDPRT>
python "<包目录>\sw_bridge.py" export-pdf <输出.pdf>
```

## 4. swapi.py 高层 API（DSH 生成脚本的模板）

```python
import swapi
m = swapi.new_part()                 # 新建零件（模板自动探测）
m.begin_sketch("Front Plane")        # 或 m.begin_sketch_on_face(x,y,z) mm
m.rect(0, 0, 120, 80)               # 中心矩形 mm
m.circle(0, 0, 10)                  # 圆 mm
m.line(0, 30, 20, 30) / m.polyline([(x,y),...]) / m.centerline(x1,y1,x2,y2)
m.end_sketch()                      # 自动 MergePoints 闭合轮廓
m.extrude(10, symmetric=False)      # 拉伸 mm（symmetric 枚举自动适配版本）
m.cut(depth=10, through=True)       # 切除 / 贯穿
m.revolve(360, cut=False)           # 旋转（需先画轮廓+中心线）
m.fillet(5, [(x,y,z)mm,...])        # 圆角，edge_points 是边上采样点
m.chamfer(1, [(x,y,z)mm,...], 45)   # 倒角
m.save(out_path)                    # 另存为
m.bring_to_front() / m.zoom_to_fit() / m.screenshot(path) / m.export_image(path)
```

所有尺寸参数一律 **毫米**，内部自动转米。输出 JSON。

跨语言选草图（新电脑可能是英文版/法文版/日文版 SolidWorks）：
```python
import swapi
# 方式一：按序号选第 N 个草图（与语言无关，推荐）
swapi.select_sketch_by_index(sw, m.model, 2)
# 方式二：自动尝试 SketchN / 草图N / 原名
swapi.select_sketch_by_name(sw, m.model, "轮廓")
```

## 5. 已踩的坑与解决方案（SolidWorks 2022 SP0 实测，通用性标注）

1. **PowerShell COM 失败**：类型库注册不完整（`{83A33D31-...}` 的 1e.0
   版本缺 win32 键、tlb 加载失败）→ 用 Python win32com dynamic 后期绑定。
   *（任何电脑都可能出现，本包已规避）*
2. **无参 COM 成员按属性访问**：`sw.RevisionNumber` 而非 `sw.RevisionNumber()`；
   带参方法正常调用。
3. **对称拉伸枚举随版本变化**：2022 = 6，2018 = 5。
   swapi 按 `RevisionNumber` 自动适配（2020 及以上=6，2019 及以下=5）。
   *（版本相关的坑，通用版已处理）*
4. **圆角必须 Options=2**（swFeatureFilletUniformRadius），否则
   FeatureFillet3 静默失败返回 None。Ftyp 用 swFeatureFilletType_Simple=0。
   签名: `FeatureFillet3(2, R1_m, 0, 0, 0, 0, 0, None×7)`。
5. **倒角方法名是 `InsertFeatureChamfer`**（FeatureChamferType 在 2022 的
   FeatureManager 上不存在；更早版本反之）。
   通用版先试前者，失败自动回退后者。
   签名: `InsertFeatureChamfer(0, ChamferType, width_m, angle_deg, 0,0,0,0)`，
   ChamferType: 1=角度-距离, 2=距离-距离。
6. **选边用坐标点**：`SelectByID2("", "EDGE", x_m, y_m, z_m, Append, 0, empty, 0)`，
   Append=True 累加多条边；empty = VARIANT(VT_DISPATCH, None)。
7. **质量属性数组顺序（2022）**：
   `[cogX, cogY, cogZ, volume, surface_area, mass, Ixx, Iyy, Izz, Ixy, Ixz, Iyz]`
   —— 与旧文档不同（但按用户约定通常跳过校验）。
8. **SaveAs3 覆盖已有文件可能返回 rc=1**，但文件实际已更新，以文件存在/
   时间戳变化为成功标志。
9. **草图推理捕捉会吸附非整数坐标（重大坑）**：swSketchInference=249、
   swSketchSnapsNearest=271、swSketchSnapsGrid=278 会把 17.5mm 吸到 18mm。
   swapi 连接时自动禁用（旧版本枚举不同时尝试 200/201/202，失败忽略）。
   *（通用版已处理版本差异）*
10. **禁用捕捉后端点不自动合并（重大坑）**：轮廓坐标闭合但几何开口，
    旋转/拉伸失败。`end_sketch()` 默认调用 `SketchManager.MergePoints(0.0005)`
    合并端点（注意要在 InsertSketch 退出前调用）。
11. **旋转特征要求**：轮廓必须在旋转轴一侧（底边贴轴可以），
    旋转轴用 `m.centerline(x1,y1,x2,y2)` 画，略超出轮廓两端。
12. **GetBodies 等无参方法**在 dynamic 下可能解析异常，必要时用
    `_oleobj_.InvokeTypes(dispid, ...)` 直接调用。
13. **窗口可能被最小化到屏幕外** (-32000,-32000)：用 MoveWindow 移回主屏。
14. **欢迎页窗口与主窗口混淆（重大坑）**：SolidWorks 2022 有两个大窗口：
      - 主窗口：标题 **"SOLIDWORKS Premium 2022 SP0.0 - [文档]"**（标准版
        则无 Premium 字样，因此通用版匹配"含 SOLIDWORKS 且非纯 SOLIDWORKS"）
      - 欢迎页：标题恰好 **"SOLIDWORKS"**（纯白 Home/Welcome 页，含
        "了解更多信息"、右上角叉号），建模后不要显示它。
    窗口匹配**必须排除纯 "SOLIDWORKS" 标题**，否则 bring_to_front/screenshot
    会把欢迎页当主窗口。swapi 已内置 `_find_sw_windows()` 分离两者，
    `bring_to_front()` 会自动隐藏欢迎页（ShowWindow SW_HIDE）。
15. **建模完成显示的是模型不是欢迎页的判定**：模型视图中央有带棱线的
    立体（等轴测），背景浅色 + 左侧特征树面板；欢迎页则是大片纯白 +
    中央图形图标。截图分析时不要只看白色占比就误判为欢迎页。
16. **键槽/直槽口 API 语义（重大坑，实测确认）**：`SketchManager.CreateSketchSlot`
    的正确用法（SolidWorks 2022 + pywin32）：
      `CreateSketchSlot(SlotCreationType, SlotLengthType, Width_m,
                        X1, Y1, Z1, X2, Y2, Z2, 0,0,0, 方向, False)`
      - SlotCreationType = 1（center_line 中心直槽口）
      - SlotLengthType = **1（FullLength）**：X1 = 槽中心，**X2 = 右圆弧圆心**
        （注意：X2 不是最外端点！）。传 (中心, 右圆心) 即可。
      - 实测：传 (中心=130, 右圆心=142.5) → 圆弧圆心落在 117.5 和 142.5，
        圆心距 25，右圆心距右端面 7.5，全长 35。
      - **CenterCenter 模式(0) 在 pywin32 下会把 X1 解释成槽左端，生成错误几何**，
        不要用。
      - *CreateSketchSlot 是 2016+ 才有的 API，极老版本（2015 及以下）可能
        不存在；遇到时用 圆+两直线+圆 手工拼槽口。*
17. **键槽切除方法（用户指导 + 实测）**：
      - 在**穿过轴心的平面**（Front Plane）画直槽口草图；
      - **选中该草图**再切（第二个草图中文名是"草图2"，旋转轮廓是"草图1"，
        选错会把整个截面切掉）——通用版用 `select_sketch_by_index` 按序号选，
        不受语言影响；
      - 拉伸切除 `FeatureCut3`：起始条件=等距(swStartOffset=3)
        StartOffset=键槽底面距轴心距离，FlipStartOffset=True（方向向外），
        终止=完全贯穿(swEndCondThroughAll=1)。
18. **键槽参数定义（用户约定）**：说"键槽长度25"指**圆心距(中心距)**25；
    "右侧圆心距右端面7.5"指右圆弧圆心到右端面的距离。全长 = 圆心距 + 2×半径
    （宽10 → 半径5 → 全长 = 25+10 = 35）。
19. **画草图正视视图的调用时机（实测）**：必须先 `InsertSketch` 进入草图，
    再 `ShowNamedView2("*Front"/"*Top"/"*Right")` 正视——顺序反了视图不会
    保持正视（会退回等轴测）。`swapi.begin_sketch` 已内置正确顺序。
20. **空白零件画草图时不强制居中**：只有基准面、无实体时 ZoomToFit 按基准面
    fit，画面偏左下是正常现象；画完轮廓后 `end_sketch` 会再 fit 一次居中。
21. **多文档积累会卡死**：连续测试会产生大量未关闭文档（几十个），导致
    NewDocument 返回 None 或卡顿。建模前若文档数异常多，先清理
    （关闭文档/重启 SolidWorks）。

## 5.1 已修复的 Bug

### Bug #1 — extrude() 拔模角度无效
- **现象**：`draft_deg` 参数定义了但没传给 FeatureExtrusion3
- **修复**：将 `draft_enabled` 和 `draft_angle_rad` 传入对应参数位

### Bug #2 — cmd_run 编码崩溃
- **现象**：Windows 中文版 GBK 编码导致 UnicodeDecodeError
- **修复**：subprocess.run 添加 `encoding='utf-8'`

### Bug #3 — cmd_run 二次崩溃
- **现象**：proc.stdout 为 None 时 .strip() 崩溃
- **修复**：添加防御性检查 `(proc.stdout or "").strip()`

### Bug #4 — ExportFile 导出 DWG 失败
- **现象**：SW 2020 可能没有 DWG 翻译器插件
- **修复**：优先使用 SaveAs3，ExportFile 作为备选

### Bug #7 — CreateDrawViewFromModelView 返回 False 而非 None
- **现象**：工程图没有视图但代码报告"已添加"
- **修复**：`if v is not None:` → `if v:`

### Bug #8 — 中文版 SW 基准面名不匹配
- **现象**：`Front Plane` 在中文版 SW 上失败
- **修复**：添加 `_PLANES_ZH` 多语言支持，`select_plane()` 自动尝试中英文

### Bug #9 — end_sketch() 未能提交草图
- **现象**：草图未正确结束，特征树无草图
- **修复**：`end_sketch()` 改用 `InsertSketch(True)` toggle 模式

### Bug #10 — revolve() 不检查返回值
- **现象**：FeatureRevolve2 返回 None 时静默失败
- **修复**：添加 `if feat is None: raise RuntimeError(...)`

### Bug #11 — 草图无法提交到特征树
- **现象**：特征树里只有 Favorites，没有草图特征
- **修复**：同 Bug #9，使用 toggle 模式 `InsertSketch(True)`

### Bug #12 — CreateDrawViewFromModelView 返回 False
- **现象**：4个视图全部返回 False，工程图是空白的
- **修复**：修复 Bug #11 后自动解决

### Bug #13 — GetMassProperties() 导致 SW 崩溃
- **现象**：massprops 可能导致 SolidWorks 卡死
- **修复**：添加 try-except 包裹，并在 docstring 中添加警告

### Bug #14 — 球体创建
- **现象**：revolve 创建球体有时几何条件不满足
- **修复**：添加 `create_sphere()` 方法，使用半圆弧旋转法创建球体

### Bug #15 — 工程图视图重叠/粘连（间距过小、等轴测位置错误）
- **根本原因**：
  - 原坐标 `(*前视, 0.150, 0.180)` / `(*上视, 0.150, 0.070)` 等间距仅 110mm
  - 等轴测 `(0.280, 0.070)` 与俯视图 y 坐标相同，导致右上区域严重重叠
  - 缺少视图间距约束，相邻视图线条直接粘连
- **修复**（强制排版约束）：
  1. 最小间距 35mm（`_DRAWING_VIEW_GAP`），严禁线条粘连
  2. 三视图严格对齐：主俯同x（长对正），主右同y（高平齐）
  3. 等轴测移至右上角独立区域 `(0.310, 0.210)`，与三视图区分离
  4. A3 横向布局重新规划：主视(0.060,0.190) / 俯视(0.060,0.080) / 右视(0.230,0.190) / 轴测(0.310,0.210)
- **附加约束**（写入 agent.cordis.yml 注意事项）：
  - 中心线：圆柱/轴类必须画十字点划线
  - 分界线：贴合特征交界处必须画分隔线或保留 0.1mm 间距
  - 线宽分层：可见轮廓 0.5mm，中心线 0.25mm，隐藏线 0.2mm

## 6. 建模脚本模板（DSH 生成代码参考）

```python
# build_xxx.py — 由 DSH 生成，python sw_bridge.py run 执行
import os
import swapi

m = swapi.new_part()
print("STEP1 new part:", m.title)

# —— 草图 + 特征 ——
m.begin_sketch("Front Plane")
m.rect(0, 0, 120, 80)
m.end_sketch()
m.extrude(10)

# —— 保存（不校验）——
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "xxx.sldprt")
print("STEP2 save:", m.save(out))
```
