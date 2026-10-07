#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Mode Gate -- 代码级强制关卡 v2（SW 进程级使用窗口）
====================================================
核心机制（并行多小屋时）:
  1. 每个小屋的 sw_bridge 调用入口自动排队（FIFO 先到先用）
  2. 获取 SW 使用权前检测 SLDWORKS.EXE 进程：
     - 进程在跑且是上一个房间启动的 → 等它完全关闭后才放行
     - 进程在跑但是用户手动开的（非小屋启动） → 允许附着使用，但不会去杀它
  3. room-end 兜底：释放锁 + 确保本房间启动的 SW 进程完全关闭（20秒宽限后强制关闭）
  4. 队列按请求顺序排队，谁先排队谁先用

命令:
    python mode_gate.py declare <1|2|3>      # 声明任务模式
    python mode_gate.py room-start <房间名>   # 小屋启动时登记
    python mode_gate.py room-end <房间名>     # 完成清理+释放SW锁+确保SW关闭
    python mode_gate.py room-fail <房间名>    # 错误回退
    python mode_gate.py sw-request <房间名>   # 排队/获取SW使用权（sw_bridge自动调用）
    python mode_gate.py sw-wait <房间名> [秒] # 阻塞等待SW使用权
    python mode_gate.py sw-release <房间名>   # 释放SW使用权
    python mode_gate.py lock-doctor [--auto] # 锁体检：检测/自动清理失效锁、失联状态
    python mode_gate.py sw-proc              # 查看SW进程状态
    python mode_gate.py sw-status            # 查看SW锁状态
    python mode_gate.py status               # 查看关卡状态
    python mode_gate.py check                # 是否允许建模
"""
import json
import os
import subprocess
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def emit(obj):
    r"""【Bug2 修复】统一 JSON 输出，杜绝中文 GBK 乱码。

    问题现象：主对话读 reports/结构件.report.json 内容存在，但 room-report-read
      有时拿到乱码，导致误判房间状态（靠 no_heartbeat 猜）。
    根因：JSON 用 ensure_ascii=False 直接 print 到 Windows 控制台；
      当 stdout 被 pwsh 以 GBK 代码页捕获、或 reconfigure 未生效时，
      中文（房间名/阶段名）会被写成乱码字节。
    修复（双重保险）：
      ① 强制以 UTF-8 字节写出（绕开控制台代码页）；
      ② 若 UTF-8 写出失败，退回 ensure_ascii=True（纯 ASCII 转义，
         任何代码页都能无损还原）—— 宁可转义也绝不乱码。
    """
    try:
        text = json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:
        text = json.dumps({"ok": False, "error": "serialize failed"}, ensure_ascii=True)
    try:
        _buf = getattr(sys.stdout, "buffer", None)
        if _buf is not None:
            _buf.write(text.encode("utf-8", "replace"))
            _buf.write(b"\n")
            _buf.flush()
            return
    except Exception:
        pass
    try:
        sys.stdout.write(text + "\n")
        sys.stdout.flush()
    except UnicodeEncodeError:
        # 代码页无法编码中文 → 退回 ASCII 转义（无损，绝不留乱码）
        try:
            sys.stdout.write(json.dumps(obj, ensure_ascii=True, indent=2) + "\n")
            sys.stdout.flush()
        except Exception:
            pass


SW_EXE = "SLDWORKS.EXE"
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
SW_MAX_HOLD = 4 * 3600   # 单房间最长持锁秒数（兜底防死锁）
SW_HANDOVER_GRACE = 20   # room-end 时等待SW自行退出的宽限秒数
SW_HEARTBEAT_TIMEOUT = 180   # 房间心跳超时秒数（超过视为死亡，释放锁）
# ── 【Bug3 修复】心跳超时与"建模耗时不匹配"导致误判 stale ───────────────────
# 问题现象：壳体机架房间被判 stale（心跳 >180s），但实际它刚落盘文件、SW 正在跑。
#   根因：小屋专心建模时【没空调 room-heartbeat】——一次 SW 建模+保存+出图
#         轻易超过 180s，而心跳阈值把"没按时上报"直接当成"死了"。
#   平台侧 list_agents 明明显示 running，两套口径打架 → 误杀正在干活的屋子。
# 修复（三重）：
#   ① 心跳超时不再是唯一判据：有【近期证据】（进度报告/产物文件/平台侧登记）
#      一律不判死，降级为 running_unverified；
#   ② 引入滞后宽限 SW_STALE_GRACE：心跳刚过期不立即判 stale，先观察；
#   ③ stale 仅在"心跳超时 + 无任何近期证据 + 超过滞后宽限"三者同时成立时给出。
# ── 【C1 修复】按测试反馈把静默宽限提升到 600s（原 240s 仍会误杀）─────────
# 测试反馈原文：3 个房间全部因"心跳 age>180s"被 stale 判死回收，
#   但磁盘证明它们真在产出模型。建议建模中的小屋给更长静默宽限（600s）。
# 结论：240s 对"一次完整建模+保存+出图"仍然偏短（实测常 5~10 分钟）。
# 现提升为 600s，并【新增磁盘产出交叉核对】——比心跳更贴近"它到底在不在干活"。
SW_STALE_GRACE = 600         # 心跳超时后的观察期；期间即使无任何上报也不判死
SW_EVIDENCE_WINDOW = 900     # 进度报告/待确认文件视为"活着证据"的时长（秒）
# 磁盘产物证据：房间产物目录里若有【近期新增/修改】的模型文件，说明小屋在干活。
#   这是最强的反误杀证据 —— 心跳可以忘记跳，但落盘文件骗不了人。
SW_ARTIFACT_WINDOW = 600     # 产物文件的 mtime 在此秒数内 → 视为活跃证据
SW_ACTIVE_EVIDENCE_WINDOW = 1800  # 兜底：极长的整体活跃窗口（防止长建模被判死）
SW_STALE_KILL = 120      # 上家SW未关闭时，等待这么久后强制接管
SW_START_GRACE = 120     # room-start 后允许无心跳的启动宽限秒数（期间状态=starting，禁止判死）
# ── 单命令级锁（Bug4/5修复）───────────────────────────────────────────────
SW_CMD_HOLD = 90         # 单条命令最长持锁秒数：超时视为命令已结束，可被接管
SW_ORPHAN_TIMEOUT = 90   # owner 心跳静默超过此秒数 → 判为孤儿锁，自动回收
SW_SW_IDLE_TIMEOUT = 600 # SW进程空闲(无心跳)超过此秒数且无人持锁 → 允许清理
# ── 【Bug2 修复】SW 锁公平轮转（防饥饿）──────────────────────────────────
# 问题现象：结构件小屋连续提交建模命令（cmd_seq 到 14），每执行一条就刷新
#   acquired_at，导致 _lock_owner_lock_age 永远小于 SW_CMD_HOLD → 超过 90 秒
#   也不会被回收；传动机构/壳体机架在队列里被活活饿死（实测锁被独占 272 秒）。
# 修复：改用【命令条数 + 独占时长】双重公平判据——
#   · 队首等待者出现后，当前持有者的累计命令数超过 SW_FAIR_CMDS 即必须让位
#   · 或连续独占（自首次取锁起）超过 SW_FAIR_SECONDS 即必须让位
#   · 让位时把队首提升为新 owner，保证 FIFO 队列真正前进
SW_FAIR_CMDS = 3         # 有人在排队时，单房间最多连续执行几条 SW 命令就必须让位
SW_FAIR_SECONDS = 180    # 有人在排队时，单房间连续独占上限（秒）
# ── 【Bug4 修复】遗留产物污染工作目录 ──────────────────────────────────────
# 问题现象：目录里混着上一次测试残留的旧件（DSH_大臂/底座板/关节铰销/
#   机械臂总装 等，时间戳 11:xx），与本轮新件（16:xx 起）同处一个目录。
#   总装时极易【误用旧件】——旧件的尺寸/接口与本轮设计无关，
#   装配出来的东西"看着能装、实际不对"，且极难排查。
# 修复：引入【任务纪元 task_epoch】——
#   · declare 2 时写入 epoch（时间戳），并记录 task_epoch_started_at；
#   · 提供 stale-artifacts 命令扫描工作目录，把"早于本纪元"的 DSH_* 产物
#     标为 legacy（遗留），供主对话在小屋 prompt 中明确禁用；
#   · room-start 时把纪元与"本轮允许使用的零件白名单"落在房间记录里，
#     让小屋有据可依，而不是靠"看目录里有什么就用什么"。
SW_ARTIFACT_GLOB_EXT = (".SLDPRT", ".sldprt", ".SLDASM", ".sldasm",
                        ".SLDDRW", ".slddrw", ".DWG", ".dwg", ".DXF", ".dxf")

# -- SW 后台监控进程（持续检测 SLDWORKS.EXE 进程状态）--

# ── 自动探测工具目录（subagent 从不同 cwd 调用也能正确定位）──────────────
def _find_tools_dir():
    candidates = [os.path.dirname(os.path.abspath(__file__))]
    script_abs = os.path.abspath(__file__)
    for _ in range(5):
        parent = os.path.dirname(script_abs)
        candidates.append(parent)
        if os.path.basename(parent) == "engineering":
            break
        script_abs = parent
    home = os.path.expanduser("~")
    for rel in [".dsh/.agent-presets/engineering/tools",
                ".dsh/profiles/web/node_modules/dsh-tool-workflow-gate/tools",
                ".dsh/engineering/tools"]:
        candidates.append(os.path.join(home, rel))
    for c in candidates:
        if os.path.isdir(os.path.join(c, "tools")):
            return os.path.join(c, "tools")
        if os.path.isfile(os.path.join(c, "mode_gate.py")):
            return c
    return os.path.dirname(os.path.abspath(__file__))


# ── 【P0-3 写侧修复】状态目录必须来自【单一事实源】────────────────────────
# 原实现把状态写到本脚本所在目录 —— 而工程模式在磁盘上有【两份副本】
#   （工作区 + ~/.dsh 安装目录），从哪份调用就写哪份，于是状态被劈成两半：
#     workflow_state.json：工作区 step=user_selected vs 安装 step=depth_asked
#     mode_state.json    ：工作区 1 个房间 vs 安装 0 个房间
#     reports/           ：工作区有防线凭据，安装副本没有
#   宿主守卫读安装副本 → 把"已完成"判成"未完成"，反复 steer 拦住对话结束。
# 现在统一由 _store.state_dir() 决定落点（优先 DSH_STATE_DIR > 工程根 > 探测）。
# _find_tools_dir() 保留为"脚本所在目录"的探测（用于定位同目录的 .py 模块）。
_BASE_DIR = _find_tools_dir()
try:
    import _store as _store_mod
    STATE_DIR = _store_mod.state_dir()
    # 状态目录可能不等于脚本目录：把两者都加进 sys.path，
    #   保证 import mode_gate / defense_gate / choice_contract 都能找到。
    for _d in (STATE_DIR, _BASE_DIR):
        if _d and os.path.isdir(_d) and _d not in sys.path:
            sys.path.insert(0, _d)
except Exception:
    STATE_DIR = _BASE_DIR

_SW_STATE_FILE = os.path.join(STATE_DIR, "sw_state.json")
_SW_MONITOR_PID_FILE = os.path.join(STATE_DIR, "sw_monitor.pid")
_SW_MONITOR_INTERVAL = 3   # 监控刷新间隔（秒）

STATE_PATH = os.path.join(STATE_DIR, "mode_state.json")
WORKFLOW_STATE_PATH = os.path.join(STATE_DIR, "workflow_state.json")
MODE_NAMES = {"1": "基础零件搭建", "2": "大型复杂器械装配", "3": "原图解分析"}
NL = chr(10)
HEARTBEAT_DIR = os.path.join(STATE_DIR, "heartbeats")
REPORTS_DIR = os.path.join(STATE_DIR, "reports")


def _sw_running():
    """检测 SolidWorks 进程是否在运行（进程级检测，不依赖任何自觉）。"""
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq " + SW_EXE, "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, timeout=15,
                             creationflags=CREATE_NO_WINDOW)
        return SW_EXE in (out.stdout or "").upper()
    except Exception:
        return False


def _sw_force_close():
    """强制关闭 SolidWorks 进程（仅用于残留进程兜底）。"""
    try:
        subprocess.run(["taskkill", "/IM", SW_EXE, "/F"],
                       capture_output=True, text=True, timeout=20,
                       creationflags=CREATE_NO_WINDOW)
        time.sleep(2)
    except Exception:
        pass


# ══ 【Bug-23 修复】平台存活登记（取代心跳作为权威判据）═══════════════════
# 用户明确建议（台账原文）："去掉心跳机制，直接采用 DSH 平台 running 状态
#   判定子代理是否死亡"。理由：
#     ① DSH 平台 list_agents 已能实时反映子代理是否 running —— 这是权威
#        事实来源，比 mode_gate 自建心跳可靠；
#     ② 心跳是"双份状态管理"，容易漂移/超时/误判，造成 room-status 与
#        residue-check 不一致（Bug-22 的根因之一）；
#     ③ 小屋 prompt 里塞"每 60 秒调 room-heartbeat"会打断建模流程，且小屋
#        未必执行（实测心跳 age 1222~1777s 而平台侧始终 running，Bug-23）。
#
# 设计：主对话在每次核对子代理后，把 list_agents 的真实状态写进本文件：
#     python mode_gate.py platform-sync <房间名> running|inactive|missing
#   mode_gate 的存活判定【优先读它】，心跳降级为辅助证据。
#   platform-sync 写入的 ts 若仍在 PLATFORM_FRESH_SEC 内，即为权威结论。
PLATFORM_STATUS_FILE = os.path.join(STATE_DIR, "platform_status.json")
# 平台状态有效期：超过则视为"未知"（不是"死亡"），退回证据链判断
PLATFORM_FRESH_SEC = 600


def _load_platform_status():
    """读取平台存活登记表 {room: {status, ts, by}}（失败返回 {}）。"""
    try:
        if os.path.exists(PLATFORM_STATUS_FILE):
            with open(PLATFORM_STATUS_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
    except Exception:
        pass
    return {}


def _save_platform_status(data):
    try:
        with open(PLATFORM_STATUS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def _platform_liveness(room):
    """【Bug-23】取房间的平台侧存活结论。

    Returns: (status|None, age_sec|None)
      "running"  -> 平台确认仍在跑（权威：禁止判死/禁止重启）
      "inactive" -> 平台确认已停止（可判 stale）
      "missing"  -> 平台侧无此子代理（登记丢失，Bug-32 场景）
      None       -> 无登记或登记过期（证据不足，退回证据链）
    """
    rec = _load_platform_status().get(room)
    if not isinstance(rec, dict):
        return None, None
    try:
        _ts = float(rec.get("ts") or 0)
    except Exception:
        return None, None
    _age = time.time() - _ts
    if _age > PLATFORM_FRESH_SEC:
        return None, _age
    return str(rec.get("status") or "").lower() or None, _age


def cmd_platform_sync(room, status, by="main"):
    """【Bug-23】主对话把 list_agents 的真实状态登记进来。

    用法（主对话在 list_agents 之后调用）：
        python mode_gate.py platform-sync 结构件 running
        python mode_gate.py platform-sync 工程图输出 inactive
        python mode_gate.py platform-sync <房间> missing

    这样 room-status / residue-check / recover 全部以【平台真实状态】为准，
    不再依赖小屋是否记得跳心跳。
    """
    _st = str(status or "").strip().lower()
    if _st not in ("running", "inactive", "missing"):
        return {"ok": False, "error": "status 必须是 running / inactive / missing"}
    data = _load_platform_status()
    data[str(room)] = {"status": _st, "ts": time.time(),
                       "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                       "by": str(by or "main")}
    ok = _save_platform_status(data)
    return {"ok": ok, "room": room, "platform_status": _st,
            "file": PLATFORM_STATUS_FILE,
            "note": ("已登记平台侧存活状态；room-status/residue-check/recover "
                     "将以此为准（%ds 内有效），心跳仅作辅助证据（Bug-23）。"
                     % PLATFORM_FRESH_SEC)}


def _heartbeat_path(room):
    """返回房间心跳文件路径。"""
    os.makedirs(HEARTBEAT_DIR, exist_ok=True)
    safe = "".join(c for c in room if c.isalnum() or c in " _-").strip()
    return os.path.join(HEARTBEAT_DIR, safe + ".heartbeat")


def cmd_room_heartbeat(name):
    """记录存活心跳（JSON 文件，mtime 反映最后存活时间）。

    ── 【Bug-23 修复 · 心跳已降级为"可选辅助证据"】─────────────────────
    用户明确要求："去掉心跳机制，直接采用 DSH 平台 running 状态判定
      子代理是否死亡"。
    实测依据：传动机构/壳体机架的心跳 age 涨到 1222~1777s（远超 180s 阈值），
      但平台侧 list_agents 始终 running、且持续产出零件 —— 心跳完全失真。
      原因：小屋忙于 SW 建模没空调 mode_gate，且长任务会阻塞心跳线程。

    现状（新语义）：
      · 本命令【仍然可用】，但不再是存活判定的必要条件；
      · 存活判定的权威来源是 platform-sync 登记的平台状态（见 _platform_liveness）；
      · 小屋 prompt 不再要求"每 60 秒跳心跳"，只要求关键节点 room-report
        + save 后 room-artifact 登记；
      · 心跳仅在"平台状态未知"时作为辅助证据参与判断。
    """
    path = _heartbeat_path(name)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"name": name, "ts": time.time(), "pid": os.getpid()}, f)
        return {"ok": True, "room": name, "path": path, "age_seconds": 0}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def cmd_room_heartbeat_check(name):
    """检查房间心跳是否超时（>180 秒视为死亡）。"""
    path = _heartbeat_path(name)
    if not os.path.exists(path):
        return {"ok": False, "room": name, "alive": False, "reason": "无心跳文件"}
    try:
        age = time.time() - os.path.getmtime(path)
        return {"ok": True, "room": name, "alive": age <= 180, "age_seconds": round(age, 1)}
    except Exception as e:
        return {"ok": False, "room": name, "error": str(e)}


def cmd_subagent_assign(room, subagent_id):
    """记录负责当前房间的 subagent_id（主对话在创建小屋时调用）。

    ── 【FL-07 修复】必须先校验【房间存在且已 room-start】──────────────────
    实测缺陷：对 ghost / 尚未 room-start 的房间调用 subagent-assign，
      返回 ok=true 但【并未真正持久化生效】（房间表里没有它）——
      主对话以为登记成功，room-status 却看不到，属于"误报成功"。
    现在显式校验：
      · 房间必须在 mode_state.rooms 里（否则说明 room-start 没执行）；
      · subagent_id 不能为空。
    校验不过就 fail-closed 并给出下一步指引，绝不假装成功。
    """
    _room = str(room or "").strip()
    _sid = str(subagent_id or "").strip()
    if not _room:
        return {"ok": False, "error": "缺少房间名"}
    if not _sid:
        return {"ok": False, "room": _room,
                "error": "缺少 subagent_id（主对话应传 subagent_fork 返回的 id）"}
    state = load_state()
    rooms = state.get("rooms") or {}
    if _room not in rooms:
        # 房间不存在 = 主对话忘了先 room-start（顺序错误），或房间名写错
        _known = sorted(rooms.keys())
        return {
            "ok": False,
            "room": _room,
            "gate": "ROOM_NOT_REGISTERED",
            "error": ("房间 %r 尚未登记（未执行 room-start），无法绑定 subagent。"
                      "正确顺序：先 mode_gate.py room-start %r，"
                      "创建小屋后再 subagent-assign。"
                      % (_room, _room)),
            "registered_rooms": _known,
            "hint": ("若房间名拼错，请核对当前任务的房间表（见 select 返回的 rooms）；"
                     "已登记房间：%s" % ("、".join(_known) if _known else "（无）")),
        }
    subs = state.setdefault("subagents", {})
    subs[_room] = {"id": _sid, "assigned_at": time.time()}
    save_state(state)
    return {"ok": True, "room": _room, "subagent_id": _sid,
            "note": "已绑定并持久化（room-status 的 subagent 字段可见）"}


def cmd_subagent_free(room):
    """清除房间对应的 subagent 记录（房间被杀/重启时使用）。"""
    state = load_state()
    subs = state.setdefault("subagents", {})
    if room in subs:
        old = subs.pop(room)
        save_state(state)
        return {"ok": True, "room": room, "freed_subagent": old}
    return {"ok": True, "room": room, "note": "该房间无 subagent 记录"}


# ══ 【残留检测】子对话 / 任务残留体检与自动清理 ═══════════════════════════
#
# 【用户要求 · 本次新增】"上个对话的小屋还是有东西留下来干扰这个新的对话。
#   加个检测：在开始之前和结束之前，都要自己测一场是否有子对话在项目栏中的。"
#
# ── 问题现象（测试部反馈 Bug#1 / Bug#6 的同源根因）──────────────────────
#   新对话发起任务时，被上一轮残留的 workflow_state.step=user_selected 拦截，
#   报"检测到进行中的任务"无法 init；同时 mode_state.json 里还留着上一轮的
#   房间/subagent 记录，导致：
#     · select 把旧房间的 ended_at 误判为本轮已完成 → 假 finished；
#     · whoami 拿到上一波的陈旧 subagent_id → 提问投递给已死会话；
#     · 锁队列里残留死房间 → sw_busy 永久阻塞。
#
# ── 判定设计（本函数只做"体检 + 分类"，不擅自杀活着的子代理）──────────
#   对每个登记在案的房间，结合 mode_state + 心跳 + 进度报告给出四分类：
#     alive     : 有新鲜心跳/报告/磁盘产出，或平台侧刚登记 → 【活着的子对话】
#                 ⇒ 默认不动它（除非 --force）；只报告，交由主对话决定。
#     zombie    : 房间标 active，但无 subagent 登记/无心跳/无产出
#                 ⇒ 典型"登记了却没拉起"，可直接清。
#     stale     : 曾有活动，但心跳+所有证据均超期
#                 ⇒ 判死，可清。
#     orphan    : rooms 里没这个房间，但 subagents 表里还留着它的映射
#                 ⇒ 上一波残影，可直接清（这正是 whoami 拿到死 id 的原因）。
#   另外单独核对 workflow_state：step 处于"推进态"但没有任何活着的房间
#     ⇒ 判定为【残留门禁状态】，这是新任务被 init 拦截的直接原因。
#
# ⚠️ 本函数【绝不】调用 taskkill 之外的任何破坏性操作；SW 进程由调用方
#    自行决定是否 close-all。
def cmd_residue_check(clean=False, force=False, quiet=False):
    """检测（并按需清理）上一轮对话残留：子对话登记 / 房间 / 门禁状态。

    Args:
        clean: True 时执行清理（清 rooms/subagents/锁/门禁推进态 + 孤儿报告）。
        force: True 时连【被判为 alive 的房间】也一并清（危险，仅用户明确要求时）。
        quiet: True 时精简输出。

    Returns:
        {"ok", "has_residue", "alive", "zombie", "stale", "orphan",
         "gate_residue", "cleaned", "actions", "hint"}
    """
    state = load_state()
    rooms = state.get("rooms") or {}
    subs = state.get("subagents") or {}
    now = time.time()

    alive, zombie, stale, orphan = [], [], [], []

    # ── 1) 房间维度分类 ─────────────────────────────────────────────────
    for name, info in rooms.items():
        info = info or {}
        if info.get("ended_at") or info.get("failed_at"):
            # 已收尾：不算"活着的子对话"，但 subagent 映射可能需要清
            continue
        if not info.get("active"):
            continue
        st = _room_status_of(name, info, subs)
        status = st.get("status")
        rec = (
            "房间[%s] 状态=%s 心跳=%ss 报告=%ss 产物=%s"
            % (name, status, st.get("heartbeat_age_sec"),
               st.get("report_age_sec"),
               bool((st.get("evidence") or {}).get("disk_artifact")))
        )
        # running / starting / running_unverified 都视为活着的子对话
        if status in ("running", "starting", "running_unverified"):
            alive.append({"room": name, "status": status, "detail": rec})
        elif status == "stale":
            stale.append({"room": name, "status": status, "detail": rec})
        elif status == "no_heartbeat":
            # 可疑：无心跳也无佐证 —— 归入僵尸（登记了却没真正跑起来）
            zombie.append({"room": name, "status": status, "detail": rec})
        else:
            zombie.append({"room": name, "status": status, "detail": rec})

    # ── 2) 孤儿映射：subagents 里有、rooms 里没有（或房间已收尾）────────
    for name, rec in subs.items():
        info = rooms.get(name) or {}
        if not info:
            orphan.append({"room": name,
                           "detail": "subagents 表残留映射（rooms 里无此房间）"})
        elif info.get("ended_at") or info.get("failed_at"):
            orphan.append({"room": name,
                           "detail": "房间已收尾，但 subagent 映射未清除"})

    # ── 3) 门禁状态残留：step 推进中，却没有任何活着的房间 ──────────────
    gate_residue = None
    try:
        if os.path.exists(WORKFLOW_STATE_PATH):
            with open(WORKFLOW_STATE_PATH, "r", encoding="utf-8") as f:
                wf = json.load(f) or {}
            step = str(wf.get("step") or "")
            ADVANCED = ("providing", "mechanics_done", "user_selected")
            no_alive_room = (len(alive) == 0)
            if step in ADVANCED and no_alive_room:
                gate_residue = {
                    "step": step,
                    "task": wf.get("task"),
                    "user_choice": wf.get("user_choice"),
                    "finished_at": wf.get("finished_at"),
                    "reason": ("workflow_state.step=%s 处于推进态，"
                               "但没有任何活着的房间 —— 属上一轮残留，"
                               "会让新任务的 init 被 IN_PROGRESS 拦截。" % step),
                }
            elif step == "finished":
                # finished 是合法终态，不算残留（守卫靠它放行）
                gate_residue = None
    except Exception:
        gate_residue = None

    has_residue = bool(zombie or stale or orphan or gate_residue)
    result = {
        "ok": True,
        "has_residue": has_residue,
        "alive": alive,
        "zombie": zombie,
        "stale": stale,
        "orphan": orphan,
        "gate_residue": gate_residue,
        "counts": {"alive": len(alive), "zombie": len(zombie),
                   "stale": len(stale), "orphan": len(orphan)},
        "cleaned": False,
        "actions": [],
    }

    # ── 4) 清理（仅当 clean=True）──────────────────────────────────────
    if clean:
        actions = []
        _st = load_state()
        _rooms = _st.setdefault("rooms", {})
        _subs = _st.setdefault("subagents", {})

        # 4a) 清僵尸 / 陈旧 / 孤儿映射
        for item in (zombie + stale + orphan):
            _rn = item["room"]
            if _rn in _rooms:
                _rooms.pop(_rn, None)
                actions.append("清除房间记录: %s" % _rn)
            if _rn in _subs:
                _subs.pop(_rn, None)
                actions.append("清除 subagent 映射: %s" % _rn)

        # 4b) 活着的房间：仅在 force 时清（默认保护，避免误杀正在干活的屋子）
        if force:
            for item in alive:
                _rn = item["room"]
                _rooms.pop(_rn, None)
                _subs.pop(_rn, None)
                actions.append("【force】清除活动房间: %s" % _rn)
        elif alive:
            actions.append("保留 %d 个活动房间（未加 --force，不清理）: %s"
                           % (len(alive), ", ".join(x["room"] for x in alive)))

        # 4c) 重置 SW 锁（残留死房间会把队列永久阻塞）
        _st["sw_lock"] = {"owner": None, "queue": [], "acquired_at": None,
                          "prev_owner": None, "sw_by_room": False}
        actions.append("重置 SW 锁（清空 owner/queue/prev_owner）")

        # 4d) 清门禁残留：把 step 置回 idle，让新任务能正常 init
        if gate_residue:
            try:
                if os.path.exists(WORKFLOW_STATE_PATH):
                    with open(WORKFLOW_STATE_PATH, "r", encoding="utf-8") as f:
                        wf = json.load(f) or {}
                    _old_step = wf.get("step")
                    wf["step"] = "idle"
                    wf["finished_at"] = None
                    wf["assembly_confirmed"] = False
                    wf["assembly_inputs"] = None
                    wf["residue_cleared_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    wf["residue_cleared_from"] = _old_step
                    with open(WORKFLOW_STATE_PATH, "w", encoding="utf-8") as f:
                        json.dump(wf, f, ensure_ascii=False, indent=2)
                    actions.append("清门禁残留: step %s -> idle" % _old_step)
            except Exception as e:
                actions.append("清门禁残留失败: %r" % (e,))
            # 顺带清掉完成标记，避免上一轮的 finished 让守卫误放行
            try:
                _mk = os.path.join(STATE_DIR, "TASK_FINISHED.json")
                if os.path.exists(_mk):
                    os.remove(_mk)
                    actions.append("清除 TASK_FINISHED.json 标记")
            except Exception:
                pass

        save_state(_st)
        result["cleaned"] = True
        result["actions"] = actions
        result["has_residue"] = False
        # ── 【修复】清理后必须刷新分类快照，不能回传"清理前"的旧数据 ──
        #   实测问题：--clean 后返回体里 orphan/gate_residue 仍是清理前的值，
        #   调用方看到 has_residue=false 却同时看到 3 个 orphan，
        #   会误判"没清掉"（真实情况是已清干净，只是快照没更新）。
        #   现在把已清理的类别记入 cleaned_items 供审计，实时字段归零。
        result["cleaned_items"] = {
            "zombie": [x.get("room") for x in zombie],
            "stale": [x.get("room") for x in stale],
            "orphan": [x.get("room") for x in orphan],
            "gate_residue_step": (gate_residue or {}).get("step"),
        }
        result["zombie"] = []
        result["stale"] = []
        result["orphan"] = []
        result["gate_residue"] = None
        # counts 也要同步归零，否则调用方读 counts 仍会看到"清理前"的残留数
        result["counts"] = {"alive": len(result.get("alive") or []),
                            "zombie": 0, "stale": 0, "orphan": 0}
        if force:
            result["alive"] = []
            result["counts"]["alive"] = 0

    # ── 5) 提示文案 ────────────────────────────────────────────────────
    if not quiet:
        if result.get("cleaned"):
            # 清理后按"实际结果"给文案，而不是按清理前的分类
            _ci = result.get("cleaned_items") or {}
            _kept = len(result.get("alive") or [])
            result["hint"] = ("清理完成。清除项：僵尸 %d / 陈旧 %d / 孤儿 %d / 门禁残留 %s%s。"
                              % (len(_ci.get("zombie") or []), len(_ci.get("stale") or []),
                                 len(_ci.get("orphan") or []),
                                 ("step=%s" % _ci["gate_residue_step"])
                                 if _ci.get("gate_residue_step") else "无",
                                 ("；保留 %d 个活动房间（未加 --force）" % _kept)
                                 if _kept else ""))
        elif not has_residue and not alive:
            result["hint"] = "干净：未检出任何子对话/任务残留。"
        elif not has_residue and alive:
            result["hint"] = ("未检出残留，但有 %d 个活动房间正在运行 —— "
                              "新任务请等其结束或显式 --force。" % len(alive))
        else:
            result["hint"] = ("检出残留：僵尸 %d / 陈旧 %d / 孤儿 %d / 门禁残留 %s。"
                              "执行 mode_gate.py residue-check --clean 自动清理。"
                              % (len(zombie), len(stale), len(orphan),
                                 "有" if gate_residue else "无"))
    return result


# ── 【双份状态修复】────────────────────────────────────────────────────
# 测试反馈：状态文件存在两份且内容矛盾 ——
#   工具实际读 .dsh\.agent-presets\engineering\tools\*（权威）
#   桌面 DSH-SW-and-CAD-main\engineering\tools\* 是另一个 finished 空壳。
#   两份都叫 workflow_state.json / mode_state.json，极易让人误读"当前状态"。
# 说明：桌面那份是【分发副本】，正常情况下不应承载运行时状态。
#   本命令用于：
#     ① 体检：列出所有副本的路径与关键字段，指出不一致；
#     ② 对齐：把权威状态覆盖到副本（--sync），或清空副本的运行时状态（--clean）。
_STATE_FILES = ("workflow_state.json", "mode_state.json", "sw_state.json",
                "workflow_state.json", "mode_state.json")


def _alt_tools_dirs():
    """探测除【权威状态目录】外的其他"工程模式 tools 目录"（分发副本）。

    ── 【P0-3 修复】候选必须覆盖全部已知落点，且不再硬编码个人路径 ─────────
    原实现只认 ~/Desktop/DSH-SW-and-CAD-main（写死了用户名与布局），
    且比较基准是 _BASE_DIR（脚本所在目录）而非【权威状态目录】——
    当脚本从安装副本运行、状态目录却指向工作区时，体检会把权威目录本身
    误报成"副本"，结论完全反过来。
    """
    out = []
    seen = set()

    def _add(p):
        try:
            ap = os.path.abspath(p)
        except Exception:
            return
        key = os.path.normcase(ap)
        if key in seen:
            return
        seen.add(key)
        if os.path.isdir(ap) and key != os.path.normcase(os.path.abspath(STATE_DIR)):
            out.append(ap)

    try:
        home = os.path.expanduser("~")
        _add(os.path.join(home, ".dsh", ".agent-presets", "engineering", "tools"))
        _add(os.path.join(home, ".dsh", "engineering", "tools"))
        # 工作区副本：以 __file__ 向上回溯找 engineering/tools（不写死用户名）
        try:
            _cur = os.path.dirname(os.path.abspath(__file__))
            for _ in range(6):
                if os.path.basename(_cur) == "engineering":
                    _add(os.path.join(_cur, "tools"))
                    break
                _nxt = os.path.dirname(_cur)
                if _nxt == _cur:
                    break
                _cur = _nxt
        except Exception:
            pass
        # 环境变量显式声明的工程根
        try:
            _eng = (os.environ.get("DSH_ENGINEERING_ROOT") or "").strip()
            if _eng:
                _add(_eng if os.path.basename(_eng).lower() == "tools"
                     else os.path.join(_eng, "tools"))
        except Exception:
            pass
    except Exception:
        pass
    return out


def cmd_doctor(sync=False, clean=False, migrate=False):
    """体检并（可选）修复状态文件多副本不一致问题。

    Args:
        sync:  把权威（STATE_DIR）状态覆盖到各副本
        clean: 清空副本里的运行时状态（只留空壳，避免误读）
        migrate: 【Bug-08 修复】把副本里【比权威更新】的状态搬到权威目录
                 （调用 _store.migrate_state，自动备份，绝不回退数据）
    """
    report = {"ok": True, "authoritative_dir": STATE_DIR, "copies": [], "issues": []}
    # ── 【Bug-08 修复】--migrate：一键收敛跨副本状态 ───────────────────────
    # 原流程需要三步（doctor 体检 → _store.py --dry-run → _store.py --migrate），
    #   且 _store.py 是另一个脚本，AI 容易漏掉第二步直接 --clean 导致进度回退。
    # 现在 doctor 直接提供 --migrate，内部调 _store.migrate_state()：
    #   只搬【更新】的文件、覆盖前自动备份、绝不回退权威目录的数据。
    if migrate:
        try:
            import _store as _st_mig
            _mg = _st_mig.migrate_state(dry_run=False)
            report["migrate"] = _mg
            report["ok"] = bool(_mg.get("ok", True))
            report["note"] = (
                "已执行状态迁移：从影子副本搬入 %d 个更新的文件到权威目录 %s；"
                "被覆盖的旧版本已备份为 *.bak-migrate-<时间戳>。"
                % (len(_mg.get("migrated") or []), STATE_DIR))
            return report
        except Exception as _e_mig:
            report["ok"] = False
            report["migrate_error"] = repr(_e_mig)
            report["hint"] = "状态迁移失败；可改用 `python tools/_store.py --migrate` 手动执行。"
            return report
    _auth = {}
    for fn in ("workflow_state.json", "mode_state.json"):
        p = os.path.join(STATE_DIR, fn)
        try:
            with open(p, "r", encoding="utf-8") as f:
                _auth[fn] = json.load(f)
        except Exception:
            _auth[fn] = None

    def _summary(d, fn):
        if not d:
            return None
        if fn == "workflow_state.json":
            return {"step": d.get("step"), "finished_at": d.get("finished_at"),
                    "user_choice": d.get("user_choice")}
        return {"mode": d.get("mode"),
                "rooms": sorted((d.get("rooms") or {}).keys()),
                "room_count": len(d.get("rooms") or {}),
                "subagents": sorted((d.get("subagents") or {}).keys()),
                "history_count": len(d.get("subagent_history") or [])}

    report["authoritative"] = {fn: _summary(_auth.get(fn), fn)
                               for fn in ("workflow_state.json", "mode_state.json")}

    for d in _alt_tools_dirs():
        entry = {"dir": d, "files": {}}
        for fn in ("workflow_state.json", "mode_state.json"):
            p = os.path.join(d, fn)
            data = None
            try:
                if os.path.exists(p):
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
            except Exception:
                data = None
            entry["files"][fn] = {
                "exists": os.path.exists(p),
                "summary": _summary(data, fn),
            }
            # 不一致判定
            if data is not None and _auth.get(fn) is not None:
                if _summary(data, fn) != _summary(_auth[fn], fn):
                    report["issues"].append(
                        "副本 %s 的 %s 与权威不一致" % (d, fn))
        report["copies"].append(entry)

        if sync:
            for fn in ("workflow_state.json", "mode_state.json"):
                try:
                    with open(os.path.join(d, fn), "w", encoding="utf-8") as f:
                        json.dump(_auth.get(fn) or {}, f, ensure_ascii=False, indent=2)
                    entry["files"][fn]["action"] = "synced"
                except Exception as e:
                    entry["files"][fn]["action"] = "sync_failed: %r" % (e,)
        elif clean:
            for fn in ("workflow_state.json", "mode_state.json"):
                try:
                    with open(os.path.join(d, fn), "w", encoding="utf-8") as f:
                        json.dump({}, f, ensure_ascii=False, indent=2)
                    entry["files"][fn]["action"] = "cleaned"
                except Exception as e:
                    entry["files"][fn]["action"] = "clean_failed: %r" % (e,)

    if not _alt_tools_dirs():
        report["note"] = "未发现其他 tools 副本目录，只有权威目录一份状态。"
    elif report["issues"]:
        report["hint"] = ("发现多副本不一致。工具【只读】权威目录(%s)；"
                          "副本仅作分发，不应承载运行时状态。"
                          "可用 --sync 对齐、或 --clean 清空副本运行时状态。"
                          % STATE_DIR)
    else:
        report["note"] = "各副本与权威状态一致。"
    report["ok"] = True
    # ── 【P0-3 修复】提示可用 _store.py 做权威目录迁移 ────────────────────
    # doctor 只做"体检/对齐/清空"；若发现副本里有【比权威更新】的状态
    #   （收敛写入点之前的遗留），应提示用 _store.py --migrate 搬过来，
    #   而不是简单清空 —— 那会让用户的任务进度凭空回退。
    try:
        _newer_found = []
        for _d in _alt_tools_dirs():
            for _fn in ("workflow_state.json", "mode_state.json"):
                _sp = os.path.join(_d, _fn)
                _dp = os.path.join(STATE_DIR, _fn)
                if os.path.isfile(_sp):
                    if not os.path.exists(_dp) or \
                       os.path.getmtime(_sp) > os.path.getmtime(_dp) + 1.0:
                        _newer_found.append("%s/%s" % (_d, _fn))
        if _newer_found:
            report["newer_in_copies"] = _newer_found
            report["migrate_hint"] = (
                "检测到副本中存在【比权威目录更新】的状态。"
                "请用 `python tools/_store.py --dry-run` 预览、"
                "`python tools/_store.py --migrate` 搬到权威目录（会自动备份）。"
                "【不要】直接用 --clean 清空，否则任务进度会回退。")
    except Exception:
        pass
    return report


def cmd_rooms_reset(keep_pmode=True):
    # [审计] rooms-reset 会清空房间与报告（含三大防线凭据），必须留痕。
    try:
        import defense_gate as _dg_r
        _dg_r.log_bypass("mode_gate.rooms-reset",
                         reason="清空房间/报告（会抹除防线凭据），已留痕",
                         by="rooms-reset")
    except Exception:
        pass
    """【bug5修复】清空房间记录/子代理对账/SW锁/心跳/进度报告。

    新任务开始时必须调用：否则上一任务的 ended_at 残留会让 select 把旧房间
    误判为已完成，出现 finished=true 但实际一个房间都没创建的假完成。

    【Bug1 修复】keep_pmode 默认 True —— parallel_mode 是策略配置而非房间状态，
    重置房间时保留它，避免并行模式在磁盘上"凭空消失"（曾导致锁判定口径漂移）。
    """
    state = load_state()
    # ── 【僵尸残留修复】把清理前的僵尸信息记录下来，便于事后审计 ──────────
    _before_rooms = list((state.get("rooms") or {}).keys())
    _before_hist = len(state.get("subagent_history") or [])
    _before_subs = list((state.get("subagents") or {}).keys())

    state["rooms"] = {}
    state["subagents"] = {}
    state["sw_lock"] = {"owner": None, "queue": [], "acquired_at": None,
                        "prev_owner": None, "sw_by_room": False}
    # ── 【僵尸残留修复】连同 subagent_history 一起清空 ────────────────────
    # 测试反馈：rooms={} 但 subagent_history 堆了多条僵尸记录
    #   （含 OLD-DEAD-SESSION-123 这类测试残留）。
    #   reset 的语义是"回到干净起点"，历史映射不清会给下一轮
    #   的 whoami / 陈旧身份判定留下残影。
    #   保留计数以便审计"这次清掉了多少"。
    state["subagent_history"] = []
    # 清理僵尸特征字段
    for _k in ("step_repaired_from", "step_repaired_at"):
        state.pop(_k, None)
    # ── 【Bug1 修复】保留 parallel_mode（策略级配置，不属于"房间状态"）──
    # rooms-reset 的语义是清空房间/对账/锁，若把 parallel_mode 一并清掉，
    #   新任务 select 前会读不到模式 → 锁判定又退回"实时推断"的不稳定态。
    #   仅当显式传 keep_pmode=False 时才清除。
    if not keep_pmode:
        state.pop("parallel_mode", None)
        state.pop("parallel_mode_updated_at", None)
    save_state(state)
    cleared = []
    for d in (HEARTBEAT_DIR, REPORTS_DIR):
        try:
            if os.path.isdir(d):
                for fn in os.listdir(d):
                    try:
                        os.remove(os.path.join(d, fn))
                        cleared.append(fn)
                    except Exception:
                        pass
        except Exception:
            pass
    return {"ok": True,
            "cleared_before": {
                "rooms": _before_rooms,
                "subagents": _before_subs,
                "subagent_history_count": _before_hist,
            },
            "message": ("房间/子代理对账/SW锁/僵尸历史已清空，"
                        "心跳与进度报告已清理(%d个文件)" % len(cleared))}


# ── 【Bug4 修复】遗留产物检测 ────────────────────────────────────────────
def _default_work_dir():
    """推断工作目录（产物所在处）。优先 state.work_dir，其次桌面 test，最后 cwd。"""
    try:
        st = load_state()
        wd = st.get("work_dir")
        if wd and os.path.isdir(wd):
            return wd
    except Exception:
        pass
    home = os.path.expanduser("~")
    for c in (os.path.join(home, "Desktop", "test"),
              os.path.join(home, "Desktop")):
        if os.path.isdir(c):
            return c
    return os.getcwd()


def _task_epoch():
    """返回本任务的起始时间戳（无则 None）。"""
    st = load_state()
    v = st.get("task_epoch_started_at")
    try:
        return float(v) if v is not None else None
    except Exception:
        return None


def _quarantine_dir(work_dir):
    """【BUG-10 修复】遗留件隔离区目录（与工作目录同级，不污染产物扫描）。"""
    return os.path.join(work_dir, "_legacy_quarantine")


def quarantine_legacy_artifacts(work_dir, epoch=None, dry_run=False):
    """【BUG-10 修复】把【上一轮遗留件】移入隔离区，根治多轮堆积。

    问题现象（测试反馈）：
      目录里混着上一次测试残留的旧件（DSH_大臂/底座板/关节铰销/机械臂总装 …），
      与本轮新件同处一个目录。总装时极易【误用旧件】—— 旧件尺寸/接口与本轮
      设计无关，装出来"看着能装、实际不对"，且极难排查。
      原实现只做到"扫描并标记 legacy"，从不清理 → 轮次越多堆得越厚。

    修复：提供物理隔离（移动到 _legacy_quarantine/）而不删除 ——
      既让工作目录只剩本轮产物（杜绝误用），又保留旧件可供追溯/回滚。

    Args:
        work_dir: 工作目录
        epoch:    任务纪元；None 时自动读 mode_state
        dry_run:  True 时只报告"会移动哪些"，不真正移动

    Returns: scan 结果 + {quarantined:[...], quarantine_dir, moved:bool}
    """
    scan = scan_legacy_artifacts(work_dir, epoch)
    out = dict(scan)
    out["quarantined"] = []
    out["quarantine_dir"] = _quarantine_dir(work_dir)
    out["moved"] = False
    out["dry_run"] = bool(dry_run)
    legacy = scan.get("legacy") or []
    if not legacy:
        out["quarantine_note"] = "无遗留件，无需隔离。"
        return out
    if dry_run:
        out["quarantined"] = [x.get("name") for x in legacy]
        out["quarantine_note"] = ("[dry-run] 将移动 %d 个遗留件到 %s（未实际执行）"
                                  % (len(legacy), out["quarantine_dir"]))
        return out
    qdir = out["quarantine_dir"]
    try:
        os.makedirs(qdir, exist_ok=True)
    except Exception as e:
        out["quarantine_note"] = "创建隔离区失败: %r" % (e,)
        return out
    moved, failed = [], []
    for item in legacy:
        src = os.path.join(work_dir, item.get("name") or "")
        if not src or not os.path.exists(src):
            continue
        dst = os.path.join(qdir, item["name"])
        # 同名冲突 → 加时间戳后缀，绝不覆盖历史
        if os.path.exists(dst):
            _ts = time.strftime("%Y%m%d_%H%M%S")
            _base, _ext = os.path.splitext(item["name"])
            dst = os.path.join(qdir, "%s__%s%s" % (_base, _ts, _ext))
        try:
            import shutil as _sh
            _sh.move(src, dst)
            moved.append(item["name"])
        except Exception as e:
            # 移动失败（文件被 SW 占用等）→ 尝试改名，再不行就记录
            try:
                _alt = os.path.join(qdir, "_move_failed_" + item["name"])
                os.replace(src, _alt)
                moved.append(item["name"])
            except Exception:
                failed.append({"name": item["name"], "error": repr(e)})
    out["quarantined"] = moved
    out["move_failed"] = failed
    out["moved"] = bool(moved)
    out["quarantine_note"] = (
        "已隔离 %d 个遗留件到 %s（未删除，可追溯/回滚）%s"
        % (len(moved), qdir,
           ("；%d 个移动失败（可能被 SolidWorks 占用）" % len(failed)) if failed else ""))
    return out


def scan_legacy_artifacts(work_dir, epoch=None):
    """扫描工作目录，把【早于本任务纪元】的 DSH_* 产物标为 legacy。

    【Bug4 修复】目的：总装前明确知道"哪些文件是上一轮的残留"，
      从而在小屋 prompt 里禁用它们，杜绝误用旧件。

    Args:
        work_dir: 要扫描的目录
        epoch: 本任务起始时间戳；None 时自动读 mode_state.json

    Returns:
        {"ok":bool, "epoch":float|None, "legacy":[...], "current":[...],
         "unknown":[...], "counts":{...}, "hint":str}
      legacy  = mtime 明显早于 epoch（>60 秒裕量）→ 上一轮残留
      current = mtime 在 epoch 之后 → 本轮产物
      unknown = 无 epoch 时全部归入（无法判定）
    """
    if epoch is None:
        epoch = _task_epoch()
    out = {"ok": True, "work_dir": work_dir, "epoch": epoch,
           "legacy": [], "current": [], "unknown": [], "counts": {}}
    if not work_dir or not os.path.isdir(work_dir):
        out["ok"] = False
        out["error"] = "目录不存在: %s" % work_dir
        return out
    try:
        names = os.listdir(work_dir)
    except Exception as e:
        out["ok"] = False
        out["error"] = str(e)
        return out
    _grace = 60.0   # 裕量：避免把"任务刚开始时刚写的文件"误判为遗留
    for fn in names:
        if not fn.lower().endswith(tuple(e.lower() for e in SW_ARTIFACT_GLOB_EXT)):
            continue
        # ── 【BUG-10 补强·实机发现】跳过 SolidWorks 临时锁文件 ────────────
        # 实机扫描时发现 "~$DSH_大臂.sldprt" 被当成"本轮产物"列进 current，
        #   它是 SW 打开文档时生成的临时锁文件（~$ 前缀），不是设计产物。
        #   把它留在 current 会污染"本轮可用零件白名单"，
        #   总装时可能被误当成有效零件。此处直接剔除。
        if fn.startswith("~$"):
            continue
        p = os.path.join(work_dir, fn)
        try:
            mtime = os.path.getmtime(p)
            size = os.path.getsize(p)
        except Exception:
            continue
        item = {"name": fn, "mtime": mtime,
                "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime)),
                "size": size}
        if epoch is None:
            out["unknown"].append(item)
        elif mtime < (epoch - _grace):
            item["reason"] = "早于本任务纪元，疑为上一轮残留"
            item["age_before_epoch_sec"] = round(epoch - mtime, 1)
            out["legacy"].append(item)
        else:
            out["current"].append(item)
    out["legacy"].sort(key=lambda x: x["mtime"])
    out["current"].sort(key=lambda x: x["mtime"])
    out["counts"] = {"legacy": len(out["legacy"]), "current": len(out["current"]),
                     "unknown": len(out["unknown"])}
    if epoch is None:
        out["hint"] = ("尚未 declare 任务纪元，无法区分新旧产物。"
                       "请先 python mode_gate.py declare 2 再扫描。")
    elif out["legacy"]:
        out["hint"] = ("⚠️ 检出 %d 个【上一轮遗留件】。总装/出图时必须【只用 current 列表】，"
                       "严禁把 legacy 文件纳入本轮装配。建议小屋 prompt 明确写出白名单。"
                       % len(out["legacy"]))
    else:
        out["hint"] = "未检出遗留件，目录干净。"
    return out


# ══ 【Bug-16/17 修复】房间产物归属注册表 ═══════════════════════════════════
# 原缺陷：mode_gate 判断"房间是否有磁盘产物"时，只用"工作目录里是否有
#   最近 mtime 的文件"来推断 —— 没有把文件与【房间名/子代理】绑定。
#   实测：结构件房间刚产出的 DSH_车架底板.SLDPRT，被同时归到 4 个房间
#   名下（结构件/传动机构/壳体机架/支撑结构）的 artifact 里，
#   各房间 evidence.disk_artifact=true、age_sec=2.5。
#   后果：① 房间完成判定过早（别的房间造个文件就当成自己完成了）；
#         ② recover/room-end 基于错误 evidence 误判；
#         ③ 无法追溯"哪个房间造了哪个零件"。
#
# 修复：提供显式登记接口，小屋 save 后调用：
#     python mode_gate.py room-artifact <房间名> <文件路径> [零件名]
#   登记表写入 artifacts_registry.json，按【房间名】隔离；
#   _recent_artifact_evidence 优先用登记表判定归属，扫描 mtime 降级为兜底。
ARTIFACT_REGISTRY_FILE = os.path.join(STATE_DIR, "artifacts_registry.json")


def _load_artifact_registry():
    """读取产物归属登记表 {room: [{path, name, ts, at}]}（失败返回 {}）。"""
    try:
        if os.path.exists(ARTIFACT_REGISTRY_FILE):
            with open(ARTIFACT_REGISTRY_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
    except Exception:
        pass
    return {}


def _save_artifact_registry(data):
    try:
        with open(ARTIFACT_REGISTRY_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def cmd_room_artifact(room, file_path, part_name=None):
    """【Bug-16/17】显式登记"本房间产出了哪个文件"，取代 mtime 猜测。

    用法（小屋在 save 之后调用）：
        python mode_gate.py room-artifact 结构件 "C:/.../DSH_车架底板.SLDPRT" 车架底板

    登记后：
      · room-status 的 artifact 只显示【本房间登记过】的文件；
      · residue-check / recover 的房间存活判定同样以登记为准；
      · 未登记的旧文件不再"共享"给所有房间（消除 Bug-16 的 4 房间共用）。
    """
    _room = str(room or "").strip()
    _fp = str(file_path or "").strip()
    if not _room:
        return {"ok": False, "error": "缺少房间名"}
    if not _fp:
        return {"ok": False, "error": "缺少文件路径"}
    _abs = os.path.abspath(_fp)
    _exists = os.path.exists(_abs)
    _mtime = None
    if _exists:
        try:
            _mtime = os.path.getmtime(_abs)
        except Exception:
            _mtime = None
    data = _load_artifact_registry()
    _list = data.get(_room)
    if not isinstance(_list, list):
        _list = []
    _name = str(part_name or os.path.splitext(os.path.basename(_abs))[0])
    # ── 【Bug-09 修复】去重必须【大小写不敏感】──────────────────────────
    # 实测缺陷：registry 登记为小写 .sldprt，而磁盘最终文件是大写 .SLDPRT
    #   （SW 保存时重命名），原来的精确比较去不掉重 → 同一零件出现两条记录，
    #   且扫描脚本按登记路径找不到文件 → 漏匹配、去重失效。
    # 现在用 normcase 归一化后比较，并【回写磁盘真实大小写路径】。
    _abs_key = os.path.normcase(os.path.abspath(_abs))
    _real_abs = _abs
    try:
        if _exists:
            _d, _n = os.path.split(_abs)
            if os.path.isdir(_d):
                for _e in os.listdir(_d):
                    if _e.lower() == _n.lower():
                        _real_abs = os.path.join(_d, _e)
                        break
    except Exception:
        _real_abs = _abs
    _list = [x for x in _list if isinstance(x, dict) and
             os.path.normcase(os.path.abspath(str(x.get("path") or ""))) != _abs_key]
    _list.append({"path": _real_abs, "name": _name, "exists": bool(_exists),
                  "mtime": _mtime, "ts": time.time(),
                  "at": time.strftime("%Y-%m-%d %H:%M:%S")})
    data[_room] = _list[-200:]   # 防无限增长
    ok = _save_artifact_registry(data)
    return {"ok": ok, "room": _room,
            "artifact": {"path": _real_abs, "name": _name, "exists": bool(_exists)},
            "registered_count": len(data[_room]),
            "file": ARTIFACT_REGISTRY_FILE,
            "note": ("已登记本房间产物归属（大小写不敏感去重，Bug-09）；"
                     "room-status 的 artifact 判定将以登记表为准（Bug-16/17）。")}


def _registered_artifacts(room):
    """取本房间【显式登记】的产物列表（含存在性复核）。"""
    rec = _load_artifact_registry().get(str(room))
    if not isinstance(rec, list):
        return []
    out = []
    for it in rec:
        if not isinstance(it, dict):
            continue
        _p = it.get("path")
        if not _p:
            continue
        _ex = os.path.exists(_p)
        out.append({"path": _p, "name": it.get("name"), "exists": bool(_ex),
                    "age_sec": (round(time.time() - os.path.getmtime(_p), 1)
                                if _ex else None)})
    return out


def _recent_artifact_evidence(room, window=None, since=None):
    """【C1 修复】扫描工作目录，判断该房间是否有【近期落盘的模型产物】。

    测试反馈：房间被判 stale，但"磁盘证明它们真在产出模型"。
      心跳是小屋"主动上报"的行为，专心建模时常常忘记；
      而模型文件落盘是客观事实 —— 用它当存活证据最可靠。

    搜索位置（按优先级）：
      1. mode_state.json 里记录的 work_dir
      2. 工程模式根目录下的 output/ 与 temp/
      3. 常见的桌面输出目录（C:/Users/<user>/Desktop/test 等）

    Args:
        since: 若给出（房间 started_at），则视为"该房间启动后产生的产物"，
               与绝对窗口取【更宽松】者 —— 长建模（如已跑 20 分钟）依然算活跃。

    Returns: {"found": bool, "newest": {...}|None, "scanned_dirs": [...], "count": int}
    """
    if window is None:
        window = SW_ARTIFACT_WINDOW
    now = time.time()

    # ══ 【Bug-16/17 修复】优先用【显式登记表】判定归属 ═════════════════════
    # 原缺陷：只按"工作目录里最近 mtime"推断 → 一个房间刚产出的文件会被
    #   同时归到多个房间名下（实测 4 个房间共用同一份 DSH_车架底板.SLDPRT）。
    # 修复：若本房间有登记产物，直接以登记表为准 —— 这是【确定性归属】，
    #   不再让别的房间"蹭"到别人的产物；无登记时才退回 mtime 扫描（兼容旧流程）。
    # ── 【Bug2 修复·真正的根因】登记表也必须按【当前任务】过滤 ──────────
    # 测试部复测：disk_artifact 仍指向 20261001_203321_77D09BD3\DSH_电池仓.sldprt。
    # 根因不在目录扫描（那部分已修），而在【登记表优先】这条路径：
    #   artifacts_registry.json 里保留了【上一个任务】的登记记录，
    #   而本函数一旦发现本房间有登记就直接返回 —— 旧任务产物因此
    #   永久优先于当前任务的真实产物，看起来"一直指向历史目录"。
    # 修复：登记项必须位于【当前任务交付目录】之下才被采纳；
    #   不在当前任务目录下的登记项视为历史残留（不参与判定）。
    _cur_task_dir = None
    try:
        _wf_st = _read_json_file(WORKFLOW_STATE_PATH) or {}
        _wd = _wf_st.get("work_dir")
        if _wd:
            _cur_task_dir = os.path.abspath(_wd)
    except Exception:
        _cur_task_dir = None
    _registered_all = _registered_artifacts(room)
    _registered = []
    _stale_registered = []
    for _it in _registered_all:
        _p = str(_it.get("path") or "")
        if not _p:
            continue
        if _cur_task_dir:
            try:
                _ap = os.path.abspath(_p)
                # 只接受当前任务目录下的产物（含子目录）
                if _ap == _cur_task_dir or _ap.startswith(_cur_task_dir + os.sep):
                    _registered.append(_it)
                else:
                    _stale_registered.append(_it)
            except Exception:
                _registered.append(_it)
        else:
            # 拿不到当前任务目录时保持旧行为（避免误伤单任务场景）
            _registered.append(_it)
    if _registered:
        _exist = [x for x in _registered if x.get("exists")]
        _newest = None
        if _exist:
            _newest = min(_exist, key=lambda x: (x.get("age_sec")
                                                 if x.get("age_sec") is not None
                                                 else 1e18))
        return {
            "found": bool(_exist),
            "newest": _newest,
            "count": len(_exist),
            "registered": _registered,
            "stale_registered_count": len(_stale_registered),
            "task_dir": _cur_task_dir,
            "source": "artifacts_registry.json（显式登记·已按当前任务过滤）",
            "scanned_dirs": [],
            "window_sec": None,
        }
    # 登记项全为历史残留 → 继续走下面的目录扫描（当前任务真实产物）

    # 有效判定窗口 = max(绝对窗口, 自房间启动以来的时长 + 裕量)
    #   · 绝对窗口：保证"最近动过" 
    #   · 自启动窗口：保证"这房间开始后产出的东西"不被时间轴误杀
    eff_window = window
    if since:
        try:
            eff_window = max(window, (now - float(since)) + 120.0)
        except Exception:
            pass
    dirs = []
    # ── 【Bug2 修复·彻底版】本任务交付目录必须来自【当前任务】─────────
    # 测试部复测：disk_artifact 仍指向历史任务目录
    #   （...\test\20261001_203321_77D09BD3\DSH_电池仓.sldprt），
    #   而当前任务目录是 20261001_230715_AD844F4D。
    # 根因：原实现从 load_state()（= mode_state.json）取 work_dir，
    #   但 mode_state.json 【根本没有 work_dir 键】—— 该字段由
    #   workflow_gate 写在 workflow_state.json 里。于是这里取不到，
    #   退化成"只扫 output/"，而历史残留目录又被别的路径带进来。
    # 修复：按优先级取【当前任务】交付目录：
    #   ① workflow_state.json 的 work_dir（init 时为每个任务创建）
    #   ② mode_state.json 的 work_dir（若某些部署写在这里）
    _task_work_dir = None
    for _st_getter, _st_name in (
            (lambda: _read_json_file(WORKFLOW_STATE_PATH), "workflow_state"),
            (lambda: load_state(), "mode_state")):
        try:
            _stv = _st_getter() or {}
            _wd = _stv.get("work_dir")
            if _wd and os.path.isdir(_wd):
                _task_work_dir = os.path.abspath(_wd)
                break
        except Exception:
            continue
    if _task_work_dir:
        dirs.append(_task_work_dir)
    # 工程模式自身 output/（当前任务产物也可能落这里）
    base = os.path.dirname(os.path.abspath(__file__))
    dirs.append(os.path.abspath(os.path.join(base, "..", "output")))
    # 【已移除】桌面/历史目录扫描：宁可少认产物，也绝不把历史任务的
    #   产物算作本房间产出（Bug2）。

    exts = tuple(e.lower() for e in SW_ARTIFACT_GLOB_EXT)
    newest, hits, seen = None, 0, set()
    for d in dirs:
        try:
            d = os.path.abspath(d)
            if d in seen or not os.path.isdir(d):
                continue
            seen.add(d)
            for fn in os.listdir(d):
                if not fn.lower().endswith(exts):
                    continue
                if fn.startswith("~$"):      # SW 临时锁文件，不算产物
                    continue
                p = os.path.join(d, fn)
                try:
                    mt = os.path.getmtime(p)
                except Exception:
                    continue
                if (now - mt) <= eff_window:
                    hits += 1
                    if newest is None or mt > newest["mtime"]:
                        newest = {"name": fn, "dir": d, "mtime": mt,
                                  "age_sec": round(now - mt, 1),
                                  "time": time.strftime("%Y-%m-%d %H:%M:%S",
                                                        time.localtime(mt))}
        except Exception:
            continue
    return {"found": hits > 0, "newest": newest, "count": hits,
            "scanned_dirs": sorted(seen),
            "window_sec": round(eff_window, 1)}


def _safe_room_name(room):
    """把房间名规整为可用作文件名的形式（去掉路径分隔符等危险字符）。"""
    return "".join(c for c in room if c.isalnum() or c in " _-").strip()


def _report_path(room):
    return os.path.join(REPORTS_DIR, _safe_room_name(room) + ".report.json")


def _read_json_file(path):
    """【Bug2 修复】稳健读取 JSON 文件：兼容 BOM 与历史 GBK 残留。

    问题现象：reports/*.report.json 内容存在，但主对话读到的有时是乱码，
      房间状态因此被误判。
    根因：文件曾被以不同编码（GBK/UTF-8-BOM）写过；用固定 utf-8 读会
      抛异常或产生 mojibake，上层 catch 后返回空 → 表现为"拿不到"。
    修复：按 utf-8-sig → utf-8 → gbk 顺序回退，全部失败才返回 None。

    ── 【Bug-09 修复】剔除 latin-1 兜底，杜绝"乱码静默成功" ──────────────
    原实现最后回退 latin-1。但 latin-1 能解码【任意字节序列】、永不抛错 ——
      一份 GBK 写的中文 JSON 会被 latin-1 "成功"解成 mojibake，函数返回
      看似有效的对象，而房间名/阶段名全是乱码 → 上层据此误判房间状态。
      这比"读失败"更危险：读失败会走兜底逻辑，乱码成功则一路错到底。
    现在：只接受 utf-8-sig / utf-8 / gbk 三种真编码；全部失败返回 None
      （调用方按"读不到"处理，而非拿到乱码当真相）。
    """
    if not path or not os.path.exists(path):
        return None
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            with open(path, "r", encoding=enc) as f:
                return json.load(f)
        except Exception:
            continue
    return None


# 小屋进度上报的标准阶段（写进小屋 prompt 的规范）
REPORT_STAGES = ("registered", "params_shown", "params_confirmed", "modeling",
                 "part_done", "validating", "done", "failed")


def _pending_confirm_path(room):
    """每个房间的待确认状态文件（记录哪些零件在等用户签字）。"""
    os.makedirs(REPORTS_DIR, exist_ok=True)
    return os.path.join(REPORTS_DIR, _safe_room_name(room) + ".pending.json")


def _load_pending(room):
    p = _pending_confirm_path(room)
    try:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f) or {}
    except Exception:
        pass
    return {}


def _save_pending(room, data):
    p = _pending_confirm_path(room)
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _norm_part(s):
    """零件名归一化：去掉括号内容、空白、常见修饰，便于宽松匹配。

    例："大臂(300mm)" -> "大臂"；"小臂 已确认" -> "小臂"。
    """
    try:
        t = str(s or "")
        # 【边界修正】括号在开头时（如 "(300mm)"）截断会得到空串，
        #   这种情况下保留原串更安全（否则会把所有带前缀括号的名字归一化成空，
        #   反而造成"任何零件都能匹配上"，属于过度匹配）。
        for ch in ("（", "("):
            i = t.find(ch)
            if i > 0:
                t = t[:i]
        t = t.replace(" ", "").replace("\t", "")
        for w in ("已确认", "待确认", "已建模", "建模中"):
            t = t.replace(w, "")
        return t.strip()
    except Exception:
        return ""


def cmd_confirm_part(room, part, by="user"):
    """【Bug1 修复】由【用户确认】来解锁某个零件的 params_confirmed。

    背景：结构件小屋在"大臂确认后"，对"小臂/连杆/立柱"自行上报了
    params_confirmed（时间戳显示 shown 与 confirmed 间隔仅 0.05~0.08 秒，
    远短于人工确认所需），并注明"沿用大臂已确认基础" —— 属于子代理越权
    代替用户签字，违反 C 模式"每个零件必须单独确认"的铁律。

    根因：room-report 无条件接受任何 stage，小屋可以自己给自己发确认。

    修复：params_confirmed 改为【需要令牌】——必须有本命令写入的授权记录，
    否则判定为越权，拒绝该上报并标记违规。
    """
    room = (room or "").strip()
    part = (part or "").strip()
    if not room or not part:
        return {"ok": False, "error": "用法: confirm-part <房间名> <零件名>"}
    # ── 【C12 修复】顺序校验：必须先 params_shown 再 confirm ──────────────
    # 测试反馈：confirm-part 与 params_confirmed 存在顺序竞态 ——
    #   必须先 confirm-part 再上报，颠倒会记"越权确认"违规，
    #   但 confirm-part 自身不提示"必须先授权"，调用方极易踩坑。
    # 修复：若尚未看到该零件的 params_shown 上报，给出明确提示
    #   （默认仅警告不阻断，因为部分流程可能跳过 shown 直接确认；
    #     加 --strict 可强制要求先 shown）。
    data = _load_pending(room)
    rp = _report_path(room)
    _shown_parts = []
    _last_stage = None
    try:
        if os.path.exists(rp):
            with open(rp, "r", encoding="utf-8") as f:
                _h = (json.load(f) or {}).get("history", [])
            for _rec in _h:
                if _rec.get("stage") == "params_shown":
                    _shown_parts.append(str(_rec.get("detail") or ""))
            if _h:
                _last_stage = _h[-1].get("stage")
    except Exception:
        pass
    _has_shown = any(part in s for s in _shown_parts)
    _strict = "--strict" in sys.argv
    if not _has_shown and _strict:
        return {"ok": False, "room": room, "part": part,
                "error": ("【顺序错误】尚未收到零件 '%s' 的 params_shown 上报，"
                          "不能直接授权确认。" % part),
                "hint": ("正确顺序：1) 小屋输出零件参数表并调 "
                         "room-report <房间> params_shown '<零件名> 待确认'；"
                         "2) 用 ask_user.py 弹卡片让用户选择；"
                         "3) 用户作答后由主对话调 confirm-part 授权。")}

    # ── 【C6 修复】令牌必须与"面板作答"挂钩，杜绝代用户签字 ──────────────
    # 测试反馈：小屋报"已确认"但面板卡片从未作答（result-child 返回"结果不存在"），
    #   两套机制不一致 → 存在被误判"代用户签字"的风险。
    # 修复：confirm-part 记录【授权来源证据】——
    #   若能读到该房间的待确认文件/pending 中的用户作答痕迹，一并登记；
    #   无作答痕迹时明确标注 by="user?unverified"，让后续审计可追溯。
    # ── 【Bug#8 修复】作答证据匹配要"宽进严出" ──────────────────────────
    # ask_user.py 回写时用的键可能是「题目 header」或「用户选中的标签」，
    #   与本命令传入的零件名未必完全相等（如 "大臂" vs "大臂(300mm)"）。
    # 匹配策略（按优先级）：
    #   ① 精确相等
    #   ② 互相包含（part in key 或 key in part）
    #   ③ 去掉括号/空格后的主体名相等
    # 命中即记录完整证据，供审计追溯"用户到底点了什么"。
    _answer_evidence = None
    try:
        _pd = _load_pending(room)
        _ans = _pd.get("answers") or {}
        _hit_key = None
        if part in _ans:
            _hit_key = part
        else:
            _pn = _norm_part(part)
            for _k in _ans.keys():
                _kn = _norm_part(_k)
                if not _kn:
                    continue
                if _pn and (_pn in _kn or _kn in _pn):
                    _hit_key = _k
                    break
        if _hit_key:
            _answer_evidence = {
                "source": "pending_answers",
                "matched_key": _hit_key,
                "value": _ans[_hit_key],
            }
    except Exception:
        pass
    # 【Bug#8 修复】若此刻没读到证据，写回前再读一次磁盘 ——
    #   作答可能正好发生在本次读取与写入之间（竞态窗口很小但真实存在）。
    if not _answer_evidence:
        try:
            _pd2 = _load_pending(room)
            _ans2 = _pd2.get("answers") or {}
            _hk2 = part if part in _ans2 else None
            if _hk2 is None:
                _pn2 = _norm_part(part)
                for _k2 in _ans2.keys():
                    _kn2 = _norm_part(_k2)
                    if _pn2 and _kn2 and (_pn2 in _kn2 or _kn2 in _pn2):
                        _hk2 = _k2
                        break
            if _hk2:
                _answer_evidence = {
                    "source": "pending_answers",
                    "matched_key": _hk2,
                    "value": _ans2[_hk2],
                }
        except Exception:
            pass
    # ── 【Bug#8 修复·二次】写回时必须【合并】而非覆盖 ──────────────────
    # 原实现：data 在函数开头读取，随后 _save_pending(room, data) 整体写回。
    #   但 ask_user.py 的作答回写发生在【这之后】——用旧副本覆盖整个文件，
    #   会把刚写入的 answers 直接冲掉（作答记录被销毁，证据永远读不到）。
    # 修复：重新读取磁盘上的当前内容，只更新 grants 字段后再写回，
    #   保留 answers / last_answer_at 等其他字段不被破坏。
    _cur = _load_pending(room) or {}
    if not isinstance(_cur, dict):
        _cur = {}
    # 若磁盘上已有 answers，说明期间有新的作答 → 以磁盘为准
    grants = _cur.get("grants") or data.get("grants") or {}
    grants[part] = {"by": by, "ts": time.time(),
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "shown_seen": bool(_has_shown),
                    "answer_evidence": _answer_evidence,
                    "verified": bool(_has_shown or _answer_evidence)}
    _cur["grants"] = grants
    _save_pending(room, _cur)
    _out = {"ok": True, "room": room, "part": part, "confirmed_by": by,
            "shown_seen": bool(_has_shown),
            # 【Bug#8 修复】把作答证据一并回传，便于主对话/审计直接看到
            #   "用户到底在面板上点了什么"，而不是只能看 verified 这一个布尔。
            "answer_evidence": _answer_evidence,
            "verified": bool(_has_shown or _answer_evidence),
            "message": "已授权该零件的确认；小屋随后上报 params_confirmed 才会被接受。"}
    if not _has_shown:
        _out["note"] = ("⚠️ 未检测到该零件的 params_shown 上报（可能顺序颠倒）。"
                        "若确认流程无误可忽略；如需强制先 shown 请加 --strict。")
    if not _answer_evidence:
        _out["audit"] = ("未读到面板作答痕迹，授权来源标记为用户手动确认（by=%s）。"
                         "若需更严格的可追溯性，请确保 ask_user.py 的作答已回写。" % by)
    return _out


def _confirm_granted(room, detail):
    """检查 detail 里提到的零件是否已获用户授权确认。

    detail 形如 "小臂 已确认" / "大臂(300mm) 已确认" → 提取零件名做前缀匹配。
    未授权则返回 (False, 零件名)。
    """
    data = _load_pending(room)
    grants = data.get("grants") or {}
    if not grants:
        return False, (detail or "").split()[0] if detail else ""
    text = detail or ""
    for part, info in grants.items():
        if part and part in text:
            return True, part
    # 兜底：若只有一个授权零件且 detail 无法解析，视为不匹配（从严）
    return False, (text.split()[0] if text.split() else "")


def cmd_room_report(room, stage, detail=""):
    """【bug6修复】小屋进度旁路上报：写文件，不走对话通道。

    关键设计：小屋在关键节点主动写进度，主对话用 room-report-read 旁路读取，
    双方零干扰——彻底替代"主对话 send_message 问进度"的插队做法。
    写报告同时刷新心跳（上报即存活证明）。

    【Bug1 修复】params_confirmed 是【唯一需要授权】的阶段：
    小屋不得自行宣布"用户已确认"，必须由 confirm-part 先写入授权令牌。
    """
    # ── 越权拦截：params_confirmed 需要用户授权令牌 ──────────────────────
    # ══ 【Bug-08 修复】令牌机制【仅 C 模式（步步确认）强制】══════════════
    # 台账待观察项：A 模式（完全自主，ask_user_at=[]）下 confirm-part 是否仍被
    #   强制调用导致卡住？答案是——原实现【无条件强制】，A 模式小屋若没走
    #   confirm-part 就上报 params_confirmed 会被判违规，与"完全自主"语义矛盾。
    # 修复：读 workflow_state 的 strategy.confirm_part_token_required；
    #   · True（C 模式）→ 保持强制令牌校验；
    #   · False（A/B 模式）→ 跳过令牌校验，但仍记录一条"自主确认"审计项，
    #     便于事后复核（自主 ≠ 无据）。
    _token_required = True
    try:
        if os.path.exists(WORKFLOW_STATE_PATH):
            with open(WORKFLOW_STATE_PATH, "r", encoding="utf-8") as _wf:
                _wfd = json.load(_wf) or {}
            _strat = ((_wfd.get("subagent_config") or {}).get("strategy") or {})
            if "confirm_part_token_required" in _strat:
                _token_required = bool(_strat.get("confirm_part_token_required"))
    except Exception:
        _token_required = True   # 读不到时保守按 C 模式处理

    if stage == "params_confirmed" and _token_required:
        ok_grant, part = _confirm_granted(room, detail)
        if not ok_grant:
            # 记录越权行为，供主对话审计
            os.makedirs(REPORTS_DIR, exist_ok=True)
            vpath = os.path.join(REPORTS_DIR, _safe_room_name(room) + ".violations.json")
            try:
                viol = []
                if os.path.exists(vpath):
                    with open(vpath, "r", encoding="utf-8") as f:
                        viol = (json.load(f) or {}).get("items", [])
                viol.append({"room": room, "part": part, "detail": detail,
                             "ts": time.time(),
                             "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                             "reason": "子代理未经用户确认自行上报 params_confirmed"})
                with open(vpath, "w", encoding="utf-8") as f:
                    json.dump({"room": room, "items": viol[-50:]}, f,
                              ensure_ascii=False, indent=2)
            except Exception:
                pass
            return {
                "ok": False, "room": room, "stage": stage, "violation": True,
                "error": ("【违规·越权确认】零件 '%s' 未经用户确认。"
                          "C 模式铁律：每个零件必须单独获得用户授权。" % (part or detail)),
                "hint": ("正确流程：先用 ask_user.py 向用户弹卡片；用户点选后，"
                         "由主对话执行 mode_gate.py confirm-part <房间名> <零件名> "
                         "写入授权，之后本上报才会被接受。"
                         "沿用上一个零件的确认 = 违规，必须逐件确认。"),
            }

    os.makedirs(REPORTS_DIR, exist_ok=True)
    path = _report_path(room)
    rec = {"room": room, "stage": stage, "detail": detail,
           "ts": time.time(), "time": time.strftime("%Y-%m-%d %H:%M:%S")}
    history = []
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                history = (json.load(f) or {}).get("history", [])
    except Exception:
        history = []
    history.append(rec)
    history = history[-20:]
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"room": room, "latest": rec, "history": history},
                  f, ensure_ascii=False, indent=2)
    hb = _heartbeat_path(room)
    try:
        with open(hb, "w", encoding="utf-8") as f:
            json.dump({"name": room, "ts": time.time(), "pid": os.getpid(),
                       "via": "report"}, f)
    except Exception:
        pass
    return {"ok": True, "room": room, "stage": stage, "reported_at": rec["time"],
            "hint": "已上报并刷新心跳"}


def cmd_room_report_read(room=None):
    """【bug6修复】主对话旁路读取小屋进度报告（零干扰，绝不打断小屋干活）。"""
    os.makedirs(REPORTS_DIR, exist_ok=True)
    if room:
        path = _report_path(room)
        # ── 【Bug2 修复】改用 _read_json_file（兼容 BOM/GBK 残留）──
        data = _read_json_file(path)
        if data:
            data.pop("_source", None)
            return {"ok": True, "room": room,
                    "report_file": os.path.basename(path),
                    **data}
        if os.path.exists(path):
            # 文件在但读不出来 → 明确区分"文件损坏"与"没上报"，
            # 避免主对话把"读不到"误当成"小屋没干活"。
            return {"ok": False, "room": room,
                    "report_file": os.path.basename(path),
                    "file_exists": True,
                    "error": ("报告文件存在但无法解析（编码损坏或 JSON 非法）。"
                              "请用 room-status 交叉核对：其 evidence.recent_report "
                              "仍会按文件 mtime 判定小屋是否活跃。"),
                    "hint": "不要据此判死房间；mtime 新鲜即说明小屋在干活。"}
        return {"ok": False, "room": room,
                "report_file": os.path.basename(path),
                "file_exists": False,
                "error": "无进度报告（小屋尚未上报任何节点，配合 room-status 判断死活）"}
    out = []
    try:
        for fn in sorted(os.listdir(REPORTS_DIR)):
            if fn.endswith(".report.json"):
                try:
                    with open(os.path.join(REPORTS_DIR, fn), "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if data:
                        out.append(data)
                except Exception:
                    pass
    except Exception:
        pass
    return {"ok": True, "reports": out, "count": len(out),
            "stages": list(REPORT_STAGES),
            "hint": ("latest.stage=小屋自报当前环节。旁路读取不打断小屋。"
                     "判死标准=room-status 为 stale 且报告长时间无更新；"
                     "send_message 没有立即回复不代表小屋死了（消息会排队）")}


def _room_status_of(name, info, subs):
    """推导单个房间的精确状态（六态）。

    completed   : 已正常收尾（ended_at 有值）
    failed      : 已回退待重做（failed_at 有值）
    running     : active 且心跳新鲜（<=SW_HEARTBEAT_TIMEOUT 秒）
    starting    : active 且无心跳但登记未超过 SW_START_GRACE 秒 —— 正常启动中，禁止判死
    stale       : active 且心跳超时 —— 判定死亡，需要重启
    no_heartbeat: active 且登记超过宽限期仍无任何心跳 —— 可疑，需人工核对
    """
    now = time.time()
    hb_path = _heartbeat_path(name)
    hb_age = None
    try:
        if os.path.exists(hb_path):
            hb_age = round(now - os.path.getmtime(hb_path), 1)
    except Exception:
        pass
    started = info.get("started_at")
    # ── 【Bug2 修复】平台侧交叉核对，消除 running vs no_heartbeat 口径噪声 ──
    # 问题：心跳文件只有子代理【主动调用 room-heartbeat】才会刷新，
    #   很多小屋专心建模、不调心跳 → 门禁判 no_heartbeat（可疑），
    #   而 DSH 平台侧该 subagent 明明 running —— 两套口径互相打架，
    #   主对话据 no_heartbeat 去 recover/restart，反而误杀正在干活的屋子。
    #
    # 修复：把"平台侧是否仍在跑"作为判据之一纳入状态推导：
    #   · 有平台侧存活佐证（进程/子代理登记新鲜）时，no_heartbeat 降级为
    #     "running_unverified"（运行中·心跳未上报），【禁止判死、禁止重启】；
    #   · 只有同时满足"无心跳 + 无平台佐证 + 超宽限期"才判 no_heartbeat。
    _sub_rec_raw = subs.get(name)
    _sub_assigned_age = None
    try:
        if isinstance(_sub_rec_raw, dict) and _sub_rec_raw.get("assigned_at"):
            _sub_assigned_age = now - float(_sub_rec_raw["assigned_at"])
    except Exception:
        _sub_assigned_age = None
    # 平台侧证据：subagent 登记仍在宽限窗口内（说明刚创建、平台侧应仍在跑）
    _platform_evidence = bool(
        _sub_assigned_age is not None and _sub_assigned_age <= SW_START_GRACE
    )
    # 进度报告也算"活着的佐证"（小屋上报过即证明它跑过）
    # ── 【Bug3 修复】证据窗口从 SW_HEARTBEAT_TIMEOUT(180s) 放宽到
    #    SW_EVIDENCE_WINDOW(900s)：建模一轮 5~10 分钟不上报是常态，
    #    报告年龄 200s 并不意味着小屋死了。
    _has_report = False
    _rep_age_sec = None
    try:
        _rp_probe = _report_path(name)
        if os.path.exists(_rp_probe):
            _rep_age_sec = round(now - os.path.getmtime(_rp_probe), 1)
            _has_report = _rep_age_sec <= SW_EVIDENCE_WINDOW
    except Exception:
        _has_report = False

    # ── 【Bug3 修复·新增判据】待确认文件也是活跃证据（C模式正在走流程）──
    _has_pending = False
    try:
        _pd_probe = _pending_confirm_path(name)
        if os.path.exists(_pd_probe):
            _has_pending = (now - os.path.getmtime(_pd_probe)) <= SW_EVIDENCE_WINDOW
    except Exception:
        _has_pending = False

    # ── 【C1 修复·最强判据】磁盘产出证据 ──────────────────────────────
    # 测试反馈：房间被判 stale，但"磁盘证明它们真在产出模型"。
    #   模型文件落盘是客观事实，比"小屋有没有记得跳心跳"可靠得多。
    #   只要工作目录里有【近期新增/修改】的 .SLDPRT/.SLDASM 等产物，
    #   就判定该房间在干活，绝不判死。
    _art = _recent_artifact_evidence(name, since=started)
    _has_artifact = bool(_art.get("found"))

    _recent_evidence = bool(_platform_evidence or _has_report or
                            _has_pending or _has_artifact)
    _hb_expired = (hb_age is not None and hb_age > SW_HEARTBEAT_TIMEOUT)

    # ══ 【Bug-23 修复】平台存活状态是【权威判据】，优先于心跳 ═══════════════
    # 用户明确要求："去掉心跳机制，直接采用 DSH 平台 running 状态判定子代理
    #   是否死亡"。实测证据：传动机构/壳体机架的心跳 age 涨到 1222~1777s，
    #   但平台侧 list_agents 始终 running、且持续产出零件（减速齿轮/舵机连杆/
    #   小齿轮/舵机支架/加强筋/上罩）—— 心跳完全失真，主对话据它 recover
    #   只会误杀正在干活的屋子。
    # 因此：平台说 running  → 直接判 running（心跳/证据一律不看）；
    #       平台说 inactive → 允许进入 stale 判定（不再被证据无限期兜住）；
    #       平台说 missing  → 明确报出"登记丢失"（Bug-32 场景），不猜。
    _plat_status, _plat_age = _platform_liveness(name)

    if info.get("failed_at"):
        status = "failed"
    elif info.get("ended_at"):
        status = "completed"
    elif _plat_status == "running":
        # 平台确认在跑 —— 权威结论，禁止判死（心跳过期也不影响）
        status = "running"
    elif _plat_status == "missing":
        # 平台侧找不到该子代理（Bug-32：fork 后未真正启动）
        status = "no_heartbeat"
    elif hb_age is not None and not _hb_expired:
        status = "running"
    elif _hb_expired:
        # ── 【Bug3 修复】心跳过期不再直接判死，改为三重条件 ──────────────
        #   ① 心跳超过 SW_HEARTBEAT_TIMEOUT
        #   ② 且【没有任何近期证据】(_recent_evidence=False)
        #   ③ 且已超过滞后宽限 SW_STALE_GRACE（刚过期时先观察）
        _age_over = hb_age - SW_HEARTBEAT_TIMEOUT
        if _recent_evidence:
            # 有报告/平台/待确认等近期证据 → 小屋在干活，只是没空跳心跳
            status = "running_unverified"
        elif _age_over > SW_STALE_GRACE:
            status = "stale"
        else:
            status = "running_unverified"
    elif started is not None and (now - started) <= SW_START_GRACE:
        status = "starting"
    elif _recent_evidence:
        # 没心跳，但有平台侧/报告佐证 → 视为运行中（心跳未上报），绝不判死
        status = "running_unverified"
    elif started is not None:
        status = "no_heartbeat"
    else:
        status = "starting"
    rec = subs.get(name)
    sub_rec = rec if isinstance(rec, dict) else (
        {"id": rec, "assigned_at": None} if rec else None)
    latest_report = None
    try:
        rp = _report_path(name)
        if os.path.exists(rp):
            with open(rp, "r", encoding="utf-8") as _f:
                latest_report = (json.load(_f) or {}).get("latest")
    except Exception:
        latest_report = None
    return {
        "room": name,
        "status": status,
        "active": bool(info.get("active")),
        "latest_report": latest_report,
        "subagent_id": (sub_rec or {}).get("id"),
        "subagent_assigned_age_sec": (round(now - sub_rec["assigned_at"], 1)
                                      if sub_rec and sub_rec.get("assigned_at") else None),
        "started_age_sec": round(now - started, 1) if started else None,
        "heartbeat_age_sec": hb_age,
        # ── 【Bug3 修复】暴露判据明细，便于主对话核对而不必猜 ──
        "heartbeat_timeout_sec": SW_HEARTBEAT_TIMEOUT,
        "stale_grace_sec": SW_STALE_GRACE,
        "report_age_sec": _rep_age_sec,
        # ── 【Bug-23】平台侧权威状态（有值时优先于心跳）──
        "platform_status": _plat_status,
        "platform_age_sec": (round(_plat_age, 1) if _plat_age is not None else None),
        "authority": ("platform(list_agents)" if _plat_status else "evidence/heartbeat"),
        "evidence": {
            "platform": _platform_evidence,
            "recent_report": _has_report,
            "pending_confirm": _has_pending,
            "disk_artifact": _has_artifact,      # 【C1】磁盘产出（最强证据）
            "any": _recent_evidence,
        },
        # 【C1】把最新产物一并回传，便于主对话直接看到"它刚做了什么"
        "artifact": _art.get("newest"),
    }


ROOM_STATUS_HINT = (
    "starting=刚登记正在启动(宽限%d秒内,正常,等待即可,严禁说'没反应'); "
    "running=心跳正常正在运行(严禁说'没反应',严禁重启); "
    "running_unverified=【Bug2】无心跳但有平台侧佐证(子代理刚登记/有近期进度报告)"
    " → 视为运行中,严禁判死、严禁重启(心跳只是小屋没主动上报而已); "
    "stale=【Bug3】心跳超%d秒 + 无任何近期证据 + 超过%d秒滞后宽限 三者同时成立才判死; "
    "no_heartbeat=无心跳+无平台佐证+超宽限期(此时才可疑,先用 list_agents 核对); "
    "completed=已完成(可 room-end 对账+interrupt_agent 清理); "
    "failed=已回退待重做(重新 room-start 后开新小屋); "
    "⚠️【Bug3】心跳过期但有近期报告/平台证据时一律判 running_unverified——"
    "小屋建模时没空跳心跳是常态，禁止据此 recover/restart！"
) % (SW_START_GRACE, SW_HEARTBEAT_TIMEOUT, SW_STALE_GRACE)


def _detect_state_copies():
    """检测工程模式是否存在【多份状态副本】（观察点 2/9/18 修复）。

    ── 为什么要单独检测 ────────────────────────────────────────────────
    工程模式在磁盘上有两份部署位置：
      · 安装副本：%DSH_HOME%\\.agent-presets\\engineering\\tools
      · 工作区副本：<repo>\\engineering\\tools
    两份都会有 mode_state.json / workflow_state.json。若【同时存在】，
    就会出现"谁最后写谁生效"：小屋从一份调用、主对话从另一份读，
    房间表 / 推进态 / 凭据互相看不见（实测症状见观察点 2、9、18）。

    本函数只做**只读检测与告警**，不迁移、不改写任何文件 ——
    状态落点由 _store.state_dir() 统一决定（写入侧已收敛），
    这里负责把"另一份也存在"这个事实显式暴露给调用方。

    Returns:
        dict: {conflict: bool, active_dir: str, candidates: [...], note: str|None}
    """
    _active = os.path.abspath(STATE_DIR)
    _cands = []
    # ① 当前权威目录
    _cands.append(_active)
    # ② 脚本自身目录（另一份副本的落点）
    try:
        _b = os.path.abspath(_BASE_DIR)
        if _b not in _cands:
            _cands.append(_b)
    except Exception:
        pass
    # ③ 环境变量显式声明的根
    try:
        for _env in ("DSH_STATE_DIR", "DSH_ENGINEERING_ROOT"):
            _v = (os.environ.get(_env) or "").strip()
            if not _v:
                continue
            _a = os.path.abspath(_v)
            if os.path.basename(_a).lower() != "tools":
                _a = os.path.join(_a, "tools")
            if _a not in _cands:
                _cands.append(_a)
    except Exception:
        pass
    # ④ DSH_HOME 下的标准安装位置
    try:
        _home = os.environ.get("USERPROFILE") or os.environ.get("HOME") or ""
        _dsh = (os.environ.get("DSH_HOME") or "").strip() or (
            os.path.join(_home, ".dsh") if _home else "")
        if _dsh:
            for _sub in (os.path.join(".agent-presets", "engineering", "tools"),
                         os.path.join("engineering", "tools")):
                _a = os.path.abspath(os.path.join(_dsh, _sub))
                if _a not in _cands:
                    _cands.append(_a)
    except Exception:
        pass

    _detail = []
    _with_state = []
    for _d in _cands:
        _entry = {"dir": _d, "exists": os.path.isdir(_d),
                  "has_mode_state": False, "has_workflow_state": False,
                  "mode_state_mtime": None, "is_active": (_d == _active)}
        try:
            _ms = os.path.join(_d, "mode_state.json")
            if os.path.isfile(_ms):
                _entry["has_mode_state"] = True
                _entry["mode_state_mtime"] = time.strftime(
                    "%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(_ms)))
                _with_state.append(_d)
            _entry["has_workflow_state"] = os.path.isfile(
                os.path.join(_d, "workflow_state.json"))
        except Exception:
            pass
        _detail.append(_entry)

    # 冲突 = 除当前权威目录外，还有【另一份确实带 state 文件】的目录
    _others = [d for d in _with_state if d != _active]
    _conflict = bool(_others)
    _note = None
    if _conflict:
        _note = ("⚠️ 检测到【多份工程模式状态副本】：本命令读取 %s，"
                 "但另有 %d 处也存在状态文件（%s）。"
                 "两份状态互不可见，谁最后写谁生效 —— 若发现房间/凭据"
                 "与预期不符，请统一用一个路径调用工具，"
                 "或设置 DSH_STATE_DIR 显式指定唯一状态目录。"
                 % (_active, len(_others), "、".join(_others)))
    return {"conflict": _conflict, "active_dir": _active,
            "candidates": _detail, "note": _note}


def cmd_room_status():
    """输出每个房间的精确状态（主对话判断小屋死活必须用本命令，禁止凭感觉猜）。

    ── 【Bug-22/06 修复】消除"room-status 说 rooms=[]，residue-check 说有 alive"
      的自相矛盾。三处改进：

      ① 【单一权威来源 + 可对账】返回 state_file / base_dir / state_mtime，
         调用方一眼看出本次读的是哪份 mode_state.json —— 实测矛盾的真因正是
         "多副本 mode_gate.py 各读各的 mode_state"（tools/ 空壳 vs 预设副本）。
      ② 【一致性自检】rooms 为空时不再静默返回成功：去 residue-check 的同一份
         房间表与平台登记里复查，若有活着/已登记的房间却 rooms=[]，明确告警。
      ③ 【mode 字段溯源】mode 从 workflow_state 的推进态交叉印证，
         避免内部 mode 字段被误重置为 "1" 时给出误导性结论。
    """
    state = load_state()
    rooms = state.get("rooms", {})
    subs = state.get("subagents", {})
    out = []
    counts = {}
    for name, info in rooms.items():
        rec = _room_status_of(name, info, subs)
        counts[rec["status"]] = counts.get(rec["status"], 0) + 1
        out.append(rec)

    # ── ① 来源可审计 ──────────────────────────────────────────────
    _src = {"state_file": STATE_PATH, "base_dir": STATE_DIR,
            "heartbeats_dir": HEARTBEAT_DIR, "reports_dir": REPORTS_DIR}
    try:
        if os.path.exists(STATE_PATH):
            _src["state_mtime"] = time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(STATE_PATH)))
            _src["state_size"] = os.path.getsize(STATE_PATH)
    except Exception:
        pass
    # ── 【观察点 2/9/18 修复】多副本检测：显式列出"另一份"状态文件 ────────
    # 工程模式在磁盘上有两份副本（工作区 + ~/.dsh 安装目录）。若两份都存在
    #   mode_state.json / workflow_state.json，就会出现"谁最后写谁生效"：
    #   不同小屋/不同终端看到的房间、凭据、推进态可能完全不一致。
    # 实测症状：room-status 指向 .dsh 内置路径，而主对话按技能文档从桌面仓库
    #   调用，状态被写进另一份 → "明明凭据存在却报缺少"。
    # 这里把全部候选并列出来并给出冲突告警，让调用方一眼看出该以哪份为准。
    _multi = _detect_state_copies()
    if _multi.get("conflict"):
        _src["state_dir_candidates"] = _multi["candidates"]
        _src["state_dir_note"] = _multi["note"]
    if _multi.get("conflict"):
        _consistency_pre = _multi["note"]
    else:
        _consistency_pre = None

    # ── ③ mode 字段溯源（与 workflow 推进态交叉印证）──────────────
    _mode_internal = state.get("mode")
    _wf_step = None
    try:
        if os.path.exists(WORKFLOW_STATE_PATH):
            _wf = _read_json_file(WORKFLOW_STATE_PATH) or {}
            _wf_step = _wf.get("step")
    except Exception:
        pass
    _mode_note = None
    # mode="1"（基础零件搭建）但 workflow 已在推进/已有房间 → 明显不一致
    if str(_mode_internal) == "1" and (rooms or (_wf_step not in (None, "idle", ""))):
        _mode_note = ("⚠️ mode 字段为 '1'（基础零件搭建）但存在房间记录或 workflow "
                      "step=%s —— 内部 mode 字段可能被误重置。请以 rooms/workflow "
                      "为准，勿据此判断任务已重置。" % _wf_step)

    # ── ② 一致性自检：rooms 空但实际有房间在跑/已登记 ─────────────
    _consistency = {"consistent": True, "warnings": []}
    if not rooms:
        _subs_n = len(subs or {})
        _hb = []
        try:
            if os.path.isdir(HEARTBEAT_DIR):
                _hb = [f for f in os.listdir(HEARTBEAT_DIR) if f.endswith(".heartbeat")]
        except Exception:
            _hb = []
        if _subs_n or _hb:
            _consistency["consistent"] = False
            _consistency["warnings"].append(
                "rooms 为空，但检测到 %d 个子代理登记 / %d 个心跳文件 —— "
                "可能存在多副本 mode_state（本命令读到的是 %s）。"
                "请用 mode_gate.py doctor 对账。" % (_subs_n, len(_hb), STATE_PATH))
    # ── 【观察点 2/9/18 修复】多副本冲突告警（合并进 consistency）────────
    if _consistency_pre:
        _consistency["consistent"] = False
        _consistency["warnings"].append(_consistency_pre)

    return {"ok": True, "mode": _mode_internal, "mode_name": state.get("mode_name"),
            "mode_note": _mode_note,
            "workflow_step": _wf_step,
            "rooms": out, "counts": counts,
            "source": _src,
            "consistency": _consistency,
            "hint": ROOM_STATUS_HINT}


def cmd_subagent_status():
    """房间↔subagent 对账表（配合 DSH list_agents 交叉核对真实状态）。"""
    state = load_state()
    subs = state.get("subagents", {})
    rooms = state.get("rooms", {})
    now = time.time()
    out = []
    for room, rec in subs.items():
        info = rooms.get(room, {})
        d = rec if isinstance(rec, dict) else {"id": rec, "assigned_at": None}
        out.append({
            "room": room,
            "subagent_id": d.get("id"),
            "assigned_age_sec": round(now - d["assigned_at"], 1) if d.get("assigned_at") else None,
            "room_status": _room_status_of(room, info, subs)["status"],
            "ended": bool(info.get("ended_at")),
            "failed": bool(info.get("failed_at")),
        })
    return {"ok": True, "assignments": out,
            "hint": "用 list_agents 对照 subagent_id 的平台侧真实状态；"
                    "room_status=completed/failed 的小屋必须 interrupt_agent 清理后才能开下一波，禁止不管"}


# ── 【问题3 修复】子代理身份自举 ────────────────────────────────────────────
# 原缺陷：whoami 只从 state["subagents"] 读，而该表由主对话的 subagent-assign
#   在【创建小屋之后】写入 —— 子代理开工第一件事就调 whoami，此刻记录往往还没
#   落盘（时序竞态），于是必然返回「尚未登记」，v1 小屋就此死掉。
#
# 修复策略（三层兜底，任一层命中即可拿到 sessionId）：
#   第1层 · 自报（最可靠）：子代理把自己的 sessionId 通过 --session-id 或
#           环境变量 DSH_SUBAGENT_SESSION_ID 传进来；whoami 立刻把它【回写】
#           到 subagents 表，之后主对话的 subagent-assign 用同一值覆盖即可。
#           这样"先调用者先登记"，彻底消除竞态。
#   第2层 · 环境变量：DSH_SESSION_ID / DSH_SUBAGENT_ID（宿主/包装脚本可能注入）
#   第3层 · 等待重试：以上都没有时，给出明确指引并告知可用 --wait 秒数轮询，
#           而不是直接判失败。
# 关键点：whoami 现在会对"自报成功"的情况写入 subagents 表，
#   使该表成为【双向可信】的记录 —— 谁先到谁登记，后到者幂等覆盖。
def _register_self_identity(room, session_id, source):
    """把子代理自报的 sessionId 写进 subagents 表（幂等）。"""
    if not room or not session_id:
        return None
    state = load_state()
    subs = state.setdefault("subagents", {})
    prev = subs.get(room)
    prev_id = None
    if isinstance(prev, dict):
        prev_id = prev.get("id")
    elif prev:
        prev_id = prev
    # 已有不同 id 时不覆盖（以主对话 subagent-assign 的权威值为准），
    # 但记录冲突供排查 —— 避免自报值把正确值冲掉。
    if prev_id and prev_id != session_id:
        return {"conflict": True, "existing": prev_id}
    subs[room] = {"id": session_id, "assigned_at": time.time(),
                  "self_reported": True, "source": source}
    save_state(state)
    return {"conflict": False, "registered": True}


def cmd_whoami(room=None, session_id=None, wait_sec=0, interval=2.0):
    """【问题3 修复】让子代理自查身份：我是哪个房间、我的 sessionId 是什么。

    背景：v1 小屋死在"不知道自己 sessionId → 提问通道用不了"；
    又因 subagent-assign 与 whoami 存在时序竞态（第一条命令就调 whoami 时，
    主对话还没写记录），必须支持【自报 sessionId】并回写，才能根治。

    用法（三种，任选其一）：
      python mode_gate.py whoami <房间名>
          ← 【Bug#5 修复·推荐】无需任何参数：自动从 DSH 注入的
             DSH_SESSION_JSONL / DSH_SESSION_ID 反解出你自己的 sessionId
             并就地登记，彻底消除"小屋不知道 sessionId"的竞态。
      python mode_gate.py whoami <房间名> --session-id <我的sessionId>
      python mode_gate.py whoami <房间名> --wait 30       # 等主对话登记（兜底）

    返回的 subagent_id 就是 ask_user.py 的 --child 参数。
    """
    # ── 第1层：自报 sessionId（命令行 > 专用环境变量）→ 立即回写 ──────────
    # 【重要】环境变量优先级说明：
    #   · DSH_SUBAGENT_SESSION_ID / DSH_SUBAGENT_ID / DSH_CHILD_SESSION_ID
    #     是【工程模式专用】变量，只应由小屋包装层显式设置 → 可安全自动采用。
    #   · DSH_SESSION_ID 是【DSH 宿主注入的通用会话 ID】：
    #       在子代理进程里它=该子代理自己的 session（正是我们要的）；
    #       但在主对话进程里它=主会话 ID。
    #     若不加区分地自动采用，主对话侧执行 whoami 时会把"主会话 ID"
    #     误登记成房间的 subagent_id，造成身份错乱（实测已复现）。
    #   因此：DSH_SESSION_ID 仅在【显式允许】时采用 ——
    #     需设置 DSH_WHOAMI_ALLOW_SESSION_ID=1（或由小屋包装层设置），
    #     否则只认专用变量。这样主对话误调用不会污染登记表。
    sid = (session_id or "").strip()
    src = "arg"
    if not sid:
        for env_key in ("DSH_SUBAGENT_SESSION_ID", "DSH_SUBAGENT_ID",
                        "DSH_CHILD_SESSION_ID"):
            v = (os.environ.get(env_key) or "").strip()
            if v:
                sid, src = v, "env:" + env_key
                break

    # ── 【Bug#5 修复】自动从 DSH 注入的会话文件路径反解 sessionId ────────
    # 问题现象（测试部 Bug#5）：
    #   小屋 prompt 让它调 whoami --session-id <你的sessionId>，但子代理的
    #   sessionId 由 DSH 在 fork 时分配，【根本不在它的 prompt 里可见】。
    #   它拿不到 id → 只能走 --wait 30 兜底 → 若主对话尚未 subagent-assign
    #   就超时 → 小屋误判失败（正是"时时序竞态"的真相）。
    #
    # 关键发现（本机实测 env）：DSH 会给【每个 shell 进程】注入
    #   DSH_SESSION_JSONL = <...>\sessions\<cwd-key>\<sessionId>\session.jsonl.zstd
    # 这个路径里【直接包含 sessionId】，且它是当前进程所属会话的事实证据 ——
    # 在子代理进程里就是该子代理自己的 session。因此可以可靠反解，
    # 无需子代理知道任何信息、也无需等待主对话登记。
    if not sid:
        _jsonl = (os.environ.get("DSH_SESSION_JSONL") or "").strip()
        if _jsonl:
            try:
                import re as _re2
                m = _re2.search(r"(session-[0-9a-fA-F-]{8,})", _jsonl)
                if m:
                    sid, src = m.group(1), "env:DSH_SESSION_JSONL"
            except Exception:
                pass

    # ── 【Bug#5 修复】DSH_SESSION_ID 改为【默认采用】并加安全护栏 ────────
    # 旧实现要求显式设置 DSH_WHOAMI_ALLOW_SESSION_ID=1 才采信，
    #   导致小屋在默认配置下永远拿不到 id（Bug#5 的直接原因）。
    # 现改为默认采用，但用【两道护栏】防止主对话误调用污染登记表：
    #   ① 若该 id 与新登记的 subagent 记录冲突，以权威值返回（已有逻辑）；
    #   ② 若调用方提供了 --room，且该 id 已经是【别的房间】的 subagent_id，
    #      则拒绝采用（一个会话不可能同时是两个房间的小屋）。
    # 保留 DSH_WHOAMI_DENY_SESSION_ID=1 作为显式关闭开关（兼容旧行为）。
    if not sid:
        _deny = (os.environ.get("DSH_WHOAMI_DENY_SESSION_ID") or "").strip() in (
            "1", "true", "yes", "on")
        if not _deny:
            v = (os.environ.get("DSH_SESSION_ID") or "").strip()
            if v:
                sid, src = v, "env:DSH_SESSION_ID"

    # ── 护栏②：该 id 是否已被【其它房间】占用？占用则拒绝自报 ─────────────
    if sid and room:
        try:
            _st_chk = load_state()
            for _rn, _rec in (_st_chk.get("subagents") or {}).items():
                _rid = _rec.get("id") if isinstance(_rec, dict) else _rec
                if _rid == sid and _rn != room:
                    return {"ok": False, "room": room,
                            "error": ("该 sessionId (%s) 已登记为房间 [%s] 的 subagent，"
                                      "不能同时作为 [%s] 的身份。" % (sid, _rn, room)),
                            "hint": ("每个房间必须使用自己的子代理会话。若你是刚创建的"
                                     "小屋，请确认 --room 填的是你自己的房间名；"
                                     "否则用 --session-id 显式传入你的 sessionId。"),
                            "occupied_by": _rn}
        except Exception:
            pass

    if room and sid:
        reg = _register_self_identity(room, sid, src)
        if reg and reg.get("conflict"):
            # 【问题3 加固】冲突时不直接判失败 —— 已有登记值通常是主对话
            # subagent-assign 写入的权威值，仍然可用。
            # 返回 ok=True + 权威 id + conflict 标记，让小屋能继续工作，
            # 同时把差异明确暴露出来供排查（避免"卡死"）。
            _authoritative = reg.get("existing")
            return {"ok": True, "room": room, "conflict": True,
                    "subagent_id": _authoritative,
                    "authoritative": True,
                    "self_reported": sid,
                    "note": ("身份冲突：该房间已登记为 %s（权威值，来自主对话），"
                             "与本次自报的 %s 不一致。已按权威值返回，可直接使用。"
                             % (_authoritative, sid)),
                    "ask_example": ("python \"<工程模式根目录>\\tools\\ask_user.py\" "
                                    "--room \"%s\" --child \"%s\" "
                                    "--question \"...\" --option \"确认\"")
                                   % (room, _authoritative)}

    state = load_state()
    subs = state.get("subagents", {}) or {}
    rooms = state.get("rooms", {}) or {}

    if room:
        rec = subs.get(room)
        # ── 第3层：等待重试（仅在没有任何身份来源时）─────────────────────
        if not rec and wait_sec and wait_sec > 0 and not sid:
            deadline = time.time() + float(wait_sec)
            while time.time() < deadline:
                time.sleep(interval)
                state = load_state()
                subs = state.get("subagents", {}) or {}
                rec = subs.get(room)
                if rec:
                    break
        if not rec:
            # 自报成功但读取失败（极端情况）
            if sid:
                return {"ok": True, "room": room, "subagent_id": sid,
                        "assigned_at": time.time(), "self_reported": True,
                        "note": "身份来自自报并已登记；可直接用作 ask_user.py 的 --child",
                        "ask_example": (
                            'python "<工程模式根目录>\\tools\\ask_user.py" '
                            '--room "%s" --child "%s" '
                            '--question "..." --option "确认"' % (room, sid))}
            _hint_lines = [
                "该房间尚未登记 subagent，且未能从环境自动识别你的身份。",
                "【Bug#5 修复】请改用以下任一方式：",
                "  ① 直接重试(推荐)：python mode_gate.py whoami \"%s\"" % room,
                "     本命令会自动从 DSH_SESSION_JSONL / DSH_SESSION_ID 反解你的 sessionId；",
                "     若仍失败，说明宿主未注入这两个变量，走方式②。",
                "  ② 显式自报：python mode_gate.py whoami \"%s\" --session-id <你的sessionId>" % room,
                "  ③ 等待登记：python mode_gate.py whoami \"%s\" --wait 30" % room,
                "",
                "诊断信息（请连同本段一起反馈）:",
                "  DSH_SESSION_ID     = %s" % (os.environ.get("DSH_SESSION_ID") or "(未设置)"),
                "  DSH_SESSION_JSONL  = %s" % (os.environ.get("DSH_SESSION_JSONL") or "(未设置)"),
            ]
            return {"ok": False, "room": room,
                    "error": chr(10).join(_hint_lines),
                    "hint": ("优先直接重试 whoami（会自动反解身份）；"
                             "仍不行再用 --session-id 显式传入。"
                             "不要在拿不到 sessionId 的情况下盲目往下做。"),
                    "known_rooms": list(subs.keys())}
        d = rec if isinstance(rec, dict) else {"id": rec}
        got = d.get("id")
        # ── 【Bug2/Bug4 加固】环境变量校正 ─────────────────────────────────
        # 若本次带上了自报身份（--session-id 或专用环境变量），而登记表里的值
        # 与之不同，说明表里可能是【上一波残留】或【主对话误写】。
        # 此时以"自报值"为准（它来自正在运行的这个子代理进程，最可信），
        # 并标记 corrected_from 便于审计 —— 避免子代理拿着陈旧 id 去
        # ask_user.py --child 投递给死会话。
        corrected_from = None
        if sid and got and sid != got:
            _prev = got
            reg2 = _register_self_identity.__wrapped__ if hasattr(_register_self_identity, "__wrapped__") else None
            # 直接覆盖（此时已有自报来源，权威性高于残留值）
            state2 = load_state()
            subs2 = state2.setdefault("subagents", {})
            subs2[room] = {"id": sid, "assigned_at": time.time(),
                           "self_reported": True, "source": src,
                           "corrected_from": _prev}
            save_state(state2)
            corrected_from = _prev
            got = sid
            d = subs2[room]
        return {"ok": True, "room": room, "subagent_id": got,
                "assigned_at": d.get("assigned_at"),
                "self_reported": bool(d.get("self_reported")),
                "corrected_from": corrected_from,
                "note": ("把 subagent_id 作为 ask_user.py 的 --child 参数"
                         + ("（已用自报身份校正，原值 %s 疑为上一波残留）" % corrected_from
                            if corrected_from else "")),
                "ask_example": ("python \"<工程模式根目录>\\tools\\ask_user.py\" "
                                "--room \"%s\" --child \"%s\" "
                                "--question \"...\" --option \"确认\"") % (room, got)}

    out = []
    for rn, rec in subs.items():
        d = rec if isinstance(rec, dict) else {"id": rec}
        info = rooms.get(rn, {}) or {}
        out.append({"room": rn, "subagent_id": d.get("id"),
                    "self_reported": bool(d.get("self_reported")),
                    "active": bool(info.get("active")),
                    "ended": bool(info.get("ended_at")),
                    "failed": bool(info.get("failed_at"))})
    return {"ok": True, "assignments": out,
            "hint": ("【Bug#5 修复】直接调 whoami <你的房间名> 即可 —— "
                     "会自动从 DSH_SESSION_JSONL/DSH_SESSION_ID 反解你的身份，"
                     "无需传 --session-id；不带房间名时列出全部已知房间身份")}


def _fresh_state():
    return {"mode": "1", "rooms": {}, "declared_at": None, "mode_name": MODE_NAMES["1"],
            "sw_lock": {"owner": None, "queue": [], "acquired_at": None,
                        "prev_owner": None, "sw_by_room": False}}


def load_state():
    if not os.path.exists(STATE_PATH):
        return _fresh_state()
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            state = json.load(f)
        # ── 【健壮性修复】空文件/非 dict 时回落到全新状态 ──────────────
        # 实测 mode_state.json 可能被写成 {}（rooms-reset 或外部脚本误写），
        #   此时 state 是合法 dict 但没有 rooms/subagents/sw_lock 键，
        #   residue-check / room-status 等命令会因缺键而异常。
        if not isinstance(state, dict):
            return _fresh_state()
        state.setdefault("rooms", {})
        state.setdefault("subagents", {})
        state.setdefault("sw_lock", {"owner": None, "queue": [], "acquired_at": None,
                                     "prev_owner": None, "sw_by_room": False})
        return state
    except Exception:
        return _fresh_state()


def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _norm_pmode(raw):
    if raw is None:
        return None
    r = str(raw).strip().upper()
    if r in ("D", "PARALLEL"):
        return "parallel"
    if r in ("E", "SEQUENTIAL"):
        return "sequential"
    return raw


# ── 【Bug1 修复】parallel_mode 双写持久化 ─────────────────────────────────
# 原缺陷：select 返回 parallel 后只写 workflow_state.json，从未写回
#   mode_state.json → 磁盘上 mode_state 的 parallel_mode 恒为 None，
#   sw-status 只能靠实时推断（读 workflow_state）才显示 parallel。
#   两个文件口径不一致 = 进程间判定不稳的温床（锁判定直接受影响）。
# 修复：mode_state.json 增加 parallel_mode 字段作为【权威落盘值】，
#   读取顺序：mode_state（权威）→ workflow_state（兼容回退）。
def _workflow_parallel_mode():
    """【BUG-04 修复】读取 workflow_state.json 里本轮的 parallel_mode。

    原缺陷（并行模式权威来源两命令打架）：
      · select 把用户选择写入 workflow_state.json；
      · 但 set-parallel-mode 只写 mode_state.json；
      · 两个文件各说各话时（一个 parallel 一个 sequential），
        sw_bridge 读 mode_state、room-start 读 get_parallel_mode，
        不同命令拿到相反结论 → "C+D 被误判为串行"或反之。
    修复：抽出统一的 workflow 读取口，供 get_parallel_mode 与自愈逻辑复用，
      并把"单一权威"明确为：**本轮进行中(step=user_selected)以 workflow 为准**，
      其余情况以 mode_state 为准；任何一次读取都把两者【对齐写回】。
    """
    try:
        if not os.path.exists(WORKFLOW_STATE_PATH):
            return None, ""
        with open(WORKFLOW_STATE_PATH, "r", encoding="utf-8") as f:
            _wf = json.load(f) or {}
        return _norm_pmode(_wf.get("parallel_mode")), str(_wf.get("step") or "")
    except Exception:
        return None, ""


def _reconcile_parallel_mode():
    """【BUG-04 修复】把两个文件的 parallel_mode 对齐，消除权威打架。

    返回 (authoritative_value, source) —— source ∈ {"workflow", "mode_state", None}
    规则（与 get_parallel_mode 的注释一致）：
      · 本轮进行中（workflow.step == user_selected）且 workflow 有值
          → 以 workflow 为准（它记录的是用户本次的真实选择）
      · 否则以 mode_state 的落盘值为准
      · 无论以谁为准，都把另一个文件【改写对齐】，让后续任何读取都一致
    """
    wf_pm, wf_step = _workflow_parallel_mode()
    st = load_state()
    ms_pm = _norm_pmode(st.get("parallel_mode"))

    if wf_step == "user_selected" and wf_pm in ("parallel", "sequential"):
        authoritative, source = wf_pm, "workflow"
    elif ms_pm in ("parallel", "sequential"):
        authoritative, source = ms_pm, "mode_state"
    elif wf_pm in ("parallel", "sequential"):
        authoritative, source = wf_pm, "workflow"
    else:
        return None, None

    # ── 对齐写回：两个文件必须一致 ──────────────────────────────────────
    if ms_pm != authoritative:
        try:
            st["parallel_mode"] = authoritative
            st["parallel_mode_updated_at"] = time.time()
            st["parallel_mode_reconciled_by"] = "reconcile(from %s)" % source
            save_state(st)
        except Exception:
            pass
    if wf_pm != authoritative:
        try:
            if os.path.exists(WORKFLOW_STATE_PATH):
                with open(WORKFLOW_STATE_PATH, "r", encoding="utf-8") as f:
                    _wf = json.load(f) or {}
                _wf["parallel_mode"] = authoritative
                _wf["parallel_mode_reconciled_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                with open(WORKFLOW_STATE_PATH, "w", encoding="utf-8") as f:
                    json.dump(_wf, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
    return authoritative, source


def get_parallel_mode():
    """读取并行模式。优先 mode_state.json 的落盘值（权威），回退 workflow_state.json。

    ── 【Bug#1 修复 · C+D 兼容】workflow_state 的优先级提升 ───────────────
    问题现象（测试部实测，阻塞性）：
      选择 C（步步确认）+ D（部分小屋并行）后，第 1 波应同时启动 3 个房间，
      实际 mode_gate 拒绝第 2、3 个 room-start，报
        "【串行模式锁定】房间 [传动机构] 登记被拒绝"。
      用户明确要求：C 和 D 必须能一起用。

    根因：
      本函数原以 mode_state.json 为【唯一权威】。但 mode_state 的
      parallel_mode 是【粘性】的 —— 上一轮若选过 E，它就一直留着
      'sequential'；新一轮 select C+D 虽然会调 set-parallel-mode 覆写，
      但存在两类漏网：
        ① select 与 room-start 由不同进程/不同时刻发起，
           room-start 可能先于 set-parallel-mode 落盘就读到了旧值；
        ② 任何一次 mode_state 恢复/手改/残留，都会让 C+D 被误判为串行，
           且【不会自愈】——表现为"并行策略完全失效"。
      而 workflow_state.json 是【本轮任务】的真实记录（user_choice +
      parallel_mode 由 select 当场写入），比粘性字段更能代表"用户这次选了什么"。

    修复策略（用户意图优先）：
      · 若 workflow_state 处于【本轮进行中】(step=user_selected) 且记有
        parallel_mode，则以它为准 —— 它才是用户本次的选择。
      · 否则回退 mode_state 的落盘值（保留原有语义，不影响其它命令）。

    ── 【BUG-04 修复】改为委托 _reconcile_parallel_mode() ────────────────
      原实现只在"①分支命中"时才顺手纠正 mode_state，②分支完全不回写
      workflow —— 于是两文件仍可能长期不一致，不同命令各读各的，
      表现为"并行模式权威来源两命令打架"。
      现在统一走 reconcile：既选出权威值，也把另一侧改写对齐。
    """
    val, _src = _reconcile_parallel_mode()
    return val


def _get_parallel_mode_legacy():
    """旧实现（保留仅作对照/回退，正常路径不再调用）。"""
    # ① 本轮任务进行中 → 以 workflow_state 记录的用户选择为准
    try:
        _step = ""
        _wf_pm = None
        if os.path.exists(WORKFLOW_STATE_PATH):
            with open(WORKFLOW_STATE_PATH, "r", encoding="utf-8") as f:
                _wf = json.load(f) or {}
            _step = str(_wf.get("step") or "")
            _wf_pm = _norm_pmode(_wf.get("parallel_mode"))
        if _step == "user_selected" and _wf_pm in ("parallel", "sequential"):
            # 顺带纠正 mode_state 的粘性旧值，避免下一条命令又读到错的
            try:
                _st = load_state()
                if _norm_pmode(_st.get("parallel_mode")) != _wf_pm:
                    _st["parallel_mode"] = _wf_pm
                    _st["parallel_mode_updated_at"] = time.time()
                    _st["parallel_mode_repaired_by"] = "get_parallel_mode(wf)"
                    save_state(_st)
            except Exception:
                pass
            return _wf_pm
    except Exception:
        pass
    # ② 回退：mode_state 落盘值
    state = load_state()
    v = _norm_pmode(state.get("parallel_mode"))
    if v:
        return v
    if not os.path.exists(WORKFLOW_STATE_PATH):
        return None
    try:
        with open(WORKFLOW_STATE_PATH, "r", encoding="utf-8") as f:
            ws = json.load(f)
        return _norm_pmode(ws.get("parallel_mode"))
    except Exception:
        return None


def set_parallel_mode(raw):
    """【Bug1 修复】把并行模式持久化进 mode_state.json。

    由 workflow_gate.py select 阶段调用（经 _sync_mode_gate），
    也可由命令行 'set-parallel-mode <D|E|parallel|sequential>' 手动校正。
    Returns: 归一化后的值（'parallel' / 'sequential' / None）

    ── 【BUG-04 修复】双写：同时校正 workflow_state.json ──────────────────
    原实现只写 mode_state，而 get_parallel_mode 在"本轮进行中"时以
    workflow 为准 —— 若 select 已把旧值写进 workflow，本函数写 mode_state
    反而造成两文件相反，下一次读取又把 mode_state 覆盖回去（来回打架）。
    现在两侧同写同改，令"并行模式权威来源"始终唯一且自洽。
    """
    val = _norm_pmode(raw)
    if val not in ("parallel", "sequential"):
        return None
    state = load_state()
    if state.get("parallel_mode") != val:
        state["parallel_mode"] = val
        state["parallel_mode_updated_at"] = time.time()
        save_state(state)
    # ── 同步写回 workflow_state（若存在）────────────────────────────────
    try:
        if os.path.exists(WORKFLOW_STATE_PATH):
            with open(WORKFLOW_STATE_PATH, "r", encoding="utf-8") as f:
                _wf = json.load(f) or {}
            if _norm_pmode(_wf.get("parallel_mode")) != val:
                _wf["parallel_mode"] = val
                _wf["parallel_mode_synced_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                _wf["parallel_mode_synced_by"] = "set_parallel_mode"
                with open(WORKFLOW_STATE_PATH, "w", encoding="utf-8") as f:
                    json.dump(_wf, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return val


def _lock_owner_alive(state, owner):
    """判断锁持有者的房间是否仍然存活。

    存活判据（任一满足即为存活）：
      1. 该房间在 rooms 中 active=True
      2. 该房间心跳文件 age <= SW_HEARTBEAT_TIMEOUT
      3. 【Bug3 修复】该房间有近期进度报告（报告刷新=小屋在干活）
      4. 【Bug3 修复】该房间有待确认文件且新鲜（C模式正在走确认流程）
      5. 【C1 修复】磁盘有近期产出（最强证据）

    【Bug3 修复说明】原实现只认"active + 心跳新鲜"。而小屋建模时【经常
    没空跳心跳】，超过 180s 就被当孤儿 → 锁被回收给别的房间 → 两个房间
    同时用 SW 抢 API，正是"锁判定不稳"的来源之一。
    现在把"报告/待确认文件"也作为存活证据：小屋每上报一次都会写报告，
    比心跳更贴近"它到底还在不在干活"。

    ── 【Bug-07 修复】外部强杀 SW 时锁必须立即失效 ────────────────────
    实测缺陷：用户手动关闭全部 SolidWorks 后，mode_state.sw_lock 的 owner
      反复被清空又重建（purged_by 依次为各房间），锁状态与平台侧
      list_agents 不一致（room 报 running / 平台 inactive）。
    根因：本函数的 5 条判据全部只看【房间侧证据】（active/心跳/报告/
      产出文件），完全没看【SW 进程事实】。于是"SW 已被外部强杀、但房间
      记录还活着"时，锁仍被认为有效占用 —— 队列里其它房间拿不到锁，
      直到某个超时兜底才被 purge，表现为锁状态反复抖动。
    修复：本房间若曾启动 SW（sw_by_room=True）或正持有锁，而
      【SLDWORKS.EXE 已不存在】，则该锁已无实际资源可让渡 —— 立即判为
      不存活（可回收），让队首房间马上拿到使用权。
      注意：只对"本房间启动的 SW"生效；用户手工开的 SW（sw_by_room=False）
      仍然允许附着使用，不会被误回收。
    """
    if not owner:
        return False
    lk = state.get("sw_lock") or {}
    # ── 【Bug-07】SW 进程事实优先：进程没了，锁就是空的 ──────────────────
    try:
        _is_owner = (lk.get("owner") == owner)
        _sw_was_ours = bool(lk.get("sw_by_room"))
        if (_is_owner or _sw_was_ours) and not _sw_running():
            return False
    except Exception:
        pass
    # 判据1: 房间仍然 active
    rooms = state.get("rooms", {})
    room = rooms.get(owner)
    if room and room.get("active"):
        return True
    # 判据2: 心跳新鲜
    try:
        hb = _heartbeat_path(owner)
        if os.path.exists(hb):
            age = time.time() - os.path.getmtime(hb)
            if age <= SW_HEARTBEAT_TIMEOUT:
                return True
    except Exception:
        pass
    # ── 判据3: 近期进度报告（Bug3 新增）──
    try:
        rp = _report_path(owner)
        if os.path.exists(rp):
            if (time.time() - os.path.getmtime(rp)) <= SW_EVIDENCE_WINDOW:
                return True
    except Exception:
        pass
    # ── 判据4: 待确认文件新鲜（Bug3 新增）──
    try:
        pd = _pending_confirm_path(owner)
        if os.path.exists(pd):
            if (time.time() - os.path.getmtime(pd)) <= SW_EVIDENCE_WINDOW:
                return True
    except Exception:
        pass
    # ── 判据5: 磁盘有近期产出（C1 新增 · 最强证据）──────────────────────
    # 房间专心建模时既没心跳也没上报，但模型文件在落盘 —— 这就是活着。
    # 若不纳入此判据，锁会被当"孤儿锁"回收给别的房间，两个房间同时抢 SW API，
    # 这正是测试反馈中"锁判定不稳"的根源之一。
    try:
        room_rec = rooms.get(owner) or {}
        _art_ok = _recent_artifact_evidence(owner, since=room_rec.get("started_at"))
        if _art_ok.get("found"):
            return True
    except Exception:
        pass
    return False


def _purge_orphan_lock(state, requester=None, verbose=False):
    """【Bug6 修复】孤儿锁快速体检 —— 清掉"owner:null 但 prev_owner 残留"的阻塞。

    问题现象：
      sw_lock 里 owner 已经是 null（持有者释放/死亡），但 prev_owner 还留着
      一个【已死房间】的名字，且 sw_by_room=True。于是队列里所有房间调用
      sw-request 都看到"SW 被上一家占着"，一律返回 sw_busy:true，
      队列永久阻塞 —— 也就是"锁没了却谁也进不来"。

    原实现的缺口：
      cmd_sw_request 里只在 `owner 为空 且 sw_by_room` 时清一次，
      且完全依赖 _lock_owner_alive() 判定。但存在两类漏网：
        ① prev_owner 指向的房间根本没在 rooms 表里（如 rooms-reset 后残留）
        ② prev_owner 对应的 SW 进程其实早已退出，却没人去核对
      这两类都会让清理条件不成立 → 标志一直挂着。

    本函数在每次取锁前做一次彻底体检，满足【任一】即清理：
      A. owner 为空 且 prev_owner 为空          → sw_by_room 无意义，清掉
      B. owner 为空 且 prev_owner 已死/未登记    → 清 prev_owner + sw_by_room
      C. owner 为空 且 SW 进程根本没在跑         → 清 prev_owner + sw_by_room
         （进程都不在了，"上一家还占着"的前提不成立）

    注意：本函数会【自行 save_state 落盘】（调用方无需再保存），
    因为它在多处被调用（sw-request / lock-doctor），统一落盘可避免
    "改了内存却没写盘 → 下一个进程读到的还是旧锁"这类隐蔽 bug。

    Returns: dict {purged: bool, actions: [..]}
    """
    lk = state.get("sw_lock")
    if not isinstance(lk, dict):
        return {"purged": False, "actions": []}
    actions = []
    owner = lk.get("owner")
    prev = lk.get("prev_owner")

    # owner 还在 → 不是孤儿场景，交给常规公平/回收逻辑处理
    if owner:
        # 但 owner 自己已死也要回收（保持与既有逻辑一致，这里只做标记）
        if not _lock_owner_alive(state, owner):
            actions.append("owner=%r 已死" % owner)
        else:
            return {"purged": False, "actions": []}

    if owner:
        # owner 死了：清 owner，转为 prev 清理流程
        lk["owner"] = None
        lk["acquired_at"] = None
        lk["cmd_seq"] = 0
        lk["fair_start"] = None

    # ── 场景 A/B：prev_owner 为空或已死 ──────────────────────────────
    if not prev:
        if lk.get("sw_by_room"):
            lk["sw_by_room"] = False
            actions.append("sw_by_room 无意义(owner/prev 均空)→清")
    elif not _lock_owner_alive(state, prev):
        lk["prev_owner"] = None
        if lk.get("sw_by_room"):
            lk["sw_by_room"] = False
        actions.append("prev_owner=%r 已死/未登记→清" % prev)
    else:
        # ── 场景 C：prev_owner 名义上"活着"，但 SW 进程其实没在跑 ────
        #  心跳文件可能因 recover/restart 残留而仍新鲜，这里用进程事实兜底。
        try:
            if not _sw_running():
                lk["prev_owner"] = None
                if lk.get("sw_by_room"):
                    lk["sw_by_room"] = False
                actions.append("SW 进程未运行(prev_owner=%r)→清" % prev)
                prev = None   # 已清，跳过下方场景 D
        except Exception:
            pass

    # ── 【新-6 修复】场景 D：owner 为空 → 锁定视为【可用】，强制推进队列 ──
    # 测试反馈（幽灵占用 / flapping orphan lock）：
    #   sw_lock = {owner: null, queue: [3个房间], prev_owner: "壳体机架",
    #              sw_by_room: true}
    #   SW 进程【空闲】却对所有房间报 sw_busy: true，谁都拿不到使用权，
    #   时而自愈时而复发，小屋重试 ~10 次 / 等 70~90s 才拿到窗口。
    #
    # 根因：owner 被清空后，prev_owner / sw_by_room 残留 —— 只要
    #   prev_owner 指向的房间"名义上还活着"（心跳新鲜或仍在 rooms 表里），
    #   上面三个场景都不成立，标志就一直挂着，队列永不推进。
    #
    # 修复原则（简单且安全）：**没有 owner 就是没有持锁者**。
    #   prev_owner 只是"上一家是谁"的审计信息，不应用于阻止他人取锁；
    #   sw_by_room 表示"SW 是被某个房间启动的"，也不应阻止取锁。
    #   因此 owner 为空时，一律清掉这两个标志，让队首立即获得使用权。
    if not lk.get("owner") and lk.get("prev_owner"):
        lk["prev_owner"] = None
        actions.append("owner 为空→prev_owner 标志清除(防幽灵占用)")
    if not lk.get("owner") and lk.get("sw_by_room"):
        lk["sw_by_room"] = False
        actions.append("owner 为空→sw_by_room 标志清除(防幽灵占用)")

    if actions:
        lk["purged_at"] = time.time()
        lk["purged_by"] = requester or "auto"
        lk["purged_actions"] = actions[-5:]
        # 【关键】立即落盘：调用方（sw-request / lock-doctor）可能不会再次保存，
        # 不落盘会导致"内存改了、磁盘没改"，下一个进程读到的仍是孤儿锁。
        try:
            save_state(state)
        except Exception:
            pass
    return {"purged": bool(actions), "actions": actions}


def _lock_owner_lock_age(state):
    """返回当前锁已持有的秒数（无 acquired_at 时返回 0）。"""
    lk = state.get("sw_lock") or {}
    at = lk.get("acquired_at")
    if not at:
        return 0.0
    try:
        return max(0.0, time.time() - float(at))
    except Exception:
        return 0.0


def _reclaim_lock(state, reason, requester=None):
    """【Bug1/3修复】回收失效锁：owner 死亡 / 超时 / 孤儿。

    返回 (reclaimed: bool, old_owner: str|None)
    """
    lk = _sw_lock(state)
    owner = lk.get("owner")
    if not owner:
        # 【Bug5修复】owner 为空但 sw_by_room=True 且进程在跑 → 释放标志，
        # 让队首能立即接管，而不是被 prev_owner 逻辑永久挡住。
        if lk.get("sw_by_room") and not lk.get("prev_owner"):
            lk["sw_by_room"] = False
            return True, None
        return False, None

    held = _lock_owner_lock_age(state)
    alive = _lock_owner_alive(state, owner)

    should_reclaim = False
    why = ""
    # 【Bug2 修复】公平轮转：有人排队时，持有者不能无限续期。
    # 只有当 requester 不是 owner 本人（即确有其他房间在争用）时才触发。
    queue = [q for q in (lk.get("queue") or []) if q != owner]
    has_waiter = bool(queue) and requester != owner
    cmds = int(lk.get("cmd_seq", 0) or 0)
    # 自"本轮首次取锁"起算的连续独占时长（首次取锁时由 sw-request 记 fair_start）
    fair_start = lk.get("fair_start") or lk.get("acquired_at")
    try:
        fair_held = time.time() - float(fair_start) if fair_start else 0.0
    except Exception:
        fair_held = 0.0

    if not alive:
        should_reclaim = True
        why = "持有者[%s]已无活动心跳/已非活动房间" % owner
    elif has_waiter and cmds >= SW_FAIR_CMDS:
        # 排队者已等候多时，且持有者已连跑满额命令 → 让位（防饥饿核心）
        should_reclaim = True
        why = ("公平轮转：持有者[%s]已连续执行 %d 条命令(上限%d)，"
               "队首[%s]等待中，强制让位" % (owner, cmds, SW_FAIR_CMDS, queue[0]))
    elif has_waiter and fair_held > SW_FAIR_SECONDS:
        should_reclaim = True
        why = ("公平轮转：持有者[%s]连续独占 %.0f 秒(上限%d)，"
               "队首[%s]等待中，强制让位" % (owner, fair_held, SW_FAIR_SECONDS, queue[0]))
    elif held > SW_CMD_HOLD and requester != owner:
        # 单命令级锁：超过 SW_CMD_HOLD 仍未释放，视为该命令已结束
        should_reclaim = True
        why = "持有者[%s]持锁%.0f秒超过单命令上限%d秒" % (owner, held, SW_CMD_HOLD)

    if should_reclaim:
        lk["prev_owner"] = owner
        lk["owner"] = None
        lk["acquired_at"] = None
        # 只有在进程确实没跑时才清 sw_by_room，避免误判正在运行的SW
        if not _sw_running():
            lk["sw_by_room"] = False
        if requester and requester in lk.get("queue", []):
            pass
        return True, owner
    return False, None


def _sw_lock(state):
    return state.setdefault("sw_lock", {"owner": None, "queue": [], "acquired_at": None,
                                        "prev_owner": None, "sw_by_room": False})


def _lock_needed(state):
    """模式2 一律启用 SW 使用窗口（串行天然无竞争，并行自动排队）。"""
    return str(state.get("mode")) == "2"


def _sw_proc_list():
    """获取 SLDWORKS.EXE 进程列表（数量、PID列表）。"""
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq " + SW_EXE, "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, timeout=10,
                             creationflags=CREATE_NO_WINDOW)
        pids = [x.strip(chr(34)).split(",")[1].strip()
                for x in (out.stdout or "").splitlines()
                if SW_EXE.upper() in x.upper()]
        return len(pids), pids
    except Exception:
        return 0, []


def cmd_sw_monitor_start():
    """标记监控已启动（用于 status 显示）。"""
    return {"ok": True, "note": "请在模式2激活时调用 sw-monitor-bg 启动真正监控"}


def cmd_sw_monitor_stop():
    """停止监控（清理 PID 文件）。"""
    try:
        os.remove(_SW_MONITOR_PID_FILE)
    except Exception:
        pass
    return {"ok": True}


def _monitor_alive():
    """检测监控进程是否真的存活（并清理失效 PID 文件）。"""
    if not os.path.exists(_SW_MONITOR_PID_FILE):
        return False
    try:
        with open(_SW_MONITOR_PID_FILE, "r") as f:
            mpid = int(f.read().strip())
    except Exception:
        return False
    if mpid <= 0:
        return False
    # Windows 用 tasklist 判断，跨平台用 os.kill(pid, 0)
    try:
        if sys.platform == "win32":
            out = subprocess.run(["tasklist", "/FI", "PID eq %d" % mpid, "/FO", "CSV", "/NH"],
                                 capture_output=True, text=True, timeout=10,
                                 creationflags=CREATE_NO_WINDOW)
            if str(mpid) not in (out.stdout or ""):
                try:
                    os.remove(_SW_MONITOR_PID_FILE)
                except Exception:
                    pass
                return False
            return True
        os.kill(mpid, 0)
        return True
    except Exception:
        try:
            os.remove(_SW_MONITOR_PID_FILE)
        except Exception:
            pass
        return False


def _write_sw_state_now():
    """【Bug2修复】立即回写一次真实进程状态（不依赖监控进程）。"""
    try:
        count, pids = _sw_proc_list()
        st = {"sw_running": count > 0, "sw_count": count, "sw_pids": pids,
              "updated_at": time.time()}
        with open(_SW_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(st, f)
        return st
    except Exception:
        return None


def cmd_sw_monitor_status():
    """查看监控状态 + SW 进程信息（含自动自愈）。"""
    alive = _monitor_alive()
    count, pids = _sw_proc_list()
    state = {}
    try:
        with open(_SW_STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
    except Exception:
        pass
    # 【Bug2修复】监控失联或状态文件过期 → 立即用真实进程回写
    file_age = None
    if state:
        try:
            file_age = time.time() - float(state.get("updated_at", 0))
        except Exception:
            file_age = None
    stale = (file_age is None or file_age > SW_MONITOR_INTERVAL * 4
             or bool(state.get("sw_running")) != (count > 0))
    if stale:
        fresh = _write_sw_state_now()
        if fresh:
            state = fresh
    return {
        "ok": True, "monitor_running": alive,
        "sw_running": count > 0, "sw_count": count, "sw_pids": pids,
        "last_updated": state.get("updated_at"),
        "state_file": state if state else {},
        "state_was_stale": stale,
        "state_file_age_seconds": round(file_age, 1) if file_age is not None else None,
    }


def _sw_monitor_loop():
    """后台监控主循环。"""
    import atexit
    def cleanup():
        try:
            os.remove(_SW_MONITOR_PID_FILE)
        except Exception:
            pass
    atexit.register(cleanup)
    while True:
        try:
            count, pids = _sw_proc_list()
            st = {"sw_running": count > 0, "sw_count": count, "sw_pids": pids, "updated_at": time.time()}
            with open(_SW_STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(st, f)
        except Exception:
            pass
        time.sleep(SW_MONITOR_INTERVAL)


def cmd_sw_monitor_bg():
    """在子进程中启动 SW 监控。"""
    import subprocess as sp
    # 检查是否已在运行
    if os.path.exists(_SW_MONITOR_PID_FILE):
        try:
            with open(_SW_MONITOR_PID_FILE, "r") as f:
                old_pid = int(f.read().strip())
            import signal
            os.kill(old_pid, 0)
            return {"ok": True, "already_running": True, "pid": old_pid}
        except Exception:
            pass
    script = os.path.abspath(__file__)
    proc = sp.Popen([sys.executable, script, "_monitor_bg_impl"],
                    creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    try:
        with open(_SW_MONITOR_PID_FILE, "w") as f:
            f.write(str(proc.pid))
    except Exception:
        pass
    return {"ok": True, "started_pid": proc.pid,
            "message": "SW 后台监控已启动（PID=%d），每 %d 秒刷新状态" % (proc.pid, SW_MONITOR_INTERVAL)}


def _monitor_bg_impl():
    """后台监控实现入口。"""
    _sw_monitor_loop()


def cmd_declare(mode):
    state = load_state()
    mode = str(mode)
    if mode not in MODE_NAMES:
        return {"ok": False, "error": "模式必须为 1/2/3,收到 " + str(mode)}
    state["mode"] = mode
    state["mode_name"] = MODE_NAMES[mode]
    state["declared_at"] = time.time()
    # ── 【Bug4 修复】declare 2 = 新任务开始 → 记录任务纪元 ──
    # 纪元用于把"上一轮残留的 DSH_* 旧件"与本轮新件区分开，
    # 防止总装时误用旧件（真实发生：11:xx 的旧大臂混进 16:xx 的新设计）。
    # 注意：仅当 mode==2 且尚未有纪元（或显式 --new-epoch）时刷新，
    #   避免重复 declare 把纪元不断推后、导致本轮已产出的零件被误标为遗留。
    _want_new_epoch = (mode == "2") and (
        "--new-epoch" in sys.argv or not state.get("task_epoch_started_at"))
    if _want_new_epoch:
        state["task_epoch_started_at"] = time.time()
        state["task_epoch_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    # ── 【Bug1 修复】declare 时把 workflow_state 的并行模式落到 mode_state ──
    # 保证 mode_state.json 里 parallel_mode 字段始终有值（不再恒为 None）。
    if mode == "2" and not _norm_pmode(state.get("parallel_mode")):
        _pm = get_parallel_mode()          # 回退读 workflow_state
        if _pm in ("parallel", "sequential"):
            state["parallel_mode"] = _pm
            state["parallel_mode_updated_at"] = time.time()
    save_state(state)
    return {"ok": True, "mode": mode, "mode_name": MODE_NAMES[mode],
            "requires_subagent": mode == "2",
            "parallel_mode": _norm_pmode(state.get("parallel_mode"))}


def cmd_room_start(name):
    state = load_state()
    if str(state.get("mode")) != "2":
        err = "当前不是大型复杂器械装配模式,无需登记小屋。"
        err += "若确认为装配任务请先: mode_gate.py declare 2"
        return {"ok": False, "error": err}
    pmode = get_parallel_mode()
    # ── 【Bug#1 修复 · 关键】把 self-heal 的结果同步进本地 state 副本 ────────
    #   场景：mode_state 是粘性 'sequential'（上一轮选过 E），而本轮用户选了 D。
    #   get_parallel_mode() 内部会读 workflow_state 判定为 'parallel' 并
    #   顺手把磁盘纠正过来。但本函数的局部变量 state 是【更早】load 的副本，
    #   里面仍是 'sequential' —— 函数结尾 save_state(state) 会把它整体写回，
    #   【把刚纠正好的磁盘值又冲回 sequential】，导致自愈白做、下一命令又误判。
    #   修复：只要 get_parallel_mode() 的结论与本地副本不一致，就以它为准，
    #   立即刷新本地副本（内存与磁盘口径始终一致）。
    try:
        if _norm_pmode(state.get("parallel_mode")) != pmode \
                and pmode in ("parallel", "sequential"):
            state["parallel_mode"] = pmode
            state["parallel_mode_synced_at"] = time.time()
    except Exception:
        pass
    rooms = state.setdefault("rooms", {})
    active_rooms = [r for r, v in rooms.items() if v.get("active")]
    # ── 【Bug#1 修复 · C+D 兼容】串行锁只应在【真串行】时生效 ──────────────
    #   用户明确要求：C（步步确认）+ D（部分小屋并行）必须能一起用，
    #   第 1 波三个房间要能【同时登记】。
    #
    #   这里额外加一道【用户意图优先】的保护：即使 mode_state 因残留/竞态
    #   被判成 sequential，只要 workflow_state 明确记录本轮用户选了 D，
    #   就按并行放行，并顺手把 mode_state 纠正回来（自愈）。
    #   这样"并行策略完全失效"不可能再发生。
    if pmode == "sequential":
        try:
            if os.path.exists(WORKFLOW_STATE_PATH):
                with open(WORKFLOW_STATE_PATH, "r", encoding="utf-8") as _wf_f:
                    _wf2 = json.load(_wf_f) or {}
                _wf_pm2 = _norm_pmode(_wf2.get("parallel_mode"))
                if str(_wf2.get("step") or "") == "user_selected" \
                        and _wf_pm2 == "parallel":
                    # 用户本轮选的是 D → 纠正 mode_state 的粘性旧值并放行
                    pmode = "parallel"
                    # ── 【关键】必须同时更新【内存副本】state ──────────────
                    #   本函数结尾会 save_state(state) 把内存 state 整体写回磁盘。
                    #   若只调 set_parallel_mode() 落盘、却不改内存里的 state，
                    #   结尾那次写回会把磁盘上刚纠正好的 'parallel'
                    #   【重新覆盖成旧的 'sequential'】—— 自愈白做，下一轮又误判。
                    state["parallel_mode"] = "parallel"
                    state["parallel_mode_updated_at"] = time.time()
                    state["parallel_mode_repaired_by"] = "room-start(C+D自愈)"
                    try:
                        set_parallel_mode("parallel")
                    except Exception:
                        pass
        except Exception:
            pass
    if pmode == "sequential" and active_rooms:
        active = active_rooms[0]
        err = "【串行模式锁定】房间 [%s] 登记被拒绝。" % name
        err += NL + "当前已有活动房间: %s" % active
        err += NL + "必须先完成当前房间（调用 room-end）后，才能开启新房间。"
        err += NL + "调用: python mode_gate.py room-end %s" % active
        # ── 诊断信息：让调用方能一眼看出"为什么被判成串行" ──────────────
        err += NL + NL + "── 诊断（若你选的是 D 并行，请核对下面两项）──"
        err += NL + "  mode_state.parallel_mode   = %r" % (
            load_state().get("parallel_mode"),)
        try:
            _wf_diag = {}
            if os.path.exists(WORKFLOW_STATE_PATH):
                with open(WORKFLOW_STATE_PATH, "r", encoding="utf-8") as _df:
                    _wf_diag = json.load(_df) or {}
            err += NL + "  workflow.step              = %r" % (_wf_diag.get("step"),)
            err += NL + "  workflow.parallel_mode     = %r" % (
                _wf_diag.get("parallel_mode"),)
            err += NL + "  workflow.user_choice       = %r" % (
                _wf_diag.get("user_choice"),)
        except Exception:
            pass
        err += NL + "  → 若 workflow.parallel_mode 为空或为 E，说明本次未按 D 调度；"
        err += NL + "    请重新执行: workflow_gate.py select <A|B|C> D"
        err += NL + "  → 也可手动纠正: mode_gate.py set-parallel-mode parallel"
        return {"ok": False, "error": err,
                "parallel_mode_seen": pmode,
                "active_rooms": active_rooms}
    # ── 【Bug4 修复】重开房间时清除陈旧的 subagent 映射 ──────────────────
    # 问题现象：上一波该房间结束后，subagents[房间] 仍留着【旧 subagent_id】。
    #   重开房间时若不清理，子代理启动后调 whoami 会拿到这个陈旧 id，
    #   然后带着它去调 ask_user.py --child <死会话> —— 提问投递给已死会话，
    #   表现为"提问发出去了但没有实时用户应答"，C模式直接卡死。
    #
    # 修复：room-start 时一并清掉 subagents[name]，保证 whoami 拿到的是
    #   "本波新登记/自报"的身份，而不是上一波的残影。
    _old_sub = None
    try:
        _subs = state.setdefault("subagents", {})
        if name in _subs:
            _old_sub = _subs.pop(name)
            # 记录到审计字段，便于排查"到底清掉了谁"
            state.setdefault("subagent_history", []).append({
                "room": name, "freed_id": (_old_sub.get("id") if isinstance(_old_sub, dict) else _old_sub),
                "at": time.time(), "reason": "room-start 重开房间，清除上一波残留"
            })
            state["subagent_history"] = state["subagent_history"][-50:]
    except Exception:
        pass

    rooms[name] = {"active": True, "started_at": time.time()}
    # ── 【Bug#18 修复】room-start 不再销毁历史报告与授权 ─────────────────
    # 测试反馈：为恢复被清空的 mode_state 而重新 room-start，结果
    #   ① 小屋 latest_report 变 None（"失忆"，看不到此前干过的活）；
    #   ② 部分零件的 confirm-part 授权令牌被重置（大臂等需重补）。
    # 根因：原实现无条件删除本房间的 report/heartbeat 文件。
    #   但"重新登记房间"不等于"重新开始建模" —— 尤其在一次
    #   mode_state 恢复/重开场景下，历史进度与用户授权都是宝贵状态，
    #   删掉会让 C 模式必须重走一遍逐件确认，极易引发越权/漏签。
    #
    # 修复策略（保留历史，只在必要时归档）：
    #   · 报告文件【保留】，但把旧的 latest 归档到 history 之前加一条
    #     "room-restart" 分隔记录，让读取方知道这是新一波；
    #   · 心跳文件【保留】（它本来就是"是否活跃"的信号，新一波会自然刷新）；
    #   · pending.json【完全不动】（授权令牌属于用户意志，任何自动流程
    #     都无权清除；要清必须显式调用 confirm-part 的撤销或 rooms-reset）。
    #   · 若调用方确实想要全新一波，可显式传 --fresh 强制清理。
    _fresh = "--fresh" in sys.argv
    if _fresh:
        for _p in (_report_path(name), _heartbeat_path(name)):
            try:
                if os.path.exists(_p):
                    os.remove(_p)
            except Exception:
                pass
    else:
        # 保留历史：在报告里插一条分隔记录，标明新一波开始
        try:
            _rp = _report_path(name)
            if os.path.exists(_rp):
                with open(_rp, "r", encoding="utf-8") as _f:
                    _old = json.load(_f) or {}
                _hist = _old.get("history") or []
                _hist.append({
                    "room": name, "stage": "room-restart",
                    "detail": "房间重新登记（保留历史进度与授权，非全新一波）",
                    "ts": time.time(),
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                })
                _old["history"] = _hist[-40:]
                _old.setdefault("latest", {})
                _old["restarted_at"] = time.time()
                with open(_rp, "w", encoding="utf-8") as _f:
                    json.dump(_old, _f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        # 心跳保留（不删），新一波会自然刷新 mtime
    save_state(state)
    msg = "小屋 [%s] 已登记,可开始建模" % name
    result = {"ok": True, "room": name, "message": msg}
    # ── 【Bug#18 修复】明确告知调用方：历史是否被保留 ─────────────────────
    result["history_preserved"] = (not _fresh)
    if _fresh:
        result["note_fresh"] = "已按 --fresh 清理该房间的历史报告与心跳（全新一波）。"
    else:
        result["note_preserved"] = (
            "本房间的历史进度报告与 confirm-part 授权【均已保留】。"
            "重开房间不等于重新建模 —— 若确实要全新一波，请加 --fresh 显式清理。")
    # ── 【C15 修复】room-start 时扫描并提示遗留文件 ────────────────────────
    # 测试反馈：上一波 stale 回收后，磁盘遗留了参数错误的旧件
    #   （旧大臂 750N/300mm/304）+ GBK 乱码脚本，Wave2 误复用风险极高。
    #   建议在 room-start 时提示并清理遗留文件，工具链统一 UTF-8。
    # 实现：复用 Bug4 的 stale-artifacts 扫描，把"早于本任务纪元"的产物
    #   在登记时就明确列给调用方，避免总装阶段误用。
    try:
        _epoch = state.get("task_epoch_started_at")
        if _epoch:
            _scan = scan_legacy_artifacts(_default_work_dir(), float(_epoch))
            if _scan.get("counts", {}).get("legacy"):
                result["legacy_artifacts"] = {
                    "count": _scan["counts"]["legacy"],
                    "files": [i["name"] for i in _scan["legacy"][:30]],
                    "warning": ("⚠️ 工作目录存在上一轮遗留件，总装/出图禁止使用这些文件！"
                                "本轮只能使用 current 列表中的产物。"),
                    "how_to_check": "python mode_gate.py stale-artifacts <工作目录>",
                }
    except Exception:
        pass
    if _old_sub is not None:
        _old_id = _old_sub.get("id") if isinstance(_old_sub, dict) else _old_sub
        result["cleared_stale_subagent"] = _old_id
        result["note"] = ("已清除上一波残留的 subagent 映射 (%s)；"
                          "whoami 将返回本波新身份。" % _old_id)
    return result


def _room_completion_evidence(room):
    """【Bug-33 修复】判定一个房间是否【真的】完成了（而非"被 room-end 清掉"）。

    背景（实测 Bug-33）：出图房间的子代理启动失败（Bug-32），从未产出任何东西；
    主对话对它调 room-end，_maybe_advance_workflow_finished 只检查"所有房间都有
    ended_at" → 判定"全部收尾" → 自动把 workflow_state.step 推进为 finished，
    并提示"无需再调 select"。结果是：任务在【没有工程图】的情况下被宣告完成，
    select 之后一律返回 ALREADY_FINISHED，出图阶段再也开不起来。

    修复：引入"完成证据"概念。一个房间算真正完成，必须至少满足其一：
      ① 有 done 阶段的进度报告（reports/<room>.report.json 的 stage=done）；
      ② 有 part_done 报告且数量 > 0（至少产出了零件）；
      ③ 有磁盘产物证据（_recent_artifact_evidence 命中）。
    仅有 ended_at（甚至完全没有报告）不算完成 —— 那是"空房间被清掉"。

    Returns: {"completed": bool, "reasons": [...], "evidence": {...}}
    """
    ev = {"done_report": False, "part_done_count": 0, "artifact": False,
          "report_stage": None, "has_report": False}
    reasons = []
    try:
        rep = _read_json_file(_report_path(room)) or {}
        if isinstance(rep, dict) and rep:
            ev["has_report"] = True
            stage = str(rep.get("stage") or rep.get("last_stage") or "")
            ev["report_stage"] = stage or None
            if stage == "done":
                ev["done_report"] = True
                reasons.append("有 done 阶段报告")
            # 统计 part_done 次数（history 或 events 里）
            _hist = rep.get("history") or rep.get("events") or []
            _pd = 0
            if isinstance(_hist, list):
                for h in _hist:
                    if isinstance(h, dict) and str(h.get("stage") or "") == "part_done":
                        _pd += 1
            if not _pd and isinstance(rep.get("parts"), list):
                _pd = len(rep["parts"])
            ev["part_done_count"] = _pd
            if _pd > 0:
                reasons.append("有 %d 条 part_done 上报" % _pd)
    except Exception:
        pass
    try:
        art = _recent_artifact_evidence(room) or {}
        if art.get("found"):
            ev["artifact"] = True
            reasons.append("有磁盘产物证据")
    except Exception:
        pass
    completed = bool(ev["done_report"] or ev["part_done_count"] > 0 or ev["artifact"])
    return {"completed": completed, "reasons": reasons, "evidence": ev}


def bypass_requested_defense():
    """是否显式绕行三大防线（唯一合法绕行，必定留痕）。"""
    return str(os.environ.get("DSH_DEFENSE_BYPASS") or "").strip() in ("1", "true", "yes")


def _maybe_advance_workflow_finished(reason="room-end"):
    """【Bug#6 修复】全部房间收尾后，自动把 workflow_state.step 推进到 finished。

    ── 问题现象（测试部 Bug#6）──────────────────────────────────────────
      三个房间全部 room-end 之后，workflow_state.json 的 step 仍是
      user_selected（非 finished），于是：
        · 工程模式守卫靠"代码级终止态"判定能否放行 —— 看不到 finished
          → 反复拦截本轮结束；
        · 即使 mode_state 里已无 active 房间，守卫依旧认为"任务未终结"；
        · 模型被迫输出 [TASK_DONE] 才能绕过 —— 破坏门禁设计初衷。
      根因：workflow_gate.py 只在 Wave1→Wave2 递进时更新 step，
        系统【没有】"房间全部结束 → 自动收尾"的逻辑；必须显式再调一次
        select 才可能触发归档，而那一轮往往因 step 已 user_selected 而卡住。

    ── 本修复做什么 ──────────────────────────────────────────────────
      在 room-end / room-fail 落盘后检查一次 mode_state：
        · 若【没有】任何 active 房间，且【不存在】failed_at 的房间
          （失败房间说明任务未真正完成，不能宣告 finished）；
        · 且 workflow_state 处于推进态（非 idle/finished）；
      则把 step 置为 finished 并写 TASK_FINISHED.json，
      让守卫能正确放行、避免"活干完了却结束不了"。

    ⚠️ 注意：这里【只认失败与否】，不校验"是否出过图"——
       出图与否由 workflow_gate 的波次门禁（drafting 房间）保证；
       本函数只是把"机械上已经全结束"这一事实同步给门禁状态机。

    Returns: dict {advanced: bool, reason: str, ...}
    """
    try:
        st = load_state()
        rooms = st.get("rooms") or {}
        # 有任何 active 房间 → 任务未结束
        active = [r for r, v in rooms.items() if (v or {}).get("active")]
        if active:
            return {"advanced": False, "reason": "仍有 active 房间: %s" % active}
        # 有失败房间 → 任务未完成（等待重做），不能宣告 finished
        failed = [r for r, v in rooms.items() if (v or {}).get("failed_at")]
        if failed:
            return {"advanced": False, "reason": "存在失败待重做房间: %s" % failed}
        # 一个房间都没有 → 不是"完成"，只是还没开始
        if not rooms:
            return {"advanced": False, "reason": "无房间记录，视为未开始"}
        # ── 【关键修正】必须确认"该建的房间都建完了" ──────────────────────
        # 只看 mode_state 里"当前已登记且无 active"是不够的：
        #   · 第 1 个房间刚 room-end，其它房间【还没 room-start】，
        #     此时 rooms 里只有它一个，会被误判为"全部完成"→ 提前 finished。
        #   · 这会让守卫在最需要它的中段直接放行，任务半途而废。
        # 正确判据：以 workflow_state.subagent_config.rooms 的【完整清单】为准，
        #   要求清单里的每一个房间都已 ended_at（且无 failed_at）。
        if not os.path.exists(WORKFLOW_STATE_PATH):
            return {"advanced": False, "reason": "workflow_state 不存在"}
        with open(WORKFLOW_STATE_PATH, "r", encoding="utf-8") as f:
            wf = json.load(f) or {}
        step = str(wf.get("step") or "")
        if step == "finished":
            return {"advanced": False, "reason": "已是 finished"}
        if step in ("idle", ""):
            # 门禁没在推进 → 不要凭空造 finished（避免误放行守卫）
            return {"advanced": False, "reason": "门禁未推进(step=%s)，无需推进" % step}
        # 取期望房间清单；拿不到清单时（配置缺失）保守起见不推进
        _cfg = wf.get("subagent_config") or {}
        _expect = [r[0] for r in (_cfg.get("rooms") or []) if r]
        if not _expect:
            return {"advanced": False,
                    "reason": "workflow_state 无 subagent_config.rooms 清单，"
                              "无法确认是否全部完成（保守不推进）"}
        _not_done = [r for r in _expect
                     if not (rooms.get(r) or {}).get("ended_at")]
        if _not_done:
            return {"advanced": False,
                    "reason": "仍有房间未收尾: %s" % _not_done}
        # ══ 【Bug-33 修复】全部 ended_at 还不够 —— 必须每个房间都有"完成证据" ══
        # 原缺陷：只检查 ended_at。出图房间因子代理启动失败（Bug-32）从未产出，
        #   对它 room-end 后被当作"空房间"清掉 → 条件满足 → 自动 finished，
        #   于是"没有工程图"的任务被宣告完成，select 之后一律 ALREADY_FINISHED。
        # 修复：逐房间校验完成证据（done 报告 / part_done / 磁盘产物）。
        #   任一房间无证据 → 不推进 finished，并把"是谁没完成"明确回传。
        _unproven = []
        _ev_map = {}
        for _r in _expect:
            _c = _room_completion_evidence(_r)
            _ev_map[_r] = _c["evidence"]
            if not _c["completed"]:
                _unproven.append(_r)
        if _unproven:
            return {
                "advanced": False,
                "reason": ("房间虽已 room-end，但缺少完成证据（无 done 报告/"
                           "无 part_done/无磁盘产物）: %s" % _unproven),
                "unproven_rooms": _unproven,
                "evidence": _ev_map,
                "hint": ("这些房间可能是【空房间被 room-end 清掉】（例如子代理未真正"
                         "启动）。请检查 list_agents 与 mode_gate.py room-status；"
                         "若确为启动失败，重新 subagent_fork 并让房间真正产出后再 room-end。"
                         "本函数不会把这种状态推进为 finished（Bug-33）。"),
            }
        # ══ 【三大防线 · 自动收尾前强制】══════════════════════════════════
        # 漏点修复：本函数会在【最后一个房间 room-end 时自动】把 step 写成
        #   finished 并落 TASK_FINISHED.json —— 而 workflow_gate._archive_finished
        #   的那道防线总闸【根本不会被执行到】。实测这意味着：只要各房间有
        #   "完成证据"（哪怕只是产出文件），三大防线可以被整体绕过（手写
        #   reports/<room>.physics.json 即可，甚至完全不跑 FEA）。
        # 修复：自动收尾【之前】同样强制三防线；不通过就不写 finished，
        #   让任务停在"房间已结束但未交付"的可修复状态。
        if not bypass_requested_defense():
            try:
                import defense_gate as _dg2
                try:
                    _dt2 = _dg2.check_task_defense()
                except Exception as _e_auto:
                    return {
                        "advanced": False,
                        "reason": "三大防线校验异常，按[失效关闭]拒绝自动收尾",
                        "defense_required": True,
                        "blockers": ["防线校验抛异常: %r" % (_e_auto,)],
                        "hint": "修复 reports/ 下的凭据结构，或用 DSH_DEFENSE_BYPASS=1 显式绕行（会留痕）。",
                    }
                if not _dt2["ok"]:
                    return {
                        "advanced": False,
                        "reason": "三大防线未通过，拒绝自动收尾为 finished",
                        "defense_required": True,
                        "blockers": _dt2["blockers"][:24],
                        "hint": ("先补齐三道防线（材料凭据 / physics-optimize / "
                                 "physics-validate-domain），再用 room-end 收尾；"
                                 "或设 DSH_DEFENSE_BYPASS=1 显式绕行（会留痕）。"),
                    }
            except ImportError:
                pass
            except Exception:
                pass
        else:
            try:
                import defense_gate as _dg3
                _dg3.log_bypass("mode_gate.auto-finish",
                                reason="DSH_DEFENSE_BYPASS=1", by="env")
            except Exception:
                pass
        wf["step"] = "finished"
        wf["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        wf["finished_by"] = "auto(%s)" % reason
        wf["finished_rooms"] = sorted(_expect)
        wf["assembly_confirmed"] = False
        with open(WORKFLOW_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(wf, f, ensure_ascii=False, indent=2)
        # 写完成标记（守卫据此放行）
        try:
            with open(os.path.join(STATE_DIR, "TASK_FINISHED.json"), "w",
                      encoding="utf-8") as f:
                json.dump({"finished": True, "finished_at": wf["finished_at"],
                           "completed_count": len(_expect),
                           "total_rooms": len(_expect),
                           "message": "全部房间已收尾，由 mode_gate 自动推进"},
                          f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        return {"advanced": True, "from_step": step, "to_step": "finished",
                "rooms": sorted(_expect), "reason": reason}
    except Exception as e:
        return {"advanced": False, "error": repr(e)}


def _auto_register_room_artifacts(name):
    """【Bug-05 修复】房间收尾时按【磁盘实际】补登记产物。

    ── 为什么需要 ────────────────────────────────────────────────────────
    实测缺陷：结构件 4 件、壳体机架 5 件在磁盘上真实存在，但
      artifacts_registry 里【没有登记】—— 因为小屋在登记完成前就
      inactive/finished 退出了（BUG-11 的姊妹问题）。
    后果：总装前的 verify-ownership 去重核对会漏掉这 9 件 → 总装缺件。

    修复：房间收尾时（room-end / room-fail）主动扫描本房间的产物目录，
      把磁盘上【真实存在】的 .SLDPRT 补录进 registry。
      归属判定沿用系统既有的"磁盘产物证据"逻辑（_recent_artifact_evidence
      的扫描目录），并做大小写不敏感匹配（Bug-09）。

    Returns: {ok, registered, scanned_dirs, note}
    """
    out = {"ok": False, "registered": 0, "paths": [], "scanned_dirs": []}
    try:
        # 复用既有的产物证据扫描（含 task work_dir / output / Desktop 兜底）
        ev = _recent_artifact_evidence(name, window=SW_ACTIVE_EVIDENCE_WINDOW)
        _dirs = list(ev.get("scanned_dirs") or [])
        out["scanned_dirs"] = _dirs
        # 已登记集合（避免重复登记）
        _existing = set()
        for _it in _registered_artifacts(name):
            try:
                _existing.add(os.path.normcase(os.path.abspath(str(_it.get("path")))))
            except Exception:
                continue
        _added = []
        for _d in _dirs:
            try:
                if not _d or not os.path.isdir(_d):
                    continue
                for _fn in os.listdir(_d):
                    if not _fn.lower().endswith(".sldprt"):
                        continue
                    # 跳过 SW 临时/锁文件（~$ 前缀）
                    if _fn.startswith("~$"):
                        continue
                    _fp = os.path.join(_d, _fn)
                    if not os.path.isfile(_fp):
                        continue
                    _key = os.path.normcase(os.path.abspath(_fp))
                    if _key in _existing:
                        continue
                    _r = cmd_room_artifact(name, _fp)
                    if _r.get("ok"):
                        _existing.add(_key)
                        _added.append(_fp)
            except Exception:
                continue
        out["registered"] = len(_added)
        out["paths"] = _added
        out["ok"] = True
        out["note"] = ("按磁盘实际补登记：新增 %d 件（防子代理提前退出导致漏登记）"
                       % len(_added))
    except Exception as e:
        out["error"] = repr(e)
    return out


def _finalize_room(name, failed=False, force=False):
    """房间收尾：标记结束 + 释放SW锁 + 确保本房间启动的SW进程完全关闭。

    ── 【Bug-25/27 修复】新增 force 语义 ─────────────────────────────────
    原缺陷（实测）：小屋完成后调 room-end，返回
      sw_lock_released=false, sw_killed=false —— 显示"已清理"，
      但【既没释放 SW 锁也没杀 SW 进程】。
      原因：小屋自报"未调 close-all 以免打断其他房间"，于是
      sw_by_room 为假 / owner 不是本房间 → 判定为"不归我释放"。
      后果：若小屋忘了 close-all，room-end 不会兜底释放 SW 锁，
      下一个房间可能排队等锁而卡住（Bug-25/27）。

    修复：
      · force=True 时【无条件】清理本房间占用的 SW 使用权：
        移除队列位、清 owner（若属于本房间或已死）、必要时强制关 SW；
      · force=False 时行为不变，但返回值新增 sw_lock_owner /
        sw_lock_release_reason，明确告知"为什么没释放"，
        调用方据此决定是否加 --force。
    """
    state = load_state()
    rooms = state.setdefault("rooms", {})
    if name in rooms:
        rooms[name]["active"] = False
        if failed:
            rooms[name]["ended_at"] = None
            rooms[name]["failed_at"] = time.time()
        else:
            rooms[name]["ended_at"] = time.time()
    lk = _sw_lock(state)
    was_owner = (lk.get("owner") == name)
    killed = False
    waited = False
    _owner_at_entry = lk.get("owner")
    _release_reason = None
    # 【Bug1修复】即使 owner 不是自己，只要本房间在队列中也要移除，
    # 避免死房间永久占队列位。
    if name in lk.get("queue", []):
        lk["queue"] = [x for x in lk.get("queue", []) if x != name]
    # ── 【Bug-25/27】--force：无条件释放本房间的 SW 使用权 ──────────────
    if force:
        _had_owner = lk.get("owner")
        if _had_owner:
            lk["prev_owner"] = _had_owner
        lk["owner"] = None
        lk["acquired_at"] = None
        lk["sw_by_room"] = False
        if _sw_running():
            _sw_force_close()
            killed = True
        save_state(state)
        _release_reason = ("--force 强制释放（原 owner=%s）" % (_had_owner or "无"))
        # ── 【Bug-05 修复】强制释放分支同样要补登记（失败房间更常见漏登记）──
        try:
            _ar_f = _auto_register_room_artifacts(name)
            if _ar_f.get("registered"):
                print("[mode_gate] 【Bug-05】--force 收尾自动补登记 %d 件产物"
                      % _ar_f["registered"])
        except Exception:
            pass
        return True, killed, waited, _release_reason
    # 【Bug1修复】owner 已是死房间（或本来就是自己）→ 都要释放锁。
    # 原先只在 was_owner 时释放，导致死房间留下的锁永久占用。
    _owner_dead = bool(lk.get("owner")) and (not _lock_owner_alive(state, lk.get("owner")))
    if not (was_owner or _owner_dead):
        # 明确告知为什么没释放（Bug-25/27：原实现只回 false 不说原因）
        if _owner_at_entry:
            _release_reason = ("SW 锁属于其它房间 [%s]，未释放（避免打断它的工作）。"
                               "若确认它已停，用 room-end --force 强制释放。"
                               % _owner_at_entry)
        else:
            _release_reason = "SW 锁无 owner（无需释放）"
    if was_owner or _owner_dead:
        if _owner_dead and not was_owner:
            lk["prev_owner"] = lk.get("owner")
        else:
            lk["prev_owner"] = name
        lk["owner"] = None
        lk["acquired_at"] = None
        # 本房间启动的 SW 必须完全关闭，下一个房间才能干净地开SW
        # 【bug修复】等待期间持续更新心跳，防止被误判为 stale
        if lk.get("sw_by_room") and _sw_running():
            for _ in range(SW_HANDOVER_GRACE // 2):
                if not _sw_running():
                    break
                time.sleep(2)
                waited = True
                # 持续写心跳，让主对话知道本房间仍在活跃收尾
                try:
                    hb_path = _heartbeat_path(name)
                    with open(hb_path, "w", encoding="utf-8") as _f:
                        json.dump({"name": name, "ts": time.time(), "pid": os.getpid(), "phase": "finalizing"}, _f)
                except Exception:
                    pass
            if _sw_running():
                _sw_force_close()
                killed = True
        lk["sw_by_room"] = False
    save_state(state)
    # ── 【Bug-05 修复】房间下线时按【磁盘实际】补登记产物 ────────────────
    #   小屋可能在登记完成前就退出（实测漏登记 9 件），总装去重核对会漏件。
    #   这里在收尾时补登记，保证 registry 与磁盘一致。
    try:
        _ar = _auto_register_room_artifacts(name)
        if _ar.get("registered"):
            print("[mode_gate] 【Bug-05】room-end 自动补登记 %d 件产物: %s"
                  % (_ar["registered"], ", ".join(os.path.basename(p) for p in _ar["paths"][:8])))
    except Exception as _e_ar:
        print("[mode_gate] 自动补登记异常（不阻断收尾）: %r" % (_e_ar,))
    # ── 【Bug-36 修复】所有 return 分支必须返回【相同数量】的值 ──────────────
    # 回归根因：force 分支返回 4 值（含 _release_reason），本行只返回 3 值，
    #   而 cmd_room_end 按 4 值解包 → 正常收尾路径抛
    #   "ValueError: not enough values to unpack (expected 4, got 3)"，
    #   导致 room-end 对所有房间全部失效（比首轮原 bug 更严重）。
    # 这里补齐第 4 个值；_release_reason 在成功释放时为 None（由调用方按
    # was_owner 判定措辞），保持与 force 分支同一契约。
    return (was_owner or _owner_dead), killed, waited, _release_reason


def cmd_room_end(name, force=False):
    """【三大防线】room-end 是"房间下线"这个不可逆动作 —— 就在这里强制执行三道防线。

    设计意图（用户要求）：不是让 AI 自己选择要不要校核，而是 AI 一旦走到
      "结束房间"这一步，系统就【必须】先拿到三份凭据，否则拒绝下线。
        · 防线①材料：本房间每个 .sldprt 都有材料凭据且密度/家族可信；
        · 防线②物理：本房间有物理校核凭据且 overall != FAIL；
        · 防线③领域：本房间有 DSVA 校验凭据且无 CRITICAL 违规。
    放行：--force 或环境变量 DSH_DEFENSE_BYPASS=1（都会留痕，绝不静默）。
    """
    if force:
        # [审计] --force 也是绕行，必须留痕（原实现直接跳过、零审计）。
        try:
            import defense_gate as _dg0
            _dg0.log_bypass("mode_gate.room-end:" + str(name),
                            reason="--force 显式强制放行（跳过三大防线）",
                            by="cli-force")
        except Exception:
            pass
    if not force:
        try:
            import defense_gate as _dg
            if _dg.bypass_requested():
                _dg.log_bypass("mode_gate.room-end:" + str(name),
                               reason="DSH_DEFENSE_BYPASS=1", by="env")
            else:
                try:
                    _def = _dg.check_room_defense(name)
                except Exception as _e_def:
                    # [FAIL-CLOSED] 凭据异常绝不当"通过"：畸形/恶意 JSON 若能抛异常
                    #   让门禁静默放行，防线就等于不存在（对抗测试 C6 实测过）。
                    #   这里把异常本身当成一次拦截，并要求人工/修数据后重试。
                    return {
                        "ok": False,
                        "room": name,
                        "gate": "DEFENSE_ERROR",
                        "error": "【三大防线】校验过程异常，按[失效关闭]拒绝 room-end。",
                        "detail": repr(_e_def),
                        "message": ("=== 三大防线校验异常（拒绝下线）===\n"
                                    "房间: %s\n异常: %r\n\n"
                                    "这通常意味着 reports/<房间>.physics.json 或 "
                                    ".domain.json 结构被改坏（例如 violations 不是数组）。\n"
                                    "请用 defense_gate.py check-room \"%s\" 定位并修复；\n"
                                    "确需放行：--force 或 DSH_DEFENSE_BYPASS=1（会留痕）。"
                                    % (name, _e_def, name)),
                    }
                if not _def["ok"]:
                    _nl = chr(10)
                    _lines = ["=== 三大防线拦截：房间 [" + str(name) + "] 不得下线 ===", ""]
                    for _b in _def["blockers"]:
                        _lines.append("  ✗ " + _b)
                    _lines += [
                        "",
                        "补齐方式：",
                        "  ① 材料：每个零件用 swapi.new_part(material='Q235') 显式赋材后重新 save()；",
                        "  ② 物理：在房间内跑 python sw_bridge.py physics-optimize <load_case.json> --room " + str(name),
                        "  ③ 领域：在房间内跑 python sw_bridge.py physics-validate-domain <domain> --room " + str(name),
                        "",
                        "确需放行（会留痕到 reports/defense_bypass.json）：",
                        "  · python mode_gate.py room-end \"" + str(name) + "\" --force",
                        "  · 或设环境变量 DSH_DEFENSE_BYPASS=1",
                    ]
                    return {
                        "ok": False,
                        "room": name,
                        "gate": "DEFENSE_REQUIRED",
                        "room_type": _def.get("room_type"),
                        "blockers": _def["blockers"],
                        "defense": _def["details"],
                        "error": "【三大防线】房间 [" + str(name) + "] 未通过强制校验，拒绝 room-end。",
                        "message": _nl.join(_lines),
                    }
        except ImportError:
            pass   # defense_gate.py 缺失时降级，绝不因门禁自身故障卡死流程
        except Exception:
            pass
    was_owner, killed, waited, _reason = _finalize_room(name, failed=False,
                                                        force=force)
    # ── 【Bug#6 修复】房间收尾后自动推进门禁到 finished（若无活动房间）──
    #   这是"活干完了守卫却不让结束"的直接修复：不再依赖再调一次 select。
    _adv = _maybe_advance_workflow_finished(reason="room-end:%s" % name)
    msg = "小屋 [%s] 已清理" % name
    if was_owner:
        msg += "；SW锁已释放"
        if killed:
            msg += "，残留SW进程已强制关闭"
        elif waited:
            msg += "，SW已自行完全关闭"
    else:
        # ── 【Bug-25/27】没释放时必须说清原因（原实现只回 false）──────────
        msg += "；SW锁未释放（%s）" % (_reason or "无 owner 变更")
    _out = {"ok": True, "room": name, "sw_lock_released": was_owner,
            "sw_killed": killed, "sw_lock_release_reason": _reason,
            "message": msg}
    if not was_owner:
        _out["hint"] = ("若该房间确实已停、需要立刻让出 SW 给下一个房间，"
                        "用: python mode_gate.py room-end \"%s\" --force"
                        % name)
    if _adv.get("advanced"):
        _out["workflow_advanced"] = True
        # ── 【Bug-33 修复】提示语必须准确：自动 finished 只在"全部房间都有
        #    完成证据"时才发生；若还有未完成的波次，_maybe_advance 会返回
        #    未推进并带 unproven_rooms，这里据实告知，不再一律说"无需再调 select"。
        _out["note"] = ("【Bug#6 修复】检测到全部房间已收尾 —— 已自动把 "
                        "workflow_state.step 推进为 finished（%s），"
                        "守卫据此可正常放行本轮结束。无需再调 select。"
                        % _adv.get("from_step"))
        _out["message"] = msg + "；门禁已自动收尾(step=finished)"
    elif _adv.get("unproven_rooms"):
        # 有房间 room-end 了但缺完成证据（Bug-33：空房间被清掉）
        _out["workflow_advanced"] = False
        _out["unproven_rooms"] = _adv.get("unproven_rooms")
        _out["note"] = ("【Bug-33 修复】未自动收尾：这些房间缺少完成证据 —— %s。"
                        "门禁不会在缺少交付物的情况下宣告完成。"
                        % (_adv.get("reason") or ""))
        _out["message"] = msg + "；门禁未收尾（存在无完成证据的房间）"
    return _out


def cmd_room_fail(name):
    # ══ 【三大防线 · 留痕体检】══════════════════════════════════════
    # room-fail 是唯一能"清掉 active 房间"却原本完全不经防线的官方命令，
    #   而它正是自动 finished 路径的必要前置。这里不阻断（回退本就是
    #   "承认失败、准备重做"），但必须把该房间当前缺哪道防线【记下来】，
    #   让"先 room-fail 清场、再伪造完成证据"这种绕过可被事后审计。
    try:
        import defense_gate as _dg_f
        _df = _dg_f.check_room_defense(name)
        _dg_f.log_bypass("mode_gate.room-fail:" + str(name),
                         reason=("房间回退留痕；当前防线缺口: " +
                                 ("; ".join(_df.get("blockers") or []) or "无")),
                         by="room-fail")
    except Exception:
        pass
    state = load_state()
    rooms = state.setdefault("rooms", {})
    if name not in rooms:
        rooms[name] = {"active": False, "ended_at": None, "failed_at": time.time()}
    was_owner, killed, waited, _reason = _finalize_room(name, failed=True)
    return {"ok": True, "room": name,
            "sw_lock_released": was_owner, "sw_killed": killed,
            "sw_lock_release_reason": _reason,
            "message": "房间 [%s] 已回退到待处理状态。重新调用 workflow_gate.py select 可再次获得该房间。" % name}


def cmd_sw_request(name):
    """排队/获取 SW 使用权。快路径直接获取；否则入队等待。"""
    state = load_state()
    lk = _sw_lock(state)
    if not _lock_needed(state):
        save_state(state)
        return {"ok": True, "need_lock": False, "room": name}
    # 【Bug2 修复·饥饿根因】原实现在这里直接给持有者续期：
    #     if lk.get("owner") == name: return {"acquired": True}
    # 这等于让持有者无限次"插自己的队"，永远不会走到下面的公平检查，
    # 于是 cmd_seq 一路涨到 14、锁被独占 272 秒，队列里的房间被饿死。
    #
    # 现在改为：持有者再次取锁时，先做公平体检——若队列里有别人在等，
    # 且自己已达连跑上限（SW_FAIR_CMDS）或连续独占超时（SW_FAIR_SECONDS），
    # 则主动让位；否则才允许续期。
    q0 = lk.setdefault("queue", [])
    if lk.get("owner") == name:
        _others = [x for x in q0 if x != name]
        _cmds = int(lk.get("cmd_seq", 0) or 0)
        _fs = lk.get("fair_start") or lk.get("acquired_at")
        try:
            _fheld = time.time() - float(_fs) if _fs else 0.0
        except Exception:
            _fheld = 0.0
        if _others and (_cmds >= SW_FAIR_CMDS or _fheld > SW_FAIR_SECONDS):
            # 让位：清空自己，把队首扶正，交由下方流程重新分配
            lk["prev_owner"] = name
            lk["owner"] = None
            lk["acquired_at"] = None
            lk["cmd_seq"] = 0
            lk["fair_start"] = None
            save_state(state)
            lk = _sw_lock(state)
        else:
            return {"ok": True, "need_lock": True, "acquired": True, "room": name,
                    "renewed": True, "cmd_seq": _cmds}
    q = lk.setdefault("queue", [])
    if name not in q:
        q.append(name)

    # ── 【Bug6 修复】孤儿锁快路径体检 ───────────────────────────────────
    # 必须在 reclaim 与快路径判定【之前】执行：owner:null 但 prev_owner 残留
    # （已死房间）会让 sw_by_room 一直挂着，队列全员 sw_busy 永久阻塞。
    # 这里做一次彻底清理，让队首能立即接管。
    try:
        _orphan = _purge_orphan_lock(state, requester=name)
        if _orphan.get("purged"):
            save_state(state)
            lk = _sw_lock(state)
    except Exception:
        pass

    # ── 【Bug1/3修复】先尝试回收失效锁（死房间/超时/孤儿）──
    reclaimed, old_owner = _reclaim_lock(state, "sw-request", requester=name)
    if reclaimed:
        save_state(state)
        lk = _sw_lock(state)

    running = _sw_running()
    # 【Bug5修复】owner为空但 sw_by_room=True 且进程在跑 → 无人持锁却挡住队首。
    # 若 prev_owner 已不再是活动房间，清掉 sw_by_room 让队首立即接管。
    if (not lk.get("owner")) and lk.get("sw_by_room"):
        _prev = lk.get("prev_owner")
        if _prev and not _lock_owner_alive(state, _prev):
            lk["sw_by_room"] = False
            lk["prev_owner"] = None
            save_state(state)
        elif not _prev:
            lk["sw_by_room"] = False
            save_state(state)
    can_fast = False
    # 快路径: 锁空闲 + 自己是队首 + (SW没在跑 或 SW不是房间启动的)
    if (not lk.get("owner")) and q and q[0] == name:
        can_fast = ((not running) or (not lk.get("sw_by_room")))
    if can_fast:
        lk["owner"] = name
        lk["acquired_at"] = time.time()
        # 【Bug4修复】单命令级锁：记录本次命令编号，命令结束后即可释放。
        # 同一个房间的下一步命令会重新排队，但不影响其他房间公平性。
        # 新一轮取锁：若上一轮已让位（fair_start 为空），重置计数与公平计时
        if not lk.get("fair_start"):
            lk["cmd_seq"] = 0
            lk["fair_start"] = time.time()
        lk["cmd_seq"] = int(lk.get("cmd_seq", 0)) + 1
        lk["hold_mode"] = "command"   # command=单命令级 | room=房间级
        lk["sw_by_room"] = (not running) or bool(lk.get("sw_by_room"))
        lk["queue"] = [x for x in q if x != name]
        save_state(state)
        return {"ok": True, "need_lock": True, "acquired": True, "room": name,
                "sw_was_running": running, "hold_mode": "command",
                "cmd_seq": lk["cmd_seq"], "max_hold_seconds": SW_CMD_HOLD}
    pos = (q.index(name) + 1) if name in q else None
    save_state(state)
    return {"ok": True, "need_lock": True, "acquired": False, "queued": True,
            "position": pos, "owner": lk.get("owner"), "sw_running": running,
            "wait_hint": "SW 正被 [%s] 使用（进程级检测）。请 Start-Sleep 90 秒后重试同一命令，队列位置已保留。" % lk.get("owner")}


def cmd_sw_wait(name, timeout=300):
    """阻塞等待 SW 使用权。上家SW进程未关闭时持续等待，超时强制接管。"""
    deadline = time.time() + int(timeout)
    state = load_state()
    lk = _sw_lock(state)
    if lk.get("owner") == name:
        return {"ok": True, "acquired": True, "room": name}
    q = lk.setdefault("queue", [])
    if name not in q:
        q.append(name)
        save_state(state)
    stale_since = None
    last_reclaim_check = 0
    while True:
        state = load_state()
        lk = _sw_lock(state)
        if lk.get("owner") == name:
            return {"ok": True, "acquired": True, "room": name}
        q = lk.get("queue", [])
        # ── 【Bug1/3/5修复】每秒检查一次失效锁，立即回收而不是干等 ──
        now_ts = time.time()
        if now_ts - last_reclaim_check >= 1.0:
            last_reclaim_check = now_ts
            reclaimed, old_owner = _reclaim_lock(state, "sw-wait", requester=name)
            if reclaimed:
                save_state(state)
                lk = _sw_lock(state)
                q = lk.get("queue", [])
        if (not lk.get("owner")) and q and q[0] == name:
            running = _sw_running()
            # 【Bug5修复】prev_owner 若已死亡，不能继续挡队首
            _prev_owner = lk.get("prev_owner")
            if _prev_owner and _prev_owner != name and not _lock_owner_alive(state, _prev_owner):
                lk["sw_by_room"] = False
                lk["prev_owner"] = None
                save_state(state)
                running = _sw_running()
            blocked_by_prev = running and lk.get("sw_by_room") and lk.get("prev_owner") not in (None, name)
            if not blocked_by_prev:
                lk["owner"] = name
                lk["acquired_at"] = time.time()
                lk["cmd_seq"] = int(lk.get("cmd_seq", 0)) + 1
                lk["hold_mode"] = "command"
                lk["sw_by_room"] = (not running) or bool(lk.get("sw_by_room"))
                lk["queue"] = [x for x in q if x != name]
                save_state(state)
                return {"ok": True, "acquired": True, "room": name, "waited": True,
                        "hold_mode": "command", "max_hold_seconds": SW_CMD_HOLD}
            if stale_since is None:
                stale_since = time.time()
            elif time.time() - stale_since > SW_STALE_KILL:
                _sw_force_close()
                lk["owner"] = name
                lk["acquired_at"] = time.time()
                lk["sw_by_room"] = True
                lk["queue"] = [x for x in q if x != name]
                save_state(state)
                return {"ok": True, "acquired": True, "room": name, "forced": True,
                        "note": "上一房间未关闭SW，已等待%d秒后强制关闭并接管" % SW_STALE_KILL}
        held = (time.time() - lk.get("acquired_at")) if lk.get("acquired_at") else 0
        if lk.get("owner") and held > SW_MAX_HOLD:
            old = lk.get("owner")
            lk["owner"] = name
            lk["acquired_at"] = time.time()
            lk["queue"] = [x for x in q if x != name]
            save_state(state)
            return {"ok": True, "acquired": True, "room": name, "took_over": True,
                    "took_over_from": old, "note": "原占用者超过%d秒未释放，已强制接管" % SW_MAX_HOLD}
        if time.time() >= deadline:
            pos = (q.index(name) + 1) if name in q else None
            return {"ok": False, "acquired": False, "room": name, "owner": lk.get("owner"),
                    "position": pos, "sw_running": _sw_running(),
                    "error": "SW 正被 [%s] 占用(已 %.0f 秒)，等待超时。请稍后重试。" % (lk.get("owner"), held)}
        time.sleep(1.0)


def cmd_lock_doctor(auto=False):
    """【Bug1/2/3/5修复】锁体检：检测并清理失效锁、孤儿队列、失联状态。

    auto=False 仅报告；auto=True 自动修复。
    """
    state = load_state()
    lk = _sw_lock(state)
    report = {"ok": True, "auto": auto, "findings": [], "fixed": []}

    owner = lk.get("owner")
    held = _lock_owner_lock_age(state)
    running, pids = _sw_proc_list()

    # 检查1: owner 是否存活
    if owner:
        alive = _lock_owner_alive(state, owner)
        report["findings"].append({
            "check": "owner_alive", "owner": owner, "alive": alive, "held_seconds": round(held, 1)
        })
        if not alive:
            if auto:
                lk["prev_owner"] = owner
                lk["owner"] = None
                lk["acquired_at"] = None
                report["fixed"].append("释放死房间[%s]持有的锁（已持%.0f秒）" % (owner, held))
            else:
                report["findings"].append({"issue": "owner_dead", "owner": owner,
                                           "hint": "运行 lock-doctor --auto 自动释放"})
        elif held > SW_CMD_HOLD:
            report["findings"].append({"issue": "owner_over_hold", "owner": owner,
                                       "held": round(held, 1), "limit": SW_CMD_HOLD})
            if auto:
                lk["prev_owner"] = owner
                lk["owner"] = None
                lk["acquired_at"] = None
                report["fixed"].append("释放超时锁（[%s] 持锁%.0f秒 > %d秒）" % (owner, held, SW_CMD_HOLD))

    # 检查2: Bug5/Bug6 —— owner=null 但 sw_by_room / prev_owner 残留
    if not lk.get("owner"):
        _prev = lk.get("prev_owner")
        _orphan = bool(lk.get("sw_by_room")) or bool(_prev)
        if _orphan:
            _prev_alive = _lock_owner_alive(state, _prev) if _prev else None
            report["findings"].append({
                "issue": "orphan_lock_residue",
                "sw_by_room": bool(lk.get("sw_by_room")),
                "prev_owner": _prev,
                "prev_owner_alive": _prev_alive,
                "sw_process_running": running,
                "hint": ("owner 已为空，但残留标志仍在 → 队列会被 sw_busy 永久阻塞。"
                         "用 lock-doctor --auto 或 sw-request 快路径自动清理。")
            })
            if auto:
                # 【Bug6 修复】用统一的体检函数，覆盖三类漏网：
                #   prev 为空 / prev 已死未登记 / SW 进程根本没跑
                _res = _purge_orphan_lock(state, requester="lock-doctor")
                if _res.get("purged"):
                    report["fixed"].append(
                        "清除孤儿锁残留: %s" % "; ".join(_res.get("actions", [])))
                else:
                    # 兜底：prev 名义上存活但确需清理时，仍按旧语义清一次
                    lk["sw_by_room"] = False
                    lk["prev_owner"] = None
                    report["fixed"].append("强制清除孤儿标志 sw_by_room + prev_owner")

    # 检查3: 队列中的死房间
    dead_in_queue = []
    for r in list(lk.get("queue", [])):
        if not _lock_owner_alive(state, r):
            dead_in_queue.append(r)
    if dead_in_queue:
        report["findings"].append({"issue": "dead_rooms_in_queue", "rooms": dead_in_queue})
        if auto:
            lk["queue"] = [x for x in lk.get("queue", []) if x not in dead_in_queue]
            report["fixed"].append("清除队列中已死亡的房间: %s" % ", ".join(dead_in_queue))

    # 检查4: Bug2 —— sw_state.json 与真实进程对比
    stale = None
    try:
        with open(_SW_STATE_FILE, "r", encoding="utf-8") as f:
            stale = json.load(f)
    except Exception:
        pass
    if stale is not None:
        age = time.time() - stale.get("updated_at", 0)
        mismatch = (bool(stale.get("sw_running")) != (running > 0))
        report["findings"].append({
            "check": "sw_state_sync", "file_running": stale.get("sw_running"),
            "real_running": running > 0, "real_count": running, "real_pids": pids,
            "file_age_seconds": round(age, 1), "mismatch": mismatch
        })
        if mismatch or age > 30:
            if auto:
                st = {"sw_running": running > 0, "sw_count": running, "sw_pids": pids,
                      "updated_at": time.time()}
                try:
                    with open(_SW_STATE_FILE, "w", encoding="utf-8") as f:
                        json.dump(st, f)
                    report["fixed"].append("回写 sw_state.json（真实进程数=%d）" % running)
                except Exception:
                    pass

    # 检查5: 队列非空但无人持锁 → 队首应能立即接管
    q = lk.get("queue", [])
    if q and not lk.get("owner"):
        report["findings"].append({"issue": "queue_stalled", "queue": q,
                                   "hint": "队首应可接管（若被 sw_by_room 挡住，已自动清除）"})

    if auto:
        save_state(state)
    report["lock_after"] = dict(_sw_lock(load_state()))
    report["sw_running_now"] = running
    report["sw_pids_now"] = pids
    return report


def cmd_sw_release(name):
    state = load_state()
    lk = _sw_lock(state)
    if lk.get("owner") == name:
        lk["prev_owner"] = name
        lk["owner"] = None
        lk["acquired_at"] = None
        save_state(state)
        nxt = lk.get("queue", [None])[0] if lk.get("queue") else None
        return {"ok": True, "released": name, "next_in_queue": nxt,
                "message": "SW 使用权已释放，队列下一个: %s" % nxt}
    return {"ok": True, "released": None, "owner": lk.get("owner"),
            "note": "该房间未持有 SW 锁"}


def cmd_sw_proc():
    running = _sw_running()
    pids = []
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq " + SW_EXE, "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, timeout=15,
                             creationflags=CREATE_NO_WINDOW)
        for line in (out.stdout or "").splitlines():
            if SW_EXE in line.upper():
                parts = [x.strip(chr(34)) for x in line.split(",")]
                if parts:
                    pids.append(parts[1] if len(parts) > 1 else parts[0])
    except Exception:
        pass
    return {"ok": True, "sw_running": running, "pids": pids, "exe": SW_EXE}


def _heartbeat_dirs():
    """所有可能出现心跳文件的目录（去重、保序）。

    ── 【观察点 11 修复】读取侧必须同时看【两份副本】──────────────────────
    工程模式在磁盘上有两份副本，`heartbeats/` 因此可能出现两处：
      · STATE_DIR/heartbeats —— _store.state_dir() 决定的权威落点（通常安装副本）；
      · _BASE_DIR/heartbeats —— 脚本自身目录（从工作区调用脚本时的落点）。
    实测症状（观察点 11）：`壳体机架` 的 `room-status` 报 no_heartbeat
      （heartbeat_age_sec: null），但磁盘上 `壳体机架.heartbeat` 的 mtime 很新 ——
      因为**写入方与读取方看的不是同一份目录**。
    修复：读取时把两处都查一遍，取【最新】的那个作为权威年龄。
    这样"从哪份调用都不会误判为无心跳"，且不改变写入落点（写入仍由
    _heartbeat_path 统一到 STATE_DIR，避免重新制造分裂）。
    """
    out = []
    for _d in (HEARTBEAT_DIR, os.path.join(_BASE_DIR, "heartbeats")):
        try:
            _a = os.path.abspath(_d)
        except Exception:
            continue
        if _a not in out:
            out.append(_a)
    return out


def _heartbeat_age(room):
    """返回房间心跳年龄（秒）；无心跳返回 None。

    ── 【观察点 11 修复】取【两处候选里最新】的心跳 ──────────────────────
    原实现只读 HEARTBEAT_DIR，若心跳被写到另一份副本就判 no_heartbeat，
    进而误触发 recover/restart（观察点 11 的"与实际文件时间不一致"）。
    现在扫描全部候选目录，返回最小年龄（= 最新心跳）。
    """
    safe = "".join(c for c in room if c.isalnum() or c in " _-").strip()
    if not safe:
        return None
    newest = None
    for _d in _heartbeat_dirs():
        try:
            hb = os.path.join(_d, safe + ".heartbeat")
            if os.path.exists(hb):
                age = os.path.getmtime(hb)
                if newest is None or age > newest:
                    newest = age
        except Exception:
            continue
    if newest is None:
        return None
    return round(time.time() - newest, 1)


def _heartbeat_age_detail(room):
    """返回心跳细节（供 room-status 排障：到底哪份目录有文件）。"""
    safe = "".join(c for c in room if c.isalnum() or c in " _-").strip()
    detail = []
    for _d in _heartbeat_dirs():
        hb = os.path.join(_d, safe + ".heartbeat")
        try:
            if os.path.exists(hb):
                detail.append({"dir": _d, "file": hb,
                               "age_sec": round(time.time() - os.path.getmtime(hb), 1)})
            else:
                detail.append({"dir": _d, "file": hb, "age_sec": None})
        except Exception as e:
            detail.append({"dir": _d, "error": repr(e)})
    return detail


def cmd_sw_status():
    state = load_state()
    # 【Bug1修复】查询状态时顺带回收失效锁（只读查询也要顺手清理）
    reclaimed, old_owner = _reclaim_lock(state, "sw-status")
    if reclaimed:
        save_state(state)
    lk = state.get("sw_lock", {})
    held = 0
    if lk.get("acquired_at"):
        held = time.time() - lk["acquired_at"]
    owner = lk.get("owner")
    _owner_alive = _lock_owner_alive(state, owner) if owner else None
    _owner_hb_age = _heartbeat_age(owner) if owner else None
    # 队列中每个房间的心跳年龄，便于排查
    queue_detail = []
    for r in lk.get("queue", []):
        queue_detail.append({
            "room": r,
            "alive": _lock_owner_alive(state, r),
            "heartbeat_age": _heartbeat_age(r),
            "is_head": (lk.get("queue", [None])[0] == r),
        })
    running, pids = _sw_proc_list()
    return {"ok": True, "owner": owner, "queue": lk.get("queue", []),
            "queue_detail": queue_detail,
            "held_seconds": round(held, 1),
            "held_limit_seconds": SW_CMD_HOLD,
            "over_hold": held > SW_CMD_HOLD,
            "sw_running": running > 0, "sw_pids": pids,
            "sw_by_room": lk.get("sw_by_room"), "prev_owner": lk.get("prev_owner"),
            "owner_alive": _owner_alive,
            "owner_heartbeat_age": _owner_hb_age,
            "owner_heartbeat_timeout": SW_HEARTBEAT_TIMEOUT,
            "reclaimed_on_query": reclaimed,
            "reclaimed_from": old_owner,
            "hold_mode": lk.get("hold_mode"),
            "parallel_mode": get_parallel_mode(), "lock_needed": _lock_needed(state)}


def cmd_status():
    state = load_state()
    active = [k for k, v in state.get("rooms", {}).items() if v.get("active")]
    _pm = _norm_pmode(state.get("parallel_mode"))
    return {"ok": True, "mode": state.get("mode"), "mode_name": state.get("mode_name"),
            "requires_subagent": str(state.get("mode")) == "2",
            "active_rooms": active, "room_count": len(active),
            # 【Bug1 修复】标注来源：persisted=来自 mode_state.json 落盘值（权威）；
            #   fallback=从 workflow_state.json 兼容回退；unknown=两处都没有。
            "parallel_mode": _pm or get_parallel_mode() or "unknown",
            "parallel_mode_source": ("persisted" if _pm else
                                     ("fallback:workflow_state" if get_parallel_mode() else "unknown"))}


def cmd_check():
    state = load_state()
    mode = str(state.get("mode", "1"))
    if mode != "2":
        return {"ok": True, "gate": "OPEN", "mode": mode}
    active = [k for k, v in state.get("rooms", {}).items() if v.get("active")]
    pmode = get_parallel_mode()
    if pmode == "sequential":
        if len(active) == 1:
            return {"ok": True, "gate": "OPEN", "mode": mode, "active_rooms": active}
        err = "【串行模式锁定】当前无活动房间或房间状态异常。"
        err += NL + "请确保先调用 room-end 结束上一个房间，再开启下一个。"
        err += NL + "当前 active: %s" % active
        return {"ok": False, "gate": "BLOCKED", "mode": mode, "error": err}
    if active:
        return {"ok": True, "gate": "OPEN", "mode": mode, "active_rooms": active}
    err = "【强制关卡】当前声明为大型复杂器械装配模式,必须先创建子代理小屋才能建模。"
    err += NL + "正确流程:1) 用 subagent/subagent_fork 创建小屋 -> "
    err += "2) 运行 mode_gate.py room-start <房间名> 登记 -> 3) 小屋内执行建模。"
    err += NL + "禁止在主对话中直接建模!"
    return {"ok": False, "gate": "BLOCKED", "mode": mode, "error": err}


def main():
    args = sys.argv[1:]
    # ── 【BUG-09 修复】--help / -h / help 走纯只读说明，零副作用 ──────────
    # 原实现把 --help 当"未知命令"处理（退出码 0），既不打印用法，
    #   也无法被自动化识别为调用错误。现统一给出完整命令表。
    if args and str(args[0]).lower() in ("--help", "-h", "help", "-?", "/?"):
        emit({"ok": True, "command": "help",
              "note": "本输出为纯说明，不读写任何状态文件",
              "usage": "python mode_gate.py <命令> [参数...]",
              "modes": MODE_NAMES,
              "commands": {
                  "declare <1|2|3>": "声明任务模式（2=大型复杂器械装配，启用小屋门禁）",
                  "room-start <房间名>": "登记小屋开始（编排命令，仅主对话可调）",
                  "room-end <房间名> [--force]": "小屋完成：清理+释放SW锁+关SW"
                                                   "（--force 强制释放 SW 锁，Bug-25/27）",
                  "room-fail <房间名>": "小屋失败回退，待重做",
                  "room-heartbeat <房间名>": "心跳保活（【Bug-23】已非必需，仅兼容）",
                  "room-report <房间> <阶段> <详情>": "进度旁路上报（小屋可调）",
                  "room-report-read [房间名]": "旁路读取小屋进度（只读）",
                  "room-artifact <房间> <文件> [零件]": "【Bug-16/17】登记房间产物归属",
                  "room-status": "所有房间精确状态（只读；含平台权威状态）",
                  "platform-sync <房间> <running|inactive|missing>":
                      "【Bug-23】登记 DSH 平台真实存活状态（主对话执行）",
                  "whoami <房间名> [--session-id <sid>]": "子代理自查身份",
                  "confirm-part <房间> <零件>": "C模式：用户确认后授权该零件",
                  "sw-request/sw-wait/sw-release": "SW 使用权排队/等待/释放",
                  "sw-status": "SW 锁与进程状态（只读）",
                  "stale-artifacts [目录] [--quarantine]": "扫描/隔离上一轮遗留件",
                  "residue-check [--clean] [--force]": "子对话残留体检与清理",
                  "rooms-reset": "清空房间/子代理/锁/报告",
                  "lock-doctor [--auto]": "锁体检：检测/清理失效锁",
                  "doctor [--sync|--clean|--migrate]": "状态文件多副本一致性体检"
                                                        "（--migrate 把副本更新的状态搬到权威目录，Bug-08）",
                  "status": "门禁总状态（只读）",
                  "check": "是否允许建模（只读）",
              },
              "entry_point": "命令行脚本，本环境没有对应的 DSH 工具"})
        return
    if not args:
        emit({"ok": False,
              "error": "用法: mode_gate.py <declare|room-start|room-end|room-fail|sw-request|sw-wait|sw-release|lock-doctor [--auto]|sw-proc|sw-status|status|check|set-parallel-mode <D|E>|residue-check [--clean] [--force]|stale-artifacts [--quarantine]> [...]",
              "hint": "运行 `python mode_gate.py --help` 查看全部命令",
              "modes": MODE_NAMES})
        sys.exit(1)
    cmd = args[0]
    if cmd == "declare":
        result = cmd_declare(args[1] if len(args) > 1 else "1")
    elif cmd == "room-start":
        result = cmd_room_start(args[1] if len(args) > 1 else "小屋")
    elif cmd == "room-end":
        # 【Bug-25/27】支持 --force 强制释放 SW 锁（小屋忘记 close-all 时兜底）
        _re_args = [a for a in args[1:] if not a.startswith("--")]
        _re_force = ("--force" in args) or ("-f" in args)
        result = cmd_room_end(_re_args[0] if _re_args else "小屋",
                              force=_re_force)
    elif cmd in ("room-fail", "room-restart"):
        result = cmd_room_fail(args[1] if len(args) > 1 else "小屋")
    elif cmd == "sw-request":
        result = cmd_sw_request(args[1] if len(args) > 1 else "unknown")
    elif cmd == "sw-wait":
        result = cmd_sw_wait(args[1] if len(args) > 1 else "unknown",
                             int(args[2]) if len(args) > 2 else 300)
    elif cmd == "sw-release":
        result = cmd_sw_release(args[1] if len(args) > 1 else "unknown")
    elif cmd == "lock-doctor":
        result = cmd_lock_doctor(auto=("--auto" in args))
    elif cmd == "room-heartbeat":
        result = cmd_room_heartbeat(args[1] if len(args) > 1 else "unknown")
    elif cmd == "room-heartbeat-check":
        result = cmd_room_heartbeat_check(args[1] if len(args) > 1 else "unknown")
    elif cmd == "subagent-assign":
        result = cmd_subagent_assign(args[1] if len(args) > 1 else "", args[2] if len(args) > 2 else "")
    elif cmd == "subagent-free":
        result = cmd_subagent_free(args[1] if len(args) > 1 else "unknown")
    elif cmd == "room-status":
        result = cmd_room_status()
    elif cmd == "room-artifact":
        # 【Bug-16/17】显式登记房间产物归属（取代 mtime 猜测）
        result = cmd_room_artifact(args[1] if len(args) > 1 else "",
                                   args[2] if len(args) > 2 else "",
                                   args[3] if len(args) > 3 else None)
    elif cmd == "platform-sync":
        # 【Bug-23】主对话把 list_agents 的真实状态登记进来（权威存活来源）
        result = cmd_platform_sync(args[1] if len(args) > 1 else "",
                                   args[2] if len(args) > 2 else "",
                                   " ".join(args[3:]) if len(args) > 3 else "main")
    elif cmd == "subagent-status":
        result = cmd_subagent_status()
    elif cmd == "room-report":
        result = cmd_room_report(args[1] if len(args) > 1 else "unknown",
                                 args[2] if len(args) > 2 else "unknown",
                                 " ".join(args[3:]) if len(args) > 3 else "")
    elif cmd == "room-report-read":
        result = cmd_room_report_read(args[1] if len(args) > 1 else None)
    elif cmd == "whoami":
        # 【问题3 修复】子代理自查身份（拿自己的 sessionId 去调 ask_user.py）
        # 支持: whoami <房间名> [--session-id <sid>] [--wait <秒>] [--interval <秒>]
        _room = None
        _sid = None
        _wait = 0
        _intv = 2.0
        _i = 1
        while _i < len(args):
            _a = args[_i]
            if _a in ("--session-id", "--session_id", "--child", "-s"):
                _sid = args[_i + 1] if _i + 1 < len(args) else None
                _i += 2
            elif _a in ("--wait", "-w"):
                try:
                    _wait = float(args[_i + 1])
                except Exception:
                    _wait = 30
                _i += 2
            elif _a == "--interval":
                try:
                    _intv = float(args[_i + 1])
                except Exception:
                    _intv = 2.0
                _i += 2
            elif _room is None and not _a.startswith("-"):
                _room = _a
                _i += 1
            else:
                _i += 1
        result = cmd_whoami(_room, session_id=_sid, wait_sec=_wait, interval=_intv)
    elif cmd == "confirm-part":
        # 【Bug1 修复】由用户确认后授权某零件，之后 params_confirmed 才会被接受
        result = cmd_confirm_part(args[1] if len(args) > 1 else "",
                                  args[2] if len(args) > 2 else "",
                                  args[3] if len(args) > 3 else "user")
    elif cmd in ("residue-check", "residue"):
        # 【残留检测】上一轮对话的子对话/房间/门禁残留体检与清理
        # 用法: mode_gate.py residue-check [--clean] [--force] [--quiet]
        result = cmd_residue_check(clean=("--clean" in args),
                                   force=("--force" in args),
                                   quiet=("--quiet" in args))
    elif cmd == "rooms-reset":
        # 【Bug1 修复】支持 --purge-pmode 显式清除并行模式落盘值
        _purge = "--purge-pmode" in args[1:]
        result = cmd_rooms_reset(keep_pmode=not _purge)
    elif cmd == "doctor":
        # 【双份状态修复】体检/对齐状态文件副本
        result = cmd_doctor(sync=("--sync" in args), clean=("--clean" in args),
                            migrate=("--migrate" in args))
    elif cmd == "stale-artifacts":
        # 【Bug4 修复】扫描工作目录，区分本轮产物与上一轮遗留件
        # 用法: mode_gate.py stale-artifacts [工作目录] [--epoch <ts>] [--quarantine] [--dry-run]
        _wd = None
        _ep = None
        for _i, _a in enumerate(args[1:], 1):
            if _a == "--epoch" and _i + 1 < len(args):
                try:
                    _ep = float(args[_i + 1])
                except Exception:
                    _ep = None
            elif not _a.startswith("-") and _wd is None:
                _wd = _a
        if _wd is None:
            _st = load_state()
            _wd = _st.get("work_dir") or os.getcwd()
        # ── 【BUG-10 修复】--quarantine 把遗留件移入隔离区，根治多轮堆积 ──
        if "--quarantine" in args or "--isolate" in args:
            result = quarantine_legacy_artifacts(_wd, _ep,
                                                 dry_run=("--dry-run" in args))
        else:
            result = scan_legacy_artifacts(_wd, _ep)
    elif cmd == "set-parallel-mode":
        # 【Bug1 修复】主对话在 workflow_gate.py select 阶段调用，
        #   把并行策略 D/E 持久化进 mode_state.json（权威落盘）。
        _val = args[1] if len(args) > 1 else None
        _set = set_parallel_mode(_val)
        result = ({"ok": True, "parallel_mode": _set,
                   "source": "persisted to mode_state.json",
                   "hint": "已落盘，后续 sw-status/check 读磁盘而非实时推断"}
                  if _set else
                  {"ok": False, "error": "参数必须为 D/E/parallel/sequential",
                   "got": _val})
    elif cmd == "sw-monitor-start":
        result = cmd_sw_monitor_start()
    elif cmd == "sw-monitor-stop":
        result = cmd_sw_monitor_stop()
    elif cmd == "sw-monitor-status":
        result = cmd_sw_monitor_status()
    elif cmd == "sw-monitor-bg":
        result = cmd_sw_monitor_bg()
    elif cmd == "_monitor_bg_impl":
        _monitor_bg_impl()
    elif cmd == "sw-proc":
        result = cmd_sw_proc()
    elif cmd == "sw-status":
        result = cmd_sw_status()
    elif cmd == "status":
        result = cmd_status()
    elif cmd == "check":
        result = cmd_check()
    else:
        result = {"ok": False, "error": "未知命令: " + cmd}
    emit(result)
    # [门禁] 防线拦截/校验异常必须以非零退出码结束：否则自动化脚本
    #   （pwsh $? / 子代理判定）会把"被拦"误读成"成功"，继续往下走。
    try:
        if isinstance(result, dict) and result.get("ok") is False:
            _g = str(result.get("gate") or "")
            if _g in ("DEFENSE_REQUIRED", "DEFENSE_ERROR"):
                sys.exit(1)
    except SystemExit:
        raise
    except Exception:
        pass


if __name__ == "__main__":
    main()