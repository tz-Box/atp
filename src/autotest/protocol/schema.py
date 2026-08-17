"""协议数据 schema：基础类型 + 三个通用信封（Observation / Action / Result）。

三个信封均为 module + data，是"算法↔服务"的统一数据容器；
各模块的 data 类型（如 SlamData / NavData）由模块层注册进来（register_data），
信封层不依赖任何具体模块。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable

# 模块标识（协议词汇）
SLAM = "slam"
NAV = "nav"
MANIP = "manip"


@dataclass
class Pose:
    x: float
    y: float
    z: float
    qx: float
    qy: float
    qz: float
    qw: float

    def to_list(self) -> list[float]:
        return [self.x, self.y, self.z, self.qx, self.qy, self.qz, self.qw]

    @classmethod
    def from_list(cls, value) -> "Pose":
        return cls(*[float(i) for i in value])


@dataclass
class Imu:
    angular_velocity: list[float]  # [gx, gy, gz]
    linear_acceleration: list[float]  # [ax, ay, az]


@dataclass
class StampedPose:
    """带时间戳的位姿：位姿估计与真值轨迹的通用载体。"""

    timestamp: float
    pose: Pose

    def to_dict(self) -> dict:
        return {"timestamp": self.timestamp, "pose": self.pose.to_list()}

    @classmethod
    def from_dict(cls, data: dict) -> "StampedPose":
        return cls(timestamp=float(data["timestamp"]), pose=Pose.from_list(data["pose"]))


def quat_to_yaw(qx: float, qy: float, qz: float, qw: float) -> float:
    """从四元数提取 z 轴 yaw（差速底盘绕 z 旋转）。"""
    siny = 2.0 * (qw * qz + qx * qy)
    cosy = 1.0 - 2.0 * (qy * qy + qz * qz)
    return math.atan2(siny, cosy)


def yaw_to_quat(yaw: float) -> tuple[float, float, float, float]:
    """z 轴 yaw 转四元数 (qx, qy, qz, qw)。"""
    half = yaw / 2.0
    return (0.0, 0.0, math.sin(half), math.cos(half))


# ---- 模块 data 注册表：信封反序列化时按 (kind, module) 分派 ----
_DECODERS: dict[str, dict[str, Callable[[dict], Any]]] = {
    "observation": {},
    "action": {},
    "result": {},
}


def register_data(kind: str, module: str, decoder: Callable[[dict], Any]) -> None:
    if kind not in _DECODERS:
        raise ValueError(f"未知 data 种类: {kind!r}（可选 observation/action/result）")
    _DECODERS[kind][module] = decoder


def _decode(kind: str, module: str, data: dict) -> Any:
    decoder = _DECODERS[kind].get(module)
    return decoder(data) if decoder else data


class _Envelope:
    """三个信封的公共序列化逻辑。"""

    @staticmethod
    def _encode(data: Any) -> Any:
        return data.to_dict() if hasattr(data, "to_dict") else data


@dataclass
class Observation(_Envelope):
    timestamp: float
    module: str
    data: Any

    def to_dict(self) -> dict:
        return {"timestamp": self.timestamp, "module": self.module, "data": self._encode(self.data)}

    @classmethod
    def from_dict(cls, data: dict) -> "Observation":
        return cls(
            timestamp=float(data["timestamp"]),
            module=data["module"],
            data=_decode("observation", data["module"], data["data"]),
        )


@dataclass
class Action(_Envelope):
    module: str
    data: Any

    def to_dict(self) -> dict:
        return {"module": self.module, "data": self._encode(self.data)}

    @classmethod
    def from_dict(cls, data: dict) -> "Action":
        return cls(module=data["module"], data=_decode("action", data["module"], data["data"]))


@dataclass
class Result(_Envelope):
    module: str
    data: Any

    def to_dict(self) -> dict:
        return {"module": self.module, "data": self._encode(self.data)}

    @classmethod
    def from_dict(cls, data: dict) -> "Result":
        return cls(module=data["module"], data=_decode("result", data["module"], data["data"]))
