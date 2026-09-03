"""运行环境准备单测（M-F4，docs/07-附录-scenario-yaml-schema.md §5）。

覆盖：host 透传 / docker F1 期明确报错 / venv 创建·复用·PATH 前置 / requirements 缺失与失败语义。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from autotest.manifest import load_algorithm_manifest
from autotest.server.runtime import RuntimePrepareError, prepare_runtime


def _manifest(tmp_path: Path, runtime: dict | None, requirements: str | None = None):
    data = {"launch": "python3 algo.py"}
    if runtime is not None:
        data["runtime"] = runtime
    (tmp_path / "scenario.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    if requirements is not None:
        (tmp_path / "requirements.txt").write_text(requirements, encoding="utf-8")
    return load_algorithm_manifest(str(tmp_path / "scenario.yaml"))


def _log(lines: list[str]):
    return lines.append


# ---- host（缺省零迁移） ----

def test_host_default_passthrough(tmp_path):
    m = _manifest(tmp_path, None)
    assert prepare_runtime(m, _log([])) == {}


def test_host_explicit_passthrough(tmp_path):
    m = _manifest(tmp_path, {"type": "host"})
    assert prepare_runtime(m, _log([])) == {}


# ---- docker（M-F5，F1 期明确报错） ----

def test_docker_rejected_in_f1(tmp_path):
    m = _manifest(tmp_path, {"type": "docker"})
    with pytest.raises(RuntimePrepareError, match="docker 尚未实现"):
        prepare_runtime(m, _log([]))


# ---- venv ----

def test_venv_create_and_path_prefix(tmp_path):
    m = _manifest(tmp_path, {"type": "venv"}, requirements="")
    env = prepare_runtime(m, _log([]))
    venv = tmp_path / ".atp-venv"
    assert (venv / "bin" / "python3").is_file()
    assert env["PATH"].startswith(f"{venv / 'bin'}:")
    assert env["PATH"].split(":", 1)[1] == os.environ.get("PATH", "")
    assert env["VIRTUAL_ENV"] == str(venv)


def test_venv_reused_on_second_call(tmp_path):
    m = _manifest(tmp_path, {"type": "venv"}, requirements="")
    lines: list[str] = []
    prepare_runtime(m, _log(lines))
    assert any("创建 venv" in line for line in lines)
    lines.clear()
    prepare_runtime(m, _log(lines))
    assert any("复用 venv" in line for line in lines)
    assert not any("创建 venv" in line for line in lines)


def test_venv_without_requirements_warns_but_passes(tmp_path):
    m = _manifest(tmp_path, {"type": "venv"}, requirements=None)
    lines: list[str] = []
    env = prepare_runtime(m, _log(lines))
    assert env["VIRTUAL_ENV"]  # 裸 venv 仍生效
    assert any("WARNING" in line and "requirements.txt" in line for line in lines)


def test_venv_pip_failure_raises(tmp_path):
    m = _manifest(tmp_path, {"type": "venv"},
                  requirements="this-package-does-not-exist-atp==0.0.0\n")
    lines: list[str] = []
    with pytest.raises(RuntimePrepareError, match="pip install"):
        prepare_runtime(m, _log(lines))
    assert any("pip install -r requirements.txt" in line for line in lines)  # 命令留痕
