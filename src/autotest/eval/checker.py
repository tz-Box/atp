"""Checker 插件接口（评审逻辑，属于执行层）。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

from ..world.base import GroundTruth


@dataclass
class Score:
    metrics: dict[str, float] = field(default_factory=dict)
    passed: bool = False


class IChecker(ABC):
    name: str = ""

    @abstractmethod
    def evaluate(
        self,
        records: list[Any],
        ground_truth: GroundTruth,
        config: Optional[dict[str, Any]] = None,
    ) -> Score:
        """records: 算法输出的记录（类型由具体 checker 自行解析）；ground_truth: 真值。"""
