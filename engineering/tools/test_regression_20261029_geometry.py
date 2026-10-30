# -*- coding: utf-8 -*-
"""回归测试：SW API 几何缺陷 C01~C04 修复（2026-10-29）

全部为【静态/纯函数】断言，不需要 SolidWorks 在跑 —— 便于 CI 与换机复核。
真机验证项在文末以说明形式列出。

覆盖：
  C01  ISketchSegment 选段重试链里不再有 Select4（官方已 Obsolete，本机 0/24 全失败）
  C02  曲面判定改用官方 ISurface.IsPlane；_has_curved_face_at 必须"几何确认"才算曲面，
       且 begin_sketch_on_face 改为【平面优先】（旧的曲面先行否决已移除）
  C03  FeatureCut3 传 26 参、FeatureCut4 传 27 参（两者形参布局不同，不能共用元组）
  C04  screenshot 默认走 SaveBMP（后台渲染，不依赖前台窗口）；提供 _bmp_to_png

运行：python test_regression_20261029_geometry.py    退出码 0 = 通过
"""
import ast
import io
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
SWAPI = os.path.join(HERE, "swapi.py")

P = F = 0
FAILS = []


def ck(label, cond, extra=""):
    global P, F
    if cond:
        P += 1
        print("  [PASS] " + label)
    else:
        F += 1
        FAILS.append(label)
        print("  [FAIL] " + label + ("  " + str(extra)[:300] if extra else ""))


def sec(t):
    print("")
    print("=" * 74)
    print(t)
    print("=" * 74)


SRC = io.open(SWAPI, encoding="utf-8").read()
TREE = ast.parse(SRC)


def _func(name):
    for node in ast.walk(TREE):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _body_no_doc(fn):
    """取函数体源码，**剔除文档字符串**。

    坑：文档字符串里会提到 API 名（例如"不需要 SetForegroundWindow"、
    "原实现用 _has_curved_face_at 先行否决"），直接 find() 会命中注释，
    导致断言假失败。这里只保留真正的代码行。
    """
    src = ast.get_source_segment(SRC, fn) or ""
    if not fn.body:
        return src
    first = fn.body[0]
    if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)):
        doc = ast.get_source_segment(SRC, first) or ""
        if doc:
            src = src.replace(doc, "", 1)
    # 再剔除行内 # 注释（它们也可能提到 API 名）
    lines = []
    for ln in src.split("\n"):
        _s = ln.strip()
        if _s.startswith("#"):
            continue
        _i = ln.find("  #")
        if _i > 0:
            ln = ln[:_i]
        lines.append(ln)
    return "\n".join(lines)


sec("C01 选段：Select4 必须从重试链里彻底移除")
fn = _func("select_all_sketch_segments")
ck("函数存在", fn is not None)
if fn is not None:
    strs = [n.value for n in ast.walk(fn)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    bad = [s for s in strs if s == "Select4"]
    ck("★ 函数体内不再出现 'Select4' 字符串常量", not bad, bad)
    strat = None
    for n in ast.walk(fn):
        if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "_STRATEGIES":
            if isinstance(n.value, ast.Tuple):
                strat = [e.value for e in n.value.elts if isinstance(e, ast.Constant)]
    ck("★ 重试链 _STRATEGIES == ('Select2', 'Select')",
       strat == ["Select2", "Select"], strat)
    ck("Select2 是首选（Select4 已摘除）",
       strat is not None and strat[0] == "Select2", strat)
ck("源码里不再有 Select4 的 _try_one 分支",
   'strategy == "Select4"' not in SRC)

sec("C02 曲面判定：改用官方 ISurface.IsPlane + 几何确认")
ck("★ 新增 _face_is_planar", _func("_face_is_planar") is not None)
ck("★ 新增 _point_on_curved_face", _func("_point_on_curved_face") is not None)
fp = _func("_face_is_planar")
if fp is not None:
    body = ast.get_source_segment(SRC, fp) or ""
    ck("_face_is_planar 用 IsPlane", "IsPlane" in body)
    ck("_face_is_planar 有 Identity==4001 兜底", "4001" in body)
    ck("_face_is_planar 能返回 None（拿不到时）", "None" in body)

hcf = _func("_has_curved_face_at")
if hcf is not None:
    body = ast.get_source_segment(SRC, hcf) or ""
    ck("★ _has_curved_face_at 调用了 _face_is_planar（平面直接跳过）",
       "_face_is_planar" in body)
    ck("★ _has_curved_face_at 有几何确认 _point_on_curved_face",
       "_point_on_curved_face" in body)
    ck("GetBox 不再是唯一判据（仅粗筛）",
       "GetBox" in body and "_point_on_curved_face" in body)

ck("★ begin_sketch_on_face 里旧的『曲面先行否决』已移除",
   "not self._has_curved_face_at(x, y, z) and self._select_face_by_box" not in SRC)
bsf = _func("begin_sketch_on_face")
if bsf is not None:
    body = _body_no_doc(bsf)
    ck("★ begin_sketch_on_face 改为平面优先（_plane_hit）", "_plane_hit" in body)
    _i_plane = body.find("_plane_hit = bool(self._select_face_by_box")
    _i_curved = body.find("_has_curved_face_at")
    ck("★ 平面尝试出现在曲面判定之前（代码，非注释）",
       _i_plane >= 0 and (_i_curved < 0 or _i_plane < _i_curved),
       (_i_plane, _i_curved))

sec("C03 切除参数：Cut3=26 参 / Cut4=27 参")
ca = _func("_cut_args")
ck("★ 新增 _cut_args 辅助函数", ca is not None)
if ca is not None:
    base_len = None
    for n in ast.walk(ca):
        if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "_base":
            if isinstance(n.value, ast.Tuple):
                base_len = len(n.value.elts)
    ck("★ _base 为 26 个元素（= FeatureCut3 形参数）", base_len == 26, base_len)
    body = ast.get_source_segment(SRC, ca) or ""
    ck("★ Cut4 在 _base 上追加 1 个（→27）", "_base + (False,)" in body)
    ck("按 API 名区分（endswith）", "endswith" in body)
    ck("UseAutoSelect 位置正确（auto_select 传给该位）",
       "auto_select,     # UseAutoSelect" in body)

calls_cut_args = 0
calls_raw = 0
for n in ast.walk(TREE):
    if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_try_cut":
        if len(n.args) >= 2 and isinstance(n.args[0], ast.Name) and n.args[0].id == "_api":
            a1 = n.args[1]
            if isinstance(a1, ast.Call) and getattr(a1.func, "id", "") == "_cut_args":
                calls_cut_args += 1
            else:
                calls_raw += 1
ck("★ 3 个 cut 调用点全部走 _cut_args", calls_cut_args == 3, calls_cut_args)
ck("★ 没有残留的『共用 26 元组』直传", calls_raw == 0, calls_raw)

sec("C04 截图：默认走 SaveBMP（不依赖前台窗口）")
ck("★ 新增 _screenshot_via_savebmp", _func("_screenshot_via_savebmp") is not None)
ck("★ 新增模块级 _bmp_to_png", _func("_bmp_to_png") is not None)
ck("_bmp_to_png 用 Pillow", "from PIL import Image" in SRC)

ss = _func("screenshot")
if ss is not None:
    body = ast.get_source_segment(SRC, ss) or ""
    _i_save = body.find("_screenshot_via_savebmp")
    _i_front = body.find("bring_to_front")
    ck("★ screenshot 先试 SaveBMP", _i_save >= 0)
    ck("★ SaveBMP 在 bring_to_front 之前（后台优先）",
       _i_save >= 0 and (_i_front < 0 or _i_save < _i_front),
       (_i_save, _i_front))

sv = _func("_screenshot_via_savebmp")
if sv is not None:
    body = _body_no_doc(sv)
    ck("调 SaveBMP", "SaveBMP" in body)
    ck("校验 BMP magic（b'BM'）", '"BM"' in body or "'BM'" in body)
    ck("校验文件大小（>54 字节头）", "54" in body)
    ck("★ SaveBMP 路径不调用 SetForegroundWindow / bring_to_front",
       "SetForegroundWindow" not in body and "bring_to_front" not in body,
       [ln for ln in body.split(chr(10))
        if "SetForegroundWindow" in ln or "bring_to_front" in ln][:3])

sec("纯函数：_bmp_to_png 容错（不依赖真机）")
try:
    sys.path.insert(0, HERE)
    import swapi  # noqa: E402
    ck("import swapi 成功", True)
    ck("_bmp_to_png 对不存在文件返回 False",
       swapi._bmp_to_png(os.path.join(HERE, "__no_such__.bmp"),
                         os.path.join(HERE, "__no_such__.png")) is False)
    ck("_bmp_to_png 函数可调用", callable(getattr(swapi, "_bmp_to_png", None)))
except Exception as _e:
    ck("import swapi 成功", False, repr(_e))

print("")
print("=" * 74)
print("汇总：PASS=%d  FAIL=%d" % (P, F))
if FAILS:
    print("失败项：")
    for x in FAILS:
        print("  - " + x)
print("")
print("真机验证（需 SW 在跑，人工执行）：")
print("  · 带 Ø10 通孔的板：_has_curved_face_at(0,0,10) 必须为 False")
print("  · 窗口最小化后 screenshot() 必须 ok=True 且文件 >1KB")
print("  · Ø10 通孔板 cut 去料体积 == 理论值 ±1%")
sys.exit(0 if F == 0 else 1)
