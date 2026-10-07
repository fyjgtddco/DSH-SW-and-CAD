# -*- coding: utf-8 -*-
"""E4：稳定运行 sw_bridge 子命令并把 stdout/stderr 以 UTF-8 落盘（避免 PS 重定向乱码）。

用法: python run_capture.py <输出文件> <命令...>
"""
import subprocess
import sys

out_file = sys.argv[1]
cmd = sys.argv[2:]
PY = r"C:\Users\j1877\AppData\Local\Programs\Python\Python313\python.exe"
p = subprocess.run([PY] + cmd,
                   cwd=r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools",
                   capture_output=True)
txt = p.stdout.decode("utf-8", "replace")
err = p.stderr.decode("utf-8", "replace")
with open(out_file, "w", encoding="utf-8") as f:
    f.write("=== EXIT %d ===\n" % p.returncode)
    f.write(txt)
    if err:
        f.write("\n=== STDERR ===\n" + err)
print("exit=%d  stdout=%d bytes  stderr=%d bytes -> %s"
      % (p.returncode, len(txt), len(err), out_file))
