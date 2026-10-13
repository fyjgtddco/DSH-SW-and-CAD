# -*- coding: utf-8 -*-
"""回归测试：★ 截图指纹必须用【全文件】哈希，不得用"前 1MB"判重复视图

════════════════════════════════════════════════════════════════════════════
锁定的真实缺陷（2026-10-13 实机复核发现，非推演）
════════════════════════════════════════════════════════════════════════════
现象：`visual_check.capture_views()` 把一次**完全正常**的四视图采集判成失败：

    capture_views ok = False
    error = 检出【重复视图】：front、top（内容完全相同）
            —— 说明视图没有真正切换（常见根因是 ShowNamedView2 的 ViewId 传了 0）

但独立复核（对同一批产物直接算 sha1）显示四张图**互不相同**：

    front  sha1=55c0009dec75
    top    sha1=52951e5ae8d1
    right  sha1=3c74e6e51edc
    iso    sha1=2988bdc13b7e     ← 4 个唯一值

根因：`file_probe()` 用 `_file_sha1(p, limit=1<<20)`（**只哈希前 1MB**）
      作为 `sha1_12`，而重复检测直接拿它比对。
      SaveBMP 产出的 BMP 是 4320054 字节，**BMP 像素自底向上存储** ——
      文件前段是文件头 + 图像【底部】的行。front 与 top 整体完全不同，
      但底部都是空白，于是前 1MB 恰好相同：

          full  sha1 : front=55c0009dec75  top=52951e5ae8d1   ← 不同
          first1MB   : front=350a1b10f2db  top=350a1b10f2db   ← 相同 → 假阳性

后果：正常采集被拦（拒绝放行），且错误信息把原因误导向"ViewId 传了 0"
      （而 ViewId 是对的）—— 会让人去修一个没坏的地方。

修复：`file_probe` 额外给出**全文件**指纹 `sha1_full_12`，
      重复视图检测改用全文件指纹；`sha1_12` 仅保留作轻量变更指纹。

本测试用**纯本地构造的文件**复现该场景（不依赖 SolidWorks），确保不回归。
运行：python test_regression_view_hash.py
"""
import io
import os
import re
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import visual_check as vc  # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []


def check(label, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] %s" % label)
    else:
        FAIL += 1
        FAILURES.append(label)
        print("  [FAIL] %s  %s" % (label, extra))


def section(t):
    print("\n" + "=" * 70)
    print(t)
    print("=" * 70)


def make_bmp_like(path, head_bytes, tail_bytes, total=3 * 1024 * 1024):
    """造一个"前段相同、后段不同"的文件，模拟 BMP 自底向上存储的真实情形。

    head_bytes : 前 1MB 的内容（模拟"底部都是空白"→ 相同）
    tail_bytes : 之后的内容（模拟真正的图像差异 → 不同）
    """
    with open(path, "wb") as f:
        f.write(head_bytes * (1024 * 1024 // max(1, len(head_bytes))))
        rest = total - 1024 * 1024
        f.write(tail_bytes * (rest // max(1, len(tail_bytes)) + 1))


def main():
    d = tempfile.mkdtemp(prefix="viewhash_")
    try:
        # ═══════════════════════════════════════════════════════════
        section("1. ★ file_probe 必须同时给出【前1MB】与【全文件】两个指纹")
        a = os.path.join(d, "a.bin")
        make_bmp_like(a, b"\x00" * 4096, b"AAAA")
        pr = vc.file_probe(a)
        check("file_probe 存在", isinstance(pr, dict), type(pr))
        check("★ 返回 sha1_12（前1MB 轻量指纹）", bool(pr.get("sha1_12")),
              pr.get("sha1_12"))
        check("★ 返回 sha1_full_12（全文件指纹）", bool(pr.get("sha1_full_12")),
              pr.get("sha1_full_12"))
        check("两者都是 12 位十六进制",
              len(str(pr.get("sha1_12"))) == 12
              and len(str(pr.get("sha1_full_12"))) == 12,
              (pr.get("sha1_12"), pr.get("sha1_full_12")))

        # ═══════════════════════════════════════════════════════════
        section("2. ★★ 复现核心 bug：前1MB 相同但全文件不同")
        b = os.path.join(d, "b.bin")
        # 与 a 完全相同的前 1MB，但尾部不同
        make_bmp_like(b, b"\x00" * 4096, b"BBBB")
        pa, pb = vc.file_probe(a), vc.file_probe(b)
        check("★ 前 1MB 指纹相同（这正是当初误报的来源）",
              pa["sha1_12"] == pb["sha1_12"],
              "%s vs %s" % (pa["sha1_12"], pb["sha1_12"]))
        check("★★ 全文件指纹不同（真实内容确实不同）",
              pa["sha1_full_12"] != pb["sha1_full_12"],
              "%s vs %s" % (pa["sha1_full_12"], pb["sha1_full_12"]))

        # ═══════════════════════════════════════════════════════════
        section("3. ★ 重复视图检测必须用全文件指纹（真执行，不只看源码字符串）")
        # ⚠️ 教训（两次踩）：本测试前两版都【无法区分】"用 sha1_12"与
        #    "用 sha1_full_12" 两种写法 ——
        #      第一版只查源码字符串；
        #      第二版自己复刻了一遍判据逻辑（复刻的那份永远用全文件指纹，
        #      所以把源码改坏它照样全绿）。
        #    正确的做法是【从源码里抽出那段真实判据去执行】，
        #    这样源码一改，执行的逻辑就跟着变 —— 反证才有区分力。
        src_lines = io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                         "visual_check.py"), encoding="utf-8").read().split("\n")
        # 定位真实实现：`d = <某表达式>` 且下一行是 digests.setdefault(d, [])
        expr = None
        for _i, _l in enumerate(src_lines):
            if re.match(r"^\s*d\s*=\s*_rec_v\.get\(", _l) or \
               re.match(r"^\s*d\s*=.*sha1.*$", _l):
                # 确认下一两行确实在做分组
                _nxt = "\n".join(src_lines[_i + 1:_i + 3])
                if "digests.setdefault" in _nxt:
                    expr = _l.split("=", 1)[1].strip()
                    break
        check("★ 能从源码抽到重复检测的真实判据表达式", bool(expr), expr)

        def dup_of_real(records):
            """执行【源码里的真实表达式】做分组 —— 改源码即改行为。"""
            if not expr:
                return None
            digests = {}
            for _v, _rec_v in records.items():
                _d = eval(expr, {"__builtins__": {}}, {"_rec_v": _rec_v})
                if _d:
                    digests.setdefault(_d, []).append(_v)
            return [vs for vs in digests.values() if len(vs) > 1]

        recs = {
            "front": {"sha1_12": pa["sha1_12"], "sha1_full_12": pa["sha1_full_12"]},
            "top":   {"sha1_12": pb["sha1_12"], "sha1_full_12": pb["sha1_full_12"]},
        }
        check("★★ 前1MB相同但全文件不同 → 真实判据不判重复（旧写法会误报）",
              dup_of_real(recs) == [], dup_of_real(recs))

        # 反向自检：证明本断言确实有区分力 —— 用前1MB指纹会误报
        _old = {}
        for _v, _r in recs.items():
            _old.setdefault(_r["sha1_12"], []).append(_v)
        check("★ 反向自检：用前1MB指纹会误报重复（证明断言能区分两种写法）",
              [vs for vs in _old.values() if len(vs) > 1] != [], _old)

        # 内容真正相同 → 必须判重复（修复不能把检测功能放宽掉）
        same = {
            "front": {"sha1_12": pa["sha1_12"], "sha1_full_12": pa["sha1_full_12"]},
            "top":   {"sha1_12": pa["sha1_12"], "sha1_full_12": pa["sha1_full_12"]},
        }
        check("★ 内容真相同 → 真实判据仍判重复（检测未被放宽）",
              dup_of_real(same) != [], dup_of_real(same))

        # 源码层记录取值顺序（辅助断言，不作为主判据）
        i = "\n".join(src_lines).find("重复视图检测")
        seg = "\n".join(src_lines)[i:i + 2600] if i > 0 else ""
        check("有重复视图检测逻辑", bool(seg))

        # ═══════════════════════════════════════════════════════════
        section("4. 真内容相同的文件仍必须被判为重复（修复不能放宽）")
        # 两个字节完全相同的文件 → 不论哪个指纹都必须相同
        c = os.path.join(d, "c.bin")
        make_bmp_like(c, b"\x00" * 4096, b"AAAA")   # 与 a 完全一致
        pc = vc.file_probe(c)
        check("★ 内容相同的文件：全文件指纹也相同（重复检测仍能工作）",
              pa["sha1_full_12"] == pc["sha1_full_12"],
              "%s vs %s" % (pa["sha1_full_12"], pc["sha1_full_12"]))

        # ═══════════════════════════════════════════════════════════
        section("5. 真实 .bmp 产物（若存在）自检")
        # 若之前实机跑的产物还在，顺便验证；不在则跳过（不算失败）
        cands = []
        for root in (os.environ.get("TEMP", ""), d):
            if not root or not os.path.isdir(root):
                continue
            for dp, _dn, fn in os.walk(root):
                for f in fn:
                    if f.lower().endswith(".bmp"):
                        cands.append(os.path.join(dp, f))
                if len(cands) > 20:
                    break
        if cands:
            from collections import defaultdict
            by_full, by_head = defaultdict(list), defaultdict(list)
            for p in cands[:20]:
                pr2 = vc.file_probe(p)
                if pr2.get("sha1_full_12"):
                    by_full[pr2["sha1_full_12"]].append(os.path.basename(p))
                if pr2.get("sha1_12"):
                    by_head[pr2["sha1_12"]].append(os.path.basename(p))
            print("      实测样本 %d 个 .bmp" % len(cands[:20]))
            print("      全文件指纹分组数 = %d，前1MB 指纹分组数 = %d"
                  % (len(by_full), len(by_head)))
            check("★ 全文件指纹的分组数 >= 前1MB 的分组数（全文件更细）",
                  len(by_full) >= len(by_head),
                  "full=%d head=%d" % (len(by_full), len(by_head)))
        else:
            print("      （无 .bmp 样本，跳过该项；不算失败）")

    finally:
        shutil.rmtree(d, ignore_errors=True)

    print("\n" + "=" * 70)
    print("汇总：PASS=%d  FAIL=%d" % (PASS, FAIL))
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  - " + f)
    print("=" * 70)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
