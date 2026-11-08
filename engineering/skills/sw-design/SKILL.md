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

### 〇、开工前必读：坐标系与方向语义（先读这一节，再动第一个草图）

> 【建模规范前置 · 2026-11-07】本节是**操作手册**，不是口号。
> 下面每一条都给出「为什么」「怎么写」「怎么当场验」三段，并配 **✅正例 / ❌反例** 代码。
> 违反本节的后果不是"图纸不太对"，而是**零件镜像、切除切反、总装整体错位**，
> 且 **SolidWorks 不会报错** —— 错误会一路带到干涉检查和出图才暴露。

#### 〇.1 三个基准面的草图局部坐标 ≠ 世界坐标（Top Plane 会镜像）

**为什么**：SolidWorks 的 Top Plane 法向是 **+Y**，它的草图局部坐标轴是
**草图 x → 世界 +X**、**草图 y → 世界 −Z**。也就是说，在 Top Plane 上画草图时，
**草图 y 增大 = 世界 Z 减小**。Front / Right Plane 的草图 y 都正常（世界 +Y），
只有 Top Plane 是反的。不修正就是**整件沿 Z 镜像**，而外形尺寸完全一样，
肉眼与包围盒检查都发现不了。

**核心约定（写进每个脚本顶部）**：

```python
# 草图局部 y → 世界 Z 的符号（唯一权威定义，脚本内不要再写第二份）
ZSIGN = {"Top Plane": -1.0,    # 镜像：草图 y 正向 = 世界 −Z
         "Front Plane": +1.0,  # 正常
         "Right Plane": +1.0}  # 正常

def wz(plane, sketch_y):
    """把草图 y 换算成世界 Z（mm）。所有"我在 Z 上要多少"的意图都过这一层。"""
    return ZSIGN[plane] * float(sketch_y)
```

**❌ 反例：直接按"草图 y 就是高度"写**

```python
# 意图：在顶面 z=+20 处挖一个 ⌀10 的孔
m.begin_sketch("Top Plane")
m.circle(0, 20, 5)          # ❌ 20 是草图 y；因镜像，孔实际落在 z = −20（顶面跑到下方）
m.end_sketch()
m.cut(through=True)
# 结果：零件外形尺寸全对、体积变化也对，但孔在下表面；SW 全程无报错。
```

**✅ 正例：把"世界 Z 意图"显式换符号**

```python
PLANE = "Top Plane"
z_intent = 20.0                             # 我真正要的世界 Z
m.begin_sketch(PLANE)
m.circle(0, wz(PLANE, -z_intent), 5)        # ✅ −20 * (−1) = 20 → 落在世界 z=+20
m.end_sketch()
m.cut(through=True)

sig = m.body_box_mm()                       # 当场验：见 〇.4 的三条判据
```

> 📌 一句话记法：**只有在 Top Plane 上，写进草图的 y 要取反**（`wz("Top Plane", -z)`）；
> Front / Right Plane 直接写 y 即可。

#### 〇.2 `cut()` 默认沿 −Y 切；要沿 +Y 必须 `flip=True`

**为什么**：`cut(depth=..., through=...)` 在不给 `flip` 时，切除方向是**基准面法向的负向**，
对最常见的 Front Plane 草图即 **−Y**。若零件真实材料在 +Y 一侧，默认切除会**从没料的一侧切**，
表现为 `FeatureCut` 返回 None、或"切了但体积几乎没变"。

```python
# 方向语义（与 swapi.cut 的实现一致，别自己另发明）
m.cut(depth=10)                  # 沿 −Y，深 10mm
m.cut(depth=10, flip=True)       # 沿 +Y，深 10mm  ← 材料在 +Y 时必须加这个
m.cut(through=True)              # 双向贯穿（through_both 默认 True）
m.cut(through=True, through_both=False)   # 只正向贯穿
```

> 🔴 **不要把 `flip=True` 和"切掉轮廓外侧"混为一谈**：
> `flip=True` 是**反转切除方向**（落到 FeatureCut 的 `Dir` 位）；
> "切掉轮廓外侧"是另一个开关 `flip_side_to_cut=True`（`Flip` 位），默认 False。
> 把方向问题误写成 `flip_side_to_cut=True` 会**切掉大半零件**。

**❌ 反例：材料在 +Y，却用默认方向**

```python
m.begin_sketch("Front Plane")
m.rect(0, 0, 40, 10)     # 轮廓画在 y∈[0,10]
m.end_sketch()
m.cut(depth=8)           # ❌ 沿 −Y 切 → 切的是 y∈[−8,0] 这块空气，等于没切
```

**✅ 正例：按材料实际所在侧显式给方向，并当场验体积**

```python
before = m.body_box_mm()
m.begin_sketch("Front Plane")
m.rect(0, 0, 40, 10)
m.end_sketch()
m.cut(depth=8, flip=True)          # ✅ 材料在 +Y → 明确翻转
after = m.body_box_mm()
assert after["volume_mm3"] < before["volume_mm3"], "切除没生效：方向反了？"
```

#### 〇.3 每个零件必须声明「上方向」与「主轴方向」，写进 `<零件>.SLDPRT.pr.json`

**为什么**：装配错位的根源几乎从来不是"配合写错"，而是**每个房间对"上"的理解不同**：
有人把 +Z 当上、有人把 +Y 当上、有人把轴心当原点。这些约定如果只存在于某个小屋的
上下文里，总装房就只能靠猜。所以**声明必须落盘**，成为可被机器读取的事实。

**命名与位置**（与既有 `<零件>.SLDPRT.origin.json` / `.material.json` 同一约定：
直接追加后缀，保证与零件**同目录、同名基**）：

```python
import json, os

PART = r"<任务>\parts\DSH_立柱.SLDPRT"

def write_pr_record(part_path, up_axis, main_axis, up_sign=1, note=""):
    """把"上方向 / 主轴方向"声明写到 <零件>.pr.json（幂等，失败不致命）。"""
    rec = {
        "schema": "dsh-engineering/part-orientation@1",
        "part": os.path.abspath(part_path),
        "part_name": os.path.splitext(os.path.basename(part_path))[0],
        "up_axis": up_axis,            # "X" | "Y" | "Z"，必填
        "up_sign": int(up_sign),       # +1 / −1，"上"是轴的正向还是负向，必填
        "main_axis": main_axis,        # "X" | "Y" | "Z"：主轴（回转轴/最长方向），必填
        "base_plane": "Top Plane",     # 建模时首个草图所在基准面（审计用）
        "note": note,
    }
    with open(str(part_path) + ".pr.json", "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)
    return rec

# 立柱：立着、轴沿 Z、从底面往上长
write_pr_record(PART, up_axis="Z", main_axis="Z", up_sign=+1,
                note="底面中心为原点，+Z 朝上；与 origin_mode=center_bottom 一致")
```

| 字段 | 取值 | 必须写对的东西 |
|---|---|---|
| `up_axis` | `"X"` / `"Y"` / `"Z"` | 该零件**装好后**"上"对应哪个世界轴 |
| `up_sign` | `+1` / `-1` | "上"是这一轴的正向还是负向（轴类件尤其容易写错） |
| `main_axis` | `"X"` / `"Y"` / `"Z"` | 回转轴 / 最长方向；装配对齐、出图定向都用它 |
| `base_plane` | 基准面名 | 首个草图所在面，用来复核 〇.1 的 ZSIGN 有没有用对 |

**❌ 反例：不声明，靠"我记得是竖着的"**

```python
m.save(PART)          # ❌ 只有 .SLDPRT，没有 .pr.json
# 总装房无从判断"上"是哪一边 → 只能按"中心对称"直觉摆放 → 整机错位
```

**✅ 正例：保存成功后立刻落声明（与 origin.json 一起）**

```python
m.save(PART)
write_pr_record(PART, up_axis="Z", main_axis="Z", up_sign=+1,
                note="底面中心为原点，+Z 朝上")
# 目录里应同时看到三件：
#   DSH_立柱.SLDPRT
#   DSH_立柱.SLDPRT.origin.json      ← origin 约定（swapi 自动写）
#   DSH_立柱.SLDPRT.pr.json          ← 本节的朝向声明（你必须写）
```

#### 〇.4 总装前：断言每个组件旋转矩阵与声明朝向一致；**零旋转装配必须报警**

**为什么**：本仓库的装配定位是"纯平移 + 配合"（`_translation_matrix_m16` 是唯一入口，
没有旋转输入），组件旋转子矩阵理应是**单位阵**。因此**零件必须按世界 up 建好**：
`up_axis` 一律等于世界 up，"靠总装时转过去"这条路径**当前不存在** ——
一个 `up_axis ≠ 世界 up` 的零件，本身就是契约违规，唯一合法处置是**重建该零件**
（或按 C1 契约上报、由总理子代理统一改契约后重建），不是"总装时转一下"。
而被别的路径改过姿态时（配合求解 / 手工拖动 / 历史 SetXform），旋转矩阵会偏离单位阵，
位姿就和 `.pr.json` 里声明的朝向**对不上** —— 此时"坐标读出来是对的"也只是假象。

由此得到两条必须报警的形态：
- **A｜零旋转装配**：声明上轴 ≠ 世界 up（本应另有姿态），实测旋转却仍是单位阵
  → 零件**根本没被摆正**（错位最常见的形态，而 `rotation_ok=true` 会让它"看起来通过"）；
- **B｜姿态被改**：声明上轴 == 世界 up（本应单位阵），实测却偏离单位阵
  → 位姿被配合/拖动改过，坐标对上也只算可疑。

**判据（`assert_orientation(...)["ok"] is True` 才允许进干涉检查）**：

```python
def assert_orientation(m, expectations, world_up="Z"):
    """
    expectations: [(组件名, pr记录 dict), ...]
    m: SWModel（装配体）
    返回 {"ok", "alarms":[...]}
    """
    alarms = []
    for name, pr in expectations:
        # ① 位姿读回（读不回来 = 未验证，绝不当通过）
        pose = m.read_component_pose(name)          # {position_mm, rotation, rotation_ok, ...}
        if not pose.get("verified"):
            alarms.append("%s: 位姿读不回来（rotation=%r）" % (name, pose.get("rotation")))
            continue
        rot = pose.get("rotation") or []
        is_identity = pose.get("rotation_ok") is True

        # ② 声明朝向必须与"是否发生旋转"自洽。
        #    本仓库装配是纯平移（没有旋转输入），所以【声明上轴 ≠ 世界 up 本身就是违规】：
        #    它不是"总装时转过去"，而是"零件建错方向了" —— 必须先重建零件。
        need_rotation = (str(pr.get("up_axis")).upper() != str(world_up).upper())
        if need_rotation and is_identity:
            alarms.append(
                "%s: 声明上轴=%s 而世界 up=%s，实测却是【零旋转装配】"
                "（旋转子矩阵=单位阵）→ 零件根本没被摆正（本仓库无旋转路径，须重建该零件）"
                % (name, pr.get("up_axis"), world_up))

        # ③ 反向：声明已对齐却出现明显旋转 → 姿态被别的路径改过
        if (not need_rotation) and (not is_identity):
            alarms.append(
                "%s: 声明上轴与世界 up 一致，却偏离了单位阵 rotation=%r"
                " → 被配合/拖动改过姿态，需复核" % (name, rot))
    return {"ok": not alarms, "alarms": alarms}
```

```python
# 总装脚本里的强制闸门：不过就不许进干涉检查 / 出图
bad = assert_orientation(m, [(n, load_pr(n)) for n in planned_components], world_up="Z")
if not bad["ok"]:
    raise RuntimeError("朝向断言未通过（零旋转装配报警）：\n" + "\n".join(bad["alarms"]))
```

**❌ 反例：只看"插进去了"就当装配成功**

```python
m.add_components_batch(paths)                    # ❌ 全部塞进原点
r = m.verify_assembly_placement(expected)        # 只看坐标
if r["n_ok"] == r["n_expected"]:
    m.save(ASM)                                  # ❌ 忽略了 rotation_ok / 声明朝向
```

**✅ 正例：坐标 + 旋转 + 声明朝向，三者一起断言**

```python
r = m.verify_assembly_placement(expected)
if not r["assembly_ok"]:                         # 已含"旋转异常"判据
    raise RuntimeError(r["verdict"])
assert_orientation(m, [(n, load_pr(n)) for n in planned], world_up="Z")   # 再加声明朝向这一层
m.save(ASM)
```

#### 〇.5 本节速查（开工前 30 秒自查）

| # | 自查项 | 机器判据 |
|---|---|---|
| 1 | Top Plane 草图 y 是否已取反 | 脚本里有 `ZSIGN` / `wz()`，且 Top Plane 调用处出现负号 |
| 2 | `cut()` 方向是否与材料所在侧一致 | 切除后 `body_box_mm()["volume_mm3"]` 确实变小 |
| 3 | 每个零件是否都有 `<零件>.SLDPRT.pr.json` | 目录里 `.SLDPRT` 与 `.pr.json` 数量一致 |
| 4 | `.pr.json` 的 `up_axis`/`up_sign`/`main_axis` 是否都填了 | 三个字段非空且在枚举内 |
| 5 | 总装是否过了朝向断言 | `assert_orientation(...)["ok"] is True`；出现零旋转装配即报警 |

---

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
