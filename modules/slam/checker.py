"""SLAM 开环 checker：APE / RPE（绝对/相对轨迹误差）。"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np

from autotest.eval import IChecker, Score
from autotest.protocol.schema import Pose, StampedPose
from autotest.world.base import GroundTruth
from autotest.protocol.data.slam import CylinderResult


class SlamChecker(IChecker):
    name = "slam"

    def evaluate(
        self,
        records: list,
        ground_truth: GroundTruth,
        config: Optional[dict] = None,
    ) -> Score:
        cfg = config or {}
        gt_trajectory = ground_truth.data.get("trajectory", [])
        # records 为 Result 信封列表，SLAM 的 result.data 是 StampedPose。
        stamped = [r.data for r in records if isinstance(r.data, StampedPose)]
        if not stamped or not gt_trajectory:
            return Score(metrics={}, passed=False)

        est, ref = self._match_by_time(
            stamped, gt_trajectory, float(cfg.get("time_tolerance", 0.05))
        )
        if len(est) < 2:
            return Score(metrics={}, passed=False)

        est_pts = np.array([[p.x, p.y, p.z] for p in est], dtype=float)
        ref_pts = np.array([[p.x, p.y, p.z] for p in ref], dtype=float)

        ate = self._ate(est_pts, ref_pts)
        rpe = self._rpe(est_pts, ref_pts, int(cfg.get("rpe_delta", 1)))

        ate_threshold = float(cfg.get("ate_threshold", 0.2))
        rpe_threshold = float(cfg.get("rpe_threshold", 0.1))
        passed = ate <= ate_threshold and rpe <= rpe_threshold

        return Score(metrics={"ate_rmse": ate, "rpe_rmse": rpe}, passed=passed)

    @staticmethod
    def _match_by_time(
        records: list[StampedPose], gt: list[StampedPose], tolerance: float
    ) -> tuple[list[Pose], list[Pose]]:
        gt_sorted = sorted(gt, key=lambda g: g.timestamp)
        est: list[Pose] = []
        ref: list[Pose] = []
        for record in records:
            index = min(
                range(len(gt_sorted)),
                key=lambda i: abs(gt_sorted[i].timestamp - record.timestamp),
            )
            if abs(gt_sorted[index].timestamp - record.timestamp) <= tolerance:
                est.append(record.pose)
                ref.append(gt_sorted[index].pose)
        return est, ref

    @staticmethod
    def _ate(est: np.ndarray, ref: np.ndarray) -> float:
        rotation, translation, scale = _umeyama(est, ref)
        aligned = scale * est @ rotation.T + translation
        return float(np.sqrt(np.mean(np.sum((aligned - ref) ** 2, axis=1))))

    @staticmethod
    def _rpe(est: np.ndarray, ref: np.ndarray, delta: int) -> float:
        count = len(est) - delta
        if count <= 0:
            return 0.0
        errors = []
        for i in range(count):
            est_delta = np.linalg.norm(est[i + delta] - est[i])
            ref_delta = np.linalg.norm(ref[i + delta] - ref[i])
            errors.append((est_delta - ref_delta) ** 2)
        return float(np.sqrt(np.mean(errors)))


def _umeyama(src: np.ndarray, dst: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    """求解 dst ≈ scale * src @ R.T + t 的相似变换（含尺度）。"""
    n = src.shape[0]
    mu_src = src.mean(axis=0)
    mu_dst = dst.mean(axis=0)
    src_c = src - mu_src
    dst_c = dst - mu_dst

    covariance = dst_c.T @ src_c / n
    u, d, vt = np.linalg.svd(covariance)

    s = np.eye(3)
    if np.linalg.det(u) * np.linalg.det(vt) < 0:
        s[2, 2] = -1
    rotation = u @ s @ vt

    var_src = np.sum(src_c**2) / n
    scale = float(np.trace(np.diag(d) @ s) / var_src) if var_src > 0 else 1.0
    translation = mu_dst - scale * rotation @ mu_src
    return rotation, translation, scale


class PipeChecker(IChecker):
    """管道中轴线 checker：对比圆柱结果与 GT 的 center / direction。

    GT 约定：ground_truth.data["pipe_segment"] 为
        [(timestamp, cx, cy, cz, dx, dy, dz), ...]（轴线上一点 + 单位方向）。
    """

    name = "pipe"

    def evaluate(self, records: list, ground_truth: GroundTruth, config: Optional[dict] = None) -> Score:
        cfg = config or {}
        gt_segments = ground_truth.data.get("pipe_segment", [])
        cylinders = [r.data for r in records if isinstance(r.data, CylinderResult) and r.data.valid]
        if not cylinders or not gt_segments:
            return Score(metrics={}, passed=False)

        center_tol = float(cfg.get("center_tolerance", 0.3))
        direction_tol_deg = float(cfg.get("direction_tolerance_deg", 5.0))

        center_errors = []
        direction_errors = []
        for cyl in cylinders:
            segment = self._nearest(cyl.timestamp, gt_segments)
            if segment is None:
                continue
            _, cx, cy, cz, dx, dy, dz = segment
            center_errors.append(math.dist(cyl.center, (cx, cy, cz)))
            direction_errors.append(self._angle_deg(cyl.direction, (dx, dy, dz)))

        if not center_errors:
            return Score(metrics={}, passed=False)

        mean_center = sum(center_errors) / len(center_errors)
        mean_direction = sum(direction_errors) / len(direction_errors)
        passed = mean_center <= center_tol and mean_direction <= direction_tol_deg
        return Score(metrics={"center_error": mean_center, "direction_error": mean_direction}, passed=passed)

    @staticmethod
    def _nearest(ts: float, segments: list[tuple]):
        return min(segments, key=lambda s: abs(s[0] - ts), default=None)

    @staticmethod
    def _angle_deg(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(x * x for x in b))
        if na == 0 or nb == 0:
            return 180.0
        cos = max(-1.0, min(1.0, dot / (na * nb)))
        return math.degrees(math.acos(cos))
