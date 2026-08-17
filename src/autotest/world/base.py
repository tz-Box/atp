"""World 接口：算法只见此层，看不到数据源是谁。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

from ..protocol.schema import Observation, StampedPose


@dataclass
class GroundTruth:
    """真值（单一来源，仅由 World 产出）。data 内容由 checker 约定解析。

    SLAM 约定 data["trajectory"] = list[StampedPose]；
    其他场景（如 pipe-real 中轴线）约定自己的键，checker 自行读取。
    """

    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def trajectory(cls, poses: list[StampedPose]) -> "GroundTruth":
        return cls(data={"trajectory": poses})


class IWorld(ABC):
    # 数据源节奏标记：回放源（rosbag/合成）由 Loader 按原始时间戳 pacing；
    # 实时源（device/rostopic）自带真实节奏，Loader 不再额外 sleep。
    realtime: bool = False

    @property
    @abstractmethod
    def testcases(self) -> list[str]:
        """本数据源的可评测用例列表（实时源通常为 ["live"]）。"""

    @abstractmethod
    def reset(self, testcase_id: str) -> Observation:
        """加载/切换 testcase，返回首帧观测。"""

    @abstractmethod
    def step(self, action=None) -> tuple[Optional[Observation], bool, dict]:
        """推进一步；开环忽略 action。返回 (observation, done, info)。"""

    @abstractmethod
    def get_ground_truth(self) -> GroundTruth:
        """当前 testcase 的真值（仅评测态可用）。"""

    @abstractmethod
    def close(self) -> None:
        """释放资源。"""
