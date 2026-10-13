# -*- coding: utf-8 -*-
r"""sw_guard.py — SolidWorks 建模期间的轻量级系统健康监控守护脚本。

═══════════════════════════════════════════════════════════════════════════════
【为什么要写这个脚本】
SolidWorks 做大型装配 / 复杂特征建模时会吃满 CPU 与内存，一旦把机器拖到
换页抖动（thrashing），整机可能长时间无响应，用户连任务管理器都点不开。
所以需要一个**后台轻量级监控**：CPU 或内存超过阈值就告警，并且**立即关闭
SolidWorks**，把机器从"卡死"救回来。

【硬性约束：绝对不能用 psutil】
本机系统 Python（3.13）与随包 Python 都**没有安装 psutil**，所以本脚本
**只依赖标准库**，直接通过 `ctypes` 调 Windows API 取 CPU / 内存：

  内存 → kernel32.GlobalMemoryStatusEx（MEMORYSTATUSEX 结构体）
   CPU → kernel32.GetSystemTimes（idle / kernel / user 三个 FILETIME，
         用两次采样的增量算占用率）

【ctypes 的坑（都已在代码里处理）】
  1. `MEMORYSTATUSEX.dwLength` **必须先赋值** 为 `ctypes.sizeof(结构体)`，
     否则 GlobalMemoryStatusEx 直接返回失败（结构体版本校验不通过）。
  2. `FILETIME` 是 64 位时间，拆成 dwLowDateTime / dwHighDateTime 两个
     32 位字段，**必须自己拼**：`high << 32 | low`。只读低 32 位会得到一个
     每约 7 分钟回绕一次的假数值。
  3. GetSystemTimes 返回的 kernel 时间**已经包含 idle 时间**，所以
     总时间 = kernel + user（不能 idle+kernel+user），忙时间 = 总 - idle。
  4. CPU 占用率是**增量**量，单次调用出不来结果：必须先存一份基线，
     隔一段时间再采一次做差。脚本用 SystemMonitor 持有这个基线。
  5. 调用失败**必须优雅降级**：返回 `{"ok": False, "error": "..."}`，
     绝不抛异常崩溃、也绝不假装读到 0%（0% 会被误判成"机器很闲"，
     是比崩溃更危险的错误）。

【taskkill 的坑】
  进程本来就不存在时 taskkill 返回**非 0**（实测中文/英文系统均为 128），
  这**不算失败**。必须结合返回码与输出文本区分：
    · 有"没有找到 / not found / No tasks"字样，或返回码 128 → 没进程，视为成功；
    · 其它非 0（如拒绝访问） → 才是真的杀失败。

【编码的坑】
Windows 下 Python 的 stdout 默认按系统 ANSI 代码页编码（简中 = cp936/GBK），
而调用方（PowerShell / DSH / 管道）按 UTF-8 解码，中文必然乱码。
所以本脚本在导入时就把 stdout/stderr 切到 UTF-8（见 _setup_utf8），
与工程内 `_utf8.py` 的做法保持一致，但**不依赖它**，保证本文件可独立运行。

【CLI 用法】
    python sw_guard.py run   [--interval 10] [--cpu 85] [--mem 85]
                             [--log <path>] [--max-ticks N] [--no-kill]
    python sw_guard.py check [--json]      # 单次体检，只读不杀
    python sw_guard.py state [--json]      # 读状态文件
    python sw_guard.py --once              # 等价于 check

【状态文件】
每次采样都把最新状态**原子写入**（先写临时文件再 os.replace）到
`engineering/tools/load_cases/sw_guard_state.json`，避免外部读到"半个 JSON"。
═══════════════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

__all__ = [
    "sample_memory",
    "sample_cpu",
    "SystemMonitor",
    "check_thresholds",
    "kill_solidworks",
    "run",
    "read_state",
    "main",
]

# ── 版本与常量 ──────────────────────────────────────────────────────────────
__version__ = "1.0.0"

# 本文件所在目录（engineering/tools），状态文件与默认日志都落在这里
TOOLS_DIR = Path(__file__).resolve().parent
LOAD_CASES_DIR = TOOLS_DIR / "load_cases"
# 状态文件路径：load_cases 若不存在，写入时会自动创建（见 _write_state_atomic）
STATE_PATH = LOAD_CASES_DIR / "sw_guard_state.json"
# 默认日志路径：run() 未显式传 log_path 时用它，保证"告警一定留痕"
DEFAULT_LOG_PATH = LOAD_CASES_DIR / "sw_guard.log"

# SolidWorks 主程序映像名；taskkill 用 /IM 按映像名杀，不依赖 PID
SW_IMAGE_NAME = "SLDWORKS.exe"

# alerts 数组上限：状态文件要长期被反复覆写，无上限增长迟早撑爆磁盘 / 拖慢读写
MAX_ALERTS = 50

# taskkill 判定"进程不存在"的关键字（中英双系统都要覆盖）
_NOT_FOUND_MARKERS = (
    "not found",        # 英文：ERROR: The process "X" not found.
    "no tasks",         # 英文：INFO: No tasks are running which match ...
    "no running instance",
    "没有找到",          # 中文：错误: 没有找到进程 "X"
    "找不到",
    "无法找到",
    "没有运行",
)
# 实测得出：taskkill 在"找不到进程"时统一返回 128（中英文系统一致）
_TASKKILL_NOT_FOUND_RC = 128


# ═══════════════════════════════════════════════════════════════════════════
# 一、控制台编码修复
# ═══════════════════════════════════════════════════════════════════════════
_utf8_ready = False


def _setup_utf8(force: bool = False) -> bool:
    """把 stdout / stderr 切到 UTF-8，保证中文告警不乱码。

    为什么必须做：Windows 下 Python 的 stdout 默认用系统 ANSI 代码页
    （简体中文机器 = cp936/GBK），而调用方（PowerShell、DSH 工具链、管道）
    按 UTF-8 解码子进程输出。两边编解码不一致 → 中文必然变成乱码
    （典型的 `����ģʽ`）。在 **Python 进程内部** reconfigure 是唯一解，
    外部设置 `[Console]::OutputEncoding` 管不到子进程内部。

    本函数**幂等且静默失败**：编码修复只是"锦上添花"，
    绝不能因为它失败而影响监控主流程。

    Args:
        force: True 时即使已修复过也重做一次（供测试断言用）。

    Returns:
        bool: 当前 stdout 是否处于 UTF-8 状态。
    """
    global _utf8_ready
    if _utf8_ready and not force:
        return True
    ok = False
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                # errors="replace"：极端情况下用 '?' 顶替无法编码的字符，绝不抛异常
                reconfigure(encoding="utf-8", errors="replace")
                ok = True
                continue
            except Exception:
                pass
        # 兜底：老版本或被重定向成不支持 reconfigure 的对象，包一层 UTF-8 wrapper
        try:
            buf = getattr(stream, "buffer", None)
            if buf is not None:
                import io

                setattr(
                    sys, stream_name,
                    io.TextIOWrapper(buf, encoding="utf-8", errors="replace",
                                     line_buffering=True),
                )
                ok = True
        except Exception:
            pass
    _utf8_ready = ok
    return ok


def _now_str() -> str:
    """返回本地时间的 `YYYY-MM-DD HH:MM:SS` 字符串（告警行与状态文件统一用它）。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _utf8_out(text: str) -> None:
    """安全打印：即使 stdout 不是 UTF-8（reconfigure 失败），也绝不因编码崩溃。

    ctypes / 监控脚本存在的意义是"救机器"，绝不能自己先崩掉。
    """
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        # 极端兜底：退化成 ASCII，只丢中文字形，不丢信息结构
        enc = getattr(sys.stdout, "encoding", None) or "ascii"
        sys.stdout.write(text.encode(enc, "replace").decode(enc, "replace") + "\n")
        sys.stdout.flush()


# ═══════════════════════════════════════════════════════════════════════════
# 二、ctypes 结构体与 kernel32 绑定
# ═══════════════════════════════════════════════════════════════════════════
class _FILETIME(ctypes.Structure):
    """Windows FILETIME 结构：64 位时间被拆成高低两个 32 位 DWORD。

    坑：ctypes 不会自动帮你合成 64 位，必须手工 `high << 32 | low`。
    """

    _fields_ = [
        ("dwLowDateTime", ctypes.c_uint32),
        ("dwHighDateTime", ctypes.c_uint32),
    ]


class _MEMORYSTATUSEX(ctypes.Structure):
    """Windows MEMORYSTATUSEX 结构，供 GlobalMemoryStatusEx 使用。

    坑：`dwLength` **必须先设为结构体字节大小**，否则 API 认为是未知版本，
    直接返回 0（失败）且不改动任何字段。这是最常见的"读到全 0"原因。
    字段顺序/类型必须与 Windows SDK 完全一致，否则内存布局错位。
    """

    _fields_ = [
        ("dwLength", ctypes.c_uint32),
        ("dwMemoryLoad", ctypes.c_uint32),
        ("ullTotalPhys", ctypes.c_uint64),
        ("ullAvailPhys", ctypes.c_uint64),
        ("ullTotalPageFile", ctypes.c_uint64),
        ("ullAvailPageFile", ctypes.c_uint64),
        ("ullTotalVirtual", ctypes.c_uint64),
        ("ullAvailVirtual", ctypes.c_uint64),
        ("ullAvailExtendedVirtual", ctypes.c_uint64),
    ]


def _load_kernel32() -> Any:
    """加载 kernel32 并声明函数签名；非 Windows 或加载失败时抛 OSError。

    为什么要声明 argtypes/restype：
      · 不声明时 ctypes 默认按 c_int 处理返回值，64 位句柄 / BOOL 会出错；
      · 传 byref 指针时没有签名约束容易传错类型，导致 API 静默失败。
    use_last_error=True 才能用 ctypes.get_last_error() 取回 GetLastError，
    用于把失败原因写进 error 字段（而不是只丢一句"失败了"）。
    """
    if sys.platform != "win32":
        raise OSError("当前平台不是 Windows（sys.platform=%s），无法调用 kernel32" % sys.platform)
    last_err = None
    for factory in (lambda: ctypes.WinDLL("kernel32", use_last_error=True),
                    lambda: ctypes.windll.kernel32):
        try:
            k32 = factory()
        except Exception as exc:  # pragma: no cover - 仅在异常环境触发
            last_err = exc
            continue
        try:
            k32.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(_MEMORYSTATUSEX)]
            k32.GlobalMemoryStatusEx.restype = ctypes.c_int
            k32.GetSystemTimes.argtypes = [
                ctypes.POINTER(_FILETIME),
                ctypes.POINTER(_FILETIME),
                ctypes.POINTER(_FILETIME),
            ]
            k32.GetSystemTimes.restype = ctypes.c_int
        except Exception as exc:  # pragma: no cover
            last_err = exc
            continue
        return k32
    raise OSError("无法加载 kernel32：%r" % (last_err,))


def _win_error_text(prefix: str) -> str:
    """把 GetLastError 翻译成可读文本，供 error 字段使用。"""
    try:
        code = ctypes.get_last_error()
    except Exception:
        code = None
    if code:
        try:
            return "%s（GetLastError=%d）" % (prefix, code)
        except Exception:
            pass
    return prefix


# ═══════════════════════════════════════════════════════════════════════════
# 三、内存采样
# ═══════════════════════════════════════════════════════════════════════════
def sample_memory() -> Dict[str, Any]:
    """用 GlobalMemoryStatusEx 采一次物理内存使用情况。

    为什么不用 psutil：环境里根本没装（系统 Python 3.13 与随包 Python 都没有），
    所以直接走 kernel32。

    为什么 percent 自己算而不是直接用 dwMemoryLoad：
      dwMemoryLoad 是 API 给的**取整整数**，精度只到 1%，用于阈值判定会丢细节；
      这里用 `used / total * 100` 得到浮点值，同时把 dwMemoryLoad 原样放在
      `os_load_pct` 字段里，方便交叉核对两者是否一致。

    Returns:
        dict: 成功时
            {"ok": True, "total_mb": float, "available_mb": float,
             "used_mb": float, "percent": float, "os_load_pct": float}
        失败时 `{"ok": False, "error": "..."}`（**绝不**返回 0% 冒充成功）。
    """
    try:
        k32 = _load_kernel32()
    except Exception as exc:
        return {"ok": False, "error": "加载 kernel32 失败：%s" % exc}

    try:
        status = _MEMORYSTATUSEX()
        # ★ 关键：先告诉 API 结构体大小，否则调用必定失败
        status.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
        ok = k32.GlobalMemoryStatusEx(ctypes.byref(status))
        if not ok:
            return {"ok": False, "error": _win_error_text("GlobalMemoryStatusEx 调用失败")}

        total_bytes = int(status.ullTotalPhys)
        avail_bytes = int(status.ullAvailPhys)
        if total_bytes <= 0:
            # 物理内存总量不可能为 0；出现即说明结构体没被正确填充
            return {"ok": False,
                    "error": "GlobalMemoryStatusEx 返回的总内存为 0，结构体可能未正确填充"}

        used_bytes = max(0, total_bytes - avail_bytes)
        mb = 1024.0 * 1024.0
        percent = used_bytes / total_bytes * 100.0
        return {
            "ok": True,
            "total_mb": round(total_bytes / mb, 2),
            "available_mb": round(avail_bytes / mb, 2),
            "used_mb": round(used_bytes / mb, 2),
            "percent": round(percent, 2),
            "os_load_pct": float(status.dwMemoryLoad),
        }
    except Exception as exc:  # 任何 ctypes 层异常都降级，不让监控脚本自杀
        return {"ok": False, "error": "采样内存时发生异常：%r" % (exc,)}


# ═══════════════════════════════════════════════════════════════════════════
# 四、CPU 采样（增量法）
# ═══════════════════════════════════════════════════════════════════════════
def _get_system_times() -> Dict[str, Any]:
    """调一次 GetSystemTimes，返回 idle / kernel / user 三个 64 位计数。

    ★ FILETIME 拼接是这里最大的坑：高低 32 位必须自己合成 64 位，
      只读 dwLowDateTime 会得到一个约 7 分钟回绕一次的毫无意义的数。

    ★ 另一个坑：Windows 的 kernel time **已经包含了 idle time**，
      所以总时间 = kernel + user（不是 idle + kernel + user，那样会重复计入
      idle 导致占用率被系统性低估）。忙时间 = 总时间 - idle。

    Returns:
        dict: 成功 {"ok": True, "idle": int, "kernel": int, "user": int, "total": int}
              失败 {"ok": False, "error": "..."}
    """
    try:
        k32 = _load_kernel32()
    except Exception as exc:
        return {"ok": False, "error": "加载 kernel32 失败：%s" % exc}

    try:
        idle, kernel, user = _FILETIME(), _FILETIME(), _FILETIME()
        ok = k32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user))
        if not ok:
            return {"ok": False, "error": _win_error_text("GetSystemTimes 调用失败")}

        # 64 位拼接：高 32 位左移 32 位后与低 32 位按位或
        idle_v = (int(idle.dwHighDateTime) << 32) | int(idle.dwLowDateTime)
        kernel_v = (int(kernel.dwHighDateTime) << 32) | int(kernel.dwLowDateTime)
        user_v = (int(user.dwHighDateTime) << 32) | int(user.dwLowDateTime)
        # kernel 含 idle，故总时间 = kernel + user
        total_v = kernel_v + user_v
        return {"ok": True, "idle": idle_v, "kernel": kernel_v,
                "user": user_v, "total": total_v}
    except Exception as exc:
        return {"ok": False, "error": "读取系统时间时发生异常：%r" % (exc,)}


class SystemMonitor:
    """持有 CPU 采样基线的监控器。

    为什么需要这个类：CPU 占用率是**两个时间点之间的增量**，单次调用
    GetSystemTimes 拿不到任何占比信息。必须有人记住"上一次的三个计数"，
    下次采样做差才能算出占用率 —— 这个状态就放在这里。
    """

    def __init__(self, min_window: float = 0.2) -> None:
        """
        Args:
            min_window: 两次采样之间的**最小**间隔（秒）。间隔太短时
                FILETIME 增量很小，除法的量化误差会被放大（比如 0.001s 内
                增量只有几百个 tick，算出 0% 或 100% 的假值）。
                低于这个窗口时本类会主动 sleep 补足。
        """
        self.min_window = max(0.05, float(min_window))
        self._last: Optional[Dict[str, int]] = None

    def reset(self) -> None:
        """丢弃基线，让下一次 sample_cpu 重新走"首采 + 校准"路径。"""
        self._last = None

    def sample_cpu(self, min_window: Optional[float] = None) -> Dict[str, Any]:
        """采样一次 CPU 总占用率（0~100）。

        ★ 首次调用没有基线，无法直接做差。这里**不返回 None / 0**，而是：
            取基线 → 短暂 sleep 一个采样窗口 → 再取一次 → 立刻算出真实占用率，
          并把第二次结果留给下一次调用当基线。
          这样 `--once` / `check` 这种"只调一次"的场景也能拿到**真实数值**，
          而不是一个"尚未初始化"的空壳（那会让体检结果毫无意义）。

        Args:
            min_window: 覆盖实例级最小采样窗口（秒）；None 表示用实例默认值。

        Returns:
            dict: 成功 {"ok": True, "percent": float, "window_sec": float}
                  失败 {"ok": False, "error": "..."}（阈值判定会跳过该维度）
        """
        window = self.min_window if min_window is None else max(0.05, float(min_window))
        first = _get_system_times()
        if not first.get("ok"):
            # 采样失败时**保留旧基线**：下次成功即可正常做差，避免反复重新校准
            return {"ok": False, "error": first.get("error", "GetSystemTimes 失败")}

        if self._last is None:
            # 首采：先建立基线，再等一个窗口，然后立刻做差 —— 保证首次就有真值
            self._last = first
            time.sleep(window)
            second = _get_system_times()
            if not second.get("ok"):
                return {"ok": False, "error": second.get("error", "GetSystemTimes 失败")}
            return self._diff(first, second, window)

        elapsed = time.monotonic() - getattr(self, "_last_mono", time.monotonic())
        if elapsed < window:
            # 间隔过短会让增量过小、量化误差放大 → 补足到最小窗口再采
            time.sleep(window - elapsed)
        second = _get_system_times()
        if not second.get("ok"):
            return {"ok": False, "error": second.get("error", "GetSystemTimes 失败")}
        return self._diff(self._last, second, window)

    def _diff(self, prev: Dict[str, int], cur: Dict[str, int],
              window: float) -> Dict[str, Any]:
        """用两次采样的增量计算 CPU 占用率，并把 cur 存为新基线。

        公式：占用率 = (总增量 - 空闲增量) / 总增量 × 100%
        """
        d_total = int(cur["total"]) - int(prev["total"])
        d_idle = int(cur["idle"]) - int(prev["idle"])
        # 更新基线（连同单调时钟，供下次判断窗口是否足够）
        self._last = cur
        self._last_mono = time.monotonic()

        if d_total <= 0:
            # 计数器没前进：采样过密、时钟回拨、或计数器异常。
            # 绝不能拿它去做除法（ZeroDivisionError / 负占用率），直接报错降级。
            return {"ok": False,
                    "error": "两次采样之间系统时间增量非正（d_total=%d），可能采样过密" % d_total}

        busy = d_total - d_idle
        percent = busy / d_total * 100.0
        # 夹到 [0, 100]：极端竞态下可能略微越界，夹紧避免出现 "-0.3%" 这种怪值
        percent = min(100.0, max(0.0, percent))
        return {"ok": True, "percent": round(percent, 2),
                "window_sec": round(window, 3)}


def sample_cpu(monitor: Optional[SystemMonitor] = None,
               min_window: float = 0.5) -> Dict[str, Any]:
    """模块级便利函数：采一次 CPU 占用率。

    有传 monitor 就复用它的基线（连续监控场景，开销最小）；
    没传就临时建一个（单次体检场景，会自动做一次校准窗口）。

    Args:
        monitor: 可复用的 SystemMonitor；None 时内部临时创建。
        min_window: 最小采样窗口（秒）；单次体检默认 0.5s，比 0.2s 更稳。

    Returns:
        dict: {"ok": bool, "percent": float} 或 {"ok": False, "error": "..."}
    """
    mon = monitor if monitor is not None else SystemMonitor(min_window=min_window)
    return mon.sample_cpu(min_window if monitor is None else None)


# ═══════════════════════════════════════════════════════════════════════════
# 五、阈值判定
# ═══════════════════════════════════════════════════════════════════════════
def check_thresholds(cpu_pct: Optional[float], mem_pct: Optional[float],
                     cpu_limit: float = 85.0, mem_limit: float = 85.0) -> Dict[str, Any]:
    """判断 CPU / 内存是否越过阈值，并生成**中文**告警原因。

    设计要点：
      · 用**严格大于**（`>`）而不是 `>=`：阈值 85.0 表示"超过 85 才告警"，
        恰好 85.0 时不该误杀 SolidWorks。
      · 某维度为 None（该次采样失败）时**不判定为超标**，并在 `errors` 里
        留痕。因为"读不到数据"和"机器很闲"是两回事：宁可漏报一次，
        也不能因为 API 失败就误杀用户的建模成果。

    Args:
        cpu_pct: CPU 占用率（%）；None 表示该维度采样失败。
        mem_pct: 内存占用率（%）；None 表示该维度采样失败。
        cpu_limit: CPU 阈值（%）。
        mem_limit: 内存阈值（%）。

    Returns:
        dict: {"breach": bool, "reasons": [中文原因...], "cpu_pct": ...,
               "mem_pct": ..., "cpu_limit": ..., "mem_limit": ..., "errors": [...]}
    """
    reasons: List[str] = []
    errors: List[str] = []

    if cpu_pct is None:
        errors.append("CPU 采样失败，本次跳过 CPU 阈值判定")
    elif float(cpu_pct) > float(cpu_limit):
        # 文案与需求一致：CPU 占用 91.2% 超过阈值 85.0%
        reasons.append("CPU 占用 %.1f%% 超过阈值 %.1f%%" % (float(cpu_pct), float(cpu_limit)))

    if mem_pct is None:
        errors.append("内存采样失败，本次跳过内存阈值判定")
    elif float(mem_pct) > float(mem_limit):
        reasons.append("内存占用 %.1f%% 超过阈值 %.1f%%" % (float(mem_pct), float(mem_limit)))

    return {
        "breach": len(reasons) > 0,
        "reasons": reasons,
        "cpu_pct": None if cpu_pct is None else round(float(cpu_pct), 2),
        "mem_pct": None if mem_pct is None else round(float(mem_pct), 2),
        "cpu_limit": float(cpu_limit),
        "mem_limit": float(mem_limit),
        "errors": errors,
    }


# ═══════════════════════════════════════════════════════════════════════════
# 六、关闭 SolidWorks
# ═══════════════════════════════════════════════════════════════════════════
def kill_solidworks(image_name: str = SW_IMAGE_NAME, timeout: float = 15.0) -> Dict[str, Any]:
    """用 taskkill 强制结束 SolidWorks 进程。

    ★ 最大的坑：进程本来就不存在时 taskkill **返回非 0**（实测 = 128），
      这**不是错误**。如果按"非 0 即失败"处理，监控会天天误报"关闭失败"，
      用户就会开始忽略告警 —— 那这个守护脚本等于废了。
      所以这里严格区分三种结果：
        1. 返回码 0                    → 真的杀掉了，method="taskkill"
        2. 输出含"没有找到/not found"   → 本来就没运行，method="not_running"，
           依然算 **ok=True**（因为期望状态"SW 已关闭"已经满足）
        3. 其它非 0（如拒绝访问/权限不足）→ 才是真的失败，ok=False

    Args:
        image_name: 映像名，默认 SLDWORKS.exe（按映像名杀，不依赖 PID）。
        timeout: 子进程超时（秒）。卡死时不能让守护脚本跟着一起卡死，
                 否则机器真被拖死时它反而帮不上忙。

    Returns:
        dict: 成功 {"ok": True, "method": "taskkill"|"not_running", ...}
              失败 {"ok": False, "method": "taskkill", "error": "...", ...}
    """
    cmd = ["taskkill", "/F", "/IM", image_name]
    try:
        # errors="replace"：taskkill 的输出按系统 ANSI 代码页解码，
        # 万一遇到不可解码字节会抛 UnicodeDecodeError 把守护脚本打崩，
        # 这里用 replace 兜底（结果只是个别字符变成 '?'，不影响关键字匹配）。
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout, errors="replace")
    except FileNotFoundError:
        return {"ok": False, "method": "taskkill",
                "error": "找不到 taskkill 命令（系统环境异常）", "output": ""}
    except subprocess.TimeoutExpired:
        return {"ok": False, "method": "taskkill",
                "error": "taskkill 执行超时（%.0f 秒），可能系统已严重卡顿" % timeout,
                "output": ""}
    except Exception as exc:
        return {"ok": False, "method": "taskkill",
                "error": "调用 taskkill 时发生异常：%r" % (exc,), "output": ""}

    rc = proc.returncode
    # stdout 与 stderr 都看：不同 Windows 版本把提示写在不同的流里
    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    low = output.lower()

    if rc == 0:
        # note 里用真实的 image_name 而不是写死 "SolidWorks"：
        # 本函数的 image_name 是可覆盖的（测试/复用其它进程时），写死会撒谎
        return {"ok": True, "method": "taskkill", "returncode": rc,
                "output": output, "note": "已强制结束进程 %s" % image_name}

    # 非 0：先判断是不是"本来就没在运行"
    matched = [m for m in _NOT_FOUND_MARKERS if (m in low if m.isascii() else m in output)]
    if matched or rc == _TASKKILL_NOT_FOUND_RC:
        # rc==128 是 taskkill 的"未找到进程"专用返回码，即便文案被本地化到
        # 认不出来，也能靠返回码兜住（matched 为空时在 note 里说明是码判定）
        how = "输出关键字：%s" % "/".join(matched) if matched else "返回码 %d" % rc
        return {"ok": True, "method": "not_running", "returncode": rc, "output": output,
                "note": "SolidWorks 未在运行（%s）" % how}

    return {"ok": False, "method": "taskkill", "returncode": rc, "output": output,
            "error": "taskkill 关闭 SolidWorks 失败（返回码 %d）：%s" % (rc, output or "无输出")}


# ═══════════════════════════════════════════════════════════════════════════
# 七、状态文件与日志（原子写）
# ═══════════════════════════════════════════════════════════════════════════
def _write_state_atomic(state: Dict[str, Any], path: Path = STATE_PATH) -> Dict[str, Any]:
    """把状态**原子地**写入 JSON 文件。

    为什么要原子写：外部脚本（或用户）随时可能读这个文件。如果直接
    `open(path,"w")` 覆盖，读到"写了一半"的 JSON 就会解析失败。
    做法是：写同目录临时文件 → flush + fsync 落盘 → `os.replace` 原子替换。
    `os.replace` 在同一卷上是原子操作，读方要么看到旧内容、要么看到新内容，
    绝不会看到半个文件。

    Returns:
        dict: {"ok": bool, "path": str} 或 {"ok": False, "error": "..."}
    """
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)  # load_cases 不存在就建
        # 临时文件必须与目标**同目录**，否则 os.replace 可能跨卷而失去原子性
        tmp = path.with_name(path.name + ".tmp%d" % os.getpid())
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(state, fh, ensure_ascii=False, indent=2)
                fh.flush()
                os.fsync(fh.fileno())  # 强制落盘，避免掉电/崩溃后留下空文件
            os.replace(tmp, path)      # ★ 原子替换
        finally:
            # 替换成功后 tmp 已不存在；失败时把残留临时文件清掉，别留垃圾
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
        return {"ok": True, "path": str(path)}
    except Exception as exc:
        return {"ok": False, "error": "写状态文件失败：%r" % (exc,), "path": str(path)}


def read_state(path: Path = STATE_PATH, retries: int = 3,
               retry_delay: float = 0.05) -> Dict[str, Any]:
    """读取状态文件。

    ★ Windows 特有的坑（并发压力测试实测到）：写方执行 `os.replace` 的那一瞬间，
      读方 open() 可能撞上**共享冲突**并抛 `PermissionError`。
      注意这**不是**原子写失效 —— 实测 29612 次并发读里 JSON 解析失败 0 次
      （永远不会读到半个文件），只有极少数撞上这个共享冲突。
      实际使用中状态文件约 10 秒才写一次，概率极低，但这里仍做几次短重试，
      让偶发的瞬时冲突自愈，而不是把一次可恢复的抖动报成"状态文件读不了"。

    Args:
        path: 状态文件路径。
        retries: 最大尝试次数（含首次）。
        retry_delay: 每次重试前的等待秒数。

    Returns:
        dict: 成功时是状态内容本身（额外补一个 "_path" 便于排查）；
              失败时 {"ok": False, "error": "..."}。
    """
    path = Path(path)
    if not path.exists():
        return {"ok": False, "error": "状态文件不存在：%s" % path, "path": str(path)}

    last_exc: Optional[BaseException] = None
    for attempt in range(max(1, int(retries))):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                return {"ok": False, "error": "状态文件内容不是 JSON 对象", "path": str(path)}
            data.setdefault("ok", True)
            data["_path"] = str(path)
            return data
        except PermissionError as exc:
            # 只对共享冲突重试；JSON 损坏等其它错误重试也没用，直接跳出
            last_exc = exc
            if attempt + 1 < max(1, int(retries)):
                time.sleep(retry_delay)
                continue
            break
        except Exception as exc:
            last_exc = exc
            break

    return {"ok": False, "error": "读取状态文件失败：%r" % (last_exc,), "path": str(path)}


def _append_log(log_path: Optional[Path], record: Dict[str, Any]) -> None:
    """把一条告警以 JSONL 形式追加进日志文件（一行一条，便于机器解析）。

    日志写入失败**绝不影响主流程**：告警的首要任务是关掉 SolidWorks，
    磁盘满/权限不足不该成为放弃救援的理由。
    """
    if not log_path:
        return
    try:
        log_path = Path(log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _trim_alerts(alerts: List[Dict[str, Any]], limit: int = MAX_ALERTS) -> List[Dict[str, Any]]:
    """只保留最近 limit 条告警。

    为什么必须裁剪：状态文件每 10 秒重写一次，如果 alerts 无限追加，
    这个文件会无限膨胀（一天就是 8640 条），既拖慢每次写入，
    又可能把磁盘吃光 —— 一个"防拖死机器"的工具反而拖死磁盘就很讽刺。
    保留 50 条足够复盘最近的异常。
    """
    if len(alerts) <= limit:
        return alerts
    return alerts[-limit:]


# ═══════════════════════════════════════════════════════════════════════════
# 八、主循环
# ═══════════════════════════════════════════════════════════════════════════
def run(interval: float = 10.0, cpu_limit: float = 85.0, mem_limit: float = 85.0,
        log_path: Optional[Any] = None, on_breach: Optional[Callable[[Dict[str, Any]], Any]] = None,
        max_ticks: Optional[int] = None, stop_after_kill: bool = True,
        state_path: Optional[Any] = None, kill_enabled: bool = True,
        startup_delay: float = 0.0) -> Dict[str, Any]:
    """后台健康监控主循环：每 interval 秒采样一次，超阈值就告警并关闭 SolidWorks。

    流程（每个 tick）：
        采样 CPU / 内存 → check_thresholds 判定
          ├─ 未超阈值：静默（只更新状态文件）
          └─ 超阈值  ：打印中文告警 → 写日志 → 记入 alerts →
                       立即 kill_solidworks() → 默认**退出循环**
        → 原子写状态文件 → sleep(interval)

    ★ 为什么杀完默认退出：SolidWorks 已经被关掉了，继续监控下去没有意义
      （不会有新的 SW 负载），反而让一个后台进程白占资源。
      需要继续观察（比如用户会立刻重启 SW）时传 `stop_after_kill=False`。
    ★ 状态文件**每个 tick 都写**（不只是告警时写），这样外部随时能通过
      updated_at 判断"守护脚本还活着吗"，而不是等出事才发现它早挂了。

    Args:
        interval: 采样间隔（秒），默认 10。
        cpu_limit: CPU 阈值（%），默认 85。
        mem_limit: 内存阈值（%），默认 85。
        log_path: 日志文件路径；None 时用默认 `load_cases/sw_guard.log`。
        on_breach: 告警回调，签名 `on_breach(alert_dict) -> Any`；
                   alert_dict 含 time / reasons / cpu_pct / mem_pct / kill 结果。
                   回调内部异常会被吞掉，绝不让用户代码打断救援流程。
        max_ticks: 最多跑多少个 tick（测试用）；None 表示无限跑。
        stop_after_kill: 杀死 SolidWorks 后是否退出主循环，默认 True。
        state_path: 状态文件路径；None 时用默认 load_cases/sw_guard_state.json。
        kill_enabled: False 时只告警不杀（演练/测试模式，对应 CLI 的 --no-kill）。
        startup_delay: 循环开始前的等待秒数（0 表示立即开始）。

    Returns:
        dict: {"ok": bool, "ticks": int, "alerts": [...], "killed": bool,
               "last": {...}, "state_path": ..., "log_path": ...,
               "started_at": ..., "ended_at": ..., "stopped_reason": ...}
    """
    monitor = SystemMonitor()
    state_file = Path(state_path) if state_path else STATE_PATH
    # 未显式给日志路径时用默认路径：需求要求"超阈值必须写日志文件"，
    # 默认落空会让告警无迹可查，所以这里给一个合理的默认值。
    log_file = Path(log_path) if log_path else DEFAULT_LOG_PATH

    started_at = _now_str()
    alerts: List[Dict[str, Any]] = []
    killed = False
    last: Dict[str, Any] = {}
    tick = 0
    stopped_reason = "completed"
    fatal_error: Optional[str] = None

    # 初始状态先落一次盘：让"守护已启动"这件事立刻可观测
    state: Dict[str, Any] = {
        "ok": True,
        "pid": os.getpid(),
        "started_at": started_at,
        "updated_at": started_at,
        "ticks": 0,
        "interval": interval,
        "cpu_limit": cpu_limit,
        "mem_limit": mem_limit,
        "kill_enabled": kill_enabled,
        "stop_after_kill": stop_after_kill,
        "last": {},
        "alerts": [],
        "killed": False,
        "version": __version__,
    }
    _write_state_atomic(state, state_file)

    if startup_delay and startup_delay > 0:
        time.sleep(startup_delay)

    try:
        while True:
            if max_ticks is not None and tick >= int(max_ticks):
                stopped_reason = "max_ticks"
                break
            tick += 1

            cpu = monitor.sample_cpu()
            mem = sample_memory()
            cpu_pct = cpu.get("percent") if cpu.get("ok") else None
            mem_pct = mem.get("percent") if mem.get("ok") else None
            verdict = check_thresholds(cpu_pct, mem_pct, cpu_limit, mem_limit)

            last = {
                "tick": tick,
                "time": _now_str(),
                "cpu_pct": verdict["cpu_pct"],
                "mem_pct": verdict["mem_pct"],
                "cpu_ok": bool(cpu.get("ok")),
                "mem_ok": bool(mem.get("ok")),
                "cpu_error": cpu.get("error"),
                "mem_error": mem.get("error"),
                "total_mb": mem.get("total_mb"),
                "available_mb": mem.get("available_mb"),
                "used_mb": mem.get("used_mb"),
                "breach": verdict["breach"],
                "reasons": verdict["reasons"],
                "errors": verdict["errors"],
            }

            kill_result: Optional[Dict[str, Any]] = None
            if verdict["breach"]:
                # ── 告警行格式与需求一致：
                # [sw-guard][ALERT] 2026-10-12 22:30:00 CPU 占用 91.2% 超过阈值 85.0% → 立即关闭 SolidWorks
                reason_text = "；".join(verdict["reasons"])
                tail = "立即关闭 SolidWorks" if kill_enabled else "演练模式（--no-kill）不执行关闭"
                message = "[sw-guard][ALERT] %s %s → %s" % (last["time"], reason_text, tail)
                _utf8_out(message)

                if kill_enabled:
                    # 超阈值后**立即**关闭：这里不加任何 sleep，
                    # 机器已经在换页抖动，每一秒的延迟都可能让 GUI 彻底失去响应。
                    kill_result = kill_solidworks()
                    if kill_result.get("ok") and kill_result.get("method") == "taskkill":
                        killed = True  # 只有真的杀掉了才算 killed
                else:
                    kill_result = {"ok": True, "method": "skipped",
                                   "note": "演练模式：未执行 taskkill"}
                alert = {
                    "time": last["time"],
                    "tick": tick,
                    "reasons": list(verdict["reasons"]),
                    "cpu_pct": verdict["cpu_pct"],
                    "mem_pct": verdict["mem_pct"],
                    "message": message,
                    "kill": kill_result,
                }
                alerts.append(alert)
                alerts = _trim_alerts(alerts)  # ★ 防止 alerts 无限增长
                last["kill"] = kill_result

                # 日志落盘：JSONL，含完整关停结果，便于事后复盘
                _append_log(log_file, {"time": last["time"], "level": "ALERT",
                                       "pid": os.getpid(), "tick": tick,
                                       "cpu_pct": verdict["cpu_pct"],
                                       "mem_pct": verdict["mem_pct"],
                                       "reasons": verdict["reasons"],
                                       "kill": kill_result})

                # 用户回调：独立 try/except，回调写错不能连累救援
                if on_breach is not None:
                    try:
                        on_breach(alert)
                    except Exception as exc:
                        _utf8_out("[sw-guard][WARN] on_breach 回调异常（已忽略）：%r" % (exc,))

                state.update({"ticks": tick, "last": last, "alerts": alerts,
                              "killed": killed, "updated_at": last["time"]})
                _write_state_atomic(state, state_file)

                if stop_after_kill and kill_enabled:
                    # ── 【真实缺陷修复】只有【真的杀掉了 SW】才允许退出循环 ──────
                    # 现场实测：内存出现一次瞬时尖峰（95.77%），但当时 SolidWorks
                    #   **根本没在运行**（method="not_running"），看护却照样退出。
                    #   后果：等到用户真的开始建模时，**已经没有任何守护进程**了 ——
                    #   保护在最需要它的时刻恰好缺席。
                    # 现在只有 method == "taskkill"（确实执行了击杀）才退出；
                    #   SW 不在运行 / 击杀失败 / 未找到进程 → 继续守护，
                    #   等 SW 真的起来时仍然有人看着。
                    if killed:
                        stopped_reason = "killed"
                        break
                    if tick >= 3:
                        # 连续多次只在"没 SW 可杀"的情况下超阈值 → 说明是别的东西
                        # 在吃资源（编译/浏览器等），继续空转没有意义，但必须
                        # 明确标注原因，避免看起来像"守护正常结束"。
                        stopped_reason = "breach_but_sw_not_running"
                        break
                    stopped_reason = "breach_sw_not_running_continue"

            # 每个 tick 都更新状态文件（含未告警的 tick），保证"存活可观测"
            state.update({"ticks": tick, "last": last, "alerts": alerts,
                          "killed": killed, "updated_at": last["time"],
                          "ok": True})
            _write_state_atomic(state, state_file)

            if max_ticks is not None and tick >= int(max_ticks):
                stopped_reason = "max_ticks"
                break
            # 放在循环末尾 sleep：跑满 max_ticks 时不会多等一个 interval
            time.sleep(max(0.0, float(interval)))
    except KeyboardInterrupt:
        # Ctrl+C 属于正常的人工停止，不是错误
        stopped_reason = "interrupted"
        _utf8_out("[sw-guard] 收到中断信号（Ctrl+C），已停止监控。")
    except Exception as exc:  # 兜底：任何未预期异常都转成结构化结果，不裸抛
        stopped_reason = "error"
        fatal_error = repr(exc)
        _utf8_out("[sw-guard][ERROR] 主循环异常终止：%r" % (exc,))

    ended_at = _now_str()
    state.update({"ticks": tick, "last": last, "alerts": alerts,
                  "killed": killed, "updated_at": ended_at,
                  "stopped_reason": stopped_reason, "ok": fatal_error is None})
    _write_state_atomic(state, state_file)

    summary: Dict[str, Any] = {
        "ok": fatal_error is None,
        "ticks": tick,
        "alerts": alerts,
        "killed": killed,
        "last": last,
        "stopped_reason": stopped_reason,
        "started_at": started_at,
        "ended_at": ended_at,
        "interval": interval,
        "cpu_limit": cpu_limit,
        "mem_limit": mem_limit,
        "kill_enabled": kill_enabled,
        "stop_after_kill": stop_after_kill,
        "state_path": str(state_file),
        "log_path": str(log_file) if log_file else None,
    }
    if fatal_error:
        summary["error"] = fatal_error
    return summary


# ═══════════════════════════════════════════════════════════════════════════
# 九、CLI
# ═══════════════════════════════════════════════════════════════════════════
def _print_json(obj: Any) -> None:
    """统一 JSON 输出：ensure_ascii=False + indent=2，保证中文直接可读。"""
    _utf8_out(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def _health_check(state_path: Path = STATE_PATH,
                  window: float = 0.5) -> Dict[str, Any]:
    """做一次即时体检：采 CPU + 内存，给出阈值判定，**不杀任何进程**。"""
    monitor = SystemMonitor(min_window=window)
    cpu = monitor.sample_cpu(window)
    mem = sample_memory()
    cpu_pct = cpu.get("percent") if cpu.get("ok") else None
    mem_pct = mem.get("percent") if mem.get("ok") else None
    verdict = check_thresholds(cpu_pct, mem_pct)
    return {
        "ok": bool(cpu.get("ok") and mem.get("ok")),
        "time": _now_str(),
        "pid": os.getpid(),
        "cpu": cpu,
        "memory": mem,
        "verdict": verdict,
        "state_file": str(state_path),
    }


def _cmd_run(args: argparse.Namespace) -> int:
    """处理 `run` 子命令：启动监控循环。"""
    max_ticks = 1 if getattr(args, "once", False) else args.max_ticks
    # --no-kill = 演练模式：只告警、不执行 taskkill。
    # 名字直译就是"不杀"，所以它关掉的是"杀"这个动作本身，
    # 而不是"杀完之后是否退出循环"（后者由 --continue-after-kill 控制）。
    kill_enabled = not getattr(args, "no_kill", False)

    summary = run(
        interval=args.interval,
        cpu_limit=args.cpu,
        mem_limit=args.mem,
        log_path=args.log,
        max_ticks=max_ticks,
        stop_after_kill=not getattr(args, "continue_after_kill", False),
        kill_enabled=kill_enabled,
    )
    _print_json(summary)
    return 0 if summary.get("ok") else 1


def _cmd_check(args: argparse.Namespace) -> int:
    """处理 `check` 子命令：单次体检，只读不杀。

    无论是否带 --json 都输出 JSON（需求要求所有输出走 json.dumps 且中文可读）；
    --json 开关保留是为了让调用方显式声明"我要机器可解析的输出"。
    """
    result = _health_check()
    _print_json(result)
    return 0 if result.get("ok") else 1


def _cmd_state(args: argparse.Namespace) -> int:
    """处理 `state` 子命令：读并打印状态文件。"""
    data = read_state()
    _print_json(data)
    if data.get("ok"):
        return 0
    return 2  # 状态文件不存在 / 损坏，用独立退出码区分于"采样失败"


def build_parser() -> argparse.ArgumentParser:
    """构建 argparse 解析器（三个子命令：run / check / state）。"""
    parser = argparse.ArgumentParser(
        prog="sw_guard.py",
        description="SolidWorks 建模健康监控：CPU/内存超阈值即告警并关闭 SolidWorks。",
    )
    parser.add_argument("--version", action="version", version="sw_guard %s" % __version__)
    # 顶层 --once：等价于 check，便于脚本一句命令做即时体检
    parser.add_argument("--once", action="store_true",
                        help="单次采样模式（等价于 check），便于脚本调用做即时体检")

    sub = parser.add_subparsers(dest="command")

    p_run = sub.add_parser("run", help="启动监控主循环（每 interval 秒采样一次）")
    p_run.add_argument("--interval", type=float, default=10.0, help="采样间隔秒数，默认 10")
    p_run.add_argument("--cpu", type=float, default=85.0, help="CPU 阈值%%，默认 85")
    p_run.add_argument("--mem", type=float, default=85.0, help="内存阈值%%，默认 85")
    p_run.add_argument("--log", type=str, default=None,
                       help="日志文件路径，默认 engineering/tools/load_cases/sw_guard.log")
    p_run.add_argument("--max-ticks", type=int, default=None, help="最多采样次数（测试用）")
    p_run.add_argument("--no-kill", action="store_true",
                       help="演练模式：只告警不执行 taskkill（不会关闭 SolidWorks）")
    p_run.add_argument("--continue-after-kill", action="store_true",
                       help="关闭 SolidWorks 后继续监控（默认杀完即退出循环）")
    p_run.add_argument("--once", action="store_true", help="只跑一个 tick 就退出")
    p_run.set_defaults(func=_cmd_run)

    p_check = sub.add_parser("check", help="单次体检：打印当前 CPU/内存，不杀进程")
    p_check.add_argument("--json", action="store_true", help="以 JSON 输出（默认即 JSON）")
    p_check.set_defaults(func=_cmd_check)

    p_state = sub.add_parser("state", help="读取并打印状态文件")
    p_state.add_argument("--json", action="store_true", help="以 JSON 输出（默认即 JSON）")
    p_state.set_defaults(func=_cmd_state)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """CLI 入口，返回进程退出码。

    退出码约定：
        0 = 成功
        1 = 采样失败 / 运行期异常
        2 = 用法错误 或 状态文件不存在
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    if getattr(args, "command", None) is None:
        # 没给子命令：带 --once 就做一次体检；否则打印帮助并以用法错误退出
        if getattr(args, "once", False):
            return _cmd_check(args)
        parser.print_help(sys.stderr)
        return 2

    return int(args.func(args))


# 导入即修复控制台编码，保证任何使用方式下中文都不乱码（幂等且静默失败）
_setup_utf8()

if __name__ == "__main__":
    sys.exit(main())
