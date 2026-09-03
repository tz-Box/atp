"""报文结构与总契约逐字段一致（协同轨 G1 在 ATP 侧的第一块）。

## 这套测试为什么重要

四平台靠一份契约并行开发，而契约是 Markdown——**没有任何机制会在报文被改坏时发出声音**。
本轮已有现成例证：v1.7 还没批准，ARS 仓内文档就已与总契约说法不同；Hub 的 267 个测试
自举闭环静默断开一周无人发现。共同点都不是有人做错了什么，而是没有告警机制。

做法承自 ARS owner 的建议（其 `tests/test_result.py` 是同一路子）：
**直接解析契约 Markdown 里的 json/jsonc 块取字段集来比对——契约一改，这里就红。**

方向语义（收发两侧要求不同，不能一刀切）：
- ATP 是**发送方**的报文（回调、health、status 响应）：契约字段必须**全部发出**，
  可以多发（新增字段向后兼容），不可漏发。
- ATP 是**接收方**的报文（submit 请求）：契约字段必须**全部认得**，
  漏认会让 Hub 传的东西被静默丢弃。

契约经 `__temp__/cicd_hub` 软链引用（唯一事实源在 Hub 仓，本仓不留副本）。
软链不可达时跳过——但 CI 上应让它真正跑起来（见 .github/workflows/ci.yml 的说明）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from autotest import CONTRACT_VERSION

_CONTRACT = (Path(__file__).resolve().parents[2]
             / "__temp__" / "cicd_hub" / "docs" / "PatrolBox-通信与接口总契约.md")

pytestmark = pytest.mark.skipif(
    not _CONTRACT.is_file(), reason="总契约不可达（__temp__/cicd_hub 软链未建）")


def _strip_jsonc(text: str) -> str:
    """去掉 // 注释，但不碰字符串内部（契约里的值含 `<分支; 通路1=...>` 这类文本）。"""
    out, in_str, esc, i = [], False, False, 0
    while i < len(text):
        c = text[i]
        if in_str:
            out.append(c)
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
            out.append(c)
        elif c == "/" and i + 1 < len(text) and text[i + 1] == "/":
            while i < len(text) and text[i] != "\n":
                i += 1
            continue
        else:
            out.append(c)
        i += 1
    return "".join(out)


def _block_after(heading_pattern: str) -> dict:
    """取某个标题行之后的第一个 ```json(c) 块并解析。"""
    md = _CONTRACT.read_text(encoding="utf-8")
    m = re.search(heading_pattern + r".*?```jsonc?\n(.*?)```", md, re.S)
    assert m, f"契约里找不到 {heading_pattern!r} 之后的 json 块——章节标题变了？"
    return json.loads(_strip_jsonc(m.group(1)))


# ── ATP 作为发送方：契约字段必须全部发出 ────────────────────────────────

def test_health_响应覆盖契约字段():
    """§4.8 `GET /atp/health`。contract 字段是 v1.7-R12 的硬要求。"""
    shape = _block_after(r"\*\*`GET /atp/health`\*\*")
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from autotest.server.http import create_app

    # health 只用到 queue_depth，用轻量替身避免起真实评测服务（本文件是纯结构测试）
    stub = SimpleNamespace(queue_depth=0, job_count=0)
    got = TestClient(create_app(service=stub)).get("/atp/health").json()
    missing = set(shape) - set(got)
    assert not missing, f"/atp/health 漏发契约字段: {missing}"
    assert got["contract"] == CONTRACT_VERSION


def test_回调报文覆盖契约字段():
    """§4.3 `POST /api/ci/callback`。ATP 是发起方，漏发字段 Hub 就归位不了。"""
    shape = _block_after(r"Authorization: Bearer <hub\.callback_token>")
    from autotest.server.callback import build_payload

    got = build_payload("chk_1", "sha1", "success", "2/2 passed")
    missing = set(shape) - set(got)
    assert not missing, f"回调报文漏发契约字段: {missing}"
    # report 子对象：summary 必发；run_url 契约明示"发起方省略"；metrics 为可选
    assert "summary" in got["report"]
    assert set(got["report"]) <= set(shape["report"]), \
        f"report 出现契约未定义的字段: {set(got['report']) - set(shape['report'])}"


def test_status_响应覆盖契约字段(tmp_path):
    """§4.8 `GET /atp/evaluations/{job_id}` 终态响应。Hub 轮询兜底靠它归位。"""
    shape = _block_after(r"\*\*`GET /atp/evaluations/\{job_id\}`\*\*")
    from autotest.server.evaluations import EvaluationStore

    store = EvaluationStore(tmp_path / "atp.db")
    store.create(cid="c1", job_id="j1", repo="o/r", ref="main", sha="sha1")
    store.update_terminal(cid="c1", status="success", summary="1/1 passed",
                          finished_at="2026-09-04T00:00:00+00:00")
    row = store.get_by_job_id("j1")
    got = {"job_id": row["job_id"], "status": row["status"], "sha": row["sha"],
           "report": {"summary": row["summary"], "run_url": None},
           "finished_at": row["finished_at"]}
    missing = set(shape) - set(got)
    assert not missing, f"status 响应漏发契约字段: {missing}"
    assert set(shape["report"]) <= set(got["report"])


# ── ATP 作为接收方：契约字段必须全部认得 ────────────────────────────────

def test_submit_请求认得契约全部字段():
    """§4.8 `POST /atp/evaluations`。漏认会让 Hub 传的东西被静默丢弃——
    R12 那类"看起来在跑，其实在错"的故障正是这么来的。"""
    shape = _block_after(r"\*\*`POST /atp/evaluations`\*\*")
    from autotest.server.http import EvaluationRequest

    known = set(EvaluationRequest.model_fields)
    unknown = set(shape) - known
    assert not unknown, f"submit 请求模型不认得契约字段: {unknown}"
