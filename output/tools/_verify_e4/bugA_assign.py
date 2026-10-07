# -*- coding: utf-8 -*-
"""E4 复验 BUG-A：6 个牌号逐个真实赋材，读凭据，检验"凭据伪造密度"是否修好。

对每个牌号：new_part(material=G) → 建简单实体 → set_material(G) → save()
然后【独立读盘】解析 <part>.material.json，检查：
  · density_kg_m3        是否 == 实测值（1000 就是 1000）
  · expected_density_kg_m3 是否存在
  · material_applied / material_apply_note
  · ok                   赋材失败时必须为 False
  · warnings             是否出现"密度=1000 等同水"告警
"""
import json
import os
import sys

TOOLS = r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools"
OUT = os.path.join(TOOLS, "_verify_e4", "bugA_parts")
os.makedirs(OUT, exist_ok=True)
sys.path.insert(0, TOOLS)

import swapi  # noqa: E402

GRADES = ["65Mn", "Q355", "2A12", "Cr12MoV", "20CrMnTi", "ZL104"]
RESULTS = []

for G in GRADES:
    _tag = G.replace("#", "").replace("-", "_").replace("/", "_")
    PART = os.path.join(OUT, "DSH_bugA_%s.SLDPRT" % _tag)
    rec = {"grade": G, "part": PART}
    print("=" * 74)
    print("### 牌号 %s" % G)
    try:
        m = swapi.new_part(material=G)
        m.begin_sketch("Front Plane")
        m.polyline([(0, 0), (80, 0), (80, 40), (0, 40)])
        m.end_sketch()
        _ex = m.extrude(10)
        rec["extrude"] = _ex
        # 显式再赋一次（new_part 的 pending 在建模后才真正写入）
        _sm = m.set_material(G)
        rec["set_material"] = _sm
        print("  set_material ->", json.dumps(_sm, ensure_ascii=False, default=str))
        rec["material_result"] = getattr(m, "material_result", None)
        _mp = m.massprops(safe=True)
        rec["massprops"] = _mp
        print("  massprops ->", json.dumps(_mp, ensure_ascii=False, default=str))
        _res = m.save(PART)
        if not (_res or {}).get("ok"):
            try:
                m.rebuild()
            except Exception:
                pass
            _res = m.save(PART)
        rec["save"] = _res
        print("  save ok =", (_res or {}).get("ok"), "| exists =", os.path.exists(PART))
    except Exception as _e:
        rec["exception"] = repr(_e)
        print("  EXCEPTION:", repr(_e))

    # ── 独立读盘：凭据 ────────────────────────────────────────────────
    _att_p = PART + ".material.json"
    rec["attestation_path"] = _att_p
    rec["attestation_exists"] = os.path.exists(_att_p)
    if os.path.exists(_att_p):
        try:
            with open(_att_p, "r", encoding="utf-8") as f:
                _att = json.load(f)
        except Exception as _e:
            _att = {"_read_error": repr(_e)}
        rec["attestation"] = _att
        print("  ── 凭据 (%s) ──" % os.path.basename(_att_p))
        for _k in ("ok", "density_kg_m3", "expected_density_kg_m3",
                   "material_applied", "material_apply_note",
                   "attested_material", "attested_density_kg_m3",
                   "requested_material", "applied_name", "applied_density_kg_m3",
                   "attested_family", "approximate_match", "cross_standard",
                   "warnings", "_unsigned_reason", "save_ok"):
            if _k in _att:
                print("     %-24s = %s" % (_k, json.dumps(_att[_k], ensure_ascii=False, default=str)))
        print("     signed(_sig)             =", bool(_att.get("_sig")))
    else:
        print("  !! 凭据不存在:", _att_p)
    RESULTS.append(rec)

with open(os.path.join(TOOLS, "_verify_e4", "out_bugA.json"), "w", encoding="utf-8") as f:
    json.dump(RESULTS, f, ensure_ascii=False, indent=1, default=str)
print("\n[saved] out_bugA.json")
__RESULT__ = {"ok": True, "count": len(RESULTS),
              "out": os.path.join(TOOLS, "_verify_e4", "out_bugA.json")}
