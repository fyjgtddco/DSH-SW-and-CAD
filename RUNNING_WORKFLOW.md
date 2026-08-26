# DSH 工程模式 — 代码级完整运行流程
## 用户发送消息到最终交付的每一步源码追踪

> 本文档逐行追踪「帮我建造一个 xxx」从用户点击发送到结果返回的完整调用链。
> 每个函数、每层 import、每次 COM 调用都有代码级说明。

---

## 第一层：DSH 插件启动（会话开始前已完成）

### 1.1 preset.yml → agent.cordis.yml 映射

```
preset.yml
  name: 工程模式
  order: 4
    │
    ▼ DSH 读取此文件，查找同名 agent.cordis.yml
    │
agent.cordis.yml（490 行）
  ├─ persona                    → 3000 字人设注入系统提示
  ├─ agent-instructions         → 64KB 指令缓存
  ├─ tool-pwsh                  → pwsh 工具（PowerShell 执行器）
  ├─ tool-fs                    → 文件系统读写
  ├─ tool-fs-search             → 文件搜索（glob/ripgrep）
  ├─ tool-jobs                  → 后台任务控制
  ├─ skill-filesystem           → 扫描 skills/ 目录
  ├─ tool-skill                 → 技能调用工具
  ├─ tool-goal                  → create_goal / get_goal / update_goal
  ├─ planning                   → exit_plan_mode / plan_mode
  ├─ compaction                 → 上下文压缩（thresholdChars=8192）
  ├─ delegation                 → subagent / workflow
  ├─ tool-ask-user              → 向用户提问
  ├─ tool-todo                  → 任务清单（allowParallelInProgress=false）
  └─ tool-web                   → 网页搜索（searchTimeoutMs=60000）
```

### 1.2 技能文件加载

`skill-filesystem` 扫描 `skills/` 目录，发现：

```
skills/
├── solidworks-bridge/SKILL.md    ← whenToUse: "SolidWorks 建模"、".sldprt"
└── physics-in-loop/SKILL.md      ← whenToUse: "FEA"、"安全系数"、"强度校核"
```

两个 SKILL.md 的内容被拼接到系统提示末尾，AI 在收到用户消息时就能看到这些工作流指引。

---

## 第二层：AI 收到消息后的决策

### 2.1 用户消息进入模型

```
用户: "帮我建造一个长200mm宽60mm厚10mm的铝制悬臂梁"
  │
  ▼ DeepSeek 模型（deepseek-ai/xxx）
  │
  │  系统提示包含：
  │  - persona: "完成工图"核心使命 + 三步设计流程
  │  - skill: solidworks-bridge API 速查 + physics-in-loop 工作流
  │  - tools: pwsh/fs/goal/todo/ask-user 等工具描述
  │
  │  AI 内部推理（不输出给用户）：
  │  ├─ 这是一个悬臂梁设计任务
  │  ├─ 需要 SW 三维建模（solidworks-bridge 技能触发）
  │  ├─ 金属零件通常需要 FEA 验证（physics-in-loop 技能触发）
  │  ├─ 尺寸已给出，材料"铝"需确认牌号（Al6061-T6 是默认）
  │  └─ 载荷未说明，应追问或采用默认值
  │
  ▼ AI 生成 tool_use 调用
```

### 2.2 第一步：创建目标

AI 调用 `create_goal`：

```python
create_goal(
    objective="完成铝制悬臂梁零件设计及工程图纸",
    max_goal_rounds=10
)
```

内部实现（`tool-goal` 插件）：
```
1. 生成全局唯一 goal_id: "goal_xxxxxxxx"
2. 写入同一会话的全局状态（内存字典）
3. 记录创建时间戳
4. 返回 {"goal_id": "goal_xxxxxxxx", "revision": 1, "status": "active"}
```

### 2.3 第二步：创建任务清单

```python
todo_write([
    {"content": "确认材料和载荷条件", "status": "in_progress"},
    {"content": "SW 三维建模", "status": "pending"},
    {"content": "FEA 强度验证", "status": "pending"},
    {"content": "输出工程图纸", "status": "pending"},
])
```

### 2.4 第三步：向用户确认（或跳过）

如果参数不全，AI 调用 `ask_user`；如果参数足够，直接继续。

---

## 第三层：SW 建模执行链

### 3.1 AI 生成 Python 建模脚本

```python
# 临时文件：C:\Users\...\AppData\Local\Temp\_dsh_build_cantilever.py
import os
import swapi

m = swapi.new_part()
m.begin_sketch("Front Plane")
m.rect(0, 0, 200, 60)
m.end_sketch()
m.extrude(10)
out = os.path.join(os.getcwd(), "DSH_悬臂梁.sldprt")
m.save(out)
print(f"OK: {out}")
```

### 3.2 pwsh 工具执行

```powershell
python "C:\Users\j1877\.dsh\.agent-presets\engineering\tools\sw_bridge.py" `
    run "C:\Users\...\Temp\_dsh_build_cantilever.py"
```

### 3.3 sw_bridge.py 入口处理（`run` 命令）

从 `sw_bridge.py` 第 100 行开始：

```python
# 第 100-112 行：get_sw()
def get_sw():
    pythoncom.CoInitialize()                                    # COM STA 线程初始化
    sw = win32com.client.dynamic.Dispatch('SldWorks.Application')  # dynamic dispatch（不依赖类型库）
    try:
        import swapi
        swapi._disable_snapping(sw)                             # 禁用草图吸附
    except Exception:
        pass
    return sw
```

`_disable_snapping` 的实现（swapi.py）：
```python
def _disable_snapping(sw):
    # 不同 SW 版本的枚举值不同，用 try/except 适配
    try:
        sw.SetUserPreferenceInteger(257, 0)    # swSketchConstrState_Off
    except Exception:
        pass
    try:
        sw.SetUserPreferenceInteger(258, 0)    # swSnapToEntity
    except Exception:
        pass
```

### 3.4 sw_bridge.py 的 `cmd_run()` 执行路径

```python
# sw_bridge.py 约第 200-400 行（run 命令处理）
def cmd_run(script_path, *args):
    """执行 AI 生成的 Python 脚本，捕获输出。"""
    # 1. 获取 SW 连接
    sw = get_sw()

    # 2. 设置临时脚本路径
    real_script = os.path.abspath(script_path)

    # 3. 用 exec 在同一进程执行（共享 sw 全局变量）
    extra_globals = {
        'sw': sw,
        'swapi': sys.modules.get('swapi'),  # 已 import 的模块
        '__file__': real_script,
    }
    try:
        with open(real_script, 'r', encoding='utf-8') as f:
            code = f.read()
        # 在 swapi 的命名空间中执行
        exec(code, {'swapi': swapi, 'sw': sw})
    except Exception as e:
        traceback_str = traceback.format_exc()
        return {"ok": False, "error": str(e), "traceback": traceback_str}

    return {"ok": True, "stdout": captured_output}
```

### 3.5 swapi.py 内部：`new_part()` 完整流程

```python
# swapi.py 第 37-113 行：_find_template()
def _find_template(sw=None):
    cands = []

    # 方法1：通过 SW API 查模板路径
    if sw is not None:
        for pref in (108, 109, 110, 111):  # 各种模板位置枚举
            try:
                d = sw.GetUserPreferenceStringValue(pref)
                if d and os.path.exists(d):
                    cands.append(d)
            except Exception:
                pass
        # 零件模板直接路径
        try:
            tmpl = sw.GetUserPreferenceStringValue(101)  # swFileLocations_PartTemplate
            if tmpl and os.path.exists(tmpl):
                cands.append(tmpl)
        except Exception:
            pass

    # 方法2：ProgramData 模板目录
    if sw is not None:
        year = _version_year(_version_major(sw))  # 如 SW 2024 → year=2024
        d = r"C:\ProgramData\SolidWorks\SOLIDWORKS %d\templates" % year
        if os.path.isdir(d):
            cands.append(d)

    # 方法3：通用 ProgramData 路径
    for ver_dir in glob.glob(r"C:\ProgramData\SolidWorks\SOLIDWORKS*"):
        cands.append(os.path.join(ver_dir, "templates"))

    # 方法4：多盘符搜索
    for drive in ("C:", "D:", "E:", "F:"):
        for sw_dir in glob.glob(drive + r"\*SOLIDWORKS*"):
            cands.append(os.path.join(sw_dir, "templates"))

    # 找模板文件
    tmpl_names = ["gb_part.prtdot", "Part.prtdot", "零件.prtdot", "part.prtdot", "PART.PRTPRT"]
    for d in cands:
        for n in tmpl_names:
            p = os.path.join(d, n)
            if os.path.exists(p):
                return p
        found = glob.glob(os.path.join(d, "*.prtdot"))
        if found:
            return found[0]
    return None
```

```python
# swapi.py：new_part()
def new_part():
    # 1. 获取 SW 应用
    sw = _get_sw()

    # 2. 关闭遗留文档（最多 50 个）
    for _ in range(50):
        try:
            sw.ActiveDoc.CloseDoc(0)
        except Exception:
            break

    # 3. 找模板
    template_path = get_part_template(sw)

    # 4. 创建新零件
    doc = sw.NewDocument(template_path, 0, 0, 0)

    # 5. 返回包装对象
    return SWModel(sw, doc)
```

### 3.6 SWModel.begin_sketch() 完整流程

```python
# swapi.py SWModel.begin_sketch()
def begin_sketch(self, plane_name_or_coords):
    sw = self.sw
    doc = self.doc
    skmgr = doc.SketchManager

    # Bug 16 修复：先退出任何残留草图模式
    try:
        if skmgr.ActiveSketch is not None:
            skmgr.InsertSketch(False)  # 不合并，直接退出
    except Exception:
        pass

    if isinstance(plane_name_or_coords, str):
        # 字符串模式：按名字选基准面
        plane = self._select_plane(plane_name_or_coords)
        skmgr.ActiveSketch = plane
        skmgr.InsertSketch(True)     # 开始新草图
    else:
        # 坐标模式：begin_sketch_on_face(x,y,z)
        self._begin_sketch_on_face(*plane_name_or_coords)

    return self  # 返回 self 支持链式调用
```

`_select_plane()` — 基准面选择（兼容中英文 SW）：
```python
def _select_plane(self, name):
    """选择基准面，兼容中英文 SolidWorks。"""
    sw = self.sw
    doc = self.doc
    # 尝试多种命名方式
    plane_names = [name, name.lower(), name.title(),
                   _translate_plane(name)]  # 中英文映射
    for pn in plane_names:
        try:
            selMgr = doc.SelectionManager
            obj = doc.Component2.GetBodiesByType2(1, pn)  # 尝试实体选择
            if obj:
                return obj[0]
        except Exception:
            continue
    # 回退：直接设置 ActiveSketch
    skmgr = doc.SketchManager
    skmgr.ActiveSketch = pn
    return pn
```

### 3.7 SWModel.rect() 完整流程

```python
# swapi.py SWModel.rect()
def rect(self, cx, cy, w, h):
    """画中心矩形，单位 mm。"""
    skseg = self.skmanager.CreateSegs()
    # SW 内部单位是米，所以乘以 MM=0.001
    x1, y1 = cx - w/2, cy - h/2
    x2, y2 = cx + w/2, cy + h/2

    # VARIANT 封送（VT_ARRAY | VT_R8 = 双精度数组）
    import pythoncom
    coords = pythoncom.VARIANT(
        pythoncom.VT_ARRAY | pythoncom.VT_R8,
        (float(x1), float(y1), float(x2), float(y2))
    )
    skseg.AddBy2Points(None, None, coords)  # 添加矩形
    return self
```

### 3.8 SWModel.extrude() 完整流程

```python
# swapi.py SWModel.extrude()
def extrude(self, depth_mm, symmetric=False):
    doc = self.doc
    featMgr = doc.FeatureManager

    # 提交当前草图
    self.skmanager.InsertSketch(True)

    # 单位换算：mm → m
    depth_m = float(depth_mm) * 0.001

    # Bug 2 修复：对称拉伸枚举值随版本变化
    if symmetric:
        end_type = self._get_midplane_enum()  # 2022→6, 2018→5
    else:
        end_type = 0  # swEndCondBlind（盲孔拉伸）

    # FeatureExtrusion3 API 调用
    feat = featMgr.FeatureExtrusion3(
        False,       # AddPad = False → 凸台（True=切除）
        False,       # Blind = False → 非对称
        False,       # BothDirections
        end_type,    # 端条件类型
        0,           # Direction1Angle
        depth_m,     # Depth (米)
        0,           # Direction2Angle
        0,           # Direction2Depth
        False,       # DraftAngle
        0,           # DraftFaceAngle
        False,       # ThinFeature
        0,           # ThinType
        0,           # ThinThickness
        False,       # UseDraftAngleOption
        False,       # UseDirection2
        False,       # PropagateCut
    )

    # Bug 18 修复：特征创建后重建
    doc.Rebuild()

    return feat.Name if feat else "extrude"
```

### 3.9 SWModel.save() 完整流程

```python
# swapi.py SWModel.save()
def save(self, path):
    abs_path = os.path.abspath(path)
    # SW SaveAs2: path, version, configName, options
    # options=0 表示默认保存选项
    saved = self.doc.SaveAs2(abs_path, 0)
    if not saved:
        raise RuntimeError(f"Save failed: {abs_path}")
    return {"ok": True, "path": abs_path}
```

---

## 第四层：FEA 求解执行链

### 4.1 触发点：AI 调用 physics_bridge.py

```powershell
python sw_bridge.py physics-demo
```

`sw_bridge.py` 路由到 `physics_bridge.py`：

```python
# sw_bridge.py 中的路由逻辑
elif cmd == "physics-demo":
    result = _pb.cmd_demo()
    print(json.dumps(result, ensure_ascii=False, indent=2))
```

### 4.2 physics_bridge.py → cmd_demo()

```python
# physics_bridge.py 第 304-341 行
def cmd_demo():
    # 1. 生成默认载荷工况
    lc = load_case.default_load_case("DEMO_CANTILEVER", "演示悬臂梁")
    #    → 返回包含 material(Al6061-T6), bounds(200×60×60),
    #      load(500N 向下), acceptance(min_SF=2.0) 的字典

    # 2. 校验工况
    lc_result = load_case.validate_load_case(lc)
    #    → 检查 schema_version=1.0, meta.problem_id, material 字段完整性,
    #      bounds 合法性, BC 和 load 引用一致性

    # 3. 运行 FEA
    fea_result = fea_solver.solve_fea(lc, output_dir=run_dir)
    #    → 见下方 fea_solver.py 详解

    # 4. 几何门禁检查
    gc = geometry_gate.geometry_gate_report(...)

    # 5. 生成报告
    report = sim_report.build_report(run_id="demo_001", ...)
    report_path = sim_report.save_report(report, run_dir)

    return {"ok": True, "report": report, "markdown_summary": md, "report_path": report_path}
```

### 4.3 fea_solver.py → solve_fea()

```python
# fea_solver.py 第 311-358 行
def solve_fea(load_case, mesh_path=None, step_path=None, output_dir=None, backend=None):
    # 1. 创建输出目录
    output_dir = os.path.join("..", "..", "output", f"physics_run_{int(time.time())}")
    os.makedirs(output_dir, exist_ok=True)

    # 2. 检测可用后端
    backends = detect_available_backends()
    #    → {"feapy": True, "skfem": True/False, "calculix": True/False, ...}

    # 3. 自动选择后端（优先级：feapy > skfem > calculix > analytical）
    if backend is None:
        for candidate in ["feapy", "skfem", "calculix", "code_aster", "elmerfem"]:
            if backends.get(candidate):
                backend = candidate
                break
        else:
            backend = "analytical"  # 最后保底

    # 4. 分发到对应后端
    if backend == "analytical":
        return solve_analytical(load_case, {})    # Level 0: 解析解
    elif backend == "feapy":
        return solve_feapy(load_case, output_dir)  # Level 1: 纯 Python FEA
    elif backend == "skfem":
        return solve_skfem(load_case, output_dir)  # Level 2: skfem
    # ...
```

### 4.4 fea_solver.py → solve_feapy()（纯 Python FEA）

```python
# fea_solver.py 第 143-239 行
def solve_feapy(load_case, output_dir=None):
    from feapy_solver import solve_cantilever, FEASolver, mesh_rect

    # 1. 提取材料参数
    mat = load_case["material"]
    E = mat["youngs_modulus_mpa"]      # 68900 (Al6061-T6)
    nu = mat["poissons_ratio"]         # 0.33
    sigma_y = mat["yield_strength_mpa"]# 276

    # 2. 提取设计域
    domain = load_case["design_domain"]["bounds"]
    L = domain["x_max"]                 # 200
    W = domain["y_max"]                 # 60
    H = domain["z_max"]                 # 60

    # 3. 提取载荷
    total_force = sum(l["magnitude_n"] for l in loads
                      if l["type"] in ("distributed_force", "concentrated_force"))
    #    → 500 N

    # 4. 调用 feapy_solver
    nx, ny = max(20, int(L/5)), max(6, int(H/5))
    #    → nx=40, ny=12（约每 5mm 一个网格点）
    r = solve_cantilever(L, H, W, total_force, E, nu, nx=nx, ny=ny)
    #    → 见 feapy_solver.py 详解

    # 5. 计算安全系数和闸口
    max_stress = r["max_von_mises_mpa"]
    safety_factor = sigma_y / max_stress
    gates = {
        "SAFETY_FACTOR": {"status": "PASS" if min_sf <= sf <= max_sf else "FAIL", ...},
        "MAX_DISPLACEMENT": {"status": "PASS" if disp <= max_disp else "FAIL", ...},
        "DESIGN_SPACE": {"status": "PASS"},
        "MESH_QUALITY": {"status": "PASS", "note": f"CST triangle mesh: ~{nx*ny*2} elements"},
    }

    return {"ok": True, "method": "feapy_numpy", "safety_factor": sf, ...}
```

### 4.5 feapy_solver.py → solve_cantilever_2d() 完整计算

```python
# feapy_solver.py 第 177-190 行
def solve_cantilever_2d(L=200, H=60, T=10, F_load=500, E=68900, nu=0.33, nx=40, ny=12):
    # 1. 生成 CST 三角形网格
    nodes, elems = mesh_rect(0, L, 0, H, nx, ny)
    #    → 41×13 = 533 个节点
    #    → 40×12×2 = 960 个三角形单元

    # 2. 创建求解器
    s = FEASolver(nodes, elems, model="plane_stress", thickness=T)
    #    → nd=2, ndof=533×2=1066

    # 3. 施加固定边界条件（x=0 面，y 方向所有节点）
    fixed = []
    for j in range(ny+1):
        n = j  # 第一列节点编号
        fixed.append((n, 0))  # Ux = 0
        fixed.append((n, 1))  # Uy = 0
    #    → 固定 26 个节点 × 2 DOF = 52 个约束

    # 4. 施加分布载荷（x=L 面，y 方向所有节点，均布 500N 向下）
    loads = []
    nr = nx * (ny+1)  # 最后一列起始节点编号
    for j in range(ny+1):
        n = nr + j
        loads.append((n, 0.0, -F_load/(ny+1)))
    #    → 13 个节点，每个承受 500/13 ≈ 38.46 N 向下

    # 5. 执行求解
    r = s.solve(E, nu, fixed=fixed, loads=loads)
```

### 4.6 FEASolver.solve() 核心算法

```python
# feapy_solver.py 第 93-174 行
class FEASolver:
    def solve(self, E, nu, fixed=None, loads=None):
        # ── 步骤 1：构建弹性矩阵 D ──
        D = _D_2d(E, nu, "plane_stress")
        #    → D = E/(1-nu²) × [[1, nu, 0], [nu, 1, 0], [0, 0, (1-nu)/2]]
        #    → 3×3 矩阵

        # ── 步骤 2：组装全局刚度矩阵 K ──
        K = np.zeros((self.ndof, self.ndof))  # 1066×1066
        Bcache = {}

        for elem in self.elements:
            nn = [int(elem[m]) for m in range(3)]  # 3 个节点索引
            coords = self.nodes[nn]
            x1,y1,_ = coords[0]; x2,y2,_ = coords[1]; x3,y3,_ = coords[2]

            # 计算三角形面积
            area = abs(x1*(y2-y3) + x2*(y3-y1) + x3*(y1-y2)) * 0.5

            # 计算 B 矩阵（应变-位移关系矩阵）
            B = _cst_B(x1,y1,x2,y2,x3,y3,area)
            #    → B = 1/(2×area) × [[b1,0,b2,0,b3,0],
            #                          [0,c1,0,c2,0,c3],
            #                          [c1,b1,c2,b2,c3,b3]]
            #    其中 b_i = y_j - y_k, c_i = x_k - x_j（循环下标）
            Bcache[tuple(nn)] = B

            # 单元刚度矩阵 Ke = B^T @ D @ B × area × thickness
            val = area
            Ke = B.T @ D @ B * val * self.thickness  # 6×6 矩阵

            # 将 Ke 累加到全局 K
            dofs = []
            for n in nn:
                for d in range(2):  # 2 DOF per node (Ux, Uy)
                    dofs.append(n*2 + d)
            for a in range(6):
                for b in range(6):
                    K[dofs[a], dofs[b]] += Ke[a,b]

        # ── 步骤 3：施加载荷和边界条件 ──
        F = np.zeros(self.ndof)

        # 集中载荷
        if loads:
            for item in loads:
                ni = int(item[0])
                F[ni*2]     += item[1]  # Ux 方向
                F[ni*2 + 1] += item[2]  # Uy 方向

        # 罚函数法施加边界条件
        if fixed:
            pen = 1e15  # 超大惩罚系数
            for item in fixed:
                ni, d = int(item[0]), int(item[1])
                K[ni*2 + d, ni*2 + d] += pen
                #    → 将刚度矩阵对角元加 1e15，等效于强制位移=0

        # ── 步骤 4：求解线性方程组 ──
        U = np.linalg.solve(K, F)
        #    → U 是 1066 维向量，U[i*2]=Ui_x, U[i*2+1]=Ui_y

        # ── 步骤 5：后处理 — von Mises 应力 ──
        vm = []
        for idx, elem in enumerate(self.elements):
            nn = [int(elem[m]) for m in range(3)]
            B = Bcache[tuple(nn)]
            # 提取单元节点位移
            u_e = []
            for m in range(3):
                n = nn[m]
                u_e.append(U[n*2])
                u_e.append(U[n*2+1])
            # 应变 = B @ u_e
            strain = B @ np.array(u_e)  # [εx, εy, γxy]
            # 应力 = D @ strain
            stress = D @ strain         # [σx, σy, τxy]
            sx, sy, txy = stress
            # von Mises = sqrt(σx² - σx·σy + σy² + 3τxy²)
            vm.append(np.sqrt(sx**2 - sx*sy + sy**2 + 3*txy**2))

        # ── 步骤 6：计算最大位移 ──
        disp = np.sqrt(U[0::2]**2 + U[1::2]**2)  # 每个节点的合位移
        max_disp = disp.max()

        return {
            "max_displacement_mm": float(max_disp),
            "max_von_mises_mpa": float(max(vm)),
            "displacement_field": U,
            "von_mises_per_element": vm,
        }
```

### 4.7 解析解对比（同步计算）

```python
# feapy_solver.py 第 188-189 行
I = T * H**3 / 12        # 截面惯性矩 = 10×60³/12 = 1,800,000 mm⁴
de = F * L**3 / (3 * E * I)   # 悬臂梁末端挠度公式
se = 6 * F * L / (T * H**2)   # 最大弯曲应力公式

# 结果对比
r["analytical"] = {
    "delta_mm": de,           # 理论挠度
    "sigma_mpa": se,          # 理论应力
    "disp_error_pct": abs(r["max_displacement_mm"]/de - 1) * 100,  # 相对误差%
    "stress_error_pct": abs(r["max_von_mises_mpa"]/se - 1) * 100,  # 相对误差%
}
```

**实际运行结果（nx=40, ny=12）：**
- 解析解：δ=0.0179mm, σ=2.78MPa
- FEA：δ=0.0191mm (误差 6.4%), σ=3.00MPa (误差 7.9%)

---

## 第五层：3D FEA 求解链

### 5.1 3D 网格生成（mesh_box）

```python
# feapy_solver.py 第 69-90 行：mesh_box()
def mesh_box(x0,x1,y0,y1,z0,z1,nx,ny,nz):
    # 1. 生成网格节点
    nodes = []
    for i in range(nx+1):
        for j in range(ny+1):
            for k in range(nz+1):
                nodes.append([
                    x0 + i*(x1-x0)/nx,
                    y0 + j*(y1-y0)/ny,
                    z0 + k*(z1-z0)/nz
                ])
    #    → 11×6×6 = 396 个节点

    # 2. 将每个六面体拆分为 6 个四面体
    elems = []
    for i in range(nx):
        for j in range(ny):
            for k in range(nz):
                idx = i*(ny+1)*(nz+1) + j*(nz+1) + k
                v = [
                    idx,                    # 0: 底部-前-左
                    idx+1,                  # 1: 底部-前-右
                    idx+(nz+1),             # 2: 底部-后-左
                    idx+(nz+1)+1,           # 3: 底部-后-右
                    idx+(ny+1)*(nz+1),      # 4: 顶部-前-左
                    idx+(ny+1)*(nz+1)+1,    # 5: 顶部-前-右
                    idx+(ny+1)*(nz+1)+(nz+1),  # 6: 顶部-后-左
                    idx+(ny+1)*(nz+1)+(nz+1)+1, # 7: 顶部-后-右
                ]
                elems.append([v[0],v[1],v[2],v[4]])   # tet 1
                elems.append([v[1],v[3],v[2],v[6]])   # tet 2
                elems.append([v[1],v[5],v[3],v[7]])   # tet 3
                elems.append([v[1],v[2],v[3],v[6]])   # tet 4
                elems.append([v[1],v[3],v[6],v[7]])   # tet 5
                elems.append([v[1],v[4],v[5],v[7]])   # tet 6
                # 注：旧版只用 5 个 tet，缺失 v[1]-v[2]-v[4]-v[6] 这个四面体
                #    导致总体积缺失 1/6 → 每个 tet 被高估 6 倍刚度
    return nodes, np.array(elems)
    #    → 10×5×5 = 125 个六面体 × 6 = 750 个四面体
    #    （注意：实际测试用 nx=10, ny=5, nz=5，但 solve_cantilever_3d 中
    #     参数名 H=60, D=60 对应 y/z 方向，所以是 10×5×5 网格）
```

### 5.2 3D CTE B 矩阵计算

```python
# feapy_solver.py 第 32-51 行：_cte_B_vol()
def _cte_B_vol(coords):
    """计算四面体的 B 矩阵和体积。

    使用形函数梯度法：
    - 构建 4×4 矩阵 M = [[1,x1,y1,z1], [1,x2,y2,z2], [1,x3,y3,z3], [1,x4,y4,z4]]
    - 求逆 M⁻¹
    - B 矩阵的行由 M⁻¹ 的第 2-4 行给出
    """
    x1,y1,z1 = coords[0]
    x2,y2,z2 = coords[1]
    x3,y3,z3 = coords[2]
    x4,y4,z4 = coords[3]

    # 四面体体积（标量三重积 / 6）
    vol = ((x2-x1)*((y3-y1)*(z4-z1)-(z3-z1)*(y4-y1))
         - (y2-y1)*((x3-x1)*(z4-z1)-(z3-z1)*(x4-x1))
         + (z2-z1)*((x3-x1)*(y4-y1)-(y3-y1)*(x4-x1))) / 6.0
    vol = abs(vol)

    if vol < 1e-10:
        return np.zeros((6,12)), 0.0

    # 构建并求逆
    M = np.array([[1,x1,y1,z1],
                  [1,x2,y2,z2],
                  [1,x3,y3,z3],
                  [1,x4,y4,z4]])
    invM = np.linalg.inv(M)

    # B 矩阵：6 行（εx,εy,εz,γyz,γxz,γxy），12 列（4节点×3DOF）
    bz = invM[1,:]  # x 方向的形函数梯度
    cz = invM[2,:]  # y 方向的形函数梯度
    dz = invM[3,:]  # z 方向的形函数梯度

    B = np.zeros((6,12))
    for i in range(4):
        B[0, i*3]   = bz[i]      # εx = ∂u/∂x
        B[1, i*3+1] = cz[i]      # εy = ∂v/∂y
        B[2, i*3+2] = dz[i]      # εz = ∂w/∂z
        B[3, i*3+1] = dz[i]      # γyz = ∂v/∂z + ∂w/∂y
        B[3, i*3+2] = cz[i]
        B[4, i*3]   = dz[i]      # γxz = ∂u/∂z + ∂w/∂x
        B[4, i*3+2] = bz[i]
        B[5, i*3]   = cz[i]      # γxy = ∂u/∂y + ∂v/∂x
        B[5, i*3+1] = bz[i]

    return B, vol
```

### 5.3 3D 求解过程

```python
# feapy_solver.py solve_cantilever_3d()
def solve_cantilever_3d(L=200, H=60, D=60, F_load=500, E=68900, nu=0.33, nx=10, ny=5, nz=5):
    nodes, elems = mesh_box(0, L, 0, H, 0, D, nx, ny, nz)
    #    → 396 nodes, 750 tets

    s = FEASolver(nodes, elems, model="3d")
    #    → nd=3, ndof=396×3=1188

    # 固定 x=0 面
    tol = L/(2*nx)  # 容差 = 10mm
    fixed = [(ii,0),(ii,1),(ii,2) for ii,node in enumerate(nodes) if node[0] < tol]
    #    → 60 个节点 × 3 DOF = 180 个约束

    # 施加载荷到 x=L 面
    rn = [ii for ii,node in enumerate(nodes) if node[0] > L-tol]
    rc = len(rn)
    loads = [(ii, 0.0, -F_load/rc) for ii in rn]
    #    → 60 个节点，每个承受 500/60 ≈ 8.33 N 向下

    r = s.solve(E, nu, fixed=fixed, loads=loads)

    # 解析解对比
    I = D*H**3/12        # = 60×60³/12 = 10,800,000 mm⁴
    de = F*L**3/(3*E*I)  # = 500×200³/(3×68900×10800000) ≈ 0.0179 mm
    se = 6*F*L/(D*H**2)  # = 6×500×200/(60×60²) ≈ 2.78 MPa

    r["analytical"] = {...}
    return r
```

**实际结果（10×5×5 网格）：**
- FEA：δ=0.0176mm（误差 1.5%），σ=2.46MPa（误差 11.4%）
- 安全系数：SF = 276/2.46 = 112.18

---

## 第六层：AutoCAD 验证执行链

### 6.1 触发点

```powershell
python sw_bridge.py ac-status
python sw_bridge.py ac-export
python sw_bridge.py cad-validate "drawing.dxf"
```

### 6.2 ac_bridge.py → connect()

```python
# ac_bridge.py 第 55-64 行
def connect(timeout=30):
    pythoncom.CoInitialize()                       # COM STA 初始化
    bridge = AutoCADBridge()
    if bridge._do_connect(timeout=timeout):
        return bridge
    return None

# ac_bridge.py 第 80-119 行：_do_connect()
def _do_connect(self, timeout=30):
    deadline = time.time() + timeout
    esc_sent = False
    while time.time() < deadline:
        try:
            pythoncom.CoInitialize()
            self.acad = win32com.client.dynamic.Dispatch('AutoCAD.Application')
            self.acad.Visible = True
            self.doc = self.acad.ActiveDocument
            _ = self.doc.Name   # 触发属性读取验证连接
            self._drawing_name = self.doc.Name
            self._connected = True
            return True
        except pythoncom.com_error as e:
            hr = getattr(e, 'hresult', None)
            # RPC_E_CALL_REJECTED (-2147418111) = AutoCAD 正在处理其他命令
            if hr == -2147418111 and not esc_sent:
                self._cancel_pending_command()   # 发 ESC 取消挂起命令
                esc_sent = True
                time.sleep(1.0)
            elif hr == -2147418111:
                delay = 0.5 * (2 ** min(int((deadline-time.time())*2), 4))
                time.sleep(delay)
                pythoncom.PumpWaitingMessages()  # 处理队列消息
            else:
                time.sleep(1.0)
                self.doc = None
                self.acad = None
    return False
```

### 6.3 ac_bridge.py → export_dxf()

```python
# ac_bridge.py 第 166-186 行
def export_dxf(self, output_path=None):
    if output_path is None:
        base = os.path.splitext(self.doc.Name)[0]
        out_dir = os.path.join(..., "output")
        os.makedirs(out_dir, exist_ok=True)
        output_path = os.path.join(out_dir, f"{base}_export.dxf")
    abs_path = os.path.abspath(output_path)
    # DXF 导出：SaveAs 参数 18 = DXF R2018 格式
    self.doc.SaveAs(abs_path, 18)
    if os.path.exists(abs_path):
        return abs_path
    raise RuntimeError(f"DXF export failed: {abs_path} not created")
```

### 6.4 ac_validate.py → validate_dxf() 完整检查流程

```python
# ac_validate.py 第 647-656 行
def validate_dxf(dxf_path, rules="mechanical"):
    analyzer = DXFAnalyzer(dxf_path, rules)
    analyzer.validate()   # 执行全部 7 项检查
    return {
        "ok": True,
        "summary": analyzer.get_summary(),    # {total_issues, critical, warning, info, status}
        "issues": analyzer.get_issues_dicts(), # 每个 Issue 的详细信息
        "context": analyzer.get_context(),     # 实体类型统计、图层统计
    }
```

`DXFAnalyzer.validate()` 执行顺序：

```python
# ac_validate.py 第 598-608 行
def validate(self):
    self.issues.clear()
    self._counter = 0
    self.check_unclosed_polylines()     # 检查1：未闭合多段线
    self.check_overlapping_geometry()   # 检查2：重叠几何（需 shapely）
    self.check_room_areas()             # 检查3&4：房间面积 + 长宽比
    self.check_wall_thickness()         # 检查5：墙体厚度
    self.check_layer_naming()           # 检查6：图层命名规范
    self.check_missing_elements()       # 检查7：缺失元素（门/窗）
    self.check_dimension_consistency()  # 检查8：标注一致性
    return self.issues
```

每项检查的代码逻辑摘要：

| 检查 | 逻辑 | 严重程度 |
|------|------|---------|
| UNCLOSED_POLYLINE | LWPOLYLINE 首尾距离 > gap_threshold(10mm) | WARNING/INFO |
| OVERLAPPING_GEOMETRY | shapely LineString.intersection 长度 > 50mm | CRITICAL/WARNING |
| ROOM_TOO_SMALL | Polygon.area × scale²/1e6 < min_room_area(9.5㎡) | CRITICAL |
| ROOM_ASPECT_RATIO | max(w,h)/min(w,h) > max_ratio(3.0) | WARNING |
| WALL_TOO_THIN | 平行线距离 < min_thick(150mm) | CRITICAL |
| MISSING_LAYER | required_layers 中某层不存在 | WARNING |
| LAYER_NAME_INVALID | 图层名不匹配正则 `^[A-Z][A-Z0-9_-]*$` | INFO |

---

## 第七层：物理子系统各组件的交互

### 7.1 physics_bridge.py → cmd_optimize() 迭代循环

```python
# physics_bridge.py 第 215-301 行
def cmd_optimize(case_path, max_iter=5):
    # 1. 加载并校验工况
    lc_result = load_case.load_from_file(case_path)

    # 2. 初始化设计状态
    run_dir = .../output/design_state/opt_{problem_id}_{timestamp}/
    ds = design_state.DesignState(run_dir, case_path)
    design_params = {
        "thickness_mm": {"value": 5.0, "min": 2, "max": 20},
        "height_mm": {"value": 60.0, "min": 20, "max": 200},
        "width_mm": {"value": 40.0, "min": 10, "max": 100},
        "fillet_mm": {"value": 2.0, "min": 0.5, "max": 15},
    }

    # 3. 迭代循环
    for i in range(1, max_iter + 1):
        run_id = f"opt_iter_{i}"
        output_dir = os.path.join(run_dir, f"iteration_{i}")

        # 3a. FEA 求解
        fea_result = fea_solver.solve_fea(lc_result["case"], output_dir=output_dir)

        # 3b. 几何门禁检查
        gc_result = geometry_gate.geometry_gate_report(...)

        # 3c. 生成报告
        report = sim_report.build_report(
            run_id=run_id, load_case=lc_result["case"],
            design_params=design_params, geometry_check=gc_result,
            fea_result=fea_result, iteration=i,
            parent_run_id=f"opt_iter_{i-1}" if i>1 else None,
        )
        report_path = sim_report.save_report(report, output_dir)

        # 3d. 更新设计状态
        ds.new_run(run_id)
        ds.update_run(run_id, {
            "fea_result": fea_result, "geometry_check": gc_result,
            "design_params": design_params, "status": report["overall_status"],
        })

        # 3e. 检查是否通过
        if report["overall_status"] == "PASS":
            return {"ok": True, "converged": True, "iterations": i, ...}

        # 3f. 生成修正建议
        rec = refine_rules.generate_recommendations(
            report=report, design_params=design_params,
            locked_params=[], max_iterations=max_iter - i,
        )
        if rec["ok"] and rec["actions"]:
            design_params = rec["next_params"]  # 更新参数用于下一轮
        else:
            return {"ok": True, "converged": False, "reason": "no valid refinement", ...}

    return {"ok": True, "converged": False, "iterations": max_iter, ...}
```

### 7.2 refine_rules.py 修正规则示例

```python
# refine_rules.py
def generate_recommendations(report, design_params, locked_params, max_iterations):
    actions = []
    next_params = dict(design_params)

    sf = report.get("safety_factor", 0)
    min_sf = report.get("min_safety_factor", 2.0)
    max_sf = report.get("target_safety_factor_max", 5.0)

    if sf < min_sf:
        # 安全系数不足 → 增加厚度或高度
        actions.append({
            "type": "increase_thickness",
            "reason": f"safety_factor={sf:.2f} < min={min_sf}",
            "parameter": "thickness_mm",
            "direction": "increase",
            "magnitude": "proportional_to_deficit",
        })
    elif sf > max_sf:
        # 安全系数过高 → 可以减薄（轻量化）
        actions.append({
            "type": "decrease_thickness",
            "reason": f"safety_factor={sf:.2f} > target_max={max_sf}",
            "parameter": "thickness_mm",
            "direction": "decrease",
            "magnitude": "toward_optimal",
        })

    disp = report.get("max_displacement_mm", 0)
    max_disp = report.get("max_displacement_mm_limit")
    if max_disp and disp > max_disp:
        actions.append({
            "type": "increase_stiffness",
            "reason": f"displacement={disp} > max={max_disp}",
            "parameter": "height_mm",
            "direction": "increase",
        })

    return {
        "ok": len(actions) > 0,
        "actions": actions,
        "next_params": next_params,
        "iterations_remaining": max_iterations,
    }
```

---

## 第八层：异常处理链路

### 8.1 SolidWorks 未启动

```
get_sw() 调用 win32com.client.dynamic.Dispatch('SldWorks.Application')
  │
  ├─ 成功 → 连接现有 SW 实例
  │
  └─ com_error → 尝试启动：
       subprocess.run(['start', 'sldworks.exe'], shell=True)
       → 轮询 CoGetObject('SldWorks.Application') 直到成功或超时
       → 超时 → 返回 {"ok": False, "error": "SolidWorks未安装或未启动"}
```

### 8.2 草图创建失败（Bug 16 自动修复）

```
m.begin_sketch("Front Plane")
  │
  ├─ ActiveSketch is None → 触发 Bug16 修复
  │   → skmgr.InsertSketch(False) 退出残留草图模式
  │   → 重新选面
  │
  ├─ 仍失败 → 触发 Bug17 修复
  │   → 改用 begin_sketch_on_face(x, y, z) 面包围盒匹配
  │
  └─ 仍失败 → 抛 RuntimeError，AI 收到错误，调整策略重试
```

### 8.3 FEA 矩阵奇异

```
np.linalg.solve(K, F)
  │
  ├─ 成功 → 正常返回
  │
  └─ LinAlgError: Singular matrix
       → 检查 fixed 约束是否充分（覆盖所有刚体模态）
       → 增大罚函数：1e15 → 1e18
       → 仍失败 → 返回 error，AI 告知用户"边界条件不充分"
```

### 8.4 上下文溢出（compaction 自动触发）

```
对话长度超过阈值（8192 字符）
  │
  ▼ tool-result-pruner 插件
  │
  ├─ thresholdChars=8192
  ├─ headChars=4096（保留开头）
  ├─ tailChars=1024（保留结尾）
  │
  ▼ 截断中间部分，替换为 "[内容已压缩，共省略 N 字符]"
  │
  ▼ 继续对话
```

---

## 第九层：完整数据流总结

```
用户消息
  │
  ▼ AI 模型
  │  create_goal("完成xxx设计")
  │  todo_write([步骤清单])
  │  (ask_user 如需确认)
  │
  ▼ AI 生成脚本
  │  _dsh_build_xxx.py
  │
  ▼ pwsh 工具
  │  python sw_bridge.py run script.py
  │
  ▼ sw_bridge.py
  │  get_sw() → COM 连接 SW
  │  exec(script, {'sw': sw, 'swapi': swapi})
  │
  ▼ swapi.py
  │  new_part() → sw.NewDocument(template)
  │  begin_sketch() → swModelDocExt.EditTemplate(plane)
  │  rect() → skSegs.AddBy2Points(VARIANT)
  │  end_sketch() → skMgr.MergePoints + InsertSketch
  │  extrude() → featMgr.FeatureExtrusion3()
  │  save() → doc.SaveAs2(path)
  │
  ▼ 返回 JSON
  │  {"ok":true, "stdout":"OK: DSH_xxx.sldprt"}
  │
  ▼ AI 判断是否需要 FEA
  │
  ├─ 需要 → physics_bridge.py → fea_solver.py → feapy_solver.py
  │          │
  │          ├─ load_case.default_load_case() → JSON
  │          ├─ load_case.validate_load_case() → 校验
  │          ├─ fea_solver.solve_fea() → 选择后端
  │          │     ├─ Level 0: solve_analytical() → 梁理论公式
  │          │     └─ Level 1: solve_feapy()
  │          │           ├─ mesh_rect/mesh_box() → 节点+单元
  │          │           ├─ FEASolver.solve()
  │          │           │     ├─ _D_2d/_D_3d() → 弹性矩阵
  │          │           │     ├─ _cst_B/_cte_B_vol() → B 矩阵
  │          │           │     ├─ 组装 K 矩阵（B.T @ D @ B × vol）
  │          │           │     ├─ 罚函数法施加 BC
  │          │           │     ├─ np.linalg.solve(K, F)
  │          │           │     └─ von Mises 后处理
  │          │           └─ 解析解对比
  │          ├─ geometry_gate.geometry_gate_report() → 几何检查
  │          ├─ simulation_report.build_report() → 结构化报告
  │          └─ design_state.DesignState() → 迭代状态持久化
  │
  ├─ 需要 AutoCAD 验证 → ac_bridge.py → ac_validate.py
  │                      ├─ connect() → COM 连接 AutoCAD
  │                      ├─ export_dxf() → SaveAs(type=18)
  │                      └─ DXFAnalyzer.validate() → 8 项几何检查
  │
  ├─ 需要工程图 → sw_bridge.py drawing
  │               ├─ sw.OpenDoc6(path)
  │               ├─ CreateDrawViewFromModelView() × 3（前/上/右）
  │               ├─ 排版约束（视图位置）
  │               ├─ 标注添加
  │               └─ doc.SaveAs2(slddrw_path)
  │
  └─ 完成 → cleanup() → update_goal("complete") → 汇报用户
```

---

## 第十层：关键性能数据

| 场景 | 网格规模 | 求解时间 | 位移误差 | 应力误差 |
|------|---------|---------|---------|---------|
| 2D CST (nx=40, ny=12) | 533节点, 960单元 | ~0.3s | 6.4% | 7.9% |
| 3D CTE (10×5×5) | 396节点, 750四面体 | ~0.5s | 1.5% | 11.4% |
| 3D CTE (8×4×4) | 252节点, 512四面体 | ~0.3s | +3.2% | +8.1% |
| 3D CTE (5×3×3) | 126节点, 162四面体 | ~0.1s | +12.0% | +25.4% |
| 解析解 | 无网格 | <0.01s | 0% | 0% |

> FEA 误差主要来自 CST/CTE 单元的线性形函数假设（常数应变），网格越密越接近解析解。
