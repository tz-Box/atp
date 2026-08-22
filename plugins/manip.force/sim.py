"""manip.force 闭环仿真：单自由度末端执行器 + 弹簧-阻尼接触壁面。

动力学（半隐式欧拉积分）：
    x <  x_wall 时无接触，f = 0（自由空间）
    x >= x_wall 时接触，  f = k·(x - x_wall) + c·ẋ（弹簧-阻尼压缩力）
    m·ẍ = F_applied - f
任务：从非接触区出发接近壁面，建立并维持目标接触力 target_force；
终止条件：force_exceeded（f > f_limit，压坏判失败）/ survived（撑满 max_steps）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from autotest.protocol.schema import (
    decode_action,
    encode_ground_truth,
    encode_observation,
    make_observation,
)
from autotest.world.base import IWorld

from .data import ManipObs


@dataclass
class ForcePhysics:
    mass: float = 1.0           # 末端等效质量（kg）
    k_contact: float = 200.0    # 接触刚度（N/m，软接触）
    c_contact: float = 1.0      # 接触阻尼（N·s/m）
    force_limit: float = 50.0   # SUT 推力截断（N）
    f_limit: float = 25.0       # 接触力上限（N，超过判 force_exceeded）


@dataclass
class ForceScenario:
    target_force: float = 5.0   # 目标接触力（N）
    x0: float = 0.48            # 初始位置（m，< x_wall 非接触出发）
    x_dot0: float = 0.0
    x_wall: float = 0.5         # 壁面位置（m）
    dt: float = 0.001           # 接触刚度高，步长取 1ms
    max_steps: int = 5000       # 5s @ 1kHz（接触收敛需要时间）


class ForceSimWorld(IWorld):
    """单自由度接触力控仿真：action 为 ManipAction(force)，step 按 dt 积分推进。"""

    closed_loop = True
    produces = ["manip.force.ManipObs"]

    def __init__(self, scenarios: dict[str, ForceScenario], physics: Optional[ForcePhysics] = None) -> None:
        self._scenarios = scenarios
        self._physics = physics or ForcePhysics()
        self._scenario: Optional[ForceScenario] = None
        self._x = 0.0
        self._x_dot = 0.0
        self._f_contact = 0.0
        self._time = 0.0
        self._steps = 0

    @classmethod
    def from_config(cls, cfg: dict) -> "ForceSimWorld":
        """场景 config → World：{testcases: {id: {target_force, ...}}, physics: {...}}。"""
        raw = cfg.get("testcases")
        if not raw:
            raise ValueError("manip.force.sim 的 dataset.config 缺 testcases")
        scenarios = {tid: ForceScenario(**params) for tid, params in raw.items()}
        physics = ForcePhysics(**cfg["physics"]) if cfg.get("physics") else None
        return cls(scenarios, physics)

    @property
    def testcases(self) -> list[str]:
        return list(self._scenarios)

    def reset(self, testcase_id: str) -> dict:
        """重置并返回首个 observation 外层信封 dict（{timestamp, module, data}）。"""
        self._scenario = self._scenarios[testcase_id]
        self._x, self._x_dot = self._scenario.x0, self._scenario.x_dot0
        self._f_contact = 0.0
        self._time = 0.0
        self._steps = 0
        return self._observation()

    def step(self, action: dict) -> tuple[Optional[dict], bool, dict]:
        """action 为闭环指令 payload dict（{module, data}，data 为 ManipAction 数据面信封）。"""
        if self._scenario is None:
            raise RuntimeError("ForceSimWorld.step() 前必须先 reset()")
        manip_action = decode_action(action["data"]) if isinstance(action, dict) else action
        p = self._physics
        force = max(-p.force_limit, min(p.force_limit, manip_action.force))

        pen = self._x - self._scenario.x_wall  # 穿透深度（>0 接触）
        self._f_contact = p.k_contact * pen + p.c_contact * self._x_dot if pen > 0 else 0.0
        x_acc = (force - self._f_contact) / p.mass

        dt = self._scenario.dt
        self._x_dot += x_acc * dt
        self._x += self._x_dot * dt
        self._time += dt
        self._steps += 1
        # 用推进后的状态重算接触力，保证 obs 与位形一致
        pen = self._x - self._scenario.x_wall
        self._f_contact = max(0.0, p.k_contact * pen + p.c_contact * self._x_dot) if pen > 0 else 0.0

        if self._f_contact > p.f_limit:
            return self._observation(), True, {"reason": "force_exceeded"}
        if self._steps >= self._scenario.max_steps:
            return self._observation(), True, {"reason": "survived"}
        return self._observation(), False, {"reason": None}

    def get_ground_truth(self) -> dict:
        """返回 GT data 信封 dict（任务参数，checker 据此判稳态力跟踪）。"""
        if self._scenario is None:
            raise RuntimeError("ForceSimWorld.get_ground_truth() 前必须先 reset()")
        return encode_ground_truth("manip.force.ForceTask", {
            "dt": self._scenario.dt,
            "max_steps": self._scenario.max_steps,
            "target_force": self._scenario.target_force,
            "x_wall": self._scenario.x_wall,
            "f_limit": self._physics.f_limit,
        })

    def close(self) -> None:
        self._scenario = None

    def _observation(self) -> dict:
        """组当前状态的 observation 外层信封（timestamp 取仿真时钟）。"""
        return make_observation("manip.force", self._time, encode_observation(
            "manip.force.ManipObs",
            ManipObs(x=self._x, x_dot=self._x_dot, f_contact=self._f_contact,
                     target_force=self._scenario.target_force),
        ))


def basic_force_scenarios() -> dict[str, ForceScenario]:
    """示例场景：两档目标接触力，正常 PI 力控 + 阻尼均可稳。"""
    return {
        "light_touch": ForceScenario(target_force=3.0),
        "firm_press": ForceScenario(target_force=8.0),
    }
