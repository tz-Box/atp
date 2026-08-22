"""pipe.slam 数据源公共转换：ROS 消息 → 协议帧（replay 回放 / rostopic 实时共用）。

replay 与 rostopic 只是"帧的来源"不同，消息 → observation 外层信封的转换逻辑
集中在此，两个数据源复用，避免各自实现散落。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Optional

import numpy as np

from autotest.protocol.schema import Imu, encode_observation, make_observation
from autotest.world import FrameAssembler
from autotest.world.rostopic import RosSubscription

from .data import SlamData

_PC2_DTYPE = {1: "<i1", 2: "<u1", 3: "<i2", 4: "<u2", 5: "<i4", 6: "<u4", 7: "<f4", 8: "<f8"}


def pointcloud_xyz(msg) -> np.ndarray:
    """PointCloud2 → (N, 3) float32 点云（仅 x/y/z，按字段 offset/datatype 保真解析）。"""
    by_name = {f.name: f for f in msg.fields}
    dtype = np.dtype(
        {
            "names": ["x", "y", "z"],
            "formats": [_PC2_DTYPE[by_name[n].datatype] for n in ("x", "y", "z")],
            "offsets": [by_name[n].offset for n in ("x", "y", "z")],
            "itemsize": msg.point_step,
        }
    )
    arr = np.frombuffer(msg.data, dtype=dtype, count=msg.width * msg.height)
    return np.stack([arr["x"], arr["y"], arr["z"]], axis=-1).astype(np.float32)


def imu_from_msg(msg) -> Imu:
    """sensor_msgs/Imu → 框架 Imu（角速度 + 线加速度）。"""
    return Imu(
        angular_velocity=[msg.angular_velocity.x, msg.angular_velocity.y, msg.angular_velocity.z],
        linear_acceleration=[
            msg.linear_acceleration.x, msg.linear_acceleration.y, msg.linear_acceleration.z,
        ],
    )


def _stamp_from_dict(raw: dict) -> float:
    """样本 dict 的时间戳：优先 ts.t_ref_ns（device 层参考钟）；
    退化读 header.stamp（ROS 风格）；再退化用当前时间。"""
    ts = raw.get("ts")
    if isinstance(ts, dict):
        ref = ts.get("t_ref_ns")
        if ref is not None:
            return float(ref) * 1e-9
    stamp = (raw.get("header") or {}).get("stamp")
    if isinstance(stamp, dict) and "sec" in stamp:
        return float(stamp["sec"]) + float(stamp.get("nanosec", 0)) * 1e-9
    return time.time()


def pointcloud_xyz_from_dict(raw: dict) -> np.ndarray:
    """tzcomm 样本 dict（PointCloud2）→ (N, 3) float32 点云。

    与 pointcloud_xyz 同构，输入是 msgpack dict 而非 rclpy 对象
    （/device/source/*/data 上的载荷形态）。
    """
    by_name = {f["name"]: f for f in raw["fields"]}
    dtype = np.dtype(
        {
            "names": ["x", "y", "z"],
            "formats": [_PC2_DTYPE[by_name[n]["datatype"]] for n in ("x", "y", "z")],
            "offsets": [by_name[n]["offset"] for n in ("x", "y", "z")],
            "itemsize": raw["point_step"],
        }
    )
    data = raw["data"]
    if isinstance(data, str):  # 容错：JSON 路径下 bin 会被解成 str
        data = data.encode("latin-1")
    arr = np.frombuffer(data, dtype=dtype, count=raw["width"] * raw["height"])
    return np.stack([arr["x"], arr["y"], arr["z"]], axis=-1).astype(np.float32)


def imu_from_dict(raw: dict) -> Imu:
    """tzcomm 样本 dict（sensor_msgs/Imu）→ 框架 Imu。"""
    av = raw.get("angular_velocity", {})
    la = raw.get("linear_acceleration", {})
    return Imu(
        angular_velocity=[av.get("x", 0.0), av.get("y", 0.0), av.get("z", 0.0)],
        linear_acceleration=[la.get("x", 0.0), la.get("y", 0.0), la.get("z", 0.0)],
    )


def read_pipe_segment(path: Path) -> list[tuple]:
    """pipe_segment_gt.csv（timestamp,cx,cy,cz,dx,dy,dz）→ [(timestamp, cx, cy, cz, dx, dy, dz), ...]"""
    segments = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(",")
        if len(parts) != 7:
            continue
        try:
            values = [float(p) for p in parts]
        except ValueError:
            continue
        segments.append(tuple(values))
    return segments


@dataclass
class SlamRostopicConverter:
    """rostopic 数据源转换器：按 topic_map 声明订阅 lidar/imu，产出 SlamData 帧。

    多实例（front/rear）经 FrameAssembler 按时间戳容差对齐：required 的 lidar
    实例都到齐（窗口内）才产帧，保证帧完整；IMU 作为 unbounded 取最近帧。
    """

    topic_map: dict  # {类型: {实例名: ROS 话题}}
    tolerance: float = 0.05  # 同步容差（秒）
    _lidar_by_topic: dict = field(init=False)
    _imu_by_topic: dict = field(init=False)
    _subscriptions: list[RosSubscription] = field(init=False)
    _assembler: Any = field(init=False)
    _required: dict = field(init=False)

    produces: ClassVar[list[str]] = ["pipe.slam.SlamObs"]

    def __post_init__(self) -> None:
        from sensor_msgs.msg import Imu, PointCloud2

        lidar_map = self.topic_map.get("lidar", {})
        imu_map = self.topic_map.get("imu", {})
        if not lidar_map:
            raise ValueError("topic_map 缺少 lidar 话题")
        self._lidar_by_topic = {topic: name for name, topic in lidar_map.items()}
        self._imu_by_topic = {topic: name for name, topic in imu_map.items()}
        self._subscriptions = [
            RosSubscription(topic, PointCloud2) for topic in self._lidar_by_topic
        ] + [RosSubscription(topic, Imu) for topic in self._imu_by_topic]
        self._assembler = FrameAssembler(tolerance=self.tolerance)
        self._required = {"lidar": list(lidar_map.keys())}

    @property
    def subscriptions(self) -> list[RosSubscription]:
        return list(self._subscriptions)

    def reset(self) -> None:
        self._assembler.clear()

    def convert(self, topic: str, msg) -> Optional[dict]:
        if topic in self._imu_by_topic:
            stamp = msg.header.stamp
            t = stamp.sec + stamp.nanosec * 1e-9
            self._assembler.push("imu", self._imu_by_topic[topic], t, imu_from_msg(msg))
            return None
        if topic in self._lidar_by_topic:
            stamp = msg.header.stamp
            t = stamp.sec + stamp.nanosec * 1e-9
            name = self._lidar_by_topic[topic]
            self._assembler.push("lidar", name, t, pointcloud_xyz(msg))
            frame = self._assembler.frame("lidar", name, required=self._required)
            if frame is None:
                return None  # 其他 lidar 实例未就绪（窗口外），本帧不产
            t0, sensors = frame
            return make_observation("pipe.slam", t0, encode_observation("pipe.slam.SlamObs", SlamData(sensors=sensors)))
        return None


@dataclass
class SlamDeviceConverter:
    """device 数据源转换器：订阅 tzcomm 样本话题（/device/source/*/data）→ SlamData 帧。

    与 SlamRostopicConverter 同构，输入是 tzcomm dict（msgpack）而非 rclpy 对象；
    载荷为强类型样本（PointCloud2 / sensor_msgs/Imu 的 dict 形态），按 header.stamp 取时间戳。
    多实例（front/rear）经 FrameAssembler 对齐，required 的 lidar 都到齐才产帧。
    """

    topic_map: dict  # {类型: {实例名: 样本话题}}
    tolerance: float = 0.05  # 同步容差（秒）
    _lidar_by_topic: dict = field(init=False)
    _imu_by_topic: dict = field(init=False)
    _topics: list = field(init=False)
    _assembler: Any = field(init=False)
    _required: dict = field(init=False)

    produces: ClassVar[list[str]] = ["pipe.slam.SlamObs"]

    def __post_init__(self) -> None:
        lidar_map = self.topic_map.get("lidar", {})
        imu_map = self.topic_map.get("imu", {})
        if not lidar_map:
            raise ValueError("topic_map 缺少 lidar 话题")
        self._lidar_by_topic = {topic: name for name, topic in lidar_map.items()}
        self._imu_by_topic = {topic: name for name, topic in imu_map.items()}
        self._topics = list(self._lidar_by_topic) + list(self._imu_by_topic)
        self._assembler = FrameAssembler(tolerance=self.tolerance)
        self._required = {"lidar": list(lidar_map.keys())}

    @property
    def topics(self) -> list[str]:
        return list(self._topics)

    def reset(self) -> None:
        self._assembler.clear()

    def convert(self, topic: str, raw: dict) -> Optional[dict]:
        if topic in self._imu_by_topic:
            self._assembler.push("imu", self._imu_by_topic[topic], _stamp_from_dict(raw), imu_from_dict(raw))
            return None
        if topic in self._lidar_by_topic:
            name = self._lidar_by_topic[topic]
            self._assembler.push("lidar", name, _stamp_from_dict(raw), pointcloud_xyz_from_dict(raw))
            frame = self._assembler.frame("lidar", name, required=self._required)
            if frame is None:
                return None  # 其他 lidar 实例未就绪（窗口外），本帧不产
            t0, sensors = frame
            return make_observation("pipe.slam", t0, encode_observation("pipe.slam.SlamObs", SlamData(sensors=sensors)))
        return None
