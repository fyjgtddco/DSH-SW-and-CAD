"""DSH 外部监控器。Python 3.10+，仅标准库。"""
from __future__ import annotations

import argparse
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
from threading import BoundedSemaphore, Condition
import time


class Fault(Exception):
    pass


def need(test, code):
    if not test:
        raise Fault(code)


def name(value):
    need(isinstance(value, str) and 0 < len(value) <= 180
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


class Coordinator:
    def __init__(self, database, workspace, tools=None, simulation=None):
        self.workspace = Path(workspace).resolve(strict=True)
        self.tools = tools
        self.simulation = simulation
        self.cv = Condition()
        self.heartbeat = 0.0
        self.db = sqlite3.connect(database, isolation_level=None, check_same_thread=False)
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY, body TEXT)")
        row = self.db.execute("SELECT body FROM state WHERE id=1").fetchone()
        self.state = json.loads(row[0]) if row else {
            "schema": 1, "epoch": None, "frozen": True, "rev": 0, "serial": 0,
            "owner": None, "tickets": {}, "tasks": {}, "last_recovery": None,
        }
        need(self.state["schema"] == 1, "STATE_SCHEMA_UNSUPPORTED")
        if self.state["owner"] and self.state["tickets"][self.state["owner"]]["state"] == "claimed":
            self.state["frozen"] = True
            self._save(self.state)
        self.review_slots = BoundedSemaphore(2)

    def close(self):
        self.db.close()

    def _save(self, s):
        self.db.execute("INSERT OR REPLACE INTO state VALUES (1, ?)", (json.dumps(s, ensure_ascii=False),))

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
            if owner["state"] != "reserved" or time.time() < owner["claim_by"]:
                return
            owner["state"] = "expired"
            s["owner"] = None
        queue = self._queue(s)
        if queue:
            first = queue[0]
            first.update(state="reserved", claim_by=time.time() + 45)
            s["owner"] = first["ticket"]

    def _commit(self, s):
        if s != self.state:
            s["rev"] = self.state["rev"] + 1
            self._save(s)
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
            rooms[room] = {"type": kind, "parts": {p: {"gen": 0, "phase": "not_started"} for p in parts},
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
                t.update(state="claimed", lease=secrets.token_urlsafe(32))
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
                p.update(gen=gen, phase="building", command=cmd)
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
                             stamp=file_stamp(f), command=None)
                else:
                    p.update(phase="failed", detail=name(d.get("reason")), command=None)
            return {"gen": p["gen"], "state": p["phase"]}
        if op in ("reopen-room", "room-failed"):
            need(not any(x["task"] == d["task"] and x["room"] == d["room"] and
                         x["state"] in ("queued", "reserved", "claimed")
                         for x in s["tickets"].values()), "SW_TICKET_NOT_SETTLED")
            if op == "room-failed":
                room.update(closed=False, failure=name(d.get("reason")))
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
                rows.append({"name": pn, "room": rn, "room_type": room["type"],
                             "state": state, "label": LABELS[state], "generation": p["gen"],
                             "detail": p.get("detail") or "；".join(part_verdict["blockers"]) or LABELS[state],
                             "file": p.get("file"), "has_report": part_verdict["passed"],
                             "checks": part_verdict.get("checks", {}), "safe_to_restart": False})
        counts = dict(Counter(p["state"] for p in rows))
        owner = s["tickets"].get(s["owner"], {})
        return {"task": d["task"], "revision": s["rev"], "parts": rows, "rooms": rooms,
                "summary": {"counts": counts, "total": len(rows), "done": counts.get("done", 0)},
                "pulse_alive": online, "quarantined": s["frozen"], "generated_at": time.time(),
                "sw": {"owner": {k: owner[k] for k in ("task", "room", "state")} if owner else None,
                       "queue": [{k: q[k] for k in ("task", "room")} for q in self._queue(s)]}}

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
            self._commit(now)
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

    def call(self, op, d):
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
            self._commit(s)
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
            if op in ("wait-lock", "wait-rooms"):
                need(self.server.wait_slots.acquire(blocking=False), "WAIT_CAPACITY")
                try:
                    result = {"ok": True} | self.server.monitor.call(op, data)
                finally:
                    self.server.wait_slots.release()
            else:
                result = {"ok": True} | self.server.monitor.call(op, data)
        except (Fault, OSError, ValueError) as e:
            result = {"ok": False, "error": str(e) or type(e).__name__}
            status = 409 if status == 200 else status
        except Exception as e:
            print(f"handler failure: {e!r}", file=sys.stderr)
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
    args = parser.parse_args()
    args.data.mkdir(parents=True, exist_ok=True)
    if args.dsh_tools:
        args.dsh_tools = args.dsh_tools.resolve(strict=True)
        need((args.dsh_tools / "defense_gate.py").is_file(), "DSH_TOOLS_INVALID")
    if args.simulation_verifier:
        args.simulation_verifier = args.simulation_verifier.resolve(strict=True)
        need(args.simulation_verifier.is_file(), "SIMULATION_VERIFIER_INVALID")
    with process_lock(args.data / "monitor.lock"):
        token_file = args.data / "host.token"
        if not token_file.exists():
            with token_file.open("x", encoding="ascii") as f:
                f.write(secrets.token_urlsafe(48) + "\n")
        token = token_file.read_text(encoding="ascii").strip()
        need(len(token) >= 40 and token.isascii(), "TOKEN_FILE_INVALID")
        monitor = Coordinator(args.data / "monitor.sqlite3", args.workspace_root,
                              str(args.dsh_tools) if args.dsh_tools else None,
                              str(args.simulation_verifier) if args.simulation_verifier else None)
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
