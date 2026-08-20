"""枚举所有基准面名称"""
import os, sys, time, pythoncom, win32com.client
sys.path.insert(0, r"C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools")
import sw_bridge, swapi
sw = sw_bridge.get_sw()

try:
    while sw.ActiveDoc:
        sw.ActiveDoc.CloseDoc()
        time.sleep(0.3)
except:
    pass

# 新建零件
tmpl = swapi.get_part_template(sw)
model = sw.NewDocument(tmpl, 0, 0.1, 0.1)
time.sleep(1)

# 枚举所有特征，找基准面
feat = model.FirstFeature
while feat:
    try:
        fname = feat.Name
        ftype = feat.GetTypeName
        if "Plane" in ftype or "RefPlane" in ftype or "基准面" in ftype:
            print(f"  Plane: {fname} ({ftype})")
            
            # 尝试用SelectByID2选中
            empty = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)
            
            # 试中文名
            model.ClearSelection2(True)
            r1 = model.Extension.SelectByID2(fname, "PLANE", 0, 0, 0, False, 0, empty, 0)
            print(f"    SelectByID2({fname}): {r1}")
            
            # 试英文名映射
            en_map = {"前视基准面": "Front Plane", "上视基准面": "Top Plane", "右视基准面": "Right Plane"}
            if fname in en_map:
                model.ClearSelection2(True)
                r2 = model.Extension.SelectByID2(en_map[fname], "PLANE", 0, 0, 0, False, 0, empty, 0)
                print(f"    SelectByID2({en_map[fname]}): {r2}")
    except Exception as e:
        print(f"  Error: {e}")
    try:
        feat = feat.GetNextFeature()
    except:
        feat = None

print("Done")