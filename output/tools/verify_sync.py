# -*- coding: utf-8 -*-
r"""校验 DSH 安装副本与仓库是否【完全同步】（逐文件哈希比对）。

DryRun 报 "0 synced" 只能说明 sync 脚本没找到要复制的 ——
但它的 Should-Skip 会跳过若干类型（.bak/_probe_/二进制等）。
因此这里做一次**独立的全量比对**，把差异逐条列出来，
证明"关键代码确实一致"，而不是只信脚本的自述。

比对对象（sync 脚本覆盖的四处落点）：
  1. engineering/tools      -> ~/.dsh/.agent-presets/engineering/tools
  2. engineering/skills     -> ~/.dsh/skills
  3. engineering/docs       -> ~/.dsh/.agent-presets/engineering/docs
  4. plugins/dsh-eng-ui     -> ~/.dsh/profiles/*/node_modules/dsh-engineering-ui
  5. preset 层根文件        -> ~/.dsh/.agent-presets/engineering/
"""
import hashlib
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

REPO = r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main"
DSH = os.path.expanduser(r"~\.dsh")
DATA = os.path.join(DSH, ".agent-presets", "engineering")

# 与 sync 脚本保持一致的跳过规则
RUNTIME_NAMES = {
    'mode_state.json', 'workflow_state.json', 'sw_state.json',
    'platform_status.json', 'artifacts_registry.json', 'TASK_FINISHED.json',
    'connection_state.json', 'design_params.json', 'defense_bypass.json',
    'violations.json', 'guard.json', 'runtime.json',
}
RUNTIME_DIRS = {'reports', 'heartbeats', 'load_cases', 'physics_runs',
                'mode_logs', '.defense', '__pycache__', 'output'}
BIN_EXT = ('.sldprt', '.sldasm', '.slddrw', '.dwg', '.dxf', '.step', '.stp',
           '.igs', '.stl', '.pdf', '.png', '.jpg', '.jpeg', '.zip', '.7z')


def skip(name, parts):
    if name in RUNTIME_NAMES:
        return True
    if '.bak' in name or name.endswith('.pyc') or name.startswith('~$'):
        return True
    if name.startswith('_probe_'):
        return True
    if '%' in name:
        return True
    if name.lower().endswith(BIN_EXT):
        return True
    for p in parts:
        if p in RUNTIME_DIRS:
            return True
        if p.startswith('_probe_') or '%' in p:
            return True
        if '.bak' in str(p):
            return True
    return False


def md5(p):
    h = hashlib.md5()
    with open(p, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def compare(src_root, dst_root, label):
    """返回 (only_in_src, only_in_dst, differ, same_count)"""
    only_src, only_dst, differ, same = [], [], [], 0
    if not os.path.isdir(src_root):
        return None
    if not os.path.isdir(dst_root):
        return ([], [], [], 0, "目标目录不存在: %s" % dst_root)
    seen = set()
    for dp, dns, fns in os.walk(src_root):
        rel_dir = os.path.relpath(dp, src_root)
        parts_dir = [] if rel_dir == '.' else rel_dir.split(os.sep)
        for fn in fns:
            rel = os.path.join(rel_dir, fn) if rel_dir != '.' else fn
            parts = parts_dir + [fn]
            if skip(fn, parts):
                continue
            seen.add(os.path.normcase(rel))
            s = os.path.join(dp, fn)
            d = os.path.join(dst_root, rel)
            if not os.path.exists(d):
                only_src.append(rel)
            elif md5(s) != md5(d):
                differ.append(rel)
            else:
                same += 1
    for dp, dns, fns in os.walk(dst_root):
        rel_dir = os.path.relpath(dp, dst_root)
        parts_dir = [] if rel_dir == '.' else rel_dir.split(os.sep)
        for fn in fns:
            rel = os.path.join(rel_dir, fn) if rel_dir != '.' else fn
            parts = parts_dir + [fn]
            if skip(fn, parts):
                continue
            if os.path.normcase(rel) not in seen:
                only_dst.append(rel)
    return (only_src, only_dst, differ, same, None)


PAIRS = [
    (os.path.join(REPO, "engineering", "tools"),
     os.path.join(DATA, "tools"), "tools"),
    (os.path.join(REPO, "engineering", "skills"),
     os.path.join(DSH, "skills"), "skills"),
    (os.path.join(REPO, "engineering", "docs"),
     os.path.join(DATA, "docs"), "docs"),
    (os.path.join(REPO, "engineering", "plugins", "dsh-engineering-ui"),
     os.path.join(DSH, "profiles", "web", "node_modules", "dsh-engineering-ui"),
     "plugin@web"),
    (os.path.join(REPO, "engineering", "plugins", "dsh-engineering-ui"),
     os.path.join(DSH, "profiles", "desktop", "node_modules", "dsh-engineering-ui"),
     "plugin@desktop"),
]

print("=" * 78)
print("DSH 安装副本 vs 仓库：逐文件哈希比对")
print("=" * 78)
total_bad = 0
for src, dst, label in PAIRS:
    r = compare(src, dst, label)
    if r is None:
        print("\n[%s] 源目录不存在，跳过" % label)
        continue
    if len(r) == 5 and r[4]:
        print("\n[%s] %s" % (label, r[4]))
        total_bad += 1
        continue
    only_src, only_dst, differ, same = r[:4]
    bad = len(only_src) + len(differ)
    total_bad += bad
    print("\n[%s]" % label)
    print("  一致 = %d" % same)
    print("  仅仓库有（安装缺） = %d" % len(only_src))
    print("  内容不同          = %d" % len(differ))
    print("  仅安装有（多余）   = %d" % len(only_dst))
    for x in only_src[:8]:
        print("      [缺] %s" % x)
    for x in differ[:8]:
        print("      [异] %s" % x)
    for x in only_dst[:5]:
        print("      [余] %s" % x)

# preset 层根文件
print("\n[preset 根文件]")
for fn in ('agent.cordis.yml', 'preset.yml', 'install-plugin.ps1', 'README.md'):
    s = os.path.join(REPO, "engineering", fn)
    d = os.path.join(DATA, fn)
    if not os.path.exists(s):
        continue
    if not os.path.exists(d):
        print("   [缺] %s" % fn)
        total_bad += 1
    elif md5(s) != md5(d):
        print("   [异] %s" % fn)
        total_bad += 1
    else:
        print("   [同] %s" % fn)

print()
print("=" * 78)
if total_bad == 0:
    print("★ 结论：安装副本与仓库【完全同步】，无差异 ✅")
else:
    print("★ 结论：仍有 %d 处差异，需要再次同步 ❌" % total_bad)
print("=" * 78)
sys.exit(1 if total_bad else 0)
