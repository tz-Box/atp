"""ATP HTTP client 单测（M-E8）：mock ATP server 验证 health/submit/status/wait 语义与退出码。

mock = stdlib ThreadingHTTPServer 预设响应表；client 经 ATP_BASE_URL 指向它，不依赖真服务。
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from autotest.client import atp
from autotest.client.cli import main as cli_main

_TOKEN = "unit-atp-token"


class _MockAtp(BaseHTTPRequestHandler):
    routes: dict = {}  # (method, path) -> (code, body) | [响应队列]
    posts: list = []

    def _respond(self, method: str):
        key = (method, self.path)
        entry = self.routes.get(key)
        if entry is None:
            code, body = 404, {"ok": False, "error": f"未mock: {key}"}
        elif isinstance(entry, list):
            code, body = entry.pop(0)
        else:
            code, body = entry
        if method == "POST":
            length = int(self.headers.get("Content-Length", 0))
            _MockAtp.posts.append((self.path, json.loads(self.rfile.read(length) or b"null"),
                                   self.headers.get("Authorization", "")))
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def do_GET(self):  # noqa: N802 stdlib 约定
        self._respond("GET")

    def do_POST(self):  # noqa: N802 stdlib 约定
        self._respond("POST")

    def log_message(self, *args):
        pass


@pytest.fixture()
def mock_atp(monkeypatch):
    _MockAtp.routes, _MockAtp.posts = {}, []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _MockAtp)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _MockAtp.base_url = f"http://127.0.0.1:{server.server_address[1]}"
    monkeypatch.setenv("ATP_BASE_URL", _MockAtp.base_url)
    monkeypatch.setenv("ATP_SERVICE_TOKEN", _TOKEN)
    yield _MockAtp
    server.shutdown()
    server.server_close()


@pytest.fixture()
def cfg_tmp(tmp_path, monkeypatch):
    """client.env 指向临时路径 + 清空 env（验证落盘配置独立生效）。"""
    cfg = tmp_path / "client.env"
    monkeypatch.setattr(atp, "_CFG_PATH", cfg)
    monkeypatch.delenv("ATP_BASE_URL", raising=False)
    monkeypatch.delenv("ATP_SERVICE_TOKEN", raising=False)
    return cfg


# ---- 函数层 ----

def test_health_ok(mock_atp):
    mock_atp.routes[("GET", "/atp/health")] = (
        200, {"ok": True, "version": "0.1.0", "tzcomm": True, "queue": 0})
    body = atp.health()
    assert body["ok"] is True and body["queue"] == 0


def test_health_unreachable(monkeypatch):
    monkeypatch.setenv("ATP_BASE_URL", "http://127.0.0.1:1")  # 死端口
    with pytest.raises(atp.AtpClientError, match="ATP 不可达"):
        atp.health()


def test_submit_payload_and_auth(mock_atp):
    mock_atp.routes[("POST", "/atp/evaluations")] = (
        202, {"ok": True, "job_id": "autotest-x1", "sha": "abc123"})
    code, body = atp.submit("owner/repo", ref="refs/heads/main", cid="chk_t1",
                            save_baseline=True)
    assert code == 202 and body["job_id"] == "autotest-x1"
    path, payload, auth = mock_atp.posts[0]
    assert auth == f"Bearer {_TOKEN}"
    assert payload == {"correlation_id": "chk_t1", "repo": "owner/repo",
                       "ref": "refs/heads/main", "check_type": "autotest",
                       "scenario": None, "save_baseline": True}


def test_submit_default_cid(mock_atp):
    mock_atp.routes[("POST", "/atp/evaluations")] = (202, {"ok": True, "job_id": "j"})
    atp.submit("/local/repo")
    assert mock_atp.posts[0][1]["correlation_id"].startswith("chk_manual_")


def test_submit_requires_token(mock_atp, monkeypatch):
    monkeypatch.delenv("ATP_SERVICE_TOKEN")
    with pytest.raises(atp.AtpClientError, match="ATP_SERVICE_TOKEN"):
        atp.submit("/local/repo")


def test_status_terminal_and_404(mock_atp):
    mock_atp.routes[("GET", "/atp/evaluations/j1")] = (
        200, {"job_id": "j1", "status": "success", "sha": "abc",
              "report": {"summary": "2/2 passed", "run_url": None},
              "finished_at": "2026-08-25T12:00:00+00:00"})
    mock_atp.routes[("GET", "/atp/evaluations/j2")] = (
        404, {"ok": False, "error": "未知 job_id: 'j2'"})
    assert atp.status("j1")["status"] == "success"
    with pytest.raises(atp.AtpClientError, match="未知 job_id"):
        atp.status("j2")


def test_wait_terminal_polls_until_done(mock_atp):
    mock_atp.routes[("GET", "/atp/evaluations/j1")] = [
        (200, {"job_id": "j1", "status": "running"}),
        (200, {"job_id": "j1", "status": "failure", "report": {"summary": "1/2 passed"}}),
    ]
    atp._WAIT_INTERVAL = 0.01  # 测试加速（模块常量直改，无需 monkeypatch 往返）
    terminal = atp.wait_terminal("j1", timeout=5.0)
    assert terminal["status"] == "failure"


def test_wait_timeout(mock_atp):
    mock_atp.routes[("GET", "/atp/evaluations/j1")] = (200, {"job_id": "j1", "status": "running"})
    atp._WAIT_INTERVAL = 0.01
    with pytest.raises(atp.AtpClientError, match="等待超时"):
        atp.wait_terminal("j1", timeout=0.05)


# ---- CLI 层（exit code 语义，可接 CI gate） ----

def test_cli_atp_submit_wait_success(mock_atp, capsys):
    mock_atp.routes[("POST", "/atp/evaluations")] = (
        202, {"ok": True, "job_id": "j1", "sha": "abc"})
    mock_atp.routes[("GET", "/atp/evaluations/j1")] = (
        200, {"job_id": "j1", "status": "success", "report": {"summary": "2/2 passed"}})
    rc = cli_main(["atp", "submit", "--repo", "/r", "--cid", "c1"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "success"


def test_cli_atp_submit_no_wait(mock_atp, capsys):
    mock_atp.routes[("POST", "/atp/evaluations")] = (202, {"ok": True, "job_id": "j1"})
    assert cli_main(["atp", "submit", "--repo", "/r", "--no-wait"]) == 0
    assert json.loads(capsys.readouterr().out)["job_id"] == "j1"


def test_cli_atp_submit_4xx_exit_1(mock_atp, capsys):
    mock_atp.routes[("POST", "/atp/evaluations")] = (
        400, {"ok": False, "error": "repo 不可达（本地路径不存在）: /x"})
    assert cli_main(["atp", "submit", "--repo", "/x"]) == 1
    assert "repo 不可达" in capsys.readouterr().out


def test_cli_atp_health_degraded_exit_1(mock_atp, capsys):
    mock_atp.routes[("GET", "/atp/health")] = (
        200, {"ok": False, "version": "0.1.0", "tzcomm": False, "queue": 0})
    assert cli_main(["atp", "health"]) == 1


def test_cli_atp_wait_failure_exit_1(mock_atp, capsys):
    mock_atp.routes[("GET", "/atp/evaluations/j1")] = (
        200, {"job_id": "j1", "status": "failure", "report": {"summary": "0/2 passed"}})
    assert cli_main(["atp", "wait", "j1"]) == 1


# ---- login/logout/whoami（交互式配置，免 export） ----

_HEALTH_OK = (200, {"ok": True, "version": "0.1.0", "tzcomm": True, "queue": 0})


def test_login_saves_file_chmod600_and_probes(mock_atp, cfg_tmp, capsys):
    mock_atp.routes[("GET", "/atp/health")] = _HEALTH_OK
    rc = cli_main(["atp", "login", "--base-url", mock_atp.base_url + "/", "--token", "tok-abc"])
    assert rc == 0
    content = cfg_tmp.read_text()
    assert f"ATP_BASE_URL={mock_atp.base_url}\n" in content  # 尾斜杠已规整
    assert "ATP_SERVICE_TOKEN=tok-abc\n" in content
    assert (cfg_tmp.stat().st_mode & 0o777) == 0o600
    # 落盘配置立即生效（env 已被 cfg_tmp 清空）
    assert atp._base_url() == mock_atp.base_url
    assert atp._token() == "tok-abc"
    assert "探活成功" in capsys.readouterr().out


def test_login_interactive_prompts(mock_atp, cfg_tmp, monkeypatch, capsys):
    mock_atp.routes[("GET", "/atp/health")] = _HEALTH_OK
    answers = iter([mock_atp.base_url])  # 地址经交互录入
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    monkeypatch.setattr(atp.getpass, "getpass", lambda prompt="": "tok-secret")
    assert cli_main(["atp", "login"]) == 0
    assert "ATP_SERVICE_TOKEN=tok-secret\n" in cfg_tmp.read_text()


def test_login_interactive_default_base(mock_atp, cfg_tmp, monkeypatch):
    mock_atp.routes[("GET", "/atp/health")] = _HEALTH_OK
    monkeypatch.setattr("builtins.input", lambda prompt="": "")  # 回车 = 默认地址
    monkeypatch.setattr(atp.getpass, "getpass", lambda prompt="": "t")
    monkeypatch.setattr(atp, "_DEFAULT_BASE", mock_atp.base_url)
    assert cli_main(["atp", "login"]) == 0
    assert f"ATP_BASE_URL={mock_atp.base_url}\n" in cfg_tmp.read_text()


def test_login_empty_token_rejected(cfg_tmp, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    monkeypatch.setattr(atp.getpass, "getpass", lambda prompt="": "")
    assert cli_main(["atp", "login"]) == 2
    assert not cfg_tmp.exists()
    assert "令牌为空" in capsys.readouterr().err


def test_login_unreachable_still_saves_warns(cfg_tmp, capsys):
    rc = cli_main(["atp", "login", "--base-url", "http://127.0.0.1:1", "--token", "t"])
    assert rc == 1
    assert cfg_tmp.exists()  # 配置仍落盘，仅警告不可达
    assert "暂不可达" in capsys.readouterr().err


def test_logout_removes_file_idempotent(cfg_tmp, capsys):
    cfg_tmp.write_text("ATP_BASE_URL=http://x\nATP_SERVICE_TOKEN=t\n")
    assert cli_main(["atp", "logout"]) == 0
    assert not cfg_tmp.exists()
    assert cli_main(["atp", "logout"]) == 0  # 幂等
    assert "无已保存配置" in capsys.readouterr().out


def test_whoami_shows_sources_and_masks_token(mock_atp, cfg_tmp, capsys):
    cfg_tmp.write_text(f"ATP_BASE_URL={mock_atp.base_url}\nATP_SERVICE_TOKEN=tok-xyz\n")
    mock_atp.routes[("GET", "/atp/health")] = _HEALTH_OK
    assert cli_main(["atp", "whoami"]) == 0
    out = capsys.readouterr().out
    assert "tok-xyz" not in out and "tok-" in out  # 掩码，不泄露全文
    assert "配置文件" in out and '"ok": true' in out


def test_whoami_unreachable_exit_1(cfg_tmp, capsys):
    cfg_tmp.write_text("ATP_BASE_URL=http://127.0.0.1:1\nATP_SERVICE_TOKEN=t\n")
    assert cli_main(["atp", "whoami"]) == 1
    assert "不可达" in capsys.readouterr().err


def test_env_overrides_file_cfg(cfg_tmp, monkeypatch):
    cfg_tmp.write_text("ATP_BASE_URL=http://file:1/\nATP_SERVICE_TOKEN=file-tok\n")
    monkeypatch.setenv("ATP_BASE_URL", "http://env:2/")
    monkeypatch.setenv("ATP_SERVICE_TOKEN", "env-tok")
    assert atp._base_url() == "http://env:2"  # 环境变量优先 + 尾斜杠规整
    assert atp._token() == "env-tok"


def test_file_cfg_auth_header(mock_atp, cfg_tmp):
    """env 清空后 submit 走落盘 token（login 后即用的核心通路）。"""
    cfg_tmp.write_text(f"ATP_BASE_URL={mock_atp.base_url}\nATP_SERVICE_TOKEN=file-tok\n")
    mock_atp.routes[("POST", "/atp/evaluations")] = (202, {"ok": True, "job_id": "j"})
    code, _ = atp.submit("/r")
    assert code == 202
    assert mock_atp.posts[0][2] == "Bearer file-tok"
