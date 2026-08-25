"""M-E2 checkout 单测：git URL/简写识别、mirror 缓存、worktree 隔离与清理、启动清扫。

不依赖网络：远端用本地 bare 仓以 `file://` URL 模拟（走与 https/ssh 相同的 URL 通路）。
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from autotest.server import checkout as ck

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git 不可用")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def _commit(repo: Path, filename: str, content: str, message: str) -> str:
    (repo / filename).write_text(content, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", message)
    return _git(repo, "rev-parse", "HEAD")


def _make_remote(tmp_path: Path) -> tuple[Path, str, str]:
    """源仓（含 scenario.yaml）+ bare 远端，返回（bare 路径， file:// URL, 初始 sha）。"""
    src = tmp_path / "src"
    src.mkdir()
    _git(src, "init", "-q", "-b", "main")
    sha = _commit(src, "scenario.yaml", "launch: true\n", "init")
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(src), str(bare)],
                   check=True, capture_output=True)
    return bare, f"file://{bare}", sha


@pytest.fixture()
def cache(tmp_path, monkeypatch):
    root = tmp_path / "cache"
    monkeypatch.setenv("ATP_CACHE_ROOT", str(root))
    return root


# ---- URL 识别与缓存键（纯函数） ----

def test_url_of_passthrough_full_url():
    assert ck._url_of("git@github.com:org/repo.git") == "git@github.com:org/repo.git"
    assert ck._url_of("https://github.com/org/repo.git") == "https://github.com/org/repo.git"
    assert ck._url_of("file:///tmp/x.git") == "file:///tmp/x.git"


def test_url_of_shorthand_joins_base(monkeypatch):
    assert ck._url_of("org/repo") == "git@github.com:org/repo.git"
    assert ck._url_of("org/repo.git") == "git@github.com:org/repo.git"
    monkeypatch.setenv("ATP_GIT_BASE", "https://hub.example.com/")
    assert ck._url_of("org/repo") == "https://hub.example.com/org/repo.git"


def test_cache_key_owner_repo():
    assert ck._cache_key("git@github.com:org/repo.git") == "org__repo.git"
    assert ck._cache_key("https://github.com/org/repo.git") == "org__repo.git"
    assert ck._cache_key("https://github.com/org/repo") == "org__repo.git"
    key = ck._cache_key("no-separator-at-all")
    assert key.endswith(".git") and len(key) == 12 + 4  # sha1 兜底


# ---- mirror + worktree ----

def test_prepare_remote_creates_mirror_and_worktree(tmp_path, cache):
    bare, url, sha = _make_remote(tmp_path)
    co = ck.prepare_checkout(url, None, job_id="autotest-j1")
    assert co.sha == sha
    assert co.worktree is not None
    assert co.repo_dir == cache / "workspaces" / "autotest-j1"
    assert (co.repo_dir / "scenario.yaml").read_text(encoding="utf-8") == "launch: true\n"
    # mirror 已建且为 bare；命名取 URL 尾段 owner__repo
    mirrors = list((cache / "repos").iterdir())
    assert len(mirrors) == 1 and mirrors[0].name.endswith("__origin.git")
    assert (mirrors[0] / "HEAD").is_file()


def test_prepare_remote_ref_isolation(tmp_path, cache):
    """两个分支内容不同 → 两个 job 的 worktree 各自看到对应 ref 的内容（互不污染）。"""
    bare, url, sha_main = _make_remote(tmp_path)
    src = tmp_path / "src"
    _git(src, "checkout", "-q", "-b", "feature")
    sha_feat = _commit(src, "scenario.yaml", "launch: feature\n", "feature change")
    _git(src, "push", "-q", str(bare), "feature")

    co_main = ck.prepare_checkout(url, "main", job_id="j-main")
    co_feat = ck.prepare_checkout(url, "feature", job_id="j-feat")
    assert (co_main.repo_dir / "scenario.yaml").read_text() == "launch: true\n"
    assert (co_feat.repo_dir / "scenario.yaml").read_text() == "launch: feature\n"
    assert co_main.sha == sha_main and co_feat.sha == sha_feat


def test_prepare_remote_mirror_reused_and_updated(tmp_path, cache):
    """第二次 submit 同 repo：复用 mirror（fetch 更新）→ 新 commit 可见。"""
    bare, url, sha1 = _make_remote(tmp_path)
    ck.prepare_checkout(url, None, job_id="j1")
    sha2 = _commit(tmp_path / "src", "scenario.yaml", "launch: v2\n", "v2")
    _git(tmp_path / "src", "push", "-q", str(bare), "main")

    co2 = ck.prepare_checkout(url, None, job_id="j2")
    assert co2.sha == sha2  # remote update 拿到新提交
    assert (co2.repo_dir / "scenario.yaml").read_text() == "launch: v2\n"
    assert len(list((cache / "repos").iterdir())) == 1  # 仍只有一个 mirror


def test_prepare_remote_ref_unreachable_leaves_no_worktree(tmp_path, cache):
    _, url, _ = _make_remote(tmp_path)
    with pytest.raises(ck.CheckoutError, match="ref 不可达"):
        ck.prepare_checkout(url, "no-such-ref", job_id="j-x")
    assert not (cache / "workspaces" / "j-x").exists()


def test_prepare_remote_repo_unreachable(tmp_path, cache):
    with pytest.raises(ck.CheckoutError, match="repo 不可达"):
        ck.prepare_checkout("file:///no/such/remote.git", None, job_id="j-y")
    assert not (cache / "workspaces").exists() or not list((cache / "workspaces").iterdir())


def test_prepare_local_path_untouched_by_worktree(tmp_path, cache):
    """本地目录通路：不建 mirror/worktree，sha 只读回填。"""
    repo = tmp_path / "local"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    sha = _commit(repo, "scenario.yaml", "launch: true\n", "init")
    co = ck.prepare_checkout(str(repo), "HEAD", job_id="j-local")
    assert co.worktree is None and co.repo_dir == repo and co.sha == sha
    assert not (cache / "repos").exists()


# ---- 清理与启动清扫 ----

def test_remove_worktree_cleans_dir_and_metadata(tmp_path, cache):
    _, url, _ = _make_remote(tmp_path)
    co = ck.prepare_checkout(url, None, job_id="j1")
    mirror = co.worktree.mirror
    assert "j1" in _git(mirror, "worktree", "list", "--porcelain")
    ck.remove_worktree(co.worktree)
    assert not co.worktree.path.exists()
    assert "j1" not in _git(mirror, "worktree", "list", "--porcelain")


def test_remove_worktree_keep_env(tmp_path, cache, monkeypatch):
    _, url, _ = _make_remote(tmp_path)
    co = ck.prepare_checkout(url, None, job_id="j1")
    monkeypatch.setenv("ATP_WORKTREE_KEEP", "1")
    ck.remove_worktree(co.worktree)
    assert co.worktree.path.exists()  # 保留现场（排查用）


def test_cleanup_stale_sweeps_leftover(tmp_path, cache):
    """模拟异常退出：滞留 worktree（含 mirror 元数据）→ 启动清扫一并移除 + prune。"""
    _, url, _ = _make_remote(tmp_path)
    co = ck.prepare_checkout(url, None, job_id="j-stale")
    mirror = co.worktree.mirror
    orphan = cache / "workspaces" / "j-orphan"  # 无 git 元数据的孤儿目录
    orphan.mkdir(parents=True)
    (orphan / "junk").write_text("x")

    removed = ck.cleanup_stale()
    assert set(removed) == {co.worktree.path, orphan}
    assert not co.worktree.path.exists() and not orphan.exists()
    assert "j-stale" not in _git(mirror, "worktree", "list", "--porcelain")
    assert ck.cleanup_stale() == []  # 二次清扫幂等


def test_cleanup_stale_no_cache_root(tmp_path, monkeypatch):
    monkeypatch.setenv("ATP_CACHE_ROOT", str(tmp_path / "nonexistent"))
    assert ck.cleanup_stale() == []
