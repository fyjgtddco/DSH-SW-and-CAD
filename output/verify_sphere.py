# -*- coding: utf-8 -*-
import json, sys
sys.path.insert(0, r"C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools")
import sw_bridge
sw = sw_bridge.get_sw()

try:
    # 打开球体零件
    part_path = r"C:\Users\j1877\Desktop\DSH-Check\SW\DSH_sphere.SLDPRT"
    doc_type = 1
    errs = sw_bridge.win32com.client.VARIANT(sw_bridge.pythoncom.VT_BYREF | sw_bridge.pythoncom.VT_I4, 0)
    warns = sw_bridge.win32com.client.VARIANT(sw_bridge.pythoncom.VT_BYREF | sw_bridge.pythoncom.VT_I4, 0)
    doc = sw.OpenDoc6(part_path, doc_type, 1, "", errs, warns)
    import time; time.sleep(1)
    doc = sw.ActiveDoc
    
    # 获取质量属性
    mp = doc.GetMassProperties()
    # mp = [cogX, cogY, cogZ, volume, surface_area, mass, Ixx, Iyy, Izz, Ixy, Ixz, Iyz]
    volume_mm3 = mp[3] * 1e9  # m³ -> mm³
    surface_mm2 = mp[4] * 1e6  # m² -> mm²
    
    # 理论值：R=50mm
    # 体积 = 4/3 * π * r³ = 523,598.78 mm³
    # 表面积 = 4 * π * r² = 31,415.93 mm²
    import math
    r = 50
    theory_vol = 4/3 * math.pi * r**3
    theory_surf = 4 * math.pi * r**2
    
    result = {
        "ok": True,
        "volume_mm3": round(volume_mm3, 2),
        "surface_mm2": round(surface_mm2, 2),
        "theory_volume": round(theory_vol, 2),
        "theory_surface": round(theory_surf, 2),
        "volume_match": abs(volume_mm3 - theory_vol) < 1,
        "is_3d_solid": volume_mm3 > 1000  # 确实是实体
    }
    doc.CloseDoc()
except Exception as e:
    result = {"ok": False, "error": str(e)}
print(json.dumps(result, ensure_ascii=False, indent=2))