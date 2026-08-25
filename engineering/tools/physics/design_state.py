# -*- coding: utf-8 -*-
"""
design_state.py — 迭代状态管理
================================
记录每一轮迭代的参数变更、仿真结果、以及回滚点。
支持版本化追踪：每轮都有唯一 run_id，可通过 parent_run_id 建立链条。
"""
import os
import json
import time
from typing import Any, Optional


STATE_DIR_NAME = "physics_runs"


class DesignState:
    """管理一个物理闭环迭代的完整状态。"""

    def __init__(self, output_dir: str, load_case_path: str):
        self.output_dir = output_dir
        self.load_case_path = load_case_path
        self.runs: list[dict] = []
        self.current_run_id: Optional[str] = None
        self._load_state()

    def _load_state(self):
        """从磁盘加载已有状态（恢复中断的迭代）。"""
        state_file = os.path.join(self.output_dir, "_state.json")
        if os.path.exists(state_file):
            try:
                with open(state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.runs = data.get("runs", [])
                self.current_run_id = data.get("current_run_id")
            except Exception:
                self.runs = []
                self.current_run_id = None

    def _save_state(self):
        """持久化状态到磁盘。"""
        os.makedirs(self.output_dir, exist_ok=True)
        state_file = os.path.join(self.output_dir, "_state.json")
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump({
                "runs": self.runs,
                "current_run_id": self.current_run_id,
                "load_case_path": self.load_case_path,
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }, f, ensure_ascii=False, indent=2)

    def new_run(self, run_id: str) -> dict:
        """开始新一轮迭代，返回空状态模板。"""
        run = {
            "run_id": run_id,
            "iteration": len(self.runs) + 1,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "design_params": {},
            "geometry_check": {},
            "fea_result": {},
            "report_path": "",
            "status": "IN_PROGRESS",
            "parent_run_id": self.current_run_id,
            "changes_from_parent": {},
        }
        self.runs.append(run)
        self.current_run_id = run_id
        self._save_state()
        return run

    def update_run(self, run_id: str, updates: dict) -> dict:
        """更新指定轮次的状态。"""
        for run in self.runs:
            if run["run_id"] == run_id:
                run.update(updates)
                self._save_state()
                return run
        raise KeyError(f"run_id not found: {run_id}")

    def get_current(self) -> Optional[dict]:
        """获取当前轮次状态。"""
        for run in reversed(self.runs):
            if run["run_id"] == self.current_run_id:
                return run
        return None

    def get_history(self) -> list[dict]:
        """获取所有历史轮次。"""
        return list(self.runs)

    def get_last_passing(self) -> Optional[dict]:
        """获取最后一个通过的门禁/仿真的轮次（用于回滚）。"""
        for run in reversed(self.runs):
            if run.get("status") == "PASS":
                return run
        return None

    def add_screenshot(self, run_id: str, image_path: str):
        """为指定轮次添加截图记录。"""
        for run in self.runs:
            if run["run_id"] == run_id:
                if "screenshots" not in run:
                    run["screenshots"] = []
                run["screenshots"].append(image_path)
                self._save_state()
                return

    def get_convergence_data(self) -> dict:
        """提取收敛数据（安全系数、质量、位移随迭代的变化）。"""
        history = self.get_history()
        if not history:
            return {"series": [], "converged": False}

        series = []
        for run in history:
            fr = run.get("fea_result", {})
            series.append({
                "iteration": run.get("iteration", 0),
                "run_id": run.get("run_id", ""),
                "safety_factor": fr.get("safety_factor"),
                "max_stress_mpa": fr.get("max_von_mises_mpa"),
                "max_displacement_mm": fr.get("max_displacement_mm"),
                "volume_mm3": fr.get("volume_mm3"),
                "mass_kg": fr.get("mass_kg"),
                "status": run.get("status"),
            })

        # 判断是否收敛：最后3轮状态均为 PASS
        last_3 = series[-3:] if len(series) >= 3 else series
        converged = all(s.get("status") == "PASS" for s in last_3) if last_3 else False

        return {"series": series, "converged": converged, "total_iterations": len(series)}


def get_default_output_dir(base_dir: str = None) -> str:
    """获取默认输出目录。"""
    if base_dir is None:
        tools_dir = os.path.dirname(os.path.abspath(__file__))
        base_dir = os.path.join(tools_dir, "..", "..", "output")
    return os.path.join(base_dir, STATE_DIR_NAME)
