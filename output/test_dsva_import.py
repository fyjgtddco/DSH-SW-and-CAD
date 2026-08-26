# -*- coding: utf-8 -*-
"""DSVA 国标导入验证测试"""
import sys, os, json
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

RULES_DIR = r'C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools\physics\rules'
PHYSICS_DIR = r'C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools\physics'
sys.path.insert(0, PHYSICS_DIR)

errors = []
warnings = []
pass_count = 0
fail_count = 0

def check(name, cond, detail=''):
    global pass_count, fail_count
    if cond:
        pass_count += 1
        print(f'  [PASS] {name}')
    else:
        fail_count += 1
        msg = f'  [FAIL] {name} — {detail}' if detail else f'  [FAIL] {name}'
        print(msg)
        if detail: errors.append(detail)

print('='*60)
print('DSVA 国标导入验证测试')
print('='*60)

# ── 测试 1: manifest.json 存在且合法 ──
print('\n【1. manifest.json】')
mf_path = os.path.join(RULES_DIR, 'manifest.json')
check('manifest.json 存在', os.path.isfile(mf_path))
if os.path.isfile(mf_path):
    with open(mf_path, 'r', encoding='utf-8') as f:
        mf = json.load(f)
    check('manifest 有 enabled_packs', 'enabled_packs' in mf)
    check('enabled_packs 非空', len(mf.get('enabled_packs', [])) >= 5)
    check('manifest 有 deprecated_standards', 'deprecated_standards' in mf)
    check('manifest 有 manual_review_required', 'manual_review_required' in mf)
    check('manifest 有 source_metadata', 'source_metadata' in mf)

# ── 测试 2: gb_standards_registry.json ──
print('\n【2. gb_standards_registry.json】')
reg_path = os.path.join(RULES_DIR, 'gb_standards_registry.json')
check('registry.json 存在', os.path.isfile(reg_path))
if os.path.isfile(reg_path):
    with open(reg_path, 'r', encoding='utf-8') as f:
        reg = json.load(f)
    check('standards 列表非空', len(reg.get('standards', [])) > 0)
    check('standards 数量 = 91', len(reg.get('standards', [])) == 91)
    # 检查关键字段
    s = reg['standards'][0]
    check('每项有 standard_id', 'standard_id' in s)
    check('每项有 title', 'title' in s)
    check('每项有 scope', 'scope' in s)
    check('每项有 is_deprecated', 'is_deprecated' in s)
    check('每项有 requires_manual_review', 'requires_manual_review' in s)
    check('每项有 dsva_requirement', 'dsva_requirement' in s)
    # 强制性国标标记
    mandatory = [s for s in reg['standards'] if s.get('is_mandatory')]
    check(f'强制性国标 GB 前缀={len(mandatory)}', len(mandatory) == 3)
    # 废止项标记
    deprecated = [s for s in reg['standards'] if s.get('is_deprecated')]
    check(f'废止项标记={len(deprecated)}', len(deprecated) == 9)
    # 人工复核项
    manual = [s for s in reg['standards'] if s.get('requires_manual_review')]
    check(f'需人工复核={len(manual)}', len(manual) == 11)

# ── 测试 3: 各个规则包 ──
print('\n【3. 规则包完整性】')
expected_packs = [
    ('gb_base_rules.json', 23),
    ('gb_materials_rules.json', 23),
    ('gb_structural_rules.json', 12),
    ('gb_transmission_rules.json', 14),
    ('gb_housing_rules.json', 2),
    ('gb_drafting_rules.json', 3),
    ('gb_electrical_rules.json', 1),
    ('gb_fasteners_rules.json', 4),
    ('gb_springs_rules.json', 2),
    ('gb_manufacturing_rules.json', 1),
    ('gb_surface_rules.json', 1),
    ('gb_safety_rules.json', 5),
]
for fname, expected_count in expected_packs:
    fpath = os.path.join(RULES_DIR, fname)
    ok = os.path.isfile(fpath)
    check(f'{fname} 存在', ok)
    if ok:
        with open(fpath, 'r', encoding='utf-8') as f:
            d = json.load(f)
        actual = d.get('total', 0)
        check(f'{fname} 项数={actual}', actual == expected_count)
        # 检查每个 rule 有基本字段
        rules = d.get('rules', [])
        if rules:
            r0 = rules[0]
            check(f'{fname} 每项有 rule_id', 'rule_id' in r0)
            check(f'{fname} 每项有 standard_id', 'standard_id' in r0)
            check(f'{fname} 每项有 severity', 'severity' in r0)
            check(f'{fname} 每项有 validation_method', 'validation_method' in r0)

# ── 测试 4: domain_validator 能加载规则包 ──
print('\n【4. domain_validator 加载测试】')
from domain_validator import list_domains, load_domain_rules, validate_with_domain

domains = list_domains()
check(f'可加载领域数={len(domains)}', len(domains) == 4)
domain_ids = {d['id'] for d in domains}
check('structural 领域存在', 'structural' in domain_ids)
check('transmission 领域存在', 'transmission' in domain_ids)
check('housing 领域存在', 'housing' in domain_ids)
check('mold 领域存在', 'mold' in domain_ids)

# 测试 structural 验证
result = validate_with_domain(
    design_params={'thickness_mm': 5, 'fillet_mm': 2},
    fea_result={
        'max_von_mises_mpa': 50,
        'max_displacement_mm': 0.5,
        'safety_factor': 3.0,
        'mass_kg': 1.5
    },
    domain_id='structural'
)
check('structural 验证 ok=True', result.get('ok') == True)
check('structural score=1.0', result.get('score', 0) == 1.0)
check('structural 通过 6 条', result.get('summary', {}).get('passed') == 6)
check('structural 0 违规', result.get('summary', {}).get('violations') == 0)

# 测试违规场景
result2 = validate_with_domain(
    design_params={'thickness_mm': 5, 'fillet_mm': 2},
    fea_result={
        'max_von_mises_mpa': 300,
        'max_displacement_mm': 10.0,
        'safety_factor': 1.5,
        'mass_kg': 1.5
    },
    domain_id='structural'
)
check('超限场景 ok=False', result2.get('ok') == False)
check('超限场景 score<1', result2.get('score', 1.0) < 1.0)
check('超限场景有 violations', result2.get('summary', {}).get('violations', 0) > 0)

# 测试不存在的领域
result3 = validate_with_domain({}, {}, 'nonexistent')
check('不存在领域返回 ok=False', result3.get('ok') == False)
check('不存在领域返回 error', 'error' in result3)

# 测试 GB 前缀标记
print('\n【5. 强制性国标标记验证】')
mandatory_list = [s for s in reg['standards'] if s.get('is_mandatory')]
ids = [s['standard_id'] for s in mandatory_list]
check('GB 50017 是强制性', 'GB 50017' in ids)
check('GB 50661 是强制性', 'GB 50661' in ids)
check('GB 2894 是强制性', 'GB 2894' in ids)

# 测试废止项
print('\n【6. 废止标准标记验证】')
deprecated_list = [s for s in reg['standards'] if s.get('is_deprecated')]
ids = [s['standard_id'] for s in deprecated_list]
check('GB/T 3323 被标记为废止', 'GB/T 3323' in ids or 'GB/T 3323' in [s.get('standard_id', '') for s in deprecated_list])
check('废止项有原因说明', all(s.get('deprecated_reason', '') != '' for s in deprecated_list))

# 测试人工复核项
print('\n【7. 人工复核项验证】')
manual_list = [s for s in reg['standards'] if s.get('requires_manual_review')]
check('GB/T 15706 需人工复核', 'GB/T 15706' in [s['standard_id'] for s in manual_list])
check('GB/T 16855 需人工复核', 'GB/T 16855' in [s['standard_id'] for s in manual_list])
check('GB 2894 需人工复核', 'GB 2894' in [s['standard_id'] for s in manual_list])
check('GB 50017 需人工复核', 'GB 50017' in [s['standard_id'] for s in manual_list])

# 测试规则包中 BLOCKER 级别
print('\n【8. BLOCKER 级别分布】')
for fname, _ in expected_packs:
    with open(os.path.join(RULES_DIR, fname), 'r', encoding='utf-8') as f:
        d = json.load(f)
    blockers = [r for r in d.get('rules', []) if r.get('severity') == 'BLOCKER']
    infos = [r for r in d.get('rules', []) if r.get('severity') == 'INFO']
    if fname in ('gb_base_rules.json', 'gb_drafting_rules.json', 'gb_fasteners_rules.json',
                  'gb_housing_rules.json', 'gb_springs_rules.json', 'gb_surface_rules.json'):
        check(f'{fname}: BLOCKER=0 INFO>0', len(blockers) == 0 and len(infos) > 0)
    elif fname in ('gb_structural_rules.json', 'gb_transmission_rules.json', 'gb_materials_rules.json', 'gb_safety_rules.json'):
        check(f'{fname}: 有 BLOCKER 或 INFO', len(blockers) + len(infos) > 0)
    elif fname in ('gb_electrical_rules.json', 'gb_manufacturing_rules.json'):
        check(f'{fname}: 有 BLOCKER', len(blockers) > 0)

# ── 汇总 ──
print()
print('='*60)
print(f'结果: {pass_count} PASS / {fail_count} FAIL')
print('='*60)
if fail_count > 0:
    print('\n失败详情:')
    for e in errors[:10]:
        print(f'  ✗ {e}')
