# -*- coding: utf-8 -*-
import sys, os, time
sys.path.insert(0, r'C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools')
import sw_bridge
import win32com.client, pythoncom

pythoncom.CoInitialize()
sw = sw_bridge.get_sw()

print('=== Tracing cmd_drawing ===')
part_path = r'C:\Users\j1877\Desktop\DSH-Check\DSH_正方体长方体组合体.SLDPRT'
part_abs = os.path.abspath(part_path)
output_path = r'C:\Users\j1877\Desktop\DSH-Check\DSH_正方体长方体组合体_v2.SLDDRW'

# Step 1: cmd_open
t0 = time.time()
print('Step 1: cmd_open...')
result = sw_bridge.cmd_open(sw, part_path)
print(f'  Result: {result}')
print(f'  Time: {time.time()-t0:.1f}s')

# Step 2: find template
t0 = time.time()
print('Step 2: _find_drawing_template...')
tmpl = sw_bridge._find_drawing_template(sw)
print(f'  Template: {tmpl}')
print(f'  Time: {time.time()-t0:.1f}s')

# Step 3: get bbox (inline, since _get_part_bbox_mm is nested)
print('Step 3: Getting part bbox...')
t0 = time.time()
default_w, default_h = 100, 100
path_abs = part_abs
bbox = None
for i in range(sw.GetDocumentCount):
    try:
        d = sw.GetDocument(i)
        if d is None:
            continue
        try:
            doc_path = d.GetPathName
            if doc_path and os.path.abspath(doc_path) == path_abs:
                print(f'  Found already-open doc: {d.GetTitle}')
                try:
                    bb = d.IGetBoundingBox
                    if bb and len(bb) == 6:
                        w_mm = (bb[3] - bb[0]) * 1000
                        h_mm = (bb[4] - bb[1]) * 1000
                        bbox = {'w': max(w_mm, 50), 'h': max(h_mm, 50)}
                        print(f'  Bbox from IGetBoundingBox: {bbox}')
                        break
                except Exception as e:
                    print(f'  IGetBoundingBox error: {e}')
                try:
                    bb = d.GetBoundingBox
                    if bb:
                        w_mm = (bb[3] - bb[0]) * 1000
                        h_mm = (bb[4] - bb[1]) * 1000
                        bbox = {'w': max(w_mm, 50), 'h': max(h_mm, 50)}
                        print(f'  Bbox from GetBoundingBox: {bbox}')
                        break
                except Exception as e:
                    print(f'  GetBoundingBox error: {e}')
        except Exception:
            pass
    except Exception as e:
        pass

if bbox is None:
    print('  Using default bbox')
    bbox = {'w': default_w, 'h': default_h}
print(f'  Time: {time.time()-t0:.1f}s')

# Step 4: compute layout
print('Step 4: _compute_layout...')
t0 = time.time()
# Need to call through cmd_drawing's globals
layout = sw_bridge.cmd_drawing.__globals__['_compute_layout'](0.420, 0.297, bbox)
print(f'  Layout:')
for v in layout:
    print(f'    {v[3]}: ({v[1]:.3f}, {v[2]:.3f})')
print(f'  Time: {time.time()-t0:.1f}s')

# Step 5: NewDocument
print('Step 5: NewDocument...')
t0 = time.time()
doc = sw.NewDocument(tmpl, 3, 0.420, 0.297)
print(f'  Doc: {doc.GetTitle if doc else None}')
print(f'  Time: {time.time()-t0:.1f}s')

# Step 6: Create views
print('Step 6: Creating views...')
t0 = time.time()
for view_name, cx, cy, label in layout:
    t1 = time.time()
    try:
        ok = doc.CreateDrawViewFromModelView(part_abs, view_name, cx, cy, 0.5)
        print(f'  {label}: ok={ok} (took {time.time()-t1:.1f}s)')
    except Exception as e:
        print(f'  {label}: ERROR {e} (took {time.time()-t1:.1f}s)')
print(f'  Total time: {time.time()-t0:.1f}s')

# Step 7: Check bboxes
print('Step 7: Checking view bboxes...')
t0 = time.time()
v = doc.GetFirstView
idx = 0
while v and idx < 10:
    try:
        bb = v.GetOutline
        if bb and len(bb) == 4:
            w = (bb[2]-bb[0])*1000
            h = (bb[3]-bb[1])*1000
            if w > 200 or h > 140:  # skip sheet outline
                pass  # this is the sheet
            else:
                print(f'  View {idx}: bbox ({bb[0]*1000:.0f},{bb[1]*1000:.0f})-({bb[2]*1000:.0f},{bb[3]*1000:.0f})mm')
        else:
            print(f'  View {idx}: no outline')
    except Exception as e:
        print(f'  View {idx}: error {e}')
    try:
        v = v.GetNextView
    except:
        break
    idx += 1
print(f'  Time: {time.time()-t0:.1f}s')

# Save
print('Step 8: Saving...')
t0 = time.time()
rc = doc.SaveAs3(output_path, 0, 2)
print(f'  RC: {rc}, exists: {os.path.exists(output_path)}')
print(f'  Time: {time.time()-t0:.1f}s')
print('=== Done ===')
