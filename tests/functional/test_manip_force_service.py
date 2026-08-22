"""测试内容：manip.force 服务通路 —— server 按 world.closed_loop 装配 ClosedLoopSession，
真实 manifest（相对 scenario 路径 + launch 工作目录语义）端到端跑通。
期望输出：两 testcase 均稳态力跟踪 passed；comm_health 双侧留痕齐全；report.json 落盘。
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import tzcomm

from autotest.protocol import topics
from autotest.server import AutotestService

_ROOT = Path(__file__).resolve().parent.parent.parent
_MANIFEST = str(_ROOT / "examples" / "manip_sut.scenario.yaml")
_POLL_INTERVAL = 0.2


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
        assert r["metrics"]["settle_error"] <= 0.5

    # comm_health：闭环通路双侧留痕（Service 侧 action 接收 + SUT 侧 obs 接收）
    health = resp.get("comm_health") or {}
    assert health.get("service") and health["service"]["msgs"] > 0, health
    assert health.get("sut") and health["sut"]["msgs"] > 0, health

    # 本机记录：artifacts/{job_id}/ 下有 report.json 与 session.log
    artifact_dir = Path(os.environ["AUTOTEST_ARTIFACTS_DIR"]) / resp["job_id"]
    report = json.loads((artifact_dir / "report.json").read_text(encoding="utf-8"))
    assert report["error"] is None
    assert len(report["results"]) == 2
