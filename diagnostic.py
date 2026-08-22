"""Quick diagnostic: test A2 view creation with algorithm coordinates."""
import sys, time
sys.path.insert(0, r'C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools')
import sw_bridge
import win32com.client, pythoncom
pythoncom.CoInitialize()
sw = sw_bridge.get_sw()
tmpl = sw_bridge._find_drawing_template(sw, 'A2')
part_path = r'C:\Users\j1877\Desktop\DSH-Check\DSH_正方体长方体组合体.SLDPRT'
print(f'Template: {tmpl.split(chr(92))[-1]}')

doc = sw.NewDocument(tmpl, 3, 0.594, 0.420)
time.sleep(2)

# Algorithm coordinates for A2@1:2 (in meters)
positions = [
    ('前视', 0.115, 0.332, 0.5),
    ('上视', 0.115, 0.188, 0.5),
    ('右视', 0.316, 0.332, 0.5),
    ('等轴测', 0.316, 0.188, 0.5),
]

for name, cx, cy, sc in positions:
    ok = doc.CreateDrawViewFromModelView(part_path, f'*{name}', cx, cy, sc)
    print(f'  create {name}: ok={ok}')
    time.sleep(0.3)

# Read all views
print()
v = doc.GetFirstView
idx = 0
while v and idx < 10:
    try:
        bb = v.GetOutline
        w_m = bb[2] - bb[0]
        h_m = bb[3] - bb[1]
        w_mm = w_m * 1000
        if w_mm > 300:
            print(f'[{idx}] SHEET: {w_mm:.0f}x{h_m*1000:.0f}mm')
        else:
            print(f'[{idx}] ({bb[0]*1000:.1f},{bb[1]*1000:.1f})-({bb[2]*1000:.1f},{bb[3]*1000:.1f}) size={w_mm:.1f}x{h_m*1000:.1f}mm')
    except Exception as e:
        print(f'[{idx}] error: {e}')
    try:
        v = v.GetNextView
    except:
        break
    idx += 1

# Safe zone for A2
safe_x1, safe_y1 = 15, 115
safe_x2, safe_y2 = 416, 405
print(f'\nSafe zone: ({safe_x1},{safe_y1})-({safe_x2},{safe_y2})mm')
print('All views should be within this area')
