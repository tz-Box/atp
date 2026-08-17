"""rostopic 数据源：实时订阅 ROS 话题，经模块转换器产协议帧。

转换器由模块提供（如 modules/slam/io.py），负责声明订阅清单并做消息转换；
数据帧经会话 obs 话题推给算法，与 rosbag / device 完全同接口。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Optional

from ..protocol.schema import Observation
from .stream import StreamWorld


@dataclass
class RosSubscription:
    """RostopicWorld 的订阅清单：ROS 话题 + 消息类型（由转换器声明）。"""

    topic: str
    msg_type: Any


class RostopicWorld(StreamWorld):
    def __init__(
        self,
        converter,
        max_frames: Optional[int] = None,
        idle_timeout: float = 5.0,
        node_name: str = "autotest-rostopic",
    ) -> None:
        super().__init__(max_frames=max_frames, idle_timeout=idle_timeout)
        self._converter = converter
        self._node_name = node_name
        self._rclpy_node = None
        self._executor = None
        self._spin_thread: Optional[threading.Thread] = None

    def _open(self) -> None:
        import rclpy

        rclpy.init()
        self._rclpy_node = rclpy.create_node(self._node_name)
        for sub in self._converter.subscriptions:
            self._rclpy_node.create_subscription(
                sub.msg_type,
                sub.topic,
                lambda msg, topic=sub.topic: self._push((topic, msg)),
                qos_profile=1,
            )
        self._executor = rclpy.executors.SingleThreadedExecutor()
        self._executor.add_node(self._rclpy_node)
        self._spin_thread = threading.Thread(target=self._executor.spin, daemon=True)
        self._spin_thread.start()

    def _to_observation(self, raw: tuple[str, Any]) -> Optional[Observation]:
        topic, msg = raw
        return self._converter.convert(topic, msg)

    def reset(self, testcase_id: str) -> Observation:
        self._converter.reset()
        return super().reset(testcase_id)

    def close(self) -> None:
        super().close()
        if self._rclpy_node is not None:
            self._rclpy_node.destroy_node()
        try:
            import rclpy

            rclpy.shutdown()
        except ImportError:
            pass
