"""ATP HTTP 面 client（v1.5 §4.8，M-E8）：手动触发评测与联调自验工具。

与 tzcomm 面 client（run/matrix/report，本机 runner 通路）并列，走 Hub 同款 HTTP 接口——
可用于：本机/运维手动触发测试（不经 Hub）、M-E6 联调前 ATP 侧自验、Hub 排障时重放提交。
仅为出站 HTTP 连接（默认 http://127.0.0.1:2335），不监听端口，与 autotest service 无冲突。

配置（免 export）：`atp login` 交互式录入一次即落盘 ~/.config/autotest/client.env（chmod 600）；
环境变量 ATP_BASE_URL / ATP_SERVICE_TOKEN 优先于配置文件（临时切换/CI 注入用）。
退出码：0=成功/接受；1=失败（评测 failure / 接口错误 / 超时）；2=用法错误——可直接接 CI gate。
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

_DEFAULT_BASE = "http://127.0.0.1:2335"
_WAIT_INTERVAL = 2.0
_CFG_PATH = Path.home() / ".config" / "autotest" / "client.env"  # atp login 落盘（chmod 600）


class AtpClientError(Exception):
    """接口层错误（连接失败/非预期状态码/认证失败）。"""


def _read_file_cfg() -> dict:
    """读 client.env（KEY=VALUE 行）；不存在/不可读 → {}（容错，按未配置处理）。"""
    try:
        out = {}
        for line in _CFG_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                out[k.strip()] = v.strip()
        return out
    except OSError:
        return {}


def _base_url() -> str:
    return (os.environ.get("ATP_BASE_URL")
            or _read_file_cfg().get("ATP_BASE_URL")
            or _DEFAULT_BASE).rstrip("/")


def _token() -> str:
    return (os.environ.get("ATP_SERVICE_TOKEN")
            or _read_file_cfg().get("ATP_SERVICE_TOKEN") or "").strip()


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


# ---- 配置管理（login/logout/whoami）：一次登录落盘，免 export ----


def _write_file_cfg(base_url: str, token: str) -> None:
    """落盘 client.env（chmod 600，覆盖写）。"""
    _CFG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CFG_PATH.write_text(
        f"ATP_BASE_URL={base_url}\nATP_SERVICE_TOKEN={token}\n", encoding="utf-8")
    os.chmod(_CFG_PATH, 0o600)


def _mask(token: str) -> str:
    return f"{token[:4]}…（共{len(token)}字符）" if token else "<未配置>"


def _probe(base: str) -> Optional[dict]:
    """对指定地址探活（health 无认证）；不可达 → None。"""
    try:
        with urllib.request.urlopen(f"{base}/atp/health", timeout=5.0) as resp:  # noqa: S310 地址由用户录入
            return json.loads(resp.read().decode())
    except Exception:  # noqa: BLE001 探活容错：任何异常都按不可达处理
        return None


def _cmd_login(args: argparse.Namespace) -> int:
    base = (args.base_url or input(f"ATP 服务地址 [{_DEFAULT_BASE}]: ").strip()
            or _DEFAULT_BASE).rstrip("/")
    token = args.token or getpass.getpass("ATP 服务令牌（输入不回显）: ").strip()
    if not token:
        print("atp client: 令牌为空，未保存", file=sys.stderr)
        return 2
    _write_file_cfg(base, token)
    print(f"已保存 → {_CFG_PATH}（chmod 600；环境变量 ATP_BASE_URL/ATP_SERVICE_TOKEN 仍优先）")
    body = _probe(base)
    if body is None:
        print(f"警告：配置已保存，但 {base} 暂不可达（稍后 atp whoami 复查）", file=sys.stderr)
        return 1
    if not body.get("ok"):
        print(f"警告：服务可达但异常（tzcomm={body.get('tzcomm')}），请联系评测负责人", file=sys.stderr)
        return 1
    print(f"探活成功：v{body.get('version')} · 队列 {body.get('queue')}，可以使用了（atp submit --help）")
    return 0


def _cmd_logout(_: argparse.Namespace) -> int:
    try:
        _CFG_PATH.unlink()
        print(f"已删除 {_CFG_PATH}")
    except FileNotFoundError:
        print(f"本机无已保存配置（{_CFG_PATH} 不存在）")
    return 0


def _cmd_whoami(_: argparse.Namespace) -> int:
    env_base = os.environ.get("ATP_BASE_URL", "").strip()
    env_tok = os.environ.get("ATP_SERVICE_TOKEN", "").strip()
    file_cfg = _read_file_cfg()
    base_src = "环境变量" if env_base else ("配置文件" if file_cfg.get("ATP_BASE_URL") else "默认")
    tok_src = "环境变量" if env_tok else ("配置文件" if file_cfg.get("ATP_SERVICE_TOKEN", "").strip() else "未配置")
    _print_json({
        "base_url": _base_url(), "base_url_source": base_src,
        "token": _mask(_token()), "token_source": tok_src,
        "config_file": str(_CFG_PATH),
    })
    body = _probe(_base_url())
    if body is None:
        print(f"atp client: ATP 不可达: {_base_url()}", file=sys.stderr)
        return 1
    _print_json({"health": body})
    return 0 if body.get("ok") else 1


_COMMANDS = {
    "login": _cmd_login,
    "logout": _cmd_logout,
    "whoami": _cmd_whoami,
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

    login_parser = sub.add_parser("login", help="交互式登录：录入服务地址+令牌并落盘（免 export）")
    login_parser.add_argument("--base-url", default=None, help="服务地址（跳过交互询问；脚本用）")
    login_parser.add_argument("--token", default=None,
                              help="服务令牌（跳过交互询问；注意 shell 历史留痕，日常建议交互录入）")

    sub.add_parser("logout", help="退出登录：删除已保存的配置文件")
    sub.add_parser("whoami", help="查看当前生效配置（来源/掩码令牌）并探活")

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
