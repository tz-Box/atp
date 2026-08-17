"""参考闭环 SUT：最小 Nav 控制器（朝 goal 转向并前进）。"""
import math

from autotest.protocol.schema import NAV, Action, quat_to_yaw
from autotest.sdk import SutBase
from autotest.protocol.data.nav import NavAction


class SimpleNav(SutBase):
    module = "nav"

    def on_reset(self, testcase_meta):
        pass

    def on_step(self, observation):
        nav = observation.data  # NavData
        robot = nav.robot_pose
        goal = nav.goal
        yaw = quat_to_yaw(robot.qx, robot.qy, robot.qz, robot.qw)
        target = math.atan2(goal.y - robot.y, goal.x - robot.x)
        heading_error = math.atan2(math.sin(target - yaw), math.cos(target - yaw))
        w = max(-1.0, min(1.0, heading_error * 2.0))
        v = 0.5 if abs(heading_error) < 0.6 else 0.0
        return Action(NAV, NavAction(v=v, w=w))
