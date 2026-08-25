"""测试内容：常驻 Service（会话池）+ 算法 manifest（scenario.yaml）+ 环境变量注入 端到端。

流程：
    1) 算法仓库根目录有 scenario.yaml（静态声明 launch/consumes/scenario）；
    2) Client 经 autotest/control 提交 manifest，立即拿到 job_id（异步）；
    3) Client 经 autotest/job/status 轮询到完成；
    4) Service 读 manifest → 加载场景 → 分配会话 → Launcher 拉起算法
       （注入 AUTOTEST_SESSION/AUTOTEST_TOPICS）→ 会话握手 → 推流 → 打分 → 返回结果。
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

import yaml
import tzcomm

from autotest.client import main as cli_main
from autotest.server import AutotestService
from autotest.protocol import topics

_ROOT = Path(__file__).resolve().parent.parent.parent
_ALGO = str(Path(__file__).resolve().parent / "_echo_algo.py")
_SCENARIO = str(_ROOT / "scenarios" / "synthetic_slam.yaml")

_POLL_INTERVAL = 0.2


def _write_manifest(tmp_path: Path) -> str:
    manifest = {
        "launch": f"{sys.executable} {_ALGO}",
        "consumes": ["pipe.slam.SlamObs"],
        "scenario": _SCENARIO,
        "required_sensors": {"lidar": ["front"]},
    }
    manifest_path = tmp_path / "scenario.yaml"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    return str(manifest_path)


def _submit(manifest: str, checker: str = "") -> dict:
    """提交评测并轮询到完成，返回最终状态（含 job_id/results/error）。"""
    node = tzcomm.Node("test-client")
    try:
        ctl = node.create_service_client(topics.control_service())
        if not ctl.wait_for_server(timeout=5):
            raise RuntimeError("control 服务不可用")
        request = {"manifest": manifest, "clock_rate": 0}  # 全速，避免测试按实时帧间隔等待
        if checker:
            request["checker"] = checker  # 评测方覆盖场景 checker（交叉测试矩阵路径）
        resp = ctl.call(request, timeout=30)
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


def _start_service() -> AutotestService:
    service = AutotestService(name="test-service")
    threading.Thread(target=service.spin, daemon=True).start()
    time.sleep(0.5)
    return service


def test_service_manifest_run(daemon, tmp_path):
    manifest_path = _write_manifest(tmp_path)
    service = _start_service()
    try:
        resp = _submit(manifest_path)
    finally:
        service.close()

    assert not resp.get("error"), resp
    results = resp["results"]
    assert len(results) == 2, results
    for r in results:
        assert r["passed"] is True
        assert r["metrics"]["ate_rmse"] < 1e-3
        assert r["metrics"]["rpe_rmse"] < 1e-3

    # 本机记录：artifacts/{job_id}/ 下有 report.json 与 session.log
    artifact_dir = Path(os.environ["AUTOTEST_ARTIFACTS_DIR"]) / resp["job_id"]
    assert (artifact_dir / "report.json").is_file()
    assert (artifact_dir / "session.log").is_file()
    report = json.loads((artifact_dir / "report.json").read_text(encoding="utf-8"))
    assert len(report["results"]) == 2
    assert report["error"] is None
    assert "testcase" in (artifact_dir / "session.log").read_text(encoding="utf-8")


def test_service_rejects_unsatisfied_consumes(daemon, tmp_path):
    """装配校验 fail fast：覆盖的 checker 声明 consumes 不被 dataset produces 满足 → 报错。"""
    manifest_path = _write_manifest(tmp_path)
    service = _start_service()
    try:
        resp = _submit(manifest_path, checker="nav2d.default")  # consumes nav2d.NavObs
    finally:
        service.close()

    assert "error" in resp
    assert "场景装配失败" in resp["error"]


def test_service_rejects_missing_manifest(daemon, tmp_path):
    service = _start_service()
    try:
        resp = _submit(str(tmp_path / "not_exist.yaml"))
    finally:
        service.close()

    assert "error" in resp
    assert "FileNotFoundError" in resp["error"]


def test_service_serial_queue(daemon, tmp_path):
    """M-E5 串行语义（契约 §4.8）：并发提交 → FIFO 排队、单 worker 逐个执行。

    手法：A 提交后立即 pause（帧闸门阻塞 worker），B 随后提交——
    B 必须保持 queued 直到 A 恢复并完成；两 job 最终都成功。
    """
    m1 = _write_manifest(tmp_path / "a")
    m2 = _write_manifest(tmp_path / "b")
    service = _start_service()
    try:
        ra = service.submit({"manifest": m1, "clock_rate": 0})
        assert not ra.get("error"), ra
        ja = ra["job_id"]
        service.command(ja, "pause")  # 帧闸门阻塞：worker 停在 A 内
        # 等 worker 取出 A（started）并确认 A 在跑
        deadline = time.monotonic() + 10
        while service.job_status(ja)["status"] != "running" and time.monotonic() < deadline:
            time.sleep(0.05)
        assert service.job_status(ja)["status"] == "running"

        rb = service.submit({"manifest": m2, "clock_rate": 0})
        jb = rb["job_id"]
        time.sleep(1.0)  # A 未放行间，B 不得开始（串行证明）
        assert service.job_status(jb)["status"] == "queued"
        assert service.queue_depth == 1

        service.command(ja, "resume")
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if service.job_status(ja)["status"] == "done" and service.job_status(jb)["status"] == "done":
                break
            time.sleep(0.2)
        for jid in (ja, jb):
            state = service.job_status(jid)
            assert state["status"] == "done" and not state["error"], state
            assert len(state["results"]) == 2
        assert service.queue_depth == 0
    finally:
        service.close()


def test_cli_matrix(daemon, tmp_path, capsys):
    """交叉测试矩阵：多算法条目逐个评测并聚合结果（--json）。"""
    m1 = _write_manifest(tmp_path / "a")
    m2 = _write_manifest(tmp_path / "b")
    matrix_path = tmp_path / "test_matrix.yaml"
    matrix_path.write_text(
        yaml.safe_dump({"algorithms": [{"manifest": m1}, {"manifest": m2}]}),
        encoding="utf-8",
    )

    service = _start_service()
    try:
        rc = cli_main(["matrix", str(matrix_path), "--json"])
    finally:
        service.close()

    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True
    assert len(data["algorithms"]) == 2
    for item in data["algorithms"]:
        assert not item.get("error"), item
        assert len(item["results"]) == 2
