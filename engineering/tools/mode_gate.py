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
# ── 【P2 清理】已删除两个【定义后从未被引用】的死常量 ────────────────────
#   SW_ORPHAN_TIMEOUT = 90    —— 孤儿锁回收实际由 _lock_owner_alive 的 5 条
#                                判据 + _purge_orphan_lock 驱动，不读该常量；
#   SW_SW_IDLE_TIMEOUT = 600  —— SW 进程空闲清理实际由 SW_MAX_HOLD /
#                                SW_CMD_HOLD 控制，该常量全文零引用。
#   留着会误导调参（"改了阈值却没生效"），故直接移除。
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

# 兼容别名：下游有两处按不带前缀的名字引用（曾 NameError）。
SW_MONITOR_INTERVAL = _SW_MONITOR_INTERVAL

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


# ══ 【阶段 C · 状态可信化】插件自动维护的子代理注册表 ═══════════════════
# 背景（用户会话实测）：主对话分不清子代理死活 —— 它手动跑 while(true)
#   platform-sync running 造假心跳，subagent-assign 从没调用，recover 把还在跑
#   的房间判成"僵尸残留"，紧接着 reset 把三个正在建模的子代理全杀了。
# 根因：存活结论靠【主对话猜/手写】，而不是平台事实。
# 修复：宿主插件 dsh-engineering-ui 在 agent/created、agent/turn-stopping、
#   agent/disposed 时【自动】维护 <STATE_DIR>/platform_agents.json：
#     {"updatedAt": ..., "source": "dsh-engineering-ui",
#      "agents": [{id, label, parentId, createdAt, status, lastEventAt}]}
#   这份文件是平台事实，主对话不需要（也不允许）再手工喂假心跳。
PLATFORM_AGENTS_FILE = os.path.join(STATE_DIR, "platform_agents.json")


def _parse_ts_any(value):
    """把插件写的时间戳解析为 epoch 秒（兼容 ISO 字符串 / 秒 / 毫秒）。

    为什么必须兼容：插件侧可能写 ISO（new Date().toISOString()），旧版本可能
      写毫秒整数。任何解析失败都返回 None —— 宁可"年龄未知"，也不猜成"很旧"。
    """
    if value is None:
        return None
    try:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            v = float(value)
            return v / 1000.0 if v > 1e11 else v
        s = str(value).strip()
        if not s:
            return None
        try:
            v = float(s)
            return v / 1000.0 if v > 1e11 else v
        except Exception:
            pass
        import datetime as _dt
        import re as _re
        _s = s.replace("Z", "+00:00")
        _s = _re.sub(r"(\.\d{6})\d+", r"\1", _s)   # 截断超出微秒的精度
        return _dt.datetime.fromisoformat(_s).timestamp()
    except Exception:
        return None


def _load_plugin_registry():
    """读取插件自动维护的子代理注册表（失败返回 {}，绝不抛异常）。"""
    try:
        if not os.path.exists(PLATFORM_AGENTS_FILE):
            return {}
        with open(PLATFORM_AGENTS_FILE, "r", encoding="utf-8-sig") as f:
            d = json.load(f)
        if not isinstance(d, dict):
            return {}
        if not isinstance(d.get("agents"), list):
            d["agents"] = []
        return d
    except Exception:
        return {}


def _plugin_agents():
    """注册表里有效的子代理记录（只保留带 id 的 dict 项）。"""
    try:
        return [a for a in (_load_plugin_registry().get("agents") or [])
                if isinstance(a, dict) and a.get("id")]
    except Exception:
        return []


def _plugin_run_id():
    """本次 DSH 运行的标识（插件写进注册表；读不到返回 None）。

    【2026-10-29 G04】DSH 崩溃重启后 runId 必然变化。mode_gate 把这值记进
    platform_status.json 的 _meta.run_id；下次读到不同的 runId 就知道
    "上一轮 fork 的所有子代理都已失联"，可以整批判 detached，
    而不是逐个房间靠 id 去注册表里碰运气。
    """
    try:
        return str((_load_plugin_registry() or {}).get("runId") or "").strip() or None
    except Exception:
        return None


def _remember_run_id():
    """把当前 runId 记进 platform_status.json（只读命令也可安全调用）。"""
    try:
        _rid = _plugin_run_id()
        if not _rid:
            return None
        _d = _load_platform_status()
        _meta = _d.get("_meta") if isinstance(_d.get("_meta"), dict) else {}
        if _meta.get("run_id") != _rid:
            _meta["run_id"] = _rid
            _meta["run_id_seen_at"] = time.time()
            _d["_meta"] = _meta
            _save_platform_status(_d)
        return _rid
    except Exception:
        return None


def _run_id_changed():
    """本次 runId 与上次记录的是否不同（= DSH 重启过）。

    返回 (changed: bool, prev_run_id, current_run_id)
    """
    try:
        _cur = _plugin_run_id()
        if not _cur:
            return False, None, None
        _meta = (_load_platform_status() or {}).get("_meta") or {}
        _prev = _meta.get("run_id") if isinstance(_meta, dict) else None
        if not _prev:
            _remember_run_id()
            return False, None, _cur
        return (str(_prev) != str(_cur)), _prev, _cur
    except Exception:
        return False, None, None


def _plugin_agent_for_room(room, sub_rec=None):
    """把房间匹配到插件注册表里的子代理记录（不猜：多条匹配返回 None）。

    匹配顺序（契约见 docs/redesign-prime-and-status.md 二·C）：
      ① mode_state.subagents[room].id 与 agents[].id 【精确相等】—— 最可靠；
      ② label 包含房间名（或房间名包含 label）且【唯一】命中；
      ③ 都没命中 / 多条命中 → None（宁可报 unbound，也不瞎认一个 id）。
    """
    agents = _plugin_agents()
    if not agents:
        return None
    _sid = None
    try:
        if isinstance(sub_rec, dict):
            _sid = sub_rec.get("id")
        elif isinstance(sub_rec, str):
            _sid = sub_rec
    except Exception:
        _sid = None
    _sid = str(_sid or "").strip()
    if _sid:
        for a in agents:
            if str(a.get("id") or "").strip() == _sid:
                return a
    _room = str(room or "").strip()
    if not _room:
        return None
    hits = []
    for a in agents:
        _label = str(a.get("label") or "").strip()
        if not _label:
            continue
        if (_room in _label) or (_label in _room):
            hits.append(a)
    if len(hits) == 1:
        return hits[0]
    return None


# ══ 【2026-10-29 · 重写】平台存活判据：主进程脉冲（唯一权威）═════════════
#
# 为什么推倒重来（会话 ceac3fff 实测事故）：
#   lead 用 list_agents 看不到 fork 出来的子代理（它们不是 Team member），
#   于是误判"已死"，在 3 分钟内连开 3 个总计子代理；三个同时存活、
#   互相 analyst-bind --force 抢身份、各自提问 → 用户看到重复选项。
#
# 旧实现的三层判据全都【可以被绕过或误读】：
#   ① lastEventAt（子代理写）—— 子代理一忙就停写，看着像死了；
#   ② 平台 status 字段 —— 事件不来时无法区分"没事件"与"已死"；
#   ③ 手动 platform-sync —— 靠主对话自觉，还容易被写成假心跳。
#
# 新判据（组合起来接近 100%，且不依赖任何外部环境）：
#   ① pulse.seq 单调递增且新鲜 —— 由【DSH 主进程的 setInterval】写，
#      与子代理在干什么无关。子代理跑 SW/等锁/发呆，脉冲照跳。
#      脉冲停 = DSH 进程没了 = 所有子代理也都没了 → 判死是正确的。
#   ② agents[] 里仍有该 id 且 status != disposed —— agent 对象还在。
#   两条同时成立 → 活着。任一不成立 → 才允许判死。
#
# platform_status.json 保留为【人工覆盖】（排障时强制标记），不作主判据。
PULSE_STALE_SEC = 35          # 与插件 PULSE_STALE_MS 对齐（3.5 拍 x 10s）


def _pulse_state():
    """读主进程脉冲。返回 {alive, seq, age_sec, reason}。

    alive=True 表示"写这份注册表的 DSH 进程还活着且在跳"。
    """
    out = {"alive": False, "seq": None, "age_sec": None, "reason": "no_registry"}
    reg = _load_plugin_registry()
    if not reg:
        return out
    pulse = reg.get("pulse") if isinstance(reg.get("pulse"), dict) else {}
    if not pulse:
        out["reason"] = "no_pulse_field"
        _ts = _parse_ts_any(reg.get("updatedAt"))
        if _ts:
            _age = time.time() - _ts
            out["age_sec"] = round(_age, 1)
            if _age <= PULSE_STALE_SEC:
                out["alive"] = True
                out["reason"] = "legacy_updatedAt"
        return out
    _ts = _parse_ts_any(pulse.get("atMs")) or _parse_ts_any(pulse.get("at"))
    try:
        out["seq"] = int(pulse.get("seq"))
    except Exception:
        out["seq"] = None
    if not _ts:
        out["reason"] = "pulse_no_ts"
        return out
    _age = time.time() - _ts
    out["age_sec"] = round(_age, 1)
    if _age <= PULSE_STALE_SEC:
        out["alive"] = True
        out["reason"] = "pulse_fresh"
    else:
        out["reason"] = ("pulse_stale(%.0fs > %ds) —— DSH 进程可能已停，"
                         "此时所有子代理同样已消失"
                         % (_age, PULSE_STALE_SEC))
    return out


def _platform_liveness(room, sub_rec=None):
    """取房间的平台侧存活结论（返回值保持旧契约 (status|None, age_sec|None)）。

    Returns:
      "running"  -> 平台确认仍在跑（权威：禁止判死/禁止重启）
      "inactive" -> 平台确认已停止/已释放（才允许判死）
      "missing"  -> 平台侧无此子代理（登记丢失）
      None       -> 无平台事实（证据不足，退回证据链，绝不当成"死亡"）

    判定顺序：
      ① 人工覆盖 platform_status.json（PLATFORM_FRESH_SEC 内）—— 排障用；
      ② 脉冲活着 + agent 在册且非 disposed -> "running"；
      ③ 脉冲活着 + agent 在册但 disposed  -> "inactive"；
      ④ 脉冲活着 + 注册表里确实没有该 agent -> "missing"；
      ⑤ 脉冲已停（DSH 进程没了）-> "inactive"（此时子代理确实也没了）。
    """
    rec = _load_platform_status().get(room)
    if isinstance(rec, dict):
        try:
            _ts = float(rec.get("ts") or 0)
        except Exception:
            _ts = 0.0
        if _ts and (time.time() - _ts) <= PLATFORM_FRESH_SEC:
            return str(rec.get("status") or "").lower() or None, time.time() - _ts

    pulse = _pulse_state()
    if not pulse.get("alive"):
        # ── 【关键区分】"没有 pulse 字段" != "pulse 过期" ──────────────────
        #   · no_registry / no_pulse_field / pulse_no_ts：
        #       插件没装、或还没重启到带 pulse 的版本 —— 这是【判据缺失】，
        #       不是"子代理死了"。此时绝不能返回 inactive（那会把所有活房间
        #       判死）。退回"agent 在册即 running"的弱判据。
        #   · pulse_stale：pulse 确实存在但停了 = DSH 进程没了，
        #       此时所有子代理也都没了 → 返回 inactive 是【正确】的。
        # 注意：reason 是带参数的完整句子（"pulse_stale(999s > 35s) ..."），
        # 必须用 startswith 判断，不能用 ==（踩过一次，导致该判死的没判死）。
        if str(pulse.get("reason") or "").startswith("pulse_stale"):
            return "inactive", pulse.get("age_sec")
        # 判据缺失 → 弱判据（agent 在册且未 disposed 就算活着）
        a_weak = _plugin_agent_for_room(room, sub_rec)
        if a_weak is not None:
            _st_w = str(a_weak.get("status") or "").strip().lower()
            if _st_w == "disposed":
                return "inactive", pulse.get("age_sec")
            return "running", pulse.get("age_sec")
        return None, pulse.get("age_sec")

    a = _plugin_agent_for_room(room, sub_rec)
    if a is None:
        _agents = _plugin_agents()
        if not _agents:
            return None, pulse.get("age_sec")
        return "missing", pulse.get("age_sec")
    _st = str(a.get("status") or "").strip().lower()
    if _st == "disposed":
        return "inactive", pulse.get("age_sec")
    return "running", pulse.get("age_sec")


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
    """返回房间心跳文件路径（按任务隔离，旧心跳不会让新房间"看起来还活着"）。"""
    try:
        _wf = os.path.join(STATE_DIR, "workflow_state.json")
        _tid = None
        if os.path.exists(_wf):
            with open(_wf, "r", encoding="utf-8") as _f:
                _tid = (json.load(_f) or {}).get("task_id")
        _base = os.path.join(HEARTBEAT_DIR, str(_tid)) if _tid else HEARTBEAT_DIR
    except Exception:
        _base = HEARTBEAT_DIR
    os.makedirs(_base, exist_ok=True)
    safe = "".join(c for c in room if c.isalnum() or c in " _-").strip()
    return os.path.join(_base, safe + ".heartbeat")


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


def cmd_whoami_self_bind(room=None):
    r"""BUG-9: whoami 时顺手补上 subagent 绑定。

    room-start 本身不记录哪个子代理占着这个房间；主对话若忘了
    subagent-assign，room-status 就显示 subagent=null，小屋上报可能失败。

    现在两条路并存：显式 assign 优先；没有绑定时 whoami 从
    DSH_SESSION_ID / DSH_SESSION_JSONL 反解并自动绑上；
    已有绑定则不覆盖（避免抢掉主对话的明确指派）。
    """
    _room = str(room or os.environ.get("DSH_ROOM") or "").strip()
    if not _room:
        return {"ok": False, "error": "缺少房间名（也没有 DSH_ROOM）"}
    state = load_state()
    rooms = state.get("rooms") or {}
    if _room not in rooms:
        return {"ok": False, "room": _room,
                "error": "房间尚未 room-start，无法自查绑定",
                "registered_rooms": sorted(rooms.keys())}
    _sid = _register_self_identity(_room)
    if not _sid:
        return {"ok": False, "room": _room,
                "error": "无法从环境变量反解 sessionId",
                "hint": "请在 DSH 会话内运行，而不是裸终端"}
    subs = state.setdefault("subagents", {})
    _exist = subs.get(_room)
    if isinstance(_exist, dict) and _exist.get("id"):
        return {"ok": True, "room": _room,
                "subagent_id": _exist.get("id"),
                "action": "kept",
                "note": "已由主对话绑定，本命令不覆盖"}
    subs[_room] = {"id": _sid, "assigned_at": time.time(),
                   "assigned_by": "whoami-self-bind"}
    save_state(state)
    return {"ok": True, "room": _room, "subagent_id": _sid,
            "action": "bound",
            "note": "已由 whoami 自动绑定（BUG-9）"}



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
    # ── 【阶段 C】新增 unbound：平台确认存活、但房间没绑 subagent ──────────
    # 用户会话实测的误杀链：subagent-assign 从没调用 → 房间"没有 subagent 登记"
    #   → 旧逻辑归入 zombie → recover 判"僵尸残留" → reset 把正在建模的小屋杀了。
    # 现在这类房间单列 unbound，并明确写"平台侧仍存活，禁止 reset；补
    #   subagent-assign 即可"。它们【不参与清理】，也不计入 has_residue。
    unbound = []

    # ── 1) 房间维度分类 ─────────────────────────────────────────────────
    for name, info in rooms.items():
        info = info or {}
        if info.get("ended_at") or info.get("failed_at"):
            # 已收尾：不算"活着的子对话"，但 subagent 映射可能需要清
            continue
        if not info.get("active"):
            continue
        st = _room_status_of(name, info, subs, state=state)
        status = st.get("status")
        # ── 【阶段 C】看板判定优先：dead 只在平台【明确】说不在时出现 ──────
        try:
            _b = _board_entry(name, info, subs, state=state, st=st)
            _bstate = str(_b.get("state") or "")
        except Exception:
            _b = {}
            _bstate = ""
        rec = (
            "房间[%s] 状态=%s 平台=%s 心跳=%ss 报告=%ss 产物=%s"
            % (name, _bstate or status, st.get("platform_status"),
               st.get("heartbeat_age_sec"), st.get("report_age_sec"),
               bool((st.get("evidence") or {}).get("disk_artifact")))
        )
        if _bstate == "unbound" or (not _bstate and status == "running"
                                    and not st.get("subagent_id")):
            unbound.append({
                "room": name, "status": status, "detail": rec,
                "platform_status": st.get("platform_status"),
                "note": ("平台侧仍存活，禁止 reset；补 subagent-assign 即可："
                         "python mode_gate.py subagent-assign %s <subagent_fork 返回的 id>"
                         % name),
                "next_action": _b.get("next_action") or UNBOUND_NEXT_ACTION,
            })
        elif _bstate in ("running", "starting", "waiting_sw", "idle"):
            # waiting_sw / idle 同样是【活着的子对话】：禁止判死、禁止清理
            alive.append({"room": name, "status": status, "state": _bstate,
                          "detail": rec})
        elif _bstate == "dead":
            stale.append({"room": name, "status": status, "state": _bstate,
                          "detail": rec})
        elif _bstate == "done":
            continue
        elif _bstate == "failed":
            continue
        elif _bstate:
            zombie.append({"room": name, "status": status, "state": _bstate,
                           "detail": rec})
        elif status in ("running", "starting", "running_unverified"):
            alive.append({"room": name, "status": status, "detail": rec})
        elif status == "stale":
            stale.append({"room": name, "status": status, "detail": rec})
        else:
            # 没有看板结论时的最后兜底：仍按旧规则（保守：不动活着的）
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
            # unbound 的房间【也是活着的】—— 只要平台说它还在跑，门禁推进态
            #   就不能算"没有任何活着的房间"（否则会把正在进行的工作流清成残留）
            no_alive_room = (len(alive) == 0 and len(unbound) == 0)
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
        # 【阶段 C】unbound = 平台存活但未绑定 subagent：不是残留，禁止 reset
        "unbound": unbound,
        "zombie": zombie,
        "stale": stale,
        "orphan": orphan,
        "gate_residue": gate_residue,
        "counts": {"alive": len(alive), "unbound": len(unbound),
                   "zombie": len(zombie), "stale": len(stale),
                   "orphan": len(orphan)},
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
        # 4b-2) unbound 房间【永不清理】：平台说它还活着，清掉就是误杀。
        if unbound:
            actions.append("保留 %d 个 unbound 房间（平台存活但未绑定 subagent，"
                           "禁止 reset；请补 subagent-assign）: %s"
                           % (len(unbound),
                              ", ".join(x["room"] for x in unbound)))

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
                            "unbound": len(result.get("unbound") or []),
                            "zombie": 0, "stale": 0, "orphan": 0}
        if force:
            result["alive"] = []
            result["counts"]["alive"] = 0

    # ── 5) 提示文案 ────────────────────────────────────────────────────
    # 【阶段 C】unbound 的文案必须与"残留"区分开：平台说它还活着，
    #   正确动作是补 subagent-assign，【不是】reset/清理。
    _unbound_hint = (
        ("；另有 %d 个 unbound 房间（平台侧仍存活，禁止 reset；补 subagent-assign 即可）: %s"
         % (len(unbound), ", ".join(x["room"] for x in unbound))) if unbound else "")
    if not quiet:
        if result.get("cleaned"):
            # 清理后按"实际结果"给文案，而不是按清理前的分类
            _ci = result.get("cleaned_items") or {}
            _kept = len(result.get("alive") or [])
            result["hint"] = ("清理完成。清除项：僵尸 %d / 陈旧 %d / 孤儿 %d / 门禁残留 %s%s%s。"
                              % (len(_ci.get("zombie") or []), len(_ci.get("stale") or []),
                                 len(_ci.get("orphan") or []),
                                 ("step=%s" % _ci["gate_residue_step"])
                                 if _ci.get("gate_residue_step") else "无",
                                 ("；保留 %d 个活动房间（未加 --force）" % _kept)
                                 if _kept else "",
                                 _unbound_hint))
        elif not has_residue and not alive and not unbound:
            result["hint"] = "干净：未检出任何子对话/任务残留。"
        elif not has_residue and alive:
            result["hint"] = ("未检出残留，但有 %d 个活动房间正在运行 —— "
                              "新任务请等其结束或显式 --force。%s" % (len(alive), _unbound_hint))
        elif not has_residue and unbound:
            result["hint"] = ("未检出残留。%s" % (_unbound_hint.lstrip("；"),))
        else:
            result["hint"] = ("检出残留：僵尸 %d / 陈旧 %d / 孤儿 %d / 门禁残留 %s%s。"
                              "执行 mode_gate.py residue-check --clean 自动清理（unbound 不会被清）。"
                              % (len(zombie), len(stale), len(orphan),
                                 "有" if gate_residue else "无", _unbound_hint))
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
    """推断工作目录（产物所在处）。优先 state.work_dir，其次桌面 test，最后 cwd。

    ── 【BUG-观察-1 修复】补读 workflow_state.json 的 work_dir ──────────────
    原缺陷：本函数只读 **mode_state.json** 的 `work_dir` 键，但该键实际上是
      **workflow_gate.prepare_task_workdir() 写进 workflow_state.json 的**
      （mode_state 里从来没有这个键）→ 第一个分支永远不成立 →
      一律落到 `Desktop\\test` 兜底。
      这正是"交付目录双轨"的第二条来源：workflow_gate 认为产物在
      `<root>\\<task_id>`，而 mode_gate 却把 `Desktop\\test`（父目录）当工作目录，
      两处对同一批零件指向不同位置。

    修复：读不到 mode_state 的 work_dir 时，继续读 workflow_state.json 的
      work_dir（与 mode_gate.py 中既有的跨文件读取口径一致），
      保证两个门禁对"产物在哪"给出同一答案。
    """
    try:
        st = load_state()
        wd = st.get("work_dir")
        if wd and os.path.isdir(wd):
            return wd
    except Exception:
        pass
    # ── 【BUG-观察-1 修复】回退读 workflow_state.json（work_dir 的真正落点）──
    try:
        import _store as _st
        _p = _st.state_path("workflow_state.json")
        if os.path.isfile(_p):
            with open(_p, "r", encoding="utf-8-sig") as _f:
                _wf = json.load(_f) or {}
            _wd2 = str(_wf.get("work_dir") or "").strip()
            if _wd2 and os.path.isdir(_wd2):
                return _wd2
    except Exception:
        pass
    # ── 【换机事故修复】不再无条件回落到 <桌面>\test / <桌面> ──────────────
    # 旧实现：读不到 work_dir 就返回桌面 test 或桌面本身。换一台电脑后，
    #   这两个目录要么不存在（于是 getcwd）、要么就是【别人的桌面】——
    #   扫描产物会把与本任务无关的桌面文件也算进来。
    # 新实现：交回 settings.delivery_root()（可配置、跨机忽略、不凭空建 test）。
    try:
        import settings as _settings
        _root, _src = _settings.delivery_root()
        if _root and os.path.isdir(_root):
            return _root
        if _root:
            try:
                os.makedirs(_root, exist_ok=True)
                return _root
            except Exception:
                pass
    except Exception:
        pass
    home = os.path.expanduser("~")
    for c in (os.path.join(home, "Desktop", "test"),):
        # 仅当【本来就存在】时沿用（不再把"桌面存在"当成理由去用桌面）
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
def _registry_file():
    """产物登记表按【任务】隔离 —— 新任务不得看到上一局的零件清单。"""
    try:
        _wf = os.path.join(STATE_DIR, "workflow_state.json")
        if os.path.exists(_wf):
            with open(_wf, "r", encoding="utf-8") as _f:
                _tid = (json.load(_f) or {}).get("task_id")
            if _tid:
                return os.path.join(STATE_DIR, "artifacts_registry.%s.json" % _tid)
    except Exception:
        pass
    return os.path.join(STATE_DIR, "artifacts_registry.json")

ARTIFACT_REGISTRY_FILE = _registry_file()


def _load_artifact_registry():
    """读取产物归属登记表 {room: [{path, name, ts, at}]}（失败返回 {}）。"""
    try:
        _rf = _registry_file()
        if os.path.exists(_rf):
            with open(_rf, "r", encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
    except Exception:
        pass
    return {}


def _save_artifact_registry(data):
    try:
        with open(_registry_file(), "w", encoding="utf-8") as f:
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
            "file": _registry_file(),
            "note": ("已登记本房间产物归属（大小写不敏感去重，Bug-09）；"
                     "room-status 的 artifact 判定将以登记表为准（Bug-16/17）。")}


def _archive_room_artifacts(room):
    """【BUG-05 修复】把该房间上一波登记的产物移到归档目录。

    缺陷（2026-10-29 竞速小车实测）：
      room-fail 后重开房间，返回 fresh_reason=auto_fresh_after_fail，
      但 parts/ 里旧的 .SLDPRT 仍然在。新小屋重做时会覆盖，
      判断失误就漏建/重复建，而且事后分不清"这批件属于哪一波"。

    做法：
      · 只归档【本房间已登记归属】的文件（artifacts_registry 里 room 匹配的那些），
        绝不按文件名前缀猜 —— 猜错会搬走别的房间的件；
      · 连同同名伴生文件一起搬（.material.json / .origin.json /
        .sw_internal.json / _preview.png / _iso.png），否则凭据与模型会脱节；
      · 目标：<工作目录>/parts/_archive/<房间>_<时间戳>/；
      · 任何失败都只返回警告，绝不让 room-start 失败
        （归档是整理，不是正确性前提）。

    Returns: {"ok": bool, "moved": [...], "target": str, "note"|"warning": str}
    """
    out = {"ok": True, "moved": [], "target": None}
    try:
        _items = _registered_artifacts(room)
        _files = [it["path"] for it in _items
                  if it.get("path") and it.get("exists")]
        if not _files:
            out["note"] = "该房间没有已登记的产物，无需归档"
            return out
        _work = _default_work_dir()
        _stamp = time.strftime("%Y%m%d-%H%M%S")
        _dst = os.path.join(str(_work), "parts", "_archive",
                            "%s_%s" % (_safe_room_name(room), _stamp))
        os.makedirs(_dst, exist_ok=True)
        out["target"] = _dst
        _suffixes = (".material.json", ".origin.json", "", "_preview.png", "_iso.png")
        for _p in _files:
            _dir = os.path.dirname(_p)
            _base = os.path.basename(_p)
            _stem = _base
            for _ext in (".SLDPRT", ".sldprt", ".SLDASM", ".sldasm"):
                if _stem.lower().endswith(_ext.lower()):
                    _stem = _stem[:-len(_ext)]
                    break
            _cands = [_base, _stem + ".SLDPRT.material.json",
                      _stem + ".sldprt.material.json",
                      _stem + ".SLDPRT.origin.json",
                      _stem + ".sldprt.origin.json",
                      _stem + ".sldprt.material.json.sw_internal.json",
                      _stem + "_preview.png", _stem + "_iso.png",
                      _stem + ".SLDPRT.material.json.sw_internal.json"]
            for _c in _cands:
                _src = os.path.join(_dir, _c)
                if not os.path.exists(_src):
                    continue
                _tgt = os.path.join(_dst, _c)
                try:
                    if os.path.exists(_tgt):
                        os.remove(_tgt)
                    # ── 【R8 / B-20·B-25 修复】搬走前先记下指纹 ──────────────
                    # 事后回填时【必须】能判断"哪份才是最新/最该回填的"，
                    # 否则就会出现实测中的事故：三路 fork 互相搬文件，
                    # "原样恢复"把已过门的 L200/L230 覆盖成最旧的 L114/L109，
                    # 全盘无第三份副本，差点把错误几何带进总装和 12 张工程图。
                    _meta = _file_fingerprint(_src)
                    import shutil as _shutil
                    _shutil.move(_src, _tgt)
                    out["moved"].append(_c)
                    out.setdefault("files", []).append(dict(_meta, archived_as=_c))
                except Exception:
                    continue
        # ── 【R8】落一份清单：归档里到底有什么、各自什么指纹、什么时候回的 ──
        # 没有这份清单，回填就只能靠人肉猜（B-25 里转台轴就是这么丢的）。
        try:
            _manifest = {
                "schema": "dsh-engineering/archive-manifest@1",
                "room": str(room),
                "archived_at": time.time(),
                "archived_at_str": time.strftime("%Y-%m-%d %H:%M:%S"),
                "reason": "room-start fresh 重开（上一波产物归档）",
                "files": out.get("files") or [],
            }
            with open(os.path.join(_dst, ARCHIVE_MANIFEST_NAME), "w",
                      encoding="utf-8") as _mf:
                json.dump(_manifest, _mf, ensure_ascii=False, indent=2)
            out["manifest"] = os.path.join(_dst, ARCHIVE_MANIFEST_NAME)
        except Exception as _e_mf:
            out.setdefault("warnings", []).append("归档清单写入失败: %r" % (_e_mf,))
        out["note"] = ("已把该房间上一波的 %d 个文件移到 %s"
                       "（新一波从干净目录开始，避免新旧混淆；"
                       "清单见 %s，可用 archive-list / archive-restore 查看与回填）"
                       % (len(out["moved"]), _dst, ARCHIVE_MANIFEST_NAME))
    except Exception as _e:
        out["ok"] = False
        out["warning"] = "归档旧产物失败（不影响 room-start）：%r" % (_e,)
    return out


ARCHIVE_MANIFEST_NAME = "_archive_manifest.json"


def _file_fingerprint(p):
    """文件指纹（大小 / mtime / sha1 前 16 位）—— 归档与回填的仲裁依据。

    实测教训（B-20）：三路 fork 互相搬文件，"原样恢复"把已过门的
      L200/L230 覆盖成最旧的 L114/L109，全盘无第三份副本。
      没有指纹就无法回答"这两份到底哪份是最新的/过门的"。
    """
    out = {"name": os.path.basename(str(p)), "path": str(p)}
    try:
        st = os.stat(p)
        out["size"] = st.st_size
        out["mtime"] = st.st_mtime
        out["mtime_str"] = time.strftime("%Y-%m-%d %H:%M:%S",
                                         time.localtime(st.st_mtime))
    except Exception as e:
        out["stat_error"] = repr(e)
        return out
    try:
        import hashlib as _hl
        h = _hl.sha1()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 256), b""):
                h.update(chunk)
        out["sha1_16"] = h.hexdigest()[:16]
    except Exception as e:
        out["hash_error"] = repr(e)
    return out


def _archive_root(room=None):
    """归档根目录 <work_dir>/parts/_archive。"""
    try:
        _work = _default_work_dir()
    except Exception:
        _work = os.getcwd()
    base = os.path.join(str(_work), "parts", "_archive")
    if room:
        return base, str(room)
    return base, None


def _archive_dirs(room):
    """该房间的全部归档批次（新→旧）。"""
    base, _ = _archive_root()
    out = []
    try:
        if not os.path.isdir(base):
            return out
        _pfx = _safe_room_name(room) + "_"
        for d in os.listdir(base):
            if not d.startswith(_pfx):
                continue
            full = os.path.join(base, d)
            if not os.path.isdir(full):
                continue
            try:
                mt = os.path.getmtime(full)
            except Exception:
                mt = 0.0
            out.append({"dir": full, "name": d, "mtime": mt})
    except Exception:
        return out
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out


def _load_archive_manifest(d):
    p = os.path.join(d, ARCHIVE_MANIFEST_NAME)
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def cmd_archive_list(room=None, as_json=True):
    """列出归档批次及其指纹（只读）。room 为空时列出所有房间的归档。"""
    base, _ = _archive_root()
    batches = []
    try:
        names = ([room] if room else
                 sorted({d.rsplit("_", 2)[0] for d in (os.listdir(base) or [])
                         if os.path.isdir(os.path.join(base, d))})
                 if os.path.isdir(base) else [])
    except Exception:
        names = [room] if room else []
    for _rn in names:
        for b in _archive_dirs(_rn):
            mf = _load_archive_manifest(b["dir"])
            batches.append({
                "room": _rn,
                "batch": b["name"],
                "dir": b["dir"],
                "mtime": b["mtime"],
                "mtime_str": time.strftime("%Y-%m-%d %H:%M:%S",
                                           time.localtime(b["mtime"])),
                "has_manifest": bool(mf),
                "files": (mf or {}).get("files") or [],
                "archived_at_str": (mf or {}).get("archived_at_str"),
            })
    out = {"ok": True, "archive_root": base, "batches": batches,
           "count": len(batches)}
    if not batches:
        out["note"] = "没有任何归档批次（parts/_archive 为空或不存在）"
    return out


def cmd_archive_restore(room, batch=None, part=None, dry_run=False,
                        force=False, as_json=True):
    """【R8 / B-25 修复】按指纹仲裁把归档产物回填到 parts/。

    为什么要"仲裁"而不是"原样恢复"：
      归档副本【不一定是最新版】。实测中把已过门（看图门 PASS、尺寸正确）的
      新版覆盖成了最旧的版本，且全盘没有第三份副本，唯一暴露信号是
      副轴看图门 passed=false —— 错误几何差点静默进总装与 12 张工程图。

    仲裁规则（宁可不动，也不覆盖更新的东西）：
      ① 默认只回填【目标不存在】的文件（安全）；
      ② --force 才允许覆盖，且覆盖前把现有文件备份成 <name>.bak-restore-<时间戳>；
      ③ 若归档件的 mtime 比现有目标【更旧】，默认拒绝并说明（--force 可越过）；
      ④ 每一步都报告指纹，可审计。

    Returns: {ok, room, batch, restored:[], skipped:[], backup_dir, notes}
    """
    out = {"ok": True, "room": str(room), "batch": batch, "part": part,
           "dry_run": bool(dry_run), "restored": [], "skipped": [],
           "backup_dir": None, "notes": []}
    batches = _archive_dirs(room)
    if not batches:
        out["ok"] = False
        out["error"] = "房间 [%s] 没有任何归档批次" % (room,)
        return out
    if batch:
        batches = [b for b in batches if b["name"] == batch]
        if not batches:
            out["ok"] = False
            out["error"] = "找不到归档批次 [%s]" % (batch,)
            return out
    src = batches[0]                       # 默认取【最新】的归档批次
    out["batch"] = src["name"]
    out["batch_dir"] = src["dir"]
    mf = _load_archive_manifest(src["dir"])
    files = (mf or {}).get("files") or []
    if not files:
        # 没有清单（旧归档）→ 退化为按文件名扫描，但【仍然仲裁】
        try:
            files = [{"name": n, "archived_as": n,
                      "path": os.path.join(src["dir"], n)}
                     for n in os.listdir(src["dir"])
                     if os.path.isfile(os.path.join(src["dir"], n))
                     and n != ARCHIVE_MANIFEST_NAME]
        except Exception:
            files = []
        out["notes"].append("该归档批次没有清单（旧归档），已退化为按文件名扫描")
    try:
        _work = _default_work_dir()
    except Exception:
        _work = os.getcwd()
    parts_dir = os.path.join(str(_work), "parts")
    for it in files:
        _an = it.get("archived_as") or it.get("name")
        _src = os.path.join(src["dir"], _an)
        if not os.path.exists(_src):
            out["skipped"].append({"file": _an, "why": "归档文件已不存在"})
            continue
        if part and part not in _an:
            continue
        _tgt = os.path.join(parts_dir, _an)
        _cur = _file_fingerprint(_tgt) if os.path.exists(_tgt) else None
        _arc = _file_fingerprint(_src)
        if _cur is None:
            rec = {"file": _an, "action": "restore", "archived": _arc}
            if not dry_run:
                try:
                    os.makedirs(parts_dir, exist_ok=True)
                    import shutil as _shutil
                    _shutil.copy2(_src, _tgt)
                    rec["target"] = _file_fingerprint(_tgt)
                except Exception as e:
                    out["skipped"].append({"file": _an, "why": "回填失败: %r" % (e,)})
                    continue
            out["restored"].append(rec)
            continue
        # 目标已存在 → 比指纹再决定
        _same = (_cur.get("sha1_16") and _arc.get("sha1_16")
                 and _cur["sha1_16"] == _arc["sha1_16"])
        if _same:
            out["skipped"].append({"file": _an,
                                   "why": "内容与归档件一致（sha1 相同），无需回填",
                                   "sha1_16": _arc.get("sha1_16")})
            continue
        _cur_mt = _cur.get("mtime") or 0
        _arc_mt = _arc.get("mtime") or 0
        if _arc_mt < _cur_mt and not force:
            out["skipped"].append({
                "file": _an,
                "why": ("归档件比现有文件【更旧】，拒绝覆盖（这正是 B-20 的覆"
                        "盖事故：把过门的新版换成了旧版）；确需覆盖请加 --force"),
                "archived": _arc, "current": _cur})
            continue
        rec = {"file": _an, "action": "overwrite", "archived": _arc,
               "current": _cur}
        if not dry_run:
            try:
                _bd = os.path.join(parts_dir, "_restore_backup",
                                   time.strftime("%Y%m%d-%H%M%S"))
                os.makedirs(_bd, exist_ok=True)
                out["backup_dir"] = _bd
                import shutil as _shutil
                _shutil.copy2(_tgt, os.path.join(_bd, _an))
                _shutil.copy2(_src, _tgt)
                rec["backup"] = os.path.join(_bd, _an)
                rec["target"] = _file_fingerprint(_tgt)
            except Exception as e:
                out["skipped"].append({"file": _an,
                                       "why": "覆盖失败: %r" % (e,)})
                continue
        out["restored"].append(rec)
    out["note"] = ("%s%d 个文件（跳过 %d 个）"
                   % ("[dry-run] 将回填 " if dry_run else "已回填 ",
                      len(out["restored"]), len(out["skipped"])))
    return out


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


def _reports_dir_task():
    """当前任务的 reports 子目录（与 defense_gate 同一隔离口径）。"""
    try:
        _wf = os.path.join(STATE_DIR, "workflow_state.json")
        if os.path.exists(_wf):
            with open(_wf, "r", encoding="utf-8") as _f:
                _tid = (json.load(_f) or {}).get("task_id")
            if _tid:
                _d = os.path.join(REPORTS_DIR, str(_tid))
                os.makedirs(_d, exist_ok=True)
                return _d
    except Exception:
        pass
    return REPORTS_DIR


def _report_path(room):
    """房间进度报告 —— 按任务隔离，避免新对话读到上一局的房间状态。"""
    return os.path.join(_reports_dir_task(), _safe_room_name(room) + ".report.json")


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

    ── 【BUG-观察-19 修复】增加"面板作答"为第二证据层 ────────────────────
    现场问题（竞速小车任务）：出图小屋用 ask_user.py 拿到用户确认后，
    上报 `room-report <房间> params_confirmed ...` 仍被拒绝
    （"需主对话 confirm-part 授权"），小屋只能跳过该步直接建模。

    根因：授权是【两套脱节的机制】
      · ask_user.py 拿到作答后只写 pending.json 的 `answers` 字段
        （_writeback_answer），从不写 `grants`，也不调用 confirm-part；
      · 而本函数【只看 grants】，`if not grants` 直接判失败。
    于是"面板上真有用户作答"这一事实完全不被采信 → 必然误判为越权确认。

    修复：grants 未命中时，回退检查 `answers` 里是否**确实有该零件的作答记录**
    （沿用 cmd_confirm_part 在 1512-1535 行使用的同一套宽松匹配：精确 → 互相包含
    → 去括号归一化后相等）。
    语义不变、从严原则不变：
      · 只有面板上**真实存在**作答痕迹才放行（无痕迹仍拒绝）；
      · 仍受 `confirm_part_token_required`（C 模式）开关约束。
    这样即便 confirm-part 漏调，只要用户真的在面板上答过，流程也能继续；
    反之（无人作答）依旧从严拦截，杜绝"代用户签字"。
    """
    data = _load_pending(room)
    grants = data.get("grants") or {}
    text = detail or ""

    def _miss():
        return False, (text.split()[0] if text.split() else "")

    if grants:
        for part, info in grants.items():
            if part and part in text:
                return True, part

    # ── 【BUG-观察-19 修复】第二证据层：面板作答痕迹（answers）──────────
    #   与 cmd_confirm_part 的证据匹配保持同一口径，避免两处判定不一致。
    try:
        _ans = data.get("answers") or {}
        if _ans:
            # 从 detail 提取待匹配的零件名（第一个词，去掉"已确认"等修饰）
            _pn = _norm_part(text.split()[0] if text.split() else "")
            # ① 精确相等 / ② 互相包含（保留原始键的写法差异容忍）
            for _k in _ans.keys():
                if _k and _k in text:
                    return True, _k
            # ③ 归一化后互相包含
            if _pn:
                for _k in _ans.keys():
                    _kn = _norm_part(_k)
                    if not _kn:
                        continue
                    if _pn in _kn or _kn in _pn:
                        return True, _k
    except Exception:
        pass

    # 兜底：无任何证据 → 从严拒绝
    return _miss()


def cmd_wait_rooms(targets, timeout=900.0, interval=10.0, all_active=False):
    """【BUG-03 修复】阻塞等待若干房间离开 active，或等待超时。

    设计目标：把"父代理反复轮询 room-board / parts 目录"换成【一次命令行阻塞】。
    轮询发生在 Python 进程里、只读状态文件，几乎不耗 token；
    到点（或状态变化）后一次性返回结构化结论。

    终止条件（任一即返回）：
      ① 目标房间全部离开 active（ended_at / failed_at / 判定 dead）；
      ② 期间任一房间状态发生变化（便于父代理及时处理异常）；
      ③ 到达 timeout；
      ④ all_active=True 时，只要 mode_state 里已经没有 active 房间就返回。

    Returns: {"ok":bool, "returned_because":str, "elapsed_sec":float,
              "rooms":[{room, state, active, ended, failed}], "board":[...]}
    """
    _t0 = time.time()
    _last_sig = None
    _first = True
    _board = []
    while True:
        st = load_state()
        rooms = st.get("rooms") or {}
        if all_active and not targets:
            targets = [n for n, v in rooms.items()
                       if isinstance(v, dict) and v.get("active")]
        rows = []
        _sig_bits = []
        for n in targets:
            info = rooms.get(n) or {}
            ended = bool(info.get("ended_at"))
            failed = bool(info.get("failed_at"))
            active = bool(info.get("active"))
            try:
                _bd = _board_entry(n, info, (st.get("subagents") or {}), state=st)
                _state = _bd.get("state")
            except Exception:
                _bd = {}
                _state = "unknown"
            rows.append({"room": n, "state": _state, "active": active,
                         "ended": ended, "failed": failed})
            _sig_bits.append("%s:%s:%s%s%s" % (n, _state, active, ended, failed))
        if targets:
            _board = rows
        else:
            _board = []
        _sig = "|".join(_sig_bits)
        # 条件①／②：全部结束 或 状态发生变化
        _all_done = bool(targets) and all(not r["active"] for r in rows)
        if _all_done:
            return {"ok": True, "returned_because": "all_rooms_inactive",
                    "elapsed_sec": round(time.time() - _t0, 1),
                    "rooms": rows, "note": "全部目标房间已离开 active 状态"}
        if all_active and not targets:
            return {"ok": True, "returned_because": "no_active_rooms",
                    "elapsed_sec": round(time.time() - _t0, 1), "rooms": [],
                    "note": "mode_state 里已无 active 房间"}
        if (not _first) and _sig != _last_sig:
            return {"ok": True, "returned_because": "state_changed",
                    "elapsed_sec": round(time.time() - _t0, 1),
                    "rooms": rows,
                    "note": ("房间状态发生变化（提前返回，便于及时处置）；"
                             "如需继续等请再次调用本命令")}
        _last_sig = _sig
        _first = False
        if (time.time() - _t0) >= timeout:
            return {"ok": True, "returned_because": "timeout",
                    "elapsed_sec": round(time.time() - _t0, 1),
                    "rooms": rows,
                    "note": ("等待超时（%ss）。房间仍在跑属正常；"
                             "请用 room-report-read 看进度，不要据此判死。"
                             % int(timeout))}
        time.sleep(max(1.0, min(interval, 60.0)))


def _write_report_and_heartbeat(room, stage, detail=""):
    """写入一条房间进度报告并刷新心跳（room-report 的实际落盘部分）。

    【BUG-12 关联】抽出成函数，让"防线未过但仍要如实记录 done"这条路径
    与正常路径共用同一套写盘逻辑，避免两处格式不一致导致读取侧解析失败。
    """
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
    try:
        hb = _heartbeat_path(room)
        with open(hb, "w", encoding="utf-8") as f:
            json.dump({"name": room, "ts": time.time(), "pid": os.getpid(),
                       "via": "report"}, f)
    except Exception:
        pass
    return rec


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

    # ── 【BUG-12 修复】上报 done 前自动跑一次三大防线校验 ─────────────────
    # 缺陷（2026-10-29 实测）：子代理只管建模、从不管验证；
    #   dispose 之前没有任何自动校验，于是「没验证也算跑完」，
    #   房间永远无法 room-end，而 reports/ 全程为空也没人发现。
    # 修复：把"房间声称完成"这一动作变成【防线校验的触发点】——
    #   · 这是【自报完成】，不是 room-end（room-end 仍会再校一次，双保险）；
    #   · 未通过不阻断上报（子代理仍需如实记录它失败了），
    #     但必须在返回里明确列出缺口，让父代理/模型看见"还差什么"；
    #   · 强制阻断留给 room-end（那里的语义才是"不可逆下线"）。
    if stage == "done":
        try:
            import defense_gate as _dg_done
            _dv = _dg_done.check_room_defense(room)
            if not _dv.get("ok"):
                os.makedirs(REPORTS_DIR, exist_ok=True)
                _hint = {
                    "ok": False,
                    "room": room,
                    "stage": "done",
                    "gate": "DEFENSE_INCOMPLETE_ON_DONE",
                    "blockers": _dv.get("blockers") or [],
                    "defense": _dv.get("details") or {},
                    "error": ("【三大防线未过】房间 %s 自报 done，但防线校验未通过 ——"
                              "这次上报仍被记录，但房间不会被判为真正完成。"
                              % room),
                    "how": [
                        "① 物理：python sw_bridge.py physics-optimize "
                        "<load_case.json> --room " + str(room),
                        "② 领域：python sw_bridge.py physics-validate-domain "
                        "<domain> --room " + str(room),
                        "③ 材料：每个零件 new_part(material=...) 后重新 save()",
                        "④ 复查：python defense_gate.py check-room " + str(room),
                    ],
                    "note": ("本上报仍会写入进度报告（如实记录你说过 done），"
                             "但 room-end 时会再次强制拦截。"),
                }
                # 先把 done 写进去（如实记录），再追加缺口提示返回
                _write_report_and_heartbeat(room, stage, detail)
                return _hint
        except Exception:
            # 防线模块不可用时不阻断上报（room-end 会 fail-closed 拦截）
            pass

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


# ══════════════════════════════════════════════════════════════════════════
#  【Bug⑨】长任务自动推进房间阶段（幂等 / 不覆盖终态 / 失败不阻断）
# ══════════════════════════════════════════════════════════════════════════

# 长任务（物理校核 / 疲劳 / 领域校验 / 看图矫正）进入执行时应推进到的阶段。
VALIDATING_STAGE = "validating"

# 这些阶段是【终态】：一旦上报过，任何自动推进都不得覆盖。
_TERMINAL_STAGES = ("done", "failed")


def _room_active(name):
    """该房间当前是否处于 active（已 room-start 且未 room-end）。

    只读判断，失败一律返回 False —— 宁可不推进阶段，也不要在没有房间上下文
    的场合凭空造出一份进度报告。
    """
    try:
        st = load_state()
        if str(st.get("mode", "1")) != "2":
            return False
        _v = (st.get("rooms") or {}).get(name) or {}
        return bool(_v.get("active"))
    except Exception:
        return False


def _report_is_terminal(room):
    """该房间是否已上报过 done / failed（终态）。

    两个来源都要看：latest.stage（最新一条）与 history 全量 —— 因为
    「先 done、后被某条迟到的建模上报覆盖成 modeling」是真实发生过的时序，
    只看 latest 会让 done 被悄悄抹掉。
    """
    try:
        data = _read_json_file(_report_path(room)) or {}
        stages = []
        _latest = data.get("latest") or {}
        if isinstance(_latest, dict):
            stages.append(str(_latest.get("stage") or ""))
        for _h in (data.get("history") or []):
            if isinstance(_h, dict):
                stages.append(str(_h.get("stage") or ""))
        return any(s in _TERMINAL_STAGES for s in stages if s)
    except Exception:
        return False


def push_room_stage(room=None, stage=VALIDATING_STAGE, detail="",
                    only_if_active=True, force=False):
    """【Bug⑨】长命令在【房间上下文】里被调用时，自动把 stage 推进一次。

    ── 要解决的真实缺陷（竞速小车任务现场）────────────────────────────
    底盘结构件房从 21:08 到 22:22（约 74 分钟），room-report.json 的 stage
    长期停在「modeling / 开始建DSH_底盘」：
      · 21:42 三件零件全部建完 → 之后进入物理校核、看图矫正、prime-report；
      · 但房间从未把 stage 推进到 part_done → validating → done。
    后果：主代理无法可靠判断房间「是在跑 FEA 还是已死机」，只能靠
      「SW CPU 是否活跃 + 文件是否新产生」这两条旁路推断（Bug⑨ 原文）。
    根因：physics-optimize / physics-validate-domain / physics-fatigue /
      visual_check 等【长任务】执行期间完全不碰 room-report；而
      room-heartbeat 只刷新时间戳，不推 stage。

    ── 本函数的行为契约（三条硬约束）──────────────────────────────────
      ① 【只在房间上下文里生效】only_if_active=True 时，房间必须真的
         room-start 过且仍 active；否则原样返回 skipped 原因，绝不写文件
         （否则主代理/全局脚本一跑就凭空多出一个房间的进度）。
      ② 【幂等】若最新 stage 已经是目标 stage，直接 skipped=already，
         不重复追加 history（长任务可能被调多次，不能把 history 刷爆）。
      ③ 【不覆盖终态】房间一旦上报过 done / failed，自动推进一律让步
         （force=True 也不行：终态是事实，不该被「自动」改写；
          真要改只能人工 room-report）。

    ── 失败绝不阻断主流程 ────────────────────────────────────────────
    本函数【永不抛异常】：所有失败都折成 {"ok": False, "skipped": 原因} 返回。
    调用方（物理校核 / 看图矫正）只管 try: push(...) except: pass ——
    进度上报坏了不能把真正的校核任务带崩。

    Args:
        room:           房间名；None → 依次取 DSH_ROOM 环境变量。
        stage:          目标阶段，默认 validating。
        detail:         详情文本（建议写「物理校核中 / 看图矫正中」这类可读状态）。
        only_if_active: 是否要求房间 active（默认 True）。
        force:          显式覆盖「最新 stage 已相同」的幂等短路（仍不覆盖终态）。

    Returns:
        {"ok": bool, "pushed": bool, "skipped": str|None, "room": str,
         "stage": str, "reason"?: str}
    """
    out = {"ok": False, "pushed": False, "skipped": None, "room": None,
           "stage": str(stage or VALIDATING_STAGE)}
    try:
        _room = (room or os.environ.get("DSH_ROOM") or "").strip()
        out["room"] = _room or None
        if not _room:
            out["skipped"] = "no-room-context"
            out["reason"] = ("未提供房间名且 DSH_ROOM 未设置 —— 本命令不在"
                             "任何房间上下文里执行，不推进任何房间的进度。")
            return out
        _stage = str(stage or VALIDATING_STAGE).strip() or VALIDATING_STAGE
        out["stage"] = _stage
        # ── ① 房间上下文 ────────────────────────────────────────────
        if only_if_active and not _room_active(_room):
            out["skipped"] = "no-active-room"
            out["reason"] = ("房间 %r 未登记为 active（没 room-start 或已 "
                             "room-end）—— 不凭空写进度报告。" % _room)
            return out
        # ── ③ 终态保护（先于幂等判断：done/failed 一律不让步）────────
        if _report_is_terminal(_room):
            out["skipped"] = "terminal-stage"
            out["reason"] = ("房间 %r 已上报过终态（done/failed）—— 自动推进"
                             "不覆盖终态，避免把「已完成」改回「进行中」。"
                             % _room)
            out["ok"] = True
            return out
        # ── ② 幂等：最新 stage 已是目标阶段 → 不重复追加 ─────────────
        try:
            _data = _read_json_file(_report_path(_room)) or {}
            _latest = _data.get("latest") or {}
            if (not force and isinstance(_latest, dict)
                    and str(_latest.get("stage") or "") == _stage):
                out["ok"] = True
                out["skipped"] = "already"
                out["reason"] = ("最新 stage 已是 %r，幂等跳过（不重复追加 "
                                 "history）。" % _stage)
                return out
        except Exception:
            pass
        # ── 真正写一条进度（复用 room-report 的落盘/心跳逻辑）────────
        _res = cmd_room_report(_room, _stage, detail or "")
        out["ok"] = bool(_res.get("ok"))
        out["pushed"] = bool(_res.get("ok"))
        if not _res.get("ok"):
            out["reason"] = _res.get("error") or "room-report 写入失败"
        else:
            out["reported_at"] = _res.get("reported_at")
        return out
    except Exception as e:
        # 绝不把进度上报的异常抛给主流程
        out["ok"] = False
        out["skipped"] = "exception"
        out["reason"] = repr(e)
        return out


def cmd_room_stage(room, stage=None, detail=""):
    """CLI 包装：任何工具（含未来新增的长任务）都能 shell 调它推进阶段。

    用法：
        python mode_gate.py room-stage "<房间名>" [阶段] [详情...]
        （阶段缺省 = validating；房间名也可由 DSH_ROOM 环境变量提供）

    设计意图：physics_bridge / visual_check 等模块与 mode_gate 之间【不建立
    反向依赖】—— 它们只需 subprocess 调本命令，或 import 后调
    push_room_stage()（推荐后者：零进程开销、失败语义明确）。
    """
    _stage = (stage or VALIDATING_STAGE).strip() or VALIDATING_STAGE
    _detail = detail or ({
        "validating": "进入校核阶段（物理/领域/看图），长任务执行中",
    }.get(_stage, ""))
    return push_room_stage(room, _stage, _detail)


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


# ══ 【阶段 C · 状态可信化】SW 使用窗口快照 + 唯一权威看板 room-board ══════
# 用户实测痛点（原文）：「SW 锁排队中的小屋和"死了"看起来一样」——
#   房间明明在 FIFO 队列里正常等锁，主对话看到"没产出/没心跳"就判死重启。
# 对策：waiting_sw 成为【一等状态】，看板直接给出 safe_to_restart=False
#   与一句"严禁判死/重启"的理由，主对话不需要自己推理。
ROOM_BOARD_STATES = ("starting", "running", "waiting_sw", "idle",
                     "done", "failed", "dead", "unbound", "detached")
#: 【2026-10-29 G04】detached = 房间绑定的 subagent 已不在平台注册表里
#:   （最典型：DSH 崩溃重启后，上一轮 fork 的子代理全部消失，但 mode_state
#:    仍记着它们的 id，插件注册表也可能残留 running —— 于是看板报 idle/存活，
#:    而 send_message 实际返回 TEAM_MEMBER_NOT_FOUND，房间永远无法推进）。
#: 判据给足宽限：刚 room-start 的房间（< DETACHED_GRACE_SEC）不判 detached，
#:   因为插件登记有 300ms 节流、子代理也可能还没真正起来。
DETACHED_GRACE_SEC = 90
DETACHED_NEXT_ACTION = ("先 python mode_gate.py room-fail <房间>，再 room-start + "
                        "subagent_fork（旧子代理已不在平台注册表，无法唤醒）")
#: 多久没有【可观察改动】（报告/产物/待确认/刚绑定）就算"活着但当前没动作"
BOARD_IDLE_SEC = 300
# ── 【R4 / B-28 修复】"活着" ≠ "在干活"：进展超时看门狗 ─────────────────────
# 实测（2026-10-30 四工位转台，15h）：wave2 总装房 06:03 之后 2 小时 03 分
#   零新文件、报告停在 modeling，而心跳一直是活的 →
#   safe_to_restart=false、next_action="无需动作（正在跑）"
#   → 主代理做了 8 轮【逐字相同】的轮询，5 小时 40 分全部烧在"等一个不会动的流程"。
# 根因：看板只测"存活"（心跳/平台登记），从不测"进展"。两者被完全解耦。
# 修复：把"多久没有可观察进展"变成机器可判定字段 progress_stalled，
#   超阈值后把 safe_to_restart 翻成 True —— 不改变 state（不误判死），
#   只解除"禁止自救"的锁，让主对话有正当出路。
# 阈值取 30 分钟：一次完整建模+保存+出图实测常 5~10 分钟，
#   30 分钟无任何报告/产物/待确认，几乎只可能是卡死。
# 可用 DSH_BOARD_NO_PROGRESS_SEC 覆盖（排障/长任务时放宽）。
BOARD_NO_PROGRESS_SEC_DEFAULT = 1800


def _board_no_progress_sec():
    """进展超时阈值（秒）。环境变量可覆盖，非法值回落默认。"""
    try:
        _v = int(os.environ.get("DSH_BOARD_NO_PROGRESS_SEC", "") or 0)
        if _v > 0:
            return _v
    except Exception:
        pass
    return BOARD_NO_PROGRESS_SEC_DEFAULT
#: waiting_sw 的统一阻断理由（契约文案，勿改）
WAITING_SW_BLOCK_REASON = "正在排队等 SolidWorks 锁，属正常等待，严禁判死/重启"
UNBOUND_NEXT_ACTION = ("python mode_gate.py subagent-assign <房间> <id>"
                       "（subagent_fork 返回值）")


def _sw_lock_snapshot(state=None):
    """读取 SW 使用窗口的【真实排队情况】（owner / queue / 持锁时长）。

    数据来源：
      · mode_state.sw_lock —— owner / queue / acquired_at（sw_bridge 排队时写）；
      · sw_state.json      —— 只有进程级事实（SLDWORKS.EXE 在不在），用于印证。
    绝不修改任何状态（纯只读快照）。
    """
    out = {"owner": None, "queue": [], "age_sec": None,
           "sw_running": None, "source": None}
    try:
        _st = state if isinstance(state, dict) else load_state()
        _lk = (_st or {}).get("sw_lock") or {}
        if isinstance(_lk, dict):
            out["owner"] = _lk.get("owner") or None
            _q = _lk.get("queue")
            out["queue"] = [str(x) for x in _q] if isinstance(_q, list) else []
            _acq = _lk.get("acquired_at")
            if _acq:
                try:
                    out["age_sec"] = round(time.time() - float(_acq), 1)
                except Exception:
                    out["age_sec"] = None
            out["source"] = "mode_state.sw_lock"
    except Exception:
        pass
    try:
        if os.path.exists(_SW_STATE_FILE):
            with open(_SW_STATE_FILE, "r", encoding="utf-8") as f:
                _sw = json.load(f) or {}
            out["sw_running"] = _sw.get("sw_running")
            out["sw_count"] = _sw.get("sw_count")
            out["sw_state_updated_at"] = _sw.get("updated_at")
    except Exception:
        pass
    return out


def _room_type_map():
    """房间 → 类型（来自总计子代理登记的编制；读不到返回 {}，绝不抛异常）。

    编制是"该开哪几间、每间什么类型"的唯一来源（docs 二·B）；这里只读。
    """
    out = {}
    try:
        import mode2_flow as _m2
        _plan = _m2.get_subagent_plan(_mode2_work_dir()) or {}
        for _r in (_plan.get("rooms") or []):
            if isinstance(_r, dict) and _r.get("name"):
                out[str(_r["name"])] = _r.get("type")
    except Exception:
        return out
    return out


def _board_guidance(board_state, room):
    """看板状态 → (safe_to_restart, restart_block_reason, next_action)。

    唯一合法可重启集合：done / failed / dead（平台已确认不在运行）。
    其余一律 False —— 并把"为什么不能动"写成机器可读的理由。
    """
    if board_state == "done":
        return True, None, "已收尾（ended_at）；无需动作，如需清理由人工 rooms-reset"
    if board_state == "failed":
        return True, None, ("python mode_gate.py room-start %s（重开后再 fork 小屋）"
                            % room)
    if board_state == "dead":
        return True, None, ("平台已确认不在运行：可 python mode_gate.py room-fail %s "
                            "后重新 room-start/fork" % room)
    if board_state == "detached":
        return False, ("房间绑定的子代理已不在平台注册表（DSH 崩溃/重启后失联）——"
                       "send_message 也唤不醒，但【不是】活房间，不可 reset 整局"), \
            DETACHED_NEXT_ACTION
    if board_state == "unbound":
        return False, ("平台侧仍存活，禁止 reset；只是缺 subagent-assign 登记"), \
            ("python mode_gate.py subagent-assign %s <id>（subagent_fork 返回值）"
             % room)
    if board_state == "waiting_sw":
        return False, WAITING_SW_BLOCK_REASON, \
            ("等待 SolidWorks 锁（正常排队），禁止重启；"
             "python mode_gate.py sw-status 查看队列")
    if board_state == "idle":
        # ── 【BUG-02 修复】旧的 next_action 教主对话 send_message 问进度 ──────
        # 缺陷（2026-10-29 实测）：fork 出来的建模小屋【不是 Team member】，
        #   send_message 必然返回 'active teammate "X" not found'；
        #   而 room-board 却写"send_message 问进度"，主对话照做 → 永远失败 →
        #   房间卡在 idle 无人能推进（本次「驱动轮组」因此白做）。
        # 修复：明确告知唯一可行的干预手段是【旁路读进度】，不是发消息。
        return False, ("平台侧确认仍存活（当前无可观察动作），禁止判死/重启"), \
            ("旁路读进度：python mode_gate.py room-report-read %s "
             "（fork 子代理不是 Team member，send_message/interrupt_agent 都唤不醒它，"
             "别浪费回合；持续 idle 超 15 分钟再考虑 room-fail+重开）" % room)
    if board_state == "starting":
        return False, ("刚登记、仍在 %d 秒启动宽限内，等待即可" % SW_START_GRACE), \
            "等待启动宽限期结束（禁止判死）"
    return False, "正在运行，严禁判死/重启", "无需动作（正在跑）"


def _board_entry(name, info, subs, state=None, type_map=None, st=None):
    """单个房间的看板行（room-board / room-status / residue-check 共用判定）。

    state 映射规则（契约见 docs/redesign-prime-and-status.md 二·C）：
      ended_at → done；failed_at → failed；
      平台 alive 但房间无 subagent 绑定 → unbound；
      平台 inactive/disposed/missing + 无任何证据 + 超宽限 → dead；
      active 且平台 alive 但无改动 → idle；
      其余按 _room_status_of（其内部已含 waiting_sw / 平台优先）。
    """
    info = info or {}
    now = time.time()
    if st is None:
        st = _room_status_of(name, info, subs, state=state)
    _subs = subs or {}
    _sub_rec = _subs.get(name)
    _sid = None
    if isinstance(_sub_rec, dict):
        _sid = _sub_rec.get("id")
    elif _sub_rec:
        _sid = _sub_rec
    _agent = _plugin_agent_for_room(name, _sub_rec) or {}
    _plat = st.get("platform_status")
    _legacy = str(st.get("status") or "")
    _swe = st.get("sw_lock") or {}
    _owner = _swe.get("owner")
    _queue = _swe.get("queue") or []
    _sw_pos = (_queue.index(name) + 1) if name in _queue else None
    _ev = st.get("evidence") or {}
    _report_age = st.get("report_age_sec")
    _art = st.get("artifact") if isinstance(st.get("artifact"), dict) else {}
    _art_age = _art.get("age_sec") if isinstance(_art, dict) else None
    _assigned_age = st.get("subagent_assigned_age_sec")
    _started = info.get("started_at")
    # "超宽限" = 房间登记已超过 SW_STALE_GRACE（刚起来的房间不判死）
    try:
        _beyond_grace = (_started is None) or ((now - float(_started)) > SW_STALE_GRACE)
    except Exception:
        _beyond_grace = (_started is None)
    # "当前有可观察改动"（用于 idle 判定；窗口比证据窗口更短、更诚实）
    _fresh_activity = bool(
        (_report_age is not None and _report_age <= BOARD_IDLE_SEC)
        or (_art_age is not None and _art_age <= BOARD_IDLE_SEC)
        or _ev.get("pending_confirm")
        or (_assigned_age is not None and _assigned_age <= BOARD_IDLE_SEC)
    )
    # ── state 推导（顺序即优先级，勿随意调整）────────────────────────────
    # ── 【2026-10-29 G04】detached 判定：绑定的 subagent 在平台侧查不到 ──────
    # 只有在"注册表里确实有别的 agent、但没有它"时才算失联；
    # 注册表整个不存在/为空（插件没跑、或本轮刚启动还没写入）时【不判】——
    # 否则会把正常房间误判成失联（那是更糟的误杀）。
    _registry_agents = _plugin_agents()
    _detached = False
    _detached_reason = None
    # 判据 A：注册表里有别的 agent 却没有它（且无任何证据）
    #
    # -- 【2026-10-29 修复】_plat 的判断从 is None 改成 不在 running/inactive --
    # 引入 pulse 后，_platform_liveness 在"脉冲活着但注册表里没这个 agent"时
    #   会返回 missing（而不是 None）。旧的 _plat is None 条件因此永远不成立，
    #   detached 就判不出来了 —— 这正是本次修复要覆盖的场景（DSH 重启后
    #   上一轮的子代理全都不在注册表里）。
    _plat_no_verdict = _plat in (None, "missing")
    if (_sid and _registry_agents and _plat_no_verdict
            and not _agent
            and _assigned_age is not None
            and _assigned_age > DETACHED_GRACE_SEC
            and not _ev.get("any")):
        _detached = True
        _detached_reason = ("平台注册表里找不到该子代理（%s），"
                            "send_message 也唤不醒 → 失联" % (_plat or "无结论"))
    # 判据 B（G04 整批失联）：DSH 重启过（runId 变了）→ 本轮之前绑定的子代理
    #   全部不可寻址，直接判 detached（不必逐个去注册表里碰运气）。
    if (_sid and not _detached and not info.get("ended_at")
            and not info.get("failed_at")):
        try:
            _rid_changed, _prev_rid, _cur_rid = _run_id_changed()
        except Exception:
            _rid_changed = False
        if _rid_changed and _assigned_age is not None:
            _detached = True
            _detached_reason = ("DSH 已重启（runId %s -> %s），上一轮 fork 的子代理"
                                "全部不可寻址" % (str(_prev_rid)[:18], str(_cur_rid)[:18]))

    if info.get("ended_at"):
        board_state = "done"
    elif info.get("failed_at"):
        board_state = "failed"
    elif _detached:
        board_state = "detached"
    elif _plat == "running" and not _sid:
        # 平台说活着，只是房间没绑 subagent —— 这是 assign 漏了，不是死
        board_state = "unbound"
    elif (_plat in ("inactive", "missing") and not _ev.get("any")
          and _beyond_grace):
        # 只有平台【明确】说不在 + 无任何证据 + 超宽限，才允许判死
        board_state = "dead"
    elif _legacy == "waiting_sw":
        board_state = "waiting_sw"
    elif _plat == "running" and not _fresh_activity:
        board_state = "idle"          # 活着，但当前没有可观察的动作
    elif _legacy in ("starting", "running", "running_unverified"):
        board_state = "starting" if _legacy == "starting" else "running"
    elif _legacy == "completed":
        board_state = "done"
    elif _legacy == "failed":
        board_state = "failed"
    elif _legacy in ("stale", "no_heartbeat"):
        # 判死必须【同时】满足：平台明确说不在 + 超过宽限（_beyond_grace）。
        #   · 平台无结论 → 报 idle（不许纯靠心跳判死，这正是误杀源头）；
        #   · 平台说不在了，但房间刚起来还在宽限内 → 报 starting（再等等）。
        if _plat and _beyond_grace:
            board_state = "dead"
        elif _plat:
            board_state = "starting"
        else:
            board_state = "idle"
    else:
        board_state = "running" if _legacy == "running" else "idle"
    _safe, _block, _next = _board_guidance(board_state, name)
    _latest = st.get("latest_report") if isinstance(st.get("latest_report"), dict) else {}
    # ── 【R4 / B-28 修复】进展超时看门狗（详见 BOARD_NO_PROGRESS_SEC_DEFAULT）──
    # "多久没有任何可观察进展"：取【最近一次能证明它在动】的时间差——
    #   进度报告 mtime / 产物 mtime / 刚绑定的 subagent / 刚 room-start，
    #   取其中最小的 age（= 最近的那次动静）。全都取不到 → 无法判定，不误杀。
    _prog_ages = []
    for _a in (_report_age, _art_age, _assigned_age):
        if _a is not None:
            try:
                _prog_ages.append(float(_a))
            except Exception:
                pass
    try:
        if _started:
            _prog_ages.append(float(now) - float(_started))
    except Exception:
        pass
    _progress_age = min(_prog_ages) if _prog_ages else None
    _no_prog_limit = _board_no_progress_sec()
    _progress_stalled = bool(
        _progress_age is not None
        and _progress_age > _no_prog_limit
        # 只有"还活着"的状态才谈得上"停滞"；done/failed/dead 已经是结论
        and board_state in ("running", "idle", "starting", "unbound")
    )
    if _progress_stalled and not _safe:
        # 关键：不把 state 改死（心跳/平台仍可能活着，误判死的代价更大），
        #   只解除"禁止自救"的锁，并给出可执行的下一步。
        _safe = True
        _block = ("已 %.0f 分钟没有任何可观察进展（进度报告/产物/待确认都没更新），"
                  "判定为【停滞】：允许重启，不必再等"
                  % (_progress_age / 60.0))
        _next = ("python mode_gate.py room-fail %s → room-start %s → 重新 subagent_fork"
                 "（停滞超过 %d 分钟，继续等只会空转；"
                 "若确认是长任务可临时调大 DSH_BOARD_NO_PROGRESS_SEC）"
                 % (name, name, int(_no_prog_limit / 60)))
    _detail_bits = ["state=%s" % board_state, "legacy=%s" % (_legacy or "?")]
    if _plat:
        _detail_bits.append("平台=%s(%ss)" % (
            _plat, st.get("platform_age_sec") if st.get("platform_age_sec") is not None
            else "?"))
    else:
        _detail_bits.append("平台=未知")
    if _report_age is not None:
        _detail_bits.append("报告=%ss" % _report_age)
    if isinstance(_art, dict) and _art.get("name"):
        _detail_bits.append("产物=%s(%ss)" % (_art.get("name"), _art_age))
    if _sw_pos:
        _detail_bits.append("SW队列第%s位" % _sw_pos)
    if _owner == name:
        _detail_bits.append("SW锁持有中(%ss)" % _swe.get("age_sec"))
    if _sid:
        _detail_bits.append("已绑定子代理=%s" % _sid)
        if _detached:
            _detail_bits.append(_detached_reason or (
                "平台注册表里找不到该 id（DSH 崩溃/重启后失联，send_message 唤不醒）"))
    elif _plat == "running":
        _detail_bits.append("未绑定子代理(unbound)")
    _type = (type_map or {}).get(name) or info.get("type") or None
    # ── 【未记录 BUG 修复】标注"属于上一轮任务"的房间 ──────────────────────
    # 这类房间既不是本轮在跑，也不该继续 active；主对话据此可用
    # room-fail / rooms-reset 清理，而不是以为"还有房间在干活"。
    _task_of_room = info.get("task_id")
    _cur_task = _current_workflow_task_id()
    _stale_task = bool(_task_of_room and _cur_task
                       and str(_task_of_room) != str(_cur_task))
    _detail_bits_extra = []
    if _stale_task:
        _detail_bits_extra.append(
            "归属上一轮任务(%s)，本轮=%s —— 不是活房间，可 room-fail 清理"
            % (_task_of_room, _cur_task))
    return {
        "room": name,
        "type": _type,
        "state": board_state,
        "stale_task": _stale_task,
        "room_task_id": _task_of_room,
        "status_legacy": _legacy or None,
        "detail": " / ".join([str(x) for x in _detail_bits]
                               + [str(x) for x in _detail_bits_extra]),
        "subagent_id": _sid or None,
        "subagent_label": (_agent.get("label") or None),
        "platform_status": _plat,
        "platform_age_sec": st.get("platform_age_sec"),
        "report_stage": (_latest.get("stage") if isinstance(_latest, dict) else None),
        "report_age_sec": _report_age,
        "artifact_newest": _art or None,
        "sw_queue_pos": _sw_pos,
        "sw_owner": _owner,
        "safe_to_restart": bool(_safe),
        "restart_block_reason": _block,
        "next_action": _next,
        # ── 【R4】进展看门狗字段（机器可判定，别只看 state）──────────────
        "progress_age_sec": (round(_progress_age, 1)
                             if _progress_age is not None else None),
        "progress_stalled": _progress_stalled,
        "no_progress_limit_sec": _no_prog_limit,
    }


ROOM_BOARD_HINT = (
    "room-board 是【唯一权威看板】：状态由代码 + 平台事实推导，禁止靠猜。"
    "state 语义：starting=刚登记在启动宽限内；running=确认在跑；"
    "waiting_sw=在 SW 队列里正常等锁（严禁判死/重启）；"
    "idle=平台确认存活但当前没有可观察动作；done/failed=已收尾；"
    "dead=平台明确 inactive/disposed/missing 且无证据且超宽限（才可重启）；"
    "unbound=平台还活着但房间没绑 subagent（补 subagent-assign，禁止 reset）；"
    "detached=房间绑的 subagent 已不在平台注册表（崩溃/重启后失联）→ "
    "room-fail 后重开，不要 reset 整局，也不要 send_message 干等。"
    "★ 判活权威是 pulse：pulse.alive=True 表示 DSH 进程在跳（子代理忙/等锁都不影响），"
    "此时上面的结论可信；pulse.alive=False 说明进程停了或插件没跑，"
    "这时【不要】据此判死，改看磁盘产物/报告证据链。"
    "★ 【进展看门狗】每行还有 progress_age_sec / progress_stalled："
    "progress_stalled=True 表示【活着但已超过阈值没有任何可观察进展】"
    "（报告/产物/待确认全不更新）—— 此时 safe_to_restart 已翻为 True，"
    "【不要再轮询干等】，直接 room-fail + room-start + 重新 fork。"
    "这是『活着 ≠ 在干活』的机器判据：心跳正常不代表它在推进。"
    "🚫 禁止用 list_agents / wait_agent 判断 fork 出来的子代理死活 —— 它们看不到。"
    "只读：不要再用 platform-sync 造假心跳，平台状态由插件自动登记。"
)


def _restart_verdict(status):
    """【BUG-观察-6 修复】把房间状态翻译成"可否安全重启"的机器可判定结论。

    背景（竞速小车任务实测）：
      room-status 返回 starting / running / running_unverified / stale /
      no_heartbeat / completed / failed 七种标签，但"哪些禁止重启"只写在
      ROOM_STATUS_HINT 散文里。主对话很容易把 starting / running_unverified
      误读成"小屋挂了"，从而对正在建模的屋子 recover/restart —— 白白重启。

    判定（唯一合法的可重启集合）：
      completed   → 已收尾，可清理/可重开
      failed      → 已回退，本就等待重做
      stale       → 心跳超时 + 无证据 + 超宽限，【唯一】判死态
      no_heartbeat→ 无心跳 + 无平台佐证 + 超宽限，可疑，需先 list_agents 核对
      其余（starting / running / running_unverified）→ 一律禁止重启。

    【阶段 C】新增 waiting_sw：在 SW 队列里正常等锁，不是死 —— 禁重启。

    Returns: (safe_to_restart: bool, block_reason: str|None)
    """
    if status in ("completed", "failed", "stale", "no_heartbeat"):
        return True, None
    if status == "waiting_sw":
        return False, WAITING_SW_BLOCK_REASON
    if status == "running":
        return False, "心跳/平台判定为运行中，严禁重启（会误杀正在建模的小屋）"
    if status == "running_unverified":
        return False, ("无心跳但有平台/报告/产出的近期佐证，视为运行中，严禁重启"
                       "（小屋建模时没空跳心跳是常态）")
    if status == "starting":
        return False, ("刚登记启动、仍在 %d 秒宽限期内，属正常，等待即可"
                       % SW_START_GRACE)
    return False, "状态未知，禁止重启（先核对 room-status / list_agents）"


def _room_status_of(name, info, subs, state=None):
    """推导单个房间的精确状态（六态 + 【阶段 C】waiting_sw）。

    completed   : 已正常收尾（ended_at 有值）
    failed      : 已回退待重做（failed_at 有值）
    waiting_sw  : 在 SW 队列里排队等锁（或自己正持锁）—— 正常等待，严禁判死/重启
    running     : active 且（平台判定在跑 / 心跳新鲜）
    starting    : active 且无心跳但登记未超过 SW_START_GRACE 秒 —— 正常启动中，禁止判死
    stale       : active 且心跳超时 + 无证据 + 超宽限 —— 判定死亡，需要重启
    no_heartbeat: active 且登记超过宽限期仍无任何心跳 —— 可疑，需人工核对

    ── 【阶段 C 修复】waiting_sw 是一等状态 ────────────────────────────────
    用户实测原文：「SW 锁排队中的小屋和"死了"看起来一样」。房间在 FIFO 队列里
    等锁时没有心跳、没有产物，旧逻辑会把它判成 stale/no_heartbeat → 主对话
    recover/restart → 误杀。现在只要还在 mode_state.sw_lock.queue 里，就明确
    报 waiting_sw 并给出禁重启理由；owner==自己则报 running（正在用 SW）。
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
    #
    # ── 【阶段 C】平台结论来源扩展为两层（手动 platform_status.json 优先，
    #    插件自动注册表 platform_agents.json 兜底），且把【已绑定的 subagent】
    #    一起传进去做精确 id 匹配 —— 主对话不用手动喂状态也不会误判。
    _plat_status, _plat_age = _platform_liveness(name, _sub_rec_raw)
    # ── 【阶段 C】SW 使用窗口排队情况（waiting_sw 的依据）─────────────────
    _sw = _sw_lock_snapshot(state)
    _sw_owner = _sw.get("owner")
    _sw_queue = _sw.get("queue") or []
    _sw_pos = (_sw_queue.index(name) + 1) if name in _sw_queue else None
    # 平台说【明确不在】时不再把"还在队列里"当成活着（那是队列残影）
    _plat_confirmed_gone = _plat_status in ("inactive", "missing")

    if info.get("failed_at"):
        status = "failed"
    elif info.get("ended_at"):
        status = "completed"
    elif _sw_owner and str(_sw_owner) == name and not _plat_confirmed_gone:
        # 自己正持有 SW 锁 → 正在用 SW（就是"在干活"的直接证据）
        status = "running"
    elif _sw_pos and not _plat_confirmed_gone:
        # 在 FIFO 队列里等锁 → 正常等待，严禁判死/重启（用户点名痛点）
        status = "waiting_sw"
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
    # ── 【BUG-观察-6 修复】把"是否可安全重启"编码成机器可判定字段 ────────
    # 背景：room-status 的七态语义只写在 ROOM_STATUS_HINT 散文里，主对话
    #   必须自己记住"running/starting/running_unverified 三者禁重启"，很容易
    #   把 starting / running_unverified 误判成"小屋挂了"而误杀正在建模的小屋。
    # 修复：直接给出布尔结论，调用方无需再解读状态标签。
    _safe_to_restart, _block_reason = _restart_verdict(status)
    return {
        "room": name,
        "status": status,
        # 【BUG-观察-6 修复】可否安全重启（true 才允许 recover/restart）
        "safe_to_restart": _safe_to_restart,
        "restart_block_reason": _block_reason,
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
        # ── 【阶段 C】SW 队列事实（waiting_sw 的依据，只读）──
        "sw_lock": _sw,
        "sw_queue_pos": _sw_pos,
        "sw_owner": _sw_owner,
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
    "waiting_sw=【阶段C】在 SW 队列里正常等锁(平台结论压过心跳,严禁判死/重启); "
    "unbound=【阶段C】平台还活着但房间没绑 subagent(补 subagent-assign,严禁 reset); "
    "completed=已完成(可 room-end 对账+interrupt_agent 清理); "
    "failed=已回退待重做(重新 room-start 后开新小屋); "
    "⚠️【Bug3】心跳过期但有近期报告/平台证据时一律判 running_unverified——"
    "小屋建模时没空跳心跳是常态，禁止据此 recover/restart！"
    "【BUG-观察-6】无需自己解读上述标签：直接读顶层 "
    "`safe_to_restart` / `restart_blocked_by`，或单房间的 "
    "`safe_to_restart` / `restart_block_reason` —— 只有 true 才允许 recover/restart。"
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
    # ── 【阶段 C】内部复用 room-board 的判定，但【保留旧字段】不破坏现有调用 ──
    #   旧字段：status / safe_to_restart / evidence / platform_status / ...
    #   新增字段：state（看板八态）/ next_action（下一步该干什么）
    _type_map = _room_type_map()
    for name, info in rooms.items():
        rec = _room_status_of(name, info, subs, state=state)
        try:
            _b = _board_entry(name, info, subs, state=state,
                              type_map=_type_map, st=rec)
            rec["state"] = _b.get("state")
            rec["next_action"] = _b.get("next_action")
            rec["subagent_label"] = _b.get("subagent_label")
            rec["sw_queue_pos"] = _b.get("sw_queue_pos")
        except Exception:
            rec["state"] = rec.get("status")
            rec["next_action"] = None
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

    # ── 【BUG-观察-6 修复】聚合"可否安全重启"，放在输出顶部最显眼位置 ──
    # 主对话只看一行就能决定"能不能动这个屋子"，无需再解读七态标签。
    _blocked = [r["room"] for r in out if not r.get("safe_to_restart")]
    _restartable = [r["room"] for r in out if r.get("safe_to_restart")]

    # ── 【阶段 C】看板态聚合（state 是新的权威口径，status 保留兼容）──────
    _state_counts = {}
    for _r0 in out:
        _s0 = str(_r0.get("state") or _r0.get("status") or "unknown")
        _state_counts[_s0] = _state_counts.get(_s0, 0) + 1

    return {"ok": True, "mode": _mode_internal, "mode_name": state.get("mode_name"),
            "mode_note": _mode_note,
            "workflow_step": _wf_step,
            # 【BUG-观察-6 修复】顶部安全重启结论（先看这两个字段）
            "safe_to_restart": not _blocked,
            "restartable_rooms": _restartable,
            "restart_blocked_by": _blocked,
            "rooms": out, "counts": counts,
            # 【阶段 C】看板口径（与 room-board 完全一致，避免两套说法）
            "state_counts": _state_counts,
            "source": _src,
            "consistency": _consistency,
            "hint": ROOM_STATUS_HINT + " 【阶段C】权威看板请用 room-board --json。"}


def cmd_room_board(room=None, as_json=True):
    # 【G04】顺手把本次 DSH 的 runId 记进 platform_status（只写 _meta，不影响房间结论）；
    #   下一次运行读到不同 runId 即可判"整批失联"。
    try:
        _remember_run_id()
    except Exception:
        pass
    """【阶段 C】唯一权威看板：每个房间"到底怎么了"由代码 + 平台事实给出。

    ── 为什么必须有它 ────────────────────────────────────────────────────
    用户会话实测：主对话分不清子代理死活 —— 手动 while(true) platform-sync
      造假心跳、subagent-assign 从没调用、recover 把还在跑的房间判成"僵尸残留"、
      reset 紧跟其后把三个正在建模的子代理全杀了；SW 排队中的小屋和"死了"
      看起来一模一样。根因是"状态判断靠主对话猜"。
    本命令把判断收进代码：平台事实（插件注册表）> SW 队列事实 > 报告/产物证据，
      并直接给出 safe_to_restart / restart_block_reason / next_action，
      调用方（含总理子代理）【不需要也不允许】自己再推断死活。

    Args:
        room: 只看看某个房间（None = 全部）。
        as_json: 保留参数；本命令【始终】输出 JSON（便于模型直接读）。

    Returns:
        {"ok", "generated_at", "rooms":[{room,type,state,status_legacy,detail,
          subagent_id,subagent_label,platform_status,report_stage,report_age_sec,
          artifact_newest,sw_queue_pos,safe_to_restart,restart_block_reason,
          next_action}], "sw_lock":{owner,queue,age_sec}, "summary":{counts,...},
         "hint"}
    """
    state = load_state()
    rooms = state.get("rooms") or {}
    subs = state.get("subagents") or {}
    _type_map = _room_type_map()
    _sw = _sw_lock_snapshot(state)
    _only = str(room).strip() if room else None
    if _only and _only not in rooms:
        return {"ok": False, "error": "房间未登记（未 room-start）或名称写错",
                "room": _only, "known_rooms": sorted(rooms.keys()),
                "hint": ("只读看板不会凭空造房间；先确认 room-start 已执行，"
                         "或核对 select 返回的房间表。")}
    names = [_only] if _only else sorted(rooms.keys())
    rows = []
    for _name in names:
        try:
            rows.append(_board_entry(_name, rooms.get(_name) or {}, subs,
                                     state=state, type_map=_type_map))
        except Exception as _e:
            # 单个房间判定失败不能拖垮整张看板（宁可按"未知"报告，也不崩）
            rows.append({"room": _name, "type": None, "state": "idle",
                         "status_legacy": None,
                         "detail": "判定异常: %r" % (_e,),
                         "subagent_id": None, "subagent_label": None,
                         "platform_status": None, "report_stage": None,
                         "report_age_sec": None, "artifact_newest": None,
                         "sw_queue_pos": None, "safe_to_restart": False,
                         "restart_block_reason": "判定异常，禁止重启（先人工核对）",
                         "next_action": "先跑 mode_gate.py doctor / room-status 核对状态文件"})
    _counts = {}
    for _r in rows:
        _counts[_r["state"]] = _counts.get(_r["state"], 0) + 1
    _alive_states = ("running", "starting", "waiting_sw", "idle", "unbound")
    _attention = [r["room"] for r in rows if r["state"] in ("unbound", "dead", "failed")]
    _reg = _load_plugin_registry()
    return {
        "ok": True,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "generated_at_ts": time.time(),
        "rooms": rows,
        "sw_lock": {"owner": _sw.get("owner"), "queue": _sw.get("queue") or [],
                    "age_sec": _sw.get("age_sec"),
                    "sw_running": _sw.get("sw_running")},
        "summary": {
            "rooms": len(rows),
            "counts": _counts,
            "alive": sum(1 for r in rows if r["state"] in _alive_states),
            "need_attention": _attention,
            "restartable": [r["room"] for r in rows if r["safe_to_restart"]],
        },
        "platform_registry": {
            "file": PLATFORM_AGENTS_FILE,
            "exists": os.path.exists(PLATFORM_AGENTS_FILE),
            "updated_at": _reg.get("updatedAt"),
            "source": _reg.get("source"),
            "agent_count": len(_reg.get("agents") or []),
        },
        # ── 【2026-10-29】主进程脉冲：判断"平台判据本身还可不可信"──────────
        # pulse.alive=True  → DSH 进程在跳，上面的 platform_status 判据可信；
        # pulse.alive=False → DSH 进程已停（或插件没跑），此时【不要】据此判死，
        #                     应改为看磁盘产物/报告等证据链。
        "pulse": _pulse_state(),
        "hint": ROOM_BOARD_HINT,
    }


def cmd_platform_refresh():
    """【阶段 C】把插件注册表【合成】进 platform_status.json（人工记录优先）。

    ── 为什么要合成而不是让主对话手动写 ──────────────────────────────────
    旧流程靠主对话 list_agents 后手工 platform-sync；实测它不但没写，反而开了
    一个 while(true) 假心跳 —— 主对话的手写状态本身就是不可信来源。
    现在平台事实由插件自动落 platform_agents.json，本命令把它【只读地】合成
    成 mode_gate 已在用的 platform_status.json 视图：
      · 人工记录（platform-sync）更新的项【不覆盖】—— 人比机器更该有最终话语权；
      · 每房间打印匹配结果（id 精确匹配 / label 包含匹配 / 未匹配）；
      · 平台说 disposed 的项合成 inactive（才允许后续判死）。

    Returns: {"ok", "file", "registry", "rooms":[...], "unmatched_agents":[...],
              "counts", "hint"}
    """
    state = load_state()
    rooms = state.get("rooms") or {}
    subs = state.get("subagents") or {}
    reg = _load_plugin_registry()
    agents = [a for a in (reg.get("agents") or []) if isinstance(a, dict) and a.get("id")]
    manual = _load_platform_status()
    merged = dict(manual)
    rows, updated, kept = [], [], []
    matched_ids = set()
    for _name in sorted(rooms.keys()):
        _sub_raw = subs.get(_name)
        _sid = (_sub_raw.get("id") if isinstance(_sub_raw, dict) else _sub_raw) or None
        _a = _plugin_agent_for_room(_name, _sub_raw)
        _row = {"room": _name, "subagent_id": _sid, "matched_by": None,
                "agent_id": None, "agent_label": None, "agent_status": None,
                "platform_status": None, "age_sec": None, "action": "unmatched"}
        if not _a:
            rows.append(_row)
            continue
        matched_ids.add(str(_a.get("id")))
        _row["agent_id"] = _a.get("id")
        _row["agent_label"] = _a.get("label")
        _row["agent_status"] = _a.get("status")
        _row["matched_by"] = ("id" if (_sid and str(_a.get("id")) == str(_sid))
                              else "label")
        # 【2026-10-29】改用脉冲判据：agent 在册 + 脉冲新鲜 = 活着
        _pst, _page = _platform_liveness(_name, _sub_raw)
        _ts = _parse_ts_any(_a.get("lastEventAtMs"))
        if _ts is None:
            _ts = _parse_ts_any(_a.get("lastEventAt"))
        if _ts is None:
            _ts = _parse_ts_any(_a.get("createdAt"))
        _row["platform_status"] = _pst
        _row["age_sec"] = round(_page, 1) if _page is not None else None
        _old = manual.get(_name)
        _old_ts = 0.0
        if isinstance(_old, dict):
            try:
                _old_ts = float(_old.get("ts") or 0)
            except Exception:
                _old_ts = 0.0
        # 人工记录更【新】→ 保留（不覆盖人）
        if _old_ts and _old_ts >= (_ts or 0):
            _row["action"] = "kept_manual"
            _row["manual_status"] = _old.get("status")
            kept.append(_name)
        elif _pst:
            merged[_name] = {
                "status": _pst,
                "ts": _ts or time.time(),
                "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "by": "plugin-registry",
                "agent": {"id": _a.get("id"), "label": _a.get("label"),
                          "status": _a.get("status")},
            }
            _row["action"] = "updated"
            updated.append(_name)
        rows.append(_row)
    _unmatched = []
    for _a in agents:
        if str(_a.get("id")) in matched_ids:
            continue
        _unmatched.append({"id": _a.get("id"), "label": _a.get("label"),
                           "status": _a.get("status")})
    _saved = _save_platform_status(merged)
    return {
        "ok": bool(_saved),
        "file": PLATFORM_STATUS_FILE,
        "registry": {"file": PLATFORM_AGENTS_FILE,
                     "exists": os.path.exists(PLATFORM_AGENTS_FILE),
                     "updated_at": reg.get("updatedAt"),
                     "source": reg.get("source"),
                     "agent_count": len(agents)},
        "rooms": rows,
        "unmatched_agents": _unmatched[:30],
        "counts": {"updated": len(updated), "kept_manual": len(kept),
                   "unmatched_rooms": len(rooms) - len(updated) - len(kept),
                   "unmatched_agents": len(_unmatched)},
        "hint": ("只读合成：人工 platform-sync 记录更新时不会被覆盖；"
                 "未匹配到房间的子代理只是「房间名/绑定缺失」，"
                 "请先 subagent-assign 再刷新。"),
    }


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


def _current_workflow_task_id():
    """当前任务 id（读 workflow_state；读不到返回 None）。

    【未记录 BUG 修复】mode_state.json 原来【没有 task_id 字段】，
    于是"哪些房间属于本轮任务"无从判断 —— 上一轮没收尾的房间
    （实测 2026-10-29 遗留 3 个 active 房间，task_epoch 还是 10 月中旬）
    会一直挂在 mode_state 里，被守卫当成"仍有 active 房间"，
    导致【任何新会话都无法正常结束】（守卫每轮都 steer 拉回来）。
    本函数提供跨文件关联所需的当前 task_id。
    """
    try:
        if not os.path.exists(WORKFLOW_STATE_PATH):
            return None
        with open(WORKFLOW_STATE_PATH, "r", encoding="utf-8-sig") as f:
            _wf = json.load(f) or {}
        _t = _wf.get("task_id")
        return str(_t) if _t else None
    except Exception:
        return None


def stale_room_names(state=None):
    """【未记录 BUG 修复】列出属于【已过期任务】的房间名。

    判据（保守，宁可少判不可误判）：
      房间记录里有 task_id、且与本轮 workflow 的 task_id 不同 → 过期。
    没有 task_id 的历史记录【不判过期】（升级前的老数据无从判断，
    误判会让正在跑的房间被当成残留）。

    用途：守卫/room-board 判断"是否还有 active 房间"时，只看本轮任务的房间。
    """
    try:
        st = state if isinstance(state, dict) else load_state()
        cur = _current_workflow_task_id()
        if not cur:
            return []
        out = []
        for _n, _v in (st.get("rooms") or {}).items():
            if not isinstance(_v, dict):
                continue
            _rt = _v.get("task_id")
            if _rt and str(_rt) != cur:
                out.append(_n)
        return out
    except Exception:
        return []


def load_state():
    if not os.path.exists(STATE_PATH):
        return _fresh_state()
    try:
        # 【BOM 容错】用 utf-8-sig：PowerShell 5.1 的 Set-Content -Encoding UTF8
        #   会写 BOM，按 utf-8 读会抛异常 → 房间表被当成空 → 状态全丢。
        with open(STATE_PATH, "r", encoding="utf-8-sig") as f:
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


def _sync_sw_state_file(running=None, pids=None, writer="auto", force=False):
    """【BUG-观察-8 修复】把 sw_state.json 与真实进程状态对齐（查询即自愈）。

    背景（竞速小车任务实测）：
      sw-state.json 记录 `file_running=false`，但 `tasklist` 显示
      SLDWORKS.exe 仍在跑（PID 15116）—— 派生缓存与事实脱节。
      根因：sw_state.json 的刷新依赖一个常驻监控进程（sw_monitor.pid），
      而该进程默认根本没启动；唯一的纠正逻辑又被 `if auto:` 关在
      `lock-doctor --auto` 里，用户不主动跑就永远不纠正。

    影响：sw_bridge._sw_locked_by_other() 读到陈旧 sw_state.json 会误判
      "SW 被占用"（假忙）或"SW 已关闭"（假闲）→ 传导成队列阻塞或 API 冲突。

    修复：凡是已经探测出真实进程数的命令（sw-status / lock-doctor），
      顺手做一次比对；不一致或文件过期（>30s）就立即回写。
      sw_state.json 是纯派生缓存，回写无副作用。

    Args:
        running/pids: 调用方已探测到的真实进程数/PID 列表；None 则自行探测
        writer: 写入者标识，便于排查
        force: 强制回写（忽略"是否需要"的判断）

    Returns: dict {checked, file_running, file_count, real_running, real_count,
                   file_age_seconds, mismatch, corrected}
    """
    if running is None:
        try:
            running, pids = _sw_proc_list()
        except Exception:
            running, pids = 0, []
    info = {
        "checked": True,
        "real_running": bool(running > 0),
        "real_count": running,
        "real_pids": pids,
        "file_running": None,
        "file_count": None,
        "file_age_seconds": None,
        "mismatch": None,
        "corrected": False,
        "writer": writer,
    }
    stale = None
    try:
        with open(_SW_STATE_FILE, "r", encoding="utf-8") as f:
            stale = json.load(f)
    except Exception:
        stale = None

    if stale is None:
        # 文件不存在 → 直接建立一份真实快照
        info["mismatch"] = True
    else:
        info["file_running"] = stale.get("sw_running")
        info["file_count"] = stale.get("sw_count")
        try:
            info["file_age_seconds"] = round(
                time.time() - float(stale.get("updated_at", 0)), 1)
        except Exception:
            info["file_age_seconds"] = None
        info["mismatch"] = (bool(stale.get("sw_running")) != bool(running > 0))

    _age = info.get("file_age_seconds")
    _need = bool(force or info["mismatch"] or _age is None or _age > 30)
    if _need:
        try:
            st = {"sw_running": running > 0, "sw_count": running, "sw_pids": pids,
                  "updated_at": time.time(), "written_by": writer}
            with open(_SW_STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(st, f)
            info["corrected"] = True
        except Exception:
            pass
    return info


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
        # 【BUG-观察-15 修复】补 encoding —— 原先无 encoding，按系统 locale(cp936)
        #   写盘，是本仓库唯一一处"写入不指定编码"的漏点。
        with open(_SW_MONITOR_PID_FILE, "w", encoding="utf-8") as f:
            f.write(str(proc.pid))
    except Exception:
        pass
    return {"ok": True, "started_pid": proc.pid,
            "message": "SW 后台监控已启动（PID=%d），每 %d 秒刷新状态" % (proc.pid, SW_MONITOR_INTERVAL)}


def _monitor_bg_impl():
    """后台监控实现入口。"""
    _sw_monitor_loop()


# ═══════════════════════════════════════════════════════════════════════════
# 【第4项 · 代码强制】SW 健康看护：锁解锁后、建模前自动启动
# ═══════════════════════════════════════════════════════════════════════════
# 用户原话：
#   「在子代理的SW锁解锁之后，利用SW建模之前，利用 DSH 的后台任务运行时
#     （ctx.jobs），让父代理启动一个耗时命令，专门做轻量级的定期检查。
#     比如每 10 秒统计一次系统关键指标，一旦内存、CPU超85％阈值就输出一行
#     警报。这个 job 会立即返回一个 job id，在后台持续跑着。一旦相关指数
#     超标立马关闭SW」
#
# 与既有 sw-monitor 的区别（不要混用）：
#   · sw-monitor（上面）：只记录 SLDWORKS.EXE 在不在 → 写 sw_state.json，**不杀进程**；
#   · sw_guard（本函数拉起）：采 CPU/内存，超阈值**立即关闭 SW** → 写 sw_guard_state.json。
#   两者互补，各自有独立 pid 文件，互不干扰。
#
# 落实方式：**由 mode_gate 在真实获取 SW 使用权的那一刻自动拉起**，
#   而不是靠"提示词提醒模型记得启动"。任何代码路径只要拿到锁，看护就在跑。

_SW_GUARD_PID_FILE = os.path.join(STATE_DIR, "sw_guard.pid")

#: 看护默认阈值与间隔（与 sw_guard.py 的默认值保持一致）
SW_GUARD_INTERVAL = 10
SW_GUARD_CPU_LIMIT = 85.0
SW_GUARD_MEM_LIMIT = 85.0


def _sw_guard_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "sw_guard.py")


def _pid_alive(pid):
    """判断 pid 是否指向一个【真实存在】的进程（Windows 安全实现）。

    ── 为什么不能用 os.kill(pid, 0) ─────────────────────────────────────
    实测（本机 Windows + Python 3.13）：对一个**确实活着**的 python 进程调
    `os.kill(pid, 0)` 会抛 `OSError(22, '参数错误', None, 87, None)`；
    而同一时刻 `tasklist /FI "PID eq <pid>"` 与 `OpenProcess` 都证明它活着。
    即 os.kill(pid,0) 在本机是【假阴性】——若用它判存活，结论恒为"没在跑"。

    后果（已实际发生）：看护的幂等判定失效 → **每次取锁都重复拉起一个新的
    看护进程**，越积越多，每个都在采 CPU/内存并在超阈值时 taskkill SW。

    正确做法：OpenProcess 拿句柄（拿得到即存在），失败再退回 tasklist 文本匹配。
    注意 OpenProcess 需要 PROCESS_QUERY_LIMITED_INFORMATION(0x1000)，
    用 PROCESS_QUERY_INFORMATION(0x400) 对高完整性进程会权限不足而误判。
    """
    try:
        pid = int(pid)
    except Exception:
        return False
    if pid <= 0:
        return False
    # ① 首选：OpenProcess —— 拿得到句柄就是存在
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h = k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if h:
            k32.CloseHandle(h)
            return True
    except Exception:
        pass
    # ② 兜底：tasklist 文本匹配（无 ctypes / 权限受限时）
    try:
        out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, timeout=15,
                             creationflags=CREATE_NO_WINDOW)
        _txt = (out.stdout or "")
        # tasklist 找不到时会输出 "信息: 没有运行的任务匹配指定标准" 之类的提示，
        # 因此必须确认输出里真的带这个 pid，而不是只要"有输出"就算活着。
        return ('"%d"' % pid) in _txt or (" %d " % pid) in _txt
    except Exception:
        return False


def _sw_guard_alive():
    """看护进程是否还活着（返回 pid 或 None）。"""
    try:
        if not os.path.exists(_SW_GUARD_PID_FILE):
            return None
        with open(_SW_GUARD_PID_FILE, "r", encoding="utf-8") as f:
            pid = int((f.read() or "").strip())
    except Exception:
        return None
    return pid if _pid_alive(pid) else None


def ensure_sw_guard(interval=None, cpu_limit=None, mem_limit=None, force=False):
    """确保 SW 健康看护在跑；已在跑则直接返回既有 pid（幂等）。

    ★ 这是"代码强制"的落点：调用方（sw_bridge 取锁后）**必然**触发它，
      不依赖模型是否记得。可用 DSH_SW_GUARD_OFF=1 显式关闭（会留痕）。

    Returns:
        {"ok":bool, "started":bool, "already_running":bool, "pid":int|None,
         "interval":..., "cpu_limit":..., "mem_limit":..., "note":...}
    """
    if str(os.environ.get("DSH_SW_GUARD_OFF") or "").strip() in ("1", "true", "yes"):
        return {"ok": True, "started": False, "already_running": False, "pid": None,
                "skipped": True,
                "note": "DSH_SW_GUARD_OFF 已设：按用户显式要求跳过后台看护。"}

    _script = _sw_guard_path()
    if not os.path.exists(_script):
        return {"ok": False, "started": False, "already_running": False, "pid": None,
                "error": ("sw_guard.py 不存在（%s）—— 无法启动 SW 健康看护。"
                          "建模期间 CPU/内存超阈值将无人处置。" % _script)}

    if not force:
        _pid = _sw_guard_alive()
        if _pid:
            return {"ok": True, "started": False, "already_running": True, "pid": _pid,
                    "note": "SW 健康看护已在运行（PID=%d），无需重复启动。" % _pid}

    _iv = int(interval if interval is not None else SW_GUARD_INTERVAL)
    _cpu = float(cpu_limit if cpu_limit is not None else SW_GUARD_CPU_LIMIT)
    _mem = float(mem_limit if mem_limit is not None else SW_GUARD_MEM_LIMIT)
    _log = os.path.join(STATE_DIR, "sw_guard.log")
    try:
        _proc = subprocess.Popen(
            [sys.executable, _script, "run",
             "--interval", str(_iv), "--cpu", str(_cpu), "--mem", str(_mem),
             "--log", _log],
            creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    except Exception as e:
        return {"ok": False, "started": False, "already_running": False, "pid": None,
                "error": "启动 SW 健康看护失败: %r" % (e,)}
    try:
        # 原子写 pid：避免别的进程读到半截内容
        _tmp = _SW_GUARD_PID_FILE + ".tmp.%d" % os.getpid()
        with open(_tmp, "w", encoding="utf-8") as f:
            f.write(str(_proc.pid))
        os.replace(_tmp, _SW_GUARD_PID_FILE)
    except Exception:
        pass
    return {"ok": True, "started": True, "already_running": False, "pid": _proc.pid,
            "interval": _iv, "cpu_limit": _cpu, "mem_limit": _mem, "log": _log,
            "note": ("SW 健康看护已启动（PID=%d）：每 %d 秒采样，"
                     "CPU/内存超 %.0f%% 即告警并立即关闭 SolidWorks。"
                     % (_proc.pid, _iv, max(_cpu, _mem)))}


def cmd_sw_guard_start(interval=None):
    r = ensure_sw_guard(interval=interval, force=False)
    return r


def cmd_sw_guard_stop():
    """停止看护（正常不需要 —— 它随 SW 关闭或任务结束自行退出）。"""
    _pid = _sw_guard_alive()
    if not _pid:
        return {"ok": True, "stopped": False, "note": "没有正在运行的 SW 健康看护。"}
    try:
        subprocess.run(["taskkill", "/F", "/PID", str(_pid)],
                       capture_output=True, text=True, timeout=15,
                       creationflags=CREATE_NO_WINDOW)
    except Exception as e:
        return {"ok": False, "error": "停止失败: %r" % (e,), "pid": _pid}
    try:
        os.remove(_SW_GUARD_PID_FILE)
    except Exception:
        pass
    return {"ok": True, "stopped": True, "pid": _pid}


def cmd_sw_guard_status():
    """看护状态：进程是否活着 + 最近一次采样结果（读 sw_guard_state.json）。"""
    _pid = _sw_guard_alive()
    _st_file = os.path.join(STATE_DIR, "sw_guard_state.json")
    _st = None
    if os.path.exists(_st_file):
        try:
            with open(_st_file, "r", encoding="utf-8") as f:
                _st = json.load(f)
        except Exception as e:
            _st = {"_read_error": repr(e)}
    return {"ok": True, "running": bool(_pid), "pid": _pid,
            "pid_file": _SW_GUARD_PID_FILE,
            "state_file": _st_file,
            "last_sample": _st,
            "thresholds": {"cpu_limit": SW_GUARD_CPU_LIMIT,
                           "mem_limit": SW_GUARD_MEM_LIMIT,
                           "interval_sec": SW_GUARD_INTERVAL},
            "note": ("running=false 表示看护未在跑 —— 若正在建模，"
                     "说明有代码路径绕过了取锁钩子（应当排查）。")}


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


def _require_subagent_plan():
    """房间开工前必须有总计子代理登记过的编制（判据来自落盘文件，不是自述）。"""
    try:
        import mode2_flow as _m2
    except Exception as e:
        return {"ok": False,
                "error": "mode2_flow 不可用，无法校验编制: %r" % (e,)}
    try:
        _plan = _m2.get_subagent_plan(_mode2_work_dir())
    except Exception as e:
        return {"ok": False, "error": "读取编制失败: %r" % (e,)}
    if not _plan.get("ok"):
        return {
            "ok": False,
            "gate": "SUBAGENT_PLAN_REQUIRED",
            "error": "尚未登记子代理编制，禁止 room-start。",
        }
    # 编制必须属于【当前任务】—— 残留的旧编制同样不能拿来开工。
    _task_now = None
    try:
        import workflow_gate as _wg2
        _task_now = (_wg2.load_state() or {}).get("task_id")
    except Exception:
        _task_now = None
    _plan_task = _plan.get("task_id")
    # 没有 task_id 的编制无法证明属于本任务 —— 一律按残留处理（fail-closed）。
    if _task_now and not _plan_task:
        return {
            "ok": False,
            "gate": "SUBAGENT_PLAN_STALE",
            "error": ("子代理编制缺少 task_id，无法证明属于当前任务 %s —— "
                      "按残留处理，禁止开工。" % _task_now),
            "hint": "让总计子代理重新登记编制（会写入本任务 task_id）。",
        }
    if _task_now and _plan_task and str(_plan_task) != str(_task_now):
        return {
            "ok": False,
            "gate": "SUBAGENT_PLAN_STALE",
            "error": ("子代理编制属于上一轮任务（%s），当前任务为 %s —— "
                      "残留编制不允许用于开工。"
                      % (_plan_task, _task_now)),
            "hint": "让总计子代理为本任务重新登记编制后再 room-start。",
        }
    return {"ok": True, "plan": _plan}


def _mode2_work_dir():
    """取 mode2_flow 的工作目录（与门禁同一任务目录）。"""
    try:
        import workflow_gate as _wg
        _st = _wg.load_state()
        return _wg._m2_work_dir(_st)
    except Exception:
        return None


def cmd_room_start(name):
    state = load_state()
    if str(state.get("mode")) != "2":
        err = "当前不是大型复杂器械装配模式,无需登记小屋。"
        err += "若确认为装配任务请先: mode_gate.py declare 2"
        return {"ok": False, "error": err}
    # ── 强制点 C：房间开工前必须已由总计子代理登记编制 ──────────────────
    # 实测违规：十阶段还停在 TaskUnderstanding，就直接 room-start + fork×2，
    #   而且 prompt 里的零件清单和门禁波次要求对不上。
    # 编制是"该开哪几间、每间做什么"的唯一来源；没有编制就不许开工。
    _plan_gate = _require_subagent_plan()
    if not _plan_gate.get("ok"):
        return _plan_gate
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

    # ── 【2026-10-29 G06】必须在覆盖前抓取"上一次是不是失败过" ────────────
    # 坑：下一行就会把 rooms[name] 整体替换成 {"active":True,...}，
    #   之后再读 rooms[name].failed_at 永远是 None → "失败后默认 fresh" 失效。
    _prev_rec = dict(rooms.get(name) or {})
    _was_failed = bool(_prev_rec.get("failed_at"))
    rooms[name] = {"active": True, "started_at": time.time(),
                   # 【未记录 BUG 修复】记录归属任务：让"过期房间"可被判出，
                   # 否则上一轮遗留的 active 房间会永久卡住守卫与新任务。
                   "task_id": _current_workflow_task_id()}
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
    # ── 【2026-10-29 G06 修复】上一次记录是 failed → 默认按【全新一波】清理 ──
    # 实测：room-fail 之后重开房间，room-start 仍是 history_preserved=true，
    #   保留旧报告与旧绑定，父代理容易误判"已建模"而跳过零件（BUG-G06）。
    #   「失败重开」的语义本来就是要重做，因此默认 fresh；确实想保留时用 --keep。
    _keep = "--keep" in sys.argv
    _fresh_explicit = "--fresh" in sys.argv
    _fresh = _fresh_explicit or (_was_failed and not _keep)
    # ── 【BUG-05 修复】fresh 重开时把该房间的旧产物归档，避免新旧混在一起 ──
    # 实测（2026-10-29 竞速小车）：底盘房间 room-fail 后重开，返回
    #   fresh_reason=auto_fresh_after_fail，但 parts/ 里上一波的 3 个 .SLDPRT
    #   原样留着。新小屋若重新建这 3 件会覆盖；判断失误就漏建/重复建，
    #   而且事后无法区分"这批零件属于哪一波"。
    # 做法：把本房间【已登记归属】的产物移到 parts/_archive/<房间>_<时间戳>/。
    #   用 artifacts_registry 的归属信息定位（不猜文件名），
    #   移动失败只记录警告，绝不阻断 room-start。
    _archive_info = None
    if _fresh:
        try:
            _archive_info = _archive_room_artifacts(name)
        except Exception as _e_arc:
            _archive_info = {"ok": False, "error": repr(_e_arc)}
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
    result["fresh_reason"] = ("explicit_fresh" if _fresh_explicit else
                              ("auto_fresh_after_fail" if _was_failed else None))
    # 【BUG-05】把旧产物归档结果告诉调用方（成功/无产物/失败三种都如实报）
    if _archive_info:
        result["archived_artifacts"] = _archive_info
    # 【G06】重开失败房间时必须把旧绑定清掉，否则 room-board 仍显示旧 subagent_id
    if _fresh:
        try:
            if (state.get("subagents") or {}).pop(name, None):
                result["subagent_binding_cleared"] = True
        except Exception:
            pass
        try:
            _pending_path = _pending_confirm_path(name)
            if _pending_path and os.path.exists(_pending_path):
                result["pending_kept"] = True   # 授权令牌属用户意志，仍不清
        except Exception:
            pass
    if _fresh:
        result["note_fresh"] = (
            ("已自动按【全新一波】清理该房间的历史报告/心跳/旧绑定"
             "（上次记录为 failed，重开即重做；如确需保留请加 --keep）。"
             if (_was_failed and not _fresh_explicit) else
             "已按 --fresh 清理该房间的历史报告与心跳（全新一波）。"))
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
            # ── 【P0 修复】stage 必须兼容写入侧的真实结构 ─────────────────
            # 原缺陷：cmd_room_report 落盘的是 {room, latest, history}，
            #   stage 在 【latest.stage】里，没有任何顶层 stage/last_stage 键。
            #   读取侧却只取顶层 → done_report 恒为 False，
            #   于是"只上报 done、没有 part_done"的房间（典型如总装/出图房间）
            #   永远拿不到完成证据，自动收尾被判「缺少完成证据」而卡死。
            #   实测现场「总装与验证」的 history 正是只有 done、无 part_done。
            _latest = rep.get("latest") if isinstance(rep.get("latest"), dict) else {}
            _hist = rep.get("history") or rep.get("events") or []
            _hist_stages = []
            if isinstance(_hist, list):
                for h in _hist:
                    if isinstance(h, dict):
                        _hist_stages.append(str(h.get("stage") or "").strip())
            # ⚠️ latest 是 history 的最后一条（cmd_room_report 写的就是同一记录），
            #   两者相加会【重复计数】。故只在 history 为空时才回退用 latest。
            if _hist_stages:
                _all_stages = _hist_stages
            else:
                _all_stages = [str(_latest.get("stage") or "").strip()]
            _all_stages += [str(rep.get("stage") or "").strip(),
                            str(rep.get("last_stage") or "").strip()]
            stage = next((s for s in _all_stages if s), "")
            ev["report_stage"] = stage or None
            # 「曾经 done 过」即算完成证据：不能用 latest 覆盖历史，
            #   否则「done 之后又上报 modeling」会把已完成证据抹掉（实测复现）。
            if "done" in _all_stages:
                ev["done_report"] = True
                reasons.append("有 done 阶段报告")
            # 统计 part_done 次数
            _pd = sum(1 for s in _all_stages if s == "part_done")
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
            except ImportError as _e_imp3:
                # ── 【P0-BUG-防线 fail-open】自动收尾同样不得静默放行 ──────
                # 原先 `except ImportError: pass` 会让"防线模块缺失"直接
                #   变成 step=finished（不可逆终态，守卫随即放行）。
                return {
                    "advanced": False,
                    "reason": "defense_gate.py 不可导入，按[失效关闭]拒绝自动收尾",
                    "defense_required": True,
                    "blockers": ["defense_gate.py 导入失败: %r" % (_e_imp3,)],
                    "hint": ("请先修复 defense_gate.py（python defense_gate.py status）；"
                             "或用 room-end --force / DSH_DEFENSE_BYPASS=1 显式绕行（会留痕）。"),
                }
            except Exception:
                pass
        else:
            # 【合法绕行留痕】DSH_DEFENSE_BYPASS=1 跳过总闸，但必须记账
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


def _visual_gate_for_room(name):
    """【第6项】房间下线前的"看图证据"强制核实。

    返回 None 表示跳过（不该拦），或返回一个"拒绝 room-end"的响应体。

    什么时候【跳过】（不拦）——刻意保守，避免误锁死流程：
      ① visual_check.py 不存在（模块没装）→ 无法核实，跳过并留提示；
      ② 本房间是装配/出图/总理这类【非建模】房间 → 看图门管的是建模件；
      ③ 本房间没有产出任何零件（还没建模）→ 没有可看的对象。

    什么时候【拦截】：
      · 建模房间 + 有零件，但存在零件【没有留下经核实的看图证据】。
        这包括：没跑过视觉门、截图不存在、截图是 0 字节、闭环未 PASS。

    为什么必须拦：用户要求"必须要让模型自己看到他建的是什么样子的东西"。
      若只在提示词里写"记得截图"，模型跳过就无从发现 —— 本函数让
      "没看图"变成**拒绝下线**，无法绕过（除非显式 --force，会留痕）。
    """
    # ① 模块可用性：不可用则跳过（绝不假装检查过）
    _vc_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "visual_check.py")
    if not os.path.exists(_vc_path):
        return None
    _vc = None
    try:
        import visual_check as _vc  # noqa: F401
    except Exception:
        return None

    # ② 只对【建模类】房间生效（与 WAVE1 同口径）
    _MODELING_TYPES = ("structural", "transmission", "housing",
                       "support", "spring", "thermal", "corrosion")
    _rtype = None
    try:
        _ms = load_state()
        for _rn, _rv in (_ms.get("rooms") or {}).items():
            if _rn == name and isinstance(_rv, dict):
                _rtype = _rv.get("type")
                break
    except Exception:
        _rtype = None
    if _rtype and _rtype not in _MODELING_TYPES:
        return None

    # ③ 找出本房间登记过的零件文件（走 artifact registry，最权威）
    _parts = []
    try:
        _rec = _load_artifact_registry().get(str(name))
        if isinstance(_rec, list):
            for _it in _rec:
                if not isinstance(_it, dict):
                    continue
                _p = str(_it.get("path") or "")
                if _p.lower().endswith((".sldprt", ".sldasm")) and os.path.exists(_p):
                    _parts.append(_p)
    except Exception:
        _parts = []
    if not _parts:
        # 没登记零件 → 无从核实，跳过（不能因为"没零件"就拦，那会误锁早期阶段）
        return None

    # ④ 逐个零件核实看图证据
    _bad = []
    # ★ 证据定位：visual_check 的 _default_out_dir 每次都建**带时间戳的新目录**
    #   （为了不覆盖历史轮次，本身合理），因此这里**不能自己拼一个固定路径**
    #   —— 那样永远找不到证据，会把所有房间误拦（比不拦更糟）。
    #   正确做法：按零件名扫 visual_check/<零件名>_* 目录，取最新证据文件。
    _gate_name = getattr(_vc, "GATE_FILE", "visual_gate.json")
    _search_roots = []
    try:
        _st_wd = (load_state() or {}).get("work_dir")
        if _st_wd:
            _search_roots.append(_st_wd)
    except Exception:
        pass
    _search_roots.append(os.getcwd())

    def _find_gate(part_path):
        _stem = os.path.splitext(os.path.basename(part_path or ""))[0]
        _cands = []
        for _r in _search_roots:
            _base = os.path.join(_r, "visual_check")
            if not os.path.isdir(_base):
                continue
            try:
                for _d in os.listdir(_base):
                    if not _d.startswith(_stem + "_"):
                        continue
                    _f = os.path.join(_base, _d, _gate_name)
                    if os.path.isfile(_f):
                        try:
                            _cands.append((os.path.getmtime(_f), _f))
                        except Exception:
                            _cands.append((0.0, _f))
            except Exception:
                continue
        if not _cands:
            return None
        _cands.sort(reverse=True)
        return _cands[0][1]

    for _p in _parts:
        _gate_file = _find_gate(_p)
        _rec_g = None
        # _gate_file 可能为 None（没找到任何证据目录）——必须先判空，
        # 否则 os.path.exists(None) 会抛 TypeError（实测踩到）。
        if _gate_file and os.path.exists(_gate_file):
            try:
                with open(_gate_file, "r", encoding="utf-8") as _f:
                    _rec_g = json.load(_f)
            except Exception as _e:
                _bad.append("%s：看图证据文件损坏（%r）"
                            % (os.path.basename(_p), _e))
                continue
        if not _rec_g:
            _bad.append("%s：没有看图证据（未跑 enforce_visual_gate）"
                        % os.path.basename(_p))
            continue
        if not _rec_g.get("passed"):
            _bad.append("%s：看图闭环未通过（%s）"
                        % (os.path.basename(_p),
                           _rec_g.get("final_verdict")
                           or (_rec_g.get("blockers") or ["未说明"])[0]))
            continue
        # ── 【R1 / BUG-25·BUG-84 修复】取闭环记录的键名必须与写入方一致 ──
        # 真实写入方 visual_check.enforce_visual_gate 落盘的是
        #   out["loop_file"] = <visual_loop.json 的绝对路径>   （字符串）
        # 旧代码只读 _rec_g.get("loop") —— 生产环境该键【从不存在】，
        #   于是回落成 _rec_g 本身；而 gate 记录里没有 rounds
        #   → verify_visual_evidence 报"没有看过图的痕迹"
        #   → 100% 假阴性（证据齐全也一律 VISUAL_EVIDENCE_REQUIRED）。
        # 回归测试一直绿，是因为它的夹具恰好写的是 "loop": {...} 这个键。
        # 修法：按优先级取 ①内嵌 loop 记录 ②loop_file 指向的文件 ③自证 rounds。
        _loop_rec = _rec_g.get("loop")
        if not _loop_rec:
            _lf = _rec_g.get("loop_file")
            if isinstance(_lf, str) and _lf:
                if not os.path.isabs(_lf):
                    _lf = os.path.join(os.path.dirname(_gate_file), _lf)
                if os.path.exists(_lf):
                    # verify_visual_evidence 支持直接传 visual_loop.json 路径
                    _loop_rec = _lf
                else:
                    _bad.append("%s：看图闭环记录文件不存在（%s）"
                                % (os.path.basename(_p), _lf))
                    continue
            elif isinstance(_rec_g.get("rounds"), list):
                _loop_rec = _rec_g
        if not _loop_rec:
            _bad.append("%s：看图证据里没有闭环记录（既无 loop 也无 loop_file）"
                        % os.path.basename(_p))
            continue
        # 复核证据文件里引用的截图是否仍真实存在（防事后删除/替换）
        try:
            _ev = _vc.verify_visual_evidence(_loop_rec, out_dir=None)
            if not _ev.get("ok"):
                _mis = (_ev.get("missing") or []) + (_ev.get("bad") or [])
                _why = (_mis[0].get("why") if _mis and isinstance(_mis[0], dict)
                        else "截图证据不成立")
                _bad.append("%s：看图证据复核失败（%s）"
                            % (os.path.basename(_p), _why))
        except Exception as _e:
            _bad.append("%s：看图证据复核异常（%r）" % (os.path.basename(_p), _e))

    if not _bad:
        return None      # 全部通过 → 不拦

    _nl = chr(10)
    return {
        "ok": False,
        "room": name,
        "gate": "VISUAL_EVIDENCE_REQUIRED",
        "blockers": _bad,
        "checked_parts": len(_parts),
        "error": ("【第6项·看图门】房间 [%s] 有 %d 个零件缺少经核实的看图证据，"
                  "拒绝 room-end。" % (name, len(_bad))),
        "message": _nl.join([
            "=== 【看图门】拦截：房间 [%s] 的零件没有「看过图」的证据 ===" % name, "",
            "用户要求：建模子代理必须以「看图→矫正→继续看→继续矫正」作为",
            "建模成功后的主要矫正方式，必须让模型自己看到建成了什么样子。", "",
            "未通过的零件：",
        ] + ["  ✗ " + _b for _b in _bad] + [
            "",
            "补齐方式（对每个零件跑一次）：",
            "  python sw_bridge.py run <你的脚本>        # 建模",
            "  python visual_check.py gate --part \"<零件>.SLDPRT\" \\",
            "         --expect '{\"bbox_mm\":[xmin,xmax,ymin,ymax,zmin,zmax]}'",
            "  该命令会：三视图+等轴测截图 → 真实回读几何比对 → 不一致则给出矫正",
            "            → 继续看 → 继续矫正，并把证据落到 visual_gate.json。", "",
            "确需放行（会留痕）：python mode_gate.py room-end \"%s\" --force" % name,
        ]),
    }


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
    # ══ 【第6项 · 代码强制】建模后必须"看过图"才能下线 ═══════════════════
    # 用户原话：
    #   「建模子代理必须以『看图→矫正模型→继续看→继续矫正』为建模成功后
    #     最为主要的矫正建模方式。必须要让模型自己看到他建的是什么样子的东西」
    #
    # 强制点：房间下线（不可逆）前，本房间产出过的每个零件都必须留下
    #   **经核实的看图证据**（截图真实存在且非空 + 闭环判定 PASS）。
    #   这不是"建议截图"，是拒绝下线的硬条件。
    # 放行：与三大防线同一套绕行机制（--force / DSH_DEFENSE_BYPASS=1，均留痕）。
    if not force:
        _vg = _visual_gate_for_room(name)
        if _vg and not _vg.get("ok") and not _vg.get("skipped"):
            return _vg
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
        except ImportError as _e_imp:
            # ── 【P0-BUG-防线 fail-open】ImportError 不再静默放行 ──────────
            # 原实现是 `except ImportError: pass` → 直接落到下面的
            #   _finalize_room()。后果：只要 defense_gate.py 不可导入
            #   （改名/语法错/路径分裂/被误删），①②③三道防线【全部不执行
            #   且无任何留痕】—— 一个"看起来在运行"的门禁，实际是空的。
            #   这与模块自述的 fail-closed（defense_gate.py:1218）直接矛盾。
            # 修复：显式拒绝 room-end，并给出可执行的排查指引。
            #   唯一合法绕行仍是 --force / DSH_DEFENSE_BYPASS=1（均留痕）。
            return {
                "ok": False,
                "room": name,
                "gate": "DEFENSE_ERROR",
                "error": ("【三大防线】defense_gate.py 不可导入，按[失效关闭]"
                          "拒绝 room-end —— 绝不在防线缺失时静默放行。"),
                "detail": repr(_e_imp),
                "message": ("=== 三大防线不可用（拒绝下线）===\n"
                            "房间: %s\n原因: defense_gate.py 导入失败 %r\n\n"
                            "这通常意味着：文件被改名/删除、语法错误、"
                            "或 tools/ 目录不完整。\n"
                            "请先修复：python defense_gate.py status\n"
                            "确需放行：--force 或 DSH_DEFENSE_BYPASS=1（会留痕）。"
                            % (name, _e_imp)),
            }
        except Exception as _e_other:
            # 非导入类异常已在上面 check_room_defense 的调用处单独处理，
            # 这里兜底同样 fail-closed（原先也是静默 pass）。
            return {
                "ok": False,
                "room": name,
                "gate": "DEFENSE_ERROR",
                "error": "【三大防线】校验链路异常，按[失效关闭]拒绝 room-end。",
                "detail": repr(_e_other),
                "message": ("=== 三大防线链路异常（拒绝下线）===\n"
                            "房间: %s\n异常: %r\n\n"
                            "确需放行：--force 或 DSH_DEFENSE_BYPASS=1（会留痕）。"
                            % (name, _e_other)),
            }
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
    # ── 【BUG-观察-8 修复】去掉 `if auto:` 门槛，统一走 _sync_sw_state_file ──
    # sw_state.json 是纯派生缓存，与事实不符时无条件回写（不再要求 --auto），
    # 避免"用户不主动跑 lock-doctor --auto 就永远不同步"。
    stale = None
    try:
        with open(_SW_STATE_FILE, "r", encoding="utf-8") as f:
            stale = json.load(f)
    except Exception:
        pass
    _sync = _sync_sw_state_file(running, pids, writer="lock-doctor")
    if stale is not None or _sync.get("corrected"):
        report["findings"].append({
            "check": "sw_state_sync", "file_running": _sync.get("file_running"),
            "real_running": _sync.get("real_running"),
            "real_count": _sync.get("real_count"), "real_pids": _sync.get("real_pids"),
            "file_age_seconds": _sync.get("file_age_seconds"),
            "mismatch": _sync.get("mismatch"),
        })
        if _sync.get("corrected"):
            report["fixed"].append(
                "回写 sw_state.json（真实进程数=%d）" % _sync.get("real_count", 0))

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
    # ── 【BUG-观察-7 修复】未持有锁时也要清残留的 prev_owner 标志 ──────────
    # 背景：_gate_sw_unlock()（在 finally 里）通常已经先把 owner 清成 null，
    #   于是小屋 close-all 之后调用的 sw-release 永远走这个"未持有锁"分支，
    #   而该分支原先直接 return —— 从不碰 prev_owner。
    #   结果：owner=null 但 prev_owner 残留 + sw_by_room=true →
    #   队列被 sw_busy 永久阻塞。这正是竞速小车任务的现场。
    # 修复：只要 prev_owner 是调用者自己、且 SW 进程确实没在跑，
    #   就把审计标志一起清掉（lock-doctor 之外的第二道自愈）。
    _purged = []
    try:
        if lk.get("prev_owner") == name and not _sw_running():
            lk["prev_owner"] = None
            if lk.get("sw_by_room"):
                lk["sw_by_room"] = False
            lk["purged_at"] = time.time()
            lk["purged_by"] = "sw-release"
            lk["purged_actions"] = ["prev_owner=%r 随 sw-release 清除" % name]
            save_state(state)
            _purged.append("prev_owner=%r 已清（SW 进程未运行）" % name)
    except Exception:
        pass
    return {"ok": True, "released": None, "owner": lk.get("owner"),
            "orphan_purged": _purged,
            "note": "该房间未持有 SW 锁" + ("（已顺带清理残留标志）" if _purged else "")}


def cmd_close_all(quit=False):
    """关闭全部 SolidWorks 并释放 SW 锁（流程清场用）。

    为什么要在这里提供：
      persona / SKILL 里写的是「每波前用 close-all 清场」，但 close-all 只在
      sw_bridge.py 里，mode_gate.py 没有 → 流程中按文档调用会得到「未知命令」，
      表现为"SW 只能确认无进程，没法用命令清场"。
      本命令 = sw_bridge.close-all + 释放本机全部房间的 SW 锁（同一件事的两半）。

    ── 【Bug⑧ 修复】默认语义改为【保活】：只关文档，不结束 SW 进程 ─────────
    现场缺陷：三个并行房间共用同一台 SW（FIFO 排队），任一房间调 close-all
    就会 Quit + taskkill 结束进程，其它房间正在进行的 RPC 连接【立即失效】
    （主代理观察到 SW PID 频繁变化：4148→30860→18388→23448→472→34744，
    每次都是冷启动）。并行模式实际退化成"串行 + 冷启动"。

    现在的分级语义：
      · 默认（quit=False）→ 只调 SldWorks.CloseAllDocuments，SW 进程【保活】，
        下一个房间/下一个零件直接复用，无需重新 Dispatch；
      · --quit           → 走原有完整的退出流程（Quit + taskkill + 轮询确认）。
    SW 锁【始终】释放（那是"使用权"层面的清场，与进程生死无关）。
    """
    out = {"ok": True, "command": "close-all", "steps": [],
           "keepalive": (not quit), "quit": bool(quit)}
    # ① 关闭 SW（默认只关文档、保活）
    try:
        import subprocess as _sp
        import sys as _sys
        import os as _os
        _bridge = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                                "sw_bridge.py")
        _cmdline = [_sys.executable, _bridge, "close-all"]
        if quit:
            _cmdline.append("--quit")
        _r = _sp.run(_cmdline,
                     capture_output=True, text=True, encoding="utf-8",
                     errors="replace", timeout=120)
        _txt = (_r.stdout or "").strip()
        out["steps"].append({"step": ("sw_bridge close-all --quit" if quit
                                      else "sw_bridge close-all (keepalive)"),
                             "ok": _r.returncode == 0,
                             "detail": _txt[-400:] if _txt else ""})
    except Exception as e:
        out["steps"].append({"step": "sw_bridge close-all", "ok": False,
                             "error": repr(e)})
    # ② 释放所有房间的 SW 锁（清场后不应再有独占者）
    try:
        st = load_state()
        _rel = []
        for _room, _v in (st.get("rooms") or {}).items():
            try:
                if _v.get("sw_lock") or _v.get("lock"):
                    _v.pop("sw_lock", None)
                    _v.pop("lock", None)
                _rel.append(_room)
            except Exception:
                continue
        st["sw_lock"] = {"owner": None, "queue": []}
        save_state(st)
        out["steps"].append({"step": "release_sw_locks", "ok": True,
                             "rooms": _rel})
    except Exception as e:
        out["steps"].append({"step": "release_sw_locks", "ok": False,
                             "error": repr(e)})
    # ③ 复查进程状态 —— 两种语义下"预期结果"是相反的，不能混为一谈
    #   · --quit（旧行为）：预期【无】SLDWORKS.exe，残留即失败；
    #   · 默认保活：预期【仍有】SLDWORKS.exe（这正是"没打断别的房间"的证据），
    #     进程没了反而要提示"下次调用需冷启动"（不判失败：文档已关干净，
    #     保活是"尽量"，SW 也可能因自身原因退出）。
    try:
        import subprocess as _sp2
        _r2 = _sp2.run(["tasklist", "/FI", "IMAGENAME eq SLDWORKS.exe"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=20)
        _remain = "SLDWORKS.EXE" in (_r2.stdout or "").upper()
        out["sw_running"] = bool(_remain)
        if quit:
            out["steps"].append({"step": "verify_exited", "ok": not _remain,
                                 "detail": ("仍有残留进程" if _remain
                                            else "进程已全部退出")})
            if _remain:
                out["ok"] = False
                out["hint"] = "SW 进程未完全退出，稍后重试或手动结束。"
        else:
            out["steps"].append({"step": "verify_keepalive", "ok": True,
                                 "detail": ("SW 进程保持运行（保活生效，"
                                            "其它房间的 RPC 连接不受影响）"
                                            if _remain else
                                            "SW 进程已自行退出（文档已关干净；"
                                            "下次调用会自动冷启动）")})
            if not _remain:
                out["note"] = ("保活未生效（SW 已退出），下次建模需重新 Dispatch；"
                               "不影响正确性，只影响启动耗时。")
    except Exception as e:
        out["steps"].append({"step": "verify_exited", "ok": False,
                             "error": repr(e)})
    return out


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
    # ── 【BUG-观察-7 修复】查询时顺手清孤儿锁（prev_owner 残留会永久阻塞队列）──
    # 背景：prev_owner 的清理原先只写在 _purge_orphan_lock() 里，而调用它的只有
    #   sw-request 与 lock-doctor --auto。小屋 close-all 关掉 SW 后走的是
    #   sw-status / sw-release 两条路径，都不清 prev_owner →
    #   owner=null 但 prev_owner 残留 + sw_by_room=true →
    #   队列里所有房间一律 sw_busy:true，永久阻塞，只能靠人工跑 lock-doctor。
    # 修复：sw-status 是最高频的旁路查询，在这里做"查询即自愈"。
    orphan = _purge_orphan_lock(state, requester="sw-status")
    if orphan.get("purged"):
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
    # ── 【BUG-观察-8 修复】查询时把 sw_state.json 与真实进程对齐（查询即自愈）──
    # 背景：sw_state.json 的刷新原先只靠一个默认根本没启动的常驻监控
    #   （sw_monitor.pid 不存在），纠正逻辑又被关在 lock-doctor --auto 里。
    #   于是 sw_state.json 可能长期记录"SW 未运行"而 SLDWORKS.exe 实际在跑
    #   （或反之）→ sw_bridge._sw_locked_by_other() 读到陈旧文件就误判"被占用"，
    #   传导成假忙/假闲甚至队列阻塞（与 BUG-观察-7 合流）。
    # 修复：sw-status 已经探测出真实进程数，顺手比对并回写派生缓存。
    _sw_sync = _sync_sw_state_file(running, pids, writer="sw-status")
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
            # 【BUG-观察-7】本次查询清掉了哪些孤儿锁标志
            "orphan_purged": orphan.get("actions", []),
            # 【BUG-观察-8】sw_state.json 与真实进程的对账/纠正结果
            "sw_state_sync": _sw_sync,
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
                  "archive-list [房间名]": "【R8】列出归档批次与文件指纹"
                                              "（只读；归档≠最新版，回填前先看）",
                  "archive-restore <房间> [--batch B] [--part 关键字] [--force]":
                      "【R8/B-25】按指纹仲裁回填归档产物：默认只补缺、"
                      "归档件更旧时拒绝覆盖（--force 才越过，且先备份）",
                  "room-heartbeat <房间名>": "心跳保活（【Bug-23】已非必需，仅兼容）",
                  "room-report <房间> <阶段> <详情>": "进度旁路上报（小屋可调）",
                  "room-stage <房间> [阶段] [详情]": "【Bug⑨】长任务推进阶段"
                                                       "（默认 validating；幂等、不覆盖"
                                                       " done/failed、失败不阻断）",
                  "room-report-read [房间名]": "旁路读取小屋进度（只读）",
                  "room-artifact <房间> <文件> [零件]": "【Bug-16/17】登记房间产物归属",
                  "room-status": "所有房间精确状态（只读；含平台权威状态）",
                  "platform-sync <房间> <running|inactive|missing>":
                      "【Bug-23】手工覆盖平台状态（保留；但不要再拿它造假心跳）",
                  "room-board [--room X] [--json]": "【阶段C】唯一权威看板"
                                                    "（别名 board）：state=starting/"
                                                    "running/waiting_sw/idle/done/"
                                                    "failed/dead/unbound，"
                                                    "含 safe_to_restart/next_action",
                  "platform-refresh": "【阶段C】把插件注册表 platform_agents.json"
                                      "合成进 platform_status.json（人工记录优先）",
                  "whoami <房间名> [--session-id <sid>]": "子代理自查身份",
                  "confirm-part <房间> <零件>": "C模式：用户确认后授权该零件",
                  "sw-request/sw-wait/sw-release": "SW 使用权排队/等待/释放",
                  "sw-status": "SW 锁与进程状态（只读）",
                  "stale-artifacts [目录] [--quarantine]": "扫描/隔离上一轮遗留件",
                  "residue-check [--clean] [--force]": "子对话残留体检与清理",
                  "rooms-reset": "清空房间/子代理/锁/报告",
                  "close-all [--quit]": "【Bug⑧】默认只关文档、SW 进程保活；"
                                       "--quit 才真正退出 SW",
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
    elif cmd in ("archive-list", "archive_ls"):
        # ── 【R8 / B-20·B-25】查看归档批次与指纹（只读）──────────────────
        _al_room = None
        for _i, _a in enumerate(args[1:]):
            if not _a.startswith("--"):
                _al_room = _a
                break
        result = cmd_archive_list(room=_al_room)
    elif cmd in ("archive-restore", "archive_restore"):
        # ── 【R8 / B-25】按指纹仲裁回填归档产物 ───────────────────────────
        # 用法: archive-restore <房间> [--batch <批次名>] [--part <关键字>]
        #                      [--force] [--dry-run]
        # 默认只回填【目标不存在】的文件；归档件比现有文件旧时【拒绝覆盖】
        #   （这正是 B-20 把过门新版覆盖成旧版的事故），确需覆盖加 --force。
        _ar_pos = [a for a in args[1:] if not a.startswith("--")]
        _ar_batch = None
        _ar_part = None
        for _i, _a in enumerate(args[1:]):
            if _a == "--batch" and _i + 2 < len(args):
                _ar_batch = args[_i + 2]
            if _a == "--part" and _i + 2 < len(args):
                _ar_part = args[_i + 2]
        if _ar_batch in _ar_pos:
            _ar_pos = [p for p in _ar_pos if p != _ar_batch]
        if _ar_part in _ar_pos:
            _ar_pos = [p for p in _ar_pos if p != _ar_part]
        if not _ar_pos:
            result = {"ok": False,
                      "error": "用法: archive-restore <房间> [--batch <批次>] "
                               "[--part <关键字>] [--force] [--dry-run]"}
        else:
            result = cmd_archive_restore(
                _ar_pos[0], batch=_ar_batch, part=_ar_part,
                dry_run=("--dry-run" in args),
                force=("--force" in args))
    elif cmd == "room-end":
        # 【Bug-25/27】支持 --force 强制释放 SW 锁（小屋忘记 close-all 时兜底）
        _re_args = [a for a in args[1:] if not a.startswith("--")]
        _re_force = ("--force" in args) or ("-f" in args)
        result = cmd_room_end(_re_args[0] if _re_args else "小屋",
                              force=_re_force)
    elif cmd in ("room-fail", "room-restart"):
        result = cmd_room_fail(args[1] if len(args) > 1 else "小屋")
    elif cmd in ("interrupt-agent", "interrupt_agent", "kill-agent", "subagent-kill"):
        # ── 【BUG-06 修复】不再返回"未知命令"那种令人无从下手的错误 ──────────
        # 实测：模型按旧文档调 mode_gate.py interrupt-agent <id>，得到
        #   {"ok": false, "error": "未知命令: interrupt-agent"}，于是它以为
        #   自己没有清理手段，反复重试。
        # 事实：清理子代理是【DSH 工具 interrupt_agent】的职责，命令行做不到。
        # 这里给出明确的可执行替代，而不是一句"未知命令"。
        result = {
            "ok": False,
            "error": ("mode_gate.py 【没有】interrupt-agent 命令 —— "
                      "清理子代理要用 DSH 工具 interrupt_agent <target>，不是命令行。"),
            "why": ("本脚本只管理【门禁状态与 SW 锁】，不控制平台子代理生命周期；"
                    "fork 出来的建模小屋不是 Team member，"
                    "interrupt_agent 对它们通常也不生效。"),
            "how": [
                "① 只是想让房间可重开 → 直接 python mode_gate.py room-start <房间>（会清旧绑定）",
                "② 已结束(completed/failed)的房间 → python mode_gate.py room-end/room-fail <房间>",
                "③ 想停掉 Team member（spawn_teammate 建的）→ 用工具 interrupt_agent",
                "④ 核对现状 → python mode_gate.py room-board --json",
            ],
        }
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
    elif cmd in ("room-board", "board"):
        # 【阶段 C】唯一权威看板：状态由代码 + 平台事实推导，不靠主对话猜。
        # 用法: mode_gate.py room-board [--room 房间名] [--json]
        #   --json 只是显式表达"我要 JSON"；本命令【始终】输出 JSON。
        _rb_room = None
        _i = 1
        while _i < len(args):
            _a = args[_i]
            if _a in ("--room", "-r", "--room-name") and _i + 1 < len(args):
                _rb_room = args[_i + 1]
                _i += 2
                continue
            if not _a.startswith("-") and _rb_room is None:
                _rb_room = _a
            _i += 1
        result = cmd_room_board(room=_rb_room, as_json=True)
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
    elif cmd == "platform-refresh":
        # 【阶段 C】把插件自动注册表合成进 platform_status.json（人工记录优先）
        result = cmd_platform_refresh()
    elif cmd == "subagent-status":
        result = cmd_subagent_status()
    elif cmd == "room-report":
        result = cmd_room_report(args[1] if len(args) > 1 else "unknown",
                                 args[2] if len(args) > 2 else "unknown",
                                 " ".join(args[3:]) if len(args) > 3 else "")
    elif cmd == "room-stage":
        # 【Bug⑨】长任务（物理校核/疲劳/领域/看图）在房间上下文里推进阶段。
        # 用法（阶段可省略，省略即 validating）：
        #   room-stage <房间名> [阶段] [详情...]
        #   room-stage --room <房间名>              （从 DSH_ROOM 取房间亦可）
        # 阶段名必须 ∈ REPORT_STAGES；不合法时【不静默当房间名】，而是明确报错，
        #   否则 room-stage 房间 validatingX 会被误当成"房间=validatingX"。
        _rs_rest = [a for a in args[1:] if not a.startswith("--")]
        _rs_room = (os.environ.get("DSH_ROOM") or "").strip()
        _rs_stage = VALIDATING_STAGE
        _rs_detail = ""
        _rs_bad_stage = None
        if _rs_rest:
            if _rs_rest[0] in REPORT_STAGES:
                _rs_stage = _rs_rest[0]
                _rs_detail = " ".join(_rs_rest[1:])
            else:
                _rs_room = _rs_room or _rs_rest[0]
                if len(_rs_rest) >= 2:
                    if _rs_rest[1] in REPORT_STAGES:
                        _rs_stage = _rs_rest[1]
                        _rs_detail = " ".join(_rs_rest[2:])
                    else:
                        _rs_bad_stage = _rs_rest[1]
                elif len(_rs_rest) > 1:
                    _rs_detail = " ".join(_rs_rest[1:])
        if _rs_bad_stage is not None:
            result = {
                "ok": False, "command": "room-stage",
                "room": _rs_room, "stage": _rs_bad_stage,
                "error": "未知阶段: %r" % _rs_bad_stage,
                "allowed_stages": list(REPORT_STAGES),
                "hint": ("用法: room-stage <房间名> [阶段] [详情...]，"
                         "阶段缺省=validating。"),
            }
        else:
            result = cmd_room_stage(_rs_room, _rs_stage, _rs_detail)
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
        # ── 【BUG-9 修复】自查身份时【顺手】把房间的 subagent 绑定补上 ────
        # 主对话若忘了 subagent-assign，room-status 会显示 subagent=null，
        # 小屋上报可能失败。这里只在【尚未绑定】时补，不覆盖主对话的显式指派。
        try:
            _sb = cmd_whoami_self_bind(_room)
            if isinstance(result, dict):
                result["subagent_binding"] = _sb
                if _sb.get("action") == "bound":
                    result["self_bound"] = True
        except Exception:
            pass
    elif cmd in ("wait-rooms", "wait-rooms-done"):
        # ── 【BUG-03 修复】阻塞等待房间推进，替代"父代理反复轮询磁盘" ────────
        # 缺陷（2026-10-29 实测）：fork 出来的子代理【不是 Team member】，
        #   wait_agent 恒返回 noProgress(no-active-peer)，list_agents 也看不到它们。
        #   父代理只能每 5~7 分钟轮询一次 room-board / parts 目录，
        #   本次轮询数十次，烧掉大量 token 且延迟很高。
        # 本命令用【单个命令行进程原地阻塞】替代这些轮询：
        #   · 只轮询状态文件（零 token），到点一次性返回结论；
        #   · 默认等全部房间离开 active（done/failed/dead）或超时；
        #   · 中途房间状态发生变化也会提前返回，便于父代理及时处置。
        # 用法:
        #   python mode_gate.py wait-rooms "<房间1>,<房间2>" [--timeout 900] [--interval 10]
        #   python mode_gate.py wait-rooms --all-active --timeout 900
        _rargs = [a for a in args[1:] if not a.startswith("--")]
        _timeout = 900.0
        _intv = 10.0
        _all_active = "--all-active" in args
        for _i, _a in enumerate(args):
            if _a == "--timeout" and _i + 1 < len(args):
                try:
                    _timeout = float(args[_i + 1])
                except Exception:
                    pass
            elif _a == "--interval" and _i + 1 < len(args):
                try:
                    _intv = float(args[_i + 1])
                except Exception:
                    pass
        _targets = []
        if _all_active:
            _st0 = load_state()
            _targets = [n for n, v in (_st0.get("rooms") or {}).items()
                        if isinstance(v, dict) and v.get("active")]
        elif _rargs:
            _targets = [x.strip() for x in _rargs[0].replace("，", ",").split(",")
                        if x.strip()]
        if not _targets and not _all_active:
            result = {"ok": False,
                      "error": "用法: wait-rooms \"<房间1>,<房间2>\" [--timeout 900] "
                               "[--interval 10]；或用 --all-active 等全部 active 房间"}
        else:
            result = cmd_wait_rooms(_targets, timeout=_timeout, interval=_intv,
                                    all_active=_all_active)
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
    # ══ 【第4项】SW 健康看护（超阈值立即关 SW）══════════════════════════
    elif cmd == "sw-guard-start":
        result = cmd_sw_guard_start(args[1] if len(args) > 1 else None)
    elif cmd == "sw-guard-stop":
        result = cmd_sw_guard_stop()
    elif cmd in ("sw-guard-status", "sw-guard"):
        result = cmd_sw_guard_status()
    elif cmd == "close-all":
        # 【Bug⑧】默认保活（只关文档、不退出 SW）；--quit 走完整退出流程
        result = cmd_close_all(quit=("--quit" in args))
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