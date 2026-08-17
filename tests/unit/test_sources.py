"""测试内容：数据源工厂分派（rosbag/rostopic/device 统一返回 IWorld）。"""
from __future__ import annotations

import pytest

from autotest.registry import get_dataset
from autotest.world import DatasetWorld, DeviceWorld, RostopicWorld
import modules.slam  # noqa: F401  触发数据源注册


def test_synthetic_factory_returns_dataset_world() -> None:
    world = get_dataset("synthetic_slam")(n_testcases=1, n_steps=3)
    assert isinstance(world, DatasetWorld)
    assert world.testcases == ["tc0"]
    world.close()


def test_device_factory_returns_device_world(daemon) -> None:
    world = get_dataset("device_slam")(data_topic="autotest/device/test")
    assert isinstance(world, DeviceWorld)
    assert world.testcases == ["live"]
    world.close()


def test_device_factory_converter_mode(daemon) -> None:
    """device_slam 带 topic_map → converter 模式（订阅 real-world device 层样本话题）。"""
    world = get_dataset("device_slam")(
        topic_map={
            "lidar": {"front": "/device/source/lidar_front/data"},
            "imu": {"imu": "/device/source/body_imu/data"},
        }
    )
    assert isinstance(world, DeviceWorld)
    assert world._converter is not None
    assert world._converter.topics == [
        "/device/source/lidar_front/data",
        "/device/source/body_imu/data",
    ]
    world.close()


def test_rostopic_factory_returns_rostopic_world() -> None:
    pytest.importorskip("sensor_msgs")
    world = get_dataset("rostopic_slam")(
        topic_map={"lidar": {"front": "/pc_front"}, "imu": {"imu": "/imu"}}
    )
    assert isinstance(world, RostopicWorld)
    assert world.testcases == ["live"]
    assert len(world._converter.subscriptions) == 2
    world.close()
