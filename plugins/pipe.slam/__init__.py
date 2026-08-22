"""pipe.slam 插件：管道 SLAM 评测（观测/结果 schema + 数据源 + checker + 场景）。

命名空间：pipe.slam
produces: [pipe.slam.SlamObs, pipe.slam.SlamResult, pipe.slam.Trajectory]
consumes: [pipe.slam.SlamObs]
"""
from autotest.registry import register_checker, register_dataset

from .data import CylinderResult, SlamData, register_schemas
from .checker import PipeChecker, SlamChecker
from .dataset import SyntheticSlamDataset
from .convert import SlamDeviceConverter, SlamRostopicConverter
from .replay import RosbagSlamDataset

register_schemas()

register_checker("pipe.slam.ape", SlamChecker)
register_checker("pipe.slam.pipe", PipeChecker)
register_dataset("pipe.slam.synthetic", lambda **cfg: __import__("autotest.world.replay", fromlist=["DatasetWorld"]).DatasetWorld(SyntheticSlamDataset(**cfg)))
register_dataset("pipe.slam.rosbag", lambda **cfg: __import__("autotest.world.replay", fromlist=["DatasetWorld"]).DatasetWorld(RosbagSlamDataset(**cfg)))
register_dataset("pipe.slam.rostopic", lambda **cfg: __import__("autotest.world.rostopic", fromlist=["RostopicWorld"]).RostopicWorld(converter=SlamRostopicConverter(cfg.pop("topic_map")), **cfg))
register_dataset("pipe.slam.device", lambda **cfg: __import__("autotest.world.device", fromlist=["DeviceWorld"]).DeviceWorld(converter=SlamDeviceConverter(cfg.pop("topic_map"), tolerance=cfg.pop("tolerance", 0.05)), **cfg) if "topic_map" in cfg else __import__("autotest.world.device", fromlist=["DeviceWorld"]).DeviceWorld(**cfg))

__all__ = [
    "CylinderResult",
    "PipeChecker",
    "RosbagSlamDataset",
    "SlamChecker",
    "SlamData",
    "SyntheticSlamDataset",
]
