"""尝试用不同方法创建球体"""
import os, sys, time, pythoncom, win32com.client
sys.path.insert(0, r"C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools")
import swapi
sw = swapi.get_sw()

try:
    while sw.ActiveDoc:
        sw.ActiveDoc.CloseDoc()
        time.sleep(0.3)
except:
    pass

out_dir = r"C:\Users\j1877\Desktop\DSH-Check\SW"
os.makedirs(out_dir, exist_ok=True)

# 方法1：尝试 FeatureSphere
tmpl = swapi.get_part_template(sw)
model = sw.NewDocument(tmpl, 0, 0.1, 0.1)
time.sleep(1)

fm = model.FeatureManager

# 尝试各种可能的球体方法
methods_to_try = [
    ("FeatureSphere", ["FeatureSphere"]),
    ("CreateSphere", ["CreateSphere"]),
]

for name, methods in methods_to_try:
    for method in methods:
        if hasattr(fm, method):
            print(f"Found {name}: {method}")
        elif hasattr(model, method):
            print(f"Found {name} on model: {method}")

# 列出 FeatureManager 的所有方法
print("\nFeatureManager methods containing 'sphere' or 'revolve':")
for attr in dir(fm):
    if 'sphere' in attr.lower() or 'revolve' in attr.lower() or 'loft' in attr.lower():
        print(f"  {attr}")

# 列出 Model 的所有方法
print("\nModel methods containing 'sphere':")
for attr in dir(model):
    if 'sphere' in attr.lower():
        print(f"  {attr}")

print("\nDone!")