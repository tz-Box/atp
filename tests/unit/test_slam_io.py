"""测试内容：slam io 转换（pointcloud/imu/pipe_segment）+ SlamRostopicConverter。

无 ROS2 环境时用 fake sensor_msgs 模块构造转换器，验证纯转换逻辑。
"""
from __future__ import annotations

import importlib
import sys
from types import ModuleType

import numpy as np

from autotest.protocol.schema import decode_observation
from autotest.registry import load_plugin

load_plugin("pipe.slam")
convert = importlib.import_module("plugins.pipe.slam.convert")
SlamRostopicConverter = convert.SlamRostopicConverter
imu_from_msg = convert.imu_from_msg
pointcloud_xyz = convert.pointcloud_xyz
read_pipe_segment = convert.read_pipe_segment


def _install_fake_sensor_msgs() -> None:
    msgs = ModuleType("sensor_msgs")
    msg_mod = ModuleType("sensor_msgs.msg")
    msg_mod.PointCloud2 = type("PointCloud2", (), {})
    msg_mod.Imu = type("Imu", (), {})
    msgs.msg = msg_mod
    sys.modules.setdefault("sensor_msgs", msgs)
    sys.modules.setdefault("sensor_msgs.msg", msg_mod)


_install_fake_sensor_msgs()


class _Field:
    def __init__(self, name, datatype, offset) -> None:
        self.name, self.datatype, self.offset = name, datatype, offset


class _V3:
    def __init__(self, x, y, z) -> None:
        self.x, self.y, self.z = x, y, z


class _Stamp:
    def __init__(self, sec, nanosec) -> None:
        self.sec, self.nanosec = sec, nanosec


def _fake_pc(n: int = 4, step: int = 12) -> object:
    msg = type("FakePC", (), {})()
    msg.fields = [_Field("x", 7, 0), _Field("y", 7, 4), _Field("z", 7, 8)]
    msg.data = np.arange(n * 3, dtype=np.float32).tobytes()
    msg.point_step = step
    msg.width = n
    msg.height = 1
    return msg


def _fake_imu() -> object:
    msg = type("FakeImu", (), {})()
    msg.header = type("H", (), {"stamp": _Stamp(0, 500_000_000)})()
    msg.angular_velocity = _V3(0.1, 0.2, 0.3)
    msg.linear_acceleration = _V3(0.0, 0.0, 9.8)
    return msg


def test_pointcloud_xyz() -> None:
    pts = pointcloud_xyz(_fake_pc(n=4))
    assert pts.shape == (4, 3)
    assert pts.dtype == np.float32
    # 第 i 点 = [3i, 3i+1, 3i+2]
    assert pts[2].tolist() == [6.0, 7.0, 8.0]


def test_imu_from_msg() -> None:
    imu = imu_from_msg(_fake_imu())
    assert imu.angular_velocity == [0.1, 0.2, 0.3]
    assert imu.linear_acceleration == [0.0, 0.0, 9.8]


def test_read_pipe_segment(tmp_path) -> None:
    csv = tmp_path / "gt.csv"
    csv.write_text("# t,cx,cy,cz,dx,dy,dz\n1.0,0,0,0,1,0,0\n2.0,1,1,1,0,1,0\n", encoding="utf-8")
    segs = read_pipe_segment(csv)
    assert segs == [(1.0, 0, 0, 0, 1, 0, 0), (2.0, 1, 1, 1, 0, 1, 0)]


def test_converter_requires_lidar() -> None:
    try:
        SlamRostopicConverter(topic_map={"imu": {"imu": "/imu"}})
    except ValueError as exc:
        assert "lidar" in str(exc)
    else:
        raise AssertionError("应因缺少 lidar 抛 ValueError")


def test_converter_imu_cache_and_lidar_frame() -> None:
    conv = SlamRostopicConverter(
        topic_map={"lidar": {"front": "/pc_front"}, "imu": {"imu": "/imu"}}
    )
    assert len(conv.subscriptions) == 2

    # imu 消息不产帧，只更新缓存
    assert conv.convert("/imu", _fake_imu()) is None
    # lidar 帧携带缓存的 imu
    msg = type("FakeLidarMsg", (), {})()
    msg.header = type("H", (), {"stamp": _Stamp(1, 500_000_000)})()
    msg.fields = [_Field("x", 7, 0), _Field("y", 7, 4), _Field("z", 7, 8)]
    msg.data = np.zeros(12, dtype=np.float32).tobytes()
    msg.point_step = 12
    msg.width = 4
    msg.height = 1
    obs = conv.convert("/pc_front", msg)

    assert obs is not None
    assert obs["module"] == "pipe.slam"
    assert obs["timestamp"] == 1.5
    payload = decode_observation(obs["data"])
    assert payload.sensors["lidar"]["front"].shape == (4, 3)
    assert payload.sensors["imu"]["imu"].angular_velocity == [0.1, 0.2, 0.3]

    # 未声明话题不产帧
    assert conv.convert("/unknown", msg) is None
