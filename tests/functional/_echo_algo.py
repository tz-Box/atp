"""测试用被拉起算法：回声里程计（无里程计时不输出）。

进程由 Service 经 Launcher 启动，会话身份来自环境变量
AUTOTEST_SESSION/AUTOTEST_TOPICS（由 Service 注入）。
"""
from __future__ import annotations

from autotest.protocol.data.slam import SlamData  # noqa: F401  触发 data 注册
from autotest.protocol.schema import SLAM, Result, StampedPose
from autotest.sdk import SutBase


class EchoSlam(SutBase):
    module = "slam"

    def on_step(self, observation):
        slam = observation.data  # SlamData
        if slam.odom is None:
            return None
        return Result(SLAM, StampedPose(timestamp=observation.timestamp, pose=slam.odom))


def main() -> None:
    sut = EchoSlam("echo-slam")
    try:
        sut.spin()
    finally:
        sut.close()


if __name__ == "__main__":
    main()
