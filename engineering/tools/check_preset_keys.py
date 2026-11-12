# -*- coding: utf-8 -*-
"""守卫：preset 里 compaction 段的配置键必须落在宿主严格白名单内。

── 为什么需要这个脚本（2026-11-12 真实事故）──────────────────────────────
我给 `preset-engineering.patch.yml` 的 compaction-engineering 加了一个
`chunkChars: 36000`（想在插件侧调分段字符预算）。看着完全合理，结果：
  · 宿主 `dsh-compaction-basic/lib/index.js:60` 第一行就是
      validateKeys(config, BASIC_COMPACT_CONFIG_KEYS, "BasicCompactionConfig")
  · `BASIC_COMPACT_CONFIG_KEYS` 是【严格白名单】，多余键直接抛
      "BasicCompactionConfig: unknown key chunkChars"
  · 而 preset 里任何一行装载失败 → **整个 engineering 预设装载失败**
    → 工程模式从预设列表里消失、界面挂在别的预设上
    → 用户看到的是"我的工程模式呢？咋加载失败了？"

单测跑不出来这件事（插件自己的测试全绿），因为它只在【宿主 schema 校验】
那一层才炸。所以必须有一个直接对着 YAML + 宿主白名单做校验的守卫。

用法：
    python tools/check_preset_keys.py
    python tools/check_preset_keys.py --yaml <其他路径>
退出码 0=通过。
"""
import json
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))

# ── 宿主 @deepseek-ai/dsh-compaction-basic 的严格白名单（逐字核对）──────────
# 出处：dsh-compaction-basic/lib/index.js:31-41
#   POLICY_CONFIG_KEYS = thresholdRatio / headroomTokens / retainRatio /
#     retainTokens / summarizationProvider / summarizationModel / maxTokens /
#     compactionRetries / maxOverflowRetries
#   BASIC_COMPACT_CONFIG_KEYS = POLICY_CONFIG_KEYS + modelPolicies + auto
HOST_ALLOWED_KEYS = {
    'thresholdRatio', 'headroomTokens', 'retainRatio', 'retainTokens',
    'summarizationProvider', 'summarizationModel', 'maxTokens',
    'compactionRetries', 'maxOverflowRetries',
    'modelPolicies', 'auto',
}

#: compaction-engineering 之外、同属 compaction group 的其它子项（各有自己的 schema）
OTHER_COMPONENT_IDS = {'command-compact', 'tool-result-pruner'}


def find_preset(custom=None):
    if custom:
        return custom
    cands = [
        os.path.join(HERE, '..', 'plugins', 'dsh-engineering-ui',
                     'preset-engineering.patch.yml'),
        os.path.join(os.path.expanduser('~'), '.dsh', 'profiles', 'desktop',
                     'node_modules', 'dsh-engineering-ui',
                     'preset-engineering.patch.yml'),
        os.path.join(os.path.expanduser('~'), '.dsh', 'profiles', 'web',
                     'node_modules', 'dsh-engineering-ui',
                     'preset-engineering.patch.yml'),
    ]
    for p in cands:
        p = os.path.abspath(p)
        if os.path.isfile(p):
            return p
    return None


def extract_config_keys(lines, start_idx):
    """从 compaction-engineering 的 config: 之后，取出缩进配置键（跳过注释）。"""
    keys = []
    base_indent = None
    for i in range(start_idx, len(lines)):
        line = lines[i]
        if not line.strip():
            continue
        # 去掉行尾注释后再看
        code = re.sub(r'\s+#.*$', '', line)
        if not code.strip():
            continue
        stripped = code.strip()
        if stripped.startswith('#'):
            continue
        indent = len(code) - len(code.lstrip())
        if base_indent is None:
            base_indent = indent
        # 离开配置块（缩进更浅，或遇到新的列表项 - id:）
        if indent < base_indent or re.match(r'^\s*-\s+id:', code):
            break
        if indent == base_indent:
            m = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)\s*:', stripped)
            if m:
                keys.append((m.group(1), i + 1))
    return keys


def main():
    args = sys.argv[1:]
    custom = None
    if '--yaml' in args:
        k = args.index('--yaml')
        if k + 1 < len(args):
            custom = args[k + 1]
    path = find_preset(custom)
    if not path:
        print('[SKIP] 找不到 preset-engineering.patch.yml')
        return 0
    print('检查：%s' % path)
    with open(path, encoding='utf-8') as f:
        lines = f.read().splitlines()

    # 定位 - id: compaction-engineering
    target = None
    for i, line in enumerate(lines):
        if re.match(r'^\s*-\s+id:\s*compaction-engineering\s*$', line):
            target = i
            break
    if target is None:
        print('[FAIL] preset 里找不到 `- id: compaction-engineering`')
        return 1

    # 再找到它下面的 config:
    cfg_at = None
    for i in range(target, min(target + 8, len(lines))):
        if re.match(r'^\s*config:\s*$', lines[i]):
            cfg_at = i + 1
            break
    if cfg_at is None:
        print('[FAIL] compaction-engineering 下没有 config:')
        return 1

    pairs = extract_config_keys(lines, cfg_at)
    if not pairs:
        print('[FAIL] compaction-engineering 的 config 是空的')
        return 1

    bad = []
    print('\n实际配置键（%d 个）：' % len(pairs))
    for key, ln in pairs:
        ok = key in HOST_ALLOWED_KEYS
        print('  %s %-24s (L%d)' % ('[OK]  ' if ok else '[BAD] ', key, ln))
        if not ok:
            bad.append((key, ln))

    if bad:
        print('\n' + '=' * 70)
        print('❌ 发现 %d 个【宿主白名单之外】的键 —— 会让整个 engineering 预设装载失败：'
              % len(bad))
        for key, ln in bad:
            print('   %s  (L%d)' % (key, ln))
        print()
        print('宿主 dsh-compaction-basic/lib/index.js:60 会执行：')
        print('  validateKeys(config, BASIC_COMPACT_CONFIG_KEYS, "BasicCompactionConfig")')
        print('严格白名单只有：')
        print('  ' + ', '.join(sorted(HOST_ALLOWED_KEYS)))
        print()
        print('后果：工程模式从预设列表里消失 / 加载失败。')
        print('修法：删掉多余键；需要插件私有配置请走环境变量（不经宿主 schema）。')
        print('=' * 70)
        return 1

    print('\n✅ 全部配置键都在宿主白名单内。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
