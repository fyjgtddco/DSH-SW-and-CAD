"""Offline A01 single-part preview. Native CAD and physical gates stay closed."""
import argparse
import json
from importlib.metadata import version
from pathlib import Path
import re
import subprocess
import sys

from .evidence import EvidenceStore, _identifier, _load_json
from .sw_export import part_bundle

CONVERTER = "2025.0.0"
PRODUCER = {"name": "webots_mcp.pipeline", "version": "1", "source_revision": None}


def native_gate():
    return {"ok": False, "error_code": "CALIBRATION_REQUIRED", "ready_for_physics": False}


def publish_part(store, run, snapshot, *, provenance="static-derived"):
    if provenance not in {"static-derived", "fixture"} or snapshot["physics"]["frame"] != "part-local":
        raise ValueError("Only uncalibrated part-local previews are supported")
    bundle = part_bundle(snapshot)
    files = [(name, bundle[name].encode("utf-8"), kind) for name, kind in
             (("robot.urdf", "exported-model"), ("visual.obj", "visual-mesh"))]
    if any(len(raw) > 4 * 1024**2 for _, raw, _ in files):
        raise ValueError("Preview exceeds the per-file evidence budget")
    store._path(f"webots_runs/{_identifier(run)}").mkdir(parents=True)
    evidence = [store.save_snapshot(run, name, raw, kind=kind, provenance=provenance,
                                   producer=PRODUCER) for name, raw, kind in files]
    common = dict(provenance=provenance, source_revision=snapshot["source_revision"],
                  status="approximate", evidence=evidence,
                  missing_information=bundle["conversion_data"]["missing_information"])
    return {kind: store.write_report(store.new_report(kind, run, data=bundle[key], **common))
            for kind, key in (("model_manifest", "manifest_data"), ("conversion_report", "conversion_data"))}


def convert_part(store, run, *, operation_id):
    operation_id = _identifier(operation_id)
    manifest = store.read_report(run, "model_manifest")
    report = store.read_report(run, "conversion_report")
    provenance = report["provenance"]["kind"]
    files = {item["path"] for item in report["evidence"] if item["producer"] == PRODUCER}
    if (provenance not in {"fixture", "static-derived"} or manifest["provenance"]["kind"] != provenance
            or manifest["source_revision"] != report["source_revision"]
            or not {"robot.urdf", "visual.obj"} <= files):
        raise ValueError("An owned part preview is required")
    if version("urdf2webots") != CONVERTER:
        raise ValueError(f"Expected urdf2webots {CONVERTER}")
    root = store._run(run)
    stage = store._path(f"webots_runs/{run}/preview")
    stage.mkdir()
    target = stage / "Preview.proto"
    with (stage / "converter.log").open("wb") as log:
        subprocess.run([sys.executable, "-B", "-m", "urdf2webots.importer",
                        "--input", str(root / "robot.urdf"), "--output", str(target)],
                       cwd=stage, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=30)
    text = target.read_text(encoding="utf-8")
    urls = re.findall(r'^\s*url\s+"([^"]+)"\s*$', text, re.M)
    if not text.startswith("#VRML_SIM R2025a") or [url.replace("\\", "/") for url in urls] != ["../visual.obj"]:
        raise ValueError("Unexpected preview format or mesh reference")
    body, = manifest["data"]["rigid_bodies"]
    matrix = body["inertia_kg_m2"]
    expected = {"mass": [body["mass_kg"]], "centerOfMass": body["com_m"],
                "inertiaMatrix": [matrix[i][j] for i,j in ((0,0),(1,1),(2,2),(0,1),(0,2),(1,2))]}
    observed = {}
    for field in expected:
        pattern = r"([^\n]+)" if field == "mass" else r"\[([^\]]+)\]"
        match = re.search(r"^\s*" + field + r"\s+" + pattern, text, re.M)
        observed[field] = [float(value) for value in match[1].split()] if match else None
    changed = [field for field in expected if expected[field] != observed[field]]
    precision = dict(preserved=not changed, expected=expected, observed=observed, changed=changed)
    report["evidence"].extend(store.file_evidence(run, f"preview/{name}", kind=kind,
        provenance=provenance, producer={"name": "urdf2webots", "version": CONVERTER})
        for name,kind in (("Preview.proto","preview-proto"),("converter.log","converter-log")))
    report["data"].update(preview_operation_id=operation_id, converter_version=CONVERTER,
                          precision=precision, physical_validation="not_run")
    receipt = store.write_report(report)
    return {"ready_for_physics": False, "precision": precision, "report": receipt}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("publish-part", "convert-part", "native-status"))
    parser.add_argument("--state", type=Path)
    parser.add_argument("--task")
    parser.add_argument("--room")
    parser.add_argument("--run")
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--operation-id")
    args = parser.parse_args()
    try:
        if args.action == "native-status":
            result = native_gate()
        else:
            store = EvidenceStore(args.state, task_id=args.task, room_id=args.room)
            if args.action == "publish-part":
                result = publish_part(store, args.run, _load_json(args.snapshot))
            else:
                result = convert_part(store, args.run, operation_id=args.operation_id)
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as error:
        parser.exit(2, json.dumps({"ok": False, "error": str(error)}) + "\n")
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
