"""测试基础设施：tzcomm 守护进程——优先复用已有的，没有就自己拉一个。

- **不 flushdb、不碰生产实例**：测试用独立端口与 Redis db（缺省 18888 / db15），
  与生产实例（17888 / db0）隔离；
- 通过 `AUTOTEST_CONTROL_SERVICE` / `AUTOTEST_JOB_STATUS_SERVICE` 覆盖服务名，
  避免与运行中的开发 server 抢占 `autotest/control`；
- `daemon` fixture 先探连通性，**不可达则本会话自起一个并在结束时收掉**。

  原设计是"只探测、不自建"，理由是"tzcomm 是系统服务"。但 18888 这个测试实例
  **没有任何东西负责常驻**——2026-09-03/04 因此连续三次让整个 functional 套件
  报 30 个 daemon 不可达的错，每次都要人工先起一个才能跑。测试套件依赖没人负责
  provision 的环境状态，就等于每次都在赌运气；自起一个是把这个赌局去掉。
  已有可达实例时仍然复用（不重复起、不干扰他人）。
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session", autouse=True)
def env_setup(tmp_path_factory):
    # 系统 tzcomm 守护进程（由系统/用户启动）；已有环境变量时尊重它
    os.environ.setdefault("TZCOMM_DAEMON_ADDR", "127.0.0.1:18888")
    os.environ.setdefault("TZCOMM_REDIS_URL", "redis://127.0.0.1:6379/15")
    # 测试专属服务名：不与运行中的开发 server 抢 autotest/control
    os.environ.setdefault("AUTOTEST_CONTROL_SERVICE", "autotest-test/control")
    os.environ.setdefault("AUTOTEST_JOB_STATUS_SERVICE", "autotest-test/job/status")
    os.environ["AUTOTEST_ARTIFACTS_DIR"] = str(tmp_path_factory.mktemp("artifacts"))
    # 算法子进程经环境继承 import autotest（pytest pythonpath 仅作用于测试进程本身；
    # 生产部署为 pip install，测试环境用 PYTHONPATH 等价模拟）
    src = str(_ROOT / "src")
    existing = os.environ.get("PYTHONPATH")
    os.environ["PYTHONPATH"] = src if not existing else f"{src}{os.pathsep}{existing}"
    yield


def _daemon_addr() -> tuple[str, int]:
    host, _, port = os.environ["TZCOMM_DAEMON_ADDR"].rpartition(":")
    return host or "127.0.0.1", int(port)


def _reachable(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _wait_reachable(host: str, port: int, deadline_s: float) -> bool:
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        if _reachable(host, port):
            return True
        time.sleep(0.2)
    return False


@pytest.fixture(scope="session")
def daemon(env_setup):
    """tzcomm 守护进程：可达则复用，不可达则本会话自起一个（结束时收掉）。"""
    host, port = _daemon_addr()
    if _wait_reachable(host, port, 2.0):
        yield None
        return

    proc = subprocess.Popen(
        [sys.executable, "-m", "tzcomm.cli", "daemon"],
        stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
        env={**os.environ},  # TZCOMM_DAEMON_ADDR / TZCOMM_REDIS_URL 已由 env_setup 注入
    )
    if not _wait_reachable(host, port, 15.0):
        proc.terminate()
        pytest.fail(f"自起 tzcomm daemon 失败：{host}:{port} 15s 内未就绪")
    try:
        yield None
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
