"""测试内容：SlamDeviceConverter——tzcomm 样本 dict（/device/source/*/data）→ observation 外层信封。

纯逻辑（无网络）：PointCloud2 / Imu 的 dict 解析 + FrameAssembler 多实例同步。
"""
from __future__ import annotations

import importlib

import numpy as np
import pytest

from autotest.protocol.schema import decode_observation
from autotest.registry import load_plugin

load_plugin("pipe.slam")
convert = importlib.import_module("plugins.pipe.slam.convert")
SlamDeviceConverter = convert.SlamDeviceConverter
imu_from_dict = convert.imu_from_dict
pointcloud_xyz_from_dict = convert.pointcloud_xyz_from_dict

_FIELDS = [
    {"name": "x", "offset": 0, "datatype": 7, "count": 1},  # 7 = float32
    {"name": "y", "offset": 4, "datatype": 7, "count": 1},
    {"name": "z", "offset": 8, "datatype": 7, "count": 1},
]


def _pc2(sec: float = 100.0, n: int = 4) -> dict:
    """模拟 /device/source/*/data 上的 PointCloud2 样本 dict（msgpack 形态）。"""
    pts = np.arange(n * 3, dtype=np.float32).reshape(n, 3)
    return {
        "header": {"stamp": {"sec": int(sec), "nanosec": int((sec - int(sec)) * 1e9)}, "frame_id": "lidar"},
        "height": 1,
        "width": n,
        "fields": _FIELDS,
        "is_bigendian": False,
        "point_step": 12,
        "row_step": 12 * n,
        "data": pts.tobytes(),
        "is_dense": True,
    }


def _imu(sec: float = 100.0) -> dict:
    return {
        "header": {"stamp": {"sec": int(sec), "nanosec": int((sec - int(sec)) * 1e9)}, "frame_id": "body"},
        "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
        "angular_velocity": {"x": 0.0, "y": 0.0, "z": 0.3},
        "linear_acceleration": {"x": 0.1, "y": 0.0, "z": 0.0},
    }


# ---- dict 解析 ----
def test_pointcloud_xyz_from_dict() -> None:
    cloud = pointcloud_xyz_from_dict(_pc2(n=4))
    assert cloud.shape == (4, 3)
    assert cloud.dtype == np.float32
    assert cloud[0, 0] == 0.0 and cloud[3, 2] == 11.0


def test_pointcloud_xyz_from_dict_str_data() -> None:
    raw = _pc2(n=2)
    raw["data"] = raw["data"].decode("latin-1")  # 容错路径：JSON 反序列化后为 str
    cloud = pointcloud_xyz_from_dict(raw)
    assert cloud.shape == (2, 3)


def test_imu_from_dict() -> None:
    imu = imu_from_dict(_imu())
    assert imu.angular_velocity == [0.0, 0.0, 0.3]
    assert imu.linear_acceleration == [0.1, 0.0, 0.0]


# ---- 时间戳：ts.t_ref_ns 优先（device 层参考钟），header.stamp 兜底 ----
def test_stamp_prefers_t_ref_ns() -> None:
    raw = _pc2(sec=100.0)
    raw["ts"] = {"t_ref_ns": 200_000_000_000}  # 200s，参考钟优先
    conv = SlamDeviceConverter(topic_map={"lidar": {"front": "/pc_front"}, "imu": {"imu": "/imu"}})
    obs = conv.convert("/pc_front", raw)
    assert obs is not None
    assert obs["timestamp"] == pytest.approx(200.0)


def test_stamp_falls_back_to_header_stamp() -> None:
    conv = SlamDeviceConverter(topic_map={"lidar": {"front": "/pc_front"}, "imu": {"imu": "/imu"}})
    obs = conv.convert("/pc_front", _pc2(sec=100.5))
    assert obs is not None
    assert obs["timestamp"] == pytest.approx(100.5)


# ---- converter 帧组装 ----
def test_device_converter_produces_frame() -> None:
    conv = SlamDeviceConverter(topic_map={"lidar": {"front": "/pc_front"}, "imu": {"imu": "/imu"}})
    assert conv.convert("/imu", _imu()) is None  # imu 只进 assembler，不产帧
    obs = conv.convert("/pc_front", _pc2())
    assert obs is not None
    assert obs["module"] == "pipe.slam"
    assert obs["timestamp"] == pytest.approx(100.0)
    payload = decode_observation(obs["data"])
    lidar = payload.sensors["lidar"]["front"]
    assert lidar.shape == (4, 3)


def test_device_converter_requires_all_lidar_instances() -> None:
    conv = SlamDeviceConverter(
        topic_map={"lidar": {"front": "/pc_front", "rear": "/pc_rear"}, "imu": {"imu": "/imu"}}
    )
    conv.convert("/imu", _imu())
    assert conv.convert("/pc_front", _pc2()) is None  # rear 未就绪（窗口外）→ 不产帧
    obs = conv.convert("/pc_rear", _pc2())
    assert obs is not None
    payload = decode_observation(obs["data"])
    assert set(payload.sensors["lidar"]) == {"front", "rear"}
    assert "imu" in payload.sensors  # unbounded 类型取最近帧


def test_device_converter_reset_clears_assembler() -> None:
    conv = SlamDeviceConverter(
        topic_map={"lidar": {"front": "/pc_front", "rear": "/pc_rear"}, "imu": {"imu": "/imu"}}
    )
    conv.convert("/pc_front", _pc2(sec=100.0))
    conv.reset()
    assert conv.convert("/pc_rear", _pc2(sec=101.0)) is None  # reset 后 front 帧已清空


def test_device_converter_unknown_topic_ignored() -> None:
    conv = SlamDeviceConverter(topic_map={"lidar": {"front": "/pc_front"}, "imu": {"imu": "/imu"}})
    assert conv.convert("/device/source/localization/data", {"x": 1}) is None


def test_device_converter_missing_lidar_raises() -> None:
    with pytest.raises(ValueError, match="lidar"):
        SlamDeviceConverter(topic_map={"imu": {"imu": "/imu"}})
