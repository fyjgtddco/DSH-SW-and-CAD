"""Private, bounded, generation-scoped Webots external-controller transport."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import socket
import sys

from .fault_resources import budget_check, sample_job
from .owned_job import OwnedJob


class Transport:
    provenance = "real-webots"

    def __init__(self, state, root, config, run,generation):
        self.root = root
        self.home = Path(config["webots_home"]).resolve(strict=True)
        self.config = config
        self.run = run
        self.generation = generation
        from .evidence import _no_reparse
        self.dir = state / "webots_runs" / run / "worker" / str(generation)
        for path in (state/"webots_runs",state/"webots_runs"/run,self.dir.parent,self.dir):
            _no_reparse(path)
        self.dir.mkdir(parents=True, exist_ok=False)
        self.job = OwnedJob(config["limits"]["process_count"])
        self.writer = self.listener = self.previous = None
        self.identity = {}
        self.connected = False
        self.quitting = False

    async def launch(self, args):
        world = (self.root / args["world_path"]).resolve(strict=True)
        if not world.is_relative_to(self.root) or world.stat().st_size > 16 * 1024**2:
            raise ValueError("World outside supported input scope")
        binding = self.config["worlds"][args["world_path"]]
        digest = hashlib.sha256(world.read_bytes()).hexdigest()
        if not re.fullmatch(r"[0-9a-f]{64}",binding["model_sha256"]) or binding["sha256"] != digest or not re.fullmatch(r"[A-Za-z0-9_-]{1,95}",binding["robot"]):
            raise ValueError("Pinned world/robot mismatch")
        def file_digest(path):
            with path.open("rb") as source:
                return hashlib.file_digest(source,"sha256").hexdigest()
        for exe in ("webots.exe","webots-controller.exe"):
            path = self.home / "msys64/mingw64/bin" / exe
            if await asyncio.to_thread(file_digest,path) != self.config["binary_sha256"][exe]:
                raise ValueError("Runtime binary hash differs from trusted configuration")
        self.kernel = (self.home/self.config["kernel_relative_path"]).resolve(strict=True)
        if (not self.kernel.is_relative_to(self.home)
                or await asyncio.to_thread(file_digest,self.kernel) != self.config["kernel_sha256"]):
            raise ValueError("Unpinned native kernel executable")
        robot = binding["robot"]
        with socket.socket() as reserve:
            reserve.bind(("127.0.0.1",0))
            port = reserve.getsockname()[1]
        token = secrets.token_hex(32)
        ready = asyncio.get_running_loop().create_future()

        async def peer(reader, writer):
            try:
                msg = json.loads(await asyncio.wait_for(reader.readline(),2))
                if (msg["token"] != token or msg["run_id"] != self.run or msg["port"] != port
                        or msg["generation"] != self.generation or msg["robot"] != robot or msg["world_sha256"] != digest
                        or not Path(msg["world"]).samefile(world) or ready.done()):
                    raise ValueError("Identity mismatch")
                identity = self.job.identity(msg["pid"])
                if not any(Path(identity["exe"]).samefile(path) for path in (sys.executable,sys._base_executable)):
                    raise PermissionError("Controller interpreter mismatch")
                self.identity.update(controller=identity,node_id=msg["node_id"],basic_time_step_ms=msg["basic_time_step_ms"])
                self.reader, self.writer = reader, writer
                ready.set_result(msg["time"])
            except Exception:
                writer.close()

        self.listener = await asyncio.start_server(peer,"127.0.0.1",0,limit=65536,backlog=2)
        rpc = self.listener.sockets[0].getsockname()[1]
        env = dict(os.environ, WEBOTS_HOME=str(self.home),A04_RUN=self.run,A04_OUTPUT=str(self.dir),A04_WORLD=str(world),
                   A04_GENERATION=str(self.generation),A04_HASH=digest,A04_ROBOT=robot,A04_PORT=str(port),A04_RPC_PORT=str(rpc),A04_TOKEN=token)
        self.identity.update(world=str(world),world_sha256=digest,model_sha256=binding["model_sha256"],
                             run_id=self.run,generation=self.generation,port=port,robot=robot,
            bootstrap=self.job.spawn([sys.executable,"-B",str(Path(__file__).with_name("bootstrap.py"))],env,self.dir))
        def save_identity():
            tmp = self.dir/"identity.tmp"
            with tmp.open("w",encoding="utf-8") as f:
                json.dump(self.identity,f,allow_nan=False)
                f.flush()
                os.fsync(f.fileno())
            tmp.replace(self.dir/"identity.json")
        save_identity()
        sim_time = await ready
        membership = self.sample()
        kernels = [p for p in membership["processes"] if Path(p["exe"]).samefile(self.kernel)]
        if not membership["complete"] or len(kernels)!=1:
            raise PermissionError("Unique native kernel identity not verified in private Job")
        self.identity.update(kernel=kernels[0],owned_processes=membership["processes"])
        save_identity()
        self.listener.close()
        self.connected = True
        return dict(data=dict(self.identity),completed_steps=0,sim_time=sim_time)

    def sample(self):
        if "bootstrap" not in self.identity:
            return None
        sample = sample_job(self.job.handle,previous=self.previous)
        self.previous = sample
        check = budget_check(sample,self.config["limits"])
        if check["within_budget"] is not True:
            raise RuntimeError("Resource budget " + str(check))
        if "kernel" in self.identity and not self.quitting:
            known=self.identity["kernel"]
            if not any((p["pid"],p["creation_time"],p["exe"])==(known["pid"],known["creation_time"],known["exe"])
                       for p in sample["processes"]):
                raise ConnectionError("Native kernel identity lost")
        if not self.quitting and (not sample["process_count"] or (self.connected and self.reader.at_eof())):
            raise ConnectionError("Owned runtime disconnected")
        return sample

    async def command(self,name,args,progress):
        if not self.connected:
            raise ConnectionError("No verified controller")
        self.quitting = name == "sim_shutdown"
        self.writer.write(json.dumps(dict(name=name,args=args),allow_nan=False).encode()+b"\n")
        await self.writer.drain()
        while True:
            raw = await self.reader.readline()
            if not raw:
                raise ConnectionError("Controller disconnected")
            msg = json.loads(raw)
            if msg.get("progress") is True:
                progress(msg["completed_steps"],msg["sim_time"])
            elif msg.get("done") is True:
                if name == "sim_shutdown":
                    while self.job.active():
                        await asyncio.sleep(.05)
                return {key:msg[key] for key in ("completed_steps","sim_time","data")}
            else:
                raise ValueError("Invalid controller response")

    async def close(self):
        self.connected = False
        if self.listener:
            self.listener.close()
        if self.writer:
            self.writer.close()
            # 【Windows 句柄释放】只 close() 不 wait_closed() 时，管道句柄要等
            #   事件循环下一轮才真正关掉；此时上层（测试的 TemporaryDirectory
            #   清理 / 换机后的目录删除）会拿到 WinError 32（文件被占用）。
            #   这里显式等传输层关闭，保证 close() 返回后句柄已释放。
            try:
                await self.writer.wait_closed()
            except Exception:
                pass
        await asyncio.to_thread(self.job.stop)
