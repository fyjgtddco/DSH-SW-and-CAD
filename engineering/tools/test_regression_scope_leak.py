# -*- coding: utf-8 -*-
"""【Bug-44 回归测试】跨作用域别名泄漏检查（NameError 类缺陷的静态防线）

背景：Bug-44 的 title-block NameError 根因是——
    cmd_drawing 内写了 "import time as _time"（函数局部别名），
    而 set_title_block 是【另一个函数】，作用域里没有 _time
    → 调 title-block 必然 NameError，标题栏字段全部写不进去。

这类缺陷有两个特点，所以必须用静态检查而非运行测试来防：
    ① 只在【特定分支】才触发（本例是 title-block 命令），
       常规建模流程跑一百遍也碰不到；
    ② 报错信息（NameError: name '_time' is not defined）与
       "某函数少 import 了个东西"之间的因果链不直观，容易误判。

本检查器用 AST 精确定位：
    · 某名字仅由【函数 A 内的 import】提供；
    · 却在【函数 B】里被读取；
    · 且 B 自己【没有本地绑定】该名字（赋值/参数/for/with/except/局部 import）。
三者同时成立 → 真实泄漏，必然 NameError。

注意两个方向的易错点（本检查器已处理，勿简化）：
    · 链式作用域是【内→外】，所以"定义在使用者的祖先内"=
      定义链是使用链的【后缀】（早期版本写成前缀，导致大量误报）；
    · 必须排除"使用者自己赋值"的名字（如 _t = d.GetTitle），
      否则 open_document_fresh 这类正常代码会被误报。

退出码：0 = 无泄漏；1 = 检出泄漏（可直接作为 CI 门禁）
"""
import ast
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))

# 被检查的门禁脚本（新增脚本请一并加入）
FILES = [
    "sw_bridge.py", "swapi.py", "ask_user.py", "mode_gate.py",
    "workflow_gate.py", "physics_bridge.py", "ac_validate.py",
    "ac_bridge.py", "_root.py", "defense_gate.py",
]


def _chain(node, parents):
    """返回从内到外的函数名链（含嵌套函数）。"""
    out = []
    cur = node
    while cur is not None:
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append(cur.name)
        cur = parents.get(cur)
    return out


def check_file(path):
    """检查单个文件，返回 [(alias, use_chain, lineno, def_chains), ...]。"""
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src, path)

    parents = {}
    for n in ast.walk(tree):
        for c in ast.iter_child_nodes(n):
            parents[c] = n

    # 模块级 import 名（这些全文件可用，不算泄漏）
    mod_imports = set()
    for n in tree.body:
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                mod_imports.add(a.asname or a.name.split(".")[0])

    # 每个函数内本地绑定的名字（赋值/参数/for/with/except/局部 import）
    func_locals = {}
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names = set()
            a = n.args
            _all_args = (list(a.args) + list(a.posonlyargs) + list(a.kwonlyargs)
                         + ([a.vararg] if a.vararg else [])
                         + ([a.kwarg] if a.kwarg else []))
            for arg in _all_args:
                if arg:
                    names.add(arg.arg)
            for sub in ast.walk(n):
                if isinstance(sub, ast.Name) and isinstance(sub.ctx, (ast.Store, ast.Del)):
                    names.add(sub.id)
                elif isinstance(sub, ast.arg):
                    names.add(sub.arg)
                elif isinstance(sub, ast.alias):
                    names.add(sub.asname or sub.name.split(".")[0])
                elif isinstance(sub, ast.ExceptHandler) and sub.name:
                    names.add(sub.name)
            func_locals[n.name] = names

    # 函数内 import 的别名 → 定义它的函数链（可能多处定义）
    local_imports = {}
    for n in ast.walk(tree):
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            ch = _chain(n, parents)
            if not ch:
                continue
            for a in n.names:
                nm = a.asname or a.name.split(".")[0]
                local_imports.setdefault(nm, []).append(ch)

    real = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Name) or not isinstance(n.ctx, ast.Load):
            continue
        al = n.id
        if al in mod_imports or al not in local_imports:
            continue
        uc = _chain(n, parents)
        if not uc:
            continue
        # 使用者函数内已本地绑定该名 → 不是 import 泄漏
        if al in func_locals.get(uc[0], set()):
            continue
        # 链是内→外：定义在使用者的祖先内 <=> 定义链是使用链的【后缀】
        ok = False
        for dc in local_imports[al]:
            if len(dc) <= len(uc) and uc[-len(uc) and -len(dc):] == dc:
                ok = True
                break
        if not ok:
            real.append((al, uc, n.lineno, local_imports[al]))
    return real


def self_check():
    """自检：确认检查器【真的能】抓出泄漏，而不是永远返回"无"。

    做法：构造一个与 Bug-44 完全同构的微型源码 ——
        f() 内 import time as _t；g() 里用 _t（但 g 没有本地绑定）
    若检查器抓不到，说明它失效了（假通过），必须报错。
    再做反向自检：使用者自己赋值的合法代码【不应】被误报。
    """
    import tempfile

    def _run(src):
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                         encoding="utf-8") as fh:
            fh.write(src)
            tmp = fh.name
        try:
            return check_file(tmp)
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    # ① 正向：应当抓出泄漏
    # 用三引号原文（避免转义层数过多导致的可读性问题）
    leak = """import os
def f():
    import time as _t
    return _t.time()
def g():
    return _t.strftime('%Y')
"""
    found = _run(leak)
    if not found:
        print("  [FAIL] 自检失败：检查器【未能】抓出构造的泄漏样本 —— "
              "它已失效（会给出假通过）")
        return False
    print("  [PASS] 自检通过：能抓出泄漏样本（alias=%s L%d）"
          % (found[0][0], found[0][2]))

    # ② 反向：合法代码不应误报
    ok_src = """def f():
    import time as _t
    return _t.time()
def h():
    _t = 5
    return _t + 1
"""
    found2 = _run(ok_src)
    if found2:
        print("  [FAIL] 反向自检失败：合法代码（使用者自己赋值）被误报 %s" % (found2,))
        return False
    print("  [PASS] 反向自检通过：合法本地绑定不误报")
    return True


def main():
    total = 0
    checked = 0

    # ── 先做检查器自身的有效性自检（防止"永远通过"的假测试）──────────
    print("=== 检查器自检 ===")
    if not self_check():
        print("")
        print("PASS=0 FAIL=1")
        return 1
    print("")

    for f in FILES:
        p = os.path.join(HERE, f)
        if not os.path.isfile(p):
            print("  [SKIP] %-20s (文件不存在)" % f)
            continue
        checked += 1
        try:
            real = check_file(p)
        except SyntaxError as e:
            print("  [FAIL] %-20s 语法错误: %s" % (f, e))
            total += 1
            continue
        total += len(real)
        if real:
            print("  [FAIL] %-20s ❌ 真实跨作用域泄漏 %d 处" % (f, len(real)))
            for r in real[:8]:
                print("         alias=%s 使用于=%s L%d 定义于=%s" % (r[0], r[1], r[2], r[3]))
        else:
            print("  [PASS] %-20s ✅ 无跨作用域别名泄漏" % f)

    print("\n检查 %d 个文件，泄漏合计: %d" % (checked, total))
    print("PASS=%d FAIL=%d" % (checked if total == 0 else 0, 0 if total == 0 else 1))
    return 0 if total == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
