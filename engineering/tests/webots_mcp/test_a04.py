"""Temporary state, tiny owned processes and synthetic traces; never starts Webots."""
import asyncio
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

TOOLS = Path(__file__).resolve().parents[2]/"tools"
sys.path.insert(0,str(TOOLS))
from webots_mcp.physical_gate import CONDITIONS,evaluate
from webots_mcp.runtime import Runtime
from webots_mcp.server import PeripheralServer
from webots_mcp.fault_resources import assert_unknown_action
from webots_mcp.ledger import Ledger


class Substitute:
    provenance = "test-double"
    effects = 0

    def __init__(self,*args):
        self.connected = False

    async def launch(self,args):
        self.connected = True
        return dict(completed_steps=0,sim_time=0.,data={"synthetic":True})

    def sample(self):
        pass

    async def command(self,name,a,progress):
        if name == "sim_step":
            type(self).effects += 1
            progress(1,.032)
            if a["steps"] == 3:
                raise ConnectionError("Side effect then lost response")
            if a["steps"] == 4:
                await asyncio.Future()
        return dict(completed_steps=1,sim_time=.032,data={})

    async def close(self):
        self.connected = False


class RuntimeTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root/"one.wbt").write_text("synthetic")
        self.server = PeripheralServer(self.root,input_root=self.root)
        self.config = {"worlds":{"one.wbt":{}}, "limits":{"process_count":8,"wall_time_s":30,"private_bytes":256*1024**2}}
        self.runtime = Runtime(self.root,self.root,self.config,Substitute)
        self.server.runtime = self.runtime
        self.ctx = dict(task_id="task",room_id="room",request_id="q")
        Substitute.effects = 0

    async def asyncTearDown(self):
        await self.runtime.close()
        self.tmp.cleanup()

    async def call(self,name,**args):
        return await self.server.dispatch(name,{**self.ctx,**args})

    async def launched(self):
        r = await self.call("sim_launch",world_path="one.wbt",operation_id="op-launch")
        self.session = dict(run_id=r["run_id"],session_generation=r["session_generation"])
        await asyncio.gather(*list(self.runtime.jobs.values()))
        self.assertEqual((await self.call("sim_job",job_id=r["job_id"],**self.session))["job_state"],"COMPLETED")

    async def test_same_id_conflict_disconnect_and_recovery(self):
        await self.launched()
        args = dict(**self.session,steps=3,operation_id="step")
        first = await self.call("sim_step",**args)
        second = await self.call("sim_step",**args,request_id="other")
        self.assertEqual(first["job_id"],second["job_id"])
        self.assertEqual((await self.call("sim_step",**{**args,"steps":2}))["error_code"],"OPERATION_CONFLICT")
        await asyncio.gather(*list(self.runtime.jobs.values()))
        result = await self.call("sim_job",**self.session,job_id=first["job_id"])
        assert_unknown_action(result,operation_id="step",session_generation=1,completed_steps=1,sim_time=.032)
        await self.runtime.close()
        self.server.runtime = self.runtime = Runtime(self.root,self.root,self.config,Substitute)
        self.assertEqual((await self.call("sim_step",**args))["error_code"],"OUTCOME_UNKNOWN")
        self.assertEqual(Substitute.effects,1)
        self.assertEqual((await self.call("sim_job",**self.session,job_id=first["job_id"],room_id="other"))["error_code"],"JOB_NOT_FOUND")

    async def test_cancel_running_outside_queue_prevents_queued_dispatch(self):
        await self.launched()
        slow = await self.call("sim_step",**self.session,operation_id="slow",steps=4)
        queued = await self.call("sim_step",**self.session,operation_id="next",steps=1)
        for _ in range(20):
            if Substitute.effects: break
            await asyncio.sleep(.001)
        cancel = await asyncio.wait_for(self.call("sim_cancel",**self.session,operation_id="cancel",job_id=slow["job_id"]),2)
        self.assertFalse(cancel["data"]["rollback_confirmed"])
        await asyncio.gather(*list(self.runtime.jobs.values()))
        self.assertEqual(Substitute.effects,1)
        self.assertEqual((await self.call("sim_job",**self.session,job_id=queued["job_id"]))["execution_state"],"not_started")

    async def test_replay_reads_accepted_launch_even_if_source_disappeared(self):
        await self.launched()
        (self.root/"one.wbt").unlink()
        result=await self.call("sim_launch",world_path="one.wbt",operation_id="op-launch",request_id="query")
        self.assertEqual(result["job_state"],"COMPLETED")
        self.assertEqual(result["run_id"],self.session["run_id"])
        denied=await self.call("sim_launch",world_path="../escape.wbt",operation_id="new")
        self.assertEqual(denied["error_code"],"INVALID_ARGUMENT")

    async def test_reset_invalidates_old_generation_and_freezes_queued_actions(self):
        await self.launched()
        reset = await self.call("sim_reset",**self.session,operation_id="reset")
        queued = await self.call("sim_step",**self.session,operation_id="old-queued",steps=1)
        await asyncio.gather(*list(self.runtime.jobs.values()))
        result = await self.call("sim_job",**self.session,job_id=reset["job_id"])
        self.assertEqual(result["data"]["new_generation"],2)
        self.assertEqual((await self.call("sim_job",**self.session,job_id=queued["job_id"]))["execution_state"],"not_started")
        self.assertEqual((await self.call("sim_step",**self.session,operation_id="old",steps=1))["error_code"],"STALE_SESSION")
        self.assertEqual((await self.call("sim_status"))["session_generation"],2)
        self.assertEqual(Substitute.effects,0)

    async def test_ledger_refusal_before_dispatch(self):
        await self.launched()
        self.runtime.ledger.db.execute("PRAGMA query_only=ON")
        r = await self.call("sim_step",**self.session,operation_id="blocked",steps=1)
        self.assertEqual(r["error_code"],"LEDGER_IO_ERROR")
        self.assertEqual(Substitute.effects,0)

    async def test_result_write_failure_is_unknown_even_before_restart(self):
        from unittest.mock import patch
        await self.launched()
        args=dict(**self.session,operation_id="lost-commit",steps=1)
        finish=self.runtime.ledger.finish
        def fault(op,phase,**updates):
            if phase in ("done","unknown"):
                raise sqlite3.OperationalError("Injected failed result commit")
            return finish(op,phase,**updates)
        with patch.object(self.runtime.ledger,"finish",fault):
            submitted=await self.call("sim_step",**args)
            await asyncio.gather(*list(self.runtime.jobs.values()))
            row=await self.call("sim_job",**self.session,job_id=submitted["job_id"])
            assert_unknown_action(row,operation_id="lost-commit",session_generation=1,completed_steps=1,sim_time=.032)
            self.assertEqual((await self.call("sim_step",**args))["error_code"],"OUTCOME_UNKNOWN")
        self.assertEqual(Substitute.effects,1)

    async def test_crash_window_and_instance_exclusion(self):
        with self.assertRaises(OSError):
            Ledger(self.root)
        await self.launched()
        for op in ("accepted","dispatched"):
            a = dict(self.ctx,**self.session,operation_id=op,steps=1)
            r = dict(a,job_id="job-"+op,ok=True,sim_time=.032,completed_steps=1)
            self.runtime.ledger.accept(a,self.runtime.ledger.digest("sim_step",a),r)
            if op=="dispatched": self.runtime.ledger.finish(op,"dispatched")
        await self.runtime.close()
        self.server.runtime = self.runtime = Runtime(self.root,self.root,self.config,Substitute)
        for op,expected in (("accepted","not_started"),("dispatched","unknown")):
            self.assertEqual((await self.call("sim_step",**self.session,operation_id=op,steps=1))["execution_state"],expected)
        self.assertEqual(Substitute.effects,0)


class PhysicalTest(unittest.TestCase):
    def setUp(self):
        self.policy = dict(topology="open-rigid",pairs={"foot-floor":"expected","arm-body":"forbidden"},
                           metric="distance",unit="m",convergence_absolute=.01,stability_limit=.01,metric_max=1)
        h = "a"*64
        conditions = {k:h for k in CONDITIONS}
        conditions["thresholds"] = hashlib.sha256(json.dumps(self.policy,sort_keys=True,separators=(",",":")).encode()).hexdigest()
        coverage = dict(distinct_bodies=True,geometry_calibrated=True,collision_enabled=True,detector_verified=True,
                        filtered=False,self_pair=True,self_collision_enabled=True,evidence_sha256=h)
        self.traces = []
        for d in (32,16):
            samples = [dict(time_ms=i*d,contacts={"foot-floor":True,"arm-body":False},value=.2,stability_error=.001) for i in range(1,64//d+1)]
            self.traces.append(dict(provenance="synthetic",world_sha256=h,robot="fixture",run_id=str(d),generation=1,step_ms=d,seed=17,metric="distance",unit="m",
                conditions=conditions.copy(),raw_evidence_sha256=h,calibrated=True,feedback_verified=True,
                coverage={k:coverage.copy() for k in self.policy["pairs"]},samples=samples))
    def verdict(self):
        return evaluate(*self.traces,self.policy)

    def test_coverage_convergence_and_expected_contacts(self):
        good = self.verdict()
        self.assertEqual(good["criteria_result"],"PASS")
        self.assertEqual(good["verdict"],"UNKNOWN")
        self.assertFalse(good["physical_valid"])
        for t in self.traces:
            t["samples"][0]["contacts"]["arm-body"]=True
        self.assertEqual(self.verdict()["criteria_result"],"FAIL")
        self.traces[0]["coverage"]["arm-body"]["self_collision_enabled"]=False
        self.assertNotIn("criteria_result",self.verdict())

    def test_changed_contact_interval_seed_alias_and_false_coverage(self):
        self.traces[0]["samples"][0]["contacts"]["foot-floor"]=False
        self.assertNotIn("criteria_result",self.verdict())
        self.traces[0]["samples"][0]["contacts"]["foot-floor"]=True
        self.traces[0]["seed"]=0
        self.traces[1]["seed"]=False
        self.assertNotIn("criteria_result",self.verdict())
        self.traces[1]["seed"]=0
        self.traces[1]["coverage"]["arm-body"]["self_pair"]=0
        self.assertNotIn("criteria_result",self.verdict())

    def test_owned_samples_use_si_clock_scope_and_both_contact_endpoints(self):
        from webots_mcp.evidence import EvidenceStore
        from webots_mcp.physical_gate import sample_from_observations
        with tempfile.TemporaryDirectory() as tmp:
            store=EvidenceStore(Path(tmp),task_id="t",room_id="r")
            base=dict(run_id="run",generation=1,world_sha256="a"*64,model_sha256="b"*64,
                      robot="r",step=1,sim_time=.032)
            def add(name,action,m,**changes):
                row={**base,"action":action,"measurement":m,**changes}
                return store.save_snapshot("run",name,json.dumps(row).encode(),kind="observation",
                                           provenance="synthetic",producer={})
            def body(name,idx,other):
                return dict(node_def=name,node_id=idx,include_descendants=False,boundingObject={"type":"Box"},
                            velocity_world=[0.,0.,0.,0.,.1,0.],contacts=[{"node_id":other}])
            s=add("s.json","sim_sensor",dict(device="s",value=.5,unit="m",device_type="PositionSensor"))
            a=add("a.json","sim_contacts",body("A",1,2))
            b=add("b.json","sim_contacts",body("B",2,1))
            opts=dict(sensor="s",unit="m",pairs={"a-b":("A","B")})
            self.assertEqual(sample_from_observations(store,"run",[s,a,b],**opts),
                             dict(time_ms=32,contacts={"a-b":True},value=.5,stability_error=.1))
            drift=add("wrong.json","sim_contacts",body("B",2,1),sim_time=.064)
            with self.assertRaisesRegex(ValueError,"different physics instants"):
                sample_from_observations(store,"run",[s,a,drift],**opts)
            (store.root/"webots_runs/run/a.json").write_text("{}")
            with self.assertRaisesRegex(ValueError,"index mismatch"):
                sample_from_observations(store,"run",[s,a,b],**opts)

    def test_missing_sensor_instability_nonconvergence_and_drift(self):
        for key,value in (("feedback_verified",False),("calibrated",False),("step_ms",15),("unit","mm")):
            test = copy.deepcopy(self.traces)
            test[1][key]=value
            self.assertNotIn("criteria_result",evaluate(*test,self.policy))
        self.traces[1]["samples"][-1]["value"]=.5
        self.assertNotIn("criteria_result",self.verdict())
        self.traces[1]["samples"][-1]["stability_error"]=float("nan")
        self.assertNotIn("criteria_result",self.verdict())


if __name__ == "__main__":
    unittest.main(verbosity=2)
