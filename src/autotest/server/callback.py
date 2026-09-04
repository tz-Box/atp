"""评测完成主动回调 Hub（v1.5 §4.3/§4.8，M-E3）。

发起方由 workflow 末步脚本改为 ATP server：评测结束（含失败）自动 POST
Hub ``/api/ci/callback``（Bearer ``hub.callback_token``），报文沿用既有定义。
配置为静态环境变量（``HUB_CALLBACK_URL`` / ``HUB_CALLBACK_TOKEN``，与 CI 脚本同名零迁移）；
未配置时跳过发送（本机开发场景），终态/摘要/基线滚动照常落档。

summarize 逻辑内化自 ``examples/ci/report.py``（该脚本保留为算法仓 GHA 自测备选路径，
纯标准库可独立运行；此处适配 server report.json 形状：以 error 键判定 ok）。
回调发送在独立 daemon 线程（不阻塞 M-E5 串行 worker）；失败按 1s/5s/15s 退避重试，
最终失败记 session.log + evaluations.callback_error——结果不丢，Hub 轮询兜底（M-E4）可拿回。
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from .artifacts import ArtifactRecorder, artifacts_root
from .evaluations import EvaluationStore, conclusion_of, scenario_outcomes
from .report import baseline_path_for, compare, load_baseline, save_baseline

_CALLBACK_TIMEOUT = 15.0
_RETRY_BACKOFF = (1.0, 5.0, 15.0)  # 首发失败后的三次退避（共 4 发）


# ---- 摘要（内化 examples/ci/report.py，适配 server report.json 形状）----

def summarize(report: dict, changes: Optional[dict] = None) -> str:
    """report.json → 结构化摘要文本（passed/n_records/metrics 概览 + vs_baseline 计数）。"""
    if report.get("error"):
        return f"评测失败: {report['error']}"
    results = report.get("results", [])
    n_passed = sum(1 for r in results if r.get("passed"))
    expects = _expects_of(report)
    if expects:
        # A11：抬头改报「场景符合度」。仍保留 testcase 计数——它是原始事实，
        # 不因预期而改写（否则没人知道 expect=fail 的场景到底跑没跑过）。
        outcomes = scenario_outcomes(results, expects)
        met = sum(1 for o in outcomes if o["met"])
        head = (f"{met}/{len(outcomes)} 场景符合预期"
                f"（testcase {n_passed}/{len(results)} passed）")
    else:
        head = f"{n_passed}/{len(results)} passed"
    parts = []
    for r in results:
        # 三分支，不能按「有无 metrics」二分：<runtime> / <scenario> 这类失败条目
        # 本就没有 metrics（它们压根没跑到打分），此前被一律标成「数据流验证」——
        # 而它们恰恰是**装配/环境失败**，是最需要被看见的那类。消费方的正则只认
        # passed|failed，于是这些行连匹配都匹配不到，在逐场景清单里静默消失。
        # （2026-09-05 生成真实夹具时发现；手编样例覆盖不到这条路径。）
        if r.get("passed") is False:
            detail = (", ".join(f"{k}={v:.4f}" for k, v in r["metrics"].items())
                      if r.get("metrics") else (r.get("error") or "无指标"))
            parts.append(f"{r['testcase_id']}: failed ({detail})")
        elif r.get("passed") is True:
            metrics = ", ".join(f"{k}={v:.4f}" for k, v in (r.get("metrics") or {}).items())
            parts.append(f"{r['testcase_id']}: passed ({metrics})" if metrics
                         else f"{r['testcase_id']}: passed")
        else:  # passed is None —— 未打分（场景省略 checker），不是失败
            parts.append(f"{r['testcase_id']}: 数据流验证 records={r.get('n_records', 0)}")
    warnings = (report.get("comm_health") or {}).get("warnings") or []
    if warnings:
        parts.append("通信告警: " + "; ".join(warnings))
    if changes:  # vs_baseline 变化计数（regressed 为回归，需关注）
        parts.append("vs_baseline: " + ", ".join(f"{k}={v}" for k, v in sorted(changes.items())))
    return "; ".join([head, *parts])


def _expects_of(report: dict) -> dict:
    """从 report 的 scenarios 段取场景期望（A11）。report 自带该信息，回调无需另传。"""
    return {sc["id"]: sc.get("expect", "pass")
            for sc in (report.get("scenarios") or []) if sc.get("id")}


def build_metrics(report: dict, changes: Optional[dict] = None) -> dict:
    """结构化指标（总契约 §4.3 report.metrics，A11 定形）。

    存在的理由：Hub console 此前**正则解析 summary 文本**取逐场景结论，
    而 summary 只是给人看的散文——ATP 改一次措辞就会静默打断消费方
    （2026-09 改 vs_baseline 分类即是一例）。结构化后该耦合消失。

    ★消费方须用 expected/actual 推导四态，不要只看 met：
      pass/pass=通过　fail/fail=预期内失败(灰)　pass/fail=意外失败(红)
      fail/pass=**预期外通过**(红) —— 它不是失败，是**判据坏了**的信号。
    """
    results = report.get("results", [])
    outcomes = scenario_outcomes(results, _expects_of(report))
    metrics: dict = {
        "scenarios": outcomes,
        "scenario_counts": {"met": sum(1 for o in outcomes if o["met"]),
                            "unmet": sum(1 for o in outcomes if not o["met"])},
        "testcases": {
            "passed": sum(1 for r in results if r.get("passed") is True),
            "failed": sum(1 for r in results if r.get("passed") is False),
            "total": len(results),
        },
    }
    if changes:
        metrics["vs_baseline"] = changes
    return metrics


def regression_changes(report: dict, repo: Optional[str] = None) -> Optional[dict]:
    """与该 repo 的基线对比（先对比后滚动），返回 changes 计数；无基线返回 None。

    D1：基线按 repo 隔离。全局单文件时多算法仓会互相覆盖，双方回归对比同时
    退化为永久 `new`（且与"多场景前缀迁移首轮全记 new"这一已知良性现象同形，
    不会被察觉）。repo 为空（本机 client 通路）沿用全局文件，行为不变。
    """
    baseline = load_baseline(baseline_path_for(repo, artifacts_root()))
    if baseline is None:
        return None
    changes: dict[str, int] = {}
    for row in compare(baseline, report):
        changes[row["change"]] = changes.get(row["change"], 0) + 1
    return changes


# ---- 报文与发送 ----

def build_payload(cid: str, sha: Optional[str], conclusion: str, summary: str,
                  metrics: Optional[dict] = None) -> dict:
    """组回调载荷（§4.3）：cid + 实际 sha + check_type + conclusion + report + finished_at。
    run_url 语义 v1.5 归 Hub（console 详情页），ATP 省略。"""
    return {
        "correlation_id": cid,
        "sha": sha or "",  # v1.2 起必带；M-E2 前非 git 仓过渡为空串
        "check_type": "autotest",
        "conclusion": conclusion,
        "report": {"summary": summary, **({"metrics": metrics} if metrics else {})},
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }


def post_callback(url: str, token: str, payload: dict) -> dict:
    """POST Hub 回调（Bearer 鉴权），返回响应 JSON。幂等：同 cid 重复返回 duplicate=true。"""
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=_CALLBACK_TIMEOUT) as resp:  # noqa: S310 Hub 地址由配置注入
        return json.loads(resp.read() or b"{}")


def send_with_retry(url: str, token: str, payload: dict,
                    log: Callable[[str], None]) -> bool:
    """首发 + 1s/5s/15s 三次退避重试；最终失败返回 False（调用方留痕）。"""
    for attempt in range(len(_RETRY_BACKOFF) + 1):
        if attempt:
            time.sleep(_RETRY_BACKOFF[attempt - 1])
        try:
            resp = post_callback(url, token, payload)
            log(f"[callback] 回调完成: conclusion={payload['conclusion']} resp={resp}")
            return True
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            log(f"[callback] 第 {attempt + 1} 次回调失败: {type(exc).__name__}: {exc}")
    return False


# ---- 总编排（_run_job 收尾调用）----

def finalize_evaluation(eval_ctx: dict, report: dict, store: EvaluationStore,
                        report_dir: Path, log: Callable[[str], None]) -> None:
    """Hub 直连评测收尾：终态+摘要落档 → 基线滚动（先对比后滚动）→ 主动回调。

    - eval_ctx: {cid, sha, save_baseline, ...}（M-E1 挂 Job）
    - report:   server report.json 载荷（error/results/comm_health）
    """
    cid = eval_ctx["cid"]
    conclusion = conclusion_of(report.get("error"), report.get("results", []),
                               _expects_of(report))          # A11：按实际 vs 期望
    repo = eval_ctx.get("repo")
    changes = regression_changes(report, repo)  # 先对比（基线按 repo 隔离，D1）
    summary = summarize(report, changes)
    store.update_terminal(cid, status=conclusion, summary=summary,
                          finished_at=datetime.now(timezone.utc).isoformat())
    # 后滚动：save_baseline=true 且 success 时基线前进（对齐 M-D3 CI 语义）
    if eval_ctx.get("save_baseline") and conclusion == "success":
        target = save_baseline(report_dir, baseline_path_for(repo, artifacts_root()))
        log(f"[baseline] 基线已滚动: {target}")

    url = os.environ.get("HUB_CALLBACK_URL", "").strip()
    token = os.environ.get("HUB_CALLBACK_TOKEN", "").strip()
    if not url or not token:
        log(f"[callback] 未配置 HUB_CALLBACK_URL/HUB_CALLBACK_TOKEN，跳过发送 cid={cid}")
        return
    payload = build_payload(cid, eval_ctx.get("sha"), conclusion, summary,
                            build_metrics(report, changes))
    # 回调线程生命周期晚于 recorder.close()：日志走独立句柄 append（ArtifactRecorder.append_log）
    def _thread_log(message: str) -> None:
        ArtifactRecorder.append_log(report_dir, message)

    threading.Thread(target=_send_and_record, args=(url, token, payload, store, cid, _thread_log),
                     daemon=True, name=f"atp-callback-{cid[-8:]}").start()


def _send_and_record(url: str, token: str, payload: dict, store: EvaluationStore,
                     cid: str, log: Callable[[str], None]) -> None:
    if not send_with_retry(url, token, payload, log):
        message = f"回调最终失败（已重试 {len(_RETRY_BACKOFF)} 次）: {url}"
        store.set_callback_error(cid, message)
        log(f"[callback] {message}——结果已落档，Hub 轮询兜底可拿回")
