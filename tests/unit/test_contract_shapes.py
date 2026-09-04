"""报文结构与总契约逐字段一致（协同轨 G1 在 ATP 侧的第一块）。

## 这套测试为什么重要

四平台靠一份契约并行开发，而契约是 Markdown——**没有任何机制会在报文被改坏时发出声音**。
做法承自 ARS owner 的建议：解析契约原文取字段集与实现逐字段比对，契约一改这里就红。

## ★为什么读入库副本，而不是测试时现读契约（2026-09-04 修正）

初版直接读 `__temp__/cicd_hub` 软链下的契约，软链不可达就整文件 skip。
但 `__temp__/` 在 .gitignore 里——**CI checkout 出来根本没有它，8 项断言会全部 skip：
绿着，什么都没测**。而这套测试本来就是为了防「约定了但没人执行」，它自己却成了那个形态。
（Hub owner 在其仓用 `git archive` 模拟 CI checkout 实测命中并给出修法，ATP 侧同形。）

现在拆成两件事：
- **契约检查**读入库副本 `tests/fixtures/contract_shapes.json`——副本缺失即 **assert 失败**
  （副本缺失是仓的问题，应当红），任何环境都会真跑；
- **「副本 vs 上游是否漂移」单列一项，只有它可以 skip**——它检查的是同步状态而非契约本身，
  拿不到上游时跳过合理。**关键在这个区分：不能因为拿不到上游，就把契约检查一并跳掉。**

副本由 `scripts/sync_contract_shapes.py` 生成；契约改动后跑一次即可（像 lockfile）。

## 方向语义（收发两侧要求不同，不能一刀切）

- ATP 作**发送方**（回调 / health / status 响应）：契约字段必须**全部发出**，
  可以多发（新增字段向后兼容），不可漏发。
- ATP 作**接收方**（submit 请求）：契约字段必须**全部认得**，
  漏认会让 Hub 传的东西被静默丢弃——R12 那类「看起来在跑，其实在错」正是如此。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from autotest import CONTRACT_VERSION

_ROOT = Path(__file__).resolve().parents[2]
_FIXTURE = _ROOT / "tests" / "fixtures" / "contract_shapes.json"
_SYNC = _ROOT / "scripts" / "sync_contract_shapes.py"
_UPSTREAM = _ROOT / "__temp__" / "cicd_hub" / "docs" / "PatrolBox-通信与接口总契约.md"


def _shape(name: str) -> dict:
    """取入库副本里的某个报文结构。副本缺失 → 失败（不是 skip）。"""
    assert _FIXTURE.is_file(), (
        f"契约副本缺失: {_FIXTURE.relative_to(_ROOT)}。"
        f"跑 python3 scripts/sync_contract_shapes.py 生成。"
        f"（此处刻意 assert 而非 skip——副本缺失是仓的问题，绿着什么都没测才是要防的）")
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))[name]


# ── 唯一允许 skip 的一项：副本与上游是否漂移 ──────────────────────────────

@pytest.mark.skipif(not _UPSTREAM.is_file(),
                    reason="上游契约不可达（__temp__/cicd_hub 软链未建）——"
                           "本项检查的是同步状态，跳过合理；契约检查本身不受影响")
def test_契约副本未与上游漂移():
    r = subprocess.run([sys.executable, str(_SYNC), "--check"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr or r.stdout


# ── ATP 作为发送方：契约字段必须全部发出 ────────────────────────────────

def test_health_响应覆盖契约字段():
    """§4.8 `GET /atp/health`。contract 字段是 v1.7-R12 的硬要求。"""
    shape = _shape("atp_health")
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
    shape = _shape("ci_callback")
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
    shape = _shape("atp_evaluation_status")
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
    shape = _shape("atp_evaluations_submit")
    from autotest.server.http import EvaluationRequest

    known = set(EvaluationRequest.model_fields)
    unknown = set(shape) - known
    assert not unknown, f"submit 请求模型不认得契约字段: {unknown}"
