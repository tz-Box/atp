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
    )
