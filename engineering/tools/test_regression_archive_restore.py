# -*- coding: utf-8 -*-
"""【R8 / B-20·B-25 回归】归档 → 回填必须按指纹仲裁，绝不能"原样恢复"。

★ 锁定的真实事故（2026-10-30 四工位转台 15h）：
  · B-20：同一房间 3 轮 fork 并存互相搬文件，"归档原样恢复"把【已过门】的
    L200/L230 版覆盖成【最旧】的 L114/L109 版，全盘无第三份副本；
    唯一暴露信号是副轴看图门 passed=false —— 错误几何差点静默进总装。
  · B-25：转台房报 done 但 DSH_转台轴.SLDPRT 不在盘上（被 fail 归档，
    重开 prompt 写"只跑物理不改建模"，没人放回）→ room-end 前缺件。

本测试锁定：
  ① 归档必须留清单（含 sha1/大小/mtime），否则回填只能靠猜；
  ② archive-list 能列出批次与指纹；
  ③ 目标不存在 → 回填；
  ④ 归档件比现有文件【更旧】→ 默认【拒绝覆盖】（这就是 B-20 的覆辙）；
  ⑤ --force 才允许覆盖，且必须先把现有文件备份出来；
  ⑥ 内容完全一致 → 跳过，不产生无谓写入。

隔离：DSH_STATE_DIR + 临时工作目录，不碰真实状态。
运行：python test_regression_archive_restore.py   退出码 0=通过。
"""
import json
import os
import sys
import tempfile
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = tempfile.mkdtemp(prefix="dsh_arch_")
WORK = tempfile.mkdtemp(prefix="dsh_arch_work_")
os.environ["DSH_STATE_DIR"] = STATE
os.environ.pop("DSH_ENGINEERING_ROOT", None)
sys.path.insert(0, HERE)

import mode_gate as mg  # noqa: E402

PASS = []
FAIL = []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (("  -> " + str(detail)[:400]) if (detail and not cond) else ""))


def wj(name, obj):
    p = os.path.join(STATE, name)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    return p


def write_file(p, data, mtime=None):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


def main():
    ROOM = "双输出轴房"
    parts = os.path.join(WORK, "parts")
    os.makedirs(parts, exist_ok=True)

    # 让 mode_gate 把 WORK 当工作目录
    wj("mode_state.json", {"mode": "2", "rooms": {}, "subagents": {},
                           "work_dir": WORK})
    mg._default_work_dir = lambda: WORK   # 测试内固定工作目录

    print("\n=== ① 归档必须留下带指纹的清单 ===")
    part = write_file(os.path.join(parts, "DSH_副轴.SLDPRT"),
                      b"NEW-VERSION-200", mtime=time.time())
    mat = write_file(os.path.join(parts, "DSH_副轴.SLDPRT.material.json"),
                     b'{"material":"45"}', mtime=time.time())
    mg._save_artifact_registry({ROOM: [{"path": part, "name": "DSH_副轴"},
                                       {"path": mat, "name": "DSH_副轴"}]})
    arc = mg._archive_room_artifacts(ROOM)
    check("归档成功", arc.get("ok") is True, arc)
    check("确实搬走了文件", len(arc.get("moved") or []) >= 1, arc.get("moved"))
    mf = os.path.join(arc.get("target") or "", mg.ARCHIVE_MANIFEST_NAME)
    check("★ 归档目录里生成了清单文件", os.path.isfile(mf), mf)
    man = json.load(open(mf, encoding="utf-8")) if os.path.isfile(mf) else {}
    files = man.get("files") or []
    check("★ 清单记录了文件指纹", bool(files), files)
    check("★ 指纹含 sha1_16", any(f.get("sha1_16") for f in files), files[:1])
    check("★ 指纹含 size/mtime",
          all(f.get("size") is not None and f.get("mtime") is not None
              for f in files), files[:1])
    check("原位置已被搬空", not os.path.exists(part))

    print("\n=== ② archive-list 能列出批次与指纹 ===")
    al = mg.cmd_archive_list(room=ROOM)
    check("archive-list ok", al.get("ok") is True, al)
    check("★ 至少一个归档批次", (al.get("count") or 0) >= 1, al.get("count"))
    b0 = (al.get("batches") or [{}])[0]
    check("批次带 has_manifest", b0.get("has_manifest") is True, b0)

    print("\n=== ③ 目标不存在 → 回填 ===")
    r = mg.cmd_archive_restore(ROOM)
    check("restore ok", r.get("ok") is True, r)
    check("★ 缺件被回填", os.path.exists(part), r.get("restored"))
    check("回填内容正确",
          open(part, "rb").read() == b"NEW-VERSION-200")
    check("★ 回填结果带目标指纹", any(x.get("target") for x in
                                     (r.get("restored") or [])), r.get("restored"))

    print("\n=== ④ 归档件更旧 → 默认拒绝覆盖（B-20 的覆辙）===")
    # 把盘上文件换成"更新的版本"，且 mtime 明显晚于归档件
    newer = b"EVEN-NEWER-300"
    write_file(part, newer, mtime=time.time() + 3600)
    r2 = mg.cmd_archive_restore(ROOM)
    check("★ 归档件更旧时【不覆盖】", open(part, "rb").read() == newer,
          open(part, "rb").read()[:40])
    skipped = r2.get("skipped") or []
    check("★ 跳过原因明确写了「更旧」",
          any("更旧" in str(s.get("why") or "") for s in skipped), skipped)

    print("\n=== ⑤ --force 才覆盖，且先备份 ===")
    r3 = mg.cmd_archive_restore(ROOM, force=True)
    check("★ --force 后允许覆盖", open(part, "rb").read() == b"NEW-VERSION-200",
          open(part, "rb").read()[:40])
    bd = r3.get("backup_dir")
    check("★ 覆盖前做了备份目录", bool(bd) and os.path.isdir(bd or ""), bd)
    check("★ 备份里保留了被覆盖的新版",
          bool(bd) and os.path.isfile(os.path.join(bd, os.path.basename(part))),
          bd)

    print("\n=== ⑥ 内容一致 → 跳过，不做无谓写入 ===")
    r4 = mg.cmd_archive_restore(ROOM)
    check("内容相同则跳过",
          any("一致" in str(s.get("why") or "") for s in (r4.get("skipped") or [])),
          r4.get("skipped"))

    print("\n=== ⑦ dry-run 不落盘 ===")
    os.remove(part)
    r5 = mg.cmd_archive_restore(ROOM, dry_run=True)
    check("dry-run 报告将要回填", len(r5.get("restored") or []) >= 1, r5)
    check("★ dry-run 不真的写盘", not os.path.exists(part), part)

    print("\nPASS=%d FAIL=%d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：" + ", ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
