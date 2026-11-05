"""Evidence-bound attribution and one isolated assembly distance-mate trial."""
import argparse
from hashlib import sha256
import json
import math
from pathlib import Path

from .evidence import EvidenceStore, _json_bytes, _load_json


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def digest(path):
    with path.open("rb") as stream:
        from hashlib import file_digest
        return file_digest(stream, "sha256").hexdigest()


def read_case(store, run):
    reports = [store.read_report(run, kind) for kind in
               ("model_manifest", "conversion_report", "experiment_report")]
    manifest, conversion, experiment = reports
    revisions = {report["source_revision"] for report in reports}
    require(len(revisions) == 1, "REPORT_REVISION_MISMATCH")
    fingerprints = {r["report_type"]: sha256(_json_bytes(r)).hexdigest() for r in reports}
    link = experiment["data"]["model"] or {}
    require(link.get("path") == "model_manifest.json" and
            link.get("sha256") == fingerprints["model_manifest"], "MODEL_LINK_MISMATCH")
    return manifest, conversion, experiment, fingerprints


def worker_observation(store, run, experiment, raw, metric, raw_sha256):
    """Normalize an unmodified A04 PositionSensor receipt using the pinned CAD measurement binding."""
    m = store.read_report(run,"model_manifest")
    c = store.read_report(run,"conversion_report")
    require(raw["run_id"] == run and raw["action"] == "sim_sensor" and
            raw["measurement"]["device_type"] == "PositionSensor" and
            any(e["sha256"] == raw["model_sha256"] and e["kind"] == "exported-model" for e in c["evidence"]),
            "SENSOR_OR_EXPORTED_MODEL_MISMATCH")
    refs = m["data"]["measurement_bindings"]
    entries = [e for e in m["evidence"] if e["kind"] == "sensor-binding" and e["sha256"] == refs[metric]
               and e["provenance"]["kind"] == m["provenance"]["kind"]]
    require(len(entries) == 1, "MISSING_CALIBRATED_SENSOR_BINDING")
    bind = _load_json(store._path(f"webots_runs/{run}/{entries[0]['path']}"))
    require(bind["id"] == metric and bind["device"] == raw["measurement"]["device"] and
            bind["robot"] == raw["robot"] and
            bind["targets"] and bind["unit"] and bind["configuration"], "SENSOR_BINDING_MISMATCH")
    value, scale = raw["measurement"]["value"], bind["scale"]
    require(all(type(n) in (float,int) and math.isfinite(n) for n in (value,scale)) and
            scale > 0 and math.isfinite(value*scale), "NONFINITE_SENSOR_MEASUREMENT")
    sim_time, step_size = raw["sim_time"], experiment["data"]["parameters"]["time_step"]
    require(all(type(n) in (float,int) and math.isfinite(n) for n in (sim_time,step_size))
            and step_size > 0, "INVALID_SENSOR_CLOCK")
    t = sim_time / step_size
    require(math.isfinite(t) and t >= 0 and math.isclose(t,round(t),abs_tol=1e-7,rel_tol=0),
            "NONINTEGRAL_SENSOR_STEP")
    return {**{k:raw[k] for k in ("run_id","generation","world_sha256","robot","sim_time","action","operation_id")},
            "id":metric, "unit":bind["unit"], "value":value*scale, "configuration":bind["configuration"],
            "step":round(t), "operation_steps":raw["step"], "targets":bind["targets"],
            "sensor_binding_sha256":entries[0]["sha256"], "raw_sha256":raw_sha256}


def raw_observation(store, run, experiment, obs):
    index = [e for e in experiment["evidence"] if e["sha256"] == obs.get("raw_sha256")
             and e["kind"] == "observation" and e["provenance"]["kind"] == experiment["provenance"]["kind"]]
    if len(index) != 1 or obs.get("run_id") != run:
        return False
    raw = _load_json(store._path(f"webots_runs/{run}/{index[0]['path']}"))
    if index[0]["producer"].get("name") == "webots_mcp.controller_worker":
        return worker_observation(store,run,experiment,raw,obs["id"],index[0]["sha256"]) == obs
    return raw == {k:v for k,v in obs.items() if k != "raw_sha256"}


def attribute(store, run, issue_id):
    manifest, conversion, experiment, hashes = read_case(store, run)
    issues = [i for i in experiment["data"]["issues"] if i["issue_id"] == issue_id]
    require(len(issues) == 1, "ISSUE_NOT_UNIQUE")
    issue = issues[0]
    observations = [o for o in experiment["data"]["observations"]
                    if o.get("id") == issue.get("observation_id") and o.get("id")]
    result = dict(run_id=run, issue=issue, source_revision=manifest["source_revision"],
                  report_sha256=hashes, candidate_components=[], observation=None, root_cause=None,
                  evidence_level="suspected", missing_information=[], next_check="capture indexed observation and map every target")
    if len(observations) != 1:
        result["candidate_components"] = issue.get("candidate_components", [])
        result["missing_information"].append("unique indexed observation")
        return result
    obs = observations[0]
    result["observation"] = obs
    if not raw_observation(store, run, experiment, obs):
        result["missing_information"].append("matched raw observation")
        return result
    result["evidence_level"] = "observed"
    from .assembly import map_candidates
    context = dict(source_revision=manifest["source_revision"], **{
        k: obs.get(k) for k in ("configuration", "generation")})
    result["context"] = context
    components = manifest["data"]["components"]
    targets = obs.get("targets")
    require(isinstance(targets, list) and targets, "OBSERVATION_TARGETS_MISSING")
    found = {}
    for target in targets:
        candidates = map_candidates(components, conversion["data"]["mappings"], target=target,
                                    snapshot=context, current=context)
        if not candidates:
            result["missing_information"].append("unmapped target: " + str(target))
        for candidate in candidates:
            found[candidate["id"]] = candidate
            if candidate.get("snapshot_context") != context:
                result["missing_information"].append("stale occurrence snapshot")
    result["candidate_components"] = list(found.values())
    if len(found) != 1:
        result["missing_information"].append("unique affected occurrence")
    if found and not result["missing_information"]:
        result.update(evidence_level="derived", next_check="verify a native distance-mate measurement on the isolated source")
    return result


def main():
    parser = argparse.ArgumentParser(description="Inspect one owned simulation issue (read only)")
    for field in ("state_dir", "task_id", "room_id", "run_id", "issue_id"):
        parser.add_argument(field)
    a = parser.parse_args()
    print(json.dumps(attribute(EvidenceStore(Path(a.state_dir).absolute(), task_id=a.task_id, room_id=a.room_id),
                               a.run_id, a.issue_id), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
