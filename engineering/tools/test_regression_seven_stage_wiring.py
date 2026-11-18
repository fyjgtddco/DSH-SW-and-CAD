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
    ck("install-0.2.0.ps1 校验 steps.json 存在", "lib\\steps.json" in _inst
       or "lib\steps.json" in _inst)
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
