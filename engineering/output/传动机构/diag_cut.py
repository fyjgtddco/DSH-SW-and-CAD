# -*- coding: utf-8 -*-
"""诊断: rect 草图 cut 选择失败原因"""
import os
import swapi

m = swapi.new_part()
# 简单方块
m.begin_sketch("Front Plane")
m.rect(0, 0, 40, 40)
m.end_sketch()
m.extrude(20)

# 在上表面画矩形槽草图
m.begin_sketch_on_face(0, 0, 20)
m.rect(0, 0, 20, 20)
m.end_sketch()

# 诊断: 手动尝试选择
sk_name = m._find_last_sketch_name()
print("LAST_SKETCH:", sk_name)
m.model.ClearSelection2(True)
sel = m.ext.SelectByID2(sk_name, "SKETCH", 0, 0, 0, False, 0, m._empty, 0)
print("SELECT_SKETCH_OK:", sel)
try:
    m.model.EditSketch()
    print("EDITSKETCH_OK")
except Exception as e:
    print("EDITSKETCH_ERR:", repr(e))
ok, info = m.select_all_sketch_segments()
print("SELECT_SEG_OK:", ok, "INFO:", info)
try:
    print("SEL_DIAG:", m._sel_diag)
except Exception:
    pass
try:
    c = m.model.SelectionManager.GetSelectedObjectCount2(-1)
    print("COUNT:", c)
except Exception as e:
    print("CNT_ERR:", repr(e))
m.screenshot(os.path.join(os.path.dirname(os.path.abspath(__file__)), "diag.png"))
print("DONE_DIAG")
