"""Dataset 协议与 DatasetWorld：开环回放（rosbag / 合成数据等离线源）。"""
from __future__ import annotations

from typing import Iterator, Optional, Protocol

from ..protocol.schema import Observation
from .base import GroundTruth, IWorld


class Dataset(Protocol):
    testcases: list[str]

    def frames(self, testcase_id: str) -> Iterator[Observation]: ...

    def ground_truth(self, testcase_id: str) -> GroundTruth: ...


class DatasetWorld(IWorld):
    """开环回放：step 忽略 action，按序返回下一帧；done=数据耗尽。"""

    def __init__(self, dataset: Dataset) -> None:
        self._dataset = dataset
        self._testcase_id: Optional[str] = None
        self._frames: Optional[Iterator[Observation]] = None

    @property
    def testcases(self) -> list[str]:
        return list(self._dataset.testcases)

    def reset(self, testcase_id: str) -> Observation:
        self._testcase_id = testcase_id
        self._frames = iter(self._dataset.frames(testcase_id))
        return next(self._frames)

    def step(self, action=None) -> tuple[Optional[Observation], bool, dict]:
        if self._frames is None:
            raise RuntimeError("DatasetWorld.step() 前必须先 reset()")
        try:
            return next(self._frames), False, {}
        except StopIteration:
            return None, True, {}

    def get_ground_truth(self) -> GroundTruth:
        if self._testcase_id is None:
            raise RuntimeError("DatasetWorld.get_ground_truth() 前必须先 reset()")
        return self._dataset.ground_truth(self._testcase_id)

    def close(self) -> None:
        self._frames = None
