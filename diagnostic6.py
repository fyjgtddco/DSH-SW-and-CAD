"""Test: verify SW ignores scale parameter and auto-scales."""
import sys, time
sys.path.insert(0, r'C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools')
import sw_bridge
import win32com.client, pythoncom
pythoncom.CoInitialize()
sw = sw_bridge.get_sw()
part_path = r'C:\Users\j1877\Desktop\DSH-Check\DSH_正方体长方体组合体.SLDPRT'

for scale in [1.0, 0.5, 0.33, 0.25, 0.2]:
    tmpl = sw_bridge._find_drawing_template(sw, 'A2')
    doc = sw.NewDocument(tmpl, 3, 0.594, 0.420)
    time.sleep(2)
    
    name = '*前视'
    ok = doc.CreateDrawViewFromModelView(part_path, name, 0.115, 0.332, scale)
    time.sleep(0.5)
    
    v = doc.GetFirstView
    idx = 0
    while v and idx < 5:
        try:
            bb = v.GetOutline
            w = (bb[2]-bb[0])*1000
            if w > 300:
                idx += 1
                v = v.GetNextView
                continue
            print(f'  scale={scale:.2f} -> view size={w:.0f}x{(bb[3]-bb[1])*1000:.0f}mm (expected {int(100/scale)}x{int(100/scale)}mm)')
        except: pass
        try: v = v.GetNextView
        except: break
        idx += 1
