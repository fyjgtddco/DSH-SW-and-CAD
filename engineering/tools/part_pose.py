#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""part_pose.py — 零件「朝向声明」(<零件>.pr.json) 的代码级强制层（P1-⑥ / 整改 D）

═══════════════════════════════════════════════════════════════════════════
【要解决的问题】"上方向"只写在技能文档里，代码里根本不存在
───────────────────────────────────────────────────────────────────────────
现场（《减速机设计过程回溯报告》第二版 §7 P1 / §12.1）：

  · 小屋建零件时靠【试错】发现坐标系语义 ——
      Top Plane 草图局部 y = 世界 −Z；cut() 默认切向 −Y，沿 +Y 要 flip=True；
  · 每个零件建完只 save() 出 .SLDPRT，【没有任何朝向声明】；
  · 于是总装时无从断言"这个零件摆正了没有"：
      组件旋转是单位阵、坐标也"对得上"，但零件本身就是躺着的。

测试员核查（bug 修复核查报告 §二 P1 / §三）结论是
「未找到：没有 pr.json 相关代码」—— 当时 .pr.json 只写在
skills/sw-design/SKILL.md 与 preset-engineering.patch.yml 的提示词里。
用户的原则是「所有约束都要写在代码里强制执行，不能只写在提示词」。

═══════════════════════════════════════════════════════════════════════════
【字段口径】（与 skills/sw-design 第〇.3 节一致，两份不得漂移）
═══════════════════════════════════════════════════════════════════════════
  {
    "schema": "dsh-part-pose/1",
    "part": "<绝对路径>",
    "up_axis": "X" | "Y" | "Z",      # 该零件【装好后】"上"对应哪个世界轴
    "up_sign": +1 | -1,              # "上"是该轴的正向还是负向
    "main_axis": "X" | "Y" | "Z",    # 主轴（回转轴 / 最长方向）
    "base_plane": "Top Plane" | ..., # 建模时选用的基准面
    "sketch_y_sign": +1 | -1,        # 草图局部 y 与世界该轴的关系（ZSIGN）
    "bbox_mm": [xmin,xmax,ymin,ymax,zmin,zmax],
    "written_at": "...", "source": "explicit" | "inferred"
  }

  ★ source="inferred" 表示"不是显式声明，而是按默认推断"。
    推断值可以落盘（否则流程卡死），但必须如实标注 ——
    总装断言遇到 inferred 降级为 WARNING 而非硬 FAIL。

═══════════════════════════════════════════════════════════════════════════
【为什么"零旋转装配"必须报警】（J4，最容易漏的一条）
═══════════════════════════════════════════════════════════════════════════
  零件声明 up_axis="Y"，装配时却零旋转塞进去 —— 它在世界里实际是 Y 朝上，
  与契约要求的 world_up="Z" 冲突。判据：声明 up_axis != 世界 world_up，
  但旋转子矩阵 == 单位阵 → "零件根本没摆正，只能在【重建该零件】里修"。

═══════════════════════════════════════════════════════════════════════════
【设计纪律】
═══════════════════════════════════════════════════════════════════════════
  · fail-closed：读不到 / 字段缺 / 枚举非法 → ok=False 并说明，绝不默认 PASS；
  · 只依赖标准库；
  · 写文件一律 os.replace 原子落盘；
  · 本模块【不 import swapi】—— 它被 swapi.save() 调用，反向依赖会成环。
"""
from __future__ import annotations

import json
import os
import sys
import time

SCHEMA = "dsh-part-pose/1"
FILE_SUFFIX = ".pr.json"

AXES = ("X", "Y", "Z")
VALID_UP_SIGN = (1, -1)

# 旋转子矩阵"≈单位阵"的容差。SW 的 MathTransform 用 double 存，
#   零旋转经 COM 往返后可能带 1e-12 级噪声，故不能要求精确 0。
IDENTITY_TOL = 1e-6


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def pr_path(part_path):
    """<零件>.SLDPRT -> <零件>.SLDPRT.pr.json（与 .origin.json 同基）。"""
    if not part_path:
        return None
    return str(part_path) + FILE_SUFFIX


def _atomic_write_json(path, obj):
    """原子写：先写 .tmp 再 os.replace，避免读到半截文件。"""
    d = os.path.dirname(os.path.abspath(path))
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _norm_axis(v):
    s = str(v or "").strip().upper()
    return s if s in AXES else None


def _norm_sign(v):
    try:
        i = int(v)
    except Exception:
        return None
    return i if i in VALID_UP_SIGN else None


def write_pr_record(part_path, up_axis=None, main_axis=None, up_sign=1,
                    base_plane=None, sketch_y_sign=None, bbox_mm=None,
                    source=None, note=None, extra=None):
    """写 <零件>.SLDPRT.pr.json（幂等；失败不抛，返回 ok=False）。

    Args:
        part_path:     零件落盘路径（.SLDPRT）
        up_axis:       "X"/"Y"/"Z"：装好后"上"对应的世界轴
        main_axis:     "X"/"Y"/"Z"：主轴（回转轴 / 最长方向）
        up_sign:       +1 / -1
        base_plane:    建模时选用的基准面名（如 "Top Plane"）
        sketch_y_sign: +1 / -1：草图局部 y 与世界轴的符号关系（ZSIGN）
        bbox_mm:       写声明时的实测包围盒六元组
        source:        "explicit"（显式声明）/ "inferred"（按默认推断）
        note / extra:  自由附加信息

    Returns: {"ok","path","record","inferred","error"?}
        即使 ok=False 也【绝不】阻断保存（与 .origin.json 同一纪律）。
    """
    if not part_path:
        return {"ok": False, "error": "缺少 part_path"}
    path = pr_path(part_path)
    ax = _norm_axis(up_axis)
    mx = _norm_axis(main_axis)
    sg = _norm_sign(up_sign)
    _inferred = False
    if ax is None:
        ax = "Z"
        _inferred = True
    if mx is None:
        mx = ax
        _inferred = True
    if sg is None:
        sg = 1
        _inferred = True
    src = str(source or ("inferred" if _inferred else "explicit"))
    rec = {
        "schema": SCHEMA,
        "part": os.path.abspath(str(part_path)),
        "up_axis": ax,
        "up_sign": sg,
        "main_axis": mx,
        "base_plane": (str(base_plane) if base_plane else None),
        "sketch_y_sign": (_norm_sign(sketch_y_sign)
                          if sketch_y_sign is not None else None),
        "bbox_mm": bbox_mm,
        "written_at": _now(),
        "source": src,
        "note": (str(note) if note else None),
    }
    if isinstance(extra, dict):
        for k, v in extra.items():
            rec.setdefault(k, v)
    try:
        _atomic_write_json(path, rec)
    except Exception as e:
        return {"ok": False, "path": path, "record": rec,
                "error": "写 pr.json 失败: %r" % (e,)}
    return {"ok": True, "path": path, "record": rec,
            "inferred": bool(_inferred)}


def read_pr_record(part_path):
    """读回 <零件>.pr.json。

    Returns: {"ok","path","record","exists","error"?}
    文件不存在时 ok=False 且 exists=False —— 调用方必须把它当"缺声明"，
      不得用默认值悄悄补一个通过。
    """
    path = pr_path(part_path)
    out = {"ok": False, "path": path, "record": None,
           "exists": bool(path and os.path.isfile(path))}
    if not path:
        out["error"] = "缺少 part_path"
        return out
    if not out["exists"]:
        out["error"] = "没有 pr.json 朝向声明：%s" % path
        return out
    try:
        with open(path, "r", encoding="utf-8") as f:
            rec = json.load(f)
    except Exception as e:
        out["error"] = "pr.json 解析失败: %r" % (e,)
        return out
    if not isinstance(rec, dict):
        out["error"] = "pr.json 顶层不是对象"
        return out
    out["ok"] = True
    out["record"] = rec
    return out


def validate_pr_record(rec, bbox_mm=None):
    """校验一份 pr.json 记录（字段齐全 + 枚举合法 + 与 bbox 自洽）。

    Returns: {"ok","errors":[...],"warnings":[...]}
    """
    errors = []
    warnings = []
    if not isinstance(rec, dict):
        return {"ok": False, "errors": ["记录不是对象"], "warnings": []}

    schema = rec.get("schema")
    if schema and str(schema) != SCHEMA:
        warnings.append("schema 不是 %s（实际 %r），按兼容口径继续校验"
                        % (SCHEMA, schema))
    for field in ("up_axis", "up_sign", "main_axis"):
        if rec.get(field) in (None, ""):
            errors.append("缺少必填字段 %s" % field)
    if rec.get("up_axis") is not None and _norm_axis(rec.get("up_axis")) is None:
        errors.append("up_axis=%r 不是 X/Y/Z" % (rec.get("up_axis"),))
    if rec.get("main_axis") is not None and _norm_axis(rec.get("main_axis")) is None:
        errors.append("main_axis=%r 不是 X/Y/Z" % (rec.get("main_axis"),))
    if rec.get("up_sign") is not None and _norm_sign(rec.get("up_sign")) is None:
        errors.append("up_sign=%r 只能是 +1/-1" % (rec.get("up_sign"),))
    if str(rec.get("source") or "") == "inferred":
        warnings.append("该朝向是【推断值】而非显式声明（source=inferred）——"
                        "总装断言会降级为 WARNING")

    # 与实测 bbox 自洽：声明的主轴应当是包围盒里最长的那一维。
    #   这不是几何定义，而是一条廉价的反自相矛盾检查。
    _bb = bbox_mm if bbox_mm is not None else rec.get("bbox_mm")
    if isinstance(_bb, (list, tuple)) and len(_bb) >= 6:
        try:
            dims = {"X": abs(float(_bb[1]) - float(_bb[0])),
                    "Y": abs(float(_bb[3]) - float(_bb[2])),
                    "Z": abs(float(_bb[5]) - float(_bb[4]))}
            _mx = _norm_axis(rec.get("main_axis"))
            if _mx and max(dims.values()) > 0:
                longest = max(dims, key=lambda k: dims[k])
                if dims[_mx] < max(dims.values()) * 0.98:
                    warnings.append(
                        "main_axis=%s 但包围盒最长维是 %s（%s vs %s mm）—— "
                        "声明与几何不自洽"
                        % (_mx, longest, round(dims[_mx], 3),
                           round(dims[longest], 3)))
        except Exception:
            pass
    return {"ok": not errors, "errors": errors, "warnings": warnings}


def _identity_like(rot, tol=IDENTITY_TOL):
    """3x3 旋转子矩阵是否≈单位阵（行主序 9 个 double）。"""
    if not rot or len(rot) < 9:
        return None
    try:
        for i in range(3):
            for j in range(3):
                want = 1.0 if i == j else 0.0
                if abs(float(rot[i * 3 + j]) - want) > tol:
                    return False
        return True
    except Exception:
        return None


def check_assembly_pose(part_path=None, rec=None, rotation=None,
                        world_up="Z", world_up_sign=1, strict=None):
    """★总装朝向断言：组件旋转矩阵 vs pr.json 声明（J2/J4/J5）。

    Args:
        part_path:     零件路径（用于读 pr.json；给了 rec 可省）
        rec:           已读到的 pr.json 记录（优先）
        rotation:      组件世界变换的 3x3 旋转子矩阵（行主序 9 数）或
                       IMathTransform.ArrayData 的 16 数（自动取左上 3x3）
        world_up:      总装契约要求的世界"上"轴（X/Y/Z）
        world_up_sign: +1 / -1
        strict:        True 时 WARNING 也算失败（默认按 source 自动决定）

    Returns:
        {"ok","verdict","code","rotation_ok","identity_like","declared",
         "world_up","errors":[...],"warnings":[...],"action"}
        verdict in PASS | WARNING | FAIL | UNKNOWN
        code    in J2_POSE_CONFLICT | J4_ZERO_ROTATION_UNPLACED |
                  J5_ROTATED_BUT_DECLARED_ALIGNED | NO_PR_RECORD | BAD_RECORD
    """
    out = {"ok": False, "verdict": "UNKNOWN", "code": None,
           "rotation_ok": None, "identity_like": None,
           "declared": None, "world_up": _norm_axis(world_up),
           "world_up_sign": _norm_sign(world_up_sign),
           "errors": [], "warnings": [], "action": None}

    if rec is None:
        rr = read_pr_record(part_path)
        if not rr.get("ok"):
            # ★fail-closed：缺声明是【确定的不通过】，不是"无法判定(UNKNOWN)"。
            #   理由（报告 §15.2 J6 原文）："无声明 = 不可审计，一律不通过"。
            #   若这里降级成 UNKNOWN，上层很容易把它当成"没结论、先放行"，
            #   那正是 P5「不可用被折叠成通过」的同一条机理。
            out["code"] = "NO_PR_RECORD"
            out["verdict"] = "FAIL"
            out["errors"].append(rr.get("error") or "没有 pr.json 朝向声明")
            out["action"] = ("先给该零件补 <零件>.SLDPRT.pr.json"
                             "（up_axis/up_sign/main_axis 三项必填）"
                             "再进总装；缺声明 = 不可审计，不通过。")
            return out
        rec = rr["record"]
    out["declared"] = {
        "up_axis": rec.get("up_axis"),
        "up_sign": rec.get("up_sign"),
        "main_axis": rec.get("main_axis"),
        "base_plane": rec.get("base_plane"),
        "sketch_y_sign": rec.get("sketch_y_sign"),
        "source": rec.get("source"),
    }
    _v = validate_pr_record(rec)
    if not _v["ok"]:
        out["code"] = "BAD_RECORD"
        out["errors"].extend(_v["errors"])
        out["action"] = "pr.json 字段不合法，先修正声明再总装。"
        return out
    out["warnings"].extend(_v["warnings"])

    _decl_axis = _norm_axis(rec.get("up_axis"))
    _decl_sign = _norm_sign(rec.get("up_sign"))
    _waxis = out["world_up"]
    _wsign = out["world_up_sign"]

    # 旋转矩阵归一化：接受 9 数（3x3 行主序）或 16 数（IMathTransform.ArrayData）
    #
    # == IMathTransform.ArrayData 的真实布局（本机实测，SW2025 rev 33.5）==
    #   d[0:3]   = 局部 X 轴（世界系方向）
    #   d[3:6]   = 局部 Y 轴
    #   d[6:9]   = 局部 Z 轴 = 法向
    #   d[9:12]  = 原点平移
    #   d[12]    = 缩放（实测恒 1.0）；d[13:16] 未用
    #   → 行向量约定：P_world = (x,y,z,1)·M
    #
    # 前一版把它当成"[r00 r01 r02 tx  r10 r11 r12 ty  r20 r21 r22 tz]"式的
    #   行主序 4x4（平移在 3/7/11），于是挑出来的"旋转"里混进了平移分量、
    #   又漏掉真正的两列 —— 判定必然错。实测三默认基准面的原始数组为：
    #     前视 [1,0,0, 0,1,0, 0,0,1, 0,0,0, 1,0,0,0]
    #     上视 [1,0,0, 0,0,-1, 0,1,0, 0,0,0, 1,0,0,0]  -> 草图 (x,y)->世界 (x,0,-y)
    #   只有按"三行三轴"读才对得上这条实测。
    #
    # 因此 16 数场景取的是【前三行、每行前三个分量】组成的 3x3：
    #   row0 = d[0:3]，row1 = d[3:6]，row2 = d[6:9]
    # 这样得到的是"局部轴在世界系里的方向矩阵"（行=轴），
    #   单位阵判定与"组件零旋转"语义一致。
    rot9 = None
    if rotation is not None:
        try:
            vals = [float(x) for x in rotation]
        except Exception:
            vals = []
        if len(vals) >= 16:
            rot9 = [vals[0], vals[1], vals[2],
                    vals[3], vals[4], vals[5],
                    vals[6], vals[7], vals[8]]
            out["axis_vectors_world"] = {
                "x": [round(vals[0], 9), round(vals[1], 9), round(vals[2], 9)],
                "y": [round(vals[3], 9), round(vals[4], 9), round(vals[5], 9)],
                "z": [round(vals[6], 9), round(vals[7], 9), round(vals[8], 9)],
                "origin": [round(vals[9], 9), round(vals[10], 9),
                           round(vals[11], 9)],
            }
        elif len(vals) >= 9:
            rot9 = vals[:9]
        out["rotation_layout"] = ("IMathTransform.ArrayData(16): "
                                  "d[0:3]=X轴 d[3:6]=Y轴 d[6:9]=Z轴 d[9:12]=平移")
    out["rotation_ok"] = (rot9 is not None)
    ident = _identity_like(rot9)
    out["identity_like"] = ident

    # 判据 (1)：声明朝向 vs 世界契约
    _aligned = (_decl_axis is not None and _waxis is not None
                and _decl_axis == _waxis
                and (_decl_sign == _wsign))

    if _aligned:
        # J5：声明已对齐，但组件却被转过 -> 姿态被别的路径改过
        if ident is False:
            out["code"] = "J5_ROTATED_BUT_DECLARED_ALIGNED"
            out["verdict"] = "WARNING"
            out["warnings"].append(
                "零件声明 up_axis=%s%s 已与世界 %s%s 对齐，但组件旋转【不是】"
                "单位阵 —— 姿态可能被配合求解/拖动改过，必须复核"
                % (_decl_axis, rec.get("up_sign"), _waxis, _wsign))
            out["action"] = "复核该组件的配合；若确属装配需要，须回写 pr.json。"
        else:
            out["verdict"] = "PASS"
            if ident is None:
                out["warnings"].append("未拿到旋转矩阵，只能做声明层比对"
                                       "（无法证明零件真的摆正了）")
    else:
        # J4：声明 != 世界契约，且组件零旋转 -> 零件根本没摆正
        if ident is True:
            out["code"] = "J4_ZERO_ROTATION_UNPLACED"
            out["verdict"] = "FAIL"
            out["errors"].append(
                "★零旋转装配报警：零件声明 up_axis=%s%s，总装契约要求 %s%s，"
                "而组件旋转是单位阵 —— 说明零件【根本没被摆正】就塞进装配了"
                % (_decl_axis, rec.get("up_sign"), _waxis, _wsign))
            out["action"] = ("本仓库装配定位全是纯平移、没有旋转输入 —— "
                             "只能在【重建该零件】里修，"
                             "不是总装时转一下就能好。")
        elif ident is False:
            # J2：声明冲突，但组件确实被转过 -> 声明与实物必有一错
            out["code"] = "J2_POSE_CONFLICT"
            out["verdict"] = "FAIL"
            out["errors"].append(
                "朝向冲突：零件声明 up_axis=%s%s，契约要求 %s%s"
                % (_decl_axis, rec.get("up_sign"), _waxis, _wsign))
            out["action"] = "核对 pr.json 与总装契约 up_contract.json，二者取其一。"
        else:
            out["code"] = "J2_POSE_CONFLICT"
            out["verdict"] = "FAIL"
            out["errors"].append(
                "朝向冲突（未拿到旋转矩阵）：声明 up_axis=%s%s != 契约 %s%s"
                % (_decl_axis, rec.get("up_sign"), _waxis, _wsign))
            out["action"] = "补上组件变换读取；在此之前不得判为通过。"

    # strict 处理：inferred 声明默认降级，其余按 verdict 收口
    if strict is None:
        strict = str(rec.get("source") or "") != "inferred"
    if out["verdict"] == "WARNING" and strict:
        out["verdict"] = "FAIL"
    out["ok"] = (out["verdict"] == "PASS")
    out["strict"] = bool(strict)
    return out


def assert_room_contract(room, parts=None, contract=None, world_up="Z",
                         world_up_sign=1, strict=True):
    """房间级朝向断言（J1/J2/J6）：该房间产出的零件必须与契约一致。

    Args:
        room:                    房间名
        parts:                   该房间的零件路径列表
        contract:                up_contract.json 内容
                                 ({"world_up","world_up_sign","rooms":{...}})
        world_up / world_up_sign: 契约缺省时使用

    Returns:
        {"ok","room","checked","missing":[..],"errors":[..],"warnings":[..],
         "per_part":{路径: 明细}}
    """
    out = {"ok": False, "room": room, "checked": 0, "missing": [],
           "errors": [], "warnings": [], "per_part": {}}
    _c = contract if isinstance(contract, dict) else {}
    _wup = _norm_axis(_c.get("world_up") or world_up) or "Z"
    _wsg = _norm_sign(_c.get("world_up_sign") if _c.get("world_up_sign")
                      is not None else world_up_sign) or 1
    _rooms = _c.get("rooms") if isinstance(_c.get("rooms"), dict) else {}
    _room_c = _rooms.get(room) if isinstance(_rooms.get(room), dict) else {}
    if _room_c:
        _ra = _norm_axis(_room_c.get("up_axis"))
        _rs = _norm_sign(_room_c.get("up_sign"))
        if _ra and _ra != _wup:
            out["warnings"].append(
                "J1：房间 %s 声明 up_axis=%s 与世界 %s 不一致"
                % (room, _ra, _wup))
        if _ra and _rs is not None and (_ra != _wup or _rs != _wsg):
            out["warnings"].append(
                "J1：房间 %s 的 up_axis/up_sign=%s%s 与世界 %s%s 不一致"
                % (room, _ra, _rs, _wup, _wsg))

    for p in (parts or []):
        r = check_assembly_pose(part_path=p, world_up=_wup,
                                world_up_sign=_wsg, strict=strict)
        out["per_part"][p] = {"verdict": r["verdict"], "code": r["code"],
                              "identity_like": r["identity_like"],
                              "declared": r["declared"]}
        out["checked"] += 1
        if r["code"] == "NO_PR_RECORD":
            out["missing"].append(p)
        out["errors"].extend(r["errors"])
        out["warnings"].extend(r["warnings"])
    # J6：缺 up_contract.json 且零件缺声明 -> 无声明 = 不可审计 -> 不通过
    out["ok"] = (not out["errors"] and not out["missing"]
                 and out["checked"] > 0)
    if out["checked"] == 0:
        out["errors"].append("没有给出任何零件 —— 无法断言（不得视为通过）")
    return out


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
        description="零件朝向声明 .pr.json 的写入 / 校验 / 总装断言")
    sub = ap.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser("write", help="写 <零件>.pr.json")
    w.add_argument("part")
    w.add_argument("--up-axis", default=None)
    w.add_argument("--up-sign", type=int, default=1)
    w.add_argument("--main-axis", default=None)
    w.add_argument("--base-plane", default=None)
    w.add_argument("--sketch-y-sign", type=int, default=None)
    w.add_argument("--bbox", default=None)
    w.add_argument("--source", default=None)

    r = sub.add_parser("read", help="读 <零件>.pr.json")
    r.add_argument("part")

    v = sub.add_parser("validate", help="校验 <零件>.pr.json")
    v.add_argument("part")

    a = sub.add_parser("check-assembly", help="总装朝向断言（单件）")
    a.add_argument("part")
    a.add_argument("--rotation", default=None,
                   help="旋转矩阵：9 或 16 个逗号分隔的数")
    a.add_argument("--world-up", default="Z")
    a.add_argument("--world-up-sign", type=int, default=1)

    cr = sub.add_parser("check-room", help="房间级朝向断言")
    cr.add_argument("room")
    cr.add_argument("--parts", default="", help="逗号分隔的零件路径")
    cr.add_argument("--contract", default=None, help="up_contract.json 路径")
    cr.add_argument("--world-up", default="Z")
    cr.add_argument("--world-up-sign", type=int, default=1)

    ns = ap.parse_args(argv)

    def _bbox(s):
        if not s:
            return None
        try:
            vals = [float(x) for x in str(s).replace(",", ",").split(",")
                    if x.strip()]
            return vals if len(vals) == 6 else None
        except Exception:
            return None

    if ns.cmd == "write":
        _emit(write_pr_record(ns.part, up_axis=ns.up_axis,
                              main_axis=ns.main_axis, up_sign=ns.up_sign,
                              base_plane=ns.base_plane,
                              sketch_y_sign=ns.sketch_y_sign,
                              bbox_mm=_bbox(ns.bbox), source=ns.source))
        return 0
    if ns.cmd == "read":
        _emit(read_pr_record(ns.part))
        return 0
    if ns.cmd == "validate":
        rr = read_pr_record(ns.part)
        if not rr["ok"]:
            _emit(rr)
            return 2
        _val = validate_pr_record(rr["record"])
        _emit(dict(rr, validation=_val))
        return 0 if _val["ok"] else 2
    if ns.cmd == "check-assembly":
        _rot = None
        if ns.rotation:
            try:
                _rot = [float(x) for x in ns.rotation.replace(",", ",").split(",")
                        if x.strip()]
            except Exception:
                _rot = None
        res = check_assembly_pose(part_path=ns.part, rotation=_rot,
                                  world_up=ns.world_up,
                                  world_up_sign=ns.world_up_sign)
        _emit(res)
        return 0 if res["ok"] else 3
    if ns.cmd == "check-room":
        _contract = None
        if ns.contract and os.path.isfile(ns.contract):
            try:
                with open(ns.contract, "r", encoding="utf-8") as f:
                    _contract = json.load(f)
            except Exception:
                _contract = None
        _parts = [x.strip() for x in (ns.parts or "").split(",") if x.strip()]
        res = assert_room_contract(ns.room, parts=_parts, contract=_contract,
                                   world_up=ns.world_up,
                                   world_up_sign=ns.world_up_sign)
        _emit(res)
        return 0 if res["ok"] else 3
    return 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
