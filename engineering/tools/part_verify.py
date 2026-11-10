#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""part_verify.py — 零件「三段自检」强制门禁（P1-⑤ / 整改 D-2）

===========================================================================
【要解决的问题】建完就宣布成功，几何根本没回读
---------------------------------------------------------------------------
现场（《减速机设计过程回溯报告》第二版 7 P5，最严重那条）：

  「小齿轮轴与大齿轮两个零件房间都返回"建模完成、三道防线 PASS"，
    但零件几何里【没有齿】。」

  大齿轮 = 半剖轮廓 polyline -> revolve(360) + 键槽切除 = 一个带轮毂的圆盘；
  小齿轮轴 = 阶梯轴 + phi53.587 圆柱段。
  完成判据只有「文件存在 + 三道防线凭据 + 看图 PASS」，没有【零件定义断言】。
  visual_check 当时只验包围盒，不验特征内容 -> 无齿零件照样 PASS。

测试员核查（bug 修复核查报告 二 P1 / 三）：
  「未找到：没有"三段自检"相关代码。仅有 body_box_mm() 和 massprops() 单个方法。」

===========================================================================
【本模块的"三段"是什么】（报告 9-D-2 原文口径）
===========================================================================
  第一段：外形 bbox 回读   —— 与期望包围盒逐面容差比对
  第二段：体积回读         —— 与解析/期望体积比对（错件、占位件、空心件的照妖镜）
  第三段：特征数回读       —— 用户特征计数与关键特征存在性

  任一偏差超限 -> ok=False，并给出【可执行的矫正指令】，
  而不是只丢一个 "FAIL" 让小屋去猜。

===========================================================================
【为什么体积比包围盒更强】
===========================================================================
  包围盒只有 6 个数，一个"实心立方体"和一个"同样的立方体但被掏空 90%"
  包围盒【完全相同】。减速机那次的两个"无齿齿轮"：
    · 大齿轮若按 da2=204.413 做成圆盘，包围盒与真齿齿轮几乎一样
      （齿顶圆就是最大外径），**bbox 根本抓不到**；
    · 但体积差异是决定性的：真齿齿轮体积 ≈ 分度圆截面 * 齿宽，
      比同外径圆盘小 15%~25%。
  -> 所以第二段（体积）是抓 P5 这类缺陷的**唯一廉价手段**。

===========================================================================
【设计纪律】
===========================================================================
  · fail-closed：读不到几何 -> ok=False（"读不到"绝不等于"没问题"）；
  · 容差可配但**有硬上限**（不许用超大容差把检查变空转）；
  · 期望值可省略 —— 省略的段落记 skipped 并**明确不计入通过**，
    绝不把"没检查"折叠成"通过"（P5 同源机理）；
  · 只依赖标准库；对 swapi 的导入一律 try/except。
"""
from __future__ import annotations

import json
import os
import sys

# --- 默认容差 ----------------------------------------------------------
# 报告 9-D-2 原文要求"任一偏差 >0.1 mm 即失败重来"，故 bbox 默认 0.1 mm。
DEFAULT_BBOX_TOL_MM = 0.1
# 体积容差 2%：报告 15.1 M1 对齿轮给出的判据就是"体积偏差 <=2%"。
DEFAULT_VOLUME_TOL_PCT = 2.0
# 体积容差的硬上限：超过 10% 就等于没查（防止用容差把检查做空）。
VOLUME_TOL_HARD_CAP_PCT = 10.0
# bbox 容差硬上限 1.0 mm：再大就失去"精确建模"的意义。
BBOX_TOL_HARD_CAP_MM = 1.0

# --- 特征类型口径 ------------------------------------------------------
# 这些是 SW 的"基础设施特征"，不属于用户建的东西，计数时必须排除。
#   来源：swapi.py 既有的特征遍历写法 + SW API 文档 GetTypeName2 返回值。
# ── 【改白名单·本机实测驱动】─────────────────────────────────────────
# 子代理在 SW2025 rev 33.5.0.53 上实测：
#   · 空零件 GetFeatureCount = 18（13 个文件夹 + 3 基准面 + 原点 + Tables）；
#   · 做一个【切除】，GetTypeName2 返回 "ICE"（Instant3D）而【不是 "Cut"】！
# 结论：用"排除式"（黑名单）计数会把 ICE 当成用户特征、又会把真正的
#   实体特征漏掉；必须改用【白名单】—— 只认已知的用户实体特征类型。
# 这一条正是报告 §15.1 要求「特征计数可机器判定」的前提：
#   计数口径不唯一，判据就不成立。
USER_FEATURE_TYPES = frozenset([
    # 拉伸 / 旋转 / 扫掠 / 放样
    "Extrusion", "Boss", "BossThin", "Cut", "CutThin", "ICE",
    "Revolution", "RevCut", "RevolveThin", "RevCutThin",
    "Sweep", "SweepCut", "SweepThin", "SweepCutThin",
    "Blend", "BlendCut", "BlendThin", "BlendCutThin",
    # 孔 / 阵列 / 镜像
    "HoleWzd", "SketchHole", "HoleSeries", "WizardHole",
    "CirPattern", "LPattern", "MirrorPattern", "DerivedLPattern",
    "MirrorSolid", "MirrorPart",
    # 修饰
    "Fillet", "VarFillet", "Chamfer", "Shell", "Draft", "Dome",
    "Thicken", "ThickenCut", "Rib", "Wrap",
    # 实体/导入
    "Imported", "BaseBody", "BodyFeature", "GridFeature", "Shape",
])

# 这些是 SW 的"基础设施"节点，明确【不算】用户特征。
#   （白名单模式下它们本来就不会被计入，这里保留是为了给调用方
#     一个可解释的分类结果，便于排查"为什么少算了一个"。）
NON_USER_FEATURES = frozenset([
    "RefPlane", "RefAxis", "OriginProfileFeature", "MaterialFolder",
    "SensorFolder", "CommentFolder", "CommentsFolder", "FavoriteFolder",
    "History", "HistoryFolder", "SketchHistory", "Ambient", "Default",
    "Document", "CoordSys", "SurfaceBodyFolder", "SolidBodyFolder",
    "SelectionSetFolder", "DocsFolder", "DetailCabinet", "InkMarkupFolder",
    "EnvFolder", "EqnFolder", "ConfigTableFolder", "Tables",
])
# 草图单独归类：草图属于"用户动作"，但与被特征计数分开看更清楚。
SKETCH_TYPES = frozenset(["Sketch", "3DSketch", "ProfileFeature"])


def _num(v, default=None):
    try:
        f = float(v)
        if f != f or f in (float("inf"), float("-inf")):
            return default
        return f
    except Exception:
        return default


def _bbox_dims(bbox):
    """六元组 -> (dx, dy, dz)。"""
    b = [float(x) for x in (bbox or [])]
    if len(b) < 6:
        return None
    return (abs(b[1] - b[0]), abs(b[3] - b[2]), abs(b[5] - b[4]))


def compare_bbox(actual, expect, tol_mm=DEFAULT_BBOX_TOL_MM):
    """第一段：包围盒逐面比对。

    Args:
        actual: 实测 [xmin,xmax,ymin,ymax,zmin,zmax]
        expect: 期望同上；也接受 [dx,dy,dz]（只比尺寸时）
        tol_mm: 容差（会被硬上限夹紧）

    Returns:
        {"checked","ok","deltas_mm","by_axis","max_abs_delta_mm",
         "correction", "note"?}
    """
    out = {"checked": False, "ok": None, "deltas_mm": {}, "by_axis": {},
           "max_abs_delta_mm": None, "correction": None}
    t = _num(tol_mm, DEFAULT_BBOX_TOL_MM)
    t = max(0.0, min(t, BBOX_TOL_HARD_CAP_MM))
    out["tolerance_mm"] = t
    if not actual or len(actual) < 6:
        out["ok"] = False
        out["error"] = "实测包围盒不可用（%r）—— 读不到不等于没问题" % (actual,)
        out["correction"] = {"hint": "零件里可能没有实体（IBody2）；先建实体再回读。"}
        return out
    if not expect:
        out["note"] = "未给期望包围盒，第一段跳过（不计入通过）"
        return out
    _an = [float(x) for x in actual[:6]]
    _en = [float(x) for x in expect]
    if len(_en) == 3:
        # 只给尺寸 -> 比边长
        _ad = _bbox_dims(_an)
        deltas = [abs(_ad[i] - _en[i]) for i in range(3)]
        out["deltas_mm"] = {"dx": deltas[0], "dy": deltas[1], "dz": deltas[2]}
        out["by_axis"] = {"X": deltas[0], "Y": deltas[1], "Z": deltas[2]}
        out["note"] = "期望只给尺寸(dx,dy,dz)，按边长比对"
    elif len(_en) >= 6:
        labels = ["xmin", "xmax", "ymin", "ymax", "zmin", "zmax"]
        deltas = [abs(_an[i] - float(_en[i])) for i in range(6)]
        out["deltas_mm"] = {labels[i]: deltas[i] for i in range(6)}
        out["by_axis"] = {
            "X": max(deltas[0], deltas[1]),
            "Y": max(deltas[2], deltas[3]),
            "Z": max(deltas[4], deltas[5]),
        }
    else:
        out["ok"] = False
        out["error"] = "期望包围盒格式不合法（需 3 或 6 个数，实际 %d）" % len(_en)
        return out
    out["checked"] = True
    out["max_abs_delta_mm"] = round(max(deltas), 6)
    out["ok"] = out["max_abs_delta_mm"] <= t
    if not out["ok"]:
        bad = {k: round(v, 4) for k, v in out["by_axis"].items() if v > t}
        out["correction"] = {
            "bad_axes": bad,
            "hint": ("包围盒超差（容差 %.3f mm）：%s。"
                     "先核对草图尺寸与拉伸方向，再重跑第一段。"
                     % (t, "、".join("%s 轴偏 %.3f mm" % (k, v)
                                     for k, v in bad.items()))),
        }
    return out


def compare_volume(actual_mm3, expect_mm3, tol_pct=DEFAULT_VOLUME_TOL_PCT):
    """第二段：体积比对（抓"无齿齿轮"这类几何替代件）。

    Args:
        actual_mm3: 实测体积（mm^3）
        expect_mm3: 期望体积（mm^3）；解析值或上轮实测值
        tol_pct:    容差百分比（会被硬上限夹紧）

    Returns:
        {"checked","ok","actual_mm3","expect_mm3","deviation_pct","correction"}
    """
    out = {"checked": False, "ok": None, "actual_mm3": None,
           "expect_mm3": None, "deviation_pct": None, "correction": None}
    a = _num(actual_mm3)
    t = _num(tol_pct, DEFAULT_VOLUME_TOL_PCT)
    t = max(0.0, min(t, VOLUME_TOL_HARD_CAP_PCT))
    out["tolerance_pct"] = t
    if a is None or a <= 0:
        out["ok"] = False
        out["error"] = ("实测体积不可用（%r）—— 读不到不等于没问题" % (actual_mm3,))
        out["correction"] = {
            "hint": ("体积回读失败。常见原因：文档处于压缩状态 / 未重建 / "
                     "只有曲面无实体。先 EditRebuild3 再 GetMassProperties。")}
        return out
    out["actual_mm3"] = round(a, 4)
    e = _num(expect_mm3)
    if e is None or e <= 0:
        out["note"] = "未给期望体积，第二段跳过（不计入通过）"
        return out
    out["expect_mm3"] = round(e, 4)
    dev = (a - e) / e * 100.0
    out["deviation_pct"] = round(dev, 4)
    out["checked"] = True
    out["ok"] = abs(dev) <= t
    if not out["ok"]:
        out["correction"] = {
            "direction": ("偏大（可能是「用简化体冒充」——例如无齿圆柱代齿轮）"
                          if dev > 0 else "偏小（可能少建/误切了材料）"),
            "hint": ("体积偏差 %.2f%%（容差 %.2f%%）：实测 %.2f mm3，期望 %.2f mm3。"
                     "先确认齿形/孔系/切除是否真的做了，再重跑第二段。"
                     % (dev, t, a, e)),
        }
    return out


def classify_features(features):
    """把特征清单分成 用户特征 / 草图 / 基础设施。

    Args:
        features: [{"name","type"}...] 或 [str...]

    Returns:
        {"user":[...],"sketch":[...],"infra":[...],"user_count","sketch_count"}
    """
    user, sketch, infra, unknown = [], [], [], []
    for f in (features or []):
        if isinstance(f, dict):
            nm = f.get("name")
            t = f.get("type")
        else:
            nm, t = str(f), None
        _t = str(t or "")
        rec = {"name": nm, "type": t}
        # ── 白名单优先：只认"已知用户实体特征" ──────────────────────────
        #   实测教训：切除返回 "ICE"；若用排除式，ICE 会被当成用户特征，
        #   而真正的 "Cut" 又可能根本不出现 —— 计数口径两处都不对。
        if _t in USER_FEATURE_TYPES:
            user.append(rec)
        elif _t in SKETCH_TYPES:
            sketch.append(rec)
        elif _t in NON_USER_FEATURES:
            infra.append(rec)
        else:
            # 未知类型：既不冒充用户特征，也不静默丢弃 —— 单独归类，
            #   由调用方决定（默认不计入 user_count，但会在返回里可见）。
            unknown.append(rec)
    return {"user": user, "sketch": sketch, "infra": infra,
            "unknown": unknown,
            "user_count": len(user), "sketch_count": len(sketch),
            "unknown_count": len(unknown),
            "note": ("用户特征按白名单计数（USER_FEATURE_TYPES）；"
                     "未知类型 %d 个未计入，见 unknown"
                     % len(unknown)) if unknown else None}


def compare_features(features, expect_features=None, expect_count=None):
    """第三段：特征数回读 + 关键特征存在性。

    Args:
        features:        实测特征清单（[{"name","type"}] 或 [str]）
        expect_features: 期望存在的特征名/类型列表
                         元素可为 str（存在即可）或 {"name":..,"count":..}
        expect_count:    期望的【用户特征】个数（精确）

    Returns:
        {"checked","ok","user_count","sketch_count","missing":[...],
         "count_ok","correction"}
    """
    import re as _re
    out = {"checked": False, "ok": None, "user_count": None,
           "sketch_count": None, "missing": [], "count_ok": None,
           "correction": None}
    if features is None:
        out["ok"] = False
        out["error"] = "特征树读取失败 —— 读不到不等于没问题（不得判通过）"
        out["correction"] = {
            "hint": ("特征树取不到。请确认 FirstFeature/GetNextFeature 的取法"
                     "（老版本是方法、新版本是属性，两种都试）。")}
        return out
    cls = classify_features(features)
    out["user_count"] = cls["user_count"]
    out["sketch_count"] = cls["sketch_count"]
    out["infra_count"] = len(cls["infra"])

    # (a) 用户特征计数
    if expect_count is not None:
        try:
            want = int(expect_count)
        except Exception:
            want = None
        if want is not None:
            out["count_ok"] = (cls["user_count"] == want)
            out["expect_count"] = want
    # (b) 关键特征存在性
    exp = []
    for f in (expect_features or []):
        if isinstance(f, dict):
            exp.append((str(f.get("name") or f.get("type") or "?"),
                        f.get("count")))
        else:
            exp.append((str(f), None))
    have_names = {}
    for r in cls["user"] + cls["sketch"]:
        for key in (r.get("name"), r.get("type")):
            if key:
                have_names[str(key)] = have_names.get(str(key), 0) + 1
    missing = []
    for nm, cnt in exp:
        # 支持 "Extrusion*" / "Cut*" 这类前缀通配（SW 的特征名带序号后缀）
        _pat = None
        if nm.endswith("*"):
            try:
                _pat = _re.compile("^" + _re.escape(nm[:-1]))
            except Exception:
                _pat = None
        if _pat is not None:
            n = sum(v for k, v in have_names.items() if _pat.match(k))
        else:
            n = have_names.get(nm, 0)
        if cnt is None:
            if n <= 0:
                missing.append({"feature": nm, "expect_count": ">=1",
                                "actual_count": n})
        elif n != int(cnt):
            missing.append({"feature": nm, "expect_count": int(cnt),
                            "actual_count": n})
    out["missing"] = missing
    out["expect_features"] = [{"feature": a, "count": b} for a, b in exp]

    checked_any = (expect_count is not None) or bool(exp)
    out["checked"] = checked_any
    if not checked_any:
        out["note"] = "未给期望特征/计数，第三段跳过（不计入通过）"
        return out
    _ok = True
    if out["count_ok"] is False:
        _ok = False
    if missing:
        _ok = False
    out["ok"] = _ok
    if not _ok:
        hints = []
        if out["count_ok"] is False:
            hints.append("用户特征数 %d != 期望 %d"
                         % (cls["user_count"], out.get("expect_count")))
        if missing:
            hints.append("缺少特征：%s"
                         % "、".join(str(x.get("feature")) for x in missing))
        out["correction"] = {
            "hint": ("第三段特征校验未过：%s。"
                     "注意：**用简化体（如无齿圆柱代齿轮）会让本段直接失败**，"
                     "这不是可以放宽的告警。" % "；".join(hints)),
            "missing_features": missing,
        }
    return out


def three_stage_check(bbox_actual=None, bbox_expect=None,
                      volume_actual_mm3=None, volume_expect_mm3=None,
                      features=None, expect_features=None,
                      expect_feature_count=None,
                      bbox_tol_mm=DEFAULT_BBOX_TOL_MM,
                      volume_tol_pct=DEFAULT_VOLUME_TOL_PCT,
                      require_all=True):
    """三段自检总入口。

    Args:
        require_all: True（默认）时【任何一段被跳过】都不算通过 ——
                     这正是"不可用不得折叠成通过"的纪律。False 仅用于
                     调试/探索阶段，生产路径禁止用。

    Returns:
        {"ok","verdict","stages":{bbox,volume,features},
         "executed":[..],"skipped":[..],"failed":[..],"corrections":[..]}
        verdict in PASS | FAIL | INCOMPLETE(有段落被跳过且 require_all)
    """
    out = {"ok": False, "verdict": None, "stages": {}, "executed": [],
           "skipped": [], "failed": [], "corrections": [],
           "enforced_by": "part_verify.three_stage_check"}
    s1 = compare_bbox(bbox_actual, bbox_expect, bbox_tol_mm)
    s2 = compare_volume(volume_actual_mm3, volume_expect_mm3, volume_tol_pct)
    s3 = compare_features(features, expect_features, expect_feature_count)
    out["stages"] = {"bbox": s1, "volume": s2, "features": s3}

    labels = (("bbox", s1), ("volume", s2), ("features", s3))
    for key, st in labels:
        if st.get("checked"):
            out["executed"].append(key)
            if not st.get("ok"):
                out["failed"].append(key)
                if st.get("correction"):
                    out["corrections"].append(dict(st["correction"], stage=key))
        else:
            out["skipped"].append(key)

    if out["failed"]:
        out["verdict"] = "FAIL"
        out["ok"] = False
    elif require_all and out["skipped"]:
        out["verdict"] = "INCOMPLETE"
        out["ok"] = False
        out["note"] = ("有段落被跳过（%s）：按纪律【不得】判通过。"
                       "要么补上期望值，要么显式接受风险并留痕。"
                       % "、".join(out["skipped"]))
    elif not out["executed"]:
        out["verdict"] = "INCOMPLETE"
        out["ok"] = False
        out["note"] = "三段全被跳过 —— 什么都没验，绝不判通过"
    else:
        out["verdict"] = "PASS"
        out["ok"] = True
    return out


# ======================================================================
# 与 SW 的接口（只做"读"，落盘判断由 swapi 的调用方负责）
# ======================================================================
def _prop_or_call(obj, name):
    """取一个 COM 成员，兼容「属性」与「方法」两种形态。

    == 真机实测（SW2025 rev 33.5.0.53, late binding）==
      f.GetTypeName2      -> 属性，返回 str；f.GetTypeName2() 抛
                             TypeError: 'str' object is not callable
      mdl.FirstFeature    -> 属性且也可调用（两种都能拿到）
      mdl.GetFeatureCount -> 属性（int），加括号会 TypeError

    旧实现写死 f.GetTypeName2()；在真机上抛异常后被 bare except 吞掉，
    于是【所有】特征类型都变成 None -> 分类全落进 unknown ->
    用户特征数恒为 0 -- 而"期望 0 个"又恰好判通过。
    这就是「读不到被折叠成通过」在特征段的复现，必须修掉。
    """
    try:
        v = getattr(obj, name, None)
    except Exception:
        return None
    if v is None:
        return None
    if isinstance(v, (str, int, float, bool)):
        return v
    if callable(v):
        try:
            return v()
        except Exception:
            return None
    return v


def read_features(model):
    """从 SWModel 读特征清单；读不到返回 None（调用方据此判 FAIL）。"""
    try:
        mdl = getattr(model, "model", model)
    except Exception:
        mdl = model
    if mdl is None:
        return None

    def _feat_like(o):
        if o is None:
            return False
        try:
            return hasattr(o, "Name") and hasattr(o, "GetTypeName2")
        except Exception:
            return False

    first = None
    try:
        cand = mdl.FirstFeature
        if _feat_like(cand):
            first = cand
    except Exception:
        pass
    if first is None:
        try:
            cand = mdl.FirstFeature()
            if _feat_like(cand):
                first = cand
        except Exception:
            pass
    if first is None:
        return None
    out = []
    f = first
    guard = 0
    while f is not None and guard < 5000:
        guard += 1
        try:
            nm = f.Name
            tn = _prop_or_call(f, "GetTypeName2")
            if nm:
                out.append({"name": str(nm),
                            "type": (str(tn) if tn else None)})
        except Exception:
            pass
        nxt = None
        try:
            cand = f.GetNextFeature
            if _feat_like(cand):
                nxt = cand
            elif callable(cand):
                c2 = cand()
                nxt = c2 if _feat_like(c2) else None
        except Exception:
            nxt = None
        if nxt is None:
            break
        f = nxt
    return out or None


def read_volume_mm3(model):
    """从 SWModel 读体积（mm^3）；读不到返回 None。

    优先顺序与 swapi 既有实现一致：
      1) IModelDoc2.GetMassProperties（无参）-> 索引 3 是体积，单位 m^3；
      2) 逐实体 IBody2.GetMassProperties 累加。
    绝不编造：两条路都失败就返回 None。
    """
    mdl = getattr(model, "model", model)
    if mdl is None:
        return None
    # ── 路径①（推荐）：IModelDocExtension.GetMassProperties2(Accuracy, Status,
    #    UseSelected) —— 本机实测（SW2025 rev 33.5）：
    #      返回 12 个 double：[0..2]质心 [3]体积 [4]表面积 [5]质量
    #                         [6..11] 惯性矩/积
    #      Accuracy: Lower=0/Medium=1/Higher=2；Status: 0=OK, 2=NoBody
    #      ★单位是 m³（SI），不是 mm³ -> 乘 1e9
    #    必须检查 Status：NoBody 时返回的数组可能全 0，直接当体积会
    #      得出"体积 0"的假结论（比失败更糟 —— 上层会当成"物件是空的"）。
    try:
        ext = getattr(mdl, "Extension", None)
        if ext is not None:
            _st = None
            try:
                import win32com.client as _w32
                import pythoncom as _pyc
                _st = _w32.VARIANT(_pyc.VT_BYREF | _pyc.VT_I4, 0)
            except Exception:
                _st = None
            mp = (ext.GetMassProperties2(1, _st, False) if _st is not None
                  else ext.GetMassProperties2(1, 0, False))
            _status = getattr(_st, "value", 0) if _st is not None else 0
            if _status not in (0, None):
                # NoBody/UnknownError -> 明确失败，绝不返回 0 当体积
                return None
            if mp is not None and len(mp) >= 4:
                _v = float(mp[3]) * 1e9
                if _v > 0:
                    return _v
    except Exception:
        pass
    # ── 路径②：IModelDoc2.GetMassProperties —— 本机实测它是【属性】不是方法，
    #    无括号访问返回 16 元素 tuple，前 12 位与 GetMassProperties2 同构。
    try:
        mp = getattr(mdl, "GetMassProperties", None)
        if callable(mp):
            mp = mp()
        if mp is not None and len(mp) >= 4:
            _v = float(mp[3]) * 1e9
            if _v > 0:
                return _v
    except Exception:
        pass
    try:
        bodies = mdl.GetBodies2(0, 1)
        bl = (list(bodies) if isinstance(bodies, tuple)
              else ([bodies] if bodies else []))
        total = 0.0
        got = False
        for b in bl:
            try:
                props = b.GetMassProperties(0) or []
                if len(props) >= 4:
                    total += float(props[3]) * 1e9
                    got = True
            except Exception:
                continue
        return total if got else None
    except Exception:
        return None


def read_bbox_mm(model):
    """从 SWModel 读包围盒（mm，六元组）；读不到返回 None。"""
    try:
        return model.body_box_mm()
    except Exception:
        return None


def self_check_model(model, expect=None, bbox_tol_mm=DEFAULT_BBOX_TOL_MM,
                     volume_tol_pct=DEFAULT_VOLUME_TOL_PCT,
                     require_all=True, work_dir=None):
    """对【已打开的 SWModel】跑三段自检（真实回读，不依赖调用方差错）。

    Args:
        model:     swapi.SWModel
        expect:    {
                     "bbox_mm": [..] 或 [dx,dy,dz],
                     "volume_mm3": number,
                     "features": ["Extrusion*", {"name":"Cut","count":2}],
                     "feature_count": int
                   }
        work_dir:  给了就把结果写成 <work_dir>/reports/<零件>.self_check.json

    Returns:
        three_stage_check 的返回 + {"actual":{"bbox_mm","volume_mm3",
        "features_count"}}，便于上层直接落台账。
    """
    exp = expect if isinstance(expect, dict) else {}
    bbox = read_bbox_mm(model)
    vol = read_volume_mm3(model)
    feats = read_features(model)
    res = three_stage_check(
        bbox_actual=bbox, bbox_expect=exp.get("bbox_mm"),
        volume_actual_mm3=vol, volume_expect_mm3=exp.get("volume_mm3"),
        features=feats, expect_features=exp.get("features"),
        expect_feature_count=exp.get("feature_count"),
        bbox_tol_mm=bbox_tol_mm, volume_tol_pct=volume_tol_pct,
        require_all=require_all)
    # == 齿轮定义断言（P5）==
    # 报告第15.1节 M1 要求「齿轮必须是真齿形：特征计数=z、da/df 实测偏差
    #   <=0.02mm、体积偏差 <=2%」。swapi._verify_gear_geometry 已实现这套
    #   判据，但此前它是 gear() 内部的返回字段，room-end 读不到。
    #   这里把它作为【第四段】并入同一份 self_check.json，让门禁能看见。
    _gear_exp = exp.get("gear") if isinstance(exp, dict) else None
    if _gear_exp:
        try:
            _gr = model._verify_gear_geometry(
                module_mm=_gear_exp.get("module_mm"),
                teeth=_gear_exp.get("teeth"),
                thickness_mm=_gear_exp.get("thickness_mm"),
                bore_dia_mm=_gear_exp.get("bore_dia_mm") or 0.0,
                profile_area_mm2=_gear_exp.get("profile_area_mm2"),
                tol_dia_mm=_gear_exp.get("tol_dia_mm", 0.05),
                tol_vol_pct=_gear_exp.get("tol_vol_pct", 2.0))
            res["stages"]["gear"] = _gr
            if _gr.get("ok"):
                res["executed"].append("gear")
            else:
                res["executed"].append("gear")
                res["failed"].append("gear")
                res["corrections"].append({
                    "stage": "gear",
                    "hint": _gr.get("error") or "齿轮定义断言未通过"})
        except Exception as _e_g:
            res["stages"]["gear"] = {"ok": False, "error": repr(_e_g)}
            res["failed"].append("gear")
            res["corrections"].append({"stage": "gear",
                                       "hint": "齿轮断言执行异常: %r" % (_e_g,)})
        # 有齿轮断言失败 -> 总判必须 FAIL（不能被前面三段 PASS 掩盖）
        if res["failed"]:
            res["ok"] = False
            res["verdict"] = "FAIL"

    _cls = classify_features(feats) if feats else None
    res["actual"] = {
        "bbox_mm": bbox,
        "volume_mm3": (round(vol, 4) if vol else None),
        "user_feature_count": (_cls["user_count"] if _cls else None),
        "sketch_count": (_cls["sketch_count"] if _cls else None),
        "feature_names": ([r.get("name") for r in _cls["user"]][:200]
                          if _cls else None),
    }
    if work_dir:
        try:
            name = "part"
            try:
                _p = getattr(model, "path", None)
                if _p:
                    name = os.path.splitext(os.path.basename(str(_p)))[0]
            except Exception:
                pass
            d = os.path.join(work_dir, "reports")
            os.makedirs(d, exist_ok=True)
            fp = os.path.join(d, "%s.self_check.json" % name)
            tmp = fp + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(res, f, ensure_ascii=False, indent=2)
            os.replace(tmp, fp)
            res["evidence_file"] = fp
        except Exception as e:
            res["evidence_error"] = repr(e)
    return res


# ======================================================================
# CLI
# ======================================================================
def _emit(obj):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _main(argv):
    import argparse
    ap = argparse.ArgumentParser(
        description="零件三段自检：bbox / 体积 / 特征数（可对离线 JSON 或已开模型）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("check", help="对【离线数据】跑三段比对")
    a.add_argument("--bbox-actual", default=None)
    a.add_argument("--bbox-expect", default=None)
    a.add_argument("--volume-actual", type=float, default=None)
    a.add_argument("--volume-expect", type=float, default=None)
    a.add_argument("--features-json", default=None,
                   help="实测特征清单 JSON 文件（[{name,type}...]）")
    a.add_argument("--expect-features", default=None, help="逗号分隔")
    a.add_argument("--expect-count", type=int, default=None)
    a.add_argument("--bbox-tol", type=float, default=DEFAULT_BBOX_TOL_MM)
    a.add_argument("--volume-tol", type=float, default=DEFAULT_VOLUME_TOL_PCT)
    a.add_argument("--allow-skip", action="store_true",
                   help="允许跳过段落仍判 PASS（调试用，生产禁止）")

    ns = ap.parse_args(argv)

    def _floats(s):
        if not s:
            return None
        try:
            return [float(x) for x in str(s).replace(",", ",").split(",")
                    if x.strip()]
        except Exception:
            return None

    if ns.cmd == "check":
        feats = None
        if ns.features_json and os.path.isfile(ns.features_json):
            try:
                with open(ns.features_json, "r", encoding="utf-8") as f:
                    feats = json.load(f)
            except Exception:
                feats = None
        expf = ([x.strip() for x in ns.expect_features.split(",") if x.strip()]
                if ns.expect_features else None)
        res = three_stage_check(
            bbox_actual=_floats(ns.bbox_actual),
            bbox_expect=_floats(ns.bbox_expect),
            volume_actual_mm3=ns.volume_actual,
            volume_expect_mm3=ns.volume_expect,
            features=feats, expect_features=expf,
            expect_feature_count=ns.expect_count,
            bbox_tol_mm=ns.bbox_tol, volume_tol_pct=ns.volume_tol,
            require_all=(not ns.allow_skip))
        _emit(res)
        return 0 if res["ok"] else 4
    return 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
