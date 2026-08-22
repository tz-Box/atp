"""测试内容：闭环 ctrl.invp 端到端 —— 进程内会话 + 常驻 Service 服务通路（server 闭环装配）。
期望输出：PD 控制器撑满 max_steps、稳态误差达标，passed=True；comm_health 双侧留痕齐全。
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path

import tzcomm

from autotest.eval import ClosedLoopSession
from autotest.protocol import messages as msg
from autotest.protocol import topics
from autotest.protocol.schema import decode_observation, encode_action
from autotest.registry import load_plugin
from autotest.sdk import SutBase
from autotest.server import AutotestService

invp = load_plugin("ctrl.invp")

_ROOT = Path(__file__).resolve().parent.parent.parent
_MANIFEST = str(_ROOT / "examples" / "invp_sut.scenario.yaml")
_POLL_INTERVAL = 0.2


class _PdPendulum(SutBase):
    module = "ctrl.invp"

    def on_step(self, observation):
        o = decode_observation(observation["data"])  # InvpObs
        force = 30.0 * o.theta + 5.0 * o.theta_dot + 1.0 * o.x + 2.0 * o.x_dot
        return msg.Action(
            "ctrl.invp",
            encode_action("ctrl.invp.InvpAction", invp.InvpAction(force=force)),
        )


def test_closed_loop_end_to_end(daemon):
    """进程内通路：ClosedLoopSession + PD SUT，两档扰动均稳。"""
    scenarios = invp.basic_invp_scenarios()
    session_id = f"invp-func-{uuid.uuid4().hex[:8]}"
    session = ClosedLoopSession(invp.InvpSimWorld(scenarios), invp.InvpChecker(), session_id=session_id, name="invp-service")
    sut = _PdPendulum("invp-sut", session_id)
    try:
        results = session.run(list(scenarios.keys()), checker_config={"settle_threshold": 0.02})
    finally:
        session.close()
        sut.close()

    assert len(results) == 2
    for r in results:
        assert r.score is not None and r.score.passed, (r.testcase_id, r.score)
        assert r.score.metrics["survived"] == 1.0
        assert r.score.metrics["settle_error"] <= 0.02
    # SDK 自动附 SUT 侧 comm 自统计（A3 仪器化覆盖闭环通路）
    assert session.sut_final is not None
    assert session.sut_final.get("comm", {}).get("msgs", 0) > 0


def _submit(manifest: str) -> dict:
    """提交评测并轮询到完成，返回最终状态。"""
    node = tzcomm.Node("test-client")
    try:
        ctl = node.create_service_client(topics.control_service())
        if not ctl.wait_for_server(timeout=5):
            raise RuntimeError("control 服务不可用")
        resp = ctl.call({"manifest": manifest, "clock_rate": 0}, timeout=30)
        if "error" in resp:
            return resp
        status = node.create_service_client(topics.job_status_service())
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            state = status.call({"job_id": resp["job_id"]}, timeout=30)
            if state.get("error"):
                return state
            if state["status"] == "done":
                return state
            time.sleep(_POLL_INTERVAL)
        return {"error": "评测超时"}
    finally:
        node.close()


def test_service_closed_loop_run(daemon):
    """服务通路：server 按 world.closed_loop 装配 ClosedLoopSession，
    真实 manifest（相对 scenario 路径 + launch 工作目录语义）端到端跑通。"""
    service = AutotestService(name="test-service")
    threading.Thread(target=service.spin, daemon=True).start()
    time.sleep(0.5)
    try:
        resp = _submit(_MANIFEST)
    finally:
        service.close()

    assert not resp.get("error"), resp
    results = resp["results"]
    assert len(results) == 2, results
    for r in results:
        assert r["passed"] is True, r
        assert r["metrics"]["survived"] == 1.0
        assert r["metrics"]["settle_error"] <= 0.02

    # comm_health：闭环通路双侧留痕（Service 侧 action 接收 + SUT 侧 obs 接收）
    health = resp.get("comm_health") or {}
    assert health.get("service") and health["service"]["msgs"] > 0, health
    assert health.get("sut") and health["sut"]["msgs"] > 0, health

    # 本机记录：artifacts/{job_id}/ 下有 report.json 与 session.log
    artifact_dir = Path(os.environ["AUTOTEST_ARTIFACTS_DIR"]) / resp["job_id"]
    report = json.loads((artifact_dir / "report.json").read_text(encoding="utf-8"))
    assert report["error"] is None
    assert len(report["results"]) == 2
