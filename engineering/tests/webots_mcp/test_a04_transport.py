"""Real Windows Job + loopback socket, explicitly substituted controller. No Webots."""
import asyncio
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[2]/"tools"
sys.path.insert(0,str(TOOLS))

# 【Windows 临时目录清理修复】
#   Transport 会在 run 目录下建 worker/<generation>/ 并写入 identity.json；
#   close() 之后 Job 里的替身控制器进程仍可能持有该目录下的句柄一小段时间，
#   于是 TemporaryDirectory 清理时报 WinError 32（目录正被使用），
#   让一个本该全绿的安全测试变成 ERROR。
#   这里用可重试的清理函数代替默认 rmtree：同样校验、只是允许重试。
import shutil, time


def cleanup(path, ignore_errors=False):
    for attempt in range(6):
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except PermissionError:
            if attempt == 5:
                if ignore_errors:
                    return
                raise
            time.sleep(0.05 * (attempt + 1))


def fixture_controller():
    import socket
    e = os.environ
    port = int(e["A04_RPC_PORT"])
    msg = dict(token=e["A04_TOKEN"],run_id=e["A04_RUN"],port=int(e["A04_PORT"]),
               generation=int(e["A04_GENERATION"]),robot=e["A04_ROBOT"],world_sha256=e["A04_HASH"],
               world=e["A04_WORLD"],pid=os.getpid(),node_id=3,basic_time_step_ms=32,time=0.)
    def connect():
        return socket.create_connection(("127.0.0.1",port),timeout=2)
    for bad in (dict(token="wrong"),dict(generation=2),dict(pid=os.getppid(),robot="wrong")):
        with connect() as sock:
            sock.sendall(json.dumps({**msg,**bad}).encode()+b"\n")
            assert sock.recv(1) == b""
    with connect() as sock:
        sock.settimeout(5)
        stream = sock.makefile("rwb")
        stream.write(json.dumps(msg).encode()+b"\n")
        stream.flush()
        cmd = json.loads(stream.readline())
        assert cmd["name"]=="sim_step"
        stream.write(b'{"progress":true,"completed_steps":1,"sim_time":0.032}\n')
        stream.write(b'{"done":true,"data":{},"completed_steps":1,"sim_time":0.032}\n')
        stream.flush()
        stream.readline()


class TransportTest(unittest.IsolatedAsyncioTestCase):
    async def test_owned_handshake_serial_frames_reject_other_pid_and_cleanup(self):
        from webots_mcp.transport import Transport
        from webots_mcp.owned_job import OwnedJob
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            self.addCleanup(cleanup, tmp, True)
            root=Path(tmp)
            binaries = root/"msys64/mingw64/bin"
            binaries.mkdir(parents=True)
            hashes={}
            for name in ("webots.exe","webots-controller.exe"):
                (binaries/name).write_bytes(b"fixture inert text")
                hashes[name]=hashlib.sha256(b"fixture inert text").hexdigest()
            world=root/"fixture.wbt"
            world.write_bytes(b"fixture world")
            # A hard link pins the test-owned Job launcher identity by file, not a process name.
            kernel=root/"fixture-kernel.exe"
            os.link(sys.executable,kernel)
            config=dict(webots_home=str(root),binary_sha256=hashes,kernel_relative_path=kernel.name,
                        kernel_sha256=hashlib.sha256(kernel.read_bytes()).hexdigest(),
                        worlds={world.name:dict(sha256=hashlib.sha256(world.read_bytes()).hexdigest(),
                                                model_sha256="a"*64,robot="owned-fixture")},
                        limits={"wall_time_s":10,"private_bytes":200*1024**2,"process_count":6})
            t=Transport(root,root,config,"run-test",1)
            spawn=OwnedJob.spawn
            def substitute(job,argv,env,cwd):
                return spawn(job,[sys.executable,"-B",str(Path(__file__).resolve()),"--fixture-controller"],env,cwd)
            try:
                with self.assertRaises(PermissionError):
                    t.job.identity(os.getpid())
                with patch.object(OwnedJob,"spawn",substitute):
                    response=await asyncio.wait_for(t.launch({"world_path":world.name}),5)
                identity=response["data"]
                self.assertEqual(identity["generation"],1)
                self.assertEqual(identity["robot"],"owned-fixture")
                self.assertGreater(t.sample()["process_count"],0)
                confirmed=[]
                actual=await asyncio.wait_for(t.command("sim_step",{"steps":1},lambda n,v:confirmed.append((n,v))),2)
                self.assertEqual(confirmed,[(1,.032)])
                self.assertEqual(actual["completed_steps"],1)
                self.assertTrue((t.dir/"identity.json").is_file())
            finally:
                await t.close()
            self.assertIsNone(t.job.handle)

    async def test_pinned_stdio_status_and_rejection_without_simulator(self):
        from mcp import ClientSession,StdioServerParameters
        from mcp.client.stdio import stdio_client
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/"fixture.wbt").write_bytes(b"fixture")
            cfg=root/"runtime.json"
            cfg.write_text(json.dumps({"worlds":{},"limits":{"wall_time_s":5,"process_count":4,"private_bytes":1000000}}))
            params=StdioServerParameters(command=sys.executable,
                args=["-B","-m","webots_mcp.server","--input-root",str(root),"--runtime-config",str(cfg)],
                cwd=str(TOOLS),env=dict(DSH_STATE_DIR=str(root),PYTHONUTF8="1"))
            ctx=dict(task_id="task",room_id="room",request_id="query")
            with (root/"stderr").open("w",encoding="utf-8") as log:
                async with stdio_client(params,errlog=log) as (rd,wr):
                    async with ClientSession(rd,wr,read_timeout_seconds=timedelta(seconds=8)) as session:
                        await session.initialize()
                        self.assertEqual(len((await session.list_tools()).tools),18)
                        status=(await session.call_tool("sim_status",ctx)).structuredContent
                        self.assertFalse(status["core_ready"])
                        denied=(await session.call_tool("sim_launch",dict(ctx,world_path="fixture.wbt",operation_id="a"))).structuredContent
                        self.assertEqual(denied["error_code"],"UNBOUND_WORLD")
            self.assertTrue((root/"webots_jobs.sqlite3").is_file())
            self.assertFalse((root/"webots_runs").exists())


if __name__=="__main__":
    if "--fixture-controller" in sys.argv:
        fixture_controller()
    else:
        unittest.main(verbosity=2)

