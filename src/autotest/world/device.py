"""device 数据源：订阅 tzcomm 设备数据话题，转协议帧（Observation）。

两种模式（由是否有 converter 区分）：
- 无 converter（透传，原行为）：设备侧产出协议帧（Observation envelope），
  本类只订阅与透传，不做格式转换——`data_topic` 指设备发布的协议帧话题。
- 有 converter（real-world device 层接入）：直接订阅设备强类型样本话题
  （如 /device/source/lidar_front/data），由模块 converter 把 tzcomm dict
  样本转成 Observation；话题名可经 tzcomm remap 配置化（代码名 ↔ 物理名）。

无论哪种模式，数据帧经会话 obs 话题再推给算法，对算法与 rosbag/rostopic 完全同接口。
"""
from __future__ import annotations

from typing import Optional

import tzcomm

from ..protocol.schema import Observation
from .stream import StreamWorld


class DeviceWorld(StreamWorld):
    def __init__(
        self,
        data_topic: str = "",
        converter=None,
        max_frames: Optional[int] = None,
        idle_timeout: float = 5.0,
        node_name: str = "autotest-device",
    ) -> None:
        super().__init__(max_frames=max_frames, idle_timeout=idle_timeout)
        self._data_topic = data_topic
        self._converter = converter
        self._node = tzcomm.Node(node_name)

    def _open(self) -> None:
        if self._converter is not None:
            for topic in self._converter.topics:
                self._node.create_subscription(
                    topic, lambda raw, t=topic: self._push((t, raw)), qos=1
                )
        else:
            self._node.create_subscription(self._data_topic, self._push, qos=1)

    def _to_observation(self, raw) -> Optional[Observation]:
        if self._converter is not None:
            topic, msg = raw
            return self._converter.convert(topic, msg)
        return Observation.from_dict(raw)

    def reset(self, testcase_id: str) -> Observation:
        if self._converter is not None:
            self._converter.reset()
        return super().reset(testcase_id)

    def close(self) -> None:
        super().close()
        self._node.close()
