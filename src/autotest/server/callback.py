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
from .evaluations import EvaluationStore, conclusion_of
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
    head = f"{n_passed}/{len(results)} passed"
    parts = []
    for r in results:
        if r.get("metrics"):
            metrics = ", ".join(f"{k}={v:.4f}" for k, v in r["metrics"].items())
            parts.append(f"{r['testcase_id']}: {'passed' if r.get('passed') else 'failed'} ({metrics})")
        else:
            parts.append(f"{r['testcase_id']}: 数据流验证 records={r.get('n_records', 0)}")
    warnings = (report.get("comm_health") or {}).get("warnings") or []
    if warnings:
        parts.append("通信告警: " + "; ".join(warnings))
    if changes:  # vs_baseline 变化计数（regressed 为回归，需关注）
        parts.append("vs_baseline: " + ", ".join(f"{k}={v}" for k, v in sorted(changes.items())))
    return "; ".join([head, *parts])


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

def build_payload(cid: str, sha: Optional[str], conclusion: str, summary: str) -> dict:
    """组回调载荷（§4.3）：cid + 实际 sha + check_type + conclusion + report + finished_at。
    run_url 语义 v1.5 归 Hub（console 详情页），ATP 省略。"""
    return {
        "correlation_id": cid,
        "sha": sha or "",  # v1.2 起必带；M-E2 前非 git 仓过渡为空串
        "check_type": "autotest",
        "conclusion": conclusion,
        "report": {"summary": summary},
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
    conclusion = conclusion_of(report.get("error"), report.get("results", []))
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
    payload = build_payload(cid, eval_ctx.get("sha"), conclusion, summary)
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
