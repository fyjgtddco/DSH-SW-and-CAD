"""Small JSON report and bounded UTF-8 log carrier; no simulation or inference.

Callers supply the already verified _store.state_dir() and authenticated context.
Run ownership is exclusive; report/log updates otherwise require one writer per
run. This module does not implement the A04 action ledger or instance lock.
"""
from __future__ import annotations

import codecs
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import stat
import tempfile

REPORT_TYPES = ("model_manifest", "conversion_report", "experiment_report")
MAX_REPORT_BYTES = 1024 * 1024
MAX_LOG_BYTES = 1024 * 1024
MAX_PAGE_BYTES = 64 * 1024
_PROVENANCE = ("synthetic", "fixture", "fake", "static-derived", "real-sw-export", "real-webots", "unknown")
_STATUS = ("supported", "approximate", "unsupported", "unknown")
_DEVICES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
_DEFAULTS = {
    "model_manifest": {"source": None, "components": [], "raw_mass_properties": [], "rigid_bodies": [], "joints": []},
    "conversion_report": {"inputs": [], "outputs": [], "tools": [], "mappings": [], "approximations": [],
                          "omissions": [], "support": [], "validations": [], "physical_validation": "unknown"},
    "experiment_report": {"model": None, "scene": None, "parameters": {"time_step": None, "seed": None,
                          "initial_state": None, "budget": None}, "actions": [], "observations": [], "issues": [],
                          "verdict": {"status": "unknown", "reason": "not evaluated by evidence carrier"}},
}


def _identifier(value):
    if (not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value)
            or value.upper() in _DEVICES):
        raise ValueError("invalid single-segment identifier")
    return value


def _relative(value):
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("invalid relative path")
    parts = value.split("/")
    if PurePosixPath(value).is_absolute() or PureWindowsPath(value).drive:
        raise ValueError("absolute paths are forbidden")
    for part in parts:
        if (part in {"", ".", ".."} or part.endswith((".", " "))
                or any(ord(c) < 32 or c in '<>:"\\|?*' for c in part)
                or part.split(".")[0].upper() in _DEVICES):
            raise ValueError("invalid relative path segment")
    return parts


def _no_reparse(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise ValueError("symlink/reparse paths are forbidden")


def _provenance(value):
    value = {"kind": value} if isinstance(value, str) else deepcopy(value)
    if not isinstance(value, dict) or value.get("kind") not in _PROVENANCE:
        raise ValueError("explicit provenance kind required")
    return value


def _json_bytes(value):
    encoded = (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode("utf-8")
    if len(encoded) > MAX_REPORT_BYTES:
        raise ValueError("report exceeds byte limit")
    return encoded


def _load_json(path):
    with path.open("rb") as stream:
        raw = stream.read(MAX_REPORT_BYTES + 1)
    if len(raw) > MAX_REPORT_BYTES:
        raise ValueError("report exceeds byte limit")
    value = json.loads(raw)
    _json_bytes(value)  # Reject NaN/Infinity and overflowed JSON floats on reads too.
    return value


def _atomic_write(path, data):
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


class EvidenceStore:
    def __init__(self, state_dir, *, task_id, room_id):
        root = Path(state_dir)
        if not root.is_absolute() or not root.is_dir():
            raise ValueError("state_dir must be an existing absolute directory")
        _no_reparse(root)
        for identity in (task_id, room_id):
            if not isinstance(identity, str) or not identity.strip() or len(identity) > 256:
                raise ValueError("nonempty task_id and room_id required")
        self.root = root.resolve(strict=True)
        self.task_id, self.room_id = task_id, room_id

    def _path(self, relative):
        path = self.root
        for part in _relative(relative):
            path = path / part
            _no_reparse(path)
        if not path.resolve().is_relative_to(self.root):
            raise ValueError("path outside state_dir")
        return path

    def _owner(self, value, run_id):
        if not isinstance(value, dict) or value.get("run_id") != run_id:
            raise ValueError("invalid/incomplete run ownership")
        if value.get("task_id") != self.task_id or value.get("room_id") != self.room_id:
            raise PermissionError("run belongs to another task/room")

    def _run(self, run_id, *, claim=False):
        run_id = _identifier(run_id)
        path = self._path(f"webots_runs/{run_id}")
        owner_path = self._path(f"webots_runs/{run_id}/.owner.json")
        claim_path = self._path(f"webots_runs/{run_id}/.owner")
        if claim:
            path.mkdir(parents=True, exist_ok=True)
            # Exclusive mkdir claims the run; only its winner may publish ownership.
            # Failed publication leaves the claim directory and fails closed.
            try:
                claim_path.mkdir()
            except FileExistsError:
                pass
            else:
                _atomic_write(owner_path, _json_bytes({"schema_version": 1, "run_id": run_id,
                                                       "task_id": self.task_id, "room_id": self.room_id}))
        if claim_path.exists() and not owner_path.exists():
            raise ValueError("incomplete run ownership claim; preserve for diagnosis")
        self._owner(_load_json(owner_path), run_id)
        return path

    def new_report(self, report_type, run_id, *, provenance, source_revision=None, status="unknown",
                   data=None, evidence=None, missing_information=None):
        """Make an in-memory carrier. No raw values, units or identities are inferred."""
        if report_type not in REPORT_TYPES or status not in _STATUS:
            raise ValueError("unsupported report_type/status")
        _identifier(run_id)
        if data is not None and not isinstance(data, dict):
            raise ValueError("report data must be an object")
        payload = deepcopy(_DEFAULTS[report_type])
        payload.update(deepcopy(data or {}))
        missing = list(missing_information or [])
        if source_revision is None and "source_revision" not in missing:
            missing.append("source_revision")
        return {"schema_version": 1, "report_type": report_type, "run_id": run_id,
                "task_id": self.task_id, "room_id": self.room_id, "source_revision": source_revision,
                "created_at": datetime.now(timezone.utc).isoformat(), "provenance": _provenance(provenance),
                "status": status, "evidence": deepcopy(evidence or []), "missing_information": missing, "data": payload}

    def _validate_report(self, report):
        if not isinstance(report, dict):
            raise ValueError("report must be an object")
        required = {"schema_version", "report_type", "run_id", "task_id", "room_id", "source_revision",
                    "created_at", "provenance", "status", "evidence", "missing_information", "data"}
        if not required <= report.keys() or type(report["schema_version"]) is not int or report["schema_version"] != 1:
            raise ValueError("invalid report header/schema_version")
        _identifier(report["run_id"])
        self._owner(report, report["run_id"])
        if report["report_type"] not in REPORT_TYPES or report["status"] not in _STATUS:
            raise ValueError("unsupported report_type/status")
        _provenance(report["provenance"])
        if (not isinstance(report["data"], dict) or not isinstance(report["evidence"], list)
                or not isinstance(report["missing_information"], list)
                or any(not isinstance(v, str) for v in report["missing_information"])
                or not isinstance(report["created_at"], str)
                or (report["source_revision"] is not None and not isinstance(report["source_revision"], str))):
            raise ValueError("invalid report fields")
        return _json_bytes(report)

    def _verify_evidence(self, report):
        for item in report["evidence"]:
            if not isinstance(item, dict) or not {"path", "sha256", "size_bytes", "kind", "provenance", "producer"} <= item.keys():
                raise ValueError("incomplete evidence index")
            actual = self.file_evidence(report["run_id"], item["path"], kind=item["kind"],
                                        provenance=item["provenance"], producer=item["producer"])
            if item["sha256"] != actual["sha256"] or item["size_bytes"] != actual["size_bytes"]:
                raise ValueError("evidence hash/size changed")

    def write_report(self, report):
        """Persist only after validation. Errors propagate; last good report survives."""
        raw = self._validate_report(report)
        self._run(report["run_id"], claim=True)
        self._verify_evidence(report)
        path = self._path(f"webots_runs/{report['run_id']}/{report['report_type']}.json")
        _atomic_write(path, raw)
        return {"path": path.relative_to(self.root).as_posix(), "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)}

    def read_report(self, run_id, report_type):
        if report_type not in REPORT_TYPES:
            raise ValueError("unsupported report_type")
        self._run(run_id)
        value = _load_json(self._path(f"webots_runs/{run_id}/{report_type}.json"))
        self._validate_report(value)
        if value["run_id"] != run_id or value["report_type"] != report_type:
            raise ValueError("report does not match requested run/type")
        self._verify_evidence(value)
        return value

    def save_snapshot(self,run_id,filename,raw,*,kind,provenance,producer):
        """Store an immutable bounded run-local observation and return its verified index."""
        self._run(run_id,claim=True)
        if len(_relative(filename)) != 1 or not isinstance(raw,bytes) or len(raw) > 4*1024**2:
            raise ValueError("Invalid observation snapshot")
        path = self._path(f"webots_runs/{run_id}/{filename}")
        with path.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        return self.file_evidence(run_id,filename,kind=kind,provenance=provenance,producer=producer)

    def file_evidence(self, run_id, relative_path, *, kind, provenance, producer=None):
        """Hash an existing run-local snapshot; versions unknown to the caller stay null."""
        self._run(run_id)
        _relative(relative_path)
        path = self._path(f"webots_runs/{run_id}/{relative_path}")
        if not isinstance(kind, str) or not kind:
            raise ValueError("evidence kind required")
        producer = deepcopy(producer) if producer is not None else {"name": None, "version": None, "source_revision": None}
        if not isinstance(producer, dict):
            raise ValueError("producer must be an object")
        digest, size = hashlib.sha256(), 0
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(64 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
        return {"path": relative_path, "sha256": digest.hexdigest(), "size_bytes": size,
                "kind": kind, "provenance": _provenance(provenance), "producer": producer}

    def append_log(self, run_id, text, *, name="worker", max_bytes=MAX_LOG_BYTES):
        self._run(run_id)
        _identifier(name)
        if type(max_bytes) is not int or not 4 <= max_bytes <= MAX_LOG_BYTES or not isinstance(text, str):
            raise ValueError("invalid log text/byte limit")
        path = self._path(f"webots_runs/{run_id}/logs/{name}.log")
        path.parent.mkdir(exist_ok=True)
        previous, total = b"", 0
        if path.exists():
            with path.open("rb") as stream:
                total = stream.seek(0, os.SEEK_END)
                stream.seek(max(0, total - max_bytes))
                previous = stream.read(max_bytes)
        added = text.encode("utf-8")
        # ponytail: one bounded tail per log, archive failure snapshots separately if long history is needed.
        kept = (previous + added)[-max_bytes:].decode("utf-8", errors="ignore").encode("utf-8")
        _atomic_write(path, kept)
        return {"path": f"logs/{name}.log", "size_bytes": len(kept), "dropped_bytes": total + len(added) - len(kept)}

    def read_log(self, run_id, *, name="worker", offset=0, limit=16384, expected_sha256=None):
        self._run(run_id)
        _identifier(name)
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 4 <= limit <= MAX_PAGE_BYTES:
            raise ValueError("invalid byte offset/page limit")
        path = self._path(f"webots_runs/{run_id}/logs/{name}.log")
        with path.open("rb") as stream:
            raw = stream.read(MAX_LOG_BYTES + 1)
        if len(raw) > MAX_LOG_BYTES or offset > len(raw):
            raise ValueError("log byte limit/offset exceeded")
        digest = hashlib.sha256(raw).hexdigest()
        if expected_sha256 is not None and expected_sha256 != digest:
            raise ValueError("log snapshot changed; restart pagination")
        decoder = codecs.getincrementaldecoder("utf-8")()
        chunk = raw[offset:offset + limit]
        try:
            text = decoder.decode(chunk, final=offset + len(chunk) == len(raw))
        except UnicodeDecodeError as error:
            raise ValueError("invalid UTF-8 log or byte offset") from error
        next_offset = offset + len(chunk) - len(decoder.getstate()[0])
        return {"path": f"logs/{name}.log", "text": text, "offset": offset, "next_offset": next_offset,
                "size_bytes": len(raw), "sha256": digest, "eof": next_offset == len(raw)}
