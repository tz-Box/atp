"""评测代码准备：Hub 只传坐标（repo/ref），评测内容定义全部来自 checkout 的仓库内容（契约 §11）。

M-E1 范围：本地路径解析（已就绪的工作副本，测试/开发通路）——
- repo = 本地目录；ref 仅作只读校验（git rev-parse，不改动工作区）；
- 实际 sha 由 rev-parse 回填（非 git 仓缺省 None，M-E2 前过渡）。

M-E2 扩展（运维 O2 deploy key 就绪后）：远程 git URL → mirror 缓存
（~/.cache/autotest/repos/<owner>__<repo>.git）+ 按 job worktree 隔离，语义不变。
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

_GIT_TIMEOUT = 10.0


class CheckoutError(Exception):
    """checkout/坐标校验失败（对应端点 4xx：repo·ref 不可达 / manifest 缺失）。"""


def resolve_checkout(repo: str, ref: Optional[str]) -> tuple[Path, Optional[str]]:
    """解析 repo+ref →（算法仓根目录， 实际 sha 或 None）。

    M-E1 仅支持本地路径；ref 给定时必须是可解析的 git 引用（否则视为"ref 不可达"）。
    """
    path = Path(repo).expanduser()
    if not path.is_dir():
        raise CheckoutError(f"repo 不可达（本地路径不存在）: {repo}")
    sha = _git_sha(path, ref)
    return path, sha


def locate_manifest(repo_dir: Path, scenario: Optional[str]) -> Path:
    """定位 manifest：submit 的 scenario（相对仓根）或仓根 scenario.yaml。"""
    rel = scenario or "scenario.yaml"
    root = repo_dir.resolve()
    manifest = (root / rel).resolve()
    if not manifest.is_relative_to(root):
        raise CheckoutError(f"scenario 路径越出仓库: {scenario}")
    if not manifest.is_file():
        raise CheckoutError(f"manifest 缺失: {rel}")
    return manifest


def _git_sha(repo_dir: Path, ref: Optional[str]) -> Optional[str]:
    """只读解析 sha（rev-parse，不 checkout；M-E2 的 worktree 才做真实隔离）。"""
    target = ref or "HEAD"
    try:
        out = subprocess.run(  # noqa: S603 git 地址由本机配置注入
            ["git", "-C", str(repo_dir), "rev-parse", "--verify", f"{target}^{{commit}}"],
            capture_output=True, text=True, timeout=_GIT_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        if ref:
            raise CheckoutError(f"ref 校验失败（git 不可用）: {exc}") from exc
        return None
    if out.returncode != 0:
        if ref:
            raise CheckoutError(f"ref 不可达: {repo_dir}@{ref}")
        return None  # 非 git 仓：sha 缺省（契约允许 M-E2 前过渡为 null）
    return out.stdout.strip()
