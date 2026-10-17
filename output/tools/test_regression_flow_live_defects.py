# -*- coding: utf-8 -*-
r"""回归测试：锁住两个实测缺陷（用户现场反馈，2026-10-13）

════════════════════════════════════════════════════════════════════════════
缺陷 A：A/B/C 与 D/E 被问了【两次】
  现象（用户原话）："A，B，C，D，E这俩个问题合集问了俩次，疑似是旧问题没有删完"
  根因：流程重排后 A/B/C + D/E 已是【第 1 步 init】的职责，
        但 provide_context 里【两处旧代码】仍在输出它们：
          · L3102 `_fixed_qs = _cc.questions_payload()...`（残留死代码）
          · L3278 `mandatory_questions = _fixed_qs`（mechanics_done 阶段又发一遍）
  修复：两处均删除；mechanics_done 改为回传 `choice_already_asked=true`
        与已收集值，不再重复提问。

缺陷 B：总计子代理【没用出来】、后续步骤全不生效
  现象（用户原话）："总计子代理没用出来…其他的步骤都没有生效"
  根因有两层：
    ① 安装副本残留 4 个旧流程僵尸房间（结构件/传动机构/壳体机架/支撑结构），
       心跳约 486s、无产物、无子代理，却因 SW_STALE_GRACE=600 被判"活着"
       → init 被 INIT_BLOCKED_ALIVE_SUBAGENTS 永久拒绝 → 新流程根本进不去。
    ② select 在【未登记编制】时仍然放行，并用代码默认的旧 5 房间表继续
       → 流程当场跳到"建零件"，总计子代理永远不会被启动。
  修复：① init 增加僵尸判定（无子代理+无产物+心跳>60s → 判僵尸并自动清理）；
        ② select 未登记编制时 fail-closed 拒绝（gate=MODE2_SUBAGENT_PLAN_REQUIRED），
           并给出"先跑总计子代理"的可执行指引。
"""
import json
import os
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
WG = os.path.join(HERE, "workflow_gate.py")
M2 = os.path.join(HERE, "mode2_flow.py")

P = F = 0
def ck(label, cond, extra=""):
    global P, F
    if cond:
        P += 1; print("  [PASS] " + label)
    else:
        F += 1; print("  [FAIL] " + label + ("  " + str(extra) if extra else ""))
def sec(t):
    print("\n" + "=" * 74); print(t); print("=" * 74)


def run(script, *args):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.run([sys.executable, script, *args],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=HERE, env=env)
    try:
        return json.loads(p.stdout or "{}")
    except Exception:
        return {"_raw": (p.stdout or "")[-400:]}


sec("缺陷 A：A/B/C 与 D/E 只能问一次（不得重复）")
src = open(WG, encoding="utf-8").read()
# ① 源码层：不得再出现可执行的 _fixed_qs 赋值 / mandatory_questions 输出
_lines = src.split("\n")
_live_fixed = [i + 1 for i, l in enumerate(_lines)
               if "_fixed_qs =" in l and not l.strip().startswith("#")]
ck("★ 无【可执行】的 _fixed_qs 赋值（旧重复源已删）", not _live_fixed, _live_fixed)
_live_mand = [i + 1 for i, l in enumerate(_lines)
              if "mandatory_questions" in l and not l.strip().startswith("#")]
ck("★ 无 mandatory_questions 输出（mechanics_done 不再重复问）",
   not _live_mand, _live_mand)
# ② 行为层：真跑一遍，确认只有 init 给出 A/B/C + D/E
run(WG, "reset")
r0 = run(WG, "init", "回归测试：六轴机械臂")
ck("init 是第 1 步（确认模式）", r0.get("step") == "mode_confirm_asked", r0.get("step"))
_msg0 = str(r0.get("message") or "")
ck("★ init 消息含 A/B/C（搭建方式）", "A = 完全自主搭建" in _msg0 and "B = 部分自主" in _msg0)
ck("★ init 消息含 D/E（并行策略）", "D = 部分小屋并行" in _msg0 and "E = 单小屋串联" in _msg0)

r1 = run(WG, "provide_context", "D 并行, B 部分自主")
ck("pc#1 进入强/弱需求（不再问 A/B/C+D/E）", r1.get("step") == "depth_asked", r1.get("step"))
ck("★ pc#1 不再含 A/B/C 选项", "A = 完全自主搭建" not in str(r1.get("message") or ""))
ck("pc#1 解析出预选项", (r1.get("parsed") or {}).get("parallel") == "D",
   r1.get("parsed"))

r2 = run(WG, "provide_context", "强需求")
ck("pc#2 进入按需提问指引", r2.get("step") == "context_asked", r2.get("step"))
ck("★ pc#2 不再含 A/B/C 选项", "A = 完全自主搭建" not in str(r2.get("message") or ""))

r3 = run(WG, "provide_context", "载荷 100N，臂展 800mm")
ck("pc#3 进入 mechanics_done", r3.get("step") == "mechanics_done", r3.get("step"))
ck("★ pc#3 不再输出 mandatory_questions", "mandatory_questions" not in r3)
ck("★ pc#3 标记 choice_already_asked=true", r3.get("choice_already_asked") is True)
ck("★ pc#3 回传已收集的选择", r3.get("collected_parallel") == "D",
   r3.get("collected_parallel"))

sec("缺陷 B-②：未登记编制时 select 必须拒绝（否则总计子代理永不启动）")
# 注意：pc#3 已推进到 mechanics_done，select 需要的 choice/parallel 从 state 取。
#   select 未登记编制时应直接拒绝。
r4 = run(WG, "select", "B,D")
ck("★ 未登记编制 → select 被拒绝", r4.get("ok") is False, r4.get("ok"))
ck("★ gate = MODE2_SUBAGENT_PLAN_REQUIRED",
   r4.get("gate") == "MODE2_SUBAGENT_PLAN_REQUIRED", r4.get("gate"))
ck("★ 拒绝时给出可执行指引（how 非空）", bool(r4.get("how")), r4.get("how"))
ck("★ 指引里点名『总计子代理』", "总计子代理" in str(r4.get("error")) + str(r4.get("why")))
ck("★ 指引含 set-subagent-plan 命令",
   any("set-subagent-plan" in str(x) for x in (r4.get("how") or [])))
ck("  拒绝时不生成旧房间表（rooms 为空）",
   not ((r4.get("subagent_config") or {}).get("rooms") or []))

sec("缺陷 B-②（正路）：登记编制后 select 放行，房间表由编制决定 + 追加总理")
# 从 select 的拒绝返回里取计划文件所在目录（= 本任务的交付目录）
wd = r4.get("plan_file")
wd = os.path.dirname(wd) if wd else None
if not wd or not os.path.isdir(wd):
    wd = os.getcwd()
print("  （计划目录：%s）" % wd)
tmp = tempfile.mkdtemp()
rooms = [
    {"name": "臂体结构", "type": "structural", "parts": ["臂管"],
     "why": "承载", "depends_on": []},
    {"name": "关节传动", "type": "transmission", "parts": ["减速器"],
     "why": "传动", "depends_on": ["臂体结构"]},
    {"name": "总装与验证", "type": "assembly", "parts": ["总装"],
     "why": "装配验证", "depends_on": ["关节传动"]},
    {"name": "工程图输出", "type": "drafting", "parts": ["图纸"],
     "why": "出图", "depends_on": ["总装与验证"]},
]
rj = os.path.join(tmp, "rooms.json")
open(rj, "w", encoding="utf-8").write(json.dumps(rooms, ensure_ascii=False))
r5 = run(M2, "set-subagent-plan", "--rooms-json", "@" + rj,
         "--rationale", "回归测试：按功能分解", "--decided-by", "总计子代理",
         "--work-dir", wd)
ck("总计子代理登记编制成功", r5.get("ok") is True, r5)
ck("  登记数量 = 编制数量", r5.get("count") == 4, r5.get("count"))

r6 = run(WG, "select", "B,D")
ck("★ 登记后 select 放行", r6.get("ok") is True,
   {"gate": r6.get("gate"), "error": r6.get("error")})
_names = [x[0] if isinstance(x, list) else x.get("name")
          for x in ((r6.get("subagent_config") or {}).get("rooms") or [])]
ck("★ 房间表 = 总计子代理的编制（非旧默认表）",
   "臂体结构" in _names and "关节传动" in _names, _names)
ck("★ 不含旧流程的『结构件/传动机构/壳体机架』默认表",
   "结构件" not in _names and "传动机构" not in _names, _names)
ck("★ 总理子代理被固定追加", "总理子代理" in _names, _names)
ck("★ rooms_source 标注为总计子代理",
   "总计子代理" in str((r6.get("subagent_config") or {}).get("rooms_source")),
   (r6.get("subagent_config") or {}).get("rooms_source"))
_pr = r6.get("prime_room") or {}
ck("★ 总理子代理职责含『校验相邻尺寸一致性』",
   any(("相邻" in str(d) and "一致" in str(d)) for d in (_pr.get("duties") or [])),
   _pr.get("duties"))
ck("★ 总理子代理职责含『汇总 docx』",
   any("docx" in str(d) for d in (_pr.get("duties") or [])), _pr.get("duties"))

sec("缺陷 B-①：僵尸房间不得永久挡住 init")
src2 = open(WG, encoding="utf-8").read()
ck("★ init 增加了僵尸判定（_room_maybe_working）",
   "_room_maybe_working" in src2)
ck("★ 僵尸判定要求『有子代理 或 有产物 或 心跳<60s』",
   "产物=True" in src2 and "< 60.0" in src2)
ck("★ 清理后会把僵尸名单回传（zombies_cleaned）",
   "zombies_cleaned" in src2)
ck("★ 仍然保留对『真活』房间的硬拦（不误杀）",
   "INIT_BLOCKED_ALIVE_SUBAGENTS" in src2 and "_alive_real" in src2)

run(WG, "reset")
print("\n" + "=" * 74)
print("实测缺陷回归：PASS=%d  FAIL=%d" % (P, F))
print("=" * 74)
sys.exit(1 if F else 0)
