"""测试内容：三信封（Observation/Action/Result）序列化 roundtrip + data 注册分派。
期望输出：字段保真、lidar 字节往返一致、未知模块 payload 原样保留。
"""
import numpy as np

import modules.nav  # noqa: F401  触发 data 注册
import modules.slam  # noqa: F401  触发 data 注册
from autotest.protocol.schema import (
    NAV,
    SLAM,
    Action,
    Imu,
    Observation,
    Pose,
    Result,
    StampedPose,
)
from autotest.protocol.data.nav import NavAction, NavData
from autotest.protocol.data.slam import SlamData


def test_pose_and_stamped_roundtrip():
    pose = Pose(1, 2, 3, 0, 0, 0, 1)
    assert Pose.from_list(pose.to_list()) == pose
    stamped = StampedPose(1.0, pose)
    assert StampedPose.from_dict(stamped.to_dict()) == stamped


def test_slam_observation_roundtrip():
    lidar = np.random.default_rng(0).normal(size=(100, 3)).astype(np.float32)
    observation = Observation(
        0.5,
        SLAM,
        SlamData(
            sensors={"lidar": {"front": lidar}, "imu": {"imu": Imu([0, 0, 0], [0, 0, 0])}},
            odom=Pose(1, 2, 3, 0, 0, 0, 1),
        ),
    )
    parsed = Observation.from_dict(observation.to_dict())
    assert parsed.module == SLAM
    assert isinstance(parsed.data, SlamData)
    assert np.array_equal(parsed.data.sensors["lidar"]["front"], lidar)
    assert parsed.data.odom.y == 2.0


def test_nav_observation_roundtrip():
    observation = Observation(
        0.3,
        NAV,
        NavData(robot_pose=Pose(0, 0, 0, 0, 0, 0, 1), goal=Pose(5, 0, 0, 0, 0, 0, 1), obstacles=[(3.0, 1.5, 0.5)]),
    )
    parsed = Observation.from_dict(observation.to_dict())
    assert isinstance(parsed.data, NavData)
    assert parsed.data.obstacles == [(3.0, 1.5, 0.5)]


def test_action_roundtrip():
    action = Action(NAV, NavAction(v=0.5, w=0.1))
    parsed = Action.from_dict(action.to_dict())
    assert isinstance(parsed.data, NavAction)
    assert parsed.data.v == 0.5


def test_result_roundtrip():
    result = Result(SLAM, StampedPose(1.0, Pose(1, 2, 3, 0, 0, 0, 1)))
    parsed = Result.from_dict(result.to_dict())
    assert isinstance(parsed.data, StampedPose)
    assert parsed.data.pose.z == 3.0


def test_unknown_module_preserved():
    observation = Observation(0.2, "manip", {"goal": [1, 2]})
    parsed = Observation.from_dict(observation.to_dict())
    assert parsed.module == "manip"
    assert parsed.data == {"goal": [1, 2]}
