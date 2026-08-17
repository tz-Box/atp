"""测试内容：消息信封 roundtrip 与协议消息编解码。
期望输出：正常 roundtrip 一致；非法类型抛 ValueError。
"""
import pytest

import modules.nav  # noqa: F401
import modules.slam  # noqa: F401
from autotest.protocol import messages as msg
from autotest.protocol.schema import NAV, SLAM, Action, Observation, Pose, Result, StampedPose
from autotest.protocol.data.nav import NavAction
from autotest.protocol.data.slam import SlamData


def test_message_roundtrip():
    message = msg.Message(msg.INIT, "s1", {"module_type": "slam"})
    assert msg.Message.from_dict(message.to_dict()) == message


def test_unknown_type_raises():
    with pytest.raises(ValueError):
        msg.Message("bogus", "s1")


def test_empty_session_id_raises():
    with pytest.raises(ValueError):
        msg.Message(msg.INIT, "")


def test_step_roundtrip():
    observation = Observation(1.0, SLAM, SlamData(odom=Pose(1, 2, 3, 0, 0, 0, 1)))
    message = msg.step("s1", observation, done=False)
    parsed_obs, done = msg.parse_step(msg.Message.from_dict(message.to_dict()))
    assert not done
    assert parsed_obs.timestamp == 1.0
    assert parsed_obs.data.odom.x == 1.0


def test_result_roundtrip():
    output = Result(SLAM, StampedPose(2.0, Pose(1, 2, 3, 0, 0, 0, 1)))
    message = msg.result("s1", output)
    parsed = msg.parse_result(msg.Message.from_dict(message.to_dict()))
    assert parsed.data.pose.z == 3.0


def test_action_roundtrip():
    command = Action(NAV, NavAction(v=0.5, w=0.1))
    message = msg.action("s1", command)
    parsed = msg.parse_action(msg.Message.from_dict(message.to_dict()))
    assert parsed.data.v == 0.5
    assert parsed.data.w == 0.1
