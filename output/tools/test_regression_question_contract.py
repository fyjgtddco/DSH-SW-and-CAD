# -*- coding: utf-8 -*-
"""【固定两问 回归测试】搭建方式(A/B/C) 与 并行策略(D/E) 的题面契约。

背景（两次简化）：
  1) 旧 question_contract 用 max_per_call=1 强制"每次只问一题"，把「搭建方式」
     与「并行策略」这两个【独立问题】当成"同批必须拆分的题"来判，于是
     "连着问两次"或"一次问两题"都被误判为违规 —— 已移除。
  2) 随后的「系统提问账本」(question_log.json) 核对机制也被用户判定为
     实用价值低、且反复造成流程卡顿 —— 同样已移除。

现在 choice_contract 只负责提供【固定题面】；强制点落在 select 的参数校验
（A/B/C 与 D/E 必须合法）。本测试锁定这两件事。
"""
import sys, os, json, tempfile, importlib.util as ilu
sys.stdout.reconfigure(encoding='utf-8')
TOOLS = sys.argv[1]
sys.path.insert(0, TOOLS)

def load(name, path):
    spec = ilu.spec_from_file_location(name, path)
    m = ilu.module_from_spec(spec); spec.loader.exec_module(m); return m

PASS=[];FAIL=[]
def check(n,c,d=""):
    (PASS if c else FAIL).append(n)
    print(("  [PASS] " if c else "  [FAIL] ")+n+(("  -> "+str(d)) if d else ""))

cc = load('cc', os.path.join(TOOLS,'choice_contract.py'))

print("=== 固定题面由代码给出（模型不可改写）===")
qs = cc.questions_payload()
check("恰好两个问题", len(qs) == 2, len(qs))
check("问题1 是搭建方式", qs[0].get('id') == 'build_mode')
check("问题2 是并行策略", qs[1].get('id') == 'parallel_mode')
check("搭建方式选项为 A/B/C",
      [o.get('label') for o in qs[0].get('options', [])] == ['A','B','C'])
check("并行策略选项为 D/E",
      [o.get('label') for o in qs[1].get('options', [])] == ['D','E'])
check("两个问题都带说明（帮助用户理解）",
      all(o.get('description') for q in qs for o in q.get('options', [])))
check("每次返回新对象（调用方改动不污染常量）",
      cc.questions_payload() is not cc.questions_payload())

print()
print("=== 账本机制必须已彻底移除 ===")
def strip_comments(text, prefix="#"):
    return "\n".join(l for l in text.split("\n") if not l.strip().startswith(prefix))

src = strip_comments(open(os.path.join(TOOLS,'choice_contract.py'), encoding='utf-8').read())
check("choice_contract 不再有 check_mandatory_asked", 'check_mandatory_asked' not in src)
check("choice_contract 不再引用账本常量", 'QUESTION_LOG_FILE' not in src)
check("choice_contract 不再有 blocked()", 'def blocked(' not in src)

wg = strip_comments(open(os.path.join(TOOLS,'workflow_gate.py'), encoding='utf-8').read())
check("workflow_gate 不再调用账本核对", 'check_mandatory_asked' not in wg)
check("workflow_gate 不再引用 question_contract", 'question_contract' not in wg)

plug = os.path.join(os.path.dirname(TOOLS), 'plugins', 'dsh-engineering-ui', 'lib', 'index.js')
if os.path.exists(plug):
    js = strip_comments(open(plug, encoding='utf-8').read(), "//")
    check("宿主插件不再写提问账本",
          'questionLogPath' not in js and 'recordAskedQuestion' not in js)

print()
print("=== select 仍强制校验 A/B/C 与 D/E（强制点未丢）===")
check("workflow_gate 校验 choice 合法性", "选择必须是 A, B, 或 C" in wg)
check("workflow_gate 校验 parallel 合法性", "并行模式必须是 D 或 E" in wg)

print()
print("PASS=%d FAIL=%d" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项: " + ", ".join(FAIL))
    sys.exit(1)
