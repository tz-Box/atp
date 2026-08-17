"""NAV 闭环 checker：到点 / 路径成功率 / 安全裕度。"""
from __future__ import annotations

import math
from typing import Optional

from autotest.eval import IChecker, Score
from autotest.protocol.schema import StampedPose
from autotest.world.base import GroundTruth


class NavChecker(IChecker):
    name = "nav"

    def evaluate(
        self,
        records: list[StampedPose],
        ground_truth: GroundTruth,
        config: Optional[dict] = None,
    ) -> Score:
        cfg = config or {}
        goal = ground_truth.data.get("goal")
        obstacles = ground_truth.data.get("obstacles", [])
        if goal is None or not records:
            return Score(metrics={}, passed=False)

        arrival_tolerance = float(cfg.get("arrival_tolerance", 0.2))
        safety_threshold = float(cfg.get("safety_margin", 0.3))

        last = records[-1].pose
        arrived = math.hypot(last.x - goal[0], last.y - goal[1]) <= arrival_tolerance
        safety_margin = self._min_clearance(records, obstacles)

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
