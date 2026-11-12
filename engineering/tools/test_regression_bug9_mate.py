# -*- coding: utf-8 -*-
"""【BUG-9 回归测试】装配配合门禁 --force-mate（SW2025 晚期绑定 CreateMate 不可用）

★ 锁定的现场事实（原文摘录）：
  「SW2025 晚期绑定下 `AddMate5/CreateMate` 不可用（SelectionManager 计数异常 0
    + AddMate5 参数类型不匹配），装配改用 `add_component` 摆位 + 位姿断言 +
    SW 原生干涉验证，配合门禁 `--force-mate` 留痕。」

★ 本测试钉死的契约（全部用假 COM，不启动 SolidWorks）：
  1. **ByRef 封送**：AddMate5 第 15 参 ErrorStatus 必须是
     `VARIANT(VT_BYREF|VT_I4)`；传裸 0 会被打包成值传递 →
     DISP_E_TYPEMISMATCH(-2147352571)，也就是历史日志里的"类型不匹配"。
  2. **选择计数**：`GetSelectedObjectCount2` 一律传 **-1**。
     官方语义 `0 = 只数没有 mark 的选择`，而配合实体 mark 都是 1 →
     用 0 必然读回 0，被误判成"没选中"（"计数异常 0"的真因）。
  3. **Callout 封送**：SelectByID2 第 8 参必须是 `VARIANT(VT_DISPATCH, None)`，
     传裸 None 会变成 VT_NULL 被拒。且两个实体 Mark **都是 1**。
  4. **分层策略**：早期绑定可用 → CreateMate 主路径（CreateMateData → 设属性 →
     CreateMate）；不可用 → 晚期 AddMate5(15 参 + ByRef)；早期绑定的 AddMate5
     是 14 参（ByRef 自动解包成第二个返回值，**不能**再塞 VARIANT）。
  5. **成功判据**：`swAddMateError_e` 的 NoError 是 **1**（0 是 ErrorUnknown）。
     "拿到 Mate2 就算成功"是静默失败源。
  6. **干涉检查**：必须走 `InterferenceDetectionManager`
     （`GetInterferenceCount` / `GetInterferences` / `Done`），
     **不得**用 `ToolsCheckInterference2` —— 它的两个 ByRef 出参在晚期绑定下
     永远读回 None，会把"有干涉"误报成"无干涉"。
  7. **门禁三态**：通过 / 违规拦截 / 未校验 必须可区分，未校验不得显示成通过。
  8. **--force-mate 留痕**：绕行必须留痕，且【明确说明"配合未真正建立，
     仅摆位 + 位姿断言 + 干涉验证"】—— 不许把降级渲染成合格。
  9. **宏兜底的安全闸**：非二进制 OLE 复合文档的 .swp 必须被拒绝执行
     （否则会弹 "Cannot open" 模态框并无限期挂死 COM 线程）；
     `RunMacro2` 的 Error 必须 `VARIANT(VT_BYREF|VT_I4)`，ModuleName 是 `<文件名>1`。

依据：docs/SW2025_AddMate_win32com_技术结论.md（报告 一-1/一-3/一-3b/一-5/
一-6/二-2b/二-2c/二-2d/三-7）。

⚠️ 全部用假 COM 对象：不启动 SolidWorks，不碰磁盘上的 .sldasm。
运行：python test_regression_bug9_mate.py
退出码：0 = 全部通过；1 = 有失败
"""
import ast
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import pythoncom                      # noqa: E402
import win32com.client as win32        # noqa: E402

SWAPI_PY = os.path.join(HERE, "swapi.py")
SW_BRIDGE_PY = os.path.join(HERE, "sw_bridge.py")

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
        FAILURES.append(label + ("  <" + str(detail)[:400] + ">"
                                 if detail else ""))
        print("  [FAIL] %s  %s" % (label, detail))


def sec(title):
    print("")
    print("=" * 74)
    print(title)
    print("=" * 74)


with io.open(SWAPI_PY, "r", encoding="utf-8") as _f:
    SRC = _f.read()
TREE = ast.parse(SRC)


def _func(name, tree=None):
    for n in ast.walk(tree if tree is not None else TREE):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n
    return None


def _body(name):
    n = _func(name)
    if n is None:
        return ""
    return ast.get_source_segment(SRC, n) or ""


def _bbody(tree, src, name):
    """按指定源码树取函数体（sw_bridge.py 用）。"""
    n = _func(name, tree)
    if n is None:
        return ""
    return ast.get_source_segment(src, n) or ""


def _bcode_only(tree, name):
    """按指定源码树取"去掉注释/docstring"的代码（sw_bridge.py 用）。

    为什么必须区分：门禁里的**注释**会解释"旧判据 errStatus != 0 是错的"，
    那是留证据，不是代码缺陷；用注释判失败会把正确修复判成 bug。
    """
    n = _func(name, tree)
    if n is None:
        return ""
    fn = ast.FunctionDef(name=n.name, args=n.args, body=list(n.body),
                         decorator_list=[], returns=None, type_comment=None)
    for _attr in ("lineno", "col_offset", "end_lineno", "end_col_offset"):
        try:
            setattr(fn, _attr, getattr(n, _attr))
        except Exception:
            pass
    fn.body = [b for b in fn.body
               if not (isinstance(b, ast.Expr)
                       and isinstance(b.value, ast.Constant)
                       and isinstance(b.value.value, str))]
    txt = ast.unparse(fn)
    return "\n".join(ln for ln in txt.splitlines()
                     if not ln.lstrip().startswith("#"))


def _code_only(name):
    """取函数体【去掉 docstring 与整行注释】的源码 —— 注释里的历史记录不算代码。"""
    n = _func(name)
    if n is None:
        return ""
    fn = ast.FunctionDef(name=n.name, args=n.args, body=list(n.body),
                         decorator_list=[], returns=None, type_comment=None)
    # ast.unparse 需要位置属性（3.13 的 get_type_comment 会读 node.lineno）
    for _attr in ("lineno", "col_offset", "end_lineno", "end_col_offset"):
        try:
            setattr(fn, _attr, getattr(n, _attr))
        except Exception:
            pass
    fn.body = [b for b in fn.body
               if not (isinstance(b, ast.Expr)
                       and isinstance(b.value, ast.Constant)
                       and isinstance(b.value.value, str))]
    txt = ast.unparse(fn)
    return "\n".join(ln for ln in txt.splitlines()
                     if not ln.lstrip().startswith("#"))


def _calls_get_selected_object_count(tree, code_only=True):
    """找出所有 GetSelectedObjectCount2(...) 调用及其第一个实参的源码形态。

    用 AST 而不是 grep —— 注释里写"官方语义 0 = ..."是【说明】，不是代码，
    不能算残留缺陷（否则测试会把正确的注释判成 bug）。
    Returns: [(arg_src, lineno), ...]
    """
    out = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        _f = n.func
        _is = (isinstance(_f, ast.Attribute)
               and _f.attr == "GetSelectedObjectCount2")
        if not _is:
            continue
        if n.args:
            out.append((ast.unparse(n.args[0]),
                        getattr(n, "lineno", "?")))
        else:
            out.append(("<无实参>", getattr(n, "lineno", "?")))
    return out


MOD = {}
try:
    exec(compile("import swapi\n", "<probe>", "exec"), MOD)
except Exception as _e:
    print("!! 无法 import swapi: %r" % (_e,))
    sys.exit(1)
swapi = MOD["swapi"]

MOD2 = {}
try:
    exec(compile("import sw_bridge\n", "<probe>", "exec"), MOD2)
except Exception as _e:
    print("!! 无法 import sw_bridge: %r" % (_e,))
    sys.exit(1)
sw_bridge = MOD2["sw_bridge"]


# ══════════════════════════════════════════════════════════════════════
sec("A. COM 参数封送：ByRef / Callout / 变体数组（纯函数，无需 SW）")
# ══════════════════════════════════════════════════════════════════════
_v = swapi._byref_i4(0)
check("★ _byref_i4 产生 VT_BYREF|VT_I4（0x4003=16387），不是裸 int",
      getattr(_v, "varianttype", None) == (pythoncom.VT_BYREF | pythoncom.VT_I4),
      getattr(_v, "varianttype", None))
check("★ _byref_i4 初值可读回 0", _v.value == 0, _v.value)
_v2 = swapi._byref_i4(7)
check("★ _byref_i4 可带初值", _v2.value == 7, _v2.value)

_n = swapi._null_dispatch()
check("★ _null_dispatch 是 VT_DISPATCH(9) 空指针，不是 VT_NULL",
      getattr(_n, "varianttype", None) == pythoncom.VT_DISPATCH,
      getattr(_n, "varianttype", None))
check("★ _null_dispatch 的值是 None", _n.value is None, _n.value)

_va = swapi._dispatch_array((1, 2))
check("★ _dispatch_array 是 VT_ARRAY|VT_DISPATCH",
      getattr(_va, "varianttype", None)
      == (pythoncom.VT_ARRAY | pythoncom.VT_DISPATCH),
      getattr(_va, "varianttype", None))
_va0 = swapi._dispatch_array([])
check("★ _dispatch_array([]) 仍返回有类型的数组，不是 None",
      _va0 is not None
      and getattr(_va0, "varianttype", None)
      == (pythoncom.VT_ARRAY | pythoncom.VT_DISPATCH),
      _va0)
check("★ _dispatch_array(None) 不抛异常", swapi._dispatch_array(None) is not None)

# ByRef 读回容错
check("★ _byref_value 读回正常值", swapi._byref_value(_v2) == 7,
      swapi._byref_value(_v2))
check("★ _byref_value(坏对象) 返回 default 而不抛",
      swapi._byref_value(object(), "dflt") == "dflt")


# ══════════════════════════════════════════════════════════════════════
sec("B. swAddMateError_e：NoError=1（0 是 ErrorUnknown，不是成功）")
# ══════════════════════════════════════════════════════════════════════
check("★ SW_ADD_MATE_ERROR_NO_ERROR == 1（官方值）",
      swapi.SW_ADD_MATE_ERROR_NO_ERROR == 1,
      swapi.SW_ADD_MATE_ERROR_NO_ERROR)
check("★ SW_ADD_MATE_ERROR_UNKNOWN == 0", swapi.SW_ADD_MATE_ERROR_UNKNOWN == 0)
check("★ mate_error_status_text(1) 含 NoError",
      "NoError" in swapi.mate_error_status_text(1),
      swapi.mate_error_status_text(1))
check("★ mate_error_status_text(0) 含 ErrorUnknown",
      "ErrorUnknown" in swapi.mate_error_status_text(0),
      swapi.mate_error_status_text(0))
check("★ mate_error_status_text(4) 含 IncorrectSelections",
      "IncorrectSelections" in swapi.mate_error_status_text(4),
      swapi.mate_error_status_text(4))
check("★ 未知错误码不抛异常",
      isinstance(swapi.mate_error_status_text(999), str))

# 成功判据：Mate2 非 None + ErrorStatus == 1 才算成功
_ok, _known = swapi.mate_ok_by_error_status("MATE", 1)
check("★ Mate2 非 None + ErrorStatus=1 → 成功", _ok is True and _known is True,
      (_ok, _known))
_ok2, _known2 = swapi.mate_ok_by_error_status("MATE", 0)
check("★ ★ Mate2 非 None 但 ErrorStatus=0(ErrorUnknown) → 【判失败】（不是成功）",
      _ok2 is False, (_ok2, _known2))
_ok3, _ = swapi.mate_ok_by_error_status("MATE", 4)
check("★ ErrorStatus=4(IncorrectSelections) → 判失败", _ok3 is False)
_ok4, _k4 = swapi.mate_ok_by_error_status(None, 1)
check("★ Mate2 为 None → 判失败（即使错误码是 1）", _ok4 is False)
_ok5, _k5 = swapi.mate_ok_by_error_status("MATE", None)
check("★ ErrorStatus 读不到(None) → 降级为看 Mate2，且标记 known=False",
      _ok5 is True and _k5 is False, (_ok5, _k5))


# ══════════════════════════════════════════════════════════════════════
sec("C. 配合参考名：<实体名>@<零部件实例名>@<装配体名>")
# ══════════════════════════════════════════════════════════════════════
check("★ 三段式全给 → 'Face2@DSH_底座-1@Assem1'",
      swapi.mate_entity_name("Face2", "DSH_底座-1", "Assem1")
      == "Face2@DSH_底座-1@Assem1",
      swapi.mate_entity_name("Face2", "DSH_底座-1", "Assem1"))
check("★ 省略装配体名 → 两段式（SW 亦接受）",
      swapi.mate_entity_name("Face2", "DSH_底座-1") == "Face2@DSH_底座-1")
check("★ 只有实体名 → 单段", swapi.mate_entity_name("Face2") == "Face2")
check("★ entity 为空 → 返回空串（不许传 'None@...'）",
      swapi.mate_entity_name("", "X") == "",
      repr(swapi.mate_entity_name("", "X")))
check("★ 各段去空白",
      swapi.mate_entity_name(" Face2 ", " C-1 ", " A ") == "Face2@C-1@A")

check("★ mate_name_segments 拆三段",
      swapi.mate_name_segments("Face2@C-1@A") == ["Face2", "C-1", "A"],
      swapi.mate_name_segments("Face2@C-1@A"))
check("★ mate_name_segments('') == []", swapi.mate_name_segments("") == [])

check("★ 三段名 → qualified=True",
      swapi.mate_name_is_qualified("Face2@C-1@A") is True)
check("★ 两段名 → qualified=True",
      swapi.mate_name_is_qualified("Face2@C-1") is True)
check("★ ★ 单段名（'Face2'）→ qualified=False（装配体里不保证选得中）",
      swapi.mate_name_is_qualified("Face2") is False)
check("★ 空段名（'Face2@@A'）→ qualified=False",
      swapi.mate_name_is_qualified("Face2@@A") is False)
check("★ 空串 → qualified=False", swapi.mate_name_is_qualified("") is False)


# ══════════════════════════════════════════════════════════════════════
sec("D. 源码级契约：计数必须 -1、Callout 必须 VARIANT、无裸 0")
# ══════════════════════════════════════════════════════════════════════
# ① 全库不得再用 GetSelectedObjectCount2(0)（"计数异常 0"的历史真因）
#    用 AST 判，不 grep：注释里解释"官方语义 0 = ..."是【说明】不是代码缺陷。
_count_calls = _calls_get_selected_object_count(TREE)
_bad_count = [x for x in _count_calls if x[0].strip() == "0"]
check("★ ★ swapi.py 里没有 GetSelectedObjectCount2(0) 的实参残留",
      not _bad_count, _bad_count[:3])
check("★ 所有 GetSelectedObjectCount2 调用都用 -1（或变量传 -1）",
      all(x[0].strip() in ("-1", "mark", "int(mark)") for x in _count_calls),
      _count_calls[:6])
check("★ 至少存在真实的 -1 计数调用（不是空断言）",
      any(x[0].strip() == "-1" for x in _count_calls),
      _count_calls[:6])

# ② _select_by_id_asm 的晚期路径必须用 VARIANT(VT_DISPATCH, None) 作 Callout
_sb = _body("_select_by_id_asm")
check("★ _select_by_id_asm 晚期路径 Callout 走 self._empty（VARIANT）",
      "self._empty" in _sb, _sb[:400])
_ctor = _body("__init__")
check("★ SWModel.__init__ 的 self._empty = VARIANT(VT_DISPATCH, None)",
      "pythoncom.VT_DISPATCH" in _ctor and "_empty" in _ctor)
check("★ 模块级 _null_dispatch 提供同一封送",
      "def _null_dispatch" in SRC and "VT_DISPATCH" in _body("_null_dispatch"))

# ③ 计数辅助函数必须恒传 -1
_sa = _body("_selected_count_all")
check("★ _selected_count_all 内部恒传 -1",
      "GetSelectedObjectCount2(-1)" in _sa, _sa[:400])
check("★ _selected_count_all 读不到返回 -1（不是 0）", "-1" in _sa)

# ④ create_mate 不再直接写死 GetSelectedObjectCount2(...)
_cm_code = _code_only("create_mate")
check("★ create_mate 的选择计数改走 _selected_count_all()",
      "_selected_count_all()" in _cm_code,
      [ln.strip() for ln in _cm_code.splitlines() if "SelectedObjectCount" in ln])
check("★ create_mate 建配合前清选择集（_ensure_clear_selection）",
      "_ensure_clear_selection()" in _cm_code)


# ══════════════════════════════════════════════════════════════════════
sec("E. AddMate5 分层：早期 14 参 / 晚期 15 参 + VARIANT ByRef")
# ══════════════════════════════════════════════════════════════════════
_ame = _body("_add_mate_early")
check("★ AddMate5 晚期路径用 _byref_i4(0)（= VARIANT(VT_BYREF|VT_I4)）",
      "_byref_i4(0)" in _ame, _ame[:200])
check("★ AddMate5 晚期路径发 15 参（_am5 + [_es]）",
      "_am5 + [_es]" in _ame)
check("★ AddMate5 早期路径发 14 参（_fn(*_am5)，不塞 VARIANT）",
      "_fn(*_am5)" in _ame,
      [ln.strip() for ln in _ame.splitlines() if "_fn(*" in ln])
check("★ 早期/晚期按 holder 名区分（startswith('early')）",
      'startswith("early")' in _ame)
check("★ 早期路径解包 tuple 的第二个返回值作为 ErrorStatus",
      "isinstance(_res, (tuple, list))" in _ame)
check("★ 成功判据走 mate_ok_by_error_status（不是 _esv in (0,1)）",
      "mate_ok_by_error_status" in _ame and "_esv in (0, 1)" not in _ame)
check("★ 早期探测在分层之前（_asm_early_probe）",
      "_asm_early_probe()" in _ame)
_ame_head = _ame.split("_asm_early_probe")[0]
check("★ 探测结果决定 holder 列表（只把真早期对象加进去）",
      "_probe.get(\"mode\") == \"early\"" in _ame, _ame[:600])

# AddMate3 也同步区分（早期 13 参 / 晚期 14 参）
check("★ AddMate3 区分早期 13 参 / 晚期 14 参",
      "_a3 + [_es3]" in _ame and "_fn(*_a3)" in _ame)

# 早期探测函数本身
_probe_body = _body("_asm_early_probe")
check("★ _asm_early_probe 检查 is_early_available()",
      "is_early_available()" in _probe_body)
check("★ _asm_early_probe 判「包装未生效（返回原对象）」",
      "_wrapped is _m" in _probe_body or "_wrapped is not _m" in _probe_body)
check("★ _asm_early_probe 从不抛异常（全 try/except）",
      "except Exception" in _probe_body)
check("★ 探测返回 mode ∈ {'early','late'}",
      '"early"' in _probe_body and '"late"' in _probe_body)


# ══════════════════════════════════════════════════════════════════════
sec("F. CreateMate 主路径：CreateMateData → 设属性 → CreateMate")
# ══════════════════════════════════════════════════════════════════════
_cm_body = _body("_mate_by_createmate")
check("★ ★ 走 CreateMateData( 而不是不存在的 CreateMateData2（报告五-2）",
      "CreateMateData(" in _cm_body or "_fn_data(" in _cm_body,
      _cm_body[:400])
def _attr_names_used(tree, func_name):
    """取某函数里【实际调用/访问】的属性名集合（不看注释与字符串）。"""
    n = _func(func_name, tree)
    if n is None:
        return set()
    out = set()
    for x in ast.walk(n):
        if isinstance(x, ast.Attribute):
            out.add(x.attr)
        elif isinstance(x, ast.Name):
            out.add(x.id)
    return out


check("★ ★ 没有实际调用 CreateMateData2（该 API 不存在，报告五-2）",
      "CreateMateData2" not in _attr_names_used(TREE, "_mate_by_createmate"),
      _attr_names_used(TREE, "_mate_by_createmate"))
check("★ 设置 MateAlignment（官方三步之一）",
      "MateAlignment" in _cm_body)
check("★ 优先用预选（不设 EntitiesToMate），预选不足才显式塞数组",
      "EntitiesToMate" in _cm_body and "_dispatch_array" in _cm_body,
      _cm_body[:600])
check("★ 每个属性赋值单独 try（晚期绑定属性可能不可见，报告五-5）",
      _cm_body.count("except Exception") >= 4)
check("★ CreateMate 返回 None 时判失败并给出原因",
      "返回 None" in _cm_body)
_ame_code = _code_only("_add_mate_early")
check("★ ★ 主路径 CreateMate 在代码里排在 AddMate5 之前（不是靠文档顺序）",
      _ame_code.index("_mate_by_createmate(")
      < _ame_code.index("AddMate5"),
      (_ame_code.index("_mate_by_createmate("),
       _ame_code.index("AddMate5")))


# ══════════════════════════════════════════════════════════════════════
sec("G. 干涉检查：InterferenceDetectionManager（不再是 ToolsCheckInterference2）")
# ══════════════════════════════════════════════════════════════════════
_ci = _body("check_interference")
_ci_code = _code_only("check_interference")
check("★ ★ check_interference 代码里【没有】ToolsCheckInterference2 调用",
      "ToolsCheckInterference2" not in _ci_code,
      [ln.strip() for ln in _ci_code.splitlines()
       if "ToolsCheckInterference2" in ln])
check("★ 主路径走 _interference_manager（InterferenceDetectionManager）",
      "_interference_manager()" in _ci_code)
check("★ 明确区分三态：available / trustworthy / approx",
      all(k in _ci_code for k in ("available", "trustworthy", "approx")),
      [k for k in ("available", "trustworthy", "approx") if k not in _ci_code])
check("★ 计数读不到时【不】当 0（返回 ok=False + 不可采信）",
      "结果不可采信" in _ci or "不可采信" in _ci)
check("★ ★ 旧实现 `int(n or 0)` 的假通过写法已消失",
      'int(n or 0)' not in _ci_code,
      [ln.strip() for ln in _ci_code.splitlines() if "n or 0" in ln])
check("★ 明确说明「读回 None 不得当 0」的理由（防回归）",
      "误报" in _ci and "None" in _ci)

_mgr = _body("_interference_manager")
check("★ _interference_manager 取 InterferenceDetectionManager 属性",
      "InterferenceDetectionManager" in _mgr)
check("★ 取 manager 时同时试属性式与方法式",
      '"attr"' in _mgr and '"call"' in _mgr)
check("★ 取不到时返回 error 而不是 None/0", '"error"' in _mgr)

_done = _body("_interference_done")
check("★ _interference_done 调用 mgr.Done()（不调会卡在干涉显示模式）",
      "Done" in _done)
check("★ Done 失败只记录不抛", "except Exception" in _done)

_det = _body("_interference_details")
check("★ Volume 按【属性】读（不带括号）",
      ".Volume" in _det and "Volume()" not in _det)
check("★ GetComponentCount 按【方法】读（带括号）",
      "GetComponentCount" in _det and "callable" in _det)
check("★ 组件列表读 .Components 【属性】（不是 GetComponents() 方法）",
      'getattr(_it, "Components", None)' in _det
      and "GetComponents" not in _attr_names_used(TREE, "_interference_details"),
      sorted(_attr_names_used(TREE, "_interference_details")))
check("★ 明细有上限（防 100+ 干涉把返回体炸掉）",
      "limit" in _det)


# ══════════════════════════════════════════════════════════════════════
sec("H. 宏兜底 RunMacro2：ByRef / ModuleName / 二进制 .swp 安全闸")
# ══════════════════════════════════════════════════════════════════════
_mm = _body("_run_mate_macro")
check("★ 宏路径只认环境变量 DSH_MATE_MACRO（绝不臆造路径）",
      "DSH_MATE_MACRO" in _mm)
check("★ RunMacro2 的 Error 用 _byref_i4（VT_BYREF|VT_I4）",
      "_byref_i4(0)" in _mm)
check("★ ★ 先校验 OLE 复合文档签名（防手写文本 .swp 弹模态框挂死）",
      "OLE_MAGIC" in _mm or "d0\\\\xcf" in _mm.lower()
      or "\\xd0\\xcf\\x11\\xe0" in _mm,
      _mm[:800])
check("★ 非 OLE 文件 → 拒绝执行（返回 ok=False）",
      "拒绝执行" in _mm)
check("★ ModuleName 优先用 GetMacroMethods 拿真名",
      "GetMacroMethods" in _mm)
check("★ 拿不到时退回 <文件名>1（报告坑 2）",
      '_stem + "1"' in _mm or '+ "1"' in _mm)
check("★ 错误码 22(InvalidIndex) 有提示",
      "22" in _mm and "InvalidIndex" in _mm)


# ══════════════════════════════════════════════════════════════════════
sec("I. 门禁三态判据（sw_bridge._mate_gate_verdict，纯函数）")
# ══════════════════════════════════════════════════════════════════════
_MGV = sw_bridge._mate_gate_verdict
check("★ 有配合名 → pass，不拦截",
      _MGV(["重合1", "同心2"])["state"] == "pass"
      and _MGV(["重合1"])["blocked"] is False,
      _MGV(["重合1"]))
check("★ 无配合名 + 未绕行 → violation，blocked=True",
      _MGV([])["state"] == "violation" and _MGV([])["blocked"] is True,
      _MGV([]))
check("★ ★ 未校验(verified=False) → unverified，【不拦截但也不显示通过】",
      _MGV([], verified=False)["state"] == "unverified"
      and _MGV([], verified=False)["blocked"] is False,
      _MGV([], verified=False))
check("★ 未校验的 note 明确写「不能视为已通过配合门禁」",
      "不能视为已通过" in str(_MGV([], verified=False)["note"]),
      _MGV([], verified=False)["note"])
check("★ 无配合 + 已绕行 → violation_bypassed，degraded=True",
      _MGV([], bypassed=True)["state"] == "violation_bypassed"
      and _MGV([], bypassed=True)["degraded"] is True,
      _MGV([], bypassed=True))
check("★ ★ 绕行 note 必须写明「配合并未真正建立」",
      "配合并未真正建立" in str(_MGV([], bypassed=True)["note"]),
      _MGV([], bypassed=True)["note"])
check("★ 绕行 note 提到「位姿断言」与「干涉」（降级交付的两条支柱）",
      "位姿" in str(_MGV([], bypassed=True)["note"])
      and "干涉" in str(_MGV([], bypassed=True)["note"]))
check("★ 有配合时 degraded=False（不是降级）",
      _MGV(["重合1"])["degraded"] is False)
check("★ 空名过滤（[''] 视为没有配合）",
      _MGV(["", None])["state"] == "violation",
      _MGV(["", None]))


# ══════════════════════════════════════════════════════════════════════
sec("J. 门禁源码契约：--force-mate 留痕 + 降级说明 + 提示的 API 事实")
# ══════════════════════════════════════════════════════════════════════
with io.open(SW_BRIDGE_PY, "r", encoding="utf-8") as _f:
    BSRC = _f.read()
BTREE = ast.parse(BSRC)

_gate = _bbody(BTREE, BSRC, "_assembly_mate_gate")
check("★ 门禁仍识别 --force-mate", "--force-mate" in _gate)
check("★ 门禁仍识别 DSH_ASSEMBLY_MATE_BYPASS", "DSH_ASSEMBLY_MATE_BYPASS" in _gate)
check("★ ★ 绕行必留痕（defense_gate.log_bypass）",
      "log_bypass" in _gate)
check("★ 留痕写入 mate 作用域（scope='mate'，报告侧可单独统计）",
      'scope="mate"' in _gate)
check("★ ★ 绕行 note 明确写「配合未真正建立」「仅靠摆位+干涉验证」",
      "配合未真正建立" in _gate and "摆位" in _gate)
check("★ 绕行时仍先查证配合（不因绕行就不查）",
      "FeatureByName" in _gate)
check("★ 门禁提示里的计数写法是 GetSelectedObjectCount2(-1)",
      "GetSelectedObjectCount2(-1)" in _gate)
check("★ ★ 门禁【代码】里不再用 errStatus != 0 作成功判据（注释里的历史记录不算）",
      "errStatus != 0" not in _bcode_only(BTREE, "_assembly_mate_gate"),
      [ln.strip() for ln in _bcode_only(BTREE, "_assembly_mate_gate").splitlines()
       if "errStatus" in ln])
check("★ 门禁提示写明 ErrorStatus == 1 才是 NoError",
      "ErrorStatus == 1" in _gate and "swAddMateError_NoError" in _gate)
check("★ 门禁提示写明 VARIANT(VT_BYREF|VT_I4)",
      "VT_BYREF|VT_I4" in _gate)
check("★ 门禁提示写明 15 参", "15 参" in _gate)
check("★ 门禁返回体带 state（三态可区分）", 'out["state"]' in _gate)
check("★ 门禁异常时 state=unverified（不假装通过）",
      '"unverified"' in _gate)

# 关键常量共用
check("★ MATE_NAME_BASES 模块级常量存在（门禁与验收共用口径）",
      isinstance(getattr(sw_bridge, "MATE_NAME_BASES", None), tuple)
      and len(sw_bridge.MATE_NAME_BASES) >= 10,
      getattr(sw_bridge, "MATE_NAME_BASES", None))
check("★ MATE_NAME_BASES 含 重合/同心（箱盖-底座、轴-轴承孔都是这两类）",
      "重合" in sw_bridge.MATE_NAME_BASES
      and "同心" in sw_bridge.MATE_NAME_BASES)

# 降级证据函数
_dev = _bbody(BTREE, BSRC, "_degraded_mate_evidence")
check("★ _degraded_mate_evidence 采集位姿（read_component_pose）",
      "read_component_pose" in _dev)
check("★ _degraded_mate_evidence 采集原生干涉（check_interference）",
      "check_interference" in _dev)
check("★ 降级证据如实标注 strength=weak",
      'strength' in _dev and 'weak' in _dev)
check("★ 降级证据透传 trustworthy / approx（近似值不冒充原生结论）",
      "trustworthy" in _dev and "approx" in _dev)
check("★ 降级证据异常不抛（门禁不能自己崩）",
      "except Exception" in _dev)

# asm-verify 也补上干涉与降级标注
_av = _bbody(BTREE, BSRC, "cmd_asm_verify")
check("★ asm-verify 现在也做原生干涉验证",
      "check_interference" in _av)
check("★ asm-verify 透传 trustworthy/approx",
      "trustworthy" in _av and "approx" in _av)
check("★ ★ asm-verify 无配合时明确写「配合未真正建立」",
      "配合未真正建立" in _av)
check("★ asm-verify 与门禁共用 MATE_NAME_BASES",
      "MATE_NAME_BASES" in _av)


# ══════════════════════════════════════════════════════════════════════
sec("K. 假 COM 端到端：晚期绑定 AddMate5 必须收到 VARIANT ByRef")
# ══════════════════════════════════════════════════════════════════════
class _FakeMate(object):
    Name = "重合1"


class _FakeSelMgr(object):
    """记录每次 GetSelectedObjectCount2 的 Mark，验证一定传 -1。"""

    def __init__(self, n=2):
        self._n = n
        self.marks = []

    def GetSelectedObjectCount2(self, mark):
        self.marks.append(mark)
        return self._n if mark == -1 else 0     # 0 → 只数无 mark 的选择 = 0

    def GetSelectedObject6(self, i, mark):
        return None


class _FakeModelLate(object):
    """只提供晚期绑定形态的假 IAssemblyDoc（无 CreateMateData）。"""

    def __init__(self, err_status=1, mate=None):
        self.SelectionManager = _FakeSelMgr()
        self.calls = []
        self._err = err_status
        self._mate = mate if mate is not None else _FakeMate()

    def ClearSelection2(self, flag):
        return True

    def AddMate5(self, *args):
        self.calls.append(args)
        # ★ 核对：第 15 参必须是 VARIANT(VT_BYREF|VT_I4)，不能是裸 int
        _es = args[14] if len(args) >= 15 else None
        _vt = getattr(_es, "varianttype", None)
        if _vt != (pythoncom.VT_BYREF | pythoncom.VT_I4):
            raise TypeError("Type mismatch: arg 15 不是 VT_BYREF|VT_I4 "
                            "(vt=%r)" % (_vt,))
        _es.value = self._err
        return self._mate if self._err == 1 else None


def _mk_model(model):
    m = swapi.SWModel.__new__(swapi.SWModel)
    m.model = model
    m.sw = None
    m._empty = swapi._null_dispatch()
    m.warnings = []
    return m


# 让探测必然走晚期：把 early 相关入口打成不可用
_orig_early_avail = swapi.is_early_available
_orig_asm_early = swapi.SWModel._asm_early
swapi.is_early_available = lambda: False
swapi.SWModel._asm_early = lambda self: self.model
try:
    # ① 晚期 + ErrorStatus=1 → 成功
    _fm = _FakeModelLate(err_status=1)
    _m = _mk_model(_fm)
    _mate, _api, _err = _m._add_mate_early(0, 0, False, None, None)
    check("★ ★ 晚期 AddMate5 收到 15 参且第 15 参是 VARIANT(VT_BYREF|VT_I4)",
          bool(_fm.calls) and len(_fm.calls[0]) == 15,
          ("calls=%r" % (len(_fm.calls[0]) if _fm.calls else None,)))
    check("★ 晚期 AddMate5 ErrorStatus=1 → 返回 Mate2 + used_api 标明 AddMate5",
          _mate is not None and "AddMate5" in str(_api),
          (_mate, _api, _err))
    check("★ 返回的 used_api 带 ErrorStatus 值（可审计）",
          "ErrorStatus=1" in str(_api), _api)

    # ② 晚期 + ErrorStatus=0(ErrorUnknown) → 必须判失败
    _fm2 = _FakeModelLate(err_status=0)
    _m2 = _mk_model(_fm2)
    _mate2, _api2, _err2 = _m2._add_mate_early(0, 0, False, None, None)
    check("★ ★ ErrorStatus=0 时即使返回对象也【判失败】",
          _mate2 is None, (_mate2, _api2, _err2))
    check("★ 失败时第三项带回可读原因（含错误码语义）",
          bool(_err2) and "ErrorStatus" in str(_err2), _err2)
    _diag = getattr(_m2, "_last_mate_diag", {}) or {}
    check("★ 诊断里记录了分层尝试明细（layers）",
          isinstance(_diag.get("layers"), list) and _diag["layers"],
          _diag)
    check("★ 诊断标出用的是晚期（probe.mode=late）",
          (_diag.get("probe") or {}).get("mode") == "late",
          _diag.get("probe"))

    # ③ 选择计数：一律 -1
    check("★ ★ _selected_count_all 只接受 -1 的读数（mark 记录里全是 -1）",
          _fm.SelectionManager.marks
          and all(k == -1 for k in _fm.SelectionManager.marks),
          _fm.SelectionManager.marks)
    check("★ 传 0 会读回 0（复现「计数异常 0」的语义成因）",
          _fm.SelectionManager.GetSelectedObjectCount2(0) == 0)
finally:
    # 恢复真实现
    swapi.is_early_available = _orig_early_avail
    swapi.SWModel._asm_early = _orig_asm_early


# ══════════════════════════════════════════════════════════════════════
sec("L. 假 COM 端到端：早期绑定探测 → CreateMate 主路径优先")
# ══════════════════════════════════════════════════════════════════════
class _FakeMateData(object):
    def __init__(self, mate_type):
        self.mate_type = mate_type
        self.attrs = {}

    def __setattr__(self, k, v):
        if k in ("mate_type", "attrs"):
            object.__setattr__(self, k, v)
        else:
            self.attrs[k] = v


class _FakeModelCreateMate(_FakeModelLate):
    """带 CreateMateData/CreateMate 的假装配体（早期绑定形态）。"""

    def __init__(self, cm_ok=True):
        _FakeModelLate.__init__(self, err_status=1)
        self.cm_calls = []
        self.data_calls = []
        self._cm_ok = cm_ok

    def CreateMateData(self, mate_type):
        self.data_calls.append(mate_type)
        return _FakeMateData(mate_type)

    def CreateMate(self, md):
        self.cm_calls.append((type(md).__name__,
                              dict(getattr(md, "attrs", {}))))
        return _FakeMate() if self._cm_ok else None


_orig_early_avail2 = swapi.is_early_available
_orig_asm_early2 = swapi.SWModel._asm_early


class _EarlyShim(object):
    """模拟早期绑定的 IAssemblyDoc（有自己的 CreateMateData/CreateMate）。"""

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def CreateMateData(self, t):
        return self._inner.CreateMateData(t)

    def CreateMate(self, md):
        return self._inner.CreateMate(md)


swapi.is_early_available = lambda: True
try:
    # ① 早期可用 → 走 CreateMate，且 AddMate5 一次都不该被调用
    _fcm = _FakeModelCreateMate(cm_ok=True)
    _mcm = _mk_model(_fcm)
    swapi.SWModel._asm_early = lambda self: _EarlyShim(self.model)
    _mt3, _api3, _err3 = _mcm._add_mate_early(0, 1, False, None, None)
    check("★ ★ 早期绑定可用时走 CreateMate 主路径",
          _mt3 is not None and "CreateMate" in str(_api3), (_api3, _err3))
    check("★ CreateMateData 被以配合类型调用（0=COINCIDENT）",
          _fcm.data_calls == [0], _fcm.data_calls)
    check("★ MateAlignment 被设成 align=1（ANTI_ALIGNED）",
          _fcm.cm_calls and _fcm.cm_calls[0][1].get("MateAlignment") == 1,
          _fcm.cm_calls)
    check("★ ★ CreateMate 成功时【不】再走 AddMate5（无重复建配合）",
          not _fcm.calls, _fcm.calls)

    # ② CreateMate 返回 None → 应回退 AddMate5，并如实记录 layers
    _fcm2 = _FakeModelCreateMate(cm_ok=False)
    _mcm2 = _mk_model(_fcm2)
    swapi.SWModel._asm_early = lambda self: _EarlyShim(self.model)
    _mt4, _api4, _err4 = _mcm2._add_mate_early(0, 1, False, None, None)
    check("★ ★ CreateMate 失败 → 自动回退（最终仍可成功）",
          _mt4 is not None, (_mt4, _api4, _err4))
    check("★ 回退后 used_api 不再是 CreateMate",
          "CreateMate" not in str(_api4), _api4)
    _d4 = getattr(_mcm2, "_last_mate_diag", {}) or {}
    _layers4 = [x.get("layer") for x in (_d4.get("layers") or [])]
    check("★ layers 里既记录了 CreateMate 失败也记录了后续尝试",
          any("CreateMate" in str(x) for x in _layers4), _layers4)
    # 早期绑定下 AddMate5 必须是 14 参（不是 15 参 + VARIANT）
    check("★ ★ 早期绑定 AddMate5 发 14 参（ByRef 自动解包，不再塞 VARIANT）",
          _fcm2.calls and len(_fcm2.calls[0]) == 14,
          (len(_fcm2.calls[0]) if _fcm2.calls else None))
finally:
    swapi.is_early_available = _orig_early_avail2
    swapi.SWModel._asm_early = _orig_asm_early2


# ══════════════════════════════════════════════════════════════════════
sec("M. 假 COM 端到端：干涉检查走 InterferenceDetectionManager + 误报防护")
# ══════════════════════════════════════════════════════════════════════
class _FakeInterference(object):
    Volume = 2.5e-8            # m³（属性，不带括号）

    def GetComponentCount(self):
        return 2

    @property
    def Components(self):
        class _C(object):
            Name2 = "DSH_底座-1"
        return (_C(), _C())


class _FakeInterfMgr(object):
    def __init__(self, count=0, items=None, count_mode="method"):
        self._count = count
        self._items = items
        self._mode = count_mode
        self.props = {}
        self.done = False

    def __setattr__(self, k, v):
        if k in ("_count", "_items", "_mode", "props", "done"):
            object.__setattr__(self, k, v)
        else:
            self.props[k] = v

    def GetInterferenceCount(self):
        return self._count

    def GetInterferences(self):
        return self._items

    def Done(self):
        self.done = True


class _FakeAsmInterf(object):
    def __init__(self, mgr):
        self._mgr = mgr
        self.SelectionManager = _FakeSelMgr()
        self.calls = []

    # ★ InterferenceDetectionManager 是 IAssemblyDoc 上的【属性】（报告 一-6）
    @property
    def InterferenceDetectionManager(self):
        return self._mgr

    def GetComponents(self, visible_only=False):
        return ()

    def ClearSelection2(self, flag):
        return True

    # 如果代码错误地走这条，会被记录下来
    def ToolsCheckInterference2(self, *a):
        self.calls.append(a)
        return None


# ① 无干涉 → count=0, ok=True, trustworthy=True, Done 被调用
_mgr0 = _FakeInterfMgr(count=0)
_fa0 = _FakeAsmInterf(_mgr0)
_m0 = _mk_model(_fa0)
swapi.SWModel._wrap_with_interface = lambda self, obj, name: None
_r0 = _m0.check_interference(treat_coincident=False)
check("★ 无干涉：ok=True count=0 trustworthy=True",
      _r0.get("ok") is True and _r0.get("count") == 0
      and _r0.get("trustworthy") is True, _r0)
check("★ ★ api 是 InterferenceDetectionManager（不是 ToolsCheckInterference2）",
      "InterferenceDetectionManager" in str(_r0.get("api")), _r0.get("api"))
check("★ ★ Done() 必须被调用（否则装配体卡在干涉显示模式）",
      _mgr0.done is True and _r0.get("done_called") is True, _r0.get("done_called"))
check("★ ★ 完全没有调用 ToolsCheckInterference2",
      not _fa0.calls, _fa0.calls)
check("★ TreatCoincidenceAsInterference 被设为 False（面贴面不算干涉）",
      _mgr0.props.get("TreatCoincidenceAsInterference") is False,
      _mgr0.props)
check("★ IncludeMultibodyPartInterferences=True",
      _mgr0.props.get("IncludeMultibodyPartInterferences") is True,
      _mgr0.props)

# ② 有干涉 → count=2 + 明细（Volume 属性 / Components 属性 / 方法带括号）
_mgr1 = _FakeInterfMgr(count=2,
                       items=[_FakeInterference(), _FakeInterference()])
_fa1 = _FakeAsmInterf(_mgr1)
_m1 = _mk_model(_fa1)
_r1 = _m1.check_interference()
check("★ ★ 有干涉：count=2（不再被误报成 0）",
      _r1.get("count") == 2 and _r1.get("ok") is True, _r1)
check("★ 明细取到 2 条且 volume_mm3 正确换算（2.5e-8 m³ = 25 mm³）",
      len(_r1.get("interferences") or []) == 2
      and abs((_r1["interferences"][0].get("volume_mm3") or 0) - 25.0) < 1e-6,
      _r1.get("interferences"))
check("★ 明细里组件名可读（.Components 属性按属性取值）",
      _r1["interferences"][0].get("components") == ["DSH_底座-1"] * 2,
      _r1["interferences"][0])

# ③ 计数读不出来 → 绝不报"无干涉"
class _BrokenMgr(_FakeInterfMgr):
    def GetInterferenceCount(self):
        raise RuntimeError("boom")


_mgr2 = _BrokenMgr()
_fa2 = _FakeAsmInterf(_mgr2)
_m2 = _mk_model(_fa2)
_r2 = _m2.check_interference()
check("★ ★ 计数读不出来 → ok=False 且 trustworthy=False（绝不报 count=0 通过）",
      _r2.get("ok") is False and _r2.get("trustworthy") is False, _r2)
check("★ 计数失败时仍调 Done()（不留干涉显示状态）",
      _mgr2.done is True)
check("★ 错误文案明确写「不可采信」",
      "不可采信" in str(_r2.get("error")), _r2.get("error"))

# ④ 完全没有 manager → 历史 API 返回 None 时不得当 0
class _NoMgrAsm(object):
    def __init__(self):
        self.SelectionManager = _FakeSelMgr()

    def GetComponents(self, visible_only=False):
        return ()

    def ToolsCheckInterference(self):
        return None          # 晚期绑定下读回 None —— 旧代码会当成 0


_fa3 = _NoMgrAsm()
_m3 = _mk_model(_fa3)
_r3 = _m3.check_interference()
check("★ ★ manager 缺失 + 历史 API 返回 None → ok=False（不是 count=0 通过）",
      _r3.get("ok") is False and not _r3.get("trustworthy"), _r3)
check("★ 此时 available 也不得为 True（结果不可采信）",
      _r3.get("available") is not True, _r3.get("available"))
check("★ 错误里带上两层的失败原因（便于排障）",
      "InterferenceDetectionManager" in str(_r3.get("error"))
      and "ToolsCheckInterference" in str(_r3.get("error")),
      _r3.get("error"))


# ══════════════════════════════════════════════════════════════════════
sec("N. 宏安全闸：非 OLE 复合文档必须拒绝（防挂死 COM 线程）")
# ══════════════════════════════════════════════════════════════════════
import tempfile  # noqa: E402

_tmpd = tempfile.mkdtemp(prefix="bug9_")
_fake_swp = os.path.join(_tmpd, "mate_macro.swp")
with open(_fake_swp, "wb") as _f:
    _f.write(b"' Sub main()\n' End Sub\n")      # 文本伪装的 .swp

_old_env = os.environ.get("DSH_MATE_MACRO")
os.environ["DSH_MATE_MACRO"] = _fake_swp
try:
    _mm_res = _m0._run_mate_macro(0, 0, 0.0, 0.0)
finally:
    if _old_env is None:
        os.environ.pop("DSH_MATE_MACRO", None)
    else:
        os.environ["DSH_MATE_MACRO"] = _old_env

check("★ ★ 文本伪装的 .swp 被拒绝执行（ok=False）",
      isinstance(_mm_res, dict) and _mm_res.get("ok") is False, _mm_res)
check("★ 拒绝原因明确写「不是二进制 OLE 复合文档」",
      "OLE" in str(_mm_res.get("error")), _mm_res.get("error"))
check("★ 拒绝原因提到「模态框/挂死」风险（让人知道为什么不能放行）",
      ("模态框" in str(_mm_res.get("error"))
       or "挂死" in str(_mm_res.get("error"))), _mm_res.get("error"))

# 未配置宏时返回 None（= 不尝试，不是失败）
_old_env2 = os.environ.pop("DSH_MATE_MACRO", None)
try:
    check("★ 未配置 DSH_MATE_MACRO → 返回 None（不尝试宏路径）",
          _m0._run_mate_macro(0, 0, 0.0, 0.0) is None)
finally:
    if _old_env2 is not None:
        os.environ["DSH_MATE_MACRO"] = _old_env2

# 配置了但文件不存在 → 结构化失败
os.environ["DSH_MATE_MACRO"] = os.path.join(_tmpd, "no_such.swp")
try:
    _mm_res2 = _m0._run_mate_macro(0, 0, 0.0, 0.0)
finally:
    if _old_env is None:
        os.environ.pop("DSH_MATE_MACRO", None)
    else:
        os.environ["DSH_MATE_MACRO"] = _old_env
check("★ 宏文件不存在 → ok=False 且说明原因",
      isinstance(_mm_res2, dict) and _mm_res2.get("ok") is False
      and "不存在" in str(_mm_res2.get("error")), _mm_res2)


# ══════════════════════════════════════════════════════════════════════
sec("O. 门禁端到端：--force-mate 留痕 + 降级说明（假 SW）")
# ══════════════════════════════════════════════════════════════════════
_logged = []
_orig_log_bypass = None
try:
    import defense_gate as _dg
    _orig_log_bypass = _dg.log_bypass
    _dg.log_bypass = lambda where, reason="", by="unknown", scope=None: \
        _logged.append({"where": where, "reason": reason, "by": by,
                        "scope": scope})
except Exception as _e:
    print("  [SKIP] defense_gate 不可用: %r" % (_e,))


class _NoMateDoc(object):
    """一个"一个配合都没有"的假装配体文档。"""

    def FeatureByName(self, name):
        return None


class _SwOpen(object):
    def OpenDoc6(self, *a):
        return _NoMateDoc()


def _patch_open_part(ok=True):
    _orig = swapi.SWModel.open_part

    def _fake(sw, path, doc_type=None, configuration=""):
        if not ok:
            return {"ok": False, "doc": None, "error": "fake open fail"}
        return {"ok": True, "doc": _NoMateDoc(), "title": os.path.basename(path)}

    swapi.SWModel.open_part = staticmethod(_fake)
    return _orig


_asm_fake = os.path.join(_tmpd, "DSH_减速器总装.sldasm")
with open(_asm_fake, "wb") as _f:
    _f.write(b"FakeAsm" * 64)

_orig_open = _patch_open_part(ok=True)
_orig_ev = sw_bridge._degraded_mate_evidence
sw_bridge._degraded_mate_evidence = lambda sw, p: {
    "ok": True, "strength": "weak", "components": 6, "poses": [],
    "interference": {"ok": True, "available": True, "count": 0,
                     "trustworthy": True, "approx": False,
                     "api": "InterferenceDetectionManager"},
    "note": "fake degraded evidence"}
try:
    # ① 无绕行 → 拦截
    _os_bypass = os.environ.pop("DSH_ASSEMBLY_MATE_BYPASS", None)
    _g0 = sw_bridge._assembly_mate_gate(_SwOpen(), _asm_fake,
                                        result={"_extra_args": []})
    check("★ ★ 无配合且无 --force-mate → blocked=True",
          _g0.get("blocked") is True and _g0.get("ok") is False, _g0)
    check("★ 违规时 state=violation", _g0.get("state") == "violation",
          _g0.get("state"))
    check("★ 违规时 degraded=True（说明这是降级交付面）",
          _g0.get("degraded") is True)

    # ② --force-mate → 放行 + 留痕 + 降级说明
    _logged[:] = []
    _g1 = sw_bridge._assembly_mate_gate(_SwOpen(), _asm_fake,
                                        result={"_extra_args": ["--force-mate"]})
    check("★ ★ --force-mate → 放行（blocked=False, ok=True）",
          _g1.get("blocked") is False and _g1.get("ok") is True, _g1)
    check("★ ★ 绕行必定留痕（log_bypass 被调用）", len(_logged) == 1, _logged)
    check("★ 留痕 scope=mate（可被报告侧单独统计）",
          _logged and _logged[0].get("scope") == "mate", _logged)
    check("★ 留痕 reason 里写明「配合未真正建立」",
          _logged and "配合未真正建立" in str(_logged[0].get("reason")),
          _logged)
    check("★ 留痕 by=cli（来源可追溯）",
          _logged and _logged[0].get("by") == "cli", _logged)
    check("★ ★ 返回体 note 明确写「配合未真正建立」+「仅由 add_component 摆位/干涉验证」",
          "配合未真正建立" in str(_g1.get("note"))
          and "摆位" in str(_g1.get("note")), _g1.get("note"))
    check("★ 返回体把降级警告单独挂一个字段（便于交付文案强制引用）",
          bool(_g1.get("degraded_delivery_warning")),
          _g1.get("degraded_delivery_warning"))
    check("★ 降级证据被采集并挂回返回体",
          isinstance(_g1.get("degraded_evidence"), dict)
          and _g1["degraded_evidence"].get("strength") == "weak",
          _g1.get("degraded_evidence"))
    check("★ 降级证据里的干涉结论可读（api/count/trustworthy）",
          (_g1.get("degraded_evidence") or {}).get("interference", {})
          .get("api") == "InterferenceDetectionManager",
          _g1.get("degraded_evidence"))

    # ③ env 绕行同样留痕，by=env
    _logged[:] = []
    os.environ["DSH_ASSEMBLY_MATE_BYPASS"] = "1"
    try:
        _g2 = sw_bridge._assembly_mate_gate(_SwOpen(), _asm_fake,
                                            result={"_extra_args": []})
    finally:
        os.environ.pop("DSH_ASSEMBLY_MATE_BYPASS", None)
    check("★ DSH_ASSEMBLY_MATE_BYPASS=1 也能放行",
          _g2.get("blocked") is False, _g2)
    check("★ env 绕行留痕 by=env",
          _logged and _logged[0].get("by") == "env", _logged)

    # ④ 打不开装配体 → 未校验（不拦截，但【不】显示成通过）
    _patch_open_part(ok=False)
    _g3 = sw_bridge._assembly_mate_gate(_SwOpen(), _asm_fake,
                                        result={"_extra_args": []})
    check("★ ★ 打不开装配体 → state=unverified（不拦截也不假装通过）",
          _g3.get("state") == "unverified" and _g3.get("blocked") is False,
          _g3)
    check("★ 未校验 note 明确「不能视为已通过配合门禁」",
          "不能视为已通过" in str(_g3.get("note")), _g3.get("note"))

    # ⑤ 非 .sldasm 不受门禁影响
    _g4 = sw_bridge._assembly_mate_gate(_SwOpen(), "x.SLDPRT",
                                        result={"_extra_args": []})
    check("★ 零件（.sldprt）不触发配合门禁",
          _g4.get("applies") is False and _g4.get("ok") is True, _g4)
finally:
    swapi.SWModel.open_part = _orig_open
    sw_bridge._degraded_mate_evidence = _orig_ev
    if _orig_log_bypass is not None:
        _dg.log_bypass = _orig_log_bypass
    if _os_bypass is not None:
        os.environ["DSH_ASSEMBLY_MATE_BYPASS"] = _os_bypass

# 清理临时目录
try:
    import shutil
    shutil.rmtree(_tmpd, ignore_errors=True)
except Exception:
    pass


# ══════════════════════════════════════════════════════════════════════
print("")
print("=" * 74)
print("汇总：PASS=%d  FAIL=%d" % (PASS, FAIL))
if FAILURES:
    print("失败项：")
    for x in FAILURES:
        print("  - " + x)
print("")
print("仍需真机验证（本测试不启动 SW，全部用假 COM）：")
print("  · SW2025 SP5.0 上 EnsureDispatch/EnsureModule 是否能拿到早期绑定 IAssemblyDoc；")
print("    early_probe.mode 到底是 early 还是 late（决定主路径）；")
print("  · CreateMateData(type) 的返回值在晚期绑定下能否设置 MateAlignment"
      "（报告 五-5 的未确认项）；")
print("  · 晚期 AddMate5(15 参 + VARIANT(VT_BYREF|VT_I4)) 是否真的不再报"
      " DISP_E_TYPEMISMATCH(-2147352571)；")
print("  · SelectByID2 用 '<实体>@<实例>@<装配体>' 名字 + mark=1 + "
      "VARIANT(VT_DISPATCH,None) 时，GetSelectedObjectCount2(-1) 是否真等于 2；")
print("  · InterferenceDetectionManager 的 GetInterferenceCount 在本机是"
      "属性式还是方法式；Done() 是否真的退出干涉显示模式；")
print("  · 真实 .swp 宏（二进制 OLE）的 GetMacroMethods 返回的模块名是否形如"
      " '<文件名>1'；")
print("  · 门禁降级证据里的位姿断言强度仍是 weak（需要建模脚本提供期望坐标"
      "才能真正 verify_assembly_placement）。")
sys.exit(0 if FAIL == 0 else 1)
