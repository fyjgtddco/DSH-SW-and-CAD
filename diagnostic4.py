"""Test: create views on A1, read actual positions, check bounds."""
import sys, time
sys.path.insert(0, r'C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools')
import sw_bridge
import win32com.client, pythoncom
pythoncom.CoInitialize()
sw = sw_bridge.get_sw()
part_path = r'C:\Users\j1877\Desktop\DSH-Check\DSH_正方体长方体组合体.SLDPRT'

# Test A1 with various scales
for scale in [1.0, 0.5, 0.33, 0.25]:
    tmpl = sw_bridge._find_drawing_template(sw, 'A1')
    doc = sw.NewDocument(tmpl, 3, 0.841, 0.594)
    time.sleep(2)
    
    names = ['*前视', '*上视', '*右视', '*等轴测']
    # Use well-separated anchor points
    coords = [
        (0.158, 0.474),
        (0.158, 0.264),
        (0.445, 0.474),
        (0.445, 0.264),
    ]
    for name, (cx, cy) in zip(names, coords):
        ok = doc.CreateDrawViewFromModelView(part_path, name, cx, cy, scale)
        time.sleep(0.3)
    
    # Read actual positions
    safe_x1, safe_y1 = 0.015, 0.158
    safe_x2, safe_y2 = 0.589, 0.579
    
    v = doc.GetFirstView
    idx = 0
    ok_count = 0
    fail_views = []
    while v and idx < 10:
        try:
            bb = v.GetOutline
            w_m = bb[2] - bb[0]
            if w_m > 0.3:
                pass
            else:
                x1,y1,x2,y2 = bb
                in_safe = x1>=safe_x1 and y1>=safe_y1 and x2<=safe_x2 and y2<=safe_y2
                if in_safe:
                    ok_count += 1
                else:
                    fail_views.append(f'{idx}:{x1*1000:.0f},{y1*1000:.0f}')
        except Exception:
            pass
        try:
            v = v.GetNextView
        except Exception:
            break
        idx += 1
    
    ratio = int(1/scale) if scale < 1 else int(scale)
    print(f'A1 @ 1:{ratio}: {ok_count}/4 in safe zone  fails={fail_views}')

# Also test A3
print()
for scale in [1.0, 0.5]:
    tmpl = sw_bridge._find_drawing_template(sw, 'A3')
    doc = sw.NewDocument(tmpl, 3, 0.420, 0.297)
    time.sleep(2)
    
    names = ['*前视', '*上视', '*右视', '*等轴测']
    coords = [
        (0.085, 0.233),
        (0.085, 0.134),
        (0.224, 0.233),
        (0.224, 0.134),
    ]
    for name, (cx, cy) in zip(names, coords):
        ok = doc.CreateDrawViewFromModelView(part_path, name, cx, cy, scale)
        time.sleep(0.3)
    
    safe_x1,safe_y1 = 0.015, 0.084
    safe_x2,safe_y2 = 0.294, 0.282
    
    v = doc.GetFirstView
    idx = 0
    ok_count = 0
    fail_views = []
    while v and idx < 10:
        try:
            bb = v.GetOutline
            w_m = bb[2]-bb[0]
            if w_m > 0.3: pass
            else:
                x1,y1,x2,y2 = bb
                in_safe = x1>=safe_x1 and y1>=safe_y1 and x2<=safe_x2 and y2<=safe_y2
                if in_safe:
                    ok_count += 1
                else:
                    fail_views.append(f'{idx}:{x1*1000:.0f},{y1*1000:.0f}')
        except Exception: pass
        try: v = v.GetNextView
        except Exception: break
        idx += 1
    
    ratio = int(1/scale) if scale < 1 else int(scale)
    print(f'A3 @ 1:{ratio}: {ok_count}/4 in safe zone  fails={fail_views}')
