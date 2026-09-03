"""评测运行环境准备（M-F4，docs/07-附录-scenario-yaml-schema.md §5）。

runtime.type:
- host（缺省）：零处理，宿主机直跑（现状零迁移）；
- venv：仓根 `.atp-venv` 复用/创建 + `pip install -r requirements.txt`（仓根；缺文件 → 裸 venv + WARNING），
  评测进程经 PATH 前置 `.atp-venv/bin` 切 venv 解释器（launch 命令不变，python3/python 均解析到 venv）；
- docker：M-F5（F2 期）实现，F1 期明确报错，不静默回退。

job 级语义：同一 checkout 现场的所有场景共享一次环境准备（_run_job 循环前调用）；
失败 → job 级错误（RuntimePrepareError），评测记 failure。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Callable

_VENV_DIR = ".atp-venv"  # 仓内 venv 目录（schema §5）


class RuntimePrepareError(Exception):
    """运行环境准备失败（venv 创建/pip install/docker 未实现）：job 级失败语义。"""


def _sdk_pythonpath() -> str:
    """ATP 自身运行时（autotest/tzcomm）的导入根，注入 venv 子进程 PYTHONPATH——

    SDK 由 runner 环境提供（venv 内不重复安装）；算法自有依赖经 requirements.txt 装到 venv
    （sys.path 前段，优先于系统包，实现版本隔离）。
    """
    import autotest  # 自身（service 进程必可导入）
    roots = [str(Path(autotest.__file__).resolve().parent.parent)]
    try:
        import tzcomm
        roots.append(str(Path(tzcomm.__file__).resolve().parent.parent))
    except ImportError:
        pass
    return os.pathsep.join(roots)


def _run(cmd: list[str], cwd: Path, log: Callable[[str], None], what: str) -> None:
    """执行环境准备命令并留痕（尾部输出进 session.log）；非零退出 → RuntimePrepareError。"""
    log(f"[runtime] {what}: {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=600)
    for line in (proc.stdout or "").splitlines()[-20:]:
        log(f"[runtime] {line}")
    if proc.returncode != 0:
        for line in (proc.stderr or "").splitlines()[-20:]:
            log(f"[runtime] {line}")
        raise RuntimePrepareError(f"{what} 失败（exit {proc.returncode}）")


def prepare_runtime(manifest, log: Callable[[str], None]) -> dict:
    """按 manifest.runtime 准备运行环境，返回注入算法进程的环境变量（host → {}）。"""
    runtime = manifest.runtime or {}
    rtype = runtime.get("type", "host")
    if rtype == "host":
        return {}
    if rtype == "docker":
        raise RuntimePrepareError(
            "runtime.type: docker 尚未实现（M-F5，F2 期；可暂用顶层 image 字段 docker+bind）")
    if rtype != "venv":
        # load_algorithm_manifest 提交期已校验，此处兜底防御
        raise RuntimePrepareError(f"runtime.type 未知: {rtype!r}")

    root = Path(manifest.dir)
    venv = root / _VENV_DIR
    python = venv / "bin" / "python3"
    if not python.is_file():
        # --system-site-packages：runner 预装基础设施（如系统级 tzcomm）对算法可见；
        # venv 自有包仍在 sys.path 前段（版本隔离价值保留）
        _run([sys.executable, "-m", "venv", "--system-site-packages", str(venv)],
             root, log, "创建 venv")
    else:
        log(f"[runtime] 复用 venv: {venv}")
    requirements = root / "requirements.txt"
    if requirements.is_file():
        _run([str(python), "-m", "pip", "install", "-r", str(requirements)],
             root, log, "pip install -r requirements.txt")
    else:
        log("[runtime] WARNING: 仓根无 requirements.txt，裸 venv（算法依赖需随仓声明）")
    existing_pp = os.environ.get("PYTHONPATH", "")
    return {
        "PATH": f"{venv / 'bin'}:{os.environ.get('PATH', '')}",
        "VIRTUAL_ENV": str(venv),
        # SDK（autotest/tzcomm）经 PYTHONPATH 透传，venv 内无需重复安装
        "PYTHONPATH": _sdk_pythonpath() + (os.pathsep + existing_pp if existing_pp else ""),
    }
