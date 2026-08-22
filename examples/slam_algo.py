"""参考黑盒 SLAM 算法进程入口：回声里程计（无里程计时回占位位姿）。

进程由评测服务拉起，会话身份由环境变量 AUTOTEST_SESSION/TOPICS 注入。
"""

from autotest.protocol import messages as msg
from autotest.protocol.schema import Pose, StampedPose, decode_observation, encode_result
from autotest.registry import load_plugin
from autotest.sdk import SutBase

load_plugin("pipe.slam")  # 注册 pipe.slam schema 编解码器


class EchoSlam(SutBase):
    module = "pipe.slam"

    def on_reset(self, testcase_meta):
        pass

    def on_step(self, observation):
        data = decode_observation(observation["data"])  # SlamData
        pose = data.odom if data.odom is not None else Pose(0, 0, 0, 0, 0, 0, 1)
        return msg.Result(
            "pipe.slam",
            encode_result(
                "pipe.slam.StampedPose",
                StampedPose(timestamp=observation["timestamp"], pose=pose),
            ),
        )


def main() -> None:
    sut = EchoSlam("echo-slam")
    try:
        sut.spin()
    finally:
        sut.close()


if __name__ == "__main__":
    main()
