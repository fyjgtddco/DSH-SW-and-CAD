"""Owned process entry point. Both native pipes are drained even after the log cap."""
import os
from pathlib import Path
import subprocess
import sys
import threading


def main():
    env = os.environ.copy()
    port, robot, world = env["A04_PORT"], env["A04_ROBOT"], env["A04_WORLD"]
    home = Path(env["WEBOTS_HOME"])
    binaries = home / "msys64/mingw64/bin"
    env["PYTHONPATH"] = str(home / "lib/controller/python")
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env["PATH"]
    ready = threading.Event()
    expected = f"ipc://{port}/{robot}".encode()

    def launch(name, args):
        proc = subprocess.Popen(args, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        def drain(pipe, suffix):
            tail = b""
            remaining = 65536
            with pipe, open(name + "." + suffix, "xb") as log:
                while chunk := pipe.read1(8192):
                    if name == "webots" and suffix == "stdout":
                        lines = (tail + chunk).split(b"\n")
                        tail = lines.pop()[-512:]
                        if any(line.strip() == expected for line in lines):
                            ready.set()
                    if remaining:
                        saved = chunk[:remaining]
                        log.write(saved)
                        log.flush()
                        remaining -= len(saved)
        for label, pipe in (("stdout",proc.stdout),("stderr",proc.stderr)):
            threading.Thread(target=drain,args=(pipe,label),daemon=True).start()
        return proc

    launch("webots", [str(binaries / "webots.exe"), "--batch", "--minimize","--mode=fast",
                     "--no-rendering","--stdout","--stderr","--extern-urls",f"--port={port}",world])
    if not ready.wait(25):
        raise TimeoutError("No matching extern URL on owned launcher output")
    ctrl = launch("controller", [str(binaries / "webots-controller.exe"), "--protocol=ipc",
                  f"--port={port}",f"--robot-name={robot}",str(Path(__file__).with_name("controller_worker.py"))])
    return ctrl.wait()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        Path("bootstrap-error.txt").write_text(str(error)[:2048], encoding="utf-8")
        raise
