"""manip.force 闭环 checker：存活判定 + 稳态力跟踪误差。"""
from __future__ import annotations

from typing import Any, Optional

from autotest.eval.checker import IChecker, Score
from autotest.protocol.schema import decode_ground_truth, decode_observation


class ForceChecker(IChecker):
    """records 为 observation 外层信封列表（首帧为 RESET 观测，其后每 action 一步）。

    passed = 撑满 max_steps（未触发 force_exceeded）且末段 10% 平均 |f - f_target| ≤ settle_threshold。
    """

    name = "manip.force.track"
    consumes = ["manip.force.ManipObs"]

    def evaluate(
        self,
        records: list[Any],
        ground_truth: dict,
        config: Optional[dict[str, Any]] = None,
    ) -> Score:
        cfg = config or {}
        gt_data = decode_ground_truth(ground_truth)
        max_steps = int(gt_data.get("max_steps", 0))
        dt = float(gt_data.get("dt", 0.001))
        f_target = float(gt_data.get("target_force", 0.0))
        settle_threshold = float(cfg.get("settle_threshold", 0.5))

        forces = self._forces(records)
        if max_steps <= 0 or not forces or f_target <= 0:
            return Score(metrics={}, passed=False)

        executed = len(forces) - 1  # 首帧为 reset 观测，不计入执行步数
        survived = executed >= max_steps
        tail = forces[-max(1, len(forces) // 10):]
        settle_error = sum(abs(f - f_target) for f in tail) / len(tail)
        peak_force = max(forces)
        tracking = sum(1 for f in forces if abs(f - f_target) <= settle_threshold) / len(forces)

        passed = survived and settle_error <= settle_threshold
        return Score(
            metrics={
                "survived": 1.0 if survived else 0.0,
                "settle_error": round(settle_error, 6),
                "peak_force": round(peak_force, 4),
                "overshoot": round(max(0.0, peak_force - f_target), 4),
                "tracking_ratio": round(tracking, 4),
                "execution_time": round(executed * dt, 4),
            },
            passed=passed,
        )

    @staticmethod
    def _forces(records: list[Any]) -> list[float]:
        forces: list[float] = []
        for rec in records:
            if not isinstance(rec, dict) or rec.get("data", {}).get("schema") != "manip.force.ManipObs":
                continue
            forces.append(decode_observation(rec["data"]).f_contact)
        return forces
