"""Test: try different anchor points to avoid auto-alignment."""
import sys, time
sys.path.insert(0, r'C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools')
import sw_bridge
import win32com.client, pythoncom
pythoncom.CoInitialize()
sw = sw_bridge.get_sw()
part_path = r'C:\Users\j1877\Desktop\DSH-Check\DSH_正方体长方体组合体.SLDPRT'

def test_coords(label, coords_2, safe_x1, safe_y1, safe_x2, safe_y2):
    tmpl = sw_bridge._find_drawing_template(sw, 'A2')
    doc = sw.NewDocument(tmpl, 3, 0.594, 0.420)
    time.sleep(2)

    names = ['*前视', '*上视', '*右视', '*等轴测']
    for name, (cx, cy) in zip(names, coords_2):
        ok = doc.CreateDrawViewFromModelView(part_path, name, cx, cy, 0.5)
        time.sleep(0.3)

    v = doc.GetFirstView
    idx = 0
    results = []
    while v and idx < 10:
        try:
            bb = v.GetOutline
            w_m = bb[2] - bb[0]
            if w_m > 0.3:
                results.append(('SHEET', True))
            else:
                x1,y1,x2,y2 = bb
                in_safe = x1>=safe_x1 and y1>=safe_y1 and x2<=safe_x2 and y2<=safe_y2
                results.append((f'({x1*1000:.0f},{y1*1000:.0f})', in_safe))
        except Exception:
            pass
        try:
            v = v.GetNextView
        except Exception:
            break
        idx += 1

    all_ok = all(r[1] for r in results if r[0] != 'SHEET')
    print(f'  {label}: {"ALL OK" if all_ok else "FAIL"}')
    for nm, r in zip(names+['SHEET'], results):
        status = "OK" if r[1] else "FAIL"
        print(f'    {nm}: {r[0]} -> {status}')
    return all_ok

print('=== A2 Tests ===')
# Test 1: Algorithm center coords
test_coords('algo_center', [
    (0.115, 0.332), (0.115, 0.188), (0.316, 0.332), (0.316, 0.188)
], 0.015, 0.115, 0.416, 0.405)

# Test 2: Push higher (increase y)
test_coords('push_higher', [
    (0.115, 0.35), (0.115, 0.22), (0.316, 0.35), (0.316, 0.22)
], 0.015, 0.115, 0.416, 0.405)

# Test 3: Even higher
test_coords('very_high', [
    (0.115, 0.37), (0.115, 0.26), (0.316, 0.37), (0.316, 0.26)
], 0.015, 0.115, 0.416, 0.405)

# Test 4: Very high (close to top)
test_coords('very_very_high', [
    (0.115, 0.39), (0.115, 0.30), (0.316, 0.39), (0.316, 0.30)
], 0.015, 0.115, 0.416, 0.405)

print()
print('=== A3 Tests ===')
def test_a3(label, coords):
    tmpl = sw_bridge._find_drawing_template(sw, 'A3')
    doc = sw.NewDocument(tmpl, 3, 0.420, 0.297)
    time.sleep(2)
    names = ['*前视', '*上视', '*右视', '*等轴测']
    for name, (cx, cy) in zip(names, coords):
        ok = doc.CreateDrawViewFromModelView(part_path, name, cx, cy, 0.5)
        time.sleep(0.3)

    safe_x1,safe_y1 = 0.015, 0.084
    safe_x2,safe_y2 = 0.294, 0.282

    v = doc.GetFirstView
    idx = 0
    while v and idx < 10:
        try:
            bb = v.GetOutline
            w_m = bb[2]-bb[0]
            if w_m > 0.3:
                pass
            else:
                x1,y1,x2,y2 = bb
                in_safe = x1>=safe_x1 and y1>=safe_y1 and x2<=safe_x2 and y2<=safe_y2
                st = "OK" if in_safe else "FAIL"
                print(f'    {idx}: ({x1*1000:.0f},{y1*1000:.0f}) -> {st}')
        except Exception:
            pass
        try:
            v = v.GetNextView
        except Exception:
            break
        idx += 1

test_a3('algo_A3', [
    (0.085, 0.233), (0.085, 0.134), (0.224, 0.233), (0.224, 0.134)
])
test_a3('push_high_A3', [
    (0.085, 0.25), (0.085, 0.17), (0.224, 0.25), (0.224, 0.17)
])
test_a3('very_high_A3', [
    (0.085, 0.27), (0.085, 0.20), (0.224, 0.27), (0.224, 0.20)
])
