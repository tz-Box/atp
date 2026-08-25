"""M-E1/M-E3 功能测试：真 Service + 本地 git 仓 → POST /atp/evaluations 端到端。

覆盖：202 骨架（sha 由 rev-parse 回填）→ job 真实执行（echo 算法）→
EvaluationStore 终态回写（success）→ 同 cid 重发幂等（不重复执行）；
M-E3：评测完成自动回调 mock Hub（Bearer + 载荷语义）。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from autotest.server import AutotestService
from autotest.server.http import create_app

_ROOT = Path(__file__).resolve().parent.parent.parent
_ALGO = str(Path(__file__).resolve().parent / "_echo_algo.py")
_SCENARIO = str(_ROOT / "scenarios" / "synthetic_slam.yaml")
_TOKEN = "func-atp-token"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git 不可用")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def _make_repo(tmp_path: Path) -> Path:
    """本地 git 算法仓：manifest + echo SUT + 预置场景引用。"""
    repo = tmp_path / "algo_repo"
    repo.mkdir()
    manifest = {
        "launch": f"{sys.executable} {_ALGO}",
        "consumes": ["pipe.slam.SlamObs"],
        "scenario": _SCENARIO,
        "required_sensors": {"lidar": ["front"]},
    }
    (repo / "scenario.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    return repo


class _MockHub(BaseHTTPRequestHandler):
    received: list[dict] = []
    auths: list[str] = []
    got = threading.Event()

    def do_POST(self):  # noqa: N802 stdlib 约定
        _MockHub.auths.append(self.headers.get("Authorization", ""))
        _MockHub.received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        _MockHub.got.set()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

    def log_message(self, *args):
        pass


def test_atp_evaluations_end_to_end(daemon, tmp_path, monkeypatch):
    monkeypatch.setenv("ATP_SERVICE_TOKEN", _TOKEN)
    repo = _make_repo(tmp_path)
    expected_sha = _git(repo, "rev-parse", "HEAD")

    service = AutotestService(name="test-service-atp")
    threading.Thread(target=service.spin, daemon=True).start()
    time.sleep(0.5)
    client = TestClient(create_app(service))
    auth = {"Authorization": f"Bearer {_TOKEN}"}
    try:
        resp = client.post("/atp/evaluations", json={
            "correlation_id": "chk_func01", "repo": str(repo), "ref": "HEAD",
        }, headers=auth)
        assert resp.status_code == 202, resp.json()
        body = resp.json()
        assert body["ok"] is True and body["sha"] == expected_sha
        job_id = body["job_id"]

        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            state = service.job_status(job_id)
            if state["status"] == "done":
                break
            time.sleep(0.2)
        assert state["status"] == "done" and not state["error"], state

        row = service.evaluations.get_by_cid("chk_func01")
        assert row["status"] == "success"
        assert row["sha"] == expected_sha
        assert row["finished_at"]

        dup = client.post("/atp/evaluations", json={
            "correlation_id": "chk_func01", "repo": str(repo), "ref": "HEAD",
        }, headers=auth)
        assert dup.status_code == 200
        assert dup.json() == {"ok": True, "duplicate": True, "job_id": job_id}
    finally:
        service.close()


def test_atp_evaluation_auto_callback(daemon, tmp_path, monkeypatch):
    """M-E3：真评测完成 → 自动回调 mock Hub（Bearer + cid + 实际 sha + conclusion + summary）。"""
    _MockHub.received, _MockHub.auths = [], []
    _MockHub.got.clear()
    hub_server = ThreadingHTTPServer(("127.0.0.1", 0), _MockHub)
    hub_server.daemon_threads = True
    threading.Thread(target=hub_server.serve_forever, daemon=True).start()
    monkeypatch.setenv("ATP_SERVICE_TOKEN", _TOKEN)
    monkeypatch.setenv("HUB_CALLBACK_URL",
                       f"http://127.0.0.1:{hub_server.server_address[1]}/api/ci/callback")
    monkeypatch.setenv("HUB_CALLBACK_TOKEN", "func-hub-token")
    repo = _make_repo(tmp_path)
    expected_sha = _git(repo, "rev-parse", "HEAD")

    service = AutotestService(name="test-service-atp-cb")
    threading.Thread(target=service.spin, daemon=True).start()
    time.sleep(0.5)
    client = TestClient(create_app(service))
    try:
        resp = client.post("/atp/evaluations", json={
            "correlation_id": "chk_func03", "repo": str(repo),
        }, headers={"Authorization": f"Bearer {_TOKEN}"})
        assert resp.status_code == 202, resp.json()

        assert _MockHub.got.wait(timeout=120), "mock Hub 未收到回调"
        payload = _MockHub.received[0]
        assert _MockHub.auths == ["Bearer func-hub-token"]
        assert payload["correlation_id"] == "chk_func03"
        assert payload["sha"] == expected_sha
        assert payload["check_type"] == "autotest"
        assert payload["conclusion"] == "success"
        assert "2/2 passed" in payload["report"]["summary"]
        assert payload["finished_at"].endswith("+00:00")
        # 终态与摘要同步落档（M-E4 状态查询复用）
        row = service.evaluations.get_by_cid("chk_func03")
        assert row["status"] == "success" and "2/2 passed" in row["summary"]
        assert not row["callback_error"]
    finally:
        service.close()
        hub_server.shutdown()
        hub_server.server_close()

def test_atp_evaluations_failure_terminal(daemon, tmp_path, monkeypatch):
    """评测运行期失败（SUT 启动即死，_wait_sut 10s 超时）→ 终态 failure 回写。"""
    monkeypatch.setenv("ATP_SERVICE_TOKEN", _TOKEN)
    repo = tmp_path / "bad_repo"
    repo.mkdir()
    (repo / "scenario.yaml").write_text(
        yaml.safe_dump({"launch": f"{sys.executable} /no/such/sut.py",
                        "scenario": _SCENARIO}),
        encoding="utf-8",
    )
    _git(repo, "init", "-q")

    service = AutotestService(name="test-service-atp-fail")
    threading.Thread(target=service.spin, daemon=True).start()
    time.sleep(0.5)
    client = TestClient(create_app(service))
    try:
        resp = client.post("/atp/evaluations", json={
            "correlation_id": "chk_func02", "repo": str(repo),
        }, headers={"Authorization": f"Bearer {_TOKEN}"})
        # SUT 启动失败在 _run_job 执行期才暴露（submit 成功、评测失败）
        assert resp.status_code == 202, resp.json()
        job_id = resp.json()["job_id"]

        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if service.job_status(job_id)["status"] == "done":
                break
            time.sleep(0.2)
        row = service.evaluations.get_by_cid("chk_func02")
        assert row["status"] == "failure"
        assert row["finished_at"]
    finally:
        service.close()
