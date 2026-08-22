"""pipe.slam rosbag2 数据源：每个 rosbag2 包为一个 testcase，读 lidar → SlamData。

消息→协议帧的转换复用 convert.py（与 rostopic 数据源同源）；多实例（front/rear）
经 FrameAssembler 按时间戳容差对齐后产帧，保证每帧含所有 lidar 实例；
GT 可选：<gt_dir>/<bag>/gt_traj.tum（TUM 位姿轨迹）或 pipe_segment_gt.csv（中轴线）。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Iterator, Optional

from autotest.protocol.schema import (
    Pose,
    StampedPose,
    encode_ground_truth,
    encode_observation,
    make_observation,
)
from autotest.world import FrameAssembler

from .convert import imu_from_msg, pointcloud_xyz, read_pipe_segment
from .data import SlamData


@dataclass
class RosbagSlamDataset:
    root: str
    topic_map: dict[str, dict[str, str]]  # {类型: {实例名: rosbag 话题}}
    gt_dir: Optional[str] = None
    max_frames: Optional[int] = None  # 每 testcase 最大帧数（冒烟测试用）
    sync_tolerance: float = 0.05  # 多实例时间戳同步容差（秒）

    produces: ClassVar[list[str]] = ["pipe.slam.SlamObs"]

    def __post_init__(self) -> None:
        root = Path(self.root)
        if not root.is_dir():
            raise ValueError(f"bag 目录不存在: {root}")
        self._root = root
        self._gt_dir = Path(self.gt_dir) if self.gt_dir else None
        self.testcases = sorted(
            d.name for d in root.iterdir() if d.is_dir() and (d / "metadata.yaml").is_file()
        )
        if not self.testcases:
            raise ValueError(f"目录 {root} 下没有 rosbag2 包（缺 metadata.yaml）")

    def frames(self, testcase_id: str) -> Iterator[dict]:
        """逐帧产出 observation 外层信封 dict（{timestamp, module, data}）。"""
        from rosbags.highlevel import AnyReader
        from rosbags.typesys import Stores, get_typestore

        lidar_map = self.topic_map.get("lidar", {})  # {实例名: rosbag 话题}
        imu_map = self.topic_map.get("imu", {})      # {实例名: rosbag 话题}
        if not lidar_map:
            raise ValueError("topic_map 缺少 lidar 话题")
        lidar_by_topic = {topic: name for name, topic in lidar_map.items()}
        imu_by_topic = {topic: name for name, topic in imu_map.items()}
        bag_dir = self._root / testcase_id

        assembler = FrameAssembler(tolerance=self.sync_tolerance)
        required = {"lidar": list(lidar_map.keys())}

        reader = AnyReader([bag_dir], default_typestore=get_typestore(Stores.ROS2_HUMBLE))
        reader.open()
        try:
            count = 0
            for conn, _ts, raw in reader.messages():
                if conn.topic in imu_by_topic:
                    msg = reader.deserialize(raw, conn.msgtype)
                    stamp = msg.header.stamp
                    t = stamp.sec + stamp.nanosec * 1e-9
                    assembler.push("imu", imu_by_topic[conn.topic], t, imu_from_msg(msg))
                elif conn.topic in lidar_by_topic:
                    msg = reader.deserialize(raw, conn.msgtype)
                    stamp = msg.header.stamp
                    t = stamp.sec + stamp.nanosec * 1e-9
                    name = lidar_by_topic[conn.topic]
                    assembler.push("lidar", name, t, pointcloud_xyz(msg))
                    frame = assembler.frame("lidar", name, required=required)
                    if frame is None:
                        continue  # 其他 lidar 实例未就绪（窗口外），本帧不产
                    if self.max_frames is not None and count >= self.max_frames:
                        break
                    t0, sensors = frame
                    count += 1
                    yield make_observation("pipe.slam", t0, encode_observation("pipe.slam.SlamObs", SlamData(sensors=sensors)))
        finally:
            reader.close()

    def ground_truth(self, testcase_id: str) -> dict:
        """返回 GT data 信封 dict。"""
        if self._gt_dir is None:
            return encode_ground_truth("pipe.slam.Trajectory", {"trajectory": []})
        # 中轴线 GT：<gt_dir>/<bag>/pipe_segment_gt.csv
        pipe_seg = self._gt_dir / testcase_id / "pipe_segment_gt.csv"
        if pipe_seg.is_file():
            return encode_ground_truth("pipe.slam.PipeSegment", {"pipe_segment": read_pipe_segment(pipe_seg)})
        # 位姿轨迹 GT：<gt_dir>/<bag>/gt_traj.tum
        path = self._gt_dir / testcase_id / "gt_traj.tum"
        if not path.is_file():
            return encode_ground_truth("pipe.slam.Trajectory", {"trajectory": []})
        trajectory = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) != 8:
                continue
            t, tx, ty, tz, qx, qy, qz, qw = (float(p) for p in parts)
            trajectory.append(StampedPose(t, Pose(tx, ty, tz, qx, qy, qz, qw)))
        return encode_ground_truth("pipe.slam.Trajectory", {"trajectory": [t.to_dict() for t in trajectory]})
