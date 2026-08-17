"""NAV 闭环仿真：差速底盘 + goal + 圆形障碍。"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from autotest.protocol.data.nav import NavAction, NavData
from autotest.protocol.schema import NAV, Observation, Pose, yaw_to_quat
from autotest.world.base import GroundTruth, IWorld


@dataclass
class NavScenario:
    start: tuple[float, float, float]  # (x, y, yaw)
    goal: tuple[float, float]
    obstacles: list[tuple[float, float, float]] = field(default_factory=list)  # (cx, cy, r)
    dt: float = 0.1
    max_steps: int = 200
    arrival_tolerance: float = 0.2


class SimWorld(IWorld):
    """差速底盘运动学仿真：action 为 NavAction(v, w)，step 推进 dt 更新位姿。"""

    def __init__(self, scenarios: dict[str, NavScenario]) -> None:
        self._scenarios = scenarios
        self._scenario: Optional[NavScenario] = None
        self._x = 0.0
        self._y = 0.0
        self._yaw = 0.0
        self._time = 0.0
        self._steps = 0

    @property
    def testcases(self) -> list[str]:
        return list(self._scenarios)

    def reset(self, testcase_id: str) -> Observation:
        self._scenario = self._scenarios[testcase_id]
        self._x, self._y, self._yaw = self._scenario.start
        self._time = 0.0
        self._steps = 0
        return self._observation()

    def step(self, action: NavAction) -> tuple[Optional[Observation], bool, dict]:
        if self._scenario is None:
            raise RuntimeError("SimWorld.step() 前必须先 reset()")
        dt = self._scenario.dt
        self._x += action.v * math.cos(self._yaw) * dt
        self._y += action.v * math.sin(self._yaw) * dt
        self._yaw += action.w * dt
        self._time += dt
        self._steps += 1

        if self._hit_obstacle():
            return self._observation(), True, {"reason": "collision"}
        if self._arrived():
            return self._observation(), True, {"reason": "arrived"}
        if self._steps >= self._scenario.max_steps:
            return self._observation(), True, {"reason": "timeout"}
        return self._observation(), False, {"reason": None}

    def get_ground_truth(self) -> GroundTruth:
        if self._scenario is None:
            raise RuntimeError("SimWorld.get_ground_truth() 前必须先 reset()")
        return GroundTruth(
            data={"goal": self._scenario.goal, "obstacles": self._scenario.obstacles}
        )

    def close(self) -> None:
        self._scenario = None

    def _observation(self) -> Observation:
        qx, qy, qz, qw = yaw_to_quat(self._yaw)
        robot_pose = Pose(self._x, self._y, 0.0, qx, qy, qz, qw)
        gx, gy = self._scenario.goal
        goal = Pose(gx, gy, 0.0, 0.0, 0.0, 0.0, 1.0)
        return Observation(
            self._time, NAV, NavData(robot_pose=robot_pose, goal=goal, obstacles=self._scenario.obstacles)
        )

    def _arrived(self) -> bool:
        gx, gy = self._scenario.goal
        return math.hypot(self._x - gx, self._y - gy) <= self._scenario.arrival_tolerance

    def _hit_obstacle(self) -> bool:
        for cx, cy, r in self._scenario.obstacles:
            if math.hypot(self._x - cx, self._y - cy) < r:
                return True
        return False


def simple_nav_scenarios() -> dict[str, NavScenario]:
    """示例场景：正前方 goal，路线上无阻挡，简单控制器可到达。"""
    return {
        "straight": NavScenario(
            start=(0.0, 0.0, 0.0),
            goal=(5.0, 0.0),
            obstacles=[(3.0, 1.5, 0.5)],
            max_steps=300,
        ),
    }
