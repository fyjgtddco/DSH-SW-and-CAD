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

#### 〇.1a 【M5】基准面姿态用代码查，别背 ZSIGN

上面那段 `ZSIGN` 是**经验表**（社区实测，非官方数值表）。官方 API Help 里
**没有**三个默认基准面基向量的数值表 —— 换 SW 版本、自建基准面、或换模板时，
背下来的符号就可能错，而错了的表现是"整件沿 Z 镜像、外形尺寸完全一样"，极难发现。

**官方给的查询通路**（这才是权威来源）：

| 接口 | 官方原文 | 用途 |
|---|---|---|
| `IRefPlane::Transform` | "Gets the plane transform **with respect to the world**." | 拿基准面变换 |
| `IMathTransform::ArrayData` | 16 doubles：`0..8` 旋转、`9..11` 平移、`12` 缩放 | 解出三轴方向 |
| `ISketch::ModelToSketchTransform` | "Gets the model-to-sketch transform for this sketch." | 草图↔模型换算（**get-only**，求反向要 `Inverse()`） |

> ⚠️ `ISketch::GetSketchTransform` / `IGetSketchTransform` **不存在**（逐条查证
> ISketch 2026 成员表）。只有 `ModelToSketchTransform`（旧名 `ModelToSketchXform` 已废弃）。

**本仓库已封装（直接用）**：

```python
ax = m.plane_axes_world("Top Plane")
# {'ok': True, 'source': 'IRefPlane::Transform',
#  'normal_world': [0.0, 1.0, 0.0],          # Top 法向 = 世界 +Y  ← 查出来的，不是背的
#  'axes_world': {'x': [...], 'y': [0,0,-1], 'z': [...]},
#  'sketch_y_axis_index': 2, 'sketch_y_sign': -1.0}   # 草图 y 沿世界 Z，符号 −1

y = m.sketch_y_for_world_z("Top Plane", 20.0)["sketch_y"]   # → −20.0
```

**返回值里 `source` 字段必须看**：

- `"IRefPlane::Transform"` = 真查到了，可信；
- `"fallback_table"` = 退回经验表（附 `note` 警告），**别据此做精密镜像判断**，
  先排查为什么 `RefPlane` 读不到（通常是基准面名不匹配或特征树异常）。

**已核对一致的结论**：

| 基准面 | 法向（世界） | 草图 y 轴 | 与 `ZSIGN` 经验表一致？ |
|---|---|---|---|
| Top Plane | **+Y** | → 世界 **−Z** | ✅ 一致（`ZSIGN=-1` 有依据） |
| Front Plane | **+Z** | → 世界 **+Y** | ✅ 一致（无需取反） |
| Right Plane | ±X | → 世界 **+Y** | ✅ 一致（无需取反） |

> 🔴 **注意 Front Plane 法向是 +Z，不是 +Y** —— 检索时最容易被搞混的一处。
> 官方示例明确写了 "canonical position is aligned with the system defined
> **Front Plane**"，即 Front 的变换≈单位阵。


#### 〇.1b 【D3】禁止把 `ActiveDoc` 当文档身份（多房间串写的根因）

**为什么**：`ISldWorks::ActiveDoc` 是**进程级全局状态**，官方原话
"Gets the currently active document" —— 它描述的是"此刻前台是谁"，
**不是"我要操作谁"**。多房间/多进程交错时它会随时被别人切换，于是：

- A 房间的草图/拉伸落到 B 房间的文档上；
- `new_part()` 的清理逻辑把 B 正在建的未命名零件关掉（半成品凭空消失）。

**官方给出的显式句柄通路**（这才是正确做法）：

| 接口 | 语义 | 备注 |
|---|---|---|
| `OpenDoc6` / `OpenDoc7` | 返回 `IModelDoc2*` 指针 | 官方明说：**不会**激活文档，`ActiveDoc` 也**不会**返回它 —— 即"显式指针"与"ActiveDoc"是两条独立通路 |
| `GetOpenDocumentByName(全路径)` | 按完整路径取已打开文档 | 参数必须是 **path + 文件名 + 扩展名**，失败返回 NULL |
| `GetDocuments()` | 返回本次会话全部已打开文档数组 | 遍历/快照用 |
| `GetFirstDocument()` + `IModelDoc2::GetNext()` | 链式遍历 | ⚠️ **不存在** `ISldWorks::GetNextDocument` |

**本仓库已提供的封装（直接用，别自己写 `sw.ActiveDoc`）**：

```python
import swapi

# ✅ 按路径取显式句柄（文档已打开时）
m = swapi.from_path(sw, r"<任务目录>\parts\DSH_大齿轮.SLDPRT")

# ✅ 取当前活动文档，但【校验它是不是我要的那一个】
m = swapi.get_model(sw, expect=r"...DSH_大齿轮.SLDPRT", strict=True)
#    strict=True  → 身份不符直接抛 RuntimeError（推荐：宁可失败，不要串写）
#    strict=False → 记 warning 继续（旧脚本兼容）

# ✅ 身份比较工具
swapi.same_document(a, b)     # 路径优先；未保存时按标题（忽略扩展名）
swapi.doc_identity(model)     # -> (完整路径, 标题, 文档类型)
```

> ⚠️ **不存在 `IModelDoc2::GetID`** —— 官方成员表里没有这个接口，
> 不要用它做身份校验。身份三元组是 **路径 + 标题 + 类型**。

> 🔴 **`CloseDoc("")` 是危险调用**：官方原话
> "If Name is specified with an empty string (\"\"), then **the active document**
> is closed without saving." —— 取不到标题时传空串，等于**随机关掉别人的文档**。
> `swapi.SWModel.close()` 已改为「取不到标题就拒绝关闭」。

**run 期间的自动守卫**：`sw_bridge.py run` 现在会在执行前后各做一次
【已打开文档快照】对比；若本次脚本关掉了**别的房间的已命名文档**，
结果里会出现 `cross_room_docs_closed`（gate=`CROSS_ROOM_DOC_CLOSED`），
不再让"零件凭空消失"被当成正常。

#### 〇.1c 【D4】COM 忙 / 调用被拒绝：必须在原地退避重试，不要重跑脚本

**现象**：用户点一下 SolidWorks、弹出保存提示、或切走窗口，
COM 调用就会抛 `RPC_E_CALL_REJECTED`（`0x8001010A`）或
`RPC_E_SERVERCALL_RETRYLATER`（`0x80010001`）。

**旧行为（错）**：把异常当"这个签名不行"，换下一个签名字段；全失败后小屋
**改脚本重跑整个建模** —— 这正是回溯报告里"同一零件 4 版脚本
（`build_room1_gear_shaft v1→v4`）"的直接成因。

**现行为（对）**：`swapi.com_retry` 在**调用点**做指数退避
（300 ms → 5 s，最多 5 次），且**只对"忙/被拒"重试**；
业务错误（轮廓无效、特征返回 None）**原样抛出，绝不重试**。

```python
feat = swapi.com_retry(lambda: self.fm.FeatureExtrusion3(...),
                       _what="拉伸", _log=self.warnings.append)
```

`extrude()` 与 `cut()` 已内置该重试，**建模脚本无需自己写重试循环**。

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
### 〇.10 【P2】停止任务必须走 stop-all（别只 interrupt 子代理）

`interrupt_agent` 只是"请求"，子代理手里的下一条建模命令**照样会执行**。
要真正停机，必须落【停机令牌】：

```powershell
python mode_gate.py stop-all "用户要求停止"
# → 之后 sw_bridge / mode_gate / physics_bridge / visual_check
#   除 status / room-board / close-all / sw-kill 外一律返回 TASK_STOPPED（退出码 4）
python mode_gate.py resume          # 新任务开始前必须恢复
```

也可用环境变量 `DSH_KILL_SWITCH=1`。停止完整链路：
1. `mode_gate.py stop-all` → 2. `interrupt_agent` 各子代理 →
3. `sw_bridge.py close-all --quit` → 4. `sw_bridge.py sw-kill` →
5. 确认无 `SLDWORKS.EXE` / `python *sw_bridge*` 残留。

### 〇.11 【P3】同零件重复建模会被闸门拦下

`sw_bridge.py run` 执行前会按脚本声明的产物名去 `part_registry` 查重：
已建好且尺寸一致 → 返回 `gate=PART_ALREADY_BUILT`、`skip_modeling=true`，
**不再重建**。确需重建加 `--force-rebuild`。

台账只有名字、没有 `dims` 时 → 结论是 `PART_REUSE_UNKNOWN`（unknown），
**绝不当作"已建好"**（这是"不可用被折叠成通过"的同一条机理）。
先 `part_registry.py update-part --dims ...` 登记尺寸，下次才能自动复用。

### 〇.12a 【斜齿】斜齿必须用 helix_gear，别拿拉伸/拔模凑

**官方查证结论**（逐页核对 help.solidworks.com 2026，检索专员完成后我核了一遍）：

| 问题 | 官方答案 |
|---|---|
| 拉伸能不能做螺旋齿？ | **不能**。`FeatureExtrusion3` 23 参、`FeatureCut4` 27 参全部逐位核对，**没有任何 twist 参数** |
| `Dchk1/Dchk2/Dang1/Dang2` 能当螺旋角吗？ | **不能**。官方原文是 "allows a **draft angle**"，那是**拔模**，方向语义是 inward/outward |
| 有没有 gear 专用接口？ | **没有**。`IFeatureManager` 成员表 grep "gear" 零命中 |
| 官方唯一的"沿路径扭转"？ | **Sweep + `ISweepFeatureData.TwistControlType` = `swTwistControlConstantTwistAlongPath`(8) + `SetTwistAngle`** |

**本仓库封装**：

```python
r = m.helix_gear(module_mm=2, teeth=24, thickness_mm=47,
                 helix_angle_deg=14.5337, bore_dia_mm=20)
# → {'ok': True, 'method': 'segmented_loft', 'segments': 10,
#    'thickness_actual_mm': 47.0, 'params': {...}}
```

**几何关系**（`params` 里全部回传，可当场核对）：

```
L (导程) = π·d / tan(β)          d = m·z
总扭转角 = 360° × b / L           ← 按"轴向推进"算，不是按齿距算
每个齿距的轴向长度 = L / z
```

> ⚠️ **这里踩过一个坑，写下来免得再犯**：曾把扭转角算成 `360·b/(L/z)`，
> 把"一个齿距的相位"当成了整圈扭转，于是 m=2/z=24/b=47/β=14.5337° 算出
> **1666°（4.6 圈）** —— 完全不是斜齿。正确值应是 **29.09°**（≈1.94 个齿距）。

**两条实现路径**（按可靠性排序）：

1. **`segmented_loft`（实测可用）**：把扭转离散成 N 段（每段 ≤3°，上限 24），
   每段在**偏移基准面**上按相位旋转整圈齿廓、拉伸 dz，段段相接。
   - 关键：**必须用偏移基准面**。首版对所有段都调 `begin_sketch(plane)`，
     结果所有段都从 z=0 起拉、互相重叠 —— 实测包围盒 z 只有 0~2mm（齿宽 6mm）
     却返回 `ok=True`，这是最危险的**假成功**。
   - 现在有断言兜底：成型后**轴向尺寸必须 ≈ 齿宽**，否则 `ok=False`。
2. **`sweep_twist`（官方正路，本机未打通）**：需要 3D 路径草图，
   而 `ISketchManager` 在 SW2025 里**没有 `InsertSketch3D`**（已实测），
   所以当前返回明确的失败原因，不会假装成功。

**两点必须如实告知用户**：

- `segmented_loft` 是**离散近似**：段与段之间是微小台阶，不是光滑螺旋面。
  段数越多越接近，但特征数也越多（24 段 ≈ 24 个拉伸特征）。
- **`helix_angle_deg = 0` 会被直接拒绝**，并提示改用直齿 `gear()` ——
  防止"以为做了斜齿、其实是直齿"。

### 〇.12 【M1】齿轮必须过"零件定义断言"，否则不算建好

`m.gear()` 现在会回读**齿顶圆**与**实体体积**，与解析齿廓比对：

- `verify.tip_dia_actual_mm` ≈ `m·z + 2m`（偏差 ≤ 0.05 mm）；
- `verify.actual_volume_mm3` ≈ 齿廓解析面积 × 齿宽（偏差 ≤ 2%）。

任一不符 → `ok=false`，错误信息会点明"齿形没有真正生成"。
**把齿圈画成 φda 圆柱的假件会被当场拒绝**（回溯报告 §7 P5 事故）。

> `gear()` 目前是**直齿**；斜齿（螺旋角 β）需要 sweep/loft 另做，
> 返回值里 `verify.helix_angle_deg` 恒为 0.0，不要把它当成斜齿轮。

### 〇.13 【D2】多房间排队建模时可开无头模式（按官方配方）

可见窗口 + 人工操作会打断 COM（见 〇.1c）。排队建模时：

```powershell
$env:DSH_SW_VISIBLE = "0"     # 无头
```

**官方正确配方**（`Keep SOLIDWORKS Invisible While Activating Documents` 示例）：
`UserControl = False` → `Visible = False` → `Frame.KeepInvisible = True`。
本模块已按此顺序实现，并**恒不改文档级 `Visible`**。

**三条必须知道的官方约束**（都是踩过坑才查到的）：

| 约束 | 官方原文 | 对我们的含义 |
|---|---|---|
| 应用级隐藏的前提 | "the SOLIDWORKS Design application can only be hidden if `ISldWorks::UserControl` is **false** and there are **no visible documents** open" | 直接 `sw.Visible=False` 在有可见文档时**会被忽略**；必须先 `UserControl=False` |
| 别用文档级隐藏 | "`IModelDoc2::Visible`=false **does not release UI resources** ... will eventually cause **critical resource shortages and instability**" | 永远不要用 `model.Visible=False` 批量藏文档 |
| 看不见就选不中 | `SelectByID2`："The IModelDoc2 object ... must be an **open and visible** document." | 本模块 65 处 `SelectByID2`（选面/选基准面/选草图段）依赖它 → **文档必须保持可见** |

> 📌 因此本实现只隐藏**应用窗口**，文档一律保持可见：
> 既能减少"用户点到 SW 打断 COM"，又不破坏 `SelectByID2`。
> 若隐藏没生效（有可见文档时会发生），`swapi._HEADLESS_STATE["note"]` 会如实记录，
> **绝不假装已无头**。

默认仍是**可视化建模**（用户明确要求"要能看到建模过程"，见
`solidworks-modeling.md §0.2`）。无头模式是**可选**，不是默认。

### 〇.14 【D4 配套】批量建模用 `batch_mode` 减少刷新

`ISldWorks::CommandInProgress` 是**官方专为跨进程批处理**提供的性能开关：

```python
with swapi.batch_mode(sw):
    m.extrude(10)
    m.cut(through=True)
    m.fillet(2, [...])
```

官方原文："**Improves performance of out-of-process applications** by informing
SOLIDWORKS that a sequence of API calls will be made" /
"**reduces the number of updates** it makes during these calls"。

- 只对 **out-of-process**（我们正是）有效；
- ⚠️ 它是**性能提示，不是忙检测**。忙/被拒的重试仍由 `com_retry` 负责；
- 异常安全：`finally` 里一定复位，绝不把 SW 留在批处理态。
