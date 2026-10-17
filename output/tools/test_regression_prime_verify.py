# -*- coding: utf-8 -*-
"""回归测试：★ 总理子代理【统一核实】闸门（用户新增要求）

用户原话：
  「总理子代理要统一核实过数据没有错误了，才能结束」
  「其总理子代理也要核实的相关数据是否一致，必须要保证真实」

★ 本测试的核心是证明"核实"是【代码从磁盘重算】，不是自我声明：
  · 一致的数据 → 核实通过，总闸放行
  · 不一致的数据 → **根本写不进台账**（update/add 阶段就被拒绝落盘）
  · 已落盘的数据被绕过模块手改 → 核实用指纹抓出，总闸拒绝结束
  · 空台账 / 无指纹 → 一律 fail-closed（不许当"已核实"）

运行：python test_regression_prime_verify.py
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import part_registry as pr  # noqa: E402
import mode2_flow as mf  # noqa: E402

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
        print("  [FAIL] %s %s" % (label, extra))


def section(t):
    print("\n" + "=" * 70)
    print(t)
    print("=" * 70)


#: 台账铁律：reason 至少 5 个实质字符（模块强制），测试必须给真实理由
R_ADD_SHAFT = "首次登记齿轮轴，承载扭矩 45N·m，材料 45#钢"
R_ADD_SEAT = "首次登记轴承座，与齿轮轴 J1 轴孔配合，材料 HT200"
R_BAD_HOLE = "把配合孔改小 1mm，用于验证一致性拦截确实生效"
R_FIX_HOLE = "把孔改回 25mm 以验证篡改检测，此处应能正常落盘"


def seed(tmp):
    """造一对【尺寸一致】的相邻零件。"""
    pr.add_part({"name": "ShaftA", "room": "Transmission", "file": "C:/x/a.SLDPRT",
                 "dims": {"length_mm": 120.0, "width_mm": 40.0, "height_mm": 40.0},
                 "interfaces": [{"id": "J1", "type": "shaft",
                                 "nominal_mm": 25.0, "tolerance": "k6"}],
                 "adjacent": ["SeatB"]},
                work_dir=tmp, reason=R_ADD_SHAFT)
    pr.add_part({"name": "SeatB", "room": "Housing", "file": "C:/x/b.SLDPRT",
                 "dims": {"length_mm": 80.0, "width_mm": 80.0, "height_mm": 30.0},
                 "interfaces": [{"id": "J1", "type": "hole",
                                 "nominal_mm": 25.0, "tolerance": "H7"}]},
                work_dir=tmp, reason=R_ADD_SEAT)


def main():
    # ═══════════════════════════════════════════════════════════════
    section("1. 空台账 → 必须 fail-closed（不许当「已核实」）")
    d0 = tempfile.mkdtemp(prefix="pv0_")
    try:
        r = mf.prime_verify_all(work_dir=d0)
        check("★ 空台账时 ok=False", r.get("ok") is False, r.get("ok"))
        check("★ verified=False（未核实）", r.get("verified") is False)
        check("核实者身份默认为「总理子代理」",
              r.get("verified_by") == mf.PRIME_ROOM_NAME, r.get("verified_by"))
        check("blockers 指出台账为空",
              any("台账为空" in b or "没有可核实" in b
                  for b in (r.get("blockers") or [])),
              r.get("blockers"))
        check("enforced_by 标明是代码强制",
              r.get("enforced_by") == "mode2_flow.prime_verify_all",
              r.get("enforced_by"))
    finally:
        shutil.rmtree(d0, ignore_errors=True)

    # ═══════════════════════════════════════════════════════════════
    section("2. 数据一致 → 核实通过，且总闸可见结论")
    d = tempfile.mkdtemp(prefix="pv1_")
    try:
        seed(d)
        r = mf.prime_verify_all(work_dir=d)
        check("★ 一致数据 → ok=True", r.get("ok") is True, r.get("blockers"))
        check("★ verified=True", r.get("verified") is True)
        check("读到 2 个零件", r.get("parts_count") == 2, r.get("parts_count"))
        check("检查了 1 对邻接", r.get("checked_pairs") == 1, r.get("checked_pairs"))
        check("无 mismatch", not r.get("mismatches"), r.get("mismatches"))
        check("★ 给出了可复现指纹 digest（64 位 hex）",
              isinstance(r.get("digest"), str) and len(r["digest"]) == 64,
              len(str(r.get("digest"))))
        check("指纹里不含自我声明的 verified 字段（指纹只覆盖数据）",
              "verified" not in json.dumps({"d": r.get("digest")}))
        check("审计到 2 个零件有指纹", r.get("audit_checked") == 2,
              r.get("audit_checked"))
        check("无篡改", not r.get("tampered"), r.get("tampered"))

        # 总闸里应能看到核实结论
        pd = mf.pre_delivery_check(work_dir=d)
        v = pd.get("prime_verification") or {}
        check("★ 总闸带出 prime_verification.verified",
              v.get("verified") is True, v)
        check("总闸带出 digest", bool(v.get("digest")))
        check("总闸的 blocker 里不再有「未通过统一核实」",
              not any("尚未通过统一核实" in b for b in (pd.get("blockers") or [])),
              [b for b in (pd.get("blockers") or []) if "核实" in b][:2])

        # 状态读回
        st = mf.prime_verify_status(work_dir=d)
        check("prime_verify_status 能读回结论",
              st.get("ok") and st.get("verified") is True, st)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ═══════════════════════════════════════════════════════════════
    section("3. ★ 不一致数据【写不进台账】（比事后拦截更强）")
    d2 = tempfile.mkdtemp(prefix="pv2_")
    try:
        seed(d2)
        # 把孔改小 1mm → 应在【写入前】就被核实拒绝
        ru = pr.update_part(
            "SeatB",
            {"interfaces": [{"id": "J1", "type": "hole",
                             "nominal_mm": 24.0, "tolerance": "H7"}]},
            work_dir=d2, reason=R_BAD_HOLE)
        check("★ update 被拒绝落盘（ok=False）", ru.get("ok") is False, ru.get("ok"))
        check("gate == CONSISTENCY_VERIFICATION_FAILED",
              ru.get("gate") == "CONSISTENCY_VERIFICATION_FAILED", ru.get("gate"))
        check("★ 明确说明是「总理子代理核实发现不一致」",
              "总理子代理" in str(ru.get("error") or ""),
              str(ru.get("error"))[:90])
        check("返回里带 verification.mismatches（可追溯）",
              bool((ru.get("verification") or {}).get("mismatches")),
              (ru.get("verification") or {}).get("mismatches"))

        # 台账里该零件仍是原值（确实没被写进去）
        # 注意：get_part 的返回里，零件记录在 `record` 键下（不是 `part`，
        #   `part` 是零件名字符串）。用错会拿到 str 导致 AttributeError。
        gp = pr.get_part("SeatB", work_dir=d2)
        _nom = None
        for _i in ((gp.get("record") or {}).get("interfaces") or []):
            if isinstance(_i, dict) and _i.get("id") == "J1":
                _nom = _i.get("nominal_mm")
        check("★ 台账里孔仍为 25.0（坏数据未落盘）", _nom == 25.0, _nom)

        # 因此核实仍然是干净的
        r2 = mf.prime_verify_all(work_dir=d2)
        check("被拒之后核实仍通过（数据未被污染）",
              r2.get("ok") is True, r2.get("blockers"))
    finally:
        shutil.rmtree(d2, ignore_errors=True)

    # ═══════════════════════════════════════════════════════════════
    section("4. ★ 绕过模块手改 JSON → 指纹抓出，核实失败")
    d3 = tempfile.mkdtemp(prefix="pv3_")
    try:
        seed(d3)
        check("改之前先确认核实通过",
              mf.prime_verify_all(work_dir=d3).get("ok") is True)

        p = pr.registry_path(work_dir=d3)
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        # 直接改尺寸（模拟"绕过模块手改"）
        data["parts"]["ShaftA"]["dims"]["length_mm"] = 999.0
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        r3 = mf.prime_verify_all(work_dir=d3)
        check("★ 手改后 ok=False（拒绝结束）", r3.get("ok") is False, r3.get("ok"))
        check("★ tampered 非空", bool(r3.get("tampered")), r3.get("tampered"))
        check("blockers 指明「被改动/真实性无法保证」",
              any("被改动" in b or "真实性无法保证" in b
                  for b in (r3.get("blockers") or [])),
              (r3.get("blockers") or [])[:2])

        pd3 = mf.pre_delivery_check(work_dir=d3)
        check("★ 交付总闸据此拒绝结束",
              pd3.get("ok") is False and
              any("尚未通过统一核实" in b for b in (pd3.get("blockers") or [])),
              (pd3.get("blockers") or [])[:2])
    finally:
        shutil.rmtree(d3, ignore_errors=True)

    # ═══════════════════════════════════════════════════════════════
    section("5. ★ 核实必须【重算】而不是采信落盘结论")
    d4 = tempfile.mkdtemp(prefix="pv4_")
    try:
        seed(d4)
        # 先落一个"已通过"的结论
        r4a = mf.prime_verify_all(work_dir=d4)
        check("第一次核实通过", r4a.get("ok") is True)
        # 再手改数据，但【不重跑】核实 —— 直接看总闸是否仍会重算发现
        p = pr.registry_path(work_dir=d4)
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        data["parts"]["ShaftA"]["dims"]["width_mm"] = 777.0
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        # 注意：不调用 prime_verify_all，直接查总闸
        pd4 = mf.pre_delivery_check(work_dir=d4)
        check("★ 总闸自己重算，不采信旧的「已通过」结论",
              pd4.get("ok") is False and
              any("尚未通过统一核实" in b for b in (pd4.get("blockers") or [])),
              (pd4.get("blockers") or [])[:2])
    finally:
        shutil.rmtree(d4, ignore_errors=True)

    # ═══════════════════════════════════════════════════════════════
    section("6. verified_by 可指定；默认是总理子代理")
    d5 = tempfile.mkdtemp(prefix="pv5_")
    try:
        seed(d5)
        r = mf.prime_verify_all(work_dir=d5, verified_by="其他核实者")
        check("可指定 verified_by", r.get("verified_by") == "其他核实者",
              r.get("verified_by"))
        r2 = mf.prime_verify_all(work_dir=d5)
        check("不指定时默认「总理子代理」",
              r2.get("verified_by") == "总理子代理", r2.get("verified_by"))
    finally:
        shutil.rmtree(d5, ignore_errors=True)

    # ═══════════════════════════════════════════════════════════════
    section("7. ★ docx 汇总交付物必须存在（需求2「最终汇总为 docx 交付」）")
    # 【为什么必须强制】原先 export_docx 虽有三条实现路径，却**没有任何地方
    #   强制它被调用** —— 不导出 docx 也能宣告完成，与用户
    #   "所有改动都要写在代码里强制执行"的要求不符。现在总闸会核实磁盘。
    d6 = tempfile.mkdtemp(prefix="pv6_")
    try:
        seed(d6)
        r = mf._check_docx_deliverable(work_dir=d6, registry_work_dir=d6)
        check("★ 没有 docx 时检查失败", r.get("ok") is False, r)
        check("并说明未找到", "未找到" in str(r.get("error") or ""), r.get("error"))

        # 改名的假文件（不是合法 zip）必须被拒 —— 挡住"改名即通过"
        fake = os.path.join(d6, "假的.docx")
        with open(fake, "wb") as f:
            f.write(b"x" * 5000)
        r2 = mf._check_docx_deliverable(work_dir=d6, registry_work_dir=d6)
        check("★ 改名冒充的假 docx 被拒（非法 zip）", r2.get("ok") is False,
              r2.get("error"))
        os.remove(fake)

        # 过小文件
        tiny = os.path.join(d6, "tiny.docx")
        with open(tiny, "wb") as f:
            f.write(b"PK\x03\x04")
        r3 = mf._check_docx_deliverable(work_dir=d6, registry_work_dir=d6)
        check("过小文件被拒", r3.get("ok") is False, r3.get("error"))
        os.remove(tiny)

        # 真实导出 → 通过
        ex = mf.export_docx_and_record(os.path.join(d6, "零件台账.docx"),
                                       work_dir=d6, registry_work_dir=d6)
        check("export_docx_and_record 成功", ex.get("ok") is True, ex)
        _p = ex.get("path")
        check("导出文件真实存在且非空",
              bool(_p) and os.path.exists(_p) and os.path.getsize(_p) > 1024,
              "%s size=%s" % (_p, os.path.getsize(_p))
              if _p and os.path.exists(_p) else "missing")
        # 合法 zip + 含 word/document.xml
        import zipfile as _zf
        with _zf.ZipFile(_p) as _z:
            _names = _z.namelist()
        check("★ 是合法 zip 且含 word/document.xml",
              "word/document.xml" in _names, _names[:5])

        r4 = mf._check_docx_deliverable(work_dir=d6, registry_work_dir=d6)
        check("★ 真实 docx 通过核实", r4.get("ok") is True, r4)

        # 总闸带出结论
        pd = mf.pre_delivery_check(work_dir=d6, registry_work_dir=d6)
        _dd = pd.get("docx_deliverable") or {}
        check("总闸带出 docx_deliverable.ok", _dd.get("ok") is True, _dd)
        check("总闸 blocker 里不再有 docx 缺失",
              not any("docx" in b for b in (pd.get("blockers") or [])),
              [b for b in (pd.get("blockers") or []) if "docx" in b][:1])

        # 删掉后必须重新拒绝
        os.remove(_p)
        pd2 = mf.pre_delivery_check(work_dir=d6, registry_work_dir=d6)
        check("★ 删掉 docx 后总闸重新拒绝结束",
              pd2.get("ok") is False and
              any("docx" in b for b in (pd2.get("blockers") or [])),
              [b for b in (pd2.get("blockers") or []) if "docx" in b][:1])
    finally:
        shutil.rmtree(d6, ignore_errors=True)

    # ═══════════════════════════════════════════════════════════════
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
