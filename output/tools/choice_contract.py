# -*- coding: utf-8 -*-
"""choice_contract —— 搭建方式与并行策略的【固定两问】（代码级强制）

══ 为什么要单独成模块 ═══════════════════════════════════════════════════════
  旧实现把「搭建方式 A/B/C」与「并行策略 D/E」交给 question_contract 管理，
  并设 max_per_call=1（每次只许问一题）。问题在于：这两问本就是【两件独立的事】，
  却被当成"同一批必须拆分的题"来判 —— 于是"连着问两次"反而落入
  "题数与期望不符 / 单次超限"这类规则，经常误判为违规。

  用户决定：把这两问【写死在代码里】，流程强制要求它们各自被系统问题问过，
  不再使用会误判的"分批提问"规则。

══ 本模块职责（已简化） ═══════════════════════════════════════════════════
  · 只提供这两个【固定题面】（问题与选项由代码给出，模型不可改写）；
  · 门禁把题面回传给模型，模型用 ask_user_question 原样询问用户；
  · 用户的最终选择由 workflow_gate.py 的 select 参数校验（A/B/C 与 D/E）。

══ 已移除的机制 ═══════════════════════════════════════════════════════════
  曾经还有一套「系统提问账本」用于核对"题目是否问全"。
  用户反馈它实用价值低且反复造成流程卡顿（记录不全就被判定"没问"），
  已连同宿主侧写入一并移除。因此本模块【不再读写任何账本文件】。
"""

import json
import os


BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ── 固定问题 1：搭建方式（A/B/C）────────────────────────────────────────────
BUILD_MODE_QUESTION = {
    "id": "build_mode",
    "header": "搭建方式",
    "question": "请选择搭建方式（A / B / C）",
    "options": [
        {"label": "A", "description": "完全自主搭建：AI 自主决策，按门禁波次调度"},
        {"label": "B", "description": "部分自主搭建：关键节点询问"},
        {"label": "C", "description": "步步确认：每个零件参数表逐件确认后再建模"},
    ],
    "multi_select": False,
}

# ── 固定问题 2：并行策略（D/E）─────────────────────────────────────────────
PARALLEL_MODE_QUESTION = {
    "id": "parallel_mode",
    "header": "并行策略",
    "question": "请选择并行策略（D / E）",
    "options": [
        {"label": "D", "description": "部分小屋并行：按波次分批启动"},
        {"label": "E", "description": "单小屋串联：一次一个房间，完成后开下一个"},
    ],
    "multi_select": False,
}

#: 强制顺序：先问搭建方式，再问并行策略（两个独立问题）
MANDATORY_QUESTIONS = (BUILD_MODE_QUESTION, PARALLEL_MODE_QUESTION)


def questions_payload():
    """返回两个固定问题（供门禁回传给模型，原样提问）。"""
    return [dict(q) for q in MANDATORY_QUESTIONS]