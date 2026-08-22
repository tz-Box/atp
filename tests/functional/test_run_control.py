"""测试内容：暂停/单步/恢复（M-B3）全链路 —— 开环 Runner、闭环 ClosedLoopSession、常驻 Service 服务通路。
期望输出：暂停后帧数冻结；step 精确放行 N 帧；resume 后跑完且结果 passed；
服务通路 job status 暴露 run_state/frames，调试命令错误分支（未知 job/已结束/未暂停 step）可观测。
"""
from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path

import tzcomm

from autotest.eval import ClosedLoopSession, Runner
from autotest.eval.run_control import RunControl
from autotest.protocol import messages as msg
from autotest.protocol import topics
from autotest.protocol.schema import StampedPose, decode_observation, encode_action, encode_result
from autotest.registry import load_plugin
from autotest.sdk import SutBase
from autotest.server import AutotestService
from autotest.world import DatasetWorld

invp = load_plugin("ctrl.invp")
slam = load_plugin("pipe.slam")

_ROOT = Path(__file__).resolve().parent.parent.parent
_MANIFEST = str(_ROOT / "examples" / "invp_sut.scenario.yaml")
_POLL = 0.1


class _PdPendulum(SutBase):
    module = "ctrl.invp"

    def on_step(self, observation):
        o = decode_observation(observation["data"])  # InvpObs
        force = 30.0 * o.theta + 5.0 * o.theta_dot + 1.0 * o.x + 2.0 * o.x_dot
        return msg.Action(
            "ctrl.invp",
            encode_action("ctrl.invp.InvpAction", invp.InvpAction(force=force)),
        )


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


def _wait_frames(control: RunControl, target: int, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if control.frames_sent >= target:
            return
        time.sleep(0.02)
    raise TimeoutError(f"frames 未达 {target}（当前 {control.frames_sent}）")


def _assert_frozen(control: RunControl, settle: float = 0.4) -> int:
    """暂停生效后帧数冻结：间隔 settle 两次读数一致，返回冻结帧数。"""
    time.sleep(settle)
    frozen = control.frames_sent
    time.sleep(settle)
    assert control.frames_sent == frozen, f"暂停后帧数未冻结: {frozen} → {control.frames_sent}"
    return frozen


def test_openloop_pause_step_resume(daemon):
    """开环通路：Runner + 实时复现（clock_rate=1.0，0.1s/帧），暂停→单步 5 帧→恢复。"""
    dataset = slam.SyntheticSlamDataset(n_testcases=1, n_steps=60, seed=7)  # dt=0.1 → 6s 窗口
    world = DatasetWorld(dataset)
    control = RunControl()
    session_id = f"ctl-open-{uuid.uuid4().hex[:8]}"
    runner = Runner(world, slam.SlamChecker(), session_id=session_id, name="ctl-open-service", control=control)
    sut = _EchoSlam("ctl-open-sut", session_id)

    results = []
    t = threading.Thread(
        target=lambda: results.extend(
            runner.run(dataset.testcases, checker_config={"ate_threshold": 0.2, "rpe_threshold": 0.1}, clock_rate=1.0)
        ),
        daemon=True,
    )
    t.start()
    try:
        _wait_frames(control, 10)  # 进入推流（约 1s）
        control.pause()
        frozen = _assert_frozen(control)
        assert control.state == "paused"

        assert control.step(5) is True  # 单步 5 帧（实时节奏 0.5s 放行完）
        _wait_frames(control, frozen + 5, timeout=10)
        assert _assert_frozen(control) == frozen + 5

        control.resume()
        assert t.join(timeout=30) is None and not t.is_alive()
    finally:
        runner.close()
        sut.close()

    assert len(results) == 1
    assert results[0].score is not None and results[0].score.passed
    assert results[0].score.metrics["ate_rmse"] < 1e-3
    assert control.frames_sent == 60  # 数据帧全量过闸（终止帧直达不计）


def test_closed_loop_pause_step_resume(daemon):
    """闭环通路：ClosedLoopSession + 大 max_steps 场景，暂停→单步 10 帧→恢复后跑满通过。"""
    scenarios = {"long_push": invp.InvpScenario(theta0=0.05, max_steps=4000)}
    control = RunControl()
    session_id = f"ctl-closed-{uuid.uuid4().hex[:8]}"
    session = ClosedLoopSession(
        invp.InvpSimWorld(scenarios), invp.InvpChecker(),
        session_id=session_id, name="ctl-closed-service", control=control,
    )
    sut = _PdPendulum("ctl-closed-sut", session_id)

    results = []
    t = threading.Thread(
        target=lambda: results.extend(session.run(["long_push"], checker_config={"settle_threshold": 0.02})),
        daemon=True,
    )
    t.start()
    try:
        _wait_frames(control, 30)  # 握手完成后进入交互
        control.pause()
        frozen = _assert_frozen(control)

        assert control.step(10) is True
        _wait_frames(control, frozen + 10, timeout=10)
        assert _assert_frozen(control) == frozen + 10

        control.resume()
        assert t.join(timeout=60) is None and not t.is_alive()
    finally:
        session.close()
        sut.close()

    assert len(results) == 1
    assert results[0].score is not None and results[0].score.passed
    assert results[0].score.metrics["survived"] == 1.0
    assert control.frames_sent == 4000  # 每 action 一帧，跑满 max_steps


# ---- 服务通路：control 调试命令 + job status 暴露 ----

_LONG_SCENARIO = """\
body: invp_sim
dataset:
  type: ctrl.invp.sim
  config:
    testcases:
      long_push: { theta0: 0.05, max_steps: 8000 }
checker: ctrl.invp.upright
checker_config: { settle_threshold: 0.02 }
"""


def _job_state(status, job_id: str) -> dict:
    state = status.call({"job_id": job_id}, timeout=30)
    assert not state.get("error"), state
    return state


def _wait_job_frames(status, job_id: str, target: int, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _job_state(status, job_id)["frames"] >= target:
            return
        time.sleep(_POLL)
    raise TimeoutError(f"job frames 未达 {target}")


def test_service_debug_commands(daemon, tmp_path):
    """服务通路：control 服务 pause/step/resume + job status run_state/frames + 错误分支。"""
    scenario_path = tmp_path / "long_invp.yaml"
    scenario_path.write_text(_LONG_SCENARIO, encoding="utf-8")

    service = AutotestService(name="test-service")
    threading.Thread(target=service.spin, daemon=True).start()
    time.sleep(0.5)
    node = tzcomm.Node("test-client")
    try:
        ctl = node.create_service_client(topics.control_service())
        if not ctl.wait_for_server(timeout=5):
            raise RuntimeError("control 服务不可用")
        status = node.create_service_client(topics.job_status_service())

        # 错误分支：未知 job_id
        resp = ctl.call({"command": "pause", "job_id": "autotest-deadbeef"}, timeout=30)
        assert "未知 job_id" in resp.get("error", "")

        # 提交长场景评测（大 max_steps 保证调试窗口）
        resp = ctl.call({"manifest": _MANIFEST, "scenario": str(scenario_path)}, timeout=30)
        assert "error" not in resp, resp
        job_id = resp["job_id"]

        _wait_job_frames(status, job_id, 50)
        state = _job_state(status, job_id)
        assert state["status"] == "running" and state["run_state"] == "running"

        # 暂停：run_state 翻转为 paused，帧数冻结
        resp = ctl.call({"command": "pause", "job_id": job_id}, timeout=30)
        assert resp.get("ok") and resp["run_state"] == "paused", resp
        time.sleep(0.4)
        frozen = _job_state(status, job_id)["frames"]
        time.sleep(0.4)
        assert _job_state(status, job_id)["frames"] == frozen

        # 单步 10 帧：精确放行后再次冻结
        resp = ctl.call({"command": "step", "job_id": job_id, "n": 10}, timeout=30)
        assert resp.get("ok"), resp
        _wait_job_frames(status, job_id, frozen + 10, timeout=10)
        time.sleep(0.4)
        assert _job_state(status, job_id)["frames"] == frozen + 10

        # 恢复：全速跑完
        resp = ctl.call({"command": "resume", "job_id": job_id}, timeout=30)
        assert resp.get("ok") and resp["run_state"] == "running", resp

        # 错误分支：非暂停状态 step 无效
        resp = ctl.call({"command": "step", "job_id": job_id, "n": 1}, timeout=30)
        assert "非暂停状态" in resp.get("error", ""), resp

        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            state = _job_state(status, job_id)
            if state["status"] == "done":
                break
            time.sleep(_POLL)
        assert state["status"] == "done", state
        assert not state.get("error"), state
        results = state["results"]
        assert len(results) == 1 and results[0]["passed"] is True, results
        assert results[0]["metrics"]["survived"] == 1.0

        # 错误分支：评测已结束后调试命令拒绝
        resp = ctl.call({"command": "pause", "job_id": job_id}, timeout=30)
        assert "已结束" in resp.get("error", ""), resp
    finally:
        node.close()
        service.close()
