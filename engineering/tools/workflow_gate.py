#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json, os, sys, time

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
if sys.stderr.encoding != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8')

STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "workflow_state.json")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ── 【P0-3 写侧修复】状态目录必须来自【单一事实源】────────────────────────
# 原实现把 workflow_state.json 写到本脚本所在目录。但工程模式有两份副本
#   （工作区 + ~/.dsh 安装目录），从哪份调用就写哪份 —— 于是
#   mode_gate 写安装副本、workflow_gate 写工作区，状态被劈成两半：
#     workflow_state.json：工作区 step=user_selected vs 安装 step=depth_asked
#   宿主守卫读安装副本 → 把"已完成"判成"未完成"，反复 steer 拦住对话结束。
# 现在所有状态/凭据统一由 _store.state_dir() 决定落点。
# STATE_DIR 用于【读写状态】；BASE_DIR 仍表示【脚本所在目录】
#   （用于定位 mode_gate.py / choice_contract.py 等同目录模块）。
try:
    import _store as _store_mod
    STATE_DIR = _store_mod.state_dir()
except Exception:
    STATE_DIR = BASE_DIR
STATE_PATH = os.path.join(STATE_DIR, "workflow_state.json")
if STATE_DIR and os.path.isdir(STATE_DIR) and STATE_DIR not in sys.path:
    sys.path.insert(0, STATE_DIR)
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
MODE_GATE_PATH = os.path.join(BASE_DIR, "mode_gate.py")
# ── 【固定两问】搭建方式(A/B/C) 与 并行策略(D/E) 的代码级强制 ──────────
# 这两问是【两个独立问题】：本模块只核对"各自是否被系统问题问过"，
#   不再使用旧的"分批/单次题数"规则 —— 那种规则把
#   "连着问两次"或"一次问两题"误判成违规，已按用户要求移除。
try:
    import choice_contract as _cc
except Exception:  # 模块缺失时降级为"不强制"，绝不锁死流程
    _cc = None

CHOICES = {
    "A": {"label": "完全自主搭建", "strategy": "parallel", "detail": "full", "desc": "AI自主决策"},
    "B": {"label": "部分自主搭建", "strategy": "hybrid", "detail": "key_points", "desc": "关键节点询问"},
    "C": {"label": "步步确认", "strategy": "sequential", "detail": "every_step", "desc": "每步确认"},
}
PARALLEL_CHOICES = {
    "D": {"label": "部分小屋并行", "mode": "parallel", "desc": "按波次分批启动小屋"},
    "E": {"label": "单小屋串联", "mode": "sequential", "desc": "一次一个房间，完成后开下一个"},
}

# 第一波 = 全部"建模类"房间类型。总装（assembly）必须在它们全部 room-end 之后才能开启。
WAVE1_TYPES = ("structural", "transmission", "housing", "support", "spring", "thermal", "corrosion")

# C模式（步步确认）参数确认协议 —— 强制注入每个建模小屋的 prompt
#
# 【B2修复】明确区分两类 mode_gate.py 调用，消除与"小屋禁止调用 mode_gate.py"铁律的矛盾：
#   ✅ 允许（进度上报通道，只写心跳/报告文件，不碰锁与房间状态机）：
#        room-report / room-heartbeat / room-report-read
#   ❌ 禁止（编排命令，会夺舍主流程）：
#        room-start / room-end / room-fail / sw-request / sw-wait / sw-release / declare / check
CONFIRMATION_PROTOCOL_C = [
    "=== C模式·步步确认协议（每个零件独立走一遍完整循环，不得合并或跳过） ===",
    "",
    "【本小屋允许调用的 mode_gate.py 命令（仅进度上报通道）】",
    "  ✅ 允许: room-report（进度上报）、room-heartbeat（心跳保活）",
    "  ❌ 禁止: room-start / room-end / room-fail / sw-request / sw-wait / sw-release / declare / check",
    "     以上编排命令由大屋（主对话）独占，小屋调用=夺舍，主流程会被劫持。",
    "  说明: room-report / room-heartbeat 只写入 reports/ 与 heartbeats/ 文件，",
    "        不修改 mode_state.json 的房间与锁状态，因此不属于编排命令，可安全调用。",
    "",
    "【每个零件的确认循环 —— 对房间内的每一个零件重复执行以下步骤，直到所有零件都走完】",
    "第零步（自查身份 · 必须先做，否则后续步骤全废）：",
    "  ⚠️ 小屋开工第一件事：调 mode_gate.py whoami <房间名> 拿到自己的 subagent_id。",
    "     【问题3 修复】该命令支持自报身份 + 回写登记，彻底消除时序竞态。",
    "     首选（最可靠 · 自报自己的 sessionId，命令会立即登记并返回）：",
    "       python \"<工程模式根目录>\\tools\\mode_gate.py\" whoami \"<房间名>\" --session-id \"<你的sessionId>\"",
    "     次选（环境变量）：设 DSH_SUBAGENT_SESSION_ID=<你的sessionId> 后再调 whoami \"<房间名>\"",
    "     兜底（等待主对话登记，轮询最多 30 秒）：",
    "       python \"<工程模式根目录>\\tools\\mode_gate.py\" whoami \"<房间名>\" --wait 30",
    "     返回的 subagent_id 就是后面 ask_user.py 的 --child 参数。",
    "     ⚠️ 注意：你自己的 sessionId 通常由 DSH 平台在创建你时给出（见你的运行上下文 /",
    "        prompt 中的 session 标识）。若确实无从获取，用 --wait 30 等主对话写入",
    "        subagent-assign 后再取；仍失败则上报 report failed 并说明原因，",
    "        禁止在不知道 sessionId 的情况下盲目往下做（v1 小屋正是死在这一步）。",
    "",
    "第一步（输出参数表 · 必须有量化设计依据）：",
    "  输出结构化【零件参数表】，字段与要求如下（缺任一项视为不合格）：",
    "    · 零件名",
    "    · 材料（含牌号，如 Q235 / 45# / 6061-T6）",
    "    · 关键尺寸（长×宽×厚 / 直径 / 孔径，全部带 mm 单位）",
    "    · 配合公差：【必须写标准配合代号】，如 H7/g6、H8/f7、k6；",
    "      严禁写「0.1mm(ISO 286-1)」这类没有配合性质的模糊说法。",
    "      依据 GB/T 1800；自由尺寸明确写「未注公差按 GB/T 1804-m」。",
    "    · 【设计依据 · 必须量化】至少包含：",
    "        a) 载荷来源与数值（引用门禁力学估算的实际值，如 750N / 300N）",
    "        b) 危险截面与校核公式（如悬臂弯矩 M=F·L，截面模量 W，σ=M/W）",
    "        c) 计算结果与许用值对比（如 σ=9.7MPa ≤ [σ]=94MPa，安全系数 n=9.7）",
    "        d) 尺寸与载荷的匹配性自检 ——",
    "           反例（真实发生）：门禁估算等效负载 750N、臂长约 300mm，",
    "           小屋却给出 长100×宽30×厚5mm 的大臂 —— 臂长与估算严重不符、",
    "           厚度过薄，属设计依据薄弱，必须重做参数。",
    "      ⚠️ 若尺寸与门禁给出的臂长/负载不匹配，必须在参数表中显式说明理由，",
    "         或修正尺寸后再提交确认。禁止不经校核直接给「看起来差不多」的尺寸。",
    "第二步（用户确认 · 必须交由主代理代问，小屋自己问不到用户）：",
    "  ⚠️【平台硬限制】子代理无权向用户提问。DSH 的 userQuestions.ask() 会校验",
    "     agents.roots().includes(agent)，而子代理不在 roots 中 → 必然抛",
    "     DELEGATED_CALLER。所以【严禁】小屋直接调 ask_user_question，调了必失败。",
    "",
    "  ✅ 正确做法：用【ask_user.py】直接在子代理面板提问（阻塞等待，不结束回合）",
    "     命令（任意工作目录都可，用绝对路径）：",
    "       python \"<工程模式根目录>\\tools\\ask_user.py\" --room \"<房间名>\" --child \"<本子代理sessionId>\" ",
    "              --header \"零件确认\" --question \"以下参数将用于建模<零件名>，要用吗？\" ",
    "              --option \"确认，开始建模\" --option \"需要修改\"",
    "     （复杂问题建议写 questions.json 再传 --spec questions.json，避免转义问题）",
    "     该脚本会：",
    "       1) 把问题登记到本子代理名下 → 选项卡片出现在【右侧子代理面板】",
    "       2) 【原地阻塞轮询】，直到用户在面板里点选并提交",
    "       3) 拿到答案后以 JSON 打印到 stdout 并退出",
    "     小屋读到 stdout 的答案即为『用户已确认』，据此继续建模 —— 全程不结束回合。",
    "",
    "  说明（为何要经过这个脚本）：DSH 平台限制子代理不能直接调 ask_user_question",
    "  （会抛 DELEGATED_CALLER）。ask_user.py 由宿主插件代为发起原生提问，",
    "  再把答案交回本小屋，等价于一次普通工具调用的返回值。",
    "",
    "  🚫【严禁自导自演】小屋（以及任何模型）不得在自己的输出里编造：",
    "     『【用户回答】』『选择：确认』『用户已回复继续』等「用户已经作答」的内容。",
    "     模型只负责输出【问题 + 选项】；用户的选择必须由前端真实捕获并注入。",
    "     自行编造用户答复 = 严重违规 = 该零件作废重做，并记为幻觉错误。",
    "",
    "  🚫【严禁越权确认 · Bug1 已由代码强制拦截】",
    "     绝不允许『沿用上一个零件的确认』——每个零件必须单独向用户提问、单独得到答复。",
    "     反面案例（真实发生）：大臂确认后，小屋对『小臂』直接上报",
    "       room-report <房间> params_confirmed '小臂 已确认(沿用大臂已确认基础)'",
    "     并且 params_shown → params_confirmed 间隔仅 0.05 秒（远短于人工确认）——",
    "     这属于代用户签字，零件即使建成也【程序上无效，必须作废重做】。",
    "     代码已强制：params_confirmed 需要用户授权令牌（由主对话在用户答复后调用",
    "       mode_gate.py confirm-part <房间名> <零件名>",
    "     写入）。未授权的 params_confirmed 会被拒绝并记入 violations.json。",
    "     ✅ 正确节奏：小臂 → 弹卡片 → 等用户答复 → 收到答复 → 上报 params_confirmed。",
    "        然后再处理下一个零件，绝不允许批量或沿用。",
    "",
    "  等待期间小屋调 mode_gate.py room-report <房间名> params_shown '<零件名> 待确认' 并保持心跳；",
    "  未收到主对话转达的确认前，禁止调用 sw_bridge 建模该零件。",
    "第三步（建模执行）：",
    "  用户确认后，用 sw_bridge.py 建模该零件；完成后调 mode_gate.py room-report <房间名> part_done '<零件名> 已建模完毕'",
    "",
    "【用户要求修改时的分支】",
    "  用户提出修改意见 → 修改参数后重新回到第二步（必须再问一次用户确认），禁止用户没说 OK 就直接改完建模",
    "",
    "【违规判定】",
    "  - 未等用户确认就调 sw_bridge = 该零件作废，重做该零件全程",
    "  - 把多个零件的确认合并成一次问 = 违规，拆回每个零件单独问",
    "  - 跳过一个零件直接建模 = 违规，补做完所有被跳过零件的确认循环",
    "",
    "【进度旁路上报（与确认循环并行，不阻断流程）】",
    "  ⚠️【问题7 修复 · 第一次上报是硬性要求】",
    "     小屋开工后【第一件事】必须上报 registered，否则主对话侧永远看到",
    "     「report=null」，会误判小屋从未启动，进而触发无谓的 recover/重启。",
    "     命令：python \"<工程模式根目录>\\tools\\mode_gate.py\" room-report \"<房间名>\" registered \"小屋已启动, 开始处理 <零件列表>\"",
    "     之后每个节点都必须上报，顺序如下：",
    "  - registered      ：小屋启动（必做，且必须最先做）",
    "  - params_shown    ：已展示参数表等用户确认",
    "  - params_confirmed：用户已确认，开始建模（需先有 confirm-part 授权）",
    "  - modeling        ：建模脚本执行中",
    "  - part_done       ：单零件完成",
    "  - validating      ：FEA/CADX 验证中",
    "  - done            ：本房间全部零件完成",
    "  - failed          ：失败，<原因>（必须写清卡在哪一步）",
    "",
    "  ⚠️【问题9 修复 · 卡住时必须先上报再退出】",
    "     任何异常（拿不到 sessionId / 建模脚本报错 / SW 调用失败 / 答复交接异常）",
    "     都必须【先 room-report <房间名> failed '<具体原因>' 再结束】，",
    "     禁止静默停摆——静默停摆会导致主对话无法判断卡点，只能盲目重启。",
]

def _fresh_state():
    return {"step": "idle", "task": "", "context": None, "mechanics_result": None,
            "user_choice": None, "choice_detail": None, "parallel_mode": None,
            "subagent_config": None, "created_at": None}

def load_state():
    if not os.path.exists(STATE_PATH):
        return _fresh_state()
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return _fresh_state()

def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def _extract_load_n(text):
    """【BUG-01 修复】从用户输入中提取【明确给出】的载荷数值（统一换算为 N）。

    原缺陷：estimate_mechanics 只做关键词匹配，从不读用户写的数值 ——
      用户明明写了「100N」，函数却从默认 500 起算，再被「冲击」关键词 ×2
      → 报出 1000N，把真实载荷放大 10 倍（实测 bug）。
    修复：先扫数字+单位，命中即作为【额定/工作载荷】基准，关键词只用于
      给"动载系数"，绝不再改动用户给出的绝对值。

    支持写法（大小写/中英文）：
      100N / 100 N / 100牛顿 / 0.1kN / 100千牛
      10kg / 10 kg / 10公斤 / 10千克 / 1t / 1吨
      220lbf / 220磅

    Returns:
        (value_n, matched_text) —— 未识别到则 (None, None)
    """
    import re as _re
    s = str(text or "")
    # 单位 → N 的换算系数；长单位排前面，避免 "kn" 被 "n" 抢先匹配
    _PATTERNS = (
        (r"(\d+(?:\.\d+)?)\s*(?:kn|千牛)", 1000.0),
        (r"(\d+(?:\.\d+)?)\s*(?:kgf|千克力|公斤力)", 9.80665),
        (r"(\d+(?:\.\d+)?)\s*(?:lbf|磅)", 4.4482216152605),
        (r"(\d+(?:\.\d+)?)\s*(?:n|牛顿)(?![a-z0-9])", 1.0),
        (r"(\d+(?:\.\d+)?)\s*(?:kg|公斤|千克)", 9.80665),
        (r"(\d+(?:\.\d+)?)\s*(?:t|吨)(?![a-z0-9])", 9806.65),
    )
    best = None
    for pat, k in _PATTERNS:
        for m in _re.finditer(pat, s, flags=_re.IGNORECASE):
            try:
                v = float(m.group(1)) * k
            except Exception:
                continue
            if v <= 0:
                continue
            # 取【最靠前】的一次命中作为额定载荷（用户通常先写主要工况）
            if best is None or m.start() < best[2]:
                best = (v, m.group(0).strip(), m.start())
    if best is None:
        return None, None
    return best[0], best[1]


def _extract_life_years(text):
    """【BUG-02 修复】从用户输入中提取设计寿命（年）。找不到返回 None。"""
    import re as _re
    s = str(text or "")
    m = _re.search(r"(\d+(?:\.\d+)?)\s*(?:年|years?|yrs?)", s, flags=_re.IGNORECASE)
    if m:
        try:
            v = float(m.group(1))
            if 0 < v <= 200:
                return v
        except Exception:
            pass
    # 选项文本兜底
    if "20~30年" in s or "20-30年" in s:
        return 30.0
    if "10年" in s:
        return 10.0
    if "3~5年" in s or "3-5年" in s:
        return 5.0
    if "1年以内" in s:
        return 1.0
    return None


# ══ 【Bug-03/04/05/11 修复】任务类型模板 + 材料推断 ═══════════════════════════
# 原缺陷：
#   · QUESTION_SPEC 与 _room_parts_scope() 全是"机械臂"口径
#     （Q1 机构类型=6轴关节臂 / Q2 臂长=大臂300+小臂250 / Q4 末端执行器=夹爪，
#      房间零件范围=大臂/小臂/关节/齿条）。实测做"竞速小车"时 17 题与零件范围
#     语义完全错位，必须人工把 17 题与 4 个房间的零件范围全部改写。
#   · estimate_mechanics 的材料推荐是硬编码关键词链（轻量→6061 铝），
#     完全不读用户 Q13「材料倾向」与 Q14「加工方式」的回答 ——
#     用户明确"3D打印 / PETG+ABS+Nylon"，门禁却回 6061-T6 铝，
#     并把它写进 load_case，进而被 swapi 当成默认材料赋给每个零件。
#
# 修复：按任务关键词选【模板】，模板自带 questions / lite_ids / room_scope；
#      材料推断改为"先读用户回答里的材料与工艺，再退回关键词"。

# 3D 打印常用材料 → physics material_db 的 id（Bug-13/29 同步补齐 material_db）
_PRINT_MATERIALS = {
    "petg": {"id": "PETG", "name": "PETG", "youngs_modulus_mpa": 2000.0,
             "poissons_ratio": 0.37, "yield_strength_mpa": 40.0,
             "uts_mpa": 50.0, "density_kg_m3": 1270.0},
    "pla": {"id": "PLA", "name": "PLA", "youngs_modulus_mpa": 3500.0,
            "poissons_ratio": 0.36, "yield_strength_mpa": 55.0,
            "uts_mpa": 60.0, "density_kg_m3": 1240.0},
    "nylon": {"id": "NYLON_PA12", "name": "Nylon PA12 (SLS/MJF)",
              "youngs_modulus_mpa": 1700.0, "poissons_ratio": 0.39,
              "yield_strength_mpa": 48.0, "uts_mpa": 50.0,
              "density_kg_m3": 1010.0},
    "pa12": {"id": "NYLON_PA12", "name": "Nylon PA12 (SLS/MJF)",
             "youngs_modulus_mpa": 1700.0, "poissons_ratio": 0.39,
             "yield_strength_mpa": 48.0, "uts_mpa": 50.0,
             "density_kg_m3": 1010.0},
    "pa66": {"id": "NYLON_6_6", "name": "Nylon 6/6 (dry)",
             "youngs_modulus_mpa": 2800.0, "poissons_ratio": 0.39,
             "yield_strength_mpa": 80.0, "uts_mpa": 85.0,
             "density_kg_m3": 1140.0},
    "tpu": {"id": "TPU", "name": "TPU (flexible)", "youngs_modulus_mpa": 50.0,
            "poissons_ratio": 0.48, "yield_strength_mpa": 30.0,
            "uts_mpa": 40.0, "density_kg_m3": 1210.0},
    "abs": {"id": "ABS", "name": "ABS Plastic", "youngs_modulus_mpa": 2400.0,
            "poissons_ratio": 0.40, "yield_strength_mpa": 45.0,
            "uts_mpa": 50.0, "density_kg_m3": 1050.0},
    "pc": {"id": "PC_POLYCARBONATE", "name": "Polycarbonate (PC)",
           "youngs_modulus_mpa": 2400.0, "poissons_ratio": 0.37,
           "yield_strength_mpa": 65.0, "uts_mpa": 70.0,
           "density_kg_m3": 1200.0},
    "pom": {"id": "POM", "name": "POM (Delrin)",
            "youngs_modulus_mpa": 3100.0, "poissons_ratio": 0.35,
            "yield_strength_mpa": 70.0, "uts_mpa": 75.0,
            "density_kg_m3": 1410.0},
}

# 工艺关键词 → 是否属于"3D 打印"路线
_PRINT_PROCESS_KW = ("3d打印", "3d 打印", "fdm", "sls", "mjf", "光固化",
                     "sla", "dlp", "增材", "打印件", "打印")


# ── 【Bug-03/05 修复】任务类型模板表 ────────────────────────────────────────
# 每个模板自带：关键词权重 / 背景问题集 / 精简题 id / 房间零件范围。
# 这样"竞速小车"任务拿到的是轮距/轴距/舵机/电机/轮胎题目与车架/车轮零件范围，
# 而不是机械臂的"大臂/小臂/夹爪"。未命中任何模板 → generic（沿用通用问题）。
TASK_TYPE_TEMPLATES = {
    "vehicle": {
        "label": "轮式车辆 / 小车",
        "keywords": (
            ("竞速小车", 5), ("小车", 4), ("赛车", 4), ("车辆", 3), ("车架", 3),
            ("阿克曼", 5), ("转向节", 4), ("舵机", 3), ("轮距", 5), ("轴距", 5),
            ("轮胎", 4), ("车轮", 4), ("差速", 4), ("悬挂", 3), ("悬架", 3),
            ("底盘", 3), ("四轮", 4), ("前轮", 3), ("后轮", 3), ("赛道", 2),
            ("vehicle", 3), ("car", 2), ("wheel", 2), ("ackermann", 4),
        ),
        "questions": [
            {"id": "mech_type", "group": "A 结构形态", "title": "车辆/机构类型",
             "hint": "几轮？什么转向与驱动形式？",
             "options": ["阿克曼转向4轮", "差速转向(左右轮速差)", "履带式",
                         "三轮/倒三轮", "四轮独立转向", "其他(请说明)"],
             "mech": True},
            {"id": "arm_lengths", "group": "A 结构形态", "title": "整车尺寸与轴距轮距",
             "hint": "长×宽×高、轴距、轮距，带单位（如 长300×宽180×高120，轴距200 轮距150）",
             "options": ["你决定", "其他(请说明)"],
             "mech": True},
            {"id": "base_form", "group": "A 结构形态", "title": "车架形式",
             "hint": "底板/骨架/管架？材料与层数？",
             "options": ["单片底板", "底板+横梁骨架", "双层板", "管架/桁架",
                         "一体成型车身", "你决定"],
             "mech": True},
            {"id": "end_effector", "group": "A 结构形态", "title": "功能附件与安装",
             "hint": "摄像头/传感器/电池仓/电机座的安装要求",
             "options": ["前置摄像头座", "电池仓", "电机座", "云台/传感器支架",
                         "暂无(仅作结构验证)", "你决定"],
             "mech": True},
            {"id": "envelope", "group": "A 结构形态", "title": "赛道约束与包络",
             "hint": "赛道尺寸/窄段/限高，以及整车外形上限",
             "options": ["无严格限制", "你决定", "其他(请说明)"],
             "mech": True},
            {"id": "load_magnitude", "group": "B 工况载荷", "title": "整车重量与载荷",
             "hint": "整车目标重量(含电池) / 碰撞冲击载荷（N 或 kg，注明单位）",
             "options": ["你决定", "其他(请说明)"],
             "mech": True},
            {"id": "load_type", "group": "B 工况载荷", "title": "负载类型",
             "hint": "动载荷性质，直接影响安全系数与疲劳校核",
             "options": ["静态保持", "平稳行驶", "频繁启停/冲击", "过弯侧向力",
                         "振动环境", "你决定"],
             "mech": True},
            {"id": "environment", "group": "B 工况载荷", "title": "使用场合",
             "hint": "环境条件决定材料与防护等级",
             "options": ["普通室内赛道", "户外(日晒雨淋)", "潮湿/水坑", "高温(>150℃)",
                         "低温(<-20℃)", "粉尘环境", "你决定"],
             "mech": True},
            {"id": "lifespan", "group": "B 工况载荷", "title": "设计寿命",
             "hint": "目标使用寿命（年）或跑动圈数",
             "options": ["1年以内(短周期)", "3~5年", "10年", "20~30年(长期)", "你决定"],
             "mech": True},
            {"id": "drive", "group": "C 驱动与运行", "title": "驱动方式",
             "hint": "动力来源与传动形式",
             "options": ["后轮双无刷电机+减速箱直驱", "单电机+传动轴", "有刷电机+齿轮箱",
                         "步进电机", "舵机驱动", "手动", "你决定"],
             "mech": False},
            {"id": "duty", "group": "C 驱动与运行", "title": "工作制度",
             "hint": "运行频率与占空比",
             "options": ["连续运行(24/7)", "间歇运行(单圈数分钟)", "偶尔使用",
                         "单次动作", "你决定"],
             "mech": False},
            {"id": "precision", "group": "C 驱动与运行", "title": "精度要求",
             "hint": "转向精度/直线度，决定配合公差与加工等级",
             "options": ["一般(±1mm)", "较高(转向±0.5°、直线偏差<20mm/5m)",
                         "精密(±0.01mm)", "无要求(仅结构验证)", "你决定"],
             "mech": False},
            {"id": "material_pref", "group": "D 制造条件", "title": "材料倾向",
             "hint": "材料偏好或限制（可指定不同零件用不同材料）",
             "options": ["PETG", "PLA", "ABS", "Nylon/PA12", "TPU",
                         "碳纤维/玻纤增强", "Q235碳钢", "6061-T6铝",
                         "无偏好(由你选)", "你决定"],
             "mech": False},
            {"id": "manufacturing", "group": "D 制造条件", "title": "加工方式",
             "hint": "打算怎么造，影响结构工艺性",
             "options": ["3D打印(FDM)", "3D打印(SLS/MJF)", "数控加工(CNC)",
                         "激光切割+组装", "外购标准件为主", "你决定"],
             "mech": False},
            {"id": "budget", "group": "D 制造条件", "title": "成本/重量倾向",
             "hint": "在成本、重量、强度之间的取舍",
             "options": ["成本优先(经济方案)", "重量优先(轻量化)",
                         "强度/可靠性优先", "均衡", "你决定"],
             "mech": False},
            {"id": "special", "group": "E 特殊要求", "title": "特殊要求",
             "hint": "抗跌落/防水/防尘/竞赛规则等硬性要求",
             "options": ["无特殊要求", "抗跌落", "防水防尘(IP等级)",
                         "需符合竞赛规则", "需符合某国标", "你决定"],
             "mech": False},
            {"id": "open_extra", "group": "Z 开放补充", "title": "其他补充（开放式）",
             "hint": "以上没覆盖到的任何要求、约束、参考案例或偏好，请自由填写；"
                     "没有可写「无」",
             "options": [], "mech": False, "open": True},
        ],
        "lite_ids": ("mech_type", "arm_lengths", "load_magnitude",
                     "load_type", "open_extra"),
        "room_scope": {
            "structural": "车架底板、横梁、悬架臂、舵机连杆、电池仓等结构件",
            "transmission": "车轮、车轴、电机座、减速齿轮、小齿轮、转向节、轴承座等传动件",
            "housing": "底壳、上罩、摄像头座、电池托架、横梁护罩等壳体/外观件",
            "support": "转向支撑座、舵机支架、加强筋等辅助支撑件",
            "assembly": "总装以上全部零件 + 干涉检查 + 整体验证",
            "drafting": "对总装体出工程图（三视图+标注+DWG/PDF）",
            "spring": "悬挂弹簧等弹性元件",
            "thermal": "电机/电调散热件",
            "corrosion": "防腐蚀处理件",
        },
    },
    "robot_arm": {
        "label": "机械臂 / 关节机器人",
        "keywords": (
            ("机械臂", 5), ("机器人", 4), ("关节臂", 5), ("大臂", 5), ("小臂", 5),
            ("夹爪", 4), ("末端执行器", 4), ("云台", 3), ("直角坐标", 3),
            ("臂长", 5), ("法兰盘", 3), ("六轴", 4), ("robot arm", 5),
        ),
        "questions": None,   # None = 用通用 QUESTION_SPEC（原机械臂口径）
        "lite_ids": ("mech_type", "arm_lengths", "load_magnitude",
                     "load_type", "open_extra"),
        "room_scope": {
            "structural": "大臂、小臂、连杆、立柱、横梁等臂部/骨架结构件",
            "transmission": "关节、齿轮、齿条、轴、轴承座、电机座、联轴器等传动件",
            "housing": "底座、外壳、护罩、机架、安装板等支撑/壳体件",
            "assembly": "总装以上全部零件 + 干涉检查 + 整体验证",
            "drafting": "对总装体出工程图（三视图+标注+DWG/PDF）",
            "support": "轴承座、支撑架等辅助支撑件",
            "spring": "弹簧等弹性元件",
            "thermal": "耐热/隔热部件",
            "corrosion": "防腐处理件",
        },
    },
    "transmission": {
        "label": "传动机构 / 减速器",
        "keywords": (
            ("减速器", 5), ("减速机", 5), ("齿轮箱", 5), ("传动", 3),
            ("行星", 4), ("蜗轮", 4), ("蜗杆", 4), ("同步带", 3), ("链传动", 3),
            ("齿轮", 3), ("齿条", 3), ("联轴器", 3), ("gearbox", 4),
        ),
        "questions": None,
        "lite_ids": ("mech_type", "arm_lengths", "load_magnitude",
                     "load_type", "open_extra"),
        "room_scope": {
            "structural": "箱体、端盖、支座、安装底板等结构件",
            "transmission": "各级齿轮、轴、键、轴承、联轴器、齿条等传动件",
            "housing": "箱体、外壳、护罩、油封盖等壳体件",
            "assembly": "总装以上全部零件 + 干涉检查 + 整体验证",
            "drafting": "对总装体出工程图（三视图+标注+DWG/PDF）",
            "support": "轴承座、支撑架等辅助支撑件",
            "spring": "弹簧等弹性元件",
            "thermal": "散热/耐热部件",
            "corrosion": "防腐处理件",
        },
    },
    "generic": {
        "label": "通用机械结构",
        "keywords": (),
        "questions": None,
        "lite_ids": ("mech_type", "arm_lengths", "load_magnitude",
                     "load_type", "open_extra"),
        "room_scope": None,   # None = 用原 _room_parts_scope 的通用表
    },
}


def task_template(task, context=None):
    """取当前任务对应的模板（未命中返回 generic 模板）。"""
    key = detect_task_type(task, context)
    return key, TASK_TYPE_TEMPLATES.get(key) or TASK_TYPE_TEMPLATES["generic"]


def question_spec_for(task, context=None):
    """【Bug-03 修复】按任务类型返回问题集。

    车辆模板 → 车辆问题；机械臂/传动/通用 → 原 QUESTION_SPEC。
    这是"任务类型自动识别 → 加载对应题目模板"的落地点。
    """
    _key, tpl = task_template(task, context)
    qs = tpl.get("questions")
    return list(qs) if qs else list(QUESTION_SPEC)


def lite_ids_for(task, context=None):
    """按任务类型返回精简题 id 列表。"""
    _key, tpl = task_template(task, context)
    return tuple(tpl.get("lite_ids") or LITE_QUESTION_IDS)


def _user_material_pref(task, context):
    """从用户回答里提取"材料倾向"与"加工方式"两题的原文片段。

    门禁的 Q13（材料倾向）/ Q14（加工方式）在第二段回答里通常写作
    「⑬PETG车架车轮ABS外壳Nylon齿轮」「⑭全部3D打印+外购轴承螺丝电机」，
    这里按常见编号/关键词定位，拿不到就返回空串。
    """
    import re as _re
    txt = str(context or "")
    hits = []
    # ① 带圈数字编号 ⑬/⑭ 或 13./14.
    for pat in (r"[⑬⒀]\s*([^\n⑭⒁]{0,120})", r"[⑭⒁]\s*([^\n⑮⒂]{0,120})",
                r"(?:^|\n)\s*13[\.、:：]\s*([^\n]{0,120})",
                r"(?:^|\n)\s*14[\.、:：]\s*([^\n]{0,120})"):
        for m in _re.finditer(pat, txt):
            hits.append(m.group(1))
    # ② 直接含"材料/加工/工艺"字样的句子
    for m in _re.finditer(r"[^\n。；;]{0,40}(?:材料|加工方式|工艺)[^\n。；;]{0,80}", txt):
        hits.append(m.group(0))
    return " ".join(hits)


def infer_material(task, context=None):
    """【Bug-04/11 修复】推断本次设计的材料。

    优先级（从高到低）：
      ① 用户回答里【显式写出】的 3D 打印材料（PETG/PLA/Nylon/TPU/ABS/PC/POM）
         —— 这是用户的真实选择，必须尊重；
      ② 识别到"3D打印/增材/FDM…"工艺，但没点名材料 → 默认 PETG
         （最通用的结构件打印材料）；
      ③ 用户点名了金属牌号（Q235/45#/40Cr/304/316/6061/7075…）；
      ④ 退回原来的关键词链（高温/轻量/腐蚀/传动）。

    Returns:
        (display_name, source, material_dict) —— material_dict 与 load_case 的
        material 字段同形，可直接写入 physics 工况。
    """
    import re as _re
    raw = (str(task or "") + " " + str(context or ""))
    low = raw.lower()
    pref = _user_material_pref(task, context).lower()
    # 用户回答优先，其次全文
    hay = (pref + " " + low) if pref.strip() else low

    # ① 显式打印材料（按出现顺序，取最先提到的作为"主材料"）
    _order = ("petg", "pa12", "pa66", "nylon", "尼龙", "pla", "tpu",
              "abs", "pc", "pom")
    best = None
    for key in _order:
        idx = hay.find(key)
        if idx < 0:
            continue
        if best is None or idx < best[0]:
            best = (idx, key)
    if best is not None:
        key = best[1]
        # 尼龙的中文别名归一到 pa66 表项
        if key in ("尼龙",):
            key = "nylon"
        info = _PRINT_MATERIALS.get(key)
        if info:
            return (info["name"], "用户回答/任务描述中的材料选择（3D 打印材料库）",
                    dict(info, source="workflow_gate.infer_material"))

    # ② 只写了工艺没写材料 → 默认 PETG
    if any(k in hay for k in _PRINT_PROCESS_KW):
        info = _PRINT_MATERIALS["petg"]
        return (info["name"],
                "识别到 3D 打印工艺但未指定材料 → 默认 PETG（可显式覆盖）",
                dict(info, source="workflow_gate.infer_material.default_print"))

    # ③ 金属牌号
    for pat, mid, E, nu, sy, rho, disp in (
            ("cr12mov|模具钢|耐热", "ST_API_4140", 205000.0, 0.29, 655.0, 7850.0,
             "Cr12MoV (等效 AISI 4140)"),
            ("7075", "AL_7075", 72000.0, 0.33, 503.0, 2810.0, "Aluminum 7075-T6"),
            ("6061", "AL_6061_T6", 68900.0, 0.33, 276.0, 2700.0,
             "Aluminum 6061-T6"),
            ("316", "ST_STAINLESS_316", 193000.0, 0.30, 170.0, 8000.0,
             "Stainless Steel 316"),
            ("304|不锈钢", "ST_STAINLESS_304", 193000.0, 0.30, 205.0, 8000.0,
             "Stainless Steel 304"),
            ("40cr|45#|传动|齿轮", "ST_API_4140", 205000.0, 0.29, 655.0, 7850.0,
             "40Cr / AISI 4140"),
            ("q235|碳钢", "ST_API_S235", 210000.0, 0.30, 235.0, 7850.0,
             "Q235 (等效 S235JR)")):
        if _re.search(pat, hay, flags=_re.IGNORECASE):
            return (disp, "用户回答/任务描述中的金属牌号",
                    {"id": mid, "name": disp, "youngs_modulus_mpa": E,
                     "poissons_ratio": nu, "yield_strength_mpa": sy,
                     "uts_mpa": round(sy * 1.2, 1), "density_kg_m3": rho,
                     "source": "workflow_gate.infer_material"})

    # ④ 关键词兜底（保持与旧行为兼容）
    t = low
    if any(k in t for k in ["高温", "heat"]):
        disp = "Cr12MoV"
    elif any(k in t for k in ["轻量", "aluminum", "6061"]):
        disp = "6061-T6 Aluminum"
    elif any(k in t for k in ["腐蚀", "rust", "不锈钢"]):
        disp = "304 Stainless"
    elif any(k in t for k in ["传动", "齿轮"]):
        disp = "45# Steel / 40Cr"
    else:
        disp = "Q235 Carbon Steel"
    return (disp, "关键词兜底（用户未给出材料，请复核）", None)


def detect_task_type(task, context=None):
    """【Bug-03/05 修复】按关键词给任务分类，决定题目模板与房间零件范围。

    Returns: 模板键（"vehicle" / "robot_arm" / "transmission" / "generic"）
    """
    t = ((str(task or "") + " " + str(context or ""))).lower()
    scores = {}
    for key, spec in TASK_TYPE_TEMPLATES.items():
        if key == "generic":
            continue
        s = 0
        for kw, w in spec.get("keywords", ()):
            if kw.lower() in t:
                s += w
        scores[key] = s
    if not scores:
        return "generic"
    best_key, best_score = max(scores.items(), key=lambda kv: kv[1])
    return best_key if best_score > 0 else "generic"


def estimate_mechanics(task, context=None):
    """力学估算（v2 · BUG-01/02/07 修复）。

    ── BUG-01 修复：先读用户数值，再做关键词修正 ─────────────────────────
      额定载荷 load_n 的取值优先级：
        ① 用户输入里【明确写出】的数值（100N / 10kg …）—— 最高优先级
        ② 无明确数值时，才用关键词粗估（默认 500N，大/重型 2000N）
      动载系数 impact_factor 单独记录，只对基准载荷乘【一次】，
      绝不改变"用户给出的额定值"本身。

    ── BUG-07 修复：同时给出 nominal / design 两个口径 ──────────────────
      nominal_load_n ：额定（工作）载荷 —— 与 physics 载荷工况必须一致
      design_load_n  ：设计（等效）载荷 = nominal × impact_factor
      两者一起返回，并由 write_load_case_file() 落盘成 physics 直接可读的
      载荷工况，从根上消除"门禁说 1000N、physics 算 100N"的口径分裂。

    ── BUG-02 修复：输出疲劳/寿命校核所需的工况参数 ─────────────────────
      load_type / design_life_years / cycles_per_year 一并给出，
      供 physics 的 fatigue 模块做 S-N + Miner 累积损伤校核。
    """
    t = (task + " " + (context or "")).lower()
    raw = (task or "") + " " + (context or "")

    # ── 1) 额定载荷：用户数值优先 ──────────────────────────────────────
    user_load, matched = _extract_load_n(raw)
    if user_load is not None:
        nominal_load = float(user_load)
        load_source = "用户输入: %s" % (matched or "")
    else:
        # 无明确数值 → 关键词粗估（明确标注为估算值，供用户复核）
        if any(k in t for k in ["大型", "重型", "large"]):
            nominal_load = 2000.0
            load_source = "关键词粗估（大型/重型）"
        elif any(k in t for k in ["小型", "轻型", "small"]):
            nominal_load = 300.0
            load_source = "关键词粗估（小型/轻型）"
        else:
            nominal_load = 500.0
            load_source = "默认基准值（用户未给出载荷，请务必复核）"

    # ── 2) 动载系数（只乘一次，不放大用户给的额定值）──────────────────
    if any(k in t for k in ["冲击", "impact", "shock", "频繁启停"]):
        impact_factor, load_type = 2.0, "impact"
    elif any(k in t for k in ["动态", "dynamic", "循环", "疲劳", "往复", "振动"]):
        impact_factor, load_type = 1.3, "cyclic"
    else:
        impact_factor, load_type = 1.0, "static"

    design_load = nominal_load * impact_factor

    # ── 3) 安全系数按负载性质取值（GB/T 3811 工程惯例量级）────────────
    if load_type == "impact":
        sf = 3.0
    elif load_type == "cyclic":
        sf = 2.5
    else:
        sf = 2.0
    if any(k in t for k in ["传动", "齿轮", "轴", "gear"]):
        sf = max(sf, 2.5)
    elif any(k in t for k in ["壳体", "机架", "housing", "frame", "承载", "support", "bearing"]):
        sf = max(sf, 2.5)

    # ── 4) 材料推荐（【Bug-04/11 修复】先读用户回答，再退回关键词）──────
    # 原缺陷：这里是一条硬编码关键词链，"轻量/6061" 命中就给铝，完全不读
    #   用户 Q13「材料倾向」/Q14「加工方式」的回答。实测任务写明
    #   "3D打印 + PETG车架/ABS外壳/Nylon齿轮"，门禁却回 6061-T6 铝，
    #   并把它写进 gate_load_case.json → swapi 再据此给每个零件赋铝。
    # 修复：infer_material() 以【用户显式材料】为最高优先，其次识别 3D 打印
    #   工艺（默认 PETG），最后才退回旧关键词链。material_spec 一并带出，
    #   供 write_load_case_file 直接落盘（不再二次解析）。
    mat, mat_source, material_spec = infer_material(task, context)
    task_type = detect_task_type(task, context)
    task_type_label = (TASK_TYPE_TEMPLATES.get(task_type) or {}).get("label", task_type)

    # ── 5) 疲劳/寿命参数（BUG-02 支撑）────────────────────────────────
    life_years = _extract_life_years(raw)
    if life_years is None:
        # 【BUG-02 一致性】默认寿命必须与 load_case.DEFAULT_ACCEPTANCE 一致（30 年），
        #   否则门禁落盘的工况与 physics 默认口径不同 → 又是一处数值分裂。
        life_years = 30.0
        life_source = "默认 30 年（用户未给出寿命，请复核）"
    else:
        life_source = "用户输入"
    # 动作频次：按工作制度粗估（保守取中值），供 Miner 累积损伤使用
    if any(k in t for k in ["连续运行", "24/7"]):
        cycles_per_year = 2_000_000.0
        duty_note = "连续运行(24/7) 估算"
    elif any(k in t for k in ["间歇", "每天数小时"]):
        cycles_per_year = 200_000.0
        duty_note = "间歇运行(每天数小时) 估算"
    elif any(k in t for k in ["偶尔"]):
        cycles_per_year = 20_000.0
        duty_note = "偶尔使用 估算"
    elif any(k in t for k in ["单次动作", "静态保持"]):
        cycles_per_year = 1_000.0
        duty_note = "单次动作/静态保持 估算"
    else:
        cycles_per_year = 200_000.0
        duty_note = "默认按间歇运行估算（请复核）"

    notes = ("额定载荷来源: %s；动载系数 ×%.1f（%s）；设计寿命 %.0f 年（%s）；"
             "动作频次 %.0f 次/年（%s）"
             % (load_source, impact_factor, load_type, life_years, life_source,
                cycles_per_year, duty_note))

    return {
        # ── 兼容旧字段（字符串形式，供旧文案直接引用）──
        "load_estimate": "%d N" % round(design_load),
        "safety_factor": sf,
        "material_recommend": mat,
        "scenario": "通用工业场景",
        "notes": notes,
        # ── BUG-07 新增：机器可读的双口径数值（physics 必须用同名数值）──
        "nominal_load_n": round(nominal_load, 3),
        "design_load_n": round(design_load, 3),
        "impact_factor": impact_factor,
        "load_type": load_type,
        "load_source": load_source,
        "user_load_n": (round(user_load, 3) if user_load is not None else None),
        # ── BUG-02 新增：疲劳/寿命校核输入 ──
        "design_life_years": life_years,
        "cycles_per_year": cycles_per_year,
        # ── 【BUG-07】口径警告：粗估时必须让调用方与用户都看得见 ──
        "load_is_estimate": user_load is None,
        # ── 【Bug-04/11 新增】材料来源与可落盘的完整材料规格 ──
        "material_source": mat_source,
        "material_spec": material_spec,
        # ── 【Bug-03/05 新增】任务类型（决定题目模板与房间零件范围）──
        "task_type": task_type,
        "task_type_label": task_type_label,
        "consistency_contract": (
            "physics 载荷工况的 magnitude_n 必须等于 nominal_load_n"
            "（设计校核另乘 impact_factor，不得直接写 design_load_n 当额定值）"),
    }

# ══ 【BUG-07 修复】门禁 → physics 载荷工况的【单一数值来源】══════════════════
# 原缺陷：门禁自己算一套（estimate_mechanics 结果只写进 workflow_state.txt 文案），
#   physics 那边由小屋/模型另写一份 load_case.json，两边各写各的 ——
#   实测出现"门禁口径 1000N / physics 实际 100N"的数据分裂，校核结论不可信。
# 修复：门禁在 mechanics_done 阶段【直接把载荷工况落盘】成 physics 可读文件，
#   并把路径一并返回。physics 侧必须用该文件（或用相同的 nominal_load_n），
#   从此两边共用同一份数值，物理上不可能再打架。
#
# 疲劳/寿命（BUG-02）：同一文件里带 acceptance.design_life_years 与
#   operating_cycles_per_year，physics 的疲劳模块直接读它做 S-N + Miner 校核。

# 材料名 → load_case 材料字段（与 physics/material_db.py 的 id 对齐）
_MATERIAL_LOOKUP = (
    # (关键词, material_id, E_mpa, nu, yield_mpa, density_kg_m3, 显示名)
    (("cr12mov", "模具钢", "耐热"), "ST_API_4140", 205000.0, 0.29, 655.0, 7850.0, "Cr12MoV (等效 AISI 4140)"),
    (("6061", "铝"), "AL_6061_T6", 68900.0, 0.33, 276.0, 2700.0, "Aluminum 6061-T6"),
    (("7075",), "AL_7075", 72000.0, 0.33, 503.0, 2810.0, "Aluminum 7075-T6"),
    (("304", "不锈钢", "腐蚀"), "ST_STAINLESS_304", 193000.0, 0.30, 205.0, 8000.0, "Stainless Steel 304"),
    (("316",), "ST_STAINLESS_316", 193000.0, 0.30, 170.0, 8000.0, "Stainless Steel 316"),
    (("40cr", "45#", "传动", "齿轮"), "ST_API_4140", 205000.0, 0.29, 655.0, 7850.0, "40Cr / AISI 4140"),
    (("q235", "碳钢"), "ST_API_S235", 210000.0, 0.30, 235.0, 7850.0, "Q235 (等效 S235JR)"),
)


# ── 【Bug-12 修复】零件包络 vs 赛道/整机尺寸的语境判别 ──────────────────────
# 原缺陷：write_load_case_file 用无差别正则抓所有"数字+mm"，再取最大的三个
#   作为设计域。实测"竞速小车"任务的赛道尺寸（4500×2100×1600mm）被当成
#   设计域落盘 → physics 在 4.5m 的巨型域上把 fixed_end 放 x=0、load_face 放
#   x=4500，而实际零件只有 300×180×120 —— 空间选择器匹配不到任何面，FEA 无意义。
#
# 修复：① 建立"赛道/场地/整机"语境词表，命中即排除该数字；
#      ② 建立"零件"语境词表，命中则优先采纳；
#      ③ 按任务类型给典型零件包络兜底；
#      ④ 若最终包络仍明显是"整机量级"（超过任务典型零件 6 倍），
#         退回任务典型包络并在 meta 里写明原因，绝不把赛道当零件。
_TRACK_CONTEXT_KW = (
    "赛道", "方框", "圆弧", "窄段", "入口", "场地", "跑道", "直线段",
    "全长", "外形", "占地面积", "工作范围", "行程范围", "臂展", "reach",
)
_PART_CONTEXT_KW = (
    "零件", "底板", "车轮", "车轴", "齿轮", "横梁", "悬架", "舵机", "电池仓",
    "上罩", "底壳", "电机座", "转向节", "连杆", "轴承座", "支架", "加强筋",
    "摄像头", "托架", "护罩", "厚", "壁厚", "孔径", "外径", "内孔", "Ø", "φ",
)
# 各任务类型的"典型零件包络"（mm），用于兜底与合理性护栏
_TASK_DEFAULT_ENVELOPE = {
    "vehicle": (300.0, 180.0, 120.0),
    "robot_arm": (350.0, 300.0, 200.0),
    "transmission": (200.0, 200.0, 100.0),
    "generic": (200.0, 60.0, 60.0),
}


def _material_for(mat_name, task_text="", material_spec=None):
    """把"推荐材料"解析成 physics load_case 需要的材料字典。

    【Bug-04/11 修复】若 mechanics 里已带 material_spec（infer_material 的产物），
    直接采用 —— 它已经尊重了用户的材料选择（PETG/ABS/Nylon…），
    不再经过"显示名 → 关键词 → 金属"这条会把塑料误判成铝的链路。
    """
    if isinstance(material_spec, dict) and material_spec.get("id"):
        return dict(material_spec)
    hay = (str(mat_name or "") + " " + str(task_text or "")).lower()
    # 打印材料优先（Bug-04/11）：显示名里带 PETG/PLA/Nylon 等要能正确落盘
    for key, info in _PRINT_MATERIALS.items():
        if key in hay:
            return dict(info, source="workflow_gate._material_for(print)")
    for keys, mid, E, nu, sy, rho, disp in _MATERIAL_LOOKUP:
        if any(k in hay for k in keys):
            return {"id": mid, "name": disp, "youngs_modulus_mpa": E,
                    "poissons_ratio": nu, "yield_strength_mpa": sy,
                    "uts_mpa": round(sy * 1.2, 1), "density_kg_m3": rho,
                    "source": "workflow_gate.estimate_mechanics"}
    return {"id": "ST_API_S235", "name": "Q235 (等效 S235JR)",
            "youngs_modulus_mpa": 210000.0, "poissons_ratio": 0.30,
            "yield_strength_mpa": 235.0, "uts_mpa": 360.0,
            "density_kg_m3": 7850.0, "source": "workflow_gate.default"}


def _extract_part_envelope(spec_text, task_type="generic"):
    """【Bug-12 修复】从任务/背景回答里提取【零件】包络（排除赛道/整机尺寸）。

    算法（三步，全部双向检查语境）：
      ① 显式带标签的零件尺寸：长/宽/高/厚/直径/轴距/轮距/孔径/外径 + 数字
         —— 这是最强的"零件"证据，直接采纳；
      ② 尺寸三元组 a×b×c（可选带 mm）—— 若语境是赛道/场地则排除；
      ③ 其余 数字+mm —— 仅当左右各 12 字窗口内【没有】赛道/场地词，
         且量级不超过任务典型零件包络的 6 倍时才采纳。

    关键修正（对照实测）：赛道尺寸的"赛道词"常在数字【之后】
    （如「1000mm窄段」「4500x4500mm方框赛道」「2100mm直线」），
    只检查前文会漏掉 → 这里左右都查。

    Returns: (L, W, H, note) —— note 说明取值来源或兜底原因。
    """
    import re as _re
    text = str(spec_text or "")
    typ = _TASK_DEFAULT_ENVELOPE.get(task_type, _TASK_DEFAULT_ENVELOPE["generic"])
    _limit = max(typ) * 6.0
    part_dims = []

    def _win(pos, n=12):
        """数字左右各 n 字的语境窗口（双向）。"""
        return text[max(0, pos - n):pos + n]

    def _is_track(pos, n=12):
        return any(k in _win(pos, n) for k in _TRACK_CONTEXT_KW)

    # ── ① 显式带标签的零件尺寸（最强证据）────────────────────────────
    # 标签后紧跟数字；排除"精度/公差"语境与赛道语境。
    # 注意：不含"轴距/轮距" —— 它们是整车布置参数，不是【零件】包络，
    #   纳入会把 300×180×120 的包络撑成 300×200×180（实机实测）。
    _LABELS = (r"长|宽|高|厚|壁厚|直径|外径|内径|孔径|"
               r"底板|车架|车轮|车板|齿轮|横梁|连杆|支架|齿宽")
    for m in _re.finditer(r"(?:%s)\s*[:：=]?\s*(\d+(?:\.\d+)?)" % _LABELS, text):
        try:
            v = float(m.group(1))
        except Exception:
            continue
        if not (1.0 <= v <= 100000.0):
            continue
        if _is_track(m.start()):
            continue
        if v not in part_dims:
            part_dims.append(v)

    # ── ② 尺寸三元组 a×b×c（可带 mm）────────────────────────────────
    for m in _re.finditer(
            r"(\d+(?:\.\d+)?)\s*[x×*]\s*(\d+(?:\.\d+)?)"
            r"(?:\s*[x×*]\s*(\d+(?:\.\d+)?))?\s*(?:mm|毫米)?", text):
        if _is_track(m.start()):
            continue
        for gi in (1, 2, 3):
            try:
                g = m.group(gi)
            except Exception:
                g = None
            if not g:
                continue
            v = float(g)
            if 1.0 <= v <= _limit and v not in part_dims:
                part_dims.append(v)

    # ── ③ 其余 数字+mm / 数字+米 / 数字+cm ──────────────────────────
    for pat, k in ((r"([±+\-~]?)\s*(\d+(?:\.\d+)?)\s*(?:mm|毫米)", 1.0),
                   (r"(\d+(?:\.\d+)?)\s*(?:cm|厘米)", 10.0),
                   (r"(\d+(?:\.\d+)?)\s*(?:m|米)(?![m米a-z])", 1000.0)):
        for m in _re.finditer(pat, text):
            # 公差/精度语境（前文）
            pre = text[max(0, m.start() - 10):m.start()]
            if any(kw in pre for kw in ("精度", "公差", "偏差", "误差", "±", "+-")):
                continue
            # 显式公差符号前缀（仅 mm 模式有第 1 组）
            if m.lastindex and m.lastindex >= 1:
                try:
                    if m.group(1) in ("±", "+", "~"):
                        continue
                except Exception:
                    pass
            try:
                v = float(m.group(2) if m.lastindex and m.lastindex >= 2
                          else m.group(1)) * k
            except Exception:
                continue
            if not (1.0 <= v <= 100000.0):
                continue
            # 赛道/场地语境（双向）→ 排除
            if _is_track(m.start()):
                continue
            # 量级护栏：明显是整机/赛道量级的不采纳
            if v > _limit:
                continue
            if v not in part_dims:
                part_dims.append(v)

    dims = sorted(set(part_dims), reverse=True)[:3]
    if not dims:
        note = ("未从任务/回答中提取到【零件】包络尺寸（赛道/整机尺寸不计入），"
                "已按任务类型取典型零件包络 %s mm；请在小屋 prompt 中明确零件长宽高。"
                % (tuple(int(x) for x in typ),))
        return typ[0], typ[1], typ[2], note

    while len(dims) < 3:
        dims.append(dims[-1])
    L, W, H = float(dims[0]), float(dims[1]), float(dims[2])

    # ── ④ 合理性护栏：仍像整机/赛道量级 → 退回任务典型包络 ──────────
    if max(L, W, H) > _limit or max(L, W, H) < 1.0:
        note = ("提取到的尺寸 %s 属【整机/赛道量级】而非零件包络，已退回任务典型"
                "零件包络 %s mm（原实现会把赛道当设计域，导致 FEA 空间选择器失配）。"
                % ([round(x, 1) for x in dims], tuple(int(x) for x in typ)))
        return typ[0], typ[1], typ[2], note

    note = ("零件包络取自任务/回答中的零件语境尺寸 %s"
            % ([round(x, 1) for x in dims],))
    return L, W, H, note


def write_load_case_file(state, mechanics, work_dir=None):
    """【BUG-07 修复】把门禁力学估算落盘为 physics 可直接用的载荷工况 JSON。

    【Bug-04/11】材料：优先用 infer_material 的 material_spec（尊重用户选择）。
    【Bug-12】设计域：取【零件包络】，绝不沿用赛道/整机尺寸。
    【Bug-15】problem_id：真实短哈希，不再把任务描述里的尺寸硬塞进去。
    【观察点4】载荷：除重力 -Z 外，追加侧向(X)与纵向(Y)工况，覆盖过弯/驱动失效模式。

    Returns: {"ok", "path", "load_case"} 或 {"ok": False, "error"}
    """
    try:
        task = state.get("task") or ""
        import hashlib as _hashlib

        mat = _material_for(mechanics.get("material_recommend"), task,
                            mechanics.get("material_spec"))
        task_type = mechanics.get("task_type") or detect_task_type(
            task, state.get("context"))
        spec_text = (task + " " + str(state.get("context") or ""))
        L, W, H, _dim_note = _extract_part_envelope(spec_text, task_type)

        nominal = float(mechanics.get("nominal_load_n") or 500.0)

        # ── 【Bug-15 修复】problem_id 用真实短哈希，可复现且不泄露尺寸 ──
        _seed = "%s|%s|%s" % (task, state.get("created_at") or "",
                              mechanics.get("nominal_load_n"))
        _pid = "GATE_" + _hashlib.sha1(_seed.encode("utf-8")).hexdigest()[:12].upper()

        # ── 【观察点4 修复】多工况载荷 ────────────────────────────────
        # 竞速小车的主要失效模式是过弯侧向力与纵向驱动力，纯 -Z 重力覆盖不到。
        # 保持 load_rated（重力，magnitude_n = 额定载荷，与门禁口径一致），
        # 追加侧向/纵向两个工况，供 physics 做多工况校核。
        _impact = float(mechanics.get("impact_factor") or 1.0)
        _lat = round(nominal * _impact, 3)
        _lon = round(nominal, 3)
        case = {
            "schema_version": "1.0",
            "meta": {
                "problem_id": _pid,
                "title": task[:120] or "门禁自动生成工况",
                "revision": "A",
                "generated_by": "workflow_gate.estimate_mechanics",
                "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "task_type": task_type,
                "task_type_label": mechanics.get("task_type_label"),
                "design_domain_mm": [round(L, 1), round(W, 1), round(H, 1)],
                "design_domain_note": _dim_note,
                "design_domain_basis": "零件包络（Bug-12：不再沿用赛道/整机尺寸）",
            },
            "units": {"length": "mm", "force": "N", "stress": "MPa", "mass": "kg"},
            "design_domain": {
                "bounds": {"x_min": 0.0, "x_max": float(L),
                           "y_min": 0.0, "y_max": float(W),
                           "z_min": 0.0, "z_max": float(H)},
                "keep_in_regions": [], "keep_out_regions": [],
            },
            "material": mat,
            "spatial_selectors": [
                {"id": "fixed_end", "type": "box",
                 "bounds": {"x_min": 0.0, "x_max": max(L * 0.05, 10.0),
                            "y_min": -10.0, "y_max": W + 10.0,
                            "z_min": -10.0, "z_max": H + 10.0},
                 "selection_rule": "faces_intersecting_region"},
                {"id": "load_face", "type": "box",
                 "bounds": {"x_min": max(L * 0.95, L - 10.0), "x_max": L,
                            "y_min": -10.0, "y_max": W + 10.0,
                            "z_min": -10.0, "z_max": H + 10.0},
                 "selection_rule": "faces_intersecting_region"},
            ],
            "boundary_conditions": [
                {"id": "bc_fixed", "spatial_selector_id": "fixed_end",
                 "type": "fixed_displacement",
                 "dof_lock": {"x": True, "y": True, "z": True,
                              "rx": True, "ry": True, "rz": True}},
            ],
            "loads": [
                {"id": "load_rated", "spatial_selector_id": "load_face",
                 "type": "distributed_force",
                 "magnitude_n": nominal,          # ← 额定载荷，与门禁口径一致
                 "direction": [0.0, 0.0, -1.0],
                 "case_name": "额定重力工况",
                 "note": "主工况：magnitude_n 必须等于门禁 nominal_load_n"},
                {"id": "load_lateral", "spatial_selector_id": "load_face",
                 "type": "distributed_force",
                 "magnitude_n": _lat,
                 "direction": [1.0, 0.0, 0.0],
                 "case_name": "侧向工况（过弯侧倾/转向拉杆受拉）",
                 "note": "侧向力 = 额定载荷 × 动载系数 %.2f" % _impact},
                {"id": "load_longitudinal", "spatial_selector_id": "load_face",
                 "type": "distributed_force",
                 "magnitude_n": _lon,
                 "direction": [0.0, 1.0, 0.0],
                 "case_name": "纵向工况（启停驱动力/制动）",
                 "note": "纵向力 = 额定载荷"},
            ],
            "load_cases": [
                {"id": "rated", "load_ids": ["load_rated"],
                 "description": "额定重力工况（主工况）"},
                {"id": "lateral", "load_ids": ["load_lateral"],
                 "description": "侧向工况：过弯侧倾、转向拉杆受拉"},
                {"id": "longitudinal", "load_ids": ["load_longitudinal"],
                 "description": "纵向工况：频繁启停/制动"},
                {"id": "combined",
                 "load_ids": ["load_rated", "load_lateral", "load_longitudinal"],
                 "description": "组合工况：全部载荷同时作用（最恶劣）"},
            ],
            "acceptance": {
                "analysis_type": "linear_static",
                "min_safety_factor": float(mechanics.get("safety_factor") or 2.0),
                "target_safety_factor_max": 5.0,
                "max_displacement_mm": None,
                "min_wall_thickness_mm": None,
                "max_mass_kg": None,
                "must_remain_in_design_domain": True,
                "mesh_quality_min": 0.1,
                # ── 【BUG-02】疲劳/寿命校核输入（physics 疲劳模块读这两项）──
                "design_life_years": float(mechanics.get("design_life_years") or 10.0),
                "operating_cycles_per_year": float(mechanics.get("cycles_per_year") or 200000.0),
                "load_type": mechanics.get("load_type") or "static",
                "impact_factor": _impact,
                "fatigue_check_required": (mechanics.get("load_type") in ("cyclic", "impact")),
                "required_load_cases": ["rated", "lateral", "longitudinal"],
            },
            "optimization": {"primary": "minimize_mass",
                             "secondary": ["minimize_max_displacement"]},
        }
        out_dir = work_dir or os.path.join(BASE_DIR, "load_cases")
        try:
            os.makedirs(out_dir, exist_ok=True)
        except Exception:
            out_dir = BASE_DIR
        path = os.path.join(out_dir, "gate_load_case.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(case, f, ensure_ascii=False, indent=2)
        return {"ok": True, "path": path, "load_case": case}
    except Exception as e:
        return {"ok": False, "error": "写入载荷工况失败: %r" % (e,)}


# ══ 【观察点1/5 修复】交付目录按任务隔离 ═══════════════════════════════════
# 原缺陷：历史任务与新任务共用同一交付目录（实测 C:\Users\j1877\Desktop\test），
#   新任务的同名零件会【直接覆盖】旧任务成果，且无任何提示/备份 ——
#   本轮 DSH_车架底板.SLDPRT(106KB) 覆盖了上一轮同名文件(81KB)，若本次失败，
#   上一轮成果已丢（观察点5）。
# 修复：
#   ① init 时按"任务 ID/时间戳"生成独立交付子目录；
#   ② 目录里写 TASK_INFO.json（任务描述/时间/ID），便于追溯；
#   ③ 同名文件已存在时自动备份 .bak（双保险）；
#   ④ 提供 work-dir 命令查询/设置当前任务目录。
WORKDIR_STATE_KEY = "work_dir"


def _task_id(task, created_at=None):
    """按任务描述 + 创建时间生成稳定的任务 ID（可读 + 唯一）。"""
    import hashlib as _hashlib
    _seed = "%s|%s" % (str(task or ""), str(created_at or ""))
    _h = _hashlib.sha1(_seed.encode("utf-8")).hexdigest()[:8].upper()
    _ts = time.strftime("%Y%m%d_%H%M%S",
                        time.localtime(time.time()))
    return "%s_%s" % (_ts, _h)


def _default_delivery_root():
    """交付根目录：优先环境变量，其次桌面 test，最后工程模式根目录/output。"""
    try:
        env = (os.environ.get("DSH_DELIVERY_ROOT") or "").strip()
        if env:
            return env
    except Exception:
        pass
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME") or ""
    if home:
        cand = os.path.join(home, "Desktop", "test")
        try:
            if os.path.isdir(os.path.join(home, "Desktop")):
                return cand
        except Exception:
            pass
    return os.path.join(BASE_DIR, "output")


def prepare_task_workdir(task, state=None):
    """【观察点1/5】为新任务创建隔离的交付目录。

    Returns: {ok, work_dir, task_id, created, backed_up, info_path, error?}
    """
    out = {"ok": False}
    try:
        _tid = _task_id(task)
        root = _default_delivery_root()
        wd = os.path.join(root, _tid)
        _created = not os.path.isdir(wd)
        os.makedirs(wd, exist_ok=True)
        # 写任务信息（可追溯：哪个目录属于哪个任务）
        _info = {
            "task_id": _tid,
            "task": str(task or "")[:2000],
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "delivery_root": root,
            "note": ("【观察点1/5 修复】每个任务独立交付目录，避免同名零件覆盖上一轮成果。"
                     "上一轮成果保留在同级其它目录中。"),
        }
        info_path = os.path.join(wd, "TASK_INFO.json")
        try:
            with open(info_path, "w", encoding="utf-8") as f:
                json.dump(_info, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        out.update({"ok": True, "work_dir": wd, "task_id": _tid,
                    "created": _created, "info_path": info_path,
                    "delivery_root": root})
        return out
    except Exception as e:
        out["error"] = "创建任务交付目录失败: %r" % (e,)
        return out


def backup_existing(path):
    """【观察点5】同名文件已存在时自动备份为 .bak-<时间戳>（防覆盖丢失）。"""
    try:
        if path and os.path.isfile(path):
            _bak = "%s.bak-%s" % (path, time.strftime("%Y%m%d_%H%M%S"))
            import shutil as _shutil
            _shutil.copy2(path, _bak)
            return {"ok": True, "backup": _bak}
    except Exception as e:
        return {"ok": False, "error": repr(e)}
    return {"ok": False, "reason": "文件不存在，无需备份"}


def cmd_work_dir(new_dir=None):
    """查询 / 设置当前任务的交付目录（观察点1/5）。"""
    state = load_state()
    if new_dir:
        _wd = os.path.abspath(str(new_dir))
        try:
            os.makedirs(_wd, exist_ok=True)
        except Exception as e:
            return {"ok": False, "error": "无法创建目录: %r" % (e,)}
        state[WORKDIR_STATE_KEY] = _wd
        save_state(state)
        return {"ok": True, "work_dir": _wd, "action": "set"}
    _cur = state.get(WORKDIR_STATE_KEY)
    return {"ok": True, "work_dir": _cur,
            "task_id": state.get("task_id"),
            "task": state.get("task"),
            "default_root": _default_delivery_root(),
            "note": ("每个任务应有独立目录（观察点1/5）。"
                     "设置: workflow_gate.py work-dir <绝对路径>")}


def _get_done_rooms():
    ms_path = os.path.join(STATE_DIR, "mode_state.json")
    done = set()
    if os.path.exists(ms_path):
        try:
            with open(ms_path, "r", encoding="utf-8") as f:
                ms = json.load(f)
            for rn, rv in ms.get("rooms", {}).items():
                if rv.get("ended_at"):
                    done.add(rn)
        except Exception:
            pass
    return done


def _rooms_detail():
    """读取 mode_state.json 的 rooms 原始记录（含 ended_at/failed_at/active）。"""
    ms_path = os.path.join(STATE_DIR, "mode_state.json")
    detail = {}
    if os.path.exists(ms_path):
        try:
            with open(ms_path, "r", encoding="utf-8") as f:
                ms = json.load(f)
            detail = ms.get("rooms", {}) or {}
        except Exception:
            pass
    return detail


def _check_wave1_done():
    """【总装门禁核心】检查第一波（全部建模类房间）是否全部完成。

    完成定义：每个建模房间 ended_at 有值且没有 failed_at。
    返回 dict：all_done / not_ended / active / failed —— 任一非空即不允许开总装。
    """
    state = load_state()
    rooms = (state.get("subagent_config") or {}).get("rooms") or _default_rooms()
    detail = _rooms_detail()
    not_ended, active, failed = [], [], []
    for name, rtype in rooms:
        if rtype not in WAVE1_TYPES:
            continue
        info = detail.get(name) or {}
        if info.get("failed_at"):
            failed.append(name)
        elif not info.get("ended_at"):
            (active if info.get("active") else not_ended).append(name)
    return {"all_done": not (not_ended or active or failed),
            "not_ended": not_ended, "active": active, "failed": failed}

def bypass_requested():
    """是否显式要求绕开三大防线（唯一合法绕行，必定留痕）。"""
    import os as _os
    return str(_os.environ.get("DSH_DEFENSE_BYPASS") or "").strip() in ("1", "true", "yes")


def _wave1_room_names():
    """第一波【建模类】房间名列表（用于开总装前的三防线强制校验）。"""
    state = load_state()
    cfg = state.get("subagent_config") or {}
    out = []
    for row in (cfg.get("rooms") or []):
        if isinstance(row, (list, tuple)) and len(row) >= 2:
            if str(row[1]) in WAVE1_TYPES:
                out.append(str(row[0]))
        elif isinstance(row, dict):
            if str(row.get("type") or "") in WAVE1_TYPES:
                out.append(str(row.get("name") or row.get("room")))
    # 兜底：退化为 mode_state 里已登记的房间（排除总装/出图）
    if not out:
        try:
            import json as _json
            _mp = os.path.join(STATE_DIR, "mode_state.json")
            if os.path.exists(_mp):
                with open(_mp, "r", encoding="utf-8") as _f:
                    _ms = _json.load(_f)
                out = [str(n) for n in (_ms.get("rooms") or {})]
        except Exception:
            out = []
    return out


def cmd_confirm_assembly(parts_payload):
    """【三大防线】开总装前强制：第一波各房间必须已通过三防线。

    为什么挂在这里：总装会把所有零件固化成一个装配体，一旦开总装，
      再回头改单个零件就要重做总装 —— 所以这是"最后一个能便宜地拦下
      材料/强度/领域问题"的时刻，必须在这里把三道防线全部卡住。
    """
    if not bypass_requested():
        try:
            import defense_gate as _dg
            _bad = []
            _rooms = _wave1_room_names()
            for _r in _rooms:
                _d = _dg.check_room_defense(_r)
                if not _d["ok"]:
                    _bad.append((_r, _d["blockers"]))
            if _bad:
                _nl = chr(10)
                _lines = ["=== 【三大防线】拦截：第一波房间未通过校验，禁止开启总装 ===", ""]
                for _r, _bs in _bad:
                    _lines.append("房间 [%s]:" % _r)
                    for _b in _bs:
                        _lines.append("   ✗ " + _b)
                _lines += [
                    "",
                    "处理：让对应小屋补齐材料凭据 / 跑 physics-optimize / 跑",
                    "physics-validate-domain 后，重新 room-end 该房间，再调 confirm-assembly。",
                    "（确需放行：设 DSH_DEFENSE_BYPASS=1，会留痕。）",
                ]
                return {
                    "ok": False,
                    "gate": "DEFENSE_REQUIRED",
                    "phase": "confirm-assembly",
                    "blocked_rooms": [x[0] for x in _bad],
                    "error": "【三大防线】第一波房间未通过强制校验，禁止开启总装。",
                    "message": _nl.join(_lines),
                }
        except ImportError:
            pass
        except Exception:
            pass
    elif bypass_requested():
        try:
            import defense_gate as _dg
            _dg.log_bypass("workflow_gate.confirm-assembly",
                           reason="DSH_DEFENSE_BYPASS=1", by="env")
        except Exception:
            pass
    """【总装确认门禁】主对话开『总装与验证』房间前必须调用，代码级强制。

    parts_payload: 主对话汇总的第一波产出零件清单（文件路径+零件名）。
    拒绝条件（任一满足即拒绝，禁止开总装）：
      1. 门禁流程未走完（step != user_selected）
      2. 第一波建模房间未全部 room-end（含仍在运行/未登记/失败待重做）
      3. 未提供零件清单
    """
    state = load_state()
    _step = str(state.get("step") or "")
    # ── 【C2 修复】前置条件放宽：不再死认 step == "user_selected" ──────────
    # 测试反馈：Wave1 三房间都 completed，select 返回 ASSEMBLY_CONFIRM_REQUIRED，
    #   但调用 confirm-assembly 却因 step 不等于 user_selected 被拒 → 流程卡死。
    #   根因是 init 曾把 step 打回 depth_asked（见 C4），而这里的前置判断
    #   只看这一个字段，没有结合"任务实际是否已推进"来判断。
    # 现改为：只要【已选过搭建方式】或【已有房间记录】，即视为门禁已走过
    #   select 阶段，允许进入总装确认（真正的把关交给下面的 _check_wave1_done）。
    _advanced = (_step == "user_selected"
                 or bool(state.get("user_choice"))
                 or bool(state.get("rooms"))
                 or bool(state.get("subagent_config")))
    if not _advanced:
        return {"ok": False, "gate": "ASSEMBLY_CONFIRM_REJECTED",
                "step": _step,
                "error": "门禁流程未完成（先走完 init→provide_context→select），不能确认总装",
                "hint": ("当前 step=%s，且未检测到已选搭建方式/房间记录。"
                         "请先按顺序完成：init → provide_context → select。" % _step)}
    # 若 step 明显不一致（例如被 init 打回），顺手校正回来，避免后续 select 再卡
    if _step != "user_selected":
        state["step"] = "user_selected"
        state["step_repaired_from"] = _step
        state["step_repaired_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        save_state(state)
    chk = _check_wave1_done()
    if not chk["all_done"]:
        return {"ok": False, "gate": "ASSEMBLY_CONFIRM_REJECTED",
                "not_ended": chk["not_ended"], "active": chk["active"], "failed": chk["failed"],
                "message": ("第一波建模房间尚未全部完成，禁止开启总装。"
                            "未完成: %s | 仍在运行: %s | 失败待重做: %s。"
                            "用 mode_gate.py room-status 核对状态，等全部 room-end 后再确认。"
                            % (chk["not_ended"], chk["active"], chk["failed"]))}
    payload = (parts_payload or "").strip()
    if not payload:
        return {"ok": False, "gate": "ASSEMBLY_CONFIRM_REJECTED",
                "message": "零件清单为空。必须汇总第一波全部产出零件（文件路径+零件名）后再确认。"}
    state["assembly_confirmed"] = True
    state["assembly_inputs"] = payload
    state["assembly_confirmed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save_state(state)
    return {"ok": True, "gate": "ASSEMBLY_CONFIRMED",
            "assembly_inputs": payload,
            "message": ("总装确认通过（全部建模房间已完成 + 零件清单已登记）。"
                        "现在调用 select 获取『总装与验证』房间；总装小屋 prompt 必须原样包含"
                        "零件清单，只做装配+干涉检查+整体验证，禁止重新建模/简化任何零件。")}


# ── 第0题：参数需求强度（前置分流题）──────────────────────────────────────
# 【问题2 修复】原方案一上来就抛 17 题，绝大多数用户被吓退或乱答。
# 现改为两段式：先问 1 题判断"参数需求是否强"，再决定问全量还是精简。
#
#   强需求（需要精确出力学校核 / 有明确工况约束 / 要做强度与公差审查）
#       → 问全量 QUESTION_SPEC（17 题，覆盖 99% 工业机械任务）
#   不强（只是想先看到东西 / 结构示意 / 快速原型 / 无严格约束）
#       → 只问 QUESTION_SPEC_LITE（5 题，覆盖决定性维度，其余由门禁取默认值）
PARAM_DEPTH_SPEC = [
    {
        "id": "param_depth",
        "group": "0 参数深度",
        "title": "参数需求强度",
        "hint": "本次设计是否需要严格把关参数（载荷/尺寸/公差/材料都要有据可依）？",
        "options": [
            "强需求：需要严格校核，参数要有设计依据（追问详细信息）",
            "不强：先出大致结构即可，细节由你按经验定（只问关键几项）",
        ],
        "mech": False,
        "depth": True,
    },
]

# ── 精简问题集（参数需求不强时使用）────────────────────────────────────────
# 【问题2 修复】从 QUESTION_SPEC 中精选 5 个"决定性维度"：
#   不给这些就完全无法建模或必错；其余维度门禁都能按任务描述取合理默认值。
#   · mech_type       —— 机构类型（决定整个拓扑结构，不给无法建模）
#   · arm_lengths     —— 臂长/行程（决定尺寸链，不给无法定尺寸）
#   · load_magnitude  —— 额定负载（决定强度校核基准）
#   · load_type       —— 负载类型（决定安全系数取值）
#   · open_extra      —— 开放补充（兜底，防止关键约束没处说）
LITE_QUESTION_IDS = ("mech_type", "arm_lengths", "load_magnitude",
                     "load_type", "open_extra")


def question_spec_lite(task=None, context=None):
    """按精简题 id 从【当前任务类型的问题集】中挑出精简版题目（保持原顺序）。

    【Bug-03 修复】原实现硬绑 QUESTION_SPEC（机械臂口径），做小车任务时
    精简 5 题也仍是"机构类型/臂长行程"的语义。现在改为：
      · 先按任务类型取问题集（车辆模板 → 车辆题目）；
      · 再按该模板的 lite_ids 过滤。
    """
    spec = question_spec_for(task, context)
    ids = lite_ids_for(task, context)
    picked = []
    for q in spec:
        if q.get("id") in ids:
            picked.append(q)
    return picked


def resolve_depth(context_text):
    """从用户对第0题的回答里判断"参数需求强度"。

    返回 "full"（问全量 17 题）或 "lite"（问精简 5 题）。
    判定规则（从严到宽）：
      1) 显式出现"强需求"字样 → full
      2) 显式出现"不强/精简"字样 → lite
      3) 其余（含未答/含糊）→ 默认 lite（宁可少问，可随时补问；
         门禁给出的默认值都会在零件参数表中标注，供后续复核）
    """
    t = str(context_text or "")
    if "强需求" in t or "严格校核" in t:
        return "full"
    if "不强" in t or "精简" in t:
        return "lite"
    return "lite"


# ── 背景问题规格表（单一事实来源 · 全流程统一）──────────────────────────────
# 每一题：id / 组 / 标题 / 提示 / 选项 / 是否力学关键
# 代码、skill 文档、persona 都从这张表生成，确保永远一致。
QUESTION_SPEC = [
    # A 组：结构形态（几何尺寸，直接决定能否建模）
    {"id": "mech_type", "group": "A 结构形态", "title": "机构类型",
     "hint": "几轴？什么机构？", 
     "options": ["6轴关节臂", "4轴/5轴关节臂", "3轴直角坐标(XYZ)", "2轴云台", "四连杆/连杆机构", "丝杠/导轨直线模组", "其他(请说明)"],
     "mech": True},
    {"id": "arm_lengths", "group": "A 结构形态", "title": "臂长/行程",
     "hint": "各段长度或各轴行程，带单位（如 大臂300mm+小臂250mm；或 X600/Y400/Z300）",
     "options": ["你决定", "其他(请说明)"],
     "mech": True},
    {"id": "base_form", "group": "A 结构形态", "title": "基座形式",
     "hint": "固定式/移动式/旋转式？安装方式？",
     "options": ["落地固定式", "壁挂式", "法兰安装", "导轨移动式", "旋转底座", "你决定"],
     "mech": True},
    {"id": "end_effector", "group": "A 结构形态", "title": "末端执行器",
     "hint": "末端形式与接口（夹爪/吸盘/法兰盘/孔径）",
     "options": ["二指夹爪", "吸盘", "标准法兰盘", "卡盘", "焊枪/工具接口", "暂无(仅作结构验证)", "你决定"],
     "mech": True},
    {"id": "envelope", "group": "A 结构形态", "title": "整体包络",
     "hint": "总体外形尺寸上限（长×宽×高）或占地范围",
     "options": ["无严格限制", "你决定", "其他(请说明)"],
     "mech": True},

    # B 组：工况载荷（力学校核的输入）
    {"id": "load_magnitude", "group": "B 工况载荷", "title": "额定负载",
     "hint": "末端需要搬运/承受的重量（N 或 kg，注明单位）",
     "options": ["你决定", "其他(请说明)"],
     "mech": True},
    {"id": "load_type", "group": "B 工况载荷", "title": "负载类型",
     "hint": "动载荷性质，直接影响安全系数与疲劳校核",
     "options": ["静态保持", "平稳动态", "频繁启停/冲击", "往复循环(疲劳)", "振动环境", "你决定"],
     "mech": True},
    {"id": "environment", "group": "B 工况载荷", "title": "使用场合",
     "hint": "环境条件决定材料与防护等级",
     "options": ["普通室内", "户外(日晒雨淋)", "潮湿/水下", "高温(>150℃)", "低温(<-20℃)", "粉尘环境", "洁净室/食品级", "你决定"],
     "mech": True},
    {"id": "lifespan", "group": "B 工况载荷", "title": "设计寿命",
     "hint": "目标使用寿命（年）或动作次数",
     "options": ["1年以内(短周期)", "3~5年", "10年", "20~30年(长期)", "你决定"],
     "mech": True},

    # C 组：驱动与运行（选型依据）
    {"id": "drive", "group": "C 驱动与运行", "title": "驱动方式",
     "hint": "动力来源与传动形式",
     "options": ["伺服电机+减速机", "步进电机", "直流有刷/无刷", "液压", "气动", "手动", "你决定"],
     "mech": False},
    {"id": "duty", "group": "C 驱动与运行", "title": "工作制度",
     "hint": "运行频率与占空比",
     "options": ["连续运行(24/7)", "间歇运行(每天数小时)", "偶尔使用", "单次动作", "你决定"],
     "mech": False},
    {"id": "precision", "group": "C 驱动与运行", "title": "精度要求",
     "hint": "定位/重复精度，决定配合公差与加工等级",
     "options": ["一般(±1mm)", "较高(±0.1mm)", "精密(±0.01mm)", "无要求(仅结构验证)", "你决定"],
     "mech": False},

    # D 组：制造与边界条件
    {"id": "material_pref", "group": "D 制造条件", "title": "材料倾向",
     "hint": "材料偏好或限制（如必须轻量化/必须防腐）",
     "options": ["Q235碳钢", "45#钢/40Cr", "304/316不锈钢", "6061-T6铝", "工程塑料", "无偏好(由你选)", "你决定"],
     "mech": False},
    {"id": "manufacturing", "group": "D 制造条件", "title": "加工方式",
     "hint": "打算怎么造，影响结构工艺性",
     "options": ["数控加工(CNC)", "焊接结构", "铸造", "钣金折弯", "3D打印", "外购标准件为主", "你决定"],
     "mech": False},
    {"id": "budget", "group": "D 制造条件", "title": "成本/重量倾向",
     "hint": "在成本、重量、强度之间的取舍",
     "options": ["成本优先(经济方案)", "重量优先(轻量化)", "强度/可靠性优先", "均衡", "你决定"],
     "mech": False},

    # E 组：特殊要求（自由补充）
    {"id": "special", "group": "E 特殊要求", "title": "特殊要求",
     "hint": "防水/防尘/防爆/轻量化/认证标准等硬性要求",
     "options": ["无特殊要求", "防水防尘(IP等级)", "防爆", "轻量化", "需符合某国标/行业标准", "你决定"],
     "mech": False},

    # ★ 开放式收尾：确保覆盖表单之外的一切
    {"id": "open_extra", "group": "Z 开放补充", "title": "其他补充（开放式）",
     "hint": "以上没覆盖到的任何要求、约束、参考案例或偏好，请自由填写；"
             "没有可写「无」",
     "options": [],   # 纯填空，不给选项
     "mech": False, "open": True},
]


def _format_question_block(num, q):
    """把一条问题规格渲染成给用户看的文本块。"""
    lines = ["[%s] %d. 【%s】%s" % (q.get("group", ""), num, q.get("title", ""),
                                   q.get("hint", ""))]
    opts = q.get("options") or []
    if opts:
        lines.append("     可选：" + " / ".join(opts))
    else:
        lines.append("     （开放题：请直接填写，无内容可写「无」）")
    return chr(10).join(lines)


def question_spec_for_prompt(task=None, context=None):
    """供子代理 prompt 注入的精简版（保证小屋也按同一套维度设计）。

    【Bug-03 修复】按任务类型取问题集 —— 小车任务注入的是车辆维度。
    """
    out = []
    for q in question_spec_for(task, context):
        opts = q.get("options") or []
        out.append("- %s：%s%s" % (
            q.get("title", ""), q.get("hint", ""),
            ("  可选: " + " / ".join(opts)) if opts else "  （开放填写）"))
    return out


def _ask_questions(task_desc, depth="lite", context=None):
    """门禁背景问题集（v4 · 两段式）。

    【问题2 修复】不再一上来就抛 17 题。分两段：
      第一段（depth=None 或 "ask"）：只问 1 题 —— 「参数需求强度」
      第二段（depth="full"）：问全量 QUESTION_SPEC（17 题）
      第二段（depth="lite"）：问精简 question_spec_lite()（5 题）

    设计目标：
      1. 【一致性】全流程只有这一份问题定义——代码、skill 文档、persona 全部引用它，
         杜绝"有时问5个、有时问10个"的分裂。
      2. 【覆盖度】强需求时分组穷举关键维度，覆盖 99% 工业机械任务；
         不强时只问决定性 5 项，其余由门禁给默认值。
      3. 【开放式收尾】两种深度末尾都必带 open 一题，让用户补充遗漏要求。

    返回纯文本（供模型原样呈现给用户）。
    """
    nl = chr(10)

    # ── 第一段：只问"参数需求强度"这一题 ──────────────────────────────
    if depth in (None, "ask", "ask_depth"):
        return nl.join([
            "=== 设计任务背景确认（第 1 步 / 共 2 步：先确认参数需求强度）===",
            "任务描述: %s" % task_desc,
            "",
        ] + [
            "%s" % _format_question_block(idx + 1, q)
            for idx, q in enumerate(PARAM_DEPTH_SPEC)
        ] + [
            "",
            "说明：这一题决定接下来问多少细节 ——",
            "  · 选「强需求」→ 会再问 17 题（载荷/尺寸/公差/材料/制造全覆盖），",
            "    每个零件的参数都必须有量化设计依据，适合真要出图、真要校核的场合。",
            "  · 选「不强」  → 只再问 5 个关键项（机构类型/臂长行程/额定负载/",
            "    负载类型/开放补充），其余维度由门禁按任务描述取合理默认值，",
            "    并在零件参数表中标注出来供你复核。适合先看结构、快速原型。",
            "",
            "请回答这一题后，门禁会给出对应的第二段问题。",
        ])

    # ── 第二段：按深度给题（【Bug-03】按任务类型取问题集）──────────────
    _key, _tpl = task_template(task_desc, context)
    if depth == "full":
        spec = question_spec_for(task_desc, context)
    else:
        spec = question_spec_lite(task_desc, context)
    total = len(spec)
    _tpl_note = ("" if _key in ("generic", "robot_arm")
                 else "（已按任务类型【%s】自动加载对应题目模板）" % _tpl.get("label"))
    header = ("=== 设计任务背景确认（第 2 步 / 共 2 步 · 参数需求【强】"
              "· 共 %d 题，请逐题作答；不确定的可写「你决定」）===" % total) \
        if depth == "full" else \
        ("=== 设计任务背景确认（第 2 步 / 共 2 步 · 参数需求【不强】"
         "· 精简 %d 题；不确定的可写「你决定」）===" % total)
    header = header + _tpl_note
    body = [
        header,
        "任务描述: %s" % task_desc,
        "",
    ] + [
        "%s" % _format_question_block(idx + 1, q)
        for idx, q in enumerate(spec)
    ]
    if depth == "full":
        body += [
            "",
            "注：标「你决定」的题，门禁会按任务描述给出合理默认值，",
            "    但默认值会在每个零件的参数表中明确标注，供你复核后再确认。",
        ]
    else:
        body += [
            "",
            "注：本次为精简模式 —— 未问到的维度（基座形式/末端执行器/使用场合/",
            "    设计寿命/驱动方式/精度/材料/加工方式/成本倾向等）由门禁按任务描述",
            "    取合理默认值，并会在每个零件的参数表中标注出来，你可随时要求补充。",
            "    如需完整 17 题，请重新发起并选择「强需求」。",
        ]
    return nl.join(body)


def _extract_structure_spec(context_text):
    """【B4修复】从用户对「A组·结构形态」的回答中提取几何规格。

    这些尺寸会注入每个建模小屋的 prompt，避免子代理无据建模。
    """
    import re as _re
    spec = {"axes": None, "arm_lengths": None, "base_type": None,
            "end_effector": None, "envelope": None, "raw": context_text or ""}
    if not context_text:
        return spec
    text = str(context_text)
    # 1) 轴数
    m = _re.search(r"(\d+)\s*轴", text)
    if m:
        spec["axes"] = int(m.group(1))
    # 2) 臂长/行程（抓 数字+mm）
    dims = _re.findall(r"(\d+(?:\.\d+)?)\s*(?:mm|毫米|MM)", text)
    if dims:
        spec["arm_lengths"] = [float(d) for d in dims[:8]]
    # 3) 基座形式
    for kw in ("落地", "壁挂", "法兰", "导轨", "移动式", "固定式", "旋转式"):
        if kw in text:
            spec["base_type"] = kw
            break
    # 4) 末端执行器
    for kw in ("夹爪", "吸盘", "法兰盘", "卡盘", "焊枪"):
        if kw in text:
            spec["end_effector"] = kw
            break
    # 5) 整体包络
    env = _re.search(r"(\d+)\s*[x×*]\s*(\d+)(?:\s*[x×*]\s*(\d+))?", text)
    if env:
        spec["envelope"] = env.group(0)
    return spec


def _room_parts_scope(task, context=None):
    """根据任务给每个房间划分明确的零件范围（防止小屋越界做别的房间的活）。

    【Bug-05 修复】原实现是硬编码的机械臂口径（大臂/小臂/关节/齿条），
    做竞速小车时 4 个房间的零件范围全部语义错位，必须人工改写。
    现在按任务类型模板取零件范围；车辆任务得到
    车架底板/车轮/车轴/舵机连杆/底壳/上罩…，不再是"大臂/小臂"。
    """
    _key, tpl = task_template(task, context)
    scope = tpl.get("room_scope")
    if isinstance(scope, dict):
        # 补齐模板未覆盖的房间类型（用通用表兜底）
        merged = dict(_GENERIC_ROOM_SCOPE)
        merged.update(scope)
        return merged
    return dict(_GENERIC_ROOM_SCOPE)


# 通用（机械臂/其它）房间零件范围 —— 模板未覆盖时的兜底表
_GENERIC_ROOM_SCOPE = {
    "structural": "大臂、小臂、连杆、立柱、横梁等臂部/骨架结构件",
    "transmission": "关节、齿轮、齿条、轴、轴承座、电机座、联轴器等传动件",
    "housing": "底座、外壳、护罩、机架、安装板等支撑/壳体件",
    "assembly": "总装以上全部零件 + 干涉检查 + 整体验证",
    "drafting": "对总装体出工程图（三视图+标注+DWG/PDF）",
    "support": "轴承座、支撑架等辅助支撑件",
    "spring": "弹簧等弹性元件",
    "thermal": "耐热/隔热部件",
    "corrosion": "防腐处理件",
}


def _default_rooms():
    return [("结构件","structural"),("传动机构","transmission"),
            ("壳体机架","housing"),("总装与验证","assembly"),("工程图输出","drafting")]

def generate_subagent_config(choice, parallel_mode, task, context=None):
    t = (task + " " + (context or "")).lower()
    rooms = list(_default_rooms())
    if any(k in t for k in ["轴承","支撑","bearing","support"]):
        rooms.append(("支撑结构","support"))
    if any(k in t for k in ["弹簧","spring","弹性"]):
        rooms.append(("弹性元件","spring"))
    if any(k in t for k in ["高温","heat"]):
        rooms.append(("耐热部件","thermal"))
    if any(k in t for k in ["腐蚀","corrosion","锈蚀"]):
        rooms.append(("防腐处理","corrosion"))
    # ══ 【Bug-08 修复】A 模式（完全自主）的确认协议必须显式声明 ═══════════
    # 台账待观察项：A 模式 ask_user_at=[]，但没人说清"是否彻底跳过确认"，
    #   以及 mode_gate 的 confirm-part 在 A 模式下是否仍被强制调用导致卡住。
    # 明确规则（代码即契约）：
    #   · A 模式【不要求】逐零件人工确认 —— 小屋可直接建模；
    #   · confirm-part 授权令牌【仅 C 模式强制】，A/B 模式下 params_confirmed
    #     不校验令牌（避免 A 模式被令牌机制卡死）；
    #   · 但【参数表仍必须输出】并写入报告，供用户事后复核（自主 ≠ 无据）。
    strategies = {
        "A": {"parallel": True, "ask_user_at": [], "detail_level": "full",
              "confirmation_required": False,
              "confirm_part_token_required": False,
              "note": ("完全自主：小屋可直接建模，无需逐零件人工确认；"
                       "但必须在报告里输出带量化设计依据的【零件参数表】。")},
        "B": {"parallel": True, "ask_user_at": ["material_selection"],
              "detail_level": "key_points",
              "confirmation_required": False,
              "confirm_part_token_required": False,
              "note": ("部分自主：关键节点（材料选择）询问，其余自主；"
                       "同样需输出零件参数表。")},
        "C": {"parallel": False, "ask_user_at": ["every_part"], "detail_level": "every_step",
              "confirmation_required": True,
              "confirm_part_token_required": True,
              "confirmation_protocol": CONFIRMATION_PROTOCOL_C,
              "note": ("步步确认：每个零件必须单独向用户提问并取得授权令牌"
                       "（mode_gate.py confirm-part）后才能上报 params_confirmed。")},
    }
    # 【用户明确要求：不要回退】C 模式 + 并行策略 D 必须被尊重，不得自动降级为 E。
    # 旧版（B3）认为"C 要逐零件阻塞确认"与"并行多小屋"冲突，于是强制改成 sequential。
    # 但实际机制已经支持该组合：
    #   · 每个小屋的提问彼此独立，各自持有自己的 pendingId
    #   · 前端选项卡按 seq 排队，用户逐个作答即可（不存在"无法串行提问"）
    #   · SW 使用权由 mode_gate 的 FIFO 进程级锁自动排队，不会互抢
    # 因此这里只按用户原始选择设置策略，不做任何隐式改写。
    if parallel_mode == "sequential":
        strategies[choice]["parallel"] = False
    result = {"rooms": rooms, "strategy": strategies[choice],
              "parallel_mode": parallel_mode, "total_rooms": len(rooms),
              # ══ 【Bug-19 修复】小屋重试上限（防止单零件卡死拖住整波）══════
              # 台账现象：结构件房间某零件反复失败重试，room-status 仍是 running，
              #   没有"失败 N 次就跳过/降级/上报"的策略，单个零件卡死会拖住整波，
              #   波 1 永远无法 room-end → 总装/出图全部卡住。
              # 明确策略（写进小屋 prompt，可被代码检查）：
              #   · 单零件最多重试 3 次（retry_limit_per_part）；
              #   · 超过后：① 跳过该零件继续下一个；② 或降级为简化几何；
              #             ③ 必须 room-report <房间> failed '<零件名> 重试3次失败'；
              #   · 房间级软超时 room_timeout_min：超过后必须上报 failed 交主对话决策，
              #     由主对话决定 room-fail 回退还是换新小屋重做。
              "retry_policy": {
                  "retry_limit_per_part": 3,
                  "on_exceed": ["跳过该零件并 room-report failed",
                                "降级为简化几何（如圆角代替复杂曲面）",
                                "上报主对话请求人工介入"],
                  "room_timeout_min": 45,
                  "note": ("【Bug-19】单零件重试上限 3 次；房间软超时 45 分钟。"
                           "超限必须 room-report failed 并交主对话决策，"
                           "禁止无限重试拖死整波。"),
              }}
    return result

def _sync_mode_gate(choice, parallel_mode):
    """把门禁状态同步到 mode_gate.py（模式 + 并行策略）。

    ── 【Bug1 修复】原实现只调 declare 2，【完全丢掉了 parallel_mode 参数】──
    后果：select 返回 parallel 后 mode_state.json 里 parallel_mode 恒为 None，
      sw-status 只能实时读 workflow_state 推断 → 两个文件口径不一致，
      锁判定（_lock_needed / _detect_gate_room / --room 强制）随之不稳。
    修复：declare 之后显式调用 set-parallel-mode，把 D/E 落盘为权威值。
    """
    _cnw = 0x08000000 if sys.platform == "win32" else 0
    try:
        import subprocess as _sp
        _sp.run([sys.executable, MODE_GATE_PATH, "declare", "2"],
                capture_output=True, text=True, encoding="utf-8",
                timeout=15, creationflags=_cnw)
    except Exception:
        pass
    # ── 【Bug1 修复】并行策略落盘（D→parallel / E→sequential）──
    if parallel_mode:
        _strict = PARALLEL_CHOICES.get(parallel_mode, {}).get("mode", parallel_mode)
        try:
            import subprocess as _sp
            _sp.run([sys.executable, MODE_GATE_PATH, "set-parallel-mode", str(_strict)],
                    capture_output=True, text=True, encoding="utf-8",
                    timeout=15, creationflags=_cnw)
        except Exception:
            pass

def _reset_mode_rooms():
    """【bug5修复】新任务开始时清空上一任务的房间残留。

    否则 mode_state.json 里旧任务的 ended_at 会让 _get_done_rooms() 把旧房间
    误判为已完成，select 直接返回 finished=true 但实际一个房间都没创建。
    """
    try:
        import subprocess as _sp
        _sp.run([sys.executable, MODE_GATE_PATH, "rooms-reset"],
                capture_output=True, text=True, encoding="utf-8", timeout=15,
                creationflags=0x08000000 if sys.platform == "win32" else 0)
    except Exception:
        pass


# ══ 【残留检测】子对话残留体检（开始前 / 结束前各跑一次）═══════════════════
#
# 【用户要求 · 本次新增】"上个对话的小屋还是有东西留下来干扰这个新的对话。
#   加个检测：在开始之前和结束之前，都要自己测一场是否有子对话在项目栏中的。"
#
# ── 为什么必须做（测试部 Bug#1 / Bug#6 的同源根因）──────────────────────
#   上一轮对话残留的 mode_state（房间/subagent）与 workflow_state
#   （step=user_selected）会跨对话存活，造成：
#     · 新任务 init 被 IN_PROGRESS 拦截，无法重开门禁（Bug#1）；
#     · select 把上一轮的 ended_at 误判为本轮完成 → 假 finished（Bug#6）；
#     · whoami 拿到上一波的死 subagent_id → 提问投递给死会话（Bug#5）。
#
# ── 设计原则 ───────────────────────────────────────────────────────────
#   · 【开始前】init 调用：auto_clean=True —— 残留会直接挡住新任务，
#     必须自动清掉才能开工（用户已确认"自动清理，仅打印日志"）。
#   · 【结束前】归档前调用：auto_clean=False —— 此时只做体检与报告，
#     若有活着的房间则明确拒绝归档（防止"还有子对话在跑就宣告完成"）。
#   · 一律【不杀活着的子代理】；force 仅由用户显式要求时使用。
def _residue_check(auto_clean=False, force=False):
    """调用 mode_gate.py residue-check，返回其 JSON 结果（失败时给降级结果）。"""
    args = [sys.executable, MODE_GATE_PATH, "residue-check"]
    if auto_clean:
        args.append("--clean")
    if force:
        args.append("--force")
    try:
        import subprocess as _sp
        out = _sp.run(args, capture_output=True, text=True, encoding="utf-8",
                      timeout=30,
                      creationflags=0x08000000 if sys.platform == "win32" else 0)
        return json.loads(out.stdout or "{}")
    except Exception as e:
        return {"ok": False, "error": "residue-check 调用失败: %r" % (e,),
                "has_residue": False, "alive": [], "counts": {}}


def cmd_init(task_desc):
    """【问题2 修复】门禁第一步只问 1 题（参数需求强度），不再一次抛 17 题。

    ── 【C4 修复】对"进行中的任务"加保护，防止误调 init 把流程打回起点 ──
    测试反馈原文：误调一次 init 把 step 打回 depth_asked，
      导致 17 题被重复问、且再也回不到总装确认态（因为 confirm-assembly
      要求 step==user_selected，而 step 已被改成 depth_asked）→ 流程卡死。
    修复策略：
      · 若当前任务已走过 select（step 属于 user_selected/mechanics_done 等
        "已推进"状态），或已有房间记录 → 判定为"进行中"。
      · 此时【拒绝】init，除非显式传 --force（或调用方明确要重来）。
      · 拒绝时返回当前状态与正确的前进方式，让调用方知道该调什么。
    """
    state = load_state()
    _step_now = str(state.get("step") or "")
    # ── 【僵尸门禁修复】区分"真进行中"与"僵尸残留" ────────────────────────
    # 测试反馈：init 被 INIT_BLOCKED_IN_PROGRESS 挡住，但 rooms 为空、零产出，
    #   卡在"既不能续跑也不能重开"。
    # 根因有二：
    #   ① 原判定查的是 workflow_state 里的 rooms（通常恒空），
    #      而僵尸房间其实记在【mode_state.json】里 —— 判断依据错位；
    #   ② 没有任何"僵尸特征"识别：房间标 active 但 subagents 为空、
    #      且长时间无心跳/无产出，显然不是真在跑，却被当成进行中。
    #
    # 新判定：
    #   · 真进行中 = 门禁处于推进态 + 确实有【活跃证据】
    #   · 僵尸残留 = 只有 step 残留，或房间 active 但无子代理/无心跳/无产出
    #   僵尸一律【允许】init 重开（这才是它该走的路），并顺带清理。
    _ADVANCED = ("providing", "mechanics_done", "user_selected", "finished")

    # 读取 mode_state 的真实房间情况（这才是权威来源）
    _ms_rooms = {}
    try:
        _ms_path = os.path.join(STATE_DIR, "mode_state.json")
        if os.path.exists(_ms_path):
            with open(_ms_path, "r", encoding="utf-8") as _mf:
                _ms_rooms = (json.load(_mf) or {}).get("rooms") or {}
    except Exception:
        _ms_rooms = {}
    _ms_subs = {}
    try:
        _ms_path2 = os.path.join(STATE_DIR, "mode_state.json")
        if os.path.exists(_ms_path2):
            with open(_ms_path2, "r", encoding="utf-8") as _mf2:
                _ms_subs = (json.load(_mf2) or {}).get("subagents") or {}
    except Exception:
        _ms_subs = {}

    _active_rooms = [r for r, v in _ms_rooms.items() if v.get("active")]
    # 僵尸特征：有活动房间，但【没有任何子代理登记】→ 根本没拉起子代理
    _zombie = bool(_active_rooms) and not _ms_subs

    _has_choice = bool(state.get("user_choice"))
    _has_asm = bool(state.get("assembly_confirmed"))
    _step_advanced = _step_now in _ADVANCED

    # ── 【残留检测 · 开始前】Bug#1 根因修复 ──────────────────────────────
    # 【用户要求】"在开始之前……都要自己测一场是否有子对话在项目栏中的"。
    #
    # 原缺陷：新对话发起任务时，上一轮残留的 step=user_selected 会让
    #   _in_progress 判定为真 → 返回 INIT_BLOCKED_IN_PROGRESS →
    #   用户被卡在"既不能续跑也不能重开"，只能手改 json（Bug#1 实测现象）。
    #
    # 修复：在"进行中"判定【之前】先跑一次残留体检并自动清理。
    #   · 若残留里【没有任何活着的房间】→ 判定为上一轮遗留，自动清掉
    #     （清 rooms/subagents/锁/门禁推进态），新任务得以正常 init；
    #   · 若确实【有活着的子对话】→ 不清理、不误杀，仍按"进行中"拦截，
    #     并在返回里明确告知是哪个房间在跑，避免误伤正在干活的任务。
    _residue = None
    _residue_cleaned = False
    _alive_live = []          # 【残留检测】当前确实活着的子对话（任何情况下都不得无视）
    _force_now = ("--force" in sys.argv)
    try:
        _pre = _residue_check(auto_clean=False)
        _alive_pre = _pre.get("alive") or []
        _alive_live = _alive_pre
        _needs_clean = bool(_pre.get("has_residue")) or bool(
            _pre.get("gate_residue"))
        # 只在"无活房间"时自动清 —— 有活房间说明真在进行中，不能动
        if _needs_clean and not _alive_pre and not _force_now:
            _residue = _residue_check(auto_clean=True)
            _residue_cleaned = True
            # 清理后重新载入门禁状态，避免用过期的内存副本继续判定
            state = load_state()
            _step_now = str(state.get("step") or "")
            _has_choice = bool(state.get("user_choice"))
            _has_asm = bool(state.get("assembly_confirmed"))
            _step_advanced = _step_now in _ADVANCED
            # mode_state 已被清空，僵尸判定依据随之失效
            _ms_rooms = {}
            _ms_subs = {}
            _active_rooms = []
            _zombie = False
        elif _needs_clean and _alive_pre:
            # 有活着的子对话 → 记录但不清理，交由下面的判定拦截
            _residue = _pre
    except Exception:
        _residue = None

    # ── 【Bug#1 补强 · 活子对话硬拦】────────────────────────────────────
    # 上一步只在"有残留"时才清理；但如果上一轮的房间【确实还活着】
    #   （心跳新鲜/平台侧刚登记），has_residue 为 False、step 可能还是 idle，
    #   此时 _in_progress 判定不成立 → 新任务被放行 → 两个任务同时抢 SW API。
    # 这是一条真实的缺口（测试用例 TEST4 暴露）。
    # 修复：只要有活着的房间且未加 --force，一律拒绝 init，并明确告知是谁在跑。
    if _alive_live and not _force_now:
        return {
            "ok": False,
            "gate": "INIT_BLOCKED_ALIVE_SUBAGENTS",
            "step": _step_now,
            "alive_subagents": [x.get("detail") or x.get("room") for x in _alive_live],
            "alive_count": len(_alive_live),
            "error": ("【开始前残留检测】检测到 %d 个仍在活动的子对话/房间，"
                      "拒绝为同一工作区开启新任务。" % len(_alive_live)),
            "message": ("上一轮任务的房间仍在运行（有新鲜心跳/登记），"
                        "若此时 init 会与它们抢占 SolidWorks 与门禁状态。"),
            "hint": ("· 等它们跑完 → mode_gate.py room-status 核对后 room-end；"
                     "· 确认它们已无响应 → 用 interrupt_agent 停掉小屋，"
                     "再调 mode_gate.py residue-check --clean 清理；"
                     "· 确实要强行重开 → workflow_gate.py init \"<任务\" --force"),
        }

    # ══ 【Bug-02 修复】上一轮已 finished 且无残留 → 自动重置，不再拒绝 ══════
    # 原缺陷：上一轮任务走完收尾后，workflow_state 仍保留 step=finished 与
    #   user_choice/parallel_mode；用户想做【下一个任务】时首次 init 被
    #   INIT_BLOCKED_IN_PROGRESS 拒绝，提示"任务已在推进中" ——
    #   但上一轮其实已经彻底结束（无活房间、无残留子代理）。
    #   用户只能靠手改 json 或猜出 reset 才能继续（实测 Bug-02 现象）。
    #
    # 修复：finished 是【明确的终态】——它本身就表示"上一轮已结束"。
    #   只要 finished 且【没有活着的子对话】，init 直接把它当作"新任务开始"，
    #   自动完成任务级状态清理（等价于自动 reset + residue-check），
    #   并在返回里透明告知"已自动归档上一轮"。这样用户无需知道 reset 的存在。
    _prev_finished = (_step_now == "finished")
    _auto_new_task = False
    if _prev_finished and not _alive_live and not _force_now:
        _auto_new_task = True
        # 归档上一轮的可审计信息（清理前快照）
        _prev_archive = {
            "prev_step": _step_now,
            "prev_task": state.get("task"),
            "prev_finished_at": state.get("finished_at"),
            "prev_user_choice": state.get("user_choice"),
            "prev_parallel_mode": state.get("parallel_mode"),
            "prev_rooms": sorted(_ms_rooms.keys()),
        }
        # 清任务级状态：房间/子代理/锁/心跳/报告 + TASK_FINISHED 标记
        try:
            _reset_mode_rooms()
        except Exception:
            pass
        try:
            _mk = os.path.join(STATE_DIR, "TASK_FINISHED.json")
            if os.path.exists(_mk):
                os.remove(_mk)
        except Exception:
            pass
        # ── 重新载入并刷新全部判定变量（清理后状态已变）─────────────────
        # 【Bug-02 修复·关键】必须把 workflow_state 里的【任务级字段】也清掉，
        #   否则 _step_now 仍为 finished、_has_choice 仍为 True，
        #   下面的 _in_progress 判定照样成立 → 依旧返回 INIT_BLOCKED_IN_PROGRESS
        #   （这正是首次实现没生效的原因：只清了 mode_state，没清 workflow_state）。
        state = load_state()
        for _k in ("user_choice", "choice_detail", "parallel_mode",
                   "subagent_config", "finished_at", "assembly_confirmed",
                   "assembly_inputs", "assembly_confirmed_at", "param_depth",
                   "depth_answer", "mechanics_result", "context", "load_case_file"):
            state.pop(_k, None)
        save_state(state)
        state = load_state()
        _step_now = str(state.get("step") or "")
        _has_choice = bool(state.get("user_choice"))
        _has_asm = bool(state.get("assembly_confirmed"))
        _step_advanced = _step_now in _ADVANCED
        _ms_rooms = {}
        _ms_subs = {}
        _active_rooms = []
        _zombie = False
        _prev_finished = False

    # 只有"推进态 + 有真实进度痕迹"才算进行中；纯僵尸不算
    _in_progress = (_step_advanced and (_has_choice or _has_asm or bool(_ms_rooms))) \
                   and not _zombie
    _force = _force_now

    if (_in_progress or _zombie) and not _force:
        if _zombie:
            # 僵尸态：明确告知可安全重开，并给出清理指引
            return {
                "ok": False,
                "gate": "ZOMBIE_DETECTED",
                "step": _step_now,
                "active_rooms": sorted(_active_rooms),
                "subagents": sorted(_ms_subs.keys()),
                "user_choice": state.get("user_choice"),
                "error": ("检测到【僵尸任务残留】：房间标记 active 但没有任何子代理登记，"
                          "说明上一轮的房间从未真正拉起子代理（零产出）。"),
                "message": ("这不是进行中的任务，可以安全重开。"
                            "直接执行 workflow_gate.py reset 清空残留，"
                            "或加 --force 强制重开: workflow_gate.py init \"<任务\" --force"),
                "hint": ("· 推荐：先 reset（清房间/僵尸记录/锁），再 init 重新开始；"
                         "· 或：init --force 由本命令自动完成清理。"),
            }
        _alive_detail = [x.get("detail") or x.get("room")
                         for x in ((_residue or {}).get("alive") or [])]
        return {
            "ok": False,
            "gate": "INIT_BLOCKED_IN_PROGRESS",
            "step": _step_now,
            "user_choice": state.get("user_choice"),
            "parallel_mode": state.get("parallel_mode"),
            "assembly_confirmed": bool(state.get("assembly_confirmed")),
            "active_rooms": sorted(_active_rooms),
            # 【残留检测】把"到底是谁在跑"讲清楚，便于判断该等还是该清
            "alive_subagents": _alive_detail,
            "error": ("检测到【进行中的任务】，已拒绝 init 以免把流程打回起点。"),
            "message": ("当前 step=%s，任务已在推进中（有活动房间与子代理）。"
                        "init 会重置 step 为 depth_asked，导致重复提问且无法回到总装确认态。"
                        % _step_now),
            "hint": ("· 想继续原任务 → 调 workflow_gate.py status 查看进度，"
                     "或 workflow_gate.py select 继续下一波；"
                     "· 若确认上一轮已结束（子对话都停了）→ 调 "
                     "mode_gate.py residue-check --clean 清残留后再 init；"
                     "· 确实要推翻重开 → 调 workflow_gate.py reset（先清空），"
                     "或显式加 --force: workflow_gate.py init \"<任务\" --force")
        }

    # ── 【BUG-12 修复】不再【无条件】强清残留 ────────────────────────────
    # 原缺陷：无论什么情况，init 末尾都硬调 _reset_mode_rooms()，
    #   把上一轮的房间/subagent/锁/心跳/报告全部抹掉。后果：
    #     · 用户只是想"重新走一遍门禁"（或误调 init），上一轮的完成记录被毁，
    #       造成"数据凭空消失"、无法审计、也无法 resume；
    #     · 与前面精心设计的 IN_PROGRESS / ALIVE 保护自相矛盾 ——
    #       前面刚判定"可以重开"，后面又把一切清空，保护形同虚设。
    # 修复策略（最小破坏）：
    #   · 【先】抓取清理前快照（必须在任何 reset 之前，否则审计信息失真）；
    #   · 没有残留 → 完全不动 mode_state（纯新任务，零副作用）；
    #   · 有残留且【无活房间】→ 才清理，并把"清了什么"透明回传；
    #   · 有活房间 → 前面已拦截返回，根本走不到这里。
    _pre_reset_snapshot = {}
    _need_reset = False
    try:
        _ms_now = {}
        _msp = os.path.join(STATE_DIR, "mode_state.json")
        if os.path.exists(_msp):
            with open(_msp, "r", encoding="utf-8") as _mrf:
                _ms_now = json.load(_mrf) or {}
        _rooms_now = _ms_now.get("rooms") or {}
        _subs_now = _ms_now.get("subagents") or {}
        _lk_now = _ms_now.get("sw_lock") or {}
        _hist_now = _ms_now.get("subagent_history") or []
        _pre_reset_snapshot = {
            "rooms": sorted(_rooms_now.keys()),
            "subagents": sorted(_subs_now.keys()),
            "has_lock_owner": bool(_lk_now.get("owner")),
            "queue": _lk_now.get("queue") or [],
            "history_count": len(_hist_now),
        }
        # 只要还有房间/子代理/历史/锁残留，才需要清
        _need_reset = bool(_rooms_now or _subs_now or _hist_now
                           or _lk_now.get("owner") or (_lk_now.get("queue") or []))
    except Exception:
        _need_reset = False
    # ── 僵尸态 + --force：也纳入统一清理（快照已在上面抓取）──────────────
    if _zombie and _force:
        _need_reset = True
    if _need_reset:
        try:
            _reset_mode_rooms()
        except Exception:
            pass
    state["step"] = "depth_asked"
    state["task"] = task_desc
    state["created_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    # ── 【观察点1/5 修复】为新任务创建隔离的交付目录 ─────────────────────
    # 原缺陷：新旧任务共用同一交付目录 → 同名零件直接覆盖上一轮成果且无提示。
    _wd_info = prepare_task_workdir(task_desc, state)
    if _wd_info.get("ok"):
        state["task_id"] = _wd_info.get("task_id")
        state[WORKDIR_STATE_KEY] = _wd_info.get("work_dir")
    state["assembly_confirmed"] = False
    state["assembly_inputs"] = None
    state["finished_at"] = None
    state["param_depth"] = None
    save_state(state)
    # 【Bug3】新任务开始 → 清除"任务已完成"标记，
    # 否则上一任务的完成标记会让守卫在本任务中错误放行。
    try:
        _mk = os.path.join(STATE_DIR, "TASK_FINISHED.json")
        if os.path.exists(_mk):
            os.remove(_mk)
    except Exception:
        pass
    return {"ok": True, "step": "depth_asked", "task": task_desc,
            # ── 【固定两问】此处只登记参数深度题；A/B/C 与 D/E 在
            #   provide_context 末段以固定两问给出（见 choice_contract）。
            "message": _ask_questions(task_desc, depth="ask",
                                      context=(state.get("context") or "")),
            # ── 【残留检测】把"开始前清掉了什么"透明地报给用户 ──────────
            "residue_cleaned": bool(_residue_cleaned or _need_reset),
            "residue_actions": ((_residue or {}).get("actions") or []) if _residue_cleaned else (
                (["【BUG-12】init 清理上一轮 mode_state 残留"] if _need_reset else [])),
            # 【BUG-12】把清理前快照回传，做到"清可审计、没清也可确认"
            "mode_state_reset": bool(_need_reset or _auto_new_task),
            "mode_state_snapshot_before_reset": _pre_reset_snapshot,
            # ── 【观察点1/5 修复】新任务的隔离交付目录 ────────────────────
            "work_dir": state.get(WORKDIR_STATE_KEY),
            "task_id": state.get("task_id"),
            "work_dir_note": (
                ("【观察点1/5 修复】已为本任务创建独立交付目录 %s —— "
                 "同名零件不会覆盖上一轮成果；如需改到别处，用 "
                 "workflow_gate.py work-dir <绝对路径>。"
                 % (state.get(WORKDIR_STATE_KEY) or "（创建失败，沿用默认）"))
                if _wd_info.get("ok") else
                ("⚠️ 交付目录创建失败（%s），将沿用默认目录 —— "
                 "注意同名零件可能覆盖上一轮成果。" % _wd_info.get("error"))),
            # ── 【Bug-02 修复】上一轮 finished 后自动开新任务（无需手动 reset）──
            "previous_task_archived": bool(_auto_new_task),
            "previous_task_snapshot": (_prev_archive if _auto_new_task else None),
            "auto_new_task_note": (
                "上一轮任务已处于 finished 终态，已【自动归档】其任务级状态"
                "（房间/子代理/锁/心跳/报告 + TASK_FINISHED 标记），本次 init "
                "直接作为新任务开始 —— 无需手动 reset（Bug-02）。"
                if _auto_new_task else None),
            "mode_state_note": (
                ("已清理上一轮残留：房间 %s / 子代理 %s / 锁持有者 %s"
                 % (_pre_reset_snapshot.get("rooms"),
                    _pre_reset_snapshot.get("subagents"),
                    _pre_reset_snapshot.get("has_lock_owner")))
                if _need_reset else
                "无上一轮残留，未改动 mode_state（本次 init 零副作用）"),
            "residue_note": (
                ("⚠️ 开始前检出并清理了上一轮对话残留（%d 项操作）：%s。"
                 "这原本会导致 init 被 IN_PROGRESS 拦截（Bug#1）。"
                 % (len((_residue or {}).get("actions") or []),
                    "；".join(((_residue or {}).get("actions") or [])[:6])))
                if _residue_cleaned else
                ("开始前残留检测通过：未检出上一轮子对话/任务残留。"
                 if _residue is not None else
                 "开始前残留检测未执行（mode_gate 不可用，已跳过）。")),
            "next_command": ("请先把第 0 题（参数需求强度）问给用户；用户回答后调用：\n"
                             "  python \"<工程模式根目录>\\tools\\workflow_gate.py\" "
                             "provide_context \"<第0题回答原文>\"\n"
                             "⚠️ 本环境没有 workflow-gate-* 这类 DSH 工具，"
                             "真实入口就是上面这条命令行（BUG-03）。")}

def _choice_qs_fallback():
    """choice_contract 不可用时的兜底题面（与固定两问保持一致）。"""
    return [
        {"id": "build_mode", "question": "请选择搭建方式（A/B/C）",
         "options": [{"label": "A"}, {"label": "B"}, {"label": "C"}]},
        {"id": "parallel_mode", "question": "请选择并行策略（D/E）",
         "options": [{"label": "D"}, {"label": "E"}]},
    ]


def cmd_provide_context(context_text):
    """【问题2 修复】两段式状态机。

    第一段：step=depth_asked，收到的是对【第 0 题】的回答
        → 判定 depth（full/lite）→ step=context_asked → 返回第二段问题
    第二段：step=context_asked，收到的是对【第二段全部问题】的回答
        → 力学估算 → step=mechanics_done → 返回 A/B/C + D/E 选项
    """
    state = load_state()
    step = state.get("step")
    nl = chr(10)

    # ── 第一段：判定参数需求深度，返回第二段问题 ──────────────────────
    if step == "depth_asked":
        # ── 【问题1 修复】第0题由模型用系统问题问；此处据其回答定深度 ──
        depth = resolve_depth(context_text)
        state["param_depth"] = depth
        state["depth_answer"] = context_text
        # 用第0题答案一并参与力学估算的关键词匹配（"大型/重型"等可能写在这里）
        state["context"] = context_text or ""
        state["step"] = "context_asked"
        save_state(state)
        total = (len(question_spec_for(state.get("task"), state.get("context")))
                 if depth == "full"
                 else len(question_spec_lite(state.get("task"), state.get("context"))))
        # ── 【问题1 修复】登记"第二段问题必须用系统问题提问" ────────────
        _spec2 = (question_spec_for(state.get("task"), state.get("context"))
                  if depth == "full"
                  else question_spec_lite(state.get("task"), state.get("context")))
        # ── 【固定两问】末段一并给出 A/B/C 与 D/E 的固定问题 ────────────
        _fixed_qs = _cc.questions_payload() if _cc is not None else _choice_qs_fallback()
        return {
            "ok": True,
            "step": "context_asked",
            "param_depth": depth,
            "param_depth_label": "强需求(全量)" if depth == "full" else "不强(精简)",
            "question_count": total,
            # ── 【固定两问】参数题由门禁给出；A/B/C 与 D/E 在末段固定问 ──
            "mandatory_contract": {
                "enforced": bool(_cc is not None),
                "stage": "context",
                "required_questions": total,
                "must_use": "ask_user_question",
                "rule": ("把下面 %d 题用系统问题问给用户；"
                         "禁止用正文文字代替提问。" % total),
            },
            "message": nl.join([
                "已识别参数需求强度: 【%s】→ 接下来问 %d 题。"
                % ("强需求" if depth == "full" else "不强", total),
                "",
                _ask_questions(state["task"], depth=depth,
                               context=state.get("context")),
            ]),
            "next_command": ("请把上述 %d 题问给用户；用户回答后再次调用：\n"
                             "  python \"<工程模式根目录>\\tools\\workflow_gate.py\" "
                             "provide_context \"<第二段全部回答原文>\"\n"
                             "⚠️ 是命令行脚本，不是 DSH 工具（BUG-03）。" % total),
        }

    # ── 第二段：收齐背景信息，做力学估算 ─────────────────────────────
    if step != "context_asked":
        return {"ok": False, "error": "请先调用 init 命令！（当前 step=%s）" % step}
    # 第0题答案 + 第二段答案拼在一起，保证力学估算能看到全部关键词
    merged = ((state.get("depth_answer") or "") + " " + (context_text or "")).strip()
    state["context"] = merged
    state["mechanics_result"] = estimate_mechanics(state["task"], merged)
    state["step"] = "mechanics_done"
    # ── 【BUG-07 修复】把力学估算落盘为 physics 可直接读取的载荷工况 ──────
    # 门禁与 physics 从此共用同一份数值（额定载荷 nominal_load_n 写进
    # magnitude_n），彻底消除"门禁 1000N / physics 100N"的口径分裂。
    _lc = write_load_case_file(state, state["mechanics_result"])
    state["load_case_file"] = _lc.get("path") if _lc.get("ok") else None
    save_state(state)
    m = state["mechanics_result"]
    depth = state.get("param_depth")
    msg = nl.join([
        "力学估算完成（参数需求: %s）:" % ("强需求" if depth == "full" else "不强/精简"),
        "  额定载荷: %s N   （来源: %s）"
        % (m.get("nominal_load_n"), m.get("load_source")),
        "  动载系数: ×%.1f（负载性质: %s） → 设计等效载荷: %s N"
        % (m.get("impact_factor", 1.0), m.get("load_type"),
           m.get("design_load_n")),
        "  安全系数: %.1f   材料: %s" % (m["safety_factor"], m["material_recommend"]),
        "  设计寿命: %.0f 年   动作频次: %.0f 次/年"
        % (m.get("design_life_years") or 0, m.get("cycles_per_year") or 0),
        "",
        "请选择搭建方式 A/B/C 和并行策略 D/E：",
        "  A = 完全自主搭建    B = 部分自主（关键节点询问）    C = 步步确认（逐零件确认）",
        "  D = 部分小屋并行（按波次分批启动）",
        "  E = 单小屋串联（一次一个房间，完成后开下一个）",
        "输入示例：C, D",
    ])
    # ── 【BUG-07】口径一致性声明：让调用方明确知道"用哪个数" ────────────
    if _lc.get("ok"):
        msg += nl + nl + nl.join([
            "【载荷工况已落盘 · 门禁与 physics 共用同一数值】",
            "  文件: %s" % _lc["path"],
            "  physics 校核必须用该文件（或 magnitude_n = %s N），"
            % m.get("nominal_load_n"),
            "  严禁另起一套数值 —— 这正是 BUG-07『门禁 1000N / physics 100N』的根因。",
            "  疲劳/寿命参数（%d 年 / %.0f 次/年）已写入 acceptance 字段，"
            % (m.get("design_life_years") or 0, m.get("cycles_per_year") or 0),
            "  physics 疲劳模块会据此做 S-N + Miner 累积损伤校核。",
        ])
    else:
        msg += nl + nl + "⚠️ 载荷工况落盘失败: %s（请手工确保 physics 使用额定载荷 %s N）" % (
            _lc.get("error"), m.get("nominal_load_n"))
    # ── 【BUG-01 修复】粗估必须显著提示，避免把估算值当用户给定值 ────────
    if m.get("load_is_estimate"):
        msg += nl + nl + nl.join([
            "⚠️【载荷为估算值 · 必须复核】",
            "  用户输入中未检出明确载荷数值，门禁按关键词取了基准值 %s N。"
            % m.get("nominal_load_n"),
            "  请务必向用户确认实际载荷，再以确认值重算（否则强度校核基准不可信）。",
        ])
    if depth != "full":
        msg += nl + nl + nl.join([
            "提示：本次为【精简参数模式】，未问到的维度由门禁按任务描述取默认值，",
            "      并会在每个零件的参数表中标注。若后续发现参数不够，可随时补充，",
            "      C 模式（步步确认）下每个零件的参数表都会重新展示供你复核。",
        ])
    # ── 【固定两问】A/B/C 与 D/E 由代码固定，模型必须原样询问 ────────────
    # 这两问是【两个独立问题】：分两次问、或一次问两题都合法。
    # 旧规则用 max_per_call=1 强制"每次只问一题"，把正常的
    #   "连着问两次"也纳入题数校验，经常误判违规 —— 已按用户要求移除。
    _fixed_qs = _cc.questions_payload() if _cc is not None else [
        {"id": "build_mode", "question": "请选择搭建方式（A/B/C）",
         "options": [{"label": "A"}, {"label": "B"}, {"label": "C"}]},
        {"id": "parallel_mode", "question": "请选择并行策略（D/E）",
         "options": [{"label": "D"}, {"label": "E"}]},
    ]
    return {"ok": True, "step": "mechanics_done", "task": state["task"],
            "param_depth": depth, "mechanics": m,
            # ── 【固定两问】机器契约：问题与选项由代码固定 ──────────────
            "mandatory_questions": _fixed_qs,
            "mandatory_contract": {
                "enforced": bool(_cc is not None),
                "stage": "choice",
                "mode": "by-question-id",
                "required_questions": 2,
                "must_use": "ask_user_question",
                "rule": ("必须用系统问题询问【搭建方式 A/B/C】与【并行策略 D/E】；"
                         "这两问是两个独立问题 —— 分两次问或一次问两题都可以，",
                         "不存在\"合并即违规\"的判定。"),
            },
            # 【BUG-07】把落盘路径与数值契约显式回传，便于自动化校验一致性
            "load_case_file": state.get("load_case_file"),
            "load_case_written": bool(_lc.get("ok")),
            "load_case_error": (None if _lc.get("ok") else _lc.get("error")),
            "numeric_contract": {
                "nominal_load_n": m.get("nominal_load_n"),
                "design_load_n": m.get("design_load_n"),
                "impact_factor": m.get("impact_factor"),
                "design_life_years": m.get("design_life_years"),
                "cycles_per_year": m.get("cycles_per_year"),
            },
            "message": msg}

def _pre_finish_residue_guard():
    """【残留检测 · 结束前】归档 finished 之前的最后一道体检。

    【用户要求】"在……结束之前，都要自己测一场是否有子对话在项目栏中的"。

    ── 为什么必须在归档前查 ─────────────────────────────────────────────
      `_archive_finished` 会写 step=finished + TASK_FINISHED.json，
      此后守卫放行、select 拒绝再调用 —— 是【不可逆】的终态。
      若此刻磁盘上还有子对话登记/房间标着 active，说明"任务其实没完"，
      一旦归档就会：
        · 让仍在跑的小屋失去门禁跟踪（它再上报也无人接收）；
        · 让守卫误放行，用户看到"活干完了"但实际零件缺失。
      因此归档前必须确认：没有任何活着的房间。

    Returns:
        (ok: bool, info: dict)  ok=False 表示【禁止归档】。
    """
    res = _residue_check(auto_clean=False)
    if not res.get("ok"):
        # 体检本身失败（mode_gate 不可用）→ 不阻断归档，避免把流程卡死
        return True, {"checked": False, "reason": "residue-check 不可用，已跳过"}
    alive = res.get("alive") or []
    if alive:
        return False, {
            "checked": True,
            "alive": [x.get("detail") or x.get("room") for x in alive],
            "count": len(alive),
        }
    return True, {"checked": True, "alive": [], "count": 0}


def _archive_finished(state, done_count, total, message):
    # ══ 【三大防线 · 交付前总闸】══════════════════════════════════════
    # 用户要求：三道防线必须在"合适的时刻强制发挥作用"。而【宣告任务完成】
    #   是不可逆的终态 —— 一旦写 TASK_FINISHED.json，守卫就放行、对话就结束。
    #   因此这是最后、也是最重要的一道强制点：任何房间三防线未通过，
    #   一律拒绝归档，退回"继续干活"。
    if not bypass_requested():
        try:
            import defense_gate as _dg
            _dt = _dg.check_task_defense()
            if not _dt["ok"]:
                _nl2 = chr(10)
                _ls = ["=== 【三大防线】交付前总闸拦截：任务未通过强制校验 ===", ""]
                for _b in _dt["blockers"][:24]:
                    _ls.append("  ✗ " + _b)
                if len(_dt["blockers"]) > 24:
                    _ls.append("  …（其余 %d 项见 defense_gate.py check-task）"
                               % (len(_dt["blockers"]) - 24))
                _ls += [
                    "",
                    "任务【不得】宣告完成。请先补齐：",
                    "  ① 材料：零件重新赋材并 save()（生成 .material.json 凭据）；",
                    "  ② 物理：对每个建模房间跑 physics-optimize --room <房间>；",
                    "  ③ 领域：对每个建模房间跑 physics-validate-domain --room <房间>；",
                    "然后用 mode_gate.py room-end 重新收尾各房间，再调 select。",
                    "",
                    "（确需放行：设 DSH_DEFENSE_BYPASS=1，会留痕到 reports/defense_bypass.json）",
                ]
                return {
                    "ok": False,
                    "gate": "DEFENSE_REQUIRED",
                    "phase": "finish",
                    "defense": {"blockers": _dt["blockers"][:24],
                                "warnings": _dt["warnings"][:12],
                                "room_count": _dt["room_count"]},
                    "error": "【三大防线】交付前总闸未通过，拒绝宣告完成。",
                    "message": _nl2.join(_ls),
                }
        except ImportError:
            pass
        except Exception:
            pass
    else:
        try:
            import defense_gate as _dg
            _dg.log_bypass("workflow_gate.finish",
                           reason="DSH_DEFENSE_BYPASS=1", by="env")
        except Exception:
            pass
    """【bug4修复】任务全部完成：状态机收敛到 finished，一次性给出收尾协议。

    归档后 select 不再返回任何可执行内容——这是防止'循环交代结果'的代码级闸门。
    """
    # ── 【残留检测 · 结束前】归档是不可逆终态，先确认没有活着的子对话 ────
    _nl = chr(10)
    _ok_fin, _fin_info = _pre_finish_residue_guard()
    if not _ok_fin:
        return {"ok": False, "gate": "RESIDUE_ACTIVE_ON_FINISH",
                "step": state.get("step"),
                "alive_subagents": _fin_info.get("alive"),
                "alive_count": _fin_info.get("count"),
                "error": ("【禁止归档】结束前残留检测发现仍有 %d 个子对话/房间处于活动状态，"
                          "任务并未真正结束。" % _fin_info.get("count", 0)),
                "message": _nl.join([
                    "=== 结束前残留检测：仍有子对话在跑，拒绝宣告完成 ===", "",
                    "仍在活动的房间/子对话:",
                ] + ["  · " + str(x) for x in (_fin_info.get("alive") or [])] + [
                    "",
                    "处理方式（二选一）:",
                    "  1) 等它跑完 → 调 mode_gate.py room-status 核对，"
                    "完成后 room-end 该房间，再调 select；",
                    "  2) 确认它已无响应 → 调 mode_gate.py room-status 看是否 stale，"
                    "必要时 interrupt_agent 停掉小屋后 room-fail 该房间。",
                    "",
                    "⚠️ 若确认这些只是残留登记（小屋其实已停），可执行 "
                    "mode_gate.py residue-check --clean 清理后再调 select。",
                ]),
                }
    state["step"] = "finished"
    state["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    state["assembly_confirmed"] = False
    save_state(state)
    # ── 【Bug3 修复】写一个显式的"任务已完成"标记文件 ──────────────────
    # 用途：dsh-engineering-ui 的 turn-stopping 守卫读它来决定【是否放行结束】。
    # 原缺陷：守卫只判断"是不是工程模式会话"，不看任务是否完成 → AI 已经交付
    #   工件、走完收尾，仍被反复 steer 拉回来继续干，用户看到"活干完了还自说自话"。
    # 现在守卫一旦看到这个标记就立即放行，彻底解决"该结束却结束不了"。
    try:
        marker = os.path.join(STATE_DIR, "TASK_FINISHED.json")
        with open(marker, "w", encoding="utf-8") as f:
            json.dump({
                "finished": True,
                "finished_at": state["finished_at"],
                "completed_count": done_count,
                "total_rooms": total,
                "message": message,
            }, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return {"ok": True, "step": "finished", "finished": True,
            "pending_rooms": [],
            "completed_count": done_count, "total_rooms": total,
            "final_directives": [
                "收尾协议（只做一遍，做完就停）：",
                "1) mode_gate.py room-status 确认全部房间 completed；配合 list_agents 对照，残余小屋 interrupt_agent 清理",
                "2) todo_write 把全部任务标 completed",
                "3) update_goal(action=complete) 结束『完成工图』目标（不做这步会导致 goal 自动续轮、反复交代）",
                "4) 向用户做一次性总结：零件清单/图纸文件/验证结果/文件路径",
                "5) 停止输出。禁止再调用 select，禁止重复交代结果。",
                "   注意：之后的『上下文注入/压缩』是系统压缩历史消息的提示，不是让你重新汇报的信号，忽略即可。",
            ],
            "message": message}


def _parse_choice_args(raw_args):
    """【BUG-03 修复】解析 select 的搭建方式(A/B/C)与并行策略(D/E)。

    原缺陷：文档示例写 `select "C, E"`（一个参数含逗号），
      而实现只认 `sys.argv[2]='C'`、`sys.argv[3]='E'` 两个独立参数，
      照文档执行必然报 "选择必须是 A, B, 或 C！" —— 文档与实现打架。

    修复：接受以下【全部】写法（兼容历史文档与自然输入）：
        select C E          select C, E        select "C, E"
        select C,E          select C ；E       select C;E
        select --choice C --parallel E
        select c e          （大小写不敏感）

    Returns: (choice, parallel) —— 未给出时 parallel 为 None
    """
    import re as _re
    toks = []
    i = 0
    while i < len(raw_args):
        a = str(raw_args[i])
        # --choice/--parallel 显式命名
        if a in ("--choice", "-c") and i + 1 < len(raw_args):
            toks.append(("choice", str(raw_args[i + 1])))
            i += 2
            continue
        if a in ("--parallel", "--pmode", "-p") and i + 1 < len(raw_args):
            toks.append(("parallel", str(raw_args[i + 1])))
            i += 2
            continue
        if a.startswith("--choice="):
            toks.append(("choice", a.split("=", 1)[1]))
            i += 1
            continue
        if a.startswith("--parallel="):
            toks.append(("parallel", a.split("=", 1)[1]))
            i += 1
            continue
        # 一个 token 里可能塞了 "C, E" / "C/E" / "C E"
        for piece in _re.split(r"[,;，；/\s]+", a):
            piece = piece.strip()
            if piece:
                toks.append((None, piece))
        i += 1

    choice, parallel = None, None
    for kind, val in toks:
        up = val.strip().upper()
        if kind == "choice":
            choice = up
        elif kind == "parallel":
            parallel = up
        elif up in CHOICES:
            if choice is None:
                choice = up
            elif parallel is None and up not in CHOICES:
                parallel = up
        elif up in PARALLEL_CHOICES:
            if parallel is None:
                parallel = up
        else:
            # 字母 A~E 之外的 token：若还没定位到 choice 就当 choice（交给上层报错）
            if choice is None:
                choice = up
    return choice, parallel


def cmd_select(choice, parallel=None):
    state = load_state()
    # 【bug4修复】已收尾的任务再次调用 select = 循环交代的源头，代码级拦截
    if state.get("step") == "finished":
        return {"ok": False, "gate": "ALREADY_FINISHED", "step": "finished",
                "finished_at": state.get("finished_at"),
                "message": ("任务已于 %s 收尾归档。禁止再次调用 select、禁止重复交代结果——"
                            "这是防循环铁闸。若确要开始新任务，先调 workflow_gate.py reset 再走门禁。"
                            % state.get("finished_at", "未知时间"))}
    # 串行模式允许重复调用 select 获取下一个房间
    _pmode_val = PARALLEL_CHOICES.get(state.get("parallel_mode", ""), {}).get("mode", "")
    if state.get("step") == "user_selected" and _pmode_val in ("sequential", "parallel"):
        pass
    elif state.get("step") != "mechanics_done":
        return {"ok": False, "error": "请先完成力学估算！"}
    # ── 【固定两问】不再校验"系统提问账本" ──────────────────────────────
    # 用户反馈：账本机制实用价值低且反复造成流程卡顿（记录不全就被判定"没问"），
    #   已整体移除 —— 包括宿主侧的写入（tools/post-execute）与这里的核对。
    # 现在两个固定问题（搭建方式 A/B/C、并行策略 D/E）仍是代码固定题面，
    #   但强制点落在【select 参数必须合法】：A/B/C 与 D/E 由下方校验把关。
    # 【bug5修复】仅本次是该任务的第一条 select（mechanics_done → user_selected）时
    # 兜底清房间残留；后续波次推进的 select 绝不能再清，否则 room-end 记录被抹掉、
    # 波次永远卡在第1波
    _first_select = (state.get("step") == "mechanics_done")
    choice = choice.upper().strip()
    if choice not in CHOICES:
        return {"ok": False, "error": "选择必须是 A, B, 或 C！"}
    if parallel is None:
        parallel = "D"
    parallel = parallel.upper().strip()
    if parallel not in PARALLEL_CHOICES:
        return {"ok": False, "error": "并行模式必须是 D 或 E！"}
    config = CHOICES[choice]
    pconfig = PARALLEL_CHOICES[parallel]
    if _first_select:
        _reset_mode_rooms()
    state["step"] = "user_selected"
    state["user_choice"] = choice
    state["choice_detail"] = config
    state["parallel_mode"] = parallel
    _pmode = PARALLEL_CHOICES.get(parallel, {}).get("mode", parallel)
    state["subagent_config"] = generate_subagent_config(choice, _pmode, state["task"], state.get("context"))
    # 【用户要求：不要回退】不再做任何隐式降级写回。
    # 用户选了 D 就按 D 调度（并行模式会自动启动 SW 监控进程做 FIFO 排队）。
    save_state(state)
    _sync_mode_gate(choice, parallel)

    # 并行模式：自动启动 SW 后台监控进程
    if _pmode == "parallel":
        import subprocess as _sp
        try:
            _sp.Popen([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "mode_gate.py"), "sw-monitor-bg"],
                    creationflags=0x08000000 if sys.platform == "win32" else 0)
        except Exception as _e:
            pass

    rooms = state["subagent_config"]["rooms"]
    # 【修复值体系混用】state["parallel_mode"] 存的是用户选项 'D'/'E'，
    # 而 subagent_config 里是 'parallel'/'sequential'。旧代码只查 PARALLEL_CHOICES
    # （键为 D/E），当值是 'parallel' 时查不到，仅靠兜底参数侥幸正确。
    # 这里显式同时接受两种写法，避免任何一条路径解析错。
    _raw_pmode = state.get("parallel_mode", "parallel")
    if _raw_pmode in ("parallel", "sequential"):
        pmode = _raw_pmode
    else:
        pmode = PARALLEL_CHOICES.get(_raw_pmode, {}).get("mode", _raw_pmode)
    nl = chr(10)

    if pmode == "sequential":
        done = _get_done_rooms()
        pending = [r for r in rooms if r[0] not in done]
        if not pending:
            return _archive_finished(state, len(done), len(rooms),
                                     "所有 %d 个房间已全部完成！执行收尾协议（只做一遍）。" % len(rooms))
        next_room = pending[0]
        # 【总装确认门禁】串行模式同样强制：轮到总装房间时必须先提交确认
        if next_room[1] == "assembly" and not state.get("assembly_confirmed"):
            return {"ok": False, "gate": "ASSEMBLY_CONFIRM_REQUIRED",
                    "pending_rooms": [{"name": next_room[0], "type": next_room[1]}],
                    "ready_to_build": False,
                    "message": nl.join([
                        "=== 【总装确认门禁】拦截：下一个房间是『总装与验证』，禁止直接开启 ===", "",
                        "强制流程（不可跳过）:",
                        "  步骤1: 调 mode_gate.py room-status 核对——全部建模房间必须为 completed；",
                        "         配合 list_agents 对照小屋真实状态，已停止的小屋用 interrupt_agent 清理",
                        "  步骤2: 汇总全部产出零件清单（文件路径+零件名）",
                        '  步骤3: 调 workflow_gate.py confirm-assembly "<零件清单>" 提交确认',
                        "  步骤4: 确认通过（gate=ASSEMBLY_CONFIRMED）后再调 select 获取总装房间", "",
                        "⚠️ 未确认前创建总装小屋 = 违规；代码已拦截。",
                    ])}
        done_count = len(done)
        total = len(rooms)
        msg = nl.join([
            "=== 门禁确认 | 串行模式 ===", "",
            "搭建方式: %s | 并行: 单小屋串行" % config["label"],
            "进度: %d/%d 个房间" % (done_count, total), "",
            "--- 当前待处理房间 ---",
            "房间名: [%s]" % next_room[0],
            "房间类型: %s" % next_room[1], "",
            "--- 执行步骤（严格按顺序）---",
            '  步骤1: python mode_gate.py room-start "%s"' % next_room[0],
            '  步骤2: subagent_fork(description="%s")' % next_room[0],
            "  步骤3: 等待该小屋完成",
            '  步骤4: python mode_gate.py room-end "%s"' % next_room[0],
            '  步骤5: 重新调用 workflow_gate.py select %s %s 获取下一个房间' % (choice, parallel),
            "",
            "严禁一次性创建所有小屋！必须逐个完成。",
            "步骤1~4 全部完成后，再调用 select 获取下一个房间。",
        ])
        pending_rooms = [{"name": next_room[0], "type": next_room[1]}]
        wave_num, wave_name = 0, "串行逐个"
    else:
        # ===== 并行模式：三波式推进（波内同时启动，波间必须等上一波全部完成）=====
        done = _get_done_rooms()
        waves = [
            (1, "独立爆发期", ["structural", "transmission", "housing", "support", "spring", "thermal", "corrosion"]),
            (2, "收敛汇合期", ["assembly"]),
            (3, "出图期", ["drafting"]),
        ]
        # ── 【Bug#19 修复】判 finished 前必须确认总装/出图波次"确实存在且已完成" ──
        # 测试反馈：三房间 completed 后门禁直接 step=finished，跳过总装与出图，
        #   导致磁盘总装仍是上一轮的遗留（带未修正 K=0.24 FAIL）、本轮 0 张新图纸。
        # 根因：原实现在"找不到 pending 波次"时【无条件】判 finished，
        #   没有区分两种情况：
        #     (a) 所有波次（含总装/出图）都真的做完了 → 应当 finished；
        #     (b) subagent_config 里【压根没有】总装/出图房间 → 不应 finished，
        #         而应报错提示配置缺失（否则任务在"没交付工图"的情况下被宣告完成）。
        # 修复：先算出"配置里应该有哪些波次"，再看它们是否都在 done 里；
        #   有缺失则明确报错并给出修复指引，绝不静默 finished。
        _configured_waves = []
        for _wn, _wname, _wtypes in waves:
            if any(r[1] in _wtypes for r in rooms):
                _configured_waves.append((_wn, _wname, _wtypes))
        _missing_waves = []
        for _wn, _wname, _wtypes in _configured_waves:
            _wr = [r for r in rooms if r[1] in _wtypes]
            if not _wr:
                continue
            _all_done = all(
                ((r[0] in done) or bool((_rooms_detail().get(r[0]) or {}).get("ended_at")))
                for r in _wr)
            if not _all_done:
                _missing_waves.append((_wn, _wname, [r[0] for r in _wr]))
        # 若配置里【缺少总装或出图房间】，这是配置缺陷，必须拦住
        _has_asm = any(r[1] == "assembly" for r in rooms)
        _has_dft = any(r[1] == "drafting" for r in rooms)
        if not _has_asm or not _has_dft:
            return {
                "ok": False,
                "gate": "CONFIG_MISSING_FINAL_WAVES",
                "has_assembly": _has_asm,
                "has_drafting": _has_dft,
                "configured_rooms": [r[0] for r in rooms],
                "error": ("门禁配置缺少【总装】或【出图】房间，禁止判为完成。"
                          "任务必须走到工图交付才算结束。"),
                "hint": ("当前 subagent_config.rooms = %s。"
                         "正常应包含「总装与验证」(assembly) 与「工程图输出」(drafting)。"
                         "请重新执行 select 以重建配置，或检查 subagent_config 是否被误清空。"
                         % ([r[0] for r in rooms],)),
            }
        cur = None
        for wnum, wname, wtypes in waves:
            wave_rooms = [r for r in rooms if r[1] in wtypes]
            # ── 【C3 修复】pending 判定必须"以房间名与 mode_state 对账" ──────
            # 原条件: (r[0] not in done and r[1] not in done)
            #   done 集合装的是【房间名】(如 结构件)，而 r[1] 是【类型名】
            #   (如 structural)。类型名永远不可能出现在房间名集合里，
            #   所以 "r[1] not in done" 恒为 True —— 这是一个无效条件，
            #   掩盖了真正的判定，也让"已完成房间"的过滤变得脆弱。
            # 现改为：只看房间名是否已完成，并额外核对 mode_state 的
            #   active/ended 实况，双源一致才认定"该房间已完成"。
            pending = []
            for r in wave_rooms:
                _rn = r[0]
                _info = _rooms_detail().get(_rn) or {}
                _finished = (_rn in done) or bool(_info.get("ended_at")) or bool(_info.get("failed_at"))
                _still_active = bool(_info.get("active")) and not _info.get("ended_at")
                if (not _finished) or _still_active:
                    pending.append(r)
            if pending:
                cur = (wnum, wname, pending)
                break
        if cur is None:
            # ── 【Bug#19 修复】判 finished 前做最后一道校验 ──────────────────
            # 只有【所有已配置波次】都确实完成，才允许归档为 finished。
            # _missing_waves 非空说明还有波次没做完（如总装未 room-end），
            #   此时不能 finished，必须给出明确指引。
            if _missing_waves:
                _mv = []
                for _wn, _wname, _names in _missing_waves:
                    _mv.append("第%d波[%s]: %s" % (_wn, _wname, "、".join(_names)))
                return {
                    "ok": False,
                    "gate": "WAVE_INCOMPLETE",
                    "missing_waves": _missing_waves,
                    "completed_rooms": sorted(done),
                    "error": "仍有波次未完成，禁止归档为 finished。",
                    "message": nl.join([
                        "以下波次尚未完成（可能磁盘已有旧产物，但门禁未登记 room-end）:",
                    ] + ["  · " + x for x in _mv] + [
                        "",
                        "必须依次做完：Wave1 建模 → Wave2 总装(需 confirm-assembly) → Wave3 出图。",
                        "每一步完成后都要调 mode_gate.py room-end <房间名>。",
                    ]),
                }
            return _archive_finished(state, len(done), len(rooms),
                                     "所有 %d 个房间已全部完成！汇总交付，执行收尾协议（只做一遍）。"
                                     "（只有看到本消息才能宣告任务完成，其他任何情况都禁止说已完成）" % len(rooms))
        wnum, wname, pending = cur
        pending_rooms = [{"name": r[0], "type": r[1]} for r in pending]
        wave_num, wave_name = wnum, wname
        if wnum == 1:
            msg = nl.join([
                "=== 门禁确认 | 部分小屋并行 · 第%d波【%s】===" % (wnum, wname), "",
                "本波 %d 个房间【同时启动】: %s" % (len(pending), " ".join("[%s]" % r[0] for r in pending)), "",
                "--- 每个房间的零件范围（小屋 prompt 必须写明，严禁越界）---",
            ] + [("  [%s] 只做: %s" % (r[0], _room_parts_scope(state.get("task", ""), state.get("context")).get(r[1], "按房间名含义判断"))) for r in pending] + [
                "--- 执行步骤 ---",
                "  步骤1: 对每个房间分别调用 mode_gate.py room-start <房间名>",
                "  步骤2: 在同一条回复里连续调用 subagent_fork 一次性创建本波全部小屋！",
                "       每个小屋的 prompt 必须包含: 房间名 + 上面写明的零件范围 + 『只做本房间零件，禁止做其他房间的零件』",
                "       + 进度上报协议: 每个关键节点调 mode_gate.py room-report <房间名> <阶段> <详情>",
                "         (阶段: registered/params_shown/params_confirmed/modeling/part_done/validating/done/failed)",
                "  ⚠️ 严禁只创建一个小屋！本波房间一个都不能少，必须同时启动！",
                "  ⚠️ 各小屋的 SW 调用自带互斥锁自动排队（FIFO 先到先用），无需人工协调",
                "  ⚠️ 小屋允许调用的 mode_gate.py 命令仅限: room-report / room-heartbeat（进度上报通道）；",
                "     room-start / room-end / sw-* / declare / check 等编排命令由大屋独占，小屋禁止调用。",

                "  ⚠️ 小屋内建模必须写单个脚本用 sw_bridge.py run 一次性执行多步操作（一次调用规划多步）",
                "  步骤3: 每个小屋完成后调用 mode_gate.py room-end <房间名>（会自动释放SW锁）",
                "  步骤4: 本波全部 room-end 后，重新调用 select %s %s 获取下一波" % (choice, parallel),
            ])
        elif wnum == 2:
            # 【总装确认门禁】代码级强制：主对话未提交确认前，绝不放行总装房间
            if not state.get("assembly_confirmed"):
                return {"ok": False, "gate": "ASSEMBLY_CONFIRM_REQUIRED",
                        "wave": 2, "wave_name": wname,
                        "pending_rooms": pending_rooms,
                        "ready_to_build": False,
                        "message": nl.join([
                            "=== 【总装确认门禁】拦截：禁止直接开启『总装与验证』 ===", "",
                            "第一波建模房间已全部 room-end，但主对话尚未完成『开总装前确认』。", "",
                            "强制流程（不可跳过）:",
                            "  步骤1: 调 mode_gate.py room-status 核对——全部建模房间必须为 completed；",
                            "         配合 list_agents 对照小屋真实状态，已停止的小屋用 interrupt_agent 清理",
                            "  步骤2: 汇总全部产出零件清单（文件路径+零件名）",
                            '  步骤3: 调 workflow_gate.py confirm-assembly "<零件清单>" 提交确认',
                            "  步骤4: 确认通过（gate=ASSEMBLY_CONFIRMED）后再调 select 获取总装房间", "",
                            "⚠️ 未确认前创建总装小屋 = 违规；代码已拦截。",
                        ])}
            msg = nl.join([
                "=== 门禁确认 | 部分小屋并行 · 第%d波【%s】===" % (wnum, wname), "",
                "前置检查通过: 上一波房间已全部完成 + 总装确认已提交（%s）" % state.get("assembly_confirmed_at", ""),
                "本波房间: [%s]（总装与验证，单独创建）" % pending[0][0], "",
                "--- 总装输入（必须原样写进总装小屋 prompt）---",
                "  零件清单: %s" % (state.get("assembly_inputs") or "（未提供！回去重新 confirm-assembly）"),
                "  ⚠️ 总装小屋只做：装配+干涉检查+整体验证。",
                "  ⚠️ 禁止重新建模/简化任何零件——所有零件已在第一波建完！",
                "",
                "--- 执行步骤 ---",
                '  步骤1: mode_gate.py room-start "%s"' % pending[0][0],
                "  步骤2: subagent_fork 创建总装小屋",
                "  步骤3: 总装 + 干涉检查 + 整体验证",
                '  步骤4: mode_gate.py room-end "%s"' % pending[0][0], "",
                "--- 报错回退规则 ---",
                "  若总装失败: 定位出错源房间（结构件/传动机构/壳体机架）",
                "  调用 mode_gate.py room-fail <源房间名> 将其回退到待处理状态",
                "  重新调用 select %s %s 重做该房间，完成后再次进入本波" % (choice, parallel),
            ])
        else:
            msg = nl.join([
                "=== 门禁确认 | 部分小屋并行 · 第%d波【%s】===" % (wnum, wname), "",
                "前置检查通过: 总装已完成（装配体已验证）",
                "本波房间: [%s]（工程图输出）" % pending[0][0], "",
                "  ⚠️ 出图小屋只做：三视图+标注+DWG/PDF 导出，禁止改动/重建装配体。", ""  ,
                "--- 执行步骤 ---",
                "--- 执行步骤 ---",
                '  步骤1: mode_gate.py room-start "%s"' % pending[0][0],
                "  步骤2: subagent_fork 创建出图小屋",
                "  步骤3: 工程图 + GB/T 检查 + DWG/PDF 导出",
                '  步骤4: mode_gate.py room-end "%s"' % pending[0][0],
                "  步骤5: select %s %s 确认全部完成" % (choice, parallel),
            ])

    # C模式（步步确认）：参数确认协议强制注入本次 select 输出，
    # 主对话必须把协议原样写进每个建模小屋的 prompt
    if choice == "C":
        msg = (msg + nl + nl +
               nl.join(["=== C模式·步步确认协议（强制注入每个建模小屋 prompt，一个字不能少）==="]
                       + CONFIRMATION_PROTOCOL_C))

    # ── 【Bug-38 修复】生成并落盘"零件归属清单"（machine-readable）─────────
    _po_written = write_part_ownership(state)
    _po_manifest = _po_written.get("manifest") if _po_written.get("ok") else None

    return {
        "ok": True, "step": "user_selected", "choice": choice,
        "choice_label": config["label"], "parallel_mode": parallel,
        "parallel_label": pconfig["label"],
        "subagent_strategy": config["strategy"],
        "subagent_config": state["subagent_config"],
        "assembly_confirmed": bool(state.get("assembly_confirmed")),
        "pending_rooms": pending_rooms,
        "completed_count": len(_get_done_rooms()),
        # ── 【C3 修复】一致性自检：把"门禁认为的完成数"与
        #    "mode_state 实际 ended 数"并排给出，一眼可辨是否不同步。
        #    若两者不一致，说明 room-end 尚未落盘或有房间被回收，
        #    调用方应先 mode_gate.py room-status 对账再继续。
        "sync": {
            "done_rooms": sorted(_get_done_rooms()),
            "all_rooms": sorted([r[0] for r in rooms]),
            "mode_state_ended": sorted([
                k for k, v in (_rooms_detail() or {}).items() if v.get("ended_at")]),
            "mode_state_active": sorted([
                k for k, v in (_rooms_detail() or {}).items() if v.get("active")]),
            "in_sync": (sorted(_get_done_rooms()) ==
                        sorted([k for k, v in (_rooms_detail() or {}).items()
                                if v.get("ended_at")])),
        },
        "total_rooms": len(rooms),
        "wave": wave_num, "wave_name": wave_name,
        "message": ((state.get("subagent_config") or {}).get("conflict_warning", "") + nl + nl
                    if (state.get("subagent_config") or {}).get("conflict_warning") else "") + msg,
        "conflict_warning": (state.get("subagent_config") or {}).get("conflict_warning"),
        # ── 【Bug-38 修复】零件归属清单（machine-readable，随 select 一并下发）──
        # 台账建议①：除写 prompt 外，还应写入 machine-readable 的零件归属清单。
        # 小屋可据此在保存前自检（sw_bridge.py check-part），
        # 主对话可在总装前用 verify-ownership 去重/核对。
        "part_ownership": _po_manifest,
        "part_ownership_file": (_po_written.get("path") if _po_written.get("ok")
                                else None),
        "part_ownership_note": (
            "【Bug-38】每个小屋只允许产出本房间 keywords 命中的零件；"
            "保存前用 sw_bridge.py check-part <文件> --room <房间名> 自检；"
            "总装前用 workflow_gate.py verify-ownership 去重核对。"),
        "ready_to_build": True
    }

# ══ 【Bug-38 修复】零件归属清单（machine-readable）══════════════════════════
# 台账现象：多房间并行时，小屋在"自主设计"时会按自己的理解补建相邻房间的
#   零件（结构件小屋误产"电机座"、传动机构小屋误产"转向支撑座"），
#   产出目录混入别房间零件；若小屋不自纠，总装时会出现重复或缺失零件。
# 台账建议：
#   ① select 返回的零件范围除写 prompt 外，还应写入 machine-readable 的
#      "零件归属清单"(JSON)；
#   ② 小屋保存零件前，校验文件名是否落在本房间归属清单内，否则拒绝保存
#      并 room-report 警告；
#   ③ 总装确认阶段增加"零件清单去重/归属校验"步骤。
# 本模块实现 ①③，② 由 sw_bridge save 侧配合（见 check_part_ownership）。

PART_OWNERSHIP_FILE = "part_ownership.json"


def _part_ownership_path(work_dir=None):
    """零件归属清单文件路径（放交付目录，便于小屋读取）。"""
    wd = work_dir
    if not wd:
        try:
            st = load_state()
            wd = st.get(WORKDIR_STATE_KEY)
        except Exception:
            wd = None
    base = wd or os.path.join(BASE_DIR, "load_cases")
    try:
        os.makedirs(base, exist_ok=True)
    except Exception:
        base = BASE_DIR
    return os.path.join(base, PART_OWNERSHIP_FILE)


def build_part_ownership(task, context=None, rooms=None):
    """【Bug-38】按房间生成"零件归属清单"（machine-readable）。

    Returns: {"schema": "...", "task_type": ..., "rooms": {房间名: {...}},
              "part_to_room": {零件关键词: 房间名}}
    """
    _key, tpl = task_template(task, context)
    scope = _room_parts_scope(task, context)
    _rooms = rooms or [r[0] for r in _default_rooms()]
    # 每个房间的"关键词集合"：从零件范围文本里按分隔符切分
    rooms_map = {}
    part_to_room = {}
    for rn, rtype in (rooms if rooms and isinstance(rooms[0], (list, tuple))
                      else [(r, None) for r in _rooms]):
        _scope_text = scope.get(rtype) if rtype else None
        if not _scope_text:
            # 按房间名反查类型
            for _n, _t in _default_rooms():
                if _n == rn:
                    _scope_text = scope.get(_t)
                    rtype = _t
                    break
        _scope_text = _scope_text or ""
        # 切分出零件关键词（、，/ 等分隔）
        import re as _re
        kws = [k.strip() for k in _re.split(r"[、,，/；;\s]+", _scope_text)
               if k.strip() and len(k.strip()) >= 2]
        # 去掉描述性尾缀（"等结构件"之类）
        kws = [k for k in kws if not k.startswith("总装") and not k.startswith("对总装")]
        rooms_map[rn] = {"type": rtype, "scope": _scope_text, "keywords": kws}
        for k in kws:
            part_to_room.setdefault(k, rn)
    return {"schema": "dsh-engineering/part-ownership@1",
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "task_type": _key,
            "task_type_label": (tpl or {}).get("label"),
            "rooms": rooms_map,
            "part_to_room": part_to_room,
            "rules": [
                "每个小屋【只允许】产出本房间 keywords 命中的零件；",
                "保存前用 sw_bridge.py check-part <文件名> --room <房间名> 校验；",
                "越界零件会被拒绝保存并记录到 reports/<房间>.violations.json；",
                "总装前主对话应调用 verify-ownership 做去重/归属核对。",
            ]}


def write_part_ownership(state=None, work_dir=None):
    """把零件归属清单落盘到交付目录（Bug-38 建议①）。"""
    st = state if state is not None else load_state()
    task = st.get("task") or ""
    ctx = st.get("context")
    _rooms = (st.get("subagent_config") or {}).get("rooms") or _default_rooms()
    manifest = build_part_ownership(task, ctx, _rooms)
    p = _part_ownership_path(work_dir)
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        return {"ok": True, "path": p, "manifest": manifest}
    except Exception as e:
        return {"ok": False, "error": "写入零件归属清单失败: %r" % (e,)}


def _norm_part_probe(part_name):
    """把零件文件名规范成用于关键词比对的"裸名"（去目录、去扩展名、去 DSH_ 前缀）。"""
    _stem = os.path.splitext(os.path.basename(str(part_name or "")))[0]
    _probe = _stem
    for _pfx in ("DSH_", "dsh_"):
        if _probe.startswith(_pfx):
            _probe = _probe[len(_pfx):]
    return _stem, _probe


def _match_rooms_for(probe, manifest):
    """【Bug-38 修复·歧义检测】返回该零件命中的【所有】房间（而非"第一个"）。

    ── 为什么必须返回全部命中 ────────────────────────────────────────────
    台账现象：结构件房间越界建了"加强筋"（本属支撑结构），check-part 未拦截，
      文件已保存，只能靠支撑房间事后重建覆盖。
    根因：关键词唯一性没有被保证 —— 车辆模板里 "加强筋" 同时出现在
      structural 与 support 两个房间的 scope 文本中，于是 part_to_room 的
      `setdefault` 只留下第一个，而 check_part_ownership 又是
      "本房间命中即放行"，两个房间就都顺利通过了。
    修复：显式收集【所有】命中房间；命中数 > 1 即判歧义，必须显式报错，
      而不是静默选边站。

    Returns: [(room, keyword), ...]
    """
    _rooms = manifest.get("rooms") or {}
    _hits = []
    for _rn, _info in _rooms.items():
        for _k in ((_info or {}).get("keywords") or []):
            if _k and (_k in probe or probe in _k):
                _hits.append((_rn, _k))
                break
    return _hits


def _existing_owner_of(part_name):
    """【Bug-38 修复】查"该零件文件当前是否已由某房间登记产出"。

    数据源：mode_gate 的 artifacts_registry.json（room-artifact 显式登记，最权威）。
    Returns: 房间名 或 None
    """
    try:
        _stem, _probe = _norm_part_probe(part_name)
        _reg_path = os.path.join(STATE_DIR, "artifacts_registry.json")
        if os.path.exists(_reg_path):
            with open(_reg_path, "r", encoding="utf-8") as f:
                _reg = json.load(f) or {}
            if isinstance(_reg, dict):
                for _rn, _items in _reg.items():
                    for _it in (_items or []):
                        if not isinstance(_it, dict):
                            continue
                        _nm = str(_it.get("name") or "")
                        _p = str(_it.get("path") or "")
                        if (_norm_part_probe(_nm)[1] == _probe
                                or _norm_part_probe(_p)[1] == _probe):
                            return _rn
    except Exception:
        pass
    return None


def check_part_ownership(part_name, room, work_dir=None):
    """【Bug-38 建议②】校验某零件是否属于该房间。

    Returns: {ok, allowed, room, part, matched_keyword, owner_room?, hint?}

    ── 【Bug-38 修复】三条新判据 ───────────────────────────────────────
    1) 歧义关键词（同一零件命中 ≥2 个房间）→ 直接拒绝，要求补齐归属定义；
    2) 文件【已存在】且已由其它房间登记 → 直接拒绝（原实现静默允许覆盖）；
    3) 未命中任何关键词 → 仍允许，但附明确提示（避免误拦新零件）。
    """
    try:
        st = load_state()
        manifest = build_part_ownership(st.get("task") or "", st.get("context"),
                                        (st.get("subagent_config") or {}).get("rooms"))
    except Exception:
        manifest = build_part_ownership("")
    p2r = manifest.get("part_to_room") or {}
    rooms = manifest.get("rooms") or {}
    _room_info = rooms.get(room) or {}
    _kws = _room_info.get("keywords") or []
    _part = str(part_name or "")
    _stem, _probe = _norm_part_probe(_part)
    _local_matched = None
    for k in _kws:
        if k and (k in _probe or _probe in k):
            _local_matched = k
            break
    # ── 判据 1：歧义检测（必须先于"本房间命中即放行"）────────────────
    _hits = _match_rooms_for(_probe, manifest)
    if len(_hits) > 1:
        _names = [h[0] for h in _hits]
        return {"ok": True, "allowed": False, "room": room, "part": _stem,
                "ambiguous_rooms": _names,
                "matched_keyword": _local_matched,
                "gate": "PART_OWNERSHIP_AMBIGUOUS",
                "error": ("【Bug-38】零件 '%s' 的归属【有歧义】：同时命中房间 %s。"
                          "歧义归属会让越界产出被静默放行"
                          "（台账：结构件越界建加强筋未被拦截）。"
                          % (_stem, _names)),
                "hint": ("· 由大屋在 workflow_gate 的房间零件范围里【去重关键词】，"
                         "让该零件只归属唯一房间；"
                         "· 或在本房间确实需要该零件时，显式补充更具体的关键词"
                         "（如 '舵机加强筋'）避免与其它房间重叠。")}
    if _local_matched:
        # ── 判据 2：文件已存在且已有"他房间"的登记产物 ──────────────
        _conflict = _existing_owner_of(_part)
        if _conflict and _conflict != room:
            return {"ok": True, "allowed": False, "room": room, "part": _stem,
                    "matched_keyword": _local_matched,
                    "owner_room": _conflict,
                    "gate": "PART_OWNERSHIP_CONFLICT",
                    "error": ("【Bug-38】文件 '%s' 已存在，且已由房间 [%s] 登记产出。"
                              "本房间 [%s] 不得静默覆盖他房间的成果。"
                              % (_stem, _conflict, room)),
                    "hint": ("· 由归属房间 [%s] 重新产出/修正该零件；"
                             "· 若确需本房间接管，请先由大屋确认并清理旧登记。"
                             % _conflict)}
        return {"ok": True, "allowed": True, "room": room, "part": _stem,
                "matched_keyword": _local_matched}
    # 找它实际属于哪个房间（单命中场景）
    _owner = None
    _owner_kw = None
    if _hits:
        _owner, _owner_kw = _hits[0][0], _hits[0][1]
    if _owner and _owner != room:
        return {"ok": True, "allowed": False, "room": room, "part": _stem,
                "owner_room": _owner, "matched_keyword": _owner_kw,
                "hint": ("零件 '%s' 属于房间 [%s]（关键词 '%s'），不属于 [%s]。"
                         "越界产出会导致总装重复/缺失（Bug-38）。"
                         % (_stem, _owner, _owner_kw, room))}
    # ── 判据 3：未命中任何关键词 → 允许；但已存在于他房间时仍拦截 ────
    _conflict3 = _existing_owner_of(_part)
    if _conflict3 and _conflict3 != room:
        return {"ok": True, "allowed": False, "room": room, "part": _stem,
                "owner_room": _conflict3, "matched_keyword": None,
                "gate": "PART_OWNERSHIP_CONFLICT",
                "error": ("【Bug-38】文件 '%s' 已存在且由房间 [%s] 登记产出，"
                          "本房间 [%s] 不得覆盖（即便零件名未命中本房间关键词）。"
                          % (_stem, _conflict3, room)),
                "hint": "若确属本房间新零件，请换一个不冲突的零件名。"}
    return {"ok": True, "allowed": True, "room": room, "part": _stem,
            "matched_keyword": None,
            "note": ("未匹配到任何房间关键词，按【允许】处理（避免误拦新零件）；"
                     "若确认越界，请 room-report 警告。")}

def verify_ownership(work_dir=None):
    """【Bug-38 建议③】总装前核对交付目录里的零件归属与去重。

    扫描交付目录的 .SLDPRT，按归属清单分类，报告：
      · 归属明确的零件（按房间分组）；
      · 越界零件（实际归属与所在房间不符）；
      · 重复零件（同名多份）。
    Returns: {ok, by_room, unowned, duplicates, total}
    """
    import glob as _glob
    st = load_state()
    wd = work_dir or st.get(WORKDIR_STATE_KEY)
    out = {"ok": True, "by_room": {}, "unowned": [], "duplicates": [],
           "total": 0, "work_dir": wd}
    if not wd or not os.path.isdir(wd):
        out["ok"] = False
        out["error"] = "交付目录不存在: %s" % wd
        return out
    manifest = build_part_ownership(st.get("task") or "", st.get("context"),
                                    (st.get("subagent_config") or {}).get("rooms"))
    p2r = manifest.get("part_to_room") or {}
    # ── 注意1：Windows 的文件 glob 是【大小写不敏感】的，同时匹配
    #   "*.SLDPRT" 与 "*.sldprt" 会把每个文件算两次（实测：4 个零件报成 6 个，
    #   且全部被误判为"重复"）。这里只匹配一次，再用集合去重兜底。
    # ── 注意2：必须【递归】扫描 —— 实测重复零件最常见于各小屋的子目录
    #   （结构件小屋/DSH_车架底板.SLDPRT 与根目录同名），
    #   只扫根目录会漏掉真正的重复（Bug-38 的核心风险）。
    _raw_files = _glob.glob(os.path.join(wd, "**", "*.SLDPRT"), recursive=True)
    if not _raw_files:
        _raw_files = _glob.glob(os.path.join(wd, "**", "*.sldprt"), recursive=True)
    _seen_paths = set()
    files = []
    for _f in _raw_files:
        _k = os.path.normcase(os.path.abspath(_f))
        if _k in _seen_paths:
            continue
        _seen_paths.add(_k)
        files.append(os.path.abspath(_f))
    files.sort()
    out["total"] = len(files)
    _seen = {}
    for fp in files:
        stem = os.path.splitext(os.path.basename(fp))[0]
        _probe = stem
        for _pfx in ("DSH_", "dsh_"):
            if _probe.startswith(_pfx):
                _probe = _probe[len(_pfx):]
        _seen.setdefault(_probe, []).append(fp)
        _owner = None
        for k, rn in p2r.items():
            if k and (k in _probe or _probe in k):
                _owner = rn
                break
        if _owner:
            out["by_room"].setdefault(_owner, []).append(stem)
        else:
            out["unowned"].append(stem)
    for stem, lst in _seen.items():
        if len(lst) > 1:
            out["duplicates"].append({"part": stem, "files": lst})
    out["summary"] = ("共 %d 个零件；归属明确 %d 个房间；未归类 %d 个；重复 %d 组"
                      % (out["total"], len(out["by_room"]),
                         len(out["unowned"]), len(out["duplicates"])))
    if out["duplicates"]:
        out["warning"] = ("存在重复零件，总装前必须去重（Bug-38）：%s"
                          % [d["part"] for d in out["duplicates"]])
    return out


def cmd_verify_ownership(work_dir=None):
    return verify_ownership(work_dir)


def cmd_check_part(part_name, room):
    return check_part_ownership(part_name, room)


def _mg_const(name, default):
    """从 mode_gate.py 动态读取常量，保证两个模块口径【单一来源】。

    【僵尸复活修复】原 workflow_gate 把心跳阈值硬编码成 180，
      而 mode_gate 已放宽到 600 —— 两套口径打架会让 recover 误判 stale
      并触发重启（"僵尸任务自动复活"的推手）。
      改为动态读取后，阈值只在 mode_gate 里定义一次。
    """
    try:
        import importlib.util as _ilu
        _p = os.path.join(BASE_DIR, "mode_gate.py")
        _spec = _ilu.spec_from_file_location("_mg_for_const", _p)
        _m = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_m)
        return getattr(_m, name, default)
    except Exception:
        return default


def _heartbeat_timeout():
    return _mg_const("SW_HEARTBEAT_TIMEOUT", 180)


def _start_grace():
    return _mg_const("SW_START_GRACE", 120)


def _stale_grace():
    return _mg_const("SW_STALE_GRACE", 600)


def cmd_recover():
    """检查所有房间的真实状态，按六态分类返回（防止误判）。

    running / starting : 正常 —— 严禁说'没反应'，严禁重启；
    stale              : 心跳超时已判死 —— 才允许走重启流程；
    no_heartbeat       : 登记超宽限期仍无心跳 —— 可疑，先 list_agents 核对再决定；
    completed / failed : 非活动状态，不在重启范围。
    """
    hb_dir = os.path.join(STATE_DIR, "heartbeats")
    ms_path = os.path.join(STATE_DIR, "mode_state.json")
    empty = {"ok": True, "running": [], "starting": [], "stale": [], "no_heartbeat": [],
             "completed": [], "failed": [], "rooms_to_restart": [], "message": ""}
    if not os.path.exists(ms_path):
        empty["message"] = "无模式状态"
        return empty
    try:
        with open(ms_path, "r", encoding="utf-8") as f:
            ms = json.load(f)
    except Exception:
        empty["message"] = "读取模式状态失败"
        return empty
    rooms = ms.get("rooms", {}) or {}
    if not rooms:
        empty["message"] = "没有房间记录"
        return empty
    import time as _time
    now = _time.time()
    running, starting, stale, no_hb, completed, failed = [], [], [], [], [], []
    zombie = []   # 【僵尸复活修复】登记了房间却没拉起子代理的残留
    for room, info in rooms.items():
        if info.get("failed_at"):
            failed.append({"room": room, "reason": "曾 room-fail，待重做"})
            continue
        if info.get("ended_at") or not info.get("active"):
            completed.append({"room": room})
            continue
        hb_path = os.path.join(hb_dir, room + ".heartbeat")
        hb_age = None
        try:
            if os.path.exists(hb_path):
                hb_age = now - os.path.getmtime(hb_path)
        except Exception:
            pass
        started = info.get("started_at")
        # ── 【僵尸复活修复】阈值必须与 mode_gate 统一，且先识别僵尸 ────────
        # 测试反馈：上一轮的僵尸任务在本次会话被自动复活（recover/重启误触发），
        #   重新登记了房间却没真正拉起子代理。
        # 根因有二：
        #   ① 此处硬编码 hb_age > 180 判死，而 mode_gate 已放宽到 600s —— 两套口径
        #      打架，recover 把"建模中没空跳心跳"的正常房间误判 stale，
        #      进而触发 restart，这就是"自动复活"的直接推手；
        #   ② 完全没有"僵尸"概念：房间 active 但没有对应 subagent 登记，
        #      说明它根本没有子代理在跑，那种房间不该进入 restart 队列。
        _HB_TIMEOUT = _heartbeat_timeout()
        _START_GRACE = _start_grace()
        _subs_all = (ms.get("subagents") or {})
        _has_sub = room in _subs_all
        # 僵尸判定：active 但没有子代理登记 → 不是"死掉需要重启"，
        #   而是"登记了却没拉起"，应当走 reset/清理，而非 restart。
        if not _has_sub and info.get("active") and not info.get("ended_at"):
            zombie.append({
                "room": room,
                "reason": "房间登记为 active，但没有对应的 subagent 记录（未真正拉起子代理）",
                "note": "这不是需要重启的死亡房间，而是僵尸残留 —— 用 reset 清理，不要 restart",
            })
            continue
        if hb_age is not None:
            if hb_age > _HB_TIMEOUT:
                # 滞后宽限：刚超时先观察，不立即判死
                if hb_age - _HB_TIMEOUT > _stale_grace():
                    stale.append({"room": room,
                                  "reason": "心跳超时 %d 秒（阈值 %d）已超观察期，判定为死亡"
                                            % (int(hb_age), _HB_TIMEOUT)})
                else:
                    running.append({"room": room, "heartbeat_age_sec": round(hb_age, 1),
                                    "note": "心跳刚过期，处于观察期，禁止判死/重启"})
            else:
                running.append({"room": room, "heartbeat_age_sec": round(hb_age, 1),
                                "note": "正在运行，禁止说没反应"})
        elif started and (now - started) <= _START_GRACE:
            starting.append({"room": room, "age_sec": int(now - started),
                             "note": "刚登记正在启动（宽限期内），等待即可"})
        elif started:
            no_hb.append({"room": room, "reason": "启动 %d 秒仍无心跳" % int(now - started),
                          "note": "可疑：先用 list_agents 核对平台侧状态，再决定重启"})
        else:
            starting.append({"room": room, "age_sec": None, "note": "无启动时间，按启动中处理"})
    rooms_to_restart = stale
    parts = ["running=%d starting=%d stale=%d no_heartbeat=%d completed=%d failed=%d"
             % (len(running), len(starting), len(stale), len(no_hb), len(completed), len(failed))]
    if stale:
        parts.append("发现 %d 个房间心跳死亡，需重启：workflow_gate.py restart <房间名>" % len(stale))
        parts.append("注意：running/starting 房间必须等待，严禁误判'没反应'、严禁误杀。")
    else:
        parts.append("所有活动房间状态正常（running/starting），无需重启，禁止说它们没反应。")
    if no_hb:
        parts.append("可疑房间（先核对后处理）: " + ", ".join(x["room"] for x in no_hb))
    # 【僵尸复活修复】僵尸房间绝不进入 restart 队列
    if zombie:
        parts.append("★ 发现 %d 个僵尸房间（登记了房间但没拉起子代理）: %s"
                     % (len(zombie), ", ".join(x["room"] for x in zombie)))
        parts.append("处理方式：workflow_gate.py reset 清理残留；"
                     "【严禁】对僵尸房间调 restart —— 那只会让空房间反复复活。")
    return {"ok": True, "running": running, "starting": starting, "stale": stale,
            "no_heartbeat": no_hb, "completed": completed, "failed": failed,
            "zombie": zombie,
            "rooms_to_restart": rooms_to_restart,
            "count": len(rooms_to_restart), "message": " | ".join(parts)}


def cmd_restart(room):
    """一键重启房间：释放旧 subagent 记录 + 将房间重新加入待处理队列。"""
    import subprocess as _sp
    mg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mode_gate.py")
    try:
        _sp.run([sys.executable, mg_path, "subagent-free", room],
                capture_output=True, text=True, encoding="utf-8", timeout=15)
    except Exception:
        pass
    try:
        out = _sp.run([sys.executable, mg_path, "room-restart", room],
                      capture_output=True, text=True, encoding="utf-8", timeout=30)
        result = json.loads(out.stdout or "{}")
    except Exception as e:
        result = {"ok": False, "error": str(e)}
    return {"ok": result.get("ok", False), "room": room, "restart_result": result}


def cmd_status():
    state = load_state()
    return {"ok": True, "step": state.get("step"), "task": state.get("task",""),
            "user_choice": state.get("user_choice"), "parallel_mode": state.get("parallel_mode"),
            "assembly_confirmed": bool(state.get("assembly_confirmed")),
            "finished_at": state.get("finished_at"),
            "ready": state.get("step") == "user_selected"}

def cmd_reset():
    state = _fresh_state()
    save_state(state)
    return {"ok": True, "step": "idle"}

def cmd_check():
    state = load_state()
    if state.get("step") != "user_selected":
        return {"ok": False, "gate": "BLOCKED", "error": "设计门禁拦截！"}
    return {"ok": True, "gate": "OPEN"}


def cmd_gate_summary():
    """【Bug-06 修复】单一事实来源的"门禁总览"。

    原缺陷：mode_gate residue-check 说 has_residue=false（干净），
    workflow_gate status 说 step=finished 且 ready=false ——
    两者对"任务是否完成/能否开新任务"的口径不一致，用户无法从单条命令判断。

    本命令把三个来源（workflow_state / mode_state / residue-check）汇总成
    一个结论，明确回答三个问题：
      ① 当前任务处于什么阶段（phase）
      ② 有没有活着的子对话（alive）
      ③ 能不能直接开新任务（can_start_new_task）以及该调什么命令
    """
    state = load_state()
    step = str(state.get("step") or "idle")
    ms_path = os.path.join(STATE_DIR, "mode_state.json")
    ms = {}
    try:
        if os.path.exists(ms_path):
            with open(ms_path, "r", encoding="utf-8") as f:
                ms = json.load(f) or {}
    except Exception:
        ms = {}
    rooms = ms.get("rooms") or {}
    active = sorted([r for r, v in rooms.items() if (v or {}).get("active")])
    failed = sorted([r for r, v in rooms.items() if (v or {}).get("failed_at")])
    ended = sorted([r for r, v in rooms.items() if (v or {}).get("ended_at")])
    _expect = [r[0] for r in ((state.get("subagent_config") or {}).get("rooms") or []) if r]
    pending_rooms = [r for r in _expect if r not in ended]

    res = _residue_check(auto_clean=False)
    alive = res.get("alive") or []

    # 阶段判定（单一结论）
    if step == "finished":
        phase = "finished(已完成)"
    elif active:
        phase = "running(有房间在跑)"
    elif step in ("idle", ""):
        phase = "idle(未开始)"
    elif failed:
        phase = "failed(有失败房间待重做)"
    elif pending_rooms:
        phase = "in_progress(还有房间未完成)"
    else:
        phase = "ready_to_archive(可收尾)"

    # 能否开新任务：终态 + 无活子对话
    can_new = (step in ("finished", "idle", "")) and not alive and not active
    if step == "finished" and not alive:
        next_action = ("可直接 workflow_gate.py init \"<新任务>\" —— "
                       "Bug-02 修复后 init 会自动归档上一轮，无需手动 reset")
    elif active or alive:
        next_action = ("等待/处理在跑的房间：mode_gate.py room-status 核对，"
                       "room-end 或 room-fail；再 workflow_gate.py recover 复查")
    elif step in ("idle", ""):
        next_action = "workflow_gate.py init \"<任务描述>\""
    else:
        next_action = "workflow_gate.py select <A|B|C>,<D|E> 继续推进"

    return {
        "ok": True,
        "phase": phase,
        "workflow": {"step": step, "task": state.get("task"),
                     "user_choice": state.get("user_choice"),
                     "parallel_mode": state.get("parallel_mode"),
                     "assembly_confirmed": bool(state.get("assembly_confirmed")),
                     "finished_at": state.get("finished_at")},
        "rooms": {"expected": _expect, "ended": ended, "active": active,
                  "failed": failed, "pending": pending_rooms,
                  "state_file": ms_path},
        "residue": {"has_residue": bool(res.get("has_residue")),
                    "alive": [x.get("detail") or x.get("room") for x in alive],
                    "alive_count": len(alive)},
        "consistent": (not (not rooms and (state.get("subagent_config") or {}).get("rooms"))),
        "can_start_new_task": can_new,
        "next_action": next_action,
        "note": ("【Bug-06】本命令是判断「能否开新任务」的唯一权威入口："
                 "workflow=任务阶段，rooms=mode_state 房间实况，residue=残留体检。"
                 "三者口径不一致时以本命令的 phase/can_start_new_task 为准。"),
    }

def _run_main_with_defense_exit():
    """[门禁] 包装 __main__：三大防线拦截时以非零码退出。

    为什么不改每个分支：workflow_gate 的每个子命令各自 print 结果，逐个
      改动易漏且易破坏既有输出格式。这里统一把 stdout 捕获、解析出 JSON，
      只在 gate 属于防线拦截时设置退出码，输出内容与顺序保持不变。

    ── 【P1-5 修复 · 所有 sys.exit 路径静默丢失输出】──────────────────────
    原实现把 stdout 换成 StringIO，在 finally 里恢复，【之后】才把缓冲写回
    （第 3529-3531 行）。但 _main_body() 内部有三处 sys.exit()：
        · 无参数/--help（用法提示）
        · select 缺参数（用法提示）
        · 未知命令（错误提示）
    SystemExit 会【穿透】finally 直接终止进程，那三行写回代码永远不会执行 →
    实测这些路径 stdout 恒为 0 字节。调用方（主对话/小屋）看不到任何提示，
    只拿到退出码，于是被逼去手改 workflow_state.json 绕过流程。

    修复：把 SystemExit 在包装层【捕获】，先无条件写回捕获的输出，
    再用原始退出码退出。help / 用法 / 未知命令 / 缺参数四类提示全部恢复。
    """
    import io as _io
    _buf = _io.StringIO()
    _old = sys.stdout
    _exit_code = 0
    _pending_exc = None
    sys.stdout = _buf
    try:
        _main_body()
    except SystemExit as _e:
        # 记录退出码，稍后写回输出再退出（不能让它跳过下面的写回）
        _code = getattr(_e, "code", 0)
        if _code is None:
            _exit_code = 0
        elif isinstance(_code, int):
            _exit_code = _code
        else:
            # sys.exit("字符串") 语义：打印该串到 stderr 并以 1 退出
            try:
                sys.stderr.write(str(_code) + "\n")
            except Exception:
                pass
            _exit_code = 1
    except BaseException as _e2:
        # 其它异常同样不能吞掉输出：先写回，再原样抛出
        _pending_exc = _e2
    finally:
        sys.stdout = _old
    # ── 无条件写回捕获的输出（这正是原实现漏掉的步骤）──
    _text = _buf.getvalue()
    if _text:
        try:
            _old.write(_text)
            _old.flush()
        except Exception:
            pass
    if _pending_exc is not None:
        raise _pending_exc
    if _exit_code:
        sys.exit(_exit_code)
    # 输出是【多行 pretty JSON】，必须整体解析（不是取最后一行）。
    _j = {}
    _t = _text.strip()
    if _t:
        try:
            _j = json.loads(_t)
        except Exception:
            try:
                _j = json.loads(_t.splitlines()[-1])
            except Exception:
                _j = {}
    try:
        if isinstance(_j, dict) and _j.get("ok") is False:
            _g = str(_j.get("gate") or "")
            if _g in ("DEFENSE_REQUIRED", "DEFENSE_ERROR"):
                sys.exit(1)
    except SystemExit:
        raise
    except Exception:
        pass


def _main_body():
    if len(sys.argv) < 2 or sys.argv[1].lower() in ("-h", "--help", "help"):
        # ── 【C2 修复】用法提示必须列全命令 ─────────────────────────────
        # 原提示漏了 confirm-assembly（以及 recover/restart），
        #   导致调用方看到帮助后以为"命令不存在"，转而去手改 workflow_state.json
        #   绕过流程 —— 这正是测试反馈"流程无法合法推进"的直接原因。
        #
        # ── 【BUG-03/04 修复】明确"本环境真实入口是 CLI，不是 DSH 工具名" ──
        # 旧文档写 workflow-gate-init / workflow-gate-select 等"工具名"，
        #   但本环境并未注册这些 DSH 工具，照文档调用必然失败。
        #   现于帮助里给出可直接复制的命令行形式，并显式说明这一点。
        _me = os.path.basename(os.path.abspath(__file__))
        print(json.dumps({"ok": False,
                          "entry_point": "命令行脚本（本环境没有 workflow-gate-* 这些 DSH 工具）",
                          "error": "用法: python %s "
                                   "{init|provide_context|select|confirm-assembly|"
                                   "status|check|recover|restart|reset} [args]" % _me,
                          "commands": {
                              "init": "初始化任务：python %s init \"<任务描述>\" [--force]" % _me,
                              "provide_context": "提交背景→返回问题集/力学估算："
                                                 "python %s provide_context \"<回答原文>\"" % _me,
                              "select": "选择搭建方式与并行策略："
                                        "python %s select C,E   （等价写法：select C E / "
                                        "select \"C, E\" / --choice C --parallel E）" % _me,
                              "confirm-assembly": "Wave1 完成后确认总装（缺此步流程会卡死）："
                                                  "python %s confirm-assembly \"<零件清单>\"" % _me,
                              "status": "查看当前门禁状态：python %s status" % _me,
                              "gate-summary": "【推荐】门禁总览（能否开新任务）："
                                              "python %s gate-summary" % _me,
                              "check": "检查是否允许建模：python %s check" % _me,
                              "work-dir [新目录]": "查询/设置本任务交付目录"
                                                   "（观察点1/5 隔离）：python %s work-dir" % _me,
                              "check-part <文件> --room <房间>": "【Bug-38】校验零件归属："
                                                   "python %s check-part <文件> --room <房间>" % _me,
                              "verify-ownership [目录]": "【Bug-38】总装前核对零件归属与去重："
                                                   "python %s verify-ownership" % _me,
                              "recover": "恢复卡住的任务状态：python %s recover" % _me,
                              "restart": "重做指定房间：python %s restart <房间名>" % _me,
                              "reset": "清空并重新开始：python %s reset" % _me,
                          },
                          "note": ("【BUG-03/04】历史文档中的 workflow-gate-init / "
                                   "workflow-gate-provide-context / workflow-gate-select "
                                   "在本环境【不存在】；真实入口就是上述命令行脚本。"
                                   "select 的并行策略以 workflow_state.json（本轮 select "
                                   "当场写入）为权威，并与 mode_state.json 自动对齐。")},
                         ensure_ascii=False, indent=2))
        sys.exit(1)
    cmd = sys.argv[1].lower()
    if cmd == "init":
        print(json.dumps(cmd_init(sys.argv[2]) if len(sys.argv) > 2 else {"ok":False,"error":"need task"}, ensure_ascii=False, indent=2))
    elif cmd == "provide_context":
        print(json.dumps(cmd_provide_context(sys.argv[2]) if len(sys.argv) > 2 else {"ok":False,"error":"need context"}, ensure_ascii=False, indent=2))
    elif cmd == "select":
        # 【BUG-03 修复】用一个参数解析器统一吃掉 "C, E" / "C E" / --choice 等写法
        _choice, _parallel = _parse_choice_args(sys.argv[2:])
        if _choice is None:
            print(json.dumps({"ok": False,
                              "error": "缺少搭建方式 A/B/C",
                              "usage": "python workflow_gate.py select C,E  "
                                       "（或 select C E / select \"C, E\"）"},
                             ensure_ascii=False, indent=2))
            sys.exit(2)
        print(json.dumps(cmd_select(_choice, _parallel), ensure_ascii=False, indent=2))
    elif cmd == "confirm-assembly":
        print(json.dumps(cmd_confirm_assembly(sys.argv[2] if len(sys.argv) > 2 else ""), ensure_ascii=False, indent=2))
    elif cmd == "status":
        print(json.dumps(cmd_status(), ensure_ascii=False, indent=2))
    elif cmd in ("verify-ownership", "verify_ownership"):
        print(json.dumps(cmd_verify_ownership(sys.argv[2] if len(sys.argv) > 2 else None),
                         ensure_ascii=False, indent=2))
    elif cmd in ("check-part", "check_part"):
        # 用法: check-part <文件名> <房间名>  或  check-part <文件名> --room <房间名>
        _cp_args = [a for a in sys.argv[2:] if not a.startswith("--")]
        _cp_room = None
        for _i, _a in enumerate(sys.argv):
            if _a == "--room" and _i + 1 < len(sys.argv):
                _cp_room = sys.argv[_i + 1]
        print(json.dumps(cmd_check_part(_cp_args[0] if _cp_args else "",
                                        _cp_room or (_cp_args[1] if len(_cp_args) > 1 else "")),
                         ensure_ascii=False, indent=2))
    elif cmd in ("work-dir", "workdir", "work_dir"):
        print(json.dumps(cmd_work_dir(sys.argv[2] if len(sys.argv) > 2 else None),
                         ensure_ascii=False, indent=2))
    elif cmd in ("gate-summary", "gate_summary", "summary"):
        print(json.dumps(cmd_gate_summary(), ensure_ascii=False, indent=2))
    elif cmd == "reset":
        print(json.dumps(cmd_reset(), ensure_ascii=False, indent=2))
    elif cmd == "recover":
        print(json.dumps(cmd_recover(), ensure_ascii=False, indent=2))
    elif cmd == "restart":
        room_name = sys.argv[2] if len(sys.argv) > 2 else ""
        print(json.dumps(cmd_restart(room_name) if room_name else {"ok":False,"error":"need room name"}, ensure_ascii=False, indent=2))
    elif cmd == "check":
        print(json.dumps(cmd_check(), ensure_ascii=False, indent=2))
    else:
        print(json.dumps({"ok": False, "error": "未知命令: " + cmd,
                          "hint": "运行 `python workflow_gate.py --help` 查看全部命令"},
                         ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    _run_main_with_defense_exit()