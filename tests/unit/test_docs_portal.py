"""文档门户路由单测（/docs 系列：门户页/清单/md 原文/路径穿越防护）。

_DOCS_DIR 指向仓内真实 docs/（只读）；门户页与 md 原文零认证（内容非敏感，降低阅读门槛）。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from autotest.server.http import create_app


@pytest.fixture
def client():
    return TestClient(create_app(service=None))  # docs 路由不触 service


def test_portal_page_served(client):
    resp = client.get("/docs")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "ATP 文档" in resp.text
    assert "/docs/marked.min.js" in resp.text  # vendor 本地渲染器（无公网 CDN 依赖）


def test_marked_js_served(client):
    resp = client.get("/docs/marked.min.js")
    assert resp.status_code == 200
    assert "marked" in resp.text[:500].lower()


def test_index_order_and_titles(client):
    items = client.get("/docs/api/index").json()["items"]
    files = [it["file"] for it in items]
    # 文件名数字前缀即学习路径序：00 导读 → 01..05 五关 → 06 速查 → 07 附录
    assert files[0] == "00-导读与学习路径.md"
    assert [f[:2] for f in files[:8]] == [f"{i:02d}" for i in range(8)]
    assert files == sorted(files)  # 自然名序即展示序（新增文档选号落位，无需改服务端）
    assert all(it["title"] for it in items)  # 每篇取到一级标题
    assert not any(f.startswith("scheme") for f in files)  # 内部资料不暴露


def test_md_raw_served(client):
    resp = client.get("/docs/md/00-导读与学习路径.md")
    assert resp.status_code == 200
    assert "markdown" in resp.headers["content-type"]
    assert resp.text.startswith("# 导读")


@pytest.mark.parametrize("name", [
    "../pyproject.toml",          # 路径穿越
    "scheme/xxx.md",              # 子目录（内部资料）不暴露
    "nonexistent.md",             # 不存在
    "readme.txt",                 # 非 md
])
def test_md_rejected(client, name):
    assert client.get(f"/docs/md/{name}").status_code == 404
