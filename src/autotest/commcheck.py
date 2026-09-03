"""tzcomm 通信自检（预检）与运行期健康采集。

两个用途：

1. **预检**：``python3 -m autotest.commcheck``——评测/部署前验证 tzcomm 链路健康。
   逐级检查 daemon 可达 → SUBPUB 回环 → Service 回环，失败时给出可归因提示
   （daemon 未启动 / 发现层异常 / 传输丢包），退出码 0/1 可接 CI gate。
2. **运行期留痕**：``snapshot_node`` 采集 Node 丢包统计，``build_health`` 汇总
   Service/SUT 双侧并产告警，入 report.json 的 ``comm_health``，
   辅助"框架 vs tzcomm vs 算法"分钟级归因。
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

# 丢包告警阈值（近 1s 窗口与累计任一超标即告警）
_LOSS_WARN_RATE = 0.01  # 1%

# 与 tzcomm.config.Config 的 daemon_addr 缺省保持一致（单一事实源在 tzcomm 侧）
_DEFAULT_DAEMON = "127.0.0.1:17888"

# 回环发送节流：留出回调取件时间，避免撞上订阅队列深度（见 check_pubsub）
_PACE_S = 0.002


def _daemon_addr() -> tuple[str, int]:
    """tzcomm daemon 地址（TZCOMM_DAEMON_ADDR，缺省与 tzcomm Config 一致 17888）。"""
    host, _, port = os.environ.get("TZCOMM_DAEMON_ADDR", _DEFAULT_DAEMON).rpartition(":")
    return host or "127.0.0.1", int(port)


@dataclass
class CheckResult:
    """单项自检结果。hint 为失败时的归因/处置提示。"""

    name: str
    ok: bool
    detail: dict = field(default_factory=dict)
    error: Optional[str] = None
    hint: Optional[str] = None

    def to_dict(self) -> dict:
        out = {"name": self.name, "ok": self.ok, "detail": self.detail}
        if self.error:
            out["error"] = self.error
        if self.hint:
            out["hint"] = self.hint
        return out


# ---- 预检：逐级回环 ----


def check_daemon(timeout: float = 3.0) -> CheckResult:
    """第 1 级：daemon TCP 可达（发现层生命线）。"""
    host, port = _daemon_addr()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return CheckResult("daemon", True, {"addr": f"{host}:{port}"})
    except OSError as exc:
        return CheckResult(
            "daemon", False, {"addr": f"{host}:{port}"}, f"{type(exc).__name__}: {exc}",
            hint="tzcomm daemon 未启动或地址错误——先起 daemon（install/setup.sh 或 tzcomm-daemon.service），"
                 "并核对 TZCOMM_DAEMON_ADDR",
        )


def check_pubsub(messages: int = 50, timeout: float = 5.0) -> CheckResult:
    """第 2 级：SUBPUB 回环（qos=1 TCP）。同节点 pub+sub 独立探针 topic，验证发现+传输。

    消息内塞发送时刻，接收侧算单向延迟（同进程同时钟，仅作量级参考）。
    """
    import tzcomm  # 延迟导入：纯 snapshot/assess 使用方无需 tzcomm 环境

    probe = f"autotest/commcheck/{uuid.uuid4().hex[:8]}/probe"
    node = tzcomm.Node(f"autotest-commcheck-{uuid.uuid4().hex[:4]}")
    received: list[float] = []  # 每条消息的单向延迟（秒）
    done = threading.Event()

    def _on_msg(data: dict) -> None:
        received.append(time.monotonic() - float(data.get("sent_at", time.monotonic())))
        if len(received) >= messages:
            done.set()

    try:
        node.create_subscription(probe, _on_msg, qos=1)
        pub = node.create_publisher(probe, qos=1)
        # 等发现层撮合 pub/sub 的 TCP 连接。**不能用固定 sleep**：qos=1 的"可靠"
        # 只在连接建立之后成立，早发的消息会被直接丢弃。原实现固定 sleep(0.5) 后
        # 一次性灌 50 条，机器有负载时连接还没建好，实测出现 42/50 这种数字——
        # 而这是**诊断工具**，假 FAIL 会把人送去排查根本不存在的丢包问题。
        # 改为发探测帧直到收到第一条为止（不计入统计），确认通路真的活了再开始测。
        warm_deadline = time.monotonic() + timeout
        while time.monotonic() < warm_deadline:
            pub.publish({"seq": -1, "sent_at": time.monotonic()})
            time.sleep(0.05)
            if received:
                break
        if not received:
            return CheckResult(
                "pubsub", False, {"topic": probe, "sent": 0, "recv": 0},
                f"预热 {timeout}s 内未建立 pub/sub 通路",
                hint="发现层未撮合成功——查 daemon 是否与客户端库同版本（daemon 无版本查询接口，"
                     "必要时重启 daemon 后复测）",
            )
        # 预热帧的延迟样本无意义，清掉；丢包计数不需要重置——tzcomm 的 gap 统计只在
        # 已有前序 seq 时才计丢（_stats.py），首条到达的帧只用于建立基线，
        # 预热期未送达的帧不会被算成丢包。
        received.clear()
        start = time.monotonic()
        for i in range(messages):
            pub.publish({"seq": i, "sent_at": time.monotonic()})
            # 必须节流：订阅投递队列默认深度 10（KEEP_LAST，对齐 ROS2），
            # 无节流地一次灌 50 条会在传输层之外被队列丢弃——现象是
            # `gap_lost=0` 但 recv < sent（传输层什么都没丢，回调没取完）。
            # 原实现无节流，实测稳定 30~49/50，把一个**诊断工具**变成了假 FAIL 源。
            # 真实 obs 推流本就受 clock_rate 节拍约束，节流后测的才是链路本身。
            time.sleep(_PACE_S)
        done.wait(timeout)
        elapsed = time.monotonic() - start
        stats = node.network_stats()
        n_recv = len(received)
        detail = {
            "topic": probe, "sent": messages, "recv": n_recv,
            "elapsed_s": round(elapsed, 3),
            "loss": stats["lost"], "loss_rate": round(stats["loss_rate"], 4),
            "latency_ms_avg": round(sum(received) / n_recv * 1000, 2) if n_recv else None,
        }
        if n_recv < messages or stats["lost"] > 0:
            return CheckResult(
                "pubsub", False, detail, f"回环收齐 {n_recv}/{messages}, gap_lost={stats['lost']}",
                hint="发现层或传输异常——查 daemon 日志与 TZCOMM_LOG_LEVEL=DEBUG 的 tzcomm 日志",
            )
        return CheckResult("pubsub", True, detail)
    except Exception as exc:  # noqa: BLE001 预检要把任何底层异常转成可归因结果
        return CheckResult(
            "pubsub", False, {"topic": probe}, f"{type(exc).__name__}: {exc}",
            hint="pub/sub 建立失败——多为 daemon 注册异常（重启 daemon 后复测）",
        )
    finally:
        node.close()


def check_service(timeout: float = 5.0) -> CheckResult:
    """第 3 级：Service 回环（请求-响应 + RTT）。"""
    import tzcomm

    name = f"autotest/commcheck/{uuid.uuid4().hex[:8]}/echo"
    node = tzcomm.Node(f"autotest-commcheck-{uuid.uuid4().hex[:4]}")
    try:
        node.create_service(name, lambda req: {"echo": req})
        client = node.create_service_client(name)
        if not client.wait_for_server(timeout=timeout):
            return CheckResult(
                "service", False, {"service": name}, "wait_for_server 超时",
                hint="service 注册未被 daemon 发现——查 daemon 注册表（tzcomm status）",
            )
        start = time.monotonic()
        resp = client.call({"ping": 1}, timeout=timeout)
        rtt_ms = (time.monotonic() - start) * 1000
        if resp.get("echo", {}).get("ping") != 1:
            return CheckResult("service", False, {"service": name}, f"响应内容异常: {resp!r}")
        return CheckResult("service", True, {"service": name, "rtt_ms": round(rtt_ms, 2)})
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            "service", False, {"service": name}, f"{type(exc).__name__}: {exc}",
            hint="service 调用失败——查对端进程是否存活（daemon 5s 心跳/15s 清理）",
        )
    finally:
        node.close()


def run_checks(messages: int = 50, timeout: float = 5.0) -> dict:
    """逐级预检：daemon 不通则后两级标记 skipped（无意义的连锁失败不浪费超时）。"""
    daemon = check_daemon()
    checks = [daemon]
    if daemon.ok:
        checks.append(check_pubsub(messages=messages, timeout=timeout))
        checks.append(check_service(timeout=timeout))
    else:
        for name in ("pubsub", "service"):
            checks.append(CheckResult(name, False, error="skipped: daemon 不可达"))
    return {
        "ok": all(c.ok for c in checks),
        "ts": datetime.now(timezone.utc).isoformat(),
        "checks": [c.to_dict() for c in checks],
    }


# ---- 运行期：健康快照与告警 ----


def snapshot_node(node: Any, side: str) -> dict:
    """采集一个 tzcomm Node 的丢包/收发统计（LossTracker 聚合）。"""
    stats = node.network_stats()
    return {
        "side": side,
        "ts": datetime.now(timezone.utc).isoformat(),
        "msgs": stats["msgs"],
        "lost": stats["lost"],
        "loss_rate": round(stats["loss_rate"], 4),
        "msgs_1s": stats["msgs_1s"],
        "lost_1s": stats["lost_1s"],
        "loss_rate_1s": round(stats["loss_rate_1s"], 4),
    }


def assess_stats(stats: Optional[dict], side: str) -> list[str]:
    """按阈值对单侧统计产告警文案（空统计不告警）。"""
    if not stats:
        return []
    warnings = []
    if stats.get("loss_rate", 0.0) > _LOSS_WARN_RATE:
        warnings.append(
            f"{side} 侧累计丢包率 {stats['loss_rate']:.2%} 超阈值 {_LOSS_WARN_RATE:.0%}"
            f"（msgs={stats.get('msgs')}, lost={stats.get('lost')}）"
        )
    if stats.get("loss_rate_1s", 0.0) > _LOSS_WARN_RATE:
        warnings.append(f"{side} 侧末尾 1s 窗口丢包率 {stats['loss_rate_1s']:.2%} 超阈值")
    return warnings


def build_health(service: Optional[dict], sut: Optional[dict]) -> dict:
    """汇总 Service/SUT 双侧快照为 comm_health（附告警）。

    - service 侧 = result 接收丢包（SUT→Service 方向）；
    - sut 侧 = obs 接收丢包（Service→SUT 方向，SDK final 自统计回传）；
      SUT 未回传（老版本 SDK / 原生 tzcomm 自实现）时 sut 为 None，不告警。
    """
    warnings = assess_stats(service, "Service") + assess_stats(sut, "SUT")
    return {"service": service, "sut": sut, "warnings": warnings}


# ---- CLI ----


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="autotest.commcheck",
        description="tzcomm 链路预检：daemon 可达 / SUBPUB 回环 / Service 回环",
    )
    parser.add_argument("--json", action="store_true", help="JSON 输出（CI gate 用）")
    parser.add_argument("--messages", type=int, default=50, help="pubsub 回环消息数（默认 50）")
    parser.add_argument("--timeout", type=float, default=5.0, help="单项超时秒数（默认 5）")
    args = parser.parse_args(argv)

    report = run_checks(messages=args.messages, timeout=args.timeout)
    if args.json:
        print(json.dumps(report, ensure_ascii=False))
    else:
        for c in report["checks"]:
            mark = "PASS" if c["ok"] else "FAIL"
            line = f"[{mark}] {c['name']}"
            if c["ok"]:
                line += f" {c['detail']}"
            else:
                line += f" {c.get('error', '')}"
                if c.get("hint"):
                    line += f"\n       提示: {c['hint']}"
            print(line)
        print(f"总体: {'PASS' if report['ok'] else 'FAIL'}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
