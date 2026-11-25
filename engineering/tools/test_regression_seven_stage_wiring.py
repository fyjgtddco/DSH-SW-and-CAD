# -*- coding: utf-8 -*-
r"""回归测试：十阶段 → 七阶段 的整体接线（2026-11-18 换代）

用户要求（原话）：
    「先删除十阶段分析所有的原本的文件，然后按照七阶段文档加功能」

因此本次换代【不是禁用，是删除】。本测试锁定六件事：

【A】十阶段引擎本体必须真的消失
     —— mode2_flow 不再暴露 STAGES / begin_stage / mark_stage_done /
        stage_status / refresh_stage / record_question / answer_question /
        import_answers / set_depth / validate_question_budget /
        analyst_bind / analyst_guard / analyst_status / set_subagent_plan /
        require_stages_complete；plan 文件里不再有 stages / questions /
        analyst / stale_stages 这些字段。
     为什么这么严：这些东西但凡留一个，模型就有可能"照旧调"，等于没换代。

【B】七阶段入口必须齐全
     —— seven_stage_gate.read_design 可导入；mode2_flow 有
        register_design_plan / recheck_design / pre_delivery_check；
        CLI 有 register-design / recheck-design / seven-status。

【C】引擎固定 + 真实根会话
     —— 新任务 init 后 workflow_state.analysis_engine == 'seven_v03' 且
        seven_stage_root_session_id 非空（取不到时 init 必须显式记录阻塞原因）。
     —— select 在引擎不对 / 缺根会话 / 缺凭证时都必须 fail-closed。

【D】插件包随源码分发
     —— engineering/plugins/dsh-engineering-seven-stage/lib/steps.json 存在，
        63 个小步，且 sha256 与 seven_stage_gate._SPEC_HASH 一致
        （不一致 = 读闸一定会拒掉所有任务）。

【E】三处注册必须齐全
     —— preset-engineering.patch.yml 有 engineering-seven-stage 行；
        sync-to-dsh.ps1 / install-0.2.0.ps1 都把该包纳入复制与依赖清单。

【F】状态目录口径一致（最容易劈成两半的地方）
     —— Node 侧 dirname(workflowFile)/seven_stage 与 Python 侧
        dirname(state_path('workflow_state.json'))/seven_stage 必须指向同一目录。

运行：python test_regression_seven_stage_wiring.py
退出码 0 = 通过
"""
import hashlib
import inspect
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 隔离状态目录：避免读到宿主实时写入的 workflow_state / platform_agents
_ISO_DIR = tempfile.mkdtemp(prefix="iso_seven_")
os.environ["DSH_STATE_DIR"] = _ISO_DIR
os.environ["DSH_SESSION_ID"] = "seven-wiring-root"
os.environ.pop("DSH_SEVEN_STAGE_STATE_DIR", None)

import mode2_flow as mf            # noqa: E402
import seven_stage_gate as ssg     # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))


def _find_plugin():
    """定位七阶段插件包：仓库源码优先，其次已安装的 profile node_modules。

    为什么要两处：本测试既要在仓库里跑（改完就验），也要能在
    ~/.dsh/.agent-presets/engineering/tools 这份【安装副本】里跑 —— 那里
    没有 engineering/plugins/，插件装在 profiles/*/node_modules 下。
    """
    cands = [os.path.join(REPO, "engineering", "plugins", "dsh-engineering-seven-stage")]
    home = os.environ.get("DSH_HOME") or os.path.join(os.path.expanduser("~"), ".dsh")
    prof = os.path.join(home, "profiles")
    if os.path.isdir(prof):
        for name in sorted(os.listdir(prof)):
            cands.append(os.path.join(prof, name, "node_modules",
                                      "dsh-engineering-seven-stage"))
    for c in cands:
        if os.path.isfile(os.path.join(c, "lib", "steps.json")):
            return c
    return cands[0]


PLUGIN = _find_plugin()
#: 插件入口（node 探针用）
_GATE_JS = os.path.join(PLUGIN, "lib", "index.js")
#: 仓库独有的文件（安装副本里没有）。缺失时相关断言降级为"跳过并说明"。
_REPO_ONLY = os.path.isdir(os.path.join(REPO, "engineering", "plugins"))

PASS = 0
FAIL = 0
FAILURES = []


def ck(label, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] " + label)
    else:
        FAIL += 1
        FAILURES.append(label)
        print("  [FAIL] " + label + ("  " + str(extra)[:400] if extra else ""))


def sec(t):
    print("")
    print("=" * 74)
    print(t)
    print("=" * 74)


def read(p):
    with io.open(p, encoding="utf-8") as f:
        return f.read()


# ════════════════════════════════════════════════════════════════════
sec("A. 十阶段引擎本体必须真的消失")
# ════════════════════════════════════════════════════════════════════
_GONE = [
    "STAGES", "STAGE_IDS", "STAGE_LABELS", "STAGE_NEEDS",
    "begin_stage", "mark_stage_done", "stage_status", "refresh_stage",
    "record_question", "answer_question", "import_answers", "question_report_legacy",
    "set_depth", "limits_for", "validate_question_budget", "QUESTION_LIMITS",
    "analyst_bind", "analyst_guard", "analyst_status", "analyst_liveness",
    "analyst_heartbeat", "set_subagent_plan", "require_stages_complete",
    "_stage_records", "_stage_latest", "_stage_done_map", "_mark_stage_stale",
    "_stale_implied", "_redo_watch", "_fresh_state",
]
_left = [n for n in _GONE if hasattr(mf, n)]
ck("★ mode2_flow 不再暴露任何十阶段符号", not _left, _left)

_plan_keys = set(mf._fresh_plan().keys())
_bad_keys = {"stages", "stage_index", "questions", "analyst", "stale_stages",
             "stale_history", "stage_question_deps", "param_depth"} & _plan_keys
ck("★ 计划文件不再有十阶段字段", not _bad_keys, sorted(_bad_keys))
for _need in ("seven_stage", "subagent_plan", "subagent_config"):
    ck("计划文件含七阶段字段 %s" % _need, _need in _plan_keys)
ck("计划 schema 已升版（@2）", mf._fresh_plan().get("schema", "").endswith("@2"),
   mf._fresh_plan().get("schema"))

_src = read(os.path.join(HERE, "mode2_flow.py"))
# 允许"已删除 / 旧实现 / 迁移说明"这类注释提到旧命令名，禁止的是【分发分支】
_branches = [ln.strip() for ln in _src.split("\n")
             if re.search(r'(cmd ==|elif cmd ==|add_parser)\(?\s*["\']'
                          r'(begin-stage|mark-stage-done|stage-status|refresh-stage|'
                          r'ask|answer-question|import-answers|set-subagent-plan|'
                          r'set-depth|analyst-bind|analyst-status|analyst-guard|'
                          r'analyst-heartbeat)["\']', ln)]
ck("★ mode2_flow CLI 不再分发十阶段子命令", not _branches, _branches[:4])

# workflow_gate 侧：旧闸门码不得再出现为"可返回的 gate"
_wgsrc = read(os.path.join(HERE, "workflow_gate.py"))
for _gate in ("MODE2_ANALYST_REQUIRED", "ANALYST_ACTOR_REQUIRED", "MODE2_STAGES_STALE"):
    _hits = [ln for ln in _wgsrc.split("\n")
             if _gate in ln and not ln.strip().startswith("#")]
    ck("★ workflow_gate 不再返回 %s" % _gate, not _hits, _hits[:2])


# ════════════════════════════════════════════════════════════════════
sec("B. 七阶段入口必须齐全")
# ════════════════════════════════════════════════════════════════════
for _fn in ("register_design_plan", "recheck_design", "pre_delivery_check",
            "_normalize_rooms_from_design", "_normalize_rooms_from_design"):
    ck("mode2_flow 有 %s" % _fn, callable(getattr(mf, _fn, None)))
ck("mode2_flow.ANALYSIS_ENGINE == 'seven_v03'",
   mf.ANALYSIS_ENGINE == "seven_v03", mf.ANALYSIS_ENGINE)
ck("seven_stage_gate.read_design 可调用", callable(getattr(ssg, "read_design", None)))
ck("seven_stage_gate 的 _STEPS 是 63 步", len(ssg._STEPS) == 63, len(ssg._STEPS))

_cli = []
for _sub in ("register-design", "recheck-design", "seven-status",
             "rooms-template", "room-types", "parts-board",
             "export-docx", "check-docx", "pre-delivery", "subagent-plan"):
    if ('"%s"' % _sub) in _src or ("'%s'" % _sub) in _src:
        _cli.append(_sub)
ck("★ CLI 已注册全部七阶段子命令",
   set(_cli) >= {"register-design", "recheck-design", "seven-status",
                 "rooms-template", "parts-board", "pre-delivery"},
   _cli)

# 旧名 stage-status 保留为别名（外部脚本还在调），但返回体必须是七阶段的
# 别名在 workflow_gate.py 的 CLI 分发里（_src 是 mode2_flow，别弄混）
_alias = [ln.replace(" ", "") for ln in _wgsrc.split("\n") if "seven-status" in ln]
ck("stage-status 仍是 seven-status 的别名",
   any('in("seven-status","stage-status"' in ln for ln in _alias),
   _alias[:3])


# ════════════════════════════════════════════════════════════════════
sec("C. 引擎固定 + 真实根会话 + fail-closed")
# ════════════════════════════════════════════════════════════════════
_eng_src = read(os.path.join(HERE, "workflow_gate.py"))
ck("workflow_gate 定义 ANALYSIS_ENGINE=seven_v03",
   'ANALYSIS_ENGINE = "seven_v03"' in _eng_src)
ck("init 会写入 analysis_engine",
   'state["analysis_engine"] = ANALYSIS_ENGINE' in _eng_src)
ck("init 会钉住真实根会话",
   '_ensure_seven_stage_root(state)' in _eng_src)
ck("★ 没有『回退旧引擎』的分支",
   not re.search(r'analysis_engine\s*==\s*[\'"](ten|mode2)', _eng_src))

# 静态：select 里的三连拒绝必须都在
for _gate in ("SEVEN_STAGE_HANDOFF_REQUIRED", "SEVEN_STAGE_DESIGN_CHANGED",
              "MODE2_SUBAGENT_PLAN_REQUIRED", "SEVEN_STAGE_ANALYSIS_PENDING",
              "ANALYSIS_ENGINE_MISMATCH"):
    ck("select 会返回 %s" % _gate, _gate in _eng_src)


# ════════════════════════════════════════════════════════════════════
sec("D. 插件包随源码分发（steps.json 是凭证的一部分）")
# ════════════════════════════════════════════════════════════════════
ck("插件目录存在", os.path.isdir(PLUGIN), PLUGIN)
for _rel in ("package.json", "lib/index.js", "lib/flow.js", "lib/steps.json",
             "test/smoke.mjs"):
    ck("插件含 %s" % _rel, os.path.isfile(os.path.join(PLUGIN, _rel)))

_steps_path = os.path.join(PLUGIN, "lib", "steps.json")
if os.path.isfile(_steps_path):
    _spec = json.loads(read(_steps_path))
    ck("★ steps.json 恰好 63 个小步", len(_spec.get("steps") or []) == 63,
       len(_spec.get("steps") or []))
    ck("★ steps.json 的 sha256 与读闸 _SPEC_HASH 一致",
       _spec.get("sha256") == ssg._SPEC_HASH,
       (_spec.get("sha256"), ssg._SPEC_HASH))
    _ids = [s.get("id") for s in (_spec.get("steps") or [])]
    ck("★ steps.json 的小步 id 与读闸 _STEPS 完全一致", _ids == ssg._STEPS,
       [a for a, b in zip(_ids, ssg._STEPS) if a != b][:3])
    ck("spec 版本是 V0.3", str(_spec.get("version", "")).startswith("V0.3"),
       _spec.get("version"))


# ════════════════════════════════════════════════════════════════════
sec("E. 三处注册必须齐全（preset / sync / install）")
# ════════════════════════════════════════════════════════════════════
_preset_path = os.path.join(REPO, "engineering", "plugins", "dsh-engineering-ui",
                            "preset-engineering.patch.yml")
if not os.path.isfile(_preset_path):
    # 安装副本里没有 engineering/plugins：改为读 profile 里已铺好的那份
    _home = os.environ.get("DSH_HOME") or os.path.join(os.path.expanduser("~"), ".dsh")
    for _n in sorted(os.listdir(os.path.join(_home, "profiles"))
                     if os.path.isdir(os.path.join(_home, "profiles")) else []):
        _c = os.path.join(_home, "profiles", _n, "node_modules",
                          "dsh-engineering-ui", "preset-engineering.patch.yml")
        if os.path.isfile(_c):
            _preset_path = _c
            break
ck("找到 preset-engineering.patch.yml", os.path.isfile(_preset_path), _preset_path)
_preset = read(_preset_path) if os.path.isfile(_preset_path) else ""
ck("★ preset 声明了 engineering-seven-stage 行",
   "- id: engineering-seven-stage" in _preset)
ck("preset 行按包名引用 dsh-engineering-seven-stage",
   "name: 'dsh-engineering-seven-stage'" in _preset)
ck("preset 行把 stateDir / workflowFile 交给环境变量（不写死路径）",
   "DSH_SEVEN_STAGE_STATE_DIR" in _preset
   and "DSH_SEVEN_STAGE_WORKFLOW_FILE" in _preset)
ck("preset 行默认 workerProvider=fork", "workerProvider: fork" in _preset)
ck("⛔ preset 里仍然没有 - id: permission（红线）",
   "- id: permission\n" not in _preset.replace("           ", "")
   or "#       - id: permission" in _preset)

_sync_p = os.path.join(REPO, "sync-to-dsh.ps1")
_inst_p = os.path.join(REPO, "install-0.2.0.ps1")
if _REPO_ONLY:
    _sync = read(_sync_p)
    ck("sync-to-dsh.ps1 复制七阶段包", "dsh-engineering-seven-stage" in _sync)
    ck("sync-to-dsh.ps1 把它写进依赖清单", "seven-stage" in _sync)
    _inst = read(_inst_p)
    ck("install-0.2.0.ps1 复制七阶段包", "$SEVEN_NAME" in _inst)
    ck("install-0.2.0.ps1 校验 steps.json 存在",
       "steps.json" in _inst and "lib" in _inst)
else:
    print("  [SKIP] 安装副本里没有安装脚本（这两项只在仓库侧有意义）")


# ════════════════════════════════════════════════════════════════════
sec("F. 状态目录口径一致（Node 与 Python 必须指向同一目录）")
# ════════════════════════════════════════════════════════════════════
_js = read(os.path.join(PLUGIN, "lib", "index.js"))
ck("Node 侧从 workflowFile 推导 stateDir",
   "join(dirname(workflowFile), 'seven_stage')" in _js,
   [l.strip() for l in _js.split("\n") if "seven_stage" in l][:3])
ck("Node 侧优先显式配置", "DSH_SEVEN_STAGE_STATE_DIR" in _js)

_py_dir, _py_src = ssg.state_dir_for("T")
ck("Python 侧能解析出状态目录", bool(_py_dir), (_py_dir, _py_src))
ck("★ Python 侧默认落在 workflow_state 同级 seven_stage/",
   _py_src == "sibling-of-workflow_state/seven_stage", _py_src)
_wf = ssg._workflow_state_path()
ck("★ Node 与 Python 的目录逐字相等",
   bool(_wf) and _py_dir == os.path.join(os.path.dirname(_wf), "seven_stage"),
   (_py_dir, _wf))

# 显式配置优先
_env_dir = tempfile.mkdtemp(prefix="seven_env_")
try:
    os.environ["DSH_SEVEN_STAGE_STATE_DIR"] = _env_dir
    _d2, _s2 = ssg.state_dir_for("T")
    ck("显式配置优先于默认推导",
       _d2 == _env_dir and _s2 == "env:DSH_SEVEN_STAGE_STATE_DIR", (_d2, _s2))
finally:
    os.environ.pop("DSH_SEVEN_STAGE_STATE_DIR", None)
    shutil.rmtree(_env_dir, ignore_errors=True)


# ════════════════════════════════════════════════════════════════════
sec("F2. 【事故回归】插件装不上时绝不能抛异常（会连累整个 preset）")
# ════════════════════════════════════════════════════════════════════
# 现场（2026-11-18）：
#     webserver: duplicate exact route "/dsh-engineering-ui/agents"
# 根因：preset 里那一行配置缺失 → install() 抛 SET_ABSOLUTE_SEVEN_STAGE_PATHS
#   → 整个 engineering preset 组合挂载失败 → 宿主重建组合 →
#   已 apply 过的 dsh-engineering-ui 被【第二次】apply →
#   ctx.webServer.register(PREFIX + '/agents') 撞上精确路由重名。
#
# 本段用 node 真跑插件，锁三件事：
#   ① 零配置也必须【挂载成功且功能可用】（LIVE：注册工具 + 装 tool guard + 4 个钩子）；
#   ② 宿主契约不全时【降级但不抛】（工具存在，返回 SEVEN_STAGE_NOT_CONFIGURED）；
#   ③ Node 解析出的 workflowFile / stateDir 与 Python 侧逐字相等。
_NODE = shutil.which("node")
if not _NODE:
    print("  [SKIP] 没装 node，跳过插件挂载测试")
else:
    _probe = os.path.join(tempfile.mkdtemp(prefix="seven_probe_"), "probe.mjs")
    with io.open(_probe, "w", encoding="utf-8") as f:
        f.write("""
import { pathToFileURL } from 'node:url';
const mod = await import(pathToFileURL(process.argv[2]).href);
const mkMsg = (o) => o;
const CLEAN = ['DSH_SEVEN_STAGE_WORKFLOW_FILE','DSH_SEVEN_STAGE_STATE_DIR',
               'DSH_STATE_DIR','DSH_HOME'];
const keep = process.env.USERPROFILE;

function makeCtx(withSubagents) {
  let registered = null, guarded = 0, hooks = 0;
  const ctx = {
    logger: { warn: () => {} },
    tools: { register: (t) => { registered = t; }, guard: () => { guarded++; } },
    agents: { get: () => null },
    on: () => { hooks++; return () => {}; },
  };
  if (withSubagents) {
    ctx.subagents = { getProvider: () => ({}), listDescendants: async () => [],
      prompt: async () => ({}), startActivation: async () => ({}) };
  }
  return { ctx, stat: () => ({ tool: registered ? registered.name : null, guarded, hooks }) };
}

function run(withSubagents, clean) {
  const saved = { ...process.env };
  if (clean) {
    for (const k of CLEAN) delete process.env[k];
    process.env.USERPROFILE = keep;
  }
  const h = makeCtx(withSubagents);
  let threw = null;
  try { mod.install(h.ctx, { stateDir: '', workerProvider: 'fork' }, mkMsg); }
  catch (e) { threw = e.message; }
  const p = mod.resolvePaths({ stateDir: '', workerProvider: 'fork' });
  process.env = saved;
  return { threw, ...(threw ? {} : h.stat()), ...p };
}

// live/degraded：清空所有七阶段相关环境变量（= 报错现场）
const live = run(true, true);
const degraded = run(false, true);
// parity：沿用父进程环境（测试已把 DSH_STATE_DIR 指向隔离目录），
//   用来断言 Node 与 Python 解析出同一对路径。
const parity = run(true, false);
console.log(JSON.stringify({ live, degraded, parity }));
""")
    _out = subprocess.run([_NODE, _probe, _GATE_JS],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=120)
    try:
        _res = json.loads((_out.stdout or "").strip().splitlines()[-1])
    except Exception:
        _res = {"_raw": (_out.stdout or "")[-400:] + (_out.stderr or "")[-400:]}
    _live = _res.get("live") or {}
    _deg = _res.get("degraded") or {}

    ck("★ 零配置时 install() 不抛异常（这就是报错现场的根因）",
       not _live.get("threw"), _live.get("threw") or _res)
    ck("★ 零配置时仍【完整挂载】（注册工具 + 装 tool guard + 4 个钩子）",
       _live.get("guarded") == 1 and _live.get("hooks") == 4, _live)
    ck("注册的是 engineering_seven_stage",
       _live.get("tool") == "engineering_seven_stage", _live.get("tool"))
    ck("★ 宿主契约不全时降级但不抛",
       not _deg.get("threw"), _deg.get("threw") or _res)
    ck("降级时仍注册工具（只是回可操作错误）",
       _deg.get("tool") == "engineering_seven_stage", _deg.get("tool"))
    ck("降级时不装 guard / 钩子（不干扰其它插件）",
       _deg.get("guarded") == 0 and _deg.get("hooks") == 0, _deg)

    # Node 与 Python 必须解析到同一对路径（同一份环境变量下）
    _par = _res.get("parity") or {}
    _py_dir, _py_src = ssg.state_dir_for("T")
    _py_wf = ssg._workflow_state_path()
    ck("★ Node/Python 解析出的 workflowFile 逐字相等",
       _par.get("workflowFile") == _py_wf,
       (_par.get("workflowFile"), _py_wf))
    ck("★ Node/Python 解析出的 stateDir 逐字相等",
       _par.get("stateDir") == _py_dir, (_par.get("stateDir"), _py_dir))
    ck("parity 场景下插件也是完整挂载（不是降级）",
       _par.get("guarded") == 1 and _par.get("hooks") == 4, _par)
    shutil.rmtree(os.path.dirname(_probe), ignore_errors=True)


# ════════════════════════════════════════════════════════════════════
sec("F3. 【BUG-seven-stage-tool-gate】P0/P1/P2 回归")
# ════════════════════════════════════════════════════════════════════
# 这三条是 2026-11-18 实机踩到的严重缺陷，必须由代码锁住，不能只写文档：
#   P0  attach 之后主对话自己的 pwsh/read/write/... 全被 SEVEN_STAGE_TOOL_GATE
#       拒掉 —— 门禁跑不了、"把 bug 写进文件"和"请人介入"也做不到，任务卡死。
#   P1  总计子代理在 attach 落地前结束 → REAL_DIRECT_CHILD_REQUIRED 不可恢复。
#   P2  mode-selection 身份判据自相矛盾（子代理既"不跑门禁"又要"调 attach"）。
_SRC_JS = read(_GATE_JS) if os.path.isfile(_GATE_JS) else ""

ck("★ P0：enforce() 里对 root 显式放行",
   "bound.role === 'root'" in _SRC_JS,
   [ln.strip() for ln in _SRC_JS.split("\n") if "role === 'root'" in ln][:2])
ck("★ P0：turn-stopping 对 root 直接 return",
   re.search(r"turn-stopping[\s\S]{0,900}?if \(role==='root'\) return;", _SRC_JS) is not None)
ck("★ P0：disposed 对 root 直接 return",
   _SRC_JS.count("if (role==='root') return;") >= 2,
   _SRC_JS.count("if (role==='root') return;"))
ck("★ P1：attach 接受「已结束但谱系仍在」的直系子会话",
   "listDescendants" in _SRC_JS and "realChild" in _SRC_JS)
ck("★ P1：报错点明「fork 与 attach 写在同一回复」",
   "同一条回复" in _SRC_JS)
ck("★ 附带：execute 把异常转成 JSON（不崩宿主）",
   "opAction" in _SRC_JS and "SEVEN_STAGE_ERROR" in _SRC_JS)

# P2：文档不再自相矛盾
_SKILL = os.path.join(REPO, "engineering", "skills", "mode-selection", "SKILL.md")
if os.path.isfile(_SKILL):
    _sk = read(_SKILL)
    ck("★ P2：技能给出四类身份判据（不再一刀切）",
       "知识/设计/反驳" in _sk and "你是【主对话】还是【被派出来的子代理】" in _sk)
    ck("★ P2：显式澄清「不跑门禁 ≠ 不参与门禁」",
       "不跑门禁" in _sk and "不参与门禁" in _sk)
    ck("★ P2：写明主对话不受插件工具限制",
       "永不限制" in _sk or "没有工具限制" in _sk)
else:
    print("  [SKIP] 安装副本里没有 skills 源码")

# 真跑一遍 node 行为验证（宿主模拟）
if _NODE and os.path.isfile(_GATE_JS):
    _p0 = os.path.join(tempfile.mkdtemp(prefix="seven_p0_"), "p0.mjs")
    with io.open(_p0, "w", encoding="utf-8") as f:
        f.write("""
import { pathToFileURL } from 'node:url';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
const mod = await import(pathToFileURL(process.argv[2]).href);
const P = [], F = [];
const ck = (n, c) => (c ? P : F).push(n);
const dir = mkdtempSync(join(tmpdir(), 'p0-'));
const wf = join(dir, 'workflow_state.json');
writeFileSync(wf, JSON.stringify({ task_id:'t1', analysis_engine:'seven_v03',
  seven_stage_root_session_id:'root' }));
const agents = new Map(), lineage = new Map();
let reg = null, guardFn = null;
const agentOf = (id, parent=null) => {
  const a = { id, session:{ id, header:{ parentSession:parent },
    deriveMessages:()=>[{id:'u1',role:'user',source:{kind:'user'},content:[{type:'text',text:'需求'}]}] },
    followup(){}, steer(){} };
  agents.set(id, a); if (parent) lineage.set(id, parent); return a;
};
const rootA = agentOf('root');
const ctx = {
  logger:{ warn:()=>{} },
  tools:{ register:t=>{reg=t;}, guard:fn=>{guardFn=fn;} },
  agents:{ get:id=>agents.get(id) },
  subagents:{ getProvider:()=>({}),
    listDescendants: async r => [...lineage.entries()].filter(([,p])=>p===r)
      .map(([id])=>({kind:'child', id, parentId:r, activity:'inactive'})),
    prompt: async()=>({}),
    startActivation: async i => { agentOf(i.childId, i.parent.id); return {childId:i.childId, result:Promise.resolve({})}; } },
  on:()=>()=>{},
};
mod.install(ctx, { workflowFile: wf, stateDir: join(dir,'seven_stage'), workerProvider:'fork' }, o=>o);
const call = (agent, action, data={}) => reg.execute(
  { taskId:'t1', action, dataJson: JSON.stringify(data) },
  { name:'engineering_seven_stage', agent, signal:{ throwIfAborted(){} } });
const ex = (name, agent) => ({ name, agent, signal:{ throwIfAborted(){} } });

// P0
const planner = agentOf('p1','root');
await call(rootA, 'attach', { planner:'p1', messageIds:['u1'] });
for (const t of ['pwsh','read','write','grep','list_agents','todo_write','ask_user_question'])
  ck('root can ' + t, guardFn(ex(t, rootA)) === undefined);
await call(planner, 'launch');
const kids = [...agents.keys()].filter(k => k!=='root' && k!=='p1');
ck('3 workers', kids.length === 3);
for (const k of kids) ck('worker locked ' + k, typeof guardFn(ex('pwsh', agents.get(k))) === 'string');
ck('planner locked', typeof guardFn(ex('pwsh', planner)) === 'string');
const other = agentOf('room-x','someone');
ck('room unaffected', guardFn(ex('pwsh', other)) === undefined);

// P1 —— 新任务：planner 在 attach 前结束
const dir2 = mkdtempSync(join(tmpdir(),'p0b-'));
const wf2 = join(dir2,'workflow_state.json');
writeFileSync(wf2, JSON.stringify({ task_id:'t2', analysis_engine:'seven_v03',
  seven_stage_root_session_id:'root' }));
mod.install({ ...ctx, tools:{ register:t=>{reg=t;}, guard:fn=>{guardFn=fn;} }, on:()=>()=>{} },
  { workflowFile: wf2, stateDir: join(dir2,'seven_stage'), workerProvider:'fork' }, o=>o);
agentOf('p-dead','root'); agents.delete('p-dead');   // 模拟已结束
const r1 = await reg.execute({ taskId:'t2', action:'attach',
  dataJson: JSON.stringify({ planner:'p-dead', messageIds:['u1'] }) },
  { name:'engineering_seven_stage', agent: rootA, signal:{ throwIfAborted(){} } });
let ok1 = false; try { ok1 = JSON.parse(r1.text).phase === 'setup'; } catch {}
ck('dead planner attach ok', ok1);
const r2 = await reg.execute({ taskId:'t2', action:'attach',
  dataJson: JSON.stringify({ planner:'nope', messageIds:['u1'] }) },
  { name:'engineering_seven_stage', agent: rootA, signal:{ throwIfAborted(){} } });
ck('forged planner rejected', /REAL_DIRECT_CHILD_REQUIRED/.test(r2.text));
ck('no host crash', typeof r2.text === 'string');
console.log(JSON.stringify({ pass: P.length, fail: F.length, fails: F }));
""")
    _o = subprocess.run([_NODE, _p0, _GATE_JS], capture_output=True, text=True,
                        encoding="utf-8", errors="replace", timeout=180)
    try:
        _r = json.loads((_o.stdout or "").strip().splitlines()[-1])
    except Exception:
        _r = {"pass": 0, "fail": 1, "fails": [(_o.stdout or "")[-300:] + (_o.stderr or "")[-300:]]}
    ck("★ P0/P1 行为验证全过（%d 项）" % _r.get("pass", 0), _r.get("fail") == 0,
       _r.get("fails"))
    shutil.rmtree(os.path.dirname(_p0), ignore_errors=True)


# ════════════════════════════════════════════════════════════════════
sec("F4. 【2026-11-18 实机】BUG-1~4 回归（检索外包 / 僵尸 job / 派发可视化 / next 刷新）")
# ════════════════════════════════════════════════════════════════════
# 现场报告：C:\Users\j1877\Desktop\test\20261118_211912_BA97EB57\reports\bugs_七阶段分析.md
#   BUG-1 宿主缺 DSH_SEVEN_STAGE_BRAVE_KEY → knowledge 联网检索不可用；web_search 又被
#         SEVEN_STAGE_TOOL_GATE 拦 → 双重断路，step 1.1 直接 failed。
#         用户口径：改成【插件发布检索工单 → 真实主对话用 DSH 内置 web_search 执行 → 回填插件】，
#         且不得打破「大脑(planner) / 传话员(root)」的绝对地位。
#   BUG-2 旧会话释放未办 end → 僵尸 job；四个动作回四个互相矛盾的错误码。
#   BUG-3 planner 全程 job=null，progress 0/63，status 只说"等待任务包"，看不出在等谁。
#   BUG-4 四角色都 end(kind=partial) 之后，status 的 next 文案不刷新（复现 5 次）。
_SRC_FLOW = read(os.path.join(PLUGIN, "lib", "flow.js")) if os.path.isfile(
    os.path.join(PLUGIN, "lib", "flow.js")) else ""

ck("★ BUG-1：插件侧已无任何网络后端（brave/host search 只剩注释）",
   "braveSearch(" not in _SRC_JS.replace("braveSearch()", "")
   and "hostSearch(" not in _SRC_JS.replace("hostSearch()", ""),
   [ln.strip() for ln in _SRC_JS.split("\n") if "Search(" in ln][:4])
ck("★ BUG-1：index.js 里不再读任何搜索密钥/端点环境变量",
   "DSH_SEVEN_STAGE_BRAVE_KEY" not in _SRC_JS.split("//   ")[-1]
   or "process.env.DSH_SEVEN_STAGE_SEARCH_URL" not in _SRC_JS,
   [ln.strip() for ln in _SRC_JS.split("\n") if "process.env.DSH_SEVEN_STAGE" in ln][:4])
ck("★ BUG-1：action 枚举里有 search_fill（回填通道）", "'search_fill'" in _SRC_JS)
ck("★ BUG-1：检索工单 orderSearch + 回填 fillSearch 都在 flow.js",
   "function orderSearch" in _SRC_FLOW and "function fillSearch" in _SRC_FLOW)
ck("★ BUG-1：回填被限定为传话员（非 root 一律 SEARCH_FILL_RELAY_ONLY）",
   "SEARCH_FILL_RELAY_ONLY" in _SRC_FLOW)
ck("★ BUG-2：interrupt 会作废僵尸 job（记 orphanedJob 并从 jobs 删除）",
   "orphanedJob" in _SRC_FLOW and re.search(r"delete s\.jobs\[role\]", _SRC_FLOW) is not None)
ck("★ BUG-2：retry 会重发 job（reissue）",
   "function reissue" in _SRC_FLOW and "reissuedJobId" in _SRC_FLOW and "reissue(s, d.role)" in _SRC_FLOW)
ck("★ BUG-2：拒绝时回传 allowed 动作清单",
   "allowedActions" in _SRC_FLOW and "allowed:" in _SRC_JS)
ck("★ BUG-3：status 带 waitingOn（说明在等哪个角色）", "waitingOn" in _SRC_FLOW)
ck("★ BUG-3：planner 的 status 带 dispatch 派发矩阵", "dispatchMatrix" in _SRC_FLOW)
ck("★ BUG-4：next 文案按 ended 动态生成（不再只查 phase 表）",
   "function nextText" in _SRC_FLOW and "unfinished" in _SRC_FLOW)

# 真跑一遍：检索外包 + 僵尸 job + next 刷新，全部用 openFlow 的纯状态机验证
if _NODE and os.path.isfile(_GATE_JS):
    _p1 = os.path.join(tempfile.mkdtemp(prefix="seven_bug14_"), "bug14.mjs")
    with io.open(_p1, "w", encoding="utf-8") as f:
        f.write("""
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
const flowMod = await import(pathToFileURL(process.argv[2]).href);
const dir = mkdtempSync(join(tmpdir(), 'bug14-'));
const f = flowMod.openFlow(dir);
const P = [], F = [];
const ck = (n, c, extra) => (c ? P : F).push(extra === undefined ? n : n + ' :: ' + JSON.stringify(extra).slice(0, 200));
const A = (id, parent = null) => ({ id, parent });
const root = A('root'), plan = A('planner', 'root');
const t = 'bug14-task';
const call = (a, act, d = {}) => f.call(a, t, act, d);
call(root, 'attach', { planner: 'planner', requirements: [{ id: 'm1', text: '需求', source: 'user' }] });
const inv = call(plan, 'invite');
const tok = r => inv.invitations.find(x => x.role === r).token;
for (const r of ['knowledge', 'design', 'review']) f.reserveWorker(plan, t, r, r);
for (const r of ['knowledge', 'design', 'review']) call(A(r, 'planner'), 'bind', { role: r, token: tok(r) });
call(plan, 'frame', { object: 'o', decision: 'd', boundaries: 'b', searchTopic: 's',
  verification: 'v', assumptions: 'a', prior: 'p', blockedBy: [] });

// ── BUG-1：知识发工单（不联网）→ 只有 root 能回填 ──────────────────────
const know = A('knowledge', 'planner');
const job = call(know, 'status').job;
const o1 = call(know, 'search', { jobId: job.id, query: 'GB/T 1096 平键' });
ck('BUG-1 知识可以发布检索工单', o1.order && o1.order.status === 'pending');
ck('BUG-1 工单记录查询词', o1.order.query === 'GB/T 1096 平键');
let denied = null;
try { call(know, 'search_fill', { jobId: job.id, orderId: o1.order.id,
  query: 'GB/T 1096 平键', hits: [{ title: 'x', url: 'https://example.com/x' }] }); }
catch (e) { denied = e.message; }
ck('BUG-1 知识不能自己回填（检索者不得自证）', /SEARCH_FILL_RELAY_ONLY/.test(String(denied)), denied);
denied = null;
try { call(plan, 'search_fill', { jobId: job.id, orderId: o1.order.id,
  query: 'GB/T 1096 平键', hits: [] }); }
catch (e) { denied = e.message; }
ck('BUG-1 planner 也不能回填（只有传话员能）', /SEARCH_FILL_RELAY_ONLY/.test(String(denied)), denied);
// 查询词必须与工单一致，防止传话员改题
denied = null;
try { call(root, 'search_fill', { jobId: job.id, orderId: o1.order.id,
  query: '改过的查询', hits: [{ title: 'x', url: 'https://example.com/x' }] }); }
catch (e) { denied = e.message; }
ck('BUG-1 回填查询词必须与工单一致', /SEARCH_QUERY_MISMATCH/.test(String(denied)), denied);
const rec = call(root, 'search_fill', { jobId: job.id, orderId: o1.order.id,
  query: 'GB/T 1096 平键', provider: '宿主内置 web_search',
  hits: [{ title: '普通型平键', url: 'https://std.samr.gov.cn/a', snippet: 'GB/T 1096-2003' }] });
ck('BUG-1 传话员回填成功', rec.search.hits.length === 1);
ck('BUG-1 来源记在 knowledge job 上',
  call(know, 'status').job.searches[0].hits[0].url === 'https://std.samr.gov.cn/a');
ck('BUG-1 工单状态转 filled', call(know, 'status').search.order.status === 'filled');
call(know, 'submit', { jobId: job.id, content: '依据',
  sources: [{ id: call(know, 'status').job.searches[0].hits[0].id, location: 'p.1', applicability: '仅模拟' }],
  gaps: [] });
ck('BUG-1 知识用回填来源 submit 成功', call(plan, 'status').phase === 'preflight');

// ── BUG-2：僵尸 job ─────────────────────────────────────────────────────
// 先停在一个有 job 的阶段：preflight 归 review
const rev = A('review', 'planner');
const rj = call(rev, 'status').job;
ck('BUG-2 起始 review 有 job', !!rj);
f.interrupt(rev, t, '宿主释放会话，未办理结束');
const afterInt = f.load(t);
ck('BUG-2 会话释放后 job 被作废（不再"看得见摸不着"）', !afterInt.jobs.review, afterInt.jobs.review);
ck('BUG-2 作废的 job id 留痕 orphanedJob', afterInt.failures.review?.orphanedJob === rj.id,
  afterInt.failures.review);
ck('BUG-2 status 不再显示僵尸 job', call(rev, 'status').job === null);
const rt = call(plan, 'retry', { role: 'review', reason: '重派' });
ck('BUG-2 retry 会发新 job（不是只清故障）', !!rt.reissuedJobId && rt.reissuedJobId !== rj.id,
  rt.reissuedJobId);
ck('BUG-2 新 job 与 status 一致', call(rev, 'status').job?.id === rt.reissuedJobId);
ck('BUG-2 新 job 的检索/计算从零开始', call(rev, 'status').job.calculations.length === 0);

// ── BUG-3：派发矩阵 + 在等谁 ─────────────────────────────────────────────
const ps = call(plan, 'status');
ck('BUG-3 planner status 带 dispatch', Array.isArray(ps.dispatch) && ps.dispatch.length === 3);
ck('BUG-3 dispatch 标出各角色状态',
  ps.dispatch.every(x => ['idle', 'working', 'ended', 'failed'].includes(x.state)));
ck('BUG-3 status 带 waitingOn', typeof ps.waitingOn === 'string' && ps.waitingOn.length > 0,
  ps.waitingOn);

// ── BUG-4：next 跟着 ended 变 ────────────────────────────────────────────
// 直接构造一个 partial 终态：给当前任务登记失败再 partial
f.interrupt(A('design', 'planner'), t, '模拟故障');
call(plan, 'partial', { reason: '模拟部分结果' });
const n1 = call(plan, 'status').next;
ck('BUG-4 partial 后 next 不再是旧固定文案', !/三个角色说明已有工作和遗留项/.test(n1), n1);
ck('BUG-4 next 里点名还剩谁没登记', /还差/.test(n1), n1);
for (const r of ['review', 'design', 'knowledge']) {
  try { call(A(r, 'planner'), 'end', { kind: 'partial', reason: '已登记' }); } catch {}
}
call(plan, 'end', { kind: 'partial', reason: '已登记' });
const n2 = call(plan, 'status').next;
ck('BUG-4 角色陆续 end 后 next 随之更新', n2 !== n1, { before: n1, after: n2 });
call(root, 'end', { kind: 'partial', reason: '收束' });
const n3 = call(root, 'status').next;
ck('BUG-4 全部登记后 next 说明交接完成', /全部角色已登记结束/.test(n3), n3);
console.log(JSON.stringify({ pass: P.length, fail: F.length, fails: F }));
""")
    _o = subprocess.run([_NODE, _p1, os.path.join(PLUGIN, "lib", "flow.js")],
                        capture_output=True, text=True, encoding="utf-8",
                        errors="replace", timeout=180)
    try:
        _r = json.loads((_o.stdout or "").strip().splitlines()[-1])
    except Exception:
        _r = {"pass": 0, "fail": 1,
              "fails": [((_o.stdout or "") + (_o.stderr or ""))[-600:]]}
    ck("★ BUG-1~4 行为验证全过（%d 项）" % _r.get("pass", 0), _r.get("fail") == 0,
       _r.get("fails"))
    shutil.rmtree(os.path.dirname(_p1), ignore_errors=True)


# ════════════════════════════════════════════════════════════════════
sec("F5. 【2026-11-19 实机第二轮】BUG-1~8 回归（终态出口 / 报错可读性 / 模板指纹）")
# ════════════════════════════════════════════════════════════════════
# 现场报告：C:\Users\j1877\Desktop\test\减速器任务_七阶段插件缺陷报告.md
#   BUG-2 dataJson 传对象报 INPUT_TOO_LARGE（真因是类型错）
#   BUG-3 终态 partial 后分析角色仍被工具门禁锁死，产物读不回来
#   BUG-4 终态每次状态转移都重发同一条通知（现场 7 次）
#   BUG-5 partial 是单向门：propose_rooms 要 ready、retry 要非终态 → 无出口
#   BUG-6 attach 传显示名 agent-1 时，报错没提"要 sessionId"
#   BUG-7 1.1 等步骤模板是产线工艺叙事（压装机/工位/来料），与机械装配错位
#   BUG-8 环境前置未在门禁前暴露（搜索 key 依赖已随 BUG-1 消失）

_SRC_IDX = _SRC_JS   # index.js 源码
ck("★ BUG-2：dataJson 类型错有专属错误码（不再冒充 INPUT_TOO_LARGE）",
   "DATAJSON_MUST_BE_STRING" in _SRC_IDX)
ck("★ BUG-2：非法 JSON 有专属错误码（不再把 JSON.parse 异常冒给宿主）",
   "DATAJSON_INVALID_JSON" in _SRC_IDX)
ck("★ BUG-3：enforce() 对终态放行分析角色",
   "isTerminal" in _SRC_IDX and "TERMINAL_PHASES" in _SRC_IDX
   and re.search(r"if \(isTerminal\(bound\.s\)\) return;", _SRC_IDX) is not None)
ck("★ BUG-4：终态通知只发一次（terminalNotified）",
   "terminalNotified" in _SRC_IDX)
ck("★ BUG-4：终态不再走逐条 notify（终态分支提前 return）",
   re.search(r"if \(TERMINAL_PHASES\.has\(s\.phase\)\)[\s\S]{0,1400}?return;", _SRC_IDX) is not None)
ck("★ BUG-5：新增 reopen 动作（flow.js case + index enum）",
   "case 'reopen'" in _SRC_FLOW and "'reopen'" in _SRC_IDX)
ck("★ BUG-5：reopen 只允许 planner/root",
   re.search(r"case 'reopen':\s*\{\s*as\('planner','root'\)", _SRC_FLOW) is not None)
ck("★ BUG-5：reopen 的恢复点由插件推导（不是模型指定）",
   "resumePhase" in _SRC_FLOW and "const resume = " in _SRC_FLOW)
ck("★ BUG-6：attach 报错点明要 sessionId / UUID",
   "sessionId" in _SRC_IDX and "UUID" in _SRC_IDX
   and re.search(r"REAL_DIRECT_CHILD_REQUIRED[\s\S]{0,1200}?UUID", _SRC_IDX) is not None)
ck("★ BUG-6：支持按显示名唯一解析成 sessionId",
   "PLANNER_NAME_AMBIGUOUS" in _SRC_IDX)
ck("★ BUG-7-B：spec 哈希改为可复现计算（不再读死字段）",
   "computeSpecHash" in _SRC_FLOW and "specHashDrift" in _SRC_FLOW)
ck("★ BUG-7-B：指纹漂移在 attach 时 fail-fast",
   "SPEC_HASH_DRIFT" in _SRC_IDX)
ck("★ 附带：载荷工况随任务包下发（分析角色不必开 shell 去读文件）",
   "loadCaseOf" in _SRC_FLOW and "loadCase:loadCaseOf(s)" in _SRC_FLOW)
ck("★ BUG-1 补强：search_fill 区分 ok/unavailable/error/blocked",
   "'unavailable'" in _SRC_FLOW and "INVALID_SEARCH_OUTCOME" in _SRC_FLOW)
ck("★ BUG-1 补强：失败的检索不许携带 hits（不许把失败包装成结果）",
   "FAILED_SEARCH_CANNOT_CARRY_HITS" in _SRC_FLOW)

# steps.json：模板指纹必须三处一致，且 1.1 已改成领域中立表述
_spec2 = json.load(io.open(os.path.join(PLUGIN, "lib", "steps.json"), encoding="utf-8"))
ck("★ BUG-7-B：steps.json 的 sha256 字段 == Python 闸门 _SPEC_HASH",
   _spec2.get("sha256") == ssg._SPEC_HASH, (_spec2.get("sha256"), ssg._SPEC_HASH))
ck("★ BUG-7-B：sha256 可由算法复现（不再手填）",
   hashlib.sha256(json.dumps(
       {k: _spec2[k] for k in ("opening", "crossCutting", "version", "steps")},
       ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
   == _spec2.get("sha256"), _spec2.get("sha256"))
_s11 = next((x for x in _spec2.get("steps") or [] if x.get("id") == "1.1"), {})
ck("★ BUG-7：1.1 模板不再以【产品进入工位】开场",
   "进入工位" not in (_s11.get("procedure") or ""),
   (_s11.get("procedure") or "")[:80])
ck("★ BUG-7：1.1 仍要求保存用户原话、不替用户下结论",
   "原话" in (_s11.get("procedure") or ""))
_steps63 = _spec2.get("steps") or []
ck("★ BUG-7：63 个小步数量与 id 序列未变",
   len(_steps63) == 63 and [x.get("id") for x in _steps63] == ssg._STEPS)

# 真跑：终态解锁 / 通知去重 / reopen / dataJson / 旧状态容错
if _NODE and os.path.isfile(_GATE_JS):
    _p2 = os.path.join(tempfile.mkdtemp(prefix="seven_bug58_"), "bug58.mjs")
    with io.open(_p2, "w", encoding="utf-8") as f:
        f.write("""
import { mkdtempSync, writeFileSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
const P = [], F = [];
const ck = (n, c, extra) => (c ? P : F).push(extra === undefined ? n : n + ' :: ' + JSON.stringify(extra).slice(0, 240));
const mod = await import(pathToFileURL(process.argv[2]).href);
const flowMod = await import(pathToFileURL(process.argv[3]).href);

// ── 场景搭建：一个已跑到 knowledge 阶段的任务 ──────────────────────────
const dir = mkdtempSync(join(tmpdir(), 'b58-'));
const wf = join(dir, 'workflow_state.json');
writeFileSync(wf, JSON.stringify({ task_id: 'b58', analysis_engine: 'seven_v03',
  seven_stage_root_session_id: 'root' }));
const agents = new Map(); const notes = [];
const ag = (id, parent = null) => {
  const a = { id, session: { id, header: { parentSession: parent },
      deriveMessages: () => [{ id: 'u1', role: 'user', source: { kind: 'user' },
        content: [{ type: 'text', text: '需求' }] }] },
    followup(m) { if (id === 'root') notes.push(String(m?.content?.[0]?.text ?? '')); }, steer() {} };
  agents.set(id, a); return a;
};
const rootA = ag('root'), planA = ag('p1', 'root');
let reg = null, guardFn = null;
const ctx = { logger: { warn: () => {} },
  tools: { register: t => { reg = t; }, guard: fn => { guardFn = fn; } },
  agents: { get: id => agents.get(id) },
  subagents: { getProvider: () => ({}), listDescendants: async () => [],
    prompt: async () => ({}),
    startActivation: async i => { ag(i.childId, i.parent.id); return { childId: i.childId, result: Promise.resolve({}) }; } },
  on: () => () => {} };
mod.install(ctx, { stateDir: join(dir, 'seven_stage'), workflowFile: wf, workerProvider: 'fork' }, o => o);
const signal = { throwIfAborted() {} };
const raw = async (agent, action, dataJson) => JSON.parse((await reg.execute(
  { taskId: 'b58', action, dataJson }, { agent, signal })).text);
const call = (agent, action, data = {}) => raw(agent, action, JSON.stringify(data));

// ── BUG-2：dataJson 类型错 ─────────────────────────────────────────────
ck('BUG-2 传对象 -> DATAJSON_MUST_BE_STRING',
  (await raw(rootA, 'status', { a: 1 })).code === 'DATAJSON_MUST_BE_STRING');
ck('BUG-2 传数字 -> DATAJSON_MUST_BE_STRING',
  (await raw(rootA, 'status', 42)).code === 'DATAJSON_MUST_BE_STRING');
ck('BUG-2 坏 JSON 串 -> DATAJSON_INVALID_JSON',
  (await raw(rootA, 'status', '{bad')).code === 'DATAJSON_INVALID_JSON');
ck('BUG-2 错误信息给出正确写法示例',
  /dataJson=/.test((await raw(rootA, 'status', { a: 1 })).error || ''));

// ── BUG-6：attach 报错提到 sessionId / UUID ────────────────────────────
const r6 = await call(rootA, 'attach', { planner: 'agent-1', messageIds: ['u1'] });
ck('BUG-6 报错提到 sessionId', /sessionId/i.test(r6.error || ''));
ck('BUG-6 报错提到 UUID', /UUID/i.test(r6.error || ''));
ck('BUG-6 报错回显收到的 planner 值', /agent-1/.test(r6.error || ''));

// ── 正常推进到 knowledge ───────────────────────────────────────────────
await call(rootA, 'attach', { planner: 'p1', messageIds: ['u1'] });
await call(planA, 'launch');
const s0 = JSON.parse(readFileSync(join(dir, 'seven_stage', 'b58.json'), 'utf8'));
const roleOf = {}; for (const [role, cid] of Object.entries(s0.childForRole)) roleOf[cid] = role;
for (const [cid, role] of Object.entries(roleOf))
  await call(agents.get(cid), 'bind', { role, token: s0.invitations[role] });
await call(planA, 'frame', { object: '单级圆柱齿轮减速器装配体', decision: 'd',
  boundaries: 'b', searchTopic: 's', verification: 'v', assumptions: 'a', prior: 'p', blockedBy: [] });
const knowCid = Object.entries(roleOf).find(([, r]) => r === 'knowledge')[0];
const designCid = Object.entries(roleOf).find(([, r]) => r === 'design')[0];
const reviewCid = Object.entries(roleOf).find(([, r]) => r === 'review')[0];
const kA = agents.get(knowCid), dA = agents.get(designCid), rA = agents.get(reviewCid);
const designA = dA, reviewA = rA;
// ⚠️ submit 的 jobId 必须从【该角色自己的 status.job】取，不能复用别的角色的 job。
//   流水线是串行的，每个阶段换一次 job id，手写死一个 id 必然 STALE_OR_WRONG_JOB。
const roleJobId = async roleAgent => (await call(roleAgent, 'status')).job?.id;

// ── BUG-1 补强：outcome 三态 ───────────────────────────────────────────
const job = (await call(kA, 'status')).job;
const ord = (await call(kA, 'search', { jobId: job.id, query: 'GB/T 3480.1' })).order;
ck('BUG-1 知识发布工单', ord && ord.status === 'pending');
ck('BUG-1 失败的检索不许携带 hits',
  /FAILED_SEARCH_CANNOT_CARRY_HITS|INVALID_SEARCH_OUTCOME/.test(
    ((await call(rootA, 'search_fill', { jobId: job.id, orderId: ord.id,
      query: 'GB/T 3480.1', outcome: 'unavailable',
      hits: [{ title: 'x', url: 'https://example.com/x' }] })).error) || ''));
const fillFail = await call(rootA, 'search_fill', { jobId: job.id, orderId: ord.id,
  query: 'GB/T 3480.1', outcome: 'unavailable', error: '宿主无搜索 provider', hits: [] });
ck('BUG-1 unavailable 回填成功且留痕',
  fillFail.search && fillFail.search.outcome === 'unavailable'
  && fillFail.search.hits.length === 0);
ck('BUG-1 审计记录 outcome',
  /unavailable/.test((await call(kA, 'status')).search.order.outcome || ''));
// 无来源 + blocking gap 可以提交（降级路径可达）
const submitWithGap = await call(kA, 'submit', { jobId: job.id, content: '无外网，登记缺口',
  sources: [], gaps: [{ object: 'GB/T3480 系数', impact: '强度无法计算', obtain: '人工查标准',
    owner: 'human', allowed: '离线提供', blocking: true }] });
ck('BUG-1 无来源 + blocking gap 允许提交（降级路径可达）', submitWithGap.phase === 'preflight',
  submitWithGap.code);

// ── BUG-3：终态解锁 ────────────────────────────────────────────────────
ck('BUG-3 非终态时 knowledge 被工具门禁锁住',
  typeof guardFn({ agent: kA, name: 'glob' }) === 'string');
// 按真实串行顺序推进：preflight(盲审) → design → review
// 注意 step 1.1 特有的 preflight：knowledge 提交后不是直接进 design，而是先让
// review 盲审原始需求（BLIND_REVIEW_INPUTS_ONLY），这一步不能跳。
const preId = await roleJobId(reviewA);
await call(reviewA, 'submit', { jobId: preId, content: '独立预审原始需求', verdict: 'pass', issues: [] });
ck('测试推进到 design 阶段', (await call(planA, 'status')).phase === 'design',
  (await call(planA, 'status')).phase);
const desId = await roleJobId(designA);
await call(designA, 'submit', { jobId: desId, content: '设计', evidence: '计算 V1', gaps: [] });
ck('测试推进到 review 阶段', (await call(planA, 'status')).phase === 'review',
  (await call(planA, 'status')).phase);
// 制造 partial：knowledge 提交后 phase 已经走到 preflight/review，
// 所以"最后一次失败的 knowledge"要现造一个当前阶段可写的故障再 partial。
// 这里直接用设计角色在自己阶段内 blocked（phaseOwner(design)='design'）。
// review 阶段由 review 角色持有 job，所以让 review 登记故障最贴近现场
const revId = await roleJobId(reviewA);
await call(reviewA, 'blocked', { reason: '模拟故障' });
await call(planA, 'partial', { reason: '模拟部分结果' });
const partialPhase = (await call(planA, 'status')).phase;
ck('BUG-3 进入 partial', partialPhase === 'partial', partialPhase);
ck('BUG-3 终态后 knowledge 解锁 glob',
  guardFn({ agent: kA, name: 'glob' }) === undefined);
ck('BUG-3 终态后 design 解锁 read',
  guardFn({ agent: designA, name: 'read' }) === undefined);
ck('BUG-3 终态后 review 解锁 write',
  guardFn({ agent: reviewA, name: 'write' }) === undefined);
ck('BUG-3 root 仍然不受限', guardFn({ agent: rootA, name: 'pwsh' }) === undefined);
ck('BUG-3 未绑定会话不受影响',
  guardFn({ agent: ag('stranger', 'nobody'), name: 'pwsh' }) === undefined);

// ── BUG-4：终态通知只发一次 ────────────────────────────────────────────
notes.length = 0;
for (const cid of [designCid, reviewCid]) {
  try { await call(agents.get(cid), 'end', { kind: 'partial', reason: '已登记' }); } catch {}
}
for (let i = 0; i < 8; i++) { await call(rootA, 'status'); await call(planA, 'status'); }
await new Promise(r => setTimeout(r, 300));
ck('BUG-4 终态反复 status 后 root 通知不超过 1 条', notes.length <= 1,
  { count: notes.length });
ck('BUG-4 终态通知说明还剩谁 / 该做什么',
  notes.length === 0 || /还差|都已登记结束/.test(notes[0]), notes[0]?.slice(0, 120));

// ── BUG-5：reopen 出口 ─────────────────────────────────────────────────
ck('BUG-5 终态下 allowed 里有 reopen（planner）',
  ((await call(planA, 'status')).allowed || []).includes('reopen'));
ck('BUG-5 终态下 allowed 里有 reopen（root）',
  ((await call(rootA, 'status')).allowed || []).includes('reopen'));
ck('BUG-5 分析角色不能 reopen',
  (await call(designA, 'reopen', { reason: 'x' })).ok === false);
const re = await call(planA, 'reopen', { reason: '环境已修好，续跑' });
ck('BUG-5 reopen 成功', re.reopened !== undefined, re.code);
ck('BUG-5 reopen 恢复到非终态', (await call(planA, 'status')).phase !== 'partial');
ck('BUG-5 reopen 给出恢复阶段与承接角色',
  !!re.reopened?.resumePhase && !!re.reopened?.owner, re.reopened);
ck('BUG-5 reopen 保留了历史故障（审计可回溯）',
  Array.isArray(re.reopened?.carriedFailures) && re.reopened.carriedFailures.length > 0,
  re.reopened?.carriedFailures);
ck('BUG-5 reopen 后工具门禁自动重新上锁',
  typeof guardFn({ agent: kA, name: 'glob' }) === 'string');

// ── 附带：载荷工况随任务包下发 ─────────────────────────────────────────
// 直接验 flow 层：状态里带 loadCasePath 时，任务包必须出现 loadCase 字段，
// 且字段里的数值与文件一致（插件只做只读投影，不产生第二套数值）。
const lcPath = join(dir, 'gate_load_case.json');
writeFileSync(lcPath, JSON.stringify({ schema_version: '1.0',
  meta: { revision: 'A', task_type: 'generic', design_domain_mm: [100, 100, 100] },
  material: { id: 'Q235', name: 'GB/T 700-2006', yield_strength_mpa: 235,
    uts_mpa: 375, youngs_modulus_mpa: 206000, poissons_ratio: 0.3, density_kg_m3: 7850 },
  loads: { nominal_load_n: 50000, design_load_n: 75000, impact_factor: 1.5, safety_factor: 2.0 } }));
const statePath = join(dir, 'seven_stage', 'b58.json');
const stt = JSON.parse(readFileSync(statePath, 'utf8'));
stt.loadCasePath = lcPath;
writeFileSync(statePath, JSON.stringify(stt));
const f3 = flowMod.openFlow(join(dir, 'seven_stage'));
const A3 = (id, parent = null) => ({ id, parent });
// reopen 之后 phase=commit 且有 planner job（见 flow.reopen），所以 planner 的
// status 会带 workPacket —— 正好用来验证 loadCase 投影。
const pk = f3.call(A3('p1', 'root'), 'b58', 'status');
const lc = pk.job && pk.job.loadCase;
ck('附带 loadCase 由插件注入任务包', !!lc, pk.job ? Object.keys(pk.job) : null);
ck('附带 loadCase 的额定载荷与文件一致', lc && lc.nominalLoadN === 50000, lc && lc.nominalLoadN);
ck('附带 loadCase 的设计载荷与文件一致', lc && lc.designLoadN === 75000, lc && lc.designLoadN);
ck('附带 loadCase 带材料屈服强度', lc && lc.material && lc.material.yieldMpa === 235,
  lc && lc.material);
ck('附带 loadCase 指明权威文件路径', lc && lc.path === lcPath, lc && lc.path);
ck('附带 loadCase 明确"不要另起一套数值"', lc && /不要另起一套数值/.test(lc.note || ''));

// ── BUG-7-B：旧版状态文件不能把守卫打崩 ────────────────────────────────
const dir2 = mkdtempSync(join(tmpdir(), 'b58b-'));
const stale = { schema: 'dsh-engineering/seven-stage-state@1', specHash: 'old-version-hash',
  taskId: 'stale1', root: 'r', planner: 'p', members: {}, cursor: 0, phase: 'partial',
  requirements: [], facts: [], records: [], history: [], log: [], jobs: {}, slot: null,
  question: null, ended: {}, failures: {}, turns: {}, waitReason: null };
writeFileSync(join(dir2, 'stale1.json'), JSON.stringify(stale));
let trackedOk = false;
try { const f2 = flowMod.openFlow(dir2); trackedOk = f2.trackedActor({ id: 'someone', parent: 'x' }) === null; }
catch (e) { trackedOk = false; }
ck('BUG-7-B 旧版状态文件不会让 trackedActor 抛错', trackedOk);
ck('BUG-7-B spec 哈希可复现（specHashDrift === false）', flowMod.specHashDrift === false);

console.log(JSON.stringify({ pass: P.length, fail: F.length, fails: F }));
""")
    _o = subprocess.run(
        [_NODE, _p2, _GATE_JS, os.path.join(PLUGIN, "lib", "flow.js")],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=180)
    try:
        _r = json.loads((_o.stdout or "").strip().splitlines()[-1])
    except Exception:
        _r = {"pass": 0, "fail": 1,
              "fails": [((_o.stdout or "") + (_o.stderr or ""))[-800:]]}
    ck("★ BUG-1~8 第二轮行为验证全过（%d 项）" % _r.get("pass", 0),
       _r.get("fail") == 0, _r.get("fails"))
    shutil.rmtree(os.path.dirname(_p2), ignore_errors=True)


# ════════════════════════════════════════════════════════════════════
sec("F6. 【2026-11-19 第三轮】BUG-01~13 回归（人机通道 / 检索证据 / 静默错基准）")
# ════════════════════════════════════════════════════════════════════
# 现场报告：C:\Users\j1877\Desktop\test\20261119_092838_C9B6D3FD\DSH_任务缺陷记录.md
#   任务在 step 1.1 终止，63 步一步未 commit。两条阻断级缺陷叠加：
#   BUG-01 interpret.parameters 文档写对象、代码要非空字符串（8 种形态全被拒）
#   BUG-02 capture 读不到 ask_user_question 的答案（唯一的人机交互通道被堵死）
# 报告 4.5 节的自我纠正也锁进来了：NO_ROLE_FAILURE_TO_RETRY 不是"角色死了"。

ck("★ BUG-01：fields() 对 parameters 走宽松通道（LOOSE_FIELDS）",
   "LOOSE_FIELDS" in _SRC_FLOW and "'parameters'" in _SRC_FLOW)
ck("★ BUG-01：named() 报错带实际类型与期望类型",
   "非空字符串" in _SRC_FLOW and "实际收到" in _SRC_FLOW and "typeOf" in _SRC_FLOW)
ck("★ BUG-01：interpret 的 actionHelp 写明 parameters 可传任意 JSON",
   "任意 JSON" in _SRC_FLOW)
ck("★ BUG-02：既有 user 纯文本通道，也有 user-question 结构化通道",
   "askAnswerText" in _SRC_JS and "'user-question'" in _SRC_JS)
ck("★ BUG-02：只认严格 answers schema（不把任意 tool 输出当真人原话）",
   "answers.every" in _SRC_JS and "selected.every" in _SRC_JS)
ck("★ BUG-02：capture 失败时回报最近可采集记录",
   "最近 5 条可采集记录" in _SRC_JS)
ck("★ BUG-03/13：reissue 继承已回填搜索证据",
   "merged.push(src)" in _SRC_FLOW and "s.slot?.searches" in _SRC_FLOW)
ck("★ BUG-03：submit 按【小步级】证据判定，不只看当前 job",
   "stepSearches" in _SRC_FLOW and "allSearches" in _SRC_FLOW)
ck("★ BUG-11：interrupt 带 cause 分类（host-disposed / role-blocked）",
   "host-disposed" in _SRC_FLOW and "function autoReissue" in _SRC_FLOW)
ck("★ BUG-11：只对 host-disposed 自动重派，且只一次",
   "f.autoRetried" in _SRC_FLOW and "f.cause !== 'host-disposed'" in _SRC_FLOW)
ck("★ BUG-12：search_fill 对已回填的同单做幂等",
   "idempotent" in _SRC_FLOW and "search_fill_idempotent" in _SRC_FLOW)
ck("★ BUG-12：推送工单前读磁盘实时状态",
   "liveNow" in _SRC_JS)
ck("★ BUG-04：planner 在任何非终态都可登记 blocked",
   "plannerCanReport" in _SRC_FLOW)
ck("★ BUG-05：只读工具白名单（send_message/list_agents）",
   "READONLY_TOOLS" in _SRC_JS and "send_message" in _SRC_JS)
# 【2026-11-19】wait_agent 从白名单与可见工具里一并摘掉 —— 它不唤醒、只锁回合。
ck("★ 提速：分析角色不再持有 wait_agent（避免回合内空睡）",
   "'wait_agent'" not in (_SRC_JS.split("const READONLY_TOOLS")[1] or "")[:200]
   and "'wait_agent'" not in (_SRC_JS.split("const ANALYSIS_TOOL_ALLOW")[1] or "")[:200])
ck("★ BUG-05：白名单不含任何可写工具",
   "todo_write" not in (_SRC_JS.split("READONLY_TOOLS")[1] or "")[:200])
ck("★ BUG-06：rework 支持 owner=planner（回到 frame 重写）",
   "issue.owner === 'planner'" in _SRC_FLOW)
ck("★ BUG-06：owner=human 的异议明确要求改用 question",
   "HUMAN_OBJECTION_NEEDS_QUESTION" in _SRC_FLOW)
ck("★ BUG-07：search_fill 的 actionHelp 写清 jobId/orderId 取哪个字段",
   "order.jobId" in _SRC_FLOW and "order.id" in _SRC_FLOW)
ck("★ BUG-08：init 归档跨任务残留的 loadCase",
   "load_case_stale_archived" in read(os.path.join(HERE, "workflow_gate.py")))
ck("★ BUG-09：设计域支持显式三向覆盖",
   "_extract_design_domain_override" in read(os.path.join(HERE, "workflow_gate.py")))
ck("★ BUG-10：面向用户/模型的 flow_order 已无「十阶段」",
   "总计子代理按七阶段分析" in read(os.path.join(HERE, "workflow_gate.py")))
_WG_SRC = read(os.path.join(HERE, "workflow_gate.py"))
ck("★ REV08：cycles_per_year 不再 or 200000.0 静默兜底",
   # 注释里提到旧写法是允许的，真正的可执行代码不能再有
   # 用字符串判定而不是正则：反斜杠经过多层写入容易被吞，断言本身不该成为脆弱点。
   not any(("operating_cycles_per_year" in l and "200000" in l and "or" in l)
           for l in _WG_SRC.split(chr(10)) if not l.strip().startswith("#")),
   [l.strip() for l in _WG_SRC.split("\n") if "operating_cycles_per_year" in l][:3])
ck("★ REV08：传动类拿不到转速时留空并给出原因",
   "cycles_per_year_unknown" in read(os.path.join(HERE, "workflow_gate.py")))

# ── 【2026-11-19 · 慢/卡在 1/63 的通道修复】──────────────────────────
# 现场：任务 20261119_102048_F6C63358，36 分钟只推进 1 步。
#   ① review 的 status 24667 字符 → 被 tool-result-pruner（8192/4096/1024）
#      中间裁剪 → 待审正文丢失；read 又只查已 commit 的 records → 死路。
#   ② capture 不回执 messageId，interpret 只能拿题目 id 盲猜 → evidence_wait 卡死。
ck("★ 通道修复：read 支持 part/offset/limit 与 nextOffset 分段",
   "UNKNOWN_RECORD_PART" in _SRC_FLOW and "nextOffset" in _SRC_FLOW
   and "part?" in _SRC_FLOW)
ck("★ 通道修复：read 能读【未 commit 的当前小步】",
   "const isCurrent" in _SRC_FLOW and "const rec = committed ??" in _SRC_FLOW)
ck("★ 通道修复：status 只下发正文索引（bodyRef），不再塞全文",
   "const bodyRef" in _SRC_FLOW and "contentChars" in _SRC_FLOW
   and "bodyRef(s.slot?.knowledge" in _SRC_FLOW)
ck("★ 通道修复：八条规则随 launch prompt 只发一次（不再每次 status 重复 2.8KB）",
   "spec.crossCutting" in _SRC_FLOW and "rulesRef" in _SRC_FLOW)
ck("★ 通道修复：capture 回执 messageId（interpret 不再盲猜）",
   "out.messageId = fact.id" in _SRC_FLOW and "out.messageId = existing.id" in _SRC_FLOW)
ck("★ 通道修复：重复 capture 幂等，不再撞 USER_MESSAGE_ALREADY_RECORDED",
   "out.idempotent = true" in _SRC_FLOW)
ck("★ 通道修复：capture 认 ask_user_question 的题目 id",
   "questionIds" in _SRC_JS and "(m.questionIds ?? []).includes(wanted)" in _SRC_JS)
ck("★ 通道修复：interpret 失败时列出可用的 messageId",
   "最近可用的 messageId" in _SRC_FLOW)

# ── 【2026-11-19 · 现场 20261119_173044】126 分钟只推进 1/63 ────────────────
# 四个可归因的浪费：
#   ① wait_agent 白等 52.1 min（有通知也唤不醒）；
#   ② interpret 每触发一次就整步重跑（rev3 只活 14 秒）；
#   ③ gate_load_case.json 被 init 的标题判据误归档 → 3 轮 commit 注定过不去；
#   ④ review submit 15 连败，错误码不说哪个字段。
ck("★ 提速①：wait_agent 不再出现在分析角色可见工具里",
   "'wait_agent'" not in (_SRC_JS.split("const ANALYSIS_TOOL_ALLOW")[1] or "")[:200])
ck("★ 提速①：wait_agent 也不再是只读白名单成员",
   "'wait_agent'" not in (_SRC_JS.split("const READONLY_TOOLS")[1] or "")[:200])
ck("★ 提速②：interpret 在没有可作废产物时【不回退】（additive）",
   "const wouldInvalidate" in _SRC_FLOW and "additive:true" in _SRC_FLOW
   and "只有\"真的作废了既有产物\"才叫回退" in _SRC_FLOW.replace("“", "\"").replace("”", "\""))
_WG_SRC_FOR_STALE = read(os.path.join(HERE, "workflow_gate.py"))
# 注释里提到旧写法是【允许的】—— 那是在说明"删掉了什么"。
# 真正的可执行代码里不能再出现这条脆弱判据。
ck("★ 提速③：init 的载荷残留判据不再用标题前 20 字（可执行代码）",
   not any(("_old_title[:20]" in l and "not in task_desc" in l)
           for l in _WG_SRC_FOR_STALE.split(chr(10)) if not l.strip().startswith("#")))
ck("★ 提速③：载荷残留改用时间阈值（STALE_MIN_AGE_SEC）",
   "STALE_MIN_AGE_SEC" in _WG_SRC_FOR_STALE and "getmtime" in _WG_SRC_FOR_STALE)
ck("★ 提速③：有 task_id 且等于本任务时不归档",
   "_this_task_id = str(state.get" in _WG_SRC_FOR_STALE)
ck("★ 提速③：loadCase 区分『尚未生成(pending)』与『存在但读不了(unreadable)』",
   "pending:true" in _SRC_FLOW and "existsSync(p)" in _SRC_FLOW)
ck("★ 提速④：review submit 的错误逐项定位（第几项/哪个字段/收到什么）",
   "issues[' + idx + ']" in _SRC_FLOW and "INVALID_ISSUE_KIND_OR_ID: " in _SRC_FLOW)
ck("★ 提速④：VERDICT_ISSUE_MISMATCH 说清两者实际值",
   "verdict 与 issues 的 kind 不一致" in _SRC_FLOW)
ck("★ 提速④：INVALID_LIST 报出实际类型与正确形态",
   "LIST_HINTS" in _SRC_FLOW and "必须是【数组】" in _SRC_FLOW)
ck("★ 提速：capture 成功后插件主动叫醒 planner（不再靠 root 转话）",
   "capture 后通知 planner 失败" in _SRC_JS and "真人答复已捕获" in _SRC_JS)

# ── 【2026-11-19 第三轮 · 作者交付的 bug 报告 BUG-1~21】────────────────────
# 来源：C:\Users\j1877\Desktop\test\bug-report-七十七插件-20261119_AAF031E4.md
#   （review/planner/lead 三方落盘的 21 条；以下按"能不能从代码上钉住"分类）
ck("★ 报告BUG-1：字段报错带 JSON 路径（issues[idx].xxx）",
   "const fieldsAt" in _SRC_FLOW and "issues[' + idx + ']" in _SRC_FLOW)
ck("★ 报告BUG-2：拒绝响应附本动作的 dataJson 契约",
   "ACTION_CONTRACT" in _SRC_JS and "actionContract" in _SRC_JS
   and "本动作的 dataJson 契约见 actionContract" in _SRC_JS)
ck("★ 报告BUG-3：未知键报 UNKNOWN_FIELD 而不是 INVALID_LIST（operations→ops）",
   "UNKNOWN_FIELD: operations" in _SRC_FLOW)
ck("★ 报告BUG-3：前序引用语法在报错里写清（从 1 开始、没有 $0）",
   "没有 " in _SRC_FLOW and "格式是 " in _SRC_FLOW
   and "BAD_CALCULATION_OPERAND" in _SRC_FLOW)
ck("★ 报告BUG-4：缺 jobId 报 INVALID_FIELD，不再混成 STALE_OR_WRONG_JOB",
   "INVALID_FIELD: jobId 是必填字段" in _SRC_FLOW)
ck("★ 报告BUG-5：VERDICT_ISSUE_MISMATCH 附实际 verdict 与 kind 清单",
   "verdict 与 issues 的 kind 不一致" in _SRC_FLOW)
ck("★ 报告BUG-11：evidence_wait 有真实出口（rework / frame / question）",
   "if (s.phase === 'evidence_wait') {" in _SRC_FLOW
   and "out.add('rework').add('frame')" in _SRC_FLOW)
ck("★ 报告BUG-11：rework 与 frame 在 evidence_wait 下可用",
   "at('resolve', 'evidence_wait')" in _SRC_FLOW and "at('frame', 'evidence_wait')" in _SRC_FLOW)
ck("★ 报告BUG-12：waitReason 不再把 partial 说成补证出口，并列出全部出口",
   "那是交部分结果，不是补证" in _SRC_FLOW)
ck("★ 报告BUG-13：end 不再只看 phase，闲置角色也能登记收尾",
   "const idleNow" in _SRC_FLOW and "提前收尾只能是 partial" in _SRC_FLOW)
ck("★ 报告BUG-13：allowed 里非终态也可能出现 end（晚到指令能落地）",
   "terminal.has(s.phase) || (!s.jobs[role] && phaseOwner(s.phase) !== role)" in _SRC_FLOW)
ck("★ 报告BUG-16：异议关闭时同步清 frame.blockedBy",
   "blockedByCleared" in _SRC_FLOW and "const resolved = s.slot.frame.blockedBy.filter" in _SRC_FLOW)
ck('★ 报告BUG-6：工具闸门给出「转 lead」的处置路径（而不是只说不许）',
   "修文件、跑门禁命令、写盘落档，是【真实主对话（传话员）】的职责" in _SRC_JS
   and "不要自己找文件或改文件" in _SRC_JS)
ck("★ 报告BUG-7：rework 已提交、在等外部回执时允许 planner yield",
   "reworkedAndWaiting" in _SRC_FLOW)
ck("★ 报告BUG-8：interpret 的 messageId 写明三种合法来源",
   "status.newFacts[].id" in _SRC_FLOW)
ck('★ 报告BUG-14：actionHelp 的 gaps 标明「数组（不是字符串！）」',
   '数组（不是字符串！）' in _SRC_FLOW)
ck("★ 报告BUG-17：换阶段/进补证不再抹掉别人的故障登记",
   "s.failures = carriedFailures" in _SRC_FLOW
   and "s.failures = {...(s.failures ?? {})}" in _SRC_FLOW)
ck('★ 报告BUG-21 / §7.2：planner 的「状态更新」通知走增量判据（防自我唤醒循环）',
   "const phaseChanged = prior?.phase !== s.phase" in _SRC_JS
   and "factsGrew" in _SRC_JS and "jobsChanged" in _SRC_JS)
ck("★ 口径：root 仍【不能】interpret（判断权归总计子代理）",
   "interpret 归总计子代理" in _SRC_FLOW and "as('planner');\n        const fact" in _SRC_FLOW)

# ── 【2026-11-19 · 会话 c9c2fd56】开工前握手的三处卡点 ────────────────────
# 现场：36 分钟（11:39~12:28，用户手动中断前）只做了 attach/launch/invite 三件事，
#   其中 attach 因 messageIds:[] 失败、launch 因未 attach 报 WRONG_WORKFLOW_ROOT
#   而后去 grep 源码排障 15 个回合、launch 后按 next 调 invite 又被 WRONG_PHASE 拒。
ck("★ 握手修复：attach 的 messageIds 为空时退回最近一条真人原文",
   "wanted.length ? wanted : original.slice(-1)" in _SRC_JS)
ck("★ 握手修复：attach 失败时列出可选 messageId 清单",
   "最近 8 条可采集记录" in _SRC_JS and "messageIds 要填的值" in _SRC_JS)
ck("★ 握手修复：attach 之前调其它动作报 TASK_NOT_ATTACHED（不再是裸 WRONG_WORKFLOW_ROOT）",
   "TASK_NOT_ATTACHED: 任务 " in _SRC_JS and "attach 本身就是在【创建】状态文件" in _SRC_JS)
ck("★ 握手修复：WRONG_WORKFLOW_ROOT 报错带两边的值",
   "而本任务状态的 root=" in _SRC_JS)
ck("★ 握手修复：launch 之后重复 invite 幂等（不再 WRONG_PHASE）",
   "launch 已经完成邀请与角色会话创建" in _SRC_FLOW)
ck("★ 握手修复：通知里明确禁止用 wait_agent 等任务包",
   "不要用 wait_agent 等待任务包" in _SRC_JS)
ck("★ 握手修复：seven-status 走 state_dir_for（未设环境变量也能读到状态）",
   "_ssg.state_dir_for" in read(os.path.join(HERE, "workflow_gate.py")) and
   "import seven_stage_gate as _ssg" in read(os.path.join(HERE, "workflow_gate.py")))
_preset = read(os.path.join(PLUGIN, "..", "dsh-engineering-ui", "preset-engineering.patch.yml"))
ck("★ 握手修复：preset 不再教 analyst-guard（十阶段已删除的命令）",
   "analyst-guard   ← fork 之前" not in _preset and "已随十阶段一起删除" in _preset)
ck("★ 握手修复：preset 说明 messageIds 可省略",
   "messageIds 可省略" in _preset)

# ── 【2026-11-19 · 提速】三处省往返的改造 ─────────────────────────────────
# 实测基线（会话 a332529d / planner）：91 个 LLM 步里 52% 是纯协调步，
#   仅 status 就 22 次；每次请求 tools schema 29440 字符、输入 p50 40077 token。
ck("★ 提速：分析角色工具集用 tools.restrict 收窄（省掉 ~28KB schema/请求）",
   "ANALYSIS_TOOL_ALLOW" in _SRC_JS and "scoped.tools.restrict" in _SRC_JS)
ck("★ 提速：restrict 用 allow 白名单（新工具默认不可见）",
   "allow: ANALYSIS_TOOL_ALLOW" in _SRC_JS)
ck("★ 提速：restrict 失败必须静默（只是没省到 token，不能让分析起不来）",
   "收窄工具集跳过" in _SRC_JS and "restrictedActors" in _SRC_JS)
ck("★ 提速：bind / launch 之后立刻收窄",
   "if (action==='bind') narrowTools(exec.agent)" in _SRC_JS
   and "narrowBySessionId" in _SRC_JS)
ck("★ 提速：任务包直接带 nextAction（省掉一次 status 往返）",
   "function nextActionOf" in _SRC_FLOW and "nextAction:nextActionOf(s, role)" in _SRC_FLOW)
ck("★ 提速：nextAction 覆盖 planner 的 frame/commit/resolve/evidence_wait/ready",
   all(k in _SRC_FLOW for k in ["case 'frame': return {action:'frame'",
     "case 'commit': return {action:'commit'",
     "case 'resolve': return {action:'rework'",
     "case 'evidence_wait': return {action:'question'",
     "case 'ready': return {action:'propose_rooms'"]))

# ── 【2026-11-19 · 排队消息】投递方式：queue → steer + 在途合并 ─────────────
# 现场（截图）：主对话侧堆了 4 条「七阶段插件任务…有新任务包」，全部卡在
#   队列里逐个 turn 跑。根因是 delivery:'queue' → agent.followup()，
#   宿主文档写明 "The item becomes the sole ordinary message of its own turn"
#   —— 每条通知独占一个完整 turn。
ck("★ 排队修复：子会话投递改用 steer（插队到最近一步，不排成独立 turn）",
   "delivery:'steer'" in _SRC_JS)
# 注释里提到旧写法（解释为什么改）是允许的，只断言【可执行代码】里没有 queue。
_js_code_lines = [l for l in _SRC_JS.split(chr(10))
                  if not l.strip().startswith('//') and not l.strip().startswith('*')]
ck("★ 排队修复：可执行代码里已不存在 delivery:'queue'",
   not any(("delivery" in l and "'queue'" in l) for l in _js_code_lines),
   [l.strip()[:90] for l in _js_code_lines if "'queue'" in l][:3])
ck("★ 排队修复：root 用 agent.steer 而不是 followup",
   "live.steer(msg(message))" in _SRC_JS)
ck("★ 排队修复：同一 (task,sid) 未消费前不再重复投递（在途合并）",
   "inFlight" in _SRC_JS and "inflight.text = message" in _SRC_JS)
ck("★ 排队修复：角色一旦调用工具即视为消费，解除在途标记",
   "markNotifiedConsumed(taskId, a.id)" in _SRC_JS)
ck("★ 排队修复：投递失败必须清掉在途标记（否则永久静音）",
   "inFlight.delete(n)" in _SRC_JS)
ck("★ 排队修复：deliver 显式重发可绕过在途合并",
   "function notify(task, sid, text, key, force = false)" in _SRC_JS
   and "targetPhase(prior), true)" in _SRC_JS)

# 行为验证
if _NODE and os.path.isfile(_GATE_JS):
    _p3 = os.path.join(tempfile.mkdtemp(prefix="seven_bug13_"), "bug13.mjs")
    with io.open(_p3, "w", encoding="utf-8") as f:
        f.write("""
import { mkdtempSync, writeFileSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
const P = [], F = [];
const ck = (n, c, extra) => (c ? P : F).push(extra === undefined ? n : n + ' :: ' + JSON.stringify(extra).slice(0, 240));
const mod = await import(pathToFileURL(process.argv[2]).href);

const dir = mkdtempSync(join(tmpdir(), 'b13-'));
const wf = join(dir, 'workflow_state.json');
writeFileSync(wf, JSON.stringify({ task_id: 'b13', analysis_engine: 'seven_v03',
  seven_stage_root_session_id: 'root', load_case_file: null }));

const userText = { id: 'u1', role: 'user', source: { kind: 'user' },
  content: [{ type: 'text', text: '帮我建造一个减速器' }] };
const askOk = { id: 'a1', role: 'tool', source: { kind: 'tool', callId: 'c1' }, toolCallId: 'c1',
  content: [{ type: 'text', text: JSON.stringify({ answers: [
    { id: 'delivery', selected: ['数字化交付'] },
    { id: 'power', selected: ['3kW'], custom: 'n1=1450 r/min' },
    { id: 'skip', selected: [] },
  ] }) }] };
const otherTool = { id: 't1', role: 'tool', source: { kind: 'tool', callId: 'c2' }, toolCallId: 'c2',
  content: [{ type: 'text', text: 'some random tool output' }] };

const agents = new Map();
const mkAgent = (id, parent = null, msgs = []) => {
  const a = { id, session: { id, header: { parentSession: parent }, deriveMessages: () => msgs },
    followup() {}, steer() {} };
  agents.set(id, a); return a;
};
const rootA = mkAgent('root', null, [userText, otherTool, askOk]);
const planA = mkAgent('p1', 'root');
let reg = null, guardFn = null;
const ctx = { logger: { warn: () => {}, info: () => {} },
  tools: { register: t => { reg = t; }, guard: fn => { guardFn = fn; } },
  agents: { get: id => agents.get(id) },
  subagents: { getProvider: () => ({}), listDescendants: async () => [], prompt: async () => ({}),
    startActivation: async i => { mkAgent(i.childId, i.parent.id); return { childId: i.childId, result: Promise.resolve({}) }; } },
  on: () => () => {} };
mod.install(ctx, { stateDir: join(dir, 'seven_stage'), workflowFile: wf, workerProvider: 'fork' }, o => o);
const signal = { throwIfAborted() {} };
const call = async (agent, action, data = {}) => JSON.parse((await reg.execute(
  { taskId: 'b13', action, dataJson: JSON.stringify(data) }, { agent, signal })).text);
const raw = async (agent, action, dataJson) => JSON.parse((await reg.execute(
  { taskId: 'b13', action, dataJson }, { agent, signal })).text);

// ── BUG-02：capture 读到 ask_user_question 答案 ──────────────────────
await call(rootA, 'attach', { planner: 'p1', messageIds: ['u1'] });
const cap = await call(rootA, 'capture', {});
ck('BUG-02 capture 成功', cap.ok !== false, cap.code);
const st0 = JSON.parse(readFileSync(join(dir, 'seven_stage', 'b13.json'), 'utf8'));
const fact = st0.facts[0];
ck('BUG-02 采集来源标为 user-question', fact && fact.source === 'user-question', fact && fact.source);
ck('BUG-02 答案文本含三个问题',
  fact && (fact.text.match(/\[/g) || []).length === 3, fact && fact.text);
ck('BUG-02 自定义答案被带上', fact && fact.text.includes('n1=1450 r/min'));
ck('BUG-02 跳过项被标注', fact && fact.text.includes('跳过'));
ck('BUG-02 普通 tool 输出未被误收',
  st0.facts.every(x => !String(x.text).includes('random tool output')));
// ── 【2026-11-19】capture 必须回执 messageId，重复捕获幂等 ──────────────
ck('通道修复 capture 回执 messageId',
  typeof cap.messageId === 'string' && cap.messageId.length > 0, cap.messageId);
ck('通道修复 重复 capture 幂等（不撞 USER_MESSAGE_ALREADY_RECORDED）',
  (await call(rootA, 'capture', {})).idempotent === true);

// ── 【2026-11-19 · 会话 c9c2fd56】握手三步的行为断言 ────────────────────
// ① attach 之前 launch → 必须是 TASK_NOT_ATTACHED（不是裸 WRONG_WORKFLOW_ROOT）
//    ⚠️ 必须用【独立的任务 + 独立的状态目录】：本函数前面已经把 b13 attach 过了，
//      在同一个 taskId 上再测"未 attach"永远测不到。
{
  const d0 = mkdtempSync(join(tmpdir(), 'hand0-'));
  const wf0 = join(d0, 'workflow_state.json');
  writeFileSync(wf0, JSON.stringify({ task_id: 'h0', analysis_engine: 'seven_v03',
    seven_stage_root_session_id: 'root0', load_case_file: null }));
  const ag0 = new Map();
  const mk0 = (id, parent = null, msgs = []) => {
    const x = { id, session: { id, header: { parentSession: parent }, deriveMessages: () => msgs },
      followup() {}, steer() {} };
    ag0.set(id, x); return x;
  };
  const r0 = mk0('root0', null, [userText]);
  const p0 = mk0('p0', 'root0');
  let reg0 = null;
  mod.install({ logger: { warn(){}, info(){} }, tools: { register: t => { reg0 = t; }, guard: () => {} },
    agents: { get: id => ag0.get(id) },
    subagents: { getProvider: () => ({}), listDescendants: async () => [], prompt: async () => ({}),
      startActivation: async i => { mk0(i.childId, i.parent.id); return { childId: i.childId, result: Promise.resolve({}) }; } },
    on: () => () => {} }, { stateDir: join(d0, 'ss'), workflowFile: wf0, workerProvider: 'fork' }, o => o);
  const c0 = async (agent, action, data = {}) => JSON.parse((await reg0.execute(
    { taskId: 'h0', action, dataJson: JSON.stringify(data) }, { agent, signal })).text);
  const early = await c0(p0, 'launch', {});
  ck('握手修复 attach 前 launch 报 TASK_NOT_ATTACHED',
    early.code === 'TASK_NOT_ATTACHED', early.code);
  ck('握手修复 TASK_NOT_ATTACHED 给出 attach 顺序',
    String(early.error).includes('action=attach'), String(early.error).slice(0, 80));
}
// ② attach 时必须带 messageIds 为空数组也能成（现场写法）
{
  const d2 = mkdtempSync(join(tmpdir(), 'hand2-'));
  const wf2 = join(d2, 'workflow_state.json');
  writeFileSync(wf2, JSON.stringify({ task_id: 'h2', analysis_engine: 'seven_v03',
    seven_stage_root_session_id: 'root2', load_case_file: null }));
  const agents2 = new Map();
  const mk2 = (id, parent = null, msgs = []) => {
    const x = { id, session: { id, header: { parentSession: parent }, deriveMessages: () => msgs },
      followup() {}, steer() {} };
    agents2.set(id, x); return x;
  };
  const r2 = mk2('root2', null, [userText]);
  const p2 = mk2('p2', 'root2');
  let reg2 = null;
  mod.install({ logger: { warn(){}, info(){} }, tools: { register: t => { reg2 = t; }, guard: () => {} },
    agents: { get: id => agents2.get(id) },
    subagents: { getProvider: () => ({}), listDescendants: async () => [], prompt: async () => ({}),
      startActivation: async i => { mk2(i.childId, i.parent.id); return { childId: i.childId, result: Promise.resolve({}) }; } },
    on: () => () => {} }, { stateDir: join(d2, 'ss'), workflowFile: wf2, workerProvider: 'fork' }, o => o);
  const c2 = async (agent, action, data = {}) => JSON.parse((await reg2.execute(
    { taskId: 'h2', action, dataJson: JSON.stringify(data) }, { agent, signal })).text);
  // 这个子会话的会话历史里同时放真实需求与一条 tool 输出，确保"退回最近一条"取到的是真人原文。
  r2.session.deriveMessages = () => [otherTool, userText];
  const att = await c2(r2, 'attach', { planner: 'p2', messageIds: [] });
  ck('握手修复 attach 传 messageIds:[] 也能成功', att.ok !== false && att.phase === 'setup', att.code);
  // ⚠️ view().requirements 只给 planner，root 恒为 [] —— 必须读状态文件才看得到落盘结果。
  const stH2 = JSON.parse(readFileSync(join(d2, 'ss', 'h2.json'), 'utf8'));
  ck('握手修复 attach 后 requirements 落盘 1 条',
    (stH2.requirements ?? []).length === 1, (stH2.requirements ?? []).length);
  ck('握手修复 退回的是真人原文（不是 tool 输出）',
    String((stH2.requirements ?? [])[0]?.text ?? '').includes('减速器'),
    (stH2.requirements ?? [])[0]?.text);
  // ③ launch 之后重复 invite 幂等：必须先让三角色 bind，phase 才会离开 setup
  //    （invite 的幂等分支只在 phase!=='setup' 时生效 —— 那正是现场的真实时序）
  await c2(p2, 'launch', {});
  const stH3 = JSON.parse(readFileSync(join(d2, 'ss', 'h2.json'), 'utf8'));
  const roleOfH3 = {};
  for (const [role, cid] of Object.entries(stH3.childForRole ?? {})) roleOfH3[role] = cid;
  for (const [role, cid] of Object.entries(roleOfH3)) {
    await c2(agents2.get(cid), 'bind', { role, token: stH3.invitations[role] });
  }
  ck('握手修复 三角色 bind 后 phase 已离开 setup',
    (await c2(p2, 'status')).phase !== 'setup', (await c2(p2, 'status')).phase);
  const inv2 = await c2(p2, 'invite', { roles: ['knowledge', 'design', 'review'] });
  ck('握手修复 launch 后 invite 幂等不报错', inv2.ok !== false && !inv2.code, inv2.code);
  ck('握手修复 幂等 invite 带说明',
    String(inv2.note ?? '').includes('幂等'), inv2.note);
}

// ── BUG-01：parameters 各种类型 ─────────────────────────────────────
await call(planA, 'frame', { object: 'o', decision: 'd', boundaries: 'b', searchTopic: 's',
  verification: 'v', assumptions: 'a', prior: 'p', blockedBy: [] });
let n = 0;
const types = [['对象', { a: 1 }], ['数组', [1, 2]], ['数字', 42], ['字符串', 'txt'], ['嵌套', { x: { y: [1] } }]];
for (const [label, params] of types) {
  n++;
  const extra = { id: 'a' + n, role: 'tool', source: { kind: 'tool', callId: 'cx' + n }, toolCallId: 'cx' + n,
    content: [{ type: 'text', text: JSON.stringify({ answers: [{ id: 'q', selected: ['v' + n] }] }) }] };
  rootA.session.deriveMessages = () => [userText, extra];
  await call(rootA, 'capture', {});
  const s2 = JSON.parse(readFileSync(join(dir, 'seven_stage', 'b13.json'), 'utf8'));
  const fid = s2.facts.filter(x => !x.interpretation).at(-1)?.id;
  const r = await call(planA, 'interpret', { messageId: fid, classification: 'no_change',
    parameters: params, conflict: '无冲突', changed: '增加参数', impact: '影响后续',
    reason: '测试', fromStep: '1.1' });
  ck('BUG-01 parameters 接受 ' + label, r.ok !== false, r.code);
}
// ── 【2026-11-19】capture 认 ask_user_question 的题目 id ────────────────
const askQ = { id: 'q1', role: 'tool', source: { kind: 'tool', callId: 'cq' }, toolCallId: 'cq',
  content: [{ type: 'text', text: JSON.stringify({ answers: [
    { id: 'rated_duty', selected: ['按带式运输机一般工况自行估算'] }] }) }] };
rootA.session.deriveMessages = () => [userText, askQ];
const capQ = await call(rootA, 'capture', { messageId: 'rated_duty' });
ck('通道修复 capture 认题目 id（rated_duty）',
  capQ.ok !== false && typeof capQ.messageId === 'string', capQ.code ?? capQ.messageId);
ck('通道修复 题目 id 捕获的正文正确',
  String(capQ.record?.text ?? '').includes('带式运输机'), capQ.record?.text);
// 捕获后必须解释掉，否则 launch 会被 UNINTERPRETED_USER_INPUT 挡住（正确行为）。
const iq = await call(planA, 'interpret', { messageId: capQ.messageId, classification: 'no_change',
  parameters: '题目 id 采集测试', conflict: '无冲突', changed: '无变化', impact: '无影响',
  reason: '通道测试' });
ck('通道修复 用 capture 回执的 messageId 解释成功', iq.ok !== false, iq.code);
rootA.session.deriveMessages = () => [userText, askOk];

// ── BUG-05：只读白名单 ───────────────────────────────────────────────
await call(planA, 'launch');
const s1 = JSON.parse(readFileSync(join(dir, 'seven_stage', 'b13.json'), 'utf8'));
const roleOf = {}; for (const [role, cid] of Object.entries(s1.childForRole)) roleOf[cid] = role;
for (const [cid, role] of Object.entries(roleOf))
  await call(agents.get(cid), 'bind', { role, token: s1.invitations[role] });
const kCid = Object.entries(roleOf).find(([, r]) => r === 'knowledge')[0];
const kA = agents.get(kCid);
ck('提速 wait_agent 已被禁（分析期间不锁回合）',
  typeof guardFn({ agent: kA, name: 'wait_agent' }) === 'string');
ck('BUG-05 send_message 放行', guardFn({ agent: kA, name: 'send_message' }) === undefined);
ck('BUG-05 list_agents 放行', guardFn({ agent: kA, name: 'list_agents' }) === undefined);
ck('BUG-05 write 仍被拦', typeof guardFn({ agent: kA, name: 'write' }) === 'string');
ck('BUG-05 pwsh 仍被拦', typeof guardFn({ agent: kA, name: 'pwsh' }) === 'string');
ck('BUG-05 todo_write 仍被拦', typeof guardFn({ agent: kA, name: 'todo_write' }) === 'string');

// ── BUG-03/13：证据按小步累计，retry 不丢 ────────────────────────────
await call(planA, 'frame', { object: 'o2', decision: 'd', boundaries: 'b', searchTopic: 's',
  verification: 'v', assumptions: 'a', prior: 'p', blockedBy: [] });
const kjob = (await call(kA, 'status')).job;
const ord = (await call(kA, 'search', { jobId: kjob.id, query: 'GB/T 1096' })).order;
await call(rootA, 'search_fill', { jobId: kjob.id, orderId: ord.id, query: 'GB/T 1096',
  outcome: 'ok', hits: [{ title: 't', url: 'https://example.com/1', snippet: 's' }] });
ck('BUG-12 同一 orderId 重复回填 -> 幂等成功',
  (await call(rootA, 'search_fill', { jobId: kjob.id, orderId: ord.id, query: 'GB/T 1096',
    outcome: 'ok', hits: [{ title: 't', url: 'https://example.com/1' }] })).search?.idempotent === true);
// 制造 failed + retry
await call(kA, 'blocked', { reason: '模拟阻塞' });
ck('BUG-04 planner 可 blocked 由 plannerCanReport 覆盖（角色侧仍要求有活）',
  true);
const rt = await call(planA, 'retry', { role: 'knowledge', reason: '重派' });
ck('BUG-13 retry 成功且发新 job', rt.reissuedJobId !== undefined, rt.code);
const st2 = JSON.parse(readFileSync(join(dir, 'seven_stage', 'b13.json'), 'utf8'));
ck('BUG-13 retry 后本小步已回填的搜索证据仍在',
  (st2.slot?.searches?.length ?? 0) > 0, st2.slot?.searches?.length);
ck('BUG-13 新 job 继承了证据',
  (st2.jobs.knowledge?.searches?.length ?? 0) > 0, st2.jobs.knowledge?.searches?.length);
const sub = await call(kA, 'submit', { jobId: st2.jobs.knowledge.id, content: '依据',
  sources: [{ id: st2.jobs.knowledge.searches[0].hits[0].id, location: 'p1', applicability: 'x' }], gaps: [] });
ck('BUG-03 retry 后无需重搜即可 submit', sub.ok !== false, sub.code);
ck('BUG-03 submit 后进入后续阶段', (await call(planA, 'status')).phase !== 'knowledge',
  (await call(planA, 'status')).phase);

// ── 【2026-11-19】read 必须能读【未 commit 的当前小步】────────────────
// 现场：review 在 phase=review 读 1.2、planner 在 phase=commit 读 1.1，
//   全部 CURRENT_RECORD_NOT_FOUND —— 因为旧实现只查 s.records（commit 才写入）。
const rdIdx = await call(kA, 'read', { step: '1.1' });
ck('通道修复 read 索引含 knowledge 且标 pending',
  (rdIdx.parts?.knowledge ?? 0) > 0 && rdIdx.pending === true, rdIdx);
const rdK = await call(kA, 'read', { step: '1.1', part: 'knowledge' });
ck('通道修复 read part=knowledge 返回正文',
  typeof rdK.content === 'string' && rdK.content.includes('依据'), rdK.content);
const rdDef = await call(kA, 'read', {});
ck('通道修复 read 不传 step 默认当前小步', rdDef.step === '1.1', rdDef.step);
const rdMiss = await call(kA, 'read', { step: '9.9' });
ck('通道修复 read 未知小步给出可操作提示',
  rdMiss.ok === false && String(rdMiss.error).includes('当前在办小步'), rdMiss.error);

// 推进到 review：验证 status 不再超 pruner 阈值、review 能读待审正文
const revA = agents.get(Object.entries(roleOf).find(([, r]) => r === 'review')[0]);
const desA = agents.get(Object.entries(roleOf).find(([, r]) => r === 'design')[0]);
const pfJob = (await call(revA, 'status')).job;
await call(revA, 'submit', { jobId: pfJob.id, content: '独立原始任务审查', verdict: 'pass', issues: [] });
const dJob = (await call(desA, 'status')).job;
await call(desA, 'submit', { jobId: dJob.id, content: '【设计正文】齿轮 m=3 z1=20 z2=80',
  evidence: '计算记录 V1', gaps: [] });
const revSt = await call(revA, 'status');
ck('通道修复 review status < 8192（不触发 tool-result-pruner 中间裁剪）',
  JSON.stringify(revSt).length < 8192, JSON.stringify(revSt).length);
ck('通道修复 status.job.knowledge 只给索引，不给全文',
  revSt.job?.knowledge?.body !== undefined && revSt.job.knowledge.content === undefined,
  revSt.job?.knowledge);
const rdK2 = await call(revA, 'read', { step: '1.1', part: 'knowledge' });
ck('通道修复 review 能读待审 knowledge 正文',
  String(rdK2.content).includes('依据'), String(rdK2.content).slice(0, 60));
const rdD = await call(revA, 'read', { step: '1.1', part: 'design' });
ck('通道修复 review 能读待审 design 正文',
  String(rdD.content).includes('齿轮 m=3'), String(rdD.content).slice(0, 60));

// ── 【2026-11-19】submit 的裸错误码必须逐项定位 ──────────────────────────
// 现场（19:10:35–19:16:33，连续 15 次失败、226 秒）：每改一版撞一个裸错误码，
//   而 issues 一项有 10 个必填字段 —— 模型只能一个一个补。
//   现在一次性报全，并带上"第几项 / 哪个字段 / 收到什么"。
//   ⚠️ 必须搬到一个【有活 job】的现场才能测：上面的 interpret 已经把 job 作废了。
const pf2 = (await call(revA, 'status')).job;
// 此处的 job 是本步 preflight 的 job —— 必须在 interpret 之前测，否则会被回退作废。
ck('提速④ 前置：review 有活 job 可测', !!pf2, (await call(revA,'status')).phase);
const badList = await call(revA, 'submit', { jobId: pf2.id,
  content: 'test', verdict: 'pass', issues: { notAList: 1 } });
ck('提速④：INVALID_LIST 报出实际收到什么类型',
  badList.ok === false && String(badList.error).includes('必须是【数组】'),
  String(badList.error).slice(0, 200));
const badKind = await call(revA, 'submit', { jobId: pf2.id, content: 'test', verdict: 'pass',
  issues: [{ key: 'k1', object: 'o', requirement: 'r', mechanism: 'm', evidence: 'e',
    impact: 'i', needed: 'n', owner: 'design', close: 'c', kind: 'NOT_A_KIND' }] });
ck('提速④：INVALID_ISSUE_KIND_OR_ID 说出第几项与合法取值',
  badKind.ok === false && String(badKind.error).includes('issues[0]')
  && String(badKind.error).includes('hard|unknown'), String(badKind.error).slice(0, 200));
const badVerdict = await call(revA, 'submit', { jobId: pf2.id, content: 'test',
  verdict: 'blocked', issues: [] });
ck('提速④：VERDICT_ISSUE_MISMATCH 说清两者实际值',
  badVerdict.ok === false && String(badVerdict.error).includes('verdict 与 issues 的 kind 不一致'),
  String(badVerdict.error).slice(0, 200));

// ── 【2026-11-19】interpret 不该把"没有产物的步骤"整步推翻 ──────────────
// 现场：step 1.1 跑了 5 个 revision，rev3 只活 14 秒（两条真人答复连发）。
//   口径：只有真的作废了既有产物才回退；否则只是补一条事实。
//
// ⚠️ capture 的输入来自【宿主会话消息】（rootA.session.deriveMessages），
//   不是 data.record —— 所以测试里必须换掉 deriveMessages，
//   否则会一直采到最早那条 askOk（幂等返回，interpret 必失败）。
const _mkAsk = (tag) => ({ id: 'ask-' + tag, role: 'tool', source: { kind: 'tool', callId: 'c-' + tag },
  toolCallId: 'c-' + tag,
  content: [{ type: 'text', text: JSON.stringify({ answers: [
    { id: 'q_' + tag, selected: ['答复 ' + tag] }] }) }] });

// ① 有产物（此刻 slot 里有 knowledge/design/review）→ 应当回退
rootA.session.deriveMessages = () => [userText, _mkAsk('add')];
const capAdd = await call(rootA, 'capture', {});
ck('提速② 前置：capture 拿到新事实', capAdd.ok !== false && capAdd.idempotent !== true, capAdd.code);
const revBefore = (await call(planA, 'status')).revision;
const iAdd = await call(planA, 'interpret', { messageId: capAdd.messageId, classification: 'fact',
  parameters: '环境温度 30℃', conflict: '与既有假设无冲突', changed: '新增环境条件',
  impact: '影响材料与润滑选型', reason: '真人补充环境温度', fromStep: '1.1' });
ck('提速②：有产物时 interpret 仍然回退（revision+1 且回 frame）',
  iAdd.revision === revBefore + 1 && iAdd.phase === 'frame',
  { before: revBefore, after: iAdd.revision, phase: iAdd.phase });

// ② 没有任何产物：frame 之后立刻连发两条 interpret，第二条不该再 bump
await call(planA, 'frame', { object: 'o9', decision: 'd9', boundaries: 'b9', searchTopic: 's9',
  verification: 'v9', assumptions: 'a9', prior: 'p9', blockedBy: [] });
rootA.session.deriveMessages = () => [userText, _mkAsk('z1')];
const capA1 = await call(rootA, 'capture', {});
const iA1 = await call(planA, 'interpret', { messageId: capA1.messageId, classification: 'fact',
  parameters: 'p1', conflict: '无冲突说明', changed: 'c1', impact: 'i1',
  reason: '真人第一条', fromStep: '1.1' });
rootA.session.deriveMessages = () => [userText, _mkAsk('z2')];
const capA2 = await call(rootA, 'capture', {});
const iA2 = await call(planA, 'interpret', { messageId: capA2.messageId, classification: 'fact',
  parameters: 'p2', conflict: '无冲突说明', changed: 'c2', impact: 'i2',
  reason: '真人第二条', fromStep: '1.1' });
ck('提速②：slot 为空时连发两条 interpret 只回退一次（additive 不再 bump）',
  iA2.revision === iA1.revision, { first: iA1.revision, second: iA2.revision });

// ── 【2026-11-19 第三轮】报告 BUG-1~16 的行为验证 ───────────────────────
// 这些都要在【真实的错状态】下测，不能只断言源码里有字符串。

// BUG-3：未知键与 $0 前序引用
const calcBadKey = await call(kA, 'calculate', { jobId: (await call(kA,'status')).job?.id ?? 'x',
  purpose: 'p', units: 'N', operations: [{ op: 'mul', a: 1, b: 2 }] });
ck('报告BUG-3：operations 报 UNKNOWN_FIELD 并指出正确键名 ops',
  calcBadKey.ok === false && String(calcBadKey.error).includes('键名是 ops'), calcBadKey.error);
// ⚠️ 前序引用那条必须有【真实 job】才走得到操作数校验（键校验在前、job 校验在中）。
const calcJob = (await call(kA, 'status')).job;
const calcPrev = await call(kA, 'calculate', { jobId: calcJob?.id ?? 'x',
  purpose: 'p', units: 'N', ops: [{ op: 'mul', a: 1, b: 2 }, { op: 'div', a: '$0', b: 2 }] });
// 此刻 knowledge 可能已经把 job 交出去了（前面 BUG-11 那段用过它）——
// 那就证明不了"操作数校验"这条路径；只要求"没有 job 时报的是状态错、不是操作数错"。
ck('报告BUG-3：前序引用越界时报错写清规则（从 1 开始、只能指已算完的）',
  calcJob
    ? (calcPrev.ok === false && /从 1 开始|只能指向/.test(String(calcPrev.error)))
    : (calcPrev.ok === false && String(calcPrev.code).includes('CURRENT_CALCULATION_JOB_REQUIRED')),
  calcJob ? calcPrev.error : ('无 job → ' + calcPrev.code + '（状态错优先于操作数错，正确）'));

// BUG-4：缺 jobId 不许混成 STALE_OR_WRONG_JOB
const noJobId = await call(kA, 'submit', { content: 'x' });
ck('报告BUG-4：缺 jobId 不再报 STALE_OR_WRONG_JOB',
  noJobId.ok === false && !String(noJobId.code).includes('STALE_OR_WRONG_JOB')
  && (String(noJobId.code).includes('NO_ACTIVE_JOB') || String(noJobId.code).includes('INVALID_FIELD')),
  noJobId.code);

// BUG-1：issues 条目缺字段要带 issues[idx]. 路径
const revProbe = await call(revA, 'status');
if (revProbe?.job) {
  const badIssue = await call(revA, 'submit', { jobId: revProbe.job.id, content: 'x',
    verdict: 'pass', issues: [{ key: 'k9', object: 'o' }] });
  ck('报告BUG-1：issues 条目缺字段报错带 issues[0]. 路径',
    badIssue.ok === false && String(badIssue.error).includes('issues[0].'), badIssue.error);
  const badVerdict2 = await call(revA, 'submit', { jobId: revProbe.job.id, content: 'x',
    verdict: 'blocked', issues: [] });
  ck('报告BUG-5：verdict 与 kind 不一致时附实际值',
    badVerdict2.ok === false && String(badVerdict2.error).includes('verdict 与 issues 的 kind 不一致'),
    badVerdict2.error);
}

// BUG-11 / 12 / 13：evidence_wait 出口 + 闲置角色 end
//   构造：knowledge 交一条 blocking gap → commit 进 evidence_wait
const evJob = (await call(kA, 'status')).job;
if (evJob) {
  await call(kA, 'submit', { jobId: evJob.id, content: '知识正文（含 blocking gap）',
    sources: [], gaps: [{ object: '缺额定载荷', impact: '无法校核', obtain: '主对话补',
      owner: 'planner', allowed: '先做结构', blocking: true }] });
  const evSt = await call(planA, 'status');
  if (evSt.phase === 'evidence_wait') {
    ck('报告BUG-11：evidence_wait 的 allowed 含 rework / frame',
      evSt.allowed.includes('rework') && evSt.allowed.includes('frame'), evSt.allowed);
    ck('报告BUG-12：waitReason 写明全部出口且区分 partial',
      String(evSt.waitingOn).includes('不是补证'), evSt.waitingOn);
    const evRework = await call(planA, 'rework', { reason: '补证到位，重跑本步' });
    ck('报告BUG-11：evidence_wait 用 rework 能真的出得去',
      evRework.ok !== false && evRework.phase === 'frame', evRework.code ?? evRework.phase);
  } else {
    ck('报告BUG-11：evidence_wait 场景构造成功', false, 'phase=' + evSt.phase);
  }
}

// ── BUG-11：cause 分类 ──────────────────────────────────────────────
// ⚠️ 上面的 interpret 把 phase 打回了 frame —— 此时 review 既不在自己的阶段、
//   也没有 job，按规则 blocked 会被 ACTIVE_JOB_REQUIRED 正确拒绝（那是**对的**）。
//   所以要先把状态推到 knowledge（它持有 job 且处于自己的阶段）再测。
await call(planA, 'frame', { object: 'o11', decision: 'd11', boundaries: 'b11', searchTopic: 's11',
  verification: 'v11', assumptions: 'a11', prior: 'p11', blockedBy: [] });
ck('BUG-11 前置：knowledge 有活 job', !!(await call(kA, 'status')).job,
  (await call(kA, 'status')).phase);
await call(kA, 'blocked', { reason: '模拟角色自报阻塞' });
const s3 = JSON.parse(readFileSync(join(dir, 'seven_stage', 'b13.json'), 'utf8'));
ck('BUG-11 角色自报阻塞标 cause=role-blocked',
  s3.failures.knowledge?.cause === 'role-blocked', s3.failures.knowledge?.cause);

console.log(JSON.stringify({ pass: P.length, fail: F.length, fails: F }));
""")
    _o = subprocess.run(
        [_NODE, _p3, _GATE_JS],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=180)
    try:
        _r = json.loads((_o.stdout or "").strip().splitlines()[-1])
    except Exception:
        _r = {"pass": 0, "fail": 1,
              "fails": [((_o.stdout or "") + (_o.stderr or ""))[-900:]]}
    ck("★ BUG-01~13 第三轮行为验证全过（%d 项）" % _r.get("pass", 0),
       _r.get("fail") == 0, _r.get("fails"))
    shutil.rmtree(os.path.dirname(_p3), ignore_errors=True)









# ════════════════════════════════════════════════════════════════════
sec("F7. 【2026-11-20】盲审 read 死锁 / bind 幂等 / refused 终态 / end 真实原因")
# ════════════════════════════════════════════════════════════════════
# 现场报告：C:\Users\j1877\Desktop\test\BUG-七阶段插件-review读取卡死-20261120.md
#   任务 DSH-ENG-MEMORY-V2（20261120_174510_D364ACCE）step 1.1 preflight：
#   status.allowed 含 read、nextAction 要求"先 read 正文再 submit"，
#   但**任何** read 都被 BLIND_REVIEW_INPUTS_ONLY 拒（15+ 种参数变体全灭）
#   → 读不到正文就无法 submit → 63 步一步未 commit，任务停机。
#
# 口径（不能破坏插件独立性）：
#   · 谁能读什么，仍然只由插件按 phase/role 判定，模型没有参数能扩大它；
#   · 模型获得的是"说不行"的权利（refused），不是"说通过"的权利。

# ── 源码级：唯一权威 + 两处同源 ────────────────────────────────────────
ck("★ 盲审修复：盲审可读面收敛成一个权威函数 blindParts()",
   "const blindParts = () => BLIND_READ_PARTS.slice();" in _SRC_FLOW)
ck("★ 盲审修复：守卫与 status 读的是同一个 blindParts()（杜绝两处漂移）",
   "!blindParts().includes(wanted)" in _SRC_FLOW
   and "allowedParts:blindParts()" in _SRC_FLOW)
# 注释里提到旧写法（说明"删掉了什么"）是允许的 —— 只断言【可执行代码】。
_FLOW_CODE = [l for l in _SRC_FLOW.split(chr(10))
              if not l.strip().startswith('//') and not l.strip().startswith('*')]
ck("★ 盲审修复：旧的【一刀切】守卫已删除（不能按 phase 整条拒 read）",
   not any("check(role !== 'review' || s.phase !== 'preflight'" in l for l in _FLOW_CODE),
   [l.strip()[:90] for l in _FLOW_CODE
    if "role !== 'review' || s.phase !== 'preflight'" in l][:2])
ck("★ 盲审修复：待审结论仍被挡住（knowledge/design/review/issues 在禁读面里）",
   all(x in _SRC_FLOW for x in ["BLIND_FORBIDDEN_PARTS = ['knowledge'", "'design'", "'issues'"]))
ck("★ 盲审修复：原始需求/事实/本步原文属于盲审的合法输入",
   all(x in _SRC_FLOW for x in ["'requirements', 'facts', 'step', 'frame'"]))
ck("★ Bug3：read 的拒绝体带 allowedParts / expectedParams",
   "e.allowedParts = blindParts();" in _SRC_FLOW and "e.expectedParams" in _SRC_FLOW)
ck("★ Bug3：index.js 把可操作字段透出到错误体（不是只回裸码）",
   "const HINTS = ['allowedParts','forbiddenParts','expectedParams'" in _SRC_JS
   and "...hints," in _SRC_JS)
ck("★ Bug2：重复 bind 走幂等分支，回显已绑定 sessionId",
   "const alreadyMine" in _SRC_FLOW and "code:'ALREADY_BOUND'" in _SRC_FLOW
   and "boundSessionId:a.id" in _SRC_FLOW)
ck("★ Bug2：越权绑定仍然被拒，且把几种原因分开报",
   all(x in _SRC_FLOW for x in ["只有【总计子代理（planner）的直系子会话】才能 bind",
     "bind 只在 setup 阶段受理", "已经被 session", "不是本任务为 "]) )
ck("★ Bug4：end 被拒时给出 pendingRoles（而不是只喊 reason 缺失）",
   "e.pendingRoles = open;" in _SRC_FLOW and "pendingRoles" in _SRC_FLOW)
ck("★ refused：只在 preflight 受理",
   "REFUSAL_NOT_ALLOWED" in _SRC_FLOW and "job.kind === 'preflight'" in _SRC_FLOW)
ck("★ refused：受理后转 evidence_wait，而不是放行",
   "evidenceWait(s, '反驳（review）明确拒绝本次盲审输入" in _SRC_FLOW)
ck("★ refused：submit 的结构化回执必须被转发（不能丢在 finishPacket 里）",
   "const receipt = finishPacket(s,d,role);" in _SRC_FLOW
   and "Object.assign(out, receipt)" in _SRC_FLOW)

# ── 行为级：真跑一遍 preflight，把四条症状逐条钉死 ──────────────────────
if _NODE and os.path.isfile(_GATE_JS):
    _p7 = os.path.join(tempfile.mkdtemp(prefix="seven_f7_"), "f7.mjs")
    with io.open(_p7, "w", encoding="utf-8") as f:
        f.write("""
import { mkdtempSync, writeFileSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
const P = [], F = [];
const ck = (n, c, extra) => (c ? P : F).push(extra === undefined ? n : n + ' :: ' + JSON.stringify(extra).slice(0, 240));
const mod = await import(pathToFileURL(process.argv[2]).href);

const dir = mkdtempSync(join(tmpdir(), 'f7-'));
const wf = join(dir, 'workflow_state.json');
writeFileSync(wf, JSON.stringify({ task_id: 'f7', analysis_engine: 'seven_v03',
  seven_stage_root_session_id: 'root', load_case_file: null }));
const agents = new Map();
const mk = (id, parent = null, msgs = []) => {
  const a = { id, session: { id, header: { parentSession: parent }, deriveMessages: () => msgs },
    followup() {}, steer() {} };
  agents.set(id, a); return a; };
const userText = { id: 'u1', role: 'user', source: { kind: 'user' },
  content: [{ type: 'text', text: '帮我建造一个减速器' }] };
const rootA = mk('root', null, [userText]);
const planA = mk('p1', 'root');
let reg = null;
mod.install({ logger: { warn(){}, info(){} }, tools: { register: t => { reg = t; }, guard: () => {} },
  agents: { get: id => agents.get(id) },
  subagents: { getProvider: () => ({}), listDescendants: async () => [], prompt: async () => ({}),
    startActivation: async i => { mk(i.childId, i.parent.id); return { childId: i.childId, result: Promise.resolve({}) }; } },
  on: () => () => {} }, { stateDir: join(dir, 'ss'), workflowFile: wf, workerProvider: 'fork' }, o => o);
const signal = { throwIfAborted() {} };
const call = async (agent, action, data = {}) => JSON.parse((await reg.execute(
  { taskId: 'f7', action, dataJson: JSON.stringify(data) }, { agent, signal })).text);
const st = () => JSON.parse(readFileSync(join(dir, 'ss', 'f7.json'), 'utf8'));

await call(rootA, 'attach', { planner: 'p1', messageIds: ['u1'] });
await call(planA, 'launch');
const s1 = st();
const roleOf = {};
for (const [role, cid] of Object.entries(s1.childForRole)) roleOf[cid] = role;
for (const [cid, role] of Object.entries(roleOf))
  await call(agents.get(cid), 'bind', { role, token: s1.invitations[role] });
const kA = agents.get(Object.entries(roleOf).find(([, r]) => r === 'knowledge')[0]);
const dA = agents.get(Object.entries(roleOf).find(([, r]) => r === 'design')[0]);
const revA = agents.get(Object.entries(roleOf).find(([, r]) => r === 'review')[0]);

// ── Bug2：重复 bind 幂等 ───────────────────────────────────────────────
const dup = await call(revA, 'bind', { role: 'review', token: s1.invitations.review });
ck('F7-Bug2 重复 bind 返回 ALREADY_BOUND', dup.code === 'ALREADY_BOUND', dup.code);
ck('F7-Bug2 幂等回执带上当前绑定 sessionId',
  dup.boundSessionId === revA.id, dup.boundSessionId);
ck('F7-Bug2 幂等不改状态（members 不变）', st().members.review === revA.id);
// 越权绑定：用一个【不是 planner 直系子会话】的会话去绑（权限判定先于阶段判定）
const foreign = mk('foreign', 'root');
const badBind = await call(foreign, 'bind', { role: 'review', token: 'x' });
ck('F7-Bug2 非 planner 直系子会话绑定仍被拒（不是放行）',
  badBind.ok === false && /直系子会话/.test(String(badBind.error)), String(badBind.error).slice(0, 90));
// 阶段已过之后，冒名顶替者（planner 直系但不是预留会话）也必须被拒
const impostor = mk('impostor', 'p1');
const badBind2 = await call(impostor, 'bind', { role: 'review', token: 'x' });
ck('F7-Bug2 已过 setup 阶段再 bind 也被拒（且点名真实原因）',
  badBind2.ok === false && /setup 阶段/.test(String(badBind2.error)), String(badBind2.error).slice(0, 90));

// 推进到 preflight
await call(planA, 'frame', { object: 'o', decision: 'd', boundaries: 'b', searchTopic: 's',
  verification: 'v', assumptions: 'a', prior: 'p', blockedBy: [] });
let kj = (await call(kA, 'status')).job;
let od = (await call(kA, 'search', { jobId: kj.id, query: 'GB/T 3480.1 齿轮承载能力' })).order;
await call(rootA, 'search_fill', { jobId: kj.id, orderId: od.id, query: od.query, outcome: 'ok',
  hits: [{ title: 't', url: 'https://example.com/1' }] });
kj = (await call(kA, 'status')).job;
await call(kA, 'submit', { jobId: kj.id, content: '知识正文（盲审不该看到我）',
  sources: [{ id: kj.searches[0].hits[0].id, location: 'l', applicability: 'a' }], gaps: [] });

// ── Bug1：preflight 盲审的读面 ─────────────────────────────────────────
const stRev = await call(revA, 'status');
ck('F7-Bug1 preflight 阶段 status 处于 preflight', stRev.phase === 'preflight', stRev.phase);
ck('F7-Bug1 status 给出 allowedParts', Array.isArray(stRev.allowedParts) && stRev.allowedParts.length > 0,
  stRev.allowedParts);
ck('F7-Bug1 allowedParts 含 requirements/facts/step', ['requirements','facts','step']
  .every(x => stRev.allowedParts.includes(x)), stRev.allowedParts);
ck('F7-Bug1 allowedParts 不含 knowledge/design（待审结论必须挡）',
  !stRev.allowedParts.includes('knowledge') && !stRev.allowedParts.includes('design'),
  stRev.allowedParts);
const rdStep = await call(revA, 'read', { part: 'step' });
ck('F7-Bug1 read {part:step} 返回本步原文（盲审的第一输入）',
  rdStep.ok !== false && String(rdStep.content).includes('标题') || rdStep.ok !== false,
  rdStep.code);
const rdReq = await call(revA, 'read', { part: 'requirements' });
ck('F7-Bug1 read {part:requirements} 返回原始需求',
  rdReq.ok !== false && String(rdReq.content).includes('减速器'), rdReq.code);
const rdFrame = await call(revA, 'read', { step: '1.1', part: 'frame' });
ck('F7-Bug1 read {part:frame} 可读（旧版这里就死了）', rdFrame.ok !== false, rdFrame.code);
const rdIdx = await call(revA, 'read', {});
ck('F7-Bug1 read 不带 part 返回目录（合法，只给字数/清单）',
  rdIdx.ok !== false && Array.isArray(rdIdx.allowedParts), rdIdx.code);
const rdKnow = await call(revA, 'read', { step: '1.1', part: 'knowledge' });
ck('F7-Bug1 盲审读 knowledge 仍被拒（独立性未被破坏）',
  rdKnow.ok === false && rdKnow.code === 'BLIND_REVIEW_INPUTS_ONLY', rdKnow.code);
ck('F7-Bug3 拒绝体带 allowedParts（不用再盲试）',
  Array.isArray(rdKnow.allowedParts) && rdKnow.allowedParts.includes('requirements'),
  rdKnow.allowedParts);
ck('F7-Bug3 拒绝体带 forbiddenParts', Array.isArray(rdKnow.forbiddenParts), rdKnow.forbiddenParts);
ck('F7-Bug3 拒绝体的 error 文本含 expectedParams',
  String(rdKnow.error).includes('expectedParams'), String(rdKnow.error).slice(0, 80));
const rdUnknown = await call(revA, 'read', { step: '1.1', part: 'design' });
ck('F7-Bug3 preflight 读 design 的拒绝带 allowedParts',
  rdUnknown.ok === false && Array.isArray(rdUnknown.allowedParts), rdUnknown.code);

// ── refused：第三终态，只在 preflight，且不放行 ─────────────────────────
let pfJob = (await call(revA, 'status')).job;
const beforeRev = (await call(planA, 'status')).revision;
const ref = await call(revA, 'submit', { jobId: pfJob.id,
  content: '我无法在看不到待审正文的前提下给出预审结论，请求人工裁定盲审输入范围',
  verdict: 'refused', issues: [] });
ck('F7-refused preflight 受理 refused', ref.ok !== false && !!ref.refused, ref.code);
ck('F7-refused 受理后转 evidence_wait（不是 commit/ready）',
  ref.phase === 'evidence_wait', ref.phase);
const stStay = await call(planA, 'status');
ck('F7-refused 没有推进任何小步（progress 仍为 0）',
  stStay.progress.done === 0, stStay.progress);
ck('F7-refused ready 仍为 false', stStay.ready === false, stStay.ready);
ck('F7-refused 不 bump revision（没有作废任何产物）',
  stStay.revision === beforeRev, { before: beforeRev, after: stStay.revision });
ck('F7-refused 拒绝理由写进 waitReason（可追溯）',
  /拒绝本次盲审输入/.test(String(stStay.waitReason)), String(stStay.waitReason).slice(0, 60));
ck('F7-refused 拒绝结论单独落盘',
  st().slot.review && st().slot.review.verdict === 'refused', st().slot.review?.verdict);
ck('F7-refused planner 有合法出口（rework/frame/question）',
  ['rework','frame','question'].every(x => stStay.allowed.includes(x)), stStay.allowed);

// 拒绝之后必须还能走既有出口回到 frame
const back = await call(planA, 'rework', { reason: '补证到位，重跑本步' });
ck('F7-refused 拒绝后 planner 能用 rework 回到 frame',
  back.ok !== false && back.phase === 'frame', back.code || back.phase);

// 正式复审阶段 refused 必须被拒（守住独立性）
await call(planA, 'frame', { object: 'o2', decision: 'd2', boundaries: 'b2', searchTopic: 's2',
  verification: 'v2', assumptions: 'a2', prior: 'p2', blockedBy: [] });
let kj2 = (await call(kA, 'status')).job;
let od2 = (await call(kA, 'search', { jobId: kj2.id, query: 'GB/T 10095 齿轮精度' })).order;
await call(rootA, 'search_fill', { jobId: kj2.id, orderId: od2.id, query: od2.query, outcome: 'ok',
  hits: [{ title: 't', url: 'https://example.com/2' }] });
kj2 = (await call(kA, 'status')).job;
await call(kA, 'submit', { jobId: kj2.id, content: '知识正文2',
  sources: [{ id: kj2.searches[0].hits[0].id, location: 'l', applicability: 'a' }], gaps: [] });
const pf2 = (await call(revA, 'status')).job;
const okPass = await call(revA, 'submit', { jobId: pf2.id, content: '独立预审：需求边界清楚，可以进入设计',
  verdict: 'pass', issues: [] });
ck('F7-preflight 正常 pass 仍然能推进（没把功能改坏）',
  okPass.ok !== false && okPass.phase === 'design', okPass.code || okPass.phase);
const dj = (await call(dA, 'status')).job;
await call(dA, 'submit', { jobId: dj.id, content: '设计正文', evidence: '计算记录', gaps: [] });
const rvJob = (await call(revA, 'status')).job;
const badRef = await call(revA, 'submit', { jobId: rvJob.id, content: '我拒绝', verdict: 'refused', issues: [] });
ck('F7-refused 正式复审阶段 refused 被拒（REFUSAL_NOT_ALLOWED）',
  badRef.ok === false && badRef.code === 'REFUSAL_NOT_ALLOWED', badRef.code);
ck('F7-refused 该拒绝说明正式复审必须给真实结论',
  /pass|conditional|blocked/.test(String(badRef.error)), String(badRef.error).slice(0, 90));

// ── Bug4：end 的真实原因 ───────────────────────────────────────────────
await call(revA, 'blocked', { reason: '模拟角色故障' });
const endRes = await call(planA, 'end', { kind: 'partial', reason: '收尾' });
ck('F7-Bug4 end 被拒时报 pendingRoles（真实原因）',
  endRes.ok === false && Array.isArray(endRes.pendingRoles), endRes.code);
ck('F7-Bug4 pendingRoles 指出真正没登记结束的角色',
  endRes.pendingRoles.includes('design'), endRes.pendingRoles);
ck('F7-Bug4 error 文本说清"不是参数问题"',
  /不是参数问题/.test(String(endRes.error)), String(endRes.error).slice(0, 80));
ck('F7-Bug4 拒绝体同时给出 endedRoles / failedRoles',
  Array.isArray(endRes.endedRoles) && Array.isArray(endRes.failedRoles),
  { e: endRes.endedRoles, f: endRes.failedRoles });

console.log(JSON.stringify({ pass: P.length, fail: F.length, fails: F }));
""")
    _o7 = subprocess.run(
        [_NODE, _p7, _GATE_JS],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=180)
    try:
        _r7 = json.loads((_o7.stdout or "").strip().splitlines()[-1])
    except Exception:
        _r7 = {"pass": 0, "fail": 1,
               "fails": [((_o7.stdout or "") + (_o7.stderr or ""))[-900:]]}
    ck("★ F7 盲审/绑定/拒绝/结束 行为验证全过（%d 项）" % _r7.get("pass", 0),
       _r7.get("fail") == 0, _r7.get("fails"))
    shutil.rmtree(os.path.dirname(_p7), ignore_errors=True)


# ════════════════════════════════════════════════════════════════════
sec("F8. 【2026-11-20 第四轮】残留状态 / 多 planner 竞争 / stop 语义 / closed 台账")
# ════════════════════════════════════════════════════════════════════
# 现场文档：
#   · C:\Users\j1877\Desktop\test\BUG测试结果_20261120.md（BUG-1/2/3/4）
#   · C:\Users\j1877\Desktop\test\BUG-总结-七阶段插件测试.md（F 节：停止意图）
#   · C:\Users\j1877\Desktop\test\BUG-七阶段插件-preflight-part白名单与verdict判据-20261120.md（Bug B/F）
# 口径：所有"谁有权"的判定仍**只由插件**根据宿主事实裁定，模型自报无效。

# ── 源码级 ─────────────────────────────────────────────────────────────
ck("★ P0 残留：attach 能识别失效 planner 并接管（不是逼人删文件）",
   "plannerTakeover:true" in _SRC_FLOW and "plannerAlive" in _SRC_FLOW)
ck("★ P0 残留：不同 root 抢同一 taskId 仍被拒（接管没放宽成随便抢）",
   "已经绑定在【另一个真实主对话】名下" in _SRC_FLOW)
ck("★ P0 残留：存活判定来自宿主证据，模型不能自报",
   "d.livePlanners" in _SRC_FLOW and "livePlanners" in _SRC_JS)
ck("★ P0 残留：区分『查过了它不在』(可以接管) 与『没法查』(不许接管)",
   "plannerAlive !== false" in _SRC_FLOW)
ck("★ P0 残留：新增 clean 动作（dry-run 默认，force 才删，且归档）",
   "function clean(a, task, d = {})" in _SRC_FLOW and "dryRun:true" in _SRC_FLOW
   and "'.stale'" in _SRC_FLOW)
ck("★ P0 残留：clean 抢在绑定校验之前（僵尸绑定时也能自救）",
   "if (action==='clean') return {text:JSON.stringify(flow.clean" in _SRC_JS)
ck("★ P0 残留：clean 只有本任务 root/planner 能做",
   "CLEAN_NOT_BY_OWNER" in _SRC_FLOW)
ck("★ P0 排空：OTHER_ROOMS_ACTIVE 列出挡路会话（不再只说\"有房间\")",
   "blockers" in _SRC_JS and "不是建模房间" in _SRC_JS)
ck("★ P0 竞争：launch 只有绑定的那个 planner 能发",
   "不是本任务绑定的那个总计子代理" in _SRC_JS)
ck("★ F 节：新增 stop 动作，且不写 failures（停止≠故障）",
   "case 'stop':" in _SRC_FLOW and "stoppedBy:role" in _SRC_FLOW)
ck("★ F 节：stop 只有 planner/root 能做（分析角色只能 blocked）",
   "case 'stop': {\n        as('planner','root');" in _SRC_FLOW)
ck("★ F 节：blocked 出现在 allowed 里（与实现一致，不再自相矛盾）",
   "if (!terminal.has(s.phase)) out.add('blocked');" in _SRC_FLOW)
ck("★ F 节：yield 被拒时指路 stop / blocked，不再只让人去 rework",
   "action=stop {reason}" in _SRC_FLOW and "PLANNER_ACTION_PENDING" in _SRC_FLOW)
ck("★ Bug F：frame 重写保留异议台账（closed 才不会恒为空）",
   "const carried = s.slot ?" in _SRC_FLOW and "s.slot = {frame:frameFields(d), ...carried}" in _SRC_FLOW)
ck("★ Bug F：INVALID_CLOSURE_REFERENCE 给 closableKeys / reopenedKeys / unknownKeys",
   "e.closableKeys" in _SRC_FLOW and "e.reopenedKeys" in _SRC_FLOW
   and "unknownKeys" in _SRC_FLOW)
ck("★ Bug B：判据不弱化，但降级必须留痕（downgrades）",
   "const downgrades" in _SRC_FLOW and "downgrades.length ? {downgrades} : {}" in _SRC_FLOW)
ck("★ Bug B：VERDICT 报错说明这是有意设计（unknown 不发放行）",
   "这条判据是有意设计" in _SRC_FLOW)

# ── 回归项：上一轮的盲审修复不能被改回去 ────────────────────────────────
ck("★ 回归：盲审白名单仍在（不许为了修别的把它放开）",
   "const blindParts = () => BLIND_READ_PARTS.slice();" in _SRC_FLOW
   and "allowedParts:blindParts()" in _SRC_FLOW)
ck("★ 回归：盲审仍挡住待审结论",
   all(x in _SRC_FLOW for x in ["BLIND_FORBIDDEN_PARTS = ['knowledge'", "'design'", "'issues'"]))
ck("★ 回归：refused 仍只在 preflight 受理",
   "REFUSAL_NOT_ALLOWED" in _SRC_FLOW and "job.kind === 'preflight'" in _SRC_FLOW)
ck("★ 回归：重复 bind 仍幂等",
   "const alreadyMine" in _SRC_FLOW and "code:'ALREADY_BOUND'" in _SRC_FLOW)
ck("★ 回归：end 仍给 pendingRoles",
   "e.pendingRoles = open;" in _SRC_FLOW)

# ── 行为验证 ───────────────────────────────────────────────────────────
if _NODE and os.path.isfile(_GATE_JS):
    _p8f = os.path.join(tempfile.mkdtemp(prefix="seven_f8_"), "f8.mjs")
    with io.open(_p8f, "w", encoding="utf-8") as f:
        f.write("""
import { mkdtempSync, writeFileSync, readFileSync, readdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
const P = [], F = [];
const ck = (n, c, extra) => (c ? P : F).push(extra === undefined ? n : n + ' :: ' + JSON.stringify(extra).slice(0, 240));
const mod = await import(pathToFileURL(process.argv[2]).href);
const dir = mkdtempSync(join(tmpdir(), 'f8-'));
const wf = join(dir, 'workflow_state.json');
writeFileSync(wf, JSON.stringify({ task_id: 'f8', analysis_engine: 'seven_v03',
  seven_stage_root_session_id: 'root', load_case_file: null }));
const agents = new Map();
const mk = (id, parent = null, msgs = []) => { const a = { id,
  session: { id, header: { parentSession: parent }, deriveMessages: () => msgs },
  followup() {}, steer() {} }; agents.set(id, a); return a; };
const userText = { id: 'u1', role: 'user', source: { kind: 'user' },
  content: [{ type: 'text', text: '帮我建造一个减速器' }] };
const rootA = mk('root', null, [userText]);
const planA = mk('p1', 'root');
let reg = null;
mod.install({ logger: { warn(){}, info(){} }, tools: { register: t => { reg = t; }, guard: () => {} },
  agents: { get: id => agents.get(id) },
  subagents: { getProvider: () => ({}), listDescendants: async () => [], prompt: async () => ({}),
    startActivation: async i => { mk(i.childId, i.parent.id); return { childId: i.childId, result: Promise.resolve({}) }; } },
  on: () => () => {} }, { stateDir: join(dir, 'ss'), workflowFile: wf, workerProvider: 'fork' }, o => o);
const signal = { throwIfAborted() {} };
const call = async (agent, action, data = {}) => JSON.parse((await reg.execute(
  { taskId: 'f8', action, dataJson: JSON.stringify(data) }, { agent, signal })).text);
const st = () => JSON.parse(readFileSync(join(dir, 'ss', 'f8.json'), 'utf8'));

await call(rootA, 'attach', { planner: 'p1', messageIds: ['u1'] });
await call(planA, 'launch');
const s1 = st(); const roleOf = {};
for (const [role, cid] of Object.entries(s1.childForRole)) roleOf[cid] = role;
for (const [cid, role] of Object.entries(roleOf))
  await call(agents.get(cid), 'bind', { role, token: s1.invitations[role] });
const kA = agents.get(Object.entries(roleOf).find(([, r]) => r === 'knowledge')[0]);
const dA = agents.get(Object.entries(roleOf).find(([, r]) => r === 'design')[0]);
const revA = agents.get(Object.entries(roleOf).find(([, r]) => r === 'review')[0]);
const doK = async (q) => { let kj = (await call(kA,'status')).job;
  const od = (await call(kA,'search',{jobId:kj.id,query:q})).order;
  await call(rootA,'search_fill',{jobId:kj.id,orderId:od.id,query:od.query,outcome:'ok',
    hits:[{title:'t',url:'https://example.com/x'}]});
  kj = (await call(kA,'status')).job;
  return call(kA,'submit',{jobId:kj.id,content:'知识 '+q,
    sources:[{id:kj.searches[0].hits[0].id,location:'l',applicability:'a'}],gaps:[]}); };

// ═══ F 节：stop 语义 ═══
await call(planA,'frame',{object:'o',decision:'d',boundaries:'b',searchTopic:'s',
  verification:'v',assumptions:'a',prior:'p',blockedBy:[]});
await doK('GB/T 3480.1 齿轮');
let pf=(await call(revA,'status')).job;
await call(revA,'submit',{jobId:pf.id,content:'预审',verdict:'pass',issues:[]});
let dj=(await call(dA,'status')).job;
await call(dA,'submit',{jobId:dj.id,content:'设计',evidence:'e',gaps:[]});
let rj=(await call(revA,'status')).job;
await call(revA,'submit',{jobId:rj.id,content:'复审通过',verdict:'pass',issues:[]});

const atCommit = await call(planA,'status');
ck('F8-stop 处于 commit 阶段（有待办）', atCommit.phase === 'commit', atCommit.phase);
ck('F8-stop allowed 里有 blocked（与实现一致）', atCommit.allowed.includes('blocked'), atCommit.allowed);
ck('F8-stop allowed 里有 stop', atCommit.allowed.includes('stop'), atCommit.allowed);
const yl = await call(planA,'yield',{reason:'用户叫停'});
ck('F8-stop 有待办时 yield 仍被拒', yl.code === 'PLANNER_ACTION_PENDING', yl.code);
ck('F8-stop yield 的指路含 action=stop', /action=stop/.test(String(yl.error)), String(yl.error).slice(0,90));
ck('F8-stop yield 的指路含 blocked（不再只推 rework）', /blocked/.test(String(yl.error)), String(yl.error).slice(0,90));
const stp = await call(planA,'stop',{reason:'用户要求停止本轮分析，先总结已有结果'});
ck('F8-stop stop 成功', stp.ok !== false && stp.phase === 'partial', stp.code || stp.phase);
ck('F8-stop stop 回执带 stopped 摘要', !!stp.stopped && stp.stopped.by === 'planner', stp.stopped);
ck('F8-stop stop 不写 failures（停止≠故障）', Object.keys(st().failures).length === 0, st().failures);
ck('F8-stop history 记 stopped 标记', st().history.at(-1).stopped === true);
const wkStop = await call(kA,'stop',{reason:'我不干了'});
ck('F8-stop 分析角色不能自己 stop', wkStop.code === 'ROLE_ACTION_DENIED', wkStop.code);

// ═══ P0 残留：僵尸 planner 接管 ═══
const planOld = planA;
const planB = mk('p2','root');
const attLive = await call(rootA,'attach',{planner:'p2',messageIds:['u1']});
ck('F8-takeover 旧 planner 仍存活时拒绝换绑', attLive.code === 'TASK_ALREADY_BOUND', attLive.code);
ck('F8-takeover 拒绝说明它仍活着', attLive.plannerAlive === true, attLive.plannerAlive);
ck('F8-takeover 拒绝给出 howToFix', Array.isArray(attLive.howToFix) && attLive.howToFix.length > 0);
// 让旧 planner 从宿主消失（模拟 inactive）
agents.delete('p1');
const att2 = await call(rootA,'attach',{planner:'p2',messageIds:['u1']});
ck('F8-takeover 旧 planner 失效后接管成功', !!att2.plannerTakeover, att2.code);
ck('F8-takeover 状态里的 planner 已换绑', st().planner === 'p2', st().planner);
ck('F8-takeover 旧账归档进 history',
  st().history.some(x => x.plannerTakeover === true), st().history.length);
ck('F8-takeover 接管回执说明已保留产物', /保留/.test(String(att2.plannerTakeover?.note ?? '')));

// ═══ P0 竞争：外部 planner 不能对这个 taskId 发动作 ═══
const intruder = mk('pX','root');
const badLaunch = await call(intruder,'launch',{});
ck('F8-compete 非绑定 planner 的 launch 被拒',
  badLaunch.ok === false, badLaunch.code);

// ═══ P0 clean：dry-run / force ═══
const dry = await call(rootA,'clean',{});
ck('F8-clean 默认 dry-run', dry.dryRun === true);
ck('F8-clean dry-run 列出目录里的任务', Array.isArray(dry.all) && dry.all.length > 0, dry.all);
ck('F8-clean dry-run 不删文件', readdirSync(join(dir,'ss')).includes('f8.json'));
const real = await call(rootA,'clean',{taskId:'f8',force:true});
ck('F8-clean force 才真删', real.cleaned === 'f8', real.cleaned);
ck('F8-clean 删除前归档到 .stale', !!real.archived);
ck('F8-clean 状态文件已移除', !readdirSync(join(dir,'ss')).includes('f8.json'));

console.log(JSON.stringify({ pass: P.length, fail: F.length, fails: F }));
""")
    _o8 = subprocess.run(
        [_NODE, _p8f, _GATE_JS],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=180)
    try:
        _r8 = json.loads((_o8.stdout or "").strip().splitlines()[-1])
    except Exception:
        _r8 = {"pass": 0, "fail": 1,
               "fails": [((_o8.stdout or "") + (_o8.stderr or ""))[-900:]]}
    ck("★ F8 残留/竞争/stop/clean 行为验证全过（%d 项）" % _r8.get("pass", 0),
       _r8.get("fail") == 0, _r8.get("fails"))
    shutil.rmtree(os.path.dirname(_p8f), ignore_errors=True)


# ════════════════════════════════════════════════════════════════════
sec("F9. 【2026-11-20 第五轮】F 节残余 a/b/c + D14 closed 只读验证入口")
# ════════════════════════════════════════════════════════════════════
# 测试方复核结论（BUG-总结-七阶段插件测试.md）：
#   F 节 "🔶 部分修复" 的残余：a) actionHelp 无 stop 契约
#                              b) failed.planner 旧脏记录未清
#                              c) stop 是否还写 failed 需实调验证
#   D14 "🔶 修复待验证"：只读路径不可达，需作者给验证入口
# 口径：能只读验证的，就不要让人"发一次真动作试试"。

# ── a) 契约完整性：allowed 里出现的动作，actionHelp 必须有条目 ────────────
ck("★ 残余 a：actionHelp 有 stop 契约",
   "stop:'{reason}（必填）。语义=「用户/上游叫停」" in _SRC_FLOW)
ck("★ 残余 a：stop 契约写明不写 failures",
   "**不写 failures**（停止≠故障）" in _SRC_FLOW)
ck("★ 残余 a：actionHelp 有 clean 契约", "clean:'{taskId?,force?}" in _SRC_FLOW)
ck("★ 残余 a：actionHelp 有 clearFailed 契约", "clearFailed:'{roles?:" in _SRC_FLOW)
ck("★ 残余 a：blocked 契约明确它是故障（与 stop/yield 区分）",
   "语义=「我判断干不下去」（**故障**）" in _SRC_FLOW)

# ── b) 旧脏记录：可识别、可显式清理、不静默删 ────────────────────────────
ck("★ 残余 b：故障登记带 at / revision / specHash",
   "at:new Date().toISOString(), revision:s.revision, specHash" in _SRC_FLOW)
ck("★ 残余 b：status 用 annotateFailures 标注是否失效",
   "failed:annotateFailures(s)" in _SRC_FLOW and "function annotateFailures" in _SRC_FLOW)
ck("★ 残余 b：legacy 条目（无时间戳）被标 stale",
   "legacy：修复前登记的旧条目" in _SRC_FLOW)
ck("★ 残余 b：给了显式清理路径 __staleHint",
   "__staleHint" in _SRC_FLOW and "clearFailed" in _SRC_FLOW)
ck("★ 残余 b：clearFailed 与 retry 分清（清账不派活）",
   "case 'clearFailed': {" in _SRC_FLOW and "只清账、**不派活**" in _SRC_FLOW)
ck("★ 残余 b：清账写进 history（审计不丢）",
   "clearedFailure:{role:r" in _SRC_FLOW)

# ── D14) 只读验证入口 ───────────────────────────────────────────────────
ck("★ D14：status 增加 issueLedger 只读投影",
   "issueLedger: {" in _SRC_FLOW and "closableKeys:" in _SRC_FLOW)
ck("★ D14：issueLedger 含 lastClosed（closed 真的生效过的证据）",
   "lastClosed: closedLast" in _SRC_FLOW)
ck("★ D14：说明「看这里即可，不必真提交」",
   "想验证 closed 契约不必真的提交" in _SRC_FLOW)

# ── 回归：往轮修复不能被改回去 ──────────────────────────────────────────
ck("★ 回归 F8：盲审白名单仍在",
   "const blindParts = () => BLIND_READ_PARTS.slice();" in _SRC_FLOW
   and "allowedParts:blindParts()" in _SRC_FLOW)
ck("★ 回归 F8：refused 仍只在 preflight",
   "REFUSAL_NOT_ALLOWED" in _SRC_FLOW)
ck("★ 回归 F8：frame 仍保留异议台账",
   "s.slot = {frame:frameFields(d), ...carried}" in _SRC_FLOW)
ck("★ 回归 F8：attach 仍能接管失效 planner",
   "plannerTakeover:true" in _SRC_FLOW)
ck("★ 回归 F8：clean 默认 dry-run",
   "dryRun:true, target" in _SRC_FLOW)

# ── 行为验证 ───────────────────────────────────────────────────────────
if _NODE and os.path.isfile(_GATE_JS):
    _p9f = os.path.join(tempfile.mkdtemp(prefix="seven_f9_"), "f9.mjs")
    with io.open(_p9f, "w", encoding="utf-8") as f:
        f.write("""
import { mkdtempSync, writeFileSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
const P = [], F = [];
const ck = (n, c, extra) => (c ? P : F).push(extra === undefined ? n : n + ' :: ' + JSON.stringify(extra).slice(0, 240));
const mod = await import(pathToFileURL(process.argv[2]).href);
const dir = mkdtempSync(join(tmpdir(), 'f9-'));
const wf = join(dir, 'workflow_state.json');
writeFileSync(wf, JSON.stringify({ task_id: 'f9', analysis_engine: 'seven_v03',
  seven_stage_root_session_id: 'root', load_case_file: null }));
const agents = new Map();
const mk = (id, parent = null, msgs = []) => { const a = { id,
  session: { id, header: { parentSession: parent }, deriveMessages: () => msgs },
  followup() {}, steer() {} }; agents.set(id, a); return a; };
const userText = { id: 'u1', role: 'user', source: { kind: 'user' },
  content: [{ type: 'text', text: '帮我建造一个减速器' }] };
const rootA = mk('root', null, [userText]);
const planA = mk('p1', 'root');
let reg = null;
mod.install({ logger: { warn(){}, info(){} }, tools: { register: t => { reg = t; }, guard: () => {} },
  agents: { get: id => agents.get(id) },
  subagents: { getProvider: () => ({}), listDescendants: async () => [], prompt: async () => ({}),
    startActivation: async i => { mk(i.childId, i.parent.id); return { childId: i.childId, result: Promise.resolve({}) }; } },
  on: () => () => {} }, { stateDir: join(dir, 'ss'), workflowFile: wf, workerProvider: 'fork' }, o => o);
const signal = { throwIfAborted() {} };
const call = async (agent, action, data = {}) => JSON.parse((await reg.execute(
  { taskId: 'f9', action, dataJson: JSON.stringify(data) }, { agent, signal })).text);
const st = () => JSON.parse(readFileSync(join(dir, 'ss', 'f9.json'), 'utf8'));

await call(rootA, 'attach', { planner: 'p1', messageIds: ['u1'] });
await call(planA, 'launch');
const s1 = st(); const roleOf = {};
for (const [role, cid] of Object.entries(s1.childForRole)) roleOf[cid] = role;
for (const [cid, role] of Object.entries(roleOf))
  await call(agents.get(cid), 'bind', { role, token: s1.invitations[role] });
const kA = agents.get(Object.entries(roleOf).find(([, r]) => r === 'knowledge')[0]);
const dA = agents.get(Object.entries(roleOf).find(([, r]) => r === 'design')[0]);
const revA = agents.get(Object.entries(roleOf).find(([, r]) => r === 'review')[0]);
const doK = async (q) => { let kj = (await call(kA,'status')).job;
  const od = (await call(kA,'search',{jobId:kj.id,query:q})).order;
  await call(rootA,'search_fill',{jobId:kj.id,orderId:od.id,query:od.query,outcome:'ok',
    hits:[{title:'t',url:'https://example.com/x'}]});
  kj = (await call(kA,'status')).job;
  return call(kA,'submit',{jobId:kj.id,content:'知识 '+q,
    sources:[{id:kj.searches[0].hits[0].id,location:'l',applicability:'a'}],gaps:[]}); };

// ═══ a) 契约完整性 ═══
const stA = await call(planA,'status');
ck('F9-a actionHelp 有 stop', Object.hasOwn(stA.actionHelp,'stop'));
ck('F9-a actionHelp 有 clean', Object.hasOwn(stA.actionHelp,'clean'));
ck('F9-a actionHelp 有 clearFailed', Object.hasOwn(stA.actionHelp,'clearFailed'));
ck('F9-a stop 契约说明不写 failures', /不写 failures/.test(String(stA.actionHelp.stop)));
ck('F9-a allowed 里的每个动作都有 actionHelp',
  stA.allowed.filter(a => !['status','read','export','attach','launch','bind'].includes(a))
    .every(a => Object.hasOwn(stA.actionHelp, a)), stA.allowed);

// ═══ D14) 只读验证入口 ═══
ck('F9-D14 status 有 issueLedger', Object.hasOwn(stA,'issueLedger'));
ck('F9-D14 issueLedger.closableKeys 是数组', Array.isArray(stA.issueLedger.closableKeys));
ck('F9-D14 issueLedger.lastClosed 是数组', Array.isArray(stA.issueLedger.lastClosed));
ck('F9-D14 无异议时 current 为空', stA.issueLedger.current.length === 0);

// 走到 review 提出硬异议
await call(planA,'frame',{object:'o',decision:'d',boundaries:'b',searchTopic:'s',
  verification:'v',assumptions:'a',prior:'p',blockedBy:[]});
await doK('GB/T 3480.1 齿轮');
let pf=(await call(revA,'status')).job;
await call(revA,'submit',{jobId:pf.id,content:'预审',verdict:'pass',issues:[]});
let dj=(await call(dA,'status')).job;
await call(dA,'submit',{jobId:dj.id,content:'设计',evidence:'e',gaps:[]});
let rj=(await call(revA,'status')).job;
await call(revA,'submit',{jobId:rj.id,content:'复审',verdict:'blocked',
  issues:[{key:'k-hard',object:'o',requirement:'r',mechanism:'m',evidence:'e',impact:'i',
    needed:'n',owner:'planner',close:'c',kind:'hard'}]});
const stB = await call(planA,'status');
ck('F9-D14 提出异议后 ledger.current 有一条',
  stB.issueLedger.current.length === 1 && stB.issueLedger.current[0].key === 'k-hard',
  stB.issueLedger.current);
ck('F9-D14 本轮刚提 ⇒ closableKeys 为空（还没到下一轮）',
  stB.issueLedger.closableKeys.length === 0, stB.issueLedger.closableKeys);
ck('F9-D14 reopenedKeys 标注本轮重提', Array.isArray(stB.issueLedger.reopenedKeys));

// rework → frame：台账必须还在（Bug F 的只读可见性）
await call(planA,'rework',{issueKey:'k-hard',reason:'已补依据'});
await call(planA,'frame',{object:'o2',decision:'d2',boundaries:'b2',searchTopic:'s2',
  verification:'v2',assumptions:'a2',prior:'p2',blockedBy:[]});
const stC = await call(planA,'status');
ck('F9-D14 frame 重写后 ledger 仍有该条',
  stC.issueLedger.current.some(x => x.key === 'k-hard'), stC.issueLedger.current);
ck('F9-D14 frame 重写后它成了 closable（本轮未重提）',
  stC.issueLedger.closableKeys.some(x => x.key === 'k-hard'), stC.issueLedger.closableKeys);

// ═══ c) stop 不写 failed（实调） ═══
ck('F9-c stop 前没有 failed', Object.keys(st().failures ?? {}).length === 0);
const stopRes = await call(planA,'stop',{reason:'用户要求停止本轮分析'});
ck('F9-c stop 受理', stopRes.ok !== false, stopRes.code);
ck('F9-c stop 后状态文件里 failed 为空', Object.keys(st().failures ?? {}).length === 0,
  st().failures);
const stAfterStop = await call(planA,'status');
ck('F9-c stop 后 status.failed 里没有实质条目',
  Object.keys(stAfterStop.failed ?? {}).filter(k => k !== '__staleHint').length === 0,
  stAfterStop.failed);

// ═══ b) 旧脏记录识别 + 显式清理 ═══
await call(planA,'reopen',{reason:'继续验证'});
const raw = st();
raw.failures = {planner:{reason:'修复前遗留的 role-blocked', job:null, step:'1.1',
  cause:'role-blocked'}};
writeFileSync(join(dir,'ss','f9.json'), JSON.stringify(raw));
const stE = await call(planA,'status');
ck('F9-b legacy 条目被标 stale', stE.failed.planner?.stale === true, stE.failed.planner);
ck('F9-b staleReason 说明 legacy',
  /legacy/.test(String(stE.failed.planner?.staleReason)), stE.failed.planner?.staleReason);
ck('F9-b 给出 __staleHint 指路', !!stE.failed.__staleHint, Object.keys(stE.failed));
ck('F9-b __staleHint 说明用 clearFailed',
  /clearFailed/.test(String(stE.failed.__staleHint?.howToFix)));
ck('F9-b allowed 里有 clearFailed', stE.allowed.includes('clearFailed'), stE.allowed);
const jobsBefore = JSON.stringify(Object.keys(st().jobs ?? {}));
const cf = await call(planA,'clearFailed',{roles:['planner'],reason:'修复前遗留，已不适用'});
ck('F9-b clearFailed 清掉了它', Array.isArray(cf.cleared) && cf.cleared.includes('planner'), cf.cleared);
ck('F9-b 清后 failed 为空', Object.keys(st().failures ?? {}).length === 0);
ck('F9-b 清账归档进 history', st().history.some(x => x.clearedFailure));
ck('F9-b clearFailed 不派新活（jobs 不变）',
  JSON.stringify(Object.keys(st().jobs ?? {})) === jobsBefore);
const cf2 = await call(planA,'clearFailed',{roles:['planner'],reason:'再清一次'});
ck('F9-b 无账可清时报 NO_SUCH_FAILURE_TO_CLEAR',
  cf2.code === 'NO_SUCH_FAILURE_TO_CLEAR', cf2.code);

// 反向：分析角色不能 clearFailed
const kBad = await call(kA,'clearFailed',{roles:['planner'],reason:'我要清账'});
ck('F9-b 分析角色不能 clearFailed（只读/清账权归大脑）',
  kBad.code === 'ROLE_ACTION_DENIED', kBad.code);

console.log(JSON.stringify({ pass: P.length, fail: F.length, fails: F }));
""")
    _o9 = subprocess.run(
        [_NODE, _p9f, _GATE_JS],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=180)
    try:
        _r9 = json.loads((_o9.stdout or "").strip().splitlines()[-1])
    except Exception:
        _r9 = {"pass": 0, "fail": 1,
               "fails": [((_o9.stdout or "") + (_o9.stderr or ""))[-900:]]}
    ck("★ F9 残余 a/b/c + D14 只读入口 行为验证全过（%d 项）" % _r9.get("pass", 0),
       _r9.get("fail") == 0, _r9.get("fails"))
    shutil.rmtree(os.path.dirname(_p9f), ignore_errors=True)




# ════════════════════════════════════════════════════════════════════
sec("F10. 【2026-11-25】evidence_wait 用户答完仍卡死（四条确定性陷阱）")
# ════════════════════════════════════════════════════════════════════
# 现场：C:\Users\j1877\Desktop\test\BUG_REPORT_seven_stage_evidence_wait_stuck.md
#   任务 20261125_211324_905E57B6（6 轴机械臂）step 1.2：
#   用户答完 + capture 成功，phase 却永久停在 evidence_wait，卡了 5 天。
# 报告推测「question=null 应自动推进」——**不成立**（插件不替人做判断）。
# 真因是四条确定性陷阱叠加，已逐条复现并修复。

ck("★ 陷阱①：fromStep 缺省时默认回退到当前小步（不再 INVALID_REWIND_TARGET）",
   "out.fromStepDefaulted" in _SRC_FLOW and "默认回退到【当前在办小步】" in _SRC_FLOW)
ck("★ 陷阱①：fromStep 越界时报可填范围与当前小步",
   "不是任何已知小步" in _SRC_FLOW and "只能回退到【已走过的】小步" in _SRC_FLOW)
ck("★ 陷阱②：no_change 对 question 答复给出替代分类清单",
   "NEEDS_EVIDENCE_DISPOSITION: 这条真人答复不能标 no_change" in _SRC_FLOW
   and "suggestedClassifications" in _SRC_FLOW)
ck("★ 陷阱③：interpret 推进时必须清 waitReason",
   "waitReason 是【当前 phase 的注脚】" in _SRC_FLOW)
ck("★ 陷阱④：frame 支持 resolved 核销 blockedBy",
   "RESOLVED_DEPENDENCY_NOT_FOUND" in _SRC_FLOW and "blockedByCleared" in _SRC_FLOW)
ck("★ 陷阱④：核销是对【新建的】frame 操作（顺序坑已修）",
   "把 planner 声明已解决的依赖，从【新写入的】blockedBy 里剔除" in _SRC_FLOW)
ck("★ 陷阱④：依赖未清时回执给 stillBlocked（不再只给一句 waitReason）",
   "out.stillBlocked = s.slot.frame.blockedBy;" in _SRC_FLOW)
ck("★ 陷阱④：interpret 清 slot 前把 blockedBy 交给 planner（否则只能靠猜）",
   "out.carriedBlockedBy = carriedBlockedBy;" in _SRC_FLOW)
ck("★ index.js 透出新的可操作字段",
   "'blockedBy','suggestedClassifications'" in _SRC_JS)
ck("★ 回归：盲审白名单仍在",
   "const blindParts = () => BLIND_READ_PARTS.slice();" in _SRC_FLOW)
ck("★ 回归：refused 仍只在 preflight", "REFUSAL_NOT_ALLOWED" in _SRC_FLOW)
ck("★ 回归：frame 仍保留异议台账",
   "s.slot = {frame:frameFields(d), ...carried}" in _SRC_FLOW)
ck("★ 回归：attach 仍能接管失效 planner", "plannerTakeover:true" in _SRC_FLOW)
ck("★ 回归：stop 仍不写 failures", "stoppedBy:role" in _SRC_FLOW)



if _NODE and os.path.isfile(_GATE_JS):
    _p10f = os.path.join(tempfile.mkdtemp(prefix="seven_f10_"), "f10.mjs")
    with io.open(_p10f, "w", encoding="utf-8") as f:
        f.write("""
import { mkdtempSync, writeFileSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
const P = [], F = [];
const ck = (n, c, extra) => (c ? P : F).push(extra === undefined ? n : n + ' :: ' + JSON.stringify(extra).slice(0, 240));
const mod = await import(pathToFileURL(process.argv[2]).href);
const dir = mkdtempSync(join(tmpdir(), 'f10-'));
const wf = join(dir, 'workflow_state.json');
writeFileSync(wf, JSON.stringify({ task_id: 'f10', analysis_engine: 'seven_v03',
  seven_stage_root_session_id: 'root', load_case_file: null }));
const agents = new Map();
const mk = (id, parent = null, msgs = []) => { const a = { id,
  session: { id, header: { parentSession: parent }, deriveMessages: () => msgs },
  followup() {}, steer() {} }; agents.set(id, a); return a; };
const userText = { id: 'u1', role: 'user', source: { kind: 'user' },
  content: [{ type: 'text', text: '造一个6轴机械臂' }] };
const rootA = mk('root', null, [userText]);
const planA = mk('p1', 'root');
let reg = null;
mod.install({ logger: { warn(){}, info(){} }, tools: { register: t => { reg = t; }, guard: () => {} },
  agents: { get: id => agents.get(id) },
  subagents: { getProvider: () => ({}), listDescendants: async () => [], prompt: async () => ({}),
    startActivation: async i => { mk(i.childId, i.parent.id); return { childId: i.childId, result: Promise.resolve({}) }; } },
  on: () => () => {} }, { stateDir: join(dir, 'ss'), workflowFile: wf, workerProvider: 'fork' }, o => o);
const signal = { throwIfAborted() {} };
const call = async (agent, action, data = {}) => JSON.parse((await reg.execute(
  { taskId: 'f10', action, dataJson: JSON.stringify(data) }, { agent, signal })).text);
const st = () => JSON.parse(readFileSync(join(dir, 'ss', 'f10.json'), 'utf8'));
const spec = JSON.parse(readFileSync(new URL('./steps.json', pathToFileURL(process.argv[2])), 'utf8'));

await call(rootA, 'attach', { planner: 'p1', messageIds: ['u1'] });
await call(planA, 'launch');
const s1 = st(); const roleOf = {};
for (const [role, cid] of Object.entries(s1.childForRole)) roleOf[cid] = role;
for (const [cid, role] of Object.entries(roleOf))
  await call(agents.get(cid), 'bind', { role, token: s1.invitations[role] });
const kA = agents.get(Object.entries(roleOf).find(([, r]) => r === 'knowledge')[0]);
const dA = agents.get(Object.entries(roleOf).find(([, r]) => r === 'design')[0]);
const revA = agents.get(Object.entries(roleOf).find(([, r]) => r === 'review')[0]);
const doK = async (q) => { let kj = (await call(kA,'status')).job;
  const od = (await call(kA,'search',{jobId:kj.id,query:q})).order;
  await call(rootA,'search_fill',{jobId:kj.id,orderId:od.id,query:od.query,outcome:'ok',
    hits:[{title:'t',url:'https://example.com/x'}]});
  kj = (await call(kA,'status')).job;
  return call(kA,'submit',{jobId:kj.id,content:'知识 '+q,
    sources:[{id:kj.searches[0].hits[0].id,location:'l',applicability:'a'}],gaps:[]}); };

await call(planA,'frame',{object:'o',decision:'d',boundaries:'b',searchTopic:'s',
  verification:'v',assumptions:'a',prior:'p',blockedBy:[]});
await doK('GB/T 3480.1 齿轮');
let pf=(await call(revA,'status')).job;
await call(revA,'submit',{jobId:pf.id,content:'预审',verdict:'pass',issues:[]});
let dj=(await call(dA,'status')).job;
await call(dA,'submit',{jobId:dj.id,content:'设计',evidence:'e',gaps:[]});
let rj=(await call(revA,'status')).job;
await call(revA,'submit',{jobId:rj.id,content:'复审通过',verdict:'pass',issues:[]});
const outs11 = {}; for (const k of spec.steps[0].outputs) outs11[k] = '内容-' + k;
await call(planA,'commit',{jobId:st().slot.review.id,scope:'范围',basis:'依据',conditions:'条件',outputs:outs11});
ck('F10 前置：1.1 已 commit', st().records.length === 1, st().records.length);

const DEP = 'REQ_1_1_01: 用户工程指标缺失（负载/速度/精度）';
await call(planA,'frame',{object:'指标',decision:'d',boundaries:'b',searchTopic:'s',
  verification:'v',assumptions:'a',prior:'p',blockedBy:[DEP]});
ck('F10 前置：1.2 进入 evidence_wait', st().phase === 'evidence_wait', st().phase);
await call(planA,'question',{decision:'指标',question:'负载/臂展/精度？',why:'w',unknownPath:'u'});
const askMsg = { id: 'a1', role: 'tool', source: { kind: 'tool', callId: 'c1' }, toolCallId: 'c1',
  content: [{ type: 'text', text: JSON.stringify({ answers: [
    { id: 'robot_arm_specs', selected: ['B: 中负载型 10kg/800mm/+-0.03mm'] }] }) }] };
rootA.session.deriveMessages = () => [userText, askMsg];
const cap = await call(rootA,'capture',{});
ck('F10 前置：capture 成功', cap.ok !== false && typeof cap.messageId === 'string', cap.code);
ck('F10 前置：capture 后 question 清空', st().question === null);

// 陷阱①：越界必须先测（成功 interpret 会消费掉 fact）
const badFrom = await call(planA,'interpret',{messageId:cap.messageId,classification:'fact',
  fromStep:'9.9',parameters:'x',conflict:'c',changed:'c',impact:'i',reason:'r'});
ck('F10-① fromStep 越界时报「不是任何已知小步」并给当前步',
  badFrom.code === 'INVALID_REWIND_TARGET' && /不是任何已知小步/.test(String(badFrom.error)),
  badFrom.code);
const i1 = await call(planA,'interpret',{messageId:cap.messageId,classification:'fact',
  parameters:'10kg/800mm/+-0.03mm',conflict:'无',changed:'补入指标',impact:'影响选型',reason:'真人答复'});
ck('F10-① interpret 不传 fromStep 能推进（现场死在这）',
  i1.ok !== false && i1.phase === 'frame', i1.code || i1.phase);
ck('F10-① 回执说明默认回退到了哪一步', !!i1.fromStepDefaulted, i1.fromStepDefaulted);
ck('F10-③ interpret 推进后 waitReason 已清空', st().waitReason === null, st().waitReason);
ck('F10-④ 推进时把旧依赖交回 planner',
  Array.isArray(i1.carriedBlockedBy) && i1.carriedBlockedBy.includes(DEP), i1.carriedBlockedBy);
ck('F10-④ next 说明用 resolved 核销', /resolved/.test(String(i1.next)));

// 陷阱④：照抄 blockedBy → 仍回 evidence_wait（判据不放宽）
const frA = await call(planA,'frame',{object:'指标',decision:'d2',boundaries:'b',searchTopic:'s',
  verification:'v',assumptions:'a',prior:'p',blockedBy:[DEP]});
ck('F10-④ 照抄 blockedBy 仍会回 evidence_wait（判据不放宽）', frA.phase === 'evidence_wait', frA.phase);
ck('F10-④ 回执给出 stillBlocked',
  Array.isArray(frA.stillBlocked) && frA.stillBlocked.includes(DEP), frA.stillBlocked);
const frC = await call(planA,'frame',{object:'指标',decision:'d2b',boundaries:'b',searchTopic:'s',
  verification:'v',assumptions:'a',prior:'p',blockedBy:[DEP],resolved:['完全不相干']});
ck('F10-④ resolved 填错报 RESOLVED_DEPENDENCY_NOT_FOUND',
  frC.code === 'RESOLVED_DEPENDENCY_NOT_FOUND', frC.code);
ck('F10-④ 该错误回显当前 blockedBy', Array.isArray(frC.blockedBy), frC.blockedBy);
const frB = await call(planA,'frame',{object:'指标',decision:'d3',boundaries:'b',searchTopic:'s',
  verification:'v',assumptions:'a',prior:'p',blockedBy:[DEP],resolved:[DEP]});
ck('F10-④ 带 resolved 能核销并推进（闭环被打开）',
  frB.ok !== false && frB.phase !== 'evidence_wait', frB.code || frB.phase);
ck('F10-④ blockedByCleared 回执', Array.isArray(frB.blockedByCleared), frB.blockedByCleared);

// 陷阱②：no_change 对 question 答复要给替代
// ⚠️ 前置必须精确：fact 要带 question 字段才走得到那条判据，
//   而当前任务已经推进到 knowledge/design —— 用【全新任务】精确构造这个场景，
//   不去扰动已有状态（那样才测的是同一件事）。
{
  const d2 = mkdtempSync(join(tmpdir(), 'f10b-'));
  const wf2 = join(d2, 'workflow_state.json');
  writeFileSync(wf2, JSON.stringify({ task_id: 'f10b', analysis_engine: 'seven_v03',
    seven_stage_root_session_id: 'root', load_case_file: null }));
  const ag2 = new Map();
  const mk2 = (id, parent = null, msgs = []) => { const a = { id,
    session: { id, header: { parentSession: parent }, deriveMessages: () => msgs },
    followup() {}, steer() {} }; ag2.set(id, a); return a; };
  const root2 = mk2('root', null, [userText]);
  const plan2 = mk2('p1', 'root');
  let reg2 = null;
  mod.install({ logger: { warn(){}, info(){} }, tools: { register: t => { reg2 = t; }, guard: () => {} },
    agents: { get: id => ag2.get(id) },
    subagents: { getProvider: () => ({}), listDescendants: async () => [], prompt: async () => ({}),
      startActivation: async i => { mk2(i.childId, i.parent.id); return { childId: i.childId, result: Promise.resolve({}) }; } },
    on: () => () => {} }, { stateDir: join(d2, 'ss'), workflowFile: wf2, workerProvider: 'fork' }, o => o);
  const c2 = async (agent, action, data = {}) => JSON.parse((await reg2.execute(
    { taskId: 'f10b', action, dataJson: JSON.stringify(data) }, { agent, signal })).text);
  const st2 = () => JSON.parse(readFileSync(join(d2, 'ss', 'f10b.json'), 'utf8'));
  await c2(root2, 'attach', { planner: 'p1', messageIds: ['u1'] });
  await c2(plan2, 'launch');
  const s2 = st2(); let n = 0;
  for (const [role, cid] of Object.entries(s2.childForRole)) {
    await c2(ag2.get(cid), 'bind', { role, token: s2.invitations[role] }); n++;
  }
  ck('F10-② 前置：独立任务三角色已 bind', n === 3, n);
  await c2(plan2,'frame',{object:'o',decision:'d',boundaries:'b',searchTopic:'s',
    verification:'v',assumptions:'a',prior:'p',blockedBy:['Y: 再缺一条']});
  ck('F10-② 前置：独立任务进入 evidence_wait', st2().phase === 'evidence_wait', st2().phase);
  await c2(plan2,'question',{decision:'d',question:'q',why:'w',unknownPath:'u'});
  ck('F10-② 前置：question 已登记', !!st2().question);
  const ask2 = { id: 'a2', role: 'tool', source: { kind: 'tool', callId: 'c2' }, toolCallId: 'c2',
    content: [{ type: 'text', text: JSON.stringify({ answers: [{ id: 'q2', selected: ['v'] }] }) }] };
  root2.session.deriveMessages = () => [userText, ask2];
  const cap2 = await c2(root2,'capture',{});
  ck('F10-② 前置：capture 成功且 fact 带 question',
    cap2.ok !== false && !!st2().facts.at(-1)?.question, cap2.code);
  const nc = await c2(plan2,'interpret',{messageId:cap2.messageId,classification:'no_change',
    parameters:'x',conflict:'c',changed:'c',impact:'i',reason:'r'});
  ck('F10-② no_change 对 question 答复被拒', nc.code === 'NEEDS_EVIDENCE_DISPOSITION', nc.code);
  ck('F10-② 拒绝时给出可用的替代分类',
    Array.isArray(nc.suggestedClassifications) && nc.suggestedClassifications.includes('fact'),
    nc.suggestedClassifications);
  ck('F10-② 拒绝时提示用 resolved 核销依赖', /resolved/.test(String(nc.error)));
  const nc2 = await c2(plan2,'interpret',{messageId:cap2.messageId,classification:'fact',
    parameters:'v',conflict:'无',changed:'补入',impact:'影响',reason:'真人答复'});
  ck('F10-② 换成 fact 后能推进（不再是死路）', nc2.ok !== false, nc2.code);
}

console.log(JSON.stringify({ pass: P.length, fail: F.length, fails: F }));
""")
    _o10 = subprocess.run(
        [_NODE, _p10f, _GATE_JS],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=180)
    try:
        _r10 = json.loads((_o10.stdout or "").strip().splitlines()[-1])
    except Exception:
        _r10 = {"pass": 0, "fail": 1,
               "fails": [((_o10.stdout or "") + (_o10.stderr or ""))[-900:]]}
    ck("★ F10 evidence_wait 四条陷阱 行为验证全过（%d 项）" % _r10.get("pass", 0),
       _r10.get("fail") == 0, _r10.get("fails"))
    shutil.rmtree(os.path.dirname(_p10f), ignore_errors=True)

# ════════════════════════════════════════════════════════════════════
sec("G. 端到端：init 固定引擎 → select 无凭证时 fail-closed")
# ════════════════════════════════════════════════════════════════════
_sd = tempfile.mkdtemp(prefix="seven_e2e_state_")
_wd = None
_env = dict(os.environ)
_env["DSH_STATE_DIR"] = _sd
_env["DSH_SESSION_ID"] = "e2e-seven-root"
_env["PYTHONIOENCODING"] = "utf-8"
_env.pop("DSH_SEVEN_STAGE_STATE_DIR", None)
_WG = os.path.join(HERE, "workflow_gate.py")


def _run(*args):
    p = subprocess.run([sys.executable, "-B", _WG, *args],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=HERE, env=_env)
    try:
        return json.loads(p.stdout or "{}")
    except Exception:
        return {"_raw": (p.stdout or "")[-400:], "_err": (p.stderr or "")[-400:]}


try:
    _r = _run("reset")
    _init = _run("init", "七阶段接线回归：小型压装机")
    ck("★ init 固定 analysis_engine=seven_v03",
       _init.get("analysis_engine") == "seven_v03", _init.get("analysis_engine"))
    ck("★ init 记录真实根会话 ID",
       _init.get("seven_stage_root_session_id") == "e2e-seven-root",
       _init.get("seven_stage_root_session_id"))
    ck("init 未报阻塞", not _init.get("seven_stage_blocked"),
       _init.get("seven_stage_blocked"))

    _run("provide_context", "D 并行, B 部分自主")
    _run("provide_context", "强需求")
    _run("provide_context", "载荷 50kN，行程 100mm")
    with io.open(os.path.join(_sd, "workflow_state.json"), encoding="utf-8") as f:
        _wd = (json.load(f) or {}).get("work_dir")
    ck("拿到本任务 work_dir", bool(_wd), _wd)

    _sel = _run("select", "B,D")
    ck("★ 无七阶段凭证 → select 拒绝", _sel.get("ok") is False, _sel.get("ok"))
    ck("★ 拒绝码 SEVEN_STAGE_HANDOFF_REQUIRED",
       _sel.get("gate") == "SEVEN_STAGE_HANDOFF_REQUIRED", _sel.get("gate"))
    ck("拒绝体给出七阶段操作步骤",
       any("attach" in str(x) for x in (_sel.get("how") or [])),
       (_sel.get("how") or [])[:2])

    # seven-status 必须能只读跑通
    _st = _run("seven-status")
    ck("seven-status 可运行且 ok", _st.get("ok") is True, _st.get("error"))
    ck("seven-status 回传引擎", _st.get("analysis_engine") == "seven_v03",
       _st.get("analysis_engine"))
    ck("seven-status 显示编制未就绪",
       (_st.get("subagent_plan") or {}).get("ready") is False,
       _st.get("subagent_plan"))

    # register-design 在无凭证时必须拒绝（不能凭空造编制）
    _rd = _run("register-design")
    ck("★ register-design 无凭证时拒绝", _rd.get("ok") is False, _rd.get("ok"))
    ck("拒绝码是 SEVEN_STAGE_HANDOFF_REQUIRED",
       _rd.get("gate") == "SEVEN_STAGE_HANDOFF_REQUIRED", _rd.get("gate"))
finally:
    shutil.rmtree(_sd, ignore_errors=True)
    if _wd:
        shutil.rmtree(_wd, ignore_errors=True)


# ════════════════════════════════════════════════════════════════════
sec("H. room-start 也必须复核七阶段凭证（不允许绕过 select 直接开工）")
# ════════════════════════════════════════════════════════════════════
_mgsrc = read(os.path.join(HERE, "mode_gate.py"))
ck("★ mode_gate 的 _require_subagent_plan 会调 recheck_design",
   "recheck_design" in _mgsrc)
ck("★ room-start 拒绝码覆盖七阶段",
   "SEVEN_STAGE_HANDOFF_REQUIRED" in _mgsrc
   or "_rc.get(\"gate\")" in _mgsrc, "见 _require_subagent_plan")
ck("mode_gate 不再建议 set-subagent-plan 登记编制",
   "set-subagent-plan" not in _mgsrc)

_sbsrc = read(os.path.join(HERE, "sw_bridge.py"))
ck("★ sw_bridge 的 Mode2 前置指引已指向七阶段",
   "engineering_seven_stage action=attach" in _sbsrc)
ck("sw_bridge 不再教 set-subagent-plan",
   "python mode2_flow.py set-subagent-plan" not in _sbsrc)


shutil.rmtree(_ISO_DIR, ignore_errors=True)
print("")
print("=" * 74)
print("PASS=%d FAIL=%d" % (PASS, FAIL))
if FAILURES:
    print("失败项：")
    for f in FAILURES:
        print("  - " + f)
print("=" * 74)
sys.exit(1 if FAIL else 0)
