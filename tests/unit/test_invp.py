"""ctrl.invp 插件单元测试：schema 编解码 / 仿真动力学 / checker 判分。"""
from autotest.protocol.schema import (
    decode_action,
    decode_observation,
    encode_action,
    encode_ground_truth,
    encode_observation,
    make_observation,
)
from autotest.registry import load_plugin

invp = load_plugin("ctrl.invp")


def test_schema_roundtrip():
    obs = invp.InvpObs(x=0.1, x_dot=-0.2, theta=0.05, theta_dot=0.01)
    env = encode_observation("ctrl.invp.InvpObs", obs)
    assert env["schema"] == "ctrl.invp.InvpObs"
    assert decode_observation(env) == obs

    act = invp.InvpAction(force=3.5)
    assert decode_action(encode_action("ctrl.invp.InvpAction", act)).force == 3.5


def test_sim_free_fall():
    """零力输入：摆必倒（fell），且远早于 max_steps。"""
    world = invp.InvpSimWorld({"t": invp.InvpScenario(theta0=0.05)})
    obs = world.reset("t")
    zero = {
        "module": "ctrl.invp",
        "data": encode_action("ctrl.invp.InvpAction", invp.InvpAction(force=0.0)),
    }
    done, steps, info = False, 0, {}
    while not done:
        obs, done, info = world.step(zero)
        steps += 1
    assert info["reason"] == "fell"
    assert steps < 500
    assert abs(decode_observation(obs["data"]).theta) > 0.2095
    world.close()


def test_sim_gt_envelope():
    """GT 信封携带任务参数（checker 判存活/稳态的依据）。"""
    world = invp.InvpSimWorld({"t": invp.InvpScenario(theta0=0.05, max_steps=300)})
    world.reset("t")
    gt = world.get_ground_truth()
    assert gt["schema"] == "ctrl.invp.InvpTask"
    assert gt["data"]["max_steps"] == 300
    assert gt["data"]["theta_limit"] == 0.2095
    world.close()


def _records(thetas):
    return [
        make_observation(
            "ctrl.invp", i * 0.02,
            encode_observation("ctrl.invp.InvpObs", invp.InvpObs(x=0.0, x_dot=0.0, theta=t, theta_dot=0.0)),
        )
        for i, t in enumerate(thetas)
    ]


def _gt(max_steps=100):
    return encode_ground_truth("ctrl.invp.InvpTask", {
        "dt": 0.02, "max_steps": max_steps, "theta_limit": 0.2095, "x_limit": 2.4,
    })


def test_checker_survived_passed():
    score = invp.InvpChecker().evaluate(_records([0.0] * 101), _gt(100), {"settle_threshold": 0.02})
    assert score.passed
    assert score.metrics["survived"] == 1.0
    assert score.metrics["settle_error"] == 0.0
    assert score.metrics["upright_ratio"] == 1.0
    assert score.metrics["survival_time"] == 2.0


def test_checker_fell_failed():
    # 执行 2 步即倒（远少于 max_steps=100）
    score = invp.InvpChecker().evaluate(_records([0.0, 0.1, 0.25]), _gt(100), None)
    assert not score.passed
    assert score.metrics["survived"] == 0.0
    assert score.metrics["max_abs_theta"] == 0.25


def test_checker_settle_error_failed():
    # 撑满 max_steps 但稳态误差超阈值
    score = invp.InvpChecker().evaluate(_records([0.1] * 101), _gt(100), {"settle_threshold": 0.02})
    assert not score.passed
    assert score.metrics["survived"] == 1.0
    assert abs(score.metrics["settle_error"] - 0.1) < 1e-9


def test_sim_from_config():
    """场景 config 装配路径（register_dataset 工厂）。"""
    world = invp.InvpSimWorld.from_config({
        "testcases": {"a": {"theta0": 0.03}, "b": {"theta0": 0.08, "max_steps": 100}},
        "physics": {"force_limit": 8.0},
    })
    assert world.testcases == ["a", "b"]
    world.reset("b")
    assert world.get_ground_truth()["data"]["max_steps"] == 100
    world.close()
