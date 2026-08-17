"""测试内容：eval 层——Loader 时钟节奏 + Runner 传感器校验（纯逻辑，无网络）。"""
from __future__ import annotations

import pytest

from autotest.eval import Loader, Runner
from autotest.protocol import messages as msg


def _ready(required: dict) -> msg.Message:
    return msg.Message(msg.READY, "s", {"required_sensors": required})


# ---- Runner._check_sensors ----
def test_check_sensors_ok() -> None:
    init = {"sensor_config": {"lidar": {"front": "/f", "rear": "/r"}, "imu": {"imu": "/i"}}}
    Runner._check_sensors(init, _ready({"lidar": ["front", "rear"], "imu": ["imu"]}))  # 不抛


def test_check_sensors_missing_raises() -> None:
    init = {"sensor_config": {"lidar": {"front": "/f"}}}
    with pytest.raises(RuntimeError, match="rear"):
        Runner._check_sensors(init, _ready({"lidar": ["front", "rear"]}))


def test_check_sensors_without_required_ok() -> None:
    Runner._check_sensors({}, _ready({}))  # 不抛


# ---- Loader._pace ----
class _Obs:
    def __init__(self, ts: float) -> None:
        self.timestamp = ts


def test_pace_no_rate_no_sleep(monkeypatch) -> None:
    calls: list = []
    monkeypatch.setattr("autotest.eval.loader.time.sleep", lambda s: calls.append(s))
    Loader._pace(None, _Obs(0.0), _Obs(0.1))  # 无 clock_rate → 全速不 sleep
    Loader._pace(0, _Obs(0.0), _Obs(0.1))     # 0 → 全速不 sleep
    Loader._pace(1.0, _Obs(0.0), None)        # next_obs None → 不 sleep
    assert calls == []


def test_pace_realtime_sleeps_original_dt(monkeypatch) -> None:
    calls: list = []
    monkeypatch.setattr("autotest.eval.loader.time.sleep", lambda s: calls.append(s))
    Loader._pace(1.0, _Obs(1.0), _Obs(1.1))  # 1.0=实时：sleep 原始帧间隔
    assert calls == pytest.approx([0.1])


def test_pace_sleeps_by_dt_over_rate(monkeypatch) -> None:
    calls: list = []
    monkeypatch.setattr("autotest.eval.loader.time.sleep", lambda s: calls.append(s))
    Loader._pace(2.0, _Obs(1.0), _Obs(1.1))  # 2.0=两倍速
    assert calls == pytest.approx([0.05])


def test_pace_non_positive_dt_no_sleep(monkeypatch) -> None:
    calls: list = []
    monkeypatch.setattr("autotest.eval.loader.time.sleep", lambda s: calls.append(s))
    Loader._pace(1.0, _Obs(0.5), _Obs(0.5))
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
