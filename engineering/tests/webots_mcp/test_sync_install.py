"""Real sync script with isolated source/profile/state; no installed DSH paths."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
REPO=Path(__file__).resolve().parents[3]
class SyncTests(unittest.TestCase):
    def test_runtime_exclusion_dryrun_backups_hash_and_rollback(self):
        with tempfile.TemporaryDirectory(prefix='s07-sync-') as tmp:
            root=Path(tmp);source=root/'source';source.mkdir(); dsh=root/'dsh';state=dsh/'.agent-presets'/'engineering'/'tools';state.mkdir(parents=True)
            profile=dsh/'profiles'/'webots-test';profile.mkdir(parents=True);(profile/'package.json').write_text('{"dependencies":{"dsh-engineering-ui":"file:./node_modules/dsh-engineering-ui"}}')
            shutil.copy2(REPO/'sync-to-dsh.ps1',source/'sync-to-dsh.ps1')
            tools=source/'engineering'/'tools';tools.mkdir(parents=True)
            (tools/'code.py').write_text('new code')
            (state/'code.py').write_text('old code')
            runtime=['webots_state.json','webots_jobs.sqlite3','webots_jobs.sqlite3-wal','webots_jobs.sqlite3-shm','webots_instance.lock','mode_state.json']
            for name in runtime: (tools/name).write_text('source runtime');(state/name).write_text('live runtime')
            for parent in ('webots_runs','nested/webots_runs'):
                folder=tools/parent;folder.mkdir(parents=True);(folder/'keep.txt').write_text('must not sync')
            own=state/'webots_runs'/'own';own.mkdir(parents=True);(own/'result.json').write_text('live report')
            env={**os.environ,'DSH_HOME':str(dsh),'DSH_STATE_DIR':str(state),'DSH_ENGINEERING_ROOT':str(source/'engineering')}
            # PowerShell 7's module path cannot be reused by Windows PowerShell 5.1.
            env = {key:value for key,value in env.items() if key.upper() != 'PSMODULEPATH'}
            def run(*args):
                result=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(source/'sync-to-dsh.ps1'),'-Profile','webots-test',*args],env=env,capture_output=True,timeout=30)
                self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))
            run('-DryRun');self.assertEqual((state/'code.py').read_text(),'old code');self.assertFalse(list(state.rglob('*.bak-sync-*')))
            run();self.assertEqual((state/'code.py').read_bytes(),(tools/'code.py').read_bytes())
            for name in runtime: self.assertEqual((state/name).read_text(),'live runtime',name)
            self.assertFalse((state/'nested'/'webots_runs').exists());self.assertFalse((state/'webots_runs'/'keep.txt').exists());self.assertEqual((own/'result.json').read_text(),'live report')
            backups=list(state.glob('code.py.bak-sync-*'));self.assertEqual(len(backups),1);self.assertEqual(backups[0].read_text(),'old code')
            run();self.assertEqual(len(list(state.glob('code.py.bak-sync-*'))),1)
            shutil.copy2(backups[0],state/'code.py');self.assertEqual((state/'code.py').read_text(),'old code')
if __name__=='__main__':unittest.main()
