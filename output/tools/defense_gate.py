#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
defense_gate.py — 工程模式【三大防线】统一强制实现
====================================================
本模块把原先"只写在提示词里、靠模型自觉"的三道质量防线，
变成【代码级、到点自动执行、不通过就不放行】的硬门禁。

三大防线
--------
① 材料防线（Material Defense）
   每一个落盘的 .sldprt 必须带一份【材料凭据】(<part>.material.json)，
   证明它的材料不是缺失值、不是密度 1000(水)、不是跨族静默替代。
   凭据由 swapi.save() 在 SolidWorks 进程内写就（那里才有真实密度）。

② 物理防线（Physics / FEA Defense）
   房间必须产出一份【物理校核凭据】(reports/<room>.physics.json)，
   综合 FEA gates 与 overall 判定；overall == FAIL 即视为未通过。

③ 领域防线（DSVA / GB 规则 Defense）
   房间必须产出一份【领域校验凭据】(reports/<room>.domain.json)，
   由 domain_validator 按 rules/<domain>_rules.json 判定；
   存在 CRITICAL 违规即视为未通过。

强制点（"AI 到了这些步骤就必须过"）
----------------------------------
  · sw_bridge.py run           产物落盘即校验防线①（材料）
  · mode_gate.py room-end      下线即校验①②③（该房间）
  · workflow_gate confirm-assembly  开总装前校验全部建模房间①②③
  · workflow_gate select(收尾)      宣告完成前校验全任务①②③

绕过
----
唯一合法绕行：DSH_DEFENSE_BYPASS=1 环境变量，或命令的 --force。
绕行【必定留痕】到 reports/defense_bypass.json，便于事后审计（绝不静默）。

CLI
---
  python defense_gate.py check-room <房间名>
  python defense_gate.py check-task
  python defense_gate.py status
"""
import glob
import json
import os
import sys
import time
import urllib.error
import urllib.request

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ── 【P0-3 写侧修复】凭据落点必须与【宿主读取处】一致 ────────────────────
# 原实现把 reports/ 与 artifacts_registry.json 写到本脚本所在目录。
# 但工程模式有两份副本：从工作区跑 physics/domain 校核时，凭据被写进
# 【工作区】的 reports/，而宿主（及其守卫、/defense/judge）读的是
# 【安装副本】的 reports/ —— 于是：
#     · 工作区：有 结构件.physics.json / domain.json
#     · 安装副本：一个都没有
#   宿主判定恒为"缺少凭据"→ 防线 fail-closed；用户还以为校核跑过了。
# 修复：统一由 _store.state_dir() 决定落点（与 mode_gate/workflow_gate 同一来源）。
try:
    import _store as _store_mod
    STATE_DIR = _store_mod.state_dir()
except Exception:
    STATE_DIR = _BASE_DIR
REPORTS_DIR = os.path.join(STATE_DIR, "reports")
ARTIFACT_REGISTRY_FILE = os.path.join(STATE_DIR, "artifacts_registry.json")
BYPASS_LOG_FILE = os.path.join(REPORTS_DIR, "defense_bypass.json")

# 需要物理/领域校核的【建模类】房间类型（与 workflow_gate.WAVE1_TYPES 对齐）
MODELING_ROOM_TYPES = ("structural", "transmission", "housing",
                       "support", "spring", "thermal", "corrosion")

# 房间类型 → DSVA 领域 ID
TYPE_TO_DOMAIN = {
    "structural": "structural",
    "support": "structural",
    "spring": "structural",
    "thermal": "structural",
    "corrosion": "structural",
    "transmission": "transmission",
    "housing": "housing",
    "assembly": "structural",
    "drafting": "structural",
}

# ── 材料家族判据（与 swapi._material_family 同源；此处本地实现，
#    避免门禁脚本依赖 pywin32/SolidWorks 才能运行）───────────────────
_FAMILY_BY_KEYWORD = (
    ("6061", "aluminum"), ("6063", "aluminum"), ("7075", "aluminum"),
    ("2024", "aluminum"), ("5052", "aluminum"), ("铝", "aluminum"),
    ("aluminum", "aluminum"), ("aluminium", "aluminum"), ("alsi", "aluminum"),
    ("ti-6al-4v", "titanium"), ("tc4", "titanium"), ("钛", "titanium"),
    ("titanium", "titanium"),
    ("q235", "steel"), ("45#", "steel"), ("40cr", "steel"), ("cr12mov", "steel"),
    ("1020", "steel"), ("1045", "steel"), ("4140", "steel"), ("4340", "steel"),
    ("abs", "plastic"), ("nylon", "plastic"), ("pom", "plastic"),
    ("polycarbonate", "plastic"), ("塑料", "plastic"),
    ("copper", "copper"), ("brass", "copper"), ("bronze", "copper"), ("铜", "copper"),
)
_FAMILY_BY_DENSITY = (
    (2400, 2900, "aluminum"),
    (4300, 4700, "titanium"),
    (7400, 8100, "steel"),
    (8300, 9000, "copper"),
    # ── 【MA-03 修复】plastic 区间排除"水"窄带（990~1010）──────────────
    # 水密度是 SW 未赋材的默认值，绝不能当工程塑料放行；
    # material_family() 会先把该窄带判为 "water"。
    (900, 989, "plastic"),
    (1011, 2000, "plastic"),
)


def material_family(name_or_density):
    """判定材料家族；无法判定返回 None（调用方应视为"未知，不阻断"）。

    ── 【MA-03 修复】密度 1000（水）不得判为 plastic ─────────────────────
    实测缺陷：material_family(1000) → "plastic"，于是 SolidWorks **未赋材**
      的默认密度（1000 = 水）被当成"合法的工程塑料"，
      可能让未赋材零件通过家族校验。
    修复：密度落在水的窄带（990~1010）时返回 "water"，
      并在 _FAMILY_BY_DENSITY 的 plastic 区间里排除该窄带 ——
      "水"不是工程塑料，它是一个明确的错误信号。
    """
    if name_or_density is None:
        return None
    if isinstance(name_or_density, bool):
        return None
    if isinstance(name_or_density, (int, float)):
        d = float(name_or_density)
        # ① 水密度窄带优先判定（SW 未赋材的默认值）
        if 990.0 <= d <= 1010.0:
            return "water"
        # ② 其余按区间判定（plastic 区间已排除水窄带）
        for lo, hi, fam in _FAMILY_BY_DENSITY:
            if lo <= d <= hi:
                return fam
        return None
    s = str(name_or_density).strip().lower()
    if not s:
        return None
    for kw, fam in _FAMILY_BY_KEYWORD:
        if kw in s:
            return fam
    return None


# ── 【Bug-04】关键材料属性表（供跨材质替代判定）────────────────────────────
# 为什么在本模块自带一份：defense_gate 必须能独立运行（不能 import swapi ——
#   那会拉起 pywin32/SolidWorks 依赖），而跨材质替代判定需要屈服/密度属性。
#
# ── 【国标材料 · 外部数据源】本表【不再包含任何 GB/T 牌号数值】────────────
#   GB/T 是国家统一标准，材料性能必须来自标准原文/权威手册，不得由代码作者
#   填写。因此国标牌号的属性一律经 _gb_props_from_json() 从外部
#   gb_materials.json 读取；本表只保留【非国标】的通用条目
#   （工程塑料 + 通用牌号），它们不涉及"国标"名义。
#   未导入国标数据时：国标牌号查不到 → 判定降级为"需人工确认"，绝不猜数值。
_MATERIAL_PROPS = {
    # ── 工程塑料（非国标强制牌号，属性取自公开供应商技术数据）──────────
    "petg": {"yield_mpa": 40, "density": 1270, "uts_mpa": 50},
    "pet": {"yield_mpa": 55, "density": 1420, "uts_mpa": 75},
    "pla": {"yield_mpa": 55, "density": 1240, "uts_mpa": 60},
    "abs": {"yield_mpa": 45, "density": 1050, "uts_mpa": 50},
    "asa": {"yield_mpa": 45, "density": 1070, "uts_mpa": 50},
    "nylon": {"yield_mpa": 80, "density": 1140, "uts_mpa": 85},
    "pa12": {"yield_mpa": 48, "density": 1010, "uts_mpa": 50},
    "tpu": {"yield_mpa": 30, "density": 1210, "uts_mpa": 40},
    "pc": {"yield_mpa": 65, "density": 1200, "uts_mpa": 70},
    "pom": {"yield_mpa": 70, "density": 1410, "uts_mpa": 75},
    "peek": {"yield_mpa": 100, "density": 1320, "uts_mpa": 110},
}


def _gb_props_from_json(name):
    """【国标材料 · 外部数据源】从导入的 JSON 取牌号属性。

    与 physics/material_db 共用同一份 gb_materials.json（单一数据源），
    使"材料库"与"防线判定"永远看到同一组数值。
    Returns: {yield_mpa, density, uts_mpa} 或 None（未收录 → 不猜数值）。
    """
    _n = str(name or "").strip()
    if not _n:
        return None
    try:
        import sys as _sys
        _pd = os.path.join(_BASE_DIR, "physics")
        if _pd not in _sys.path:
            _sys.path.insert(0, _pd)
        import material_db as _mdb
        _m = _mdb.get_material(_n)
        if not _m:
            # 再试 GB_ 前缀 id（JSON 里常用 GB_Q235 这类键）
            _gid = "GB_" + _n.replace("-", "_").replace("#", "").upper()
            _m = _mdb.get_material(_gid)
        if _m and _m.get("yield_mpa"):
            return {
                "yield_mpa": _m.get("yield_mpa"),
                "density": _m.get("density_kg_m3"),
                "uts_mpa": _m.get("uts_mpa"),
                "standard": _m.get("standard"),
                "source": "gb_materials.json（外部导入）",
            }
    except Exception:
        pass
    return None

# 跨材质替代的判定阈值：屈服强度或密度差异超过此比例即判"不可接受替代"
#
# ── 【MA-06 修复】双层容差口径必须明确分工，不能各判各的 ────────────────
# 实测矛盾：swapi 层用 5%（MATCH_TOL）判"名称匹配是否可信"，
#   defense_gate 层用 10% 判"属性替代是否可接受"；
#   于是 PETG→PET（密度差 11.8%）出现"sub_block=True 但 mismatch_rej=False"
#   的表面矛盾（两层各自正确，但没人说清谁负责什么）。
# 明确分工（两者不是同一个判据，本就不该相等）：
#   · swapi 侧 5%  ——【写入期】判断"SW 是否真的写成了我要的材料"，
#                     偏严，用于尽早发现近似替换并告警；
#   · gate 侧 10%  ——【校验期】判断"已生效的材料替代能否放行"，
#                     偏宽，留给同族等价写法（Q235↔S235JR 等）余地。
# 两层都保留，但命中任一层即视为"属性级不可接受"，不再出现口径分裂观感。
_SUBSTITUTION_TOL_PCT = 10.0
# ── 【Bug-C 运行时修复】密度实测差异容差 ────────────────────────────────
# 请求 PETG(1270) 而 SW 实得 PET(1420) → 差 11.8%，必须拦截。
# 同族材料（如 6061 vs 6061-T6）密度基本相同，不会误伤。
_DENSITY_MISMATCH_TOL = 0.05


def _material_props_lookup(name):
    """按材料名解析完整属性（physics material_db → 内置最小表）。

    Returns: (ok, info, reason)
    """
    _nm = str(name or "").strip()
    if not _nm:
        return False, None, "材料名为空"
    try:
        import sys as _sys
        _pd = os.path.join(_BASE_DIR, "physics")
        if _pd not in _sys.path:
            _sys.path.insert(0, _pd)
        import material_db as _mdb
        _m = _mdb.get_material(_nm)
        if _m:
            return True, {
                "name": _m.get("name") or _nm,
                "yield_mpa": _m.get("yield_mpa"),
                "density_kg_m3": _m.get("density_kg_m3"),
                "uts_mpa": _m.get("uts_mpa"),
                "standard": _m.get("standard"),
            }, "material_db"
    except Exception:
        pass
    return False, None, "材料库不可用或未收录"


def _material_props_any(name):
    """查关键属性：内置最小表 → custom_materials.json → 【material_db 兜底】。

    ── 【MA-02 修复】必须 fallback 到 material_db ────────────────────────
    实测缺陷：内置表缺 Q355 / 20CrMnTi / GCr15 / 65Mn / QT500-7 / 2A12 /
      ZL104 共 7 个国标牌号，而这些牌号在 physics/material_db 里【是有的】。
      原实现只查内置表 + custom_materials，查不到就返回 None →
      跨材质替代判定降级为"无法对比"，只能 needs_review（过度保守），
      国标牌号之间的替代差异（如 Q235→45# 屈服差 51%）反而判不出来。
    现在末尾接【外部导入的国标材料 JSON】（与 material_db 同源），
      把属性对比能力补齐；国标数值不在本文件写死。
    """
    # ① 输入归一化（MA-05：剥 BOM、全角转半角）
    _raw = str(name or "")
    _k = _norm_material_key(_raw)
    if not _k:
        return None
    # ② 内置最小表（键已归一化，这里逐项归一化比对，容忍 BOM/全角）
    for _mk, _mv in _MATERIAL_PROPS.items():
        if _norm_material_key(_mk) == _k:
            return dict(_mv)
    # ③ 用户自定义材料
    try:
        _cm = _read_json(os.path.join(_BASE_DIR, "custom_materials.json")) or {}
        for _ck, _cv in _cm.items():
            if _norm_material_key(_ck) == _k and isinstance(_cv, dict):
                return {
                    "yield_mpa": _cv.get("yield_mpa"),
                    "density": _cv.get("density_kg_m3") or _cv.get("density"),
                    "uts_mpa": _cv.get("uts_mpa"),
                }
    except Exception:
        pass
    # ④ 【国标材料 · 外部数据源】从导入的 gb_materials.json 取属性
    #    数值不再写死在本模块 —— GB/T 是国家标准，必须来自权威数据源。
    #    未导入时返回 None → 判定降级为"需人工确认"，绝不猜数值。
    _gbp = _gb_props_from_json(_raw)
    if _gbp:
        return _gbp
    # ⑤ 【MA-02】physics/material_db（通用牌号；其国标部分同样来自外部 JSON）
    try:
        _ok, _info, _ = _material_props_lookup(_raw)
        if _ok and _info:
            return {
                "yield_mpa": _info.get("yield_mpa"),
                "density": _info.get("density_kg_m3"),
                "uts_mpa": _info.get("uts_mpa"),
            }
    except Exception:
        pass
    return None


def _norm_material_key(s):
    """【MA-05 修复】材料名归一化：剥 BOM/零宽字符、全角转半角、小写去分隔符。

    实测：`\\ufeffQ235` 与 `Ｑ２３５`（全角）会因字符形态差异被判"查不到"，
      属 fail-closed 的安全方向，但会误伤合法输入、也让排障困惑。
    """
    import re as _re
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
    return _re.sub(r"[\s\-_（）()·.]+", "", _t)


def material_substitution_check(requested, applied, attested_density=None):
    """【Bug-04 修复】判定"请求材料 → 实际生效材料"是否为不可接受的替代。

    背景：PETG（ρ1270/σy40）被 SW 近似成 PET（ρ1420/σy55）时，
      原实现仅凭 approximate_match=True 给 WARN 放行 —— 二者屈服差约 37%，
      属跨材料静默替代，本应拦截。同族（都属 plastic）不足以作为放行理由，
      必须做【属性级】对比。

    Returns: {blocking: bool, reason: str, requested, applied,
              req_props, app_props, deltas{pct}}
      · blocking=True  → 防线①应判不通过（硬阻断）
      · blocking=False → 允许，但 reason 可能带说明（差异在容差内/无法判定）
    """
    _rq = str(requested or "").strip()
    _ap = str(applied or "").strip()
    _out = {"blocking": False, "reason": "", "requested": _rq, "applied": _ap,
            "req_props": None, "app_props": None, "deltas": {}}
    if not _rq or not _ap or _rq.lower() == _ap.lower():
        return _out
    _rp = _material_props_any(_rq)
    _apx = _material_props_any(_ap)
    _out["req_props"] = _rp
    _out["app_props"] = _apx

    def _usable(_d):
        """属性是否【足以支撑比较】：必须有屈服强度。

        踩坑（真实缺陷）：GCr15 这类"只有标准身份、力学数值待补全"的牌号，
          _material_props_any 会返回一个【部分填充】的 dict（例如只有密度）。
          旧逻辑只判 `if not _rp or not _apx`，这种"非空但不完整"的返回值
          会绕过守卫；随后两个差值都算不出来（None），于是落入 else 分支
          输出"差异在容差内，已放行" —— 等于【拿不到数据却当成通过】，
          是危险误放行。现在改为：缺屈服强度即视为不可比较。
        """
        return bool(isinstance(_d, dict) and _d.get("yield_mpa"))

    if not _usable(_rp) or not _usable(_apx):
        # 属性不全 → 【不静默放行】：标记需人工确认（fail-closed 到"待复核"）
        _out["blocking"] = False
        _out["needs_review"] = True
        _out["degraded"] = True
        _out["degraded_reason_code"] = "MATERIAL_PROPS_INCOMPLETE"
        _miss = []
        for _tag, _d in (("请求", _rp), ("实际", _apx)):
            if not _usable(_d):
                _miss.append("%s材料 %s" % (
                    _tag, "属性未收录（可能仅有标准身份，力学数值待补全）"
                    if _d else "查不到属性"))
        _out["reason"] = (
            "材料被替换（请求 %s → 实际 %s），但%s —— 无法做属性级对比；"
            "请人工确认该替代是否可接受（跨材质替代不得静默放行）。"
            % (_rq, _ap, "、".join(_miss)))
        return _out

    def _pct(_a, _b):
        try:
            _a, _b = float(_a), float(_b)
            if not _b:
                return None
            return abs(_a - _b) / abs(_b) * 100.0
        except Exception:
            return None

    _dy = _pct(_apx.get("yield_mpa"), _rp.get("yield_mpa"))
    _dd = _pct(_apx.get("density"), _rp.get("density"))
    _out["deltas"] = {"yield_pct": _dy, "density_pct": _dd}
    _bad = []
    if _dy is not None and _dy > _SUBSTITUTION_TOL_PCT:
        _bad.append("屈服强度 %s→%s MPa（差 %.1f%%）"
                    % (_rp.get("yield_mpa"), _apx.get("yield_mpa"), _dy))
    if _dd is not None and _dd > _SUBSTITUTION_TOL_PCT:
        _bad.append("密度 %s→%s kg/m³（差 %.1f%%）"
                    % (_rp.get("density"), _apx.get("density"), _dd))
    if _bad:
        _out["blocking"] = True
        _out["reason"] = (
            "跨材质静默替代：请求材料 %s，实际生效 %s，关键属性差异超容差(%.0f%%)—— %s。"
            "不得以 approximate_match 放行；请改用材料库中的正确牌号，"
            "或用 set_custom_material() 显式登记后重做。"
            % (_rq, _ap, _SUBSTITUTION_TOL_PCT, "；".join(_bad)))
    else:
        _out["reason"] = ("材料被替换（%s → %s），关键属性差异在容差(%.0f%%)内，已放行但留痕"
                          % (_rq, _ap, _SUBSTITUTION_TOL_PCT))
    return _out


def _read_json(path):
    """稳健读取 JSON（utf-8-sig → utf-8 → gbk），失败返回 None。"""
    if not path or not os.path.exists(path):
        return None
    for enc in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                return json.load(f)
        except Exception:
            continue
    return None


def _write_json(path, data):
    """【Bug-06 修复】原子写入 JSON，杜绝守卫读到"半截文件"。

    原实现直接 open(w) 写入：守卫每 5 秒巡检 reports/ 下的凭据，
      若恰好在写入过程中读取，就会拿到【截断的 JSON】→ 解析失败 →
      误判 credential-unreadable / signature-invalid → 把一份本来合法的
      凭据隔离掉（实测：隔离区累计 19 个文件，部分即由此产生）。
    修复：先写同目录临时文件并 fsync，再用 os.replace 原子替换
      （同盘 replace 在 Windows/POSIX 均为原子操作），守卫永远只能看到
      "完整旧版"或"完整新版"。
    """
    try:
        import tempfile as _tf
        _dir = os.path.dirname(path) or "."
        os.makedirs(_dir, exist_ok=True)
        _fd, _tmp = _tf.mkstemp(prefix=".tmp-", suffix=".json", dir=_dir)
        try:
            with os.fdopen(_fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except Exception:
                    pass
            os.replace(_tmp, path)
        except Exception:
            try:
                if os.path.exists(_tmp):
                    os.remove(_tmp)
            except Exception:
                pass
            raise
        return True
    except Exception:
        # 原子写入失败 → 退回直接写入（宁可写成功也不要丢凭据）
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except Exception:
            return False


# ══════════════════════════════════════════════════════════════════════
#  防线 ① 材料
# ══════════════════════════════════════════════════════════════════════

def _real_case_path(path):
    """【Bug-09 修复】返回磁盘上【真实大小写】的路径（找不到则原样返回）。

    实测缺陷：`save()` 时传入的路径可能是小写 `.sldprt`，而 SolidWorks
      保存后磁盘上的真实文件是 `.SLDPRT`。凭据名变成
      `xxx.sldprt.material.json`，与零件 `xxx.SLDPRT` 的基名不一致 ——
      下游按"零件路径 + .material.json"查找时会 MISS，表现为"凭据丢失"。
    修复：逐级用目录列举做 casefold 比对，命中即返回真实大小写路径，
      使凭据名与零件名严格同基名。
    ⚠️ 关键：Windows 下 os.path.exists() 【大小写不敏感】，因此不能靠
      "exists 就返回原样" 短路 —— 必须无条件逐级还原真实大小写。
    """
    try:
        p = str(path)
        _abs = os.path.abspath(p)
        if not os.path.exists(_abs):
            return p
        _drive, _tail = os.path.splitdrive(_abs)
        _segs = [s for s in _tail.split(os.sep) if s]
        _cur = _drive + os.sep if _drive else os.sep
        for _seg in _segs:
            # 无条件在父目录里按 casefold 找真实名（不信任 exists 的短路）
            _real_seg = None
            try:
                if os.path.isdir(_cur):
                    _want = _seg.casefold()
                    for _e in os.listdir(_cur):
                        if _e.casefold() == _want:
                            _real_seg = _e
                            break
            except Exception:
                _real_seg = None
            _cur = os.path.join(_cur, _real_seg if _real_seg else _seg)
        return _cur if os.path.exists(_cur) else p
    except Exception:
        return str(path)


def attestation_path(part_path):
    """材料凭据文件路径：<part>.SLDPRT → <part>.SLDPRT.material.json

    ── 【Bug-09 修复】用磁盘【真实大小写】的零件路径拼凭据名 ──────────────
    原实现直接 str(part_path) 拼接：若 save() 传的是 `xxx.sldprt` 而磁盘是
      `xxx.SLDPRT`，凭据会写成 `xxx.sldprt.material.json`，
      与零件基名不一致 → 查找 MISS，被误判为"凭据未落盘"。

    ── 【P2 修复】必须校验路径边界 ────────────────────────────────────────
    实测（子代理3）：传 `../x`、`Z:`、`CON`、UNC 路径都被【原样返回】，
      调用方随后会按这个路径读写 —— 等于给了一条越界写入的通道。
      现在是纯函数式校验：命中可疑形态时【规范化到安全名】并记录原因，
      绝不把危险路径原样交出去。
    """
    _p = str(part_path or "")
    _why = _unsafe_path_reason(_p)
    if _why:
        # 危险路径：退回"仅用文件名"（去掉任何目录成分与保留名）
        _base = os.path.basename(_p.replace("\\", "/").rstrip("/")) or "part"
        _safe = "".join(c for c in _base if c.isalnum() or c in "._- ").strip()
        if not _safe:
            _safe = "part.SLDPRT"
        _fallback = os.path.join(os.path.abspath(os.getcwd()), _safe)
        try:
            # 记录一次越界尝试，便于审计（绝不静默）
            note_unsigned_attempt(_fallback, "material",
                                  "凭据路径越界被规范化: %r (%s)" % (_p, _why))
        except Exception:
            pass
        return _fallback + ".material.json"
    return _real_case_path(part_path) + ".material.json"


def _unsafe_path_reason(path):
    """【P2 修复】判定路径是否越界/危险；安全返回 None。

    拦截的形态：
      · 空路径；
      · 目录穿越（.. 成分）；
      · 非本地盘符（UNC \\\\server\\share、设备路径 \\\\?\\）；
      · Windows 保留设备名（CON/PRN/AUX/NUL/COM1-9/LPT1-9）；
      · 含 NUL 等控制字符。
    注意：绝对路径本身【不】判危险（零件就在绝对路径上），
      只判"能逃出预期目录"或"操作系统特殊语义"的形态。
    """
    if not path or not str(path).strip():
        return "空路径"
    _p = str(path)
    if "\x00" in _p or any(ord(c) < 32 for c in _p):
        return "含控制字符"
    _n = _p.replace("/", "\\")
    # UNC / 设备路径
    if _n.startswith("\\\\"):
        return "UNC 或设备路径"
    # 目录穿越
    _parts = [x for x in _n.split("\\") if x not in ("", ".")]
    if ".." in _parts:
        return "含目录穿越成分(..)"
    # Windows 保留设备名（任意一段命中即危险）
    _RESERVED = {"con", "prn", "aux", "nul"}
    for _seg in _parts:
        _stem = _seg.split(".")[0].strip().lower()
        if _stem in _RESERVED:
            return "Windows 保留设备名 %s" % _seg
        if len(_stem) == 4 and _stem[:3] in ("com", "lpt") and _stem[3] in "123456789":
            return "Windows 保留设备名 %s" % _seg
    # 裸盘符（"Z:" 这种无后续路径的）
    if len(_n) == 2 and _n[1] == ":":
        return "裸盘符无路径"
    return None


def write_material_attestation(part_path, info):
    """由 swapi.save() 在 SolidWorks 进程内调用：固化材料事实。

    info 期望字段（缺省容忍）：applied_name / material / density_kg_m3 /
      density_before_kg_m3 / source / ok / approximate_match /
      rejected_candidates。

    ── 【签后补字段 BUG 修复】info 可能是一份【宿主已签名的凭据】。 ─────────
    原实现无条件 rec.update(info) 并自行追加 part_size_bytes / part_md5 /
    attested_* 等字段 —— 对已签名凭据而言，这等于在签名之后改动凭据体，
    HMAC 必然失配，防线①因此永远验签失败。
    现在：已签名凭据【原样落盘】（这些字段已由宿主在签发时纳入待签体，
    且 part_md5/part_size 由宿主自己读盘算出）；只有未签名兜底凭据才由
    本函数补齐本地可观测字段。
    """
    _signed = bool((info or {}).get("_sig"))
    if _signed:
        rec = dict(info)
        _write_json(attestation_path(part_path), rec)
        return rec

    rec = {
        "schema": "dsh-material-attestation/1",
        "part": os.path.abspath(str(part_path)),
        "part_name": os.path.splitext(os.path.basename(str(part_path)))[0],
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "ts": time.time(),
    }
    rec.update(info or {})
    # ── [防伪造] 由本函数【自己】计算零件 size + md5，不依赖调用方传入 ──
    #   否则调用方漏传就让"哈希绑定"静默失效（实测 swapi 传了、其它调用方没传，
    #   导致合法零件反而被判缺 part_md5）。这里统一兜底，保证原子性。
    try:
        _pp = str(part_path)
        if os.path.exists(_pp):
            rec["part_size_bytes"] = os.path.getsize(_pp)
            import hashlib as _hl
            _h = _hl.md5()
            with open(_pp, "rb") as _pf:
                for _chunk in iter(lambda: _pf.read(1 << 20), b""):
                    _h.update(_chunk)
            rec["part_md5"] = _h.hexdigest()
    except Exception:
        pass
    # 归一化：材料名 + 密度 + 家族
    _name = (rec.get("applied_name") or rec.get("material")
             or rec.get("requested_material"))
    _dens = rec.get("density_kg_m3")
    try:
        _dens = float(_dens) if _dens is not None else None
    except Exception:
        _dens = None
    rec["attested_material"] = _name
    rec["attested_density_kg_m3"] = _dens
    rec["attested_family"] = material_family(_name) or material_family(_dens)
    _write_json(attestation_path(part_path), rec)
    return rec


def check_material_attestation(part_path):
    """校验单个零件的材料凭据。

    Returns: {ok, level, reasons[], attestation?}
      ok=False → 该零件材料不可信（硬阻断）
    """
    out = {"ok": False, "level": "BLOCK", "reasons": [], "part": str(part_path)}
    # ── 【Bug-09 修复】零件与凭据都要按【磁盘真实大小写】解析 ──────────────
    # 大小写不一致（.sldprt vs .SLDPRT）会让凭据查找 MISS，被误判为
    #   "未落盘"。这里先归一化到真实路径，再据此找凭据。
    _real_part = _real_case_path(part_path)
    out["part"] = _real_part
    if not os.path.exists(str(_real_part)):
        out["reasons"].append("零件文件不存在: %s" % part_path)
        return out
    att = _read_json(attestation_path(_real_part))
    if not att:
        # 二次兜底：目录内按 casefold 找同名凭据（应对历史遗留的大小写混乱）
        try:
            _d, _n = os.path.split(_real_part)
            _want = (_n + ".material.json").casefold()
            for _e in os.listdir(_d):
                if _e.casefold() == _want:
                    att = _read_json(os.path.join(_d, _e))
                    if att:
                        break
        except Exception:
            pass
    if not att:
        out["reasons"].append(
            "缺少材料凭据（%s.material.json）—— 该零件未经过材料防线校验。"
            % os.path.basename(str(_real_part)))
        out["hint"] = ("用 swapi.new_part(material='Q235') 显式指定材料后重新保存；"
                       "凭据由 swapi.save() 自动写出。")
        return out
    # ── 【Bug16 修复】非法材料必须先拦（在验签之前判定）───────────────
    # 非法材料不会获得宿主签名（swapi.save 已提前返回），因此若先验签，
    #   报错会是"缺少签名"这种误导性原因；这里先给出准确诊断。
    if att.get("material_invalid"):
        out["reasons"].append(
            "非法材料：%s" % (att.get("error") or "材料不在合法材料库中"))
        out["hint"] = att.get("hint") or (
            "请使用内置材料（Q235/45#/304/6061-T6/PETG/PLA/ABS/Nylon/PC/TPU…）"
            "或用 save_custom_material() 显式登记后重试。")
        out["material_invalid"] = True
        return out
    # ── 【主防线】先验签：不经宿主签发的凭据一律不可信 ────────────────
    _sig = credential_is_signed_and_valid(att)
    if not _sig["ok"]:
        out["reasons"].append("材料凭据未通过签名校验：%s" % _sig["reason"])
        out["hint"] = ("材料凭据必须由宿主签名（swapi.save 会自动请求 /defense/sign）。"
                       "手工写入的 JSON 无法通过验签。")
        return out
    # ── 【防伪造】凭据必须与零件【同源】：schema 正确、part 路径一致、非手抄 ──
    _schema = str(att.get("schema") or "")
    if _schema != "dsh-material-attestation/1":
        out["reasons"].append(
            "材料凭据 schema 不匹配（%r）—— 非 swapi.save() 产出，疑似手工伪造" % _schema)
    _part_in_att = str(att.get("part") or "").strip()
    if _part_in_att:
        try:
            # ── 【Bug-09 修复】路径比对必须【大小写不敏感】─────────────────
            # Windows 上 xxx.SLDPRT 与 xxx.sldprt 是同一个文件；原实现用
            #   os.path.abspath 直接比较，大小写不同就误判为"挪用他人凭据"。
            _a1 = os.path.normcase(os.path.abspath(_part_in_att))
            _a2 = os.path.normcase(os.path.abspath(str(_real_part)))
            if _a1 != _a2:
                out["reasons"].append(
                    "材料凭据记录的零件路径与校验目标不一致（凭据=%s）—— 疑似挪用他人凭据"
                    % os.path.basename(_part_in_att))
        except Exception:
            pass
    else:
        out["reasons"].append("材料凭据缺少 part 字段（无法确认凭据属于该零件）")
    # ── 【防伪造】零件本体必须"像个真零件"：体积下限 + 哈希一致 ──────────
    _MIN_PART_BYTES = 4096          # 真 SLDPRT 通常几十 KB~几 MB
    try:
        _psz = os.path.getsize(str(part_path))
    except Exception:
        _psz = 0
    if _psz < _MIN_PART_BYTES:
        out["reasons"].append(
            "零件文件仅 %d 字节（< %d），不像真实的 SolidWorks 零件（疑似伪造）"
            % (_psz, _MIN_PART_BYTES))
    _att_md5 = str(att.get("part_md5") or "").strip()
    if _att_md5:
        try:
            import hashlib as _hl
            _h = _hl.md5()
            with open(str(part_path), "rb") as _pf:
                for _ch in iter(lambda: _pf.read(1 << 20), b""):
                    _h.update(_ch)
            if _h.hexdigest() != _att_md5:
                out["reasons"].append(
                    "零件内容与凭据记录的 md5 不一致 —— 凭据不是为当前文件生成的（疑似挪用/篡改）")
        except Exception:
            pass
    else:
        out["reasons"].append("材料凭据缺少 part_md5（无法确认凭据绑定当前零件内容）")
    out["attestation"] = {
        "material": att.get("attested_material"),
        "density_kg_m3": att.get("attested_density_kg_m3"),
        "family": att.get("attested_family"),
        "source": att.get("source"),
        "requested_material": att.get("requested_material"),
        "at": att.get("at"),
    }
    _name = att.get("attested_material")
    _dens = att.get("attested_density_kg_m3")
    _fam = att.get("attested_family")
    # (1) 材料身份缺失
    if not _name:
        out["reasons"].append("凭据里没有材料名（材料身份未知）")
    # (2) 密度缺失
    if _dens is None:
        out["reasons"].append("凭据里没有密度 —— 无法排除 SW 默认密度")
    else:
        # (3) 水密度：SW 未赋材质时的默认值
        if abs(float(_dens) - 1000.0) < 1.0:
            out["reasons"].append(
                "密度 1000 kg/m³（等同水）—— 典型的 SolidWorks 未赋材质默认值")
        # (4) 家族未知且密度不在任何工程材料区间
        elif _fam is None:
            out["reasons"].append(
                "密度 %.0f kg/m³ 不属于任何已知工程材料家族" % float(_dens))
        # ── 【MA-03 修复】水密度单列拦截 ────────────────────────────────
        # 密度 1000 曾被判为 plastic 而放行；现在 material_family 返回
        #   "water"，这里明确报出，避免"未赋材零件被当塑料"。
        elif _fam == "water":
            out["reasons"].append(
                "密度 %.0f kg/m³ 落在【水】的区间（990~1010）—— 这是 "
                "SolidWorks 未赋材质的默认密度，不是任何工程材料；"
                "请显式赋材后重新保存（如 swapi.new_part(material='Q235')）"
                % float(_dens))
    # (5) 显式记录过跨族拒绝 / 材料不匹配
    #
    # ── 【MA-01 修复】"曾被拒"标志不能单独定罪 ────────────────────────────
    # 实测缺陷：8 个【精确匹配】的国标牌号（Q355/GCr15/Cr12MoV/QT500-7/
    #   2A12/ZL104/20CrMnTi/65Mn）被材料防线误 BLOCK，而近似匹配的
    #   HT200/Q235 反而 PASS —— 完全自相矛盾。
    # 根因：swapi 的 rejected_candidates 是【累积】列表（多候选逐个尝试的
    #   正常过程），而校验侧只看标志就定罪，于是"中途试错、最终成功"被当成
    #   "材料写错了"。签发侧已修（成功即清标志），这里再加一道独立判据：
    #   若【请求名 == 实际生效名】（精确匹配，用户要什么就是什么），
    #   则该历史标志与本次结论无关，不得据此拦截。
    _mmr = bool(att.get("material_mismatch_rejected"))
    _req_for_mmr = str(att.get("requested_material") or "").strip()
    _app_for_mmr = str(att.get("applied_name")
                       or att.get("attested_material_name") or "").strip()
    _exact_name_match = bool(_req_for_mmr and _app_for_mmr
                             and _req_for_mmr.lower() == _app_for_mmr.lower())
    if _mmr and not _exact_name_match:
        out["reasons"].append("材料写入曾被判定为跨族/密度不符并拒绝（material_mismatch_rejected）")
    elif _mmr and _exact_name_match:
        out["material_mismatch_note"] = (
            "凭据带 material_mismatch_rejected 标志，但请求名与实际生效名一致"
            "（%s）—— 判为多候选试错的历史记录，不作为阻断依据（MA-01）"
            % _app_for_mmr)
    if att.get("ok") is False and _name is None:
        out["reasons"].append("凭据自报未成功写入材料")
    # ── 【Bug-04 修复】跨材质静默替代检测（属性级，不只看同族）─────────────
    # 实测缺陷：设计规格要求 PETG（ρ1270 / σy 40MPa），而 SW 材料库无此牌号，
    #   被近似成 PET（ρ1420 / σy≈55MPa，屈服差约 37%）。凭据只标
    #   approximate_match=True 并配 WARN，防线【放行】—— 属"跨材料静默替代"，
    #   本应拦截。原来判不出来是因为凭据里没有"用户请求的材料"，
    #   门禁只能看 approximate_match 这个过宽的布尔量（同族即 true）。
    # 现在凭据已带 requested_material（宿主签名），这里做属性级对比：
    #   · 请求名与实际生效名不同 → 对比二者在材料库中的关键属性；
    #   · 屈服强度或密度差异超过阈值 → 判为跨材质替代，硬阻断；
    #   · 属性查不到时【判为需要人工确认】（不静默放行）。
    _req_m = str(att.get("requested_material") or "").strip()
    _app_m = str(att.get("attested_material")
                 or att.get("attested_material_name")
                 or att.get("applied_name") or "").strip()
    # ── 【Bug-C 运行时修复】近似匹配必须按【属性差异】判定，不能只看名字 ──
    # 实测缺陷（子代理1/2）：请求 PETG(ρ1270/σy40)，SW 实得 PET(ρ1420/σy55)，
    #   凭据里 requested_material 与 attested_material 都写 "PETG"
    #   （attested 记的是【校验通过的名字】），于是"名字不同"这条判据
    #   根本不触发 —— PETG→PET 被静默放行，防线①可绕过。
    # 修复：加两条与"名字"无关的独立判据：
    #   ① 【密度实测差异】凭据记录的实测密度 vs 请求材料的期望密度，
    #      相对差 > 阈值即判实得材料与请求材料不是同一材料；
    #   ② 【近似匹配 + 期望属性不符】approximate_match=True 时，对比
    #      applied_name（SW 真实生效名）与 requested 的属性差异。
    _applied_real = str(att.get("applied_name") or "").strip()
    # ① 密度实测差异（最强证据：密度是 SW 写进零件的客观事实）
    if _req_m and _dens:
        try:
            _ok_r, _info_r, _ = _material_props_lookup(_req_m)
            _exp_d = None
            if _ok_r and _info_r:
                _exp_d = _info_r.get("density_kg_m3") or _info_r.get("density")
            # 兜底：查本模块内置属性表
            if not _exp_d:
                _p = _material_props_any(_req_m)
                _exp_d = (_p or {}).get("density")
            if _exp_d:
                _rel = abs(float(_dens) - float(_exp_d)) / float(_exp_d)
                out["density_deviation_pct"] = round(_rel * 100, 2)
                if _rel > _DENSITY_MISMATCH_TOL:
                    out["reasons"].append(
                        "【Bug-C】实得材料密度与请求材料不符：请求 %s（期望 %.0f kg/m³），"
                        "实测密度 %.0f kg/m³，相差 %.1f%%（容差 %.0f%%）—— "
                        "说明 SW 实际生效的不是请求的材料（近似替代），"
                        "请改用材料库中精确存在的牌号，不要以近似匹配放行。"
                        % (_req_m, float(_exp_d), float(_dens), _rel * 100,
                           _DENSITY_MISMATCH_TOL * 100))
                    out["material_density_mismatch"] = True
        except Exception:
            pass
    # ② 名字不同的常规路径（保留原有属性级对比）
    _cmp_target = _applied_real or _app_m
    if _req_m and _cmp_target and _req_m.lower() != _cmp_target.lower():
        _ms = material_substitution_check(_req_m, _cmp_target, _dens)
        out["material_substitution"] = _ms
        if _ms.get("blocking"):
            out["reasons"].append(_ms["reason"])
        elif _ms.get("reason"):
            out["material_substitution_note"] = _ms["reason"]
    # ③ 【Bug-C 运行时修复】屈服强度差异（凭据里的属性指纹）
    #    这是识别"名字与密度都看不出"的替代的关键判据：
    #    6061-T6(σy276) → 7075(σy503) 名字不同但密度仅差 4.1%，
    #    单靠密度容差会漏掉，而屈服差 82% 一眼可辨。
    try:
        _ey = att.get("expected_yield_mpa")
        _ay = att.get("applied_yield_mpa")
        _ypct = att.get("yield_deviation_pct")
        if _ypct is None and _ey and _ay:
            try:
                _ypct = abs(float(_ay) - float(_ey)) / float(_ey) * 100.0
            except Exception:
                _ypct = None
        if _ypct is not None:
            out["yield_deviation_pct"] = round(float(_ypct), 2)
            if float(_ypct) > _SUBSTITUTION_TOL_PCT:
                out["reasons"].append(
                    "【Bug-C】实得材料屈服强度与请求材料不符：请求 %s（σy≈%s MPa），"
                    "实际生效 %s（σy≈%s MPa），相差 %.1f%%（容差 %.0f%%）—— "
                    "属不可接受的牌号替代，请改用材料库中精确存在的牌号。"
                    % (_req_m or "?", _ey, _applied_real or _app_m or "?", _ay,
                       float(_ypct), _SUBSTITUTION_TOL_PCT))
                out["material_yield_mismatch"] = True
    except Exception:
        pass
    # ── 【Bug-C 修复】跨标准体系替代必须拦截 ────────────────────────────
    # 实测缺陷：请求 Q235（GB/T 700），SW 材料库无此牌号时回退命中
    #   AISI 1020（美标），而凭据只标 approximate_match=false ——
    #   报告看起来像"精确匹配"，实际是跨标准替代（屈服差约 49%）。
    # 凭据现已带宿主签名的 cross_standard 标记，这里据此硬阻断。
    #
    # ── 【BUG-D 修复·死循环】原提示给的出路【不存在】──────────────────
    # 实测（子代理全量赋材矩阵）：45#→AISI 1045、Q235→AISI 1020、
    #   40Cr→1.2083、316→AISI 316 全部被跨标准 BLOCK。
    #   而原提示写"请改用国标牌号重新赋材（如 Q235/45#/40Cr/HT200）"——
    #   这些牌号在 SolidWorks 材质库里【根本不存在】，照做必然再次被
    #   解析成同一个欧美标、再次 BLOCK。用户陷入无解循环，只能
    #   DSH_DEFENSE_BYPASS=1 绕过防线（那等于防线失效）。
    # 现在：如实说明"SW 无此牌号"这一客观约束，并给出【真正可执行】的
    #   两条出路：① 用 set_custom_material() 在 SW 中登记国标牌号后重做；
    #   ② 若该跨标准替代在工程上可接受（屈服差在容差内），
    #      走显式豁免流程（写明理由并留痕），而不是静默放行。
    if att.get("cross_standard"):
        _cs_note = str(att.get("cross_standard_note") or "").strip()
        # 判断是否为"SW 材质库无该牌号"导致的必然替代
        _sw_missing = bool(att.get("material_applied") is False) or bool(
            att.get("applied_name") and att.get("requested_material")
            and att.get("applied_name") != att.get("requested_material"))
        out["reasons"].append(
            "跨标准体系材料替代（疑似以欧美标顶替国标牌号）："
            + (_cs_note or ("请求 %s，实际生效 %s" % (_req_m or "?", _app_m or "?")))
            + "。"
            + ("【注意】SolidWorks 材质库通常【不含】国标牌号，"
               "因此单纯改用国标牌号重新赋材无法解决问题（会再次命中同一替代）。"
               "可执行的出路：① 用 set_custom_material() 把该 GB 牌号"
               "（含 E/ν/σy/σb/ρ）登记进 SolidWorks 后再赋材重做；"
               "② 若该替代在工程上可接受，走显式豁免并在报告中写明理由与"
               "屈服差异，不要静默放行。"
               if _sw_missing else
               "请改用材料库中精确存在的牌号重新赋材。"))
        out["cross_standard"] = True
        out["sw_material_library_missing_gb"] = _sw_missing
        out["actionable_hint"] = (
            "set_custom_material(name, density, e_mpa, yield_mpa, uts_mpa, "
            "poissons_ratio) 登记国标牌号 → 重新赋材 → 重做校核"
            if _sw_missing else
            "改用材料库中精确存在的牌号")
    # (6) 未赋材兜底警告：近似匹配按 WARNING 处理，不阻断
    if not out["reasons"]:
        out["ok"] = True
        out["level"] = "WARN" if att.get("approximate_match") else "PASS"
        if att.get("approximate_match"):
            out["reasons"].append("材料为近似匹配（库中无精确同名），建议核对")
    return out


# ══════════════════════════════════════════════════════════════════════
#  防线 ② 物理 / 防线 ③ 领域
# ══════════════════════════════════════════════════════════════════════

def _room_file(room, suffix):
    safe = "".join(c for c in str(room) if c.isalnum() or c in " _-").strip()
    return os.path.join(REPORTS_DIR, safe + suffix)


def physics_attest_path(room):
    return _room_file(room, ".physics.json")


def domain_attest_path(room):
    return _room_file(room, ".domain.json")


def _seal_attestation(payload, kind, room, extra_meta=None):
    """把凭据体落盘，并在【只有未签名时】补齐展示用元数据。

    ── 【签后补字段 BUG 修复】这是三道防线此前 100% 失效的根因 ──────────────
    宿主签名 = HMAC(canonicalJson(待签体))，验签端先剥离 _sig/_kid/_ts/_nonce
    再逐字节重算。因此【任何在签名之后写入凭据体的字段都会让验签必然失配】。

    原实现无条件 rec.update(payload)，而 payload 是宿主已签好的凭据 →
    必然多出 schema/room/at/ts 等字段 → HMAC 对不上 → 防线①②③全部拦死，
    现场只能靠 `room-end --force` 绕过（审计留痕可证）。

    正确语义：
      · 已签名凭据（含 _sig）→ 【原样落盘】，一个字段都不加、不改。
        需要的元数据已由宿主在签发时纳入待签体（见 defense-sign.js）。
      · 未签名兜底凭据（宿主不可用）→ 仍补齐元数据，便于人工排障；
        它本来就过不了验签（fail-closed），补字段不会造成额外损害。
    """
    body = dict(payload or {})
    if not body.get("_sig"):
        rec = {
            "schema": "dsh-%s-attestation/1" % kind,
            "room": str(room),
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "ts": time.time(),
        }
        rec.update(extra_meta or {})
        rec.update(body)
        body = rec
    return body


def write_physics_attestation(room, payload):
    """由 physics 链路写就（sw_bridge/physics_bridge 调用）。"""
    rec = _seal_attestation(payload, "physics", room)
    _write_json(physics_attest_path(room), rec)
    return rec


def write_domain_attestation(room, payload):
    rec = _seal_attestation(payload, "domain", room)
    _write_json(domain_attest_path(room), rec)
    return rec


# ══════════════════════════════════════════════════════════════════════
#  【三大防线 · 签名式信任根】宿主验签
# ══════════════════════════════════════════════════════════════════════
#  密码学定位（用户给定）：签名是主防线，守卫是补丁。
#  本模块【不持有密钥】—— 密钥只在 DSH 宿主进程内存里，
#  因此签名与验签都必须请求宿主。这样即使 AI 能读写工程模式目录下的
#  任何文件，也拿不到签发能力，"手写一份格式完美的凭据"必然验签失败。
#
#  宿主端点由宿主在 <tools>/.defense/runtime.json 中公布（含端口）。
#  宿主不可用时的行为 = 【fail-closed】：无法验签即视为未通过防线。
#  唯一的合法绕行仍是 DSH_DEFENSE_BYPASS=1 / --force，且必定留痕。

# ── 【P0-3 修复】.defense 也必须落在【权威状态目录】────────────────────
# 宿主把 .defense/ 写在它加载插件的那份 tools 目录（安装副本）。若 Python
# 把 runtime.json / workspace.txt 写到【脚本所在目录】（工作区），就会：
#   · 在无关副本里凭空造出一份 .defense（含伪造/过期的 kid 与 port）；
#   · 而 _runtime_candidates() 又优先读这份 → 连到【已死的端口】，
#     三道防线全部 fail-closed，且现象与"签名服务未装配"难以区分。
DEFENSE_DIR = os.path.join(STATE_DIR, ".defense")
RUNTIME_FILE = os.path.join(DEFENSE_DIR, "runtime.json")


def _runtime_candidates():
    """runtime.json 的候选位置（按可信度排序）。

    ── 【Bug3 + P0-3 修复】──────────────────────────────────────────────
    宿主把 .defense/ 写在【它加载插件的那份 tools 目录】（安装副本）；
    而脚本可能从工作区副本被调用。原实现把"脚本自身目录"排在**第一位**，
    于是任何在工作区残留的 .defense/runtime.json（例如非宿主进程写下的
    测试产物）都会**优先于真宿主**被采用 → 连到死端口 → 防线 fail-closed。

    现按可信度排序：
      ① 权威状态目录（_store 判定，通常就是安装副本 = 宿主所在处）
      ② 安装副本（显式拼出，便于 DSH_HOME 与 ~ 不一致的场景）
      ③ 脚本自身目录（最低，兼容未安装的纯仓库使用）
    """
    cands = [RUNTIME_FILE]
    try:
        home = os.path.expanduser("~")
        dsh_home = os.environ.get("DSH_HOME") or os.path.join(home, ".dsh")
        # 宿主实际加载位置（安装目录）—— 最可能的真相来源
        cands.append(os.path.join(dsh_home, ".agent-presets", "engineering",
                                  "tools", ".defense", "runtime.json"))
        cands.append(os.path.join(dsh_home, "engineering", "tools",
                                  ".defense", "runtime.json"))
        # 脚本自身目录（最后兜底；可能是工作区副本）
        cands.append(os.path.join(_BASE_DIR, ".defense", "runtime.json"))
        # 工程模式根目录旁的另一份副本
        cands.append(os.path.abspath(os.path.join(_BASE_DIR, "..", "..",
                                        "engineering", "tools", ".defense",
                                        "runtime.json")))
    except Exception:
        pass
    out, seen = [], set()
    for c in cands:
        try:
            a = os.path.abspath(c)
        except Exception:
            continue
        if a in seen:
            continue
        seen.add(a)
        out.append(a)
    return out


def _workspace_root_candidates():
    """本副本声明的工程模式根（仓库根 + engineering），供宿主扩大受管根。"""
    _root = os.path.abspath(os.path.join(_BASE_DIR, "..", ".."))
    # 兼容：engineering 目录也写一份（部分部署把 output 放在 engineering 下）
    return [_root, os.path.abspath(os.path.join(_BASE_DIR, ".."))]


def _defense_dirs():
    """所有已知 .defense 目录（两份副本各一份）。"""
    dirs = []
    for _c in _runtime_candidates():
        try:
            _d = os.path.dirname(os.path.abspath(_c))
        except Exception:
            continue
        if _d and _d not in dirs:
            dirs.append(_d)
    for _d in (DEFENSE_DIR, os.path.join(_BASE_DIR, ".defense")):
        try:
            _a = os.path.abspath(_d)
        except Exception:
            continue
        if _a not in dirs:
            dirs.append(_a)
    return dirs


def _publish_workspace_root():
    """把本副本的工程模式根目录声明给宿主（供宿主扩大受管根）。

    ── 【Bug8 修复】工程模式有工作区/安装目录两份副本。宿主按自己的
    #    toolsDir 算受管物理运行根，而报告可能写在另一份里，于是 /sign
    #    以 "report path outside managed physics_runs root" 拒签。
    #    这里由 Python 侧把【自己的根目录】写到宿主的 .defense/workspace.txt，
    #    使宿主能把该位置也纳入受管范围。

    ── 【Bug-02 修复】workspace.txt 原先【整体覆盖写】，谁最后跑谁生效 ───
    实测：工作区副本（Desktop\\DSH-SW-and-CAD-main）与安装副本
      （~/.dsh/.agent-presets/engineering）各写各的，后者把前者覆盖掉，
      于是宿主读到的受管根里【没有】工作区路径 → 报告落在工作区
      output/physics_runs 时一律被拒签（"report path outside managed
      physics_runs root"），防线②凭据永远签不出来。
    现在：
      ① 【合并式写入】读取已有内容并求并集，不覆盖别的副本的声明；
      ② 【写入所有 .defense 目录】两份副本各写一份，宿主无论选中哪份
         都能读到【包括了工作区】的完整受管根集合；
      ③ 路径归一化去重（大小写不敏感），避免同一目录重复。
    """
    _roots = _workspace_root_candidates()
    _written = []
    for _d in _defense_dirs():
        try:
            if not os.path.isdir(_d):
                continue
            _dest = os.path.join(_d, "workspace.txt")
            _merged = []
            _seen = set()
            # ① 先并入磁盘上已有的声明（含另一份副本写的）
            try:
                if os.path.isfile(_dest):
                    with open(_dest, "r", encoding="utf-8", errors="replace") as _f:
                        for _ln in _f.read().splitlines():
                            _s = _ln.strip()
                            if _s:
                                _merged.append(_s)
            except Exception:
                pass
            # ② 再并入本副本声明的根
            _merged.extend(_roots)
            # ③ 归一化去重（大小写不敏感，保留首个真实写法）
            _out = []
            for _m in _merged:
                try:
                    _a = os.path.abspath(_m)
                except Exception:
                    continue
                _k = os.path.normcase(_a)
                if _k in _seen:
                    continue
                _seen.add(_k)
                _out.append(_a)
            if not _out:
                continue
            with open(_dest, "w", encoding="utf-8") as _f:
                _f.write("\n".join(_out))
            _written.append(_dest)
        except Exception:
            continue
    return _written or None


def _find_runtime_file():
    """返回第一个存在的 runtime.json 路径（无则 None）。"""
    for c in _runtime_candidates():
        try:
            if os.path.exists(c):
                return c
        except Exception:
            continue
    return None


def _read_runtime():
    """读取宿主公布的运行时信息（端口 / kid）。"""
    # ── 【Bug3 修复】在多个候选位置查找（工作区 / 安装目录可能各一份）──
    p = _find_runtime_file()
    if not p:
        return {}
    return _read_json(p) or {}


def _host_base(require_owner=True):
    """宿主基地址；宿主未公布端口时返回 None。

    ── 【FL-05 修复】默认必须通过【端口归属校验】才返回基地址 ──────────────
    实测缺陷：`request_signed_credential` / `_host_base` 单点信任
      runtime.json 里的 port —— 同一用户改写该文件即可把【签发请求】
      劫持到任意端点（sign 层原本没有任何 owner_check；
      check_room_defense 那条路径有，属覆盖不全）。
    现在把校验收进 _host_base 本身，使 sign / verify / judge 三条链路
      统一受保护：
        · require_owner=True（默认）→ 端口监听者必须是 DSH 主进程；
          校验不通过返回 None（调用方按 fail-closed 处理）；
        · 校验结果按 (port,pid) 缓存 30 秒 —— 端口归属检查要起
          PowerShell 查 TCP 表，每次调用都做会让门禁明显变慢。
    """
    rt = _read_runtime()
    port = rt.get("port")
    if not port:
        return None
    if require_owner:
        global _HOST_OWNER_CACHE
        _key = (str(port), str(rt.get("pid") or ""))
        _now = time.time()
        _hit = _HOST_OWNER_CACHE.get("key")
        if _hit == _key and (_now - float(_HOST_OWNER_CACHE.get("at") or 0)) < 30.0:
            _ok = bool(_HOST_OWNER_CACHE.get("ok"))
        else:
            try:
                _own = _port_owner_check(rt.get("pid"), port)
                _ok = bool(_own.get("ok"))
            except Exception:
                _ok = False
            _HOST_OWNER_CACHE = {"key": _key, "at": _now, "ok": _ok}
        if not _ok:
            # 归属校验不通过 → 不交出基地址（fail-closed，不把请求发往可疑端点）
            return None
    host = str(rt.get("host") or "127.0.0.1")
    # [Bug8] 把本副本根目录声明给宿主，使其受管根覆盖两个部署位置
    try:
        _publish_workspace_root()
    except Exception:
        pass
    return "http://%s:%s" % (host, port)


# 端口归属校验缓存（避免每次调用都起 PowerShell 查 TCP 表）
_HOST_OWNER_CACHE = {}


def _post_json(url, payload, timeout=5.0):
    """向宿主 POST JSON；返回解析后的 dict 或 None。"""
    try:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url, data=data,
            headers={"content-type": "application/json; charset=utf-8"},
            method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
        return json.loads(body)
    except Exception:
        return None


def _port_owner_check(expected_pid, port=None):
    """校验宿主端口确实由 DSH 主进程持有（堵"假宿主"冒充）。

    对抗测试实证：Python 原本只信 runtime.json 里的 port —— AI 起一个
    "恒真"HTTP 服务并改写该文件即可整体绕过信任根。这里增加两项锚点：
      ① 该端口在【本机】的监听者 PID 必须等于宿主写入的 pid；
      ② 该 PID 的进程名必须匹配 DSH 主程序（DeepSeek Harness / electron / node）。

    ⚠️ 这是【提高成本】而非密码学保证：同用户仍可杀掉真 DSH 后占位，
       但那会立刻被用户察觉，且守卫会记录异常。真正闭环由宿主侧判定
       (judgeDefense) 承担 —— 见 check_room_defense 的宿主优先路径。

    Returns: {ok, reason}
    """
    if not expected_pid:
        return {"ok": False, "reason": "runtime.json 未记录宿主 pid"}
    try:
        if os.name != "nt":
            return {"ok": True, "reason": "非 Windows：跳过端口归属校验"}
        import subprocess as _sp
        # 查该端口监听者的 PID（端口由调用方显式给出，避免依赖模块级状态）
        _p = int(port if port is not None else 0)
        if not _p:
            return {"ok": False, "reason": "未提供宿主端口"}
        ps = ("$c = Get-NetTCPConnection -LocalPort %d -State Listen -ErrorAction SilentlyContinue; "
              "if ($c) { $c | Select-Object -First 1 -ExpandProperty OwningProcess }" ) % _p
        r = _sp.run(["powershell", "-NoProfile", "-Command", ps],
                    capture_output=True, text=True, timeout=10)
        port_pid = (r.stdout or "").strip()
        if not port_pid:
            return {"ok": False, "reason": "端口 %s 无监听者" % _p}
        if str(port_pid) != str(expected_pid):
            return {"ok": False,
                    "reason": "端口监听者 PID=%s 与宿主声明的 pid=%s 不一致（疑似假宿主）"
                              % (port_pid, expected_pid)}
        # 查进程名
        ps2 = ("(Get-Process -Id %s -ErrorAction SilentlyContinue).ProcessName" % str(expected_pid))
        r2 = _sp.run(["powershell", "-NoProfile", "-Command", ps2],
                     capture_output=True, text=True, timeout=10)
        name = (r2.stdout or "").strip()
        if not name:
            return {"ok": False, "reason": "无法取得 PID %s 的进程名" % expected_pid}
        # ② 进程名必须像 DSH 宿主
        if not any(k in name.lower() for k in ("deepseek", "harness", "electron", "node")):
            return {"ok": False,
                    "reason": "PID %s 进程名为 %r，不是 DSH 宿主（疑似假宿主）"
                              % (expected_pid, name)}
        # ③ 更强锚点：可执行文件路径必须是真实 DSH 安装（进程名可被改名冒充，
        #    但可执行路径要伪造就必须在别处放一个同名的真程序，成本更高且更显眼）。
        ps3 = ("(Get-Process -Id %s -ErrorAction SilentlyContinue).Path" % str(expected_pid))
        r3 = _sp.run(["powershell", "-NoProfile", "-Command", ps3],
                     capture_output=True, text=True, timeout=10)
        exe_path = (r3.stdout or "").strip()
        if not exe_path:
            return {"ok": False, "reason": "无法取得 PID %s 的可执行路径" % expected_pid}
        _lp = exe_path.replace("/", "\\").lower()
        _ok_path = any(k in _lp for k in (
            "\\dsh\\", "deepseek harness.exe", "deepseek-harness",
            "\\app.asar", "electron",
        ))
        if not _ok_path:
            return {"ok": False,
                    "reason": "PID %s 可执行路径 %r 不是 DSH 安装（疑似假宿主）"
                              % (expected_pid, exe_path)}
        return {"ok": True,
                "reason": "端口归属校验通过 (pid=%s, name=%s, exe=%s)"
                          % (port_pid, name, exe_path)}
    except Exception as e:
        return {"ok": False, "reason": "端口归属校验异常: %r" % (e,)}


def _get_port():
    """当前 runtime.json 声明的端口。"""
    return (_read_runtime() or {}).get("port")


def host_judge_room(room, require_physics=True):
    """请求【宿主】执行完整防线判定（C 方案）。

    Returns: {ok, blockers, warnings, details, judged_by} 或 None（宿主不可用）。
    """
    base = _host_base()
    if not base:
        return None
    res = _post_json(base + "/dsh-engineering-ui/defense/judge",
                     {"room": str(room), "kind": "room",
                      "require_physics": bool(require_physics)}, timeout=8.0)
    if not isinstance(res, dict) or "blockers" not in res:
        return None
    return res


def host_verify_credential(cred):
    """请求宿主验签（门禁内部通道，不消费 nonce）。

    Returns: {ok, reason, host_available}
      host_available=False 表示宿主不在（此时调用方按 fail-closed 处理）。

    ── 【P2 修复】门禁必须走 /defense/verify-gate，不能用公开端点 ──────────
    公开端点 /defense/verify 现在【单次消费 nonce】（防外部重放）。
    但门禁自身要对同一份凭据做多阶段校验：
        room-end → confirm-assembly → 收尾 select
    若走公开端点，第二次校验就会被判"重放"而失败 —— 那是把防重放
    误伤到了正常流程。因此门禁固定走【内部端点 verify-gate】（不消费）。
    两个端点共用同一套 HMAC 校验，安全强度一致，只是消费语义不同。
    """
    base = _host_base()
    if not base:
        return {"ok": False, "reason": "宿主未公布防线端点（runtime.json 缺失或无私端口）",
                "host_available": False}
    # ① 优先走内部端点（不消费 nonce，支持门禁多阶段校验）
    res = _post_json(base + "/dsh-engineering-ui/defense/verify-gate", cred)
    if not isinstance(res, dict):
        # ② 兼容旧宿主（尚无 verify-gate 端点）→ 回落公开端点
        res = _post_json(base + "/dsh-engineering-ui/defense/verify", cred)
    if not isinstance(res, dict):
        return {"ok": False, "reason": "宿主验签端点无响应",
                "host_available": False}
    return {"ok": bool(res.get("verified")), "reason": res.get("reason"),
            "host_available": True}


def request_signed_credential(spec):
    """向宿主请求签发一份凭据。

    spec 只描述"要签什么"；待签体由【宿主自己读磁盘】构造，
    调用方无法指定结论（例如无法让宿主为一份不存在的 PASS 背书）。
    """
    base = _host_base()
    if not base:
        return {"ok": False, "error": "宿主未公布防线端点（runtime.json 缺失或无私端口）"}
    res = _post_json(base + "/dsh-engineering-ui/defense/sign", spec, timeout=8.0)
    if not isinstance(res, dict):
        return {"ok": False, "error": "宿主签发端点无响应"}
    return res


def note_unsigned_attempt(room, kind, reason):
    """记录一次"宿主不可用导致产出无签名凭据"的事实（审计用）。"""
    try:
        items = _read_json(BYPASS_LOG_FILE) or {"items": []}
        if not isinstance(items, dict) or not isinstance(items.get("items"), list):
            items = {"items": []}
        items["items"].append({
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "where": "defense_gate.unsigned:" + str(room),
            "kind": str(kind),
            "reason": str(reason or "宿主签名服务不可用"),
            "by": "fail-closed",
        })
        items["items"] = items["items"][-200:]
        _write_json(BYPASS_LOG_FILE, items)
    except Exception:
        pass


def credential_is_signed_and_valid(cred):
    """凭据必须带签名且经宿主验签通过。

    这是防线①②③共同的强制前置：任何凭据在进入原有判定逻辑之前，
    都必须先过这一关。不带签名 = 直接判不可信（不进入后续逻辑）。
    """
    if not isinstance(cred, dict):
        return {"ok": False, "reason": "凭据不是对象"}
    if not cred.get("_sig"):
        return {"ok": False, "reason": "凭据缺少签名 —— 非宿主签发，疑似伪造"}
    v = host_verify_credential(cred)
    if not v["ok"]:
        return {"ok": False, "reason": "签名校验未通过：%s" % (v.get("reason") or "未知")}
    return {"ok": True}


def _physics_roots_candidates():
    """物理运行根目录的候选（受管根 + 报告实际落点）。

    ── 【P0-3 / Bug8 修复】必须同时覆盖【两份副本】与【仓库根】──────────
    工程模式的物理报告由 physics_bridge 写在 <repo>/output/physics_runs，
    而脚本可能从【工作区】或【安装副本】加载；同时受管根还要与宿主
    （defense-sign.js managedRoots()）保持一致，否则宿主会以
    "report path outside managed physics_runs root" 拒签（Bug8）。
    因此这里把三处都纳入：状态目录、脚本目录，各自向上两级。
    """
    bases = []
    for d in (STATE_DIR, _BASE_DIR):
        try:
            a = os.path.abspath(d)
            if a and a not in bases:
                bases.append(a)
        except Exception:
            continue
    out = []
    for b in bases:
        for rel in ((os.pardir, os.pardir, "output", "physics_runs"),
                    (os.pardir, "output", "physics_runs"),
                    ("output", "physics_runs")):
            try:
                p = os.path.abspath(os.path.join(b, *rel))
                if p not in out:
                    out.append(p)
            except Exception:
                continue
    return out


def _physics_run_roots():
    """受管的物理运行根目录（只有这些目录下的报告才算"真实运行"）。"""
    return _physics_roots_candidates()


def _real_physics_runs(case_path=None):
    """扫描 output/physics_runs，返回【真实存在】的仿真报告列表。

    用途：凭据只写 JSON 是【可手写伪造】的 —— 必须回查磁盘上是否真有
      对应的 FEA 运行报告，否则"三大防线"就只是一张可自制的通行证。
    """
    out = []
    for base in _physics_roots_candidates():
        base = os.path.abspath(base)
        if not os.path.isdir(base):
            continue
        for path in glob.glob(os.path.join(base, "*", "**", "*_report.json"),
                              recursive=True):
            rep = _read_json(path)
            if not isinstance(rep, dict):
                continue
            out.append({
                "path": path,
                "run_id": rep.get("run_id"),
                "overall": rep.get("overall_status"),
                "case": ((rep.get("load_case_summary") or {}).get("problem_id")
                         or (rep.get("load_case_summary") or {}).get("id")),
                "mtime": os.path.getmtime(path),
            })
        if out:
            break
    return out


def _attestation_is_backed_by_real_run(att):
    """凭据是否【有真实运行记录背书】（防手写伪造）。

    [!] 不能只按 run_id 匹配：实测 run_id 在磁盘上【不唯一】——
       opt_iter_1 同时存在 7 份报告，overall 分别为 FAIL/PASS/UNKNOWN。
       若只看 run_id，伪造者写一个常见的 run_id 即可借到别人的真实运行。
       因此判据按【强度递减】且要求【结论一致】：

      ① report_path 真实存在 且 其 overall 与凭据一致；
      ② run_dir 是真实目录 且 其下有报告 且 报告 overall 与凭据一致；
      ③ run_id 命中的真实报告里，存在与凭据 overall 一致的那一份；
      ④ 以上都不成立 -> 无背书（判为疑似伪造）。
    """
    _run = str(att.get("run_id") or "").strip()
    _dir = str(att.get("run_dir") or "").strip()
    _rp = str(att.get("report_path") or "").strip()
    _want = str(att.get("overall") or "").strip().upper()

    # ── 【防伪造】运行记录必须位于【受管目录 output/physics_runs】之下 ────
    # 对抗测试 C1d：自己新建任意目录 + 一份 _report.json 即可"制造真实运行"。
    # 这里限定只接受受管根目录内的路径 —— 手工在别处造目录不再算数。
    def _under_managed_root(p):
        if not p:
            return False
        try:
            ap = os.path.abspath(p)
        except Exception:
            return False
        for root in _physics_run_roots():
            try:
                if os.path.commonpath([ap, root]) == root:
                    return True
            except Exception:
                continue
        return False

    _att_room = str(att.get("room") or "").strip()

    def _report_matches(path):
        """读一份报告：① overall 与凭据一致；② （若报告带房间）房间归属一致。

        房间归属是防"借用/复制他人报告"的关键：报告由 physics_bridge 在运行
        时就写入 room，无法靠事后手写凭据伪造（对抗测试 C1c/C1d 的根因）。
        """
        rep = _read_json(path)
        if not isinstance(rep, dict):
            return False
        _rep_room = str(rep.get("room") or "").strip()
        if _att_room:
            # 强制要求报告带房间归属：不带 room 的报告（含历史遗留报告）
            #   一律【不作为背书】—— 它们无法证明"属于这次、这个房间的运行"，
            #   正是对抗测试 C1c/C1d 借用的对象。
            if not _rep_room:
                return False
            if _rep_room != _att_room:
                return False        # 报告属于别的房间 → 不可借用
        # ── 报告必须"结构完整"，不能是一份手写的两行 JSON ──────────────
        # 真实报告由 simulation_report.build_report() 产出，带固定字段：
        #   schema_version / run_id / timestamp / load_case_summary /
        #   geometry_gate / fea_result / acceptance_criteria /
        #   overall_status / passed_gates / failed_gates / release_readiness
        # 只写 {"overall_status":"PASS","room":X} 的一行假报告会被这里挡下。
        _required = ("schema_version", "run_id", "timestamp", "fea_result",
                     "geometry_gate", "acceptance_criteria", "overall_status")
        _missing = [k for k in _required if k not in rep]
        if _missing:
            return False
        _fea = rep.get("fea_result")
        if not isinstance(_fea, dict):
            return False
        # 真实 fea_result 至少带 gates（可能为空 dict）或 ok/error 之一
        if not (("gates" in _fea) or ("ok" in _fea) or ("error" in _fea)):
            return False
        # ── 【P3 修复】run_id 必须与报告相互印证 ──────────────────────────
        # 实测缺陷（子代理1）：只带 report_path 时把 run_id 改成假值仍判
        #   backed=true —— 背书检查没看 run_id。虽然签名能兜住（假 run_id
        #   会让 HMAC 失配），但背书本身不应只看单一字段。
        # 现在：凭据带了 run_id 时，要求报告里的 run_id 与之【一致】。
        if _run:
            _rep_run = str(rep.get("run_id") or "").strip()
            if _rep_run and _rep_run != _run:
                return False        # 报告属于另一次运行 → 凭据 run_id 不真实
        got = str(rep.get("overall_status") or rep.get("overall") or "").strip().upper()
        if not _want:
            return True
        return got == _want

    # (1) 最直接：凭据自带 report_path —— 且必须位于受管根目录内
    if _rp and not _under_managed_root(_rp):
        return False, ("report_path 不在受管的 output/physics_runs 目录内"
                       "（疑似自建目录/伪造运行记录）")
    if _rp and os.path.exists(_rp):
        if _report_matches(_rp):
            return True, "report_path 真实存在且结论一致"
        return False, ("report_path 存在但结论与凭据不一致（凭据=%s）" % (_want or "?"))
    # (2) run_dir：唯一路径，最强背书
    if _dir and not _under_managed_root(_dir):
        return False, "run_dir 不在受管的 output/physics_runs 目录内（疑似自建/伪造）"
    if _dir and os.path.isdir(_dir):
        hits = glob.glob(os.path.join(_dir, "**", "*_report.json"), recursive=True)
        if any(_report_matches(h) for h in hits):
            return True, "run_dir 下存在结论一致的真实报告"
        if hits:
            return False, "run_dir 存在报告但结论与凭据不一致（疑似篡改 overall）"
        return False, "run_dir 下没有任何真实报告"
    # (3) run_id 单用【不作为背书】—— 它跨任务复用（opt_iter_1 有 7 份且状态各异），
    #     只凭 run_id 就能"借"到别人的真实运行，等于没防。仅作为诊断信息返回。
    #
    # ── 【P3 修复】run_id 必须与 run_dir 或 report_path【相互印证】──────────
    # 实测缺陷（子代理1）：凭据只带 report_path 时，把 run_id 改成任意假值
    #   仍判 backed=true —— 因为该分支根本没看 run_id。
    #   虽然签名能兜住最终结论（假 run_id 会让 HMAC 失配），但背书检查
    #   本身不应"只看一个字段"，否则一旦某条路径的签名校验被绕过，
    #   背书就成了空壳。现在：若凭据同时带 run_id 与 report_path/run_dir，
    #   则要求【报告里的 run_id 与凭据 run_id 一致】才算背书。
    runs = _real_physics_runs()
    if _run:
        _same = [r for r in runs if str(r.get("run_id") or "") == _run]
        if not _same:
            # run_id 在磁盘上找不到任何对应运行 → 明确判无背书
            return False, ("凭据声明的 run_id=%r 在受管 output/physics_runs 下"
                           "找不到任何对应运行记录（run_id 不真实）" % _run)
        if _same and not (_rp or _dir):
            _dirs = ", ".join(sorted({os.path.dirname(str(r.get("path"))) for r in _same})[:2])
            return False, ("凭据只有 run_id=%r，而该 run_id 在磁盘上对应 %d 份不同运行"
                           "（如 %s）—— 无法确认是哪一次，必须携带 run_dir 或 report_path"
                           % (_run, len(_same), _dirs))
    return False, ("凭据未携带 run_dir / report_path（唯一运行路径），"
                   "仅有 run_id 无法追到具体这次运行")

def check_physics_attestation(room):
    out = {"ok": False, "level": "BLOCK", "reasons": [], "room": str(room)}
    att = _read_json(physics_attest_path(room))
    if not att:
        out["reasons"].append(
            "缺少物理校核凭据（reports/%s.physics.json）—— 该房间未做过 FEA/疲劳校核。"
            % str(room))
        out["hint"] = ("在房间内执行: python sw_bridge.py physics-optimize "
                       "<load_case.json> --room %s" % str(room))
        return out
    # ── 【主防线】先验签 ──────────────────────────────────────────────
    _sig = credential_is_signed_and_valid(att)
    if not _sig["ok"]:
        out["reasons"].append("物理凭据未通过签名校验：%s" % _sig["reason"])
        out["hint"] = ("物理凭据必须由宿主签发（physics-optimize 会自动请求）。"
                       "手工写入的 JSON 无法通过验签。")
        return out
    # ── 【防伪造】凭据的 room 必须就是被校验的房间（防跨房间借用）──
    _att_room = str(att.get("room") or "").strip()
    if _att_room and _att_room != str(room):
        out["reasons"].append(
            "物理凭据归属房间为 %r，与被校验房间 %r 不一致 —— 疑似挪用其他房间的凭据"
            % (_att_room, str(room)))
    out["attestation"] = {
        "overall": att.get("overall"),
        "safety_factor": att.get("safety_factor"),
        "fatigue": att.get("fatigue_verdict"),
        "run_id": att.get("run_id"),
        "at": att.get("at"),
    }
    # ── 【防伪造】凭据必须有真实运行记录背书 ──────────────────────────
    _backed, _why = _attestation_is_backed_by_real_run(att)
    out["backed_by_real_run"] = _backed
    if not _backed:
        out["reasons"].append(
            "物理凭据缺少真实运行记录背书（%s）—— 凭据不可信，疑似手工伪造。" % _why)
        out["hint"] = ("请真实运行: python sw_bridge.py physics-optimize "
                       "<load_case.json> --room %s" % str(room))
    _overall = str(att.get("overall") or "").upper()
    if _overall == "FAIL":
        _failed = att.get("failed_gates") or []
        out["reasons"].append(
            "物理校核判定为 FAIL（未通过闸口: %s）"
            % (", ".join(map(str, _failed)) or "见报告"))
    elif _overall not in ("PASS", "REVIEW"):
        out["reasons"].append("物理校核结论缺失或无法识别（overall=%r）" % att.get("overall"))
    # 疲劳闸口：明确 FAIL 也阻断
    if str(att.get("fatigue_verdict") or "").upper() == "FAIL":
        out["reasons"].append("疲劳/寿命校核未通过")
    if not out["reasons"]:
        out["ok"] = True
        out["level"] = "WARN" if _overall == "REVIEW" else "PASS"
        if _overall == "REVIEW":
            out["reasons"].append("物理校核为 REVIEW（有需复核项），已放行但留痕")
    return out


def check_domain_attestation(room):
    out = {"ok": False, "level": "BLOCK", "reasons": [], "room": str(room)}
    att = _read_json(domain_attest_path(room))
    if not att:
        out["reasons"].append(
            "缺少领域校验凭据（reports/%s.domain.json）—— 未跑 DSVA/GB 规则校验。"
            % str(room))
        out["hint"] = ("在房间内执行: python sw_bridge.py physics-validate-domain "
                       "<domain> --room %s" % str(room))
        return out
    # ── 【主防线】先验签 ──────────────────────────────────────────────
    _sig = credential_is_signed_and_valid(att)
    if not _sig["ok"]:
        out["reasons"].append("领域凭据未通过签名校验：%s" % _sig["reason"])
        out["hint"] = ("领域凭据必须由宿主签发（physics-validate-domain 会自动请求）。"
                       "手工写入的 JSON 无法通过验签。")
        return out
    # ── 【FL-01 修复】领域凭据必须校验 room 归属（防跨房间挪用）────────────
    # 实测缺陷：对 room="CF房间A" 签发的 domain 凭据，复制到
    #   reports/CF房间B.domain.json 后，校验【PASS】未被识别 ——
    #   因为本函数只校验 domain/rules_file/violations/score/passed/run_id，
    #   唯独漏了 att.room 与目标房间的一致性（physics 凭据有此校验，
    #   domain 凭据缺，属单防线置信漏洞；虽有 physics 线兜底，仍应补齐）。
    _att_room = str(att.get("room") or "").strip()
    if _att_room and _att_room != str(room):
        out["reasons"].append(
            "领域凭据归属房间为 %r，与被校验房间 %r 不一致 —— "
            "疑似挪用其他房间的凭据（FL-01）" % (_att_room, str(room)))
        out["room_mismatch"] = True
    # ── 【防伪造】领域凭据必须指明 domain，且该 domain 的规则文件真实存在 ──
    _dom = str(att.get("domain") or "").strip()
    if not _dom:
        out["reasons"].append("领域凭据未声明 domain，无法确认校验基于哪套规则（疑似伪造）")
    else:
        _rules = os.path.join(_BASE_DIR, "physics", "rules", _dom + "_rules.json")
        if not os.path.exists(_rules):
            out["reasons"].append(
                "领域凭据声明 domain=%r，但规则文件 %s 不存在（疑似伪造）"
                % (_dom, os.path.basename(_rules)))
        else:
            out["rules_file"] = _rules
    _v = att.get("violations")
    if not isinstance(_v, list):
        out["reasons"].append("violations 字段不是数组（凭据结构异常，疑似伪造/损坏）")
        _v = []
    # [防伪造] 只写 violations=[] 是无成本的 —— 必须能证明"真的跑过校验"：
    #   要求凭据携带 score 或 passed 项数，且二者至少有一项存在。
    _score = att.get("score")
    _passed = att.get("passed")
    if _score is None and not isinstance(_passed, list):
        out["reasons"].append(
            "领域凭据既无 score 也无 passed 清单 —— 无法证明真的执行过 DSVA 校验"
            "（空 violations 不足以作为通过依据，疑似伪造）")
    # run_id/时间戳：领域校验同样要有可追溯标识
    if not str(att.get("run_id") or "").strip() and not str(att.get("checked_at") or "").strip():
        out["reasons"].append("领域凭据缺少 run_id/checked_at 之类的可追溯标识（疑似伪造）")
    out["attestation"] = {
        "domain": att.get("domain"),
        "score": att.get("score"),
        "violations": len(_v),
        "at": att.get("at"),
    }
    if _v:
        _ids = [str(x.get("rule_id")) for x in _v if isinstance(x, dict)]
        out["reasons"].append(
            "存在 %d 条 CRITICAL 领域违规: %s"
            % (len(_v), ", ".join(_ids[:6]) or "见凭据"))
    if not out["reasons"]:
        out["ok"] = True
        out["level"] = "PASS"
    return out


# ══════════════════════════════════════════════════════════════════════
#  汇总：房间 / 任务
# ══════════════════════════════════════════════════════════════════════

def _registered_artifacts(room):
    """读 artifacts_registry.json，取本房间登记的产物（含存在性）。"""
    reg = _read_json(ARTIFACT_REGISTRY_FILE) or {}
    rec = reg.get(str(room))
    if not isinstance(rec, list):
        return []
    out = []
    for it in rec:
        if not isinstance(it, dict):
            continue
        p = it.get("path")
        if p:
            out.append({"path": p, "name": it.get("name"),
                        "exists": os.path.exists(p)})
    return out


def _modeling_parts(room):
    """本房间登记的【零件】产物（.sldprt），排除装配体/图纸。"""
    out = []
    for a in _registered_artifacts(room):
        _p = str(a.get("path") or "")
        if _p.lower().endswith(".sldprt") and a.get("exists"):
            out.append(a)
    return out


def _room_types():
    """从 workflow_state.subagent_config.rooms 取 房间名 → 类型 映射。"""
    st = _read_json(os.path.join(STATE_DIR, "workflow_state.json")) or {}
    cfg = st.get("subagent_config") or {}
    out = {}
    for row in (cfg.get("rooms") or []):
        if isinstance(row, (list, tuple)) and len(row) >= 2:
            out[str(row[0])] = str(row[1])
        elif isinstance(row, dict):
            _n = row.get("name") or row.get("room")
            if _n:
                out[str(_n)] = str(row.get("type") or "")
    return out


def check_room_defense(room, require_physics=None):
    """校验一个房间的三大防线。

    require_physics: None = 按房间类型自动判定（建模类房间强制）；
                     True/False = 显式覆盖。

    Returns: {ok, room, blockers[], warnings[], details{}}
    """
    room = str(room)
    rtypes = _room_types()
    rtype = rtypes.get(room, "")
    if require_physics is None:
        require_physics = (rtype in MODELING_ROOM_TYPES) or (rtype == "")

    details = {}
    blockers = []
    warnings = []

    # ── 【C 方案】宿主优先判定 ────────────────────────────────────────────
    # 为什么：Python 侧信任的是"某个 HTTP 端点"，该端点可被同用户进程冒充
    #   （对抗测试实证：伪造 runtime.json 指向恒真服务即可整体绕过）。
    #   把判定权收回宿主后，AI 在 Python 侧不再有可冒充的判定点。
    #   仅在"宿主可用且端口归属校验通过"时走此路径；否则回落到本地判定
    #   并明确标注降级原因（fail-closed 语义不变：本地判定对签名同样严格）。
    _host_note = None
    # ── 【MA-04 / FL-03 修复】降级必须带【结构化原因】───────────────────
    # 实测缺陷：runtime.json 被指向 9999 死端口时，校验确实 fail-closed 回落
    #   本地判定（judged_by="local"），但 degraded_note 为空字符串 ——
    #   排障时看不出"为什么没走宿主判定"，只能靠猜。
    # 现在同时给出：
    #   degraded_reason_code —— 机器可判定的枚举
    #   degraded_note        —— 人类可读原因（报告明确点名这个字段）
    _degrade_code = None
    try:
        _rt = _read_runtime()
        if _rt.get("port"):
            _own = _port_owner_check(_rt.get("pid"), _rt.get("port"))
            if _own.get("ok"):
                _hj = host_judge_room(room, require_physics=bool(require_physics))
                if isinstance(_hj, dict) and "blockers" in _hj:
                    _hj.setdefault("room", room)
                    _hj.setdefault("room_type", rtype if "rtype" in dir() else None)
                    _hj["judged_by"] = "host"
                    return {
                        "ok": bool(_hj.get("ok")),
                        "room": room,
                        "room_type": (rtypes.get(room, "") or None),
                        "require_physics": bool(require_physics),
                        "blockers": _hj.get("blockers") or [],
                        "warnings": _hj.get("warnings") or [],
                        "details": _hj.get("details") or {},
                        "judged_by": "host",
                    }
                _degrade_code = "HOST_JUDGE_NO_RESPONSE"
                _host_note = ("宿主判定端点无响应（可能端口不通/宿主重启中），"
                              "已回落本地判定；本地判定对签名同样严格（fail-closed）")
            else:
                _degrade_code = "PORT_OWNER_MISMATCH"
                _host_note = ("宿主端口归属校验未通过（%s），已回落本地判定 —— "
                              "常见原因：runtime.json 指向的不是 DSH 主进程"
                              "（被改写/残留旧文件）" % _own.get("reason"))
        else:
            _degrade_code = "SIGNER_NOT_ASSEMBLED"
            _host_note = ("签名服务未装配（runtime.json 缺失或无私端口）—— "
                          "请重启 DSH 使签名端点生效；"
                          "期间防线以 fail-closed 方式回落本地判定")
    except Exception as _e_host:
        _host_note = "宿主判定异常（%r），已回落本地判定" % (_e_host,)

    # ── ① 材料：本房间所有零件 ──
    parts = _modeling_parts(room)
    mat_results = []
    if parts:
        for a in parts:
            r = check_material_attestation(a["path"])
            r["part_name"] = a.get("name") or os.path.basename(a["path"])
            mat_results.append(r)
            if not r["ok"]:
                blockers.append("【防线①材料】%s: %s"
                                % (r["part_name"], "；".join(r["reasons"])))
            elif r["level"] == "WARN":
                warnings.append("【防线①材料】%s: %s"
                                % (r["part_name"], "；".join(r["reasons"])))
    else:
        mat_results = []
    details["material"] = {"part_count": len(parts), "results": mat_results}

    # ── ② 物理 ──
    if require_physics:
        ph = check_physics_attestation(room)
        details["physics"] = {k: v for k, v in ph.items() if k != "hint"}
        if not ph["ok"]:
            blockers.append("【防线②物理】" + "；".join(ph["reasons"]))
            details["physics_hint"] = ph.get("hint")
        elif ph["level"] == "WARN":
            warnings.append("【防线②物理】" + "；".join(ph["reasons"]))
        # ── ③ 领域 ──
        dm = check_domain_attestation(room)
        details["domain"] = {k: v for k, v in dm.items() if k != "hint"}
        if not dm["ok"]:
            blockers.append("【防线③领域】" + "；".join(dm["reasons"]))
            details["domain_hint"] = dm.get("hint")
    else:
        details["physics"] = {"skipped": True, "reason":
                              "房间类型 %r 非建模类，本房间不做物理/领域强制" % rtype}
        details["domain"] = {"skipped": True}

    if _host_note:
        details["host_note"] = _host_note
    # ── 【MA-04 / FL-03 修复】降级原因必须显式可读 ──────────────────────
    # 报告点名 degraded_note 为空；这里补齐三段：
    #   judged_by / degraded / degraded_reason_code / degraded_note
    # 让调用方一眼看出"本次是谁判的、为什么没用宿主判定"。
    _out_local = {
        "ok": not blockers, "room": room, "room_type": rtype or None,
        "require_physics": bool(require_physics),
        "blockers": blockers, "warnings": warnings, "details": details,
        "judged_by": "local",
        "degraded": bool(_degrade_code),
    }
    if _degrade_code:
        _out_local["degraded_reason_code"] = _degrade_code
        _out_local["degraded_note"] = _host_note or (
            "宿主判定不可用，已回落本地判定（原因代码 %s）" % _degrade_code)
    else:
        _out_local["degraded_note"] = ""
    return _out_local


def _all_rooms():
    """全部房间名（mode_state.rooms ∪ workflow_state.subagent_config.rooms）。"""
    names = set()
    ms = _read_json(os.path.join(STATE_DIR, "mode_state.json")) or {}
    for n in (ms.get("rooms") or {}):
        names.add(str(n))
    for n in _room_types():
        names.add(str(n))
    return sorted(names)


def check_task_defense():
    """全任务三防线汇总（交付前总门禁）。"""
    rooms = {}
    blockers = []
    warnings = []
    for room in _all_rooms():
        r = check_room_defense(room)
        rooms[room] = r
        blockers.extend(r["blockers"])
        warnings.extend(r["warnings"])
    # 任务级额外要求：至少存在一份【通过】的物理凭据
    # [防伪造] 必须"PASS 且有真实运行背书"，只看字面 overall 会被 C1c/C1d 骗过。
    phys_pass = []
    for r in rooms.values():
        _ph = (r.get("details", {}) or {}).get("physics") or {}
        if str(_ph.get("overall") or "").upper() != "PASS":
            continue
        if not _ph.get("backed_by_real_run"):
            continue
        phys_pass.append(r)
    if not phys_pass:
        blockers.append(
            "【防线②物理】全任务没有任何一份【有真实运行背书】的 PASS 物理凭据 —— "
            "未做过（可追溯的）强度/寿命校核的任务不得交付")
    return {"ok": not blockers, "blockers": blockers, "warnings": warnings,
            "rooms": rooms, "room_count": len(rooms)}


def log_bypass(where, reason="", by="unknown"):
    """记录一次绕行（绝不静默绕过）。"""
    try:
        items = _read_json(BYPASS_LOG_FILE) or {"items": []}
        if not isinstance(items, dict) or not isinstance(items.get("items"), list):
            items = {"items": []}
        items["items"].append({
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "where": str(where), "reason": str(reason), "by": str(by),
        })
        items["items"] = items["items"][-200:]
        _write_json(BYPASS_LOG_FILE, items)
    except Exception:
        pass


def bypass_requested():
    return str(os.environ.get("DSH_DEFENSE_BYPASS") or "").strip() in ("1", "true", "yes")


# ══════════════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════════════

def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = sys.argv[1:]
    cmd = args[0] if args else "status"
    if cmd == "check-room":
        room = args[1] if len(args) > 1 else ""
        if not room:
            print(json.dumps({"ok": False, "error": "需要房间名"}, ensure_ascii=False))
            sys.exit(2)
        r = check_room_defense(room)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        sys.exit(0 if r["ok"] else 1)
    if cmd == "check-task":
        r = check_task_defense()
        print(json.dumps(r, ensure_ascii=False, indent=2))
        sys.exit(0 if r["ok"] else 1)
    if cmd == "status":
        _rt = _read_runtime()
        # ── 【P0-3 修复】回报【真正生效】的 runtime.json 路径 ──────────────
        # 原先固定回报 RUNTIME_FILE（脚本自身目录下那份），而实际生效的是
        #   _find_runtime_file() 按多候选回退命中的那份。当两份副本分裂时，
        #   该字段会指向一个【并不存在】的路径，把排障引向错误方向。
        _rt_path = _find_runtime_file()
        _host = {"assembled": bool(_rt.get("port")),
                 "runtime_file": _rt_path or RUNTIME_FILE,
                 "runtime_file_exists": bool(_rt_path),
                 "expected_local_path": RUNTIME_FILE,
                 "kid": _rt.get("kid"), "port": _rt.get("port"), "pid": _rt.get("pid")}
        if not _host["assembled"]:
            _host["action_required"] = "签名服务未装配：请【重启 DSH】使宿主加载签名端点（否则凭据无法签发，防线必然不通过）"
        else:
            _host["owner_check"] = _port_owner_check(_rt.get("pid"), _rt.get("port"))
        print(json.dumps({
            "ok": True,
            "host_signature": _host,
            "rooms": _all_rooms(),
            "room_types": _room_types(),
            "reports_dir": REPORTS_DIR,
            "bypass_env": bypass_requested(),
        }, ensure_ascii=False, indent=2))
        return
    print(json.dumps({"ok": False, "error": "unknown command: %s" % cmd,
                      "usage": "defense_gate.py check-room <room> | check-task | status"},
                     ensure_ascii=False))
    sys.exit(2)


if __name__ == "__main__":
    main()
