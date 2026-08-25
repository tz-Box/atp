"""`POST /atp/evaluations` 单测（M-E1）：认证 / 字段校验 / cid 幂等 / 202 骨架。

TestClient + 真 EvaluationStore（tmp SQLite）+ 假执行层，不依赖 tzcomm daemon。
业务层 _FakeService 复用真 checkout/真 store，仅把"拉起评测执行"替换为登记假 job。
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from autotest.server.checkout import CheckoutError, locate_manifest, resolve_checkout
from autotest.server.evaluations import EvaluationStore, conclusion_of
from autotest.server.http import create_app

_TOKEN = "test-atp-token"


class _FakeService:
    """AutotestService Hub 直通路的最小替身（真 store + 真 checkout，假执行）。"""

    def __init__(self, store: EvaluationStore) -> None:
        self._store = store
        self.submitted: list[str] = []

    @property
    def evaluations(self) -> EvaluationStore:
        return self._store

    @property
    def queue_depth(self) -> int:
        return 0

    def submit_evaluation(self, req: dict) -> dict:
        try:
            repo_dir, sha = resolve_checkout(req["repo"], req.get("ref"))
            manifest = locate_manifest(repo_dir, req.get("scenario"))
        except CheckoutError as exc:
            return {"error": str(exc)}
        self.submitted.append(str(manifest))
        job_id = f"autotest-fake{len(self.submitted):02d}"
        created = self._store.create(
            cid=req["correlation_id"], job_id=job_id, repo=req["repo"],
            ref=req.get("ref"), sha=sha, scenario=req.get("scenario"),
            save_baseline=bool(req.get("save_baseline")), pms_task_id=req.get("pms_task_id"),
        )
        if not created:
            existing = self._store.get_by_cid(req["correlation_id"])
            return {"duplicate": True, "job_id": existing["job_id"]}
        return {"job_id": job_id, "sha": sha}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    store = EvaluationStore(tmp_path / "atp.db")
    repo = tmp_path / "algo_repo"
    repo.mkdir()
    (repo / "scenario.yaml").write_text("launch: true\n", encoding="utf-8")
    monkeypatch.setenv("ATP_SERVICE_TOKEN", _TOKEN)
    service = _FakeService(store)
    client = TestClient(create_app(service))
    return service, store, repo, client


def _auth() -> dict:
    return {"Authorization": f"Bearer {_TOKEN}"}


# ---- 认证 ----

def test_auth_503_when_token_not_configured(env, monkeypatch):
    _, _, repo, client = env
    monkeypatch.delenv("ATP_SERVICE_TOKEN")
    resp = client.post("/atp/evaluations", json={"correlation_id": "c1", "repo": str(repo)})
    assert resp.status_code == 503


def test_auth_401_missing_or_wrong(env):
    _, _, repo, client = env
    body = {"correlation_id": "c1", "repo": str(repo)}
    assert client.post("/atp/evaluations", json=body).status_code == 401
    assert client.post("/atp/evaluations", json=body,
                       headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.post("/atp/evaluations", json=body,
                       headers={"Authorization": f"token {_TOKEN}"}).status_code == 401


# ---- 字段校验 ----

def test_missing_correlation_id_422(env):
    _, _, repo, client = env
    resp = client.post("/atp/evaluations", json={"repo": str(repo)}, headers=_auth())
    assert resp.status_code == 422


def test_empty_correlation_id_422(env):
    _, _, repo, client = env
    resp = client.post("/atp/evaluations", json={"correlation_id": "", "repo": str(repo)},
                       headers=_auth())
    assert resp.status_code == 422


def test_check_type_rejected_400(env):
    _, _, repo, client = env
    resp = client.post("/atp/evaluations",
                       json={"correlation_id": "c1", "repo": str(repo), "check_type": "lint"},
                       headers=_auth())
    assert resp.status_code == 400
    assert resp.json()["ok"] is False
    assert "check_type" in resp.json()["error"]


# ---- 提交与幂等 ----

def test_submit_202_skeleton(env):
    service, store, repo, client = env
    resp = client.post("/atp/evaluations", json={"correlation_id": "c1", "repo": str(repo)},
                       headers=_auth())
    assert resp.status_code == 202
    body = resp.json()
    assert body["ok"] is True
    assert body["job_id"] == "autotest-fake01"
    assert body["sha"] is None  # 非 git 仓：sha 缺省（M-E2 前过渡）
    assert service.submitted == [str(repo / "scenario.yaml")]
    row = store.get_by_cid("c1")
    assert row["job_id"] == "autotest-fake01"
    assert row["status"] == "running"
    assert row["save_baseline"] == 0


def test_submit_duplicate_cid_200_no_rerun(env):
    service, store, repo, client = env
    body = {"correlation_id": "c1", "repo": str(repo)}
    first = client.post("/atp/evaluations", json=body, headers=_auth())
    second = client.post("/atp/evaluations", json=body, headers=_auth())
    assert first.status_code == 202
    assert second.status_code == 200
    assert second.json() == {"ok": True, "duplicate": True, "job_id": "autotest-fake01"}
    assert len(service.submitted) == 1  # 不重复执行


def test_submit_repo_unreachable_400(env):
    _, _, _, client = env
    resp = client.post("/atp/evaluations",
                       json={"correlation_id": "c1", "repo": "/no/such/repo"},
                       headers=_auth())
    assert resp.status_code == 400
    assert "repo 不可达" in resp.json()["error"]


def test_submit_manifest_missing_400(env, tmp_path):
    _, _, _, client = env
    empty_repo = tmp_path / "empty_repo"
    empty_repo.mkdir()
    resp = client.post("/atp/evaluations",
                       json={"correlation_id": "c1", "repo": str(empty_repo)},
                       headers=_auth())
    assert resp.status_code == 400
    assert "manifest 缺失" in resp.json()["error"]


def test_submit_scenario_override(env):
    service, _, repo, client = env
    (repo / "ci").mkdir()
    (repo / "ci" / "eval.yaml").write_text("launch: true\n", encoding="utf-8")
    resp = client.post("/atp/evaluations",
                       json={"correlation_id": "c1", "repo": str(repo), "scenario": "ci/eval.yaml"},
                       headers=_auth())
    assert resp.status_code == 202
    assert service.submitted == [str(repo / "ci" / "eval.yaml")]


# ---- checkout 单元 ----

def test_checkout_ref_unreachable(tmp_path):
    repo = tmp_path / "git_repo"
    repo.mkdir()
    (repo / "scenario.yaml").write_text("launch: true\n", encoding="utf-8")
    os.system(f"git -C {repo} init -q && git -C {repo} add -A && "
              f"git -C {repo} -c user.email=t@t -c user.name=t commit -qm init")
    with pytest.raises(CheckoutError, match="ref 不可达"):
        resolve_checkout(str(repo), "no-such-ref")


def test_locate_manifest_escape_rejected(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    with pytest.raises(CheckoutError, match="越出仓库"):
        locate_manifest(repo, "../outside.yaml")


# ---- EvaluationStore 单元 ----

def test_store_roundtrip(tmp_path):
    store = EvaluationStore(tmp_path / "sub" / "atp.db")  # 父目录自动创建
    assert store.create(cid="c1", job_id="j1", repo="/r", ref="main", sha="abc",
                        save_baseline=True, pms_task_id="t1") is True
    assert store.create(cid="c1", job_id="j2", repo="/r", ref=None, sha=None) is False  # PK 幂等
    row = store.get_by_cid("c1")
    assert row["job_id"] == "j1" and row["status"] == "running"
    assert row["save_baseline"] == 1 and row["pms_task_id"] == "t1"
    assert store.get_by_job_id("j1")["cid"] == "c1"
    store.update_terminal("c1", status="success", finished_at="2026-08-25 12:00:00",
                          summary="2/2 passed")
    row = store.get_by_cid("c1")
    assert row["status"] == "success" and row["summary"] == "2/2 passed"
    assert row["finished_at"] == "2026-08-25 12:00:00"
    store.set_callback_error("c1", "连接拒绝")
    assert store.get_by_cid("c1")["callback_error"] == "连接拒绝"
    assert store.get_by_cid("nope") is None
    assert store.get_by_job_id("nope") is None


def test_conclusion_of_semantics():
    assert conclusion_of("ValueError: x", []) == "failure"
    assert conclusion_of(None, [{"passed": True}, {"passed": False}]) == "failure"
    assert conclusion_of(None, [{"passed": True}, {"passed": None}]) == "success"  # 数据流验证不判
    assert conclusion_of(None, []) == "success"


# ---- M-E4 状态查询与探活 ----

def test_evaluation_status_running(env):
    _, store, _, client = env
    store.create(cid="c1", job_id="j1", repo="/r", ref=None, sha=None)
    resp = client.get("/atp/evaluations/j1", headers=_auth())
    assert resp.status_code == 200
    assert resp.json() == {"job_id": "j1", "status": "running"}


def test_evaluation_status_terminal(env):
    _, store, _, client = env
    store.create(cid="c1", job_id="j1", repo="/r", ref=None, sha="abc123")
    store.update_terminal("c1", status="success", summary="2/2 passed",
                          finished_at="2026-08-25T12:00:00+00:00")
    resp = client.get("/atp/evaluations/j1", headers=_auth())
    assert resp.status_code == 200
    assert resp.json() == {
        "job_id": "j1", "status": "success", "sha": "abc123",
        "report": {"summary": "2/2 passed", "run_url": None},
        "finished_at": "2026-08-25T12:00:00+00:00",
    }


def test_evaluation_status_404_and_auth(env):
    _, _, _, client = env
    assert client.get("/atp/evaluations/nope", headers=_auth()).status_code == 404
    assert client.get("/atp/evaluations/nope").status_code == 401  # 查询同需认证


def test_atp_health_fields(env):
    _, _, _, client = env
    resp = client.get("/atp/health")  # 探活无需认证
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"ok", "version", "tzcomm", "queue"}
    assert isinstance(body["version"], str) and body["version"]
    assert body["queue"] == 0


def test_atp_health_tzcomm_unreachable_degrades(env, monkeypatch):
    """daemon 不可达 → ok/tzcomm=False 降级（Hub 据此跳过路由），探活本身仍即时响应。"""
    monkeypatch.setenv("TZCOMM_DAEMON_ADDR", "127.0.0.1:1")  # 死端口
    _, _, _, client = env
    start = time.monotonic()
    resp = client.get("/atp/health")
    assert time.monotonic() - start < 2.0  # 快检带超时，不被拖住
    assert resp.status_code == 200
    assert resp.json()["ok"] is False
    assert resp.json()["tzcomm"] is False
