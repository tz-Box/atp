"""算法进程启动器：注入评测环境变量后启动算法。

Launcher 抽象：Service 不关心算法在宿主直启还是 docker 容器，
统一注入 AUTOTEST_SESSION / AUTOTEST_TOPICS 环境变量，由具体实现决定启动方式。
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
from typing import Optional

from .protocol import topics

# docker 镜像内算法目录（bind 挂载点，与 -w 工作目录一致）
_CONTAINER_WORKDIR = "/workspace"


def build_algo_env(session_id: str, extra_env: Optional[dict] = None) -> dict:
    """评测环境变量：会话号 + 会话话题表（JSON），供算法进程读取。"""
    env = {
        "AUTOTEST_SESSION": session_id,
        "AUTOTEST_TOPICS": json.dumps(
            {
                "obs": topics.obs_topic(session_id),
                "result": topics.result_topic(session_id),
                "action": topics.action_topic(session_id),
                "ctl": topics.ctl_service(session_id),
            }
        ),
    }
    if extra_env:
        env.update(extra_env)
    return env


def stop_process(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


class ProcessLauncher:
    """宿主直启：Popen 算法命令，注入评测环境变量。"""

    def launch(self, cmd: str, env: dict, cwd: str) -> subprocess.Popen:
        full_env = os.environ.copy()
        full_env.update(env)
        return subprocess.Popen(
            shlex.split(cmd),
            env=full_env,
            cwd=cwd,
        )


class DockerLauncher:
    """docker+bind：`docker run --network host` + `-e` 注入评测环境变量。

    算法根目录 bind 挂载进容器（-v <cwd>:/workspace），镜像无需因代码更新而重建，
    避免重复构建；网络用 host 直通 tzcomm daemon。
    """

    def launch(self, image: str, cmd: str, env: dict, cwd: str) -> subprocess.Popen:
        docker_cmd = [
            "docker", "run", "--rm",
            "--network", "host",
            "-v", f"{os.path.abspath(cwd)}:{_CONTAINER_WORKDIR}",
            "-w", _CONTAINER_WORKDIR,
        ]
        for key, value in env.items():
            docker_cmd += ["-e", f"{key}={value}"]
        docker_cmd += [image, *shlex.split(cmd)]
        return subprocess.Popen(docker_cmd)


def launch_algorithm(manifest, session_id: str) -> subprocess.Popen:
    """按 manifest 选择 Launcher 并拉起算法：有 image 走 docker+bind，否则宿主直启。"""
    env = build_algo_env(session_id)
    if manifest.image:
        return DockerLauncher().launch(manifest.image, manifest.launch, env, cwd=manifest.dir)
    return ProcessLauncher().launch(manifest.launch, env, cwd=manifest.dir)
