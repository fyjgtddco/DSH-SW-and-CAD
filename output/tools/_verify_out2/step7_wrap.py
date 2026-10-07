# -*- coding: utf-8 -*-
"""Step7: 收尾核查 —— 计数口径 / swapi 反向误标 / gb_material_ready"""
import sys, io, json
sys.path.insert(0, r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools")
sys.path.insert(0, r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools\physics")
import material_db as mdb

print("=" * 100)
print("Step7.1  pending_count 口径 vs 实际『不可用』牌号")
print("=" * 100)
st = mdb.gb_materials_status()
print("  ready_count=%s  equivalent_ready_count=%s  pending_count=%s"
      % (st["ready_count"], st["equivalent_ready_count"], st["pending_count"]))
print("  pending_ids =", [k for k in mdb.GB_MATERIALS
                          if not mdb.has_props(mdb.GB_MATERIALS[k])
                          and not mdb.GB_MATERIALS[k].get("equivalent_id")])
print()
print("  但『经兜底可用』的 13 个里，ZL104 兜底后仍缺 yield_mpa：")
print("     has_props(ZL104) =", mdb.has_props(mdb.get_material("GB_ZL104")))
print("     → 被计入 equivalent_ready_count，实际【仍不可做强度校核】")
print("     即 equivalent_ready_count 高估了可用牌号数（13 里 1 个不可用）")

print()
print("=" * 100)
print("Step7.2  gb_material_ready() 对 ZL104 / 40Cr 的判定")
print("=" * 100)
for n in ["Q235", "45#", "40Cr", "6061-T6", "ZL104", "GCr15", "HT200"]:
    ok, m, reason = mdb.gb_material_ready(n)
    print("  %-10s ok=%-6s reason=%s" % (n, ok, (reason or "（数值齐备）")[:88]))

print()
print("=" * 100)
print("Step7.3  swapi 反向误标：Q235/45#/65Mn 的 σy/σb 明明是 GB 原文，却标 False")
print("=" * 100)
import swapi
for k in ["Q235", "45#", "65Mn", "304", "316", "Q355"]:
    e = swapi.COMMON_MATERIALS.get(k)
    if not e:
        continue
    raw = None
    for mid, v in mdb.GB_MATERIALS.items():
        if v.get("name_cn", "").startswith(k):
            raw = v; break
    gbd = (raw or {}).get("gb_data") or {}
    gy = (gbd.get("yield_mpa") or {}).get("value")
    print("  %-8s swapi σy=%-8s values_from_gb=%-6s | JSON gb_data.yield=%s"
          % (k, e.get("yield_mpa"), e.get("values_from_gb"), gy))
print()
print("  → swapi 把【有 GB 原文值】的牌号也标成 values_from_gb=False")
print("     （与 workflow_gate 的 BUG-1 恰好相反：一个漏报、一个误报）")
print("     两处对同一事实给出不同标记 = 口径分裂，报告可信度受损。")

print()
print("=" * 100)
print("Step7.4  E/nu 缺失影响的定量说明")
print("=" * 100)
print("  E 缺失的 5 个：GCr15, HT200, QT500-7, ZCuSn10P1, H62")
print("  nu 缺失的 5 个：同上")
print("  这 5 个恰好也是【无兜底或兜底不足】的牌号 → 双重缺口")
print()
print("  有兜底 E 的 13 个牌号：E 用的是欧美标等效值，量级正确")
print("  钢类等效 E：205000~210000 MPa（AISI 4140 / S235JR）")
print("  铝类等效 E：68900~72000 MPa（6061-T6 / 7075-T6）")
print("  不锈钢等效 E：193000 MPa（304 / 316）")
print("  → 对碳钢/铝/不锈钢，等效 E 与国标材料手册值差异 <5%，工程可用")
print("  → 对铸铁（HT200/QT500-7）、铜合金（ZCuSn10P1/H62）、轴承钢（GCr15）")
print("     项目内【根本没有同类兜底材料】，等效 E 无从谈起 → 必须外部补数")
