# -*- coding: utf-8 -*-
"""
ac_validate.py — DXF / AutoCAD 图纸几何验证引擎
==================================================
从 CADX 提取并适配的机械制图验证引擎，供 DSH 工程模式使用。

功能：
  - 加载 DXF 文件（ezdxf）或直接读取 AutoCAD 实体（ac_bridge）
  - 7 项几何/结构验证检查
  - 返回 Issue 列表（含 severity / location / fix_suggestion）

检查项（7项）：
  1. UNCLOSED_POLYLINE  — 应闭合但未闭合的闭合多段线
  2. OVERLAPPING_GEOMETRY — 墙体/轮廓线错误重叠
  3. ROOM_TOO_SMALL      — 房间面积低于最低标准
  4. ROOM_ASPECT_RATIO   — 房间长宽比异常
  5. WALL_TOO_THIN       — 平行墙间距低于最小厚度
  6. MISSING_ELEMENTS    — 房间缺少门/窗
  7. DIMENSION_MISMATCH  — 标注值与实际几何不符

依赖：
  - ezdxf（用于 DXF 解析，可选：没有时回退到直接读取 AutoCAD）
  - shapely（用于几何运算，可选：没有时跳过重叠检查）
  - ac_bridge（用于实时读取 AutoCAD 实体）

用法（DXF 文件）：
  from ac_validate import validate_dxf
  issues = validate_dxf("output/my_drawing.dxf", rules="mechanical")

用法（直连 AutoCAD）：
  import ac_bridge
  from ac_validate import validate_live
  ac = ac_bridge.connect()
  issues = validate_live(ac, rules="mechanical")
"""
import json
import math
import os
import re
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any


# ==================== Issue 数据类 ====================

@dataclass
class Issue:
    """验证问题。"""
    id: int
    check: str          # 检查类型
    severity: str       # CRITICAL / WARNING / INFO
    message: str
    location_x: float = 0.0
    location_y: float = 0.0
    entity_handle: str = ""
    entity_layer: str = ""
    rule_key: str = ""
    expected: str = ""
    actual: str = ""
    suggested_fix: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ==================== 规则加载 ====================

_RULES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ac_rules.json")

_DEFAULT_RULES = {
    "min_room_area_sqm": 9.5,
    "min_wall_thickness_mm": 150,
    "max_wall_thickness_mm": 300,
    "min_door_width_mm": 750,
    "max_door_width_mm": 1200,
    "min_window_area_sqm": 0.5,
    "ventilation_pct_of_floor": 10,
    "max_stair_riser_mm": 160,
    "min_stair_tread_mm": 250,
    "min_ceiling_height_m": 2.7,
    "required_layers": ["WALLS", "DOORS", "WINDOWS", "DIMENSIONS"],
    "layer_name_pattern": r"^[A-Z][A-Z0-9_-]*$",
    "min_room_width_m": 2.4,
    "min_room_length_m": 3.0,
    "max_room_aspect_ratio": 3.0,
    "unit_scale": 1.0,
    "gap_threshold_mm": 10.0,
}


def _load_rules(rules: str = "mechanical") -> dict:
    """加载验证规则。

    Args:
        rules: "mechanical" | "general" | "residential" | 自定义 JSON 路径
    """
    if os.path.isfile(rules):
        with open(rules, "r", encoding="utf-8") as f:
            return {**_DEFAULT_RULES, **json.load(f)}

    rules_map = {
        "mechanical": {
            "min_room_area_sqm": 6.0,
            "min_wall_thickness_mm": 100,
            "max_wall_thickness_mm": 500,
            "min_door_width_mm": 700,
            "max_door_width_mm": 2000,
            "required_layers": ["OUTLINE", "THIN", "CENTER", "HIDDEN", "DIM", "TEXT"],
            "layer_name_pattern": r"^[A-Z][A-Z0-9_-]*$",
        },
        "general": {
            "min_room_area_sqm": 4.0,
            "min_wall_thickness_mm": 100,
            "max_wall_thickness_mm": 500,
            "min_door_width_mm": 600,
            "max_door_width_mm": 2000,
            "required_layers": [],
        },
        "residential": {
            "min_room_area_sqm": 9.5,
            "min_wall_thickness_mm": 150,
            "max_wall_thickness_mm": 300,
            "min_door_width_mm": 750,
            "max_door_width_mm": 1200,
            "required_layers": ["WALLS", "DOORS", "WINDOWS"],
        },
    }
    return {**_DEFAULT_RULES, **(rules_map.get(rules, {}))}


# ==================== DXF 分析器（基于 ezdxf） ====================

class DXFAnalyzer:
    """基于 ezdxf 的 DXF 文件验证引擎。"""

    def __init__(self, dxf_path: str, rules: str = "mechanical"):
        self.dxf_path = dxf_path
        self.rules = _load_rules(rules)
        self.scale = self.rules.get("unit_scale", 1.0)
        self.issues: List[Issue] = []
        self._counter = 0
        self._doc = None
        self._msp = None
        self._load_dxf()

    def _load_dxf(self):
        """加载 DXF 文件。"""
        try:
            import ezdxf
            self._doc = ezdxf.readfile(self.dxf_path)
            self._msp = self._doc.modelspace()
        except ImportError:
            raise RuntimeError(
                "ezdxf 未安装，无法验证 DXF 文件。\n"
                "请运行: pip install ezdxf shapely"
            )
        except Exception as e:
            raise RuntimeError(f"无法打开 DXF 文件: {e}")

    def _next_id(self) -> int:
        self._counter += 1
        return self._counter

    def _to_mm(self, value: float) -> float:
        return value * self.scale

    def _to_sqm(self, area: float) -> float:
        return area * (self.scale ** 2) / 1_000_000

    # ------------------------------------------------------------------
    # 实体辅助
    # ------------------------------------------------------------------

    def _entities_on_layer(self, layer: str):
        """按图层过滤实体（大小写不敏感）。"""
        for e in self._msp:
            if e.dxf.layer.upper() == layer.upper():
                yield e

    def _all_layers(self) -> list:
        """收集文档中所有已使用的图层名称（包括实体实际所在图层）。

        Bug-3 修复: 原来只读文档级图层表（可能只有 ['0', 'Defpoints']），
        但实体可能挂在未注册的自定义层上，导致 MISSING_LAYER 误报。
        现在同时扫描实体所在的图层，确保检测到所有实际使用的层。
        """
        layers = set()
        try:
            for l in self._doc.layers:
                layers.add(l.dxf.name)
        except Exception:
            pass
        # 从实体实际所在图层收集（兜底，防止文档层表不完整）
        for e in self._msp:
            try:
                ln = getattr(e.dxf, 'layer', None)
                if ln:
                    layers.add(ln)
            except Exception:
                pass
        return list(layers)

    def _entity_midpoint(self, entity) -> tuple:
        """返回实体的中点坐标（近似）。"""
        try:
            if entity.dxftype() == "LINE":
                s, e = entity.dxf.start, entity.dxf.end
                return ((s.x + e.x) / 2, (s.y + e.y) / 2)
            if entity.dxftype() == "LWPOLYLINE":
                pts = list(entity.get_points(format="xy"))
                if pts:
                    return (sum(p[0] for p in pts) / len(pts),
                            sum(p[1] for p in pts) / len(pts))
            if entity.dxftype() == "CIRCLE":
                c = entity.dxf.center
                return (c.x, c.y)
        except Exception:
            pass
        return (0.0, 0.0)

    # ------------------------------------------------------------------
    # 检查 1：未闭合的多段线
    # ------------------------------------------------------------------

    def check_unclosed_polylines(self):
        gap_mm = self.rules.get("gap_threshold_mm", 10.0)
        for ent in self._msp:
            if ent.dxftype() != "LWPOLYLINE":
                continue
            if ent.closed:
                continue
            pts = list(ent.get_points(format="xy"))
            if len(pts) < 3:
                continue
            dist = math.dist(pts[0], pts[-1])
            if dist < gap_mm:
                mx, my = self._entity_midpoint(ent)
                self.issues.append(Issue(
                    id=self._next_id(),
                    check="UNCLOSED_POLYLINE",
                    severity="INFO",
                    message=f"多段线近似闭合（缺口={dist:.1f}mm），建议闭合",
                    location_x=mx, location_y=my,
                    entity_handle=ent.dxf.handle,
                    entity_layer=ent.dxf.layer,
                    suggested_fix="将多段线闭合（PEDIT → Close）",
                ))
            else:
                mx, my = self._entity_midpoint(ent)
                self.issues.append(Issue(
                    id=self._next_id(),
                    check="UNCLOSED_POLYLINE",
                    severity="WARNING",
                    message=f"未闭合多段线（缺口={dist:.1f}mm）— 图层 '{ent.dxf.layer}'",
                    location_x=mx, location_y=my,
                    entity_handle=ent.dxf.handle,
                    entity_layer=ent.dxf.layer,
                    expected="闭合多段线",
                    actual=f"开口（缺口={dist:.1f}mm）",
                    suggested_fix="闭合多段线端点",
                ))

    # ------------------------------------------------------------------
    # 检查 2：重叠几何（需要 shapely）
    # ------------------------------------------------------------------

    def check_overlapping_geometry(self):
        """检测重叠的墙体/轮廓线。"""
        try:
            from shapely.geometry import LineString, Polygon
            from shapely.ops import unary_union
        except ImportError:
            return  # shapely 未安装，跳过此检查

        wall_layers = {"WALLS", "WALL", "WALLS-EXT", "WALLS-INT", "A-WALL",
                        "OUTLINE", "THIN"}
        wall_geoms = []

        for ent in self._msp:
            if ent.dxf.layer.upper() not in wall_layers:
                continue
            if ent.dxftype() == "LINE":
                s, e = ent.dxf.start, ent.dxf.end
                wall_geoms.append((ent, LineString([(s.x, s.y), (e.x, e.y)])))
            elif ent.dxftype() == "LWPOLYLINE":
                try:
                    pts = [(p[0], p[1]) for p in ent.get_points(format="xy")]
                    if len(pts) >= 2:
                        geom = LineString(pts) if not ent.closed else Polygon(pts)
                        wall_geoms.append((ent, geom))
                except Exception:
                    pass

        for i in range(len(wall_geoms)):
            for j in range(i + 1, len(wall_geoms)):
                e1, g1 = wall_geoms[i]
                e2, g2 = wall_geoms[j]
                try:
                    if not g1.intersects(g2):
                        continue
                    inter = g1.intersection(g2)
                    if inter.is_empty:
                        continue
                    if hasattr(inter, 'area') and inter.area > 100:
                        cx, cy = inter.centroid.x, inter.centroid.y
                        self.issues.append(Issue(
                            id=self._next_id(),
                            check="OVERLAPPING_GEOMETRY",
                            severity="CRITICAL",
                            message=f"重叠几何：{e1.dxf.handle} ↔ {e2.dxf.handle}",
                            location_x=cx, location_y=cy,
                            entity_handle=e1.dxf.handle,
                            entity_layer=e1.dxf.layer,
                            suggested_fix="重新绘制重叠的墙体或轮廓线",
                        ))
                    elif hasattr(inter, 'length') and inter.length > 50:
                        cx, cy = inter.centroid.x, inter.centroid.y
                        self.issues.append(Issue(
                            id=self._next_id(),
                            check="OVERLAPPING_GEOMETRY",
                            severity="WARNING",
                            message=f"交叉/重合线段：{e1.dxf.handle} ↔ {e2.dxf.handle}（长度={inter.length:.0f}mm）",
                            location_x=cx, location_y=cy,
                            entity_handle=e1.dxf.handle,
                            entity_layer=e1.dxf.layer,
                            suggested_fix="检查线条是否意图交叉或需断开",
                        ))
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # 检查 3 & 4：房间面积与长宽比
    # ------------------------------------------------------------------

    def check_room_areas(self):
        min_area = self.rules.get("min_room_area_sqm", 9.5)
        max_ratio = self.rules.get("max_room_aspect_ratio", 3.0)
        room_layers = {"ROOMS", "ROOM", "A-ROOM", "SPACE"}

        try:
            from shapely.geometry import Polygon
        except ImportError:
            return

        for ent in self._msp:
            if ent.dxf.layer.upper() not in room_layers:
                continue
            if ent.dxftype() != "LWPOLYLINE" or not ent.closed:
                continue
            try:
                pts = [(p[0], p[1]) for p in ent.get_points(format="xy")]
                if len(pts) < 3:
                    continue
                poly = Polygon(pts)
                area_sqm = self._to_sqm(poly.area)
                bounds = poly.bounds
                width_m = (bounds[2] - bounds[0]) * self.scale / 1000
                height_m = (bounds[3] - bounds[1]) * self.scale / 1000

                if area_sqm < min_area:
                    cx, cy = poly.centroid.x, poly.centroid.y
                    self.issues.append(Issue(
                        id=self._next_id(),
                        check="ROOM_TOO_SMALL",
                        severity="CRITICAL",
                        message=f"房间面积 {area_sqm:.1f}m² 低于最低标准 {min_area}m²",
                        location_x=cx, location_y=cy,
                        entity_handle=ent.dxf.handle,
                        entity_layer=ent.dxf.layer,
                        rule_key="min_room_area_sqm",
                        expected=f"≥ {min_area}m²",
                        actual=f"{area_sqm:.1f}m²",
                        suggested_fix=f"增大房间尺寸，当前缺口 {min_area - area_sqm:.1f}m²",
                    ))

                if width_m > 0 and height_m > 0:
                    ratio = max(width_m, height_m) / min(width_m, height_m)
                    if ratio > max_ratio:
                        cx, cy = poly.centroid.x, poly.centroid.y
                        self.issues.append(Issue(
                            id=self._next_id(),
                            check="ROOM_ASPECT_RATIO",
                            severity="WARNING",
                            message=f"房间长宽比 {ratio:.1f} 超过最大允许值 {max_ratio}",
                            location_x=cx, location_y=cy,
                            entity_handle=ent.dxf.handle,
                            entity_layer=ent.dxf.layer,
                            expected=f"≤ {max_ratio}",
                            actual=f"{ratio:.1f}",
                            suggested_fix="调整房间比例使其更均衡",
                        ))
            except Exception:
                pass

    # ------------------------------------------------------------------
    # 检查 5：墙体厚度
    # ------------------------------------------------------------------

    def check_wall_thickness(self):
        min_thick = self.rules.get("min_wall_thickness_mm", 150)
        max_thick = self.rules.get("max_wall_thickness_mm", 300)
        wall_layers = {"WALLS", "WALL", "WALLS-EXT", "WALLS-INT", "A-WALL", "OUTLINE"}

        lines = []
        for ent in self._msp:
            if ent.dxf.layer.upper() not in wall_layers:
                continue
            if ent.dxftype() == "LINE":
                s = ent.dxf.start
                e = ent.dxf.end
                lines.append((ent, s.x, s.y, e.x, e.y))

        for i in range(len(lines)):
            for j in range(i + 1, len(lines)):
                e1, x1, y1, x2, y2 = lines[i]
                e2, x3, y3, x4, y4 = lines[j]
                # 方向向量
                dx1, dy1 = x2 - x1, y2 - y1
                dx2, dy2 = x4 - x3, y4 - y3
                len1 = math.sqrt(dx1**2 + dy1**2)
                len2 = math.sqrt(dx2**2 + dy2**2)
                if len1 < 1 or len2 < 1:
                    continue
                # 平行检测（叉积 ≈ 0）
                cross = abs(dx1 * dy2 - dy1 * dx2)
                if cross / (len1 * len2) > 0.05:
                    continue
                # 中线点投影
                mx2, my2 = (x3 + x4) / 2, (y3 + y4) / 2
                t = ((mx2 - x1) * dx1 + (my2 - y1) * dy1) / (len1 ** 2)
                if t < -0.1 or t > 1.1:
                    continue
                proj_x = x1 + dx1 * t
                proj_y = y1 + dy1 * t
                dist_mm = math.sqrt((mx2 - proj_x)**2 + (my2 - proj_y)**2) * self.scale
                if dist_mm > 600:
                    continue
                cx = (x1 + x4) / 2
                cy = (y1 + y4) / 2
                if 10 < dist_mm < min_thick:
                    self.issues.append(Issue(
                        id=self._next_id(),
                        check="WALL_TOO_THIN",
                        severity="CRITICAL",
                        message=f"墙厚 {dist_mm:.0f}mm < 最小 {min_thick}mm",
                        location_x=cx, location_y=cy,
                        entity_handle=e1.dxf.handle,
                        entity_layer=e1.dxf.layer,
                        expected=f"≥ {min_thick}mm",
                        actual=f"{dist_mm:.0f}mm",
                        suggested_fix=f"加宽墙体，当前不足 {min_thick - dist_mm:.0f}mm",
                    ))
                elif dist_mm > max_thick:
                    self.issues.append(Issue(
                        id=self._next_id(),
                        check="WALL_TOO_THICK",
                        severity="WARNING",
                        message=f"墙厚 {dist_mm:.0f}mm > 最大 {max_thick}mm",
                        location_x=cx, location_y=cy,
                        entity_handle=e1.dxf.handle,
                        entity_layer=e1.dxf.layer,
                        expected=f"≤ {max_thick}mm",
                        actual=f"{dist_mm:.0f}mm",
                        suggested_fix="确认墙厚是否合理，如非设计意图则缩减",
                    ))

    # ------------------------------------------------------------------
    # 检查 6：缺失元素（房间无门/窗）
    # ------------------------------------------------------------------

    def check_missing_elements(self):
        room_layers = {"ROOMS", "ROOM", "A-ROOM", "SPACE"}
        door_layers = {"DOORS", "DOOR", "A-DOOR"}
        window_layers = {"WINDOWS", "WINDOW", "A-WINDOW"}

        rooms, doors, windows = [], [], []

        for ent in self._msp:
            layer = ent.dxf.layer.upper()
            if layer in room_layers and ent.dxftype() == "LWPOLYLINE" and ent.closed:
                rooms.append(ent)
            elif layer in door_layers:
                mx, my = self._entity_midpoint(ent)
                doors.append((mx, my))
            elif layer in window_layers:
                mx, my = self._entity_midpoint(ent)
                windows.append((mx, my))

        try:
            from shapely.geometry import Polygon
        except ImportError:
            return

        for ent in rooms:
            try:
                pts = [(p[0], p[1]) for p in ent.get_points(format="xy")]
                if len(pts) < 3:
                    continue
                poly = Polygon(pts)
                buffered = poly.buffer(500 * self.scale)  # 500mm 缓冲
                cx, cy = poly.centroid.x, poly.centroid.y

                has_door = any(buffered.contains(
                    Polygon([(dx-1, dy-1), (dx+1, dy-1), (dx+1, dy+1), (dx-1, dy+1)])
                ) for dx, dy in doors) if doors else True

                has_window = any(buffered.contains(
                    Polygon([(wx-1, wy-1), (wx+1, wy-1), (wx+1, wy+1), (wx-1, wy+1)])
                ) for wx, wy in windows) if windows else True

                if not has_door and doors:
                    self.issues.append(Issue(
                        id=self._next_id(),
                        check="ROOM_NO_DOOR",
                        severity="WARNING",
                        message=f"房间（图层 '{ent.dxf.layer}'）附近无门",
                        location_x=cx, location_y=cy,
                        entity_handle=ent.dxf.handle,
                        suggested_fix="在房间墙上添加门开口",
                    ))
                if not has_window and windows:
                    self.issues.append(Issue(
                        id=self._next_id(),
                        check="ROOM_NO_WINDOW",
                        severity="INFO",
                        message=f"房间（图层 '{ent.dxf.layer}'）附近无窗",
                        location_x=cx, location_y=cy,
                        entity_handle=ent.dxf.handle,
                        suggested_fix="添加窗户以满足通风要求（≥10% 地板面积）",
                    ))
            except Exception:
                pass

    # ------------------------------------------------------------------
    # 检查 7：标注一致性
    # ------------------------------------------------------------------

    def check_dimension_consistency(self):
        for ent in self._msp:
            if ent.dxftype() != "DIMENSION":
                continue
            try:
                text_override = ent.dxf.get("text", "")
                if not text_override or text_override.strip() in ("", "<>"):
                    continue
                try:
                    displayed = float(re.sub(r"[^\d.]", "", text_override))
                except ValueError:
                    continue
                # 实际测量值
                try:
                    measurement = ent.dxf.get("actual_measurement", None)
                    if measurement is None:
                        continue
                    tolerance = max(abs(measurement) * 0.02, 1.0)
                    if abs(displayed - measurement) > tolerance:
                        mx, my = self._entity_midpoint(ent)
                        self.issues.append(Issue(
                            id=self._next_id(),
                            check="DIMENSION_MISMATCH",
                            severity="CRITICAL",
                            message=f"标注显示 {displayed}，实际几何为 {measurement:.1f}",
                            location_x=mx, location_y=my,
                            entity_handle=ent.dxf.handle,
                            expected=f"{measurement:.1f}",
                            actual=f"{displayed}",
                            suggested_fix="更新标注值以匹配实际几何，或调整几何",
                        ))
                except Exception:
                    pass
            except Exception:
                pass

    # ------------------------------------------------------------------
    # 检查 8：图层命名规范
    # ------------------------------------------------------------------

    def check_layer_naming(self):
        existing = set(self._all_layers())
        pattern = self.rules.get("layer_name_pattern", "")
        required = self.rules.get("required_layers", [])

        for req in required:
            if req.upper() not in {l.upper() for l in existing}:
                self.issues.append(Issue(
                    id=self._next_id(),
                    check="MISSING_LAYER",
                    severity="WARNING",
                    message=f"缺少必需图层 '{req}'",
                    rule_key="required_layers",
                    expected=req,
                    actual="缺失",
                    suggested_fix=f"创建图层 '{req}' 并将相关实体移至该图层",
                ))

        if pattern:
            import re as _re
            for ln in existing:
                if ln in ("0", "DEFPOINTS", "DSH_CADX_VALIDATION"):
                    continue
                if not _re.match(pattern, ln):
                    self.issues.append(Issue(
                        id=self._next_id(),
                        check="LAYER_NAME_INVALID",
                        severity="INFO",
                        message=f"图层 '{ln}' 不符合命名规范",
                        entity_layer=ln,
                        rule_key="layer_name_pattern",
                        expected=f"正则: {pattern}",
                        actual=ln,
                        suggested_fix="重命名图层（大写、字母数字和下划线）",
                    ))

    # ------------------------------------------------------------------
    # 执行全部检查
    # ------------------------------------------------------------------

    def validate(self) -> List[Issue]:
        self.issues.clear()
        self._counter = 0
        self.check_unclosed_polylines()
        self.check_overlapping_geometry()
        self.check_room_areas()
        self.check_wall_thickness()
        self.check_layer_naming()
        self.check_missing_elements()
        self.check_dimension_consistency()
        return self.issues

    def get_summary(self) -> dict:
        critical = sum(1 for i in self.issues if i.severity == "CRITICAL")
        warning = sum(1 for i in self.issues if i.severity == "WARNING")
        info = sum(1 for i in self.issues if i.severity == "INFO")
        return {
            "total_issues": len(self.issues),
            "critical": critical,
            "warning": warning,
            "info": info,
            "status": "FAILED" if critical > 0 else ("REVIEW" if warning > 0 else "PASSED"),
            "drawing": self.dxf_path,
            "checks_run": 7,
            "layers": list(self._all_layers()),
        }

    def get_issues_dicts(self) -> List[dict]:
        return [i.to_dict() for i in self.issues]

    def get_context(self) -> dict:
        """返回图纸元数据（供 LLM 分析用）。"""
        type_counts = {}
        layer_counts = {}
        for e in self._msp:
            t = e.dxftype()
            type_counts[t] = type_counts.get(t, 0) + 1
            l = e.dxf.layer
            layer_counts[l] = layer_counts.get(l, 0) + 1
        return {
            "file": self.dxf_path,
            "entity_count": sum(type_counts.values()),
            "entity_types": type_counts,
            "layers": layer_counts,
        }


# ==================== 便捷函数 ====================

def validate_dxf(dxf_path: str, rules: str = "mechanical") -> dict:
    """验证 DXF 文件，返回 {ok, summary, issues}。"""
    analyzer = DXFAnalyzer(dxf_path, rules)
    analyzer.validate()
    return {
        "ok": True,
        "summary": analyzer.get_summary(),
        "issues": analyzer.get_issues_dicts(),
        "context": analyzer.get_context(),
    }


def validate_live(ac_bridge, rules: str = "mechanical") -> dict:
    """从 Live AutoCAD 读取并验证（需要 ezdxf 导出 DXF 后验证）。

    先导出 DXF，再调用 validate_dxf。
    """
    try:
        dxf_path = ac_bridge.export_dxf()
        return validate_dxf(dxf_path, rules)
    except Exception as e:
        return {"ok": False, "error": f"live validation failed: {e}"}
