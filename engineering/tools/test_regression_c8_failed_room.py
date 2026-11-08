#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""【C8 回归测试】part_registry 任务台账隔离 + select 不回收失败房间。

对应缺陷（01-缺陷清单与日志定位.md · C8，P2）：

  ① `:1383`：part_registry 的默认路径指向【旧任务 20261027 的残留台账】，
     于是相邻尺寸一致性校验在"拿上一轮的数据"上做，输出「一致性通过」的假阳性。
  ② `:4650`：room-fail 之后 select 不回收回退房 —— `_finished` 把 failed_at
     当成了完成，失败房间被跳过、从没重做，交付里就少了该有的零件。

本文件验证 ②（①的 task_id 校验由 gate 组在 part_registry.py 侧负责）：

  判据 A：`_get_done_rooms()` 只认 ended_at，绝不把 failed_at 收进"完成"集合
  判据 B：`_get_failed_rooms()` 能单独列出"失败且未重开"的房间
  判据 C：select 的波次判定源码里，`_finished` 不再包含 failed_at
          （这条是回归"改回去"的护栏：谁把 failed_at 加回完成判据，这里会红）
  判据 D：select 返回体带 failed_rooms 字段（让调用方看得见"还欠谁"）

用法：
    python test_regression_c8_failed_room.py
退出码 0 = 全部通过。
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
_checks = []


def check(name, ok, detail=None):
    _checks.append({"name": name, "ok": bool(ok), "detail": detail})
    print(("  [PASS] " if ok else "  [FAIL] ") + name)
    if not ok and detail is not None:
        print("         detail: %r" % (detail,))
    return bool(ok)


def _write_mode_state(state_dir, rooms):
    p = os.path.join(state_dir, "mode_state.json")
    with io.open(p, "w", encoding="utf-8") as f:
        json.dump({"mode": "2", "rooms": rooms}, f, ensure_ascii=False, indent=2)
    return p


def main():
    state_dir = tempfile.mkdtemp(prefix="dsh-c8-")
    try:
        # 构造：甲=已完成，乙=失败待重做，丙=还在跑，丁=失败后已重开（failed_at 被清）
        _write_mode_state(state_dir, {
            "甲": {"active": False, "ended_at": 1000.0},
            "乙": {"active": False, "failed_at": 1001.0},
            "丙": {"active": True, "started_at": 1002.0},
            "丁": {"active": True, "started_at": 1003.0},   # 重开过 → failed_at 为 None
        })

        probe = (
            "import sys, json\n"
            "sys.path.insert(0, %r)\n"
            "import workflow_gate as wg\n"
            "print(json.dumps({\n"
            "  'done': sorted(wg._get_done_rooms()),\n"
            "  'failed': sorted(wg._get_failed_rooms()),\n"
            "}, ensure_ascii=False))\n"
        ) % HERE
        env = dict(os.environ)
        env["DSH_STATE_DIR"] = state_dir
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        p = subprocess.run([PY, "-c", probe], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=env, timeout=120)
        try:
            info = json.loads((p.stdout or "").strip().splitlines()[-1])
        except Exception:
            info = {"_raw": p.stdout, "_err": (p.stderr or "")[-2000:]}

        print("\n=== 判据 A：完成集合只认 ended_at ===")
        check("done == ['甲']", info.get("done") == ["甲"], info.get("done"))
        check("失败房间乙不在完成集合里", "乙" not in (info.get("done") or []),
              info.get("done"))
        check("还在跑的丙不在完成集合里", "丙" not in (info.get("done") or []),
              info.get("done"))

        print("\n=== 判据 B：失败房间单独列出 ===")
        check("failed == ['乙']", info.get("failed") == ["乙"], info.get("failed"))
        check("重开过的丁不算失败", "丁" not in (info.get("failed") or []),
              info.get("failed"))

        print("\n=== 判据 C：select 的 _finished 不得包含 failed_at ===")
        src_path = os.path.join(HERE, "workflow_gate.py")
        with io.open(src_path, encoding="utf-8") as f:
            src = f.read()
        # 找到波次判定里那行 _finished = ...
        bad = []
        for line in src.splitlines():
            s = line.strip()
            if s.startswith("_finished = ") and "failed_at" in s:
                bad.append(s)
        check("没有『_finished 含 failed_at』的写法", not bad, bad)
        check("_get_failed_rooms 已定义", "def _get_failed_rooms(" in src, None)

        print("\n=== 判据 D：select 返回体带 failed_rooms ===")
        check("源码含 failed_rooms 字段", '"failed_rooms"' in src, None)
        check("源码含 failed_rooms_note 字段", '"failed_rooms_note"' in src, None)

    finally:
        shutil.rmtree(state_dir, ignore_errors=True)

    total = len(_checks)
    passed = sum(1 for c in _checks if c["ok"])
    print("\n=== 汇总：%d/%d 通过 ===" % (passed, total))
    if passed != total:
        for c in _checks:
            if not c["ok"]:
                print("  ✗", c["name"], "->", repr(c["detail"])[:300])
        return 1
    print("C8 回归：全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
