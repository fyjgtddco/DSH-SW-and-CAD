"""The monitor and engineering mode use the same manifest file."""
from hashlib import sha256
import json
from pathlib import Path

FIDELITY = ("Full", "Compressed", "Structured", "Pointer")


def canonical(data):
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def fingerprint(data):
    return sha256(canonical(data).encode()).hexdigest()


def manifest(path):
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    rows = data.get("pages") if isinstance(data, dict) else None
    if not isinstance(data, dict) or type(data.get("schema")) is not int or data["schema"] != 1 or not isinstance(rows, list) or not rows:
        raise ValueError("PAGE_MANIFEST_INVALID")
    out = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("PAGE_MANIFEST_INVALID")
        key, floor = row.get("id"), row.get("min_fidelity")
        side = row.get("side")
        if (not isinstance(key, str) or not key or key in out or floor not in FIDELITY
                or side not in ("main", "monitor") or row.get("scope") not in ("task", "part")
                or row.get("type") not in ("Constraint", "Evidence", "Plan")
                or not isinstance(row.get("source"), str)
                or row.get("degrade_path") != list(FIDELITY[:FIDELITY.index(floor) + 1])):
            raise ValueError("PAGE_MANIFEST_INVALID")
        out[key] = row
    expected = {"wf.mechanics_result", "lc.gate_load_case", "mode.rooms",
                "reg.parts", "mon.part.<part>", "mon.owner"}
    if set(out) != expected or any(
            out[k]["side"] != ("monitor" if k.startswith("mon.") else "main") for k in expected):
        raise ValueError("PAGE_MANIFEST_INVALID")
    return out


def project(row, fidelity):
    floor = row["min_fidelity"]
    if fidelity not in FIDELITY or FIDELITY.index(fidelity) > FIDELITY.index(floor):
        raise ValueError("PAGE_FIDELITY_BELOW_MINIMUM")
    meta = {k: row[k] for k in ("id", "type", "scope", "min_fidelity", "dirty", "source", "sha256", "fingerprint_kind")
            if k in row}
    meta["fidelity"] = fidelity
    if fidelity == "Compressed":
        meta["json"] = canonical(row.get("value"))
    elif fidelity != "Pointer":
        meta["value"] = row.get("structured", row.get("value")) if fidelity == "Structured" else row.get("value")
    return meta
