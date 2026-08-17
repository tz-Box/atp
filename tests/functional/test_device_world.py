"""测试内容：device 数据源端到端（tzcomm 发布 → DeviceWorld 出帧）。

- 透传模式：设备发布协议帧（Observation envelope），DeviceWorld 直接透传。
- converter 模式：设备发布强类型样本 dict（PointCloud2 / Imu），
  SlamDeviceConverter 转成 SlamData 帧（real-world device 层接入路径）。
"""
from __future__ import annotations

import threading
import time
import uuid

import numpy as np
import tzcomm

import modules.slam  # noqa: F401  注册 observation decoder
from autotest.protocol.data.slam import SlamData
from autotest.protocol.schema import SLAM, Observation
from autotest.world import DeviceWorld
from modules.slam.convert import SlamDeviceConverter

_FIELDS = [
    {"name": "x", "offset": 0, "datatype": 7, "count": 1},  # 7 = float32
    {"name": "y", "offset": 4, "datatype": 7, "count": 1},
    {"name": "z", "offset": 8, "datatype": 7, "count": 1},
]


def _frame(i: int) -> Observation:
    return Observation(
        float(i),
        SLAM,
        SlamData(sensors={"lidar": {"lidar": np.zeros((4, 3), np.float32)}}),
    )


def _pc2_dict(sec: float, n: int = 4) -> dict:
    pts = np.arange(n * 3, dtype=np.float32).reshape(n, 3)
    return {
        "header": {"stamp": {"sec": int(sec), "nanosec": 0}, "frame_id": "lidar"},
        "height": 1,
        "width": n,
        "fields": _FIELDS,
        "is_bigendian": False,
        "point_step": 12,
        "row_step": 12 * n,
        "data": pts.tobytes(),
        "is_dense": True,
    }


def _imu_dict(sec: float) -> dict:
    return {
        "header": {"stamp": {"sec": int(sec), "nanosec": 0}, "frame_id": "body"},
        "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
        "angular_velocity": {"x": 0.0, "y": 0.0, "z": 0.3},
        "linear_acceleration": {"x": 0.1, "y": 0.0, "z": 0.0},
    }


def test_device_world_end_to_end(daemon) -> None:
    topic = f"autotest/device/it-{uuid.uuid4().hex[:8]}"
    pub_node = tzcomm.Node("device-pub")
    pub = pub_node.create_publisher(topic, qos=1)
    world = DeviceWorld(data_topic=topic, max_frames=3, idle_timeout=5.0)

    holder: dict = {}

    def _reset() -> None:
        holder["first"] = world.reset("live")

    def _feed() -> None:
        for i in range(3):
            pub.publish(_frame(i).to_dict())
            time.sleep(0.1)

    try:
        reset_t = threading.Thread(target=_reset)
        reset_t.start()
        time.sleep(0.5)  # 等订阅建立（reset 的 _open 创建订阅）
        feed_t = threading.Thread(target=_feed)
        feed_t.start()

        reset_t.join(timeout=10)
        assert not reset_t.is_alive(), "首帧等待超时"
        first = holder["first"]
        assert first.timestamp == 0.0
        assert first.data.sensors["lidar"]["lidar"].shape == (4, 3)

        frames = [first]
        while True:
            observation, done, _ = world.step()
            if done:
                break
            frames.append(observation)
        assert [f.timestamp for f in frames] == [0.0, 1.0, 2.0]
        feed_t.join(timeout=5)
    finally:
        world.close()
        pub_node.close()


def test_device_world_converter_end_to_end(daemon) -> None:
    """real-world device 层路径：发布强类型样本 dict，converter 转 SlamData 帧。"""
    uid = uuid.uuid4().hex[:8]
    front_topic = f"/device/source/lidar_front-{uid}"
    rear_topic = f"/device/source/lidar_rear-{uid}"
    imu_topic = f"/device/source/body_imu-{uid}"
    converter = SlamDeviceConverter(
        topic_map={
            "lidar": {"front": front_topic, "rear": rear_topic},
            "imu": {"imu": imu_topic},
        }
    )
    world = DeviceWorld(converter=converter, max_frames=2, idle_timeout=5.0)

    pub_node = tzcomm.Node("device-pub2")
    pubs = {t: pub_node.create_publisher(t, qos=1) for t in converter.topics}
    holder: dict = {}

    def _reset() -> None:
        holder["first"] = world.reset("live")

    def _feed() -> None:
        for sec in (100, 101):
            pubs[imu_topic].publish(_imu_dict(sec))
            pubs[front_topic].publish(_pc2_dict(sec))
            pubs[rear_topic].publish(_pc2_dict(sec))
            time.sleep(0.1)

    try:
        reset_t = threading.Thread(target=_reset)
        reset_t.start()
        time.sleep(0.5)
        feed_t = threading.Thread(target=_feed)
        feed_t.start()

        reset_t.join(timeout=10)
        assert not reset_t.is_alive(), "首帧等待超时"
        first = holder["first"]
        assert first.timestamp == 100.0
        assert set(first.data.sensors["lidar"]) == {"front", "rear"}
        assert "imu" in first.data.sensors  # unbounded 类型带最近帧

        obs, done, _ = world.step()
        assert not done
        assert obs.timestamp == 101.0
        feed_t.join(timeout=5)
    finally:
        world.close()
        pub_node.close()
