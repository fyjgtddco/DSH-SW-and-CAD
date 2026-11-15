from pathlib import Path
import json
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase, main
from unittest.mock import patch
import sys

from memory_gate import checkpoint, collect, fidelity_view, lifecycle_guard, preparation
from page_contract import manifest
from trace_replay import replay


class MemoryContract(TestCase):
    def setUp(self):
        self.state = Path(self.enterContext(TemporaryDirectory()))
        self.save("workflow_state.json", {"task_id": "T", "work_dir": str(self.state),
                                         "mechanics_result": {"load_n": 120}})
        self.save("mode_state.json", {"rooms": {"A": {"active": True, "extra": "长" * 300}}})
        self.save("gate_load_case.json", {"load_n": 120})

    def save(self, file, data):
        (self.state / file).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def test_manifest_and_fidelity(self):
        specs = manifest(Path(__file__).with_name("pages_manifest.json"))
        self.assertEqual(specs["lc.gate_load_case"]["degrade_path"], ["Full"])
        data = {"schema": 1, "pages": list(specs.values())}
        data["pages"].append(data["pages"][0])
        self.save("invalid.json", data)
        with self.assertRaisesRegex(ValueError, "PAGE_MANIFEST_INVALID"):
            manifest(self.state / "invalid.json")
        self.assertEqual(fidelity_view({"ok": True}, "pointer", "stage-status")["fidelity"], "Pointer")

    def test_dirty_checkpoint_and_reset_precondition(self):
        with patch.dict(sys.modules, {"_store": SimpleNamespace(state_dir=lambda: str(self.state))}):
            self.assertEqual(lifecycle_guard(remote=False)["gate"], "DIRTY_PAGES_PENDING")
            checkpoint(self.state)
            self.assertIsNone(lifecycle_guard(remote=False))
            self.save("gate_load_case.json", {"load_n": 99})
            self.assertEqual(lifecycle_guard(remote=False)["dirty_pages"], ["lc.gate_load_case"])

    def test_compaction_respects_floor(self):
        view = collect(self.state, remote=False)
        big = preparation(view, 10_000)
        small = preparation(view, big["bytes"] - 600)
        self.assertTrue(small["ok"], small)
        self.assertEqual(small["coverage"]["mode.rooms"], "Structured")
        self.assertEqual(small["coverage"]["lc.gate_load_case"], "Full")
        self.assertEqual(preparation(view, 5)["error"], "PAGE_MINIMUM_OVER_BUDGET")

    def test_oracle_requires_evidence(self):
        self.assertEqual(replay([], [], 6000, True)["oracle"]["status"], "UNKNOWN")
        event = {"event":"compaction.committed","session":"S","coverage":{"p":"Pointer","x":"Full"},
                 "minimum":{"p":"Structured"},"bytes":10}
        result = replay([event, {"event":"page.use","session":"S","page":"p"}], [], 6000, True)
        self.assertEqual(result["counts"]["bootstrap_loss"], 1)
        self.assertEqual(result["oracle"]["removable_page_residencies"], 1)


if __name__ == "__main__":
    main(verbosity=2)
