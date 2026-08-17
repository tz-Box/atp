"""测试内容：FrameAssembler 多传感器时间戳同步（容差窗口对齐、未就绪不产帧、unbounded 最近帧）。"""
from __future__ import annotations

from autotest.world import FrameAssembler


def _frame(assembler: FrameAssembler) -> tuple[float, dict] | None:
    return assembler.frame("lidar", "front", required={"lidar": ["front", "rear"]})


def test_assembler_produces_complete_frame() -> None:
    a = FrameAssembler(tolerance=0.05)
    assert _frame(a) is None  # 未 push 任何数据

    a.push("lidar", "front", 1.00, "pc-front")
    a.push("imu", "imu", 1.01, "imu-1")
    assert _frame(a) is None  # rear 未到

    a.push("lidar", "rear", 1.02, "pc-rear")  # 窗口内（|1.02-1.00|=0.02<=0.05）
    t0, sensors = _frame(a)
    assert t0 == 1.00
    assert sensors["lidar"] == {"front": "pc-front", "rear": "pc-rear"}
    assert sensors["imu"] == {"imu": "imu-1"}


def test_assembler_requires_window() -> None:
    a = FrameAssembler(tolerance=0.05)
    a.push("lidar", "front", 1.00, "pc-front")
    a.push("lidar", "rear", 1.10, "pc-rear")  # 差 0.1 > 容差
    assert _frame(a) is None  # rear 窗口外 → 不产帧

    # 参考实例更新后再触发
    a.push("lidar", "rear", 1.02, "pc-rear2")
    t0, sensors = _frame(a)
    assert sensors["lidar"]["rear"] == "pc-rear2"


def test_assembler_unbounded_takes_latest() -> None:
    a = FrameAssembler(tolerance=0.05)
    a.push("imu", "imu", 0.50, "imu-old")
    a.push("lidar", "front", 1.00, "pc-front")
    a.push("lidar", "rear", 1.01, "pc-rear")
    # imu 时间戳在窗口外，但 unbounded → 取最近帧
    t0, sensors = _frame(a)
    assert sensors["imu"] == {"imu": "imu-old"}


def test_assembler_clear() -> None:
    a = FrameAssembler(tolerance=0.05)
    a.push("lidar", "front", 1.00, "pc-front")
    a.clear()
    assert _frame(a) is None
