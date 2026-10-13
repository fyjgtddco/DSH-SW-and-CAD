# -*- coding: utf-8 -*-
"""visual_check —— 「看图 → 矫正 → 继续看 → 继续矫正」的代码级强制闭环

═══════════════════════════════════════════════════════════════════════════
本模块存在的唯一理由（用户原话）
═══════════════════════════════════════════════════════════════════════════

    「在组装的时候，组装子代理必须在成功建模之后，使用截图进行查看，
      三视图查看建模如何，即建模子代理必须以"看图→矫正模型→继续看→
      继续矫正"为建模成功后最为主要的矫正建模方式。
      必须要让模型自己看到他建的是什么样子的东西」

以及用户的总原则：

    「所有约束都要写在代码里强制执行，不能只写在提示词/文档里」

因此本模块把「建模后必须看图」从一个**口头约定**升级为**会返回 False、
能阻断放行**的代码契约。四段能力：

  A. capture_views()          —— 真正驱动 SolidWorks 切到 前视/上视/右视/等轴测
                                 再截图，落盘并回传每张图的路径/大小/哈希。
  B. check_and_correct()      —— ★核心闭环。每一轮都【真实回读几何】
                                 （body_box_mm 六元组包围盒）与 expect 比对，
                                 不一致就产出结构化「矫正指令」进入下一轮；
                                 达到 max_rounds 仍不一致 → FAIL，绝不上报假成功。
  C. verify_visual_evidence()  —— 「看图这件事真的发生过」的代码级证明：
                                 每轮引用的截图必须真实存在于磁盘且字节数 > 0。
  D. enforce_visual_gate()     —— 建模成功之后【必须】走的门；
                                 未通过时 ok=False，上游房间收尾可据此拒绝放行。

═══════════════════════════════════════════════════════════════════════════
设计原则
═══════════════════════════════════════════════════════════════════════════

1. **fail-closed**：任何"取不到几何 / 截不到图 / 零件打不开"的情形一律
   ok=False 并说明原因，**绝不返回一个不存在的文件路径当成功**。
2. **判据来自真实回读**，不是"截图这个动作做过了"。截图是【证据】，
   几何回读才是【判据】—— 这正是原缺陷（把"截了图"当"建对了"）的修复点。
3. **只依赖标准库**（json/os/sys/time/argparse/subprocess/hashlib...）。
   对 swapi / mode2_flow 的导入一律 try/except 包住 ——
   它们不在时本模块仍可完成逻辑校验（降级会如实标注）。
4. **全量留痕**：每轮的截图路径、实测包围盒、偏差、判定、矫正建议
   都原子写（os.replace）到 out_dir/visual_loop.json，供总理子代理/父代理复查。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time

# ── 【编码修复】Windows 控制台默认 cp936，中文 JSON 会乱码 ──────────────
# 做法与 sw_guard.py 的 _setup_utf8 一致（它在导入时就 reconfigure）。
# 这里【不 import sw_guard】—— 该文件正被其他子代理修改，避免耦合。
# 同时提供 _utf8_out() 兜底：reconfigure 失败也不因编码崩掉。
_UTF8_READY = False


def _setup_utf8(force: bool = False) -> bool:
    """把 stdout/stderr 切到 UTF-8；幂等且静默失败（绝不影响主流程）。"""
    global _UTF8_READY
    if _UTF8_READY and not force:
        return True
    ok = False
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
                ok = True
                continue
            except Exception:
                pass
        # 兜底：不支持 reconfigure 的对象，包一层 UTF-8 wrapper
        try:
            buf = getattr(stream, "buffer", None)
            if buf is not None:
                import io
                setattr(sys, stream_name,
                        io.TextIOWrapper(buf, encoding="utf-8",
                                         errors="replace", line_buffering=True))
                ok = True
        except Exception:
            pass
    _UTF8_READY = ok
    return ok


_setup_utf8()


def _utf8_out(text: str) -> None:
    """安全打印：即使 stdout 不是 UTF-8 也绝不因编码崩溃。"""
    try:
        sys.stdout.write(text + "\n")
        sys.stdout.flush()
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "ascii"
        sys.stdout.write(text.encode(enc, "replace").decode(enc, "replace") + "\n")
        sys.stdout.flush()


def emit(obj) -> None:
    """统一的 CLI 输出口径：json.dumps(..., ensure_ascii=False, indent=2)。"""
    _utf8_out(json.dumps(obj, ensure_ascii=False, indent=2))


# ═══════════════════════════════════════════════════════════════════════════
# 一、★ 代码级常量：所有阈值/次数/判定都在这里，且【真正生效】
# ═══════════════════════════════════════════════════════════════════════════

#: 默认三视图（+ 等轴测）。三视图看不出立体形状，所以必须补等轴测图。
DEFAULT_VIEWS = ("front", "top", "right", "iso")

#: 视图名 → SolidWorks ShowNamedView2 的 VName 字符串。
#  【核实来源】swapi.py 第 5211~5218 行的 `_PLANE_VIEW` 映射表用的就是这套
#   带 `*` 前缀的官方视图名（"*Front" / "*Top" / "*Right" / "*Bottom" /
#   "*Back" / "*Left"），第 5079 行等轴测用 `VIEW_ISO_NAME = "*Isometric"`
#   （swapi.py 第 2724 行）。因此这里沿用同一套 VName，不自己造名字。
VIEW_VNAME = {
    "front": "*Front",
    "back": "*Back",
    "left": "*Left",
    "right": "*Right",
    "top": "*Top",
    "bottom": "*Bottom",
    "iso": "*Isometric",
    "isometric": "*Isometric",
}

#: 视图名 → ShowNamedView2 的 ViewId。
#
#  ══ 【实机实测·重要修正】ViewId 必须给真实值，传 0 是【无效调用】══════
#  实测方法（2026-10-12，SW2025 SP5.0，零件 DSH_底座立柱）：
#    用 SaveBMP 导出每次切换后的位图并算 sha1，看图像是否真的变了。
#    结果（决定性证据）：
#      *Front  vid=0 → sha1=e126aa76ca02
#      *Front  vid=1 → sha1=4e3be2f2a435   ← 变了
#      *Front  vid=2 → sha1=3f50a44335db   ← 变了
#      *Top    vid=0 → sha1=3f50a44335db   ← 与上一张【完全相同】= 没切换
#      *Top    vid=5 → sha1=0595a644ddd0   ← 变了
#      *Right  vid=0 → sha1=0595a644ddd0   ← 与上一张【完全相同】= 没切换
#      *Right  vid=4 → sha1=5c822a33f474   ← 变了
#      *Iso    vid=0 → sha1=5c822a33f474   ← 与上一张【完全相同】= 没切换
#      *Iso    vid=7 → sha1=07e3b653b472   ← 变了
#    结论：`ShowNamedView2(name, 0)` 不改变视角 —— 图像原封不动地停在
#    上一次的取景上。三视图会因此拍成三张一模一样的图。
#    （swapi.py:5223 的 `_normal_to` 就是传 0，所以它只对"已经在正确
#      基准面草图"的场景有效，不能用于本模块的"逐视图取证"。）
#
#  取值依据：swconst.tlb 的 `swStandardViews_e` 枚举【恰好有 9 个成员】
#    （值 1..9，与官方标准视图一一对应：
#      前/后/左/右/上/下/等轴测/上下二等轴测/左右二等轴测）。
#
#  ★★ 二次确证（2026-10-12，非对称零件像素级反推）★★
#    为排除"只是图变了、但视角映射错位"的可能，另造了一个
#    **非对称**长方体 120(X) × 40(Y) × 20(Z)，渲染后做像素轮廓测量，
#    用三个视图各自独有的宽高比反推它到底在显示哪两个轴：
#      *Front vid=1 → 实测 px 989×331，比值 2.988  ← 理论 120/40 = 3.0 ✓
#      *Top   vid=5 → 实测 px 989×165，比值 5.994  ← 理论 120/20 = 6.0 ✓
#      *Right vid=4 → 实测 px 381×765，比值 0.498  ← 理论  20/40 = 0.5 ✓
#    三者在 0.4% 像素误差内与理论值一一吻合，证明：
#      front = XY 面（宽 X、高 Y）
#      top   = XZ 面（宽 X、高 Z）
#      right = ZY 面（宽 Z、高 Y）
#    这正是 GB/T 标准三视图的正确投影关系，映射无误。
#    （同一套取值在 Ø100×170 的圆柱上也自洽：front=正圆、
#      top=竖长方形、right=横长方形。）
#
#  ⚠️ 未经实测的取值：back=2 / left=3 / bottom=6 是按同一枚举的自然顺序
#    推导的，本模块**未**对它们做像素级反推确认（本次只验收了
#    front/top/right/iso 四个）。若调用方要用这三个视图，
#    **请先按上面的 sha1 / 轮廓比值方法自测一遍**，不要默认它们一定对。
VIEW_ID = {
    "front": 1,
    "back": 2,
    "left": 3,
    "right": 4,
    "top": 5,
    "bottom": 6,
    "iso": 7,
    "isometric": 7,
}

#: 视图名 → 该视图应当显示的「水平轴, 垂直轴」。
#  ★ 依据：2026-10-12 用非对称长方体 120(X)×40(Y)×20(Z) 做像素级
#    轮廓反推确证（详见 VIEW_ID 上方的二次确证记录）：
#      front = XY 面 / top = XZ 面 / right = ZY 面。
#
#  ⚠️ 【命名口径提醒 · 务必知悉】这里沿用 **SolidWorks 自己的视图/基准面
#    命名**，与 swapi.py:5211-5218 的 `_PLANE_VIEW` 完全一致：
#        "Front Plane" → "*Front"   （前视基准面，法向 Z，含 X、Y）
#        "Top Plane"   → "*Top"     （上视基准面，法向 Y，含 X、Z）
#        "Right Plane" → "*Right"   （右视基准面，法向 X，含 Z、Y）
#    判据来源：在 swapi 里 `begin_sketch("Front Plane")` 后画
#      `rect(0,0,120,40)` 并 `extrude(20)`，得到 bbox
#      X=120 / Y=40 / Z=20 —— 说明 Front Plane 确实张成 X、Y 两轴，
#      与上表一致。因此本表是"与 SW 基准面定义自洽"的。
#
#    对习惯了「主视图/俯视图/左视图」严格 GB/T 心智模型的调用方：
#      SW 的 "Front" 是 **XY 投影**。若你的零件把回转轴放在 Z 上
#      （如本工程 DSH_底座立柱），那么 SW 的 front 视图看上去是
#      "一个圆" —— 那是**正确**的 XY 投影，不是拍错。
#      本模块只保证"名字与 SW 基准面定义一致且投影可验算"，
#      不替调用方重新定义视图语义。
VIEW_PLANE_AXES = {
    "front": ("x", "y"),
    "top": ("x", "z"),
    "right": ("z", "y"),
    "back": ("x", "y"),
    "bottom": ("x", "z"),
    "left": ("z", "y"),
}

#: 轮廓比值校验的允许相对误差。
#  像素测量本身有 ±1px 级别误差（本次实测三视图偏差最大 0.4%），
#  5% 足够容纳测量噪声，同时仍能抓出"拿错轴/拍错面"这种量级错误。
SILHOUETTE_RATIO_TOL = 0.05

#: 轮廓像素测量时的背景判定阈值。
#  SW 视口背景是浅色渐变；模型本体是灰色填充 + 洋红高亮边线。
_BG_DARK = 190          # 任一通道低于此值视为"深色像素（模型/边线）"
_MAGENTA_MIN = 180      # 洋红判定：R、B 高
_MAGENTA_G_MAX = 140    # 洋红判定：G 低


def measure_silhouette(path, step=2, max_pixels=4_000_000):
    """测量位图里「模型轮廓」的像素包围盒（用于验算视图投影是否正确）。

    ★ 为什么需要它：光看"三张图 sha1 不一样"只能证明图变了，
      不能证明**拍对了面**。本函数量出轮廓宽高比，配合
      `VIEW_PLANE_AXES` + `body_box_mm()` 就能算出"这个视图理论上
      该是什么比值"，从而把"拍对了"变成可验算的判据。

    判据：SW 视口背景为浅色渐变，模型本体为灰色填充/洋红高亮边线，
      因此把"任一通道明显偏暗"或"明显偏洋红"的像素视为模型像素。

    Args:
        path: 位图路径（PNG/BMP 均可，由 PIL 解码）
        step: 采样步长（2 = 隔一个像素取一个，够精度且快）
        max_pixels: 像素总量上限，超过则自动放大 step，避免大图卡死
    Returns:
        {"ok","px_w","px_h","ratio","box","sampled_step","error"?}
    """
    out = {"ok": False, "px_w": 0, "px_h": 0, "ratio": None, "box": None,
           "sampled_step": step}
    try:
        from PIL import Image           # 可选依赖：PIL 不在就如实失败
    except Exception as e:
        out["error"] = "PIL 不可用，无法做轮廓测量: %r" % (e,)
        return out
    try:
        im = Image.open(path).convert("RGB")
    except Exception as e:
        out["error"] = "打开位图失败: %r" % (e,)
        return out
    W, H = im.size
    total = W * H
    if total > max_pixels:
        step = max(step, int((total / float(max_pixels)) ** 0.5) + 1)
        out["sampled_step"] = step
    px = im.load()
    minx, miny, maxx, maxy = W, H, -1, -1
    for y in range(0, H, step):
        for x in range(0, W, step):
            try:
                r, g, b = px[x, y]
            except Exception:
                continue
            is_magenta = (r > _MAGENTA_MIN and b > _MAGENTA_MIN
                          and g < _MAGENTA_G_MAX)
            is_dark = (r < _BG_DARK and g < _BG_DARK and b < _BG_DARK)
            if is_magenta or is_dark:
                if x < minx:
                    minx = x
                if y < miny:
                    miny = y
                if x > maxx:
                    maxx = x
                if y > maxy:
                    maxy = y
    if maxx < 0:
        out["error"] = "未在图中找到模型轮廓像素（可能是空白图或全背景）"
        return out
    w, h = maxx - minx + 1, maxy - miny + 1
    out.update({"ok": True, "px_w": w, "px_h": h,
                "ratio": round(w / float(h), 4) if h else None,
                "box": [minx, miny, maxx, maxy], "image_size": [W, H]})
    return out


def expected_view_ratio(view, bbox_mm):
    """某视图的【理论】轮廓宽高比 = 水平轴尺寸 / 垂直轴尺寸。

    Returns: float 或 None（视图/包围盒不可用时）。
    """
    axes = VIEW_PLANE_AXES.get(view)
    if not axes or not bbox_mm or len(bbox_mm) != 6:
        return None
    try:
        idx = {"x": 0, "y": 1, "z": 2}
        h_ax, v_ax = axes
        hi = idx[h_ax]
        vi = idx[v_ax]
        h_len = float(bbox_mm[2 * hi + 1]) - float(bbox_mm[2 * hi])
        v_len = float(bbox_mm[2 * vi + 1]) - float(bbox_mm[2 * vi])
        if v_len <= 0 or h_len <= 0:
            return None
        return round(h_len / v_len, 4)
    except Exception:
        return None


def check_view_projection(views, bbox_mm, tol=None):
    """★ 验算「三视图真的拍对了面」—— 而不只是「三张图不一样」。

    原理：对每个视图量出轮廓像素宽高比，与
      `VIEW_PLANE_AXES` 指定的投影面理论比值（由真实包围盒算出）比对。
      例如 120×40×20 的长方体：front 应 ≈3.0、top 应 ≈6.0、right 应 ≈0.5。
      若视图拍错了面（例如把上视当前视），比值会明显对不上 → 判失败。

    ★ 这是对"三视图"最实质的校验：杜绝"三张图各不相同、但全都不是
      正确的三视图"这种更隐蔽的假成功。

    Args:
        views: capture_views 返回的 `views` 字典（每项含 path）
        bbox_mm: 真实包围盒六元组（来自 read_actual_geometry）
        tol: 允许相对误差，默认 SILHOUETTE_RATIO_TOL
    Returns:
        {"ok","verified","tolerance","checked":[...],"failed":[...],
         "skipped":[...],"note"}
        ★ `ok`       = 验算做过且【全部通过】
        ★ `verified` = 验算是否真的做成了（至少一个视图完成比对且无失败）
          —— 用来区分「验算失败（拍错面）」与「压根没验成（缺包围盒/
          PIL 不可用/退化尺寸）」。这两者绝不能混为一谈：
          前者要拦，后者应如实标注"未验算"而不是判失败。
    """
    tol = SILHOUETTE_RATIO_TOL if tol is None else float(tol)
    out = {"ok": False, "verified": False, "tolerance": tol,
           "checked": [], "failed": [], "skipped": [], "note": ""}
    if not views:
        out["note"] = "没有视图可验算"
        return out
    if not bbox_mm or len(bbox_mm) != 6:
        out["note"] = "没有真实包围盒，无法算出理论比值 —— 跳过投影验算"
        return out
    for v, rec in views.items():
        if v not in VIEW_PLANE_AXES:
            out["skipped"].append({"view": v, "why": "等轴测/无投影面定义，不适用"})
            continue
        path = (rec or {}).get("path")
        exp = expected_view_ratio(v, bbox_mm)
        if exp is None:
            out["skipped"].append({"view": v, "why": "算不出理论比值（退化尺寸）"})
            continue
        ms = measure_silhouette(path) if path and os.path.exists(path) else \
            {"ok": False, "error": "文件不存在: %s" % path}
        if not ms.get("ok"):
            out["skipped"].append({"view": v, "why": ms.get("error")})
            continue
        got = ms.get("ratio")
        try:
            rel = abs(got - exp) / exp if (got and exp) else None
        except Exception:
            rel = None
        item = {"view": v, "axes": list(VIEW_PLANE_AXES[v]),
                "expected_ratio": exp, "measured_ratio": got,
                "px_w": ms["px_w"], "px_h": ms["px_h"],
                "rel_error": (round(rel, 4) if rel is not None else None),
                "within_tolerance": (rel is not None and rel <= tol)}
        (out["checked"] if item["within_tolerance"]
         else out["failed"]).append(item)
    out["ok"] = bool(out["checked"]) and not out["failed"]
    out["verified"] = bool(out["checked"]) or bool(out["failed"])
    if out["ok"]:
        out["note"] = ("%d 个视图的轮廓宽高比与理论投影一致 —— "
                       "三视图确实拍对了面。" % len(out["checked"]))
    elif out["failed"]:
        out["note"] = ("以下视图的轮廓比例与理论投影不符，疑似拍错面：%s"
                       % "、".join("%s(实测%s/理论%s)"
                                   % (x["view"], x["measured_ratio"],
                                      x["expected_ratio"])
                                   for x in out["failed"]))
    else:
        out["note"] = ("未能完成投影验算（%d 个视图被跳过）—— "
                       "如实标注为「未验算」，不等于验算失败。"
                       % len(out["skipped"]))
        out["verified"] = False
    return out


def _is_orthographic(view):
    """该视图是否属于正投影视图（等轴测不参与轮廓比例验算）。"""
    return view in VIEW_PLANE_AXES


#: 视图名 → 截图文件名的中文标签（文件名可读性）。
VIEW_LABEL_CN = {
    "front": "前视", "back": "后视", "left": "左视", "right": "右视",
    "top": "上视", "bottom": "下视", "iso": "等轴测",
}

#: 文件名里禁止出现的字符（Windows 非法字符 + 路径分隔符）。
_FS_BAD_CHARS = '<>:"/\\|?*'

# ★ 几何比对容差（mm）。包围盒六元组的每一个面都要求 |实测 - 期望| <= 此值。
#   取 0.05mm 的理由：本工程口径是 mm，SW 建模脚本的坐标本身就是
#   0.01mm 量级的十进制数，0.05mm 足够吸收浮点/换算噪声，
#   同时远小于任何一个真实设计尺寸误差（不可能"把 5mm 的偏差放过去"）。
BBOX_TOL_MM = 0.05

# ★ 闭环默认最大轮数（用户口径：看图→矫正→继续看→继续矫正）。
MAX_ROUNDS_DEFAULT = 3

# ★ 硬上限：防止上游传 max_rounds=10000 把闭环变成"无限重试"，绕过纠偏语义。
MAX_ROUNDS_HARD_CAP = 10

# ★ 截图文件必须大于这个字节数才算"真的截到了图"。
#   0 字节当然是假的；这里再往上抬一点，拦掉"只写了 PNG 魔数/文件头"的
#   假截图（真实 SW 截图都是几十 KB 起步）。
MIN_SHOT_BYTES = 64

# ★ 切完视图后、截图前必须等待的秒数。
#   实机教训（2026-10-12，SW2025 SP5.0）：ShowNamedView2 之后立刻调
#   ViewZoomtofit2 再立刻截图，SW 可能还没重绘完 —— 抓到的取景是
#   过渡帧（零件被放到巨大/偏离），虽然文件非空、swapi 也报 ok=True，
#   但那张图对"看形状"没有价值。留一个可调的静置时间。
VIEW_SETTLE_SEC = 0.8

# ★ 截图前等待 SW 窗口尺寸稳定的参数。
#  实机教训（2026-10-12）：get_sw() 会调 _show_main_window(maximize=True)，
#  窗口最大化动画期间抓屏，会得到与后续不一致的尺寸 —— 实测同一轮里
#  第一张图是 1707x1019、后三张是 2560x1528，且第一张的取景是错的。
#  这里在截图前轮询窗口矩形，连续 STABLE_POLLS 次不变才认为稳定。
WINDOW_STABLE_POLLS = 3
WINDOW_STABLE_TIMEOUT_SEC = 8.0

# ★ 是否在首次截图前强制把 SW 主窗口最大化并等其稳定。
#  实机实测（2026-10-12）：SW 启动后主窗口先是 1707x1019（非最大化），
#  只有等到某次交互之后才变成 2560x1528（最大化）。若不等，
#  第一张图必然是"未最大化窗口 + 旧取景"，对看模型毫无价值。
MAXIMIZE_BEFORE_SHOT = True

#: 判定取值
VERDICT_PASS = "PASS"
VERDICT_MISMATCH = "MISMATCH"
VERDICT_FAIL = "FAIL"          # 轮次用尽仍不一致（与 PASS 对称）
VERDICT_ERROR = "ERROR"        # 连几何/截图都取不到（比 MISMATCH 更早的失败）

#: 证据文件名（闭环留痕 / 强制门证据）
LOOP_FILE = "visual_loop.json"
GATE_FILE = "visual_gate.json"


# ═══════════════════════════════════════════════════════════════════════════
# 二、可选依赖（一律 try/except；不可用时降级并【如实标注】）
# ═══════════════════════════════════════════════════════════════════════════
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

_SWAPI = None
_SWAPI_ERR = None
_MODE2 = None
_MODE2_ERR = None


def _try_import_swapi():
    """尝试加载 swapi（SolidWorks 桥接）。返回 (module_or_None, error_or_None)。"""
    global _SWAPI, _SWAPI_ERR
    if _SWAPI is not None or _SWAPI_ERR is not None:
        return _SWAPI, _SWAPI_ERR
    try:
        import swapi as _m
        _SWAPI = _m
        _SWAPI_ERR = None
    except Exception as e:                       # pragma: no cover - 环境相关
        _SWAPI = None
        _SWAPI_ERR = "%s: %s" % (type(e).__name__, e)
    return _SWAPI, _SWAPI_ERR


def _try_import_mode2():
    """尝试加载 mode2_flow（模式二强制契约）。

    ⚠️ mode2_flow / part_registry 正在被其他子代理修改，
    这里【只读导入】且用 try/except 包住 —— 不在时本模块必须照常工作。
    """
    global _MODE2, _MODE2_ERR
    if _MODE2 is not None or _MODE2_ERR is not None:
        return _MODE2, _MODE2_ERR
    try:
        import mode2_flow as _m
        _MODE2 = _m
        _MODE2_ERR = None
    except Exception as e:                       # pragma: no cover - 环境相关
        _MODE2 = None
        _MODE2_ERR = "%s: %s" % (type(e).__name__, e)
    return _MODE2, _MODE2_ERR


# ═══════════════════════════════════════════════════════════════════════════
# 三、通用小工具
# ═══════════════════════════════════════════════════════════════════════════
def _now_str():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _safe_name(text, fallback="part"):
    """把零件名/视图名清洗成可安全用作文件名的字符串。"""
    s = str(text or "").strip()
    for ch in _FS_BAD_CHARS:
        s = s.replace(ch, "_")
    s = s.replace(" ", "_").strip("._")
    return s or fallback


def atomic_write_json(path, data):
    """原子写 JSON：先写临时文件再 os.replace（避免并发读到半个文件）。

    写法与 mode2_flow._atomic_write_json（第 166~171 行）保持一致。
    """
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = "%s.tmp.%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def read_json(path):
    """读 JSON（兼容 BOM）。失败返回 (None, 错误说明)。"""
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f), None
    except Exception as e:
        return None, "%s: %s" % (type(e).__name__, e)


def _file_sha1(path, limit=None):
    """算文件 sha1（limit 非空时只算前 limit 字节），失败返回 None。

    ══ 【实机事故·不要用 limit 做"内容是否相同"的判据】══════════════════════
    本函数原本被 file_probe 以 `limit=1<<20`（只哈希前 1MB）调用，用来给
    "重复视图检测"提供指纹。实测（2026-10-12，DSH_悬臂支架）证明这会【误报】：

      SaveBMP 产出的 BMP 是 4320054 字节，而 **BMP 像素按【自底向上】存储** ——
      文件前段是文件头 + 图像【底部】的行。front 与 top 两张图虽然整体
      完全不同（全文件 sha1 分别 55c0009dec75 / 52951e5ae8d1），
      但它们底部都是空白区域，于是：
          全文件 sha1   : front=55c0009dec75  top=52951e5ae8d1   ← 不同（正确）
          前 1MB sha1   : front=350a1b10f2db  top=350a1b10f2db   ← 【相同】（假阳性）
      结果"重复视图检测"把一个**完全正常的采集**判成了失败，
      错误信息还误导成"ViewId 传了 0"（而 ViewId 其实是对的）。

    结论：`limit` 只适合"快速取一个变更指纹"（如文件是否被碰过），
      **绝不能**用于"两张图内容是否相同"的判定 —— 那必须覆盖整幅像素。
    """
    try:
        h = hashlib.sha1()
        with open(path, "rb") as f:
            if limit:
                h.update(f.read(limit))
            else:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def file_probe(path):
    """探测一个"截图文件"的真实存在性 —— 这是 verify 的最小单元。

    Returns:
        {"path","exists","size_bytes","is_file","sha1_12","sha1_full_12","ok","why"}
        ok=True 仅当：路径存在 + 是普通文件 + 字节数 > MIN_SHOT_BYTES。

    ★ 两个指纹的分工（不要混用 —— 混用会误报，实测已栽过）：
        · `sha1_12`      = 前 1MB 的指纹。**只**用于"这个文件后来被改过吗"
                           一类轻量判断，快但**不能**代表整幅图像内容。
        · `sha1_full_12` = **全文件**指纹。用于"两张图内容是否相同"
                           （重复视图检测必须用它）。
    """
    out = {"path": path, "exists": False, "size_bytes": 0,
           "is_file": False, "sha1_12": None, "sha1_full_12": None,
           "ok": False, "why": ""}
    if not path or not isinstance(path, str):
        out["why"] = "路径为空或类型错误（%r）" % (path,)
        return out
    p = os.path.abspath(path)
    out["path"] = p
    try:
        out["exists"] = os.path.exists(p)
        out["is_file"] = os.path.isfile(p)
        if out["is_file"]:
            out["size_bytes"] = int(os.path.getsize(p))
            out["sha1_12"] = (_file_sha1(p, limit=1 << 20) or "")[:12] or None
            # 全文件指纹：重复视图检测依赖它（前 1MB 会假阳性，见 _file_sha1 注释）
            out["sha1_full_12"] = (_file_sha1(p) or "")[:12] or None
    except Exception as e:
        out["why"] = "文件探测异常: %s: %s" % (type(e).__name__, e)
        return out
    if not out["exists"]:
        out["why"] = "文件不存在于磁盘（可能是「假装截了图」）"
        return out
    if not out["is_file"]:
        out["why"] = "路径不是普通文件（可能是目录/符号链接）"
        return out
    if out["size_bytes"] <= 0:
        out["why"] = "文件是 0 字节（空文件，等于没截到图）"
        return out
    if out["size_bytes"] <= MIN_SHOT_BYTES:
        out["why"] = ("文件只有 %d 字节，小于 %d 字节下限 —— "
                      "极可能不是真实截图" % (out["size_bytes"], MIN_SHOT_BYTES))
        return out
    # ── ★ 内容格式校验：必须是真实位图，不能是随便写的填充字节 ──────────
    # 支持的两种取证通道产物：PNG（屏幕抓取）/ BMP（SW 内置渲染）。
    # 这一步拦掉「往文件里写点垃圾冒充截图」。
    try:
        with open(p, "rb") as f:
            head = f.read(16)
    except Exception as e:
        out["why"] = "读取文件头失败: %r" % (e,)
        return out
    out["magic"] = head[:4].hex()
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        out["format"] = "PNG"
    elif head.startswith(b"BM"):
        out["format"] = "BMP"
    elif head.startswith(b"\xff\xd8\xff"):
        out["format"] = "JPEG"
    elif head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        out["format"] = "WEBP"
    else:
        out["why"] = ("文件头 magic=%s 不是已知位图格式（PNG/BMP/JPEG/WEBP）"
                      "—— 怀疑是伪造的截图文件" % head[:4].hex())
        return out
    out["ok"] = True
    out["why"] = "文件真实存在、大小合格、且是合法 %s 位图" % out["format"]
    return out


def _default_out_dir(part_path=None, tag="visual"):
    """默认输出目录：工作目录/visual_check/<零件名>_<时间戳>。"""
    base = os.getcwd()
    stem = _safe_name(os.path.splitext(os.path.basename(part_path or ""))[0]
                      or tag)
    out = os.path.join(base, "visual_check", "%s_%s" % (stem, time.strftime("%Y%m%d_%H%M%S")))
    os.makedirs(out, exist_ok=True)
    return out


# ═══════════════════════════════════════════════════════════════════════════
# 四、A. 三视图截图采集
# ═══════════════════════════════════════════════════════════════════════════
def _normalize_views(views=None):
    """把 views 参数规整为合法视图名列表（保序去重）。

    非法视图名【直接拒绝】而不是静默忽略 —— 静默忽略会让调用方以为
    「我要的图都截到了」，实际少了一张，正是本模块要杜绝的假成功。
    """
    if views is None:
        return list(DEFAULT_VIEWS), []
    if isinstance(views, str):
        raw = [v.strip() for v in views.split(",") if v.strip()]
    else:
        raw = [str(v).strip() for v in views if str(v).strip()]
    good, bad = [], []
    for v in raw:
        key = v.lower()
        if key in VIEW_VNAME:
            key = "iso" if key == "isometric" else key
            if key not in good:
                good.append(key)
        else:
            bad.append(v)
    return good, bad


def _shot_path(out_dir, part_path, view, round_no=None):
    """截图落盘的基础路径：DSH_<零件名>__<视图>.png（文件名可区分视图）。

    ⚠️ 这只是【基础路径】。真实扩展名由取证通道决定：
      · SaveBMP 通道 → 实际写 .bmp（且 SaveBMP 会忽略传入的扩展名，
        只认 .bmp 才不会张冠李戴）；
      · 屏幕抓取通道 → .png。
    调用方必须采信 `_grab_screenshot` 回的 `path`，不要想当然用这个基础路径。

    round_no 非空时落到 out_dir/round_<N>/ 子目录 ——
    ★ 这是必须的：闭环每轮都要重新截图，如果共用同一个文件名，
      第 2 轮会把第 1 轮的图【覆盖】掉，于是 round 1 的记录指向的磁盘文件
      其实是 round 2 的内容 —— 证据链被悄悄篡改，而 verify 还查不出来。
      分轮目录让每轮证据各自独立、可复核。
    """
    stem = _safe_name(os.path.splitext(os.path.basename(part_path or ""))[0]
                      or "part")
    # 零件名本身常已带 DSH_ 前缀（本工程默认前缀就是 DSH_）——
    # 再拼一次会得到 DSH_DSH_xxx 这种重复前缀，文件名难看且不利于检索。
    if not stem.upper().startswith("DSH_"):
        stem = "DSH_" + stem
    base = out_dir
    if round_no is not None:
        base = os.path.join(out_dir, "round_%d" % int(round_no))
        try:
            os.makedirs(base, exist_ok=True)
        except Exception:
            base = out_dir          # 建不了子目录就退回原目录（不致命）
    return os.path.join(base, "%s__%s.png" % (stem, view))


def _apply_view(m, view):
    """★ 真正驱动 SolidWorks 切到指定视图。

    【核实过的调用链（不是猜的）】
      · swapi.py:5076-5090 `set_view_iso()` → `self.model.ShowNamedView2(
            VIEW_ISO_NAME, VIEW_ISO_ID)`，即 `ShowNamedView2("*Isometric", 7)`，
        随后 `ViewZoomtofit2()` + `ActiveView.ZoomByFactor(1.2)`。
      · swapi.py:5220-5230 `_normal_to(view_name)` → `self.model.ShowNamedView2(
            view_name, 0)` 再 `ViewZoomtofit2()`。
      · swapi.py:5211-5218 `_PLANE_VIEW` 给出 "*Front"/"*Top"/"*Right" 等 VName。

    本函数按上面既有写法实现：等轴测走 set_view_iso()；其余标准视图
    走 ShowNamedView2(vname, VIEW_ID[view]) + ViewZoomtofit2()。

    ★ 注意：这里【不照抄】swapi `_normal_to` 的 vid=0 —— 实机实测证明
      vid=0 是无效调用，图像不会切换（详见 VIEW_ID 上的实测记录）。
    """
    vname = VIEW_VNAME[view]
    vid = VIEW_ID.get(view)
    res = {"view": view, "vname": vname, "view_id": vid,
           "method": None, "zoom_ok": False, "error": None}
    model = getattr(m, "model", None)
    if vid is None:
        res["error"] = ("视图 %r 没有经过实测的 ViewId —— 拒绝用 0 兜底"
                        "（实测 vid=0 不切换视角，会得到重复的假三视图）"
                        % view)
        return res

    # ── ① 等轴测：直接用 swapi 已验证过的 set_view_iso() ──────────────
    if view == "iso" and hasattr(m, "set_view_iso"):
        try:
            m.set_view_iso()
            res["method"] = "SWModel.set_view_iso() [swapi.py:5076]"
            res["zoom_ok"] = True
            res["window"] = wait_window_stable(m)
            _settle()
            return res
        except Exception as e:
            res["error"] = "set_view_iso 失败: %r" % (e,)
            # 失败则继续往下走通用路径

    # ── ② 通用路径：ShowNamedView2(vname, vid) + ViewZoomtofit2() ─────
    if model is None:
        res["error"] = "SWModel.model 为空，无法切换视图"
        return res
    try:
        model.ShowNamedView2(vname, vid)
        res["method"] = "IModelDoc2.ShowNamedView2(%r, %d)" % (vname, vid)
    except Exception as e:
        res["error"] = "ShowNamedView2(%r, %d) 失败: %r" % (vname, vid, e)
        return res
    # 居中缩放：与 swapi `_normal_to`（5226-5229 行）同一口径 —— 只做
    # ViewZoomtofit2，**不**再叠加 ZoomByFactor。理由（实机教训）：
    #   叠加 1.2 倍会把零件放到超出视口，三视图就看不到完整轮廓了，
    #   而"看完整形状"正是本闭环的目的。
    #
    # ── 【实机修复】Zoomtofit 必须连做两次、中间静置 ────────────────────
    # 实机现象（2026-10-12）：打开文档后【第一张】视图常常取景不对
    #   （零件被放大到只剩局部，或仍是切换过程中的过渡帧），
    #   而后面的视图正常。根因是 SW 首次 ViewZoomtofit2 时还在重建/重绘，
    #   缩放基于旧的视口范围算出来。
    # 修复：先静置等重建完，再连做两次 zoom-to-fit（第二次基于已稳定的
    #   视口重新算），每次之间静置。这对第一张图尤其关键。
    _settle()
    zok = False
    zerr = None
    for _try in range(2):
        try:
            model.ViewZoomtofit2()
            zok = True
        except Exception as e:
            zerr = repr(e)
            try:
                model.ActiveView.ZoomByFactor(0.9)   # swapi.py:5098 兜底口径
                zok = True
                res["zoom_fallback"] = "ActiveView.ZoomByFactor(0.9) [swapi.py:5098]"
            except Exception as e2:
                zerr = "%r / %r" % (e, e2)
        _settle()
    res["zoom_ok"] = zok
    if zerr and not zok:
        res["zoom_error"] = zerr
    # 缩放后等窗口尺寸稳定再截图（避免抓到窗口最大化动画的过渡帧）
    res["window"] = wait_window_stable(m)
    _settle()                      # 再静置，保证截到的是终态取景
    return res


def _settle(seconds=None):
    """静置等待 SW 重绘（失败绝不抛异常）。"""
    try:
        time.sleep(VIEW_SETTLE_SEC if seconds is None else float(seconds))
    except Exception:
        pass


def wait_window_stable(model, timeout=None, polls=None):
    """确保 SolidWorks 主窗口【已最大化且尺寸稳定】，然后才允许截图。

    实机教训（2026-10-12，SW2025 SP5.0）：
      · `swapi.get_sw()` 最后会调 `_show_main_window()`，但 SW 主窗口
        在刚启动的一段时间里仍是 1707x1019（未最大化），之后才变成
        2560x1528（最大化）。最大化动画期间抓屏 → 得到尺寸异常、
        取景错误的图（实测同一轮第一张 1707x1019、后三张 2560x1528，
        且第一张根本没有正确显示零件轮廓）。
      · 只看"矩形连续 N 次不变"不够 —— 未最大化的窗口本身也是稳定的，
        于是"稳定"判定通过，却仍抓到旧取景。
    因此本函数做两件事：
      ① MAXIMIZE_BEFORE_SHOT 时主动 ShowWindow(SW_MAXIMIZE) 并等
         IsZoomed() 为真；
      ② 再轮询窗口矩形，连续 WINDOW_STABLE_POLLS 次不变才算稳定。

    Returns: {"stable","maximized","rect","waited_s","reason",...}
    """
    out = {"stable": False, "maximized": None, "rect": None,
           "waited_s": 0.0, "reason": ""}
    if model is None:
        out["reason"] = "model 为空，跳过窗口稳定检查"
        return out
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
    except Exception as e:
        out["reason"] = "无法加载 user32（非 Windows？）: %r" % (e,)
        return out
    hwnd = None
    # ── 【实机修复】必须盯【主窗口】，不能盯视口窗口 ─────────────────────
    # 实测（2026-10-12）：`ActiveView.GetViewHWnd`（视口）在一段时间内
    # 稳定在 1707x870，但 swapi 截图取的是【主窗口】矩形 —— 主窗口此时
    # 还没被最大化（1707x1019），过一会儿才变成 2560x1528。
    # 盯错窗口 → "稳定"判定通过，却仍抓到未最大化时的旧取景。
    # 因此用 swapi 自己的 `_find_main_hwnd()`（swapi.py:2741）定位主窗口，
    # 保证"等稳定"和"截图"盯的是同一个窗口。
    swapi, _err = _try_import_swapi()
    if swapi is not None:
        try:
            _main = swapi._find_main_hwnd()
            hwnd = int(_main) if _main else None
            out["hwnd_source"] = "swapi._find_main_hwnd() [swapi.py:2741]"
        except Exception as e:
            out["hwnd_source_error"] = repr(e)
    if not hwnd:
        try:
            hwnd = int(model.model.ActiveView.GetViewHWnd)
            out["hwnd_source"] = "ActiveView.GetViewHWnd（视口，兜底）"
        except Exception:
            try:
                hwnd = int(model.model.ActiveView.GetViewHWnd())
                out["hwnd_source"] = "ActiveView.GetViewHWnd（视口，兜底）"
            except Exception:
                hwnd = None
    out["hwnd"] = hwnd
    if not hwnd:
        out["reason"] = "拿不到主窗口 HWND，跳过窗口稳定检查"
        return out
    # ── ① 主动最大化（未最大化时第一张图必然是错的取景）──────────────
    if MAXIMIZE_BEFORE_SHOT:
        try:
            out["was_zoomed"] = bool(user32.IsZoomed(hwnd))
            if not out["was_zoomed"]:
                user32.ShowWindow(hwnd, 9)      # SW_RESTORE（swapi.py:2808 同款）
                user32.ShowWindow(hwnd, 3)      # SW_MAXIMIZE（swapi.py:2809 同款）
                _mz0 = time.time()
                while time.time() - _mz0 < tmo:
                    if user32.IsZoomed(hwnd):
                        break
                    time.sleep(0.2)
                out["maximize_waited_s"] = round(time.time() - _mz0, 2)
            out["maximized"] = bool(user32.IsZoomed(hwnd))
        except Exception as e:
            out["maximized"] = None
            out["maximize_error"] = repr(e)
    tmo = WINDOW_STABLE_TIMEOUT_SEC if timeout is None else float(timeout)
    need = WINDOW_STABLE_POLLS if polls is None else int(polls)
    t0 = time.time()
    last = None
    same = 0
    while time.time() - t0 < tmo:
        try:
            rc = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rc))
            cur = (rc.left, rc.top, rc.right, rc.bottom)
        except Exception:
            break
        if cur == last:
            same += 1
            if same >= need:
                out["stable"] = True
                out["rect"] = list(cur)
                out["waited_s"] = round(time.time() - t0, 2)
                out["reason"] = "窗口尺寸已连续 %d 次不变" % need
                return out
        else:
            same = 0
            last = cur
        time.sleep(0.25)
    out["rect"] = list(last) if last else None
    out["waited_s"] = round(time.time() - t0, 2)
    out["reason"] = ("等待 %.1fs 仍未观测到窗口尺寸稳定（继续截图，"
                     "但该图尺寸可能处于窗口变形过程中）" % out["waited_s"])
    return out


#: 截图窗口标题里必须出现的关键字（不区分大小写）。
#  ★ 这是本模块对 swapi 返回值的【独立复核】，不是重复劳动 ——
#  实机教训（2026-10-12）：swapi.screenshot 返回 ok=True 且
#  verified_foreground=True，但那张图里【同时叠着 DSH Web GUI】，
#  因为 DSH GUI 是置顶窗口，抓屏把两边的像素都抓进来了。
#  靠 swapi 的 ok 字段无法发现这种情况；这里用窗口标题做二次判据：
#  标题里必须出现 SOLIDWORKS，否则我们无法确认这张图拍的是 SW。
SHOT_TITLE_MUST_CONTAIN = "SOLIDWORKS"


def _shot_is_trustworthy(shot):
    """独立复核一张截图是否可信 —— 不看 swapi 的 ok，只看硬证据。

    Returns: (trustworthy: bool, why: str)
    """
    if not isinstance(shot, dict):
        return False, "screenshot 返回不是对象"
    if not shot.get("ok"):
        return False, "screenshot 自报 ok=False"
    title = str(shot.get("window_title") or "")
    if not title:
        return False, ("screenshot 未回报 window_title —— 无法确认拍的是 "
                       "SolidWorks 窗口（可能是别的窗口叠在上面）")
    if SHOT_TITLE_MUST_CONTAIN.lower() not in title.lower():
        return False, ("screenshot 的 window_title=%r 里不含 %r —— "
                       "疑似抓到了别的窗口（如 DSH GUI 叠在 SW 上）"
                       % (title, SHOT_TITLE_MUST_CONTAIN))
    return True, "窗口标题含 %r，确认拍的是 SolidWorks" % SHOT_TITLE_MUST_CONTAIN


def _grab_via_savebmp(m, path, width=1600, height=900):
    """★ 首选路径：用 SolidWorks 内置渲染导出位图（SaveBMP），不经屏幕抓取。

    【核实来源】swapi.py:5182-5192 `export_image()` 就是这个调用：
        self.zoom_to_fit(); ok = self.model.SaveBMP(path, width, height)

    为什么首选它（实机教训，2026-10-12）：
      swapi.screenshot 是【屏幕抓取】—— DSH GUI 置顶叠在 SW 上时，
      抓到的图里同时有 DSH 界面；窗口标题检查只能发现「完全抓错窗口」，
      无法发现「标题对但内容被遮挡」。SaveBMP 由 SW 自己渲染，
      **完全没有遮挡问题**，是更可靠的取证方式。

    ⚠️ 两个必须处理的真实陷阱（都已实测确认）：
      ① **SaveBMP 忽略扩展名**：传 .png / .jpg 路径，写出来的仍是
         BMP 字节（实测三种扩展名产出的文件字节数完全相同、
         magic 都是 42 4D = 'BM'）。因此本模块**只传 .bmp 路径**，
         并在落盘后校验 magic，绝不把 BMP 字节冒充成 PNG。
      ② 它依赖当前缩放/视图状态，所以调用方需先切好视图并 Zoomtofit。

    Returns: {"ok","path","size_bytes","sha1_12","via","error"?}
    """
    out = {"ok": False, "path": path,
           "via": "SWModel.model.SaveBMP [与 swapi.py:5189 同款调用]"}
    model = getattr(m, "model", None)
    if model is None:
        out["error"] = "SWModel.model 为空，无法 SaveBMP"
        return out
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass
    try:
        ret = model.SaveBMP(path, int(width), int(height))
    except Exception as e:
        out["error"] = "SaveBMP 抛异常: %r" % (e,)
        return out
    out["savebmp_return"] = ret
    probe = file_probe(path)
    out["size_bytes"] = probe["size_bytes"]
    out["sha1_12"] = probe["sha1_12"]
    # ★ 全文件指纹：重复视图检测必须用它（前 1MB 在 BMP 上会假阳性）
    out["sha1_full_12"] = probe.get("sha1_full_12")
    if not probe["ok"]:
        out["error"] = "SaveBMP 未产出可用文件: %s" % probe["why"]
        return out
    # ★ 校验真实文件格式：必须是 BMP（magic 'BM' = 42 4D）
    try:
        with open(path, "rb") as f:
            magic = f.read(2)
    except Exception as e:
        out["error"] = "读取导出文件失败: %r" % (e,)
        return out
    out["magic"] = magic.hex()
    if magic != b"BM":
        out["error"] = ("SaveBMP 产出的文件 magic=%s 不是 BMP —— "
                        "格式不符合预期，拒绝采用" % magic.hex())
        return out
    out["ok"] = True
    out["format"] = "BMP（SolidWorks 内置渲染，无屏幕遮挡问题）"
    return out


def _grab_screenshot(m, path, prefer_savebmp=True):
    """截取当前视图到 path，返回统一的证据结构。

    两条路径，**优先 SaveBMP（SW 自渲染，无遮挡风险）**：
      ① SaveBMP（`_grab_via_savebmp`）→ 产出 .bmp；
      ② 回退 swapi.screenshot 屏幕抓取 → 产出 .png，
         并做窗口标题独立复核（`_shot_is_trustworthy`）。

    【核实过的返回结构（swapi.py:5103-5180 screenshot）】
      成功 → {"ok": True, "path", "size": "WxH", "activated",
              "verified_foreground", "window_title", "note"}
      失败 → {"ok": False, "error": ...}（可能带 "activation"/"hint"）
    注意：swapi 在无法把 SW 激活到前台时【故意返回 ok=False】，
    因为它宁愿失败也不愿抓到 DSH GUI 的画面 —— 本模块必须尊重这个语义，
    把 ok=False 如实上报，绝不兜底成「假装成功」。
    """
    # ── ① 首选：SW 内置渲染导出 ───────────────────────────────────────
    if prefer_savebmp:
        bmp_path = os.path.splitext(path)[0] + ".bmp"
        r = _grab_via_savebmp(m, bmp_path)
        r["trustworthy"] = bool(r.get("ok"))
        r["trust_note"] = ("SaveBMP 由 SolidWorks 自己渲染，"
                           "不存在窗口遮挡问题" if r.get("ok")
                           else r.get("error"))
        if r.get("ok"):
            r["source"] = "savebmp"
            return r
        fallback_reason = r.get("error")
    else:
        fallback_reason = "调用方要求跳过 SaveBMP"

    # ── ② 回退：swapi 屏幕抓取 ────────────────────────────────────────
    try:
        r = m.screenshot(path=path)
    except Exception as e:
        return {"ok": False, "error": "screenshot 抛异常: %r" % (e,),
                "source": "screenshot", "savebmp_error": fallback_reason,
                "via": "SWModel.screenshot [swapi.py:5103]"}
    if not isinstance(r, dict):
        return {"ok": False, "error": "screenshot 返回非 dict: %r" % (r,),
                "source": "screenshot", "savebmp_error": fallback_reason,
                "via": "SWModel.screenshot [swapi.py:5103]"}
    r = dict(r)
    r["source"] = "screenshot"
    r["savebmp_error"] = fallback_reason
    r.setdefault("via", "SWModel.screenshot [swapi.py:5103]")
    # ── ★ 独立复核：即使 swapi 说 ok=True，也要确认拍的是 SW 窗口 ──────
    # 实机教训：DSH GUI 置顶时，swapi 会返回 ok=True/verified_foreground=True
    # 但图里叠着 DSH 界面。那种图无法用来"看模型建成什么样"，
    # 因此这里把它降级为不可信，并附上原因供上层判断。
    ok_trust, why = _shot_is_trustworthy(r)
    r["trustworthy"] = bool(ok_trust)
    r["trust_note"] = why
    if r.get("ok") and not ok_trust:
        r["swapi_ok"] = True
        r["ok"] = False
        r["error"] = ("swapi 自报截图成功，但独立复核判定不可信：%s" % why)
        r["hint"] = ("请把 SolidWorks 窗口置于最前（不要被 DSH GUI 等窗口遮挡）"
                     "后重试；本模块宁可判失败也不接受一张看不清模型的图。"
                     "（SaveBMP 路径也不可用：%s）" % fallback_reason)
    return r


def _open_part_model(part_path):
    """打开零件/装配体，返回 (SWModel, meta, error)。

    失败一律返回 (None, meta, 错误说明) —— 调用方据此返回结构化失败，
    **绝不**编造截图路径。装配体/零件按扩展名区分 doc_type：
      .sldprt=1 / .sldasm=2 / .slddrw=3（swapi.py:12252 同一张映射表）。
    """
    meta = {"part": os.path.abspath(part_path) if part_path else None,
            "opened": False, "sw_available": False, "doc_type": None,
            "via": None, "swapi_error": None}
    if not part_path:
        return None, meta, "part_path 为空"
    if not os.path.exists(part_path):
        return None, meta, "零件文件不存在: %s" % part_path
    swapi, err = _try_import_swapi()
    if swapi is None:
        meta["swapi_error"] = err
        return None, meta, ("swapi 不可用（%s）—— 无 SolidWorks/无 pywin32 时"
                            "无法采集真实截图" % err)
    ext = os.path.splitext(part_path)[1].lower()
    doc_type = {".sldprt": 1, ".sldasm": 2, ".slddrw": 3}.get(ext, 1)
    meta["doc_type"] = doc_type
    try:
        sw = swapi.get_sw()
    except Exception as e:
        return None, meta, "连接 SolidWorks 失败: %r" % (e,)
    meta["sw_available"] = True
    try:
        r = swapi.SWModel.open_part(sw, os.path.abspath(part_path), doc_type)
    except Exception as e:
        return None, meta, "open_part 抛异常: %r" % (e,)
    meta["via"] = "SWModel.open_part(sw, path, doc_type) [swapi.py:12235]"
    if not isinstance(r, dict) or not r.get("ok"):
        return None, meta, "打开文档失败: %s" % (
            (r or {}).get("error") or r)
    doc = r.get("doc")
    if doc is None:
        return None, meta, "open_part 返回 ok 但 doc 为空"
    meta["opened"] = True
    meta["title"] = r.get("title")
    try:
        m = swapi.SWModel(sw, doc)
    except Exception as e:
        return None, meta, "构造 SWModel 失败: %r" % (e,)
    return m, meta, None


def capture_views(part_path, out_dir, views=None, model=None, round_no=None,
                  verify_projection=False, expect_bbox_mm=None):
    """采集三视图（+等轴测）截图。

    Args:
        part_path: 零件/装配体路径（.sldprt / .sldasm）
        out_dir:    截图落盘目录（不存在会自动创建）
        views:      视图列表，默认 ["front","top","right","iso"]
        model:      【可选】已打开的 SWModel。闭环每轮复用它，避免重复
                    OpenDoc 把同一零件打开多次（既慢又可能让 SW 弹窗）。
                    为空时本函数自行打开。
        round_no:   【可选】轮次号。非空时截图落到 out_dir/round_<N>/，
                    保证多轮证据互不覆盖。
        verify_projection: 【可选】是否做「投影面验算」——
                    量出每个正投影视图的轮廓宽高比，与由真实包围盒算出的
                    理论值比对，证明"拍对了面"而不只是"三张图不一样"。
                    ⚠️ 需要可见轮廓与深色/洋红像素（PIL 可用）；
                    包围盒退化为平面（某轴长度为 0）时该项会被如实跳过。
        expect_bbox_mm: 【可选】用于验算的包围盒六元组。给了就用它算理论
                    比值；没给且 verify_projection=True 时，本函数会自己
                    回读一次真实包围盒（并记录来源）。
    Returns:
        {
          "ok": bool,                 # 所有请求视图都截到 + 无重复 + 投影验算过
          "part": 绝对路径,
          "out_dir": 绝对路径,
          "views": {view: {"ok","path","size_bytes","sha1_12","vname",
                           "view_id","method","shot":{...取证通道原始返回}}},
          "opened": bool, "sw_available": bool,
          "requested": [...], "captured": [...], "failed": [...],
          "invalid_views": [...],
          "duplicate_views": [[重复的视图组], ...],
          "projection_check": {...} | 不存在（未开启时）,
          "error": 失败总因（ok=False 时一定有）,
          "note": ...
        }

    ★ 失败模式（绝不假装成功）：
      · 零件不存在 / swapi 不可用 / SW 连不上 / 文档打不开 → ok=False，
        views 里不出现任何伪造路径；
      · 视图切不过去 → 该视图 ok=False 并带 error，不会落到「上一张图」上；
      · 取证通道返回 ok=False（如 SW 没能激活到前台）→ 该视图 ok=False，
        即使文件恰好存在也不认（因为内容不可信）；
      · ★ 不同视图产出【同一张图】→ ok=False（视图根本没切换，
        详见 VIEW_ID 里 vid=0 的实测记录）；
      · ★ 投影面验算不过 → ok=False（图不一样但拍错了面）。
    """
    part_abs = os.path.abspath(part_path) if part_path else None
    out_dir = os.path.abspath(out_dir) if out_dir else _default_out_dir(part_path)
    out = {"ok": False, "part": part_abs, "out_dir": out_dir,
           "views": {}, "opened": False, "sw_available": False,
           "requested": [], "captured": [], "failed": [],
           "invalid_views": [], "error": None, "round": round_no,
           "reused_open_model": bool(model is not None),
           "generated_at": _now_str(),
           "enforced_by": "visual_check.capture_views"}
    want, bad = _normalize_views(views)
    out["requested"] = want
    out["invalid_views"] = bad
    out["views_source"] = ("默认三视图+等轴测" if views is None
                           else "调用方指定")
    if bad:
        out["error"] = ("非法视图名 %s；合法视图：%s"
                        % ("、".join(bad), "、".join(sorted(VIEW_VNAME))))
        return out
    if not want:
        out["error"] = "views 为空 —— 没有要采集的视图"
        return out
    try:
        os.makedirs(out_dir, exist_ok=True)
    except Exception as e:
        out["error"] = "无法创建输出目录 %s: %r" % (out_dir, e)
        return out

    m = model
    if m is None:
        m, meta, err = _open_part_model(part_abs)
        out["opened"] = bool(meta.get("opened"))
        out["sw_available"] = bool(meta.get("sw_available"))
        out["open_meta"] = meta
        if m is None:
            out["error"] = err
            for v in want:
                out["views"][v] = {"ok": False, "path": None, "view": v,
                                   "error": err}
                out["failed"].append(v)
            return out
    else:
        # 复用已打开的模型：不能声称"本次打开了文档"，如实标注复用
        out["opened"] = True
        out["sw_available"] = True
        out["open_meta"] = {"opened": True, "via": "复用调用方传入的 SWModel",
                            "reused": True}

    for v in want:
        rec = {"view": v, "ok": False, "path": None, "error": None}
        sw_res = _apply_view(m, v)
        rec.update({k: sw_res.get(k) for k in
                    ("vname", "view_id", "method", "zoom_ok")})
        if sw_res.get("error"):
            rec["error"] = "视图切换失败: %s" % sw_res["error"]
            out["views"][v] = rec
            out["failed"].append(v)
            continue
        path = _shot_path(out_dir, part_abs, v, round_no=round_no)
        # 先删掉同名旧文件：否则「截图失败但旧文件还在」会被误判成成功。
        # （同时清掉另一条路径可能留下的另一种扩展名，避免张冠李戴）
        for _stale in (path, os.path.splitext(path)[0] + ".bmp"):
            try:
                if os.path.exists(_stale):
                    os.remove(_stale)
            except Exception:
                pass
        shot = _grab_screenshot(m, path)
        rec["shot"] = shot
        # ★ 实际落盘文件由 shot 决定（SaveBMP 走 .bmp，屏幕抓取走 .png）——
        #   必须采信真实路径，不能想当然写回请求的 path。
        real_path = shot.get("path") or path
        rec["path"] = real_path
        rec["shot_source"] = shot.get("source")
        probe = file_probe(real_path)
        rec["size_bytes"] = probe["size_bytes"]
        rec["sha1_12"] = probe["sha1_12"]
        # ★ 全文件指纹：重复视图检测用它（见 _file_sha1 里 1MB 假阳性的实测记录）
        rec["sha1_full_12"] = probe.get("sha1_full_12")
        rec["file_ok"] = bool(probe["ok"])
        if not shot.get("ok"):
            # ★ 截图通道明确说「不可信」→ 本模块也必须判为失败，
            #   即使文件碰巧存在（可能是上一次的残留）。
            rec["ok"] = False
            rec["error"] = "截图失败: %s" % (
                shot.get("error") or shot.get("note") or shot)
            rec["hint"] = shot.get("hint")
        elif not probe["ok"]:
            rec["ok"] = False
            rec["error"] = "截图文件不可用: %s" % probe["why"]
        else:
            rec["ok"] = True
        if rec["ok"]:
            out["captured"].append(v)
        else:
            out["failed"].append(v)
        out["views"][v] = rec

    # ── ★ 重复视图检测：三视图绝不能是同一张图 ──────────────────────────
    # 实机教训（2026-10-12）：`ShowNamedView2(name, 0)` 不切换视角，
    # 于是 front/top/right 会拍成【字节完全相同】的图，看起来"采到了 3 张"，
    # 实际只有 1 个视角 —— 这种假三视图完全无法用于"看建模建成什么样"。
    # 这里用 sha1 做代码级拦截：只要两张不同的视图产出相同内容，即判失败。
    #
    # ⚠️【必须用全文件指纹 sha1_full_12，不能用 sha1_12】━━━━━━━━━━━━━━━━━━━
    #   实测踩到的假阳性（DSH_悬臂支架）：
    #     SaveBMP 的 BMP 是 4320054 字节，像素【自底向上】存储，文件前段是
    #     图像【底部】的行。front 与 top 整体完全不同（全文件 sha1
    #     55c0009dec75 / 52951e5ae8d1），但底部都是空白 → 前 1MB 恰好相同
    #     （都是 350a1b10f2db）→ 重复检测把一次**完全正常**的采集判成失败，
    #     还误报成"ViewId 传了 0"（其实 ViewId 是对的，视角也确实切了）。
    #   因此这里改用全文件指纹；sha1_12 仅保留作"文件是否被碰过"的轻量指纹。
    digests = {}
    for v in out["captured"]:
        _rec_v = out["views"].get(v) or {}
        d = _rec_v.get("sha1_full_12") or _rec_v.get("sha1_12")
        if d:
            digests.setdefault(d, []).append(v)
    dup_groups = [vs for vs in digests.values() if len(vs) > 1]
    out["duplicate_views"] = dup_groups
    if dup_groups:
        _desc = "；".join("、".join(vs) + "（内容完全相同）" for vs in dup_groups)
        out["ok"] = False
        out["error"] = ("检出【重复视图】：%s —— 说明视图没有真正切换"
                        "（常见根因是 ShowNamedView2 的 ViewId 传了 0）。"
                        "三视图必须是不同视角，拒绝用重复图冒充三视图。" % _desc)
        out["note"] = "采集失败：三视图内容重复，无法用于看形状。"
        return out

    out["ok"] = (not out["failed"]) and bool(out["captured"])
    if not out["ok"]:
        out["error"] = ("以下视图未成功截图: %s（绝不返回不存在的路径当成功）"
                        % "、".join(out["failed"]))
    out["note"] = ("三视图用于比对轮廓/孔位，等轴测图用于看立体形状 —— "
                   "两者都截到、且内容互不重复才算采集完成。" if out["ok"] else
                   "采集未完成，见 failed/error。")

    # ── ★ 投影面验算（可选但强烈建议）：证明"拍对了面"，不只是"图不一样"──
    # 需要真实包围盒才能算出理论比值；调用方给了 expect_bbox_mm 就做，
    # 否则如实标注"未验算"，绝不假装验过。
    if verify_projection:
        bbox = expect_bbox_mm
        if bbox is None:
            # 自己回读一次真实包围盒（有模型时）
            try:
                geo = read_actual_geometry(m)
                bbox = geo.get("bbox_mm") if geo.get("ok") else None
                out["projection_bbox_source"] = geo.get("sources", {}).get("bbox")
            except Exception as e:
                bbox = None
                out["projection_bbox_error"] = repr(e)
        proj = check_view_projection(out["views"], bbox)
        out["projection_check"] = proj
        # ★ 只有【验算做过且失败】才拦；"没验成"（缺包围盒/PIL 缺失/
        #   退化尺寸）如实标注未验算，不误判为拍错面。
        if out["ok"] and proj.get("failed") and not proj.get("ok"):
            out["ok"] = False
            out["error"] = ("三视图投影面验算未通过：%s"
                            "（图都拍到了，但内容不是正确的三视图）"
                            % proj.get("note"))
            out["note"] = "采集失败：三视图未通过投影面验算。"
        elif not proj.get("verified"):
            out.setdefault("warnings", []).append(
                "投影面验算未完成：%s（已如实标注为未验算，未据此判定成败）"
                % proj.get("note"))
    return out


# ═══════════════════════════════════════════════════════════════════════════
# 五、B. ★ 视觉检查闭环（看图 → 矫正 → 继续看 → 继续矫正）
# ═══════════════════════════════════════════════════════════════════════════
_AXIS_NAMES = ("x", "y", "z")


def parse_expect(expect):
    """解析 expect —— 接受 dict，或 "@文件.json" / JSON 字符串 / 文件路径。

    Returns: (expect_dict_or_None, error_or_None)
    """
    if expect is None:
        return None, "expect 为空 —— 闭环必须有期望几何（来自零件参数表）"
    if isinstance(expect, dict):
        return expect, None
    if not isinstance(expect, str):
        return None, "expect 类型不支持: %r" % (type(expect),)
    s = expect.strip()
    if s.startswith("@"):
        s = s[1:].strip()
    if s.startswith("{") or s.startswith("["):
        try:
            d = json.loads(s)
        except Exception as e:
            return None, "expect JSON 解析失败: %s" % e
        return (d, None) if isinstance(d, dict) else \
            (None, "expect JSON 必须是对象，收到 %s" % type(d).__name__)
    if os.path.exists(s):
        d, err = read_json(s)
        if err:
            return None, "expect 文件读取失败（%s）: %s" % (s, err)
        return (d, None) if isinstance(d, dict) else \
            (None, "expect 文件内容必须是对象，收到 %s" % type(d).__name__)
    return None, "expect 既不是 JSON 也不是存在的文件路径: %r" % (expect,)


def normalize_expect(expect):
    """把 expect 规整成可比对的形式。

    支持的 expect 形态：
      {"bbox_mm": [xmin,xmax,ymin,ymax,zmin,zmax], "features":[...], "note":...}
      {"bbox_mm": {"x":[min,max], "y":[...], "z":[...]}}          （等价写法）
      {"bbox_mm": {"xmin":..,"xmax":..,"ymin":..,"ymax":..,...}}  （等价写法）

    Returns: (norm_or_None, error_or_None)
    """
    if not isinstance(expect, dict):
        return None, "expect 必须是对象"
    norm = {"raw": expect, "note": expect.get("note"),
            "bbox_mm": None, "features": expect.get("features") or [],
            "tolerance_mm": None}
    bbox = expect.get("bbox_mm", expect.get("bbox"))
    if bbox is None:
        return norm, None          # 没有包围盒期望 → 只查特征存在性
    vals = None
    if isinstance(bbox, (list, tuple)):
        if len(bbox) != 6:
            return None, ("bbox_mm 必须是 6 个数字 "
                          "[xmin,xmax,ymin,ymax,zmin,zmax]，收到 %d 个" % len(bbox))
        vals = list(bbox)
    elif isinstance(bbox, dict):
        if all(k in bbox for k in ("xmin", "xmax", "ymin", "ymax",
                                   "zmin", "zmax")):
            vals = [bbox["xmin"], bbox["xmax"], bbox["ymin"], bbox["ymax"],
                    bbox["zmin"], bbox["zmax"]]
        elif all(k in bbox for k in ("x", "y", "z")):
            vals = []
            for ax in ("x", "y", "z"):
                pair = bbox[ax]
                if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                    return None, "bbox_mm.%s 必须是 [min,max] 两元素数组" % ax
                vals.extend([pair[0], pair[1]])
        else:
            return None, ("bbox_mm 对象里必须是 {xmin..zmax} 或 {x:[min,max],...}；"
                          "收到键 %s" % sorted(bbox.keys()))
    else:
        return None, "bbox_mm 类型不支持: %s" % type(bbox).__name__
    try:
        norm["bbox_mm"] = [round(float(v), 6) for v in vals]
    except Exception as e:
        return None, "bbox_mm 含非数字项: %s" % e
    for i in range(3):
        lo, hi = norm["bbox_mm"][2 * i], norm["bbox_mm"][2 * i + 1]
        if hi < lo:
            return None, ("bbox_mm 的 %s 轴 min(%s) > max(%s) —— 参数表写反了"
                          % (_AXIS_NAMES[i], lo, hi))
    tol = expect.get("tolerance_mm", expect.get("tol_mm"))
    try:
        norm["tolerance_mm"] = (None if tol is None
                                else max(0.0, float(tol)))
    except Exception:
        return None, "tolerance_mm 必须是数字"
    return norm, None


def read_actual_geometry(m):
    """★ 真实回读几何 —— 闭环的判据来源。

    【核实过的 API（不是猜的）】
      · swapi.py:7730-7767 `SWModel.body_box_mm()`
        → 返回 [xmin,xmax,ymin,ymax,zmin,zmax]（mm），多实体取并集；
          取不到返回 None（文档明确写「调用方【不得】用设计域代替」）。
      · swapi.py:7699-7714 `SWModel._bbox_extent_mm(axis)`
        → 单轴边长（mm），取不到返回 None。
      · swapi.py:4781-4880 `SWModel.massprops(safe=True)`
        → {"ok","volume_mm3","surface_area_mm2","mass_kg",
           "density_kg_m3","center_of_mass_mm","volume_source","mass_source"}

    ★ 关键：包围盒取不到时返回 ok=False —— 绝不用"设计域/期望值"顶上，
      否则闭环会变成"拿期望去比期望"，必然假通过。

    Returns:
        {"ok":bool, "bbox_mm":[...]|None, "extents_mm":{x,y,z},
         "mass":{...}|None, "sources":{...}, "error":str|None}
    """
    out = {"ok": False, "bbox_mm": None, "extents_mm": {},
           "mass": None, "sources": {}, "error": None}
    if m is None:
        out["error"] = "SWModel 为空，无法回读几何"
        return out
    bbox = None
    try:
        bbox = m.body_box_mm()
    except Exception as e:
        out["error"] = "body_box_mm() 抛异常: %r" % (e,)
    out["sources"]["bbox"] = "SWModel.body_box_mm() [swapi.py:7730]"
    if bbox is None:
        out["error"] = (out["error"] or
                        "body_box_mm() 返回 None —— 文档里没有任何实体，"
                        "或包围盒退化。不得用设计域代替（swapi.py:7734 口径）。")
        return out
    try:
        out["bbox_mm"] = [round(float(v), 4) for v in bbox]
    except Exception as e:
        out["error"] = "body_box_mm() 返回值无法解析为数字: %r (%r)" % (e, bbox)
        return out
    # 单轴边长：优先用 _bbox_extent_mm（swapi 的专用方法），失败则从包围盒推
    for i, ax in enumerate(_AXIS_NAMES):
        v = None
        if hasattr(m, "_bbox_extent_mm"):
            try:
                v = m._bbox_extent_mm(i)
            except Exception:
                v = None
        if v is None:
            v = out["bbox_mm"][2 * i + 1] - out["bbox_mm"][2 * i]
            out["sources"]["extent_" + ax] = "由 body_box_mm 推导"
        else:
            out["sources"]["extent_" + ax] = "SWModel._bbox_extent_mm(%d) [swapi.py:7699]" % i
        out["extents_mm"][ax] = round(float(v), 4)
    # 质量属性（辅助证据，失败不影响判据）
    if hasattr(m, "massprops"):
        try:
            mp = m.massprops(safe=True)
            if isinstance(mp, dict) and mp.get("ok"):
                out["mass"] = {k: mp.get(k) for k in
                               ("volume_mm3", "mass_kg", "surface_area_mm2",
                                "density_kg_m3", "volume_source", "mass_source")}
                out["sources"]["mass"] = "SWModel.massprops(safe=True) [swapi.py:4781]"
        except Exception as e:
            out["mass"] = {"error": repr(e)}
    out["ok"] = True
    return out


def compare_bbox(actual6, expect6, tol_mm=None):
    """逐面比对两个包围盒六元组，产出偏差 + 矫正建议。

    比对哪些量：6 个面（xmin/xmax/ymin/ymax/zmin/zmax）逐个比。
    为什么比"面"而不是只比"尺寸"：零件整体偏移（位置错）也必须被抓出来，
    只比 3 个边长会漏掉"尺寸对但装错位置"的情形。

    Returns:
        {"matched":bool, "tolerance_mm":float, "deltas_mm":{face:delta},
         "max_abs_delta_mm":float, "failed_faces":[...], "deltas_by_axis":{...},
         "correction": {...}|None}
    """
    tol = BBOX_TOL_MM if tol_mm is None else float(tol_mm)
    faces = ("xmin", "xmax", "ymin", "ymax", "zmin", "zmax")
    deltas, failed = {}, []
    for i, f in enumerate(faces):
        try:
            d = round(float(actual6[i]) - float(expect6[i]), 4)
        except Exception:
            d = None
        deltas[f] = d
        if d is None or abs(d) > tol:
            failed.append(f)
    # 按轴归并：轴级偏差 = 两个面里绝对值更大的那个（决定要不要改）
    by_axis = {}
    for i, ax in enumerate(_AXIS_NAMES):
        lo_f, hi_f = faces[2 * i], faces[2 * i + 1]
        d_lo, d_hi = deltas[lo_f], deltas[hi_f]
        cands = [d for d in (d_lo, d_hi) if d is not None]
        worst = max(cands, key=abs) if cands else None
        by_axis[ax] = {
            "min_face": lo_f, "max_face": hi_f,
            "delta_min_mm": d_lo, "delta_max_mm": d_hi,
            "worst_delta_mm": worst,
            "actual_size_mm": (round(float(actual6[2 * i + 1]) - float(actual6[2 * i]), 4)
                               if actual6 else None),
            "expect_size_mm": (round(float(expect6[2 * i + 1]) - float(expect6[2 * i]), 4)
                               if expect6 else None),
        }
        by_axis[ax]["size_delta_mm"] = (
            None if (by_axis[ax]["actual_size_mm"] is None
                     or by_axis[ax]["expect_size_mm"] is None)
            else round(by_axis[ax]["actual_size_mm"] - by_axis[ax]["expect_size_mm"], 4))
    vals = [abs(v) for v in deltas.values() if v is not None]
    maxd = round(max(vals), 4) if vals else None
    res = {"matched": not failed, "tolerance_mm": tol, "deltas_mm": deltas,
           "max_abs_delta_mm": maxd, "failed_faces": failed,
           "deltas_by_axis": by_axis, "correction": None}
    if not failed:
        return res
    # ── 矫正建议：挑偏差最大的轴，说明差多少、往哪边改 ──────────────────
    axis = None
    best = -1.0
    for ax in _AXIS_NAMES:
        w = by_axis[ax]["worst_delta_mm"]
        if w is not None and abs(w) > best:
            best, axis = abs(w), ax
    aw = by_axis.get(axis) or {}
    size_d = aw.get("size_delta_mm")
    worst_d = aw.get("worst_delta_mm")
    if size_d is not None and abs(size_d) > tol:
        direction = "增大" if size_d < 0 else "减小"
        hint = ("%s 轴【尺寸】偏差 %+.4f mm（实测 %.4f / 期望 %.4f）："
                "该轴整体要%s %.4f mm。"
                % (axis.upper(), size_d, aw.get("actual_size_mm") or 0.0,
                   aw.get("expect_size_mm") or 0.0, direction, abs(size_d)))
    else:
        direction = "正向" if (worst_d or 0) > 0 else "负向"
        hint = ("%s 轴【位置】偏差 %+.4f mm（面 %s）：形状尺寸基本对，"
                "但整体沿 %s 轴%s偏移了 %.4f mm，需平移校正。"
                % (axis.upper(), worst_d or 0.0,
                   aw.get("min_face") if abs(aw.get("delta_min_mm") or 0)
                   >= abs(aw.get("delta_max_mm") or 0) else aw.get("max_face"),
                   axis.upper(), direction, abs(worst_d or 0.0)))
    res["correction"] = {
        "axis": axis,
        "delta_mm": worst_d,
        "size_delta_mm": size_d,
        "failed_faces": failed,
        "hint": hint,
        "suggest": ("按上表修改参数后重建，然后【再走一轮看图比对】"
                    "（闭环未 PASS 不得放行）。"),
    }
    return res


def compare_features(actual_features, expect_features):
    """比对特征存在性。expect features 支持字符串或 {"name":..,"count":..}。"""
    exp = []
    for f in (expect_features or []):
        if isinstance(f, dict):
            exp.append((str(f.get("name") or f.get("type") or "?"),
                        f.get("count")))
        else:
            exp.append((str(f), None))
    if not exp:
        return {"checked": False, "missing": [], "matched": True,
                "note": "expect 未给 features，跳过特征存在性检查（不视为失败）"}
    have = {}
    for f in (actual_features or []):
        if isinstance(f, dict):
            nm = str(f.get("name") or f.get("type") or "")
        else:
            nm = str(f)
        if nm:
            have[nm] = have.get(nm, 0) + 1
    missing = []
    for nm, cnt in exp:
        n = have.get(nm, 0)
        if cnt is None:
            if n <= 0:
                missing.append({"feature": nm, "expect_count": ">=1",
                                "actual_count": n})
        elif n != int(cnt):
            missing.append({"feature": nm, "expect_count": int(cnt),
                            "actual_count": n})
    return {"checked": True, "missing": missing, "matched": not missing,
            "expect": [{"feature": a, "count": b} for a, b in exp],
            "actual": have}


def _collect_document_features(m):
    """尽力从文档里取一份「特征名清单」（取不到就返回 None，不编造）。

    实现方式与 swapi 既有遍历写法一致（swapi.py:7591-7605 用
    `FirstFeature` + `GetNextFeature()` + `GetTypeName2()`）。

    ── 【实机修复】FirstFeature 必须【按属性取】，不能当方法调 ──────────
    实机实测（2026-10-12，SW2025 SP5.0，late binding）：
      · `model.FirstFeature()`  → com_error(-2147352573, '找不到成员。')
      · `model.FirstFeature`    → 正常返回 COMObject
    早期 SW 版本 / 静态绑定下它是方法，所以这里【两种都试】，
    任一种成功即可 —— 跨版本都能取到特征树。
    另：`f.GetNextFeature` 同样可能属性/方法两种形态，一并兼容。
    """
    model = getattr(m, "model", None)
    if model is None:
        return None

    def _is_feature_like(obj):
        """判断 obj 是不是一个 Feature 对象（而不是绑定方法/None）。

        ★ 这是必需的鸭子类型判据：老版本 SW 下 `model.FirstFeature` 是
          【绑定方法】，它「不是 None」，若只判非空就会把方法本身当成
          特征节点，于是 Name/GetNextFeature 全取不到 → 静默返回 None，
          特征存在性检查就永远形同虚设（这正是回归测试用例13 抓到的缺陷）。
        """
        if obj is None:
            return False
        try:
            return hasattr(obj, "Name") and hasattr(obj, "GetTypeName2")
        except Exception:
            return False

    first = None
    # ① 先按【属性】取（本机 SW2025 实测：FirstFeature 是属性）
    try:
        cand = model.FirstFeature
        if _is_feature_like(cand):
            first = cand
    except Exception:
        pass
    # ② 属性取不到 → 按【方法】调用（老版本 / 静态绑定）
    if first is None:
        try:
            cand = model.FirstFeature()
            if _is_feature_like(cand):
                first = cand
        except Exception:
            pass
    if first is None:
        return None
    out = []
    f = first
    guard = 0
    while f is not None and guard < 5000:
        guard += 1
        try:
            nm = f.Name
            tn = None
            try:
                tn = f.GetTypeName2()
            except Exception:
                pass
            if nm:
                out.append({"name": str(nm),
                            "type": (str(tn) if tn else None)})
        except Exception:
            pass
        # GetNextFeature 同样可能是属性或方法，用同一套鸭子类型判据
        nxt = None
        try:
            cand = f.GetNextFeature
            if _is_feature_like(cand):
                nxt = cand
            elif callable(cand):
                cand2 = cand()
                nxt = cand2 if _is_feature_like(cand2) else None
        except Exception:
            nxt = None
        if nxt is None:
            break
        f = nxt
    return out or None


def check_and_correct(part_path, expect, max_rounds=MAX_ROUNDS_DEFAULT,
                      out_dir=None):
    """★ 核心：「看图 → 矫正 → 继续看 → 继续矫正」的强制闭环。

    每一轮的流程：
      ① 采集三视图 + 等轴测图（capture_views，真实调用 SW 切视图并截图）；
      ② 【真实回读几何】（read_actual_geometry → body_box_mm 六元组）；
      ③ 与 expect 比对（包围盒逐面容差 + 特征存在性）；★ 判据是几何，不是「截了图」
      ④ 一致 → verdict=PASS，结束；不一致 → 产出 correction（矫正指令），
         记录本轮，进入下一轮；
      ⑤ 轮数用尽仍不一致 → final_verdict=FAIL，如实列出还差什么。

    Args:
        part_path:  零件/装配体路径
        expect:     期望几何 dict（或 JSON / @文件）
        max_rounds: 最大轮数（默认 MAX_ROUNDS_DEFAULT=3；
                    超过 MAX_ROUNDS_HARD_CAP 会被夹紧，防止绕过纠偏语义）
        out_dir:    留痕目录（默认工作目录下 visual_check/<零件>_<时间戳>）
    Returns:
        {"ok","rounds":[...],"final_verdict","note","evidence_file",...}
        rounds[i] 结构见模块文档；每轮都含 views / actual_bbox_mm /
        deltas_mm / verdict / correction。
    """
    out_dir = os.path.abspath(out_dir) if out_dir else _default_out_dir(part_path)
    try:
        os.makedirs(out_dir, exist_ok=True)
    except Exception as e:
        return {"ok": False, "final_verdict": VERDICT_ERROR, "rounds": [],
                "out_dir": out_dir,
                "error": "无法创建输出目录: %r" % (e,),
                "enforced_by": "visual_check.check_and_correct"}
    # max_rounds 规整：非法值夹紧到合法区间（不允许用 0/负数/超大值绕过闭环）
    try:
        mr = int(max_rounds)
    except Exception:
        mr = MAX_ROUNDS_DEFAULT
    note_rounds = []
    if mr < 1:
        mr = 1
        note_rounds.append("max_rounds < 1 已夹紧为 1（闭环至少看一轮）")
    if mr > MAX_ROUNDS_HARD_CAP:
        note_rounds.append("max_rounds=%s 超过硬上限 %d，已夹紧"
                           % (max_rounds, MAX_ROUNDS_HARD_CAP))
        mr = MAX_ROUNDS_HARD_CAP

    exp_raw, err = parse_expect(expect)
    if exp_raw is None:
        return {"ok": False, "final_verdict": VERDICT_ERROR, "rounds": [],
                "out_dir": out_dir, "error": err,
                "enforced_by": "visual_check.check_and_correct"}
    exp, err = normalize_expect(exp_raw)
    if exp is None:
        return {"ok": False, "final_verdict": VERDICT_ERROR, "rounds": [],
                "out_dir": out_dir, "error": "expect 不合法: %s" % err,
                "enforced_by": "visual_check.check_and_correct"}

    loops = {"schema": "dsh-engineering/visual-loop@1",
             "part": os.path.abspath(part_path) if part_path else None,
             "out_dir": out_dir, "max_rounds": mr,
             "tolerance_mm": (exp["tolerance_mm"]
                              if exp["tolerance_mm"] is not None else BBOX_TOL_MM),
             "expect": exp_raw, "expect_normalized": {
                 "bbox_mm": exp["bbox_mm"], "features": exp["features"]},
             "started_at": _now_str(), "rounds": [],
             "final_verdict": None, "ok": False,
             "rounds_notes": note_rounds,
             "geometry_source": ("SWModel.body_box_mm() 真实回读 [swapi.py:7730]"),
             "judged_by": ("真实几何回读 vs expect（截图仅作证据，"
                           "不作为通过依据）")}
    evidence = os.path.join(out_dir, LOOP_FILE)
    # 先落一份"开始"痕迹，即使中途异常也有据可查
    atomic_write_json(evidence, loops)

    m, meta, open_err = _open_part_model(part_path)
    loops["open_meta"] = meta
    if m is None:
        loops["final_verdict"] = VERDICT_ERROR
        loops["ok"] = False
        loops["error"] = open_err
        loops["note"] = ("无法打开零件，闭环未开始 —— 绝不假装看过了。"
                         "（%s）" % open_err)
        loops["finished_at"] = _now_str()
        atomic_write_json(evidence, loops)
        loops["evidence_file"] = evidence
        return loops

    for rnd in range(1, mr + 1):
        rec = {"round": rnd, "at": _now_str(), "verdict": VERDICT_MISMATCH,
               "views": {}, "actual_bbox_mm": None, "deltas_mm": {},
               "correction": None, "error": None}
        # ① 采图（复用已打开的模型 + 分轮目录，避免覆盖上一轮证据）
        #    verify_projection=True：每一轮都验算"三视图是否拍对了面"。
        #
        # ⚠️ 【语义修正·重要】这里**必须传 None**，让 capture_views 自己
        #    回读【真实】包围盒来算理论比值 —— 不能传 exp["bbox_mm"]。
        #    原因：投影验算问的是"相机拍的是不是正确的那个面"，
        #      参照物是**模型实际尺寸**（渲染出来的就是它）。
        #      若拿【期望】尺寸去算理论比值，那么在"模型与期望不符"
        #      （恰恰是本闭环要检测的主场景）时，投影验算会跟着一起失败，
        #      把两个互相独立的结论搅在一起：
        #        · 几何比对 → 模型尺寸对不对
        #        · 投影验算 → 图拍得对不对
        #      混用会让"模型尺寸错"被误报成"视图拍错了"，误导矫正方向。
        views = capture_views(part_path, out_dir, views=list(DEFAULT_VIEWS),
                              model=m, round_no=rnd,
                              verify_projection=True,
                              expect_bbox_mm=None)
        rec["views"] = {
            "ok": views.get("ok"),
            "out_dir": views.get("out_dir"),
            "captured": views.get("captured"),
            "failed": views.get("failed"),
            "error": views.get("error"),
            "duplicate_views": views.get("duplicate_views"),
            "projection_check": views.get("projection_check"),
            "shots": {v: {"ok": (rec_v or {}).get("ok"),
                          "path": (rec_v or {}).get("path"),
                          "size_bytes": (rec_v or {}).get("size_bytes"),
                          "sha1_12": (rec_v or {}).get("sha1_12"),
                          "vname": (rec_v or {}).get("vname"),
                          "view_id": (rec_v or {}).get("view_id"),
                          "method": (rec_v or {}).get("method"),
                          "shot_source": (rec_v or {}).get("shot_source"),
                          "error": (rec_v or {}).get("error")}
                      for v, rec_v in (views.get("views") or {}).items()},
        }
        # ② 真实回读几何
        geo = read_actual_geometry(m)
        rec["geometry"] = {"ok": geo["ok"], "sources": geo["sources"],
                           "mass": geo["mass"], "error": geo["error"]}
        if not geo["ok"]:
            rec["verdict"] = VERDICT_ERROR
            rec["error"] = geo["error"]
            rec["correction"] = {
                "axis": None, "delta_mm": None,
                "hint": ("回读不到几何（%s）—— 无法判定，不得视为通过。"
                         "先确认零件里有实体（IBody2）再重建。" % geo["error"])}
            loops["rounds"].append(rec)
            atomic_write_json(evidence, loops)
            continue
        rec["actual_bbox_mm"] = geo["bbox_mm"]
        rec["actual_extents_mm"] = geo["extents_mm"]

        # ③ 比对
        cmp_ok = True
        if exp["bbox_mm"] is not None:
            cmp = compare_bbox(geo["bbox_mm"], exp["bbox_mm"],
                               exp["tolerance_mm"])
            rec["deltas_mm"] = cmp["deltas_mm"]
            rec["deltas_by_axis"] = cmp["deltas_by_axis"]
            rec["max_abs_delta_mm"] = cmp["max_abs_delta_mm"]
            rec["failed_faces"] = cmp["failed_faces"]
            rec["tolerance_mm"] = cmp["tolerance_mm"]
            cmp_ok = cmp["matched"]
            if not cmp_ok:
                rec["correction"] = cmp["correction"]
        else:
            rec["deltas_mm"] = {}
            rec["tolerance_mm"] = None
            rec["note"] = "expect 未给 bbox_mm，本轮只做特征存在性检查"

        feats = _collect_document_features(m)
        fcmp = compare_features(feats, exp["features"])
        rec["feature_check"] = fcmp
        if fcmp["checked"] and not fcmp["matched"]:
            cmp_ok = False
            if rec["correction"] is None:
                rec["correction"] = {
                    "axis": None, "delta_mm": None,
                    "hint": ("缺少期望特征：%s —— 补齐这些特征后重建，再走一轮看图比对。"
                             % "、".join(str(x.get("feature")) for x in fcmp["missing"])),
                    "missing_features": fcmp["missing"]}
            else:
                rec["correction"]["missing_features"] = fcmp["missing"]
        elif not fcmp["checked"] and feats is None:
            # 特征清单读不到：如实标注，绝不当作"特征都在"
            rec["feature_check"]["note"] = (
                (fcmp.get("note") or "") +
                " / 另：文档特征树读取失败，本轮未能核对特征存在性")

        # ④ 判定
        if cmp_ok:
            rec["verdict"] = VERDICT_PASS
            rec["correction"] = None
        else:
            rec["verdict"] = VERDICT_MISMATCH
        loops["rounds"].append(rec)
        atomic_write_json(evidence, loops)
        if rec["verdict"] == VERDICT_PASS:
            break

    # ── ⑤ 汇总 ─────────────────────────────────────────────────────────
    verdicts = [r.get("verdict") for r in loops["rounds"]]
    passed = VERDICT_PASS in verdicts
    if passed:
        loops["ok"] = True
        loops["final_verdict"] = VERDICT_PASS
        _n = verdicts.index(VERDICT_PASS) + 1
        loops["passed_round"] = _n
        loops["note"] = ("第 %d 轮几何与期望一致，闭环通过（看图→矫正→继续看→"
                         "继续矫正已完成）。" % _n)
    else:
        loops["ok"] = False
        # 轮次用尽仍不一致 → FAIL；若全是 ERROR（连几何都读不到）也判 FAIL，
        # 但用 final_verdict=FAIL 并如实说明「是读不到而不是不一致」。
        loops["final_verdict"] = VERDICT_FAIL
        miss = []
        last = loops["rounds"][-1] if loops["rounds"] else {}
        if last.get("failed_faces"):
            miss.append("包围盒仍有 %d 个面超差（%s），最大偏差 %s mm"
                        % (len(last["failed_faces"]),
                           "、".join(last["failed_faces"]),
                           last.get("max_abs_delta_mm")))
        if (last.get("feature_check") or {}).get("missing"):
            miss.append("仍缺少特征：%s"
                        % "、".join(str(x.get("feature"))
                                    for x in last["feature_check"]["missing"]))
        if not miss:
            miss.append("未能取得可用几何/特征判据（%s）"
                        % (last.get("error") or "详见各轮 error"))
        loops["blockers"] = miss
        loops["note"] = ("已用满 %d 轮仍未与期望一致 —— 如实判 FAIL，"
                         "不谎报成功。还差：%s" % (mr, "；".join(miss)))
    loops["finished_at"] = _now_str()
    loops["rounds_used"] = len(loops["rounds"])
    loops["evidence_file"] = evidence
    loops["enforced_by"] = "visual_check.check_and_correct"

    # ── ★ 看图证据自检：即使几何 PASS，也要把"图到底有没有看成功"讲清楚 ──
    # 分层语义（很重要）：
    #   · 闭环的 ok/final_verdict 判的是【几何是否收敛】；
    #   · 截图是【证据】，它的成立与否由 verify_visual_evidence 判，
    #     并由 enforce_visual_gate 作为放行的必要条件。
    # 但绝不能因为"几何对了"就把截图失败悄悄吞掉 —— 这里显式落一条
    # visual_evidence_ok + warnings，让上层一眼看见"这一轮其实没看到图"。
    ev = verify_visual_evidence(loops, out_dir=out_dir)
    loops["visual_evidence_ok"] = bool(ev.get("ok"))
    loops["visual_evidence"] = {
        "ok": ev.get("ok"), "checked_rounds": ev.get("checked_rounds"),
        "checked_shots": ev.get("checked_shots"),
        "total_bytes": ev.get("total_bytes"),
        "missing_count": len(ev.get("missing") or []),
        "bad_count": len(ev.get("bad") or []),
        "conclusion": ev.get("conclusion")}
    if not ev.get("ok"):
        warn = ("⚠️ 几何判定已完成，但【看图证据不成立】：%s "
                "—— 未通过 enforce_visual_gate 的必要条件，不得据此放行。"
                % (ev.get("conclusion") or ev.get("error") or "无截图记录"))
        loops.setdefault("warnings", []).append(warn)
        if loops.get("ok"):
            loops["note"] = (loops.get("note") or "") + " " + warn
    atomic_write_json(evidence, loops)
    return loops


# ═══════════════════════════════════════════════════════════════════════════
# 六、C. 「看图真的发生过」的代码级证明
# ═══════════════════════════════════════════════════════════════════════════
def view_label_cn(view):
    """取视图的中文标签（如 front → 前视）。"""
    return VIEW_LABEL_CN.get(view, view)


def view_labels():
    """返回 视图名 → 中文标签 的映射（供调用方展示）。"""
    return {k: VIEW_LABEL_CN.get(k, k) for k in VIEW_VNAME}


def verify_visual_evidence(loop_record, out_dir=None):
    """校验「看图」这件事真的发生过（每轮引用的截图必须真实存在且非空）。

    强制点（缺一不可）：
      ① loop_record 里必须有 rounds，且至少 1 轮；
      ② 每轮必须至少有 1 张截图记录，且每张图的路径必须真实存在于磁盘；
      ③ 文件必须是普通文件且 size > 0（并 > MIN_SHOT_BYTES 字节）；
      ④ 每张图的 sha1 必须能算出来（内容可读）。

    任一条不满足 → ok=False，并逐条指出是【哪一轮、哪张图、什么毛病】。
    这是用户「必须要让模型自己看到他建的是什么样子的东西」的代码级证明。

    Args:
        loop_record: check_and_correct 的返回值，或 visual_loop.json 的路径
        out_dir:     可选；record 里没有绝对路径时用作相对路径基准
    Returns:
        {"ok","checked_rounds","checked_shots","missing":[...],
         "bad":[...],"total_bytes","detail":[...]}
    """
    out = {"ok": False, "checked_rounds": 0, "checked_shots": 0,
           "missing": [], "bad": [], "total_bytes": 0, "detail": [],
           "source": None}
    rec = loop_record
    if isinstance(rec, str):
        out["source"] = os.path.abspath(rec)
        rec, err = read_json(rec)
        if rec is None:
            out["error"] = "读取闭环记录失败（%s）: %s" % (loop_record, err)
            return out
    if not isinstance(rec, dict):
        out["error"] = "闭环记录必须是对象或 JSON 路径，收到 %s" % type(rec).__name__
        return out
    rounds = rec.get("rounds")
    if not isinstance(rounds, list) or not rounds:
        out["error"] = ("闭环记录里没有 rounds —— 没有「看过图」的痕迹，"
                        "视为未看图")
        return out
    out["checked_rounds"] = len(rounds)

    def _resolve(p):
        if not p:
            return p
        if os.path.isabs(p):
            return p
        base = out_dir or rec.get("out_dir") or os.getcwd()
        return os.path.join(base, p)

    for r in rounds:
        rno = (r or {}).get("round")
        views = (r or {}).get("views") or {}
        shots = views.get("shots") or {}
        # 兼容两种写法：shots 字典（本模块产出）/ 直接给路径列表
        items = []
        if isinstance(shots, dict):
            items = [(k, v) for k, v in shots.items()]
        elif isinstance(shots, list):
            items = [(None, v) for v in shots]
        if not items and isinstance(views.get("paths"), dict):
            items = [(k, v) for k, v in views["paths"].items()]
        if not items:
            out["missing"].append({
                "round": rno,
                "why": "该轮没有任何截图记录（views.shots 为空）—— "
                       "这一轮等于「没看图」",
            })
            continue
        for vname, v in items:
            vname = vname or (v or {}).get("view")
            p = _resolve((v or {}).get("path") if isinstance(v, dict) else v)
            probe = file_probe(p)
            d = {"round": rno, "view": vname, "path": probe["path"],
                 "exists": probe["exists"], "size_bytes": probe["size_bytes"],
                 "ok": bool(probe["ok"]), "why": probe["why"]}
            out["detail"].append(d)
            out["checked_shots"] += 1
            out["total_bytes"] += int(probe["size_bytes"] or 0)
            if not probe["ok"]:
                entry = {"round": rno, "view": vname,
                         "path": probe["path"],
                         "size_bytes": probe["size_bytes"],
                         "why": probe["why"]}
                if not probe["exists"]:
                    out["missing"].append(entry)
                else:
                    out["bad"].append(entry)
    out["ok"] = (not out["missing"]) and (not out["bad"]) \
        and out["checked_shots"] > 0
    if out["ok"]:
        out["conclusion"] = ("已核实 %d 轮 / %d 张截图真实存在且非空"
                             "（合计 %d 字节）—— 「看过图」有磁盘证据。"
                             % (out["checked_rounds"], out["checked_shots"],
                                out["total_bytes"]))
    else:
        out["conclusion"] = ("「看过图」的证据不成立：缺失 %d 张、异常 %d 张。"
                             "详见 missing/bad。"
                             % (len(out["missing"]), len(out["bad"])))
    return out


# ═══════════════════════════════════════════════════════════════════════════
# 七、C2. 与既有流程衔接（只读调用，不改别人的文件）
# ═══════════════════════════════════════════════════════════════════════════
def _expect_to_dims(expect_norm, loop_record):
    """把 expect/实测包围盒折算成 mode2_flow 上报习惯里的 dims 口径。"""
    dims = {}
    bbox = None
    if isinstance(expect_norm, dict):
        bbox = expect_norm.get("bbox_mm")
    if not bbox:
        rounds = (loop_record or {}).get("rounds") or []
        for r in reversed(rounds):
            if r.get("actual_bbox_mm"):
                bbox = r["actual_bbox_mm"]
                break
    if isinstance(bbox, list) and len(bbox) == 6:
        dims = {
            "x_mm": round(float(bbox[1]) - float(bbox[0]), 4),
            "y_mm": round(float(bbox[3]) - float(bbox[2]), 4),
            "z_mm": round(float(bbox[5]) - float(bbox[4]), 4),
            "bbox_mm": [round(float(v), 4) for v in bbox],
            "source": "visual_check（实测/期望包围盒）",
        }
    return dims


def _projection_summary(loop_record):
    """从闭环记录里摘出「投影面验算」结论（供报告/上报引用）。

    取最后一轮里 `views.projection_check` 的结果；没有就返回 None
    （表示当时未开启验算 —— 如实留空，不假装验过）。
    """
    try:
        rounds = (loop_record or {}).get("rounds") or []
        for r in reversed(rounds):
            pc = ((r.get("views") or {}).get("projection_check"))
            if pc:
                return {"ok": pc.get("ok"), "verified": pc.get("verified"),
                        "tolerance": pc.get("tolerance"),
                        "checked": [x.get("view") for x in
                                    (pc.get("checked") or [])],
                        "failed": [x.get("view") for x in
                                   (pc.get("failed") or [])],
                        "note": pc.get("note")}
    except Exception:
        pass
    return None


def report_visual_result(room, part_name, loop_record, work_dir=None,
                         credentials=None, adjacent=None):
    """把视觉闭环结论登记进模式二计划文件；mode2_flow 不可用时【降级并标注】。

    只做两件事，且都【只读地】使用 mode2_flow 的公开 API
    （load_plan / save_plan / record_prime_report），不修改其源码：
      · 往 plan["notes"] 追加一条视觉闭环记录（append_note 语义）；
      · 若调用方给了 room/part_name，再尝试走 record_prime_report 的字段习惯
        登记一条 prime report（缺必填字段时会被它拒绝，本函数如实转述）。

    降级路径：mode2_flow 不在 / 计划读不出来 → 只写本地 JSON 证据文件
    `visual_report.json`（在 loop_record 所在目录），并显式标注 degraded=True。

    Args:
        credentials: 【可选】三道防线凭据 {material, physics, domain}。
            ⚠️ 这三条线**不是**视觉闭环的产出 —— 必须由建模小屋/上游提供。
            不传时本函数会把它们如实登记为「未提供」，**不**编造占位链接
            （旧实现填的"见材料防线报告"会被误读成已提供凭据）。
        adjacent:    【可选】该零件直接接触/配合的零件名列表（可为空列表）。
    """
    out = {"ok": False, "degraded": True, "mode2_flow_available": False,
           "room": room, "part_name": part_name,
           "record_file": None, "degraded_file": None,
           "evidence_verified": None, "error": None,
           "enforced_by": "visual_check.report_visual_result"}
    rec = loop_record
    if isinstance(rec, str):
        rec, err = read_json(rec)
        if rec is None:
            out["error"] = "读取闭环记录失败: %s" % err
            return out
    if not isinstance(rec, dict):
        out["error"] = "loop_record 必须是对象或 JSON 路径"
        return out
    out["record_file"] = rec.get("evidence_file")
    out_dir = rec.get("out_dir") or (os.path.dirname(out["record_file"])
                                     if out["record_file"] else os.getcwd())
    # 先做证据核实（报告里必须带"看图证据是否成立"）
    ev = verify_visual_evidence(rec, out_dir=out_dir)
    out["evidence_verified"] = {"ok": ev.get("ok"),
                                "shots": ev.get("checked_shots"),
                                "missing": len(ev.get("missing") or []),
                                "bad": len(ev.get("bad") or []),
                                "conclusion": ev.get("conclusion")}
    payload = {
        "schema": "dsh-engineering/visual-report@1",
        "at": _now_str(),
        "room": room, "part_name": part_name,
        "part": rec.get("part"),
        "final_verdict": rec.get("final_verdict"),
        "rounds_used": rec.get("rounds_used"),
        "max_rounds": rec.get("max_rounds"),
        "tolerance_mm": rec.get("tolerance_mm"),
        "evidence_file": rec.get("evidence_file"),
        "visual_evidence": out["evidence_verified"],
        "projection_verified": _projection_summary(rec),
        "blockers": rec.get("blockers") or [],
        "dims": _expect_to_dims(rec.get("expect_normalized"), rec),
        "note": rec.get("note"),
    }
    # 无论走哪条路，本地证据文件都落一份（供父代理直接复查）
    try:
        local = os.path.join(out_dir or os.getcwd(), "visual_report.json")
        atomic_write_json(local, payload)
        out["degraded_file"] = local
    except Exception as e:
        out["error"] = "写本地证据失败: %r" % (e,)

    m2, m2err = _try_import_mode2()
    if m2 is None:
        out["ok"] = True
        out["degraded"] = True
        out["degraded_reason"] = ("mode2_flow 不可用（%s）—— 已降级为"
                                  "只写本地 JSON 证据文件，未登记进模式二计划。"
                                  % m2err)
        return out
    out["mode2_flow_available"] = True
    try:
        plan = m2.load_plan(work_dir)
    except Exception as e:
        plan = {"_load_error": repr(e)}
    if isinstance(plan, dict) and plan.get("_load_error"):
        out["ok"] = True
        out["degraded"] = True
        out["degraded_reason"] = ("mode2 计划文件不可读（%s）—— 已降级为本地证据。"
                                  % plan["_load_error"])
        return out
    try:
        note = {
            "kind": "visual_loop",
            "room": room, "part_name": part_name, "part": rec.get("part"),
            "final_verdict": rec.get("final_verdict"),
            "rounds_used": rec.get("rounds_used"),
            "evidence_file": rec.get("evidence_file"),
            "visual_evidence_ok": out["evidence_verified"]["ok"],
            "blockers": rec.get("blockers") or [],
            "at": payload["at"],
        }
        notes = plan.get("notes")
        if not isinstance(notes, list):
            notes = []
        notes.append(note)
        plan["notes"] = notes[-200:]
        save_res = m2.save_plan(plan, work_dir)
        out["plan_path"] = (save_res or {}).get("path")
        out["plan_note"] = note
        if not (save_res or {}).get("ok"):
            out["degraded"] = True
            out["degraded_reason"] = ("写模式二计划失败（%s）—— 已保留本地证据。"
                                      % (save_res or {}).get("error"))
        else:
            out["degraded"] = False
    except Exception as e:
        out["degraded"] = True
        out["degraded_reason"] = "登记进 mode2 计划异常（%r）—— 已保留本地证据。" % (e,)

    # ── 可选：按 record_prime_report 的字段习惯再登记一条 ────────────────
    # 只在 room/part_name 都给了、且它确实提供该函数时尝试；
    # 缺必填字段会被它拒绝 —— 本函数【如实转述】它的拒绝原因，不掩盖。
    #
    # ⚠️ 【如实性修正】三道防线凭据（material/physics/domain）**不是**
    #   本模块的职责 —— 视觉闭环只证明"看过图且几何对上"。
    #   原实现填的是"见材料防线报告"这类**看起来像链接、实际不是**的
    #   占位文字，会让总理子代理误以为凭据已给。
    #   现在：调用方给了 credentials 就用调用方的；没给就显式写成
    #   "未提供（不由 visual_check 产出）"，绝不伪装成已提供。
    if room and part_name and hasattr(m2, "record_prime_report"):
        try:
            if isinstance(credentials, dict) and credentials:
                cred = {k: credentials.get(k) for k in
                        ("material", "physics", "domain")}
            else:
                cred = {k: "未提供（不由 visual_check 产出，需建模小屋自行上报）"
                        for k in ("material", "physics", "domain")}
            rep = {"part_name": part_name, "room": room,
                   "file": rec.get("part") or "",
                   "dims": payload["dims"] or {"note": "visual_check 未取得尺寸"},
                   "credentials": cred,
                   "adjacent": list(adjacent) if adjacent else [],
                   "visual_loop": {"final_verdict": rec.get("final_verdict"),
                                   "evidence_file": rec.get("evidence_file"),
                                   "visual_evidence_ok": out["evidence_verified"]["ok"],
                                   "projection_verified": payload.get(
                                       "projection_verified")}}
            pr = m2.record_prime_report(rep, work_dir)
            out["prime_report"] = {"ok": (pr or {}).get("ok"),
                                   "error": (pr or {}).get("error"),
                                   "note": (pr or {}).get("note")}
            if not cred.get("material") or "未提供" in str(cred.get("material")):
                out.setdefault("warnings", []).append(
                    "三道防线凭据未由调用方提供，已如实标注为「未提供」—— "
                    "总理子代理如需凭据请让建模小屋另行上报。")
            if (pr or {}).get("ok"):
                out["degraded"] = out["degraded"] and False
        except Exception as e:
            out["prime_report"] = {"ok": False, "error": repr(e)}
    out["ok"] = True
    return out


# ═══════════════════════════════════════════════════════════════════════════
# 八、D. ★ 建模后强制闭环节点（房间收尾必须走的门）
# ═══════════════════════════════════════════════════════════════════════════
def enforce_visual_gate(part_path, expect, out_dir=None,
                        max_rounds=MAX_ROUNDS_DEFAULT):
    """建模成功之后【必须】走的视觉门。

    判定顺序（任一环节不过 → passed=False，上游据此拒绝放行）：
      ① 跑 check_and_correct 闭环；
      ② 闭环 final_verdict 必须 == PASS；
      ③ 「看图证据」必须经 verify_visual_evidence 核实成立
         （每张截图真实存在且非空）—— 光"几何对"不够，还【必须看过图】；
      ④ 结果落盘到 out_dir/visual_gate.json。

    Returns:
        {"ok","passed","evidence_file","loop_file","blockers","enforced_by",
          "final_verdict","rounds_used","visual_evidence", "expect",...}
        · passed=False 时 ok=False，blockers 列出未通过的全部原因。
        · enforced_by 恒为 "visual_check.enforce_visual_gate"（表明是代码强制，
          不是提示词约定）。
    """
    out_dir = os.path.abspath(out_dir) if out_dir else _default_out_dir(part_path)
    out = {"ok": False, "passed": False, "blockers": [],
           "evidence_file": None, "loop_file": None,
           "enforced_by": "visual_check.enforce_visual_gate",
           "at": _now_str(), "part": (os.path.abspath(part_path)
                                      if part_path else None)}
    try:
        os.makedirs(out_dir, exist_ok=True)
    except Exception as e:
        out["blockers"].append("无法创建输出目录 %s: %r" % (out_dir, e))
        out["error"] = out["blockers"][-1]
        return out
    gate_file = os.path.join(out_dir, GATE_FILE)
    out["evidence_file"] = gate_file

    loop = check_and_correct(part_path, expect, max_rounds=max_rounds,
                             out_dir=out_dir)
    out["loop_file"] = loop.get("evidence_file")
    out["final_verdict"] = loop.get("final_verdict")
    out["rounds_used"] = loop.get("rounds_used")
    out["expect"] = loop.get("expect")
    out["note"] = loop.get("note")
    if not loop.get("ok"):
        out["blockers"].append(
            "视觉闭环未通过（final_verdict=%s）：%s"
            % (loop.get("final_verdict"), loop.get("note") or loop.get("error")))
    for b in (loop.get("blockers") or []):
        out["blockers"].append("闭环残留问题: %s" % b)
    # ③ 看图证据核实
    ev = verify_visual_evidence(loop, out_dir=out_dir)
    out["visual_evidence"] = {
        "ok": ev.get("ok"), "checked_rounds": ev.get("checked_rounds"),
        "checked_shots": ev.get("checked_shots"),
        "total_bytes": ev.get("total_bytes"),
        "missing": ev.get("missing"), "bad": ev.get("bad"),
        "error": ev.get("error"),
        "conclusion": ev.get("conclusion")}
    if not ev.get("ok"):
        # conclusion 在"连 rounds 都没有"时为空 —— 退化成 error，避免出现
        # 「看图证据不成立：None」这种看不出所以然的提示。
        _why = ev.get("conclusion") or ev.get("error") or "闭环未产出任何截图记录"
        out["blockers"].append("看图证据不成立：%s" % _why)
    # ③b 投影面验算（若闭环里做过）：图都在、但拍错了面也必须拦。
    #     注意区分「验算失败」与「没验成」—— 后者不拦，只如实记录。
    proj = _projection_summary(loop)
    out["projection_verified"] = proj
    if proj is not None and proj.get("failed") and not proj.get("ok"):
        out["blockers"].append(
            "三视图投影面验算未通过：%s（拍到的图不是正确的三视图）"
            % proj.get("note"))
    elif proj is not None and not proj.get("ok"):
        out.setdefault("warnings", []).append(
            "投影面验算未完成：%s" % proj.get("note"))
    out["passed"] = (bool(loop.get("ok")) and bool(ev.get("ok"))
                     and not (proj is not None and proj.get("failed")
                              and not proj.get("ok")))
    out["ok"] = out["passed"]
    if out["passed"]:
        out["note"] = ("建模后视觉门通过：已看 %d 轮 / %d 张截图，"
                       "第 %s 轮几何与期望一致%s。"
                       % (ev.get("checked_rounds"), ev.get("checked_shots"),
                          loop.get("passed_round"),
                          "，且三视图投影面验算通过" if proj else
                          "（投影面验算未开启）"))
    else:
        out["note"] = ("视觉门未通过 —— 上游房间收尾【不得放行】。原因见 blockers。")
    try:
        atomic_write_json(gate_file, out)
    except Exception as e:
        out["blockers"].append("写门证据失败: %r" % (e,))
        out["ok"] = out["passed"] = False
    return out


# ═══════════════════════════════════════════════════════════════════════════
# 九、CLI
# ═══════════════════════════════════════════════════════════════════════════
def _build_parser():
    p = argparse.ArgumentParser(
        prog="visual_check.py",
        description=("建模后视觉闭环：三视图+等轴测截图 → 真实回读几何比对 → "
                     "矫正 → 继续看 → 继续矫正（全部为代码级强制）。"),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=("示例：\n"
                "  python visual_check.py capture --part DSH_底座.SLDPRT --out .\\shots\n"
                "  python visual_check.py loop    --part A.SLDPRT --expect \"{\\\"bbox_mm\\\":[-50,50,-30,30,0,40]}\"\n"
                "  python visual_check.py loop    --part A.SLDPRT --expect @expect.json --max-rounds 4\n"
                "  python visual_check.py verify  --record .\\visual_check\\A_20250101_120000\\visual_loop.json\n"
                "  python visual_check.py gate    --part A.SLDPRT --expect @expect.json\n"))
    sub = p.add_subparsers(dest="cmd", metavar="<命令>")

    c1 = sub.add_parser("capture", help="采集三视图 + 等轴测截图")
    c1.add_argument("--part", required=True, help="零件/装配体路径（.sldprt/.sldasm）")
    c1.add_argument("--out", required=True, help="截图输出目录")
    c1.add_argument("--views", default=",".join(DEFAULT_VIEWS),
                    help="视图列表，逗号分隔，默认 front,top,right,iso")
    c1.add_argument("--verify-projection", action="store_true",
                    dest="verify_projection",
                    help="开启三视图投影面验算（量轮廓宽高比与理论值比对，"
                         "证明拍对了面而不只是图不一样）")
    c1.add_argument("--expect", default=None,
                    help="可选：提供期望 bbox 供投影验算算理论比值"
                         "（不提供则由模块自行回读真实包围盒）")

    c2 = sub.add_parser("loop", help="跑「看图→矫正」闭环（真实回读几何判定）")
    c2.add_argument("--part", required=True, help="零件/装配体路径")
    c2.add_argument("--expect", required=True,
                    help='期望几何 JSON，或 @文件.json 路径')
    c2.add_argument("--out", default=None, help="留痕目录（默认自动生成）")
    c2.add_argument("--max-rounds", type=int, default=MAX_ROUNDS_DEFAULT,
                    dest="max_rounds",
                    help="最大轮数，默认 %d（硬上限 %d）"
                         % (MAX_ROUNDS_DEFAULT, MAX_ROUNDS_HARD_CAP))

    c3 = sub.add_parser("verify", help="校验「看图」是否真的发生过（截图存在且非空）")
    c3.add_argument("--record", required=True, help="visual_loop.json 路径")
    c3.add_argument("--out", default=None, help="相对路径基准目录（可选）")

    c4 = sub.add_parser("gate", help="建模后强制视觉门（闭环 + 证据核实）")
    c4.add_argument("--part", required=True, help="零件/装配体路径")
    c4.add_argument("--expect", required=True, help="期望几何 JSON 或 @文件")
    c4.add_argument("--out", default=None, help="留痕目录（默认自动生成）")
    c4.add_argument("--max-rounds", type=int, default=MAX_ROUNDS_DEFAULT,
                    dest="max_rounds", help="最大轮数，默认 %d" % MAX_ROUNDS_DEFAULT)

    c5 = sub.add_parser("report", help="把闭环结论登记进模式二计划（不可用时降级）")
    c5.add_argument("--room", default="", help="上报房间名")
    c5.add_argument("--part-name", default="", dest="part_name", help="零件名")
    c5.add_argument("--record", required=True, help="visual_loop.json 路径")
    c5.add_argument("--work-dir", default=None, dest="work_dir",
                    help="模式二计划所在目录（可选）")
    c5.add_argument("--credentials", default=None,
                    help="三道防线凭据 JSON，形如 "
                         '\'{"material":"...","physics":"...","domain":"..."}\'。'
                         "不提供则如实登记为「未提供」（本模块不产凭据）")
    c5.add_argument("--adjacent", default=None,
                    help="直接接触/配合的零件名，逗号分隔（可为空）")
    return p


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = _build_parser()
    if not argv:
        parser.print_help()
        return 2
    ns = parser.parse_args(argv)

    if ns.cmd == "capture":
        views = [v for v in (ns.views or "").split(",") if v.strip()]
        exp_bbox = None
        if ns.expect:
            _e, _err = parse_expect(ns.expect)
            if _e is None:
                emit({"ok": False, "error": "expect 不合法: %s" % _err,
                      "enforced_by": "visual_check.capture_views"})
                return 1
            _n, _nerr = normalize_expect(_e)
            if _n is None:
                emit({"ok": False, "error": "expect 不合法: %s" % _nerr,
                      "enforced_by": "visual_check.capture_views"})
                return 1
            exp_bbox = _n.get("bbox_mm")
        res = capture_views(ns.part, ns.out, views=views,
                            verify_projection=bool(ns.verify_projection),
                            expect_bbox_mm=exp_bbox)
        emit(res)
        return 0 if res.get("ok") else 1

    if ns.cmd == "loop":
        res = check_and_correct(ns.part, ns.expect, max_rounds=ns.max_rounds,
                                out_dir=ns.out)
        emit(res)
        return 0 if res.get("ok") else 1

    if ns.cmd == "verify":
        res = verify_visual_evidence(ns.record, out_dir=ns.out)
        emit(res)
        return 0 if res.get("ok") else 1

    if ns.cmd == "gate":
        res = enforce_visual_gate(ns.part, ns.expect, out_dir=ns.out,
                                  max_rounds=ns.max_rounds)
        emit(res)
        return 0 if res.get("passed") else 1

    if ns.cmd == "report":
        cred = None
        if ns.credentials:
            cred, _cerr = parse_expect(ns.credentials)   # 复用 JSON/@文件 解析
            if cred is None:
                emit({"ok": False,
                      "error": "credentials 不是合法 JSON: %s" % _cerr,
                      "enforced_by": "visual_check.report_visual_result"})
                return 1
        adj = None
        if ns.adjacent is not None:
            adj = [a.strip() for a in ns.adjacent.split(",") if a.strip()]
        res = report_visual_result(ns.room, ns.part_name, ns.record,
                                   work_dir=ns.work_dir,
                                   credentials=cred, adjacent=adj)
        emit(res)
        return 0 if res.get("ok") else 1

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
