"""Single live owner, durable intent and conservative crash recovery."""
import hashlib
import json
import sqlite3
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat()


class Ledger:
    def __init__(self, root):
        import msvcrt
        from .evidence import _no_reparse
        _no_reparse(root)
        for name in ("webots_instance.lock","webots_jobs.sqlite3","webots_jobs.sqlite3-wal",
                     "webots_jobs.sqlite3-shm","webots_jobs.sqlite3-journal","webots_state.json"):
            _no_reparse(root/name)
        self.lock = (root / "webots_instance.lock").open("a+b")
        self.lock.seek(0)
        try:
            msvcrt.locking(self.lock.fileno(), msvcrt.LK_NBLCK, 1)
            self.db = sqlite3.connect(root / "webots_jobs.sqlite3", timeout=.2)
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("""CREATE TABLE IF NOT EXISTS actions(
                op TEXT PRIMARY KEY, digest TEXT NOT NULL, task TEXT NOT NULL, room TEXT NOT NULL,
                job TEXT UNIQUE NOT NULL, run TEXT NOT NULL, gen INTEGER NOT NULL,
                phase TEXT NOT NULL, result TEXT NOT NULL, updated TEXT NOT NULL)""")
            with self.db:
                for row in self.db.execute("SELECT * FROM actions WHERE phase IN ('accepted','dispatched')").fetchall():
                    dispatched = row["phase"] == "dispatched"
                    self.finish(row["op"], "unknown" if dispatched else "rejected",
                                error_code="OUTCOME_UNKNOWN" if dispatched else "INTERRUPTED_BEFORE_DISPATCH",
                                execution_state="unknown" if dispatched else "not_started",
                                ok=False, job_state="UNKNOWN" if dispatched else "FAILED", stale=True)
        except BaseException:
            if hasattr(self, "db"):
                self.db.close()
            self.lock.close()
            raise

    def close(self):
        self.db.close()
        self.lock.close()

    def lookup(self, key, value):
        assert key in ("op", "job")
        return self.db.execute(f"SELECT * FROM actions WHERE {key}=?", (value,)).fetchone()

    @staticmethod
    def digest(name, args):
        canonical = json.dumps([name, {k:v for k,v in args.items() if k != "request_id"}],
                               sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(canonical.encode()).hexdigest()

    def accept(self, args, digest, result):
        op = args["operation_id"]
        with self.db:
            self.db.execute("INSERT INTO actions VALUES(?,?,?,?,?,?,?,?,?,?)",
                (op,digest,args["task_id"],args["room_id"],result["job_id"],result["run_id"],
                 result["session_generation"],"accepted",json.dumps(result,allow_nan=False),now()))

    def finish(self, op, phase, **updates):
        row = self.lookup("op", op)
        old = json.loads(row["result"])
        old.update(updates, snapshot_at=now())
        with self.db:
            self.db.execute("UPDATE actions SET phase=?,result=?,updated=? WHERE op=?",
                            (phase,json.dumps(old,allow_nan=False),old["snapshot_at"],op))
        return old
