"""ctrl.invp 闭环仿真：经典车-倒立摆动力学（Sutton-Barto 参数化）。

动力学（半隐式欧拉积分，同 gym cartpole 约定）：
    temp = (F + mp·l·θ̇²·sinθ) / (mc+mp)
    θ̈   = (g·sinθ - cosθ·temp) / (l·(4/3 - mp·cos²θ/(mc+mp)))
    ẍ   = temp - mp·l·θ̈·cosθ/(mc+mp)
theta=0 为竖直向上；终止条件：fell（|θ| 超限）/ out_of_bounds（|x| 超限）/ survived（撑满 max_steps）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from autotest.protocol.schema import (
    decode_action,
    encode_ground_truth,
    encode_observation,
    make_observation,
)
from autotest.world.base import IWorld

from .data import InvpObs


@dataclass
class InvpPhysics:
    g: float = 9.8
    cart_mass: float = 1.0
    pole_mass: float = 0.1
    half_length: float = 0.5  # 摆长一半（质心距铰点）
    force_limit: float = 10.0
    theta_limit: float = 0.2095  # ≈12°，超过判 fell
    x_limit: float = 2.4  # 轨道半宽，超过判 out_of_bounds


@dataclass
class InvpScenario:
    theta0: float = 0.0  # 初始摆角扰动（rad）
    theta_dot0: float = 0.0
    x0: float = 0.0
    x_dot0: float = 0.0
    dt: float = 0.02
    max_steps: int = 500  # 10s @ 50Hz


class InvpSimWorld(IWorld):
    """车-倒立摆仿真：action 为 InvpAction(force)，step 按 dt 积分推进。"""

    closed_loop = True
    produces = ["ctrl.invp.InvpObs"]

    def __init__(self, scenarios: dict[str, InvpScenario], physics: Optional[InvpPhysics] = None) -> None:
        self._scenarios = scenarios
        self._physics = physics or InvpPhysics()
        self._scenario: Optional[InvpScenario] = None
        self._x = 0.0
        self._x_dot = 0.0
        self._theta = 0.0
        self._theta_dot = 0.0
        self._time = 0.0
        self._steps = 0

    @classmethod
    def from_config(cls, cfg: dict) -> "InvpSimWorld":
        """场景 config → World：{testcases: {id: {theta0, ...}}, physics: {...}}。"""
        raw = cfg.get("testcases")
        if not raw:
            raise ValueError("ctrl.invp.sim 的 dataset.config 缺 testcases")
        scenarios = {tid: InvpScenario(**params) for tid, params in raw.items()}
        physics = InvpPhysics(**cfg["physics"]) if cfg.get("physics") else None
        return cls(scenarios, physics)

    @property
    def testcases(self) -> list[str]:
        return list(self._scenarios)

    def reset(self, testcase_id: str) -> dict:
        """重置并返回首个 observation 外层信封 dict（{timestamp, module, data}）。"""
        self._scenario = self._scenarios[testcase_id]
        self._x, self._x_dot = self._scenario.x0, self._scenario.x_dot0
        self._theta, self._theta_dot = self._scenario.theta0, self._scenario.theta_dot0
        self._time = 0.0
        self._steps = 0
        return self._observation()

    def step(self, action: dict) -> tuple[Optional[dict], bool, dict]:
        """action 为闭环指令 payload dict（{module, data}，data 为 InvpAction 数据面信封）。"""
        if self._scenario is None:
            raise RuntimeError("InvpSimWorld.step() 前必须先 reset()")
        invp_action = decode_action(action["data"]) if isinstance(action, dict) else action
        p = self._physics
        force = max(-p.force_limit, min(p.force_limit, invp_action.force))

        total_mass = p.cart_mass + p.pole_mass
        polemass_length = p.pole_mass * p.half_length
        costheta, sintheta = math.cos(self._theta), math.sin(self._theta)
        temp = (force + polemass_length * self._theta_dot**2 * sintheta) / total_mass
        theta_acc = (p.g * sintheta - costheta * temp) / (
            p.half_length * (4.0 / 3.0 - p.pole_mass * costheta**2 / total_mass)
        )
        x_acc = temp - polemass_length * theta_acc * costheta / total_mass

        dt = self._scenario.dt
        self._x_dot += x_acc * dt
        self._x += self._x_dot * dt
        self._theta_dot += theta_acc * dt
        self._theta += self._theta_dot * dt
        self._time += dt
        self._steps += 1

        if abs(self._theta) > p.theta_limit:
            return self._observation(), True, {"reason": "fell"}
        if abs(self._x) > p.x_limit:
            return self._observation(), True, {"reason": "out_of_bounds"}
        if self._steps >= self._scenario.max_steps:
            return self._observation(), True, {"reason": "survived"}
        return self._observation(), False, {"reason": None}

    def get_ground_truth(self) -> dict:
        """返回 GT data 信封 dict（任务参数，checker 据此判存活与稳态）。"""
        if self._scenario is None:
            raise RuntimeError("InvpSimWorld.get_ground_truth() 前必须先 reset()")
        p = self._physics
        return encode_ground_truth("ctrl.invp.InvpTask", {
            "dt": self._scenario.dt,
            "max_steps": self._scenario.max_steps,
            "theta_limit": p.theta_limit,
            "x_limit": p.x_limit,
        })

    def close(self) -> None:
        self._scenario = None

    def _observation(self) -> dict:
        """组当前状态的 observation 外层信封（timestamp 取仿真时钟）。"""
        return make_observation("ctrl.invp", self._time, encode_observation(
            "ctrl.invp.InvpObs",
            InvpObs(x=self._x, x_dot=self._x_dot, theta=self._theta, theta_dot=self._theta_dot),
        ))


def basic_invp_scenarios() -> dict[str, InvpScenario]:
    """示例场景：两档初始扰动，正常 PD/LQR 控制器均可稳。"""
    return {
        "small_push": InvpScenario(theta0=0.05),
        "medium_push": InvpScenario(theta0=0.10, theta_dot0=0.2),
    }
