# -*- coding: utf-8 -*-
"""
material_db.py — 工程材料数据库（纯 Python，零外部依赖）
==========================================================
涵盖常用机械材料。所有单位：MPa（弹性模量/屈服强度）、kg/m³（密度）。
材料属性来自公开工程手册，实际设计请以供应商技术数据书为准。
"""
from typing import Any

# ── 铝合金 ──────────────────────────────────────────────
AL_ALLOYS = {
    "AL_1060": {"name": "Aluminum 1060 (annealed)",
                "E_mpa": 69000, "nu": 0.33, "yield_mpa": 35, "uts_mpa": 90, "density_kg_m3": 2700},
    "AL_6061": {"name": "Aluminum 6061 (T6)",
                "E_mpa": 68900, "nu": 0.33, "yield_mpa": 276, "uts_mpa": 310, "density_kg_m3": 2700},
    "AL_6061_T6": {"name": "Aluminum 6061-T6",
                   "E_mpa": 68900, "nu": 0.33, "yield_mpa": 276, "uts_mpa": 310, "density_kg_m3": 2700},
    "AL_7075": {"name": "Aluminum 7075-T6",
                "E_mpa": 72000, "nu": 0.33, "yield_mpa": 503, "uts_mpa": 572, "density_kg_m3": 2810},
    "AL_5052": {"name": "Aluminum 5052-H32",
                "E_mpa": 70000, "nu": 0.33, "yield_mpa": 193, "uts_mpa": 228, "density_kg_m3": 2680},
}

# ── 结构钢 ──────────────────────────────────────────────
STEEL_ALLOYS = {
    "ST_API_S235": {"name": "Steel S235JR (EN 10025)",
                    "E_mpa": 210000, "nu": 0.30, "yield_mpa": 235, "uts_mpa": 360, "density_kg_m3": 7850},
    "ST_API_A36": {"name": "Steel A36 (ASTM)",
                   "E_mpa": 200000, "nu": 0.30, "yield_mpa": 250, "uts_mpa": 400, "density_kg_m3": 7850},
    "ST_API_1020": {"name": "Steel AISI 1020 (cold drawn)",
                    "E_mpa": 205000, "nu": 0.29, "yield_mpa": 350, "uts_mpa": 420, "density_kg_m3": 7850},
    "ST_API_4140": {"name": "Steel AISI 4140 (quenched & tempered)",
                    "E_mpa": 205000, "nu": 0.29, "yield_mpa": 655, "uts_mpa": 760, "density_kg_m3": 7850},
    "ST_API_4340": {"name": "Steel AISI 4340 (quenched & tempered)",
                    "E_mpa": 200000, "nu": 0.29, "yield_mpa": 745, "uts_mpa": 860, "density_kg_m3": 7850},
    "ST_STAINLESS_304": {"name": "Stainless Steel 304",
                         "E_mpa": 193000, "nu": 0.30, "yield_mpa": 205, "uts_mpa": 515, "density_kg_m3": 8000},
    "ST_STAINLESS_316": {"name": "Stainless Steel 316",
                         "E_mpa": 193000, "nu": 0.30, "yield_mpa": 170, "uts_mpa": 515, "density_kg_m3": 8000},
}

# ── 钛合金 ──────────────────────────────────────────────
TI_ALLOYS = {
    "TI_GRADE1": {"name": "Titanium Grade 1 (commercially pure)",
                  "E_mpa": 105000, "nu": 0.34, "yield_mpa": 170, "uts_mpa": 240, "density_kg_m3": 4500},
    "TI_GRADE5": {"name": "Titanium Ti-6Al-4V (Grade 5)",
                  "E_mpa": 114000, "nu": 0.34, "yield_mpa": 880, "uts_mpa": 950, "density_kg_m3": 4430},
}

# ── 工程塑料 ────────────────────────────────────────────
# 【Bug-13/29 修复】补齐 3D 打印常用材料（PETG/PLA/PA12/TPU…）。
# 原缺陷：本表只有 PC / Nylon 6-6 / ABS —— 与 swapi.COMMON_MATERIALS 同样
#   缺失 PETG/PLA/PA12，导致"3D打印"任务在 physics 侧也拿不到正确材料属性。
# 属性取自公开供应商技术数据（Prusament / Bambu / SLS 粉末规格），
# 实际设计请以所用耗材的技术数据书为准。
PLASTICS = {
    "PC_POLYCARBONATE": {"name": "Polycarbonate (PC)",
                         "E_mpa": 2400, "nu": 0.37, "yield_mpa": 65, "uts_mpa": 70, "density_kg_m3": 1200},
    "PC": {"name": "Polycarbonate (PC)",
           "E_mpa": 2400, "nu": 0.37, "yield_mpa": 65, "uts_mpa": 70, "density_kg_m3": 1200},
    "NYLON_6_6": {"name": "Nylon 6/6 (dry)",
                  "E_mpa": 2800, "nu": 0.39, "yield_mpa": 80, "uts_mpa": 85, "density_kg_m3": 1140},
    "NYLON_PA12": {"name": "Nylon PA12 (SLS/MJF)",
                   "E_mpa": 1700, "nu": 0.39, "yield_mpa": 48, "uts_mpa": 50, "density_kg_m3": 1010},
    "PA12": {"name": "Nylon PA12 (SLS/MJF)",
             "E_mpa": 1700, "nu": 0.39, "yield_mpa": 48, "uts_mpa": 50, "density_kg_m3": 1010},
    "ABS": {"name": "ABS Plastic",
            "E_mpa": 2400, "nu": 0.40, "yield_mpa": 45, "uts_mpa": 50, "density_kg_m3": 1050},
    "PETG": {"name": "PETG",
             "E_mpa": 2000, "nu": 0.37, "yield_mpa": 40, "uts_mpa": 50, "density_kg_m3": 1270},
    "PLA": {"name": "PLA",
            "E_mpa": 3500, "nu": 0.36, "yield_mpa": 55, "uts_mpa": 60, "density_kg_m3": 1240},
    "TPU": {"name": "TPU (flexible)",
            "E_mpa": 50, "nu": 0.48, "yield_mpa": 30, "uts_mpa": 40, "density_kg_m3": 1210},
    "POM": {"name": "POM (Delrin)",
            "E_mpa": 3100, "nu": 0.35, "yield_mpa": 70, "uts_mpa": 75, "density_kg_m3": 1410},
    "PEEK": {"name": "PEEK",
             "E_mpa": 3700, "nu": 0.38, "yield_mpa": 100, "uts_mpa": 110, "density_kg_m3": 1320},
}

# ── 【国标材料 · 外部数据源】GB 牌号体系 ─────────────────────────────────────
# 【重要】本文件【不再内置任何国标材料数值】。
#
# 为什么撤销内置数值：
#   国标（GB/T）是【国家统一标准】，材料力学性能必须来自标准原文或权威手册，
#   不能由代码作者"按经验填"。此前版本在源码里硬编码了一套 GB 数值，
#   这既不可审计（无法追溯来源），也有"私造标准"的风险。
#   现在改为【从外部 JSON 导入】——数据来源可查、可替换、可版本管理。
#
# 数据文件位置（按优先级查找，找到即用）：
#   ① 环境变量 DSH_GB_MATERIALS 指向的文件
#   ② <本文件目录>/gb_materials.json
#   ③ <工程模式根>/tools/gb_materials.json
#   ④ <本文件目录>/rules/gb_materials.json
#
# JSON 结构（字段名与 material_db 内部一致）：
#   {
#     "schema": "dsh-gb-materials/1",
#     "source": "数据出处：GB/T 标准号 + 版本 + 手册/供应商技术数据书",
#     "materials": {
#       "<材料ID>": {
#         "name": "材料全名（建议含标准号）",
#         "E_mpa": "<弹性模量 MPa>",
#         "nu": "<泊松比>",
#         "yield_mpa": "<屈服强度 MPa>",
#         "uts_mpa": "<抗拉强度 MPa>",
#         "density_kg_m3": "<密度 kg/m³>",
#         "standard": "<标准号与版本>",
#         "aliases": ["<图纸上可能出现的其它写法>"]
#       }
#     }
#   }
# 说明：此处【不给出任何示例数值】—— 国标数值必须由你从权威来源导入，
#   避免示例被误当成可直接使用的材料数据。
# 缺失时该表为空 —— 上层应据此报"材料未收录"，绝不猜数值。
import json as _json
import os as _os

GB_MATERIALS = {}
GB_MATERIALS_SOURCE = None      # 数据来源说明（写进报告，便于审计）
GB_MATERIALS_FILE = None        # 实际加载到的文件路径
GB_MATERIALS_ERROR = None       # 加载失败原因（供排错；None 表示正常）


def _gb_candidate_paths():
    """国标材料 JSON 的候选路径（按优先级）。"""
    _here = _os.path.dirname(_os.path.abspath(__file__))
    _cands = []
    _env = (_os.environ.get("DSH_GB_MATERIALS") or "").strip()
    if _env:
        _cands.append(_env)
    _cands.append(_os.path.join(_here, "gb_materials.json"))
    # 工程模式根（tools/gb_materials.json）：本文件在 <root>/tools/physics/
    _cands.append(_os.path.abspath(_os.path.join(_here, "..", "gb_materials.json")))
    _cands.append(_os.path.join(_here, "rules", "gb_materials.json"))
    return _cands


def _load_gb_materials():
    """从外部 JSON 载入国标材料表；失败时保持空表并记录原因。

    设计原则：**绝不内置兜底数值**。找不到数据就返回空表，
      由上层如实报"该牌号未收录"，而不是拿作者臆测的数字顶替。
    """
    global GB_MATERIALS, GB_MATERIALS_SOURCE, GB_MATERIALS_FILE, GB_MATERIALS_ERROR
    for _p in _gb_candidate_paths():
        try:
            if not _p or not _os.path.isfile(_p):
                continue
            with open(_p, "r", encoding="utf-8-sig") as _f:
                _d = _json.load(_f)
            if not isinstance(_d, dict):
                GB_MATERIALS_ERROR = "顶层不是对象: %s" % _p
                continue
            _mats = _d.get("materials")
            if not isinstance(_mats, dict) or not _mats:
                GB_MATERIALS_ERROR = "缺少 materials 段或为空: %s" % _p
                continue
            # ── 分层校验：标准身份 ≠ 力学数值 ────────────────────────────
            # 为什么分两层：用户手里先有的是【标准身份】（牌号名、标准号、
            #   官网、年份），力学数值要再去标准原文抄。若强制要求数值齐全，
            #   这批已确认的标准身份会被整体丢弃，等于白做。
            # 因此：只要有【身份信息】就收下并标记补全状态；
            #   需要数值的调用方用 _has_props()/gb_material_ready() 自查。
            _ok, _bad, _incomplete = {}, [], []
            for _k, _v in _mats.items():
                if not isinstance(_v, dict):
                    _bad.append(_k); continue
                _ident = (_v.get("name") or _v.get("name_cn")
                          or _v.get("standard"))
                if not _ident:
                    _bad.append(_k); continue
                _ok[str(_k)] = _v
                if not (_v.get("E_mpa") and _v.get("yield_mpa")
                        and _v.get("density_kg_m3")):
                    _incomplete.append(str(_k))
            if not _ok:
                GB_MATERIALS_ERROR = "materials 段无有效条目: %s" % _p
                continue
            GB_MATERIALS = _ok
            GB_MATERIALS_FILE = _p
            GB_MATERIALS_SOURCE = str(_d.get("source") or "").strip() or None
            _notes = []
            if _bad:
                _notes.append("已忽略结构不合法的条目: %s" % ", ".join(_bad))
            if _incomplete:
                _notes.append("以下 %d 个牌号【仅确认标准身份，力学数值待补全】：%s"
                              % (len(_incomplete), ", ".join(_incomplete)))
            GB_MATERIALS_ERROR = ("；".join(_notes) if _notes else None)
            return
        except Exception as _e:
            GB_MATERIALS_ERROR = "读取失败 %s: %r" % (_p, _e)
            continue
    # 没找到任何数据文件
    if GB_MATERIALS_ERROR is None:
        GB_MATERIALS_ERROR = ("未找到国标材料数据文件。请把你的 GB 材料 JSON 放到：%s "
                              "（或设置环境变量 DSH_GB_MATERIALS 指向它）"
                              % _gb_candidate_paths()[1])


_load_gb_materials()


def reload_gb_materials():
    """重新载入国标材料（导入新数据后无需重启进程）。"""
    _load_gb_materials()
    global _GB_ALIAS_INDEX
    _GB_ALIAS_INDEX = None
    return {"ok": bool(GB_MATERIALS), "count": len(GB_MATERIALS),
            "file": GB_MATERIALS_FILE, "source": GB_MATERIALS_SOURCE,
            "error": GB_MATERIALS_ERROR}


def gb_materials_status():
    """国标材料数据源状态（供 doctor / 报告展示，说明"数值从哪来"）。

    ── 【口径说明】三个计数含义不同，避免误读 ──────────────────────────
      identity_count        : 已确认【标准身份】的牌号数（有牌号/标准号即可）
      ready_count           : JSON 里【原生数值齐全】的牌号数（E+σy+ρ 都在）
      equivalent_ready_count: 原生不全、但【经 equivalent_id 兜底后可用】的
      pending_count         : 既无原生数值、也无兜底 → 真正不可用，必须补数据
    报告里同时给出这三者，下游就不会把"经兜底可用"误当成"国标数值已齐"。
    """
    # ── 【P0-B 修复·口径统一 + 分桶语义】────────────────────────────────
    # 实测缺陷：两处口径不同 ——
    #   native_ready_ids = has_props(GB_MATERIALS[k])   ← 原始 JSON 条目
    #   equivalent_ready  = has_props(get_material(k))  ← 已过一致性守卫
    #   于是 ZL104（原生字段齐、但被守卫撤回了 σy/σb）被算进 native，
    #   ready_count 虚高 1，pending 少 1。
    #
    # 修复后的三桶语义（互斥且穷尽，且都基于【全链路解析结果】）：
    #   ready_count            : 解析后可用，且【不需要兜底】——
    #                            即原始 JSON 自身字段就够（全部来自数据文件）
    #   equivalent_ready_count : 解析后可用，但【依赖 equivalent_id 兜底】补齐
    #   pending_count          : 解析后仍不可用（含被一致性守卫撤回的）
    _resolved_cache = {}
    for _k in GB_MATERIALS:
        try:
            _resolved_cache[_k] = get_material(_k) or {}
        except Exception:
            _resolved_cache[_k] = {}

    _native, _equiv = [], []
    for _k in GB_MATERIALS:
        _res = _resolved_cache.get(_k) or {}
        if not has_props(_res):
            continue                      # 解析后仍不可用 → pending
        if has_props(GB_MATERIALS.get(_k) or {}):
            _native.append(_k)            # 自身字段够用 → 不需要兜底
        else:
            _equiv.append(_k)             # 靠兜底才够用
    return {
        "loaded": bool(GB_MATERIALS),
        "count": len(GB_MATERIALS),
        "identity_count": len(GB_MATERIALS),        # 已确认标准身份
        "ready_count": len(_native),                # 原生国标数值齐全
        "equivalent_ready_count": len(_equiv),      # 经兜底【真正】可用
        "pending_count": len(GB_MATERIALS) - len(_native) - len(_equiv),
        "native_ready_ids": _native,
        "equivalent_ready_ids": _equiv,
        "file": GB_MATERIALS_FILE,
        "source": GB_MATERIALS_SOURCE,
        "error": GB_MATERIALS_ERROR,
        "searched": _gb_candidate_paths(),
    }


def has_props(entry):
    """该材料条目的【力学数值】是否齐全（能否直接用于 FEA/强度校核）。

    与"标准身份是否已确认"分开判断：身份齐 ≠ 数值齐。
    """
    if not isinstance(entry, dict):
        return False
    return bool(entry.get("E_mpa") and entry.get("yield_mpa")
                and entry.get("density_kg_m3"))


def gb_material_ready(name):
    """查询某牌号是否可用于力学计算。Returns: (ok, entry_or_None, reason)"""
    _m = get_material(name)
    if not _m:
        return False, None, "该牌号未收录"
    if has_props(_m):
        return True, _m, None
    _miss = [k for k in ("E_mpa", "nu", "yield_mpa", "uts_mpa", "density_kg_m3")
             if not _m.get(k)]
    return False, _m, ("已确认标准身份（%s），但力学数值缺失：%s —— "
                       "请从标准原文补全后再做校核"
                       % (_m.get("standard") or _m.get("name") or "?", ", ".join(_miss)))


def _build_gb_alias_index():
    """建立 GB 牌号别名 → 材料 id 的索引（大小写/符号不敏感）。"""
    idx = {}
    for _mid, _info in GB_MATERIALS.items():
        idx[_norm_key(_mid)] = _mid
        idx[_norm_key(_info.get("name", ""))] = _mid
        for _a in (_info.get("aliases") or []):
            idx[_norm_key(_a)] = _mid
    return idx


def _norm_key(s):
    """归一化材料键：剥 BOM/零宽字符、全角转半角、小写、去分隔符。"""
    _t = str(s or "")
    _t = _t.replace("\ufeff", "").replace("\u200b", "").replace("\u200e", "")
    _out = []
    for _ch in _t:
        _cp = ord(_ch)
        if _cp == 0x3000:
            _out.append(" ")
        elif 0xFF01 <= _cp <= 0xFF5E:
            _out.append(chr(_cp - 0xFEE0))
        else:
            _out.append(_ch)
    _t = "".join(_out).strip().lower()
    for _ch2 in (" ", "\t", "\u3000", "-", "_", "（", "）", "(", ")", "·", "."):
        _t = _t.replace(_ch2, "")
    return _t


_GB_ALIAS_INDEX = None


def lookup_gb(name):
    """按 GB 牌号（含别名）查材料；未收录返回 None（绝不猜数值）。"""
    global _GB_ALIAS_INDEX
    if _GB_ALIAS_INDEX is None:
        _GB_ALIAS_INDEX = _build_gb_alias_index()
    _k = _norm_key(name)
    if not _k:
        return None
    _mid = _GB_ALIAS_INDEX.get(_k)
    if not _mid or _mid not in GB_MATERIALS:
        return None
    return {"id": _mid, **GB_MATERIALS[_mid]}


# ── 合并字典 ────────────────────────────────────────────
ALL_MATERIALS: dict[str, dict[str, Any]] = {}
ALL_MATERIALS.update(AL_ALLOYS)
ALL_MATERIALS.update(STEEL_ALLOYS)
ALL_MATERIALS.update(TI_ALLOYS)
ALL_MATERIALS.update(PLASTICS)
# 【国标材料】来自外部 JSON（若未导入则为空 —— 不内置任何数值）
ALL_MATERIALS.update(GB_MATERIALS)


def _apply_equivalent(info):
    """【数值兜底 + 逐字段来源标注】国标条目缺字段时，用 equivalent_id 补齐。

    设计要点：
      · 只在【字段为空】时填充，绝不覆盖已有的国标数值；
      · 【逐字段】记录来源：哪些字段来自 GB 原文、哪些是等效兜底 ——
        因为真实情况是混合的：国标原文给了 σy/σb，但不列 E/ν
        （属材料常数），于是同一材料的数值一半来自 GB、一半来自兜底。
        整体性标记（values_from_gb 单一布尔）会掩盖这种混合，
        报告里就无法精确说明"哪个数可宣称是国标值"。
      · 无 equivalent_id 或指向不存在的 id → 保持为空（如实报待补全）。
    """
    if not isinstance(info, dict):
        return info

    _FIELDS = ("E_mpa", "nu", "yield_mpa", "uts_mpa", "density_kg_m3")
    # 该字段在导入时的来源层级（gb_data[f].source_layer）
    _gbd = info.get("gb_data") if isinstance(info.get("gb_data"), dict) else {}

    # ── 【来源层级】什么算"可宣称国标值" ──────────────────────────────────
    #   GB_ORIGINAL      国标原文（openstd 全文 PDF 文本提取）  → 可宣称
    #   GB_GRADE_DEF     国标牌号定义（如 HT200 = σb≥200）      → 可宣称
    #   GB_STIPULATED    国标规定值（转引自行业资料，非官方全文）→ 【不可宣称】
    #                    理由：数值确属标准规定，但来源是二手转引，
    #                    无法追溯到官方原文，按"不得冒充国标原文"原则
    #                    不列入可宣称集合（与 GB_SCAN 同级处理）。
    #   GB_SCAN          国标扫描页无法提取，按【手册】填        → 不可宣称
    #   GB_HARDNESS_ONLY 国标仅给硬度，不列 σy/σb                → 不可宣称
    #   HANDBOOK         手册值（E/ν/ρ 全部属此）                → 不可宣称
    # ⚠️ 实测缺陷：导入脚本对【所有有值的字段】都标了 status="value"，
    #   而旧 _is_gb 只看 status → 把手册值(E/ν/ρ)也当成"国标原文值"，
    #   于是 values_source 写出"全部字段来自国标原文"（实际 E/ν/ρ 是手册值）。
    #   现在按 source_layer 判定；无该字段的老数据回退到 status 判定。
    _GB_CLAIMABLE = ("GB_ORIGINAL", "GB_GRADE_DEF")

    def _is_gb(f):
        _e = _gbd.get(f) if isinstance(_gbd, dict) else None
        if not isinstance(_e, dict):
            return False
        if _e.get("value") is None:
            return False
        _layer = _e.get("source_layer")
        if _layer:
            return _layer in _GB_CLAIMABLE
        # 老数据（无 source_layer）：沿用 status 判定
        return _e.get("status") == "value"

    def _layer_of(f):
        _e = _gbd.get(f) if isinstance(_gbd, dict) else None
        if not isinstance(_e, dict):
            return None
        return _e.get("source_layer") or (
            "GB_ORIGINAL" if _e.get("status") == "value" else None)

    # ── 【BUG 修复·密度误标】先快照"兜底前就已经存在的字段" ───────────────
    # 实测缺陷：密度是 JSON【原生值】（来自项目原有牌号数据），但 gb_data 里
    #   没有 density 条目 → _is_gb('density_kg_m3') 恒为 False →
    #   9 个牌号的原生密度被误标成 EQUIVALENT_FALLBACK，
    #   报告里写成"density 为等效兜底（Steel S235JR）"，而实际是自带值。
    # 现在引入第三类来源 PROJECT_NATIVE（项目原有数据，既非国标原文、
    #   也非兜底），三分类互斥且穷尽。
    _native_before = {f for f in _FIELDS if info.get(f) is not None}

    _from_gb, _from_eq, _from_native = [], [], []
    for _f in _FIELDS:
        if info.get(_f) is None:
            continue
        if _is_gb(_f):
            _from_gb.append(_f)
        elif _f in _native_before:
            _from_native.append(_f)
        else:
            _from_eq.append(_f)

    _eid = info.get("equivalent_id")
    _missing = [_f for _f in _FIELDS if info.get(_f) is None]

    if _missing and _eid:
        _src = ALL_MATERIALS.get(str(_eid).upper())
        if isinstance(_src, dict):
            _filled = []
            for _f in _missing:
                _sv = _src.get(_f)
                if _sv is not None:
                    info[_f] = _sv
                    _filled.append(_f)
            if _filled:
                info["equivalent_filled"] = _filled
                info["equivalent_name"] = _src.get("name")
                _from_eq.extend(_filled)

    # ── 【跨字段一致性守卫】兜底后不得出现 σb < σy 这类物理不可能 ────────
    # 实测缺陷（真实发生）：ZL104 的 σb=150 来自 GB 原文（ZL104 F 态抗拉），
    #   而 σy 缺失 → 被等效材料 6061-T6 兜底成 276 → 得到 σb(150) < σy(276)，
    #   物理上不可能。根因是【跨材料族兜底】：铸造铝合金的屈服特性与
    #   变形铝合金完全不同，不能拿 6061-T6 的 σy 去配 ZL104 的 σb。
    # 处理：一旦出现 σb < σy，说明这组数据自相矛盾 →
    #   撤回可疑值并记录原因；宁可如实报"待补全"，也不给出矛盾数值。
    #
    # ⚠️ 【二次修复】撤回后必须【同步清理 _from_gb / _from_native】——
    #   否则台账仍认为该字段"来自国标原文"，values_source 会写出
    #   "全部字段来自国标原文" 而字段其实已为 None（自相矛盾）。
    #   实测：ZL104 被守卫撤回 σy/σb 后，values_source 仍写
    #   "GB 标准数值（全部字段来自国标原文）"。
    def _drop(fields):
        for _f in fields:
            info[_f] = None
            for _lst in (_from_gb, _from_native, _from_eq):
                while _f in _lst:
                    _lst.remove(_f)
            info["equivalent_filled"] = [
                f for f in (info.get("equivalent_filled") or []) if f != _f]

    _sy, _sb = info.get("yield_mpa"), info.get("uts_mpa")
    if _sy is not None and _sb is not None and float(_sb) < float(_sy):
        _sy_is_gb = _is_gb("yield_mpa")
        _sb_is_gb = _is_gb("uts_mpa")
        if not _sy_is_gb and _sb_is_gb:
            # 撤掉兜底来的 σy，保留国标 σb
            _drop(["yield_mpa"])
            info["consistency_guard"] = (
                "已撤回等效兜底的屈服强度：国标 σb=%.0f MPa 低于兜底 σy=%.0f MPa，"
                "物理上不可能（跨材料族兜底不成立）。屈服强度请从标准原文补全。"
                % (float(_sb), float(_sy)))
        elif _sy_is_gb and not _sb_is_gb:
            _drop(["uts_mpa"])
            info["consistency_guard"] = (
                "已撤回等效兜底的抗拉强度：国标 σy=%.0f MPa 高于兜底 σb=%.0f MPa，"
                "物理上不可能。抗拉强度请从标准原文补全。"
                % (float(_sy), float(_sb)))
        else:
            # 两者都不是"经兜底补入"的值，但仍互相矛盾 →
            #   说明【源数据本身有误】（如 ZL104：σy 是 T6 态估算 160、
            #   σb 是 F 态 150，两者状态不一致）。整组撤回并如实说明。
            _drop(["yield_mpa", "uts_mpa"])
            info["consistency_guard"] = (
                "已撤回 σy/σb：二者关系 σb(%.0f) < σy(%.0f) 物理上不可能。"
                "常见原因是【两者材料状态不一致】（例如 σy 取 T6 态、σb 取 F 态）。"
                "请按同一状态重新取值后填入。" % (float(_sb), float(_sy)))

    # ── 逐字段来源台账（供报告精确表述）────────────────────────────────
    # 来源分类互斥且穷尽：
    #   GB_STANDARD          国标原文/牌号定义值（可宣称符合国标）
    #   GB_STIPULATED        国标规定值但属【二手转引】（不可宣称官方原文）
    #   HANDBOOK             手册值（E/ν/ρ 全部属此；GB 产品标准不列这些常数）
    #   GB_SCAN              国标扫描页无法提取、按手册填的 σy/σb
    #   GB_HARDNESS_ONLY     国标只给硬度，该字段无国标值
    #   PROJECT_NATIVE       项目原有数据（既非国标、也非兜底）
    #   EQUIVALENT_FALLBACK  经 equivalent_id 从等效材料补齐
    #   MISSING              仍缺
    _LAYER_TO_SRC = {
        "GB_ORIGINAL": "GB_STANDARD",
        "GB_GRADE_DEF": "GB_STANDARD",
        # ⚠️ GB_STIPULATED 必须【单独成类】，不能并入 GB_STANDARD：
        #   40Cr/20CrMnTi 的 σy/σb 确属 GB/T 3077 规定值，但来源是
        #   Xometry/机电之家等【二手转引】，无法追溯到官方原文。
        #   按"不得冒充国标原文"原则，它可宣称"符合标准规定"，
        #   但不可宣称"来自官方原文"。若不单独成类，会被误判为 PROJECT_NATIVE
        #   （实测出现过：40Cr 的 σy=785 被显示成"原有"）。
        "GB_STIPULATED": "GB_STIPULATED",
        "GB_SCAN": "GB_SCAN",
        "GB_HARDNESS_ONLY": "GB_HARDNESS_ONLY",
        "HANDBOOK": "HANDBOOK",
    }
    _ledger = {}
    for _f in _FIELDS:
        if info.get(_f) is None:
            _ledger[_f] = {"value": None, "source": "MISSING"}
        elif _f in _from_gb:
            _ledger[_f] = {"value": info.get(_f), "source": "GB_STANDARD",
                           "layer": _layer_of(_f)}
        else:
            _lay = _layer_of(_f)
            _src = _LAYER_TO_SRC.get(_lay) if _lay else None
            if _src:
                _ledger[_f] = {"value": info.get(_f), "source": _src, "layer": _lay}
            elif _f in _from_native:
                _ledger[_f] = {"value": info.get(_f), "source": "PROJECT_NATIVE"}
            elif _f in _from_eq:
                _ledger[_f] = {"value": info.get(_f),
                               "source": "EQUIVALENT_FALLBACK",
                               "equivalent": info.get("equivalent_name")}
            else:
                _ledger[_f] = {"value": info.get(_f), "source": "UNKNOWN"}
    info["field_provenance"] = _ledger

    _still_missing = [_f for _f in _FIELDS if info.get(_f) is None]
    info["values_pending"] = bool(_still_missing)
    # values_from_gb 的语义：是否【全部已填字段】都来自国标原文/牌号定义。
    #   只要有一个字段是手册值/兜底/项目原有值 → False。
    _filled_all = [f for f in _FIELDS if info.get(f) is not None]
    info["values_from_gb"] = bool(_filled_all) and set(_filled_all) <= set(_from_gb)
    info["gb_sourced_fields"] = _from_gb
    info["native_fields"] = _from_native
    info["fallback_fields"] = _from_eq
    # 手册值字段单列（报告需明确"这些不是国标值"）
    info["handbook_fields"] = [
        f for f in _FIELDS
        if info.get(f) is not None and f not in _from_gb
        and _LAYER_TO_SRC.get(_layer_of(f)) in ("HANDBOOK", "GB_SCAN",
                                                "GB_HARDNESS_ONLY")]

    # 人类可读的来源描述（报告直接用这句）
    # ⚠️ 去重：同一字段可能同时命中"手册值"和"项目原有数据"（例如密度既是
    #   手册值、也曾在项目原有表里），必须避免在同一句里重复罗列同一字段。
    _seen = set()
    _parts = []

    def _take(fields):
        _out = [f for f in fields if f not in _seen]
        _seen.update(_out)
        return _out

    _g = _take(_from_gb)
    if _g:
        _parts.append("%s 来自国标（原文/牌号定义）" % ", ".join(_g))
    # 转引的国标规定值单独说明（可宣称"符合标准规定"，但非官方原文）
    _stip = _take([f for f in _FIELDS
                   if info.get(f) is not None
                   and _LAYER_TO_SRC.get(_layer_of(f)) == "GB_STIPULATED"])
    if _stip:
        _parts.append("%s 为 GB 标准规定值（二手转引，非官方原文提取）"
                      % ", ".join(_stip))
    _h = _take(info["handbook_fields"])
    if _h:
        _parts.append("%s 为手册值（GB 产品标准不列这些常数）" % ", ".join(_h))
    _n = _take(_from_native)
    if _n:
        _parts.append("%s 为项目原有数据" % ", ".join(_n))
    _e = _take(_from_eq)
    if _e:
        _parts.append("%s 为等效兜底（%s）"
                      % (", ".join(_e),
                         info.get("equivalent_name") or _eid or "项目原有材料"))
    _m = _take(_still_missing)
    if _m:
        _parts.append("仍缺 %s" % ", ".join(_m))
    if not _parts:
        info["values_source"] = "待补全（无兜底材料）"
    elif info["values_from_gb"]:
        info["values_source"] = "GB 标准数值（全部字段来自国标原文/牌号定义）"
    else:
        info["values_source"] = "混合来源：" + "；".join(_parts)
    return info


def get_material(material_id: str) -> dict | None:
    """通过 ID 获取材料属性；ID 不区分大小写。

    【国标材料】除标准 id 外，还接受 GB 牌号与别名
    （'Q235' / '45#' / '40Cr' / 'HT200' / 'LY12' / '304' …），
    使上游可以直接用图纸上的牌号，无需映射成欧美标。
    """
    key = material_id.upper().strip()
    _hit = ALL_MATERIALS.get(key)
    if _hit:
        # ⚠️ GB_MATERIALS 会被合并进 ALL_MATERIALS，因此这条"直接命中"分支
        #   同样要过一遍等效填充 —— 否则用 GB_xxx 这种 id 查询时会绕过兜底，
        #   表现为"明明登记了 equivalent_id 却仍然算不了"。
        if isinstance(_hit, dict) and (key in GB_MATERIALS
                                       or _hit.get("equivalent_id")):
            return _apply_equivalent(dict(_hit))
        return _hit
    # ① GB 牌号/别名索引（数值未补全时按 equivalent_id 兜底）
    _gb = lookup_gb(material_id)
    if _gb:
        _out = {k: v for k, v in _gb.items() if k != "id"}
        return _apply_equivalent(_out)
    # ② 宽松匹配（去分隔符后比较），兜住 "45 #" / "Q 235" 这类写法
    _nk = _norm_key(material_id)
    for _mid, _info in ALL_MATERIALS.items():
        if _norm_key(_mid) == _nk:
            return _info
    return None


def list_materials(category: str = "") -> list[dict]:
    """列出材料；可按类别过滤：'al' / 'steel' / 'ti' / 'plastic' / 'gb'。

    category='gb' 只列【已导入】的国标材料（来自外部 JSON；
      未导入时返回空列表 —— 不内置任何数值）。
    """
    cats = {"al": AL_ALLOYS, "steel": STEEL_ALLOYS, "ti": TI_ALLOYS,
            "plastic": PLASTICS, "gb": GB_MATERIALS}
    if category:
        c = cats.get(category.lower(), {})
        return [{"id": k, **v} for k, v in c.items()]
    return [{"id": k, **v} for k, v in ALL_MATERIALS.items()]


def material_to_json(mat: dict) -> dict:
    """将材料字典转为标准 JSON 格式（供 load_case 使用）。"""
    return {
        "id": mat.get("id", ""),
        "name": mat.get("name", ""),
        "youngs_modulus_mpa": mat["E_mpa"],
        "poissons_ratio": mat["nu"],
        "yield_strength_mpa": mat["yield_mpa"],
        "uts_mpa": mat.get("uts_mpa", 0),
        "density_kg_m3": mat["density_kg_m3"],
        "source": "material_db",
    }


def resolve_material(load_case: dict) -> dict:
    """从 load_case 解析材料，缺失时尝试从 id 匹配，仍缺则返回错误。"""
    mat = load_case.get("material", {})
    if mat and mat.get("youngs_modulus_mpa"):
        return {"ok": True, "material": mat}
    mat_id = load_case.get("material", {}).get("id", "")
    if mat_id:
        found = get_material(mat_id)
        if found:
            return {"ok": True, "material": material_to_json({"id": mat_id, **found})}
    return {"ok": False, "error": f"material not found: {mat_id!r}; check available IDs via material_db.list_materials()"}
