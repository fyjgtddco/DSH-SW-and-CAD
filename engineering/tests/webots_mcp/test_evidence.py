"""Offline report/evidence checks. No SW, Webots, DSH or third-party packages."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
try:
    from webots_mcp import evidence
except ModuleNotFoundError as error:
    if error.name not in {"webots_mcp", "webots_mcp.evidence"}:
        raise
    evidence = None

FIXTURES = Path(__file__).parent / "fixtures"


class EvidenceChecks(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(evidence, "S04 evidence carrier is missing")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "中文状态"
        self.root.mkdir()
        self.store = evidence.EvidenceStore(self.root, task_id="task-1", room_id="room-1")

    def report(self, report_type="model_manifest", run_id="run-1", **kwargs):
        return self.store.new_report(report_type, run_id, provenance={"kind": "synthetic"}, **kwargs)

    def test_fixture_sources_and_raw_method_arrays_are_not_reinterpreted(self):
        """A conversion, identity merge or synthetic-to-real upgrade must fail."""
        cases = json.loads((FIXTURES / "cases.json").read_text(encoding="utf-8"))["cases"]
        for case in cases:
            raw = deepcopy(case["input"])
            report = self.store.new_report("model_manifest", case["fixture_id"],
                                           provenance=raw["provenance"], data=raw)
            self.store.write_report(report)
            loaded = self.store.read_report(case["fixture_id"], "model_manifest")
            self.assertEqual(loaded["data"]["source"], raw["source"])
            self.assertEqual(loaded["data"]["components"], raw["components"])
            self.assertEqual(loaded["provenance"], raw["provenance"])
            self.assertIsNone(loaded["source_revision"])
            self.assertIn("source_revision", loaded["missing_information"])
            self.assertEqual(case["input"], raw)
        mass = json.loads((FIXTURES / "sw-mass-properties.json").read_text(encoding="utf-8"))
        report = self.report(data={"raw_mass_properties": mass})
        self.store.write_report(report)
        self.assertEqual(self.store.read_report("run-1", "model_manifest")["data"]["raw_mass_properties"], mass)

    def test_three_reports_roundtrip_and_run_ownership_is_enforced(self):
        """Reading or replacing a run from another task/room must fail."""
        for kind in ("model_manifest", "conversion_report", "experiment_report"):
            report = self.report(kind, source_revision="fixture-revision", status="unsupported")
            index = self.store.write_report(report)
            self.assertEqual(index["path"], f"webots_runs/run-1/{kind}.json")
            self.assertEqual(self.store.read_report("run-1", kind), report)
        for task, room in (("task-2", "room-1"), ("task-1", "room-2")):
            stranger = evidence.EvidenceStore(self.root, task_id=task, room_id=room)
            with self.assertRaises(PermissionError):
                stranger.read_report("run-1", "model_manifest")
            with self.assertRaises(PermissionError):
                stranger.write_report(stranger.new_report("model_manifest", "run-1", provenance="fake"))
            with self.assertRaises(PermissionError):
                stranger.read_log("run-1")

    def test_file_hash_version_and_missing_evidence_are_checked(self):
        """Stale/missing evidence cannot survive a report read or write."""
        report = self.report()
        self.store.write_report(report)
        folder = self.root / "webots_runs/run-1/evidence"
        folder.mkdir()
        source = folder / "零件.txt"
        source.write_bytes(b"abc")
        item = self.store.file_evidence("run-1", "evidence/零件.txt", kind="source-snapshot",
                                        provenance="synthetic", producer={"name": "fixture", "version": "v1", "source_revision": None})
        self.assertEqual(item["sha256"], "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
        self.assertEqual(item["size_bytes"], 3)
        self.assertEqual(item["producer"]["version"], "v1")
        report["evidence"] = [item]
        self.store.write_report(report)
        self.assertEqual(self.store.read_report("run-1", "model_manifest")["evidence"], [item])
        source.write_bytes(b"abd")
        with self.assertRaisesRegex(ValueError, "evidence"):
            self.store.read_report("run-1", "model_manifest")
        with self.assertRaisesRegex(ValueError, "evidence"):
            self.store.write_report(report)
        source.unlink()
        with self.assertRaises(FileNotFoundError):
            self.store.write_report(report)

    def test_first_run_owner_claim_is_exclusive_and_incomplete_claim_fails_closed(self):
        """Concurrent rooms cannot replace ownership or adopt a partial claim."""
        barrier = threading.Barrier(2)

        def claim(room):
            store = evidence.EvidenceStore(self.root, task_id="task-1", room_id=room)
            barrier.wait(timeout=5)
            try:
                store.write_report(store.new_report("model_manifest", "race", provenance="fake"))
                return room
            except (PermissionError, ValueError):
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(claim, room) for room in ("room-1", "room-2")]
            winners = [future.result() for future in futures if future.result() is not None]
        self.assertEqual(len(winners), 1)
        owner = evidence.EvidenceStore(self.root, task_id="task-1", room_id=winners[0])
        self.assertEqual(owner.read_report("race", "model_manifest")["room_id"], winners[0])
        with patch.object(evidence.os, "fsync", side_effect=OSError("owner write rejected")):
            with self.assertRaises(OSError):
                self.store.write_report(self.report(run_id="partial"))
        with self.assertRaisesRegex(ValueError, "incomplete"):
            self.store.write_report(self.report(run_id="partial"))
        with self.assertRaises((ValueError, PermissionError)):
            evidence.EvidenceStore(self.root, task_id="other", room_id="other").write_report(
                evidence.EvidenceStore(self.root, task_id="other", room_id="other").new_report(
                    "model_manifest", "partial", provenance="fake"))

    def test_nonfinite_json_and_write_failure_preserve_previous_report(self):
        """Invalid numbers or failed persistence must never replace the last report."""
        self.store.write_report(self.report())
        path = self.root / "webots_runs/run-1/model_manifest.json"
        before = path.read_bytes()
        for number in (float("nan"), float("inf"), -float("inf")):
            report = self.report(data={"raw_mass_properties": {"mass": number}})
            with self.assertRaises(ValueError):
                self.store.write_report(report)
            self.assertEqual(path.read_bytes(), before)
        for target in ("fsync", "replace"):
            with patch.object(evidence.os, target, side_effect=OSError("disk write rejected")):
                with self.assertRaises(OSError):
                    self.store.write_report(self.report(status="unsupported"))
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(list(path.parent.glob("*.tmp")), [])
        path.write_text(before.decode("utf-8").replace('"source_revision": null', '"source_revision": NaN'), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.store.read_report("run-1", "model_manifest")

    def test_malformed_status_and_provenance_are_contract_errors(self):
        """Untrusted containers must raise ValueError, not unhashable TypeError."""
        for field, bad in (("status", {}), ("provenance", {"kind": []})):
            report = self.report()
            report[field] = bad
            with self.assertRaises(Exception) as caught:
                self.store.write_report(report)
            self.assertIsInstance(caught.exception, ValueError)
        with self.assertRaises(ValueError):
            self.report(status={})
        with self.assertRaises(ValueError):
            self.store.new_report("model_manifest", "run-1", provenance={"kind": []})

    def test_utf8_log_pages_trim_and_stale_snapshot(self):
        """Splitting Chinese UTF-8 or accepting a cursor after trim must fail."""
        self.store.write_report(self.report())
        self.store.append_log("run-1", "甲乙丙丁\n")
        first = self.store.read_log("run-1", limit=4)
        self.assertEqual((first["text"], first["next_offset"]), ("甲", 3))
        text, page = first["text"], first
        while not page["eof"]:
            page = self.store.read_log("run-1", offset=page["next_offset"], limit=4, expected_sha256=first["sha256"])
            text += page["text"]
        self.assertEqual(text, "甲乙丙丁\n")
        self.store.append_log("run-1", "abc零件", name="small", max_bytes=8)
        result = self.store.append_log("run-1", "末", name="small", max_bytes=8)
        self.assertEqual(self.store.read_log("run-1", name="small")["text"], "件末")
        self.assertLessEqual(result["size_bytes"], 8)
        self.assertGreater(result["dropped_bytes"], 0)
        self.store.append_log("run-1", "新日志", max_bytes=8)
        with self.assertRaisesRegex(ValueError, "changed"):
            self.store.read_log("run-1", offset=first["next_offset"], expected_sha256=first["sha256"])
        with self.assertRaises(ValueError):
            self.store.read_log("run-1", offset=1, limit=4)

    def test_identifiers_and_reparse_escape_are_rejected(self):
        """Traversal, Windows device paths and junctions cannot reach outside state."""
        for run_id in ("../escape", "C:\\escape", "/escape", "a/b", "CON", "x:stream", "bad."):
            with self.assertRaises(ValueError):
                self.store.write_report(self.report(run_id=run_id))
        self.store.write_report(self.report())
        for relative in ("../outside", "evidence/../../outside", "C:/outside", "evidence/x:stream", "evidence/NUL.txt"):
            with self.assertRaises(ValueError):
                self.store.file_evidence("run-1", relative, kind="log", provenance="synthetic")
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("outside", encoding="utf-8")
        link = self.root / "webots_runs/run-1/evidence"
        if os.name == "nt":
            result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.addCleanup(os.rmdir, link)
        else:
            link.symlink_to(outside, target_is_directory=True)
            self.addCleanup(link.unlink)
        with self.assertRaises(ValueError):
            self.store.file_evidence("run-1", "evidence/secret.txt", kind="log", provenance="synthetic")
        self.assertEqual((outside / "secret.txt").read_text(encoding="utf-8"), "outside")

    def test_candidates_and_evidence_levels_do_not_become_root_causes(self):
        """A merged body must retain both candidates and unknown verdict."""
        issues = [{"issue_id": "contact-1", "evidence_level": "observed",
                   "candidate_components": [{"id": "part-1"}, {"id": "part-2"}],
                   "root_cause": None, "missing_information": ["causal experiment"],
                   "value": 0.002, "unit": "m", "threshold": None, "sim_time": None}]
        report = self.report("experiment_report", data={"issues": issues})
        self.store.write_report(report)
        loaded = self.store.read_report("run-1", "experiment_report")
        self.assertEqual(loaded["data"]["issues"], issues)
        self.assertEqual(loaded["data"]["verdict"]["status"], "unknown")
        self.assertIsNone(loaded["data"]["model"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
