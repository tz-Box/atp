"""测试内容：闭环 Nav 端到端（Service + TzComm + SUT + SimWorld + NavChecker）。
期望输出：straight 场景到达 goal、安全裕度达标，passed=True。
"""
import math
import uuid

from autotest.eval import ClosedLoopSession
from autotest.protocol import messages as msg
from autotest.protocol.schema import decode_observation, encode_action
from autotest.registry import load_plugin
from autotest.sdk import SutBase

nav2d = load_plugin("nav2d")


class _SimpleNav(SutBase):
    module = "nav2d"

    def on_step(self, observation):
        nav = decode_observation(observation["data"])  # NavData
        robot = nav.robot_pose
        goal = nav.goal
        yaw = 2.0 * math.atan2(robot.qz, robot.qw)  # 平面 yaw 四元数（仅 z/w 分量）
        target = math.atan2(goal.y - robot.y, goal.x - robot.x)
        error = math.atan2(math.sin(target - yaw), math.cos(target - yaw))
        w = max(-1.0, min(1.0, error * 2.0))
        v = 0.5 if abs(error) < 0.6 else 0.0
        return msg.Action("nav2d", encode_action("nav2d.NavAction", nav2d.NavAction(v=v, w=w)))


def test_closed_loop_end_to_end(daemon):
    scenarios = nav2d.simple_nav_scenarios()
    session_id = f"nav-func-{uuid.uuid4().hex[:8]}"
    world = nav2d.SimWorld(scenarios)
    checker = nav2d.NavChecker()

    session = ClosedLoopSession(world, checker, session_id=session_id, name="nav-service")
    sut = _SimpleNav("nav-sut", session_id)
    try:
        results = session.run(
            list(scenarios.keys()),
            checker_config={"arrival_tolerance": 0.2, "safety_margin": 0.3},
        )
    finally:
        session.close()
        sut.close()

    assert len(results) == 1
    result = results[0]
    assert result.score is not None
    assert result.score.passed
    assert result.score.metrics["arrived"] == 1.0
    assert result.score.metrics["safety_margin"] >= 0.3
