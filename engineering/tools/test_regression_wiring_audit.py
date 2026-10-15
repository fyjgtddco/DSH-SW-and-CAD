# -*- coding: utf-8 -*-
"""系统审计：本次改造是否"主逻辑改了、衍生代码漏改"。

★ 为什么要做这个审计
  本次会话已因"漏改衍生代码"栽过两次：
    · showEngLimitFallbackCard 仍用旧判据（_isEngineeringContext），
      导致"最需要降级的时刻降级卡片也打不开"；
    · SUBPAN_AUTHORITY 拼写错误在函数里修了、CLI 的 argparse default 里漏了。
  这两次都是"同一件事有多个落点，只改了其中一个"。
  因此这里把常见的"多落点"模式做成机器检查，而不是靠人眼。

审计的 5 类问题（每一类都是我实际踩过的）：
  A. 旧判据残留 —— 同一语义的判定，某些落点仍用旧变量/旧函数
  B. 作用域错误 —— 在 apply(ctx) 之外使用 ctx.*（会抛 ReferenceError 被吞）
  C. 定义未调用 —— 实现了功能，但没有任何调用点（等于没做）
  D. 字段名不符 —— 读第三方返回时用了不存在的键（静默拿到 0/None）
  E. 未接入门禁 —— 实现了强制逻辑，但没挂进"拒绝放行"的检查链

运行：python _audit_wiring.py
"""
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# ══════════════════════════════════════════════════════════════════════════
# 路径重定向（DSH_AUDIT_ROOT）—— 让"变异反证"能在【临时副本】上跑
# ══════════════════════════════════════════════════════════════════════════
# 【为什么需要它】本审计的"有效性反证"必须真的改坏代码来验证审计能抓到；
#   若直接在仓库原文件上改，一旦还原失败就会毁掉文件 ——
#   实测发生过：client.js 被清成 0 字节。
#   有了这个开关，反证脚本可以把整个待审目录复制到 %TEMP%，在副本上变异，
#   仓库文件【全程只读、零风险】。
#
# 用法：设 DSH_AUDIT_ROOT=<某目录>，审计就去该目录下找 plugins/... 等文件。
#   不设时 = 正常模式，审仓库真身（自动回归走这条）。
_AUDIT_ROOT = (os.environ.get("DSH_AUDIT_ROOT") or "").strip()
if _AUDIT_ROOT:
    _AUDIT_ROOT = os.path.abspath(_AUDIT_ROOT)
    # 需要重定向的目录：plugins 与 tools 都在被测根下
    _PLUGINS_ROOT = _AUDIT_ROOT
    HERE = os.path.join(_AUDIT_ROOT, "tools") if os.path.isdir(
        os.path.join(_AUDIT_ROOT, "tools")) else HERE
    ROOT = _AUDIT_ROOT
else:
    _PLUGINS_ROOT = ROOT

CLIENT = os.path.join(_PLUGINS_ROOT, "plugins", "dsh-engineering-ui", "lib", "client.js")
HOST = os.path.join(_PLUGINS_ROOT, "plugins", "dsh-engineering-ui", "lib", "index.js")

PASS = 0
FAIL = 0
WARN = 0
ISSUES = []


def ck(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] %s" % label)
    else:
        FAIL += 1
        ISSUES.append(label)
        print("  [FAIL] %s  %s" % (label, detail))


def warn(label, detail=""):
    global WARN
    WARN += 1
    print("  [WARN] %s  %s" % (label, detail))


def sec(t):
    print("\n" + "=" * 72)
    print(t)
    print("=" * 72)


def read(p):
    with io.open(p, encoding="utf-8") as f:
        return f.read()


def strip_comments(src, js=False):
    r"""剥注释。★ 必须用【状态机】而不是贪婪正则。

    ── 为什么不能用 /\*[\s\S]*?\*/ ────────────────────────────────────────
    实测踩到的坑：client.js 的 L5228 有一行
        patterns: ['dsh-resource://subagentchat/session/**'],
    字符串里的 `/**` 让正则把它当成块注释起点，然后**一路吞到下一个 `*/`** ——
    跨过几百行正常代码，导致"函数调用点"被误判为不存在（审计假失败）。
    字符串里的 `//`、`/**` 都可能造成同类误判，因此这里按字符扫、
    并跳过字符串/模板串/正则，才是可靠做法。
    """
    out = []
    i = 0
    n = len(src)
    state = "code"          # code | line | block | sq | dq | tpl
    while i < n:
        c = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if state == "code":
            if c == "/" and nxt == "/":
                state = "line"; i += 2; continue
            if c == "/" and nxt == "*":
                state = "block"; i += 2; continue
            if c == "'":
                state = "sq"; out.append(c); i += 1; continue
            if c == '"':
                state = "dq"; out.append(c); i += 1; continue
            if c == "`":
                state = "tpl"; out.append(c); i += 1; continue
            out.append(c); i += 1; continue
        if state == "line":
            if c == "\n":
                state = "code"; out.append(c)
            i += 1; continue
        if state == "block":
            if c == "*" and nxt == "/":
                state = "code"; i += 2; continue
            if c == "\n":
                out.append(c)          # 保留换行，行号不错位
            i += 1; continue
        # 字符串内部：保留内容与转义，遇到未转义的收尾引号回到 code
        if c == "\\":
            out.append(c); out.append(nxt); i += 2; continue
        if (state == "sq" and c == "'") or (state == "dq" and c == '"') \
                or (state == "tpl" and c == "`"):
            state = "code"; out.append(c); i += 1; continue
        out.append(c); i += 1
    res = "".join(out)
    if not js:
        # Python 侧：只剩 # 行注释需要处理（三引号已在上面按字符串保留）
        res = re.sub(r"^\s*#.*$", "", res, flags=re.M)
    return res


def apply_span(src):
    """返回 apply(ctx) 的 (起行, 止行)（1-based）。找不到返回 (None, None)。"""
    lines = src.split("\n")
    start = None
    for i, l in enumerate(lines):
        if re.match(r"^\s{4}function apply\(ctx\)", l):
            start = i + 1
            break
    if start is None:
        return None, None
    for i in range(start, len(lines)):
        if re.match(r"^    \}\s*$", lines[i]):
            return start, i + 1
    return start, None


def main():
    client = read(CLIENT)
    host = read(HOST)
    ccode = strip_comments(client, js=True)

    # ═══════════════════════════════════════════════════════════════
    sec("审计 B：作用域 —— apply(ctx) 之外不得使用 ctx.*（会抛 ReferenceError）")
    a_start, a_end = apply_span(client)
    ck("能定位 apply(ctx) 的起止行", a_start is not None and a_end is not None,
       "start=%s end=%s" % (a_start, a_end))
    print("      apply 范围：L%s .. L%s" % (a_start, a_end))

    if a_start and a_end:
        # 找出所有"顶层函数定义"（缩进 4 空格的 function）
        lines = client.split("\n")
        fns = []
        for i, l in enumerate(lines):
            m = re.match(r"^\s{4}(?:async\s+)?function (\w+)\(", l)
            if m:
                # 找到该函数结束（下一个同行缩进的 function 或 exports.）
                fns.append((m.group(1), i + 1))
        # 逐个检查 ctx.* 使用
        bad_scope = []
        for name, ln in fns:
            if a_start <= ln <= a_end:
                continue      # 在 apply 里，允许
            # 取该函数体（到下一个顶层 function 为止）
            nxt = len(lines)
            for _fname, _fline in fns:
                if _fline > ln:
                    nxt = _fline - 1
                    break
            body = strip_comments("\n".join(lines[ln - 1:nxt]), js=True)
            for m in re.finditer(r"(?<![.\w])ctx\.\w+", body):
                bad_scope.append("%s (L%d): %s" % (name, ln, m.group(0)))
        ck("★ apply 之外的函数没有直接用 ctx.*（缺陷1 同类）", not bad_scope,
           bad_scope[:5])

    # ═══════════════════════════════════════════════════════════════
    sec("审计 A：旧判据残留 / 判据被绕过 —— 弹窗相关函数必须真正做工程模式判定")
    # ⚠️ 只查"是否出现旧判据名"是不够的（反证实测暴露的盲区）：
    #   把降级卡片的判定整段短路成 `if (false)` 时，函数体里【不再出现】
    #   _isEngineeringContext，旧写法照样全绿 —— 但判定其实被彻底绕过了。
    #   因此这里同时校验"判定确实存在且未被短路"。
    popup_fns = ["installEngLimitPopup", "tryAutoOpenEngLimitPopup",
                 "showEngLimitFallbackCard", "engLimitAutoTick",
                 "startEngLimitAutoTrigger"]
    lines = client.split("\n")

    def fn_body(fn):
        ln = None
        for i, l in enumerate(lines):
            if re.search(r"^\s*function %s\(" % re.escape(fn), l):
                ln = i + 1
                break
        if ln is None:
            return None, None
        nxt = len(lines)
        for i in range(ln, len(lines)):
            if re.search(r"^\s{4}(?:async\s+)?function \w+\(", lines[i]):
                nxt = i
                break
        return ln, strip_comments("\n".join(lines[ln - 1:nxt]), js=True)

    for fn in popup_fns:
        ln, body = fn_body(fn)
        if ln is None:
            ck("函数 %s 存在" % fn, False, "未找到")
            continue
        # ① 不得再直接用旧判据
        ck("★ %s 未使用旧判据 _isEngineeringContext()" % fn,
           not re.search(r"_isEngineeringContext\s*\(\)", body),
           "L%d 仍在用旧判据" % ln)
        # ② 判定不得被【字面短路】—— 这是反证1 暴露的真实盲区
        shorted = re.search(r"if\s*\(\s*(false|0)\s*\)", body)
        ck("★ %s 的工程模式判定未被字面短路（if(false)）" % fn, not shorted,
           "L%d 出现 `if (false)`，判定被绕过" % ln)
        # ③ 判定必须【在调用链上真实存在】——两种合法形态：
        #    (a) 本函数直接调 _sessionIsEngineering；
        #    (b) 本函数委托给一个会做判定的函数（如 engLimitAutoTick →
        #        tryAutoOpenEngLimitPopup，后者才做判定）。
        #    先前只认 (a)，把合法的委托写法误判为缺陷（实测）。
        _DELEGATES = {
            "engLimitAutoTick": r"tryAutoOpenEngLimitPopup\s*\(",
            "startEngLimitAutoTrigger": r"engLimitAutoTick\s*\(",
        }
        if fn in ("showEngLimitFallbackCard", "installEngLimitPopup",
                  "engLimitAutoTick", "startEngLimitAutoTrigger"):
            _direct = bool(re.search(r"_sessionIsEngineering\s*\(", body))
            _deleg = bool(re.search(_DELEGATES.get(fn, r"(?!x)x"), body))
            ck("★ %s 的工程模式判定在调用链上真实存在" % fn,
               _direct or _deleg,
               "L%d 既未直接判定，也未见委托调用" % ln)

    # ═══════════════════════════════════════════════════════════════
    sec("审计 C：定义未调用 —— 实现了但没人调（等于没做）")
    # C1: startEngLimitAutoTrigger 必须被 apply 调用
    calls = re.findall(r"startEngLimitAutoTrigger\s*\(", ccode)
    ck("★ startEngLimitAutoTrigger 有调用点（缺陷1：自动弹出零调用点）",
       len(calls) >= 2, "出现 %d 次（定义1+调用1 才算够）" % len(calls))
    # C2: installEngLimitPopup 必须被 apply 调用
    calls2 = re.findall(r"installEngLimitPopup\s*\(", ccode)
    ck("installEngLimitPopup 有调用点", len(calls2) >= 2, len(calls2))
    # C3: showEngLimitFallbackCard 必须被降级分支调用
    calls3 = re.findall(r"showEngLimitFallbackCard\s*\(", ccode)
    ck("showEngLimitFallbackCard 有调用点", len(calls3) >= 2, len(calls3))
    # C4: sw_bridge 的 _ensure_guard_quiet 必须被取锁路径调用
    swb = read(os.path.join(HERE, "sw_bridge.py"))
    swbc = strip_comments(swb)
    ck("★ sw_bridge._ensure_guard_quiet 有调用点（第4项：取锁即拉起看护）",
       len(re.findall(r"_ensure_guard_quiet\s*\(", swbc)) >= 2,
       len(re.findall(r"_ensure_guard_quiet\s*\(", swbc)))
    # C5: mode2_flow 的新闸门必须被 workflow_gate 调用
    wg = read(os.path.join(HERE, "workflow_gate.py"))
    wgc = strip_comments(wg)
    ck("workflow_gate 调用了 pre_delivery_check（交付前总闸接线）",
       "pre_delivery_check" in wgc)
    ck("workflow_gate 透传了 mode2 命令",
       "mode2" in wgc and "_m2_require" in wgc)

    # ═══════════════════════════════════════════════════════════════
    sec("审计 D：字段名不符 —— 读第三方返回时用了不存在的键")
    # D1: mode2_flow 读 part_registry.audit_verification 的字段名必须真实存在
    pr_src = read(os.path.join(HERE, "part_registry.py"))
    m2_src = read(os.path.join(HERE, "mode2_flow.py"))
    # ⚠️ 必须在【剥掉注释后】的源码上扫描 —— 否则"解释性注释里提到旧键名"
    #    会被当成真实违规（本审计脚本第一版就因此产生假失败）。
    m2_code = strip_comments(m2_src)
    _av_start = pr_src.find("def audit_verification")
    _av_end = pr_src.find("\ndef ", _av_start + 10)
    av_body = pr_src[_av_start:_av_end if _av_end > 0 else _av_start + 4000]
    av_keys = set(re.findall(r'"(\w+)":', av_body))
    # 只检查【读取】av 的键；写 av["..."] = ... 是本地赋值，不算第三方契约。
    used = set(re.findall(r'av\.get\("(\w+)"', m2_code))
    bogus = sorted(used - av_keys)
    ck("★ mode2_flow 读 audit_verification 用的键都真实存在", not bogus,
       "不存在的键: %s（真实键: %s）" % (bogus, sorted(av_keys)[:8]))

    # D2: check_adjacency_consistency 的键
    ca_src = pr_src[pr_src.find("def check_adjacency_consistency"):]
    ca_src = ca_src[:ca_src.find("\ndef ", 10) if ca_src.find("\ndef ", 10) > 0 else 3000]
    ca_keys = set(re.findall(r'"(\w+)":', ca_src))
    used_ca = set(re.findall(r'cons\.get\("(\w+)"', m2_src)) | \
              set(re.findall(r'res\.get\("(\w+)"', m2_src))
    bogus_ca = sorted(used_ca - ca_keys)
    ck("mode2_flow 读一致性结果的键都存在", not bogus_ca,
       "不存在的: %s" % bogus_ca)

    # D3: prime_verify_all 里用的 list_parts 键
    lp_src = pr_src[pr_src.find("def list_parts"):]
    lp_src = lp_src[:2000]
    lp_keys = set(re.findall(r'"(\w+)":', lp_src))
    ck("list_parts 返回含 parts 键（prime_verify 依赖它）", "parts" in lp_keys,
       sorted(lp_keys)[:8])

    # ═══════════════════════════════════════════════════════════════
    sec("审计 E：未接入门禁 —— 强制逻辑必须挂在「拒绝放行」的链上")
    # E1: 交付总闸必须是 _archive_finished 的一部分
    ck("★ 交付前总闸接在 _archive_finished（拒绝归档）",
       "MODE2_PRE_DELIVERY_BLOCKED" in wgc)
    # E2: 看图门必须接在 room-end
    mg = read(os.path.join(HERE, "mode_gate.py"))
    mgc = strip_comments(mg)
    ck("★ 看图门接在 room-end（拒绝下线）",
       "VISUAL_EVIDENCE_REQUIRED" in mgc and "_visual_gate_for_room" in mgc)
    # E3: 三道防线的 room-end 仍在
    ck("三道防线仍在 room-end", "DEFENSE_REQUIRED" in mgc)
    # E4: docx 强制必须接进总闸
    ck("★ docx 交付物强制接进总闸",
       "_check_docx_deliverable" in strip_comments(m2_src)
       and "docx_deliverable" in strip_comments(m2_src))
    # E5: part_registry 的写入前闸门必须真的在写路径上
    # 判据：① 有 _write_gate 定义；② 有【调用点】（>=2 次出现：定义 + 至少 1 处调用）；
    #       ③ 闸门失败常量被使用。原先只查字符串存在，无法区分"定义了但没调用"。
    pcode = strip_comments(pr_src)
    _wg_def = len(re.findall(r"def _write_gate\(", pr_src))
    _wg_calls = len(re.findall(r"(?<!def )_write_gate\s*\(", pcode))
    ck("★ 一致性闸门在 part_registry 写路径上",
       _wg_def == 1 and _wg_calls >= 2
       and "CONSISTENCY_VERIFICATION_FAILED" in pcode,
       "def=%d calls=%d（需 def=1 且 calls>=2）" % (_wg_def, _wg_calls))

    # ═══════════════════════════════════════════════════════════════
    sec("审计 F：CLI 与函数的一致性（拼写错误曾漏在 argparse default）")
    for name, src, subs in (
        ("mode2_flow.py", m2_src, ["set-depth", "ask", "set-subagent-plan",
                                   "prime-report", "prime-verify", "export-docx",
                                   "check-docx", "pre-delivery"]),
    ):
        miss = [s for s in subs if ('"%s"' % s) not in src and ("'%s'" % s) not in src]
        ck("%s 的子命令都已在 argparse 注册" % name, not miss, miss)

    # ═══════════════════════════════════════════════════════════════
    sec("审计 G：host 半（index.js）—— ESM + 路由已注册")
    hcode = strip_comments(host, js=True)
    # 再兜一层：状态机已能剥块注释，但为稳妥，把"行首 // 起始的多行块"也过滤掉
    hcode2 = "\n".join(l for l in hcode.split("\n") if not re.match(r"^\s*//", l))
    _bare = re.findall(r"(?:^|[^.\w])require\s*\(", hcode2)
    ck("index.js 无裸 require（ESM）", not _bare,
       "发现 %d 处：%s" % (len(_bare), _bare[:3]))
    ck("sw-guard-job 路由已注册", "'/sw-guard-job'" in host or '"/sw-guard-job"' in host)
    ck("ctx.jobs 被真实使用", "ctx.get('jobs')" in host or 'ctx.get("jobs")' in host)
    # G3: host 半的看护端点必须真的被调用（缺陷1 同类：定义了没人调）
    ck("★ ensureSwGuardJob 有调用点（不是只定义）",
       len(re.findall(r"ensureSwGuardJob\s*\(", host)) >= 2,
       len(re.findall(r"ensureSwGuardJob\s*\(", host)))

    # ═══════════════════════════════════════════════════════════════
    sec("审计 H：需求逐条接线 —— 每个能力都必须【被调用】，而不只是被定义")
    # 这一类是本会话栽过两次的坑：
    #   · tryAutoOpenEngLimitPopup 零调用点 → 自动弹出 0% 实现；
    #   · export_docx 零调用点 → docx 交付无人强制。
    # 判据统一为：出现次数 >= 2（定义 1 次 + 至少 1 处调用）。
    wgc2 = strip_comments(wg)
    m2c = strip_comments(m2_src)
    mgc2 = strip_comments(mg)
    prc = strip_comments(pr_src)

    def wired(src, name, need=2, label=None):
        n = len(re.findall(r"(?<![\w.])%s\s*\(" % re.escape(name), src))
        ck("★ %s 已接线（>=%d 次：定义+调用）" % (label or name, need),
           n >= need, "实际 %d 次" % n)

    def not_dead(src, name, label=None):
        """★ 调用点不得被【字面短路】包住（反证实测暴露的盲区）。

        只数"出现次数"是不够的：把调用写成
            if False:
                foo()
        次数照样够，但这条线实际上是死的。
        反证 4/5 正是这样绕过了审计 H（第一版只查次数）。
        这里检查 name 的调用【前一行/同行】不得出现 `if False:` / `if 0:`。
        """
        lines = src.split("\n")
        dead = []
        for i, l in enumerate(lines):
            if re.search(r"(?<![\w.])%s\s*\(" % re.escape(name), l):
                # 同行或上一行/上两行出现字面制假
                ctx = "\n".join(lines[max(0, i - 2):i + 1])
                if re.search(r"if\s+(False|0)\s*:", ctx):
                    dead.append("L%d" % (i + 1))
        ck("★ %s 的调用点未被 if(False) 短路" % (label or name),
           not dead, "疑似死代码：%s" % dead[:3])

    # 需求1：流程重排 + 总计子代理
    wired(wgc2, "generate_subagent_config", 2, "子代理编制生成")
    wired(wgc2, "_m2_require", 3, "mode2_flow 契约模块接入")
    # 需求2：编制由总计子代理决定 + 房间表采用编制
    wired(m2c, "set_subagent_plan", 2, "总计子代理登记编制")
    wired(m2c, "get_subagent_plan", 2, "读取编制")
    wired(m2c, "prime_room_definition", 2, "总理子代理定义")
    # 需求2/新增：总理核实 + docx
    wired(m2c, "prime_verify_all", 2, "总理统一核实")
    wired(m2c, "_check_docx_deliverable", 2, "docx 交付物核实")
    wired(m2c, "export_docx_and_record", 2, "docx 导出入口")
    # ★ 这两条是反证 4/5 抓出来的盲区：光"有调用"不够，调用还必须是活的
    not_dead(m2c, "prime_verify_all", "总理统一核实")
    not_dead(m2c, "_check_docx_deliverable", "docx 交付物核实")
    # 总闸必须被 workflow_gate 调用（跨文件）
    ck("★ 交付前总闸被 workflow_gate 调用（跨文件接线）",
       len(re.findall(r"pre_delivery_check\s*\(", wgc2)) >= 1,
       "workflow_gate 里 %d 次" % len(re.findall(r"pre_delivery_check\s*\(", wgc2)))
    ck("★ 看图门被 room-end 调用（跨文件接线）",
       len(re.findall(r"_visual_gate_for_room\s*\(", mgc2)) >= 2,
       "mode_gate 里 %d 次" % len(re.findall(r"_visual_gate_for_room\s*\(", mgc2)))
    # 需求4：双路看护都被调用
    ck("★ OS 子进程看护被取锁路径调用（sw_bridge）",
       len(re.findall(r"_ensure_guard_quiet\s*\(", swbc)) >= 2,
       "%d 次" % len(re.findall(r"_ensure_guard_quiet\s*\(", swbc)))
    # 需求3：台账反查
    wired(prc, "related_parts", 2, "按零件反查关联")
    wired(prc, "check_adjacency_consistency", 2, "相邻尺寸一致性")
    # 第6项：看图闭环
    vc = read(os.path.join(HERE, "visual_check.py"))
    vcc = strip_comments(vc)
    wired(vcc, "verify_visual_evidence", 2, "看图证据核实")
    wired(vcc, "enforce_visual_gate", 2, "看图强制门")

    print("\n" + "=" * 72)
    print("审计结果：PASS=%d  FAIL=%d  WARN=%d" % (PASS, FAIL, WARN))
    if ISSUES:
        print("未通过项：")
        for x in ISSUES:
            print("  - " + x)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
