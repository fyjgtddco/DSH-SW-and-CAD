# -*- coding: utf-8 -*-
"""反证：确认 test_regression_wiring_audit.py（接线审计）【真的能抓出问题】。

════════════════════════════════════════════════════════════════════════════
★ 安全设计：全程【不碰仓库文件】，只在临时副本上变异
════════════════════════════════════════════════════════════════════════════
【事故教训】本脚本第一版直接在仓库原文件上做"改坏 → 还原"，
  而还原逻辑没有校验与兜底，结果把 `client.js` 清成了 0 字节。
  虽然已从备份完整恢复，但这种"破坏性验证"本身就是设计缺陷。

现在的做法（结构性消除风险，而不是"小心一点"）：
  ① 把待审目录（plugins/ + 相关 tools/）复制到系统临时目录的一份副本；
  ② 通过环境变量 `DSH_AUDIT_ROOT` 让审计脚本去【副本】里读文件；
  ③ 变异只发生在副本上，仓库文件全程只读；
  ④ 跑完直接删掉整个临时目录 —— 不需要"还原"，因为从没改过真身；
  ⑤ 结束时校验仓库文件的 SHA-256 未变，作为"确实没碰"的证据。

这样即使变异/清理全失败，最坏结果也只是临时目录残留，**不可能损坏仓库**。

运行：python prove_audit_effective.py
"""
import hashlib
import io
import os
import shutil
import subprocess
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)          # engineering/
AUDIT = os.path.join(HERE, "test_regression_wiring_audit.py")


def _copy_tree_for_mutation(tmp):
    """把审计需要读的目录复制到 tmp（副本），返回审计根路径。

    审计脚本在 DSH_AUDIT_ROOT 模式下会去找 <root>/plugins/... 与 <root>/tools/...，
    因此这里按同样结构铺一份。
    """
    os.makedirs(os.path.join(tmp, "tools"), exist_ok=True)
    shutil.copytree(os.path.join(ROOT, "plugins"),
                    os.path.join(tmp, "plugins"),
                    dirs_exist_ok=True)
    # tools：复制审计要读的全部 .py（漏一个就会让副本基准变红 —— 实测踩到：
    #   第一次只复制 5 个，审计 H 读 visual_check.py 时取到空串，基准直接 rc=1）
    for n in ("part_registry.py", "mode2_flow.py", "workflow_gate.py",
              "mode_gate.py", "sw_bridge.py", "visual_check.py", "sw_guard.py"):
        src = os.path.join(HERE, n)
        if os.path.exists(src):
            shutil.copyfile(src, os.path.join(tmp, "tools", n))
    return tmp


def run_audit(audit_root):
    """在指定 root 上跑审计，返回 (returncode, 结论行)。"""
    env = dict(os.environ)
    env["DSH_AUDIT_ROOT"] = audit_root
    # ⚠️ 必须 encoding + errors="replace"：子进程 stdout 在本机是 GBK，
    #    按 utf-8 硬解会在 reading 线程里抛 UnicodeDecodeError（实测踩到）。
    p = subprocess.run([sys.executable, AUDIT], capture_output=True,
                       text=True, encoding="utf-8", errors="replace",
                       cwd=HERE, env=env)
    last = ""
    for l in (p.stdout or "").splitlines():
        if "审计结果" in l:
            last = l.strip()
            break
        if "未通过项" in l:
            last = l.strip()
    return p.returncode, last


def mutate_in_copy(root, rel_path, pairs, label):
    """在【副本】里做变异（绝不碰仓库），跑审计，断言它变红。

    Returns: True=审计抓住了, False=没抓住（覆盖不足）, None=跳过(锚点未命中)
    """
    path = os.path.join(root, rel_path)
    if not os.path.exists(path):
        print("  [SKIP] %s：副本里找不到 %s" % (label, rel_path))
        return None
    with io.open(path, encoding="utf-8") as f:
        orig = f.read()
    if not orig:
        print("  [SKIP] %s：副本文件为空" % label)
        return None
    cur = orig
    hit = 0
    for a, b in pairs:
        if a in cur:
            cur = cur.replace(a, b)
            hit += 1
    if hit == 0:
        print("  [SKIP] %s：变异锚点未命中（代码可能已改）" % label)
        return None
    # 写入副本（想怎么写都行 —— 它马上会被整个删掉）
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(cur)
    rc, msg = run_audit(root)
    caught = (rc != 0)
    print("  [%s] %s -> %s" % ("PASS" if caught else "FAIL", label, msg))
    # 立刻把副本恢复，便于同一 root 上跑下一组变异
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(orig)
    return caught


def main():
    print("=" * 72)
    print("反证：接线审计必须能抓出问题（否则它只是摆设）")
    print("=" * 72)

    repo_client = os.path.join(ROOT, "plugins", "dsh-engineering-ui",
                               "lib", "client.js")
    if not os.path.exists(repo_client):
        print("找不到仓库 client.js：%s" % repo_client)
        return 1
    with io.open(repo_client, "rb") as f:
        repo_hash_before = hashlib.sha256(f.read()).hexdigest()
    repo_size_before = os.path.getsize(repo_client)
    print("仓库 client.js：%d bytes  sha256=%s…" % (repo_size_before,
                                                    repo_hash_before[:12]))
    print("（本次反证只会写临时副本；结束时校验仓库文件哈希不变）")
    print()

    tmp = tempfile.mkdtemp(prefix="dsh_audit_proof_")
    try:
        _copy_tree_for_mutation(tmp)

        rc, msg = run_audit(tmp)
        print("  [基准] 未改动副本 -> %s (rc=%d)" % (msg, rc))
        if rc != 0:
            print("  基准不全绿，反证无意义 —— 先修审计")
            return 1

        CLIENT_REL = os.path.join("plugins", "dsh-engineering-ui", "lib", "client.js")
        M2_REL = os.path.join("tools", "mode2_flow.py")

        results = []
        results.append(mutate_in_copy(tmp, CLIENT_REL, [
            ("if (!preChecked) {\n          var _sess = session",
             "if (false) {\n          var _sess = session"),
        ], "反证1 降级卡片判定被短路 if(false)（审计 A）"))

        results.append(mutate_in_copy(tmp, CLIENT_REL, [
            ("var disposeAuto = startEngLimitAutoTrigger();",
             "var disposeAuto = null;"),
        ], "反证2 自动弹出触发源无调用点（审计 C）"))

        results.append(mutate_in_copy(tmp, M2_REL, [
            ('av.get("parts_with_fingerprint", 0)',
             'av.get("fingerprintsZZZ", 0)'),
        ], "反证3 读不存在的返回键（审计 D）"))

        # 反证4：摘掉"总理统一核实"的调用（审计 H：定义未调用）
        results.append(mutate_in_copy(tmp, M2_REL, [
            ("_pv = prime_verify_all(work_dir=work_dir,",
             "_pv = None\n        if False:\n            _pv = prime_verify_all(work_dir=work_dir,"),
        ], "反证4 总理统一核实调用被摘掉（审计 H）"))

        # 反证5：docx 强制检查不再挂进总闸（审计 H：接线）
        results.append(mutate_in_copy(tmp, M2_REL, [
            ("_docx = _check_docx_deliverable(work_dir=work_dir,",
             "_docx = {'ok': True}\n        if False:\n            _docx = _check_docx_deliverable(work_dir=work_dir,"),
        ], "反证5 docx 强制检查被摘掉（审计 H）"))

        ran = [r for r in results if r is not None]
        caught = [r for r in ran if r]
        print()
        print("反证命中 %d / %d" % (len(caught), len(ran)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ★ 关键校验：仓库文件必须【一个字节都没变】
    with io.open(repo_client, "rb") as f:
        repo_hash_after = hashlib.sha256(f.read()).hexdigest()
    repo_size_after = os.path.getsize(repo_client)
    print()
    print("仓库 client.js 事后：%d bytes  sha256=%s…" %
          (repo_size_after, repo_hash_after[:12]))
    if repo_hash_after != repo_hash_before or repo_size_after != repo_size_before:
        print("★★ 仓库文件被改动！这是严重问题（反证本不该碰真身）")
        return 1
    print("★ 仓库文件哈希未变 —— 反证全程零风险 ✅")

    if not ran:
        print("★ 没有任何反证真正执行（锚点全未命中）—— 检查代码是否已大改")
        return 1
    if len(caught) < len(ran):
        print("★ 有 %d 组变异未被审计抓到 —— 审计覆盖不足" % (len(ran) - len(caught)))
        return 1
    print("★ 审计有效：%d 组变异全被抓，且仓库零改动" % len(caught))
    return 0


if __name__ == "__main__":
    sys.exit(main())
