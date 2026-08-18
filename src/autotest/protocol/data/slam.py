"""SLAM 协议 data：观测 payload（SlamData）与结果（CylinderResult）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from autotest.protocol.schema import SLAM, Imu, Pose, StampedPose, register_data

_LIDAR = "lidar"
_IMU = "imu"


@dataclass
class SlamData:
    """SLAM 观测 payload。

    sensors 是「类型 → 命名实例 → 数据」的多实例容器，支撑同类型多数据源
    （如 lidar 的 front/rear）。当前已知类型：
      - lidar：实例值为 (N, 3) float32 点云
      - imu  ：实例值为 Imu
    odom 是单值状态（可选），不放 sensors。
    """

    sensors: dict = field(default_factory=dict)  # {类型: {实例名: 数据}}
    odom: Optional[Pose] = None

    def to_dict(self) -> dict:
        data: dict = {"sensors": {}}
        for stype, instances in self.sensors.items():
            data["sensors"][stype] = {
                name: _encode_sensor(stype, value) for name, value in instances.items()
            }
        if self.odom is not None:
            data["odom"] = self.odom.to_list()
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "SlamData":
        sensors: dict = {}
        for stype, instances in data.get("sensors", {}).items():
            sensors[stype] = {
                name: _decode_sensor(stype, value) for name, value in instances.items()
            }
        odom = Pose.from_list(data["odom"]) if "odom" in data else None
        return cls(sensors=sensors, odom=odom)


def _encode_sensor(stype: str, value) -> dict:
    if stype == _LIDAR:
        return {
            "shape": list(value.shape),
            "dtype": str(value.dtype),
            "data": value.tobytes(),
        }
    if stype == _IMU:
        return {
            "angular_velocity": value.angular_velocity,
            "linear_acceleration": value.linear_acceleration,
        }
    if hasattr(value, "to_dict"):
        return value.to_dict()
    return value


def _decode_sensor(stype: str, value):
    if stype == _LIDAR:
        return np.frombuffer(value["data"], dtype=np.dtype(value["dtype"])).reshape(
            value["shape"]
        )
    if stype == _IMU:
        return Imu(
            angular_velocity=value["angular_velocity"],
            linear_acceleration=value["linear_acceleration"],
        )
    return value


@dataclass
class CylinderResult:
    """管道圆柱拟合结果（中轴线）：轴线上一点 + 单位方向。"""

    timestamp: float
    center: tuple[float, float, float]
    direction: tuple[float, float, float]
    valid: bool = True
    straightness_residual: float = 0.0
    radius: float = 0.0

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "center": list(self.center),
            "direction": list(self.direction),
            "valid": self.valid,
            "straightness_residual": self.straightness_residual,
            "radius": self.radius,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CylinderResult":
        return cls(
            timestamp=float(data["timestamp"]),
            center=tuple(data["center"]),
            direction=tuple(data["direction"]),
            valid=bool(data.get("valid", True)),
            straightness_residual=float(data.get("straightness_residual", 0.0)),
            radius=float(data.get("radius", 0.0)),
        )


def _decode_slam_result(data):
    """SLAM 的 result 可能是位姿（StampedPose）或圆柱（CylinderResult），按内容分派。"""
    if isinstance(data, dict) and "center" in data:
        return CylinderResult.from_dict(data)
    return StampedPose.from_dict(data)


register_data("observation", SLAM, SlamData.from_dict)
register_data("result", SLAM, _decode_slam_result)
