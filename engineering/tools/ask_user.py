#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ask_user.py —— 子代理专用的阻塞式提问 CLI
==========================================
用途：让【子代理】直接在【右侧子代理面板】向用户提问并阻塞等待答案。

背景（已核实 DSH 源码）：
  · dsh-user-questions 的 ask() 会校验 agents.roots().includes(agent)，
    子代理不在 roots 中 → 必然抛 DELEGATED_CALLER，无法自己调 ask_user_question。
  · host-apiproxy 的 provider 用 sessionId = request.agent?.id 决定帧路由，
    应答校验 matchesQuestions() 也要求 sessionId 与 pending 一致。
  · 因此提问必须由"能通过校验的 root"承载 —— 由本插件 host 半代为发起。

本 CLI 的工作方式（阻塞等待，不结束回合）：
  1) POST /dsh-engineering-ui/ask-child，带上本子代理的 sessionId + 问题/选项
  2) host 代表本子代理发起原生 ask()，返回 pendingId
  3) 本脚本【原地轮询】等待用户在子代理面板作答
  4) 拿到答案后打印 JSON 并退出 —— 调用方从 stdout 读到答案，继续原逻辑

这与已废弃的 followup 方案的本质区别：
  followup 把答案变成"下一轮新消息"，子代理会当成新任务；
  本方案答案回到【发起提问的那次调用栈】，子代理直接从工具返回值继续。

用法：
  python ask_user.py --room <房间名> --child <子代理sessionId> \
      --question "以下是零件参数，要用吗？" \
      --option "确认，开始建模" --option "需要修改"

  或从 JSON 文件读取问题（推荐，避免命令行转义问题）：
  python ask_user.py --room <房间名> --child <sessionId> --spec questions.json

输出（stdout，单行 JSON）：
  {"ok": true, "answers": [{"id":"q1","selected":["确认，开始建模"],"custom":""}]}
  {"ok": false, "error": "..."}
"""
import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DEFAULT_HOST = os.environ.get("DSH_HOST", "http://127.0.0.1:3080")
PREFIX = "/dsh-engineering-ui"


def _post(url, payload, timeout=30):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"content-type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", "replace")
    return json.loads(body)


def _get(url, timeout=30):
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", "replace")
    return json.loads(body)


def main():
    ap = argparse.ArgumentParser(description="子代理阻塞式提问")
    ap.add_argument("--child", required=True, help="本子代理的 sessionId")
    ap.add_argument("--room", default="", help="房间名（用于进度上报，可选）")
    ap.add_argument("--question", default="", help="问题文本")
    ap.add_argument("--option", action="append", default=[], help="选项（可多次）")
    ap.add_argument("--spec", default="", help="问题 JSON 文件路径（含 questions 数组）")
    ap.add_argument("--header", default="", help="问题分组标题")
    ap.add_argument("--multi", action="store_true", help="是否多选")
    ap.add_argument("--host", default=DEFAULT_HOST, help="DSH host 地址")
    ap.add_argument("--timeout", type=int, default=1800, help="等待用户作答的最长秒数")
    ap.add_argument("--interval", type=float, default=2.0, help="轮询间隔秒数")
    args = ap.parse_args()

    # 组装问题
    if args.spec:
        try:
            with open(args.spec, "r", encoding="utf-8") as f:
                spec = json.load(f)
            questions = spec.get("questions") if isinstance(spec, dict) else spec
            if not isinstance(questions, list) or not questions:
                raise ValueError("spec 中没有有效的 questions 数组")
        except Exception as e:
            print(json.dumps({"ok": False, "error": "读取 spec 失败: %s" % e}, ensure_ascii=False))
            return 1
    else:
        if not args.question:
            print(json.dumps({"ok": False, "error": "缺少 --question 或 --spec"}, ensure_ascii=False))
            return 1
        questions = [{
            "id": "q1",
            "question": args.question,
            "header": args.header or "确认",
            "options": [{"label": o} for o in (args.option or ["确认"])],
            "multiSelect": bool(args.multi),
        }]

    # 1) 登记提问
    try:
        reg = _post(args.host + PREFIX + "/ask-child", {
            "childSessionId": args.child,
            "questions": questions,
        })
    except Exception as e:
        print(json.dumps({"ok": False, "error": "无法连接 host: %s" % e}, ensure_ascii=False))
        return 1

    if not reg.get("ok") or not reg.get("pendingId"):
        print(json.dumps({"ok": False, "error": reg.get("error", "登记失败")}, ensure_ascii=False))
        return 1

    pending_id = reg["pendingId"]
    room = args.room

    # 2) 阻塞轮询，直到用户作答（期间保持心跳，避免被判 stale）
    deadline = time.time() + int(args.timeout)
    base = args.host + PREFIX
    while time.time() < deadline:
        try:
            pending = _get("%s/pending-child?childSessionId=%s" % (base, args.child))
            still = [p for p in (pending.get("pending") or []) if p.get("pendingId") == pending_id]
            if not still:
                # 该 pending 已消失 => 已作答（答案由 host 侧保存）
                ans = _get("%s/result-child?pendingId=%s" % (base, pending_id))
                print(json.dumps(ans, ensure_ascii=False))
                return 0
        except Exception:
            pass
        time.sleep(args.interval)

    print(json.dumps({"ok": False, "error": "等待用户作答超时（%d 秒）" % args.timeout},
                     ensure_ascii=False))
    return 1


if __name__ == "__main__":
    sys.exit(main())
