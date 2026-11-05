"""One authorized trial. All writes target a newly created isolated assembly pack."""
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from shutil import copyfile, copyfileobj
import json
import math
import os
import tempfile

from .evidence import _atomic_write, _identifier, _json_bytes, _load_json, _no_reparse
from .repair import attribute, digest, raw_observation, read_case, require


@contextmanager
def sw_lock(room_label):
    import sw_bridge
    token = sw_bridge._gate_sw_lock(room_label)
    require(token.get("skipped") is False and token.get("room") == room_label
            and not token.get("busy") and not token.get("timeout"), "SW_EXCLUSIVE_LOCK_REQUIRED")
    try:
        yield
    finally:
        sw_bridge._gate_sw_unlock(token)


class UnsafeClose(RuntimeError):
    """The owned session might still be able to write the candidate."""


@contextmanager
def opened_pack(app, path):
    """Adapter for an explicitly authorized, dedicated, already-owned empty SW session."""
    require(app.GetDocumentCount() == 0, "DEDICATED_EMPTY_SESSION_REQUIRED")
    import pythoncom
    from win32com.client import VARIANT
    error = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    warning = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    try:
        model = app.OpenDoc6(str(path), 2, 1, "", error, warning)
        require(model is not None and error.value == warning.value == 0 and
                Path(model.GetPathName()).resolve() == path.resolve(), "PACK_OPEN_FAILED")
        yield model
    finally:
        try:
            doc = app.GetOpenDocumentByName(str(path))
            if doc is not None and Path(doc.GetPathName()).resolve() == path.resolve():
                app.CloseDoc(doc.GetTitle())
            require(app.GetDocumentCount() == 0, "DOCUMENTS_STILL_OPEN")
        except Exception as error:
            raise UnsafeClose("OWNED_SESSION_CLOSE_UNCONFIRMED") from error


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def distance_dimension(model, candidate, context, policy, path, resolve=None):
    """Resolve the feature and occurrence afresh. Never retain a native object across rebuild."""
    from .assembly import resolve_reference, resolved_component
    actual = Path(model.GetPathName()).resolve()
    require(actual == path.resolve() and
            model.ConfigurationManager.ActiveConfiguration.Name == context["configuration"],
            "DIRTY_OR_WRONG_DOCUMENT")
    native = resolve_reference(model.Extension, candidate["persistent_reference"], invoke=resolve)
    require(native.GetSuppression2() in (2, 3) and native.Name2 == candidate["instance_path"]
            and Path(native.GetPathName()).resolve() == (path.parent / Path(candidate["source_file"]).name).resolve(),
            "WRONG_OR_SUPPRESSED_OCCURRENCE")
    resolved_component(candidate, resolution=dict(method="IModelDocExtension.GetObjectByPersistReference3",
        status="resolved", object_type="IComponent2", snapshot=context, component_id=candidate["id"],
        owner_document=candidate["owner_document"], instance_path=native.Name2,
        configuration=native.ReferencedConfiguration, persistent_reference=candidate["persistent_reference"]),
        snapshot=context, current=context)
    feature = resolve_reference(model.Extension, policy["mate_reference"], invoke=resolve)
    mate = feature.GetSpecificFeature2()
    require(feature.IsSuppressed2(1, None) in ([False], (False,)) and mate.Type == 5 and
            mate.GetMateEntityCount() == 2, "ACTIVE_DISTANCE_MATE_REQUIRED")
    entities = [mate.MateEntity(i).ReferenceComponent for i in range(2)]
    require(any(c is not None and c.Name2 == native.Name2 and c.ReferencedConfiguration == native.ReferencedConfiguration
                and c.GetPathName() == native.GetPathName() for c in entities), "MATE_COMPONENT_MISMATCH")
    display = feature.GetFirstDisplayDimension()
    dim = display.GetDimension2(0)
    require(dim.FullName == policy["dimension"] and dim.ReadOnly is False, "DIMENSION_NOT_WRITABLE")
    return dim


def checked_distance(dim, expected):
    values = dim.GetSystemValue3(1, None)
    require(isinstance(values, (list, tuple)) and len(values) == 1 and number(values[0]) and
            math.isclose(values[0], expected, rel_tol=0, abs_tol=1e-12), "DIMENSION_DRIFT")


def edit_distance(model, candidate, context, policy, path, *, resolve=None, save=None):
    """Use only an already opened isolated document. Helpers may be injected for native ABI tests."""
    require(model.GetSaveFlag() is False, "DIRTY_DOCUMENT")
    dim = distance_dimension(model,candidate,context,policy,path,resolve)
    checked_distance(dim,policy["old_m"])
    status = dim.SetSystemValue3(policy["new_m"], 1, None)
    require(type(status) is int and status == 0 and model.ForceRebuild3(False) is True and
            model.Extension.GetWhatsWrongCount() == 0, "SET_OR_REBUILD_FAILED")
    checked_distance(distance_dimension(model,candidate,context,policy,path,resolve),policy["new_m"])
    if save is None:
        import pythoncom
        from win32com.client import VARIANT
        error = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        warning = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        saved = model.Save3(1, error, warning)
        outcome = (saved, error.value, warning.value)
    else:
        outcome = save(model)
    require(outcome == (True, 0, 0) and not model.GetSaveFlag(), "SAVE_NOT_CLEAN")


def verified_physics(store, run, conversion, experiment):
    from .physical_gate import evaluate
    data = experiment["data"]
    pair = data["physics_pair"]
    policy = pair["policy"]
    traces = []
    for field in ("coarse","fine"):
        ref = pair[field]
        trace_run = ref["run_id"]
        report = experiment if trace_run == run else store.read_report(trace_run,"experiment_report")
        require(report["source_revision"] == experiment["source_revision"] and
                report["provenance"]["kind"] == "real-webots", "PAIR_OWNER_OR_REVISION_MISMATCH")
        raw = [e for e in report["evidence"] if e["kind"] == "physics-trace" and
               e["sha256"] == ref["sha256"] and e["provenance"]["kind"] == "real-webots"]
        require(len(raw) == 1, "PAIR_RAW_TRACE_REQUIRED")
        trace = _load_json(store._path(f"webots_runs/{trace_run}/{raw[0]['path']}"))
        require(trace.get("run_id") == trace_run and "raw_evidence_sha256" not in trace,
                "PAIR_TRACE_BINDING_MISMATCH")
        trace["raw_evidence_sha256"] = ref["sha256"]
        coverage = {e["sha256"]:e["path"] for e in report["evidence"] if e["kind"] == "collision-coverage"
                    and e["provenance"]["kind"] == "real-webots"}
        for pair_name, claimed in trace["coverage"].items():
            path = coverage.get(claimed["evidence_sha256"])
            require(path, "UNINDEXED_COLLISION_COVERAGE")
            attachment = _load_json(store._path(f"webots_runs/{trace_run}/{path}"))
            require(all(_json_bytes(attachment.get(k)) == _json_bytes(trace[k])
                        for k in ("run_id","generation","world_sha256","robot"))
                    and json.dumps(attachment.get("pairs",{}).get(pair_name),sort_keys=True) ==
                    json.dumps({k:v for k,v in claimed.items() if k != "evidence_sha256"},sort_keys=True),
                    "COLLISION_COVERAGE_CONTENT_MISMATCH")
        traces.append(trace)
    coarse, fine = traces
    require(coarse["run_id"] == run and any(e["sha256"] == coarse["conditions"]["model"] and
            e["kind"] == "exported-model" for e in conversion["evidence"]), "EXPORTED_MODEL_BINDING_REQUIRED")
    gate = evaluate(coarse,fine,policy)
    require(gate == data["verdict"] and gate.get("physical_valid") is True and
            gate.get("provenance") == "real-webots" and gate.get("verdict") in ("PASS","FAIL"),
            "PHYSICS_NOT_VALID")
    metric = policy["metric"]
    criteria = [m for m in data["parameters"]["thresholds"] if m["id"] == metric]
    rows = [o for o in data["observations"] if o["id"] == metric]
    require(len(rows) == 1 and type(rows[0].get("step")) is int and 1 <= rows[0]["step"] <= len(coarse["samples"]),
            "OBSERVATION_STEP_MISMATCH")
    if "sensor_binding_sha256" in rows[0]:
        require(rows[0]["sensor_binding_sha256"] == coarse["conditions"]["sensor_binding"],
                "PHYSICS_SENSOR_BINDING_MISMATCH")
    sample = coarse["samples"][rows[0]["step"]-1]
    parameters = data["parameters"]
    require(type(parameters["seed"]) is int and parameters["seed"] == coarse["seed"] and
            number(parameters["time_step"]) and math.isclose(parameters["time_step"]*1000,coarse["step_ms"],
            rel_tol=0,abs_tol=1e-9) and number(rows[0]["sim_time"]) and
            math.isclose(rows[0]["sim_time"]*1000,sample["time_ms"],rel_tol=0,abs_tol=1e-6) and
            rows[0]["value"] == sample["value"], "OBSERVATION_CLOCK_OR_VALUE_MISMATCH")
    require(len(criteria) == len(rows) == 1 and rows[0]["unit"] == criteria[0]["unit"] == policy["unit"] and
            rows[0]["generation"] == coarse["generation"] and rows[0]["robot"] == coarse["robot"] and
            rows[0]["world_sha256"] == coarse["world_sha256"] and rows[0]["value"] ==
            max(row["value"] for trace in traces for row in trace["samples"]) and
            criteria[0]["direction"] == "max" and criteria[0]["limit"] == policy["metric_max"],
            "PHYSICS_METRIC_BINDING_MISMATCH")
    return {"policy":policy,"seed":coarse["seed"],"step_ms":coarse["step_ms"],
            "conditions":{k:v for k,v in coarse["conditions"].items() if k != "model"}}


def checked_experiment(store, run):
    m, c, e, hashes = read_case(store, run)
    require([r["provenance"]["kind"] for r in (m,c,e)] == ["real-sw-export","real-sw-export","real-webots"]
            and all(r["status"] == "supported" and not r["missing_information"] and r["evidence"] for r in (m,c,e)),
            "REAL_SUPPORTED_EVIDENCE_REQUIRED")
    require(exported_revision(store,run,m["source_revision"]) ==
            {k:hashes[k] for k in ("model_manifest","conversion_report")}, "EXPORT_REPORT_DRIFT")
    data = e["data"]
    link = data.get("conversion") or {}
    require(link == {"path":"conversion_report.json","sha256":hashes["conversion_report"]},
            "CONVERSION_LINK_MISMATCH")
    data["verified_physics_conditions"] = verified_physics(store,run,c,e)
    params = data["parameters"]
    for key in ("seed","initial_state","time_step","thresholds","environment","budget"):
        require(params.get(key) is not None, "COMPARISON_PARAMETER_MISSING:" + key)
    require(data["scene"] and data["actions"] and params["thresholds"], "COMPARISON_CASE_MISSING")
    for obs in data["observations"]:
        require(raw_observation(store, run, e, obs) and obs.get("robot") and
                isinstance(obs.get("world_sha256"), str) and len(obs["world_sha256"]) == 64 and
                type(obs.get("generation")) is int and number(obs.get("sim_time")) and
                type(obs.get("step")) is int, "UNVERIFIED_METRIC_OBSERVATION")
    return m, data, hashes


def scores(data):
    result = {}
    for metric in data["parameters"]["thresholds"]:
        name, unit, limit, direction = (metric[k] for k in ("id","unit","limit","direction"))
        require(name not in result and unit and number(limit) and direction in ("max","min"), "INVALID_CRITERION")
        rows = [o for o in data["observations"] if o.get("id") == name]
        require(len(rows) == 1 and rows[0].get("unit") == unit and number(rows[0].get("value")),
                "MISSING_REGRESSION_METRIC:" + name)
        result[name] = (rows[0]["value"] - limit) * (1 if direction == "max" else -1)
    return result


def restore_member(snapshot, destination):
    """Replace only a staged file and avoid buffering a CAD document in memory."""
    _no_reparse(destination.parent)
    _no_reparse(destination)
    descriptor, name = tempfile.mkstemp(dir=destination.parent)
    try:
        with os.fdopen(descriptor,"wb") as sink, snapshot.open("rb") as source:
            copyfileobj(source,sink)
            sink.flush()
            os.fsync(sink.fileno())
        os.replace(name,destination)
    finally:
        Path(name).unlink(missing_ok=True)


def exported_revision(store, run, revision):
    reports = [store.read_report(run, name) for name in ("model_manifest","conversion_report")]
    for report in reports:
        require(report["source_revision"] == revision and report["provenance"]["kind"] == "real-sw-export"
                and report["status"] == "supported" and not report["missing_information"] and report["evidence"],
                "CANDIDATE_EXPORT_UNVERIFIED")
    manifest, conversion = reports
    pack = manifest["data"].get("source_pack") or {}
    snapshots = {e["sha256"] for e in manifest["evidence"] if e["kind"] == "cad-pack-member"
                 and e["provenance"]["kind"] == "real-sw-export"}
    require(revision in snapshots and pack and set(pack.values()) <= snapshots, "CAD_SNAPSHOT_MISSING")
    require(not conversion["data"].get("omissions") and not conversion["data"].get("missing_information")
            and conversion["data"].get("support") == "supported" and any(
                e["kind"] == "exported-model" and e["provenance"]["kind"] == "real-sw-export"
                for e in conversion["evidence"]), "CANDIDATE_MODEL_UNVERIFIED")
    return {r["report_type"]:sha256(_json_bytes(r)).hexdigest() for r in reports}


def trial(store, run, issue_id, policy, *, source_dir, room_label, operation_id, candidate_run,
          open_owned, export_candidate, retest, lock=sw_lock):
    """Host-supplied policy and callbacks are trusted capabilities, never model-generated tool arguments.
    open_owned(root) must open only this detached pack and close it before returning.
    export_candidate(root, candidate_run) publishes the A01 reports under SW lock.
    retest(candidate_run, baseline_run) publishes the A04 experiment using the fixed case, with no SW lock.
    """
    _identifier(operation_id)
    _identifier(issue_id)
    _identifier(candidate_run)
    require(candidate_run != run and not store._path(f"webots_runs/{candidate_run}").exists(),
            "FRESH_CANDIDATE_RUN_REQUIRED")
    require(isinstance(room_label, str) and room_label.strip() and store.room_id ==
            "room-" + sha256(room_label.encode("utf-8")).hexdigest(), "SW_ROOM_OWNER_MISMATCH")
    before, scenario, fingerprints = checked_experiment(store, run)
    attribution = attribute(store, run, issue_id)
    require(attribution["evidence_level"] == "derived" and not attribution["missing_information"] and
            attribution["report_sha256"] == fingerprints == policy["approved_reports"], "ATTRIBUTION_OR_APPROVAL_CHANGED")
    candidate, = attribution["candidate_components"]
    require(candidate["id"] == policy["component_id"] and policy["kind"] == "distance_mate"
            and policy["reason"].strip() and policy["expected_improvement"].strip(), "UNAUTHORIZED_TARGET")
    old, new, low, high = (policy[k] for k in ("old_m","new_m","min_m","max_m"))
    require(all(number(v) for v in (old,new,low,high)) and 0 <= low <= new <= high and old != new,
            "PARAMETER_OUT_OF_RANGE")
    metric = attribution["observation"]["id"]
    require(scenario["verdict"].get("verdict") == "FAIL" and
            metric == scenario["verified_physics_conditions"]["policy"]["metric"], "BASELINE_PHYSICAL_FAILURE_REQUIRED")
    previous = scores(scenario)
    require(previous.get(metric, 0) > 0, "NO_BASELINE_VIOLATION")
    pack = policy["files"]
    root_name = policy["root"]
    source_dir = Path(source_dir).resolve()
    require(pack == before["data"].get("source_pack") and root_name in pack and root_name.lower().endswith(".sldasm") and
            Path(candidate["owner_document"]).resolve() == source_dir / root_name and
            candidate["source_file"] and Path(candidate["source_file"]).resolve().parent == source_dir and
            Path(candidate["source_file"]).name in pack and pack[root_name] == before["source_revision"],
            "PACK_OR_OWNER_MISMATCH")
    for name in pack:
        require(Path(name).name == name and name.lower().endswith((".sldasm",".sldprt")) and ":" not in name,
                "FLAT_CAD_PACK_REQUIRED")
    base = store._path(f"webots_runs/{run}/repair-{operation_id}")
    require(not base.is_relative_to(source_dir) and not source_dir.is_relative_to(base), "ISOLATION_REQUIRED")
    claim = store._path(f"webots_runs/{run}/repair-claim-{issue_id}.json")
    with claim.open("xb") as stream:
        stream.write(_json_bytes({"operation_id":operation_id,"baseline":fingerprints}))
    base.mkdir()  # One trial per baseline issue, even if a client supplies a new operation ID.
    original, work = base/"snapshot", base/"candidate"
    original.mkdir()
    work.mkdir()
    journal = dict(operation_id=operation_id, baseline_run=run, reports=fingerprints, policy=policy,
                   candidate_component=candidate, original_revision=before["source_revision"])
    def record(state, **fields):
        journal.update(state=state, **fields)
        _atomic_write(base/"trial.json", _json_bytes(journal))
    def verify_sources():
        for name, expected in pack.items():
            src = source_dir / name
            _no_reparse(src)
            require(digest(src) == expected, "SOURCE_DRIFT:" + name)
    record("PREPARING")
    verify_sources()
    for name, expected in pack.items():
        copyfile(source_dir/name, original/name)
        require(digest(original/name) == expected, "SNAPSHOT_DRIFT")
        copyfile(original/name, work/name)
    root = work/root_name
    try:
        with lock(room_label):
            verify_sources()
            require(read_case(store,run)[3] == fingerprints, "BASELINE_DRIFT")
            require(all(digest(work/name) == expected for name,expected in pack.items()), "STAGED_PACK_DRIFT")
            record("EDIT_DISPATCHED_OUTCOME_UNKNOWN")
            with open_owned(root) as model:
                dependencies = model.GetDependencies2(True,True,False)
                expected = {work/name for name in pack if name != root_name}
                require(isinstance(dependencies,(list,tuple)) and len(dependencies) % 2 == 0 and
                        {Path(p).resolve() for p in dependencies[1::2]} == expected,
                        "EXTERNAL_OR_MISSING_DOCUMENT_REFERENCE")
                members = model.GetComponents(False)
                require(isinstance(members,(list,tuple)) and members and all(
                    c.GetSuppression2() in (2,3) and Path(c.GetPathName()).resolve() in expected
                    for c in members), "EXTERNAL_ASSEMBLY_DEPENDENCY")
                edit_distance(model, candidate, attribution["context"], policy, root)
            require(all(digest(work/name) == expected for name,expected in pack.items() if name != root_name),
                    "UNAUTHORIZED_MEMBER_CHANGE")
            revised = digest(root)
            require(revised != pack[root_name], "NO_SAVED_REVISION")
            record("REBUILT", revised=revised, candidate_run=candidate_run)
            export_candidate(root, candidate_run)
            export_hashes = exported_revision(store,candidate_run,revised)
            require(not store._path(f"webots_runs/{candidate_run}/experiment_report.json").exists(),
                    "PREEXISTING_RETEST_REPORT")
            record("EXPORTED", export_reports=export_hashes)
        verify_sources()
        record("RETEST_DISPATCHED_OUTCOME_UNKNOWN")
        retest(candidate_run,run)
        after, replay, new_hashes = checked_experiment(store, candidate_run)
        require(all(new_hashes[k] == v for k,v in export_hashes.items()), "EXPORT_CHANGED_DURING_RETEST")
        require(after["data"].get("source_pack") == pack | {root_name:revised}, "RETEST_CAD_DEPENDENCY_DRIFT")
        require(after["source_revision"] == revised and all(_json_bytes(replay[k]) == _json_bytes(scenario[k]) for k in
                ("scene","parameters","actions","verified_physics_conditions")), "RETEST_NOT_COMPARABLE")
        require(replay["verdict"].get("verdict") == "PASS", "RETEST_PHYSICAL_FAILURE")
        require(not {i["issue_id"] for i in replay["issues"] if i.get("severity") in ("error","critical")}
                - {i["issue_id"] for i in scenario["issues"] if i.get("severity") in ("error","critical")},
                "NEW_SEVERE_ISSUE")
        require(read_case(store,run)[3] == fingerprints, "BASELINE_DRIFT")
        current = scores(replay)
        require(current[metric] <= 0 and all(current[k] <= previous[k] for k in previous),
                "NO_IMPROVEMENT_OR_REGRESSION")
        require(digest(root) == revised, "CANDIDATE_DRIFT")
        verify_sources()
        record("CANDIDATE_VERIFIED", retest_run=candidate_run, retest_reports=new_hashes,
               baseline_scores=previous, retest_scores=current)
    except UnsafeClose as error:
        record("OUTCOME_UNKNOWN", error=str(error))
        raise
    except Exception as error:
        try:
            record("ROLLBACK_PENDING", error=str(error))
        finally:
            for name, expected in pack.items():
                _no_reparse(base)
                _no_reparse(original)
                _no_reparse(original/name)
                require(digest(original/name) == expected, "SNAPSHOT_CORRUPTED")
                restore_member(original/name,work/name)
                require(digest(work/name) == expected, "ROLLBACK_FAILED")
        record("ROLLED_BACK", restored=pack)
        raise
    return journal
