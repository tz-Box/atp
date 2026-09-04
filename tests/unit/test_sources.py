"""测试内容：数据源工厂分派（synthetic/rosbag/rostopic/device 统一返回 IWorld）。"""
from __future__ import annotations

import pytest

from autotest.registry import get_dataset, load_plugin
from autotest.world import DatasetWorld, DeviceWorld, RostopicWorld

load_plugin("pipe.slam")  # 触发数据源注册


def test_synthetic_factory_returns_dataset_world() -> None:
    world = get_dataset("pipe.slam.synthetic")(n_testcases=1, n_steps=3)
    assert isinstance(world, DatasetWorld)
    assert world.testcases == ["tc0"]
    world.close()


def test_device_factory_returns_device_world(daemon) -> None:
    world = get_dataset("pipe.slam.device")(data_topic="autotest/device/test")
    assert isinstance(world, DeviceWorld)
    assert world.testcases == ["live"]
    world.close()


def test_device_factory_converter_mode(daemon) -> None:
    """pipe.slam.device 带 topic_map → converter 模式（订阅 real-world device 层样本话题）。"""
    world = get_dataset("pipe.slam.device")(
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
    world = get_dataset("pipe.slam.rostopic")(
        topic_map={"lidar": {"front": "/pc_front"}, "imu": {"imu": "/imu"}}
    )
    assert isinstance(world, RostopicWorld)
    assert world.testcases == ["live"]
    assert len(world._converter.subscriptions) == 2
    world.close()


# ---- 传感器实例一致性（契约 §8；2026-09-04 由 cicd_test_slam 暴露）----

def test_check_data_sensors_catches_instance_mismatch():
    """★SUT 要 front、body 有 front、但数据实际给的是别的实例名 → 必须拦住。

    此前只有 check_sensors（SUT 要什么 vs **body 声明**什么），而 body 是资产声明、
    不是数据事实。于是这种组合会"握手通过"，SUT 真去读 front 时才 KeyError——
    **声明被拿去和错的东西比对了**。仓内当时 3 个使用非空 body 的场景，3 个都不一致。
    """
    from autotest.eval.runner import check_data_sensors
    from autotest.protocol import messages as msg
    from autotest.registry import load_plugin
    from autotest.world.replay import DatasetWorld

    slam = load_plugin("pipe.slam")
    ready = msg.Message(type=msg.READY, session_id="s", payload={
        "module": "pipe.slam", "required_sensors": {"lidar": ["front"]}})

    # 数据给 lidar/lidar，SUT 要 lidar/front → 拦住，且报文指出"数据实际未提供"
    bad = DatasetWorld(slam.SyntheticSlamDataset(n_testcases=1, n_steps=2,
                                                 lidar_instances=["lidar"]))
    with pytest.raises(RuntimeError, match="数据实际未提供"):
        check_data_sensors(bad, ready)

    # 数据给 lidar/front（缺省，对齐 body/pbox_v1）→ 通过
    good = DatasetWorld(slam.SyntheticSlamDataset(n_testcases=1, n_steps=2))
    assert check_data_sensors(good, ready) == []


def test_synthetic_lidar_instances_default_matches_pbox_body():
    """合成数据源的缺省实例名必须与 body/pbox_v1 对齐，否则默认配置即不一致。"""
    from autotest.body import load_body
    from autotest.protocol.schema import decode_observation
    from autotest.registry import load_plugin

    slam = load_plugin("pipe.slam")
    ds = slam.SyntheticSlamDataset(n_testcases=1, n_steps=2)
    actual = decode_observation(next(iter(ds.frames("tc0")))["data"]).sensors
    declared = load_body("body/pbox_v1.yaml").sensor_topics
    for stype, insts in actual.items():
        assert set(insts) <= set(declared.get(stype, {})), (
            f"{stype} 实例 {sorted(insts)} 不在 body 声明 {sorted(declared.get(stype, {}))} 内")
