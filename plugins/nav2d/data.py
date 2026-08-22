"""nav2d 数据 schema：观测 payload（NavData）与闭环指令（NavAction）。"""
from __future__ import annotations

from dataclasses import dataclass, field

import msgpack

from autotest.protocol.schema import Pose, register_data


@dataclass
class NavAction:
    """闭环指令：差速底盘线速度 / 角速度。"""

    v: float
    w: float

    def to_dict(self) -> dict:
        return {"v": self.v, "w": self.w}

    @classmethod
    def from_dict(cls, data: dict) -> "NavAction":
        return cls(v=float(data["v"]), w=float(data["w"]))


@dataclass
class NavData:
    """观测 payload：机器人位姿 + 目标点 + 圆形障碍。"""

    robot_pose: Pose
    goal: Pose
    obstacles: list[tuple[float, float, float]] = field(default_factory=list)  # (cx, cy, r)

    def to_dict(self) -> dict:
        return {
            "robot_pose": self.robot_pose.to_list(),
            "goal": self.goal.to_list(),
            "obstacles": [list(o) for o in self.obstacles],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "NavData":
        return cls(
            robot_pose=Pose.from_list(data["robot_pose"]),
            goal=Pose.from_list(data["goal"]),
            obstacles=[tuple(o) for o in data.get("obstacles", [])],
        )


def register_schemas():
    """注册跨进程数据 schema（observation/action）。

    GT（nav2d.NavGoal）不注册：进程内传递、不编码（v1.1 §6）。
    """
    register_data("observation", "nav2d.NavObs",
                  lambda b: NavData.from_dict(msgpack.unpackb(b, raw=False)),
                  lambda obj: msgpack.packb(obj.to_dict(), use_bin_type=True))
    register_data("action", "nav2d.NavAction",
                  lambda b: NavAction.from_dict(msgpack.unpackb(b, raw=False)),
                  lambda obj: msgpack.packb(obj.to_dict(), use_bin_type=True))
