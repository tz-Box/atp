"""测试内容：CI/CD 支撑——manifest image 字段、DockerLauncher、launch_algorithm 选择、Hub 回调。"""
from __future__ import annotations

import importlib.util
import json
import sys
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest

from autotest.launcher import DockerLauncher, ProcessLauncher, launch_algorithm
from autotest.manifest import AlgorithmManifest, load_algorithm_manifest

# examples/ 非包，从文件路径加载 report 模块
_spec = importlib.util.spec_from_file_location("ci_report", "examples/ci/report.py")
ci_report = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ci_report)  # type: ignore[union-attr]


# ---- manifest image 字段 ----
def test_manifest_image_field(tmp_path) -> None:
    y = tmp_path / "scenario.yaml"
    y.write_text("launch: python3 a.py\nimage: registry/algo:v2\n", encoding="utf-8")
    manifest = load_algorithm_manifest(str(y))
    assert manifest.image == "registry/algo:v2"
    assert manifest.launch == "python3 a.py"


def test_manifest_image_default_empty(tmp_path) -> None:
    y = tmp_path / "scenario.yaml"
    y.write_text("launch: python3 a.py\n", encoding="utf-8")
    assert load_algorithm_manifest(str(y)).image == ""


# ---- DockerLauncher：命令组装（不真跑 docker） ----
def test_docker_launcher_command(monkeypatch) -> None:
    captured: dict = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return "proc"

    monkeypatch.setattr("autotest.launcher.subprocess.Popen", fake_popen)
    proc = DockerLauncher().launch(
        "registry/algo:v1", "python3 algo.py", {"AUTOTEST_SESSION": "s1"}, cwd="/tmp/algo"
    )
    assert proc == "proc"

    cmd = captured["cmd"]
    assert cmd[:3] == ["docker", "run", "--rm"]
    assert cmd[cmd.index("--network") + 1] == "host"
    assert "-v" in cmd and "/tmp/algo:/workspace" in cmd
    assert "-w" in cmd and "/workspace" in cmd
    assert "AUTOTEST_SESSION=s1" in cmd
    assert cmd[-3:] == ["registry/algo:v1", "python3", "algo.py"]


# ---- launch_algorithm：按 manifest.image 选择 Launcher ----
def test_launch_algorithm_prefers_docker(monkeypatch) -> None:
    calls: dict = {}

    class _FakeDockerLauncher:
        def launch(self, image, cmd, env, cwd):
            calls["docker"] = (image, cmd, env, cwd)
            return "docker-proc"

    monkeypatch.setattr("autotest.launcher.DockerLauncher", _FakeDockerLauncher)
    manifest = AlgorithmManifest(launch="python3 a.py", image="img:v1", dir="/tmp/x")
    assert launch_algorithm(manifest, "sess-1") == "docker-proc"

    image, cmd, env, cwd = calls["docker"]
    assert image == "img:v1"
    assert cmd == "python3 a.py"
    assert cwd == "/tmp/x"
    assert env["AUTOTEST_SESSION"] == "sess-1"
    assert "AUTOTEST_TOPICS" in env


def test_launch_algorithm_falls_back_to_process(monkeypatch) -> None:
    calls: dict = {}

    class _FakeProcessLauncher:
        def launch(self, cmd, env, cwd):
            calls["process"] = (cmd, env, cwd)
            return "proc"

    monkeypatch.setattr("autotest.launcher.ProcessLauncher", _FakeProcessLauncher)
    manifest = AlgorithmManifest(launch="python3 a.py", dir="/tmp/x")
    assert launch_algorithm(manifest, "sess-2") == "proc"

    cmd, env, cwd = calls["process"]
    assert cmd == "python3 a.py" and cwd == "/tmp/x"
    assert env["AUTOTEST_SESSION"] == "sess-2"


# ---- report：summary 生成 ----
def test_summarize_ok() -> None:
    summary = ci_report.summarize(
        {"ok": True, "results": [{"testcase_id": "tc0", "passed": True,
                                  "metrics": {"ate_rmse": 0.0012}},
                                 {"testcase_id": "tc1", "passed": None, "n_records": 42}]}
    )
    assert summary.startswith("1/2 passed")
    assert "tc0: passed (ate_rmse=0.0012)" in summary
    assert "tc1: 数据流验证 records=42" in summary


def test_summarize_failed() -> None:
    assert ci_report.summarize({"ok": False, "error": "评测超时"}) == "评测失败: 评测超时"


def test_summarize_appends_comm_warnings() -> None:
    """comm_health 有告警时摘要尾部附带（CI 侧直接可见疑似 tzcomm 链路问题）。"""
    summary = ci_report.summarize({
        "ok": True,
        "results": [{"testcase_id": "tc0", "passed": True, "metrics": {"ate_rmse": 0.0012}}],
        "comm_health": {"service": {"loss_rate": 0.0}, "sut": None,
                        "warnings": ["SUT 侧累计丢包率 4.80% 超阈值 1%"]},
    })
    assert summary.startswith("1/1 passed")
    assert "通信告警" in summary and "丢包率" in summary


def test_summarize_no_warnings_when_comm_absent() -> None:
    summary = ci_report.summarize({"ok": True, "results": []})
    assert "通信告警" not in summary


# ---- report：回调载荷（v1.3 §4.3） ----
_ENV = {"correlation_id": "chk_01JABC", "sha": "abc1234567", "check_type": "autotest",
        "run_url": "https://github.com/o/r/actions/runs/1"}


def test_build_payload_success() -> None:
    payload = ci_report.build_payload(
        {"ok": True, "results": [{"testcase_id": "tc0", "passed": True,
                                  "metrics": {"ate_rmse": 0.0012}}]},
        dict(_ENV),
    )
    assert payload["correlation_id"] == "chk_01JABC"
    assert payload["sha"] == "abc1234567"  # 实际 checkout sha，通路3 回填 pending 用
    assert payload["check_type"] == "autotest"
    assert payload["conclusion"] == "success"
    assert payload["report"]["summary"].startswith("1/1 passed")
    assert payload["report"]["run_url"] == _ENV["run_url"]
    datetime.fromisoformat(payload["finished_at"])  # ISO8601 可解析


def test_build_payload_failure_and_dataflow() -> None:
    # 评测报错 → failure
    assert ci_report.build_payload({"ok": False, "error": "x"}, dict(_ENV))["conclusion"] == "failure"
    # testcase failed → failure
    bad = {"ok": True, "results": [{"testcase_id": "tc0", "passed": False, "metrics": {}}]}
    assert ci_report.build_payload(bad, dict(_ENV))["conclusion"] == "failure"
    # passed=None（数据流验证，无 GT）不判失败
    flow = {"ok": True, "results": [{"testcase_id": "tc0", "passed": None, "n_records": 3}]}
    assert ci_report.build_payload(flow, dict(_ENV))["conclusion"] == "success"
    # run_url 可选：缺省不出现在 report 里
    env = {k: v for k, v in _ENV.items() if k != "run_url"}
    assert "run_url" not in ci_report.build_payload(flow, env)["report"]


# ---- report：mock callback server 验收（v1.3 §4.3 载荷 + Bearer 鉴权） ----
class _Handler(BaseHTTPRequestHandler):
    received: dict = {}

    def do_POST(self) -> None:  # noqa: N802 stdlib 命名
        _Handler.received = {
            "path": self.path,
            "auth": self.headers.get("Authorization"),
            "body": json.loads(self.rfile.read(int(self.headers["Content-Length"]))),
        }
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

    def log_message(self, *args) -> None:
        pass


def test_post_callback_against_mock_server(tmp_path, monkeypatch) -> None:
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/api/ci/callback"
        report_path = tmp_path / "report.json"
        report_path.write_text(json.dumps(
            {"ok": True, "results": [{"testcase_id": "tc0", "passed": True,
                                      "metrics": {"ate_rmse": 0.0012}}]}
        ), encoding="utf-8")
        monkeypatch.setenv("HUB_CALLBACK_URL", url)
        monkeypatch.setenv("HUB_CALLBACK_TOKEN", "tok-123")
        monkeypatch.setenv("CORRELATION_ID", "chk_01JABC")
        monkeypatch.setenv("ACTUAL_SHA", "abc1234567")
        monkeypatch.delenv("RUN_URL", raising=False)

        monkeypatch.setattr(sys, "argv", ["report.py", str(report_path)])
        assert ci_report.main() == 0

        assert _Handler.received["path"] == "/api/ci/callback"
        assert _Handler.received["auth"] == "Bearer tok-123"
        body = _Handler.received["body"]
        assert body["correlation_id"] == "chk_01JABC"
        assert body["sha"] == "abc1234567"
        assert body["check_type"] == "autotest"
        assert body["conclusion"] == "success"
        assert set(body["report"]) == {"summary"}  # 无 RUN_URL 时 run_url 缺省
        datetime.fromisoformat(body["finished_at"])
    finally:
        server.shutdown()


def test_main_missing_env(monkeypatch) -> None:
    for key in ("HUB_CALLBACK_URL", "HUB_CALLBACK_TOKEN", "CORRELATION_ID", "ACTUAL_SHA"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(sys, "argv", ["report.py", "whatever.json"])
    assert ci_report.main() == 2
