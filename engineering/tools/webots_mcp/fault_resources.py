"""Read-only resource measurements and fault oracles; no worker/recovery implementation.

Windows sampling needs the already available pywin32 package. Nothing here opens
an arbitrary process for termination, launches Webots, or assigns a physical verdict.
"""
from datetime import datetime, timezone
import math
import time

METRICS = frozenset(("wall_time_s", "cpu_time_s", "cpu_percent_one_core",
                     "working_set_bytes", "private_bytes", "process_count", "gpu_percent",
                     "log_bytes", "screenshot_bytes", "video_bytes"))


def _nonnegative(value):
    return (type(value) is int and value >= 0) or (type(value) is float and math.isfinite(value) and value >= 0)


def budget_check(sample, limits):
    """Check caller-supplied ceilings; None means unmeasured, never a budget pass."""
    if not isinstance(sample, dict) or not isinstance(limits, dict) or not limits:
        raise ValueError("A sample and nonempty explicit budget are required")
    exceeded, unknown = [], []
    for metric, ceiling in limits.items():
        if metric not in METRICS or not _nonnegative(ceiling):
            raise ValueError("Unsupported metric or invalid ceiling")
        value = sample.get(metric)
        if not _nonnegative(value):
            unknown.append(metric)
        elif value > ceiling:
            exceeded.append(metric)
    return {"within_budget": False if exceeded else (None if unknown else True),
            "exceeded": exceeded, "unknown": unknown}


def sample_job(job, *, previous=None):
    """Sample a caller-owned live Windows Job handle, without changing its state.

    The first sample starts the wall interval. Subsequent samples must retain the
    same open handle. Working sets are summed, including shared pages per process;
    private_bytes is the sum of live processes' private commit, not physical RAM.
    """
    import win32api
    import win32con
    import win32job
    import win32process

    try:
        job_key = int(job)  # Process-local handle only, never a persistent ownership ID.
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("An explicit live Job handle is required") from error
    if isinstance(job, bool) or job_key <= 0:
        raise ValueError("A null Job would query the host Job; an explicit handle is required")
    now = time.monotonic()
    accounting = win32job.QueryInformationJobObject(job, win32job.JobObjectBasicAccountingInformation)
    cpu_time = (accounting["TotalUserTime"] + accounting["TotalKernelTime"]) / 10_000_000
    started, cpu_percent = now, None
    if previous is not None:
        if (not isinstance(previous, dict) or previous.get("job_handle") != job_key
                or not all(_nonnegative(previous.get(key)) for key in
                           ("sample_monotonic_s", "started_monotonic_s", "cpu_time_s"))
                or previous["sample_monotonic_s"] >= now
                or previous["started_monotonic_s"] > previous["sample_monotonic_s"]
                or previous["cpu_time_s"] > cpu_time):
            raise ValueError("Previous sample must belong to this live Job and precede this sample")
        started = previous["started_monotonic_s"]
        cpu_percent = 100 * (cpu_time - previous["cpu_time_s"]) / (now - previous["sample_monotonic_s"])
    # ponytail: discrete samples can miss short peaks; use sustained sampling/native limits when enforcing budgets.
    pids = win32job.QueryInformationJobObject(job, win32job.JobObjectBasicProcessIdList)
    processes, missing = [], []
    for pid in pids:
        handle = None
        try:
            handle = win32api.OpenProcess(win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ,
                                          False, pid)
            if not win32job.IsProcessInJob(handle, job):
                raise ValueError("PID no longer belongs to sampled Job")
            memory = win32process.GetProcessMemoryInfo(handle)
            processes.append({"pid": pid,
                "creation_time": str(win32process.GetProcessTimes(handle)["CreationTime"]),
                "exe": win32process.GetModuleFileNameEx(handle, 0),
                "working_set_bytes": memory["WorkingSetSize"], "private_bytes": memory["PagefileUsage"]})
        except (win32api.error, ValueError) as error:
            missing.append({"pid": pid, "reason": str(error)})
        finally:
            if handle is not None:
                handle.Close()
    after_pids = win32job.QueryInformationJobObject(job, win32job.JobObjectBasicProcessIdList)
    complete = not missing and set(pids) == set(after_pids) and len(pids) == accounting["ActiveProcesses"]
    if not complete and not missing:
        missing.append({"pid": None, "reason": "Job membership changed during sample"})
    return {"schema_version": 1, "provenance": "host-measured", "scope": "windows-job",
            "snapshot_at": datetime.now(timezone.utc).isoformat(), "job_handle": job_key,
            "sample_monotonic_s": now, "started_monotonic_s": started,
            "wall_time_s": now - started, "cpu_time_s": cpu_time,
            "cpu_percent_one_core": cpu_percent, "process_count": accounting["ActiveProcesses"],
            "working_set_bytes": sum(p["working_set_bytes"] for p in processes) if complete else None,
            "private_bytes": sum(p["private_bytes"] for p in processes) if complete else None,
            "gpu_percent": None, "complete": complete, "processes": processes,
            "missing_information": missing + [{"metric": "gpu_percent", "reason": "Not measured"}]}


def assert_unknown_action(result, *, operation_id, session_generation, completed_steps, sim_time):
    """A04 test oracle for a dispatched action whose final result was lost.

    Caller supplies the last confirmed progress. This validates an observation;
    it does not manufacture a result, retry an action, or implement recovery.
    """
    expected = {"ok": False, "error_code": "OUTCOME_UNKNOWN", "execution_state": "unknown",
                "operation_id": operation_id, "session_generation": session_generation,
                "completed_steps": completed_steps, "sim_time": sim_time}
    if not isinstance(result, dict):
        raise AssertionError("An uncertain action requires a result object")
    for key, value in expected.items():
        actual = result.get(key)
        invalid_number = isinstance(value, (int, float)) and (
            (type(value) is bool and type(actual) is not bool) or
            (type(value) is not bool and (not _nonnegative(actual) or
             (type(value) is int and type(actual) is not int))))
        if key not in result or actual != value or invalid_number:
            raise AssertionError(f"Uncertain action lost {key}: {result.get(key)!r}, expected {value!r}")
