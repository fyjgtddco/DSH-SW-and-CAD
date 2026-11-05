# A1–A3 接口任务书（交给实现方）

> 项目：`C:/Users/j1877/Desktop/DSH-SW-and-CAD-main/`
> 目标读者：负责编写 SW 几何导出 / 真实几何 FEA / SW Simulation 接口的实现者
> 编写者已核对当前源码与环境（2026-11-05）。**本文所有"现状"陈述均已实测确认，请勿凭想象改写。**

---

## 第 0 部分 先读这一节，否则写出来一定跑不通

### 0.1 环境事实（已实测）

| 项 | 实测结果 | 影响 |
|---|---|---|
| Python | **3.13.15**（`C:\Users\j1877\AppData\Local\Programs\Python\Python313\python.exe`） | 可用 3.13 语法，但**不要引入 3.14 新特性** |
| numpy | ✅ 已装 | |
| **gmsh** | ✅ 已装，**4.15.2**（`Scripts\gmsh.bat`） | A2 可以直接跑 |
| **CalculiX / ccx** | ❌ **未安装**（全盘无 ccx.exe） | A2 必须"检测不到就如实报告 UNSUPPORTED"，**不许假装能算** |
| scipy | ❌ 未装 | **不能用 scipy**，矩阵运算只能用 numpy |
| meshio | ❌ 未装 | **不能依赖 meshio 读网格**，要么让 gmsh 直接写 .inp，要么自己解析 |
| pywin32 / win32com | ✅ 已装 | |
| ezdxf / shapely / Pillow | ✅ 已装 | |
| SolidWorks | 2018–2025，经 COM | 单实例，**任何 SW 调用必须走现有 FIFO 锁** |

### 0.2 六条硬性约束（违反任意一条，代码一律不接受）

1. **不许吞异常。** 每个接口统一返回 `{ok: bool, error: str, ...}`。**禁止** `except Exception: pass`。
   本项目历史上因为宽泛 except 吞掉异常，导致 "包围盒恒为 None → FEA 悄悄退回默认尺寸 → 安全系数与载荷无关"（Bug17/Bug18）。
2. **不许静默降级。** 真实求解器失败，**不得**自动退回等效梁模型并沿用 PASS。必须显式返回 `UNSUPPORTED` / `UNKNOWN` 并写明原因。
3. **不许伪造数据。** 拿不到真实几何、真实材料、真实网格，就返回失败并说明缺什么。禁止用编造值凑一个能过的结论。
4. **只新增，不重写门禁。** 不要修改 `defense_gate.py`、`workflow_gate.py` 的判定逻辑，不要动 `SOLVER_BACKENDS` 的检测方式，不要放宽任何 threshold。
5. **SW COM 三条必踩之坑**（本项目已用血换来的）：
   - `model.GetPathName` 是**属性不是方法**，加括号会抛 `'str' object is not callable`；
   - `OpenDoc6` 必须传满 **6 个参数**，后两个是 VARIANT；直接复用 `swapi.SWModel.open_part(sw, path, doc_type=1)`，**不要自己写**；
   - `SaveAs3` 的返回值**两个方向都不可信**（可能 rc=0 但没写盘，也可能 rc≠0 但写成功了）。**判成败一律看磁盘指纹（size + mtime + sha256）**，见 `swapi.py:14492 save_as()` 的既有实现，直接复用。
6. **路径必须用 Windows 原生反斜杠且做 `os.path.abspath` 归一化。** 宿主签名服务按**字符串包含关系**判断报告是否落在受管目录内，带 `..` 的路径会被拒签（BUG-PHYS-RPTPATH-1）。

### 0.3 代码风格要求

- 中文注释写**为什么**，不要写"这行做了什么"；
- 每个"看起来多余"的防御性代码，必须在注释里写明它在防哪个已知缺陷；
- 函数返回值里凡是"推断/兜底/估算"得来的值，必须带 `source` 字段说明来源，**不得与实测值混同**。

---

## A1 · SolidWorks 几何导出（STEP / STL）

### A1.1 现状（已核实，不要重复劳动）

```
engineering/tools/swapi.py:14492   save_as(self, path)          ← 已有，底层能力完整
engineering/tools/swapi.py:14521   _EXT_FMT = {".step":17, ".stp":17, ".igs":9,
                                              ".iges":9, ".stl":0, ...}
```

**关键事实：**

- 格式枚举表**已经存在**，但 ⚠️ **它只在 SaveAs3 抛异常、回退 `Extension.SaveAs` 时才被用到**。正常路径是 `self.model.SaveAs3(path, 0, 2)`，靠扩展名自动推断格式。
- **没有任何 CLI 命令**：`sw_bridge.py` 里搜不到 step/stl/iges 命令（只有 `export-pdf` 和 `save`）。
- **没有导出后回读验证**：不知道导出的 STEP 是不是当前几何、是空壳还是实体。

### A1.2 要写什么

**① `swapi.SWModel.export_geometry(self, out_path, model_type="part")`**

- 输入：零件/装配体已打开，`out_path` 带目标扩展名（`.step/.stp/.stl/.igs/.iges/x_t/.x_b`）；
- 内部：复用 `self.save_as(out_path)`，**不要另写一套 SaveAs 逻辑**；
- 导出后**必须做真实性校验**（见 ②）；
- 返回：`{ok, path, size_bytes, sha256, format, source_doc, exported_at, verified: {...}}`。

**② 导出真实性校验（这是本任务的核心价值，不是可选项）**

只判断"文件存在"是不够的。要求：

| 校验 | 做法 | 失败含义 |
|---|---|---|
| 文件非空 | `size_bytes > 1024` | 空壳 |
| 格式正确 | STEP：首行含 `ISO-10303-21`；STL：首 5 字节是 `solid` 或文件长度 = 84 + 50×n | 导出成了别的格式 |
| 实体数量 | STEP 里数 `MANIFOLD_SOLID_BREP` 出现次数；STL 按文件长度反推三角面数 | 导出成空几何 |
| 包围盒一致 | 从 STEP/STL 解析出的 bbox，与 `swapi.body_box_mm()` 的实测值比对，误差 ≤ 1mm | 导出的是旧版本/别的零件 |

**任何一项失败 → `ok: false`，不许"先返回成功让下游试试"。**

**③ CLI 命令（加进 `sw_bridge.py`）**

沿用现有风格（`drawing` / `dwg` / `dxf` 都是这种写法）：

```powershell
python sw_bridge.py export-step <零件.sldprt|装配.sldasm> [--out <目标路径>]
python sw_bridge.py export-stl  <零件.sldprt|装配.sldasm> [--out <目标路径>] [--tolerance 0.1]
python sw_bridge.py export-geo  <文件> --format step|stl|iges   # 统一入口（可选）
```

- 默认输出路径：与源文件**同目录同名**，扩展名替换；
- **必须把 `export-step`/`export-stl`/`export-geo` 加进 `sw_bridge.py:7420` 的 `_ROOM_REQUIRED_CMDS`**（它们要抢 SW 独占权并写文件，不加会破坏并行锁语义）；
- 命令注册位置：`sw_bridge.py` 的 `elif cmd == ...` 分发表（约 7620–7710 行区间），和 `export-pdf` 相邻；
- **不要**让它依赖 `_mode2_preflight_for_run` 那套建模门禁（导出不是建模）。

**④ 导出清单（供 A2 / Webots 消费）**

每次导出写一份 `<零件名>.geometry.json` 到零件同目录：

```json
{
  "schema_version": "1.0",
  "source_file": "C:\\...\\DSH_大臂.SLDPRT",
  "source_sha256": "...",
  "exported_at": "2026-11-05T18:50:00+08:00",
  "exports": {
    "step": {"path": "...", "sha256": "...", "size_bytes": 0,
             "bbox_mm": {"x": 0, "y": 0, "z": 0},
             "solid_count": 1, "verified": true},
    "stl":  {"path": "...", "sha256": "...", "triangle_count": 0,
             "bbox_mm": {...}, "verified": true}
  },
  "verification": {"bbox_matches_body_box": true,
                   "bbox_error_mm": 0.0,
                   "failures": []}
}
```

### A1.3 必须通过的验收场景

1. 对一个已知尺寸的方块零件导出 STEP → 解析出的 bbox 与 `body_box_mm()` 误差 < 1mm；
2. 对**空零件**（无实体）导出 → `ok:false`，错误信息说明"无实体"；
3. 导出后立即改零件尺寸再导出 → 第二次的 sha256 与第一次不同，bbox 反映新尺寸；
4. 导出**装配体** `.sldasm` → `solid_count` 等于组件实体总数（或明确说明 STEP 是单壳合并）；
5. SW 未运行时调用 → 返回明确的"SW 不可用"，**不抛未捕获异常**。

---

## A2 · 真实几何 Gmsh + CalculiX 求解链

### A2.1 现状（已核实——这段必须先看清楚）

打开 `engineering/tools/physics/fea_solver.py`：

```python
# L1022  _write_cfx_input(step_path, load_case, output_dir)
#   实际写出来的内容是：
"NODE
1, 0.0, 0.0, 0.0
2, {x_max}, 0.0, 0.0
*ELEMENT, TYPE=C3D8R, ELSET=ALL
1, 1, 2, 3, 4, 5, 6, 7, 8      ← 只有 2 个节点，却引用 8 个节点！
"
# L1067  inp_path = _write_cfx_input(step_path, {}, output_dir)
#                                        ^^^^ 传的是空 dict！所有载荷/材料/边界全丢
# L1073  调用 "ccx -i <inp_path>"，但 inp_path 是【文件全路径】
#        而 ccx 的 -i 参数要的是【不含扩展名的 job 名】
# L1138  return run_calculix(mesh_path, mesh_path, output_dir)
#                               ^^^^^^^^^ 把 mesh 路径当 step 路径传
```

**结论：这整条链目前是三重坏掉的壳子：**
① 输入文件是硬编码假模型（节点数不够、单元引用越界）；
② 载荷工况根本没传进来（传的是 `{}`）；
③ ccx 的 `-i` 参数用法错误。

另外：

```
mesh_adapter.py:178  generate_mesh_gmsh()  —— 只有 merge(step) + generate(3)，
                                              没有物理分组、没有区域集合、没有局部细化
mesh_adapter.py:266  adapt_mesh()          —— 【从未被 physics_bridge 调用过】
                                              （全仓搜索确认：零调用点，是死代码）
fea_solver.py:1122   自动选后端顺序 = ["feapy", "calculix", ...]
                     但 feapy 恒为 True（纯 numpy 永远可用）
                     ⇒ calculix 分支【永远不会被自动选中】
```

### A2.2 要写什么

#### ① 打通 STEP → Gmsh（带物理分组）

重写 `mesh_adapter.generate_mesh_gmsh`，签名扩展为：

```python
def generate_mesh_gmsh(step_path: str, msh_path: str,
                       load_case: dict,          # ← 新增：用它做区域选择
                       order: int = 1,
                       mesh_size_mm: float = None,
                       refine_regions: list = None) -> dict:
    """返回 {ok, msh_path, num_nodes, num_elements, element_types,
             physical_groups: {name: {dim, tag, face_count, area_mm2}},
             mesh_stats: {...}, warnings: []}"""
```

**区域选择是这条链的核心难点。** `load_case` 里已经有现成结构（`physics/load_case.py:86`）：

```json
"spatial_selectors": [
  {"id": "fixed_end", "type": "box",
   "bounds": {"x_min":0,"x_max":10,"y_min":-10,"y_max":70,"z_min":-10,"z_max":70},
   "selection_rule": "faces_intersecting_region"}
]
```

映射方法（用 gmsh 的 OCC 内核）：

1. `gmsh.model.occ.importShapes(step_path)` 导入；
2. `gmsh.model.occ.synchronize()`；
3. 遍历所有 `dim=2` 的曲面，取每个面的包围盒（`gmsh.model.getBoundingBox(2, tag)`）；
4. 与 selector 的 box 做相交判定 → 选中的面进 `gmsh.model.addPhysicalGroup(2, faces)`，名字用 selector 的 `id`；
5. ⚠️ **选中为空必须报错**，不许随便补一张面（这是文档 3.5-5 明确要求）。返回 `{ok:false, error:"region 'fixed_end' 未命中任何面（几何可能已变更）"}`；
6. 网格生成：
   - `gmsh.option.setNumber("Mesh.MeshSizeMax", mesh_size_mm)`
   - 局部细化：`refine_regions` 传 `{selector_id: size_mm}`，在热点区加 `Mesh.MeshSizeMin`；
   - `gmsh.option.setNumber("Mesh.Order", order)`（1=线性，2=二次）
   - **注意：现有代码是先 `generate(3)` 后设 `Mesh.Order`，顺序反了，必须改。**
7. 输出：`gmsh.write(msh_path)`；
8. **同时导出 CalculiX 输入**：`gmsh.option.setNumber("Mesh.Format", 39); gmsh.write("mesh.inp")` —— gmsh 原生支持写 CalculiX 格式，这样就不需要 meshio。

#### ② 重写 `_write_cfx_input`（保留函数名，彻底换实现）

```python
def _write_cfx_input(mesh_inp_path: str, load_case: dict, output_dir: str,
                     analysis_type: str = "linear_static") -> dict:
    """在 gmsh 导出的 mesh.inp 基础上，追加材料/边界/载荷/求解步骤。
    返回 {ok, inp_path, sections_written: [...], error?}"""
```

**必须生成的段（顺序有讲究，CalculiX 要求特定次序）：**

```
*INCLUDE, INPUT=<gmsh 导出的 mesh.inp>     ← 节点/单元/ELSET 从这里来
*MATERIAL, NAME=MAT1
*ELASTIC
<E>, <nu>                                   ← 单位 N/mm²(=MPa)，直接可用
*DENSITY
<rho_tonne_mm3>                             ← ⚠️ 见下方单位陷阱
*SOLID SECTION, ELSET=EALL, MATERIAL=MAT1
*STEP, NAME=STEP-1, INC=100
*STATIC
*BOUNDARY
<NSET_FIXED>, 1, 3, 0.0                     ← 固定面
*LOAD
<NSET_LOAD>, 3, -<F_N>                      ← 集中力
*NODE FILE
U, RF
*EL FILE
S
*END STEP
```

**⚠️ 单位陷阱（文档 3.4 专门提醒过）**

本项目工况用 **N–mm–MPa** 制。这个单位制下：
- 长度 = mm，力 = N，应力 = MPa（= N/mm²），**可直接用**；
- **但密度的单位是 tonne/mm³**。钢 7850 kg/m³ = **7.85e-9 tonne/mm³**。
- **绝对不能把 `density_kg_m3`（7850）原样填进去** —— 会差 10⁹ 倍，重力载荷直接爆炸。

写一个显式转换函数并单测它：

```python
def _density_to_tonne_per_mm3(rho_kg_m3: float) -> float:
    """kg/m³ → tonne/mm³。1 kg = 1e-3 tonne，1 m³ = 1e9 mm³ ⇒ ÷1e12"""
    return float(rho_kg_m3) / 1.0e12
```

**边界/载荷的节点集**：从 gmsh 的 physical group 导出时，让 gmsh 同时写 `.msh` 里的 physical group，然后在 Python 侧用 `gmsh.model.mesh.getNodesForPhysicalGroup(2, tag)` 取节点号，生成 `*NSET`。不要靠坐标猜。

#### ③ 重写 `run_calculix`

```python
def run_calculix(inp_path: str, output_dir: str,
                 timeout_s: int = 600) -> dict:
    """真正启动 ccx。
    注意：ccx 的 -i 要的是【不含扩展名的 job 名】，不是全路径。
    Returns: {ok, exit_code, stdout, stderr, frd_path, dat_path,
              elapsed_s, backend_version}"""
```

- 工作目录切到 `output_dir`，`-i` 传 `model`（不带 .inp）；
- 捕获 `returncode`、`stdout`、`stderr` **全部落盘**（`ccx.log`）；
- 超时 / 奇异矩阵 / `*ERROR` 关键字 → 返回 `ok:false` + **明确分类**（`TIMEOUT` / `SINGULAR_MATRIX` / `NOT_CONVERGED` / `INPUT_INVALID`）；
- **未安装 ccx → `{ok:false, error:"CalculiX not installed", code:"BACKEND_UNAVAILABLE"}`**，不许假装成功。

#### ④ 结果解析（无 meshio，自己写）

写 `_parse_frd(frd_path)`：

- CalculiX 的 `.frd` 是固定格式文本，块以 ` -4` 开头、` -3` 结尾；
- 需要提取：`DISP`（位移 U）、`STRESS`（应力 S，6 分量）、`FORC`（反力 RF）；
- 输出：`{max_displacement_mm, max_von_mises_mpa, max_stress_location:[x,y,z], reaction_forces:{...}}`；
- **应力提取必须说明是节点值还是积分点值**（CalculiX 的 `*EL FILE` 给积分点，`*NODE FILE` 外推后才给节点值）。

#### ⑤ 数值有效性检查（求解后必做，缺一不可）

```python
def _check_numerical_validity(frd: dict, load_case: dict) -> dict:
    """返回 {valid: bool, checks:[{name, passed, detail}], failures: []}"""
```

| 检查 | 判据 | 失败含义 |
|---|---|---|
| 平衡 | Σ反力 + Σ外载荷 ≈ 0，**相对误差 < 1%**；但外力接近 0 时改用**绝对门限**（≤ 1e-6 N） | 模型/约束有问题 |
| 有限值 | 所有应力/位移非 NaN、非 inf | 求解发散 |
| 刚体位移 | 最大位移 < 零件尺寸 × 10 | 约束不足 |
| 位移合理性 | 0 < max_disp，且不在固定面上 | 边界条件错 |

**任一失败 → `numerical_validity: FAILED`，本次分析结论一律不得为 PASS。**

#### ⑥ 网格收敛（三档）

```python
def run_convergence_study(step_path, load_case, output_dir,
                          levels=(1.0, 0.6, 0.35)) -> dict:
    """三个网格尺寸档，关注量 = max_von_mises。
    收敛判据：相邻两档变化 < 5%（门限可配），且细化方向单调。
    Returns: {converged: bool, history: [...], final_mesh_size_mm, note}"""
```

⚠️ **奇异点识别**：如果峰值应力随网格加密无限上升（每档都涨 > 5%），必须**明确标注"该峰值位于应力奇异点，不可直接作为强度判据"**，而不是硬报 `converged: false` 就完事。

#### ⑦ `solve_fea` 的降级规则必须改

```python
# 现状（fea_solver.py:1122）
for candidate in ["feapy", "calculix", "code_aster", "elmerfem"]:
# feapy 恒 True ⇒ calculix 永远轮不到

# 要求：
#   1. 由 load_case 里的显式字段决定所需后端：
#      acceptance.require_real_geometry == True  ⇒ 必须用真实几何后端
#   2. 真实后端不可用 / 失败 ⇒ 返回
#      {ok:false, code:"REAL_GEOMETRY_UNAVAILABLE", required:"calculix",
#       fallback_used:false, hint:"..."}
#   3. 只有 load_case 里显式写了 allow_equivalent_model:true 才允许退回 feapy，
#      且退回后 report 里必须带
#      {model_equivalence:"equivalent_beam", conclusion_scope:"估算，非真实几何校核"}
```

**这是整套改造的安全底线：不许再出现"算不出来就退回悬臂梁，然后报告照样 PASS"。**

### A2.3 必须通过的验收场景

1. **同 bbox、不同拓扑**：一个实心块 200×60×10 和一个同样 bbox 但中间挖了大孔的零件 → 网格节点数、单元数必须不同，求解结果 max_stress 必须不同（差 > 20%）。**如果两者结果一样，说明根本没读真实几何。**
2. **区域选择落空**：把 `fixed_end` 的 box 挪到零件外面 → 启动前报错，不许跑。
3. **单位一致性**：同一个零件、同样载荷，分别用 N-mm 制和 SI 制各写一份工况，换算后结果量级一致（误差 < 1%）。
4. **求解失败**：故意给一个欠约束的模型 → `ok:false` + `code:"SINGULAR_MATRIX"`，**不许返回 PASS**。
5. **ccx 未安装**：本机现在就是这种情况 → `code:"BACKEND_UNAVAILABLE"`，测试应当**明确 skip 并报告缺口**，不许用假 COM / 假字符串测试冒充已通过。
6. **解析基准**：一个悬臂梁，用 CalculiX 算出的 max_disp 与梁理论 `FL³/(3EI)` 误差 < 5%（这是判断"这条链真的对的"最硬的标准）。

### A2.4 交付时必须说明

- 本机没装 ccx，**上面 1/3/4/6 号场景在本机无法真实跑通**，只能给出"代码已就绪、等安装后验证"的说明；
- 不要写"已实现并验证通过"——写"已实现，待安装 CalculiX 后按场景 X/Y/Z 验证"。

---

## A3 · SolidWorks Simulation API（可选第二后端）

### A3.1 现状（已核实）

```python
# mesh_adapter.py:229
def check_sw_simulation_available(sw_app) -> bool:
    sim_guid = "{E3C2B4A0-6A1F-4B5E-9D8F-1A2B3C4D5E6F}"  # ← 代码原文写着"占位符，实际需查询"
    sw_app.GetAddInClient2(sim_guid)
    ...
    sw_app.GetAddInClient2("{3B0FF6F0-4B6E-11D4-B5E3-00C04F68DDB5}")  # 另一个 GUID

# mesh_adapter.py:246
def create_simulation_study(...):
    return {"ok": False,
            "error": "SolidWorks Simulation API implementation pending; ..."}
```

**连"检测插件是否装了"这一步都是假的** —— GUID 是占位符，第一个调用的返回值还被 `try/except` 吞了。

### A3.2 要写什么

**① 先把"到底装没装"做真**

```python
def detect_simulation_addin(sw_app) -> dict:
    """返回 {available, addin_name, addin_guid, version, license_ok,
             evidence, error}
    方法（按可靠性排序，全都要试）：
      a. sw_app.GetAddInClient2 用【真实】Simulation GUID
      b. 遍历 sw_app.GetAddInCount / GetAddInName / GetAddInGUID
      c. 查注册表 HKLM\SOFTWARE\SolidWorks\Addins\<GUID>
    """
```

真实 GUID 有几个候选（**必须实地查注册表确认，不要照抄网上**）：

```
{3B0FF6F0-4B6E-11D4-B5E3-00C04F68DDB5}   ← 常见 Simulation/CosmosWorks
{7F1D6E2C-...}                          ← 各版本可能不同
```

**要求：** 写一个 `python sw_bridge.py sim-detect` 命令，把**本机实际查到的** GUID、版本、许可状态打印出来，落盘到 `reports/sim_detect.json`。**先有事实，再写实现。**

**② 真实实现 `create_simulation_study`**

SolidWorks Simulation 的 COM 对象链（概念上）：

```python
# 1. 拿到 Simulation 对象
addin = sw_app.GetAddInObject(<Simulation GUID>)
# 或 cw = sw_app.GetAddInClient2(guid)[1]

# 2. 研究管理
study_mgr = model.Extension.GetCosmosWorks()   # 或 addin.StudyManager
study = study_mgr.CreateNewStudy3(name, type, mesh_type, ...)

# 3. 材料
study.ApplyMaterialToAllBodies(...)  或 按 body 逐个

# 4. 约束与载荷
study.AddRestraint(...)      # 面/边选择 → 用 SelectByID2 或坐标选面
study.AddForce(...)

# 5. 网格
study.CreateMesh(0, mesh_size, tolerance, ...)

# 6. 求解
study.RunAnalysis()

# 7. 结果
results = study.GetResults()
results.GetMinMaxStress(...) / GetMaximumDisplacement(...)
```

**注意：不同 SW 版本 API 差异很大（2018 ~ 2025）。** 所以：

- 每个 API 调用都要**单独 try/except 并记录到 `api_log`**；
- 任何一步失败，返回**精确到步**的失败信息：
  `{ok:false, failed_at:"CreateNewStudy3", error:"...", api_log:[...]}`；
- **不许整体 try/except 后返回一个笼统的失败**（那样等于什么都没说）。

**③ 结果映射到项目既有的报告结构**

必须填的字段（与 `physics/simulation_report.py` 对齐）：

```json
{
  "backend": "sw_simulation",
  "backend_version": "SW2025 SP5.0",
  "execution_status": "SUCCEEDED | FAILED",
  "model_validity": "VALID | INVALID | UNKNOWN",
  "numerical_validity": "VERIFIED | FAILED | UNKNOWN",
  "design_verdict": "PASS | FAIL | UNKNOWN",
  "max_von_mises_mpa": 0.0,
  "max_displacement_mm": 0.0,
  "safety_factor": 0.0,
  "mesh": {"nodes": 0, "elements": 0, "element_type": "solid_tet4",
           "max_aspect_ratio": 0.0, "quality": 0.0},
  "gates": {"SAFETY_FACTOR": {...}, "MAX_DISPLACEMENT": {...}, ...}
}
```

⚠️ **闸口归属必须遵守现有 `GATE_OWNER` 表**（`simulation_report.py:652`）：

```
DESIGN_SPACE / CONNECTIVITY / WALL_THICKNESS  → geometry 层
SAFETY_FACTOR / MAX_DISPLACEMENT / FATIGUE / MESH_QUALITY → fea 层
```

**SW Simulation 属于 fea 层，不许越界去给几何层闸口下结论**（这是 Bug18 修复的核心，两层同时判定会产出互相矛盾的结论）。

**④ 与 SW 锁的关系（容易漏，漏了会撞车）**

SW Simulation 全程占用 SW 实例，**必须**：

- 进入前 `mode_gate.py sw-request <房间>` 拿独占锁；
- 求解期间**持续刷心跳**（一次 Simulation 求解可能几分钟，超过锁的容忍期会被判定僵死）；
- 结束后 `sw-release`；
- 失败/异常路径也要 release（`try/finally`）。

### A3.3 必须通过的验收场景

1. **未装 Simulation 的机器**：`detect_simulation_addin` 返回 `available:false`，且 `sw_bridge.py sim-detect` 能明确说出"查了哪些 GUID、注册表哪些键、结论是什么"；
2. **装了但无许可**：返回 `available:false, license_ok:false`，**不许假装能用**；
3. **分析失败**：能指出失败发生在哪一步 API；
4. **数值校验**：SW Simulation 算出来的结果，与 A2 的 CalculiX 对同一个零件、同一工况的结果，量级一致（差异 < 20%）—— 这是交叉验证。

---

## 附 · 三者关系与实施顺序

```
A1 (STEP/STL 导出)  ──┬──> A2 (Gmsh + CalculiX 真实几何 FEA)
   ★ 双解锁点         └──> Webots MCP（URDF + mesh 输入）
                           需另做 URDF 导出（当前全仓 URDF 零命中）

A3 (SW Simulation)  ── 独立可选的第二后端，受许可限制，最后做
```

**顺序建议：A1 → A2 → A3。** A1 不做，A2 没有输入；A2 不做，整个物理防线仍是"等效悬臂梁"。

## 附 · 交接时必须回答的问题

实现完成后，请逐条回答：

1. 你的导出/求解代码，**哪些路径在"出错时"会返回失败**？列出来。
2. 有没有任何一处 `except` 会导致「拿不到真实数据 → 用默认值继续跑」？如果有，在哪行？
3. 单位换算你做了几处？分别是哪几个量？
4. 你新增的函数，**谁调用它**？（如果没人调用，等于没写 —— 注意 `adapt_mesh` 就是死代码的前车之鉴）
5. 你改了哪些文件？有没有碰 `defense_gate.py` / `workflow_gate.py` / `SOLVER_BACKENDS`？（应当都没有）

---

*本文由源码核对生成。所有"现状"引用均可在标注行号处验证。*
