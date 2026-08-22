"""Test: different strategies for iso view placement."""
import sys, time
sys.path.insert(0, r'C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools')
import sw_bridge
import win32com.client, pythoncom
pythoncom.CoInitialize()
sw = sw_bridge.get_sw()
part_path = r'C:\Users\j1877\Desktop\DSH-Check\DSH_正方体长方体组合体.SLDPRT'

def get_view_sizes(doc):
    v = doc.GetFirstView
    views = []
    while v:
        try:
            bb = v.GetOutline
            w = (bb[2]-bb[0])*1000
            if w > 300:
                views.append(('SHEET', w, (bb[3]-bb[1])*1000))
            else:
                views.append(('VIEW', w, (bb[3]-bb[1])*1000, bb))
        except: pass
        try: v = v.GetNextView
        except: break
    return views

def test(label, coords, safe_x1, safe_y1, safe_x2, safe_y2):
    tmpl = sw_bridge._find_drawing_template(sw, 'A2')
    doc = sw.NewDocument(tmpl, 3, 0.594, 0.420)
    time.sleep(2)
    
    names = ['*前视', '*上视', '*右视', '*等轴测']
    for name, (cx, cy) in zip(names, coords):
        doc.CreateDrawViewFromModelView(part_path, name, cx, cy, 0.5)
        time.sleep(0.3)
    
    views = get_view_sizes(doc)
    all_ok = True
    print(f'  {label}:')
    for v in views:
        if v[0] == 'SHEET':
            continue
        _, w, h, bb = v
        x1,y1,x2,y2 = bb
        in_safe = x1>=safe_x1 and y1>=safe_y1 and x2<=safe_x2 and y2<=safe_y2
        if not in_safe:
            all_ok = False
        print(f'    ({x1*1000:.0f},{y1*1000:.0f})-({x2*1000:.0f},{y2*1000:.0f}) size={w:.0f}x{h:.0f} {"OK" if in_safe else "FAIL"}')
    return all_ok

safe_x1,safe_y1 = 0.015, 0.115
safe_x2,safe_y2 = 0.416, 0.405

print('=== A2 Tests ===')
# Strategy 1: Algorithm coords
test('algo', [
    (0.115, 0.332), (0.115, 0.188), (0.316, 0.332), (0.316, 0.188)
], safe_x1, safe_y1, safe_x2, safe_y2)

# Strategy 2: Put iso view much higher
test('iso_higher', [
    (0.115, 0.332), (0.115, 0.188), (0.316, 0.332), (0.316, 0.280)
], safe_x1, safe_y1, safe_x2, safe_y2)

# Strategy 3: Wider spacing between cols
test('wider', [
    (0.08, 0.332), (0.08, 0.188), (0.38, 0.332), (0.38, 0.188)
], safe_x1, safe_y1, safe_x2, safe_y2)

# Strategy 4: ISO as LAST, very high
test('iso_last_high', [
    (0.316, 0.332), (0.115, 0.332), (0.115, 0.188), (0.316, 0.300)
], safe_x1, safe_y1, safe_x2, safe_y2)

# Strategy 5: Try creating iso FIRST
print()
print('=== Create order test ===')
tmpl = sw_bridge._find_drawing_template(sw, 'A2')
doc = sw.NewDocument(tmpl, 3, 0.594, 0.420)
time.sleep(2)
# Create iso first
doc.CreateDrawViewFromModelView(part_path, '*等轴测', 0.316, 0.188, 0.5)
time.sleep(0.3)
doc.CreateDrawViewFromModelView(part_path, '*前视', 0.115, 0.332, 0.5)
time.sleep(0.3)
doc.CreateDrawViewFromModelView(part_path, '*上视', 0.115, 0.188, 0.5)
time.sleep(0.3)
doc.CreateDrawViewFromModelView(part_path, '*右视', 0.316, 0.332, 0.5)
time.sleep(0.3)
views = get_view_sizes(doc)
all_ok = True
for v in views:
    if v[0] == 'SHEET':
        continue
    _, w, h, bb = v
    x1,y1,x2,y2 = bb
    in_safe = x1>=safe_x1 and y1>=safe_y1 and x2<=safe_x2 and y2<=safe_y2
    if not in_safe:
        all_ok = False
    print(f'  ({x1*1000:.0f},{y1*1000:.0f})-({x2*1000:.0f},{y2*1000:.0f}) size={w:.0f}x{h:.0f} {"OK" if in_safe else "FAIL"}')
print(f'  All OK: {all_ok}')
