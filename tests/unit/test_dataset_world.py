"""测试内容：DatasetWorld 开环回放 reset/step/done/GT。
期望输出：首帧正确、顺序推进、数据耗尽 done=True、GT 长度正确、未 reset 先 step 报错。
"""
import pytest

from autotest.protocol.schema import decode_ground_truth, decode_observation
from autotest.registry import load_plugin
from autotest.world import DatasetWorld

slam = load_plugin("pipe.slam")
SyntheticSlamDataset = slam.SyntheticSlamDataset


def test_openloop_replay():
    dataset = SyntheticSlamDataset(n_testcases=1, n_steps=5, seed=1)
    world = DatasetWorld(dataset)
    first = world.reset("tc0")
    assert first["timestamp"] == 0.0
    assert first["module"] == "pipe.slam"
    assert decode_observation(first["data"]).odom is not None

    count = 1
    while True:
        observation, done, _ = world.step()
        if done:
            break
        count += 1
        assert observation is not None

    assert count == 5
    gt = world.get_ground_truth()
    assert len(decode_ground_truth(gt)["trajectory"]) == 5
    world.close()


def test_step_before_reset_raises():
    world = DatasetWorld(SyntheticSlamDataset(n_testcases=1, n_steps=2))
    with pytest.raises(RuntimeError):
        world.step()
    world.close()
