# -*- coding: utf-8 -*-
"""临时自测（子任务 B）：用假 COM 对象验证 create_mate / create_lock_mate /
_component_doc / _component_bodies 的分支与回退，不连接 SolidWorks。"""
import sys, os, types
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) or ".")
import swapi

PASS = FAIL = 0
def check(label, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print("  [PASS] " + label)
    else:
        FAIL += 1; print("  [FAIL] " + label + "  " + str(extra))

# ── 假接口类工厂：模拟 gen_py 模块里的 IAssemblyDoc / IComponent2 / IModelDoc2
class _EarlyBase(object):
    def __init__(self, raw):
        self._oleobj_ = raw
        self._raw = raw
    def __getattr__(self, name):
        return getattr(self._raw, name)

class FakeAsmEarly(_EarlyBase):
    """记录调用顺序与参数，供断言。"""
    def __init__(self, raw):
        _EarlyBase.__init__(self, raw)
    def AddConcentricMateWithTolerance(self, *a):
        self._raw.calls.append(("AddConcentricMateWithTolerance", a))
        if self._raw.success_api == "AddConcentricMateWithTolerance":
            return make_mate("concentric")
        raise Exception("not supported")
    def AddDistanceMate(self, *a):
        self._raw.calls.append(("AddDistanceMate", a))
        if self._raw.success_api == "AddDistanceMate":
            return make_mate("distance")
        raise Exception("not supported")
    def AddMate5(self, *a):
        self._raw.calls.append(("AddMate5", a))
        if any(x is None for x in a[:14]):
            raise Exception("float() argument must be a string or a real number, not 'NoneType'")
        if self._raw.success_api == "AddMate5":
            return make_mate("addmate5")
        return None
    def AddMate3(self, *a):
        self._raw.calls.append(("AddMate3", a))
        return None
    def CreateMate(self, *a):
        self._raw.calls.append(("CreateMate", a))
        if len(a) != 1:
            raise Exception("takes from 1 to 2 positional arguments")
        if self._raw.success_api == "CreateMate":
            return make_mate("createmate")
        return None

class FakeCompEarly(_EarlyBase):
    def GetModelDoc2(self):
        return self._raw.doc
    def GetBodies2(self, body_type):
        self._raw.body_type_arg = body_type
        return self._raw.bodies      # 模拟"枚举器"：非 list，需要 list()
    def GetBodies(self, body_type):
        return self._raw.bodies

class FakeModelDocEarly(_EarlyBase):
    pass

class FakeEntityEarly(_EarlyBase):
    """IFace2 的 Select 系列其实定义在 IEntity 上（本机类型库实测）。"""
    def Select4(self, append, data):
        if data is None:
            raise Exception("Select4 需要 SelectData")
        self._raw.selected = (append, "sd")
        return True
    def Select2(self, append, mark):
        self._raw.selected = (append, mark)
        return True

class FakeMod(object):
    IAssemblyDoc = FakeAsmEarly
    IComponent2 = FakeCompEarly
    IModelDoc2 = FakeModelDocEarly
    IEntity = FakeEntityEarly

def make_mate(tag):
    m = types.SimpleNamespace()
    m.Name = "Mate_" + tag
    return m

# ── 假 face / body ──────────────────────────────────────────────────────
class FakeBody(object):
    def __init__(self, faces): self._faces = faces
    def GetFaces(self): return tuple(self._faces)

class FakeFace(object):
    def __init__(self, area, normal=1):
        self._area = area; self._normal = normal
        self.selected = None
        self._oleobj_ = self          # early(face,'IEntity') 才能包装
    @property
    def Normal(self): return self._normal
    def GetArea(self): return self._area
    def Select2(self, append, mark):
        self.selected = (append, mark); return True
    def Select(self, append):
        self.selected = (append, None); return True
    def Select4(self, append, data):
        if data is None: raise Exception("Select4 需要 SelectData")
        self.selected = (append, "sd"); return True

class FakeComp(object):
    def __init__(self, name, bodies=None, doc="DOC"):
        self.Name2 = name
        self._bodies = bodies if bodies is not None else []
        self.doc = doc
        self.body_type_arg = None
        self._oleobj_ = self          # 让 early() 能包装成 IComponent2
    @property
    def bodies(self):
        return self._bodies
    # 晚期绑定下这两个方法【应失败】（BUG-12 的真实现象），
    # 只有走 early(IComponent2) 才拿到值 —— 这样测试才咬得住修复点。
    def GetModelDoc2(self):
        # BUG-12 原始现象：晚期绑定下恒报"找不到成员"（不可用）
        raise Exception("找不到成员。")
    def GetBodies2(self, body_type):
        # 老版本（SW2018~2024）晚期绑定下 GetBodies2 本身是可用的 ——
        # 这正是"早期绑定只是增强，晚期必须继续能跑"的对照面。
        self.body_type_arg = body_type
        return self._bodies
    def GetBodies(self, body_type):
        self.body_type_arg = body_type
        return self._bodies
    def Select2(self, append, mark): return True

class FakeSelMgr(object):
    def __init__(self, n): self._n = n
    def GetSelectedObjectCount2(self, m): return self._n
    def CreateSelectData(self):
        return types.SimpleNamespace(Mark=0)

class FakeExt(object):
    def __init__(self, owner): self.owner = owner
    def SelectByID2(self, name, typ, x, y, z, append, mark, callout, opt):
        self.owner.sel_calls.append((name, typ, callout))
        try:
            if callout is not None and type(callout).__name__ == "VARIANT":
                raise Exception("Python instance can not be converted to a COM object")
        except Exception:
            raise
        if name in self.owner.selectable:
            self.owner.sel_ok += 1
            return True
        return False

class FakeAssembly(object):
    def __init__(self, comps, selectable=(), sel_count=2, success_api="AddMate5"):
        self._oleobj_ = self          # 让 early(model,'IAssemblyDoc') 能包装
        self.comps = comps
        self.selectable = set(selectable)
        self.sel_calls = []
        self.sel_ok = 0
        self.calls = []
        self.success_api = success_api
        self.SketchManager = object()
        self.FeatureManager = object()
        self.Extension = FakeExt(self)
        self.SelectionManager = FakeSelMgr(sel_count)
    def GetComponents(self, flag):
        return tuple(self.comps)
    def ClearSelection2(self, flag):
        self.sel_ok = 0
        return True
    def GetTitle(self): return "FakeAsm"
    def EditRebuild3(self): return True

class FakeSw(object):
    def ActivateDoc3(self, *a): return None
    def ActivateDoc2(self, *a): return None


def build(m, success_api="AddMate5", selectable=(), sel_count=2):
    doc = FakeAssembly(m, selectable=selectable, sel_count=sel_count,
                       success_api=success_api)
    return swapi.SWModel(FakeSw(), doc), doc

print("=" * 70)
print("0. API 存在性 / 枚举正确性")
print("=" * 70)
check("SWModel.create_mate", hasattr(swapi.SWModel, "create_mate"))
check("SWModel.create_lock_mate", hasattr(swapi.SWModel, "create_lock_mate"))
for name, val in (("concentric", 1), ("distance", 5), ("lock", 16),
                 ("tangent", 4), ("screw", 17), ("gear", 10)):
    got, _err = swapi.SWModel._parse_mate_type(name)
    check("枚举 %s = %d（旧表错值已修正）" % (name, val), got == val, got)
check("旧表错值不再出现：'lock' 不是 9", swapi.SWModel._parse_mate_type("lock")[0] != 9)
check("未知类型返回错误而非抛异常", swapi.SWModel._parse_mate_type("nope")[0] is None)

# 装载假 gen_py 模块（模拟早期绑定可用）
_orig_state = dict(swapi._EARLY_STATE)
swapi._EARLY_STATE["module"] = FakeMod
swapi._EARLY_STATE["failed"] = False

print("=" * 70)
print("1. BUG-12：组件文档/实体访问")
print("=" * 70)
f1 = FakeFace(0.5); f2 = FakeFace(2.0); f3 = FakeFace(1.0, normal=None)
comp = FakeComp("DSH_底部-1", bodies=(FakeBody([f1, f2, f3]),), doc="IModelDoc2")
m, doc = build([comp])
check("_component_doc 经 early(IComponent2) 拿到文档", m._component_doc(comp) == "IModelDoc2")
bl = m._component_bodies(comp)
check("_component_bodies 用 GetBodies2(0) 并 list() 化",
      len(bl) == 1 and comp.body_type_arg == 0, (len(bl), comp.body_type_arg))
best, area = m._best_planar_face(comp)
check("取最大平面（2.0），跳过法线为 None 的曲面", best is f2 and area == 2.0, (best, area))
check("非法组件不抛异常", m._component_bodies(None) == [] and m._component_doc(None) is None)

print("=" * 70)
print("2. BUG-11：create_mate 走专用 API（同心/距离）")
print("=" * 70)
m, doc = build([comp], selectable=["Front Plane"], success_api="AddDistanceMate")
r = m.create_mate("distance", ("Front Plane", comp), distance_mm=10)
check("distance 配合 ok=True", r.get("ok") is True, r)
check("命中专用 AddDistanceMate", "AddDistanceMate" in str(r.get("used_api")), r.get("used_api"))
check("AddDistanceMate 的 Distance = 10mm→0.01m",
      any(c[0] == "AddDistanceMate" and abs(c[1][2] - 0.01) < 1e-9 for c in doc.calls),
      [c for c in doc.calls if c[0] == "AddDistanceMate"])
check("装配链路 SelectByID2 的 Callout = None（早期绑定要求）",
      all(c[2] is None for c in doc.sel_calls if c[1] == "PLANE") and doc.sel_calls,
      doc.sel_calls)

m, doc = build([comp, FakeComp("DSH_上盖-1", bodies=(FakeBody([f2]),))],
               success_api="AddConcentricMateWithTolerance")
r = m.create_mate("concentric", ("DSH_底部-1", "DSH_上盖-1"))
check("concentric 配合 ok=True", r.get("ok") is True, r)
check("命中专用 AddConcentricMateWithTolerance",
      "AddConcentricMateWithTolerance" in str(r.get("used_api")), r.get("used_api"))

print("=" * 70)
print("3. BUG-11：回退链（专用 API 都不行 → AddMate5 全参 → CreateMate）")
print("=" * 70)
m, doc = build([comp], selectable=["Front Plane"], success_api="AddMate5")
r = m.create_mate("distance", ("Front Plane", comp), distance_mm=25)
check("回退到 AddMate5 且 ok=True", r.get("ok") is True and "AddMate5" in str(r.get("used_api")), r)
am = [c for c in doc.calls if c[0] == "AddMate5"][0][1]
check("AddMate5 至少 14 个参数（报告 3.3）", len(am) >= 14, len(am))
check("AddMate5 的浮点参数无 None（BUG-11 直接成因）",
      all(am[i] is not None for i in (3, 4, 5, 6, 7, 8, 9, 10)), am)
check("AddMate5 的 MateType 用正确枚举 5", am[0] == 5, am[0])

m, doc = build([comp], selectable=["Front Plane"], success_api="CreateMate")
r = m.create_mate("coincident", ("Front Plane", comp))
check("再回退到 CreateMate(None) 且 ok=True",
      r.get("ok") is True and "CreateMate" in str(r.get("used_api")), r)
cm = [c for c in doc.calls if c[0] == "CreateMate"][0][1]
check("CreateMate 只传 1 个参数（报告 3.2）", len(cm) == 1, cm)

print("=" * 70)
print("4. BUG-14：create_lock_mate → swMateLOCK=16")
print("=" * 70)
c1 = FakeComp("A-1", bodies=(FakeBody([f2]),))
c2 = FakeComp("B-1", bodies=(FakeBody([f2]),))
m, doc = build([c1, c2], success_api="AddMate5")
r = m.create_lock_mate(("A-1", "B-1"))
check("create_lock_mate ok=True", r.get("ok") is True, r)
check("mate_enum == 16（不是旧的 9）", r.get("mate_enum") == 16, r.get("mate_enum"))
am = [c for c in doc.calls if c[0] == "AddMate5"]
check("传给 SW 的 MateType 是 16", am and am[0][1][0] == 16, am[:1])
check("单组件也能建锁定（不因<2 直接报错）",
      m.create_lock_mate("A-1").get("error") is None)
check("空 entities 返回 ok=False 不抛异常",
      m.create_lock_mate(None).get("ok") is False)

print("=" * 70)
print("5. 失败路径必须如实回报（不谎报成功、不抛异常）")
print("=" * 70)
m, doc = build([comp], selectable=set(), sel_count=0, success_api="AddMate5")
r = m.create_mate("distance", ("不存在的面", comp), distance_mm=5)
check("选不中 → ok=False 且带 selected_count", r.get("ok") is False and "selected_count" in r, r)
check("给出可操作 hint", bool(r.get("hint")), r.get("hint"))
check("entities 少于 2 个 → ok=False", m.create_mate("distance", ("x",)).get("ok") is False)
check("entities=None → ok=False", m.create_mate("distance", None).get("ok") is False)
m2, _ = build([comp], success_api="none")
r = m2.create_mate("angle", ("Front Plane", comp), angle_deg=30)
check("全 API 失败 → ok=False + error", r.get("ok") is False and bool(r.get("error")), r)

print("=" * 70)
print("6. 晚期回退：早期绑定不可用时不得崩")
print("=" * 70)
swapi._EARLY_STATE["module"] = None
swapi._EARLY_STATE["failed"] = True
m, doc = build([comp], selectable=["Front Plane"], success_api="AddMate5")
check("early 不可用时 _component_doc 返回 None 不抛", m._component_doc(comp) is None)
check("early 不可用时 _component_bodies 走晚期 GetBodies2 仍拿到实体",
      len(m._component_bodies(comp)) == 1)
r = m.create_mate("distance", ("Front Plane", comp), distance_mm=10)
check("晚期下 create_mate 不抛异常且结构完整",
      isinstance(r, dict) and "ok" in r, r)

swapi._EARLY_STATE.clear(); swapi._EARLY_STATE.update(_orig_state)

print("=" * 70)
print("PASS=%d FAIL=%d" % (PASS, FAIL))
print("=" * 70)
sys.exit(0 if FAIL == 0 else 1)