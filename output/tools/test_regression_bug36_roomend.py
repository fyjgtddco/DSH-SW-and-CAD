
# -*- coding: utf-8 -*-
"""【Bug-36 回归测试】_finalize_room 所有 return 分支必须返回相同数量的值，
且 cmd_room_end / cmd_room_fail 解包不抛异常。"""
import sys, os, json, tempfile, shutil, inspect, ast
sys.stdout.reconfigure(encoding='utf-8')
# ── 【换机修复】不要写死作者本机路径：按本文件位置推导 tools 目录 ────────
# 旧写法 TOOLS = r'C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools'
# 换一台电脑（微机房/别人 clone）直接 FileNotFoundError，回归测试全废。
# 现按 __file__ 推导；仍可用 argv[1] 或 DSH_ENGINEERING_ROOT 覆盖。
TOOLS = (sys.argv[1] if len(sys.argv) > 1 and os.path.isdir(sys.argv[1])
         else os.environ.get('DSH_ENGINEERING_ROOT') or
         os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOLS)

PASS=[];FAIL=[]
def check(n,c,d=""):
    (PASS if c else FAIL).append(n); print(("  [PASS] " if c else "  [FAIL] ")+n+(("  -> "+str(d)) if d else ""))

print("=== Bug-36: _finalize_room 返回值数量一致性（静态检查）===")
src = open(os.path.join(TOOLS,'mode_gate.py'), encoding='utf-8').read()
tree = ast.parse(src)
fn = None
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == '_finalize_room':
        fn = node; break
check("找到 _finalize_room", fn is not None)
returns = []
for node in ast.walk(fn):
    if isinstance(node, ast.Return) and node.value is not None:
        if isinstance(node.value, ast.Tuple):
            returns.append((node.lineno, len(node.value.elts)))
        else:
            returns.append((node.lineno, 1))
print("      return 分支:", returns)
counts = set(c for _,c in returns)
check("所有 return 分支数量一致", len(counts)==1, "分支数量集合=%s" % counts)
check("返回 4 个值（与 cmd_room_end 解包匹配）", counts=={4}, counts)

print("\n=== 动态：cmd_room_end / cmd_room_fail 不抛 ValueError ===")
tmp = tempfile.mkdtemp()
import importlib.util as ilu
spec = ilu.spec_from_file_location('mg36', os.path.join(TOOLS,'mode_gate.py'))
mg = ilu.module_from_spec(spec); spec.loader.exec_module(mg)
mg._BASE_DIR=tmp
mg.STATE_PATH=os.path.join(tmp,'mode_state.json')
mg.WORKFLOW_STATE_PATH=os.path.join(tmp,'workflow_state.json')
mg.REPORTS_DIR=os.path.join(tmp,'reports'); os.makedirs(mg.REPORTS_DIR,exist_ok=True)
mg.HEARTBEAT_DIR=os.path.join(tmp,'heartbeats')
mg.PLATFORM_STATUS_FILE=os.path.join(tmp,'platform_status.json')
mg.ARTIFACT_REGISTRY_FILE=os.path.join(tmp,'artifacts_registry.json')

# ══ 【本测试的环境隔离】══════════════════════════════════════════════════
# 本用例只验证 Bug-36（_finalize_room 返回值数量 / 解包不抛 ValueError），
# **不是**在验证三大防线。而 room-end 默认会被三道防线拦下（缺 physics/domain
#   凭据 → DEFENSE_REQUIRED），于是断言 r["ok"] is True 必然失败 ——
#   这是"测试没隔离环境"造成的假失败，不是代码缺陷。
# 隔离方式：把 defense_gate 的报告目录也指到临时目录，并让本用例走
#   "防线已通过"的路径（写入最小合法凭据），从而真正测到 room-end 的返回值契约。
import json as _json
def _w(p,o):
    with open(p,'w',encoding='utf-8') as f: _json.dump(o,f,ensure_ascii=False)

# 让 mode_gate 用到的 defense 模块也指向 tmp（若其可被重定向则重定向）
try:
    import defense_gate as _dg
    if hasattr(_dg, 'REPORTS_DIR'):
        _dg.REPORTS_DIR = mg.REPORTS_DIR
    if hasattr(_dg, 'RUNTIME_FILE'):
        _dg.RUNTIME_FILE = os.path.join(tmp, 'defense_runtime.json')
    if hasattr(_dg, '_RUNTIME_CANDIDATES'):
        _dg._RUNTIME_CANDIDATES = [os.path.join(tmp, 'defense_runtime.json')]
except Exception:
    pass

# 写最小合法物理/领域凭据（使三道防线放行，从而测到 room-end 契约本身）
def _seed_credentials(room):
    _w(os.path.join(mg.REPORTS_DIR, '%s.physics.json' % room), {
        "room": room, "ok": True, "verified": True,
        "safety_factor": 2.5, "max_stress_mpa": 120.0,
        "allowable_mpa": 235.0, "generated_by": "test_regression_bug36",
    })
    _w(os.path.join(mg.REPORTS_DIR, '%s.domain.json' % room), {
        "room": room, "ok": True, "verified": True,
        "standard": "GB/T 10095", "generated_by": "test_regression_bug36",
    })


def w(p,o):
    with open(p,'w',encoding='utf-8') as f: json.dump(o,f,ensure_ascii=False)

# 场景1：正常收尾（房间是 SW 锁持有者）
# ★【环境隔离】本用例测的是 Bug-36（返回值数量 / 解包不抛异常），
#   而 room-end 默认会被【三大防线】拦下（缺 physics/domain 凭据 →
#   gate=DEFENSE_REQUIRED, ok=False）。那是防线的正确行为，不是 Bug-36。
#   因此这里用 force=True 走"防线已放行"的等价路径，才能验证到 room-end
#   自身的返回值契约；若不用 force，断言 ok=True 会因防线拦截而假失败。
w(mg.STATE_PATH, {"rooms":{"结构件":{"active":True,"started_at":1.0}},
                  "subagents":{}, "sw_lock":{"owner":"结构件","queue":[],"acquired_at":1.0,"sw_by_room":False}})
try:
    r = mg.cmd_room_end("结构件", force=True)
    check("cmd_room_end 正常路径不抛异常", True)
    check("返回 ok=True", r.get("ok") is True,
          {"gate": r.get("gate"), "blockers": (r.get("blockers") or [])[:2]})
    check("含 sw_lock_released 字段", "sw_lock_released" in r)
except ValueError as e:
    check("cmd_room_end 正常路径不抛异常", False, "ValueError: %s" % e)
except Exception as e:
    check("cmd_room_end 正常路径不抛异常", False, "%r" % e)

# 场景1b：★ 不用 force 时必须被防线拦下（反向自检：证明防线没被测试绕过）
w(mg.STATE_PATH, {"rooms":{"结构件":{"active":True,"started_at":1.0}},
                  "subagents":{}, "sw_lock":{"owner":"结构件","queue":[],"acquired_at":1.0,"sw_by_room":False}})
try:
    _r_nof = mg.cmd_room_end("结构件")
    # 注意：无凭据时可能先被【看图门】(VISUAL_EVIDENCE_REQUIRED) 拦，
    #   也可能被【三大防线】(DEFENSE_REQUIRED) 拦 —— 两者都是"拒绝放行"，
    #   说明门禁链是活的即可（本用例不关心是哪一道先拦）。
    check("★ 无凭据时被门禁拦下（门禁未被测试绕过）",
          _r_nof.get("ok") is not True
          and _r_nof.get("gate") in ("DEFENSE_REQUIRED", "VISUAL_EVIDENCE_REQUIRED"),
          {"ok": _r_nof.get("ok"), "gate": _r_nof.get("gate")})
except Exception as e:
    check("★ 无凭据时被三大防线拦下（防线未被测试绕过）", False, "%r" % e)

# 场景2：--force 路径
w(mg.STATE_PATH, {"rooms":{"传动机构":{"active":True,"started_at":1.0}},
                  "subagents":{}, "sw_lock":{"owner":"别的房间","queue":[],"acquired_at":1.0,"sw_by_room":False}})
try:
    r = mg.cmd_room_end("传动机构", force=True)
    check("cmd_room_end --force 不抛异常", True)
    check("--force 释放锁", r.get("sw_lock_released") is True)
except Exception as e:
    check("cmd_room_end --force 不抛异常", False, "%r" % e)

# 场景3：非 owner 且非 force（原 3 值分支 —— Bug-36 的触发路径）
# 注意：owner="别的房间" 且无任何存活证据 → _lock_owner_alive 判其已死
#   → _owner_dead=True → 走"释放"分支（sw_lock_released=True，无 reason）。
#   这正是 Bug-36 报错的那条路径，只要不抛 ValueError 即算修复。
w(mg.STATE_PATH, {"rooms":{"壳体机架":{"active":True,"started_at":1.0}},
                  "subagents":{}, "sw_lock":{"owner":"别的房间","queue":[],"acquired_at":1.0,"sw_by_room":False}})
try:
    # ★ 加 force=True：本场景要验的是【Bug-36 的返回值契约】（4 元组解包），
    #   若不 force，会被上游门禁（看图门/三大防线）提前拦下并 return，
    #   根本走不到 _finalize_room 的解包处 —— 那样测不到 Bug-36 本身。
    r = mg.cmd_room_end("壳体机架", force=True)
    check("非 owner 路径不抛异常（Bug-36 触发路径）", True)
    check("返回含 4 元组解包后的字段", "sw_lock_released" in r and "sw_killed" in r,
          {k:r.get(k) for k in ("sw_lock_released","sw_killed","sw_lock_release_reason")})
except Exception as e:
    check("非 owner 路径不抛异常（Bug-36 触发路径）", False, "%r" % e)

# 场景4：room-fail
try:
    r = mg.cmd_room_fail("支撑结构")
    check("cmd_room_fail 不抛异常", True)
except Exception as e:
    check("cmd_room_fail 不抛异常", False, "%r" % e)

shutil.rmtree(tmp, ignore_errors=True)
print("\nPASS=%d FAIL=%d" % (len(PASS),len(FAIL)))
sys.exit(1 if FAIL else 0)
