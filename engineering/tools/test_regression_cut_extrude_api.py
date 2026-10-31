# -*- coding: utf-8 -*-
"""回归测试：切除/拉伸 API 参数布局漂移 + do_cut 状态依赖型选择错乱（R5）

★ 锁定的真实事故（同一场 15 小时会话，本任务最大坑，100 分钟零产物）：
  1. FeatureCut4 有时返回 CDispatch 但**去料=0**，有时直接返回 None；
  2. FeatureCut3=26 参 / FeatureCut4=27 参，容易传错位；
  3. FeatureExtrusion3 的「切除」位在第 18、方向位 pos3；
  4. **cut(flip=True) 会切到补集**（实测 -30520.6 mm³）；
  5. 26 段闭合轮廓一次 cut 全部签名失败；
  6. 关键实证：两环同草图**净距 0.328mm → FeatureExtrusion3 返回 none/no_body**；
     净距 0.522 / 0.716 / 0.911 mm 全部 PASS。
  7. do_cut 状态依赖型选择错乱：同一调用在不同选择状态下结果不同。

★ 本测试钉死的契约（全部假 COM，不启动 SolidWorks）：
  A. 参数个数/顺序：Cut3=26 / Cut4=27，且与类型库逐位同名同序；
  B. 返回 None 必须显式报错（不许静默"换下一个签名"）；
  C. 体积读不到 ≠ 成功（旧实现量不出来就 return 成功）；
  D. flip 不得切补集：方向重试只动 Dir（第 3 位），Flip（第 2 位）恒 False；
  E. do_cut/extrude 必须【先清选择、再重建、再选轮廓】；
  F. 净距过小必须在调 API 之前给出可读原因。

依据（写死在断言里，防止有人"顺手改回去"）：
  【类型库】%LOCALAPPDATA%\Temp\gen_py\3.13\
            83A33D31-27C5-11CE-BFD4-00400513BB57x0x33x0.py
    · IFeatureManager.FeatureCut3       L22427-22443 → 26 参
    · IFeatureManager.FeatureCut4       L22446-22462 → 27 参（= Cut3 + OptimizeGeometry）
    · IFeatureManager.FeatureExtrusion3 L22597-22611 → 23 参（无任何切除位）
  【网页】SOLIDWORKS API Help「FeatureCut4 Method (IFeatureManager)」
    https://help.solidworks.com/2017/English/api/sldworksapi/SolidWorks.Interop.sldworks~SolidWorks.Interop.sldworks.IFeatureManager~FeatureCut4.html
    「Sd: True for a single-ended cut, false for a doubled-ended cut
      Flip: True to remove material outside of the profile of the flip side to cut
      Dir: True for Direction 1 to be opposite the default direction」
    —— 即 Flip = 切轮廓外侧（补集），Dir = 反转方向。这是本任务最关键的语义依据。
  【网页】SOLIDWORKS API Help「FeatureExtruRefSurface3」
    「Dchk1: True allows draft angle in first direction, false does not allow drafting」
    —— 第 8 位是拔模开关，不是方向。

⚠️ 全部用假 COM 对象 / monkeypatch：不 import 真 COM，不启动 SolidWorks。

运行：python test_regression_cut_extrude_api.py
退出码：0 = 全部通过；1 = 有失败
"""
import ast
import io
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ── stub pywin32（必须在 import swapi 之前）──────────────────────────────
for _n in ("pythoncom", "win32com", "win32com.client", "win32con"):
    sys.modules.setdefault(_n, types.ModuleType(_n))
sys.modules["win32com"].client = sys.modules["win32com.client"]
sys.modules["win32com.client"].VARIANT = lambda *a, **k: None
sys.modules["win32com.client"].dynamic = types.SimpleNamespace(
    Dispatch=lambda *a, **k: None)
sys.modules["win32com.client"].gencache = types.SimpleNamespace(
    EnsureDispatch=lambda *a, **k: None)
sys.modules["pythoncom"].VT_DISPATCH = 9
sys.modules["pythoncom"].VT_ARRAY = 8192
sys.modules["pythoncom"].VARIANT = lambda *a, **k: None

import swapi  # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [ok] %s" % label)
    else:
        FAIL += 1
        FAILURES.append(label + ("  <" + str(detail)[:400] + ">" if detail else ""))
        print("  [FAIL] %s  %s" % (label, detail))


def sec(t):
    print("")
    print("=" * 74)
    print(t)
    print("=" * 74)


SRC = io.open(os.path.join(HERE, "swapi.py"), encoding="utf-8").read()
TREE = ast.parse(SRC)


def _func(name):
    for node in ast.walk(TREE):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _source_of(name):
    fn = _func(name)
    return (ast.get_source_segment(SRC, fn) or "") if fn is not None else ""


# ══════════════════════════════════════════════════════════════════════
# 假 COM：够 cut()/extrude() 跑完，并记录每次 FeatureCut*/FeatureExtrusion3
#         的实参，供"参数个数/顺序"与"Flip 恒 False"断言使用。
# ══════════════════════════════════════════════════════════════════════
class FakeSketchSeg(object):
    def __init__(self, n):
        self._n = n

    def Select2(self, append, mark):
        return True


class FakeSketch(object):
    def __init__(self, name="Sketch1", nseg=1):
        self.Name = name
        self._segs = [FakeSketchSeg(i) for i in range(nseg)]

    @property
    def GetSketchSegments(self):
        return tuple(self._segs)


class FakeSelectionMgr(object):
    def __init__(self, doc):
        self._doc = doc

    def GetSelectedObjectCount2(self, mark=-1):
        return self._doc._sel_count

    def GetSelectedObjectType3(self, i, mark=-1):
        return 10


class FakeBody(object):
    def __init__(self, doc):
        self._doc = doc

    def GetBodyBox(self):
        return [-0.05, -0.04, -0.005, 0.05, 0.04, 0.005]

    def GetFaces(self):
        return tuple([object()] * 6)

    def GetEdges(self):
        return tuple([object()] * 12)

    def GetMassProperties(self, accuracy=0):
        return [0, 0, 0, self._doc._vol_m3, 0, 0]


class FakeFeatureManager(object):
    """记录每次特征 API 的实参，并模拟"返回对象但去料 0 / 返回 None"。"""

    def __init__(self, doc):
        self._doc = doc
        self.calls = []

    def _do(self, api, args, n_expected):
        self._doc.calls.append((api, tuple(args), n_expected))
        self.calls.append((api, tuple(args), n_expected))
        return self._doc.feature_result(api, args)

    def FeatureCut3(self, *args):
        return self._do("FeatureCut3", args, 26)

    def FeatureCut4(self, *args):
        return self._do("FeatureCut4", args, 27)

    def FeatureExtrusion3(self, *args):
        return self._do("FeatureExtrusion3", args, 23)


class FakeModelDoc(object):
    """假 ModelDoc2。mode 控制"特征到底有没有去料"。"""

    def __init__(self, mode="ok", volume_mm3=100000.0, bodies=1):
        self.mode = mode
        self._vol_m3 = float(volume_mm3) * 1e-9
        self._vol_init_m3 = float(volume_mm3) * 1e-9
        self._bodies = bodies
        self._sel_count = 0
        self.calls = []
        self.events = []          # 记录"清选择 / 重建 / 选轮廓"的先后顺序
        self.FeatureManager = FakeFeatureManager(self)
        self.SketchManager = self._make_sketch_manager()
        self.Extension = self._make_extension()
        self.SelectionManager = FakeSelectionMgr(self)

    # ---- 事件记录 ----
    def _event(self, tag):
        self.events.append(tag)

    # ---- ModelDoc2 ----
    def ClearSelection2(self, all_=True):
        self._event("ClearSelection2")
        self._sel_count = 0
        return True

    def ForceRebuild3(self, top_only=False):
        self._event("ForceRebuild3")
        return True

    def EditRebuild3(self):
        self._event("EditRebuild3")
        return True

    def GetBodies2(self, body_type=0, visible_only=True):
        if self._bodies <= 0:
            return None
        return tuple(FakeBody(self) for _ in range(self._bodies))

    def GetMassProperties(self, accuracy=0):
        return [0, 0, 0, self._vol_m3, 0, 0]

    def FirstFeature(self):
        return None

    def EditSketch(self):
        return True

    # ---- 特征结果模拟 ----
    def feature_result(self, api, args):
        if self.mode == "none":
            return None                       # SW 直接返回 None
        if self.mode == "no_removal":
            return object()                   # 返回 CDispatch 但不去料
        if self.mode == "cut_complement":
            # 模拟"切到补集"：一次去掉 78% 材料（-30520.6mm³ 那类事故）
            self._vol_m3 = self._vol_init_m3 * 0.22
            return object()
        # mode == "ok"：按目标去料 5%
        self._vol_m3 = self._vol_init_m3 * 0.95
        return object()

    # ---- SketchManager / Extension ----
    def _make_sketch_manager(self):
        doc = self

        class _SM(object):
            def __init__(self):
                self._sketch = FakeSketch("Sketch1", 1)

            @property
            def ActiveSketch(self):
                return None

            def InsertSketch(self, flag=True):
                return True

        return _SM()

    def _make_extension(self):
        doc = self

        class _Ext(object):
            def SelectByID2(self, name, sel_type, x, y, z, append,
                            mark, callout, selectopt):
                doc._event("SelectByID2")
                doc._sel_count += 1
                return True

        return _Ext()


def make_model(**kw):
    doc = FakeModelDoc(**kw)
    m = object.__new__(swapi.SWModel)
    m.sw = None
    m.model = doc
    m.skm = doc.SketchManager
    m.fm = doc.FeatureManager
    m.ext = doc.Extension
    m._empty = None
    m.warnings = []
    m._active_sketch_name = "Sketch1"
    m._last_cut_feature_name = None
    m._consumed_sketches = set()
    m._sketch_area_mm2 = 0.0
    m._area_sketch_name = "Sketch1"
    m._sketch_circles_local = []
    m._sketch_normal_axis = 2
    m._sketch_axis_offset_mm = 0.0
    m._sketch_open_count = 1
    m._last_begin_plane = "Top Plane"
    m._plane_generation = 0
    m._surface_sketch_offset = 0.0
    m._right_plane_mode = False
    m._sel_diag = ""
    m._pending_material = None
    m._pending_density = None
    m.material_result = None
    # 让"选中轮廓"一步直接成功（选择链路本身由别的测试覆盖）
    m._ensure_cut_profile_selected = lambda: (True, "fake:ok")
    return m


# ══════════════════════════════════════════════════════════════════════
sec("A. 官方形参表：Cut3=26 / Cut4=27 / Extrusion3=23（逐位同名同序）")
# ══════════════════════════════════════════════════════════════════════
_CUT3 = ("Sd", "Flip", "Dir", "T1", "T2", "D1", "D2", "Dchk1", "Dchk2",
         "Ddir1", "Ddir2", "Dang1", "Dang2", "OffsetReverse1", "OffsetReverse2",
         "TranslateSurface1", "TranslateSurface2", "NormalCut", "UseFeatScope",
         "UseAutoSelect", "AssemblyFeatureScope", "AutoSelectComponents",
         "PropagateFeatureToParts", "T0", "StartOffset", "FlipStartOffset")
_CUT4 = _CUT3 + ("OptimizeGeometry",)
_EXT3 = ("Sd", "Flip", "Dir", "T1", "T2", "D1", "D2", "Dchk1", "Dchk2",
         "Ddir1", "Ddir2", "Dang1", "Dang2", "OffsetReverse1", "OffsetReverse2",
         "TranslateSurface1", "TranslateSurface2", "Merge", "UseFeatScope",
         "UseAutoSelect", "T0", "StartOffset", "FlipStartOffset")

check("★ CUT_PARAM_NAMES 与类型库 FeatureCut3 逐位一致（26 参）",
      tuple(getattr(swapi, "CUT_PARAM_NAMES", ())) == _CUT3,
      getattr(swapi, "CUT_PARAM_NAMES", None))
check("★ CUT_PARAM_NAMES_4 与类型库 FeatureCut4 逐位一致（27 参）",
      tuple(getattr(swapi, "CUT_PARAM_NAMES_4", ())) == _CUT4,
      getattr(swapi, "CUT_PARAM_NAMES_4", None))
check("★ Cut4 = Cut3 + 末尾 OptimizeGeometry（不能共用同一元组）",
      tuple(getattr(swapi, "CUT_PARAM_NAMES_4", ()))[:-1]
      == tuple(getattr(swapi, "CUT_PARAM_NAMES", ()))
      and tuple(getattr(swapi, "CUT_PARAM_NAMES_4", ()))[-1] == "OptimizeGeometry")
check("★ EXTRUDE_PARAM_NAMES 与类型库 FeatureExtrusion3 逐位一致（23 参）",
      tuple(getattr(swapi, "EXTRUDE_PARAM_NAMES", ())) == _EXT3,
      getattr(swapi, "EXTRUDE_PARAM_NAMES", None))
check("★ FeatureExtrusion3 形参表里【没有】任何切除位（AddPad/cut 都不存在）",
      not any(("cut" in n.lower() or "addpad" in n.lower())
              for n in getattr(swapi, "EXTRUDE_PARAM_NAMES", ())),
      getattr(swapi, "EXTRUDE_PARAM_NAMES", None))
check("第 18 位是 Merge（不是切除开关）",
      _EXT3[17] == "Merge", _EXT3[17])
check("第 3 位是 Dir（方向位 pos3，1-based）", _EXT3[2] == "Dir", _EXT3[2])
check("第 8 位是 Dchk1（拔模开关，不是方向）", _EXT3[7] == "Dchk1", _EXT3[7])

# _cut_args 实跑：按官方顺序取值，名字写错当场抛错
_vals = {n: 0 for n in _CUT3}
try:
    _t = swapi._args_in_official_order(_CUT3, _vals)
    check("_args_in_official_order 按官方顺序展开（26 元）",
          isinstance(_t, tuple) and len(_t) == 26, type(_t))
except Exception as _e:
    check("_args_in_official_order 按官方顺序展开（26 元）", False, repr(_e))

try:
    _bad = dict(_vals)
    _bad.pop("NormalCut")
    swapi._args_in_official_order(_CUT3, _bad)
    check("★ 缺参数必须抛 ValueError（不许静默错位）", False, "没抛")
except ValueError:
    check("★ 缺参数必须抛 ValueError（不许静默错位）", True)
except Exception as _e:
    check("★ 缺参数必须抛 ValueError（不许静默错位）", False, repr(_e))

try:
    _bad2 = dict(_vals)
    _bad2["AddPad"] = True
    swapi._args_in_official_order(_CUT3, _bad2)
    check("★ 多参数必须抛 ValueError（拼错名字即暴露）", False, "没抛")
except ValueError:
    check("★ 多参数必须抛 ValueError（拼错名字即暴露）", True)
except Exception as _e:
    check("★ 多参数必须抛 ValueError（拼错名字即暴露）", False, repr(_e))

# ══════════════════════════════════════════════════════════════════════
sec("B. 实参个数：Cut3 收到 26 个 / Cut4 收到 27 个")
# ══════════════════════════════════════════════════════════════════════
m = make_model(mode="ok")
m._sketch_circles_local = [(0.0, 0.0, 5.0)]
m._sketch_area_mm2 = 78.54
try:
    m.cut(depth=10, through=False)
    _cut_ok = True
    _cut_err = ""
except Exception as _e:
    _cut_ok = False
    _cut_err = repr(_e)
check("cut() 在假 COM 上跑通（ok 模式）", _cut_ok, _cut_err)
_n_calls = len(m.model.calls)
check("至少调用了一次 FeatureCut*", _n_calls >= 1, _n_calls)
_arity_bad = []
for _api, _args, _n_exp in m.model.calls:
    if len(_args) != _n_exp:
        _arity_bad.append((_api, len(_args), _n_exp))
check("★ 每个 FeatureCut3 收到 26 参、Cut4 收到 27 参（逐个核对）",
      not _arity_bad, _arity_bad)

# ══════════════════════════════════════════════════════════════════════
sec("C. flip 不得切补集：方向重试只动 Dir，Flip 恒 False")
# ══════════════════════════════════════════════════════════════════════
for _label, _kw in (("默认", {}),
                    ("flip=True", {"flip": True}),
                    ("through=True", {"through": True})):
    mm = make_model(mode="ok")
    mm._sketch_circles_local = [(0.0, 0.0, 5.0)]
    mm._sketch_area_mm2 = 78.54
    try:
        mm.cut(**_kw)
    except Exception:
        pass
    _flip_slot = [a[1] for _api, a, _n in mm.model.calls
                  if len(a) >= 3 and _api.startswith("FeatureCut")]
    check("★ %s：FeatureCut 的 Flip 位（第2位）恒 False —— 绝不切补集" % _label,
          all(v is False or v == 0 for v in _flip_slot), _flip_slot[:6])

# flip=True 必须落进 Dir 位（第 3 位）
mm = make_model(mode="ok")
mm._sketch_circles_local = [(0.0, 0.0, 5.0)]
mm._sketch_area_mm2 = 78.54
try:
    mm.cut(depth=10, flip=True)
except Exception:
    pass
_dir_slots = [a[2] for _api, a, _n in mm.model.calls
              if len(a) >= 3 and _api.startswith("FeatureCut")]
check("★ flip=True 落进 Dir 位（第 3 位，且首次尝试就为 True）",
      bool(_dir_slots) and _dir_slots[0] is True, _dir_slots[:6])

# 显式 flip_side_to_cut=True 才会置 Flip 位，且切补集必须判失败
mm = make_model(mode="cut_complement")
mm._sketch_circles_local = [(0.0, 0.0, 5.0)]
mm._sketch_area_mm2 = 78.54
try:
    mm.cut(depth=10, flip_side_to_cut=True)
    _comp_raised = False
    _comp_msg = ""
except Exception as _e:
    _comp_raised = True
    _comp_msg = str(_e)
check("★ flip_side_to_cut=True 切掉 78% 材料 → 必须报错（不许静默毁件）",
      _comp_raised, _comp_msg[:300])
check("★ 报错信息要点明「补集」并给出 flip=True 的改法",
      ("补集" in _comp_msg and "flip=True" in _comp_msg), _comp_msg[:300])

# ══════════════════════════════════════════════════════════════════════
sec("D. 返回 None / 去料为 0 必须显式报错")
# ══════════════════════════════════════════════════════════════════════
mm = make_model(mode="none")
mm._sketch_circles_local = [(0.0, 0.0, 5.0)]
mm._sketch_area_mm2 = 78.54
try:
    mm.cut(depth=10)
    _none_raised = False
    _none_msg = ""
except Exception as _e:
    _none_raised = True
    _none_msg = str(_e)
check("★ FeatureCut 全返回 None → cut() 必须抛错（不许静默成功）",
      _none_raised, _none_msg[:300])
check("★ 报错里必须出现「返回 None」且附带可操作原因",
      ("返回 None" in _none_msg) and ("处置" in _none_msg or "建议" in _none_msg),
      _none_msg[:400])

mm = make_model(mode="no_removal")
mm._sketch_circles_local = [(0.0, 0.0, 5.0)]
mm._sketch_area_mm2 = 78.54
try:
    mm.cut(depth=10)
    _nr_raised = False
    _nr_msg = ""
except Exception as _e:
    _nr_raised = True
    _nr_msg = str(_e)
check("★ 返回 CDispatch 但去料=0 → 必须抛错（不许静默成功）",
      _nr_raised, _nr_msg[:300])

# 体积读不到 → 也必须判失败（旧实现 return f, "" 算成功）
class _NoVolumeBody(FakeBody):
    def GetMassProperties(self, accuracy=0):
        raise RuntimeError("IBody2.GetMassProperties 不可用（假 COM）")


class _NoVolumeDoc(FakeModelDoc):
    """两条体积读取路径都挂掉：GetMassProperties + 逐实体累加退路。"""

    def GetMassProperties(self, accuracy=0):
        raise RuntimeError("IModelDoc2.GetMassProperties 不可用（假 COM）")

    def GetBodies2(self, body_type=0, visible_only=True):
        return (_NoVolumeBody(self),)


_d = _NoVolumeDoc(mode="ok")
mm = make_model(mode="ok")
mm.model = _d
mm.fm = _d.FeatureManager
mm.skm = _d.SketchManager
mm.ext = _d.Extension
mm._sketch_circles_local = [(0.0, 0.0, 5.0)]
mm._sketch_area_mm2 = 78.54
try:
    mm.cut(depth=10)
    _vol_raised = False
    _vol_msg = ""
except Exception as _e:
    _vol_raised = True
    _vol_msg = str(_e)
check("★ 体积读不到 ≠ 成功 → 必须报错（旧实现量不出来就判成功）",
      _vol_raised, _vol_msg[:300])
check("★ 报错信息必须说明「无法读取体积/无法证明去了料」",
      ("无法读取" in _vol_msg) or ("无法证明" in _vol_msg), _vol_msg[:300])

# ══════════════════════════════════════════════════════════════════════
sec("E. do_cut 状态依赖型选择错乱：先清选择 → 重建 → 再选轮廓")
# ══════════════════════════════════════════════════════════════════════
mm = make_model(mode="ok")
mm._sketch_circles_local = [(0.0, 0.0, 5.0)]
mm._sketch_area_mm2 = 78.54
mm.model._sel_count = 3          # 模拟"上一步残留 3 个脏选择"
_sel_order = []
_orig_ensure = mm._ensure_cut_profile_selected


def _ensure_spy():
    _sel_order.append("select_profile")
    return _orig_ensure()


mm._ensure_cut_profile_selected = _ensure_spy
try:
    mm.cut(depth=10)
except Exception:
    pass
_ev = mm.model.events
_i_clear = next((i for i, e in enumerate(_ev) if e == "ClearSelection2"), -1)
_i_rebuild = next((i for i, e in enumerate(_ev)
                   if e in ("ForceRebuild3", "EditRebuild3")), -1)
_i_select = _ev.index("SelectByID2") if "SelectByID2" in _ev else -1
check("★ cut 前先 ClearSelection2（清脏选择）", _i_clear >= 0, _ev[:12])
check("★ cut 前做强制重建（ForceRebuild3 优先）", _i_rebuild >= 0, _ev[:12])
check("★ 顺序：先清选择 → 再重建 → 最后才选轮廓",
      _i_clear >= 0 and _i_rebuild > _i_clear
      and (_i_select < 0 or _i_select > _i_rebuild),
      (_i_clear, _i_rebuild, _i_select, _ev[:12]))
check("★ 残留脏选择必须记成告警（状态依赖的可观测证据）",
      any("残留选中对象" in str(w.get("msg", "")) for w in mm.warnings),
      [w.get("msg") for w in mm.warnings][:3])

# extrude 同样要自愈
mm2 = make_model(mode="ok")
mm2.model._bodies = 1
try:
    mm2.extrude(10)
except Exception:
    pass
_ev2 = mm2.model.events
check("★ extrude 前同样先 ClearSelection2",
      "ClearSelection2" in _ev2, _ev2[:12])
check("★ extrude 前同样做强制重建",
      ("ForceRebuild3" in _ev2) or ("EditRebuild3" in _ev2), _ev2[:12])

# 草图编辑态下【不得】重建（ForceRebuild3 会把草图重置掉 = Bug T2 形态）
mm3 = make_model(mode="ok")
mm3.model._sel_count = 1


class _ActiveSketchSM(object):
    ActiveSketch = FakeSketch("Sketch1", 1)

    def InsertSketch(self, flag=True):
        return True


mm3.skm = _ActiveSketchSM()
_r = mm3._reset_selection_state(reason="草图编辑态")
check("★ 草图编辑态：只清选择、不重建（避免把草图重置掉）",
      _r.get("rebuilt") is False
      and _r.get("sketch_active") is True
      and "ForceRebuild3" not in mm3.model.events,
      (_r, mm3.model.events[:8]))

# ══════════════════════════════════════════════════════════════════════
sec("F. 净距过小：调 API 之前就要给可读原因")
# ══════════════════════════════════════════════════════════════════════
check("★ 新增 _ring_clearance_mm 纯函数", _func("_ring_clearance_mm") is not None)
check("★ 新增 _cut_none_hint 诊断函数", _func("_cut_none_hint") is not None)
check("★ 新增 _reset_selection_state 自愈函数",
      _func("_reset_selection_state") is not None)

_g = swapi._ring_clearance_mm([(0.0, 0.0, 5.0), (10.328, 0.0, 5.0)])
check("净距 0.328mm 能被算出来", _g is not None and abs(_g - 0.328) < 1e-6, _g)
_g2 = swapi._ring_clearance_mm([(0.0, 0.0, 5.0), (10.522, 0.0, 5.0)])
check("净距 0.522mm 能被算出来", _g2 is not None and abs(_g2 - 0.522) < 1e-6, _g2)
check("单个环 / 空输入返回 None",
      swapi._ring_clearance_mm([(0, 0, 1)]) is None
      and swapi._ring_clearance_mm([]) is None
      and swapi._ring_clearance_mm(None) is None)
check("两环相交时净距为负",
      swapi._ring_clearance_mm([(0, 0, 5), (3, 0, 5)]) < 0)

check("★ 合并容差已从 0.5mm 降到 0.1mm（0.328mm 那次失败的真正根因）",
      abs(float(getattr(swapi, "SKETCH_MERGE_TOL_MM", 0)) - 0.1) < 1e-9,
      getattr(swapi, "SKETCH_MERGE_TOL_MM", None))
check("★ end_sketch 不再写死 0.0005（改用 SKETCH_MERGE_TOL_M）",
      "MergePoints(SKETCH_MERGE_TOL_M)" in _source_of("end_sketch")
      and "MergePoints(0.0005)" not in SRC)

# 净距 < 合并容差（真正退化）→ 必须在调 API 之前直接报错，并给出可读原因
mm = make_model(mode="ok")
mm._sketch_circles_local = [(0.0, 0.0, 5.0), (10.05, 0.0, 5.0)]   # 净距 0.05mm
mm._sketch_area_mm2 = 157.08
try:
    mm.cut(through=True)
    _gap_raised = False
    _gap_msg = ""
except Exception as _e:
    _gap_raised = True
    _gap_msg = str(_e)
check("★ 净距 0.05mm（< 合并容差）→ cut 直接报错，不给 SW 返回 none/no_body 的机会",
      _gap_raised, _gap_msg[:300])
check("★ 报错里给出实测依据（0.328 必失败 / 0.522 起 PASS）",
      ("0.328" in _gap_msg) and ("0.522" in _gap_msg), _gap_msg[:400])
check("★ 报错里给出可操作处置（加大间距 / 分次建模 / extrude_with_holes）",
      ("加大" in _gap_msg) and ("分两次" in _gap_msg)
      and ("extrude_with_holes" in _gap_msg), _gap_msg[:400])
check("★ 净距预检发生在调用 FeatureCut 之前（一次 API 都没调）",
      len(mm.model.calls) == 0, mm.model.calls[:3])

# 净距 0.328mm（历史失败点）→ 容差已下调，允许尝试，但必须留下可读原因
mm = make_model(mode="ok")
mm._sketch_circles_local = [(0.0, 0.0, 5.0), (10.328, 0.0, 5.0)]
mm._sketch_area_mm2 = 157.08
try:
    mm.cut(through=True)
    _gap_w_ok = True
    _gap_w_msg = ""
except Exception as _e:
    _gap_w_ok = False
    _gap_w_msg = str(_e)
check("★ 净距 0.328mm（历史失败点）：容差下调后允许尝试（不再一刀切拒绝）",
      _gap_w_ok, _gap_w_msg[:300])
_gap_warns = [w.get("msg", "") for w in mm.warnings]
check("★ 净距 0.328mm 必须留下【可读原因】告警（含实测依据与处置）",
      any(("净距" in m and "0.328" in m and "0.522" in m
           and "加大" in m) for m in _gap_warns),
      _gap_warns[:3])

# 净距 0.522mm → 应当放行（实测 PASS 区间）
mm = make_model(mode="ok")
mm._sketch_circles_local = [(0.0, 0.0, 5.0), (10.522, 0.0, 5.0)]
mm._sketch_area_mm2 = 157.08
try:
    mm.cut(through=True)
    _gap2_ok = True
    _gap2_msg = ""
except Exception as _e:
    _gap2_ok = False
    _gap2_msg = str(_e)
check("★ 净距 0.522mm → 放行（实测 PASS 区间，不许误拒）",
      _gap2_ok, _gap2_msg[:300])

# ══════════════════════════════════════════════════════════════════════
sec("G. FeatureExtrusion3：不再当切除兜底；负深度走 Dir 位")
# ══════════════════════════════════════════════════════════════════════
check("★ cut() 里不再有 FeatureExtrusion3 兜底（它只能增料，不是切除 API）",
      '"_try_cut("FeatureExtrusion3"' not in SRC
      and '"FeatureExtrusion3", (' not in _source_of("cut"),
      "仍存在 Extrusion3 兜底调用")

mm = make_model(mode="ok")
try:
    mm.extrude(10)
except Exception:
    pass
_ext_calls = [a for api, a, _n in mm.model.calls if api == "FeatureExtrusion3"]
check("extrude 调用 FeatureExtrusion3 一次", len(_ext_calls) >= 1, len(_ext_calls))
if _ext_calls:
    a = _ext_calls[0]
    check("★ extrude 实参 23 个（与类型库一致）", len(a) == 23, len(a))
    check("★ 第 18 位（index 17）是 Merge=True，不是切除开关",
          a[17] is True, a[17])
    check("★ 第 3 位 Dir 在正深度时为 False", a[2] is False, a[2])

mm = make_model(mode="ok")
try:
    mm.extrude(-10)
except Exception:
    pass
_ext_calls = [a for api, a, _n in mm.model.calls if api == "FeatureExtrusion3"]
if _ext_calls:
    a = _ext_calls[0]
    check("★ 负深度 → Dir 位（第 3 位）=True（方向反转走对位置）",
          a[2] is True, a[2])
    check("★ 负深度【不再】污染 Dchk1 拔模开关位（第 8 位）",
          a[7] is False, a[7])

# 返回对象但无实体 → 必须报错
mm = make_model(mode="ok", bodies=0)
try:
    mm.extrude(10)
    _nb_raised = False
    _nb_msg = ""
except Exception as _e:
    _nb_raised = True
    _nb_msg = str(_e)
check("★ extrude 返回对象但零件无实体（no_body）→ 必须报错",
      _nb_raised, _nb_msg[:300])

# ══════════════════════════════════════════════════════════════════════
print("")
print("=" * 74)
print("汇总：PASS=%d  FAIL=%d" % (PASS, FAIL))
if FAILURES:
    print("失败项：")
    for x in FAILURES:
        print("  - " + x)
print("")
print("真机验证（需 SW 在跑，人工执行）：")
print("  · Ø10 通孔板 cut(through=True) 去料 == π*5^2*10 mm3 ±1%")
print("  · cut(flip=True) 不得出现「去料 > 60%」的补集特征")
print("  · 两环同草图：净距 0.328mm 报错 / 0.522mm 成功")
sys.exit(0 if FAIL == 0 else 1)
