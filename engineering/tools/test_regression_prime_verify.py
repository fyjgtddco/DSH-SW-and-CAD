#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回归：交付前总闸的【统一核实】判据（原「总理子代理统一核实」位置）。

── 这个文件为什么被整份重写 ──────────────────────────────────────────────
它原先测的是 prime_verify_all：由总理子代理从磁盘重算台账、邻接、未决记录、
指纹，得出 verified=true 才允许结束。用户要求取消该角色后，同一批判据
搬到了【外部监控器】的 verify-task 里，判定项完全一致（甚至更严）：

  | 原 prime_verify_all 判据 | 现在 verify-task 里对应项 |
  |---|---|
  | 零件台账非空（空 → fail-closed） | part_registry 审计 intact_count |
  | 相邻尺寸一致性（有 mismatch → 拒） | check_adjacency_consistency |
  | 未决记录（open → 拒） | list_unresolved |
  | 逐零件 SHA-256 防篡改 | part_registry.audit_verification |
  | 结论可复现（digest 落盘） | 监控器持久化状态机 + revision |

所以本文件现在的职责是：确认【交付总闸确实在问监控器】，
并且"监控器说没过 → 拒绝交付"，而不是"某个代理说自己核实过了"。

三条断言：
  ① pre_delivery_check 源码里必须出现监控器 verify-task 调用；
  ② 监控器不可达时 fail-closed（不是跳过检查）；
  ③ 返回体里带 monitor_verification（可审计），不再带 prime_verification。

运行：python test_regression_prime_verify.py
"""
import inspect
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import mode2_flow as mf

PASS = 0
FAIL = 0
FAILS = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] " + name)
    else:
        FAIL += 1
        FAILS.append(name)
        print("  [FAIL] " + name + ("  -> " + str(detail) if detail else ""))


def section(t):
    print("\n" + "=" * 70)
    print(t)
    print("=" * 70)


def main():
    section("1. 交付总闸必须向【监控器】要核实结论，而不是信任何代理的自述")
    src = inspect.getsource(mf.pre_delivery_check)
    check("pre_delivery_check 调用监控器 verify-task",
          "verify-task" in src, src[:300])
    check("pre_delivery_check 不再引用 prime_verify_all",
          "prime_verify_all" not in src)
    check("参数名已从 require_primes 改为 require_monitor",
          "require_monitor" in inspect.signature(mf.pre_delivery_check).parameters)
    check("旧参数名 require_primes 仍被接受（向后兼容）",
          "require_primes" in inspect.signature(mf.pre_delivery_check).parameters)
    check("返回体带 monitor_verification 字段",
          "monitor_verification" in src)
    check("返回体不再带 prime_verification",
          "prime_verification" not in src)

    section("2. 监控器不可达 → fail-closed（绝不跳过核实）")
    # 隔离状态目录：没有 sw_monitor.json = 未接入
    import tempfile
    old = os.environ.get("DSH_STATE_DIR")
    tmp = tempfile.mkdtemp(prefix="pd_")
    try:
        os.environ["DSH_STATE_DIR"] = tmp
        import importlib
        import sw_monitor_client as mc
        importlib.reload(mc)
        try:
            mc.require_ready()
            check("未接入时 require_ready 抛错", False, "居然没抛")
        except Exception as e:
            check("未接入时 require_ready 抛错", True)
            check("错误码是 MONITOR_NOT_CONFIGURED",
                  getattr(e, "code", "") == "MONITOR_NOT_CONFIGURED",
                  getattr(e, "code", ""))
        # 直接跑总闸：十阶段/编制都没做，blockers 必然非空；
        #   关键是其中必须有"监控器核实失败"这一条 —— 说明它 fail-closed。
        r = mf.pre_delivery_check(work_dir=tmp)
        joined = "\n".join(r.get("blockers") or [])
        check("总闸 ok=False", r.get("ok") is False)
        check("blockers 里明确写了监控器核实失败",
              "监控器" in joined and ("核实" in joined or "verify" in joined),
              joined[:400])
    finally:
        if old is None:
            os.environ.pop("DSH_STATE_DIR", None)
        else:
            os.environ["DSH_STATE_DIR"] = old
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    section("3. 旧实现必须彻底消失（防止被悄悄加回来）")
    for gone in ("prime_verify_all", "prime_verify_status",
                 "check_prime_consistency", "_persist_consistency",
                 "_persist_verify", "prime_reports"):
        check("mode2_flow 不再有 %s" % gone, not hasattr(mf, gone))

    section("4. 相邻尺寸 / 台账审计仍然在（能力没丢，只是换了执行者）")
    import part_registry as pr
    for fn in ("audit_verification", "list_unresolved",
               "check_adjacency_consistency"):
        check("part_registry 仍有 %s" % fn, callable(getattr(pr, fn, None)))

    section("汇总")
    print("  PASS=%d  FAIL=%d" % (PASS, FAIL))
    if FAILS:
        print("\n失败明细：")
        for x in FAILS:
            print("  - " + x)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
