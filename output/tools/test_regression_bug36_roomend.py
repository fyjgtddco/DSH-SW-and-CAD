
# -*- coding: utf-8 -*-
"""【Bug-36 回归测试】_finalize_room 所有 return 分支必须返回相同数量的值，
且 cmd_room_end / cmd_room_fail 解包不抛异常。"""
import sys, os, json, tempfile, shutil, inspect, ast
sys.stdout.reconfigure(encoding='utf-8')
TOOLS = r'C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools'
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

def w(p,o):
    with open(p,'w',encoding='utf-8') as f: json.dump(o,f,ensure_ascii=False)

# 场景1：正常收尾（房间是 SW 锁持有者）
w(mg.STATE_PATH, {"rooms":{"结构件":{"active":True,"started_at":1.0}},
                  "subagents":{}, "sw_lock":{"owner":"结构件","queue":[],"acquired_at":1.0,"sw_by_room":False}})
try:
    r = mg.cmd_room_end("结构件")
    check("cmd_room_end 正常路径不抛异常", True)
    check("返回 ok=True", r.get("ok") is True)
    check("含 sw_lock_released 字段", "sw_lock_released" in r)
except ValueError as e:
    check("cmd_room_end 正常路径不抛异常", False, "ValueError: %s" % e)
except Exception as e:
    check("cmd_room_end 正常路径不抛异常", False, "%r" % e)

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
    r = mg.cmd_room_end("壳体机架")
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
