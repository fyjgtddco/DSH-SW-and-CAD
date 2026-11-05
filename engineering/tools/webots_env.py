# -*- coding: utf-8 -*-
r"""webots_env.py — 工程模式【Webots 仿真可用性自检】（流程里的"能不能用"判据）

═══════════════════════════════════════════════════════════════════════════
【为什么需要它】
Webots 那条链默认是**关的**（preset 里 mcp-webots 行 disabled），要同时满足
六个环境条件才会真装配：

    DSH_WEBOTS_ENABLE=1 / DSH_WEBOTS_PYTHON / DSH_WEBOTS_INPUT_ROOT
    DSH_STATE_DIR / DSH_ENGINEERING_ROOT / WEBOTS_HOME

缺哪一项，表现出来都是一模一样的"工具不见了 / 一调就 CALIBRATION_REQUIRED"。
没有这个自检，AI 和用户都只能靠猜；更糟的是可能把"没配好"说成"仿真通过"。

本模块只做**只读判断**：不启动 Webots、不启动 MCP、不写任何文件、不碰 SW。

【与其它模块的关系】
  · 身份/授权：lib/webots.js 的 resolveWebotsContext（Node 半，逐请求）
  · 证据读取：webots_mcp.host（Python 半，只读）
  · 本模块：**配置面**的体检 —— 两者都不替代

用法：
    python webots_env.py            # 人类可读
    python webots_env.py --json     # 机器可读（AI 直接读 ok/blocking 字段）
"""
from __future__ import annotations

import json
import os
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# 缺任一项 → 整条链不会装配（与 preset-engineering.patch.yml 的 disabled 表达式一致）
REQUIRED = [
    ("DSH_WEBOTS_ENABLE", "唯一开关，必须精确等于 '1'"),
    ("DSH_WEBOTS_PYTHON", "装好 webots_mcp/requirements.txt 的解释器绝对路径"),
    ("DSH_WEBOTS_INPUT_ROOT", "已存在的只读模型输入根（绝对路径）"),
    ("DSH_STATE_DIR", "已存在的绝对状态目录；Node 与 Python 必须解析到同一处"),
    ("DSH_ENGINEERING_ROOT", "工程模式根的 engineering 目录（cwd 为其 tools）"),
    ("WEBOTS_HOME", "Webots 安装根（例如 D:\\Webots）"),
]
OPTIONAL = [("DSH_WEBOTS_RUNTIME_CONFIG", "可选的已批准运行配置 JSON（绝对路径）")]


def _check_name(name, hint, path_like):
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return {"name": name, "ok": False, "value": None, "hint": hint,
                "reason": "未设置"}
    if name == "DSH_WEBOTS_ENABLE":
        return {"name": name, "ok": raw == "1", "value": raw, "hint": hint,
                "reason": None if raw == "1" else "必须为 '1'（当前 %r）" % raw}
    if path_like:
        ab = os.path.isabs(raw)
        exists = os.path.exists(raw)
        ok = ab and exists
        if ok:
            reason = None
        elif not ab:
            reason = "必须是绝对路径"
        else:
            reason = "路径不存在"
        return {"name": name, "ok": ok, "value": raw, "hint": hint, "reason": reason}
    return {"name": name, "ok": True, "value": raw, "hint": hint, "reason": None}


def probe():
    checks = []
    for name, hint in REQUIRED:
        # DSH_WEBOTS_ENABLE 不是路径；其余五个都要是存在的绝对路径
        checks.append(_check_name(name, hint, name != "DSH_WEBOTS_ENABLE"))
    optional = [_check_name(n, h, True) for n, h in OPTIONAL]
    blocking = [c for c in checks if not c["ok"]]
    enabled = not blocking
    return {
        "ok": True,
        "sim_available": enabled,
        # ⚠️ 可用 != 就绪：就算配齐，也只是"能装配"。
        #    真 READY 还需要真实控制器握手 + 机器人身份（见 integration-contract）。
        "ready": False,
        "ready_reason": "配置齐备只代表可装配；READY 需真实握手与身份确认，本版未实现",
        "checks": checks,
        "optional": optional,
        "blocking": [c["name"] for c in blocking],
        "next_action": ("可直接用 mcp__webots__* 工具" if enabled
                        else "先补 " + ", ".join(c["name"] for c in blocking)),
        "notes": [
            "本自检不启动 Webots / MCP，不写任何文件，不申请 SW 锁。",
            "缺项时正确行为是报告'仿真不可用'，不是伪造结果。",
        ],
    }


def main(argv):
    report = probe()
    if "--json" in argv:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("Webots 仿真可用性自检")
        print("=" * 46)
        for c in report["checks"]:
            mark = "OK " if c["ok"] else "缺 "
            print("[%s] %-26s %s" % (mark, c["name"], c["value"] or "-"))
            if c["reason"]:
                print("       -> %s（%s）" % (c["reason"], c["hint"]))
        for c in report["optional"]:
            print("[可选] %-24s %s" % (c["name"], c["value"] or "-"))
        print("-" * 46)
        print("可装配：%s" % ("是" if report["sim_available"] else "否"))
        print("就绪  ：否（%s）" % report["ready_reason"])
        print("下一步：%s" % report["next_action"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
