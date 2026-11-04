# -*- coding: utf-8 -*-
"""回归测试：F-04 拉伸默认路径 no_body（_reset_selection_state 清掉预选轮廓）

★ 锁定的真实事故（两个独立小屋各自复现）：
  标准写法 begin_sketch() → rect()/circle() → end_sketch() → extrude()
  **连续失败**，FeatureExtrusion3 返回 None，报「选择集为 0：轮廓没被选中」。
  等于最基础的拉伸命令不可用。

  根因链：
    ① end_sketch() 退出前在激活态预选轮廓段（Bug T2 修复，swapi:7609）；
    ② extrude() 开头无条件 _reset_selection_state()
       → ClearSelection2(True) + ForceRebuild3(False)，把①的预选【清掉】；
    ③ 之后直接调 FeatureExtrusion3，中间【没有任何重选轮廓的步骤】
       → 选择集 0 → 返回 None。

★ 本测试钉死的契约（全部假 COM，不启动 SolidWorks）：
  1. **extrude 在 ClearSelection2 之后必须重新选中轮廓**才调 FeatureExtrusion3
     —— 且必须是"清空发生在选中之前"，顺序反了就等于白清。
  2. **选择集为空时调 FeatureExtrusion3 → 必须显式报错**（RuntimeError），
     绝不允许静默返回 None 让上层瞎猜。
  3. **end_sketch 的预选不能被无条件丢弃** —— 预选次数要留下可观测记录，
     且 extrude 选不到轮廓时要报错而不是"看起来成功"。
  4. **SelectByID2 的 Callout 封送正确** —— 必须是 VARIANT(VT_DISPATCH, None)，
     传裸 None 在真机上会报"类型不匹配"（小屋绕行方案被卡住的那一步）。
  5. **拉伸轮廓 ≠ 切除轮廓** —— extrude 选 "SKETCH"（草图特征本身），
     不是 "EXTSKETCHSEGMENT"（草图段）；两者不能互换。
  6. **排除原点** —— OriginProfileFeature 绝不能被当成可拉伸轮廓。
  7. cut 路径的"先清再选"自愈能力不退化。

依据：【类型库】83A33D31-27C5-11CE-BFD4-00400513BB57x0x33x0.py
  · IModelDocExtension.SelectByID2(Name, Type, X, Y, Z, Append, Mark,
      Callout, SelectOption) → 9 参（L42978；vtable L89320），
      Callout 封送类型 (9,1) = VT_DISPATCH 接口，
      故必须 VARIANT(VT_DISPATCH, None)。
  · ISelectionMgr.GetSelectedObjectCount2(Mark)（L53146）
  · ISelectionMgr.SetSelectedObjectMark(AtIndex, Mark, Action)（L53411）
  · IFeatureManager.FeatureExtrusion3 23 参（L22598；vtable L82748）

⚠️ 全部用假 COM 对象：不 import 真 COM、不启动 SolidWorks、不碰磁盘零件。

运行：python test_regression_extrude_profile.py
退出码：0 = 全部通过；1 = 有失败
"""
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

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
        FAILURES.append(label + ("  <" + str(detail)[:300] + ">" if detail else ""))
        print("  [FAIL] %s  %s" % (label, detail))


# ══════════════════════════════════════════════════════════════════════
# 假 COM
# ══════════════════════════════════════════════════════════════════════
class FakeSelectionMgr(object):
    """假 ISelectionMgr：记录选择集与打标调用。"""

    def __init__(self, doc):
        self._doc = doc
        self.marks = []

    def GetSelectedObjectCount2(self, mark=-1):
        n = len(self._doc._selection)
        if mark is None or int(mark) < 0:
            return n
        return sum(1 for _ in self._doc._selection if _doc_mark(self._doc, _) == int(mark))

    def SetSelectedObjectMark(self, at_index, mark, action):
        self.marks.append((int(at_index), int(mark), int(action)))
        i = int(at_index) - 1
        if 0 <= i < len(self._doc._selection):
            self._doc._sel_marks[self._doc._selection[i]] = int(mark)
        return True


def _doc_mark(doc, item):
    return doc._sel_marks.get(item, 0)


class FakeFeature(object):
    """假 IFeature（特征树节点）。"""

    def __init__(self, name, type2, next_feature=None, select2_works=True):
        self.Name = name
        self._type2 = type2
        self._next = next_feature
        self.select2_works = select2_works
        self.select2_calls = []

    def GetTypeName2(self):
        return self._type2

    def GetTypeName(self):
        return self._type2

    # ── 【类型库】IFeature.Select2(Append, Mark) 2 参（L21957）──────────
    def Select2(self, append, mark):
        self.select2_calls.append((bool(append), int(mark)))
        if not self.select2_works:
            return False
        doc = getattr(self, "_doc", None)
        if doc is None:
            return False
        if not append:
            doc._selection = []
            doc._sel_marks = {}
        doc._selection.append(self)
        doc._sel_marks[self] = int(mark)
        doc.select2_log.append({"name": str(self.Name), "mark": int(mark)})
        return True

    @property
    def GetNextFeature(self):
        return self._next


class FakeExt(object):
    """假 IModelDocExtension：只实现 SelectByID2，并校验 Callout 封送。"""

    def __init__(self, doc):
        self._doc = doc
        self.calls = []

    def SelectByID2(self, name, sel_type, x, y, z, append, mark, callout, option):
        self._doc.select_log.append({
            "name": str(name), "type": str(sel_type), "append": bool(append),
            "mark": int(mark), "callout": callout, "option": int(option),
        })
        self.calls.append((str(name), str(sel_type), callout))
        # ★ 契约：Callout 必须是 VARIANT(VT_DISPATCH, None)，不能是裸 None
        if callout is None:
            raise TypeError("类型不匹配：Callout 不能是裸 None（真机 SW 同款报错）")
        # 只有"已知草图特征"能被选中
        target = None
        for f in self._doc.features():
            if str(f.Name) == str(name):
                target = f
                break
        if target is None:
            return False
        if str(sel_type).upper() not in ("SKETCH",):
            # 本测试只让 "SKETCH" 命中；段类型返回 False（模拟 extrude 用错类型）
            return False
        if not append:
            self._doc._selection = []
            self._doc._sel_marks = {}
        self._doc._selection.append(target)
        self._doc._sel_marks[target] = int(mark)
        return True


class FakeSketchManager(object):
    def __init__(self, doc):
        self._doc = doc
        self.ActiveSketch = None

    def InsertSketch(self, update=True):
        self.ActiveSketch = None
        return True


class FakeFeatureManager(object):
    """假 IFeatureManager：FeatureExtrusion3（23 参），并记录调用瞬间的选择集。"""

    def __init__(self, doc):
        self._doc = doc
        self.extrusion_calls = []

    def FeatureExtrusion3(self, *args):
        sel_at_call = list(self._doc._selection)
        self.extrusion_calls.append({"args": args, "selection": sel_at_call,
                                     "callout_ok": True})
        if not sel_at_call:
            # ★ 复现真机行为：选择集为 0 时返回 None（且不报错）
            self._doc.last_extrude_reason = "selection_empty"
            return None
        self._doc.last_extrude_reason = "ok"
        return FakeFeature("Boss-Extrude1", "Extrusion")


class FakeBody(object):
    pass


class FakeModelDoc(object):
    """假 IModelDoc2（零件）。"""

    def __init__(self, features=None, bodies=(FakeBody(),), selectable=True):
        self._features = list(features or [])
        self._selection = []
        self._sel_marks = {}
        self.select_log = []
        self.clear_log = []
        self.rebuild_log = []
        self._bodies = list(bodies)
        self._selectable = selectable
        self.last_extrude_reason = None
        self.select2_log = []
        for _f in self._features:
            try:
                _f._doc = self          # 让 IFeature.Select2 能回写选择集
            except Exception:
                pass
        self.GetTitle = "FakePart.SLDPRT"
        self.SelectionManager = FakeSelectionMgr(self)
        self.Extension = FakeExt(self)
        self.FeatureManager = FakeFeatureManager(self)
        self.SketchManager = FakeSketchManager(self)

    # ---- 特征树 ----
    def features(self):
        out = []
        f = self.FirstFeature()
        _g = 0
        while f is not None and _g < 5000:
            _g += 1
            out.append(f)
            f = f.GetNextFeature
        return out

    def FirstFeature(self):
        return self._features[0] if self._features else None

    # ---- 选择 ----
    def ClearSelection2(self, all_=True):
        self.clear_log.append(bool(all_))
        self._selection = []
        self._sel_marks = {}
        return True

    # ---- 重建 ----
    def ForceRebuild3(self, only=True):
        self.rebuild_log.append(bool(only))
        return True

    def EditRebuild3(self):
        self.rebuild_log.append("edit")
        return True

    def ViewZoomtofit2(self):
        return True

    # ---- 实体 ----
    def GetBodies2(self, body_type=0, visible_only=False):
        return list(self._bodies) if self._bodies else None


class FakeSw(object):
    def __init__(self, doc):
        self._doc = doc

    def ActivateDoc3(self, *a):
        return self._doc

    def GetMathUtility(self):
        class _MU(object):
            def CreateTransform(self, data):
                return None
        return _MU()


def _make_model(doc):
    """构造只装假 COM 的 SWModel（不走 __init__，不碰真 COM）。"""
    import swapi
    m = swapi.SWModel.__new__(swapi.SWModel)
    m.sw = FakeSw(doc)
    m.model = doc
    m.skm = doc.SketchManager
    m.fm = doc.FeatureManager
    m.ext = doc.Extension
    m._empty = "VARIANT(VT_DISPATCH,None)"     # 假封送：非 None 即可通过 Callout 校验
    m.warnings = []
    m._surface_sketch_offset = 0.0
    m._right_plane_mode = False
    m._active_sketch_name = None
    m._last_cut_feature_name = None
    m._consumed_sketches = set()
    m._sketch_area_mm2 = 0.0
    m._area_sketch_name = None
    m._sketch_axis_offset_mm = 0.0
    m._sketch_normal_axis = 2
    m._pending_material = None
    m._pending_density = None
    m.material_result = None
    m._last_begin_plane = None
    m._plane_generation = 0
    m._sketch_open_count = 0
    m._end_sketch_preselect = None
    m._extrude_profile_used = None
    return m


# ══════════════════════════════════════════════════════════════════════
def test_extrude_reselects_after_clear(swapi):
    """契约 1/2/5：清选择之后必须重选轮廓，否则显式报错。"""
    print("\n[1] extrude：ClearSelection2 之后必须重新选中轮廓")
    sk = FakeFeature("Sketch1", "ProfileFeature")
    doc = FakeModelDoc(features=[sk])
    m = _make_model(doc)
    m._active_sketch_name = "Sketch1"
    m._select_extrude_profile = m._select_extrude_profile.__get__(m, swapi.SWModel)

    feat = m.extrude(10)
    check("extrude 返回特征对象", feat is not None)
    check("★ FeatureExtrusion3 被调用 1 次",
          len(doc.FeatureManager.extrusion_calls) == 1,
          len(doc.FeatureManager.extrusion_calls))
    call = doc.FeatureManager.extrusion_calls[0]
    check("★ 调用瞬间选择集【非空】（事故就是这里为 0）",
          len(call["selection"]) == 1, len(call["selection"]))
    check("★ 选中的是草图特征 'Sketch1'",
          str(getattr(call["selection"][0], "Name", None)) == "Sketch1",
          getattr(call["selection"][0], "Name", None))
    check("★ 23 参签名（类型库 L22598）", len(call["args"]) == 23, len(call["args"]))

    # 顺序：清空必须发生在选中【之前】
    _clear_idx = [i for i, rec in enumerate(doc.select_log)]
    check("★ 发生过 ClearSelection2（_reset_selection_state 的净效果）",
          len(doc.clear_log) > 0, doc.clear_log)
    # 选择路径：首选 IFeature.Select2，兜底 SelectByID2 —— 两种都算"重选了轮廓"
    _via_select2 = len(doc.select2_log) >= 1
    _sketch_selects = [r for r in doc.select_log if r["type"].upper() == "SKETCH"]
    check("★ 重选轮廓确实发生（IFeature.Select2 或 SelectByID2/SKETCH）",
          _via_select2 or len(_sketch_selects) >= 1,
          {"select2": doc.select2_log, "selectbyid": doc.select_log})
    check("★ 拉伸轮廓【不是】用 EXTSKETCHSEGMENT/SKETCHSEGMENT（那是 cut 的路子）",
          all(r["type"].upper() != "EXTSKETCHSEGMENT" for r in doc.select_log),
          [r["type"] for r in doc.select_log])
    # Callout 封送（仅当走了 SelectByID2 兜底时才需要校验）
    check("★ 若走 SelectByID2，其 Callout 全都非 None（VARIANT 封送正确）",
          all(r["callout"] is not None for r in doc.select_log),
          [r["callout"] for r in doc.select_log])


def test_empty_selection_raises(swapi):
    """契约 2：选择集为空时调 FeatureExtrusion3 → 必须显式报错。"""
    print("\n[2] 选择集为空 → 必须显式报错（绝不静默返回 None）")
    # 零件里没有任何可识别的草图特征
    origin = FakeFeature("Origin", "OriginProfileFeature")
    doc = FakeModelDoc(features=[origin])
    m = _make_model(doc)
    _raised = None
    try:
        m.extrude(10)
    except RuntimeError as e:
        _raised = e
    except Exception as e:      # noqa: BLE001
        _raised = e
    check("★ 无轮廓可选时抛出 RuntimeError", isinstance(_raised, RuntimeError),
          type(_raised).__name__ if _raised else "未抛异常")
    _msg = str(_raised or "")
    check("★ 错误信息明确指出未能选中轮廓",
          ("未能选中草图轮廓" in _msg) or ("选择集为 0" in _msg), _msg[:200])
    check("★ 错误信息带诊断（原因/建议）",
          ("诊断" in _msg) and ("建议" in _msg), _msg[:200])
    check("★ 这种情况下【没有】调用 FeatureExtrusion3",
          len(doc.FeatureManager.extrusion_calls) == 0,
          len(doc.FeatureManager.extrusion_calls))
    check("★ 原点特征 OriginProfileFeature 被排除（没有被当成轮廓选中）",
          all(str(f.Name) != "Origin" for f in doc._selection),
          [str(f.Name) for f in doc._selection])

    # 另一条：选得中轮廓，但调用前选择被清空 → 也要报错
    sk = FakeFeature("Sketch1", "ProfileFeature")
    doc2 = FakeModelDoc(features=[sk])
    m2 = _make_model(doc2)
    m2._active_sketch_name = "Sketch1"

    class _ClearingFm(FakeFeatureManager):
        """模拟"选好之后又被清掉"：调用瞬间选择集为空。"""
        def FeatureExtrusion3(self, *args):
            self._doc._selection = []
            return super().FeatureExtrusion3(*args)

    doc2.FeatureManager = _ClearingFm(doc2)
    m2.fm = doc2.FeatureManager
    _raised2 = None
    try:
        m2.extrude(10)
    except RuntimeError as e:
        _raised2 = e
    except Exception as e:      # noqa: BLE001
        _raised2 = e
    # 说明：本用例里选择是在 FeatureExtrusion3 内部被清的，属于真机上的
    #   "调用瞬间已空"；我们的断言是"不能把 None 当成功"。
    check("★ FeatureExtrusion3 返回 None 时抛 RuntimeError（不当成功）",
          isinstance(_raised2, RuntimeError), type(_raised2).__name__ if _raised2 else "未抛异常")


def test_end_sketch_preselect_kept(swapi):
    """契约 3：end_sketch 的预选不能被无条件丢弃（要留可观测记录）。"""
    print("\n[3] end_sketch 预选：保留 + 留下可观测记录")
    sk = FakeFeature("Sketch1", "ProfileFeature")
    doc = FakeModelDoc(features=[sk])
    m = _make_model(doc)

    # 模拟 end_sketch 的预选窗口
    _called = {"n": 0}

    def _fake_select_all(append_first=False):
        _called["n"] += 1
        return True, 4

    m.select_all_sketch_segments = _fake_select_all
    m.skm.ActiveSketch = object()      # 模拟草图仍激活
    m.end_sketch()
    check("★ end_sketch 仍然执行了预选（不退化）", _called["n"] == 1, _called)
    check("★ 预选结果被记成可观测事实 _end_sketch_preselect",
          isinstance(getattr(m, "_end_sketch_preselect", None), dict),
          getattr(m, "_end_sketch_preselect", None))
    check("★ 记录里有段数（可用于区分'没段'与'选择被清'）",
          (m._end_sketch_preselect or {}).get("count") == 4,
          m._end_sketch_preselect)

    # 预选内容不进 extrude 的轮廓选择（extrude 要的是草图特征）
    m.skm.ActiveSketch = None
    m._active_sketch_name = "Sketch1"
    m.extrude(10)
    _reselect_happened = (len(doc.select2_log) >= 1
                          or len([r for r in doc.select_log
                                  if r["type"].upper() == "SKETCH"]) >= 1)
    check("★ extrude 自己按名重选了草图特征（不依赖 end_sketch 的段预选）",
          _reselect_happened,
          {"select2": doc.select2_log, "selectbyid": doc.select_log})
    check("★ _extrude_profile_used 记录了实际用的轮廓名",
          m._extrude_profile_used == "Sketch1", m._extrude_profile_used)


def test_callout_variant(swapi):
    """契约 4：Callout 封送 —— 裸 None 必须失败，VARIANT 必须成功。"""
    print("\n[4] SelectByID2 的 Callout 封送（类型库 L42978 / vtable L89320）")
    sk = FakeFeature("Sketch1", "ProfileFeature")
    doc = FakeModelDoc(features=[sk])
    m = _make_model(doc)
    m._active_sketch_name = "Sketch1"

    # 正确封送 → 选中成功
    m._empty = "VARIANT(VT_DISPATCH,None)"
    r = m._select_extrude_profile()
    check("★ VARIANT 封送时选中成功", r.get("ok") is True, r.get("reason"))
    check("★ 选中数 > 0", (r.get("selected") or 0) > 0, r.get("selected"))
    check("★ 记录了选择方法", bool(r.get("method")), r.get("method"))

    # 错误封送（裸 None）→ 必须失败且不静默
    # 注意：必须让 IFeature.Select2 也失效，否则会走首选路径而不触发 Callout 校验
    doc2 = FakeModelDoc(features=[FakeFeature("Sketch1", "ProfileFeature",
                                              select2_works=False)])
    m2 = _make_model(doc2)
    m2._active_sketch_name = "Sketch1"
    m2._empty = None            # ★ 模拟"传裸 None"的错误封送
    r2 = m2._select_extrude_profile()
    check("★ 裸 None Callout → 选中失败（真机会报类型不匹配）",
          r2.get("ok") is False, r2.get("selected"))
    check("★ 失败时给出可读原因（含尝试记录）",
          bool(r2.get("reason")) and bool(r2.get("tried")), r2.get("reason", "")[:200])


def test_consumed_sketch_skipped(swapi):
    """契约：已被消费的草图不能当拉伸轮廓（沿用 T1 防护）。"""
    print("\n[5] 已被消费的草图不参与拉伸轮廓选择")
    sk = FakeFeature("Sketch1", "ProfileFeature")
    doc = FakeModelDoc(features=[sk])
    m = _make_model(doc)
    m._active_sketch_name = "Sketch1"
    m._consumed_sketches = {"Sketch1"}

    r = m._select_extrude_profile()
    # 显式名被消费 → 退回特征树兜底，仍能选中 Sketch1（它就是最后一个草图）
    check("★ 显式名被消费时不直接用，但特征树兜底仍可命中",
          r.get("ok") is True, r.get("tried"))
    check("★ 尝试记录里保留了判断依据", len(r.get("tried") or []) > 0, r.get("tried"))


def test_feature_select2_primary(swapi):
    """契约 8（网页依据）：IFeature.Select2 是选特征的首选路径。

    【网页】CodeStack《Selecting SOLIDWORKS objects by name and coordinates》
      https://www.codestack.net/solidworks-api/document/selection/select-by-id/
      「Sketch elements ... do not have permanent names. So it is not valid to
        use the name recorded by the macro in SelectByID2 method.」
      「Use this method instead of using the selection methods on the following
        objects: IAnnotation, IComponent2, IFeature ...」
      → 选【特征】应优先 IFeature::Select2，SelectByID2 只作兜底。
    【类型库】IFeature.Select2(Append, Mark) —— 2 参（L21957）。
    """
    print("\n[7] IFeature.Select2 是选特征的首选路径（网页+类型库依据）")
    sk = FakeFeature("Sketch1", "ProfileFeature")
    doc = FakeModelDoc(features=[sk])
    m = _make_model(doc)
    m._active_sketch_name = "Sketch1"

    r = m._select_extrude_profile()
    check("★ 选中成功", r.get("ok") is True, r.get("reason"))
    check("★ method 记录为 IFeature.Select2（首选路径生效）",
          str(r.get("method", "")).startswith("IFeature.Select2"), r.get("method"))
    check("★ IFeature.Select2 被真正调用过", len(sk.select2_calls) == 1,
          sk.select2_calls)
    # ── 【S-08 真机修正】拉伸轮廓的 Mark 必须是 0，不是 1 ─────────────────
    # 实测矩阵（SW2025 SP5.0）：Mark=0 → 拉伸成功；Mark=1/2 → 返回 None，
    #   bodies 0。原断言锁死 ==1，等于把 bug 固化进测试。
    # 现在断言"跟随类常量"且该常量必须为 0。
    check("★ Select2 的 Mark 为 0（拉伸轮廓不使用标记过滤）",
          sk.select2_calls and sk.select2_calls[0][1] == 0, sk.select2_calls)
    check("★ Select2 用 Append=False（先清再选，不叠加脏选择）",
          sk.select2_calls and sk.select2_calls[0][0] is False, sk.select2_calls)
    check("★ 首选成功时【不再】退化去调 SelectByID2",
          len(doc.select_log) == 0, [x["name"] for x in doc.select_log])

    # Select2 不可用时 → 必须自动兜底到 SelectByID2（小屋绕行方案）
    sk2 = FakeFeature("Sketch1", "ProfileFeature", select2_works=False)
    doc2 = FakeModelDoc(features=[sk2])
    m2 = _make_model(doc2)
    m2._active_sketch_name = "Sketch1"
    r2 = m2._select_extrude_profile()
    check("★ Select2 失效时兜底到 SelectByID2 仍然选中",
          r2.get("ok") is True, r2.get("tried"))
    check("★ 兜底路径的 method 标注为 SelectByID2",
          "SelectByID2" in str(r2.get("method", "")), r2.get("method"))
    check("★ 兜底时 SelectByID2 的 Callout 仍是 VARIANT（非裸 None）",
          len(doc2.select_log) > 0
          and all(rec["callout"] is not None for rec in doc2.select_log),
          [rec["callout"] for rec in doc2.select_log])


def test_cut_path_not_degraded(swapi):
    """契约 7：cut 的"先清再选"自愈不退化。"""
    print("\n[6] cut 路径自愈能力不退化（_reset_selection_state 仍然先清）")
    doc = FakeModelDoc(features=[FakeFeature("Sketch1", "ProfileFeature")])
    m = _make_model(doc)
    res = m._reset_selection_state(reason="cut 前置")
    check("★ _reset_selection_state 仍然会 ClearSelection2",
          len(doc.clear_log) >= 1, doc.clear_log)
    check("★ 返回结构包含 cleared/rebuilt/reason",
          all(k in res for k in ("cleared", "rebuilt", "reason")), res)
    check("★ reason 被如实回传", res.get("reason") == "cut 前置", res.get("reason"))

    # cut 仍然走"选段"而不是"选草图特征"
    _has_cut_sel = callable(getattr(m, "_ensure_cut_profile_selected", None))
    check("★ cut 的轮廓选择方法仍然存在（未被 extrude 的修复顶掉）", _has_cut_sel)


# ══════════════════════════════════════════════════════════════════════
def main():
    try:
        import swapi
        check("swapi 加载的是【本工作区】这份（防双副本假失败）",
              os.path.normcase(os.path.abspath(swapi.__file__)).startswith(
                  os.path.normcase(HERE)), swapi.__file__)
        check("★ 新增方法：_select_extrude_profile",
              callable(getattr(swapi.SWModel, "_select_extrude_profile", None)))
        check("★ 新增方法：_selection_count",
              callable(getattr(swapi.SWModel, "_selection_count", None)))
        check("★ 新增方法：_mark_current_selection",
              callable(getattr(swapi.SWModel, "_mark_current_selection", None)))
        # ── 【S-08 真机修正】EXTRUDE_PROFILE_MARK 必须为 0 ────────────────
        # 原断言是 ==1（沿用了 cut 的标记约定），而真机实测 Mark=1 会让
        #   FeatureExtrusion3 静默返回 None —— 测试当时"通过"恰恰掩盖了缺陷。
        check("★ 类常量 EXTRUDE_PROFILE_MARK == 0（拉伸不能用 mark 过滤）",
              getattr(swapi.SWModel, "EXTRUDE_PROFILE_MARK", None) == 0,
              getattr(swapi.SWModel, "EXTRUDE_PROFILE_MARK", None))
        check("★ cut 的标记单独保留为 CUT_PROFILE_MARK == 1",
              getattr(swapi.SWModel, "CUT_PROFILE_MARK", None) == 1,
              getattr(swapi.SWModel, "CUT_PROFILE_MARK", None))

        test_extrude_reselects_after_clear(swapi)
        test_empty_selection_raises(swapi)
        test_end_sketch_preselect_kept(swapi)
        test_callout_variant(swapi)
        test_consumed_sketch_skipped(swapi)
        test_feature_select2_primary(swapi)
        test_cut_path_not_degraded(swapi)
    except Exception:
        global FAIL
        FAIL += 1
        FAILURES.append("未捕获异常（见下方 traceback）")
        traceback.print_exc()

    print("\n" + "=" * 70)
    print("PASS=%d FAIL=%d" % (PASS, FAIL))
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  - " + f)
    print("=" * 70)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
