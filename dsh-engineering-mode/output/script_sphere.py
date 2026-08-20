# -*- coding: utf-8 -*-
import os, json, sys, math
sys.path.insert(0, r"C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools")
import sw_bridge
sw = sw_bridge.get_sw()
import swapi

try:
    m = swapi.new_part()
    # 在 Front Plane 画半圆 + 中心线
    m.begin_sketch("Front Plane")
    # 半圆弧：从 (0, -50) 到 (0, 50)，圆心在 (50, 0) 不对...
    # 正确：圆心在原点，画右半圆弧
    # 用 polyline 画半圆轮廓：从 (0, -50) 到 (0, 50) 再回到 (0, -50)
    # 但 swapi 没有 arc 方法，只能用 polyline 近似或用圆 + 切除
    # 
    # 方法：画整圆，画中心线，旋转时只取右半部分
    # 但旋转需要封闭轮廓...
    # 
    # 最简单：画一个圆心在 (50, 0) 的圆，半径 50，然后画中心线在 Y 轴
    # 这样圆的右半部分就是半圆
    m.circle(50, 0, 50)  # 圆心 (50, 0)，半径 50，这样圆经过原点
    m.centerline(0, -60, 0, 60)  # Y 轴中心线作为旋转轴
    m.end_sketch()
    # 旋转 360 度生成球
    m.revolve(360)
    m._visual_step("revolve")
    
    out_dir = r"C:\Users\j1877\Desktop\DSH-Check\SW"
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "DSH_sphere.sldprt")
    rc = m.save(out_path)
    m.set_view_iso()
    shot = m.screenshot(os.path.join(out_dir, "sphere_iso.png"))
    print(json.dumps({"ok": True, "path": out_path,
                      "screenshot": shot.get("path",""), "rc": rc},
                     ensure_ascii=False))
except Exception as e:
    import traceback
    print(json.dumps({"ok": False, "error": str(e),
                      "trace": traceback.format_exc()[-3000:]},
                     ensure_ascii=False))