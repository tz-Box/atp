"""测试内容：data 信封编解码 roundtrip + observation 外层信封 + GT 信封 + 未知 schema 拒收。
期望输出：字段保真、lidar 字节往返一致、未注册 schema 抛 SchemaError。
"""
import numpy as np
import pytest

from autotest.protocol.schema import (
    Imu,
    Pose,
    SchemaError,
    StampedPose,
    decode_action,
    decode_ground_truth,
    decode_observation,
    decode_result,
    encode_action,
    encode_ground_truth,
    encode_observation,
    encode_result,
    make_observation,
)
from autotest.registry import load_plugin

slam = load_plugin("pipe.slam")  # 触发 pipe.slam schema 注册
nav2d = load_plugin("nav2d")  # 触发 nav2d schema 注册


def test_pose_and_stamped_roundtrip():
    pose = Pose(1, 2, 3, 0, 0, 0, 1)
    assert Pose.from_list(pose.to_list()) == pose
    stamped = StampedPose(1.0, pose)
    assert StampedPose.from_dict(stamped.to_dict()) == stamped


def test_slam_observation_roundtrip():
    lidar = np.random.default_rng(0).normal(size=(100, 3)).astype(np.float32)
    envelope = encode_observation(
        "pipe.slam.SlamObs",
        slam.SlamData(
            sensors={"lidar": {"front": lidar}, "imu": {"imu": Imu([0, 0, 0], [0, 0, 0])}},
            odom=Pose(1, 2, 3, 0, 0, 0, 1),
        ),
    )
    assert envelope["schema"] == "pipe.slam.SlamObs"
    assert envelope["v"] == 1
    assert envelope["enc"] == "msgpack"
    parsed = decode_observation(envelope)
    assert isinstance(parsed, slam.SlamData)
    assert np.array_equal(parsed.sensors["lidar"]["front"], lidar)
    assert parsed.odom.y == 2.0


def test_observation_outer_envelope():
    data = encode_observation("pipe.slam.SlamObs", slam.SlamData())
    obs = make_observation("pipe.slam", 0.5, data)
    assert set(obs) == {"timestamp", "module", "data"}
    assert obs["timestamp"] == 0.5
    assert obs["module"] == "pipe.slam"
    assert obs["data"] is data


def test_nav_observation_roundtrip():
    envelope = encode_observation(
        "nav2d.NavObs",
        nav2d.NavData(
            robot_pose=Pose(0, 0, 0, 0, 0, 0, 1),
            goal=Pose(5, 0, 0, 0, 0, 0, 1),
            obstacles=[(3.0, 1.5, 0.5)],
        ),
    )
    parsed = decode_observation(envelope)
    assert isinstance(parsed, nav2d.NavData)
    assert parsed.obstacles == [(3.0, 1.5, 0.5)]


def test_action_roundtrip():
    envelope = encode_action("nav2d.NavAction", nav2d.NavAction(v=0.5, w=0.1))
    parsed = decode_action(envelope)
    assert isinstance(parsed, nav2d.NavAction)
    assert parsed.v == 0.5


def test_result_roundtrip():
    envelope = encode_result("pipe.slam.StampedPose", StampedPose(1.0, Pose(1, 2, 3, 0, 0, 0, 1)))
    parsed = decode_result(envelope)
    assert isinstance(parsed, StampedPose)
    assert parsed.pose.z == 3.0


def test_ground_truth_roundtrip():
    envelope = encode_ground_truth("pipe.slam.Trajectory", {"trajectory": []})
    assert set(envelope) == {"schema", "v", "data"}
    assert decode_ground_truth(envelope) == {"trajectory": []}


def test_ground_truth_missing_fields_raises():
    with pytest.raises(SchemaError):
        decode_ground_truth({"data": {}})


def test_unknown_schema_rejected():
    """未知 schema 必须拒收（不再静默透传）。"""
    with pytest.raises(SchemaError):
        decode_observation({"schema": "manip.Obs", "v": 1, "enc": "msgpack", "blob": b"\x91\x80"})
    with pytest.raises(SchemaError):
        encode_observation("manip.Obs", slam.SlamData())
