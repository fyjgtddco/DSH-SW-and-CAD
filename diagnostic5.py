"""Test: push iso view higher to avoid title block."""
import sys, time
sys.path.insert(0, r'C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools')
import sw_bridge
import win32com.client, pythoncom
pythoncom.CoInitialize()
sw = sw_bridge.get_sw()
part_path = r'C:\Users\j1877\Desktop\DSH-Check\DSH_正方体长方体组合体.SLDPRT'

def test_iso_height(label, front_cy, top_cy, iso_cy):
    tmpl = sw_bridge._find_drawing_template(sw, 'A2')
    doc = sw.NewDocument(tmpl, 3, 0.594, 0.420)
    time.sleep(2)
    
    names = ['*前视', '*上视', '*右视', '*等轴测']
    coords = [
        (0.115, front_cy),
        (0.115, top_cy),
        (0.316, front_cy),
        (0.316, iso_cy),
    ]
    for name, (cx, cy) in zip(names, coords):
        ok = doc.CreateDrawViewFromModelView(part_path, name, cx, cy, 0.5)
        time.sleep(0.3)
    
    safe_x1,safe_y1 = 0.015, 0.115
    safe_x2,safe_y2 = 0.416, 0.405
    
    v = doc.GetFirstView
    idx = 0
    results = []
    while v and idx < 10:
        try:
            bb = v.GetOutline
            w_m = bb[2]-bb[0]
            if w_m > 0.3:
                results.append(('SHEET', True))
            else:
                x1,y1,x2,y2 = bb
                in_safe = x1>=safe_x1 and y1>=safe_y1 and x2<=safe_x2 and y2<=safe_y2
                results.append((f'({x1*1000:.0f},{y1*1000:.0f})', in_safe))
        except Exception: pass
        try: v = v.GetNextView
        except: break
        idx += 1
    
    all_ok = all(r[1] for r in results if r[0] != 'SHEET')
    status = "ALL OK" if all_ok else "FAIL"
    print(f'  {label}: {status}')
    for nm, r in zip(names+['SHEET'], results):
        print(f'    {nm}: {r[0]} -> {"OK" if r[1] else "FAIL"}')
    return all_ok

print('=== A2: varying iso view y position ===')
test_iso_height('front=332,top=188,iso=188', 0.332, 0.188, 0.188)
test_iso_height('front=332,top=188,iso=220', 0.332, 0.188, 0.220)
test_iso_height('front=332,top=188,iso=250', 0.332, 0.188, 0.250)
test_iso_height('front=332,top=188,iso=280', 0.332, 0.188, 0.280)
test_iso_height('front=332,top=188,iso=300', 0.332, 0.188, 0.300)
test_iso_height('front=350,top=200,iso=200', 0.350, 0.200, 0.200)
test_iso_height('front=350,top=200,iso=230', 0.350, 0.200, 0.230)
test_iso_height('front=370,top=220,iso=220', 0.370, 0.220, 0.220)
