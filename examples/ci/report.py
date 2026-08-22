"""CI 回调：评测结果 → Hub `POST /api/ci/callback`（v1.3 §4.3）。

纯标准库，无第三方依赖。用法：python3 report.py <report.json>

环境变量（workflow 传入）：
  HUB_CALLBACK_URL   Hub 回调端点（.../api/ci/callback）
  HUB_CALLBACK_TOKEN Bearer token（hub.callback_token）
  CORRELATION_ID     Hub 生成的对账锚点（chk_<26位ULID>）
  CHECK_TYPE         检查类型（默认 autotest）
  ACTUAL_SHA         实际 checkout 的 commit sha（必带：通路3 触发时 sha 不定，回调回填 Hub pending）
  RUN_URL            workflow run URL（可选，写入 report.run_url；一律指 run，不指分支）
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

_CALLBACK_TIMEOUT = 15.0


def load_report(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def summarize(report: dict) -> str:
    """report.json → 结构化摘要文本（passed/n_records/metrics 概览，v1.3 §4.3）。"""
    if not report.get("ok"):
        return f"评测失败: {report.get('error', '未知错误')}"
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
    # 通信告警（丢包超阈值等）附摘要尾部：CI 侧直接可见"疑似 tzcomm 链路问题"归因提示
    warnings = (report.get("comm_health") or {}).get("warnings") or []
    if warnings:
        parts.append("通信告警: " + "; ".join(warnings))
    return "; ".join([head, *parts])


def build_payload(report: dict, env: dict) -> dict:
    """组回调载荷（v1.3 §4.3）：cid + 实际 sha + check_type + conclusion + report + finished_at。"""
    conclusion = "failure"
    if report.get("ok") and all(r.get("passed") is not False for r in report.get("results", [])):
        conclusion = "success"  # passed=None 为数据流验证（无 GT），不判失败
    payload = {
        "correlation_id": env["correlation_id"],
        "sha": env["sha"],
        "check_type": env.get("check_type") or "autotest",
        "conclusion": conclusion,
        "report": {"summary": summarize(report)},
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    if env.get("run_url"):
        payload["report"]["run_url"] = env["run_url"]
    return payload


def post_callback(url: str, token: str, payload: dict) -> dict:
    """POST Hub 回调（Bearer 鉴权），返回响应 JSON。幂等：同 cid 重复返回 duplicate=true。"""
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=_CALLBACK_TIMEOUT) as resp:  # noqa: S310 Hub 地址由配置注入
        return json.loads(resp.read() or b"{}")


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: python3 report.py <report.json>", file=sys.stderr)
        return 2
    env = {
        "url": os.environ.get("HUB_CALLBACK_URL", ""),
        "token": os.environ.get("HUB_CALLBACK_TOKEN", ""),
        "correlation_id": os.environ.get("CORRELATION_ID", ""),
        "check_type": os.environ.get("CHECK_TYPE", "autotest"),
        "sha": os.environ.get("ACTUAL_SHA", ""),
        "run_url": os.environ.get("RUN_URL", ""),
    }
    required = {"url": "HUB_CALLBACK_URL", "token": "HUB_CALLBACK_TOKEN",
                "correlation_id": "CORRELATION_ID", "sha": "ACTUAL_SHA"}
    missing = [name for key, name in required.items() if not env[key]]
    if missing:
        print(f"缺少环境变量: {', '.join(missing)}", file=sys.stderr)
        return 2

    try:
        report = load_report(sys.argv[1])
    except (OSError, json.JSONDecodeError) as exc:
        # 评测进程异常（未产出合法 report.json）也要把 failure 回传 Hub，避免 pending 超时
        report = {"ok": False, "error": f"report.json 读取失败: {exc}"}
    payload = build_payload(report, env)
    try:
        resp = post_callback(env["url"], env["token"], payload)
    except urllib.error.URLError as exc:
        print(f"[report] 回调失败: {exc}", file=sys.stderr)
        return 1
    print(f"[report] 回调完成: conclusion={payload['conclusion']} resp={resp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
