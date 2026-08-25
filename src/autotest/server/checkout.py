"""评测代码准备：Hub 只传坐标（repo/ref），评测内容定义全部来自 checkout 的仓库内容（契约 §11）。

repo 三种形式（M-E2 齐备）：
1. **本地目录**（测试/开发通路）：只读校验（git rev-parse，不改动工作区），sha 回填；
2. **git URL**（`https://…`、`git@…`、`file://…`）：mirror 缓存 + 按 job worktree 隔离；
3. **`owner/repo` 简写**：拼 `ATP_GIT_BASE`（缺省 `git@github.com:`，deploy key 走系统 ssh config）。

布局（`ATP_CACHE_ROOT`，缺省 `~/.cache/autotest`）：
- `repos/<owner>__<repo>.git`：`git clone --mirror` 缓存，每次 submit `remote update --prune` 更新；
- `workspaces/<job_id>/`：`git worktree add --detach <sha>` 按 job 隔离，评测终态后清理
  （`ATP_WORKTREE_KEEP=1` 保留现场便于排查）；服务启动时 `cleanup_stale` 清扫异常退出残留。
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_GIT_TIMEOUT = 10.0       # 本地 git 操作（rev-parse / worktree）
_GIT_NET_TIMEOUT = 180.0  # clone / fetch（网络操作）


class CheckoutError(Exception):
    """checkout/坐标校验失败（对应端点 4xx：repo·ref 不可达 / manifest 缺失）。"""


@dataclass
class Worktree:
    """一次评测的 git worktree 隔离现场（终态后由 remove_worktree 清理）。"""

    mirror: Path  # 所属 mirror 仓（worktree 元数据挂在其下）
    path: Path    # worktree 根目录（workspaces/<job_id>）


@dataclass
class Checkout:
    """坐标解析结果：评测用仓库根目录 + 实际 sha；worktree 非空表示远端隔离现场。"""

    repo_dir: Path
    sha: Optional[str]
    worktree: Optional[Worktree] = None


# mirror 级互斥锁：并发 submit（HTTP 线程）对同一 mirror 的 fetch/worktree 串行化
_LOCKS: dict[Path, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def resolve_checkout(repo: str, ref: Optional[str]) -> tuple[Path, Optional[str]]:
    """解析本地路径 repo+ref →（算法仓根目录， 实际 sha 或 None）。仅本地目录形式。"""
    path = Path(repo).expanduser()
    if not path.is_dir():
        raise CheckoutError(f"repo 不可达（本地路径不存在）: {repo}")
    sha = _git_sha(path, ref)
    return path, sha


def prepare_checkout(repo: str, ref: Optional[str], *, job_id: str) -> Checkout:
    """解析坐标并备好评测现场：本地路径只读；git URL/简写 → mirror 缓存 + worktree 隔离。"""
    path = Path(repo).expanduser()
    if path.is_dir() and not _is_git_url(repo):  # 本地目录优先（含形如 owner/repo 的相对目录）
        repo_dir, sha = resolve_checkout(repo, ref)
        return Checkout(repo_dir=repo_dir, sha=sha)

    url = _url_of(repo)
    mirror = _ensure_mirror(url)
    sha = _resolve_sha(mirror, ref)
    lock = _lock_for(mirror)
    with lock:
        ws = _cache_root() / "workspaces" / job_id
        try:
            _git(mirror, "worktree", "add", "--detach", str(ws), sha)
        except CheckoutError:
            shutil.rmtree(ws, ignore_errors=True)
            raise
    return Checkout(repo_dir=ws, sha=sha, worktree=Worktree(mirror=mirror, path=ws))


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


def remove_worktree(worktree: Worktree) -> None:
    """评测终态清理 worktree。ATP_WORKTREE_KEEP=1 保留现场（排查用）。"""
    if os.environ.get("ATP_WORKTREE_KEEP", "").strip() == "1":
        return
    lock = _lock_for(worktree.mirror)
    with lock:
        try:
            _git(worktree.mirror, "worktree", "remove", "--force", str(worktree.path))
        except CheckoutError:
            pass  # mirror 缺失/元数据异常时退化为直接删目录
        shutil.rmtree(worktree.path, ignore_errors=True)


def cleanup_stale() -> list[Path]:
    """服务启动清扫滞留 worktree（上次异常退出残留）+ 全部 mirror 元数据 prune。"""
    ws_root = _cache_root() / "workspaces"
    removed: list[Path] = []
    if ws_root.is_dir():
        for entry in ws_root.iterdir():
            mirror = _mirror_of_worktree(entry)
            if mirror is not None and (mirror / "HEAD").is_file():
                try:
                    _git(mirror, "worktree", "remove", "--force", str(entry))
                except CheckoutError:
                    shutil.rmtree(entry, ignore_errors=True)
            else:
                shutil.rmtree(entry, ignore_errors=True)  # mirror 已没了，目录可直接删
            removed.append(entry)
    repos = _cache_root() / "repos"
    if repos.is_dir():
        for mirror in repos.iterdir():
            if (mirror / "HEAD").is_file():
                try:
                    _git(mirror, "worktree", "prune")
                except CheckoutError:
                    pass
    return removed


# ---- 内部：URL 识别与缓存键 ----


def _is_git_url(repo: str) -> bool:
    return "://" in repo or repo.startswith("git@")


def _url_of(repo: str) -> str:
    """repo 坐标 → git URL。owner/repo 简写拼 ATP_GIT_BASE（缺省 git@github.com:）。"""
    if _is_git_url(repo):
        return repo
    base = os.environ.get("ATP_GIT_BASE", "git@github.com:")
    return f"{base}{repo}" if repo.endswith(".git") else f"{base}{repo}.git"


def _cache_key(url: str) -> str:
    """git URL → mirror 目录名：<owner>__<repo>.git；无法提取时 sha1 兜底。"""
    match = re.search(r"[:/]([^/:]+)/([^/]+?)(?:\.git)?$", url)
    if match:
        return f"{match.group(1)}__{match.group(2)}.git"
    return f"{hashlib.sha1(url.encode()).hexdigest()[:12]}.git"


def _cache_root() -> Path:
    return Path(os.environ.get("ATP_CACHE_ROOT", "~/.cache/autotest")).expanduser()


def _lock_for(mirror: Path) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(mirror, threading.Lock())


# ---- 内部：git 操作 ----


def _ensure_mirror(url: str) -> Path:
    """mirror 缓存：首次 clone --mirror，之后 remote update --prune 更新。失败即 repo 不可达。"""
    mirror = _cache_root() / "repos" / _cache_key(url)
    lock = _lock_for(mirror)
    with lock:
        try:
            if (mirror / "HEAD").is_file():
                _git(mirror, "remote", "update", "--prune", timeout=_GIT_NET_TIMEOUT)
            else:
                mirror.parent.mkdir(parents=True, exist_ok=True)
                _git(None, "clone", "--mirror", url, str(mirror), timeout=_GIT_NET_TIMEOUT)
        except CheckoutError as exc:
            raise CheckoutError(f"repo 不可达（mirror 同步失败）: {url}: {exc}") from exc
    return mirror


def _resolve_sha(mirror: Path, ref: Optional[str]) -> str:
    """在 mirror 上解析 ref → 实际 sha（远端通路 sha 必填，契约不再有 null 过渡）。"""
    target = ref or "HEAD"
    try:
        return _git(mirror, "rev-parse", "--verify", f"{target}^{{commit}}")
    except CheckoutError as exc:
        raise CheckoutError(f"ref 不可达: {mirror.name}@{target}") from exc


def _git_sha(repo_dir: Path, ref: Optional[str]) -> Optional[str]:
    """本地路径只读解析 sha（rev-parse，不 checkout）。"""
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
        return None  # 非 git 仓：sha 缺省（本地路径过渡）
    return out.stdout.strip()


def _mirror_of_worktree(path: Path) -> Optional[Path]:
    """由 worktree 的 .git 文件（gitdir: <mirror>/worktrees/<name>）反推所属 mirror。"""
    gitfile = path / ".git"
    try:
        content = gitfile.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not content.startswith("gitdir: "):
        return None
    gitdir = Path(content[len("gitdir: "):])
    return gitdir.parent.parent if gitdir.parent.name == "worktrees" else None


def _git(repo_dir: Optional[Path], *args: str, timeout: float = _GIT_TIMEOUT) -> str:
    cmd = ["git"] + (["-C", str(repo_dir)] if repo_dir is not None else []) + list(args)
    try:
        out = subprocess.run(  # noqa: S603 git 地址由本机配置注入
            cmd, capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CheckoutError(f"git 不可用: {exc}") from exc
    if out.returncode != 0:
        raise CheckoutError(f"git {' '.join(args)}: {out.stderr.strip()[:200]}")
    return out.stdout.strip()
