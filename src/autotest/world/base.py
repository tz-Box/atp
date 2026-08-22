"""World 抽象：数据集回放 / 模拟器 / 真实设备的统一接口。

v1.1 §6 冻结：
- reset/step 返回 observation 外层信封 dict（{timestamp, module, data}）；
- get_ground_truth() 返回 GT 信封 dict（{schema, v, data}，进程内不编码）；
- 数据源声明 produces（产出的观测 schema 列表，供场景装配校验）。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional


class IWorld(ABC):
    realtime: bool = False
    closed_loop: bool = False  # True = step 消费 action 推进（SimWorld），server 据此选闭环会话
    produces: list[str] = []  # 本数据源产出的观测 schema（命名空间键，供 produces⊇consumes 校验）

    @abstractmethod
    def testcases(self) -> list[str]:
        """返回可用的 testcase_id 列表。"""

    @abstractmethod
    def reset(self, testcase_id: str) -> dict:
        """重置到指定 testcase，返回首个 observation（外层信封 dict）。"""

    @abstractmethod
    def step(self, action: Optional[Any] = None) -> tuple[Optional[dict], bool, dict]:
        """推进一步。返回 (observation, done, info)；action 为闭环指令 payload dict。"""
        return None, True, {}

    @abstractmethod
    def get_ground_truth(self) -> dict:
        """返回 GT 信封 {schema, v, data}（v1.1 §6 冻结）。

        子类实现示例：
          encode_ground_truth("pipe.slam.Trajectory", {"trajectory": [...]})
        调用方（checker）按 decode_ground_truth 取 data 自行解析。
        """

    def close(self) -> None:
        pass
