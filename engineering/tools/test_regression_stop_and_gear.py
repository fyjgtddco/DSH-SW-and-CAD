# -*- coding: utf-8 -*-
"""回归测试：P2 全局停机开关 + P5/M1 齿轮零件定义断言

锁定的现场缺陷（《减速机设计过程回溯报告_第二版_20261106.md》）：

  P2｜子代理不受停止指令控制
    现象：用户下达停止令后，子代理手里的下一条 sw_bridge / mode_gate
          命令仍照常执行，停止后仍有建模进程在跑。
    根因：每个命令入口都不检查"是否已被要求停机"。
    修复：新增 tools/_stop.py（STOP_ALL 文件 + DSH_KILL_SWITCH 环境变量），
          四个命令入口（sw_bridge / mode_gate / physics_bridge / visual_check）
          在解析出命令名之后、任何副作用之前 guard()，命中即 TASK_STOPPED。

  P5/M1｜没有齿的"齿轮"被宣布建模成功
    现象：大齿轮 = 半剖轮廓 revolve + 键槽，小齿轮轴 = 阶梯轴 + φda 圆柱段，
          两者都没有齿形，却在交付说明里写"零件交付完成"。
    根因：完成判据只看"文件存在 + 三道防线凭据 + 看图 PASS"，
          没有"零件定义断言"。
    修复：gear() 增加齿顶圆 + 体积双重回读（_verify_gear_geometry），
          齿圈画成圆柱的假件会被当场拒绝。

本测试全部用临时目录 + 环境变量隔离，【不碰真实状态文件】，可反复运行。
运行：python test_regression_stop_and_gear.py
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
FAILS = []
PASSES = [0]


def check(name, cond, detail=""):
    if cond:
        PASSES[0] += 1
        print("  [OK ] %s" % name)
    else:
        FAILS.append(name)
        print("  [FAIL] %s  %s" % (name, detail))


def section(title):
    print("\n== %s ==" % title)


def run_py(args, env_extra=None):
    env = dict(os.environ)
    env.update(env_extra or {})
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable] + list(args), cwd=HERE,
        capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env)


def main():
    tmp = tempfile.mkdtemp(prefix="dsh_stop_gear_test_")
    os.environ["DSH_STATE_DIR"] = tmp
    os.environ.pop("DSH_KILL_SWITCH", None)
    for m in ("_stop", "_store", "mode_gate", "swapi", "sw_bridge"):
        sys.modules.pop(m, None)
    sys.path.insert(0, HERE)

    # ── A. _stop 模块本体 ────────────────────────────────────────────────
    section("A. _stop 停机开关（文件闸）")
    import _stop
    check("初始未停机", not _stop.is_stopped())
    r = _stop.request_stop("回归测试", "tester")
    check("request_stop 成功", bool(r.get("ok")) and bool(r.get("stopped")))
    check("is_stopped 为真", _stop.is_stopped())
    check("guard 拒绝 room-start",
          (_stop.guard("room-start") or {}).get("gate") == "TASK_STOPPED")
    check("guard 放行 status",
          _stop.guard("status", allow={"status"}) is None)
    r2 = _stop.clear_stop()
    check("clear_stop 成功且删除标记",
          bool(r2.get("ok")) and r2.get("removed") is True)
    check("清除后未停机", not _stop.is_stopped())

    # ── B. 环境变量闸 ───────────────────────────────────────────────────
    section("B. _stop 停机开关（环境变量闸）")
    os.environ["DSH_KILL_SWITCH"] = "1"
    check("env 命中后 is_stopped", _stop.is_stopped())
    check("guard 拒绝 new",
          (_stop.guard("new") or {}).get("gate") == "TASK_STOPPED")
    os.environ.pop("DSH_KILL_SWITCH", None)
    check("取消 env 后未停机", not _stop.is_stopped())

    # ── C. 命令入口端到端 ───────────────────────────────────────────────
    section("C. 四个命令入口端到端")
    p = run_py(["mode_gate.py", "stop-all", "回归测试"])
    check("mode_gate stop-all 落盘",
          '"stopped": true' in p.stdout, "rc=%s out=%s" % (p.returncode, p.stdout[:300]))

    p = run_py(["mode_gate.py", "room-start", "测试房间"])
    check("room-start 被 TASK_STOPPED 拦截",
          '"gate": "TASK_STOPPED"' in p.stdout and p.returncode == 4,
          "rc=%s out=%s" % (p.returncode, p.stdout[:300]))

    p = run_py(["mode_gate.py", "status"])
    check("status 仍可执行（只读放行）", p.returncode == 0,
          "rc=%s out=%s" % (p.returncode, p.stdout[:300]))

    p = run_py(["sw_bridge.py", "check-part", "DSH_x.SLDPRT", "--room", "测试房间"])
    check("sw_bridge check-part 仍可执行（清理类放行）", p.returncode == 0,
          "rc=%s out=%s" % (p.returncode, p.stdout[:300]))

    p = run_py(["workflow_gate.py", "init", "停机测试任务"])
    check("workflow_gate init 被 TASK_STOPPED 拦截",
          '"gate": "TASK_STOPPED"' in p.stdout and p.returncode == 4,
          "rc=%s out=%s" % (p.returncode, p.stdout[:300]))

    p = run_py(["workflow_gate.py", "status"])
    check("workflow_gate status 仍可执行（只读放行）", p.returncode == 0,
          "rc=%s out=%s" % (p.returncode, p.stdout[:300]))

    p = run_py(["sw_bridge.py", "info"])
    check("sw_bridge info 被 TASK_STOPPED 拦截",
          '"gate": "TASK_STOPPED"' in p.stdout and p.returncode == 4,
          "rc=%s out=%s" % (p.returncode, p.stdout[:300]))

    p = run_py(["physics_bridge.py", "demo"])
    check("physics_bridge demo 被 TASK_STOPPED 拦截",
          '"gate": "TASK_STOPPED"' in p.stdout and p.returncode == 4,
          "rc=%s out=%s" % (p.returncode, p.stdout[:300]))

    p = run_py(["visual_check.py", "capture", "--part", "DSH_x.SLDPRT",
                "--out", tmp])
    check("visual_check capture 被 TASK_STOPPED 拦截",
          '"gate": "TASK_STOPPED"' in p.stdout and p.returncode == 4,
          "rc=%s out=%s" % (p.returncode, p.stdout[:300]))

    p = run_py(["mode_gate.py", "resume"])
    check("resume 清除停机", '"ok": true' in p.stdout,
          "rc=%s out=%s" % (p.returncode, p.stdout[:300]))

    p = run_py(["mode_gate.py", "status"])
    check("resume 后 status 正常", p.returncode == 0,
          "rc=%s out=%s" % (p.returncode, p.stdout[:300]))

    # ── D. 齿轮零件定义断言 ─────────────────────────────────────────────
    section("D. 齿轮零件定义断言（M1/P5）")
    try:
        import swapi
        import sw_bridge as sw_bridge_mod
        has_swapi = True
    except Exception as e:
        has_swapi = False
        print("  [SKIP] swapi 不可导入：%r" % (e,))

    if has_swapi:
        import math as _m

        class Dummy:
            def __init__(self, box, vol):
                self._box = box
                self._vol = vol

            def body_box_mm(self):
                return self._box

            def _body_volume_mm3(self):
                return self._vol

        # 真齿圈：体积 = 齿廓解析面积 × 齿宽；齿顶圆 da = m·z+2m = 52
        d = Dummy([-26, 26, -26, 26, 0, 40], 1500.0 * 40)
        r = swapi.SWModel._verify_gear_geometry(d, 2, 24, 40, 0.0, 1500.0)
        check("真齿圈通过（体积/齿顶圆一致）", r.get("ok") is True,
              "r=%r" % (r,))

        # 假件：同样 φ52、同样齿宽，但按圆柱计体积（无齿）
        d2 = Dummy([-26, 26, -26, 26, 0, 40], _m.pi * 26 * 26 * 40)
        r2 = swapi.SWModel._verify_gear_geometry(d2, 2, 24, 40, 0.0, 1500.0)
        check("无齿圆柱被拒绝",
              r2.get("ok") is False and (r2.get("volume_dev_pct") or 0) > 2.0,
              "r=%r" % (r2,))

        # 齿顶圆不对（φ60）
        d3 = Dummy([-30, 30, -30, 30, 0, 40], 1500.0 * 40)
        r3 = swapi.SWModel._verify_gear_geometry(d3, 2, 24, 40, 0.0, 1500.0)
        check("齿顶圆不符被拒绝", r3.get("ok") is False, "r=%r" % (r3,))

        # 无实体
        d4 = Dummy(None, None)
        r4 = swapi.SWModel._verify_gear_geometry(d4, 2, 24, 40, 0.0, 1500.0)
        check("无实体被拒绝", r4.get("ok") is False, "r=%r" % (r4,))

    # ── M6. 斜齿几何参数（helix_gear 离线校验）──────────────────────────
    section("M6. 斜齿 helix_gear 几何参数")
    if has_swapi:
        class _FakeGearM(swapi.SWModel):
            def __init__(self):
                pass

        # 回溯报告的真实参数：m=2, z=24, b=47, β=14.5337°
        _r = _FakeGearM().helix_gear(2, 24, 47, 14.5337)
        _p = _r.get("params") or {}
        check("斜齿参数算出分度圆 d = m·z = 48",
              _p.get("pitch_dia_mm") == 48.0, "d=%r" % (_p.get("pitch_dia_mm"),))
        check("斜齿齿顶圆 da = m·z+2m = 52",
              _p.get("tip_dia_mm") == 52.0, "da=%r" % (_p.get("tip_dia_mm"),))
        # 导程 L = π·d / tan(β)
        _lead_exp = math.pi * 48.0 / math.tan(math.radians(14.5337))
        check("导程 L = π·d/tan(β)",
              abs((_p.get("lead_mm") or 0) - _lead_exp) < 0.5,
              "lead=%r exp=%.2f" % (_p.get("lead_mm"), _lead_exp))
        # 扭转角 = 360·b/L（不是 360·b/(L/z) —— 后者会算成 1666°）
        _twist_exp = 360.0 * 47.0 / _lead_exp
        check("总扭转角 = 360·b/L（不是按齿距算）",
              abs((_p.get("twist_total_deg") or 0) - _twist_exp) < 0.2,
              "twist=%r exp=%.3f" % (_p.get("twist_total_deg"), _twist_exp))
        check("扭转角物理合理（0 < 扭转 < 360°）",
              0 < (_p.get("twist_total_deg") or 0) < 360,
              "twist=%r" % (_p.get("twist_total_deg"),))
        check("扭转约等于 1~3 个齿距（斜齿的典型形态）",
              1.0 <= (_p.get("twist_total_deg") or 0) / (360.0 / 24) <= 3.0,
              "ratio=%.2f" % ((_p.get("twist_total_deg") or 0) / (360.0 / 24),))

        # β=0 必须被拒绝（不能拿直齿冒充斜齿）
        _r0 = _FakeGearM().helix_gear(2, 24, 47, 0.0)
        check("螺旋角=0 被拒绝并提示改用 gear()",
              _r0.get("ok") is False and "gear()" in (_r0.get("error") or ""),
              "err=%r" % ((_r0.get("error") or "")[:80],))

        # β≥45° 必须被拒绝
        _r45 = _FakeGearM().helix_gear(2, 24, 47, 50.0)
        check("螺旋角≥45° 被拒绝", _r45.get("ok") is False)

        # 非法齿数
        _rz = _FakeGearM().helix_gear(2, 2, 47, 15.0)
        check("齿数<3 被拒绝", _rz.get("ok") is False)

    # ── M5. 基准面姿态代码查询（替代硬编码 ZSIGN）────────────────────────
    section("M5. 基准面姿态查询（IRefPlane::Transform）")
    if has_swapi:
        class _FakeRefPlane:
            def __init__(self, arr):
                self.Transform = type("T", (), {"ArrayData": arr})()



        # 说明：plane_axes_world 的实现已改为"选择基准面 → 选择集 →
        #   IFeature.GetSpecificFeature2 → IRefPlane.Transform"（真机实测唯一可用
        #   通路；FeatureByName / FirstFeature 遍历在本机 SW2025 都不可靠）。
        #   因此测试用一个假的 select_plane + 假 SelectionManager 来喂数据。
        _orig_sel_plane = swapi.SWModel.select_plane
        _orig_early = swapi.early

        _ARRS = {
            "Top Plane": [1, 0, 0, 0, 0, -1, 0, 1, 0,
                          0.0, 0.0, 0.0, 1.0, 0, 0, 0],
            "Front Plane": [1, 0, 0, 0, 1, 0, 0, 0, 1,
                            0.0, 0.0, 0.0, 1.0, 0, 0, 0],
            "Right Plane": [0, 0, -1, 0, 1, 0, 1, 0, 0,
                            0.0, 0.0, 0.0, 1.0, 0, 0, 0],
        }

        def _mk_model(name, arr=None):
            class _F:
                def __init__(self):
                    self.Name = name

                @property
                def _ref(self):
                    _n = _STATEFUL.get("current") or name
                    return _FakeRefPlane(_ARRS.get(_n, _ARRS["Top Plane"]))

                def GetSpecificFeature2(self):
                    return self._ref

                def GetSpecificFeature(self):
                    return self._ref

            _the_feat = _F()

            class _SelMgr:
                def GetSelectedObject6(self, i, mark):
                    return _the_feat

            class _M:
                def __init__(self):
                    self.SelectionManager = _SelMgr()

                def ClearSelection2(self, flag):
                    return True

            class _SM(swapi.SWModel):
                def __init__(self, model):
                    self.model = model
                    self.sw = None
                    self.warnings = []

            return _SM(_M())

        _STATEFUL = {"current": None}

        def _fake_select_plane(self, name):
            _STATEFUL["current"] = name
            return True

        def _fake_early(obj, iface_name="IDispatch"):
            return obj

        swapi.SWModel.select_plane = _fake_select_plane
        swapi.early = _fake_early

        # Top Plane：局部 x→世界X, y→世界−Z, 法向(z)→世界+Y
        _top = _mk_model("Top Plane",
                         [1, 0, 0, 0, 0, -1, 0, 1, 0,
                          0.0, 0.0, 0.0, 1.0, 0, 0, 0])
        _a = _top.plane_axes_world("Top Plane")
        check("Top Plane 法向 = 世界 +Y（官方语义）",
              _a.get("normal_world") == [0.0, 1.0, 0.0], "n=%r" % (_a.get("normal_world"),))
        check("Top Plane 草图 y 沿世界 Z 且符号为 −1",
              _a.get("sketch_y_axis_index") == 2 and _a.get("sketch_y_sign") == -1.0,
              "idx=%r sign=%r" % (_a.get("sketch_y_axis_index"), _a.get("sketch_y_sign")))
        check("Top Plane 查询来源标明为 IRefPlane::Transform",
              _a.get("source") == "IRefPlane::Transform")
        _z = _top.sketch_y_for_world_z("Top Plane", 20.0)
        check("世界 Z=20 → Top Plane 草图 y=−20（镜像换算正确）",
              _z.get("sketch_y") == -20.0, "y=%r" % (_z.get("sketch_y"),))

        # Front Plane：单位阵 → 法向 +Z，草图 y 沿世界 Y
        _front = _mk_model("Front Plane",
                           [1, 0, 0, 0, 1, 0, 0, 0, 1,
                            0.0, 0.0, 0.0, 1.0, 0, 0, 0])
        _af = _front.plane_axes_world("Front Plane")
        check("Front Plane 法向 = 世界 +Z（不是 +Y）",
              _af.get("normal_world") == [0.0, 0.0, 1.0],
              "n=%r" % (_af.get("normal_world"),))
        check("Front Plane 草图 y 沿世界 Y",
              _af.get("sketch_y_axis_index") == 1)
        _zf = _front.sketch_y_for_world_z("Front Plane", 20.0)
        check("Front Plane 无法把世界 Z 映射到草图 y（如实报警）",
              _zf.get("sketch_y") is None and bool(_zf.get("warning")))

        # 读不到变换 → 退回经验表且明确标注来源
        class _MBad(swapi.SWModel):
            def __init__(self):
                class _M:
                    def FeatureByName(self, n):
                        return None

                    def FirstFeature(self):
                        return None
                self.model = _M()

        _fb = _MBad().plane_axes_world("Top Plane")
        check("查不到变换时退回经验表并标注来源",
              _fb.get("ok") is True and _fb.get("source") == "fallback_table"
              and bool(_fb.get("note")), "fb=%r" % (_fb.get("source"),))

    # ── E. D4：COM 忙 / 拒绝 的指数退避重试 ────────────────────────────
    section("E. D4 COM 忙退避重试")
    if has_swapi:
        class _FakeComErr(Exception):
            def __init__(self, hr=None, msg=""):
                super().__init__(msg)
                if hr is not None:
                    self.hresult = hr

        # ① 业务错误【不】重试
        _n = [0]

        def _biz():
            _n[0] += 1
            raise _FakeComErr(None, "轮廓无效")

        try:
            swapi.com_retry(_biz)
            check("业务错误应抛出", False, "未抛异常")
        except Exception:
            check("业务错误不重试（只调 1 次）", _n[0] == 1, "calls=%d" % _n[0])

        # ② 忙错误重试后成功
        _n2 = [0]

        def _busy_ok():
            _n2[0] += 1
            if _n2[0] < 3:
                raise _FakeComErr(-2147417846, "Call was rejected by callee")
            return "OK"

        _t0 = time.time()
        _r = swapi.com_retry(_busy_ok)
        check("忙错误重试后成功", _r == "OK" and _n2[0] == 3, "calls=%d" % _n2[0])
        check("退避确实产生了等待", (time.time() - _t0) >= 0.05,
              "elapsed=%.3f" % (time.time() - _t0))

        # ③ 一直忙 → 达到上限后抛出
        _n3 = [0]

        def _always_busy():
            _n3[0] += 1
            raise _FakeComErr(-2147418111, "server busy")

        try:
            swapi.com_retry(_always_busy)
            check("一直忙应最终抛出", False, "未抛异常")
        except Exception as _e3:
            check("一直忙重试到上限（%d 次）" % swapi.COM_RETRY_MAX,
                  _n3[0] == swapi.COM_RETRY_MAX and swapi.is_com_busy_error(_e3),
                  "calls=%d" % _n3[0])

        # ④ 错误码映射正确性（官方 RPC_E_* 值）
        _want = {-2147417846, -2147418111}
        check("RPC_E 错误码集合正确",
              _want.issubset(set(swapi._COM_BUSY_CODES)),
              "codes=%r" % (sorted(swapi._COM_BUSY_CODES),))
        check("中文『调用被拒绝』被识别",
              swapi.is_com_busy_error(Exception("调用被拒绝，请重试")))
        check("普通错误不被误判为忙",
              not swapi.is_com_busy_error(Exception("feature returned None")))

    # ── F. D3：文档身份守卫（ActiveDoc 不再是身份）────────────────────────
    section("F. D3 文档身份守卫")
    if has_swapi:
        class _Doc:
            def __init__(self, path, title, typ=1):
                self._p, self._t, self._ty = path, title, typ

            def GetPathName(self):
                return self._p

            def GetTitle(self):
                return self._t

            def GetType(self):
                return self._ty

        _a = _Doc(r"C:\t\parts\DSH_x.SLDPRT", "DSH_x.SLDPRT")
        _b = _Doc(r"c:\T\parts\dsh_X.sldprt", "dsh_X.sldprt")
        _c = _Doc(r"C:\t\parts\other.SLDPRT", "other.SLDPRT")
        _u1 = _Doc("", "Part1.SLDPRT")
        _u2 = _Doc("", "Part1")

        check("同路径（大小写不敏感）判为同一文档",
              swapi.same_document(_a, _b) is True)
        check("不同路径判为不同文档",
              swapi.same_document(_a, _c) is False)
        check("未保存按标题（忽略扩展名）判同一文档",
              swapi.same_document(_u1, _u2) is True)
        check("None 一律不同",
              swapi.same_document(_a, None) is False)

        # 跨房间误关检测
        class _SW:
            def __init__(self, docs):
                self._docs = docs

            def GetDocuments(self):
                return self._docs

        _base = sw_bridge_mod._snapshot_open_docs(_SW([_a, _c, _u1]))
        check("文档快照含 3 项", _base is not None and len(_base) == 3,
              "base=%r" % (_base,))
        # 已命名的 other.SLDPRT 在 run 后消失 → 违规
        _after = sw_bridge_mod._snapshot_open_docs(_SW([_a, _u1]))
        _v = sw_bridge_mod._cross_room_doc_violation(_base, _after, room="传动")
        check("检出跨房间误关（已命名文档消失）",
              _v is not None and _v.get("count") == 1
              and _v.get("gate") == "CROSS_ROOM_DOC_CLOSED", "v=%r" % (_v,))
        # 只有未命名文档消失 → 不算违规
        _after2 = sw_bridge_mod._snapshot_open_docs(_SW([_a, _c]))
        check("仅未命名文档消失不算违规",
              sw_bridge_mod._cross_room_doc_violation(_base, _after2) is None)
        check("sw=None 时快照返回 None（不误报）",
              sw_bridge_mod._snapshot_open_docs(None) is None)

    # ── G. D2：建模期可见性可配置 ─────────────────────────────────────────
    section("G. D2 无头模式可配置")
    _hp = run_py(["-c", "import swapi;print(swapi.HEADLESS_MODE, swapi.VISUAL_MODE)"],
                 env_extra={"DSH_SW_VISIBLE": "0"})
    check("DSH_SW_VISIBLE=0 → 无头（不显示窗口）",
          _hp.stdout.strip() == "True False", "out=%r" % _hp.stdout.strip())
    _hp2 = run_py(["-c", "import swapi;print(swapi.HEADLESS_MODE, swapi.VISUAL_MODE)"],
                  env_extra={"DSH_SW_VISIBLE": "1"})
    check("DSH_SW_VISIBLE=1 → 保持可视化（默认偏好）",
          _hp2.stdout.strip() == "False True", "out=%r" % _hp2.stdout.strip())
    _hp3 = run_py(["-c", "import swapi;print(swapi.HEADLESS_MODE, swapi.VISUAL_MODE)"],
                  env_extra={"DSH_SW_VISIBLE": ""})
    check("未设置 → 默认可视化",
          _hp3.stdout.strip() == "False True", "out=%r" % _hp3.stdout.strip())

    # ── I. D4 配套：batch_mode（CommandInProgress）异常安全 ──────────────
    section("I. D4 配套 batch_mode")

    class _FakeSwB:
        def __init__(self):
            self.CommandInProgress = False

    if has_swapi:
        _sb = _FakeSwB()
        with swapi.batch_mode(_sb):
            _inside = _sb.CommandInProgress
        check("batch_mode 期间置 True、结束复位 False",
              _inside is True and _sb.CommandInProgress is False,
              "inside=%r after=%r" % (_inside, _sb.CommandInProgress))

        _sb2 = _FakeSwB()
        try:
            with swapi.batch_mode(_sb2):
                raise ValueError("boom")
        except ValueError:
            pass
        check("batch_mode 异常也复位（不留批处理态）",
              _sb2.CommandInProgress is False)

        with swapi.batch_mode(None) as _ok:
            pass
        check("batch_mode(sw=None) 不抛异常", _ok is False)

        # 错误码必须覆盖微软权威定义的两个高频码
        check("覆盖 RPC_E_CALL_REJECTED(0x80010001=-2147418111)",
              -2147418111 in swapi._COM_BUSY_CODES)
        check("覆盖 RPC_E_SERVERCALL_RETRYLATER(0x8001010A=-2147417846)",
              -2147417846 in swapi._COM_BUSY_CODES)

    # ── H. P3：同零件重复建模闸门 ────────────────────────────────────────
    section("H. P3 同零件重复建模闸门（去重）")
    try:
        import part_registry as pr
        _have_pr = True
    except Exception as e:
        _have_pr = False
        print("  [SKIP] part_registry 不可导入：%r" % (e,))

    if _have_pr and has_swapi:
        _old_reg = os.environ.get("DSH_PART_REGISTRY")
        os.environ["DSH_PART_REGISTRY"] = os.path.join(tmp, "part_registry.json")
        try:
            _part = os.path.join(tmp, "DSH_大齿轮.SLDPRT")
            with open(_part, "wb") as _f:
                _f.write(b"x" * 1024)
            _add = pr.add_part(
                {"name": "大齿轮", "room": "输出轴与大齿轮", "file": _part,
                 "dims": "200.413x200.413x40", "material": "45#"},
                reason="回归测试登记", skip_verification=True)
            check("台账登记成功", bool(_add.get("ok")),
                  "err=%r" % (_add.get("error"),))

            _sc = os.path.join(tmp, "build_gear.py")
            with open(_sc, "w", encoding="utf-8") as _f:
                _f.write("import swapi\nm = swapi.new_part()\n")
                _f.write("m.save(r'" + _part + "')\n") 

            _g = sw_bridge_mod._check_part_reuse_before_run(
                _sc, room="输出轴与大齿轮", extra_args=[])
            check("已建好且尺寸一致 → 判定复用（跳过重建）",
                  bool(_g) and _g.get("skip_modeling") is True
                  and _g.get("gate") == "PART_ALREADY_BUILT",
                  "g=%r" % ({k: _g.get(k) for k in
                             ("skip_modeling", "gate", "verdict", "unknown")}
                            if _g else _g,))

            # 未登记的新零件 → 不拦截
            _sc2 = os.path.join(tmp, "build_new.py")
            with open(_sc2, "w", encoding="utf-8") as _f:
                _f.write("import swapi\nm = swapi.new_part()\n")
                _f.write("m.save(r'" + os.path.join(tmp, "DSH_全新件.SLDPRT") + "')\n")
            check("未登记的新零件不拦截",
                  sw_bridge_mod._check_part_reuse_before_run(_sc2, room="x") is None)
        finally:
            if _old_reg is None:
                os.environ.pop("DSH_PART_REGISTRY", None)
            else:
                os.environ["DSH_PART_REGISTRY"] = _old_reg

    print("\n==============================")
    print("PASS=%d  FAIL=%d" % (PASSES[0], len(FAILS)))
    if FAILS:
        print("失败项：")
        for f in FAILS:
            print("  - %s" % f)
    print("==============================")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
