"""测试内容：eval 层——Loader 时钟节奏 + Runner 传感器校验（纯逻辑，无网络）。"""
from __future__ import annotations

import pytest

from autotest.eval import Loader, Runner
from autotest.eval.runner import check_sensors
from autotest.protocol import messages as msg


def _ready(required: dict) -> msg.Message:
    return msg.Message(msg.READY, "s", {"required_sensors": required})


# ---- eval.runner.check_sensors ----
def test_check_sensors_ok() -> None:
    init = {"sensor_config": {"lidar": {"front": "/f", "rear": "/r"}, "imu": {"imu": "/i"}}}
    check_sensors(init, _ready({"lidar": ["front", "rear"], "imu": ["imu"]}))  # 不抛


def test_check_sensors_missing_raises() -> None:
    init = {"sensor_config": {"lidar": {"front": "/f"}}}
    with pytest.raises(RuntimeError, match="rear"):
        check_sensors(init, _ready({"lidar": ["front", "rear"]}))


def test_check_sensors_without_required_ok() -> None:
    check_sensors({}, _ready({}))  # 不抛


# ---- Loader._pace ----
def _obs(ts: float) -> dict:
    """observation 外层信封（_pace 只读 timestamp 字段）。"""
    return {"timestamp": ts, "module": "test", "data": {}}


def test_pace_no_rate_no_sleep(monkeypatch) -> None:
    calls: list = []
    monkeypatch.setattr("autotest.eval.loader.time.sleep", lambda s: calls.append(s))
    Loader._pace(None, _obs(0.0), _obs(0.1))  # 无 clock_rate → 全速不 sleep
    Loader._pace(0, _obs(0.0), _obs(0.1))     # 0 → 全速不 sleep
    Loader._pace(1.0, _obs(0.0), None)        # next_obs None → 不 sleep
    assert calls == []


def test_pace_realtime_sleeps_original_dt(monkeypatch) -> None:
    calls: list = []
    monkeypatch.setattr("autotest.eval.loader.time.sleep", lambda s: calls.append(s))
    Loader._pace(1.0, _obs(1.0), _obs(1.1))  # 1.0=实时：sleep 原始帧间隔
    assert calls == pytest.approx([0.1])


def test_pace_sleeps_by_dt_over_rate(monkeypatch) -> None:
    calls: list = []
    monkeypatch.setattr("autotest.eval.loader.time.sleep", lambda s: calls.append(s))
    Loader._pace(2.0, _obs(1.0), _obs(1.1))  # 2.0=两倍速
    assert calls == pytest.approx([0.05])


def test_pace_non_positive_dt_no_sleep(monkeypatch) -> None:
    calls: list = []
    monkeypatch.setattr("autotest.eval.loader.time.sleep", lambda s: calls.append(s))
    Loader._pace(1.0, _obs(0.5), _obs(0.5))
    assert calls == []


# ---- Message.seq（数据帧序号，排查丢帧用）----
def test_message_seq_roundtrip() -> None:
    m = msg.step("s", None, done=True)
    m.seq = 7
    parsed = msg.Message.from_dict(m.to_dict())
    assert parsed.seq == 7


def test_message_without_seq_defaults_none() -> None:
    parsed = msg.Message.from_dict({"type": "ready", "session_id": "s"})
    assert parsed.seq is None


# ---- 判据层核心（A11 批 0）：passed 派生纪律 ----

def test_score_from_judgements_empty_is_not_passed():
    """★零判据不得判过：all([]) == True 的洞在派生处堵死。

    场景级同款缺陷（声明了却零结果的场景被算成符合预期）2026-09-05 修过；
    判据下沉一层时若按「所有下层都 met」递归，这个洞会原样重开——
    一个根本没跑成的用例显示为符合预期。
    """
    from autotest.eval.checker import Judgement, Score
    assert Score.from_judgements({}, []).passed is False


def test_score_from_judgements_none_state_never_passes():
    from autotest.eval.checker import Judgement, Score
    js = [Judgement("a", "a <= 1", "pass", 0.5), Judgement("b", "b <= 1", "none")]
    assert Score.from_judgements({"a": 0.5}, js).passed is False


def test_score_from_judgements_all_pass():
    from autotest.eval.checker import Judgement, Score
    js = [Judgement("a", "a <= 1", "pass", 0.5)]
    score = Score.from_judgements({"a": 0.5}, js)
    assert score.passed is True and score.judgements == js


def test_score_not_run_enumerates_declared_criteria():
    """判据以声明的清单枚举，不以算出来的结果枚举——且声明进了成绩单，
    产出方可自检、消费方可核对（与场景级 2026-09-05 的结论同一条纪律）。"""
    from autotest.eval.checker import Score
    score = Score.not_run((("a", "a <= 1"), ("b", "b >= 2")))
    assert score.passed is False and score.metrics == {}
    assert [(j.metric, j.rule, j.actual, j.value) for j in score.judgements] == [
        ("a", "a <= 1", "none", None), ("b", "b >= 2", "none", None)]


def test_plain_score_constructor_still_works():
    """存量构造方式（Score(metrics=..., passed=...)）不受判据层影响——批 0 不改对外语义。"""
    from autotest.eval.checker import Score
    s = Score(metrics={"x": 1.0}, passed=True)
    assert s.passed is True and s.judgements == []
