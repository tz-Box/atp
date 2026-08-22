"""测试内容：通信健康链路——commcheck 真实回环、Runner/SDK 双侧统计采集、Service 状态与留痕。"""
import json
import os
import uuid
from pathlib import Path

from autotest import commcheck
from autotest.eval import Runner
from autotest.protocol import messages as msg
from autotest.protocol.schema import StampedPose, decode_observation, encode_result
from autotest.registry import load_plugin
from autotest.sdk import SutBase
from autotest.world import DatasetWorld

from .test_service import _start_service, _submit, _write_manifest

slam = load_plugin("pipe.slam")


def test_commcheck_run_all_ok(daemon):
    """真实 daemon 下三级预检全部通过，pubsub 回环收齐。"""
    report = commcheck.run_checks(messages=30, timeout=5.0)
    assert report["ok"] is True, report
    by_name = {c["name"]: c for c in report["checks"]}
    assert by_name["pubsub"]["detail"]["recv"] == 30
    assert by_name["pubsub"]["detail"]["loss"] == 0
    assert by_name["service"]["detail"]["rtt_ms"] >= 0


class _EchoSlam(SutBase):
    module = "pipe.slam"

    def on_step(self, observation):
        data = decode_observation(observation["data"])
        if data.odom is None:
            return None
        return msg.Result(
            "pipe.slam",
            encode_result(
                "pipe.slam.StampedPose",
                StampedPose(timestamp=observation["timestamp"], pose=data.odom),
            ),
        )


def test_runner_captures_bilateral_comm_stats(daemon):
    """Runner：sut_final 带回 SDK 自动附的 SUT 侧 comm 统计；Service 侧快照结构完整。"""
    session_id = f"func-{uuid.uuid4().hex[:8]}"
    dataset = slam.SyntheticSlamDataset(n_testcases=1, n_steps=20, seed=7)
    runner = Runner(DatasetWorld(dataset), slam.SlamChecker(), session_id=session_id, name="func-service")
    sut = _EchoSlam("func-sut", session_id)
    try:
        results = runner.run(dataset.testcases, clock_rate=0)
    finally:
        service_stats = runner.comm_snapshot()
        runner.close()
        sut.close()

    assert len(results) == 1
    # SUT 侧：SDK 在 TERMINATE 时自动附 comm（obs 接收统计）
    assert runner.sut_final is not None
    sut_comm = runner.sut_final.get("comm")
    assert sut_comm is not None, runner.sut_final
    assert sut_comm["msgs"] >= 20  # obs 推流至少 20 帧
    assert sut_comm["loss_rate"] == 0.0
    # Service 侧：result 接收统计
    assert service_stats["side"] == "service"
    assert service_stats["msgs"] >= 20
    assert service_stats["loss_rate"] == 0.0
    # 汇总：双侧无损 → 无告警
    health = commcheck.build_health(service_stats, sut_comm)
    assert health["warnings"] == []


def test_service_status_and_report_carry_comm_health(daemon, tmp_path):
    """Service 全链路：job status 响应与 artifacts/report.json 均带 comm_health。"""
    manifest_path = _write_manifest(tmp_path)
    service = _start_service()
    try:
        resp = _submit(manifest_path)
    finally:
        service.close()

    assert not resp.get("error"), resp
    health = resp.get("comm_health")
    assert health, resp
    assert health["service"]["side"] == "service"
    assert health["sut"] is not None  # _echo_algo 走 SDK，final 回传 comm
    assert health["warnings"] == []

    report_path = Path(os.environ["AUTOTEST_ARTIFACTS_DIR"]) / resp["job_id"] / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["comm_health"]["service"]["msgs"] >= 0
    assert report["comm_health"]["warnings"] == []
