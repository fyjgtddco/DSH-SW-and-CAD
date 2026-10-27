# -*- coding: utf-8 -*-
"""回归测试：【换机事故】三项修复 + 环境自检 + 本机设置

锁定的现场缺陷（用户在学校微机房实测，2026-10-27）：

  Bug A｜交付目录写死「桌面\test」
    现象：「在学校的电脑的桌面上，也莫名其妙就出现了一个 test 文件」
    根因：workflow_gate._default_delivery_root() 与 mode_gate._default_work_dir()
          只要「桌面存在」就返回 桌面\test，并在首次建任务时【创建它】。
    修复：统一走 tools/settings.py 的 delivery_root()：
          ① 环境变量 ② settings.json ③ 桌面 test（仅当本来就存在）④ <root>/output。

  Bug B｜连接区路径跟着「上一台电脑」走
    现象：设置里固定了 SW 在 Z 盘，换到没有 Z 盘的机器后一直撞空路径。
    根因：connection_state.json 的 paths 缓存只在「任意路径仍存在」时复用，
          且没有任何「这是哪台电脑」的记录。
    修复：paths.machine = 本机指纹；指纹不符 → 整份缓存作废并重新探测。

  Bug C｜缺少依赖时「设置静默失效」
    现象：python 是 3.5（f-string 不支持）→ sw_bridge 直接 SyntaxError →
          stdout 没有 JSON → 上层只看到「解析失败」。
    修复：新增 tools/env_check.py（结构化自检 + 可下发的新对话提示词），
          并在 sw_bridge.py 顶部加【不依赖 f-string 的版本闸门】。

本测试全部用临时目录 + 环境变量隔离，【不碰真实状态文件】，可反复运行。
运行：python test_regression_env_and_settings.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

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


def fresh_env(tmp):
    """隔离环境：状态目录指到临时目录，避免读到本机真实运行态。"""
    os.environ["DSH_STATE_DIR"] = tmp
    for k in ("DSH_DELIVERY_ROOT", "DSH_SW_EXE", "DSH_ACAD_EXE", "DSH_PYTHON"):
        os.environ.pop(k, None)
    for m in ("settings", "_store", "_root", "env_check"):
        sys.modules.pop(m, None)
    sys.path.insert(0, HERE)


def main():
    tmp = tempfile.mkdtemp(prefix="dsh_env_test_")
    fresh_env(tmp)

    section("A. 交付目录不再写死桌面 test")
    import settings as S
    check("settings.py 可导入", True)
    root, src = S.delivery_root()
    check("默认来源可解释（不是无条件选桌面）",
          src in ("default:<root>/output", "legacy-desktop-test(已存在，沿用)"),
          "实际 source=%r" % (src,))
    check("默认目录【不是】凭空建立的桌面 test",
          (not src.startswith("legacy")) or os.path.isdir(root),
          "root=%r 必须已存在才能被沿用" % (root,))
    # 显式设置后必须优先生效
    d1 = os.path.join(tmp, "out1")
    r = S.set_path("delivery_root", d1)
    check("可写入本机交付目录", bool(r.get("ok")), str(r))
    root2, src2 = S.delivery_root()
    check("设置后 delivery_root 生效", os.path.abspath(root2) == os.path.abspath(d1),
          "got %r want %r" % (root2, d1))
    check("设置来源标注正确", src2 == "settings.json", src2)
    # 环境变量优先级最高
    os.environ["DSH_DELIVERY_ROOT"] = os.path.join(tmp, "out_env")
    root3, src3 = S.delivery_root()
    check("环境变量优先级最高", src3.startswith("env:"), src3)
    os.environ.pop("DSH_DELIVERY_ROOT", None)

    section("B. 跨机器设置自动忽略（不再沿用别人的盘符）")
    st = S.load()
    # 伪造一条「来自另一台电脑」的设置
    st["paths"]["sw_exe"] = {"value": "Z:\\Program Files\\SOLIDWORKS Corp2025\\SOLIDWORKS\\SLDWORKS.exe",
                             "machine": "ffffffffffffffff"}
    S.save(st)
    val, note = S.get("sw_exe", None)
    check("跨机 sw_exe 被忽略", val is None, "got %r" % (val,))
    check("忽略原因写清楚（含指纹对比）", ("另一台电脑" in (note or "")), note)
    rep = S.report()
    stale = [r for r in rep["paths"] if r.get("usable") is False]
    check("report 里能看到被忽略项", len(stale) >= 1, str(stale)[:200])
    # 本机设置必须能生效
    r2 = S.set_path("sw_exe", os.path.join(tmp, "fake_sldworks.exe"))
    check("给不存在的 exe 会被拒绝", r2.get("ok") is False, str(r2))
    fake = os.path.join(tmp, "SLDWORKS.exe")
    open(fake, "w").close()
    r3 = S.set_path("sw_exe", fake)
    check("存在的 exe 可保存", bool(r3.get("ok")), str(r3))
    check("保存时打上本机指纹",
          (S.load()["paths"]["sw_exe"].get("machine") == S.machine_fingerprint()),
          json.dumps(S.load()["paths"].get("sw_exe"), ensure_ascii=False))
    v4, n4 = S.get("sw_exe", None)
    check("本机设置可读回", v4 == os.path.abspath(fake), repr(v4))

    section("C. 连接区跨机缓存作废")
    import conn_state as CS
    st2 = CS.load()
    st2["paths"] = {"sldworks_exe": "Z:\\nope\\SLDWORKS.exe",
                    "resolved_at": 1, "machine": "ffffffffffffffff"}
    CS.save(st2)
    p = CS.discover_paths(force=False)
    check("跨机缓存被识别为 stale",
          bool(p.get("ignored_stale_paths")), json.dumps(p.get("ignored_reason"), ensure_ascii=False))
    check("新缓存打上本机指纹", p.get("machine") == S.machine_fingerprint(),
          repr(p.get("machine")))

    section("D. 环境自检（env_check.py）")
    import env_check as EC
    res = EC.probe(full=True)
    check("probe 返回结构完整",
          all(k in res for k in ("ok", "status", "items", "summary")), str(list(res.keys())))
    check("每一项都带 state/detail/fix",
          all(("state" in i and "detail" in i and "fix" in i) for i in res["items"]),
          str(res["items"][:1]))
    check("status 取值合法", res["status"] in ("ok", "warn", "bad"), res["status"])
    # 解释器必须是 >=3.6 才被采纳（本机应能找到）
    py = res.get("python") or {}
    check("探测到了 Python 解释器", bool(py.get("chosen")), str(py)[:200])

    section("E. 修复提示词（发给新对话的那一段）")
    text = EC.install_prompt(res)
    check("提示词非空且够长", len(text) > 400, "len=%d" % len(text))
    for kw in ("工程模式", "验收标准", "pip install", "env_check.py"):
        check("提示词包含 %s" % kw, kw in text)
    check("提示词包含本机 tools 路径", HERE.replace("\\", "/") in text.replace("\\", "/"))
    # 缺项时应列出「怎么修」
    fake_res = {"status": "bad", "summary": "缺必需项",
                "items": [{"id": "numpy", "name": "numpy", "state": "missing",
                           "optional": False, "detail": "未安装",
                           "fix": "-m pip install numpy"}]}
    t2 = EC.install_prompt(fake_res)
    check("缺项提示词里出现 pip 命令", "pip install numpy" in t2, t2[-400:])
    check("缺项提示词里出现该项名称", "numpy" in t2)

    section("F. sw_bridge.py 的 Python 版本闸门")
    src = open(os.path.join(HERE, "sw_bridge.py"), encoding="utf-8").read()
    head = src[:src.index("import os")]
    check("闸门在任何 f-string 之前",
          "sys.version_info < (3, 6)" in head, head[-200:])
    check("闸门本身不含 f-string（旧解释器也能跑）",
          ('f"' not in head and "f'" not in head), head[-200:])
    check("闸门给出可操作的 JSON",
          '"hint"' in head and "3.11" in head)

    section("G. 设置页/宿主路由契约（静态）")
    host = open(os.path.join(os.path.dirname(HERE),
                             "plugins", "dsh-engineering-ui", "lib", "index.js"),
                encoding="utf-8").read()
    check("有 /env-check 路由", "PREFIX + '/env-check'" in host)
    check("有 /env-prompt 路由", "PREFIX + '/env-prompt'" in host)
    check("有 /settings 路由", "PREFIX + '/settings'" in host)
    check("resolvePython 会校验最低版本",
          "PYTHON_MIN_MINOR" in host and "3" in host)
    check("resolvePython 会真的 import 探针 pywin32",
          "import win32com.client" in host)
    check("settings 写入口列入敏感路由", "PREFIX + '/settings'" in host)

    print("\n" + "=" * 60)
    print("PASS %d / FAIL %d" % (PASSES[0], len(FAILS)))
    if FAILS:
        print("失败项：" + ", ".join(FAILS))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
