# -*- coding: utf-8 -*-
"""核验 .material.json 凭据是否被防线接受（尤其赋材失败的 65Mn/Q355/GCr15）。"""
import os, sys, json
TOOLS = r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools"
sys.path.insert(0, TOOLS)
import defense_gate as dg

G = os.path.join(TOOLS, "_verify_e2e")
for tag in ("4545", "65Mn", "Q355", "GCr15", "ZL104"):
    part = os.path.join(G, "DSH_承重支架_%s.SLDPRT" % tag)
    print("=" * 88)
    print("零件:", os.path.basename(part), "| 存在 =", os.path.exists(part))
    ap = None
    for n in ("attestation_path",):
        f = getattr(dg, n, None)
        if f:
            try:
                ap = f(part)
            except Exception as e:
                print("  %s() EXC %r" % (n, e))
    print("  凭据路径:", ap)
    for n in ("verify_material_attestation", "verify_attestation",
              "check_material_attestation", "material_attestation_status"):
        f = getattr(dg, n, None)
        if not f:
            continue
        try:
            r = f(part)
            print("  %s ->" % n, json.dumps(r, ensure_ascii=False, default=str)[:700])
        except Exception as e:
            print("  %s EXC: %r" % (n, e))
print()
print("defense_gate 里与材料凭据相关的公开函数：")
print([n for n in dir(dg) if "attest" in n.lower() or "material" in n.lower()])
