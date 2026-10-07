# -*- coding: utf-8 -*-
"""防线②物理凭据 + 房间级联合校验的实际行为。"""
import os, sys, json
TOOLS = r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools"
sys.path.insert(0, TOOLS)
import defense_gate as dg

ROOM = "E2E验证"
print("=" * 88)
print("1) check_physics_attestation(room=%r)" % ROOM)
for n in ("check_physics_attestation", "physics_att_path"):
    f = getattr(dg, n, None)
    if not f:
        print("   %s 不存在" % n); continue
    try:
        r = f(ROOM)
        print("   %s ->" % n, json.dumps(r, ensure_ascii=False, default=str)[:1500])
    except Exception as e:
        print("   %s EXC: %r" % (n, e))

print()
print("=" * 88)
print("2) 房间级联合门禁（材料 + 物理）")
for n in ("room_end_gate", "check_room", "verify_room", "room_defense_check",
          "gate_room", "check_room_defense"):
    f = getattr(dg, n, None)
    if f:
        try:
            r = f(ROOM)
            print("   %s ->" % n, json.dumps(r, ensure_ascii=False, default=str)[:1500])
        except Exception as e:
            print("   %s EXC: %r" % (n, e))
print("   公开候选:", [n for n in dir(dg) if "room" in n.lower()][:30])

print()
print("=" * 88)
print("3) 对 45#(跨标准 BLOCK) 与 65Mn(赋材失败但凭据 PASS) 的结论对比")
G = os.path.join(TOOLS, "_verify_e2e")
for tag in ("4545", "65Mn", "Q355", "GCr15", "ZL104"):
    part = os.path.join(G, "DSH_承重支架_%s.SLDPRT" % tag)
    try:
        r = dg.check_material_attestation(part)
        print("   %-6s -> ok=%-5s level=%-6s reasons=%s"
              % (tag, r.get("ok"), r.get("level"),
                 (r.get("reasons") or ["-"])[0][:90]))
    except Exception as e:
        print("   %-6s EXC %r" % (tag, e))
