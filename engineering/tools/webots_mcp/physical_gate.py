"""Judge a bounded rigid-body pair experiment from two complete observation traces."""
import math
import re


def number(x):
    if type(x) not in (float,int) or not math.isfinite(x):
        raise ValueError("Expected finite measurement")
    return x


def digest(x):
    if not isinstance(x,str) or not re.fullmatch(r"[a-f0-9]{64}",x):
        raise ValueError("Missing SHA256 evidence binding")
    return x


CONDITIONS = ("model","scene_except_step","actions","initial_state","materials","thresholds",
              "coverage","calibration","sensor_binding")


def sample_from_observations(store,run,receipts,*,sensor,unit,pairs):
    """Decode one aligned SI position sample and exact-node contacts from owned snapshots.

    Stability metric in this supported recipe is peak monitored body angular speed (rad/s).
    Physical calibration/coverage is evaluated separately; empty contacts alone cannot pass.
    """
    import hashlib
    import json
    if not pairs or len(pairs)>64 or len(receipts)>129:
        raise ValueError("Bounded explicit collision pairs required")
    bodies = {}
    measurement = None
    scope = None
    provenance = None
    for ref in receipts:
        origin = ref["provenance"]["kind"]
        if ref["kind"] != "observation" or origin not in ("real-webots","synthetic"):
            raise ValueError("Wrong observation source")
        actual = store.file_evidence(run,ref["path"],kind=ref["kind"],provenance=ref["provenance"],
                                     producer=ref["producer"])
        if actual != ref or actual["size_bytes"] > 65536:
            raise ValueError("Observation index mismatch")
        with store._path(f"webots_runs/{run}/{ref['path']}").open("rb") as file:
            raw = file.read(65537)
        if hashlib.sha256(raw).hexdigest()!=ref["sha256"]:
            raise ValueError("Observation changed")
        record = json.loads(raw)
        identity = tuple(record[k] for k in ("run_id","generation","robot","world_sha256","model_sha256",
                                             "step","sim_time"))
        if record["run_id"] != run or (scope is not None and (scope!=identity or provenance!=origin)):
            raise ValueError("Observations from different physics instants")
        scope,provenance = identity,origin
        m = record["measurement"]
        if record["action"]=="sim_sensor" and m["device"]==sensor and m["unit"]==unit and unit in ("m","rad"):
            if measurement is not None or m["device_type"]!="PositionSensor":
                raise ValueError("Ambiguous sensor binding")
            measurement = number(m["value"])
        elif record["action"]=="sim_contacts" and m["include_descendants"] is False and m["boundingObject"]:
            if m["node_def"] in bodies:
                raise ValueError("Duplicate body snapshot")
            bodies[m["node_def"]]=m
        else:
            raise ValueError("Unexpected observation for recipe")
    if measurement is None or set(bodies)!={body for pair in pairs.values() for body in pair}:
        raise ValueError("Missing sensor or required body")
    contacts = {}
    max_speed = 0.
    for body in bodies.values():
        velocity = body["velocity_world"]
        if len(velocity)!=6:
            raise ValueError("Missing rigid-body velocity")
        max_speed = max(max_speed,math.hypot(*(number(v) for v in velocity[3:])))
    for label,(a,b) in pairs.items():
        left,right=bodies[a],bodies[b]
        if left["node_id"]==right["node_id"]:
            raise ValueError("Pair resolves to the same body")
        direct = any(p["node_id"]==right["node_id"] for p in left["contacts"])
        reverse = any(p["node_id"]==left["node_id"] for p in right["contacts"])
        if direct != reverse:
            raise ValueError("Contact endpoints disagree")
        contacts[label]=direct
    ms=number(scope[-1])*1000
    if not math.isclose(ms,round(ms),rel_tol=0,abs_tol=1e-6):
        raise ValueError("Nonintegral simulation clock")
    return dict(time_ms=round(ms),contacts=contacts,value=measurement,stability_error=max_speed)


def evaluate(coarse,fine,policy):
    """Synthetic traces exercise identical decisions but cannot pass a physical gate."""
    result = dict(verdict="UNKNOWN",physical_valid=False,provenance="synthetic",reasons=[])
    try:
        real = all(trace["provenance"] == "real-webots" for trace in (coarse,fine))
        if real:
            result["provenance"] = "real-webots"
        elif any(trace["provenance"] != "synthetic" for trace in (coarse,fine)):
            raise ValueError("Mixed or unsupported provenance")
        if coarse["run_id"] == fine["run_id"] or not coarse["run_id"] or not fine["run_id"]:
            raise ValueError("Two distinct runs required")
        if not isinstance(coarse["robot"],str) or not coarse["robot"] or coarse["robot"] != fine["robot"]:
            raise ValueError("Different or missing robot identity")
        for trace in (coarse,fine):
            digest(trace["world_sha256"])
        for key in CONDITIONS:
            if digest(coarse["conditions"][key]) != digest(fine["conditions"][key]):
                raise ValueError("Different condition: "+key)
        if coarse["seed"] != fine["seed"] or any(type(t["seed"]) is not int for t in (coarse,fine)):
            raise ValueError("Incomparable seed")
        dc,df = coarse["step_ms"],fine["step_ms"]
        if type(dc) is not int or type(df) is not int or not 0 < df <= 1000 or dc != 2*df:
            raise ValueError("Expected step-halving comparison")
        if policy["topology"] != "open-rigid":
            raise ValueError("Closed-loop/flexible/global constraint validity unsupported")
        pairs = policy["pairs"]
        if not isinstance(pairs,dict) or not 0 < len(pairs)<=64 or not policy["unit"] or not policy["metric"]:
            raise ValueError("Explicit pairs, measurement and unit required")
        tolerance = number(policy["convergence_absolute"])
        stable_limit = number(policy["stability_limit"])
        if min(tolerance,stable_limit) < 0:
            raise ValueError("Negative tolerance")
        threshold = number(policy["metric_max"])
        from hashlib import sha256
        import json
        policy_hash = sha256(json.dumps(policy,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()
        if coarse["conditions"]["thresholds"] != policy_hash:
            raise ValueError("Policy differs from frozen threshold hash")
        samples = []
        hits = []
        contact_times = []
        for trace in (coarse,fine):
            digest(trace["raw_evidence_sha256"])
            if trace["generation"] != 1 or type(trace["generation"]) is not int:
                raise ValueError("Invalid generation")
            if trace["calibrated"] is not True or trace["feedback_verified"] is not True:
                raise ValueError("Unverified model or actuator feedback")
            if trace["metric"] != policy["metric"] or trace["unit"] != policy["unit"]:
                raise ValueError("Metric or unit mismatch")
            if set(trace["coverage"]) != set(pairs):
                raise ValueError("Missing required collision pair")
            for pair, expectation in pairs.items():
                c = trace["coverage"][pair]
                if (expectation not in ("expected","forbidden") or c["distinct_bodies"] is not True
                        or c["geometry_calibrated"] is not True or c["collision_enabled"] is not True
                        or c["detector_verified"] is not True or c["filtered"] is not False
                        or type(c["self_pair"]) is not bool
                        or (c["self_pair"] and c["self_collision_enabled"] is not True)):
                    raise ValueError("Uncovered collision pair: "+pair)
                digest(c["evidence_sha256"])
            rows = trace["samples"]
            if not 1 <= len(rows) <= 10000:
                raise ValueError("Unsupported trace size")
            values,h = [],set()
            events = {k:[] for k in pairs}
            for i,row in enumerate(rows,1):
                if type(row["time_ms"]) is not int or row["time_ms"] != i*trace["step_ms"] or set(row["contacts"]) != set(pairs):
                    raise ValueError("Missing observation or inconsistent sample time")
                if any(type(v) is not bool for v in row["contacts"].values()):
                    raise ValueError("Unknown contact sample")
                h.update(k for k,v in row["contacts"].items() if v)
                for pair, present in row["contacts"].items():
                    if present:
                        events[pair].append(row["time_ms"])
                values.append(number(row["value"]))
                if not 0 <= number(row["stability_error"]) <= stable_limit:
                    raise ValueError("Instability exceeds calibrated bound")
            samples.append(values)
            hits.append(h)
            contact_times.append(events)
        if len(samples[1]) != 2*len(samples[0]):
            raise ValueError("Different duration")
        if hits[0] != hits[1] or any(abs(x-y)>tolerance for x,y in zip(samples[0],samples[1][1::2])):
            raise ValueError("Contact classification or metric failed convergence")
        for pair in hits[0]:
            left,right = (events[pair] for events in contact_times)
            if set(left) != {((t+dc-1)//dc)*dc for t in right}:
                raise ValueError("Contact interval occupancy failed convergence: "+pair)
        if (max(samples[0]) > threshold) != (max(samples[1]) > threshold):
            raise ValueError("Threshold classification failed convergence")
        violations = [pair for pair,expected in pairs.items() if (pair in hits[0]) != (expected=="expected")]
        if max(max(v) for v in samples) > threshold:
            violations.append("metric_max")
        decision = "FAIL" if violations else "PASS"
        result.update(criteria_result=decision,physical_valid=real,verdict=decision if real else "UNKNOWN",
                      run_ids=[trace["run_id"] for trace in (coarse,fine)], raw_sha256=[trace["raw_evidence_sha256"] for trace in (coarse,fine)], reasons=violations if real else ["Synthetic trace"]+violations)
    except (KeyError,TypeError,ValueError,OverflowError) as err:
        result["reasons"] = [str(err)]
    return result
