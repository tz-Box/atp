"""nav2d 插件：2D 差速底盘导航闭环评测（仿真 + checker）。

命名空间：nav2d
produces: [nav2d.NavObs, nav2d.NavAction, nav2d.NavGoal]
consumes: [nav2d.NavObs]
"""
from autotest.registry import register_checker, register_dataset

from .data import NavAction, NavData, register_schemas
from .checker import NavChecker
from .sim import NavScenario, SimWorld, simple_nav_scenarios

register_schemas()

register_dataset("nav2d.sim", lambda **cfg: SimWorld.from_config(cfg))

register_checker("nav2d.default", NavChecker)

__all__ = ["NavAction", "NavChecker", "NavData", "NavScenario", "SimWorld", "simple_nav_scenarios"]
