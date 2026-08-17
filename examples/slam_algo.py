"""参考黑盒 SLAM 算法进程入口：回声里程计（无里程计时回占位位姿）。

进程由评测服务拉起，会话身份由环境变量 AUTOTEST_SESSION/TOPICS 注入。
"""

from autotest.protocol.schema import SLAM, Pose, Result, StampedPose
from autotest.sdk import SutBase
from autotest.protocol.data.slam import SlamData  # noqa: F401  触发 data 注册


class EchoSlam(SutBase):
    module = "slam"

    def on_reset(self, testcase_meta):
        pass

    def on_step(self, observation):
        slam = observation.data  # SlamData
        pose = slam.odom if slam.odom is not None else Pose(0, 0, 0, 0, 0, 0, 1)
        return Result(SLAM, StampedPose(observation.timestamp, pose))


def main() -> None:
    sut = EchoSlam("echo-slam")
    try:
        sut.spin()
    finally:
        sut.close()


if __name__ == "__main__":
    main()
