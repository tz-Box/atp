"""算法侧静态注册声明：算法仓库根目录的 scenario.yaml。

v1.1 §3 冻结：module 常量化移除，改由 consumes 列表声明算法消费的数据 schema。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class AlgorithmManifest:
    launch: str  # 启动命令（在算法根目录执行）
    consumes: list[str] = field(default_factory=list)  # 算法声明的输入 schema（命名空间键）
    scenario: str = ""  # 建议场景（评测方可覆盖）
    required_sensors: dict = field(default_factory=dict)  # {类型: [实例名...]}
    output_topic: str = ""  # 算法自有产物 topic（ROS 侧，可选）
    hyperparams: dict = field(default_factory=dict)  # 算法超参，经 INIT 下发
    image: str = ""  # 可选：docker 镜像名，填了则以 docker+bind 方式拉起
    dir: str = ""  # 算法根目录（yaml 所在目录），解析时填入


def load_algorithm_manifest(path: str) -> AlgorithmManifest:
    """读取算法 scenario.yaml，dir 为 yaml 所在目录（launch 的工作目录）。"""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"算法 manifest 不存在: {p}")
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    manifest = AlgorithmManifest(
        launch=data["launch"],
        consumes=data.get("consumes", []),
        scenario=data.get("scenario", ""),
        required_sensors=data.get("required_sensors", {}),
        output_topic=data.get("output_topic", ""),
        hyperparams=data.get("hyperparams", {}),
        image=data.get("image", ""),
        dir=str(p.parent),
    )
    return manifest
