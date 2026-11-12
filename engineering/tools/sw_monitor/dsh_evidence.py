"""以独立进程读取当前 DSH 权威状态并复用官方验签。"""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch

from monitor import Fault, digest, file_stamp, inside, matches, need, registry_matches


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def row_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


class Review:
    def __init__(self, req, tools, simulation):
        self.req, self.root, self.tools, self.simulation = req, Path(req["root"]), tools, simulation
        self.files, self.blockers, self.checks, self.rows = {}, [], {}, []
        self.room = next(iter(req["rooms"])) if len(req["rooms"]) == 1 else None

    def file(self, value, roots=None):
        path = (self.root / str(value or "")).resolve(strict=True)
        need(any(path.is_relative_to(root) for root in (roots or [self.root])), "EVIDENCE_PATH_OUTSIDE_SCOPE")
        current = digest(path)
        need(self.files.setdefault(str(path), current) == current, "EVIDENCE_CHANGED")
        return path

    def check(self, kind, run):
        try:
            self.checks[kind] = {"status": "PASS"} | (run() or {})
        except (Fault, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            message = str(e) or type(e).__name__
            status = "UNSUPPORTED" if "UNSUPPORTED" in message else "BLOCK"
            self.checks[kind] = {"status": status, "reason": message}
            self.blockers.append(f"{kind}: {message}")

    def require_dsh(self):
        need(self.tools, "DSH_TOOLS_UNCONFIGURED")
        self.state = read(self.tools / "workflow_state.json")
        need(self.state.get("task_id") == self.req["task"] and
             Path(self.state["work_dir"]).resolve() == self.root, "DSH_TASK_SCOPE_MISMATCH")
        sys.path.insert(0, str(self.tools))
        os.environ["DSH_STATE_DIR"] = str(self.tools)
        os.environ["DSH_PART_REGISTRY"] = str(self.root / "load_cases" / "part_registry.json")
        import defense_gate
        import part_registry
        self.dg, self.pr = defense_gate, part_registry

    def registry(self, names, whole=False):
        path = inside(self.root, str(self.root / "load_cases" / "part_registry.json"))
        if whole:
            self.file(str(path))
        data = read(path)
        need(data.get("task_id") == self.req["task"], "PART_REGISTRY_TASK_MISMATCH")
        rows = data.get("parts") or {}
        for room, part, model in names:
            row = rows.get(part) or {}
            need(row.get("room") == room and inside(self.root, row.get("file")) == model,
                 f"PART_REGISTRY_MISMATCH: {room}/{part}")
            if not whole:
                self.rows.append({"file": str(path), "task": self.req["task"],
                                  "part": part, "sha256": row_hash(row)})
        return data

    def model(self):
        name = self.req["part"]
        part = self.req["rooms"][self.room]["parts"][name]
        model = self.file(part["file"])
        need(file_stamp(model) == part["stamp"], "MODEL_CHANGED_AFTER_SAVE")
        sha = self.files[str(model)]
        self.require_dsh()
        self.check("登记", lambda: self.registry([(self.room, name, model)]) and {})
        self.check("材料", lambda: self.material(model))
        physics = {}
        self.check("物理", lambda: self.physics(model, sha, part, physics))
        self.check("领域", lambda: self.domain(part, physics))
        if self.req["required"]:
            self.check("仿真", lambda: self.simulate(model, sha, part["gen"]))
        else:
            self.checks["仿真"] = {"status": "NOT_REQUIRED"}

    @staticmethod
    def official(result, label, strict=True):
        need(result.get("ok") is True and (not strict or result.get("level") == "PASS"),
             label + ": " + ("；".join(map(str, result.get("reasons") or []))
                              or str(result.get("level") or "结论不可用")))

    def material(self, model):
        path = self.file(self.dg.attestation_path(str(model)))
        result = self.dg.check_material_attestation(str(model))
        self.official(result, "材料校验")
        return {"file": str(path), "attestation": result.get("attestation")}

    def receipt(self, kind, saved_at):
        receipts = self.req["receipts"]
        need(isinstance(receipts, dict), "INVALID_RECEIPTS")
        if kind in receipts:
            path = self.file(receipts[kind])
        else:
            # 权威任务路径；多零件应显式提供归档后的原始签名凭据。
            need(Path(self.room).name == self.room and "/" not in self.room and "\\" not in self.room,
                 "RECEIPT_PATH_REQUIRED")
            path = self.file(str(self.root / "reports" / f"{self.room}.{kind}.json"))
        body = read(path)
        ts = body.get("_ts")
        need(body.get("room") == self.room and type(ts) in (int, float)
             and saved_at <= ts / 1000 <= time.time() + 10, "STALE_OR_WRONG_ROOM_CREDENTIAL")
        return path, body

    def physics(self, model, sha, part, physics):
        path, body = self.receipt("physics", part["saved_at"])
        need(body.get("part_sha256") == sha and inside(self.root, body.get("part_path")) == model,
             "PHYSICS_MODEL_HASH_MISMATCH")
        roots = [self.root] + [(base / "output" / "physics_runs").resolve()
                               for base in (self.tools, self.tools.parent, self.tools.parent.parent)]
        report = self.file(body.get("report_path"), roots)
        need(body.get("report_sha256") == self.files[str(report)] and bool(body.get("run_id")),
             "PHYSICS_REPORT_HASH_MISMATCH")
        with patch.object(self.dg, "resolve_room_credential", return_value=(str(path), [str(path)])):
            result = self.dg.check_physics_attestation(self.room)
        self.official(result, "物理校验")
        need(result.get("backed_by_real_run") is True, "PHYSICS_REAL_RUN_REQUIRED")
        physics["run_id"] = body["run_id"]
        return {"file": str(path), "report": str(report), "run_id": body["run_id"]}

    def domain(self, part, physics):
        path, body = self.receipt("domain", part["saved_at"])
        need(physics.get("run_id") and body.get("run_id") == physics["run_id"],
             "DOMAIN_PHYSICS_RUN_MISMATCH")
        with patch.object(self.dg, "resolve_room_credential", return_value=(str(path), [str(path)])):
            result = self.dg.check_domain_attestation(self.room)
        self.official(result, "领域校验")
        return {"file": str(path), "warnings": result.get("non_blocking_violations") or []}

    def simulate(self, model, sha, gen):
        need(self.simulation, "SIMULATION_VERIFIER_UNSUPPORTED")
        request = {"task": self.req["task"], "room": self.room, "part": self.req["part"],
                   "root": str(self.root), "generation": gen, "model": str(model), "sha256": sha,
                   "receipt": self.req["receipts"].get("simulation")}
        reply = subprocess.run([sys.executable, "-B", str(self.simulation)], timeout=25,
                               input=json.dumps(request, ensure_ascii=False), encoding="utf-8",
                               capture_output=True)
        need(reply.returncode == 0, "SIMULATION_VERIFIER_FAILED")
        result = json.loads(reply.stdout)
        need(all(result.get(k) == request[k] for k in ("task", "room", "part", "generation", "sha256"))
             and result.get("status") == "PASS" and result.get("real_run") is True
             and isinstance(result.get("evidence"), dict) and result["evidence"], "SIMULATION_NOT_VERIFIED")
        for path, fingerprint in result["evidence"].items():
            actual = self.file(path)
            need(self.files[str(actual)] == fingerprint, "SIMULATION_REPORT_HASH_MISMATCH")
        return {"run_id": result.get("run_id")}

    def bom(self, room):
        path = self.file(self.req["receipts"].get("bom") or
                         str(self.root / "reports" / f"{self.room}.bom.json"))
        raw = read(path)
        need(isinstance(raw, list) or raw.get("room", self.room) == self.room, "BOM_ROOM_MISMATCH")
        rows = raw if isinstance(raw, list) else raw.get("items", raw.get("bom"))
        need(isinstance(rows, list) and rows, "BOM_EMPTY")
        seen = set()
        for item in rows:
            q = item.get("qty", item.get("quantity"))
            label = item.get("name")
            need(type(q) is int and q > 0 and isinstance(label, str) and label.strip()
                 and label not in seen and (item.get("spec") or item.get("standard")), "BOM_ITEM_INVALID")
            seen.add(label)
        # 编制可以只有一个 BOM 行，也可以逐项列出计划采购件。
        planned = set(room["parts"])
        aggregate = len(planned) == 1 and "bom" in next(iter(planned)).casefold()
        need(aggregate or planned <= seen, "BOM_PLAN_ITEMS_MISSING")
        return {"file": str(path), "count": len(rows)}

    def other(self):
        # 总装、工程图仍由 DSH 原有门禁负责。这里只同步其已收尾事件。
        self.require_dsh()
        path = self.tools / "mode_state.json"
        row = (read(path).get("rooms") or {}).get(self.room) or {}
        ended = row.get("ended_at")
        need(not row.get("active") and type(ended) in (int, float) and ended >= self.req["registered_at"]
             and not row.get("failed_at"), "DSH_ROOM_NOT_ENDED")
        return {"room": self.room, "source": "mode_state.json"}

    def task(self):
        model_rows = [(room, part, Path(p["file"])) for room, v in self.req["rooms"].items()
                      if v["type"] == "model" for part, p in v["parts"].items()]
        if not model_rows:
            need(bool(self.req["rooms"]), "NO_DELIVERABLE_ROOMS")
            self.checks["台账一致性"] = {"status": "NOT_APPLICABLE"}
            return
        self.require_dsh()
        data = self.registry(model_rows, whole=True)
        wd = str(self.root)
        expected = {p for _, p, _ in model_rows}
        need(set(data["parts"]) == expected, "REGISTRY_PLAN_SET_MISMATCH")
        audit = self.pr.audit_verification(work_dir=wd)
        need(audit.get("ok") is True and not audit.get("tampered") and
             not audit.get("parts_without_fingerprint") and audit.get("intact_count") == len(expected),
             "PART_REGISTRY_AUDIT_INCOMPLETE")
        unresolved = self.pr.list_unresolved(work_dir=wd)
        need(unresolved.get("ok") is True and not unresolved.get("items"), "REGISTRY_UNRESOLVED")
        if len(expected) > 1:
            c = self.pr.check_adjacency_consistency(work_dir=wd)
            need(c.get("ok") is True and c.get("evaluated") is True
                 and not c.get("mismatches") and not c.get("dangling_adjacency")
                 and c.get("pairs_with_interface_data") == c.get("checked_pairs"),
                 "ADJACENCY_NOT_VERIFIED")
            self.checks["相邻尺寸"] = {"status": "PASS", "checked_pairs": c.get("checked_pairs")}
        else:
            self.checks["相邻尺寸"] = {"status": "NOT_APPLICABLE"}
        self.checks["台账一致性"] = {"status": "PASS", "parts": len(expected)}

    def run(self):
        if self.req["op"] == "review-part":
            try:
                self.model()
            except (Fault, OSError, ValueError, ImportError, KeyError) as e:
                self.blockers.append(str(e))
        elif self.req["op"] == "verify-task":
            self.check("任务总核实", self.task)
        else:
            room = self.req["rooms"][self.room]
            if room["type"] == "purchased":
                self.check("采购清单", lambda: self.bom(room))
            else:
                self.check("DSH 收尾", self.other)
        if not self.blockers:
            need(matches(self.files) and registry_matches(self.rows), "EVIDENCE_CHANGED_DURING_REVIEW")
            if getattr(self, "state", None):
                need(read(self.tools / "workflow_state.json").get("task_id") == self.req["task"],
                     "DSH_TASK_CHANGED_DURING_REVIEW")
        return {"passed": not self.blockers, "status": "blocked" if self.blockers else "passed",
                "checks": self.checks, "blockers": self.blockers, "files": self.files,
                "registry_rows": self.rows}


if __name__ == "__main__":
    request = json.load(sys.stdin)
    try:
        with contextlib.redirect_stdout(sys.stderr):
            result = Review(request, Path(sys.argv[1]) if sys.argv[1] else None,
                            Path(sys.argv[2]) if sys.argv[2] else None).run()
    except (Fault, OSError, ValueError, ImportError, subprocess.TimeoutExpired) as error:
        result = {"passed": False, "status": "blocked", "checks": {},
                  "blockers": [str(error)], "files": {}, "registry_rows": []}
    print(json.dumps(result, ensure_ascii=False))
