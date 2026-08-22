"""Checker 插件接口（评审逻辑，属于执行层）。

v1.1 §7 冻结：checker 声明 produces / consumes（数据 schema 归属校验）。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Score:
    metrics: dict[str, float] = field(default_factory=dict)
    passed: bool = False


class IChecker(ABC):
    name: str = ""
    consumes: list[str] = []  # 本 checker 依赖的观测 schema（命名空间键，供 produces⊇consumes 校验）

    @abstractmethod
    def evaluate(
        self,
        records: list[Any],
        ground_truth: dict,
        config: Optional[dict[str, Any]] = None,
    ) -> Score:
        """records: 评测收集的记录——开环为 result payload（{module, data}）列表，
        闭环为 observation 外层信封列表；内容由具体 checker 自行解码（decode_result /
        decode_observation），核心不感知。
        ground_truth: GT 信封 dict（{schema, v, data}，v1.1 §6）。"""
