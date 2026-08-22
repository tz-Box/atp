"""测试内容：开环 SLAM 端到端（Service + TzComm + SUT + checker）。
期望输出：所有 testcase 通过，ATE/RPE≈0（SUT 回声 odom，即真值）。
"""
import uuid

from autotest.eval import Runner
from autotest.protocol import messages as msg
from autotest.protocol.schema import StampedPose, decode_observation, encode_result
from autotest.registry import load_plugin
from autotest.sdk import SutBase
from autotest.world import DatasetWorld

slam = load_plugin("pipe.slam")


class _EchoSlam(SutBase):
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


def test_openloop_end_to_end(daemon):
    session_id = f"func-{uuid.uuid4().hex[:8]}"
    dataset = slam.SyntheticSlamDataset(n_testcases=2, n_steps=20, seed=7)
    world = DatasetWorld(dataset)
    checker = slam.SlamChecker()

    runner = Runner(world, checker, session_id=session_id, name="func-service")
    sut = _EchoSlam("func-sut", session_id)
    try:
        results = runner.run(
            dataset.testcases,
            checker_config={"ate_threshold": 0.2, "rpe_threshold": 0.1},
            clock_rate=0,  # 全速：测试不按原始帧间隔等待
        )
    finally:
        runner.close()
        sut.close()

    assert len(results) == 2
    for result in results:
        assert result.score is not None
        assert result.score.passed
        assert result.score.metrics["ate_rmse"] < 1e-3
        assert result.score.metrics["rpe_rmse"] < 1e-3
