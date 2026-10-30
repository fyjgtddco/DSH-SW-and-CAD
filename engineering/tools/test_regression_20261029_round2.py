# -*- coding: utf-8 -*-
"""回归测试：2026-10-29 第二次修 bug（门禁/编排四缺陷）

覆盖：
  G01 init 遇僵尸房间 → 自动清理并继续（不再 ZOMBIE_DETECTED）
  G03 ask_user 默认 host 取 DSH_WEB_URL；连不上 6s 内快速失败 + 降级指引
  G04 room-board 新增 detached 状态（绑定子代理已不在平台注册表）
  G05 analyst 已失联 + 十阶段完成 → 免 --force 自动接管
  G06 room-fail 后 room-start 默认按 fresh 清理（--keep 才保留）

运行：python test_regression_20261029_round2.py   退出码 0 = 通过
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = HERE
sys.path.insert(0, HERE)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["DSH_SESSION_ID"] = "test-analyst"

import mode2_flow as mf  # noqa: E402

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
    print("\n" + "=" * 72)
    print(t)
    print("=" * 72)


def run(script, args, state_dir, session="test-analyst"):
    env = dict(os.environ)
    env["DSH_STATE_DIR"] = state_dir
    env["DSH_ENGINEERING_ROOT"] = os.path.dirname(TOOLS)
    env["DSH_SESSION_ID"] = session
    env["PYTHONIOENCODING"] = "utf-8"
    # ⚠️ 必须清掉真实 GUI 地址：否则 ask_user 会连上【真宿主】并阻塞等用户作答
    #    （实测本测试因此挂住 540s）。这里只想验证"连不上时的降级路径"。
    for _k in ("DSH_HOST", "DSH_WEB_URL", "DSH_WEB_URL_ALT"):
        env.pop(_k, None)
    p = subprocess.run([sys.executable, os.path.join(TOOLS, script)] + args,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env, cwd=TOOLS)
    out = (p.stdout or "") + (p.stderr or "")
    # 用 raw_decode 从第一个 { 起解析【一个】完整 JSON（后面可能还有日志）
    _i = out.find("{")
    if _i >= 0:
        try:
            return json.JSONDecoder().raw_decode(out[_i:])[0]
        except Exception:
            pass
    try:
        return json.loads(out)
    except Exception:
        return {"_raw": out[-600:], "_rc": p.returncode}


def fresh():
    """每个用例独立临时状态目录，并在【父进程】也切过去。

    坑：只给子进程设 DSH_STATE_DIR，父进程里 import 的 mode2_flow 仍会读
    ~/.dsh 安装副本的 workflow_state —— 于是 current_task_id 取到的是【真实
    会话】的任务号，编制 task_id 对不上，room-start 被误拒，测试假失败。
    """
    d = tempfile.mkdtemp(prefix="r2_")
    os.environ["DSH_STATE_DIR"] = d
    try:
        import _store
        _store.reset_cache()
    except Exception:
        pass
    return d


def walk_stages(d):
    mf.set_depth("lite", d)
    for s in mf.STAGE_IDS:
        mf.begin_stage(s, d)
        mf.mark_stage_done(s, "art-" + s, d)


def main():
    # ── G01：僵尸房间自动清理 ────────────────────────────────────────
    sec("G01 init 遇僵尸房间 → 自动清理并继续")
    d = fresh()
    try:
        with open(os.path.join(d, "workflow_state.json"), "w", encoding="utf-8") as f:
            json.dump({"step": "user_selected", "task": "旧任务",
                       "task_id": "OLD", "user_choice": "C"}, f, ensure_ascii=False)
        with open(os.path.join(d, "mode_state.json"), "w", encoding="utf-8") as f:
            json.dump({"mode": "2",
                       "rooms": {"结构件": {"active": True, "started_at": time.time() - 7200}},
                       "subagents": {}, "sw_lock": {"owner": None, "queue": []}},
                      f, ensure_ascii=False)
        # 【2026-10-29】僵尸判定改走 pulse：这里放一份"进程已停"的注册表
        #   （脉冲很旧 = DSH 进程没了 = 上一轮房间确实没人跑 → 判僵尸正确）
        _old_ts = int((time.time() - 3600) * 1000)
        with open(os.path.join(d, "platform_agents.json"), "w", encoding="utf-8") as f:
            json.dump({"updatedAt": _old_ts, "source": "test",
                       "pulse": {"seq": 1, "atMs": _old_ts,
                                 "intervalMs": 10000, "staleMs": 35000},
                       "agents": []}, f, ensure_ascii=False)
        r = run("workflow_gate.py", ["init", "新任务：测试僵尸自愈"], d)
        ck("★ G01 init 不再返回 ZOMBIE_DETECTED", r.get("gate") != "ZOMBIE_DETECTED", r)
        ck("G01 init 成功进入第一步", r.get("ok") is True, r.get("gate") or r.get("error"))
        ck("G01 回传 zombies_cleaned", r.get("zombies_cleaned") == ["结构件"],
           r.get("zombies_cleaned"))
        ck("G01 回传 auto_clean_note", bool(r.get("auto_clean_note")), r.get("auto_clean_note"))
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ── G03：ask_user 默认 host ─────────────────────────────────────
    sec("G03 ask_user host 解析与快速失败")
    d = fresh()
    try:
        # 显式指向一个确定没人监听的端口 → 必定走快速失败路径
        r = run("ask_user.py", ["--room", "总计子代理", "--child", "child-x",
                                "--question", "测试问题", "--option", "A",
                                "--host", "http://127.0.0.1:59999",
                                "--timeout", "5"],
                d)
        ck("★ G03 连不上时 gate=ASK_BRIDGE_UNREACHABLE",
           r.get("gate") == "ASK_BRIDGE_UNREACHABLE", r.get("gate") or r.get("error"))
        ck("G03 给出父代理代问降级指引",
           "send_message" in str(r.get("hint") or ""), r.get("hint"))
        ck("G03 回传 fallback=parent_relay", r.get("fallback") == "parent_relay", r)
        ck("G03 有 tried_hosts 列表", bool(r.get("tried_hosts")), r.get("tried_hosts"))
    finally:
        shutil.rmtree(d, ignore_errors=True)
    # host 默认值来自 DSH_WEB_URL
    env = dict(os.environ)
    env.pop("DSH_HOST", None)
    env["DSH_WEB_URL"] = "http://127.0.0.1:65531"
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.run([sys.executable, "-c",
                        "import sys; sys.path.insert(0, r'%s'); import ask_user; "
                        "print(ask_user.DEFAULT_HOST)" % TOOLS],
                       capture_output=True, text=True, encoding="utf-8", env=env)
    ck("★ G03 默认 host 取 DSH_WEB_URL", "65531" in (p.stdout or ""), p.stdout)

    # ── G05：analyst 失联自动接管 ───────────────────────────────────
    sec("G05 旧 analyst 已 disposed + 十阶段完成 → 免 --force 接管")
    d = fresh()
    try:
        now = time.time()
        with open(os.path.join(d, "workflow_state.json"), "w", encoding="utf-8") as f:
            json.dump({"step": "mechanics_done", "task": "t", "task_id": "T1",
                       "work_dir": d}, f, ensure_ascii=False)
        # 用旧 analyst 绑定并走完十阶段（期间调用者身份必须切到 old-analyst，
        #   否则会被 ANALYST_ACTOR_REQUIRED 拒绝 —— 这正是身份闸门在工作）
        mf.analyst_bind("old-analyst", d)
        os.environ["DSH_SESSION_ID"] = "old-analyst"
        walk_stages(d)
        os.environ["DSH_SESSION_ID"] = "test-analyst"
        with open(os.path.join(d, "platform_agents.json"), "w", encoding="utf-8") as f:
            json.dump({"updatedAt": now, "source": "dsh-engineering-ui",
                       "agents": [{"id": "new-analyst", "label": "总计子代理（十阶段分析）",
                                   "status": "running", "createdAt": now},
                                  {"id": "old-analyst", "label": "旧总计子代理",
                                   "status": "disposed", "createdAt": now - 900}]},
                      f, ensure_ascii=False)
        # 【2026-10-29】新增闸门：旧 analyst 还活着时拒绝抢身份（ANALYST_STILL_ALIVE）。
        #   本用例测的是"旧 analyst 已失联 → 免 force 接管"，所以要让它的
        #   存活证据消失：脉冲活着 + old-analyst 标 disposed。
        _now_ms = int(time.time() * 1000)
        with open(os.path.join(d, "platform_agents.json"), "w", encoding="utf-8") as f:
            json.dump({"updatedAt": _now_ms, "source": "test",
                       "pulse": {"seq": 9, "atMs": _now_ms,
                                 "intervalMs": 10000, "staleMs": 35000},
                       "agents": [{"id": "new-analyst", "label": "总计子代理（接管）",
                                   "status": "running"},
                                  {"id": "old-analyst", "label": "旧总计子代理",
                                   "status": "disposed"}]},
                      f, ensure_ascii=False)
        r = run("mode2_flow.py", ["analyst-bind", "--work-dir", d], d, "new-analyst")
        ck("★ G05 免 --force 自动接管成功", r.get("ok") is True, r)
        ck("G05 回传 auto_rebind=True", r.get("auto_rebind") is True, r.get("auto_rebind"))
        ck("G05 说明自动接管原因",
           "失联" in str(r.get("auto_rebind_reason") or ""), r.get("auto_rebind_reason"))
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ── G06：room-fail 后 room-start 默认 fresh ─────────────────────
    sec("G06 room-fail 后 room-start 默认 fresh，--keep 保留")
    d = fresh()
    try:
        with open(os.path.join(d, "workflow_state.json"), "w", encoding="utf-8") as f:
            json.dump({"step": "user_selected", "task": "t", "task_id": "T1",
                       "work_dir": d}, f, ensure_ascii=False)
        # 需要编制才能 room-start：先绑定 + 十阶段 + 编制
        mf.analyst_bind("test-analyst", d)
        os.environ["DSH_SESSION_ID"] = "test-analyst"
        walk_stages(d)
        mf.set_subagent_plan(
            [{"name": "结构件", "type": "structural", "parts": ["p"], "why": "w"},
             {"name": "总装与验证", "type": "assembly", "parts": ["a"], "why": "w"},
             {"name": "工程图输出", "type": "drafting", "parts": ["d"], "why": "w"}],
            "测试", "总计子代理", d)
        with open(os.path.join(d, "mode_state.json"), "w", encoding="utf-8") as f:
            json.dump({"mode": "2", "rooms": {}, "subagents": {},
                       "sw_lock": {"owner": None, "queue": []}}, f, ensure_ascii=False)
        r = run("mode_gate.py", ["room-start", "结构件"], d)
        ck("首次 room-start 成功", r.get("ok") is True, r)
        run("mode_gate.py", ["room-fail", "结构件", "测试失败"], d)
        r2 = run("mode_gate.py", ["room-start", "结构件"], d)
        ck("★ G06 失败后 room-start 默认 fresh", r2.get("history_preserved") is False, r2)
        ck("G06 fresh_reason=auto_fresh_after_fail",
           r2.get("fresh_reason") == "auto_fresh_after_fail", r2.get("fresh_reason"))
        run("mode_gate.py", ["room-fail", "结构件", "再次失败"], d)
        r3 = run("mode_gate.py", ["room-start", "结构件", "--keep"], d)
        ck("G06 --keep 可保留历史", r3.get("history_preserved") is True, r3)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ── G04：detached 状态 ─────────────────────────────────────────
    sec("G04 room-board 新增 detached（绑定子代理已不在平台注册表）")
    d = fresh()
    try:
        now = time.time()
        with open(os.path.join(d, "workflow_state.json"), "w", encoding="utf-8") as f:
            json.dump({"step": "user_selected", "task": "t", "task_id": "T1",
                       "work_dir": d}, f, ensure_ascii=False)
        with open(os.path.join(d, "mode_state.json"), "w", encoding="utf-8") as f:
            json.dump({"mode": "2",
                       "rooms": {"结构件": {"active": True,
                                            "started_at": now - 3600}},
                       "subagents": {"结构件": {"id": "gone-sub",
                                                "assigned_at": now - 3600}},
                       "sw_lock": {"owner": None, "queue": []}}, f, ensure_ascii=False)
        # 【2026-10-29】detached 判定现在要求"脉冲活着"（进程在跳）
        #   + 注册表里【有别的 agent】但【没有它】。两条件缺一不可。
        _now_ms = int(now * 1000)
        with open(os.path.join(d, "platform_agents.json"), "w", encoding="utf-8") as f:
            json.dump({"updatedAt": _now_ms, "source": "dsh-engineering-ui",
                       "pulse": {"seq": 7, "atMs": _now_ms,
                                 "intervalMs": 10000, "staleMs": 35000},
                       "agents": [{"id": "other-sub", "label": "别的小屋",
                                   "status": "running", "createdAt": now}]},
                      f, ensure_ascii=False)
        r = run("mode_gate.py", ["room-board", "--room", "结构件"], d)
        rooms = r.get("rooms") or []
        ck("room-board 返回一个房间", len(rooms) == 1, r.get("ok"))
        if rooms:
            ck("★ G04 state=detached", rooms[0].get("state") == "detached",
               rooms[0].get("state"))
            ck("G04 safe_to_restart=False（不要 reset 整局）",
               rooms[0].get("safe_to_restart") is False, rooms[0].get("safe_to_restart"))
            ck("G04 next_action 指向 room-fail 重开",
               "room-fail" in str(rooms[0].get("next_action") or ""),
               rooms[0].get("next_action"))
    finally:
        shutil.rmtree(d, ignore_errors=True)

    print("\n" + "=" * 72)
    print("汇总：PASS=%d FAIL=%d" % (P, F))
    if FAILS:
        print("失败项：")
        for x in FAILS:
            print("  - " + x)
    return 0 if F == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
