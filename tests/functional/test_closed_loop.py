"""测试内容：闭环 Nav 端到端（Service + TzComm + SUT + SimWorld + NavChecker）。
期望输出：simple 场景到达 goal、安全裕度达标，passed=True。
"""
import math
import uuid

from autotest.protocol.schema import NAV, Action, quat_to_yaw
from autotest.eval import ClosedLoopSession
from autotest.sdk import SutBase
from modules.nav import NavChecker, SimWorld, simple_nav_scenarios
from autotest.protocol.data.nav import NavAction


class _SimpleNav(SutBase):
    module = "nav"

    def on_step(self, observation):
        nav = observation.data  # NavData
        robot = nav.robot_pose
        goal = nav.goal
        yaw = quat_to_yaw(robot.qx, robot.qy, robot.qz, robot.qw)
        target = math.atan2(goal.y - robot.y, goal.x - robot.x)
        error = math.atan2(math.sin(target - yaw), math.cos(target - yaw))
        w = max(-1.0, min(1.0, error * 2.0))
        v = 0.5 if abs(error) < 0.6 else 0.0
        return Action(NAV, NavAction(v=v, w=w))


def test_closed_loop_end_to_end(daemon):
    scenarios = simple_nav_scenarios()
    session_id = f"nav-func-{uuid.uuid4().hex[:8]}"
    world = SimWorld(scenarios)
    checker = NavChecker()

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
