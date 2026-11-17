#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回归：parts-board 子进程必须拿到【宿主真实端口】（2026-11-16 图二现场）。

── 现场 ────────────────────────────────────────────────────────────────
子代理能正常读监控器（quarantined=false、SW 占用者可见），但右下角
「零件」面板报：
    读取失败: 连不上 DSH 宿主桥 (http://127.0.0.1:19387)
而 GUI 实际在 54023。

── 根因 ────────────────────────────────────────────────────────────────
parts-board 路由用 spawnSync 起 mode2_flow.py，env 来自插件里的 pyEnv()。
pyEnv() 只做 `Object.assign({}, process.env)` —— 完全赌宿主进程自己有
DSH_WEB_URL。而宿主进程里这个变量缺失时，sw_monitor_client 一路落到
固定端口 19387 → 连不上。

我上一轮只修了 Python 侧的解析顺序（读 DSH_WEB_URL），没修
「谁来喂这个变量」—— 同一件事的另一半。当时的 e2e 测试手动设了 env，
所以又没抓到。

本文件锁死这个契约：
  ① 插件源码里 pyEnv() 必须显式注入宿主端口（不能只靠 process.env）；
  ② 宿主端口来源必须是 ctx.webServer.port；
  ③ 注入的变量名必须是 sw_monitor_client 认的那几个。

用法：python test_regression_partsboard_port.py
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _find_plugin_index():
    """定位 dsh-engineering-ui/lib/index.js。

    两种部署形态（2026-11-16 实测）：
      ① 仓库源码：<repo>/engineering/tools/ 的上一级有 plugins/
      ② 安装副本：<DSH_HOME>/.agent-presets/engineering/tools/，
         插件在 <DSH_HOME>/profiles/*/node_modules/dsh-engineering-ui/lib/
    原先只写 ①，安装副本上跑直接 FileNotFoundError —— 测试在仓库绿、
    在副本崩，等于没覆盖真实部署。
    """
    cands = [
        os.path.join(HERE, "..", "plugins", "dsh-engineering-ui", "lib", "index.js"),
    ]
    try:
        home = os.environ.get("DSH_HOME") or os.path.join(
            os.path.expanduser("~"), ".dsh")
        prof = os.path.join(home, "profiles")
        if os.path.isdir(prof):
            for name in sorted(os.listdir(prof)):
                cands.append(os.path.join(
                    prof, name, "node_modules", "dsh-engineering-ui", "lib", "index.js"))
    except Exception:
        pass
    for c in cands:
        if os.path.isfile(c):
            return c
    return cands[0]


PLUGIN = _find_plugin_index()
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
    print("parts-board 子进程端口注入")
    print("=" * 68)

    check("找到插件 index.js", os.path.isfile(PLUGIN), PLUGIN)
    if not os.path.isfile(PLUGIN):
        return _summary()
    print("  插件: " + PLUGIN)
    src = open(PLUGIN, encoding="utf-8").read()

    print("\n=== ① pyEnv 必须显式注入宿主端口 ===")
    m = re.search(r"function pyEnv\(\)\s*\{(.*?)\n  \}", src, re.S)
    check("插件里有 pyEnv()", bool(m), "未找到 function pyEnv()")
    body = m.group(1) if m else ""
    check("pyEnv 里调用了 hostOrigin/hostPort（不是只继承 process.env）",
          "hostOrigin" in body or "hostPort" in body,
          body[:300])
    check("pyEnv 里写入 DSH_WEB_URL", "DSH_WEB_URL" in body, body[:300])
    check("pyEnv 里写入 DSH_WEB_PORT", "DSH_WEB_PORT" in body, body[:300])

    print("\n=== ② 端口来源必须是 ctx.webServer.port ===")
    check("存在 hostPort() helper", "function hostPort()" in src)
    check("hostPort 读 ctx.webServer.port",
          re.search(r"webServer\s*&&\s*ctx\.webServer\.port", src) is not None
          or "ctx.webServer.port" in src,
          "未找到 webServer.port")
    hm = re.search(r"function hostPort\(\)\s*\{(.*?)\n  \}", src, re.S)
    check("hostPort 做了数值校验（非法端口返回 null）",
          bool(hm) and ("Number.isFinite" in hm.group(1) or "isFinite" in hm.group(1)),
          (hm.group(1)[:200] if hm else ""))

    print("\n=== ③ 不许把固定端口写死进 pyEnv ===")
    check("pyEnv 里没有硬编码 19387", "19387" not in body, body[:300])

    print("\n=== ④ parts-board 路由确实用 pyEnv ===")
    i = src.find("path: PREFIX + '/parts-board'")
    check("找到 parts-board 路由", i > 0)
    seg = src[i:i + 3000] if i > 0 else ""
    check("parts-board 的 spawnSync 传了 env: pyEnv()",
          "pyEnv()" in seg, seg[:300])

    return _summary()


def _summary():
    print("\n" + "=" * 68)
    print("  PASS=%d  FAIL=%d" % (PASS, FAIL))
    if FAILS:
        for f in FAILS:
            print("  - " + f)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
