"""Dataset 协议与 DatasetWorld：开环回放（rosbag / 合成数据等离线源）。"""
from __future__ import annotations

from typing import Iterator, Optional, Protocol

from .base import IWorld


class Dataset(Protocol):
    testcases: list[str]
    produces: list[str]  # 产出的观测 schema（命名空间键）

    def frames(self, testcase_id: str) -> Iterator[dict]:
        """逐帧产出 observation 外层信封 dict（{timestamp, module, data}）。"""
        ...

    def ground_truth(self, testcase_id: str) -> dict:
        """返回 GT 信封 dict（{schema, v, data}）。"""
        ...


class DatasetWorld(IWorld):
    """开环回放：step 忽略 action，按序返回下一帧；done=数据耗尽。"""

    def __init__(self, dataset: Dataset) -> None:
        self._dataset = dataset
        self._testcase_id: Optional[str] = None
        self._frames: Optional[Iterator[dict]] = None

    @property
    def produces(self) -> list[str]:
        return list(getattr(self._dataset, "produces", []))

    @property
    def testcases(self) -> list[str]:
        return list(self._dataset.testcases)

    def reset(self, testcase_id: str) -> dict:
        self._testcase_id = testcase_id
        self._frames = iter(self._dataset.frames(testcase_id))
        return next(self._frames)

    def step(self, action=None) -> tuple[Optional[dict], bool, dict]:
        if self._frames is None:
            raise RuntimeError("DatasetWorld.step() 前必须先 reset()")
        try:
            return next(self._frames), False, {}
        except StopIteration:
            return None, True, {}

    def get_ground_truth(self) -> dict:
        if self._testcase_id is None:
            raise RuntimeError("DatasetWorld.get_ground_truth() 前必须先 reset()")
        return self._dataset.ground_truth(self._testcase_id)

    def close(self) -> None:
        self._frames = None
