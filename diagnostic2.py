"""Test: create views at algorithm coords for A2, then check actual positions."""
import sys, time
sys.path.insert(0, r'C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools')
import sw_bridge
import win32com.client, pythoncom
pythoncom.CoInitialize()
sw = sw_bridge.get_sw()
tmpl = sw_bridge._find_drawing_template(sw, 'A2')
part_path = r'C:\Users\j1877\Desktop\DSH-Check\DSH_正方体长方体组合体.SLDPRT'

doc = sw.NewDocument(tmpl, 3, 0.594, 0.420)
time.sleep(2)

# Algorithm coords for A2@1:2 (from _compute_layout output)
# These are the CENTER coordinates (meters from bottom-left)
positions = [
    ('*前视', 0.115, 0.332),
    ('*上视', 0.115, 0.188),
    ('*右视', 0.316, 0.332),
    ('*等轴测', 0.316, 0.188),
]

for name, cx, cy in positions:
    ok = doc.CreateDrawViewFromModelView(part_path, name, cx, cy, 0.5)
    print(f'  create {name}: ok={ok}')
    time.sleep(0.3)

# Read actual positions
print()
safe_x1, safe_y1 = 0.015, 0.115
safe_x2, safe_y2 = 0.416, 0.405

v = doc.GetFirstView
idx = 0
all_ok = True
while v and idx < 10:
    try:
        bb = v.GetOutline
        w_m = bb[2] - bb[0]
        if w_m > 0.3:
            print(f'[{idx}] SHEET: {(bb[2]-bb[0])*1000:.0f}x{(bb[3]-bb[1])*1000:.0f}mm')
        else:
            x1, y1, x2, y2 = bb
            in_safe = x1 >= safe_x1 and y1 >= safe_y1 and x2 <= safe_x2 and y2 <= safe_y2
            status = 'OK' if in_safe else 'FAIL'
            if not in_safe:
                all_ok = False
            print(f'[{idx}] ({x1*1000:.1f},{y1*1000:.1f})-({x2*1000:.1f},{y2*1000:.1f}) -> {status}')
    except Exception as e:
        print(f'[{idx}] error: {e}')
    try:
        v = v.GetNextView
    except:
        break
    idx += 1

print(f'\nAll views in safe zone: {all_ok}')
doc.SaveAs(r'C:\Users\j1877\Desktop\DSH-Check\test_a2_algo.SLDDRW')
print('Saved to test_a2_algo.SLDDRW')
