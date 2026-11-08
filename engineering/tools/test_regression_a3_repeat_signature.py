# -*- coding: utf-8 -*-
"""【A3 回归测试】重复调用提醒可绕过 —— 后端语义归一化

现场（缺陷清单 A3 / P1）：
    同一件事重复调用时，只要把提示里的一个数字改掉就能绕过检测：
        "近8分钟:"  →  "intermediate 最新10:"
    检测器按【原始参数字符串】建链，改一个字符 = 一条新链 → 计数永远从 1 开始，
    重复提醒永远不触发。

根因：链路键是"完整参数原样 JSON"（宿主 @deepseek-ai/dsh-repeat-tool-reminder
    的 canonicalize() 只做深排序，不做任何语义归一化）。凡是参数里带
    数字 / 时间戳 / 路径 / 目录名的调用，改一个字符就绕过。

修复（本仓库后端部分）：
    workflow_gate.normalize_tool_signature(tool, args) 把参数按语义归一化
    （剥离数字/时间戳/"近N分钟"窗口/绝对路径/UUID/长 hex/结构性目录名），
    再算指纹；workflow_gate.same_call(a, b) 是"这两次调用算不算同一次"的唯一判据。
    CLI 只读入口：workflow_gate.py call-signature "<工具>" "<参数>"。

本测试锁定两件事（缺一不可）：
  ① 【不漏】改数字/时间戳/路径/目录名/空白 → 仍判为同一次调用（这才是 A3）；
  ② 【不误伤】房间名/命令名/选项字母/零件名不同 → 必须判为不同调用。

运行：python test_regression_a3_repeat_signature.py
退出码 0 = 通过
"""
import contextlib
import io
import json
import os
import sys
import tempfile

_ISO = tempfile.mkdtemp(prefix="a3_state_")
os.environ["DSH_STATE_DIR"] = _ISO
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import _store  # noqa: E402
_store.reset_cache()
import workflow_gate as wg  # noqa: E402

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
        print("  [FAIL] " + label + ("  " + str(extra)[:400] if extra else ""))


def sec(t):
    print("")
    print("=" * 74)
    print(t)
    print("=" * 74)


# ════════════════════════════════════════════════════════════════════
sec("① 【不漏】改数字 / 时间窗 / 目录名 → 仍算同一次调用（A3 的现场）")
# ════════════════════════════════════════════════════════════════════
# 这是缺陷文档里逐字引用的绕过样例
ck("★★ 缺陷原样复现：'近8分钟:' 与 'intermediate 最新10:' 判为同一次调用",
   wg.same_call("tool", "近8分钟: 检查",
                "tool", "intermediate 最新10: 检查") is True,
   (wg.normalize_tool_signature("tool", "近8分钟: 检查")["normalized"],
    wg.normalize_tool_signature("tool", "intermediate 最新10: 检查")["normalized"]))

ck("只改数字（8 → 10）→ 同一次调用",
   wg.same_call("tool", "看一下最近 8 条记录", "tool", "看一下最近 10 条记录"))
ck("只改单位（分钟 → 小时）→ 同一次调用",
   wg.same_call("tool", "近 8 分钟的心跳", "tool", "近 2 小时的心跳"))
ck("只改时间戳 → 同一次调用",
   wg.same_call("tool", "2026-11-07 23:10:00 检查",
                "tool", "2026-11-07 23:40:12 检查"))
ck("只改绝对路径 → 同一次调用",
   wg.same_call("read", '{"file_path":"C:\\a\\b.json"}',
                "read", '{"file_path":"C:\\x\\y.json"}'))
ck("只改 UUID → 同一次调用",
   wg.same_call("tool", "id=3f2a1b4c-1111-2222-3333-444455556666",
                "tool", "id=3f2a1b4c-7777-8888-9999-000011112222"))
ck("只改长 hex（run id）→ 同一次调用",
   wg.same_call("tool", "run=deadbeefdeadbeef",
                "tool", "run=0123456789abcdef"))
ck("只改空白/换行 → 同一次调用",
   wg.same_call("tool", "  foo   bar  ", "tool", "foo\nbar"))
ck("参数对象里只改尺寸数字 → 同一次调用（同一个零件同一个动作）",
   wg.same_call("add_part", {"name": "A", "dims": 25},
                "add_part", {"name": "A", "dims": 30}))
ck("dict 键顺序不同 → 同一次调用（深排序）",
   wg.same_call("add_part", {"name": "A", "dims": 25},
                "add_part", {"dims": 25, "name": "A"}))


# ════════════════════════════════════════════════════════════════════
sec("② 【不误伤】真正不同的调用必须判为不同（否则会误报重复）")
# ════════════════════════════════════════════════════════════════════
ck("★ 房间名不同 → 不是同一次调用（room-end 结构件 vs 传动件）",
   wg.same_call("room-end", "room-end 结构件",
                "room-end", "room-end 传动件") is False)
ck("★ 选项字母不同 → 不是同一次调用（select C vs select D）",
   wg.same_call("select", "select C", "select", "select D") is False)
ck("命令组合不同 → 不是同一次调用（C,E vs C,D）",
   wg.same_call("select", "select C,E", "select", "select C,D") is False)
ck("工具名不同 → 不是同一次调用",
   wg.same_call("read", "x", "write", "x") is False)
ck("零件名不同 → 不是同一次调用",
   wg.same_call("add_part", {"name": "A", "dims": 25},
                "add_part", {"name": "B", "dims": 25}) is False)
ck("中文实义词不同（底座 vs 立柱）→ 不是同一次调用",
   wg.same_call("ask", "确认底座参数", "ask", "确认立柱参数") is False)


# ════════════════════════════════════════════════════════════════════
sec("③ 归一化结构：签名稳定、噪声可追溯、不崩")
# ════════════════════════════════════════════════════════════════════
s1 = wg.normalize_tool_signature("tool", "近8分钟: 检查")
s2 = wg.normalize_tool_signature("tool", "intermediate 最新10: 检查")
ck("两次归一化的 signature 完全相同", s1["signature"] == s2["signature"],
   (s1["signature"], s2["signature"]))
ck("★ 归一化文本里已经不含原始数字（8 / 10 都被剥离）",
   "8" not in s1["normalized"] and "10" not in s2["normalized"],
   (s1["normalized"], s2["normalized"]))
ck("★ 结构性目录名 intermediate 被识别为噪声（noise_removed 可追溯）",
   "intermediate" in [str(x).lower() for x in s2["noise_removed"]],
   s2["noise_removed"])
ck("同一输入两次调用签名一致（纯函数，无时间依赖）",
   wg.normalize_tool_signature("read", '{"p":"C:\\a"}')["signature"] ==
   wg.normalize_tool_signature("read", '{"p":"C:\\a"}')["signature"])

# 边界输入不能抛异常
for _bad in (None, "", [], {}, 12345, {"a": [1, 2, {"b": None}]}):
    try:
        r = wg.normalize_tool_signature("tool", _bad)
        ck("边界输入 %r 返回 ok=True（不抛异常）" % (_bad,), r.get("ok") is True, r)
    except Exception as e:
        ck("边界输入 %r 返回 ok=True（不抛异常）" % (_bad,), False, repr(e))


# ════════════════════════════════════════════════════════════════════
sec("④ CLI 只读入口：call-signature（可被调用方直接验证）")
# ════════════════════════════════════════════════════════════════════

def _run_cli(argv):
    buf = io.StringIO()
    old = sys.argv
    sys.argv = ["workflow_gate.py"] + argv
    try:
        with contextlib.redirect_stdout(buf):
            wg._main_body()
    finally:
        sys.argv = old
    return json.loads(buf.getvalue() or "{}")


try:
    out = _run_cli(["call-signature", "tool", "近8分钟: 检查"])
except SystemExit as e:
    out = {"__exit__": e.code}
ck("CLI call-signature 输出合法 JSON 且 ok=true",
   isinstance(out, dict) and out.get("ok") is True, out)
ck("CLI 输出带 signature / normalized / noise_removed",
   all(k in out for k in ("signature", "normalized", "noise_removed")), list(out.keys()))

out2 = _run_cli(["call-signature", "tool", "intermediate 最新10: 检查"])
ck("★ CLI 两次调用同一件事 → 同一个 signature（绕不过去）",
   out.get("signature") == out2.get("signature"),
   (out.get("signature"), out2.get("signature")))

# help 里必须能查到（否则模型/调用方不知道有这个判据）
_buf = io.StringIO()
_old = sys.argv
sys.argv = ["workflow_gate.py", "--help"]
try:
    with contextlib.redirect_stdout(_buf):
        wg._main_body()
except SystemExit:
    pass
finally:
    sys.argv = _old
_help = json.loads(_buf.getvalue() or "{}")
ck("CLI 帮助里列出 call-signature",
   "call-signature" in (_help.get("commands") or {}),
   list((_help.get("commands") or {}).keys()))

import shutil  # noqa: E402
shutil.rmtree(_ISO, ignore_errors=True)

print("")
print("PASS=%d FAIL=%d" % (P, F))
if F:
    print("失败项: " + ", ".join(FAILS))
    sys.exit(1)
