# -*- coding: utf-8 -*-
"""回归测试：2026-10-28 重构 —— 总计子代理必须真实存在 + 子代理不得跑编排命令

覆盖：
  1) select 在 analyst 未绑定 → MODE2_ANALYST_REQUIRED（不许拿主对话自演的十阶段开工）
  2) 主对话越权调 set-subagent-plan → MODE2_ANALYST_REQUIRED
  3) analyst 绑定 + 十阶段走完 + 编制登记 → 放行；wave1 必须含【总理子代理】
  4) 绑定后主对话调 begin-stage → ANALYST_ACTOR_REQUIRED（身份不匹配）
  5) 子代理会话调 init → ROOT_SESSION_REQUIRED（三个小屋各自重跑门禁的根因）

运行：python test_regression_analyst_gate.py     退出码 0 = 通过
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PASS = 0
FAIL = 0
FAILURES = []


def check(label, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] %s" % label)
    else:
        FAIL += 1
        FAILURES.append(label)
        print("  [FAIL] %s %s" % (label, str(extra)[:500]))


def _env(state_dir, session):
    e = dict(os.environ)
    e["DSH_STATE_DIR"] = state_dir
    e["DSH_ENGINEERING_ROOT"] = os.path.dirname(TOOLS)
    e["DSH_SESSION_ID"] = session
    e["PYTHONIOENCODING"] = "utf-8"
    return e


def run(script, args, state_dir, session):
    p = subprocess.run([sys.executable, os.path.join(TOOLS, script)] + args,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=_env(state_dir, session), cwd=TOOLS)
    out = (p.stdout or "") + (p.stderr or "")
    try:
        return json.loads(out)
    except Exception:
        return {"_raw": out, "_code": p.returncode}


STAGES = ["TaskUnderstanding", "FunctionalDecomposition", "PhysicsReasoning",
          "PrincipleSynthesis", "MechanismSynthesis", "RecursivePartReasoning",
          "PartSynthesis", "EngineeringValidation", "DesignIteration",
          "ExplainableDesignOutput"]

ROOMS = [
    {"name": "结构件", "type": "structural", "parts": ["支架"], "why": "承力"},
    {"name": "总装与验证", "type": "assembly", "parts": ["DSH_总装"], "why": "总装"},
    {"name": "工程图输出", "type": "drafting", "parts": ["DSH_图纸"], "why": "出图"},
]


def main():
    d = tempfile.mkdtemp(prefix="analyst_gate_")
    try:
        now = time.time()
        wf = {
            "step": "mechanics_done", "task": "测试任务：小型支架",
            "task_id": "TESTTASK001", "work_dir": d,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "session_started_ts": now - 1000,
            "depth_asked_ts": now - 900,
            "context_asked_ts": now - 800,
            "mechanics_done_ts": now - 700,
            "param_depth": "lite", "context": "x",
        }
        with open(os.path.join(d, "workflow_state.json"), "w", encoding="utf-8") as f:
            json.dump(wf, f, ensure_ascii=False, indent=2)

        print("=" * 70)
        print("1. analyst 未绑定 → select 必须被拒")
        r = run("workflow_gate.py", ["select", "A,D"], d, "MAIN-SESSION")
        check("★ select gate = MODE2_ANALYST_REQUIRED",
              r.get("gate") == "MODE2_ANALYST_REQUIRED", r)

        print("=" * 70)
        print("2. 主对话越权登记编制 → 拒")
        r = run("mode2_flow.py",
                ["set-subagent-plan", "--rooms-json", json.dumps(ROOMS, ensure_ascii=False),
                 "--rationale", "测试", "--work-dir", d], d, "MAIN-SESSION")
        check("★ 未绑定 analyst 时 set-subagent-plan 被拒",
              r.get("gate") == "MODE2_ANALYST_REQUIRED", r)

        print("=" * 70)
        print("3. analyst 绑定 → 主对话再调阶段命令 → 身份不匹配")
        r = run("mode2_flow.py", ["analyst-bind", "--work-dir", d], d, "ANALYST-SESSION")
        check("analyst-bind 成功", r.get("ok") is True, r)
        r = run("mode2_flow.py", ["set-depth", "lite"], d, "ANALYST-SESSION")
        check("set-depth 成功", r.get("ok") is True, r)
        r = run("mode2_flow.py", ["begin-stage", "TaskUnderstanding"], d, "MAIN-SESSION")
        check("★ 主对话调 begin-stage → ANALYST_ACTOR_REQUIRED",
              r.get("gate") == "ANALYST_ACTOR_REQUIRED", r)

        print("=" * 70)
        print("4. analyst 走完十阶段 + 登记编制 → 放行")
        for s in STAGES:
            run("mode2_flow.py", ["begin-stage", s], d, "ANALYST-SESSION")
            run("mode2_flow.py", ["mark-stage-done", s, "--artifact", "art-" + s],
                d, "ANALYST-SESSION")
        r = run("mode2_flow.py", ["stage-status"], d, "ANALYST-SESSION")
        check("十阶段 10/10 完成", r.get("done_count") == 10, r.get("done_count"))
        r = run("mode2_flow.py",
                ["set-subagent-plan", "--rooms-json", json.dumps(ROOMS, ensure_ascii=False),
                 "--rationale", "测试", "--work-dir", d], d, "ANALYST-SESSION")
        check("analyst 登记编制成功", r.get("ok") is True, r)
        r = run("workflow_gate.py", ["select", "A,D"], d, "MAIN-SESSION")
        check("★ select 放行", r.get("ok") is True, r.get("error"))
        names = [x.get("name") for x in (r.get("pending_rooms") or [])]
        check("★ wave1 同时包含建模房间与总理子代理",
              "结构件" in names and "总理子代理" in names, names)
        check("总理子代理已在房间表中",
              any(x[0] == "总理子代理" for x in
                  ((r.get("subagent_config") or {}).get("rooms") or [])), names)

        print("=" * 70)
        print("5. 子代理会话调编排命令 → ROOT_SESSION_REQUIRED")
        ms = {"mode": "2", "rooms": {"结构件": {"active": True, "started_at": now}},
              "subagents": {"结构件": {"id": "SUB-X", "assigned_at": now}}}
        with open(os.path.join(d, "mode_state.json"), "w", encoding="utf-8") as f:
            json.dump(ms, f, ensure_ascii=False, indent=2)
        r = run("workflow_gate.py", ["init", "另一个任务"], d, "SUB-X")
        check("★ 子代理调 init → ROOT_SESSION_REQUIRED",
              r.get("gate") == "ROOT_SESSION_REQUIRED", r)
        r = run("workflow_gate.py", ["select", "A,D"], d, "SUB-X")
        check("★ 子代理调 select → ROOT_SESSION_REQUIRED",
              r.get("gate") == "ROOT_SESSION_REQUIRED", r)

        print("=" * 70)
        print("6. 平台注册表存在时：主会话不能自封总计子代理")
        d2 = tempfile.mkdtemp(prefix="analyst_gate2_")
        try:
            with open(os.path.join(d2, "workflow_state.json"), "w", encoding="utf-8") as f:
                json.dump({"step": "mechanics_done", "task": "t2",
                           "task_id": "T2", "work_dir": d2}, f, ensure_ascii=False)
            with open(os.path.join(d2, "platform_agents.json"), "w", encoding="utf-8") as f:
                json.dump({"updatedAt": int(now), "source": "dsh-engineering-ui",
                           "agents": [{"id": "SUB-A", "label": "总计子代理（十阶段分析）",
                                       "status": "running", "createdAt": now}]},
                          f, ensure_ascii=False)
            r = run("mode2_flow.py", ["analyst-bind", "--work-dir", d2], d2, "MAIN-SESSION")
            check("★ 主会话自封 analyst → ANALYST_BIND_REQUIRES_SUBAGENT",
                  r.get("gate") == "ANALYST_BIND_REQUIRES_SUBAGENT", r)
            r = run("mode2_flow.py", ["analyst-bind", "--work-dir", d2], d2, "SUB-A")
            check("★ 真实子代理 analyst-bind → 成功", r.get("ok") is True, r)
        finally:
            shutil.rmtree(d2, ignore_errors=True)

        print("=" * 70)
        print("汇总：PASS=%d FAIL=%d" % (PASS, FAIL))
        if FAILURES:
            print("失败项：")
            for x in FAILURES:
                print("  - " + x)
        return 0 if FAIL == 0 else 1
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
