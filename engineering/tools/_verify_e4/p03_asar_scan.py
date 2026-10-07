# -*- coding: utf-8 -*-
"""在 app.asar 中定位宿主的 defense/sign 实现，确认凭据字段白名单。只读。"""
import os
import re

P = r"Z:\DSH\resources\app.asar"
KEYS = [b"applied_density_kg_m3", b"material_apply_note", b"expected_density_kg_m3",
        b"dsh-material-attestation", b"defense/sign", b"attested_density_kg_m3",
        b"material_applied", b"attested_material"]
size = os.path.getsize(P)
print("asar size =", size)
found = {k: [] for k in KEYS}
CH = 8 << 20
overlap = 4096
with open(P, "rb") as f:
    off = 0
    prev = b""
    while True:
        buf = f.read(CH)
        if not buf:
            break
        blob = prev + buf
        base = off - len(prev)
        for k in KEYS:
            st = 0
            while True:
                i = blob.find(k, st)
                if i < 0:
                    break
                found[k].append(base + i)
                st = i + 1
                if len(found[k]) > 40:
                    break
        prev = blob[-overlap:]
        off += len(buf)
for k in KEYS:
    print("%-28s n=%-4d %s" % (k.decode(), len(found[k]), found[k][:8]))
