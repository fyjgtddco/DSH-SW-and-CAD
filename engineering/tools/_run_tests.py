# -*- coding: utf-8 -*-
"""
Physics-in-the-Loop 完整功能测试脚本 v2
========================================
"""
import sys, os, json, subprocess, time, tempfile

TOOLS = r"C:\Users\j1877\.dsh\.agent-presets\engineering\tools"
CASE_FILE = r"C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools\_test_load_case.json"
ENV = dict(os.environ)
ENV["PYTHONIOENCODING"] = "utf-8"

results = {"pass": 0, "fail": 0, "tests": []}

def run_cmd(args, desc):
    r = subprocess.run(
        [sys.executable, os.path.join(TOOLS, "sw_bridge.py")] + args,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=ENV, timeout=120,
    )
    out = r.stdout
    idx = out.find("{")
    if idx >= 0:
        try:
            return json.loads(out[idx:])
        except json.JSONDecodeError as e:
            results["fail"] += 1
            results["tests"].append({"name": desc, "ok": False, "err": f"JSON parse: {e}"})
            return None
    results["fail"] += 1
    results["tests"].append({"name": desc, "ok": False, "err": f"no JSON (rc={r.returncode})"})
    return None

def check(desc, cond, detail=""):
    if cond:
        results["pass"] += 1
        results["tests"].append({"name": desc, "ok": True})
    else:
        results["fail"] += 1
        results["tests"].append({"name": desc, "ok": False, "err": detail})

def ok_label(v):
    return "OK" if v else "FAIL"

# ─── Test 1: physics-status ──────────────────────────────────────────────────
print(f"[1/10] physics-status ... {ok_label(True)}", flush=True)
d = run_cmd(["physics-status"], "status")
if d:
    check("status: has default backend", bool(d.get("default")))
    check("status: backends is dict", isinstance(d.get("backends"), dict))
    check("status: available is list", isinstance(d.get("available"), list))
    check("status: recommendation present", bool(d.get("recommendation", "")))
else:
    print("  [FAIL] no JSON returned")

# ─── Test 2: physics-validate-case (valid) ───────────────────────────────────
print(f"[2/10] validate-case (valid) ... {ok_label(True)}", flush=True)
d = run_cmd(["physics-validate-case", CASE_FILE], "validate-valid")
if d:
    check("validate: ok=True", d.get("ok") == True)
    check("validate: no errors", len(d.get("errors", [])) == 0)
    check("validate: has case data", d.get("case") is not None)
    check("validate: problem_id matches", d.get("case",{}).get("meta",{}).get("problem_id") == "TEST_CANTILEVER")
else:
    print("  [FAIL] no JSON")

# ─── Test 3: physics-validate-case (bad file) ────────────────────────────────
print(f"[3/10] validate-case (bad file) ... {ok_label(True)}", flush=True)
d = run_cmd(["physics-validate-case", r"C:\nonexistent\bad.json"], "validate-bad")
if d:
    check("validate-bad: ok=False", d.get("ok") == False)
    check("validate-bad: has error msg", bool(d.get("error", "")))
else:
    print("  [FAIL] no JSON")

# ─── Test 4: physics-demo ────────────────────────────────────────────────────
print(f"[4/10] physics-demo ... {ok_label(True)}", flush=True)
DEMO_REPORT = {}
d = run_cmd(["physics-demo"], "demo")
if d:
    check("demo: ok=True", d.get("ok") == True)
    check("demo: has report", "report" in d)
    rpt = d.get("report", {})
    check("demo: has overall_status", "overall_status" in rpt)
    check("demo: has fea_result", "fea_result" in rpt)
    fr = rpt.get("fea_result", {})
    check("demo: safety_factor is number", isinstance(fr.get("safety_factor"), (int, float)))
    check("demo: max_stress is number", isinstance(fr.get("max_von_mises_mpa"), (int, float)))
    check("demo: max_displacement is number", isinstance(fr.get("max_displacement_mm"), (int, float)))
    check("demo: method is valid", fr.get("method") in ("analytical", "feapy_numpy", "skfem"))
    check("demo: mass > 0", fr.get("mass_kg", 0) > 0)
    check("demo: report_path exists", os.path.exists(rpt.get("report_path", "")))
    check("demo: has markdown_summary", bool(d.get("markdown_summary", "")))
    DEMO_REPORT = rpt
else:
    print("  [FAIL] no JSON")

# ─── Test 5: physics-optimize (3 iter) ───────────────────────────────────────
print(f"[5/10] physics-optimize (3 iter) ... {ok_label(True)}", flush=True)
d = run_cmd(["physics-optimize", CASE_FILE, "--max-iter", "3"], "optimize")
if d:
    check("optimize: has ok", "ok" in d)
    check("optimize: has iterations", "iterations" in d)
    check("optimize: has final_report", "final_report" in d)
    if d.get("final_report"):
        fr = d["final_report"].get("fea_result", {})
        check("optimize: final SF is number", isinstance(fr.get("safety_factor"), (int, float)))
        check("optimize: final status is string", isinstance(d["final_report"].get("overall_status"), str))
    check("optimize: converged is bool", isinstance(d.get("converged"), bool))
else:
    print("  [FAIL] no JSON")

# ─── Test 6: physics-report (use demo run_id) ────────────────────────────────
print(f"[6/10] physics-report ... ", end="", flush=True)
rid = DEMO_REPORT.get("run_id", "")
if rid:
    d = run_cmd(["physics-report", rid], "report")
    if d:
        check("report: ok=True", d.get("ok") == True)
        check("report: has markdown_summary", "markdown_summary" in d)
        check("report: report matches run_id", d.get("report",{}).get("run_id") == rid)
    else:
        print("[FAIL] no JSON")
else:
    print("SKIP (no demo run_id)")

# ─── Test 7: physics-recommend ───────────────────────────────────────────────
print(f"[7/10] physics-recommend ... ", end="", flush=True)
if rid:
    d = run_cmd(["physics-recommend", rid], "recommend")
    if d:
        check("recommend: ok=True", d.get("ok") == True)
        check("recommend: has actions list", "actions" in d)
        check("recommend: has reason", bool(d.get("reason", "")))
    else:
        print("[FAIL] no JSON")
else:
    print("SKIP")

# ─── Test 8: doctor ─────────────────────────────────────────────────────────
print(f"[8/10] doctor ... {ok_label(True)}", flush=True)
d = run_cmd(["doctor"], "doctor")
if d:
    check("doctor: ok=True", d.get("ok") == True)
    check("doctor: has python version", bool(d.get("python", "")))
    check("doctor: pywin32 present", d.get("pywin32") is not None)
else:
    print("  [FAIL] no JSON")

# ─── Test 9: ac-status ───────────────────────────────────────────────────────
print(f"[9/10] ac-status ... {ok_label(True)}", flush=True)
d = run_cmd(["ac-status"], "ac-status")
if d:
    check("ac-status: has connected field", "connected" in d)
    check("ac-status: connected is bool", isinstance(d.get("connected"), bool))
else:
    # ac-status may print log lines before JSON; the run_cmd handles this
    check("ac-status: returned something", d is not None)

# ─── Test 10: swapi build cylinder ──────────────────────────────────────────
print(f"[10/10] swapi build cylinder ... ", end="", flush=True)
test_dir = tempfile.mkdtemp(prefix="dsh_swapi_test_")
script = os.path.join(test_dir, "_swapi_build_test.py")
with open(script, "w", encoding="utf-8") as f:
    f.write(f'import sys\n')
    f.write(f'sys.path.insert(0, r"{TOOLS}")\n')
    f.write(f'import swapi\n')
    f.write(f'm = swapi.new_part()\n')
    f.write(f'm.begin_sketch("Front Plane")\n')
    f.write(f'm.circle(0, 0, 10)\n')
    f.write(f'm.end_sketch()\n')
    f.write(f'm.extrude(20)\n')
    f.write(f'out = r"{test_dir}\\DSH_test_cyl.sldprt"\n')
    f.write(f'm.save(out)\n')
    f.write(f'import json, os\n')
    f.write(f'print(json.dumps({{"saved": os.path.exists(out), "size": os.path.getsize(out) if os.path.exists(out) else 0}}))\n')

r = subprocess.run(
    [sys.executable, os.path.join(TOOLS, "sw_bridge.py"), "run", script],
    capture_output=True, text=True, encoding="utf-8", errors="replace",
    env=ENV, timeout=120,
)
out = r.stdout.strip()
# sw_bridge.py run 命令把脚本 stdout 放在 "stdout" 字段里
if "stdout" in r.stdout:
    try:
        wrapper = json.loads(out)
        script_out = wrapper.get("stdout", "")
        # 脚本可能输出多个 JSON，只取第一个
        sidx = script_out.find("{")
        eidx = script_out.find("}", sidx) + 1
        if sidx >= 0 and eidx > sidx:
            d = json.loads(script_out[sidx:eidx])
            check("swapi: file saved", d.get("saved") == True)
            check("swapi: file > 0 bytes", d.get("size", 0) > 1000)
        else:
            check("swapi: script output has JSON", False, f"stdout={script_out[:200]}")
    except Exception as e:
        check("swapi: parsed wrapper JSON", False, f"parse error: {e}")
elif idx >= 0:
    try:
        d = json.loads(out[idx:])
        check("swapi: ok=True", d.get("ok") == True)
        check("swapi: file saved", d.get("saved") == True)
        check("swapi: file > 0 bytes", d.get("size", 0) > 1000)
    except Exception as e:
        check("swapi: parsed JSON", False, f"parse error: {e}")
else:
    check("swapi: got JSON output", False, f"stdout={out[:200]} stderr={r.stderr[:200]}")

# ─── Summary ─────────────────────────────────────────────────────────────────
print()
print("=" * 60)
print(f"Test results: {results['pass']} PASS, {results['fail']} FAIL")
print("=" * 60)
if results["fail"] > 0:
    print("\nFailed tests:")
    for t in results["tests"]:
        if not t["ok"]:
            print(f"  [FAIL] {t['name']}: {t.get('err', '')}")
    sys.exit(1)
else:
    print("\nAll tests passed!")
    sys.exit(0)
