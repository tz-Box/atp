"""ctrl.invp 数据 schema：倒立摆观测 payload（InvpObs）与闭环指令（InvpAction）。"""
from __future__ import annotations

from dataclasses import dataclass

import msgpack

from autotest.protocol.schema import register_data


@dataclass
class InvpAction:
    """闭环指令：作用于小车的水平力（N），仿真侧按 force_limit 截断。"""

    force: float

    def to_dict(self) -> dict:
        return {"force": self.force}

    @classmethod
    def from_dict(cls, data: dict) -> "InvpAction":
        return cls(force=float(data["force"]))


@dataclass
class InvpObs:
    """观测 payload：小车位置/速度 + 摆杆角度/角速度（SI 单位，theta=0 为竖直向上）。"""

    x: float
    x_dot: float
    theta: float
    theta_dot: float

    def to_dict(self) -> dict:
        return {"x": self.x, "x_dot": self.x_dot, "theta": self.theta, "theta_dot": self.theta_dot}

    @classmethod
    def from_dict(cls, data: dict) -> "InvpObs":
        return cls(
            x=float(data["x"]),
            x_dot=float(data["x_dot"]),
            theta=float(data["theta"]),
            theta_dot=float(data["theta_dot"]),
        )


def register_schemas():
    """注册跨进程数据 schema（observation/action）。

    GT（ctrl.invp.InvpTask）不注册：进程内传递、不编码（v1.1 §6）。
    """
    register_data("observation", "ctrl.invp.InvpObs",
                  lambda b: InvpObs.from_dict(msgpack.unpackb(b, raw=False)),
                  lambda obj: msgpack.packb(obj.to_dict(), use_bin_type=True))
    register_data("action", "ctrl.invp.InvpAction",
                  lambda b: InvpAction.from_dict(msgpack.unpackb(b, raw=False)),
                  lambda obj: msgpack.packb(obj.to_dict(), use_bin_type=True))
