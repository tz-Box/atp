"""测试内容：开环 SLAM 端到端（Service + TzComm + SUT + checker）。
期望输出：所有 testcase 通过，ATE/RPE≈0（SUT 回声 odom，即真值）。
"""
import uuid

from autotest.eval import Runner
from autotest.protocol.schema import SLAM, Result, StampedPose
from autotest.sdk import SutBase
from autotest.world import DatasetWorld
from modules.slam import SlamChecker, SyntheticSlamDataset


class _EchoSlam(SutBase):
    module = "slam"

    def on_step(self, observation):
        slam = observation.data  # SlamData
        if slam.odom is None:
            return None
        return Result(SLAM, StampedPose(timestamp=observation.timestamp, pose=slam.odom))


def test_openloop_end_to_end(daemon):
    session_id = f"func-{uuid.uuid4().hex[:8]}"
    dataset = SyntheticSlamDataset(n_testcases=2, n_steps=20, seed=7)
    world = DatasetWorld(dataset)
    checker = SlamChecker()

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
