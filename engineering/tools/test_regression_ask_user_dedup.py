# -*- coding: utf-8 -*-
"""【A10 回归测试】ask_user.py 去重函数作用域（NameError）+ 同题重试复用旧卡

现场（缺陷清单 A10 / P1）：
    _dedup_across_sessions() 是【模块级】函数（325 行），它在 341 行调用
    _same_question() —— 而 _same_question 当时是 main() 里的【闭包】（661 行）。
    作用域错位 → 只要走到"跨会话去重"这条路，必抛
        NameError: name '_same_question' is not defined
    而且崩在 main() 的 try 之外，整个提问进程直接挂掉；报错与"重复提问"毫无
    关联，现场只能被误判成"宿主桥不可用"。

本测试锁定三件事：
  ① _same_question / _is_modify_question / _digits_of / _semantic_key 必须是
     【模块级】定义（0 缩进 def），main() 里不得再有同名闭包（会遮蔽）；
  ② _same_question 的两种调用口径都成立：
        _same_question(p)                     # p 自带 questions
        _same_question({"questions": peer})   # 显式给另一侧
     另加显式口径 questions= / against=（去重层就是用 questions= 调的）；
  ③ 真跑一遍"同题重试 + 已有 pending"：_dedup_across_sessions 必须命中，
     且 main() 复用旧卡、不再 POST /ask-child 登记第二张卡。

运行：python test_regression_ask_user_dedup.py [tools目录]
退出码 0 = 通过
"""
import ast
import io
import json
import os
import sys
import importlib.util as ilu

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = sys.argv[1] if len(sys.argv) > 1 else HERE
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

P = F = 0
FAILS = []


def ck(label, cond, extra=""):
    global P, F
    if cond:
        P += 1
        print("  [PASS] " + label)
    else:
        F += 1
        FAILS.append(label)
        print("  [FAIL] " + label + ("  " + str(extra)[:300] if extra else ""))


def sec(t):
    print("")
    print("=" * 74)
    print(t)
    print("=" * 74)


AU_PATH = os.path.join(TOOLS, "ask_user.py")
SRC = io.open(AU_PATH, encoding="utf-8").read()

_spec = ilu.spec_from_file_location("ask_user_under_test", AU_PATH)
au = ilu.module_from_spec(_spec)
_spec.loader.exec_module(au)

# 题目样例：两条措辞不同、语义相同（归一化后互相包含），选项集合一致
Q_A = [{"id": "q1", "header": "参数确认",
        "question": "以下参数将用于建模底座安装板，要用吗？",
        "options": [{"label": "确认"}, {"label": "需要修改"}]}]
Q_B = [{"id": "q1", "header": "参数确认",
        "question": "请确认：底座安装板 参数确认",
        "options": [{"label": "确认"}, {"label": "需要修改"}]}]
Q_MOD = [{"id": "q1", "header": "参数确认",
          "question": "底座安装板 厚度改为 12mm，确认？",
          "options": [{"label": "确认"}, {"label": "需要修改"}]}]
Q_OTHER_OPTS = [{"id": "q1", "header": "参数确认",
                 "question": "以下参数将用于建模底座安装板，要用吗？",
                 "options": [{"label": "确认"}]}]


# ════════════════════════════════════════════════════════════════════
sec("① 去重所需函数必须定义在【模块级】（NameError 的直接成因）")
# ════════════════════════════════════════════════════════════════════
tree = ast.parse(SRC)
MOD_DEFS = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
for _fn in ("_same_question", "_is_modify_question", "_digits_of", "_semantic_key"):
    n = MOD_DEFS.get(_fn)
    ck("模块级 def %s" % _fn, n is not None,
       "未找到（可能仍是 main() 里的闭包）")
    if n is not None:
        ck("  %s 是 0 缩进（col_offset=%d）" % (_fn, n.col_offset), n.col_offset == 0)

ck("_same_question 是模块属性（可被 _dedup_across_sessions 取到）",
   callable(getattr(au, "_same_question", None)))
ck("_dedup_across_sessions 也是模块级函数",
   callable(getattr(au, "_dedup_across_sessions", None)))


def _nested_defs(fn_node, name):
    """在函数体里（任意深度）找同名 def。"""
    hits = []
    for sub in ast.walk(fn_node):
        if isinstance(sub, ast.FunctionDef) and sub.name == name and sub is not fn_node:
            hits.append(sub.lineno)
    return hits


_main_node = MOD_DEFS.get("main")
ck("main() 里不再有同名闭包 _same_question（否则遮蔽模块级实现）",
   bool(_main_node) and not _nested_defs(_main_node, "_same_question"),
   _nested_defs(_main_node, "_same_question") if _main_node else "main 不存在")
for _fn in ("_is_modify_question", "_digits_of", "_semantic_key"):
    ck("main() 里不再有同名闭包 %s" % _fn,
       bool(_main_node) and not _nested_defs(_main_node, _fn))

ck("main() 把'我方题目'注入模块级单槽（_set_current_questions）",
   "_set_current_questions(questions)" in SRC)


# ════════════════════════════════════════════════════════════════════
sec("② _same_question 的多种调用口径都要成立")
# ════════════════════════════════════════════════════════════════════
au._set_current_questions(Q_A)
ck("单参口径 _same_question(p)：同题 → True",
   au._same_question({"questions": Q_B}) is True)
ck("单参口径 _same_question(p)：异题（选项不同）→ False",
   au._same_question({"questions": Q_OTHER_OPTS}) is False)
ck("显式口径 questions=：同题 → True",
   au._same_question({"questions": Q_B}, questions=Q_A) is True)
ck("显式口径 questions=：异题 → False",
   au._same_question({"questions": Q_OTHER_OPTS}, questions=Q_A) is False)
ck("显式口径 against=：同题 → True",
   au._same_question({}, against={"questions": Q_B}, questions=Q_A) is True)
ck("未注入我方题目时不得炸（返回 False 而不是 NameError）",
   (au._set_current_questions(None) or True) and au._same_question(Q_B) is False)
au._set_current_questions(Q_A)

ck("题数不同 → False", au._same_question({"questions": Q_A + Q_B}) is False)
ck("'修改类'问句不与普通确认合并（防止复用旧参数卡）",
   au._same_question({"questions": Q_MOD}, questions=Q_A) is False)
ck("两边都是修改类且文本一致 → True",
   au._same_question({"questions": Q_MOD}, questions=Q_MOD) is True)


# ════════════════════════════════════════════════════════════════════
sec("③ 同题重试 + 已有 pending：_dedup_across_sessions 必须命中")
# ════════════════════════════════════════════════════════════════════
_REAL_GLOBAL = au._global_pending

# 注：这里就是要复现"模块级函数调用模块级函数"的路径 —— 修复前必然 NameError
try:
    au._global_pending = lambda host: {
        "pending": [{"pendingId": "P-PEER", "childSessionId": "child-peer",
                     "questions": Q_B}]}
    _dup = au._dedup_across_sessions("http://127.0.0.1:1", Q_A, "child-me")
    ck("跨会话去重不再抛 NameError（这就是 A10 的现场）", True)
    ck("同题被识别为命中", _dup.get("hit") is True, _dup)
    ck("命中别人的卡 → owned_by_me=False", _dup.get("owned_by_me") is False, _dup)
    ck("带回 peer 的 pendingId", _dup.get("pendingId") == "P-PEER", _dup)

    au._global_pending = lambda host: {
        "pending": [{"pendingId": "P-MINE", "childSessionId": "child-me",
                     "questions": Q_B}]}
    _dup2 = au._dedup_across_sessions("http://127.0.0.1:1", Q_A, "child-me")
    ck("同题且是我自己建的卡 → owned_by_me=True", _dup2.get("owned_by_me") is True, _dup2)

    au._global_pending = lambda host: {
        "pending": [{"pendingId": "P-OTHER", "childSessionId": "child-peer",
                     "questions": Q_OTHER_OPTS}]}
    _dup3 = au._dedup_across_sessions("http://127.0.0.1:1", Q_A, "child-me")
    ck("选项不同的题不得被误合并", _dup3.get("hit") is False, _dup3)
except NameError as e:
    ck("跨会话去重不再抛 NameError（这就是 A10 的现场）", False, repr(e))
finally:
    au._global_pending = _REAL_GLOBAL


# ════════════════════════════════════════════════════════════════════
sec("④ 端到端：同题重试 → 复用旧卡，不再登记第二张")
# ════════════════════════════════════════════════════════════════════
# 用一个假宿主替代 HTTP：pending 里有"我"的旧卡（措辞略有不同），
# 一轮轮询后卡片消失并带回答案 —— 断言全程【没有】POST /ask-child。
class _FakeHost(object):
    def __init__(self):
        self.posts = []
        self.pending_calls = 0
        self.answered = False

    def get(self, url, timeout=30):
        if "/pending-child" in url:
            self.pending_calls += 1
            # 前 2 次：旧卡还在（脚本应认出来并复用）；之后：卡片消失
            if self.pending_calls > 2:
                return {"pending": []}
            return {"pending": [{"pendingId": "P-OLD",
                                 "childSessionId": "child-me",
                                 "questions": Q_B}]}
        if "/result-child" in url:
            return {"ok": True,
                    "answers": [{"id": "q1", "selected": ["确认"], "custom": ""}]}
        return {}

    def post(self, url, payload, timeout=30):
        self.posts.append((url, payload))
        return {"ok": True, "pendingId": "P-NEW"}


_fh = _FakeHost()
_REAL_GET, _REAL_POST, _REAL_SLEEP = au._get, au._post, au.time.sleep
_REAL_ARGV = sys.argv[:]
_out = io.StringIO()
try:
    au._get = _fh.get
    au._post = _fh.post
    au.time.sleep = lambda s: None
    sys.argv = ["ask_user.py", "--child", "child-me",
                "--question", Q_A[0]["question"],
                "--option", "确认", "--option", "需要修改",
                "--header", "参数确认",
                "--timeout", "3", "--seg-max", "5", "--interval", "0.01"]
    _old_stdout = sys.stdout
    sys.stdout = _out
    try:
        rc = au.main()
    finally:
        sys.stdout = _old_stdout
    ck("main() 正常返回（没有 NameError 崩掉）", rc == 0, rc)
    _ask_posts = [p for p in _fh.posts if "/ask-child" in p[0]]
    ck("复用了已有卡片：没有登记新卡", len(_ask_posts) == 0,
       [p[0] for p in _fh.posts])
    _txt = _out.getvalue().strip()
    _ok = False
    try:
        # emit() 输出的是 indent=2 的多行 JSON，整体解析（不能只取最后一行）
        _ok = json.loads(_txt or "{}").get("ok") is True
    except Exception:
        _ok = False
    ck("作答结果被正常 emit（ok=true）", _ok, _txt[:200])
except NameError as e:
    ck("main() 正常返回（没有 NameError 崩掉）", False, repr(e))
finally:
    au._get, au._post, au.time.sleep = _REAL_GET, _REAL_POST, _REAL_SLEEP
    sys.argv = _REAL_ARGV


print("")
print("PASS=%d FAIL=%d" % (P, F))
if F:
    print("失败项: " + ", ".join(FAILS))
    sys.exit(1)
