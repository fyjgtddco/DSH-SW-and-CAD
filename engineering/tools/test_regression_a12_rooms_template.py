# -*- coding: utf-8 -*-
"""【A12 回归测试】任务书 rooms 示例缺 type → 子代理照抄必失败

现场（d921be74:475）：
    子代理照抄任务书里的 rooms 示例后调 set-subagent-plan，被拒：
        "子代理【X】缺少 type 字段"
    —— 校验器强制每个 room 有 type，而提示词里流传的示例只有
    {name, parts, why, depends_on}，示例与校验器不同源，模型只能反复试错。

修复：新增子命令 rooms-template（输出含 type 的完整示例，type 取值直接从
    CANONICAL_ROOM_TYPES 反查），并让缺 type 的报错直接指向该子命令。

本测试锁定：
  A. 模板本身是合法的（每个房间都有 type，且 type 落在规范类型表内）；
  B. 模板的类型口径与校验器【同源】（allowed_types == CANONICAL_ROOM_TYPES）；
  C. 模板可直接投喂 set-subagent-plan（不需要手工补 type，不产生多余自动补齐）；
  D. 缺 type 的报错里必须出现 rooms-template 指引；
  E. --out 写出的文件同样可直接 --rooms-json @file 使用。

运行：python test_regression_a12_rooms_template.py
退出码 0 = 通过
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile

# ── 必须隔离状态目录，否则会读到宿主实时写入的子代理注册表 ──────────────
_ISO_DIR = tempfile.mkdtemp(prefix="iso_a12_")
os.environ["DSH_STATE_DIR"] = _ISO_DIR
os.environ["DSH_SESSION_ID"] = "test-analyst-a12"
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mode2_flow as mf  # noqa: E402

P = F = 0
FAILS = []


def ck(label, cond, extra=""):
    global P, F
    if cond:
        P += 1
        print("  [PASS] " + label)
    else:
        F += 1
        FAILS.append(label)
        print("  [FAIL] " + label + ("  " + str(extra)[:400] if extra else ""))


def sec(t):
    print("")
    print("=" * 74)
    print(t)
    print("=" * 74)


def fresh():
    d = tempfile.mkdtemp(prefix="m2a12_")
    try:
        mf.analyst_bind("test-analyst-a12", d)
    except Exception:
        pass
    return d


def prime_all_stages(d):
    """把十阶段按序走完（set_subagent_plan 的前置门禁）。"""
    mf.set_depth("lite", d)
    for sid in mf.STAGE_IDS:
        assert mf.begin_stage(sid, d)["ok"], sid
        q = mf.record_question("题目-%s" % sid, sid, "需要该参数", d)
        assert q["ok"], q
        assert mf.answer_question(q["question"]["seq"], "答复-%s" % sid, d)["ok"]
        assert mf.mark_stage_done(sid, "产物-%s" % sid, d)["ok"], sid


# ════════════════════════════════════════════════════════════════════
sec("A. rooms-template 模板本身合法（每个房间都带 type）")
# ════════════════════════════════════════════════════════════════════
tpl = mf.rooms_template()
ck("rooms-template 返回 ok", tpl.get("ok") is True, tpl)
_rooms = tpl.get("rooms") or []
ck("模板至少 3 个房间", len(_rooms) >= 3, len(_rooms))
ck("★ 每个房间都有非空 type",
   all(str(r.get("type") or "").strip() for r in _rooms),
   [r.get("name") for r in _rooms if not str(r.get("type") or "").strip()])
ck("★ 每个 type 都是规范类型（无需再规范化）",
   all(r.get("type") in mf.CANONICAL_ROOM_TYPES for r in _rooms),
   [(r.get("name"), r.get("type")) for r in _rooms
    if r.get("type") not in mf.CANONICAL_ROOM_TYPES])
ck("每个房间都有 name", all(str(r.get("name") or "").strip() for r in _rooms))
ck("每个房间都列出了 parts（非建模屋也得有占位名）",
   all(isinstance(r.get("parts"), list) and r.get("parts") for r in _rooms),
   [(r.get("name"), r.get("parts")) for r in _rooms
    if not (isinstance(r.get("parts"), list) and r.get("parts"))])
ck("每个房间都有 why（可审计的存在理由）",
   all(str(r.get("why") or "").strip() for r in _rooms))
_names = {r.get("name") for r in _rooms}
_dangling = [d for r in _rooms for d in (r.get("depends_on") or []) if d not in _names]
ck("★ 模板里的 depends_on 全部指向模板内真实存在的房间", not _dangling, _dangling)
ck("模板房间名不重复", len(_names) == len(_rooms))
ck("模板字段说明覆盖全部 5 个字段",
   {f["field"] for f in (tpl.get("fields") or [])} ==
   {"name", "type", "parts", "why", "depends_on"}, tpl.get("fields"))


# ════════════════════════════════════════════════════════════════════
sec("B. 类型口径与校验器【同源】（不允许第二份手写类型表）")
# ════════════════════════════════════════════════════════════════════
ck("★ allowed_types == CANONICAL_ROOM_TYPES（同一个来源）",
   tuple(tpl.get("allowed_types") or ()) == tuple(mf.CANONICAL_ROOM_TYPES),
   tpl.get("allowed_types"))
for _tp in ("assembly", "drafting"):
    ck("模板已含收尾房间类型 %s（免得靠自动补齐）" % _tp,
       _tp in [r.get("type") for r in _rooms])
ck("模板给出 usage（怎么用）", bool(tpl.get("usage")))
ck("usage 里含 set-subagent-plan 的完整命令",
   any("set-subagent-plan" in str(x) for x in (tpl.get("usage") or [])))


# ════════════════════════════════════════════════════════════════════
sec("C. 模板可直接投喂 set-subagent-plan（照抄不会再报缺 type）")
# ════════════════════════════════════════════════════════════════════
d = fresh()
try:
    prime_all_stages(d)
    r = mf.set_subagent_plan(tpl["rooms"], "按功能分解得到 5 个模块",
                             "总计子代理", d)
    ck("★ 模板原样登记成功（不再出现'缺少 type 字段'）", r.get("ok") is True,
       r.get("error"))
    ck("模板房间全部落盘（数量一致）",
       (r.get("count") or 0) == len(_rooms), r.get("count"))
    ck("★ 模板已含 assembly/drafting → 不需要自动补齐",
       not (r.get("auto_added_final_rooms") or []),
       r.get("auto_added_final_rooms"))
    ck("没有房间因为类型问题被规范化（模板本身就用规范类型）",
       not (r.get("room_types_normalized") or []),
       r.get("room_types_normalized"))
    _sp = (mf.load_plan(d).get("subagent_plan") or {})
    ck("落盘后的每个房间仍带 type",
       all(x.get("type") for x in (_sp.get("rooms") or [])),
       _sp.get("rooms"))
finally:
    shutil.rmtree(d, ignore_errors=True)


# ════════════════════════════════════════════════════════════════════
sec("D. 缺 type 的报错必须指向 rooms-template（原地可自救）")
# ════════════════════════════════════════════════════════════════════
d2 = fresh()
try:
    prime_all_stages(d2)
    bad = [{"name": "结构件", "parts": ["DSH_某零件"], "why": "承载", "depends_on": []}]
    r = mf.set_subagent_plan(bad, "照抄任务书旧示例", "总计子代理", d2)
    ck("缺 type → 明确拒绝", r.get("ok") is False, r)
    ck("★ 报错文本写明'缺少 type 字段'",
       "缺少 type 字段" in str(r.get("error") or ""), r.get("error"))
    _all_text = json.dumps(r, ensure_ascii=False)
    ck("★ 报错里直接指向 rooms-template 子命令",
       "rooms-template" in _all_text, _all_text[:400])
    ck("报错里同时给了 allowed 白名单", bool(r.get("allowed")), r.get("allowed"))
    ck("hint 里给出'补一个 type'的可执行指引",
       "补一个" in str(r.get("hint") or "") or "白名单" in str(r.get("hint") or ""),
       r.get("hint"))
finally:
    shutil.rmtree(d2, ignore_errors=True)


# ════════════════════════════════════════════════════════════════════
sec("E. CLI 全路径：rooms-template 可构建 + --out 文件可直接复用")
# ════════════════════════════════════════════════════════════════════
_bad = []
try:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        mf.main(["rooms-template", "--help"])
except SystemExit as _e:
    if _e.code not in (0, None):
        _bad.append(_e.code)
except Exception as _e:
    _bad.append(repr(_e))
ck("子命令 rooms-template 的 argparse 可构建（无 NameError）", not _bad, _bad)

d3 = fresh()
try:
    prime_all_stages(d3)
    _out = os.path.join(d3, "rooms.json")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = mf.main(["rooms-template", "--out", _out, "--work-dir", d3])
    ck("CLI rooms-template 返回 0", code == 0, buf.getvalue()[-300:])
    ck("--out 文件已写出", os.path.exists(_out))
    with io.open(_out, encoding="utf-8") as f:
        _saved = json.load(f)
    ck("★ --out 写出的是裸 rooms 数组（可直接 --rooms-json @file）",
       isinstance(_saved, list) and _saved and all(r.get("type") for r in _saved),
       type(_saved).__name__)
    buf2 = io.StringIO()
    with contextlib.redirect_stdout(buf2):
        code2 = mf.main(["set-subagent-plan", "--rooms-json", "@" + _out,
                         "--rationale", "直接复用模板文件", "--work-dir", d3])
    ck("★ 模板文件走 CLI set-subagent-plan 成功", code2 == 0,
       buf2.getvalue()[-400:])
finally:
    shutil.rmtree(d3, ignore_errors=True)

shutil.rmtree(_ISO_DIR, ignore_errors=True)

print("")
print("PASS=%d FAIL=%d" % (P, F))
if F:
    print("失败项: " + ", ".join(FAILS))
    sys.exit(1)
