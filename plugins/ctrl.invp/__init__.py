"""ctrl.invp 插件：倒立摆闭环评测（仿真 + checker）。

命名空间：ctrl.invp
produces: [ctrl.invp.InvpObs, ctrl.invp.InvpAction, ctrl.invp.InvpTask]
consumes: [ctrl.invp.InvpObs]
"""
from autotest.registry import register_checker, register_dataset

from .data import InvpAction, InvpObs, register_schemas
from .checker import InvpChecker
from .sim import InvpPhysics, InvpScenario, InvpSimWorld, basic_invp_scenarios

register_schemas()

register_dataset("ctrl.invp.sim", lambda **cfg: InvpSimWorld.from_config(cfg))

register_checker("ctrl.invp.upright", InvpChecker)

__all__ = [
    "InvpAction", "InvpChecker", "InvpObs", "InvpPhysics", "InvpScenario",
    "InvpSimWorld", "basic_invp_scenarios",
]
