# -*- coding: utf-8 -*-
"""test_regression_20261111_debug_part_and_selftest.py — 两项回归

覆盖本轮修的两个缺陷：

A. 调试/测试件混进交付台账（P3 的真实形态）
   现场（20261111_131815_580E38FA）：
     台账 15 条里 6 条是调试件 —— 测试块、DIAG_下箱座_debug、DIAG_min_block、
     DIAG_rightplane、DIAG_manualcut、DIAG_manualcut2；
     而 parts/ 目录下这些文件【早已不存在】（收尾时已清理）。
   危害：check_reusable 读到"有记录、文件不在"；相邻尺寸核对会把它们
     拉进 pairs 产出假冲突，把真交付件的冲突淹没掉；导出 docx 也会带上它们。
   本轮修法：
     ① 新增 _looks_like_debug_part() 识别特征；
     ② add_part 登记时打 debug_part 结构化 warning（不硬拒，避免逼人绕过模块）；
     ③ _check_core 把它们排除在 pairs/dangling/one_way/self_loops 之外，
        但通过 debug_parts/debug_notes 如实回显。

B. sw_bridge selftest 的 A2 / A3a 长期跑红
   现场：验收报告把它记成"annotate 离线自检 2 项未合"。
   根因【不在被测代码，在自检自己的期望表】：
     · _sig_exp 漏登记 InsertModelAnnotations4（8 参，官方 2024 FCS 起就有）；
     · A3a 硬编码了一份已过期的期望顺序。
   本轮修法：补 ...4 进期望表；A3a 改为守住三条语义契约
     （首选 InsertModelDimensions / ...4→3→2→无后缀 / AutoDimension 垫底），
     不再抄一份会漂移的完整列表。

运行：python test_regression_20261111_debug_part_and_selftest.py
"""
import io
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import part_registry as PR

PASS = []
FAIL = []

#: 现场 6 个真实调试件名（大小写/中文都覆盖到）
DEBUG_NAMES = ["测试块", "DIAG_下箱座_debug", "DIAG_min_block",
               "DIAG_rightplane", "DIAG_manualcut", "DIAG_manualcut2"]
#: 现场真实交付件名 —— 一个都不许被误判
REAL_NAMES = ["输出轴", "大齿轮", "输入轴平键", "大齿轮平键", "输出轴平键",
              "齿轮轴", "下箱座", "上箱盖", "输入透盖", "输入闷盖",
              "输出透盖", "输出闷盖"]


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print("  [PASS] %s" % name)
    else:
        FAIL.append((name, detail))
        print("  [FAIL] %s %s" % (name, detail))


def sec(t):
    print("\n" + "=" * 70)
    print(t)
    print("=" * 70)


def main():
    sec("A1. 调试件识别：现场 6 个必须全部命中")
    for n in DEBUG_NAMES:
        hit = PR._looks_like_debug_part(n)
        check("命中调试件 %-22s -> %r" % (n, hit), bool(hit), "返回 None")

    sec("A2. ★ 交付件零误报（误判会把真零件排除掉，比漏判更危险）")
    for n in REAL_NAMES:
        hit = PR._looks_like_debug_part(n)
        check("交付件 %-12s 不命中" % n, hit is None, "误判为 %r" % (hit,))

    sec("A3. 其它常见调试命名也要能认出")
    for n in ["probe_planes", "probe_big_disc", "debug_v4", "tmp_block",
              "临时件", "草稿件", "底座_test", "盘_bak"]:
        check("识别 %-16s" % n, bool(PR._looks_like_debug_part(n)))

    sec("A4. ★ 真实台账：6 个调试件被排除在 pairs 之外，但如实回显")
    real_task = r"C:\Users\j1877\Desktop\test\20261111_131815_580E38FA"
    if os.path.isdir(real_task):
        r = PR.check_adjacency_consistency(work_dir=real_task)
        got = sorted(r.get("debug_parts") or [])
        check("★ debug_parts 报出 6 个调试件", len(got) == 6, "实际=%r" % (got,))
        check("名单与现场一致",
              got == sorted(DEBUG_NAMES), "实际=%r" % (got,))
        # pairs 里绝不能再出现调试件
        bad = [p for p in (r.get("pairs") or [])
               if any(x in got for x in p.get("parts", []))]
        check("★ 调试件不出现在 pairs 里", not bad, "污染=%r" % (bad,))
        # 真实件必须还在
        real_pairs = [tuple(p.get("parts")) for p in (r.get("pairs") or [])]
        check("真实零件配对仍在（>=8 对）", len(real_pairs) >= 8,
              "实际=%d 对：%r" % (len(real_pairs), real_pairs[:4]))
        check("debug_count 字段可用", r.get("debug_count") == 6,
              repr(r.get("debug_count")))
    else:
        print("  [SKIP] 真实任务目录不存在，跳过 A4（不算失败）")

    sec("A5. 登记时打 debug_part 警告（不硬拒，但必须看得见）")
    wd = tempfile.mkdtemp(prefix="dbg_part_")
    try:
        # 用一个真实存在的文件当载体（登记不要求文件必须是真零件）
        carrier = os.path.join(wd, "DIAG_probe_x.SLDPRT")
        with io.open(carrier, "wb") as f:
            f.write(b"x" * 16)
        rec = {"name": "DIAG_probe_x", "room": "壳体", "file": carrier}
        r = PR.add_part(rec, work_dir=wd, reason="调试件登记验证", skip_verification=True)
        check("登记本身不被硬拒（允许调试期查重）", r.get("ok") is True,
              repr(r)[:200])
        kinds = [w.get("kind") for w in (r.get("warnings") or []) if isinstance(w, dict)]
        check("★ 给出 debug_part_in_registry 警告",
              "debug_part_in_registry" in kinds, "warnings=%r" % (kinds,))
        w = [x for x in (r.get("warnings") or [])
             if isinstance(x, dict) and x.get("kind") == "debug_part_in_registry"]
        if w:
            check("警告里带命中特征", bool(w[0].get("matched")), repr(w[0])[:200])
    finally:
        shutil.rmtree(wd, ignore_errors=True)

    sec("B. sw_bridge selftest：A2 / A3a 必须转绿")
    try:
        import sw_bridge as SB
        cands = SB._annotation_api_candidates(None)
        apis = []
        for a, args in cands:
            if a not in apis:
                apis.append(a)
        check("★ 候选链含 InsertModelAnnotations4", "InsertModelAnnotations4" in apis,
              repr(apis))
        # A2 语义：每个 API 的参数个数与官方一致
        sig = {"InsertModelDimensions": 1, "InsertModelAnnotations4": 8,
               "InsertModelAnnotations3": 6, "InsertModelAnnotations2": 6,
               "InsertModelAnnotations": 4, "AutoDimension": 5}
        bad = [(a, len(args)) for a, args in cands
               if a in sig and len(args) != sig[a]]
        check("★ 参数个数全部符合官方签名", not bad, "不符=%r" % (bad,))
        # A3a 三条语义契约
        check("★ 首位是 InsertModelDimensions", apis and apis[0] == "InsertModelDimensions",
              repr(apis[:2]))
        ann = [a for a in apis if a.startswith("InsertModelAnnotations")]
        expect = ["InsertModelAnnotations4", "InsertModelAnnotations3",
                  "InsertModelAnnotations2", "InsertModelAnnotations"]
        check("★ 注解类按新→旧排列（4→3→2→无后缀）",
              ann == [a for a in expect if a in ann], repr(ann))
        check("★ AutoDimension 垫底", apis and apis[-1] == "AutoDimension",
              repr(apis[-2:]))
    except Exception as e:
        check("sw_bridge 可导入并取候选链", False, repr(e))

    sec("B2. 实际跑一遍 selftest，必须全绿")
    try:
        import subprocess
        import json as _json
        here = os.path.dirname(os.path.abspath(__file__))
        p = subprocess.run([sys.executable, os.path.join(here, "sw_bridge.py"), "selftest"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=180)
        out = (p.stdout or "").strip()
        # selftest 输出是 JSON（末尾可能带说明）
        i = out.find("{")
        j = out.rfind("}")
        d = _json.loads(out[i:j + 1]) if i >= 0 and j > i else {}
        failed = d.get("failed") or []
        check("★ selftest failed 为空（原先 [A2, A3a]）", failed == [],
              "failed=%r" % (failed,))
        check("★ selftest passed == 16", d.get("passed") == 16,
              "passed=%r" % (d.get("passed"),))
    except Exception as e:
        check("selftest 可执行", False, repr(e))

    print("\n" + "=" * 70)
    print("汇总：PASS=%d  FAIL=%d" % (len(PASS), len(FAIL)))
    print("=" * 70)
    if FAIL:
        for n, d in FAIL:
            print("  FAILED: %s %s" % (n, d))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
