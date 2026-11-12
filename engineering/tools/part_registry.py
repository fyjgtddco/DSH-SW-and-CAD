#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""part_registry.py — 零件数据台账（尺寸一致性底座）

═══════════════════════════════════════════════════════════════════════════
【要解决的问题】零件之间没有任何尺寸台账
───────────────────────────────────────────────────────────────────────────
改了一个零件的尺寸，没人知道哪些相邻零件会受影响：
  · 各小屋（房间）各自建模，只认自己那份参数表；
  · 轴颈改成 φ25，配对轴承座的孔还是 φ24 —— 直到总装才发现装不上；
  · 事后想回溯"这个尺寸当初是谁定的、改过几次"，磁盘上没有任何记录。

本模块提供【唯一的零件数据台账】，作为"总理子代理"的数据底座：
  ① 写入：add / update / link / remove（追加式修订历史，rev 自增）
  ② 查询：get / list / related（按零件反查关联）/ reverse（反查入边）
  ③ 校验：check_adjacency_consistency（★ 相邻零件尺寸一致性，代码级强制）
  ④ 影响面：find_related_for_change（要改某个零件时，谁得跟着改 + 为什么）
  ⑤ 交付：export_docx（导出 Word 台账）

═══════════════════════════════════════════════════════════════════════════
【台账文件落点】
───────────────────────────────────────────────────────────────────────────
默认：  <本脚本所在目录>/load_cases/part_registry.json
        （目录不存在会自动创建 —— 工程模式既有约定，见 workflow_gate.py）
覆盖优先级（高 → 低）：
  ① 环境变量 DSH_PART_REGISTRY（完整文件路径，测试/多实例隔离用）
  ② 函数参数 work_dir（若其 basename 已是 load_cases 则直接用它，
     否则拼 work_dir/load_cases/part_registry.json）
  ③ 默认落点

═══════════════════════════════════════════════════════════════════════════
【一致性判定规则 —— 全部代码化，不是注释里的"应该"】
───────────────────────────────────────────────────────────────────────────
对每一对【相邻零件】(A, B)，把双方 interfaces 归一化
（id / type / nominal_mm / tolerance）后按下列优先级配对：

  R1 same_id          两侧接口 id 完全相同                → 配对
  R2 explicit_mate    任一侧用 mates_with / mate_with /
                      pairs_with 显式声明对方 id 或对方
                      零件名                                → 配对
  R3 affix_id         去掉"轴颈/孔/孔位/座/套/配合…"等
                      噪声词后余下的标识相同（且非空）
                      （轴颈1 ↔ 孔1 → 都剩下 "1"）          → 配对
  R4 unique_shaft_hole 一侧恰好 1 个 shaft、另一侧恰好 1 个
                      hole（且都带 nominal）                → 配对

配对后判定：
  · 两侧 nominal_mm 都存在且【不相等】→ mismatch（severity=ERROR，
    escalate_to_parent=true）—— 公差带可以不同，基本尺寸必须相同。
  · 同 id 但两侧 type 都是 shaft（或都是 hole）→ warning
    （kind=interface_type_conflict，仍需父代理仲裁）。
  · 两侧 nominal 都缺失 → 该对计入"无接口数据可查"，绝不假 PASS。

另外强制校验（同样进 warnings / dangling）：
  · 单向邻接（A 把 B 列为 adjacent，B 没回列）→ warning one_way_adjacency
  · 悬空邻接（adjacent 里的零件从未登记）      → dangling_adjacency
  · 同零件 dims 与 bbox_mm 包围盒尺寸对不上    → warning self_bbox_mismatch

【禁止假 PASS】返回里恒有 evaluated 字段：
  evaluated=false 表示"没数据可查"（台账空 / 无邻接 / 邻接对全无接口），
  此时 ok 必为 false 且 reason 说明原因 —— 与"真检查通过"严格区分。

═══════════════════════════════════════════════════════════════════════════
【docx 导出：三级策略（本机实测系统 Python 无 python-docx）】
───────────────────────────────────────────────────────────────────────────
  ① try: import docx
  ② 失败 → 自动探测随包运行时的 site-packages（~/.dsh/dsh-runtimes 下，
     含 docx/__init__.py 的 site-packages 目录），加进 sys.path 再试
  ③ 仍失败 → 纯标准库自己写 OOXML（zipfile：
     [Content_Types].xml + _rels/.rels + word/document.xml +
     word/_rels/document.xml.rels + word/styles.xml + docProps/*）
  任何一步都失败 → 返回 ok=false 并说明卡在哪一步，绝不产出坏文件。
  可用 strategy 参数强制走某条路径（"auto"/"python-docx"/"stdlib"），
  便于回归测试逐个验证。

【用法（CLI）】
    python part_registry.py add --part "齿轮轴" --room "传动机构" --file "C:/x.SLDPRT" \
           --material "45#钢" --dims "120x40x40" --adjacent "轴承座,联轴器"
    python part_registry.py get --part "齿轮轴" [--json]
    python part_registry.py list [--json]
    python part_registry.py related --part "齿轮轴" [--depth 2] [--json]
    python part_registry.py reverse --part "轴承座" [--json]
    python part_registry.py check-consistency [--json]
    python part_registry.py for-change --part "齿轮轴" [--json]
    python part_registry.py export-docx --out "台账.docx" [--strategy auto]
    python part_registry.py link --a "齿轮轴" --b "轴承座"
    （另有 update / remove 两个便利子命令）

【用法（import）】
    from part_registry import add_part, related_parts, check_adjacency_consistency
    add_part({"name": "齿轮轴", "room": "传动机构", "file": "C:/x.SLDPRT",
              "dims": {"length_mm": 120, "width_mm": 40, "height_mm": 40},
              "interfaces": [{"id": "轴颈1", "type": "shaft", "nominal_mm": 25.0}]})

【退出码】 0=ok / 1=操作失败或未通过校验 / 2=命令行用法错误
═══════════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import zipfile

# ── Windows 控制台 UTF-8（与 _utf8.py 同口径，但本模块不依赖它，保持自包含）──
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ══════════════════════════════════════════════════════════════════════════
#  常量
# ══════════════════════════════════════════════════════════════════════════

SCHEMA = "dsh-engineering/part-registry@1"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOAD_CASES_DIRNAME = "load_cases"
REGISTRY_FILENAME = "part_registry.json"
ENV_REGISTRY = "DSH_PART_REGISTRY"

# 修订历史只保留最近 N 条，防台账无限膨胀（台账本身是追加式的）
MAX_REVISIONS = 2000

# ══ 【强制·如实填写】变更说明（reason）══════════════════════════════════
# 用户要求："每个零件改一次都要如实填写对应的数据，代码必须执行"。
# 因此只要【真的改动了已登记的数据】，就必须给出变更说明：
#   · 空串 / 纯空白 / 占位词（"无""待定""N/A"…）→ 拒绝落盘；
#   · 太短（少于 MIN_REASON_CHARS 个字符）→ 拒绝（"改"这种一个字的说明没有信息量）；
#   · 纯标点 / 单一重复字符（"aaaaa"）→ 拒绝。
# 说明本身会写进 revisions[].reason，事后可完整回溯"谁在何时为什么改了哪个尺寸"。
MIN_REASON_CHARS = 5
_REASON_PLACEHOLDERS = {
    "无", "暂无", "没有", "待定", "待补充", "待确认", "略", "略过", "同上", "如上",
    "不填", "免填", "空", "null", "none", "nil", "n/a", "na", "n.a.", "xxx", "xx",
    "test", "testing", "测试", "临时", "随便", "占位", "-", "--", "---", "...",
    "。", "，", "、", "tbd", "todo", "fixme", "asdf", "aaa", "???",
}

# 需要【变更说明】的写操作（全部落盘型操作都在内）
REASON_REQUIRED_OPS = ("add", "update", "link", "unlink", "remove")

# ══ 记账字段（bookkeeping）══════════════════════════════════════════════
# 这些字段会随每次调用刷新（例如 reported_at 取当前时间），但它们【不代表
# 零件数据发生了实质变化】，因此：
#   · 刷新它们【不】算作"改动"（不计入 changed、不触发 rev 增长、不产生修订）；
#   · 否则同一份数据重复登记会被当成改过，rev 无意义增长、空改动检测随机失效
#     （实测踩到：同一秒内调用看似正常，跨秒就变成 update，属时序不确定性缺陷）。
_BOOKKEEPING_FIELDS = ("reported_at",)

# add_part 的必填字段（缺任一 → {"ok": False, "error": ...}）
REQUIRED_FIELDS = ("name", "room", "file")

# ══ 【Bug⑪】零件 origin（原点）约定：从 <零件>.origin.json 读取 ══════════
# 现场缺陷（竞速小车任务，装配前必须人工换算坐标）：
#   · 后轮 / 电机座：origin 在轮/电机轴心，z=0 为轴面；
#   · 万向轮支架：origin 在【件角】（非中心），顶面 z=0 为装配面，叉口向下；
#   · 底盘：origin 在底盘中心，z=3 为顶面。
#   三种约定并存 → 总装房必须逐件换算，按"中心对称"直觉装配会全部错位。
#
# 约定来源：swapi 在 save()/save_as() 时把 origin 约定写到 <零件>.origin.json
#   （见 swapi.ORIGIN_SCHEMA）。本模块【只读】它，把结论并入台账记录：
#     · 台账里多一个 origin 字段，装配/一致性核对可直接消费；
#     · 缺记录时显式标注 origin_missing=True 并给 warning —— 绝不假装已知。
ORIGIN_RECORD_SUFFIX = ".origin.json"
ORIGIN_STANDARD_MODE = "center_bottom"     # 标准约定：底面中心
ORIGIN_STANDARD_CN = "底面中心（标准约定）"

# update_part 允许被打补丁的字段白名单（name 不允许改：改名会撕裂邻接图）
# 【Bug⑪】新增 origin：origin 约定是零件的事实之一，改约定必须走 update
# 【Bug①】新增 material_consistency：材料一致性是客观事实，可显式覆盖（如人工核实后更正）
PATCHABLE_FIELDS = ("room", "file", "dims", "bbox_mm", "material", "credentials",
                    "interfaces", "adjacent", "reported_by", "reported_at",
                    "origin", "material_consistency")

# 接口类型同义词 → 归一化类型
_TYPE_SYNONYMS = {
    "shaft": "shaft", "轴": "shaft", "轴颈": "shaft", "轴头": "shaft",
    "轴伸": "shaft", "journal": "shaft", "轴段": "shaft",
    "hole": "hole", "孔": "hole", "孔位": "hole", "孔径": "hole",
    "bore": "hole", "内孔": "hole", "配合孔": "hole",
    "face": "face", "面": "face", "平面": "face",
    "slot": "slot", "槽": "slot", "键槽": "slot",
    "thread": "thread", "螺纹": "thread",
    "flange": "flange", "法兰": "flange",
}

# R3：配对时先去掉的"噪声词"—— 按接口类型分表（见 _strip_id_noise 的说明）。
# 之所以不合并成一张表：合并后 "键槽1" 与 "孔径1" 都会被削成 "1"，
# 于是键槽被误当成轴承孔，报出根本不存在的尺寸冲突（实测踩到）。
_HOLE_NOISE = ("安装孔", "配合孔", "螺栓孔", "孔位", "孔径", "内孔", "中心孔",
               "过孔", "孔", "口")
_SHAFT_NOISE = ("轴颈", "轴头", "轴伸", "轴段", "轴肩", "轴", "端")
_GENERIC_NOISE = ("法兰盘", "法兰", "端面", "平面", "键槽", "槽", "面", "座", "套")
# 兼容别名：旧代码/外部若有引用 _ID_NOISE，仍可用（等价于三类并集）
_ID_NOISE = _HOLE_NOISE + _SHAFT_NOISE + _GENERIC_NOISE

# R2：显式声明配对关系的字段名（大小写不敏感）
_MATE_FIELDS = ("mates_with", "mate_with", "pairs_with", "mate", "pair_with",
                "mates", "配合对象", "配对")

# 公差带字母 → 孔/轴（GB/T 1800 / ISO 286：孔用大写，轴用小写）
_HOLE_TOL_LETTERS = set("ABCDEFGH")
_SHAFT_TOL_LETTERS = set("JKMNPRSTUVXYZ") | set("jkmnprstuvxyz") | set("abcdefg")


# ══════════════════════════════════════════════════════════════════════════
#  通用小工具
# ══════════════════════════════════════════════════════════════════════════

def _now():
    """返回 (时间戳, 'YYYY-mm-dd HH:MM:SS')。"""
    return time.time(), time.strftime("%Y-%m-%d %H:%M:%S")


def _fmt_num(v):
    """数值 → 人类可读字符串（25.0 / 24.5 / 0.125），用于 detail 文案。"""
    try:
        f = float(v)
    except Exception:
        return str(v)
    if abs(f - round(f)) < 1e-9:
        return "%.1f" % f
    s = ("%.4f" % f).rstrip("0").rstrip(".")
    return s or "0"


def _as_float(v):
    """宽松取数：数值或数字字符串 → float；其余返回 None（不抛异常）。"""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip())
        except Exception:
            return None
    return None


def _norm_name(name):
    """零件名归一化：去首尾空白（中文名不做大小写折叠）。"""
    return str(name or "").strip()


#: 调试/测试件的命名特征（大小写不敏感的前缀 / 包含模式）。
#  现场（20261111_131815_580E38FA）：台账 15 条里 6 条是这种东西 ——
#    测试块、DIAG_下箱座_debug、DIAG_min_block、DIAG_rightplane、
#    DIAG_manualcut、DIAG_manualcut2，且 parts/ 下文件早已被清掉。
#  这些件不是交付物：会被查重闸/相邻尺寸核对当成真零件，也会混进导出的 docx。
#  刻意【只做特征命中，不做全名白名单】——小屋的调试件名字千变万化。
_DEBUG_PART_PREFIXES = ("diag_", "diag-", "probe_", "probe-", "debug_",
                        "debug-", "tmp_", "temp_", "scratch_")
_DEBUG_PART_CONTAINS = ("_debug", "-debug", "_test", "-test", "_尝试",
                        "_临时", "_备份", "_bak")


def _looks_like_debug_part(name):
    """判断零件名是否像【调试/测试件】。命中返回命中的特征串，否则 None。

    只读名字、不看其它字段 —— 判据必须可复核、可解释（本仓库一贯要求）。
    """
    s = _norm_name(name)
    if not s:
        return None
    low = s.casefold()
    for pfx in _DEBUG_PART_PREFIXES:
        if low.startswith(pfx):
            return pfx
    for sub in _DEBUG_PART_CONTAINS:
        if sub in low:
            return sub
    # 纯中文的"测试块 / 测试件"这类：中文不做 casefold，单独判前缀
    for cn in ("测试块", "测试件", "临时件", "草稿件"):
        if s.startswith(cn):
            return cn
    return None


def _norm_key(name):
    """零件名比较键：去空白 + casefold（用于宽松命中已登记零件）。"""
    return _norm_name(name).casefold()


def _resolve_key(parts, name):
    """在已登记零件里定位 name 的真实键。

    顺序：精确 → 去空白精确 → casefold 精确。找不到返回 None。
    这样调用方写错大小写/多打空格仍能命中，但绝不模糊匹配到别的零件。
    """
    if not isinstance(parts, dict):
        return None
    raw = _norm_name(name)
    if raw in parts:
        return raw
    target = _norm_key(raw)
    for k in parts:
        if _norm_key(k) == target:
            return k
    return None


def _parse_list_arg(value):
    """'A,B, C' / ['A','B'] / None → ['A','B']（去空、去重、保序）。"""
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple)) else str(value).split(",")
    out = []
    for it in items:
        s = _norm_name(it)
        if s and s not in out:
            out.append(s)
    return out


# ══════════════════════════════════════════════════════════════════════════
#  台账路径 / 读写
# ══════════════════════════════════════════════════════════════════════════

def registry_path(work_dir=None):
    """解析台账文件绝对路径（不创建目录）。

    优先级：DSH_PART_REGISTRY 环境变量 > work_dir 参数 > 脚本目录/load_cases。
    work_dir 若本身就是 load_cases 目录，则直接在其中落文件。
    """
    env = (os.environ.get(ENV_REGISTRY) or "").strip().strip('"').strip("'")
    if env:
        return os.path.abspath(os.path.expanduser(env))

    if work_dir:
        wd = os.path.abspath(os.path.expanduser(str(work_dir)))
        if os.path.basename(wd).lower() == LOAD_CASES_DIRNAME:
            return os.path.join(wd, REGISTRY_FILENAME)
        return os.path.join(wd, LOAD_CASES_DIRNAME, REGISTRY_FILENAME)

    # ── 【R2 修复】默认台账必须属于【当前任务】──────────────────────────
    # 现场（2026-11-10 复检）：不设 DSH_PART_REGISTRY 时默认落到
    #   <tools>/load_cases/part_registry.json（上一任务的台账）→
    #   add_part 被 C8 的 TASK_ID_MISMATCH 正确拒绝 → "run 自动登记"
    #   即使修好 R1 也写不进去。
    # 修法：无显式 work_dir/环境变量时，从 workflow_state.json 取本任务
    #   work_dir，台账落到 <work_dir>/load_cases/part_registry.json
    #   （与显式 work_dir 的口径完全一致）。取不到才回退旧默认。
    try:
        _wd_cur = _current_work_dir()
        if _wd_cur and os.path.isdir(_wd_cur):
            return os.path.join(_wd_cur, LOAD_CASES_DIRNAME, REGISTRY_FILENAME)
    except Exception:
        pass
    return os.path.join(BASE_DIR, LOAD_CASES_DIRNAME, REGISTRY_FILENAME)


def _current_work_dir():
    """从 workflow_state.json 取【本任务】交付目录（只读、失败返回 None）。

    与 _current_task_id 同源（_state_candidates），只是取 work_dir 字段。
    """
    for p in _state_candidates(None):
        try:
            if not os.path.isfile(p):
                continue
            with open(p, "r", encoding="utf-8-sig") as f:
                d = json.load(f) or {}
            wd = str(d.get("work_dir") or "").strip()
            if wd:
                return os.path.abspath(wd)
        except Exception:
            continue
    return None


def _fresh_registry():
    """空台账骨架。"""
    _ts, _at = _now()
    return {
        "schema": SCHEMA,
        "task_id": None,
        "task_id_source": None,
        "created_at": _at,
        "updated_at": _at,
        "revisions": [],
        # 经用户【如实说明理由】后仍允许保留的已知不一致（不许静默放过）
        "unresolved": [],
        "parts": {},
    }


def _read_task_id(work_dir=None):
    """尽力从 workflow_state.json 取当前任务号，用于给台账打任务戳。

    只读、失败即放弃（台账不因缺少门禁状态而不可用）。
    返回 (task_id, 来源说明)。
    """
    cands = []
    if work_dir:
        cands.append(os.path.join(os.path.abspath(str(work_dir)), "workflow_state.json"))
    cands.append(os.path.join(BASE_DIR, "workflow_state.json"))
    try:
        import _store  # 工程模式状态单一事实源（存在则优先用它）
        cands.insert(0, os.path.join(_store.state_dir(), "workflow_state.json"))
    except Exception:
        pass
    for p in cands:
        try:
            if not os.path.isfile(p):
                continue
            with open(p, "r", encoding="utf-8-sig") as f:
                d = json.load(f) or {}
            tid = d.get("task_id")
            if tid:
                return str(tid), p
        except Exception:
            continue
    return None, None


# ══ 【C8 修复】台账必须属于【当前任务】═══════════════════════════════════
# 现场（缺陷清单 C8 / P2）：
#   同一台机器换任务后，读的是【上一轮任务】留下的 load_cases/part_registry.json。
#   于是"一致性检查"报出的是旧任务的零件/邻接，看起来是"当前任务数据不一致"，
#   实际是拿错了台账 —— 典型的【一致性假阳性】。
#   更糟的是写路径：新任务的零件被 add 进旧台账，task_id 一栏如果已经有了旧任务
#   的戳，_set_task_id 会直接 return（"已打过戳"），旧任务号就永久留在新台账里，
#   之后任何"按 task_id 比对"的地方都再也对不上。
#
# 根因：
#   ① _read_task_id() 只是"尽力取一个"，取不到就放弃（不区分"没有权威来源"和
#      "权威来源说是 A、台账却写着 B"）；
#   ② 读台账时【完全没有】校验台账的 task_id 是否等于当前 workflow_state 的
#      task_id —— 旧台账被当成当前台账，静默通过。
#
# 修复：
#   ① 新增 _current_task_id()：只读【权威状态目录】的 workflow_state.json；
#   ② 新增 _task_id_check()：台账 task_id 与当前 task_id 比对，不一致 → 明确的
#      TASK_ID_MISMATCH（列出两边 task_id + 台账路径 + 状态文件路径 + 怎么修）；
#   ③ _set_task_id() 不再"有戳就不管"：戳属于旧任务 → 拒绝写入（而不是静默继承）；
#   ④ _load_registry() 默认校验（strict_task_id=True），所有写路径都在这里返回
#      明确错误、绝不落盘；只读查询/工具命令（list/info/report/export）传
#      strict_task_id=False，改由结果里的 task_id_mismatch 字段给出警告。


def _state_candidates(work_dir=None):
    """workflow_state.json 的候选路径（按权威程度排序，去重保序）。

    顺序与 _read_task_id 一致：显式 work_dir > _store 权威状态目录 > 本脚本目录。
    """
    out = []
    if work_dir:
        out.append(os.path.join(os.path.abspath(str(work_dir)), "workflow_state.json"))
    try:
        import _store  # 工程模式状态单一事实源（存在则优先用它）
        out.append(os.path.join(_store.state_dir(), "workflow_state.json"))
    except Exception:
        pass
    out.append(os.path.join(BASE_DIR, "workflow_state.json"))
    seen, uniq = set(), []
    for p in out:
        k = os.path.abspath(p).lower()
        if k not in seen:
            seen.add(k)
            uniq.append(os.path.abspath(p))
    return uniq


def _current_task_id(work_dir=None):
    """当前任务号（只认 workflow_state.json）。返回 (task_id, 来源路径)。

    与 _read_task_id 的区别：不再把"台账自己的 task_id"混进候选来源 ——
    一个只会自证的值不能用来判断"这是不是当前任务"。
    """
    for p in _state_candidates(work_dir):
        try:
            if not os.path.isfile(p):
                continue
            with open(p, "r", encoding="utf-8-sig") as f:
                d = json.load(f) or {}
            tid = d.get("task_id")
            if tid:
                return str(tid), p
        except Exception:
            continue
    return None, None


def _task_id_check(data, work_dir=None, action=""):
    """台账 task_id 与当前任务号的一致性检查（C8 的核心判据）。

    Returns: None（通过/无法判定/纯历史归档）或 明确的错误 dict。
      · 两边都有值且不等 → TASK_ID_MISMATCH（绝不静默通过）
      · 台账无戳（新台账）→ 通过，由 _set_task_id 补戳
      · 当前任务号取不到（例如手工在 shell 里跑）→ 通过，但不假装"已核对"
    """
    ledger_tid = str((data or {}).get("task_id") or "").strip() or None
    cur_tid, cur_src = _current_task_id(work_dir)
    path = registry_path(work_dir)
    if ledger_tid and cur_tid and ledger_tid != cur_tid:
        return {
            "ok": False,
            "gate": "TASK_ID_MISMATCH",
            "rejected": True,
            "action": action or None,
            "ledger_task_id": ledger_tid,
            "current_task_id": cur_tid,
            "registry_file": path,
            "registry_task_id_source": (data or {}).get("task_id_source"),
            "workflow_state_file": cur_src,
            "error": ("台账不属于当前任务：台账 task_id=%s，当前任务 task_id=%s。"
                      "（台账：%s；当前任务：%s）"
                      % (ledger_tid, cur_tid, path, cur_src)),
            "why": ("同一台机器换任务后，默认台账路径仍然指向上一轮的台账 —— "
                    "旧零件/旧邻接会被当成当前数据，一致性检查因此报出假阳性，"
                    "新任务的数据还会被追加进旧台账。"),
            "hint": ("处理方式（任选其一，别直接改 task_id 蒙混过关）：\n"
                     "  ① 换任务时用独立的台账文件："
                     "设置 DSH_PART_REGISTRY=<新任务目录>/part_registry.json，"
                     "或给 add_part 传 work_dir=<当前任务目录>；\n"
                     "  ② 确属同一任务的延续（workflow_state 刚被重建）："
                     "把 workflow_state.task_id 恢复成本台账的 task_id；\n"
                     "  ③ 旧台账要留着审计：改名备份（如 "
                     "part_registry.<旧task_id>.json）后让本任务从空台账开始。"),
        }
    return None


def _parts_map(data):
    """安全地取台账的 parts 字典（拦截/空骨架时返回 {}，绝不抛 KeyError）。

    【C8 修复】配套：_load_registry 在任务号不一致时会返回【空骨架】+ 明确
    错误，此时调用方若继续 data["parts"][...] 会 KeyError，把"清清楚楚的
    TASK_ID_MISMATCH"变成"命令执行异常"，反而更难排查。
    """
    try:
        _p = (data or {}).get("parts")
        return _p if isinstance(_p, dict) else {}
    except Exception:
        return {}


def _load_registry(work_dir=None, strict_task_id=True):
    """读台账。返回 (data, path, error)。

    ── 损坏保护（代码级强制）────────────────────────────────────────────
    文件存在但不是合法 JSON / 顶层不是对象 → 返回 error，且【后续写操作
    一律拒绝落盘】（见 _save_registry 的 error 传递），避免一次误写把
    用户攒了半天的台账覆盖成空骨架。

    ── 【C8 修复】任务归属校验（strict_task_id）─────────────────────────
    strict_task_id=True（默认，写路径）：台账 task_id 与当前 workflow_state
      的 task_id 不一致 → 直接返回 error（gate=TASK_ID_MISMATCH），
      调用方必须 fail-closed，绝不允许把新任务的数据写进旧任务的台账。
    strict_task_id=False（只读查询/工具命令）：不拦截，改由
      _task_id_mismatch_warning() 在结果里回显警告 —— 让"我看到的是旧台账"
      这件事对模型可见，而不是把旧数据当成当前事实。
    """
    path = registry_path(work_dir)
    if not os.path.isfile(path):
        return _fresh_registry(), path, None
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except Exception as e:
        return _fresh_registry(), path, "台账文件不是合法 JSON：%s（%r）" % (path, e)
    if not isinstance(data, dict):
        return _fresh_registry(), path, "台账文件顶层必须是 JSON 对象：%s" % path
    # 结构自愈：字段缺失/类型不对时补齐（不丢已有数据）
    data.setdefault("schema", SCHEMA)
    data.setdefault("task_id", None)
    data.setdefault("created_at", None)
    data.setdefault("updated_at", None)
    if not isinstance(data.get("revisions"), list):
        data["revisions"] = []
    if not isinstance(data.get("unresolved"), list):
        data["unresolved"] = []
    if not isinstance(data.get("parts"), dict):
        data["parts"] = {}
    # ── 【C8 修复】任务归属校验（见函数 docstring 的 strict_task_id 说明）──
    _mm = _task_id_check(data, work_dir, action="load")
    if _mm:
        if strict_task_id:
            _remember_gate(_mm)
            return _fresh_registry(), path, _mm["error"]
        # 只读模式：不拦截，但把警告挂到返回的数据上（调用方会原样回显）
        data["task_id_mismatch"] = _task_id_mismatch_warning(_mm)
    return data, path, None


def _save_registry(data, work_dir=None):
    """写台账（原子替换：先写 .tmp 再 os.replace）。

    返回 (ok, path, error)。data 为空/非 dict 时拒绝写。
    """
    if not isinstance(data, dict):
        return False, registry_path(work_dir), "内部错误：待写入的台账不是 dict"
    path = registry_path(work_dir)
    _ts, at = _now()
    data["schema"] = SCHEMA
    data["updated_at"] = at
    if len(data.get("revisions") or []) > MAX_REVISIONS:
        data["revisions"] = data["revisions"][-MAX_REVISIONS:]
    try:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True, path, None
    except Exception as e:
        return False, path, "写入台账失败：%r" % (e,)


def _append_revision(data, action, part, changes=None, extra=None, reason=None,
                     verification=None, fields=None, stamp_parts=None):
    """向 revisions 追加一条修订记录（追加式历史，永不改写旧记录）。

    【强制·如实填写】每条修订都带 reason（变更说明）。调用方（写闸门）已保证
    reason 非空且不是占位词；这里再兜一层，防止有人绕过闸门直接调本函数 ——
    缺 reason 的记录会被打上 "__UNVERIFIED__" 标记，audit_verification 能查出来。

    Args:
        stamp_parts: 本次动作【连带改动】的其它零件名列表。像 remove_part 会顺手
            清理别人的 adjacent、link 会同时改两侧 —— 这些被连带改动的零件也必须
            刷新指纹，否则 audit 会把这次【合法】改动误报成绕过模块手改（实测踩到）。
            这里为每个连带零件再追加一条 revisions，记录其改动后的 part_digest。

    返回新记录的 rev 序号（即该零件本次的版本号）。
    """
    rev = 1
    rec = data["parts"].get(part)
    if isinstance(rec, dict):
        try:
            rev = int(rec.get("rev") or 1)
        except Exception:
            rev = 1
    _ts, at = _now()
    _reason = str(reason or "").strip()
    entry = {
        "rev": rev,
        "action": action,
        "part": part,
        "ts": _ts,
        "at": at,
        "changes": changes or {},
        "reason": _reason or "__UNVERIFIED__",
    }
    if fields is not None:
        entry["declared_fields"] = list(fields)
    if verification is not None:
        # ── 总理子代理核实的证据（可事后重算 digest 验证真实性）──────────
        # part_digest = 【本记录所属零件】的实质数据指纹，是逐零件审计的依据：
        # 审计时只需找"该零件最后一次被写入"时留下的 part_digest 与现状比对，
        # 就能精确判定它是被合法改过还是被绕过模块手改（不受其它零件影响）。
        _own = data["parts"].get(part)
        entry["verification"] = {
            "verified": verification.get("verified"),
            "evaluated": verification.get("evaluated"),
            "skipped": bool(verification.get("skipped")),
            "scope": verification.get("scope"),
            "neighbors_checked": verification.get("neighbors_checked"),
            "digest": verification.get("digest"),
            "part_digest": (_canonical_digest(_substance(_own))
                            if isinstance(_own, dict) else None),
            "mismatch_count": len(verification.get("mismatches") or []),
            "mismatches": verification.get("mismatches") or [],
            "reason": verification.get("reason"),
            "summary": verification.get("summary"),
        }
    if extra:
        entry.update(extra)
    data["revisions"].append(entry)

    # ── 连带改动的零件也要刷新指纹（否则合法改动会被 audit 误报为手改）──
    # remove_part 清理别人的 adjacent、link 同时改两侧 —— 这些"被动改到"的
    # 零件同样需要一个记录了改动后数据的 part_digest。
    for other in (stamp_parts or []):
        if other == part:
            continue
        _orec = data["parts"].get(other)
        if not isinstance(_orec, dict):
            continue
        try:
            _orev = int(_orec.get("rev") or 1)
        except Exception:
            _orev = 1
        data["revisions"].append({
            "rev": _orev,
            "action": action + ":collateral",
            "part": other,
            "ts": _ts, "at": at,
            "changes": changes or {},
            "reason": _reason or "__UNVERIFIED__",
            "collateral_of": part,
            "verification": {
                "verified": None, "evaluated": False, "skipped": False,
                "scope": [other], "neighbors_checked": 0, "digest": None,
                "part_digest": _canonical_digest(_substance(_orec)),
                "mismatch_count": 0, "mismatches": [],
                "reason": "因 %s 的动作 %s 被连带改动，记录改动后指纹" % (part, action),
                "summary": "连带改动指纹（%s ← %s）" % (other, part),
            },
        })
    return rev


def audit_verification(name=None, work_dir=None):
    """★ 事后审计：证明台账数据没有被绕过模块手改（真实性的可验证性）。

    ── 审计口径（关键：必须逐零件、只看"最后一次写入"）──────────────────
    早期实现拿每条历史记录的 scope-digest 与现状比，并且"只要范围内任意零件
    后来被合法改过"就判为正常 —— 结果掩盖了真实篡改：
    手改了 A，只要 B 后来被合法改过，A 的篡改就被解释掉了（实测踩到漏洞）。

    现在改成【逐零件、只认最后一次写入】：
      ① 对每个零件，找出台账里【最后一次写入它】的那条带 part_digest 的记录；
      ② 用当前磁盘数据重算该零件的实质数据指纹；
      ③ 一致 → 该零件自最后写入以来未被改动（无论中间有多少其它改动）；
         不一致 → 该零件在最后一次合法写入之后被改过，而台账里【没有】
                  任何更晚的合法写入能解释它 ⇒ 判定为绕过模块手改。

    这样：正常迭代（每次改动都走模块、都会留下更新的 part_digest）永远自洽；
    只有真正的手改才会暴露。
    """
    # 【C8 修复】只读查询：不拦截旧台账，但在结果里回显 task_id 不一致警告
    data, path, err = _load_registry(work_dir, strict_task_id=False)
    if err:
        return {"ok": False, "error": err, "file": path}
    parts = data.get("parts") or {}
    revs = [r for r in (data.get("revisions") or []) if isinstance(r, dict)]

    # ── 每个零件最后一次带 part_digest 的写入 ──────────────────────────
    # 注意：remove 记录写的是【被删零件】，它不在 parts 里；要单独处理，
    # 否则一次合法删除会被误判成"绕过模块删除"（实测踩到这个误报）。
    last_write = {}
    removed_by_module = {}
    unverified = []
    for i, r in enumerate(revs):
        v = r.get("verification") or {}
        pt = r.get("part")
        if r.get("deleted_by_module"):
            removed_by_module[pt] = r
            continue
        if not v.get("part_digest"):
            unverified.append({"seq": i, "rev": r.get("rev"), "action": r.get("action"),
                               "part": pt, "at": r.get("at"),
                               "reason": ("该修订没有零件指纹"
                                          "（skip_verification、旧记录，或绕过闸门写入）"
                                          if r.get("reason") != "__UNVERIFIED__"
                                          else "该修订是绕过闸门写入的（reason=__UNVERIFIED__）")})
            continue
        last_write[pt] = (i, r)

    tampered, intact_rows = [], []
    for pt, (seq, r) in sorted(last_write.items(), key=lambda kv: str(kv[0])):
        if name and _norm_key(pt) != _norm_key(name):
            continue
        rec = parts.get(pt)
        if rec is None:
            # 零件不在台账里了：若它是在【本次写入之后】被模块合法删除的，属正常；
            # 否则说明有人绕过模块把它抠掉了。
            dele = removed_by_module.get(pt)
            if dele is not None:
                intact_rows.append({"part": pt, "rev": r.get("rev"),
                                    "last_action": r.get("action"), "at": r.get("at"),
                                    "reason": r.get("reason"),
                                    "digest": (r.get("verification") or {}).get("part_digest"),
                                    "verified": (r.get("verification") or {}).get("verified"),
                                    "deleted_by_module": True,
                                    "deleted_at": dele.get("at"),
                                    "deleted_reason": dele.get("reason")})
                continue
            tampered.append({"part": pt, "verdict": "DELETED_OUTSIDE_MODULE",
                             "at": r.get("at"), "reason": r.get("reason"),
                             "detail": ("该零件最后一次由模块写入后，又被从台账里删除，"
                                        "但台账没有对应的 remove 修订记录")})
            continue
        now = _canonical_digest(_substance(rec))
        recorded = (r.get("verification") or {}).get("part_digest")
        if now == recorded:
            intact_rows.append({"part": pt, "rev": r.get("rev"),
                                "last_action": r.get("action"), "at": r.get("at"),
                                "reason": r.get("reason"), "digest": recorded,
                                "verified": (r.get("verification") or {}).get("verified")})
        else:
            tampered.append({
                "part": pt, "verdict": "UNEXPLAINED",
                "last_module_write": {"rev": r.get("rev"), "action": r.get("action"),
                                      "at": r.get("at"), "reason": r.get("reason")},
                "digest_recorded": recorded, "digest_now": now,
                "detail": ("零件 %s 在最后一次由模块写入之后，实质数据被改动，"
                           "但台账里没有更晚的合法写入能解释这次变化" % pt),
            })

    # 台账里存在、但从未被本模块写过指纹的零件（例如旧格式/外部塞进来的）
    orphans = []
    for pt in sorted(parts.keys()):
        if pt in last_write:
            continue
        if name and _norm_key(pt) != _norm_key(name):
            continue
        orphans.append({"part": pt, "rev": (parts[pt] or {}).get("rev"),
                        "detail": "该零件在台账里存在，但没有任何带指纹的模块写入记录"})

    if tampered:
        verdict_txt = ("发现 %d 个零件的实质数据【无法解释地被改动】—— "
                       "台账很可能是绕过本模块被直接改过的，请人工核查"
                       % len(tampered))
    elif orphans:
        verdict_txt = ("未发现篡改；但有 %d 个零件缺少指纹记录（旧格式或外部写入），"
                       "无法对其真实性背书" % len(orphans))
    else:
        verdict_txt = ("全部完好：%d 个零件的实质数据与其最后一次模块写入时的指纹一致，"
                       "未发现绕过模块的手改" % len(intact_rows))

    return {
        "ok": True,
        "file": path,
        # 【C8 修复】审计读的是哪个任务的台账，必须说清楚
        "task_id": data.get("task_id"),
        "task_id_mismatch": data.get("task_id_mismatch"),
        "part_filter": name,
        "parts_in_registry": len(parts),
        "parts_with_fingerprint": len(last_write),
        "intact_count": len(intact_rows),
        "intact": intact_rows,
        "tampered": tampered,
        "tampered_count": len(tampered),
        "parts_without_fingerprint": orphans,
        "records_without_digest": unverified,
        "tamper_suspected": bool(tampered),
        "all_explained": (not tampered),
        "verdict": verdict_txt,
        "summary": ("审计 %d 个零件：%d 个完好、%d 个疑似被手改、%d 个无指纹"
                    % (len(parts), len(intact_rows), len(tampered), len(orphans))),
        "note": ("审计是【逐零件、只认最后一次模块写入】的：正常迭代每次都会留下"
                 "更新的指纹，因此不会误报；只有「最后一次合法写入之后数据又变了、"
                 "且台账里找不到任何更晚的合法写入」才会判为篡改。"
                 "指纹只覆盖实质数据（尺寸/接口/邻接/材料等），不含 rev 等记账字段。"),
    }


def _set_task_id(data, work_dir=None):
    """给台账补任务号（仅当尚未打过戳时）。

    ── 【C8 修复】旧实现是"只要台账已经有 task_id 就直接 return" ──────────
    于是【上一轮任务的戳】会被无条件继承：新任务的零件被写进旧台账，而台账
    仍自称属于旧任务 —— 之后任何按 task_id 的比对都永远对不上，一致性检查
    也会拿旧任务的零件当当前数据（假阳性）。

    现在：已有戳时先与【权威来源】比对，
      · 一致   → 通过（正常的同一任务续写）；
      · 不一致 → 返回 TASK_ID_MISMATCH 错误，调用方必须拒绝落盘；
      · 取不到当前任务号 → 保持原样（手工在 shell 里跑时不臆造结论）。

    Returns: None（通过）/ 错误 dict（必须拒绝落盘）
    """
    if data.get("task_id"):
        _mm = _task_id_check(data, work_dir, action="write")
        if _mm:
            _remember_gate(_mm)
            return _mm
        return None
    tid, src = _read_task_id(work_dir)
    if tid:
        data["task_id"] = tid
        data["task_id_source"] = src
    return None


# ══════════════════════════════════════════════════════════════════════════
#  字段校验（全部代码化）
# ══════════════════════════════════════════════════════════════════════════

def _valid_reason(reason, op="update", what=""):
    """★【强制·如实填写】校验变更说明。不通过则返回错误文案，通过返回 None。

    这是"每次改动都必须如实填写对应数据"的代码级闸门 —— 不是注释建议。
    判定（全部强制）：
      ① 必须是字符串或 None；None/空/纯空白 → 拒绝
      ② 去掉空白与标点后，实质字符数 < MIN_REASON_CHARS → 拒绝
      ③ 命中占位词表（"无""待定""N/A""测试"…）→ 拒绝
      ④ 去掉空白后是【单一字符重复】（"aaaaa"）→ 拒绝
    Args:
        op:   操作名（add/update/link/remove），用于错误文案
        what: 改了什么（零件名/邻接对），用于错误文案
    Returns:
        None 表示通过；否则返回 {"error","hint","field"} 用的错误字符串
    """
    if reason is None:
        return ("操作 '%s'%s 必须填写变更说明（reason）—— "
                "台账铁律：每改一次都要如实填写对应数据"
                % (op, ("（%s）" % what) if what else ""))
    if not isinstance(reason, str):
        return "reason 必须是字符串，收到 %s" % type(reason).__name__

    s = reason.strip()
    if not s:
        return ("变更说明不能为空 —— 操作 '%s'%s 必须如实说明改了什么、为什么改"
                % (op, ("（%s）" % what) if what else ""))

    # 去掉空白与常见标点后再看"实质内容"
    core = "".join(ch for ch in s if not ch.isspace()
                   and ch not in "，,。.、;；:：!！?？~～-—_=+*/\\|'\"`()（）[]【】{}<>")
    if len(core) < MIN_REASON_CHARS:
        return ("变更说明过于简短（实质内容 %d 个字符，至少需要 %d 个）：%r —— "
                "请写清改了什么、依据是什么"
                % (len(core), MIN_REASON_CHARS, s))

    if s.lower() in _REASON_PLACEHOLDERS or core.lower() in _REASON_PLACEHOLDERS:
        return ("变更说明是占位词（%r），等于没填 —— 请如实描述本次改动" % s)

    if len(set(core)) == 1:
        return "变更说明是单一字符重复（%r），不是有效说明" % s
    return None


def _require_reason(reason, op, what=""):
    """_valid_reason 的语法糖：返回可直接并入结果 dict 的错误，或 None。"""
    err = _valid_reason(reason, op=op, what=what)
    if not err:
        return None
    return {
        "ok": False,
        "error": err,
        "field": "reason",
        "op": op,
        "rejected": True,
        "hint": ("所有落盘型改动（add/update/link/remove）都必须带 reason。"
                 "CLI 用 --reason \"...\"；代码里传 add_part(..., reason=...) / "
                 "update_part(name, patch, reason=...) / link_adjacent(a, b, reason=...)。"),
    }


def _diff_fields(old_rec, patch, ignore=("reported_at",)):
    """算出 patch 相对 old_rec 真实改动了哪些字段（字段名列表，已排序）。

    用于强制"声明改了哪些字段"与"实际改了哪些字段"完全一致 ——
    谎报/漏报都会被拒绝落盘。
    """
    changed = []
    if not isinstance(old_rec, dict):
        old_rec = {}
    for k, v in (patch or {}).items():
        if k in ignore:
            continue
        if old_rec.get(k) != v:
            changed.append(k)
    return sorted(changed)


def _parse_fields_arg(value):
    """解析 --fields / fields 参数：'dims,material' 或 ['dims','material'] → 排序列表。"""
    if value is None:
        return None
    items = value if isinstance(value, (list, tuple)) else str(value).split(",")
    out = []
    for it in items:
        s = str(it or "").strip()
        if s and s not in out:
            out.append(s)
    return sorted(out)


def _parse_dims(value):
    """解析尺寸。支持 dict 或 '120x40x40' / '120×40×40' / '120,40,40'。

    返回 (dims_dict|None, error|None)。
    规则（强制）：
      · 值必须可转 float 且 >= 0（负数尺寸直接拒绝）；
      · 字符串只接受 3 个数字 → length_mm / width_mm / height_mm；
        给 6 个数字会被拒绝并提示改用 --bbox（避免把包围盒当尺寸）。
    """
    if value is None:
        return None, None
    if isinstance(value, dict):
        if not value:
            return None, None
        out = {}
        for k, v in value.items():
            fv = _as_float(v)
            if fv is None:
                # ══ 【2026-11-11 修复·报错可读性】══════════════════════════
                # 现场（20261111_131815_580E38FA）：小屋把别的零件的整段
                #   {"volume_mm3": 6300, "bbox_mm": [...]} 直接塞进 dims，
                #   旧报错只说"'bbox_mm' 不是数值"，看不出"这是抄错了零件"。
                #   这里点名它其实该走哪个字段，省掉一轮猜。
                _hint = ""
                if str(k) in ("bbox_mm", "bbox"):
                    _hint = ("—— bbox_mm 是包围盒 6 元组，应当作为"
                             "【同级字段 bbox_mm】传入，不能混进 dims；"
                             "dims 只放 length_mm/width_mm/height_mm 这类标量。")
                elif str(k) in ("volume_mm3", "volume"):
                    _hint = ("—— volume_mm3 是体积，不属于外形尺寸；"
                             "dims 只放长/宽/高，体积请单独放实际测量字段。")
                return None, ("dims 字段 %r 不是数值：%r%s"
                              "（若这是从别的零件整段复制的 dims，"
                              "请核对是否抄错了零件）"
                              % (k, v, _hint))
            if fv < 0:
                return None, "dims 字段 %r 为负数（%s），尺寸不允许为负" % (k, _fmt_num(fv))
            out[str(k)] = fv
        return out, None

    s = str(value).strip()
    if not s:
        return None, None
    for ch in ("×", "x", "X", "*", "，", ",", ";", " ", "\t"):
        s = s.replace(ch, "|")
    raw = [p for p in s.split("|") if p.strip()]
    nums = []
    for p in raw:
        fv = _as_float(p)
        if fv is None:
            return None, "dims 无法解析出数值：%r（期望如 '120x40x40'）" % (value,)
        nums.append(fv)
    if len(nums) == 6:
        return None, ("dims 收到 6 个数字（%r）—— 6 元组是包围盒，"
                      "请改用 bbox_mm / --bbox" % (value,))
    if len(nums) != 3:
        return None, "dims 需要 3 个数字（长x宽x高），实际 %d 个：%r" % (len(nums), value)
    if any(n < 0 for n in nums):
        return None, "dims 含负数：%r" % (value,)
    return {"length_mm": nums[0], "width_mm": nums[1], "height_mm": nums[2]}, None


def _parse_bbox(value):
    """解析 SW 真实包围盒 6 元组 [xmin,xmax,ymin,ymax,zmin,zmax]。

    强制校验：长度为 6、全为数值、每对 min <= max。
    返回 (bbox_list|None, error|None)。
    """
    if value is None:
        return None, None
    if isinstance(value, (list, tuple)):
        nums = [_as_float(v) for v in value]
    else:
        s = str(value).strip()
        if not s:
            return None, None
        for ch in ("×", "x", "X", "*", "，", ",", ";", " ", "\t"):
            s = s.replace(ch, "|")
        parts = [p for p in s.split("|") if p.strip()]
        nums = [_as_float(p) for p in parts]
    if any(n is None for n in nums):
        return None, "bbox_mm 必须是 6 个数值：%r" % (value,)
    if len(nums) != 6:
        return None, "bbox_mm 必须是 6 元组 [xmin,xmax,ymin,ymax,zmin,zmax]，实际 %d 个" % len(nums)
    for i, label in ((0, "x"), (2, "y"), (4, "z")):
        if nums[i] > nums[i + 1]:
            return None, ("bbox_mm 的 %s 区间非法：min=%s > max=%s"
                          % (label, _fmt_num(nums[i]), _fmt_num(nums[i + 1])))
    return nums, None


def _norm_iface_type(t):
    """接口类型归一化（中英文同义词 → shaft/hole/face/...）。"""
    s = str(t or "").strip()
    if not s:
        return "unknown"
    low = s.lower()
    if low in _TYPE_SYNONYMS:
        return _TYPE_SYNONYMS[low]
    return low


def _strip_id_noise(s, typ=None):
    """按【接口类型】去掉 id 里的噪声词，返回余下标识（R3 配对判定用）。

    ── 为什么必须按类型分开去噪声 ────────────────────────────────────────
    早期实现用一张全局噪声表，"键槽1" 与 "孔径1" 都会被削成 "1"，
    于是键槽被误判成与轴承孔同一处配合，凭空造出 mismatch（实测踩到）。
    现在只去掉【与该接口类型相符】的噪声词：
      · hole  → 孔/孔径/孔位/内孔/安装孔/配合孔
      · shaft → 轴/轴颈/轴头/轴伸/轴段
      · 其它  → 只去法兰/端面/平面/槽 这类中性词
    这样 "轴颈1"→"1"、"孔1"→"1" 仍能配对，而 "键槽1"→"键槽1"（类型不符，
    不去"槽"以外的词），不会再和孔混淆。
    """
    out = str(s or "")
    if typ == "hole":
        words = _HOLE_NOISE
    elif typ == "shaft":
        words = _SHAFT_NOISE
    else:
        words = _GENERIC_NOISE
    changed = True
    while changed:
        changed = False
        for w in words:
            if w and w in out:
                out = out.replace(w, "")
                changed = True
    return out.strip("-_ #.#、,，()（）").strip()


def _types_complementary(ta, tb):
    """两个接口类型是否构成【互补配合】（轴+孔）。

    R3（去噪声后 id 相同）与 R4（唯一轴孔）都要求互补：
    只有"轴配孔"在物理上才是同一处配合。两个孔（如 轴承座.孔1 与
    底座.安装孔1）不构成配合对，不能仅凭 id 残余相同就判为同一处。
    """
    return {ta, tb} == {"shaft", "hole"}


def _norm_interfaces(value):
    """把 interfaces 归一化成 [{'id','type','nominal','tolerance','raw'}]。

    接收 list[dict] 或 dict（{id: {...}}）；非法的项被【丢弃并记录】到
    第二个返回值，而不是静默吞掉 —— 调用方可据此告警。
    """
    out, dropped = [], []
    items = []
    if value is None:
        return out, dropped
    if isinstance(value, dict):
        for k, v in value.items():
            if isinstance(v, dict):
                it = dict(v)
                it.setdefault("id", k)
            else:
                it = {"id": k, "nominal_mm": v}
            items.append(it)
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        return out, [{"reason": "interfaces 既不是数组也不是对象", "value": repr(value)}]

    for idx, it in enumerate(items):
        if not isinstance(it, dict):
            dropped.append({"index": idx, "reason": "接口项不是对象", "value": repr(it)})
            continue
        iid = _norm_name(it.get("id") or it.get("name") or "")
        if not iid:
            dropped.append({"index": idx, "reason": "接口缺少 id", "value": it})
            continue
        nominal = _as_float(it.get("nominal_mm", it.get("nominal")))
        mates = []
        for f in _MATE_FIELDS:
            if f in it:
                mates.extend(_parse_list_arg(it.get(f)))
        out.append({
            "id": iid,
            "type": _norm_iface_type(it.get("type")),
            "nominal": nominal,
            "tolerance": str(it.get("tolerance") or "").strip(),
            "mates": mates,
            "raw": it,
        })
    return out, dropped


def _tolerance_kind(tol):
    """公差带首字母 → 'hole' / 'shaft' / 'unknown'（GB/T 1800 大小写约定）。"""
    s = str(tol or "").strip()
    if not s:
        return "unknown"
    c = s[0]
    if c in _HOLE_TOL_LETTERS:
        return "hole"
    if c in _SHAFT_TOL_LETTERS:
        return "shaft"
    return "unknown"


def _dims_from_bbox(bbox):
    """由包围盒推出外形尺寸 dict。"""
    if not bbox or len(bbox) != 6:
        return None
    return {
        "length_mm": round(float(bbox[1]) - float(bbox[0]), 4),
        "width_mm": round(float(bbox[3]) - float(bbox[2]), 4),
        "height_mm": round(float(bbox[5]) - float(bbox[4]), 4),
    }


# ══════════════════════════════════════════════════════════════════════════
#  ★【总理子代理核实】写入前强制核实（不许自报，必须代码算出）
# ══════════════════════════════════════════════════════════════════════════
# 用户要求："写进代码的时候，总理子代理也要核实相关数据是否一致，必须要保证真实"。
#
# 【为什么"自报已核实"不算数】
#   如果只是让调用方传一个 verified=True，那是【自我声明】，谁都能写 True，
#   等于没有核实。真实性必须由【代码从磁盘上的真实数据】算出来，并且可复现。
#
# 【本模块的做法：核实 = 计算 + 指纹 + 落账，三者缺一不可】
#   ① 计算：写入前，从【磁盘上重新读取】台账（不信调用方传进来的内存对象），
#      算出本次改动的真实影响范围（该零件 + 所有直接邻接零件），
#      并对该范围跑一遍真实的接口尺寸一致性比对。
#   ② 指纹：把【改动生效后的】影响范围内各零件的真实记录做规范化 JSON
#      序列化后取 SHA-256 —— 这个 verified_digest 是"当时到底核对了哪些数据"
#      的密码学证据，事后任何人都能重算验证。
#   ③ 落账：digest / 范围 / 核对结论 / 核对时间全部写进 revisions[]；
#      以后 audit_verification() 重算 digest，就能证明数据有没有被绕过模块手改。
#
# 【硬闸门】核实发现不一致（mismatch）时：
#   · 默认【拒绝落盘】并 escalate_to_parent=true，把冲突交父代理裁决；
#   · 只有显式 allow_mismatch=True 且【如实填写理由】（理由同样受 _valid_reason
#     强制），才允许带着"已知问题"落盘，并记入 unresolved 台账 ——
#     绝不静默放过，也绝不允许"填个无"糊弄过去。

def _substance(rec):
    """零件记录的【实质数据】投影：只保留会影响装配/配合的字段。

    ── 为什么 digest 不能覆盖整条记录 ────────────────────────────────────
    rev / reported_at 这类记账字段每次正常写入都会变；如果把它们算进指纹，
    那么"上一次核实的指纹"在下一次【合法】改动之后必然对不上 ——
    审计就退化成"永远报警"，等于没有审计价值（实测踩到）。
    因此指纹只覆盖实质数据：尺寸、包围盒、接口、邻接、房间、文件、材料、凭据。
    """
    if not isinstance(rec, dict):
        return rec
    keys = ("name", "room", "file", "dims", "bbox_mm", "material",
            "credentials", "interfaces", "adjacent")
    return {k: rec.get(k) for k in keys if k in rec}


def _substance_payload(parts, scope):
    """影响范围内各零件的实质数据（digest 的输入）。"""
    return {nm: _substance(parts[nm]) for nm in scope if nm in parts}


def _canonical_digest(obj):
    """对任意 JSON 可序列化对象取稳定的 SHA-256 指纹（键排序、紧凑分隔符）。

    稳定性要求：同样的数据，无论字典插入顺序、无论跑多少次，指纹必须一致 ——
    否则"事后重算验证"就失去意义。
    """
    import hashlib
    try:
        blob = json.dumps(obj, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), default=str)
    except Exception:
        blob = repr(obj)
    return hashlib.sha256(blob.encode("utf-8", "replace")).hexdigest()


def _impact_scope(data, name):
    """算出改动 name 的【真实影响范围】：该零件 + 所有直接邻接零件（双向）。

    为什么用"直接邻接"而不是全图：直接接触/配合的零件才可能因尺寸改动而装不上；
    二级以上属于传递影响，由 find_related_for_change 提示，不进强制闸门。
    """
    parts = data.get("parts") or {}
    key = _resolve_key(parts, name)
    if key is None:
        return [], []
    scope = {key}
    reasons = []
    # 出边：我列的邻接
    for raw in _parse_list_arg(parts[key].get("adjacent")):
        k = _resolve_key(parts, raw)
        if k and k != key:
            scope.add(k)
            reasons.append({"part": k, "why": "%s 的 adjacent 列出了它" % key})
    # 入边：谁把我列为邻接
    for src, rec in parts.items():
        if src == key:
            continue
        ks = [_resolve_key(parts, x) or x for x in _parse_list_arg(rec.get("adjacent"))]
        if key in ks:
            if src not in scope:
                reasons.append({"part": src, "why": "%s 把 %s 列为 adjacent" % (src, key)})
            scope.add(src)
    return sorted(scope), reasons


def _verify_impact(data, name, work_dir=None, action="update", detail=None):
    """★ 总理子代理核实：对本次改动的影响范围做真实的一致性核对。

    ── 与调用方"自报"无关 ────────────────────────────────────────────────
    本函数只读 data（调用方刚重读过磁盘的台账）+ 真实计算，不接收任何
    "已核实"标志位。它返回的 mismatches 是从接口数据里真算出来的。

    Returns:
        {"verified": bool,          # 是否真的核对了（有可比对的接口数据）
         "scope": [零件名...],       # 本次核实覆盖的零件
         "scope_reasons": [...],     # 为什么这些零件在范围内
         "digest": "sha256...",      # 影响范围真实数据的指纹（改动生效后重算）
         "mismatches": [...],        # 真实算出的不一致
         "neighbors_checked": n,
         "evaluated": bool, "reason": str|None,
         "summary": "..."}
    """
    parts = data.get("parts") or {}
    key = _resolve_key(parts, name)
    if key is None:
        return {"verified": False, "evaluated": False, "scope": [], "scope_reasons": [],
                "digest": None, "mismatches": [], "neighbors_checked": 0,
                "reason": "零件未登记，无法核实", "summary": "未能核实：零件未登记"}

    scope, scope_reasons = _impact_scope(data, key)
    neighbors = [x for x in scope if x != key]
    # 只核对"该零件 ↔ 每个邻接零件"这些对（定向核对，不外溢到无关零件）
    pairs = [(key, nb) for nb in neighbors]
    core = _check_core(data, only_pairs=pairs) if pairs else {
        "mismatches": [], "pairs_with_interface_data": 0, "pairs": [],
        "warnings": [], "insufficient": [], "dangling": [], "one_way": [], "self_loops": [],
    }

    # ── digest 覆盖【影响范围内各零件的实质数据】（改动已生效后的值）──
    # 只取会影响力/配合判定的字段（见 _substance），不含 rev 等记账字段，
    # 这样"上一版与这一版实质数据相同"时指纹稳定，审计不会永远误报。
    digest = _canonical_digest(_substance_payload(parts, scope))
    record_digest = _canonical_digest(parts.get(key) or {})

    with_data = core.get("pairs_with_interface_data", 0)
    mismatches = core.get("mismatches") or []
    evaluated = bool(neighbors) and with_data > 0

    if not neighbors:
        reason = ("零件 %s 没有任何邻接零件（adjacent 为空且无人引用它）—— "
                  "无相关数据可核实；若它确实与其他零件配合，请先 link_adjacent" % key)
    elif with_data == 0:
        reason = ("零件 %s 有 %d 个邻接零件，但没有任何一对声明了可比较的 "
                  "interfaces/nominal_mm —— 相关数据缺失，无法核实一致性（这不是通过）"
                  % (key, len(neighbors)))
    else:
        reason = None

    if mismatches:
        summary = ("核实未通过：影响范围内发现 %d 处尺寸不一致 —— 拒绝落盘，交父代理裁决"
                   % len(mismatches))
    elif not evaluated:
        summary = "未能核实：%s" % reason
    else:
        summary = ("核实通过：已核对 %s 与 %d 个直接邻接零件的接口尺寸，"
                   "未发现不一致（digest=%s）" % (key, len(neighbors), digest[:16]))

    return {
        "verified": bool(evaluated and not mismatches),
        "evaluated": bool(evaluated),
        "scope": scope,
        "scope_reasons": scope_reasons,
        "neighbors_checked": len(neighbors),
        "digest": digest,
        "record_digest": record_digest,
        "mismatches": mismatches,
        "warnings": core.get("warnings") or [],
        "insufficient": core.get("insufficient") or [],
        "dangling_adjacency": core.get("dangling") or [],
        "one_way_adjacency": core.get("one_way") or [],
        "reason": reason,
        "action": action,
        "detail": detail,
        "summary": summary,
    }


def _record_unresolved(data, name, verification, reason, action="update"):
    """把"经如实说明理由后仍保留的不一致"记入 unresolved 台账（可追溯、不静默）。

    ── 去重（避免同一处不一致被反复登记）────────────────────────────────
    一次核实的范围是"该零件 + 全部直接邻接零件"，因此既可能查出本次新引入的
    不一致，也会再次看到【先前已登记过】的旧不一致。若不去重，同一个问题会随
    每次改动重复堆积（实测：link 两次后同一处 A-B 被记了 2 条）。
    这里以 (零件对, 接口, 差异描述) 为键，只对仍处于 open 的记录去重。
    """
    _ts, at = _now()
    existing = data.setdefault("unresolved", [])
    open_keys = set()
    for u in existing:
        if not isinstance(u, dict):
            continue
        if str(u.get("status") or "open").lower() != "open":
            continue
        open_keys.add((tuple(u.get("parts") or []), u.get("interface"), u.get("detail")))

    added = 0
    for m in verification.get("mismatches") or []:
        key = (tuple(m.get("parts") or []), m.get("interface"), m.get("detail"))
        if key in open_keys:
            continue
        open_keys.add(key)
        existing.append({
            "part": name,
            "parts": m.get("parts"),
            "interface": m.get("interface"),
            "detail": m.get("detail"),
            "severity": m.get("severity") or "ERROR",
            "escalate_to_parent": True,
            "action": action,
            "reason": reason,
            "digest": verification.get("digest"),
            "scope": verification.get("scope"),
            "ts": _ts, "at": at,
            "status": "open",
        })
        added += 1
    return added


def _write_gate(data, name, reason, work_dir, action, allow_mismatch=False,
                skip_verify=False):
    """★ 所有落盘型改动共用的强制闸门：如实填写 + 总理子代理核实。

    顺序（任一不过就返回错误，调用方必须直接 return，不得落盘）：
      ① 变更说明如实填写检查（_valid_reason）
      ② 总理子代理核实（_verify_impact）—— 真实计算，不看调用方声明
      ③ 核实发现不一致 → 默认拒绝；allow_mismatch=True 时需【再次】如实填写理由，
         并记入 unresolved 台账

    Returns: (error_dict | None, verification | None)
    """
    err = _require_reason(reason, op=action, what=name)
    if err:
        return err, None

    if skip_verify:
        # 跳过核实也要留下【明确的痕迹】：skipped=True + evaluated=False，
        # 且不产生 part_digest —— audit 会把该零件列入"无指纹、不背书"清单。
        return None, {"verified": None, "evaluated": False, "skipped": True,
                      "scope": [], "neighbors_checked": 0, "digest": None,
                      "mismatches": [], "reason": "调用方显式跳过核实（skip_verification=True）",
                      "summary": "已跳过总理子代理核实 —— 本次改动未经验证，可由 audit 查出"}
    v = _verify_impact(data, name, work_dir=work_dir, action=action, detail=reason)
    mm = v.get("mismatches") or []
    if mm:
        if not allow_mismatch:
            return {
                "ok": False,
                "gate": "CONSISTENCY_VERIFICATION_FAILED",
                "rejected": True,
                "part": name,
                "action": action,
                "error": ("【核实未通过·拒绝落盘】总理子代理核实发现 %d 处相关数据不一致，"
                          "已阻止本次改动写入台账" % len(mm)),
                "verification": v,
                "mismatches": mm,
                "escalate_to_parent": True,
                "hint": ("这是保护，不是故障：相邻零件尺寸对不上时先改数据，别先写台账。"
                         "处理方式二选一：\n"
                         "  1) 把相关零件的接口尺寸改一致后重试（推荐）；\n"
                         "  2) 确属设计允许（如过渡配合/预留余量）时，显式传 "
                         "allow_mismatch=True 并【如实写明理由】，"
                         "该不一致会记入台账 unresolved 清单，不会静默消失。"),
            }, None
        # 允许带病落盘，但理由必须【额外】如实填写一条"为什么可以不一致"
        err2 = _require_reason(allow_mismatch if isinstance(allow_mismatch, str) else None,
                               op="allow_mismatch", what=name)
        if err2:
            err2["error"] = ("allow_mismatch=True 时必须【另外如实写明】"
                             "为什么允许这个不一致（不能只给 True）：%s" % err2.get("error"))
            err2["mismatches"] = mm
            err2["verification"] = v
            return err2, None
    return None, v


# ══════════════════════════════════════════════════════════════════════════
#  写入接口
# ══════════════════════════════════════════════════════════════════════════

def origin_record_path(part_path):
    """【Bug⑪】零件 origin 约定文件路径：<零件>.SLDPRT → <零件>.SLDPRT.origin.json。"""
    if not part_path:
        return None
    return str(part_path) + ORIGIN_RECORD_SUFFIX


def read_origin_record(part_path):
    """【Bug⑪】读取零件旁的 <零件>.origin.json；不存在/损坏返回 None（只读、不抛异常）。

    容忍两种入参：零件路径（<part>.SLDPRT）或 origin 文件路径本身。
    """
    if not part_path:
        return None
    _s = str(part_path)
    _cands = ([_s] if _s.lower().endswith(ORIGIN_RECORD_SUFFIX)
              else [_s + ORIGIN_RECORD_SUFFIX])
    for p in _cands:
        try:
            if not os.path.isfile(p):
                continue
            with open(p, "r", encoding="utf-8-sig") as f:
                d = json.load(f)
            if isinstance(d, dict):
                return d
        except Exception:
            continue
    return None


def material_consistency(part_path):
    """【Bug①】从材料凭据里读出"请求材质 vs SW 实际生效材质"的一致性结论。

    现场缺陷（竞速小车任务）：
      用户确认 PETG（ρ1270 / σy≈40），但 SolidWorks 材质库【没有 PETG】
      （只有 PET，ρ1420 / σy≈55）。set_custom_material("PETG", ...) 只写了
      material.json 凭据签名，**没有**真正写进 SW 零件内部材质：
        · 凭据层签名有效 → 防线①在签名层通过；
        · .sldprt 属性栏显示的却是 PET(1420) → 用户拿零件直接打印/加工会被误导。
      报告里只有一句"实测 1420 不一致"的 WARNING，看不出"要的是 PETG、实得 PET"。

    本函数把凭据里【已经签名】的三组事实并列出来，让台账/交付物上能直接看到：
      requested_material（设计要的）
      applied_name      （SW 实际写入的牌号）
      applied_density / expected_density（客观密度证据）

    ⚠️ 本模块【只读凭据】，不验签、不改凭据、不阻断任何流程 ——
       验签与门禁判定属于 defense_gate 的职责。

    Returns:
        {"available": bool, "credential_file": str,
         "requested_material", "applied_material",
         "requested_density_kg_m3", "applied_density_kg_m3",
         "density_deviation_pct", "name_matches": bool|None,
         "consistent": bool|None, "warning": str|None}
    """
    out = {"available": False, "credential_file": None,
           "requested_material": None, "applied_material": None,
           "requested_density_kg_m3": None, "applied_density_kg_m3": None,
           "density_deviation_pct": None,
           "name_matches": None, "consistent": None, "warning": None}
    if not part_path:
        return out
    _p = str(part_path) + ".material.json"
    out["credential_file"] = _p
    try:
        if not os.path.isfile(_p):
            return out
        with open(_p, "r", encoding="utf-8-sig") as f:
            att = json.load(f)
        if not isinstance(att, dict):
            return out
    except Exception:
        return out
    out["available"] = True
    # ── 【Bug①】SW 内部实际材质：先看旁挂记录，再看凭据里可能带的字段 ────
    # 旁挂记录由 swapi 写入（宿主签名白名单不含这些键，签后补字段会让
    #   HMAC 失配，所以它们只能落在旁挂文件里）。
    _side = None
    try:
        for _c in (str(part_path) + ".material.json.sw_internal.json",
                   str(part_path) + ".sw_internal.json"):
            if os.path.isfile(_c):
                with open(_c, "r", encoding="utf-8-sig") as _f:
                    _side = json.load(_f)
                out["sw_internal_record_file"] = _c
                break
    except Exception:
        _side = None
    if isinstance(_side, dict):
        out["sw_internal_material"] = _side.get("sw_internal_material")
        out["sw_internal_matches_request"] = _side.get("sw_internal_matches_request")
        out["sw_internal_density_kg_m3"] = _side.get("density_kg_m3")
        if _side.get("sw_internal_warning"):
            out["sw_internal_warning"] = _side.get("sw_internal_warning")
    else:
        out["sw_internal_material"] = att.get("sw_internal_material")
        out["sw_internal_matches_request"] = att.get("sw_internal_matches_request")
    _req = str(att.get("requested_material") or "").strip() or None
    # applied_name 是 SW 真实写入的牌号；attested_material 是"校验通过的名字"
    _app = str(att.get("applied_name") or att.get("attested_material") or "").strip() or None
    out["requested_material"] = _req
    out["applied_material"] = _app
    try:
        _ed = att.get("expected_density_kg_m3")
        _ad = att.get("applied_density_kg_m3")
        if _ad is None:
            _ad = att.get("density_kg_m3")
        out["requested_density_kg_m3"] = float(_ed) if _ed is not None else None
        out["applied_density_kg_m3"] = float(_ad) if _ad is not None else None
    except Exception:
        pass
    _name_match = (bool(_req) and bool(_app)
                   and _req.casefold() == _app.casefold())
    out["name_matches"] = _name_match if (_req and _app) else None
    # ── 【Bug①】SW 内部实际材质 vs 请求材质（来自旁挂记录，最直接的证据）──
    _sw_int = str(out.get("sw_internal_material") or "").strip() or None
    out["sw_internal_material"] = _sw_int
    if out.get("sw_internal_matches_request") is None and _req and _sw_int:
        out["sw_internal_matches_request"] = (
            _req.casefold() == _sw_int.casefold())
    # 密度偏差：客观证据（名字可能被"标准化"成同一个，密度不会）
    _dev = None
    try:
        if out["requested_density_kg_m3"] and out["applied_density_kg_m3"]:
            _dev = abs(out["applied_density_kg_m3"]
                       - out["requested_density_kg_m3"]) \
                / float(out["requested_density_kg_m3"]) * 100.0
            out["density_deviation_pct"] = round(_dev, 2)
    except Exception:
        _dev = None
    # 一致性判定（三条独立证据，任一不成立即判"不一致"）：
    #   ① 名字不同（requested_material vs applied_name）；
    #   ② 密度偏差 >5%（客观物理证据，名字可能被"标准化"成同一个）；
    #   ③ 【Bug① 核心】SW 内部实际材质与请求材质不同（来自旁挂记录）。
    _consistent = True
    if out["name_matches"] is False:
        _consistent = False
    if _dev is not None and _dev > 5.0:
        _consistent = False
    if out.get("sw_internal_matches_request") is False:
        _consistent = False
    out["consistent"] = _consistent
    if not _consistent:
        # 优先用旁挂记录里的 warning（它写清了"凭据认 A、SW 实际是 B"）
        out["warning"] = out.get("sw_internal_warning") or (
            "【Bug①】材料凭据与 SW 实际生效材质不一致：请求 %r"
            "（ρ≈%s），SW 实际 %r（ρ≈%s），密度偏差 %s%%。"
            "凭据按请求材质记录，但 .sldprt 打开后属性栏显示的是实际材质 ——"
            "直接拿零件打印/加工请以 SW 实际材质为准，"
            "或在 SW 材质库中指定精确存在的牌号。"
            % (_req, out["requested_density_kg_m3"],
               _app, out["applied_density_kg_m3"],
               out["density_deviation_pct"]))
    return out


def origin_summary(part_path):
    """【Bug⑪】把 origin 记录压成台账可用的精简结论。

    Returns:
        {"origin_mode", "origin_mode_cn", "is_standard", "assembly_note",
         "origin_mm", "bbox_mm", "record_file", "missing": bool}
        · 没有记录时仍返回对象，但 missing=True（由调用方决定是 warning 还是拦截）。
    """
    _rec = read_origin_record(part_path)
    _p = origin_record_path(part_path)
    if not _rec:
        return {"origin_mode": None, "origin_mode_cn": None,
                "is_standard": None, "assembly_note": None,
                "origin_mm": None, "bbox_mm": None,
                "record_file": _p, "missing": True}
    _mode = _rec.get("origin_mode")
    # ⚠️ 只取【稳定】字段：at/ts/revision/created_at 这类记账字段每次 save 都变，
    #   若带进台账 payload，会让"重复登记同一份数据"被误判成"改过"——
    #   与模块里 _BOOKKEEPING_FIELDS 的设计意图（空改动不落盘）直接冲突。
    return {
        "origin_mode": _mode,
        "origin_mode_cn": _rec.get("origin_mode_cn"),
        "is_standard": (bool(_mode == ORIGIN_STANDARD_MODE)
                        if _mode is not None else None),
        "assembly_note": _rec.get("assembly_note"),
        "origin_mm": _rec.get("origin_mm"),
        "bbox_mm": _rec.get("bbox_mm"),
        "record_file": _p,
        "missing": False,
        "standard_mode": ORIGIN_STANDARD_MODE,
    }


def add_part(record, work_dir=None, reason=None, allow_mismatch=False,
             fields=None, skip_verification=False):
    """新增或更新一个零件（已存在则视为更新，rev 自增 + 追加修订记录）。

    Args:
        record: dict，必填 name / room / file；
                可选 dims（dict 或 '120x40x40'）、bbox_mm、material、
                credentials（dict）、interfaces（list/dict）、adjacent
                （list 或 'A,B'）、reported_by、reported_at。
        work_dir: 台账落点覆盖（默认脚本目录/load_cases）。
        reason:   ★【强制·如实填写】本次改动的说明。新增零件时也必须填
                  （说明"这是什么零件、依据什么登记"）。缺了或写占位词 → 拒绝落盘。
        allow_mismatch: 核实发现不一致时，默认拒绝落盘。确属设计允许（过渡配合/
                  预留余量等）时，传【说明理由的字符串】（不是 True）放行，
                  该不一致会记入台账 unresolved 清单。
        fields:   可选。声明本次改了哪些字段（'dims,material' 或列表）。
                  ★一旦给出，必须与实际 diff 完全一致 —— 谎报/漏报都拒绝落盘。
        skip_verification: 仅供测试/离线批处理使用；为 True 时跳过总理子代理核实
                  （revisions 里会留下 evaluated=false 的记录，事后可审计）。

    Returns:
        {"ok": True, "action": "add"|"update", "part": ..., "rev": n,
         "file": <台账路径>, "warnings": [...], "part_record": {...},
         "verification": {...}}   ← 总理子代理核实的真实结论与指纹
        校验失败 → {"ok": False, "error": "...", ...}（绝不落盘）
    """
    if not isinstance(record, dict):
        return {"ok": False, "error": "record 必须是 dict，收到 %s" % type(record).__name__}

    # ── ① 必填字段校验（硬性）──────────────────────────────────────────
    missing = [f for f in REQUIRED_FIELDS if not _norm_name(record.get(f))]
    if missing:
        return {"ok": False,
                "error": "缺少必填字段：%s" % ", ".join(missing),
                "required": list(REQUIRED_FIELDS),
                "received": sorted([k for k in record.keys()]),
                "hint": ("add_part 至少需要 name（零件名）、room（所属房间）、"
                         "file（模型文件路径）")}

    name = _norm_name(record.get("name"))
    room = _norm_name(record.get("room"))
    file_path = str(record.get("file") or "").strip()

    # ── ①b 【强制】如实填写变更说明（在动任何数据之前就拦）
    if not skip_verification:
        err = _require_reason(reason, op="add", what=name)
        if err:
            err["error"] = ("登记/更新零件 %r 必须如实填写变更说明（reason）：%s"
                            % (name, err.get("error")))
            return err
    elif reason is not None:
        err = _require_reason(reason, op="add", what=name)
        if err:
            return err

    # ── ② 可选字段校验 ────────────────────────────────────────────────
    warnings = []

    dims, err = _parse_dims(record.get("dims"))
    if err:
        return {"ok": False, "error": err, "field": "dims", "part": name}

    bbox, err = _parse_bbox(record.get("bbox_mm"))
    if err:
        return {"ok": False, "error": err, "field": "bbox_mm", "part": name}

    # ══ 【2026-11-11 修复·P3 台账上报值张冠李戴】══════════════════════════
    # 现场（验收复跑 20261111_131815_580E38FA）：输出轴被上报成平键的尺寸 ——
    #   `report_DSH_输出轴.json` 的 dims/bbox 与 `report_DSH_输出轴平键.json`
    #   **逐字节相同**（volume 6300、bbox [-25,25,0,9,-7,7]）；
    #   而输出轴磁盘上的真实包围盒是 [-109,64,-25,25,-25,25]
    #   （见 parts\DSH_输出轴.SLDPRT.origin.json / .pr.json，
    #    由 swapi 在 save() 时实测写入）。3 个平键的 dims 也完全相同、未区分。
    # 总理子代理当场发现了它并拦下，但那是"靠人眼"，代码层没有判据。
    #
    # 这条缺陷为什么必须由代码拦：
    #   · dims/bbox 是装配换算与相邻尺寸一致性的输入，错了会连带把
    #     正确零件的核对结论一起带偏（假阴性：真冲突被掩盖）；
    #   · 它是"复制上一件、忘改"的典型产物，靠自觉无法根治。
    #
    # 判据（只用磁盘上已有的事实，不依赖任何自报）：
    #   若零件旁挂的 origin.json / pr.json 里记着【实测 bbox_mm】，
    #   而本次上报的 bbox_mm 与它相差超过容差 → 记为 warning 并 escalate。
    #   ⚠️ 只 WARN 不阻断：确有合法情形（先登记后建模、登记的是待加工毛坯），
    #      那种场景需要人判断；但"两份数据对不上"这件事必须被看见。
    _disk_bbox_note = None
    try:
        _disk_bbox = None
        _disk_src = None
        for _suf, _nm in ((ORIGIN_RECORD_SUFFIX, "origin.json"),
                          (".pr.json", "pr.json")):
            _p = str(file_path) + _suf
            if not os.path.isfile(_p):
                continue
            try:
                with open(_p, "r", encoding="utf-8-sig") as _f:
                    _j = json.load(_f) or {}
            except Exception:
                continue
            _b = _j.get("bbox_mm")
            if isinstance(_b, (list, tuple)) and len(_b) == 6:
                _disk_bbox, _disk_src = list(_b), _nm
                break
        if _disk_bbox is not None:
            if bbox:
                _d = max(abs(float(bbox[i]) - float(_disk_bbox[i]))
                         for i in range(6))
                if _d > 0.5:
                    _disk_bbox_note = {
                        "kind": "reported_bbox_vs_disk_mismatch",
                        "part": name,
                        "severity": "WARN",
                        "escalate_to_parent": True,
                        "reported_bbox_mm": list(bbox),
                        "disk_bbox_mm": _disk_bbox,
                        "disk_source": _disk_src,
                        "max_delta_mm": round(_d, 4),
                        "detail": ("%s 上报的 bbox_mm 与磁盘实测（%s）相差 %smm ——"
                                   "上报值很可能抄错了零件（张冠李戴）。"
                                   "以磁盘实测为准重新登记。"
                                   % (name, _disk_src, _fmt_num(round(_d, 4)))),
                    }
            else:
                # 没上报 bbox 时，顺手把磁盘实测结果算进警告，便于发现"漏报"
                _disk_bbox_note = {
                    "kind": "bbox_available_on_disk",
                    "part": name,
                    "severity": "INFO",
                    "disk_bbox_mm": _disk_bbox,
                    "disk_source": _disk_src,
                    "detail": ("%s 未上报 bbox_mm，但磁盘 %s 里有实测包围盒 %s；"
                               "建议按实测值登记，装配换算才不会偏。"
                               % (name, _disk_src, _disk_bbox)),
                }
    except Exception as _e_bb:
        _disk_bbox_note = None
    if _disk_bbox_note:
        warnings.append(_disk_bbox_note)

    if bbox and not dims:
        dims = _dims_from_bbox(bbox)
        warnings.append("dims 缺失，已由 bbox_mm 推算外形尺寸 %s"
                        % _fmt_dims(dims))
    elif bbox and dims:
        derived = _dims_from_bbox(bbox)
        if derived and _dims_delta(dims, derived) > 0.5:
            warnings.append("dims 与 bbox_mm 推算尺寸不一致（差 %smm），"
                            "请确认哪一份是真实尺寸" % _fmt_num(_dims_delta(dims, derived)))

    credentials = record.get("credentials")
    if credentials is not None:
        if not isinstance(credentials, dict):
            return {"ok": False, "error": "credentials 必须是 dict（material/physics/domain → 文件路径）",
                    "field": "credentials", "part": name}
        credentials = {str(k): str(v) for k, v in credentials.items()}
        for k, v in credentials.items():
            if v and not os.path.isfile(v):
                warnings.append("凭据文件不存在：credentials.%s = %s" % (k, v))

    interfaces, dropped = _norm_interfaces(record.get("interfaces"))
    for d in dropped:
        warnings.append("接口项被丢弃（%s）：%s" % (d.get("reason"), d.get("value")))
    for it in interfaces:
        if it["nominal"] is None:
            warnings.append("接口 %s 缺少 nominal_mm，"
                            "相邻尺寸一致性无法核对这一项" % it["id"])
        if it["nominal"] is not None and it["nominal"] <= 0:
            warnings.append("接口 %s 的 nominal_mm=%s 非正数，疑为录入错误"
                            % (it["id"], _fmt_num(it["nominal"])))
        tk = _tolerance_kind(it["tolerance"])
        if it["type"] == "hole" and tk == "shaft":
            warnings.append("接口 %s 类型为孔，但公差带 %r 像轴用公差（GB/T 1800 中孔用大写字母）"
                            % (it["id"], it["tolerance"]))
        if it["type"] == "shaft" and tk == "hole":
            warnings.append("接口 %s 类型为轴，但公差带 %r 像孔用公差（GB/T 1800 中轴用小写字母）"
                            % (it["id"], it["tolerance"]))

    adjacent = _parse_list_arg(record.get("adjacent"))
    if name in adjacent:
        return {"ok": False, "error": "adjacent 不能包含零件自身（%s）" % name,
                "field": "adjacent", "part": name}

    if dims and any(float(v) <= 0 for v in dims.values()):
        warnings.append("尺寸含 0 值：%s —— 通常是漏填或建模失败，请复核" % _fmt_dims(dims))

    if not os.path.isfile(file_path):
        warnings.append("模型文件当前不存在于磁盘：%s（允许先登记后建模）" % file_path)

    # ══ 【2026-11-11 新增·P3 调试件混进交付台账】══════════════════════════
    # 现场（验收复跑 20261111_131815_580E38FA，台账实锤）：
    #   本任务台账 15 条里 6 条是【调试/测试件】——
    #     测试块、DIAG_下箱座_debug、DIAG_min_block、DIAG_rightplane、
    #     DIAG_manualcut、DIAG_manualcut2
    #   而 parts/ 目录里这些文件【早已不存在】（收尾时被 tidy/删除）。
    #   后果不是"多几条脏数据"这么轻：
    #     · check_reusable 会读到"有记录、但文件不在"的条目，判定语义
    #       在 new/reuse/unknown 之间摇摆；
    #     · 相邻尺寸一致性核对会把不存在的零件拉进 pairs，产出假冲突；
    #     · 交付台账（导出 docx）里出现 DIAG_* 这种明显不是零件的东西。
    #   为什么不在 add_part 里直接【拒绝】：小屋调试阶段确实需要登记能查重，
    #     一刀切拒绝会逼它们绕过模块手写 JSON（那更糟）。
    #   所以这里做两件事：
    #     ① 打 debug_part=True 标记 + 结构化 warning，让下游能过滤；
    #     ② 文件已不存在时升级为 WARN 并注明"这是残留登记"。
    #   真正的"交付前剔除"由 export/docx 与 check-consistency 侧按标记过滤。
    _dbg_hit = _looks_like_debug_part(name)
    if _dbg_hit:
        warnings.append({
            "kind": "debug_part_in_registry",
            "part": name,
            "severity": "WARN" if not os.path.isfile(file_path) else "INFO",
            "matched": _dbg_hit,
            "file_exists": bool(os.path.isfile(file_path)),
            "detail": ("%s 看起来是【调试/测试件】（命中 %r）。它不应该出现在交付台账里："
                       "会被查重闸与相邻尺寸核对当成真零件，也会混进导出的 docx。"
                       "调试件请建模到 scripts/ 或临时目录，不要 save 到 parts/；"
                       "已经登记的可由交付前清理剔除。%s"
                       % (name, _dbg_hit,
                          "" if os.path.isfile(file_path)
                          else "（当前磁盘上已无此文件 —— 属残留登记）")),
        })

    # ── ②b 【Bug⑪】并入零件 origin 约定（来自 <零件>.origin.json）────────
    # 为什么放在台账里：装配侧（总装/工程图/一致性核对）读的是【台账】。
    #   origin 只躺在零件目录边上的 .origin.json 里没人读，等于没记录。
    # 缺记录时不硬编造：origin_missing=True + warning，让调用方知道要补。
    # ── 【Bug①】材料凭据一致性：把"要的材质 vs SW 实际生效的"并入台账 ──
    # 凭据本身由 defense_gate 验签；这里只做【可见性】——让"零件属性栏显示 PET、
    #   凭据却写 PETG"这种落差不只躺在 reports/ 里，而能随台账一起交付。
    _mat_con = material_consistency(file_path)
    if _mat_con.get("available") and _mat_con.get("consistent") is False:
        warnings.append(_mat_con.get("warning")
                        or "材料凭据与 SW 实际生效材质不一致")

    origin = origin_summary(file_path)
    origin_missing = bool(origin.get("missing"))
    if origin_missing:
        warnings.append(
            "该零件缺少 origin 约定记录（%s）—— 装配时无法自动换算基准面。"
            "请用 swapi.SWModel.set_origin_mode('%s') 声明后重新 save()，"
            "或用 origin 参数显式登记。" % (os.path.basename(str(file_path))
                                            + ORIGIN_RECORD_SUFFIX,
                                            ORIGIN_STANDARD_MODE))
    elif origin.get("is_standard") is False:
        warnings.append(
            "该零件 origin 非标准约定（%s）：装配请按台账 origin 字段里的"
            " assembly_note 单独换算；标准约定为「%s」。"
            % (origin.get("origin_mode"), ORIGIN_STANDARD_CN))
    # 调用方也可显式覆盖（例如零件尚未落盘、先登记再建模的场景）
    _rec_origin = record.get("origin")
    if isinstance(_rec_origin, dict):
        for _k, _v in _rec_origin.items():
            if _v is not None:
                origin[_k] = _v
        origin["missing"] = False
        origin["source"] = "explicit(add_part origin=)"
    elif isinstance(_rec_origin, str) and _rec_origin.strip():
        origin["origin_mode"] = _rec_origin.strip()
        origin["mode_cn"] = None
        origin["is_standard"] = (origin["origin_mode"] == ORIGIN_STANDARD_MODE)
        origin["missing"] = False
        origin["source"] = "explicit(add_part origin=)"
    origin.pop("missing", None)
    origin["standard_mode"] = ORIGIN_STANDARD_MODE

    # ── ③ 读台账 ──────────────────────────────────────────────────────
    data, path, err = _load_registry(work_dir)
    if err:
        # 【C8 修复】任务号不一致 → 带结构化判据返回（不是"台账损坏"）
        return _write_fail(err, path,
                           hint="台账损坏时拒绝写入，避免覆盖已有数据；"
                                "请先人工修复或改名备份")

    # ── 【C8 修复】补/校验任务号：不一致 → 明确拒绝（不静默继承旧任务号）──
    _tid_err = _set_task_id(data, work_dir)
    if _tid_err:
        return _tid_err
    parts = _parts_map(data)
    key = _resolve_key(parts, name)
    _ts, at = _now()

    payload = {
        "name": name,
        "room": room,
        "file": file_path,
    }
    if dims:
        payload["dims"] = dims
    if bbox:
        payload["bbox_mm"] = bbox
    if record.get("material") is not None:
        payload["material"] = str(record.get("material") or "").strip()
    # ── 【Bug⑪】origin 约定随零件记录落盘（装配/一致性核对的输入）────────
    if origin:
        payload["origin"] = origin
    # ── 【Bug①】材料一致性结论随记录落盘（缺失凭据时不写，避免噪音）────────
    if _mat_con.get("available"):
        payload["material_consistency"] = {
            "requested_material": _mat_con.get("requested_material"),
            "applied_material": _mat_con.get("applied_material"),
            "requested_density_kg_m3": _mat_con.get("requested_density_kg_m3"),
            "applied_density_kg_m3": _mat_con.get("applied_density_kg_m3"),
            "density_deviation_pct": _mat_con.get("density_deviation_pct"),
            "name_matches": _mat_con.get("name_matches"),
            "consistent": _mat_con.get("consistent"),
            "credential_file": _mat_con.get("credential_file"),
        }
    if credentials is not None:
        payload["credentials"] = credentials
    # interfaces 只在【显式给出】时覆盖 —— 否则 add_part 的常规更新
    # （例如只改材料）会把已登记的接口数据抹成空，导致一致性检查突然"没数据"。
    if record.get("interfaces") is not None:
        payload["interfaces"] = [dict(it["raw"]) for it in interfaces]
    payload["adjacent"] = adjacent
    payload["reported_by"] = _norm_name(record.get("reported_by") or room)
    # ── reported_at 是【记账字段】，不参与"改了什么"的判定 ────────────────
    # 历史缺陷：原来无条件写 payload["reported_at"] = now()，秒级时间戳每次调用
    # 都不同 —— 同一份数据重复登记会被当成"改动"，于是 rev 无意义增长、空改动
    # 检测（no-op）随机失效（实测：同一秒内调用看似正常，跨秒就变成 update，
    # 属典型的时序不确定性缺陷）。
    # 现在的口径：显式给了就用给的；没给就刷新为当前时间，但【不计入 changed】——
    # 即"有真实改动时顺带刷新时间戳，只有时间戳变化时不产生修订"。
    payload["reported_at"] = (str(record.get("reported_at"))
                              if record.get("reported_at") is not None else at)

    # ── ④ 新增 or 更新（先在内存里改，闸门过了才落盘）─────────────────
    # 【关键】这里先做一次深拷贝作为回滚点：核实不通过时把内存恢复原样，
    # 保证"拒绝落盘"是真的什么都没写（磁盘+内存都不留痕）。
    import copy as _copy
    backup = _copy.deepcopy(data)

    if key is None:
        # 新记录补齐默认键，保证台账结构统一（缺省 = 空，而不是键不存在）
        payload.setdefault("credentials", {})
        payload.setdefault("interfaces", [])
        payload.setdefault("dims", {})
        payload.setdefault("material", "")
        payload["rev"] = 1
        parts[name] = payload
        action = "add"
        changed = {}
    else:
        old = parts[key]
        try:
            new_rev = int(old.get("rev") or 1) + 1
        except Exception:
            new_rev = 2
        changed = {}
        merged = dict(old)
        for k, v in payload.items():
            if k == "adjacent":
                # adjacent 是并集语义：add 只追加，不抹掉已有邻接（去邻接请用 remove/link 管理）
                before = _parse_list_arg(old.get("adjacent"))
                union = list(before)
                for x in v:
                    if x not in union:
                        union.append(x)
                if union != before:
                    changed["adjacent"] = {"from": before, "to": union}
                merged["adjacent"] = union
                continue
            if k == "credentials":
                before = old.get("credentials") or {}
                union = dict(before)
                union.update(v or {})
                if union != before:
                    changed["credentials"] = {"from": sorted(before.keys()),
                                              "to": sorted(union.keys())}
                merged["credentials"] = union
                continue
            if k == "interfaces" and not v and old.get("interfaces"):
                # 保护：显式传了空 interfaces 会抹掉已有接口数据，导致一致性检查
                # 突然"没数据可查"。这种情况要求调用方明确用 update_part 清空。
                merged["interfaces"] = old.get("interfaces")
                continue
            before = old.get(k)
            if k in _BOOKKEEPING_FIELDS:
                # 记账字段只跟着刷新，不算作"改动"（否则每次调用都像改过）
                merged[k] = v
                continue
            if before != v:
                changed[k] = {"from": before, "to": v}
            merged[k] = v

        # ── 空改动：不写无意义的修订（否则重复登记会让 rev 无限增长）────
        # 这里【不落盘】：既然没有任何实质变化，磁盘上什么都不该动。
        if not changed:
            return {
                "ok": True, "action": "no-op", "part": key,
                "rev": old.get("rev"), "changed": [], "file": path,
                "reason": str(reason or "").strip(),
                "warnings": warnings + ["本次登记的数据与台账现有值完全一致，未产生改动，"
                                        "因此没有写入修订记录"],
                "part_record": old, "parts_count": len(parts),
                "verification": {"verified": None, "evaluated": False,
                                 "summary": "无改动，无需核实"},
                "note": "空改动不落盘：台账只记录真实发生的变化，避免 rev 无意义增长。",
            }

        merged["rev"] = new_rev
        parts[key] = merged
        action = "update"

    target = _resolve_key(parts, name)

    # ── ④b 【强制】fields 声明必须与实际 diff 完全一致（防止谎报改动范围）──
    declared = _parse_fields_arg(fields)
    if declared is not None and action == "update":
        actual = sorted(changed.keys())
        if declared != actual:
            return {
                "ok": False,
                "gate": "DECLARED_FIELDS_MISMATCH",
                "rejected": True,
                "part": target,
                "error": ("声明的改动字段与实际情况不一致（声明 %s，实际 %s）—— "
                          "台账要求如实填写改动范围" % (declared or "无", actual or "无")),
                "declared_fields": declared,
                "actual_fields": actual,
                "hint": "要么去掉 fields（由模块自行判定），要么如实列出真实改动的字段。",
            }

    # ── ④c 【强制】总理子代理核实（真实计算，不看调用方声明）─────────────
    gate_err, verification = _write_gate(
        data, target, reason, work_dir, action,
        allow_mismatch=allow_mismatch, skip_verify=skip_verification)
    if gate_err:
        data.clear()
        data.update(backup)      # 回滚：拒绝落盘必须真的什么都没改
        return gate_err

    # 核实所需的字段名（changes 为空时用动作本身说明）
    _reason_text = str(reason or "").strip()
    if action == "add":
        _append_revision(data, "add", target,
                         changes={"fields": sorted(payload.keys())},
                         reason=_reason_text, verification=verification,
                         fields=declared)
    else:
        _append_revision(data, "update", target, changes=changed,
                         reason=_reason_text, verification=verification,
                         fields=declared)

    # 带病落盘时登记 unresolved（不静默）
    if verification and (verification.get("mismatches") or []):
        _record_unresolved(data, target, verification, _reason_text, action=action)

    ok, path, werr = _save_registry(data, work_dir)
    if not ok:
        return {"ok": False, "error": werr, "file": path}
    # 【C8 修复】统一走 parts 变量 —— 拦截时拿到的是空骨架，
    #   直接索引 data["parts"] 会把"明确的任务号错误"变成 KeyError。
    parts = _parts_map(data)

    # ── ⑤ 邻接登记后的数据体检（缺零件 → 明确的 warning，不闷声）────────
    for other in sorted(set(adjacent)):
        if _resolve_key(parts, other) is None:
            warnings.append("adjacent 中的 %r 尚未登记到台账（悬空邻接），"
                            "请补登记或改用 link_adjacent" % other)

    rec = parts[_resolve_key(parts, name)]
    out = {
        "ok": True,
        "action": action,
        "part": rec["name"],
        "rev": rec.get("rev"),
        "room": rec.get("room"),
        "file": path,
        "reason": _reason_text,
        "warnings": warnings,
        "revision_count": len(data["revisions"]),
        "parts_count": len(parts),
        "part_record": rec,
        "note": "已落盘（含修订历史 + 核实指纹）；邻接关系建议用 link_adjacent 建立，保证双向。",
    }
    if verification:
        out["verification"] = {
            "verified": verification.get("verified"),
            "evaluated": verification.get("evaluated"),
            "scope": verification.get("scope"),
            "neighbors_checked": verification.get("neighbors_checked"),
            "digest": verification.get("digest"),
            "mismatch_count": len(verification.get("mismatches") or []),
            "mismatches": verification.get("mismatches") or [],
            "reason": verification.get("reason"),
            "summary": verification.get("summary"),
        }
        out["unresolved_recorded"] = len(verification.get("mismatches") or [])
    else:
        out["verification"] = {"verified": None, "evaluated": False,
                               "summary": "已跳过核实（skip_verification=True）—— "
                                          "该记录可被 audit_verification 查出"}
    return out


def update_part(name, patch, work_dir=None, reason=None, allow_mismatch=False,
                fields=None, skip_verification=False):
    """局部更新一个已登记零件（只改 patch 里出现的字段，rev 自增）。

    Args:
        name:  已登记零件名
        patch: dict，键必须在 PATCHABLE_FIELDS 内（不允许改名；
               改 adjacent 请用 link_adjacent / remove_part 以保证双向一致）
        reason: ★【强制·如实填写】本次改了什么、为什么改。缺了/占位词 → 拒绝落盘。
        allow_mismatch: 核实发现不一致时默认拒绝；确属设计允许时传【说明理由的字符串】。
        fields: 可选。声明本次改了哪些字段；一旦给出必须与实际 diff 完全一致。
        skip_verification: 仅测试用，跳过核实（会在台账里留痕，可被审计查出）。
    Returns:
        与 add_part 同构的 {"ok":..., "action": "update", ...}
    """
    name = _norm_name(name)
    if not name:
        return {"ok": False, "error": "缺少零件名 name"}
    if not isinstance(patch, dict) or not patch:
        return {"ok": False, "error": "patch 必须是非空 dict"}

    # 【强制】如实填写变更说明（在动任何数据之前就拦）
    if not skip_verification:
        err = _require_reason(reason, op="update", what=name)
        if err:
            err["error"] = ("更新零件 %r 必须如实填写变更说明（reason）：%s"
                            % (name, err.get("error")))
            return err
    elif reason is not None:
        err = _require_reason(reason, op="update", what=name)
        if err:
            return err

    data, path, err = _load_registry(work_dir)
    if err:
        return _write_fail(err, path)

    parts = data["parts"]
    key = _resolve_key(parts, name)
    if key is None:
        return {"ok": False, "error": "零件未登记：%s" % name, "file": path,
                "hint": "先用 add_part 登记，或用 list_parts 查看已有零件"}

    if "name" in patch and _norm_name(patch.get("name")) != key:
        return {"ok": False,
                "error": "update_part 不支持改名（%s → %s）" % (key, _norm_name(patch.get("name"))),
                "hint": "改名会撕裂邻接图；请 remove_part 旧名后 add_part 新名，并重建 link"}

    # ── 【Bug⑪】origin 补丁归一化 ─────────────────────────────────────
    # 允许两种写法：字符串（只给 origin_mode）或 dict（完整结论）。
    # 统一转成与 add_part 落盘同构的 dict，避免台账里出现两种 schema。
    if "origin" in patch:
        _po = patch.get("origin")
        if isinstance(_po, str):
            patch = dict(patch)
            patch["origin"] = {"origin_mode": _po.strip(),
                               "is_standard": (_po.strip() == ORIGIN_STANDARD_MODE),
                               "standard_mode": ORIGIN_STANDARD_MODE,
                               "source": "explicit(update origin=)"}
        elif isinstance(_po, dict):
            _nd = dict(_po)
            _nd.setdefault("standard_mode", ORIGIN_STANDARD_MODE)
            if _nd.get("origin_mode") is not None:
                _nd.setdefault("is_standard",
                               _nd.get("origin_mode") == ORIGIN_STANDARD_MODE)
            patch = dict(patch)
            patch["origin"] = _nd
        elif _po is None:
            patch = dict(patch)
            patch.pop("origin", None)

    unknown = [k for k in patch.keys() if k not in PATCHABLE_FIELDS]
    if unknown:
        return {"ok": False,
                "error": "存在不可更新字段：%s" % ", ".join(sorted(unknown)),
                "allowed": list(PATCHABLE_FIELDS)}

    warnings = []
    newvals = {}
    if "room" in patch:
        v = _norm_name(patch.get("room"))
        if not v:
            return {"ok": False, "error": "room 不能为空"}
        newvals["room"] = v
    if "file" in patch:
        v = str(patch.get("file") or "").strip()
        if not v:
            return {"ok": False, "error": "file 不能为空"}
        newvals["file"] = v
        if not os.path.isfile(v):
            warnings.append("模型文件不存在于磁盘：%s" % v)
    if "dims" in patch:
        dims, e = _parse_dims(patch.get("dims"))
        if e:
            return {"ok": False, "error": e, "field": "dims"}
        newvals["dims"] = dims
    if "bbox_mm" in patch:
        bbox, e = _parse_bbox(patch.get("bbox_mm"))
        if e:
            return {"ok": False, "error": e, "field": "bbox_mm"}
        newvals["bbox_mm"] = bbox
    if "material" in patch:
        newvals["material"] = str(patch.get("material") or "").strip()
    if "credentials" in patch:
        cr = patch.get("credentials")
        if cr is not None and not isinstance(cr, dict):
            return {"ok": False, "error": "credentials 必须是 dict"}
        newvals["credentials"] = {str(k): str(v) for k, v in (cr or {}).items()}
    if "origin" in patch:
        # 【Bug⑪】已在上面归一化为 dict（字符串/None 都转过来了）
        _ov = patch.get("origin")
        if not isinstance(_ov, dict):
            return {"ok": False, "error": "origin 必须是 dict 或 origin_mode 字符串"}
        newvals["origin"] = _ov
    if "material_consistency" in patch:
        # 【Bug①】人工核实后更正材料一致性结论（如已换成 SW 库中精确牌号）
        _mc = patch.get("material_consistency")
        if not isinstance(_mc, dict):
            return {"ok": False, "error": "material_consistency 必须是 dict"}
        newvals["material_consistency"] = _mc
    if "interfaces" in patch:
        ifaces, dropped = _norm_interfaces(patch.get("interfaces"))
        for d in dropped:
            warnings.append("接口项被丢弃（%s）：%s" % (d.get("reason"), d.get("value")))
        newvals["interfaces"] = [dict(it["raw"]) for it in ifaces]
    if "adjacent" in patch:
        adj = _parse_list_arg(patch.get("adjacent"))
        if key in adj:
            return {"ok": False, "error": "adjacent 不能包含零件自身（%s）" % key}
        newvals["adjacent"] = adj
        warnings.append("直接覆盖 adjacent 会绕过双向校验；"
                        "推荐用 link_adjacent / remove_part 维护邻接图")
    if "reported_by" in patch:
        newvals["reported_by"] = _norm_name(patch.get("reported_by"))
    if "reported_at" in patch:
        newvals["reported_at"] = str(patch.get("reported_at") or "")

    import copy as _copy
    backup = _copy.deepcopy(data)

    old = parts[key]
    changed = {}
    merged = dict(old)
    for k, v in newvals.items():
        if k in _BOOKKEEPING_FIELDS:
            # 记账字段（如 reported_at）只刷新，不算作"改动"
            merged[k] = v
            continue
        before = old.get(k)
        if before != v:
            changed[k] = {"from": before, "to": v}
            merged[k] = v

    # ── 空改动直接返回，不写无意义的修订 ──────────────────────────────
    # 历史缺陷：patch 与现有值完全相同时仍会 rev+1 并追加一条 changes={} 的修订，
    # 台账里堆满"什么都没改"的垃圾记录，还会污染 audit 的最后写入指纹。
    if not changed:
        return {
            "ok": True, "action": "no-op", "part": key,
            "rev": old.get("rev"), "changed": [], "file": path,
            "reason": str(reason or "").strip(),
            "warnings": warnings + ["patch 与现有值完全一致，未产生任何改动，"
                                    "因此没有写入修订记录"],
            "part_record": old,
            "verification": {"verified": None, "evaluated": False,
                             "summary": "无改动，无需核实"},
            "note": "空改动不落盘：台账只记录真实发生的变化，避免无意义的 rev 增长。",
        }

    try:
        merged["rev"] = int(old.get("rev") or 1) + 1
    except Exception:
        merged["rev"] = 2
    parts[key] = merged

    # ── 【强制】声明字段必须与实际 diff 完全一致 ──────────────────────
    declared = _parse_fields_arg(fields)
    if declared is not None:
        actual = sorted(changed.keys())
        if declared != actual:
            return {
                "ok": False, "gate": "DECLARED_FIELDS_MISMATCH", "rejected": True,
                "part": key,
                "error": ("声明的改动字段与实际情况不一致（声明 %s，实际 %s）—— "
                          "台账要求如实填写改动范围" % (declared or "无", actual or "无")),
                "declared_fields": declared, "actual_fields": actual,
            }

    # ── 【强制】总理子代理核实 ────────────────────────────────────────
    gate_err, verification = _write_gate(
        data, key, reason, work_dir, "update",
        allow_mismatch=allow_mismatch, skip_verify=skip_verification)
    if gate_err:
        data.clear()
        data.update(backup)          # 回滚：拒绝落盘必须真的什么都没改
        return gate_err

    _reason_text = str(reason or "").strip()
    _append_revision(data, "update", key, changes=changed, reason=_reason_text,
                     verification=verification, fields=declared)
    if verification and (verification.get("mismatches") or []):
        _record_unresolved(data, key, verification, _reason_text, action="update")

    ok, path, werr = _save_registry(data, work_dir)
    if not ok:
        return {"ok": False, "error": werr, "file": path}
    out = {"ok": True, "action": "update", "part": key, "rev": merged["rev"],
           "changed": sorted(changed.keys()), "file": path,
           "reason": _reason_text,
           "warnings": warnings, "part_record": merged}
    if verification:
        out["verification"] = {
            "verified": verification.get("verified"),
            "evaluated": verification.get("evaluated"),
            "scope": verification.get("scope"),
            "neighbors_checked": verification.get("neighbors_checked"),
            "digest": verification.get("digest"),
            "mismatch_count": len(verification.get("mismatches") or []),
            "mismatches": verification.get("mismatches") or [],
            "reason": verification.get("reason"),
            "summary": verification.get("summary"),
        }
        out["unresolved_recorded"] = len(verification.get("mismatches") or [])
    else:
        out["verification"] = {"verified": None, "evaluated": False,
                               "summary": "已跳过核实（skip_verification=True）"}
    return out


def link_adjacent(name_a, name_b, work_dir=None, reason=None,
                  allow_mismatch=False, skip_verification=False):
    """★ 双向建立邻接关系（A→B 与 B→A 都写）。

    这是尺寸一致性检查的基础：只有双向登记后，
    related_parts / check_adjacency_consistency 才能把两个零件当成一对。

    强制校验：
      · A、B 都必须已登记（否则拒绝，避免造出悬空邻接）；
      · A、B 不能同名（拒绝自环）；
      · ★ 必须如实填写 reason（建立了哪两件的配合关系、依据什么）；
      · ★ 总理子代理核实：建立邻接后立刻核对这一对（及双方其它邻接）的
        接口尺寸，发现不一致 → 拒绝落盘（除非 allow_mismatch 并写明理由）。
    """
    a = _norm_name(name_a)
    b = _norm_name(name_b)
    if not a or not b:
        return {"ok": False, "error": "缺少零件名（a=%r, b=%r）" % (name_a, name_b)}
    if _norm_key(a) == _norm_key(b):
        return {"ok": False, "error": "不能把零件与自己建立邻接（%s）" % a}

    if not skip_verification:
        err = _require_reason(reason, op="link", what="%s ↔ %s" % (a, b))
        if err:
            err["error"] = ("建立邻接关系 %s ↔ %s 必须如实填写变更说明（reason）：%s"
                            % (a, b, err.get("error")))
            return err
    elif reason is not None:
        err = _require_reason(reason, op="link", what="%s ↔ %s" % (a, b))
        if err:
            return err

    data, path, err = _load_registry(work_dir)
    if err:
        return _write_fail(err, path)

    parts = data["parts"]
    ka = _resolve_key(parts, a)
    kb = _resolve_key(parts, b)
    missing = [x for x, k in ((a, ka), (b, kb)) if k is None]
    if missing:
        return {"ok": False,
                "error": "零件未登记：%s" % "、".join(missing),
                "hint": "先 add_part 登记两侧零件，再建立邻接关系",
                "file": path}

    if kb in _parse_list_arg(parts[ka].get("adjacent")) and \
       ka in _parse_list_arg(parts[kb].get("adjacent")):
        # 已经双向登记：幂等返回，不产生新修订（避免无意义的 rev 增长）
        return {"ok": True, "action": "link", "already_linked": True,
                "a": ka, "b": kb, "file": path,
                "adjacent": {ka: parts[ka].get("adjacent", []),
                             kb: parts[kb].get("adjacent", [])},
                "note": "两侧原本就已互相登记，未做改动（因此不需要 reason）"}

    import copy as _copy
    backup = _copy.deepcopy(data)

    changed_parts = []
    for src, dst in ((ka, kb), (kb, ka)):
        rec = parts[src]
        adj = _parse_list_arg(rec.get("adjacent"))
        if dst in adj:
            continue
        adj.append(dst)
        rec["adjacent"] = adj
        try:
            rec["rev"] = int(rec.get("rev") or 1) + 1
        except Exception:
            rec["rev"] = 2
        changed_parts.append(src)

    # ── 【强制】总理子代理核实：建立邻接后立即核对这一对 ───────────────
    _reason_text = str(reason or "").strip()
    gate_err, verification = _write_gate(
        data, ka, reason, work_dir, "link",
        allow_mismatch=allow_mismatch, skip_verify=skip_verification)
    if gate_err:
        data.clear()
        data.update(backup)
        gate_err["link"] = "%s ↔ %s" % (ka, kb)
        return gate_err

    for src, dst in ((ka, kb), (kb, ka)):
        _append_revision(data, "link", src,
                         changes={"adjacent": {"added": dst}},
                         extra={"linked_with": dst},
                         reason=_reason_text, verification=verification)
    if verification and (verification.get("mismatches") or []):
        _record_unresolved(data, ka, verification, _reason_text, action="link")

    ok, path, werr = _save_registry(data, work_dir)
    if not ok:
        return {"ok": False, "error": werr, "file": path}
    out = {
        "ok": True,
        "action": "link",
        "a": ka, "b": kb,
        "bidirectional": True,
        "updated_parts": changed_parts,
        "reason": _reason_text,
        "adjacent": {ka: parts[ka].get("adjacent", []),
                     kb: parts[kb].get("adjacent", [])},
        "file": path,
        "note": "已双向登记（A↔B）；check_adjacency_consistency 会把它当作一个待核对配合对。",
    }
    if verification:
        out["verification"] = {
            "verified": verification.get("verified"),
            "evaluated": verification.get("evaluated"),
            "scope": verification.get("scope"),
            "neighbors_checked": verification.get("neighbors_checked"),
            "digest": verification.get("digest"),
            "mismatch_count": len(verification.get("mismatches") or []),
            "mismatches": verification.get("mismatches") or [],
            "reason": verification.get("reason"),
            "summary": verification.get("summary"),
        }
    return out


def remove_part(name, work_dir=None, reason=None, skip_verification=False):
    """删除零件，并【清理所有其它零件对它的邻接引用】（避免悬空邻接）。

    ★ 必须如实填写 reason（为什么删、是废弃还是改名重建）——
      删除同样会改变台账数据，因此同样受"如实填写"约束。
    """
    name = _norm_name(name)
    if not name:
        return {"ok": False, "error": "缺少零件名 name"}
    if not skip_verification:
        err = _require_reason(reason, op="remove", what=name)
        if err:
            err["error"] = ("删除零件 %r 必须如实填写变更说明（reason）：%s"
                            % (name, err.get("error")))
            return err
    elif reason is not None:
        err = _require_reason(reason, op="remove", what=name)
        if err:
            return err

    data, path, err = _load_registry(work_dir)
    if err:
        return _write_fail(err, path)

    parts = data["parts"]
    key = _resolve_key(parts, name)
    if key is None:
        return {"ok": False, "error": "零件未登记：%s" % name, "file": path}

    import copy as _copy
    backup = _copy.deepcopy(data)
    _reason_text = str(reason or "").strip()

    # 删除前先给"被删零件"留下最后指纹，供 audit 判定是【模块合法删除】
    # 还是被人从 JSON 里偷偷抠掉（后者不会有 remove 修订记录）。
    rec = parts[key]
    _removed_digest = _canonical_digest(_substance(rec))

    parts.pop(key)
    cleaned = []
    for other, orec in parts.items():
        adj = _parse_list_arg(orec.get("adjacent"))
        if key in adj:
            orec["adjacent"] = [x for x in adj if x != key]
            try:
                orec["rev"] = int(orec.get("rev") or 1) + 1
            except Exception:
                orec["rev"] = 2
            cleaned.append(other)
    _append_revision(data, "remove", key,
                     changes={"removed": {"room": rec.get("room"), "file": rec.get("file")}},
                     extra={"cleaned_in": cleaned,
                            "removed_part_digest": _removed_digest,
                            "deleted_by_module": True},
                     reason=_reason_text,
                     stamp_parts=cleaned)

    ok, path, werr = _save_registry(data, work_dir)
    if not ok:
        data.clear()
        data.update(backup)
        return {"ok": False, "error": werr, "file": path}
    return {"ok": True, "action": "remove", "removed": key,
            "reason": _reason_text,
            "cleaned_adjacency_in": sorted(cleaned),
            "parts_count": len(parts), "file": path}


# ══════════════════════════════════════════════════════════════════════════
#  查询接口
# ══════════════════════════════════════════════════════════════════════════

def _fmt_dims(dims):
    """尺寸 dict → '120×40×40mm' 风格短串。"""
    if not isinstance(dims, dict) or not dims:
        return "-"
    keys = ("length_mm", "width_mm", "height_mm")
    if all(k in dims for k in keys):
        return "×".join(_fmt_num(dims[k]) for k in keys)
    return " ".join("%s=%s" % (k, _fmt_num(v)) for k, v in sorted(dims.items()))


def _part_brief(rec):
    """零件记录的摘要视图（列表/报告用，避免刷屏）。"""
    dims = rec.get("dims") or {}
    return {
        "name": rec.get("name"),
        "room": rec.get("room"),
        "file": rec.get("file"),
        "dims": dims,
        "dims_text": _fmt_dims(dims),
        "bbox_mm": rec.get("bbox_mm"),
        "material": rec.get("material"),
        # 【Bug⑪】origin 约定：装配换基准面的唯一依据（缺失时如实为 None）
        "origin": rec.get("origin"),
        "origin_mode": (rec.get("origin") or {}).get("origin_mode")
                       if isinstance(rec.get("origin"), dict) else None,
        "adjacent": _parse_list_arg(rec.get("adjacent")),
        "interface_count": len(_norm_interfaces(rec.get("interfaces"))[0]),
        "interfaces": [{"id": i["id"], "type": i["type"],
                        "nominal_mm": i["nominal"], "tolerance": i["tolerance"]}
                       for i in _norm_interfaces(rec.get("interfaces"))[0]],
        "reported_by": rec.get("reported_by"),
        "reported_at": rec.get("reported_at"),
        "rev": rec.get("rev"),
    }


def get_part(name, work_dir=None):
    """取单个零件的完整记录。"""
    name = _norm_name(name)
    if not name:
        return {"ok": False, "error": "缺少零件名 name"}
    # 【C8 修复】只读查询：不拦截旧台账，但在结果里回显 task_id 不一致警告
    data, path, err = _load_registry(work_dir, strict_task_id=False)
    if err:
        return {"ok": False, "error": err, "file": path}

    parts = data["parts"]
    key = _resolve_key(parts, name)
    if key is None:
        return {"ok": False, "error": "零件未登记：%s" % name,
                "file": path, "known_parts": sorted(parts.keys()),
                "hint": "用 list_parts() 查看台账里已有的零件名"}
    return {"ok": True, "part": key, "file": path,
            "task_id": data.get("task_id"),
            "task_id_mismatch": data.get("task_id_mismatch"),
            "record": parts[key],
            "brief": _part_brief(parts[key])}


# ══ 【B9 修复】建模前置查重：同一零件不允许被反复重建 ═══════════════════════
# 现场（2026-11-07 会话，测试员 P3 完全同构）：
#   · 底盘 05:47 用 scripts/rebuild_chassis_7mm.py 重建（8mm→7mm）；
#   · 电机安装验证块 14:54:46 与 14:57:16 两次写盘（中间 14:56:12 还改了一次脚本）；
#   · parts/ 里多出 DSH_万向轮转接板1.SLDPRT（83 KB，无 .material.json）；
#   · parts_rejects/ 与 parts/ 同时存在 DSH_电机安装验证块（81,386 B / 81,479 B）。
#   测试员原话：「建模入口没有『目标零件是否已存在 + 是否同规格』的前置检查，
#     save() 直接覆盖同名文件」。
#
# 本函数就是那个前置检查：建模【之前】先问台账，而不是建完再覆盖。
REUSE_VERDICT = {
    "reuse": "已存在且规格一致 → 直接复用，不要再建模",
    "rebuild": "已存在但规格不一致 → 允许重建，必须写明改了什么",
    "new": "台账无此件 → 正常建模",
    "unknown": "台账有登记但缺可比对字段 → 不得当作『一致』，按 rebuild 处理",
}


def _dims_equal(a, b, tol=0.01):
    """尺寸/包围盒比较：容忍浮点误差，缺失一律视为"不可比"。"""
    if a is None or b is None:
        return None
    if isinstance(a, dict) and isinstance(b, dict):
        keys = set(a.keys()) | set(b.keys())
        if not keys:
            return None
        for k in keys:
            try:
                va, vb = float(a.get(k)), float(b.get(k))
            except Exception:
                return None
            if abs(va - vb) > tol:
                return False
        return True
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        if len(a) != len(b):
            return False
        try:
            return all(abs(float(x) - float(y)) <= tol for x, y in zip(a, b))
        except Exception:
            return None
    return None


def check_reusable(name, dims=None, bbox_mm=None, material=None,
                   file_path=None, work_dir=None, tol=0.01):
    """【B9】建模前置查重：这个零件是不是已经建好了？

    Args:
        name:       零件名（与台账同名口径，大小写/空格容错见 _resolve_key）。
        dims:       本次打算用的尺寸 dict（length_mm/width_mm/height_mm 等）。
        bbox_mm:    本次期望的包围盒（六元组或 dict）。
        material:   本次打算用的材料代号（Q235 / PETG …）。
        file_path:  本次打算落盘的路径（可选，用于给出"目标已被占用"警告）。
        work_dir:   台账落点覆盖。
        tol:        尺寸容差（mm），默认 0.01。

    Returns:
        {"ok", "verdict": reuse|rebuild|new|unknown, "part", "record",
         "diffs": [...], "why", "file_exists", "action"(可直接照做的下一步),
         "skip_modeling": bool}
    判据（缺一不可，任一不满足就不许 reuse）：
      ① 台账里有这件（同 task_id 台账）；
      ② 台账记录的 file 【真实存在】于磁盘且非空；
      ③ 尺寸/包围盒可比对【且一致】；
      ④ 若给了 material，必须与台账一致。
      其中 ③ 若缺可比对字段 → verdict=unknown（**不得**当成一致，
      这正是 P5「不可用被折叠成通过」的同一条机理）。
    """
    name_in = _norm_name(name)
    if not name_in:
        return {"ok": False, "error": "缺少零件名 name"}
    data, path, err = _load_registry(work_dir, strict_task_id=False)
    out = {"ok": True, "part": name_in, "file": path,
           "task_id": data.get("task_id") if isinstance(data, dict) else None,
           "task_id_mismatch": (data.get("task_id_mismatch")
                                if isinstance(data, dict) else None),
           "target_file": file_path, "diffs": []}
    if err:
        return dict(out, ok=False, error=err, verdict="unknown",
                    skip_modeling=False, why="台账不可读，无法判重（按重建处理）")
    parts = data["parts"]
    key = _resolve_key(parts, name_in)
    if key is None:
        return dict(out, verdict="new", record=None, skip_modeling=False,
                    file_exists=(os.path.exists(file_path) if file_path else None),
                    why="台账未登记该零件 —— 正常建模",
                    action="正常建模，建完用 add_part 登记")

    rec = parts[key]
    out["part"] = key
    out["record"] = rec
    _f = str(rec.get("file") or "")
    _exists = bool(_f) and os.path.exists(_f) and os.path.getsize(_f) > 0
    out["file_exists"] = _exists
    out["record_file"] = _f or None
    out["record_rev"] = rec.get("rev")

    diffs = []
    # ① 文件真的在（文件不存在 → 绝不能"复用"）
    if not _exists:
        diffs.append({"field": "file", "record": _f or None,
                      "now": "（磁盘上不存在/为空）",
                      "why": "台账登记的文件不在磁盘上，复用会拿到空气"})
    # ② 尺寸 / 包围盒
    #    【B9 修复·实测坑】入参可能是 '180x140x8' 这种字符串，而台账里存的是
    #    {"length_mm":..., "width_mm":..., "height_mm":...} 的 dict。
    #    直接比 → 类型不同 → _dims_equal 返回 None（"不可比"）→ 判成 unknown，
    #    于是"尺寸明明改了却不下 rebuild 结论"，查重等于没做。
    #    这里先统一过一遍 _parse_dims（台账落盘用的同一个解析器），保证同口径。
    _dims_cmp = dims
    if isinstance(dims, str):
        _parsed, _derr = _parse_dims(dims)
        if _parsed:
            _dims_cmp = _parsed
    _bbox_cmp = bbox_mm
    if isinstance(bbox_mm, str):
        try:
            _vals = [float(x) for x in str(bbox_mm).replace("，", ",").split(",") if x.strip()]
            if len(_vals) == 6:
                _bbox_cmp = _vals
        except Exception:
            _bbox_cmp = bbox_mm
    _cmp = False
    for field, now_val in (("dims", _dims_cmp), ("bbox_mm", _bbox_cmp)):
        if now_val is None:
            continue
        eq = _dims_equal(rec.get(field), now_val, tol)
        if eq is False:
            diffs.append({"field": field, "record": rec.get(field),
                          "now": now_val, "why": "尺寸/包围盒不一致"})
            _cmp = True
        elif eq is True:
            _cmp = True
        # eq is None → 字段缺失，不可比（下面统一按 unknown 处理）
    # ③ 材料
    if material:
        _m_old = str(rec.get("material") or "").strip()
        if not _m_old:
            diffs.append({"field": "material", "record": None, "now": material,
                          "why": "台账没登记材料，无法确认同规格"})
        elif _m_old.lower() != str(material).strip().lower():
            diffs.append({"field": "material", "record": _m_old, "now": material,
                          "why": "材料不一致"})

    out["diffs"] = diffs
    hard = [d for d in diffs if d.get("field") in ("file", "dims", "bbox_mm",
                                                   "material")]
    if hard:
        out.update(verdict="rebuild", skip_modeling=False,
                   why="台账有该零件但规格/落盘不一致 —— 允许重建，但必须说明改了什么",
                   action=("允许重建。重建前先在 report 里写明差异：%s；"
                           "重建后用 add_part(reason=...) 登记新的 rev"
                           % "；".join("%s %r→%r" % (d["field"], d.get("record"),
                                                     d.get("now")) for d in hard)))
        return out
    if not _cmp:
        # 一个可比字段都没给 → 不下"一致"的结论（P5 同源：不可用≠通过）
        out.update(verdict="unknown", skip_modeling=False,
                   why=("台账有该零件，但本次没有提供尺寸/包围盒可比对 —— "
                        "不得据此判定『已建好』"),
                   action=("补上 dims 或 bbox_mm 再查一次；"
                           "或直接复用台账记录里的规格"))
        return out
    out.update(verdict="reuse", skip_modeling=True,
               why="台账已登记、文件在盘、规格一致 —— 不要重复建模",
               action=("直接复用 %s（rev=%s）。禁止再新建同名件："
                       "重复建模会产生 xxx1.SLDPRT 这类多余件"
                       % (_f, rec.get("rev"))))
    return out


def list_parts(work_dir=None):
    """列出全部零件（按名称排序，附摘要与统计）。"""
    # 【C8 修复】只读查询：不拦截旧台账，但在结果里回显 task_id 不一致警告
    data, path, err = _load_registry(work_dir, strict_task_id=False)
    if err:
        return {"ok": False, "error": err, "file": path}
    parts = data["parts"]
    items = [_part_brief(parts[k]) for k in sorted(parts.keys())]
    rooms = {}
    for it in items:
        r = it.get("room") or "(未填房间)"
        rooms[r] = rooms.get(r, 0) + 1
    return {
        "ok": True,
        "file": path,
        "task_id": data.get("task_id"),
        "updated_at": data.get("updated_at"),
        "count": len(items),
        "rooms": rooms,
        # 【C8 修复】回显任务号不一致警告（只读不拦截，但必须让人看见）
        "task_id_mismatch": data.get("task_id_mismatch"),
        "parts": items,
        "note": ("台账为空时 count=0 —— 这不是错误，但此时任何一致性检查都"
                 "无法给出结论（见 check_adjacency_consistency 的 evaluated 字段）。"),
    }


def _edge_map(data):
    """由 adjacent 构造无向邻接表 + 单向/悬空明细。

    返回 (adj: {name: set(neighbors)}, one_way: [(a,b)], dangling: [(a,b)])。
    只把「台账里已登记」的邻居计入 adj（悬空的另列，避免 BFS 走进不存在的零件）。
    """
    parts = data.get("parts") or {}
    adj = {k: set() for k in parts}
    one_way, dangling = [], []
    for src in parts:
        for dst in _parse_list_arg(parts[src].get("adjacent")):
            kdst = _resolve_key(parts, dst)
            if kdst is None:
                dangling.append((src, dst))
                continue
            if kdst == src:
                continue
            adj[src].add(kdst)
            # 回边检查：对方是否把我（用它的真实键）也列进 adjacent
            back = [_resolve_key(parts, x) or x
                    for x in _parse_list_arg(parts[kdst].get("adjacent"))]
            if src not in back and (src, kdst) not in one_way:
                one_way.append((src, kdst))
    return adj, one_way, dangling


def related_parts(name, depth=1, work_dir=None):
    """★ 核心：按零件反查关联零件（对 adjacent 图做 BFS，depth 控制层数）。

    这是用户要求的"后面哪个零件数据错了、或者要改，可以根据这个文件去看
    那个是相关联的"的查询入口。

    Returns:
        {"ok": True, "root": ..., "depth": n, "related": [名称...],
         "by_distance": {1: [...], 2: [...]}, "related_detail": [...],
         "unreachable": [...], "file": ...}
    """
    name = _norm_name(name)
    if not name:
        return {"ok": False, "error": "缺少零件名 name"}
    try:
        depth = int(depth)
    except Exception:
        return {"ok": False, "error": "depth 必须是整数，收到 %r" % (depth,)}
    if depth < 0:
        return {"ok": False, "error": "depth 不能为负：%d" % depth}

    # 【C8 修复】只读查询：不拦截旧台账，但在结果里回显 task_id 不一致警告
    data, path, err = _load_registry(work_dir, strict_task_id=False)
    if err:
        return {"ok": False, "error": err, "file": path}
    parts = data["parts"]
    root = _resolve_key(parts, name)
    if root is None:
        return {"ok": False, "error": "零件未登记：%s" % name, "file": path,
                "known_parts": sorted(parts.keys())}

    adj, _one_way, _dangling = _edge_map(data)

    # ── BFS：逐层扩散，记录每个零件的 (距离, 经由谁发现) ────────────────
    by_distance = {}
    detail = []
    visited = {root: (0, None)}
    frontier = [root]
    for d in range(1, depth + 1):
        nxt = []
        layer = []
        for cur in frontier:
            for nb in sorted(adj.get(cur, ())):
                if nb in visited:
                    continue
                visited[nb] = (d, cur)
                layer.append(nb)
                nxt.append(nb)
        if not layer:
            break
        by_distance[d] = sorted(layer)
        for nm in sorted(layer):
            rec = parts[nm]
            detail.append({
                "name": nm,
                "distance": d,
                "via": visited[nm][1],
                "room": rec.get("room"),
                "rev": rec.get("rev"),
                "shared_interfaces": _shared_interface_ids(parts, root, nm),
            })
        frontier = nxt

    related = []
    for d in sorted(by_distance.keys()):
        related.extend(by_distance[d])

    return {
        "ok": True,
        "root": root,
        "depth": depth,
        "related": related,
        "by_distance": by_distance,
        "related_detail": detail,
        "related_count": len(related),
        "unreachable": sorted([k for k in parts if k not in visited]),
        "file": path,
        "task_id": data.get("task_id"),
        "note": ("按 adjacent 图 BFS 反查；depth=%d 表示最多扩到 %d 层。"
                 "距离 1 = 直接邻接（会直接受尺寸改动影响）。" % (depth, depth)),
    }


def _shared_interface_ids(parts, a, b):
    """两个零件之间「看起来是同一处配合」的接口对（id 相同 / 去噪声后相同 / 显式声明）。

    纯信息展示，不做判定（判定在 check_adjacency_consistency 里）。
    """
    ia = _norm_interfaces(parts.get(a, {}).get("interfaces"))[0]
    ib = _norm_interfaces(parts.get(b, {}).get("interfaces"))[0]
    hits = []
    for x in ia:
        for y in ib:
            why = _ids_related(x, y, a, b)
            if why:
                hits.append({"a_interface": x["id"], "b_interface": y["id"],
                             "matched_by": why,
                             "a_nominal_mm": x["nominal"], "b_nominal_mm": y["nominal"],
                             "consistent": (None if (x["nominal"] is None or y["nominal"] is None)
                                            else abs(x["nominal"] - y["nominal"]) < 1e-9)})
    return hits


def reverse_lookup(name, work_dir=None):
    """反向查：哪些零件把 name 列进了自己的 adjacent（入边）。

    入边 = "谁依赖我"。改动 name 的尺寸时，入边的零件最可能受牵连，
    因此这个查询是 find_related_for_change 的第一层依据。
    """
    name = _norm_name(name)
    if not name:
        return {"ok": False, "error": "缺少零件名 name"}
    # 【C8 修复】只读查询：不拦截旧台账，但在结果里回显 task_id 不一致警告
    data, path, err = _load_registry(work_dir, strict_task_id=False)
    if err:
        return {"ok": False, "error": err, "file": path}
    parts = data["parts"]
    key = _resolve_key(parts, name)

    inbound, outbound = [], []
    for src, rec in parts.items():
        adj_keys = [_resolve_key(parts, x) or x for x in _parse_list_arg(rec.get("adjacent"))]
        if key is not None and key in adj_keys and src != key:
            declared = [x for x in _parse_list_arg(rec.get("adjacent"))
                        if (_resolve_key(parts, x) or x) == key]
            # 入边 src→key 是双向的，当且仅当 key 的回边 key→src 存在。
            # （历史缺陷：这里误写成判断 key 是否列了 key，恒为 False，
            #   导致所有入边都被错报成单向。）
            back = src in [_resolve_key(parts, x) or x
                           for x in _parse_list_arg(parts[key].get("adjacent"))]
            inbound.append({"from": src, "declared_as": declared[0] if declared else key,
                            "room": rec.get("room"), "rev": rec.get("rev"),
                            "bidirectional": bool(back)})
        if key is not None and src == key:
            for x in _parse_list_arg(rec.get("adjacent")):
                kx = _resolve_key(parts, x)
                back = False
                if kx is not None:
                    back = key in [_resolve_key(parts, y) or y
                                   for y in _parse_list_arg(parts[kx].get("adjacent"))]
                outbound.append({"to": kx or x, "registered": kx is not None,
                                 "bidirectional": bool(back)})

    if key is None:
        return {"ok": False,
                "error": "零件未登记：%s" % name,
                "file": path,
                "inbound": inbound,
                "known_parts": sorted(parts.keys()),
                "hint": ("该名字不在台账中；inbound 列出的是「别的零件提到了这个名字」"
                         "（悬空引用）")}

    return {
        "ok": True,
        "part": key,
        "file": path,
        "inbound": sorted(inbound, key=lambda x: x["from"]),
        "inbound_names": sorted([x["from"] for x in inbound]),
        "in_degree": len(inbound),
        "outbound": sorted(outbound, key=lambda x: str(x["to"])),
        "outbound_names": sorted([str(x["to"]) for x in outbound]),
        "out_degree": len(outbound),
        "one_way_inbound": sorted([x["from"] for x in inbound if not x["bidirectional"]]),
        "unregistered_outbound": sorted([str(x["to"]) for x in outbound if not x["registered"]]),
        "note": ("inbound=谁把我列为邻接（谁依赖我）；outbound=我列了谁。"
                 "one_way_inbound / unregistered_outbound 非空说明邻接图登记不全。"),
    }


# ══════════════════════════════════════════════════════════════════════════
#  ★ 尺寸一致性检查
# ══════════════════════════════════════════════════════════════════════════

def _ids_related(a, b, part_a="", part_b=""):
    """判定两个接口是否指同一处配合（返回匹配依据字符串，不匹配返回 None）。

    规则（与 R1/R2/R3 对应）：
      · "same_id"       ：id 完全相同
      · "explicit_mate" ：任一侧用 mates_with 等字段声明了对方 id 或对方零件名
      · "affix_id"      ：【轴孔互补】且去掉各自类型的噪声词后余下标识相同
    """
    if a["id"] == b["id"]:
        return "same_id"

    for side, other, other_part in ((a, b, part_b), (b, a, part_a)):
        for m in side.get("mates") or []:
            if m == other["id"] or (other_part and m == other_part):
                return "explicit_mate"

    # R3 附加约束：只有轴+孔互补才可能是同一处配合。
    # （历史缺陷：不判互补时，"键槽1"↔"孔径1" 都会被削成 "1" 而误配对，
    #   进而报出根本不存在的尺寸冲突。）
    if not _types_complementary(a["type"], b["type"]):
        return None
    ra, rb = _strip_id_noise(a["id"], a["type"]), _strip_id_noise(b["id"], b["type"])
    if ra and rb and ra == rb:
        return "affix_id"
    return None


def _pair_interfaces(ia, ib, name_a, name_b):
    """按 R1→R2→R3→R4 的优先级为两个零件的接口配对。

    Returns: (matches, unmatched_a, unmatched_b, notes)
      matches: [{"a": iface, "b": iface, "matched_by": str}]
    """
    matches, notes = [], []
    used_a, used_b = set(), set()

    # ── R1 + R2 + R3：逐对扫描，按依据优先级择优 ──────────────────────
    cands = []
    for xa, x in enumerate(ia):
        for xb, y in enumerate(ib):
            why = _ids_related(x, y, name_a, name_b)
            if why:
                rank = {"same_id": 0, "explicit_mate": 1, "affix_id": 2}[why]
                cands.append((rank, xa, xb, why))
    cands.sort(key=lambda t: (t[0], t[1], t[2]))
    for rank, xa, xb, why in cands:
        if xa in used_a or xb in used_b:
            continue
        used_a.add(xa)
        used_b.add(xb)
        matches.append({"a": ia[xa], "b": ib[xb], "matched_by": why})

    # ── R4：一侧恰好 1 个 shaft、另一侧恰好 1 个 hole（都带 nominal）───
    def _uniq(seq, t):
        got = [i for i in seq if i["type"] == t and i["nominal"] is not None]
        return got[0] if len(got) == 1 else None

    for (src, dst, sname, dname, s_idx, d_idx) in (
            (ia, ib, name_a, name_b, used_a, used_b),
            (ib, ia, name_b, name_a, used_b, used_a)):
        s = _uniq(src, "shaft")
        h = _uniq(dst, "hole")
        if s is None or h is None:
            continue
        xs = src.index(s)
        xh = dst.index(h)
        if xs in s_idx or xh in d_idx:
            continue
        s_idx.add(xs)
        d_idx.add(xh)
        if src is ia:
            matches.append({"a": s, "b": h, "matched_by": "unique_shaft_hole"})
        else:
            matches.append({"a": h, "b": s, "matched_by": "unique_shaft_hole"})
        notes.append("R4 唯一轴孔推定：%s.%s ↔ %s.%s（两侧各只有一个轴/孔接口，"
                     "按配合对处理）" % (sname, s["id"], dname, h["id"]))

    unmatched_a = [i for n, i in enumerate(ia) if n not in used_a]
    unmatched_b = [i for n, i in enumerate(ib) if n not in used_b]
    return matches, unmatched_a, unmatched_b, notes


def _adjacency_pairs(data):
    """收集全部邻接对（无向去重）+ 单向/悬空明细。

    Returns: (pairs, one_way, dangling, self_loops)
      pairs: [(a, b)]，a、b 均为台账里真实存在的零件名
    """
    parts = data.get("parts") or {}
    seen = set()
    pairs = []
    one_way, dangling, self_loops = [], [], []
    for src in parts:
        for raw in _parse_list_arg(parts[src].get("adjacent")):
            dst = _resolve_key(parts, raw)
            if dst is None:
                dangling.append({"from": src, "to": raw})
                continue
            if dst == src:
                self_loops.append(src)
                continue
            key = tuple(sorted([src, dst]))
            if key in seen:
                continue
            seen.add(key)
            pairs.append((src, dst))
            back = [_resolve_key(parts, x) or x
                    for x in _parse_list_arg(parts[dst].get("adjacent"))]
            if src not in back:
                one_way.append({"from": src, "to": dst})
    pairs.sort()
    return pairs, one_way, dangling, self_loops


def _check_core(data, only_pairs=None):
    """一致性检查内核（check_adjacency_consistency 与 find_related_for_change 共用）。

    only_pairs: 若给出 [(a,b)]，只检查这些对（用于"改某个零件时"的定向复检）。
    """
    parts = data.get("parts") or {}
    # ══ 【2026-11-11 新增·调试件不参与一致性核对】══════════════════════════
    # 现场（20261111_131815_580E38FA）：台账 15 条里 6 条是调试/测试件
    #   （测试块、DIAG_*），且 parts/ 下文件早已被清掉。
    # 为什么必须在这里滤掉（而不是只在登记时提示）：
    #   _adjacency_pairs 只按 adjacent 字段连边，调试件一旦被别的零件写进
    #   adjacent（小屋调试时经常顺手写上"测试块"），就会凭空产生 A-B 配对 →
    #   以"接口缺失"或"尺寸不符"的形式污染 mismatches / insufficient，
    #   而真正交付件的冲突会被淹没在这些噪音里（假阳性把真问题埋掉）。
    # 处理：把它们单独归入 debug_parts 名单并给出 INFO 级提醒，
    #   不参与 pairs 计算 —— 既不假装看不见，也不让它们制造假冲突。
    _debug_names = []
    _debug_notes = []
    for _nm in list(parts.keys()):
        _hit = _looks_like_debug_part(_nm)
        if not _hit:
            continue
        _debug_names.append(_nm)
        _debug_notes.append({
            "kind": "debug_part_excluded",
            "part": _nm,
            "severity": "INFO",
            "escalate_to_parent": False,
            "detail": ("%s 是调试/测试件（命中 %r），已排除在相邻尺寸核对之外；"
                       "交付前应从台账剔除，不要让它进 docx。" % (_nm, _hit)),
        })
    _dbg = set(_debug_names)

    pairs, one_way, dangling, self_loops = _adjacency_pairs(data)
    if _dbg:
        # 只要有一端是调试件，这条边就不该参与核对
        pairs = [(a, b) for (a, b) in pairs if a not in _dbg and b not in _dbg]
        one_way = [x for x in one_way
                   if x.get("from") not in _dbg and x.get("to") not in _dbg]
        dangling = [x for x in dangling
                    if x.get("from") not in _dbg and x.get("to") not in _dbg]
        self_loops = [x for x in self_loops if x not in _dbg]
    if only_pairs is not None:
        want = set(tuple(sorted(p)) for p in only_pairs)
        pairs = [p for p in pairs if tuple(sorted(p)) in want]

    mismatches, warnings, insufficient, pair_rows = [], [], [], []
    pairs_with_data = 0

    for a, b in pairs:
        rec_a, rec_b = parts[a], parts[b]
        ia, drop_a = _norm_interfaces(rec_a.get("interfaces"))
        ib, drop_b = _norm_interfaces(rec_b.get("interfaces"))
        for d in drop_a:
            warnings.append({"kind": "bad_interface_item", "part": a,
                             "severity": "WARN", "escalate_to_parent": False,
                             "detail": "接口项被忽略（%s）" % d.get("reason")})
        for d in drop_b:
            warnings.append({"kind": "bad_interface_item", "part": b,
                             "severity": "WARN", "escalate_to_parent": False,
                             "detail": "接口项被忽略（%s）" % d.get("reason")})

        matches, un_a, un_b, notes = _pair_interfaces(ia, ib, a, b)
        for n in notes:
            warnings.append({"kind": "inferred_pair", "parts": [a, b],
                             "severity": "INFO", "escalate_to_parent": False,
                             "detail": n})

        comparable = 0
        row = {"parts": [a, b], "matched_interfaces": [], "unmatched": [],
               "status": "no_interface_data"}

        for m in matches:
            xa, xb = m["a"], m["b"]
            entry = {"a_interface": xa["id"], "a_type": xa["type"],
                     "a_nominal_mm": xa["nominal"],
                     "b_interface": xb["id"], "b_type": xb["type"],
                     "b_nominal_mm": xb["nominal"], "matched_by": m["matched_by"]}
            row["matched_interfaces"].append(entry)

            # 判定 1：两侧 nominal 都有 → 必须相等
            if xa["nominal"] is not None and xb["nominal"] is not None:
                comparable += 1
                diff = round(abs(xa["nominal"] - xb["nominal"]), 6)
                if diff > 1e-9:
                    mismatches.append({
                        "parts": [a, b],
                        "interface": xa["id"],
                        "interfaces": {"%s" % a: {"id": xa["id"], "type": xa["type"],
                                                  "nominal_mm": xa["nominal"],
                                                  "tolerance": xa["tolerance"]},
                                       "%s" % b: {"id": xb["id"], "type": xb["type"],
                                                  "nominal_mm": xb["nominal"],
                                                  "tolerance": xb["tolerance"]}},
                        "matched_by": m["matched_by"],
                        "detail": ("%s %s nominal=%smm，%s %s nominal=%smm，差 %smm"
                                   % (a, xa["id"], _fmt_num(xa["nominal"]),
                                      b, xb["id"], _fmt_num(xb["nominal"]),
                                      _fmt_num(diff))),
                        "severity": "ERROR",
                        "escalate_to_parent": True,
                        "suggestion": ("相邻配合的基本尺寸必须一致：请确认哪一侧是设计值，"
                                       "改另一侧或补过渡件；公差带差异不影响本判定。"),
                    })
            else:
                # 判定 1'：同 id 却缺 nominal —— 不能算通过，明确记账
                missing_side = a if xa["nominal"] is None else b
                warnings.append({
                    "kind": "missing_nominal", "parts": [a, b],
                    "interface": xa["id"], "part_missing_nominal": missing_side,
                    "severity": "WARN", "escalate_to_parent": False,
                    "detail": ("接口 %s 在 %s 侧缺少 nominal_mm，无法核对基本尺寸 "
                               "（不判定为通过）" % (xa["id"], missing_side)),
                })

            # 判定 2：同 id 但两侧类型都是轴/都是孔 → 类型冲突，交父代理仲裁
            if m["matched_by"] == "same_id" and xa["type"] == xb["type"] \
                    and xa["type"] in ("shaft", "hole"):
                warnings.append({
                    "kind": "interface_type_conflict", "parts": [a, b],
                    "interface": xa["id"], "severity": "WARN",
                    "escalate_to_parent": True,
                    "detail": ("同名接口 %s 在两侧都是 %s —— 一处配合通常应为"
                               "轴+孔；请确认是否重复登记或类型填错"
                               % (xa["id"], "轴(shaft)" if xa["type"] == "shaft" else "孔(hole)")),
                })

            # 判定 3：孔用了轴的公差带字母（或反之）→ 记录（不拦流程）
            for side, it in ((a, xa), (b, xb)):
                tk = _tolerance_kind(it["tolerance"])
                if it["type"] == "hole" and tk == "shaft":
                    warnings.append({"kind": "tolerance_letter_suspect", "parts": [a, b],
                                     "interface": it["id"], "severity": "WARN",
                                     "escalate_to_parent": False,
                                     "detail": ("%s 的 %s 为孔，公差带 %r 却是轴用字母"
                                                % (side, it["id"], it["tolerance"]))})
                if it["type"] == "shaft" and tk == "hole":
                    warnings.append({"kind": "tolerance_letter_suspect", "parts": [a, b],
                                     "interface": it["id"], "severity": "WARN",
                                     "escalate_to_parent": False,
                                     "detail": ("%s 的 %s 为轴，公差带 %r 却是孔用字母"
                                                % (side, it["id"], it["tolerance"]))})

        if comparable > 0:
            pairs_with_data += 1
            row["status"] = "checked"
            row["comparable_interfaces"] = comparable
        else:
            insufficient.append({
                "parts": [a, b],
                "reason": ("双方都未声明 interfaces" if (not ia and not ib)
                           else "没有任何一对接口能配对（id 不相关且非唯一轴孔）"
                           if (ia and ib)
                           else "仅一侧声明了 interfaces"),
                "a_interface_count": len(ia), "b_interface_count": len(ib),
            })
        if un_a or un_b:
            row["unmatched"] = {
                a: [i["id"] for i in un_a],
                b: [i["id"] for i in un_b],
            }
        pair_rows.append(row)

    # ── 全局体检：单向邻接 / 悬空 / 自环 / dims↔bbox ────────────────────
    for ow in one_way:
        warnings.append({
            "kind": "one_way_adjacency", "parts": [ow["from"], ow["to"]],
            "severity": "WARN", "escalate_to_parent": True,
            "detail": ("单向邻接：%s 把 %s 列为 adjacent，但 %s 未回列 —— "
                       "邻接图登记不全，反查会漏零件；请用 link_adjacent 双向建立"
                       % (ow["from"], ow["to"], ow["to"])),
        })
    for sl in self_loops:
        warnings.append({"kind": "self_adjacency", "parts": [sl, sl],
                         "severity": "WARN", "escalate_to_parent": True,
                         "detail": "零件 %s 把自己列为 adjacent（自环），无意义" % sl})
    for dg in dangling:
        warnings.append({
            "kind": "dangling_adjacency", "parts": [dg["from"], dg["to"]],
            "severity": "WARN", "escalate_to_parent": True,
            "detail": ("悬空邻接：%s 把 %r 列为 adjacent，但该零件从未登记到台账 —— "
                       "无法参与任何一致性核对" % (dg["from"], dg["to"])),
        })
    for nm, rec in sorted(parts.items()):
        dims = rec.get("dims")
        bbox = rec.get("bbox_mm")
        if not isinstance(dims, dict) or not bbox or len(bbox) != 6:
            continue
        derived = _dims_from_bbox(bbox)
        if not derived:
            continue
        delta = _dims_delta(dims, derived)
        if delta > 0.5:
            warnings.append({
                "kind": "self_bbox_mismatch", "parts": [nm, nm],
                "severity": "WARN", "escalate_to_parent": True,
                "detail": ("%s 的 dims(%s) 与 bbox_mm 推算尺寸(%s) 相差 %smm —— "
                           "两份尺寸数据至少有一份是错的"
                           % (nm, _fmt_dims(dims), _fmt_dims(derived), _fmt_num(delta))),
            })

    return {
        "pairs": pairs,
        "pair_rows": pair_rows,
        "pairs_with_interface_data": pairs_with_data,
        "mismatches": mismatches,
        "warnings": warnings,
        "insufficient": insufficient,
        "dangling": dangling,
        "one_way": one_way,
        "self_loops": self_loops,
        # 【2026-11-11】调试/测试件：已排除在核对之外，但必须如实报出来
        "debug_parts": _debug_names,
        "debug_notes": _debug_notes,
    }


def _dims_delta(d1, d2):
    """两份尺寸 dict 的最大分量差（缺键按 0 处理，返回绝对值）。"""
    keys = set(d1.keys()) | set(d2.keys())
    worst = 0.0
    for k in keys:
        try:
            a = float(d1.get(k) or 0)
            b = float(d2.get(k) or 0)
        except Exception:
            continue
        worst = max(worst, abs(a - b))
    return round(worst, 4)


def check_adjacency_consistency(work_dir=None, only_pairs=None):
    """★ 遍历所有邻接对，核对直接接触零件的接口尺寸是否配置一致。

    判定规则见模块 docstring（R1 same_id / R2 explicit_mate / R3 affix_id /
    R4 unique_shaft_hole 配对；配对后 nominal_mm 必须相等，公差带可不同）。

    Returns（关键字段）:
        ok                 : 只在 evaluated=true 且无 mismatch 时为 true
        evaluated          : 是否有"真的可查"的数据（无数据 → false，绝不假 PASS）
        reason             : evaluated=false 时的具体原因
        checked_pairs      : 检查的邻接对总数
        pairs_with_interface_data : 其中真的比过尺寸的对数
        mismatches         : [{"parts","interface","detail","severity"="ERROR",
                               "escalate_to_parent": true, ...}]
        warnings / insufficient / dangling_adjacency
        escalate_to_parent : 有 mismatch 或数据完整性问题时为 true（交父代理干涉）
        summary            : 一句话结论
    """
    # 【C8 修复】只读查询：不拦截旧台账，但在结果里回显 task_id 不一致警告
    data, path, err = _load_registry(work_dir, strict_task_id=False)
    if err:
        return {"ok": False, "evaluated": False, "error": err, "file": path,
                "checked_pairs": 0, "mismatches": [], "escalate_to_parent": False,
                "summary": "台账不可读，无法检查。"}

    parts = data["parts"]
    core = _check_core(data, only_pairs=only_pairs)
    pairs = core["pairs"]
    checked = len(pairs)
    with_data = core["pairs_with_interface_data"]
    mismatches = core["mismatches"]

    # ── evaluated 判定（区分"真通过"与"没数据"）─────────────────────────
    evaluated = with_data > 0
    if not parts:
        reason = "台账为空（parts 为空），没有任何零件可检查"
    elif checked == 0:
        reason = ("台账有 %d 个零件，但没有任何邻接关系（adjacent 全为空）—— "
                  "请先用 link_adjacent 建立配合关系" % len(parts))
    elif with_data == 0:
        reason = ("%d 对邻接零件中没有一对同时声明了可比较的 interfaces/nominal_mm —— "
                  "接口数据缺失，无法核对尺寸一致性（这不是通过）" % checked)
    else:
        reason = None

    escalate = bool(mismatches) or bool(core["dangling"]) or \
        any(w.get("escalate_to_parent") for w in core["warnings"])

    if not evaluated:
        summary = "未能检查：%s" % reason
    elif mismatches:
        summary = ("发现 %d 处相邻零件尺寸不一致（共检查 %d 对，其中 %d 对可比对）—— "
                   "已标记 escalate_to_parent=true，交父代理仲裁干涉。"
                   % (len(mismatches), checked, with_data))
    else:
        summary = ("通过：%d 对相邻零件中 %d 对完成接口尺寸比对，未发现基本尺寸冲突。"
                   % (checked, with_data))
        if core["insufficient"]:
            summary += (" 另有 %d 对因缺少接口数据未比对（见 insufficient）。"
                        % len(core["insufficient"]))

    return {
        "ok": bool(evaluated and not mismatches),
        "evaluated": bool(evaluated),
        "reason": reason,
        "file": path,
        "task_id": data.get("task_id"),
        "updated_at": data.get("updated_at"),
        # 【C8 修复】旧台账被当成当前台账时，把警告随结论一起回显 ——
        #   否则"检查通过/不一致"看起来是当前任务的事实，实际读的是上一轮的数据。
        "task_id_mismatch": data.get("task_id_mismatch"),
        "parts_count": len(parts),
        "checked_pairs": checked,
        "pairs_with_interface_data": with_data,
        "pairs": [{"parts": list(p)} for p in pairs],
        "pair_rows": core["pair_rows"],
        "mismatches": mismatches,
        "mismatch_count": len(mismatches),
        "warnings": core["warnings"],
        "warning_count": len(core["warnings"]),
        "insufficient": core["insufficient"],
        "insufficient_count": len(core["insufficient"]),
        "dangling_adjacency": core["dangling"],
        "one_way_adjacency": core["one_way"],
        "self_adjacency": core["self_loops"],
        # ── 经用户如实说明理由后仍保留的已知不一致（绝不静默）────────────
        "unresolved": _open_unresolved(data),
        "unresolved_count": len(_open_unresolved(data)),
        "escalate_to_parent": bool(escalate),
        # ── 【2026-11-11】调试/测试件：已从 pairs 里排除，但如实回显 ────────
        # 现场：台账 15 条里 6 条是"测试块 / DIAG_*"，parts/ 下文件早没了。
        # 它们必须【被排除在核对之外】（否则凭空造出假冲突），
        # 但也必须【让人看得见】（否则交付台账里混着 DIAG_* 都没人知道）。
        "debug_parts": core.get("debug_parts") or [],
        "debug_count": len(core.get("debug_parts") or []),
        "summary": summary,
        "note": ("evaluated=false 表示「没数据可查」，与「检查通过」严格区分；"
                 "不一致项一律 severity=ERROR、escalate_to_parent=true。"),
    }


def _unresolved_is_open(data, u):
    """判断某条 unresolved 记录【当前是否仍未决】（全模块唯一判定口径）。

    ── 为什么必须只有一个口径 ────────────────────────────────────────────
    一条记录的"未决"由两件事共同决定：
      ① status 仍为 open（没人手工关闭过）；
      ② 该不一致【现在依然存在】（两侧还没被改成一致）。
    历史缺陷：list_unresolved 用 ①+②，而 resolve_unresolved 报的 open_indexes
    只用 ① —— 于是"改一致后自动消解"的记录仍被算作可关闭，两个接口给出的
    未决数量互相矛盾（实测 G3.11 抓到这个不一致）。
    现在统一到这里，任何地方判断未决都必须调它。
    """
    if not isinstance(u, dict):
        return False
    if str(u.get("status") or "open").lower() != "open":
        return False
    parts = (data or {}).get("parts") or {}
    live = _still_inconsistent(parts, u.get("parts") or [], u.get("interface"))
    return live is not False     # False=已一致 → 自动消解；True/None → 仍视为未决


def _open_unresolved(data):
    """取台账里仍处于未决状态的 unresolved 记录（已放行但仍需处理的已知问题）。"""
    return [u for u in ((data or {}).get("unresolved") or [])
            if _unresolved_is_open(data, u)]


def _still_inconsistent(parts, names, iface_id):
    """判断某个 unresolved 记录现在是否仍然不一致。

    返回 True=仍不一致 / False=已一致（可自动消解）/ None=无法判断（保留）。
    """
    if not names or len(names) != 2:
        return None
    a = _resolve_key(parts, names[0])
    b = _resolve_key(parts, names[1])
    if a is None or b is None:
        return None
    ia, _ = _norm_interfaces(parts[a].get("interfaces"))
    ib, _ = _norm_interfaces(parts[b].get("interfaces"))
    found = False
    for x in ia:
        for y in ib:
            if iface_id and x["id"] != iface_id and y["id"] != iface_id:
                continue
            if x["nominal"] is None or y["nominal"] is None:
                continue
            if not _ids_related(x, y, a, b):
                continue
            found = True
            if abs(x["nominal"] - y["nominal"]) > 1e-9:
                return True
    return False if found else None


def list_unresolved(work_dir=None, include_resolved=False):
    """列出"经如实说明理由后仍保留"的已知不一致（unresolved 台账）。

    这些不是被遗忘的问题 —— 是【被显式记录并放行】的问题，必须始终可见，
    直到有人把它们改一致（自动消解）或显式标记 resolved。
    """
    # 【C8 修复】只读查询：不拦截旧台账，但在结果里回显 task_id 不一致警告
    data, path, err = _load_registry(work_dir, strict_task_id=False)
    if err:
        return {"ok": False, "error": err, "file": path}
    allrec = [u for u in (data.get("unresolved") or []) if isinstance(u, dict)]
    openrec = _open_unresolved(data)
    resolved = [u for u in allrec
                if str(u.get("status") or "open").lower() != "open"]
    out = openrec if not include_resolved else allrec
    # ── 索引必须与 items 一一对应 ──────────────────────────────────────
    # 历史缺陷：items 返回的是 open 子集，而 resolve_unresolved 的 index 作用在
    # 【完整数组】上 —— 一旦有记录已被自动消解（改一致了），用户在清单里看到的
    # 第 0 条与真正被 resolve 的第 0 条就不是同一条（实测：显示 A-C，却关掉了 A-B）。
    # 现在给每条【输出项】带上它在完整数组里的真实下标 index。这里用浅拷贝，
    # 避免把下标写进台账本体（下标会随数组变化，存进去只会误导）。
    full_index = {id(u): n for n, u in enumerate(allrec)}
    out = [dict(u, index=full_index.get(id(u))) for u in out]
    return {
        "ok": True,
        "file": path,
        "count": len(out),
        "open_count": len(openrec),
        "resolved_count": len(resolved),
        "items": out,
        "summary": ("存在 %d 处【已知并放行】的尺寸不一致，需要处理"
                    % len(openrec)) if openrec else "没有未决的不一致记录",
        "note": ("unresolved 记录来自 allow_mismatch（带理由放行）或删除/改名等操作；"
                 "当两侧接口尺寸被改成一致后，记录会自动从 open 列表消失。"
                 "关闭某条请用该条的 index 字段（已随记录一并返回），不要用它在"
                 "清单里的位置 —— 自动消解会改变位置。"),
    }


def resolve_unresolved(index, work_dir=None, reason=None, note=None):
    """把某条 unresolved 记录标记为 resolved（需如实说明理由）。

    Args:
        index: 记录在 unresolved 完整数组里的下标（即 list_unresolved 返回的
               items[*].index）。★ 不要用它在清单里的位置 —— 自动消解会改变位置。
        reason: ★ 必填，说明为什么可以判定该问题已解决
    """
    err = _require_reason(reason, op="resolve_unresolved", what="index=%s" % index)
    if err:
        return err
    data, path, e = _load_registry(work_dir)
    if e:
        return {"ok": False, "error": e, "file": path}
    try:
        requested = int(index)
    except Exception:
        return {"ok": False, "error": "index 必须是整数，收到 %r" % (index,)}

    arr = data.get("unresolved") or []
    if not arr:
        return {"ok": False, "error": "台账里没有任何 unresolved 记录", "file": path}
    if requested < 0 or requested >= len(arr):
        return {"ok": False,
                "error": "index 越界：%d（完整数组共 %d 条）" % (requested, len(arr)),
                "file": path}

    # ── 安全网：拒绝关闭"其实已经不未决"的记录，并给出正确的下标建议 ──────
    # 这样即使调用方用的是清单位置（而非 index 字段），也不会误关另一条。
    target = arr[requested]
    if not _unresolved_is_open(data, target):
        why = ("该记录 status 已是 resolved" if str(target.get("status") or "open").lower()
               != "open" else
               "该记录对应的不一致【已经不存在了】（两侧尺寸已改一致，系统已自动消解）")
        return {"ok": False,
                "error": ("index=%d 无需关闭：%s" % (requested, why)),
                "record": target,
                "open_indexes": [n for n, x in enumerate(arr)
                                 if _unresolved_is_open(data, x)],
                "file": path}

    _ts, at = _now()
    arr[requested]["status"] = "resolved"
    arr[requested]["resolved_reason"] = str(reason).strip()
    arr[requested]["resolved_at"] = at
    if note:
        arr[requested]["resolved_note"] = str(note)
    data["unresolved"] = arr
    _append_revision(data, "resolve-unresolved", arr[requested].get("part") or "-",
                     changes={"unresolved_index": requested, "status": "resolved"},
                     reason=str(reason).strip(),
                     extra={"unresolved_detail": arr[requested].get("detail"),
                            "unresolved_parts": arr[requested].get("parts")})
    ok, path, werr = _save_registry(data, work_dir)
    if not ok:
        return {"ok": False, "error": werr, "file": path}
    return {"ok": True, "action": "resolve-unresolved", "index": requested,
            "parts": arr[requested].get("parts"),
            "detail": arr[requested].get("detail"), "file": path,
            "remaining_open": len([x for x in arr if _unresolved_is_open(data, x)]),
            "note": "已标记为 resolved；该记录仍保留在台账中可追溯。"}


def find_related_for_change(name, work_dir=None, max_depth=3):
    """★ 要改某个零件时，返回所有可能需要跟着改的零件（含理由）。

    理由分类（reasons / reason_code）：
      · direct_adjacency   距离 1：直接接触/配合，尺寸改动直接影响
      · shared_interface   与该零件存在"同一处配合"的接口（id 相同 / 去噪声后相同 /
                           显式 mates_with 声明 / 唯一轴孔推定）
      · transitive_impact  距离 ≥2：二级及以上的传递影响（通过中间零件传导）
      · same_room          同房间（信息项，便于找到该找谁改）

    额外附带：针对受影响集合的定向一致性复检结果（改之前先看有没有既有冲突）。
    """
    name = _norm_name(name)
    if not name:
        return {"ok": False, "error": "缺少零件名 name"}
    # 【C8 修复】只读查询：不拦截旧台账，但在结果里回显 task_id 不一致警告
    data, path, err = _load_registry(work_dir, strict_task_id=False)
    if err:
        return {"ok": False, "error": err, "file": path}
    parts = data["parts"]
    root = _resolve_key(parts, name)
    if root is None:
        return {"ok": False, "error": "零件未登记：%s" % name, "file": path,
                "known_parts": sorted(parts.keys())}

    try:
        max_depth = int(max_depth)
    except Exception:
        max_depth = 3
    max_depth = max(1, min(max_depth, 10))

    adj, one_way, dangling = _edge_map(data)

    # ── BFS 求距离 + 经由 ───────────────────────────────────────────────
    dist = {root: 0}
    via = {root: None}
    frontier = [root]
    for d in range(1, max_depth + 1):
        nxt = []
        for cur in frontier:
            for nb in sorted(adj.get(cur, ())):
                if nb in dist:
                    continue
                dist[nb] = d
                via[nb] = cur
                nxt.append(nb)
        if not nxt:
            break
        frontier = nxt

    # ── 受影响集合 ─────────────────────────────────────────────────────
    impacted = []
    for nm in sorted([x for x in dist if x != root]):
        d = dist[nm]
        reasons, codes = [], []
        if d == 1:
            codes.append("direct_adjacency")
            reasons.append("直接邻接：与 %s 直接接触/配合（距离 1）" % root)
        else:
            codes.append("transitive_impact")
            reasons.append("%d 级传递影响：经由 %s 传导（%s → %s）"
                           % (d, via.get(nm), root, _path_text(via, root, nm)))
        shared = _shared_interface_ids(parts, root, nm)
        if shared:
            codes.append("shared_interface")
            for h in shared:
                if h["consistent"] is False:
                    reasons.append("共享接口 %s↔%s 的 nominal 已经不一致（%s vs %smm），"
                                   "改动前必须先定哪一侧是设计值"
                                   % (h["a_interface"], h["b_interface"],
                                      _fmt_num(h["a_nominal_mm"]), _fmt_num(h["b_nominal_mm"])))
                else:
                    reasons.append("共享接口 %s↔%s（配对依据 %s）"
                                   % (h["a_interface"], h["b_interface"], h["matched_by"]))
        rec = parts[nm]
        if rec.get("room") and rec.get("room") == parts[root].get("room"):
            codes.append("same_room")
            reasons.append("同属房间「%s」，可直接找该小屋改" % rec.get("room"))
        impacted.append({
            "name": nm,
            "distance": d,
            "via": via.get(nm),
            "reasons": reasons,
            "reason_codes": codes,
            "room": rec.get("room"),
            "rev": rec.get("rev"),
            "file": rec.get("file"),
            "shared_interfaces": shared,
            "adjacent_to_root": nm in adj.get(root, set()),
        })

    by_distance = {}
    for it in impacted:
        by_distance.setdefault(it["distance"], []).append(it["name"])

    # ── 定向复检：只查"根零件 ↔ 直接邻接"这些对 ─────────────────────────
    direct_pairs = [(root, nm) for nm in sorted(adj.get(root, ()))]
    focused = _check_core(data, only_pairs=direct_pairs) if direct_pairs else None
    focused_out = None
    if focused is not None:
        focused_out = {
            "checked_pairs": len(focused["pairs"]),
            "pairs_with_interface_data": focused["pairs_with_interface_data"],
            "evaluated": focused["pairs_with_interface_data"] > 0,
            "mismatches": focused["mismatches"],
        }

    my_rec = parts[root]
    return {
        "ok": True,
        "found": True,
        "part": root,
        "file": path,
        "task_id": data.get("task_id"),
        "part_brief": _part_brief(my_rec),
        "max_depth": max_depth,
        "impacted_count": len(impacted),
        "impacted": impacted,
        "by_distance": by_distance,
        "related": [it["name"] for it in impacted],
        "inbound": sorted(reverse_lookup(root, work_dir=work_dir).get("inbound_names") or []),
        "direct_adjacency_check": focused_out,
        "data_integrity": {
            "dangling_adjacency": dangling,
            "one_way_adjacency": [{"from": a, "to": b} for a, b in one_way],
        },
        "suggested_actions": _suggest_actions(root, impacted, focused_out),
        "summary": ("改动 %s 前，请同步复核 %d 个关联零件（直接邻接 %d 个、"
                    "传递影响 %d 个）。"
                    % (root, len(impacted),
                       len(by_distance.get(1) or []),
                       len([x for x in impacted if x["distance"] >= 2]))),
        "note": ("reasons 说明「为什么它需要跟着改」：直接邻接 / 共享接口 / 二级传递。"
                 "距离 1 的必须逐个复核；距离 ≥2 的按需复核。"),
    }


def _path_text(via, root, target):
    """把 via 链还原成 'A → B → C' 文本（用于传递影响的说明）。"""
    chain = []
    cur = target
    guard = 0
    while cur is not None and guard < 32:
        chain.append(cur)
        if cur == root:
            break
        cur = via.get(cur)
        guard += 1
    return " → ".join(reversed(chain))


def _suggest_actions(root, impacted, focused_out):
    """为「要改这个零件」生成可执行的下一步建议。"""
    acts = []
    direct = [x["name"] for x in impacted if x["distance"] == 1]
    if direct:
        acts.append("先逐个复核距离 1 的零件：%s" % "、".join(direct))
    if focused_out and focused_out.get("mismatches"):
        for m in focused_out["mismatches"]:
            acts.append("【改前先裁决】%s" % m["detail"])
    if focused_out and not focused_out.get("evaluated"):
        acts.append("直接邻接对缺 interfaces/nominal_mm 数据，"
                    "一致性无法核对 —— 请先补 registration 的 interfaces 字段")
    acts.append("改完后重跑 check-consistency，确认 escalate_to_parent=false")
    acts.append("把改动写入台账（update_part），保留修订历史以便回溯谁在何时改了哪个尺寸")
    return acts


# ══════════════════════════════════════════════════════════════════════════
#  docx 导出：三级策略
# ══════════════════════════════════════════════════════════════════════════

# ── 策略②：探测随包运行时的 site-packages（不写死路径）──────────────────

def _runtime_search_roots():
    """产出可能放着随包 Python 依赖的根目录（全部动态推导，无硬编码用户名）。"""
    roots = []
    home = os.path.expanduser("~")
    dsh_home = (os.environ.get("DSH_HOME") or "").strip().strip('"').strip("'")
    if dsh_home:
        roots.append(os.path.join(dsh_home, "dsh-runtimes"))
        roots.append(dsh_home)
    if home:
        roots.append(os.path.join(home, ".dsh", "dsh-runtimes"))
        roots.append(os.path.join(home, ".dsh"))
    # 本进程解释器自己的 site-packages 之外，也看看同目录的 Lib
    try:
        roots.append(os.path.dirname(os.path.dirname(sys.executable)))
    except Exception:
        pass
    out, seen = [], set()
    for r in roots:
        try:
            rp = os.path.abspath(r)
        except Exception:
            continue
        k = os.path.normcase(rp)
        if k in seen or not os.path.isdir(rp):
            continue
        seen.add(k)
        out.append(rp)
    return out


def _discover_docx_site_packages(max_depth=6):
    """在随包运行时目录里搜「含 docx 包」的 site-packages / dist-packages。

    Returns: [{"site_packages":..., "docx":..., "python_exe":...|None}, ...]
    深度受限，避免在大目录上无限遍历。
    """
    found = []
    seen = set()
    for root in _runtime_search_roots():
        base_depth = root.rstrip("\\/").count(os.sep)
        for dirpath, dirnames, _files in os.walk(root):
            if dirpath.count(os.sep) - base_depth > max_depth:
                dirnames[:] = []
                continue
            # 跳过明显无用的巨型目录
            dirnames[:] = [d for d in dirnames
                           if d.lower() not in ("__pycache__", ".git", "node_modules",
                                                "cache", "sessions", "attachments")]
            if os.path.basename(dirpath).lower() not in ("site-packages", "dist-packages"):
                continue
            pkg = os.path.join(dirpath, "docx", "__init__.py")
            if not os.path.isfile(pkg):
                continue
            # 去重：搜索根之间存在包含关系（~/.dsh 与 ~/.dsh/dsh-runtimes），
            # 不去重会把同一个 site-packages 探测/尝试多次（实测踩到）。
            k = os.path.normcase(os.path.abspath(dirpath))
            if k in seen:
                dirnames[:] = []
                continue
            seen.add(k)
            py = None
            probe = dirpath
            for _ in range(5):
                probe = os.path.dirname(probe)
                if not probe:
                    break
                cand = os.path.join(probe, "python.exe")
                if os.path.isfile(cand):
                    py = cand
                    break
            found.append({"site_packages": dirpath, "docx": pkg, "python_exe": py})
            dirnames[:] = []
    return found


def _try_import_docx(probe=True):
    """尝试拿到可用的 python-docx 模块。

    Returns: (module|None, strategy|None, diagnostics: dict)
      strategy: "python-docx"（直接导入成功）
                "python-docx(sys.path注入)"（注入随包 site-packages 后成功）
    ── 关键防御（本机实测踩到）──────────────────────────────────────────
    直接把随包 site-packages 塞进 sys.path 再 import docx，可能因为
    lxml 是【另一个 Python 版本编译的 C 扩展】而抛 ImportError（不是
    ModuleNotFoundError）。因此：
      · 捕获 Exception（不只 ImportError）；
      · 注入失败必须【把刚加的路径撤掉】，并清掉 sys.modules 里半成品
        docx* 模块 —— 否则会把宿主的 lxml 等包一起污染。
    """
    diag = {"probed": [], "injected": [], "attempts": [], "available": False,
            "module_path": None, "bundled_python": None}

    # ① 直接 import（probe=False 也要做：它只是禁止遍历文件系统，
    #    不应该连"当前解释器本来就装了 python-docx"这种情况也一起放弃）
    try:
        import docx  # noqa: F401
        diag["available"] = True
        diag["module_path"] = getattr(docx, "__file__", None)
        diag["attempts"].append({"step": "direct_import", "ok": True,
                                 "path": diag["module_path"]})
        return docx, "python-docx", diag
    except Exception as e:
        diag["attempts"].append({"step": "direct_import", "ok": False,
                                 "error": "%s: %s" % (type(e).__name__, e)})

    if not probe:
        return None, None, diag

    # ② 探测随包 site-packages → 注入 sys.path → 重试
    cands = _discover_docx_site_packages()
    diag["probed"] = cands
    for c in cands:
        sp = c["site_packages"]
        if os.path.normcase(sp) in [os.path.normcase(x) for x in sys.path]:
            continue
        added = False
        try:
            sys.path.append(sp)      # 追加而非插队：避免遮蔽标准库/宿主依赖
            added = True
            for m in [k for k in list(sys.modules) if k == "docx" or k.startswith("docx.")]:
                sys.modules.pop(m, None)
            import docx  # noqa: F401
            diag["available"] = True
            diag["module_path"] = getattr(docx, "__file__", None)
            diag["bundled_python"] = c.get("python_exe")
            diag["injected"].append(sp)
            diag["attempts"].append({"step": "inject_sys_path", "site_packages": sp, "ok": True,
                                     "path": diag["module_path"]})
            return docx, "python-docx(sys.path注入)", diag
        except Exception as e:
            diag["attempts"].append({"step": "inject_sys_path", "site_packages": sp, "ok": False,
                                     "error": "%s: %s" % (type(e).__name__, e),
                                     "hint": "常见原因：该 site-packages 下的 C 扩展"
                                             "（如 lxml）是另一个 Python 版本编译的，"
                                             "当前解释器无法加载"})
            if added:
                try:
                    sys.path.remove(sp)
                except ValueError:
                    pass
            for m in [k for k in list(sys.modules) if k == "docx" or k.startswith("docx.")]:
                sys.modules.pop(m, None)
    return None, None, diag


# ── 内容块构造（两种渲染器共用同一份 blocks）────────────────────────────

def _build_docx_blocks(data, consistency):
    """把台账整理成中性的内容块列表，供 python-docx / stdlib 两种渲染器共用。

    块类型：{"kind": "title"|"heading"|"para"|"table", ...}
    """
    parts = data.get("parts") or {}
    ts, at = _now()
    blocks = []
    blocks.append({"kind": "title", "text": "DSH 工程模式 · 零件数据台账"})

    # ── 1. 任务信息 ────────────────────────────────────────────────────
    blocks.append({"kind": "heading", "level": 1, "text": "一、任务信息"})
    info_rows = [
        ["任务号 task_id", data.get("task_id") or "（未关联门禁任务）"],
        ["台账版本 schema", data.get("schema") or SCHEMA],
        ["台账文件", registry_path(None)],
        ["创建时间", data.get("created_at") or "-"],
        ["最近更新", data.get("updated_at") or "-"],
        ["零件总数", str(len(parts))],
        ["房间分布", "、".join("%s(%d)" % (k, v) for k, v in
                            sorted(_room_counts(parts).items())) or "-"],
        ["邻接对总数", str(len(consistency.get("pairs") or []))],
        ["修订记录条数", str(len(data.get("revisions") or []))],
        ["文档生成时间", at],
        ["一致性结论", consistency.get("summary") or "-"],
    ]
    blocks.append({"kind": "table", "headers": ["项目", "内容"], "rows": info_rows,
                   "widths": [1.0, 3.0]})

    # ── 2. 零件总表 ────────────────────────────────────────────────────
    blocks.append({"kind": "heading", "level": 1, "text": "二、零件总表"})
    if not parts:
        blocks.append({"kind": "para", "text": "（台账为空，尚未登记任何零件）"})
    else:
        rows = []
        for nm in sorted(parts.keys()):
            rec = parts[nm]
            ifaces = _norm_interfaces(rec.get("interfaces"))[0]
            itxt = "；".join("%s[%s]%s%s" % (
                i["id"], i["type"],
                ("φ%s" % _fmt_num(i["nominal"])) if i["nominal"] is not None else "?",
                ("·" + i["tolerance"]) if i["tolerance"] else "") for i in ifaces) or "-"
            rows.append([
                nm,
                rec.get("room") or "-",
                _fmt_dims(rec.get("dims") or {}) or "-",
                rec.get("material") or "-",
                "、".join(_parse_list_arg(rec.get("adjacent"))) or "-",
                itxt,
                str(rec.get("rev") if rec.get("rev") is not None else "-"),
            ])
        blocks.append({"kind": "table",
                       "headers": ["名称", "房间", "尺寸(mm)", "材料", "邻接零件",
                                   "接口(尺寸/公差)", "版本"],
                       "rows": rows,
                       "widths": [1.1, 0.9, 1.0, 0.8, 1.3, 2.6, 0.5]})

    # ── 3. 邻接关系表 ──────────────────────────────────────────────────
    blocks.append({"kind": "heading", "level": 1, "text": "三、邻接关系表"})
    pair_rows = consistency.get("pair_rows") or []
    if not pair_rows:
        blocks.append({"kind": "para",
                       "text": "（没有任何邻接关系；请用 link_adjacent 双向登记配合对）"})
    else:
        rows = []
        for pr in pair_rows:
            a, b = pr["parts"]
            matched = pr.get("matched_interfaces") or []
            desc = "；".join("%s(%s)↔%s(%s)[%s]"
                            % (m["a_interface"], _fmt_num(m["a_nominal_mm"])
                               if m["a_nominal_mm"] is not None else "?",
                               m["b_interface"], _fmt_num(m["b_nominal_mm"])
                               if m["b_nominal_mm"] is not None else "?",
                               m["matched_by"]) for m in matched) or "-"
            bad = [m for m in matched
                   if m["a_nominal_mm"] is not None and m["b_nominal_mm"] is not None
                   and abs(m["a_nominal_mm"] - m["b_nominal_mm"]) > 1e-9]
            rows.append([a, b, desc,
                         "不一致" if bad else ("已核对" if pr.get("status") == "checked"
                                              else "无接口数据")])
        blocks.append({"kind": "table",
                       "headers": ["零件 A", "零件 B", "接口配对（A↔B）", "结论"],
                       "rows": rows, "widths": [1.0, 1.0, 4.0, 0.9]})

    # ── 4. 一致性问题清单 ──────────────────────────────────────────────
    blocks.append({"kind": "heading", "level": 1, "text": "四、一致性问题清单"})
    ms = consistency.get("mismatches") or []
    if not consistency.get("evaluated"):
        blocks.append({"kind": "para",
                       "text": "⚠ 本次【未能检查】：%s" % (consistency.get("reason") or "-")})
    if not ms:
        blocks.append({"kind": "para",
                       "text": "未发现相邻零件基本尺寸不一致。"
                               "（注意：本结论仅覆盖下方「已比对」的接口）"})
    else:
        rows = []
        for m in ms:
            rows.append(["、".join(m["parts"]), m.get("interface") or "-",
                         m.get("detail") or "-", m.get("severity") or "ERROR",
                         "是" if m.get("escalate_to_parent") else "否"])
        blocks.append({"kind": "table",
                       "headers": ["零件对", "接口", "详情", "级别", "上报父代理"],
                       "rows": rows, "widths": [1.1, 0.9, 4.2, 0.6, 0.7]})

    warn = consistency.get("warnings") or []
    if warn:
        blocks.append({"kind": "heading", "level": 2,
                       "text": "其它告警 / 数据完整性问题（%d 条）" % len(warn)})
        rows = []
        for w in warn:
            rows.append(["、".join(w.get("parts") or []) or "-",
                         w.get("kind") or "-", w.get("detail") or "-",
                         "是" if w.get("escalate_to_parent") else "否"])
        blocks.append({"kind": "table",
                       "headers": ["相关零件", "类型", "详情", "上报父代理"],
                       "rows": rows, "widths": [1.2, 1.0, 4.5, 0.7]})

    insuff = consistency.get("insufficient") or []
    if insuff:
        blocks.append({"kind": "heading", "level": 2,
                       "text": "未比对清单（缺接口数据，%d 对）" % len(insuff)})
        rows = [[a, b, i.get("reason") or "-", str(i.get("a_interface_count")),
                 str(i.get("b_interface_count"))]
                for i in insuff for a, b in [i["parts"]]]
        blocks.append({"kind": "table",
                       "headers": ["零件 A", "零件 B", "原因", "A接口数", "B接口数"],
                       "rows": rows, "widths": [1.0, 1.0, 3.5, 0.7, 0.7]})

    # ── 5. 修订历史摘要 ────────────────────────────────────────────────
    blocks.append({"kind": "heading", "level": 1, "text": "五、修订历史摘要"})
    revs = data.get("revisions") or []
    if not revs:
        blocks.append({"kind": "para", "text": "（暂无修订记录）"})
    else:
        blocks.append({"kind": "para",
                       "text": "共 %d 条修订记录，下表列出最近 %d 条（完整记录见台账 JSON 的 revisions 数组）。"
                               % (len(revs), min(60, len(revs)))})
        rows = []
        for r in revs[-60:]:
            ch = r.get("changes") or {}
            if ch:
                txt = "；".join("%s: %s → %s" % (
                    k, _short(v.get("from") if isinstance(v, dict) else v),
                    _short(v.get("to") if isinstance(v, dict) else v))
                    if isinstance(v, dict) and ("from" in v or "to" in v)
                    else "%s: %s" % (k, _short(v)) for k, v in sorted(ch.items()))
            else:
                txt = "-"
            rows.append([str(r.get("rev", "-")), r.get("action") or "-",
                         r.get("part") or "-", r.get("at") or "-", txt])
        blocks.append({"kind": "table",
                       "headers": ["版本", "动作", "零件", "时间", "改动内容"],
                       "rows": rows, "widths": [0.4, 0.6, 1.0, 1.2, 5.0]})

    blocks.append({"kind": "para",
                   "text": "—— 本文档由 engineering/tools/part_registry.py 自动生成，"
                           "内容来自 %s ——" % registry_path(None)})
    return blocks


def _room_counts(parts):
    out = {}
    for rec in (parts or {}).values():
        r = rec.get("room") or "(未填房间)"
        out[r] = out.get(r, 0) + 1
    return out


def _short(v, limit=90):
    """把任意值压成短文本（用于修订历史表格）。"""
    try:
        s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    except Exception:
        s = repr(v)
    s = str(s).replace("\n", " ")
    return s if len(s) <= limit else s[:limit - 1] + "…"


# ── 渲染器 A：python-docx（策略①②成功后使用）────────────────────────────

def _render_with_python_docx(blocks, out_path, docx_mod):
    """用 python-docx 渲染内容块。"""
    doc = docx_mod.Document()
    for blk in blocks:
        k = blk.get("kind")
        if k == "title":
            doc.add_heading(blk.get("text") or "", level=0)
        elif k == "heading":
            doc.add_heading(blk.get("text") or "", level=int(blk.get("level") or 1))
        elif k == "para":
            doc.add_paragraph(blk.get("text") or "")
        elif k == "table":
            headers = list(blk.get("headers") or [])
            rows = list(blk.get("rows") or [])
            t = doc.add_table(rows=0, cols=len(headers))
            try:
                t.style = "Table Grid"
            except Exception:
                pass
            if headers:
                cells = t.add_row().cells
                for i, h in enumerate(headers):
                    cells[i].text = str(h)
            for r in rows:
                cells = t.add_row().cells
                for i in range(len(headers)):
                    cells[i].text = _short(r[i] if i < len(r) else "", 1000)
            doc.add_paragraph("")
    doc.save(out_path)
    return True


# ── 渲染器 B：纯标准库 OOXML（策略③降级）───────────────────────────────

_XML_BAD = {c: None for c in range(0x20) if c not in (0x09, 0x0A, 0x0D)}
_XML_BAD[0x7F] = None

_OOXML_RELS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_OOXML_CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
_OOXML_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _xml_escape(s):
    """XML 文本转义 + 剔除 XML 1.0 非法控制字符（Word 会拒绝打开）。"""
    t = str(s if s is not None else "").translate(_XML_BAD)
    return (t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;").replace("'", "&apos;"))


def _w_p(text, style=None, bold=False):
    """一个 w:p 段落。"""
    ppr = ""
    if style or bold:
        ppr = "<w:pPr>"
        if style:
            ppr += '<w:pStyle w:val="%s"/>' % _xml_escape(style)
        if bold:
            ppr += "<w:rPr><w:b/></w:rPr>"
        ppr += "</w:pPr>"
    rpr = "<w:rPr><w:b/></w:rPr>" if bold else ""
    return ('<w:p>%s<w:r>%s<w:t xml:space="preserve">%s</w:t></w:r></w:p>'
            % (ppr, rpr, _xml_escape(text)))


def _w_tbl(headers, rows, widths=None):
    """一个带边框的 w:tbl 表格（总宽按 A4 正文宽度 9746 twips 分配）。"""
    total = 9746
    n = max(1, len(headers) or (len(rows[0]) if rows else 1))
    if not widths or len(widths) != n:
        widths = [1.0] * n
    s = float(sum(widths)) or 1.0
    cols = [max(400, int(total * (w / s))) for w in widths]
    # 修正舍入误差，保证 gridCol 之和 == 表宽
    cols[-1] += total - sum(cols)

    borders = ("<w:tblBorders>"
               '<w:top w:val="single" w:sz="4" w:space="0" w:color="808080"/>'
               '<w:left w:val="single" w:sz="4" w:space="0" w:color="808080"/>'
               '<w:bottom w:val="single" w:sz="4" w:space="0" w:color="808080"/>'
               '<w:right w:val="single" w:sz="4" w:space="0" w:color="808080"/>'
               '<w:insideH w:val="single" w:sz="4" w:space="0" w:color="808080"/>'
               '<w:insideV w:val="single" w:sz="4" w:space="0" w:color="808080"/>'
               "</w:tblBorders>")

    def _cell(text, w, bold):
        return ('<w:tc><w:tcPr><w:tcW w:w="%d" w:type="dxa"/>%s</w:tcPr>%s</w:tc>'
                % (w,
                   '<w:shd w:val="clear" w:color="auto" w:fill="F2F2F2"/>' if bold else "",
                   _w_p(text, bold=bold)))

    out = ['<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/>'
           '<w:tblW w:w="%d" w:type="dxa"/>%s'
           '<w:tblLayout w:type="fixed"/></w:tblPr>' % (total, borders)]
    out.append("<w:tblGrid>%s</w:tblGrid>"
               % "".join('<w:gridCol w:w="%d"/>' % w for w in cols))
    if headers:
        out.append("<w:tr>%s</w:tr>"
                   % "".join(_cell(h, cols[i], True) for i, h in enumerate(headers)))
    for r in rows:
        out.append("<w:tr>%s</w:tr>"
                   % "".join(_cell(r[i] if i < len(r) else "", cols[i], False)
                             for i in range(n)))
    out.append("</w:tbl>")
    # 表格后必须跟一个段落，否则部分阅读器会丢内容
    out.append(_w_p(""))
    return "".join(out)


_STYLES_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<w:styles xmlns:w="%s">'
    "<w:docDefaults><w:rPrDefault><w:rPr>"
    '<w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="\u5b8b\u4f53" w:cs="Calibri"/>'
    '<w:sz w:val="21"/><w:szCs w:val="21"/><w:lang w:val="en-US" w:eastAsia="zh-CN"/>'
    "</w:rPr></w:rPrDefault>"
    "<w:pPrDefault><w:pPr><w:spacing w:after=\"120\" w:line=\"276\" w:lineRule=\"auto\"/>"
    "</w:pPr></w:pPrDefault></w:docDefaults>"
    '<w:style w:type="paragraph" w:default="1" w:styleId="Normal">'
    '<w:name w:val="Normal"/><w:qFormat/></w:style>'
    '<w:style w:type="paragraph" w:styleId="Title">'
    '<w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:qFormat/>'
    '<w:pPr><w:jc w:val="center"/><w:spacing w:before="240" w:after="240"/></w:pPr>'
    '<w:rPr><w:b/><w:sz w:val="44"/><w:szCs w:val="44"/></w:rPr></w:style>'
    '<w:style w:type="paragraph" w:styleId="Heading1">'
    '<w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:qFormat/>'
    '<w:pPr><w:keepNext/><w:outlineLvl w:val="0"/>'
    '<w:spacing w:before="240" w:after="120"/></w:pPr>'
    '<w:rPr><w:b/><w:sz w:val="30"/><w:szCs w:val="30"/></w:rPr></w:style>'
    '<w:style w:type="paragraph" w:styleId="Heading2">'
    '<w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:qFormat/>'
    '<w:pPr><w:keepNext/><w:outlineLvl w:val="1"/>'
    '<w:spacing w:before="200" w:after="100"/></w:pPr>'
    '<w:rPr><w:b/><w:sz w:val="24"/><w:szCs w:val="24"/></w:rPr></w:style>'
    '<w:style w:type="table" w:styleId="TableGrid">'
    '<w:name w:val="Table Grid"/><w:qFormat/></w:style>'
    "</w:styles>"
) % _OOXML_W_NS


def _render_with_stdlib_ooxml(blocks, out_path):
    """纯标准库生成 .docx（OOXML = 一个 zip 包）。

    包内结构（Word/WPS 可打开的最小合法集）：
        [Content_Types].xml
        _rels/.rels
        word/document.xml
        word/_rels/document.xml.rels
        word/styles.xml
        docProps/core.xml
        docProps/app.xml
    """
    body = []
    for blk in blocks:
        k = blk.get("kind")
        if k == "title":
            body.append(_w_p(blk.get("text") or "", style="Title"))
        elif k == "heading":
            lvl = int(blk.get("level") or 1)
            body.append(_w_p(blk.get("text") or "",
                             style="Heading1" if lvl <= 1 else "Heading2"))
        elif k == "para":
            body.append(_w_p(blk.get("text") or ""))
        elif k == "table":
            body.append(_w_tbl(list(blk.get("headers") or []),
                               list(blk.get("rows") or []),
                               blk.get("widths")))
    body.append(_w_p(""))

    sect = ('<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
            '<w:pgMar w:top="1440" w:right="1080" w:bottom="1440" w:left="1080"'
            ' w:header="851" w:footer="992" w:gutter="0"/>'
            '<w:cols w:space="425"/><w:docGrid w:type="lines" w:linePitch="312"/>'
            "</w:sectPr>")

    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="%s"><w:body>%s%s</w:body></w:document>'
                % (_OOXML_W_NS, "".join(body), sect))

    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="%s">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
        "</Types>") % _OOXML_CT_NS

    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="%s">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
            '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
            "</Relationships>") % _OOXML_RELS_NS

    doc_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="%s">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                "</Relationships>") % _OOXML_RELS_NS

    ts, at = _now()
    iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))
    core = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<cp:coreProperties'
            ' xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"'
            ' xmlns:dc="http://purl.org/dc/elements/1.1/"'
            ' xmlns:dcterms="http://purl.org/dc/terms/"'
            ' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            "<dc:title>%s</dc:title><dc:creator>DSH part_registry.py</dc:creator>"
            "<cp:lastModifiedBy>DSH part_registry.py</cp:lastModifiedBy>"
            '<dcterms:created xsi:type="dcterms:W3CDTF">%s</dcterms:created>'
            '<dcterms:modified xsi:type="dcterms:W3CDTF">%s</dcterms:modified>'
            "</cp:coreProperties>"
            % (_xml_escape("DSH 零件数据台账"), iso, iso))

    app = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"'
           ' xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
           "<Application>DSH engineering part_registry.py</Application>"
           "<Company>DSH</Company></Properties>")

    d = os.path.dirname(os.path.abspath(out_path))
    if d:
        os.makedirs(d, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        # [Content_Types].xml 必须第一个写入（部分解析器按序嗅探）
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document)
        z.writestr("word/_rels/document.xml.rels", doc_rels)
        z.writestr("word/styles.xml", _STYLES_XML)
        z.writestr("docProps/core.xml", core)
        z.writestr("docProps/app.xml", app)
    return True


# ── 对外接口 ────────────────────────────────────────────────────────────

def export_docx(out_path, work_dir=None, strategy="auto", probe=True):
    """把台账导出为 Word 文档。

    Args:
        out_path: 目标 .docx 路径（目录不存在会自动创建）
        work_dir: 台账落点覆盖
        strategy: "auto"（默认，三级自动降级）
                  "python-docx"（强制策略①②，失败即报错）
                  "stdlib"（强制策略③纯标准库 OOXML）
        probe:    auto 模式下是否允许探测随包 site-packages（测试可关）

    Returns:
        {"ok": True, "out": ..., "strategy": "...", "size_bytes": n,
         "zip_entries": [...], "has_document_xml": True, "sections": [...],
         "diagnostics": {...}}
        失败 → {"ok": False, "error": ..., "attempts": [...]}

    ── 强制自检（代码化）────────────────────────────────────────────────
    生成后立即用 zipfile 重新打开，校验：
      · 是合法 zip；
      · 含 [Content_Types].xml 与 word/document.xml；
      · word/document.xml 非空。
    任一条不满足 → ok=false（绝不把坏文件当成功交付）。
    """
    if not out_path:
        return {"ok": False, "error": "缺少输出路径 out_path"}
    out = os.path.abspath(os.path.expanduser(str(out_path)))
    if not out.lower().endswith(".docx"):
        return {"ok": False, "error": "输出文件必须以 .docx 结尾：%s" % out}

    # 【C8 修复】只读查询：不拦截旧台账，但在结果里回显 task_id 不一致警告
    data, path, err = _load_registry(work_dir, strict_task_id=False)
    if err:
        return {"ok": False, "error": err, "file": path}

    consistency = check_adjacency_consistency(work_dir=work_dir)
    blocks = _build_docx_blocks(data, consistency)
    sections = []
    for blk in blocks:
        if blk["kind"] == "table":
            sections.append("表:%s(%d行)" % ("/".join(blk.get("headers") or []),
                                          len(blk.get("rows") or [])))
        elif blk["kind"] == "heading":
            sections.append(blk.get("text"))
        elif blk["kind"] == "title":
            sections.append(blk.get("text"))

    attempts = []
    used = None
    diag = {}

    # ── 策略①②：python-docx ───────────────────────────────────────────
    if strategy in ("auto", "python-docx"):
        docx_mod, how, diag = _try_import_docx(probe=probe)
        attempts.extend(diag.get("attempts") or [])
        if docx_mod is not None:
            try:
                _render_with_python_docx(blocks, out, docx_mod)
                used = how
            except Exception as e:
                attempts.append({"step": "render_python_docx", "ok": False,
                                 "error": "%s: %s" % (type(e).__name__, e)})
                if strategy == "python-docx":
                    return {"ok": False, "error": "python-docx 渲染失败：%r" % (e,),
                            "strategy_requested": strategy, "attempts": attempts}
        elif strategy == "python-docx":
            return {"ok": False,
                    "error": "无法导入 python-docx（已尝试直接导入与注入随包 site-packages）",
                    "strategy_requested": strategy, "attempts": attempts,
                    "probed_site_packages": diag.get("probed") or [],
                    "hint": ("本机系统 Python 未安装 python-docx；随包运行时的 "
                             "site-packages 若为其它 Python 版本编译（lxml C 扩展"
                             "ABI 不符）同样无法加载。可用 strategy='stdlib' 走"
                             "纯标准库 OOXML 导出。")}

    # ── 策略③：纯标准库 OOXML ─────────────────────────────────────────
    if used is None:
        try:
            _render_with_stdlib_ooxml(blocks, out)
            used = "stdlib-ooxml(纯标准库降级)"
            attempts.append({"step": "stdlib_ooxml", "ok": True, "out": out})
        except Exception as e:
            return {"ok": False, "error": "纯标准库 OOXML 生成失败：%r" % (e,),
                    "strategy_requested": strategy, "attempts": attempts}

    # ── 强制自检 ──────────────────────────────────────────────────────
    check = _verify_docx_file(out)
    if not check["ok"]:
        return {"ok": False, "error": "生成的 docx 自检未通过：%s" % check["error"],
                "out": out, "strategy": used, "attempts": attempts}

    size = os.path.getsize(out)
    return {
        "ok": True,
        "out": out,
        "file": path,
        "strategy": used,
        "size_bytes": size,
        "size_human": _human_size(size),
        "zip_entries": check["entries"],
        "has_document_xml": check["has_document_xml"],
        "document_xml_bytes": check["document_xml_bytes"],
        "parts_count": len(data.get("parts") or {}),
        "mismatch_count": consistency.get("mismatch_count"),
        "consistency_ok": consistency.get("ok"),
        "consistency_evaluated": consistency.get("evaluated"),
        "sections": sections,
        "attempts": attempts,
        "python_docx": diag.get("module_path"),
        "probed_site_packages": [c["site_packages"] for c in (diag.get("probed") or [])],
        "note": ("docx 本质是 zip 包；已用 zipfile 回读校验，"
                 "确保 Word/WPS 可打开。strategy 字段说明实际走了哪条路径。"),
    }


def _verify_docx_file(path):
    """回读校验 .docx：合法 zip + 必需部件齐全。返回 {ok, error, entries, ...}。"""
    try:
        if not os.path.isfile(path):
            return {"ok": False, "error": "文件未生成：%s" % path,
                    "entries": [], "has_document_xml": False,
                    "document_xml_bytes": 0}
        with zipfile.ZipFile(path, "r") as z:
            bad = z.testzip()
            if bad is not None:
                return {"ok": False, "error": "zip 内条目 CRC 校验失败：%s" % bad,
                        "entries": z.namelist(), "has_document_xml": False,
                        "document_xml_bytes": 0}
            entries = z.namelist()
            need = ["[Content_Types].xml", "word/document.xml", "_rels/.rels"]
            missing = [n for n in need if n not in entries]
            if missing:
                return {"ok": False, "error": "缺少必需部件：%s" % ", ".join(missing),
                        "entries": entries, "has_document_xml": "word/document.xml" in entries,
                        "document_xml_bytes": 0}
            doc_bytes = len(z.read("word/document.xml"))
            if doc_bytes <= 0:
                return {"ok": False, "error": "word/document.xml 为空",
                        "entries": entries, "has_document_xml": True,
                        "document_xml_bytes": 0}
            return {"ok": True, "error": None, "entries": entries,
                    "has_document_xml": True, "document_xml_bytes": doc_bytes}
    except Exception as e:
        return {"ok": False, "error": "无法作为 zip 打开：%r" % (e,),
                "entries": [], "has_document_xml": False, "document_xml_bytes": 0}


def _human_size(n):
    for unit in ("B", "KB", "MB"):
        if n < 1024 or unit == "MB":
            return ("%.1f %s" % (n, unit)) if unit != "B" else ("%d B" % n)
        n /= 1024.0
    return "%d B" % n


# ══════════════════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════════════════

# 【C8 修复】最近一次被拦下的 TASK_ID_MISMATCH（同一进程内 CLI 用）。
# _load_registry 只返回 (data, path, error) 三元组，CLI 需要完整结构（含两边
# task_id 与文件路径）才能给出可执行的报错，因此在这里留一份"最后拦截记录"，
# main() 发现结果失败且本槽非空时，把详细信息补进输出。
_LAST_TASK_ID_GATE = [None]


def _remember_gate(err):
    """记录最近一次 TASK_ID_MISMATCH（供 CLI 输出补齐字段）。"""
    try:
        _LAST_TASK_ID_GATE[0] = dict(err or {})
    except Exception:
        _LAST_TASK_ID_GATE[0] = None


def _write_fail(err, path, hint=None):
    """写路径失败的统一返回（【C8 修复】）。

    台账任务号与当前任务不一致时，_load_registry 只能返回一句 error 文本；
    这里把结构化判据（两边 task_id + 两个文件路径 + 修法）一并补进结果，
    让调用方/模型一眼看出"是台账拿错了"，而不是跑去改零件数据。
    """
    out = {"ok": False, "error": err, "file": path}
    _g = _LAST_TASK_ID_GATE[0]
    if isinstance(_g, dict) and _g.get("error") == err:
        for _k, _v in _g.items():
            out.setdefault(_k, _v)
        out["gate"] = "TASK_ID_MISMATCH"
    elif hint:
        out["hint"] = hint
    return out


def _task_id_mismatch_warning(gate):
    """把 TASK_ID_MISMATCH 压成一条"只读时可见"的警告（不抛错）。"""
    if not gate:
        return None
    return {
        "gate": "TASK_ID_MISMATCH",
        "ledger_task_id": gate.get("ledger_task_id"),
        "current_task_id": gate.get("current_task_id"),
        "registry_file": gate.get("registry_file"),
        "workflow_state_file": gate.get("workflow_state_file"),
        "message": ("⚠ 本台账不属于当前任务（台账 %s ≠ 当前 %s）—— 下面的数据"
                    "可能来自上一轮任务，不要当成当前任务的既有事实；写入已被拒绝。"
                    % (gate.get("ledger_task_id"), gate.get("current_task_id"))),
        "hint": gate.get("hint"),
    }


def _emit(obj):
    """统一 JSON 输出（ensure_ascii=False + indent=2）。

    与 mode_gate.emit 同口径：优先用 UTF-8 字节直写，绕开控制台代码页；
    编码失败才退回 ASCII 转义 —— 宁可转义，绝不留乱码。
    """
    try:
        text = json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:
        text = json.dumps({"ok": False, "error": "结果序列化失败"}, ensure_ascii=True)
    try:
        buf = getattr(sys.stdout, "buffer", None)
        if buf is not None:
            buf.write(text.encode("utf-8", "replace"))
            buf.write(b"\n")
            buf.flush()
            return
    except Exception:
        pass
    try:
        sys.stdout.write(text + "\n")
        sys.stdout.flush()
    except UnicodeEncodeError:
        try:
            sys.stdout.write(json.dumps(obj, ensure_ascii=True, indent=2) + "\n")
            sys.stdout.flush()
        except Exception:
            pass


def cmd_add(ns):
    """add 子命令：登记/更新一个零件。"""
    adj = _parse_list_arg(ns.adjacent)
    rec = {"name": ns.part, "room": ns.room, "file": ns.file}
    if ns.material is not None:
        rec["material"] = ns.material
    if ns.dims is not None:
        rec["dims"] = ns.dims
    if ns.bbox is not None:
        rec["bbox_mm"] = ns.bbox
    if adj:
        rec["adjacent"] = adj
    if ns.interface:
        ifaces = []
        for raw in ns.interface:
            try:
                ifaces.append(json.loads(raw))
            except Exception as e:
                return {"ok": False, "error": "--interface 不是合法 JSON：%r（%r）" % (raw, e)}
        rec["interfaces"] = ifaces
    if ns.credentials:
        try:
            rec["credentials"] = json.loads(ns.credentials)
        except Exception as e:
            return {"ok": False, "error": "--credentials 不是合法 JSON：%r" % (e,)}
    if ns.reported_by:
        rec["reported_by"] = ns.reported_by
    # 【Bug⑪】origin 约定：显式给了就用；没给则 add_part 内部从 .origin.json 读
    if getattr(ns, "origin", None):
        rec["origin"] = ns.origin

    res = add_part(rec, work_dir=ns.work_dir, reason=ns.reason,
                   allow_mismatch=(ns.allow_mismatch or False),
                   fields=ns.fields)
    if not res.get("ok"):
        return res
    # 建立双向邻接（add 里的 adjacent 只写单向；这里补双向，保证图完整）
    linked = []
    failed_links = []
    for other in adj:
        lr = link_adjacent(ns.part, other, work_dir=ns.work_dir,
                           reason="登记零件 %s 时按其 adjacent 字段建立双向邻接（%s ↔ %s）"
                                  % (ns.part, ns.part, other),
                           allow_mismatch=(ns.allow_mismatch or False))
        if lr.get("ok"):
            if not lr.get("already_linked"):
                linked.append(other)
        else:
            failed_links.append({"other": other, "error": lr.get("error"),
                                 "gate": lr.get("gate"),
                                 "mismatches": lr.get("mismatches")})
    out = dict(res)
    out["bidirectional_links_created"] = linked
    if failed_links:
        out["link_failures"] = failed_links
        out["warnings"] = list(out.get("warnings") or []) + \
            ["以下邻接未能双向建立：%s"
             % "、".join("%s(%s)" % (x["other"], x.get("gate") or x.get("error"))
                        for x in failed_links)]
    return out


def cmd_get(ns):
    """get 子命令：读单个零件。"""
    res = get_part(ns.part, work_dir=ns.work_dir)
    if not res.get("ok"):
        return res
    if ns.json:
        return res
    rec = res["record"]
    return {"ok": True, "part": res["part"], "file": res["file"],
            "record": rec, "brief": res["brief"]}


def cmd_list(ns):
    """list 子命令：列全部零件。"""
    res = list_parts(work_dir=ns.work_dir)
    if not res.get("ok") or ns.json:
        return res
    return {"ok": True, "file": res["file"], "task_id": res["task_id"],
            "count": res["count"], "rooms": res["rooms"], "parts": res["parts"]}


def cmd_related(ns):
    """related 子命令：按零件反查关联（BFS）。"""
    res = related_parts(ns.part, depth=ns.depth, work_dir=ns.work_dir)
    if not res.get("ok") or ns.json:
        return res
    return {"ok": True, "root": res["root"], "depth": res["depth"],
            "related": res["related"], "related_count": res["related_count"],
            "by_distance": res["by_distance"], "related_detail": res["related_detail"],
            "unreachable": res["unreachable"], "file": res["file"],
            "note": res["note"]}


def cmd_reverse(ns):
    """reverse 子命令：反查入边。"""
    return reverse_lookup(ns.part, work_dir=ns.work_dir)


def cmd_check_consistency(ns):
    """check-consistency 子命令：★ 尺寸一致性检查。"""
    return check_adjacency_consistency(work_dir=ns.work_dir)


def cmd_for_change(ns):
    """for-change 子命令：改这个零件要连带改谁。"""
    return find_related_for_change(ns.part, work_dir=ns.work_dir)


def cmd_link(ns):
    """link 子命令：双向建立邻接。"""
    return link_adjacent(ns.a, ns.b, work_dir=ns.work_dir, reason=ns.reason,
                         allow_mismatch=(ns.allow_mismatch or False))


def cmd_update(ns):
    """update 子命令：局部更新零件字段。"""
    patch = {}
    for k, v in (("room", ns.room), ("file", ns.file), ("material", ns.material),
                 ("dims", ns.dims), ("bbox_mm", ns.bbox), ("reported_by", ns.reported_by)):
        if v is not None:
            patch[k] = v
    if ns.adjacent is not None:
        patch["adjacent"] = _parse_list_arg(ns.adjacent)
    if ns.interface:
        ifaces = []
        for raw in ns.interface:
            try:
                ifaces.append(json.loads(raw))
            except Exception as e:
                return {"ok": False, "error": "--interface 不是合法 JSON：%r（%r）" % (raw, e)}
        patch["interfaces"] = ifaces
    if not patch:
        return {"ok": False, "error": "没有给出任何要更新的字段",
                "hint": "可用字段：room/file/material/dims/bbox/adjacent/interface/reported-by"}
    return update_part(ns.part, patch, work_dir=ns.work_dir, reason=ns.reason,
                       allow_mismatch=(ns.allow_mismatch or False), fields=ns.fields)


def cmd_remove(ns):
    """remove 子命令：删除零件并清理别人的邻接引用。"""
    return remove_part(ns.part, work_dir=ns.work_dir, reason=ns.reason)


def cmd_unresolved(ns):
    """unresolved 子命令：查看/关闭"经说明理由后仍保留"的已知不一致。"""
    if ns.resolve:
        return resolve_unresolved(ns.resolve, work_dir=ns.work_dir, reason=ns.reason,
                                  note=ns.note)
    return list_unresolved(work_dir=ns.work_dir)


def cmd_check_reusable(ns):
    """check-reusable 子命令：【B9】建模前置查重。"""
    return check_reusable(ns.part, dims=ns.dims, bbox_mm=ns.bbox,
                          material=ns.material, file_path=ns.file,
                          work_dir=ns.work_dir)


def cmd_audit(ns):
    """audit 子命令：★ 重算核实指纹，证明台账数据有没有被绕过模块手改。"""
    return audit_verification(name=ns.part, work_dir=ns.work_dir)


def cmd_export_docx(ns):
    """export-docx 子命令：导出 Word 台账。"""
    return export_docx(ns.out, work_dir=ns.work_dir, strategy=ns.strategy,
                       probe=(not ns.no_probe))


def build_parser():
    """构造 argparse 解析器（全部子命令）。"""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--work-dir", dest="work_dir", default=None,
                        help="台账落点覆盖（默认 <脚本目录>/load_cases/part_registry.json）")
    common.add_argument("--json", action="store_true",
                        help="输出完整明细（默认输出精简视图；两者都是 JSON）")

    # ★【强制·如实填写】所有落盘型改动共用的参数
    reason_common = argparse.ArgumentParser(add_help=False)
    reason_common.add_argument(
        "--reason", required=True,
        help="★必填：本次改动的真实说明（改了什么、为什么改）。"
             "空/占位词（\"无\"\"待定\"）会被拒绝落盘")
    reason_common.add_argument(
        "--allow-mismatch", dest="allow_mismatch", default=None, metavar="理由",
        help="核实发现不一致时默认拒绝落盘；确属设计允许时在这里【如实写明理由】才放行"
             "（会记入 unresolved 清单，不会静默消失）")

    p = argparse.ArgumentParser(
        prog="part_registry.py",
        description="DSH 工程模式 · 零件数据台账（尺寸一致性底座）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=("退出码：0=成功 / 1=操作失败或一致性未通过 / 2=命令行用法错误\n"
                "示例：\n"
                "  python part_registry.py add --part \"齿轮轴\" --room \"传动机构\" "
                "--file \"C:/x.SLDPRT\" --dims \"120x40x40\" --adjacent \"轴承座,联轴器\"\n"
                "  python part_registry.py check-consistency --json\n"))
    sub = p.add_subparsers(dest="cmd", metavar="<命令>")

    s = sub.add_parser("add", parents=[common, reason_common],
                       help="新增/更新零件（name/room/file 必填）")
    s.add_argument("--part", required=True, help="零件名")
    s.add_argument("--room", required=True, help="所属房间")
    s.add_argument("--file", required=True, help="模型文件路径")
    s.add_argument("--material", default=None, help="材料牌号")
    s.add_argument("--origin", default=None,
                   help="【Bug⑪】origin 约定（如 center_bottom / center_center / "
                        "corner_top）。不给则从零件旁 <零件>.origin.json 读取；"
                        "缺记录会给出 warning（标准约定=底面中心）")
    s.add_argument("--dims", default=None, help="关键尺寸，如 \"120x40x40\"（长x宽x高，mm）")
    s.add_argument("--bbox", default=None, help="SW 真实包围盒 6 元组 xmin,xmax,ymin,ymax,zmin,zmax")
    s.add_argument("--adjacent", default=None, help="邻接零件名，逗号分隔（会双向建立）")
    s.add_argument("--interface", action="append", default=None,
                   help="接口 JSON，可重复，如 '{\"id\":\"轴颈1\",\"type\":\"shaft\",\"nominal_mm\":25}'")
    s.add_argument("--credentials", default=None,
                   help="凭据 JSON，如 '{\"material\":\"...\",\"physics\":\"...\"}'")
    s.add_argument("--reported-by", dest="reported_by", default=None, help="上报房间")
    s.add_argument("--fields", default=None,
                   help="可选：声明本次改动字段（逗号分隔）。一旦给出必须与实际 diff 完全一致")
    s.set_defaults(func=cmd_add)

    s = sub.add_parser("get", parents=[common], help="读取单个零件")
    s.add_argument("--part", required=True)
    s.set_defaults(func=cmd_get)

    s = sub.add_parser("list", parents=[common], help="列出全部零件")
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("related", parents=[common], help="★按零件反查关联零件（BFS）")
    s.add_argument("--part", required=True)
    s.add_argument("--depth", type=int, default=1, help="遍历层数（默认 1）")
    s.set_defaults(func=cmd_related)

    s = sub.add_parser("reverse", parents=[common], help="反查：谁把我列为邻接（入边）")
    s.add_argument("--part", required=True)
    s.set_defaults(func=cmd_reverse)

    s = sub.add_parser("check-consistency", parents=[common],
                       help="★相邻零件尺寸一致性检查")
    s.set_defaults(func=cmd_check_consistency)

    s = sub.add_parser("for-change", parents=[common],
                       help="要改这个零件时，谁需要跟着改（含理由）")
    s.add_argument("--part", required=True)
    s.set_defaults(func=cmd_for_change)

    s = sub.add_parser("link", parents=[common, reason_common],
                       help="双向建立邻接关系 A↔B")
    s.add_argument("--a", required=True)
    s.add_argument("--b", required=True)
    s.set_defaults(func=cmd_link)

    s = sub.add_parser("update", parents=[common, reason_common], help="局部更新零件字段")
    s.add_argument("--part", required=True)
    s.add_argument("--room", default=None)
    s.add_argument("--file", default=None)
    s.add_argument("--material", default=None)
    s.add_argument("--dims", default=None)
    s.add_argument("--bbox", default=None)
    s.add_argument("--adjacent", default=None)
    s.add_argument("--interface", action="append", default=None)
    s.add_argument("--reported-by", dest="reported_by", default=None)
    s.add_argument("--fields", default=None,
                   help="可选：声明本次改动字段（逗号分隔）。一旦给出必须与实际 diff 完全一致")
    s.set_defaults(func=cmd_update)

    s = sub.add_parser("remove", parents=[common, reason_common],
                       help="删除零件并清理邻接引用")
    s.add_argument("--part", required=True)
    s.set_defaults(func=cmd_remove)

    s = sub.add_parser("unresolved", parents=[common],
                       help="查看/关闭「经说明理由后仍保留」的已知不一致")
    s.add_argument("--resolve", default=None, metavar="序号",
                   help="把指定序号的记录标记为已解决（需同时给 --reason）")
    s.add_argument("--reason", default=None, help="--resolve 时必填：为什么可以判定已解决")
    s.add_argument("--note", default=None, help="可选备注")
    s.set_defaults(func=cmd_unresolved)

    # ── 【B9 修复】建模前置查重：建之前先问"这件是不是已经有了" ──────────
    s = sub.add_parser("check-reusable", parents=[common],
                       help="★建模前置查重：该零件是否已建好、能否直接复用（B9）")
    s.add_argument("--part", required=True, help="零件名")
    s.add_argument("--dims", default=None, help="本次打算用的尺寸，如 \"120x40x40\"")
    s.add_argument("--bbox", default=None,
                   help="本次期望包围盒 6 元组 xmin,xmax,ymin,ymax,zmin,zmax")
    s.add_argument("--material", default=None, help="本次打算用的材料牌号")
    s.add_argument("--file", default=None, help="本次打算落盘的路径（可选）")
    s.set_defaults(func=cmd_check_reusable)

    s = sub.add_parser("audit", parents=[common],
                       help="★重算核实指纹，证明台账数据有没有被绕过模块手改")
    s.add_argument("--part", default=None, help="只审计某个零件（默认全部）")
    s.set_defaults(func=cmd_audit)

    s = sub.add_parser("export-docx", parents=[common], help="导出 Word 台账")
    s.add_argument("--out", required=True, help="输出 .docx 路径")
    s.add_argument("--strategy", default="auto",
                   choices=["auto", "python-docx", "stdlib"],
                   help="导出策略（默认 auto：python-docx → 注入随包 site-packages → 纯标准库 OOXML）")
    s.add_argument("--no-probe", action="store_true",
                   help="auto 模式下不探测随包 site-packages（直接走 stdlib 降级）")
    s.set_defaults(func=cmd_export_docx)

    return p


def main(argv=None):
    """CLI 入口。返回退出码：0=成功 / 1=失败 / 2=用法错误。"""
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.cmd:
        parser.print_help()
        return 2
    # ── 【C8 修复】每次命令开始前清空"最后拦截记录"，避免串味 ────────────
    _LAST_TASK_ID_GATE[0] = None
    try:
        result = args.func(args)
    except Exception as e:
        result = {"ok": False, "error": "命令执行异常：%s: %s" % (type(e).__name__, e),
                  "command": args.cmd}
    # ── 【C8 修复】被 TASK_ID_MISMATCH 拦下时，把完整判据补进输出 ──────────
    # 写路径只能拿到一句 error 文本；这里补上两边 task_id + 两个文件路径 + 修法，
    # 让模型/用户一眼看出"是台账拿错了"，而不是去改零件数据。
    if isinstance(result, dict) and not result.get("ok") and _LAST_TASK_ID_GATE[0]:
        _gate = _LAST_TASK_ID_GATE[0]
        for _k, _v in _gate.items():
            result.setdefault(_k, _v)
        result["gate"] = "TASK_ID_MISMATCH"
    _emit(result)
    return 0 if isinstance(result, dict) and result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
