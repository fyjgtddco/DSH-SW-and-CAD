# -*- coding: utf-8 -*-
"""回归测试：装配定位（AddComponent5 坐标被忽略 → 假装配 51→1）

★ 锁定的真实事故（2026-10-30 四工位转台，15 小时长跑）：
    n_planned=51 / n_ok=51 / n_coord_applied=1
  —— 51 个组件全部"插入成功"，只有 1 个真正应用了坐标。
  结果 764KB 的 .SLDASM 是「插了但没定位」的假装配：
  干涉检查、联锁验证、12 张工程图全部无法做。

★ 本测试钉死的契约（全部用假 COM，不启动 SolidWorks）：
  1. **坐标未生效必须 ok:false** —— 传了非零坐标、但读回位置不符时，
     add_component 绝对不许返回 ok:true（事故里 51 件全报 ok 的直接原因）。
  2. **位姿读不回来不算通过** —— GetTotalTransform/Transform2 全挂时，
     coord_applied 必须是 None 而不是 True，ok 必须是 false。
  3. **verify_assembly_placement 能判出假装配** —— 51 件里只有 1 件带坐标，
     必须 n_ok=1 / n_expected=51 / assembly_ok=False / fake_assembly_risk=True。
  4. **矩阵布局必须是 SolidWorks 官方排布** —— 平移在 ArrayData 的 9/10/11，
     【不是】3/7/11（.NET 行优先）。历史实现写/读都错位，是本事故根因之一。
  5. **detect_fake_assembly 能独立判假装配** —— 即使没有设计坐标台账，
     只要组件全堆在原点就判假装配。
  6. **add_components_batch 逐件断言** —— 批量插入走 AddComponents 的
     16×N 变换矩阵（官方布局），插入后必须逐件读回；位姿没生效 → ok:false。

依据（写死在断言里，防止有人"顺手改回去"）：
  【类型库】83A33D31-27C5-11CE-BFD4-00400513BB57x0x33x0.py
    · IAssemblyDoc.AddComponent5(CompName, ConfigOption, NewConfigName,
      UseConfigForPartReferences, ExistingConfigName, X, Y, Z)
    · IComponent2.GetTotalTransform(IncludePresentationXform) -> IMathTransform
    · IComponent2.Transform2（可读可写属性）
    · IMathUtility.CreateTransform(ArrayDataIn) —— 【单参数】16 元数组
  【网页】CodeStack https://www.codestack.net/solidworks-api/geometry/transformation/
    「0-8 - rotational part of the matrix / 9-11 - translation part (x, y, z)」
  【网页】CadBooster https://cadbooster.com/understanding-math-and-mathtransform-in-the-solidworks-api/
    「Transforms contains an array of [(Names count) x 16] doubles.」

⚠️ 全部用假 COM 对象 / monkeypatch：不 import win32com 的真 COM，
   不启动 SolidWorks，不碰磁盘上的 .sldasm。

运行：python test_regression_assembly_placement.py
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
# 假 COM：MathTransform / IComponent2 / IAssemblyDoc
# ══════════════════════════════════════════════════════════════════════
def _m16(x_mm=0.0, y_mm=0.0, z_mm=0.0, rot=None):
    """按【SolidWorks 官方布局】造 16 元数组：0..8 旋转 / 9..11 平移(米) / 12..15 尾行。"""
    r = rot or [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
    return [r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8],
            x_mm * 0.001, y_mm * 0.001, z_mm * 0.001,
            0.0, 0.0, 0.0, 1.0]


class FakeTransform(object):
    """假 IMathTransform：只暴露 ArrayData（真 SW 也是主要靠它）。"""

    def __init__(self, arr):
        self.ArrayData = list(arr)


class FakeComp(object):
    """假 IComponent2。

    mode:
      "honor"    —— GetTotalTransform 如实返回真实位置（坐标生效）
      "ignore"   —— 坐标被忽略：永远返回原点（事故现场）
      "unreadable" —— 位姿完全读不回来（GetTotalTransform/Transform2 都抛）
    """

    def __init__(self, name, mode="honor", pos_mm=(0.0, 0.0, 0.0), rot=None):
        self.Name2 = name
        self._mode = mode
        self._pos = list(pos_mm)
        self._rot = rot
        self._assigned = None

    # ---- 读位姿 ----
    def GetTotalTransform(self, include=True):
        if self._mode == "unreadable":
            raise RuntimeError("GetTotalTransform: DISP_E_MEMBERNOTFOUND(假 COM)")
        return FakeTransform(_m16(self._pos[0], self._pos[1], self._pos[2], self._rot))

    @property
    def Transform2(self):
        if self._mode == "unreadable":
            raise RuntimeError("Transform2: 只读属性不可用(假 COM)")
        return FakeTransform(_m16(self._pos[0], self._pos[1], self._pos[2], self._rot))

    @Transform2.setter
    def Transform2(self, mt):
        self._assigned = mt
        if self._mode == "honor":
            a = getattr(mt, "ArrayData", mt)
            self._pos = [float(a[9]) * 1000.0, float(a[10]) * 1000.0,
                         float(a[11]) * 1000.0]
        # "ignore" 模式：写入被静默忽略（真实事故现象）


class FakeAsmDoc(object):
    """假 IModelDoc2（装配体）。

    ignore_coords=True 复现事故：AddComponent5 的 X/Y/Z 被静默忽略，
    返回一个落在原点的组件。
    """

    def __init__(self, comps=None, ignore_coords=True, unreadable=False,
                 batch_mode="ok"):
        self.comps = list(comps or [])
        self.ignore_coords = ignore_coords
        self.unreadable = unreadable
        self.batch_mode = batch_mode          # "ok" / "noop" / "missing"
        self.add_calls = []
        self.batch_calls = []
        self.GetTitle = "FakeAsm.SLDASM"

    # ---- IAssemblyDoc 插入 ----
    def AddComponent5(self, *args):
        self.add_calls.append(("AddComponent5", args))
        if self.unreadable:
            mode = "unreadable"
        else:
            mode = "ignore" if self.ignore_coords else "honor"
        x, y, z = (float(args[-3]), float(args[-2]), float(args[-1]))
        pos = (0.0, 0.0, 0.0) if self.ignore_coords else (x * 1000.0,
                                                          y * 1000.0,
                                                          z * 1000.0)
        # 注意：SW 内部单位是米，假 COM 也按米收
        c = FakeComp("C%d" % (len(self.comps) + 1), mode=mode, pos_mm=pos)
        self.comps.append(c)
        return c

    # ---- 批量插入 ----
    def AddComponents(self, names, transforms):
        self.batch_calls.append(("AddComponents", list(names), list(transforms)))
        if self.batch_mode == "missing":
            raise AttributeError("AddComponents 不存在(假 COM)")
        out = []
        for i, n in enumerate(names):
            arr = transforms[i * 16:(i + 1) * 16]
            if self.batch_mode == "noop":
                pos = (0.0, 0.0, 0.0)       # 变换矩阵被忽略（事故）
            else:
                pos = (arr[9] * 1000.0, arr[10] * 1000.0, arr[11] * 1000.0)
            c = FakeComp("B%d" % (len(self.comps) + 1), mode="honor", pos_mm=pos)
            self.comps.append(c)
            out.append(c)
        return out

    AddComponents3 = AddComponents

    # ---- 其它被 swapi 用到的 ----
    def GetComponents(self, top_only=False):
        return list(self.comps) if self.comps else None

    def GetComponentCount(self, top_only=False):
        return len(self.comps)

    def ClearSelection2(self, flag=True):
        return True

    def ForceRebuild3(self, only=True):
        return True

    def GetTitle_(self):
        return self.GetTitle


class FakeSw(object):
    """假 SldWorks.Application：只提供 OpenDoc6 / ActivateDoc3 / GetMathUtility。"""

    def __init__(self, parent_doc):
        self._doc = parent_doc
        self.math_calls = []

    def OpenDoc6(self, *args):
        return object()          # 预打开零件：非 None 即可

    def ActivateDoc3(self, *args):
        return self._doc

    @property
    def GetMathUtility(self):
        outer = self

        class _MU(object):
            def CreateTransform(self, data):
                outer.math_calls.append(("CreateTransform", len(list(data))))
                return FakeTransform([float(v) for v in data])

            def ICreateTransform(self, data):
                inner = list(data)
                return FakeTransform(list(inner))

        return _MU()


def _make_model(doc):
    """构造一个只装了假 COM 的 SWModel（不走 __init__，不碰真 COM）。"""
    import swapi
    m = swapi.SWModel.__new__(swapi.SWModel)
    m.sw = FakeSw(doc)
    m.model = doc
    m.warnings = []
    m._last_transform_diag = ""
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
    m._empty = None
    return m


def _touch(path):
    """造一个存在的 .sldprt 占位文件（add_component 会查 os.path.isfile）。"""
    with open(path, "wb") as f:
        f.write(b"FAKE")
    return path


# ══════════════════════════════════════════════════════════════════════
def test_matrix_layout(swapi):
    """根因①：矩阵布局必须是 SW 官方排布（平移在 9/10/11）。"""
    print("\n[1] MathTransform 布局（类型库 + CodeStack/CadBooster 依据）")
    arr = swapi._translation_matrix_m16(10.0, 20.0, 30.0, 0.001)
    check("16 元数组长度", len(arr) == 16, len(arr))
    check("★ 平移在索引 9/10/11（不是 3/7/11）",
          arr[9:12] == [0.01, 0.02, 0.03], arr[9:12])
    check("★ 索引 3/7/11 是旋转子矩阵的 0（不是平移）",
          arr[3] == 0.0 and arr[7] == 0.0 and arr[11] == 0.03,
          [arr[3], arr[7], arr[11]])
    check("索引 0..8 是单位旋转阵", arr[0:9] == [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0], arr[0:9])
    check("尾行 12..15 = (0,0,0,1)", arr[12:16] == [0.0, 0.0, 0.0, 1.0], arr[12:16])

    pos = swapi._position_from_transform_mm(FakeTransform(arr), units="mm")
    check("★ 读回位置走 9/10/11 且换算回 mm",
          pos is not None and [round(v, 6) for v in pos] == [10.0, 20.0, 30.0], pos)
    check("读不出数组时返回 None（绝不编造位置）",
          swapi._position_from_transform_mm(None) is None
          and swapi._position_from_transform_mm(FakeTransform([1.0] * 5)) is None)
    rot = swapi._rotation_from_transform(FakeTransform(arr))
    check("旋转子矩阵取 0..8", rot == [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0], rot)

    # _make_math_transform 也必须是官方布局（它是 set_component_transform 的入口）
    m = _make_model(FakeAsmDoc())
    mt, _ = m._make_math_transform(10.0, 20.0, 30.0, units="mm")
    a2 = getattr(mt, "ArrayData", None)
    check("★ _make_math_transform 产出的矩阵平移在 9/10/11",
          a2 is not None and a2[9:12] == [0.01, 0.02, 0.03], a2)


def test_add_component_contract(swapi, tmpdir):
    """契约 1/2：坐标没生效必须 ok:false；位姿读不回来也不算通过。"""
    print("\n[2] add_component：坐标未生效 → ok:false（事故 51 件全报 ok 的反例）")
    part = _touch(os.path.join(tmpdir, "part_demo.sldprt"))

    # ① 事故复现：AddComponent5 忽略坐标
    doc = FakeAsmDoc(ignore_coords=True)
    m = _make_model(doc)
    r = m.add_component(part, 113.0, 0.0, 40.0)
    check("组件确实插入了", r.get("component") is not None and r.get("component_count") == 1)
    check("★ coord_applied=False（坐标被忽略）", r.get("coord_applied") is False, r.get("coord_applied"))
    check("★ ok=False —— 绝不把『插进去了』当成『装配好了』",
          r.get("ok") is False, r.get("ok"))
    check("★ 带 fake_assembly_risk 标记", r.get("fake_assembly_risk") is True, r.get("fake_assembly_risk"))
    check("error 明确说坐标未验证通过",
          "坐标未验证通过" in str(r.get("error")), r.get("error"))
    check("未验证通过时不给 next_action 画饼（仍给自救路径）",
          isinstance(r.get("next_action"), str) and "place_components_by_coords" in r.get("next_action"))

    # ② 坐标读不回来：coord_applied 必须是 None（不是 True），ok=False
    doc2 = FakeAsmDoc(ignore_coords=False, unreadable=True)
    m2 = _make_model(doc2)
    r2 = m2.add_component(part, 50.0, 0.0, 0.0)
    check("★ 位姿读不回来时 coord_applied=None（不是 True）",
          r2.get("coord_applied") is None, r2.get("coord_applied"))
    check("★ 位姿读不回来时 ok=False（读不回≠通过）", r2.get("ok") is False, r2.get("ok"))

    # ③ 坐标生效（含补救链）：ok=True 且 coord_applied=True
    doc3 = FakeAsmDoc(ignore_coords=False)
    m3 = _make_model(doc3)
    r3 = m3.add_component(part, 10.0, 20.0, 30.0)
    check("坐标生效时 coord_applied=True", r3.get("coord_applied") is True, r3.get("coord_applied"))
    check("坐标生效时 ok=True", r3.get("ok") is True, r3.get("ok"))

    # ④ 零坐标不受本闸门影响（原点插入本来就不需要坐标校验）
    doc4 = FakeAsmDoc(ignore_coords=True)
    m4 = _make_model(doc4)
    r4 = m4.add_component(part, 0.0, 0.0, 0.0)
    check("零坐标插入不因『坐标未生效』被误判为失败",
          r4.get("ok") is True, (r4.get("ok"), r4.get("error")))


def test_verify_assembly_placement(swapi, tmpdir):
    """契约 3：verify_assembly_placement 能判出【假装配】。"""
    print("\n[3] verify_assembly_placement：判出假装配（51→1 事故场景）")
    # 场景：51 件里只有第 1 件真的落在目标坐标，其余 50 件全堆在原点
    comps = [FakeComp("C1", mode="honor", pos_mm=(0.0, 0.0, 90.0))]
    for i in range(2, 52):
        comps.append(FakeComp("C%d" % i, mode="ignore", pos_mm=(0.0, 0.0, 0.0)))
    doc = FakeAsmDoc(comps=comps)
    m = _make_model(doc)
    expect = [("C1", 0.0, 0.0, 90.0)]
    for i in range(2, 52):
        expect.append(("C%d" % i, float(i * 10), 0.0, 0.0))
    r = m.verify_assembly_placement(expect)
    check("★ 期望 51 件", r.get("n_expected") == 51, r.get("n_expected"))
    check("★ n_ok=1（只有 1 件真的带坐标）", r.get("n_ok") == 1, r.get("n_ok"))
    check("★ assembly_ok=False", r.get("assembly_ok") is False, r.get("assembly_ok"))
    check("★ fake_assembly_risk=True", r.get("fake_assembly_risk") is True, r.get("fake_assembly_risk"))
    check("★ verdict 里写明【假装配】", "假装配" in str(r.get("verdict")), r.get("verdict"))
    check("n_mismatch=50", r.get("n_mismatch") == 50, r.get("n_mismatch"))
    check("每件都有 delta_mm", all("delta_mm" in it for it in r["items"][1:]), r["items"][1])

    # 全部达标 → 通过
    ok_comps = [FakeComp("D1", mode="honor", pos_mm=(0.0, 0.0, 90.0)),
                FakeComp("D2", mode="honor", pos_mm=(120.0, 0.0, 40.0))]
    m_ok = _make_model(FakeAsmDoc(comps=ok_comps))
    r_ok = m_ok.verify_assembly_placement([("D1", 0, 0, 90), ("D2", 120, 0, 40)])
    check("全部达标时 assembly_ok=True", r_ok.get("assembly_ok") is True, r_ok.get("verdict"))
    check("全部达标时 fake_assembly_risk=False", r_ok.get("fake_assembly_risk") is False)

    # 位姿读不回来 → 不许通过
    bad = [FakeComp("E1", mode="unreadable")]
    m_bad = _make_model(FakeAsmDoc(comps=bad))
    r_bad = m_bad.verify_assembly_placement([("E1", 0, 0, 0)])
    check("★ 位姿读不回来 → n_verified=0 / assembly_ok=False",
          r_bad.get("n_verified") == 0 and r_bad.get("assembly_ok") is False,
          (r_bad.get("n_verified"), r_bad.get("assembly_ok")))

    # 组件名找不到 → missing，同样不通过
    m_miss = _make_model(FakeAsmDoc(comps=[FakeComp("F1", mode="honor")]))
    r_miss = m_miss.verify_assembly_placement([("不存在的组件", 1, 2, 3)])
    check("★ 找不到组件时进 missing 且不通过",
          r_miss.get("missing") == ["不存在的组件"] and r_miss.get("assembly_ok") is False,
          r_miss.get("missing"))

    # 空期望 → 明确报错，不能"空表算通过"
    m_empty = _make_model(FakeAsmDoc())
    r_empty = m_empty.verify_assembly_placement([])
    check("★ 空期望表不算通过（防空转）",
          r_empty.get("assembly_ok") is False and "error" in r_empty,
          r_empty.get("error"))

    # 旋转异常 → 即便位置对也判可疑
    rot45 = [0.7071, -0.7071, 0.0, 0.7071, 0.7071, 0.0, 0.0, 0.0, 1.0]
    m_rot = _make_model(FakeAsmDoc(comps=[FakeComp("G1", mode="honor",
                                                  pos_mm=(5.0, 0.0, 0.0), rot=rot45)]))
    r_rot = m_rot.verify_assembly_placement([("G1", 5, 0, 0)])
    check("★ 旋转非单位阵 → 位置对也不计通过",
          r_rot.get("n_ok") == 0 and r_rot.get("assembly_ok") is False, r_rot.get("items"))

    # 前缀匹配（"DSH_底座" → "DSH_底座-1"）
    m_pre = _make_model(FakeAsmDoc(comps=[FakeComp("DSH_底座-1", mode="honor",
                                                  pos_mm=(3.0, 0.0, 0.0))]))
    r_pre = m_pre.verify_assembly_placement([("DSH_底座", 3, 0, 0)])
    check("组件名前缀匹配可用", r_pre.get("assembly_ok") is True, r_pre.get("missing"))


def test_read_component_pose(swapi):
    """契约 4：read_component_pose 语义（读回成功≠位置正确）。"""
    print("\n[4] read_component_pose：读回成功与位置正确必须分开")
    m = _make_model(FakeAsmDoc())
    p = m.read_component_pose(FakeComp("P1", mode="honor", pos_mm=(1.0, 2.0, 3.0)))
    check("读回成功 verified=True", p.get("verified") is True, p.get("error"))
    check("位置按 9/10/11 换算回 mm",
          [round(v, 4) for v in p["position_mm"]] == [1.0, 2.0, 3.0], p.get("position_mm"))
    check("source 记录实际读法", p.get("source") == "GetTotalTransform", p.get("source"))
    check("旋转为单位阵时 rotation_ok=True", p.get("rotation_ok") is True)
    p2 = m.read_component_pose(FakeComp("P2", mode="unreadable"))
    check("★ 读不回来 verified=False 且 position_mm=None",
          p2.get("verified") is False and p2.get("position_mm") is None, p2)
    p3 = m.read_component_pose(None)
    check("组件为 None 时明确报错", p3.get("ok") is False and "error" in p3)


def test_detect_fake_assembly(swapi):
    """契约 5：没有设计坐标台账也能判假装配。"""
    print("\n[5] detect_fake_assembly：无台账也能判假装配")
    comps = [FakeComp("H1", mode="ignore", pos_mm=(0.0, 0.0, 0.0))]
    for i in range(2, 52):
        comps.append(FakeComp("H%d" % i, mode="ignore", pos_mm=(0.0, 0.0, 0.0)))
    r = _make_model(FakeAsmDoc(comps=comps)).detect_fake_assembly()
    check("★ 51 件全在原点 → fake_assembly=True", r.get("fake_assembly") is True, r.get("verdict"))
    check("n_at_origin=51", r.get("n_at_origin") == 51, r.get("n_at_origin"))
    check("★ verdict 写明【假装配】", "假装配" in str(r.get("verdict")), r.get("verdict"))
    check("ok=False", r.get("ok") is False)

    good = [FakeComp("J1", mode="honor", pos_mm=(0.0, 0.0, 20.0)),
            FakeComp("J2", mode="honor", pos_mm=(150.0, 0.0, 20.0))]
    r2 = _make_model(FakeAsmDoc(comps=good)).detect_fake_assembly()
    check("分布正常时 fake_assembly=False", r2.get("fake_assembly") is False, r2.get("verdict"))

    r3 = _make_model(FakeAsmDoc(comps=[FakeComp("K1", mode="unreadable")])).detect_fake_assembly()
    check("★ 位姿读不回来 → 按假装配处理（无法证明被定位过）",
          r3.get("fake_assembly") is True, r3.get("verdict"))

    r4 = _make_model(FakeAsmDoc()).detect_fake_assembly()
    check("空装配体不报通过", r4.get("ok") is False and "error" in r4, r4.get("error"))


def test_add_components_batch(swapi, tmpdir):
    """契约 6：批量插入（16×N 变换矩阵）+ 逐件断言。"""
    print("\n[6] add_components_batch：AddComponents 的 16×N 矩阵 + 逐件断言")
    p1 = _touch(os.path.join(tmpdir, "b1.sldprt"))
    p2 = _touch(os.path.join(tmpdir, "b2.sldprt"))
    coords = [(10.0, 0.0, 0.0), (20.0, 5.0, 0.0)]

    # 正常：变换矩阵被采纳
    doc = FakeAsmDoc(batch_mode="ok")
    m = _make_model(doc)
    r = m.add_components_batch([p1, p2], coords)
    check("调用了 AddComponents 系列", len(doc.batch_calls) == 1, doc.batch_calls)
    _api, _names, _tf = doc.batch_calls[0]
    check("★ Transforms = 每件 16 个 double（2 件 → 32）", len(_tf) == 32, len(_tf))
    check("★ 第 1 件的平移在 9/10/11", _tf[9:12] == [0.01, 0.0, 0.0], _tf[9:12])
    check("★ 第 2 件矩阵从索引 16 开始", _tf[16 + 9:16 + 12] == [0.02, 0.005, 0.0],
          _tf[16 + 9:16 + 12])
    check("批量插入后逐件读回：n_verified=2", r.get("n_verified") == 2, r.get("n_verified"))
    check("批量插入全部达标 → ok=True", r.get("ok") is True, r.get("placement", {}).get("verdict"))
    check("positions_ok=True", r.get("positions_ok") is True)

    # 事故形态：变换矩阵被忽略 → 必须 ok=False
    doc2 = FakeAsmDoc(batch_mode="noop")
    m2 = _make_model(doc2)
    r2 = m2.add_components_batch([p1, p2], coords)
    check("★ 批量坐标被忽略时 ok=False", r2.get("ok") is False, r2.get("ok"))
    check("★ 批量坐标被忽略时 positions_ok=False", r2.get("positions_ok") is False)
    check("错误里给出假装配结论", "假装配" in str(r2.get("error")), r2.get("error"))

    # coords 数量不符 → 明确报错（不许悄悄补原点）
    r3 = _make_model(FakeAsmDoc()).add_components_batch([p1, p2], [(1, 2, 3)])
    check("★ coords 数量不符时明确报错", "error" in r3 and r3.get("ok") is False, r3.get("error"))

    # API 全不可用 → 明确报错（不假装成功）
    r4 = _make_model(FakeAsmDoc(batch_mode="missing")).add_components_batch([p1], [(1, 2, 3)])
    check("★ AddComponents* 不可用时明确报错", "error" in r4 and r4.get("ok") is False, r4.get("error"))


# ══════════════════════════════════════════════════════════════════════
def main():
    import tempfile
    import shutil

    tmpdir = tempfile.mkdtemp(prefix="sw_asm_placement_")
    try:
        import swapi
        check("swapi 加载的是【本工作区】这份（防双副本假失败）",
              os.path.normcase(os.path.abspath(swapi.__file__)).startswith(
                  os.path.normcase(HERE)), swapi.__file__)
        check("★ 新增模块级助手：_translation_matrix_m16",
              callable(getattr(swapi, "_translation_matrix_m16", None)))
        check("★ 新增模块级助手：_position_from_transform_mm",
              callable(getattr(swapi, "_position_from_transform_mm", None)))
        check("★ 新增方法：verify_assembly_placement",
              callable(getattr(swapi.SWModel, "verify_assembly_placement", None)))
        check("★ 新增方法：detect_fake_assembly",
              callable(getattr(swapi.SWModel, "detect_fake_assembly", None)))
        check("★ 新增方法：read_component_pose",
              callable(getattr(swapi.SWModel, "read_component_pose", None)))
        check("★ 新增方法：add_components_batch",
              callable(getattr(swapi.SWModel, "add_components_batch", None)))

        test_matrix_layout(swapi)
        test_add_component_contract(swapi, tmpdir)
        test_verify_assembly_placement(swapi, tmpdir)
        test_read_component_pose(swapi)
        test_detect_fake_assembly(swapi)
        test_add_components_batch(swapi, tmpdir)
    except Exception:
        global FAIL
        FAIL += 1
        FAILURES.append("未捕获异常（见下方 traceback）")
        traceback.print_exc()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

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
