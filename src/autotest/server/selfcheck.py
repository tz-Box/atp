"""tzcomm 数据面后台自检：把 /atp/health 从"端口通"升级为"数据面真的通"。

## 为什么需要（2026-09-03 生产事故）

原 `/atp/health` 只对 daemon 端口做一次 socket 连通性检查。那天生产 daemon（8/23 启动）
与客户端库（TzComm D13「取消端口池，端口改由内核分配」，9/3）**版本错配**：
客户端 bind 内核分配的端口，daemon 却按旧账本登记另一个端口，订阅方连过去 Connection refused
——**数据面完全不通，而 health 一路报 `tzcomm: true`**，Hub 也就一路把评测往这台机器上派。

组件级探活的典型盲区：**端口在监听 ≠ 通信能工作**。唯一可靠的判据是真发一条、真收一条。

tzcomm 的 daemon 协议**没有版本查询 op**（见 `tzcomm/_proto.py` 的 OP_* 清单），
所以 ATP 无从比对"我这边的库"与"那边的 daemon"是否同版本；`tzcomm.__version__` 也未随
该次改造递增。**故版本号帮不上忙，回环才是可靠信号**——这是本模块存在的全部理由。

## 设计取舍

- **后台周期跑，health 读缓存**：契约 §4.8 的 health 供 Hub 池化探活，必须即时响应，
  不能每次现做一次往返。
- **长驻 node，不是每次新建**：少一次注册/注销churn，且顺带验证"长连接是否一直可用"。
- **qos=1（TCP）**：与评测的 obs 通道一致（契约 §3.1）。UDP 丢包是预期内的，
  用它做健康判据会让 health 抖动；UDP 侧的丢包另有 `comm_health` 逐 job 统计。
- **重复发直到收到为止**：qos=1 的"可靠"是**连接建立之后**才可靠。订阅方与发布方的
  TCP 连接需要发现层撮合，早发的消息会丢——`commcheck.check_pubsub` 固定 sleep 0.5s
  再发 50 条，实测会出现 42/50 这种数字。健康判据不能建在这种竞态上，故这里反复重试，
  **收到任意一条即判通**。
"""
from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

_PROBE_TIMEOUT = 3.0      # 单轮探测上限
_PROBE_INTERVAL = 0.2     # 轮内重发间隔


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class TzcommSelfCheck:
    """周期性 qos=1 回环自检；`snapshot()` 供 /atp/health 即时读取。"""

    def __init__(self, interval: float = 30.0, name: str = "atp-selfcheck") -> None:
        self._interval = interval
        self._name = name
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._node = None
        self._pub = None
        self._topic = ""
        self._hits = 0
        # 首轮完成前 loopback 为 None：此时 health 退回"仅 daemon 可达"判定，
        # 并在报文里如实标注 checked_at=None，不假装已经验过
        self._state = {"loopback": None, "checked_at": None, "error": None,
                       "lib_version": _lib_version()}

    # ---- 生命周期 ----

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="atp-tzcomm-selfcheck")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._teardown()

    # ---- 对外快照 ----

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._state)

    # ---- 内部 ----

    def _loop(self) -> None:
        while not self._stop.is_set():
            ok, err = self._probe_once()
            with self._lock:
                self._state = {"loopback": ok, "checked_at": _now_iso(), "error": err,
                               "lib_version": _lib_version()}
            self._stop.wait(self._interval)

    def _ensure_node(self) -> None:
        if self._node is not None:
            return
        import tzcomm  # 延迟导入：与 commcheck 一致，纯离线使用方无需 tzcomm 环境

        self._topic = f"autotest/selfcheck/{uuid.uuid4().hex[:8]}/probe"
        node = tzcomm.Node(f"{self._name}-{uuid.uuid4().hex[:4]}")
        node.create_subscription(self._topic, self._on_msg, qos=1)
        self._pub = node.create_publisher(self._topic, qos=1)
        self._node = node

    def _on_msg(self, _data: dict) -> None:
        self._hits += 1

    def _probe_once(self) -> tuple[bool, Optional[str]]:
        try:
            self._ensure_node()
        except Exception as exc:  # noqa: BLE001 daemon 不可达/注册失败都归为不健康
            self._teardown()
            return False, f"{type(exc).__name__}: {exc}"

        before = self._hits
        deadline = time.monotonic() + _PROBE_TIMEOUT
        try:
            while time.monotonic() < deadline:
                # 反复发：qos=1 的可靠性只在连接建立之后成立，早发的会丢（见模块 docstring）
                self._pub.publish({"probe": 1})
                if self._hits > before:
                    return True, None
                time.sleep(_PROBE_INTERVAL)
        except Exception as exc:  # noqa: BLE001 发布失败 = 数据面不可用
            self._teardown()
            return False, f"{type(exc).__name__}: {exc}"
        self._teardown()  # 探测失败：丢弃可能已失效的 node，下轮重建
        return False, f"回环 {_PROBE_TIMEOUT}s 内未收到消息"

    def _teardown(self) -> None:
        node, self._node, self._pub = self._node, None, None
        if node is not None:
            try:
                node.close()
            except Exception:  # noqa: BLE001 关闭失败不应影响自检循环
                pass


def _lib_version() -> str:
    """本进程加载的 tzcomm 库版本。

    ⚠ 参考值：daemon 侧无版本查询接口，且该版本号未随 D13 改造递增，
    **不能用它判断两侧是否同版本**（正是它没能发现 2026-09-03 那次错配）。
    留在报文里仅为排障时快速确认"我这边加载的是哪一份"。
    """
    try:
        import tzcomm

        return str(getattr(tzcomm, "__version__", "unknown"))
    except Exception:  # noqa: BLE001
        return "unavailable"
