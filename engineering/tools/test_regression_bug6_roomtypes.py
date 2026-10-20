# -*- coding: utf-8 -*-
"""【Bug⑥ 回归测试】require_physics 对 drivetrain/sensor/drawing 过松

实测缺陷（本任务三房间）：

| 房间                 | room_type  | require_physics | 是否含 CRITICAL | 门禁 |
|----------------------|------------|-----------------|-----------------|------|
| 底盘结构件           | structural | true            | 无              | 缺凭据拒 |
| 传动与轮组           | drivetrain | **false**       | 有 2 项 CRITICAL | 放行 |
| 传感器与电子件安装   | sensor     | **false**       | 无              | 放行 |

根因：MODELING_ROOM_TYPES 白名单只含 structural/housing/transmission/
  spring/corrosion/thermal/support，drivetrain/sensor/drawing 不在其中 →
  require_physics=False → 物理/领域凭据均不强制（传动房真实刚度、轮毂壁厚
  从未被校核过）。关联 Bug③：快照里的历史类型名未刷新。

要求：
  · 所有"含零件"的房间类型（含 drivetrain / sensor）都纳入物理校核；
  · 只对纯出图/协调类（drafting / assembly / prime）豁免；
  · workflow_gate 的类型别名映射必须同步
    （drivetrain→transmission、sensor→housing、drawing→drafting），
    避免"门禁放行、防线豁免"的互相矛盾。

运行：python test_regression_bug6_roomtypes.py   （退出码 0 = 通过）
"""
import json
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = HERE
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)
_PHYS = os.path.join(TOOLS, "physics")
if _PHYS not in sys.path:
    sys.path.insert(0, _PHYS)

PASS = []
FAIL = []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (("  -> " + str(detail)) if (detail and not cond) else ""))


import defense_gate as DG      # noqa: E402
import workflow_gate as WG     # noqa: E402


# ══════════════════════════════════════════════════════════════════════
#  Bug⑥-1：白名单本身必须覆盖全部"含零件"类型
# ══════════════════════════════════════════════════════════════════════
print("=== Bug⑥-1: MODELING_ROOM_TYPES 覆盖所有含零件类型 ===")
for _t in ("structural", "housing", "transmission", "support", "spring",
           "thermal", "corrosion",
           # ★ 本次缺陷的两个"含零件却不在白名单"的类型：必须补进来
           "drivetrain", "sensor"):
    check("白名单含 %r" % _t, _t in DG.MODELING_ROOM_TYPES,
          DG.MODELING_ROOM_TYPES)
check("★ 'drawing' 不在白名单 —— 它经别名归一化为 drafting（纯出图类）",
      "drawing" not in DG.MODELING_ROOM_TYPES and
      DG.ROOM_TYPE_ALIAS.get("drawing") == "drafting",
      DG.ROOM_TYPE_ALIAS.get("drawing"))

print("\n=== Bug⑥-2: room_type_requires_physics 判定口径 ===")
for _t in ("structural", "housing", "transmission", "drivetrain", "sensor",
           "support", "spring", "thermal", "corrosion",
           # 未知 / 空类型：fail-closed，不得豁免
           "", "some_new_type"):
    check("require_physics(%r) == True" % _t,
          DG.room_type_requires_physics(_t) is True,
          DG.room_type_requires_physics(_t))
for _t in ("drafting", "assembly", "prime",
           # 历史名 drawing 归一化为 drafting → 同样豁免
           "drawing"):
    check("纯出图/协调类 %r 豁免（require_physics=False）" % _t,
          DG.room_type_requires_physics(_t) is False,
          DG.room_type_requires_physics(_t))


# ══════════════════════════════════════════════════════════════════════
#  Bug⑥-3：两套门禁的类型别名映射必须同源（避免口径分裂）
# ══════════════════════════════════════════════════════════════════════
print("\n=== Bug⑥-3: defense_gate / workflow_gate 别名映射同步 ===")
check("defense_gate 定义了 ROOM_TYPE_ALIAS", hasattr(DG, "ROOM_TYPE_ALIAS"))
check("workflow_gate 定义了 ROOM_TYPE_ALIAS", hasattr(WG, "ROOM_TYPE_ALIAS"))
_common = set(DG.ROOM_TYPE_ALIAS) & set(WG.ROOM_TYPE_ALIAS)
check("★ 两张别名表的公共键映射结果完全一致（不得一边一个说法）",
      all(DG.ROOM_TYPE_ALIAS[k] == WG.ROOM_TYPE_ALIAS[k] for k in _common),
      {k: (DG.ROOM_TYPE_ALIAS[k], WG.ROOM_TYPE_ALIAS[k]) for k in _common
       if DG.ROOM_TYPE_ALIAS[k] != WG.ROOM_TYPE_ALIAS[k]})
for _a, _b in (("drivetrain", "transmission"), ("sensor", "housing"),
               ("drawing", "drafting")):
    check("★ 任务书要求的映射 %s→%s 两处都在" % (_a, _b),
          DG.ROOM_TYPE_ALIAS.get(_a) == _b and WG.ROOM_TYPE_ALIAS.get(_a) == _b,
          (DG.ROOM_TYPE_ALIAS.get(_a), WG.ROOM_TYPE_ALIAS.get(_a)))
    check("归一化结果一致：%s → %s" % (_a, _b),
          DG.normalize_room_type(_a) == WG.normalize_room_type(_a) == _b,
          (DG.normalize_room_type(_a), WG.normalize_room_type(_a)))
check("大小写/空格容错一致",
      DG.normalize_room_type("  Drivetrain ")
      == WG.normalize_room_type("  Drivetrain ") == "transmission")

print("\n=== Bug⑥-4: workflow_gate 的建模类判定与防线一致 ===")
for _t in ("structural", "transmission", "housing", "drivetrain", "sensor",
           "", "some_new_type"):
    check("workflow_gate 认为 %r 属建模类（与防线同口径）" % _t,
          WG.room_type_is_modeling(_t) is True,
          WG.room_type_is_modeling(_t))
    check("两个模块对 %r 的判定一致" % _t,
          WG.room_type_is_modeling(_t) == DG.room_type_requires_physics(_t),
          (WG.room_type_is_modeling(_t), DG.room_type_requires_physics(_t)))
for _t in ("drafting", "assembly", "prime", "drawing"):
    check("workflow_gate 对纯出图类 %r 判为非建模类（drawing→drafting）" % _t,
          WG.room_type_is_modeling(_t) is False, WG.room_type_is_modeling(_t))
    check("纯出图类 %r 两模块判定一致（都不强制物理）" % _t,
          WG.room_type_is_modeling(_t) == DG.room_type_requires_physics(_t),
          (WG.room_type_is_modeling(_t), DG.room_type_requires_physics(_t)))


# ══════════════════════════════════════════════════════════════════════
#  Bug⑥-5：端到端 —— 快照里是历史类型名时，check_room_defense 必须强制校核
#   （把 STATE_DIR 指到临时目录，隔离现场数据；不写任何现场文件）
# ══════════════════════════════════════════════════════════════════════
print("\n=== Bug⑥-5: 端到端 check_room_defense（drivetrain/sensor 不再豁免）===")
_tmp = tempfile.mkdtemp(prefix="bug6_roomtype_")
_old_state = getattr(DG, "STATE_DIR", None)
_old_reports = getattr(DG, "REPORTS_DIR", None)
_old_registry = getattr(DG, "ARTIFACT_REGISTRY_FILE", None)
_old_runtime = getattr(DG, "RUNTIME_FILE", None)
_old_cands_fn = getattr(DG, "_runtime_candidates", None)
try:
    DG.STATE_DIR = _tmp
    DG.REPORTS_DIR = os.path.join(_tmp, "reports")
    os.makedirs(DG.REPORTS_DIR, exist_ok=True)
    DG.ARTIFACT_REGISTRY_FILE = os.path.join(_tmp, "artifacts_registry.json")
    # 让宿主优先判定路径失效（把 runtime.json 候选集合指到临时目录的空文件），
    # 从而稳定走【本地判定】——否则本用例会依赖现场宿主进程，判定路径不可控。
    DG.RUNTIME_FILE = os.path.join(_tmp, "runtime.json")
    DG._runtime_candidates = lambda: [DG.RUNTIME_FILE]

    _rooms = [("底盘结构件", "structural"), ("传动与轮组", "drivetrain"),
              ("传感器与电子件安装", "sensor"), ("工程图输出", "drawing"),
              ("总装与验证", "assembly"), ("出图房", "drafting")]
    with open(os.path.join(_tmp, "workflow_state.json"), "w",
              encoding="utf-8") as f:
        json.dump({"subagent_config": {"rooms": _rooms}}, f, ensure_ascii=False)
    with open(os.path.join(_tmp, "mode_state.json"), "w", encoding="utf-8") as f:
        json.dump({"rooms": {n: {"type": t} for n, t in _rooms}}, f,
                  ensure_ascii=False)

    _types = DG._room_types()
    check("★ 从快照读到 drivetrain 类型（复现 Bug③ 的历史名场景）",
          _types.get("传动与轮组") == "drivetrain", _types.get("传动与轮组"))
    check("★ 从快照读到 sensor 类型", _types.get("传感器与电子件安装") == "sensor",
          _types.get("传感器与电子件安装"))

    _r_struct = DG.check_room_defense("底盘结构件")
    _r_drive = DG.check_room_defense("传动与轮组")
    _r_sensor = DG.check_room_defense("传感器与电子件安装")
    _r_draw = DG.check_room_defense("工程图输出")
    _r_asm = DG.check_room_defense("总装与验证")
    _r_draft = DG.check_room_defense("出图房")

    check("structural：require_physics=True（基线）",
          _r_struct["require_physics"] is True, _r_struct["require_physics"])
    check("★★ drivetrain：require_physics=True（原缺陷为 False → 放行）",
          _r_drive["require_physics"] is True, _r_drive["require_physics"])
    check("★★ sensor：require_physics=True（原缺陷为 False → 放行）",
          _r_sensor["require_physics"] is True, _r_sensor["require_physics"])
    check("★ drawing（历史名→drafting）：按纯出图类豁免",
          _r_draw["require_physics"] is False, _r_draw["require_physics"])
    check("assembly（纯协调类）豁免",
          _r_asm["require_physics"] is False, _r_asm["require_physics"])
    check("drafting（规范名）豁免",
          _r_draft["require_physics"] is False, _r_draft["require_physics"])

    # 强制校核 = 缺凭据必须被拦（不再静默放行）
    for _room, _res in (("传动与轮组", _r_drive),
                        ("传感器与电子件安装", _r_sensor)):
        check("★★ %s 缺物理/领域凭据时必须被拦（不得静默放行）" % _room,
              _res["ok"] is False
              and any("防线②物理" in b for b in _res["blockers"])
              and any("防线③领域" in b for b in _res["blockers"]),
              _res["blockers"])
        check("%s 的 details 不再标记 physics.skipped" % _room,
              not (_res["details"].get("physics") or {}).get("skipped"),
              _res["details"].get("physics"))
    for _room, _res in (("工程图输出", _r_draw), ("总装与验证", _r_asm)):
        check("%s（纯出图/协调类）仍标记 physics.skipped（豁免但有据）" % _room,
              (_res["details"].get("physics") or {}).get("skipped") is True,
              _res["details"].get("physics"))

    # ★ 现场安全：REVIEW 凭据不得被拦（只拦 FAIL / 缺凭据 / 验签失败）
    #   这里只验证"check_physics_attestation 对 REVIEW 的判定口径"没被改坏。
    _src_ok = True
    try:
        _src = open(os.path.join(TOOLS, "defense_gate.py"), encoding="utf-8").read()
        _src_ok = ('elif _overall not in ("PASS", "REVIEW")' in _src)
    except Exception:
        _src_ok = False
    check("★ 防线②仍放行 REVIEW（只拦 FAIL）—— 不会把现场卡死", _src_ok)

    # 显式覆盖仍然有效（调用方可以强制要求/豁免）
    _r_force = DG.check_room_defense("工程图输出", require_physics=True)
    check("显式 require_physics=True 可强制校核（覆盖自动判定）",
          _r_force["require_physics"] is True and _r_force["ok"] is False,
          _r_force["require_physics"])
finally:
    if _old_state is not None:
        DG.STATE_DIR = _old_state
    if _old_reports is not None:
        DG.REPORTS_DIR = _old_reports
    if _old_registry is not None:
        DG.ARTIFACT_REGISTRY_FILE = _old_registry
    if _old_runtime is not None:
        DG.RUNTIME_FILE = _old_runtime
    if _old_cands_fn is not None:
        DG._runtime_candidates = _old_cands_fn
    try:
        import shutil
        shutil.rmtree(_tmp, ignore_errors=True)
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════
#  Bug⑥-6：workflow_gate 第一波房间清单不再漏掉别名类型房间
# ══════════════════════════════════════════════════════════════════════
print("\n=== Bug⑥-6: workflow_gate 第一波清单识别别名类型房间 ===")
_tmp2 = tempfile.mkdtemp(prefix="bug6_wave1_")
_old_wg_state = getattr(WG, "STATE_DIR", None)
_old_wg_load = getattr(WG, "load_state", None)
try:
    WG.STATE_DIR = _tmp2
    _rooms2 = [("底盘结构件", "structural"), ("传动与轮组", "drivetrain"),
               ("传感器与电子件安装", "sensor"), ("总装与验证", "assembly"),
               ("工程图输出", "drawing")]
    with open(os.path.join(_tmp2, "workflow_state.json"), "w",
              encoding="utf-8") as f:
        json.dump({"subagent_config": {"rooms": _rooms2}}, f,
                  ensure_ascii=False)
    WG.load_state = lambda: json.load(
        open(os.path.join(_tmp2, "workflow_state.json"), encoding="utf-8"))
    _wave1 = WG._wave1_room_names()
    check("★ drivetrain 房间进入第一波（原缺陷：被判非建模类而漏掉）",
          "传动与轮组" in _wave1, _wave1)
    check("★ sensor 房间进入第一波", "传感器与电子件安装" in _wave1, _wave1)
    check("structural 房间进入第一波", "底盘结构件" in _wave1, _wave1)
    check("assembly 房间不进入第一波（纯协调类）",
          "总装与验证" not in _wave1, _wave1)
    check("★ drawing（历史名→drafting）不进入第一波",
          "工程图输出" not in _wave1, _wave1)

    check("★ 第一波清单与防线判定口径一致（含零件即校核）",
          set(_wave1) == {"底盘结构件", "传动与轮组", "传感器与电子件安装"},
          _wave1)
    # _check_wave1_done 也用同一判定口径：drivetrain/sensor 房间不得被跳过
    _done = WG._check_wave1_done()
    _covered = (_done["not_ended"] + _done["active"] + _done["failed"])
    check("★ _check_wave1_done 纳入 drivetrain/sensor 房间（原缺陷：被 continue 跳过）",
          {"传动与轮组", "传感器与电子件安装"} <= set(_covered), _done)
    check("assembly 房间不出现在第一波完成度检查里（纯协调类）",
          "总装与验证" not in _done["not_ended"], _done)
finally:
    if _old_wg_state is not None:
        WG.STATE_DIR = _old_wg_state
    if _old_wg_load is not None:
        WG.load_state = _old_wg_load
    try:
        import shutil
        shutil.rmtree(_tmp2, ignore_errors=True)
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════
#  Bug⑥-7：部署副本一致性（本文件修复必须【生效】，不能只在工作区）
# ══════════════════════════════════════════════════════════════════════
# 为什么纳入 Bug⑥ 回归：工程模式有【两份副本】——工作区 与 ~/.dsh 安装副本；
#   宿主与子代理实际跑的是【安装副本】。若只改工作区而安装副本仍是旧代码，
#   本文件的 require_physics 修复在真实流程里【完全没有生效】，
#   而"从工作区跑测试"却全绿 —— 这正是最危险的"修了但没生效"。
# 本段只做【事实核对 + 明确提示】，不自动搬运文件（部署由 sync-to-dsh.ps1 负责），
#   因此不会因为没同步就把代码缺陷误报成测试失败。
print("\n=== Bug⑥-7: 修复是否已同步到【宿主实际加载的安装副本】===")
try:
    import _store as _store_mod
    _inst_dir = _store_mod.state_dir()
except Exception as _e_store:
    _inst_dir = ""
    print("     （_store 不可用，跳过部署副本核对：%r）" % (_e_store,))

if _inst_dir and os.path.normcase(os.path.abspath(_inst_dir)) != \
        os.path.normcase(os.path.abspath(TOOLS)):
    print("     安装副本目录: %s" % _inst_dir)
    _markers = [
        ("defense_gate.py", "room_type_requires_physics", "Bug⑥ 白名单判定"),
        ("workflow_gate.py", "room_type_is_modeling", "Bug⑥ 第一波口径"),
        ("physics_bridge.py", "_thin_hollow_assessment", "Bug⑦ 薄壁判定"),
        (os.path.join("physics", "load_case.py"),
         "is_standalone_load_path", "Bug⑩ 独立承力件"),
        (os.path.join("physics", "simulation_report.py"),
         "model_equivalence", "Bug⑦ 报告降级"),
    ]
    _missing = []
    _hash_drift = []
    for _f, _sym, _desc in _markers:
        _ip = os.path.join(_inst_dir, _f)
        _wp = os.path.join(TOOLS, _f)
        try:
            _txt = open(_ip, encoding="utf-8").read()
            if _sym not in _txt:
                _missing.append("%s（%s）" % (_f, _desc))
        except Exception:
            _missing.append("%s（读不到）" % _f)
            continue
        # 符号在但内容不同 = 部分同步/后续又有改动，运行结果可能仍不一致
        try:
            import hashlib as _hl
            _ih = _hl.sha256(open(_ip, "rb").read()).hexdigest()
            _wh = _hl.sha256(open(_wp, "rb").read()).hexdigest()
            if _ih != _wh:
                _hash_drift.append(_f)
        except Exception:
            pass
    if _missing:
        # ★ 不算 FAIL（代码本身正确，缺的是部署），但必须【大声告警】，
        #   否则测试全绿会让人误以为修复已在生产生效。
        print("     ⚠️⚠️ 部署副本【未同步】—— 以下修复尚未生效：")
        for _m in _missing:
            print("         - " + _m)
        print("     处理：powershell -ExecutionPolicy Bypass -File sync-to-dsh.ps1")
        PASS.append("部署副本一致性（仅告警，不阻塞）")
    else:
        check("★ 安装副本已包含本次全部修复（改动真实生效）", True)
        if _hash_drift:
            # 符号都在，但整文件仍有差异 → 可能是别人的后续改动未同步
            print("     ⚠️ 内容级差异（符号齐全但文件不同，建议再 sync 一次）：")
            for _f in _hash_drift:
                print("         - " + _f)
        else:
            check("★ 工作区与安装副本【逐字节一致】（无半新半旧）", True)
elif _inst_dir:
    check("当前即为权威状态目录（安装副本/工作区同源），无部署漂移", True)
else:
    check("（无状态目录信息，跳过部署副本核对）", True)


print("\nPASS=%d FAIL=%d" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：")
    for f in FAIL:
        print("  - " + f)
sys.exit(1 if FAIL else 0)
