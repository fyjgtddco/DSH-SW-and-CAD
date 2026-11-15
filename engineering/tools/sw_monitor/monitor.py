"""DSH 外部监控器。Python 3.10+，仅标准库。"""
from __future__ import annotations

import argparse
import importlib.util
import re
from collections import Counter
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
from threading import BoundedSemaphore, Condition, Thread
import time


class Fault(Exception):
    pass


def fault_code(value):
    match = re.match(r"^[A-Z][A-Z0-9_]+", value) if isinstance(value, str) else None
    return match.group() if match else None


def need(test, code):
    if not test:
        raise Fault(code)


def name(value, max_length=180):
    need(isinstance(value, str) and 0 < len(value) <= max_length
         and value == value.strip() and not any(ord(c) < 32 for c in value), "INVALID_NAME")
    return value


def inside(root, value):
    need(isinstance(value, str) and bool(value), "INVALID_PATH")
    path = (Path(root) / value).resolve(strict=True)
    need(path.is_relative_to(Path(root)), "PATH_OUTSIDE_TASK")
    return path


def file_stamp(path):
    stat = path.stat()
    return [stat.st_size, stat.st_mtime_ns, stat.st_ino]


def digest(path):
    before = path.stat()
    need(path.is_file() and before.st_size > 0, "EMPTY_OR_NOT_FILE")
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    after = path.stat()
    need((before.st_size, before.st_mtime_ns, before.st_ino)
         == (after.st_size, after.st_mtime_ns, after.st_ino), "FILE_CHANGED")
    return h.hexdigest()


def matches(files):
    try:
        return all(digest(Path(p)) == sha for p, sha in files.items())
    except (OSError, Fault):
        return False


def registry_matches(rows):
    try:
        for row in rows:
            data = json.loads(Path(row["file"]).read_text(encoding="utf-8-sig"))
            if data.get("task_id") != row["task"]:
                return False
            raw = json.dumps(data["parts"][row["part"]], sort_keys=True, ensure_ascii=False,
                             separators=(",", ":")).encode()
            if hashlib.sha256(raw).hexdigest() != row["sha256"]:
                return False
        return True
    except (OSError, KeyError, TypeError, ValueError):
        return False


LABELS = dict(not_started="未开始", queued="等待 SW", building="正在建",
              validating="待验收", done="完成", rebuilding="需要重建", failed="失败",
              blocked="验收受阻", disconnected="宿主失联", stale="文件或凭据已变更")


def _contract_dir(tools):
    """找出 page_contract.py / pages_manifest.json 所在目录。

    两种部署形态都要支持（2026-11-13 合入时实测踩到）：
      ① 独立交付包：monitor.py 在包根，工程侧文件在
         <包>/工程模式接入包/engineering/tools/；
      ② 仓库 / 安装副本内嵌：monitor.py 在 <工程模式根>/tools/sw_monitor/，
         而 page_contract.py 就在上一级 <工程模式根>/tools/。
    原先只认 ①，于是内嵌部署下兜底路径拼成
      tools/sw_monitor/工程模式接入包/engineering/tools/
    → FileNotFoundError，selfcheck 9 项全 ERROR。
    """
    cands = []
    if tools:
        cands.append(Path(tools))
    here = Path(__file__).parent
    cands.append(here.parent)          # ② 内嵌：tools/
    cands.append(here)
    cands.append(here / "工程模式接入包" / "engineering" / "tools")   # ① 独立包
    for c in cands:
        try:
            if (c / "page_contract.py").is_file() and (c / "pages_manifest.json").is_file():
                return c
        except OSError:
            continue
    return cands[0] if cands else here


def load_contract(tools):
    src = _contract_dir(tools)
    spec = importlib.util.spec_from_file_location("_dsh_page_contract", src / "page_contract.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, module.manifest(src / "pages_manifest.json")


class Coordinator:
    def __init__(self, database, workspace, tools=None, simulation=None, *, com_runner=None, com_probe=None):
        self.workspace = Path(workspace).resolve(strict=True)
        self.tools = tools
        self.simulation = simulation
        self.contract, self.pages = load_contract(tools if tools and
            (Path(tools) / "defense_gate.py").is_file() and
            (Path(tools) / "page_contract.py").is_file() else None)
        self.com_runner, self.com_probe = com_runner, com_probe
        self.com_busy = False
        self.cv = Condition()
        self.heartbeat = 0.0
        self.db = sqlite3.connect(database, isolation_level=None, check_same_thread=False)
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY, body TEXT)")
        self.db.execute("CREATE TABLE IF NOT EXISTS events (rev INTEGER PRIMARY KEY, op TEXT NOT NULL, payload TEXT NOT NULL, ts REAL NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS event_tasks (task TEXT NOT NULL, rev INTEGER NOT NULL, PRIMARY KEY(task, rev))")
        row = self.db.execute("SELECT body FROM state WHERE id=1").fetchone()
        self.state = json.loads(row[0]) if row else {
            "schema": 1, "epoch": None, "frozen": True, "rev": 0, "serial": 0,
            "owner": None, "tickets": {}, "tasks": {}, "last_recovery": None,
        }
        need(self.state["schema"] == 1, "STATE_SCHEMA_UNSUPPORTED")
        if self.state["owner"] and self.state["tickets"][self.state["owner"]]["state"] == "claimed":
            restarted = self._snapshot()
            restarted["frozen"] = True
            t = restarted["tickets"][restarted["owner"]]
            for job in t.get("com_jobs", {}).values():
                if job["result"]["state"] == "running":
                    job["result"].update(state="quarantined", error="MONITOR_RESTARTED_DURING_COM")
            with self.cv:
                self._commit(restarted, "restart", t)
        self.review_slots = BoundedSemaphore(2)

    def close(self):
        self.db.close()

    def _save(self, s, op, context):
        old = self.state
        changed = {k for k, task in s["tasks"].items() if old["tasks"].get(k) != task}
        tickets = []
        for k, t in s["tickets"].items():
            prev = old["tickets"].get(k)
            if prev != t:
                changed.add(t["task"])
                tickets.append({f: t.get(f) for f in ("task", "room", "seq", "state", "current")})
        if context.get("task") in s["tasks"]:
            changed.add(context["task"])
        if old["frozen"] != s["frozen"] or op == "recover":
            changed.update(s["tasks"])
        payload = {"tasks": sorted(changed), "tickets": tickets,
                   "frozen": s["frozen"], "scope": {k: context[k] for k in
                   ("task", "room", "part", "command") if isinstance(context.get(k), str)}}
        if op == "recover":
            payload["dirty_pages"] = context.get("dirty_pages", [])
            payload["observed_rev"] = context.get("observed_rev")
        if op.startswith("com-"):
            payload["state"] = context.get("state")
            payload["fault"] = fault_code(context.get("error"))
            if op == "com-complete" and context.get("state") == "saved":
                t = s["tickets"][context["ticket"]]
                job = t["com_jobs"][context["command"]]
                payload["model_sha256"] = context.get("sha256")
                payload["spec_sha256"] = self.contract.fingerprint(
                    {k: job["input"][k] for k in ("material", "steps")})
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.db.execute("INSERT OR REPLACE INTO state VALUES (1, ?)", (json.dumps(s, ensure_ascii=False),))
            self.db.execute("INSERT INTO events VALUES (?, ?, ?, ?)",
                            (s["rev"], op, json.dumps(payload, ensure_ascii=False), time.time()))
            self.db.executemany("INSERT INTO event_tasks VALUES (?, ?)", [(t, s["rev"]) for t in changed])
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def _online(self):
        return self.heartbeat > 0 and time.monotonic() - self.heartbeat < 15

    def _queue(self, s):
        return sorted((x for x in s["tickets"].values() if x["state"] == "queued"),
                      key=lambda x: x["seq"])

    def _advance(self, s):
        if s["frozen"] or not self._online():
            return
        if s["owner"]:
            owner = s["tickets"][s["owner"]]
            idle_claim = (self.com_runner and owner["state"] == "claimed" and
                          owner["current"] is None)
            if (owner["state"] != "reserved" and not idle_claim) or time.time() < owner["claim_by"]:
                return
            owner["state"] = "expired"
            s["owner"] = None
        queue = self._queue(s)
        if queue:
            first = queue[0]
            first.update(state="reserved", claim_by=time.time() + 45)
            s["owner"] = first["ticket"]

    def _commit(self, s, op="tick", context=None):
        if s != self.state:
            s["rev"] = self.state["rev"] + 1
            self._save(s, op, context or {})
            self.state = s
            self.cv.notify_all()

    def _tick(self, s):
        if not self._online() and s["owner"]:
            if s["tickets"][s["owner"]]["state"] == "claimed":
                s["frozen"] = True
        self._advance(s)

    @staticmethod
    def _ticket(s, d):
        t = s["tickets"].get(name(d.get("ticket")))
        need(t and all(t[k] == d.get(k) for k in ("epoch", "task", "room", "session")), "TICKET_SCOPE")
        return t

    def _ticket_status(self, s, t):
        q = [x["ticket"] for x in self._queue(s)]
        state = t["state"]
        if state in ("queued", "reserved", "claimed"):
            if s["frozen"]:
                state = "quarantined"
            elif not self._online():
                state = "offline"
        return {"ticket": t["ticket"], "state": state, "claim_by": t.get("claim_by"),
                "position": q.index(t["ticket"]) + 1 if t["ticket"] in q else None}

    def _read_task(self, s, d):
        t = s["tasks"].get(name(d.get("task")))
        need(t is not None, "TASK_UNKNOWN")
        return t

    def _room(self, s, d):
        task = self._read_task(s, d)
        room = task["rooms"].get(name(d.get("room")))
        need(room is not None, "ROOM_UNKNOWN")
        return task, room

    def _claimed(self, s, d):
        t = self._ticket(s, d)
        need(not s["frozen"] and self._online() and s["owner"] == t["ticket"]
             and t["state"] == "claimed" and isinstance(d.get("lease"), str)
             and secrets.compare_digest(t["lease"], d["lease"]), "LEASE_DENIED")
        return t

    def _attach(self, s, d, recover=False):
        epoch = name(d.get("epoch"))
        if recover:
            actual = self._dirty_pages(s)
            need(d.get("dirty_pages", []) == actual, "DIRTY_PAGES_PENDING")
            if actual:
                need(type(d.get("observed_rev")) is int and d["observed_rev"] == s["rev"],
                     "DIRTY_PAGES_PENDING")
        elif s["epoch"] != epoch:
            need(not self._dirty_pages(s), "DIRTY_PAGES_PENDING")
        if self.com_runner and (recover or s["epoch"] != epoch):
            need(not self.com_busy, "COM_WORKER_STILL_RUNNING")
            self.com_probe()
        if recover:
            need(d.get("drained") is True and len(name(d.get("reason"))) >= 5, "RECOVERY_PROOF_REQUIRED")
            need(s["frozen"] or not self._online(), "HOST_STILL_ACTIVE")
            if s["owner"]:
                s["tickets"][s["owner"]]["state"] = "abandoned"
                s["owner"] = None
            for task in s["tasks"].values():
                for room in task["rooms"].values():
                    for p in room["parts"].values():
                        if p["phase"] == "building":
                            p.update(phase="failed", detail="恢复后需重新开工", command=None)
            s["last_recovery"] = {"reason": d["reason"], "at": time.time(), "epoch": epoch}
        elif s["epoch"] != epoch:
            need((not self._online() and not s["frozen"]) or s["epoch"] is None,
                 "RECOVERY_REQUIRED")
            need(d.get("drained") is True and s["owner"] is None, "RECOVERY_REQUIRED")
        if s["epoch"] != epoch:
            for t in s["tickets"].values():
                if t["state"] in ("queued", "reserved"):
                    t["state"] = "abandoned"
            s["owner"] = None
        s["epoch"] = epoch
        if recover or s["owner"] is None:
            s["frozen"] = False
        self.heartbeat = time.monotonic()
        self._advance(s)
        return {"state": "quarantined" if s["frozen"] else "ready"}

    def _plan(self, s, d):
        task = name(d.get("task"))
        root = inside(self.workspace, d.get("root"))
        need(root.is_dir(), "WORK_DIR_REQUIRED")
        data = d.get("rooms")
        need(isinstance(data, list) and 0 < len(data) <= 100, "INVALID_ROOMS")
        rooms = {}
        for spec in data:
            need(isinstance(spec, dict), "INVALID_ROOM")
            room = name(spec.get("name"))
            kind = spec.get("type")
            need(room not in rooms and kind in ("model", "purchased", "other"), "INVALID_ROOM")
            parts = spec.get("parts")
            need(isinstance(parts, list) and len(parts) <= 200 and
                 (kind == "other" or bool(parts)), "INVALID_PARTS")
            parts = [name(p) for p in parts]
            need(len(set(parts)) == len(parts), "DUPLICATE_PART")
            rooms[room] = {"type": kind, "parts": {p: {"gen": 0, "phase": "not_started", **self._part_contract()} for p in parts},
                           "review": None, "closed": False}
        model_names = [pn for r in rooms.values() if r["type"] == "model" for pn in r["parts"]]
        need(len({n.casefold() for n in model_names}) == len(model_names), "DUPLICATE_MODEL_NAME")
        existing = s["tasks"].get(task)
        sim = d.get("simulation_required", False)
        need(isinstance(sim, bool), "INVALID_SIMULATION_POLICY")
        if existing:
            old = {r: (x["type"], list(x["parts"])) for r, x in existing["rooms"].items()}
            new = {r: (x["type"], list(x["parts"])) for r, x in rooms.items()}
            need(existing["root"] == str(root) and all(new.get(r) == item for r, item in old.items())
                 and existing["simulation_required"] == sim, "PLAN_CONFLICT_USE_NEW_TASK")
            existing["rooms"].update({r: item for r, item in rooms.items() if r not in old})
        else:
            need(all(Path(t["root"]) != root for t in s["tasks"].values()), "TASK_ROOT_ALREADY_BOUND")
            s["tasks"][task] = {"root": str(root), "rooms": rooms, "simulation_required": sim,
                                "registered_at": time.time()}
        return {"task": task, "rooms": len(rooms)}

    def _mutate(self, s, op, d):
        if op in ("attach", "recover"):
            return self._attach(s, d, recover=op == "recover")
        need(s["epoch"] is not None and d.get("epoch") == s["epoch"], "HOST_EPOCH")
        if op == "heartbeat":
            # tick 在更新心跳前已记录脱线期间的持锁不确定性。
            self.heartbeat = time.monotonic()
            return {"state": "quarantined" if s["frozen"] else "ready"}
        need(self._online() and not s["frozen"], "HOST_OFFLINE_OR_QUARANTINED")
        if op == "plan":
            return self._plan(s, d)
        task, room = self._room(s, d)
        if op == "request":
            key = name(d.get("ticket"))
            session = name(d.get("session"))
            if key in s["tickets"]:
                return self._ticket_status(s, self._ticket(s, d))
            need(not room["closed"] and not room.get("failure") and room["type"] != "purchased",
                 "ROOM_NOT_SW_ELIGIBLE")
            if self.com_runner:
                need(room["type"] == "model", "COM_ROOM_UNSUPPORTED")
            need(not any(t["state"] in ("queued", "reserved", "claimed")
                         and (t["task"], t["room"]) == (d["task"], d["room"])
                         for t in s["tickets"].values()), "ROOM_ALREADY_QUEUED")
            s["serial"] += 1
            t = {k: d[k] for k in ("epoch", "task", "room", "session", "ticket")}
            t.update(state="queued", seq=s["serial"], commands={}, current=None)
            s["tickets"][key] = t
            self._advance(s)
            return self._ticket_status(s, t)
        if op in ("claim", "cancel"):
            t = self._ticket(s, d)
            if op == "cancel":
                if t["state"] in ("queued", "reserved"):
                    if s["owner"] == t["ticket"]:
                        s["owner"] = None
                    t["state"] = "cancelled"
                need(t["state"] in ("cancelled", "expired", "abandoned"), "CANNOT_CANCEL_CLAIMED")
                self._advance(s)
                return self._ticket_status(s, t)
            need(s["owner"] == t["ticket"] and t["state"] in ("reserved", "claimed"), "NOT_GRANTED")
            if t["state"] == "reserved":
                t.update(state="claimed", lease=secrets.token_urlsafe(32), claim_by=time.time() + 45)
            return self._ticket_status(s, t) | {"lease": t["lease"]}
        if op == "release":
            old = self._ticket(s, d)
            if old["state"] == "released" and d.get("lease") == old.get("lease"):
                return {"state": "released"}
            t = self._claimed(s, d)
            need(t["current"] is None and d.get("drained") is True, "NOT_DRAINED")
            t["state"] = "released"
            s["owner"] = None
            self._advance(s)
            return {"state": "released"}
        if op in ("begin", "end", "part-start", "part-saved", "part-failed"):
            need(not self.com_runner, "COM_GATE_REQUIRED")
            t = self._claimed(s, d)
            cmd = name(d.get("command"))
            if op == "begin":
                need(t["current"] is None and cmd not in t["commands"], "COMMAND_ALREADY_STARTED")
                t["current"] = cmd
                t["commands"][cmd] = "running"
                return {"state": "running"}
            if op == "end":
                if t["commands"].get(cmd) == "ended":
                    return {"state": "ended"}
                need(t["current"] == cmd, "COMMAND_SCOPE")
                t["commands"][cmd] = "ended"
                t["current"] = None
                for p in room["parts"].values():
                    if p.get("command") == cmd and p["phase"] == "building":
                        p.update(phase="failed", detail="命令结束时未提交保存结果", command=None)
                return {"state": "ended"}
            need(t["current"] == cmd and room["type"] == "model", "COMMAND_SCOPE")
            p = room["parts"].get(name(d.get("part")))
            need(p is not None, "PART_UNKNOWN")
            if op == "part-start":
                expected = d.get("gen")
                need(type(expected) is int and expected == p["gen"], "STALE_GENERATION")
                need(p["phase"] != "building", "PART_ALREADY_BUILDING")
                gen = p["gen"] + 1
                p.clear()
                p.update(gen=gen, phase="building", command=cmd, **self._part_contract())
                room.update(review=None, closed=False)
            else:
                need(type(d.get("gen")) is int and d["gen"] == p["gen"]
                     and p["phase"] == "building" and p.get("command") == cmd, "STALE_GENERATION")
                if op == "part-saved":
                    f = inside(task["root"], d.get("file"))
                    need(f.suffix.lower() == ".sldprt" and f.is_file() and f.stat().st_size > 0,
                         "NATIVE_PART_REQUIRED")
                    for rn, other in task["rooms"].items():
                        for pn, row in other["parts"].items():
                            if (rn, pn) != (d["room"], d["part"]) and row.get("file"):
                                need(os.path.normcase(row["file"]) != os.path.normcase(str(f)), "FILE_ALREADY_ASSIGNED")
                    p.update(phase="validating", file=str(f), saved_at=time.time(),
                             stamp=file_stamp(f), sha256=digest(f), command=None)
                else:
                    p.update(phase="failed", detail=name(d.get("reason")), command=None)
            return {"gen": p["gen"], "state": p["phase"]}
        if op in ("reopen-room", "room-failed"):
            need(not any(x["task"] == d["task"] and x["room"] == d["room"] and
                         x["state"] in ("queued", "reserved", "claimed")
                         for x in s["tickets"].values()), "SW_TICKET_NOT_SETTLED")
            if op == "room-failed":
                reason = name(d.get("reason"))
                room.update(closed=False, failure=reason)
                for p in room["parts"].values():
                    if p["phase"] in ("building", "validating") and not self._current_result(p.get("review"))["passed"]:
                        p.update(phase="failed", detail=reason, command=None)
                        p.pop("review", None)
                return {"state": "failed"}
            pn = d.get("part")
            need(pn is None or (name(pn) in room["parts"] and room["type"] == "model"), "PART_UNKNOWN")
            room.update(closed=False, review=None, failure=None)
            for label, p in room["parts"].items():
                if pn is None or pn == label:
                    p.pop("review", None)
                    if room["type"] == "model":
                        p.update(phase="rebuilding" if p["gen"] else "not_started", detail=None)
            return {"state": "reopened"}
        raise Fault("UNKNOWN_OPERATION")

    def _snapshot(self):
        # ponytail: one atomic state row; split into tables if retained task history makes commits slow.
        return json.loads(json.dumps(self.state, ensure_ascii=False))

    def _wait_lock(self, d):
        deadline = self._deadline(d)
        with self.cv:
            while True:
                s = self._snapshot()
                self._tick(s)
                self._commit(s)
                t = self._ticket(s, d)
                info = self._ticket_status(s, t)
                if info["state"] != "queued":
                    return info
                left = deadline - time.monotonic()
                if left <= 0:
                    return info | {"timeout": True}
                self.cv.wait(min(left, 1))

    @staticmethod
    def _deadline(d):
        timeout = d.get("timeout", 25)
        need(type(timeout) in (float, int) and 0 <= timeout <= 25, "INVALID_WAIT")
        return time.monotonic() + timeout

    def _current_result(self, result):
        if result and result["passed"] and not (matches(result["files"]) and
                                                registry_matches(result.get("registry_rows", []))):
            return {"passed": False, "status": "stale", "blockers": ["验收后文件或凭据变化"]}
        return result or {"passed": False, "status": "pending", "blockers": []}

    def _room_result(self, room, results=None):
        if room.get("failure"):
            return {"passed": False, "status": "blocked", "blockers": [room["failure"]]}
        if room["type"] != "model":
            return self._current_result(room["review"])
        if results is None:
            results = [self._current_result(p.get("review")) for p in room["parts"].values()]
        passed = all(r["passed"] for r in results)
        status = next((v for v in ("stale", "blocked", "pending")
                       if any(r["status"] == v for r in results)), "passed")
        return {"passed": passed, "status": status,
                "blockers": [b for r in results for b in r["blockers"]]}

    def _board(self, s, d):
        task = self._read_task(s, d)
        rows, rooms = [], {}
        online = self._online()
        for rn, room in task["rooms"].items():
            by_part = {pn: self._current_result(p.get("review")) for pn, p in room["parts"].items()
                       if room["type"] == "model"}
            verdict = self._room_result(room, list(by_part.values()))
            rooms[rn] = {k: v for k, v in verdict.items() if k not in ("files", "registry_rows")} | {
                "closed": bool(room["closed"] and verdict["passed"])}
            waiting = any(x["task"] == d["task"] and x["room"] == rn and
                          x["state"] in ("queued", "reserved") for x in s["tickets"].values())
            for pn, p in room["parts"].items():
                part_verdict = by_part.get(pn, verdict)
                state = p["phase"]
                if room.get("failure") and not part_verdict["passed"]:
                    state = "failed"
                elif part_verdict["passed"]:
                    state = "done"
                elif part_verdict["status"] in ("blocked", "stale"):
                    state = part_verdict["status"] if state in ("validating", "not_started") else state
                elif state == "building" and (not online or s["frozen"]):
                    state = "disconnected"
                elif state == "not_started" and waiting:
                    state = "queued"
                page = self._part_page(d["task"], rn, pn, p, part_verdict["passed"])
                rows.append({k: page[k] for k in ("fidelity", "min_fidelity", "degrade_path", "dirty")} |
                            {"name": pn, "room": rn, "room_type": room["type"],
                             "state": state, "label": LABELS[state], "generation": p["gen"],
                             "detail": p.get("detail") or "；".join(part_verdict["blockers"]) or LABELS[state],
                             "file": p.get("file"), "has_report": part_verdict["passed"],
                             "checks": part_verdict.get("checks", {}), "safe_to_restart": False})
        counts = dict(Counter(p["state"] for p in rows))
        owner = s["tickets"].get(s["owner"], {})
        return {"task": d["task"], "revision": s["rev"], "parts": rows, "rooms": rooms,
                "summary": {"counts": counts, "total": len(rows), "done": counts.get("done", 0)},
                "pulse_alive": online, "quarantined": s["frozen"], "generated_at": time.time(),
                "sw": {"owner": ({k: owner[k] for k in ("task", "room", "state")} |
                                 {"command": owner.get("current")}) if owner else None,
                       "queue": [{k: q[k] for k in ("task", "room")} | {"position": i}
                                 for i, q in enumerate(self._queue(s), 1)]}}

    def _review(self, op, d):
        with self.cv:
            s = self._snapshot()
            self._tick(s)
            self._commit(s)
            need(d.get("epoch") == s["epoch"] and self._online() and not s["frozen"], "HOST_NOT_READY")
        task = self._read_task(s, d)
        rn = name(d.get("room")) if op != "verify-task" else None
        pn = name(d.get("part")) if op == "review-part" else None
        targets = ({rn: self._room(s, d)[1]} if rn is not None else
                   {r: v for r, v in task["rooms"].items() if v["type"] != "other"})
        if op == "review-part":
            need(targets[rn]["type"] == "model" and pn in targets[rn]["parts"], "PART_UNKNOWN")
            need(not targets[rn].get("failure"), "ROOM_NEEDS_REOPEN")
        if op == "review-room":
            need(targets[rn]["type"] != "model", "USE_REVIEW_PART")
            need(not targets[rn].get("failure"), "ROOM_NEEDS_REOPEN")
        need(not any(x["task"] == d["task"] and x["room"] in targets and
                     x["state"] == "claimed" and x["current"] for x in s["tickets"].values()),
             "SW_COMMAND_IN_FLIGHT")
        if op == "close-room":
            need(not any(x["task"] == d["task"] and x["room"] == rn and
                         x["state"] in ("queued", "reserved", "claimed") for x in s["tickets"].values()),
                 "SW_TICKET_NOT_SETTLED")
            result = self._room_result(targets[rn])
            need(result["passed"], "ROOM_NOT_VERIFIED")
        elif op == "verify-task":
            pending = [r for r, v in targets.items() if not v["closed"] or not self._room_result(v)["passed"]]
            need(not pending, "ROOMS_NOT_VERIFIED: " + ", ".join(pending))
            result = self._provider(op, d, task, targets)
            need(all(self._room_result(x)["passed"] for x in targets.values()), "ROOM_EVIDENCE_CHANGED")
        else:
            result = self._provider(op, d, task, targets)
        with self.cv:
            now = self._snapshot()
            self._tick(now)
            self._commit(now)
            need(now["epoch"] == s["epoch"] and self._online() and not now["frozen"], "HOST_NOT_READY")
            new_task = self._read_task(now, d)
            if op == "verify-task":
                need({r for r, x in new_task["rooms"].items() if x["type"] != "other"} == set(targets),
                     "REVIEW_INPUT_CHANGED")
            need(all(new_task["rooms"].get(r) == x for r, x in targets.items()) and
                 all(x["task"] != d["task"] or x["room"] not in targets or
                     x == s["tickets"].get(k) for k, x in now["tickets"].items()), "REVIEW_INPUT_CHANGED")
            if op == "close-room":
                new_task["rooms"][rn]["closed"] = True
            elif op in ("review-room", "review-part"):
                subject = new_task["rooms"][rn]
                if pn is not None:
                    subject = subject["parts"][pn]
                subject["review"] = result
                if not result["passed"]:
                    new_task["rooms"][rn]["closed"] = False
            self._commit(now, op, d)
        return {k: v for k, v in result.items() if k not in ("files", "registry_rows")}

    def _provider(self, op, d, task, targets):
        if op == "review-part":
            row = next(iter(targets.values()))
            need(row["parts"][d["part"]]["phase"] == "validating", "PART_NOT_SAVED")
        need(self.review_slots.acquire(blocking=False), "REVIEW_BUSY_RETRY")
        try:
            arg = {"op": op, "task": d["task"], "root": task["root"], "rooms": targets,
                   "required": task["simulation_required"], "receipts": d.get("receipts", {}),
                   "part": d.get("part"), "registered_at": task["registered_at"]}
            command = [sys.executable, "-B", str(Path(__file__).with_name("dsh_evidence.py")),
                       self.tools or "", self.simulation or ""]
            result = subprocess.run(command, input=json.dumps(arg, ensure_ascii=False),
                                    encoding="utf-8", capture_output=True, timeout=50,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                                    env=os.environ | {"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
            need(result.returncode == 0, "EVIDENCE_PROVIDER_FAILED: " + result.stderr[-400:])
            out = json.loads(result.stdout)
            need(isinstance(out.get("files"), dict) and type(out.get("passed")) is bool, "BAD_PROVIDER_RESULT")
            need(not out["passed"] or (matches(out["files"]) and registry_matches(out.get("registry_rows", []))),
                 "EVIDENCE_CHANGED")
            return out
        except subprocess.TimeoutExpired:
            raise Fault("EVIDENCE_PROVIDER_TIMEOUT") from None
        finally:
            self.review_slots.release()

    def _com(self, op, d):
        from com_gate import validated_steps
        need(self.com_runner is not None, "COM_GATE_NOT_CONFIGURED")
        with self.cv:
            s = self._snapshot()
            self._tick(s)
            self._commit(s)
            t = self._ticket(s, d)
            cmd = name(d.get("command"))
            need(isinstance(d.get("lease"), str) and secrets.compare_digest(
                t.get("lease", ""), d["lease"]), "LEASE_DENIED")
            previous = t.get("com_jobs", {}).get(cmd)
            if op == "com-status":
                deadline = self._deadline(d)
                need(previous is not None, "COM_JOB_UNKNOWN")
                while previous["result"]["state"] == "running":
                    left = deadline - time.monotonic()
                    if left <= 0:
                        break
                    self.cv.wait(left)
                    previous = self._ticket(self.state, d)["com_jobs"][cmd]
                return previous["result"]
            fields = {k: d.get(k) for k in ("part", "gen", "file", "material", "steps")}
            if previous:
                need(fields == previous["input"], "COM_REPLAY_CONFLICT")
                return previous["result"]
            t = self._claimed(s, d)
            task, room = self._room(s, d)
            need(not self.com_busy and t["current"] is None, "COM_WORKER_BUSY")
            need(room["type"] == "model", "COM_ROOM_UNSUPPORTED")
            pn = name(d.get("part"))
            p = room["parts"].get(pn)
            need(p is not None and type(d.get("gen")) is int and d["gen"] == p["gen"],
                 "STALE_GENERATION_OR_PART")
            steps = validated_steps(d.get("steps"))
            material = name(d.get("material"))
            file = name(d.get("file"), 1024)
            need(not any(c in file for c in '<>"|?*'), "INVALID_FILE")
            rel = Path(file)
            need(not rel.drive and not rel.root, "INVALID_FILE")
            need(all(seg not in (".", "..") and seg == seg.rstrip(" .") and ":" not in seg
                     for seg in rel.parts if seg != rel.anchor), "INVALID_FILE")
            raw = Path(task["root"]) / rel
            parent = inside(task["root"], str(raw.parent))
            target = parent / raw.name
            need(parent.is_dir() and target.suffix.casefold() == ".sldprt"
                 and not os.path.lexists(target), "NEW_OUTPUT_REQUIRED")
            for rn, other in task["rooms"].items():
                for part, row in other["parts"].items():
                    if (rn, part) != (d["room"], pn) and row.get("file"):
                        need(Path(row["file"]) != target, "FILE_ALREADY_ASSIGNED")
            gen = p["gen"] + 1
            p.clear()
            p.update(gen=gen, phase="building", command=cmd, **self._part_contract())
            room.update(review=None, closed=False)
            t["current"] = cmd
            accepted = {"state": "running", "command": cmd, "gen": gen, "part": pn,
                        "started_at": time.time()}
            t.setdefault("com_jobs", {})[cmd] = {"input": fields, "result": accepted}
            work = {"op": "build", "file": str(target), "root": task["root"],
                    "room": d["room"], "material": material, "steps": steps}
            self._commit(s, "com-build", d | accepted)
            self.com_busy = True
            try:
                Thread(target=self._run_com, args=(dict(d), work, accepted), daemon=True).start()
            except Exception:
                self.com_busy = False
                s = self._snapshot()
                s["frozen"] = True
                self._commit(s)
                raise
            return accepted

    def _run_com(self, d, work, accepted):
        cmd = d["command"]
        path = Path(work["file"])
        error, cleanup, stamp, report = None, None, None, None
        try:
            report = self.com_runner(work)
            need(report.get("closed") is True and report.get("documents") == 0
                 and type(report.get("pid")) is int, "CLOSE_NOT_CONFIRMED")
            stamp = file_stamp(path)
            need(not path.is_symlink() and path.resolve() == path and digest(path) == report["sha256"]
                 and file_stamp(path) == stamp, "COM_OUTPUT_CHANGED")
        except Exception as e:
            error = (str(e) or type(e).__name__)[:700]
            try:
                self.com_runner.recover_owned(error)
            except Exception as e:
                cleanup = (str(e) or type(e).__name__)[:500]
        with self.cv:
            try:
                s = self._snapshot()
                self._tick(s)
                t = self._ticket(s, d)
                _, room = self._room(s, d)
                p = room["parts"][d["part"]]
                result = dict(accepted, finished_at=time.time())
                if error:
                    result.update(state="quarantined" if cleanup else "failed", error=error,
                                  recovery_error=cleanup, recovered=cleanup is None)
                    if os.path.lexists(path):
                        result["unverified_file"] = str(path)
                    p.update(phase="failed", detail=error, command=None)
                    if cleanup:
                        s["frozen"] = True
                else:
                    result.update(report, state="saved", file=str(path))
                    p.update(phase="validating", file=str(path), saved_at=time.time(),
                             stamp=stamp, sha256=report["sha256"], command=None)
                t["com_jobs"][cmd]["result"] = result
                if not cleanup:
                    t.update(current=None, state="released")
                    s["owner"] = None
                    # Heartbeat loss remains visible. A drained worker has no lease to carry over.
                    if not s["frozen"]:
                        self._advance(s)
                self._commit(s, "com-complete", d | result)
            except Exception as e:
                # Keep the original ticket in-flight if durable commit fails.
                print(f"COM completion commit failed: {e!r}", file=sys.stderr)
                self.state["frozen"] = True
                t = self.state["tickets"][d["ticket"]]
                t["com_jobs"][cmd]["result"].update(state="quarantined", error="COM_COMMIT_FAILED")
            finally:
                self.com_busy = False
                self.cv.notify_all()

    def _part_contract(self):
        row = self.pages["mon.part.<part>"]
        return {k: row[k] for k in ("min_fidelity", "degrade_path")}

    def _part_page(self, task, room, name, part, passed):
        spec = self.pages["mon.part.<part>"]
        phase = part["phase"]
        fidelity = "Full" if passed else "Structured" if part.get("file") else "Pointer"
        fidelity = self.contract.FIDELITY[min(self.contract.FIDELITY.index(fidelity),
                                              self.contract.FIDELITY.index(spec["min_fidelity"]))]
        file = part.get("file")
        observed = None
        if file and not passed:
            try:
                observed = file_stamp(Path(file))
            except OSError:
                pass
        return {"id": "mon.part:" + self.contract.canonical([task, room, name]),
                "task": task, "room": room, "part": name, "phase": phase, "generation": part["gen"],
                "type": spec["type"], "scope": spec["scope"],
                "source": part.get("file") or "POST /v1/page-status",
                "fingerprint_kind": "model" if part.get("sha256") else "page",
                "details": {k: part[k] for k in ("file", "saved_at", "sha256", "stamp") if k in part},
                "checks": (part.get("review") or {}).get("checks", {}) if passed else {},
                "sha256": part.get("sha256") or self.contract.fingerprint(
                    {k: part.get(k) for k in ("phase", "gen", "command", "stamp", "file")}),
                "file_stamp_matches": observed == part.get("stamp") if file and not passed else None,
                "fidelity": fidelity, **self._part_contract(),
                "dirty": phase in ("building", "validating") and not passed}

    def _page_rows(self, s, task=None):
        rows = []
        for tid, t in s["tasks"].items():
            if task is not None and task != tid:
                continue
            for rn, room in t["rooms"].items():
                for pn, p in room["parts"].items():
                    rows.append(self._part_page(tid, rn, pn, p, self._current_result(p.get("review"))["passed"]
                                if room["type"] == "model" else self._room_result(room)["passed"]))
        owner = s["tickets"].get(s["owner"])
        spec = self.pages["mon.owner"]
        rows.append({"id": "mon.owner", "task": owner["task"] if owner else None,
                     "type": spec["type"], "scope": spec["scope"], "fidelity": "Structured",
                     "min_fidelity": spec["min_fidelity"], "degrade_path": spec["degrade_path"],
                     "source": "POST /v1/page-status", "phase": owner["state"] if owner else "unowned",
                     "dirty": bool(owner and owner["state"] == "claimed"),
                     "room": owner["room"] if owner else None,
                     "command": owner.get("current") if owner else None})
        return rows

    def _dirty_pages(self, s):
        return sorted(row["id"] for row in self._page_rows(s) if row["dirty"])

    def _inspect_pages(self, op, d):
        with self.cv:
            s = self._snapshot()
            self._tick(s)
            self._commit(s)
            need(d.get("epoch") == s["epoch"] or op == "page-status", "HOST_EPOCH")
            task = d.get("task")
            if op == "page-status":
                if task is not None:
                    name(task)
                rows = self._page_rows(s, task)
                return {"task": task, "revision": s["rev"], "pages": rows,
                        "dirty_pages": sorted(row["id"] for row in rows if row["dirty"])}
            name(task)
            cursor, limit = d.get("since_rev", 0), d.get("limit", 100)
            need(type(cursor) is int and 0 <= cursor <= s["rev"] and
                 type(limit) is int and 1 <= limit <= 500, "AUDIT_CURSOR_INVALID")
            records = self.db.execute(
                "SELECT e.rev,e.op,e.payload,e.ts FROM event_tasks t JOIN events e ON e.rev=t.rev "
                "WHERE t.task=? AND t.rev>? ORDER BY t.rev LIMIT ?", (task, cursor, limit + 1)).fetchall()
            events = []
            for rev, kind, payload, ts in records[:limit]:
                row = json.loads(payload)
                row["tasks"] = [task]
                row["tickets"] = [x for x in row["tickets"] if x["task"] == task]
                if row["scope"].get("task") != task:
                    row["scope"] = {}
                    row.pop("fault", None)
                    row.pop("state", None)
                    row.pop("model_sha256", None)
                    row.pop("spec_sha256", None)
                row["dirty_pages"] = [page for page in row.get("dirty_pages", [])
                                      if page == "mon.owner" and any(x["task"] == task for x in row["tickets"])
                                      or page.startswith("mon.part:") and json.loads(page[9:])[0] == task]
                events.append({"rev": rev, "op": kind, "payload": row, "ts": ts})
            return {"events": events, "revision": s["rev"], "has_more": len(records) > limit,
                    "next_since_rev": events[-1]["rev"] if events else cursor}

    def call(self, op, d):
        if op in ("audit", "page-status"):
            return self._inspect_pages(op, d)
        if op in ("com-build", "com-status"):
            return self._com(op, d)
        if op == "wait-lock":
            return self._wait_lock(d)
        if op in ("review-room", "review-part", "verify-task", "close-room"):
            return self._review(op, d)
        if op in ("board", "wait-rooms"):
            deadline = self._deadline(d)
            wanted = d.get("rooms")
            if op == "wait-rooms":
                need(isinstance(wanted, list) and bool(wanted), "EXPLICIT_ROOMS_REQUIRED")
                wanted = [name(r) for r in wanted]
            while True:
                with self.cv:
                    s = self._snapshot()
                    self._tick(s)
                    self._commit(s)
                view = self._board(s, d)
                if op == "board":
                    return view
                need(all(r in view["rooms"] for r in wanted), "ROOM_UNKNOWN")
                subset = {r: view["rooms"][r] for r in wanted}
                if not view["pulse_alive"] or view["quarantined"]:
                    state = "paused"
                elif all(x["closed"] for x in subset.values()):
                    state = "ready"
                elif any(x["status"] in ("blocked", "stale") for x in subset.values()) or any(
                        p["room"] in wanted and p["state"] in ("failed", "rebuilding")
                        for p in view["parts"]):
                    state = "attention"
                else:
                    state = "waiting"
                left = deadline - time.monotonic()
                if state != "waiting" or left <= 0:
                    return {"state": state, "rooms": subset}
                with self.cv:
                    if s["rev"] == self.state["rev"]:
                        self.cv.wait(min(left, 3))
        with self.cv:
            s = self._snapshot()
            self._tick(s)
            self._commit(s)
            s = self._snapshot()
            out = self._mutate(s, op, d)
            self._advance(s)
            self._commit(s, op, d)
            return out


@contextmanager
def process_lock(path):
    with path.open("a+b") as f:
        f.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


class HTTP(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, handler):
        self.slots = BoundedSemaphore(64)
        self.wait_slots = BoundedSemaphore(40)
        super().__init__(addr, handler)

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.connection.settimeout(8)
        self.close_connection = True
        status = 200
        try:
            length = int(self.headers.get("Content-Length", "0"))
            need(0 < length <= 1024 * 1024, "INVALID_BODY_SIZE")
            raw = self.rfile.read(length)
            if not secrets.compare_digest(self.headers.get("Authorization", "").encode(),
                                          ("Bearer " + self.server.token).encode()):
                status = 401
                raise Fault("UNAUTHORIZED")
            need(self.path.startswith("/v1/") and "/" not in self.path[4:], "INVALID_ROUTE")
            data = json.loads(raw)
            need(isinstance(data, dict), "OBJECT_REQUIRED")
            op = self.path[4:]
            if op in ("wait-lock", "wait-rooms", "com-status"):
                need(self.server.wait_slots.acquire(blocking=False), "WAIT_CAPACITY")
                try:
                    result = {"ok": True} | self.server.monitor.call(op, data)
                finally:
                    self.server.wait_slots.release()
            else:
                result = {"ok": True} | self.server.monitor.call(op, data)
        except (Fault, OSError, ValueError) as e:
            result = {"ok": False, "error": str(e) or type(e).__name__}
            if result["error"] == "DIRTY_PAGES_PENDING":
                page_status = self.server.monitor.call("page-status", {})
                result.update(dirty_pages=page_status["dirty_pages"], revision=page_status["revision"])
            status = 409 if status == 200 else status
        except Exception as e:
            # 【可观测性修复 2026-11-13】业务拒绝码（Fault）可能被包在别的
            #   异常里冒出来（例如 com_probe 在 _attach 里被调用，异常穿过
            #   Condition 上下文）。原实现一律吞成 MONITOR_INTERNAL_ERROR，
            #   使用者只看到"内部错误"，真实原因（UNMANAGED_SW_RUNNING /
            #   COM_WORKER_STILL_RUNNING …）全丢 —— 排查时极难定位。
            code = fault_code(str(e)) or fault_code(getattr(e, "code", "") or "")
            print(f"handler failure: {e!r}", file=sys.stderr)
            if code:
                status, result = 409, {"ok": False, "error": code}
            else:
                status, result = 500, {"ok": False, "error": "MONITOR_INTERNAL_ERROR"}
        body = json.dumps(result, ensure_ascii=False).encode()
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            pass

    def log_message(self, *_):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18779)
    parser.add_argument("--dsh-tools", type=Path)
    parser.add_argument("--simulation-verifier", type=Path)
    parser.add_argument("--solidworks-exe", type=Path, required=True)
    parser.add_argument("--com-timeout", type=int, default=2700,
                        help="Seconds for a submitted COM job (including SW startup)")
    args = parser.parse_args()
    need(args.dsh_tools and 1 <= args.com_timeout <= 3600, "COM_SETTINGS_INVALID")
    exe = args.solidworks_exe.resolve(strict=True)
    need(exe.is_file() and exe.name.casefold() == "sldworks.exe", "SOLIDWORKS_EXE_INVALID")
    from com_gate import OwnedSolidWorks, machine_mutex
    args.data.mkdir(parents=True, exist_ok=True)
    if args.dsh_tools:
        args.dsh_tools = args.dsh_tools.resolve(strict=True)
        need((args.dsh_tools / "defense_gate.py").is_file(), "DSH_TOOLS_INVALID")
        load_contract(args.dsh_tools)
    if args.simulation_verifier:
        args.simulation_verifier = args.simulation_verifier.resolve(strict=True)
        need(args.simulation_verifier.is_file(), "SIMULATION_VERIFIER_INVALID")
    with machine_mutex(r"Global\DSH_SW_COM_Service_v1"), process_lock(args.data / "monitor.lock"):
        token_file = args.data / "host.token"
        if not token_file.exists():
            with token_file.open("x", encoding="ascii") as f:
                f.write(secrets.token_urlsafe(48) + "\n")
        token = token_file.read_text(encoding="ascii").strip()
        need(len(token) >= 40 and token.isascii(), "TOKEN_FILE_INVALID")
        sw = OwnedSolidWorks(str(exe), str(args.dsh_tools), args.com_timeout)
        monitor = Coordinator(args.data / "monitor.sqlite3", args.workspace_root,
                              str(args.dsh_tools) if args.dsh_tools else None,
                              str(args.simulation_verifier) if args.simulation_verifier else None,
                              com_runner=sw, com_probe=sw.probe)
        with HTTP(("127.0.0.1", args.port), Handler) as server:
            server.token, server.monitor = token, monitor
            print(f"DSH monitor http://127.0.0.1:{server.server_port} | token: {token_file}", flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass


if __name__ == "__main__":
    try:
        main()
    except (Fault, OSError) as error:
        sys.exit(str(error))
