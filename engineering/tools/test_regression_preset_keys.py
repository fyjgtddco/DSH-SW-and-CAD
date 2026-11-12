# -*- coding: utf-8 -*-
"""回归：preset 的 compaction 配置键必须落在宿主严格白名单内。

── 事故（2026-11-12）──────────────────────────────────────────────────────
我在 preset-engineering.patch.yml 的 compaction-engineering 下加了
`chunkChars: 36000`，想让插件用自己的分段预算。插件单测全绿、YAML 也能解析，
但 DSH 重启后**工程模式直接加载失败、从预设列表里消失**。

根因：宿主 @deepseek-ai/dsh-compaction-basic/lib/index.js:60 第一行是
  validateKeys(config, BASIC_COMPACT_CONFIG_KEYS, "BasicCompactionConfig")
那是**严格白名单**（11 个键），多一个就抛
  "BasicCompactionConfig: unknown key chunkChars"
而 preset 里任何一行装载失败 → 整个 engineering 预设装载失败。

这类错误在"YAML 能否解析"和"插件单测"两层都看不见，只在宿主 schema
校验那一层才炸。所以必须有这条回归守着。
"""
import os
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = HERE if os.path.basename(HERE) == 'tools' else os.path.join(HERE, 'tools')
CHECKER = os.path.join(TOOLS, 'check_preset_keys.py')
PRESET = os.path.abspath(os.path.join(
    TOOLS, '..', 'plugins', 'dsh-engineering-ui', 'preset-engineering.patch.yml'))

PASS = []
FAIL = []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (("  -> " + str(detail)[:400]) if (detail and not cond) else ""))


def run_checker(yaml_path=None):
    cmd = [sys.executable, CHECKER]
    if yaml_path:
        cmd += ['--yaml', yaml_path]
    p = subprocess.run(cmd, capture_output=True, text=True,
                       encoding='utf-8', errors='replace', timeout=120)
    return p.returncode, (p.stdout or '') + (p.stderr or '')


print("=== ① 仓库 preset 必须通过白名单校验 ===")
check("check_preset_keys.py 存在", os.path.isfile(CHECKER), CHECKER)
check("preset 存在", os.path.isfile(PRESET), PRESET)
rc, out = run_checker()
check("仓库 preset 校验通过（rc=0）", rc == 0, out)
check("输出确认无越界键", 'BAD' not in out, out)
for must in ('thresholdRatio', 'headroomTokens', 'maxTokens'):
    check("配置含 %s" % must, must in out, out)

print("\n=== ② 装好的副本也必须通过（两处 profile 都要）===")
home = os.path.expanduser('~')
for prof in ('desktop', 'web'):
    p = os.path.join(home, '.dsh', 'profiles', prof, 'node_modules',
                     'dsh-engineering-ui', 'preset-engineering.patch.yml')
    if not os.path.isfile(p):
        check("[%s] 副本存在" % prof, False, p)
        continue
    rc2, out2 = run_checker(p)
    check("[%s] 安装副本校验通过" % prof, rc2 == 0, out2)

print("\n=== ③ 反证：混入非法键时必须报错（守卫真的有效）===")
bad = os.path.join(TOOLS, '_tmp_bad_preset.yml')
try:
    with open(PRESET, encoding='utf-8') as f:
        src = f.read()
    # 在 maxTokens 那一行后插一个非法键（同缩进）
    import re
    injected = re.sub(r'(?m)^(\s+)maxTokens:\s*\d+\s*$',
                      lambda m: m.group(0) + '\n' + m.group(1) + 'chunkChars: 36000',
                      src, count=1)
    check("注入成功（确实改到了内容）", injected != src)
    with open(bad, 'w', encoding='utf-8') as f:
        f.write(injected)
    rc3, out3 = run_checker(bad)
    check("非法键必须让校验失败（rc!=0）", rc3 != 0, out3[:300])
    check("错误信息点名 chunkChars", 'chunkChars' in out3, out3[:300])
    check("错误信息解释后果（预设装载失败）",
          '预设' in out3 and ('失败' in out3 or '消失' in out3), out3[:300])
finally:
    try:
        if os.path.exists(bad):
            os.remove(bad)
    except Exception:
        pass

print("\n=== ④ 阈值口径：必须是用户指定的 200K（1M × 0.2）===")
import re as _re
with open(PRESET, encoding='utf-8') as f:
    txt = f.read()
start = txt.find('- id: compaction-engineering')
tail = txt[start:]
end = tail.find('- id: command-compact')
block = (tail[:end] if end > 0 else tail)
code_lines = [l for l in block.splitlines() if not l.strip().startswith('#')]
code = '\n'.join(code_lines)
m = _re.search(r'thresholdRatio:\s*([\d.]+)', code)
check("thresholdRatio 存在", bool(m), code)
if m:
    check("thresholdRatio == 0.2（200K 阈值）", float(m.group(1)) == 0.2, m.group(1))
    check("1M 窗口 × 0.2 == 200000",
          int(1000000 * float(m.group(1))) == 200000)
check("headroomTokens 保持 8000",
      bool(_re.search(r'headroomTokens:\s*8000', code)), code)
check("maxTokens 已显式声明（不与 headroom 联动）",
      bool(_re.search(r'maxTokens:\s*\d+', code)), code)

print("\nPASS=%d FAIL=%d" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：" + ", ".join(FAIL))
sys.exit(1 if FAIL else 0)
