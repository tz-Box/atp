"""场景配置：YAML 声明 dataset + checker + body（v1.1 §8 冻结）。

module 概念已移除：场景不再绑定算法模块，改由命名空间 + produces/consumes 归属校验。
body 必填：所有评测必须声明本体资产，确保数据集与本体对齐。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Scenario:
    body: str  # 本体资产引用（如 pbox_v1），必填
    dataset_type: str  # 命名空间键（如 pipe.slam.rosbag）
    dataset_config: dict = field(default_factory=dict)
    checker: str = ""  # 命名空间键（如 pipe.slam.ape），空 = 数据流验证
    checker_config: dict = field(default_factory=dict)
    sensor_config: dict = field(default_factory=dict)  # {类型: {实例名: topic}}，经 INIT 下发
    hyperparams: dict = field(default_factory=dict)  # 算法超参，经 INIT 下发
    # 指标方向声明 {指标名: "lower"|"higher"}（metrics 块，M 2026-09-11 拍板）：
    # 回归对比（vs_baseline）只对声明了方向的指标给好/坏判断，未声明的指标只报
    # 数值与 delta、不判方向——缺省不得猜。声明与阈值同住被测仓（测试语义不出仓）。
    metric_directions: dict = field(default_factory=dict)


_METRIC_DIRECTIONS = ("lower", "higher")


def _parse_metric_directions(data: dict, path: str) -> dict:
    """解析场景 `metrics:` 块 → {指标名: 方向}。

    形状为 {指标名: {direction: lower|higher}}（嵌套 dict 而非扁平串，为批 2 趋势判据
    在同一处扩展留位）。非法即报错，不静默降级：方向声明写错却被当成"未声明"，
    会让该指标悄悄退回「未判定」态，与「拼错的声明该被看见」相悖。
    """
    metrics = data.get("metrics")
    if metrics is None:
        return {}
    if not isinstance(metrics, dict):
        raise ValueError(f"场景 metrics 需为 dict（{{指标名: {{direction: ...}}}}）: {path}")
    directions: dict[str, str] = {}
    for name, spec in metrics.items():
        if not isinstance(spec, dict):
            raise ValueError(
                f"场景 metrics.{name} 需为 dict（如 {{direction: lower}}）: {path}")
        unknown = set(spec) - {"direction"}
        if unknown:
            raise ValueError(
                f"场景 metrics.{name} 含未知键 {sorted(unknown)}（当前仅支持 direction）: {path}")
        direction = spec.get("direction")
        if direction not in _METRIC_DIRECTIONS:
            raise ValueError(
                f"场景 metrics.{name}.direction 非法（lower|higher）: {direction!r}（{path}）")
        directions[name] = direction
    return directions


def load_scenario(path: str) -> Scenario:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"场景配置非法（需 dict）: {path}")
    if "body" not in data:
        raise ValueError(f"场景配置缺 body（本体资产必填，v1.1 §8）: {path}")
    if "dataset" not in data:
        raise ValueError(f"场景配置缺 dataset: {path}")
    dataset = data["dataset"]
    if not isinstance(dataset, dict) or "type" not in dataset:
        raise ValueError(f"场景 dataset 缺 type: {path}")
    return Scenario(
        body=data["body"],
        dataset_type=dataset["type"],
        dataset_config=dataset.get("config", {}),
        checker=data.get("checker", ""),
        checker_config=data.get("checker_config", {}),
        sensor_config=data.get("sensor_config", {}),
        hyperparams=data.get("hyperparams", {}),
        metric_directions=_parse_metric_directions(data, path),
    )
