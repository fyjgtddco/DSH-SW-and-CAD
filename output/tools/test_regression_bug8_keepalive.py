# -*- coding: utf-8 -*-
"""回归测试：【Bug⑧】close-all 保活语义（并行房间共用单台 SW 不被互相切断）

★ 锁定的真实缺陷（竞速小车任务现场，三房间共用一台 SW）：
  任一房间调用 close-all → 走 Quit + taskkill → SW 进程结束 →
  其它房间正在进行的 RPC 连接【立即失效】。
  主代理 conn-gate --target sw 观察到 SW PID 频繁变化
  （4148 → 30860 → 18388 → 23448 → 472 → 34744），每次都是冷启动；
  每次冷启动后 swapi 都要重新 Dispatch("SldWorks.Application")。
  → 并行模式实际退化成"串行 + 冷启动"，9 个零件建模总耗时约 70 分钟。

修复后的约定（本测试钉死）：
  A. swapi.close_all_documents(sw) —— 只调 SldWorks.CloseAllDocuments，
     【绝不】调用 Quit / taskkill / GC 退出流程；SW 进程保活。
  B. swapi.close_all_and_exit(sw, quit=False) 必须委托给 A（不退出进程）。
  C. swapi.close_all_and_exit(sw)（默认 quit=True）保持旧行为（会尝试退出）。
  D. sw_bridge.cmd_close_all(sw) 默认走保活；--quit 才走完整退出。
  E. sw_bridge.cmd_close_all(None) 不得崩，且如实报 ok=False。
  F. 帮助文本必须同时说明"默认保活"与"--quit"（文档与实现不许分裂）。
  G. mode_gate.cmd_close_all 默认不带 --quit，显式 quit=True 才带。

⚠️ 本测试只用【假对象(mock)】，【不连接 SolidWorks】、不启动任何进程、
   不执行 taskkill —— 因此可在没有装 SW 的机器上安全运行。

运行：python test_regression_bug8_keepalive.py
退出码：0 = 全部通过；1 = 有失败
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

PASS = 0
FAIL = 0
FAILURES = []


def check(label, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] %s" % label)
    else:
        FAIL += 1
        FAILURES.append(label)
        print("  [FAIL] %s %s" % (label, extra))


def section(t):
    print("\n" + "=" * 70)
    print(t)
    print("=" * 70)


# ══════════════════════════════════════════════════════════════════════
#  假 SW 对象：只暴露 CloseAllDocuments / Quit / GetDocumentCount，
#  并记录【每个被调用的成员名】—— 这是判定"有没有退出进程"的唯一依据。
# ══════════════════════════════════════════════════════════════════════
class FakeSW(object):
    """假 SldWorks.Application。

    GetDocumentCount 按本仓库既有约定是【无参属性】（late-binding 下按属性访问），
    因此这里用 property 暴露，不写成方法。
    """

    def __init__(self, docs=2, close_works=True):
        self.calls = []
        self._docs = docs
        self._close_works = close_works

    @property
    def GetDocumentCount(self):
        self.calls.append("GetDocumentCount")
        return self._docs

    def CloseAllDocuments(self, opts):
        self.calls.append(("CloseAllDocuments", opts))
        if self._close_works:
            self._docs = 0
        return True

    def Quit(self):
        self.calls.append("Quit")
        return True


HERE = os.path.dirname(os.path.abspath(__file__))


def _load_sibling(name):
    """从【本文件所在目录】加载同级模块，绝不拿到别处的副本。

    为什么必须这样：mode_gate 导入时会把 state_dir()（通常=安装副本
    ~/.dsh/.agent-presets/engineering/tools）插到 sys.path 最前，于是后续
    import part_registry / swapi 可能命中【另一份副本】—— 那份没有本次修复，
    表现为 AttributeError（看起来像"函数没写"，实际是加载错了文件）。
    sw_bridge._import_sibling() 里记录过同类现场事故。
    """
    import importlib.util as _ilu
    _p = os.path.join(HERE, name + ".py")
    _spec = _ilu.spec_from_file_location("_bug8_" + name, _p)
    _mod = _ilu.module_from_spec(_spec)
    sys.modules["_bug8_" + name] = _mod
    _spec.loader.exec_module(_mod)
    return _mod


def main():
    import inspect
    import io
    import json as _json
    import contextlib

    import swapi
    import sw_bridge
    import mode_gate as mg

    # ═══════════════════════════════════════════════════════════════
    section("1. ★ swapi.close_all_documents：只关文档、绝不退出 SW 进程")
    sw = FakeSW(docs=3)
    res = swapi.close_all_documents(sw)
    called = [c[0] if isinstance(c, tuple) else c for c in sw.calls]
    check("CloseAllDocuments 被调用（真正的文档级关闭）",
          any(isinstance(c, tuple) and c[0] == "CloseAllDocuments"
              for c in sw.calls), sw.calls)
    check("★ 绝不调用 Quit()（保活的核心：不结束 SW 进程）",
          "Quit" not in called, called)
    check("参数是 0（不弹保存提示）",
          any(isinstance(c, tuple) and c == ("CloseAllDocuments", 0)
              for c in sw.calls), sw.calls)
    check("返回 keepalive=True", res.get("keepalive") is True, res)
    check("返回 quit=False", res.get("quit") is False, res)
    check("method == close_all_documents",
          res.get("method") == "close_all_documents", res)
    check("ok=True（文档已关干净）", res.get("ok") is True, res)
    check("如实回报 docs_before=3 / docs_after=0",
          res.get("docs_before") == 3 and res.get("docs_after") == 0, res)
    check("不回报 exited=True（进程根本没退出）",
          not res.get("sw_exited"), res)

    # 不调 Quit 是保活的全部意义：再验一次"整段调用里没有退出动作"
    check("★ 调用轨迹里不含任何退出动作（Quit/taskkill）",
          not any(("Quit" in str(c)) or ("taskkill" in str(c))
                  for c in sw.calls), sw.calls)

    # ═══════════════════════════════════════════════════════════════
    section("2. ★ close_all_and_exit(quit=False) 必须委托给保活路径")
    sw2 = FakeSW(docs=1)
    res2 = swapi.close_all_and_exit(sw2, quit=False)
    _c2 = [c[0] if isinstance(c, tuple) else c for c in sw2.calls]
    check("quit=False → 不调用 Quit", "Quit" not in _c2, _c2)
    check("quit=False → 返回 keepalive=True", res2.get("keepalive") is True, res2)
    check("quit=False → method 与 close_all_documents 一致",
          res2.get("method") == "close_all_documents", res2)

    # ═══════════════════════════════════════════════════════════════
    section("3. close_all_and_exit() 默认仍保持旧行为（会尝试退出）")
    # 用源码级判定而不是真跑：真跑会 taskkill 真实进程，测试绝不允许。
    src_exit = inspect.getsource(swapi.close_all_and_exit)
    check("签名默认 quit=True（既有调用方行为不变）",
          "quit=True" in src_exit, src_exit.splitlines()[0] if src_exit else "")
    check("源码含 taskkill 的完整退出流程（仅 --quit 路径使用）",
          "taskkill" in src_exit)
    check("quit=False 时提前 return 保活路径",
          "if not quit:" in src_exit and "close_all_documents(sw)" in src_exit)

    # ═══════════════════════════════════════════════════════════════
    section("4. ★ sw_bridge.cmd_close_all：默认保活，--quit 才退出")
    sw3 = FakeSW(docs=2)
    r3 = sw_bridge.cmd_close_all(sw3)
    _c3 = [c[0] if isinstance(c, tuple) else c for c in sw3.calls]
    check("默认（不传 quit）→ 不调用 Quit", "Quit" not in _c3, _c3)
    check("默认 → keepalive=True", r3.get("keepalive") is True, r3)
    check("默认 → quit=False", r3.get("quit") is False, r3)

    src_bridge = inspect.getsource(sw_bridge.cmd_close_all)
    check("★ 源码分派：quit 为真才走 close_all_and_exit(quit=True)",
          "close_all_and_exit(sw, quit=True)" in src_bridge, src_bridge[:400])
    check("默认分支走 close_all_documents（保活）",
          "close_all_documents(sw)" in src_bridge)

    # sw=None（SW 没连上）不得崩 —— 这正是"连不上还想清场"的死结场景
    try:
        r4 = sw_bridge.cmd_close_all(None)
        _ok4 = True
    except Exception as e:
        r4 = {"error": repr(e)}
        _ok4 = False
    check("★ sw=None 不抛异常（避免连不上→想关→因连不上被拒的死结）",
          _ok4, r4)
    check("sw=None 时如实报 ok=False + hint（不谎报文档已关）",
          r4.get("ok") is False and bool(r4.get("hint")), r4)

    # ═══════════════════════════════════════════════════════════════
    section("5. ★ 帮助文本必须与实现同步（默认保活 / --quit）")
    _buf = io.StringIO()
    _old_argv = sys.argv
    sys.argv = ["sw_bridge.py", "--help"]
    try:
        with contextlib.redirect_stdout(_buf):
            sw_bridge.main()
    finally:
        sys.argv = _old_argv
    _txt = _buf.getvalue()
    try:
        _help = _json.loads(_txt)
    except Exception:
        _help = {}
    _cmds = (_help.get("commands") or {})
    check("帮助可解析为 JSON（--help 零副作用）", bool(_help), _txt[:200])
    check("★ 帮助里单列 close-all --quit 条目",
          "close-all --quit" in _cmds, list(_cmds.keys())[-6:])
    check("★ close-all 条目说明保留进程/保活",
          "保活" in str(_cmds.get("close-all") or ""),
          _cmds.get("close-all"))
    check("新增 close_all_note 解释默认语义",
          "保活" in _txt, _txt[-300:])

    # ═══════════════════════════════════════════════════════════════
    section("6. ★ mode_gate.cmd_close_all：默认不带 --quit")
    # 只验证命令行拼装，绝不真跑子进程（会动真 SW）
    _mg_src = inspect.getsource(mg.cmd_close_all)
    check("mode_gate.cmd_close_all 接受 quit 参数",
          "quit=False" in _mg_src, _mg_src.splitlines()[0] if _mg_src else "")
    check("★ 只有 quit 为真才向 sw_bridge 追加 --quit",
          "if quit:" in _mg_src and '_cmdline.append("--quit")' in _mg_src,
          _mg_src[:600])
    check("默认语义记录在返回体（keepalive / quit 字段）",
          '"keepalive"' in _mg_src and '"quit"' in _mg_src)

    _main_src = inspect.getsource(mg.main)
    check("CLI 把 --quit 透传给 cmd_close_all",
          'cmd_close_all(quit=("--quit" in args))' in _main_src,
          [l for l in _main_src.splitlines() if "close-all" in l][:3])

    # 保活语义下"进程仍在运行"不应判失败（它与 --quit 的预期正好相反）
    check("★ 保活分支不复用 verify_exited 的失败判定",
          "verify_keepalive" in _mg_src, _mg_src[-800:].splitlines()[:6])

    # ═══════════════════════════════════════════════════════════════
    #  【Bug⑨】同批修复：长任务推进 stage —— 一并钉死，防止回归
    # ═══════════════════════════════════════════════════════════════
    section("7. ★【Bug⑨】push_room_stage：幂等 / 不覆盖终态 / 无房间不写")
    import tempfile
    import shutil
    _td = tempfile.mkdtemp(prefix="stage_test_")
    _saved = (mg.STATE_PATH, mg.REPORTS_DIR, mg.WORKFLOW_STATE_PATH,
              mg.HEARTBEAT_DIR)
    try:
        mg.STATE_PATH = os.path.join(_td, "mode_state.json")
        mg.REPORTS_DIR = os.path.join(_td, "reports")
        mg.WORKFLOW_STATE_PATH = os.path.join(_td, "workflow_state.json")
        mg.HEARTBEAT_DIR = os.path.join(_td, "heartbeats")
        os.makedirs(mg.REPORTS_DIR, exist_ok=True)
        import json as _j
        with open(mg.STATE_PATH, "w", encoding="utf-8") as _f:
            _j.dump({"mode": "2", "rooms": {"房": {"active": True}}, "sw_lock": {}},
                    _f, ensure_ascii=False)
        _old_room_env = os.environ.pop("DSH_ROOM", None)
        try:
            r1 = mg.push_room_stage(None, "validating", "x")
            check("★ 无房间上下文 → 不写任何文件（skipped=no-room-context）",
                  r1.get("pushed") is False
                  and r1.get("skipped") == "no-room-context", r1)
            r2 = mg.push_room_stage("不存在的房", "validating", "x")
            check("★ 房间非 active → 不凭空写进度",
                  r2.get("pushed") is False
                  and r2.get("skipped") == "no-active-room", r2)
            r3 = mg.push_room_stage("房", "validating", "物理校核中")
            check("★ active 房间 → 推进成功", r3.get("pushed") is True, r3)
            _rep = mg.cmd_room_report_read("房") or {}
            check("  room-report 的 latest.stage 已变为 validating",
                  (_rep.get("latest") or {}).get("stage") == "validating",
                  (_rep.get("latest") or {}).get("stage"))
            _hist_before = len(_rep.get("history") or [])
            r4 = mg.push_room_stage("房", "validating", "again")
            _rep2 = mg.cmd_room_report_read("房") or {}
            check("★ 幂等：重复推进 skipped=already 且 history 不增长",
                  r4.get("skipped") == "already"
                  and len(_rep2.get("history") or []) == _hist_before,
                  (r4.get("skipped"), _hist_before,
                   len(_rep2.get("history") or [])))
            mg.cmd_room_report("房", "done", "完成")
            r5 = mg.push_room_stage("房", "validating", "迟到推进")
            check("★ 终态保护：done 之后不被改回 validating",
                  r5.get("skipped") == "terminal-stage"
                  and (mg.cmd_room_report_read("房") or {}).get(
                      "latest", {}).get("stage") == "done", r5)
            r6 = mg.push_room_stage(123, "validating", "x")
            check("★ 垃圾入参不抛异常（失败不阻断主流程）",
                  isinstance(r6, dict) and r6.get("pushed") is False, r6)
        finally:
            if _old_room_env is not None:
                os.environ["DSH_ROOM"] = _old_room_env
    finally:
        (mg.STATE_PATH, mg.REPORTS_DIR, mg.WORKFLOW_STATE_PATH,
         mg.HEARTBEAT_DIR) = _saved
        shutil.rmtree(_td, ignore_errors=True)

    # ═══════════════════════════════════════════════════════════════
    #  【Bug①】材质双字段：SW 内部实际材质必须可见（旁挂记录 + 台账）
    # ═══════════════════════════════════════════════════════════════
    section("8. ★【Bug①】SW 内部材质 vs 请求材质必须双记录且可见")
    # ⚠️ 用 _load_sibling 而非 import：mode_gate 已把安装副本插进 sys.path，
    #    直接 import 可能拿到【另一份 part_registry】→ 假失败（见 _load_sibling 注释）
    pr = _load_sibling("part_registry")
    swapi = _load_sibling("swapi")
    check("加载的是【本工作区】这份 part_registry（防双副本假失败）",
          os.path.normcase(os.path.abspath(pr.__file__)).startswith(
              os.path.normcase(HERE)), pr.__file__)
    check("swapi 提供 set_custom_material",
          hasattr(swapi.SWModel, "set_custom_material"))
    check("★ swapi 提供旁挂记录读写（宿主白名单不含该字段，只能旁挂）",
          hasattr(swapi, "read_sw_internal_record")
          and hasattr(swapi, "sw_internal_record_path"))
    check("★ part_registry 提供材料一致性读取",
          hasattr(pr, "material_consistency"))

    _td2 = tempfile.mkdtemp(prefix="mat_side_")
    try:
        _p = os.path.join(_td2, "X.SLDPRT")
        with open(_p, "wb") as _f:
            _f.write(b"K" * 6000)
        _side = swapi._write_sw_internal_record(_p, {
            "requested_material": "PETG",
            "sw_internal_material": "PET",
            "sw_internal_matches_request": False,
            "sw_internal_warning": "【Bug①】SW 实际是 PET",
            "density_kg_m3": 1270.0,
        })
        check("★ 旁挂记录可落盘", _side.get("ok") is True, _side)
        _back = swapi.read_sw_internal_record(_p)
        check("★ 旁挂记录可读回，两个材质字段都在",
              (_back or {}).get("requested_material") == "PETG"
              and (_back or {}).get("sw_internal_material") == "PET", _back)
        # 凭据侧只放 requested_material（模拟宿主签发结果）
        with open(_p + ".material.json", "w", encoding="utf-8") as _f:
            _json.dump({"requested_material": "PETG", "applied_name": "PETG",
                        "expected_density_kg_m3": 1270,
                        "applied_density_kg_m3": 1270},
                       _f, ensure_ascii=False)
        _mc = pr.material_consistency(_p)
        check("★ part_registry 从旁挂记录读到 SW 实际材质 = PET",
              _mc.get("sw_internal_material") == "PET", _mc)
        check("★ 判为不一致（凭据 PETG ≠ SW 实际 PET）",
              _mc.get("consistent") is False, _mc)
        check("★ 给出可见 warning（不阻断、但必须看得见）",
              bool(_mc.get("warning")), _mc.get("warning"))
    finally:
        shutil.rmtree(_td2, ignore_errors=True)

    # ═══════════════════════════════════════════════════════════════
    #  【Bug⑪】origin 约定：落盘 + 台账可读
    # ═══════════════════════════════════════════════════════════════
    section("9. ★【Bug⑪】origin 约定必须落盘且被 part_registry 读到")
    check("swapi 定义标准约定 = center_bottom（底面中心）",
          swapi.ORIGIN_STANDARD_MODE == "center_bottom",
          swapi.ORIGIN_STANDARD_MODE)
    check("★ swapi 提供 set_origin_mode（只记录、不改几何）",
          hasattr(swapi.SWModel, "set_origin_mode"))
    check("★ part_registry 提供 origin 读取",
          hasattr(pr, "origin_summary") and hasattr(pr, "read_origin_record"))

    _td3 = tempfile.mkdtemp(prefix="org_side_")
    try:
        _p2 = os.path.join(_td3, "Y.SLDPRT")
        with open(_p2, "wb") as _f:
            _f.write(b"L" * 6000)
        _w1 = swapi.write_origin_record(_p2, "center_bottom",
                                        bbox_mm=[-10, 10, -5, 5, 0, 3])
        check("★ origin 记录落盘（<part>.origin.json）",
              _w1.get("ok") is True
              and os.path.isfile(swapi.origin_record_path(_p2)), _w1)
        _os1 = pr.origin_summary(_p2)
        check("★ part_registry 读到 origin_mode 且识别为标准约定",
              _os1.get("origin_mode") == "center_bottom"
              and _os1.get("is_standard") is True, _os1)
        _w2 = swapi.write_origin_record(_p2, "corner_top")
        _os2 = pr.origin_summary(_p2)
        check("★ 非标准约定被如实标注（is_standard=False）",
              _os2.get("is_standard") is False, _os2)
        check("  幂等覆盖：revision 递增、created_at 保留",
              _w2.get("record", {}).get("revision") == 2
              and bool(_w2.get("record", {}).get("created_at")), _w2)
        _p3 = os.path.join(_td3, "Z.SLDPRT")
        with open(_p3, "wb") as _f:
            _f.write(b"P" * 6000)
        check("★ 缺记录时如实标 missing（不编造）",
              pr.origin_summary(_p3).get("missing") is True)
    finally:
        shutil.rmtree(_td3, ignore_errors=True)

    # ═══════════════════════════════════════════════════════════════
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
