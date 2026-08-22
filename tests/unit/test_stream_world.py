"""测试内容：StreamWorld 缓冲语义（reset 首帧 / step 顺序 / max_frames 耗尽 / 空闲超时）。

真实数据源的回调是异步的，因此测试按真实时序模拟：先 reset（阻塞等首帧），
再喂帧（feed 模拟订阅回调入队）。帧为 observation 外层信封 dict。
"""
from __future__ import annotations

import threading
import time

import pytest

from autotest.protocol.schema import make_observation
from autotest.world import StreamWorld


class _FeedWorld(StreamWorld):
    """测试子类：feed 手动入队，_to_observation 原样透传（无外部依赖）。"""

    def _open(self) -> None:
        pass

    def _to_observation(self, raw):
        return raw

    def feed(self, obs: dict) -> None:
        self._push(obs)


def _obs(ts: float) -> dict:
    return make_observation("test.stream", ts, {"schema": "test.Raw", "v": 1, "enc": "none", "blob": b""})


def test_stream_buffer_and_max_frames() -> None:
    world = _FeedWorld(max_frames=3, idle_timeout=1.0)
    assert world.testcases == ["live"]

    holder: dict = {}
    reset_t = threading.Thread(target=lambda: holder.setdefault("first", world.reset("live")))
    reset_t.start()
    time.sleep(0.1)
    world.feed(_obs(0.0))
    world.feed(_obs(1.0))
    world.feed(_obs(2.0))
    reset_t.join(timeout=5)
    assert holder["first"]["timestamp"] == 0.0

    frames = []
    while True:
        observation, done, _ = world.step()
        if done:
            break
        frames.append(observation["timestamp"])
    assert frames == [1.0, 2.0]
    world.close()


def test_stream_idle_timeout_done() -> None:
    world = _FeedWorld(max_frames=None, idle_timeout=0.2)

    holder: dict = {}
    reset_t = threading.Thread(target=lambda: holder.setdefault("first", world.reset("live")))
    reset_t.start()
    time.sleep(0.1)
    world.feed(_obs(0.0))
    reset_t.join(timeout=5)
    assert holder["first"]["timestamp"] == 0.0

    observation, done, info = world.step()
    assert observation is None
    assert done
    assert info["reason"] == "idle_timeout"
    world.close()


def test_stream_reset_timeout_raises() -> None:
    world = _FeedWorld(idle_timeout=0.2)
    with pytest.raises(TimeoutError):
        world.reset("live")
    world.close()
