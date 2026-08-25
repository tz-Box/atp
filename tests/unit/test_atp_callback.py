"""M-E3 单测：主动回调 Hub——summarize 内化 / 基线先对比后滚动 / 退避重试 / 失败留痕。

mock Hub = 本机 http.server 线程；不依赖 tzcomm daemon。
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from autotest.server import callback as cb
from autotest.server.evaluations import EvaluationStore


class _MockHub(BaseHTTPRequestHandler):
    received: list[dict] = []
    auths: list[str] = []
    fail_times: int = 0
    got = threading.Event()

    def do_POST(self):  # noqa: N802 stdlib 约定
        _MockHub.auths.append(self.headers.get("Authorization", ""))
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _MockHub.received.append(body)
        _MockHub.got.set()
        if _MockHub.fail_times > 0:
            _MockHub.fail_times -= 1
            self.send_response(500)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

    def log_message(self, *args):  # 静音
        pass


@pytest.fixture()
def hub():
    _MockHub.received, _MockHub.auths, _MockHub.fail_times = [], [], 0
    _MockHub.got.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _MockHub)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/api/ci/callback"
    yield url
    server.shutdown()
    server.server_close()


def _report(passed=(True, True), error=None) -> dict:
    results = [
        {"testcase_id": f"tc{i}", "passed": p, "metrics": {"ape": 0.01 * (i + 1)}, "n_records": 10}
        for i, p in enumerate(passed)
    ]
    return {"job_id": "j1", "results": results, "error": error,
            "comm_health": {"warnings": []}}


# ---- summarize ----

def test_summarize_passed_with_metrics():
    text = cb.summarize(_report())
    assert text.startswith("2/2 passed")
    assert "tc0: passed (ape=0.0100)" in text


def test_summarize_failure_report():
    assert cb.summarize(_report(error="RuntimeError: boom")) == "评测失败: RuntimeError: boom"


def test_summarize_dataflow_and_warnings_and_baseline():
    report = {"job_id": "j", "error": None,
              "results": [{"testcase_id": "live", "passed": None, "metrics": None, "n_records": 7}],
              "comm_health": {"warnings": ["SUT 侧累计丢包率 2.00% 超阈值"]}}
    text = cb.summarize(report, changes={"improved": 1, "regressed": 2})
    assert "1/1 passed" not in text  # passed=None 不计入
    assert "live: 数据流验证 records=7" in text
    assert "通信告警: SUT 侧累计丢包率" in text
    assert text.endswith("vs_baseline: improved=1, regressed=2")


# ---- build_payload ----

def test_build_payload_shape():
    p = cb.build_payload("chk_x", "abc123", "success", "2/2 passed")
    assert p["correlation_id"] == "chk_x"
    assert p["sha"] == "abc123"
    assert p["check_type"] == "autotest"
    assert p["conclusion"] == "success"
    assert p["report"] == {"summary": "2/2 passed"}  # run_url 省略（v1.5 归 Hub）
    assert p["finished_at"].endswith("+00:00")


def test_build_payload_sha_none_empty():
    assert cb.build_payload("c", None, "success", "s")["sha"] == ""


# ---- 发送与重试 ----

def test_post_callback_payload_and_auth(hub):
    resp = cb.post_callback(hub, "tok", cb.build_payload("c1", "s", "success", "sum"))
    assert resp == {"ok": True}
    assert _MockHub.auths == ["Bearer tok"]
    assert _MockHub.received[0]["correlation_id"] == "c1"


def test_send_with_retry_recovers(hub, monkeypatch):
    monkeypatch.setattr(cb, "_RETRY_BACKOFF", (0.05, 0.05, 0.05))
    _MockHub.fail_times = 2  # 前两次 500
    logs: list[str] = []
    ok = cb.send_with_retry(hub, "tok", cb.build_payload("c2", "s", "success", "sum"), logs.append)
    assert ok is True
    assert len(_MockHub.received) == 3
    assert sum("失败" in line for line in logs) == 2


def test_send_with_retry_gives_up(hub, monkeypatch):
    monkeypatch.setattr(cb, "_RETRY_BACKOFF", (0.01, 0.01, 0.01))
    _MockHub.fail_times = 99
    ok = cb.send_with_retry(hub, "tok", cb.build_payload("c3", "s", "success", "sum"), lambda m: None)
    assert ok is False
    assert len(_MockHub.received) == 4  # 首发 + 3 次重试


# ---- finalize_evaluation 总编排 ----

@pytest.fixture()
def store(tmp_path):
    s = EvaluationStore(tmp_path / "atp.db")
    s.create(cid="c1", job_id="j1", repo="/r", ref=None, sha="sha1", save_baseline=True)
    return s


def test_finalize_without_callback_config(store, tmp_path, monkeypatch):
    """未配置 HUB_CALLBACK_*：跳过发送，终态+摘要照常落档，基线滚动不依赖回调配置。"""
    monkeypatch.setenv("AUTOTEST_ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.delenv("HUB_CALLBACK_URL", raising=False)
    monkeypatch.delenv("HUB_CALLBACK_TOKEN", raising=False)
    report_dir = tmp_path / "j1"
    report_dir.mkdir()
    report = _report()
    (report_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")
    logs: list[str] = []

    cb.finalize_evaluation({"cid": "c1", "sha": "sha1", "save_baseline": True},
                           report, store, report_dir, logs.append)

    row = store.get_by_cid("c1")
    assert row["status"] == "success"
    assert "2/2 passed" in row["summary"]
    assert (tmp_path / "baseline.json").is_file()  # save_baseline=True + success → 滚动
    assert any("跳过发送" in line for line in logs)


def test_finalize_sends_callback_with_vs_baseline(store, tmp_path, monkeypatch, hub):
    """配置后：先发回调（摘要含 vs_baseline），后滚动基线（先对比后滚动语义）。"""
    monkeypatch.setenv("AUTOTEST_ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setenv("HUB_CALLBACK_URL", hub)
    monkeypatch.setenv("HUB_CALLBACK_TOKEN", "tok")
    # 预置基线：tc0 指标更差 → 本轮 improved
    baseline = _report(passed=(True, True))
    baseline["results"][0]["metrics"]["ape"] = 0.5
    (tmp_path / "baseline.json").write_text(json.dumps(baseline), encoding="utf-8")
    report_dir = tmp_path / "j1"
    report_dir.mkdir()
    report = _report()
    (report_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")

    cb.finalize_evaluation({"cid": "c1", "sha": "sha1", "save_baseline": True},
                           report, store, report_dir, lambda m: None)

    assert _MockHub.got.wait(timeout=5)
    payload = _MockHub.received[0]
    assert payload["correlation_id"] == "c1" and payload["sha"] == "sha1"
    assert payload["conclusion"] == "success"
    # 既有 compare 语义（M-D3）：deltas 全非正（含零差）即 improved → tc0 改善 + tc1 持平 = improved=2
    assert "vs_baseline: improved=2" in payload["report"]["summary"]
    assert _MockHub.auths == ["Bearer tok"]
    # 先对比后滚动：回调摘要基于旧基线，滚动后的新基线=本轮报告
    rolled = json.loads((tmp_path / "baseline.json").read_text(encoding="utf-8"))
    assert rolled["results"][0]["metrics"]["ape"] == pytest.approx(0.01)


def test_finalize_failure_no_baseline_roll(store, tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOTEST_ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.delenv("HUB_CALLBACK_URL", raising=False)
    monkeypatch.delenv("HUB_CALLBACK_TOKEN", raising=False)
    report_dir = tmp_path / "j1"
    report_dir.mkdir()
    report = _report(passed=(True, False))
    (report_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")

    cb.finalize_evaluation({"cid": "c1", "sha": "sha1", "save_baseline": True},
                           report, store, report_dir, lambda m: None)

    assert store.get_by_cid("c1")["status"] == "failure"
    assert not (tmp_path / "baseline.json").exists()  # failure 不滚动


def test_finalize_callback_failure_marks_store(store, tmp_path, monkeypatch, hub):
    monkeypatch.setenv("AUTOTEST_ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setenv("HUB_CALLBACK_URL", hub)
    monkeypatch.setenv("HUB_CALLBACK_TOKEN", "tok")
    monkeypatch.setattr(cb, "_RETRY_BACKOFF", (0.01, 0.01, 0.01))
    _MockHub.fail_times = 99
    report_dir = tmp_path / "j1"
    report_dir.mkdir()
    (report_dir / "report.json").write_text(json.dumps(_report()), encoding="utf-8")

    cb.finalize_evaluation({"cid": "c1", "sha": "sha1", "save_baseline": False},
                           _report(), store, report_dir, lambda m: None)

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if store.get_by_cid("c1")["callback_error"]:
            break
        time.sleep(0.05)
    assert "回调最终失败" in store.get_by_cid("c1")["callback_error"]
    assert store.get_by_cid("c1")["status"] == "success"  # 结果不丢，轮询兜底可拿回
