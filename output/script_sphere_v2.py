# -*- coding: utf-8 -*-
import os, json, sys
sys.path.insert(0, r"C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools")
import sw_bridge
sw = sw_bridge.get_sw()
import swapi

try:
    m = swapi.new_part()
    m.begin_sketch("Front Plane")
    m.circle(50, 0, 50)  # 圆心(50,0), R=50, 圆过原点
    m.centerline(0, -60, 0, 60)  # Y轴中心线
    m.end_sketch()
    m.revolve(360)
    m._visual_step("revolve")
    
    out_dir = r"C:\Users\j1877\Desktop\DSH-Check\SW"
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "DSH_sphere.sldprt")
    rc = m.save(out_path)
    m.set_view_iso()
    shot = m.screenshot(os.path.join(out_dir, "sphere_iso.png"))
    print(json.dumps({"ok": True, "path": out_path, "screenshot": shot.get("path","")}, ensure_ascii=False))
except Exception as e:
    import traceback
    print(json.dumps({"ok": False, "error": str(e), "trace": traceback.format_exc()[-2000:]}, ensure_ascii=False))