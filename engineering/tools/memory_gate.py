"""工程模式的磁盘页面、检查点及四档摘要。"""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

from page_contract import FIDELITY, canonical, fingerprint, manifest, project


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def subset(value):
    if isinstance(value, dict):
        return {k: {f: v[f] for f in ("active", "type", "stage", "failed_at", "ended_at", "started_at")
                    if f in v} if isinstance(v, dict) and "active" in v else v for k, v in value.items()}
    return value


def collect(state_dir, work_dir=None, remote=True):
    state_dir = Path(state_dir).resolve()
    wf_path = state_dir / "workflow_state.json"
    wf = read(wf_path) if wf_path.exists() else {}
    task = wf.get("task_id")
    configured = wf.get("work_dir")
    root = Path(configured).resolve() if task and configured else None
    if work_dir and (not root or root != Path(work_dir).resolve()):
        raise ValueError("PAGE_TASK_SCOPE_MISMATCH")
    specs = manifest(Path(__file__).with_name("pages_manifest.json"))
    checkpoint = state_dir / "page_checkpoint.json"
    before = read(checkpoint) if checkpoint.exists() else {}
    previous = before.get("hashes", {}) if before.get("task") == task else {}
    rows = []
    for row in specs.values():
        if row["side"] != "main":
            continue
        source = row["source"]
        if source == "load_case":
            scheme = "task" if root else "state"
            path = root / "gate_load_case.json" if root else state_dir / "load_cases" / "gate_load_case.json"
            pointer = ""
        else:
            scheme, source = source.split(":", 1)
            relative, _, pointer = source.partition("#/")
            path = (state_dir if scheme == "state" else root) / relative if scheme == "state" or root else None
        if not task or path is None or not path.exists():
            if task and row["id"] in previous:
                raise ValueError("PAGE_SOURCE_MISSING: " + row["id"])
            continue
        if not path.resolve().is_relative_to(root if scheme == "task" else state_dir):
            raise ValueError("PAGE_SOURCE_OUTSIDE_SCOPE")
        value = wf if path == wf_path else read(path)
        if row["id"] == "reg.parts" and value.get("task_id") != task:
            raise ValueError("PAGE_TASK_SCOPE_MISMATCH")
        if pointer:
            value = value.get(pointer)
        if value is None:
            if row["id"] in previous:
                raise ValueError("PAGE_SOURCE_MISSING: " + row["id"])
            continue
        sha = fingerprint(value)
        rows.append(row | {"source": str(path) + ("#/" + pointer if pointer else ""),
                           "value": value, "structured": subset(value) if row["id"] == "mode.rooms" else value,
                           "sha256": sha, "fidelity": "Full", "dirty": previous.get(row["id"]) != sha})
    monitor = None
    if remote and task:
        import sw_monitor_client as client
        if client.enabled():
            monitor = client.call("page-status", {"task": task})
            rows.extend({**r, "side": "monitor"} for r in monitor["pages"]
                        if r.get("task") == task or r["id"] == "mon.owner" and r.get("task") is None)
    return {"task": task, "pages": rows, "revision": (monitor or {}).get("revision"),
            "dirty_pages": sorted(r["id"] for r in rows if r["dirty"])}


def checkpoint(state_dir):
    initial = collect(state_dir, remote=False)
    before = {r["id"]: r["sha256"] for r in initial["pages"]}
    dest = Path(state_dir) / "page_checkpoint.json"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=state_dir, delete=False) as f:
        tmp = Path(f.name)
        json.dump({"task": initial["task"], "hashes": before}, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    try:
        current = collect(state_dir, remote=False)
        if initial["task"] != current["task"] or before != {r["id"]: r["sha256"] for r in current["pages"]}:
            raise ValueError("PAGE_CHANGED_DURING_CHECKPOINT")
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)
    return {"ok": True, "task": initial["task"], "checkpoint": str(dest), "pages": list(before)}


def preparation(view, budget):
    if type(budget) is not int or not 1 <= budget <= 100_000:
        raise ValueError("PAGE_BUDGET_INVALID")
    active = sorted(view["pages"], key=lambda r: (r["type"] == "Evidence", r["id"]))
    levels = []
    for row in active:
        path = row.get("degrade_path") or ["Full"]
        if row.get("side") == "monitor":
            row = row | {"structured": {k: row[k] for k in ("phase", "generation", "room", "command") if k in row}}
            active[len(levels)] = row
            level = row["fidelity"]
            if level == "Full":
                row["value"] = row["structured"] | {
                    "checks": row.get("checks", {}), "details": row.get("details", {})}
        else:
            level = "Pointer" if row["type"] == "Evidence" and "Pointer" in path else path[0]
        levels.append(level)
    def document():
        rows = [project(row, level) for row, level in zip(active, levels)]
        down = [r["id"] for r in rows if r["fidelity"] != "Full"]
        return rows, canonical({"task": view["task"], "pages": rows, "degraded_pages": down})
    rows, summary = document()
    # UTF-8 bytes are a conservative, reproducible budget proxy. Parent DSH still measures tokens.
    while len(summary.encode()) > budget:
        candidates = [i for i, row in enumerate(active) if levels[i] != row["min_fidelity"]]
        if not candidates:
            return {"ok": False, "error": "PAGE_MINIMUM_OVER_BUDGET", "bytes": len(summary.encode()),
                    "budget": budget}
        index = max(candidates, key=lambda i: len(canonical(rows[i]).encode()))
        levels[index] = FIDELITY[FIDELITY.index(levels[index]) + 1]
        rows, summary = document()
    return {"ok": True, "summary": summary, "bytes": len(summary.encode()), "budget": budget,
            "coverage": {r["id"]: r["fidelity"] for r in rows},
            "minimum": {r["id"]: r["min_fidelity"] for r in rows}, "task": view["task"],
            "degraded_pages": [r["id"] for r in rows if r["fidelity"] != "Full"]}


def lifecycle_guard(remote=True):
    try:
        import _store
        view = collect(_store.state_dir(), remote=remote)
        if view["dirty_pages"]:
            return {"ok": False, "gate": "DIRTY_PAGES_PENDING", "error": "DIRTY_PAGES_PENDING",
                    "dirty_pages": view["dirty_pages"]}
    except Exception as e:
        return {"ok": False, "gate": "PAGE_CHECK_FAILED", "error": str(e)}
    return None


def fidelity_view(data, level, source):
    if not level or level.lower() == "full":
        return data
    level = level.title()
    base = {"fidelity": level, "source": source, "sha256": fingerprint(data)}
    if level == "Compressed":
        base["json"] = canonical(data)
    elif level == "Structured":
        base["fields"] = {k: v for k, v in data.items()
                          if isinstance(v, (str, int, float, bool, type(None)))}
        for k in ("stages", "steps", "blockers", "missing", "issues", "next"):
            if k in data:
                base["fields"][k] = data[k]
    elif level != "Pointer":
        raise ValueError("PAGE_FIDELITY_INVALID")
    return {"ok": data.get("ok", True), **base}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("op", choices=("page-status", "page-check", "page-checkpoint", "compaction-prep"))
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--dirty", action="store_true")
    parser.add_argument("--budget", type=int, default=6000)
    args = parser.parse_args()
    try:
        if args.state_dir is None:
            import _store
            args.state_dir = Path(_store.state_dir())
        if args.op == "page-checkpoint":
            out = checkpoint(args.state_dir)
        else:
            view = collect(args.state_dir, args.work_dir)
            if args.op == "compaction-prep":
                out = preparation(view, args.budget)
            else:
                rows = [{k: v for k, v in r.items() if k not in ("value", "structured")}
                        for r in view["pages"] if not args.dirty or r["dirty"]]
                clean = args.op != "page-check" or not view["dirty_pages"]
                out = {**view, "ok": clean, "pages": rows}
                if not clean:
                    out["error"] = "DIRTY_PAGES_PENDING"
    except Exception as e:
        out = {"ok": False, "error": str(e) or type(e).__name__}
    print(json.dumps(out, ensure_ascii=False))
    return 0 if out.get("ok") else 2


if __name__ == "__main__":
    sys.exit(main())
