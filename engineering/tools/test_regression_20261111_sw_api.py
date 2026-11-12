# -*- coding: utf-8 -*-
"""回归测试：2026-11-11 轮 4 个已确认真机缺陷的修复。

锁定的事实（每条都在 swapi.py 里带"现场现象/根因/修复判据"注释）：
  P0-1 swEndConditions_e 枚举值订正：
        swEndCondThroughAllBoth = 9（旧值 2 = swEndCondThroughNext，
        被误当"双向贯穿"，导致双环辐板 DSH_大齿轮 孔不贯穿、体积偏差 49.95%）。
        连带订正 UpToVertex/UpToSurface/OffsetFromSurface/MidPlane/UpToBody/
        UpToSelection/UpToNext 共 7 个成员。
        证据：test/20261111_131815_580E38FA/reports/DSH_大齿轮.self_check.json
        （实测 1214042.68 mm³ vs 期望 809621.02 mm³）
  P0-1b cut 的 Sd 位：双向贯穿必须 Sd=False（双端），否则 T2 被忽略。
  P0-2 save_as 的 Options：主路径 Silent|SaveReferenced=5，回退必须是 1(Silent)，
        绝不再是 2(Copy)。返回体要带结构化 sw_errors/sw_warnings（含十六进制）。
  P1-a read_component_pose 接受组件名字符串（原来 str(cid) 进来 → 12/12 全失败）。
  P1-b AddMate5/AddMate3 统一 15/14 参 + ByRef ErrorStatus，且配合链路不重建。

⚠️ 全部用假 COM / 纯断言：不启动 SolidWorks，不碰磁盘上的 .sldasm。
运行：python test_regression_20261111_sw_api.py
退出码：0 = 全部通过，1 = 有失败
"""
import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

SWAPI_PY = os.path.join(HERE, "swapi.py")

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


def sec(title):
    print("")
    print("=" * 74)
    print(title)
    print("=" * 74)


with open(SWAPI_PY, "r", encoding="utf-8") as _f:
    SRC = _f.read()
TREE = ast.parse(SRC)


def _func(name):
    for n in ast.walk(TREE):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n
    return None


def _body(name):
    n = _func(name)
    return ast.get_source_segment(SRC, n) or "" if n is not None else ""


def _code_only(name):
    """取函数体【去掉 docstring 与整行注释】的源码 —— 注释里的历史记录不算代码。"""
    n = _func(name)
    if n is None:
        return ""
    fn = ast.FunctionDef(name=n.name, args=n.args, body=list(n.body),
                         decorator_list=[], returns=None, type_comment=None)
    fn.body = [b for b in fn.body
               if not (isinstance(b, ast.Expr)
                       and isinstance(b.value, ast.Constant)
                       and isinstance(b.value.value, str))]
    txt = ast.unparse(fn)
    return "\n".join(ln for ln in txt.splitlines()
                     if not ln.lstrip().startswith("#"))


MOD = {}
try:
    exec(compile("import swapi\n", "<probe>", "exec"), MOD)
except Exception as _e:
    print("!! 无法 import swapi: %r" % (_e,))
    sys.exit(1)
swapi = MOD["swapi"]

# ══════════════════════════════════════════════════════════════════════
sec("A. swEndConditions_e：必须等于官方值（纯断言，不需 SW）")
# ══════════════════════════════════════════════════════════════════════
# 官方原文（2018/2019/2025 三份文档成员表完全一致）：
#   https://help.solidworks.com/2025/english/api/swconst/
#     SolidWorks.Interop.swconst~SolidWorks.Interop.swconst.swEndConditions_e.html
OFFICIAL = {
    "SW_END_BLIND": 0,                # swEndCondBlind
    "SW_END_THROUGH": 1,              # swEndCondThroughAll
    "SW_END_THROUGH_NEXT": 2,         # swEndCondThroughNext
    "SW_END_UP_TO_VERTEX": 3,         # swEndCondUpToVertex
    "SW_END_UP_TO_SURFACE": 4,        # swEndCondUpToSurface
    "SW_END_OFFSET_FROM_SURFACE": 5,  # swEndCondOffsetFromSurface
    "SW_END_MID_PLANE": 6,            # swEndCondMidPlane
    "SW_END_UP_TO_BODY": 7,           # swEndCondUpToBody
    "SW_END_THROUGH_BOTH": 9,         # swEndCondThroughAllBoth  ★ 本轮核心
    "SW_END_UP_TO_SELECTION": 10,     # swEndCondUpToSelection
    "SW_END_UP_TO_NEXT": 11,          # swEndCondUpToNext
}
for _k, _v in sorted(OFFICIAL.items()):
    _got = getattr(swapi, _k, "<缺失>")
    check("%s == %d（官方值）" % (_k, _v), _got == _v, _got)

check("★ SW_END_THROUGH_BOTH 绝不是 2（2 是 swEndCondThroughNext）",
      getattr(swapi, "SW_END_THROUGH_BOTH", None) != 2,
      getattr(swapi, "SW_END_THROUGH_BOTH", None))
# 全表必须无重复值（重复 = 又有人填错）
_vals = [getattr(swapi, k) for k in OFFICIAL]
check("枚举全表 11 个值互不重复（防串值）",
      len(set(_vals)) == len(_vals), _vals)
check("★ 源码里不再有 'SW_END_THROUGH_BOTH = 2' 的旧写法",
      "SW_END_THROUGH_BOTH = 2 " not in SRC
      and "SW_END_THROUGH_BOTH = 2\t" not in SRC
      and "SW_END_THROUGH_BOTH = 2\n" not in SRC)
check("★ 枚举块注明订正日期 2026-11-11 与证据任务号",
      "2026-11-11" in SRC and "580E38FA" in SRC)
check("★ 枚举块写明 2 = swEndCondThroughNext 的误用史（防回改）",
      "swEndCondThroughNext" in SRC)

# _get_midplane_enum 必须与官方 MidPlane 一致（旧实现在老版本返回 5 = 偏移）
_mid = swapi._get_midplane_enum(None)
check("★ _get_midplane_enum(None) == 6（swEndCondMidPlane）", _mid == 6, _mid)
check("★ _get_midplane_enum 不再按版本返回 5（5 是 OffsetFromSurface）",
      "return 5" not in _body("_get_midplane_enum"),
      _body("_get_midplane_enum")[:200])

# ══════════════════════════════════════════════════════════════════════
sec("B. cut 的 Sd 位：双向贯穿必须双端（Static AST 断言）")
# ══════════════════════════════════════════════════════════════════════
_body_cut_args = _body("_cut_args")
check("★ _cut_args 有 sd 形参（Sd 不再恒 True）", "sd=True" in _body_cut_args,
      _body_cut_args[:200])
# _base 元组第 1 位必须由 sd 驱动（文本定位，避免 AST 行号偏移）
_i_base = _body_cut_args.find("_base = (")
_sd_line = ""
if _i_base >= 0:
    for _ln in _body_cut_args[_i_base:].splitlines()[1:]:
        if _ln.strip():
            _sd_line = _ln.strip()
            break
check("★ _base 第 1 位是 bool(sd)（不再写死 True）",
      _sd_line.startswith("bool(sd)"), _sd_line)
check("★ _cut_args 里不再有写死的 Sd=True 位",
      "# Sd" not in _sd_line or "bool(sd)" in _sd_line, _sd_line)
# 注意：extrude() 的 FeatureExtrusion3 第 1 位仍是 True（单向增料，语义正确），
#   故此处只对 _cut_args 断言，不做全文件断言 —— 避免误伤 extrude。

_bcut = _body("cut") if _func("cut") is not None else ""
check("★ 候选链是 6 元组（含 sd）",
      "_label, _t1, _t2, _dd1, _dd2, _sd in _cands" in SRC)
check("★ 双向贯穿候选传 sd=False（through_both → 双端）",
      "_sd_both = not bool(through_both)" in SRC)

# ══════════════════════════════════════════════════════════════════════
sec("C. save_as 的 Options 位掩码（官方 swSaveAsOptions_e）")
# ══════════════════════════════════════════════════════════════════════
# 官方原文：Silent=1 / Copy=2 / SaveReferenced=4 / AvoidRebuildOnSave=8
check("SW_SAVE_AS_SILENT == 1", getattr(swapi, "SW_SAVE_AS_SILENT", None) == 1)
check("SW_SAVE_AS_COPY == 2", getattr(swapi, "SW_SAVE_AS_COPY", None) == 2)
check("SW_SAVE_AS_SAVE_REFERENCED == 4",
      getattr(swapi, "SW_SAVE_AS_SAVE_REFERENCED", None) == 4)
check("SW_SAVE_AS_AVOID_REBUILD_ON_SAVE == 8",
      getattr(swapi, "SW_SAVE_AS_AVOID_REBUILD_ON_SAVE", None) == 8)
check("★ 主路径组合 == Silent|SaveReferenced == 5",
      getattr(swapi, "SW_SAVE_AS_OPTIONS_PRIMARY", None) == 5,
      getattr(swapi, "SW_SAVE_AS_OPTIONS_PRIMARY", None))
check("★ 回退组合 == Silent == 1（绝不再是 Copy=2）",
      getattr(swapi, "SW_SAVE_AS_OPTIONS_FALLBACK", None) == 1,
      getattr(swapi, "SW_SAVE_AS_OPTIONS_FALLBACK", None))
check("★ 回退选项不含 Copy 位",
      not (getattr(swapi, "SW_SAVE_AS_OPTIONS_FALLBACK", 0)
           & getattr(swapi, "SW_SAVE_AS_COPY", 2)))
def _code_only(name):
    """取函数体文本，剔除 docstring 与整行注释 —— 注释里的历史记录不算代码。"""
    n = _func(name)
    if n is None:
        return ""
    body = n.body
    _lines = ast.get_source_segment(SRC, n) or ""
    # 去掉函数自身的 docstring 文本（首条 Expr/Constant）
    if body and isinstance(body[0], ast.Expr) and \
            isinstance(body[0].value, ast.Constant) and \
            isinstance(body[0].value.value, str):
        _d = body[0].value.value
        if _d:
            _lines = _lines.replace(_d, "")
    return "\n".join(ln for ln in _lines.splitlines()
                     if not ln.lstrip().startswith("#"))


def _saveas3_option_values():
    """AST 精确取出所有 SaveAs3(...) 调用的第 3 个实参（Options）字面值。"""
    _vals = []
    for node in ast.walk(TREE):
        if not isinstance(node, ast.Call):
            continue
        _f = node.func
        _nm = getattr(_f, "attr", None) or getattr(_f, "id", None)
        if _nm != "SaveAs3":
            continue
        if len(node.args) >= 3:
            _vals.append(ast.get_source_segment(SRC, node.args[2]))
    return _vals


# 只断言【可执行代码】里不再有 SaveAs3(..., 2)；注释里保留历史记录是允许的
#   （注释是一等公民，故意留着"旧值是 Copy=2"的教训）。
_sa_code = _code_only("save_as")
_opt_vals = _saveas3_option_values()
check("★ 源码里所有 SaveAs3 的 Options 实参都不是字面 2（Copy）",
      all((v or "").strip() != "2" for v in _opt_vals), _opt_vals)
check("★ save_as 代码里不再出现 SaveAs3(path, 0, 2) 的 Copy 写法",
      not any(ln.strip().startswith("ret = self.model.SaveAs3(")
              and ln.strip().endswith(", 2)")
              for ln in _sa_code.splitlines()),
      [ln.strip() for ln in _sa_code.splitlines()
       if ln.strip().startswith("ret = self.model.SaveAs3(")])
check("★ save_as 回退用 SW_SAVE_AS_OPTIONS_FALLBACK 常量",
      "SW_SAVE_AS_OPTIONS_FALLBACK" in _sa_code)
check("★ 注释里保留『Copy=2 是历史误值』的教训（防回改）",
      "Options=2 是" in SRC or "swSaveAsOptions_Copy" in SRC)
check("★ 主路径使用 Extension.SaveAs（官方推荐）",
      "Extension.SaveAs(" in SRC and "SW_SAVE_AS_OPTIONS_PRIMARY" in SRC)

# 错误位掩码解码：0x10 文件锁 / 0x100 扩展名 / 0x2000 需先存引用
_dec = swapi._decode_sw_save_mask
check("0 → []（无错误位）", _dec(0) == [], _dec(0))
check("None → []（读不到不等于没错误）", _dec(None) == [], _dec(None))
_b10 = _dec(0x10)
check("0x10 解出 swFileLockError", any(b["name"] == "swFileLockError" for b in _b10),
      _b10)
check("0x100 解出 swFileSaveAsInvalidFileExtension",
      any(b["name"] == "swFileSaveAsInvalidFileExtension" for b in _dec(0x100)))
check("0x2000 解出 swFileSaveRequiresSavingReferences",
      any(b["name"] == "swFileSaveRequiresSavingReferences"
          for b in _dec(0x2000)))
_mix = _dec(0x10 | 0x100)
check("位掩码可解出多个位（0x10|0x100 → 2 条）", len(_mix) == 2, _mix)
check("每条都带十六进制 hex 字段",
      all(str(b.get("hex", "")).startswith("0x") for b in _mix), _mix)
_h = swapi._sw_save_error_hint(0x10)
check("★ error 文案点名常见码 0x10", "0x10" in _h and "锁" in _h, _h)
_h2 = swapi._sw_save_error_hint(0x100)
check("★ error 文案点名常见码 0x100", "0x100" in _h2, _h2)
_h3 = swapi._sw_save_error_hint(0x2000)
check("★ error 文案点名常见码 0x2000", "0x2000" in _h3, _h3)
check("空掩码 → 空提示（不编造错误）", swapi._sw_save_error_hint(0) == "")
check("★ 致命位集合含 0x10/0x100/0x2000",
      (swapi._SW_FILE_SAVE_FATAL_BITS & 0x10)
      and (swapi._SW_FILE_SAVE_FATAL_BITS & 0x100)
      and (swapi._SW_FILE_SAVE_FATAL_BITS & 0x2000),
      hex(swapi._SW_FILE_SAVE_FATAL_BITS))
_sa_body = _body("save_as")
check("★ save_as 有装配体前置（GetType/ActivateDoc3/EditAssembly/SaveAll）",
      all(k in _sa_body for k in ("GetType", "ActivateDoc3", "ActivateDoc2",
                                  "EditAssembly", "SaveAll")))
check("★ 磁盘指纹判定保留（before/after + updated）",
      "updated = (before is None) or (after != before)" in _sa_body)
check("★ 返回体带结构化 sw_errors / sw_warnings",
      '"sw_errors": _sw_errors' in _sa_body
      and '"sw_warnings": _sw_warnings' in _sa_body)

# ══════════════════════════════════════════════════════════════════════
sec("D. read_component_pose：字符串入参归一（假 COM）")
# ══════════════════════════════════════════════════════════════════════
def _m16(x=0.0, y=0.0, z=0.0):
    return [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0,
            x * 0.001, y * 0.001, z * 0.001, 0.0, 0.0, 0.0, 1.0]


class FakeTransform(object):
    def __init__(self, arr):
        self.ArrayData = list(arr)


class FakeComp(object):
    def __init__(self, name, pos_mm=(0.0, 0.0, 0.0)):
        self.Name2 = name
        self._pos = list(pos_mm)

    def GetTotalTransform(self, include=True):
        return FakeTransform(_m16(*self._pos))


class FakeCompMgr(object):
    """假的 GetComponentByName / GetComponents 宿主（装配体文档）。"""

    def __init__(self, comps):
        self._comps = list(comps)
        self.by_name_calls = []

    def GetComponentByName(self, name):
        self.by_name_calls.append(name)
        for c in self._comps:
            if c.Name2 == name:
                return c
        return None

    def GetComponents(self, visible_only=False):
        return tuple(self._comps)


class FakeModel(object):
    """只提供 read_component_pose 需要的成员。"""

    ASSEMBLY_POS_TOL_MM = 0.5
    ASSEMBLY_ROT_TOL = 1e-6

    def __init__(self, comps):
        self._mgr = FakeCompMgr(comps)

    def GetComponentByName(self, name):
        return self._mgr.GetComponentByName(name)

    def GetComponents(self, visible_only=False):
        return self._mgr.GetComponents(visible_only)

    def _component_display_name(self, comp):
        return getattr(comp, "Name2", None)


_m = swapi.SWModel.__new__(swapi.SWModel)
_c1 = FakeComp("DSH_底座-1", (10.0, 20.0, 30.0))
_c2 = FakeComp("DSH_立柱-1", (0.0, 0.0, 125.0))
_m.model = FakeModel([_c1, _c2])

# ① 传对象：原行为不变
_p = _m.read_component_pose(_c1)
check("传 IComponent2 对象 → ok=True 且位置正确",
      _p.get("ok") is True
      and [round(v, 3) for v in _p["position_mm"]] == [10.0, 20.0, 30.0],
      _p)

# ② 传完整组件名：必须解析成功（本轮核心修复）
_p2 = _m.read_component_pose("DSH_立柱-1")
check("★ 传组件名字符串 → ok=True（原来 12/12 全 false）",
      _p2.get("ok") is True, _p2)
check("★ 字符串入参解析到正确位置 0/0/125",
      _p2.get("position_mm") is not None
      and [round(v, 3) for v in _p2["position_mm"]] == [0.0, 0.0, 125.0],
      _p2.get("position_mm"))
check("★ 返回体标注解析途径 resolved_by",
      bool(_p2.get("resolved_by")), _p2.get("resolved_by"))

# ③ 传唯一前缀：也要能解析（总装脚本常只给基名）
_p3 = _m.read_component_pose("DSH_底座")
check("★ 唯一前缀 'DSH_底座' → 解析成功",
      _p3.get("ok") is True, _p3)

# ④ str(COMObject) 形态：必须结构化失败，不抛异常
_p4 = None
try:
    _p4 = _m.read_component_pose("<COMObject <unknown>>")
except Exception as _e:
    check("★ str(cid) 形态必须【不抛异常】", False, repr(_e))
else:
    check("★ str(cid) 形态必须【不抛异常】", True)
    check("★ str(cid) 形态 → ok=False（结构化失败）",
          _p4.get("ok") is False, _p4)
    check("★ str(cid) 形态 → 有 error 且提示改传 Name2",
          bool(_p4.get("error")) and "Name2" in str(_p4.get("hint", "")),
          (_p4.get("error"), _p4.get("hint")))

# ⑤ 不存在的名字：结构化失败 + 提示 list_components
_p5 = _m.read_component_pose("DSH_不存在的件-9")
check("★ 找不到的名字 → ok=False 不抛异常", _p5.get("ok") is False, _p5)
check("★ 找不到时 hints 提供 list_components",
      "list_components" in str(_p5.get("hint", "")), _p5.get("hint"))

# ⑥ 字典形态 {"component": name}
_p6 = _m.read_component_pose({"component": "DSH_底座-1"})
check("★ dict {'component': name} → 解析成功", _p6.get("ok") is True, _p6)

# ⑦ 空字典：结构化失败
_p7 = _m.read_component_pose({"foo": 1})
check("★ dict 缺字段 → ok=False", _p7.get("ok") is False, _p7)

# ⑧ 矩阵索引 9/10/11 不得被改动
_pos_body = _body("_position_from_transform_mm")
check("★ _position_from_transform_mm 仍用索引 9/10/11",
      all(k in _pos_body for k in ("[9]", "[10]", "[11]")), _pos_body[:300])

# ══════════════════════════════════════════════════════════════════════
sec("E. AddMate5/AddMate3：15/14 参 + ByRef ErrorStatus；配合链路不重建")
# ══════════════════════════════════════════════════════════════════════
# ⚠️ 【BUG-9 更新】本轮把 ByRef 封送收敛成模块级 _byref_i4()（= 同一个
#    VARIANT(VT_BYREF|VT_I4)，但只在【晚期绑定】路径使用）；早期绑定必须
#    发 14 参（ByRef 自动解包成第二个返回值，再塞 VARIANT 会参数个数错）。
#    成功判据也从 "_esv in (0,1)" 收紧成 mate_ok_by_error_status()
#    —— 官方 swAddMateError_e 的 NoError 是 1，0 是 ErrorUnknown，
#    "0 也算成功"是错的。因此本节断言同步到新实现（判据更严，不是放宽）。
_ame = _body("_add_mate_early")
check("★ 不再有 14 参的 AddMate5 尝试（'14' 形式已删）",
      '"14"' not in _ame, _ame[:200])
check("★ 晚期 AddMate5 发 15 参（_am5 + [ErrorStatus]）",
      "_fn(*(_am5 + [_es]))" in _ame)
check("★ 早期 AddMate5 发 14 参（不塞 VARIANT，ByRef 自动解包）",
      "_fn(*_am5)" in _ame)
check("★ AddMate5 的 ErrorStatus 由 _byref_i4 构造（VT_BYREF|VT_I4）",
      "_byref_i4(0)" in _ame
      and "VT_BYREF | pythoncom.VT_I4" in _body("_byref_i4"),
      _body("_byref_i4")[:200])
check("★ 成功判据是 mate_ok_by_error_status（ErrorStatus 必须 == NoError(1)）",
      "mate_ok_by_error_status" in _ame)
check("★ 不再把 ErrorStatus 0(ErrorUnknown) 当成功",
      "_esv in (0, 1)" not in _ame,
      [ln.strip() for ln in _ame.splitlines() if "_esv in (" in ln])
check("★ 晚期 AddMate3 发 14 参 + ErrorStatus（原来只发 12 参）",
      "_a3 + [_es3]" in _ame and "_byref_i4(0)" in _ame,
      [ln.strip() for ln in _ame.splitlines() if "_a3" in ln])
check("★ 失败时把 ErrorStatus 写进返回体字段",
      "_last_mate_error_status" in _ame and "mate_error_status" in _body("create_mate"))
check("★ 失败时把 ErrorStatus 写进返回体字段",
      "_last_mate_error_status" in _ame and "mate_error_status" in _body("create_mate"))
_cm = _body("create_mate")
# 只检查【代码】：注释里写"原实现无条件 self.rebuild()"是允许的（留证据）。
_cm_code = _code_only("create_mate")
check("★ create_mate 成功后【不再】调 rebuild()",
      "self.rebuild()" not in _cm_code,
      [ln.strip() for ln in _cm_code.splitlines() if "rebuild" in ln])
check("★ create_mate 回退链发 15 参 AddMate5（不再是 8 参）",
      "_fn5(_mt, 0, False, _d2, _du2, _dl2, _gn2, _gd2," in _cm)
check("★ add_mate 源码里没有 8 参 AddMate5 残留",
      "(_mt, 0, False, False, False, _d2, _a2" not in SRC)
_pcm = _body("place_component_by_mate")
_pcm_code = _code_only("place_component_by_mate")
check("★ place_component_by_mate 也不再 rebuild()",
      "self.rebuild()" not in _pcm_code,
      [ln.strip() for ln in _pcm_code.splitlines() if "rebuild" in ln])

# ══════════════════════════════════════════════════════════════════════
print("")
print("=" * 74)
print("汇总：PASS=%d  FAIL=%d" % (PASS, FAIL))
if FAILURES:
    print("失败项：")
    for x in FAILURES:
        print("  - " + x)
print("")
print("仍需真机验证（本测试不启动 SW）：")
print("  · 双向贯穿孔是否真贯通（双环辐板 DSH_大齿轮，体积应回到 809621 mm³ 量级）")
print("  · 装配体 SaveAs 是否落盘（v2/v3 应出现真实文件）")
print("  · 配合是否不再卡死（600s 级超时应消失）")
sys.exit(0 if FAIL == 0 else 1)
