# -*- coding: utf-8 -*-
"""
simulation_report.py — 结构化仿真报告生成
==========================================
将 FEA 求解结果、几何门禁结果、设计参数汇总为标准 JSON 报告。
报告可作为 Agent 上下文输入，也可导出为 Markdown 摘要。
"""
import json
import os
import time
from typing import Any, Optional


def _material_provenance_note(mat):
    """【可审计性】把材料数值来源压缩成一句话，写进报告。

    为什么需要：国标牌号的"标准身份"与"力学数值"是两件事。当数值尚未从
      标准原文补全时，系统会用项目原有的等效材料（欧美标）兜底，保证
      FEA 仍可运行。此时报告必须【明说】用的是兜底值 —— 否则读者会
      误以为 235 MPa 就是 GB/T 700 的原文值，等于用兜底冒充国标。
    Returns: dict 或 None
    """
    if not isinstance(mat, dict) or not mat:
        return None
    _pending = mat.get("values_pending")
    _from_gb = mat.get("values_from_gb")
    _src = mat.get("values_source")
    _eq = mat.get("equivalent_name")
    _std = mat.get("standard") or mat.get("name") or ""
    _gb_fields = list(mat.get("gb_sourced_fields") or [])
    _fp = mat.get("field_provenance") if isinstance(
        mat.get("field_provenance"), dict) else {}
    if not _gb_fields and _fp:
        _gb_fields = [k for k, v in _fp.items()
                      if isinstance(v, dict) and v.get("source") == "GB_STANDARD"]
    # ── 【BUG 修复·扫描页字段未计入】GB_SCAN 字段也算"有国标依据" ─────────
    # 实测缺陷：2A12 的 σy/σb 来自 GB/T 6892-2023，但该标准官方 PDF 是
    #   扫描图片无法文本提取，故按【手册】填 → 层级标 GB_SCAN（不是
    #   GB_STANDARD）。于是 gb_sourced_fields 为空 → 报告落到 UNKNOWN，
    #   写"来源未标注"。而实际上它【有国标出处】，只是数值取自手册。
    #   这属于"有国标依据但数值非原文"，应归入 MIXED_SOURCE 并如实说明。
    _scan_fields = []
    if _fp:
        _scan_fields = [k for k, v in _fp.items()
                        if isinstance(v, dict)
                        and v.get("source") in ("GB_SCAN", "GB_HARDNESS_ONLY")]
    _gb_or_scan = list(_gb_fields) + [f for f in _scan_fields
                                      if f not in _gb_fields]

    # ── 【BUG 修复·身份冒充】不得用 values_pending 反推"来自国标" ──────────
    # values_pending 的语义是"兜底后【仍缺】字段"：
    #   40Cr 经兜底后字段齐了 → False，但 gb_sourced_fields=[]（无一来自 GB）。
    # 旧代码 `if _from_gb is True or _pending is False` 会把 40Cr 判成
    #   level=GB_NATIVE / claimable_as_gb=True，报告原文写
    #   "力学数值来自 GB 标准原文（GB/T 3077-2015）" ——
    #   而实际是 AISI 4140 的 655/760。这正是本系统最该杜绝的"兜底冒充国标"。
    # 判据改为：只有【每个已填字段都在 gb_sourced_fields 里】才可宣称 GB_NATIVE。
    _filled = [f for f in ("E_mpa", "nu", "yield_mpa", "uts_mpa", "density_kg_m3")
               if mat.get(f) is not None]
    _all_from_gb = bool(_filled) and set(_filled) <= set(_gb_fields)

    if _all_from_gb:
        return {
            "level": "GB_NATIVE",
            "claimable_as_gb": True,
            "gb_sourced_fields": _gb_fields,
            "note": ("力学数值全部来自 GB 标准原文（%s），可用于国标口径的结论"
                     % (_std or "标准号未标注")),
        }
    if _pending is True and not _eq:
        return {
            "level": "PENDING_NO_FALLBACK",
            "claimable_as_gb": False,
            "missing_fields": [f for f in
                               ("E_mpa", "nu", "yield_mpa", "uts_mpa",
                                "density_kg_m3") if mat.get(f) is None],
            "note": ("该牌号仅确认标准身份，力学数值待补全且无等效兜底材料 —— "
                     "强度/寿命类结论不可用，请从标准原文补全数值"),
        }
    if _eq:
        return {
            "level": "EQUIVALENT_FALLBACK",
            "claimable_as_gb": False,
            "equivalent_material": _eq,
            "gb_sourced_fields": _gb_fields,
            "note": ("力学数值【不是】全部来自 GB 原文：%s 来自 GB 原文，"
                     "其余为等效材料「%s」的数值兜底；结论可用于工程判断，"
                     "但不得整体宣称为 GB/T 标准值。"
                     % (", ".join(_gb_fields) if _gb_fields else "无字段", _eq)),
        }
    if _pending is True:
        return {
            "level": "PENDING_NO_FALLBACK",
            "claimable_as_gb": False,
            "note": ("该牌号仅确认标准身份，力学数值待补全且无等效兜底材料 —— "
                     "强度/寿命类结论不可用，请从标准原文补全数值"),
        }
    # ── 【BUG 修复·混合来源落 UNKNOWN】必须单列 MIXED_SOURCE ──────────────
    # 实测缺陷（子代理发现，9/18 牌号受影响：304/316/45#/65Mn/H62/Q235/Q355/
    #   QT500-7/ZCuSn10P1）：这些牌号 σy/σb 来自 GB 原文，但 E/ν/ρ 是手册值
    #   （GB 产品标准本就不列这些材料常数）—— 属【混合来源】。
    #   旧逻辑的分支只覆盖"全 GB""全兜底""全缺"，混合情形全部落到 UNKNOWN，
    #   于是报告写"材料数值来源未标注…无法确认是否为国标原文值"，
    #   而同一份报告的 field_provenance 明明写着 yield_mpa=GB_STANDARD ——
    #   报告自我矛盾。
    # 现在：有 GB 值但非全部 → MIXED_SOURCE，并分层说明：
    #   · 强度判据（σy/σb）可宣称国标口径
    #   · 刚度判据（E/ν）不可宣称（手册值）
    if _gb_fields or _scan_fields:
        _hb = list(mat.get("handbook_fields") or [])
        if not _hb and _fp:
            _hb = [k for k, v in _fp.items()
                   if isinstance(v, dict)
                   and v.get("source") in ("HANDBOOK", "GB_SCAN",
                                           "GB_HARDNESS_ONLY")]
        _strength_gb = [f for f in _gb_fields
                        if f in ("yield_mpa", "uts_mpa")]
        _stiff_gb = [f for f in _gb_fields if f in ("E_mpa", "nu")]
        # ── 【P1-2 修复·过度宣称】GB_SCAN 字段不得计入"可宣称国标" ──────────
        # 实测缺陷（子代理发现）：40Cr/2A12/20CrMnTi/6061-T6/7075-T6 的
        #   σy/σb 来源是 GB_SCAN，而 gb_materials.json 自己定义 GB_SCAN =
        #   "国标扫描页无法文本提取，【按手册填】"，
        #   material_db._GB_CLAIMABLE 也只认 GB_ORIGINAL/GB_GRADE_DEF。
        #   但报告给出的机器可读布尔量 strength_claimable_as_gb 却是 true ——
        #   下游（自动化脚本/验收）会据此以为"强度值可宣称符合国标"。
        #   文字 note 是诚实的，但布尔量必须与之一致。
        # 现在：strength_claimable_as_gb 只统计【真正的国标原文/牌号定义】字段；
        #   若某牌号的 σy/σb 全部来自 GB_SCAN，则为 False，
        #   并单独给出 scan_sourced_fields 供读者判断。
        _strength_strict = [f for f in _strength_gb]
        _scan_strength = [f for f in _scan_fields
                          if f in ("yield_mpa", "uts_mpa")]
        _scan_note = ("；其中 %s 有国标出处但数值取自手册"
                      "（该标准官方 PDF 为扫描页，无法文本提取）"
                      % ", ".join(_scan_fields)) if _scan_fields else ""
        return {
            "level": "MIXED_SOURCE",
            "claimable_as_gb": False,
            "gb_sourced_fields": _gb_fields,
            "scan_sourced_fields": _scan_fields,
            "handbook_fields": _hb,
            # 【分层可宣称性】机器可读判据必须与 note 文字一致：
            #   只有 GB_ORIGINAL / GB_GRADE_DEF 的 σy/σb 才算"可宣称国标"
            "strength_claimable_as_gb": bool(_strength_strict) and not _eq,
            "stiffness_claimable_as_gb": bool(_stiff_gb) and not _eq,
            # 若强度值来自扫描页（按手册填），单独标出，避免被误读
            "strength_from_scan_only": (bool(_scan_strength)
                                        and not _strength_strict),
            "note": ("混合来源：%s 来自国标原文/牌号定义；%s 为手册值"
                     "（GB 产品标准不列这些材料常数）%s。"
                     "%s%s%s"
                     % (", ".join(_gb_fields) or "无",
                        ", ".join(_hb) or "无",
                        _scan_note,
                        ("强度判据（σy/σb）可宣称国标口径；"
                         if _strength_strict else
                         ("强度值（σy/σb）取自手册（国标扫描页无法提取），"
                          "【不得】宣称符合国标；" if _scan_strength else "")),
                        ("刚度/位移判据（E/ν）为手册值，不可宣称国标口径"
                         if (_hb or not _stiff_gb) else ""),
                        "")),
        }
    return {
        "level": "UNKNOWN",
        "claimable_as_gb": False,
        "note": ("材料数值来源未标注（values_source=%r）—— 无法确认是否为国标原文值，"
                 "引用结论时请人工核实" % (_src,)),
    }


def build_report(
    run_id: str,
    load_case: dict,
    design_params: dict,
    geometry_check: dict,
    fea_result: dict,
    iteration: int = 1,
    parent_run_id: Optional[str] = None,
) -> dict[str, Any]:
    """构建完整仿真报告。

    Args:
        run_id: 本次运行唯一标识
        load_case: 载荷工况字典
        design_params: 当前设计参数
        geometry_check: geometry_gate 输出
        fea_result: fea_solver 输出
        iteration: 当前迭代轮次
        parent_run_id: 上一轮运行 ID（用于追踪链）

    Returns:
        完整报告字典，可 JSON 序列化保存
    """
    mat = load_case.get("material", {})
    acc = load_case.get("acceptance", {})

    # ── 【NEW-01 修复】acceptance_criteria 必须【完整透传】全部验收参数 ────
    # 原缺陷：只透传 SF 上下限 / 位移 / 分析类型 5 个字段，把 30 年寿命、
    #   年循环次数、负载类型、冲击系数、可靠度、表面加工等【疲劳相关参数
    #   全部丢掉】。后果：报告顶层 acceptance_criteria 看不到 30 年寿命，
    #   下游工具（refine_rules / 第三方消费方）无从得知判据 ——
    #   虽然 fatigue 子模块自己去读 load_case 算对了，但"判据"与"结论"
    #   分处两地，审计与复现都困难。
    # 修复：以 load_case.acceptance 为基准【整表透传】，再补默认值兜底。
    _acc_out = dict(acc) if isinstance(acc, dict) else {}
    _acc_out.setdefault("min_safety_factor", 2.0)
    _acc_out.setdefault("max_safety_factor", _acc_out.get("target_safety_factor_max", 5.0))
    _acc_out.setdefault("max_displacement_mm", None)
    _acc_out.setdefault("analysis_type", "linear_static")

    # 综合判定
    overall = fea_result.get("overall", "UNKNOWN")
    if geometry_check.get("status") == "FAIL":
        overall = "FAIL"
    elif geometry_check.get("status") == "WARNING" and overall != "FAIL":
        overall = "REVIEW"
    # ── 【BUG-02 修复】疲劳/寿命结论必须参与整体判定 ─────────────────────
    # 原实现完全不看疲劳，导致"静力过了但 30 年寿命不够"的零件被误判为 PASS。
    _fat = fea_result.get("fatigue") or {}
    _fat_verdict = _fat.get("verdict")
    if _fat_verdict == "FAIL":
        overall = "FAIL"
    elif _fat_verdict in ("REVIEW", "NOT_EVALUATED") and overall == "PASS":
        overall = "REVIEW"

    report = {
        "schema_version": "1.1",
        "run_id": run_id,
        "iteration": iteration,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "parent_run_id": parent_run_id,

        "load_case_summary": {
            "problem_id": load_case.get("meta", {}).get("problem_id", "?"),
            "title": load_case.get("meta", {}).get("title", "?"),
            "material": mat.get("name", "?"),
            "material_id": mat.get("id", ""),
            "yield_strength_mpa": mat.get("yield_strength_mpa", 0),
            "E_mpa": mat.get("youngs_modulus_mpa", 0),
            # ── 【BUG-05/08 修复】material 缺失时必须显式报错，而不是显示 '?' ──
            "material_resolved": bool(mat.get("name") or mat.get("id")),
            "density_kg_m3": mat.get("density_kg_m3", 0),
        },

        # ── 【国标材料修复】报告携带【完整材料块】───────────────────────
        # 国标材料合规校验（domain=gb_material，见 physics/rules/
        #   gb_material_verify_rules.json）需要屈服/密度/抗拉/弹性模量。
        #   原先报告只在 load_case_summary 里散落几个字段，领域防线取不全 →
        #   "无数据 → CRITICAL"，国标材料数据白白躺在库里用不上。
        #   现在原样落一份 material，使 GB 材料校验有可靠数据源。
        "material": ({
            "id": mat.get("id", ""),
            "name": mat.get("name", ""),
            "youngs_modulus_mpa": mat.get("youngs_modulus_mpa", 0),
            "poissons_ratio": mat.get("poissons_ratio", 0),
            "yield_strength_mpa": mat.get("yield_strength_mpa", 0),
            "uts_mpa": mat.get("uts_mpa", 0),
            "density_kg_m3": mat.get("density_kg_m3", 0),
            "standard": mat.get("standard", ""),
            "source": mat.get("source", ""),
            # ── 【数值来源可审计】报告必须能区分"国标数值"与"等效兜底值"────
            # 实测缺陷（子代理发现）：这些标记原先在 _props_from_name 就被丢掉，
            #   报告里全文搜不到 values_source —— 于是从报告上【看不出】
            #   235 MPa 是 GB/T 700 原文值还是 AISI 1020 的等效值。
            #   这正是"用兜底值冒充国标值"的风险点，防线也拦不住。
            #   现在原样落盘，并在下方给出人类可读的结论字段。
            "values_source": mat.get("values_source"),
            "values_from_gb": mat.get("values_from_gb"),
            "values_pending": mat.get("values_pending"),
            "identity_source": mat.get("identity_source"),
            "equivalent_name": mat.get("equivalent_name"),
            "equivalent_id": mat.get("equivalent_id"),
            # 【BUG 修复】逐字段台账与国标字段清单必须落进报告 ——
            #   否则"哪个数来自国标"在报告里读不出来（核心审计意图失效）。
            "gb_sourced_fields": mat.get("gb_sourced_fields"),
            "field_provenance": mat.get("field_provenance"),
            "fallback_fields": mat.get("fallback_fields"),
            "native_fields": mat.get("native_fields"),
            "consistency_guard": mat.get("consistency_guard"),
            # ── 【新 P0 修复】如实用料来源（零件本体 / 载荷文件 / 显式指定）──
            # 实测缺陷：Q235 零件被按载荷文件的 PETG 校核，报告里看不出这一点。
            #   这里把 material_resolution 一并落盘，使"校核用的材料从哪来"
            #   可追溯，避免再次出现"国标材料在物理校核里被悄悄顶掉"。
            "resolution": (load_case.get("material_resolution") or None),
        } if mat else None),
        # 材料口径来源单独成块（便于审计与前端展示）
        "material_resolution": (load_case.get("material_resolution") or None),
        # ── 【一句话结论】材料数值是否可直接用于国标宣称 ──────────────────
        # 让报告读者不必翻 JSON 就能知道：这组数值是不是国标原文值。
        "material_provenance": _material_provenance_note(mat),

        "design_params": design_params,
        # ── 【Bug12 修复】报告必须携带【真实几何】──────────────────────
        # cmd_validate_domain 会从最近一次报告里读 real_geometry 来推导
        #   设计参数（thickness_mm/fillet_mm/wall_thickness_mm 等）。
        #   原报告不落该字段，导致领域规则拿不到设计参数、全报"无数据"。
        "real_geometry": (load_case.get("real_geometry") or None),

        "geometry_gate": geometry_check,

        "fea_result": fea_result,

        # ── 【BUG-02 修复】疲劳/设计寿命独立成块，便于直接读取结论 ─────────
        "fatigue": _fat,

        "acceptance_criteria": _acc_out,
        # ── 【Bug17 残留修复】顶层必须暴露【真实安全系数】──────────────
        # 测试部实测：对外汇总的 min_safety_factor 被钳制到 acceptance 阈值
        #   (2.5)，与 gate 层的 actual_sf（46.738 / 0.284）不一致，
        #   下游看到"min_safety_factor=2.5"会误以为真实 SF 就是 2.5。
        # 修复：顶层显式给出【实际值】与【要求值】，两者分开、顾名思义：
        #   actual_safety_factor          —— 真实计算值（随载荷变化）
        #   required_min_safety_factor    —— 验收下限（来自 acceptance）
        #   required_max_safety_factor    —— 目标上限（过度设计判据）
        "actual_safety_factor": fea_result.get("safety_factor"),
        "required_min_safety_factor": _acc_out.get("min_safety_factor"),
        "required_max_safety_factor": _acc_out.get("max_safety_factor"),

        "overall_status": overall,
        "passed_gates": _extract_passed(geometry_check, fea_result),
        "failed_gates": _extract_failed(geometry_check, fea_result),
        "not_evaluated": _extract_not_evaluated(geometry_check, fea_result),

        "release_readiness": {
            "status": _release_status(overall, fea_result, acc),
            "human_review_required": overall != "PASS",
            "limitations": fea_result.get("limitations", []),
            # 【BUG-02】交付前必须明确"寿命是否已验证"
            "fatigue_verified": _fat_verdict in ("PASS", "FAIL", "REVIEW"),
            "fatigue_verdict": _fat_verdict,
        },
    }
    # ── 【BUG-05 修复】材料未赋/密度虚标（1000=水）必须作为显式告警 ───────
    warnings = []
    if not report["load_case_summary"]["material_resolved"]:
        warnings.append("材料未解析（material 字段为空）—— 强度/寿命/质量结论均不可信")
    _rho = mat.get("density_kg_m3") or 0
    # ── 【P0 修复·告警失灵】必须检查【零件实测密度】，不能只看 material 块 ──
    # 实测缺陷（子代理在真实流程中复现）：旧代码只看 material.density_kg_m3，
    #   而该值曾被回填成【期望密度】(7850)，于是"零件真实密度=1000（水，
    #   即根本没赋上材）"这一事实被掩盖 → "密度=1000 等同水"的告警从未触发。
    #   现在同时检查 real_geometry.density_kg_m3（零件客观事实）。
    _rg = report.get("real_geometry") or {}
    _rho_parts = _rg.get("density_kg_m3")
    for _tag, _val in (("校核口径 material", _rho),
                       ("零件实测 real_geometry", _rho_parts)):
        try:
            _v = float(_val)
        except Exception:
            continue
        if _v and abs(_v - 1000.0) < 1.0:
            warnings.append(
                "【%s】材料密度为 1000 kg/m³（等同水）—— 极可能是 SolidWorks "
                "未赋材质导致的默认值，质量属性与强度校核全部失真" % _tag)
    # 两者不一致 → "校核用的密度"与"零件真实密度"不同，必须显式告警
    try:
        if _rho and _rho_parts and abs(float(_rho) - float(_rho_parts)) > 1.0:
            warnings.append(
                "校核口径密度(%s) 与 零件实测密度(%s) 不一致 —— 材料可能未真正"
                "赋到零件上，或凭据与零件脱节；质量/重力结论不可信"
                % (_rho, _rho_parts))
    except Exception:
        pass
    # 赋材未生效（凭据显式标记）
    if mat.get("material_applied") is False:
        warnings.append("赋材未生效：%s" % (mat.get("material_apply_note")
                                            or "零件材料未真正写入"))
    if _fat_verdict == "NOT_EVALUATED":
        warnings.append("疲劳/寿命校核未执行：%s" % _fat.get("error", "未知原因"))
    if warnings:
        report["warnings"] = warnings
    return report


# ══════════════════════════════════════════════════════════════════════
#  闸口归属表 —— 【Bug18 修复】消除"两层 DESIGN_SPACE 结论相反"
# ══════════════════════════════════════════════════════════════════════
#  问题：同一次校核里 fea_result.gates.DESIGN_SPACE=PASS，而
#        geometry_gate 的 DESIGN_SPACE=FAIL —— 两层各执一词，用户无法判断
#        该信谁（测试部实测承载板 200×60×10 即为此矛盾）。
#
#  根因：一个闸口 ID 被【两层同时判定】。空间是否越界本质是几何问题，
#        FEA 层只做力学，没有资格给结论。
#
#  修复：为每个闸口声明唯一归属；聚合 passed/failed 时，凡是有归属的闸口
#        【只采信归属层】，另一层即使给出结论也一律忽略。这样两层不可能
#        再出现相反结论 —— 归属层是唯一事实源。
GATE_OWNER = {
    # 几何层负责
    "DESIGN_SPACE": "geometry",
    "CONNECTIVITY": "geometry",
    "WALL_THICKNESS": "geometry",
    # 力学层负责
    "SAFETY_FACTOR": "fea",
    "MAX_DISPLACEMENT": "fea",
    "FATIGUE": "fea",
    "MESH_QUALITY": "fea",
}


def _gate_status_of(owner: str, gid: str, gc: dict, fr: dict):
    """按归属层取某闸口的状态；不属于该层或未给出则返回 None。"""
    if owner == "geometry":
        for gate in gc.get("gates", []):
            if gate.get("id") == gid:
                return gate.get("status"), gate
        return None, None
    val = (fr.get("gates") or {}).get(gid)
    if isinstance(val, dict):
        return val.get("status"), {"id": gid, **val}
    return None, None


def _gates_belonging_to(owner: str, gc: dict, fr: dict) -> list:
    """列出归属该层的全部闸口条目（几何层取 gates[]，力学层取 gates{}）。"""
    ids = []
    if owner == "geometry":
        for gate in gc.get("gates", []):
            gid = gate.get("id")
            if gid and GATE_OWNER.get(gid, "geometry") == "geometry":
                ids.append((gid, gate.get("status"), gate))
    else:
        for gid, val in (fr.get("gates") or {}).items():
            if GATE_OWNER.get(gid, "fea") == "fea":
                ids.append((gid, (val or {}).get("status") if isinstance(val, dict) else None,
                            {"id": gid, **(val if isinstance(val, dict) else {})}))
    return ids


def _extract_passed(gc: dict, fr: dict) -> list[str]:
    """【Bug18 修复】按归属层收集 PASS —— 每个闸口只由归属层决定。"""
    passed = []
    # 几何层负责的闸口
    for gid, status, _ in _gates_belonging_to("geometry", gc, fr):
        if status == "PASS":
            passed.append(gid)
    # 力学层负责的闸口
    for gid, status, _ in _gates_belonging_to("fea", gc, fr):
        if status == "PASS":
            passed.append(gid)
    return list(dict.fromkeys(passed))


def _extract_failed(gc: dict, fr: dict) -> list[dict]:
    """【Bug18 修复】按归属层收集 FAIL —— 杜绝两层对同一闸口各执一词。"""
    failed = []
    for gid, status, entry in _gates_belonging_to("geometry", gc, fr):
        if status == "FAIL":
            failed.append(entry)
    for gid, status, entry in _gates_belonging_to("fea", gc, fr):
        if status == "FAIL":
            failed.append(entry)
    return failed


def _extract_not_evaluated(gc: dict, fr: dict) -> list[str]:
    """【Bug18 修复】按归属层收集"未评估"闸口。

    注意：FEA 层把 DESIGN_SPACE 标为 N/A（它不再越权判定），但这【不表示】
    空间未评估 —— 归属层（geometry_gate）已经给出结论。因此这里同样只按
    归属层收集，避免把"归属层已判定"的闸口误报为未评估。
    """
    not_eval = []
    for gid, status, _ in _gates_belonging_to("geometry", gc, fr):
        if status in ("N/A", "NOT_EVALUATED"):
            not_eval.append(gid)
    for gid, status, _ in _gates_belonging_to("fea", gc, fr):
        if status in ("N/A", "NOT_EVALUATED"):
            not_eval.append(gid)
    return list(dict.fromkeys(not_eval))


def _release_status(overall: str, fr: dict, acc: dict) -> str:
    if overall == "PASS":
        sf = fr.get("safety_factor", 0)
        min_sf = acc.get("min_safety_factor", 2.0)
        max_sf = acc.get("target_safety_factor_max", 5.0)
        # Bug-21 修复: 安全系数在范围内 → RELEASE_CANDIDATE；超出上限 → OVER_ENGINEERED
        if min_sf <= sf <= max_sf:
            return "RELEASE_CANDIDATE"
        return "OVER_ENGINEERED"
    elif overall == "FAIL":
        return "NEEDS_REVISION"
    # Bug-21 修复: 安全系数过高但整体 PASS 时，返回 OVER_ENGINEERED 而非 RELEASE_CANDIDATE
    if overall == "WARNING":
        sf = fr.get("safety_factor", 0)
        max_sf = acc.get("target_safety_factor_max", 5.0)
        if sf > max_sf:
            return "OVER_ENGINEERED"
        return "REVIEW_NEEDED"
    return "PENDING"


def save_report(report: dict, output_dir: str) -> str:
    """保存报告为 JSON 文件，并返回文件路径。"""
    os.makedirs(output_dir, exist_ok=True)
    run_id = report["run_id"]
    path = os.path.join(output_dir, f"{run_id}_report.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return path


def report_to_markdown(report: dict) -> str:
    """将报告转为 Markdown 摘要。"""
    lines = [
        f"# Simulation Report: {report['load_case_summary']['problem_id']}",
        f"**Run ID:** {report['run_id']}  **Iteration:** {report['iteration']}",
        f"**Time:** {report['timestamp']}",
        "",
        "## Load Case",
        f"- Problem: {report['load_case_summary']['title']}",
        f"- Material: {report['load_case_summary']['material']}",
        f"- Yield Strength: {report['load_case_summary']['yield_strength_mpa']} MPa",
        "",
        "## Results",
        f"- **Overall Status:** {report['overall_status']}",
        f"- Safety Factor: {report['fea_result'].get('safety_factor', 'N/A')}",
        f"- Max Stress: {report['fea_result'].get('max_von_mises_mpa', 'N/A')} MPa",
        f"- Max Displacement: {report['fea_result'].get('max_displacement_mm', 'N/A')} mm",
        f"- Volume: {report['fea_result'].get('volume_mm3', 'N/A')} mm³",
        f"- Mass: {report['fea_result'].get('mass_kg', 'N/A')} kg",
    ]
    # ── 【BUG-02 修复】Markdown 摘要必须包含疲劳/寿命结论 ─────────────────
    _fat = report.get("fatigue") or (report.get("fea_result") or {}).get("fatigue") or {}
    if _fat:
        lines += [
            "",
            "## Fatigue / Design Life",
            f"- Verdict: **{_fat.get('verdict', 'N/A')}**",
            f"- Endurance Limit Se: {_fat.get('endurance_limit_mpa', 'N/A')} MPa",
            f"- Fatigue Safety Factor: {_fat.get('fatigue_sf', 'N/A')}"
            f" (min {_fat.get('min_required_fatigue_sf', 'N/A')})",
            f"- Design Life: {_fat.get('design_life_years', 'N/A')} years"
            f" @ {_fat.get('cycles_per_year', 'N/A')} cycles/yr",
            f"- Miner Damage @ Design Life: {_fat.get('damage_design_life', 'N/A')} (< 1 通过)",
            f"- Allowable Life: {_fat.get('life_years_allowable', 'N/A')} years",
        ]
        if _fat.get("reason"):
            lines.append(f"- Reason: {_fat['reason']}")
        if _fat.get("verdict") == "NOT_EVALUATED":
            lines.append(f"- ⚠️ 未评估原因: {_fat.get('error', '未知')}")
    lines += [
        "",
        "## Release Readiness",
        f"- Status: {report['release_readiness']['status']}",
        f"- Human Review Required: {report['release_readiness']['human_review_required']}",
        f"- Fatigue Verified: {report['release_readiness'].get('fatigue_verified')}",
    ]
    if report.get("warnings"):
        lines.append("\n### Warnings")
        for w in report["warnings"]:
            lines.append(f"- ⚠️ {w}")
    if report["failed_gates"]:
        lines.append("\n### Failed Gates")
        for g in report["failed_gates"]:
            lines.append(f"- ❌ {g.get('id', '?')}: {g.get('note', '')}")
    if report["not_evaluated"]:
        lines.append("\n### Not Evaluated")
        for g in report["not_evaluated"]:
            lines.append(f"- ⏭ {g}")
    # Bug-23 修复: 确保末尾换行，防止 markdown 截断
    return "\n".join(lines) + "\n"
