"""重放 trace 元数据及可选监控审计。不会执行原工具。"""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys

from page_contract import FIDELITY


def replay(rows, audit, budget, oracle=False):
    runs, recent, duplicate, observed, missing = {}, {}, set(), Counter(), []
    checkpoints, windows = {}, []
    def build(key):
        if all(key):
            if key in duplicate:
                observed["duplicate_build_candidates"] += 1
            duplicate.add(key)
    for row in rows:
        event, session = row["event"], row.get("session")
        if event == "tool.start":
            runs[row["id"]] = row
        elif event == "tool.result" and row.get("status") == "returned" and row["id"] in runs:
            begin = runs.pop(row["id"])
            tool = row["tool"]
            # Generic shell invocations may write; count only explicitly read-only tool names.
            key = (session, tool, begin["args_sha256"], row["result_sha256"])
            if key in recent and re.search(r"^(read|status|massprops|list|inspect)(_|$)", tool):
                observed["refetch_candidates"] += 1
            recent[key] = row["at"]
        elif event == "build.verified":
            key = (row.get("room"), row.get("part"), row.get("spec_sha256"), row.get("model_sha256"))
            build(key)
        elif event in ("compaction.prepared", "compaction.committed"):
            if event == "compaction.committed":
                checkpoints[session] = dict(row)
                windows.append(checkpoints[session])
            else:
                observed["prepared_windows"] += 1
            used = row.get("bytes")
            if type(used) is int and used > budget:
                observed["over_budget"] += 1
            required = row.get("minimum", {})
            levels = row.get("coverage", {})
            for page, floor in required.items():
                if levels.get(page) not in FIDELITY or floor not in FIDELITY or FIDELITY.index(levels[page]) > FIDELITY.index(floor):
                    observed["bootstrap_loss" if event == "compaction.committed" else "prepared_below_minimum"] += 1
                    missing.append(page)
        elif event == "page.use" and session in checkpoints:
            check = checkpoints[session]
            check.setdefault("_used", set()).add(row["page"])
        elif event == "reset.committed" and row.get("dirty_pages"):
            observed["flush_miss"] += 1
    for row in audit:
        if row["op"] == "com-complete" and row["payload"].get("state") == "saved":
            p = row["payload"]
            scope = p["scope"]
            build((scope.get("room"), scope.get("part"), p.get("spec_sha256"), p.get("model_sha256")))
        if row["op"] == "recover" and row["payload"].get("unacknowledged"):
            observed["flush_miss"] += 1
    result = {"counts": dict(observed), "inflight_tool_starts": len(runs),
              "bootstrap_missing": sorted(set(missing)), "audit_events": len(audit),
              "limitations": "工具哈希只能识别重复候选。没有 page.use 或 build.verified 的项目不能证明状态使用或重复建模。"}
    if oracle:
        supported = [row for row in windows if "_used" in row]
        result["oracle"] = ({"status": "PARTIAL", "observed_windows": len(supported),
                             "removable_page_residencies": sum(len(set(r.get("coverage", {})) -
                                 set(r.get("minimum", {})) - r["_used"]) for r in supported),
                             "required_page_misses": sum(len(r["_used"] - set(r.get("coverage", {})))
                                                         for r in supported)}
                            if supported else {"status": "UNKNOWN", "reason": "无逐页使用事件"})
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument("op", choices=["replay"])
    p.add_argument("--task", required=True)
    p.add_argument("--state-dir", type=Path)
    p.add_argument("--audit-json", type=Path)
    p.add_argument("--budget", type=int, default=6000)
    p.add_argument("--oracle", action="store_true")
    args = p.parse_args()
    try:
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,180}", args.task):
            raise ValueError("TRACE_TASK_INVALID")
        if args.state_dir is None:
            import _store
            args.state_dir = Path(_store.state_dir())
        path = args.state_dir / "trace" / (args.task + ".jsonl")
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if any(row.get("task") != args.task for row in rows):
            raise ValueError("TRACE_SCOPE_MISMATCH")
        audit = json.loads(args.audit_json.read_text(encoding="utf-8-sig")) if args.audit_json else {}
        audit = audit.get("events", [])
        if any(row.get("payload", {}).get("tasks") != [args.task] for row in audit):
            raise ValueError("TRACE_SCOPE_MISMATCH")
        print(json.dumps({"ok": True, "task": args.task, **replay(rows, audit, args.budget, args.oracle)},
                         ensure_ascii=False))
        return 0
    except (ValueError, OSError, KeyError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    sys.exit(main())
