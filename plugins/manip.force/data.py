"""manip.force 数据 schema：单自由度接触力控观测 payload（ManipObs）与闭环指令（ManipAction）。"""
from __future__ import annotations

from dataclasses import dataclass

import msgpack

from autotest.protocol.schema import register_data


@dataclass
class ManipAction:
    """闭环指令：作用于末端执行器的推力（N，压缩壁面方向为正），仿真侧按 force_limit 截断。"""

    force: float

    def to_dict(self) -> dict:
        return {"force": self.force}

    @classmethod
    def from_dict(cls, data: dict) -> "ManipAction":
        return cls(force=float(data["force"]))


@dataclass
class ManipObs:
    """观测 payload：末端位置/速度 + 当前接触力 + 目标接触力（SI 单位）。

    target_force 随 testcase 变、由 World 经 obs 下发（对齐 nav2d goal 先例）：
    SUT 不依赖场景先验，完全按帧自适应。
    """

    x: float           # 末端位置（m）
    x_dot: float       # 末端速度（m/s）
    f_contact: float   # 当前接触力（N，未接触为 0）
    target_force: float  # 目标接触力（N）

    def to_dict(self) -> dict:
        return {
            "x": self.x,
            "x_dot": self.x_dot,
            "f_contact": self.f_contact,
            "target_force": self.target_force,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ManipObs":
        return cls(
            x=float(data["x"]),
            x_dot=float(data["x_dot"]),
            f_contact=float(data["f_contact"]),
            target_force=float(data["target_force"]),
        )


def register_schemas():
    """注册跨进程数据 schema（observation/action）。

    GT（manip.force.ForceTask）不注册：进程内传递、不编码（v1.1 §6）。
    """
    register_data("observation", "manip.force.ManipObs",
                  lambda b: ManipObs.from_dict(msgpack.unpackb(b, raw=False)),
                  lambda obj: msgpack.packb(obj.to_dict(), use_bin_type=True))
    register_data("action", "manip.force.ManipAction",
                  lambda b: ManipAction.from_dict(msgpack.unpackb(b, raw=False)),
                  lambda obj: msgpack.packb(obj.to_dict(), use_bin_type=True))
