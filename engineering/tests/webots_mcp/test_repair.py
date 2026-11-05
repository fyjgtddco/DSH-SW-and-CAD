"""Offline contracts. Synthetic records never qualify as real physics."""
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import json
import unittest
from unittest.mock import patch

from webots_mcp.evidence import EvidenceStore, _json_bytes
from webots_mcp.repair import attribute, digest, worker_observation
from webots_mcp import repair_trial as rt


class RepairTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.src = self.base/"src"
        self.src.mkdir()
        self.root = self.src/"Root.SLDASM"
        self.part = self.src/"Slide.SLDPRT"
        self.root.write_bytes(b"original")
        self.part.write_bytes(b"slide")
        self.label = "repair-test"
        (self.base/"state").mkdir()
        self.store = EvidenceStore(self.base/"state", task_id="A03",
            room_id="room-"+sha256(self.label.encode()).hexdigest())
        self.make("before", digest(self.root), 4)
        self.policy = dict(approved_reports=attribute(self.store,"before","gap")["report_sha256"],
            component_id="slide", kind="distance_mate", reason="known offset", expected_improvement="reduce residual",
            old_m=.04,new_m=.02,min_m=.01,max_m=.05, root=self.root.name,
            files={self.root.name:digest(self.root),self.part.name:digest(self.part)},
            dimension="D1@Distance1",mate_reference="bWF0ZQ==")

    def make(self, run, revision, value, *, merged=False, stale=False, step=.032):
        ctx = dict(source_revision=revision,configuration="Default",generation=1)
        self.candidate = dict(id="slide",snapshot_context=ctx | {"generation":2 if stale else 1},
            configuration="Default",instance_path="Slide-1",source_file=str(self.part),
            persistent_reference="c2xpZGU=",owner_document=str(self.root))
        candidates = [self.candidate]
        if merged:
            candidates.append(self.candidate | {"id":"slide2","instance_path":"Slide-2"})
        target = {"kind":"urdf_link","id":"slide"}
        obs = dict(id="travel",value=value,unit="mm",configuration="Default",generation=1,
                   targets=[target],run_id=run,world_sha256="0"*64,robot="bench",sim_time=step,step=1)
        def write(kind,data,evidence=None):
            r = self.store.new_report(kind,run,provenance="synthetic",source_revision=revision,
                    status="supported", data=data,evidence=evidence)
            receipt = self.store.write_report(r)
            return {"path":kind+".json","sha256":receipt["sha256"]}
        ml = write("model_manifest",dict(components=candidates))
        conv = write("conversion_report",dict(mappings=[dict(target=target, component_ids=[c["id"] for c in candidates])]))
        folder = self.store.root/"webots_runs"/run
        (folder/"obs.json").write_bytes(_json_bytes(obs))
        index = self.store.file_evidence(run,"obs.json",kind="observation",provenance="synthetic")
        obs["raw_sha256"] = index["sha256"]
        write("experiment_report",dict(model=ml,conversion=conv,scene={"template":"s"},actions=[{"motor":1}],
            parameters=dict(seed=0,initial_state={"q":0},time_step=step,environment={"g":9.81},budget=1,
                 thresholds=[dict(id="travel",unit="mm",direction="max",limit=2)]),
            verdict=dict(verdict="UNKNOWN",provenance="synthetic",physical_valid=False,reasons=["fixture"]),
            issues=[dict(issue_id="gap",observation_id="travel")],observations=[obs]),[index])

    def test_attribution(self):
        result = attribute(self.store,"before","gap")
        self.assertEqual(result["evidence_level"], "derived")
        self.assertIsNone(result["root_cause"])
        self.make("merged",digest(self.root),4,merged=True)
        self.assertEqual(len(attribute(self.store,"merged","gap")["candidate_components"]),2)
        self.make("stale",digest(self.root),4,stale=True)
        self.assertIn("stale occurrence snapshot",attribute(self.store,"stale","gap")["missing_information"])
        (self.store.root/"webots_runs/before/obs.json").write_text("changed")
        with self.assertRaisesRegex(ValueError,"hash/size"):
            attribute(self.store,"before","gap")

    def test_native_distance_api_with_abi_double(self):
        self.make("fixture",digest(self.root),4)
        candidate = self.candidate
        dim = SimpleNamespace(FullName="D1@Distance1",ReadOnly=False,metres=.04)
        dim.GetSystemValue3 = lambda scope,names:[dim.metres]
        calls = []
        def set_value(value,scope,names):
            calls.append((scope,names))
            dim.metres = value
            return 0
        dim.SetSystemValue3 = set_value
        native = SimpleNamespace(Name2="Slide-1",ReferencedConfiguration="Default",
            GetSuppression2=lambda:2, GetPathName=lambda:str(self.part))
        mate = SimpleNamespace(Type=5,GetMateEntityCount=lambda:2,
            MateEntity=lambda i:SimpleNamespace(ReferenceComponent=native if i else None))
        feature = SimpleNamespace(IsSuppressed2=lambda *a:[False],GetSpecificFeature2=lambda:mate,
            GetFirstDisplayDimension=lambda:SimpleNamespace(GetDimension2=lambda i:dim))
        model = SimpleNamespace(Extension=SimpleNamespace(GetWhatsWrongCount=lambda:0),GetPathName=lambda:str(self.root),
            GetSaveFlag=lambda:False,ForceRebuild3=lambda top:True,
            ConfigurationManager=SimpleNamespace(ActiveConfiguration=SimpleNamespace(Name="Default")))
        def resolve(_,raw):
            return ({b"slide":native,b"mate":feature}[raw],0)
        def invoke(save=lambda m:(True,0,0)):
            rt.edit_distance(model,candidate,candidate["snapshot_context"],self.policy,self.root,
                resolve=resolve,save=save)
        invoke()
        self.assertEqual((dim.metres,calls),(.02,[(1,None)]))
        dim.metres = .04
        dim.ReadOnly = True
        with self.assertRaisesRegex(ValueError,"DIMENSION_NOT_WRITABLE"):
            invoke()
        self.assertEqual(len(calls),1)
        dim.ReadOnly = False
        with self.assertRaisesRegex(ValueError,"SAVE_NOT_CLEAN"):
            invoke(lambda m:(True,0,2))

    def contract_pair(self, run, revision, value, root_bytes=None, *, contradiction=False):
        # Temporary ABI wire doubles exercise the trusted-producer contract. No real observation occurs.
        from webots_mcp.physical_gate import evaluate, CONDITIONS
        policy = dict(topology="open-rigid",pairs={"slide/base":"expected"},unit="mm",metric="travel",
                      convergence_absolute=.1,stability_limit=.2,metric_max=2)
        conditions = dict.fromkeys(CONDITIONS, "a"*64)
        conditions["thresholds"] = sha256(json.dumps(policy,sort_keys=True,separators=(",",":"),
            allow_nan=False).encode()).hexdigest()
        conditions["model"] = sha256(revision.encode()).hexdigest()
        refs, traces = [], []
        for run_id, step in ((run,32),(run+"-half",16)):
            self.make(run_id,revision,value)
            m,c,e,_ = rt.read_case(self.store,run_id)
            folder = self.store.root/"webots_runs"/run_id
            def raw(name,data,kind,origin):
                (folder/name).write_bytes(data)
                return self.store.file_evidence(run_id,name,kind=kind,provenance=origin)
            cad_root = raw("cad-root.bin",root_bytes if root_bytes is not None else self.root.read_bytes(),
                           "cad-pack-member","real-sw-export")
            cad_part = raw("cad-part.bin",self.part.read_bytes(),"cad-pack-member","real-sw-export")
            m["data"]["source_pack"] = {self.root.name:revision,self.part.name:digest(self.part)}
            model_index = raw("export.bin",revision.encode(),"exported-model","real-sw-export")
            pair = dict(distinct_bodies=True,geometry_calibrated=True,collision_enabled=True,
                        detector_verified=True,filtered=False,self_pair=False)
            coverage = dict(run_id=run_id,generation=1,world_sha256="0"*64,robot="bench",
                            pairs={"slide/base":pair | {"detector_verified":not contradiction}})
            cover = raw("coverage.json",_json_bytes(coverage),"collision-coverage","real-webots")
            trace = dict(provenance="real-webots",run_id=run_id,generation=1,conditions=conditions,seed=0,robot="bench",world_sha256="0"*64,
                step_ms=step,metric="travel",unit="mm",calibrated=True,feedback_verified=True,
                coverage={"slide/base":pair | {"evidence_sha256":cover["sha256"]}},
                samples=[dict(time_ms=i*step,contacts={"slide/base":True},value=value,stability_error=0)
                         for i in range(1,33//step+1)])
            idx = raw("trace.json",_json_bytes(trace),"physics-trace","real-webots")
            refs.append({"run_id":run_id,"sha256":idx["sha256"]})
            traces.append(trace | {"raw_evidence_sha256":idx["sha256"]})
            for doc in (m,c):
                doc["provenance"]["kind"] = "real-sw-export"
                doc["evidence"] = [model_index] if doc is c else [cad_root,cad_part]
                if doc is c:
                    doc["data"]["support"] = "supported"
                receipt = self.store.write_report(doc)
                e["data"]["model" if doc is m else "conversion"] = {
                    "path":doc["report_type"]+".json","sha256":receipt["sha256"]}
            e["provenance"]["kind"] = e["evidence"][0]["provenance"]["kind"] = "real-webots"
            e["evidence"].extend([cover,idx])
            self.store.write_report(e)
        e = self.store.read_report(run,"experiment_report")
        e["data"]["physics_pair"] = {"coarse":refs[0],"fine":refs[1],"policy":policy}
        e["data"]["verdict"] = evaluate(*traces,policy)
        self.store.write_report(e)

    def test_paired_trace_wire_contract(self):
        self.contract_pair("wire",digest(self.root),4)
        _,data,_ = rt.checked_experiment(self.store,"wire")
        self.assertEqual(data["verdict"]["verdict"],"FAIL")
        e = self.store.read_report("wire","experiment_report")
        e["data"]["verdict"]["verdict"] = "PASS"
        self.store.write_report(e)
        with self.assertRaisesRegex(ValueError,"PHYSICS_NOT_VALID"):
            rt.checked_experiment(self.store,"wire")
        (self.store.root/"webots_runs/wire-half/trace.json").write_text("changed")
        with self.assertRaisesRegex(ValueError,"hash/size"):
            rt.checked_experiment(self.store,"wire")

    def test_native_worker_observation_to_occurrence(self):
        self.contract_pair("native-wire",digest(self.root),4)
        run = "native-wire"
        m,c,e,_ = rt.read_case(self.store,run)
        folder = self.store.root/"webots_runs"/run
        binding = dict(id="travel",device="slider-sensor",robot="bench",configuration="Default",scale=1000,
                       targets=[{"kind":"urdf_link","id":"slide"}],unit="mm")
        (folder/"binding.json").write_bytes(_json_bytes(binding))
        index = self.store.file_evidence(run,"binding.json",kind="sensor-binding",provenance="real-sw-export")
        m["evidence"].append(index)
        m["data"]["measurement_bindings"] = {"travel":index["sha256"]}
        receipt = self.store.write_report(m)
        e["data"]["model"]["sha256"] = receipt["sha256"]
        raw = dict(run_id=run,generation=1,world_sha256="0"*64,robot="bench",sim_time=.064,
                   step=1,action="sim_sensor",operation_id="read2",targets=[],
                   model_sha256=c["evidence"][0]["sha256"],
                   measurement={"value":.004,"device":"slider-sensor","device_type":"PositionSensor"})
        (folder/"native.json").write_bytes(_json_bytes(raw))
        ri = self.store.file_evidence(run,"native.json",kind="observation",provenance="real-webots",
                    producer={"name":"webots_mcp.controller_worker","version":"A04","source_revision":None})
        e["evidence"].append(ri)
        e["data"]["observations"] = [worker_observation(self.store,run,e,raw,"travel",ri["sha256"])]
        self.store.write_report(e)
        result = attribute(self.store,run,"gap")
        self.assertEqual([c["id"] for c in result["candidate_components"]],["slide"])
        self.assertEqual((result["observation"]["value"],result["observation"]["step"]),(4,2))
        e["data"]["observations"][0]["value"] = 5
        self.store.write_report(e)
        self.assertIn("matched raw observation",attribute(self.store,run,"gap")["missing_information"])

    def test_contradictory_coverage_rejected(self):
        self.contract_pair("conflict",digest(self.root),4,contradiction=True)
        with self.assertRaisesRegex(ValueError,"COVERAGE_CONTENT_MISMATCH"):
            rt.checked_experiment(self.store,"conflict")

    def test_full_trial_with_wire_doubles(self):
        self.contract_pair("baseline",digest(self.root),4)
        self.policy["approved_reports"] = attribute(self.store,"baseline","gap")["report_sha256"]
        stages = []
        @contextmanager
        def lock(_):
            stages.append("locked")
            yield
            stages.append("released")
        @contextmanager
        def opened(path):
            member = SimpleNamespace(GetPathName=lambda:str(path.parent/self.part.name),GetSuppression2=lambda:2)
            yield SimpleNamespace(GetComponents=lambda recursive:[member],
                GetDependencies2=lambda *a:[self.part.name,member.GetPathName()])
        def edit(model,candidate,context,policy,path):
            path.write_bytes(b"fixture repaired version")
        prepared = {}
        def export(root,run):
            stages.append("export")
            self.contract_pair(run,digest(root),1,root.read_bytes())
            prepared.update(self.store.read_report(run,"experiment_report"))
            (self.store.root/"webots_runs"/run/"experiment_report.json").unlink()
        def replay(run,before):
            stages.append("retest")
            self.store.write_report(prepared)
        with patch.object(rt,"edit_distance",edit):
            journal = rt.trial(self.store,"baseline","gap",self.policy,source_dir=self.src,
                room_label=self.label,operation_id="case",candidate_run="corrected",open_owned=opened,
                export_candidate=export,retest=replay,lock=lock)
        self.assertEqual(journal["state"],"CANDIDATE_VERIFIED")
        self.assertEqual(stages,["locked","export","released","retest"])
        self.assertEqual(self.root.read_bytes(),b"original")

    def test_synthetic_gate(self):
        with self.assertRaisesRegex(ValueError,"REAL_SUPPORTED"):
            rt.checked_experiment(self.store,"before")

    def run_trial(self, *, value=1, step=.032, fail=False, drift=False):
        @contextmanager
        def lock(_):
            yield
        @contextmanager
        def opened(path):
            c = SimpleNamespace(GetPathName=lambda:str(path.parent/self.part.name),GetSuppression2=lambda:2)
            yield SimpleNamespace(GetComponents=lambda _: [c], GetDependencies2=lambda *a:[self.part.name,c.GetPathName()])
        def edit(_model,_c,_ctx,_policy,path):
            path.write_bytes(b"changed")
            if fail:
                raise ValueError("rebuild fixture")
        prepared = {}
        def export(path, candidate_run):
            if drift:
                self.root.write_bytes(b"user revision")
            self.make("after",digest(path),value,step=step)
            prepared.update(self.store.read_report(candidate_run,"experiment_report"))
            (self.store.root/"webots_runs"/candidate_run/"experiment_report.json").unlink()
        def check_export(store, run, revision):
            reports = [store.read_report(run,k) for k in ("model_manifest","conversion_report")]
            self.assertEqual(reports[0]["source_revision"],revision)
            return {r["report_type"]:sha256(_json_bytes(r)).hexdigest() for r in reports}
        def offline_only(store, run):
            m, _, e, hashes = rt.read_case(store,run)
            m["data"]["source_pack"] = self.policy["files"] | {self.root.name:m["source_revision"]}
            e["data"]["verified_physics_conditions"] = {"policy":{"metric":"travel"}}
            e["data"]["verdict"]["verdict"] = "FAIL" if run == "before" else "PASS"
            return m,e["data"],hashes
        # Exercise transaction orchestration separately from the real-only gate tested above.
        with patch.object(rt,"checked_experiment",offline_only), patch.object(rt,"edit_distance",edit), patch.object(rt,"exported_revision",check_export):
            return rt.trial(self.store,"before","gap",self.policy,source_dir=self.src,room_label=self.label,
                operation_id="one",candidate_run="after",open_owned=opened,export_candidate=export,
                retest=lambda *args: self.store.write_report(prepared),lock=lock)

    def test_candidate_and_replay(self):
        self.assertEqual(self.run_trial()["state"],"CANDIDATE_VERIFIED")
        self.assertEqual(self.root.read_bytes(),b"original")
        with self.assertRaisesRegex(ValueError,"FRESH_CANDIDATE"):

            self.run_trial()

    def test_rebuild_rollback(self):
        with self.assertRaisesRegex(ValueError,"rebuild fixture"):
            self.run_trial(fail=True)
        base = self.store.root/"webots_runs/before/repair-one"
        self.assertEqual((base/"candidate"/self.root.name).read_bytes(),b"original")
        self.assertEqual(json.loads((base/"trial.json").read_text())["state"],"ROLLED_BACK")

    def test_threshold_failure(self):
        with self.assertRaisesRegex(ValueError,"REGRESSION"):
            self.run_trial(value=3)

    def test_changed_step(self):
        with self.assertRaisesRegex(ValueError,"COMPARABLE"):
            self.run_trial(step=.016)

    def test_user_drift_survives(self):
        with self.assertRaisesRegex(ValueError,"SOURCE_DRIFT"):
            self.run_trial(drift=True)
        self.assertEqual(self.root.read_bytes(),b"user revision")


if __name__ == "__main__":
    unittest.main()
