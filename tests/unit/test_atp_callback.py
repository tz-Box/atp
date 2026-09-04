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
from autotest.server import report as rp
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


# ---- D1：基线按 repo 隔离（本组是该缺陷的回归防线）----

def test_baseline_path_isolated_per_repo():
    """不同 repo → 不同基线文件；无 repo（本机 client 通路）→ 沿用全局文件。"""
    root = Path("/tmp/artifacts")
    a = rp.baseline_path_for("tz-Box/cicd_test", root)
    b = rp.baseline_path_for("tz-Box/cicd_test_slam", root)
    assert a != b and a.parent == b.parent == root / "baselines"
    assert rp.baseline_path_for(None, root) == root / "baseline.json"   # 向后兼容
    assert rp.baseline_path_for("", root) == root / "baseline.json"
    # 归一后同名的不同 repo 仍互不覆盖（摘要后缀保证唯一性）
    assert rp.baseline_slug("a/b") != rp.baseline_slug("a_b")
    # GitHub 坐标大小写不敏感 → 同一仓不得分裂成两份基线（实测库中两种写法都出现过）
    assert rp.baseline_slug("tz-Box/cicd_test") == rp.baseline_slug("tz-box/cicd_test")
    # 本地路径大小写敏感 → 不得合并
    assert rp.baseline_slug("/ws/Algo") != rp.baseline_slug("/ws/algo")


def test_finalize_two_repos_do_not_overwrite_each_other(store, tmp_path, monkeypatch):
    """★D1 回归：A 仓滚动基线后，B 仓评测既看不到 A 的基线、也不覆盖它。

    修复前两仓共用 artifacts/baseline.json：B 与 A 的 testcase_id 对不上 → 永久 new，
    且 B 滚动即抹掉 A 的基线。该症状与"多场景前缀迁移首轮全记 new"这一已知良性现象
    完全同形，因而不会被察觉——故此处以行为断言固化。
    """
    monkeypatch.setenv("AUTOTEST_ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.delenv("HUB_CALLBACK_URL", raising=False)
    monkeypatch.delenv("HUB_CALLBACK_TOKEN", raising=False)
    store.create(cid="c2", job_id="j2", repo="tz-Box/b", ref=None, sha="sha2",
                 save_baseline=True)

    def _run(cid: str, job: str, repo: str, report: dict) -> None:
        d = tmp_path / job
        d.mkdir(exist_ok=True)
        (d / "report.json").write_text(json.dumps(report), encoding="utf-8")
        cb.finalize_evaluation(
            {"cid": cid, "sha": "s", "repo": repo, "save_baseline": True},
            report, store, d, lambda _m: None)

    # A 仓先跑并滚动基线
    report_a = _report()
    _run("c1", "j1", "tz-Box/a", report_a)
    path_a = rp.baseline_path_for("tz-Box/a", tmp_path)
    assert path_a.is_file()

    # B 仓的 testcase 命名空间完全不同
    report_b = {"job_id": "j2", "error": None, "comm_health": {"warnings": []},
                "results": [{"testcase_id": "slam:tc0", "passed": True,
                             "metrics": {"ate": 0.2}, "n_records": 5}]}
    _run("c2", "j2", "tz-Box/b", report_b)

    # ① B 没读到 A 的基线（否则摘要会出现 vs_baseline: new=1 之类的错误对比）
    assert "vs_baseline" not in store.get_by_cid("c2")["summary"]
    # ② B 滚动后 A 的基线原样健在，内容未被顶替
    assert json.loads(path_a.read_text(encoding="utf-8"))["results"] == report_a["results"]
    # ③ 两份基线各自独立存在
    assert rp.baseline_path_for("tz-Box/b", tmp_path).is_file()


def test_finalize_same_repo_second_run_compares_against_own_baseline(store, tmp_path, monkeypatch):
    """同一 repo 第二次评测能对上自己的基线（隔离没有把对比功能一起隔离掉）。"""
    monkeypatch.setenv("AUTOTEST_ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.delenv("HUB_CALLBACK_URL", raising=False)
    monkeypatch.delenv("HUB_CALLBACK_TOKEN", raising=False)
    d = tmp_path / "j1"
    d.mkdir()
    (d / "report.json").write_text(json.dumps(_report()), encoding="utf-8")
    ctx = {"cid": "c1", "sha": "s", "repo": "tz-Box/a", "save_baseline": True}
    cb.finalize_evaluation(ctx, _report(), store, d, lambda _m: None)   # 首轮：建基线
    cb.finalize_evaluation(ctx, _report(), store, d, lambda _m: None)   # 次轮：应对上
    assert "vs_baseline: same=2" in store.get_by_cid("c1")["summary"]


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
    # compare 语义：tc0 指标变好 → improved；tc1 逐项持平 → same（不计入 improved）
    assert "vs_baseline: improved=1, same=1" in payload["report"]["summary"]
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


# ---- A11：场景级期望结果（总契约 §10 A11）----

def _report_with(expects: dict, results: list) -> dict:
    return {"job_id": "j1", "error": None, "comm_health": {"warnings": []},
            "results": results,
            "scenarios": [{"id": k, "expect": v} for k, v in expects.items()]}


def test_a11_expected_failure_does_not_turn_run_red():
    """★expect=fail 的场景确实失败 → 整次评测仍 success。

    修复前 conclusion_of 的一行「任一 testcase passed=False 即 failure」，会让四个消费者
    同时被喂错误事实：check-run ❌ / PMS「失败必通知」推飞书 / Hub 概览失败数 / 通路4
    交付物冻结永远答「没测过」。而 advisory 帮不上忙——autotest 恒 false，本就该参与聚合。
    """
    rep = _report_with({"smoke": "pass", "degraded": "fail"},
                       [{"testcase_id": "smoke:tc0", "passed": True},
                        {"testcase_id": "degraded:tc0", "passed": False},
                        {"testcase_id": "degraded:tc1", "passed": False}])
    assert cb._expects_of(rep) == {"smoke": "pass", "degraded": "fail"}
    from autotest.server.evaluations import conclusion_of
    assert conclusion_of(None, rep["results"], cb._expects_of(rep)) == "success"


def test_a11_unexpected_pass_is_failure():
    """★expect=fail 的场景**通过**了 → failure。

    这是 A11 想表达、而此前体系里完全无法表达的信号：degraded 变绿说明判据
    已经不能拒绝坏算法了。它不是「意外失败」——它根本不是失败，是判据坏了。
    """
    from autotest.server.evaluations import conclusion_of, scenario_outcomes
    rep = _report_with({"degraded": "fail"},
                       [{"testcase_id": "degraded:tc0", "passed": True}])
    assert conclusion_of(None, rep["results"], cb._expects_of(rep)) == "failure"
    o = scenario_outcomes(rep["results"], cb._expects_of(rep))[0]
    # 四态须由 expected/actual 推导；只看 met 会把它和「意外失败」混为一谈
    assert (o["expected"], o["actual"], o["met"]) == ("fail", "pass", False)


def test_a11_metrics_shape_and_raw_facts_preserved():
    """metrics 形状符合契约 A11；testcase 计数保持原始事实，不因预期被改写。"""
    rep = _report_with({"smoke": "pass", "degraded": "fail"},
                       [{"testcase_id": "smoke:tc0", "passed": True},
                        {"testcase_id": "degraded:tc0", "passed": False},
                        {"testcase_id": "degraded:tc1", "passed": False}])
    m = cb.build_metrics(rep, {"same": 3})
    assert set(m) == {"scenarios", "scenario_counts", "testcases", "vs_baseline"}
    assert set(m["scenarios"][0]) == {"name", "expected", "actual", "met", "testcases"}
    assert m["scenario_counts"] == {"met": 2, "unmet": 0}
    # ★原始事实不改写：degraded 的两条确实失败了，否则没人知道它跑没跑过
    assert m["testcases"] == {"passed": 1, "failed": 2, "total": 3}


def test_a11_absent_expects_keeps_old_semantics():
    """未声明 expect（存量仓、本机通路）→ 沿用原语义，行为不变。"""
    from autotest.server.evaluations import conclusion_of
    results = [{"testcase_id": "tc0", "passed": False}]
    assert conclusion_of(None, results) == "failure"
    assert conclusion_of(None, results, {}) == "failure"
