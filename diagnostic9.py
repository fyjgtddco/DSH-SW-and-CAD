"""Pure pre-calculation - fixed reference measurement."""
import sys, time, os
sys.path.insert(0, r'C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools')
import sw_bridge
import win32com.client, pythoncom
pythoncom.CoInitialize()
sw = sw_bridge.get_sw()
part_path = r'C:\Users\j1877\Desktop\DSH-Check\DSH_正方体长方体组合体.SLDPRT'
part_abs = os.path.abspath(part_path)
output_path = r'C:\Users\j1877\Desktop\DSH-Check\test_pure4.SLDDRW'

# Step 1: Open part
print("=== Step 1: Open part ===")
result = sw_bridge.cmd_open(sw, part_path)
print(f"  ok={result.get('ok')}")

# Step 2: Create A3 reference to measure SW auto-scale
print("\n=== Step 2: Measure reference view ===")
tmpl = sw_bridge._find_drawing_template(sw, 'A3')
ref_doc = sw.NewDocument(tmpl, 3, 0.420, 0.297)
time.sleep(2)
ref_doc.CreateDrawViewFromModelView(part_abs, '*前视', 0.085, 0.20, 0)
time.sleep(0.5)

# Find the view by iterating from first non-sheet view
v = ref_doc.GetFirstView
ref_w_mm, ref_h_mm = None, None
view_idx = 0
while v:
    try:
        bb = v.GetOutline
        w = (bb[2]-bb[0])*1000
        if w > 300:
            print(f"  [{view_idx}] SHEET: {w:.0f}x{(bb[3]-bb[1])*1000:.0f}mm")
        else:
            ref_w_mm = w
            ref_h_mm = (bb[3]-bb[1])*1000
            print(f"  [{view_idx}] VIEW: {w:.1f}x{ref_h_mm:.1f}mm at ({bb[0]*1000:.1f},{bb[1]*1000:.1f})")
            break
    except Exception as e:
        print(f"  [{view_idx}] error: {e}")
    try:
        v = v.GetNextView
    except:
        break
    view_idx += 1

# Close reference - don't activate it
try:
    sw.CloseAllDocuments(False)
except:
    pass
time.sleep(0.5)

# Re-open part
result = sw_bridge.cmd_open(sw, part_path)
print(f"  Part reopened: ok={result.get('ok')}")

if not ref_w_mm:
    print("FAILED to measure reference view!")
    sys.exit(1)

print(f"  Reference: {ref_w_mm:.1f}x{ref_h_mm:.1f}mm")

# Step 3: Pre-calculate layouts
print("\n=== Step 3: Pre-calculate layouts ===")
_MARGINS = {"left": 15, "right": 15, "top": 15, "bottom": 10}
_TITLE_RATIO = {"width": 0.30, "height": 0.25}

def calc_layout(pw_mm, ph_mm, ref_w, ref_h):
    ml, mr, mt, mb = _MARGINS["left"], _MARGINS["right"], _MARGINS["top"], _MARGINS["bottom"]
    tb_h = ph_mm * _TITLE_RATIO["height"]
    tb_w = pw_mm * _TITLE_RATIO["width"]
    
    safe_x1, safe_y1 = ml, mb + tb_h
    safe_x2, safe_y2 = pw_mm - max(mr, tb_w), ph_mm - mt
    mid_x = (safe_x1 + safe_x2) / 2
    mid_y = (safe_y1 + safe_y2) / 2
    
    vw, vh = ref_w, ref_h
    iso_w, iso_h = ref_w * 1.25, ref_h * 1.26
    
    tl_cx, tl_cy = (safe_x1 + mid_x) / 2, (mid_y + safe_y2) / 2
    bl_cx, bl_cy = (safe_x1 + mid_x) / 2, (safe_y1 + mid_y) / 2
    tr_cx, tr_cy = (mid_x + safe_x2) / 2, (mid_y + safe_y2) / 2
    br_cx, br_cy = (mid_x + safe_x2) / 2, (safe_y1 + mid_y) / 2
    
    margin = 10
    front_ok = (tl_cx - vw/2 >= safe_x1 + margin and tl_cx + vw/2 <= safe_x2 - margin and
                tl_cy - vh/2 >= safe_y1 + margin and tl_cy + vh/2 <= safe_y2 - margin)
    iso_ok = (br_cx - iso_w/2 >= safe_x1 + margin and br_cx + iso_w/2 <= safe_x2 - margin and
              br_cy - iso_h/2 >= safe_y1 + margin and br_cy + iso_h/2 <= safe_y2 - margin)
    
    layout = [
        ("*前视", tl_cx, tl_cy, "前视图"),
        ("*上视", bl_cx, bl_cy, "俯视图"),
        ("*右视", tr_cx, tr_cy, "右视图"),
        ("*等轴测", br_cx, br_cy, "等轴测"),
    ]
    
    return layout, iso_w, iso_h, front_ok and iso_ok, (safe_x1, safe_y1, safe_x2, safe_y2)

papers = [("A3", 420, 297), ("A2", 594, 420), ("A1", 841, 594)]
selected = None
for pname, pw, ph in papers:
    layout, iso_w, iso_h, fits, safe = calc_layout(pw, ph, ref_w_mm, ref_h_mm)
    status = "FIT" if fits else "TOO SMALL"
    print(f"  {pname}: ref={ref_w_mm:.0f}x{ref_h_mm:.0f} iso={iso_w:.0f}x{iso_h:.0f} -> {status}")
    if fits and selected is None:
        selected = (pname, pw, ph, layout, safe)

if not selected:
    print("  Force A1")
    pname, pw, ph = "A1", 841, 594
    layout, iso_w, iso_h, _, safe = calc_layout(pw, ph, ref_w_mm, ref_h_mm)
    selected = (pname, pw, ph, layout, safe)

pname, pw_mm, ph_mm, layout, safe = selected
print(f"\n  Selected: {pname}")
for name, cx, cy, label in layout:
    print(f"    {label}: ({cx:.0f},{cy:.0f})mm")

# Step 4: Create drawing
print("\n=== Step 4: Create drawing ===")
tmpl = sw_bridge._find_drawing_template(sw, pname)
doc = sw.NewDocument(tmpl, 3, pw_mm/1000, ph_mm/1000)
time.sleep(2)

for name, cx, cy, label in layout:
    ok = doc.CreateDrawViewFromModelView(part_abs, name, cx/1000, cy/1000, 0)
    time.sleep(0.3)
    print(f"  Created {label}")

# Step 5: Verify
print("\n=== Step 5: Verify ===")
v = doc.GetFirstView
idx = 0
all_ok = True
actual_views = []
while v:
    try:
        bb = v.GetOutline
        w = (bb[2]-bb[0])*1000
        if w > 300:
            print(f"  [{idx}] SHEET: {w:.0f}x{(bb[3]-bb[1])*1000:.0f}mm")
        else:
            x1,y1,x2,y2 = bb
            in_safe = x1*1000>=safe[0] and y1*1000>=safe[1] and x2*1000<=safe[2] and y2*1000<=safe[3]
            if not in_safe: all_ok = False
            print(f"  [{idx}] ({x1*1000:.0f},{y1*1000:.0f})-({x2*1000:.0f},{y2*1000:.0f}) size={w:.0f}x{(y2-y1)*1000:.0f} {'OK' if in_safe else 'FAIL'}")
            actual_views.append(bb)
    except Exception as e:
        print(f"  [{idx}] error: {e}")
    try: v = v.GetNextView
    except: break
    idx += 1

# Spacing
print("\n  Spacing:")
for i in range(len(actual_views)):
    for j in range(i+1, len(actual_views)):
        x1a,y1a,x2a,y2a = actual_views[i]
        x1b,y1b,x2b,y2b = actual_views[j]
        dx = max(0, x1b-x2a, x1a-x2b) * 1000
        dy = max(0, y1b-y2a, y1a-y2b) * 1000
        dist = (dx*dx + dy*dy)**0.5
        s = "OK" if dist >= 10 else "CLOSE"
        print(f"    [{i}]<->[{j}]: {dist:.0f}mm {s}")
        if dist < 10: all_ok = False

print(f"\n=== {'PASS' if all_ok else 'FAIL'} ===")
if all_ok:
    doc.SaveAs(output_path)
    print(f"Saved: {output_path}")
else:
    doc.SaveAs(output_path.replace('.SLDDRW', '_fail.SLDDRW'))
