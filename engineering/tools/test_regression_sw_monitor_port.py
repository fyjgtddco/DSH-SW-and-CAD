#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回归：宿主桥端口【不许写死】（2026-11-12 真机接入失败的直接原因）。

── 现场 ────────────────────────────────────────────────────────────────
会话 630e2e4d：监控器装好、目录齐全、attach 却报
  MONITOR_BRIDGE_UNREACHABLE: 连不上 DSH 宿主桥（127.0.0.1:19387）
而宿主实际在 57496。DSH 的 GUI 端口【每次启动都变】，DSH_WEB_PORT 在
真实会话里根本没被注入。

原有的 e2e 测试【抓不到】这个缺陷，因为它自己把 DSH_WEB_PORT 设成假端口，
恰好绕开了"真实环境不注入"这个前提 —— 替身替得太好反而屏蔽了真 bug。

本文件专门锁死这件事：
  ① 解析顺序必须是 DSH_WEB_URL → ALT → DSH_WEB_PORT → 固定端口；
  ② 没有 DSH_WEB_URL 时【不许】静默假装成功（要能看出是回落）；
  ③ 源码里不许再出现写死的 127.0.0.1:<固定端口> 用于桥调用。

用法：python test_regression_sw_monitor_port.py
"""
import importlib
import inspect
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PASS = 0
FAIL = 0
FAILS = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] " + name)
    else:
        FAIL += 1
        FAILS.append(name)
        print("  [FAIL] " + name + ("  -> " + str(detail) if detail else ""))


def main():
    print("=" * 68)
    print("宿主桥端口解析（不许写死）")
    print("=" * 68)

    envs = ("DSH_SW_MONITOR_HOST", "DSH_WEB_URL", "DSH_WEB_URL_ALT",
            "DSH_WEB_PORT")
    saved = {k: os.environ.get(k) for k in envs}
    try:
        import sw_monitor_client as mc

        def reload_with(**kw):
            for k in envs:
                os.environ.pop(k, None)
            for k, v in kw.items():
                os.environ[k] = v
            importlib.reload(mc)
            return mc._origin()

        print("\n=== ① 优先级 ===")
        o, src = reload_with(DSH_WEB_URL="http://127.0.0.1:57496")
        check("DSH_WEB_URL 生效", o == "http://127.0.0.1:57496", (o, src))
        check("source 标明来自 DSH_WEB_URL", "DSH_WEB_URL" in src, src)

        o, src = reload_with(DSH_WEB_URL="http://127.0.0.1:57496",
                             DSH_WEB_PORT="19387")
        check("DSH_WEB_URL 优先于 DSH_WEB_PORT",
              o == "http://127.0.0.1:57496", (o, src))

        o, src = reload_with(DSH_WEB_URL_ALT="http://127.0.0.1:61234")
        check("DSH_WEB_URL_ALT 可用", o == "http://127.0.0.1:61234", (o, src))

        o, src = reload_with(DSH_WEB_PORT="50505")
        check("只有 DSH_WEB_PORT 时用它", o == "http://127.0.0.1:50505", (o, src))

        o, src = reload_with(DSH_SW_MONITOR_HOST="http://127.0.0.1:40000")
        check("DSH_SW_MONITOR_HOST 最高优先",
              o == "http://127.0.0.1:40000", (o, src))

        print("\n=== ② 回落必须可识别（不许假装成功）===")
        o, src = reload_with()
        check("无任何环境变量时标记 fallback", src == "fallback", (o, src))

        print("\n=== ③ 源码不许写死端口做桥调用 ===")
        src_code = inspect.getsource(mc)
        # 允许出现在注释/常量里；禁止的是 f-string 拼出 127.0.0.1:<固定端口>
        bad = []
        for line in src_code.split("\n"):
            st = line.strip()
            if st.startswith("#"):
                continue
            if "127.0.0.1:19387" in st or "127.0.0.1:3080" in st:
                # FALLBACK_PORTS 常量那一行是允许的（它就是兜底值）
                if "FALLBACK_PORTS" in st:
                    continue
                bad.append(st)
        check("没有写死的桥地址", not bad, bad[:3])

        print("\n=== ④ status 必须暴露 bridge_source ===")
        reload_with(DSH_WEB_URL="http://127.0.0.1:57496")
        st = mc.status_summary()
        check("status 带 bridge", "bridge" in st, sorted(st)[:8])
        check("status 带 bridge_source（排障用）",
              "bridge_source" in st, sorted(st)[:8])
        check("bridge 指向真实端口",
              "57496" in str(st.get("bridge")), st.get("bridge"))
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        importlib.reload(sys.modules[__name__].__dict__.get(
            "sw_monitor_client", sw_monitor_client)) if False else None

    print("\n" + "=" * 68)
    print("  PASS=%d  FAIL=%d" % (PASS, FAIL))
    if FAILS:
        for f in FAILS:
            print("  - " + f)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
