"""Explicit, in-memory fixtures. No Webots, persistence, queue, or recovery."""
from copy import deepcopy
from uuid import uuid4


class FakeWorker:
    def __init__(self, mode):
        if mode not in ("complete", "error", "timeout", "unknown"):
            raise ValueError("Unknown fake worker fixture")
        self.mode = mode
        self.runs = {}
        self.jobs = {}
        self.operations = set()

    def check_identity(self, args):
        run = self.runs.get(args.get("run_id"))
        if run is None:
            return "RUN_NOT_FOUND"
        if any(args[key] != run[key] for key in ("task_id", "room_id")):
            return "OWNERSHIP_MISMATCH"
        if args["session_generation"] != run["session_generation"]:
            return "STALE_GENERATION"
        return None

    def dispatch(self, name, args, result):
        if name == "sim_status":
            result.update(ok=True, job_state="NEW", execution_state="not_started")
            return result
        if name not in ("sim_launch", "sim_convert"):
            error = self.check_identity(args)
            if error:
                result["error_code"] = error
                return result
        if name in ("sim_job", "sim_cancel"):
            job = self.jobs.get(args["job_id"])
            if job is None or job["run_id"] != args["run_id"]:
                result["error_code"] = "JOB_NOT_FOUND"
                return result
            if name == "sim_job":
                snapshot = deepcopy(job)
                snapshot["request_id"] = args["request_id"]
                return snapshot
        if name in ("sim_cancel", "sim_reset"):
            result.update(error_code="CORE_NOT_IMPLEMENTED", hint="A04 must implement cancellation/reset semantics.")
            return result
        operation = args.get("operation_id")
        if operation:
            # ponytail: fixture-only memory; A04 supplies the durable operation ledger.
            key = (args["task_id"], args["room_id"], args.get("run_id"), operation)
            if key in self.operations:
                result["error_code"] = "OPERATION_REUSED"
                result["hint"] = "Fixture refuses replay; this is not durable deduplication."
                return result
            self.operations.add(key)
        if name in ("sim_launch", "sim_convert"):
            result.update(run_id="fake-run-" + uuid4().hex, session_generation=1)
            self.runs[result["run_id"]] = deepcopy(result)
        result.update(job_id="fake-job-" + uuid4().hex,
                      data={"fixture": self.mode, "real_execution": False},
                      hint="Synthetic fixture only; real execution and physical validity are unverified.")
        if self.mode == "complete":
            result.update(ok=True, error_code=None, execution_state="completed", job_state="COMPLETED")
        elif self.mode == "error":
            result.update(error_code="WORKER_ERROR", job_state="FAILED")
        else:
            result.update(error_code="OUTCOME_UNKNOWN", execution_state="unknown", job_state="UNKNOWN",
                          hint=f"Fake {self.mode}; query this job, do not replay the operation.")
        self.jobs[result["job_id"]] = deepcopy(result)
        return result
