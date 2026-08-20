"""检查零件实体和特征详情"""
import sys, time
sys.path.insert(0, r"C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools")
import swapi
sw = swapi.get_sw()

d = sw.ActiveDoc
print(f"Doc: {d.GetTitle}")

# 检查实体
print("\nBodies:")
try:
    body = d.FirstBody
    while body:
        print(f"  Body: {body.Name}")
        try:
            body = body.GetNextBody
        except:
            break
except Exception as e:
    print(f"  Error: {e}")

# 检查特征详情
print("\nFeatures detail:")
feat = d.FirstFeature
while feat:
    try:
        fname = feat.Name
        ftype = feat.GetTypeName
        print(f"  {fname} ({ftype})")
        
        # 检查是否是草图特征
        if ftype == "ProfileFeature":
            try:
                sk = feat.GetSpecificFeature2
                if sk:
                    print(f"    Sketch: {sk.Name}")
            except:
                pass
        
        # 检查是否是拉伸特征
        if ftype == "SolidBody" or "Extrude" in ftype:
            try:
                feat_feat = feat.GetSpecificFeature2
                if feat_feat:
                    print(f"    Depth: {feat_feat.EndDepth * 1000:.2f} mm")
            except:
                pass
    except:
        pass
    try:
        feat = feat.GetNextFeature
    except:
        feat = None

# 截图
d.ShowNamedView2("*Isometric", 0)
d.ViewZoomtofit2()
time.sleep(1)
m = swapi.from_active(sw)
m.screenshot(r"C:\Users\j1877\Desktop\DSH-Check\SW\pyramid_check.png")
print("\nScreenshot saved")