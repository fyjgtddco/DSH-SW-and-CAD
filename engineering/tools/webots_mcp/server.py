"""Official MCP SDK stdio transport with explicit unavailable/fake execution."""
import argparse
import asyncio
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import sys

from jsonschema import Draft202012Validator
from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from . import __version__
from .fake_worker import FakeWorker

INSTRUCTIONS = "Bounded Webots runtime and evidence inspection. Execution requires an explicitly pinned local runtime. Native CAD export and repair require calibration. Fake fixtures never prove readiness or physics. Unknown outcomes must not be replayed."
CAPABILITIES = ["stored_reports", "bounded_evidence", "attribution", "pinned_runtime", "durable_operations"]
BLOCKED_NATIVE = ["native_mesh_frame", "complete_global_mate_state", "native_identity_calibration",
                  "converter_physical_precision", "calibrated_collision_feedback", "authorized_native_repair"]
ID = {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9_-]{0,95}(?![\s\S])"}
TEXT = {"type": "string", "minLength": 1, "maxLength": 256}
CONTEXT = {key: ID for key in ("task_id", "room_id", "request_id")}
SESSION = {"run_id": ID, "session_generation": {"type": "integer", "minimum": 1}}
MUTATIONS = {"sim_launch", "sim_shutdown", "sim_step", "sim_reset", "sim_screenshot",
             "sim_contacts", "sim_convert", "sim_motor", "sim_sensor", "sim_cancel"}


def tool(name, description, properties=None, *, scoped=True, optional=()):
    fields = {**CONTEXT, **(SESSION if scoped else {}), **(properties or {})}
    if name in MUTATIONS:
        fields["operation_id"] = ID
    return types.Tool(name=name, description=description, inputSchema={
        "type": "object", "properties": fields,
        "required": [key for key in fields if key not in optional], "additionalProperties": False})


TOOLS = [
    tool("sim_status", "Cached availability; never starts Webots.", scoped=False),
    tool("sim_launch", "Submit a pinned world job when a runtime is configured.", {"world_path": TEXT}, scoped=False),
    tool("sim_shutdown", "Request owned-session shutdown and confirm exit."),
    tool("sim_step", "Submit bounded steps.", {"steps": {"type": "integer", "minimum": 1, "maximum": 1000}}),
    tool("sim_reset", "Cold-reset a pinned world into a new generation."),
    tool("sim_scene", "Read scene snapshot."),
    tool("sim_field", "Read one node field.", {"node": TEXT, "field": TEXT}),
    tool("sim_screenshot", "Request an on-demand view."),
    tool("sim_contacts", "Request contact observation; no physical verdict.", {"node": TEXT}),
    tool("sim_convert", "Conversion requires a calibrated converter binding.", {"urdf_path": TEXT}, scoped=False),
    tool("sim_devices", "Discover devices of the owned robot."),
    tool("sim_motor", "Submit one motor command.", {"device": TEXT, "velocity": {"type": "number"},
         "position": {"type": "number"}}, optional=("position",)),
    tool("sim_sensor", "Request one sensor sample.", {"device": TEXT}),
    tool("sim_job", "Read job snapshot; unknown must not be replayed.", {"job_id": ID}),
    tool("sim_cancel", "Request cancellation; acknowledgement is not rollback.", {"job_id": ID}),
    tool("sim_report", "Read an owned stored report.", {"run_id": ID, "report_type": {
         "enum": ["model_manifest", "conversion_report", "experiment_report"]}}, scoped=False),
    tool("sim_attribute", "Inspect one owned issue; retain all candidates and evidence levels. Never writes CAD.",
         {"run_id": ID, "issue_id": ID}, scoped=False),
    tool("sim_evidence", "Read an owned bounded log page.", {"run_id": ID, "name": ID,
         "offset": {"type": "integer", "minimum": 0},
         "limit": {"type": "integer", "minimum": 4, "maximum": 16384},
         "expected_sha256": {"type": "string", "pattern": r"^[a-f0-9]{64}(?![\s\S])"}},
         scoped=False, optional=("name", "offset", "limit", "expected_sha256")),
]
TOOL_INDEX = {item.name: item for item in TOOLS}


class PeripheralServer:
    def __init__(self, state_dir, *, input_root, fake_worker=None, runtime_config=None):
        self.state_dir = Path(state_dir).resolve(strict=True)
        self.input_root = Path(input_root).resolve(strict=True)
        if not self.state_dir.is_dir() or not self.input_root.is_dir():
            raise ValueError("State and input roots must be existing directories")
        self.runtime = None
        if runtime_config is not None:
            if fake_worker:
                raise ValueError("Select exactly one worker backend")
            from .runtime import Runtime
            self.runtime = Runtime(self.state_dir,self.input_root,runtime_config)
        self.worker = FakeWorker(fake_worker) if fake_worker else None

    def envelope(self, args):
        result = {key: args.get(key) for key in (
            "run_id", "job_id", "request_id", "operation_id", "session_generation", "task_id", "room_id")}
        for key, value in result.items():
            valid = (type(value) is int and value >= 1) if key == "session_generation" else (
                isinstance(value, str) and re.fullmatch(ID["pattern"], value))
            if not valid:
                result[key] = None
        result.update(schema_version=1, ok=False, error_code=None, execution_state="not_started",
                      sim_time=None, completed_steps=None, evidence=[], core_ready=False,
                      provenance="fake" if self.worker else "unavailable", job_state="NEW",
                      snapshot_at=datetime.now(timezone.utc).isoformat(), stale=True,
                      capabilities=CAPABILITIES, blocked_native=BLOCKED_NATIVE,
                      hint="Runtime is opt-in; native CAD export and repair await measured calibration.")
        return result

    def input_path(self, value, suffix):
        relative = Path(value)
        if relative.is_absolute() or ".." in relative.parts or ":" in value or "\\" in value:
            raise ValueError("Input must be a relative path inside input_root")
        path = (self.input_root / relative).resolve(strict=True)
        if not path.is_relative_to(self.input_root) or not path.is_file() or path.suffix.lower() != suffix:
            raise ValueError("Input path escapes root or has an unsupported file type")
        return path

    async def dispatch(self, name, args):
        result = self.envelope(args if isinstance(args, dict) else {})
        if name not in TOOL_INDEX:
            result["error_code"] = "UNKNOWN_TOOL"
            return result
        try:
            json.dumps(args, allow_nan=False)
            errors = list(Draft202012Validator(TOOL_INDEX[name].inputSchema).iter_errors(args))
            if errors:
                raise ValueError(errors[0].message)
            for key, suffix in (("world_path", ".wbt"), ("urdf_path", ".urdf")):
                if key in args and not self.runtime:
                    self.input_path(args[key], suffix)
        except (ValueError, TypeError, OSError) as error:
            result.update(error_code="INVALID_ARGUMENT", hint=str(error)[:256])
            return result
        if name in ("sim_report", "sim_evidence", "sim_attribute"):
            try:
                from .evidence import EvidenceStore
                store = EvidenceStore(self.state_dir, task_id=args["task_id"], room_id=args["room_id"])
                if name == "sim_report":
                    data = store.read_report(args["run_id"], args["report_type"])
                elif name == "sim_attribute":
                    from .repair import attribute
                    data = attribute(store, args["run_id"], args["issue_id"])
                else:
                    page_args = {key: args[key] for key in ("name", "offset", "limit", "expected_sha256") if key in args}
                    data = store.read_log(args["run_id"], **page_args)
                result.update(ok=True, execution_state="completed", data=data)
            except ImportError:
                result["error_code"] = "EVIDENCE_UNAVAILABLE"
            except PermissionError:
                result["error_code"] = "OWNERSHIP_MISMATCH"
            except FileNotFoundError:
                result["error_code"] = "EVIDENCE_NOT_FOUND"
            except (ValueError, TypeError, KeyError, AttributeError) as error:
                result.update(error_code="EVIDENCE_INVALID", hint=str(error)[:256])
            except OSError as error:
                result.update(error_code="EVIDENCE_IO_ERROR", hint=str(error)[:256])
            return result
        if name == "sim_convert" and not self.worker:
            result.update(error_code="CALIBRATION_REQUIRED",
                          hint="Native export/conversion is blocked: measured mesh frame, complete mates and converter precision are unverified.")
            return result
        if self.runtime:
            return await self.runtime.dispatch(name,args,result,self.input_path)
        if self.worker:
            return self.worker.dispatch(name, args, result)
        if name == "sim_status":
            result.update(ok=True, unsupported=["unconfigured_runtime", "native_export", "native_repair"],
                          state_dir=str(self.state_dir))
        else:
            result["error_code"] = "CORE_NOT_IMPLEMENTED"
        return result

    def sdk_server(self):
        server = Server("webots", version=__version__, instructions=INSTRUCTIONS)

        @server.list_tools()
        async def list_tools():
            return TOOLS

        @server.call_tool(validate_input=False)
        async def call_tool(name, arguments):
            result = await self.dispatch(name, arguments)
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, allow_nan=False))],
                structuredContent=result, isError=not result["ok"])

        return server


async def serve(peripheral):
    server = peripheral.sdk_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", required=True)
    parser.add_argument("--fake-worker", choices=("complete", "error", "timeout", "unknown"))
    parser.add_argument("--runtime-config", type=Path, help="Trusted A04 pinned local runtime configuration")
    args = parser.parse_args()
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    declared = os.environ.get("DSH_STATE_DIR")
    if not declared or not Path(declared).is_dir():
        parser.error("DSH_STATE_DIR must explicitly name an existing isolated state directory")
    from _store import state_dir
    resolved = Path(state_dir()).resolve(strict=True)
    if resolved != Path(declared).resolve(strict=True):
        parser.error("Resolved state_dir does not match DSH_STATE_DIR")
    logging.info("webots state_dir=%s core_ready=false backend=%s", resolved,
                 "configured" if args.runtime_config else ("fake" if args.fake_worker else "unavailable"))
    config = json.loads(args.runtime_config.read_text(encoding="utf-8")) if args.runtime_config else None
    peripheral = PeripheralServer(resolved, input_root=args.input_root, fake_worker=args.fake_worker,runtime_config=config)
    async def run():
        try:
            await serve(peripheral)
        finally:
            if peripheral.runtime:
                await peripheral.runtime.close()
    asyncio.run(run())


if __name__ == "__main__":
    main()

