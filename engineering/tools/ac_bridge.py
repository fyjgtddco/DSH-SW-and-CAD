# -*- coding: utf-8 -*-
"""
ac_bridge.py — AutoCAD COM 桥接（DSH 工程模式通用版）
======================================================
从 CADX 提取的 AutoCAD COM 桥接模块，适配 DSH 工程模式使用。

功能：
  - 连接运行中的 AutoCAD（COM 自动化）
  - 导出 DXF 文件（供 AI 读取验证）
  - 读取图纸实体（图层/线条/圆/文字/标注）
  - 高亮验证问题（在 AutoCAD 中标记）

依赖：
  - pywin32 (win32com, pythoncom)
  - 与 sw_bridge.py 使用相同的连接模式（dynamic dispatch）

用法：
  import ac_bridge
  ac = ac_bridge.connect()          # 连接 AutoCAD
  dxf_path = ac.export_dxf()        # 导出 DXF
  entities = ac.get_entities()      # 读取所有实体
  issues = ac.validate()            # 运行几何验证

【重要约束】
  - COM 操作使用 dynamic dispatch（不依赖类型库注册）
  - 坐标单位默认为 mm（与 swapi 一致）
  - AutoCAD busy 时使用 ESC 取消挂起命令后重试
"""
import os
import sys
import time
import json

import pythoncom
import win32com.client

# Fix encoding on Windows terminals
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass


# ==================== 常量 ====================
_VALIDATION_LAYER = "DSH_CADX_VALIDATION"
_COLOR_CRITICAL = 1    # 红色
_COLOR_WARNING = 4     # 黄色
_COLOR_INFO = 3        # 绿色


# ==================== 连接 ====================

def connect(timeout=30):
    """连接运行中的 AutoCAD 实例。

    返回 AutoCADBridge 对象；失败返回 None。
    """
    pythoncom.CoInitialize()
    bridge = AutoCADBridge()
    if bridge._do_connect(timeout=timeout):
        return bridge
    return None


class AutoCADBridge:
    """AutoCAD COM 桥接。所有 COM 操作在调用线程上执行（单线程模式）。

    与 sw_bridge.py / swapi.py 保持一致：使用 dynamic dispatch，
    不依赖类型库注册。
    """

    def __init__(self):
        self.acad = None
        self.doc = None
        self._connected = False
        self._drawing_name = None

    def _do_connect(self, timeout=30):
        """尝试连接 AutoCAD（带超时和重试）。"""
        deadline = time.time() + timeout
        esc_sent = False
        while time.time() < deadline:
            try:
                pythoncom.CoInitialize()
                self.acad = win32com.client.dynamic.Dispatch('AutoCAD.Application')
                self.acad.Visible = True
                self.doc = self.acad.ActiveDocument
                # 触发一次属性读取以验证连接
                _ = self.doc.Name
                self._drawing_name = self.doc.Name
                self._connected = True
                print(f"[ac_bridge] Connected to AutoCAD — document: {self._drawing_name}")
                return True
            except pythoncom.com_error as e:
                hr = getattr(e, 'hresult', None)
                # RPC_E_CALL_REJECTED (-2147418111) = AutoCAD busy
                if hr == -2147418111 and not esc_sent:
                    print("[ac_bridge] AutoCAD busy, sending ESC to cancel pending command...")
                    self._cancel_pending_command()
                    esc_sent = True
                    time.sleep(1.0)
                elif hr == -2147418111:
                    delay = 0.5 * (2 ** min(int((deadline - time.time()) * 2), 4))
                    print(f"[ac_bridge] AutoCAD still busy, retrying in {delay:.1f}s")
                    time.sleep(delay)
                    pythoncom.PumpWaitingMessages()
                else:
                    print(f"[ac_bridge] COM error (attempt): {e}")
                    time.sleep(1.0)
                    self.doc = None
                    self.acad = None
            except Exception as e:
                print(f"[ac_bridge] Connection failed: {e}")
                time.sleep(1.0)
                self.doc = None
                self.acad = None
        return False

    @property
    def connected(self):
        return self._connected

    @property
    def drawing_name(self):
        return self._drawing_name or ""

    @property
    def model_space(self):
        if not self.doc:
            raise RuntimeError("No active AutoCAD document")
        return self.doc.ModelSpace

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    def _cancel_pending_command(self):
        """发送 ESC 取消 AutoCAD 挂起的命令。"""
        try:
            hwnd = self.acad.HWND
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            user32.SetForegroundWindow(hwnd)
            time.sleep(0.3)
            shell = win32com.client.dynamic.Dispatch("WScript.Shell")
            for _ in range(5):
                shell.SendKeys("{ESC}")
                time.sleep(0.15)
            time.sleep(1.0)
        except Exception as e:
            print(f"[ac_bridge] _cancel_pending_command: {e}")

    def _point(self, x, y, z=0.0):
        """创建 COM 兼容的 3D 点数组。"""
        return win32com.client.VARIANT(
            pythoncom.VT_ARRAY | pythoncom.VT_R8, (float(x), float(y), float(z))
        )

    # ------------------------------------------------------------------
    # DXF 导出
    # ------------------------------------------------------------------

    def export_dxf(self, output_path=None):
        """将当前图纸导出为 DXF 文件。

        Args:
            output_path: 输出路径（可选），默认保存到临时目录
        Returns:
            DXF 文件路径
        """
        if not self.doc:
            raise RuntimeError("No active AutoCAD document")
        if output_path is None:
            base = os.path.splitext(self.doc.Name)[0]
            out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "output")
            os.makedirs(out_dir, exist_ok=True)
            output_path = os.path.join(out_dir, f"{base}_export.dxf")
        abs_path = os.path.abspath(output_path)
        # DXF 导出：SaveAs 参数 18 = DXF R2018
        self.doc.SaveAs(abs_path, 18)
        if os.path.exists(abs_path):
            return abs_path
        raise RuntimeError(f"DXF export failed: {abs_path} not created")

    # ------------------------------------------------------------------
    # 实体读取
    # ------------------------------------------------------------------

    def get_entities(self):
        """读取 ModelSpace 中所有实体，返回列表。

        每个实体包含：type, layer, handle, 几何数据（坐标/半径/面积等）。
        """
        if not self.doc:
            raise RuntimeError("No active AutoCAD document")
        entities = []
        ms = self.model_space
        for i in range(ms.Count):
            ent = ms.Item(i)
            info = {
                "index": i,
                "type": ent.EntityName,
                "layer": ent.Layer,
                "handle": ent.Handle,
                "color": ent.Color,
            }
            # LINE
            try:
                info["start"] = (ent.StartPoint[0], ent.StartPoint[1])
                info["end"] = (ent.EndPoint[0], ent.EndPoint[1])
                info["length"] = ent.Length
            except Exception:
                pass
            # CIRCLE / ARC
            try:
                info["center"] = (ent.Center[0], ent.Center[1])
                info["radius"] = ent.Radius
            except Exception:
                pass
            # LWPOLYLINE / POLYLINE
            try:
                coords = list(ent.Coordinates)
                points = [(coords[j], coords[j + 1]) for j in range(0, len(coords), 2)]
                info["points"] = points
                info["closed"] = bool(ent.Closed)
                info["area"] = ent.Area
            except Exception:
                pass
            # TEXT / MTEXT
            try:
                info["text"] = ent.TextString
            except Exception:
                pass
            # DIMENSION
            try:
                info["measurement"] = ent.Measurement
            except Exception:
                pass
            entities.append(info)
        return entities

    def get_entities_on_layer(self, layer_name):
        """获取指定图层上的所有实体。"""
        return [e for e in self.get_entities() if e.get("layer") == layer_name]

    def get_all_layers(self):
        """返回所有图层名称列表。"""
        if not self.doc:
            raise RuntimeError("No active AutoCAD document")
        return [self.doc.Layers.Item(i).Name for i in range(self.doc.Layers.Count)]

    # ------------------------------------------------------------------
    # 图层管理
    # ------------------------------------------------------------------

    def ensure_layer(self, name, color=7):
        """确保图层存在（不存在则创建），并设置颜色。"""
        if not self.doc:
            raise RuntimeError("No active AutoCAD document")
        try:
            self.doc.Layers.Add(name)
        except pythoncom.com_error:
            pass  # 已存在
        layer = self.doc.Layers.Item(name)
        layer.Color = color

    # ------------------------------------------------------------------
    # 问题高亮
    # ------------------------------------------------------------------

    def highlight_issue(self, x, y, message, severity="CRITICAL"):
        """在指定坐标处绘制标记（圆圈 + 文字标注）。

        Args:
            x, y: 坐标（mm）
            message: 问题描述
            severity: CRITICAL / WARNING / INFO
        """
        color_map = {
            "CRITICAL": _COLOR_CRITICAL,
            "WARNING": _COLOR_WARNING,
            "INFO": _COLOR_INFO,
        }
        color = color_map.get(severity, _COLOR_CRITICAL)
        self.ensure_layer(_VALIDATION_LAYER, color)
        try:
            center = self._point(x, y)
            circle = self.model_space.AddCircle(center, 500.0)
            circle.Layer = _VALIDATION_LAYER
            circle.Color = color
            # 文字标注（略高于标记）
            text_pt = self._point(x, y + 600)
            mtext = self.model_space.AddMText(text_pt, 5000.0, f"[{severity}] {message}")
            mtext.Layer = _VALIDATION_LAYER
            mtext.Color = color
            mtext.Height = 200.0
        except Exception as e:
            print(f"[ac_bridge] highlight_issue failed: {e}")

    def clear_validation_layer(self):
        """清除验证图层上的所有标记。"""
        try:
            self.send_command(f'-LAYDEL\nN\n{_VALIDATION_LAYER}\n\nY\n')
            time.sleep(0.5)
            self.ensure_layer(_VALIDATION_LAYER, _COLOR_CRITICAL)
        except Exception as e:
            print(f"[ac_bridge] clear_validation_layer: {e}")

    # ------------------------------------------------------------------
    # 绘图命令（供设计生成使用）
    # ------------------------------------------------------------------

    def send_command(self, command):
        """向 AutoCAD 发送命令字符串。"""
        if not self.doc:
            raise RuntimeError("No active AutoCAD document")
        self.doc.SendCommand(command + "\n")

    def draw_line(self, x1, y1, x2, y2, layer="0"):
        """画直线。"""
        p1 = self._point(x1, y1)
        p2 = self._point(x2, y2)
        line = self.model_space.AddLine(p1, p2)
        line.Layer = layer
        return line

    def draw_rectangle(self, x, y, width, height, layer="0"):
        """画矩形（闭合多段线）。"""
        points = win32com.client.VARIANT(
            pythoncom.VT_ARRAY | pythoncom.VT_R8,
            [x, y, x + width, y, x + width, y + height, x, y + height],
        )
        pline = self.model_space.AddLightWeightPolyline(points)
        pline.Closed = True
        pline.Layer = layer
        return pline

    def zoom_extents(self):
        """缩放至全部实体。"""
        self.send_command("ZOOM\nE\n")

    def regen(self):
        """重生成图纸。"""
        self.send_command("REGEN\n")

    # ------------------------------------------------------------------
    # 状态查询
    # ------------------------------------------------------------------

    def status(self):
        """返回连接状态摘要。"""
        return {
            "connected": self._connected,
            "drawing": self._drawing_name,
            "layer_count": len(self.get_all_layers()) if self._connected else 0,
            "entity_count": len(self.get_entities()) if self._connected else 0,
        }

    def close(self):
        """断开连接（不清闭 AutoCAD 本身）。"""
        self._connected = False
        self._drawing_name = None
        self.doc = None
        self.acad = None


# ==================== 便捷函数 ====================

def get_ac():
    """获取 AutoCAD 桥接实例（连接失败时返回 None）。"""
    return connect()
