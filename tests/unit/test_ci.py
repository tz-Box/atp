"""测试内容：CI/CD 支撑——manifest image 字段、DockerLauncher、launch_algorithm 选择、report 渲染。"""
from __future__ import annotations

import importlib.util
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
    y.write_text("module: slam\nlaunch: python3 a.py\nimage: registry/algo:v2\n", encoding="utf-8")
    manifest = load_algorithm_manifest(str(y))
    assert manifest.image == "registry/algo:v2"
    assert manifest.launch == "python3 a.py"


def test_manifest_image_default_empty(tmp_path) -> None:
    y = tmp_path / "scenario.yaml"
    y.write_text("module: slam\nlaunch: python3 a.py\n", encoding="utf-8")
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
    manifest = AlgorithmManifest(module="slam", launch="python3 a.py", image="img:v1", dir="/tmp/x")
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
    manifest = AlgorithmManifest(module="slam", launch="python3 a.py", dir="/tmp/x")
    assert launch_algorithm(manifest, "sess-2") == "proc"

    cmd, env, cwd = calls["process"]
    assert cmd == "python3 a.py" and cwd == "/tmp/x"
    assert env["AUTOTEST_SESSION"] == "sess-2"


# ---- report 渲染 ----
def test_build_body_ok() -> None:
    body = ci_report.build_body(
        {"ok": True, "results": [{"testcase_id": "tc0", "passed": True,
                                  "metrics": {"ate_rmse": 0.0012}}]},
        "autotest", "abc1234567",
    )
    assert "`autotest` @ `abc12345`" in body
    assert "tc0" in body and "✅" in body
    assert "ate_rmse=0.0012" in body


def test_build_body_failed() -> None:
    body = ci_report.build_body({"ok": False, "error": "评测超时"}, "mannultest", "")
    assert "评测失败" in body and "评测超时" in body
