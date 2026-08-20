# -*- coding: utf-8 -*-
import math, os, json, traceback, sys
sys.path.insert(0, r"C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools")
import sw_bridge
sw = sw_bridge.get_sw()
import swapi

try:
    m = swapi.new_part()
    m.begin_sketch("Front Plane")
    m.polyline([(0, 34.641), (-30, 0), (30, 0), (0, 34.641)])
    m.end_sketch()
    r_in = 60 * math.sqrt(3) / 6
    draft_deg = math.degrees(math.atan(r_in / 50))
    feat = m.fm.FeatureExtrusion3(
        True, False, False,
        0, draft_deg, 50, 0,
        False, False, False, False, 0, 0,
        False, False, False, False, True, False, True,
        0, 0, False
    )
    m._visual_step("extrude+draft")
    _out_dir = r"C:\Users\j1877\Desktop\dsh-engineering-mode\output"
    os.makedirs(_out_dir, exist_ok=True)
    out_path = os.path.join(_out_dir, "DSH_triangular_pyramid.sldprt")
    rc = m.save(out_path)
    m.set_view_iso()
    shot = m.screenshot(os.path.join(_out_dir, "pyramid_iso.png"))
    print(json.dumps({"ok": True, "path": out_path, "screenshot": shot.get("path",""), "rc": rc}, ensure_ascii=False))
except Exception as e:
    print(json.dumps({"ok": False, "error": str(e), "trace": traceback.format_exc()[-3000:]}, ensure_ascii=False))