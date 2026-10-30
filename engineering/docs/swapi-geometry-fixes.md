# SolidWorks API 几何缺陷修复方案（C01~C04）

- **日期**：2026-10-29
- **缺陷来源**：`test\20261029_082936_4159A331\PLUGIN_BUGS_20261029.md`（BUG-C01~C04）
- **证据等级**：本文结论来自**本机 SW2025 SP5.0 真实 COM 实测** + 官方 API 帮助（本机 CHM）+ 类型库反射。
  凡未实测的都明确标 `【推断】`，不含臆造的签名。

---

## 0. 摘要（4 条一句话结论）

| 编号 | 结论 | 优先级 |
|---|---|---|
| **C01** | 不是"选段算法不稳"，而是 **`ISketchSegment.Select4` 在本机 0/24 全失败（com_error）**；`Select2(Append,0)` 24/24 成功。而**一次挤出多轮廓**（外形+多个内环）实测体积 84000 mm³ **精确命中理论值** —— 这条路才是稳的。 | ★★★ |
| **C02** | **已复现**：`_has_curved_face_at(0,0,10)` 在**顶面平面点**上返回 **True**。根因是拿圆柱面的 `GetBox`（[-5,-5,0 → 5,5,10]）当判据，而这个包围盒在 Z 向**跨越整个板厚**，把孔正上方的平面点整个包住。 | ★★★ |
| **C03** | **`FeatureCut4` 需要 27 个参数，而 `swapi.cut()` 只传 26 个** → 每次都给 COM 抛 `-2147352561 非选择性的参数`。27 参实测**真的能切**（去料 0.818%，与 Ø10 通孔理论值一致）；26 参必抛异常。 | ★★★ |
| **C04** | **`ModelDoc2.SaveBMP` 不需要前台窗口**：把 SW 窗口 `ShowWindow(hwnd, 6)` 最小化后仍返回 True，0.1s 写出 4320054 字节（1600×900×3+54 ✓）。不需要 `SetForegroundWindow`。 | ★★☆ |

---

## 1. C01 — cut 在多矩形内环选段上不稳定

### 1.1 现场症状
`PLUGIN_BUGS_20261029.md`：`cut` 对多矩形内环选段不稳定（**选段 20/24 部分失败**）；
而 `extrude_with_holes`（把所有孔做进基体草图内环一次挤出）最稳。

### 1.2 本机实测（决定性）

| 实验 | 结果 |
|---|---|
| `ISketchSegment.Select4(Append, SelectData)` × 24 段 | **0 / 24 成功**，抛 `com_error` |
| `ISketchSegment.Select2(Append, 0)` × 24 段 | **24 / 24 成功** |
| `ISketchManager.GetSketchContours`（属性访问） | 返回 3 条轮廓（3 层嵌套矩形）→ `len()` 可用 |
| `ISketchContour.Select2` | `com_error -2147352573 找不到成员`（动态绑定下不可调度） |
| **一次 `FeatureExtrusion3`（1 外形 + 2 内环）** | **体积 84000 mm³，与理论值精确相符** |

**根因**：`swapi.select_all_sketch_segments()` 有一份 `_seg_select_strategy` **策略缓存**。
一旦某次上下文把策略定成 `Select4`，后续所有选段都会走 `Select4` → 本机全抛异常 →
表现为"部分失败 / 选段 0"。`Select2` 才是本机可用路径。

### 1.3 修复方案

**(A) 选段侧（改动小）** — `swapi.py` `select_all_sketch_segments()`（约 L6708、L6808）

```python
# 现在的顺序是 Select2 > Select > Select4，且结果会缓存。
# 建议：把 Select4 从【自动重试链】里彻底摘掉，只保留 Select2 / Select。
#       并把缓存改成"每次选段后校验 GetSelectedObjectCount2"，不达标就重试另一种，
#       而不是把一次性成败固化成全局策略。
ok = False
try:
    ok = bool(cls_or_self._seg.Select2(appending, 0))   # 本机 24/24 可用
except Exception:
    ok = False
if not ok:
    try:
        ok = bool(cls_or_self._seg.Select(appending))
    except Exception:
        ok = False
# 不要再回退到 Select4 / Select4(Append, SelectData)
```

> 官方帮助：`ISketchSegment.Select4` 已标 **Obsolete**（本机 CHM 2025 确认），推荐 `Select2`。

**(B) 轮廓侧（推荐，治本）** — 让 `extrude_with_holes` 成为默认开孔路径

实测证明：**根本不是"选段算法"的问题，而是"根本不该选段"**。
把外形 + 所有孔画进**同一张草图**，一次 `FeatureExtrusion3`（UseAutoSelect=True）
让 SW 自己认轮廓 —— 不需要逐个选段，也就没有"选段失败"这件事。

```python
m.begin_sketch("Top Plane")
m.rect(0, 0, 120, 80)
for (cx, cy, r) in holes:
    m.circle(cx, cy, r)
m.end_sketch()
f = m.extrude_with_holes(depth_mm)   # 已存在；确保 UseAutoSelect=True
```

**取舍**：短期先做 (A)（摘掉 Select4 即可消除"部分失败"）；中期凡"板件 + 多孔"，
优先走 `extrude_with_holes`，`cut` 只留给"在已有实体上单独挖一个孔/槽"。

### 1.4 风险
- `Select2` 在 SW2018~2025 全版本可用（官方未标 Obsolete），风险低。
- 全内环一次挤出要求所有孔**深度相同**；不同深度仍需分批或改用 cut。

### 1.5 回归测试建议
1. 静态断言：`swapi.py` 里 `Select4` 不再出现在自动重试链（源码字符串检查）；
2. 纯函数断言：把"选段结果 → 是否继续回退"抽成纯函数，注入 mock；
3. 体积断言（skip-if-no-SW）：`extrude_with_holes` 在 2 孔板上体积 == 理论值。

---
## 2. C02 — `_has_curved_face_at` 误判挡住 M3 沉孔

### 2.1 现场症状
`_has_curved_face_at` 返回 false positive，阻塞 M3 沉孔（counterbore）创建。

### 2.2 本机实测（已复现）
构造 120×80×10 板 + Ø10 通孔后：

```
faceCount = 14，其中圆柱面 1 个
该圆柱面 GetBox = [-5,-5,0, 5,5,10]        ← Z 从 0 到 10（贯穿整个板厚）
_has_curved_face_at(0, 0, 10) → True        ← 顶面(z=10)正上方的【平面点】被误判为曲面
```

**根因**：判据是"点是否落在曲面 `GetBox` 的包围盒内（±1mm）"。
圆柱面的包围盒是一个**立方体**，Z 向跨越整个板厚 —— 于是孔正上方的顶面
（本来就是平面、本来正适合打沉孔）被整个包进去 → 误判 → 走不到平面分支。

### 2.3 修复方案（`swapi.py` L6150 `_has_curved_face_at` / L6432 调用点）

**不要用包围盒当判据。用官方 `ISurface` 的几何类型判定：**

```python
def _face_is_planar(self, face):
    """官方判定：平面 vs 曲面（SW2018+ 全版本可用）。"""
    try:
        surf = face.GetSurface
        if not surf:
            return None                      # 拿不到 → None，由调用方决定
        try:
            return bool(surf.IsPlane)        # 属性访问，不要加括号
        except Exception:
            pass
        try:
            return int(surf.Identity) == 4001   # PLANE_TYPE，本机实测确认
        except Exception:
            pass
        return None
    except Exception:
        return None
```

本机实测：6 个平面面 `IsPlane=True / Identity=4001`；圆柱面 `IsPlane=False`。

**最小改动（推荐先上这一版）**：`swapi.py` L6432 附近

```python
# 旧：if not self._has_curved_face_at(x, y, z) and self._select_face_by_box(x, y, z):
# 新：平面优先；平面选不到、且点确实在曲面几何上时才转曲面
if self._select_face_by_box(x, y, z):
    ...                                  # 平面优先，直接命中顶面
elif self._has_curved_face_at(x, y, z):
    ...                                  # 平面选不到 → 才考虑曲面
```

**更稳的第二步**：给 `_has_curved_face_at` 加"几何二次筛"——包围盒只做粗筛，
再用 `ISurface` 的最近点/法向确认点真的落在该面上：

```python
def _has_curved_face_at(self, x, y, z):
    for face in self._iter_faces():
        if self._face_is_planar(face) is True:
            continue                       # 平面直接跳过（这才是本意）
        bb = face.GetBox
        if not (bb[0]-0.001 <= x/1000 <= bb[3]+0.001 and
                bb[1]-0.001 <= y/1000 <= bb[4]+0.001 and
                bb[2]-0.001 <= z/1000 <= bb[5]+0.001):
            continue                       # 包围盒只用于缩小候选集
        # 二次确认：用 GetClosestPointOn / 法向判定点是否真在该曲面几何上
        # ⚠️ GetClosestPointOn 的返回值语义需本机复核（现有注释已警告
        #    "v 参数不是角度"）。拿不准时，宁可【返回 False】让平面路径生效。
        return self._point_really_on_surface(face, x, y, z)
    return False
```

> 关键取舍：**误判成"曲面"的代价（沉孔打不了）远大于漏判**。
> 所以 `_has_curved_face_at` 应当在不确定时返回 **False**（fail-open 到平面路径）。

### 2.4 风险
- `Identity == 4001` 是 PLANE_TYPE 常量，跨版本稳定（本机 2025 实测；2018+ 同族）。
- `IsPlane` 在早期弱类型绑定下可能返回异常对象 → 用 `isinstance(v, bool)` 兜底。

### 2.5 回归测试建议
1. 纯函数：`_face_is_planar` 注入三个 mock face（平面 / 圆柱 / 拿不到）→ True / False / None；
2. 源码断言：`_has_curved_face_at` 不再把 `GetBox` 当**唯一**判据（必须出现 `_face_is_planar`）；
3. 真机（skip-if-no-SW）：带 Ø10 孔的板，`_has_curved_face_at(0,0,10)` 必须为 **False**。

---

## 3. C03 — 电机腔 cut 方向/选段错误

### 3.1 现场症状
内腔沿 Z 切变成贯穿槽、**选段 = 0**；应沿 Y（电机轴方向）。

### 3.2 本机实测（决定性）

```
FeatureCut4(27 参数, 正确选中草图轮廓, T1=ThroughAll) → 真实切除
    去料 0.818% == π·(Ø10/2)²·10 / 96000 = 785.4/96000 = 0.818%   ✔ 精确
FeatureCut4(26 参数 = swapi 目前传的)                 → 抛 com_error -2147352561 非选择性的参数
FeatureCut3(26 参数)                                  → 也能切
FeatureCut4(27, NormalCut=True, OptimizeGeometry=True) → 返回 None（钣金专属，非钣金不可用）
```

**两个硬结论**：

1. **`swapi.cut()` 传给 `FeatureCut4` 的是 26 个参数，而它需要 27 个** →
   每次 `FeatureCut4` 调用都必然抛异常。当前 `_apis = ("FeatureCut3", "FeatureCut4")`
   把 Cut3 放前面，所以"表面能用"，但：
   - `FeatureCut3` 在 SW2025 已标 **Obsolete**；
   - 一旦 Cut3 某个签名不适用于当前场景（底面 / 对侧面），就彻底失败 →
     这正是"T1 第 2 次 cut 必失败"的直接原因。

2. **27 参的补齐项是最后的 `OptimizeGeometry`**，而且中间几个布尔量**映射错位**。
   对照官方签名：

```
FeatureCut4(Sd, Flip, Dir, T1, T2, D1, D2, Dchk1, Dchk2, Ddir1, Ddir2,
            Dang1, Dang2, OffsetReverse1, OffsetReverse2,
            TranslateSurface1, TranslateSurface2, NormalCut,
            UseFeatScope, UseAutoSelect, AssemblyFeatureScope,
            AutoSelectComponents, PropagateFeatureToParts,
            T0, StartOffset, FlipStartOffset, OptimizeGeometry)   ← 共 27 个
```

`swapi.py` L8416 附近当前传的是 26 元组。**位置 19 是 `UseFeatScope`（现在写死 False），
位置 20 才是 `UseAutoSelect`（现在塞的是 `auto_select`）** —— 也就是
**`auto_select` 一直喂给了错误的参数位**，真正要求"自动选轮廓"的那一位是 False。
这解释了为什么"选段 0 也静默返回 None"。

### 3.3 修复方案 — `swapi.py` `cut()`（L8021 起，参数元组在 L8416 附近）

```python
# 27 参，按官方顺序逐位对齐
_f, _why = _try_cut(_api, (
    True,               # Sd
    _flip_pass,         # Flip
    _dir_pass,          # Dir
    _t1,                # T1
    _t2,                # T2
    _dd1,               # D1
    _dd2,               # D2
    False,              # Dchk1
    False,              # Dchk2
    False,              # Ddir1
    False,              # Ddir2
    0.0,                # Dang1
    0.0,                # Dang2
    False,              # OffsetReverse1
    False,              # OffsetReverse2
    False,              # TranslateSurface1
    False,              # TranslateSurface2
    False,              # NormalCut        ← 非钣金必须 False（实测 True 返回 None）
    True,               # UseFeatScope
    bool(auto_select),  # UseAutoSelect    ← ★ 关键：正确位置
    False,              # AssemblyFeatureScope
    True,               # AutoSelectComponents
    False,              # PropagateFeatureToParts
    0.0,                # T0
    0.0,                # StartOffset
    False,              # FlipStartOffset
    False,              # OptimizeGeometry ← 非钣金必须 False（实测 True 返回 None）
))
```

**"方向切反成贯穿槽"的补救**（现有代码已有 Flip 重试循环，补一条几何校验）：

```python
_vol_before = self._body_volume_mm3()
f = ...FeatureCut4...
_vol_after = self._body_volume_mm3()
_removed = _vol_before - _vol_after
if _expected_mm3 and _removed > _expected_mm3 * 3:
    # 明显超出预期 = 方向反了（切成了整条贯穿槽）
    self._undo_last_feature()
    continue
```

```python
def _undo_last_feature(self):
    """撤销刚建的特征（SW2018+ 可用 model.EditUndo2(1)）。"""
    try:
        self.model.EditUndo2(1)
        return True
    except Exception:
        return False
```

### 3.4 风险
- 27 参在 SW2018~2025 官方签名一致，但各版本对 `NormalCut` / `OptimizeGeometry`
  的处理不同：**非钣金两者都必须 False**（本机实测）。
- ⚠️ 现有代码注释写着"Cut4(27 参) → 无效的参数数目"，与本次实测（27 参成功）**矛盾**。
  该注释大概率来自另一套参数集。**换机器/换版本后必须重跑一次探针复核。**
- 在复核前，建议保留 `Cut3 → Cut4` 顺序，但**把 Cut4 的 26 参改成 27 参**
  （否则它永远白抛异常、白拖慢）。

### 3.5 回归测试建议
1. 源码断言：`FeatureCut4` 的调用元组长度必须为 27（`len(args) == 27`）；
2. 单位测试：把参数构造抽成纯函数 `_cut4_args(...)`，断言 index 19 是 `auto_select`；
3. 真机（skip-if-no-SW）：Ø10 通孔板，去料体积 == 理论值 ±1%。

---
## 4. C04 — 后台截图失败（前台被 DSH GUI 占用）

### 4.1 现场症状
`visual_check.py gate` 截图失败、stdout 为空，前台窗口是 DSH GUI。

### 4.2 本机实测（决定性）

```
窗口最小化 ShowWindow(hwnd, 6) 后：
  model.SaveBMP(path, 1600, 900)  → True，0.1s 写出 4320054 字节
     1600*900*3 = 4320000 + 54 字节 BMP 头 = 4320054  ✔ 完全吻合
  model.SaveBMP(path, 800, 600)   → True，0.02s 写出 1440054 字节
     800*600*3 = 1440000 + 54    ✔
  magic 前 2 字节 = 42 4D ("BM") → 确认是合法 BMP

  传 .png 路径 → 仍写出 BMP 字节（证实 SaveBMP 忽略扩展名）
  窗口不激活 / 不 SetForegroundWindow / 在后台  → 全部成功
```

**结论**：`ModelDoc2.SaveBMP` **与窗口前台状态无关**，是真正的后台渲染。
"C04 截图失败"的根因是 `swapi.screenshot()`（L5531）走的是**屏幕抓取 + 强制激活窗口**，
而不是 `SaveBMP`。

补充实测：
- `IModelView.GetScreenshot` **不存在**（AttributeError）→ 官方无此 API；
- `IModelView.GetViewDIB` 存在，返回 int（DIBSECTION 指针），可用但更麻烦；
- `GetViewDIBx64` 在动态绑定下抛 com_error。

### 4.3 修复方案 — `swapi.py` `screenshot()`（L5531）

```python
def screenshot(self, path=None, for_vision=False):
    """默认走 SaveBMP（后台渲染，不依赖前台窗口）；抓屏仅作降级。"""
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "solidworks_live.png")

    # 首选：SaveBMP（后台，稳定，本机实测最小化也 OK）
    bmp = os.path.splitext(path)[0] + ".bmp"
    try:
        ok = bool(self.model.SaveBMP(bmp, 1600, 900))
        if ok and os.path.exists(bmp) and os.path.getsize(bmp) > 54:
            if path.lower().endswith((".png", ".jpg", ".jpeg")):
                if _bmp_to_png(bmp, path):
                    try: os.remove(bmp)
                    except Exception: pass
                    return {"ok": True, "path": path, "size": "1600x900",
                            "method": "SaveBMP+convert",
                            "activated": False, "verified_foreground": False,
                            "note": "SaveBMP 后台渲染（不依赖前台窗口）"}
            return {"ok": True, "path": bmp, "size": "1600x900",
                    "method": "SaveBMP", "activated": False,
                    "verified_foreground": False,
                    "note": "SaveBMP 后台渲染（不依赖前台窗口）"}
    except Exception:
        pass

    # 降级：原抓屏路径（才需要激活窗口）
    # ...原实现保持不变...
```

```python
def _bmp_to_png(bmp_path, png_path):
    """BMP → PNG（用 Pillow；没装就返回 False，调用方退回 .bmp）。"""
    try:
        from PIL import Image
        with Image.open(bmp_path) as im:
            im.save(png_path, "PNG")
        return True
    except Exception:
        return False
```

**同时**：`visual_check.py` 的 `_grab_via_savebmp` 已经是这个思路（优先 SaveBMP），
但 `swapi.screenshot()` 仍是抓屏优先 —— **两处口径不一致**。建议统一为"SaveBMP 首选"。

### 4.4 风险
- `SaveBMP` 输出的是**当前视图**（不一定是标准三视图）→ 调用前仍需
  `ShowNamedView2("*Front" / "*Top" / "*Right" / "*Isometric", 0)` 切视图；
  好消息是**切视图同样不需要前台**。
- 大分辨率（4K）时 `SaveBMP` 更慢（本机 1600×900 约 0.1s，可接受）。
- 输出是 BMP → 需要 Pillow 转 PNG；没装 Pillow 就直接留 BMP（vision 也能读）。

### 4.5 回归测试建议
1. 源码断言：`swapi.screenshot` 里 `SaveBMP` 出现在 `SetForegroundWindow` **之前**；
2. 纯函数：`_bmp_to_png` 在"无 Pillow / 正常"两种环境下的返回值；
3. 真机（skip-if-no-SW）：窗口最小化后 `screenshot()` 返回 `ok=True`、文件 > 1KB、
   前两字节 `BM`（或 PNG magic `89 50 4E 47`）。

---

## 5. SW 2018~2025 版本差异与降级策略

| API | 2018~2024 | 2025（本机实测） | 降级策略 |
|---|---|---|---|
| `ISketchSegment.Select4` | 官方标 Obsolete | 24/24 **全抛 com_error** | 一律改用 `Select2(Append, 0)` |
| `ISketchSegment.Select2` | 可用 | 24/24 成功 | 首选 |
| `ISketchContour.Select2` | 【推断】可用 | `找不到成员` | 不要选 contour，用自动选轮廓 |
| `ISurface.IsPlane` / `IsCylinder` | 可用（属性） | 可用（属性） | 属性访问，不要加括号 |
| `ISurface.Identity` | PLANE_TYPE=4001 | PLANE_TYPE=4001 | 兜底判据 |
| `IFeatureManager.FeatureCut3` | 可用 | 可用但 **Obsolete** | 当前稳定首选；Cut4 修好 27 参后可换 |
| `IFeatureManager.FeatureCut4` | 26 参抛异常 / 27 参可用【推断】 | 26 参抛 `-2147352561`；27 参**真实切除** | 必须传 27 参 |
| `IModelDoc2.SaveBMP` | 可用 | **最小化也能导出** | 首选截图 |
| `IModelView.GetScreenshot` | 不存在 | 不存在 | 用 SaveBMP |
| `IModelDoc2.EditUndo2` | 可用 | 【推断】可用 | 切除体积超预期时撤销 |

> 建议在 `swapi.py` 顶部维护一张 `_API_CAPS` 能力表（首次真机探测后缓存落盘），
> 把"本机哪些 API 可用/哪些抛异常"记下来，避免每次建模都白吃几十个 com_error。

---

## 6. 来源清单

**本机官方帮助（CHM，SW2025 SP5.0 安装目录自带，离线可查）**
- `SolidWorks.Interop.sldworks~...IModelDoc2~SaveBMP.html`
- `SolidWorks.Interop.sldworks~...IFeatureManager~FeatureCut4.html`（含 Obsolete 标记）
- `SolidWorks.Interop.sldworks~...ISketchSegment~Select2.html` / `~Select4.html`
- `SolidWorks.Interop.sldworks~...ISurface~IsPlane.html` / `~Identity.html`
- `SolidWorks.Interop.sldworks~...IModelDoc2~EditUndo2.html`
- `SolidWorks.Interop.swconst~...swFileSaveTypes_e.html`（BMP/PNG 枚举）

**本机类型库反射（权威签名来源，非网络）**
- `Z:\Program Files\SOLIDWORKS Corp2025\SOLIDWORKS\SolidWorks.Interop.sldworks.dll`
  （`.NET Reflection` 读 `IFeatureManager.FeatureCut4` 的参数个数 = 27）
- `Z:\Program Files\SOLIDWORKS Corp2025\SOLIDWORKS\sldworks.tlb`
  （`pythoncom.LoadTypeLib` 读各成员 `invkind`：FUNC / PROPGET）

**在线参考（交叉验证用，非结论来源）**
- [SolidWorks API Help — IFeatureManager](https://help.solidworks.com/2024/English/api/sldworksapi/SolidWorks.Interop.sldworks~SolidWorks.Interop.sldworks.IFeatureManager.html)
- [SolidWorks API Help — ISurface](https://help.solidworks.com/2024/English/api/sldworksapi/SolidWorks.Interop.sldworks~SolidWorks.Interop.sldworks.ISurface.html)
- [SolidWorks API Help — IModelDoc2](https://help.solidworks.com/2024/English/api/sldworksapi/SolidWorks.Interop.sldworks~SolidWorks.Interop.sldworks.IModelDoc2.html)
- [codestack — SolidWorks API 示例集](https://github.com/xarial/codestack)

> ⚠️ 本文所有"本机实测"数据来自 **SW2025 SP5.0 / pywin32 静态绑定（CDispatch）**。
> 换机器或换 SW 版本后请重跑一次探针复核 —— 尤其是 `FeatureCut4` 27 参
> 与 `ISketchContour.Select2` 这两条。

## 7. 落地顺序建议

1. **C02**（最小改动、收益直接）：`_face_is_planar` + `_has_curved_face_at` fail-open 到平面路径。
   → 沉孔马上能打，风险最低。
2. **C04**（独立、不碰几何）：`screenshot()` 改为 SaveBMP 优先。
   → 看图闭环不再依赖前台窗口。
3. **C01(A)**（一行级）：把 `Select4` 从自动重试链摘掉。
   → 消除"选段部分失败"。
4. **C03**（需真机复核）：Cut4 补到 27 参 + 参数位对齐 + 体积校验撤销。
   → 影响最大，务必先在本机跑一次 27 参探针确认。
5. **C01(B)**（中期重构）：多孔板统一走 `extrude_with_holes`。

> 前 3 条都可以在**没有本机 SW 实测**的情况下先落地（纯静态/纯逻辑改动），
> 第 4、5 条建议保留真机回归（skip-if-no-SW）。

