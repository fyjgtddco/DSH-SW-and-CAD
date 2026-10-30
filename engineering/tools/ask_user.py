#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ask_user.py —— 子代理专用的阻塞式提问 CLI
==========================================
用途：让【子代理】直接在【右侧子代理面板】向用户提问并阻塞等待答案。

背景（已核实 DSH 源码）：
  · dsh-user-questions 的 ask() 会校验 agents.roots().includes(agent)，
    子代理不在 roots 中 → 必然抛 DELEGATED_CALLER，无法自己调 ask_user_question。
  · host-apiproxy 的 provider 用 sessionId = request.agent?.id 决定帧路由，
    应答校验 matchesQuestions() 也要求 sessionId 与 pending 一致。
  · 因此提问必须由"能通过校验的 root"承载 —— 由本插件 host 半代为发起。

本 CLI 的工作方式（阻塞等待，不结束回合）：
  1) POST /dsh-engineering-ui/ask-child，带上本子代理的 sessionId + 问题/选项
  2) host 代表本子代理发起原生 ask()，返回 pendingId
  3) 本脚本【原地轮询】等待用户在子代理面板作答
  4) 拿到答案后打印 JSON 并退出 —— 调用方从 stdout 读到答案，继续原逻辑

这与已废弃的 followup 方案的本质区别：
  followup 把答案变成"下一轮新消息"，子代理会当成新任务；
  本方案答案回到【发起提问的那次调用栈】，子代理直接从工具返回值继续。

用法：
  python ask_user.py --room <房间名> --child <子代理sessionId> \
      --question "以下是零件参数，要用吗？" \
      --option "确认，开始建模" --option "需要修改"

  或从 JSON 文件读取问题（推荐，避免命令行转义问题）：
  python ask_user.py --room <房间名> --child <sessionId> --spec questions.json

输出（stdout，单行 JSON）：
  {"ok": true, "answers": [{"id":"q1","selected":["确认，开始建模"],"custom":""}]}
  {"ok": false, "error": "..."}
"""
import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


NL_S = chr(10)          # 换行（避免源码里出现字面量换行导致的转义问题）
NL_B = b"\n"


def emit(obj):
    r"""【Bug2 修复】稳健 JSON 输出，杜绝中文 GBK 乱码。

    本脚本是【小屋唯一的提问通道】，输出里的房间名/问题/用户选项全是中文。
    原实现直接 print(json.dumps(..., ensure_ascii=False))：一旦 stdout 被
    pwsh 以 GBK 代码页捕获，答案就会以乱码回传给小屋 —— 小屋据此判断
    "用户选了什么"会出错，是本轮"报告/提问频道疑似未完全生效"的根因之一。

    修复：优先以 UTF-8 字节直写（绕开控制台代码页）；失败则退回
    ensure_ascii=True 的纯 ASCII 转义（任何代码页都能无损还原）。
    """
    try:
        text = json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:
        text = json.dumps({"ok": False, "error": "serialize failed"}, ensure_ascii=True)
    try:
        _buf = getattr(sys.stdout, "buffer", None)
        if _buf is not None:
            _buf.write(text.encode("utf-8", "replace"))
            _buf.write(NL_B.encode("utf-8"))
            _buf.flush()
            return
    except Exception:
        pass
    try:
        sys.stdout.write(text + NL_S)
        sys.stdout.flush()
    except UnicodeEncodeError:
        try:
            sys.stdout.write(json.dumps(obj, ensure_ascii=True, indent=2) + NL_S)
            sys.stdout.flush()
        except Exception:
            pass


# ── 【2026-10-29 G03 修复】默认 host 不能写死 3080 ────────────────────────
# 实测：DSH Web GUI 实际监听 64488 / 63211 / 55660…（每次启动端口都变），
#   而本脚本默认 POST 到 127.0.0.1:3080 → WinError 10061（无法连接），
#   子代理提问全部失败，只能父代理代问（还要多一轮往返）。
# 现在按下面的顺序解析 host：
#   ① --host 显式参数（调用方最清楚）
#   ② 环境变量 DSH_HOST
#   ③ DSH_WEB_URL（DSH 宿主注入的真实 GUI 地址，最可靠）
#   ④ 兼容旧口径：3080 / 19387（老版本曾用固定端口）
# 并在连接失败时【快速失败 + 给出降级指引】，不再傻等到 540s 超时。
def _resolve_default_host():
    for _env in ("DSH_HOST", "DSH_WEB_URL", "DSH_WEB_URL_ALT"):
        _v = (os.environ.get(_env) or "").strip()
        if _v.startswith("http://") or _v.startswith("https://"):
            return _v.rstrip("/")
    return "http://127.0.0.1:3080"


DEFAULT_HOST = _resolve_default_host()
PREFIX = "/dsh-engineering-ui"

#: 连接失败时最多尝试的候选 host（快速探测，每个 ≤2s，总 ≤6s）
FAST_FAIL_HOSTS = ("DSH_HOST", "DSH_WEB_URL")


def _candidate_hosts(explicit=None):
    """按优先级列出候选 host（去重，保序）。"""
    vals = []
    if explicit:
        vals.append(str(explicit).strip().rstrip("/"))
    for _env in FAST_FAIL_HOSTS:
        _v = (os.environ.get(_env) or "").strip()
        if _v.startswith("http://") or _v.startswith("https://"):
            vals.append(_v.rstrip("/"))
    for _v in ("http://127.0.0.1:3080", "http://127.0.0.1:19387"):
        vals.append(_v)
    out = []
    for v in vals:
        if v and v not in out:
            out.append(v)
    return out


_BRIDGE_DOWN_HINT = (
    "宿主代问桥不可用（/dsh-engineering-ui/ask-child 连不上）。"
    "这不是你的错，是插件 host 半没在对应端口上跑。降级做法（必须照做）：\n"
    "  ① 把问题列表用 send_message 发给父代理（主对话），请它用 ask_user_question 代问用户；\n"
    "  ② 父代理把用户答复回传给你后，你再用 mode2_flow.py answer-question <seq> \"<答复>\" 逐条落账；\n"
    "  ③ 在上述两步完成前，不要把问题当成'已问过'，也不要编造用户回答。"
)

def _post(url, payload, timeout=30):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"content-type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", "replace")
    return json.loads(body)


def _get(url, timeout=30):
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", "replace")
    return json.loads(body)


def _writeback_answer(room, questions, ans, pending_id, child):
    """【Bug#8 修复】把面板作答结果回写进 pending.json 的 answers 字段。

    目的：让 mode_gate.py confirm-part 能校验"用户确实在面板上点过"，
      使 C 模式逐件确认具备完整可追溯性（而非仅靠主对话的独立授权）。

    写入结构：
      {"answers": {"<零件名/题目摘要>": {
          "selected": [...], "custom": "...",
          "pendingId": "...", "child": "...",
          "ts": 1234567890.0, "time": "2026-09-15 11:20:00"}}}

    容错：任何异常都不影响主流程（作答结果照常返回给调用方）。
    """
    if not room:
        return
    try:
        import time as _t
        path = os.path.join(_TOOLS_DIR, "reports",
                            _safe_room(room) + ".pending.json")
        data = {}
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f) or {}
            except Exception:
                data = {}
        answers = data.get("answers") or {}
        # 从作答结果里提取选中的标签
        sel = []
        custom = ""
        try:
            for a in (ans.get("answers") or []):
                sel.extend(a.get("selected") or [])
                if a.get("custom"):
                    custom = str(a.get("custom"))
        except Exception:
            pass
        # ── 【Bug#2 修复】键必须包含【零件名】，否则 confirm-part 永远匹配不上 ──
        # 现象（测试部实测）：
        #   confirm-part "结构件" "立柱" → answer_evidence=null、
        #   audit="未读到面板作答痕迹"，但 pending.json 里明明有作答记录。
        # 根因：原实现优先用 `header` 作键，而小屋提问时写的是
        #   --header "零件确认"（通用标题！），零件名只在 --question 里。
        #   → answers 的键变成 "零件确认"/"确认，开始建模" 这类泛化标签，
        #     完全不含零件名 → confirm-part 按零件名（立柱/大臂）去查必然 miss。
        # 修复：键的优先级改为
        #   ① 从 question 文本中提取的【零件名】（最可靠）
        #   ② header
        #   ③ id / 摘要兜底
        #   并且【同时】写入多个别名键，让各种匹配策略都能命中。
        key = ""
        part_guess = ""
        try:
            q0 = (questions or [{}])[0]
            _qtext = str(q0.get("question") or "")
            # 提取零件名：优先取【】/（）内的名称（小屋常用写法）
            import re as _re2
            for _pat in (r"[【\[]([^】\]]+)[】\]]", r"[（(]([^）)]+)[）)]"):
                _m = _re2.search(_pat, _qtext)
                if _m:
                    _cand = _m.group(1).strip()
                    # 排除明显不是零件名的内容（纯数字/规格/材质）
                    if _cand and not _re2.fullmatch(r"[\d\.\sA-Za-z\-]+", _cand):
                        part_guess = _cand
                        break
            # 若括号里没找到，尝试"建模<名>" / "用于<名>" 句式
            if not part_guess:
                _m2 = _re2.search(r"(?:建模|用于|确认)\s*([\u4e00-\u9fa5A-Za-z0-9_\-]{2,20})",
                                  _qtext)
                if _m2:
                    part_guess = _m2.group(1).strip()
        except Exception:
            part_guess = ""
        try:
            q0 = (questions or [{}])[0]
            key = part_guess or str(q0.get("header") or "").strip() \
                  or str(q0.get("id") or "").strip()
            if not key:
                key = str(q0.get("question") or "")[:40]
        except Exception:
            key = part_guess or ""
        if not key:
            key = "answer_%d" % int(_t.time())
        rec = {
            "selected": sel,
            "custom": custom,
            "pendingId": pending_id,
            "child": child,
            "ts": _t.time(),
            # 【Bug#8 修复】原写成 _t.time.strftime(...) —— _t 是 time 模块，
            #   而 _t.time 是【函数】不是结构体，调 .strftime 必抛 AttributeError，
            #   又被外层 except 静默吞掉 → 回写永远失败且无任何报错。
            #   正确写法：_t.strftime(...) 或 time.localtime() 转换。
            "time": _t.strftime("%Y-%m-%d %H:%M:%S", _t.localtime()),
        }
        answers[key] = rec
        # ── 【Bug#2 修复】多别名登记 ──────────────────────────────────────
        # 让 confirm-part 无论用【零件名 / header / 问题摘要 / 选中标签】
        # 哪种键去查都能命中，避免"证据明明在却读不到"。
        _aliases = []
        if part_guess:
            _aliases.append(part_guess)
        try:
            _q0 = (questions or [{}])[0]
            _h = str(_q0.get("header") or "").strip()
            if _h:
                _aliases.append(_h)
            _qtext_full = str(_q0.get("question") or "").strip()
            if _qtext_full:
                _aliases.append(_qtext_full[:40])
        except Exception:
            pass
        for _al in _aliases:
            if _al and _al not in answers:
                answers[_al] = dict(rec)
        # 同时按"选中标签"登记一份，供按零件名精确匹配
        for s in sel:
            answers[str(s)] = dict(rec)
        data["answers"] = answers
        data["last_answer_at"] = rec["time"]
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        # ── 【Bug#8 修复】回写失败必须留痕，不能再静默吞掉 ──────────────
        # 原实现 except: pass，导致"回写永远失败"却毫无痕迹，
        #   排查时只能看到 confirm-part 的 audit 说"未读到作答痕迹"。
        # 现在把失败原因写到 tools/reports/_ask_user_writeback_error.log，
        #   便于定位；仍不影响主流程（答案照常返回）。
        try:
            _log = os.path.join(_TOOLS_DIR, "reports", "_ask_user_writeback_error.log")
            os.makedirs(os.path.dirname(_log), exist_ok=True)
            with open(_log, "a", encoding="utf-8") as _lf:
                _lf.write("%s  room=%s  error=%r\n"
                          % (__import__("time").strftime("%Y-%m-%d %H:%M:%S"),
                             room, e))
        except Exception:
            pass




# ══ 【2026-10-29 修复】跨会话去重：同一道题只允许存在一张卡 ══════════════
# 现场事故（会话 ceac3fff）：三个总计子代理同时存活（各自 sessionId 不同），
#   它们各自调 ask_user.py 问同一批问题；而旧去重只查
#     /pending-child?childSessionId=<自己>
#   只能看到"自己的"卡，看不到另外两个子代理刚建的同一张卡 ——
#   于是同一题被登记 3 次，用户看到已经答过的选项反复弹出来。
#
# 修复（三重）：
#   ① 先按【全局】查 pending（rootSessionId=*），跨会话比对题目语义；
#   ② 命中 → 直接复用那张卡（不新建）；
#   ③ 若命中的是【别人的】卡（childSessionId 不同），不阻塞等待自己那张，
#      而是立刻返回 gate=ASK_DUPLICATE_OWNED_BY_PEER + 已答结果/指引，
#      让调用方明白"这题别人已经在问了，别再重复问用户"。
def _global_pending(host):
    """跨会话取全部未结提问（插件支持 rootSessionId=*）。"""
    try:
        return _get("%s/pending-child?rootSessionId=*" % (host + PREFIX), timeout=5)
    except Exception:
        return {}


def _dedup_across_sessions(host, questions, my_child):
    """在【所有子代理】的未结提问里找同一道题。

    Returns: {"hit": bool, "owned_by_me": bool, "pendingId": str|None,
              "peer_child": str|None}
    """
    out = {"hit": False, "owned_by_me": False, "pendingId": None,
           "peer_child": None}
    data = _global_pending(host)
    for p in (data.get("pending") or []):
        pid = p.get("pendingId")
        if not pid:
            continue
        peer_qs = p.get("questions") or []
        if not peer_qs or len(peer_qs) != len(questions):
            continue
        if not _same_question({"questions": peer_qs}):
            continue
        out["hit"] = True
        out["pendingId"] = pid
        out["peer_child"] = p.get("childSessionId")
        out["owned_by_me"] = (str(p.get("childSessionId") or "") == str(my_child))
        return out
    return out



# ══ 【2026-10-29 修复】已答台账：同一道题答过就不再弹 ═══════════════════
# 上面那层只能拦住"同时未结"的重复卡；但实测里还有另一种情况：
#   卡已结算（用户答过了），另一个子代理稍后才问同一道题 → 又弹一次。
# 用户原话："会突然跳出来已经选择过的选项"。
#
# 因此再加一层【持久化已答台账】：
#   · 每次成功拿到答案 → 按"题目语义指纹"落盘（含答案与时间）；
#   · 每次提问前先查台账，命中且未过期 → 直接返回旧答案，不再打扰用户；
#   · TTL 默认 2 小时（够一次完整设计任务），过期自动忽略。
# 台账位置：与 mode_state 同目录（_store.state_dir()/ask_answered.json）。
# ══ 题目文本归一化 / 选项指纹（模块级）═══════════════════════════════════
# 【2026-10-29 修复】这两个函数原先定义在 main() 内部的闭包里，导致
#   【模块级】的已答台账 _ledger_key() 调不到它们 → 指纹恒为 None →
#   台账永远写不进去 → "同一题反复弹出"根本拦不住。
#   现在提到模块级，闭包内直接引用同名模块函数（行为完全一致）。
import re as _re_mod


def _norm_text(s):
    """问题文本归一化：去空白/标点/常见套话，只留实义字符。"""
    t = str(s or "")
    t = _re_mod.sub(r"[\s，。、；：！？,.;:!?~～…\-—_（）()【】\[\]\"'“”‘’]", "", t)
    for w in ("以下参数将用于建模", "以下参数用于", "参数如下", "请确认",
              "要用吗", "可以用吗", "是否确认", "确认开始建模", "开始建模",
              "参数确认", "请选择", "请核对", "核对后", "将用于"):
        t = t.replace(w, "")
    return t


def _opts_of(qs):
    """选项标签集合指纹（每题的选项排序后成元组）。"""
    out = []
    for q in (qs or []):
        labels = []
        for o in (q.get("options") or []):
            if isinstance(o, dict):
                labels.append(str(o.get("label", "")))
            else:
                labels.append(str(o))
        out.append(tuple(sorted(labels)))
    return tuple(out)

ASK_LEDGER_NAME = "ask_answered.json"
ASK_LEDGER_TTL_SEC = 2 * 3600


def _ledger_path():
    """台账路径（优先与门禁状态同目录，取不到则退到本脚本目录）。"""
    try:
        import _store
        _d = _store.state_dir()
    except Exception:
        _d = None
    if not _d:
        _d = os.path.dirname(os.path.abspath(__file__))
    try:
        os.makedirs(_d, exist_ok=True)
    except Exception:
        pass
    return os.path.join(_d, ASK_LEDGER_NAME)


def _ledger_load():
    try:
        p = _ledger_path()
        if not os.path.exists(p):
            return {}
        with open(p, "r", encoding="utf-8-sig") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _ledger_save(data):
    try:
        p = _ledger_path()
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
        return True
    except Exception:
        return False


def _ledger_key(questions):
    """题目语义指纹（与 _same_question 同源的归一化口径）。

    ⚠️ _opts_of() 返回【元组的元组】（每题一个 label 元组），
       不能直接 "|".join —— 之前这里抛 TypeError 被 except 吞掉，
       指纹恒为 None，台账永远写不进去（"同一题反复弹"因此拦不住）。
       现在显式展平。
    """
    try:
        import hashlib
        parts = []
        for q in questions:
            parts.append(_norm_text(q.get("question", "")))
            _o = _opts_of([q])
            _labels = list(_o[0]) if (_o and isinstance(_o[0], (tuple, list))) else []
            parts.append("|".join(_labels))
        raw = "\u0001".join(parts)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    except Exception:
        return None


def _ledger_get(questions):
    """查已答台账；命中且未过期返回答案，否则 None。"""
    key = _ledger_key(questions)
    if not key:
        return None
    data = _ledger_load()
    rec = data.get(key)
    if not isinstance(rec, dict):
        return None
    try:
        age = time.time() - float(rec.get("ts") or 0)
    except Exception:
        return None
    if age > ASK_LEDGER_TTL_SEC:
        return None
    return {"answers": rec.get("answers"), "answered_at": rec.get("at"),
            "age_sec": round(age, 1), "key": key}


def _ledger_put(questions, ans):
    """把答案写进台账（同一指纹覆盖为最新）。"""
    key = _ledger_key(questions)
    if not key:
        return False
    data = _ledger_load()
    # 顺手清过期项，避免无限增长
    now = time.time()
    for k in list(data.keys()):
        try:
            if now - float((data[k] or {}).get("ts") or 0) > ASK_LEDGER_TTL_SEC:
                data.pop(k, None)
        except Exception:
            data.pop(k, None)
    data[key] = {"answers": (ans or {}).get("answers"),
                 "ts": now,
                 "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                 "questions": [q.get("question") for q in questions]}
    return _ledger_save(data)

def _safe_room(room):
    return "".join(c for c in str(room) if c.isalnum() or c in " _-").strip()


_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))


def _resolve_self_session_id():
    r"""解析"我自己的 sessionId"（本子代理的身份）。

    ── 【BUG-01 修复】新增 --child auto ────────────────────────────────────
    缺陷（2026-10-29 竞速小车任务实测）：
      建模小屋需要确认参数时必须走本桥，但 --child 要求填【自己的 sessionId】，
      而 DSH 只把身份放在环境变量 DSH_SESSION_ID 里；提示词又写着
      "--child <自己的 sessionId>"（占位符），子代理无从得知该填什么 ——
      于是"小屋一旦需要确认任何参数就彻底无法继续"。

    修复：--child 接受 auto（或 self/me/current），按下列顺序自动解析：
      ① 环境变量 DSH_SESSION_ID（DSH 注入给子代理的会话 id，最权威）
      ② 环境变量 DSH_AGENT_ID / DSH_CHILD_SESSION_ID（兼容别名）
      ③ mode_state.json 里属于"本进程/本房间"的绑定（whoami 同源口径）
    全部失败返回空串，由调用方给出可执行的报错（而不是静默用错身份）。

    注意：绝不能猜一个"看起来像"的 id —— 猜错会把提问投递给别的会话，
    表现为"提问发出去了但用户看不到/答完不回传"，比直接报错更难排查。
    """
    for _k in ("DSH_SESSION_ID", "DSH_AGENT_ID", "DSH_CHILD_SESSION_ID"):
        _v = str(os.environ.get(_k) or "").strip()
        if _v:
            return _v, _k
    return "", None


def main():
    ap = argparse.ArgumentParser(description="子代理阻塞式提问")
    ap.add_argument("--child", required=True,
                    help=("本子代理的 sessionId；"
                          "填 auto 则自动取 DSH_SESSION_ID（【BUG-01】推荐用法）"))
    ap.add_argument("--room", default="", help="房间名（用于进度上报，可选）")
    ap.add_argument("--question", default="", help="问题文本")
    ap.add_argument("--option", action="append", default=[], help="选项（可多次）")
    ap.add_argument("--spec", default="", help="问题 JSON 文件路径（含 questions 数组）")
    ap.add_argument("--header", default="", help="问题分组标题")
    ap.add_argument("--multi", action="store_true", help="是否多选")
    ap.add_argument("--host", default=DEFAULT_HOST, help="DSH host 地址")
    # ── 【C5 修复】默认超时必须低于 DSH shell 调用上限（600s）──────────────
    # 原默认 1800s（30 分钟）远超外壳上限 → 首次提问必被掐断，
    #   而 host 侧 pending 仍然存活 → 面板堆积多张待答卡，重试又重复登记。
    # 现默认 540s（留 60s 余量），并允许 DSH_ASK_TIMEOUT 环境变量覆盖。
    _def_to = int(os.environ.get("DSH_ASK_TIMEOUT", "540"))
    ap.add_argument("--timeout", type=int, default=_def_to,
                    help="等待用户作答的最长秒数（默认 540，须 < DSH shell 上限 600）")
    ap.add_argument("--interval", type=float, default=2.0, help="轮询间隔秒数")
    args = ap.parse_args()

    # ── 【BUG-01 修复】--child auto → 自动取自己的 DSH_SESSION_ID ──────────
    # 只认这几个明确的占位写法；其他值一律原样使用（保持向后兼容）。
    if str(args.child).strip().lower() in ("auto", "self", "me", "current", "this"):
        _sid, _src = _resolve_self_session_id()
        if not _sid:
            emit({
                "ok": False,
                "gate": "CHILD_SESSION_UNKNOWN",
                "error": ("--child auto 无法自动确定本子代理的 sessionId："
                          "环境变量 DSH_SESSION_ID 未设置。"),
                "why": ("DSH 只在【子代理进程内】注入 DSH_SESSION_ID；"
                        "在普通 shell 里手动跑本脚本时它是空的。"),
                "how": [
                    "① 在子代理里调用时优先用 auto：--child auto",
                    "② 若确实拿不到，请把父代理分配的房间 id 显式传入：",
                    "     --child <你的 sessionId>",
                    "③ 查当前已知会话：python mode_gate.py whoami <房间名>",
                ],
            })
            return 1
        args.child = _sid
        sys.stderr.write("[ask_user] --child auto 解析为 %s (来源: %s)%s"
                         % (_sid, _src, NL_S))

    # 组装问题
    if args.spec:
        try:
            with open(args.spec, "r", encoding="utf-8") as f:
                spec = json.load(f)
            questions = spec.get("questions") if isinstance(spec, dict) else spec
            if not isinstance(questions, list) or not questions:
                raise ValueError("spec 中没有有效的 questions 数组")
        except Exception as e:
            emit({"ok": False, "error": "读取 spec 失败: %s" % e})
            return 1
    else:
        if not args.question:
            emit({"ok": False, "error": "缺少 --question 或 --spec"})
            return 1
        questions = [{
            "id": "q1",
            "question": args.question,
            "header": args.header or "确认",
            "options": [{"label": o} for o in (args.option or ["确认"])],
            "multiSelect": bool(args.multi),
        }]

    # ── 【C5 修复 → Bug#3 强化】登记前先查该子代理是否已有【同题】pending ──
    # 场景：首次提问被外壳 600s 掐断，但 host 侧 pending 仍存活。
    #   重试时若直接再登记，面板会堆出第 2、3 张同样的卡，用户体验极差，
    #   而且用户答了旧卡、脚本却在等新卡 → 看起来"卡死"。
    #
    # ── 【Bug#3 修复】旧实现要求问题文本【逐字完全一致】，实测必漏 ────────
    # 测试反馈：壳体机架"底座安装板"一度堆了 5 张卡。
    # 根因：小屋每次重试时措辞略有差异 ——
    #     "以下参数将用于建模底座安装板，要用吗？"
    #     "确认开始建模底座安装板？"
    #     "底座安装板参数确认"
    #   三者语义相同但字符串不同，逐字比对必然返回 False
    #   → 每个都当成新问题登记 → 卡片堆积。
    #
    # 修复策略（两层，从严到宽）：
    #   ① 语义指纹匹配（主路径）：把问题文本归一化后提取
    #      【归一化文本 + 选项集合 + 关键数字】三者全等即视为同一张卡；
    #   ② 归一化后互相包含 / 数字集合一致（措辞微调但主体与尺寸相同）。
    #   硬约束：选项集合必须完全一致才可合并 ——
    #     选项是"用户能选什么"的契约，放宽会把 A 零件的选项套到 B 零件上。
    import re as _re
    # _norm_text / _opts_of 已提到模块级（台账复用同一口径），这里不再重复定义

    # ── 【Bug#3 修复·防误合并】"修改类"问句不参与合并 ──────────────────
    # 反例：'底座安装板 厚度改为 12mm 确认' 与 '底座安装板 参数确认'
    #   归一化后都含"底座安装板"，会被第②层"数字一致/互相包含"误判为同题。
    #   但前者是【用户已要求修改后的再次确认】，语义上是新的一轮提问，
    #   若复用旧卡片，用户点的是"旧参数"的选项 → 确认内容与实际建模不符。
    #   这类必须登记【新卡】，绝不能与旧卡合并。
    _MODIFY_KW = ("改为", "改成", "修改", "调整", "改为为", "变更",
                  "重做", "重新", "换成")

    def _is_modify_question(s):
        t = str(s or "")
        return any(k in t for k in _MODIFY_KW)

    def _digits_of(s):
        """提取文本里的关键数字（尺寸），用于判定是否为同一零件。"""
        return sorted(_re.findall(r"\d+(?:\.\d+)?", str(s or "")))

    # _opts_of 已提到模块级

    def _semantic_key(qs):
        """语义指纹：(归一化问题文本元组, 选项集合, 数字集合)。三者全等=同题。"""
        norm = tuple(_norm_text(q.get("question", "")) for q in (qs or []))
        digits = tuple(_digits_of(" ".join(str(q.get("question", "")) for q in (qs or []))))
        return (norm, _opts_of(qs), digits)

    def _same_question(p):
        """判断 host 侧 pending 与本脚本要问的题是否【语义相同】（Bug#3）。"""
        try:
            qs = p.get("questions") or []
            if len(qs) != len(questions):
                return False
            # ── 硬约束 0：修改类问句不合并（否则会复用旧参数的卡）──
            _new_is_mod = any(_is_modify_question(q.get("question", ""))
                              for q in questions)
            _old_is_mod = any(_is_modify_question(q.get("question", ""))
                              for q in qs)
            if _new_is_mod != _old_is_mod:
                return False
            # ── 硬约束：选项集合必须一致（用户可选项是契约，不可放宽）──
            if _opts_of(qs) != _opts_of(questions):
                return False
            # 第①层：语义指纹全等
            try:
                if _semantic_key(qs) == _semantic_key(questions):
                    return True
            except Exception:
                pass
            # 第②层：归一化后互相包含 / 关键数字一致
            for a, b in zip(qs, questions):
                na = _norm_text(a.get("question", ""))
                nb = _norm_text(b.get("question", ""))
                if na == nb:
                    continue
                if not na or not nb:
                    return False
                if (na in nb or nb in na) and min(len(na), len(nb)) >= 2:
                    continue
                da = _digits_of(a.get("question", ""))
                db = _digits_of(b.get("question", ""))
                if da and da == db:
                    continue
                return False
            return True
        except Exception:
            return False

    # ── 【2026-10-29 G03 修复】先选一个真正可达的 host（快速失败，不等 540s）──
    # 旧实现直接拿 args.host 去 POST；3080 没监听时只会在最后报一句
    #   "无法连接 host"，既没有降级指引，也没有尝试 DSH_WEB_URL 里那个真实端口。
    _hosts = _candidate_hosts(args.host if args.host != DEFAULT_HOST else None)
    _host = None
    for _h in _hosts:
        try:
            _probe = _get("%s/pending-child?childSessionId=%s" % (_h + PREFIX, args.child),
                          timeout=3)
            if isinstance(_probe, dict):
                _host = _h
                break
        except Exception:
            continue
    if not _host:
        emit({
            "ok": False,
            "gate": "ASK_BRIDGE_UNREACHABLE",
            "error": "无法连接宿主代问桥（已尝试：%s）" % " / ".join(_hosts),
            "tried_hosts": _hosts,
            "hint": _BRIDGE_DOWN_HINT,
            "fallback": "parent_relay",
        })
        return 1
    args.host = _host

    pending_id = None
    reused = False
    # ── ① 先查【跨会话】去重（2026-10-29 修复）──────────────────────────
    # 旧实现只查自己的 childSessionId，看不到别的子代理刚建的同一张卡，
    # 于是同一题被登记多次 → 用户看到已答过的选项反复弹出。
    _dup = _dedup_across_sessions(args.host, questions, args.child)
    if _dup.get("hit"):
        if _dup.get("owned_by_me"):
            pending_id = _dup.get("pendingId")
            reused = True
        else:
            # 别的子代理已经在问同一题 → 不要重复问用户
            emit({
                "ok": False,
                "gate": "ASK_DUPLICATE_OWNED_BY_PEER",
                "pendingId": _dup.get("pendingId"),
                "peer_child": _dup.get("peer_child"),
                "my_child": args.child,
                "questions": questions,
                "error": ("这道题【另一个子代理正在问用户】，不要重复提问。"),
                "why": ("2026-10-29 实测：多个总计子代理同时存活时，各自提问导致",
                        "用户看到已答过的选项反复弹出。"),
                "next": ("① 不要重问；② 等父代理把用户答复回传给你（send_message），",
                         "然后直接 mode2_flow.py answer-question <seq> \"<答复>\" 落账；",
                         "③ 若你确认自己是被误开的重复子代理 → 立刻结束回合。"),
            })
            return 1
    try:
        _pend = _get("%s/pending-child?childSessionId=%s" % (args.host + PREFIX, args.child))
        for _p in (_pend.get("pending") or []):
            if _same_question(_p) and _p.get("pendingId"):
                pending_id = _p.get("pendingId")
                reused = True
                break
    except Exception:
        pending_id = None

    if pending_id:
        # 复用已有卡片：不重复登记，避免面板堆积
        pass
    else:
        # 1) 登记新提问
        try:
            reg = _post(args.host + PREFIX + "/ask-child", {
                "childSessionId": args.child,
                "questions": questions,
            })
        except Exception as e:
            emit({"ok": False, "gate": "ASK_BRIDGE_UNREACHABLE",
                  "error": "无法连接 host: %s" % e,
                  "host": args.host,
                  "hint": _BRIDGE_DOWN_HINT,
                  "fallback": "parent_relay"})
            return 1

        if not reg.get("ok") or not reg.get("pendingId"):
            emit({"ok": False, "error": reg.get("error", "登记失败")})
            return 1

        pending_id = reg["pendingId"]
    room = args.room

    # 2) 阻塞轮询，直到用户作答（期间保持心跳，避免被判 stale）
    #    ── 【Bug#7 修复】必须区分【已作答】与【卡片已过期消失】──────────
    #    测试反馈：卡片在宿主端 10 分钟 TTL 过期后 pending 消失，但小屋
    #      报告仍停在 params_shown —— 因为它以为"pending 还在等"，
    #      既不敢重发（怕加剧 Bug#3 堆卡），又不会退出 → 静默卡死。
    #    根因：原实现只要 pending 消失就去取 result，
    #      取不到 result 时【静默 pass 继续轮询】，直到 timeout 才报错。
    #    修复：一旦"pending 消失 + result 取不到（连续 N 次确认）"，
    #      立即以明确错误退出，让小屋据此上报 failed，进入主对话介入流程。
    deadline = time.time() + int(args.timeout)
    base = args.host + PREFIX
    _gone_streak = 0          # pending 消失但取不到 result 的连续次数
    _GONE_LIMIT = 3           # 连续 3 次（约 6 秒）即确认为"过期消失"
    while time.time() < deadline:
        try:
            pending = _get("%s/pending-child?childSessionId=%s" % (base, args.child))
            still = [p for p in (pending.get("pending") or []) if p.get("pendingId") == pending_id]
            if not still:
                # 该 pending 已从 host 侧消失：两种可能 ——
                #   (a) 用户已作答（答案由 host 侧 childResults 保存，可取出）
                #   (b) 卡片 TTL 过期被清理（取不到 result）
                ans = _get("%s/result-child?pendingId=%s" % (base, pending_id))
                if ans and ans.get("ok") and ans.get("answers") is not None:
                    # ── (a) 已作答 ──
                    # 【Bug#8 修复】把作答结果回写 pending.json，供门禁校验
                    _writeback_answer(args.room, questions, ans, pending_id, args.child)
                    emit(ans)
                    return 0
                # ── (b) 取不到 result → 可能是过期，也可能刚被删除的竞态 ──
                _gone_streak += 1
                if _gone_streak >= _GONE_LIMIT:
                    emit({"ok": False,
                          "error": "pending expired before answer"
                                   "（提问卡片在宿主端已过期/被清理，且无作答结果）",
                          "pendingId": pending_id,
                          "pending_alive": False,
                          "expired": True,
                          "hint": ("卡片已消失且无答案。请【不要】把它当成『用户未回复』"
                                   "继续等待：应上报 room-report <房间> failed "
                                   "'提问卡片过期未作答'，由主对话决定重发或介入。"
                                   "若确需重问，请重新调用本脚本（会登记新卡片）。"),
                          "room": args.room or None,
                          "child": args.child})
                    return 2      # 专门的状态码：过期（区别于超时 1）
            else:
                _gone_streak = 0   # 卡片还在 → 计数清零
        except Exception:
            pass
        time.sleep(args.interval)

    # ── 【C5 修复】超时输出必须可自救：说明 pending 仍在、如何继续等待 ──
    # 原实现只说"超时"，调用方（小屋）不知道卡片还在、会重新登记导致堆积。
    emit({"ok": False,
          "error": "等待用户作答超时（%d 秒）" % args.timeout,
          "pendingId": pending_id,
          "pending_alive": True,
          "expired": False,
          "hint": ("提问卡片仍在面板上等待作答。请【不要重新提问】，"
                   "直接再调一次本脚本（同题会自动复用同一张卡，不会重复登记）；"
                   "或延长 --timeout（须低于外壳上限 600s）。")})
    return 1

    # ── 【C5 修复】超时输出必须可自救：说明 pending 仍在、如何继续等待 ──
    # 原实现只说"超时"，调用方（小屋）不知道卡片还在、会重新登记导致堆积。
    emit({"ok": False,
          "error": "等待用户作答超时（%d 秒）" % args.timeout,
          "pendingId": pending_id,
          "pending_alive": True,
          "hint": ("提问卡片仍在面板上等待作答。请【不要重新提问】，"
                   "直接再调一次本脚本（同题会自动复用同一张卡，不会重复登记）；"
                   "或延长 --timeout（须低于外壳上限 600s）。")})
    return 1


if __name__ == "__main__":
    sys.exit(main())
