# -*- coding: utf-8 -*-
import os, json, sys
sys.path.insert(0, r"C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools")
import sw_bridge
sw = sw_bridge.get_sw()
import swapi

try:
    m = swapi.new_part()
    # 画圆 R=50mm
    m.begin_sketch("Front Plane")
    m.circle(0, 0, 50)
    m.end_sketch()
    # 拉伸成圆柱体，高 20mm
    m.extrude(20)
    m._visual_step("extrude")
    out_dir = r"C:\Users\j1877\Desktop\DSH-Check\SW"
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "DSH_circle.sldprt")
    rc = m.save(out_path)
    m.set_view_iso()
    shot = m.screenshot(os.path.join(out_dir, "circle_iso.png"))
    print(json.dumps({"ok": True, "path": out_path,
                      "screenshot": shot.get("path",""), "rc": rc},
                     ensure_ascii=False))
except Exception as e:
    import traceback
    print(json.dumps({"ok": False, "error": str(e),
                      "trace": traceback.format_exc()[-3000:]},
                     ensure_ascii=False))