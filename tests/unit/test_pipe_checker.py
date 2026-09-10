"""测试内容：PipeChecker 中轴线 center/direction 误差与通过判定。
期望输出：匹配→通过；center 偏差→不通过；direction 偏差→不通过；invalid/无GT→空 Score。
"""
from autotest.protocol.schema import encode_ground_truth, encode_result
from autotest.registry import load_plugin

slam = load_plugin("pipe.slam")
CylinderResult = slam.CylinderResult
PipeChecker = slam.PipeChecker


def _result(ts, center, direction, valid=True):
    return {
        "module": "pipe.slam",
        "data": encode_result(
            "pipe.slam.CylinderResult", CylinderResult(ts, center, direction, valid=valid)
        ),
    }


def _gt(segments):
    return encode_ground_truth("pipe.slam.PipeSegment", {"pipe_segment": segments})


def test_exact_match_passes():
    gt = _gt([(0.0, 0, 0, 0, 1, 0, 0)])
    records = [_result(0.0, (0, 0, 0), (1, 0, 0))]
    score = PipeChecker().evaluate(records, gt)
    assert score.metrics["center_error"] == 0.0
    assert score.metrics["direction_error"] == 0.0
    assert score.passed


def test_center_offset_fails():
    gt = _gt([(0.0, 0, 0, 0, 1, 0, 0)])
    records = [_result(0.0, (0.5, 0, 0), (1, 0, 0))]
    score = PipeChecker().evaluate(records, gt, {"center_tolerance": 0.3, "direction_tolerance_deg": 5.0})
    assert score.metrics["center_error"] == 0.5
    assert not score.passed


def test_direction_offset_fails():
    gt = _gt([(0.0, 0, 0, 0, 1, 0, 0)])
    records = [_result(0.0, (0, 0, 0), (0, 1, 0))]
    score = PipeChecker().evaluate(records, gt, {"center_tolerance": 0.3, "direction_tolerance_deg": 5.0})
    assert score.metrics["direction_error"] == 90.0
    assert not score.passed


def test_invalid_result_ignored():
    gt = _gt([(0.0, 0, 0, 0, 1, 0, 0)])
    records = [_result(0.0, (0, 0, 0), (1, 0, 0), valid=False)]
    score = PipeChecker().evaluate(records, gt)
    assert score.metrics == {}
    assert not score.passed


def test_no_gt_returns_empty():
    records = [_result(0.0, (0, 0, 0), (1, 0, 0))]
    score = PipeChecker().evaluate(records, encode_ground_truth("pipe.slam.PipeSegment", {}))
    assert score.metrics == {}
    assert not score.passed


# ---- 判据层（A11 批 0）----

def test_judgements_present_on_pass_and_none_when_unassessable():
    checker = PipeChecker()
    gt = _gt([(0.0, 0, 0, 0, 1, 0, 0)])
    score = checker.evaluate([_result(0.0, (0, 0, 0), (1, 0, 0))], gt)
    assert [(j.metric, j.actual) for j in score.judgements] == [
        ("center_error", "pass"), ("direction_error", "pass")]
    # GT 缺失 =「没法评」：判据以声明清单在场（actual=none），不是「判据没过」
    empty = checker.evaluate([_result(0.0, (0, 0, 0), (1, 0, 0))], _gt([]))
    assert empty.passed is False
    assert [(j.metric, j.actual) for j in empty.judgements] == [
        ("center_error", "none"), ("direction_error", "none")]
