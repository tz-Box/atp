"""运行控制：暂停 / 单步 / 继续（调试语义，帧级闸门）。

语义冻结：
- `pause`：World 停喂帧（时钟冻结），SUT 阻塞等 STEP——算法断点/现场观察的安全态；
- `step(n)`：暂停中放行 n 帧（每帧一个配额），配额耗尽自动回到阻塞；
- `resume`：解除暂停并清空残余配额（避免恢复后多走"幽灵帧"）；
- `frames_sent`：累计放行帧数（job status 暴露，调试时确认喂帧位置）。

仅数据帧（done=False）过闸门；终止帧（done=True）是协议收尾必须直达。
线程安全：会话线程调 wait_gate，server 控制线程调 pause/step/resume。
"""
from __future__ import annotations

import threading


class RunControl:
    """帧级运行闸门：会话帧循环每帧 publish 前调 wait_gate()。"""

    def __init__(self) -> None:
        self._paused = threading.Event()  # set = 暂停中
        self._quota_changed = threading.Event()  # 配额/暂停态变化通知（wait_gate 的等待对象）
        self._lock = threading.Lock()
        self._step_quota = 0
        self._frames_sent = 0

    # ---- 控制侧（server 命令处理线程） ----
    def pause(self) -> None:
        with self._lock:
            self._paused.set()
            self._quota_changed.set()

    def resume(self) -> None:
        with self._lock:
            self._paused.clear()
            self._step_quota = 0  # 残余配额作废：恢复即全速，不留幽灵帧
            self._quota_changed.set()

    def step(self, n: int = 1) -> bool:
        """暂停中发放 n 帧配额；非暂停状态无意义，返回 False。"""
        with self._lock:
            if not self._paused.is_set():
                return False
            self._step_quota += max(1, n)
            self._quota_changed.set()
            return True

    # ---- 会话侧（帧循环） ----
    def wait_gate(self) -> None:
        """每帧 publish 前调用：运行中直通；暂停中阻塞至有配额或 resume。"""
        while True:
            with self._lock:
                if not self._paused.is_set():
                    self._frames_sent += 1
                    return
                if self._step_quota > 0:
                    self._step_quota -= 1
                    self._frames_sent += 1
                    if self._step_quota == 0:
                        self._quota_changed.clear()  # 配额耗尽，重置等待
                    return
                self._quota_changed.clear()
            self._quota_changed.wait(timeout=1.0)  # 超时重检，防丢通知死等

    # ---- 观测 ----
    @property
    def state(self) -> str:
        return "paused" if self._paused.is_set() else "running"

    @property
    def frames_sent(self) -> int:
        with self._lock:
            return self._frames_sent
