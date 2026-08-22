"""算法侧适配（bridge）参考实现：仅依赖 tzcomm + 通信协议，不依赖框架代码。

这是放在算法仓库里的"算法适配"文件（本仓库 examples/ 只作为模板），
它把 tzcomm 的管道数据转成 ROS 话题喂给 ROS2 算法，再把算法输出的话题
转回 tzcomm 的 RESULT。

进程/握手契约：
    1) 进程由评测服务按 scenario.yaml（manifest）拉起，会话身份由环境变量
       AUTOTEST_SESSION/AUTOTEST_TOPICS 注入；
    2) Service 经 ctl 发 INIT（携带 sensor_config，含 ROS 话题映射）；
    3) 算法回 READY 后进入测试流程（obs 收数据、result 回结果）。

ROS 环境由 manifest 的 launch 命令负责 source（Service 不感知 ROS）。
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass

import msgpack
import numpy as np
import tzcomm

# ---- 协议常量（算法侧按协议声明，不 import 框架）----

INIT = "init"
READY = "ready"
RESET = "reset"
RESET_ACK = "reset_ack"
STEP = "step"
RESULT = "result"
TERMINATE = "terminate"
FINAL = "final"

MODULE = "pipe.slam"  # 插件命名空间（v1.1 §3）
# 本算法消费/产出的数据 schema（v1.1 §4.2 数据面信封）
_OBS_SCHEMA = "pipe.slam.SlamObs"
_RESULT_SCHEMA = "pipe.slam.CylinderResult"
# 本算法声明的输入传感器需求（交给框架校验场景是否提供）
_REQUIRED_SENSORS = {"lidar": ["front", "rear"]}
# 本算法声明的输出话题（算法侧自有，不来自框架）
_CYLINDER_TOPIC = "/cylinder_extraction_independent/result"


@dataclass
class _CylinderSlot:
    timestamp: float
    center: tuple[float, float, float]
    direction: tuple[float, float, float]
    straightness_residual: float


class Ros2PipeBridge:
    """tzcomm 侧订阅 obs、发布 result、服务 ctl；ROS 侧发布 lidar/imu、订阅圆柱结果。"""

    def __init__(self, name: str = "pipe-bridge") -> None:
        self._node = tzcomm.Node(name)

        # ---- 会话身份由评测服务注入（环境变量），无需注册握手 ----
        session_id = os.environ.get("AUTOTEST_SESSION", "")
        topics = json.loads(os.environ.get("AUTOTEST_TOPICS", "{}"))
        if not session_id or not topics:
            raise RuntimeError("缺少 AUTOTEST_SESSION/AUTOTEST_TOPICS（应由评测服务注入）")
        self.session_id = session_id

        # ---- tzcomm 侧：声明数据输入 / 数据输出 / 指令三个接口 ----
        self._obs_sub = self._node.create_subscription(topics["obs"], self._on_obs, qos=1)
        self._result_pub = self._node.create_publisher(topics["result"], single_pub=True)
        self._node.create_service(topics["ctl"], self._on_ctl)

        # ---- ROS 侧（收到 INIT 后按 sensor_config 配置）----
        self._rclpy = None
        self._ros = None
        self._lidar_pubs: dict[str, object] = {}
        self._imu_pubs: dict[str, object] = {}

    # ---------------- tzcomm 指令（ctl service） ----------------
    def _on_ctl(self, request: dict) -> dict:
        mtype = request.get("type")
        payload = request.get("payload", {})
        if mtype == INIT:
            self._configure_ros(payload.get("sensor_config", {}))
            return {
                "type": READY,
                "session_id": self.session_id,
                "payload": {"module": MODULE, "required_sensors": _REQUIRED_SENSORS},
            }
        if mtype == RESET:
            return {"type": RESET_ACK, "session_id": self.session_id, "payload": {"ok": True}}
        if mtype == TERMINATE:
            return {"type": FINAL, "session_id": self.session_id, "payload": {}}
        raise ValueError(f"未知控制消息: {mtype!r}")

    def _configure_ros(self, sensor_config: dict) -> None:
        lidar_map = sensor_config.get("lidar", {})  # {实例名: ROS 话题}
        imu_map = sensor_config.get("imu", {})      # {实例名: ROS 话题}
        if not lidar_map:
            raise ValueError("sensor_config 缺少 lidar 实例")

        import rclpy
        from rclpy.node import Node
        import sensor_msgs_py.point_cloud2 as pc2
        from builtin_interfaces.msg import Time
        from sensor_msgs.msg import Imu, PointCloud2
        from std_msgs.msg import Header

        self._rclpy = rclpy
        self._pc2 = pc2
        self._Imu = Imu
        self._Header = Header
        self._Time = Time
        rclpy.init()
        self._ros = Node("ros2_pipe_bridge")
        self._lidar_pubs = {
            name: self._ros.create_publisher(PointCloud2, topic, 10)
            for name, topic in lidar_map.items()
        }
        self._imu_pubs = {
            name: self._ros.create_publisher(Imu, topic, 10)
            for name, topic in imu_map.items()
        }

        from fast_lio.msg import PipeCenterline

        self._ros.create_subscription(PipeCenterline, _CYLINDER_TOPIC, self._on_cylinder, 10)
        threading.Thread(target=self._spin_ros, daemon=True).start()

    def _spin_ros(self) -> None:
        from rclpy.executors import ExternalShutdownException

        try:
            while self._rclpy.ok():
                self._rclpy.spin_once(self._ros, timeout_sec=0.05)
        except ExternalShutdownException:
            pass

    # ---------------- tzcomm 数据输入（obs） ----------------
    def _on_obs(self, message: dict) -> None:
        if message.get("type") != STEP:
            return
        payload = message.get("payload", {})
        if payload.get("done"):
            return
        observation = payload.get("observation")
        if not observation:
            return

        lidars, imus = self._decode_observation(observation)
        self._publish_ros(observation.get("timestamp", 0.0), lidars, imus)
        # 不在此等待：算法积累多帧才出结果，结果由 _on_cylinder 异步回传

    @staticmethod
    def _decode_observation(observation: dict) -> tuple[dict, dict]:
        """observation 外层信封 {timestamp, module, data}；data 为数据面信封
        {schema, v, enc, blob}（v1.1 §4.2），blob 是 msgpack 编码的 SlamData dict。"""
        envelope = observation.get("data", {})
        if envelope.get("schema") != _OBS_SCHEMA:
            raise ValueError(f"未知 observation schema: {envelope.get('schema')!r}")
        data = msgpack.unpackb(envelope["blob"], raw=False)
        sensors = data.get("sensors", {})
        lidars = {
            name: np.frombuffer(raw["data"], dtype=np.dtype(raw["dtype"])).reshape(raw["shape"])
            for name, raw in sensors.get("lidar", {}).items()
        }
        imus = dict(sensors.get("imu", {}))
        return lidars, imus

    def _build_result(self, slot: _CylinderSlot) -> dict:
        """RESULT 消息：payload.data 为数据面信封 {schema, v, enc, blob}（v1.1 §4.2）。"""
        blob = msgpack.packb(
            {
                "timestamp": slot.timestamp,
                "center": list(slot.center),
                "direction": list(slot.direction),
                "valid": True,
                "straightness_residual": slot.straightness_residual,
                "radius": 0.0,
            },
            use_bin_type=True,
        )
        return {
            "type": RESULT,
            "session_id": self.session_id,
            "payload": {
                "module": MODULE,
                "data": {"schema": _RESULT_SCHEMA, "v": 1, "enc": "msgpack", "blob": blob},
            },
        }

    # ---------------- ROS 侧 ----------------
    def _publish_ros(self, ts: float, lidars: dict, imus: dict) -> None:
        if self._ros is None:
            return
        stamp = self._Time()
        stamp.sec = int(ts)
        stamp.nanosec = int((ts - int(ts)) * 1e9)

        for name, cloud in lidars.items():
            pub = self._lidar_pubs.get(name)
            if pub is None:
                continue
            header = self._Header(frame_id="lidar")
            header.stamp = stamp
            pub.publish(self._pc2.create_cloud_xyz32(header, cloud.astype("float32")))

        for name, imu in imus.items():
            pub = self._imu_pubs.get(name)
            if pub is None:
                continue
            msg = self._Imu()
            msg.header.frame_id = "imu"
            msg.header.stamp = stamp
            msg.angular_velocity.x = imu["angular_velocity"][0]
            msg.angular_velocity.y = imu["angular_velocity"][1]
            msg.angular_velocity.z = imu["angular_velocity"][2]
            msg.linear_acceleration.x = imu["linear_acceleration"][0]
            msg.linear_acceleration.y = imu["linear_acceleration"][1]
            msg.linear_acceleration.z = imu["linear_acceleration"][2]
            pub.publish(msg)

    def _on_cylinder(self, cylinder_msg) -> None:
        if not cylinder_msg.valid or len(cylinder_msg.points) < 2:
            return
        p1 = cylinder_msg.points[0]
        p2 = cylinder_msg.points[1]
        direction = (p2.x - p1.x, p2.y - p1.y, p2.z - p1.z)
        norm = (direction[0] ** 2 + direction[1] ** 2 + direction[2] ** 2) ** 0.5
        if norm > 1e-9:
            direction = (direction[0] / norm, direction[1] / norm, direction[2] / norm)
        stamp = cylinder_msg.header.stamp
        ts = stamp.sec + stamp.nanosec * 1e-9
        slot = _CylinderSlot(
            timestamp=ts,
            center=(p1.x, p1.y, p1.z),
            direction=direction,
            straightness_residual=float(cylinder_msg.straightness_residual),
        )
        self._result_pub.publish(self._build_result(slot))

    def spin(self) -> None:
        self._node.spin()

    def close(self) -> None:
        self._node.close()
        if self._rclpy is not None and self._rclpy.ok():
            self._rclpy.shutdown()


def main() -> None:
    bridge = Ros2PipeBridge()
    try:
        bridge.spin()
    finally:
        bridge.close()


if __name__ == "__main__":
    main()
