"""ATP HTTP 面 client（v1.5 §4.8，M-E8）：手动触发评测与联调自验工具。

与 tzcomm 面 client（run/matrix/report，本机 runner 通路）并列，走 Hub 同款 HTTP 接口——
可用于：本机/运维手动触发测试（不经 Hub）、M-E6 联调前 ATP 侧自验、Hub 排障时重放提交。
仅为出站 HTTP 连接（默认 http://127.0.0.1:2335），不监听端口，与 autotest service 无冲突。

配置（env）：ATP_BASE_URL（缺省 http://127.0.0.1:2335）、ATP_SERVICE_TOKEN（submit/status 必填）。
退出码：0=成功/接受；1=失败（评测 failure / 接口错误 / 超时）；2=用法错误——可直接接 CI gate。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Optional

_DEFAULT_BASE = "http://127.0.0.1:2335"
_WAIT_INTERVAL = 2.0


class AtpClientError(Exception):
    """接口层错误（连接失败/非预期状态码/认证失败）。"""


def _base_url() -> str:
    return os.environ.get("ATP_BASE_URL", _DEFAULT_BASE).rstrip("/")


def _token() -> str:
    return os.environ.get("ATP_SERVICE_TOKEN", "").strip()


def _request(method: str, path: str, payload: Optional[dict] = None,
             *, auth: bool = True, timeout: float = 30.0) -> tuple[int, dict]:
    url = f"{_base_url()}{path}"
    headers = {"Content-Type": "application/json"}
    if auth:
        token = _token()
        if not token:
            raise AtpClientError("未配置 ATP_SERVICE_TOKEN（submit/status 需要 Bearer 认证）")
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 地址由本机 env 注入
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode())
        except Exception:  # noqa: BLE001 非 JSON 错误体（如 502 网关页）
            return exc.code, {"ok": False, "error": f"HTTP {exc.code}: {exc.reason}"}
    except (urllib.error.URLError, OSError) as exc:
        raise AtpClientError(f"ATP 不可达: {url}: {exc}") from exc


def health() -> dict:
    """GET /atp/health（无认证）→ {ok, version, tzcomm, queue}。"""
    _, body = _request("GET", "/atp/health", auth=False, timeout=5.0)
    return body


def submit(repo: str, *, ref: Optional[str] = None, cid: Optional[str] = None,
           scenario: Optional[str] = None, save_baseline: bool = False) -> tuple[int, dict]:
    """POST /atp/evaluations（Hub 同款载荷）。cid 缺省生成 chk_manual_<时间戳>。"""
    cid = cid or f"chk_manual_{time.strftime('%Y%m%d_%H%M%S')}"
    payload = {
        "correlation_id": cid,
        "repo": repo,
        "ref": ref,
        "check_type": "autotest",
        "scenario": scenario,
        "save_baseline": save_baseline,
    }
    return _request("POST", "/atp/evaluations", payload)


def status(job_id: str) -> dict:
    """GET /atp/evaluations/{job_id} → running | 终态 {status, sha, report, finished_at}。"""
    code, body = _request("GET", f"/atp/evaluations/{job_id}")
    if code == 404:
        raise AtpClientError(body.get("error", f"未知 job_id: {job_id}"))
    return body


def wait_terminal(job_id: str, timeout: float = 1800.0) -> dict:
    """轮询至终态（running → success/failure），超时抛 AtpClientError。"""
    deadline = time.monotonic() + timeout
    while True:
        body = status(job_id)
        if body.get("status") != "running":
            return body
        if time.monotonic() >= deadline:
            raise AtpClientError(f"等待超时（{timeout:.0f}s）: {job_id} 仍 running")
        time.sleep(_WAIT_INTERVAL)


# ---- CLI 子命令（挂 autotest.client 的 `atp` 组） ----


def _print_json(data: dict) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=1))


def _cmd_health(_: argparse.Namespace) -> int:
    body = health()
    _print_json(body)
    return 0 if body.get("ok") else 1


def _cmd_submit(args: argparse.Namespace) -> int:
    code, body = submit(args.repo, ref=args.ref, cid=args.cid,
                        scenario=args.scenario, save_baseline=args.save_baseline)
    if code not in (200, 202) or not body.get("ok"):
        _print_json(body)
        return 1
    if args.no_wait or body.get("duplicate"):
        _print_json(body)  # 仅即时应答语义时输出 submit 响应（保持 stdout 单 JSON）
        return 0
    print(f"job_id: {body['job_id']}（查态：atp status/wait {body['job_id']}）", file=sys.stderr)
    return _wait_and_report(body["job_id"], args.timeout)


def _cmd_status(args: argparse.Namespace) -> int:
    body = status(args.job_id)
    _print_json(body)
    return 0 if body.get("status") in (None, "running", "success") else 1


def _cmd_wait(args: argparse.Namespace) -> int:
    return _wait_and_report(args.job_id, args.timeout)


def _wait_and_report(job_id: str, timeout: float) -> int:
    terminal = wait_terminal(job_id, timeout)
    _print_json(terminal)
    return 0 if terminal.get("status") == "success" else 1


_COMMANDS = {
    "health": _cmd_health,
    "submit": _cmd_submit,
    "status": _cmd_status,
    "wait": _cmd_wait,
}


def main(argv: list[str]) -> int:
    """`python3 -m autotest.client atp ...` 子命令组入口（argv 为 atp 之后的参数）。"""
    parser = argparse.ArgumentParser(
        prog="autotest atp", description="ATP HTTP 面 client（手动触发/联调自验；Hub 同款接口）")
    sub = parser.add_subparsers(dest="atp_cmd", required=True)

    sub.add_parser("health", help="探活：GET /atp/health（ok/version/tzcomm/queue）")

    submit_parser = sub.add_parser("submit", help="提交评测（默认等待终态）")
    submit_parser.add_argument("--repo", required=True,
                               help="算法仓坐标：本地路径 / git URL / owner-repo 简写")
    submit_parser.add_argument("--ref", default=None, help="分支/tag/sha（缺省远端 HEAD）")
    submit_parser.add_argument("--cid", default=None,
                               help="correlation_id（缺省 chk_manual_<时间戳>；同 cid 幂等）")
    submit_parser.add_argument("--scenario", default=None,
                               help="场景 id（仓内 scenario.yaml 清单）| manifest 相对仓根路径；缺省=清单全跑")
    submit_parser.add_argument("--save-baseline", action="store_true", help="成功时滚动基线")
    submit_parser.add_argument("--no-wait", action="store_true", help="202 即返，不等待终态")
    submit_parser.add_argument("--timeout", type=float, default=1800.0,
                               help="等待终态超时秒数（默认 1800）")

    status_parser = sub.add_parser("status", help="查询评测状态")
    status_parser.add_argument("job_id")

    wait_parser = sub.add_parser("wait", help="等待评测终态并输出 summary")
    wait_parser.add_argument("job_id")
    wait_parser.add_argument("--timeout", type=float, default=1800.0)

    args = parser.parse_args(argv)
    try:
        return _COMMANDS[args.atp_cmd](args)
    except AtpClientError as exc:
        print(f"atp client: {exc}", file=sys.stderr)
        return 1
