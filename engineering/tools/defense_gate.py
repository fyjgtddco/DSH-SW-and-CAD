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
    (900, 2000, "plastic"),
)


def material_family(name_or_density):
    """判定材料家族；无法判定返回 None（调用方应视为"未知，不阻断"）。"""
    if name_or_density is None:
        return None
    if isinstance(name_or_density, bool):
        return None
    if isinstance(name_or_density, (int, float)):
        d = float(name_or_density)
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

def attestation_path(part_path):
    """材料凭据文件路径：<part>.SLDPRT → <part>.SLDPRT.material.json"""
    return str(part_path) + ".material.json"


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
    if not os.path.exists(str(part_path)):
        out["reasons"].append("零件文件不存在: %s" % part_path)
        return out
    att = _read_json(attestation_path(part_path))
    if not att:
        out["reasons"].append(
            "缺少材料凭据（%s.material.json）—— 该零件未经过材料防线校验。"
            % os.path.basename(str(part_path)))
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
            if os.path.abspath(_part_in_att) != os.path.abspath(str(part_path)):
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
    # (5) 显式记录过跨族拒绝 / 材料不匹配
    if att.get("material_mismatch_rejected"):
        out["reasons"].append("材料写入曾被判定为跨族/密度不符并拒绝（material_mismatch_rejected）")
    if att.get("ok") is False and _name is None:
        out["reasons"].append("凭据自报未成功写入材料")
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


def _publish_workspace_root():
    """把本副本的工程模式根目录声明给宿主（供宿主扩大受管根）。

    ── 【Bug8 修复】工程模式有工作区/安装目录两份副本。宿主按自己的
    #    toolsDir 算受管物理运行根，而报告可能写在另一份里，于是 /sign
    #    以 "report path outside managed physics_runs root" 拒签。
    #    这里由 Python 侧把【自己的根目录】写到宿主的 .defense/workspace.txt，
    #    使宿主能把该位置也纳入受管范围。
    """
    try:
        _rt = _find_runtime_file()
        if not _rt:
            return None
        # ── 【Bug8 修复·根目录写错】必须声明【仓库根】而非 engineering ────
        # 物理报告落在 tools/../../output/physics_runs（= 仓库根/output），
        #   而原实现写的是 tools/..（= engineering）—— 宿主按该声明判受管根，
        #   报告路径超出声明范围 → 拒签：
        #     "report path outside managed physics_runs root"
        # 现在写入的是 tools/../..（仓库根），与 _physics_run_roots 的
        #   "tools/../../output/physics_runs" 完全对齐。
        _root = os.path.abspath(os.path.join(_BASE_DIR, "..", ".."))
        # 兼容：engineering 目录也写一份（部分部署把 output 放在 engineering 下）
        _roots = [_root, os.path.abspath(os.path.join(_BASE_DIR, ".."))]
        _dest = os.path.join(os.path.dirname(_rt), "workspace.txt")
        with open(_dest, "w", encoding="utf-8") as f:
            f.write("\n".join(_roots))
        return _dest
    except Exception:
        return None


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


def _host_base():
    """宿主基地址；宿主未公布端口时返回 None。"""
    rt = _read_runtime()
    port = rt.get("port")
    if not port:
        return None
    host = str(rt.get("host") or "127.0.0.1")
    # [Bug8] 把本副本根目录声明给宿主，使其受管根覆盖两个部署位置
    try:
        _publish_workspace_root()
    except Exception:
        pass
    return "http://%s:%s" % (host, port)


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
    """请求宿主验签。

    Returns: {ok, reason, host_available}
      host_available=False 表示宿主不在（此时调用方按 fail-closed 处理）。
    """
    base = _host_base()
    if not base:
        return {"ok": False, "reason": "宿主未公布防线端点（runtime.json 缺失或无私端口）",
                "host_available": False}
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
    runs = _real_physics_runs()
    if _run:
        _same = [r for r in runs if str(r.get("run_id") or "") == _run]
        if _same:
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
                _host_note = "宿主判定端点无响应，已回落本地判定"
            else:
                _host_note = "宿主端口归属校验未通过（%s），已回落本地判定" % _own.get("reason")
        else:
            _host_note = "签名服务未装配（runtime.json 缺失）—— 请重启 DSH 使签名端点生效"
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
    return {"ok": not blockers, "room": room, "room_type": rtype or None,
            "require_physics": bool(require_physics),
            "blockers": blockers, "warnings": warnings, "details": details,
            "judged_by": "local"}


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
