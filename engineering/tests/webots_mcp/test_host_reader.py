"""Read-only host adapter checks; all state and profiles live in a fresh directory."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
TOOLS = Path(__file__).resolve().parents[2] / 'tools'
sys.path.insert(0, str(TOOLS))
from webots_mcp.evidence import EvidenceStore
class HostReaderTests(unittest.TestCase):
    def test_real_report_and_utf8_page_and_foreign_owner(self):
        with tempfile.TemporaryDirectory(prefix='s07-reader-') as tmp:
            root=Path(tmp); state=root/'state'; state.mkdir()
            env={**os.environ,'DSH_HOME':str(root/'dsh'),'DSH_STATE_DIR':str(state),'DSH_ENGINEERING_ROOT':str(TOOLS.parent),'PYTHONIOENCODING':'utf-8','PYTHONUTF8':'1'}
            store=EvidenceStore(state,task_id='task-1',room_id='room-1')
            store.write_report(store.new_report('experiment_report','run-1',provenance='synthetic'))
            store.append_log('run-1','中文日志\nnext')
            def call(kind,*args,room='room-1'):
                result=subprocess.run([sys.executable,'-B','-m','webots_mcp.host',kind,'--task-id','task-1','--room-id',room,'--run-id','run-1',*args],cwd=TOOLS,env=env,capture_output=True,text=True,encoding='utf-8',timeout=15)
                self.assertEqual(result.returncode,0,result.stderr)
                return json.loads(result.stdout)
            report=call('report','--report-type','experiment_report')
            self.assertTrue(report['ok']);self.assertEqual(report['data']['provenance']['kind'],'synthetic'); self.assertEqual(report['data']['data']['verdict']['status'],'unknown')
            page=call('evidence','--limit','6')['data'];self.assertEqual(page['text'],'中文');self.assertEqual(page['next_offset'],6)
            self.assertEqual(call('report','--report-type','experiment_report',room='other')['error_code'],'OWNERSHIP_MISMATCH')
            self.assertEqual(call('evidence','--name','../outside')['error_code'],'EVIDENCE_INVALID')
            self.assertEqual(call('evidence','--offset','1')['error_code'],'EVIDENCE_INVALID')
            self.assertEqual(call('evidence','--expected-sha256','0'*64)['error_code'],'EVIDENCE_INVALID')
if __name__=='__main__': unittest.main()
