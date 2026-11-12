# SolidWorks 2025 + pywin32 晚期绑定建配合：可落地技术结论

> 环境：SW2025（`Z:\Program Files\SOLIDWORKS Corp2025`）、Python + pywin32、`win32com.client.Dispatch`（无 makepy）。
> 本文每条结论都附来源；标 **【推断】** 的是我基于机制推理、需现场验证的，标 **【未找到官方确认】** 的是查不到文档的。

---

## 一、结论速查表

| # | 问题 | 结论 | 推荐做法 |
|---|---|---|---|
| 1 | AddMate5 签名 | **15 个参数**，第 15 个 `ErrorStatus` 是 **ByRef `System.Int32`**（`out`）。前 14 个全 ByVal：`MateType(int), Align(int), Flip(bool), Distance(double), DistAbsUpper(double), DistAbsLower(double), GearNumer(double), GearDenom(double), Angle(double), AngleAbsUpper(double), AngleAbsLower(double), ForPositioningOnly(bool), LockRotation(bool), WidthMateOption(int)`，返回 `Mate2` | 签名本身没错——**错在第 15 个参数必须传 `win32com.client.VARIANT(pythoncom.VT_BYREF\|pythoncom.VT_I4, 0)`**，传裸 `0` 就报 `Type mismatch (-2147352571)` |
| 1b | AddMate5 是否还该用 | **官方 API Help 已标注 `Obsolete. Superseded by IAssemblyDoc::CreateMate`**（2026/2027 版页面明写；2025 版页面被 Akamai 挡了抓不到，见「不确定」） | 新代码不要再用 AddMate5；但**它并没有被移除**，`VARIANT(VT_BYREF\|VT_I4)` 修好后依然可用（有 SW2025 实测通过的报告） |
| 2 | 现代写法 | **`AssemblyDoc.CreateMateData(Type) -> IMateFeatureData`，转型成具体接口设属性，再 `AssemblyDoc.CreateMate(MateData) -> IFeature`**。官方 `CreateMate` Remarks 就是这个三步流程 | **`CreateMateData2` 不存在——【未找到官方确认】，不要用**（官方只有 `CreateMateData`，SW2018 起） |
| 2b | IDispatch 指针怎么传 | `MateData.EntitiesToMate` 是 `System.Object`（变体数组）。**不要传 Python list**，要传 `VARIANT(VT_ARRAY\|VT_DISPATCH, (ent1, ent2))` | 或者干脆**不设 `EntitiesToMate`，改用 `SelectByID2(..., Mark=1)` 预选**（官方 Remarks 明确支持，且这是最省事、最不容易错的路径） |
| 3 | SelectByID2 用法 | `Name` 格式 **`<实体名>@<零部件实例名>@<装配体名>`**（实例名带 `-1/-2` 后缀，不是零件文件名）。`Append=False` 起新选择集、`Append=True` 追加；**配合实体的 Mark 全部用 `1`**（官方 AddMate5 Remarks：`selectbyid2 with mark = 1`） | 每次建配合前 `ClearSelection2(True)`；第 8 个参数 `Callout` **必须传 `VARIANT(VT_DISPATCH, None)`，不能传 `None`** |
| 3b | 为什么 `GetSelectedObjectCount2` 是 0 | **两个最常见原因，都不是"没选中"**：① **`Mark` 传了 `0`，但选择时用了 `Mark=1`** —— 官方定义 `0 = 只数没有 mark 的选择`，`-1 = 全部`，所以必然返回 0；② `SelectByID2` 的 `Callout` 传了 `None` 直接抛 type mismatch，选择压根没发生（异常被吞了就觉得"选了但没选中"） | **统计一律用 `GetSelectedObjectCount2(-1)`**；再逐个 `GetSelectedObject6(i, -1)` 做 None 检查 |
| 4 | 早期绑定能否解决 | **能。** `gencache.EnsureDispatch` 会按类型库生成包装，ByRef 参数自动变成"返回值解包"，`AddMate5` 会直接 `mate, err = ...` 双返回值，不匹配问题消失 | 但**代价是 gencache 脆弱**：SW 升级/换版本 → `gen_py` 缓存过期 → `CLSIDToPackageMap` 之类 AttributeError。清理：删 `%TEMP%\gen_py`（或 `%LOCALAPPDATA%\Temp\gen_py`），必要时再删 `sys.modules` 里 `win32com.gen_py.*` |
| 5 | 宏替代路径 | **可行且推荐做兜底。** `ISldWorks.RunMacro2(FilePathName, ModuleName, ProcedureName, Options, Error)` —— 5 个参数，`Error` 是 ByRef `VT_I4`，`Options` 取 `swRunMacroOption_e`（`swRunMacroDefault=0`、`swRunMacroUnloadAfterRun=1`） | **三个必踩坑**：① `Error` 必须 `VARIANT(VT_BYREF\|VT_I4, 0)`；② `ModuleName` 是 **`<文件名>1`**（SW 自动加 1，传文件名主干会得到 error **22 InvalidIndex**）；③ `.swp` **必须是二进制 OLE 复合文档**，手写文本改名 `.swp` 会弹 `Cannot open` 模态框并**无限期卡死 COM 线程** |
| 6 | 干涉检查 | `IAssemblyDoc.InterferenceDetectionManager` 拿 `IInterferenceDetectionMgr`；**`GetInterferenceCount()` 是普通返回值，才是可靠的"0"**；`GetInterferences()` 返 `IInterference` 数组；`Done()` 必须调，否则装配体停在干涉显示模式 | `TreatCoincidenceAsInterference=False`（面贴面不算干涉）、`IncludeMultibodyPartInterferences=True`；**不要用 `ToolsCheckInterference2`**——晚期绑定下两个 ByRef out 参数无法填充，永远读回 `None`，等于"永远无干涉" |

---

## 二、可运行代码片段

### 2a) 晚期绑定：两平面重合 + 两圆柱面同心（完整最小示例）

```python
# -*- coding: utf-8 -*-
"""
SW2025 + pywin32 纯晚期绑定（Dispatch，无 makepy）建配合的最小可运行骨架。
关键点全部加了注释：ByRef 输出参数、Callout 空指针、Mark、计数用 -1。
"""
import pythoncom
import win32com.client as win32

# ---- 常量（来自 swconst 类型库；晚期绑定下没法 import，直接写字面值最稳）----
swMateCOINCIDENT   = 0
swMateCONCENTRIC   = 1
swMateAlignALIGNED      = 0
swMateAlignANTI_ALIGNED = 1
swMateAlignCLOSEST      = 2
swAddMateError_NoError  = 1        # 注意：成功是 1，不是 0！0 是 ErrorUknown
SW_DOC_ASSEMBLY    = 2


def _byref_i4(v=0):
    """给所有 ByRef System.Int32 输出参数用。传裸 0 会报 DISP_E_TYPEMISMATCH。"""
    return win32.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, v)


def _null_dispatch():
    """给 Callout / SelectData 这类 IDispatch* 参数用。传 None 会报 Type mismatch。"""
    return win32.VARIANT(pythoncom.VT_DISPATCH, None)


def _dispatch_array(objs):
    """给 EntitiesToMate 这类 'Object 变体数组' 用。传 Python list 不认。"""
    return win32.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_DISPATCH, tuple(objs))


def add_mate_late_bound(assy, mate_type, align, *, flip=False,
                        distance=0.0, angle=0.0, lock_rotation=False):
    """
    晚期绑定调用 AddMate5。
    前置条件：调用前已经用 SelectByID2(..., Append, Mark=1) 选好了实体。
    返回 (mate, error_status)；mate 为 None 表示失败。
    """
    err = _byref_i4(0)                     # ← 15 号参数：ByRef int

    result = assy.AddMate5(
        mate_type,        # 1  MateTypeFromEnum    swMateType_e
        align,            # 2  AlignFromEnum       swMateAlign_e
        flip,             # 3  Flip                仅 swMateDISTANCE 有效
        distance,         # 4  Distance            仅 swMateDISTANCE
        0.0,              # 5  DistanceAbsUpperLimit
        0.0,              # 6  DistanceAbsLowerLimit
        0.0,              # 7  GearRatioNumerator  仅 swMateGEAR
        0.0,              # 8  GearRatioDenominator
        angle,            # 9  Angle               仅 swMateANGLE（弧度）
        0.0,              # 10 AngleAbsUpperLimit
        0.0,              # 11 AngleAbsLowerLimit
        False,            # 12 ForPositioningOnly  只摆位不建配合
        lock_rotation,    # 13 LockRotation
        0,                # 14 WidthMateOption     仅 swMateWIDTH（0=Centered）
        err,              # 15 ErrorStatus         ← ByRef，必须是 VARIANT
    )
    # ★ 晚期绑定下 ByRef 结果在 err.value 里，不在返回值里！
    return result, err.value


def mate_two_faces(assy_doc, ent1, ent2, mate_type, align):
    """先清选择集 → 两个实体都打 Mark=1 → 建配合 → 再清。"""
    model = assy_doc
    model.ClearSelection2(True)            # ★ 必须先清，否则残留选择会污染 mark

    model.Extension.SelectByID2(
        ent1,                              # "<面名>@<实例名>@<装配体名>"，或 ""+坐标
        "FACE", 0, 0, 0,
        False,                             # Append=False → 替换选择集
        1,                                 # ★ Mark=1：配合实体的官方 mark
        _null_dispatch(),                  # ★ Callout，必须是 VT_DISPATCH 空指针
        0,                                 # swSelectOptionDefault
    )
    model.Extension.SelectByID2(
        ent2, "FACE", 0, 0, 0,
        True,                              # Append=True → 追加
        1,                                 # ★ 第二个实体同样是 1（不是 2！）
        _null_dispatch(),
        0,
    )

    n = model.SelectionManager.GetSelectedObjectCount2(-1)   # ★ 必须 -1，见下文说明
    print("selected:", n)
    assert n == 2, "选择失败：检查名字格式 / 组件是否轻量化 / Callout 是否为空指针"

    mate, err = add_mate_late_bound(assy_doc, mate_type, align)
    print("mate:", mate, "err:", err)      # 成功 err == swAddMateError_NoError(1)
    model.ClearSelection2(True)
    return mate, err


# ------------------------------ 用法 ------------------------------
def main():
    sw = win32.Dispatch("SldWorks.Application")   # 晚期绑定；已启动就用 GetActiveObject
    sw.Visible = True
    model = sw.ActiveDoc
    if model is None or model.GetType != SW_DOC_ASSEMBLY:   # 零参方法晚期绑定按属性读
        raise RuntimeError("先打开一个装配体")

    assy = model          # AssemblyDoc 与 ModelDoc2 同源，晚期绑定直接复用即可

    # 1) 两平面重合（贴合：法线相反 → ANTI_ALIGNED；同向 → ALIGNED）
    mate_two_faces(assy,
                   "Plane1@Part1-1@Assem1",      # 实体@实例@装配体
                   "Plane1@Part2-1@Assem1",
                   swMateCOINCIDENT, swMateAlignANTI_ALIGNED)

    # 2) 两圆柱面同心
    mate_two_faces(assy,
                   "Face2@Part1-1@Assem1",
                   "Face2@Part2-1@Assem1",
                   swMateCONCENTRIC, swMateAlignALIGNED)

    model.EditRebuild3()


if __name__ == "__main__":
    main()
```

### 2a-bis) 更推荐的 `CreateMate` 路径（同样晚期绑定）

```python
import pythoncom, win32com.client as win32

def mate_by_createmate(assy, ent1, ent2, mate_type=0, align=1):
    """
    CreateMateData -> 设属性 -> CreateMate。官方推荐路径，且绕开 AddMate5 的 ByRef。
    两条子路线，任选其一：
    """
    model = assy
    # --- 路线 A（推荐）：预选 + EntitiesToMate，用 mark=1 让 SW 自己解析
    model.ClearSelection2(True)
    null_callout = win32.VARIANT(pythoncom.VT_DISPATCH, None)
    model.Extension.SelectByID2(ent1, "FACE", 0, 0, 0, False, 1, null_callout, 0)
    model.Extension.SelectByID2(ent2, "FACE", 0, 0, 0, True,  1, null_callout, 0)

    md = assy.CreateMateData(mate_type)      # 0=COINCIDENT 1=CONCENTRIC 5=DISTANCE ...
    # CreateMateData 返回 System.Object，晚期绑定下直接用，属性名照官方接口写：
    md.MateAlignment = align                 # swMateAlign_e

    # 路线 B：显式塞实体数组（需要真 IDispatch 数组，不是 list）
    sel = model.SelectionManager
    e1 = sel.GetSelectedObject6(1, -1)
    e2 = sel.GetSelectedObject6(2, -1)
    if e1 is None or e2 is None:
        raise RuntimeError("GetSelectedObject6 拿到空——选择其实是失败的")
    md.EntitiesToMate = win32.VARIANT(
        pythoncom.VT_ARRAY | pythoncom.VT_DISPATCH, (e1, e2))

    feat = assy.CreateMate(md)               # 返回 IFeature
    model.ClearSelection2(True)
    return feat
```

> 参照官方 `Create Standard Mates Example (VBA)` 的写法：`Set ConcMateData = Part.CreateMateData(1)` → `ConcMateData.EntitiesToMate = (EntitiesToMateVar)` → `ConcMateData.MateAlignment = 1` → `Set MateFeature = Part.CreateMate(ConcMateData)`。VBA 里 `EntitiesToMateVar = EntitiesToMate`（Object 数组赋值给 Variant）这一步，在 pywin32 里对应的就是上面那个 `VT_ARRAY \| VT_DISPATCH` VARIANT。

### 2b) gencache 早期绑定初始化 + 失败回退

```python
import os, re, sys, shutil, glob
import pythoncom
import win32com.client as win32


def _purge_gen_py():
    """彻底清掉 makepy 缓存：内存里的模块 + 磁盘上的 gen_py 目录。"""
    for name in [m for m in sys.modules if re.match(r"win32com\.gen_py\..+", m)]:
        del sys.modules[name]
    candidates = [
        os.path.join(os.environ.get("TEMP", ""), "gen_py"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Temp", "gen_py"),
    ]
    for path in {p for p in candidates if p and os.path.isdir(p)}:
        try:
            shutil.rmtree(path)
            print("removed", path)
        except OSError as exc:
            print("could not remove", path, exc)


def connect_solidworks(prefer_early_binding=True, visible=True):
    """
    返回 (app, mode)。mode ∈ {"early", "late"}。
    早期绑定能用就用（ByRef 参数自动解包，最好写）；
    任何异常都回退到 Dispatch 晚期绑定，保证脚本不会因为缓存坏了直接死掉。
    """
    win32.gencache.is_readonly = False        # 允许往 site-packages 写缓存
    app = None
    mode = "late"

    if prefer_early_binding:
        for attempt in (1, 2):
            try:
                app = win32.gencache.EnsureDispatch("SldWorks.Application")
                mode = "early"
                break
            except Exception as exc:          # com_error / AttributeError(CLSIDToPackageMap) / ...
                print(f"EnsureDispatch failed (attempt {attempt}): {exc!r}")
                if attempt == 1:
                    _purge_gen_py()           # 清缓存后重试一次
        else:
            print("early binding unavailable -> falling back to late binding")

    if app is None:
        try:
            app = win32.GetActiveObject("SldWorks.Application")   # 复用已开进程
        except pythoncom.com_error:
            app = win32.Dispatch("SldWorks.Application")
        mode = "late"

    if visible:
        try:
            app.Visible = True
        except Exception:
            pass
    return app, mode


# ---- 早期绑定下 AddMate5 的写法（ByRef 变成"多返回值"，不需要 VARIANT）----
def add_mate_early_bound(assy):
    mate, err = assy.AddMate5(
        0, 0, False,              # 面重合、ALIGNED、不翻转
        0.0, 0.0, 0.0, 0.0, 0.0,  # distance / 上下限 / 齿轮比
        0.0, 0.0, 0.0,            # angle / 上下限
        False, False, 0,          # ForPositioningOnly / LockRotation / WidthMateOption
    )                             # ★ 没有第 15 个参数，ByRef 结果变成第二个返回值
    return mate, err
```

> 已知坑：`EnsureDispatch` 之后如果 **又** 用 `Dispatch` 拿同一个对象，会撞上
> `AttributeError: module 'win32com.gen_py.<GUID>x0xNx0' has no attribute '...'`——因为同进程里
> gencache 已强制早期绑定语义。**一个脚本里只选一种模式**，不要混。SW 升级（2024→2025）后
> 类型库版本号变（`x0x33x0` 之类），旧缓存不匹配也会报错 → 删 `%TEMP%\gen_py` 重来。

### 2c) RunMacro2 调 VBA 宏（兜底路径）

```python
import pythoncom
import win32com.client as win32

swRunMacroDefault        = 0
swRunMacroUnloadAfterRun = 1


def run_sw_macro(sw_app, swp_path, procedure="main", module_name=None, unload=True):
    """
    跑一个 .swp VBA 宏。

    三个必踩坑：
      1) Error 是 ByRef VT_I4 输出参数，传裸 0 → Type mismatch (arg index 5)
      2) ModuleName 是 SW 自动命名的 "<文件名>1"，不是文件名主干
         （Tools > Macro > New 建 sample_macro.swp → 模块名 sample_macro1）
         传错得到 swRunMacroError = 22 (InvalidIndex)
      3) .swp 必须是二进制 OLE 复合文档；手写文本改名 .swp 会弹 "Cannot open"
         模态框，把 COM STA 线程无限期挂住
    """
    if module_name is None:
        stem = swp_path.rsplit("\\", 1)[-1].rsplit(".", 1)[0]
        module_name = stem + "1"          # ← 见坑 2

    err = win32.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)   # ← 见坑 1
    ok = sw_app.RunMacro2(
        swp_path,                          # FilePathName（.swp 或 .NET dll）
        module_name,                       # ModuleName
        procedure,                         # ProcedureName
        swRunMacroUnloadAfterRun if unload else swRunMacroDefault,   # Options
        err,                               # Error（ByRef）
    )
    code = err.value
    if not ok or code != 0:
        raise RuntimeError(f"RunMacro2 failed: ok={ok} swRunMacroError={code}")
    return True


# 用法：宏里做配合（宏在 SW 自己的进程内跑，等于早期绑定，ByRef 全自动）
# .swp 内容要点（VBA）：
#   Sub main()
#       Dim swApp As SldWorks.SldWorks, swModel As ModelDoc2, swAssy As AssemblyDoc
#       Dim err As Long, m As Mate2
#       Set swApp = Application.SldWorks
#       Set swModel = swApp.ActiveDoc
#       Set swAssy = swModel
#       swModel.ClearSelection2 True
#       swModel.Extension.SelectByID2 "Plane1@Part1-1@Assem1", "PLANE", 0,0,0, False, 1, Nothing, 0
#       swModel.Extension.SelectByID2 "Plane1@Part2-1@Assem1", "PLANE", 0,0,0, True,  1, Nothing, 0
#       Set m = swAssy.AddMate5(0, 1, False, 0,0,0, 0,0, 0,0,0, False, False, 0, err)
#       If m Is Nothing Then MsgBox "mate failed: " & err
#       swModel.EditRebuild3
#   End Sub
```

### 2d) 干涉检查（退化路径的正确验证手段）

```python
def check_interference(assy, treat_coincident=False):
    """
    用 GetInterferenceCount 判定，不要用 ToolsCheckInterference2。
    ToolsCheckInterference2 在晚期绑定下两个 ByRef out 参数永远填不进去，
    读回来是 None，会把"有干涉"误报成"无干涉"。
    """
    mgr = assy.InterferenceDetectionManager
    mgr.TreatCoincidenceAsInterference = treat_coincident      # 面贴面是否算干涉
    mgr.IncludeMultibodyPartInterferences = True
    mgr.TreatSubAssembliesAsComponents = True
    try:
        count = int(mgr.GetInterferenceCount() or 0)          # 普通返回值，可当测量值
        items = mgr.GetInterferences() if count else None     # IInterference 数组/None
        out = []
        if items:
            arr = items if isinstance(items, (list, tuple)) else (items,)
            for it in arr:
                out.append({
                    "volume_m3": it.Volume,          # 重叠体积（属性，不带括号）
                    "count": it.GetComponentCount(), # 方法，带括号
                    # 注意：是 .Components 属性，不是 .GetComponents()
                    "comps": [c.Name2 for c in (it.Components or ())],
                })
        return count, out
    finally:
        mgr.Done()          # ★ 必须调，否则装配体卡在干涉显示模式
```

---

## 三、为什么会"参数类型不匹配"——机制说明

COM 里 `ByRef` 的 `System.Int32` 是 `VT_BYREF | VT_I4`。pywin32 晚期绑定走的是
`IDispatch::Invoke` + `dynamic.py` 的通用路径，没有类型库信息，所以**无法推断哪个参数是
输出参数**，只能照你传的 VARIANT 类型原样打包。你传 Python `int` → 打包成 `VT_I4`（值传递），
SW 的类型库期待 `VT_BYREF|VT_I4`，`Invoke` 返回 `DISP_E_TYPEMISMATCH (-2147352571)`，
pywin32 再包装成 `TypeError: 参数个数或类型错误`。

同一机制也解释了 `SelectByID2` 的报错（arg index 8）：`Callout` 是 `IDispatch*`，
Python `None` 打包成 `VT_NULL`，SW 只认 `VT_DISPATCH`（NULL 指针）。修法是
`win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)` —— 注意 `pythoncom.Nothing` 有的
SW 版本认、有的不认，`VARIANT(VT_DISPATCH, None)` 才是稳的写法。

而 `GetSelectedObjectCount2(0)` 返回 0 是**文档写死的语义**：
`-1 = 全部选择，0 = 只数没有 mark 的选择`。你用 `Mark=1` 选的东西，用 `0` 去数当然是 0。

---

## 四、证据与链接

**官方 API Help（help.solidworks.com）**

- AddMate5 完整签名（15 参数，`ByRef ErrorStatus`）：
  <https://help.solidworks.com/2017/English/api/sldworksapi/SolidWorks.Interop.sldworks~SolidWorks.Interop.sldworks.IAssemblyDoc~AddMate5.html>
- AddMate5 **标注 Obsolete / Superseded by CreateMate**（2026 版页面）：
  <https://help.solidworks.com/2026/english/api/sldworksapi/SolidWorks.Interop.sldworks~SolidWorks.Interop.sldworks.IAssemblyDoc~AddMate5.html>
- AddMate5 参数逐条含义（`Flip` 仅 DISTANCE 有效、`WidthMateOption` 仅 WIDTH 有效）：
  <https://help.solidworks.com/2027/english/api/sldworksapi/SOLIDWORKS.Interop.sldworks~SOLIDWORKS.Interop.sldworks.IAssemblyDoc~AddMate5.html>
- CreateMate（三步流程 + 支持的 mate-specific 接口清单 + 哪些支持预选）：
  <https://help.solidworks.com/2020/English/api/sldworksapi/SolidWorks.Interop.sldworks~SolidWorks.Interop.sldworks.IAssemblyDoc~CreateMate.html>
- CreateMateData（SW2018 FCS 起；`Type` 允许的枚举值清单）：
  <https://help.solidworks.com/2025/english/api/sldworksapi/SolidWorks.Interop.sldworks~SolidWorks.Interop.sldworks.IAssemblyDoc~CreateMateData.html>
- CreateMateData（2023 版，同一签名）：
  <https://help.solidworks.com/2023/English/api/sldworksapi/SolidWorks.Interop.sldworks~SolidWorks.Interop.sldworks.IAssemblyDoc~CreateMateData.html>
- `ICoincidentMateFeatureData.EntitiesToMate`（**"可以用 SelectByID2 + Mark=1 预选"** 的明文依据 + 合法实体组合表）：
  <https://help.solidworks.com/2027/english/api/sldworksapi/SolidWorks.Interop.sldworks~SolidWorks.Interop.sldworks.ICoincidentMateFeatureData~EntitiesToMate.html>
- `ICoincidentMateFeatureData.MateAlignment`（SW2019 FCS）：
  <https://help.solidworks.com/2026/english/api/sldworksapi/SolidWorks.Interop.sldworks~SolidWorks.Interop.sldworks.ICoincidentMateFeatureData~MateAlignment.html>
- `IHingeMateFeatureData.EntitiesToMate`（明文写出 **Mark=1 同心 / 32768 重合 / 65536 角度**）：
  <https://help.solidworks.com/2027/english/api/sldworksapi/SolidWorks.Interop.sldworks~SolidWorks.Interop.sldworks.IHingeMateFeatureData~EntitiesToMate.html>
- `ISelectionMgr.GetSelectedObjectCount2`（**Mark `-1` = 全部、`0` = 无 mark**）：
  <https://help.solidworks.com/2027/english/api/sldworksapi/SolidWorks.Interop.sldworks~SolidWorks.Interop.sldworks.ISelectionMgr~GetSelectedObjectCount2.html>
- `swMateType_e`（COINCIDENT=0, CONCENTRIC=1, DISTANCE=5, ANGLE=6, LOCK=16, WIDTH=11 …）：
  <https://help.solidworks.com/2025/English/api/swconst/SolidWorks.Interop.swconst~SolidWorks.Interop.swconst.swMateType_e.html>
- `swAddMateError_e`（**NoError=1**, ErrorUknown=0, IncorrectSelections=4, OverDefined=5）：
  <https://help.solidworks.com/2027/english/api/swconst/SolidWorks.Interop.swconst~SolidWorks.Interop.swconst.swAddMateError_e.html>
- `swMateAlign_e`（ALIGNED=0 / ANTI_ALIGNED=1 / CLOSEST=2）：见上面 Get Mates 示例中的枚举节
- `ISldWorks.RunMacro2`（5 参数、`ByRef Error`、`swRunMacroOption_e`、.NET DLL 用法）：
  <https://help.solidworks.com/2023/english/api/sldworksapi/SOLIDWORKS.Interop.sldworks~SOLIDWORKS.Interop.sldworks.ISldWorks~RunMacro2.html>
- `swRunMacroOption_e`（`swRunMacroDefault=0`, `swRunMacroUnloadAfterRun=1`）：
  <https://help.solidworks.com/2023/english/api/swconst/SolidWorks.Interop.swconst~SolidWorks.Interop.swconst.swRunMacroOption_e.html>
- `IInterferenceDetectionMgr` 方法清单（`Done/GetInterferenceCount/GetInterferences/ExportResults`）：
  <https://help.solidworks.com/2022/english/api/sldworksapi/SolidWorks.Interop.sldworks~SolidWorks.Interop.sldworks.IInterferenceDetectionMgr_methods.html>
- `IInterferenceDetectionMgr` 属性（`TreatCoincidenceAsInterference` 等）：
  <https://help.solidworks.com/2010/english/api/sldworksapi/SolidWorks.Interop.sldworks~SolidWorks.Interop.sldworks.IInterferenceDetectionMgr_members.html>
- 官方 `Create Standard Mates Example (VBA)`（CreateMateData→EntitiesToMate→MateAlignment→CreateMate 的原始写法）：
  <https://help.solidworks.com/2024/english/api/sldworksapi/Create_Standard_Mates_Example_VB.htm>
- 官方 `Add and Mate Component Example (C#)`（`AddMate5(..., out mateError)` 的调用形态）：
  <https://help.solidworks.com/2016/english/api/sldworksapi/Add_Component_and_Mate_Example_CSharp.htm>

**社区 / 实战（关键，因为这些坑官方文档不写）**

- ★ **SW2025 晚期绑定实测坑清单**（SelectByID2 的 Callout 必须 `VT_DISPATCH` null、AddMate5 在 SW2025 可用且 ByRef 要 VARIANT、`ToolsCheckInterference2` 晚期绑定必坏、`IInterference.Components` 是属性、RunMacro2 的模块名/二进制 .swp/error 22、gen_py 版本号 `x0x33x0`）：
  <https://andrewbartels1.com/SolidworksMCP-python/agents/com-api-pitfalls>
  原始 markdown：<https://raw.githubusercontent.com/andrewbartels1/SolidworksMCP-python/main/docs/agents/com-api-pitfalls.md>
- ★ **AddMate3/AddMate5 的 `longstatus` 在 pywin32 下报 Type mismatch，正解是 `VARIANT(VT_BYREF|VT_I4, 1)`**：
  <https://forum.cadmunity.com/t/struggling-with-swaddmateerror-e-enumeration-in-python/1091/2>
- ★ **SW2025 + Python `CreateMate` 建配合失败的问题帖**（SelectByRay/SelectByID2 都返回 True，配合仍建不出来——注意官方示例用 SelectByRay 时 **Append 是 `False` 后接 `True`、mark 是 `1` 和 `2`**，帖子里的写法与官方不一致）：
  <https://3dswym.3dexperience.3ds.com/question/solidworks-user-forum/issue-creating-mates-with-python-api-in-solidworks-2025-createmate-instead-of-addmate5_B5p086UVQ9iDnLofV_s8Kw>
- **byRef 参数在 win32com 下的两种做法**（早期绑定解包成多返回值 vs 手搓 VARIANT；且"不 makepy 也能用 VARIANT 走通"）：
  <https://mail.python.org/archives/list/python-win32@python.org/thread/37VDNENPBJRYL7GWW3MKNQLK2XJTCKDC/>
- **SelectByID2 的 `Nothing` 在 pywin32 里怎么写**（`VARIANT(VT_DISPATCH, None)`；有 `pythoncom.Nothing` 也能通的说法，稳的是前者）：
  <https://stackoverflow.com/questions/19454804/pointer-callout-for-selectbyid2>
  <https://stackoverflow.com/questions/41175007/pass-the-variable-nothing-using-pythons-win32com>
- **gencache 损坏清理**（`CLSIDToPackageMap` AttributeError → 删 `%TEMP%\gen_py`；含重试函数）：
  <https://stackoverflow.com/questions/33267002/why-am-i-suddenly-getting-a-no-attribute-clsidtopackagemap-error-with-win32com>
- **gencache 早期绑定 vs Dispatch 的模式差异**（大小写敏感、集合索引 0/1 起）：
  <https://blog.csdn.net/liyuanjunfrank/article/details/127849598>
- **选择 mark 的官方设计意图**（配合用 1；mark 用 2 的幂）：
  <https://cadbooster.com/nl/how-to-work-with-selections-in-the-solidworks-api/>
- **配合 mark 的实践总结 + `EntityName@ComponentName@AssemblyName` 名字格式 + 必须 Append=True**：
  <https://samernoufal.com/resources/solidworks-add-mates-api>
- **AddMate5 用 mark=1 预选、建完 ClearSelection2、无预选则 `swAddMateError_IncorrectSelections`**：
  <https://www.west.cn/www/info/31052-1.htm>
- **`CreateMate` 建"单圆柱滑块"宽度配合会失败、而 AddMate5 能成**（说明 CreateMate 并非万能，需保留 AddMate5 兜底）：
  <https://forum.mycad.visiativ.com/t/probleme-a-la-creation-de-contrainte-glissiere/114188/2>
- **`SelectByID2` 在装配体里选到顶层同名特征的问题 + `特征名@实例名@装配体名` 严格格式**：
  <https://www.volcengine.com/article/1616824>
- **轻量化零部件导致选择集为空 / AddMate 返回 -1，需 `ResolveAllLightWeightComponents`**：
  <https://jiaqiwang969.github.io/solidworks-GPT/zh-Hans/docs/codestack/solidworks-api/troubleshooting/macros/assembly-drawing-lightweight-components/>
- **CodeStack 官方文章：配合要按 mate 类型给不同 mark，拿不准就录宏看 SW 自己用什么 mark**：
  <https://www.codestack.net/solidworks-api/document/assembly/mates/>

---

## 五、不确定 / 需现场验证的地方

1. **【不确定，重要】AddMate5 在 SW2025 是否已被标记 Obsolete。**
   `help.solidworks.com/2026/.../AddMate5.html` 明确写 `Obsolete. Superseded by IAssemblyDoc::CreateMate.`，
   2025 版页面被 Akamai（Access Denied）挡住抓不到。**我没有 SW2025 本地 API Help 的直接证据。**
   → 现场验证：在 `Z:\Program Files\SOLIDWORKS Corp2025\` 下找 `sldworksapi.chm` / 在线 help 查
   `IAssemblyDoc::AddMate5` 是否带 Obsolete 标记。**但这不影响修复**——Obsolete ≠ 移除，
   有 SW2025 实测通过的报告（上面 andrewbartels1 的坑 #17），配 `VARIANT(VT_BYREF|VT_I4)` 就能用。

2. **【未找到官方确认】`CreateMateData2` / `AssemblyDoc.CreateMate2`。**
   我多次检索官方 API Help，**只存在 `CreateMateData`、`CreateMate`**，没有 `CreateMateData2`。
   （顺带：网上有中文博客提 `CreateMate2(swMateCOINCIDENT, 0, ...)` 这种"依赖选择集"的写法，官方文档里查不到这个签名，**疑似虚构，别信**。）
   → 结论：只用 `CreateMateData` + `CreateMate`。

3. **【推断】`GetSelectedObjectCount2` 返回 0 的具体成因是哪个。**
   我给了两个高可信原因（Mark 传 0 vs 用 1 选；Callout 传 None 导致异常被吞）。
   但**你现场到底是哪个，我没有你的代码，无法确定**。
   → 现场验证：先把它改成 `GetSelectedObjectCount2(-1)` 再看；若还是 0，就在 `SelectByID2` 前后打日志，
   打印它的返回值 + `ClearSelection2` 前后计数，确认异常是否被 `except` 吞掉。

4. **【推断】`MateData.EntitiesToMate` 能否直接吃 Python list。**
   上面的 SW2025 问题帖里两种写法（`VARIANT(VT_ARRAY|VT_DISPATCH, (...))` 和裸 `[ent1, ent2]`）都"没建出配合"，
   说明**两种可能都有问题，或者问题根本不在这里**。官方 VBA 是"Object 数组赋给 Variant"，
   对应 pywin32 的形式应是有类型的 SAFEARRAY。我推荐 **优先用"预选 + Mark=1"路线**，绕开数组封送。

5. **【推断】`CreateMateData` 返回值在晚期绑定下的属性可见性。**
   官方返回类型是 `System.Object`，实际是 `ICoincidentMateFeatureData` 之类的具体接口。
   晚期绑定下 pywin32 没有类型信息，**能否直接 `.MateAlignment = ...` 而不先 flag 接口，需要实测**。
   若报 `DISP_E_MEMBERNOTFOUND` / 赋了不生效，可参考 andrewbartels1 项目里的做法：
   先用类型库 flag 该对象（他们用 `gencache.EnsureModule('{83A33D31-27C5-11CE-BFD4-00400513BB57}', 0, 0, 33)` 生成包装再 cast）。

6. **【推断】`SelectByID2` 的 `Name` 用实体名 vs 用 `""` + 坐标。**
   官方两种都支持。但坐标选择是**"沿视线方向的屏幕拾取"**，不是"最近的实体"——
   在 andrewbartels1 的坑 #34 里，一块 12mm 板上 z=-6 的边选到了 z=+6 的边。
   → 装配体里建议**优先用名字**；名字拿不准时，用 `IBody2.GetFaces()` 遍历 + `IFace2.GetClosestPointOn`
   自己算距离再 `Select4`，比坐标盲选可靠。

7. **【推断】`swAddMateError_NoError = 1`。**
   官方枚举页明确写 `NoError 1`，但有多个第三方教程（thecadcoder 等）在代码里传 `swAddMateError_ErrorUknown (0)`
   当"初始值"，这容易让人误以为 0 是成功。**判断成功请看 `Mate2 Is Nothing` / `err.value == 1`，两个都查。**

8. **【未找到官方确认】`.swp` 宏的 ModuleName 规则"文件名+1"。**
   这是 andrewbartels1 项目在 SW2026 上实测的结论（error 22 的成因），官方 RunMacro2 示例里
   `RunMacroSub.swp → RunMacroSub1` 与之吻合，但官方没有把这条规则写成文档。
   → 稳妥做法：先 `swApp.GetMacroMethods(swp_path, 0)` 拿到 `"Module.Proc"` 字符串数组，
   拆开即得真实模块名（官方 `GetMacroMethods` 就是干这个的），别靠猜。

9. **【未验证】早期绑定在本机能否一次成功。**
   `Z:\` 是网络/映射盘安装路径（`SOLIDWORKS Corp2025`，非标准目录名，可能是绿色版/改版安装），
   类型库注册可能不完整，`EnsureDispatch` 可能直接失败。→ 现场先单独跑 2b 的 `connect_solidworks()`，
   看返回的 `mode` 是 `early` 还是 `late`，再决定主路径。
