# -*- coding: utf-8 -*-
"""mode2_flow —— 模式二（大型复杂器械装配）的【代码级强制契约】（七阶段引擎版）

═══════════════════════════════════════════════════════════════════════════
本模块存在的唯一理由
═══════════════════════════════════════════════════════════════════════════
用户明确要求：

    「所有改动都要写在代码里面，强制执行的」
    「不能是写在提示词里面的」

因此本模块把原先只存在于 SKILL.md / persona 自然语言里的流程约束，
全部升级为**可以被调用、会返回失败、能阻断流程**的代码契约。

═══════════════════════════════════════════════════════════════════════════
【2026-11-18 · 十阶段 → 七阶段】
═══════════════════════════════════════════════════════════════════════════
用户要求：「先删除十阶段分析所有的原本的文件，然后按照七阶段文档加功能」。

**已彻底删除**（不是禁用、不是降级，是删除）：
  · 十阶段流水线本体（STAGES / STAGE_IDS / STAGE_LABELS / STAGE_NEEDS）；
  · begin-stage / mark-stage-done / stage-status / refresh-stage 及其
    stale（产物过期）台账、_stage_* 系列判据；
  · 按需提问体系的题数与阶段挂载校验（ask / answer-question /
    import-answers / questions / validate_question_budget / QUESTION_LIMITS）；
  · 总计子代理的身份绑定（analyst-bind / analyst-guard / analyst-status /
    analyst-heartbeat / analyst_liveness）—— 该职能由七阶段插件的
    **真实会话谱系校验**接管，不再靠「自己报一个 sessionId」；
  · 子代理编制登记入口 set-subagent-plan / require_stages_complete。

**为什么连提问体系也删**：七阶段插件的真人澄清由插件自己的
question → capture → interpret 三段接管（原话必须来自宿主消息），
旧的「强 10~50 / 弱 5~10」题数硬约束属于另一套口径，两者并存必然打架。

═══════════════════════════════════════════════════════════════════════════
本模块现在负责什么
═══════════════════════════════════════════════════════════════════════════
  ① **七阶段凭证的下游消费与复核** —— 房间草案只能来自
     `seven_stage_gate.read_design()` 读到的凭据；编制登记时把
     ticket / revision / 分析者会话 ID 一并落盘。
  ② **房间类型规范化** —— 编制里的 type 必须落在规范类型表内，
     规范失败 = 明确报错（历史 Bug③/⑥：drivetrain/sensor 被静默豁免校核）。
  ③ **零件进度表 / 删件策略 / 房间实时状态** —— 数据源是外部监控器。
  ④ **交付前总闸** —— 七阶段凭证 + 监控器 verify-task + docx 汇总。

设计原则：**fail-closed**。任何"信息不足"的情形都返回 ok=False 并说明原因，
绝不静默放行。这样上游（门禁/主对话）无法"假装做过了"。

═══════════════════════════════════════════════════════════════════════════
原子写入
═══════════════════════════════════════════════════════════════════════════
本文件会被多个小屋并发读取（各自上报），因此落盘一律走 `os.replace`
原子替换，避免读到半个 JSON —— 这与 defense_gate.py 的处理保持一致。
"""
from __future__ import annotations

try:
    import sys as _sys_utf8
    for _stream_name in ("stdout", "stderr"):
        _st = getattr(_sys_utf8, _stream_name, None)
        if _st is not None and hasattr(_st, "reconfigure"):
            _st.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import argparse
import hashlib
import json
import os
import re
import sys
import time
import zipfile

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

#: 流程状态文件（分析结论 + 编制快照 + 凭证登记，与 part_registry.json 同目录）
PLAN_FILE = "mode2_plan.json"

# ═══════════════════════════════════════════════════════════════════════════
# 一、七阶段引擎标识 + 编制权威
# ═══════════════════════════════════════════════════════════════════════════
#: workflow_state.analysis_engine 的唯一合法值。旧引擎已删除，不存在回退分支。
ANALYSIS_ENGINE = "seven_v03"

#: 编制只能来自七阶段插件里的「总计子代理」角色（planner）。
SUBPLAN_AUTHORITY = "总计子代理"

#: 每轮至少一个房间；上限沿用历史的 24（房间数由总计子代理按分析结论决定）。
MIN_SUBAGENTS = 1
MAX_SUBAGENTS = 24


# ═══════════════════════════════════════════════════════════════════════════
# 二、状态读写（原子写）—— 与门禁读同一份 workflow_state
# ═══════════════════════════════════════════════════════════════════════════
def _state_dir():
    """状态目录权威裁决者（_store.state_dir()），与门禁读同一份状态文件。

    ── 为什么不能按"脚本所在目录"拼路径 ────────────────────────────────
    工程模式在磁盘上有两份 tools（仓库副本 + ~/.dsh 安装副本）。按
    __file__ 拼 workflow_state.json 会拿到【自己这份】的状态 —— 而门禁
    / 守卫读的是权威目录。实测后果：plan 的 task_id 取自另一份，
    编制 task_id 与当前任务永远对不上 → select 报 SUBAGENT_PLAN_STALE。
    """
    try:
        import _store as _st
        _d = _st.state_dir()
        if _d:
            return _d
    except Exception:
        pass
    return os.path.dirname(os.path.abspath(__file__))


def _workflow_state():
    """只读：权威目录里的 workflow_state.json（读不到返回 {}）。"""
    try:
        _p = os.path.join(_state_dir(), "workflow_state.json")
        if os.path.exists(_p):
            with open(_p, "r", encoding="utf-8-sig") as _f:
                return json.load(_f) or {}
    except Exception:
        pass
    return {}


def current_task_id(work_dir=None):
    """当前任务 task_id（权威目录里的 workflow_state）。读不到返回 None。"""
    return (_workflow_state() or {}).get("task_id") or None


def _plan_dir(work_dir=None):
    """计划文件所在目录。

    优先级：显式 work_dir → workflow_state 里当前任务的 work_dir → tools/load_cases。

    为什么要加中间这一层：
      set/编制命令不带 --work-dir 时曾直接落到 load_cases，
      而 workflow_gate.select / mode_gate.room-start 读的是【任务目录】，
      于是"登记成功"却"门禁看不见"—— 表现为编制 task_id 对不上、
      room-start 被 SUBAGENT_PLAN_STALE 误拦。
    """
    base = work_dir
    if not base:
        # 直接读状态文件，不 import 任何同族模块：
        #   sys.path 里同时存在「仓库 tools」与「安装副本 tools」，
        #   import workflow_gate 可能命中另一套 → 读到另一个任务的 work_dir。
        base = (_workflow_state() or {}).get("work_dir") or None
    if not base:
        try:
            import _root  # noqa: F401  （仅为触发根目录解析，失败则忽略）
        except Exception:
            pass
        base = os.path.join(BASE_DIR, "load_cases")
    try:
        os.makedirs(base, exist_ok=True)
    except Exception:
        base = BASE_DIR
    return base


def plan_path(work_dir=None):
    return os.path.join(_plan_dir(work_dir), PLAN_FILE)


def _atomic_write_json(path, data):
    """原子写：先写临时文件再 os.replace，避免并发读到半个 JSON。"""
    tmp = path + ".tmp.%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _fresh_plan():
    """空白计划文件。

    ── 七阶段版的变化 ────────────────────────────────────────────────
    不再有 stages / questions / stale_stages / analyst 这些十阶段字段。
    现在只保存"分析结论的下游形态"：七阶段凭证登记 + 房间编制 + 交付台账。
    """
    return {
        "schema": "dsh-engineering/mode2-plan@2",
        "analysis_engine": ANALYSIS_ENGINE,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "updated_at": None,
        # ── 七阶段凭证登记（由 workflow_gate select 的交接入口写入）──────
        # ticket      : read_design 返回的指纹，用来禁止"换一份设计继续用"
        # revision    : 该设计版本号；用户改答后 +1，旧凭证自动失效
        # analyst     : 总计子代理的真实 sessionId（由宿主插件给出，非模型自报）
        "seven_stage": None,
        # ── 本轮子代理编制（房间草案来自 read_design 的 rooms）──────────
        "subagent_plan": None,
        "subagent_config": None,
        # ── 交付台账 ──────────────────────────────────────────────────
        "docx_export": None,
        "notes": [],
    }


def load_plan(work_dir=None):
    p = plan_path(work_dir)
    if not os.path.exists(p):
        return _fresh_plan()
    try:
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f) or {}
        # 老文件缺字段时补齐，避免下游 KeyError
        base = _fresh_plan()
        for k, v in base.items():
            d.setdefault(k, v)
        return d
    except Exception:
        # 读坏时【不静默返回空计划】——空计划会让"未做"看起来像"没要求"。
        d = _fresh_plan()
        d["_load_error"] = "计划文件读取失败: %s" % p
        return d


def save_plan(plan, work_dir=None):
    plan = dict(plan or {})
    plan["schema"] = "dsh-engineering/mode2-plan@2"
    plan["analysis_engine"] = ANALYSIS_ENGINE
    plan["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    # task_id 一律以【权威目录】的 workflow_state 为准 ——
    # 否则编制与任务对不上，room-start 会被 SUBAGENT_PLAN_STALE 误拦。
    if not plan.get("task_id"):
        try:
            plan["task_id"] = current_task_id()
        except Exception:
            pass
    p = plan_path(work_dir)
    try:
        _atomic_write_json(p, plan)
        return {"ok": True, "path": p}
    except Exception as e:
        return {"ok": False, "error": "写入计划失败: %r" % (e,), "path": p}


# ═══════════════════════════════════════════════════════════════════════════
# 三、房间类型规范化 + rooms 快照（三处一致性）
# ═══════════════════════════════════════════════════════════════════════════
# 缺陷回顾（2026-10-19 实测）：
#   总计子代理在登记编制时把房间类型写成 drivetrain / sensor / drawing，
#   门禁只认 transmission / housing / drafting，于是：
#     · workflow_gate.select 用【新类型】路由波次 → 按 drafting 放行；
#     · 而 mode2_plan.json 的 rooms 快照从未刷新 → mode_gate.room-status /
#       defense_gate.check-room 读到旧类型 drivetrain/sensor → require_physics
#       被算成 false → 物理/领域凭据被静默豁免（虚假 CRITICAL 也一起放行）；
#     · 更早还有 drawing 导致 CONFIG_MISSING_FINAL_WAVES。
#
# 现在（代码强制）：
#   ① 类型必须能被规范化到【规范类型表】；规范失败 = 明确报错，不静默放过；
#   ② 登记编制时同步写 mode2_plan.json 的 subagent_config.rooms
#      （与 workflow_state.subagent_config.rooms 同构：[房间名, 类型] 对）；
#   ③ 只读命令 room-types 输出三处 room_type 的一致性自检。


CANONICAL_ROOM_TYPES = (
    "structural", "transmission", "housing", "support", "spring", "thermal",
    "corrosion", "assembly", "drafting",
)

ROOM_TYPE_ALIASES = {
    # Bug③ 实测：drivetrain 是历史遗留类型，不在波1类型表 → require_physics=false
    "drivetrain": "transmission",
    "drive": "transmission",
    "gear": "transmission",
    "传动": "transmission",
    # Bug⑥ 实测：sensor 不在白名单 → 物理/领域凭据被静默豁免
    "sensor": "housing",
    "sensors": "housing",
    "electronics": "housing",
    "electronic": "housing",
    "外壳": "housing",
    "壳体": "housing",
    # Bug③ 实测：drawing 导致 CONFIG_MISSING_FINAL_WAVES（门禁只认 drafting）
    "drawing": "drafting",
    "drawings": "drafting",
    "dwg": "drafting",
    "工程图": "drafting",
    "出图": "drafting",
    # 其它常见同义写法
    "structure": "structural",
    "frame": "structural",
    "结构": "structural",
    "机架": "structural",
    "assembly_validation": "assembly",
    "assembly-validation": "assembly",
    "总装": "assembly",
    "验证": "assembly",
        # "prime_agent" 已删除（总理子代理角色取消）
    # "总理" 角色已删除（监控器接替它的验收职责）
}

#: rooms-template 的字段说明（与登记编制时的校验器同源，避免第二份口径）。
ROOMS_TEMPLATE_FIELDS = (
    ("name", "房间名（唯一，中文可读，如 结构件 / 传动件）"),
    ("type", "房间类型，必须是规范类型之一（见 allowed_types，可写同义词）"),
    ("parts", "本屋负责的零件名清单（非建模屋也要占位名，空数组会被拒）"),
    ("why", "为什么需要这个子代理（可审计的存在理由）"),
    ("depends_on", "依赖的房间名清单（可空；指向不存在的房间会被拒）"),
)


def normalize_room_type(raw):
    """把房间类型规范化到 CANONICAL_ROOM_TYPES。**失败即报错**（不静默兜底）。

    Args:
        raw: 原始类型字符串（大小写 / 空白 / 连字符 / 同义词都容忍）

    Returns:
        {"ok": True, "type": <规范类型>, "raw":..., "normalized": bool, "alias":...}
        或 {"ok": False, "error": "...", "raw":..., "allowed": [...]}

    为什么不做"猜一个最接近的"兜底：
      静默兜底正是 Bug③/⑥ 的来源 —— drivetrain 被悄悄当成未知类型，
      门禁于是 require_physics=false，凭据被静默豁免。宁可报错让人改。
    """
    _raw = raw
    s = str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not s:
        return {"ok": False, "raw": _raw,
                "error": ("房间类型为空 —— 必须显式给 type（规范类型：%s）。"
                          % "/".join(CANONICAL_ROOM_TYPES)),
                "allowed": list(CANONICAL_ROOM_TYPES)}
    if s in CANONICAL_ROOM_TYPES:
        return {"ok": True, "type": s, "raw": _raw, "normalized": (str(_raw) != s),
                "alias": (str(_raw) if str(_raw) != s else None)}
    if s in ROOM_TYPE_ALIASES:
        canon = ROOM_TYPE_ALIASES[s]
        return {"ok": True, "type": canon, "raw": _raw, "normalized": True,
                "alias": str(_raw),
                "note": ("类型 %r 不在门禁规范表内，已规范化为 %r"
                         "（否则会漏启动或被免校核）。" % (str(_raw), canon))}
    return {"ok": False, "raw": _raw,
            "error": ("房间类型 %r 无法规范化到门禁认可的规范类型（%s）。"
                      "未知类型会让波次路由漏启动、或 require_physics 直接豁免 —— "
                      "拒绝登记。请改用规范类型，或先在此处补别名并写回归测试。"
                      % (str(_raw), "/".join(CANONICAL_ROOM_TYPES))),
            "allowed": list(CANONICAL_ROOM_TYPES),
            "known_aliases": sorted(ROOM_TYPE_ALIASES)}

def room_type_snapshot(rooms):
    """把编制里的 rooms 规范化为门禁可直接消费的快照（[(name, type), ...]）。

    单独抽出来是为了让 set_subagent_plan / room_types_report 共用同一套
    规范化逻辑 —— 两处各写一份正是"口径分裂"的来源。
    """
    out, seen = [], set()
    for r in (rooms or []):
        if not isinstance(r, dict):
            continue
        nm = str(r.get("name") or "").strip()
        nz = normalize_room_type(r.get("type"))
        if not nm or not nz.get("ok"):
            continue
        if nm in seen:
            continue
        seen.add(nm)
        out.append([nm, nz["type"]])
    return out

def rooms_template(work_dir=None, out_file=None):
    """输出一份【含 type 的完整 rooms 示例】，供总计子代理照抄后改写。

    字段口径与 set_subagent_plan 的校验逻辑一一对应（同样的 name/type/parts/
    why/depends_on），type 取值直接来自 CANONICAL_ROOM_TYPES —— 不允许在这里
    再维护第二份类型表，否则又会变成"示例与校验器不同源"。

    返回结构刻意包了一层 {"rooms": [...]}：
      set-subagent-plan 对 {"rooms": [...]} 会自动解包（BUG-3 修复），
      因此把本命令的输出【整份】存成 rooms.json 也能直接用。
    """
    rooms = [
        {"name": "结构件", "type": "structural",
         "parts": ["DSH_车架底板", "DSH_立柱"],
         "why": "承载整车与电池，决定主要刚度与安装基准", "depends_on": []},
        {"name": "传动件", "type": "transmission",
         "parts": ["DSH_主动齿轮", "DSH_从动齿轮", "DSH_传动轴"],
         "why": "把电机转矩按减速比传到执行机构", "depends_on": ["结构件"]},
        {"name": "壳体", "type": "housing",
         "parts": ["DSH_上壳体", "DSH_下壳体"],
         "why": "包覆并固定内部件，兼顾散热与防护", "depends_on": ["结构件"]},
        {"name": "总装与验证", "type": "assembly",
         "parts": ["DSH_整机装配体"],
         "why": "完成装配 + 干涉检查 + 整体验证", "depends_on": ["结构件", "传动件"]},
        {"name": "工程图输出", "type": "drafting",
         "parts": ["DSH_整机图纸"],
         "why": "输出三视图/装配图并导出 DWG/PDF", "depends_on": ["总装与验证"]},
    ]
    payload = {
        "ok": True,
        "rooms": rooms,
        "fields": [{"field": k, "desc": v} for k, v in ROOMS_TEMPLATE_FIELDS],
        "allowed_types": list(CANONICAL_ROOM_TYPES),
        "type_aliases": {k: v for k, v in sorted(ROOM_TYPE_ALIASES.items())},
        "usage": [
            "① 本模板只说明【字段长什么样】—— 真正的房间草案由七阶段插件产出：",
            "   总计子代理 action=propose_rooms 提交 rooms（字段与本模板同构）；",
            "② 按实际功能分解改 name/parts/why/depends_on，**type 必须保留**；",
            "③ 草案定稿后由【真实根会话】登记编制：",
            '   python mode2_flow.py register-design',
        ],
        "note": ("房间至少 1 个；总装(assembly)与出图(drafting)若七阶段草案没写，"
                 "register-design 会按流程自动补齐，不必自己记。"
                 "非建模房间的 parts 也要写占位名（写装配体名/图纸名即可）。"),
        "engine": ANALYSIS_ENGINE,
    }
    if out_file:
        try:
            _p = os.path.abspath(str(out_file))
            _d = os.path.dirname(_p)
            if _d:
                os.makedirs(_d, exist_ok=True)
            with open(_p, "w", encoding="utf-8") as f:
                # 落盘时只写【可直接投喂】的部分，避免把 usage/note 混进编制文件
                json.dump(rooms, f, ensure_ascii=False, indent=2)
            payload["out_file"] = _p
        except Exception as e:
            payload["ok"] = False
            payload["error"] = "写入模板文件失败: %r" % (e,)
            payload["out_file"] = None
    return payload

def _read_state_json(name):
    """只读：从状态目录（与门禁同一份）读某个 json；读不到返回 (None, 原因)。

    刻意【不 import workflow_gate / mode_gate】：
      工程模式有"仓库 / 安装副本"两套 tools，import 可能命中另一套，
      于是读到另一个任务的状态 —— 这里与 _plan_dir 一样按 _store 权威目录直读。
    """
    try:
        _sd = None
        try:
            import _store as _st
            _sd = _st.state_dir()
        except Exception:
            _sd = None
        if not _sd:
            _sd = BASE_DIR
        _p = os.path.join(_sd, name)
        if not os.path.exists(_p):
            return None, "文件不存在: %s" % _p
        with open(_p, "r", encoding="utf-8") as _f:
            return json.load(_f) or {}, _p
    except Exception as e:
        return None, "读取失败: %r" % (e,)

def _rooms_as_pairs(raw):
    """把任意形态的 rooms 列表统一成 {房间名: 类型}（None 表示没有类型信息）。

    兼容三种历史写法：
      · [["结构件","structural"], ...]      ← workflow_state 现行写法
      · [{"name":"结构件","type":"structural"}, ...]
      · {"结构件": {...}}                    ← mode_state.rooms（只有房间状态，无类型）
    """
    out = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            _t = None
            if isinstance(v, dict):
                _t = v.get("type") or v.get("room_type")
            out[str(k)] = (str(_t) if _t else None)
        return out
    for row in (raw or []):
        if isinstance(row, (list, tuple)) and len(row) >= 1:
            out[str(row[0])] = (str(row[1]) if len(row) >= 2 and row[1] else None)
        elif isinstance(row, dict):
            _n = row.get("name") or row.get("room")
            if _n:
                _t = row.get("type") or row.get("room_type")
                out[str(_n)] = (str(_t) if _t else None)
    return out

def sync_rooms_snapshot(rooms, work_dir=None, source="workflow_gate.select",
                        task_id=None):
    """【Bug③ 修复】把门禁侧的权威房间表回写进 mode2_plan.json 的 rooms 快照。

    为什么还需要"反向同步"：
      set-subagent-plan 写的是【编制】的快照；workflow_gate.select 会据此重建
      运行期房间配置（只含编制里的房间）。两边若只单向同步，plan 快照与
      workflow_state 仍可能差房间 → 三处一致性判不一致（假告警），
      真不一致反而被淹没。
      因此 select 落盘后调用本函数，把【最终生效的】房间表写回 plan 快照。

    Args:
        rooms: [(房间名, 类型), ...] 或 [{"name","type"}, ...]
        work_dir: 计划文件目录
        source: 写入来源（留痕，默认 workflow_gate.select）
        task_id: 归属任务；给出时同时校正 plan.task_id（避免跨任务污染）

    Returns: {"ok": bool, "rooms": [...], "changes": [...]}
    """
    pairs = []
    seen = set()
    for row in (rooms or []):
        if isinstance(row, (list, tuple)) and len(row) >= 1:
            _n, _t = str(row[0]), (row[1] if len(row) >= 2 else None)
        elif isinstance(row, dict):
            _n, _t = str(row.get("name") or ""), row.get("type")
        else:
            continue
        _n = _n.strip()
        if not _n or _n in seen:
            continue
        _nz = normalize_room_type(_t)
        if not _nz.get("ok"):
            return {"ok": False, "room": _n, "raw_type": _t,
                    "error": ("房间【%s】的类型 %r 无法规范化：%s"
                              % (_n, _t, _nz.get("error"))),
                    "allowed": _nz.get("allowed")}
        seen.add(_n)
        pairs.append([_n, _nz["type"]])
    if not pairs:
        return {"ok": False, "error": "rooms 为空 —— 拒绝把空房间表写进快照。"}

    plan = load_plan(work_dir)
    if plan.get("_load_error"):
        return {"ok": False, "error": plan["_load_error"]}
    cfg = plan.get("subagent_config") or {}
    old = [list(x) for x in (cfg.get("rooms") or []) if isinstance(x, (list, tuple))]
    changes = []
    _old_map = {x[0]: (x[1] if len(x) > 1 else None) for x in old}
    for n, t in pairs:
        if _old_map.get(n) != t:
            changes.append({"room": n, "from": _old_map.get(n), "to": t})
    for n in _old_map:
        if n not in {x[0] for x in pairs}:
            changes.append({"room": n, "from": _old_map[n], "to": None,
                            "note": "已从快照移除（不在最终房间表里）"})
    cfg.update({
        "rooms": pairs,
        "total_rooms": len(pairs),
        "rooms_source": (cfg.get("rooms_source")
                         or "总计子代理（mode2_flow.set-subagent-plan）"),
        "synced_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "synced_from": source,
    })
    if task_id:
        cfg["task_id"] = task_id
        plan["task_id"] = task_id
    plan["subagent_config"] = cfg
    save_plan(plan, work_dir)
    return {"ok": True, "rooms": pairs, "changes": changes,
            "path": plan_path(work_dir), "synced_from": source}

def room_types_report(work_dir=None):
    """【Bug③ 修复】三处 room_type 一致性【只读】自检。

    背景（实测）：同一个房间在三个工具里 room_type 各不相同 ——
      mode_gate.room-status → drivetrain（读 mode2_plan 的旧快照）
      defense_gate.check-room → sensor（读 workflow_state）
      workflow_gate.select → transmission（读本次编制）
    结果是传动房按 drivetrain 判 → require_physics=false → 真实 CREDIBLE 问题
    被静默豁免。

    本命令只读、不修数据，把四处来源并列出来 + 判定一致性 + 指出每个房间
    规范化后会走哪条门禁（require_physics 是否强制），供调用方定位差异。

    Returns:
        {"ok": 一致性通过, "consistent": bool, "sources": {...},
         "by_room": [...], "conflicts": [...], "warnings": [...]}
    """
    plan = load_plan(work_dir)
    if plan.get("_load_error"):
        return {"ok": False, "error": plan["_load_error"], "checked": False}

    sp = plan.get("subagent_plan") or {}
    src_plan_rooms = _rooms_as_pairs(sp.get("rooms") or [])       # 编制本身
    src_snapshot = _rooms_as_pairs(
        (plan.get("subagent_config") or {}).get("rooms") or [])    # plan 快照
    _wf, _wf_note = _read_state_json("workflow_state.json")
    _ms, _ms_note = _read_state_json("mode_state.json")
    src_wf = _rooms_as_pairs(((_wf or {}).get("subagent_config") or {}).get("rooms") or [])
    src_ms = _rooms_as_pairs((_ms or {}).get("rooms") or {})       # 无类型，仅房间名
    # ── 任务隔离：workflow_state 若属于【另一个任务】，它的 rooms 与本编制无关 ──
    # 不隔离的话，上一轮残留的房间会被算进"三处一致性"，把正常状态误报成冲突。
    _wf_task = (_wf or {}).get("task_id")
    _plan_task = plan.get("task_id")
    _wf_foreign = bool(_wf_task and _plan_task and str(_wf_task) != str(_plan_task))
    if _wf_foreign:
        _wf_note = ("workflow_state 属于另一个任务（%s），本任务为 %s —— "
                    "不参与本次一致性比对。" % (_wf_task, _plan_task))
        src_wf = {}
    elif _wf is None and _wf_note:
        pass

    # 门禁实际会怎么判：defense_gate 读 workflow_state 的 rooms；
    # 波1 建模类类型（workflow_gate.WAVE1_TYPES / defense_gate.MODELING_ROOM_TYPES）。
    _wave1 = ("structural", "transmission", "housing", "support", "spring",
              "thermal", "corrosion")
    _finite = ("assembly", "drafting")

    # 参与比对的房间 = 编制 / 快照 / workflow_state 的并集。
    # mode_state.rooms 是【历史遗留台账】（可能还是上一轮任务的房间），
    # 只能作为"额外告警"列出，不能算进三处一致性 —— 否则会拿旧任务房间
    # 去比对本任务的编制，永远假报冲突。
    names = []
    for _d in (src_plan_rooms, src_snapshot, src_wf):
        for _n in _d:
            if _n not in names:
                names.append(_n)
    names.sort()
    _ms_only = sorted(n for n in src_ms if n not in names)

    by_room, conflicts, warnings = [], [], []
    for n in names:
        raw = {k: v.get(n) for k, v in (
            ("subagent_plan", src_plan_rooms),
            ("plan_snapshot", src_snapshot),
            ("workflow_state", src_wf),
            ("mode_state", src_ms))}
        # 规范化每个来源
        norm = {}
        bad = {}
        for k, v in raw.items():
            if v is None:
                norm[k] = None
                continue
            _nz = normalize_room_type(v)
            if _nz.get("ok"):
                norm[k] = _nz["type"]
            else:
                norm[k] = "<非法:%s>" % v
                bad[k] = _nz.get("error")
        _eff = None
        for k in ("subagent_plan", "plan_snapshot", "workflow_state"):
            if norm.get(k) and not str(norm[k]).startswith("<非法"):
                _eff = norm[k]
                break
        _distinct = sorted({v for v in norm.values()
                            if v and not str(v).startswith("<非法")})
        _ok = len(_distinct) <= 1 and not bad
        row = {"room": n, "raw": raw, "normalized": norm,
               "effective_type": _eff,
               "require_physics": bool(_eff in _wave1),
               "wave": ("wave1" if _eff in _wave1 else
                        ("final" if _eff in _finite else None)),
               "consistent": _ok}
        if bad:
            row["type_errors"] = bad
        if not _ok:
            conflicts.append({"room": n, "distinct_types": _distinct,
                              "raw": raw, "normalized": norm})
        if _eff and _eff not in CANONICAL_ROOM_TYPES:
            warnings.append("房间【%s】的类型 %r 不是规范类型。" % (n, _eff))
        by_room.append(row)

    # 快照 vs 编制：这是 Bug③ 的直接判据
    def _norm_or_none(v):
        return normalize_room_type(v).get("type") if v else None

    # 快照 vs 编制 —— Bug③ 的直接判据（含 raw，便于一眼看出旧类型）
    # 注：原先这里对"固定追加的总理子代理"做例外处理（只存在于快照、编制里没有
    #   属正常）。该角色已删除，快照不再追加任何固定房间，因此例外也一并去掉 ——
    #   留着它只会让真正的不一致被当成"固定角色"静默放过。
    _snap_diff, _snap_fixed_only = [], []
    for n in sorted(set(src_plan_rooms) | set(src_snapshot)):
        a, b = _norm_or_none(src_plan_rooms.get(n)), _norm_or_none(src_snapshot.get(n))
        if a == b:
            continue
        _snap_diff.append({"room": n,
                           "subagent_plan": a,
                           "subagent_plan_raw": src_plan_rooms.get(n),
                           "plan_snapshot": b,
                           "plan_snapshot_raw": src_snapshot.get(n)})
    if _snap_diff:
        conflicts.append({"scope": "plan_snapshot_vs_subagent_plan",
                          "detail": _snap_diff,
                          "fix": ("重新执行 set-subagent-plan 同步快照，"
                                  "或 workflow_gate.py select 重建配置。")})

    # workflow_state 侧要分两种情况 —— 混在一起会把"还没 select"误报成"不一致"：
    #   · 编制里有、workflow_state 还没有 → 正常时序（set-subagent-plan 在 select 之前），
    #     只做提示，不算冲突；
    #   · 两边都有但类型不同 → 真不一致，报警告（select 后应自动对齐）。
    _wf_diff, _wf_pending = [], []
    for n in sorted(set(src_plan_rooms) | set(src_wf)):
        a, b = _norm_or_none(src_plan_rooms.get(n)), _norm_or_none(src_wf.get(n))
        if a != b and b is None and a is not None:
            _wf_pending.append({"room": n, "subagent_plan": a})
        elif a != b:
            _wf_diff.append({"room": n, "subagent_plan": a,
                             "subagent_plan_raw": src_plan_rooms.get(n),
                             "workflow_state": b,
                             "workflow_state_raw": src_wf.get(n)})
    if _ms_only:
        warnings.append("mode_state.json 里有 %d 个本任务编制里没有的房间（%s）—— "
                        "属历史残留台账，未参与一致性比对；如确认无关可用 "
                        "mode_gate.py rooms-reset 清理。" % (len(_ms_only), "、".join(_ms_only)))
    if _wf_foreign:
        warnings.append(_wf_note)
    if _wf_pending:
        warnings.append("有 %d 个房间尚未写入 workflow_state.subagent_config.rooms —— "
                        "执行 workflow_gate.py select 后会自动同步。"
                        % len(_wf_pending))
    if _wf_diff:
        warnings.append("workflow_state.subagent_config.rooms 与编制不一致（%d 个房间）—— "
                        "请重新执行 workflow_gate.py select 重建配置。"
                        % len(_wf_diff))

    if not sp and not src_snapshot:
        return {"ok": False, "checked": True,
                "error": ("尚无子代理编制与 rooms 快照 —— 先由总计子代理登记编制"
                          "（set-subagent-plan 会同步写快照）。"),
                "sources": {"workflow_state_file": _wf_note,
                            "mode_state_file": _ms_note},
                "by_room": [], "conflicts": [], "warnings": warnings}

    return {"ok": not conflicts, "checked": True,
            "consistent": not conflicts,
            "plan_file": plan_path(work_dir),
            "sources": {
                "subagent_plan": src_plan_rooms,
                "plan_snapshot": src_snapshot,
                "workflow_state": src_wf,
                "mode_state_rooms": sorted(src_ms),
                "workflow_state_file": _wf_note,
                "mode_state_file": _ms_note,
            },
            "snapshot_synced": not _snap_diff,
            "snapshot_fixed_only_rooms": _snap_fixed_only,
            "workflow_state_synced": not _wf_diff,
            "workflow_state_pending": _wf_pending,
            "by_room": by_room,
            "conflicts": conflicts,
            "warnings": warnings,
            "normalized_types": [{"room": r["name"], "raw": r.get("type_raw"),
                                  "type": r["type"]}
                                 for r in (sp.get("rooms") or [])
                                 if r.get("type_normalized")],
            "wave1_types": list(_wave1),
            "note": ("三处 room_type 必须一致：不一致会让 defense_gate 用旧类型"
                     "判 require_physics（drivetrain/sensor 直接被豁免校核）。")}

def get_subagent_plan(work_dir=None):
    """读回子代理编制，并附带**七阶段凭证的新鲜度**（只读，不改变 ok 语义）。

    ── 为什么编制还要带新鲜度 ────────────────────────────────────────
    编制是在"七阶段分析完成"时登记的。但用户【之后】仍可能改答题目 →
    插件的 revision +1、下游消息凭据作废 → 编制所依据的结论已失效。
    此时若只看 ok=True 就开房间，等于拿着过期结论去建模。

    本函数把 seven_ok / seven_revision / stale_hint 一并给出，让 mode_gate /
    workflow_gate 的调用方（以及人）一眼看到"编制还成立吗"；真正的拦截由
    select / pre-delivery 里的 recheck_design（比对已保存 ticket）负责。
    """
    plan = load_plan(work_dir)
    sp = plan.get("subagent_plan")
    if not sp:
        return {"ok": False,
                "error": ("尚无子代理编制 —— 必须先由【真实根会话】调 "
                          "mode2_flow.py register-design 登记七阶段房间草案，"
                          "禁止使用固定房间表。")}
    out = {"ok": True, **sp}
    try:
        cur = recheck_design(work_dir)
        if not cur.get("ok"):
            out.update({
                "seven_ok": False,
                "seven_error": cur.get("error"),
                "stale_hint": ("编制所依据的七阶段凭证已失效（%s）—— "
                               "必须重新登记编制后才能据此开工。"
                               % (cur.get("error") or "凭证不可用")),
            })
        else:
            _saved = sp.get("seven_stage_revision")
            _cur_rev = cur.get("revision")
            _stale = (_saved is not None and _cur_rev != _saved)
            out.update({
                "seven_ok": True,
                "seven_ticket": cur.get("ticket"),
                "seven_revision": _cur_rev,
                "stages_ready": True,
                "stale_hint": (("编制所依据的七阶段设计版本已变（登记 %r → 当前 %r）"
                                "—— 必须重新登记编制。" % (_saved, _cur_rev))
                               if _stale else None),
            })
    except Exception as _e:
        # 凭证信息读不出来时不得假装"没问题"
        out["seven_ok"] = None
        out["seven_status_error"] = repr(_e)
    return out

def _room_live_map(work_dir=None):
    """房间 → 看板行（含 state/report_stage/artifact）。失败返回 {}。"""
    out = {}
    try:
        import mode_gate as _mg
        live = _mg.cmd_room_board(as_json=True)
        for row in ((live or {}).get("rooms") or []):
            nm = str(row.get("room") or "")
            if nm:
                out[nm] = row
    except Exception:
        return {}
    return out

def _pulse_alive():
    """DSH 主进程是否还在跳（pulse）。无结论时返回 None（不臆断）。"""
    try:
        import mode_gate as _mg
        st = _mg._pulse_state()
        return bool(st.get("alive"))
    except Exception:
        return None

def autonomy_mode(work_dir=None):
    """读本轮"搭建方式" A/B/C（A=完全自主，无需问；B/C=需要问）。

    用户要求（原文）：「先删除对应的错误文件（如果没有选择自主
      干完话，即总计子代理之后还能问的话，应该先调用 DSH 内部的提问用户，
      先问再删，但是假设选择了『A = 完全自主搭建』那就不用问，直接删即可）」。

    来源：workflow_state.json 的 user_choice（select 当场写入，是权威）。
    读不到 → 返回 None（调用方必须按"需要问"处理，绝不默认自主）。
    """
    try:
        import _store as _st
        p = _st.state_path("workflow_state.json")
        if not os.path.isfile(p):
            return None
        with open(p, "r", encoding="utf-8-sig") as f:
            j = json.load(f) or {}
        c = str(j.get("user_choice") or "").strip().upper()
        if c in ("A", "B", "C"):
            return c
    except Exception:
        return None
    return None

def can_delete_without_asking(work_dir=None):
    """能不能【不问就删】失败件：只有 A（完全自主）可以。

    Returns: {ok, mode, may_delete_silently, must_ask, reason}
      · mode = None  → 读不到选择 → must_ask=True（保守，绝不擅自删）
      · mode = "A"   → may_delete_silently=True
      · mode = "B"/"C" → must_ask=True
    """
    mode = autonomy_mode(work_dir)
    if mode == "A":
        return {"ok": True, "mode": mode, "may_delete_silently": True,
                "must_ask": False,
                "reason": "本轮搭建方式为 A（完全自主搭建）—— 可直接删除失败件，无需询问。"}
    if mode in ("B", "C"):
        return {"ok": True, "mode": mode, "may_delete_silently": False,
                "must_ask": True,
                "reason": ("本轮搭建方式为 %s（非完全自主）—— 必须先向用户提问确认，"
                           "得到答复后再删，不得擅自删除。" % mode)}
    return {"ok": False, "mode": None, "may_delete_silently": False,
            "must_ask": True,
            "reason": ("读不到本轮搭建方式（user_choice）—— 按【需要问】处理："
                       "先用 ask_user.py / ask_user_question 询问，确认后再删。")}

def _room_rebuild_flags():
    """读 mode_state，找出"被 room-fail / restart 过、正在重做"的房间。

    用户要求：「还有需要『重建』」—— 与"未开始"必须区分开：
      · 从未开始 = 编制里有、一次也没建过；
      · 需要重建 = 建过但失败/被回退，正在或等着重做。
    判据（都来自 mode_gate 写的权威状态文件，不靠猜）：
      · room 记录里 restart_count > 0，或
      · 存在 last_failed_at 且没有被更晚的 ended_at 覆盖。
    """
    out = {}
    try:
        import mode_gate as _mg
        st = _mg.load_state()
        rooms = st.get("rooms") or {}
        for name, info in rooms.items():
            if not isinstance(info, dict):
                continue
            _rc = int(info.get("restart_count") or 0)
            _failed_at = info.get("failed_at")
            _ended_at = info.get("ended_at")
            _rebuild = False
            if _rc > 0:
                _rebuild = True
            if _failed_at and not _ended_at:
                _rebuild = True
            if _rebuild:
                out[str(name)] = {
                    "restart_count": _rc,
                    "failed_at": _failed_at,
                    "reason": info.get("failed_reason") or info.get("fail_reason"),
                }
    except Exception:
        return {}
    return out

def parts_board(work_dir=None):
    """★ 零件进度表 —— 数据源 = 外部监控器（唯一权威）。

    ── 为什么整段重写 ──────────────────────────────────────────────────
    旧实现从 mode2_plan 的编制 + prime_reports（总理子代理上报）推导状态：
      · "完成"= 总理子代理报过一次；"正在建"= 房间 report_stage 说 modeling。
      这两个都是【自报】，没有任何东西核对零件文件与验收凭据。
    教程 4 要求把数据读取换成监控器 board：
      · 状态来自监控器（part-start/part-saved/part-failed 逐件事件 + 验收结论）；
      · 前端按 label/state/detail/checks 渲染，保留 counts.done/total；
      · 后台断开时展示不可用或缓存时间，【禁止回退到旧 prime-report 判完成】。

    Returns: {ok, parts:[{name, room, room_type, state, label, detail,
                          generation, file, has_report, checks,
                          safe_to_restart}],
              summary:{counts, total, done}, pulse_alive, quarantined,
              generated_at, source:"sw_monitor", degraded?}
    """
    try:
        import sw_monitor_client as _mc
    except Exception as e:
        return {"ok": False, "source": "sw_monitor",
                "error": "缺少 sw_monitor_client.py: %r" % (e,)}
    if not _mc.enabled():
        return {"ok": False, "source": "sw_monitor", "degraded": True,
                "error": "未接入外部监控器，逐零件看板不可用。",
                "hint": ("旧实现读 prime_reports 判「完成」，该数据源已随总理子代理一起删除。"
                         "启动 DSH-SW-Monitor 并 mode_gate.py sw-monitor attach --drained 后重试。"),
                "parts": [], "summary": {"counts": {}, "total": 0, "done": 0},
                "pulse_alive": False, "quarantined": False}
    try:
        b = _mc.board()
    except Exception as e:
        code = getattr(e, "code", "MONITOR_ERROR")
        return {"ok": False, "source": "sw_monitor", "degraded": True,
                "code": code, "error": str(e),
                "hint": "监控器不可达：看板展示的是「不可用」，不是「没零件」。",
                "parts": [], "summary": {"counts": {}, "total": 0, "done": 0},
                "pulse_alive": False, "quarantined": False}
    parts = []
    for row in (b.get("parts") or []):
        if not isinstance(row, dict):
            continue
        parts.append({
            "name": row.get("name"),
            "room": row.get("room"),
            "room_type": row.get("room_type"),
            "state": row.get("state"),
            "label": row.get("label"),
            "detail": row.get("detail"),
            "generation": row.get("generation"),
            "file": row.get("file"),
            "has_report": bool(row.get("has_report")),
            "checks": row.get("checks") or {},
            "safe_to_restart": bool(row.get("safe_to_restart")),
        })
    summary = b.get("summary") or {}
    return {
        "ok": True,
        "source": "sw_monitor",
        "task": b.get("task"),
        "revision": b.get("revision"),
        "parts": parts,
        "rooms": b.get("rooms") or {},
        "summary": {
            "counts": summary.get("counts") or {},
            "total": summary.get("total", len(parts)),
            "done": summary.get("done", 0),
        },
        "sw": b.get("sw") or {},
        "pulse_alive": b.get("pulse_alive"),
        "quarantined": b.get("quarantined"),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "note": ("状态全部来自外部监控器：part-start/part-saved/part-failed 逐件事件，"
                 "加上三大防线逐件验收结论。pulse_alive=false 表示宿主失联，"
                 "此时看板显示的是「不可用」，不代表零件没建。"),
    }

def _sha256_json(obj):
    """稳定的 SHA-256（键排序、紧凑分隔符）——同数据必得同指纹。"""
    import hashlib
    try:
        blob = json.dumps(obj, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), default=str)
    except Exception:
        blob = repr(obj)
    return hashlib.sha256(blob.encode("utf-8", "replace")).hexdigest()

def _check_docx_deliverable(work_dir=None, registry_work_dir=None):
    """核实 docx 汇总交付物真的存在且合法。

    判定（缺一不可）：
      ① 在交付目录（及台账目录）下能找到 **.docx**；
      ② 文件非空（> MIN_DOCX_BYTES）；
      ③ 是**合法 zip**（docx 本质是 zip）且**含 word/document.xml** ——
         这一条能挡住"随便写个空文件改名成 .docx"的假交付。

    为什么在校验里做 zip 检查而不是只看扩展名：
      只看扩展名等于"改名即通过"，与全仓 fail-closed 的口径不符。
    """
    MIN_DOCX_BYTES = 1024   # 真 docx 至少有 1KB；纯 stdlib 降级版约 5KB
    out = {"ok": False, "path": None, "size_bytes": 0, "candidates": [],
           "checked_dirs": [], "error": None}
    _roots = []
    for _d in (work_dir, registry_work_dir):
        if _d:
            _roots.append(os.path.abspath(str(_d)))
    # ══ ★【实测缺陷修复 · 2026-10-13】先认【导出时记录的路径】══════════════
    # 现象：`export-docx --out <任意路径>` 成功后，`check-docx` 却报
    #   "在 <work_dir>、<load_cases> 下未找到任何 .docx" → 交付总闸误拦。
    # 根因：本函数只在【固定目录】里 walk，而导出方完全可以把 docx
    #   导出到别处（--out 是允许的），于是"真导出了却查不到"。
    # 修法：把 export_docx_and_record 记录在计划文件里的路径优先纳入检查，
    #   但仍**自己重查磁盘**（不采信记录本身），保持 fail-closed 语义不变。
    _recorded = None
    try:
        _plan = load_plan(work_dir)
        _de = (_plan or {}).get("docx_export") or {}
        _rp = _de.get("path")
        if _rp and os.path.isfile(str(_rp)):
            _recorded = os.path.abspath(str(_rp))
    except Exception:
        _recorded = None
    if _recorded:
        _roots.insert(0, os.path.dirname(_recorded))
        out["recorded_path"] = _recorded
    try:
        import part_registry as _pr
        try:
            _p = _pr.registry_path(work_dir=registry_work_dir or work_dir)
            if _p:
                _roots.append(os.path.dirname(os.path.abspath(_p)))
        except Exception:
            pass
    except Exception:
        pass
    # 去重保序
    _seen = set()
    _roots = [r for r in _roots
              if not (os.path.normcase(r) in _seen or _seen.add(os.path.normcase(r)))]
    out["checked_dirs"] = _roots

    _cands = []
    # ★ 记录路径本身作为第一候选（已 isfile，且随后仍要过 zip 校验）
    if _recorded:
        try:
            _cands.append((os.path.getmtime(_recorded), _recorded))
        except Exception:
            pass
    for _r in _roots:
        if not os.path.isdir(_r):
            continue
        try:
            for _dirpath, _dirs, _files in os.walk(_r):
                # 跳过明显的无关目录，避免把测试产物当交付物
                if any(_s in _dirpath for _s in ("__pycache__", ".git")):
                    continue
                for _f in _files:
                    if _f.lower().endswith(".docx"):
                        _fp = os.path.join(_dirpath, _f)
                        try:
                            _cands.append((os.path.getmtime(_fp), _fp))
                        except Exception:
                            _cands.append((0.0, _fp))
        except Exception:
            continue
    _cands.sort(reverse=True)
    out["candidates"] = [_p for _m, _p in _cands][:20]
    if not _cands:
        out["error"] = ("在 %s 下未找到任何 .docx —— 交付前必须导出汇总 docx。"
                        % "、".join(_roots) or "交付目录")
        return out

    # 逐个候选校验，取第一个合法的（最新的优先）
    for _m, _fp in _cands:
        try:
            _sz = os.path.getsize(_fp)
        except Exception:
            continue
        if _sz < MIN_DOCX_BYTES:
            out["error"] = ("找到的 docx 过小（%s，%d 字节 < %d）—— 疑似空文件。"
                            % (os.path.basename(_fp), _sz, MIN_DOCX_BYTES))
            continue
        # ★ 必须是真的 zip 且含 word/document.xml
        try:
            import zipfile
            with zipfile.ZipFile(_fp) as _z:
                _names = _z.namelist()
            if "word/document.xml" not in _names:
                out["error"] = ("%s 不是合法 docx（zip 内缺少 word/document.xml）。"
                                % os.path.basename(_fp))
                continue
        except Exception as e:
            out["error"] = ("%s 不是合法 zip/docx（%r）—— 疑似改名冒充。"
                            % (os.path.basename(_fp), e))
            continue
        out.update({"ok": True, "path": _fp, "size_bytes": _sz,
                    "error": None,
                    "note": "docx 汇总交付物已核实（合法 zip + 含 word/document.xml）。"})
        return out

    if not out["error"]:
        out["error"] = "找到 .docx 但都不合格（详见 candidates）。"
    return out

def export_docx_and_record(out_path=None, work_dir=None, registry_work_dir=None):
    """docx 汇总入口：导出并回写计划文件（供交付总闸核实）。

    这是"把 docx 变成流程一等公民"的落点：
      · 导出成功后把路径/大小/时间写进计划文件；
      · 交付前总闸会**自己重查磁盘**（不采信这里的记录），
        因此即使有人手改计划文件也骗不过去。
    """
    try:
        import part_registry as _pr
    except Exception as e:
        return {"ok": False, "error": "part_registry 不可用：%r" % (e,)}
    _wd = registry_work_dir or work_dir
    _out = out_path
    if not _out:
        _base = os.path.abspath(str(work_dir or _wd or "."))
        _out = os.path.join(_base, "零件台账_汇总.docx")
    try:
        r = _pr.export_docx(_out, work_dir=_wd)
    except TypeError:
        r = _pr.export_docx(_out)
    except Exception as e:
        return {"ok": False, "error": "导出 docx 异常：%r" % (e,)}
    _existed = os.path.exists(_out)
    _sz = os.path.getsize(_out) if _existed else 0
    _res = {"ok": bool(r.get("ok")) if isinstance(r, dict) else bool(r),
            "path": _out, "size_bytes": _sz,
            "detail": r if isinstance(r, dict) else None}
    # 回写计划文件（仅作记录；总闸仍会自己重查磁盘）
    try:
        plan = load_plan(work_dir)
        if not plan.get("_load_error"):
            plan["docx_export"] = {"path": _out, "size_bytes": _sz,
                                   "at": time.strftime("%Y-%m-%d %H:%M:%S")}
            save_plan(plan, work_dir)
    except Exception:
        pass
    return _res

def _print(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))

# ═══════════════════════════════════════════════════════════════════════════
# 六、七阶段分析结论 → 房间编制（唯一入口）
# ═══════════════════════════════════════════════════════════════════════════
def _guard_engine():
    """确认当前任务的分析引擎是 seven_v03。

    为什么要有这一步：旧引擎已删除，若某个任务还带着旧引擎标记，
    说明它是升级前留下的现场 —— 必须显式报错，而不是"猜一个"继续跑。
    """
    wf = _workflow_state()
    eng = str(wf.get("analysis_engine") or "").strip()
    if not wf:
        return {"ok": False,
                "gate": "WORKFLOW_STATE_UNAVAILABLE",
                "error": "读不到权威目录里的 workflow_state.json —— 无法确认分析引擎。",
                "how": ["先跑 python \"<根>\\tools\\workflow_gate.py\" init \"<任务>\""]}
    if eng != ANALYSIS_ENGINE:
        return {"ok": False,
                "gate": "ANALYSIS_ENGINE_MISMATCH",
                "error": ("当前任务的 analysis_engine=%r，本版只支持 %r。"
                          "十阶段引擎已删除，旧任务请用旧版本工具链收尾，"
                          "或在 workflow_gate 里为新任务重新 init。"
                          % (eng, ANALYSIS_ENGINE)),
                "expected": ANALYSIS_ENGINE}
    return {"ok": True, "engine": eng, "task_id": wf.get("task_id")}


def _read_design_for_task(work_dir=None, include_records=False):
    """调用 seven_stage_gate.read_design 读取当前任务的七阶段凭证。

    信任根：宿主（DSH 进程）写、ACL 保护的七阶段状态文件。
    模型的任何自报字段都不参与判定 —— 只看 task_id + 真实根会话 ID。

    Returns: read_design 的返回体（ok=False 时带 gate/error）。
    """
    g = _guard_engine()
    if not g.get("ok"):
        return g
    wf = _workflow_state()
    task_id = wf.get("task_id")
    root_sid = wf.get("seven_stage_root_session_id")
    if not str(root_sid or "").strip():
        return {"ok": False,
                "gate": "SEVEN_STAGE_ROOT_SESSION_MISSING",
                "error": ("workflow_state 里没有 seven_stage_root_session_id —— "
                          "任务初始化时没有记录真实根会话，无法校验七阶段凭证。")}
    try:
        import seven_stage_gate as _ssg
    except Exception as e:
        return {"ok": False,
                "gate": "SEVEN_STAGE_GATE_UNAVAILABLE",
                "error": "seven_stage_gate.py 不可用（%r）—— 七阶段凭证无法校验，禁止继续。" % (e,),
                "how": ["确认 engineering/tools/seven_stage_gate.py 已随源码同步到安装副本。"]}
    try:
        return _ssg.read_design(task_id, root_sid, include_records=include_records)
    except Exception as e:
        return {"ok": False, "gate": "SEVEN_STAGE_GATE_ERROR",
                "error": "read_design 调用失败：%r" % (e,)}


def _normalize_rooms_from_design(rooms, rationale):
    """把 read_design 返回的 rooms 规范成门禁可直接消费的编制。

    与旧 set_subagent_plan 的校验口径保持一致（type 白名单 / 零件范围 /
    依赖存在性 / 自动补齐总装与出图两个收尾房间），但**不再**接受
    调用方自报的 decided_by —— 权威来自七阶段凭证本身。
    """
    if not isinstance(rooms, list) or not rooms:
        return {"ok": False,
                "error": "read_design 返回的 rooms 为空 —— 七阶段草案必须有房间。"}
    if not str(rationale or "").strip():
        return {"ok": False,
                "error": ("必须给出 rationale（为什么是这个编制）—— "
                          "没有分解理由的编制无法审计，拒绝登记。")}
    if not (MIN_SUBAGENTS <= len(rooms) <= MAX_SUBAGENTS):
        return {"ok": False,
                "error": ("房间数量 %d 超出允许范围 [%d,%d]。"
                          % (len(rooms), MIN_SUBAGENTS, MAX_SUBAGENTS))}

    norm, seen = [], set()
    for i, r in enumerate(rooms):
        if not isinstance(r, dict):
            return {"ok": False, "error": "rooms[%d] 不是对象" % i}
        name = str(r.get("name") or "").strip()
        if not name:
            return {"ok": False, "error": "rooms[%d] 缺少 name" % i}
        if name in seen:
            return {"ok": False, "error": "rooms 里出现重复房间名: %r" % name}
        seen.add(name)
        parts = r.get("parts") or []
        if not isinstance(parts, list) or not parts:
            return {"ok": False,
                    "error": ("房间【%s】没有列出 parts（该屋负责哪些零件）。"
                              "编制必须写明零件范围，否则无法做归属校验。" % name),
                    "hint": ("即使本屋【不建零件】（总装 / 出图），"
                             "也要写至少一个占位名：总装写 DSH_XX装配体，"
                             "出图写 DSH_XX图纸。空数组 [] 会被拒绝。"),
                    "example_parts": ["DSH_XX装配体"]}
        why = str(r.get("why") or "").strip()
        if not why:
            return {"ok": False,
                    "error": ("房间【%s】缺少 why（为什么需要它）。"
                              "用户要求每个零件都能追溯到存在的理由，编制同理。" % name)}
        _nz = normalize_room_type(r.get("type"))
        if not _nz.get("ok"):
            return {"ok": False,
                    "room": name, "raw_type": r.get("type"),
                    "allowed": list(CANONICAL_ROOM_TYPES),
                    "error": ("房间【%s】%s：%s"
                              % (name,
                                 "缺少 type 字段"
                                 if (r.get("type") is None or not str(r.get("type")).strip())
                                 else "的房间类型无法规范化",
                                 _nz.get("error"))),
                    "fix_cmd": "python mode2_flow.py rooms-template --out rooms.json",
                    "fix_hint": ("照抄一份含 type 的完整示例再改："
                                 "python mode2_flow.py rooms-template --out rooms.json")}
        item = {
            "name": name,
            "type": _nz["type"],
            "parts": [str(p).strip() for p in parts if str(p).strip()],
            "why": why,
            "depends_on": [str(d).strip() for d in (r.get("depends_on") or [])
                           if str(d).strip()],
        }
        if _nz.get("normalized"):
            item["type_raw"] = str(r.get("type"))
            item["type_normalized"] = True
        norm.append(item)

    # ══ 【BUG-07】自动补齐收尾房间：总装 + 出图 ══════════════════════════
    # 这两个房间不属于"设计决策"：任何 Mode 2 任务都必须装配 + 出图。
    # 让模型去记住这件事属于把流程约束放在提示词里 —— 正是不该做的事。
    _AUTO_FINAL_ROOMS = (
        ("总装与验证", "assembly",
         "整机装配 + 干涉检查 + 整体物理/领域验证（自动补齐）"),
        ("工程图输出", "drafting",
         "各零件三视图 + 装配图 + DWG/PDF 导出（自动补齐）"),
    )
    _auto_added = []
    for _nm, _tp, _why in _AUTO_FINAL_ROOMS:
        if any(r["type"] == _tp for r in norm):
            continue
        _use, _exist, _i = _nm, {r["name"] for r in norm}, 2
        while _use in _exist:
            _use = "%s%d" % (_nm, _i)
            _i += 1
        norm.append({"name": _use, "type": _tp, "parts": [], "why": _why,
                     "depends_on": [], "auto_added": True})
        _auto_added.append({"name": _use, "type": _tp, "why": _why})

    _names = {r["name"] for r in norm}
    for r in norm:
        _bad = [d for d in r["depends_on"] if d not in _names]
        if _bad:
            return {"ok": False,
                    "error": ("房间【%s】依赖了不存在的房间: %s"
                              % (r["name"], "、".join(_bad)))}
    return {"ok": True, "rooms": norm, "auto_added_final_rooms": (_auto_added or None)}


def register_design_plan(work_dir=None, include_records=False, actor=None):
    """★【唯一入口】把七阶段分析结论登记为本轮子代理编制。

    调用者：**真实根会话**（不是总计子代理、更不是模型自报）。
    信任链：workflow_state 的 task_id + seven_stage_root_session_id
            → seven_stage_gate.read_design（宿主写的状态文件）
            → 本函数校验房间草案 → 落盘编制 + 凭证 ticket。

    Args:
        include_records: 需要完整设计记录时置 True（受控交付目录保存规格与来源）。

    Returns: {"ok": True, ticket, revision, analyst_session_id, rooms, ...}
    """
    g = _guard_engine()
    if not g.get("ok"):
        return g
    draft = _read_design_for_task(work_dir=work_dir, include_records=include_records)
    if not draft.get("ok"):
        return {"ok": False,
                "gate": draft.get("gate") or "SEVEN_STAGE_HANDOFF_REQUIRED",
                "error": draft.get("error") or "七阶段凭证不可用。",
                "how": ["真实根会话：engineering_seven_stage action=attach 绑定总计子代理；",
                        "总计子代理：action=launch 启动知识/设计/反驳三个角色；",
                        "总计子代理：action=propose_rooms 提交房间草案并 end(kind=complete)。"]}
    _norm = _normalize_rooms_from_design(draft.get("rooms"), draft.get("rationale"))
    if not _norm.get("ok"):
        return _norm
    rooms = _norm["rooms"]
    plan = load_plan(work_dir)
    if plan.get("_load_error"):
        return {"ok": False, "error": plan["_load_error"]}
    _tid = current_task_id(work_dir) or plan.get("task_id")
    ticket = draft.get("ticket")
    revision = draft.get("revision")
    analyst = draft.get("analyst_session_id")

    plan["seven_stage"] = {
        "task_id": _tid,
        "ticket": ticket,
        "revision": revision,
        "analyst_session_id": analyst,
        "scope": draft.get("scope"),
        "registered_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "registered_by": actor or "root",
    }
    plan["subagent_plan"] = {
        "decided_by": SUBPLAN_AUTHORITY,
        "task_id": _tid,
        "rationale": str(draft.get("rationale")).strip(),
        "scope": draft.get("scope"),
        "count": len(rooms),
        "rooms": rooms,
        "seven_stage_ticket": ticket,
        "seven_stage_revision": revision,
        "analyst_session_id": analyst,
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _snap = room_type_snapshot(rooms)
    plan["subagent_config"] = {
        "rooms": _snap,
        "total_rooms": len(_snap),
        "rooms_source": "七阶段插件 read_design（roomProposal）",
        "rooms_rationale": str(draft.get("rationale")).strip(),
        "room_parts": {r["name"]: r.get("parts") or [] for r in rooms},
        "room_types_normalized": [{"room": r["name"], "raw": r.get("type_raw"),
                                   "type": r["type"]}
                                  for r in rooms if r.get("type_normalized")],
        "synced_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "synced_from": "seven_stage",
        "task_id": _tid,
    }
    save_plan(plan, work_dir)
    if include_records and draft.get("design_record") is not None:
        try:
            _ddir = _plan_dir(work_dir)
            _fp = os.path.join(_ddir, "seven_stage_design_%s.json"
                               % str(revision or 0))
            _atomic_write_json(_fp, {
                "task_id": _tid, "ticket": ticket, "revision": revision,
                "design_record": draft.get("design_record"),
                "end_reasons": draft.get("end_reasons"),
            })
        except Exception:
            pass
    return {"ok": True, "count": len(rooms), "rooms": rooms,
            "decided_by": SUBPLAN_AUTHORITY,
            "ticket": ticket, "revision": revision,
            "analyst_session_id": analyst,
            "rooms_snapshot": _snap,
            "auto_added_final_rooms": _norm.get("auto_added_final_rooms"),
            "plan_file": plan_path(work_dir),
            "note": ("编制已落盘并登记七阶段凭证（ticket/revision/analyst）。"
                     "建模小屋必须严格按此编制创建，数量不得固定。")}


def recheck_design(work_dir=None, saved_ticket=None, include_records=False):
    """在 select / 恢复开工 / 建模放权 / 交付前【重查】七阶段凭证。

    与 register_design_plan 的差别：这里比对**已保存的 ticket**，
    设计一旦被用户改答（revision 变化）就立刻失效 —— 防止拿旧结论开工。
    """
    plan = load_plan(work_dir)
    if not plan.get("seven_stage"):
        return {"ok": False, "gate": "SEVEN_STAGE_NOT_REGISTERED",
                "error": ("本任务还没有登记七阶段交接凭证 —— "
                          "真实根会话必须先调用 register-design 登记编制。")}
    want = saved_ticket or (plan["seven_stage"] or {}).get("ticket")
    if not str(want or "").strip():
        return {"ok": False, "gate": "SEVEN_STAGE_TICKET_MISSING",
                "error": "已保存的七阶段 ticket 为空 —— 无法复核设计是否变动。"}
    g = _guard_engine()
    if not g.get("ok"):
        return g
    wf = _workflow_state()
    try:
        import seven_stage_gate as _ssg
    except Exception as e:
        return {"ok": False, "gate": "SEVEN_STAGE_GATE_UNAVAILABLE",
                "error": "seven_stage_gate.py 不可用（%r）" % (e,)}
    cur = _ssg.read_design(wf.get("task_id"),
                           wf.get("seven_stage_root_session_id"),
                           want, include_records=include_records)
    if not cur.get("ok"):
        return {"ok": False,
                "gate": cur.get("gate") or "SEVEN_STAGE_HANDOFF_REQUIRED",
                "error": cur.get("error") or "七阶段凭证复核未通过。",
                "saved_ticket": want}
    return cur


# ═══════════════════════════════════════════════════════════════════════════
# 七、按需提问（七阶段版：不再有题数配额）
# ═══════════════════════════════════════════════════════════════════════════
# 旧引擎的「强 10~50 / 弱 5~10」题数硬约束已随十阶段一起删除。
# 七阶段的真人澄清由插件接管：question（总计子代理提出决策缺口）
# → 真实根会话把原话交给用户 → capture（从宿主消息取原话）
# → interpret（分类为 fact/preference/authorization/estimate/unknown）。
# 因此这里【不再登记题目】，只提供一条只读入口把插件的提问现状转给门禁。
def question_report(work_dir=None, final=False):
    """只读：把七阶段插件的真人澄清现状汇总给门禁/交付总闸。

    数据源是插件状态目录（DSH_SEVEN_STAGE_STATE_DIR）里的任务文件，
    不是本文件的计划文件 —— 计划文件只保存下游编制的快照。
    """
    out = {"ok": True, "engine": ANALYSIS_ENGINE, "final": bool(final),
           "asked": None, "min": None, "max": None,
           "note": "七阶段不设题数配额：澄清由插件 question/capture/interpret 接管。"}
    try:
        wf = _workflow_state()
        sd = os.environ.get("DSH_SEVEN_STAGE_STATE_DIR") or ""
        tid = wf.get("task_id")
        if sd and tid:
            _p = os.path.join(sd, "%s.json" % tid)
            if os.path.exists(_p):
                with open(_p, "r", encoding="utf-8-sig") as _f:
                    st = json.load(_f) or {}
                out["asked"] = len(st.get("facts") or [])
                out["open_question"] = (st.get("question") or {}).get("id")
                out["phase"] = st.get("phase")
                out["revision"] = st.get("revision")
    except Exception as e:
        out["read_error"] = repr(e)
        out["ok"] = False
        out["error"] = "七阶段状态读取失败：%r" % (e,)
    return out


# ═══════════════════════════════════════════════════════════════════════════
# 八、交付前总闸
# ═══════════════════════════════════════════════════════════════════════════
def pre_delivery_check(work_dir=None, registry_work_dir=None, require_monitor=True,
                       require_primes=None):
    """宣告任务完成之前的**强制总闸**。任一不满足 → ok=False，禁止交付。

    检查项（七阶段版）：
      ① 七阶段凭证可用且与已登记 ticket 一致（= 设计已独立审查通过）；
      ② 子代理编制存在，且来自当前版本的七阶段凭证；
      ③ 监控器 verify-task 通过（逐件验收 + 台账审计 + 邻接尺寸 + 仿真策略）；
      ④ ★ docx 汇总交付物存在且合法。

    ── 与十阶段版的差别 ────────────────────────────────────────────────
    旧版 ① 查"十阶段是否都留产出"、② 查"题数是否达标"、③ 查"analyst-bind
    是否执行"。这三项都已删除：七阶段的完成度由插件的 63 个小步凭证
    一次性校验（read_design），比按阶段数点更严 —— 它连每一步的
    知识/设计/复审三件套都要核对。
    """
    if require_primes is not None:
        # 旧参数名，保留为兼容别名：老调用点传 False 仍表示"跳过监控器核实"。
        require_monitor = bool(require_primes)
    blockers = []
    plan = load_plan(work_dir)
    if plan.get("_load_error"):
        return {"ok": False, "blockers": [plan["_load_error"]], "checked": False}

    # ── ① 七阶段凭证（含"设计是否被改过"）──────────────────────────────
    _ss = recheck_design(work_dir)
    if not _ss.get("ok"):
        blockers.append("七阶段交接未通过：%s" % (_ss.get("error") or "凭证不可用"))
    else:
        _saved = ((plan.get("seven_stage") or {}).get("revision"))
        if _saved is not None and _ss.get("revision") != _saved:
            blockers.append("七阶段设计版本已变（登记 %r → 当前 %r）—— "
                            "必须重新登记编制后才能交付。"
                            % (_saved, _ss.get("revision")))

    # ── ② 编制必须来自七阶段凭证 ───────────────────────────────────────
    sp = plan.get("subagent_plan")
    if not sp:
        blockers.append("缺少子代理编制（必须由七阶段 read_design 的房间草案登记）")
    elif sp.get("decided_by") != SUBPLAN_AUTHORITY:
        blockers.append("子代理编制不是由【%s】决定的（实际 %r）"
                        % (SUBPLAN_AUTHORITY, sp.get("decided_by")))
    elif _ss.get("ok") and str(sp.get("seven_stage_ticket") or "") != str(_ss.get("ticket") or ""):
        blockers.append("编制所依据的七阶段 ticket 与当前设计不一致 —— 必须重新登记。")

    # ── ③ 监控器统一核实 ───────────────────────────────────────────────
    # 每次【重算】，不采信任何落盘结论：验收之后若模型/凭据/台账被改动，
    # 这里必须立刻变 stale 并拒绝交付。
    _pv = None
    if require_monitor:
        try:
            import sw_monitor_client as _mc_vt
            _pv = _mc_vt.call("verify-task", {})
        except Exception as e:
            blockers.append("监控器统一核实失败：%s（按 fail-closed 拒绝结束）" % (e,))
        if _pv is not None and _pv.get("passed") is not True:
            blockers.append("★ 监控器【尚未通过统一核实】—— 证据不足，禁止结束任务：")
            for _b in (_pv.get("blockers") or [])[:8]:
                blockers.append("    · %s" % _b)

    # ── ④ ★ 交付前必须有一份汇总 docx ───────────────────────────────
    _docx = None
    if require_monitor:
        _docx = _check_docx_deliverable(work_dir=work_dir,
                                        registry_work_dir=registry_work_dir)
        if not _docx.get("ok"):
            blockers.append("★ 缺少 docx 汇总交付物 —— 禁止结束任务：")
            blockers.append("    · %s" % (_docx.get("error") or "未找到 docx"))
            blockers.append("    · 导出方式："
                            "python part_registry.py export-docx --out \"<交付目录>/零件台账.docx\""
                            " --work-dir \"<交付目录>\"")

    # ══ 【A8】强放行必须进交付结论，不许再"报告里仍是全签发" ═════════
    _bypass = {"count": 0, "line": "", "must_render": False}
    try:
        import defense_gate as _dg_a8
        _bypass = _dg_a8.bypass_summary()
    except Exception as _e_byp:
        _bypass = {"count": None, "line": "", "must_render": False,
                   "error": "强放行记录读取失败：%r（按不可信处理）" % (_e_byp,)}
    _banned = ["三防线全部签发", "三防线均已签发", "全部签发", "三道防线全部通过"]
    return {"ok": not blockers, "blockers": blockers, "checked": True,
            "analysis_engine": ANALYSIS_ENGINE,
            # ── 【A8】强放行披露（交付文案的强制输入）──────────────────────
            "bypass": _bypass,
            "bypass_count": _bypass.get("count"),
            "bypass_line": _bypass.get("line") or "",
            "bypass_must_render": bool(_bypass.get("must_render")),
            "banned_phrases": _banned,
            "delivery_wording_rule": (
                "若 bypass_count > 0，交付总结【不得】写「三防线全部签发」之类措辞，"
                "必须原样渲染 bypass_line，并注明这些判定未获真几何 FEA 证据。"
                if (_bypass.get("count") or 0) > 0 else
                "本次无强放行记录，可以正常表述三防线结论。"),
            "seven_stage": {"ok": _ss.get("ok"), "ticket": _ss.get("ticket"),
                            "revision": _ss.get("revision"),
                            "analyst_session_id": _ss.get("analyst_session_id"),
                            "error": _ss.get("error")},
            "subagents": ((sp or {}).get("count")),
            "monitor_verification": (None if _pv is None else {
                "passed": _pv.get("passed"),
                "status": _pv.get("status"),
                "checks": _pv.get("checks"),
                "blockers": _pv.get("blockers"),
            }),
            "docx_deliverable": _docx}


# ═══════════════════════════════════════════════════════════════════════════
# 九、CLI
# ═══════════════════════════════════════════════════════════════════════════
def _main_body(argv):
    ap = argparse.ArgumentParser(
        prog="mode2_flow.py",
        description="模式二（大型器械装配）下游契约：七阶段凭证复核 / 房间编制 / "
                    "零件进度表 / 交付前总闸。十阶段分析引擎已删除。")
    sub = ap.add_subparsers(dest="cmd")

    # ── 七阶段（新）──────────────────────────────────────────────────
    p = sub.add_parser("register-design",
                       help="★由【真实根会话】调用：读取七阶段凭证并登记本轮编制")
    p.add_argument("--include-records", action="store_true",
                   help="同时导出完整设计记录与结束理由")
    p.add_argument("--actor", default=None, help="调用者 sessionId（默认取 DSH_SESSION_ID）")
    p.add_argument("--work-dir", default=None)

    p = sub.add_parser("recheck-design",
                       help="★重查七阶段凭证（select / 恢复开工 / 交付前）")
    p.add_argument("--ticket", default=None, help="已保存的 ticket（默认取计划文件里的）")
    p.add_argument("--work-dir", default=None)

    p = sub.add_parser("seven-status",
                       help="只读：七阶段凭证 + 已登记编制的当前状态")
    p.add_argument("--work-dir", default=None)

    p = sub.add_parser("questions", help="只读：七阶段真人澄清汇总（不设题数配额）")
    p.add_argument("--final", action="store_true")
    p.add_argument("--work-dir", default=None)

    # ── 房间编制 / 类型 ─────────────────────────────────────────────
    p = sub.add_parser("subagent-plan", help="查看当前子代理编制")
    p.add_argument("--work-dir", default=None)

    p = sub.add_parser("rooms-template",
                       help="★输出含 type 的完整 rooms 示例（照抄后改成自己的房间）")
    p.add_argument("--out", default="", help="可选：把 rooms 数组直接写进该 json 文件")
    p.add_argument("--work-dir", default=None)

    p = sub.add_parser("room-types", help="三处 room_type 一致性自检（只读）")
    p.add_argument("--work-dir", default=None)

    p = sub.add_parser("sync-rooms", help="把门禁侧权威房间表回写进计划快照")
    p.add_argument("--rooms-json", required=True, help="JSON 数组或 @文件路径")
    p.add_argument("--source", default="workflow_gate.select")
    p.add_argument("--task-id", default=None)
    p.add_argument("--work-dir", default=None)

    # ── 交付 ────────────────────────────────────────────────────────
    p = sub.add_parser("export-docx",
                       help="★汇总导出 docx（交付前总闸会核实它存在）")
    p.add_argument("--out", default=None, help="输出路径（默认 <交付目录>/零件台账_汇总.docx）")
    p.add_argument("--registry-work-dir", default=None)
    p.add_argument("--work-dir", default=None)

    p = sub.add_parser("check-docx", help="核实 docx 交付物是否存在且合法")
    p.add_argument("--registry-work-dir", default=None)
    p.add_argument("--work-dir", default=None)

    p = sub.add_parser("pre-delivery", help="★交付前总闸")
    p.add_argument("--work-dir", default=None)

    p = sub.add_parser("parts-board",
                       help="★零件进度表（未开始/正在建/完成/需要重建/失败）")
    p.add_argument("--json", action="store_true", help="强制 JSON 输出（默认即 JSON）")
    p.add_argument("--work-dir", default=None)

    p = sub.add_parser("delete-policy",
                       help="★删失败件前要不要问用户（A 模式=不问，B/C=必须问）")
    p.add_argument("--json", action="store_true", help="强制 JSON 输出（默认即 JSON）")
    p.add_argument("--work-dir", default=None)

    p = sub.add_parser("plan", help="打印完整计划文件")
    p.add_argument("--work-dir", default=None)

    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help()
        return 2

    wd = getattr(args, "work_dir", None)

    def _rooms_json_error(exc):
        """--rooms-json 解析失败的【可操作】报错（BUG-2）。"""
        return {
            "ok": False,
            "error": "rooms-json 解析失败: %r" % (exc,),
            "hint": ("三种写法都支持，请在 PowerShell 里优先用 ②：",
                     " ① 内联 JSON：--rooms-json '[{\"name\":\"结构件\","
                     "\"type\":\"structural\",\"parts\":[\"DSH_x\"],"
                     "\"why\":\"…\"}]'",
                     " ② 文件路径（推荐）：--rooms-json C:\\path\\rooms.json"
                     "（不带 @，避免 PowerShell 把 @ 当 splatting 吃掉）",
                     " ③ 兼容写法：--rooms-json \"@C:\\path\\rooms.json\""
                     "（bash / cmd / Python 下可用）"),
            "note": "文件内容必须是【JSON 数组】，不要包一层 {\"rooms\": [...]}。",
        }

    def _load_json_arg(v):
        r"""支持 --rooms-json '[{...}]' 或 --rooms-json @file.json（便于长内容）。

        ── 【BUG-2】PowerShell 的 @ 会被吃掉 ─────────────────────────
        现场：在 PowerShell 里写 --rooms-json "@C:path
ooms.json"，
          @ 被 PS 当成 splatting/数组符号解释，参数被拆散，命令直接失败。
        处理（三重，任一即可用）：
          ① 仍支持 @file.json（bash / cmd / Python 直接调用时最自然）；
          ② 支持不带 @ 的 .json 文件路径（推荐 PS 用户这样写）；
          ③ 支持把 JSON 直接内联传入。
        """
        s = v
        _is_path = False
        if isinstance(v, str):
            _raw = v.strip()
            if _raw.startswith("@"):
                _is_path = True
                _raw = _raw[1:].strip().strip("'\"")
            elif _raw.lower().endswith(".json") and os.path.isfile(_raw):
                _is_path = True
            if _is_path:
                if not os.path.isfile(_raw):
                    raise IOError("文件不存在: %s" % _raw)
                with open(_raw, "r", encoding="utf-8-sig") as f:
                    s = f.read()
        elif isinstance(v, (list, dict)):
            return v
        return json.loads(s)

    _actor = getattr(args, "actor", None)
    if args.cmd == "register-design":
        r = register_design_plan(wd, include_records=bool(args.include_records),
                                 actor=_actor)
    elif args.cmd == "recheck-design":
        r = recheck_design(wd, getattr(args, "ticket", None))
    elif args.cmd == "seven-status":
        _pl = load_plan(wd)
        _cur = recheck_design(wd)
        r = {"ok": True,
             "engine": ANALYSIS_ENGINE,
             "registered": _pl.get("seven_stage"),
             "current": {k: _cur.get(k) for k in
                         ("ok", "ticket", "revision", "analyst_session_id", "error")},
             "plan_file": plan_path(wd)}
    elif args.cmd == "questions":
        r = question_report(wd, final=args.final)
    elif args.cmd == "subagent-plan":
        r = get_subagent_plan(wd)
    elif args.cmd == "rooms-template":
        r = rooms_template(wd, getattr(args, "out", "") or None)
    elif args.cmd == "room-types":
        r = room_types_report(wd)
    elif args.cmd == "sync-rooms":
        try:
            _rooms = _load_json_arg(args.rooms_json)
        except Exception as e:
            _print(_rooms_json_error(e))
            return 1
        r = sync_rooms_snapshot(_rooms, wd, source=args.source,
                                task_id=getattr(args, "task_id", None))
    elif args.cmd == "export-docx":
        r = export_docx_and_record(getattr(args, "out", None), wd,
                                   getattr(args, "registry_work_dir", None))
    elif args.cmd == "check-docx":
        r = _check_docx_deliverable(wd, getattr(args, "registry_work_dir", None))
    elif args.cmd == "pre-delivery":
        r = pre_delivery_check(wd)
    elif args.cmd == "parts-board":
        r = parts_board(wd)
    elif args.cmd == "delete-policy":
        r = can_delete_without_asking(wd)
    elif args.cmd == "plan":
        r = load_plan(wd)
    else:
        ap.print_help()
        return 2

    _print(r)
    return 0 if r.get("ok") else 1


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        return _main_body(argv)
    except SystemExit:
        raise
    except Exception as e:
        import traceback
        _print({"ok": False, "error": "内部异常: %r" % (e,),
                "traceback": traceback.format_exc()[-1200:]})
        return 1


if __name__ == "__main__":
    sys.exit(main())
