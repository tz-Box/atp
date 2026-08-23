"""HTTP 面路由单测（TestClient + 假业务层，不依赖 tzcomm daemon）。

验收点：
- 路由 → AutotestService 公共方法（submit/command/job_status）的映射正确；
- {"error": ...} 语义与 tzcomm 面一致，HTTP 状态码仅作运维提示（404/409）。
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from autotest.server.http import create_app


class _FakeService:
    """AutotestService 公共业务面的最小替身。"""

    def __init__(self) -> None:
        self.jobs: dict[str, dict] = {}
        self.submitted: list[dict] = []

    @property
    def job_count(self) -> int:
        return len(self.jobs)

    def submit(self, request: dict) -> dict:
        self.submitted.append(request)
        if "manifest" not in request:
            return {"error": "KeyError: 'manifest'"}
        self.jobs["autotest-fake01"] = {"status": "running"}
        return {"job_id": "autotest-fake01"}

    def command(self, job_id: str, command: str, n: int = 1) -> dict:
        if job_id not in self.jobs:
            return {"error": f"未知 job_id: {job_id!r}"}
        if command not in ("pause", "step", "resume"):
            return {"error": f"未知调试命令: {command!r}"}
        return {"ok": True, "job_id": job_id, "run_state": "paused", "frames": 42}

    def job_status(self, job_id: str) -> dict:
        if job_id not in self.jobs:
            return {"error": f"未知 job_id: {job_id!r}"}
        return {"job_id": job_id, "status": "running", "run_state": "running",
                "frames": 42, "error": None, "results": [], "comm_health": None}


def _client() -> TestClient:
    return TestClient(create_app(_FakeService()))


def test_health():
    resp = _client().get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "jobs": 0}


def test_submit_ok_strips_none_fields():
    client = _client()
    resp = client.post("/api/submit", json={"manifest": "/repo/scenario.yaml"})
    assert resp.status_code == 200
    assert resp.json() == {"job_id": "autotest-fake01"}


def test_submit_missing_manifest_422():
    resp = _client().post("/api/submit", json={})  # 缺 manifest（pydantic 422）
    assert resp.status_code == 422


def test_submit_business_error_400():
    class _BadManifest(_FakeService):
        def submit(self, request: dict) -> dict:
            return {"error": "ManifestError: 文件不存在: /nope.yaml"}

    resp = TestClient(create_app(_BadManifest())).post(
        "/api/submit", json={"manifest": "/nope.yaml"})
    assert resp.status_code == 400
    assert "ManifestError" in resp.json()["error"]


def test_command_unknown_job_404():
    resp = _client().post("/api/command", json={"job_id": "nope", "command": "pause"})
    assert resp.status_code == 404
    assert "未知 job_id" in resp.json()["error"]


def test_command_bad_command_409():
    client = _client()
    client.post("/api/submit", json={"manifest": "/repo/scenario.yaml"})
    resp = client.post("/api/command", json={"job_id": "autotest-fake01", "command": "bogus"})
    assert resp.status_code == 409
    assert "未知调试命令" in resp.json()["error"]


def test_command_ok():
    client = _client()
    client.post("/api/submit", json={"manifest": "/repo/scenario.yaml"})
    resp = client.post("/api/command", json={"job_id": "autotest-fake01", "command": "step", "n": 5})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert resp.json()["frames"] == 42


def test_job_status_404_and_ok():
    client = _client()
    assert client.get("/api/jobs/nope").status_code == 404
    client.post("/api/submit", json={"manifest": "/repo/scenario.yaml"})
    resp = client.get("/api/jobs/autotest-fake01")
    assert resp.status_code == 200
    assert resp.json()["status"] == "running"
