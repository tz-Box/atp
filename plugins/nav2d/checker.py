"""nav2d 闭环 checker：到点 / 路径成功率 / 安全裕度。"""
from __future__ import annotations

import math
from typing import Any, Optional

from autotest.eval.checker import IChecker, Score
from autotest.protocol.schema import (
    StampedPose,
    decode_ground_truth,
    decode_observation,
)


class NavChecker(IChecker):
    name = "nav2d.default"
    consumes = ["nav2d.NavObs"]

    def evaluate(
        self,
        records: list[Any],
        ground_truth: dict,
        config: Optional[dict[str, Any]] = None,
    ) -> Score:
        cfg = config or {}
        gt_data = decode_ground_truth(ground_truth)
        goal = gt_data.get("goal")
        obstacles = gt_data.get("obstacles", [])
        # records 为 observation 外层信封（{timestamp, module, data}）列表，
        # data 解码为 NavData，取 (timestamp, robot_pose) 重建轨迹。
        trajectory = self._trajectory(records)
        if goal is None or not trajectory:
            return Score(metrics={}, passed=False)

        arrival_tolerance = float(cfg.get("arrival_tolerance", 0.2))
        safety_threshold = float(cfg.get("safety_margin", 0.3))

        last = trajectory[-1].pose
        arrived = math.hypot(last.x - goal[0], last.y - goal[1]) <= arrival_tolerance
        safety_margin = self._min_clearance(trajectory, obstacles)

        passed = arrived and safety_margin >= safety_threshold
        return Score(
            metrics={
                "arrived": 1.0 if arrived else 0.0,
                "safety_margin": safety_margin,
                "path_success": 1.0 if arrived else 0.0,
            },
            passed=passed,
        )

    @staticmethod
    def _trajectory(records: list[Any]) -> list[StampedPose]:
        trajectory: list[StampedPose] = []
        for rec in records:
            if not isinstance(rec, dict) or rec.get("data", {}).get("schema") != "nav2d.NavObs":
                continue
            obs = decode_observation(rec["data"])
            trajectory.append(StampedPose(timestamp=float(rec["timestamp"]), pose=obs.robot_pose))
        return trajectory

    @staticmethod
    def _min_clearance(records: list[StampedPose], obstacles: list[tuple]) -> float:
        if not obstacles:
            return float("inf")
        clearance = float("inf")
        for record in records:
            px, py = record.pose.x, record.pose.y
            for cx, cy, r in obstacles:
                margin = math.hypot(px - cx, py - cy) - r
                clearance = min(clearance, margin)
        return clearance
