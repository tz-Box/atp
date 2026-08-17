"""测试内容：SlamChecker 的 ATE/RPE 对齐与通过判定。
期望输出：恒等/平移/旋转/缩放对齐后误差≈0；带噪声误差大于阈值时判定不通过。
"""
import numpy as np

from autotest.protocol.schema import SLAM, Pose, Result, StampedPose
from autotest.world.base import GroundTruth
from modules.slam import SlamChecker


def _results(points):
    return [
        Result(SLAM, StampedPose(float(i), pose)) for i, pose in enumerate(points)
    ]


def _line(xs):
    return [Pose(x, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0) for x in xs]


def test_identity_error_zero():
    checker = SlamChecker()
    gt = GroundTruth.trajectory([StampedPose(float(i), p) for i, p in enumerate(_line(range(10)))])
    score = checker.evaluate(_results(_line(range(10))), gt)
    assert score.metrics["ate_rmse"] < 1e-6
    assert score.metrics["rpe_rmse"] < 1e-6
    assert score.passed


def test_translation_error_zero():
    checker = SlamChecker()
    gt = GroundTruth.trajectory([StampedPose(float(i), p) for i, p in enumerate(_line(range(10)))])
    est = _results([Pose(x + 5.0, 3.0, -2.0, 0.0, 0.0, 0.0, 1.0) for x in range(10)])
    score = checker.evaluate(est, gt)
    assert score.metrics["ate_rmse"] < 1e-6
    assert score.metrics["rpe_rmse"] < 1e-6


def test_rotation_error_zero():
    checker = SlamChecker()
    gt = GroundTruth.trajectory([StampedPose(float(i), p) for i, p in enumerate(_line(range(10)))])
    est = _results([Pose(0.0, float(x), 0.0, 0.0, 0.0, 0.0, 1.0) for x in range(10)])
    score = checker.evaluate(est, gt)
    assert score.metrics["ate_rmse"] < 1e-6


def test_scale_error_zero():
    checker = SlamChecker()
    gt = GroundTruth.trajectory([StampedPose(float(i), p) for i, p in enumerate(_line(range(10)))])
    est = _results([Pose(x * 2.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0) for x in range(10)])
    score = checker.evaluate(est, gt)
    assert score.metrics["ate_rmse"] < 1e-6


def test_noisy_estimate_fails_threshold():
    checker = SlamChecker()
    gt = GroundTruth.trajectory([StampedPose(float(i), p) for i, p in enumerate(_line(range(10)))])
    noise = np.random.default_rng(0).normal(0.0, 0.5, size=10)
    est = _results([Pose(x + noise[i], 0.0, 0.0, 0.0, 0.0, 0.0, 1.0) for i, x in enumerate(range(10))])
    score = checker.evaluate(est, gt, {"ate_threshold": 0.1, "rpe_threshold": 0.1})
    assert score.metrics["ate_rmse"] > 0.1
    assert not score.passed
