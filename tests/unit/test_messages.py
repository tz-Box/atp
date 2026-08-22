"""测试内容：消息信封 roundtrip 与数据消息（step/result/action）编解码。
期望输出：正常 roundtrip 一致；非法类型抛 ValueError；data 字段可经 schema 解码。
"""
import pytest

from autotest.protocol import messages as msg
from autotest.protocol.schema import (
    Pose,
    StampedPose,
    decode_action,
    decode_observation,
    decode_result,
    encode_action,
    encode_observation,
    encode_result,
    make_observation,
)
from autotest.registry import load_plugin

slam = load_plugin("pipe.slam")  # 触发 schema 注册
nav2d = load_plugin("nav2d")


def test_message_roundtrip():
    message = msg.Message(msg.INIT, "s1", {"consumes": ["pipe.slam.SlamObs"]})
    assert msg.Message.from_dict(message.to_dict()) == message


def test_unknown_type_raises():
    with pytest.raises(ValueError):
        msg.Message("bogus", "s1")


def test_empty_session_id_raises():
    with pytest.raises(ValueError):
        msg.Message(msg.INIT, "")


def test_init_with_body_profile():
    message = msg.init("s1", {"k": 1}, body_profile={"body_name": "pbox", "body_version": "1"})
    parsed = msg.Message.from_dict(message.to_dict())
    assert parsed.payload["body_profile"]["body_name"] == "pbox"
    assert parsed.payload["k"] == 1


def test_step_roundtrip():
    obs = make_observation(
        "pipe.slam",
        1.0,
        encode_observation("pipe.slam.SlamObs", slam.SlamData(odom=Pose(1, 2, 3, 0, 0, 0, 1))),
    )
    message = msg.step("s1", obs, done=False)
    parsed_obs, done = msg.parse_step(msg.Message.from_dict(message.to_dict()))
    assert not done
    assert parsed_obs["timestamp"] == 1.0
    payload = decode_observation(parsed_obs["data"])
    assert payload.odom.x == 1.0


def test_step_done_without_observation():
    message = msg.step("s1", None, done=True)
    parsed_obs, done = msg.parse_step(msg.Message.from_dict(message.to_dict()))
    assert done
    assert parsed_obs is None


def test_result_roundtrip():
    output = msg.Result(
        "pipe.slam",
        encode_result("pipe.slam.StampedPose", StampedPose(2.0, Pose(1, 2, 3, 0, 0, 0, 1))),
    )
    message = msg.result("s1", output)
    payload = msg.parse_result(msg.Message.from_dict(message.to_dict()))
    assert payload["module"] == "pipe.slam"
    parsed = decode_result(payload["data"])
    assert parsed.pose.z == 3.0


def test_action_roundtrip():
    command = msg.Action("nav2d", encode_action("nav2d.NavAction", nav2d.NavAction(v=0.5, w=0.1)))
    message = msg.action("s1", command)
    payload = msg.parse_action(msg.Message.from_dict(message.to_dict()))
    assert payload["module"] == "nav2d"
    parsed = decode_action(payload["data"])
    assert parsed.v == 0.5
    assert parsed.w == 0.1
