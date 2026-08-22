"""测试内容：RunControl 调试闸门语义（暂停/单步/恢复，纯线程，无 tzcomm）。
期望输出：运行态直通计数；暂停阻塞；step 配额精确放行且可累积；resume 清残余配额（无幽灵帧）。
"""
from __future__ import annotations

import threading
import time

from autotest.eval.run_control import RunControl


def _pump(control: RunControl, n: int, done: threading.Event) -> None:
    """后台线程连续过闸 n 帧（模拟会话帧循环），完成后置 done。"""
    for _ in range(n):
        control.wait_gate()
    done.set()


def test_running_passthrough() -> None:
    control = RunControl()
    for _ in range(5):
        control.wait_gate()
    assert control.state == "running"
    assert control.frames_sent == 5


def test_pause_blocks_and_resume_releases() -> None:
    control = RunControl()
    control.wait_gate()  # 1 帧已过闸
    control.pause()
    assert control.state == "paused"

    done = threading.Event()
    t = threading.Thread(target=_pump, args=(control, 3, done), daemon=True)
    t.start()
    time.sleep(0.2)
    assert control.frames_sent == 1  # 暂停中无新帧放行
    assert not done.is_set()

    control.resume()
    assert t.join(timeout=2) is None and done.is_set()
    assert control.frames_sent == 4
    assert control.state == "running"


def test_step_quota_exact() -> None:
    control = RunControl()
    control.pause()
    assert control.step(3) is True

    for _ in range(3):  # 配额内直通（同步调用不阻塞）
        control.wait_gate()
    assert control.frames_sent == 3

    done = threading.Event()  # 配额耗尽：第 4 帧阻塞
    t = threading.Thread(target=_pump, args=(control, 1, done), daemon=True)
    t.start()
    time.sleep(0.2)
    assert control.frames_sent == 3
    assert not done.is_set()

    control.resume()  # 恢复后阻塞帧放行
    assert t.join(timeout=2) is None and done.is_set()
    assert control.frames_sent == 4


def test_step_requires_pause() -> None:
    control = RunControl()
    assert control.step(1) is False  # 非暂停状态 step 无意义
    assert control.frames_sent == 0
    assert control.state == "running"


def test_step_accumulates() -> None:
    control = RunControl()
    control.pause()
    control.step(2)
    control.step(3)  # 配额累积：2 + 3 = 5
    for _ in range(5):
        control.wait_gate()
    assert control.frames_sent == 5

    done = threading.Event()  # 第 6 帧阻塞
    t = threading.Thread(target=_pump, args=(control, 1, done), daemon=True)
    t.start()
    time.sleep(0.2)
    assert not done.is_set()
    assert control.frames_sent == 5
    control.resume()
    assert t.join(timeout=2) is None and done.is_set()


def test_resume_clears_residual_quota() -> None:
    control = RunControl()
    control.pause()
    control.step(5)
    for _ in range(2):  # 消耗 2/5 配额
        control.wait_gate()
    control.resume()  # 残余 3 配额作废：恢复即全速，不留幽灵帧
    assert control.state == "running"
    for _ in range(10):
        control.wait_gate()
    assert control.frames_sent == 12  # 全部直通，无配额记账
