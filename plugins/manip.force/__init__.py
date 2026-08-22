"""manip.force 插件：单自由度接触力控闭环评测（仿真 + checker）。

命名空间：manip.force
produces: [manip.force.ManipObs, manip.force.ManipAction, manip.force.ForceTask]
consumes: [manip.force.ManipObs]
"""
from autotest.registry import register_checker, register_dataset

from .data import ManipAction, ManipObs, register_schemas
from .checker import ForceChecker
from .sim import ForcePhysics, ForceScenario, ForceSimWorld, basic_force_scenarios

register_schemas()

register_dataset("manip.force.sim", lambda **cfg: ForceSimWorld.from_config(cfg))

register_checker("manip.force.track", ForceChecker)

__all__ = [
    "ForceChecker", "ForcePhysics", "ForceScenario", "ForceSimWorld",
    "ManipAction", "ManipObs", "basic_force_scenarios",
]
