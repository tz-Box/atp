"""pipe.slam 合成数据集：固定圆形轨迹 + 点云/IMU/里程计观测 + GT 轨迹。"""
from __future__ import annotations

from dataclasses import dataclass, field
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
    # 激光实例名。**必须与所引用 body 的实例名对齐**——否则算法声明 required_sensors
    # 时会拿到"body 里有、数据里没有"的虚假通过（2026-09-04 由 cicd_test_slam 暴露，
    # 见 eval/runner.py check_data_sensors）。缺省 ["front"] 对齐 body/pbox_v1.yaml。
    # 配多个即产多实例，可用于验证 FrameAssembler 的多实例时间戳对齐。
    lidar_instances: list = field(default_factory=lambda: ["front"])

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
                sensors={
                    # 多实例时各实例给不同噪声实现，便于区分（否则对齐 bug 看不出来）
                    "lidar": {name: (lidar if i == 0 else
                                     rng.normal(0.0, 0.5, size=lidar.shape).astype(np.float32))
                              for i, name in enumerate(self.lidar_instances)},
                    "imu": {"imu": imu},
                },
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
