"""测试用被拉起算法：回声里程计（无里程计时不输出）。

进程由 Service 经 Launcher 启动，会话身份来自环境变量
AUTOTEST_SESSION/AUTOTEST_TOPICS（由 Service 注入）。
"""
from __future__ import annotations

from autotest.protocol import messages as msg
from autotest.protocol.schema import StampedPose, decode_observation, encode_result
from autotest.registry import load_plugin
from autotest.sdk import SutBase

load_plugin("pipe.slam")  # 注册 pipe.slam schema 编解码器


class EchoSlam(SutBase):
    module = "pipe.slam"

    def on_step(self, observation):
        data = decode_observation(observation["data"])  # SlamData
        if data.odom is None:
            return None
        return msg.Result(
            "pipe.slam",
            encode_result(
                "pipe.slam.StampedPose",
                StampedPose(timestamp=observation["timestamp"], pose=data.odom),
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
