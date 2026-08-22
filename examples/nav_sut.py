"""参考闭环 SUT：最小 Nav 控制器（朝 goal 转向并前进）。"""
import math

from autotest.protocol import messages as msg
from autotest.protocol.schema import decode_observation, encode_action
from autotest.registry import load_plugin
from autotest.sdk import SutBase

nav2d = load_plugin("nav2d")  # 注册 nav2d schema 编解码器


class SimpleNav(SutBase):
    module = "nav2d"

    def on_reset(self, testcase_meta):
        pass

    def on_step(self, observation):
        nav = decode_observation(observation["data"])  # NavData
        robot = nav.robot_pose
        goal = nav.goal
        yaw = 2.0 * math.atan2(robot.qz, robot.qw)  # 平面 yaw 四元数（仅 z/w 分量）
        target = math.atan2(goal.y - robot.y, goal.x - robot.x)
        heading_error = math.atan2(math.sin(target - yaw), math.cos(target - yaw))
        w = max(-1.0, min(1.0, heading_error * 2.0))
        v = 0.5 if abs(heading_error) < 0.6 else 0.0
        return msg.Action("nav2d", encode_action("nav2d.NavAction", nav2d.NavAction(v=v, w=w)))


def main() -> None:
    sut = SimpleNav("simple-nav")
    try:
        sut.spin()
    finally:
        sut.close()


if __name__ == "__main__":
    main()
