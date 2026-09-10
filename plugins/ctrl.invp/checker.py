"""ctrl.invp 闭环 checker：存活判定 + 稳态误差。"""
from __future__ import annotations

from typing import Any, Optional

from autotest.eval.checker import IChecker, Score, judged
from autotest.protocol.schema import decode_ground_truth, decode_observation


class InvpChecker(IChecker):
    """records 为 observation 外层信封列表（首帧为 RESET 观测，其后每 action 一步）。

    passed = 撑满 max_steps（survived）且末段 10% 平均 |theta| ≤ settle_threshold。
    """

    name = "ctrl.invp.upright"
    consumes = ["ctrl.invp.InvpObs"]

    def evaluate(
        self,
        records: list[Any],
        ground_truth: dict,
        config: Optional[dict[str, Any]] = None,
    ) -> Score:
        cfg = config or {}
        gt_data = decode_ground_truth(ground_truth)
        max_steps = int(gt_data.get("max_steps", 0))
        dt = float(gt_data.get("dt", 0.02))
        theta_limit = float(gt_data.get("theta_limit", 0.2095))
        settle_threshold = float(cfg.get("settle_threshold", 0.02))
        # 判据清单先于早退声明（A11 批 0）：survived / settle_error 是判据，
        # 其余指标（survival_time 等）是观测事实，不设判据。
        criteria = (("survived", "survived == 1（撑满 max_steps）"),
                    ("settle_error", f"settle_error <= {settle_threshold}"))

        thetas = self._thetas(records)
        if max_steps <= 0 or not thetas:
            return Score.not_run(criteria)

        executed = len(thetas) - 1  # 首帧为 reset 观测，不计入执行步数
        survived = executed >= max_steps
        tail = thetas[-max(1, len(thetas) // 10):]
        settle_error = sum(abs(t) for t in tail) / len(tail)
        max_abs_theta = max(abs(t) for t in thetas)
        upright = sum(1 for t in thetas if abs(t) <= theta_limit) / len(thetas)

        settle_rounded = round(settle_error, 6)
        return Score.from_judgements(
            metrics={
                "survived": 1.0 if survived else 0.0,
                "survival_time": round(executed * dt, 4),
                "max_abs_theta": round(max_abs_theta, 6),
                "settle_error": settle_rounded,
                "upright_ratio": round(upright, 4),
            },
            judgements=[
                judged("survived", criteria[0][1], survived, 1.0 if survived else 0.0),
                judged("settle_error", criteria[1][1],
                       settle_error <= settle_threshold, settle_rounded),
            ],
        )

    @staticmethod
    def _thetas(records: list[Any]) -> list[float]:
        thetas: list[float] = []
        for rec in records:
            if not isinstance(rec, dict) or rec.get("data", {}).get("schema") != "ctrl.invp.InvpObs":
                continue
            thetas.append(decode_observation(rec["data"]).theta)
        return thetas
