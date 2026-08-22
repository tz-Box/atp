"""pipe.slam 合成数据集：固定圆形轨迹 + 点云/IMU/里程计观测 + GT 轨迹。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Iterator

import numpy as np

from autotest.protocol.schema import (
    Pose,
    StampedPose,
    encode_ground_truth,
    encode_observation,
    make_observation,
)

from .data import SlamData


def _yaw_to_quat(yaw: np.ndarray) -> np.ndarray:
    half = yaw / 2.0
    return np.stack(
        [np.zeros_like(half), np.zeros_like(half), np.sin(half), np.cos(half)], axis=-1
    )


@dataclass
class SyntheticSlamDataset:
    n_testcases: int = 2
    n_steps: int = 50
    dt: float = 0.1
    radius: float = 2.0
    seed: int = 0

    produces: ClassVar[list[str]] = ["pipe.slam.SlamObs"]

    def __post_init__(self) -> None:
        self.testcases = [f"tc{i}" for i in range(self.n_testcases)]

    def _trajectory(self, offset: float) -> list[tuple[float, float, float, float, np.ndarray]]:
        yaw = np.arange(self.n_steps) * self.dt * 0.3 + offset
        x = self.radius * np.cos(yaw)
        y = self.radius * np.sin(yaw)
        z = np.zeros(self.n_steps)
        q = _yaw_to_quat(yaw)
        ts = np.arange(self.n_steps) * self.dt
        return [
            (float(ts[i]), float(x[i]), float(y[i]), float(z[i]), q[i]) for i in range(self.n_steps)
        ]

    def frames(self, testcase_id: str) -> Iterator[dict]:
        """逐帧产出 observation 外层信封 dict（{timestamp, module, data}）。"""
        index = int(testcase_id.replace("tc", ""))
        rng = np.random.default_rng(self.seed + index)
        for ts, x, y, z, q in self._trajectory(offset=index * 0.5):
            lidar = rng.normal(0.0, 0.5, size=(32, 3)).astype(np.float32)
            from autotest.protocol.schema import Imu
            imu = Imu(angular_velocity=[0.0, 0.0, 0.3], linear_acceleration=[0.0, 0.0, 0.0])
            odom = Pose(x=x, y=y, z=z, qx=float(q[0]), qy=float(q[1]), qz=float(q[2]), qw=float(q[3]))
            slam_data = SlamData(
                sensors={"lidar": {"lidar": lidar}, "imu": {"imu": imu}},
                odom=odom,
            )
            yield make_observation("pipe.slam", ts, encode_observation("pipe.slam.SlamObs", slam_data))

    def ground_truth(self, testcase_id: str) -> dict:
        """返回 GT data 信封 dict（{schema, v, data}）。"""
        index = int(testcase_id.replace("tc", ""))
        trajectory = []
        for ts, x, y, z, q in self._trajectory(offset=index * 0.5):
            trajectory.append(
                StampedPose(
                    timestamp=ts,
                    pose=Pose(x=x, y=y, z=z, qx=float(q[0]), qy=float(q[1]), qz=float(q[2]), qw=float(q[3])),
                )
            )
        return encode_ground_truth("pipe.slam.Trajectory", {"trajectory": [t.to_dict() for t in trajectory]})
