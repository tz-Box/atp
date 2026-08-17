"""NAV 模块：数据、闭环仿真、checker。"""
from autotest.registry import register_checker

from .checker import NavChecker
from autotest.protocol.data.nav import NavAction, NavData  # noqa: F401  触发 data 注册
from .sim import NavScenario, SimWorld, simple_nav_scenarios

register_checker("nav", NavChecker)

__all__ = ["NavAction", "NavChecker", "NavData", "NavScenario", "SimWorld", "simple_nav_scenarios"]
