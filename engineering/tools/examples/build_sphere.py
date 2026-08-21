# -*- coding: utf-8 -*-
"""
示例：球体（使用 Modeler 创建，不依赖草图旋转）
=================================================
设计需求: 创建 D50mm 球体，球心在原点。

执行: python sw_bridge.py run examples\build_sphere.py
（或在 DSH 里说："用 SolidWorks 建模一个直径 50mm 的球体"）

本示例展示了如何使用 IModeler 创建球体：
1. 获取 Modeler 对象
2. 创建球形曲面
3. 从曲面创建面
4. 从面创建实体
5. 添加到零件

注意：直接调用 CreateSphericalSurface 比 revolve(360) 更可靠，
避免了草图旋转的几何条件限制。
"""
import os

# sw_bridge 的 run 会把包目录加入 sys.path，因此可 import swapi
import swapi

# ---- 1. 新建零件（自动探测模板）----
m = swapi.new_part()
print("STEP1 new part:", m.title)

# ---- 2. 使用 Modeler 创建球体（D50mm，球心在原点）----
try:
    # 方式1: create_sphere 方法（推荐）
    feat = m.create_sphere(cx=0, cy=0, cz=0, radius=25)
    print("STEP2 create_sphere OK")
except Exception as e:
    # 方式2: 手动调用 Modeler API
    print("尝试手动模式...")
    modeler = m.sw.IGetModeler
    surface = modeler.CreateSphericalSurface(0, 0, 0, 25 * 0.001)  # 转米
    empty = __import__('win32com.client', fromlist=['VARIANT']).VARIANT(
        __import__('pythoncom', fromlist=['VT_DISPATCH']).VT_DISPATCH, None)
    face = modeler.CreateFace(surface, empty, empty)
    body = modeler.CreateBodyFromFaces(face, 1)
    feat = m.model.InsertFeatureAddBody(body)
    print("STEP2 manual create_sphere OK")

# ---- 3. 保存 ----
out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "DSH_球体.sldprt")
print("STEP3 save:", m.save(out))
print("DONE - 球体已保存到:", out)
