"""NAV 协议 data：观测 payload（NavData）与闭环指令（NavAction）。"""
from __future__ import annotations

from dataclasses import dataclass, field

from autotest.protocol.schema import NAV, Pose, register_data


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


register_data("observation", NAV, NavData.from_dict)
register_data("action", NAV, NavAction.from_dict)
