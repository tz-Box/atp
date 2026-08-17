"""场景配置：YAML 声明 module + dataset + checker。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Scenario:
    module: str
    dataset_type: str
    dataset_config: dict = field(default_factory=dict)
    checker: str = ""
    checker_config: dict = field(default_factory=dict)
    sensor_config: dict = field(default_factory=dict)  # {类型: {实例名: topic}}，经 INIT 下发给算法
    hyperparams: dict = field(default_factory=dict)  # 算法超参，经 INIT 下发


def load_scenario(path: str) -> Scenario:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "module" not in data or "dataset" not in data:
        raise ValueError(f"场景配置非法（需 module + dataset）: {path}")
    dataset = data["dataset"]
    if not isinstance(dataset, dict) or "type" not in dataset:
        raise ValueError(f"场景 dataset 缺 type: {path}")
    return Scenario(
        module=data["module"],
        dataset_type=dataset["type"],
        dataset_config=dataset.get("config", {}),
        checker=data.get("checker", ""),
        checker_config=data.get("checker_config", {}),
        sensor_config=data.get("sensor_config", {}),
        hyperparams=data.get("hyperparams", {}),
    )
