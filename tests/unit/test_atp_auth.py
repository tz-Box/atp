"""M-E11 飞书 OAuth 登录单测：成员门 / 会话 / 双通道鉴权（读=会话或Bearer，写=admin或Bearer）。

飞书出站（_http_json）整体打桩；白名单/会话文件 monkeypatch 到 tmp_path，测试间隔离。
双通道部分复用 test_atp_evaluations 的 _FakeService（真 store + 假执行）。
"""
from __future__ import annotations

import json
import urllib.parse
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from autotest.server import auth
from autotest.server.evaluations import EvaluationStore
from autotest.server.http import create_app

_TOKEN = "test-atp-token"
_OPEN_ID = "ou_test_zhangsan"
_NAME = "张三"


class _FakeService:
    """AutotestService 最小替身（与 test_atp_evaluations 同：真 store，假执行）。"""

    def __init__(self, store: EvaluationStore) -> None:
        self._store = store

    @property
    def evaluations(self) -> EvaluationStore:
        return self._store

    @property
    def queue_depth(self) -> int:
        return 0

    def submit_evaluation(self, req: dict) -> dict:
        created = self._store.create(
            cid=req["correlation_id"], job_id="autotest-fake01", repo=req["repo"],
            ref=req.get("ref"), sha=None)
        if not created:
            return {"duplicate": True,
                    "job_id": self._store.get_by_cid(req["correlation_id"])["job_id"]}
        return {"job_id": "autotest-fake01", "sha": None}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """隔离白名单/会话文件 + ATP_SERVICE_TOKEN；feishu 未配置（默认 oauth off）。"""
    monkeypatch.setattr(auth, "USERS_PATH", tmp_path / "console_users.json")
    monkeypatch.setattr(auth, "SESSIONS_PATH", tmp_path / "sessions.json")
    auth._sessions.clear()
    auth._states.clear()
    monkeypatch.setenv("ATP_SERVICE_TOKEN", _TOKEN)
    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)
    client = TestClient(create_app(_FakeService(EvaluationStore(tmp_path / "atp.db"))))
    return client, tmp_path


def _write_users(path: Path, users: list[dict]) -> None:
    path.write_text(json.dumps({"users": users}, ensure_ascii=False), encoding="utf-8")


def _mock_feishu(monkeypatch, open_id=_OPEN_ID, name=_NAME):
    def _fake(url: str, *, method: str = "GET", headers=None, payload=None) -> dict:
        if url == auth.TOKEN_URL:
            assert payload["client_id"] and payload["code"] == "code-1"
            return {"access_token": "uat-1"}
        if url == auth.USERINFO_URL:
            assert headers["Authorization"] == "Bearer uat-1"
            return {"data": {"open_id": open_id, "name": name}}
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr(auth, "_http_json", _fake)


def _start_state(client: TestClient) -> str:
    resp = client.get("/api/auth/feishu/start", follow_redirects=False)
    assert resp.status_code == 302
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(resp.headers["location"]).query)
    assert qs["client_id"] == ["app-id-1"] and qs["response_type"] == ["code"]
    return qs["state"][0]


def _login(client: TestClient) -> None:
    state = _start_state(client)
    resp = client.get(f"/api/auth/feishu/callback?code=code-1&state={state}",
                      follow_redirects=False)
    assert resp.status_code == 302 and resp.headers["location"] == "/console"
    assert auth.SESSION_COOKIE in resp.cookies


# ---- options / start ----

def test_options_oauth_off(env):
    client, _ = env
    assert client.get("/api/auth/options").json() == {"oauth": False}


def test_options_oauth_on_and_start(env, monkeypatch):
    client, _ = env
    monkeypatch.setenv("FEISHU_APP_ID", "app-id-1")
    monkeypatch.setenv("FEISHU_APP_SECRET", "secret-1")
    assert client.get("/api/auth/options").json() == {"oauth": True}
    _start_state(client)


def test_start_without_config_login_error(env):
    client, _ = env
    resp = client.get("/api/auth/feishu/start", follow_redirects=False)
    assert resp.status_code == 302
    assert "login_error=" in resp.headers["location"]


# ---- callback 成员门 ----

def _enable_oauth(monkeypatch):
    monkeypatch.setenv("FEISHU_APP_ID", "app-id-1")
    monkeypatch.setenv("FEISHU_APP_SECRET", "secret-1")


def test_callback_open_id_direct_hit(env, monkeypatch):
    client, tmp = env
    _enable_oauth(monkeypatch)
    _mock_feishu(monkeypatch)
    _write_users(tmp / "console_users.json",
                 [{"name": _NAME, "oauth_open_id": _OPEN_ID, "role": "admin"}])
    _login(client)
    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json() == {"name": _NAME, "open_id": _OPEN_ID, "role": "admin"}


def test_callback_name_match_backfill(env, monkeypatch):
    client, tmp = env
    _enable_oauth(monkeypatch)
    _mock_feishu(monkeypatch)
    users_path = tmp / "console_users.json"
    _write_users(users_path, [{"name": _NAME, "role": "member"}])  # 无 oauth_open_id
    _login(client)
    me = client.get("/api/auth/me").json()
    assert me["role"] == "member"
    saved = json.loads(users_path.read_text(encoding="utf-8"))["users"]
    assert saved[0]["oauth_open_id"] == _OPEN_ID  # 回填写回文件


def test_callback_not_in_whitelist_rejected(env, monkeypatch):
    client, tmp = env
    _enable_oauth(monkeypatch)
    _mock_feishu(monkeypatch)
    _write_users(tmp / "console_users.json", [{"name": "李四", "role": "member"}])
    state = _start_state(client)
    resp = client.get(f"/api/auth/feishu/callback?code=code-1&state={state}",
                      follow_redirects=False)
    assert "login_error=" in resp.headers["location"]
    assert client.get("/api/auth/me").status_code == 401  # 未建会话


def test_callback_expired_state_rejected(env, monkeypatch):
    client, _ = env
    _enable_oauth(monkeypatch)
    _mock_feishu(monkeypatch)
    resp = client.get("/api/auth/feishu/callback?code=code-1&state=bogus",
                      follow_redirects=False)
    assert "login_error=" in resp.headers["location"]


# ---- me / logout / 会话持久化 ----

def test_me_401_when_anonymous(env):
    client, _ = env
    assert client.get("/api/auth/me").status_code == 401


def test_logout_clears_session(env, monkeypatch):
    client, tmp = env
    _enable_oauth(monkeypatch)
    _mock_feishu(monkeypatch)
    _write_users(tmp / "console_users.json",
                 [{"name": _NAME, "oauth_open_id": _OPEN_ID, "role": "admin"}])
    _login(client)
    assert client.get("/api/auth/me").status_code == 200
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401


def test_session_persisted_to_disk(env, monkeypatch):
    client, tmp = env
    _enable_oauth(monkeypatch)
    _mock_feishu(monkeypatch)
    _write_users(tmp / "console_users.json",
                 [{"name": _NAME, "oauth_open_id": _OPEN_ID, "role": "admin"}])
    _login(client)
    saved = json.loads((tmp / "sessions.json").read_text(encoding="utf-8"))
    assert len(saved) == 1
    assert next(iter(saved.values()))["name"] == _NAME


# ---- 双通道鉴权（人通道会话 + 机器通道 Bearer 分层并存）----

def test_reader_session_or_bearer(env, monkeypatch):
    client, tmp = env
    _enable_oauth(monkeypatch)
    _mock_feishu(monkeypatch)
    _write_users(tmp / "console_users.json",
                 [{"name": _NAME, "oauth_open_id": _OPEN_ID, "role": "member"}])
    assert client.get("/atp/evaluations").status_code == 401  # 匿名
    _login(client)
    assert client.get("/atp/evaluations").status_code == 200  # member 会话可读
    client.post("/api/auth/logout")
    resp = client.get("/atp/evaluations",
                      headers={"Authorization": f"Bearer {_TOKEN}"})
    assert resp.status_code == 200  # 机器 Bearer 不受影响


def test_writer_requires_admin_or_bearer(env, monkeypatch):
    client, tmp = env
    _enable_oauth(monkeypatch)
    _mock_feishu(monkeypatch)
    _write_users(tmp / "console_users.json",
                 [{"name": _NAME, "oauth_open_id": _OPEN_ID, "role": "member"}])
    body = {"correlation_id": "c1", "repo": "/r"}
    assert client.post("/atp/evaluations", json=body).status_code == 401  # 匿名
    _login(client)
    assert client.post("/atp/evaluations", json=body).status_code == 403  # member 只读

    # 同用户提权 admin（改白名单后重登）
    _write_users(tmp / "console_users.json",
                 [{"name": _NAME, "oauth_open_id": _OPEN_ID, "role": "admin"}])
    client.post("/api/auth/logout")
    _login(client)
    assert client.post("/atp/evaluations", json=body).status_code == 202  # admin 可写

    client.post("/api/auth/logout")
    resp = client.post("/atp/evaluations", json={"correlation_id": "c2", "repo": "/r"},
                       headers={"Authorization": f"Bearer {_TOKEN}"})
    assert resp.status_code == 202  # 机器 Bearer 全权


def test_503_when_no_token_no_oauth(env, monkeypatch):
    client, _ = env
    monkeypatch.delenv("ATP_SERVICE_TOKEN")
    assert client.get("/atp/evaluations").status_code == 503
    assert client.post("/atp/evaluations", json={"correlation_id": "c", "repo": "/r"}
                       ).status_code == 503


def test_401_when_oauth_on_but_anonymous(env, monkeypatch):
    """token 未配但 OAuth 已配 → 未登录是 401（引导飞书登录）而非 503。"""
    client, _ = env
    monkeypatch.delenv("ATP_SERVICE_TOKEN")
    _enable_oauth(monkeypatch)
    assert client.get("/atp/evaluations").status_code == 401
