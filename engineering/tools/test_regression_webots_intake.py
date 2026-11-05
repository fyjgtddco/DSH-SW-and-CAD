# -*- coding: utf-8 -*-
"""【Webots 接入 · 回归】仿真入口必须【不碰 SW、不改门禁、缺配置就如实说不可用】

背景（2026-11-05 接入 D1）：
  Webots 仿真链被接进工程模式，但它有一条容易走偏的边界 ——
  一旦「仿真」被塞进 sw_bridge 的常规路径，就会连带变成：
    · 取 SW 独占锁（并行房间互相排队，纯仿真把建模房间卡死）；
    · get_sw() 把 SolidWorks 拉起来（只是想跑个仿真却占住 SW 窗口）；
    · 强制 --room（CLI 侧没有房间归属就调不动）。
  这些都是【已经踩过的坑】的同类。本测试把边界钉死。

锁定四件事：
  ① sim-status / sim-env / sim-list 在 _NO_SW_CMDS 里（main() 会把 sw 置 None）；
  ② webots_env.probe() 零副作用：只读环境变量与路径存在性，不建文件不启动进程；
  ③ 缺配置时 sim_available=false 且 blocking 逐项列出 —— 绝不允许"看起来能用"；
  ④ 即使配置齐备，ready 也恒为 false（本版没有真实握手/身份确认）。

运行：
    python test_regression_webots_intake.py <工程模式根>/tools
"""
import sys, os, json, tempfile, importlib.util as ilu
sys.stdout.reconfigure(encoding='utf-8')
TOOLS = sys.argv[1]
sys.path.insert(0, TOOLS)

PASS = []; FAIL = []
def check(n, c, d=""):
    (PASS if c else FAIL).append(n)
    print(("  [PASS] " if c else "  [FAIL] ") + n + (("  -> " + str(d)) if d else ""))

def load(name, path):
    spec = ilu.spec_from_file_location(name, path)
    m = ilu.module_from_spec(spec); spec.loader.exec_module(m); return m

# ── ① sw_bridge 的命令分类 ─────────────────────────────────────────────────
print("=== sim-* 必须被判为「完全不碰 SolidWorks」 ===")
src = open(os.path.join(TOOLS, 'sw_bridge.py'), encoding='utf-8').read()
for cmd in ('"sim-status"', '"sim-env"', '"sim-list"'):
    check("_LOCAL_CMDS 含 " + cmd.strip('"'), cmd in src)
check("sim-report 有分发分支", 'elif cmd == "sim-report":' in src)
check("help 里登记了 sim-status", '"sim-status": "Webots 仿真可用性自检' in src)

# ⚠️ 关键：sim 命令要出现在 _LOCAL_CMDS 段（该段并入 _NO_SW_CMDS）
import re
m = re.search(r"_LOCAL_CMDS\s*=\s*\{(.*?)\}", src, re.S)
local_block = m.group(1) if m else ""
check("_LOCAL_CMDS 代码块里确实有 sim-status", '"sim-status"' in local_block, (local_block[:60] + '...') if local_block else 'not found')
check("_NO_SW_CMDS 由 _LOCAL_CMDS 并集构成", "_NO_SW_CMDS = _PURE_COMPUTE_CMDS | _CAD_ONLY_CMDS | _LOCAL_CMDS" in src)

# ── ② webots_env.probe() 零副作用 ─────────────────────────────────────────
print()
print("=== webots_env.probe() 只读、不落盘、不启动进程 ===")
we = load('webots_env', os.path.join(TOOLS, 'webots_env.py'))
src_we = open(os.path.join(TOOLS, 'webots_env.py'), encoding='utf-8').read()
for bad in ('subprocess', 'os.makedirs', 'open(', 'shutil', 'Popen'):
    check("不出现 " + bad, bad not in src_we.replace('开放', ''))

# ── ③ 缺配置必须如实说不可用 ───────────────────────────────────────────────
print()
print("=== 环境缺项 → sim_available=false，并逐项列出 blocking ===")
saved = {k: os.environ.pop(k, None) for k in
         ('DSH_WEBOTS_ENABLE', 'DSH_WEBOTS_PYTHON', 'DSH_WEBOTS_INPUT_ROOT',
          'DSH_STATE_DIR', 'DSH_ENGINEERING_ROOT', 'WEBOTS_HOME',
          'DSH_WEBOTS_RUNTIME_CONFIG')}
try:
    r0 = we.probe()
    check("ok=true（自检本身成功）", r0.get('ok') is True)
    check("sim_available=false", r0.get('sim_available') is False)
    check("blocking 列出全部 6 项", len(r0.get('blocking', [])) == 6, r0.get('blocking'))
    check("ready 恒为 false", r0.get('ready') is False)
    check("next_action 提示补哪几项", 'DSH_WEBOTS_ENABLE' in r0.get('next_action', ''))

    # 配一半：ENABLE=1 但其余仍缺 → 仍不可用（防止"开了开关就算好"）
    os.environ['DSH_WEBOTS_ENABLE'] = '1'
    r1 = we.probe()
    check("只开 ENABLE 仍不可用", r1.get('sim_available') is False)
    check("blocking 少了一项", len(r1.get('blocking', [])) == 5, r1.get('blocking'))

    # 全部配齐（用真实存在的临时目录 + 当前解释器）
    tmp = tempfile.mkdtemp()
    os.environ.update({
        'DSH_WEBOTS_PYTHON': sys.executable,
        'DSH_WEBOTS_INPUT_ROOT': tmp,
        'DSH_STATE_DIR': tmp,
        'DSH_ENGINEERING_ROOT': TOOLS,
        'WEBOTS_HOME': tmp,
    })
    r2 = we.probe()
    check("配齐后 sim_available=true", r2.get('sim_available') is True, r2.get('blocking'))
    check("配齐后 ready 仍为 false（没有真实握手）", r2.get('ready') is False)

    # 路径必须存在
    os.environ['DSH_WEBOTS_INPUT_ROOT'] = os.path.join(tmp, 'missing-dir')
    r3 = we.probe()
    check("输入根不存在 → 不可用", r3.get('sim_available') is False)
    check("失败原因写明「路径不存在」",
          any(c.get('reason') == '路径不存在' for c in r3['checks']))

    # 相对路径必须拒
    os.environ['DSH_WEBOTS_INPUT_ROOT'] = 'relative/dir'
    r4 = we.probe()
    check("相对路径 → 不可用", r4.get('sim_available') is False)
    check("失败原因写明「必须是绝对路径」",
          any(c.get('reason') == '必须是绝对路径' for c in r4['checks']))
finally:
    for k, v in saved.items():
        if v is None: os.environ.pop(k, None)
        else: os.environ[k] = v

# ── ④ JSON 输出可用（AI 直接解析） ─────────────────────────────────────────
print()
print("=== --json 输出可被解析，且字段稳定 ===")
import io, contextlib
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    we.main(['--json'])
try:
    parsed = json.loads(buf.getvalue())
    check("--json 是合法 JSON", True)
    for key in ('ok', 'sim_available', 'ready', 'checks', 'blocking', 'next_action'):
        check("含字段 " + key, key in parsed)
except Exception as e:
    check("--json 是合法 JSON", False, e)

print()
print("PASS=%d FAIL=%d" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项: " + ", ".join(FAIL))
    sys.exit(1)
