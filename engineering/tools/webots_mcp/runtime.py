"""Single-instance scheduler. All ledger changes occur on the MCP event-loop thread."""
import asyncio
import json
from datetime import datetime, timedelta, timezone
import sqlite3
import time
from uuid import uuid4

from .ledger import Ledger, now
from .fault_resources import budget_check


class Runtime:
    def __init__(self,state,root,config,backend=None):
        if backend is None:
            from .transport import Transport
            backend = Transport
        self.backend = backend
        self.state,self.root,self.config = state,root,config
        if not {"process_count","wall_time_s","private_bytes"} <= set(config["limits"]):
            raise ValueError("Explicit process, wall and private memory ceilings required")
        budget_check({},config["limits"])
        self.ledger = Ledger(state)
        self.owner = self.live = self.monitor = None
        self.jobs = {}
        self.controls = {}
        self.serial = asyncio.Lock()
        self.cleanup = asyncio.Lock()
        self.progress = dict(completed_steps=None,sim_time=None)
        self.observed = None
        self.sample_at = None
        self.next_projection = 0.
        self.poisoned = False
        self.closing = False
        self.last_error = None
        try:
            self.project("NEW")
        except BaseException:
            self.ledger.close()
            raise

    def project(self,state):
        from .evidence import _atomic_write
        owner = self.owner or (None,None,None,None)
        stamp = datetime.now(timezone.utc)
        ready = bool(self.live and self.live.connected and self.backend.provenance=="real-webots" and not self.poisoned)
        snapshot = dict(schema_version=1,task_id=owner[0],room_id=owner[1],run_id=owner[2],
            session_generation=owner[3],job_state=state,core_ready=ready,stale=not ready,
            snapshot_at=stamp.isoformat(),valid_until=(stamp+timedelta(seconds=2)).isoformat(),
            provenance=self.backend.provenance,diagnostic=self.last_error,**self.progress)
        _atomic_write(self.state/"webots_state.json",json.dumps(snapshot,allow_nan=False).encode())

    def fail(self,result,code,hint):
        return dict(result,ok=False,error_code=code,hint=hint)

    def owned(self,row,a):
        return row and (row["task"],row["room"]) == (a["task_id"],a["room_id"])

    def snapshot(self,row,request):
        result = dict(json.loads(row["result"]),request_id=request)
        live = (self.owner == (row["task"],row["room"],row["run"],row["gen"])
                and self.live and self.live.connected and not self.poisoned)
        result.update(core_ready=bool(live and self.backend.provenance == "real-webots"),
                      stale=not bool(live))
        if self.poisoned and row["phase"] == "dispatched":
            result.update(ok=False,error_code="OUTCOME_UNKNOWN",execution_state="unknown",job_state="UNKNOWN",
                          core_ready=False,stale=True,hint="Result commit unavailable. Do not replay.")
        return result

    async def dispatch(self,name,a,r,input_path=None):
        r.update(provenance=self.backend.provenance,hint="Query sim_job by job/run/generation. Unknown actions must not be replayed.")
        try:
            return await self._dispatch(name,a,r,input_path)
        except (sqlite3.Error,OSError) as err:
            self.poisoned = True
            return self.fail(r,"LEDGER_IO_ERROR",str(err)[:200])

    async def _dispatch(self,name,a,r,input_path):
        scope = (a["task_id"],a["room_id"])
        if name == "sim_status":
            visible = self.owner and self.owner[:2] == scope
            ready = bool(visible and self.live and self.live.connected and not self.poisoned)
            return dict(r,ok=True,core_ready=ready and self.backend.provenance == "real-webots",job_state="READY" if ready else "NEW",
                        session_generation=self.owner[3] if visible else None,
                        run_id=self.owner[2] if visible else None,stale=not ready,
                        snapshot_at=(self.sample_at or self.observed) if visible else r["snapshot_at"],
                        diagnostic=self.last_error if visible else None, **(self.progress if visible else {}))
        if name == "sim_job":
            row = self.ledger.lookup("job",a["job_id"])
            if not self.owned(row,a) or (row["run"],row["gen"]) != (a["run_id"],a["session_generation"]):
                return self.fail(r,"JOB_NOT_FOUND","No job in this scope")
            return self.snapshot(row,a["request_id"])
        op = a.get("operation_id")
        digest = self.ledger.digest(name,a)
        if op and (row := self.ledger.lookup("op",op)):
            if not self.owned(row,a) or row["digest"] != digest:
                return self.fail(r,"OPERATION_CONFLICT","Operation ID already bound")
            return self.snapshot(row,a["request_id"])
        if input_path is not None:
            try:
                for key,suffix in (("world_path",".wbt"),("urdf_path",".urdf")):
                    if key in a:
                        input_path(a[key],suffix)
            except (ValueError,OSError) as error:
                return self.fail(r,"INVALID_ARGUMENT",str(error)[:256])
        if self.poisoned:
            return self.fail(r,"LEDGER_IO_ERROR","Persistence failed; restart and reconcile")
        if name == "sim_convert":
            return self.fail(r,"UNSUPPORTED","Converter requires calibrated binding")
        if name == "sim_launch":
            if self.owner or self.closing:
                return self.fail(r,"INSTANCE_BUSY","One active session per state root")
            if a["world_path"] not in self.config["worlds"]:
                return self.fail(r,"UNBOUND_WORLD","Explicit approved world hash and robot required")
            run,gen = "run-"+uuid4().hex,1
        else:
            run,gen = a["run_id"],a["session_generation"]
            if self.owner != (*scope,run,gen):
                return self.fail(r,"STALE_SESSION","Run/generation is not live in this scope")
        if name in ("sim_field","sim_scene","sim_devices"):
            if not self.live or not self.live.connected or self.serial.locked() or self.jobs:
                return self.fail(r,"WORKER_BUSY","Read only in an idle verified session")
            async with self.serial:
                try:
                    data = await asyncio.wait_for(self.live.command(name,a,lambda *_:None),5)
                    return dict(r,**data,ok=True,execution_state="completed",stale=False)
                except Exception as err:
                    await self.invalidate()
                    return self.fail(r,"WORKER_DISCONNECTED",str(err))
        if name == "sim_cancel":
            target = self.ledger.lookup("job",a["job_id"])
            if not self.owned(target,a) or (target["run"],target["gen"]) != (run,gen):
                return self.fail(r,"JOB_NOT_FOUND","Target outside active session")
        if (len(self.jobs)>=16 and name!="sim_cancel") or (len(self.controls)>=16 and name=="sim_cancel"):
            return self.fail(r,"QUEUE_FULL","Session queue is bounded")
        r.update(run_id=run,session_generation=gen,job_id="job-"+uuid4().hex,ok=True,
                 job_state="QUEUED",execution_state="not_started",stale=False)
        self.ledger.accept(a,digest,r)
        if name == "sim_launch":
            self.last_error = None
            self.owner = (*scope,run,gen)
            self.world_path = a["world_path"]
            self.progress = dict(completed_steps=None,sim_time=None)
            self.observed = now()
            self.sample_at = None
        if name == "sim_cancel":
            self.ledger.finish(op,"dispatched",job_state="RUNNING",execution_state="running")
            task = asyncio.create_task(self.cancel_target(op,target,scope,run,gen))
            self.controls[op] = task
            task.add_done_callback(lambda _:self.controls.pop(op,None))
            return await asyncio.shield(task)
        task = asyncio.create_task(self.execute(name,a,op,self.owner))
        self.jobs[op] = task
        task.add_done_callback(lambda _:self.jobs.pop(op,None))
        return r

    async def cancel_target(self,op,target,scope,run,gen):
        try:
            job = self.jobs.get(target["op"])
            if job:
                job.cancel()
                await asyncio.gather(job,return_exceptions=True)
                # Task may have been cancelled before its coroutine started.
                if self.ledger.lookup("op",target["op"])["phase"] == "accepted":
                    if self.live is None and self.owner == (*scope,run,gen):
                        await self.invalidate()
                    self.ledger.finish(target["op"],"rejected",ok=False,error_code="CANCELLED_BEFORE_DISPATCH",
                                       job_state="CANCELLED",execution_state="not_started")
            return self.ledger.finish(op,"done",job_state="COMPLETED",execution_state="completed",
                                      data={"target_job":target["job"],"rollback_confirmed":False})
        except (OSError,sqlite3.Error):
            self.poisoned = True
            return self.snapshot(self.ledger.lookup("op",op),None)

    def observe(self,name,a,op,owner,out):
        from .evidence import EvidenceStore
        store = EvidenceStore(self.state,task_id=owner[0],room_id=owner[1])
        identity = self.live.identity
        data = out["data"]
        generation = self.owner[3]
        observation = dict(run_id=owner[2],generation=generation,world_sha256=identity["world_sha256"],
            robot=identity["robot"],sim_time=out["sim_time"],step=out["completed_steps"],action=name,
            operation_id=op,targets=[{"def":data["node_def"],"node_id":data["node_id"]}] if "node_def" in data else [],
            measurement=data,model_sha256=identity["model_sha256"])
        producer = dict(name="webots_mcp.controller_worker",version="A04",source_revision=None)
        raw = json.dumps(observation,ensure_ascii=False,sort_keys=True,allow_nan=False).encode()
        items = [store.save_snapshot(owner[2],"obs-"+op+".json",raw,kind="observation",
                                     provenance="real-webots",producer=producer)]
        if name == "sim_screenshot":
            filename = "capture-"+op+".jpg"
            image = (self.live.dir/filename).read_bytes()
            import hashlib
            if hashlib.sha256(image).hexdigest() != data["sha256"]:
                raise ValueError("Screenshot bytes changed after capture")
            items.append(store.save_snapshot(owner[2],filename,image,kind="screenshot",
                                             provenance="real-webots",producer=producer))
        if name == "sim_launch":
            report = store.new_report("experiment_report",owner[2],provenance="real-webots",
                data={"runtime_identity":identity,"model":{"sha256":identity["model_sha256"]}},
                evidence=items,missing_information=["paired_trace","calibration","collision_coverage","physical_gate"])
            store.write_report(report)
        store.append_log(owner[2],json.dumps(dict(action=name,operation_id=op,generation=generation,
            completed_steps=out["completed_steps"],sim_time=out["sim_time"],evidence=items),allow_nan=False)+"\n")
        return items

    async def execute(self,name,a,op,owner):
        dispatched = False
        try:
            async with self.serial:
                if self.owner != owner or self.closing or self.poisoned:
                    raise RuntimeError("Session invalidated before dispatch")
                self.ledger.finish(op,"dispatched",job_state="RUNNING",execution_state="running",**self.progress)
                self.project("STARTING" if name in ("sim_launch","sim_reset") else "RUNNING")
                dispatched = True
                def progress(n,t):
                    self.progress = dict(completed_steps=n,sim_time=t)
                    self.observed = now()
                    self.ledger.finish(op,"dispatched",**self.progress)
                if name in ("sim_launch","sim_reset"):
                    generation = owner[3]
                    if name == "sim_reset":
                        if self.monitor:
                            self.monitor.cancel()
                        await self.live.close()
                        generation += 1
                        self.owner = (*owner[:3],generation)
                    if self.backend.provenance == "real-webots":
                        from .evidence import EvidenceStore
                        store = EvidenceStore(self.state,task_id=owner[0],room_id=owner[1])
                        store._run(owner[2],claim=True)
                    self.live = self.backend(self.state,self.root,self.config,owner[2],generation)
                    self.monitor = asyncio.create_task(self.watch())
                    call = self.live.launch(dict(a,world_path=self.world_path))
                else:
                    call = self.live.command(name,a,progress)
                out = await asyncio.wait_for(call,30)
                self.progress = {k:out[k] for k in ("completed_steps","sim_time")}
                self.observed = now()
                if name == "sim_reset":
                    out["data"]["new_generation"] = generation
                if self.backend.provenance == "real-webots":
                    out["evidence"] = self.observe(name,a,op,owner,out)
                if name == "sim_shutdown":
                    await self.invalidate()
                self.project("READY" if self.live else "COMPLETED")
                self.ledger.finish(op,"done",job_state="COMPLETED",execution_state="completed",
                                   core_ready=bool(self.live and self.live.connected and self.backend.provenance == "real-webots"),**out)
        except (Exception,asyncio.CancelledError) as err:
            try:
                self.last_error = self.last_error or str(err) or "Cancellation requested"
                if dispatched or (name == "sim_launch" and self.owner == owner):
                    await self.invalidate()
                self.ledger.finish(op,"unknown" if dispatched else "rejected",ok=False,
                    error_code="OUTCOME_UNKNOWN" if dispatched else "NOT_DISPATCHED",
                    execution_state="unknown" if dispatched else "not_started",
                    job_state="UNKNOWN" if dispatched else "FAILED",stale=True,
                    hint=(self.last_error or str(err) or "Cancellation; no rollback proof")[:256])
            except Exception as failure:
                self.last_error = str(failure)
                self.poisoned = True
            if isinstance(err,sqlite3.Error):
                self.poisoned = True

    async def watch(self):
        try:
            while self.live:
                await asyncio.sleep(.25)
                if not self.live:
                    return
                self.live.sample()
                self.sample_at = now()
                if self.live.connected and time.monotonic() >= self.next_projection:
                    self.next_projection = time.monotonic()+1
                    self.project("RUNNING" if self.serial.locked() else "READY")
        except Exception as err:
            self.last_error = str(err)[:256]
            running = [op for op in self.jobs if self.ledger.lookup("op",op)["phase"] == "dispatched"]
            for op in running:
                self.jobs[op].cancel()
            if not running:
                await self.invalidate()

    async def invalidate(self):
        async with self.cleanup:
            await self._invalidate()

    async def _invalidate(self):
        self.closing = True
        self.owner = None
        if self.monitor and self.monitor is not asyncio.current_task():
            self.monitor.cancel()
        if self.live:
            try:
                await self.live.close()
            except Exception:
                self.poisoned = True
                raise
        self.live = self.owner = None
        self.project("UNKNOWN" if self.last_error else "COMPLETED")
        self.closing = False
        self.observed = now()

    async def close(self):
        tasks = list(self.jobs.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks,*list(self.controls.values()),return_exceptions=True)
        try:
            await self.invalidate()
        finally:
            self.ledger.close()
