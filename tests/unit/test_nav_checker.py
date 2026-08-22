"""测试内容：NavChecker 到点 / 安全裕度 / 路径成功率。
期望输出：到点且安全→通过；未到点→不通过；贴障碍（安全裕度不足）→不通过。
"""
import math

from autotest.protocol.schema import (
    Pose,
    encode_ground_truth,
    encode_observation,
    make_observation,
)
from autotest.registry import load_plugin

nav2d = load_plugin("nav2d")
NavChecker = nav2d.NavChecker
NavData = nav2d.NavData


def _pose(x, y):
    return Pose(x, y, 0.0, 0.0, 0.0, 0.0, 1.0)


def _records(points):
    """构造闭环 records：observation 外层信封（{timestamp, module, data}）列表。"""
    return [
        make_observation(
            "nav2d",
            float(i),
            encode_observation("nav2d.NavObs", NavData(robot_pose=_pose(x, y), goal=_pose(5.0, 0.0))),
        )
        for i, (x, y) in enumerate(points)
    ]


def _gt(goal, obstacles):
    return encode_ground_truth("nav2d.NavGoal", {"goal": goal, "obstacles": obstacles})


def test_arrived_and_safe_passes():
    records = _records([(0.0, 0.0), (2.5, 0.0), (5.0, 0.0)])
    gt = _gt((5.0, 0.0), [(3.0, 1.5, 0.5)])
    score = NavChecker().evaluate(records, gt, {"arrival_tolerance": 0.2, "safety_margin": 0.3})
    assert score.metrics["arrived"] == 1.0
    assert score.metrics["safety_margin"] >= 0.3
    assert score.passed


def test_not_arrived_fails():
    records = _records([(0.0, 0.0), (1.0, 0.0)])
    gt = _gt((5.0, 0.0), [])
    score = NavChecker().evaluate(records, gt, {"arrival_tolerance": 0.2, "safety_margin": 0.3})
    assert score.metrics["arrived"] == 0.0
    assert not score.passed


def test_collision_fails_even_if_arrived():
    records = _records([(0.0, 0.0), (3.0, 1.6), (5.0, 0.0)])
    gt = _gt((5.0, 0.0), [(3.0, 1.5, 0.5)])
    score = NavChecker().evaluate(records, gt, {"arrival_tolerance": 0.2, "safety_margin": 0.3})
    assert score.metrics["arrived"] == 1.0
    assert score.metrics["safety_margin"] < 0.3
    assert not score.passed


def test_no_obstacle_safety_infinite():
    records = _records([(0.0, 0.0), (5.0, 0.0)])
    gt = _gt((5.0, 0.0), [])
    score = NavChecker().evaluate(records, gt)
    assert math.isinf(score.metrics["safety_margin"])
    assert score.passed
