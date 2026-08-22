"""Test: find working anchor configuration for A2."""
import sys, time
sys.path.insert(0, r'C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools')
import sw_bridge
import win32com.client, pythoncom
pythoncom.CoInitialize()
sw = sw_bridge.get_sw()
part_path = r'C:\Users\j1877\Desktop\DSH-Check\DSH_正方体长方体组合体.SLDPRT'

def get_actual_views(doc):
    v = doc.GetFirstView
    views = []
    while v:
        try:
            bb = v.GetOutline
            w = (bb[2]-bb[0])*1000
            if w > 300:
                views.append(('SHEET', w, (bb[3]-bb[1])*1000))
            else:
                views.append(('VIEW', w, (bb[3]-bb[1])*1000, tuple(bb)))
        except: pass
        try: v = v.GetNextView
        except: break
    return views

safe_x1,safe_y1 = 0.015, 0.115
safe_x2,safe_y2 = 0.416, 0.405

# Try many combinations systematically
found = False
for fc_y in [0.30, 0.32, 0.34, 0.36]:
    for tc_y in [0.15, 0.18, 0.20, 0.22]:
        for rc_y in [fc_y]:
            for ic_y in [0.15, 0.18, 0.22, 0.26, 0.28]:
                tmpl = sw_bridge._find_drawing_template(sw, 'A2')
                doc = sw.NewDocument(tmpl, 3, 0.594, 0.420)
                time.sleep(2)
                doc.CreateDrawViewFromModelView(part_path, '*前视', 0.115, fc_y, 0.5)
                time.sleep(0.3)
                doc.CreateDrawViewFromModelView(part_path, '*上视', 0.115, tc_y, 0.5)
                time.sleep(0.3)
                doc.CreateDrawViewFromModelView(part_path, '*右视', 0.316, rc_y, 0.5)
                time.sleep(0.3)
                doc.CreateDrawViewFromModelView(part_path, '*等轴测', 0.316, ic_y, 0.5)
                time.sleep(0.5)

                views = get_actual_views(doc)
                all_ok = True
                fail_reasons = []
                for v in views:
                    if v[0] == 'SHEET':
                        continue
                    _, w, h, bb = v
                    x1,y1,x2,y2 = bb
                    if not (x1>=safe_x1 and y1>=safe_y1 and x2<=safe_x2 and y2<=safe_y2):
                        all_ok = False
                        fail_reasons.append(f'({x1*1000:.0f},{y1*1000:.0f})')

                if all_ok:
                    print(f'FOUND! front_y={fc_y:.3f} top_y={tc_y:.3f} iso_y={ic_y:.3f}')
                    for v in views:
                        if v[0] == 'SHEET': continue
                        _, w, h, bb = v
                        print(f'  ({bb[0]*1000:.0f},{bb[1]*1000:.0f})-({bb[2]*1000:.0f},{bb[3]*1000:.0f}) size={w:.0f}x{h:.0f}')
                    found = True
                    break
                # Skip - just try next combination

            if found: break
        if found: break
        if found: break
    if found: break

if not found:
    print('No combination found for A2')
    # Try A1
    for fc_y in [0.40, 0.45, 0.50]:
        for tc_y in [0.20, 0.25, 0.30]:
            for ic_y in [0.20, 0.25, 0.30]:
                tmpl = sw_bridge._find_drawing_template(sw, 'A1')
                doc = sw.NewDocument(tmpl, 3, 0.841, 0.594)
                time.sleep(2)
                doc.CreateDrawViewFromModelView(part_path, '*前视', 0.20, fc_y, 0.5)
                time.sleep(0.3)
                doc.CreateDrawViewFromModelView(part_path, '*上视', 0.20, tc_y, 0.5)
                time.sleep(0.3)
                doc.CreateDrawViewFromModelView(part_path, '*右视', 0.50, fc_y, 0.5)
                time.sleep(0.3)
                doc.CreateDrawViewFromModelView(part_path, '*等轴测', 0.50, ic_y, 0.5)
                time.sleep(0.5)

                views = get_actual_views(doc)
                # A1 safe zone
                s_x1,s_y1 = 0.015, 0.1485
                s_x2,s_y2 = 0.589, 0.579
                all_ok = True
                for v in views:
                    if v[0] == 'SHEET': continue
                    _, w, h, bb = v
                    if not (bb[0]>=s_x1 and bb[1]>=s_y1 and bb[2]<=s_x2 and bb[3]<=s_y2):
                        all_ok = False
                        print(f'  FAIL ({bb[0]*1000:.0f},{bb[1]*1000:.0f}) size={w:.0f}x{h:.0f}')
                if all_ok:
                    print(f'FOUND A1! front_y={fc_y:.3f} top_y={tc_y:.3f} iso_y={ic_y:.3f}')
                    for v in views:
                        if v[0] == 'SHEET': continue
                        _, w, h, bb = v
                        print(f'  ({bb[0]*1000:.0f},{bb[1]*1000:.0f})-({bb[2]*1000:.0f},{bb[3]*1000:.0f}) size={w:.0f}x{h:.0f}')
                    found = True
                    break
                if found: break
            if found: break
        if found: break
    if not found:
        print('No combination found for A1 either')
