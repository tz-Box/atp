"""SLAM 模块：数据、三数据源（rosbag/rostopic/device）、checker。

数据源工厂统一返回 IWorld 实例（算法只见 World，不见来源）。
"""
from autotest.registry import register_checker, register_dataset
from autotest.world import DatasetWorld, DeviceWorld, RostopicWorld

from .checker import PipeChecker, SlamChecker
from autotest.protocol.data.slam import CylinderResult, SlamData  # noqa: F401  触发 data 注册
from .dataset import SyntheticSlamDataset
from .convert import SlamDeviceConverter, SlamRostopicConverter
from .replay import RosbagSlamDataset


def _synthetic_world(**cfg) -> DatasetWorld:
    return DatasetWorld(SyntheticSlamDataset(**cfg))


def _rosbag_world(**cfg) -> DatasetWorld:
    return DatasetWorld(RosbagSlamDataset(**cfg))


def _rostopic_world(**cfg) -> RostopicWorld:
    topic_map = cfg.pop("topic_map")
    return RostopicWorld(converter=SlamRostopicConverter(topic_map), **cfg)


def _device_world(**cfg) -> DeviceWorld:
    """device 数据源工厂：有 topic_map → converter 模式（real-world device 层样本）；
    否则透传模式（设备侧发布协议帧，data_topic）。"""
    topic_map = cfg.pop("topic_map", None)
    if topic_map:
        tolerance = cfg.pop("tolerance", 0.05)
        return DeviceWorld(converter=SlamDeviceConverter(topic_map, tolerance=tolerance), **cfg)
    return DeviceWorld(**cfg)


register_checker("slam", SlamChecker)
register_checker("pipe", PipeChecker)
register_dataset("synthetic_slam", _synthetic_world)
register_dataset("rosbag_slam", _rosbag_world)
register_dataset("rostopic_slam", _rostopic_world)
register_dataset("device_slam", _device_world)

__all__ = [
    "CylinderResult",
    "PipeChecker",
    "RosbagSlamDataset",
    "SlamChecker",
    "SlamData",
    "SyntheticSlamDataset",
]
