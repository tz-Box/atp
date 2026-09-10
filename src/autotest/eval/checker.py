"""Checker 插件接口（评审逻辑，属于执行层）。

v1.1 §7 冻结：checker 声明 produces / consumes（数据 schema 归属校验）。

## 判据层（A11 批 0，2026-09-11，M 拍板「按讨论推进」）

此前 `Score` 只有 {metrics: 值, passed: 一个总布尔}——阈值住在插件 config、键名各插件
自取、合并塌成 bool 发生在插件内部，外面拿不到「哪条判据挂了、规则是什么」。
判据层把它拆出来：每条判据 = 一个 `Judgement`，`passed` 从判据**派生**，不再手写。

三条纪律（每条都对应一个真实撞过的洞）：
- **判据以「声明的清单」枚举，不以「算出来的结果」枚举**——checker 没法评时
  （数据没到 / GT 缺失 / 样本不足）判据仍须在场，`actual="none"`；否则「没跑成」
  会从结果里静默消失（场景级同款缺陷 2026-09-05 修过，不得在下一层重开）。
- **零判据不得判过**：`all([]) == True` 会让没跑成的用例显示为通过——
  `from_judgements` 对空清单恒判 False（批 1 报文层对应 not_run 态）。
- **`rule` 是插件产出的人可读串**：不保证机器可解析，消费方只用于显示、
  不得解析它做判定（需要判定就该在报文里给结构化字段）。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional


@dataclass
class Judgement:
    """一条判据的一次判定结果。

    actual 三态：pass / fail / **none**（判据已声明、但本次没算出来）——
    「没法评」和「判据没过」必须分开：前者查数据源，后者查算法。
    """
    metric: str            # 判据针对的指标名（与 Score.metrics 键对应）
    rule: str              # 人可读判据串（含已解析的阈值，如 "ate_rmse <= 0.2"）
    actual: str            # "pass" | "fail" | "none"
    value: Optional[float] = None   # 本次实测值；actual="none" 时为 None


@dataclass
class Score:
    metrics: dict[str, float] = field(default_factory=dict)
    passed: bool = False
    judgements: list[Judgement] = field(default_factory=list)

    @classmethod
    def from_judgements(cls, metrics: dict[str, float],
                        judgements: list[Judgement]) -> "Score":
        """passed 从判据派生：判据非空 且 全部 pass。

        空清单/含 none 恒 False——见模块 docstring 的「零判据不得判过」。
        """
        passed = bool(judgements) and all(j.actual == "pass" for j in judgements)
        return cls(metrics=metrics, passed=passed, judgements=judgements)

    @classmethod
    def not_run(cls, criteria: Iterable[tuple[str, str]]) -> "Score":
        """「没法评」的成绩单：声明的判据全部在场、全部 actual="none"、恒不通过。

        criteria: (指标名, 判据串) 清单——与正常路径同源，保证两条路径声明一致。
        """
        return cls(metrics={}, passed=False,
                   judgements=[Judgement(metric=m, rule=r, actual="none")
                               for m, r in criteria])


def judged(metric: str, rule: str, ok: bool, value: float) -> Judgement:
    """正常算出值后的单条判定（缩短四个 checker 的样板）。"""
    return Judgement(metric=metric, rule=rule,
                     actual="pass" if ok else "fail", value=value)


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
