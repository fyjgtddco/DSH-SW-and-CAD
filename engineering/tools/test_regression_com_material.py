# -*- coding: utf-8 -*-
r"""回归测试：COM 晚绑定属性/方法混淆（R11）+ 材质写入不可靠（R9）

★ 锁定的真实事故（台账 BUG-71/74/77/85/92 + BUG-30/55/56）：
  COM 侧：
    1. 把【属性】当【方法】调用、或反之 —— IComponent2.GetSurface/GetEdges、
       IFace2.Normal、IModelDoc2.RevisionNumber 都踩过；
    2. com_error「参数个数不正确」= 早期/晚绑定签名混用
       （IModelDoc2.GetMassProperties 无参，却传了 (0)）；
    3. 现有代码到处 try/except 猜「这是属性还是方法」，猜错被吞成静默 None。
  材质侧：
    4. SW 材质库没有国标牌号（Q235/45/40Cr/20CrMnTi/GCr15/HT200/ZL104…）；
    5. 跨标准替代被误判（把国标近似成欧美标，Q235 → AISI 1020）；
    6. **实测 33/35 件 sw_internal_matches_request=false** —— 凭据与实物脱节。

★ 本测试钉死的契约（全部假 COM，不启动 SolidWorks）：
  A. 类型库判定表与官方一致：方法必须加括号、属性绝不能加括号；
  B. _com_get 对方法一律调用、对属性一律不加括号（不再靠 callable() 猜）；
  C. 成员缺失 / 签名不符 → 抛 ComMemberError（可读），不是静默 None；
  D. GetMassProperties 必须按【无参方法】调用（传 (0) 会被判为错误）；
  E. 材质写入后必须【读回校验】，不一致必须报错；
  F. 跨标准替代必须被【拒绝】（不允许静默近似成欧美标）；
  G. 缺国标牌号时必须给出可操作路径（set_custom_material / 自定义 .sldmat）。

依据（写死在断言里，防止有人「顺手改回去」）：
  【类型库】%LOCALAPPDATA%\Temp\gen_py\3.13\
            83A33D31-27C5-11CE-BFD4-00400513BB57x0x33x0.py
    判定规则：方法 → 生成 def Foo(self, ...)；
              属性 → 只出现在 _prop_map_get_ / _prop_map_put_ 字典里。
    · IFace2.Normal                   L21163  _prop_map_get_ → **属性**
    · IFace2.GetBox / GetSurface      L20604 / L20604 区段 → **方法**
    · ISurface.IsPlane / IsCylinder / Identity  L62439 / L62423 / L62394 → **方法**
      （本文件旧注释写「静态绑定下是属性，加括号会 TypeError」—— 那是错的）
    · IModelDoc2.GetMassProperties    L38295  def GetMassProperties(self) → 方法，**无参**
    · IModelDocExtension.GetMassProperties  L41895 → 方法，(Accuracy, Status) 两参
      （把两者混用就是 com_error「参数个数不正确」的直接来源）
    · ISldWorks.RevisionNumber        L60473  → **方法**
    · ISldWorks.GetMaterialDatabases  L59525  → **方法**
    · IPartDoc.GetMaterialPropertyName2  L47356 → 方法，(ConfigName, Database)
    · IPartDoc.SetMaterialPropertyName2  L47898 → 方法，(ConfigName, Database, Name)
    · IPartDoc.MaterialIdName         L47915  _prop_map_get_ → **属性**
    · IComponent2.Name2 / Transform2  L10416 / L10426 → **属性**
  【网页】SOLIDWORKS API Help「Get Material Example (VBA)」
    https://help.solidworks.com/2026/English/api/sldworksapi/Get_Material_Example_VB.htm
    「sMatName = swPart.GetMaterialPropertyName2("Default", sMatDB)」
    —— 证实它是**方法**且带 out 参数 Database。
  【网页】SOLIDWORKS API Help「Get Mass Properties Example (VBA)」
    https://help.solidworks.com/2026/English/api/sldworksapi/Get_Mass_Properties_of_ActiveDoc_Example_VB.htm
    「vMassProp = swModelExt.GetMassProperties2(1, nStatus, True)」
    —— 带参的是 ModelDocExtension 版；ModelDoc2 版无参。
  【网页】pywin32 Issue #1896（方法/属性在 late-binding 下的表现差异）
    https://github.com/mhammond/pywin32/issues/1896

⚠️ 全部用假 COM 对象 / monkeypatch：不 import 真 COM，不启动 SolidWorks。

运行：python test_regression_com_material.py
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


def _src_of(name):
    fn = _func(name)
    return (ast.get_source_segment(SRC, fn) or "") if fn is not None else ""


# ══════════════════════════════════════════════════════════════════════
# 假 COM：可切换「成员是属性还是方法」「方法签名」「回读材质名」
# ══════════════════════════════════════════════════════════════════════
class FakeMassPropsModel(object):
    """假 IModelDoc2：GetMassProperties 是【无参方法】（类型库 L38295）。"""

    def __init__(self, arr=None, strict_arity=True):
        self._arr = arr or [0, 0, 0, 0.0001, 0.01, 0.000785, 0, 0, 0, 0, 0, 0]
        self._strict = strict_arity
        self.calls = []

    def GetMassProperties(self, *args):
        self.calls.append(args)
        if self._strict and args:
            raise TypeError("GetMassProperties 不接受实参（收到 %d 个）" % len(args))
        return tuple(self._arr)


class FakeRevisionSw(object):
    """假 ISldWorks：RevisionNumber 是【方法】（类型库 L60473）。"""

    def RevisionNumber(self):
        return "33.5.0"


class FakeBody(object):
    def __init__(self, doc):
        self._doc = doc

    def GetMassProperties(self, accuracy=0):
        return [0, 0, 0, self._doc._vol, 0.01, self._doc._mass, 0, 0, 0, 0, 0, 0]

    def GetBodyBox(self):
        return [-0.05, -0.04, -0.005, 0.05, 0.04, 0.005]

    def SetMaterialProperty(self, cfg, db, name):
        return 1        # 非 0 = 失败（实测恒 1/6）


class FakeMaterialDoc(object):
    """假 IPartDoc + IModelDoc2，用于 set_material 全链路。

    mode:
      "ok"          —— 写入成功且回读一致
      "silent_swap" —— SW 静默接受调用，但内部材质被换成另一个（33/35 事故现场）
      "no_write"    —— 完全没写进去，回读仍是旧材料
    """

    def __init__(self, mode="ok", req="Q235", actual="Q235",
                 volume_m3=0.0001, mass_kg=0.785):
        # mass/volume = 0.785/0.0001 = 7850 kg/m³（钢），与 Q235 期望密度一致，
        # 否则会先被"密度/家族不符"拦掉，测不到读回校验那条路径。
        self.mode = mode
        self._req = req
        self._actual = actual
        self._vol = volume_m3
        self._mass = mass_kg
        self._bodies = 1
        self.calls = []
        self.write_calls = []

    # ---- IModelDoc2 ----
    def GetMassProperties(self, *args):
        if args:
            raise TypeError("GetMassProperties 不接受实参")
        return [0, 0, 0, self._vol, 0.01, self._mass, 0, 0, 0, 0, 0, 0]

    def GetBodies2(self, body_type=0, visible_only=False):
        return (FakeBody(self),) if self._bodies else None

    def ForceRebuild3(self, top_only=False):
        return True

    @property
    def ConfigurationManager(self):
        return types.SimpleNamespace(
            ActiveConfiguration=types.SimpleNamespace(Name="默认"))

    # ---- IPartDoc：方法 ----
    def SetMaterialPropertyName2(self, cfg, db, name):
        self.write_calls.append((cfg, db, name))
        if self.mode == "silent_swap":
            self._actual = "AISI 1020"
        elif self.mode == "no_write":
            self._actual = "普通碳钢"
        else:
            self._actual = name
        return None      # void

    def GetMaterialPropertyName2(self, cfg="", db=None):
        return self._actual

    # ---- IPartDoc：属性（绝不能加括号）----
    @property
    def MaterialIdName(self):
        return self._actual


def make_swmodel(doc=None):
    doc = doc or FakeMaterialDoc()
    m = object.__new__(swapi.SWModel)
    m.sw = None
    m.model = doc
    m.skm = types.SimpleNamespace(ActiveSketch=None, InsertSketch=lambda f=True: True)
    m.fm = types.SimpleNamespace()
    m.ext = types.SimpleNamespace()
    m._empty = None
    m.warnings = []
    m._pending_material = None
    m._pending_density = None
    m.material_result = None
    m._requested_material = None
    m._material_source = None
    m._active_sketch_name = None
    m._consumed_sketches = set()
    m._last_cut_feature_name = None
    return m


# ══════════════════════════════════════════════════════════════════════
sec("A. 类型库判定表：方法 / 属性必须分清")
# ══════════════════════════════════════════════════════════════════════
_METHODS = set(getattr(swapi, "_COM_METHOD_NAMES", frozenset()))
check("★ 新增 _COM_METHOD_NAMES 判定表", len(_METHODS) > 0, len(_METHODS))
check("★ 新增 ComMemberError 异常类型",
      isinstance(getattr(swapi, "ComMemberError", None), type))
check("★ 新增 _com_get / _com_get_soft",
      callable(getattr(swapi, "_com_get", None))
      and callable(getattr(swapi, "_com_get_soft", None)))

for _n in ("GetMassProperties", "GetBox", "GetSurface", "GetEdges",
           "RevisionNumber", "GetMaterialDatabases",
           "GetMaterialPropertyName2", "SetMaterialPropertyName2",
           "IsPlane", "IsCylinder", "Identity",
           "FirstFeature", "GetNextFeature", "GetTypeName2",
           "GetTitle", "GetPathName"):
    check("方法 %r 在判定表里（类型库是 def）" % _n, _n in _METHODS, _n)

for _n in ("Normal", "MaterialIdName", "MaterialUserName",
           "MaterialPropertyValues", "Name2", "Transform2"):
    check("属性 %r 【不】在判定表里（类型库是 _prop_map_）" % _n,
          _n not in _METHODS, _n)

# ══════════════════════════════════════════════════════════════════════
sec("B. _com_get：方法一定调用、属性一定不加括号（不靠 callable 猜）")
# ══════════════════════════════════════════════════════════════════════


class _Both(object):
    """同时提供「可调用」与「不可调用」两种成员，验证判定不靠 callable。"""

    def __init__(self):
        self.method_called = 0

    def GetMassProperties(self, *a):
        self.method_called += 1
        return ("called", a)

    @property
    def Normal(self):
        return [0.0, 0.0, 1.0]


_o = _Both()
_r = swapi._com_get(_o, "GetMassProperties")
check("★ 方法被调用（返回值来自方法体）", _r == ("called", ()), _r)
check("★ 方法确实被调用了一次", _o.method_called == 1, _o.method_called)

_r2 = swapi._com_get(_o, "Normal")
check("★ 属性原样返回（不是调用结果）", _r2 == [0.0, 0.0, 1.0], _r2)


class _CallableProp(object):
    """陷阱：属性值本身是可调用的（函数/类/代理）—— callable() 会误判。"""

    def __init__(self):
        self.calls = 0

    @property
    def Normal(self):
        def _inner():
            self.calls += 1
            return "WRONG"
        return _inner


_cp = _CallableProp()
_r3 = swapi._com_get(_cp, "Normal")
check("★ 可调用属性【不】被调用（callable() 猜法会在这里出错）",
      _cp.calls == 0 and callable(_r3), (_cp.calls, type(_r3)))


class _ImplicitlyEvaluated(object):
    def GetMassProperties(self):
        return "IMPLICIT"


_ie = _ImplicitlyEvaluated()
_ie.__dict__["GetMassProperties"] = (1, 2, 3, 4, 5, 6)
_r4 = swapi._com_get(_ie, "GetMassProperties")
check("★ 无参方法已被隐式求值时原样返回（不强行调用）",
      _r4 == (1, 2, 3, 4, 5, 6), _r4)

try:
    swapi._com_get(_ie, "Normal", 1, 2)
    _argp = False
except Exception:
    _argp = True
check("★ 属性传实参 → 抛错（不静默吞）", _argp)

# ══════════════════════════════════════════════════════════════════════
sec("C. 成员缺失 / 签名不符 → ComMemberError（可读，不是静默 None）")
# ══════════════════════════════════════════════════════════════════════
try:
    swapi._com_get(_o, "__no_such_member__")
    _miss = False
    _miss_msg = ""
except swapi.ComMemberError as _e:
    _miss = True
    _miss_msg = str(_e)
except Exception as _e:
    _miss = False
    _miss_msg = "wrong type: %r" % (_e,)
check("★ 成员不存在 → ComMemberError", _miss, _miss_msg[:200])
check("★ 错误信息包含成员名（可读）", "__no_such_member__" in _miss_msg,
      _miss_msg[:200])

try:
    swapi._com_get(None, "GetMassProperties")
    _none = False
    _none_msg = ""
except swapi.ComMemberError as _e:
    _none = True
    _none_msg = str(_e)
except Exception as _e:
    _none = False
    _none_msg = "wrong type"
check("★ obj=None → ComMemberError", _none, _none_msg[:200])


class _StrictArity(object):
    def GetMassProperties(self):
        raise TypeError("takes 1 positional argument but 2 were given")


try:
    swapi._com_get(_StrictArity(), "GetMassProperties", 0)
    _arity = False
    _arity_msg = ""
except swapi.ComMemberError as _e:
    _arity = True
    _arity_msg = str(_e)
except Exception as _e:
    _arity = False
    _arity_msg = "wrong type: %r" % (_e,)
check("★ 方法实参不匹配 → ComMemberError（把 com_error 翻译成人话）",
      _arity, _arity_msg[:200])
check("★ 错误信息提示核对类型库形参个数",
      "形参" in _arity_msg or "实参" in _arity_msg, _arity_msg[:200])

check("_com_get_soft 缺成员返回默认值",
      swapi._com_get_soft(_o, "__nope__", default="D") == "D")
check("_com_get_soft 成功时返回真值",
      swapi._com_get_soft(_o, "Normal") == [0.0, 0.0, 1.0])

# ══════════════════════════════════════════════════════════════════════
sec("D. GetMassProperties 必须按【无参方法】调用")
# ══════════════════════════════════════════════════════════════════════
_md = FakeMassPropsModel()
_m = make_swmodel()
_m.model = _md
_res = _m.massprops()
check("massprops() 在假 COM 上成功", _res.get("ok") is True, _res.get("error"))
check("★ GetMassProperties 被【无参】调用（旧代码先按属性取，恒失败）",
      _md.calls and all(len(a) == 0 for a in _md.calls), _md.calls)
check("★ 体积来源就是 GetMassProperties（不再静默降级）",
      _res.get("volume_source") == "GetMassProperties", _res.get("volume_source"))

_md2 = FakeMassPropsModel()
_m2 = make_swmodel()
_m2.model = _md2
_vol = _m2._body_volume_mm3()
check("★ _body_volume_mm3 用无参方法读（旧代码传 (0) 抛 com_error）",
      _md2.calls and all(len(a) == 0 for a in _md2.calls), _md2.calls)
check("_body_volume_mm3 拿到正确体积（0.0001 m³ = 100000 mm³）",
      _vol is not None and abs(_vol - 100000.0) < 1.0, _vol)

check("★ 源码里不再有 self.model.GetMassProperties(0)（签名混用）",
      "self.model.GetMassProperties(0)" not in SRC,
      [ln for ln in SRC.split(chr(10))
       if "self.model.GetMassProperties(0)" in ln][:3])

# ══════════════════════════════════════════════════════════════════════
sec("E. RevisionNumber：方法，必须加括号")
# ══════════════════════════════════════════════════════════════════════
_maj = swapi._early_major_from_sw(FakeRevisionSw())
check("★ RevisionNumber 按方法调用能取到 33", _maj == 33, _maj)
check("★ 源码里不再用 callable() 猜 RevisionNumber",
      "if callable(rev):" not in _src_of("_early_major_from_sw"),
      _src_of("_early_major_from_sw")[:300])


class _PropRevision(object):
    @property
    def RevisionNumber(self):
        return "33.5.0"


check("属性式 RevisionNumber 也能读到（兼容）",
      swapi._early_major_from_sw(_PropRevision()) == 33,
      swapi._early_major_from_sw(_PropRevision()))
check("None 一律返回 0（不抛）", swapi._early_major_from_sw(None) == 0)

# ══════════════════════════════════════════════════════════════════════
sec("F. 材质名归一化：只做无损归一，绝不做模糊匹配")
# ══════════════════════════════════════════════════════════════════════
check("★ 新增 _normalize_material_name / _material_names_match",
      callable(getattr(swapi, "_normalize_material_name", None))
      and callable(getattr(swapi, "_material_names_match", None)))

_same = [
    ("SolidWorks Materials|普通碳钢", "普通碳钢"),
    ("solidworks materials|AISI 1020", "AISI 1020"),
    ("  AISI 1020  ", "AISI 1020"),
    ("AISI  1020", "AISI 1020"),
    ("SolidWorks Materials - 灰铸铁", "灰铸铁"),
]
for _a, _b in _same:
    check("归一化后判同一材料: %r == %r" % (_a, _b),
          swapi._material_names_match(_a, _b) is True)

_cross = [
    ("AISI 1020", "Q235"),
    ("普通碳钢", "Q235"),
    ("S235JR", "Q235"),
    ("SUS304", "06Cr19Ni10"),
    ("AISI 1045", "45"),
]
for _a, _b in _cross:
    check("★ 跨标准替代判【不匹配】: %r vs %r" % (_a, _b),
          swapi._material_names_match(_a, _b) is False)

check("空名一律不匹配",
      swapi._material_names_match("", "Q235") is False
      and swapi._material_names_match("Q235", "") is False)

# ══════════════════════════════════════════════════════════════════════
sec("G. 读回校验：写入后 SW 内部材质 != 请求 → 必须报错")
# ══════════════════════════════════════════════════════════════════════
check("★ 新增 _read_back_material",
      callable(getattr(swapi, "_read_back_material", None)))

_doc_ok = FakeMaterialDoc(mode="ok")
_rb = swapi._read_back_material(make_swmodel(_doc_ok))
check("★ 回读优先用 GetMaterialPropertyName2（方法）",
      _rb.get("method") == "GetMaterialPropertyName2", _rb)
check("回读拿到正确材料名", _rb.get("name") == "Q235", _rb)


class _PropOnlyDoc(FakeMaterialDoc):
    def GetMaterialPropertyName2(self, cfg="", db=None):
        raise AttributeError("GetMaterialPropertyName2 不可用（假 COM）")


_rb2 = swapi._read_back_material(make_swmodel(_PropOnlyDoc(mode="ok")))
check("★ 方法不可用时回落到属性 MaterialIdName",
      _rb2.get("method") == "MaterialIdName" and _rb2.get("name") == "Q235",
      _rb2)

_doc_ok = FakeMaterialDoc(mode="ok", req="Q235", actual="Q235")
_m = make_swmodel(_doc_ok)
_res = _m.set_material("Q235")
check("★ 写入成功且回读一致 → ok=True", _res.get("ok") is True,
      (_res.get("error"), _res.get("readback_material")))
check("★ 返回体固化读回证据 sw_internal_matches_request=True",
      _res.get("sw_internal_matches_request") is True,
      _res.get("sw_internal_matches_request"))
check("返回体带 readback_material",
      _res.get("readback_material") == "Q235", _res.get("readback_material"))

_doc_swap = FakeMaterialDoc(mode="silent_swap", req="Q235", actual="Q235")
_m = make_swmodel(_doc_swap)
_res = _m.set_material("Q235")
check("★ SW 静默换成 AISI 1020 → 必须 ok=False（33/35 事故现场）",
      _res.get("ok") is False, (_res.get("ok"), _res.get("readback_material")))
check("★ 报错里点明「读回不一致」并给出实际材质",
      ("读回不一致" in str(_res.get("error")))
      and ("AISI 1020" in str(_res.get("error"))),
      str(_res.get("error"))[:400])
check("★ 记录 readback_mismatch 审计明细",
      bool(_res.get("readback_mismatch")), _res.get("readback_mismatch"))

_doc_now = FakeMaterialDoc(mode="no_write", req="Q235", actual="Q235")
_m = make_swmodel(_doc_now)
_res = _m.set_material("Q235")
check("★ 完全没写进去（回读仍是旧材料）→ ok=False",
      _res.get("ok") is False, (_res.get("ok"), _res.get("readback_material")))

# ══════════════════════════════════════════════════════════════════════
sec("H. 跨标准替代必须被拒（BUG-55）")
# ══════════════════════════════════════════════════════════════════════
check("★ set_material 新增 allow_cross_standard 参数",
      "allow_cross_standard" in (_src_of("set_material") or ""))
check("★ 新增 _gb_material_advice（缺国标牌号的可操作路径）",
      callable(getattr(swapi, "_gb_material_advice", None)))

_adv = swapi._gb_material_advice("Q235")
check("★ 建议里明确「SW 官方库不收录 GB 牌号」",
      "不收录" in _adv, _adv[:300])
check("★ 建议里给出 set_custom_material 这条可执行路径",
      "set_custom_material" in _adv, _adv[:400])
check("★ 建议里给出自定义 .sldmat / Custom Materials 这条路径",
      ("Custom Materials" in _adv) or ("sldmat" in _adv), _adv[:400])
check("★ 建议里明确禁止「近似成欧美标」",
      ("严禁" in _adv) and ("欧美标" in _adv), _adv[:400])
check("★ 建议里给出显式放行的正规开关 allow_cross_standard=True",
      "allow_cross_standard=True" in _adv, _adv[:400])

_x_std, _x_note = swapi._detect_cross_standard("Q235", "AISI 1020")
check("★ Q235 → AISI 1020 判定为跨标准", _x_std is True, _x_note)
check("跨标准说明里带屈服强度差异佐证",
      "屈服" in _x_note or "MPa" in _x_note, _x_note[:200])
_x_std2, _ = swapi._detect_cross_standard("Q235", "Q235")
check("同名不算跨标准", _x_std2 is False)

_cut = _src_of("set_material")
_i_x = _cut.find("if _cross_std and not allow_cross_standard:")
check("★ 源码里跨标准分支存在", _i_x >= 0)
if _i_x >= 0:
    _seg = _cut[_i_x:_i_x + 900]
    check("★ 跨标准分支里有 continue（拒绝该候选，不是只加警告）",
          "continue" in _seg, _seg[:400])
    check("★ 跨标准被拒时记录 cross_standard_rejected 审计明细",
          "cross_standard_rejected" in _seg, _seg[:400])

# ══════════════════════════════════════════════════════════════════════
sec("I. 材质写入失败路径：三类原因都要可读")
# ══════════════════════════════════════════════════════════════════════
_m = make_swmodel(FakeMaterialDoc(mode="silent_swap"))
_res = _m.set_material("Q235")
check("★ 失败时 ok=False", _res.get("ok") is False)
check("★ 失败时给出 hint（可操作路径）",
      bool(_res.get("hint"))
      and ("set_custom_material" in str(_res.get("hint"))),
      str(_res.get("hint"))[:400])
check("★ 失败时 material_mismatch_rejected=True（供材料防线拦截）",
      _res.get("material_mismatch_rejected") is True,
      _res.get("material_mismatch_rejected"))
check("★ 失败时同时标记 rejected_readback_mismatch",
      _res.get("rejected_readback_mismatch") is True,
      _res.get("rejected_readback_mismatch"))

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
print("  · 对同一零件连续 set_material 两次，第二次读回必须是第二次的牌号")
print("  · set_material('Q235') 在中文库环境应失败并给出 _gb_material_advice")
print("  · massprops() 必须走 GetMassProperties 路径（volume_source 可辨）")
sys.exit(0 if FAIL == 0 else 1)
