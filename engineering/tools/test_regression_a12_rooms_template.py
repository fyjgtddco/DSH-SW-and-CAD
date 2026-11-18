# -*- coding: utf-8 -*-
"""【A12 回归测试 · 七阶段版】rooms 示例与校验器必须同源

现场（d921be74:475）：
    子代理照抄任务书里的 rooms 示例后登记编制，被拒：
        "房间【X】缺少 type 字段"
    —— 校验器强制每个 room 有 type，而提示词里流传的示例只有
    {name, parts, why, depends_on}，示例与校验器不同源，模型只能反复试错。

【2026-11-18 · 十阶段 → 七阶段】
    'set-subagent-plan' 已被删除（编制只能来自七阶段 read_design）。
    本测试改为锁定七阶段版的三件事：
      A. rooms-template 本身合法（每个房间都带 type，且 type 落在规范类型表内）；
      B. 类型口径与校验器【同源】（allowed_types == CANONICAL_ROOM_TYPES）；
      C. 同一份房间草案经 _normalize_rooms_from_design 规范化后：
         · 缺 type → 明确拒绝，且报错指向 rooms-template；
         · 含 assembly/drafting → 不产生多余自动补齐；
         · 缺收尾房间 → 自动补齐（这是流程决定的，不靠模型记）。

运行：python test_regression_a12_rooms_template.py
退出码 0 = 通过
"""
import contextlib
import io
import json
import os
import sys

_ISO_DIR = os.path.join(os.environ.get("TEMP", "."), "iso_a12_seven")
os.makedirs(_ISO_DIR, exist_ok=True)
os.environ["DSH_STATE_DIR"] = _ISO_DIR
os.environ["DSH_SESSION_ID"] = "test-root-a12"
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
ck("模板标注了引擎（避免再被当成十阶段产物）",
   tpl.get("engine") == mf.ANALYSIS_ENGINE, tpl.get("engine"))


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
ck("usage 指向七阶段入口 register-design",
   any("register-design" in str(x) for x in (tpl.get("usage") or [])),
   tpl.get("usage"))
ck("★ usage 不再教人调已删除的 set-subagent-plan",
   not any("set-subagent-plan" in str(x) for x in (tpl.get("usage") or [])),
   tpl.get("usage"))


# ════════════════════════════════════════════════════════════════════
sec("C. 同一份草案经 register-design 的规范化器")
# ════════════════════════════════════════════════════════════════════
r = mf._normalize_rooms_from_design(tpl["rooms"], "按功能分解得到 5 个模块")
ck("★ 模板原样通过规范化（不再出现'缺少 type 字段'）", r.get("ok") is True,
   r.get("error"))
ck("房间数一致", (r.get("count") if r.get("count") else len(r.get("rooms") or []))
   == len(_rooms), len(r.get("rooms") or []))
ck("★ 模板已含 assembly/drafting → 不需要自动补齐",
   not (r.get("auto_added_final_rooms") or []),
   r.get("auto_added_final_rooms"))

# 缺 type → 明确拒绝，且报错指向 rooms-template
bad = [{"name": "结构件", "parts": ["DSH_某零件"], "why": "承载", "depends_on": []}]
rb = mf._normalize_rooms_from_design(bad, "照抄任务书旧示例")
ck("缺 type → 明确拒绝", rb.get("ok") is False, rb)
ck("★ 报错文本写明'缺少 type 字段'",
   "缺少 type 字段" in str(rb.get("error") or ""), rb.get("error"))
_all_text = json.dumps(rb, ensure_ascii=False)
ck("★ 报错里直接指向 rooms-template 子命令",
   "rooms-template" in _all_text, _all_text[:400])
ck("报错里同时给了 allowed 白名单", bool(rb.get("allowed")), rb.get("allowed"))

# 缺收尾房间 → 自动补齐（流程决定，不靠模型记）
only_model = [
    {"name": "结构件", "type": "structural", "parts": ["DSH_车架底板"],
     "why": "承载整车", "depends_on": []},
]
ra = mf._normalize_rooms_from_design(only_model, "只登记了建模房间")
ck("缺收尾房间时仍成功", ra.get("ok") is True, ra.get("error"))
_added = {x["type"] for x in (ra.get("auto_added_final_rooms") or [])}
ck("★ 自动补齐 assembly + drafting",
   _added == {"assembly", "drafting"}, _added)
ck("补齐的房间标了 auto_added（可溯源）",
   all(x.get("auto_added") for x in (ra.get("rooms") or [])
       if x.get("type") in ("assembly", "drafting")),
   [x.get("name") for x in (ra.get("rooms") or [])])

# 空草案 → 拒绝
ck("rooms 为空 → 拒绝", mf._normalize_rooms_from_design([], "空").get("ok") is False)
ck("rationale 为空 → 拒绝",
   mf._normalize_rooms_from_design(tpl["rooms"], "").get("ok") is False)


# ════════════════════════════════════════════════════════════════════
sec("D. CLI 全路径：rooms-template 可构建 + --out 文件可直接复用")
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

_out = os.path.join(_ISO_DIR, "rooms.json")
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    code = mf.main(["rooms-template", "--out", _out, "--work-dir", _ISO_DIR])
ck("CLI rooms-template 返回 0", code == 0, buf.getvalue()[-300:])
ck("--out 文件已写出", os.path.exists(_out))
with io.open(_out, encoding="utf-8") as f:
    _saved = json.load(f)
ck("★ --out 写出的是裸 rooms 数组",
   isinstance(_saved, list) and _saved and all(r.get("type") for r in _saved),
   type(_saved).__name__)
ck("写出的文件能再次通过规范化器",
   mf._normalize_rooms_from_design(_saved, "直接复用模板文件").get("ok") is True)

print("")
print("PASS=%d FAIL=%d" % (P, F))
if F:
    print("失败项: " + ", ".join(FAILS))
    sys.exit(1)
