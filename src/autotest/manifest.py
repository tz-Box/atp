"""算法侧静态注册声明：算法仓库根目录的 scenario.yaml。

由算法开发者编写，声明算法的能力与建议配置；评测服务读取后
分配会话并拉起算法（yaml 取代"注册握手"的静态部分，会话仍由服务注入）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class AlgorithmManifest:
    module: str  # slam / nav / manip
    launch: str  # 启动命令（在算法根目录执行）
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
        module=data["module"],
        launch=data["launch"],
        scenario=data.get("scenario", ""),
        required_sensors=data.get("required_sensors", {}),
        output_topic=data.get("output_topic", ""),
        hyperparams=data.get("hyperparams", {}),
        image=data.get("image", ""),
        dir=str(p.parent),
    )
    return manifest
