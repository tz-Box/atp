"""manip.force 插件单元测试：schema 编解码 / 仿真动力学 / checker 判分。"""
from autotest.protocol.schema import (
    decode_action,
    decode_observation,
    encode_action,
    encode_ground_truth,
    encode_observation,
    make_observation,
)
from autotest.registry import load_plugin

mforce = load_plugin("manip.force")


def test_schema_roundtrip():
    obs = mforce.ManipObs(x=0.48, x_dot=0.1, f_contact=1.5, target_force=5.0)
    env = encode_observation("manip.force.ManipObs", obs)
    assert env["schema"] == "manip.force.ManipObs"
    assert decode_observation(env) == obs

    act = mforce.ManipAction(force=3.5)
    assert decode_action(encode_action("manip.force.ManipAction", act)).force == 3.5


def test_sim_force_exceeded():
    """满推力恒推：接触力必超 f_limit（force_exceeded），且远早于 max_steps。"""
    world = mforce.ForceSimWorld({"t": mforce.ForceScenario(target_force=5.0)})
    obs = world.reset("t")
    full = {
        "module": "manip.force",
        "data": encode_action("manip.force.ManipAction", mforce.ManipAction(force=50.0)),
    }
    done, steps, info = False, 0, {}
    while not done:
        obs, done, info = world.step(full)
        steps += 1
    assert info["reason"] == "force_exceeded"
    assert steps < 5000
    assert decode_observation(obs["data"]).f_contact > 25.0
    world.close()


def test_sim_free_space_no_contact():
    """零力输入：原地不动，始终无接触（f=0），撑满 max_steps 判 survived。"""
    world = mforce.ForceSimWorld({"t": mforce.ForceScenario(target_force=5.0, max_steps=100)})
    obs = world.reset("t")
    zero = {
        "module": "manip.force",
        "data": encode_action("manip.force.ManipAction", mforce.ManipAction(force=0.0)),
    }
    done, info = False, {}
    while not done:
        obs, done, info = world.step(zero)
    assert info["reason"] == "survived"
    o = decode_observation(obs["data"])
    assert o.f_contact == 0.0
    assert o.x == 0.48  # 未接触、零推力 → 静止
    world.close()


def test_sim_gt_envelope():
    """GT 信封携带任务参数（checker 判存活/稳态力跟踪的依据）。"""
    world = mforce.ForceSimWorld({"t": mforce.ForceScenario(target_force=8.0, max_steps=300)})
    world.reset("t")
    gt = world.get_ground_truth()
    assert gt["schema"] == "manip.force.ForceTask"
    assert gt["data"]["max_steps"] == 300
    assert gt["data"]["target_force"] == 8.0
    world.close()


def _records(forces, target=5.0):
    return [
        make_observation(
            "manip.force", i * 0.001,
            encode_observation("manip.force.ManipObs",
                               mforce.ManipObs(x=0.5, x_dot=0.0, f_contact=f, target_force=target)),
        )
        for i, f in enumerate(forces)
    ]


def _gt(max_steps=100, target=5.0):
    return encode_ground_truth("manip.force.ForceTask", {
        "dt": 0.001, "max_steps": max_steps, "target_force": target,
        "x_wall": 0.5, "f_limit": 25.0,
    })


def test_checker_survived_passed():
    score = mforce.ForceChecker().evaluate(_records([5.0] * 101), _gt(100), {"settle_threshold": 0.5})
    assert score.passed
    assert score.metrics["survived"] == 1.0
    assert score.metrics["settle_error"] == 0.0
    assert score.metrics["tracking_ratio"] == 1.0
    assert score.metrics["overshoot"] == 0.0
    assert score.metrics["execution_time"] == 0.1


def test_checker_crush_failed():
    # 执行 2 步即压坏终止（远少于 max_steps=100）
    score = mforce.ForceChecker().evaluate(_records([0.0, 10.0, 30.0]), _gt(100), None)
    assert not score.passed
    assert score.metrics["survived"] == 0.0
    assert score.metrics["peak_force"] == 30.0
    assert score.metrics["overshoot"] == 25.0


def test_checker_settle_error_failed():
    # 撑满 max_steps 但稳态力误差超阈值（力偏低 1N）
    score = mforce.ForceChecker().evaluate(_records([4.0] * 101), _gt(100), {"settle_threshold": 0.5})
    assert not score.passed
    assert score.metrics["survived"] == 1.0
    assert abs(score.metrics["settle_error"] - 1.0) < 1e-9


def test_sim_from_config():
    """场景 config 装配路径（register_dataset 工厂）。"""
    world = mforce.ForceSimWorld.from_config({
        "testcases": {"a": {"target_force": 3.0}, "b": {"target_force": 8.0, "max_steps": 100}},
        "physics": {"f_limit": 20.0},
    })
    assert world.testcases == ["a", "b"]
    world.reset("b")
    gt = world.get_ground_truth()
    assert gt["data"]["max_steps"] == 100
    assert gt["data"]["f_limit"] == 20.0
    world.close()
