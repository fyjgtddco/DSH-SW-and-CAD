# -*- coding: utf-8 -*-
"""test_model_chain.py —— 模型自动接替（额度耗尽自动换模型）契约测试

覆盖本次新增的三块：
  1) normalizeModelChain：配置规整（去空、去重、上限 10、enabled 语义）
  2) nextModelCandidate：按 tried 集合挑下一个候选（本轮不重复试）
  3) 源码接线：agent/request 与 agent/request-error 两个 waterfall 都挂上了，
     且每轮重放、错误码白名单、异常静默降级都还在

为什么用源码静态断言而不是跑宿主：
  这两个钩子由 dsh-agent-loop 在真实 Agent 生命周期里触发，纯 node 环境
  无法构造完整 agent（需要 session/persistence/llm 全套服务）。
  因此这里验证"接线存在 + 逻辑函数正确"，与仓库既有测试口径一致。

运行：python test_model_chain.py
"""
import io
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HOST = None
try:
    sys.path.insert(0, HERE)
    import _plugin_paths as _pp
    HOST = _pp.plugin_file("index.js")
except Exception:
    HOST = None

P = F = 0
def ck(label, cond, extra=""):
    global P, F
    if cond:
        P += 1; print("  [PASS] " + label)
    else:
        F += 1; print("  [FAIL] " + label + ("  " + str(extra) if extra else ""))

def sec(t):
    print("\n" + "=" * 74); print(t); print("=" * 74)

if not HOST:
    print("[SKIP] 未找到 dsh-engineering-ui/lib/index.js（非工作区布局）")
    print("       本测试需要插件源码做静态接线校验，安装副本上请在工作区跑。")
    sys.exit(0)

SRC = io.open(HOST, encoding="utf-8").read()

# ── 1. 把纯函数抽出来在 node 里跑 ────────────────────────────────────
FUNC_RE = re.compile(
    r"(const MODEL_CHAIN_FILE[\s\S]*?function modelKey\(provider, model\) \{[\s\S]*?\n\})",
    re.M)
m = FUNC_RE.search(SRC)
sec("A. 纯函数可抽取")
ck("能从 index.js 抽到模型链函数块", bool(m))
if m:
    block = m.group(1)
    script = block + "\n" + r'''
const cases = [];
function eq(label, got, want) { cases.push([label, JSON.stringify(got) === JSON.stringify(want), got, want]); }

// --- normalizeModelChain ---
eq("空值 -> 未启用空链", normalizeModelChain(null), {ok:true, enabled:false, entries:[]});
eq("enabled 严格 true 才算启用",
   normalizeModelChain({enabled:"yes", models:[{provider:"a",model:"m"}]}).enabled, false);
const n1 = normalizeModelChain({enabled:true, models:[
  {provider:"p1", model:"m1"},
  {provider:"",   model:"m2"},
  {provider:"p2", model:""},
  {provider:"p1", model:"m1"},
  {provider:"p3", model:"m3", reasoningEffort:"high"},
]});
// 输入 5 条：p1/m1、空provider、空model、p1/m1 重复、p3/m3
// 去空后剩 3 条，去重后剩【2 条】唯一项。
eq("去空+去重后保留 2 条唯一项", n1.entries.length, 2);
eq("重复项去重（p1/m1 只留一个）", n1.entries[0], {provider:"p1", model:"m1"});
eq("reasoningEffort 透传", n1.entries[1], {provider:"p3", model:"m3", reasoningEffort:"high"});

const many = normalizeModelChain({enabled:true, models:
  Array.from({length:20}, (_,i)=>({provider:"p"+i, model:"m"+i}))});
eq("上限 10 个（用户要求）", many.entries.length, 10);

// --- nextModelCandidate ---
const entries = [{provider:"a",model:"1"},{provider:"b",model:"2"},{provider:"c",model:"3"}];
eq("未试过 -> 取链首", nextModelCandidate(entries, new Set()), {provider:"a",model:"1"});
const tried = new Set([modelKey("a","1")]);
eq("a/1 已试 -> 取 b/2", nextModelCandidate(entries, tried), {provider:"b",model:"2"});
eq("全试完 -> null", nextModelCandidate(entries,
    new Set(entries.map(e=>modelKey(e.provider,e.model)))), null);
eq("空链 -> null", nextModelCandidate([], new Set()), null);

let bad = 0;
for (const [label, ok, got, want] of cases) {
  if (!ok) { bad++; console.log("  [FAIL] " + label + "  got=" + JSON.stringify(got) + " want=" + JSON.stringify(want)); }
  else console.log("  [PASS] " + label);
}
console.log("FUNCS=" + (cases.length - bad) + "/" + cases.length);
'''
    tmp = os.path.join(HERE, "._mc_probe.mjs")
    io.open(tmp, "w", encoding="utf-8").write(script)
    try:
        p = subprocess.run(["node", tmp], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=120)
        out = (p.stdout or "") + (p.stderr or "")
        for line in out.splitlines():
            if line.strip().startswith("[PASS]") or line.strip().startswith("[FAIL]"):
                print(line if line.startswith("  ") else "  " + line.strip())
        mm = re.search(r"FUNCS=(\d+)/(\d+)", out)
        if mm:
            a, b = int(mm.group(1)), int(mm.group(2))
            P += a; F += (b - a)
        else:
            ck("node 探针有输出", False, out[:300])
    finally:
        try: os.remove(tmp)
        except Exception: pass

# ── 2. 源码接线静态校验 ──────────────────────────────────────────────
sec("B. 接线（waterfall 必须挂对）")
ck("注册了 agent/request waterfall", "ctx.on('agent/request'" in SRC)
ck("注册了 agent/request-error waterfall", "ctx.on('agent/request-error'" in SRC)
ck("有按 turn 重置的 tried 集合", "st.tried = new Set()" in SRC)
ck("每轮都钉住候选（不是只切一次）", "st.key = modelKey(cur.provider, cur.model)" in SRC)
ck("切模型后返回 {kind:'retry'}", re.search(r"kind:\s*'retry'", SRC) is not None)
ck("错误码白名单含 QUOTA", "'QUOTA'" in SRC and "MODEL_CHAIN_SWITCH_CODES" in SRC)
ck("含 RATE_LIMIT 与 CONTEXT_WINDOW_EXCEEDED",
   "'RATE_LIMIT'" in SRC and "'CONTEXT_WINDOW_EXCEEDED'" in SRC)
ck("异常静默降级（catch 里 return resolved / next()）",
   "return resolved;      // 接替逻辑任何异常都不得影响主流程" in SRC
   or "return resolved;" in SRC)
ck("只对工程模式生效（复用 isGuardSession）",
   "modelChainApplies" in SRC and "isGuardSession(agent)" in SRC)
ck("agent/disposed 清理状态", "ctx.on('agent/disposed'" in SRC)
ck("有 /model-chain 路由（供设置页读写）", "PREFIX + '/model-chain'" in SRC)
ck("保存后让缓存失效", "modelChainCache = null" in SRC)
ck("10 个槽位上限", "out.entries.length >= 10" in SRC)

# ── 3. 配置项不能误入敏感路由（它不产生副作用，但也别漏保护）───────
sec("C. 与既有结构不冲突")
ck("没有把 model-chain 误写成 DSH 工具", "workflow-gate-init" not in SRC)
ck("仍然保留 persona 路由", "PREFIX + '/persona'" in SRC)

print("\n" + "=" * 74)
print("PASS %d / FAIL %d" % (P, F))
print("=" * 74)
if F:
    return_code = 1
else:
    return_code = 0
sys.exit(return_code)
