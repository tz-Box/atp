"""CI 报告发送：评测结果 → GitHub Issue（+ 内网接口，仅 autotest 分支）。

纯标准库，无第三方依赖。用法：python3 report.py <report.json>

环境变量（workflow 传入）：
  GITHUB_TOKEN      仓库读写 token（GitHub Actions 自动提供）
  GITHUB_REPOSITORY 仓库名（owner/repo）
  GITHUB_SHA        触发评测的 commit sha
  BRANCH            触发分支（autotest / mannultest）
  INNER_WEBHOOK_URL 内网接口地址；仅 autotest 分支发送，留空则跳过
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from typing import Optional

_GITHUB_API = "https://api.github.com"
_WEBHOOK_TIMEOUT = 10.0


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default) or default


def load_report(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def build_body(report: dict, branch: str, sha: str) -> str:
    short_sha = sha[:8] if sha else "unknown"
    lines = [f"## Autotest 评测结果（`{branch}` @ `{short_sha}`）", ""]
    if not report.get("ok"):
        lines += ["**评测失败**", "", f"```\n{report.get('error', '')}\n```", ""]
        return "\n".join(lines)

    lines += ["| testcase | 结论 | 指标 |", "|---|---|---|"]
    for r in report.get("results", []):
        status = "✅ passed" if r.get("passed") else "❌ failed"
        if r.get("metrics"):
            metrics = ", ".join(f"{k}={v:.4f}" for k, v in r["metrics"].items())
            lines.append(f"| {r['testcase_id']} | {status} | {metrics} |")
        else:
            lines.append(f"| {r['testcase_id']} | 数据流验证 | records={r.get('n_records')}（无 GT 评分） |")
    return "\n".join(lines)


def _api(method: str, url: str, token: str, payload: Optional[dict] = None) -> None:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"token {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=_WEBHOOK_TIMEOUT) as resp:  # noqa: S310 内部地址由用户配置
        resp.read()


def post_issue(repo: str, token: str, title: str, body: str) -> None:
    """在算法仓库创建一个 issue 承载评测报告。"""
    _api("POST", f"{_GITHUB_API}/repos/{repo}/issues", token, {"title": title, "body": body})


def post_webhook(url: str, payload: dict) -> None:
    """内网接口：POST 评测结果 JSON（autotest 分支）。"""
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=_WEBHOOK_TIMEOUT) as resp:  # noqa: S310 内网地址由用户配置
        resp.read()


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: python3 report.py <report.json>", file=sys.stderr)
        return 2
    report = load_report(sys.argv[1])

    repo = _env("GITHUB_REPOSITORY")
    token = _env("GITHUB_TOKEN")
    branch = _env("BRANCH", "unknown")
    sha = _env("GITHUB_SHA")
    webhook_url = _env("INNER_WEBHOOK_URL")

    if not repo or not token:
        print("缺少 GITHUB_REPOSITORY/GITHUB_TOKEN", file=sys.stderr)
        return 2

    body = build_body(report, branch, sha)
    post_issue(repo, token, f"Autotest 评测：{branch} @ {sha[:8] if sha else ''}", body)
    print(f"[report] GitHub issue 已创建：{repo}")

    if branch == "autotest" and webhook_url:
        post_webhook(webhook_url, {"branch": branch, "commit": sha, "report": report})
        print("[report] 已发送内网接口")
    elif branch == "autotest":
        print("[report] autotest 分支但未配置 INNER_WEBHOOK_URL，跳过内网接口")
    return 0


if __name__ == "__main__":
    sys.exit(main())
