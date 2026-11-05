"""Joint-entry regressions: private temporary state and offline evidence only."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
from webots_mcp.evidence import EvidenceStore
from webots_mcp.server import PeripheralServer
from webots_mcp.runtime import Runtime


class IntegrationTest(unittest.TestCase):
    def test_completed_receipt_does_not_restore_readiness(self):
        async def check():
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                config = {'worlds': {}, 'limits': {'process_count': 4, 'wall_time_s': 5, 'private_bytes': 1000000}}
                runtime = Runtime(root, root, config)
                args = dict(task_id='task', room_id='room', request_id='q', operation_id='done')
                result = dict(**args, run_id='old-run', session_generation=1, job_id='old-job',
                              ok=True, core_ready=True, stale=False, execution_state='completed')
                runtime.ledger.accept(args, 'digest', result)
                runtime.ledger.finish('done', 'done')
                await runtime.close()
                runtime = Runtime(root, root, config)
                try:
                    row = runtime.snapshot(runtime.ledger.lookup('job', 'old-job'), 'new-query')
                    self.assertEqual(row['execution_state'], 'completed')
                    self.assertFalse(row['core_ready'])
                    self.assertTrue(row['stale'])
                finally:
                    await runtime.close()
        asyncio.run(check())

    def test_mcp_issue_reads_actual_a03_and_ownership(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = EvidenceStore(root, task_id='task', room_id='room')
            receipt = store.write_report(store.new_report('model_manifest', 'run', provenance='synthetic'))
            store.write_report(store.new_report('conversion_report', 'run', provenance='synthetic'))
            store.write_report(store.new_report('experiment_report', 'run', provenance='synthetic', data={
                'model': {'path': 'model_manifest.json', 'sha256': receipt['sha256']},
                'issues': [{'issue_id': 'issue', 'candidate_components': [{'id': 'one'}, {'id': 'two'}]}]}))
            server = PeripheralServer(root, input_root=root)
            args = dict(task_id='task', room_id='room', request_id='q', run_id='run', issue_id='issue')
            result = asyncio.run(server.dispatch('sim_attribute', args))
            self.assertTrue(result['ok'], result)
            self.assertEqual(len(result['data']['candidate_components']), 2)
            self.assertIsNone(result['data']['root_cause'])
            self.assertEqual(result['data']['evidence_level'], 'suspected')
            self.assertEqual(asyncio.run(server.dispatch('sim_attribute', args | {'room_id': 'other'}))['error_code'], 'OWNERSHIP_MISMATCH')

    def test_status_names_available_code_and_native_gates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            server = PeripheralServer(root, input_root=root)
            args = dict(task_id='task', room_id='room', request_id='q')
            status = asyncio.run(server.dispatch('sim_status', args))
            self.assertIn('attribution', status['capabilities'])
            self.assertIn('native_mesh_frame', status['blocked_native'])
            self.assertFalse(status['core_ready'])
            self.assertNotIn('not implemented', status['hint'])
            (root / 'input.urdf').write_text('<robot name="x"/>')
            rejected = asyncio.run(server.dispatch('sim_convert', args | {'operation_id': 'convert', 'urdf_path': 'input.urdf'}))
            self.assertEqual(rejected['error_code'], 'CALIBRATION_REQUIRED')
            self.assertFalse((root / 'webots_jobs.sqlite3').exists())


class PipelineTest(unittest.TestCase):
    def test_part_capture_publishes_reports_and_fixed_converter_audit(self):
        from webots_mcp.pipeline import publish_part, convert_part
        from test_sw_export import ExportChecks
        from webots_mcp.sw_export import capture_part
        fixture = ExportChecks(); fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        snapshot = capture_part(fixture.model, expected_source=fixture.source, sw_version='fixture', provenance='fixture')
        with tempfile.TemporaryDirectory() as tmp:
            store = EvidenceStore(Path(tmp), task_id='task', room_id='room')
            publish_part(store, 'part', snapshot, provenance='fixture')
            before = store.read_report('part', 'model_manifest')
            self.assertEqual(before['provenance']['kind'], 'fixture')
            converted = convert_part(store, 'part', operation_id='convert')
            self.assertFalse(converted['ready_for_physics'])
            self.assertTrue(converted['precision']['preserved'], converted)
            report = store.read_report('part', 'conversion_report')
            self.assertEqual(report['data']['physical_validation'], 'not_run')
            self.assertEqual(report['status'], 'approximate')
            self.assertIn('collision geometry', report['data']['omissions'])
            with self.assertRaises(FileExistsError):
                publish_part(store, 'part', snapshot, provenance='fixture')
            with self.assertRaises(FileExistsError):
                convert_part(store, 'part', operation_id='convert')

    def test_converter_rounding_is_reported_and_native_capture_is_blocked(self):
        from webots_mcp.pipeline import publish_part, convert_part, native_gate
        from webots_mcp.physics import mass_properties
        snapshot = dict(physics=mass_properties(1e-8,[1e-8,0,0],[[1e-9,0,0],[0,2e-9,0],[0,0,2e-9]],
            length_unit='m', mass_unit='kg', frame='part-local'), vertices_m=[0,0,0,.1,0,0,0,.1,0],
            source={'sha256':'a'*64}, source_revision='a'*64, raw_mass_properties={}, sw_version='fixture')
        with tempfile.TemporaryDirectory() as tmp:
            store = EvidenceStore(Path(tmp), task_id='task', room_id='room')
            publish_part(store,'tiny',snapshot,provenance='fixture')
            result = convert_part(store,'tiny',operation_id='convert')
            self.assertFalse(result['precision']['preserved'])
            self.assertIn('mass', result['precision']['changed'])
            self.assertEqual(native_gate()['error_code'],'CALIBRATION_REQUIRED')


if __name__ == '__main__':
    unittest.main()
