"""实时流数据源基类：订阅 → 缓冲 → 出帧（device / rostopic 共用）。

子类只需实现 _open（建立订阅）与 _to_observation（消息 → 外层信封），
reset/step 的帧缓冲、空闲超时、max_frames 耗尽等语义由本类统一处理。
"""
from __future__ import annotations

import queue
from typing import Any, Optional

from ..protocol.schema import encode_ground_truth
from .base import IWorld


class StreamWorld(IWorld):
    """订阅型数据源：回调经 _push 入队，reset/step 从队列取帧。

    - reset 阻塞等首帧（idle_timeout 内无帧报错）；
    - step 取下一帧，max_frames 达上限或 idle_timeout 无帧 → done=True。
    """

    realtime: bool = True  # 订阅型数据源自带真实到达节奏，Loader 不做 pacing

    def __init__(
        self,
        testcases: tuple[str, ...] = ("live",),
        max_frames: Optional[int] = None,
        idle_timeout: float = 5.0,
    ) -> None:
        self._testcases = list(testcases)
        self._max_frames = max_frames
        self._idle_timeout = idle_timeout
        self._queue: "queue.Queue[dict]" = queue.Queue()
        self._opened = False
        self._sent = 0

    @property
    def testcases(self) -> list[str]:
        return list(self._testcases)

    # ---- 子类钩子 ----
    def _open(self) -> None:
        """建立订阅（reset 首次调用时执行一次）。"""

    def _to_observation(self, raw: Any) -> Optional[dict]:
        """消息 → observation 外层信封 dict；返回 None 表示本消息不产帧（如 IMU 状态更新）。"""
        raise NotImplementedError

    def _push(self, raw: Any) -> None:
        obs = self._to_observation(raw)
        if obs is not None:
            self._queue.put(obs)

    # ---- IWorld ----
    def reset(self, testcase_id: str) -> dict:
        if not self._opened:
            self._open()
            self._opened = True
        self._drain()
        self._sent = 0
        try:
            return self._queue.get(timeout=self._idle_timeout)
        except queue.Empty as exc:
            raise TimeoutError(f"数据源无数据（{self._idle_timeout}s 内无帧）") from exc

    def step(self, action=None) -> tuple[Optional[dict], bool, dict]:
        if self._max_frames is not None and self._sent >= self._max_frames:
            return None, True, {}
        try:
            obs = self._queue.get(timeout=self._idle_timeout)
            self._sent += 1
            return obs, False, {}
        except queue.Empty:
            return None, True, {"reason": "idle_timeout"}

    def get_ground_truth(self) -> dict:
        """实时源默认无 GT（空 data）；有 GT 的实时场景由插件 world 覆写。"""
        return encode_ground_truth("", {})

    def close(self) -> None:
        self._drain()

    def _drain(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return
