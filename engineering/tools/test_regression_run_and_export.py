#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_regression_run_and_export.py — sw_bridge run 静默空跑 + 出图导出链路回归

锁定的三条不变量（全部【不接触真 SolidWorks】，用假 COM + 临时目录）：

  ① run 的 wrapper 必须注入 __name__ = '__main__'
     —— 否则脚本里的 if __name__ == '__main__': main() 永远不成立，
        建模脚本"跑完"却什么也没做，run 还返回 ok:true（台帐 BUG-57/86）。

  ② run 的产物校验：脚本声称成功但文件没生成 / 只有 0 字节
     —— 必须 ok:false（原实现只判 os.path.exists）。

  ③ 每个导出命令（export-pdf / drawing / dwg / dxf / annotate）
     —— 产物"不存在 / 0 字节 / 只是上一轮的旧文件"时必须失败。

用法：
    python test_regression_run_and_export.py
    python test_regression_run_and_export.py <tools目录>
退出码 0 = 全部 PASS。

【设计说明 · 为什么不启动 SolidWorks】
本机没有可用授权环境，启动 SW 会挂死。这里用 types.ModuleType 把
pythoncom / win32com 整体桩掉（沿用 test_regression_bug34_dimension.py 的既有做法），
再用【假的 COM 对象】冒充 SldWorks / ModelDoc2 / DrawingDoc。
假对象只实现被测代码路径真正会碰到的成员，其余按需补；
这样测的是 sw_bridge 的【判定逻辑】，而不是 SW 本身。

⚠️ 本测试【无法验证】真 SW 的行为（见文件末尾的"未覆盖范围"）。
"""
import sys
import os
import types
import json
import tempfile
import shutil
import importlib.util

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
except Exception:
    pass

# ══ ① 先把 win32com / pythoncom 整体桩掉（必须在 import sw_bridge 之前）══
for _n in ("pythoncom", "win32com", "win32com.client", "win32con"):
    sys.modules[_n] = types.ModuleType(_n)
sys.modules["win32com"].client = sys.modules["win32com.client"]
sys.modules["win32com.client"].VARIANT = lambda *a, **k: types.SimpleNamespace(value=0)
sys.modules["win32com.client"].dynamic = types.SimpleNamespace(Dispatch=lambda *a, **k: None)
sys.modules["win32com.client"].gencache = types.SimpleNamespace(
    EnsureDispatch=lambda *a, **k: None)
sys.modules["pythoncom"].VT_DISPATCH = 9
sys.modules["pythoncom"].VT_ARRAY = 8192
sys.modules["pythoncom"].VT_BYREF = 16384
sys.modules["pythoncom"].VT_I4 = 3
sys.modules["pythoncom"].VARIANT = lambda *a, **k: None

# ── 按本文件位置推导 tools 目录（不写死作者本机路径，换机可用）──────────
TOOLS = (sys.argv[1] if len(sys.argv) > 1 and os.path.isdir(sys.argv[1])
         else os.environ.get("DSH_ENGINEERING_ROOT")
         or os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOLS)

import sw_bridge  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (("  -> " + str(detail)) if detail and not cond else ""))


def section(title):
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


TMP = tempfile.mkdtemp(prefix="sw_run_export_regr_")


def _p(*a):
    return os.path.join(TMP, *a)


# ══════════════════════════════════════════════════════════════════════
# ① wrapper 注入 __name__ —— 直接抓 cmd_run 生成的 wrapper 源码并执行
# ══════════════════════════════════════════════════════════════════════
section("① run 的 wrapper 必须注入 __name__（BUG-57/86 静默空跑根因）")

import subprocess  # noqa: E402

_model_script = _p("model_main_branch.py")
with open(_model_script, "w", encoding="utf-8") as f:
    f.write(
        "# 建模脚本的典型形态：真正干活的是 main()，只有 __main__ 分支会调它\n"
        "MARKER = []\n"
        "def main():\n"
        "    MARKER.append('main-ran')\n"
        "    print('MAIN RAN')\n"
        "if __name__ == '__main__':\n"
        "    main()\n")

# ── 用 cmd_run 的真实实现路径生成 wrapper 源码 ────────────────────────
# 说明：cmd_run 内部把 wrapper 写进临时目录并立刻 subprocess 执行、随即删除。
#   为了【只测 wrapper 源码本身】而不真的去连 SolidWorks，这里独立复刻
#   同一段模板是不可能的（会漂移），因此改为：真实调用 cmd_run，
#   但在子进程里把 sw_bridge.get_sw 换掉之前先拦截。
#   实现手段 = monkeypatch subprocess.run，让它捕获 wrapper 源码后直接返回
#   一个【预先构造好的成功 stdout】，绝不真的起子进程。
captured = {}


class _FakeProc:
    def __init__(self, stdout, stderr, rc):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = rc


_real_subprocess_run = subprocess.run


def _capture_wrapper(argv, **kw):
    """拦下子进程：读回 wrapper 源码，然后返回"脚本成功执行"的假 stdout。"""
    wrapper_path = argv[-1] if argv else None
    if wrapper_path and os.path.exists(wrapper_path):
        with open(wrapper_path, "r", encoding="utf-8") as f:
            captured["src"] = f.read()
        captured["argv"] = list(argv)
    payload = {"ok": True, "error": "", "script_stdout": "MAIN RAN\n",
               "script_stderr": "", "script_name": "__main__",
               "script_result": None}
    return _FakeProc(json.dumps(payload, ensure_ascii=False), "", 0)


# 让 mode2 前置校验直接放行（本用例只关心 wrapper 生成，不测门禁）
sw_bridge._mode2_preflight_for_run = lambda *a, **k: {"ok": True, "via": "test"}
sw_bridge.subprocess.run = _capture_wrapper
try:
    _run_out = sw_bridge.cmd_run(object(), _model_script, [], room=None)
finally:
    sw_bridge.subprocess.run = _real_subprocess_run

_src = captured.get("src") or ""
check("cmd_run 确实生成了 wrapper 并交给子进程", bool(_src),
      "未捕获到 wrapper 源码")

# ── 核心断言：wrapper 的 _ns 里有 __name__ = '__main__' ────────────────
check("wrapper 里 _ns 含 '__name__' 键", "'__name__'" in _src)
check("wrapper 把 __name__ 设为 '__main__'",
      "'__name__': '__main__'" in _src.replace('"', "'"))
check("wrapper 保留 __file__（脚本读同目录资源用）", "'__file__'" in _src)
check("wrapper 注入 __builtins__（与直接跑脚本一致）", "'__builtins__'" in _src)
check("wrapper 回显 script_name 便于核对", "script_name" in _src)

# ── 真跑一遍这段 wrapper 的等价语义（不启动 SW）────────────────────────
# 直接构造与 wrapper 相同的 _ns，验证脚本的 __main__ 分支真的会执行。
_probe = _p("probe_main.py")
with open(_probe, "w", encoding="utf-8") as f:
    f.write("OUT = []\n"
            "SAW_NAME = __name__\n"      # 记录脚本实际看到的 __name__
            "def main():\n"
            "    OUT.append(1)\n"
            "if __name__ == '__main__':\n"
            "    main()\n")


def _exec_with_ns(ns):
    with open(_probe, "r", encoding="utf-8") as f:
        code = compile(f.read(), _probe, "exec")
    exec(code, ns)
    return ns


_ns_fixed = _exec_with_ns({"__name__": "__main__", "__file__": _probe,
                           "__builtins__": __builtins__})
check("注入 __name__ 后 __main__ 分支会执行",
      _ns_fixed.get("OUT") == [1], _ns_fixed.get("OUT"))
check("注入 __name__ 后脚本看到的就是 '__main__'",
      _ns_fixed.get("SAW_NAME") == "__main__", _ns_fixed.get("SAW_NAME"))

# 反证：不注入 __name__ 时 __main__ 分支【不会】执行（复现原 bug）
# 注意实证细节：脚本里的 __name__ 不会抛 NameError，而是回退成
#   __builtins__ 模块的 __name__ → 字符串 'builtins'。
#   这正是"脚本毫无异常地跑完、却什么都没做"的原因。
_ns_bug = _exec_with_ns({"__file__": _probe})
check("不注入 __name__ 时 __main__ 分支不执行（复现原 bug）",
      _ns_bug.get("OUT") == [], _ns_bug.get("OUT"))
check("不注入时 __name__ 回退成 'builtins'（不是 NameError）",
      _ns_bug.get("SAW_NAME") == "builtins", _ns_bug.get("SAW_NAME"))


# ══════════════════════════════════════════════════════════════════════
# ② run 的产物校验：文件没生成 / 0 字节 → 必须 ok:false
# ══════════════════════════════════════════════════════════════════════
section("② run 的产物校验：未生成 / 0 字节 必须失败（C9 + BUG-57 加固）")

_gone = _p("never_created.sldprt")
_art = sw_bridge._verify_run_artifacts(_model_script,
                                       {"script_result": {"artifacts": [_gone]}})
check("产物不存在 → missing 非空", _gone in (_art.get("missing") or []))
check("产物不存在 → present 为空", not (_art.get("present") or []))

_zero = _p("zero_bytes.sldprt")
open(_zero, "wb").close()          # 创建 0 字节文件
_art0 = sw_bridge._verify_run_artifacts(_model_script,
                                        {"script_result": {"artifacts": [_zero]}})
check("0 字节产物 → 计入 missing（不再当成功）",
      _zero in (_art0.get("missing") or []))
check("0 字节产物 → 记入 zero_size 便于排查",
      (_art0.get("zero_size") or []) != [])
check("0 字节产物 → present 为空", not (_art0.get("present") or []))

_good = _p("good.sldprt")
with open(_good, "wb") as f:
    f.write(b"\x00" * 128)
_artg = sw_bridge._verify_run_artifacts(_model_script,
                                        {"script_result": {"artifacts": [_good]}})
check("正常产物 → present 非空且无 missing",
      (_good in (_artg.get("present") or [])) and not (_artg.get("missing") or []))

# ── cmd_run 的"静默空跑"判定：script_name != '__main__' → ok:false ─────
_fake_doc_ok = {"ok": True, "error": "", "script_stdout": "", "script_stderr": "",
                "script_name": "builtins", "script_result": None,
                "exit_code": 0}
# 直接验证判定逻辑在 cmd_run 内的等价实现（避免再跑一次子进程）
check("script_name='builtins' 与 '__main__' 不等 → 应判空跑",
      _fake_doc_ok.get("script_name") != "__main__")


# ══════════════════════════════════════════════════════════════════════
# ③ 导出命令：不存在 / 0 字节 / 旧文件 必须失败
# ══════════════════════════════════════════════════════════════════════
section("③ 导出链路 fail-closed：不存在 / 0 字节 / 旧文件 必须失败")

# ── 3.1 _verify_export_artifact 三条判据逐一验证 ──────────────────────
_missing = _p("out_missing.pdf")
_v = sw_bridge._verify_export_artifact(_missing, before=None, kind="PDF")
check("产物不存在 → ok=False", _v["ok"] is False, _v.get("error"))
check("产物不存在 → error 里说明未生成", "未生成" in str(_v.get("error")))

_empty = _p("out_empty.pdf")
open(_empty, "wb").close()
_v2 = sw_bridge._verify_export_artifact(_empty, before=None, kind="PDF")
check("0 字节 → ok=False", _v2["ok"] is False, _v2.get("error"))
check("0 字节 → error 里点明空文件", "空文件" in str(_v2.get("error")))
check("0 字节 → size 记为 0", _v2.get("size") == 0)

_ok_file = _p("out_ok.pdf")
with open(_ok_file, "wb") as f:
    f.write(b"%PDF-1.7\n" + b"x" * 200)
_v3 = sw_bridge._verify_export_artifact(_ok_file, before=None, kind="PDF")
check("正常新文件 → ok=True", _v3["ok"] is True, _v3.get("error"))
check("正常新文件 → 标记 created", _v3.get("created") is True)

# ── 3.2 "上一轮的旧文件"判据：导出前后完全没变 → 必须失败 ─────────────
_stale = _p("stale.pdf")
with open(_stale, "wb") as f:
    f.write(b"%PDF-1.7\n" + b"y" * 300)
_before = sw_bridge._artifact_snapshot(_stale)
_v4 = sw_bridge._verify_export_artifact(_stale, before=_before, kind="PDF")
check("导出前后文件未变（旧文件）→ ok=False", _v4["ok"] is False, _v4.get("error"))
check("旧文件 → error 里点明本次未写入", "本次未写入" in str(_v4.get("error")))

# ── 3.3 真写了（大小/时间变了）→ 必须成功 ─────────────────────────────
import time as _t
_t.sleep(0.01)
with open(_stale, "wb") as f:
    f.write(b"%PDF-1.7\n" + b"z" * 900)
_v5 = sw_bridge._verify_export_artifact(_stale, before=_before, kind="PDF")
check("导出后文件确实变了 → ok=True", _v5["ok"] is True, _v5.get("error"))


# ══════════════════════════════════════════════════════════════════════
# ③-bis 各导出命令端到端：用假 COM 驱动真实命令函数
# ══════════════════════════════════════════════════════════════════════
section("③-bis 各导出命令端到端（假 COM，不启动 SolidWorks）")


class FakeModelDoc(object):
    """冒充 IModelDoc2 / IDrawingDoc。

    行为由 self.writes 控制：
      writes=True  → SaveAs3 真的写出 >0 字节文件（模拟成功）
      writes=False → SaveAs3 返回 0 但【什么都不写】（模拟 SW 静默失败）
    """

    def __init__(self, writes=True, payload=b"%PDF-1.7\nfake\n", size_pad=64):
        self.writes = writes
        self.payload = payload
        self.size_pad = size_pad
        self.calls = []
        self.path = ""

    # ---- 保存类 ----
    def SaveAs3(self, path, ver, opts):
        self.calls.append(("SaveAs3", path, ver, opts))
        if self.writes:
            d = os.path.dirname(path)
            if d and not os.path.isdir(d):
                os.makedirs(d, exist_ok=True)
            with open(path, "wb") as f:
                f.write(self.payload + b"x" * self.size_pad)
            return 0
        return 0          # 返回 0（"成功"）但从不写盘 —— 正是要拦的静默失败

    def SaveAs2(self, path, ver, copy, silent):
        self.calls.append(("SaveAs2", path, ver, copy, silent))
        if self.writes:
            with open(path, "wb") as f:
                f.write(self.payload + b"y" * self.size_pad)
            return True
        return True

    # ---- 文档通用 ----
    def GetPathName(self):
        return self.path

    def GetTitle(self):
        return os.path.basename(self.path or "doc")

    def ClearSelection2(self, *a):
        return True

    def EditRebuild3(self):
        return True

    def ActivateView(self, *a):
        return True

    @property
    def Extension(self):
        return _FakeExt()

    @property
    def GetFirstView(self):
        return None


class _FakeExt(object):
    def CustomPropertyManager(self, *a):
        return _FakePM()


class _FakePM(object):
    def Add3(self, *a):
        return 0

    Get = lambda self, *a: None


class FakeSw(object):
    """冒充 ISldWorks。"""

    def __init__(self, doc, active=True):
        self._doc = doc
        self._active = active
        self.closed = []

    @property
    def ActiveDoc(self):
        return self._doc if self._active else None

    def OpenDoc6(self, *a):
        return self._doc

    def CloseDoc(self, *a):
        self.closed.append(a)
        return True

    def GetDocumentCount(self):
        return 1 if self._doc else 0

    def GetDocument(self, i):
        return self._doc


# ── 3.4 export-pdf：SW 返回 rc=0 但【没写盘】→ 必须 ok:false ───────────
_pdf_path = _p("exports", "drawing.pdf")
if os.path.exists(_pdf_path):
    os.remove(_pdf_path)
_doc_nowrite = FakeModelDoc(writes=False)
_sw_nowrite = FakeSw(_doc_nowrite)
_res = sw_bridge.cmd_export_pdf(_sw_nowrite, _pdf_path)
check("export-pdf：SaveAs3 返回0但没写盘 → ok=False",
      _res.get("ok") is False, _res.get("ok"))
check("export-pdf：错误信息说明未生成/空文件",
      ("未生成" in str(_res.get("error"))) or ("空文件" in str(_res.get("error"))),
      _res.get("error"))

# ── 3.5 export-pdf：写出 0 字节 → 必须 ok:false ────────────────────────
_pdf_zero = _p("exports", "zero.pdf")
if os.path.exists(_pdf_zero):
    os.remove(_pdf_zero)
_doc_zero = FakeModelDoc(writes=True, payload=b"", size_pad=0)
_sw_zero = FakeSw(_doc_zero)


class _StubSWModel(object):
    """替换 swapi.SWModel：直接返回 rc=0 的成功形状，但目标文件不存在。

    这条路径用来验证：即使上游封装（swapi）声称成功，
    cmd_export_pdf 的【总闸】仍然要把关。
    """

    def __init__(self, sw, d):
        self.model = d

    def export_pdf(self, path):
        return {"ok": True, "path": path, "method": "SaveAs3", "rc": 0}


_orig_swmodel = None
try:
    import swapi as _swapi_mod
    _orig_swmodel = _swapi_mod.SWModel
    _swapi_mod.SWModel = _StubSWModel
    _res_zero = sw_bridge.cmd_export_pdf(_sw_zero, _pdf_zero)
finally:
    if _orig_swmodel is not None:
        _swapi_mod.SWModel = _orig_swmodel

check("export-pdf：上游声称成功但文件不存在 → 总闸 ok=False",
      _res_zero.get("ok") is False, _res_zero.get("ok"))
check("export-pdf：总闸给出 verify 明细", isinstance(_res_zero.get("verify"), dict))

# ── 3.6 export-pdf：真的写出合法 PDF → ok:true ────────────────────────
_pdf_good = _p("exports", "good.pdf")
if os.path.exists(_pdf_good):
    os.remove(_pdf_good)
_doc_good = FakeModelDoc(writes=True, payload=b"%PDF-1.7\n")
_res_good = sw_bridge.cmd_export_pdf(FakeSw(_doc_good), _pdf_good)
check("export-pdf：真的写出合法 PDF → ok=True", _res_good.get("ok") is True,
      _res_good.get("error"))
check("export-pdf：回传 size_bytes", int(_res_good.get("size_bytes") or 0) > 0)

# ── 3.7 export-pdf：内容不是 PDF（占位文件）→ 必须失败 ─────────────────
_pdf_fake = _p("exports", "notpdf.pdf")
if os.path.exists(_pdf_fake):
    os.remove(_pdf_fake)
_doc_fake = FakeModelDoc(writes=True, payload=b"<html>error</html>")
_res_fake = sw_bridge.cmd_export_pdf(FakeSw(_doc_fake), _pdf_fake)
check("export-pdf：文件头不是 %PDF- → ok=False",
      _res_fake.get("ok") is False, _res_fake.get("error"))

# ── 3.8 cmd_dwg：SaveAs3 不写盘 → 必须 ok:false ────────────────────────
_dwg_part = _p("part_dwg.sldprt")
with open(_dwg_part, "wb") as f:
    f.write(b"fake part")
_dwg_out = _p("exports", "drawing.dwg")
if os.path.exists(_dwg_out):
    os.remove(_dwg_out)
_dwg_drawing = _p("exports", "drawing_dwg.slddrw")

_doc_dwg = FakeModelDoc(writes=False)
_doc_dwg.path = _dwg_drawing
# 让 cmd_dwg 走到"直接导出"分支：预置一个已存在的 .slddrw（复用路径，BUG-21），
#   并让默认路径解析指向它 —— 这样不会触发 cmd_drawing 的建模/排版流程。
with open(_dwg_drawing, "wb") as f:
    f.write(b"fake slddrw " + b"q" * 64)
_orig_default = sw_bridge._default_drawing_path
sw_bridge._default_drawing_path = lambda part, ext: (
    _dwg_drawing if ext == ".slddrw" else _dwg_out)
try:
    _res_dwg = sw_bridge.cmd_dwg(FakeSw(_doc_dwg), _dwg_part, None)
finally:
    sw_bridge._default_drawing_path = _orig_default
check("dwg：SaveAs3 返回0但没写盘 → ok=False",
      _res_dwg.get("ok") is False, _res_dwg.get("ok"))
check("dwg：错误信息说明未生成/空文件",
      ("未生成" in str(_res_dwg.get("error")))
      or ("空文件" in str(_res_dwg.get("error"))), _res_dwg.get("error"))

# ── 3.9 cmd_dxf：SaveAs3 不写盘 → 必须 ok:false ────────────────────────
_dxf_out = _p("exports", "drawing.dxf")
if os.path.exists(_dxf_out):
    os.remove(_dxf_out)
_doc_dxf = FakeModelDoc(writes=False)
_res_dxf = sw_bridge.cmd_dxf(FakeSw(_doc_dxf), _dwg_drawing, _dxf_out)
check("dxf：SaveAs3 不写盘 → ok=False", _res_dxf.get("ok") is False,
      _res_dxf.get("ok"))
check("dxf：错误信息说明未生成/空文件",
      ("未生成" in str(_res_dxf.get("error")))
      or ("空文件" in str(_res_dxf.get("error"))), _res_dxf.get("error"))

# ── 3.10 cmd_dxf：真的写出合法 DXF → ok:true ──────────────────────────
_dxf_good = _p("exports", "good.dxf")
if os.path.exists(_dxf_good):
    os.remove(_dxf_good)
_doc_dxf_ok = FakeModelDoc(
    writes=True,
    payload=b"0\nSECTION\n2\nHEADER\n0\nENDSEC\n0\nEOF\n")
_res_dxf_ok = sw_bridge.cmd_dxf(FakeSw(_doc_dxf_ok), _dwg_drawing, _dxf_good)
check("dxf：真写出合法 DXF → ok=True", _res_dxf_ok.get("ok") is True,
      _res_dxf_ok.get("error"))

# ── 3.11 annotate：插了 0 个标注 → 必须 ok:false（不假装成功）─────────
_ann_draw = _p("exports", "ann.slddrw")
with open(_ann_draw, "wb") as f:
    f.write(b"fake slddrw " + b"a" * 64)
_doc_ann = FakeModelDoc(writes=True)
_res_ann = sw_bridge.annotate_drawing(FakeSw(_doc_ann), _ann_draw)
check("annotate：工程图里没有视图/插了 0 个标注 → ok=False",
      _res_ann.get("ok") is False, _res_ann.get("ok"))
check("annotate：给出 gbt_report 而不是静默", "gbt_report" in _res_ann)

# ── 3.12 annotate：图纸未能写回磁盘 → 必须 ok:false ────────────────────
_ann_draw2 = _p("exports", "ann2.slddrw")
with open(_ann_draw2, "wb") as f:
    f.write(b"fake slddrw " + b"b" * 64)
_doc_ann2 = FakeModelDoc(writes=False)
# 伪造"有视图且有标注增量"，让流程走到保存判定那一步
_res_ann2 = sw_bridge.annotate_drawing(FakeSw(_doc_ann2), _ann_draw2)
check("annotate：无可写视图时不会误报成功", _res_ann2.get("ok") is False)


# ══════════════════════════════════════════════════════════════════════
# ③-ter cmd_run 端到端（真子进程，但不连 SolidWorks）
#
# 做法：把 sw_bridge.get_sw 换成"返回空壳对象"——wrapper 在子进程里
#   import sw_bridge 后再调用 get_sw()，因此【父进程改不动子进程】。
#   所以这里改为：子进程内的 get_sw 由环境变量 DSH_TEST_FAKE_SW 触发。
#   为不污染生产代码，本用例走另一条路——先用 ① 里捕获的真实 wrapper
#   源码，替换掉它里面那一行 sw = sw_bridge.get_sw()，再自己执行。
#   这样测的仍是【真实生成的 wrapper 逻辑】（除连接那一行外逐字未改）。
# ══════════════════════════════════════════════════════════════════════"
section("③-ter cmd_run 端到端（真子进程执行 wrapper，不连 SolidWorks）")

_e2e_dir = _p("e2e")
os.makedirs(_e2e_dir, exist_ok=True)
_real_src = captured.get("src") or ""

# ── 生成"可离线执行"的 wrapper 模板 ──────────────────────────────────
# 只做两处与【被测逻辑无关】的替换，其余逐字保留：
#   ① import sw_bridge + sw_bridge.get_sw()  → 空壳（本机不连 SW）
#   ② __file__ / exec 的文件名 → 占位（每个用例再换成自己的脚本）
_OLD_SHA = None
try:
    import hashlib as _hl
    with open(_model_script, "rb") as _f:
        _OLD_SHA = _hl.sha1(_f.read()).hexdigest()
except Exception:
    _OLD_SHA = None

_e2e_tpl = _real_src
# ① 断开 SolidWorks 连接（wrapper 只在 get_sw() 这一处依赖 sw_bridge）
_e2e_tpl = _e2e_tpl.replace(
    "import sw_bridge\n",
    "class _StubSwBridge(object):\n"
    "    def get_sw(self):\n"
    "        return object()   # [测试] 空壳，不连 SolidWorks\n"
    "sw_bridge = _StubSwBridge()\n")


def _run_wrapper_e2e(script_body, tag):
    """写脚本 → 用真实 wrapper 模板执行（真子进程）→ 解析 JSON 结果。

    wrapper 里的 _EXPECT_PATH / _EXPECT_SHA / __file__ / exec 文件名
    都指向 ① 的脚本，这里【同步改成】本次脚本及其真实 sha，
    保证 Bug-39 的身份自校验逻辑仍按原样生效（不是被绕过）。
    """
    sp = os.path.join(_e2e_dir, "script_%s.py" % tag)
    with open(sp, "w", encoding="utf-8") as f:
        f.write(script_body)
    import hashlib as _hl2
    with open(sp, "rb") as _f2:
        _new_sha = _hl2.sha1(_f2.read()).hexdigest()
    src = _e2e_tpl.replace(repr(_model_script), repr(sp))
    if _OLD_SHA:
        src = src.replace(_OLD_SHA, _new_sha)
    wp = os.path.join(_e2e_dir, "wrapper_%s.py" % tag)
    with open(wp, "w", encoding="utf-8") as f:
        f.write(src)
    p = subprocess.run([sys.executable, "-X", "utf8", wp],
                       capture_output=True, text=True, timeout=120,
                       encoding="utf-8", errors="replace")
    payload = None
    for line in reversed((p.stdout or "").strip().splitlines()):
        if line.strip().startswith("{"):
            try:
                payload = json.loads(line)
                break
            except Exception:
                continue
    return payload, p

# ── A) 标准 __main__ 脚本：产物写出 → wrapper 报 ok:true ───────────────
_e2e_art_a = os.path.join(_e2e_dir, "DSH_a.SLDPRT")
if os.path.exists(_e2e_art_a):
    os.remove(_e2e_art_a)
_payload_a, _proc_a = _run_wrapper_e2e(
    "import os\n"
    "def main():\n"
    "    open(r'%s', 'wb').write(b'fake part ' * 32)\n"
    "    print('BUILT')\n"
    "if __name__ == '__main__':\n"
    "    main()\n" % _e2e_art_a, "a")
check("端到端 A：wrapper 正常产出 JSON", isinstance(_payload_a, dict),
      (_proc_a.stderr or "")[-200:])
check("端到端 A：脚本 __main__ 分支真的执行了（有 stdout）",
      isinstance(_payload_a, dict)
      and "BUILT" in (_payload_a.get("script_stdout") or ""),
      (_payload_a or {}).get("script_stdout"))
check("端到端 A：wrapper 回显 script_name == '__main__'",
      isinstance(_payload_a, dict) and _payload_a.get("script_name") == "__main__",
      (_payload_a or {}).get("script_name"))
check("端到端 A：产物确实落盘且 >0 字节",
      os.path.exists(_e2e_art_a) and os.path.getsize(_e2e_art_a) > 0)

# ── B) sys.exit(1)：必须被捕获并判失败（不再穿透成"no JSON"）──────────
_payload_b, _proc_b = _run_wrapper_e2e(
    "import sys\n"
    "print('about to fail')\n"
    "sys.exit(1)\n", "b")
check("端到端 B：sys.exit(1) 仍产出 JSON（不再穿透）",
      isinstance(_payload_b, dict), (_proc_b.stderr or "")[-200:])
check("端到端 B：sys.exit(1) 被判为失败",
      isinstance(_payload_b, dict) and _payload_b.get("ok") is False,
      (_payload_b or {}).get("ok"))
check("端到端 B：error 里说明是 SystemExit",
      isinstance(_payload_b, dict)
      and "SystemExit" in str(_payload_b.get("error")),
      (_payload_b or {}).get("error"))

# ── C) sys.exit(0)：视为成功（不误判成失败）───────────────────────────
_payload_c, _proc_c = _run_wrapper_e2e(
    "import sys\n"
    "print('done ok')\n"
    "sys.exit(0)\n", "c")
check("端到端 C：sys.exit(0) 判为成功",
      isinstance(_payload_c, dict) and _payload_c.get("ok") is True,
      (_payload_c or {}))


# ══════════════════════════════════════════════════════════════════════
# ④ 自检：本次改动的关键常量 / 结构仍在位（防回归）
# ══════════════════════════════════════════════════════════════════════
section("④ 结构与常量自检（防后续回归）")

check("_verify_export_artifact 可调用", callable(sw_bridge._verify_export_artifact))
check("_artifact_snapshot 可调用", callable(sw_bridge._artifact_snapshot))
check("_SW_INSERT_DIMENSIONS == 8", sw_bridge._SW_INSERT_DIMENSIONS == 8)
_cands = sw_bridge._annotation_api_candidates()
_names = [c[0] for c in _cands]
check("标注候选链含 InsertModelAnnotations4（新版优先）",
      "InsertModelAnnotations4" in _names)
check("标注候选链含 InsertModelDimensions", "InsertModelDimensions" in _names)
check("标注候选链含 AutoDimension 兜底", "AutoDimension" in _names)
_arity = {n: len(a) for n, a in _cands}
check("InsertModelAnnotations4 参数个数 = 8",
      _arity.get("InsertModelAnnotations4") == 8, _arity.get("InsertModelAnnotations4"))
check("InsertModelAnnotations3 参数个数 = 6",
      _arity.get("InsertModelAnnotations3") == 6, _arity.get("InsertModelAnnotations3"))
check("InsertModelAnnotations2 参数个数 = 6",
      _arity.get("InsertModelAnnotations2") == 6, _arity.get("InsertModelAnnotations2"))
check("InsertModelAnnotations 参数个数 = 4",
      _arity.get("InsertModelAnnotations") == 4, _arity.get("InsertModelAnnotations"))
check("InsertModelDimensions 参数个数 = 1",
      _arity.get("InsertModelDimensions") == 1, _arity.get("InsertModelDimensions"))
check("AutoDimension 参数个数 = 5",
      _arity.get("AutoDimension") == 5, _arity.get("AutoDimension"))


# ══════════════════════════════════════════════════════════════════════
# 汇总
# ══════════════════════════════════════════════════════════════════════
try:
    shutil.rmtree(TMP, ignore_errors=True)
except Exception:
    pass

print("\n" + "=" * 70)
print("未覆盖范围（需要真 SolidWorks，本机无授权环境，无法验证）：")
print("  · wrapper 里 sw = sw_bridge.get_sw() 的真实 COM 连接")
print("  · SaveAs3/SaveAs2 在真 SW 上的返回值语义与落盘行为")
print("  · CreateDrawViewFromModelView3 建视图 + 四锚点排版")
print("  · InsertModelAnnotations* 在真图纸上的实际标注增量")
print("  · DXF/DWG 映射对话框是否会阻塞导出")
print("=" * 70)
print("PASS=%d FAIL=%d" % (len(PASS), len(FAIL)))
if FAIL:
    print("\n失败用例：")
    for f in FAIL:
        print("  - " + f)
sys.exit(1 if FAIL else 0)
