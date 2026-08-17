"""薄数据流加载器：World 帧流 → 会话 obs topic 推流 → 收集 RESULT。

等价于 PyTorch DataLoader 的迭代：只负责"取帧 + 传输 + 收结果"，
不涉及协议握手 / testcase 编排 / 打分（那些在 Runner）。
"""
from __future__ import annotations

import threading
import time
from typing import Optional

from ..protocol import messages as msg
from ..protocol import topics
from ..protocol.schema import Result
from ..world.base import IWorld

_RESULT_TIMEOUT = 30.0


class Loader:
    """把 IWorld 帧流经会话 obs topic 推给 SUT，异步收集 result。

    一次 load() 跑完一个 testcase 的全部数据流：推完全部帧并等待收齐结果。
    """

    def __init__(self, node, world: IWorld, session_id: str) -> None:
        self._world = world
        self._session_id = session_id
        self._obs_pub = node.create_publisher(topics.obs_topic(session_id), qos=1)
        self._result_sub = node.create_subscription(topics.result_topic(session_id), self._on_result)
        self._results: list[Result] = []
        self._results_lock = threading.Lock()
        self._seq = 0  # 数据帧序号：排查丢帧时按 0..n-1 对账

    def _on_result(self, data: dict) -> None:
        result = msg.parse_result(msg.Message.from_dict(data))
        with self._results_lock:
            self._results.append(result)

    def load(self, testcase_id: str, clock_rate: Optional[float] = 1.0) -> list[Result]:
        """推完一个 testcase 的数据流并收齐结果，返回 records。

        clock_rate：1.0 = 实时复现（每帧按原始时间戳间隔 sleep，保留帧率波动/突发）；
        >1 加速，<1 减速，0/None = 全速（不 sleep，降级选项）。
        实时数据源（world.realtime=True，如 device/rostopic）自带节奏，不额外 sleep。
        """
        with self._results_lock:
            self._results = []
        self._seq = 0

        observation = self._world.reset(testcase_id)
        n_sent = 0
        while True:
            self._publish_step(observation, done=False)
            n_sent += 1
            next_obs, done, _ = self._world.step()
            if done:
                self._publish_step(None, done=True)
                break
            if not self._world.realtime:
                self._pace(clock_rate, observation, next_obs)
            observation = next_obs

        deadline = time.monotonic() + _RESULT_TIMEOUT
        while time.monotonic() < deadline:
            with self._results_lock:
                if len(self._results) >= n_sent:
                    break
            time.sleep(0.01)
        with self._results_lock:
            return list(self._results)

    def _publish_step(self, observation, done: bool) -> None:
        message = msg.step(self._session_id, observation, done)
        message.seq = self._seq
        self._seq += 1
        self._obs_pub.publish(message.to_dict())

    @staticmethod
    def _pace(clock_rate: Optional[float], current, next_obs) -> None:
        """按原始帧间隔等待：sleep((next.timestamp - current.timestamp) / clock_rate)。

        0/None 全速；1.0 实时复现原始帧率（含波动）；>1 加速；<1 减速。
        """
        if not clock_rate or next_obs is None:
            return
        dt = next_obs.timestamp - current.timestamp
        if dt > 0:
            time.sleep(dt / clock_rate)
