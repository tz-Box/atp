"""测试内容：PipeChecker 中轴线 center/direction 误差与通过判定。
期望输出：匹配→通过；center 偏差→不通过；direction 偏差→不通过；invalid/无GT→空 Score。
"""
from autotest.protocol.schema import SLAM, Result
from autotest.world.base import GroundTruth
from modules.slam import CylinderResult, PipeChecker


def _result(ts, center, direction, valid=True):
    return Result(SLAM, CylinderResult(ts, center, direction, valid=valid))


def test_exact_match_passes():
    gt = GroundTruth(data={"pipe_segment": [(0.0, 0, 0, 0, 1, 0, 0)]})
    records = [_result(0.0, (0, 0, 0), (1, 0, 0))]
    score = PipeChecker().evaluate(records, gt)
    assert score.metrics["center_error"] == 0.0
    assert score.metrics["direction_error"] == 0.0
    assert score.passed


def test_center_offset_fails():
    gt = GroundTruth(data={"pipe_segment": [(0.0, 0, 0, 0, 1, 0, 0)]})
    records = [_result(0.0, (0.5, 0, 0), (1, 0, 0))]
    score = PipeChecker().evaluate(records, gt, {"center_tolerance": 0.3, "direction_tolerance_deg": 5.0})
    assert score.metrics["center_error"] == 0.5
    assert not score.passed


def test_direction_offset_fails():
    gt = GroundTruth(data={"pipe_segment": [(0.0, 0, 0, 0, 1, 0, 0)]})
    records = [_result(0.0, (0, 0, 0), (0, 1, 0))]
    score = PipeChecker().evaluate(records, gt, {"center_tolerance": 0.3, "direction_tolerance_deg": 5.0})
    assert score.metrics["direction_error"] == 90.0
    assert not score.passed


def test_invalid_result_ignored():
    gt = GroundTruth(data={"pipe_segment": [(0.0, 0, 0, 0, 1, 0, 0)]})
    records = [_result(0.0, (0, 0, 0), (1, 0, 0), valid=False)]
    score = PipeChecker().evaluate(records, gt)
    assert score.metrics == {}
    assert not score.passed


def test_no_gt_returns_empty():
    records = [_result(0.0, (0, 0, 0), (1, 0, 0))]
    score = PipeChecker().evaluate(records, GroundTruth())
    assert score.metrics == {}
    assert not score.passed
