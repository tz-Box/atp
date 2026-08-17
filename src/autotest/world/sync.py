"""多传感器时间戳同步：按容差窗口把多实例数据组装成帧。

背景：SLAM 帧 = 同一时刻（容差内）的 front/rear 点云 + IMU。数据源按消息
到达顺序推流时，交错的话题会让每帧缺实例（如只含 front 不含 rear），算法
消费不完整帧会出错或不出结果。

用法：数据源把各路传感器消息 push 进来，以参考实例触发产帧；required 声明
的实例必须都在容差窗口内才产帧（保证帧完整），其余类型尽力携带（unbounded
类型取最近帧，如高频 IMU）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class _Entry:
    timestamp: float
    data: Any


class FrameAssembler:
    def __init__(self, tolerance: float = 0.05, unbounded: tuple[str, ...] = ("imu",)) -> None:
        """tolerance：required 实例与参考帧的最大时间差（秒）；unbounded：取最近帧的类型。"""
        self._tolerance = tolerance
        self._unbounded = set(unbounded)
        self._latest: dict[tuple[str, str], _Entry] = {}

    def push(self, stype: str, name: str, timestamp: float, data: Any) -> None:
        """登记一路传感器的最新一帧（同实例覆盖旧帧）。"""
        self._latest[(stype, name)] = _Entry(timestamp, data)

    def clear(self) -> None:
        self._latest.clear()

    def frame(
        self, ref_stype: str, ref_name: str, required: Optional[dict[str, list[str]]] = None
    ) -> Optional[tuple[float, dict]]:
        """以 (ref_stype, ref_name) 为参考产帧。

        required：{类型: [实例...]}，这些实例必须都在容差窗口内才产帧（返回 None 表示未就绪）。
        返回 (参考时间戳, {类型: {实例: 数据}})；参考实例与窗口内实例必带，unbounded 类型取最近帧。
        """
        ref = self._latest.get((ref_stype, ref_name))
        if ref is None:
            return None
        t0 = ref.timestamp

        if required:
            for stype, names in required.items():
                for name in names:
                    entry = self._latest.get((stype, name))
                    if entry is None or abs(entry.timestamp - t0) > self._tolerance:
                        return None

        sensors: dict[str, dict[str, Any]] = {}
        for (stype, name), entry in self._latest.items():
            if (stype, name) == (ref_stype, ref_name):
                sensors.setdefault(stype, {})[name] = entry.data
            elif stype in self._unbounded or abs(entry.timestamp - t0) <= self._tolerance:
                sensors.setdefault(stype, {})[name] = entry.data
        return t0, sensors
