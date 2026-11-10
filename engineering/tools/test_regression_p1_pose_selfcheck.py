#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""回归：P1 核心缺口 —— pr.json 朝向声明 + 三段自检（第二次修复验收）

对应《减速机设计过程回溯报告》第二版 7（P1/P5）与
《bug 修复核查报告》三「未修复的 7 项」中的：
  · 第 5 项：无三段自检 / visual_check 提前
  · 第 6 项：无 pr.json 声明

判据（全部机器可判，与报告 15.1/15.2 对齐）：
  A. pr.json
     A1 write_pr_record 落盘且字段齐全
     A2 幂等：重复写不产生第二份/不损坏
     A3 read_pr_record 缺文件时 ok=False 且 exists=False（绝不默认通过）
     A4 validate_pr_record 抓得出非法枚举
     A5 source=inferred 被如实标注
  B. 总装朝向断言（J2/J4/J5）
     B1 声明==契约 且 零旋转        -> PASS
     B2 声明!=契约 且 零旋转        -> J4 FAIL（★零旋转装配报警）
     B3 声明!=契约 且有旋转         -> J2 FAIL
     B4 声明==契约 但被转过         -> J5 WARNING（strict 时 FAIL）
     B5 没有 pr.json                -> NO_PR_RECORD FAIL
     B6 inferred 声明 -> strict 自动降级（WARNING 不算 FAIL）
  C. 三段自检
     C1 bbox 一致 -> PASS
     C2 bbox 超差 -> FAIL 且给矫正指令
     C3 体积偏差 >2% -> FAIL（抓"无齿圆柱代齿轮"）
     C4 特征缺失 -> FAIL
     C5 全部跳过 -> INCOMPLETE，绝不 PASS
     C6 容差超硬上限被夹紧
     C7 无齿齿轮场景：bbox 完全一致、仅体积不符 -> 必须 FAIL
        （这是 P5 的回归锚点：证明 bbox 抓不到、体积能抓到）

运行: python test_regression_p1_pose_selfcheck.py
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import part_pose as pp
import part_verify as pv
try:
    import visual_check as vc
except Exception:
    vc = None

PASS = 0
FAIL = 0
FAILS = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [OK]   %s" % name)
    else:
        FAIL += 1
        FAILS.append("%s  %s" % (name, detail))
        print("  [FAIL] %s  %s" % (name, detail))


def section(t):
    print("\n" + "=" * 72)
    print(t)
    print("=" * 72)


def main():
    tmp = tempfile.mkdtemp(prefix="p1_pose_")
    try:
        part = os.path.join(tmp, "DSH_小齿轮.SLDPRT")
        # 造一个假零件文件（本模块只关心"声明与断言"，不碰 COM）
        with open(part, "wb") as f:
            f.write(b"fake sldprt")

        section("A. pr.json 写入 / 读取 / 校验")

        # --- A3 先验：没有声明时必须 ok=False ---
        r0 = pp.read_pr_record(part)
        check("A3 缺文件 -> ok=False", r0["ok"] is False, r0)
        check("A3 缺文件 -> exists=False", r0["exists"] is False, r0)

        # --- A1 写入 ---
        bbox = [-26.8, 26.8, -26.8, 26.8, 0.0, 40.0]
        w = pp.write_pr_record(part, up_axis="Z", up_sign=1, main_axis="Z",
                               base_plane="Top Plane", sketch_y_sign=-1,
                               bbox_mm=bbox)
        check("A1 写入 ok", w["ok"] is True, w)
        check("A1 文件存在", os.path.isfile(w["path"]), w["path"])
        rr = pp.read_pr_record(part)
        check("A1 读回 ok", rr["ok"] is True, rr)
        rec = rr["record"]
        check("A1 up_axis=Z", rec.get("up_axis") == "Z", rec)
        check("A1 up_sign=1", rec.get("up_sign") == 1, rec)
        check("A1 main_axis=Z", rec.get("main_axis") == "Z", rec)
        check("A1 base_plane 记录", rec.get("base_plane") == "Top Plane", rec)
        check("A1 sketch_y_sign=-1", rec.get("sketch_y_sign") == -1, rec)
        check("A1 schema 正确", rec.get("schema") == pp.SCHEMA, rec)

        # --- A2 幂等 ---
        before = os.path.getmtime(w["path"])
        w2 = pp.write_pr_record(part, up_axis="Z", up_sign=1, main_axis="Z")
        check("A2 重复写仍 ok", w2["ok"] is True, w2)
        check("A2 只有一份声明文件",
              len([x for x in os.listdir(tmp) if x.endswith(".pr.json")]) == 1,
              os.listdir(tmp))
        check("A2 内容可再读", pp.read_pr_record(part)["ok"] is True)

        # --- A4 非法枚举 ---
        bad = dict(rec, up_axis="W")
        v = pp.validate_pr_record(bad)
        check("A4 up_axis=W 被拒", v["ok"] is False and v["errors"], v)
        bad2 = dict(rec, up_sign=7)
        v2 = pp.validate_pr_record(bad2)
        check("A4 up_sign=7 被拒", v2["ok"] is False and v2["errors"], v2)
        v3 = pp.validate_pr_record(dict(rec))
        check("A4 合法记录通过", v3["ok"] is True, v3)

        # --- A5 inferred 标注 ---
        p2 = os.path.join(tmp, "DSH_套筒.SLDPRT")
        w3 = pp.write_pr_record(p2)   # 什么都不给 -> 推断
        check("A5 未声明 -> inferred", w3["record"]["source"] == "inferred", w3)
        v4 = pp.validate_pr_record(w3["record"])
        check("A5 inferred 产生 warning",
              any("inferred" in x for x in v4["warnings"]), v4)

        section("B. 总装朝向断言（J2/J4/J5）")

        IDENT = [1, 0, 0, 0, 1, 0, 0, 0, 1]
        # 绕 X 轴转 90 度（非单位阵）
        ROT90X = [1, 0, 0,
                  0, 0, -1,
                  0, 1, 0]

        # B1 声明==契约 且 零旋转 -> PASS
        r = pp.check_assembly_pose(part_path=part, rotation=IDENT,
                                   world_up="Z", world_up_sign=1)
        check("B1 对齐+零旋转 -> PASS", r["verdict"] == "PASS", r)
        check("B1 identity_like=True", r["identity_like"] is True, r)

        # B2 声明!=契约 且 零旋转 -> J4 FAIL
        p3 = os.path.join(tmp, "DSH_躺着的梁.SLDPRT")
        pp.write_pr_record(p3, up_axis="Y", up_sign=1, main_axis="X")
        r = pp.check_assembly_pose(part_path=p3, rotation=IDENT,
                                   world_up="Z", world_up_sign=1)
        check("B2 零旋转装配 -> FAIL", r["verdict"] == "FAIL", r)
        check("B2 code=J4_ZERO_ROTATION_UNPLACED",
              r["code"] == "J4_ZERO_ROTATION_UNPLACED", r)
        check("B2 给出重建动作", bool(r["action"]), r)

        # B3 声明!=契约 且有旋转 -> J2 FAIL
        r = pp.check_assembly_pose(part_path=p3, rotation=ROT90X,
                                   world_up="Z", world_up_sign=1)
        check("B3 声明冲突 -> FAIL", r["verdict"] == "FAIL", r)
        check("B3 code=J2_POSE_CONFLICT", r["code"] == "J2_POSE_CONFLICT", r)

        # B4 声明==契约 但被转过 -> J5 WARNING
        r = pp.check_assembly_pose(part_path=part, rotation=ROT90X,
                                   world_up="Z", world_up_sign=1, strict=False)
        check("B4 被转过 -> WARNING", r["verdict"] == "WARNING", r)
        check("B4 code=J5_ROTATED_BUT_DECLARED_ALIGNED",
              r["code"] == "J5_ROTATED_BUT_DECLARED_ALIGNED", r)
        r_s = pp.check_assembly_pose(part_path=part, rotation=ROT90X,
                                     world_up="Z", world_up_sign=1, strict=True)
        check("B4 strict=True -> FAIL", r_s["verdict"] == "FAIL", r_s)

        # B5 没有 pr.json
        p4 = os.path.join(tmp, "DSH_无声明.SLDPRT")
        with open(p4, "wb") as f:
            f.write(b"x")
        r = pp.check_assembly_pose(part_path=p4, rotation=IDENT)
        check("B5 缺声明 -> FAIL", r["verdict"] == "FAIL", r)
        check("B5 code=NO_PR_RECORD", r["code"] == "NO_PR_RECORD", r)

        # B6 inferred 声明自动降级
        p5 = os.path.join(tmp, "DSH_推断件.SLDPRT")
        pp.write_pr_record(p5)          # inferred, up_axis 默认 Z
        r = pp.check_assembly_pose(part_path=p5, rotation=ROT90X,
                                   world_up="Z", world_up_sign=1)
        check("B6 inferred -> strict 自动 False", r.get("strict") is False, r)
        check("B6 inferred 的 J5 不升级为 FAIL",
              r["verdict"] == "WARNING", r)

        # B7 16 数 ArrayData 的解析（按【本机实测】布局：
        #    d[0:3]=X轴 d[3:6]=Y轴 d[6:9]=Z轴 d[9:12]=平移 d[12]=缩放）
        tf16 = [1, 0, 0,
                0, 1, 0,
                0, 0, 1,
                5, 6, 7,       # 平移 5,6,7 —— 不应污染旋转判定
                1, 0, 0, 0]
        r = pp.check_assembly_pose(part_path=part, rotation=tf16)
        check("B7 16 数变换 -> identity_like=True", r["identity_like"] is True, r)

        # B8 房间级断言
        rc = pp.assert_room_contract("结构件", parts=[part],
                                     contract={"world_up": "Z",
                                               "world_up_sign": 1,
                                               "rooms": {"结构件": {"up_axis": "Z",
                                                                  "up_sign": 1}}})
        check("B8 房间级 -> ok", rc["ok"] is True, rc)
        rc2 = pp.assert_room_contract("结构件", parts=[p3])
        check("B8 有躺着的件 -> 不通过", rc2["ok"] is False, rc2)
        rc3 = pp.assert_room_contract("结构件", parts=[])
        check("B8 不给零件 -> 不通过（不得视为通过）",
              rc3["ok"] is False, rc3)



        # B9 ★实测布局回归：本机 SW2025 实测的三个默认基准面 ArrayData
        #   上视基准面 [1,0,0, 0,0,-1, 0,1,0, 0,0,0, 1,0,0,0]
        #   -> 若按"三行三轴"读，X=[1,0,0], Y=[0,0,-1], Z=[0,1,0]；
        #      法向 = Z = +Y，与实测"上视面法向 +Y、草图 (x,y)->世界 (x,0,-y)"吻合。
        #   旧版（平移插在 3/7/11）会把 row0 读成 [1,0,0] 但对 row1 读到
        #   [0,1,0]（其实是 d[4:7]），整块错位 —— 这条用例就是防它回归。
        top_plane_array = [1, 0, 0, 0, 0, -1, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0]
        r = pp.check_assembly_pose(part_path=part, rotation=top_plane_array)
        axes = (r.get("axis_vectors_world") or {})
        check("B9 16 数按三行三轴解析：X 轴 = [1,0,0]",
              axes.get("x") == [1.0, 0.0, 0.0], axes)
        check("B9 16 数按三行三轴解析：Y 轴 = [0,0,-1]",
              axes.get("y") == [0.0, 0.0, -1.0], axes)
        check("B9 法向（Z 轴）= [0,1,0] -> +Y（上视面实测）",
              axes.get("z") == [0.0, 1.0, 0.0], axes)
        check("B9 上视面不是单位阵（识得出来转过了）",
              r.get("identity_like") is False, r)

        section("C. 三段自检")

        BBOX = [0.0, 100.0, 0.0, 50.0, 0.0, 10.0]

        # C1 bbox 一致
        s = pv.compare_bbox(BBOX, BBOX)
        check("C1 bbox 一致 -> ok", s["ok"] is True, s)
        check("C1 max_delta=0", s["max_abs_delta_mm"] == 0.0, s)

        # C2 bbox 超差
        s = pv.compare_bbox([0.0, 100.5, 0.0, 50.0, 0.0, 10.0], BBOX)
        check("C2 超差 0.5mm -> FAIL", s["ok"] is False, s)
        check("C2 给矫正指令", bool(s.get("correction")), s)

        # C3 体积偏差
        s = pv.compare_volume(40000, 50000)
        check("C3 体积 -20% -> FAIL", s["ok"] is False, s)
        check("C3 偏差算出 -20", abs(s["deviation_pct"] + 20.0) < 1e-6, s)
        s = pv.compare_volume(49500, 50000)
        check("C3 体积 -1% -> PASS", s["ok"] is True, s)

        # C4 特征缺失
        feats = [{"name": "Sketch1", "type": "Sketch"},
                 {"name": "Boss-Extrude1", "type": "Extrusion"},
                 {"name": "Cut-Extrude1", "type": "Cut"}]
        s = pv.compare_features(feats, expect_features=["Extrusion", "Cut",
                                                        "Fillet"])
        check("C4 缺 Fillet -> FAIL", s["ok"] is False, s)
        check("C4 missing 里含 Fillet",
              any("Fillet" in str(x.get("feature")) for x in s["missing"]), s)
        s2 = pv.compare_features(feats, expect_features=["Extrusion*", "Cut*"])
        check("C4 通配 Extrusion* 命中", s2["ok"] is True, s2)
        sc = pv.compare_features(feats, expect_count=2)
        check("C4 用户特征数=2（排除 Sketch）", sc["user_count"] == 2, sc)
        sc2 = pv.compare_features(feats, expect_count=3)
        check("C4 计数不符 -> FAIL", sc2["ok"] is False, sc2)

        # C5 全跳过 -> INCOMPLETE
        s = pv.three_stage_check()
        check("C5 全跳过 -> 不 ok", s["ok"] is False, s)
        check("C5 verdict=INCOMPLETE", s["verdict"] == "INCOMPLETE", s)

        # C6 容差夹紧
        s = pv.compare_volume(1000, 2000, tol_pct=999)
        check("C6 体积容差夹紧到 <=10",
              s["tolerance_pct"] <= pv.VOLUME_TOL_HARD_CAP_PCT, s)
        b = pv.compare_bbox(BBOX, [0, 100.5, 0, 50, 0, 10], tol_mm=999)
        check("C6 bbox 容差夹紧到 <=1.0",
              b["tolerance_mm"] <= pv.BBOX_TOL_HARD_CAP_MM, b)

        # ★C7 P5 回归锚点：无齿圆柱 vs 真齿轮 —— bbox 一模一样，只有体积不同
        #   真齿轮 z=24, m=2, b=40：分度圆 d=48，体积≈ 齿圈体积
        #   这里用"同外径圆盘"模拟无齿件：包围盒相同，体积偏大。
        da = 52.0
        disk_bbox = [-da / 2, da / 2, -da / 2, da / 2, 0.0, 40.0]
        gear_bbox = list(disk_bbox)          # 齿顶圆就是最大外径 -> bbox 相同
        disk_vol = 3.141592653589793 * (da / 2) ** 2 * 40
        gear_vol = disk_vol * 0.82           # 真齿体积约为圆盘的 82%
        res = pv.three_stage_check(
            bbox_actual=disk_bbox, bbox_expect=gear_bbox,
            volume_actual_mm3=disk_vol, volume_expect_mm3=gear_vol,
            features=[{"name": "Boss-Extrude1", "type": "Extrusion"}],
            expect_features=["Extrusion"],
            expect_feature_count=1)
        check("C7 无齿件 bbox 仍然一致（bbox 抓不到）",
              res["stages"]["bbox"]["ok"] is True, res["stages"]["bbox"])
        check("C7 ★体积把它抓出来 -> 总判 FAIL",
              res["ok"] is False and res["verdict"] == "FAIL", res)
        check("C7 failed 里含 volume", "volume" in res["failed"], res)

        # ★C8 实测驱动回归：SW2025 上"切除"的 GetTypeName2 返回 "ICE"
        #   若用排除式计数，ICE 会被当成用户特征、真 Cut 又不出现 → 口径两处都错。
        #   这里断言白名单口径下 ICE 被正确计入、基础设施不被计入。
        _f8 = [
            {"name": "Material <1>", "type": "MaterialFolder"},
            {"name": "Front Plane", "type": "RefPlane"},
            {"name": "Origin", "type": "OriginProfileFeature"},
            {"name": "Sketch1", "type": "Sketch"},
            {"name": "Boss-Extrude1", "type": "Extrusion"},
            {"name": "Cut-Extrude1", "type": "ICE"},
            {"name": "Fillet1", "type": "Fillet"},
        ]
        _c8 = pv.classify_features(_f8)
        check("C8 ICE 切除被计入用户特征",
              any(r.get("type") == "ICE" for r in _c8["user"]), _c8["user"])
        check("C8 用户特征数 = 3（Extrusion+ICE+Fillet）",
              _c8["user_count"] == 3, _c8["user_count"])
        check("C8 基础设施（RefPlane/MaterialFolder/Origin）不计入",
              len(_c8["infra"]) == 3, _c8["infra"])
        check("C8 草图单独归类", _c8["sketch_count"] == 1, _c8["sketch"])

        # 未知类型不得被静默并进用户特征
        _c8b = pv.classify_features([{"name": "X", "type": "SomeNewType"}])
        check("C8 未知类型不冒充用户特征", _c8b["user_count"] == 0, _c8b)
        check("C8 未知类型可见（不静默丢弃）",
              _c8b["unknown_count"] == 1, _c8b)



        # ★C9 真机 bug 回归：GetTypeName2 是【属性】不是方法
        #   真机实测（SW2025）：f.GetTypeName2 -> str；f.GetTypeName2() 抛
        #   TypeError: 'str' object is not callable。
        #   旧代码写死调用形式 -> 异常被 bare except 吞 -> 所有 type=None
        #   -> 用户特征数恒 0 -> 而"期望 0"恰好判通过（又一次假通过）。
        class _FakeFeatProp(object):
            """模拟真机：GetTypeName2 是属性（不可调用）。"""
            def __init__(self, nm, tn):
                self.Name = nm
                self.GetTypeName2 = tn          # 属性，不是方法

        class _FakeFeatMethod(object):
            """模拟老版本：GetTypeName2 是方法。"""
            def __init__(self, nm, tn):
                self.Name = nm
                self._tn = tn
            def GetTypeName2(self):
                return self._tn

        class _FakeModel(object):
            def __init__(self, feats):
                self._f = feats
            @property
            def FirstFeature(self):
                return self._f[0] if self._f else None

        f_prop = _FakeFeatProp("Boss-Extrude1", "Extrusion")
        check("C9 属性形态取得到类型",
              pv._prop_or_call(f_prop, "GetTypeName2") == "Extrusion")
        f_meth = _FakeFeatMethod("Cut-Extrude1", "ICE")
        check("C9 方法形态也取得到",
              pv._prop_or_call(f_meth, "GetTypeName2") == "ICE")
        check("C9 成员缺失返回 None",
              pv._prop_or_call(f_prop, "NoSuchMember") is None)

        # 端到端：属性形态的特征树 -> 能正确分类出 1 个用户特征
        feats_prop = [
            {"name": "收藏", "type": "FavoriteFolder"},
            {"name": "实体", "type": "SolidBodyFolder"},
            {"name": "草图1", "type": "ProfileFeature"},
            {"name": "Boss-Extrude1", "type": "Extrusion"},
        ]
        _c9 = pv.classify_features(feats_prop)
        check("C9 属性形态下用户特征数=1（不是 0）",
              _c9["user_count"] == 1, _c9)
        check("C9 ProfileFeature 归草图",
              _c9["sketch_count"] == 1, _c9)
        check("C9 基础设施归 infra",
              len(_c9["infra"]) == 2, _c9)

        section("D. visual_check expect 扩展")
        if vc is not None:
            _extra_volume_expect_tests()
        else:
            check("D0 visual_check 可导入", False, "import failed")

        section("汇总")
        print("  PASS=%d  FAIL=%d" % (PASS, FAIL))
        if FAILS:
            print("\n失败明细：")
            for x in FAILS:
                print("  - " + x)
        return 0 if FAIL == 0 else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)



def _extra_volume_expect_tests():
    """D 段：visual_check 的 expect 现在必须能表达体积/特征数（P5 回归）。"""
    section("D. visual_check expect 扩展（体积 / 特征数）")
    exp, err = vc.normalize_expect({
        "bbox_mm": [0, 10, 0, 10, 0, 10],
        "volume_mm3": 1234.5,
        "volume_tolerance_pct": 1.5,
        "feature_count": 4,
    })
    check("D1 解析 ok", exp is not None and err is None, err)
    check("D1 volume_mm3 生效", exp.get("volume_mm3") == 1234.5, exp)
    check("D1 volume_tolerance_pct 生效",
          exp.get("volume_tolerance_pct") == 1.5, exp)
    check("D1 feature_count 生效", exp.get("feature_count") == 4, exp)

    exp2, err2 = vc.normalize_expect({"volume_mm3": "abc"})
    check("D2 非法体积被拒", exp2 is None and bool(err2), err2)
    exp3, err3 = vc.normalize_expect({"feature_count": "x"})
    check("D3 非法特征数被拒", exp3 is None and bool(err3), err3)
    # 老用法不受影响
    exp4, err4 = vc.normalize_expect({"bbox_mm": [0, 1, 0, 1, 0, 1]})
    check("D4 老用法（仅 bbox）仍可用", exp4 is not None, err4)
    check("D4 未给体积时不硬塞默认", exp4.get("volume_mm3") is None, exp4)

if __name__ == "__main__":
    sys.exit(main())
