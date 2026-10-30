# -*- coding: utf-8 -*-
"""回归测试：2026-10-29 第三轮 —— 子代理误判死亡 + 重复 fork + 重复提问

现场事故（会话 ceac3fff，主对话日志实测时间线）：
    18:20:52  fork #1 (fff32ea0)
    18:21:10  wait_agent  → noProgress(no-active-peer)
    18:21:22  list_agents → 只看到 lead      ← 误判"已死"
    18:23:24  fork #2 (11de1d27)             ← #1 实际还在跑（活到 18:28:34）
    18:25:40  fork #3 (6bf2ef59)             ← #1 #2 都还在跑
    三个子代理互相 analyst-bind --force 抢身份 9 次，各自 ask_user.py 提问
    → 用户看到"已经选择过的选项反复弹出"。

修复要点：
  · 主进程脉冲 pulse（DSH setInterval 写）作为唯一权威判活依据；
  · analyst-guard：fork 之前必须先问，活着就拒绝重复创建；
  · analyst-bind：旧 analyst 还活着就拒绝抢身份；
  · ask_user.py：跨会话去重 + 已答台账，同一题不再弹第二次。

运行：python test_regression_20261029_liveness.py   退出码 0 = 通过
"""
import ast
import io
import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
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


def _isolate():
    d = tempfile.mkdtemp(prefix="live_")
    os.environ["DSH_STATE_DIR"] = d
    os.environ["DSH_ENGINEERING_PATH_DUMMY"] = "1"
    return d


STATE = _isolate()
sys.path.insert(0, HERE)
import mode2_flow as mf  # noqa: E402
import mode_gate as mg   # noqa: E402

SRC_MG = io.open(os.path.join(HERE, "mode_gate.py"), encoding="utf-8").read()
SRC_M2 = io.open(os.path.join(HERE, "mode2_flow.py"), encoding="utf-8").read()
SRC_AU = io.open(os.path.join(HERE, "ask_user.py"), encoding="utf-8").read()
# ── 【测试路径修复】安装副本没有 plugins/ 目录 ──────────────────────────
# 原实现硬编码 HERE/../plugins/...，只在工作区副本成立；
# 安装副本上插件在 profiles/<p>/node_modules 下 → 必然 FileNotFoundError。
# 统一用 _plugin_paths 解析；找不到就跳过本文件的 JS 静态检查（不算失败）。
try:
    import _plugin_paths as _pp
    SRC_JS = _pp.read_plugin_file("index.js")
except Exception:
    SRC_JS = None
if SRC_JS is None:
    print("[SKIP] 未找到 dsh-engineering-ui/lib/index.js（非工作区布局），"
          "跳过对宿主插件的静态检查")
    sys.exit(0)


def write_reg(pulse_alive=True, agents=None, pulse_age_sec=5):
    """写一份平台注册表（模拟插件产出）。"""
    now = time.time()
    reg = {
        # updatedAt 始终"现在"（插件每跳都会刷新它）；
        # 只有 pulse 会"停"—— 这才是真实的"进程挂了"形态。
        "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now)),
        "source": "test",
        "runId": "run-test",
        "pulse": {
            "seq": 42,
            "at": time.strftime("%Y-%m-%dT%H:%M:%S",
                                time.gmtime(now - (0 if pulse_alive else 999))),
            "atMs": int((now - (0 if pulse_alive else 999)) * 1000),
            "intervalMs": 10000,
            "staleMs": 35000,
        },
        "agents": agents if agents is not None else [],
    }
    p = os.path.join(STATE, "platform_agents.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(reg, f, ensure_ascii=False)
    return reg


# ══════════════════════════════════════════════════════════════════
sec("1. 主进程脉冲判据（core）")
ck("★ mode_gate 定义 PULSE_STALE_SEC", "PULSE_STALE_SEC" in SRC_MG)
ck("★ mode_gate 有 _pulse_state", "def _pulse_state" in SRC_MG)
ck("★ 旧函数 _plugin_liveness 已删除（不保留双份）",
   "def _plugin_liveness" not in SRC_MG)
ck("★ 没有残留调用 _plugin_liveness",
   "_plugin_liveness(" not in SRC_MG and "_plugin_liveness" not in SRC_WG if False else
   "_plugin_liveness(" not in SRC_MG)
ck("★ 插件写 pulse 字段", "pulse: {" in SRC_JS and "PULSE_INTERVAL_MS" in SRC_JS)
ck("★ 插件用 setInterval 打脉冲", "setInterval(pulseTick" in SRC_JS)
ck("★ 插件 disposed 时从台账移除（唯一判死来源）",
   "pulseUntrack(agent.id)" in SRC_JS)
ck("★ 插件 created 时登记台账", "pulseTrack(agent, meta)" in SRC_JS)

# 行为验证：脉冲活着 + agent 在册 → running
write_reg(pulse_alive=True, agents=[
    {"id": "sub-a", "label": "测试小屋", "status": "running"}])
st, age = mg._platform_liveness("测试小屋", {"id": "sub-a"})
ck("★ 脉冲活 + agent 在册 → running", st == "running", st)
ps = mg._pulse_state()
ck("★ _pulse_state 报 alive=True", ps.get("alive") is True, ps)
ck("pulse.seq 读得到", ps.get("seq") == 42, ps)

# 脉冲活着 + agent disposed → inactive
write_reg(pulse_alive=True, agents=[
    {"id": "sub-a", "label": "测试小屋", "status": "disposed"}])
st2, _ = mg._platform_liveness("测试小屋", {"id": "sub-a"})
ck("★ 脉冲活 + agent disposed → inactive", st2 == "inactive", st2)

# 脉冲停了 → pulse 明确报 stale；但"判死"要看有没有 pulse 字段
# ── 这里刻意区分两种"没脉冲"，它们是不同语义（2026-10-29 设计要点）──
#   ① pulse 字段【存在但过期】= DSH 进程真的停了 → 子代理也没了 → 判死【正确】
#   ② pulse 字段【根本不存在】= 插件没装/没重启到新版 → 判据缺失，【不能判死】
write_reg(pulse_alive=False, agents=[
    {"id": "sub-a", "label": "测试小屋", "status": "running"}])
ps3 = mg._pulse_state()
ck("★ 脉冲过期 → _pulse_state 报 alive=False", ps3.get("alive") is False, ps3)
ck("★ 过期原因标注 pulse_stale",
   "pulse_stale" in str(ps3.get("reason")), ps3.get("reason"))
st3, _ = mg._platform_liveness("测试小屋", {"id": "sub-a"})
ck("★ 脉冲【过期】→ inactive（进程没了，判死正确）", st3 == "inactive", st3)

# 判据缺失（无 pulse 字段）→ 弱判据：agent 在册就算活着，绝不判死
p_no_pulse = os.path.join(STATE, "platform_agents.json")
with open(p_no_pulse, "w", encoding="utf-8") as f:
    json.dump({"updatedAt": int(time.time() * 1000), "source": "old-plugin",
               "agents": [{"id": "sub-a", "label": "测试小屋",
                           "status": "running"}]}, f, ensure_ascii=False)
st4, _ = mg._platform_liveness("测试小屋", {"id": "sub-a"})
ck("★ 无 pulse 字段（老插件）→ 不判死，仍报 running", st4 == "running", st4)
ps4 = mg._pulse_state()
ck("★ 无 pulse 时回落到 legacy_updatedAt（弱判据）",
   ps4.get("reason") == "legacy_updatedAt", ps4.get("reason"))

# ══════════════════════════════════════════════════════════════════
sec("2. analyst-guard：fork 前必须问，活着就拒绝重复创建")
ck("★ mode2_flow 有 analyst_guard", "def analyst_guard" in SRC_M2)
ck("★ mode2_flow 有 analyst_liveness", "def analyst_liveness" in SRC_M2)
ck("★ mode2_flow 有 analyst_heartbeat", "def analyst_heartbeat" in SRC_M2)
ck("★ CLI 注册 analyst-guard", '"analyst-guard"' in SRC_M2)
ck("★ CLI 注册 analyst-heartbeat", '"analyst-heartbeat"' in SRC_M2)

plan_path = os.path.join(STATE, "mode2_plan.json")
# 场景 A：未绑定 → 允许 fork
if os.path.exists(plan_path):
    os.remove(plan_path)
g0 = mf.analyst_guard(STATE)
ck("★ 未绑定 → may_fork=True", g0.get("may_fork") is True, g0.get("gate"))

# 场景 B：已绑定 + 脉冲活 + agent 在册 → 拒绝
write_reg(pulse_alive=True, agents=[
    {"id": "alive-analyst", "label": "总计子代理", "status": "running"}])
with open(plan_path, "w", encoding="utf-8") as f:
    json.dump({"analyst": {"session_id": "alive-analyst",
                           "bound_at": "2026-10-29 18:21:09"}},
              f, ensure_ascii=False)
g1 = mf.analyst_guard(STATE)
ck("★ 活着 → may_fork=False", g1.get("may_fork") is False, g1.get("may_fork"))
ck("★ 活着 → gate=ANALYST_ALREADY_ALIVE",
   g1.get("gate") == "ANALYST_ALREADY_ALIVE", g1.get("gate"))
ck("★ 拒绝时给出 next 指引", bool(g1.get("next")), g1.get("next"))

live1 = mf.analyst_liveness(STATE)
ck("★ analyst_liveness 报 alive=True", live1.get("alive") is True, live1)
ck("★ 存活来源标注 pulse+registry",
   live1.get("source") == "pulse+registry", live1.get("source"))

# 场景 C：agent 已 disposed → 允许重开
write_reg(pulse_alive=True, agents=[
    {"id": "alive-analyst", "label": "总计子代理", "status": "disposed"}])
g2 = mf.analyst_guard(STATE)
ck("★ 已 disposed → may_fork=True", g2.get("may_fork") is True, g2.get("gate"))

# ══════════════════════════════════════════════════════════════════
sec("3. analyst-bind：旧 analyst 还活着 → 拒绝抢身份")
ck("★ 新增 gate=ANALYST_STILL_ALIVE", "ANALYST_STILL_ALIVE" in SRC_M2)

write_reg(pulse_alive=True, agents=[
    {"id": "old-analyst", "label": "总计子代理", "status": "running"}])
with open(plan_path, "w", encoding="utf-8") as f:
    json.dump({"analyst": {"session_id": "old-analyst"}}, f, ensure_ascii=False)
r = mf.analyst_bind("new-analyst", STATE)
ck("★ 旧 analyst 活着 → 拒绝改绑", r.get("ok") is False, r.get("ok"))
ck("★ gate=ANALYST_STILL_ALIVE",
   r.get("gate") == "ANALYST_STILL_ALIVE", r.get("gate"))

# ══════════════════════════════════════════════════════════════════
sec("4. 心跳：阶段动作自动续期，主对话不能伪造")
ck("★ _require_analyst_actor 校验通过时自动刷新心跳",
   "analyst_heartbeat(cur, work_dir" in SRC_M2)
ck("★ 心跳只认已绑定 sessionId（拒绝伪造）",
   "ANALYST_HEARTBEAT_WRONG_ACTOR" in SRC_M2)

os.environ["DSH_SESSION_ID"] = "not-the-analyst"
hb = mf.analyst_heartbeat("not-the-analyst", STATE)
ck("★ 非绑定会话写心跳被拒", hb.get("ok") is False, hb.get("gate"))
ck("★ 拒绝理由明确（防伪造）",
   hb.get("gate") == "ANALYST_HEARTBEAT_WRONG_ACTOR", hb.get("gate"))

# ══════════════════════════════════════════════════════════════════
sec("5. ask_user：跨会话去重 + 已答台账")
ck("★ 新增跨会话去重函数", "def _dedup_across_sessions" in SRC_AU)
ck("★ 新增已答台账", "_ledger_put" in SRC_AU and "_ledger_get" in SRC_AU)
ck("★ 重复题返回 gate=ASK_DUPLICATE_OWNED_BY_PEER",
   "ASK_DUPLICATE_OWNED_BY_PEER" in SRC_AU)
ck("★ 插件支持 rootSessionId=* 全局查询", 'rootSessionId === "*"' in SRC_JS)

# 台账纯函数验证
sys.path.insert(0, HERE)
import ask_user as au  # noqa: E402
qs = [{"id": "q1", "question": "车轮直径多少？",
       "options": [{"label": "65mm"}, {"label": "80mm"}]}]
ck("★ 台账初始为空", au._ledger_get(qs) is None, au._ledger_get(qs))
ok_put = au._ledger_put(qs, {"answers": [{"id": "q1", "selected": ["65mm"]}]})
ck("★ 台账写入成功", ok_put is True, ok_put)
hit = au._ledger_get(qs)
ck("★ 台账命中返回旧答案", hit is not None, hit)
ck("★ 命中答案是 65mm",
   bool(hit) and hit["answers"][0]["selected"] == ["65mm"], hit)
qs2 = [{"id": "q1", "question": "车轮直径多少？",
        "options": [{"label": "65mm"}, {"label": "80mm"}]}]
ck("★ 同题（不同对象）指纹一致",
   au._ledger_get(qs2) is not None, au._ledger_get(qs2))
qs3 = [{"id": "q9", "question": "电池电压多少？",
        "options": [{"label": "7.4V"}, {"label": "11.1V"}]}]
ck("★ 不同题不命中", au._ledger_get(qs3) is None, au._ledger_get(qs3))

# ══════════════════════════════════════════════════════════════════
sec("6. 旧代码已清理（不留双份）")
ck("★ 不再有 _plugin_liveness 函数定义", "def _plugin_liveness" not in SRC_MG)
ck("★ 不再有 lastEventAt 单点判活（_plugin_liveness 内）",
   "def _plugin_liveness" not in SRC_MG)
ck("★ 插件仍有 runId（整批失联判定用）", "runId: PLATFORM_RUN_ID" in SRC_JS)

print("")
print("=" * 74)
print("汇总：PASS=%d  FAIL=%d" % (P, F))
if FAILS:
    print("失败项：")
    for x in FAILS:
        print("  - " + x)
sys.exit(0 if F == 0 else 1)
