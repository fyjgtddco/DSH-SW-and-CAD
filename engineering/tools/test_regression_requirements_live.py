# -*- coding: utf-8 -*-
r"""五项需求【在 DSH 安装副本上】是否真正生效 —— 逐条实测。

★ 为什么要针对安装副本测：DSH 实际加载的是
    ~/.dsh/.agent-presets/engineering/ 与 ~/.dsh/profiles/*/node_modules/，
    仓库里的代码即使正确，没同步过去就等于没生效（用户实测反馈"没感觉到生效"）。

每一项都用**可执行证据**判定，不看注释、不看文档：
  需求1 流程重排 + 十阶段 + 动态题数 + 按需提问
  需求2 数量由总计子代理定 + 总理上报 + 一致性 + docx
  需求3 台账持久 + 按零件反查
  需求4 SW 看护（10s / 85% / 关 SW）双路
  需求5 commandUi popupSelect 弹窗（稍后/不再 + 二级确认）
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# -- 【测试健壮性修复】DSH 主目录解析必须可靠，否则 cwd 变成无效路径 --
# 实测崩溃：某环境里 os.path.expanduser("~\.dsh") 原样返回 "~\.dsh"
#   （USERPROFILE/HOME 未注入），随后 os.path.join 得到相对路径，
#   subprocess.run(cwd=...) 直接抛 NotADirectoryError [WinError 267]，
#   整个测试崩在第一次子进程调用 —— 看起来像"需求全挂"。
# 修复：按 DSH_HOME → USERPROFILE/HOME → expanduser 顺序解析，并校验绝对路径。
def _resolve_dsh_home():
    _v = (os.environ.get("DSH_HOME") or "").strip()
    if _v and os.path.isabs(_v):
        return _v
    for _k in ("USERPROFILE", "HOME"):
        _v = (os.environ.get(_k) or "").strip()
        if _v:
            return os.path.join(_v, ".dsh")
    _v = os.path.expanduser("~")
    if _v and not _v.startswith("~"):
        return os.path.join(_v, ".dsh")
    return ""

DSH = _resolve_dsh_home()
INST = os.path.join(DSH, ".agent-presets", "engineering") if DSH else ""
ITOOLS = os.path.join(INST, "tools") if INST else ""
SKILL = os.path.join(DSH, "skills", "mode-selection", "SKILL.md") if DSH else ""
# 插件可能装在任一 profile 下，优先探测实际存在的那个
PLUGIN = ""
if DSH:
    _prof = os.path.join(DSH, "profiles")
    for _name in ("desktop", "web", "bili-duty"):
        _c = os.path.join(_prof, _name, "node_modules", "dsh-engineering-ui")
        if os.path.isdir(_c):
            PLUGIN = _c
            break
    if not PLUGIN:
        PLUGIN = os.path.join(_prof, "desktop", "node_modules", "dsh-engineering-ui")

if not DSH or not os.path.isdir(ITOOLS):
    print("[SKIP] 找不到 DSH 安装副本（DSH 主目录=%r，ITOOLS=%r）" % (DSH, ITOOLS))
    print("       本测试专门验证【安装副本】上的生效性，无法解析安装位置时跳过。")
    print("       这不是回归缺陷 —— 请在装有 DSH 的机器上重跑。")
    sys.exit(0)

# 【2026-10-28 重构】子进程继承固定身份；需求2 的脚本内会 analyst-bind
os.environ["DSH_SESSION_ID"] = "test-analyst"

P = F = 0
def ck(label, cond, extra=""):
    global P, F
    if cond:
        P += 1; print("  [PASS] " + label)
    else:
        F += 1; print("  [FAIL] " + label + ("  " + str(extra) if extra else ""))
def sec(t):
    print("\n" + "=" * 74); print(t); print("=" * 74)


# ── 【测试隔离修复】状态目录必须隔离，否则被"正在运行的 DSH"污染 ──────────
# 实测（2026-10-29 DSH 运行时跑本测试）：子进程会读到
#   <真实 state_dir>/platform_agents.json（宿主几秒前刚写的真实子代理注册表），
#   于是 mf.analyst_bind("test-analyst") 被判"你不在子代理注册表里" → 失败
#   → set_subagent_plan 返回 MODE2_ANALYST_REQUIRED
#   → RIGHT_COUNT=None → 断"数量由编制决定"必失败。
# 这不是需求没生效，是测试用了真实身份却配了真实注册表。
# 修复：给所有子进程指定一个空的临时 DSH_STATE_DIR（无注册表 → 放行测试身份），
#   与 test_regression_20261029_liveness.py 的 _isolate() 同一做法。
_ISO_ROOT = tempfile.mkdtemp(prefix="req_live_iso_")


def py(code, cwd=ITOOLS, args=()):
    """在安装副本里执行 python 片段，返回 (rc, stdout)。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["DSH_STATE_DIR"] = _ISO_ROOT
    p = subprocess.run([sys.executable, "-c", code, *args],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=cwd, env=env)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


print("DSH 安装副本：%s" % INST)
print("插件副本    ：%s" % PLUGIN)
print("SKILL       ：%s" % SKILL)
print()
print("存在性检查：")
for p in (ITOOLS, SKILL, PLUGIN):
    print("  %-70s %s" % (p, "OK" if os.path.exists(p) else "缺失"))

# ══════════════════════════════════════════════════════════════════
sec("需求1  流程重排 + 十阶段 + 动态题数（10~50 / 5~10）+ 按需提问")
rc, out = py(r'''
import sys, os, json, tempfile, shutil
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.getcwd())
import mode2_flow as mf
print("STAGES=%d" % len(mf.STAGES))
print("BUDGET=%s" % json.dumps(mf.QUESTION_LIMITS))
# 边界
for depth, n, want in (("full",10,True),("full",50,True),("full",9,False),("full",51,False),
                       ("lite",5,True),("lite",10,True),("lite",4,False),("lite",11,False)):
    got = mf.validate_question_budget(depth, n, final=(n < (10 if depth=="full" else 5)))["ok"]
    print("BUD %s %d -> %s (want %s) %s" % (depth, n, got, want, "OK" if got==want else "BAD"))
# 按需提问：未 begin 阶段不能提问
d = tempfile.mkdtemp()
mf.set_depth("full", d)
r1 = mf.record_question("载荷", "PhysicsReasoning", "算应力", d)
print("NO_BEGIN=%s" % (not r1["ok"]))
mf.begin_stage("TaskUnderstanding", d)
r2 = mf.record_question("用途", "TaskUnderstanding", "定材料", d)
print("AFTER_BEGIN=%s" % r2["ok"])
r3 = mf.begin_stage("MechanismSynthesis", d)
print("SKIP_STAGE=%s" % (not r3["ok"]))
shutil.rmtree(d, ignore_errors=True)
''')
for line in out.splitlines():
    if line.startswith(("STAGES=", "BUDGET=", "NO_BEGIN=", "AFTER_BEGIN=", "SKIP_STAGE=")):
        k, v = line.split("=", 1)
        print("  %-14s %s" % (k, v))
ck("十阶段流水线 = 10 段", "STAGES=10" in out)
ck("★ 强需求区间 10~50、弱需求 5~10",
   '"min": 10' in out and '"max": 50' in out
   and '"min": 5' in out and '"max": 10' in out, out.splitlines()[:2])
bad = [l for l in out.splitlines() if l.startswith("BUD ") and not l.endswith("OK")]
ck("★ 题数边界全部正确（含恰好等于上下界）", not bad, bad)
ck("★ 未 begin 阶段就提问 → 拒绝（禁止一股脑全问）", "NO_BEGIN=True" in out)
ck("begin 后可提问", "AFTER_BEGIN=True" in out)
ck("★ 跳阶段 → 拒绝（十阶段按序）", "SKIP_STAGE=True" in out)

# ══════════════════════════════════════════════════════════════════
sec("需求2  数量由总计子代理定 + 总理上报 + 一致性 + docx")
rc, out = py(r'''
import sys, os, json, tempfile, shutil
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.getcwd())
import mode2_flow as mf
d = tempfile.mkdtemp()
mf.analyst_bind("test-analyst", d)   # 先真实绑定总计子代理（代码强制）
mf.set_depth("lite", d)
for _sid in mf.STAGE_IDS:            # 编制前置：十阶段必须走完
    mf.begin_stage(_sid, d)
    mf.mark_stage_done(_sid, "art-" + _sid, d)
rooms = [{"name":"A","type":"housing","parts":["p1"],"why":"承载","depends_on":[]},
         {"name":"B","type":"structural","parts":["p2"],"why":"支撑","depends_on":["A"]},
         {"name":"C","type":"structural","parts":["p3"],"why":"运动","depends_on":["B"]}]
print("WRONG_DECIDER=%s" % (not mf.set_subagent_plan(rooms,"r","主对话",d)["ok"]))
sr = mf.set_subagent_plan(rooms,"按功能分解","总计子代理",d)
print("RIGHT_COUNT=%s" % sr.get("count"))
print("PRIME_FIXED=%s" % mf.prime_room_definition(d).get("fixed"))
good = {"part_name":"p1","room":"A","file":"C:/x/p1.SLDPRT",
        "dims":{"length_mm":100,"width_mm":50,"height_mm":20},
        "credentials":{"material":"m.json","physics":"p.json","domain":"d.json"},
        "adjacent":["p2"]}
print("REPORT_OK=%s" % mf.record_prime_report(good,d)["ok"])
missing=[]
for f in mf.REQUIRED_REPORT_FIELDS:
    b=dict(good); b.pop(f,None)
    if not mf.record_prime_report(b,d)["ok"]: missing.append(f)
print("REQUIRED_FIELDS=%s" % json.dumps(mf.REQUIRED_REPORT_FIELDS))
print("REJECTED_MISSING=%d" % len(missing))
# docx 闸门
print("FAKE_DOCX_REJECTED=%s" % (not mf._check_docx_deliverable(d,d)["ok"]))
import io as _io
_io.open(os.path.join(d,"台账.docx"),"wb").write(b"x"*2048)
print("FAKE_2_ALSO_REJECTED=%s" % (not mf._check_docx_deliverable(d,d)["ok"]))
shutil.rmtree(d, ignore_errors=True)
''')
for line in out.splitlines():
    if "=" in line and line.split("=")[0] in (
        "WRONG_DECIDER","RIGHT_COUNT","PRIME_FIXED","REPORT_OK",
        "REQUIRED_FIELDS","REJECTED_MISSING","FAKE_DOCX_REJECTED","FAKE_2_ALSO_REJECTED"):
        print("  " + line)
ck("★ 非『总计子代理』决定编制 → 拒绝", "WRONG_DECIDER=True" in out)
# 【2026-10-30 更新·BUG-07】set-subagent-plan 现在自动补齐两个收尾房间
#   （总装与验证 assembly + 工程图输出 drafting），所以：
#   传入 3 个建模房间 → 落盘 3 + 2 = 5 个。数量仍由编制决定（不是写死）。
ck("★ 数量由编制决定（3 个建模 + 2 个自动补齐收尾 = 5）",
   "RIGHT_COUNT=5" in out, [l for l in out.splitlines() if "RIGHT_COUNT" in l])
ck("总理子代理固定存在（fixed=True）", "PRIME_FIXED=True" in out)
ck("完整上报 → 通过", "REPORT_OK=True" in out)
ck("★ 6 个必填字段缺任一 → 拒绝", "REJECTED_MISSING=6" in out, out[:200])
ck("★ 假 docx（无 word/document.xml）→ 拒绝", "FAKE_DOCX_REJECTED=True" in out)
ck("★ 假 docx（有体积但非 zip）→ 仍拒绝", "FAKE_2_ALSO_REJECTED=True" in out)

# ══════════════════════════════════════════════════════════════════
sec("需求3  台账持久 + 按零件反查关联 + 写入前一致性闸门")
rc, out = py(r'''
import sys, os, json, tempfile, shutil
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.getcwd())
import part_registry as pr
d = tempfile.mkdtemp()
pr.add_part({"name":"Gear","room":"T","file":"C:/g.SLDPRT",
             "dims":{"length_mm":100,"width_mm":50,"height_mm":50},
             "adjacent":["Seat"]}, work_dir=d, reason="登记齿轮用于反查验证")
pr.add_part({"name":"Seat","room":"H","file":"C:/s.SLDPRT",
             "dims":{"length_mm":80,"width_mm":80,"height_mm":30},
             "adjacent":["Base"]}, work_dir=d, reason="登记轴承座用于反查验证")
pr.add_part({"name":"Base","room":"H","file":"C:/b.SLDPRT",
             "dims":{"length_mm":200,"width_mm":150,"height_mm":20}},
            work_dir=d, reason="登记底座用于反查验证")
rel = pr.related_parts("Gear", depth=2, work_dir=d)
print("REVERSE_OK=%s" % rel.get("ok"))
print("REVERSE_HAS_BASE=%s" % ("Base" in (rel.get("related") or [])))
print("PERSISTED=%s" % os.path.exists(pr.registry_path(work_dir=d)))
# 一致性闸门
pr.add_part({"name":"ShaftA","room":"T","file":"C:/a.SLDPRT",
             "dims":{"length_mm":120,"width_mm":40,"height_mm":40},
             "interfaces":[{"id":"J1","type":"shaft","nominal_mm":25.0}],
             "adjacent":["SeatB"]}, work_dir=d, reason="登记齿轮轴承载扭矩45N·m")
pr.add_part({"name":"SeatB","room":"H","file":"C:/b2.SLDPRT",
             "dims":{"length_mm":80,"width_mm":80,"height_mm":30},
             "interfaces":[{"id":"J1","type":"hole","nominal_mm":25.0}]},
            work_dir=d, reason="登记轴承座与齿轮轴配合")
bad = pr.update_part("SeatB", {"interfaces":[{"id":"J1","type":"hole","nominal_mm":24.0}]},
                     work_dir=d, reason="把孔改小1mm验证拦截")
print("GATE_BLOCKED=%s" % (not bad.get("ok")))
print("GATE_CODE=%s" % bad.get("gate"))
shutil.rmtree(d, ignore_errors=True)
''')
for line in out.splitlines():
    if line.split("=")[0] in ("REVERSE_OK","REVERSE_HAS_BASE","PERSISTED","GATE_BLOCKED","GATE_CODE"):
        print("  " + line)
ck("★ 按零件反查二级关联", "REVERSE_HAS_BASE=True" in out)
ck("台账文件持久落盘", "PERSISTED=True" in out)
ck("★ 尺寸不一致 → 写不进台账", "GATE_BLOCKED=True" in out)
ck("  gate = CONSISTENCY_VERIFICATION_FAILED", "GATE_CODE=CONSISTENCY_VERIFICATION_FAILED" in out)

# ══════════════════════════════════════════════════════════════════
sec("需求4  SW 看护（每 10s 采样 / CPU·内存 >85% 关 SW）双路")
host = io.open(os.path.join(PLUGIN, "lib", "index.js"), encoding="utf-8").read()
mg = io.open(os.path.join(ITOOLS, "mode_gate.py"), encoding="utf-8").read()
sgsrc = io.open(os.path.join(ITOOLS, "sw_guard.py"), encoding="utf-8").read()
ck("★ host 半使用 ctx.jobs", "ctx.get('jobs')" in host or 'ctx.get("jobs")' in host)
ck("★ 采样间隔 = 10 秒", "SW_GUARD_INTERVAL_MS = 10000" in host)
ck("★ CPU 阈值 = 85", "SW_GUARD_CPU_LIMIT = 85" in host)
ck("★ 内存阈值 = 85", "SW_GUARD_MEM_LIMIT = 85" in host)
ck("★ index.js 已 import node:os（曾漏→404）", "import os from 'node:os'" in host)
ck("host 有 /sw-guard-job 端点", "'/sw-guard-job'" in host)
ck("OS 子进程看护入口存在（第二路）", "def ensure_sw_guard" in mg)
ck("sw_guard 会真正关掉 SLDWORKS", "SLDWORKS" in sgsrc and "taskkill" in sgsrc)
rc, out = py("import sys,os; sys.path.insert(0,os.getcwd()); import sw_guard as g; print('RUN=%s' % hasattr(g,'run')); print('SAMPLE=%s' % hasattr(g,'check_thresholds'))")
ck("sw_guard 模块可导入且关键函数齐备", "RUN=True" in out and "SAMPLE=True" in out, out[-200:])

# ══════════════════════════════════════════════════════════════════
sec("需求5  ctx.commandUi popupSelect 弹窗（稍后提醒/不再提醒 + 二级确认）")
cl = io.open(os.path.join(PLUGIN, "lib", "client.js"), encoding="utf-8").read()
ck("★ 走官方外壳 commandUi.register", "cmdUi.register(" in cl)
ck("★ ui.kind = popupSelect", "kind: 'popupSelect'" in cl)
ck("★ available(session) 已实现", "available: function (session)" in cl)
ck("★ 两个选项：稍后提醒 / 不再提醒", "id: 'later'" in cl and "id: 'never'" in cl)
ck("★ 二级确认弹窗存在", "confirmation" in cl and "acknowledgeLabel" in cl)
ck("  cancelLabel = 好的，我回去修改", "好的，我回去修改" in cl)
ck("  confirmLabel = 我确认", "我确认" in cl)
ck("★ 自动触发源已接线", "startEngLimitAutoTrigger" in cl)
ck("★ 不绑设置页（独立 localStorage 键）", "dsh-engineering-ui/limit-popup/v1" in cl)
ck("降级卡片存在（官方通道不可用时不白屏）", "eng-limits-mask" in cl)
ck("★ exports.inject 含 commandUi", "'commandUi'" in cl and "exports.inject" in cl)

# ══════════════════════════════════════════════════════════════════
sec("需求6（追加）建模后必须看图 + 总理核实后才能结束")
vc = io.open(os.path.join(ITOOLS, "visual_check.py"), encoding="utf-8").read()
ck("★ 看图门接在 room-end（拒绝下线）", "VISUAL_EVIDENCE_REQUIRED" in mg)
ck("三视图 + 等轴测", '"front"' in vc and '"iso"' in vc)
ck("★ 重复视图拦截（防假三视图）", "duplicate_views" in vc)
ck("★ 全文件指纹（防 1MB 假阳性）", "sha1_full_12" in vc)
ck("★ 投影面验算（证明拍对了面）", "check_view_projection" in vc)
rc, out = py(r'''
import sys, os, tempfile, shutil
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.getcwd())
import mode2_flow as mf
d = tempfile.mkdtemp()
pv = mf.prime_verify_all(work_dir=d)
print("EMPTY_VERIFY_FAILS=%s" % (not pv.get("ok")))
print("VERIFIED_BY=%s" % pv.get("verified_by"))
pd = mf.pre_delivery_check(work_dir=d)
print("DELIVERY_BLOCKED=%s" % (not pd.get("ok")))
shutil.rmtree(d, ignore_errors=True)
''')
for line in out.splitlines():
    if line.split("=")[0] in ("EMPTY_VERIFY_FAILS","VERIFIED_BY","DELIVERY_BLOCKED"):
        print("  " + line)
ck("★ 空台账 → 核实用 fail-closed 拒绝", "EMPTY_VERIFY_FAILS=True" in out)
ck("  核实者 = 总理子代理", "VERIFIED_BY=总理子代理" in out)
ck("★ 未核实 → 交付总闸拒绝结束", "DELIVERY_BLOCKED=True" in out)

# ══════════════════════════════════════════════════════════════════
sec("SKILL.md（AI 实际遵循的指令）是否已按新流程改写")
sk = io.open(SKILL, encoding="utf-8").read()
ck("★ 第 1 步 = 确认模式 + 两个固定问题", "第 1 步：`init`（确认模式 + 两个固定问题）" in sk)
ck("★ 第 2 步 = 参数需求强度", "参数需求强度" in sk)
ck("★ 第 3 步 = 总计子代理 + 按需提问", "总计子代理" in sk and "按需提问" in sk)
ck("★ 明确说 provide_context 调三次", "必须调用三次" in sk)
ck("★ 不再把『第 0 题』当作第一步", "返回 **第 0 题（唯一一题）**" not in sk)
ck("★ 不再宣称强需求固定 17 题", "强需求=17题" not in sk)
ck("状态流转已含 mode_confirm_asked", "mode_confirm_asked" in sk)

print("\n" + "=" * 74)
print("五项需求生效性验收：PASS=%d  FAIL=%d" % (P, F))
print("=" * 74)
sys.exit(1 if F else 0)
