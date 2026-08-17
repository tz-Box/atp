"""测试基础设施：直接使用系统 tzcomm 守护进程（系统服务，不自建、不清空）。

- 不启动独立 daemon、不 flushdb：tzcomm 是系统服务，测试连系统 daemon（`TZCOMM_DAEMON_ADDR`）；
- 通过 `AUTOTEST_CONTROL_SERVICE` / `AUTOTEST_JOB_STATUS_SERVICE` 覆盖服务名，
  避免与运行中的开发 server 抢占 `autotest/control`；
- `daemon` fixture 仅做连通性检查（服务不可达时明确报错，而非静默失败）。
"""
from __future__ import annotations

import os
import socket
import time

import pytest


@pytest.fixture(scope="session", autouse=True)
def env_setup(tmp_path_factory):
    # 系统 tzcomm 守护进程（由系统/用户启动）；已有环境变量时尊重它
    os.environ.setdefault("TZCOMM_DAEMON_ADDR", "127.0.0.1:18888")
    os.environ.setdefault("TZCOMM_REDIS_URL", "redis://127.0.0.1:6379/15")
    # 测试专属服务名：不与运行中的开发 server 抢 autotest/control
    os.environ.setdefault("AUTOTEST_CONTROL_SERVICE", "autotest-test/control")
    os.environ.setdefault("AUTOTEST_JOB_STATUS_SERVICE", "autotest-test/job/status")
    os.environ["AUTOTEST_ARTIFACTS_DIR"] = str(tmp_path_factory.mktemp("artifacts"))
    yield


def _daemon_addr() -> tuple[str, int]:
    host, _, port = os.environ["TZCOMM_DAEMON_ADDR"].rpartition(":")
    return host or "127.0.0.1", int(port)


@pytest.fixture(scope="session")
def daemon(env_setup):
    """校验系统 tzcomm 守护进程可达（测试不自建 daemon、不 flushdb）。"""
    host, port = _daemon_addr()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return None
        except OSError:
            time.sleep(0.2)
    pytest.fail(f"系统 tzcomm 守护进程不可达 {host}:{port}（请先启动 tzcomm daemon）")
